"""Tests for clause 8 of the Drawing Format Standard: layers, line types, line groups, colour.

The expected values are written by hand from the clause text. The functions here are small
reference readers of `docs/standard/data/line-styles.json`. They are not the product code.
"""

from __future__ import annotations

import itertools
import json
import re
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path

import pytest

# The line widths of the DXF format, in hundredths of a millimetre (0 is the default width and is not a member).
VALID_DXF_LINEWEIGHTS = (0, 5, 9, 13, 15, 18, 20, 25, 30, 35, 40, 50, 53, 60, 70, 80, 90, 100, 106, 120, 140, 158, 200, 211)

DOCS = Path(__file__).resolve().parents[1] / "docs" / "standard"
STANDARD = (DOCS / "drawing-format-standard.md").read_text(encoding="utf-8")
DATA = json.loads((DOCS / "data" / "line-styles.json").read_text(encoding="utf-8"))

SIZES = ("A0", "A1", "A2", "A3", "A4")


def clause_8_text() -> str:
    parts = re.split(r"^## ", STANDARD, flags=re.MULTILINE)
    return next(p for p in parts if p.startswith("8 Layers"))


def table_rows(text: str, first_header: str) -> list[list[str]]:
    """The rows of the first markdown table whose header row starts with `first_header`."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.startswith(f"| {first_header} |"):
            rows = []
            for row in lines[i + 2 :]:
                if not row.startswith("|"):
                    break
                rows.append([c.strip() for c in row.strip().strip("|").split("|")])
            return rows
    raise AssertionError(f"no table starting with {first_header!r}")


def micrometres(width_mm: float) -> int:
    """Clause 8.3.2 step 1: round to 3 places, half-even, on the exact value; whole micrometres."""
    rounded = Decimal(width_mm).quantize(Decimal(1).scaleb(-3), rounding=ROUND_HALF_EVEN)
    return int(rounded * 1000)


def snap(width_mm: float) -> float:
    """Clause 8.3.2, written from the clause text."""
    um = micrometres(width_mm)
    if um == 0 or um > 2110:
        raise ValueError("8.3.2")
    best = min(DATA["cad_line_widths_um"], key=lambda member: (abs(member - um), member))
    return best / 1000


def group_for(size: str) -> dict:
    for name, group in DATA["line_groups"].items():
        if size in group["sizes"]:
            return {"name": name, **group}
    raise KeyError(size)


def entity_width(size: str, layer: str, role: str | None = None) -> float:
    layers = {row["name"]: row for row in DATA["layers"]}
    chosen = role or layers[layer]["role"]
    return group_for(size)[chosen]


def pattern_mm(line_type: str, d_mm: float) -> list[float]:
    quantum = Decimal(1).scaleb(-3)
    out = []
    for multiple in DATA["line_types"][line_type]["pattern_d"]:
        value = Decimal(multiple) * Decimal(str(d_mm))
        out.append(float(value.quantize(quantum, rounding=ROUND_HALF_EVEN)))
    return out


# --------------------------------------------------------------------------- #
# the data file and the text agree
# --------------------------------------------------------------------------- #


def test_the_layer_table_in_the_text_equals_the_data_file():
    rows = table_rows(clause_8_text(), "Layer")
    from_text = [
        {
            "name": r[0],
            "role": r[1].replace(" ", "_"),
            "line_type": r[2],
            "aci": int(r[3]),
            "description": r[4],
        }
        for r in rows
    ]
    assert from_text == DATA["layers"]


def test_the_line_group_table_in_the_text_equals_the_data_file():
    rows = table_rows(clause_8_text(), "Line group")
    from_text = {
        r[0]: {
            "sizes": [s.strip() for s in r[1].split(",")],
            "thin": float(r[2]),
            "thick": float(r[3]),
            "extra_thick": float(r[4]),
        }
        for r in rows
    }
    assert from_text == DATA["line_groups"]


def test_the_line_type_table_in_the_text_equals_the_data_file():
    rows = table_rows(clause_8_text(), "Identifier")
    from_text = {}
    for r in rows:
        ident = r[0].strip("`")
        pattern = [] if r[2].startswith("no pattern") else [float(x) for x in r[2].split(",")]
        from_text[ident] = {"iso_type": r[1], "pattern_d": [int(x) if x == int(x) else x for x in pattern]}
    assert from_text == DATA["line_types"]


def test_the_cad_width_set_in_the_text_equals_the_data_file_and_the_dxf_set():
    quoted = re.search(r"> 0\.05(.*?)2\.11", clause_8_text(), re.DOTALL).group(0)
    numbers = [float(x) for x in re.findall(r"\d+\.\d+", quoted)]
    assert [round(n * 1000) for n in numbers] == DATA["cad_line_widths_um"]
    reference = {w * 10 for w in VALID_DXF_LINEWEIGHTS if w > 0}  # hundredths -> micrometres
    assert set(DATA["cad_line_widths_um"]) == reference


# --------------------------------------------------------------------------- #
# line groups
# --------------------------------------------------------------------------- #


def test_every_size_is_in_exactly_one_group():
    seen = [s for g in DATA["line_groups"].values() for s in g["sizes"]]
    assert sorted(seen) == sorted(SIZES)


@pytest.mark.parametrize(
    ("size", "group"),
    [("A4", "0.35"), ("A3", "0.35"), ("A2", "0.5"), ("A1", "0.5"), ("A0", "0.7")],
)
def test_clause_8_2_group_by_size(size, group):
    assert group_for(size)["name"] == group


def test_group_widths_are_in_the_cad_set_and_follow_the_ratios():
    cad = {w / 1000 for w in DATA["cad_line_widths_um"]}
    for name, g in DATA["line_groups"].items():
        assert float(name) == g["thick"]
        assert {g["thin"], g["thick"], g["extra_thick"]} <= cad
        assert g["extra_thick"] == pytest.approx(2 * g["thick"])
        assert snap(g["thick"] / 2) == g["thin"]  # clause 8.2.3: half of 0.35 is 0.175, table says 0.18


@pytest.mark.parametrize("size", SIZES)
def test_clause_6_3_3_border_is_at_least_0_7_mm(size):
    assert group_for(size)["extra_thick"] >= 0.7


# --------------------------------------------------------------------------- #
# snap
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("width", "expected"),
    [
        (0.36, 0.35),
        (0.12, 0.13),
        (0.175, 0.18),
        (0.55, 0.53),
        (0.165, 0.15),
        (0.275, 0.25),
        (0.45, 0.4),
        (0.7, 0.7),
        (0.02, 0.05),
    ],
)
def test_clause_8_3_3_snap_table(width, expected):
    assert snap(width) == expected


@pytest.mark.parametrize("width", [2.2, 0.0, 0.0004, 2.1111])
def test_clause_8_3_2_out_of_range_is_an_error(width):
    with pytest.raises(ValueError, match=r"8\.3\.2"):
        snap(width)


def test_clause_8_3_3_the_binary_trap_is_real_and_the_rule_avoids_it():
    assert 0.275 * 100 == 27.500000000000004  # nearer to 30 than to 25 in binary
    assert snap(0.275) == 0.25


def test_snap_ties_always_go_to_the_thinner_member():
    members = DATA["cad_line_widths_um"]
    for low, high in itertools.pairwise(members):
        if (low + high) % 2 == 0:
            assert snap((low + high) / 2 / 1000) == low / 1000


# --------------------------------------------------------------------------- #
# line types
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("line_type", "d", "expected"),
    [
        ("dashed", 0.18, [2.16, -0.54]),
        ("dashed", 0.35, [4.2, -1.05]),
        ("dashed", 0.5, [6.0, -1.5]),
        ("chain", 0.18, [4.32, -0.54, 0.09, -0.54]),
        ("chain", 0.35, [8.4, -1.05, 0.175, -1.05]),
        ("chain", 0.5, [12.0, -1.5, 0.25, -1.5]),
        ("chain_double_dashed", 0.18, [4.32, -0.54, 0.09, -0.54, 0.09, -0.54]),
        ("chain_double_dashed", 0.35, [8.4, -1.05, 0.175, -1.05, 0.175, -1.05]),
        ("chain_double_dashed", 0.5, [12.0, -1.5, 0.25, -1.5, 0.25, -1.5]),
        ("continuous", 0.35, []),
    ],
)
def test_clause_8_4_4_patterns(line_type, d, expected):
    assert pattern_mm(line_type, d) == expected


def test_the_pattern_table_in_the_text_matches_these_numbers():
    rows = table_rows(clause_8_text(), "Line type")
    by_type = {r[0].strip("`"): r[1:] for r in rows}
    for line_type, cells in by_type.items():
        for cell, d in zip(cells, (0.18, 0.35, 0.5)):
            expected = [float(x) for x in cell.split(",")]
            assert pattern_mm(line_type, d) == expected


# --------------------------------------------------------------------------- #
# layers and colour
# --------------------------------------------------------------------------- #


def test_there_are_19_standard_layers_with_distinct_names():
    names = [row["name"] for row in DATA["layers"]]
    assert len(names) == 19
    assert len(set(names)) == 19
    assert all(re.fullmatch(r"[A-Z][A-Z0-9_]{0,30}", n) for n in names)


def test_no_two_standard_layers_share_a_cad_colour():
    colours = [row["aci"] for row in DATA["layers"]]
    assert len(set(colours)) == len(colours)
    assert all(1 <= c <= 255 for c in colours)


def test_every_layer_uses_a_known_role_and_line_type():
    for row in DATA["layers"]:
        assert row["role"] in DATA["roles"], row
        assert row["line_type"] in DATA["line_types"], row


# --------------------------------------------------------------------------- #
# what an entity gets (8.7)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("size", "layer", "role", "width"),
    [
        ("A3", "OUTLINE", None, 0.35),
        ("A3", "HIDDEN", None, 0.18),
        ("A3", "PIPE", None, 0.7),
        ("A0", "OUTLINE", None, 0.7),
        ("A0", "HIDDEN", None, 0.35),
        ("A0", "PIPE", None, 1.4),
        ("A3", "TITLEBLOCK", "extra_thick", 0.7),
        ("A0", "TITLEBLOCK", "extra_thick", 1.4),
    ],
)
def test_clause_8_7_1_resolved_widths(size, layer, role, width):
    assert entity_width(size, layer, role) == width


def test_clause_8_7_1_hidden_line_on_a3_has_the_stated_dash():
    width = entity_width("A3", "HIDDEN")
    line_type = next(r for r in DATA["layers"] if r["name"] == "HIDDEN")["line_type"]
    assert pattern_mm(line_type, width) == [2.16, -0.54]
