"""Drawing document -> checks -> normalised content (clauses 4, 7 to 11, 16, 17.3)."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import jsonschema

from . import resources
from .hatch import marks as hatch_marks
from .numbers import FIT_TOL_MM, angle, cos_sin, mm
from .rules import (
    ALL_RATIOS,
    BAR_HEIGHT,
    BAR_INSET,
    BAR_LABEL_BASELINE,
    BAR_RATIO_BASELINE,
    BAR_TEXT_HEIGHT,
    INSETS_MM,
    MIN_BAR_FRAME_HEIGHT,
    MIN_NORTH_FRAME,
    NORTH_INSET,
    NORTH_LABEL_HEIGHT,
    NORTH_SIZE,
    TICK_LENGTH,
    ZONE_LETTERS,
    ZONES,
    Mapper,
    arc_sweep,
    bar_available,
    bar_labels,
    derived_bar_length,
    drawing_space,
    fit_scale,
    line_group,
    north_deg,
    text_box,
    window_fits,
)

FURNITURE_LAYER = "TITLEBLOCK"  # 16.6.1
SUPPORTED_VERSION = (1, 0)  # 18.2


class _DuplicateKey(ValueError):
    pass


def _no_duplicates(pairs):
    keys = [k for k, _ in pairs]
    if len(keys) != len(set(keys)):
        raise _DuplicateKey("repeated key")
    return dict(pairs)


def parse_text(text: str) -> tuple[Any, set[str]]:
    """Parse JSON text. Returns ``(document, errors)``: 4.1 for bad JSON, 4.4 for a repeated key."""
    try:
        return json.loads(text, object_pairs_hook=_no_duplicates), set()
    except _DuplicateKey:
        return None, {"4.4"}
    except ValueError:
        return None, {"4.1"}


# ------------------------------------------------------------------------------------------------
# schema


def schema_errors(doc) -> set[str]:
    """18.6: the clause of each failed schema rule."""
    validator = jsonschema.Draft202012Validator(resources.schema())
    clauses = set()
    for error in validator.iter_errors(doc):
        clauses.add(error.schema.get("x-clause", "4.2") if isinstance(error.schema, dict) else "4.2")
    return clauses


# ------------------------------------------------------------------------------------------------
# resolved facts


def _frame(vp) -> tuple[float, float, float, float]:
    f = vp["frame"]
    return (f["x_mm"], f["y_mm"], f["width_mm"], f["height_mm"])


def _layer_table(doc, errors):
    """8.5: the declared layers as ``name -> (role, line_type, aci)``."""
    styles = {row["name"]: row for row in resources.line_styles()["layers"]}
    declared: dict[str, tuple[str, str, int]] = {}
    seen = set()
    for item in doc["layers"]:
        name = item["name"]
        if name in seen:
            errors.add("8.5.5")
            continue
        seen.add(name)
        if name in styles:
            std = styles[name]
            for key in ("role", "line_type", "aci"):
                if key in item and item[key] != std[key]:
                    errors.add("8.5.3")
            declared[name] = (std["role"], std["line_type"], std["aci"])
        else:
            declared[name] = (item["role"], item["line_type"], item["aci"])
    colours = [v[2] for v in declared.values()]
    if len(set(colours)) != len(colours):
        errors.add("8.6.3")
    return declared, styles


# ------------------------------------------------------------------------------------------------
# geometry helpers for 10.3 and 7.7.2


def _area(points) -> float:
    return sum(points[i][0] * points[(i + 1) % len(points)][1] - points[(i + 1) % len(points)][0] * points[i][1] for i in range(len(points))) / 2


def _orient(a, b, c) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _on_segment(a, b, p) -> bool:
    return min(a[0], b[0]) <= p[0] <= max(a[0], b[0]) and min(a[1], b[1]) <= p[1] <= max(a[1], b[1])


def _segments_touch(a, b, c, d) -> bool:
    """True when the closed segments ab and cd share any point."""
    o1, o2, o3, o4 = _orient(a, b, c), _orient(a, b, d), _orient(c, d, a), _orient(c, d, b)
    if (o1 > 0) != (o2 > 0) and (o3 > 0) != (o4 > 0) and 0 not in (o1, o2, o3, o4):
        return True
    return (
        (o1 == 0 and _on_segment(a, b, c))
        or (o2 == 0 and _on_segment(a, b, d))
        or (o3 == 0 and _on_segment(c, d, a))
        or (o4 == 0 and _on_segment(c, d, b))
    )


def _edges(loop):
    return [(loop[i], loop[(i + 1) % len(loop)]) for i in range(len(loop))]


def _loop_ok(loop) -> bool:
    """10.3: no repeated point in a row, a non-zero area, no crossing or touching of its own edges."""
    n = len(loop)
    if any(loop[i] == loop[(i + 1) % n] for i in range(n)):
        return False
    if abs(_area(loop)) < 1e-12:
        return False
    edges = _edges(loop)
    for i in range(n):
        for j in range(i + 1, n):
            if j == i + 1 or (i == 0 and j == n - 1):
                # adjacent edges share exactly their common vertex: reject only an overlap
                a, b = edges[i]
                c, d = edges[j]
                shared = b if j == i + 1 else a
                other_i = a if j == i + 1 else b
                other_j = d if j == i + 1 else c
                if _orient(other_i, shared, other_j) == 0 and (
                    (other_i[0] - shared[0]) * (other_j[0] - shared[0]) + (other_i[1] - shared[1]) * (other_j[1] - shared[1]) > 0
                ):
                    return False
                continue
            if _segments_touch(*edges[i], *edges[j]):
                return False
    return True


def _inside(point, loop) -> bool:
    x, y = point
    inside = False
    for a, b in _edges(loop):
        if (a[1] > y) != (b[1] > y) and x < (b[0] - a[0]) * (y - a[1]) / (b[1] - a[1]) + a[0]:
            inside = not inside
    return inside


def _loops_cross(one, two) -> bool:
    return any(_segments_touch(*e, *f) for e in _edges(one) for f in _edges(two))


def _hatch_ok(boundary, holes) -> bool:
    if not _loop_ok(boundary):
        return False
    for hole in holes:
        if not _loop_ok(hole) or _loops_cross(boundary, hole) or not all(_inside(p, boundary) for p in hole):
            return False
    for i in range(len(holes)):
        for j in range(i + 1, len(holes)):
            if _loops_cross(holes[i], holes[j]) or _inside(holes[i][0], holes[j]) or _inside(holes[j][0], holes[i]):
                return False
    return True


def _arc_paper(entity, mapper, rotation):
    """Paper centre, radius, start angle (visual, ccw) and sweep of an arc entity."""
    cx, cy = mapper.point(entity["centre_m"])
    r = entity["radius_m"] * mapper.k
    sweep = arc_sweep(entity["start_deg"], entity["end_deg"])
    return cx, cy, r, (entity["start_deg"] + rotation) % 360.0, sweep


def _points_for_containment(entity, mapper, rotation, resolved_text_box):
    t = entity["type"]
    if t in ("line", "centre_line"):
        return [mapper.point(entity["from_m"]), mapper.point(entity["to_m"])]
    if t == "polyline":
        return [mapper.point(p) for p in entity["points_m"]]
    if t == "hatch":
        return [mapper.point(p) for p in entity["boundary_m"]]
    if t == "circle":
        cx, cy = mapper.point(entity["centre_m"])
        r = entity["radius_m"] * mapper.k
        return [(cx - r, cy), (cx + r, cy), (cx, cy - r), (cx, cy + r)]
    if t == "arc":
        cx, cy, r, start, sweep = _arc_paper(entity, mapper, rotation)
        pts = []
        angles = [start, start + sweep]
        angles += [a for a in (0, 90, 180, 270) if (a - start) % 360.0 <= sweep]
        for a in angles:
            c, s = cos_sin(a)
            pts.append((cx + r * c, cy - r * s))
        return pts
    if t == "text":
        return resolved_text_box
    return []


# ------------------------------------------------------------------------------------------------
# the checks


def check_document(doc) -> set[str]:
    """All error clauses of a document that passed the schema (4.6)."""
    errors: set[str] = set()
    major, minor = (int(p) for p in doc["standard_version"].split("."))
    if major != SUPPORTED_VERSION[0] or minor > SUPPORTED_VERSION[1]:
        return {"18.2"}  # 18.2: a document of another version is not read further

    sheet = doc["sheet"]
    size, orientation = sheet["size"], sheet.get("orientation", "landscape")
    group_name, _ = line_group(size)
    if "line_group" in sheet and sheet["line_group"] != group_name:
        errors.add("8.2.2")
    _, _, ds = drawing_space(size, orientation)
    declared, _ = _layer_table(doc, errors)

    ids: list[str] = []
    frames = []
    for vp in doc["viewports"]:
        ids.append(vp["id"])
        frame = _frame(vp)
        frames.append(frame)
        x, y, w, h = frame
        dx, dy, dw, dh = ds
        if x < dx - FIT_TOL_MM or y < dy - FIT_TOL_MM or x + w > dx + dw + FIT_TOL_MM or y + h > dy + dh + FIT_TOL_MM:
            errors.add("7.2.2")
        window = vp["window"]
        rotation = vp.get("rotation_deg", 0.0) % 360.0
        window_ok = window["xmax_m"] > window["xmin_m"] and window["ymax_m"] > window["ymin_m"]
        if not window_ok:
            errors.add("7.6.1")
        denominator = None
        if window_ok:
            if vp["scale"] == "fit":
                denominator = fit_scale(frame, window, rotation)
                if denominator is None:
                    errors.add("7.5.2")
            else:
                denominator = vp["scale"]
                if not window_fits(frame, window, denominator, rotation):
                    errors.add("7.5.1")
        if "scale_bar" in vp:
            _check_scale_bar(vp, frame, denominator, errors)
        if "north" in vp and (w < MIN_NORTH_FRAME or h < MIN_NORTH_FRAME):
            errors.add("7.10.7")
        mapper = Mapper(frame, window, denominator, rotation) if denominator else None
        for e in vp["entities"]:
            if "id" in e:
                ids.append(e["id"])
            _check_entity(e, declared, mapper, rotation, frame, errors)
    if len(ids) != len(set(ids)):
        errors.add("9.8")
    for i in range(len(frames)):
        for j in range(i + 1, len(frames)):
            a, b = frames[i], frames[j]
            ox = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
            oy = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
            if ox > FIT_TOL_MM and oy > FIT_TOL_MM:
                errors.add("7.2.3")
    return errors


def _check_scale_bar(vp, frame, denominator, errors):
    w, h = frame[2], frame[3]
    sb = vp["scale_bar"]
    if h < MIN_BAR_FRAME_HEIGHT:
        errors.add("7.9.10")
    if denominator is None:
        return
    k = 1000 / denominator
    if "length_m" in sb:
        if sb["length_m"] * k > bar_available(w) + FIT_TOL_MM:
            errors.add("7.9.3")
    elif derived_bar_length(w, denominator) is None:
        errors.add("7.9.4")


def _check_entity(e, declared, mapper, rotation, frame, errors):
    t = e["type"]
    if e["layer"] not in declared:
        errors.add("8.5.1")
    if t in ("line", "centre_line") and e["from_m"] == e["to_m"]:
        errors.add("9.3")
    if t == "polyline":
        pts = e["points_m"]
        n = len(pts)
        if any(pts[i] == pts[i + 1] for i in range(n - 1)) or (e.get("closed") and pts[0] == pts[-1]):
            errors.add("9.4.1")
    if t == "arc" and arc_sweep(e["start_deg"], e["end_deg"]) is None:
        errors.add("9.5.1")
        return
    if t == "hatch" and not _hatch_ok(e["boundary_m"], e.get("holes_m", [])):
        errors.add("10.3")
    if mapper is None:
        return
    text_corners = []
    if t == "text":
        anchor = mapper.point(e["anchor_m"])
        text_corners = text_box(anchor, e["text"], e["height_mm"], e.get("rotation_deg", 0.0), e.get("align_h", "left"), e.get("align_v", "baseline"))
    x, y, w, h = frame
    for px, py in _points_for_containment(e, mapper, rotation, text_corners):
        if px < x - FIT_TOL_MM or px > x + w + FIT_TOL_MM or py < y - FIT_TOL_MM or py > y + h + FIT_TOL_MM:
            errors.add("7.7.1")
            break


# ------------------------------------------------------------------------------------------------
# normalised content (16)


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


def _sheet_furniture(size, orientation, group):
    """16.6.1."""
    W, H, _ = drawing_space(size, orientation)
    L, T, R, B = INSETS_MM[size]
    long_n, short_n = ZONES[size]
    nh, nv = (long_n, short_n) if orientation == "landscape" else (short_n, long_n)
    zw, zh = (W - L - R) / nh, (H - T - B) / nv
    lay, solid = FURNITURE_LAYER, "continuous"
    out = [_polyline("sheet.border", lay, [(L, T), (W - R, T), (W - R, H - B), (L, H - B)], True, solid, group["extra_thick"])]
    for a, b in [((W / 2, 0), (W / 2, T + 5)), ((W / 2, H), (W / 2, H - B - 5)), ((0, H / 2), (L + 5, H / 2)), ((W, H / 2), (W - R - 5, H / 2))]:
        out.append(_line("sheet.centring_mark", lay, a, b, solid, 0.5))
    for k in range(1, nh):
        x = L + k * zw
        out.append(_line("sheet.zone_mark", lay, (x, 0), (x, T), solid, 0.35))
        out.append(_line("sheet.zone_mark", lay, (x, H), (x, H - B), solid, 0.35))
    for j in range(1, nv):
        y = T + j * zh
        out.append(_line("sheet.zone_mark", lay, (0, y), (L, y), solid, 0.35))
        out.append(_line("sheet.zone_mark", lay, (W, y), (W - R, y), solid, 0.35))
    for k in range(1, nh + 1):
        x = L + (k - 0.5) * zw
        out.append(_text("sheet.zone_label", lay, (x, T / 2), str(k), 3.5, 0, "centre", "middle"))
        out.append(_text("sheet.zone_label", lay, (x, H - B / 2), str(k), 3.5, 0, "centre", "middle"))
    for j in range(1, nv + 1):
        y = T + (j - 0.5) * zh
        out.append(_text("sheet.zone_label", lay, (L / 2, y), ZONE_LETTERS[j - 1], 3.5, 0, "centre", "middle"))
        out.append(_text("sheet.zone_label", lay, (W - R / 2, y), ZONE_LETTERS[j - 1], 3.5, 0, "centre", "middle"))
    return out


def _scale_bar(vid, frame, denominator, sb, thin):
    """16.6.3."""
    x, y, w, h = frame
    k = 1000 / denominator
    length = sb.get("length_m") or derived_bar_length(w, denominator)
    div, unit = sb.get("divisions", 5), sb.get("unit", "m")
    total = length * k
    seg = total / div
    x0, y0 = x + BAR_INSET, y + h - 18
    yb = y0 + BAR_HEIGHT
    origin, lay, solid = f"viewport.{vid}.scale_bar", FURNITURE_LAYER, "continuous"
    out = [_polyline(origin, lay, [(x0, y0), (x0 + total, y0), (x0 + total, yb), (x0, yb)], True, solid, thin)]
    for i in range(1, div):
        out.append(_line(origin, lay, (x0 + i * seg, y0), (x0 + i * seg, yb), solid, thin))
    for i in range(0, div, 2):
        out.append(_hatch(origin, lay, [(x0 + i * seg, y0), (x0 + (i + 1) * seg, y0), (x0 + (i + 1) * seg, yb), (x0 + i * seg, yb)], [], "solid", 0, thin))
    for i in range(div + 1):
        out.append(_line(origin, lay, (x0 + i * seg, yb), (x0 + i * seg, yb + TICK_LENGTH), solid, thin))
    for i, label in enumerate(bar_labels(length, div, unit)):
        out.append(_text(origin, lay, (x0 + i * seg, yb + BAR_LABEL_BASELINE), label, BAR_TEXT_HEIGHT, 0, "centre", "baseline"))
    out.append(_text(origin, lay, (x0 + total / 2, yb + BAR_RATIO_BASELINE), f"1:{denominator}", BAR_TEXT_HEIGHT, 0, "centre", "baseline"))
    return out


def _north_arrow(vid, frame, rotation, nb, thin):
    """16.6.4."""
    cx, cy = frame[0] + NORTH_INSET + NORTH_SIZE / 2, frame[1] + NORTH_INSET + NORTH_SIZE / 2
    phi = north_deg(rotation, nb.get("model_bearing_deg", 0))
    c, s = cos_sin(phi)

    def turn(p):
        dx, dy = p[0] - cx, p[1] - cy
        return (cx + dx * c - dy * s, cy + dx * s + dy * c)

    origin, lay = f"viewport.{vid}.north_arrow", FURNITURE_LAYER
    head = [turn(p) for p in [(cx, cy - 9), (cx - 4.14, cy + 5.76), (cx, cy + 0.9), (cx + 4.14, cy + 5.76)]]
    return [
        _hatch(origin, lay, head, [], "solid", 0, thin),
        _line(origin, lay, turn((cx, cy + 0.9)), turn((cx, cy + 9)), "continuous", thin),
        _text(origin, lay, turn((cx, cy - 11.5)), nb.get("label", "N"), NORTH_LABEL_HEIGHT, 0, "centre", "baseline"),
    ]


def sort_key(e):
    """16.8."""
    t = e["type"]
    geo: list[float] = []
    last = ""
    if t == "line":
        geo = [*e["from_mm"], *e["to_mm"]]
    elif t == "polyline":
        geo = [c for p in e["points_mm"] for c in p] + [1 if e["closed"] else 0]
    elif t == "arc":
        geo = [*e["centre_mm"], e["radius_mm"], e["start_deg"], e["sweep_deg"]]
    elif t == "circle":
        geo = [*e["centre_mm"], e["radius_mm"]]
    elif t == "hatch":
        geo = [c for p in e["boundary_mm"] for c in p] + [len(e["holes_mm"])] + [c for h in e["holes_mm"] for p in h for c in p] + [e["angle_deg"]]
        last = e["pattern"]
    elif t == "text":
        geo = [*e["anchor_mm"], e["height_mm"], e["rotation_deg"]]
        last = e["text"]
    return (
        e["origin"].encode(), t.encode(), e["layer"].encode(), e.get("id", "").encode(),
        e.get("line_type", "").encode(), e.get("width_mm", 0), geo, last.encode(),
        e.get("align_h", "").encode(), e.get("align_v", "").encode(),
    )


def normalise_document(doc) -> dict:
    """Normalised content of a document that has no errors (16)."""
    sheet = doc["sheet"]
    size, orientation = sheet["size"], sheet.get("orientation", "landscape")
    W, H, ds = drawing_space(size, orientation)
    group_name, group = line_group(size)
    errors: set[str] = set()
    declared, _ = _layer_table(doc, errors)
    entities = _sheet_furniture(size, orientation, group)
    viewports = []
    for vp in doc["viewports"]:
        frame = _frame(vp)
        rotation = vp.get("rotation_deg", 0.0) % 360.0
        denominator = fit_scale(frame, vp["window"], rotation) if vp["scale"] == "fit" else vp["scale"]
        mapper = Mapper(frame, vp["window"], denominator, rotation)
        viewports.append({"id": vp["id"], "scale": denominator, "frame": dict(vp["frame"])})
        entities += _viewport_entities(vp, declared, group, mapper, rotation)
        if "scale_bar" in vp:
            entities += _scale_bar(vp["id"], frame, denominator, vp["scale_bar"], group["thin"])
        if "north" in vp:
            entities += _north_arrow(vp["id"], frame, rotation, vp["north"], group["thin"])
    entities.sort(key=sort_key)
    viewports.sort(key=lambda v: v["id"].encode())
    return {
        "standard_version": doc["standard_version"],
        "sheet": {
            "size": size, "orientation": orientation, "line_group": group_name, "width_mm": W, "height_mm": H,
            "drawing_space": dict(zip(["x_mm", "y_mm", "width_mm", "height_mm"], ds)),
        },
        "viewports": viewports,
        "entities": entities,
    }


def _viewport_entities(vp, declared, group, mapper, rotation):
    """16.6.2."""
    out = []
    origin = f"viewport.{vp['id']}"
    for e in vp["entities"]:
        t = e["type"]
        layer_role, layer_lt, _ = declared[e["layer"]]
        role = e.get("role") or ("thin" if t == "centre_line" else layer_role)
        line_type = e.get("line_type") or ("chain" if t == "centre_line" else layer_lt)
        width = group[role]
        id_ = e.get("id")
        if t in ("line", "centre_line"):
            out.append(_line(origin, e["layer"], mapper.point(e["from_m"]), mapper.point(e["to_m"]), line_type, width, id_))
        elif t == "polyline":
            out.append(_polyline(origin, e["layer"], [mapper.point(p) for p in e["points_m"]], e.get("closed", False), line_type, width, id_))
        elif t in ("circle", "arc"):
            cx, cy = mapper.point(e["centre_m"])
            item = {"type": t, "origin": origin, "layer": e["layer"], "centre_mm": _pt((cx, cy)), "radius_mm": mm(e["radius_m"] * mapper.k)}
            if t == "arc":
                item["start_deg"] = angle(e["start_deg"] + rotation)
                item["sweep_deg"] = mm(arc_sweep(e["start_deg"], e["end_deg"]))
            item["line_type"], item["width_mm"] = line_type, width
            if id_:
                item["id"] = id_
            out.append(item)
        elif t == "hatch":
            item = _hatch(origin, e["layer"], [mapper.point(p) for p in e["boundary_m"]], [[mapper.point(p) for p in hole] for hole in e.get("holes_m", [])], e["pattern"], e.get("angle_deg", 0) + rotation, width, id_)
            out.append(item)
            out += hatch_marks(item, origin + ".hatch", group["thin"])
        elif t == "text":
            out.append(_text(origin, e["layer"], mapper.point(e["anchor_m"]), e["text"], e["height_mm"], e.get("rotation_deg", 0.0), e.get("align_h", "left"), e.get("align_v", "baseline"), id_))
    return out


# ------------------------------------------------------------------------------------------------
# the runner (17.3)


def run_document(doc_or_text) -> dict:
    """The result of one document: ``{"result": "ok", "normalised": ...}`` or an error list."""
    if isinstance(doc_or_text, str):
        doc, errors = parse_text(doc_or_text)
        if errors:
            return _error_result(errors)
    else:
        doc = doc_or_text
    errors = schema_errors(doc)
    if not errors:
        errors = check_document(doc)
    if errors:
        return _error_result(errors)
    return {"result": "ok", "normalised": normalise_document(doc)}


def _error_result(errors) -> dict:
    return {"result": "error", "errors": [{"clause": c} for c in sorted(errors)]}


def run_file(path) -> dict:
    return run_document(Path(path).read_text(encoding="utf-8"))


__all__ = ["ALL_RATIOS", "check_document", "normalise_document", "parse_text", "run_document", "run_file", "schema_errors", "sort_key"]
_ = math
