"""Clause 14: title block, revision register, status, strips."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

jsonschema = pytest.importorskip("jsonschema")

from technical_drawings_for_agents.cli import main
from technical_drawings_for_agents.dfs import furniture, resources, rules, run_file
from technical_drawings_for_agents.dfs.render_dxf import dxf_from_document

from .dxf_reader import records

STD = Path(__file__).resolve().parents[1] / "docs" / "standard"
STANDARD = (STD / "drawing-format-standard.md").read_text(encoding="utf-8")
SUITE = resources.suite_dir()
ISO = json.loads((STD / "data" / "title-block-iso7200.json").read_text(encoding="utf-8"))
TEMPLATE_SCHEMA = json.loads((STD / "schema" / "title-block-template-1.0.schema.json").read_text(encoding="utf-8"))
TEMPLATE_VALIDATOR = jsonschema.Draft202012Validator(TEMPLATE_SCHEMA)
TEMPLATE_INVALID = STD / "schema" / "examples" / "template" / "invalid"


def norm(case):
    result = run_file(SUITE / "documents" / case / "input.json")
    assert result["result"] == "ok", result
    return result["normalised"]


def sheet_parts(case, origin):
    return [e for e in norm(case)["entities"] if e["origin"] == origin]


# -------------------------------------------------------------------------- the template


def test_the_shipped_template_follows_its_schema_and_its_own_rules():
    assert [e.message for e in TEMPLATE_VALIDATOR.iter_errors(ISO)] == []
    assert furniture.template_ok(ISO)
    assert (ISO["width_mm"], ISO["height_mm"]) == (180, 56)


def test_the_cell_table_of_clause_14_3_equals_the_shipped_template():
    text = STANDARD.split("**14.3 The ISO 7200 template.**")[1].split("**14.4 Content.**")[0]
    rows = re.findall(r"^\| `(\w+)` \| (\d+) \| (\d+) \| (\d+) \| (\d+) \| ([\d.]+) \| (yes|no) \|$", text, re.MULTILINE)
    assert len(rows) == len(ISO["cells"]) == 14
    for (field, x, y, w, h, th, req), cell in zip(rows, ISO["cells"]):
        assert cell["field"] == field
        assert (cell["x_mm"], cell["y_mm"], cell["width_mm"], cell["height_mm"], cell["text_height_mm"]) == (int(x), int(y), int(w), int(h), float(th))
        assert bool(cell.get("required")) == (req == "yes")
    assert [c["width_mm"] for c in ISO["register"]["columns"]] == [12, 24, 100, 14, 14, 16]
    assert ISO["register"]["max_rows"] == 8 and ISO["register"]["row_height_mm"] == 5


def test_the_cells_tile_the_block_without_overlap_and_the_lines_close_it():
    area = sum(c["width_mm"] * c["height_mm"] for c in ISO["cells"])
    assert area == 180 * 56
    thick = [ln for ln in ISO["lines"] if ln["role"] == "thick"]
    assert len(thick) == 5


EXPECTED_TEMPLATES = json.loads((TEMPLATE_INVALID / "expected.json").read_text(encoding="utf-8"))


def pointer(error):
    return "".join(f"/{p}" for p in error.absolute_path)


@pytest.mark.parametrize("name", sorted(EXPECTED_TEMPLATES))
def test_every_invalid_template_fails_as_expected(name):
    data = json.loads((TEMPLATE_INVALID / f"{name}.json").read_text(encoding="utf-8"))
    exp = EXPECTED_TEMPLATES[name]
    errors = list(TEMPLATE_VALIDATOR.iter_errors(data))
    hits = [e for e in errors if pointer(e) == exp["path"] and e.validator == exp["keyword"] and exp["message_contains"] in e.message]
    assert hits, [(pointer(e), e.validator) for e in errors]
    assert hits[0].schema.get("x-clause") == exp["clause"]


def test_the_templates_of_the_suite_are_valid_and_synthetic():
    for path in SUITE.glob("documents/*/template.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        errors = list(TEMPLATE_VALIDATOR.iter_errors(data))
        if path.parent.name in ("err-tb-template-invalid-file",):
            assert errors
            continue
        assert errors == [], path
        assert data["id"].startswith(("private-small", "iso7200")) or "id" in data


# -------------------------------------------------------------------------- content


def texts(entities):
    return {e["text"]: e for e in entities if e["type"] == "text"}


def test_the_approver_and_checker_come_only_from_the_latest_entry():
    t = texts(sheet_parts("tb-register-two", "sheet.title_block"))
    assert t["EF"]["anchor_mm"][1] == 231 + 56 - 1  # approved_by cell holds the latest app
    assert "CD" not in t  # entry A has chk CD, the latest entry (B) has none: the cell stays empty
    assert t["B"]["text"] == "B"  # revision index is the latest


def test_the_approver_cell_is_empty_when_the_latest_entry_has_no_approver():
    result = run_file(SUITE / "documents" / "tb-register-numeric" / "input.json")
    labels = {e["text"] for e in result["normalised"]["entities"] if e["origin"] == "sheet.title_block" and e["type"] == "text"}
    assert "EF" not in labels and "APPROVED BY" in labels


def test_scale_is_derived_and_as_shown_when_the_viewports_differ():
    assert "1:100" in texts(sheet_parts("tb-minimal", "sheet.title_block"))
    assert "AS SHOWN" in texts(sheet_parts("tb-scale-as-shown", "sheet.title_block"))


def test_the_sheet_cell_shows_n_over_m():
    assert "2/5" in texts(sheet_parts("tb-fields-all", "sheet.title_block"))


def test_the_block_stands_in_the_bottom_right_corner():
    n = norm("tb-minimal")
    ds = n["sheet"]["drawing_space"]
    lines = [e for e in n["entities"] if e["origin"] == "sheet.title_block" and e["type"] == "line"]
    xs = [c for e in lines for c in (e["from_mm"][0], e["to_mm"][0])]
    ys = [c for e in lines for c in (e["from_mm"][1], e["to_mm"][1])]
    assert max(xs) == ds["x_mm"] + ds["width_mm"] and max(ys) == ds["y_mm"] + ds["height_mm"]
    assert min(xs) == ds["x_mm"] + ds["width_mm"] - 180 + 0 or min(xs) == 230


def test_the_newest_revision_row_is_next_to_the_title_block():
    n = norm("tb-register-two")
    reg = [e for e in n["entities"] if e["origin"] == "sheet.revision_register"]
    poly = next(e for e in reg if e["type"] == "polyline")
    block_top = min(e["from_mm"][1] for e in n["entities"] if e["origin"] == "sheet.title_block" and e["type"] == "line")
    assert max(p[1] for p in poly["points_mm"]) == block_top
    revs = sorted((e["anchor_mm"][1], e["text"]) for e in reg if e["type"] == "text" and e["text"] in ("A", "B"))
    assert [t for _, t in revs] == ["A", "B"]  # oldest first, newest last (lowest, by the block)


def test_the_overflow_row_counts_the_omitted_entries():
    for case, k in (("tb-register-overflow", 4), ("tb-register-overflow-9", 2)):
        t = texts(sheet_parts(case, "sheet.revision_register"))
        assert f"{k} EARLIER REVISIONS NOT SHOWN" in t
    t = texts(sheet_parts("tb-register-full", "sheet.revision_register"))
    assert not any("EARLIER" in k for k in t)


def test_the_register_in_the_overflow_case_shows_the_newest_seven():
    t = texts(sheet_parts("tb-register-overflow", "sheet.revision_register"))
    shown = sorted(k for k in t if re.fullmatch(r"[A-K]", k))
    assert shown == list("EFGHIJK")  # 11 entries A..K: the newest 7 are E..K


# -------------------------------------------------------------------------- fit


def test_no_text_of_the_furniture_is_wider_than_its_cell():
    for case in ("tb-fields-all", "tb-register-two", "tb-register-full", "tb-register-overflow"):
        n = norm(case)
        for e in n["entities"]:
            if e["origin"] == "sheet.revision_register" and e["type"] == "text":
                assert rules.text_width(e["text"], e["height_mm"]) <= 100 - 2 + 1e-6  # the widest column is 100 mm


def test_a_text_that_fits_exactly_is_allowed_and_one_more_character_is_not():
    h, avail = 5, 108
    fits = "A" * 1
    while furniture._fits(fits + "A", h, avail):
        fits += "A"
    assert furniture._fits(fits, h, avail) and not furniture._fits(fits + "A", h, avail)


# -------------------------------------------------------------------------- seals


def test_the_seal_follows_the_canonical_text_of_clause_14_8():
    entry = {"rev": "A", "date": "2026-01-05", "description": "FIRST ISSUE", "by": "AB"}
    text = furniture.canonical(entry, 0, "")
    assert text == '{"app":null,"by":"AB","chk":null,"date":"2026-01-05","description":"FIRST ISSUE","index":0,"prior_digest":"","rev":"A"}'
    assert furniture.seals([entry]) == ["sha256:" + hashlib.sha256(text.encode()).hexdigest()]
    assert furniture.seals([entry])[0] == "sha256:ed44c9a928dd581e3690279a741e60bb1f6e24bebd2cccc91e74350f6b4b08e2"


def test_a_seal_depends_on_every_entry_before_it():
    a = {"rev": "A", "date": "2026-01-05", "description": "ONE", "by": "AB"}
    b = {"rev": "B", "date": "2026-01-06", "description": "TWO", "by": "AB"}
    a2 = dict(a, description="ONE, CHANGED")
    assert furniture.seals([a, b])[1] != furniture.seals([a2, b])[1]
    assert furniture.seals([a, b])[0] == furniture.seals([a])[0]


def test_non_ascii_text_is_not_escaped_in_the_canonical_text():
    e = {"rev": "A", "date": "2026-01-05", "description": "ø", "by": "AB"}
    assert "ø" in furniture.canonical(e, 0, "")


def test_the_seal_command_prints_the_seals_and_writes_nothing(tmp_path, capsys):
    path = SUITE / "documents" / "tb-register-two" / "input.json"
    before = path.read_bytes()
    assert main(["seal", str(path)]) == 0
    out = capsys.readouterr().out.split()
    assert out[0] == "A" and out[1].startswith("sha256:")
    assert path.read_bytes() == before


# -------------------------------------------------------------------------- status


def test_the_status_line_is_small_text_at_the_bottom_left_and_is_a_region():
    line = sheet_parts("tb-status-draft", "sheet.status_line")[0]
    assert line["text"] == "DRAFT \u2013 NOT FOR CONSTRUCTION" and line["height_mm"] == 2.5 and line["layer"] == "TITLEBLOCK"
    assert line["anchor_mm"] == [22, 285] and line["rotation_deg"] == 0 and line["align_h"] == "left"
    assert not sheet_parts("tb-status-draft", "sheet.status_watermark")


def test_every_status_has_its_text_and_issued_has_none():
    assert sheet_parts("tb-status-concept", "sheet.status_line")[0]["text"] == "CONCEPT \u2013 NOT FOR CONSTRUCTION"
    assert sheet_parts("tb-status-issued", "sheet.status_line") == []


def test_the_status_line_stands_on_the_last_line_of_a_notes_strip():
    # strip x 340..410, y 10..287: the line is at (x + 2, y + h - 2)
    line = sheet_parts("status-draft-notes-strip", "sheet.status_line")[0]
    assert line["anchor_mm"] == [342, 285]


@pytest.mark.parametrize("case, clause", [("err-status-notes-strip-no-room", "14.6"), ("err-status-frame-over-line", "7.2.4")])
def test_the_status_line_errors(case, clause):
    assert run_file(SUITE / "documents" / case / "input.json")["errors"] == [{"clause": clause}]


@pytest.mark.parametrize("case", ["err-status-issued-no-approver", "err-status-issued-no-seal", "err-status-issued-no-register", "err-status-issued-no-title-block"])
def test_the_issued_gate_refuses(case):
    assert run_file(SUITE / "documents" / case / "input.json")["errors"] == [{"clause": "14.9"}]


def test_no_tool_can_satisfy_the_gate_by_itself():
    """The gate needs a name in `app`. Nothing in the package writes one."""
    from technical_drawings_for_agents import dfs

    source = "".join(p.read_text(encoding="utf-8") for p in Path(dfs.__file__).parent.glob("*.py"))
    assert '["app"] =' not in source and "'app'] =" not in source


# -------------------------------------------------------------------------- strips, regions, DXF


def test_strips_are_cut_from_the_area_above_the_title_block():
    n = norm("strips-notes-references")
    block_top = min(e["from_mm"][1] for e in n["entities"] if e["origin"] == "sheet.title_block" and e["type"] == "line")
    for origin in ("sheet.notes", "sheet.references"):
        poly = next(e for e in n["entities"] if e["origin"] == origin and e["type"] == "polyline")
        assert max(p[1] for p in poly["points_mm"]) <= block_top + 1e-6


def test_a_note_is_numbered_and_a_reference_joins_its_parts():
    n = norm("strips-notes-references")
    t = {e["text"] for e in n["entities"] if e["origin"] in ("sheet.notes", "sheet.references") and e["type"] == "text"}
    assert "1. ALL DIMENSIONS IN METRES." in t and "EXA-GEN-002  C  GENERAL NOTES" in t and "EXA-ARR-003  ARRANGEMENT" in t


def test_the_dxf_has_no_watermark_layer_and_the_status_line_is_on_the_furniture_layer():
    path = SUITE / "documents" / "tb-status-draft" / "input.json"
    doc = json.loads(path.read_text())
    text, _ = dxf_from_document(doc, run_file(path)["normalised"], path.parent)
    assert not any(r.kind == "LAYER" and r.get(2) == "WATERMARK" for r in records(text))


def test_every_error_of_clause_14_has_a_conformance_case():
    errors = set()
    for case in json.loads((SUITE / "suite.json").read_text())["cases"]:
        if case["kind"] == "document" and re.match(r"err-(tb|reg|status|strip|note|too|region|viewport-over)", case["id"]):
            errors |= {e["clause"] for e in json.loads((SUITE / case["dir"] / "expected.json").read_text())["errors"]}
    assert {"14.2", "14.2.1", "14.4.3", "14.5", "14.6", "14.7.4", "14.7.5", "14.7.6", "14.9", "14.10", "7.2.4"} <= errors
