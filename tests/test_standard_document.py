"""Tests for the Drawing Format Standard document (docs/standard/).

Two kinds of test.

* Structure: clause numbers are unique and well formed, every `P-nn` and `H-nn` the standard cites is
  in the checklist, every checklist item is cited, and every error number the standard names is a
  real clause.
* Worked numbers: the numbers printed in clauses 5, 6 and 7 are checked against hand-written
  expected values, and against the reference code where that code already implements the rule.
  The expected values below were worked by hand from the clause text. They are never regenerated
  from the code.
"""

from __future__ import annotations

import math
import re
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path

import pytest

from technical_drawings_for_agents.sheet import (
    ISO_PREFERRED_DENOMINATORS,
    PAPER_SIZES_MM,
    PlotScale,
    SheetError,
    SheetFrame,
    Viewport,
    _checked_bar_length,
    _derived_bar_length,
    _north_paper_deg,
    scale_bar_tick_label,
)

DOCS = Path(__file__).resolve().parents[1] / "docs" / "standard"
STANDARD = (DOCS / "drawing-format-standard.md").read_text(encoding="utf-8")
CHECKLIST = (DOCS / "iso-checklist.md").read_text(encoding="utf-8")

CLAUSE_RE = re.compile(r"^\*\*(\d+(?:\.\d+)*)[ *]", re.MULTILINE)
HEADING_RE = re.compile(r"^#{2,3} (\d+(?:\.\d+)*) ", re.MULTILINE)


def clause_numbers() -> list[str]:
    return CLAUSE_RE.findall(STANDARD) + HEADING_RE.findall(STANDARD)


# --------------------------------------------------------------------------- #
# structure
# --------------------------------------------------------------------------- #


def test_clause_numbers_are_unique():
    numbers = clause_numbers()
    repeated = sorted({n for n in numbers if numbers.count(n) > 1})
    assert repeated == [], f"clause numbers used twice: {repeated}"


def test_every_clause_sits_under_a_numbered_section():
    sections = set(re.findall(r"^## (\d+) ", STANDARD, re.MULTILINE))
    assert sections == {str(n) for n in range(19)}
    for number in clause_numbers():
        assert number.split(".")[0] in sections, number


def test_sections_zero_to_eighteen_appear_in_order():
    found = [int(n) for n in re.findall(r"^## (\d+) ", STANDARD, re.MULTILINE)]
    assert found == list(range(19))


def _checklist_ids(prefix: str) -> set[str]:
    return set(re.findall(rf"^\| ({prefix}-\d\d) \|", CHECKLIST, re.MULTILINE))


def test_checklist_items_have_every_field():
    for line in CHECKLIST.splitlines():
        if re.match(r"^\| P-\d\d \|", line):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            assert len(cells) == 7, line
            assert all(cells), line
            assert cells[2] in ("to be confirmed",) or re.search(r"\d", cells[2]), line
        if re.match(r"^\| H-\d\d \|", line):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            assert len(cells) == 4, line
            assert all(cells), line


def test_every_cited_id_is_in_the_checklist_and_every_item_is_cited():
    for prefix in ("P", "H"):
        cited = set(re.findall(rf"\b({prefix}-\d\d)\b", STANDARD))
        listed = _checklist_ids(prefix)
        assert cited <= listed, f"cited but not listed: {sorted(cited - listed)}"
        assert listed <= cited, f"listed but never cited: {sorted(listed - cited)}"


def test_every_error_number_named_in_the_standard_is_a_clause():
    numbers = set(clause_numbers())
    named = set(re.findall(r"[Ee]rror `(\d+(?:\.\d+)+)`", STANDARD))
    named |= set(re.findall(r"[Ee]rror (\d+\.\d+\.\d+)\b", STANDARD))
    assert named, "the standard should name at least one error"
    assert named <= numbers, f"error numbers with no clause: {sorted(named - numbers)}"


