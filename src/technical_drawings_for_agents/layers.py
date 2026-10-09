"""Drawing layer tables: one declared pen vocabulary for DXF, SVG and PDF.

The toolkit used to let layer names and plotted appearance drift apart: DXF
entities could name layers that did not exist in the DXF table, while SVG output
used a separate hard-coded pen. This module makes that choice explicit. A layer
table is a text artifact, validated at the boundary, and only affects callers
that opt in by passing it to the DXF/SVG emitters.
"""

from __future__ import annotations

import difflib
import logging
import math
import re
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml

from .style import COL_CAD_OUTLINE

Background = Literal["light", "dark"]
UndeclaredPolicy = Literal["error", "warn"]

DEFAULT_PX_PER_MM: float = 96.0 / 25.4

VALID_DXF_LINEWEIGHTS = (
    0,
    5,
    9,
    13,
    15,
    18,
    20,
    25,
    30,
    35,
    40,
    50,
    53,
    60,
    70,
    80,
    90,
    100,
    106,
    120,
    140,
    158,
    200,
    211,
)
MAX_LINEWEIGHT_MM = 2.11

_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")
_INVALID_NAME_CHARS = set('<>/\\":;?*|=,')
_RESERVED_LAYER_NAMES = {"0", "DEFPOINTS"}
_DEFAULT_TABLE_PATH = Path(__file__).resolve().parent / "data" / "layers.default.yaml"
_LOG = logging.getLogger("technical_drawings_for_agents.layers")

_STOCK_LINETYPES = {
    "BYBLOCK",
    "BYLAYER",
    "CONTINUOUS",
    "CENTER",
    "CENTER2",
    "CENTERX2",
    "DASHDOT",
    "DASHDOT2",
    "DASHDOTX2",
    "DASHED",
    "DASHED2",
    "DASHEDX2",
    "DIVIDE",
    "DIVIDE2",
    "DIVIDEX2",
    "DOT",
    "DOT2",
    "DOTX2",
    "PHANTOM",
    "PHANTOM2",
    "PHANTOMX2",
}

class LayerTableError(ValueError):
    """Raised when a layer table is malformed, or a drawing uses an undeclared layer."""


@dataclass(frozen=True)
class LinetypeSpec:
    name: str
    pattern_mm: tuple[float, ...]
    description: str = ""

    def pattern_in_units(self, plot_scale: float) -> tuple[float, ...]:
        """Convert paper millimetres to drawing units (metres) at 1:``plot_scale``."""

        if isinstance(plot_scale, bool) or not isinstance(plot_scale, (int, float)):
            raise LayerTableError(f"plot_scale must be a positive number, got {plot_scale!r}")
        if plot_scale <= 0 or not math.isfinite(float(plot_scale)):
            raise LayerTableError(f"plot_scale must be a positive number, got {plot_scale!r}")
        return tuple(float(element) * float(plot_scale) / 1000.0 for element in self.pattern_mm)

    def dash_mm(self) -> tuple[float, ...]:
        """SVG-ready absolute paper-mm run lengths (dash, gap, ...), signs dropped."""

        return tuple(abs(element) for element in self.pattern_mm)


