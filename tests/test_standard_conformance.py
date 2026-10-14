"""Checks on the conformance suite itself (docs/standard/conformance/).

There is no implementation of the standard yet, so these tests cannot run the suite. They check
that the suite is well formed, that its schema-error cases really fail the schema in the expected
way, that the cases that must pass the schema do, and that the expected values agree with a second,
independent source: the existing reference code of this tool, or a small calculation written here
from the clause text. The expected files were written by hand and are never regenerated from any
of these.
"""

from __future__ import annotations

import json
import re
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path

import pytest

jsonschema = pytest.importorskip("jsonschema")

from .reference_sheet import (
    ISO_PREFERRED_DENOMINATORS,
    PlotScale,
    SheetFrame,
    Viewport,
    _derived_bar_length,
    _north_paper_deg,
    scale_bar_tick_label,
)

STD = Path(__file__).resolve().parents[1] / "docs" / "standard"
SUITE_DIR = STD / "conformance"
SUITE = json.loads((SUITE_DIR / "suite.json").read_text(encoding="utf-8"))
SCHEMA = json.loads((STD / "schema" / "drawing-document-1.0.schema.json").read_text(encoding="utf-8"))
RESULT_SCHEMA = json.loads((SUITE_DIR / "result.schema.json").read_text(encoding="utf-8"))
STANDARD = (STD / "drawing-format-standard.md").read_text(encoding="utf-8")
STYLES = json.loads((STD / "data" / "line-styles.json").read_text(encoding="utf-8"))

DOC_VALIDATOR = jsonschema.Draft202012Validator(SCHEMA)

SCHEMA_ERROR_CASES = {
    "err-scale-300": "7.3.2",
    "err-no-viewports": "7.1.1",
    "err-version-not-formed": "18.1",
    "err-sheet-size": "6.1.3",
    "err-text-character": "11.3.3",
    "err-sym-lib-path-and-symbols": "13.3.1",
    "err-sym-scale-zero": "13.3.2",
    "err-tb-both-sources": "14.2",
    "err-status-unknown": "14.9",
    "err-reg-seal-form": "14.7.6",
}


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def doc_cases():
    return [c for c in SUITE["cases"] if c["kind"] == "document"]


def unit_cases():
    return [c for c in SUITE["cases"] if c["kind"] == "unit"]


def case_input(case):
    return load(SUITE_DIR / case["dir"] / "input.json")


def case_expected(case):
    return load(SUITE_DIR / case["dir"] / "expected.json")


def clause_numbers() -> set[str]:
    found = set(re.findall(r"^\*\*(\d+(?:\.\d+)*)[ *]", STANDARD, re.MULTILINE))
    return found | set(re.findall(r"^#{2,3} (\d+(?:\.\d+)*) ", STANDARD, re.MULTILINE))


def validator_for(name: str):
    return jsonschema.Draft202012Validator({"$ref": f"#/$defs/{name}", "$defs": RESULT_SCHEMA["$defs"]})


def as_float_text(value) -> str:
    """JSON text with every number as a float, so 2 and 2.0 compare equal (clause 16.9)."""

    def conv(v):
        if isinstance(v, bool):
            return v
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, list):
            return [conv(x) for x in v]
        return v

    return json.dumps(conv(value))


def rhe(value: float, places: int) -> float:
    out = float(Decimal(value).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_EVEN))
    return 0.0 if out == 0 else out


# --------------------------------------------------------------------------- #
# the suite is well formed
# --------------------------------------------------------------------------- #


def test_the_suite_holds_data_only():
    allowed = {".json", ".md"}
    files = [p for p in SUITE_DIR.rglob("*") if p.is_file()]
    assert files
    assert {p.suffix for p in files} <= allowed, sorted({p.suffix for p in files} - allowed)


def test_suite_ids_are_unique_and_every_file_is_listed():
    ids = [c["id"] for c in SUITE["cases"]]
    assert len(ids) == len(set(ids))
    listed_dirs = {c["dir"] for c in doc_cases()}
    on_disk = {f"documents/{p.name}" for p in (SUITE_DIR / "documents").iterdir() if p.is_dir()}
    assert listed_dirs == on_disk
    listed_units = {c["file"] for c in unit_cases()}
    assert listed_units == {f"units/{p.name}" for p in (SUITE_DIR / "units").glob("*.json")}


def test_every_case_names_real_clauses():
    numbers = clause_numbers()
    for case in SUITE["cases"]:
        assert case["clauses"], case["id"]
        assert set(case["clauses"]) <= numbers, (case["id"], set(case["clauses"]) - numbers)
        assert case["description"]


