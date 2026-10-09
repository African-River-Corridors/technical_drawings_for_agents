"""THE plot path: drawing geometry -> a review-grade sheet, or nothing at all.

The contract this module exists to hold is one sentence: **``<stem>.pdf`` means
"passed every check"**. A reviewer who opens a canonically-named artifact is
looking at every entity the source says is visible, on the layout that actually
holds the drawing, on a real paper size. If any of that cannot be proved, this
module writes no canonically-named file and exits non-zero — because an
incomplete review artifact is worse than none, being the one that gets trusted
and signed against.

Pipeline, unconditionally, for every generator in the toolkit::

    geometry --(ezdxf SVGBackend on a layout.Page)--> SVG --(rsvg|cairosvg)--> PDF/PNG

Stage 1 has exactly one implementation and is not selectable. Stage 2 is a
two-link chain (``rsvg-convert``, then ``cairosvg``) with **no fallback past the
end of it**: a missing backend is a named, actionable error, never a degraded
artifact.

Before a single byte is written, the DXF path runs a **fidelity check** against
the in-memory document (:func:`check_fidelity`). The recording is taken with the
*same* ``Frontend``/``RenderContext``/``Configuration`` the SVG backend receives
and is then **replayed** into that backend, so the check measures the bytes that
actually ship rather than a model of them. Four checks, defined in
:class:`FidelityReport`:

* **F1 coverage** — every expected-visible drawable unit produced >=1 backend op;
* **F2 explosion** — per ``INSERT``, ``recorded_ops >= exploded_leaves``. A
  *lower bound*, never equality: measured fan-out at ezdxf 1.4.4 is many-to-many
  (a 4-vertex ``LWPOLYLINE`` merges to one op, an ``MTEXT`` fans to three, a
  rendered ``DIMENSION`` to six, a 3x4 ``MINSERT`` to twenty-four from a leaf
  walk reporting two), so equality is provably impossible. The bound is still
  exactly the assertion that catches a backend dropping a block's children;
* **F3 extent** — a non-degenerate plotted bounding box;
* **F4 layout** — the plotted layout is the one holding the drawing.

Visibility is resolved with ``RenderContext.resolve_visible``, the frontend's own
predicate, so frozen layers, off layers and the ``invisible`` flag are excluded
from the expectation *by construction*. Those are correct CAD semantics — a
frozen layer does not plot — and can never be reported as missing geometry.

This module is strictly read-only with respect to ``meta.yaml``: it never writes
one, never reads ``for_construction``, and never assigns ``status``. Nothing here
can move a drawing towards ISSUED FOR CONSTRUCTION.

Nothing in :mod:`technical_drawings_for_agents.render` changes. That path is frozen legacy; see
its docstring and ``docs/specs/P4-pdf-plot-path-fidelity.md`` §2.3 for why its
"faithful" backend is the lossy one.
"""

from __future__ import annotations

import logging
import os
import re
import runpy
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal

import ezdxf
from ezdxf.addons.drawing import Frontend, RenderContext
from ezdxf.addons.drawing import layout as ezdxf_layout
from ezdxf.addons.drawing.config import BackgroundPolicy, ColorPolicy, Configuration
from ezdxf.addons.drawing.recorder import Player, Recorder
from ezdxf.addons.drawing.svg import SVGBackend

from .provenance import EmitPolicy, ProvenanceError, write_text_canonical

log = logging.getLogger("technical_drawings_for_agents.plot")

Format = Literal["pdf", "png", "svg"]
FidelityMode = Literal["strict", "warn", "off"]
SvgBackendName = Literal["auto", "rsvg", "cairosvg"]

#: The closed set of failure kinds. The distinction between failure *kinds*
#: travels on the exception rather than in a class hierarchy, so callers and
#: tests branch on data instead of on ``except`` ordering.
PLOT_ERROR_KINDS = frozenset({"input", "backend", "fidelity", "bundle", "unsupported"})

#: CLI exit codes, all >= 3 so 0/1/2 keep the meaning every existing script
#: relies on (``cli.py``: 2 = bad input, 1 = failure, 0 = ok).
EXIT_CODES: dict[str, int] = {
    "fidelity": 3,
    "bundle": 4,
    "backend": 5,
    "unsupported": 6,
    "input": 2,
}

#: ``SVGBackend``/``layout.Page``/``recorder`` were verified at 1.4.4 only.
#: Claiming support for the ``>=1.1`` the legacy path declares would be
#: inventing a capability, so the floor is asserted at import-use time.
MIN_EZDXF = (1, 4)

#: Recursion cap for the exploded-leaf walk. Exceeding it is an error naming the
#: block, never a silent truncation.
MAX_EXPLODE_DEPTH = 16

_MAX_REPORTED_FINDINGS = 20
_SHEET_INDEX_RE = re.compile(r"^(?P<number>.+?)(?:[_-]S?(?P<index>\d+))?$")
_MISSING_PAGE_TEXT = "SHEET MISSING"

_BACKEND_HELP = (
    "no SVG->PDF backend available. Install ONE of:\n"
    "  * rsvg-convert   — macOS: brew install librsvg      "
    "Debian/Ubuntu: apt-get install librsvg2-bin\n"
    "  * cairosvg       — pip install 'technical_drawings_for_agents[plot-cairo]'\n"
    "or set RSVG_CONVERT_BIN to an rsvg-convert binary."
)


class PlotError(RuntimeError):
    """Raised when a plot request is malformed, a required backend is missing, or the
    rendered plot is not a faithful, complete representation of its source.

    ``kind`` is a member of :data:`PLOT_ERROR_KINDS` and maps to a CLI exit code
    via :data:`EXIT_CODES`.
    """

    def __init__(self, message: str, *, kind: str) -> None:
        if kind not in PLOT_ERROR_KINDS:
            raise ValueError(
                f"PlotError kind {kind!r} is not one of {sorted(PLOT_ERROR_KINDS)}"
            )
        super().__init__(message)
        self.kind = kind

    @property
    def exit_code(self) -> int:
        return EXIT_CODES[self.kind]


