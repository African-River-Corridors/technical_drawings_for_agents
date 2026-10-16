"""The pure rules of the standard: sheet, scale, scale bar, north, widths, text.

Each function names the clause it follows. They hold no state and read only the data files.
"""

from __future__ import annotations

import math
from decimal import ROUND_HALF_EVEN, Decimal

from . import resources
from .numbers import FIT_TOL_MM, cos_sin, mm, round_half_even

# 6.1, 6.3, 6.5 ------------------------------------------------------------------------------
SIZES_MM = {"A0": (841, 1189), "A1": (594, 841), "A2": (420, 594), "A3": (297, 420), "A4": (210, 297)}
INSETS_MM = {  # left, top, right, bottom
    "A0": (20, 20, 20, 20),
    "A1": (20, 20, 20, 20),
    "A2": (20, 10, 10, 10),
    "A3": (20, 10, 10, 10),
    "A4": (20, 10, 10, 10),
}
ZONES = {"A0": (24, 16), "A1": (16, 12), "A2": (12, 8), "A3": (8, 6), "A4": (6, 4)}  # long, short
ZONE_LETTERS = "ABCDEFGHJKLMNPQR"

# 7.3 ------------------------------------------------------------------------------------------
ISO_RATIOS = (1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000, 50000, 100000)
HOUSE_RATIOS = (25, 250, 1250, 2500)
ALL_RATIOS = tuple(sorted((*ISO_RATIOS, *HOUSE_RATIOS)))
MAP_RATIOS = (200000, 500000, 1000000, 2000000, 5000000, 10000000)  # 7.3.5, from version 1.1
ALL_RATIOS_1_1 = tuple(sorted((*ALL_RATIOS, *MAP_RATIOS)))


def ratios(minor: int) -> tuple[int, ...]:
    """7.3.5 and 18.2.1: the ratios a document of version 1.<minor> may use."""
    return ALL_RATIOS_1_1 if minor >= 1 else ALL_RATIOS

# 7.9, 7.10 ------------------------------------------------------------------------------------
BAR_INSET, BAR_HEIGHT, TICK_LENGTH = 6.0, 3.0, 1.5
BAR_LABEL_BASELINE, BAR_RATIO_BASELINE, BAR_TEXT_HEIGHT = 5.0, 9.0, 2.5
NORTH_SIZE, NORTH_INSET, NORTH_LABEL_HEIGHT = 18.0, 10.0, 3.5
MIN_BAR_FRAME_HEIGHT, MIN_NORTH_FRAME = 46.0, 38.0

CAP_HEIGHT_UNITS = 700  # 11.7.1


def drawing_space(size: str, orientation: str) -> tuple[float, float, tuple[float, float, float, float]]:
    """6.3: sheet width, height and the drawing space ``(x, y, width, height)``."""
    short, long = SIZES_MM[size]
    width, height = (long, short) if orientation == "landscape" else (short, long)
    left, top, right, bottom = INSETS_MM[size]
    return float(width), float(height), (float(left), float(top), float(width - left - right), float(height - top - bottom))


def line_group(size: str) -> tuple[str, dict]:
    """8.2: the group name and widths for a sheet size."""
    for name, group in resources.line_styles()["line_groups"].items():
        if size in group["sizes"]:
            return name, group
    raise KeyError(size)


# 7.4, 7.5 -------------------------------------------------------------------------------------
class Mapper:
    """The map from model metres to paper millimetres (7.4)."""

    def __init__(self, frame, window, denominator, rotation_deg):
        x, y, w, h = frame
        self.cx = (window["xmin_m"] + window["xmax_m"]) / 2
        self.cy = (window["ymin_m"] + window["ymax_m"]) / 2
        self.fx, self.fy = x + w / 2, y + h / 2
        self.k = 1000 / denominator
        self.cos, self.sin = cos_sin(rotation_deg)

    def point(self, p) -> tuple[float, float]:
        dx, dy = p[0] - self.cx, p[1] - self.cy
        rx, ry = dx * self.cos - dy * self.sin, dx * self.sin + dy * self.cos
        return (self.fx + rx * self.k, self.fy - ry * self.k)


def required_size(window, denominator, rotation_deg) -> tuple[float, float]:
    """7.5: paper size of the bounding box of the turned window."""
    c, s = cos_sin(rotation_deg)
    wm = window["xmax_m"] - window["xmin_m"]
    hm = window["ymax_m"] - window["ymin_m"]
    k = 1000 / denominator
    return (abs(wm * c) + abs(hm * s)) * k, (abs(wm * s) + abs(hm * c)) * k