def test_the_first_cases_the_ticket_asks_for_are_there():
    ids = {c["id"] for c in SUITE["cases"]}
    for entity in ("line", "polyline", "arc", "circle", "centre-line", "hatch", "text"):
        assert f"ent-{entity}" in ids
    for size in ("a0-landscape", "a1-landscape", "a2-landscape", "a3-landscape", "a4-landscape"):
        assert f"sheet-{size}" in ids
    assert {"unit-snap_width", "unit-bar_length", "unit-bar_labels", "unit-round_half_even"} <= ids
    assert len([c for c in doc_cases() if c["id"].startswith("err-")]) >= 15


# --------------------------------------------------------------------------- #
# inputs against the document schema
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("case", doc_cases(), ids=lambda c: c["id"])
def test_inputs_pass_the_document_schema_unless_they_are_schema_error_cases(case):
    errors = list(DOC_VALIDATOR.iter_errors(case_input(case)))
    if case["id"] in SCHEMA_ERROR_CASES:
        assert errors
        clauses = {e.schema.get("x-clause") for e in errors}
        assert clauses == {SCHEMA_ERROR_CASES[case["id"]]}, [(e.validator, e.message) for e in errors]
        assert case_expected(case) == {
            "result": "error",
            "errors": [{"clause": SCHEMA_ERROR_CASES[case["id"]]}],
        }
    else:
        assert [e.message for e in errors] == []


@pytest.mark.parametrize("case", doc_cases(), ids=lambda c: c["id"])
def test_expected_files_follow_the_result_schema(case):
    validator = validator_for("expected")
    assert [e.message for e in validator.iter_errors(case_expected(case))] == []


def test_error_cases_expect_one_clause_each_and_that_clause_exists():
    numbers = clause_numbers()
    for case in doc_cases():
        expected = case_expected(case)
        if expected["result"] == "error":
            assert len(expected["errors"]) == 1, case["id"]  # clause 17.6, rule 2
            assert expected["errors"][0]["clause"] in numbers


# --------------------------------------------------------------------------- #
# invariants of the expected content (clause 16)
# --------------------------------------------------------------------------- #


def sort_key(e):
    t = e["type"]
    geo, last = [], ""
    if t == "line":
        geo = [*e["from_mm"], *e["to_mm"]]
    elif t == "polyline":
        geo = [c for p in e["points_mm"] for c in p] + [1 if e["closed"] else 0]
    elif t == "arc":
        geo = [*e["centre_mm"], e["radius_mm"], e["start_deg"], e["sweep_deg"]]
    elif t == "circle":
        geo = [*e["centre_mm"], e["radius_mm"]]
    elif t == "hatch":
        geo = (
            [c for p in e["boundary_mm"] for c in p]
            + [len(e["holes_mm"])]
            + [c for h in e["holes_mm"] for p in h for c in p]
            + [e["angle_deg"]]
        )
        last = e["pattern"]
    elif t == "text":
        geo = [*e["anchor_mm"], e["height_mm"], e["rotation_deg"]]
        last = e["text"]
    return (
        e["origin"].encode(), t.encode(), e["layer"].encode(), e.get("id", "").encode(),
        e.get("line_type", "").encode(), e.get("width_mm", 0), geo, last.encode(),
        e.get("align_h", "").encode(), e.get("align_v", "").encode(),
    )


def numbers_in(value):
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from numbers_in(v)
    elif isinstance(value, list):
        for v in value:
            yield from numbers_in(v)


OK_CASES = [c for c in doc_cases() if case_expected(c)["result"] == "ok"]


@pytest.mark.parametrize("case", OK_CASES, ids=lambda c: c["id"])
def test_entities_are_sorted_by_clause_16_8(case):
    entities = case_expected(case).get("entities", [])
    assert entities == sorted(entities, key=sort_key)


@pytest.mark.parametrize("case", OK_CASES, ids=lambda c: c["id"])
def test_numbers_are_rounded_to_three_places_and_have_no_negative_zero(case):
    expected = case_expected(case)
    for n in numbers_in({k: v for k, v in expected.items() if k != "origin_prefixes"}):
        assert n == rhe(n, 3), n
        assert str(n) != "-0.0"


@pytest.mark.parametrize("case", OK_CASES, ids=lambda c: c["id"])
def test_widths_come_from_the_line_group_or_are_the_fixed_furniture_widths(case):
    inp = case_input(case)
    size = inp["sheet"]["size"]
    group = next(g for g in STYLES["line_groups"].values() if size in g["sizes"])
    allowed = {group["thin"], group["thick"], group["extra_thick"], 0.5, 0.35}
    for e in case_expected(case).get("entities", []):
        if "width_mm" in e:
            assert e["width_mm"] in allowed, (e["origin"], e["width_mm"])


