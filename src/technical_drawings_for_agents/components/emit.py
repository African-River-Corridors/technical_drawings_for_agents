"""Emit placed component geometry to GeoJSON, SVG, and DXF."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any, Iterable

from ..layers import DEFAULT_PX_PER_MM, LayerTable, LayerTableError
from ..svg import ViewBox, svg_circle, svg_line, svg_polygon, svg_text
from .place import PlacedFeature
from .spec import Point

if TYPE_CHECKING:
    from ..dxf import DxfBuilder


def to_geojson(
    placed: Iterable[PlacedFeature],
    crs: str = "EPSG:32630",
    instance: dict[str, Any] | None = None,
    policy=None,
) -> dict[str, Any]:
    """Return a GeoJSON FeatureCollection.

    Coordinates are stored as ``[x, y]`` easting/northing pairs in the declared
    projected CRS. This intentionally follows the file CRS axis order rather
    than lon/lat web-map convention.
    """

    features = []
    for feature in placed:
        features.append(
            {
                "type": "Feature",
                "properties": _properties(feature, instance),
                "geometry": _geometry(feature, policy),
            }
        )
    return {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": crs}},
        "features": features,
    }


def to_svg(
    placed: Iterable[PlacedFeature],
    viewbox: ViewBox,
    stroke: str = "#111111",
    fill: str = "none",
    stroke_width: float = 1.5,
    point_radius: float = 0.08,
    label_size: float = 12,
    policy=None,
    *,
    pens: LayerTable | None = None,
    px_per_mm: float = DEFAULT_PX_PER_MM,
) -> list[str]:
    """Return SVG element strings for already placed features.

    With ``pens`` omitted, the historical single-pen SVG output is unchanged.
    With ``pens`` supplied, layer-table pens provide ink and width; the existing
    ``source_status == "verify"`` dash remains a provenance signal and wins over
    a layer-table dash.
    """

    if pens is not None and (stroke != "#111111" or stroke_width != 1.5):
        raise LayerTableError("pens= and stroke=/stroke_width= are mutually exclusive")

    elements: list[str] = []
    for feature in placed:
        feature_stroke = stroke
        feature_width = stroke_width
        dash = "5,4" if feature.source_status == "verify" else None
        if pens is not None:
            pen = pens.pen(feature.layer)
            feature_stroke = pen.ink
            feature_width = round(pen.width_px(px_per_mm), 2)
            dash = dash or pen.svg_dash(px_per_mm)
        if feature.kind == "polygon":
            points = [_svg_point(viewbox, point) for point in _point_list(feature.coords)]
            elem = svg_polygon(
                points,
                fill=fill,
                stroke=feature_stroke,
                stroke_width=feature_width,
                policy=policy,
            )
            elements.append(_with_dash(elem, dash))
        elif feature.kind == "line":
            p1, p2 = _point_list(feature.coords)
            x1, y1 = _svg_point(viewbox, p1)
            x2, y2 = _svg_point(viewbox, p2)
            elements.append(
                svg_line(
                    x1,
                    y1,
                    x2,
                    y2,
                    stroke=feature_stroke,
                    stroke_width=feature_width,
                    dash=dash,
                    policy=policy,
                )
            )
        elif feature.kind == "polyline":
            points = _point_list(feature.coords)
            for p1, p2 in zip(points, points[1:]):
                x1, y1 = _svg_point(viewbox, p1)
                x2, y2 = _svg_point(viewbox, p2)
                elements.append(
                    svg_line(
                        x1,
                        y1,
                        x2,
                        y2,
                        stroke=feature_stroke,
                        stroke_width=feature_width,
                        dash=dash,
                        policy=policy,
                    )
                )
        elif feature.kind == "circle":
            cx, cy = _svg_point(viewbox, _single_point(feature.coords))
            assert feature.radius is not None
            elem = svg_circle(
                cx,
                cy,
                viewbox.length(feature.radius),
                fill=fill,
                stroke=feature_stroke,
                stroke_width=feature_width,
                policy=policy,
            )
            elements.append(_with_dash(elem, dash))
        elif feature.kind == "point":
            cx, cy = _svg_point(viewbox, _single_point(feature.coords))
            r = max(2.0, viewbox.length(point_radius))
            elem = svg_circle(
                cx,
                cy,
                r,
                fill=feature_stroke,
                stroke=feature_stroke,
                stroke_width=feature_width,
                policy=policy,
            )
            elements.append(_with_dash(elem, dash))
        elif feature.kind == "label":
            x, y = _svg_point(viewbox, _single_point(feature.coords))
            elements.append(
                svg_text(
                    x,
                    y,
                    feature.text or "",
                    font_size=label_size,
                    fill=feature_stroke,
                    policy=policy,
                )
            )
        else:
            raise ValueError(f"unsupported placed feature kind {feature.kind!r}")

        # ``hatch`` is metadata for the calling sheet; this low-level emitter
        # deliberately does not apply fill patterns.

    return elements


def to_dxf(
    placed: Iterable[PlacedFeature],
    dxf: DxfBuilder,
    point_size: float = 0.12,
    text_height: float = 0.25,
) -> None:
    """Write placed geometry to a passed :class:`~technical_drawings_for_agents.dxf.DxfBuilder`."""

    for feature in placed:
        layer = feature.layer or "0"
        if feature.kind == "polygon":
            dxf.polyline(_point_list(feature.coords), closed=True, layer=layer)
        elif feature.kind == "line":
            p1, p2 = _point_list(feature.coords)
            dxf.line(p1, p2, layer=layer)
        elif feature.kind == "polyline":
            dxf.polyline(_point_list(feature.coords), closed=False, layer=layer)
        elif feature.kind == "circle":
            assert feature.radius is not None
            dxf.circle(_single_point(feature.coords), feature.radius, layer=layer)
        elif feature.kind == "point":
            x, y = _single_point(feature.coords)
            half = point_size / 2.0
            dxf.line((x - half, y), (x + half, y), layer=layer)
            dxf.line((x, y - half), (x, y + half), layer=layer)
        elif feature.kind == "label":
            dxf.text(
                _single_point(feature.coords),
                feature.text or "",
                height=text_height,
                layer=layer,
                rotation=0.0,
            )
        else:
            raise ValueError(f"unsupported placed feature kind {feature.kind!r}")


def _properties(feature: PlacedFeature, instance: dict[str, Any] | None) -> dict[str, Any]:
    props = {
        "role": feature.role,
        "layer": feature.layer,
        "tag": feature.tag,
        "source_status": feature.source_status,
        "hatch": feature.hatch,
        "component": feature.component,
    }
    if instance:
        props.update(instance)
    return props


def _geometry(feature: PlacedFeature, policy=None) -> dict[str, Any]:
    if feature.kind == "polygon":
        return {
            "type": "Polygon",
            "coordinates": [_coords(_closed(_point_list(feature.coords)), policy)],
        }
    if feature.kind in {"line", "polyline"}:
        return {"type": "LineString", "coordinates": _coords(_point_list(feature.coords), policy)}
    if feature.kind in {"point", "label"}:
        return {"type": "Point", "coordinates": _coord(_single_point(feature.coords), policy)}
    if feature.kind == "circle":
        assert feature.radius is not None
        ring = _circle_ring(_single_point(feature.coords), feature.radius)
        return {"type": "Polygon", "coordinates": [_coords(ring, policy)]}
    raise ValueError(f"unsupported placed feature kind {feature.kind!r}")


def _circle_ring(center: Point, radius: float, segments: int = 48) -> list[Point]:
    cx, cy = center
    points = [
        (
            cx + radius * math.cos(math.tau * i / segments),
            cy + radius * math.sin(math.tau * i / segments),
        )
        for i in range(segments)
    ]
    points.append(points[0])
    return points


def _closed(points: list[Point]) -> list[Point]:
    if points and points[0] != points[-1]:
        return [*points, points[0]]
    return points


def _coord(point: Point, policy=None) -> list[float]:
    if policy is None:
        return [point[0], point[1]]
    return [
        policy.q(point[0], policy.precision_m),
        policy.q(point[1], policy.precision_m),
    ]


def _coords(points: list[Point], policy=None) -> list[list[float]]:
    return [_coord(point, policy) for point in points]


def _svg_point(viewbox: ViewBox, point: Point) -> tuple[float, float]:
    return viewbox.point(point[0], point[1])


def _single_point(coords: Point | list[Point]) -> Point:
    if isinstance(coords, tuple):
        return coords
    raise TypeError("feature coords are not a single point")


def _point_list(coords: Point | list[Point]) -> list[Point]:
    if isinstance(coords, list):
        return coords
    raise TypeError("feature coords are not a point list")


def _with_dash(element: str, dash: str | None) -> str:
    if not dash:
        return element
    return element.replace("/>", f' stroke-dasharray="{dash}"/>')
