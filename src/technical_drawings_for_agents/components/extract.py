"""Materialise component geometry from supplier DXF windows."""

from __future__ import annotations

import math
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterable

import ezdxf

from .spec import CleanupRule, ComponentSpecError, Feature, FromDxf, Point, Shape

METRES_PER_UNIT = {
    "mm": 0.001,
    "cm": 0.01,
    "m": 1.0,
    "in": 0.0254,
    "ft": 0.3048,
}


def materialize_features(features: Iterable[Feature]) -> list[Feature]:
    """Expand unresolved ``from_dxf`` features into ordinary geometry features."""

    doc_cache: dict[Path, Any] = {}
    expanded: list[Feature] = []
    for feature in features:
        if feature.from_dxf is None:
            expanded.append(feature)
            continue
        expanded.extend(_extract_feature(feature, doc_cache))
    return expanded


def _extract_feature(feature: Feature, doc_cache: dict[Path, Any]) -> list[Feature]:
    source = feature.from_dxf
    assert source is not None

    doc = _read_dxf(source.file, doc_cache)
    include = set(source.include)
    layers = set(source.layers) if source.layers is not None else None

    materialized: list[Feature] = []
    for entity in doc.modelspace():
        kind = entity.dxftype().lower()
        if kind not in include:
            continue
        if layers is not None and _entity_layer(entity) not in layers:
            continue

        extracted = None
        if kind == "line":
            extracted = _line(feature, entity)
        elif kind == "lwpolyline":
            extracted = _lwpolyline(feature, entity)
        elif kind == "arc":
            extracted = _arc(feature, entity)
        elif kind == "circle":
            extracted = _circle(feature, entity)

        if extracted is not None:
            materialized.append(extracted)

    return _apply_cleanup(materialized, source.cleanup)


def _read_dxf(path: Path, doc_cache: dict[Path, Any]) -> Any:
    absolute = path.resolve(strict=False)
    if absolute in doc_cache:
        return doc_cache[absolute]
    if not absolute.exists():
        raise ComponentSpecError(f"from_dxf file not found: {absolute}")
    try:
        doc = ezdxf.readfile(str(absolute))
    except Exception as exc:  # noqa: BLE001
        raise ComponentSpecError(f"from_dxf file unreadable: {absolute}: {exc}") from exc
    doc_cache[absolute] = doc
    return doc


def _line(source_feature: Feature, entity: Any) -> Feature | None:
    source = _source(source_feature)
    points_dxf = [_xy(entity.dxf.start), _xy(entity.dxf.end)]
    if not _all_inside(points_dxf, source.region):
        return None

    points = [_local(point, source) for point in points_dxf]
    if _polyline_length(points, closed=False) < source.min_length_m:
        return None
    return _shape_feature(
        source_feature,
        Shape("polyline", {"points": points, "closed": False}),
        _entity_layer(entity),
    )


def _lwpolyline(source_feature: Feature, entity: Any) -> Feature | None:
    source = _source(source_feature)
    points_dxf = [(float(x), float(y)) for x, y in entity.get_points("xy")]
    closed = bool(getattr(entity, "closed", False))
    min_count = 3 if closed else 2
    if len(points_dxf) < min_count or not _all_inside(points_dxf, source.region):
        return None

    points = [_local(point, source) for point in points_dxf]
    if _polyline_length(points, closed=closed) < source.min_length_m:
        return None
    return _shape_feature(
        source_feature,
        Shape("polyline", {"points": points, "closed": closed}),
        _entity_layer(entity),
    )


def _arc(source_feature: Feature, entity: Any) -> Feature | None:
    source = _source(source_feature)
    points_dxf = _arc_points(
        _xy(entity.dxf.center),
        float(entity.dxf.radius),
        float(entity.dxf.start_angle),
        float(entity.dxf.end_angle),
        source.arc_segments,
    )
    if not _all_inside(points_dxf, source.region):
        return None

    points = [_local(point, source) for point in points_dxf]
    if _polyline_length(points, closed=False) < source.min_length_m:
        return None
    return _shape_feature(
        source_feature,
        Shape("polyline", {"points": points, "closed": False}),
        _entity_layer(entity),
    )


