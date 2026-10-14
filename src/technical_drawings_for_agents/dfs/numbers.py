"""Numbers: rounding, exact trigonometry, fit tolerance (clause 5)."""

from __future__ import annotations

import math
from decimal import ROUND_HALF_EVEN, Decimal

FIT_TOL_MM = 1e-6  # 5.7


def round_half_even(value: float, places: int) -> float:
    """5.5: round the exact binary value half-even; a negative zero becomes 0."""
    result = float(Decimal(value).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_EVEN))
    return 0.0 if result == 0 else result


def mm(value: float) -> float:
    """Paper length, 3 places (5.4)."""
    return round_half_even(value, 3)


def angle(value: float) -> float:
    """Angle on paper in [0, 360), 3 places; a rounded 360 is written 0 (16.4)."""
    result = round_half_even(value % 360.0, 3)
    return 0.0 if result >= 360.0 else result


def cos_sin(degrees: float) -> tuple[float, float]:
    """Cosine and sine, exact at multiples of 90 degrees (5.6.1)."""
    d = degrees % 360.0
    exact = {0.0: (1.0, 0.0), 90.0: (0.0, 1.0), 180.0: (-1.0, 0.0), 270.0: (0.0, -1.0)}
    if d in exact:
        return exact[d]
    r = math.radians(d)
    return math.cos(r), math.sin(r)