def test_origin_prefixes_filter_the_expected_entities():
    for case in OK_CASES:
        expected = case_expected(case)
        prefixes = expected.get("origin_prefixes")
        if prefixes:
            assert all(any(e["origin"].startswith(p) for p in prefixes) for e in expected["entities"])


# --------------------------------------------------------------------------- #
# cross-checks against the reference code and independent calculations
# --------------------------------------------------------------------------- #


def reference_viewport(vp, scale):
    f = vp["frame"]
    w = vp["window"]
    return Viewport(
        (w["xmin_m"], w["ymin_m"], w["xmax_m"], w["ymax_m"]),
        PlotScale(scale),
        SheetFrame(f["x_mm"], f["y_mm"], f["width_mm"], f["height_mm"]),
        vp.get("rotation_deg", 0.0),
    )


ENTITY_CASES = [c for c in OK_CASES if c["id"].startswith("ent-") and c["id"] not in ("ent-a0-widths",)]


@pytest.mark.parametrize("case", ENTITY_CASES, ids=lambda c: c["id"])
def test_mapped_points_agree_with_the_reference_viewport(case):
    """Map every source point with the existing reference viewport and compare as sorted sets."""
    inp = case_input(case)
    vp = inp["viewports"][0]
    ref = reference_viewport(vp, vp["scale"])
    k = 1000 / vp["scale"]

    def p3(point):
        return [rhe(c, 3) for c in ref.point(*point)]

    want = {"line": [], "polyline": [], "circle": [], "arc": [], "hatch": [], "text": []}
    for src in vp["entities"]:
        t = src["type"]
        if t in ("line", "centre_line"):
            want["line"].append([p3(src["from_m"]), p3(src["to_m"])])
        elif t == "polyline":
            want["polyline"].append([p3(p) for p in src["points_m"]])
        elif t in ("circle", "arc"):
            want[t].append([p3(src["centre_m"]), rhe(src["radius_m"] * k, 3)])
        elif t == "hatch":
            want["hatch"].append([p3(p) for p in src["boundary_m"]])
        elif t == "text":
            want["text"].append(p3(src["anchor_m"]))

    got = {"line": [], "polyline": [], "circle": [], "arc": [], "hatch": [], "text": []}
    for e in case_expected(case)["entities"]:
        if e["origin"].endswith(".hatch"):
            continue  # the marks of a hatch are checked in test_standard_hatch.py
        t = e["type"]
        if t == "line":
            got[t].append([e["from_mm"], e["to_mm"]])
        elif t == "polyline":
            got[t].append(e["points_mm"])
        elif t in ("circle", "arc"):
            got[t].append([e["centre_mm"], e["radius_mm"]])
        elif t == "hatch":
            got[t].append(e["boundary_mm"])
        elif t == "text":
            got[t].append(e["anchor_mm"])
    for t, wanted in want.items():
        assert sorted(map(as_float_text, got[t])) == sorted(map(as_float_text, wanted)), t


@pytest.mark.parametrize(
    "case",
    [c for c in OK_CASES if c["id"].startswith("bar-derived-1-")],
    ids=lambda c: c["id"],
)
def test_derived_bar_agrees_with_the_reference_code(case):
    vp = case_input(case)["viewports"][0]
    ref = reference_viewport(vp, vp["scale"])
    length = _derived_bar_length(ref)
    labels = [scale_bar_tick_label(length, 5, i, "m") for i in range(6)]
    expected = case_expected(case)["entities"]
    texts = sorted((e["anchor_mm"][0], e["text"]) for e in expected if e["type"] == "text" and e["text"] != f"1:{vp['scale']}")
    assert [t for _, t in texts] == labels
    outline = next(e for e in expected if e["type"] == "polyline")
    total = outline["points_mm"][1][0] - outline["points_mm"][0][0]
    assert total == rhe(ref.length_mm(length), 3)
    ratio = next(e for e in expected if e["type"] == "text" and e["text"].startswith("1:"))
    assert ratio["anchor_mm"][0] == rhe(outline["points_mm"][0][0] + total / 2, 3)


