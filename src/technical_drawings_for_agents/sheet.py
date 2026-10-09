"""Paper-space sheets with an asserted plot scale (1:N on a declared ISO 216 size).

Why this module exists, and why it is separate from :mod:`technical_drawings_for_agents.svg`.

The legacy path is a **pixel canvas**: :class:`~technical_drawings_for_agents.svg.ViewBox` auto-fits a
model extent into whatever canvas the caller chose, so the px/m ratio is an accident of
the canvas size. Such a sheet can draw a scale bar that is true to *itself*, but it
cannot prove that the ratio printed in its title block is the ratio a scale rule will
measure on paper — and a sheet that misstates its own scale is the fastest way for a
drawing to fail scrutiny.

This module is the opt-in path for sheets that need that proof:

* **Paper is millimetres.** The emitted root carries ``width="420mm"`` with
  ``viewBox="0 0 420 297"``, so one SVG user unit is exactly one millimetre of paper.
  There is no dpi anywhere and no ``preserveAspectRatio`` scaling to guess at.
* **Model is metres**, matching :class:`~technical_drawings_for_agents.dxf.DxfBuilder`. The only
  model→paper mapping is the arithmetic ``mm = m * 1000 / N`` inside
  :meth:`Viewport.point`. It is deliberately *not* an SVG ``transform="scale(...)"``,
  which would multiply every stroke width and font size by 1/N.
* **The printed scale, the bar geometry, the tick labels and the north bearing are
  derived**, never typed — so they cannot be mistyped, and
  :mod:`technical_drawings_for_agents.validate` can recompute all of them from the record this module
  emits into the SVG.

Two assemblers coexist on purpose (:class:`~technical_drawings_for_agents.svg.Drawing` in pixels,
:class:`SheetDrawing` in millimetres). No shared base class: the single most dangerous
mistake available here is a number in the wrong unit, and two types keep the unit
visible at every call site.

.. warning::
   The legacy ``svg_*`` primitives default to ``stroke_width=1`` / ``1.5`` and
   ``font_size=11`` — **pixel** defaults, about 0.26 mm at 96 dpi. On a millimetre
   sheet those become 1.0–1.5 mm lines and 11 mm text, i.e. 4× to 40× too heavy. A
   generator using :class:`SheetDrawing` must pass explicit millimetre widths and font
   sizes. :data:`LW_DEFAULT_MM` covers this module's own furniture only.

Open questions deliberately left open rather than guessed at (P1 spec §8):

* **ISO 5457 frame margins** are not encoded. The 10 mm default in :class:`Margins` is
  an explicitly labelled **house default**, not a standards citation.
* **Furniture dimensions** — bar height, tick overshoot, label baselines, the 18 mm
  north arrow — are house constants of this module, stated so independent
  implementations agree. They are *not* sourced from a drafting standard.
* **Grid vs true north** is not computed. ``north.model_bearing_deg`` is a declared
  input; meridian convergence is P8/#60's.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field, replace
from html import unescape
from pathlib import Path
from typing import Any

import yaml


class SheetError(ValueError):
    """Raised when a sheet config, plot scale or viewport binding is malformed."""


# --------------------------------------------------------------------------- #
# constants
# --------------------------------------------------------------------------- #

#: ISO 216 A-series trim sizes as ``(short_edge_mm, long_edge_mm)``. Anything outside
#: this table is rejected loudly rather than guessed at: an unbounded size would mean
#: inventing frame conventions we do not have.
PAPER_SIZES_MM: dict[str, tuple[float, float]] = {
    "A0": (841.0, 1189.0),
    "A1": (594.0, 841.0),
    "A2": (420.0, 594.0),
    "A3": (297.0, 420.0),
    "A4": (210.0, 297.0),
}

#: The **round-numbers** series ``scale: fit`` searches, ascending. It includes 1250 and
#: 2500 because survey and site plans use them daily — they are round enough to set on a
#: scale rule, which is what ``fit`` needs. Membership here is *not* a claim about ISO
#: 5455; see :data:`ISO_PREFERRED_DENOMINATORS`.
PREFERRED_DENOMINATORS: tuple[int, ...] = (
    1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500,
    1000, 1250, 2000, 2500, 5000, 10000, 20000, 25000, 50000, 100000,
)

#: The strict 1/2/5-decade set. :attr:`PlotScale.is_preferred` reports membership of
#: *this* set, and drives the "not an ISO 5455 preferred ratio" warning. Two concepts,
#: two names, no fudge: 1:1250 is a legitimate `fit` target and a non-preferred ratio.
ISO_PREFERRED_DENOMINATORS: tuple[int, ...] = (
    1, 2, 5, 10, 20, 50, 100, 200, 500,
    1000, 2000, 5000, 10000, 20000, 50000, 100000,
)

SHEET_METADATA_SCHEMA = "technical_drawings_for_agents/sheet/1"
SHEET_METADATA_ID = "technical_drawings_for_agents-sheet"

#: Provisional stroke width for this module's own furniture, in millimetres. The real
#: lineweight/linetype table is P7/#59; this is not it.
LW_DEFAULT_MM = 0.25

#: Absorbs IEEE-754 representation of exact millimetre arithmetic and nothing more. A
#: tolerance that hides a 1 mm overflow is a tolerance that hides a mistake.
FIT_TOL_MM = 1e-6

#: House furniture dimensions (paper mm). Stated so two implementations agree; not
#: sourced from a drafting standard (spec §8.4).
SCALE_BAR_INSET_MM = 6.0
SCALE_BAR_HEIGHT_MM = 3.0
SCALE_BAR_TICK_OVERSHOOT_MM = 1.5
SCALE_BAR_TICK_BASELINE_MM = 4.5
SCALE_BAR_RATIO_BASELINE_MM = 8.5
SCALE_BAR_FONT_MM = 3.0
NORTH_ARROW_INSET_MM = 10.0
NORTH_ARROW_SIZE_MM = 18.0
NORTH_LABEL_FONT_MM = 4.0

#: The reservation name that :class:`SheetDrawing` fills with a title-block placeholder.
TITLE_BLOCK_REGION = "title-block"

_ORIENTATIONS = ("landscape", "portrait")
_EDGES = ("left", "right", "top", "bottom")
_SCALE_BAR_UNITS = ("m", "km")
_INK = "#111111"
_SEGMENT_FILLS = ("#ffffff", "#d9e2ec")


# --------------------------------------------------------------------------- #
# geometry primitives
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Margins:
    """Frame insets in paper millimetres. 10 mm is a **house default** (spec §8.1)."""

    left_mm: float = 10.0
    right_mm: float = 10.0
    top_mm: float = 10.0
    bottom_mm: float = 10.0


@dataclass(frozen=True)
class PaperSize:
    """An ISO 216 size plus an orientation. Portrait is ``width = short edge``."""

    name: str
    orientation: str

    def __post_init__(self) -> None:
        name = str(self.name).strip().upper()
        orientation = str(self.orientation).strip().lower()
        if name not in PAPER_SIZES_MM:
            raise SheetError(
                f"sheet.size {self.name!r} is not a supported ISO 216 size "
                f"(available: {', '.join(PAPER_SIZES_MM)})"
            )
        if orientation not in _ORIENTATIONS:
            raise SheetError(
                f"sheet.orientation {self.orientation!r} must be one of {list(_ORIENTATIONS)}"
            )
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "orientation", orientation)

    @property
    def width_mm(self) -> float:
        short, long = PAPER_SIZES_MM[self.name]
        return short if self.orientation == "portrait" else long

    @property
    def height_mm(self) -> float:
        short, long = PAPER_SIZES_MM[self.name]
        return long if self.orientation == "portrait" else short


@dataclass(frozen=True)
class Reservation:
    """A named strip cut off one edge of the free rectangle, in declared order."""

    name: str
    edge: str
    size_mm: float


@dataclass(frozen=True)
class SheetFrame:
    """An axis-aligned rectangle in paper millimetres, origin top-left, +y **down**.

    ``+y`` down matches the SVG coordinate sense, so no furniture code flips a sign.
    Model ``+y`` up is handled exactly once, inside :meth:`Viewport.point`.
    """

    x_mm: float
    y_mm: float
    width_mm: float
    height_mm: float

    @property
    def right_mm(self) -> float:
        return self.x_mm + self.width_mm

    @property
    def bottom_mm(self) -> float:
        return self.y_mm + self.height_mm

    def contains_mm(self, x_mm: float, y_mm: float, tol_mm: float = 0.0) -> bool:
        return (
            self.x_mm - tol_mm <= x_mm <= self.right_mm + tol_mm
            and self.y_mm - tol_mm <= y_mm <= self.bottom_mm + tol_mm
        )


@dataclass(frozen=True)
class PlotScale:
    """An exact 1:N plot scale. N is a positive integer denominator."""

    denominator: int

    def __post_init__(self) -> None:
        # ``isinstance(True, int)`` is True in Python, so ``scale: true`` would
        # otherwise be accepted as 1:1. Guard the bool first.
        if isinstance(self.denominator, bool) or not isinstance(self.denominator, int):
            raise SheetError("plot scale denominator must be a positive integer")
        if self.denominator < 1:
            raise SheetError("plot scale denominator must be a positive integer")

    @property
    def text(self) -> str:
        return f"1:{self.denominator}"

    @property
    def is_preferred(self) -> bool:
        return self.denominator in ISO_PREFERRED_DENOMINATORS

    def mm_per_m(self) -> float:
        return 1000.0 / self.denominator

    def paper_mm(self, model_m: float) -> float:
        return float(model_m) * self.mm_per_m()

    def model_m(self, paper_mm: float) -> float:
        return float(paper_mm) / self.mm_per_m()

    @classmethod
    def parse(cls, text: str) -> "PlotScale":
        """Parse exactly ``"1:N"`` (optional surrounding whitespace).

        ``str(text)`` is deliberate: a non-string YAML value then fails the regex and
        raises :class:`SheetError` instead of a bare ``TypeError``. ``"1:1250 (A3)"`` is
        rejected — the paper size belongs to ``sheet.size``, not smuggled into a scale
        string, and accepting decorated variants means writing a parser for prose.
        """
        match = re.fullmatch(r"\s*1:([1-9][0-9]*)\s*", str(text))
        if match is None:
            raise SheetError(
                f"cannot parse plot scale {text!r}: expected '1:N' with an integer N"
            )
        return cls(int(match.group(1)))


@dataclass(frozen=True)
class NorthRef:
    """Bearing of north measured from model +y, clockwise positive."""

    model_bearing_deg: float = 0.0
    label: str = "N"


@dataclass(frozen=True)
class ScaleBarSpec:
    length_m: float | None = None
    divisions: int = 5
    unit: str = "m"


class _ExtentRecorder:
    """Accumulates the paper bbox of every point mapped through a viewport.

    :class:`Viewport` stays frozen; a :class:`SheetDrawing` hands it one of these so
    ``drawn_extent_mm`` can be emitted and containment made *checkable* (spec §3.8).
    """

    def __init__(self) -> None:
        self._bbox: list[float] | None = None

    def record(self, x_mm: float, y_mm: float) -> None:
        if self._bbox is None:
            self._bbox = [x_mm, y_mm, x_mm, y_mm]
            return
        self._bbox = [
            min(self._bbox[0], x_mm),
            min(self._bbox[1], y_mm),
            max(self._bbox[2], x_mm),
            max(self._bbox[3], y_mm),
        ]

    def bbox(self) -> list[float] | None:
        return None if self._bbox is None else list(self._bbox)


@dataclass(frozen=True)
class Viewport:
    """Binds a model extent to a plot scale inside a paper frame.

    Isotropic by construction — one :class:`PlotScale`, so a vertically exaggerated
    view is *not expressible*. Exaggeration needs two scales, two bars and a mandatory
    "VERTICAL EXAGGERATION ×n" annotation; getting that wrong is worse than not having
    it, so it is a follow-up (spec §7.3).
    """

    extent_m: tuple[float, float, float, float]
    scale: PlotScale
    frame: SheetFrame
    rotation_deg: float = 0.0
    recorder: _ExtentRecorder | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        extent = tuple(self.extent_m)
        if len(extent) != 4:
            raise SheetError("viewport.extent_m must be [xmin, ymin, xmax, ymax]")
        for index, value in enumerate(extent):
            if not _is_number(value):
                raise SheetError(f"viewport.extent_m[{index}] must be a finite number")
        xmin, ymin, xmax, ymax = (float(value) for value in extent)
        if xmax <= xmin:
            raise SheetError(
                f"viewport.extent_m: xmax ({xmax}) must be greater than xmin ({xmin})"
            )
        if ymax <= ymin:
            raise SheetError(
                f"viewport.extent_m: ymax ({ymax}) must be greater than ymin ({ymin})"
            )
        if not _is_number(self.rotation_deg):
            raise SheetError("viewport.rotation_deg must be a finite number")
        object.__setattr__(self, "extent_m", (xmin, ymin, xmax, ymax))
        object.__setattr__(self, "rotation_deg", float(self.rotation_deg) % 360.0)

    # --- the model -> paper map (the only place the two mappings compose) --- #

    def point(self, model_x: float, model_y: float) -> tuple[float, float]:
        """Map a model point (metres) to a paper point (millimetres)."""
        x_mm, y_mm = self._point(model_x, model_y)
        if self.recorder is not None:
            self.recorder.record(x_mm, y_mm)
        return x_mm, y_mm

    def _point(self, model_x: float, model_y: float) -> tuple[float, float]:
        mm_per_m = 1000.0 / self.scale.denominator
        cx, cy = self.centre_m
        dx, dy = model_x - cx, model_y - cy
        if self.rotation_deg:
            angle = math.radians(self.rotation_deg)
            dx, dy = (
                dx * math.cos(angle) - dy * math.sin(angle),
                dx * math.sin(angle) + dy * math.cos(angle),
            )
        fx = self.frame.x_mm + self.frame.width_mm / 2.0
        fy = self.frame.y_mm + self.frame.height_mm / 2.0
        # -dy: model +y is up, paper +y is down.
        return (fx + dx * mm_per_m, fy - dy * mm_per_m)

    def length_mm(self, model_m: float) -> float:
        return self.scale.paper_mm(model_m)

    def model_m(self, paper_mm: float) -> float:
        return self.scale.model_m(paper_mm)

    def contains(self, model_x: float, model_y: float, tol_m: float = 0.0) -> bool:
        x_mm, y_mm = self._point(model_x, model_y)
        return self.frame.contains_mm(x_mm, y_mm, tol_mm=self.length_mm(tol_m))

    # --- derived facts --- #

    @property
    def required_mm(self) -> tuple[float, float]:
        """Paper size of the axis-aligned bounding box of the rotated extent."""
        rw_m, rh_m = _rotated_extent_m(self.extent_m, self.rotation_deg)
        mm_per_m = self.scale.mm_per_m()
        return (rw_m * mm_per_m, rh_m * mm_per_m)

    @property
    def fits(self) -> bool:
        width_mm, height_mm = self.required_mm
        return (
            width_mm <= self.frame.width_mm + FIT_TOL_MM
            and height_mm <= self.frame.height_mm + FIT_TOL_MM
        )

    @property
    def centre_m(self) -> tuple[float, float]:
        xmin, ymin, xmax, ymax = self.extent_m
        return ((xmin + xmax) / 2.0, (ymin + ymax) / 2.0)

    @property
    def scale_text(self) -> str:
        return self.scale.text


@dataclass(frozen=True)
class Sheet:
    """A resolved piece of paper: size, margins, reserved regions, one viewport.

    ``warnings`` carries resolve-time notices that cannot be recovered from the emitted
    record — today only the ``on_overflow: fit`` resolution, because the word ``fit``
    deliberately never reaches the sheet, the metadata or the title block.
    """

    paper: PaperSize
    margins: Margins
    viewport: Viewport
    regions: dict[str, SheetFrame]
    north: NorthRef | None = None
    scale_bar: ScaleBarSpec = field(default_factory=ScaleBarSpec)
    source: Path | None = None
    warnings: tuple[str, ...] = ()

    @property
    def frame(self) -> SheetFrame:
        """The drawing frame: the paper rect inset by the margins."""
        return SheetFrame(
            self.margins.left_mm,
            self.margins.top_mm,
            self.paper.width_mm - self.margins.left_mm - self.margins.right_mm,
            self.paper.height_mm - self.margins.top_mm - self.margins.bottom_mm,
        )

    @property
    def scale_text(self) -> str:
        """The derived canonical ``"1:N"`` — what P9's SCALE field must render."""
        return self.viewport.scale.text

    # --- PaperFrame protocol -------------------------------------------------
    # The sheet-furniture consumer defined downstream reads a paper-space model
    # through a five-member protocol: `size`, `orientation`, `frame_mm()`,
    # `to_device()`, `device_per_mm()`. That consumer was built against a fake frame
    # so it could land independently, and this module never implemented the protocol,
    # so it could not be driven from a real sheet at all. These three methods and the
    # two properties below are that missing seam. They are pure reads of resolved
    # state, add no validation surface, and know nothing of the drawing lifecycle.

    @property
    def size(self) -> str:
        """ISO paper size name, e.g. ``"A3"``."""
        return self.paper.name

    @property
    def orientation(self) -> str:
        """``"portrait"`` or ``"landscape"``."""
        return self.paper.orientation

    def frame_mm(self) -> tuple[float, float, float, float]:
        """The inner frame rectangle in paper mm as ``(x, y, w, h)``.

        This is the FULL drawing frame, not the viewport frame: the title block sits
        inside the drawing frame and may occupy a region reserved away from the
        viewport, so returning the reserved-down viewport rectangle here would push
        that furniture off its own reservation.
        """
        return (self.frame.x_mm, self.frame.y_mm, self.frame.width_mm, self.frame.height_mm)

    def to_device(self, x_mm: float, y_mm: float) -> tuple[float, float]:
        """Paper mm -> emitted user units.

        The sheet emits with a mm-valued viewBox, so one user unit IS one
        millimetre and this is the identity. It exists so a consumer never assumes
        that, and so a future device-unit change has one place to land.
        """
        return (float(x_mm), float(y_mm))

    def device_per_mm(self) -> float:
        """Emitted user units per paper millimetre. See `to_device`."""
        return 1.0

    def metadata_json(self) -> str:
        return sheet_metadata_json(self)


