"""Site layout: place many components from a text placements register, then check it.

This closes the loop between a GIS editor (where a human drags real footprints
around on an orthophoto) and the drawings/BOQ pipeline (which needs geometry it
can regenerate). The layout config and the placements register are **text in
git** — the GeoPackage is an editor, not the source of truth.

Two entry points:

``derive_placements``
    Read a GeoJSON export of an editable footprint layer and derive, per
    feature, its centroid, long-axis rotation and footprint size. Writes a
    generated ``placements.yaml`` whose diff is the change log.

``build_layout`` / ``check_layout``
    Expand each placement into its component geometry (one placement can carry
    several specs — e.g. a slab *and* the unit that sits on it) and assert the
    layout rules: parallelism, clear spacing, no overlap, inside an envelope.

Checks fail loudly. A silently-skipped placement is a defect, not a default:
an unmapped ``type`` is an error, never a shrug.

Generated registers are written in a **canonical order** (``canonical_instances``),
so one physical edit is one record's worth of diff and re-exporting the editor
layer in a different feature order changes no bytes. Determinism comes from that
sort, not from a synthesised identity: nothing derivable from an export alone is
both stable under a move and non-renumbering under a delete, so a durable join
key must be *authored*. The tool makes it first-class, validated and counted —
it does not fake one.
"""

from __future__ import annotations

import difflib
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Literal

import yaml

from ..layers import LayerTable, LayerTableError, resolve_layer_table
from .place import PlacedFeature, place
from .spec import ComponentSpecError, Point, load_component

# The generated register is the site change log, so its bytes are a contract:
# fixed rounding, fixed key order, and a total order over the emitted values.
REGISTER_ROUND_DP = 3
REGISTER_KEY_ORDER = ("type", "origin_utm", "rotation_deg", "size_m", "tag", "id")
# An identity ends up as a GeoJSON property, DXF text, a CSV join key and maybe a
# filename, so it is constrained at the door rather than sanitised in five emitters.
ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

CHECK_KINDS = {"parallel", "clear-spacing", "no-overlap", "within-envelope", "ids-present"}
Severity = Literal["error", "warn"]

_GENERATED_REGISTER_PREFIX = "# GENERATED placements register"
_DERIVED_RE = re.compile(r"^# Derived from: (?P<source>.*)$")


class LayoutError(ValueError):
    """Raised when a layout config, placements register or check is malformed."""


# --------------------------------------------------------------------------- #
# config + register
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Placement:
    """One placed item: a type, an anchor, a bearing, and free-form properties."""

    type: str
    origin: Point
    rotation_deg: float
    tag: str | None = None
    id: str | None = None
    size_m: tuple[float, float] | None = None
    properties: dict[str, Any] = field(default_factory=dict)

    @property
    def label(self) -> str:
        return self.tag or self.type

    @property
    def identity(self) -> str | None:
        """The durable join key, or None — author-supplied, never derived.

        No identity derivable from an editor export is both stable under a move
        and non-renumbering under a delete, so the tool refuses to invent one.
        An explicit ``id`` wins; a ``tag`` is already an equipment identity.
        """

        return self.id or self.tag or None

    @property
    def has_durable_id(self) -> bool:
        return self.identity is not None


@dataclass(frozen=True)
class Check:
    kind: str
    params: dict[str, Any]


@dataclass(frozen=True)
class Editor:
    """How to read back the GIS layer a human drags footprints around in."""

    type_field: str = "type"
    tag_field: str = "tag"
    id_field: str = "id"
    parked: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class RegisterDrift:
    """What ``--check-register`` found: the bytes on disk versus the bytes we would write."""

    register: Path
    on_disk: str
    expected: str

    @property
    def clean(self) -> bool:
        return self.on_disk == self.expected

    def unified_diff(self) -> list[str]:
        return list(
            difflib.unified_diff(
                self.on_disk.splitlines(keepends=True),
                self.expected.splitlines(keepends=True),
                fromfile=self.register.name,
                tofile=self.register.name,
            )
        )


@dataclass(frozen=True)
class Layout:
    id: str
    crs: str
    components_root: Path
    types: dict[str, list[Path]]
    placements: list[Placement]
    checks: list[Check]
    source: Path
    layer_table: LayerTable | None = None
    editor: Editor = field(default_factory=Editor)
    register: Path | None = None
    groups: list[Group] = field(default_factory=list)


@dataclass(frozen=True)
class Group:
    """A set of placements laid out to a rule rather than to where they landed.

    The human puts the group roughly where it belongs on the imagery; the rule
    derives the spec-compliant pose from that on every build. This keeps the
    corrected geometry a *regenerating* fact rather than a one-off hand fix.
    """

    name: str
    within: list[str]
    bearing: str = "mean"           # mean | first
    pitch: str = "spec"             # spec (short edge + clear_m) | as-placed
    clear_m: float = 0.0
    order: str = "north-to-south"   # north-to-south | south-to-north | as-listed


@dataclass(frozen=True)
class Finding:
    severity: Severity
    check: str
    message: str

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.severity.upper()}: {self.check}: {self.message}"


