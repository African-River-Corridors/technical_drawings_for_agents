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




# --------------------------------------------------------------------------- #
# the font (clauses 11.6 and 11.7)
# --------------------------------------------------------------------------- #

import hashlib
import json

import pytest

ROOT = Path(__file__).resolve().parents[1]
FONT_DIR = ROOT / "src" / "dfs" / "data" / "fonts"
FONT = FONT_DIR / "BarlowSemiCondensed-Regular.ttf"
WIDTHS = json.loads((ROOT / "docs" / "standard" / "data" / "font-widths.json").read_text(encoding="utf-8"))

CHARSET = [chr(i) for i in range(0x20, 0x7F)] + list("°±×µ²³·–Ø")


def test_the_font_file_its_licence_and_its_source_note_ship_together():
    assert FONT.is_file()
    assert (FONT_DIR / "OFL.txt").is_file()
    assert (FONT_DIR / "SOURCE.txt").is_file()
    assert "Barlow Semi Condensed" in (ROOT / "NOTICE").read_text(encoding="utf-8")


def test_the_sha256_in_the_standard_the_widths_file_and_the_source_note_match_the_font():
    digest = hashlib.sha256(FONT.read_bytes()).hexdigest()
    assert digest in STANDARD
    assert WIDTHS["sha256"] == digest
    assert digest in (FONT_DIR / "SOURCE.txt").read_text(encoding="utf-8")


def test_the_licence_is_ofl_1_1_with_no_reserved_font_name():
    text = (FONT_DIR / "OFL.txt").read_text(encoding="utf-8")
    assert "SIL OPEN FONT LICENSE Version 1.1" in text
    header = text.split("SIL OPEN FONT LICENSE Version 1.1")[0]
    assert "Reserved Font Name" not in header
    assert "embedded" in text  # permission 1 of the licence is to use, copy and embed


def test_the_character_set_in_the_standard_and_the_widths_file_agree():
    assert list(WIDTHS["advance"]) == CHARSET
    for code in ("U+00B0", "U+00B1", "U+00D7", "U+00B5", "U+00B2", "U+00B3", "U+00B7", "U+2013", "U+00D8"):
        assert code in STANDARD


def test_the_widths_file_equals_the_font_and_the_font_allows_embedding():
    ttlib = pytest.importorskip("fontTools.ttLib")
    pens = pytest.importorskip("fontTools.pens.boundsPen")
    font = ttlib.TTFont(FONT)
    cmap, hmtx = font.getBestCmap(), font["hmtx"]
    assert font["head"].unitsPerEm == WIDTHS["units_per_em"] == 1000
    assert font["OS/2"].fsType == 0
    for char, advance in WIDTHS["advance"].items():
        assert hmtx[cmap[ord(char)]][0] == advance, char
    glyphs = font.getGlyphSet()
    pen = pens.BoundsPen(glyphs)
    glyphs[cmap[ord("H")]].draw(pen)
    assert pen.bounds[3] - pen.bounds[1] == WIDTHS["cap_height_units"] == 700


def test_clause_11_7_4_worked_numbers():
    adv = WIDTHS["advance"]
    assert (adv["A"], adv["B"]) == (508, 538)
    width = (adv["A"] + adv["B"]) * 7 / 700
    assert round(width, 3) == 10.46
    assert round(100 - width / 2, 2) == 94.77
    assert round(100 - width, 2) == 89.54