# --------------------------------------------------------------------------- #
# value objects
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class PageSpec:
    """A paper size. Either a ``PAGE_SIZES`` key or explicit millimetres."""

    name: str = "ISO A3"
    width_mm: float | None = None
    height_mm: float | None = None
    margin_mm: float = 10.0
    landscape: bool = True

    def __post_init__(self) -> None:
        if self.name == "custom":
            for field_name in ("width_mm", "height_mm"):
                value = getattr(self, field_name)
                if value is None:
                    raise PlotError(
                        f"page.{field_name} is required when page.name == 'custom'",
                        kind="unsupported",
                    )
                if not isinstance(value, (int, float)) or value <= 0:
                    raise PlotError(
                        f"page.{field_name} must be a positive number of millimetres, "
                        f"got {value!r}",
                        kind="unsupported",
                    )
        elif self.name not in ezdxf_layout.PAGE_SIZES:
            raise PlotError(
                f"page.name {self.name!r} is not a known paper size; expected one of "
                f"{sorted(ezdxf_layout.PAGE_SIZES)} or 'custom' with width_mm/height_mm",
                kind="unsupported",
            )
        if not isinstance(self.margin_mm, (int, float)) or self.margin_mm < 0:
            raise PlotError(
                f"page.margin_mm must be a non-negative number, got {self.margin_mm!r}",
                kind="unsupported",
            )

    @classmethod
    def parse(cls, text: str, *, margin_mm: float = 10.0, landscape: bool = True) -> PageSpec:
        """Parse a CLI ``--page`` value: ``ISO A3``, ``ANSI D``, ``420x297``, ``custom:420x297``."""
        raw = str(text).strip()
        if not raw:
            raise PlotError("--page was empty; expected e.g. 'ISO A3' or '420x297'", kind="unsupported")
        body = raw[len("custom:") :].strip() if raw.lower().startswith("custom:") else raw
        match = re.fullmatch(r"(?P<w>\d+(?:\.\d+)?)\s*[xX×]\s*(?P<h>\d+(?:\.\d+)?)", body)
        if match is not None:
            return cls(
                name="custom",
                width_mm=float(match.group("w")),
                height_mm=float(match.group("h")),
                margin_mm=margin_mm,
                landscape=landscape,
            )
        if raw.lower().startswith("custom:"):
            raise PlotError(
                f"--page 'custom:' needs WIDTHxHEIGHT in millimetres, got {body!r}",
                kind="unsupported",
            )
        canonical = _match_page_name(raw)
        return cls(name=canonical, margin_mm=margin_mm, landscape=landscape)

    def size_mm(self) -> tuple[float, float]:
        """Width and height in millimetres, oriented per :attr:`landscape`."""
        if self.name == "custom":
            width, height = float(self.width_mm), float(self.height_mm)  # type: ignore[arg-type]
        else:
            raw_w, raw_h, units = ezdxf_layout.PAGE_SIZES[self.name]
            if units is not ezdxf_layout.Units.mm:
                raise PlotError(
                    f"page {self.name!r} is declared in {units!r}; only millimetre paper "
                    "sizes are supported",
                    kind="unsupported",
                )
            width, height = float(raw_w), float(raw_h)
        long_side, short_side = max(width, height), min(width, height)
        return (long_side, short_side) if self.landscape else (short_side, long_side)

    def to_page(self) -> ezdxf_layout.Page:
        width, height = self.size_mm()
        if self.margin_mm * 2 >= min(width, height):
            raise PlotError(
                f"page.margin_mm {self.margin_mm:g} leaves no drawable area on a "
                f"{width:g}x{height:g} mm sheet",
                kind="unsupported",
            )
        return ezdxf_layout.Page(
            width,
            height,
            ezdxf_layout.Units.mm,
            margins=ezdxf_layout.Margins.all(self.margin_mm),
        )

    @property
    def label(self) -> str:
        width, height = self.size_mm()
        base = f"{width:g}x{height:g}mm" if self.name == "custom" else self.name
        return f"{base} {'landscape' if self.landscape else 'portrait'}"


@dataclass(frozen=True)
class PlotRequest:
    """One plot job. Frozen: a request is a record of what was asked, not a scratchpad."""

    source: Path
    out_dir: Path
    stem: str | None = None
    layout: str | None = None
    page: PageSpec = PageSpec()
    formats: tuple[Format, ...] = ("pdf",)
    fidelity: FidelityMode = "strict"
    svg_backend: SvgBackendName = "auto"
    white_background: bool = True
    ink_check: bool = False
    dpi: int = 300

    def __post_init__(self) -> None:
        if not self.formats:
            raise PlotError("formats must name at least one of pdf/png/svg", kind="unsupported")
        unknown = [fmt for fmt in self.formats if fmt not in ("pdf", "png", "svg")]
        if unknown:
            raise PlotError(
                f"unsupported output format(s) {unknown}; expected pdf, png or svg",
                kind="unsupported",
            )
        if self.fidelity not in ("strict", "warn", "off"):
            raise PlotError(
                f"fidelity must be strict, warn or off, got {self.fidelity!r}",
                kind="unsupported",
            )
        if self.svg_backend not in ("auto", "rsvg", "cairosvg"):
            raise PlotError(
                f"svg_backend must be auto, rsvg or cairosvg, got {self.svg_backend!r}",
                kind="backend",
            )
        if not isinstance(self.dpi, int) or isinstance(self.dpi, bool) or self.dpi <= 0:
            raise PlotError(f"dpi must be a positive int, got {self.dpi!r}", kind="unsupported")

    @property
    def resolved_stem(self) -> str:
        return self.stem or Path(self.source).stem


@dataclass(frozen=True)
class FidelityFinding:
    """One fidelity problem, naming what is missing rather than that something is."""

    check: Literal["coverage", "explosion", "extent", "layout"]
    severity: Literal["error", "warn"]
    message: str

    def __str__(self) -> str:
        return f"{self.severity}: {self.check}: {self.message}"


@dataclass(frozen=True)
class FidelityReport:
    """What the plot proved about its source. Frozen so P3 can serialise it as-is."""

    source: Path
    layout: str
    expected_units: int
    covered_units: int
    expected_exploded: int
    recorded_ops: int
    extent_mm: tuple[float, float] | None
    findings: tuple[FidelityFinding, ...] = ()

    @property
    def ok(self) -> bool:
        return not any(finding.severity == "error" for finding in self.findings)

    @property
    def errors(self) -> tuple[FidelityFinding, ...]:
        return tuple(f for f in self.findings if f.severity == "error")

    def summary(self) -> str:
        extent = (
            "extent none"
            if self.extent_mm is None
            else f"extent {self.extent_mm[0]:.4g}x{self.extent_mm[1]:.4g}"
        )
        return (
            f"{Path(self.source).name} [{self.layout}]: "
            f"{self.covered_units}/{self.expected_units} visible units covered, "
            f"{self.recorded_ops} ops vs {self.expected_exploded} exploded leaves, "
            f"{extent}, {len(self.errors)} error(s)"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": str(self.source),
            "layout": self.layout,
            "expected_units": self.expected_units,
            "covered_units": self.covered_units,
            "expected_exploded": self.expected_exploded,
            "recorded_ops": self.recorded_ops,
            "extent_mm": list(self.extent_mm) if self.extent_mm else None,
            "ok": self.ok,
            "findings": [
                {"check": f.check, "severity": f.severity, "message": f.message}
                for f in self.findings
            ],
        }


@dataclass(frozen=True)
class PlotResult:
    """What a plot produced. ``outputs`` are the paths that exist, and only those."""

    request: PlotRequest
    outputs: tuple[Path, ...]
    svg_backend: str
    fidelity: FidelityReport | None
    pages: int

    @property
    def state(self) -> str:
        if self.fidelity is None:
            return "unchecked"
        return "ok" if self.fidelity.ok else "degraded"

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": str(self.request.source),
            "outputs": [str(path) for path in self.outputs],
            "svg_backend": self.svg_backend,
            "pages": self.pages,
            "state": self.state,
            "page": self.request.page.label,
            "fidelity": None if self.fidelity is None else self.fidelity.to_dict(),
        }


# --------------------------------------------------------------------------- #
# the fidelity check
# --------------------------------------------------------------------------- #


def check_fidelity(doc, layout_name: str, *, config: Configuration | None = None) -> FidelityReport:
    """Record the frontend for ``layout_name`` and compare it against the source.

    Pure: no file I/O, no mutation of ``doc``. This is the primitive the tests
    drive directly and the thing :func:`plot` runs before it writes a byte.
    """
    report, _player = _check_and_record(doc, layout_name, config=config, source=Path("<memory>"))
    return report