def _circle(source_feature: Feature, entity: Any) -> Feature | None:
    source = _source(source_feature)
    center_dxf = _xy(entity.dxf.center)
    radius_dxf = float(entity.dxf.radius)
    if not _circle_inside(center_dxf, radius_dxf, source.region):
        return None

    scale = METRES_PER_UNIT[source.units]
    return _shape_feature(
        source_feature,
        Shape(
            "circle",
            {"center": _local(center_dxf, source), "diameter": radius_dxf * 2 * scale},
        ),
        _entity_layer(entity),
    )


def _shape_feature(source: Feature, shape: Shape, dxf_layer: str) -> Feature:
    return Feature(
        role=source.role,
        layer=source.layer,
        tag=source.tag,
        source_status=source.source_status,
        hatch=source.hatch,
        shape=shape,
        dxf_layer=dxf_layer,
    )


def _apply_cleanup(features: list[Feature], rules: tuple[CleanupRule, ...]) -> list[Feature]:
    if not rules:
        return features

    cleaned = features
    for rule in rules:
        if "drop" in rule:
            cleaned = [feature for feature in cleaned if not _drop_feature(feature, rule)]
        elif "reclassify" in rule:
            reclassify = rule["reclassify"]
            cleaned = [
                replace(feature, **reclassify["set"])
                if _matches_reclassify(feature, reclassify["match"])
                else feature
                for feature in cleaned
            ]
        else:
            raise ComponentSpecError(f"unsupported cleanup rule: {rule!r}")
    return cleaned


def _drop_feature(feature: Feature, rule: CleanupRule) -> bool:
    drop = rule["drop"]
    if drop == "diagonal":
        return _is_diagonal_line(feature, rule["angle_tol_deg"])
    if drop == "shorter_than":
        length = _feature_path_length(feature)
        return length is not None and length < rule["length_m"]
    if drop == "outside":
        return _has_vertex_outside(feature, _rule_box(rule))
    if drop == "layers":
        return feature.dxf_layer in rule["layers"]
    if drop == "region":
        return _all_vertices_inside(
            feature,
            _expanded_box(rule["region_local_m"], rule["margin_m"]),
        )
    raise ComponentSpecError(f"unsupported cleanup drop rule: {drop!r}")


def _is_diagonal_line(feature: Feature, angle_tol_deg: float) -> bool:
    if _feature_kind(feature) != "line":
        return False
    points = _feature_vertices(feature)
    if len(points) != 2:
        return False
    (x0, y0), (x1, y1) = points
    angle = abs(math.degrees(math.atan2(y1 - y0, x1 - x0))) % 180.0
    near_horizontal = min(angle, 180.0 - angle) <= angle_tol_deg
    near_vertical = abs(angle - 90.0) <= angle_tol_deg
    return not (near_horizontal or near_vertical)


def _feature_path_length(feature: Feature) -> float | None:
    shape = feature.shape
    if shape is None:
        return None
    if shape.kind == "line":
        return _polyline_length(shape.data["points"], closed=False)
    if shape.kind == "polyline":
        return _polyline_length(shape.data["points"], closed=bool(shape.data["closed"]))
    return None


def _has_vertex_outside(feature: Feature, box: tuple[float, float, float, float]) -> bool:
    vertices = _feature_vertices(feature)
    return bool(vertices) and any(not _point_inside(point, box) for point in vertices)


def _all_vertices_inside(feature: Feature, box: tuple[float, float, float, float]) -> bool:
    vertices = _feature_vertices(feature)
    return bool(vertices) and all(_point_inside(point, box) for point in vertices)


def _matches_reclassify(feature: Feature, match: dict[str, Any]) -> bool:
    if "kind" in match and _feature_kind(feature) != match["kind"]:
        return False
    if "dxf_layer" in match and feature.dxf_layer != match["dxf_layer"]:
        return False
    if "r_min_m" in match or "r_max_m" in match:
        radius = _feature_radius(feature)
        if radius is None:
            return False
        if "r_min_m" in match and radius < match["r_min_m"]:
            return False
        if "r_max_m" in match and radius > match["r_max_m"]:
            return False
    if "region_local_m" in match and not _all_vertices_inside(feature, match["region_local_m"]):
        return False
    return True


