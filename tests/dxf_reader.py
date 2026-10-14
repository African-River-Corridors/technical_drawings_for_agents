"""A small reader of ASCII DXF, written for the tests. It does not use ezdxf.

The DXF writer of the tool uses ezdxf. A reader built on the same library could repeat the same
mistake, so this one works on the group codes of the file itself. It reads only what the writer of
``dfs/render_dxf.py`` makes. It rebuilds the normalised content of the standard (clause 16) from the
DXF: paper-space entities directly, model-space entities through the viewports (clause 7.4).
"""

from __future__ import annotations

import re

from technical_drawings_for_agents.dfs.numbers import angle, cos_sin, mm, round_half_even
from technical_drawings_for_agents.dfs.rules import Mapper


def tags(text: str):
    lines = text.split("\n")
    return [(int(lines[i].strip()), lines[i + 1].rstrip("\r")) for i in range(0, len(lines) - 1, 2)]


class Record:
    def __init__(self, kind, items, block=None, section=None):
        self.kind, self.items, self.block, self.section = kind, items, block, section

    def get(self, code, default=None, nth=0):
        found = [v for c, v in self.items if c == code]
        return found[nth] if len(found) > nth else default

    def all(self, code):
        return [v for c, v in self.items if c == code]

    def f(self, code, default=None, nth=0):
        v = self.get(code, None, nth)
        return default if v is None else float(v)

    def xdata(self):
        """The ``key=value`` strings after the 1001 TDFA tag."""
        out, on = {}, False
        for c, v in self.items:
            if c == 1001:
                on = v == "TDFA"
            elif on and c == 1000 and "=" in v:
                k, _, val = v.partition("=")
                out[k] = val
        return out


def records(text: str) -> list[Record]:
    items = tags(text)
    out: list[Record] = []
    section = block = None
    i = 0
    while i < len(items):
        code, value = items[i]
        if code == 0 and value not in ("SECTION", "ENDSEC", "EOF"):
            j = i + 1
            while j < len(items) and items[j][0] != 0:
                j += 1
            rec = Record(value, items[i + 1 : j], block, section)
            if value == "BLOCK":
                block = rec.get(2)
            elif value == "ENDBLK":
                block = None
            out.append(rec)
            i = j
        elif code == 0 and value == "SECTION":
            section = items[i + 1][1]
            i += 2
        else:
            i += 1
    return out


# ---------------------------------------------------------------------------------------------


def linetype_id(name: str) -> str:
    if name.upper() == "CONTINUOUS":
        return "continuous"
    m = re.fullmatch(r"TDFA_([A-Z_]+?)_\d{3}", name)
    assert m, name
    return m.group(1).lower()


def sheet_size(recs) -> tuple[float, float]:
    """The paper size of the layout SHEET, from its plot settings (codes 44 and 45)."""
    for r in recs:
        if r.kind == "LAYOUT" and r.get(1, nth=1) == "SHEET":
            return r.f(44), r.f(45)
    raise AssertionError("no layout SHEET")


def _loops(r: Record):
    """The polyline paths of a HATCH, in order."""
    items = r.items
    start = next(k for k, (c, _) in enumerate(items) if c == 91)
    n_paths = int(items[start][1])
    k = start + 1
    loops = []
    for _ in range(n_paths):
        assert items[k][0] == 92 and int(items[k][1]) & 2, "polyline paths only"
        k += 1
        assert items[k][0] == 72
        k += 1
        assert items[k][0] == 73
        k += 1
        count = int(items[k][1])
        k += 1
        loop = []
        for _ in range(count):
            assert items[k][0] == 10 and items[k + 1][0] == 20
            loop.append((float(items[k][1]), float(items[k + 1][1])))
            k += 2
        assert items[k][0] == 97 and int(items[k][1]) == 0
        k += 1
        loops.append(loop)
    return loops


def _text_point(r: Record) -> tuple[float, float]:
    """The alignment point: the second point when the text is not left and baseline."""
    if r.get(11) is not None and (int(r.get(72, 0)) or int(r.get(73, 0))):
        return r.f(11), r.f(21)
    return r.f(10), r.f(20)


HALIGN = {0: "left", 1: "centre", 2: "right"}
VALIGN = {0: "baseline", 2: "middle", 3: "top"}


def _style(r: Record):
    return linetype_id(r.get(6)), int(r.get(370)) / 100


def read_normalised(text: str) -> dict:
    """Make the sheet facts, the viewports and the entities of the normalised content from DXF text."""
    recs = records(text)
    width, height = sheet_size(recs)
    def in_paper(r):  # code 67 = 1 marks paper space; the active layout is written in ENTITIES
        in_block = r.section == "BLOCKS" and r.block and r.block.startswith("*Paper_Space")
        return bool(in_block or (r.section == "ENTITIES" and r.get(67) == "1"))

    paper = [r for r in recs if in_paper(r)]
    model = [r for r in recs if r.section == "ENTITIES" and r.get(67) != "1"]
    viewports = {}
    for r in paper:
        x = r.xdata()
        if r.kind == "VIEWPORT" and "id" in x:
            cx, cy, w, h = r.f(10), r.f(20), r.f(40), r.f(41)
            n = int(x["scale"])
            assert round(r.f(45) * 1000 / h) == n  # the viewport plots at 1:N
            viewports[x["id"]] = {
                "n": n,
                "frame": (cx - w / 2, height - (cy + h / 2), w, h),
                "centre": (r.f(12), r.f(22)),
                "twist": r.f(51),
            }
    entities = []
    for r in paper:
        x = r.xdata()
        if "origin" in x:
            entities.append(_paper_entity(r, x, height))
    for r in model:
        x = r.xdata()
        if "vp" in x:
            entities.append(_model_entity(r, x, viewports[x["vp"]]))
    return {
        "sheet": {"width_mm": width, "height_mm": height},
        "viewports": viewports,
        "entities": entities,
    }