@pytest.mark.parametrize(
    ("name", "rotation", "bearing"),
    [("north-0-0", 0, 0), ("north-90-0", 90, 0), ("north-90-90", 90, 90), ("north-40-25-0", 40.25, 0),
     ("north-0-12-5", 0, 12.5), ("north-m10-0", -10, 0)],
)
def test_north_arrow_agrees_with_the_reference_direction(name, rotation, bearing):
    import math

    case = next(c for c in OK_CASES if c["id"] == name)
    entities = case_expected(case)["entities"]
    hatch = next(e for e in entities if e["type"] == "hatch")
    tip = hatch["boundary_mm"][0]
    centre = (39, 29)  # frame (20, 10) plus 19, clause 7.10.5
    seen = math.degrees(math.atan2(tip[0] - centre[0], -(tip[1] - centre[1]))) % 360.0
    want = _north_paper_deg(rotation % 360.0, bearing)
    assert seen == pytest.approx(want, abs=0.02) or abs(seen - want) > 359.9


# unit checks ------------------------------------------------------------------


def unit_checks(name):
    return load(SUITE_DIR / "units" / f"{name}.json")["checks"]


def test_unit_functions_are_those_of_clause_17_5():
    table = STANDARD.split("**17.5 Unit checks.**")[1].split("**17.6")[0]
    named = set(re.findall(r"^\| `(\w+)` \|", table, re.MULTILINE))
    on_disk = {p.stem for p in (SUITE_DIR / "units").glob("*.json")}
    assert on_disk == named
    for stem in on_disk:
        assert load(SUITE_DIR / "units" / f"{stem}.json")["function"] == stem


@pytest.mark.parametrize("check", unit_checks("round_half_even"), ids=lambda c: c["id"])
def test_unit_round_half_even(check):
    i = check["input"]
    assert rhe(i["value"], i["places"]) == check["expected"]


@pytest.mark.parametrize("check", unit_checks("map_point"), ids=lambda c: c["id"])
def test_unit_map_point_agrees_with_reference(check):
    i = check["input"]
    w = i["window"]
    f = i["frame"]
    ref = Viewport(
        (w["xmin_m"], w["ymin_m"], w["xmax_m"], w["ymax_m"]),
        PlotScale(i["scale"]),
        SheetFrame(f["x_mm"], f["y_mm"], f["width_mm"], f["height_mm"]),
        i["rotation_deg"],
    )
    assert [rhe(c, 3) for c in ref.point(*i["point_m"])] == check["expected"]


@pytest.mark.parametrize("check", unit_checks("window_fits"), ids=lambda c: c["id"])
def test_unit_window_fits_agrees_with_reference(check):
    i = check["input"]
    w, f = i["window"], i["frame"]
    ref = Viewport(
        (w["xmin_m"], w["ymin_m"], w["xmax_m"], w["ymax_m"]),
        PlotScale(i["scale"]),
        SheetFrame(f["x_mm"], f["y_mm"], f["width_mm"], f["height_mm"]),
        i["rotation_deg"],
    )
    assert ref.fits is check["expected"]


@pytest.mark.parametrize("check", unit_checks("fit_scale"), ids=lambda c: c["id"])
def test_unit_fit_scale_agrees_with_reference(check):
    i = check["input"]
    w, f = i["window"], i["frame"]
    frame = SheetFrame(f["x_mm"], f["y_mm"], f["width_mm"], f["height_mm"])
    got = None
    for n in sorted((*ISO_PREFERRED_DENOMINATORS, 25, 250, 1250, 2500)):
        vp = Viewport((w["xmin_m"], w["ymin_m"], w["xmax_m"], w["ymax_m"]), PlotScale(n), frame, i["rotation_deg"])
        if vp.fits:
            got = n
            break
    assert got == check["expected"]


@pytest.mark.parametrize("check", unit_checks("bar_length"), ids=lambda c: c["id"])
def test_unit_bar_length_agrees_with_reference(check):
    i = check["input"]
    frame = SheetFrame(0, 0, i["frame_width_mm"], 277)
    vp = Viewport((0, 0, 1, 1), PlotScale(i["scale"]), frame)
    assert _derived_bar_length(vp) == check["expected"]


@pytest.mark.parametrize("check", unit_checks("bar_labels"), ids=lambda c: c["id"])
def test_unit_bar_labels_agree_with_reference(check):
    i = check["input"]
    got = [scale_bar_tick_label(i["length_m"], i["divisions"], k, i["unit"]) for k in range(i["divisions"] + 1)]
    assert got == check["expected"]


@pytest.mark.parametrize("check", unit_checks("north_deg"), ids=lambda c: c["id"])
def test_unit_north_deg_agrees_with_reference(check):
    i = check["input"]
    assert rhe(_north_paper_deg(i["rotation_deg"] % 360.0, i["model_bearing_deg"]), 3) == check["expected"]


