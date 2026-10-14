"""The reference pipeline (technical_drawings_for_agents.dfs): suite, outputs, repeat builds, CLI."""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from technical_drawings_for_agents.cli import main
from technical_drawings_for_agents.dfs import resources, run_document, run_file
from technical_drawings_for_agents.dfs.conformance import compare, run_suite
from technical_drawings_for_agents.dfs.render_pdf import (
    FONT_ASCENT,
    FONT_BBOX,
    FONT_CAP_HEIGHT,
    FONT_DESCENT,
    pdf_from_normalised,
)
from technical_drawings_for_agents.dfs.render_svg import svg_from_normalised

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "docs" / "standard" / "schema" / "examples"
VALID = sorted((EXAMPLES / "valid").glob("*.json"))
INVALID = sorted(p for p in (EXAMPLES / "invalid").glob("*.json") if p.name != "expected.json")
SUITE = resources.suite_dir()


def normalised(path):
    result = run_file(path)
    assert result["result"] == "ok", result
    return result["normalised"]


# --------------------------------------------------------------------------- #
# the conformance suite, run through this implementation
# --------------------------------------------------------------------------- #


def test_the_implementation_passes_every_conformance_case():
    failed = [(case, problems[0]) for case, problems in run_suite(SUITE) if problems]
    assert failed == []


def test_the_comparison_can_fail():
    expected = json.loads((SUITE / "documents" / "ent-circle" / "expected.json").read_text())
    result = run_file(SUITE / "documents" / "ent-circle" / "input.json")
    assert compare(result, expected) == []
    expected["entities"][0]["radius_mm"] += 0.001
    assert compare(result, expected)
    expected["result"] = "error"
    expected["errors"] = [{"clause": "9.6"}]
    assert compare(result, expected)


def test_every_schema_invalid_example_is_an_error_naming_the_expected_clause():
    expected = json.loads((EXAMPLES / "invalid" / "expected.json").read_text())
    for path in INVALID:
        result = run_file(path)
        assert result["result"] == "error", path.name
        assert expected[path.stem]["clause"] in {e["clause"] for e in result["errors"]}, path.name


@pytest.mark.parametrize("path", VALID, ids=lambda p: p.name)
def test_every_valid_example_builds(path):
    norm = normalised(path)
    assert norm["entities"]


def test_bad_json_and_a_repeated_key_are_errors():
    assert run_document("{not json")["errors"] == [{"clause": "4.1"}]
    assert run_document('{"a": 1, "a": 2}')["errors"] == [{"clause": "4.4"}]


# --------------------------------------------------------------------------- #
# repeat builds
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("path", VALID, ids=lambda p: p.name)
def test_two_builds_of_one_document_are_identical_in_every_output(path):
    a, b = normalised(path), normalised(path)
    assert json.dumps(a) == json.dumps(b)
    assert svg_from_normalised(a) == svg_from_normalised(b)
    assert pdf_from_normalised(a) == pdf_from_normalised(b)


# --------------------------------------------------------------------------- #
# the PDF
# --------------------------------------------------------------------------- #


def test_the_pdf_embeds_the_font_and_its_text_can_be_read():
    pypdf = pytest.importorskip("pypdf")
    import io

    norm = normalised(EXAMPLES / "valid" / "all-entities.json")
    reader = pypdf.PdfReader(io.BytesIO(pdf_from_normalised(norm)))
    page = reader.pages[0]
    font = page["/Resources"]["/Font"]["/F1"]
    descriptor = font["/FontDescriptor"]
    assert str(font["/BaseFont"]) == "/BarlowSemiCondensed-Regular"
    assert descriptor["/FontFile2"].get_data() == resources.FONT_FILE.read_bytes()
    assert "SECTION A-A" in page.extract_text()
    assert [round(float(v), 2) for v in page.mediabox] == [0, 0, 1190.55, 841.89]


def test_the_font_constants_of_the_pdf_match_the_font_file():
    ttlib = pytest.importorskip("fontTools.ttLib")
    font = ttlib.TTFont(resources.FONT_FILE)
    head, os2, hhea = font["head"], font["OS/2"], font["hhea"]
    assert FONT_BBOX == (head.xMin, head.yMin, head.xMax, head.yMax)
    assert (FONT_ASCENT, FONT_DESCENT, FONT_CAP_HEIGHT) == (hhea.ascent, hhea.descent, os2.sCapHeight)