# --------------------------------------------------------------------------- #
# config loading
# --------------------------------------------------------------------------- #


def load_sheet_config(path: str | Path) -> Sheet | None:
    """Load the ``sheet:`` block from a YAML file. Returns ``None`` when absent.

    Absence is the opt-out and is never an error. A present-but-invalid block always
    raises :class:`SheetError` with a message naming the offending YAML path.

    An unreadable file or unparseable YAML is *not* a malformed sheet block, so
    ``OSError`` / ``yaml.YAMLError`` propagate unwrapped — the CLI maps those to its
    usage exit code (2) and a :class:`SheetError` to its failure exit code (1).
    """
    config_path = Path(path)
    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "sheet" not in data:
        return None
    block = data["sheet"]
    if not isinstance(block, dict):
        raise SheetError(f"{config_path.name}: sheet must be a mapping")
    return replace(resolve_sheet(block), source=config_path)


def resolve_sheet(config: dict, *, context: str = "sheet") -> Sheet:
    """Build a :class:`Sheet` from an already-parsed mapping (the testable core)."""
    if not isinstance(config, dict):
        raise SheetError(f"{context} must be a mapping")
    # Convenience: accept a whole meta.yaml-shaped mapping that wraps the block.
    if "sheet" in config and "size" not in config:
        block = config["sheet"]
        if not isinstance(block, dict):
            raise SheetError(f"{context} must be a mapping")
        config = block

    _reject_unknown(
        config,
        {"size", "orientation", "margins_mm", "reserve", "viewport", "north", "scale_bar"},
        context,
    )
    if "size" not in config:
        raise SheetError(f"{context}.size is required (one of {', '.join(PAPER_SIZES_MM)})")
    paper = _load_paper(config["size"], config.get("orientation", "landscape"))
    margins = _load_margins(config.get("margins_mm", 10), paper, f"{context}.margins_mm")
    drawing_frame = SheetFrame(
        margins.left_mm,
        margins.top_mm,
        paper.width_mm - margins.left_mm - margins.right_mm,
        paper.height_mm - margins.top_mm - margins.bottom_mm,
    )
    reservations = _load_reservations(config.get("reserve", []), f"{context}.reserve")
    regions, viewport_frame = _cut_reservations(
        drawing_frame, reservations, f"{context}.reserve"
    )

    if "viewport" not in config:
        raise SheetError(f"{context} needs a viewport: {{extent_m, scale}}")
    viewport, warnings = _load_viewport(
        config["viewport"], viewport_frame, paper, f"{context}.viewport"
    )
    scale_bar = _load_scale_bar(config.get("scale_bar"), viewport, f"{context}.scale_bar")
    north = _load_north(config.get("north"), f"{context}.north")
    return Sheet(
        paper=paper,
        margins=margins,
        viewport=viewport,
        regions=regions,
        north=north,
        scale_bar=scale_bar,
        warnings=tuple(warnings),
    )