@dataclass(frozen=True)
class LayerSpec:
    name: str
    aci: int
    lineweight_mm: float = 0.25
    linetype: str = "CONTINUOUS"
    plot: bool = True
    description: str = ""
    true_color: str | None = None
    pen: str | None = None

    @property
    def dxf_lineweight(self) -> int:
        """``lineweight_mm`` snapped to a valid DXF lineweight (hundredths of a mm)."""

        return _snap_lineweight(self.name, self.lineweight_mm)[0]

    @property
    def written_lineweight_mm(self) -> float:
        """The width actually plotted: ``lineweight_mm`` after the DXF enum snap.

        DXF can only carry 24 discrete lineweights, so an authored ``0.36`` is
        written as ``0.35``. The SVG/PDF pen uses *this* value, not the authored
        one, because the point of the table is that the three artifacts agree —
        keeping an unrepresentable width alive in one of them would reintroduce
        exactly the drift this module exists to remove. The load-time warning is
        what tells the author their value was not representable.
        """

        return _snap_lineweight(self.name, self.lineweight_mm)[1]

    def ink(self, background: Background = "light") -> str:
        """Resolve the SVG/PDF pen colour: ``pen`` -> ``true_color`` -> ACI."""

        if background not in ("light", "dark"):
            raise LayerTableError(f"background must be one of light, dark, got {background!r}")
        if self.pen:
            return _normalise_hex(self.pen, f"layers.{self.name}.pen")
        if self.true_color:
            return _normalise_hex(self.true_color, f"layers.{self.name}.true_color")
        if self.aci == 7:
            return "#111111" if background == "light" else COL_CAD_OUTLINE
        return _aci_to_hex(self.aci)


@dataclass(frozen=True)
class Pen:
    """One resolved pen: what SVG/PDF should draw this layer with."""

    layer: str
    ink: str
    width_mm: float
    dash_mm: tuple[float, ...] = ()

    def width_px(self, px_per_mm: float) -> float:
        return self.width_mm * px_per_mm

    def dash_px(self, px_per_mm: float) -> tuple[float, ...]:
        return tuple(element * px_per_mm for element in self.dash_mm)

    def svg_dash(self, px_per_mm: float) -> str | None:
        """Return the ``stroke-dasharray`` string accepted by SVG primitives."""

        if not self.dash_mm:
            return None
        return ",".join(_fmt_px(value) for value in self.dash_px(px_per_mm))


@dataclass(frozen=True)
class ApplyReport:
    layers_created: tuple[str, ...] = ()
    layers_updated: tuple[str, ...] = ()
    linetypes_created: tuple[str, ...] = ()
    lineweights_snapped: tuple[tuple[str, float, float], ...] = ()
    undeclared: tuple[str, ...] = ()


