"""Clause 13 (symbols): the library schema, placement, ports, and the DXF blocks."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

jsonschema = pytest.importorskip("jsonschema")

from technical_drawings_for_agents.dfs import resources, run_file
from technical_drawings_for_agents.dfs.render_dxf import dxf_from_document

from .dxf_reader import records

STD = Path(__file__).resolve().parents[1] / "docs" / "standard"
SCHEMA_DIR = STD / "schema"
LIB_SCHEMA = json.loads((SCHEMA_DIR / "symbol-library-1.0.schema.json").read_text(encoding="utf-8"))
DOC_SCHEMA = json.loads((SCHEMA_DIR / "drawing-document-1.0.schema.json").read_text(encoding="utf-8"))
LIB_DIR = SCHEMA_DIR / "examples" / "library"
SUITE = resources.suite_dir()
VALIDATOR = jsonschema.Draft202012Validator(LIB_SCHEMA)


def pointer(error) -> str:
    return "".join(f"/{p}" for p in error.absolute_path)


def test_the_library_schema_is_valid_and_follows_the_document_schema():
    jsonschema.Draft202012Validator.check_schema(LIB_SCHEMA)
    for name in ("symbol", "symbol_set", "symbol_part", "symbol_part_line", "symbol_part_polyline", "symbol_part_arc", "symbol_part_circle", "symbol_part_hatch"):
        assert LIB_SCHEMA["$defs"][name] == DOC_SCHEMA["$defs"][name], name


def test_every_x_clause_of_the_library_schema_is_a_clause():
    text = (STD / "drawing-format-standard.md").read_text(encoding="utf-8")
    import re

    numbers = set(re.findall(r"^\*\*(\d+(?:\.\d+)*)[ *]", text, re.MULTILINE)) | set(re.findall(r"^#{2,3} (\d+(?:\.\d+)*) ", text, re.MULTILINE))

    def walk(node):
        if isinstance(node, dict):
            if "x-clause" in node:
                yield node["x-clause"]
            for v in node.values():
                yield from walk(v)
        elif isinstance(node, list):
            for v in node:
                yield from walk(v)

    used = set(walk(LIB_SCHEMA))
    assert used and used <= numbers, used - numbers


def test_the_example_library_is_valid():
    data = json.loads((LIB_DIR / "valves.json").read_text(encoding="utf-8"))
    assert [e.message for e in VALIDATOR.iter_errors(data)] == []


INVALID = LIB_DIR / "invalid"
EXPECTED = json.loads((INVALID / "expected.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_every_invalid_library_fails_with_the_expected_error(name):
    data = json.loads((INVALID / f"{name}.json").read_text(encoding="utf-8"))
    expected = EXPECTED[name]
    errors = list(VALIDATOR.iter_errors(data))
    matches = [e for e in errors if pointer(e) == expected["path"] and e.validator == expected["keyword"] and expected["message_contains"] in e.message]
    assert matches, [(pointer(e), e.validator) for e in errors]
    assert matches[0].schema.get("x-clause") == expected["clause"]


def norm(case):
    result = run_file(SUITE / "documents" / case / "input.json")
    assert result["result"] == "ok"
    return result["normalised"]


def parts(case):
    return [e for e in norm(case)["entities"] if e["origin"].endswith(".symbol")]


def extent(entities):
    xs, ys = [], []
    for e in entities:
        if e["type"] == "polyline":
            for x, y in e["points_mm"]:
                xs.append(x)
                ys.append(y)
    return max(xs) - min(xs), max(ys) - min(ys)


def test_clause_13_4_1_a_paper_symbol_has_one_size_on_the_paper_at_every_scale():
    assert extent(parts("sym-paper-valve")) == extent(parts("sym-paper-scale-200")) == (6, 4)


def test_clause_13_4_1_a_model_symbol_follows_the_scale():
    def radius(case):
        return next(e["radius_mm"] for e in parts(case) if e["type"] == "circle")

    assert radius("sym-model-tank") == 5 and radius("sym-model-tank-200") == 2.5


def test_the_insert_scale_multiplies_the_size():
    assert extent(parts("sym-paper-twice")) == (12, 8)


def test_a_part_takes_the_role_of_its_layer_unless_it_names_one():
    widths = {(e["type"], e["width_mm"]) for e in parts("sym-paper-valve") if e["type"] != "text"}
    assert widths == {("polyline", 0.35), ("line", 0.18)}  # EQUIP is thick; the tick line says thin
    heavy = {e["width_mm"] for e in parts("sym-line-types-and-roles") if e["type"] == "polyline"}
    assert heavy == {0.18, 0.7}  # HIDDEN is thin; the role of the second insert is extra thick
    on_hidden = {e["line_type"] for e in parts("sym-line-types-and-roles") if e["layer"] == "HIDDEN" and e["type"] != "text"}
    assert on_hidden == {"dashed"}  # the layer HIDDEN is dashed, and no part says otherwise


def test_the_label_is_upright_text_at_the_slot_whatever_the_rotation():
    for case in ("sym-rotate-90", "sym-rotated-viewport"):
        labels = [e for e in parts(case) if e["type"] == "text"]
        assert labels and all(e["rotation_deg"] == 0 for e in labels)


def test_ports_are_places_that_dimensions_can_point_at():
    n = norm("sym-ports")
    texts = [e["text"] for e in n["entities"] if e["origin"].endswith(".dimension") and e["type"] == "text"]
    assert texts == ["3.40"]  # valve 1 out at 3.3 m, valve 2 in at 6.7 m: 3.4 m


def test_a_file_library_gives_the_same_content_as_the_same_library_in_the_document():
    a = norm("sym-file-library")["entities"]
    inline = json.loads((SUITE / "documents" / "sym-file-library" / "input.json").read_text())
    lib = json.loads((SUITE / "documents" / "sym-file-library" / "library.json").read_text())
    inline["symbol_libraries"] = [{"id": "std", "symbols": lib["symbols"]}]
    from technical_drawings_for_agents.dfs import run_document

    b = run_document(inline)["normalised"]["entities"]
    assert a == b


def test_symbols_are_written_to_the_dxf_as_blocks_and_inserts():
    path = SUITE / "documents" / "sym-paper-valve" / "input.json"
    doc = json.loads(path.read_text())
    result = run_file(path)
    text, _ = dxf_from_document(doc, result["normalised"], path.parent)
    recs = records(text)
    blocks = [r for r in recs if r.kind == "BLOCK" and (r.get(2) or "").startswith("TDFA_")]
    assert [b.get(2) for b in blocks] == ["TDFA_std_valve@EQUIP"]
    inserts = [r for r in recs if r.kind == "INSERT" and r.xdata().get("type") == "symbol"]
    assert len(inserts) == 1
    insert = inserts[0]
    assert insert.get(2) == "TDFA_std_valve@EQUIP"
    assert insert.f(41) == pytest.approx(0.1)  # f = s * N / 1000: the symbol plots at its size on paper
    assert (insert.f(10), insert.f(20)) == (3.0, 4.0)


def test_one_block_serves_many_inserts_on_one_layer():
    doc = {
        "standard_version": "1.0", "sheet": {"size": "A3"}, "layers": [{"name": "EQUIP"}],
        "symbol_libraries": json.loads((SUITE / "documents" / "sym-paper-valve" / "input.json").read_text())["symbol_libraries"],
        "viewports": [{"id": "main", "frame": {"x_mm": 20, "y_mm": 10, "width_mm": 390, "height_mm": 277},
                       "window": {"xmin_m": 0, "ymin_m": 0, "xmax_m": 10, "ymax_m": 8}, "scale": 100,
                       "entities": [{"type": "symbol", "layer": "EQUIP", "library": "std", "symbol": "valve", "position_m": [x, 4]} for x in (2, 4, 6)]}],
    }
    norm_ = __import__("technical_drawings_for_agents.dfs", fromlist=["run_document"]).run_document(doc)["normalised"]
    text, _ = dxf_from_document(doc, norm_)
    recs = records(text)
    assert len([r for r in recs if r.kind == "BLOCK" and (r.get(2) or "").startswith("TDFA_")]) == 1
    assert len([r for r in recs if r.kind == "INSERT" and r.xdata().get("type") == "symbol"]) == 3


def test_every_error_of_clause_13_has_a_conformance_case():
    errors = set()
    for case in json.loads((SUITE / "suite.json").read_text())["cases"]:
        if case["id"].startswith("err-sym"):
            errors |= {e["clause"] for e in json.loads((SUITE / case["dir"] / "expected.json").read_text())["errors"]}
    assert {"13.1", "13.3.1", "13.3.2", "13.5.1", "7.7.1", "12.2"} <= errors