def _common(kind, origin, layer, x):
    e = {"type": kind, "origin": origin, "layer": layer}
    if "id" in x and x["id"] != "None":
        e["id"] = x["id"]
    return e


def _paper_entity(r: Record, x, height):
    kind = x["type"]
    e = _common(kind, x["origin"], r.get(8), x)
    flip = lambda px, py: [mm(px), mm(height - py)]  # noqa: E731
    if kind == "line":
        e["from_mm"], e["to_mm"] = flip(r.f(10), r.f(20)), flip(r.f(11), r.f(21))
        e["line_type"], e["width_mm"] = _style(r)
    elif kind == "polyline":
        xs, ys = [float(v) for v in r.all(10)], [float(v) for v in r.all(20)]
        e["points_mm"] = [flip(a, b) for a, b in zip(xs, ys)]
        e["closed"] = bool(int(r.get(70, 0)) & 1)
        e["line_type"], e["width_mm"] = _style(r)
    elif kind == "circle":
        e["centre_mm"], e["radius_mm"] = flip(r.f(10), r.f(20)), mm(r.f(40))
        e["line_type"], e["width_mm"] = _style(r)
    elif kind == "hatch":
        loops = [[flip(px, py) for px, py in loop] for loop in _loops(r)]
        e["boundary_mm"], e["holes_mm"] = loops[0], loops[1:]
        e["pattern"], e["angle_deg"], e["width_mm"] = x["pattern"], angle(float(x["angle"])), int(r.get(370)) / 100
    elif kind == "text":
        px, py = _text_point(r)
        e["anchor_mm"], e["text"], e["height_mm"] = flip(px, py), r.get(1), mm(r.f(40))
        e["rotation_deg"] = angle(r.f(50, 0.0))
        e["align_h"], e["align_v"] = HALIGN[int(r.get(72, 0))], VALIGN[int(r.get(73, 0))]
    return e


def _model_entity(r: Record, x, vp):
    kind = x["type"]
    n = vp["n"]
    mapper = Mapper(vp["frame"], {"xmin_m": vp["centre"][0], "xmax_m": vp["centre"][0], "ymin_m": vp["centre"][1], "ymax_m": vp["centre"][1]}, n, vp["twist"])
    k = 1000 / n
    point = lambda px, py: [mm(c) for c in mapper.point((px, py))]  # noqa: E731
    layer = r.get(8)
    origin = f"viewport.{x['vp']}"
    if kind in ("line", "centre_line"):
        e = _common("line", origin, layer, x)
        e["from_mm"], e["to_mm"] = point(r.f(10), r.f(20)), point(r.f(11), r.f(21))
        e["line_type"], e["width_mm"] = _style(r)
    elif kind == "polyline":
        e = _common("polyline", origin, layer, x)
        xs, ys = [float(v) for v in r.all(10)], [float(v) for v in r.all(20)]
        e["points_mm"] = [point(a, b) for a, b in zip(xs, ys)]
        e["closed"] = bool(int(r.get(70, 0)) & 1)
        e["line_type"], e["width_mm"] = _style(r)
    elif kind in ("circle", "arc"):
        e = _common(kind, origin, layer, x)
        e["centre_mm"], e["radius_mm"] = point(r.f(10), r.f(20)), mm(r.f(40) * k)
        if kind == "arc":
            e["start_deg"] = angle(r.f(50) + vp["twist"])
            e["sweep_deg"] = mm((r.f(51) - r.f(50)) % 360.0)
        e["line_type"], e["width_mm"] = _style(r)
    elif kind == "hatch":
        e = _common("hatch", origin, layer, x)
        loops = [[point(px, py) for px, py in loop] for loop in _loops(r)]
        e["boundary_mm"], e["holes_mm"] = loops[0], loops[1:]
        e["pattern"] = x["pattern"]
        e["angle_deg"] = angle(float(x["angle"]) + vp["twist"])
        e["width_mm"] = int(r.get(370)) / 100
    else:
        e = _common("text", origin, layer, x)
        px, py = _text_point(r)
        e["anchor_mm"], e["text"] = point(px, py), r.get(1)
        e["height_mm"] = mm(r.f(40) * 1000 / n)
        e["rotation_deg"] = angle(r.f(50, 0.0) + vp["twist"])
        e["align_h"], e["align_v"] = HALIGN[int(r.get(72, 0))], VALIGN[int(r.get(73, 0))]
    return e


def style_tables(text: str):
    """Layers, line types, the text style and the header variables that the standard fixes."""
    recs = records(text)
    layers = {r.get(2): {"aci": int(r.get(62)), "linetype": r.get(6), "lineweight": int(r.get(370)), "plot": int(r.get(290, 1))} for r in recs if r.kind == "LAYER"}
    ltypes = {r.get(2): [float(v) for v in r.all(49)] for r in recs if r.kind == "LTYPE"}
    styles = {r.get(2): r.get(3) for r in recs if r.kind == "STYLE"}
    items = tags(text)
    header = {}
    for i, (c, v) in enumerate(items):
        if c == 9 and v in ("$INSUNITS", "$PSLTSCALE", "$MEASUREMENT"):
            header[v] = int(items[i + 1][1])
    return layers, ltypes, styles, header


_ = (cos_sin, round_half_even)