def _load_paper(size: Any, orientation: Any) -> PaperSize:
    if not isinstance(size, str):
        raise SheetError(
            f"sheet.size must be an ISO 216 size name, got {size!r} "
            f"(available: {', '.join(PAPER_SIZES_MM)})"
        )
    if not isinstance(orientation, str):
        raise SheetError(
            f"sheet.orientation {orientation!r} must be one of {list(_ORIENTATIONS)}"
        )
    return PaperSize(size, orientation)


def _load_margins(raw: Any, paper: PaperSize, ctx: str) -> Margins:
    if isinstance(raw, dict):
        _reject_unknown(raw, set(_EDGES), ctx)
        values = {
            edge: _positive_number(raw.get(edge, 10), f"{ctx}.{edge}") for edge in _EDGES
        }
    else:
        margin = _positive_number(raw, ctx)
        values = {edge: margin for edge in _EDGES}

    if values["left"] + values["right"] >= paper.width_mm:
        total = values["left"] + values["right"]
        raise SheetError(
            f"{ctx}: left+right ({total:.1f}) exceeds the {paper.name} width "
            f"({paper.width_mm:.1f})"
        )
    if values["top"] + values["bottom"] >= paper.height_mm:
        total = values["top"] + values["bottom"]
        raise SheetError(
            f"{ctx}: top+bottom ({total:.1f}) exceeds the {paper.name} height "
            f"({paper.height_mm:.1f})"
        )
    return Margins(
        left_mm=values["left"],
        right_mm=values["right"],
        top_mm=values["top"],
        bottom_mm=values["bottom"],
    )


