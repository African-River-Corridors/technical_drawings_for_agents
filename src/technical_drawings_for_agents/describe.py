"""Describe a finished DXF in comparable, deterministic terms.

Everything else in this package *authors* a drawing and then proves the thing it
authored. This module reads a DXF nobody here wrote and states what is in it:
layers and their entity counts, paper-space layouts, and — the reason this exists
— **the plot scale each viewport actually holds**.

Two jobs, one primitive:

1. **Gating a drawing produced elsewhere.** `check` and `validate` take a drawing
   directory or an SVG, so a finished DXF from another toolchain could not be
   assessed at all. A generator that states a scale in its title block and draws
   its scale bar from the same number cannot disagree with *itself*; what nothing
   verified was whether the viewport plots at the scale the sheet claims. That is
   the 1:1250-against-1:1157 defect, and it is measurable from the file.

2. **An acceptance bar for a port.** Byte-comparing DXFs does not work — ezdxf
   writes handles and timestamps, and P3 already established that a format enters
   a byte-identity assertion only if the tool declares it reproducible. Entity-level
   facts do work, and they fail *specifically*: "layer ANK-PIPE-OUT lost 3 entities"
   is actionable in a way a pixel diff is not.

Nothing here is derived from anything the drawing merely asserts. The scale comes
from viewport geometry and the header's unit code, never from title-block text.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

# DXF $INSUNITS code for millimetres. Paper space is millimetres in every sheet
# this toolkit produces, so mm is the target of the model->paper conversion below.
_UNITS_MM = 4

# $INSUNITS == 0 means "unitless". A model-unit length with no unit cannot be
# converted to paper millimetres, so a scale CANNOT be computed. It is reported as
# unknown rather than guessed: assuming metres here would silently invent the very
# number this module exists to verify.
_UNITS_UNITLESS = 0

# In DXF, every paper-space layout carries one VIEWPORT pseudo-entity with id 1
# that represents the sheet itself rather than a window onto the model. Its
# view_height equals its height, so it always yields a meaningless 1:1 and would
# swamp the real windows. Excluded by id, which is the documented discriminator.
_LAYOUT_PSEUDO_VIEWPORT_ID = 1


class DescribeError(RuntimeError):
    """Raised when a DXF cannot be read or described."""


def _round(value: float, places: int = 3) -> float:
    """Round for reporting.

    Comparison never leans on this — `compare_description` applies an explicit
    tolerance to the full-precision value. Rounding here is so two descriptions of
    the same drawing render identically in a diff, not so they compare equal.
    """
    return round(float(value), places)


@dataclass(frozen=True)
class ViewportFacts:
    """One paper-space viewport, and the scale its geometry actually holds."""

    layout: str
    viewport_id: int
    paper_width_mm: float
    paper_height_mm: float
    view_height_units: float
    plot_scale: float | None
    """1:N expressed as the float N, or None when the header declares no units."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "layout": self.layout,
            "viewport_id": self.viewport_id,
            "paper_mm": [_round(self.paper_width_mm), _round(self.paper_height_mm)],
            "view_height_units": _round(self.view_height_units, 6),
            "plot_scale": None if self.plot_scale is None else _round(self.plot_scale, 4),
        }


@dataclass(frozen=True)
class LayoutFacts:
    """A paper-space layout — one sheet."""

    name: str
    entities: int
    viewports: tuple[ViewportFacts, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "entities": self.entities,
            "viewports": [vp.to_dict() for vp in self.viewports],
        }


@dataclass(frozen=True)
class DxfDescription:
    """Deterministic, comparable facts about a finished DXF."""

    path: Path
    dxf_version: str
    insunits: int
    insunits_name: str
    modelspace_entities: int
    layers: tuple[tuple[str, int], ...]
    """(layer name, entity count) over modelspace AND every layout, sorted by name."""
    entity_types: tuple[tuple[str, int], ...]
    """(DXF type, count) over modelspace only, sorted by type."""
    layouts: tuple[LayoutFacts, ...]
    block_count: int
    block_resident_entities: int
    """Entities inside block DEFINITIONS, which `modelspace_entities` excludes.

    Reported because the gap between the two is enormous and silent. Measured on
    a vendor process-plant sheet `F2024E001-454`: 3,089 in modelspace against 259,762
    inside 212 block definitions reached by 21 INSERTs — an entity count taken from
    modelspace alone understates that drawing by ~84x. Our own generators emit
    mostly flat modelspace geometry, so the two numbers are close; a vendor drawing
    is where reading one and assuming the other goes wrong.
    """

    def to_dict(self) -> dict[str, Any]:
        """A stable mapping. Key order and sequence order are both deterministic,
        so two runs over the same file produce identical YAML/JSON."""
        return {
            "dxf_version": self.dxf_version,
            "insunits": self.insunits,
            "insunits_name": self.insunits_name,
            "modelspace_entities": self.modelspace_entities,
            "block_count": self.block_count,
            "block_resident_entities": self.block_resident_entities,
            "layers": {name: count for name, count in self.layers},
            "entity_types": {name: count for name, count in self.entity_types},
            "layouts": [layout.to_dict() for layout in self.layouts],
        }

    @property
    def stated_scales(self) -> tuple[float, ...]:
        """Every computable viewport scale, deduplicated and sorted.

        Key-map and detail insets legitimately sit at their own scales, so a sheet
        having several is normal — this is a summary, not an assertion.
        """
        seen = {
            vp.plot_scale
            for layout in self.layouts
            for vp in layout.viewports
            if vp.plot_scale is not None
        }
        return tuple(sorted(seen))