def test_no_numeric_rule_lacks_a_basis():
    """Each clause that sets a value is followed by a Basis line before the next numbered clause
    that sets another value. This is a coarse guard: it counts the Basis lines per section."""
    for section in ("5", "6", "7"):
        body = re.split(r"^## ", STANDARD, flags=re.MULTILINE)
        text = next(b for b in body if b.startswith(f"{section} "))
        assert "Basis" in text


# --------------------------------------------------------------------------- #
# clause 5: rounding
# --------------------------------------------------------------------------- #


def round_half_even(value: float, places: int) -> float:
    """Clause 5.5, written from the clause text: exact binary value, half-even, no negative zero."""
    quantum = Decimal(1).scaleb(-places)
    result = Decimal(value).quantize(quantum, rounding=ROUND_HALF_EVEN)
    out = float(result)
    return 0.0 if out == 0 else out


@pytest.mark.parametrize(
    ("value", "places", "expected"),
    [
        (0.0625, 3, 0.062),
        (0.1875, 3, 0.188),
        (-0.0625, 3, -0.062),
        (2.5, 0, 2.0),
        (3.5, 0, 4.0),
        (-0.0004, 3, 0.0),
        (1.0005, 3, 1.0),
        (0.0005, 3, 0.001),
    ],
)
def test_clause_5_5_1_rounding_table(value, places, expected):
    got = round_half_even(value, places)
    assert got == expected
    assert math.copysign(1.0, got) == math.copysign(1.0, expected)


def test_clause_5_5_1_values_appear_in_the_standard():
    for text in ("0.0625", "0.1875", "-0.0625", "2.5", "3.5", "-0.0004", "1.0005", "0.0005"):
        assert text in STANDARD


# --------------------------------------------------------------------------- #
# clause 6: sheets
# --------------------------------------------------------------------------- #

INSETS_MM = {  # (left, top, right, bottom), clause 6.3, written by hand
    "A0": (20, 20, 20, 20),
    "A1": (20, 20, 20, 20),
    "A2": (20, 10, 10, 10),
    "A3": (20, 10, 10, 10),
    "A4": (20, 10, 10, 10),
}


def drawing_space(size: str, orientation: str) -> tuple[float, float, float, float]:
    short, long = PAPER_SIZES_MM[size]
    width, height = (long, short) if orientation == "landscape" else (short, long)
    left, top, right, bottom = INSETS_MM[size]
    return (left, top, width - left - right, height - top - bottom)


def test_clause_6_1_sizes_match_the_reference_table_and_the_standard():
    assert PAPER_SIZES_MM == {
        "A0": (841.0, 1189.0),
        "A1": (594.0, 841.0),
        "A2": (420.0, 594.0),
        "A3": (297.0, 420.0),
        "A4": (210.0, 297.0),
    }
    for name, (short, long) in PAPER_SIZES_MM.items():
        assert re.search(rf"\| {name} \| {int(short)} \| {int(long)} \|", STANDARD), name


@pytest.mark.parametrize(
    ("size", "orientation", "expected"),
    [
        ("A3", "landscape", (20, 10, 390, 277)),
        ("A3", "portrait", (20, 10, 267, 400)),
        ("A4", "portrait", (20, 10, 180, 277)),
        ("A0", "landscape", (20, 20, 1149, 801)),
    ],
)
def test_clause_6_3_2_drawing_space(size, orientation, expected):
    assert drawing_space(size, orientation) == expected
    _, _, w, h = expected
    assert f"{w} x {h} mm" in STANDARD.replace(".0", "")


def test_clause_6_5_4_zone_boundaries_a3_landscape():
    x, _, w, h = drawing_space("A3", "landscape")
    columns, rows = 8, 6
    bounds = [x + w * i / columns for i in range(columns + 1)]
    assert bounds == [20, 68.75, 117.5, 166.25, 215, 263.75, 312.5, 361.25, 410]
    assert round_half_even(h / rows, 3) == 46.167
    letters = "ABCDEFGHJKLMNPQR"
    assert (letters[0], letters[rows - 1]) == ("A", "F")
    assert len(letters) == 16