def _load_reservations(raw: Any, ctx: str) -> list[Reservation]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise SheetError(f"{ctx} must be a list of {{name, edge, size_mm}} mappings")
    reservations: list[Reservation] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        item_ctx = f"{ctx}[{index}]"
        if not isinstance(item, dict):
            raise SheetError(f"{item_ctx} must be a mapping of {{name, edge, size_mm}}")
        _reject_unknown(item, {"name", "edge", "size_mm"}, item_ctx)
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            raise SheetError(f"{item_ctx}.name must be a non-empty string")
        if name in seen:
            raise SheetError(f"{item_ctx}.name {name!r} is already used")
        seen.add(name)
        edge = item.get("edge")
        if edge not in _EDGES:
            raise SheetError(f"{item_ctx}.edge {edge!r} must be one of {list(_EDGES)}")
        reservations.append(
            Reservation(
                name=name,
                edge=str(edge),
                size_mm=_positive_number(item.get("size_mm"), f"{item_ctx}.size_mm"),
            )
        )
    return reservations


def _cut_reservations(
    frame: SheetFrame, reservations: list[Reservation], ctx: str
) -> tuple[dict[str, SheetFrame], SheetFrame]:
    """Cut each reservation off the current free rect **in listed order**.

    Order-dependence is real (bottom-then-right != right-then-bottom) so it is declared
    in the data rather than resolved by a solver: a solver is another thing two
    implementations must reimplement identically; a list is not.
    """
    regions: dict[str, SheetFrame] = {}
    free = frame
    for index, reservation in enumerate(reservations):
        size = reservation.size_mm
        if reservation.edge in ("left", "right"):
            remaining = free.width_mm - size
            if remaining <= 0:
                raise SheetError(
                    f"{ctx}[{index}] ({reservation.name!r}, {reservation.edge}, "
                    f"{size:.1f} mm) leaves no room: free width would be {remaining:.1f} mm"
                )
            if reservation.edge == "left":
                region = SheetFrame(free.x_mm, free.y_mm, size, free.height_mm)
                free = SheetFrame(free.x_mm + size, free.y_mm, remaining, free.height_mm)
            else:
                region = SheetFrame(free.right_mm - size, free.y_mm, size, free.height_mm)
                free = SheetFrame(free.x_mm, free.y_mm, remaining, free.height_mm)
        else:
            remaining = free.height_mm - size
            if remaining <= 0:
                raise SheetError(
                    f"{ctx}[{index}] ({reservation.name!r}, {reservation.edge}, "
                    f"{size:.1f} mm) leaves no room: free height would be {remaining:.1f} mm"
                )
            if reservation.edge == "top":
                region = SheetFrame(free.x_mm, free.y_mm, free.width_mm, size)
                free = SheetFrame(free.x_mm, free.y_mm + size, free.width_mm, remaining)
            else:
                region = SheetFrame(free.x_mm, free.bottom_mm - size, free.width_mm, size)
                free = SheetFrame(free.x_mm, free.y_mm, free.width_mm, remaining)
        regions[reservation.name] = region
    return regions, free


def _load_viewport(
    raw: Any, frame: SheetFrame, paper: PaperSize, ctx: str
) -> tuple[Viewport, list[str]]:
    if not isinstance(raw, dict):
        raise SheetError(f"{ctx} must be a mapping of {{extent_m, scale}}")
    _reject_unknown(raw, {"extent_m", "scale", "rotation_deg", "on_overflow", "align"}, ctx)
    extent = _extent(raw.get("extent_m"), f"{ctx}.extent_m")
    rotation = _angle(raw.get("rotation_deg", 0.0), f"{ctx}.rotation_deg")

    align = raw.get("align", "center")
    if align != "center":
        raise SheetError(f"{ctx}.align: only 'center' is supported (see issue #53 follow-ups)")
    on_overflow = raw.get("on_overflow", "error")
    if on_overflow not in ("error", "fit"):
        raise SheetError(f"{ctx}.on_overflow {on_overflow!r} must be 'error' or 'fit'")

    if "scale" not in raw:
        raise SheetError(f"{ctx}.scale is required: a positive integer denominator or 'fit'")
    requested = raw["scale"]
    if requested == "fit":
        denominator = _smallest_round_denominator(extent, frame, rotation, ctx)
    elif isinstance(requested, int) and not isinstance(requested, bool) and requested >= 1:
        denominator = requested
    else:
        raise SheetError(
            f"{ctx}.scale must be a positive integer denominator (1:N) or the string "
            f"'fit', got {requested!r}"
        )

    viewport = Viewport(extent, PlotScale(denominator), frame, rotation)
    if viewport.fits:
        return viewport, []

    fitted = _smallest_round_denominator(extent, frame, rotation, ctx)
    if on_overflow == "fit":
        return (
            Viewport(extent, PlotScale(fitted), frame, rotation),
            [f"sheet: 1:{denominator} does not fit; on_overflow: fit resolved to 1:{fitted}"],
        )

    xmin, ymin, xmax, ymax = extent
    required_w, required_h = viewport.required_mm
    raise SheetError(
        f"{ctx}: the extent ({xmax - xmin:.1f} × {ymax - ymin:.1f} m) needs "
        f"{required_w:.3f} × {required_h:.3f} mm at 1:{denominator}, but the "
        f"{paper.name} {paper.orientation} viewport frame is {frame.width_mm:.3f} × "
        f"{frame.height_mm:.3f} mm. Smallest preferred scale that fits: 1:{fitted}. "
        f"Set {ctx}.scale, enlarge sheet.size, or set {ctx}.on_overflow: fit."
    )


def _load_north(raw: Any, ctx: str) -> NorthRef | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise SheetError(f"{ctx} must be a mapping of {{model_bearing_deg, label}}")
    _reject_unknown(raw, {"model_bearing_deg", "label"}, ctx)
    label = raw.get("label", "N")
    if not isinstance(label, str) or not label.strip():
        raise SheetError(f"{ctx}.label must be a non-empty string")
    return NorthRef(
        model_bearing_deg=_angle(raw.get("model_bearing_deg", 0.0), f"{ctx}.model_bearing_deg"),
        label=label,
    )


