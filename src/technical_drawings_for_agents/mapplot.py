"""A map sheet from GeoJSON, with no QGIS and no GDAL — plotted through :mod:`plot`.

The GIS review path used to be "open QGIS". This turns a small ``plot.yaml`` into
an SVG sheet built from the toolkit's existing furniture (:mod:`technical_drawings_for_agents.svg`)
and hands it to :func:`technical_drawings_for_agents.plot.plot`, so a map ends its run at the same
review-grade PDF every other generator does.

Deliberately narrow, and the narrowness is the design:

* GeoJSON is read with the standard-library ``json`` module. **No GDAL, no OGR,
  no PyQGIS, no ``ogr2ogr``, no ``pyproj``.**
* This module **never reprojects and never clips**. Every layer must already be
  in ``map.crs`` and inside ``map.extent``; a backdrop must already be clipped to
  it. Preparing that is P8's ``geo`` verb (#60). A tool that quietly reprojects is
  a tool that quietly produces a plausible-looking wrong drawing.
* A backdrop is referenced by **relative path**, never base64-embedded (P8 owns
  that decision, and this must not regress it).
* An unsupported geometry type is an error naming the feature index — a silently
  skipped feature is a defect, not a default.

Fidelity for a map is the SVG-level invariant that **every input feature produced
at least one element in the sheet body**, counted during emission and reported in
the same :class:`~technical_drawings_for_agents.plot.FidelityReport` shape the DXF path uses.

Like :mod:`technical_drawings_for_agents.plot`, this module never reads ``for_construction`` and
never writes ``meta.yaml``. Its ``meta:`` block is *input*, read once and drawn.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from . import svg as svg_furniture
from .backdrop import (
    LIBRSVG_HREF_NOTE,
    BackdropError,
    backdrop_image_element,
    load_backdrop,
)
from .plot import (
    FidelityFinding,
    FidelityReport,
    PlotError,
    PlotRequest,
    PlotResult,
)
from .provenance import EmitPolicy, write_text_canonical
from .style import COL_CAD_BG, COL_CAD_OBJECT
from .svg import ViewBox

# svg.py owns the single XML-escaping/identifier implementation in this package;
# re-implementing it here would be a second escaper to keep in step. Same
# CLI-plumbing precedent as ``cli.py``'s import of ``legibility._merge_declarations``.
_escape = svg_furniture._svg_escape
_ident = svg_furniture._svg_id

log = logging.getLogger("technical_drawings_for_agents.mapplot")

SUPPORTED_GEOMETRY = ("Point", "LineString", "Polygon", "MultiLineString", "MultiPolygon")

#: Relative tolerance, against the extent's own span, for the backdrop-alignment
#: check. A misaligned backdrop is exactly the plausible-looking wrong artifact
#: the plot path exists to refuse.
BACKDROP_TOLERANCE = 1e-6

DEFAULT_SHEET_PX = (1400.0, 990.0)  # ~A3 landscape aspect
DEFAULT_PADDING_PX = 70.0

_EPSG_RE = re.compile(r"(?:^|:)EPSG:*(?P<code>\d+)$", re.IGNORECASE)


@dataclass(frozen=True)
class MapLayer:
    """One vector layer: a GeoJSON file, a style, and an optional label property."""

    name: str
    geojson: Path
    style: dict[str, Any] = field(default_factory=dict)
    label: str | None = None

    @property
    def stroke(self) -> str:
        return str(self.style.get("stroke", COL_CAD_OBJECT))

    @property
    def fill(self) -> str:
        return str(self.style.get("fill", "none"))

    @property
    def stroke_width(self) -> float:
        return float(self.style.get("stroke_width", 1.2))

    @property
    def point_radius(self) -> float:
        return float(self.style.get("point_radius", 3.0))


@dataclass(frozen=True)
class Backdrop:
    """An already-clipped, already-projected raster backdrop. Never prepared here.

    Two forms, and the ``manifest`` one is preferred:

    * ``manifest:`` — a ``tdfa.geo/1`` geo manifest written by a GIS tool's ``geo`` step
      (the legacy ``sankofa.geo/1`` id is still read, with a DeprecationWarning).
      Delegated wholly to P8's :mod:`technical_drawings_for_agents.backdrop`, which re-hashes the
      sidecar, matches its frame against the sheet's, honours the declared opacity
      and refuses a parent-escaping href. This is the one to use.
    * ``image:`` + ``extent:`` — the plain form. Kept because it is the shape the P4
      spec defines, and subjected to the *same* href policy: P8 measured that
      librsvg silently drops a ``../`` link (exit 0, blank backdrop), which is
      exactly the plausible-looking-wrong artifact this path refuses.
    """

    image: Path
    extent: tuple[float, float, float, float]
    manifest: Path | None = None
    loaded: Any | None = None  # technical_drawings_for_agents.backdrop.Backdrop when manifest-backed


@dataclass(frozen=True)
class MapConfig:
    """A validated ``plot.yaml``. Constructing one is the whole validation pass."""

    source: Path
    crs: str
    extent: tuple[float, float, float, float]
    layers: tuple[MapLayer, ...]
    meta: dict[str, Any]
    backdrop: Backdrop | None = None
    width_px: float = DEFAULT_SHEET_PX[0]
    height_px: float = DEFAULT_SHEET_PX[1]
    background: str = COL_CAD_BG

    @property
    def number(self) -> str:
        return str(self.meta.get("number", self.source.parent.name))

    @property
    def span(self) -> tuple[float, float]:
        xmin, ymin, xmax, ymax = self.extent
        return (xmax - xmin, ymax - ymin)


@dataclass(frozen=True)
class MapPlot:
    """What a map run produced: the sheet, the plot result, and the coverage report."""

    config: MapConfig
    svg: Path
    result: PlotResult
    fidelity: FidelityReport


# --------------------------------------------------------------------------- #
# config
# --------------------------------------------------------------------------- #


def load_map_config(path: str | Path) -> MapConfig:
    """Read and validate a ``plot.yaml``. Every message names the offending key."""
    config_path = Path(path)
    if not config_path.is_file():
        raise PlotError(f"map config not found: {config_path}", kind="input")
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise PlotError(f"cannot read {config_path}: {exc}", kind="input") from exc
    if not isinstance(raw, dict):
        raise PlotError(f"{config_path}: top level must be a mapping", kind="input")

    block = raw.get("map")
    if not isinstance(block, dict):
        raise PlotError(f"{config_path}: 'map' must be a mapping", kind="input")

    crs = block.get("crs")
    if not isinstance(crs, str) or not crs.strip():
        raise PlotError(
            f"{config_path}: map.crs is required and must be a string like 'EPSG:32630' "
            "(recorded and asserted — mapplot never reprojects)",
            kind="input",
        )
    extent = _extent(block.get("extent"), f"{config_path}: map.extent")

    layers_raw = block.get("layers")
    if not isinstance(layers_raw, list) or not layers_raw:
        raise PlotError(
            f"{config_path}: map.layers must be a non-empty list of layer mappings",
            kind="input",
        )
    layers: list[MapLayer] = []
    for index, entry in enumerate(layers_raw):
        ctx = f"{config_path}: map.layers[{index}]"
        if not isinstance(entry, dict):
            raise PlotError(f"{ctx} must be a mapping", kind="input")
        name = entry.get("name")
        if not isinstance(name, str) or not name.strip():
            raise PlotError(f"{ctx}.name is required and must be a non-empty string", kind="input")
        geojson = entry.get("geojson")
        if not isinstance(geojson, str) or not geojson.strip():
            raise PlotError(f"{ctx}.geojson is required (a path to a GeoJSON file)", kind="input")
        geojson_path = (config_path.parent / geojson).resolve()
        if not geojson_path.is_file():
            raise PlotError(f"{ctx}.geojson not found: {geojson_path}", kind="input")
        style = entry.get("style", {})
        if not isinstance(style, dict):
            raise PlotError(f"{ctx}.style must be a mapping", kind="input")
        label = entry.get("label")
        if label is not None and not isinstance(label, str):
            raise PlotError(f"{ctx}.label must be a property name (string)", kind="input")
        layers.append(
            MapLayer(name=name.strip(), geojson=geojson_path, style=dict(style), label=label)
        )

    backdrop = _backdrop(block.get("backdrop"), config_path, extent)

    meta = raw.get("meta", {})
    if not isinstance(meta, dict):
        raise PlotError(f"{config_path}: 'meta' must be a mapping", kind="input")

    sheet = raw.get("sheet", {})
    if not isinstance(sheet, dict):
        raise PlotError(f"{config_path}: 'sheet' must be a mapping", kind="input")
    width = _positive(sheet.get("width_px", DEFAULT_SHEET_PX[0]), f"{config_path}: sheet.width_px")
    height = _positive(
        sheet.get("height_px", DEFAULT_SHEET_PX[1]), f"{config_path}: sheet.height_px"
    )
    background = sheet.get("background", COL_CAD_BG)
    if not isinstance(background, str) or not background.strip():
        raise PlotError(f"{config_path}: sheet.background must be a colour string", kind="input")

    return MapConfig(
        source=config_path,
        crs=crs.strip(),
        extent=extent,
        layers=tuple(layers),
        meta=dict(meta),
        backdrop=backdrop,
        width_px=width,
        height_px=height,
        background=background.strip(),
    )


def _extent(value: Any, ctx: str) -> tuple[float, float, float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise PlotError(f"{ctx} must be [xmin, ymin, xmax, ymax]", kind="input")
    try:
        xmin, ymin, xmax, ymax = (float(item) for item in value)
    except (TypeError, ValueError) as exc:
        raise PlotError(f"{ctx} must hold four numbers, got {value!r}", kind="input") from exc
    if xmax <= xmin or ymax <= ymin:
        raise PlotError(
            f"{ctx} must satisfy xmin < xmax and ymin < ymax, got {list(value)}", kind="input"
        )
    return (xmin, ymin, xmax, ymax)


def _positive(value: Any, ctx: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise PlotError(f"{ctx} must be a number, got {value!r}", kind="input") from exc
    if number <= 0:
        raise PlotError(f"{ctx} must be positive, got {number:g}", kind="input")
    return number


def _backdrop(
    value: Any, config_path: Path, extent: tuple[float, float, float, float]
) -> Backdrop | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise PlotError(f"{config_path}: map.backdrop must be a mapping", kind="input")
    if "manifest" in value:
        return _manifest_backdrop(value, config_path)
    image = value.get("image")
    if not isinstance(image, str) or not image.strip():
        raise PlotError(f"{config_path}: map.backdrop.image is required", kind="input")
    image_path = (config_path.parent / image).resolve()
    if not image_path.is_file():
        raise PlotError(
            f"{config_path}: map.backdrop.image not found: {image_path}. Preparing a "
            "clipped, projected backdrop is the 'geo' verb's job (P8, #60).",
            kind="input",
        )
    backdrop_extent = _extent(value.get("extent"), f"{config_path}: map.backdrop.extent")
    span_x, span_y = extent[2] - extent[0], extent[3] - extent[1]
    tolerance = (
        BACKDROP_TOLERANCE * span_x,
        BACKDROP_TOLERANCE * span_y,
        BACKDROP_TOLERANCE * span_x,
        BACKDROP_TOLERANCE * span_y,
    )
    offsets = [abs(a - b) for a, b in zip(backdrop_extent, extent)]
    if any(offset > tol for offset, tol in zip(offsets, tolerance)):
        raise PlotError(
            f"{config_path}: map.backdrop.extent {list(backdrop_extent)} does not match "
            f"map.extent {list(extent)} (offsets {[round(o, 6) for o in offsets]} exceed "
            f"{BACKDROP_TOLERANCE:g} of the extent span). A misaligned backdrop draws a "
            "plausible-looking wrong sheet, so it is refused rather than stretched. "
            "Re-clip it with the 'geo' verb (P8, #60) — mapplot never clips or reprojects.",
            kind="input",
        )
    return Backdrop(image=image_path, extent=backdrop_extent)


def _manifest_backdrop(value: dict[str, Any], config_path: Path) -> Backdrop:
    """Delegate wholly to P8's :mod:`technical_drawings_for_agents.backdrop`. Nothing re-implemented."""
    manifest = value.get("manifest")
    if not isinstance(manifest, str) or not manifest.strip():
        raise PlotError(
            f"{config_path}: map.backdrop.manifest must be a path to a "
            "'tdfa.geo/1' geo manifest",
            kind="input",
        )
    if "image" in value or "extent" in value:
        raise PlotError(
            f"{config_path}: map.backdrop takes either 'manifest' or "
            "'image' + 'extent', never both — two sources of truth for one raster",
            kind="input",
        )
    manifest_path = (config_path.parent / manifest).resolve()
    try:
        loaded = load_backdrop(manifest_path)
    except BackdropError as exc:
        raise PlotError(f"{config_path}: map.backdrop.manifest: {exc}", kind="input") from exc
    return Backdrop(
        image=loaded.image_path,
        extent=loaded.bbox,
        manifest=manifest_path,
        loaded=loaded,
    )


