"""The marks of a hatch (clause 10.5), made from the normalised hatch entity."""

from __future__ import annotations

import math
from decimal import Decimal

from . import resources
from .numbers import cos_sin, mm


def unit_mm(thin_width_mm: float) -> float:
    """10.5.1: u = 10 x the thin width, worked in decimal so that 0.18 gives 1.8."""
    factor = resources.hatch_patterns()["unit_factor"]
    return float(Decimal(str(thin_width_mm)) * factor)


def _loops(entity):
    return [entity["boundary_mm"], *entity["holes_mm"]]


def _crosses(sa, sb, c) -> bool:
    return (sa <= c < sb) or (sb <= c < sa)


def _segment_distance(p, a, b) -> float:
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    length2 = dx * dx + dy * dy
    if length2 == 0:
        return math.hypot(p[0] - ax, p[1] - ay)
    u = max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / length2))
    return math.hypot(p[0] - (ax + u * dx), p[1] - (ay + u * dy))


def marks(hatch: dict, origin: str, thin_width_mm: float) -> list[dict]:
    """The mark entities of one normalised hatch (empty for ``solid``)."""
    pattern = resources.hatch_patterns()["patterns"][hatch["pattern"]]
    if pattern["kind"] == "solid":
        return []
    u = unit_mm(thin_width_mm)
    c, s = cos_sin(hatch["angle_deg"])
    direction, normal = (c, -s), (s, c)

    def ts(p):
        return (direction[0] * p[0] + direction[1] * p[1], normal[0] * p[0] + normal[1] * p[1])

    def point(t, across):
        return (t * direction[0] + across * normal[0], t * direction[1] + across * normal[1])

    loops = [[ts(p) for p in loop] for loop in _loops(hatch)]
    all_t = [t for loop in loops for t, _ in loop]
    all_s = [v for loop in loops for _, v in loop]
    layer, width = hatch["layer"], hatch["width_mm"]
    out: list[dict] = []

    if pattern["kind"] == "lines":
        step = pattern["step_u"] * u
        for k in range(math.ceil(min(all_s) / step), math.floor(max(all_s) / step) + 1):
            c0 = k * step
            crossings = []
            for loop in loops:
                for i, (ta, sa) in enumerate(loop):
                    tb, sb = loop[(i + 1) % len(loop)]
                    if _crosses(sa, sb, c0):
                        crossings.append(ta + (c0 - sa) / (sb - sa) * (tb - ta))
            crossings.sort()
            for t1, t2 in zip(crossings[0::2], crossings[1::2]):
                if t2 > t1:
                    a, b = point(t1, c0), point(t2, c0)
                    out.append({
                        "type": "line", "origin": origin, "layer": layer,
                        "from_mm": [mm(a[0]), mm(a[1])], "to_mm": [mm(b[0]), mm(b[1])],
                        "line_type": pattern["line_type"], "width_mm": width,
                    })
        return out

    col, row = pattern["column_u"] * u, pattern["row_u"] * u
    edges = [(loop[i], loop[(i + 1) % len(loop)]) for loop in loops for i in range(len(loop))]
    for r in range(math.ceil(min(all_s) / row), math.floor(max(all_s) / row) + 1):
        offset = 0.5 if r % 2 else 0.0
        mark = pattern["marks"]["odd" if r % 2 else "even"]
        clearance = mark["clearance_u"] * u
        s0 = r * row
        for q in range(math.ceil(min(all_t) / col - offset), math.floor(max(all_t) / col - offset) + 1):
            t0 = (q + offset) * col
            crossing = sum(1 for (ta, sa), (tb, sb) in edges if _crosses(sa, sb, s0) and ta + (s0 - sa) / (sb - sa) * (tb - ta) > t0)
            if crossing % 2 == 0:
                continue
            centre = point(t0, s0)
            paper_edges = [(a, b) for a, b in ((_back(a, direction, normal), _back(b, direction, normal)) for a, b in edges)]
            if any(_segment_distance(centre, a, b) < clearance for a, b in paper_edges):
                continue
            if mark["shape"] == "dot":
                out.append({
                    "type": "circle", "origin": origin, "layer": layer,
                    "centre_mm": [mm(centre[0]), mm(centre[1])], "radius_mm": mm(mark["radius_u"] * u),
                    "line_type": "continuous", "width_mm": width,
                })
            else:
                pts = []
                for x, y in mark["points_u"]:
                    pts.append([mm(centre[0] + x * u * direction[0] + y * u * normal[0]), mm(centre[1] + x * u * direction[1] + y * u * normal[1])])
                out.append({
                    "type": "polyline", "origin": origin, "layer": layer, "points_mm": pts, "closed": True,
                    "line_type": "continuous", "width_mm": width,
                })
    return out


def _back(ts_point, direction, normal):
    t, across = ts_point
    return (t * direction[0] + across * normal[0], t * direction[1] + across * normal[1])
