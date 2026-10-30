"""Clause 12 (dimensions and leaders): the text always equals the measured geometry."""

from __future__ import annotations

import json
import math
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path

import pytest

from dfs import resources, run_document, run_file

STD = Path(__file__).resolve().parents[1] / "docs" / "standard"
SUITE = resources.suite_dir()
DOCS = [SUITE / "documents" / c["dir"].split("/")[1] / "input.json" for c in json.loads((SUITE / "suite.json").read_text())["cases"] if c["kind"] == "document" and c["id"].startswith("dim-")]
DOCS.append(STD / "schema" / "examples" / "valid" / "dimensions.json")


def fmt(value: float, decimals: int) -> str:
    q = Decimal(value).quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_EVEN)
    return format(q, "f")


def point(spec, entities):
    if "point_m" in spec:
        return spec["point_m"]
    e = next(x for x in entities if x.get("id") == spec["ref"])
    if e["type"] == "line":
        return e["from_m"] if spec["at"] == "start" else e["to_m"]
    return e["centre_m"]


def measured_texts(doc):
    """The text of each dimension, worked out again from the input geometry (not from the pipeline)."""
    entities = doc["viewports"][0]["entities"]
    out = []
    for e in entities:
        if e["type"] != "dimension":
            continue
        unit = e.get("unit", "m")
        k = 1000 if unit == "mm" else 1
        kind = e["kind"]
        dec = e.get("decimals", 1 if kind == "angle" else (2 if unit == "m" else 0))
        if kind == "linear":
            a, b = point(e["from"], entities), point(e["to"], entities)
            axis = 0 if e["orientation"] == "horizontal" else 1
            out.append(fmt(abs(b[axis] - a[axis]) * k, dec))
        elif kind == "aligned":
            a, b = point(e["from"], entities), point(e["to"], entities)
            out.append(fmt(math.hypot(b[0] - a[0], b[1] - a[1]) * k, dec))
        elif kind in ("radius", "diameter"):
            target = next(x for x in entities if x.get("id") == e["of"])
            out.append(("R" if kind == "radius" else "Ø") + fmt(target["radius_m"] * (1 if kind == "radius" else 2) * k, dec))
        else:
            v, a, b = (point(e[n], entities) for n in ("vertex", "from", "to"))
            ta = math.degrees(math.atan2(a[1] - v[1], a[0] - v[0]))
            tb = math.degrees(math.atan2(b[1] - v[1], b[0] - v[0]))
            out.append(fmt((tb - ta) % 360, dec) + "°")
    return out


@pytest.mark.parametrize("path", DOCS, ids=lambda p: p.parent.name if p.name == "input.json" else p.name)
def test_every_dimension_text_equals_the_measured_geometry(path):
    doc = json.loads(path.read_text(encoding="utf-8"))
    result = run_file(path)
    assert result["result"] == "ok"
    texts = sorted(e["text"] for e in result["normalised"]["entities"] if e["origin"].endswith(".dimension") and e["type"] == "text")
    assert texts == sorted(measured_texts(doc))


@pytest.mark.parametrize("path", DOCS, ids=lambda p: p.parent.name if p.name == "input.json" else p.name)
def test_the_parts_have_the_arrowheads_and_widths_of_clause_12_3(path):
    result = run_file(path)
    group = resources.line_styles()["line_groups"]["0.35"]
    u = 10 * group["thin"]
    for e in result["normalised"]["entities"]:
        if not e["origin"].endswith((".dimension", ".leader")):
            continue
        if e["type"] == "hatch":
            tip, p2, p3 = e["boundary_mm"]
            base = [(p2[0] + p3[0]) / 2, (p2[1] + p3[1]) / 2]
            assert math.hypot(tip[0] - base[0], tip[1] - base[1]) == pytest.approx(2 * u, abs=0.003)
            assert math.hypot(p2[0] - p3[0], p2[1] - p3[1]) == pytest.approx(u / 2, abs=0.003)
            assert e["pattern"] == "solid"
        if e["type"] in ("line", "arc", "hatch"):
            assert e["width_mm"] == group["thin"]
        if e["type"] in ("line", "arc"):
            assert e["line_type"] == "continuous"


def test_every_dimension_has_two_arrowheads_except_radius_and_leader():
    result = run_file(SUITE / "documents" / "dim-radius" / "input.json")
    assert sum(1 for e in result["normalised"]["entities"] if e["origin"].endswith(".dimension") and e["type"] == "hatch") == 1
    result = run_file(SUITE / "documents" / "dim-diameter-90" / "input.json")
    assert sum(1 for e in result["normalised"]["entities"] if e["origin"].endswith(".dimension") and e["type"] == "hatch") == 2
    result = run_file(SUITE / "documents" / "dim-leader" / "input.json")
    assert sum(1 for e in result["normalised"]["entities"] if e["origin"].endswith(".leader") and e["type"] == "hatch") == 1


def test_the_dimension_follows_the_geometry_it_points_at():
    path = SUITE / "documents" / "dim-linear-horizontal" / "input.json"
    doc = json.loads(path.read_text())
    before = run_file(path)["normalised"]
    doc["viewports"][0]["entities"][0]["to_m"] = [7, 1]  # the line is now 6 m long
    after = run_document(doc)["normalised"]
    text = lambda n: next(e["text"] for e in n["entities"] if e["origin"].endswith(".dimension") and e["type"] == "text")
    assert (text(before), text(after)) == ("8.00", "6.00")


def test_clause_12_8_1_worked_numbers():
    parts = [fmt(1.254, 2)] * 3
    assert parts == ["1.25"] * 3
    assert fmt(3.762, 2) == "3.76"
    assert sum(Decimal(p) for p in parts) == Decimal("3.75") != Decimal("3.76")


def test_the_rotated_viewport_turns_the_dimension_with_the_model():
    plain = run_file(SUITE / "documents" / "dim-linear-horizontal" / "input.json")["normalised"]
    turned = run_file(SUITE / "documents" / "dim-rotated-viewport" / "input.json")["normalised"]
    pl = [e for e in plain["entities"] if e["origin"].endswith(".dimension") and e["type"] == "line"]
    tl = [e for e in turned["entities"] if e["origin"].endswith(".dimension") and e["type"] == "line"]
    assert any(e["from_mm"][1] == e["to_mm"][1] for e in pl)  # horizontal on the sheet
    assert any(e["from_mm"][0] == e["to_mm"][0] for e in tl)  # vertical on the sheet after a turn of 90


def test_each_kind_and_each_error_of_clause_12_has_a_conformance_case():
    ids = {c["id"] for c in json.loads((SUITE / "suite.json").read_text())["cases"]}
    for kind in ("linear-horizontal", "linear-vertical", "aligned", "radius", "diameter-90", "angle", "leader"):
        assert f"dim-{kind}" in ids
    errors = set()
    for c in json.loads((SUITE / "suite.json").read_text())["cases"]:
        if c["id"].startswith(("err-dim", "err-leader")):
            errors |= {e["clause"] for e in json.loads((SUITE / c["dir"] / "expected.json").read_text())["errors"]}
    assert {"12.2", "12.4.1", "12.4.3", "12.5.1", "12.5.4", "12.6.1", "12.8", "7.7.1"} <= errors