# --------------------------------------------------------------------------- #
# emission
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _Feature:
    index: int
    layer: str
    geometry_type: str
    elements: tuple[str, ...]


def build_map_svg(config: MapConfig, out_dir: Path) -> tuple[str, tuple[_Feature, ...]]:
    """Emit the sheet markup and the per-feature element counts. No file I/O."""
    xmin, ymin, xmax, ymax = config.extent
    view = ViewBox(
        real_min_x=xmin,
        real_max_x=xmax,
        real_min_y=ymin,
        real_max_y=ymax,
        svg_width=config.width_px,
        svg_height=config.height_px,
        padding=DEFAULT_PADDING_PX,
    )
    parts: list[str] = []
    if config.backdrop is not None:
        parts.append(_backdrop_element(config.backdrop, view, out_dir))

    features: list[_Feature] = []
    running = 0
    for layer in config.layers:
        parts.append(f'<g class="map-layer" data-layer="{_ident(layer.name)}">')
        for feature in _features(layer):
            elements = _emit_feature(feature, layer, view, running)
            features.append(
                _Feature(
                    index=running,
                    layer=layer.name,
                    geometry_type=str((feature.get("geometry") or {}).get("type", "")),
                    elements=elements,
                )
            )
            parts.extend(elements)
            running += 1
        parts.append("</g>")

    parts.append(svg_furniture.svg_border(config.width_px, config.height_px))
    parts.append(
        svg_furniture.svg_scale_bar(view, xmin + config.span[0] * 0.06, ymin + config.span[1] * 0.06,
                                   _scale_bar_length(config.span[0]))
    )
    # svg_north_arrow emits bare geometry; the class="north-arrow" marker is what
    # validate.py's V11 looks for, so the group is added here rather than changing
    # svg.py (which P1/P5/P7 goldens depend on byte-for-byte).
    parts.append(
        '<g class="north-arrow">'
        + svg_furniture.svg_north_arrow(
            view, xmax - config.span[0] * 0.06, ymax - config.span[1] * 0.08
        )
        + "</g>"
    )
    parts.append(
        svg_furniture.svg_title_block(
            config.width_px,
            config.height_px,
            project=str(config.meta.get("project", "")),
            title=str(config.meta.get("title", "")),
            drawing_no=str(config.meta.get("number", "")),
            scale=str(config.meta.get("scale", "")),
            date=str(config.meta.get("date", "")),
            revision=str(config.meta.get("revision", "")),
        )
    )
    status = str(config.meta.get("status", "DRAFT"))
    parts.append(svg_furniture.svg_status_watermark(config.width_px, config.height_px, status))
    parts.append(
        f'<g class="map-crs"><title>{_escape(config.crs)}</title></g>'
    )
    body = "\n".join(parts)
    return (
        svg_furniture.svg_wrap(body, config.width_px, config.height_px, background=config.background),
        tuple(features),
    )


