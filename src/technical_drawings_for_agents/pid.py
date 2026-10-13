"""P&ID generator — *data YAML → ISA-5.1 symbols → ISO house sheet*.

Unlike :mod:`technical_drawings_for_agents.bfd` (which delegates layout to Graphviz — right for
*topology*, wrong for a real P&ID), this generator uses **deterministic
positional layout**: every symbol declares its own ``at: [x, y]`` on an inner
canvas, and lines connect **named ports** (``<id>.<port>``) with **orthogonal
routing** (optional explicit ``waypoints``). Cloning a diagram to another site
is a *data edit*, never a code change.

Three concerns stay separated, mirroring ``bfd``:

* **Content** — a YAML data file: tagged equipment / instruments / lines / loops
  with explicit coordinates and IEC ``=system +location`` designations. Tags
  reference the project's shared register (e.g. ``parts-db/<project>.tags.yaml``);
  this tool draws what the data declares and never mints a tag itself.
* **Geometry** — the pure, parametric symbol library :mod:`technical_drawings_for_agents.isa`.
* **Sheet** — the shared ISO house chrome in :mod:`technical_drawings_for_agents.isosheet`
  (identical frame / title block / notes / legend used by ``bfd``), plus the
  diagonal status watermark the standard requires (drawings stay
  **CONCEPT — NOT FOR CONSTRUCTION** until an engineer signs off).
"""

from __future__ import annotations

import math
from pathlib import Path

import yaml

from . import isa
from . import isosheet
from .isosheet import BLACK, GREY

# Direction unit vectors, SVG space (+y down).
_DIRV = {"N": (0.0, -1.0), "E": (1.0, 0.0), "S": (0.0, 1.0), "W": (-1.0, 0.0)}
_STUB = 16.0  # length a line leaves a port before it may turn

# Line types (ISA-5.1-lite). Each is drawn distinctly so a reviewer can tell a
# process pipe from a signal at a glance.
LINE_TYPES = {
    "process": dict(sw=2.2, dash=None, arrow=True, ticks=None),
    "signal": dict(sw=1.0, dash=None, arrow=False, ticks=None),      # instrument-to-process lead
    "electric": dict(sw=1.3, dash="7 4", arrow=False, ticks=None),   # electrical signal
    "pneumatic": dict(sw=1.4, dash=None, arrow=False, ticks="slash"),  # pneumatic signal (// marks)
}

# Symbol types whose tag is drawn *externally* (bubbles carry their tag inside;
# tie-ins carry a destination label inside).
_EXTERNAL_TAG = {"pump", "valve", "vessel", "tank", "filter", "dosing_skid", "skid"}
_TAGGABLE = _EXTERNAL_TAG | {"instrument", "tie_in"}


def load(path: str | Path) -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# Placement
# --------------------------------------------------------------------------
def _items(data: dict) -> list[dict]:
    """All placed symbols: equipment + instruments, in declaration order."""
    return list(data.get("equipment", []) or []) + list(data.get("instruments", []) or [])


def place_symbols(data: dict) -> dict[str, isa.Symbol]:
    """Instantiate every equipment/instrument symbol at its declared position.

    Returns ``{id: Symbol}``. Raises on a duplicate id or unknown symbol type.
    """
    placed: dict[str, isa.Symbol] = {}
    for item in _items(data):
        sid = item.get("id")
        if not sid:
            raise ValueError(f"symbol missing 'id': {item!r}")
        if sid in placed:
            raise ValueError(f"duplicate symbol id '{sid}'")
        if "at" not in item:
            raise ValueError(f"symbol '{sid}' missing 'at: [x, y]'")
        x, y = item["at"]
        kind = item.get("type")
        params = {k: v for k, v in item.items() if k not in ("id", "at", "type", "caption")}
        placed[sid] = isa.build_symbol(kind, float(x), float(y), **params)
    return placed


# --------------------------------------------------------------------------
# Orthogonal routing
# --------------------------------------------------------------------------
def _stub(pt, direction):
    vx, vy = _DIRV[direction]
    return (pt[0] + vx * _STUB, pt[1] + vy * _STUB)


