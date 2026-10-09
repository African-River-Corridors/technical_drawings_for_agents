"""Dimensions and setting-out computed from placed layout geometry.

A dimension declares WHAT to measure — two placements, a group, a datum, a route —
and this module computes the value from the same placed geometry the sheet draws,
with the same distance code the layout checks use. **There is no field anywhere in
the schema that supplies the displayed number.**

Three properties carry the whole design:

``one source per number``
    Every ``clearance`` scalar is :func:`~.layout.polygon_gap` applied to
    :func:`~.layout.footprint` — the same function objects ``check_layout``'s
    ``clear-spacing`` rule calls. A sheet therefore cannot contradict its own check.

``the witness invariant``
    ``polygon_gap`` returns a scalar, but a dimension needs two *points*, and for
    parallel rectangles the minimum-distance pair is non-unique. The pair comes from
    a deterministic two-branch rule (§4.3), and :func:`render_dimensions` then asserts
    ``drawn_length_m == abs(value_m)`` and **raises** otherwise. That makes it
    structurally impossible to ship a line spanning one distance labelled with
    another — the exact defect this module exists to eliminate.

``never invent``
    Literal coordinates are legal only inside a sourced ``datums:`` entry; a ``label``
    matching ``\\d[.,]\\d`` is rejected at load and the author is pointed at
    ``expect_m``, which is *checked* and never printed. An unresolvable reference is a
    hard error with an actionable message, never a guess and never a blank.

The register carries no elevation, so ``setting_out.z`` is a mandatory explicit
policy and ``z: {source: none}`` renders the literal ``NOT SURVEYED``. No default can
exist that does not either invent a level or hide its absence.

A fourth property, added by P11: ``a chain may not contradict its own overall``. Where a
sheet dimensions both the parts of a run and its overall length, a declared ``chains:``
entry names them and :func:`check_chains` asserts that the **displayed** components sum
to the **displayed** overall. Rounding each part independently is what breaks this, so
the comparison happens on the printed numbers, at a tolerance of half a display unit
derived from ``decimals``. Chains are declared and never inferred from geometry, and the
tool never adjusts a component to make the sum work — it reports, the drafter decides.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal

import yaml

from ..legibility import LegibilityError, text_box
from ..provenance import ProvenanceError
from ..provenance import fmt as canonical_fmt
from ..style import COL_CAD_CENTER, COL_CAD_DIM, COL_CAD_DIM_TEXT
from ..svg import svg_circle, svg_line, svg_polygon, svg_text
from .layout import (
    Finding,
    Layout,
    LayoutError,
    Placement,
    Severity,
    _angle_delta,
    _mean_bearing,
    _project,
    _rects_overlap,
    build_layout,
    footprint,
    polygon_gap,
    snap_groups,
)
from .place import PlacedFeature
from .spec import SOURCE_STATUSES, Point

DIMENSION_KINDS = ("clearance", "centres", "envelope", "offset", "chainage")
AXIS_WORDS = ("easting", "northing", "principal", "principal-cross", "direct")
SNAP_MODES = ("effective", "as-placed")
ON_ZERO_MODES = ("error", "leader")
Z_SOURCES = ("none", "property", "datum")
SETTING_OUT_INCLUDES = ("placements", "datums", "route-points")
SETTING_OUT_ORDERS = ("id", "register", "northing")
WITNESS_MODES = ("edge", "vertex", "axis", "point", "path")

#: Below this a dimension line cannot be drawn honestly, so the leader form is used.
ZERO_EPS_M = 1e-6
#: ``drawn_length_m`` vs ``value_m``. A violation is a code defect, so it raises.
WITNESS_TOL_M = 1e-9
#: Tie window when re-enumerating ``polygon_gap``'s own loop for the witness pair.
VERTEX_MATCH_TOL_M = 1e-12
#: The literal printed in a ``Z`` cell when no level has been surveyed.
NOT_SURVEYED = "NOT SURVEYED"
#: A decimal number inside a label is the one construct that smuggles a hand-typed
#: measurement onto a sheet disguised as a caption.
_LABEL_NUMBER = re.compile(r"\d[.,]\d")
_BEARING_AXIS = re.compile(r"^bearing:(-?\d+(?:\.\d+)?)$")

_MAX_DECIMALS = 4
_SHORT_DIMENSION_ARROWS = 4.0     # arrowheads flip outside below 4 * arrow_px
_DEFAULT_FONT_SIZE = 10.0         # matches svg_dimension_h's text (svg.py)

#: A chain needs at least this many components. One "component" plus an overall is a
#: truncated declaration, not a run, and accepting it would check nothing while
#: reporting clean.
_MIN_CHAIN_COMPONENTS = 2
#: Millimetres per metre — the discrepancy is reported in mm whatever ``decimals`` is.
_MM_PER_M = 1000.0

_TOP_LEVEL_KEYS = frozenset(
    {
        "dimension_set",
        "defaults",
        "datums",
        "naming",
        "routes",
        "dimensions",
        "chains",
        "setting_out",
    }
)
_DIMENSION_SET_KEYS = frozenset({"id", "layout", "snap"})
_STYLE_KEYS = (
    "decimals",
    "units_suffix",
    "offset_px",
    "witness_gap_px",
    "witness_over_px",
    "text_gap_px",
    "arrow_px",
    "expect_tol_m",
    "on_zero",
    "parallel_tol_deg",
)
_TEXT_KEYS = frozenset({"side", "along_px", "rotate", "leader"})
_DIMENSION_COMMON_KEYS = frozenset(
    {"id", "kind", "label", "expect_m", "expect_source", "severity", "text", "layer", *_STYLE_KEYS}
)
_PER_KIND_KEYS: dict[str, frozenset[str]] = {
    "clearance": frozenset({"from", "to"}),
    "centres": frozenset({"from", "to"}),
    "envelope": frozenset({"of", "axis"}),
    "offset": frozenset({"from", "to", "axis"}),
    "chainage": frozenset({"route", "from", "to"}),
}
_REQUIRED_PER_KIND: dict[str, tuple[str, ...]] = {
    "clearance": ("from", "to"),
    "centres": ("from", "to"),
    "envelope": ("of", "axis"),
    "offset": ("from", "to", "axis"),
    "chainage": ("route",),
}
_CHAIN_KEYS = frozenset({"id", "overall", "components", "severity", "note"})
_DATUM_KEYS = frozenset({"name", "point", "z", "source", "status"})
_NAMING_KEYS = frozenset({"name", "where"})
_WHERE_KEYS = frozenset({"type", "properties"})
_WHERE_POSITION_KEYS = ("origin_utm", "size_m", "rotation_deg")
_ROUTE_KEYS = frozenset({"name", "kind", "points", "attributes"})
_SETTING_OUT_KEYS = frozenset(
    {"include", "of", "z", "note", "vertical_datum", "order", "decimals"}
)
_REF_SELECTORS = ("placement", "feature", "datum", "station")
_FEATURE_FILTERS = frozenset({"placement", "role", "tag", "component"})
_SELECTOR_KEYS = ("all", "type", "types", "placements")


class DimensionError(ValueError):
    """Raised when a dimensions file, a reference, or a measurement is invalid."""


# --------------------------------------------------------------------------- #
# model
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Datum:
    """A named, sourced control point — the only place a literal coordinate may enter."""

    name: str
    point: Point
    source: str
    z: float | None = None
    status: str = "verify"


@dataclass(frozen=True)
class Ref:
    """A parsed-at-load reference. Exactly one selector is populated."""

    kind: Literal["placement", "feature", "datum", "station"]
    key: str | None = None
    filters: dict[str, str] = field(default_factory=dict)
    station: str | None = None
    inner: Ref | None = None

    def describe(self) -> str:
        if self.kind == "feature":
            parts = [f"{name} {self.filters[name]!r}" for name in sorted(self.filters)]
            return f"feature ({', '.join(parts)})" if parts else "feature (no filters)"
        if self.kind == "station":
            if self.inner is not None:
                return f"station project:{self.inner.describe()}"
            return f"station {self.station!r}"
        return f"{self.kind} {self.key!r}"


@dataclass(frozen=True)
class Route:
    name: str
    kind: str
    points: tuple[Ref, ...]
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Style:
    """Every renderable knob, resolved from ``defaults`` then the dimension itself.

    The ``*_px`` names are the projector's own paper units: an ``svg.ViewBox``
    measures in SVG pixels and P1's ``sheet.Viewport`` in paper millimetres, and both
    are accepted by :func:`render_dimensions`. What the suffix really pins down is
    that these are *paper-space* lengths, never model metres — an extension line must
    be the same physical size on the sheet whatever the plot scale.
    """

    decimals: int = 3
    units_suffix: str = " m"
    offset_px: float = 25.0
    witness_gap_px: float = 3.0
    witness_over_px: float = 4.0
    text_gap_px: float = 4.0
    arrow_px: float = 5.0
    expect_tol_m: float = 0.002
    on_zero: Literal["error", "leader"] = "error"
    parallel_tol_deg: float = 1.0
    text_side: Literal["above", "below"] = "above"
    text_along_px: float = 0.0
    text_rotate: Literal["auto", "none"] = "auto"
    text_leader: bool = False
    layer: str = "DIM"


@dataclass(frozen=True)
class DimensionSpec:
    id: str
    kind: str
    style: Style
    from_ref: Ref | None = None
    to_ref: Ref | None = None
    of: dict[str, Any] | None = None
    axis: str | None = None
    route: str | None = None
    label: str = ""
    expect_m: float | None = None
    expect_source: str | None = None
    severity: Severity = "error"


@dataclass(frozen=True)
class Chain:
    """An ordered run: several component dimensions that must sum to one overall.

    A declaration, never an inference. Collinear dimensions are not necessarily a run
    and a run is not necessarily collinear, so a geometric guess would fire on unrelated
    dimensions — which is how a check gets switched off. ``components`` order is the
    order a reader adds them up in and is preserved verbatim in the failure message.
    """

    id: str
    overall: str
    components: tuple[str, ...]
    severity: Severity = "error"
    note: str = ""


@dataclass(frozen=True)
class SettingOutSpec:
    include: tuple[str, ...] = ("placements",)
    of: dict[str, Any] = field(default_factory=lambda: {"all": True})
    z_source: Literal["none", "property", "datum"] = "none"
    z_property: str | None = None
    z_datum: str | None = None
    note: str | None = None
    vertical_datum: str | None = None
    order: Literal["id", "register", "northing"] = "id"
    decimals: int = 3


@dataclass(frozen=True)
class DimensionSet:
    id: str
    source: Path
    layout_path: Path
    snap: Literal["effective", "as-placed"]
    defaults: Style
    datums: tuple[Datum, ...]
    naming: tuple[tuple[str, dict[str, Any]], ...]
    routes: tuple[Route, ...]
    dimensions: tuple[DimensionSpec, ...]
    setting_out: SettingOutSpec | None = None
    chains: tuple[Chain, ...] = ()


@dataclass(frozen=True)
class Measurement:
    """One computed dimension: the value, the geometry that proves it, the text."""

    spec: DimensionSpec
    value_m: float
    anchor_a: Point
    anchor_b: Point
    text: str
    path: tuple[Point, ...] = ()
    direction_token: str = ""
    witness_mode: Literal["edge", "vertex", "axis", "point", "path"] = "point"
    provenance: dict[str, Any] = field(default_factory=dict)

    @property
    def drawn_length_m(self) -> float:
        return math.dist(self.anchor_a, self.anchor_b)

    @property
    def is_zero(self) -> bool:
        return abs(self.value_m) < ZERO_EPS_M


@dataclass(frozen=True)
class AnnotationBox:
    """A text box handed to P5's legibility checks. P6 never resolves collisions.

    ``estimated`` stays ``True`` even for monospace, where P5's own metric reports
    ``confidence == "exact"``: a table lookup of nominal advances is still not a
    measured advance from the rendering font. ``confidence`` carries P5's word so a
    consumer can tell the two apart.
    """

    id: str
    kind: str
    x: float
    y: float
    width: float
    height: float
    rotation_deg: float = 0.0
    text: str = ""
    estimated: bool = True
    confidence: str = "estimated"


@dataclass(frozen=True)
class SettingOutRow:
    id: str
    easting: float
    northing: float
    z: float | None
    kind: str
    type_name: str = ""
    source: str = ""


@dataclass(frozen=True)
class SettingOutTable:
    """Pre-formatted setting-out text: the SVG table and the sidecar share these rows."""

    header: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    note: str | None = None
    caption: str = ""


@dataclass(frozen=True)
class Resolver:
    """Key -> placement / datum / route indexes for one dimension set."""

    layout: Layout
    placements: dict[str, Placement]
    datums: dict[str, Datum]
    routes: dict[str, Route]
    snap: str
    snap_report: tuple[str, ...] = ()
    _feature_cache: list[list[tuple[Placement, PlacedFeature]]] = field(
        default_factory=list, repr=False, compare=False
    )

    @property
    def known_keys(self) -> list[str]:
        return sorted(self.placements)

    def placed_features(self) -> list[tuple[Placement, PlacedFeature]]:
        if not self._feature_cache:
            try:
                self._feature_cache.append(build_layout(self.layout))
            except LayoutError as exc:
                raise DimensionError(f"cannot expand component geometry: {exc}") from exc
        return self._feature_cache[0]

    def key_for(self, placement: Placement) -> str:
        """The durable handle for a placement, including an author-declared ``naming``."""

        identity = placement.identity
        if identity is not None:
            return identity
        for key, candidate in self.placements.items():
            if candidate is placement:
                return key
        raise DimensionError(_unkeyed_message(self.layout, placement, self.known_keys))


# --------------------------------------------------------------------------- #
# loading
# --------------------------------------------------------------------------- #


def load_dimensions(
    path: str | Path, *, layout_path: str | Path | None = None
) -> DimensionSet:
    """Load and fully validate a dimensions file. Raises ``DimensionError`` on any defect."""

    source = Path(path).resolve()
    data = _read_yaml(source)
    if not isinstance(data, dict):
        raise DimensionError(f"{source.name}: top level must be a mapping")
    _reject_unknown(data, _TOP_LEVEL_KEYS, source.name)

    block = data.get("dimension_set")
    if not isinstance(block, dict):
        raise DimensionError(f"{source.name}: missing dimension_set: {{id, layout}}")
    _reject_unknown(block, _DIMENSION_SET_KEYS, f"{source.name}: dimension_set")
    set_id = block.get("id")
    if not isinstance(set_id, str) or not set_id.strip():
        raise DimensionError(f"{source.name}: dimension_set.id must be a non-empty string")
    snap = str(block.get("snap", "effective"))
    if snap not in SNAP_MODES:
        raise DimensionError(
            f"{source.name}: dimension_set.snap must be one of {list(SNAP_MODES)}, got {snap!r}"
        )

    resolved_layout = _resolve_layout_path(block.get("layout"), layout_path, source)
    defaults = _style_from(data.get("defaults"), f"{source.name}: defaults", Style())
    datums = _load_datums(data.get("datums"), source.name)
    naming = _load_naming(data.get("naming"), source.name, {d.name for d in datums})
    routes = _load_routes(data.get("routes"), source.name)
    dimensions = _load_dimension_specs(data.get("dimensions"), defaults, source.name)
    _reject_duplicate_route_refs(dimensions, routes, source.name)
    chains = _load_chains(data.get("chains"), dimensions, source.name)
    setting_out = _load_setting_out(data.get("setting_out"), defaults, datums, source.name)

    return DimensionSet(
        id=set_id.strip(),
        source=source,
        layout_path=resolved_layout,
        snap=snap,  # type: ignore[arg-type]
        defaults=defaults,
        datums=tuple(datums),
        naming=tuple(naming),
        routes=tuple(routes),
        dimensions=tuple(dimensions),
        setting_out=setting_out,
        chains=tuple(chains),
    )


def _resolve_layout_path(raw: Any, override: str | Path | None, source: Path) -> Path:
    if override is not None:
        candidate = Path(override).resolve()
    else:
        if not isinstance(raw, str) or not raw.strip():
            raise DimensionError(
                f"{source.name}: dimension_set.layout must be a path to a layout config "
                "(or pass --layout)"
            )
        candidate = (source.parent / raw.strip()).resolve()
    if not candidate.is_file():
        raise DimensionError(
            f"{source.name}: dimension_set.layout not a readable file: {candidate}"
        )
    return candidate


def _read_yaml(path: Path) -> Any:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise DimensionError(f"cannot read {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise DimensionError(f"invalid YAML in {path}: {exc}") from exc


def _reject_unknown(raw: Mapping[str, Any], allowed: Iterable[str], ctx: str) -> None:
    """A typo must not be a silent default, so unknown keys are rejected everywhere."""

    allowed_set = set(allowed)
    unknown = sorted(str(key) for key in raw if str(key) not in allowed_set)
    if unknown:
        raise DimensionError(
            f"{ctx}: unknown key(s) {', '.join(unknown)} "
            f"(allowed: {', '.join(sorted(allowed_set))})"
        )


def _number(raw: Any, ctx: str) -> float:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise DimensionError(f"{ctx} must be a number, got {raw!r}")
    value = float(raw)
    if not math.isfinite(value):
        raise DimensionError(f"{ctx} must be finite, got {raw!r}")
    return value


def _positive(raw: Any, ctx: str) -> float:
    value = _number(raw, ctx)
    if value <= 0:
        raise DimensionError(f"{ctx} must be > 0, got {value!r}")
    return value


def _non_negative(raw: Any, ctx: str) -> float:
    value = _number(raw, ctx)
    if value < 0:
        raise DimensionError(f"{ctx} must be >= 0, got {value!r}")
    return value


def _decimals(raw: Any, ctx: str) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise DimensionError(f"{ctx} must be an integer, got {raw!r}")
    if not 0 <= raw <= _MAX_DECIMALS:
        raise DimensionError(
            f"{ctx} must be between 0 and {_MAX_DECIMALS}, got {raw} — the placements register "
            "is itself 3 dp, so a 4th decimal is already noise and a 5th is a lie about precision"
        )
    return raw


def _coord(raw: Any, ctx: str) -> Point:
    if not isinstance(raw, list) or len(raw) != 2:
        raise DimensionError(f"{ctx} must be [E, N]")
    return (_number(raw[0], f"{ctx}[0]"), _number(raw[1], f"{ctx}[1]"))


def _style_from(raw: Any, ctx: str, base: Style) -> Style:
    """Resolve the ten shared style keys over ``base``. Used for defaults and overrides."""

    if raw is None:
        return base
    if not isinstance(raw, dict):
        raise DimensionError(f"{ctx} must be a mapping")
    _reject_unknown(raw, _STYLE_KEYS, ctx)
    values: dict[str, Any] = {}
    if "decimals" in raw:
        values["decimals"] = _decimals(raw["decimals"], f"{ctx}.decimals")
    if "units_suffix" in raw:
        suffix = raw["units_suffix"]
        if not isinstance(suffix, str):
            raise DimensionError(f"{ctx}.units_suffix must be a string")
        values["units_suffix"] = suffix
    for key in ("offset_px", "arrow_px", "expect_tol_m", "parallel_tol_deg"):
        if key in raw:
            values[key] = _positive(raw[key], f"{ctx}.{key}")
    for key in ("witness_gap_px", "witness_over_px", "text_gap_px"):
        if key in raw:
            values[key] = _non_negative(raw[key], f"{ctx}.{key}")
    if "on_zero" in raw:
        mode = str(raw["on_zero"])
        if mode not in ON_ZERO_MODES:
            raise DimensionError(f"{ctx}.on_zero must be one of {list(ON_ZERO_MODES)}")
        values["on_zero"] = mode
    return replace(base, **values)


def _text_hints(raw: Any, ctx: str, base: Style) -> Style:
    if raw is None:
        return base
    if not isinstance(raw, dict):
        raise DimensionError(f"{ctx} must be a mapping")
    _reject_unknown(raw, _TEXT_KEYS, ctx)
    values: dict[str, Any] = {}
    if "side" in raw:
        side = str(raw["side"])
        if side not in ("above", "below"):
            raise DimensionError(f"{ctx}.side must be 'above' or 'below', got {side!r}")
        values["text_side"] = side
    if "along_px" in raw:
        values["text_along_px"] = _number(raw["along_px"], f"{ctx}.along_px")
    if "rotate" in raw:
        rotate = str(raw["rotate"])
        if rotate not in ("auto", "none"):
            raise DimensionError(f"{ctx}.rotate must be 'auto' or 'none', got {rotate!r}")
        values["text_rotate"] = rotate
    if "leader" in raw:
        if not isinstance(raw["leader"], bool):
            raise DimensionError(f"{ctx}.leader must be a boolean")
        values["text_leader"] = raw["leader"]
    return replace(base, **values)


def _load_datums(raw: Any, ctx: str) -> list[Datum]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise DimensionError(f"{ctx}: datums must be a list")
    datums: list[Datum] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        where = f"{ctx}: datums[{index}]"
        if not isinstance(item, dict):
            raise DimensionError(f"{where} must be a mapping")
        _reject_unknown(item, _DATUM_KEYS, where)
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            raise DimensionError(f"{where}.name must be a non-empty string")
        name = name.strip()
        if name in seen:
            raise DimensionError(f"{where}.name: duplicate datum name {name!r}")
        seen.add(name)
        if "point" not in item:
            raise DimensionError(f"{where}.point is required ([E, N] in the layout CRS)")
        point = _coord(item["point"], f"{where}.point")
        source = item.get("source")
        if not isinstance(source, str) or not source.strip():
            raise DimensionError(
                f"{where}.source is required and must be non-empty: a datum is the only place a "
                "literal coordinate may enter a dimensions file, so it carries a citation"
            )
        status = str(item.get("status", "verify"))
        if status not in SOURCE_STATUSES:
            raise DimensionError(
                f"{where}.status must be one of {sorted(SOURCE_STATUSES)}, got {status!r}"
            )
        z = None if item.get("z") is None else _number(item["z"], f"{where}.z")
        datums.append(Datum(name=name, point=point, source=source.strip(), z=z, status=status))
    return datums


def _load_naming(
    raw: Any, ctx: str, datum_names: set[str]
) -> list[tuple[str, dict[str, Any]]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise DimensionError(f"{ctx}: naming must be a list")
    entries: list[tuple[str, dict[str, Any]]] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        where = f"{ctx}: naming[{index}]"
        if not isinstance(item, dict):
            raise DimensionError(f"{where} must be a mapping")
        _reject_unknown(item, _NAMING_KEYS, where)
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            raise DimensionError(f"{where}.name must be a non-empty string")
        name = name.strip()
        if name in seen or name in datum_names:
            raise DimensionError(
                f"{where}.name: {name!r} already names a naming entry or a datum — one key, "
                "one thing"
            )
        seen.add(name)
        where_clause = item.get("where")
        if not isinstance(where_clause, dict) or not where_clause:
            raise DimensionError(f"{where}.where must be a non-empty mapping")
        position = [key for key in _WHERE_POSITION_KEYS if key in where_clause]
        if position:
            raise DimensionError(
                f"{where}.where: position key(s) {', '.join(position)} are not allowed — a "
                "coordinate binding breaks the moment the thing moves, which is the opposite of "
                "what a computed dimension is for. Bind by 'type' and register 'properties', or "
                "set the 'tag' attribute in the editor layer"
            )
        _reject_unknown(where_clause, _WHERE_KEYS, f"{where}.where")
        if "type" in where_clause and not isinstance(where_clause["type"], str):
            raise DimensionError(f"{where}.where.type must be a string")
        properties = where_clause.get("properties", {})
        if not isinstance(properties, dict):
            raise DimensionError(f"{where}.where.properties must be a mapping")
        entries.append((name, dict(where_clause)))
    return entries


def _load_routes(raw: Any, ctx: str) -> list[Route]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise DimensionError(f"{ctx}: routes must be a list")
    routes: list[Route] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        where = f"{ctx}: routes[{index}]"
        if not isinstance(item, dict):
            raise DimensionError(f"{where} must be a mapping")
        _reject_unknown(item, _ROUTE_KEYS, where)
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            raise DimensionError(f"{where}.name must be a non-empty string")
        name = name.strip()
        if name in seen:
            raise DimensionError(f"{where}.name: duplicate route name {name!r}")
        seen.add(name)
        kind = item.get("kind")
        if not isinstance(kind, str) or not kind.strip():
            raise DimensionError(
                f"{where}.kind is required (pipe / cable / duct / …) so a quantities consumer "
                "can group without guessing"
            )
        points = item.get("points")
        if not isinstance(points, list) or len(points) < 2:
            raise DimensionError(f"{where}.points must be a list of at least 2 references")
        refs = tuple(
            _load_ref(entry, f"{where}.points[{position}]", allow_station=False)
            for position, entry in enumerate(points)
        )
        attributes = item.get("attributes", {})
        if not isinstance(attributes, dict):
            raise DimensionError(f"{where}.attributes must be a mapping")
        routes.append(
            Route(name=name, kind=kind.strip(), points=refs, attributes=dict(attributes))
        )
    return routes


def _load_ref(raw: Any, ctx: str, *, allow_station: bool = True) -> Ref:
    if not isinstance(raw, dict):
        raise DimensionError(f"{ctx} must be a mapping with exactly one selector key")
    present = [key for key in _REF_SELECTORS if key in raw]
    extra = sorted(str(key) for key in raw if str(key) not in _REF_SELECTORS)
    if extra:
        if "point" in extra:
            raise DimensionError(
                f"{ctx}: a reference may not carry a literal 'point' — declare it once under "
                "datums: with a source, then reference it as {datum: <name>}"
            )
        raise DimensionError(
            f"{ctx}: unknown reference key(s) {', '.join(extra)} "
            f"(allowed: {', '.join(_REF_SELECTORS)})"
        )
    if len(present) != 1:
        found = ", ".join(present) if present else "none"
        raise DimensionError(
            f"{ctx}: exactly one of {', '.join(_REF_SELECTORS)} is required, found {found}"
        )
    kind = present[0]
    if kind == "station" and not allow_station:
        raise DimensionError(f"{ctx}: a station reference is only valid inside a chainage from/to")
    if kind in ("placement", "datum"):
        key = raw[kind]
        if not isinstance(key, str) or not key.strip():
            raise DimensionError(f"{ctx}.{kind} must be a non-empty string key")
        return Ref(kind=kind, key=key.strip())  # type: ignore[arg-type]
    if kind == "feature":
        filters = raw["feature"]
        if not isinstance(filters, dict) or not filters:
            raise DimensionError(
                f"{ctx}.feature must be a mapping of "
                f"{{{', '.join(sorted(_FEATURE_FILTERS))}}} filters"
            )
        _reject_unknown(filters, _FEATURE_FILTERS, f"{ctx}.feature")
        cleaned: dict[str, str] = {}
        for name, value in filters.items():
            if not isinstance(value, str) or not value.strip():
                raise DimensionError(f"{ctx}.feature.{name} must be a non-empty string")
            cleaned[str(name)] = value.strip()
        return Ref(kind="feature", filters=cleaned)
    return _station_ref(raw["station"], ctx)


def _station_ref(raw: Any, ctx: str) -> Ref:
    if isinstance(raw, dict):
        inner = raw.get("project")
        if len(raw) != 1 or inner is None:
            raise DimensionError(
                f"{ctx}.station must be 'start', 'end', 'vertex:<n>', or {{project: <Ref>}}"
            )
        return Ref(kind="station", station="project", inner=_load_ref(
            inner, f"{ctx}.station.project", allow_station=False
        ))
    token = str(raw).strip()
    if token in ("start", "end"):
        return Ref(kind="station", station=token)
    if token.startswith("vertex:"):
        index = token.split(":", 1)[1]
        if not index.isdigit():
            raise DimensionError(
                f"{ctx}.station: 'vertex:<n>' needs a 0-based integer, got {token!r}"
            )
        return Ref(kind="station", station=token)
    if token.startswith("project:"):
        raise DimensionError(
            f"{ctx}.station: use the mapping form {{project: {{placement: <key>}}}} so the "
            "projected reference is itself validated"
        )
    raise DimensionError(
        f"{ctx}.station must be 'start', 'end', 'vertex:<n>' or {{project: <Ref>}}, got {token!r}"
    )


def _load_dimension_specs(raw: Any, defaults: Style, ctx: str) -> list[DimensionSpec]:
    if not isinstance(raw, list) or not raw:
        raise DimensionError(f"{ctx}: dimensions must be a non-empty list")
    specs: list[DimensionSpec] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        where = f"{ctx}: dimensions[{index}]"
        if not isinstance(item, dict):
            raise DimensionError(f"{where} must be a mapping")
        spec_id = item.get("id")
        if not isinstance(spec_id, str) or not spec_id.strip():
            raise DimensionError(f"{where}.id must be a non-empty string")
        spec_id = spec_id.strip()
        if spec_id in seen:
            raise DimensionError(f"{where}.id: duplicate dimension id {spec_id!r}")
        seen.add(spec_id)
        kind = item.get("kind")
        if kind not in DIMENSION_KINDS:
            raise DimensionError(
                f"{spec_id}.kind must be one of {list(DIMENSION_KINDS)}, got {kind!r}"
            )
        allowed = _DIMENSION_COMMON_KEYS | _PER_KIND_KEYS[kind]
        _reject_unknown(item, allowed, spec_id)
        for required in _REQUIRED_PER_KIND[kind]:
            if required not in item:
                raise DimensionError(f"{spec_id} ({kind}) requires {required!r}")

        style = _style_from(
            {key: item[key] for key in _STYLE_KEYS if key in item}, spec_id, defaults
        )
        style = _text_hints(item.get("text"), f"{spec_id}.text", style)
        layer = item.get("layer", style.layer)
        if not isinstance(layer, str) or not layer.strip():
            raise DimensionError(f"{spec_id}.layer must be a non-empty string")
        style = replace(style, layer=layer.strip())

        label = item.get("label", "")
        if not isinstance(label, str):
            raise DimensionError(f"{spec_id}.label must be a string")
        if _LABEL_NUMBER.search(label):
            raise DimensionError(
                f"{spec_id}.label: labels may not contain a decimal number ({label!r}). "
                "A dimension prints the COMPUTED value; declare a specified value as "
                "expect_m: <n> with expect_source, and it will be checked, not printed"
            )
        expect_m = None if item.get("expect_m") is None else _number(
            item["expect_m"], f"{spec_id}.expect_m"
        )
        expect_source = item.get("expect_source")
        if expect_m is not None:
            if not isinstance(expect_source, str) or not expect_source.strip():
                raise DimensionError(
                    f"{spec_id}.expect_source is required whenever expect_m is given: an "
                    "expectation with no citation cannot be reviewed"
                )
            expect_source = expect_source.strip()
        elif expect_source is not None:
            raise DimensionError(f"{spec_id}.expect_source is set but expect_m is not")
        severity = item.get("severity", "error")
        if severity not in ("error", "warn"):
            raise DimensionError(f"{spec_id}.severity must be 'error' or 'warn', got {severity!r}")

        axis = None
        if "axis" in item:
            axis = _axis(item["axis"], spec_id, kind)
        of = None
        if "of" in item:
            of = _selector(item["of"], f"{spec_id}.of")
        route = None
        if kind == "chainage":
            route = item.get("route")
            if not isinstance(route, str) or not route.strip():
                raise DimensionError(f"{spec_id}.route must be a non-empty route name")
            route = route.strip()

        from_ref = to_ref = None
        if kind == "chainage":
            from_ref = _load_ref(item["from"], f"{spec_id}.from") if "from" in item else Ref(
                kind="station", station="start"
            )
            to_ref = _load_ref(item["to"], f"{spec_id}.to") if "to" in item else Ref(
                kind="station", station="end"
            )
        elif kind in ("clearance", "centres", "offset"):
            from_ref = _load_ref(item["from"], f"{spec_id}.from", allow_station=False)
            to_ref = _load_ref(item["to"], f"{spec_id}.to", allow_station=False)

        specs.append(
            DimensionSpec(
                id=spec_id,
                kind=str(kind),
                style=style,
                from_ref=from_ref,
                to_ref=to_ref,
                of=of,
                axis=axis,
                route=route,
                label=label,
                expect_m=expect_m,
                expect_source=expect_source,
                severity=severity,  # type: ignore[arg-type]
            )
        )
    return specs


def _axis(raw: Any, spec_id: str, kind: str) -> str:
    token = str(raw).strip()
    if kind not in ("envelope", "offset"):
        raise DimensionError(
            f"{spec_id}: axis is only meaningful for envelope and offset, not {kind}"
        )
    if token == "direct":
        if kind != "offset":
            raise DimensionError(f"{spec_id}.axis 'direct' is only valid for kind: offset")
        return token
    if token in AXIS_WORDS:
        return token
    match = _BEARING_AXIS.match(token)
    if match is None:
        raise DimensionError(
            f"{spec_id}.axis must be one of {list(AXIS_WORDS)} or 'bearing:<deg>', got {token!r}"
        )
    _number(float(match.group(1)), f"{spec_id}.axis bearing")
    return token


def _selector(raw: Any, ctx: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise DimensionError(f"{ctx} must be a mapping with exactly one of {list(_SELECTOR_KEYS)}")
    present = [key for key in _SELECTOR_KEYS if key in raw]
    _reject_unknown(raw, _SELECTOR_KEYS, ctx)
    if len(present) != 1:
        found = ", ".join(present) if present else "none"
        raise DimensionError(
            f"{ctx}: exactly one of {', '.join(_SELECTOR_KEYS)} is required, found {found}"
        )
    key = present[0]
    value = raw[key]
    if key == "all":
        if value is not True:
            raise DimensionError(f"{ctx}.all must be true")
        return {"all": True}
    if key == "type":
        if not isinstance(value, str) or not value.strip():
            raise DimensionError(f"{ctx}.type must be a non-empty string")
        return {"type": value.strip()}
    if not isinstance(value, list) or not value:
        raise DimensionError(f"{ctx}.{key} must be a non-empty list")
    items = []
    for position, entry in enumerate(value):
        if not isinstance(entry, str) or not entry.strip():
            raise DimensionError(f"{ctx}.{key}[{position}] must be a non-empty string")
        items.append(entry.strip())
    return {key: items}


def _load_setting_out(
    raw: Any, defaults: Style, datums: Sequence[Datum], ctx: str
) -> SettingOutSpec | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise DimensionError(f"{ctx}: setting_out must be a mapping")
    _reject_unknown(raw, _SETTING_OUT_KEYS, f"{ctx}: setting_out")

    include = raw.get("include", ["placements"])
    if isinstance(include, str):
        include = [include]
    if not isinstance(include, list) or not include:
        raise DimensionError(f"{ctx}: setting_out.include must be a non-empty list")
    tokens: list[str] = []
    for position, entry in enumerate(include):
        token = str(entry)
        if token not in SETTING_OUT_INCLUDES:
            raise DimensionError(
                f"{ctx}: setting_out.include[{position}] must be one of "
                f"{list(SETTING_OUT_INCLUDES)}, got {token!r}"
            )
        if token in tokens:
            raise DimensionError(f"{ctx}: setting_out.include has duplicate {token!r}")
        tokens.append(token)

    of = _selector(raw.get("of", {"all": True}), f"{ctx}: setting_out.of")

    if "z" not in raw:
        raise DimensionError(
            f"{ctx}: setting_out: declare a z source — z: {{source: property, property: "
            "level_m}} | {source: datum, datum: P5} | {source: none} (the placements register "
            "carries no elevation, so there is no default that does not either invent a level "
            "or hide its absence)"
        )
    z_raw = raw["z"]
    if not isinstance(z_raw, dict):
        raise DimensionError(f"{ctx}: setting_out.z must be a mapping with a source: key")
    z_source = str(z_raw.get("source", ""))
    if z_source not in Z_SOURCES:
        raise DimensionError(
            f"{ctx}: setting_out.z.source must be one of {list(Z_SOURCES)}, got {z_source!r}"
        )
    _reject_unknown(z_raw, {"source", "property", "datum"}, f"{ctx}: setting_out.z")
    z_property = z_datum = None
    if z_source == "property":
        z_property = z_raw.get("property")
        if not isinstance(z_property, str) or not z_property.strip():
            raise DimensionError(
                f"{ctx}: setting_out.z source 'property' needs property: <register property name>"
            )
        z_property = z_property.strip()
    elif z_source == "datum":
        z_datum = z_raw.get("datum")
        if not isinstance(z_datum, str) or not z_datum.strip():
            raise DimensionError(f"{ctx}: setting_out.z source 'datum' needs datum: <datum name>")
        z_datum = z_datum.strip()
        match = next((d for d in datums if d.name == z_datum), None)
        if match is None:
            raise DimensionError(
                f"{ctx}: setting_out.z.datum {z_datum!r} is not declared under datums:"
            )
        if match.z is None:
            raise DimensionError(
                f"{ctx}: setting_out.z.datum {z_datum!r} carries no z: — a datum used as a level "
                "must declare one, with its source"
            )

    note = raw.get("note")
    if note is not None and (not isinstance(note, str) or not note.strip()):
        raise DimensionError(f"{ctx}: setting_out.note must be a non-empty string when present")
    if z_source == "none" and note is None:
        raise DimensionError(
            f"{ctx}: setting_out.note is required when z.source is 'none': the table prints "
            f"{NOT_SURVEYED!r} in every Z cell, and a reader is owed the reason on the face of "
            "the drawing"
        )
    vertical_datum = raw.get("vertical_datum")
    if vertical_datum is not None and (
        not isinstance(vertical_datum, str) or not vertical_datum.strip()
    ):
        raise DimensionError(f"{ctx}: setting_out.vertical_datum must be a non-empty string")
    if z_source != "none" and vertical_datum is None:
        raise DimensionError(
            f"{ctx}: setting_out.vertical_datum is required when a level is printed "
            '(e.g. "MSL (m)") — a level with no stated datum is not a level'
        )
    order = str(raw.get("order", "id"))
    if order not in SETTING_OUT_ORDERS:
        raise DimensionError(
            f"{ctx}: setting_out.order must be one of {list(SETTING_OUT_ORDERS)}, got {order!r}"
        )
    decimals = (
        defaults.decimals
        if "decimals" not in raw
        else _decimals(raw["decimals"], f"{ctx}: setting_out.decimals")
    )
    return SettingOutSpec(
        include=tuple(tokens),
        of=of,
        z_source=z_source,  # type: ignore[arg-type]
        z_property=z_property,
        z_datum=z_datum,
        note=None if note is None else note.strip(),
        vertical_datum=None if vertical_datum is None else vertical_datum.strip(),
        order=order,  # type: ignore[arg-type]
        decimals=decimals,
    )


def _reject_duplicate_route_refs(
    dimensions: Sequence[DimensionSpec], routes: Sequence[Route], ctx: str
) -> None:
    names = {route.name for route in routes}
    for spec in dimensions:
        if spec.route is not None and spec.route not in names:
            known = ", ".join(sorted(names)) or "none declared"
            raise DimensionError(
                f"{ctx}: {spec.id}.route {spec.route!r} is not declared under routes: "
                f"(known: {known})"
            )


def _load_chains(raw: Any, dimensions: Sequence[DimensionSpec], ctx: str) -> list[Chain]:
    """Load and fully validate ``chains:``. Every defect is a hard error naming the path.

    Everything a chain can get wrong is decidable here, from the file alone, so nothing is
    deferred to :func:`check_chains`: an unknown id, a truncated run, an id claimed by two
    chains, a chain compared across two precisions. A chain that loads is a chain that can
    be checked, and a chain that cannot be checked never loads.
    """

    if raw is None:
        return []
    if not isinstance(raw, list) or not raw:
        raise DimensionError(
            f"{ctx}: chains must be a non-empty list — omit the key entirely if there is no "
            "run to check"
        )
    by_id = {spec.id: spec for spec in dimensions}
    known = ", ".join(sorted(by_id)) or "none"
    chains: list[Chain] = []
    seen_ids: set[str] = set()
    claimed: dict[str, str] = {}
    for index, item in enumerate(raw):
        where = f"{ctx}: chains[{index}]"
        if not isinstance(item, dict):
            raise DimensionError(f"{where} must be a mapping")
        _reject_unknown(item, _CHAIN_KEYS, where)
        chain_id = item.get("id")
        if not isinstance(chain_id, str) or not chain_id.strip():
            raise DimensionError(f"{where}.id must be a non-empty string")
        chain_id = chain_id.strip()
        if chain_id in seen_ids:
            raise DimensionError(f"{where}.id: duplicate chain id {chain_id!r}")
        seen_ids.add(chain_id)

        overall = item.get("overall")
        if not isinstance(overall, str) or not overall.strip():
            raise DimensionError(
                f"{where}.overall must be the id of the one dimension that spans the whole run"
            )
        overall = overall.strip()
        if overall not in by_id:
            raise DimensionError(
                f"{where} ({chain_id}).overall {overall!r} is not a declared dimension id "
                f"(known: {known})"
            )

        components = item.get("components")
        if not isinstance(components, list):
            raise DimensionError(
                f"{where} ({chain_id}).components must be a list of dimension ids, in the order "
                "a reader adds them up"
            )
        if len(components) < _MIN_CHAIN_COMPONENTS:
            raise DimensionError(
                f"{where} ({chain_id}).components lists {len(components)} id(s), needs at least "
                f"{_MIN_CHAIN_COMPONENTS}: a one-component chain is not a chain, and accepting it "
                "would report clean while checking nothing"
            )
        names: list[str] = []
        for position, entry in enumerate(components):
            at = f"{where} ({chain_id}).components[{position}]"
            if not isinstance(entry, str) or not entry.strip():
                raise DimensionError(f"{at} must be a non-empty dimension id")
            name = entry.strip()
            if name not in by_id:
                raise DimensionError(
                    f"{at}: {name!r} is not a declared dimension id (known: {known})"
                )
            if name == overall:
                raise DimensionError(
                    f"{at}: {name!r} is also chain {chain_id!r}'s overall — a run cannot be one of "
                    "its own parts, and the check would then be trivially satisfied"
                )
            if name in names:
                raise DimensionError(
                    f"{at}: {name!r} already appears in chain {chain_id!r}'s components — one "
                    "dimension measures one length once, so a repeat is a copy-paste, not a bay"
                )
            owner = claimed.get(name)
            if owner is not None:
                raise DimensionError(
                    f"{at}: {name!r} is already a component of chain {owner!r} — a dimension "
                    "belongs to one run, and two claims mean one of the declarations is wrong"
                )
            names.append(name)
            claimed[name] = chain_id

        severity = item.get("severity", "error")
        if severity not in ("error", "warn"):
            raise DimensionError(
                f"{where} ({chain_id}).severity must be 'error' or 'warn', got {severity!r}"
            )
        note = item.get("note", "")
        if not isinstance(note, str):
            raise DimensionError(f"{where} ({chain_id}).note must be a string")

        _reject_mixed_decimals(chain_id, overall, names, by_id, where)
        chains.append(
            Chain(
                id=chain_id,
                overall=overall,
                components=tuple(names),
                severity=severity,  # type: ignore[arg-type]
                note=note.strip(),
            )
        )
    return chains


def _reject_mixed_decimals(
    chain_id: str,
    overall: str,
    components: Sequence[str],
    by_id: Mapping[str, DimensionSpec],
    where: str,
) -> None:
    """One precision per chain. Comparing a 3 dp sum against a 2 dp total is meaningless."""

    decimals = by_id[overall].style.decimals
    odd = sorted(
        {name for name in components if by_id[name].style.decimals != decimals}
    )
    if odd:
        detail = ", ".join(f"{name} at {by_id[name].style.decimals} dp" for name in odd)
        raise DimensionError(
            f"{where} ({chain_id}): mixed decimals — overall {overall!r} displays at "
            f"{decimals} dp but {detail}. A chain is compared at ONE displayed precision; "
            "comparing across two says nothing about either"
        )


def placement_key(placement: Placement) -> str:
    """The durable handle for a placement: P10's ``identity`` (``id`` then ``tag``).

    Raises when a placement carries neither. There is deliberately no ordinal, index,
    "nth of type" or nearest-match fallback: register order is a canonical sort over
    the *pose*, so an ordinal reference would silently re-point at different equipment
    the moment something moved.
    """

    identity = placement.identity
    if identity is None:
        raise DimensionError(
            f"{placement.type} at ({placement.origin[0]:.3f}, {placement.origin[1]:.3f}) "
            f"@ {placement.rotation_deg:.3f}° has neither a stable id nor a tag, so it has no "
            "durable handle to dimension or to set out"
        )
    return identity


def effective_layout(dset: DimensionSet, layout: Layout) -> tuple[Layout, list[str]]:
    """Apply the declared snap policy.

    Measuring poses the sheet does not draw is not expressible.
    """

    if dset.snap == "as-placed":
        return layout, []
    return snap_groups(layout)


def resolve(dset: DimensionSet, layout: Layout) -> Resolver:
    """Build the key -> Placement index plus the datum and route indexes.

    Raises on a duplicate key, a ``naming`` predicate that matches zero or several
    placements, or a datum name that collides with a placement key.
    """

    snapped, report = effective_layout(dset, layout)
    index: dict[str, Placement] = {}
    for placement in snapped.placements:
        identity = placement.identity
        if identity is None:
            continue
        existing = index.get(identity)
        if existing is not None:
            raise DimensionError(
                f"duplicate placement key {identity!r}: {existing.type} at "
                f"({existing.origin[0]:.3f}, {existing.origin[1]:.3f}) and {placement.type} at "
                f"({placement.origin[0]:.3f}, {placement.origin[1]:.3f}) both claim it — every "
                "reference to that key would be ambiguous"
            )
        index[identity] = placement

    for name, where in dset.naming:
        matches = [p for p in snapped.placements if _matches_where(p, where)]
        if len(matches) != 1:
            raise DimensionError(
                f"naming {name!r}: predicate {where!r} matched {len(matches)} placement(s), "
                f"expected exactly 1. Candidates: {_describe_candidates(snapped.placements, where)}"
            )
        if name in index:
            raise DimensionError(
                f"naming {name!r} collides with an existing placement id or tag — one key, "
                "one thing"
            )
        index[name] = matches[0]

    datums = {datum.name: datum for datum in dset.datums}
    collisions = sorted(set(datums) & set(index))
    if collisions:
        raise DimensionError(
            f"datum name(s) {', '.join(collisions)} collide with a placement key — a reference "
            "would be ambiguous about whether it means the survey point or the equipment"
        )
    return Resolver(
        layout=snapped,
        placements=index,
        datums=datums,
        routes={route.name: route for route in dset.routes},
        snap=dset.snap,
        snap_report=tuple(report),
    )


def _matches_where(placement: Placement, where: Mapping[str, Any]) -> bool:
    if "type" in where and placement.type != where["type"]:
        return False
    for key, value in (where.get("properties") or {}).items():
        if placement.properties.get(key) != value:
            return False
    return True


def _describe_candidates(placements: Sequence[Placement], where: Mapping[str, Any]) -> str:
    wanted = where.get("type")
    pool = [p for p in placements if wanted is None or p.type == wanted]
    if not pool:
        return "none of that type"
    return "; ".join(
        f"{p.type} properties="
        f"{{{', '.join(f'{k}: {p.properties[k]!r}' for k in sorted(p.properties))}}}"
        for p in pool[:6]
    )


def _unkeyed_message(layout: Layout, placement: Placement, known: Sequence[str]) -> str:
    unkeyed = sum(1 for p in layout.placements if not p.has_durable_id)
    return (
        f"{placement.type} at ({placement.origin[0]:.3f}, {placement.origin[1]:.3f}) has neither "
        f"a stable id nor a tag. Known keys: {', '.join(known) or 'none'}. "
        f"{unkeyed} of {len(layout.placements)} placements cannot be dimensioned or set out. "
        "Fix by (a) setting the 'tag' or 'id' attribute in the editor placements layer and "
        "re-running `technical_drawings_for_agents layout --from-geojson`, or (b) declaring a naming: entry with "
        "a property predicate"
    )


def _placement_for(ref: Ref, resolver: Resolver, spec_id: str) -> Placement:
    assert ref.key is not None
    placement = resolver.placements.get(ref.key)
    if placement is None:
        unkeyed = sum(1 for p in resolver.layout.placements if not p.has_durable_id)
        raise DimensionError(
            f"{spec_id}: no placement keyed {ref.key!r}. "
            f"Known keys: {', '.join(resolver.known_keys) or 'none'}. "
            f"{unkeyed} of {len(resolver.layout.placements)} placements have neither a stable id "
            "nor a tag and cannot be dimensioned. Fix by (a) setting the 'tag' attribute in the "
            "QGIS placements layer and re-running `technical_drawings_for_agents layout --from-geojson`, or "
            "(b) declaring a naming: entry with a property predicate"
        )
    return placement


def _datum_for(ref: Ref, resolver: Resolver, spec_id: str) -> Datum:
    assert ref.key is not None
    datum = resolver.datums.get(ref.key)
    if datum is None:
        raise DimensionError(
            f"{spec_id}: no datum named {ref.key!r}. "
            f"Declared datums: {', '.join(sorted(resolver.datums)) or 'none'}"
        )
    return datum


def _feature_for(ref: Ref, resolver: Resolver, spec_id: str) -> PlacedFeature:
    filters = ref.filters
    key = filters.get("placement")
    matches: list[PlacedFeature] = []
    for placement, feature in resolver.placed_features():
        if key is not None and placement.identity != key:
            continue
        if "role" in filters and feature.role != filters["role"]:
            continue
        if "tag" in filters and feature.tag != filters["tag"]:
            continue
        if "component" in filters and feature.component != filters["component"]:
            continue
        matches.append(feature)
    if len(matches) != 1:
        summary = "; ".join(
            f"role={f.role!r} tag={f.tag!r} component={f.component!r}" for f in matches[:8]
        )
        raise DimensionError(
            f"{spec_id}: {ref.describe()} selected {len(matches)} placed feature(s), expected "
            f"exactly 1. Matches: {summary or 'none'}"
        )
    return matches[0]


def _ref_point(ref: Ref, resolver: Resolver, spec_id: str) -> Point:
    if ref.kind == "placement":
        return _placement_for(ref, resolver, spec_id).origin
    if ref.kind == "datum":
        return _datum_for(ref, resolver, spec_id).point
    if ref.kind == "feature":
        return _feature_point(_feature_for(ref, resolver, spec_id), spec_id, ref)
    raise DimensionError(f"{spec_id}: {ref.describe()} does not resolve to a point")


def _feature_point(feature: PlacedFeature, spec_id: str, ref: Ref) -> Point:
    coords = feature.coords
    if isinstance(coords, tuple):
        return coords
    if not coords:
        raise DimensionError(f"{spec_id}: {ref.describe()} has no coordinates")
    return (
        sum(point[0] for point in coords) / len(coords),
        sum(point[1] for point in coords) / len(coords),
    )


def _ref_footprint(ref: Ref, resolver: Resolver, spec_id: str) -> tuple[list[Point], str]:
    if ref.kind == "placement":
        placement = _placement_for(ref, resolver, spec_id)
        return _footprint_of(placement, spec_id), placement.label
    if ref.kind == "feature":
        feature = _feature_for(ref, resolver, spec_id)
        coords = feature.coords
        if isinstance(coords, tuple) or len(coords) < 3:
            raise DimensionError(
                f"{spec_id}: {ref.describe()} is not a polygon, so it has no footprint to "
                "measure a clear gap from"
            )
        return list(coords), feature.tag or feature.role
    raise DimensionError(
        f"{spec_id}: {ref.describe()} has no footprint — a clearance needs two placed "
        "footprints (a datum is a point)"
    )


def _footprint_of(placement: Placement, spec_id: str) -> list[Point]:
    try:
        return footprint(placement)
    except LayoutError as exc:
        raise DimensionError(f"{spec_id}: {exc}") from exc


# --------------------------------------------------------------------------- #
# measurement
# --------------------------------------------------------------------------- #


def measure(dset: DimensionSet, layout: Layout) -> list[Measurement]:
    """Compute every dimension, in declaration order.

    Raises ``DimensionError`` on a reference that cannot be resolved or a geometry
    that cannot be dimensioned. Nothing renders blank, nothing renders ``0.000`` as a
    stand-in for "unknown", and nothing quietly drops itself from the sheet.
    """

    resolver = resolve(dset, layout)
    return [_measure_one(spec, dset, resolver) for spec in dset.dimensions]


def _measure_one(spec: DimensionSpec, dset: DimensionSet, resolver: Resolver) -> Measurement:
    if spec.kind == "clearance":
        measurement = _measure_clearance(spec, resolver)
    elif spec.kind == "centres":
        measurement = _measure_centres(spec, resolver)
    elif spec.kind == "envelope":
        measurement = _measure_envelope(spec, resolver)
    elif spec.kind == "offset":
        measurement = _measure_offset(spec, resolver)
    elif spec.kind == "chainage":
        measurement = _measure_chainage(spec, resolver)
    else:  # pragma: no cover - guarded by _load_dimension_specs
        raise DimensionError(f"{spec.id}: unhandled kind {spec.kind!r}")

    if not math.isfinite(measurement.value_m):
        raise DimensionError(
            f"{spec.id}: computed a non-finite value ({measurement.value_m}) — a NaN would "
            "format as 'nan m' and print on a sheet"
        )
    provenance = {
        "dimension_set": dset.id,
        "layout": str(dset.layout_path),
        "snap": dset.snap,
        "kind": spec.kind,
        **measurement.provenance,
    }
    return replace(measurement, provenance=provenance)


def _refs_summary(spec: DimensionSpec) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    if spec.from_ref is not None:
        summary["from"] = spec.from_ref.describe()
    if spec.to_ref is not None:
        summary["to"] = spec.to_ref.describe()
    if spec.of is not None:
        summary["of"] = spec.of
    if spec.route is not None:
        summary["route"] = spec.route
    if spec.axis is not None:
        summary["axis"] = spec.axis
    return summary


def _measure_clearance(spec: DimensionSpec, resolver: Resolver) -> Measurement:
    assert spec.from_ref is not None and spec.to_ref is not None
    first, first_label = _ref_footprint(spec.from_ref, resolver, spec.id)
    second, second_label = _ref_footprint(spec.to_ref, resolver, spec.id)
    if _rects_overlap(first, second):
        raise DimensionError(
            f"{spec.id}: {first_label} and {second_label} overlap — there is no clear gap to "
            "dimension. `polygon_gap` collapses interpenetration and contact to the same value, "
            "so a zero clearance here would state that they touch when they do not. Fix the "
            "layout (the no-overlap check should have caught this)"
        )
    # The scalar is polygon_gap's return value, full stop: the same function object
    # `_check_clear_spacing` calls, so a sheet cannot contradict its own check.
    value = polygon_gap(first, second)
    anchor_a, anchor_b, mode = _clearance_witness(first, second, value)
    return Measurement(
        spec=spec,
        value_m=value,
        anchor_a=anchor_a,
        anchor_b=anchor_b,
        text=_measurement_text(spec, value, ""),
        witness_mode=mode,
        provenance={"refs": _refs_summary(spec), "labels": [first_label, second_label]},
    )


def _measure_centres(spec: DimensionSpec, resolver: Resolver) -> Measurement:
    assert spec.from_ref is not None and spec.to_ref is not None
    a = _ref_point(spec.from_ref, resolver, spec.id)
    b = _ref_point(spec.to_ref, resolver, spec.id)
    value = math.dist(a, b)
    coincident = value < ZERO_EPS_M
    if coincident and spec.style.on_zero == "error":
        raise DimensionError(
            f"{spec.id}: {spec.from_ref.describe()} and {spec.to_ref.describe()} share one "
            f"origin ({a[0]:.3f}, {a[1]:.3f}), so there is no centre-to-centre distance to "
            "dimension. Two placements at the same origin is a data defect; if the coincidence "
            "is deliberate, declare on_zero: leader so it is stated rather than blanked"
        )
    suffix = " (coincident)" if coincident else ""
    return Measurement(
        spec=spec,
        value_m=value,
        anchor_a=a,
        anchor_b=b,
        text=_measurement_text(spec, value, "") + suffix,
        witness_mode="point",
        provenance={"refs": _refs_summary(spec)},
    )


def _measure_envelope(spec: DimensionSpec, resolver: Resolver) -> Measurement:
    assert spec.of is not None and spec.axis is not None
    selected = _select_placements(spec.of, resolver, spec.id)
    unit = _axis_unit(spec.axis, spec, resolver, selected, None)
    corners: list[Point] = []
    for placement in selected:
        corners.extend(_footprint_of(placement, spec.id))
    lo, hi = _project(corners, unit)
    value = hi - lo
    centroid = (
        sum(point[0] for point in corners) / len(corners),
        sum(point[1] for point in corners) / len(corners),
    )
    base = centroid[0] * unit[0] + centroid[1] * unit[1]
    anchor_a = (centroid[0] + (lo - base) * unit[0], centroid[1] + (lo - base) * unit[1])
    anchor_b = (centroid[0] + (hi - base) * unit[0], centroid[1] + (hi - base) * unit[1])
    return Measurement(
        spec=spec,
        value_m=value,
        anchor_a=anchor_a,
        anchor_b=anchor_b,
        text=_measurement_text(spec, value, ""),
        witness_mode="axis",
        provenance={
            "refs": _refs_summary(spec),
            "members": [placement.label for placement in selected],
            "axis_unit": [unit[0], unit[1]],
        },
    )


def _measure_offset(spec: DimensionSpec, resolver: Resolver) -> Measurement:
    assert spec.from_ref is not None and spec.to_ref is not None and spec.axis is not None
    a = _ref_point(spec.from_ref, resolver, spec.id)
    b = _ref_point(spec.to_ref, resolver, spec.id)
    if spec.axis == "direct":
        value = math.dist(a, b)
        token = ""
        anchor_b = b
    else:
        unit = _axis_unit(spec.axis, spec, resolver, [], (a, b))
        value = (b[0] - a[0]) * unit[0] + (b[1] - a[1]) * unit[1]
        token = _direction_token(spec.axis, value)
        # The drawn line IS the measured component, never the hypotenuse.
        anchor_b = (a[0] + value * unit[0], a[1] + value * unit[1])
    if abs(value) < ZERO_EPS_M and spec.style.on_zero == "error":
        raise DimensionError(
            f"{spec.id}: the {spec.axis} component of {spec.from_ref.describe()} -> "
            f"{spec.to_ref.describe()} is zero, so no dimension line can be drawn. A zero "
            "component is often a legitimate fact — declare on_zero: leader to state it as a "
            "leader note instead of blanking it"
        )
    return Measurement(
        spec=spec,
        value_m=value,
        anchor_a=a,
        anchor_b=anchor_b,
        text=_measurement_text(spec, value, token),
        direction_token=token,
        witness_mode="axis" if spec.axis != "direct" else "point",
        provenance={"refs": _refs_summary(spec)},
    )


def _measure_chainage(spec: DimensionSpec, resolver: Resolver) -> Measurement:
    assert spec.route is not None
    route = resolver.routes[spec.route]
    points = _route_points(route, resolver, spec.id)
    start = _station(spec.from_ref, points, resolver, spec.id, default="start")
    end = _station(spec.to_ref, points, resolver, spec.id, default="end")
    if (start[0], start[1]) > (end[0], end[1]):
        raise DimensionError(
            f"{spec.id}: chainage stations are out of order along route {route.name!r} — "
            "'from' lies beyond 'to'. A negative chainage is almost always a reversed "
            "reference, and silently taking abs() would hide it"
        )
    path = _sub_path(points, start, end)
    value = sum(math.dist(a, b) for a, b in zip(path, path[1:]))
    return Measurement(
        spec=spec,
        value_m=value,
        anchor_a=path[0],
        anchor_b=path[-1],
        text=_measurement_text(spec, value, ""),
        path=tuple(path),
        witness_mode="path",
        provenance={
            "refs": _refs_summary(spec),
            "route": {"name": route.name, "kind": route.kind, "attributes": route.attributes},
        },
    )


def _route_points(route: Route, resolver: Resolver, spec_id: str) -> list[Point]:
    points = [_ref_point(ref, resolver, spec_id) for ref in route.points]
    for index, (a, b) in enumerate(zip(points, points[1:])):
        if math.dist(a, b) <= 1e-6:
            raise DimensionError(
                f"{spec_id}: route {route.name!r} has a repeated vertex at index {index + 1} "
                f"({b[0]:.3f}, {b[1]:.3f}) — a zero-length segment makes chainage ambiguous "
                "and station projection undefined"
            )
    return points


def project_onto_route(query: Point, points: Sequence[Point]) -> tuple[int, float, Point]:
    """Nearest point on a polyline: ``(segment_index, t, point)`` with ``t`` clamped to [0, 1].

    Pinned by test to agree with ``layout._point_segment_distance``, which computes the
    same projection parameter internally but does not return it.
    """

    if len(points) < 2:
        raise DimensionError("a route needs at least two points to project onto")
    best: tuple[float, int, float, Point] | None = None
    for index, (start, end) in enumerate(zip(points, points[1:])):
        dx, dy = end[0] - start[0], end[1] - start[1]
        length_sq = dx * dx + dy * dy
        if length_sq == 0:
            t = 0.0
        else:
            t = max(
                0.0,
                min(
                    1.0,
                    ((query[0] - start[0]) * dx + (query[1] - start[1]) * dy) / length_sq,
                ),
            )
        point = (start[0] + t * dx, start[1] + t * dy)
        distance = math.dist(query, point)
        if best is None or distance < best[0]:
            best = (distance, index, t, point)
    assert best is not None
    return best[1], best[2], best[3]


def _station(
    ref: Ref | None, points: Sequence[Point], resolver: Resolver, spec_id: str, *, default: str
) -> tuple[int, float, Point]:
    if ref is None:
        ref = Ref(kind="station", station=default)
    if ref.kind != "station":
        return project_onto_route(_ref_point(ref, resolver, spec_id), points)
    token = ref.station or default
    if token == "start":
        return 0, 0.0, points[0]
    if token == "end":
        return len(points) - 2, 1.0, points[-1]
    if token == "project":
        assert ref.inner is not None
        return project_onto_route(_ref_point(ref.inner, resolver, spec_id), points)
    index = int(token.split(":", 1)[1])
    if not 0 <= index < len(points):
        raise DimensionError(
            f"{spec_id}: station {token!r} is out of range — the route has "
            f"{len(points)} vertices "
            f"(0..{len(points) - 1})"
        )
    if index == len(points) - 1:
        return len(points) - 2, 1.0, points[-1]
    return index, 0.0, points[index]


def _sub_path(
    points: Sequence[Point], start: tuple[int, float, Point], end: tuple[int, float, Point]
) -> list[Point]:
    path = [start[2]]
    for index in range(start[0] + 1, end[0] + 1):
        path.append(points[index])
    path.append(end[2])
    # A station landing exactly on a vertex would otherwise repeat it.
    deduped = [path[0]]
    for point in path[1:]:
        if math.dist(point, deduped[-1]) > 0.0:
            deduped.append(point)
    return deduped if len(deduped) >= 2 else [path[0], path[-1]]


def _select_placements(
    selector: Mapping[str, Any], resolver: Resolver, ctx: str
) -> list[Placement]:
    placements = resolver.layout.placements
    if "all" in selector:
        selected = list(placements)
    elif "type" in selector:
        _validate_types([selector["type"]], resolver.layout, ctx)
        selected = [p for p in placements if p.type == selector["type"]]
    elif "types" in selector:
        _validate_types(selector["types"], resolver.layout, ctx)
        wanted = set(selector["types"])
        selected = [p for p in placements if p.type in wanted]
    else:
        keys = selector["placements"]
        selected = [
            _placement_for(Ref(kind="placement", key=key), resolver, ctx) for key in keys
        ]
    if not selected:
        raise DimensionError(
            f"{ctx}: selector {dict(selector)!r} matched no placements. An empty extent is not "
            "0.000 — it is a selector that matched nothing, i.e. a typo"
        )
    return selected


def _validate_types(names: Sequence[Any], layout: Layout, ctx: str) -> None:
    unknown = sorted({str(name) for name in names} - set(layout.types))
    if unknown:
        raise DimensionError(
            f"{ctx}: unknown placement type(s) {', '.join(unknown)} "
            f"(known: {', '.join(sorted(layout.types))})"
        )


def _axis_unit(
    axis: str,
    spec: DimensionSpec,
    resolver: Resolver,
    selected: Sequence[Placement],
    points: tuple[Point, Point] | None,
) -> Point:
    if axis == "easting":
        return (1.0, 0.0)
    if axis == "northing":
        return (0.0, 1.0)
    match = _BEARING_AXIS.match(axis)
    if match is not None:
        return _unit_from_bearing(float(match.group(1)))
    if axis in ("principal", "principal-cross"):
        if not selected:
            raise DimensionError(
                f"{spec.id}.axis {axis!r} reads the bearing from the selection, so it needs one "
                "— use easting / northing / bearing:<deg> for a two-point offset"
            )
        bearing = _principal_bearing(selected, spec)
        if axis == "principal-cross":
            bearing = (bearing + 90.0) % 180.0
        return _unit_from_bearing(bearing)
    if axis == "direct":
        if points is None:
            raise DimensionError(f"{spec.id}.axis 'direct' needs a from/to pair")
        a, b = points
        length = math.dist(a, b)
        if length == 0:
            raise DimensionError(f"{spec.id}.axis 'direct': the two points are coincident")
        return ((b[0] - a[0]) / length, (b[1] - a[1]) / length)
    raise DimensionError(f"{spec.id}.axis {axis!r} is not a known axis")  # pragma: no cover


def _principal_bearing(selected: Sequence[Placement], spec: DimensionSpec) -> float:
    bearings = [placement.rotation_deg % 180.0 for placement in selected]
    common = _mean_bearing(bearings)
    worst = max(
        ((_angle_delta(bearing, common), bearing) for bearing in bearings), key=lambda item: item[0]
    )
    if worst[0] > spec.style.parallel_tol_deg:
        raise DimensionError(
            f"{spec.id}.axis {spec.axis!r}: the selection is not parallel — {worst[1]:.3f}° is "
            f"{worst[0]:.3f}° off the group mean {common:.3f}°, tol "
            f"{spec.style.parallel_tol_deg:g}°. 'The group's own bearing' is undefined for a "
            "non-parallel group, and a mean would produce a foreshortened extent that looks "
            "plausible"
        )
    return common


def _unit_from_bearing(bearing_deg: float) -> Point:
    angle = math.radians(bearing_deg)
    return (math.cos(angle), math.sin(angle))


def _direction_token(axis: str, value: float) -> str:
    if axis == "easting":
        return "W" if value < 0 else "E"
    if axis == "northing":
        return "S" if value < 0 else "N"
    return ""


# --------------------------------------------------------------------------- #
# formatting
# --------------------------------------------------------------------------- #


def format_value(
    value_m: float,
    *,
    decimals: int,
    units_suffix: str = " m",
    direction_token: str = "",
) -> str:
    """Fixed-point formatting — the ONLY permitted transform between value and text.

    Delegates to P3's :func:`technical_drawings_for_agents.provenance.fmt`, which is the package's one
    float-to-text emit path. That is round-half-**even** on the exact binary value,
    *not* ``Decimal(...).quantize(..., ROUND_HALF_UP)``: the two differ at ties (0.0625
    -> 0.062 here, 0.063 there), and P3 chose half-even deliberately because ties at mm
    precision are odd multiples of 1/16 m, which occur in real work. Two rounding paths
    in one package is exactly the divergence this module exists to prevent, so there is
    only ever one.

    A compass token renders the sign for an axis-signed offset: ``-6.039`` prints as
    ``6.039 m W``.
    """

    try:
        text = canonical_fmt(abs(value_m) if direction_token else value_m, decimals)
    except ProvenanceError as exc:
        raise DimensionError(f"cannot format {value_m!r}: {exc}") from exc
    text = f"{text}{units_suffix}"
    return f"{text} {direction_token}" if direction_token else text


def displayed_value(spec: DimensionSpec, value_m: float) -> tuple[str, float]:
    """The number P6 prints for ``spec``, as text and parsed back to a float.

    Goes through :func:`format_value` — the same call ``_measurement_text`` makes, minus
    the label and the units suffix — so the checked number cannot drift from the printed
    one. The suffix is dropped rather than stripped afterwards, and an axis-signed offset
    keeps P6's compass convention (``-6.039`` prints ``6.039 m W``, so the displayed
    number is ``6.039``): the reader adds up what is on the sheet.
    """

    token = _direction_token(spec.axis, value_m) if spec.kind == "offset" and spec.axis else ""
    text = format_value(
        value_m, decimals=spec.style.decimals, units_suffix="", direction_token=token
    )
    number = text.split(" ", 1)[0]
    return number, float(number)


def _measurement_text(spec: DimensionSpec, value: float, token: str) -> str:
    body = format_value(
        value,
        decimals=spec.style.decimals,
        units_suffix=spec.style.units_suffix,
        direction_token=token,
    )
    return f"{spec.label} {body}" if spec.label else body


# --------------------------------------------------------------------------- #
# the witness pair (§4.3)
# --------------------------------------------------------------------------- #


def _clearance_witness(
    a: list[Point], b: list[Point], value_m: float
) -> tuple[Point, Point, Literal["edge", "vertex"]]:
    """Two points spanning exactly ``value_m``, chosen deterministically.

    ``polygon_gap`` returns a scalar and for two parallel rectangles the
    minimum-distance pair is non-unique, so a naive argmin lands the drawn line at
    whichever corner the loop happened to visit first — and moves it when the input
    order changes. Two branches, both fully determined by the data.
    """

    axis = _max_separation_axis(a, b)
    if axis is not None and abs(axis[1] - value_m) <= WITNESS_TOL_M:
        return (*_edge_witness(a, b, axis[0]), "edge")
    return (*_vertex_witness(a, b, value_m), "vertex")


def _max_separation_axis(a: list[Point], b: list[Point]) -> tuple[Point, float] | None:
    """Largest projection separation over the 8 edge normals — the same axis set
    ``_rects_overlap`` tests. Oriented so it points from ``a`` towards ``b``."""

    best: tuple[float, Point] | None = None
    for polygon in (a, b):
        for index in range(len(polygon)):
            x1, y1 = polygon[index]
            x2, y2 = polygon[(index + 1) % len(polygon)]
            norm = math.hypot(x2 - x1, y2 - y1)
            if norm == 0:
                continue
            axis = (-(y2 - y1) / norm, (x2 - x1) / norm)
            a_min, a_max = _project(a, axis)
            b_min, b_max = _project(b, axis)
            forward, reverse = b_min - a_max, a_min - b_max
            if forward >= reverse:
                separation, oriented = forward, axis
            else:
                separation, oriented = reverse, (-axis[0], -axis[1])
            if best is None or separation > best[0]:
                best = (separation, oriented)
    return None if best is None else (best[1], best[0])


def _edge_witness(a: list[Point], b: list[Point], axis: Point) -> tuple[Point, Point]:
    """Both anchors at the midpoint of the facing overlap — where a drafter would put them.

    Stable under input order and under vertex relabelling, and it centres the dimension
    in the gap instead of parking it at an arbitrary end.
    """

    cross = (-axis[1], axis[0])
    a_far = max(point[0] * axis[0] + point[1] * axis[1] for point in a)
    b_near = min(point[0] * axis[0] + point[1] * axis[1] for point in b)
    a_lo, a_hi = _project(a, cross)
    b_lo, b_hi = _project(b, cross)
    middle = (max(a_lo, b_lo) + min(a_hi, b_hi)) / 2.0
    return (
        (a_far * axis[0] + middle * cross[0], a_far * axis[1] + middle * cross[1]),
        (b_near * axis[0] + middle * cross[0], b_near * axis[1] + middle * cross[1]),
    )


def _vertex_witness(a: list[Point], b: list[Point], value_m: float) -> tuple[Point, Point]:
    """The first pair within ``VERTEX_MATCH_TOL_M``, enumerated exactly as ``_polygon_gap`` is.

    Tie-break is therefore ``(polygon_order, edge_index, point_index)`` — determined by
    the data, never by float noise.
    """

    for polygon, other, first_is_a in ((a, b, True), (b, a, False)):
        for index in range(len(polygon)):
            start = polygon[index]
            end = polygon[(index + 1) % len(polygon)]
            for point in other:
                _, _, projection = project_onto_route(point, (start, end))
                if abs(math.dist(point, projection) - value_m) <= VERTEX_MATCH_TOL_M:
                    return (projection, point) if first_is_a else (point, projection)
    raise DimensionError(
        "no witness pair reproduces the computed clear gap — the gap scalar and the footprint "
        "corners disagree, which is a code defect, not a data defect"
    )


# --------------------------------------------------------------------------- #
# checks
# --------------------------------------------------------------------------- #


def chain_tolerance_mm(decimals: int) -> float:
    """Half a display unit, in millimetres — the pass window for a chain at ``decimals``.

    Derived, never hard-coded: a chain displayed at 3 dp compares at 0.5 mm, one at 2 dp at
    5 mm. A tolerance is needed at all because summing *rounded* floats reintroduces binary
    representation error (``2.499 + 2.499 + 2.499`` is not exactly ``7.497`` in IEEE 754),
    while a genuine mismatch is a whole display unit or more — so half a unit separates the
    two cleanly and cannot absorb a real 1 mm contradiction.
    """

    return 0.5 * 10.0 ** (3 - _decimals(decimals, "chain_tolerance_mm decimals"))


def check_chains(dset: DimensionSet, values: Mapping[str, float]) -> list[Finding]:
    """Assert every declared chain's displayed components sum to its displayed overall.

    ``values`` is the already-computed model value per dimension id — normally
    ``{m.spec.id: m.value_m for m in measure(...)}`` — so the check reads the same numbers
    the sheet drew and cannot recompute its way into disagreeing with it.

    The comparison is on the **displayed** values, not the model values. That is the whole
    point: the failure is one that rounding *introduces*, so a model-value comparison would
    pass on exactly the sheets that contradict themselves. Nothing here adjusts a
    component to make the sum work — absorbing the discrepancy into one bay would hide a
    real ambiguity from whoever has to build it.
    """

    by_id = {spec.id: spec for spec in dset.dimensions}
    findings: list[Finding] = []
    for chain in dset.chains:
        overall_spec = by_id[chain.overall]
        decimals = overall_spec.style.decimals
        overall_text, overall_shown = displayed_value(
            overall_spec, _chain_value(values, chain, chain.overall)
        )
        parts: list[tuple[str, str]] = []
        total = 0.0
        for name in chain.components:
            text, shown = displayed_value(by_id[name], _chain_value(values, chain, name))
            parts.append((name, text))
            total += shown
        discrepancy_mm = (total - overall_shown) * _MM_PER_M
        if abs(discrepancy_mm) < chain_tolerance_mm(decimals):
            continue
        summed = " + ".join(f"{name} {text}" for name, text in parts)
        message = (
            f"chain {chain.id!r}: components sum to {canonical_fmt(total, decimals)} m but "
            f"overall {chain.overall!r} displays {overall_text} m — "
            f"{abs(discrepancy_mm):.1f} mm apart at {decimals} dp. components: {summed}"
        )
        if chain.note:
            message = f"{message}. note: {chain.note}"
        findings.append(Finding(chain.severity, "chain-consistency", message))
    return findings


def _chain_value(values: Mapping[str, float], chain: Chain, dimension_id: str) -> float:
    """The model value for one chain member, or a loud failure.

    Load-time validation guarantees the id names a declared dimension, so a gap here means
    the caller passed a partial value map — a silent skip would report a chain clean without
    having added it up.
    """

    if dimension_id not in values:
        raise DimensionError(
            f"chain {chain.id!r}: no computed value for dimension {dimension_id!r} — "
            f"check_chains needs a value for every member (given: "
            f"{', '.join(sorted(values)) or 'none'})"
        )
    return values[dimension_id]


def check_dimensions(
    dset: DimensionSet,
    measurements: Iterable[Measurement],
    *,
    rows: Sequence[SettingOutRow] | None = None,
) -> list[Finding]:
    """Expectation, zero-value, axis-duplication, setting-out-Z and chain findings.

    Reuses ``layout.Finding`` so the CLI prints layout and dimension findings with one
    formatter. ``rows`` is optional and only supplies the row count in the
    ``setting-out-z`` message. With no ``chains:`` declared the chain pass contributes
    nothing, so a spec that predates P11 is unaffected.
    """

    materialised = list(measurements)
    findings: list[Finding] = []
    for measurement in materialised:
        spec = measurement.spec
        if spec.expect_m is not None:
            delta = abs(measurement.value_m - spec.expect_m)
            if delta > spec.style.expect_tol_m:
                refs = measurement.provenance.get("refs", {})
                arrow = f"{refs.get('from', '')} -> {refs.get('to', '')}".strip(" ->")
                findings.append(
                    Finding(
                        spec.severity,
                        "dimension-expectation",
                        f"{spec.id}"
                        + (f" ({arrow})" if arrow else "")
                        + f" computed {canonical_fmt(measurement.value_m, spec.style.decimals)} m, "
                        f"expected {spec.expect_m:g} m "
                        f"±{spec.style.expect_tol_m:g} ({spec.expect_source}). The sheet shows "
                        f"{canonical_fmt(measurement.value_m, spec.style.decimals)} m",
                    )
                )
        if measurement.is_zero:
            findings.append(
                Finding(
                    "warn",
                    "dimension-zero",
                    f"{spec.id} ({spec.kind}) computed a zero value and is annotated as a "
                    "leader note; a zero dimension on a GA is usually a modelling slip",
                )
            )
        if spec.axis is not None and _BEARING_AXIS.match(spec.axis):
            findings.append(
                Finding(
                    "warn",
                    "dimension-axis",
                    f"{spec.id} axis {spec.axis!r} duplicates the placement bearing that lives "
                    "in the register; prefer 'principal' / 'principal-cross' so the dimension "
                    "follows the layout when it is re-snapped",
                )
            )
    setting_out = dset.setting_out
    if setting_out is not None and setting_out.z_source == "none":
        count = "every" if rows is None else f"{len(rows)}"
        findings.append(
            Finding(
                "warn",
                "setting-out-z",
                f"{count} row(s) have no surveyed level (z source 'none'), so the Z column "
                f"reads {NOT_SURVEYED!r}",
            )
        )
    findings.extend(
        check_chains(dset, {m.spec.id: m.value_m for m in materialised})
    )
    return findings


# --------------------------------------------------------------------------- #
# setting out
# --------------------------------------------------------------------------- #


def setting_out_rows(dset: DimensionSet, layout: Layout) -> list[SettingOutRow]:
    """Rows straight from the register and the datums. Raises on a missing id or Z."""

    spec = dset.setting_out
    if spec is None:
        raise DimensionError(f"{dset.source.name}: no setting_out: block to build a table from")
    resolver = resolve(dset, layout)
    rows: list[SettingOutRow] = []

    if "placements" in spec.include:
        selected = _select_placements(spec.of, resolver, f"{dset.source.name}: setting_out.of")
        unkeyed = [p for p in selected if not p.has_durable_id]
        if unkeyed:
            known = ", ".join(resolver.known_keys) or "none"
            named = {id(p) for p in resolver.placements.values()}
            still_unkeyed = [p for p in unkeyed if id(p) not in named]
            if still_unkeyed:
                sample = still_unkeyed[0]
                raise DimensionError(
                    f"{dset.source.name}: setting_out: {len(still_unkeyed)} of {len(selected)} "
                    f"selected placement(s) have neither a stable id nor a tag, so they cannot "
                    f"appear in a setting-out table — a blank or an ordinal id in a setting-out "
                    f"table is a number someone will set out. First: {sample.type} at "
                    f"({sample.origin[0]:.3f}, {sample.origin[1]:.3f}). Known keys: {known}. "
                    "Fix by setting the 'tag' attribute in the QGIS placements layer and "
                    "re-running `technical_drawings_for_agents layout --from-geojson`, by declaring a naming: "
                    "entry with a property predicate, or by narrowing setting_out.of"
                )
        for placement in selected:
            rows.append(
                SettingOutRow(
                    id=resolver.key_for(placement),
                    easting=placement.origin[0],
                    northing=placement.origin[1],
                    z=_placement_z(spec, placement, resolver, dset),
                    kind="placement",
                    type_name=placement.type,
                    source=str(placement.properties.get("snapped_by", "")),
                )
            )

    if "datums" in spec.include:
        for datum in dset.datums:
            rows.append(
                SettingOutRow(
                    id=datum.name,
                    easting=datum.point[0],
                    northing=datum.point[1],
                    z=datum.z if spec.z_source != "none" else None,
                    kind="datum",
                    type_name="datum",
                    source=datum.source,
                )
            )

    if "route-points" in spec.include:
        for route in dset.routes:
            for index, ref in enumerate(route.points):
                point = _ref_point(ref, resolver, f"{dset.source.name}: routes[{route.name}]")
                rows.append(
                    SettingOutRow(
                        id=f"{route.name}.{index}",
                        easting=point[0],
                        northing=point[1],
                        z=None if spec.z_source == "none" else _datum_z(spec, dset),
                        kind="route-point",
                        type_name=route.kind,
                        source=ref.describe(),
                    )
                )

    if not rows:
        raise DimensionError(
            f"{dset.source.name}: setting_out selected nothing to tabulate "
            f"(include: {list(spec.include)})"
        )
    return _ordered_rows(rows, spec)


def _placement_z(
    spec: SettingOutSpec, placement: Placement, resolver: Resolver, dset: DimensionSet
) -> float | None:
    if spec.z_source == "none":
        return None
    if spec.z_source == "datum":
        return _datum_z(spec, dset)
    assert spec.z_property is not None
    value = placement.properties.get(spec.z_property)
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DimensionError(
            f"{dset.source.name}: setting_out.z property {spec.z_property!r} is missing or "
            f"non-numeric on {resolver.key_for(placement)} — there is no partial Z column, "
            "because a table with some levels filled reads as though the blanks were zero"
        )
    return float(value)


def _datum_z(spec: SettingOutSpec, dset: DimensionSet) -> float | None:
    if spec.z_datum is None:
        return None
    datum = next(d for d in dset.datums if d.name == spec.z_datum)
    return datum.z


def _ordered_rows(rows: list[SettingOutRow], spec: SettingOutSpec) -> list[SettingOutRow]:
    if spec.order == "register":
        return rows
    if spec.order == "northing":
        return sorted(rows, key=lambda row: (-row.northing, row.easting, row.id))
    return sorted(rows, key=lambda row: (row.id.casefold(), row.id))


def setting_out_table(
    rows: Sequence[SettingOutRow], spec: SettingOutSpec, *, crs: str, snap: str
) -> SettingOutTable:
    """Format the rows once. The SVG table and the text sidecar both consume this."""

    header = ("ID", "E (m)", "N (m)", "Z (m)")
    formatted = tuple(
        (
            row.id,
            canonical_fmt(row.easting, spec.decimals),
            canonical_fmt(row.northing, spec.decimals),
            NOT_SURVEYED if row.z is None else canonical_fmt(row.z, spec.decimals),
        )
        for row in rows
    )
    # With no surveyed level there is no vertical datum to name, and inventing a
    # plausible-looking one in the caption would undo what the Z column just said.
    datum_text = spec.vertical_datum or f"Z {NOT_SURVEYED}"
    return SettingOutTable(
        header=header,
        rows=formatted,
        note=spec.note,
        caption=f"SETTING OUT — {crs}, {datum_text}, poses: {snap}",
    )


def dump_setting_out(
    rows: Iterable[SettingOutRow],
    spec: SettingOutSpec,
    *,
    crs: str,
    fmt: Literal["csv", "yaml"] = "csv",
    snap: str = "effective",
) -> str:
    """Serialise the table. The SVG table and this text come from the SAME rows."""

    materialised = list(rows)
    table = setting_out_table(materialised, spec, crs=crs, snap=snap)
    if fmt == "csv":
        lines = [f"# {table.caption}"]
        if table.note:
            lines.extend(f"# note: {part}" for part in table.note.splitlines())
        lines.append(",".join(table.header) + ",KIND,TYPE,SOURCE")
        for row, values in zip(materialised, table.rows):
            lines.append(
                ",".join([*(_csv_cell(value) for value in values), row.kind,
                          _csv_cell(row.type_name), _csv_cell(row.source)])
            )
        return "\n".join(lines) + "\n"
    if fmt == "yaml":
        payload = {
            "setting_out": {
                "caption": table.caption,
                "crs": crs,
                "snap": snap,
                "vertical_datum": spec.vertical_datum,
                "note": table.note,
                "columns": list(table.header),
                "rows": [
                    {
                        "id": row.id,
                        "E": values[1],
                        "N": values[2],
                        "Z": values[3],
                        "kind": row.kind,
                        "type": row.type_name,
                        "source": row.source,
                    }
                    for row, values in zip(materialised, table.rows)
                ],
            }
        }
        return yaml.safe_dump(payload, sort_keys=False, width=100, allow_unicode=True)
    raise DimensionError(f"unknown setting-out format {fmt!r} (csv or yaml)")


def _csv_cell(value: str) -> str:
    text = str(value)
    if any(char in text for char in ',"\n'):
        return '"' + text.replace('"', '""') + '"'
    return text


def dump_measurements(dset: DimensionSet, measurements: Iterable[Measurement]) -> str:
    """The machine-readable measurement register — the quantities interface.

    Carries the **full-precision** ``value_m`` alongside the rounded ``text``: the
    rounded string is for the sheet and is never fed back into a calculation.
    """

    import json

    payload = {
        "dimension_set": dset.id,
        "layout": str(dset.layout_path),
        "snap": dset.snap,
        "dimensions": [
            {
                "id": m.spec.id,
                "kind": m.spec.kind,
                "value_m": m.value_m,
                "decimals": m.spec.style.decimals,
                "text": m.text,
                "witness_mode": m.witness_mode,
                "anchors": [list(m.anchor_a), list(m.anchor_b)],
                "path": [list(point) for point in m.path],
                "layer": m.spec.style.layer,
                "expect_m": m.spec.expect_m,
                "expect_source": m.spec.expect_source,
                "provenance": m.provenance,
            }
            for m in measurements
        ],
    }
    return json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n"


# --------------------------------------------------------------------------- #
# rendering
# --------------------------------------------------------------------------- #


def render_dimensions(
    vb: Any,
    measurements: Iterable[Measurement],
    *,
    setting_out: SettingOutTable | None = None,
    setting_out_at: tuple[float, float] = (40.0, 40.0),
) -> tuple[list[str], list[AnnotationBox]]:
    """SVG elements plus the annotation boxes P5's legibility checks consume.

    Never returns ``""`` for a measurement: ``Drawing.add`` filters falsy elements, so
    an empty string would make a dimension vanish from the sheet unnoticed. A
    measurement that reached here is drawable by construction, and the witness
    invariant is asserted first.

    ``vb`` is any projector exposing ``point(x, y) -> (u, v)`` with ``v`` increasing
    down the page: ``svg.ViewBox`` (SVG pixels) and P1's ``sheet.Viewport`` (paper
    millimetres) both qualify, and the ``*_px`` style knobs are then in that
    projector's own paper units.
    """

    elements: list[str] = []
    boxes: list[AnnotationBox] = []
    for measurement in measurements:
        _assert_witness_invariant(measurement)
        body, box = _render_one(vb, measurement)
        elements.append(body)
        boxes.append(box)
    if setting_out is not None:
        table_svg, table_box = svg_setting_out_table(
            setting_out_at[0], setting_out_at[1], setting_out.header, setting_out.rows,
            note=setting_out.note, caption=setting_out.caption,
        )
        elements.append(table_svg)
        boxes.append(
            AnnotationBox(
                id="setting-out",
                kind="setting-out-table",
                x=table_box["x"],
                y=table_box["y"],
                width=table_box["width"],
                height=table_box["height"],
                text=setting_out.caption,
                confidence=table_box["confidence"],
            )
        )
    return elements, boxes


def _assert_witness_invariant(measurement: Measurement) -> None:
    """``drawn_length == value``, else raise. The single most important line here.

    An invariant rather than a finding: a violation means a code defect, not a data
    defect, and it makes shipping a line that spans one distance while its text states
    another structurally impossible.
    """

    if measurement.witness_mode == "path":
        walked = sum(
            math.dist(a, b) for a, b in zip(measurement.path, measurement.path[1:])
        )
        if abs(walked - measurement.value_m) > WITNESS_TOL_M:
            raise DimensionError(
                f"{measurement.spec.id}: the measured path walks {walked!r} m but the value "
                f"states {measurement.value_m!r} m"
            )
        return
    drawn = measurement.drawn_length_m
    if abs(drawn - abs(measurement.value_m)) > WITNESS_TOL_M:
        raise DimensionError(
            f"{measurement.spec.id}: witness geometry spans {drawn!r} m but the text states "
            f"{measurement.value_m!r} m — a dimension may never contradict the geometry it "
            "annotates"
        )


def _render_one(vb: Any, measurement: Measurement) -> tuple[str, AnnotationBox]:
    spec = measurement.spec
    style = spec.style
    open_tag = (
        f'<g id="dim-{spec.id}" class="dimension" data-kind="{spec.kind}" '
        f'data-layer="{style.layer}">'
    )
    leader = (
        measurement.witness_mode == "path"
        or style.text_leader
        or measurement.is_zero
    )
    if leader:
        body, box = _leader_form(vb, measurement)
    else:
        body, box = _linear_form(vb, measurement)
    return "\n".join([open_tag, *body, "</g>"]), box


def _linear_form(vb: Any, measurement: Measurement) -> tuple[list[str], AnnotationBox]:
    style = measurement.spec.style
    ax, ay = vb.point(*measurement.anchor_a)
    bx, by = vb.point(*measurement.anchor_b)
    length = math.hypot(bx - ax, by - ay)
    if length == 0:  # pragma: no cover - guarded by the leader branch
        return _leader_form(vb, measurement)
    ux, uy = (bx - ax) / length, (by - ay) / length
    # The normal is taken in paper space, after projection: the y axis is inverted
    # there, so offsetting in model metres and then mapping would flip "above" for a
    # vertical dimension.
    nx, ny = uy, -ux
    side = 1.0 if style.text_side == "above" else -1.0
    off = style.offset_px * side

    parts: list[str] = []
    for px, py in ((ax, ay), (bx, by)):
        parts.append(
            svg_line(
                px + nx * style.witness_gap_px * side,
                py + ny * style.witness_gap_px * side,
                px + nx * (style.offset_px + style.witness_over_px) * side,
                py + ny * (style.offset_px + style.witness_over_px) * side,
                stroke=COL_CAD_DIM,
                stroke_width=0.5,
            )
        )
    a_off = (ax + nx * off, ay + ny * off)
    b_off = (bx + nx * off, by + ny * off)
    parts.append(
        svg_line(a_off[0], a_off[1], b_off[0], b_off[1], stroke=COL_CAD_DIM, stroke_width=0.7)
    )
    outside = length < _SHORT_DIMENSION_ARROWS * style.arrow_px
    parts.append(_arrowhead(a_off, (ux, uy), style.arrow_px, outward=outside))
    parts.append(_arrowhead(b_off, (-ux, -uy), style.arrow_px, outward=outside))
    if measurement.witness_mode == "point":
        for px, py in ((ax, ay), (bx, by)):
            half = style.arrow_px / 2.0
            parts.append(
                svg_line(px - half, py, px + half, py, stroke=COL_CAD_CENTER, stroke_width=0.5)
            )
            parts.append(
                svg_line(px, py - half, px, py + half, stroke=COL_CAD_CENTER, stroke_width=0.5)
            )

    if outside:
        tx = b_off[0] + ux * (style.arrow_px * 2.0) + nx * style.text_gap_px * side
        ty = b_off[1] + uy * (style.arrow_px * 2.0) + ny * style.text_gap_px * side
        anchor = "start"
    else:
        mid = ((a_off[0] + b_off[0]) / 2.0, (a_off[1] + b_off[1]) / 2.0)
        tx = mid[0] + nx * style.text_gap_px * side + ux * style.text_along_px
        ty = mid[1] + ny * style.text_gap_px * side + uy * style.text_along_px
        anchor = "middle"
    rotate = _text_rotation(ux, uy) if style.text_rotate == "auto" else 0.0
    parts.append(
        svg_text(
            tx, ty, measurement.text, font_size=_DEFAULT_FONT_SIZE,
            fill=COL_CAD_DIM_TEXT, anchor=anchor, rotate=rotate,
        )
    )
    return parts, _annotation_box(measurement, tx, ty, anchor, rotate)


def _arrowhead(apex: tuple[float, float], direction: Point, size: float, *, outward: bool) -> str:
    """The same triangle ``svg_dimension_h`` draws: ``size`` long, ``size/2`` half-height."""

    ux, uy = (-direction[0], -direction[1]) if outward else direction
    nx, ny = uy, -ux
    base = (apex[0] + ux * size, apex[1] + uy * size)
    half = size / 2.0
    return svg_polygon(
        [
            apex,
            (base[0] + nx * half, base[1] + ny * half),
            (base[0] - nx * half, base[1] - ny * half),
        ],
        fill=COL_CAD_DIM,
        stroke=COL_CAD_DIM,
        stroke_width=0,
    )


def _text_rotation(ux: float, uy: float) -> float:
    """Rotation normalised into (-90, 90] so text always reads left to right."""

    degrees = math.degrees(math.atan2(uy, ux))
    while degrees > 90.0:
        degrees -= 180.0
    while degrees <= -90.0:
        degrees += 180.0
    return 0.0 if abs(degrees) < 1e-9 else degrees


def _leader_form(vb: Any, measurement: Measurement) -> tuple[list[str], AnnotationBox]:
    style = measurement.spec.style
    if measurement.witness_mode == "path" and len(measurement.path) >= 2:
        target = _arc_length_midpoint(measurement.path, measurement.value_m)
    else:
        target = (
            (measurement.anchor_a[0] + measurement.anchor_b[0]) / 2.0,
            (measurement.anchor_a[1] + measurement.anchor_b[1]) / 2.0,
        )
    svg, box = svg_dimension_leader_note(
        vb, target, measurement.text,
        elbow_px=(style.offset_px * 0.72, -style.offset_px * 0.56),
        run_px=style.offset_px * 2.08,
    )
    return [svg], _annotation_box(
        measurement, box["x"], box["y"] + box["height"], "start", 0.0, confidence=box["confidence"]
    )


def _arc_length_midpoint(path: Sequence[Point], value_m: float) -> Point:
    """Walk the path to half its length — deterministic, and independent of vertex count."""

    target = value_m / 2.0
    walked = 0.0
    for a, b in zip(path, path[1:]):
        span = math.dist(a, b)
        if walked + span >= target:
            t = 0.0 if span == 0 else (target - walked) / span
            return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
        walked += span
    return path[-1]


def _annotation_box(
    measurement: Measurement,
    x: float,
    y: float,
    anchor: str,
    rotate: float,
    *,
    confidence: str | None = None,
) -> AnnotationBox:
    box, measured_confidence = _text_extent(measurement.text, x, y, anchor)
    return AnnotationBox(
        id=measurement.spec.id,
        kind="dimension-text",
        x=box[0],
        y=box[1],
        width=box[2],
        height=box[3],
        rotation_deg=rotate,
        text=measurement.text,
        confidence=confidence or measured_confidence,
    )


def _text_extent(
    text: str, x: float, y: float, anchor: str, font_size: float = _DEFAULT_FONT_SIZE
) -> tuple[tuple[float, float, float, float], str]:
    """Text extent from P5's own estimator — never a second width model."""

    try:
        box, confidence = text_box(text, x, y, font_size, family="monospace", anchor=anchor)
    except LegibilityError as exc:  # pragma: no cover - anchor is validated upstream
        raise DimensionError(f"cannot size dimension text {text!r}: {exc}") from exc
    return (box.x0, box.y0, box.width, box.height), confidence