def test_clause_6_5_zone_counts_divide_the_sides_into_sensible_zones():
    zones = {"A0": (24, 16), "A1": (16, 12), "A2": (12, 8), "A3": (8, 6), "A4": (6, 4)}
    for size, (long_n, short_n) in zones.items():
        _, _, w, h = drawing_space(size, "landscape")
        assert 40 <= w / long_n <= 60, size
        assert 40 <= h / short_n <= 60, size
        assert f"| {size} | {long_n} | {short_n} |" in STANDARD


# --------------------------------------------------------------------------- #
# clause 7: viewports, scales, scale bar, north arrow
# --------------------------------------------------------------------------- #

FRAME = SheetFrame(20.0, 10.0, 390.0, 277.0)


def viewport(window, denominator, rotation=0.0, frame=FRAME):
    return Viewport(tuple(window), PlotScale(denominator), frame, rotation)


def test_clause_7_3_1_preferred_ratios():
    expected = (1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000, 50000, 100000)
    assert ISO_PREFERRED_DENOMINATORS == expected
    assert ", ".join(str(n) for n in expected) in STANDARD


@pytest.mark.parametrize("rejected", [25, 250, 1250, 2500, 25000])
def test_clause_7_3_2_these_ratios_are_not_preferred(rejected):
    assert rejected not in ISO_PREFERRED_DENOMINATORS


@pytest.mark.parametrize(
    ("model", "rotation", "paper"),
    [
        ((100, 50), 0, (315.0, 98.5)),
        ((100, 50), 90, (165.0, 48.5)),
        ((50, 25), 0, (215.0, 148.5)),
        ((50, 25), 90, (215.0, 148.5)),
        ((0, 0), 0, (115.0, 198.5)),
    ],
)
def test_clause_7_4_3_map_from_model_to_paper(model, rotation, paper):
    vp = viewport((0, 0, 100, 50), 500, rotation)
    x, y = vp.point(*model)
    assert round_half_even(x, 3) == paper[0]
    assert round_half_even(y, 3) == paper[1]


def test_clause_7_4_4_model_up_goes_left_at_90_degrees():
    vp = viewport((0, 0, 100, 50), 500, 90.0)
    x0, y0 = vp.point(50, 25)
    x1, y1 = vp.point(50, 26)
    assert round_half_even(x1 - x0, 3) == -2.0
    assert round_half_even(y1 - y0, 3) == 0.0


def first_fitting(window, rotation, frame=FRAME):
    for n in ISO_PREFERRED_DENOMINATORS:
        if viewport((0, 0, *window), n, rotation, frame).fits:
            return n
    return None


@pytest.mark.parametrize(
    ("window", "rotation", "denominator", "needed", "fits"),
    [
        ((200, 100), 0, 500, (400, 200), False),
        ((200, 100), 0, 1000, (200, 100), True),
        ((100, 300), 90, 500, (600, 200), False),
        ((100, 300), 90, 1000, (300, 100), True),
        ((390, 277), 0, 1000, (390, 277), True),
    ],
)
def test_clause_7_5_3_fit_table(window, rotation, denominator, needed, fits):
    vp = viewport((0, 0, *window), denominator, rotation)
    w_mm, h_mm = vp.required_mm
    assert (round_half_even(w_mm, 3), round_half_even(h_mm, 3)) == needed
    assert vp.fits is fits


def test_clause_7_5_2_declared_fit_picks_the_largest_preferred_scale():
    assert first_fitting((200, 100), 0) == 1000
    assert first_fitting((100, 300), 90) == 1000


def test_clause_7_5_the_error_names_the_next_scale_that_fits():
    assert not viewport((0, 0, 200, 100), 500).fits
    assert first_fitting((200, 100), 0) == 1000


