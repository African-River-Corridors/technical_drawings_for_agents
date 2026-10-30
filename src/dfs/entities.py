"""Constructors of the normalised entities (clause 16.4)."""

from __future__ import annotations

from .numbers import angle, mm


def _pt(p) -> list[float]:
    return [mm(p[0]), mm(p[1])]


def _line(origin, layer, a, b, line_type, width, id_=None):
    e = {"type": "line", "origin": origin, "layer": layer, "from_mm": _pt(a), "to_mm": _pt(b), "line_type": line_type, "width_mm": width}
    if id_:
        e["id"] = id_
    return e


def _polyline(origin, layer, points, closed, line_type, width, id_=None):
    e = {"type": "polyline", "origin": origin, "layer": layer, "points_mm": [_pt(p) for p in points], "closed": closed, "line_type": line_type, "width_mm": width}
    if id_:
        e["id"] = id_
    return e


def _hatch(origin, layer, boundary, holes, pattern, angle_deg, width, id_=None):
    e = {
        "type": "hatch", "origin": origin, "layer": layer,
        "boundary_mm": [_pt(p) for p in boundary], "holes_mm": [[_pt(p) for p in hole] for hole in holes],
        "pattern": pattern, "angle_deg": angle(angle_deg), "width_mm": width,
    }
    if id_:
        e["id"] = id_
    return e


def _text(origin, layer, anchor, text, height, rotation, align_h, align_v, id_=None):
    e = {
        "type": "text", "origin": origin, "layer": layer, "anchor_mm": _pt(anchor), "text": text,
        "height_mm": height, "rotation_deg": angle(rotation), "align_h": align_h, "align_v": align_v,
    }
    if id_:
        e["id"] = id_
    return e


def _arc(origin, layer, centre, radius, start_deg, sweep_deg, line_type, width):
    return {
        "type": "arc", "origin": origin, "layer": layer, "centre_mm": _pt(centre), "radius_mm": mm(radius),
        "start_deg": angle(start_deg), "sweep_deg": mm(sweep_deg), "line_type": line_type, "width_mm": width,
    }