def load_layout(path: str | Path) -> Layout:
    """Load a site-layout config, resolving paths relative to the config file."""

    config_path = Path(path).resolve()
    data = _read_yaml(config_path)
    if not isinstance(data, dict):
        raise LayoutError(f"{config_path.name}: top level must be a mapping")

    layout_block = data.get("layout")
    if not isinstance(layout_block, dict):
        raise LayoutError(f"{config_path.name}: missing layout: {{id, crs}}")
    layout_id = layout_block.get("id")
    if not isinstance(layout_id, str) or not layout_id.strip():
        raise LayoutError(f"{config_path.name}: layout.id must be a non-empty string")
    crs = layout_block.get("crs", "EPSG:32630")
    if not isinstance(crs, str) or not crs.strip():
        raise LayoutError(f"{config_path.name}: layout.crs must be a non-empty string")

    base = config_path.parent
    layer_table = _load_layer_table_ref(data.get("layers"), base, config_path.name)

    components_block = data.get("components")
    if not isinstance(components_block, dict):
        raise LayoutError(f"{config_path.name}: missing components: {{root, types}}")
    root = (base / str(components_block.get("root", "."))).resolve()
    if not root.is_dir():
        raise LayoutError(f"{config_path.name}: components.root not a directory: {root}")

    raw_types = components_block.get("types")
    if not isinstance(raw_types, dict) or not raw_types:
        raise LayoutError(f"{config_path.name}: components.types must be a non-empty mapping")
    types: dict[str, list[Path]] = {}
    for name, specs in raw_types.items():
        if isinstance(specs, str):
            specs = [specs]
        if not isinstance(specs, list) or not specs:
            raise LayoutError(f"components.types.{name} must be a spec path or list of paths")
        resolved: list[Path] = []
        for spec in specs:
            spec_path = (root / str(spec)).resolve()
            if not spec_path.is_file():
                raise LayoutError(f"components.types.{name}: spec not found: {spec_path}")
            resolved.append(spec_path)
        types[str(name)] = resolved

    register = None
    register_is_generated = False
    if isinstance(data.get("placements"), str):
        register = (base / str(data["placements"])).resolve()
        register_is_generated = register.is_file() and _register_is_generated(register)
    placements = _load_placements_block(data.get("placements"), base, config_path.name)
    # A hand-edit can collide or duplicate a pose just as an export can, so the
    # load boundary enforces the same rules as the write boundary.
    canonical_instances([placement_instance(p) for p in placements])
    checks = _load_checks(data.get("checks"), config_path.name)
    editor = _load_editor(data.get("editor"), config_path.name)
    groups = _load_groups(data.get("groups"), config_path.name, set(types))
    if register is not None and register_is_generated:
        for index, group in enumerate(groups):
            if group.order == "as-listed":
                raise LayoutError(
                    f"groups[{index}].snap.order 'as-listed' cannot be used with a GENERATED "
                    f"placements register ({register}): member order would follow the canonical "
                    "sort, not your intent — use north-to-south / south-to-north, or an inline "
                    "placements list"
                )

    unknown = sorted({p.type for p in placements} - set(types))
    if unknown:
        raise LayoutError(
            f"{config_path.name}: placement type(s) with no components.types entry: "
            f"{', '.join(unknown)}"
        )

    return Layout(
        id=layout_id,
        crs=crs,
        components_root=root,
        types=types,
        placements=placements,
        checks=checks,
        source=config_path,
        layer_table=layer_table,
        editor=editor,
        register=register,
        groups=groups,
    )


BEARING_MODES = {"mean", "first"}
PITCH_MODES = {"spec", "as-placed"}
ORDER_MODES = {"north-to-south", "south-to-north", "as-listed"}

# A snap move is worth reporting at 1 mm / 0.001°. Below that it is register
# rounding, and reporting it as a "move" of 0.000 m would only cry wolf.
SNAP_REPORT_MIN_M = 1e-3
SNAP_REPORT_MIN_DEG = 1e-3


def _load_groups(raw: Any, ctx: str, known_types: set[str]) -> list[Group]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise LayoutError(f"{ctx}: groups must be a list")
    groups: list[Group] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise LayoutError(f"groups[{index}] must be a mapping")
        within = item.get("within")
        if isinstance(within, str):
            within = [within]
        if not isinstance(within, list) or not within:
            raise LayoutError(f"groups[{index}].within must name at least one type")
        unknown = sorted({str(w) for w in within} - known_types)
        if unknown:
            raise LayoutError(f"groups[{index}].within names unknown type(s): {', '.join(unknown)}")

        snap = item.get("snap")
        if not isinstance(snap, dict):
            raise LayoutError(f"groups[{index}] needs a snap: mapping")
        bearing = str(snap.get("bearing", "mean"))
        if bearing not in BEARING_MODES:
            raise LayoutError(
                f"groups[{index}].snap.bearing must be one of {sorted(BEARING_MODES)}"
            )
        pitch = str(snap.get("pitch", "spec"))
        if pitch not in PITCH_MODES:
            raise LayoutError(f"groups[{index}].snap.pitch must be one of {sorted(PITCH_MODES)}")
        order = str(snap.get("order", "north-to-south"))
        if order not in ORDER_MODES:
            raise LayoutError(f"groups[{index}].snap.order must be one of {sorted(ORDER_MODES)}")
        clear = snap.get("clear_m", 0.0)
        if isinstance(clear, bool) or not isinstance(clear, (int, float)):
            raise LayoutError(f"groups[{index}].snap.clear_m must be a number")
        if pitch == "spec" and "clear_m" not in snap:
            raise LayoutError(
                f"groups[{index}].snap: pitch 'spec' needs clear_m (the specified clear gap)"
            )
        groups.append(
            Group(
                name=str(item.get("name", f"group-{index}")),
                within=[str(w) for w in within],
                bearing=bearing,
                pitch=pitch,
                clear_m=float(clear),
                order=order,
            )
        )
    return groups


def _load_editor(raw: Any, ctx: str) -> Editor:
    if raw is None:
        return Editor()
    if not isinstance(raw, dict):
        raise LayoutError(f"{ctx}: editor must be a mapping")
    parked = raw.get("parked", [])
    if not isinstance(parked, list):
        raise LayoutError(f"{ctx}: editor.parked must be a list of named bbox zones")
    return Editor(
        type_field=_editor_field(raw, "type_field", "type", ctx),
        tag_field=_editor_field(raw, "tag_field", "tag", ctx),
        id_field=_editor_field(raw, "id_field", "id", ctx),
        parked=[_exclude_zone(zone, index) for index, zone in enumerate(parked)],
    )


def _editor_field(raw: dict[str, Any], key: str, default: str, ctx: str) -> str:
    value = raw.get(key, default)
    if not isinstance(value, str) or not value.strip():
        raise LayoutError(f"{ctx}: editor.{key} must be a non-empty string")
    return value


def _load_placements_block(raw: Any, base: Path, ctx: str) -> list[Placement]:
    """``placements:`` is either an inline instance list or a path to a register."""

    if isinstance(raw, str):
        register = (base / raw).resolve()
        if not register.is_file():
            raise LayoutError(f"{ctx}: placements register not found: {register}")
        data = _read_yaml(register)
        if not isinstance(data, dict) or not isinstance(data.get("instances"), list):
            raise LayoutError(f"{register.name}: placements register needs instances: [...]")
        raw = data["instances"]
    if not isinstance(raw, list):
        raise LayoutError(f"{ctx}: placements must be a list of instances or a register path")
    return [_placement(item, index) for index, item in enumerate(raw)]