def _elbow(p, q, adir):
    """One orthogonal bend from ``p`` toward ``q``, leaving ``p`` along ``adir``."""
    if abs(p[0] - q[0]) < 0.5 or abs(p[1] - q[1]) < 0.5:
        return []
    if adir in ("E", "W"):
        return [(q[0], p[1])]
    return [(p[0], q[1])]


def _dedupe(points):
    out = []
    for pt in points:
        if not out or abs(out[-1][0] - pt[0]) > 0.01 or abs(out[-1][1] - pt[1]) > 0.01:
            out.append(pt)
    # drop the middle of any three collinear points
    simplified = out[:1]
    for i in range(1, len(out) - 1):
        ax, ay = out[i - 1]
        bx, by = out[i]
        cx, cy = out[i + 1]
        collinear = (abs(ax - bx) < 0.01 and abs(bx - cx) < 0.01) or (
            abs(ay - by) < 0.01 and abs(by - cy) < 0.01
        )
        if not collinear:
            simplified.append(out[i])
    if len(out) > 1:
        simplified.append(out[-1])
    return simplified


def route(a_pt, a_dir, b_pt, b_dir, waypoints=None):
    """Return an orthogonal polyline of points from port A to port B.

    With ``waypoints`` the caller controls the mid-route explicitly; otherwise a
    single L-bend is inserted between the two port stubs.
    """
    if waypoints:
        pts = [tuple(a_pt)] + [tuple(w) for w in waypoints] + [tuple(b_pt)]
        return _dedupe(pts)
    pa = _stub(a_pt, a_dir)
    pb = _stub(b_pt, b_dir)
    pts = [tuple(a_pt), pa] + _elbow(pa, pb, a_dir) + [pb, tuple(b_pt)]
    return _dedupe(pts)


# --------------------------------------------------------------------------
# Drawing lines
# --------------------------------------------------------------------------
def _seg_angle(p, q) -> float:
    return math.degrees(math.atan2(q[1] - p[1], q[0] - p[0]))


def _polyline_svg(points, sw, dash) -> str:
    d = f' stroke-dasharray="{dash}"' if dash else ""
    pts = " ".join(f"{x:.2f},{y:.2f}" for x, y in points)
    return f'<polyline points="{pts}" fill="none" stroke="{BLACK}" stroke-width="{sw}"{d}/>'


def _arrowhead(p, q, size=7.0) -> str:
    ang = math.radians(_seg_angle(p, q))
    tip = q
    left = (tip[0] - size * math.cos(ang - math.radians(22)),
            tip[1] - size * math.sin(ang - math.radians(22)))
    right = (tip[0] - size * math.cos(ang + math.radians(22)),
             tip[1] - size * math.sin(ang + math.radians(22)))
    pts = f"{tip[0]:.2f},{tip[1]:.2f} {left[0]:.2f},{left[1]:.2f} {right[0]:.2f},{right[1]:.2f}"
    return f'<polygon points="{pts}" fill="{BLACK}"/>'


def _pneumatic_ticks(points) -> str:
    """Double-slash marks along the polyline (ISA pneumatic-signal convention)."""
    out = []
    for p, q in zip(points, points[1:]):
        length = math.hypot(q[0] - p[0], q[1] - p[1])
        if length < 24:
            continue
        ang = math.atan2(q[1] - p[1], q[0] - p[0])
        nx, ny = -math.sin(ang), math.cos(ang)  # normal
        mx, my = (p[0] + q[0]) / 2, (p[1] + q[1]) / 2
        for off in (-4, 4):
            bx, by = mx + math.cos(ang) * off, my + math.sin(ang) * off
            x1, y1 = bx - nx * 5 - math.cos(ang) * 3, by - ny * 5 - math.sin(ang) * 3
            x2, y2 = bx + nx * 5 + math.cos(ang) * 3, by + ny * 5 + math.sin(ang) * 3
            out.append(f'<line x1="{x1:.2f}" y1="{y1:.2f}" x2="{x2:.2f}" y2="{y2:.2f}" '
                       f'stroke="{BLACK}" stroke-width="1.1"/>')
    return "".join(out)


