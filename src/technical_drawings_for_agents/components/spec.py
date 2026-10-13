"""YAML schema loader for to-scale component geometry."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

Point = tuple[float, float]
ShapeKind = Literal["rect", "circle", "line", "polyline", "point"]
DxfEntityKind = Literal["line", "lwpolyline", "arc", "circle"]
CleanupRule = dict[str, Any]

SOURCE_STATUSES = {"verified", "sourced", "as-received", "verify"}
SHAPE_KEYS = {"rect", "circle", "line", "polyline", "point"}
FROM_DXF_UNITS = {"mm", "cm", "m", "in", "ft"}
FROM_DXF_TYPES = {"line", "lwpolyline", "arc", "circle"}
FROM_DXF_KEYS = {
    "file",
    "region",
    "units",
    "origin",
    "include",
    "layers",
    "min_length_m",
    "arc_segments",
    "cleanup",
}
DEFAULT_FROM_DXF_INCLUDE: tuple[DxfEntityKind, ...] = ("line", "lwpolyline", "arc", "circle")
DROP_CLEANUP_RULES = {"diagonal", "shorter_than", "outside", "layers", "region"}
RECLASSIFY_MATCH_KEYS = {"kind", "dxf_layer", "r_min_m", "r_max_m", "region_local_m"}
RECLASSIFY_SET_KEYS = {"role", "layer", "tag", "source_status"}
RECLASSIFY_KINDS = {"polygon", "polyline", "line", "circle"}


class ComponentSpecError(ValueError):
    """Raised when a component YAML file does not match the schema."""


@dataclass(frozen=True)
class ComponentInfo:
    id: str
    name: str
    units: str


@dataclass(frozen=True)
class Shape:
    kind: ShapeKind
    data: dict[str, Any]


@dataclass(frozen=True)
class FromDxf:
    file: Path
    region: tuple[float, float, float, float]
    units: str
    origin: Point
    include: tuple[DxfEntityKind, ...] = DEFAULT_FROM_DXF_INCLUDE
    layers: tuple[str, ...] | None = None
    min_length_m: float = 0.0
    arc_segments: int = 24
    cleanup: tuple[CleanupRule, ...] = ()


@dataclass(frozen=True)
class Feature:
    role: str
    layer: str = "0"
    tag: str | None = None
    source_status: str = "verify"
    hatch: str | None = None
    shape: Shape | None = None
    text: str | None = None
    at: Point | None = None
    from_dxf: FromDxf | None = None
    dxf_layer: str | None = field(default=None, compare=False, repr=False)


@dataclass(frozen=True)
class Component:
    component: ComponentInfo
    views: dict[str, list[Feature]]
    _expanded_views: dict[str, list[Feature]] = field(
        default_factory=dict, init=False, repr=False, compare=False
    )

    @property
    def id(self) -> str:
        return self.component.id

    @property
    def name(self) -> str:
        return self.component.name

    @property
    def units(self) -> str:
        return self.component.units

    def view(self, name: str) -> list[Feature]:
        try:
            features = self.views[name]
        except KeyError as exc:
            available = ", ".join(sorted(self.views))
            raise ComponentSpecError(f"unknown view '{name}' (available: {available})") from exc
        if all(feature.from_dxf is None for feature in features):
            return features
        try:
            return self._expanded_views[name]
        except KeyError:
            from .extract import materialize_features

            expanded = materialize_features(features)
            self._expanded_views[name] = expanded
            return expanded


def load_component(path: str | Path) -> Component:
    """Load and validate a component YAML file."""

    path = Path(path)
    spec_dir = path.resolve().parent
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    root = _mapping(raw, str(path))

    comp_raw = _mapping(_required(root, "component", str(path)), "component")
    comp = ComponentInfo(
        id=_required_string(comp_raw, "id", "component"),
        name=_required_string(comp_raw, "name", "component"),
        units=_required_string(comp_raw, "units", "component"),
    )
    if comp.units != "m":
        raise ComponentSpecError(f"component.units must be 'm' (got {comp.units!r})")

    views_raw = _mapping(_required(root, "views", str(path)), "views")
    if not views_raw:
        raise ComponentSpecError("views must contain at least one view")

    views: dict[str, list[Feature]] = {}
    for view_name, view_raw in views_raw.items():
        if not isinstance(view_name, str) or not view_name:
            raise ComponentSpecError("view names must be non-empty strings")
        view_map = _mapping(view_raw, f"views.{view_name}")
        features_raw = _required(view_map, "features", f"views.{view_name}")
        if not isinstance(features_raw, list):
            raise ComponentSpecError(f"views.{view_name}.features must be a list")
        views[view_name] = [
            _feature(item, f"views.{view_name}.features[{idx}]", spec_dir)
            for idx, item in enumerate(features_raw)
        ]

    return Component(component=comp, views=views)


def _feature(raw: Any, ctx: str, spec_dir: Path) -> Feature:
    data = _mapping(raw, ctx)
    role = _required_string(data, "role", ctx)
    layer = _optional_string(data, "layer", ctx, default="0") or "0"
    tag = _optional_string(data, "tag", ctx)
    source_status = _optional_string(data, "source_status", ctx, default="verify") or "verify"
    if source_status not in SOURCE_STATUSES:
        allowed = ", ".join(sorted(SOURCE_STATUSES))
        raise ComponentSpecError(
            f"{ctx}.source_status must be one of {allowed} (got {source_status!r})"
        )
    hatch = _optional_string(data, "hatch", ctx)

    has_shape = "shape" in data
    has_from_dxf = "from_dxf" in data
    has_label = "text" in data or "at" in data
    feature_forms = [
        name
        for name, present in (
            ("shape", has_shape),
            ("label text/at", has_label),
            ("from_dxf", has_from_dxf),
        )
        if present
    ]
    if len(feature_forms) != 1:
        raise ComponentSpecError(
            f"{ctx} must contain exactly one of shape, label text/at, or from_dxf"
        )

    if has_label:
        if role != "label":
            raise ComponentSpecError(f"{ctx}: label text/at fields require role 'label'")
        text = _required_string(data, "text", ctx)
        at = _coord(_required(data, "at", ctx), f"{ctx}.at")
        return Feature(
            role=role,
            layer=layer,
            tag=tag,
            source_status=source_status,
            hatch=hatch,
            text=text,
            at=at,
        )

    if role == "label":
        raise ComponentSpecError(f"{ctx}: label features use text/at, not shape or from_dxf")

    if has_from_dxf:
        from_dxf = _from_dxf(_required(data, "from_dxf", ctx), f"{ctx}.from_dxf", spec_dir)
        return Feature(
            role=role,
            layer=layer,
            tag=tag,
            source_status=source_status,
            hatch=hatch,
            from_dxf=from_dxf,
        )

    shape = _shape(_required(data, "shape", ctx), f"{ctx}.shape")
    return Feature(
        role=role,
        layer=layer,
        tag=tag,
        source_status=source_status,
        hatch=hatch,
        shape=shape,
    )


def _from_dxf(raw: Any, ctx: str, spec_dir: Path) -> FromDxf:
    data = _mapping(raw, ctx)
    unknown = sorted(set(data) - FROM_DXF_KEYS)
    if unknown:
        raise ComponentSpecError(f"{ctx} has unknown key(s): {', '.join(unknown)}")

    file_value = _required_string(data, "file", ctx)
    file_path = Path(file_value)
    if not file_path.is_absolute():
        file_path = spec_dir / file_path
    file_path = file_path.resolve(strict=False)

    region = _region(_required(data, "region", ctx), f"{ctx}.region")

    units = _required_string(data, "units", ctx)
    if units not in FROM_DXF_UNITS:
        allowed = ", ".join(sorted(FROM_DXF_UNITS))
        raise ComponentSpecError(f"{ctx}.units must be one of {allowed} (got {units!r})")

    origin = _coord(_required(data, "origin", ctx), f"{ctx}.origin")

    include_raw = data.get("include", list(DEFAULT_FROM_DXF_INCLUDE))
    include_items = tuple(item.lower() for item in _string_list(include_raw, f"{ctx}.include"))
    invalid = sorted(set(include_items) - FROM_DXF_TYPES)
    if invalid:
        allowed = ", ".join(sorted(FROM_DXF_TYPES))
        raise ComponentSpecError(
            f"{ctx}.include must only contain {allowed} (got {', '.join(invalid)})"
        )

    layers_raw = data.get("layers")
    layers = None if layers_raw is None else tuple(_string_list(layers_raw, f"{ctx}.layers"))

    min_length_m = _number(data.get("min_length_m", 0.0), f"{ctx}.min_length_m")
    if min_length_m < 0:
        raise ComponentSpecError(f"{ctx}.min_length_m must be >= 0")

    arc_segments_raw = data.get("arc_segments", 24)
    if isinstance(arc_segments_raw, bool) or not isinstance(arc_segments_raw, int):
        raise ComponentSpecError(f"{ctx}.arc_segments must be an integer")
    if arc_segments_raw < 4:
        raise ComponentSpecError(f"{ctx}.arc_segments must be >= 4")

    cleanup = _cleanup_rules(data.get("cleanup", []), f"{ctx}.cleanup")

    return FromDxf(
        file=file_path,
        region=region,
        units=units,
        origin=origin,
        include=include_items,
        layers=layers,
        min_length_m=min_length_m,
        arc_segments=arc_segments_raw,
        cleanup=cleanup,
    )


def _cleanup_rules(raw: Any, ctx: str) -> tuple[CleanupRule, ...]:
    if not isinstance(raw, list):
        raise ComponentSpecError(f"{ctx} must be a list")
    return tuple(_cleanup_rule(item, f"{ctx}[{idx}]") for idx, item in enumerate(raw))


def _cleanup_rule(raw: Any, ctx: str) -> CleanupRule:
    data = _mapping(raw, ctx)
    has_drop = "drop" in data
    has_reclassify = "reclassify" in data
    if has_drop == has_reclassify:
        raise ComponentSpecError(f"{ctx} must contain exactly one of drop or reclassify")
    if has_drop:
        return _drop_cleanup_rule(data, ctx)
    return _reclassify_cleanup_rule(data, ctx)


def _drop_cleanup_rule(data: dict[str, Any], ctx: str) -> CleanupRule:
    drop = _required_string(data, "drop", ctx)
    if drop not in DROP_CLEANUP_RULES:
        allowed = ", ".join(sorted(DROP_CLEANUP_RULES))
        raise ComponentSpecError(f"{ctx}.drop must be one of {allowed} (got {drop!r})")

    if drop == "diagonal":
        _reject_unknown(data, {"drop", "angle_tol_deg"}, ctx)
        angle_tol_deg = _number(data.get("angle_tol_deg", 3.0), f"{ctx}.angle_tol_deg")
        if not 0 <= angle_tol_deg <= 45:
            raise ComponentSpecError(f"{ctx}.angle_tol_deg must be between 0 and 45")
        return {"drop": drop, "angle_tol_deg": angle_tol_deg}

    if drop == "shorter_than":
        _reject_unknown(data, {"drop", "length_m"}, ctx)
        length_m = _number(_required(data, "length_m", ctx), f"{ctx}.length_m")
        if length_m < 0:
            raise ComponentSpecError(f"{ctx}.length_m must be >= 0")
        return {"drop": drop, "length_m": length_m}

    if drop == "outside":
        _reject_unknown(data, {"drop", "footprint_m", "region_local_m", "margin_m"}, ctx)
        has_footprint = "footprint_m" in data
        has_region = "region_local_m" in data
        if has_footprint == has_region:
            raise ComponentSpecError(
                f"{ctx} with drop: outside must contain exactly one of footprint_m or "
                "region_local_m"
            )
        margin_m = _non_negative_number(data.get("margin_m", 0.1), f"{ctx}.margin_m")
        if has_footprint:
            footprint = _coord(_required(data, "footprint_m", ctx), f"{ctx}.footprint_m")
            if footprint[0] <= 0 or footprint[1] <= 0:
                raise ComponentSpecError(f"{ctx}.footprint_m values must be positive")
            return {"drop": drop, "footprint_m": footprint, "margin_m": margin_m}
        region = _region(_required(data, "region_local_m", ctx), f"{ctx}.region_local_m")
        return {"drop": drop, "region_local_m": region, "margin_m": margin_m}

    if drop == "layers":
        _reject_unknown(data, {"drop", "layers"}, ctx)
        layers = tuple(_string_list(_required(data, "layers", ctx), f"{ctx}.layers"))
        return {"drop": drop, "layers": layers}

    if drop == "region":
        _reject_unknown(data, {"drop", "region_local_m", "margin_m"}, ctx)
        region = _region(_required(data, "region_local_m", ctx), f"{ctx}.region_local_m")
        margin_m = _non_negative_number(data.get("margin_m", 0.0), f"{ctx}.margin_m")
        return {"drop": drop, "region_local_m": region, "margin_m": margin_m}

    raise ComponentSpecError(f"{ctx}.drop has unsupported value {drop!r}")


def _reclassify_cleanup_rule(data: dict[str, Any], ctx: str) -> CleanupRule:
    _reject_unknown(data, {"reclassify"}, ctx)
    reclassify = _mapping(_required(data, "reclassify", ctx), f"{ctx}.reclassify")
    _reject_unknown(reclassify, {"match", "set"}, f"{ctx}.reclassify")
    match = _cleanup_match(reclassify.get("match", {}), f"{ctx}.reclassify.match")
    updates = _cleanup_set(
        _required(reclassify, "set", f"{ctx}.reclassify"),
        f"{ctx}.reclassify.set",
    )
    return {"reclassify": {"match": match, "set": updates}}


def _cleanup_match(raw: Any, ctx: str) -> dict[str, Any]:
    data = _mapping(raw, ctx)
    _reject_unknown(data, RECLASSIFY_MATCH_KEYS, ctx)

    match: dict[str, Any] = {}
    if "kind" in data:
        kind = _required_string(data, "kind", ctx)
        if kind not in RECLASSIFY_KINDS:
            allowed = ", ".join(sorted(RECLASSIFY_KINDS))
            raise ComponentSpecError(f"{ctx}.kind must be one of {allowed} (got {kind!r})")
        match["kind"] = kind
    if "dxf_layer" in data:
        match["dxf_layer"] = _required_string(data, "dxf_layer", ctx)
    if "r_min_m" in data:
        match["r_min_m"] = _non_negative_number(data["r_min_m"], f"{ctx}.r_min_m")
    if "r_max_m" in data:
        match["r_max_m"] = _non_negative_number(data["r_max_m"], f"{ctx}.r_max_m")
    if match.get("r_min_m", 0.0) > match.get("r_max_m", float("inf")):
        raise ComponentSpecError(f"{ctx}.r_min_m must be <= r_max_m")
    if "region_local_m" in data:
        match["region_local_m"] = _region(data["region_local_m"], f"{ctx}.region_local_m")
    return match


def _cleanup_set(raw: Any, ctx: str) -> dict[str, Any]:
    data = _mapping(raw, ctx)
    if not data:
        raise ComponentSpecError(f"{ctx} must contain at least one field")
    _reject_unknown(data, RECLASSIFY_SET_KEYS, ctx)

    updates: dict[str, Any] = {}
    if "role" in data:
        updates["role"] = _required_string(data, "role", ctx)
    if "layer" in data:
        updates["layer"] = _required_string(data, "layer", ctx)
    if "tag" in data:
        tag = data["tag"]
        if tag is not None and not isinstance(tag, str):
            raise ComponentSpecError(f"{ctx}.tag must be a string or null")
        updates["tag"] = tag
    if "source_status" in data:
        source_status = _required_string(data, "source_status", ctx)
        if source_status not in SOURCE_STATUSES:
            allowed = ", ".join(sorted(SOURCE_STATUSES))
            raise ComponentSpecError(
                f"{ctx}.source_status must be one of {allowed} (got {source_status!r})"
            )
        updates["source_status"] = source_status
    return updates


def _shape(raw: Any, ctx: str) -> Shape:
    data = _mapping(raw, ctx)
    unknown = sorted(set(data) - SHAPE_KEYS)
    if unknown:
        raise ComponentSpecError(f"{ctx} has unknown shape key(s): {', '.join(unknown)}")

    present = [key for key in SHAPE_KEYS if key in data]
    if len(present) != 1:
        raise ComponentSpecError(f"{ctx} must contain exactly one primitive shape key")

    kind = present[0]
    value = data[kind]
    if kind == "rect":
        rect = _mapping(value, f"{ctx}.rect")
        size = _coord(_required(rect, "size", f"{ctx}.rect"), f"{ctx}.rect.size")
        if size[0] <= 0 or size[1] <= 0:
            raise ComponentSpecError(f"{ctx}.rect.size values must be positive")
        center = _coord(_required(rect, "center", f"{ctx}.rect"), f"{ctx}.rect.center")
        return Shape("rect", {"size": size, "center": center})

    if kind == "circle":
        circle = _mapping(value, f"{ctx}.circle")
        diameter = _number(
            _required(circle, "diameter", f"{ctx}.circle"), f"{ctx}.circle.diameter"
        )
        if diameter <= 0:
            raise ComponentSpecError(f"{ctx}.circle.diameter must be positive")
        center = _coord(_required(circle, "center", f"{ctx}.circle"), f"{ctx}.circle.center")
        return Shape("circle", {"diameter": diameter, "center": center})

    if kind == "line":
        points = _points(value, f"{ctx}.line", min_count=2)
        if len(points) != 2:
            raise ComponentSpecError(f"{ctx}.line must contain exactly two points")
        return Shape("line", {"points": points})

    if kind == "polyline":
        poly = _mapping(value, f"{ctx}.polyline")
        closed = poly.get("closed", False)
        if not isinstance(closed, bool):
            raise ComponentSpecError(f"{ctx}.polyline.closed must be true or false")
        points = _points(
            _required(poly, "points", f"{ctx}.polyline"),
            f"{ctx}.polyline.points",
            min_count=3 if closed else 2,
        )
        return Shape("polyline", {"points": points, "closed": closed})

    if kind == "point":
        return Shape("point", {"point": _coord(value, f"{ctx}.point")})

    raise ComponentSpecError(f"{ctx} has unsupported shape kind {kind!r}")


def _required(data: dict[str, Any], key: str, ctx: str) -> Any:
    if key not in data:
        raise ComponentSpecError(f"{ctx} missing required key '{key}'")
    return data[key]


def _required_string(data: dict[str, Any], key: str, ctx: str) -> str:
    value = _required(data, key, ctx)
    if not isinstance(value, str) or value == "":
        raise ComponentSpecError(f"{ctx}.{key} must be a non-empty string")
    return value


def _optional_string(
    data: dict[str, Any], key: str, ctx: str, default: str | None = None
) -> str | None:
    value = data.get(key, default)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ComponentSpecError(f"{ctx}.{key} must be a string or null")
    return value


def _mapping(value: Any, ctx: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ComponentSpecError(f"{ctx} must be a mapping")
    return value


def _coord(value: Any, ctx: str) -> Point:
    if not isinstance(value, (list, tuple)) or isinstance(value, (str, bytes)) or len(value) != 2:
        raise ComponentSpecError(f"{ctx} must be a two-number coordinate [x, y]")
    return (_number(value[0], f"{ctx}[0]"), _number(value[1], f"{ctx}[1]"))


def _region(value: Any, ctx: str) -> tuple[float, float, float, float]:
    if not isinstance(value, (list, tuple)) or isinstance(value, (str, bytes)) or len(value) != 4:
        raise ComponentSpecError(f"{ctx} must be a four-number region [x0, y0, x1, y1]")
    x0 = _number(value[0], f"{ctx}[0]")
    y0 = _number(value[1], f"{ctx}[1]")
    x1 = _number(value[2], f"{ctx}[2]")
    y1 = _number(value[3], f"{ctx}[3]")
    if x0 >= x1 or y0 >= y1:
        raise ComponentSpecError(f"{ctx} must satisfy x0 < x1 and y0 < y1")
    return (x0, y0, x1, y1)


def _points(value: Any, ctx: str, min_count: int) -> list[Point]:
    if not isinstance(value, list) or len(value) < min_count:
        raise ComponentSpecError(f"{ctx} must contain at least {min_count} coordinate points")
    return [_coord(point, f"{ctx}[{idx}]") for idx, point in enumerate(value)]


def _string_list(value: Any, ctx: str) -> list[str]:
    if not isinstance(value, list):
        raise ComponentSpecError(f"{ctx} must be a list")
    items: list[str] = []
    for idx, item in enumerate(value):
        if not isinstance(item, str) or item == "":
            raise ComponentSpecError(f"{ctx}[{idx}] must be a non-empty string")
        items.append(item)
    return items


def _reject_unknown(data: dict[str, Any], allowed: set[str], ctx: str) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ComponentSpecError(f"{ctx} has unknown key(s): {', '.join(unknown)}")


def _non_negative_number(value: Any, ctx: str) -> float:
    number = _number(value, ctx)
    if number < 0:
        raise ComponentSpecError(f"{ctx} must be >= 0")
    return number


def _number(value: Any, ctx: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ComponentSpecError(f"{ctx} must be a number")
    return float(value)