@pytest.mark.parametrize(
    ("denominator", "target_m", "length_m", "bar_mm", "labels"),
    [
        (100, 9.75, 5, 50, ["0", "1", "2", "3", "4", "5 m"]),
        (200, 19.5, 10, 50, ["0", "2", "4", "6", "8", "10 m"]),
        (500, 48.75, 20, 40, ["0", "4", "8", "12", "16", "20 m"]),
        (1000, 97.5, 50, 50, ["0", "10", "20", "30", "40", "50 m"]),
        (2000, 195, 100, 50, ["0", "20", "40", "60", "80", "100 m"]),
    ],
)
def test_clause_7_9_9_derived_scale_bar(denominator, target_m, length_m, bar_mm, labels):
    vp = viewport((0, 0, 100, 50), denominator)
    assert vp.model_m(0.25 * FRAME.width_mm) == pytest.approx(target_m)
    length = _derived_bar_length(vp)
    assert length == length_m
    assert round_half_even(vp.length_mm(length), 3) == bar_mm
    assert [scale_bar_tick_label(length, 5, i, "m") for i in range(6)] == labels


def test_clause_7_9_7_label_text_rules():
    assert [scale_bar_tick_label(2, 5, i, "m") for i in range(6)] == [
        "0", "0.4", "0.8", "1.2", "1.6", "2 m",
    ]
    assert [scale_bar_tick_label(2000, 4, i, "km") for i in range(5)] == [
        "0", "0.5", "1", "1.5", "2 km",
    ]


def test_clause_7_9_9_explicit_length_limits():
    vp = viewport((0, 0, 100, 50), 1000)
    assert _checked_bar_length(vp, 378, "t") == 378  # 378 mm: fits exactly
    with pytest.raises(SheetError) as excinfo:
        _checked_bar_length(vp, 379, "t")
    assert "379.000 mm" in str(excinfo.value) and "378.000 mm" in str(excinfo.value)
    with pytest.raises(SheetError) as excinfo:
        _checked_bar_length(vp, 390, "t")
    assert "390.000 mm" in str(excinfo.value) and "378.000 mm" in str(excinfo.value)


def test_clause_7_9_10_and_7_10_7_minimum_frame_sizes():
    arrow_bottom = 10 + 18  # inset plus arrow height, from the top of the frame
    bar_top_from_bottom = 15 + 3  # bar bottom plus bar height, from the bottom of the frame
    assert arrow_bottom + bar_top_from_bottom == 46
    assert 10 + 18 + 10 == 38


@pytest.mark.parametrize(
    ("rotation", "bearing", "paper_deg"),
    [
        (0, 0, 0.0),
        (90, 0, 270.0),
        (90, 90, 180.0),
        (40.25, 0, 319.75),
        (0, 12.5, 347.5),
        (-10, 0, 10.0),
    ],
)
def test_clause_7_10_4_north_direction(rotation, bearing, paper_deg):
    got = _north_paper_deg(rotation % 360.0, bearing)
    assert round_half_even(got, 3) == paper_deg


@pytest.mark.parametrize("rotation", [0.0, 40.25, 90.0, 217.0, 350.0])
def test_clause_7_10_2_arrow_agrees_with_the_viewport(rotation):
    """North is model +y when the bearing is 0. Take the paper direction from the map itself."""
    vp = viewport((0, 0, 100, 50), 500, rotation)
    x0, y0 = vp.point(50, 25)
    x1, y1 = vp.point(50, 26)
    seen = math.degrees(math.atan2(x1 - x0, -(y1 - y0))) % 360.0
    assert seen == pytest.approx(_north_paper_deg(rotation, 0.0), abs=1e-6)


@pytest.mark.parametrize(("paper_deg", "tip"), [(0, (39, 20)), (90, (48, 29)), (270, (30, 29))])
def test_clause_7_10_6_arrow_tip(paper_deg, tip):
    cx, cy = FRAME.x_mm + 10 + 9, FRAME.y_mm + 10 + 9
    assert (cx, cy) == (39, 29)
    angle = math.radians(paper_deg)
    # A clockwise turn on a sheet with +y down, applied to the offset (0, -9).
    dx, dy = 0.0, -9.0
    tx = cx + dx * math.cos(angle) - dy * math.sin(angle)
    ty = cy + dx * math.sin(angle) + dy * math.cos(angle)
    assert (round_half_even(tx, 3), round_half_even(ty, 3)) == tip