def _scale_bar_length(span_x: float) -> float:
    """The largest 1/2/5 x 10^n at most a fifth of the extent. Derived, never typed.

    Rounding *down* rather than to nearest: a bar longer than the space it was
    sized for is a bar that runs off the sheet.
    """
    target = span_x / 5.0
    if target <= 0:
        return 1.0
    magnitude = 10.0 ** math.floor(math.log10(target))
    for step in (5.0, 2.0, 1.0):
        if step * magnitude <= target:
            return step * magnitude
    return magnitude


def _backdrop_element(backdrop: Backdrop, view: ViewBox, out_dir: Path) -> str:
    """A relative href, never a base64 blob (P8 owns that decision).

    A manifest-backed backdrop is emitted by P8's own checked element builder —
    digest, frame match, coverage and href policy all come from there. The plain
    ``image:`` form gets the href policy applied here, because P8 measured that
    librsvg silently drops a parent-escaping link: exit 0, empty stderr, and a
    blank backdrop that looks rendered at a glance.
    """
    if backdrop.loaded is not None:
        try:
            return backdrop_image_element(backdrop.loaded, view, out_dir)
        except BackdropError as exc:
            raise PlotError(str(exc), kind="input") from exc

    xmin, ymin, xmax, ymax = backdrop.extent
    x, y = view.point(xmin, ymax)
    width = view.length(xmax - xmin)
    height = view.length(ymax - ymin)
    href = os.path.relpath(backdrop.image, out_dir).replace(os.sep, "/")
    if href.split("/")[0] == "..":
        raise PlotError(
            f"map.backdrop.image href {href!r} escapes the sheet directory {out_dir}: "
            f"{LIBRSVG_HREF_NOTE}. Put the sidecar under {out_dir}, emit the sheet from "
            f"a directory at or above {Path(backdrop.image).parent}, or use the "
            "'manifest:' form so technical_drawings_for_agents.backdrop performs the full check.",
            kind="input",
        )
    # The xlink namespace is declared on the element itself: svg_wrap's root has no
    # xmlns:xlink (and changing it would move bytes in every existing golden sheet),
    # and an undeclared prefix is a hard XML parse error in librsvg. Both the SVG 1.1
    # xlink:href and the SVG 2 href are written so old and new renderers agree.
    return (
        f'<image class="map-backdrop" xmlns:xlink="http://www.w3.org/1999/xlink" '
        f'x="{x:.1f}" y="{y:.1f}" '
        f'width="{width:.1f}" height="{height:.1f}" '
        f'xlink:href="{_escape(href)}" '
        f'href="{_escape(href)}" '
        'preserveAspectRatio="none"/>'
    )