def _units_name(code: int) -> str:
    """Human-readable unit name for an $INSUNITS code, via ezdxf's own table."""
    from ezdxf import units

    if code == _UNITS_UNITLESS:
        return "unitless"
    try:
        return str(units.decode(code))
    except Exception:  # noqa: BLE001 - an unknown code is data, not a crash
        return f"code-{code}"


def _model_to_mm_factor(insunits: int) -> float | None:
    """Model units -> paper millimetres, or None when no conversion is defined.

    Returns None for a unitless header rather than defaulting: a wrong factor here
    produces a confidently wrong scale, which is worse than an honest gap.
    """
    if insunits == _UNITS_UNITLESS:
        return None
    from ezdxf import units

    try:
        return float(units.conversion_factor(insunits, _UNITS_MM))
    except Exception:  # noqa: BLE001 - an unusable unit code is data, not a crash
        return None


def _viewport_facts(
    layout_name: str, viewport, factor: float | None
) -> ViewportFacts | None:
    """Facts for one VIEWPORT entity, or None if it is the layout pseudo-viewport."""
    dxf = viewport.dxf
    viewport_id = int(getattr(dxf, "id", 0) or 0)
    if viewport_id == _LAYOUT_PSEUDO_VIEWPORT_ID:
        return None

    height_mm = float(getattr(dxf, "height", 0.0) or 0.0)
    width_mm = float(getattr(dxf, "width", 0.0) or 0.0)
    view_height = float(getattr(dxf, "view_height", 0.0) or 0.0)

    # A zero-height viewport or an unconvertible header both mean the scale is not
    # computable. Report the geometry and leave the scale None.
    scale: float | None = None
    if factor is not None and height_mm > 0.0 and view_height > 0.0:
        scale = (view_height * factor) / height_mm

    return ViewportFacts(
        layout=layout_name,
        viewport_id=viewport_id,
        paper_width_mm=width_mm,
        paper_height_mm=height_mm,
        view_height_units=view_height,
        plot_scale=scale,
    )


def describe_dxf(path: str | Path) -> DxfDescription:
    """Read a DXF and return its comparable facts.

    Reading goes through :meth:`DxfBuilder.read_dxf`, which already handles the
    recover-on-malformed fallback that vendor and third-party files need. This
    module does not open files itself.
    """
    from .dxf import DxfBuilder

    path = Path(path)
    if not path.is_file():
        raise DescribeError(f"not a file: {path}")
    try:
        doc = DxfBuilder.read_dxf(path)
    except Exception as exc:
        raise DescribeError(f"cannot read '{path}': {exc}") from exc

    insunits = int(doc.header.get("$INSUNITS", 0) or 0)
    factor = _model_to_mm_factor(insunits)

    layer_counts: dict[str, int] = {}
    type_counts: dict[str, int] = {}

    msp = doc.modelspace()
    modelspace_entities = 0
    for entity in msp:
        modelspace_entities += 1
        layer = str(getattr(entity.dxf, "layer", "") or "")
        layer_counts[layer] = layer_counts.get(layer, 0) + 1
        dxftype = entity.dxftype()
        type_counts[dxftype] = type_counts.get(dxftype, 0) + 1

    layouts: list[LayoutFacts] = []
    # Tab order is the order a human sees in CAD, and it is stable in the file, so
    # it is the right traversal for a description meant to be diffed.
    for name in doc.layout_names_in_taborder():
        if name.lower() == "model":
            continue
        layout = doc.layout(name)
        entities = 0
        for entity in layout:
            entities += 1
            layer = str(getattr(entity.dxf, "layer", "") or "")
            layer_counts[layer] = layer_counts.get(layer, 0) + 1
        viewports = tuple(
            facts
            for facts in (
                _viewport_facts(name, vp, factor) for vp in layout.viewports()
            )
            if facts is not None
        )
        layouts.append(
            LayoutFacts(name=name, entities=entities, viewports=viewports)
        )

    # Block definitions exclude the *Model_Space / *Paper_Space records, which are
    # layout plumbing rather than content and would make the count meaningless.
    block_count = 0
    block_resident_entities = 0
    for block in doc.blocks:
        if block.name.lower().startswith("*"):
            continue
        block_count += 1
        block_resident_entities += sum(1 for _ in block)

    return DxfDescription(
        path=path,
        dxf_version=str(doc.dxfversion),
        insunits=insunits,
        insunits_name=_units_name(insunits),
        modelspace_entities=modelspace_entities,
        layers=tuple(sorted(layer_counts.items())),
        entity_types=tuple(sorted(type_counts.items())),
        layouts=tuple(layouts),
        block_count=block_count,
        block_resident_entities=block_resident_entities,
    )


