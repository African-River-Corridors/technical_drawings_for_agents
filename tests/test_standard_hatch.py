"""Clause 10.5 (hatch marks): the data file, the worked numbers and properties of the cases."""

from __future__ import annotations

import itertools
import json
import math
import re
from decimal import Decimal
from pathlib import Path

import pytest

STD = Path(__file__).resolve().parents[1] / "docs" / "standard"
STANDARD = (STD / "drawing-format-standard.md").read_text(encoding="utf-8")
DATA = json.loads((STD / "data" / "hatch-patterns.json").read_text(encoding="utf-8"))
CASES = STD / "conformance" / "documents"
SUITE = json.loads((STD / "conformance" / "suite.json").read_text(encoding="utf-8"))


def expected(case):
    return json.loads((CASES / case / "expected.json").read_text(encoding="utf-8"))


def clause_10_5_7_rows():
    text = STANDARD.split("**10.5.7 Table.**")[1].split("*Basis")[0]
    return {m[0].strip("`"): m for m in re.findall(r"^\| (`\w+`) \| (\w+) \| ([^|]+) \| ([^|]+) \| ([^|]+) \|$", text, re.MULTILINE)}


def test_every_pattern_of_the_schema_is_in_the_data_file_and_the_table():
    schema = json.loads((STD / "schema" / "drawing-document-1.0.schema.json").read_text(encoding="utf-8"))
    names = schema["$defs"]["entity_hatch"]["properties"]["pattern"]["enum"]
    assert set(names) == set(DATA["patterns"]) == set(clause_10_5_7_rows())


def test_the_table_of_clause_10_5_7_agrees_with_the_data_file():
    rows = clause_10_5_7_rows()
    for name, spec in DATA["patterns"].items():
        _, kind, step, line_type, marks = rows[name]
        assert kind == spec["kind"]
        if spec["kind"] == "lines":
            assert f"step {spec['step_u']}" in step
            assert f"`{spec['line_type']}`" in line_type
        if spec["kind"] == "lattice":
            assert f"`A` {spec['column_u']}, `B` {spec['row_u']}" in step
            for mark in spec["marks"].values():
                assert f"clearance {mark['clearance_u']}" in marks
                for x, y in mark.get("points_u", []):
                    assert f"({x}, {y})" in marks.replace("-0.4, -0.3", "-0.4, -0.3")
            dot = spec["marks"]["even"]
            if dot["shape"] == "dot":
                assert f"radius {dot['radius_u']}" in marks


def test_the_unit_is_ten_times_the_thin_width():
    assert DATA["unit_factor"] == 10
    for thin, unit in [("0.18", "1.8"), ("0.25", "2.5"), ("0.35", "3.5")]:
        assert Decimal(thin) * DATA["unit_factor"] == Decimal(unit)
        assert f"{unit} mm" in STANDARD


def test_clause_10_5_9_worked_numbers():
    u = 1.8
    assert (math.ceil(168.5 / u), math.floor(188.5 / u)) == (94, 104)
    assert round(94 * u, 3) == 169.2 and round(104 * u, 3) == 187.2
    assert (math.ceil(168.5 / 3.6), math.floor(188.5 / 3.6)) == (47, 52)


def lines(case):
    return [e for e in expected(case)["entities"] if e["type"] == "line"]


def test_lines_at_angle_0_are_on_a_grid_fixed_to_the_paper_and_split_by_the_hole():
    segments = lines("hatch-lines-0")
    ys = sorted({s["from_mm"][1] for s in segments})
    assert all(abs(y / 1.8 - round(y / 1.8)) < 1e-6 for y in ys)
    assert ys[0] == 154.8 and ys[-1] == 181.8 and len(ys) == 16
    for s in segments:
        assert s["from_mm"][1] == s["to_mm"][1]
        assert 168 <= s["from_mm"][0] < s["to_mm"][0] <= 262
        y = s["from_mm"][1]
        if 164.5 < y < 176.5:  # the hole spans x 195 to 215
            assert s["to_mm"][0] <= 195 or s["from_mm"][0] >= 215
    assert len(segments) == 16 + 7


def test_soil_is_lines_that_are_dashed_and_water_has_twice_the_step():
    assert {s["line_type"] for s in lines("hatch-soil")} == {"dashed"}
    water = sorted({s["from_mm"][1] for s in lines("hatch-water")})
    assert [round(b - a, 3) for a, b in itertools.pairwise(water)] == [3.6] * (len(water) - 1)


def test_lines_in_a_turned_viewport_are_vertical():
    segments = lines("hatch-lines-rotated-viewport")
    assert segments
    assert all(s["from_mm"][0] == s["to_mm"][0] for s in segments)
    assert all(abs(s["from_mm"][0] / 1.8 - round(s["from_mm"][0] / 1.8)) < 1e-6 for s in segments)


def test_the_unit_follows_the_line_group():
    ys = sorted({s["from_mm"][1] for s in lines("hatch-a0-unit")})
    assert all(abs(y / 3.5 - round(y / 3.5)) < 1e-6 for y in ys)


def test_solid_hatches_have_no_marks():
    marks = [e for e in expected("hatch-solid")["entities"] if e["origin"].endswith(".hatch")]
    assert marks == []


def points(entity):
    if entity["type"] == "circle":
        cx, cy = entity["centre_mm"]
        r = entity["radius_mm"]
        return [(cx - r, cy), (cx + r, cy), (cx, cy - r), (cx, cy + r)]
    return [tuple(p) for p in entity["points_mm"]]


@pytest.mark.parametrize("case", ["hatch-concrete", "hatch-rockfill"])
def test_lattice_marks_keep_inside_the_region_and_off_the_hole(case):
    marks = [e for e in expected(case)["entities"] if e["origin"].endswith(".hatch") and e["type"] != "hatch"]
    assert len(marks) > 10
    for mark in marks:
        for x, y in points(mark):
            assert 175 <= x <= 205 and 158.5 <= y <= 178.5
            assert not (185 < x < 190 and 166.5 < y < 173.5)
    kinds = {m["type"] for m in marks}
    assert kinds == ({"circle", "polyline"} if case == "hatch-concrete" else {"polyline"})


def test_every_pattern_has_a_conformance_case():
    ids = {c["id"] for c in SUITE["cases"]}
    for pattern in ("lines", "soil", "water", "concrete", "rockfill", "solid"):
        assert any(i.startswith(f"hatch-{pattern}") for i in ids), pattern