@pytest.mark.parametrize("check", unit_checks("line_pattern"), ids=lambda c: c["id"])
def test_unit_line_pattern_is_the_multiple_of_the_width(check):
    i = check["input"]
    multiples = STYLES["line_types"][i["line_type"]]["pattern_d"]
    got = [rhe(float(Decimal(str(m)) * Decimal(str(i["width_mm"]))), 3) for m in multiples]
    assert got == check["expected"]


@pytest.mark.parametrize("check", unit_checks("snap_width"), ids=lambda c: c["id"])
def test_unit_snap_width(check):
    um = int(Decimal(check["input"]["width_mm"]).quantize(Decimal("0.001"), rounding=ROUND_HALF_EVEN) * 1000)
    if um == 0 or um > 2110:
        assert check["expected"] == {"error": "8.3.2"}
        return
    best = min(STYLES["cad_line_widths_um"], key=lambda m: (abs(m - um), m))
    assert best / 1000 == check["expected"]


@pytest.mark.parametrize("check", unit_checks("arc_sweep"), ids=lambda c: c["id"])
def test_unit_arc_sweep(check):
    i = check["input"]
    sweep = (i["end_deg"] - i["start_deg"]) % 360
    assert (check["expected"] == {"error": "9.5.1"}) if sweep == 0 else (check["expected"] == sweep)


@pytest.mark.parametrize("check", unit_checks("drawing_space"), ids=lambda c: c["id"])
def test_unit_drawing_space(check):
    insets = {"A0": (20, 20, 20, 20), "A1": (20, 20, 20, 20)}
    i = check["input"]
    short, long = {"A0": (841, 1189), "A3": (297, 420), "A4": (210, 297)}[i["size"]]
    w, h = (long, short) if i["orientation"] == "landscape" else (short, long)
    left, top, right, bottom = insets.get(i["size"], (20, 10, 10, 10))
    assert check["expected"] == {"x_mm": left, "y_mm": top, "width_mm": w - left - right, "height_mm": h - top - bottom}


def test_the_sheet_cases_agree_with_clause_6_by_independent_counts():
    zones = {"A0": (24, 16), "A1": (16, 12), "A2": (12, 8), "A3": (8, 6), "A4": (6, 4)}
    for case in OK_CASES:
        if not case["id"].startswith("sheet-"):
            continue
        inp = case_input(case)
        size, orient = inp["sheet"]["size"], inp["sheet"]["orientation"]
        long_n, short_n = zones[size]
        nh, nv = (long_n, short_n) if orient == "landscape" else (short_n, long_n)
        entities = case_expected(case)["entities"]

        def count(origin, entities=entities):
            return sum(1 for e in entities if e["origin"] == origin)

        assert count("sheet.border") == 1
        assert count("sheet.centring_mark") == 4
        assert count("sheet.zone_mark") == 2 * (nh - 1) + 2 * (nv - 1)
        assert count("sheet.zone_label") == 2 * nh + 2 * nv


def font_advances():
    ttlib = pytest.importorskip("fontTools.ttLib")
    font = ttlib.TTFont(
        STD.parents[1] / "src/technical_drawings_for_agents/data/fonts/BarlowSemiCondensed-Regular.ttf"
    )
    cmap, hmtx = font.getBestCmap(), font["hmtx"]
    return lambda ch: hmtx[cmap[ord(ch)]][0]


@pytest.mark.parametrize("check", unit_checks("text_width"), ids=lambda c: c["id"])
def test_unit_text_width_agrees_with_the_font_file(check):
    advance = font_advances()
    i = check["input"]
    total = sum(advance(ch) for ch in i["text"])
    assert rhe(total * i["height_mm"] / 700, 3) == check["expected"]


@pytest.mark.parametrize("check", unit_checks("text_box"), ids=lambda c: c["id"])
def test_unit_text_box_agrees_with_a_second_calculation(check):
    import math

    advance = font_advances()
    i = check["input"]
    ax, ay = i["anchor_mm"]
    h = i["height_mm"]
    width = sum(advance(ch) for ch in i["text"]) * h / 700
    left = {"left": ax, "centre": ax - width / 2, "right": ax - width}[i["align_h"]]
    base = {"baseline": ay, "middle": ay + h / 2, "top": ay + h}[i["align_v"]]
    rho = math.radians(i["rotation_deg"])
    cos, sin = round(math.cos(rho), 12), round(math.sin(rho), 12)
    corners = []
    for x, y in [(left, base), (left + width, base), (left + width, base - h), (left, base - h)]:
        dx, dy = x - ax, y - ay
        corners.append([rhe(ax + dx * cos + dy * sin, 3), rhe(ay - dx * sin + dy * cos, 3)])
    assert corners == [[float(a), float(b)] for a, b in check["expected"]]