# --------------------------------------------------------------------------- #
# comparison
# --------------------------------------------------------------------------- #


def compare_description(
    expected: dict[str, Any],
    actual: dict[str, Any],
    *,
    scale_tolerance: float = 0.5,
) -> list[str]:
    """Compare an expectation against a description; return findings, empty if none.

    **The expectation may be partial, and that is the point.** Only keys present in
    `expected` are asserted. An assertion harness that demanded an exhaustive
    expectation would go stale on the first legitimate change and be switched off —
    so a golden file can pin the three facts that matter and stay silent on the rest.

    `scale_tolerance` is absolute in N of 1:N. The default 0.5 catches the defect
    class this exists for (1:1157 read against a claimed 1:1250) while tolerating
    the floating-point residue of a viewport height that is exact only in principle.
    """
    findings: list[str] = []

    for key in (
        "dxf_version",
        "insunits",
        "modelspace_entities",
        "block_count",
        "block_resident_entities",
    ):
        if key in expected and expected[key] != actual.get(key):
            findings.append(
                f"{key}: expected {expected[key]!r}, found {actual.get(key)!r}"
            )

    for key in ("layers", "entity_types"):
        for name, want in (expected.get(key) or {}).items():
            got = (actual.get(key) or {}).get(name)
            if got is None:
                findings.append(f"{key}: '{name}' expected {want}, absent")
            elif got != want:
                findings.append(f"{key}: '{name}' expected {want}, found {got}")

    expected_layouts = expected.get("layouts") or []
    actual_by_name = {
        layout.get("name"): layout for layout in (actual.get("layouts") or [])
    }
    for want_layout in expected_layouts:
        name = want_layout.get("name")
        got_layout = actual_by_name.get(name)
        if got_layout is None:
            findings.append(f"layout '{name}': expected, absent")
            continue
        if "entities" in want_layout and want_layout["entities"] != got_layout.get(
            "entities"
        ):
            findings.append(
                f"layout '{name}': expected {want_layout['entities']} entities, "
                f"found {got_layout.get('entities')}"
            )
        findings.extend(
            _compare_viewports(name, want_layout, got_layout, scale_tolerance)
        )

    return findings


def _compare_viewports(
    layout_name: str,
    want_layout: dict[str, Any],
    got_layout: dict[str, Any],
    scale_tolerance: float,
) -> list[str]:
    """Compare viewports of one layout, matched by viewport_id."""
    findings: list[str] = []
    got_by_id = {
        vp.get("viewport_id"): vp for vp in (got_layout.get("viewports") or [])
    }
    for want_vp in want_layout.get("viewports") or []:
        vp_id = want_vp.get("viewport_id")
        got_vp = got_by_id.get(vp_id)
        if got_vp is None:
            findings.append(f"layout '{layout_name}': viewport {vp_id} expected, absent")
            continue
        want_scale = want_vp.get("plot_scale")
        if want_scale is None:
            continue
        got_scale = got_vp.get("plot_scale")
        if got_scale is None:
            findings.append(
                f"layout '{layout_name}' viewport {vp_id}: expected 1:{want_scale}, "
                "scale not computable (header declares no units)"
            )
        elif abs(float(got_scale) - float(want_scale)) > scale_tolerance:
            findings.append(
                f"layout '{layout_name}' viewport {vp_id}: expected 1:{want_scale}, "
                f"plots at 1:{got_scale}"
            )
    return findings