def _load_scale_bar(raw: Any, viewport: Viewport, ctx: str) -> ScaleBarSpec:
    if raw is None:
        return ScaleBarSpec()
    if not isinstance(raw, dict):
        raise SheetError(f"{ctx} must be a mapping of {{length_m, divisions, unit}}")
    _reject_unknown(raw, {"length_m", "divisions", "unit"}, ctx)
    length = raw.get("length_m")
    if length is not None:
        length = _checked_bar_length(viewport, length, f"{ctx}.length_m")
    divisions = raw.get("divisions", 5)
    if isinstance(divisions, bool) or not isinstance(divisions, int) or divisions < 1:
        raise SheetError(f"{ctx}.divisions must be an integer >= 1, got {divisions!r}")
    unit = raw.get("unit", "m")
    if unit not in _SCALE_BAR_UNITS:
        raise SheetError(f"{ctx}.unit {unit!r} must be 'm' or 'km'")
    return ScaleBarSpec(length_m=length, divisions=divisions, unit=unit)


# --------------------------------------------------------------------------- #
# scale resolution
# --------------------------------------------------------------------------- #


def _rotated_extent_m(
    extent_m: tuple[float, float, float, float], rotation_deg: float
) -> tuple[float, float]:
    xmin, ymin, xmax, ymax = extent_m
    w_m, h_m = xmax - xmin, ymax - ymin
    angle = math.radians(rotation_deg)
    return (
        abs(w_m * math.cos(angle)) + abs(h_m * math.sin(angle)),
        abs(w_m * math.sin(angle)) + abs(h_m * math.cos(angle)),
    )


def _smallest_round_denominator(
    extent: tuple[float, float, float, float],
    frame: SheetFrame,
    rotation_deg: float,
    ctx: str,
) -> int:
    """First entry of :data:`PREFERRED_DENOMINATORS` whose extent fits the frame."""
    for denominator in PREFERRED_DENOMINATORS:
        if Viewport(extent, PlotScale(denominator), frame, rotation_deg).fits:
            return denominator
    rw_m, rh_m = _rotated_extent_m(extent, rotation_deg)
    required = max(
        1,
        math.ceil(
            max(rw_m * 1000.0 / frame.width_mm, rh_m * 1000.0 / frame.height_mm)
        ),
    )
    raise SheetError(
        f"{ctx}: no preferred scale fits: the extent needs 1:{required} or smaller; "
        "widen the paper or set an explicit scale"
    )


def _bar_available_width_mm(frame: SheetFrame) -> float:
    """Paper width a scale bar may occupy inside its own inset.

    A bar drawn flush to the frame edge has no clearance from the border and is not
    printable as drawn, so the usable width is the frame less the inset on **both**
    sides. (This corrects P1 base-spec §3.2, which permitted a bar exactly as wide as
    the frame — 500 m at A3 1:1250 is exactly 400.0 mm, which acceptance test 16
    requires to be rejected.)
    """
    return frame.width_mm - 2.0 * SCALE_BAR_INSET_MM


def _checked_bar_length(viewport: Viewport, length_m: Any, ctx: str) -> float:
    length = _positive_number(length_m, ctx)
    needed_mm = viewport.length_mm(length)
    available_mm = _bar_available_width_mm(viewport.frame)
    if needed_mm > available_mm + FIT_TOL_MM:
        raise SheetError(
            f"{ctx} {_fmt_measure(length)} needs {needed_mm:.3f} mm at "
            f"{viewport.scale.text} but only {available_mm:.3f} mm is available inside "
            f"the {viewport.frame.width_mm:.3f} mm viewport frame "
            f"({SCALE_BAR_INSET_MM:g} mm inset each side)"
        )
    return length


def _derived_bar_length(viewport: Viewport) -> float:
    """Largest ``{1, 2, 5} x 10**n`` metres at or below a quarter-frame target.

    A round bar length is the point of a scale bar — nobody measures against 43 m — so
    failing to find one is an error, not a fallback.
    """
    target_m = viewport.model_m(0.25 * viewport.frame.width_mm)
    available_mm = _bar_available_width_mm(viewport.frame)
    candidates = [
        value
        for exponent in range(-3, 7)
        for multiplier in (1.0, 2.0, 5.0)
        for value in (multiplier * (10.0**exponent),)
        if value <= target_m + 1e-12 and viewport.length_mm(value) <= available_mm + FIT_TOL_MM
    ]
    if not candidates:
        raise SheetError(
            "cannot derive a round scale-bar length for a "
            f"{viewport.frame.width_mm:.1f} mm viewport at {viewport.scale.text}; "
            "set sheet.scale_bar.length_m"
        )
    return max(candidates)


def _resolved_bar_length(sheet: Sheet) -> float:
    if sheet.scale_bar.length_m is None:
        return _derived_bar_length(sheet.viewport)
    return float(sheet.scale_bar.length_m)


def _north_paper_deg(rotation_deg: float, model_bearing_deg: float) -> float:
    """Where model north lands on paper, in SVG's clockwise degrees from "up".

    The sign is NEGATIVE rotation. ``Viewport.point`` turns the model by
    ``+rotation_deg`` to lay it on the sheet, so a feature bearing north in the model
    comes to rest ``rotation_deg`` the OTHER way round the paper. The formula read
    ``(rotation_deg - model_bearing_deg)`` until 2026-09-18, which agrees with the
    viewport only at 0 and 180 degrees — so every sheet at a right-angle rotation drew
    its north arrow pointing exactly the wrong way, and a river-channel section pack, which
    is rotated 90 degrees to put the river's long axis across an A3, drew north to the
    right while the viewport put it to the left. Caught by an engineer reading the sheet.

    The test for this derives the expected bearing FROM the viewport. The previous one
    restated this expression, so it could not have failed.
    """
    return (-rotation_deg - model_bearing_deg) % 360.0


# --------------------------------------------------------------------------- #
# furniture
# --------------------------------------------------------------------------- #


def sheet_frame(sheet: Sheet, *, stroke_mm: float = 0.35) -> str:
    """Stroke the drawing frame — the paper rect inset by the margins."""
    frame = sheet.frame
    return (
        f'<rect class="sheet-frame" x="{_fmt3(frame.x_mm)}" y="{_fmt3(frame.y_mm)}" '
        f'width="{_fmt3(frame.width_mm)}" height="{_fmt3(frame.height_mm)}" fill="none" '
        f'stroke="{_INK}" stroke-width="{_fmt3(stroke_mm)}"/>'
    )


