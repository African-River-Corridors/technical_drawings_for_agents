"""Clause 11 (lettering): the parts that do not need a font. Expected values are by hand."""

from __future__ import annotations

import re
from pathlib import Path

STANDARD = (
    Path(__file__).resolve().parents[1] / "docs" / "standard" / "drawing-format-standard.md"
).read_text(encoding="utf-8")

# The ISO 3098 nominal heights, by hand. Each step is about the square root of 2.
HEIGHTS_MM = (2.5, 3.5, 5, 7, 10, 14, 20)


def clause_11() -> str:
    parts = re.split(r"^## ", STANDARD, flags=re.MULTILINE)
    return next(p for p in parts if p.startswith("11 Lettering"))


def test_clause_11_2_lists_the_height_series():
    text = clause_11()
    line = re.search(r"^> ([\d., ]+)$", text, re.MULTILINE).group(1)
    assert tuple(float(x) for x in line.split(",")) == tuple(float(h) for h in HEIGHTS_MM)


def test_each_height_is_about_root_two_times_the_one_before():
    for low, high in zip(HEIGHTS_MM, HEIGHTS_MM[1:]):
        assert 1.3 < high / low < 1.5


def test_the_furniture_text_heights_are_in_the_series():
    assert 2.5 in HEIGHTS_MM  # scale bar text, clause 7.9.1
    assert 3.5 in HEIGHTS_MM  # zone labels and the north label, clauses 6.5.3 and 7.10.5


def test_the_font_is_not_adopted_and_the_text_says_so():
    assert "No font is adopted yet" in clause_11()