def _feature_kind(feature: Feature) -> str | None:
    shape = feature.shape
    if shape is None:
        return None
    if shape.kind == "circle":
        return "circle"
    if shape.kind == "line":
        return "line"
    if shape.kind == "polyline":
        points = shape.data["points"]
        closed = bool(shape.data["closed"])
        if closed:
            return "polygon"
        if len(points) == 2:
            return "line"
        return "polyline"
    if shape.kind == "rect":
        return "polygon"
    return shape.kind


def _feature_radius(feature: Feature) -> float | None:
    shape = feature.shape
    if shape is None or shape.kind != "circle":
        return None
    return float(shape.data["diameter"]) / 2.0


def _feature_vertices(feature: Feature) -> list[Point]:
    shape = feature.shape
    if shape is None:
        return []
    if shape.kind in {"line", "polyline"}:
        return list(shape.data["points"])
    if shape.kind == "circle":
        return [shape.data["center"]]
    if shape.kind == "point":
        return [shape.data["point"]]
    if shape.kind == "rect":
        cx, cy = shape.data["center"]
        w, h = shape.data["size"]
        hw, hh = w / 2.0, h / 2.0
        return [
            (cx - hw, cy - hh),
            (cx + hw, cy - hh),
            (cx + hw, cy + hh),
            (cx - hw, cy + hh),
        ]
    return []


def _rule_box(rule: CleanupRule) -> tuple[float, float, float, float]:
    margin = rule["margin_m"]
    if "footprint_m" in rule:
        width, height = rule["footprint_m"]
        return (
            -width / 2.0 - margin,
            -height / 2.0 - margin,
            width / 2.0 + margin,
            height / 2.0 + margin,
        )
    return _expanded_box(rule["region_local_m"], margin)


def _expanded_box(
    region: tuple[float, float, float, float], margin: float
) -> tuple[float, float, float, float]:
    x0, y0, x1, y1 = region
    return (x0 - margin, y0 - margin, x1 + margin, y1 + margin)


def _point_inside(point: Point, box: tuple[float, float, float, float]) -> bool:
    x, y = point
    x0, y0, x1, y1 = box
    return x0 <= x <= x1 and y0 <= y <= y1


def _source(feature: Feature) -> FromDxf:
    source = feature.from_dxf
    assert source is not None
    return source


def _entity_layer(entity: Any) -> str:
    return str(getattr(entity.dxf, "layer", "0"))


def _xy(point: Any) -> Point:
    return (float(point[0]), float(point[1]))


def _local(point: Point, source: FromDxf) -> Point:
    scale = METRES_PER_UNIT[source.units]
    ox, oy = source.origin
    x, y = point
    return ((x - ox) * scale, (y - oy) * scale)


def _all_inside(points: Iterable[Point], region: tuple[float, float, float, float]) -> bool:
    x0, y0, x1, y1 = region
    return all(x0 <= x <= x1 and y0 <= y <= y1 for x, y in points)


def _circle_inside(center: Point, radius: float, region: tuple[float, float, float, float]) -> bool:
    cx, cy = center
    x0, y0, x1, y1 = region
    return x0 <= cx - radius and cx + radius <= x1 and y0 <= cy - radius and cy + radius <= y1


def _polyline_length(points: list[Point], closed: bool) -> float:
    total = sum(_distance(start, end) for start, end in zip(points, points[1:]))
    if closed and len(points) > 1:
        total += _distance(points[-1], points[0])
    return total


def _distance(start: Point, end: Point) -> float:
    return math.hypot(end[0] - start[0], end[1] - start[1])


def _arc_points(
    center: Point, radius: float, start_deg: float, end_deg: float, segments: int
) -> list[Point]:
    sweep = end_deg - start_deg
    if sweep <= 0:
        sweep += 360.0
    cx, cy = center
    return [
        (
            cx + radius * math.cos(math.radians(start_deg + sweep * idx / segments)),
            cy + radius * math.sin(math.radians(start_deg + sweep * idx / segments)),
        )
        for idx in range(segments + 1)
    ]