# --------------------------------------------------------------------------- #
# SVG helpers that need the text metric (kept beside the semantics, not in svg.py)
# --------------------------------------------------------------------------- #


def svg_dimension_leader_note(
    vb: Any,
    target: Point,
    label: str,
    *,
    elbow_px: tuple[float, float] = (18.0, -14.0),
    run_px: float = 52.0,
    dot_radius: float = 3.0,
    font_size: float = _DEFAULT_FONT_SIZE,
) -> tuple[str, dict[str, Any]]:
    """A leader-and-note annotation: dot, kinked leader, text at the end.

    The offsets are paper-space, so the elbow and the run are computed after
    projection. ``svg_leader`` takes three *world* points instead, and there is no
    documented inverse of a projector to round-trip through — so this shares
    ``svg_leader``'s primitives, weights and colours rather than its signature.
    """

    tx, ty = vb.point(*target)
    ex, ey = tx + elbow_px[0], ty + elbow_px[1]
    lx, ly = ex + run_px, ey
    parts = [
        svg_circle(tx, ty, dot_radius, fill=COL_CAD_DIM, stroke=COL_CAD_DIM, stroke_width=0),
        svg_line(tx, ty, ex, ey, stroke=COL_CAD_DIM, stroke_width=0.5),
        svg_line(ex, ey, lx, ly, stroke=COL_CAD_DIM, stroke_width=0.5),
        svg_text(
            lx + 5, ly - 4, label, font_size=font_size, fill=COL_CAD_DIM_TEXT, anchor="start"
        ),
    ]
    extent, confidence = _text_extent(label, lx + 5, ly - 4, "start", font_size)
    return "\n".join(parts), {
        "x": extent[0],
        "y": extent[1],
        "width": extent[2],
        "height": extent[3],
        "confidence": confidence,
    }