def _check_and_record(
    doc,
    layout_name: str,
    *,
    config: Configuration | None,
    source: Path,
) -> tuple[FidelityReport, Player]:
    _require_ezdxf()
    cfg = config if config is not None else white_review_config()
    target = _layout_by_name(doc, layout_name)
    ctx, player = _record_layout(doc, target, cfg)

    ops = Counter(properties.handle for _record, properties in player.recordings())
    units = _drawable_units(ctx, target)
    findings: list[FidelityFinding] = []

    # F4 first: a check against the wrong layout makes F1-F3 meaningless noise.
    layout_counts = _unit_counts_by_layout(doc, cfg)
    if not units:
        elsewhere = {name: n for name, n in layout_counts.items() if n > 0}
        detail = ", ".join(f"{name}={n}" for name, n in sorted(layout_counts.items()))
        if elsewhere:
            findings.append(
                FidelityFinding(
                    check="layout",
                    severity="error",
                    message=(
                        f"layout {layout_name!r} holds no visible entity, but "
                        f"{', '.join(sorted(elsewhere))} does — plotting it would write a "
                        f"blank sheet. Layout unit counts: {detail}. "
                        f"Pass --layout {min(elsewhere)!r} to plot the drawing."
                    ),
                )
            )
        else:
            findings.append(
                FidelityFinding(
                    check="layout",
                    severity="error",
                    message=(
                        f"no layout in {Path(source).name} holds a visible entity "
                        f"(counts: {detail}); there is no sheet to plot"
                    ),
                )
            )

    covered = 0
    for unit in units:
        recorded = ops.get(unit.handle, 0)
        if recorded >= 1:
            covered += 1
        else:
            findings.append(
                FidelityFinding(
                    check="coverage",
                    severity="error",
                    message=(
                        f"{unit.dxftype} handle={unit.handle} layer={unit.layer!r}"
                        f"{unit.block_note} is visible in the source but produced no "
                        "backend call — it was not drawn"
                    ),
                )
            )
        if unit.dxftype == "INSERT" and recorded < unit.exploded_leaves:
            findings.append(
                FidelityFinding(
                    check="explosion",
                    severity="error",
                    message=(
                        f"INSERT handle={unit.handle} layer={unit.layer!r}"
                        f"{unit.block_note} exploded to {unit.exploded_leaves} visible "
                        f"leaf entities but only {recorded} backend call(s) were recorded "
                        f"(recorded_ops < exploded_leaves): "
                        f"{unit.exploded_leaves - recorded} dropped"
                    ),
                )
            )

    extent_mm = _extent(player)
    if units:
        if extent_mm is None:
            findings.append(
                FidelityFinding(
                    check="extent",
                    severity="error",
                    message=(
                        f"{len(units)} visible unit(s) in layout {layout_name!r} but the "
                        "plotted bounding box is empty — the sheet would come out blank"
                    ),
                )
            )
        elif not (extent_mm[0] > 0 or extent_mm[1] > 0):
            findings.append(
                FidelityFinding(
                    check="extent",
                    severity="error",
                    message=(
                        f"the plotted bounding box of layout {layout_name!r} has zero width "
                        "and zero height — the sheet would come out blank"
                    ),
                )
            )

    report = FidelityReport(
        source=Path(source),
        layout=layout_name,
        expected_units=len(units),
        covered_units=covered,
        expected_exploded=sum(u.exploded_leaves for u in units if u.dxftype == "INSERT"),
        recorded_ops=sum(ops.values()),
        extent_mm=extent_mm,
        findings=tuple(findings),
    )
    return report, player


@dataclass(frozen=True)
class _Unit:
    """A drawable unit: an entity in the plotted layout, or an ``ATTRIB`` it owns."""

    handle: str
    dxftype: str
    layer: str
    block: str | None
    exploded_leaves: int

    @property
    def block_note(self) -> str:
        return "" if self.block is None else f" block={self.block!r}"


def _drawable_units(ctx: RenderContext, target) -> tuple[_Unit, ...]:
    """Expected-visible units, keyed by handle.

    ``ATTRIB``s are units in their own right because ezdxf attributes their
    backend ops to the ``ATTRIB``'s own handle, not the owning ``INSERT``'s
    (verified at 1.4.4). Folding them into the INSERT would produce a spurious
    F2 failure on every tagged block.
    """
    units: list[_Unit] = []
    for entity in target:
        if not _resolve_visible(ctx, entity):
            continue
        dxftype = entity.dxftype()
        block = entity.dxf.get("name") if dxftype == "INSERT" else None
        leaves = _exploded_leaves(ctx, entity) if dxftype == "INSERT" else 0
        units.append(
            _Unit(
                handle=entity.dxf.handle,
                dxftype=dxftype,
                layer=str(entity.dxf.get("layer", "0")),
                block=block,
                exploded_leaves=leaves,
            )
        )
        if dxftype == "INSERT":
            for attrib in getattr(entity, "attribs", ()):  # ATTRIBs own their ops
                if _resolve_visible(ctx, attrib):
                    units.append(
                        _Unit(
                            handle=attrib.dxf.handle,
                            dxftype="ATTRIB",
                            layer=str(attrib.dxf.get("layer", "0")),
                            block=block,
                            exploded_leaves=0,
                        )
                    )
    return tuple(units)


def _exploded_leaves(ctx: RenderContext, insert, depth: int = 0) -> int:
    """Recursive ``virtual_entities()`` leaf count, visible leaves only."""
    if depth >= MAX_EXPLODE_DEPTH:
        raise PlotError(
            f"block {insert.dxf.get('name', '?')!r} nests deeper than "
            f"{MAX_EXPLODE_DEPTH} levels; refusing to truncate the exploded-leaf count "
            "silently — flatten the block or raise MAX_EXPLODE_DEPTH deliberately",
            kind="fidelity",
        )
    try:
        children = list(insert.virtual_entities())
    except ezdxf.DXFStructureError as exc:
        raise PlotError(
            f"INSERT handle={insert.dxf.handle} references block "
            f"{insert.dxf.get('name', '?')!r} which cannot be exploded: {exc}",
            kind="fidelity",
        ) from exc
    total = 0
    for child in children:
        if child.dxftype() == "INSERT":
            total += _exploded_leaves(ctx, child, depth + 1)
        elif _resolve_visible(ctx, child):
            total += 1
    return total


def _resolve_visible(ctx: RenderContext, entity) -> bool:
    """``RenderContext``'s own predicate — the frontend's exact visibility rule."""
    try:
        return bool(ctx.resolve_visible(entity))
    except AttributeError:
        # Not a DXFGraphic (e.g. a raw ATTDEF in an odd document): not drawable.
        return False


def _record_layout(doc, target, cfg: Configuration) -> tuple[RenderContext, Player]:
    ctx = RenderContext(doc)
    ctx.set_current_layout(target)
    recorder = Recorder()
    try:
        Frontend(ctx, recorder, config=cfg).draw_layout(target, finalize=True)
    except PlotError:
        raise
    except AttributeError as exc:
        # D5: an unrendered DIMENSION has no geometry block, and ezdxf's frontend
        # dies inside dimension.py with a bare AttributeError. Name it instead.
        raise PlotError(
            _unrendered_dimension_message(doc, target, exc),
            kind="fidelity",
        ) from exc
    except ezdxf.DXFStructureError as exc:
        raise PlotError(
            f"layout {_layout_name(target)!r} cannot be traversed: {exc}",
            kind="fidelity",
        ) from exc
    return ctx, recorder.player()


