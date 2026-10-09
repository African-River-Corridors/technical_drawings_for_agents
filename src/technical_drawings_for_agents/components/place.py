"""Placement transforms for component geometry."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from .spec import Component, Feature, Point

PlacedKind = Literal["polygon", "polyline", "line", "circle", "point", "label"]


@dataclass(frozen=True)
class PlacedFeature:
    component: str
    role: str
    layer: str
    tag: str | None
    source_status: str
    hatch: str | None
    kind: PlacedKind
    coords: Point | list[Point]
    radius: float | None = None
    text: str | None = None


def place(
    component: Component,
    view: str,
    origin: Point,
    rotation_deg: float = 0.0,
) -> list[PlacedFeature]:
    """Place a component view in world metres.

    Local coordinates use +x east and +y north. The transform is rotation about
    the local origin followed by translation to ``origin``.
    """

    angle = math.radians(rotation_deg)
    ox, oy = float(origin[0]), float(origin[1])

    def xf(point: Point) -> Point:
        x, y = point
        rx = x * math.cos(angle) - y * math.sin(angle)
        ry = x * math.sin(angle) + y * math.cos(angle)
        return (ox + rx, oy + ry)

    placed: list[PlacedFeature] = []
    for feature in component.view(view):
        placed.append(_place_feature(component.id, feature, xf))
    return placed


def _place_feature(component_id: str, feature: Feature, xf) -> PlacedFeature:
    base = dict(
        component=component_id,
        role=feature.role,
        layer=feature.layer or "0",
        tag=feature.tag,
        source_status=feature.source_status,
        hatch=feature.hatch,
    )

    if feature.role == "label":
        assert feature.at is not None
        return PlacedFeature(**base, kind="label", coords=xf(feature.at), text=feature.text)

    assert feature.shape is not None
    shape = feature.shape
    if shape.kind == "rect":
        cx, cy = shape.data["center"]
        w, h = shape.data["size"]
        hw, hh = w / 2.0, h / 2.0
        points = [
            (cx - hw, cy - hh),
            (cx + hw, cy - hh),
            (cx + hw, cy + hh),
            (cx - hw, cy + hh),
        ]
        return PlacedFeature(**base, kind="polygon", coords=[xf(point) for point in points])

    if shape.kind == "circle":
        return PlacedFeature(
            **base,
            kind="circle",
            coords=xf(shape.data["center"]),
            radius=shape.data["diameter"] / 2.0,
        )

    if shape.kind == "line":
        return PlacedFeature(
            **base,
            kind="line",
            coords=[xf(point) for point in shape.data["points"]],
        )

    if shape.kind == "polyline":
        kind = "polygon" if shape.data["closed"] else "polyline"
        return PlacedFeature(
            **base,
            kind=kind,
            coords=[xf(point) for point in shape.data["points"]],
        )

    if shape.kind == "point":
        return PlacedFeature(**base, kind="point", coords=xf(shape.data["point"]))

    raise ValueError(f"unsupported shape kind {shape.kind!r}")
