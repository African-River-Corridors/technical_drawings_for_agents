"""Drawing document -> checks -> normalised content (clauses 4, 7 to 11, 16, 17.3)."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import jsonschema

from . import dimensions as dims
from . import furniture, resources, symbols
from . import image as images
from .entities import _hatch, _line, _polyline, _pt, _text
from .hatch import marks as hatch_marks
from .hatch import unit_mm
from .numbers import FIT_TOL_MM, angle, cos_sin, mm
from .rules import (
    ALL_RATIOS,
    BAR_HEIGHT,
    BAR_INSET,
    BAR_LABEL_BASELINE,
    BAR_RATIO_BASELINE,
    BAR_TEXT_HEIGHT,
    INSETS_MM,
    MAP_RATIOS,
    MIN_BAR_FRAME_HEIGHT,
    MIN_EXAGGERATED_FRAME_HEIGHT,
    MIN_NORTH_FRAME,
    NORTH_INSET,
    NORTH_LABEL_HEIGHT,
    NORTH_SIZE,
    TICK_LENGTH,
    VBAR_BOTTOM,
    VBAR_DIVISIONS,
    VBAR_LABEL_GAP,
    VBAR_RATIO_GAP,
    WORDS_BASELINE,
    ZONE_LETTERS,
    ZONES,
    Mapper,
    arc_sweep,
    bar_available,
    bar_labels,
    derived_bar_length,
    drawing_space,
    exaggeration_words,
    fit_scale,
    line_group,
    north_deg,
    ratios,
    text_box,
    text_width,
    vertical_bar_length,
    vertical_denominator,
    window_fits,
)

FURNITURE_LAYER = "TITLEBLOCK"  # 16.6.1
SUPPORTED_VERSION = (1, 2)  # 18.2
COLOUR_MEMBERS = ("print_colour", "fill_opacity", "halo_colour")  # 8.6.5


def _minor(doc) -> int:
    return int(doc["standard_version"].split(".")[1])


def exaggeration(vp) -> int:
    """7.3.6: the vertical exaggeration of a viewport, 1 when it has none."""
    return int(vp.get("vertical_exaggeration", 1))


def print_profile(doc) -> str:
    """8.6.4."""
    return doc["sheet"].get("print_profile", "monochrome")


def colour_table(doc) -> dict:
    """8.6.5: the colour members of each declared layer (empty under the monochrome profile)."""
    if print_profile(doc) != "colour":
        return {}
    return {item["name"]: {k: item[k] for k in COLOUR_MEMBERS if k in item} for item in doc["layers"] if any(k in item for k in COLOUR_MEMBERS)}


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
    if t == "image":
        x0, y0, x1, y1 = entity["bbox_m"]
        return [mapper.point((x0, y0)), mapper.point((x1, y1))]
    return []


# ------------------------------------------------------------------------------------------------
# the checks


def check_document(doc, base_dir=None) -> set[str]:
    """All error clauses of a document that passed the schema (4.6)."""
    errors: set[str] = set()
    major, minor = (int(p) for p in doc["standard_version"].split("."))
    if major != SUPPORTED_VERSION[0] or minor > SUPPORTED_VERSION[1]:
        return {"18.2"}  # 18.2: a document of another version is not read further

    sheet = doc["sheet"]
    size, orientation = sheet["size"], sheet.get("orientation", "landscape")
    group_name, group = line_group(size)
    if "line_group" in sheet and sheet["line_group"] != group_name:
        errors.add("8.2.2")
    _, _, ds = drawing_space(size, orientation)
    declared, _ = _layer_table(doc, errors)
    minor = _minor(doc)
    profile = print_profile(doc)
    coloured = [item for item in doc["layers"] if any(k in item for k in COLOUR_MEMBERS)]
    if minor < 1 and ("print_profile" in sheet or coloured):
        errors.add("18.2.1")
    elif coloured and profile != "colour":
        errors.add("8.6.5")
    ratio_list = ratios(minor)
    libs, lib_errors = symbols.load_libraries(doc, base_dir)
    errors |= lib_errors

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
        ex = exaggeration(vp)
        exaggerated = "vertical_exaggeration" in vp
        if exaggerated and minor < 2:
            errors.add("18.2.1")  # 7.3.6 is from version 1.2
        denominator = None
        if window_ok:
            if vp["scale"] == "fit":
                denominator = fit_scale(frame, window, rotation, ratio_list, ex)
                if denominator is None:
                    errors.add("7.5.2")
            elif vp["scale"] not in ratio_list:
                errors.add("18.2.1")  # a map ratio (7.3.5) in a document of version 1.0
            elif exaggerated and vertical_denominator(vp["scale"], ex, ratio_list) is None:
                errors.add("7.3.6.2")
            else:
                denominator = vp["scale"]
                if not window_fits(frame, window, denominator, rotation, ex):
                    errors.add("7.5.1")
        if exaggerated and minor >= 2:
            _check_exaggerated(vp, frame, rotation, ex, errors)
        if "scale_bar" in vp:
            _check_scale_bar(vp, frame, denominator, errors)
        if "north" in vp and (w < MIN_NORTH_FRAME or h < MIN_NORTH_FRAME):
            errors.add("7.10.7")
        mapper = Mapper(frame, window, denominator, rotation, ex) if denominator else None
        index = {e["id"]: e for e in vp["entities"] if "id" in e}
        chains = []
        for e in vp["entities"]:
            if "id" in e:
                ids.append(e["id"])
            _check_entity(e, declared, mapper, rotation, frame, errors)
            if e["type"] == "image":
                _check_image(e, base_dir, minor, profile, rotation, errors)
            if e["type"] == "symbol":
                symbol = libs.get(e["library"], {}).get(e["symbol"])
                if symbol is None:
                    if not lib_errors:
                        errors.add("13.3.2")
                elif mapper is not None and e["layer"] in declared:
                    sctx = _symbol_context(e, declared, group, mapper, rotation, vp["id"], denominator)
                    parts_, errs = symbols.parts(e, symbol, sctx)
                    errors |= errs
                    if any(not _inside_frame(px, py, frame) for part in parts_ for px, py in dims.part_points(part)):
                        errors.add("7.7.1")
            if mapper is not None and e["type"] in ("dimension", "leader") and e["layer"] in declared:
                res = _dimension_result(e, declared, group, mapper, rotation, vp["id"], index, libs, denominator)
                errors |= res.errors
                if not res.errors:
                    if any(not _inside_frame(px, py, frame) for part in res.entities for px, py in dims.part_points(part)):
                        errors.add("7.7.1")
                    if res.chain:
                        chains.append(res.chain)
        if not dims.check_chains(chains):
            errors.add("12.8")
    if len(ids) != len(set(ids)):
        errors.add("9.8")
    scales = [_resolved_scale(vp, ds, ratio_list) for vp in doc["viewports"]]
    plan = furniture.plan(doc, size, ds, group, scales, base_dir)
    errors |= plan.errors
    for frame in frames:
        if any(furniture._overlap(frame, rect) for _, rect in plan.regions):
            errors.add("7.2.4")
    for i in range(len(frames)):
        for j in range(i + 1, len(frames)):
            a, b = frames[i], frames[j]
            ox = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
            oy = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
            if ox > FIT_TOL_MM and oy > FIT_TOL_MM:
                errors.add("7.2.3")
    return errors


def _inside_frame(px, py, frame) -> bool:
    x, y, w, h = frame
    return x - FIT_TOL_MM <= px <= x + w + FIT_TOL_MM and y - FIT_TOL_MM <= py <= y + h + FIT_TOL_MM


def _symbol_context(e, declared, group, mapper, rotation, vid, denominator):
    role, line_type, _ = declared[e["layer"]]
    return symbols.SymbolContext(mapper, rotation, denominator, group, role, line_type, f"viewport.{vid}")


def _dimension_result(e, declared, group, mapper, rotation, vid, index, libs, denominator):
    role = e.get("role") or declared[e["layer"]][0]
    ctx = dims.Context(mapper, rotation, unit_mm(group["thin"]), group[role], f"viewport.{vid}", index, libs, denominator)
    return dims.dimension(e, ctx) if e["type"] == "dimension" else dims.leader(e, ctx)


def _resolved_scale(vp, ds, ratio_list=ALL_RATIOS):
    """The scale of a viewport, or None when it cannot be resolved (the error is reported elsewhere)."""
    window = vp["window"]
    if not (window["xmax_m"] > window["xmin_m"] and window["ymax_m"] > window["ymin_m"]):
        return None
    if vp["scale"] != "fit":
        return vp["scale"]
    return fit_scale(_frame(vp), window, vp.get("rotation_deg", 0.0) % 360.0, ratio_list, exaggeration(vp))


def _check_image(e, base_dir, minor, profile, rotation, errors):
    """9.10."""
    if minor < 1:
        errors.add("18.2.1")
        return
    if profile != "colour":
        errors.add("9.10.5")
    if rotation != 0:
        errors.add("9.10.4")
    x0, y0, x1, y1 = e["bbox_m"]
    if not (x1 > x0 and y1 > y0):
        errors.add("9.10.3")
    data, digest, info = images.read(e["path"], base_dir)
    if data is None or info is None:
        errors.add("9.10.1")
    elif digest != e["sha256"]:
        errors.add("9.10.2")


def _check_exaggerated(vp, frame, rotation, ex, errors):
    """7.3.6.3 to 7.3.6.9 and 7.9.12: what a viewport with a vertical exaggeration must have, and may not hold."""
    if rotation != 0:
        errors.add("7.3.6.3")
    if "scale_bar" not in vp:
        errors.add("7.3.6.4")
    if "north" in vp:
        errors.add("7.3.6.9")  # a section has no north; an exaggerated plan distorts every bearing
    for e in vp["entities"]:
        t = e["type"]
        if t == "image":
            errors.add("7.3.6.5")
        elif t in ("circle", "arc"):
            errors.add("7.3.6.6")
        elif t == "symbol":
            errors.add("7.3.6.7")
        elif t == "dimension" and not (e["kind"] == "linear" and e["orientation"] == "horizontal"):
            errors.add("7.3.6.8")
    w, h = frame[2], frame[3]
    if h < MIN_EXAGGERATED_FRAME_HEIGHT - FIT_TOL_MM or text_width(exaggeration_words(ex), BAR_TEXT_HEIGHT) > bar_available(w) + FIT_TOL_MM:
        errors.add("7.9.12")


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


def _vertical_scale_bar(vid, frame, denominator, ex, thin):
    """16.6.5: the vertical scale bar and the words of a viewport with a vertical exaggeration (7.9.11)."""
    x, y, _, h = frame
    nv = denominator // ex
    kv = 1000 / nv
    length = vertical_bar_length(h, nv)
    total = length * kv
    seg = total / VBAR_DIVISIONS
    xl, xr = x + BAR_INSET, x + BAR_INSET + BAR_HEIGHT
    y1 = y + h - VBAR_BOTTOM
    y0 = y1 - total
    origin, lay, solid = f"viewport.{vid}.vertical_scale_bar", FURNITURE_LAYER, "continuous"
    out = [_polyline(origin, lay, [(xl, y0), (xr, y0), (xr, y1), (xl, y1)], True, solid, thin)]
    for i in range(1, VBAR_DIVISIONS):
        out.append(_line(origin, lay, (xl, y1 - i * seg), (xr, y1 - i * seg), solid, thin))
    for i in range(0, VBAR_DIVISIONS, 2):
        out.append(_hatch(origin, lay, [(xl, y1 - i * seg), (xr, y1 - i * seg), (xr, y1 - (i + 1) * seg), (xl, y1 - (i + 1) * seg)], [], "solid", 0, thin))
    for i in range(VBAR_DIVISIONS + 1):
        out.append(_line(origin, lay, (xr, y1 - i * seg), (xr + TICK_LENGTH, y1 - i * seg), solid, thin))
    labels = bar_labels(length, 1, "m")  # 7.9.11: the two ends only, "0" and the length with its unit
    out.append(_text(origin, lay, (xr + VBAR_LABEL_GAP, y1), labels[0], BAR_TEXT_HEIGHT, 0, "left", "middle"))
    out.append(_text(origin, lay, (xr + VBAR_LABEL_GAP, y0), labels[1], BAR_TEXT_HEIGHT, 0, "left", "middle"))
    out.append(_text(origin, lay, (xl, y0 - VBAR_RATIO_GAP), f"Vertical 1:{nv}", BAR_TEXT_HEIGHT, 0, "left", "baseline"))
    yb = y + h - 18 + BAR_HEIGHT  # the bottom of the horizontal bar (16.6.3)
    out.append(_text(origin, lay, (xl, yb + WORDS_BASELINE), exaggeration_words(ex), BAR_TEXT_HEIGHT, 0, "left", "baseline"))
    return out


def _plate(vid, frame, which, denominator, spec, thin):
    """8.6.7: a white plate under the scale bar or the north arrow of a viewport that holds an image."""
    x, y, w, h = frame
    if which == "scale_bar":
        total = (spec.get("length_m") or derived_bar_length(w, denominator)) * 1000 / denominator
        x0, y0, x1, y1 = x + 2, y + h - 22, x + BAR_INSET + total + 10, y + h - 2
    else:
        x0, y0, x1, y1 = x + 8, y + 2, x + 30, y + 30
    item = _hatch(f"viewport.{vid}.backing", FURNITURE_LAYER, [(x0, y0), (x1, y0), (x1, y1), (x0, y1)], [], "solid", 0, thin)
    item["print_colour"] = "#FFFFFF"
    return item


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
    elif t == "image":
        geo = list(e["box_mm"])
        last = e["sha256"]
    return (
        e["origin"].encode(), t.encode(), e["layer"].encode(), e.get("id", "").encode(),
        e.get("line_type", "").encode(), e.get("width_mm", 0), geo, last.encode(),
        e.get("align_h", "").encode(), e.get("align_v", "").encode(),
    )


def normalise_document(doc, base_dir=None) -> dict:
    """Normalised content of a document that has no errors (16)."""
    sheet = doc["sheet"]
    size, orientation = sheet["size"], sheet.get("orientation", "landscape")
    W, H, ds = drawing_space(size, orientation)
    group_name, group = line_group(size)
    errors: set[str] = set()
    declared, _ = _layer_table(doc, errors)
    libs, _ = symbols.load_libraries(doc, base_dir)
    ratio_list = ratios(_minor(doc))
    entities = _sheet_furniture(size, orientation, group)
    entities += furniture.plan(doc, size, ds, group, [_resolved_scale(vp, ds, ratio_list) for vp in doc["viewports"]], base_dir).entities
    viewports = []
    for vp in doc["viewports"]:
        frame = _frame(vp)
        rotation = vp.get("rotation_deg", 0.0) % 360.0
        ex = exaggeration(vp)
        denominator = fit_scale(frame, vp["window"], rotation, ratio_list, ex) if vp["scale"] == "fit" else vp["scale"]
        mapper = Mapper(frame, vp["window"], denominator, rotation, ex)
        resolved = {"id": vp["id"], "scale": denominator, "frame": dict(vp["frame"])}
        if ex > 1:
            resolved["vertical_exaggeration"] = ex  # 16.2: only when there is one
        viewports.append(resolved)
        entities += _viewport_entities(vp, declared, group, mapper, rotation, base_dir)
        index = {e["id"]: e for e in vp["entities"] if "id" in e}
        for e in vp["entities"]:
            if e["type"] in ("dimension", "leader"):
                entities += _dimension_result(e, declared, group, mapper, rotation, vp["id"], index, libs, denominator).entities
            elif e["type"] == "symbol":
                sctx = _symbol_context(e, declared, group, mapper, rotation, vp["id"], denominator)
                entities += symbols.parts(e, libs[e["library"]][e["symbol"]], sctx)[0]
        plate = print_profile(doc) == "colour" and any(e["type"] == "image" for e in vp["entities"])
        if "scale_bar" in vp:
            entities += _scale_bar(vp["id"], frame, denominator, vp["scale_bar"], group["thin"])
            if plate:
                entities.append(_plate(vp["id"], frame, "scale_bar", denominator, vp["scale_bar"], group["thin"]))
            if ex > 1:
                entities += _vertical_scale_bar(vp["id"], frame, denominator, ex, group["thin"])
        if "north" in vp:
            entities += _north_arrow(vp["id"], frame, rotation, vp["north"], group["thin"])
            if plate:
                entities.append(_plate(vp["id"], frame, "north", denominator, vp["north"], group["thin"]))
    colours = colour_table(doc)
    if colours:
        _apply_colours(entities, colours)
    entities.sort(key=sort_key)
    viewports.sort(key=lambda v: v["id"].encode())
    sheet_out = {
        "size": size, "orientation": orientation, "line_group": group_name, "width_mm": W, "height_mm": H,
        "drawing_space": dict(zip(["x_mm", "y_mm", "width_mm", "height_mm"], ds)),
    }
    if print_profile(doc) == "colour":
        sheet_out["print_profile"] = "colour"  # 16.2: only the colour profile is named
    return {
        "standard_version": doc["standard_version"],
        "sheet": sheet_out,
        "viewports": viewports,
        "entities": entities,
    }


def entity_style(e, declared, group) -> tuple[str, float]:
    """8.7 and 9.7: the line type and width of an entity of a viewport."""
    layer_role, layer_lt, _ = declared[e["layer"]]
    role = e.get("role") or ("thin" if e["type"] == "centre_line" else layer_role)
    line_type = e.get("line_type") or ("chain" if e["type"] == "centre_line" else layer_lt)
    return line_type, group[role]


def declared_layers(doc) -> dict:
    """The declared layers as ``name -> (role, line_type, aci)`` (8.5)."""
    return _layer_table(doc, set())[0]


def _apply_colours(entities, colours):
    """8.6.6 and 16.4.1: the colour members of a layer, on each entity of a viewport that is on it.

    The sheet furniture (origins ``sheet.*``, ``title_block.*`` and the like) and the scale bars and
    north arrow of a viewport stay black.
    """
    for e in entities:
        o = e["origin"]
        if not o.startswith("viewport.") or o.endswith((".scale_bar", ".vertical_scale_bar", ".north_arrow", ".backing")) or e["layer"] not in colours:
            continue
        c = colours[e["layer"]]
        if e["type"] == "image":
            continue  # an image has its own pixels (9.10.6)
        if "print_colour" in c:
            e["print_colour"] = c["print_colour"]
        if e["type"] == "hatch" and e["pattern"] == "solid" and "fill_opacity" in c:
            e["fill_opacity"] = c["fill_opacity"]
        if e["type"] == "text" and "halo_colour" in c:
            e["halo_colour"] = c["halo_colour"]


def _image(origin, e, mapper, base_dir):
    """16.4 and 16.6.2."""
    x0, y0, x1, y1 = e["bbox_m"]
    (px0, py1), (px1, py0) = mapper.point((x0, y0)), mapper.point((x1, y1))
    _, _, info = images.read(e["path"], base_dir)
    item = {
        "type": "image", "origin": origin, "layer": e["layer"], "box_mm": [mm(px0), mm(py0), mm(px1), mm(py1)],
        "sha256": e["sha256"], "size_px": [info.width_px, info.height_px], "components": info.components,
        "opacity": mm(e.get("opacity", 1)),
    }
    if "id" in e:
        item["id"] = e["id"]
    return item


def _viewport_entities(vp, declared, group, mapper, rotation, base_dir=None):
    """16.6.2."""
    out = []
    origin = f"viewport.{vp['id']}"
    for e in vp["entities"]:
        t = e["type"]
        if t in ("dimension", "leader", "symbol"):
            continue  # their parts are made by dimensions.py and symbols.py (12.7, 13.7)
        if t == "image":
            out.append(_image(origin, e, mapper, base_dir))
            continue
        line_type, width = entity_style(e, declared, group)
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


def run_document(doc_or_text, base_dir=None) -> dict:
    """The result of one document: ``{"result": "ok", "normalised": ...}`` or an error list."""
    if isinstance(doc_or_text, str):
        doc, errors = parse_text(doc_or_text)
        if errors:
            return _error_result(errors)
    else:
        doc = doc_or_text
    errors = schema_errors(doc)
    if not errors:
        errors = check_document(doc, base_dir)
    if errors:
        return _error_result(errors)
    return {"result": "ok", "normalised": normalise_document(doc, base_dir)}


def _error_result(errors) -> dict:
    return {"result": "error", "errors": [{"clause": c} for c in sorted(errors)]}


def run_file(path) -> dict:
    return run_document(Path(path).read_text(encoding="utf-8"), Path(path).resolve().parent)


__all__ = ["ALL_RATIOS", "MAP_RATIOS", "check_document", "normalise_document", "parse_text", "run_document", "run_file", "schema_errors", "sort_key"]
_ = math