def _placement(raw: Any, index: int) -> Placement:
    if not isinstance(raw, dict):
        raise LayoutError(f"placements[{index}] must be a mapping")
    type_name = raw.get("type")
    if not isinstance(type_name, str) or not type_name.strip():
        raise LayoutError(f"placements[{index}] missing type")
    if "origin_utm" not in raw:
        raise LayoutError(f"placements[{index}] ({type_name}) missing origin_utm")
    origin = _coord(raw["origin_utm"], f"placements[{index}].origin_utm")
    rotation = raw.get("rotation_deg", 0.0)
    if isinstance(rotation, bool) or not isinstance(rotation, (int, float)):
        raise LayoutError(f"placements[{index}].rotation_deg must be a number")
    size = raw.get("size_m")
    size_m = None
    if size is not None:
        size_m = _coord(size, f"placements[{index}].size_m")
    descriptor = _describe_record(type_name, origin, float(rotation))
    tag = _optional_identity(raw.get("tag"), f"placements[{index}].tag", descriptor)
    placement_id = _optional_identity(raw.get("id"), f"placements[{index}].id", descriptor)
    # `id` is reserved: left unreserved it would fall through into `properties`,
    # onto every emitted GeoJSON feature, and back out in the wrong key position.
    reserved = {"type", "origin_utm", "rotation_deg", "size_m", "tag", "id"}
    return Placement(
        type=type_name,
        origin=origin,
        rotation_deg=float(rotation),
        tag=tag,
        id=placement_id,
        size_m=size_m,
        properties={k: v for k, v in raw.items() if k not in reserved},
    )


def _load_checks(raw: Any, ctx: str) -> list[Check]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise LayoutError(f"{ctx}: checks must be a list")
    checks: list[Check] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise LayoutError(f"checks[{index}] must be a mapping")
        kind = item.get("check")
        if kind not in CHECK_KINDS:
            raise LayoutError(
                f"checks[{index}].check must be one of {sorted(CHECK_KINDS)}, got {kind!r}"
            )
        checks.append(Check(kind=str(kind), params={k: v for k, v in item.items() if k != "check"}))
    return checks


def _read_yaml(path: Path) -> Any:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise LayoutError(f"cannot read {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise LayoutError(f"invalid YAML in {path}: {exc}") from exc


def _load_layer_table_ref(raw: Any, base: Path, ctx: str) -> LayerTable | None:
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw.strip():
        raise LayoutError(f"{ctx}: layers must be a path or 'default'")
    try:
        return resolve_layer_table(raw.strip(), base=base)
    except LayerTableError as exc:
        raise LayoutError(f"{ctx}: layers: {exc}") from exc


def _coord(value: Any, ctx: str) -> Point:
    if not isinstance(value, list) or len(value) != 2:
        raise LayoutError(f"{ctx} must be [x, y]")
    if any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in value):
        raise LayoutError(f"{ctx} must contain numeric x,y values")
    return (float(value[0]), float(value[1]))


# --------------------------------------------------------------------------- #
# canonical register order + author-supplied identity
# --------------------------------------------------------------------------- #


def round_reg(value: float, dp: int = REGISTER_ROUND_DP) -> float:
    """Round to register precision, normalising -0.0 away.

    ``-0.0`` and ``0.0`` compare equal, so a sort cannot order them — but they
    serialise to *different bytes*, which is exactly the class of bug this
    canonicalisation exists to kill.
    """

    rounded = round(float(value), dp)
    return 0.0 if rounded == 0.0 else rounded


def canonical_sort_key(instance: Mapping[str, Any]) -> tuple[Any, ...]:
    """The total order over register instances: type, pose, size, identity last.

    Identity sorts *last* on purpose: populating or correcting an id must not
    move a single record, so the file's order is a function of the physical
    layout rather than of how much of the identity gap has been closed.
    """

    return _canonical_sort_key(_normalise_instance(instance, "instance", REGISTER_ROUND_DP))


def canonical_instances(
    instances: Sequence[Mapping[str, Any]], *, dp: int = REGISTER_ROUND_DP
) -> list[dict[str, Any]]:
    """Round, re-key, validate and sort register instances. Pure: input is untouched.

    Raises ``LayoutError`` on bad id syntax, a case-insensitive identity
    collision, or two records tied on the whole key (a pasted copy that was
    never moved). The sort is therefore total on every input it accepts, so two
    conforming implementations produce byte-identical files.
    """

    normalised = [
        _normalise_instance(instance, f"instances[{index}]", dp)
        for index, instance in enumerate(instances)
    ]
    _validate_identities_are_unique(normalised)
    _validate_poses_are_unique(normalised)
    return sorted(normalised, key=_canonical_sort_key)


def id_coverage(instances: Sequence[Mapping[str, Any]]) -> tuple[int, int]:
    """``(with_identity, total)`` — the number the register header tracks."""

    normalised = [
        _normalise_instance(instance, f"instances[{index}]", REGISTER_ROUND_DP)
        for index, instance in enumerate(instances)
    ]
    return sum(1 for item in normalised if _instance_identity(item) is not None), len(normalised)


def placement_instance(placement: Placement) -> dict[str, Any]:
    """Serialise a ``Placement`` back to register-instance form (round-trippable).

    One implementation, shared by ``--emit-register`` and ``check_register``:
    two serialisers for one contract is how the ends of a round-trip drift.
    """

    values: dict[str, Any] = {
        "type": placement.type,
        "origin_utm": [round_reg(placement.origin[0]), round_reg(placement.origin[1])],
        "rotation_deg": round_reg(placement.rotation_deg),
    }
    if placement.size_m is not None:
        values["size_m"] = [round_reg(placement.size_m[0]), round_reg(placement.size_m[1])]
    if placement.tag:
        values["tag"] = placement.tag
    if placement.id:
        values["id"] = placement.id
    extras = {k: v for k, v in placement.properties.items() if k not in REGISTER_KEY_ORDER}
    return _ordered_instance(values, extras)