def sheet_scale_bar(
    viewport: Viewport,
    frame: SheetFrame,
    *,
    length_m: float | None = None,
    divisions: int = 5,
    unit: str = "m",
) -> str:
    """A scale bar whose every glyph is derived, anchored in paper millimetres.

    There is deliberately **no** caption parameter. The legacy
    :func:`~technical_drawings_for_agents.svg.svg_scale_bar` has a free ``label=`` slot, and a real sheet
    used it to paint a hand-spaced ``"0                50 m"`` tick row over a correctly
    computed one — a *measurement claim* the bar's own geometry contradicted. Removing
    the slot removes the defect; validation check V7 makes any re-introduction (an extra
    text node inside the group) fail by name.

    The bar is anchored to the *frame*, never to a model point: furniture whose position
    depends on the extent moves when the extent is nudged, which is how two scale bars
    on one real sheet ended up nearly colliding.
    """
    if isinstance(divisions, bool) or not isinstance(divisions, int) or divisions < 1:
        raise SheetError(f"sheet.scale_bar.divisions must be an integer >= 1, got {divisions!r}")
    if unit not in _SCALE_BAR_UNITS:
        raise SheetError(f"sheet.scale_bar.unit {unit!r} must be 'm' or 'km'")
    bar_viewport = viewport if viewport.frame == frame else replace(viewport, frame=frame)
    if length_m is None:
        length = _derived_bar_length(bar_viewport)
    else:
        length = _checked_bar_length(bar_viewport, length_m, "sheet.scale_bar.length_m")

    total_mm = viewport.length_mm(length)
    seg_mm = total_mm / divisions
    x0 = frame.x_mm + SCALE_BAR_INSET_MM
    y0 = (
        frame.bottom_mm
        - SCALE_BAR_INSET_MM
        - SCALE_BAR_HEIGHT_MM
        - SCALE_BAR_RATIO_BASELINE_MM
    )
    bar_bottom = y0 + SCALE_BAR_HEIGHT_MM

    parts = [
        f'<g class="scale-bar" data-tdfa-bar="length_m={_fmt_measure(length)};'
        f'divisions={divisions};unit={unit};total_mm={_fmt3(total_mm)}">'
    ]
    for index in range(divisions):
        parts.append(
            f'<rect x="{_fmt3(x0 + index * seg_mm)}" y="{_fmt3(y0)}" '
            f'width="{_fmt3(seg_mm)}" height="{_fmt3(SCALE_BAR_HEIGHT_MM)}" '
            f'fill="{_SEGMENT_FILLS[index % 2]}" stroke="{_INK}" '
            f'stroke-width="{_fmt3(LW_DEFAULT_MM)}"/>'
        )
    parts.append(
        f'<line x1="{_fmt3(x0)}" y1="{_fmt3(bar_bottom)}" x2="{_fmt3(x0 + total_mm)}" '
        f'y2="{_fmt3(bar_bottom)}" stroke="{_INK}" stroke-width="{_fmt3(LW_DEFAULT_MM)}"/>'
    )
    for index in range(divisions + 1):
        x_mm = x0 + index * seg_mm
        parts.append(
            f'<line x1="{_fmt3(x_mm)}" y1="{_fmt3(bar_bottom)}" x2="{_fmt3(x_mm)}" '
            f'y2="{_fmt3(bar_bottom + SCALE_BAR_TICK_OVERSHOOT_MM)}" stroke="{_INK}" '
            f'stroke-width="{_fmt3(LW_DEFAULT_MM)}"/>'
        )
        parts.append(
            f'<text x="{_fmt3(x_mm)}" '
            f'y="{_fmt3(bar_bottom + SCALE_BAR_TICK_BASELINE_MM)}" '
            f'font-size="{_fmt3(SCALE_BAR_FONT_MM)}" fill="{_INK}" text-anchor="middle" '
            f'font-family="monospace">'
            f'{_svg_escape(scale_bar_tick_label(length, divisions, index, unit))}</text>'
        )
    parts.append(
        f'<text x="{_fmt3(x0 + total_mm / 2.0)}" '
        f'y="{_fmt3(bar_bottom + SCALE_BAR_RATIO_BASELINE_MM)}" '
        f'font-size="{_fmt3(SCALE_BAR_FONT_MM)}" fill="{_INK}" text-anchor="middle" '
        f'font-family="monospace">{_svg_escape(viewport.scale.text)}</text>'
    )
    parts.append("</g>")
    return "\n".join(parts)


def scale_bar_tick_label(length_m: float, divisions: int, index: int, unit: str) -> str:
    """The derived label for tick ``index``. Shared with validation so V7 cannot drift."""
    value = float(length_m) * index / divisions
    if unit == "km":
        value = value / 1000.0
    suffix = f" {unit}" if index == divisions else ""
    return f"{_fmt_measure(value)}{suffix}"


def sheet_north_arrow(
    viewport: Viewport,
    frame: SheetFrame,
    *,
    model_bearing_deg: float = 0.0,
    label: str = "N",
    size_mm: float = NORTH_ARROW_SIZE_MM,
) -> str:
    """A north arrow whose paper bearing is computed, never assumed.

    ``paper_deg = (viewport.rotation_deg - model_bearing_deg) % 360``. With both zero
    the arrow points to the top of the sheet — the same intent as the legacy fixed
    glyph. The arrow rotates and the label counter-rotates about its own centre: a
    rotated "N" at 217° is unreadable, and a *non*-rotated arrow on a rotated view is
    simply a lie.
    """
    if not _is_number(size_mm) or size_mm <= 0:
        raise SheetError("north arrow size_mm must be a positive finite number")
    if not isinstance(label, str) or not label.strip():
        raise SheetError("sheet.north.label must be a non-empty string")
    paper_deg = _north_paper_deg(viewport.rotation_deg, model_bearing_deg)
    cx = frame.x_mm + NORTH_ARROW_INSET_MM + size_mm / 2.0
    cy = frame.y_mm + NORTH_ARROW_INSET_MM + size_mm / 2.0
    tip_y = cy - size_mm / 2.0
    base_y = cy + size_mm * 0.32
    inner_y = cy + size_mm * 0.05
    wing = size_mm * 0.23
    label_y = tip_y - 2.5
    return "\n".join(
        [
            f'<g class="north-arrow" transform="rotate({_fmt3(paper_deg)} '
            f'{_fmt3(cx)} {_fmt3(cy)})">',
            f'<text x="{_fmt3(cx)}" y="{_fmt3(label_y)}" '
            f'font-size="{_fmt3(NORTH_LABEL_FONT_MM)}" fill="{_INK}" '
            f'text-anchor="middle" font-family="monospace" '
            f'transform="rotate({_fmt3(-paper_deg)} {_fmt3(cx)} {_fmt3(label_y)})">'
            f"{_svg_escape(label)}</text>",
            f'<polygon points="{_fmt3(cx)},{_fmt3(tip_y)} {_fmt3(cx - wing)},'
            f'{_fmt3(base_y)} {_fmt3(cx)},{_fmt3(inner_y)} {_fmt3(cx + wing)},'
            f'{_fmt3(base_y)}" fill="{_INK}" stroke="{_INK}" '
            f'stroke-width="{_fmt3(LW_DEFAULT_MM)}"/>',
            f'<line x1="{_fmt3(cx)}" y1="{_fmt3(inner_y)}" x2="{_fmt3(cx)}" '
            f'y2="{_fmt3(cy + size_mm / 2.0)}" stroke="{_INK}" '
            f'stroke-width="{_fmt3(LW_DEFAULT_MM)}"/>',
            "</g>",
        ]
    )


def sheet_title_block_placeholder(frame: SheetFrame) -> str:
    """An **empty** group reserving the title-block rectangle for P9/#61.

    It draws nothing — a px-sized stub block in a mm sheet would be a wrong artifact,
    and a wrong artifact is worse than a missing one. What it does provide is a genuine
    element carrying the reservation geometry, so validation has something real to find
    (no test needs to splice a marker in by hand) and P9 can replace the group's
    *contents* without touching the reservation itself.
    """
    return (
        f'<g class="title-block" data-tdfa-placeholder="true" '
        f'data-tdfa-frame="x={_fmt3(frame.x_mm)};y={_fmt3(frame.y_mm)};'
        f'width={_fmt3(frame.width_mm)};height={_fmt3(frame.height_mm)}"></g>'
    )