def _features(layer: MapLayer) -> list[dict[str, Any]]:
    try:
        data = json.loads(layer.geojson.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PlotError(f"cannot read GeoJSON {layer.geojson}: {exc}", kind="input") from exc
    if not isinstance(data, dict):
        raise PlotError(f"{layer.geojson}: not a GeoJSON object", kind="input")
    if data.get("type") != "FeatureCollection":
        raise PlotError(
            f"{layer.geojson}: expected a FeatureCollection, got {data.get('type')!r}",
            kind="unsupported",
        )
    features = data.get("features")
    if not isinstance(features, list) or not features:
        raise PlotError(
            f"{layer.geojson}: FeatureCollection has no features — an empty layer would "
            "draw nothing and pass unnoticed",
            kind="input",
        )
    return features


def assert_crs(config: MapConfig) -> None:
    """Every layer's declared CRS must equal ``map.crs``. Nothing is transformed."""
    wanted = _normalise_crs(config.crs)
    for layer in config.layers:
        try:
            data = json.loads(layer.geojson.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PlotError(f"cannot read GeoJSON {layer.geojson}: {exc}", kind="input") from exc
        declared = _declared_crs(data)
        if declared is not None and _normalise_crs(declared) != wanted:
            raise PlotError(
                f"layer {layer.name!r} ({layer.geojson}) declares CRS {declared!r} but "
                f"map.crs is {config.crs!r}. mapplot never reprojects — run the 'geo' verb "
                "(P8, #60) to bring the layer into the sheet's CRS first.",
                kind="input",
            )


def assert_within_extent(config: MapConfig) -> None:
    """Coordinates must already sit in ``map.extent`` (tolerance: one extent span).

    Generous on purpose: the check is here to catch a layer in the *wrong CRS or
    wrong units*, which lands orders of magnitude away, not to police a footprint
    that overhangs the frame by a metre.
    """
    xmin, ymin, xmax, ymax = config.extent
    span_x, span_y = config.span
    for layer in config.layers:
        for feature in _features(layer):
            for x, y in _coordinates(feature.get("geometry") or {}):
                if not (xmin - span_x <= x <= xmax + span_x) or not (
                    ymin - span_y <= y <= ymax + span_y
                ):
                    raise PlotError(
                        f"layer {layer.name!r} has a coordinate ({x:g}, {y:g}) more than one "
                        f"extent span outside map.extent {list(config.extent)} — the layer is "
                        f"almost certainly not in {config.crs}. mapplot never reprojects and "
                        "never clips: run the 'geo' verb (P8, #60).",
                        kind="input",
                    )


def _declared_crs(data: dict[str, Any]) -> str | None:
    crs = data.get("crs")
    if isinstance(crs, str):
        return crs
    if isinstance(crs, dict):
        properties = crs.get("properties")
        if isinstance(properties, dict) and isinstance(properties.get("name"), str):
            return properties["name"]
    return None


def _normalise_crs(text: str) -> str:
    match = _EPSG_RE.search(str(text).strip())
    if match is not None:
        return f"EPSG:{int(match.group('code'))}"
    return " ".join(str(text).split()).upper()


def _coordinates(geometry: dict[str, Any]):
    def walk(value):
        if isinstance(value, (list, tuple)):
            if value and all(isinstance(item, (int, float)) for item in value[:2]) and not any(
                isinstance(item, (list, tuple)) for item in value
            ):
                yield (float(value[0]), float(value[1]))
                return
            for item in value:
                yield from walk(item)

    yield from walk(geometry.get("coordinates", []))


def _emit_feature(
    feature: dict[str, Any], layer: MapLayer, view: ViewBox, index: int
) -> tuple[str, ...]:
    geometry = feature.get("geometry")
    if not isinstance(geometry, dict):
        raise PlotError(
            f"layer {layer.name!r} feature {index} has no geometry object", kind="input"
        )
    kind = geometry.get("type")
    if kind not in SUPPORTED_GEOMETRY:
        raise PlotError(
            f"layer {layer.name!r} feature {index} has geometry type {kind!r}, which mapplot "
            f"does not draw. Supported: {', '.join(SUPPORTED_GEOMETRY)}. A silently skipped "
            "feature is a defect, not a default — split the geometry upstream.",
            kind="unsupported",
        )
    coords = geometry.get("coordinates")
    elements: list[str] = []
    if kind == "Point":
        x, y = view.point(float(coords[0]), float(coords[1]))
        elements.append(
            svg_furniture.svg_circle(
                x, y, layer.point_radius, fill=layer.stroke, stroke=layer.stroke,
                stroke_width=layer.stroke_width,
            )
        )
    elif kind == "LineString":
        elements.append(_polyline(coords, layer, view))
    elif kind == "MultiLineString":
        elements.extend(_polyline(part, layer, view) for part in coords)
    elif kind == "Polygon":
        elements.extend(_ring(ring, layer, view) for ring in coords)
    else:  # MultiPolygon
        elements.extend(
            _ring(ring, layer, view) for polygon in coords for ring in polygon
        )
    label = _label(feature, layer, view, coords, kind)
    if label is not None:
        elements.append(label)
    if not elements:
        raise PlotError(
            f"layer {layer.name!r} feature {index} ({kind}) produced no SVG element — "
            "an empty geometry would vanish from the sheet unnoticed",
            kind="input",
        )
    return tuple(elements)


def _polyline(coords, layer: MapLayer, view: ViewBox) -> str:
    points = [view.point(float(x), float(y)) for x, y, *_ in coords]
    path = "M " + " L ".join(f"{x:.1f} {y:.1f}" for x, y in points)
    return svg_furniture.svg_path(
        path, fill="none", stroke=layer.stroke, stroke_width=layer.stroke_width
    )


def _ring(ring, layer: MapLayer, view: ViewBox) -> str:
    points = [view.point(float(x), float(y)) for x, y, *_ in ring]
    return svg_furniture.svg_polygon(
        points, fill=layer.fill, stroke=layer.stroke, stroke_width=layer.stroke_width
    )


def _label(feature, layer: MapLayer, view: ViewBox, coords, kind: str) -> str | None:
    if layer.label is None:
        return None
    properties = feature.get("properties") or {}
    value = properties.get(layer.label)
    if value in (None, ""):
        return None
    points = list(_coordinates({"coordinates": coords}))
    if not points:
        return None
    cx = sum(x for x, _ in points) / len(points)
    cy = sum(y for _, y in points) / len(points)
    x, y = view.point(cx, cy)
    return svg_furniture.svg_text(x, y, str(value), font_size=10, fill=layer.stroke)


# --------------------------------------------------------------------------- #
# the entry point
# --------------------------------------------------------------------------- #


def build(
    config_path: str | Path,
    out_dir: str | Path | None = None,
    **plot_kw: Any,
) -> MapPlot:
    """Build a map sheet and plot it. One code path, ending at :func:`plot`."""
    config = load_map_config(config_path)
    out = Path(out_dir) if out_dir is not None else (config.source.parent / "out")
    out.mkdir(parents=True, exist_ok=True)

    assert_crs(config)
    assert_within_extent(config)
    svg_text, features = build_map_svg(config, out)

    report = _coverage_report(config, features)
    if not report.ok:
        raise PlotError(
            "map sheet is not faithful — "
            + report.summary()
            + "".join(f"\n  - {finding.message}" for finding in report.errors),
            kind="fidelity",
        )

    svg_path = out / f"{config.number}.svg"
    write_text_canonical(svg_path, svg_text, EmitPolicy.from_environment())
    log.info(
        "map %s: %d layer(s), %d feature(s) in %s -> %s",
        config.number,
        len(config.layers),
        len(features),
        config.crs,
        svg_path,
    )
    # Late import so THE entry point is resolved at call time: there is exactly one
    # route from a map sheet to a PDF, and it stays observable (and patchable) as one.
    from .plot import plot

    result = plot(PlotRequest(source=svg_path, out_dir=out, stem=config.number, **plot_kw))
    return MapPlot(config=config, svg=svg_path, result=result, fidelity=report)


def _coverage_report(config: MapConfig, features: Sequence[_Feature]) -> FidelityReport:
    findings = tuple(
        FidelityFinding(
            check="coverage",
            severity="error",
            message=(
                f"layer {feature.layer!r} feature {feature.index} ({feature.geometry_type}) "
                "produced no element in the sheet body"
            ),
        )
        for feature in features
        if not feature.elements
    )
    return FidelityReport(
        source=config.source,
        layout="map",
        expected_units=len(features),
        covered_units=sum(1 for feature in features if feature.elements),
        expected_exploded=0,
        recorded_ops=sum(len(feature.elements) for feature in features),
        extent_mm=config.span,
        findings=findings,
    )