def check_register(
    layout: Layout, *, instances: Sequence[Mapping[str, Any]] | None = None
) -> RegisterDrift:
    """Compare the register on disk with the bytes the pipeline would write. Never writes.

    ``instances=None`` re-canonicalises what ``load_layout`` read, catching a
    non-canonical order or a hand-edit. Passing instances derived from a fresh
    export also catches staleness against the editor — the CI gate.
    """

    if layout.register is None:
        raise LayoutError(
            f"{layout.source.name}: an inline placements list has no register to check"
        )
    try:
        on_disk = layout.register.read_text(encoding="utf-8")
    except OSError as exc:
        raise LayoutError(f"cannot read {layout.register}: {exc}") from exc
    raw = (
        list(instances)
        if instances is not None
        else [placement_instance(placement) for placement in layout.placements]
    )
    canonical = canonical_instances(raw)
    unknown = sorted({str(item["type"]) for item in canonical} - set(layout.types))
    if unknown:
        raise LayoutError(
            f"{layout.source.name}: placement type(s) with no components.types entry: "
            f"{', '.join(unknown)}"
        )
    source = _register_source(on_disk) or layout.register.name
    expected = dump_placements_register(canonical, source=source, crs=layout.crs)
    return RegisterDrift(register=layout.register, on_disk=on_disk, expected=expected)


def canonicalise_register(path: Path, *, crs: str, source: str | None = None) -> bool:
    """Rewrite a register in canonical order from its own contents. True iff it wrote."""

    register = Path(path)
    try:
        on_disk = register.read_text(encoding="utf-8")
    except OSError as exc:
        raise LayoutError(f"cannot read {register}: {exc}") from exc
    provenance = source if source is not None else _register_source(on_disk)
    if provenance is None:
        raise LayoutError(
            f"{register.name}: no '# Derived from:' line to preserve — pass "
            "--register-source <text>, or regenerate with --from-geojson"
        )
    if not provenance.strip():
        raise LayoutError(f"{register.name}: register source text must be non-empty")
    data = _read_yaml(register)
    if not isinstance(data, dict) or not isinstance(data.get("instances"), list):
        raise LayoutError(f"{register.name}: placements register needs instances: [...]")
    expected = dump_placements_register(
        canonical_instances(data["instances"]), source=provenance, crs=crs
    )
    if expected == on_disk:
        return False
    register.write_text(expected, encoding="utf-8", newline="\n")
    return True


def _normalise_instance(instance: Mapping[str, Any], ctx: str, dp: int) -> dict[str, Any]:
    if not isinstance(instance, Mapping):
        raise LayoutError(f"{ctx} must be a mapping")
    for key in instance:
        if not isinstance(key, str):
            raise LayoutError(f"{ctx}: key {key!r} must be a string")

    type_name = instance.get("type")
    if not isinstance(type_name, str) or not type_name.strip():
        raise LayoutError(f"{ctx}.type must be a non-empty string")
    if "origin_utm" not in instance:
        raise LayoutError(f"{ctx} ({type_name}) missing origin_utm")
    origin = _coord(instance["origin_utm"], f"{ctx}.origin_utm")
    rotation = instance.get("rotation_deg", 0.0)
    if isinstance(rotation, bool) or not isinstance(rotation, (int, float)):
        raise LayoutError(f"{ctx}.rotation_deg must be a number")

    values: dict[str, Any] = {
        "type": type_name,
        "origin_utm": [round_reg(origin[0], dp), round_reg(origin[1], dp)],
        "rotation_deg": round_reg(float(rotation), dp),
    }
    size = instance.get("size_m")
    if size is not None:
        size_m = _coord(size, f"{ctx}.size_m")
        values["size_m"] = [round_reg(size_m[0], dp), round_reg(size_m[1], dp)]

    descriptor = _describe_record(
        type_name, (values["origin_utm"][0], values["origin_utm"][1]), values["rotation_deg"]
    )
    tag = _optional_identity(instance.get("tag"), f"{ctx}.tag", descriptor)
    if tag is not None:
        values["tag"] = tag
    placement_id = _optional_identity(instance.get("id"), f"{ctx}.id", descriptor)
    if placement_id is not None:
        values["id"] = placement_id

    extras = {key: instance[key] for key in instance if key not in REGISTER_KEY_ORDER}
    return _ordered_instance(values, extras)


def _ordered_instance(values: Mapping[str, Any], extras: Mapping[str, Any]) -> dict[str, Any]:
    """REGISTER_KEY_ORDER first, then any remaining properties alphabetically."""

    ordered = {key: values[key] for key in REGISTER_KEY_ORDER if key in values}
    for key in sorted(extras):
        ordered[key] = extras[key]
    return ordered