def _flow_arrow_mid(points) -> str:
    """A small filled flow arrow at the midpoint of the longest segment."""
    best = max(zip(points, points[1:]), key=lambda pq: math.dist(pq[0], pq[1]), default=None)
    if not best:
        return ""
    p, q = best
    mx, my = (p[0] + q[0]) / 2, (p[1] + q[1]) / 2
    return isa.flow_arrow(mx, my, rotation=_seg_angle(p, q)).svg


def draw_line(line: dict, points) -> str:
    ltype = line.get("type", "process")
    spec = LINE_TYPES[ltype]
    parts = [_polyline_svg(points, spec["sw"], spec["dash"])]
    if spec["arrow"] and len(points) >= 2:
        parts.append(_arrowhead(points[-2], points[-1]))
        parts.append(_flow_arrow_mid(points))
    if spec["ticks"] == "slash":
        parts.append(_pneumatic_ticks(points))
    if line.get("label"):
        mid = points[len(points) // 2]
        parts.append(isosheet.text(mid[0] + 4, mid[1] - 4, line["label"], size=8, fill=GREY))
    return "".join(parts)


# --------------------------------------------------------------------------
# Annotations (external tags / captions)
# --------------------------------------------------------------------------
def _annotations(item: dict, sym: isa.Symbol) -> str:
    x0, y0, x1, y1 = sym.bbox
    cx = (x0 + x1) / 2
    parts = []
    kind = item.get("type")
    if item.get("tag") and kind in _EXTERNAL_TAG:
        dx = item.get("tag_dx", 0)
        dy = item.get("tag_dy", 14)
        parts.append(isosheet.text(cx + dx, y1 + dy, item["tag"], size=8.5, anchor="middle",
                                   weight="bold"))
    if item.get("caption"):
        parts.append(isosheet.text(cx, y0 - 6, item["caption"], size=8, anchor="middle", fill=GREY))
    return "".join(parts)


# --------------------------------------------------------------------------
# Port resolution
# --------------------------------------------------------------------------
def resolve_port(placed: dict[str, isa.Symbol], ref: str):
    """Resolve ``"<id>.<port>"`` to ``(point, direction)``; raise if unresolved."""
    if "." not in ref:
        raise ValueError(f"line endpoint '{ref}' must be '<id>.<port>'")
    sid, port = ref.split(".", 1)
    if sid not in placed:
        raise ValueError(f"line endpoint '{ref}' references unknown symbol '{sid}'")
    sym = placed[sid]
    if port not in sym.ports:
        raise ValueError(
            f"line endpoint '{ref}': symbol '{sid}' has no port '{port}' "
            f"(ports: {', '.join(sorted(sym.ports))})"
        )
    return sym.ports[port], sym.port_dirs[port]


# --------------------------------------------------------------------------
# Legend
# --------------------------------------------------------------------------
def _legend_items():
    def g_pump(x, y):
        return isa.pump(x + 11, y, scale=0.5).svg
    def g_valve(x, y):
        return isa.valve(x + 11, y, kind="gate", scale=0.5).svg
    def g_inst(x, y):
        return isa.instrument(x + 11, y, mount="field", variable="F", functions="T", scale=0.45).svg
    def g_process(x, y):
        return (f'<line x1="{x}" y1="{y}" x2="{x+26}" y2="{y}" stroke="black" stroke-width="2.2"/>'
                + isa.flow_arrow(x + 20, y, scale=0.8).svg)
    def g_signal(x, y):
        return f'<line x1="{x}" y1="{y}" x2="{x+26}" y2="{y}" stroke="black" stroke-width="1"/>'
    def g_electric(x, y):
        return f'<line x1="{x}" y1="{y}" x2="{x+26}" y2="{y}" stroke="black" stroke-width="1.3" stroke-dasharray="7 4"/>'
    def g_pneumatic(x, y):
        return _pneumatic_ticks([(x, y), (x + 26, y)]) + \
            f'<line x1="{x}" y1="{y}" x2="{x+26}" y2="{y}" stroke="black" stroke-width="1.4"/>'
    def g_tie(x, y):
        return isa.tie_in(x + 11, y, scale=0.7).svg
    return [
        (g_pump, "Centrifugal pump"),
        (g_valve, "Valve (gate/globe/butterfly/check)"),
        (g_inst, "Instrument bubble (field / panel / DCS)"),
        (g_tie, "Tie-in / battery limit (off-page)"),
        (g_process, "Process line"),
        (g_signal, "Instrument signal (to process)"),
        (g_electric, "Electric signal"),
        (g_pneumatic, "Pneumatic signal"),
    ]


# --------------------------------------------------------------------------
# Validation (data model)
# --------------------------------------------------------------------------
def validate_data(data: dict) -> list[str]:
    """Return a list of human-readable problems with the P&ID data (empty = ok).

    Enforces the P&ID rules: every symbol has a tag; every line has a known type
    and resolves to declared ports.
    """
    problems: list[str] = []
    try:
        placed = place_symbols(data)
    except Exception as exc:  # noqa: BLE001
        return [f"cannot place symbols: {exc}"]

    for item in _items(data):
        sid = item.get("id", "?")
        if item.get("type") in _TAGGABLE and not str(item.get("tag", "")).strip():
            problems.append(f"symbol '{sid}' ({item.get('type')}) has no tag")

    for line in data.get("lines", []) or []:
        lid = line.get("id", "?")
        ltype = line.get("type", "process")
        if ltype not in LINE_TYPES:
            problems.append(f"line '{lid}' has unknown type '{ltype}' (one of {', '.join(LINE_TYPES)})")
        for end in ("from", "to"):
            ref = line.get(end)
            if not ref:
                problems.append(f"line '{lid}' missing '{end}'")
                continue
            try:
                resolve_port(placed, ref)
            except Exception as exc:  # noqa: BLE001
                problems.append(f"line '{lid}': {exc}")

    for loop in data.get("loops", []) or []:
        for m in loop.get("members", []):
            if m not in placed:
                problems.append(f"loop '{loop.get('id', '?')}' references unknown member '{m}'")

    return problems


# --------------------------------------------------------------------------
# Build
# --------------------------------------------------------------------------
def _auto_canvas(placed: dict[str, isa.Symbol], margin=80.0):
    xs, ys = [], []
    for sym in placed.values():
        x0, y0, x1, y1 = sym.bbox
        xs += [x0, x1]
        ys += [y0, y1]
    if not xs:
        return [0, 0, 1000, 600]
    return [0, 0, max(xs) + margin, max(ys) + margin]


def build(data_path: str | Path, out_dir: str | Path) -> list[Path]:
    """Render the P&ID data file to an ISO sheet (SVG). Returns the paths."""
    data = load(data_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    problems = validate_data(data)
    if problems:
        raise ValueError("P&ID data invalid:\n  - " + "\n  - ".join(problems))

    meta = data.setdefault("meta", {})
    meta.setdefault("doctype", "P&ID (ISA-5.1)")
    # resolve logo paths (relative to the data file) into embedded data URIs
    logos = meta.get("logos")
    if logos:
        base = Path(data_path).parent
        meta["logos"] = {k: isosheet.img_data_uri(base / v)
                         for k, v in logos.items() if (base / v).exists()}

    placed = place_symbols(data)

    body = []
    # lines first (under symbols)
    for line in data.get("lines", []) or []:
        a_pt, a_dir = resolve_port(placed, line["from"])
        b_pt, b_dir = resolve_port(placed, line["to"])
        pts = route(a_pt, a_dir, b_pt, b_dir, line.get("waypoints"))
        body.append(draw_line(line, pts))
    # symbols + annotations
    for item in _items(data):
        sym = placed[item["id"]]
        body.append(sym.svg)
        body.append(_annotations(item, sym))

    canvas = data.get("canvas")
    vb = [0, 0, canvas[0], canvas[1]] if canvas else _auto_canvas(placed)

    status = str(meta.get("status_key") or meta.get("watermark") or "CONCEPT").upper()
    svg = isosheet.sheet(
        "".join(body), vb, meta, _legend_items(), data.get("notes", []),
        sheet_size=tuple(data.get("sheet", [1600, 1000])), watermark=status,
    )
    stem = str(meta.get("number", "pid")).replace(" ", "_")
    out = out_dir / f"{stem}.svg"
    out.write_text(svg, encoding="utf-8", newline="\n")
    return [out]
