"""A ``PaperFrame`` stand-in, so P9's tests do not depend on a sheet generator.

P9 consumes the 5-member :class:`~technical_drawings_for_agents.titleblock.PaperFrame` protocol
read-only. Testing against a ``FakeFrame`` proves that: if a test needed anything
the protocol does not declare, it would not compile here. The real
:class:`technical_drawings_for_agents.sheet.Sheet` is exercised through
``DrawingMeta._paper_frame`` in ``test_p9_backward_compat.py``.

``device_per_mm`` is deliberately *not* 1.0 for every size, so the "physically
identical text at every sheet size" test compares real millimetres rather than
two identity mappings.
"""

from __future__ import annotations

from dataclasses import dataclass

#: ISO 216 A-series trim sizes as ``(short_edge_mm, long_edge_mm)``.
PAPER = {
    "A0": (841.0, 1189.0),
    "A1": (594.0, 841.0),
    "A2": (420.0, 594.0),
    "A3": (297.0, 420.0),
    "A4": (210.0, 297.0),
}

#: The sizes and orientations every title-block geometry test runs over.
SIZES = ("A3", "A2", "A1", "A0")
ORIENTATIONS = ("landscape", "portrait")


@dataclass(frozen=True)
class FakeFrame:
    """Implements ``PaperFrame`` and nothing else."""

    size: str
    orientation: str = "landscape"
    margin_mm: float = 10.0
    scale: float = 1.0

    @property
    def width_mm(self) -> float:
        short, long = PAPER[self.size]
        return long if self.orientation == "landscape" else short

    @property
    def height_mm(self) -> float:
        short, long = PAPER[self.size]
        return short if self.orientation == "landscape" else long

    def frame_mm(self) -> tuple[float, float, float, float]:
        return (
            self.margin_mm,
            self.margin_mm,
            self.width_mm - 2 * self.margin_mm,
            self.height_mm - 2 * self.margin_mm,
        )

    def to_device(self, x_mm: float, y_mm: float) -> tuple[float, float]:
        return (x_mm * self.scale, y_mm * self.scale)

    def device_per_mm(self) -> float:
        return self.scale


@dataclass(frozen=True)
class NarrowFrame:
    """A frame too narrow for the 180 mm layout, to prove it raises rather than clamps."""

    size: str = "A4"
    orientation: str = "portrait"
    width: float = 150.0

    def frame_mm(self) -> tuple[float, float, float, float]:
        return (10.0, 10.0, self.width, 270.0)

    def to_device(self, x_mm: float, y_mm: float) -> tuple[float, float]:
        return (x_mm, y_mm)

    def device_per_mm(self) -> float:
        return 1.0