def _unrendered_dimension_message(doc, target, exc: AttributeError) -> str:
    suspects = [
        entity.dxf.handle
        for entity in target.query("DIMENSION")
        if entity.dxf.get("geometry", None) in (None, "")
        or entity.dxf.get("geometry", None) not in doc.blocks
    ]
    where = (
        f" Unrendered DIMENSION handle(s): {', '.join(suspects)} — call .render() on the "
        "dimension before plotting."
        if suspects
        else ""
    )
    return (
        f"layout {_layout_name(target)!r} could not be drawn: ezdxf raised "
        f"AttributeError({exc}). This is the signature of an entity with no geometry "
        f"block (typically a DIMENSION that was never rendered).{where}"
    )


def _extent(player: Player) -> tuple[float, float] | None:
    box = player.bbox()
    if not box.has_data:
        return None
    size = box.size
    return (float(size.x), float(size.y))


def _unit_counts_by_layout(doc, cfg: Configuration) -> dict[str, int]:
    """Expected-visible unit count per layout. No recording — visibility only."""
    counts: dict[str, int] = {}
    for name in doc.layouts.names():
        target = doc.layouts.get(name)
        ctx = RenderContext(doc)
        ctx.set_current_layout(target)
        counts[name] = sum(1 for entity in target if _resolve_visible(ctx, entity))
    return counts


def white_review_config() -> Configuration:
    """A white sheet with black ink — the review default, not ezdxf's dark screen sheet."""
    return Configuration(
        background_policy=BackgroundPolicy.WHITE,
        color_policy=ColorPolicy.BLACK,
    )


def dark_config() -> Configuration:
    """ezdxf's default screen-style sheet, for a caller that explicitly asks."""
    return Configuration()


# --------------------------------------------------------------------------- #
# layout resolution
# --------------------------------------------------------------------------- #


def resolve_layout(doc, requested: str | None, *, config: Configuration | None = None) -> str:
    """Pick the layout to plot (§4.7). Never returns a layout that would plot blank.

    1. ``requested`` -> exactly that; an unknown name is an input error.
    2. modelspace, if it holds >=1 expected-visible unit.
    3. exactly one other layout with units -> that one, logged at INFO.
    4. otherwise refuse to guess.
    """
    cfg = config if config is not None else white_review_config()
    names = list(doc.layouts.names())
    if requested is not None:
        if requested not in names:
            raise PlotError(
                f"layout {requested!r} is not in this document; it has {sorted(names)}",
                kind="input",
            )
        return requested

    counts = _unit_counts_by_layout(doc, cfg)
    model = doc.modelspace().name
    if counts.get(model, 0) > 0:
        return model
    candidates = sorted(name for name, n in counts.items() if n > 0 and name != model)
    if len(candidates) == 1:
        log.info(
            "modelspace is empty; plotting paper-space layout %r instead (%d visible unit(s)).",
            candidates[0],
            counts[candidates[0]],
        )
        return candidates[0]
    detail = ", ".join(f"{name}={n}" for name, n in sorted(counts.items()))
    if not candidates:
        raise PlotError(
            f"no layout holds a visible entity (counts: {detail}); there is no sheet to "
            "plot. Refusing to write a blank PDF.",
            kind="fidelity",
        )
    raise PlotError(
        f"modelspace is empty and {len(candidates)} paper-space layouts hold geometry "
        f"({detail}); refusing to guess. Pass --layout with one of {candidates}.",
        kind="fidelity",
    )


# --------------------------------------------------------------------------- #
# stage 2 — the SVG -> PDF/PNG chain
# --------------------------------------------------------------------------- #


def find_rsvg() -> str | None:
    """Locate ``rsvg-convert``, honouring ``RSVG_CONVERT_BIN``.

    Mirrors the ``SOFFICE_BIN`` / ``ODA_CONVERTER`` convention already used by
    :func:`technical_drawings_for_agents.render.find_soffice` and ``ingest.find_oda``.
    """
    override = os.environ.get("RSVG_CONVERT_BIN")
    if override and Path(override).exists():
        return override
    return shutil.which("rsvg-convert")


def find_svg_backend(preference: SvgBackendName = "auto") -> tuple[str, object]:
    """Resolve the SVG->PDF backend. Returns ``(name, handle)`` or raises.

    There is no third link and no fallback past the end of the chain: a missing
    backend is an error naming both remedies, never a degraded artifact.
    """
    if preference not in ("auto", "rsvg", "cairosvg"):
        raise PlotError(
            f"unknown svg backend {preference!r}; expected auto, rsvg or cairosvg",
            kind="backend",
        )
    if preference in ("auto", "rsvg"):
        binary = find_rsvg()
        if binary is not None:
            return "rsvg", binary
        if preference == "rsvg":
            raise PlotError(
                "--svg-backend rsvg was requested but rsvg-convert was not found on PATH "
                "and RSVG_CONVERT_BIN is unset. Install it (macOS: brew install librsvg; "
                "Debian/Ubuntu: apt-get install librsvg2-bin) or choose another backend. "
                "Not falling back to cairosvg: an explicit backend request is honoured "
                "or it fails.",
                kind="backend",
            )
    if preference in ("auto", "cairosvg"):
        try:
            import cairosvg  # type: ignore
        except ImportError as exc:
            if preference == "cairosvg":
                raise PlotError(
                    "--svg-backend cairosvg was requested but the module is not "
                    f"importable ({exc}). Install it with "
                    "pip install 'technical_drawings_for_agents[plot-cairo]'.",
                    kind="backend",
                ) from exc
        else:
            return "cairosvg", cairosvg
    raise PlotError(_BACKEND_HELP, kind="backend")


def _convert(
    backend: tuple[str, object],
    svg_path: Path,
    target: Path,
    fmt: str,
    *,
    dpi: int,
    policy: EmitPolicy,
) -> None:
    """Convert ``svg_path`` to ``target``, atomically. Never leaves a partial file."""
    name, handle = backend
    tmp = target.with_name(target.name + ".plot-tmp")
    try:
        if name == "rsvg":
            _run_rsvg(str(handle), svg_path, tmp, fmt, dpi=dpi, policy=policy)
        else:
            _run_cairosvg(handle, svg_path, tmp, fmt, dpi=dpi)
        if not tmp.exists() or tmp.stat().st_size == 0:
            raise PlotError(
                f"{name} produced no bytes for {target.name} from {svg_path.name}",
                kind="backend",
            )
        os.replace(tmp, target)
    finally:
        if tmp.exists():
            tmp.unlink()


def _run_rsvg(
    binary: str,
    svg_path: Path,
    target: Path,
    fmt: str,
    *,
    dpi: int,
    policy: EmitPolicy,
) -> None:
    cmd = [binary, "-f", fmt, "-o", str(target)]
    if fmt == "png":
        cmd += ["--dpi-x", str(dpi), "--dpi-y", str(dpi)]
    cmd.append(str(svg_path))
    _run_rsvg_cmd(cmd, policy)