def _optional_identity(value: Any, ctx: str, descriptor: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise LayoutError(f"{ctx} must be a string")
    stripped = value.strip()
    if not stripped:
        return None
    if not ID_PATTERN.fullmatch(stripped):
        raise LayoutError(
            f"{ctx}: invalid identity {stripped!r} on {descriptor} — an identity must match "
            "[A-Za-z0-9][A-Za-z0-9._-]{0,63} to stay safe unquoted in YAML/CSV/DXF/paths"
        )
    return stripped


def _validate_identities_are_unique(instances: Sequence[Mapping[str, Any]]) -> None:
    """Case-insensitive, flat namespace: a BOQ join key must not need the type."""

    seen: dict[str, tuple[str, Mapping[str, Any]]] = {}
    for instance in instances:
        identity = _instance_identity(instance)
        if identity is None:
            continue
        folded = identity.casefold()
        if folded in seen:
            first_identity, first = seen[folded]
            raise LayoutError(
                f"duplicate placement identity {identity!r} (case-insensitively equal to "
                f"{first_identity!r}): {_describe_instance(first)} and "
                f"{_describe_instance(instance)} claim one durable id — clear or change the "
                "pasted feature's id/tag"
            )
        seen[folded] = (identity, instance)


def _validate_poses_are_unique(instances: Sequence[Mapping[str, Any]]) -> None:
    """A full-key tie is two items at one pose to the millimetre — a never-moved paste."""

    seen: set[tuple[Any, ...]] = set()
    for instance in instances:
        key = _canonical_sort_key(instance)
        if key in seen:
            raise LayoutError(
                f"duplicate placement: 2 × {str(instance['type'])!r} at the same pose "
                f"({instance['origin_utm'][0]:.3f}, {instance['origin_utm'][1]:.3f}) "
                f"@ {instance['rotation_deg']:.3f}° — a pasted copy was probably never moved"
            )
        seen.add(key)


def _canonical_sort_key(instance: Mapping[str, Any]) -> tuple[Any, ...]:
    size = instance.get("size_m")
    # absent sorts before present, and 0.0 never None: mixing None with float raises
    size_key = (False, 0.0, 0.0) if size is None else (True, round_reg(size[0]), round_reg(size[1]))
    identity = _instance_identity(instance)
    identity_key = (identity is not None, (identity or "").casefold(), identity or "")
    return (
        instance["type"],
        round_reg(instance["origin_utm"][0]),
        round_reg(instance["origin_utm"][1]),
        round_reg(instance["rotation_deg"]),
        size_key,
        identity_key,
    )


def _instance_identity(instance: Mapping[str, Any]) -> str | None:
    placement_id = instance.get("id")
    if isinstance(placement_id, str) and placement_id:
        return placement_id
    tag = instance.get("tag")
    if isinstance(tag, str) and tag:
        return tag
    return None


def _describe_instance(instance: Mapping[str, Any]) -> str:
    return _describe_record(
        str(instance.get("type")),
        (float(instance["origin_utm"][0]), float(instance["origin_utm"][1])),
        float(instance.get("rotation_deg", 0.0)),
    )


def _describe_record(type_name: str, origin: Point, rotation_deg: float) -> str:
    return f"{type_name} at ({origin[0]:.3f}, {origin[1]:.3f}) @ {rotation_deg:.3f}°"


def _instances_are_canonical(
    instances: Sequence[Mapping[str, Any]], canonical: Sequence[Mapping[str, Any]]
) -> bool:
    if len(instances) != len(canonical):
        return False
    for raw, expected in zip(instances, canonical):
        if not isinstance(raw, Mapping) or list(raw.keys()) != list(expected.keys()):
            return False
        for key, expected_value in expected.items():
            # repr, not ==, for the reserved keys: 0.0 == -0.0 and 40 == 40.0,
            # but they emit different bytes.
            if key in REGISTER_KEY_ORDER:
                if repr(raw[key]) != repr(expected_value):
                    return False
            elif raw[key] != expected_value:
                return False
    return True


def _register_is_generated(path: Path) -> bool:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise LayoutError(f"cannot read {path}: {exc}") from exc
    return any(line.startswith(_GENERATED_REGISTER_PREFIX) for line in text.splitlines())


def _register_source(text: str) -> str | None:
    for line in text.splitlines():
        match = _DERIVED_RE.match(line)
        if match:
            return match.group("source")
    return None


# --------------------------------------------------------------------------- #
# derive a placements register from a GIS export
# --------------------------------------------------------------------------- #


def footprint_pose(ring: list[Any]) -> tuple[Point, float, tuple[float, float]]:
    """Return (centroid, long-axis bearing in degrees, (long, short)) for a quad ring.

    The ring is a GeoJSON linear ring (first vertex repeated or not). Rotation is
    measured from east, counter-clockwise, normalised to [0, 180) — a rectangle
    has no front, so a bearing and its reverse are the same pose.
    """

    points = [(float(x), float(y)) for x, y, *_ in ring]
    if len(points) >= 2 and points[0] == points[-1]:
        points = points[:-1]
    if len(points) != 4:
        raise LayoutError(f"footprint must be a 4-corner rectangle, got {len(points)} corners")

    cx = sum(p[0] for p in points) / 4.0
    cy = sum(p[1] for p in points) / 4.0
    edge_a = math.dist(points[0], points[1])
    edge_b = math.dist(points[1], points[2])
    if edge_a >= edge_b:
        long_edge, short_edge = edge_a, edge_b
        dx, dy = points[1][0] - points[0][0], points[1][1] - points[0][1]
    else:
        long_edge, short_edge = edge_b, edge_a
        dx, dy = points[2][0] - points[1][0], points[2][1] - points[1][1]
    rotation = math.degrees(math.atan2(dy, dx)) % 180.0
    return (cx, cy), rotation, (long_edge, short_edge)


def derive_placements(
    geojson: dict[str, Any],
    *,
    type_field: str = "type",
    tag_field: str = "tag",
    id_field: str = "id",
    exclude: Iterable[dict[str, Any]] = (),
    round_to: int = 3,
) -> list[dict[str, Any]]:
    """Derive placement instances from a GeoJSON export of an editable layer.

    ``exclude`` holds named parked-item zones — ``{name, bbox: [xmin, ymin,
    xmax, ymax]}`` — so a palette row is skipped by *declared intent* rather
    than by a magic coordinate band buried in a script.

    Return order stays **source order**: this is a pure reader with a documented
    public contract, and canonicalisation is the *writer's* job. Identity syntax,
    uniqueness and pose uniqueness are enforced here all the same — a bad export
    fails before a byte is written.
    """

    features = geojson.get("features")
    if not isinstance(features, list):
        raise LayoutError("GeoJSON export has no features list")

    zones = [_exclude_zone(zone, index) for index, zone in enumerate(exclude)]
    instances: list[dict[str, Any]] = []
    for index, feature in enumerate(features):
        if not isinstance(feature, Mapping):
            raise LayoutError(f"features[{index}]: must be a GeoJSON feature mapping")
        geometry = feature.get("geometry") or {}
        if geometry.get("type") != "Polygon":
            raise LayoutError(
                f"features[{index}]: expected Polygon footprints, got {geometry.get('type')!r}"
            )
        rings = geometry.get("coordinates") or []
        if not rings:
            raise LayoutError(f"features[{index}]: empty Polygon")
        (cx, cy), rotation, (long_edge, short_edge) = footprint_pose(rings[0])

        properties = feature.get("properties") or {}
        if not isinstance(properties, Mapping):
            raise LayoutError(f"features[{index}]: properties must be a mapping")
        type_name = properties.get(type_field)
        if not isinstance(type_name, str) or not type_name.strip():
            raise LayoutError(f"features[{index}]: missing {type_field!r} property")

        parked = next((z for z in zones if _in_bbox((cx, cy), z["bbox"])), None)
        if parked is not None:
            continue

        instance: dict[str, Any] = {
            "type": type_name,
            "origin_utm": [round_reg(cx, round_to), round_reg(cy, round_to)],
            "rotation_deg": round_reg(rotation, round_to),
            "size_m": [round_reg(long_edge, round_to), round_reg(short_edge, round_to)],
        }
        tag = properties.get(tag_field)
        if isinstance(tag, str) and tag.strip():
            instance["tag"] = tag.strip()
        placement_id = properties.get(id_field)
        if placement_id is not None:
            if not isinstance(placement_id, str):
                raise LayoutError(f"features[{index}]: {id_field!r} property must be a string")
            if placement_id.strip():
                instance["id"] = placement_id.strip()
        instances.append(instance)
    # Validate here, discarding the sorted result: an export with a duplicate
    # identity or a never-moved paste must fail before anything is written.
    canonical_instances(instances)
    return instances


def _exclude_zone(zone: Any, index: int) -> dict[str, Any]:
    if not isinstance(zone, dict) or "bbox" not in zone:
        raise LayoutError(f"exclude[{index}] must be a mapping with a bbox")
    bbox = zone["bbox"]
    if not isinstance(bbox, list) or len(bbox) != 4:
        raise LayoutError(f"exclude[{index}].bbox must be [xmin, ymin, xmax, ymax]")
    return {"name": str(zone.get("name", f"zone-{index}")), "bbox": [float(v) for v in bbox]}


def _in_bbox(point: Point, bbox: list[float]) -> bool:
    xmin, ymin, xmax, ymax = bbox
    return xmin <= point[0] <= xmax and ymin <= point[1] <= ymax


def dump_placements_register(
    instances: Sequence[Mapping[str, Any]],
    *,
    source: str,
    crs: str = "EPSG:32630",
    assert_canonical: bool = True,
) -> str:
    """Render a generated placements register — text-as-truth, diffable in git.

    Refuses unsorted input rather than quietly sorting it: silent reordering
    inside a function named ``dump`` is how the two ends of the round-trip drift
    apart. Call ``canonical_instances()`` first.
    """

    canonical = canonical_instances(instances)
    if assert_canonical and not _instances_are_canonical(list(instances), canonical):
        raise LayoutError(
            "register instances are not in canonical order; call canonical_instances() first"
        )
    with_identity, total = id_coverage(canonical)
    # The identity gap becomes a diffable line that moves when the gap closes,
    # instead of a fact living in an agent's head.
    if with_identity == total:
        ids_line = f"# ids: {with_identity} of {total} placements carry a durable id.\n"
    else:
        ids_line = (
            f"# ids: {with_identity} of {total} placements carry a durable id "
            f"({total - with_identity} identified by pose only).\n"
        )
    header = (
        "# GENERATED placements register — do NOT hand-edit.\n"
        f"# Derived from: {source}\n"
        f"# CRS: {crs}. origin_utm = footprint centroid; rotation_deg = long-axis\n"
        "# bearing from east, normalised to [0, 180). size_m = [long, short] as placed.\n"
        f"{ids_line}"
        "# Regenerate with: technical_drawings_for_agents layout <layout.yaml> --from-geojson <export.geojson>\n"
    )
    # allow_unicode=True is load-bearing, not cosmetic: yaml.safe_dump defaults to
    # False and escapes every non-ASCII character, so a free-form property reading
    # "Décanteur Ø—1" lands on disk as "D\xE9canteur \xD8—1". An escaped
    # register is not human-reviewable, which defeats the whole text-as-truth
    # rationale for generating one — and Ghanaian and Portuguese names and
    # descriptions carry accents routinely. Canonical text policy: UTF-8, LF, no BOM.
    body = yaml.safe_dump(
        {"instances": canonical},
        sort_keys=False,
        default_flow_style=None,
        width=100,
        allow_unicode=True,
    )
    return header + body


# --------------------------------------------------------------------------- #
# build
# --------------------------------------------------------------------------- #


def snap_groups(layout: Layout) -> tuple[Layout, list[str]]:
    """Apply every ``groups:`` snap rule, returning the layout and a move report.

    Members are laid out on a common bearing, evenly pitched along the axis
    perpendicular to it, about the group's own centroid — so the group stays
    where the human put it while its internal geometry meets the spec.

    Never silent: every member that moved is reported with how far, because a
    large correction means the placement was further out than its author
    realised, which is worth seeing.
    """

    if not layout.groups:
        return layout, []

    placements = list(layout.placements)
    report: list[str] = []
    for group in layout.groups:
        members = [
            (index, placement)
            for index, placement in enumerate(placements)
            if placement.type in set(group.within)
        ]
        if len(members) < 2:
            report.append(f"{group.name}: {len(members)} member(s) — nothing to snap")
            continue

        bearings = [placement.rotation_deg % 180.0 for _, placement in members]
        common = bearings[0] if group.bearing == "first" else _mean_bearing(bearings)

        pitch = _group_pitch(group, members)
        ordered = _order_members(members, group.order, common)

        cx = sum(placement.origin[0] for _, placement in members) / len(members)
        cy = sum(placement.origin[1] for _, placement in members) / len(members)
        angle = math.radians(common)
        # cross axis: perpendicular to the common bearing
        ux, uy = -math.sin(angle), math.cos(angle)
        span = pitch * (len(ordered) - 1)

        for slot, (index, placement) in enumerate(ordered):
            offset = span / 2.0 - slot * pitch
            new_origin = (cx + ux * offset, cy + uy * offset)
            moved = math.dist(placement.origin, new_origin)
            turned = _angle_delta(placement.rotation_deg, common)
            placements[index] = Placement(
                type=placement.type,
                origin=new_origin,
                rotation_deg=common,
                tag=placement.tag,
                id=placement.id,
                size_m=placement.size_m,
                properties={**placement.properties, "snapped_by": group.name},
            )
            if moved > SNAP_REPORT_MIN_M or turned > SNAP_REPORT_MIN_DEG:
                report.append(
                    f"{group.name}: {placement.label} moved {moved:.3f} m, "
                    f"turned {turned:.3f}° -> bearing {common:.3f}°"
                )

        report.append(
            f"{group.name}: {len(ordered)} member(s) on bearing {common:.3f}°, "
            f"pitch {pitch:.3f} m ({group.pitch})"
        )

    snapped = Layout(
        id=layout.id,
        crs=layout.crs,
        components_root=layout.components_root,
        types=layout.types,
        placements=placements,
        checks=layout.checks,
        source=layout.source,
        editor=layout.editor,
        register=layout.register,
        groups=layout.groups,
    )
    return snapped, report


def _mean_bearing(bearings: list[float]) -> float:
    """Circular mean over undirected bearings (period 180°, so double the angle)."""

    sin_sum = sum(math.sin(math.radians(2 * b)) for b in bearings)
    cos_sum = sum(math.cos(math.radians(2 * b)) for b in bearings)
    return (math.degrees(math.atan2(sin_sum, cos_sum)) / 2.0) % 180.0


def _group_pitch(group: Group, members: list[tuple[int, Placement]]) -> float:
    """Centre-to-centre spacing along the cross axis."""

    sizes = [placement.size_m for _, placement in members]
    if group.pitch == "spec":
        if any(size is None for size in sizes):
            raise LayoutError(
                f"group {group.name!r}: pitch 'spec' needs every member's size_m "
                "(short edge + clear_m); regenerate the register with --from-geojson"
            )
        shorts = {round(size[1], 6) for size in sizes}  # type: ignore[index]
        if len(shorts) > 1:
            raise LayoutError(
                f"group {group.name!r}: pitch 'spec' needs members of one size across the "
                f"cross axis, got short edges {sorted(shorts)}"
            )
        return shorts.pop() + group.clear_m

    # as-placed: preserve the mean centre-to-centre spacing the human created
    origins = [placement.origin for _, placement in members]
    gaps = [math.dist(a, b) for a, b in zip(origins, origins[1:])]
    return sum(gaps) / len(gaps)


def _order_members(
    members: list[tuple[int, Placement]], order: str, bearing: float
) -> list[tuple[int, Placement]]:
    if order == "as-listed":
        return members
    angle = math.radians(bearing)
    ux, uy = -math.sin(angle), math.cos(angle)
    # sort by projection onto the cross axis; that axis points "up-ish" for any
    # bearing in [0, 180), so descending projection is north-to-south
    ranked = sorted(
        members, key=lambda item: item[1].origin[0] * ux + item[1].origin[1] * uy, reverse=True
    )
    return ranked if order == "north-to-south" else list(reversed(ranked))


def build_layout(layout: Layout, view: str = "plan") -> list[tuple[Placement, PlacedFeature]]:
    """Expand every placement into placed component geometry, in world metres."""

    placed: list[tuple[Placement, PlacedFeature]] = []
    for placement in layout.placements:
        for spec_path in layout.types[placement.type]:
            component = load_component(spec_path)
            try:
                features = place(
                    component,
                    view,
                    origin=placement.origin,
                    rotation_deg=placement.rotation_deg,
                )
            except (KeyError, ComponentSpecError) as exc:
                raise LayoutError(
                    f"{placement.label} ({placement.type}): component "
                    f"{spec_path.name} has no {view!r} view ({exc})"
                ) from exc
            placed.extend((placement, feature) for feature in features)
    return placed


def layout_instance_properties(placement: Placement) -> dict[str, Any]:
    """Per-instance GeoJSON properties for a placement (stable key order).

    The instance tag is emitted as ``placed_tag``, deliberately NOT as ``tag``:
    instance properties override feature properties downstream, so writing the
    instance tag to ``tag`` would erase every component feature's own tag — an
    FA-130's nozzle ``N1`` would come out labelled with the unit's tag. Both
    facts matter, so they get separate keys. The identity is emitted as
    ``placed_id`` for exactly the same reason — a bare ``id`` would clobber
    every component feature's own.
    """

    properties: dict[str, Any] = {"placed_type": placement.type}
    if placement.tag:
        properties["placed_tag"] = placement.tag
    if placement.id:
        properties["placed_id"] = placement.id
    properties.update(
        {k: placement.properties[k] for k in sorted(placement.properties) if k not in {"tag", "id"}}
    )
    return properties


# --------------------------------------------------------------------------- #
# checks
# --------------------------------------------------------------------------- #


def check_layout(layout: Layout) -> list[Finding]:
    """Run every configured layout rule. Empty list means the layout is clean."""

    findings: list[Finding] = []
    for check in layout.checks:
        params = check.params
        if check.kind == "parallel":
            findings.extend(_check_parallel(layout, params))
        elif check.kind == "clear-spacing":
            findings.extend(_check_clear_spacing(layout, params))
        elif check.kind == "no-overlap":
            findings.extend(_check_no_overlap(layout, params))
        elif check.kind == "within-envelope":
            findings.extend(_check_within_envelope(layout, params))
        elif check.kind == "ids-present":
            findings.extend(_check_ids_present(layout, params))
        else:  # pragma: no cover - guarded by _load_checks
            raise LayoutError(f"unhandled check {check.kind!r}")
    return findings


def _severity(params: dict[str, Any], default: Severity = "error") -> Severity:
    value = params.get("severity", default)
    if value not in ("error", "warn"):
        raise LayoutError(f"check severity must be 'error' or 'warn', got {value!r}")
    return value  # type: ignore[return-value]


def _selected(layout: Layout, params: dict[str, Any], key: str = "within") -> list[Placement]:
    wanted = params.get(key)
    if wanted is None:
        return list(layout.placements)
    if isinstance(wanted, str):
        wanted = [wanted]
    if not isinstance(wanted, list):
        raise LayoutError(f"check {key!r} must be a type name or list of type names")
    names = {str(item) for item in wanted}
    unknown = sorted(names - set(layout.types))
    if unknown:
        raise LayoutError(f"check {key!r} names unknown type(s): {', '.join(unknown)}")
    return [p for p in layout.placements if p.type in names]


def _check_parallel(layout: Layout, params: dict[str, Any]) -> list[Finding]:
    tol = float(params.get("tol_deg", 1.0))
    severity = _severity(params)
    selected = _selected(layout, params)
    if len(selected) < 2:
        return []
    reference = selected[0]
    findings: list[Finding] = []
    for other in selected[1:]:
        delta = _angle_delta(reference.rotation_deg, other.rotation_deg)
        if delta > tol:
            findings.append(
                Finding(
                    severity,
                    "parallel",
                    f"{other.label} is {delta:.2f}° off {reference.label} "
                    f"({other.rotation_deg:.2f}° vs {reference.rotation_deg:.2f}°, tol {tol:g}°)",
                )
            )
    return findings


def _check_clear_spacing(layout: Layout, params: dict[str, Any]) -> list[Finding]:
    if "min_m" not in params:
        raise LayoutError("clear-spacing check requires min_m")
    minimum = float(params["min_m"])
    tol = float(params.get("tol_m", 0.0))
    severity = _severity(params)
    selected = _selected(layout, params)
    findings: list[Finding] = []
    for first, second in _pairs(selected):
        gap = _polygon_gap(_footprint(first), _footprint(second))
        if gap + 1e-9 < minimum - tol:
            findings.append(
                Finding(
                    severity,
                    "clear-spacing",
                    f"{first.label} ↔ {second.label} clear gap {gap:.3f} m "
                    f"< required {minimum:g} m",
                )
            )
    return findings


def _check_no_overlap(layout: Layout, params: dict[str, Any]) -> list[Finding]:
    severity = _severity(params)
    selected = _selected(layout, params)
    findings: list[Finding] = []
    for first, second in _pairs(selected):
        if _rects_overlap(_footprint(first), _footprint(second)):
            findings.append(
                Finding(
                    severity,
                    "no-overlap",
                    f"{first.label} overlaps {second.label}",
                )
            )
    return findings


def _check_within_envelope(layout: Layout, params: dict[str, Any]) -> list[Finding]:
    bbox = params.get("bbox")
    if not isinstance(bbox, list) or len(bbox) != 4:
        raise LayoutError("within-envelope check requires bbox: [xmin, ymin, xmax, ymax]")
    envelope = [float(value) for value in bbox]
    severity = _severity(params)
    findings: list[Finding] = []
    for placement in _selected(layout, params):
        outside = [corner for corner in _footprint(placement) if not _in_bbox(corner, envelope)]
        if outside:
            findings.append(
                Finding(
                    severity,
                    "within-envelope",
                    f"{placement.label}: {len(outside)} of 4 footprint corners fall "
                    f"outside the envelope {envelope}",
                )
            )
    return findings


def _check_ids_present(layout: Layout, params: dict[str, Any]) -> list[Finding]:
    """Declared-only identity gate: one finding per placement with no durable id.

    Opt-in because the tool never invents an identity, and the live registers
    have a real gap that has to be closed by an author, not by the tool.
    """

    severity = _severity(params)
    findings: list[Finding] = []
    for placement in _selected(layout, params):
        if not placement.has_durable_id:
            findings.append(
                Finding(
                    severity,
                    "ids-present",
                    f"{placement.type} at ({placement.origin[0]:.3f}, "
                    f"{placement.origin[1]:.3f}) @ {placement.rotation_deg:.3f}° has no "
                    "durable id (set `tag` or `id` on the editor feature)",
                )
            )
    return findings


# --------------------------------------------------------------------------- #
# geometry helpers (rotated-rectangle footprints; no external geometry dep)
# --------------------------------------------------------------------------- #


def _footprint(placement: Placement) -> list[Point]:
    """The placed footprint rectangle corners, world metres.

    Uses ``size_m`` when the register carries it (the as-placed footprint read
    back from the editor). Without a size there is no footprint to test — a
    check that needs one says so rather than assuming a dimension.
    """

    if placement.size_m is None:
        raise LayoutError(
            f"{placement.label} ({placement.type}) has no size_m — spacing/overlap checks "
            "need the as-placed footprint; regenerate the register with --from-geojson"
        )
    long_edge, short_edge = placement.size_m
    angle = math.radians(placement.rotation_deg)
    cos_a, sin_a = math.cos(angle), math.sin(angle)
    half_long, half_short = long_edge / 2.0, short_edge / 2.0
    cx, cy = placement.origin
    corners: list[Point] = []
    for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
        lx, ly = sx * half_long, sy * half_short
        corners.append((cx + lx * cos_a - ly * sin_a, cy + lx * sin_a + ly * cos_a))
    return corners


def footprint(placement: Placement) -> list[Point]:
    """Public alias for the placed footprint rectangle (see :func:`_footprint`).

    Exists so a caller outside this module (``components/dimensions.py``) shares the
    *same function object* the spacing and overlap checks use, rather than reaching
    for a private name — which invites a future "tidy-up" into a local copy, and a
    local copy is exactly the divergence between drawn geometry and printed number
    that dimensioning-from-geometry exists to eliminate.
    """

    return _footprint(placement)


def polygon_gap(a: list[Point], b: list[Point]) -> float:
    """Public alias for the minimum clear distance between two convex polygons.

    The scalar a ``clearance`` dimension prints is this function's return value, so a
    sheet cannot contradict its own ``clear-spacing`` check. See :func:`footprint`.
    """

    return _polygon_gap(a, b)


def _pairs(items: list[Placement]) -> Iterable[tuple[Placement, Placement]]:
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            yield items[i], items[j]


def _angle_delta(first: float, second: float) -> float:
    """Smallest angle between two undirected bearings, in [0, 90]."""

    delta = abs(first - second) % 180.0
    return min(delta, 180.0 - delta)


def _rects_overlap(a: list[Point], b: list[Point]) -> bool:
    """Separating-axis test for two convex polygons (exact for rectangles)."""

    for polygon in (a, b):
        for index in range(len(polygon)):
            x1, y1 = polygon[index]
            x2, y2 = polygon[(index + 1) % len(polygon)]
            axis = (-(y2 - y1), x2 - x1)
            norm = math.hypot(*axis)
            if norm == 0:
                continue
            axis = (axis[0] / norm, axis[1] / norm)
            a_min, a_max = _project(a, axis)
            b_min, b_max = _project(b, axis)
            if a_max <= b_min + 1e-9 or b_max <= a_min + 1e-9:
                return False
    return True


def _project(polygon: list[Point], axis: Point) -> tuple[float, float]:
    values = [point[0] * axis[0] + point[1] * axis[1] for point in polygon]
    return min(values), max(values)


def _polygon_gap(a: list[Point], b: list[Point]) -> float:
    """Minimum clear distance between two convex polygons (0.0 if they touch)."""

    if _rects_overlap(a, b):
        return 0.0
    best = math.inf
    for polygon, other in ((a, b), (b, a)):
        for index in range(len(polygon)):
            p1 = polygon[index]
            p2 = polygon[(index + 1) % len(polygon)]
            for point in other:
                best = min(best, _point_segment_distance(point, p1, p2))
    return best


def _point_segment_distance(point: Point, start: Point, end: Point) -> float:
    px, py = point
    x1, y1 = start
    x2, y2 = end
    dx, dy = x2 - x1, y2 - y1
    length_sq = dx * dx + dy * dy
    if length_sq == 0:
        return math.dist(point, start)
    t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / length_sq))
    return math.dist(point, (x1 + t * dx, y1 + t * dy))