def test_the_pdf_has_one_page_of_the_sheet_size_for_every_size():
    pypdf = pytest.importorskip("pypdf")
    import io

    sizes = {"a0": (841, 1189), "a1": (594, 841), "a2": (420, 594), "a3": (297, 420), "a4": (210, 297)}
    for name, (short, long) in sizes.items():
        norm = normalised(EXAMPLES / "valid" / f"sheet-{name}-landscape.json")
        reader = pypdf.PdfReader(io.BytesIO(pdf_from_normalised(norm)))
        box = reader.pages[0].mediabox
        assert len(reader.pages) == 1
        assert (round(float(box.width) / 72 * 25.4), round(float(box.height) / 72 * 25.4)) == (long, short)


def test_the_pdf_draws_in_black_only():
    norm = normalised(EXAMPLES / "valid" / "all-entities.json")
    data = pdf_from_normalised(norm)
    import zlib

    streams = re.findall(rb"stream\n(.*?)\nendstream", data, re.DOTALL)
    content = zlib.decompress(streams[0]).decode("ascii")
    assert "0 G 0 g" in content
    assert not re.search(r"\d [\d.]+ [\d.]+ (rg|RG)\b", content)


# --------------------------------------------------------------------------- #
# the SVG
# --------------------------------------------------------------------------- #


def test_the_svg_is_xml_in_millimetres_with_every_entity():
    norm = normalised(EXAMPLES / "valid" / "all-entities.json")
    svg = svg_from_normalised(norm)
    root = ET.fromstring(svg)
    assert root.attrib["width"] == "420mm" and root.attrib["viewBox"] == "0 0 420 297"
    drawn = [el for el in root if el.tag.split("}")[1] in ("line", "path", "circle", "text")]
    not_drawn = [e for e in norm["entities"] if e["type"] == "hatch" and e["pattern"] != "solid"]
    assert len(drawn) == len(norm["entities"]) - len(not_drawn)  # a marked hatch draws only its marks
    assert "#000" in svg and not re.search(r'(stroke|fill)="#(?!000)[0-9a-fA-F]{3,6}"', svg)


def test_the_svg_gives_dashes_in_paper_millimetres():
    norm = normalised(SUITE / "documents" / "ent-line" / "input.json")
    svg = svg_from_normalised(norm, embed_font=False)
    assert 'stroke-dasharray="2.16 0.54"' in svg  # HIDDEN on A3: thin 0.18, dashed 12d 3d
    assert "base64" not in svg


def test_a_font_is_embedded_in_the_svg_by_default():
    norm = normalised(EXAMPLES / "valid" / "minimal.json")
    assert "@font-face" in svg_from_normalised(norm)


# --------------------------------------------------------------------------- #
# the commands
# --------------------------------------------------------------------------- #


def test_cli_validate_and_errors(capsys):
    good = str(EXAMPLES / "valid" / "minimal.json")
    assert main(["validate", good]) == 0
    bad = str(SUITE / "documents" / "err-bar-379" / "input.json")
    assert main(["validate", bad]) == 1
    assert "clause 7.9.3" in capsys.readouterr().err


def test_cli_normalise_prints_the_normalised_content(capsys):
    assert main(["normalise", str(EXAMPLES / "valid" / "minimal.json")]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["sheet"]["size"] == "A4" and out["sheet"]["orientation"] == "portrait"


def test_cli_run_is_the_runner_of_clause_17_3(capsys):
    case = SUITE / "documents" / "fit-barge-250"
    assert main(["run", str(case / "input.json")]) == 0
    result = json.loads(capsys.readouterr().out)
    assert compare(result, json.loads((case / "expected.json").read_text())) == []


def test_cli_build_writes_four_files(tmp_path, capsys):
    assert main(["build", str(EXAMPLES / "valid" / "custom-layer.json"), "--out", str(tmp_path)]) == 0
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "custom-layer.dxf",
        "custom-layer.normalised.json",
        "custom-layer.pdf",
        "custom-layer.svg",
    ]
    assert (tmp_path / "custom-layer.pdf").read_bytes().startswith(b"%PDF-1.7")


def test_cli_build_writes_nothing_when_the_document_has_an_error(tmp_path):
    assert main(["build", str(SUITE / "documents" / "err-duplicate-id" / "input.json"), "--out", str(tmp_path)]) == 1
    assert list(tmp_path.iterdir()) == []


def test_cli_conformance_passes(capsys):
    assert main(["conformance"]) == 0
    assert "cases pass" in capsys.readouterr().out