def _run_rsvg_cmd(cmd: list[str], policy: EmitPolicy) -> None:
    """Run rsvg-convert. rc != 0 is never recoverable, and stderr is quoted verbatim.

    ``SOURCE_DATE_EPOCH`` is forwarded when the caller resolved one: measured on
    librsvg 2.62.3 / cairo 1.18.4, two PDF conversions of byte-identical SVG more
    than a second apart differ, and setting the epoch makes them byte-identical.
    (The spec's §2.7 "no timestamp in output" measured two runs inside the same
    second — see the PR body.) Determinism is the caller's opt-in, exactly as it
    is for the DXF/SVG writers.
    """
    env = dict(os.environ)
    if policy.source_date_epoch is not None:
        env["SOURCE_DATE_EPOCH"] = str(policy.source_date_epoch)
    try:
        completed = subprocess.run(
            cmd, capture_output=True, text=True, timeout=180, env=env, check=False
        )
    except OSError as exc:
        raise PlotError(f"could not execute {cmd[0]}: {exc}", kind="backend") from exc
    if completed.returncode != 0:
        raise PlotError(
            f"rsvg-convert failed (rc={completed.returncode}) on "
            f"{' '.join(cmd[-3:])}: {(completed.stderr or '').strip()[:500]}",
            kind="backend",
        )


_IMAGE_HREF_RE = re.compile(r"<image\b[^>]*?\bhref=\"([^\"]+)\"", re.IGNORECASE)


def _mirror_linked_rasters(source_svg: Path, svg_text: str, out_dir: Path) -> list[Path]:
    """Copy every relative ``<image href>`` sidecar of ``source_svg`` beside its plotted copy.

    Only plain relative paths that stay inside the source directory are mirrored — that is
    the only shape librsvg will load anyway (see ``technical_drawings_for_agents.backdrop``). ``data:`` URIs
    and absolute/URL hrefs are left alone. A linked file that does not exist raises: a
    blank backdrop at exit 0 is the failure this exists to prevent.
    """
    src_dir = source_svg.resolve().parent
    copied: list[Path] = []
    for href in dict.fromkeys(_IMAGE_HREF_RE.findall(svg_text)):
        if href.startswith(("data:", "http://", "https://", "file:")) or Path(href).is_absolute():
            continue
        src = (src_dir / href).resolve()
        if src_dir not in src.parents and src != src_dir:
            raise PlotError(
                f"linked raster {href!r} in {source_svg.name} escapes the sheet directory; "
                "librsvg will not load it. Emit the sheet beside its sidecar.",
                kind="fidelity",
            )
        if not src.is_file():
            raise PlotError(
                f"linked raster {href!r} referenced by {source_svg.name} is missing "
                f"({src}); the plot would render a blank backdrop at exit 0.",
                kind="fidelity",
            )
        dst = (out_dir / href).resolve()
        if dst == src:
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        copied.append(dst)
    return copied


def _run_cairosvg(module, svg_path: Path, target: Path, fmt: str, *, dpi: int) -> None:
    try:
        if fmt == "pdf":
            module.svg2pdf(url=str(svg_path), write_to=str(target))
        else:
            module.svg2png(url=str(svg_path), write_to=str(target), dpi=dpi)
    except Exception as exc:
        raise PlotError(
            f"cairosvg failed converting {svg_path.name} to {fmt}: {exc}", kind="backend"
        ) from exc


# --------------------------------------------------------------------------- #
# the one entry point
# --------------------------------------------------------------------------- #


def plot(request: PlotRequest) -> PlotResult:
    """THE plot entry point. Every generator ends here.

    Pipeline, unconditionally: geometry -> SVG -> (PDF, PNG).

    * ``.dxf`` -> ezdxf ``SVGBackend`` on a real ``Page``, fidelity-checked, then converted
    * ``.svg`` -> converted as-is (it *is* the sheet)
    * ``.py``  -> executed, then every ``.svg`` **and** ``.dxf`` it wrote is plotted

    Raises :class:`PlotError` on a missing backend, a fidelity error, or a bad
    request. Writes nothing at a canonical path unless every enabled check for
    that artifact passed.
    """
    source = Path(request.source)
    if not source.exists():
        raise PlotError(f"source not found: {source}", kind="input")
    suffix = source.suffix.lower()
    if suffix == ".dxf":
        return _plot_dxf(request)
    if suffix == ".svg":
        return _plot_svg(request)
    if suffix == ".py":
        return _plot_py(request)
    raise PlotError(
        f"unsupported source type {suffix!r} for {source.name} (expect .dxf, .svg or .py)",
        kind="unsupported",
    )


def plot_source(source: str | Path, out_dir: str | Path, **kw: Any) -> PlotResult:
    """Keyword convenience wrapper building a :class:`PlotRequest`. Generators call this."""
    return plot(PlotRequest(source=Path(source), out_dir=Path(out_dir), **kw))


def _plot_dxf(request: PlotRequest) -> PlotResult:
    policy = _policy()
    cfg = white_review_config() if request.white_background else dark_config()
    doc = _read_dxf(request.source)
    layout_name = resolve_layout(doc, request.layout, config=cfg)

    report: FidelityReport | None = None
    infix = ".unchecked"
    if request.fidelity == "off":
        _require_ezdxf()
        target = _layout_by_name(doc, layout_name)
        _ctx, player = _record_layout(doc, target, cfg)
    else:
        report, player = _check_and_record(
            doc, layout_name, config=cfg, source=Path(request.source)
        )
        if report.ok:
            infix = ""
        elif request.fidelity == "strict":
            raise PlotError(_fidelity_message(report), kind="fidelity")
        else:
            infix = ".degraded"
            log.warning("%s", _fidelity_message(report))

    svg_text = _svg_from_player(player, request.page)
    outputs, backend_name = _emit(request, svg_text, infix, policy)
    return PlotResult(
        request=request,
        outputs=outputs,
        svg_backend=backend_name,
        fidelity=report,
        pages=1,
    )


def _plot_svg(request: PlotRequest) -> PlotResult:
    """An SVG source *is* the sheet: no fidelity check exists to run against it.

    There is no source-of-truth entity set to compare an SVG against — the SVG is
    the drawing. ``--ink-check`` is the only verification available here.
    """
    policy = _policy()
    if request.page != PageSpec():
        # A silently ignored flag is the same class of defect as a silently dropped
        # entity, so say so: stage 1 (and with it layout.Page) does not run for an
        # SVG source. An SVG-authored sheet's paper size comes from the SVG itself.
        log.warning(
            "--page %s does not apply to the SVG source %s: an SVG *is* the sheet, so its "
            "page size comes from its own width/height. --page sizes DXF plots only.",
            request.page.label,
            Path(request.source).name,
        )
    try:
        svg_text = Path(request.source).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise PlotError(f"cannot read SVG {request.source}: {exc}", kind="input") from exc
    outputs, backend_name = _emit(request, svg_text, "", policy, source_is_sheet=True)
    return PlotResult(
        request=request,
        outputs=outputs,
        svg_backend=backend_name,
        fidelity=None,
        pages=1,
    )


