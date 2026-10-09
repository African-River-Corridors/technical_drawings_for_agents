"""Block-flow / process-schematic generator — *data → layout engine → ISO sheet*.

The three concerns are deliberately separated:

* **Content** lives in a YAML data file (nodes, edges, groups, dosing, meta).
* **Layout** is delegated to Graphviz ``dot`` (rank-based, collision-free) — we do
  NOT hand-place coordinates.
* **Style** is an ISO-house sheet (monochrome, ISO 5457-ish frame + ISO 7200 title
  block + legend + notes) that *wraps* the rendered layout as a nested ``<svg>``.

Two views from one data file:
  * ``block``   — the logical block-flow schematic (BFD).
  * ``profile`` — a hydraulic long-section (elevation vs chainage) when nodes carry
    ``elev`` / ``chainage``.

Rendering to PDF/PNG re-uses :mod:`technical_drawings_for_agents.render`.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from collections import defaultdict, deque
from pathlib import Path

import yaml

# Shared ISO house-sheet furniture (frame, title block, legend, notes, logo strip).
# These used to live here; they were extracted to isosheet.py so the P&ID generator
# (pid.py) reuses exactly the same sheet chrome. The leading-underscore aliases keep
# the internal/profile call sites unchanged.
from .isosheet import (  # noqa: F401  (re-exported for profile.py)
    img_data_uri as _img_data_uri,
    notes_block as _notes,
    rect as _rect,
    sheet as _sheet,
    text as _t,
    titleblock as _iso_titleblock,
    wrap as _wrap,
)

# --- monochrome print tokens (this drawing class is black-on-white, not the CAD dark theme) ---
BLACK = "#000000"
WHITE = "#ffffff"
GREY = "#555555"
RED = "#b00000"

# node type -> graphviz shape/style (monochrome, ISO-ish)
_TYPE = {
    "source": dict(shape="cds", style="filled"),
    "sink": dict(shape="cds", style="filled"),
    "pond": dict(shape="invhouse", style="filled"),
    "tank": dict(shape="cylinder", style="filled"),
    "skid": dict(shape="box3d", style="filled"),
    "process": dict(shape="box", style="filled"),
    "interface": dict(shape="box", style="filled,dashed"),
    "dose": dict(shape="note", style="filled"),
    "instrument": dict(shape="circle", style="filled"),
}
_EDGE = {
    "process": 'penwidth=2.0, arrowsize=0.8',
    "dose": 'style=dashed, penwidth=1.0, arrowsize=0.7',
    "signal": 'style=dotted, penwidth=0.9, arrowhead=none',
}


def load(path: str | Path) -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def _esc(text: str) -> str:
    return str(text).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _node_line(n: dict) -> str:
    style = _TYPE.get(n.get("type", "process"), _TYPE["process"])
    label = n.get("label", n["id"])
    if n.get("tag"):
        label = f"{label}\n{n['tag']}"
    attrs = [
        f'label="{_esc(label)}"',
        f'shape={style["shape"]}',
        f'style="{style["style"]}"',
        "fillcolor=white",
        "color=black",
        "fontcolor=black",
    ]
    return f'  "{n["id"]}" [{", ".join(attrs)}];'


def _spine(data: dict) -> list[str]:
    """Ordered main-flow chain from the process-kind edges (branches/dosing excluded)."""
    proc = [(e["from"], e["to"]) for e in data["edges"] if e.get("kind", "process") == "process"]
    succ = {a: b for a, b in proc}
    starts = {a for a, _ in proc} - {b for _, b in proc}
    if not starts:
        return [n["id"] for n in data["nodes"]]
    order, cur = [], sorted(starts)[0]
    seen = set()
    while cur is not None and cur not in seen:
        order.append(cur)
        seen.add(cur)
        cur = succ.get(cur)
    return order


def to_dot(data: dict) -> str:
    """Serialise the data model to a styled Graphviz DOT.

    ``meta.wrap`` (int) turns on a serpentine (boustrophedon) layout: the main spine
    is chunked into rows of that many blocks, alternating direction, so a long chain
    fills the sheet instead of a thin strip. Without it, a single left-to-right row.
    """
    nodes = {n["id"]: n for n in data["nodes"]}
    grouped = {m for g in data.get("groups", []) for m in g["members"]}
    wrap = data.get("meta", {}).get("wrap")
    lines = [
        "digraph BFD {",
        f"  rankdir={'TB' if wrap else 'LR'};",
        "  splines=ortho;",
        "  nodesep=0.30; ranksep=0.65;",
        '  bgcolor="white";',
        '  node [fontname="Helvetica", fontsize=11, margin="0.16,0.10", penwidth=1.3];',
        '  edge [fontname="Helvetica", fontsize=9, color=black];',
    ]
    for i, g in enumerate(data.get("groups", [])):
        lines.append(f'  subgraph cluster_{i} {{')
        lines.append(f'    label="{_esc(g["label"])}"; labeljust="l"; fontsize=9; fontname="Helvetica-Bold";')
        lines.append('    style=dashed; color=black; margin=12;')
        for m in g["members"]:
            lines.append("  " + _node_line(nodes[m]))
        lines.append("  }")
    for nid, n in nodes.items():
        if nid not in grouped:
            lines.append(_node_line(n))

    row_of: dict[str, int] = {}
    if wrap:
        spine = _spine(data)
        for r in range(0, len(spine), wrap):
            idx = r // wrap
            row = spine[r : r + wrap]  # every row reads left-to-right (no boustrophedon)
            for n in row:
                row_of[n] = idx
            lines.append("  { rank=same; " + " ".join(f'"{n}"' for n in row) + " }")
            for a, b in zip(row, row[1:]):  # invisible ordering within the row (flat edge)
                lines.append(f'  "{a}" -> "{b}" [style=invis, constraint=false, weight=100];')

    for e in data.get("edges", []):
        kind = e.get("kind", "process")
        r_from, r_to = row_of.get(e["from"]), row_of.get(e["to"])
        same_row = wrap and r_from is not None and r_from == r_to
        cross_row = wrap and kind == "process" and r_from is not None and r_to is not None and r_from != r_to
        if cross_row:
            # light "continued on next row" connector — de-emphasised, steps the rank down
            attr = 'style=dashed, color="#999999", penwidth=0.8, arrowsize=0.7, label="continued", fontcolor="#999999", fontsize=7'
        else:
            attr = _EDGE.get(kind, _EDGE["process"])
            if e.get("label"):
                attr += f', label="{_esc(e["label"])}"'
            # within a serpentine row rank=same already places nodes; drop the rank constraint
            if e.get("constraint") is False or same_row:
                attr += ", constraint=false"
        lines.append(f'  "{e["from"]}" -> "{e["to"]}" [{attr}];')
    lines.append("}")
    return "\n".join(lines)


def _dot_to_inner_svg(dot: str) -> tuple[str, float, float]:
    """Render DOT with the ``dot`` binary; return (inner-svg-body, width, height)."""
    exe = shutil.which("dot")
    if not exe:
        raise RuntimeError("Graphviz 'dot' not found on PATH — needed for BFD layout.")
    out = subprocess.run([exe, "-Tsvg"], input=dot, capture_output=True, text=True, check=True).stdout
    m = re.search(r'viewBox="([\d.\- ]+)"', out)
    vb = [float(v) for v in m.group(1).split()] if m else [0, 0, 1000, 700]
    w, h = vb[2], vb[3]
    # strip xml decl / doctype / outer <svg ...> and </svg>; keep the inner <g> body
    body = re.sub(r"<\?xml.*?\?>", "", out, flags=re.S)
    body = re.sub(r"<!DOCTYPE.*?>", "", body, flags=re.S)
    body = re.sub(r"<svg[^>]*>", "", body, count=1, flags=re.S)
    body = body.rsplit("</svg>", 1)[0]
    return body, w, h, vb  # type: ignore[return-value]


# ---------------- ISO sheet furniture ----------------
# The frame / title block / legend / notes / logo strip now live in isosheet.py
# and are imported at the top of this module (aliased to their former private
# names). Only the BFD-specific legend glyphs and the doctype default remain here.


# legend glyphs (monochrome)
def _g_box(x, y):
    return _rect(x, y - 6, 26, 14, 1.0)


def _g_skid(x, y):
    return _rect(x, y - 7, 26, 15, 1.0) + f'<circle cx="{x+8}" cy="{y}" r="3.5" fill="white" stroke="black"/><circle cx="{x+18}" cy="{y}" r="3.5" fill="white" stroke="black"/>'


def _g_pond(x, y):
    return f'<path d="M{x},{y+7} l5,-13 h16 l5,13 z" fill="white" stroke="black" stroke-width="1"/>'


def _g_proc(x, y):
    return _rect(x, y - 6, 26, 13, 1.0)


def _g_solid(x, y):
    return f'<line x1="{x}" y1="{y}" x2="{x+26}" y2="{y}" stroke="black" stroke-width="2"/>'


def _g_dash(x, y):
    return f'<line x1="{x}" y1="{y}" x2="{x+26}" y2="{y}" stroke="black" stroke-width="1" stroke-dasharray="4 3"/>'


LEGEND = [
    (_g_pond, "Lined pond"),
    (_g_skid, "KSB pump skid (2 pumps, duty/standby)"),
    (_g_proc, "Process / treatment unit"),
    (_g_solid, "Process line (water)"),
    (_g_dash, "Dosing / chemical injection"),
]


def build_block(data: dict, out_dir: Path, stem: str) -> Path:
    dot = to_dot(data)
    (out_dir / f"{stem}.dot").write_text(dot, encoding="utf-8", newline="\n")
    body, gw, gh, vb = _dot_to_inner_svg(dot)
    svg = _sheet(body, vb, data["meta"], LEGEND, data.get("notes", []),
                 tuple(data.get("sheet", [1500, 950])))
    out = out_dir / f"{stem}.svg"
    out.write_text(svg, encoding="utf-8", newline="\n")
    return out


# ---------------- swimlane view (deterministic grid: lane = row, process step = column) ----------------

def _columns(data: dict) -> dict[str, int]:
    """Maximally-compact column index. Walking the flow in topological order, a block
    STACKS in its predecessor's column (same column, different lane) whenever that lane
    slot is free; it only advances a column when forced — i.e. the block is in the same
    lane as its predecessor, or the target slot is already taken. Stacks may run in any
    vertical direction."""
    nodes = {n["id"]: n for n in data["nodes"]}
    ids = list(nodes)
    proc = [(e["from"], e["to"]) for e in data["edges"] if e.get("kind", "process") == "process"]
    lane = {i: nodes[i].get("lane") for i in ids}
    nostack = {i: bool(nodes[i].get("nostack")) for i in ids}  # force a fresh column, never stack
    succ, preds, indeg = defaultdict(list), defaultdict(list), {i: 0 for i in ids}
    for a, b in proc:
        succ[a].append(b)
        preds[b].append(a)
        indeg[b] += 1
    q = deque(i for i in ids if indeg[i] == 0)
    order = []
    while q:
        n = q.popleft()
        order.append(n)
        for m in succ[n]:
            indeg[m] -= 1
            if indeg[m] == 0:
                q.append(m)
    order += [i for i in ids if i not in order]  # any stragglers (cycles — shouldn't happen)

    occupied: set[tuple[int, str]] = set()
    col: dict[str, int] = {}
    for b in order:
        d = 0
        for a in preds[b]:
            can_stack = lane[a] != lane[b] and not nostack[b]
            d = max(d, col[a] if can_stack else col[a] + 1)
        while (d, lane[b]) in occupied:  # slot taken -> advance
            d += 1
        col[b] = d
        occupied.add((d, lane[b]))
    return col


def _node_shape(n: dict, x, y, w, h) -> str:
    """Monochrome block by type, centred label (multi-line + tag)."""
    t = n.get("type", "process")
    cx, cy = x + w / 2, y + h / 2
    parts = []
    if t == "pond":
        parts.append(f'<path d="M{x+8},{y} L{x+w-8},{y} L{x+w},{y+h} L{x},{y+h} Z" fill="white" stroke="black" stroke-width="1.3"/>')
    elif t == "tank":
        parts.append(f'<rect x="{x}" y="{y+4}" width="{w}" height="{h-8}" rx="3" fill="white" stroke="black" stroke-width="1.3"/>')
    elif t == "skid":
        parts.append(_rect(x, y, w, h, 1.3))
        parts.append(f'<circle cx="{x+14}" cy="{y+h-10}" r="4.5" fill="white" stroke="black"/><circle cx="{x+28}" cy="{y+h-10}" r="4.5" fill="white" stroke="black"/>')
    elif t in ("source", "sink"):
        parts.append(f'<path d="M{x},{y} L{x+w-12},{y} L{x+w},{cy} L{x+w-12},{y+h} L{x},{y+h} Z" fill="white" stroke="black" stroke-width="1.2"/>')
    elif t == "dose":
        parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="white" stroke="black" stroke-width="1" stroke-dasharray="4 2"/>')
    elif t == "interface":
        parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="white" stroke="black" stroke-width="1.5" stroke-dasharray="6 3"/>')
    else:
        parts.append(_rect(x, y, w, h, 1.3))
    raw = str(n.get("label", n["id"])).split("\n")  # YAML double-quote "\n" = real newline
    lines: list[str] = []
    for ln in raw:
        lines.extend(_wrap(ln, 30))  # word-wrap to the box width (no truncation)
    tag_i = None
    if n.get("tag"):
        tag_i = len(lines)
        lines.append(n["tag"])
    ty = cy - (len(lines) - 1) * 4.8
    for i, ln in enumerate(lines):
        parts.append(_t(cx, ty + i * 9.5 + 3, ln, 7.5 if i == 0 else 7,
                        anchor="middle", weight="bold" if i == 0 else "normal",
                        fill=GREY if i == tag_i else BLACK))
    return "".join(parts)


def build_swimlane(data: dict, out_dir: Path, stem: str) -> Path:
    lanes = data["lanes"]
    lane_idx = {l["id"]: i for i, l in enumerate(lanes)}
    nodes = {n["id"]: n for n in data["nodes"]}
    col = _columns(data)
    # resolve cell collisions (same lane+col) by nudging the later node one column on
    occupied: set[tuple[int, int]] = set()
    cell: dict[str, tuple[int, int]] = {}
    for nid in [n["id"] for n in data["nodes"]]:
        c, li = col[nid], lane_idx.get(nodes[nid].get("lane"), len(lanes) - 1)
        while (c, li) in occupied:
            c += 1
        occupied.add((c, li))
        cell[nid] = (c, li)
    ncol = max(c for c, _ in cell.values()) + 1

    # geometry (inner drawing coordinates) — lanes sized to fill the sheet's drawing band
    LABEL_W, COLW, LANEH, NW, NH, TOP = 156, 210, 196, 168, 90, 20
    W = LABEL_W + ncol * COLW + 30
    H = TOP + len(lanes) * LANEH + 20

    def cx(c):
        return LABEL_W + c * COLW + COLW / 2

    def cyl(li):
        return TOP + li * LANEH + LANEH / 2

    p = []
    # lane bands + labels
    for i, l in enumerate(lanes):
        yb = TOP + i * LANEH
        band = "#f4f4f4" if i % 2 == 0 else "#ffffff"
        p.append(f'<rect x="0" y="{yb}" width="{W}" height="{LANEH}" fill="{band}" stroke="#bbbbbb" stroke-width="0.6"/>')
        p.append(f'<rect x="0" y="{yb}" width="{LABEL_W}" height="{LANEH}" fill="white" stroke="#bbbbbb" stroke-width="0.6"/>')
        # lane label, wrapped
        words = l["label"].split(" ")
        rows_txt, cur = [], ""
        for w in words:
            if len(cur) + len(w) > 20:
                rows_txt.append(cur); cur = w
            else:
                cur = (cur + " " + w).strip()
        rows_txt.append(cur)
        ly = yb + LANEH / 2 - (len(rows_txt) - 1) * 7
        for j, r in enumerate(rows_txt):
            p.append(_t(10, ly + j * 13, r, 9.5, anchor="start", weight="bold"))

    # edges first (under nodes)
    for e in data["edges"]:
        a, b = e["from"], e["to"]
        if a not in cell or b not in cell:
            continue
        (ca, la), (cb, lb) = cell[a], cell[b]
        sx, sy, tx, ty = cx(ca), cyl(la), cx(cb), cyl(lb)
        dose = e.get("kind") == "dose"
        stroke = '#999999" stroke-dasharray="4 3' if dose else "black"
        sw = 1.0 if dose else 2.0
        if ca == cb:  # stacked in the same column -> a clean vertical drop/rise
            down = lb > la
            y1 = sy + NH / 2 if down else sy - NH / 2   # leave from source bottom/top edge
            y2 = ty - NH / 2 if down else ty + NH / 2   # enter target top/bottom edge
            lo, hi = min(la, lb), max(la, lb)
            blocked = any((ca, k) in occupied for k in range(lo + 1, hi))
            if not blocked:                              # nothing in between -> straight vertical
                d = f"M{sx},{y1} L{tx},{y2}"
            else:                                        # skip an occupied lane -> riser clear of the box
                rx = sx + NW / 2 + 24
                d = f"M{sx},{y1} L{rx},{y1} L{rx},{y2} L{tx},{y2}"
        elif la == lb:  # same lane -> straight horizontal, edge to edge
            d = f"M{sx + NW / 2},{sy} L{tx - NW / 2},{ty}"
        else:  # different column and lane -> exit toward the target, one orthogonal jog
            xm = (sx + NW / 2 + tx - NW / 2) / 2
            d = f"M{sx + NW / 2},{sy} L{xm},{sy} L{xm},{ty} L{tx - NW / 2},{ty}"
        arrow = "" if dose else ' marker-end="url(#aw)"'
        p.append(f'<path d="{d}" fill="none" stroke="{stroke}" stroke-width="{sw}"{arrow}/>')

    # nodes
    for nid, (c, li) in cell.items():
        x, y = cx(c) - NW / 2, cyl(li) - NH / 2
        p.append(_node_shape(nodes[nid], x, y, NW, NH))

    defs = '<defs><marker id="aw" markerWidth="9" markerHeight="9" refX="6" refY="3" orient="auto"><path d="M0,0 L6,3 L0,6 Z" fill="black"/></marker></defs>'
    inner = defs + "".join(p)
    svg = _sheet(inner, [0, 0, W, H], data["meta"], LEGEND, data.get("notes", []),
                 tuple(data.get("sheet", [1500, 950])))
    out = out_dir / f"{stem}.svg"
    out.write_text(svg, encoding="utf-8", newline="\n")
    return out


def build(data_path: str | Path, out_dir: str | Path, view: str = "block") -> list[Path]:
    data = load(data_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    data.setdefault("meta", {}).setdefault("doctype", "BLOCK-FLOW SCHEMATIC")
    # resolve logo paths (relative to the data file) into embedded data URIs
    logos = data["meta"].get("logos")
    if logos:
        base = Path(data_path).parent
        data["meta"]["logos"] = {
            k: _img_data_uri(base / v) for k, v in logos.items() if (base / v).exists()
        }
    stem = data["meta"].get("number", "bfd").replace(" ", "_")
    outputs = []
    if view in ("block", "all"):
        outputs.append(build_block(data, out_dir, stem))
    if view in ("swimlane", "all") and data.get("lanes"):
        outputs.append(build_swimlane(data, out_dir, stem + "_swimlane"))
    if view in ("profile", "all") and any("elev" in n for n in data["nodes"]):
        from .profile import build_profile  # optional second view
        outputs.append(build_profile(data, out_dir, stem + "_profile"))
    return outputs
