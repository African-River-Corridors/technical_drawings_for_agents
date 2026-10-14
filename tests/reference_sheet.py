"""A frozen, independent reference for the rules of the Drawing Format Standard.

This is the sheet and scale code of the tool before the clean break (version 0.1.0), kept in the tests as
a second implementation. The conformance tests compare the standard's worked numbers and the cases with
it. It is not part of the package. Do not change it to make a test pass.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any


class SheetError(ValueError):
    """Raised when a sheet config, plot scale or viewport binding is malformed."""


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


#: The strict 1/2/5-decade set. :attr:`PlotScale.is_preferred` reports membership of
#: *this* set, and drives the "not an ISO 5455 preferred ratio" warning. Two concepts,
#: two names, no fudge: 1:1250 is a legitimate `fit` target and a non-preferred ratio.
ISO_PREFERRED_DENOMINATORS: tuple[int, ...] = (
    1, 2, 5, 10, 20, 50, 100, 200, 500,
    1000, 2000, 5000, 10000, 20000, 50000, 100000,
)


#: Absorbs IEEE-754 representation of exact millimetre arithmetic and nothing more. A
#: tolerance that hides a 1 mm overflow is a tolerance that hides a mistake.
FIT_TOL_MM = 1e-6


#: House furniture dimensions (paper mm). Stated so two implementations agree; not
#: sourced from a drafting standard (spec §8.4).
SCALE_BAR_INSET_MM = 6.0


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


def scale_bar_tick_label(length_m: float, divisions: int, index: int, unit: str) -> str:
    """The derived label for tick ``index``. Shared with validation so V7 cannot drift."""
    value = float(length_m) * index / divisions
    if unit == "km":
        value = value / 1000.0
    suffix = f" {unit}" if index == divisions else ""
    return f"{_fmt_measure(value)}{suffix}"


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


def _fmt_measure(value: float) -> str:
    """Integral values as integers, else ``%g`` — matches the legacy bar's labels."""
    if math.isclose(value, round(value), abs_tol=1e-9):
        return str(int(round(value)))
    return f"{value:g}"