#: Prefix of the data attributes this module writes on sheet furniture.
DATA_ATTR_PREFIX = "data-tdfa-"
#: Pre-release prefix for the same attributes. Read (with a DeprecationWarning) so
#: drawings rendered before the rename keep validating; never written.
LEGACY_DATA_ATTR_PREFIX = "data-sankofa-"


def read_sheet_data_attr(attrib: dict[str, str], name: str) -> str | None:
    """Return the value of sheet data attribute ``name`` (e.g. ``"bar"``), or None.

    Reads ``data-tdfa-<name>``. Falls back to the legacy ``data-sankofa-<name>``
    and emits a :class:`DeprecationWarning` when only the legacy spelling is present.
    """
    import warnings

    current = attrib.get(DATA_ATTR_PREFIX + name)
    if current is not None:
        return current
    legacy = attrib.get(LEGACY_DATA_ATTR_PREFIX + name)
    if legacy is not None:
        warnings.warn(
            f"SVG attribute {LEGACY_DATA_ATTR_PREFIX + name!r} is deprecated; re-render "
            f"to write {DATA_ATTR_PREFIX + name!r}. The legacy spelling is still read and "
            "will be removed in a future release.",
            DeprecationWarning,
            stacklevel=2,
        )
    return legacy


def parse_sheet_data_value(value: str) -> dict[str, str]:
    """Split a ``key=value;key=value`` sheet data attribute into a dict."""
    out: dict[str, str] = {}
    for part in value.split(";"):
        if "=" in part:
            key, _, val = part.partition("=")
            out[key.strip()] = val.strip()
    return out

# --------------------------------------------------------------------------- #
# the emitted sheet record
# --------------------------------------------------------------------------- #


def sheet_metadata_json(sheet: Sheet, *, drawn_extent_mm: list[float] | None = None) -> str:
    """Serialise the sheet record: sorted keys, no whitespace, no provenance.

    Byte-stable by construction — identical inputs give identical bytes — and carries no
    timestamp, path, hostname or version string, so it does not pre-empt P3/#55's
    manifest and provenance stamp.
    """
    return json.dumps(
        _round_floats(_sheet_metadata_payload(sheet, drawn_extent_mm=drawn_extent_mm)),
        sort_keys=True,
        separators=(",", ":"),
    )


def _sheet_metadata_payload(
    sheet: Sheet, *, drawn_extent_mm: list[float] | None = None
) -> dict[str, Any]:
    bar_length = _resolved_bar_length(sheet)
    north = None
    if sheet.north is not None:
        north = {
            "label": sheet.north.label,
            "model_bearing_deg": sheet.north.model_bearing_deg,
            "paper_deg": _north_paper_deg(
                sheet.viewport.rotation_deg, sheet.north.model_bearing_deg
            ),
        }
    viewport = sheet.viewport
    return {
        "schema": SHEET_METADATA_SCHEMA,
        "paper_units": "mm",
        "model_units": "m",
        "paper": {
            "name": sheet.paper.name,
            "orientation": sheet.paper.orientation,
            "width_mm": sheet.paper.width_mm,
            "height_mm": sheet.paper.height_mm,
        },
        "scale": {
            "denominator": viewport.scale.denominator,
            "text": viewport.scale.text,
            "mm_per_m": viewport.scale.mm_per_m(),
            "is_preferred": viewport.scale.is_preferred,
        },
        "scale_bar": {
            "length_m": bar_length,
            "divisions": sheet.scale_bar.divisions,
            "unit": sheet.scale_bar.unit,
            "total_mm": viewport.length_mm(bar_length),
        },
        "north": north,
        "viewport": {
            "extent_m": list(viewport.extent_m),
            "rotation_deg": viewport.rotation_deg,
            "frame_mm": [
                viewport.frame.x_mm,
                viewport.frame.y_mm,
                viewport.frame.width_mm,
                viewport.frame.height_mm,
            ],
            "required_mm": list(viewport.required_mm),
            "drawn_extent_mm": drawn_extent_mm,
        },
    }


def read_sheet_metadata(svg_text: str) -> dict | None:
    """Extract the sheet record from an SVG. ``None`` for a legacy (non-sheet) file."""
    match = re.search(
        rf'<metadata\b[^>]*\bid="{re.escape(SHEET_METADATA_ID)}"[^>]*>(.*?)</metadata>',
        svg_text,
        flags=re.DOTALL,
    )
    if match is None:
        return None
    try:
        payload = json.loads(unescape(match.group(1)))
    except json.JSONDecodeError as exc:
        raise SheetError(f"sheet metadata is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise SheetError("sheet metadata must be a JSON object")
    if payload.get("schema") != SHEET_METADATA_SCHEMA:
        raise SheetError(
            f"sheet metadata schema {payload.get('schema')!r} is not supported "
            f"(expected {SHEET_METADATA_SCHEMA!r})"
        )
    return payload


# --------------------------------------------------------------------------- #
# the assembler
# --------------------------------------------------------------------------- #


@dataclass
class SheetDrawing:
    """Paper-space sheet assembler. Mutable, mirroring ``svg.Drawing``.

    ``title_block`` must be ``None``: rendering a real ISO 7200 title block is P9/#61.
    When the sheet reserves a ``title-block`` region, an empty placeholder group is
    emitted at that rectangle (see :func:`sheet_title_block_placeholder`). When it does
    not, nothing is emitted and ``validate`` reports its existing missing-title-block
    problem — the gap stays visible rather than being papered over.
    """

    sheet: Sheet
    status: str | None = None
    title_block: dict | None = None
    background: str = "#ffffff"
    elements: list[str] = field(default_factory=list)
    paper_elements: list[str] = field(default_factory=list)
    defs: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._recorder = _ExtentRecorder()
        self.sheet = replace(
            self.sheet, viewport=replace(self.sheet.viewport, recorder=self._recorder)
        )

    @property
    def vp(self) -> Viewport:
        """The bound viewport — mirrors ``Drawing.vb``, named to keep the unit visible."""
        return self.sheet.viewport

    def add(self, *elements: str) -> "SheetDrawing":
        self.elements.extend(element for element in elements if element)
        return self

    def add_paper(self, *elements: str) -> "SheetDrawing":
        """Add elements in paper space, OUTSIDE the viewport clip.

        ``add()`` places content in the clipped viewport group, which is correct for
        anything that belongs to the drawing view: geometry that strays off-view is
        clipped, and the containment check reports it. But a sheet also carries
        furniture that legitimately lives *outside* the viewport — a legend, a
        schedule, notes, a title block — placed in the regions the sheet itself
        reserves via ``reserve:``. Passing those to ``add()`` silently clips them away.

        That failure mode is quiet in the worst way: the elements remain in the emitted
        DOM, so a checker that parses the SVG still finds them and reports the sheet
        clean, while the rendered PDF is missing its legend and title block entirely.
        Observed on the first real project sheet.

        Use ``add()`` for the view, ``add_paper()`` for the reserved regions.
        """

        self.paper_elements.extend(element for element in elements if element)
        return self

    def add_defs(self, *defs: str) -> "SheetDrawing":
        self.defs.extend(item for item in defs if item)
        return self

    def add_model(self, *points_and_elements: Any) -> "SheetDrawing":
        """Add SVG strings and/or record model ``(x, y)`` points (or sequences of them).

        Recording is what makes ``drawn_extent_mm`` — and therefore the containment
        warning — possible for geometry the caller mapped itself.
        """
        for item in points_and_elements:
            if isinstance(item, str):
                self.add(item)
            elif _is_point(item):
                self.vp.point(float(item[0]), float(item[1]))
            elif isinstance(item, (list, tuple)) and item and all(map(_is_point, item)):
                for point in item:
                    self.vp.point(float(point[0]), float(point[1]))
            else:
                raise SheetError(
                    "SheetDrawing.add_model expects SVG strings or model [x, y] points, "
                    f"got {item!r}"
                )
        return self

    def render(self) -> str:
        """Assemble the sheet in a fixed order (spec §3.7). Deterministic, no I/O."""
        if self.title_block is not None:
            raise SheetError(
                "title block rendering in paper space is issue #61; pass title_block=None"
            )
        from .svg import svg_status_watermark  # local: keeps the px/mm modules decoupled

        sheet = self.sheet
        width, height = sheet.paper.width_mm, sheet.paper.height_mm
        viewport = sheet.viewport
        frame = viewport.frame

        parts = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{_fmt_g(width)}mm" '
            f'height="{_fmt_g(height)}mm" viewBox="0 0 {_fmt_g(width)} {_fmt_g(height)}">',
            f'<metadata id="{SHEET_METADATA_ID}">'
            f"{sheet_metadata_json(sheet, drawn_extent_mm=self._recorder.bbox())}</metadata>",
            f'<rect width="{_fmt_g(width)}" height="{_fmt_g(height)}" '
            f'fill="{_svg_escape(self.background)}"/>',
        ]
        clip = (
            f'<clipPath id="tdfa-viewport"><rect x="{_fmt3(frame.x_mm)}" '
            f'y="{_fmt3(frame.y_mm)}" width="{_fmt3(frame.width_mm)}" '
            f'height="{_fmt3(frame.height_mm)}"/></clipPath>'
        )
        parts.append("<defs>" + "\n".join([*self.defs, clip]) + "</defs>")
        parts.append(sheet_frame(sheet))
        parts.append('<g class="viewport" clip-path="url(#tdfa-viewport)">')
        parts.extend(self.elements)
        parts.append("</g>")
        if self.paper_elements:
            # Unclipped, for the reserved regions. See `add_paper`.
            parts.append('<g class="paper">')
            parts.extend(self.paper_elements)
            parts.append("</g>")
        parts.append(
            sheet_scale_bar(
                viewport,
                frame,
                length_m=sheet.scale_bar.length_m,
                divisions=sheet.scale_bar.divisions,
                unit=sheet.scale_bar.unit,
            )
        )
        if sheet.north is not None:
            parts.append(
                sheet_north_arrow(
                    viewport,
                    frame,
                    model_bearing_deg=sheet.north.model_bearing_deg,
                    label=sheet.north.label,
                )
            )
        if self.status is not None:
            # svg_status_watermark is dimensionless in its own units, so passing
            # millimetres sizes the diagonal text correctly.
            parts.append(svg_status_watermark(width, height, self.status))
        title_region = sheet.regions.get(TITLE_BLOCK_REGION)
        if title_region is not None:
            parts.append(sheet_title_block_placeholder(title_region))
        parts.append("</svg>")
        return "\n".join(parts)

    def svg(self) -> str:
        return self.render()


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #


def _extent(raw: Any, ctx: str) -> tuple[float, float, float, float]:
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        raise SheetError(f"{ctx} must be [xmin, ymin, xmax, ymax] (four numbers)")
    for index, value in enumerate(raw):
        if not _is_number(value):
            raise SheetError(f"{ctx}[{index}] must be a finite number, got {value!r}")
    xmin, ymin, xmax, ymax = (float(value) for value in raw)
    if xmax <= xmin:
        raise SheetError(f"{ctx}: xmax ({xmax}) must be greater than xmin ({xmin})")
    if ymax <= ymin:
        raise SheetError(f"{ctx}: ymax ({ymax}) must be greater than ymin ({ymin})")
    return (xmin, ymin, xmax, ymax)


def _angle(raw: Any, ctx: str) -> float:
    if not _is_number(raw):
        raise SheetError(f"{ctx} must be a finite number of degrees, got {raw!r}")
    return float(raw) % 360.0


def _positive_number(raw: Any, ctx: str) -> float:
    if not _is_number(raw):
        raise SheetError(f"{ctx} must be a positive finite number, got {raw!r}")
    value = float(raw)
    if value <= 0:
        raise SheetError(f"{ctx} must be > 0, got {value:g}")
    return value


def _is_number(value: Any) -> bool:
    # Booleans first: ``isinstance(True, int)`` is True, and a YAML ``true`` where a
    # number belongs is a mistake, not a 1.
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


def _is_point(value: Any) -> bool:
    return (
        isinstance(value, (list, tuple))
        and len(value) == 2
        and _is_number(value[0])
        and _is_number(value[1])
    )


def _reject_unknown(raw: dict, expected: set[str], ctx: str) -> None:
    """A typo'd key is an error, never ignored.

    A silently dropped ``scale_denominator:`` would leave the sheet at a different scale
    than its author wrote — exactly the defect class this module closes.
    """
    for key in raw:
        if key not in expected:
            raise SheetError(
                f"{ctx}: unknown key {key!r} (expected: {', '.join(sorted(expected))})"
            )


def _fmt_measure(value: float) -> str:
    """Integral values as integers, else ``%g`` — matches the legacy bar's labels."""
    if math.isclose(value, round(value), abs_tol=1e-9):
        return str(int(round(value)))
    return f"{value:g}"


def _fmt3(value: float) -> str:
    """1 µm on paper, with ``-0.0`` normalised so output is stable across platforms."""
    if math.isclose(value, 0.0, abs_tol=0.0005):
        value = 0.0
    return f"{value:.3f}"


def _fmt_g(value: float) -> str:
    if math.isclose(value, 0.0, abs_tol=1e-12):
        value = 0.0
    return f"{value:g}"


def _svg_escape(value: Any) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _round_floats(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, list):
        return [_round_floats(item) for item in value]
    if isinstance(value, dict):
        return {key: _round_floats(item) for key, item in value.items()}
    return value


__all__ = [
    "FIT_TOL_MM",
    "ISO_PREFERRED_DENOMINATORS",
    "LW_DEFAULT_MM",
    "PAPER_SIZES_MM",
    "PREFERRED_DENOMINATORS",
    "SCALE_BAR_INSET_MM",
    "SHEET_METADATA_ID",
    "SHEET_METADATA_SCHEMA",
    "TITLE_BLOCK_REGION",
    "Margins",
    "NorthRef",
    "PaperSize",
    "PlotScale",
    "Reservation",
    "ScaleBarSpec",
    "Sheet",
    "SheetDrawing",
    "SheetError",
    "SheetFrame",
    "Viewport",
    "DATA_ATTR_PREFIX",
    "LEGACY_DATA_ATTR_PREFIX",
    "load_sheet_config",
    "parse_sheet_data_value",
    "read_sheet_data_attr",
    "read_sheet_metadata",
    "resolve_sheet",
    "scale_bar_tick_label",
    "sheet_frame",
    "sheet_metadata_json",
    "sheet_north_arrow",
    "sheet_scale_bar",
    "sheet_title_block_placeholder",
]