def _plot_py(request: PlotRequest) -> PlotResult:
    """Execute a generator, then plot **both** the SVGs and the DXFs it wrote.

    ``render_py`` globs ``*.dxf`` only, so a generator that writes an SVG (most of
    them) gets no PDF from ``render``. That asymmetry is frozen there and fixed
    here; it is what the site-GA script's hand-rolled ``rsvg-convert`` call was
    working around.
    """
    py_path = Path(request.source).resolve()
    out_dir = Path(request.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    added = str(py_path.parent)
    inserted = added not in sys.path
    if inserted:
        sys.path.insert(0, added)
    try:
        runpy.run_path(str(py_path), run_name="__main__")
    finally:
        if inserted:
            sys.path.remove(added)

    sheets = sorted(out_dir.glob("*.svg")) + sorted(out_dir.glob("*.dxf"))
    sheets = [p for p in sheets if not _is_generated_infix(p)]
    if not sheets:
        raise PlotError(
            f"{py_path.name} produced no .svg or .dxf in {out_dir}; there is nothing to plot",
            kind="input",
        )
    outputs: list[Path] = []
    reports: list[FidelityReport] = []
    backend = "none"
    for sheet in sheets:
        sub = replace(request, source=sheet, stem=sheet.stem)
        result = plot(sub)
        outputs.extend(result.outputs)
        backend = result.svg_backend
        if result.fidelity is not None:
            reports.append(result.fidelity)
    return PlotResult(
        request=request,
        outputs=tuple(outputs),
        svg_backend=backend,
        fidelity=_merge_reports(reports, Path(request.source)),
        pages=len(sheets),
    )


def _merge_reports(reports: Sequence[FidelityReport], source: Path) -> FidelityReport | None:
    if not reports:
        return None
    if len(reports) == 1:
        return reports[0]
    return FidelityReport(
        source=source,
        layout=",".join(r.layout for r in reports),
        expected_units=sum(r.expected_units for r in reports),
        covered_units=sum(r.covered_units for r in reports),
        expected_exploded=sum(r.expected_exploded for r in reports),
        recorded_ops=sum(r.recorded_ops for r in reports),
        extent_mm=None,
        findings=tuple(f for r in reports for f in r.findings),
    )


def _svg_from_player(player: Player, page: PageSpec) -> str:
    """Replay the *checked* recording into the SVG backend.

    Replaying rather than re-traversing is what makes the fidelity check measure
    the bytes that ship: the ops asserted on are literally the ops serialised.
    (Verified byte-identical to a second traversal at ezdxf 1.4.4.)
    """
    _require_ezdxf()
    backend = SVGBackend()
    player.replay(backend)
    return backend.get_string(
        page.to_page(),
        settings=ezdxf_layout.Settings(fit_page=True),
        xml_declaration=True,
    )


def _emit(
    request: PlotRequest,
    svg_text: str,
    infix: str,
    policy: EmitPolicy,
    *,
    source_is_sheet: bool = False,
) -> tuple[tuple[Path, ...], str]:
    """Write the sheet SVG, then convert. Ordered so a missing backend leaves an SVG.

    The SVG is always written: it is the sheet, it is viewable in any browser, and
    an SVG can never be mistaken for the PDF a reviewer signs against — so writing
    it is not a degraded artifact even when stage 2 then fails.

    Rasters are converted to staging paths, verified (``--ink-check``), and only
    then ``os.replace``d onto their canonical names. A check that runs *after* the
    canonical file exists is not a gate, it is a log line.
    """
    out_dir = Path(request.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{request.resolved_stem}{infix}"
    outputs: list[Path] = []

    svg_path = out_dir / f"{stem}.svg"
    if not (source_is_sheet and Path(request.source).resolve() == svg_path.resolve()):
        write_text_canonical(svg_path, svg_text, policy)
        if source_is_sheet:
            # A sheet may link a raster sidecar (`technical_drawings_for_agents.backdrop`). Copying the SVG
            # alone silently breaks that link: librsvg resolves hrefs against the SVG's OWN
            # directory and renders a blank backdrop at exit 0 (measured: a 7-sheet pack
            # plotted with zero images and no error). Mirror the sidecars beside the copy,
            # or fail loudly if one is missing.
            outputs_extra = _mirror_linked_rasters(Path(request.source), svg_text, out_dir)
        else:
            outputs_extra = []
    else:
        outputs_extra = []
    # The SVG is always written *and* always reported: it is the stage-1 artifact,
    # it is what P5's legibility checks read, and an output this module put on disk
    # but did not mention would be exactly the kind of surprise it exists to prevent.
    outputs.append(svg_path)
    outputs.extend(outputs_extra)

    raster = [fmt for fmt in ("pdf", "png") if fmt in request.formats]
    if not raster:
        return tuple(outputs), "none"

    try:
        backend = find_svg_backend(request.svg_backend)
    except PlotError as exc:
        raise PlotError(
            f"{exc}\n  The SVG sheet at {svg_path} was written and is viewable in any "
            "browser; no PDF was produced.",
            kind="backend",
        ) from exc
    log.info("SVG->%s via the %s backend.", "/".join(raster), backend[0])

    staged: list[tuple[Path, Path]] = []
    try:
        for fmt in raster:
            target = out_dir / f"{stem}.{fmt}"
            stage = out_dir / f"{stem}.{fmt}.plot-staged"
            _convert(backend, svg_path, stage, fmt, dpi=request.dpi, policy=policy)
            staged.append((stage, target))
        if request.ink_check:
            _ink_check([stage for stage, _ in staged if _.suffix == ".pdf"])
        for stage, target in staged:
            os.replace(stage, target)
            outputs.append(target)
    finally:
        for stage, _target in staged:
            if stage.exists():
                stage.unlink()
    return tuple(outputs), backend[0]


def _fidelity_message(report: FidelityReport) -> str:
    errors = report.errors
    shown = errors[:_MAX_REPORTED_FINDINGS]
    lines = [f"plot is not faithful — {report.summary()}"]
    lines += [f"  - {finding.check}: {finding.message}" for finding in shown]
    if len(errors) > len(shown):
        lines.append(f"  ... and {len(errors) - len(shown)} more finding(s)")
    return "\n".join(lines)


def _is_generated_infix(path: Path) -> bool:
    """True for artifacts this module generated (``*.degraded.svg`` etc.).

    Keeps a re-plot of a directory from treating its own previous output as a
    fresh source.
    """
    return any(part in (".degraded", ".unchecked", ".partial") for part in Path(path).suffixes)


# --------------------------------------------------------------------------- #
# optional ink check
# --------------------------------------------------------------------------- #


def _ink_check(pdfs: Sequence[Path]) -> None:
    """Rasterise each PDF and require ink on every page. Runs before promotion.

    A *pre-raster* fidelity check proves the frontend emitted the geometry; it
    cannot prove the converter honoured it. This closes that gap, coarsely: it
    catches *blank*, not *incomplete*. The precise check is F1-F4.

    Missing ``pdftoppm`` or Pillow is a ``backend`` error, never a skipped check:
    ``--ink-check`` was asked for explicitly.
    """
    if not pdfs:
        return
    pdftoppm = shutil.which("pdftoppm")
    if pdftoppm is None:
        raise PlotError(
            "--ink-check needs pdftoppm (poppler): macOS 'brew install poppler', "
            "Debian/Ubuntu 'apt-get install poppler-utils'.",
            kind="backend",
        )
    try:
        from PIL import Image  # type: ignore
    except ImportError as exc:
        raise PlotError(
            "--ink-check needs Pillow: pip install 'technical_drawings_for_agents[verify]'.",
            kind="backend",
        ) from exc

    for pdf in pdfs:
        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp) / "page"
            completed = subprocess.run(
                [pdftoppm, "-r", "72", "-png", str(pdf), str(prefix)],
                capture_output=True,
                text=True,
                timeout=180,
                check=False,
            )
            if completed.returncode != 0:
                raise PlotError(
                    f"pdftoppm failed on {pdf.name}: {(completed.stderr or '').strip()[:500]}",
                    kind="backend",
                )
            pages = sorted(Path(tmp).glob("page*.png"))
            if not pages:
                raise PlotError(
                    f"pdftoppm rasterised no page from {pdf.name}", kind="fidelity"
                )
            for index, page_png in enumerate(pages, start=1):
                with Image.open(page_png) as image:
                    # histogram() over the 8-bit grey channel: one C-level pass, and
                    # no per-pixel Python loop on a 300 dpi sheet.
                    ink = sum(image.convert("L").histogram()[:250])
                if ink == 0:
                    raise PlotError(
                        f"plotted page {index} of {len(pages)} contains no ink "
                        f"({pdf.name}); the sheet is blank",
                        kind="fidelity",
                    )


# --------------------------------------------------------------------------- #
# --review: sheet discovery + bundling
# --------------------------------------------------------------------------- #


def discover_sheets(
    target: str | Path, *, exclude: Sequence[Path] = ()
) -> tuple[Path, ...]:
    """Ordered sheet discovery for ``--review``. A total order in every branch.

    1. a file is its own only sheet;
    2. a directory whose ``meta.yaml`` carries ``sheets:`` uses that list, verbatim,
       as the order — the explicit, reviewable ordering hook;
    3. otherwise the PDFs in ``<target>/out/`` if any, else the SVGs, ordered by
       ``(drawing_number, sheet_index, filename)``.

    Listed-but-absent sheets are **returned**, not dropped: the bundler must be
    able to name what is missing. A silently shortened sheet set is the exact
    failure this whole module exists to prevent.

    ``exclude`` drops paths from branch 3 — the CLI passes the ``--review`` target,
    because a bundle written into ``out/`` would otherwise become a "sheet" of the
    next run, and a review PDF quietly containing the previous review PDF is worse
    than a loud error.
    """
    path = Path(target)
    if path.is_file():
        return (path,)
    if not path.is_dir():
        raise PlotError(f"--review target not found: {path}", kind="input")

    declared = _declared_sheets(path)
    if declared is not None:
        return tuple((path / entry).resolve() for entry in declared)

    excluded = {Path(item).resolve() for item in exclude}
    out_dir = path / "out" if (path / "out").is_dir() else path
    for suffix in (".pdf", ".svg"):
        found = [
            candidate
            for candidate in out_dir.iterdir()
            if candidate.is_file()
            and candidate.suffix.lower() == suffix
            and not _is_generated_infix(candidate)
            and candidate.resolve() not in excluded
        ]
        if found:
            return tuple(sorted(found, key=_sheet_sort_key))
    raise PlotError(
        f"no sheets to bundle: {out_dir} holds no .pdf and no .svg. "
        "Build the sheets first (--review collates artifacts, it does not build them).",
        kind="bundle",
    )


def _declared_sheets(directory: Path) -> list[str] | None:
    """Read ``sheets:`` from the raw ``meta.yaml`` mapping. Never writes, never validates it.

    Read from the raw YAML rather than through ``DrawingMeta`` on purpose: the
    plot path must not depend on ``meta.py``'s field list, and ``meta.py`` is not
    modified by this change.
    """
    meta_path = directory / "meta.yaml"
    if not meta_path.is_file():
        return None
    import yaml

    try:
        data = yaml.safe_load(meta_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise PlotError(f"cannot read {meta_path}: {exc}", kind="input") from exc
    if not isinstance(data, dict) or "sheets" not in data:
        return None
    sheets = data["sheets"]
    if not isinstance(sheets, list) or not sheets or not all(
        isinstance(entry, str) and entry.strip() for entry in sheets
    ):
        raise PlotError(
            f"{meta_path}: 'sheets' must be a non-empty list of paths relative to the "
            f"drawing directory, got {sheets!r}",
            kind="input",
        )
    return [entry.strip() for entry in sheets]


def _sheet_sort_key(path: Path) -> tuple[str, int, str]:
    match = _SHEET_INDEX_RE.fullmatch(path.stem)
    if match is None:  # pragma: no cover — the pattern matches any non-empty stem
        return (path.stem, 0, path.name)
    index = match.group("index")
    return (match.group("number"), int(index) if index else 0, path.name)


def review_bundle(
    sheets: Sequence[Path],
    out_pdf: str | Path,
    *,
    allow_missing: bool = False,
    svg_backend: SvgBackendName = "auto",
) -> Path:
    """Collate sheets into one ordered multi-page PDF, or produce nothing.

    A sheet that is listed but absent, or empty, makes the bundle **incomplete**.
    By default no bundle is written. ``allow_missing`` writes one — to
    ``<name>.partial.pdf``, with a ``SHEET MISSING`` placeholder page in each gap
    — and the caller still gets ``PlotError(kind="bundle")``, so the exit code and
    the filename both say "incomplete". Nothing about it can be mistaken for a
    complete review set.

    The bundler copies pages. It never renders a watermark, never reads
    ``for_construction``, and never writes ``meta.yaml``.
    """
    target = Path(out_pdf)
    if not sheets:
        raise PlotError("--review was given no sheets to bundle", kind="bundle")
    if target.suffix.lower() != ".pdf":
        raise PlotError(f"--review output must be a .pdf, got {target.name}", kind="bundle")

    missing = [sheet for sheet in sheets if not _usable_sheet(sheet)]
    if missing and not allow_missing:
        detail = "\n".join(f"  - {sheet}: {_missing_reason(sheet)}" for sheet in missing)
        raise PlotError(
            f"cannot bundle a complete review set: {len(missing)} of {len(sheets)} "
            f"sheet(s) unusable. No bundle written.\n{detail}\n"
            "Build the missing sheets, or pass --allow-missing for a triage bundle "
            "(written as *.partial.pdf, still exit 4).",
            kind="bundle",
        )

    target.parent.mkdir(parents=True, exist_ok=True)
    final = target if not missing else target.with_suffix(".partial.pdf")
    policy = _policy()
    with tempfile.TemporaryDirectory() as tmp:
        resolved = [
            sheet if _usable_sheet(sheet) else _placeholder_sheet(Path(tmp), index, sheet)
            for index, sheet in enumerate(sheets)
        ]
        pages = _merge_pdfs(resolved, final, Path(tmp), policy=policy, svg_backend=svg_backend)

    counted = _page_count(final)
    if counted != len(sheets):
        final.unlink(missing_ok=True)
        raise PlotError(
            f"bundle page count mismatch: {len(sheets)} sheet(s) in, {counted} page(s) out "
            f"(converter reported {pages}). No bundle written — a review PDF that is "
            "quietly short a sheet is the failure this refuses to ship.",
            kind="bundle",
        )
    log.info("bundled %d sheet(s) -> %s", counted, final)
    if missing:
        raise PlotError(
            f"incomplete review bundle written to {final} ({len(missing)} placeholder "
            f"page(s)). NOT FOR REVIEW. Missing: "
            + ", ".join(str(sheet) for sheet in missing),
            kind="bundle",
        )
    return final


def _usable_sheet(sheet: Path) -> bool:
    path = Path(sheet)
    return path.is_file() and path.stat().st_size > 0


def _missing_reason(sheet: Path) -> str:
    path = Path(sheet)
    if not path.exists():
        return "does not exist"
    if not path.is_file():
        return "is not a file"
    return "is zero bytes"


def _placeholder_sheet(tmp: Path, index: int, missing: Path) -> Path:
    """A generated page, never an edit to a real sheet (§6.8).

    The path is wrapped rather than set on one line: a page whose warning runs off
    the edge is a page whose warning does not work.
    """
    chunks = [str(missing)[start : start + 68] for start in range(0, len(str(missing)), 68)]
    lines = [f"{_MISSING_PAGE_TEXT} — NOT FOR REVIEW", *chunks]
    body = "".join(
        f'<text x="210" y="{120 + offset * 14}" font-family="sans-serif" '
        f'font-size="{16 if offset == 0 else 9}" text-anchor="middle" '
        f'fill="#000000">{_xml_escape(line)}</text>'
        for offset, line in enumerate(lines)
    )
    svg = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<svg xmlns="http://www.w3.org/2000/svg" width="420mm" height="297mm" '
        'viewBox="0 0 420 297">'
        '<rect width="420" height="297" fill="#ffffff"/>'
        f"{body}</svg>\n"
    )
    path = tmp / f"missing-{index:04d}.svg"
    path.write_text(svg, encoding="utf-8", newline="\n")
    return path


def _xml_escape(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _merge_pdfs(
    sheets: Sequence[Path],
    target: Path,
    tmp: Path,
    *,
    policy: EmitPolicy,
    svg_backend: SvgBackendName,
) -> int:
    """Bundle. All-SVG goes through rsvg in one call; anything else merges PDFs."""
    for sheet in sheets:
        if sheet.suffix.lower() not in (".svg", ".pdf"):
            raise PlotError(
                f"cannot bundle {sheet.name}: only .svg and .pdf sheets can be collated",
                kind="bundle",
            )
    needs_svg = any(sheet.suffix.lower() == ".svg" for sheet in sheets)
    backend = find_svg_backend(svg_backend) if needs_svg else None
    staged = target.with_name(target.name + ".plot-tmp")
    try:
        # rsvg bundles many SVGs into one N-page PDF in argument order, in a single
        # call and with no PDF-merge dependency at all. Verified: rc 0, Pages: 3.
        if needs_svg and backend is not None and backend[0] == "rsvg" and not any(
            sheet.suffix.lower() == ".pdf" for sheet in sheets
        ):
            _run_rsvg_cmd(
                [str(backend[1]), "-f", "pdf", "-o", str(staged)]
                + [str(sheet) for sheet in sheets],
                policy,
            )
        else:
            pages = [
                sheet
                if sheet.suffix.lower() == ".pdf"
                else _convert_one(backend, sheet, tmp, index, policy=policy)
                for index, sheet in enumerate(sheets)
            ]
            _append_pdfs(pages, staged)
        os.replace(staged, target)
        return len(sheets)
    finally:
        if staged.exists():
            staged.unlink()


def _convert_one(
    backend: tuple[str, object] | None,
    sheet: Path,
    tmp: Path,
    index: int,
    *,
    policy: EmitPolicy,
) -> Path:
    if backend is None:  # pragma: no cover — only reachable via a caller bug
        raise PlotError(
            f"cannot convert {sheet.name}: no SVG backend was resolved", kind="backend"
        )
    out = tmp / f"page-{index:04d}.pdf"
    _convert(backend, sheet, out, "pdf", dpi=300, policy=policy)
    return out


def _append_pdfs(pdfs: Sequence[Path], target: Path) -> None:
    try:
        from pypdf import PdfWriter  # type: ignore
    except ImportError:
        pdfunite = shutil.which("pdfunite")
        if pdfunite is None:
            raise PlotError(
                "merging PDF sheets needs pypdf (pip install 'technical_drawings_for_agents[plot]') or "
                "pdfunite (macOS 'brew install poppler', Debian/Ubuntu "
                "'apt-get install poppler-utils').",
                kind="backend",
            ) from None
        completed = subprocess.run(
            [pdfunite, *[str(pdf) for pdf in pdfs], str(target)],
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        if completed.returncode != 0:
            raise PlotError(
                f"pdfunite failed (rc={completed.returncode}): "
                f"{(completed.stderr or '').strip()[:500]}",
                kind="bundle",
            )
        return
    writer = PdfWriter()
    try:
        for pdf in pdfs:
            writer.append(str(pdf))
        with open(target, "wb") as handle:
            writer.write(handle)
    finally:
        writer.close()


def _page_count(pdf: Path) -> int:
    """Read the page count back. Never trust the converter's own arithmetic."""
    try:
        from pypdf import PdfReader  # type: ignore
    except ImportError:
        pass
    else:
        try:
            return len(PdfReader(str(pdf)).pages)
        except Exception as exc:  # noqa: BLE001 — an unreadable bundle is a bundle failure
            raise PlotError(f"cannot read back {pdf.name} to count pages: {exc}", kind="bundle")
    pdfinfo = shutil.which("pdfinfo")
    if pdfinfo is None:
        raise PlotError(
            f"cannot verify the page count of {pdf.name}: neither pypdf "
            "(pip install 'technical_drawings_for_agents[plot]') nor pdfinfo (poppler) is available. "
            "Refusing to hand over an unverified review bundle.",
            kind="backend",
        )
    completed = subprocess.run(
        [pdfinfo, str(pdf)], capture_output=True, text=True, timeout=60, check=False
    )
    if completed.returncode != 0:
        raise PlotError(
            f"pdfinfo failed on {pdf.name}: {(completed.stderr or '').strip()[:500]}",
            kind="bundle",
        )
    match = re.search(r"^Pages:\s+(\d+)$", completed.stdout, re.MULTILINE)
    if match is None:
        raise PlotError(f"pdfinfo reported no page count for {pdf.name}", kind="bundle")
    return int(match.group(1))


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #


def _policy() -> EmitPolicy:
    try:
        return EmitPolicy.from_environment()
    except ProvenanceError as exc:
        raise PlotError(str(exc), kind="input") from exc


def _read_dxf(path: Path):
    try:
        return ezdxf.readfile(Path(path))
    except (OSError, ezdxf.DXFError) as exc:
        raise PlotError(f"cannot read DXF {path}: {exc}", kind="input") from exc


def _layout_by_name(doc, layout_name: str):
    try:
        return doc.layouts.get(layout_name)
    except (KeyError, ezdxf.DXFKeyError) as exc:
        raise PlotError(
            f"layout {layout_name!r} is not in this document; it has "
            f"{sorted(doc.layouts.names())}",
            kind="input",
        ) from exc


def _layout_name(target) -> str:
    return str(getattr(target, "name", "?"))


def _match_page_name(text: str) -> str:
    """Case-insensitive, whitespace-tolerant ``PAGE_SIZES`` lookup."""
    wanted = " ".join(text.split()).casefold()
    for key in ezdxf_layout.PAGE_SIZES:
        if key.casefold() == wanted:
            return key
    raise PlotError(
        f"--page {text!r} is not a known paper size; expected one of "
        f"{sorted(ezdxf_layout.PAGE_SIZES)}, 'WIDTHxHEIGHT' in mm, or "
        "'custom:WIDTHxHEIGHT'",
        kind="unsupported",
    )


def _require_ezdxf() -> None:
    """R2: ``SVGBackend``/``layout.Page``/``recorder`` were verified at 1.4.4 only."""
    installed = tuple(ezdxf.version[:2])
    if installed < MIN_EZDXF:
        raise PlotError(
            f"technical_drawings_for_agents plot needs ezdxf >= {'.'.join(str(n) for n in MIN_EZDXF)} for "
            f"SVGBackend/layout.Page/recorder; found {ezdxf.__version__}. "
            "The legacy 'render' path still works on older ezdxf.",
            kind="backend",
        )