def window_fits(frame, window, denominator, rotation_deg) -> bool:
    rw, rh = required_size(window, denominator, rotation_deg)
    return rw <= frame[2] + FIT_TOL_MM and rh <= frame[3] + FIT_TOL_MM


def fit_scale(frame, window, rotation_deg, ratio_list=ALL_RATIOS):
    """7.5.2: the first ratio that fits, or None."""
    for n in ratio_list:
        if window_fits(frame, window, n, rotation_deg):
            return n
    return None


# 7.9 ------------------------------------------------------------------------------------------
def bar_available(frame_width: float) -> float:
    return frame_width - 2 * BAR_INSET


def derived_bar_length(frame_width: float, denominator: int):
    """7.9.4: the largest {1,2,5} x 10^n metres that is at most a quarter frame and fits; or None."""
    k = 1000 / denominator
    target = (frame_width / 4) / k
    best = None
    for exponent in range(-3, 7):
        for multiple in (1, 2, 5):
            value = multiple * 10.0**exponent
            if value <= target + 1e-12 and value * k <= bar_available(frame_width) + FIT_TOL_MM:
                best = value if best is None else max(best, value)
    return best


def bar_labels(length_m: float, divisions: int, unit: str) -> list[str]:
    """7.9.7."""
    labels = []
    for i in range(divisions + 1):
        value = length_m * i / divisions
        if unit == "km":
            value = value / 1000
        rounded = round_half_even(value, 6)
        text = str(int(rounded)) if rounded == int(rounded) else f"{rounded:.6f}".rstrip("0").rstrip(".")
        labels.append(text + (f" {unit}" if i == divisions else ""))
    return labels


def north_deg(rotation_deg: float, bearing_deg: float) -> float:
    """7.10.2."""
    return (-(rotation_deg % 360.0) - bearing_deg) % 360.0


# 8 --------------------------------------------------------------------------------------------
def snap_width(width_mm: float):
    """8.3.2: nearest CAD width, ties to the thinner; None when out of range."""
    um = int(Decimal(width_mm).quantize(Decimal("0.001"), rounding=ROUND_HALF_EVEN) * 1000)
    if um == 0 or um > 2110:
        return None
    members = resources.line_styles()["cad_line_widths_um"]
    best = min(members, key=lambda m: (abs(m - um), m))
    return best / 1000


def line_pattern(line_type: str, width_mm: float) -> list[float]:
    """8.4.1: the dashes and gaps, in mm."""
    return [
        mm(float(Decimal(str(m)) * Decimal(str(width_mm))))
        for m in resources.line_styles()["line_types"][line_type]["pattern_d"]
    ]


# 9.5 ------------------------------------------------------------------------------------------
def arc_sweep(start_deg: float, end_deg: float):
    """9.5: counter-clockwise sweep in [0, 360); 0 means error 9.5.1 (returned as None)."""
    sweep = (end_deg - start_deg) % 360.0
    return None if sweep == 0 else sweep


# 11.7 -----------------------------------------------------------------------------------------
def text_width(text: str, height_mm: float) -> float:
    """11.7.1."""
    advance = resources.font_widths()["advance"]
    return mm(sum(advance[c] for c in text) * height_mm / CAP_HEIGHT_UNITS)


def text_width_exact(text: str, height_mm: float) -> float:
    advance = resources.font_widths()["advance"]
    return sum(advance[c] for c in text) * height_mm / CAP_HEIGHT_UNITS


def text_box(anchor, text, height_mm, rotation_deg, align_h, align_v) -> list[list[float]]:
    """11.7.2 and 11.7.3: the four corners on the paper, not rounded."""
    ax, ay = anchor
    w = text_width_exact(text, height_mm)
    left = {"left": ax, "centre": ax - w / 2, "right": ax - w}[align_h]
    base = {"baseline": ay, "middle": ay + height_mm / 2, "top": ay + height_mm}[align_v]
    c, s = cos_sin(rotation_deg)
    out = []
    for x, y in [(left, base), (left + w, base), (left + w, base - height_mm), (left, base - height_mm)]:
        dx, dy = x - ax, y - ay
        out.append([ax + dx * c + dy * s, ay - dx * s + dy * c])
    return out


def rounded_box(anchor, text, height_mm, rotation_deg, align_h, align_v):
    return [[mm(x), mm(y)] for x, y in text_box(anchor, text, height_mm, rotation_deg, align_h, align_v)]


def hypot(a, b) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])
