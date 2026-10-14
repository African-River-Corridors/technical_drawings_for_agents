"""The DXF output (standard clause 15.4): read back with a reader that does not use ezdxf."""

from __future__ import annotations

import io
import json
from pathlib import Path

import ezdxf
import pytest

from technical_drawings_for_agents.dfs import resources, rules
from technical_drawings_for_agents.dfs.conformance import _same
from technical_drawings_for_agents.dfs.pipeline import run_file, sort_key
from technical_drawings_for_agents.dfs.render_dxf import FONT_FILE_NAME, dxf_from_document

from .dxf_reader import read_normalised, records, style_tables

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "docs" / "standard" / "schema" / "examples" / "valid"
SUITE = resources.suite_dir()


def ok_inputs():
    paths = sorted(EXAMPLES.glob("*.json"))
    for case in json.loads((SUITE / "suite.json").read_text())["cases"]:
        if case["kind"] == "document":
            expected = json.loads((SUITE / case["dir"] / "expected.json").read_text())
            if expected["result"] == "ok":
                paths.append(SUITE / case["dir"] / "input.json")
    return paths


INPUTS = ok_inputs()


def build(path):
    doc = json.loads(path.read_text(encoding="utf-8"))
    result = run_file(path)
    assert result["result"] == "ok"
    text, warnings = dxf_from_document(doc, result["normalised"], path.parent)
    return doc, result["normalised"], text, warnings


def label(path):
    return f"{path.parent.name}/{path.name}" if path.name == "input.json" else path.name


@pytest.mark.parametrize("path", INPUTS, ids=label)
def test_dxf_read_back_gives_the_same_normalised_content(path):
    _, norm, text, _ = build(path)
    back = read_normalised(text)
    want = sorted(norm["entities"], key=sort_key)
    got = sorted(back["entities"], key=sort_key)
    assert len(got) == len(want)
    for g, w in zip(got, want):
        assert _same(g, w), (g, w)
    assert back["sheet"] == {"width_mm": norm["sheet"]["width_mm"], "height_mm": norm["sheet"]["height_mm"]}
    assert sorted(back["viewports"]) == [v["id"] for v in norm["viewports"]]
    for vp in norm["viewports"]:
        frame = back["viewports"][vp["id"]]["frame"]
        assert [round(c, 6) for c in frame] == [vp["frame"][k] for k in ("x_mm", "y_mm", "width_mm", "height_mm")]
        assert back["viewports"][vp["id"]]["n"] == vp["scale"]


@pytest.mark.parametrize("path", INPUTS[:12], ids=label)
def test_ezdxf_reads_the_file_without_audit_errors(path):
    _, _, text, _ = build(path)
    doc = ezdxf.read(io.StringIO(text))
    assert len(doc.audit().errors) == 0


def test_two_builds_of_one_document_are_identical():
    path = EXAMPLES / "all-entities.json"
    assert build(path)[2] == build(path)[2]


def test_model_space_is_in_metres_and_paper_space_in_millimetres():
    path = EXAMPLES / "all-entities.json"
    doc, norm, text, _ = build(path)
    layers, ltypes, styles, header = style_tables(text)
    assert header["$INSUNITS"] == 6 and header["$PSLTSCALE"] == 1
    recs = records(text)
    model_lines = [r for r in recs if r.section == "ENTITIES" and r.kind == "LINE"]
    src = next(e for e in doc["viewports"][0]["entities"] if e["type"] == "line")
    assert (model_lines[0].f(10), model_lines[0].f(20), model_lines[0].f(11), model_lines[0].f(21)) == (
        src["from_m"][0], src["from_m"][1], src["to_m"][0], src["to_m"][1],
    )
    border = next(r for r in recs if r.kind == "LWPOLYLINE" and r.xdata().get("origin") == "sheet.border")
    assert [float(v) for v in border.all(10)] == [20.0, 410.0, 410.0, 20.0]  # paper millimetres


def test_viewports_plot_at_the_stated_scale():
    for name in ("two-viewports", "ent-rotated-90"):
        path = EXAMPLES / f"{name}.json" if (EXAMPLES / f"{name}.json").exists() else SUITE / "documents" / name / "input.json"
        _, norm, text, _ = build(path)
        for r in records(text):
            x = r.xdata()
            if r.kind == "VIEWPORT" and "id" in x:
                paper_height_mm, model_height_m = r.f(41), r.f(45)
                assert round(paper_height_mm / model_height_m, 6) == round(1000 / int(x["scale"]), 6)
    path = SUITE / "documents" / "ent-rotated-90" / "input.json"
    _, _, text, _ = build(path)
    twist = [r.f(51) for r in records(text) if r.kind == "VIEWPORT" and "id" in r.xdata()]
    assert twist == [90.0]


def test_layers_carry_colour_line_type_and_snapped_width_and_no_two_share_a_colour():
    doc, norm, text, _ = build(EXAMPLES / "all-entities.json")
    layers, _, _, _ = style_tables(text)
    styles_data = {row["name"]: row for row in resources.line_styles()["layers"]}
    group = rules.line_group("A3")[1]
    standard = [n for n in layers if n in styles_data]
    assert standard
    for name in standard:
        row = styles_data[name]
        assert layers[name]["aci"] == row["aci"]
        assert layers[name]["lineweight"] == round(group[row["role"]] * 100)
        assert layers[name]["lineweight"] / 100 in {w / 1000 for w in resources.line_styles()["cad_line_widths_um"]}
    assert len({layers[n]["aci"] for n in standard}) == len(standard)
    assert layers["VIEWPORT"]["plot"] == 0  # the viewport frames do not plot
    assert all(layers[n]["plot"] == 1 for n in standard)


def test_line_types_hold_the_patterns_in_paper_millimetres():
    _, _, text, _ = build(SUITE / "documents" / "ent-line" / "input.json")
    _, ltypes, _, _ = style_tables(text)
    assert ltypes["TDFA_DASHED_018"] == [2.16, -0.54]  # clause 8.4.4: d = 0.18
    assert ltypes["TDFA_CHAIN_018"] == [4.32, -0.54, 0.09, -0.54]


def test_the_font_is_named_in_the_dxf():
    _, _, text, _ = build(EXAMPLES / "minimal.json")
    _, _, styles, _ = style_tables(text)
    assert styles["TDFA"] == FONT_FILE_NAME == resources.FONT_FILE.name


def test_overlapping_model_windows_give_a_warning():
    _, _, _, warnings = build(EXAMPLES / "two-viewports.json")
    assert any("15.4.9" in w for w in warnings)
    _, _, _, none = build(EXAMPLES / "minimal.json")
    assert none == []


def test_the_dxf_has_a_sheet_layout_with_the_paper_size():
    _, _, text, _ = build(EXAMPLES / "sheet-a0-landscape.json")
    doc = ezdxf.read(io.StringIO(text))
    layout = doc.layouts.get("SHEET")
    assert (layout.dxf.paper_width, layout.dxf.paper_height) == (1189.0, 841.0)