def svg_setting_out_table(
    x: float,
    y: float,
    header: Sequence[str],
    rows: Sequence[Sequence[str]],
    *,
    col_px: Sequence[float] = (84.0, 96.0, 96.0, 96.0),
    row_px: float = 13.0,
    font_size: float = 8.0,
    note: str | None = None,
    caption: str = "",
) -> tuple[str, dict[str, Any]]:
    """Render a pre-formatted table. Takes STRINGS — it does no arithmetic.

    That is the point: the SVG table and the CSV sidecar are two renderings of one
    ``list[SettingOutRow]``, so they cannot drift.
    """

    if len(header) > len(col_px):
        raise DimensionError(
            f"setting-out table has {len(header)} columns but only {len(col_px)} widths"
        )
    width = sum(col_px[: len(header)])
    parts = ['<g class="setting-out-table">']
    cursor = y
    if caption:
        parts.append(
            svg_text(x, cursor, caption, font_size=font_size, fill=COL_CAD_DIM_TEXT, anchor="start")
        )
        cursor += row_px
    for line in (header, *rows):
        offset = x
        for index, cell in enumerate(line):
            parts.append(
                svg_text(
                    offset + 3, cursor, str(cell), font_size=font_size,
                    fill=COL_CAD_DIM_TEXT, anchor="start",
                )
            )
            offset += col_px[index]
        parts.append(
            svg_line(x, cursor + 3, x + width, cursor + 3, stroke=COL_CAD_DIM, stroke_width=0.3)
        )
        cursor += row_px
    if note:
        for line in note.splitlines():
            parts.append(
                svg_text(
                    x, cursor, line, font_size=font_size - 1, fill=COL_CAD_DIM, anchor="start"
                )
            )
            cursor += row_px - 3
    parts.append("</g>")
    _, confidence = _text_extent(caption or "".join(header), x, y, "start", font_size)
    return "\n".join(parts), {
        "x": x,
        "y": y - font_size,
        "width": width,
        "height": cursor - y + font_size,
        "confidence": confidence,
    }