@dataclass(frozen=True)
class LayerTable:
    layers: tuple[LayerSpec, ...]
    linetypes: tuple[LinetypeSpec, ...] = ()
    version: int = 1
    default_lineweight_mm: float = 0.25
    undeclared: UndeclaredPolicy = "error"
    background: Background = "light"
    source: Path | None = None

    def __post_init__(self) -> None:
        _validate_layer_table_object(self)

    # -- lookup -------------------------------------------------------------
    def names(self) -> tuple[str, ...]:
        return tuple(layer.name for layer in self.layers)

    def has(self, name: str) -> bool:
        return _fold(name) in {_fold(layer.name) for layer in self.layers}

    def get(self, name: str) -> LayerSpec:
        folded = _fold(name)
        for layer in self.layers:
            if _fold(layer.name) == folded:
                return layer
        raise LayerTableError(_unknown_layer_message(name, self.names()))

    def pen(self, name: str) -> Pen:
        layer = self.get(name)
        dash = ()
        linetype = self._custom_linetype(layer.linetype)
        if linetype is not None:
            dash = linetype.dash_mm()
        return Pen(
            layer=layer.name,
            ink=layer.ink(self.background),
            width_mm=layer.written_lineweight_mm,
            dash_mm=dash,
        )

    def pens(self) -> dict[str, Pen]:
        return {layer.name.upper(): self.pen(layer.name) for layer in self.layers}

    # -- application --------------------------------------------------------
    def apply(self, doc, *, plot_scale: float | None = None) -> ApplyReport:
        doc.header["$LWDISPLAY"] = 1

        linetypes_created: list[str] = []
        for linetype in self._referenced_custom_linetypes():
            if plot_scale is None:
                layer = self._first_layer_using_linetype(linetype.name)
                raise LayerTableError(
                    f"layer table {_source_label(self)}: layer '{layer.name}' uses custom "
                    f"linetype '{linetype.name}', whose pattern is declared in paper mm; "
                    "pass plot_scale=<1:N denominator> so it can be converted to drawing units"
                )
            if _table_has_entry(doc.linetypes, linetype.name):
                continue
            elements = linetype.pattern_in_units(plot_scale)
            pattern = [sum(abs(element) for element in elements), *elements]
            doc.linetypes.add(
                linetype.name,
                pattern=pattern,
                description=linetype.description,
            )
            linetypes_created.append(linetype.name)

        layers_created: list[str] = []
        layers_updated: list[str] = []
        snapped: list[tuple[str, float, float]] = []
        for layer in self.layers:
            dxf_lineweight, written_mm = _snap_lineweight(layer.name, layer.lineweight_mm)
            if abs(layer.lineweight_mm - written_mm) > 0.001:
                _LOG.warning(
                    "layer '%s': lineweight %g mm is not a DXF lineweight; writing %g mm",
                    layer.name,
                    layer.lineweight_mm,
                    written_mm,
                )
                snapped.append((layer.name, layer.lineweight_mm, written_mm))
            attribs = {
                "color": layer.aci,
                "lineweight": dxf_lineweight,
                "linetype": self._resolved_linetype_name(layer.linetype),
                "plot": layer.plot,
            }
            true_color = _true_color_int(layer.true_color)
            if true_color is not None:
                attribs["true_color"] = true_color

            if _table_has_entry(doc.layers, layer.name):
                record = doc.layers.get(layer.name)
                record.dxf.color = layer.aci
                record.dxf.lineweight = dxf_lineweight
                record.dxf.linetype = attribs["linetype"]
                record.dxf.plot = int(layer.plot)
                if true_color is not None:
                    record.dxf.true_color = true_color
                record.description = layer.description
                layers_updated.append(layer.name)
            else:
                record = doc.layers.add(layer.name, **attribs)
                record.description = layer.description
                layers_created.append(layer.name)

        return ApplyReport(
            layers_created=tuple(layers_created),
            layers_updated=tuple(layers_updated),
            linetypes_created=tuple(linetypes_created),
            lineweights_snapped=tuple(snapped),
        )

    def check_doc(self, doc) -> tuple[str, ...]:
        findings: list[str] = []
        zero_findings: list[tuple[str, Any]] = []
        undeclared: set[str] = set()

        for label, layout in _scan_layouts(doc):
            for entity in layout:
                _record_layer_finding(entity, label, self, zero_findings, undeclared)
        for block in doc.blocks:
            name = str(block.name)
            if name.startswith(("*", "_")):
                continue
            for entity in block:
                _record_layer_finding(entity, f"block {name}", self, zero_findings, undeclared)

        by_location: dict[str, Counter[str]] = {}
        for location, entity in zero_findings:
            by_location.setdefault(location, Counter())[entity.dxftype()] += 1
        for location, counts in by_location.items():
            total = sum(counts.values())
            detail = ", ".join(f"{kind} ×{count}" for kind, count in sorted(counts.items()))
            findings.append(
                f"geometry on layer '0': {total} entit(ies) ({detail}) in {location} "
                "- every entity must live on a declared layer"
            )

        for name in sorted(undeclared):
            findings.append(
                f"layer '{name}' is not declared in the layer table ({_source_label(self)})"
            )
        return tuple(findings)

    def _referenced_custom_linetypes(self) -> tuple[LinetypeSpec, ...]:
        used = {_fold(layer.linetype) for layer in self.layers}
        return tuple(linetype for linetype in self.linetypes if _fold(linetype.name) in used)

    def _first_layer_using_linetype(self, name: str) -> LayerSpec:
        folded = _fold(name)
        for layer in self.layers:
            if _fold(layer.linetype) == folded:
                return layer
        raise AssertionError("referenced linetype had no layer")

    def _custom_linetype(self, name: str) -> LinetypeSpec | None:
        folded = _fold(name)
        for linetype in self.linetypes:
            if _fold(linetype.name) == folded:
                return linetype
        return None

    def _resolved_linetype_name(self, name: str) -> str:
        custom = self._custom_linetype(name)
        if custom is not None:
            return custom.name
        return name.upper()


def load_layer_table(path: str | Path) -> LayerTable:
    table_path = Path(path).resolve()
    try:
        raw = yaml.safe_load(table_path.read_text(encoding="utf-8")) or {}
    except OSError as exc:
        raise LayerTableError(f"cannot read layer table {table_path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise LayerTableError(f"invalid YAML in layer table {table_path}: {exc}") from exc
    return _layer_table_from_raw(raw, table_path)


@lru_cache(maxsize=1)
def default_layer_table() -> LayerTable:
    return load_layer_table(_DEFAULT_TABLE_PATH)


def resolve_layer_table(
    spec: str | Path | None, *, base: Path | None = None
) -> LayerTable | None:
    if spec is None:
        return None
    if isinstance(spec, str) and spec.strip().casefold() == "default":
        return default_layer_table()
    path = Path(spec)
    if not path.is_absolute() and base is not None:
        path = base / path
    return load_layer_table(path)


def _layer_table_from_raw(raw: Any, source: Path) -> LayerTable:
    if not isinstance(raw, dict):
        raise LayerTableError(f"{source.name}: top level must be a mapping")
    _reject_unknown(raw, {"layer_table", "linetypes", "layers"}, source.name)

    meta = raw.get("layer_table")
    if not isinstance(meta, dict):
        raise LayerTableError(f"{source.name}: missing layer_table: {{version}}")
    _reject_unknown(meta, {"version", "default_lineweight_mm", "undeclared", "background"},
                    "layer_table")

    version = meta.get("version")
    if isinstance(version, bool) or not isinstance(version, int):
        raise LayerTableError(f"{source.name}: layer_table.version must be 1")
    if version != 1:
        raise LayerTableError(f"{source.name}: layer_table.version {version!r} is not supported; "
                              "supported version is 1")

    default_lineweight = _float(meta.get("default_lineweight_mm", 0.25),
                                "layer_table.default_lineweight_mm")
    _snap_lineweight("layer_table.default_lineweight_mm", default_lineweight)

    undeclared = meta.get("undeclared", "error")
    if undeclared not in ("error", "warn"):
        raise LayerTableError("layer_table.undeclared must be one of error, warn")
    background = meta.get("background", "light")
    if background not in ("light", "dark"):
        raise LayerTableError("layer_table.background must be one of light, dark")

    linetypes = _load_linetypes(raw.get("linetypes", []))
    layers = _load_layers(raw.get("layers"), default_lineweight, linetypes)

    return LayerTable(
        layers=tuple(layers),
        linetypes=tuple(linetypes),
        version=version,
        default_lineweight_mm=default_lineweight,
        undeclared=undeclared,
        background=background,
        source=source,
    )


def _load_linetypes(raw: Any) -> list[LinetypeSpec]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise LayerTableError("linetypes must be a list")
    linetypes: list[LinetypeSpec] = []
    seen: dict[str, str] = {}
    for index, item in enumerate(raw):
        ctx = f"linetypes[{index}]"
        if not isinstance(item, dict):
            raise LayerTableError(f"{ctx} must be a mapping")
        _reject_unknown(item, {"name", "pattern_mm", "description"}, ctx)
        name = _name(item.get("name"), f"{ctx}.name")
        folded = _fold(name)
        if folded in seen:
            raise LayerTableError(
                f"{ctx}.name duplicate linetype {name!r} "
                f"(case-insensitively equal to {seen[folded]!r})"
            )
        if folded.upper() in _STOCK_LINETYPES:
            raise LayerTableError(f"{ctx}.name {name!r} shadows a stock ezdxf linetype")
        seen[folded] = name
        pattern = item.get("pattern_mm")
        if not isinstance(pattern, list) or not 2 <= len(pattern) <= 12:
            raise LayerTableError(f"{ctx}.pattern_mm must be a list of 2-12 numbers")
        values = tuple(_float(value, f"{ctx}.pattern_mm[{i}]") for i, value in enumerate(pattern))
        if values[0] <= 0:
            raise LayerTableError(f"{ctx}.pattern_mm must start with a dash")
        if not any(value < 0 for value in values):
            raise LayerTableError(f"{ctx}.pattern_mm must contain at least one gap")
        for value in values:
            if abs(value) > 100.0:
                raise LayerTableError(f"{ctx}.pattern_mm values must be <= 100.0 mm")
        linetypes.append(
            LinetypeSpec(
                name=name,
                pattern_mm=values,
                description=_description(item.get("description", ""), f"{ctx}.description"),
            )
        )
    return linetypes


def _load_layers(
    raw: Any, default_lineweight: float, linetypes: list[LinetypeSpec]
) -> list[LayerSpec]:
    if not isinstance(raw, list) or not raw:
        raise LayerTableError("layers must be a non-empty list")
    layers: list[LayerSpec] = []
    seen: dict[str, str] = {}
    custom = {_fold(linetype.name): linetype.name for linetype in linetypes}
    available = sorted([*_STOCK_LINETYPES, *(linetype.name for linetype in linetypes)])
    for index, item in enumerate(raw):
        ctx = f"layers[{index}]"
        if not isinstance(item, dict):
            raise LayerTableError(f"{ctx} must be a mapping")
        _reject_unknown(
            item,
            {
                "name",
                "aci",
                "true_color",
                "lineweight_mm",
                "linetype",
                "plot",
                "description",
                "pen",
            },
            ctx,
        )
        name = _name(item.get("name"), f"{ctx}.name")
        folded = _fold(name)
        if folded in seen:
            raise LayerTableError(
                f"{ctx}.name duplicate layer {name!r} "
                f"(case-insensitively equal to {seen[folded]!r})"
            )
        seen[folded] = name

        aci = item.get("aci")
        if isinstance(aci, bool) or not isinstance(aci, int) or not 1 <= aci <= 255:
            raise LayerTableError(f"{ctx}.aci must be an integer in the range 1..255")

        lineweight = _float(item.get("lineweight_mm", default_lineweight),
                            f"{ctx}.lineweight_mm")
        _, written_mm = _snap_lineweight(name, lineweight)
        if abs(lineweight - written_mm) > 0.001:
            _LOG.warning(
                "layer '%s': lineweight %g mm is not a DXF lineweight; writing %g mm",
                name,
                lineweight,
                written_mm,
            )

        linetype_raw = item.get("linetype", "CONTINUOUS")
        if not isinstance(linetype_raw, str) or not linetype_raw.strip():
            raise LayerTableError(f"{ctx}.linetype must be a non-empty string")
        linetype = linetype_raw.strip()
        linetype_folded = _fold(linetype)
        if linetype_folded.upper() in _STOCK_LINETYPES:
            resolved_linetype = linetype.upper()
        elif linetype_folded in custom:
            resolved_linetype = custom[linetype_folded]
        else:
            raise LayerTableError(
                f"{ctx}.linetype unknown linetype {linetype!r} "
                f"(available: {', '.join(available)})"
            )

        plot = item.get("plot", True)
        if not isinstance(plot, bool):
            raise LayerTableError(f"{ctx}.plot must be true or false")

        layers.append(
            LayerSpec(
                name=name,
                aci=aci,
                lineweight_mm=lineweight,
                linetype=resolved_linetype,
                plot=plot,
                description=_description(item.get("description", ""), f"{ctx}.description"),
                true_color=_optional_hex(item.get("true_color"), f"{ctx}.true_color"),
                pen=_optional_hex(item.get("pen"), f"{ctx}.pen"),
            )
        )
    return layers


def _validate_layer_table_object(table: LayerTable) -> None:
    if table.version != 1:
        raise LayerTableError(f"layer table version must be 1, got {table.version!r}")
    _snap_lineweight("layer_table.default_lineweight_mm", table.default_lineweight_mm)
    if table.undeclared not in ("error", "warn"):
        raise LayerTableError("layer_table.undeclared must be one of error, warn")
    if table.background not in ("light", "dark"):
        raise LayerTableError("layer_table.background must be one of light, dark")
    if not table.layers:
        raise LayerTableError("layers must be a non-empty list")

    seen_layers: dict[str, str] = {}
    custom: dict[str, str] = {}
    available = sorted([*_STOCK_LINETYPES, *(linetype.name for linetype in table.linetypes)])
    for linetype in table.linetypes:
        _name(linetype.name, f"linetypes.{linetype.name}.name")
        folded = _fold(linetype.name)
        if folded in custom:
            raise LayerTableError(f"duplicate linetype {linetype.name!r}")
        custom[folded] = linetype.name
        if folded.upper() in _STOCK_LINETYPES:
            raise LayerTableError(f"linetype {linetype.name!r} shadows a stock ezdxf linetype")
        _description(linetype.description, f"linetypes.{linetype.name}.description")
        values = tuple(
            _float(value, f"linetypes.{linetype.name}.pattern_mm[{index}]")
            for index, value in enumerate(linetype.pattern_mm)
        )
        if not 2 <= len(values) <= 12:
            raise LayerTableError(f"linetype {linetype.name!r}: pattern_mm must have 2-12 elements")
        if values[0] <= 0:
            raise LayerTableError(f"linetype {linetype.name!r}: pattern_mm must start with a dash")
        if not any(value < 0 for value in values):
            raise LayerTableError(f"linetype {linetype.name!r}: pattern_mm needs at least one gap")
        if any(abs(value) > 100.0 for value in values):
            raise LayerTableError(f"linetype {linetype.name!r}: pattern_mm values must be <= 100.0")

    for layer in table.layers:
        _name(layer.name, f"layers.{layer.name}.name")
        folded = _fold(layer.name)
        if folded in seen_layers:
            raise LayerTableError(f"duplicate layer {layer.name!r}")
        seen_layers[folded] = layer.name
        if (
            isinstance(layer.aci, bool)
            or not isinstance(layer.aci, int)
            or not 1 <= layer.aci <= 255
        ):
            raise LayerTableError(f"layer {layer.name!r}: aci must be in the range 1..255")
        _snap_lineweight(layer.name, layer.lineweight_mm)
        linetype_folded = _fold(layer.linetype)
        if linetype_folded.upper() not in _STOCK_LINETYPES and linetype_folded not in custom:
            raise LayerTableError(
                f"layer {layer.name!r}: unknown linetype {layer.linetype!r} "
                f"(available: {', '.join(available)})"
            )
        if not isinstance(layer.plot, bool):
            raise LayerTableError(f"layer {layer.name!r}: plot must be true or false")
        _description(layer.description, f"layers.{layer.name}.description")
        _optional_hex(layer.true_color, f"layers.{layer.name}.true_color")
        _optional_hex(layer.pen, f"layers.{layer.name}.pen")


def _record_layer_finding(
    entity,
    location: str,
    table: LayerTable,
    zero_findings: list[tuple[str, Any]],
    undeclared: set[str],
) -> None:
    layer = str(getattr(entity.dxf, "layer", "0"))
    if entity.dxftype() == "POINT" and layer.casefold() == "defpoints":
        return
    if layer == "0":
        zero_findings.append((location, entity))
        return
    if not table.has(layer):
        undeclared.add(layer)


def _scan_layouts(doc) -> list[tuple[str, Any]]:
    layouts = [("modelspace", doc.modelspace())]
    for layout in doc.layouts:
        if str(layout.name).lower() != "model":
            layouts.append((f"paperspace {layout.name}", layout))
    return layouts


def _snap_lineweight(layer_name: str, value: float) -> tuple[int, float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise LayerTableError(f"layer '{layer_name}': lineweight {value!r} must be a number")
    if value < 0 or value > MAX_LINEWEIGHT_MM:
        raise LayerTableError(
            f"layer '{layer_name}': lineweight {value:g} mm is outside the DXF range "
            f"0..{MAX_LINEWEIGHT_MM:g} mm"
        )
    hundredths = float(value) * 100.0
    for candidate in VALID_DXF_LINEWEIGHTS:
        if math.isclose(hundredths, candidate, abs_tol=1e-9):
            return candidate, candidate / 100.0
    nearest = min(
        VALID_DXF_LINEWEIGHTS,
        key=lambda candidate: (abs(candidate - hundredths), candidate),
    )
    return nearest, nearest / 100.0


def _name(value: Any, ctx: str) -> str:
    if not isinstance(value, str):
        raise LayerTableError(f"{ctx} must be a string")
    name = value.strip()
    if not 1 <= len(name) <= 255:
        raise LayerTableError(f"{ctx} must be 1-255 characters")
    reserved = name.casefold().upper()
    if reserved in _RESERVED_LAYER_NAMES:
        raise LayerTableError(f"{ctx} {name!r} is reserved")
    bad = sorted({char for char in name if char in _INVALID_NAME_CHARS})
    if bad:
        raise LayerTableError(f"{ctx} contains invalid character(s): {''.join(bad)}")
    return name


def _description(value: Any, ctx: str) -> str:
    if not isinstance(value, str):
        raise LayerTableError(f"{ctx} must be a string")
    if len(value) > 255:
        raise LayerTableError(f"{ctx} must be <= 255 characters")
    return value


def _float(value: Any, ctx: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise LayerTableError(f"{ctx} must be a number")
    return float(value)


def _optional_hex(value: Any, ctx: str) -> str | None:
    if value is None:
        return None
    return _normalise_hex(value, ctx)


def _normalise_hex(value: Any, ctx: str) -> str:
    if not isinstance(value, str) or not _HEX.fullmatch(value):
        raise LayerTableError(f"{ctx} must be #RRGGBB")
    return value.lower()


def _true_color_int(value: str | None) -> int | None:
    if value is None:
        return None
    return int(value[1:], 16)


def _aci_to_hex(aci: int) -> str:
    """Resolve an ACI index through ezdxf's own palette — never a second copy of it.

    ``ezdxf`` is a hard dependency of this package, and its AutoCAD colour palette
    is the only authority for what an ACI index looks like. Carrying a local table
    of "the usual" hex values would be a second source of truth that could quietly
    disagree with what the DXF plots, so a missing ezdxf fails loudly instead.
    """

    try:
        from ezdxf.colors import aci2rgb
    except ModuleNotFoundError as exc:  # pragma: no cover - ezdxf is a hard dependency
        raise LayerTableError(
            f"ezdxf is required to resolve ACI {aci} to #RRGGBB; install ezdxf>=1.1"
        ) from exc
    rgb = aci2rgb(aci)
    return f"#{rgb.r:02x}{rgb.g:02x}{rgb.b:02x}"


def _table_has_entry(table: Any, name: str) -> bool:
    if hasattr(table, "has_entry"):
        return bool(table.has_entry(name))
    return name in table


def _reject_unknown(data: dict[str, Any], allowed: set[str], ctx: str) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise LayerTableError(f"{ctx}: unknown key(s): {', '.join(unknown)}")


def _unknown_layer_message(name: str, available: tuple[str, ...]) -> str:
    close = difflib.get_close_matches(name, available, n=3, cutoff=0.0)
    suffix = f" (declared: {', '.join(available)})" if available else ""
    if close:
        suffix = f" (closest declared: {', '.join(close)}; declared: {', '.join(available)})"
    return f"layer {name!r} is not declared in the layer table{suffix}"


def _source_label(table: LayerTable) -> str:
    return str(table.source) if table.source is not None else "<in-memory>"


def _fold(name: str) -> str:
    return str(name).casefold()


def _fmt_px(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")
