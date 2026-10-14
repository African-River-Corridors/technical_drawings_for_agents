"""The examples are drawing documents. They build, they match the goldens, and the key numbers are the ones that
the clauses of the standard give when worked by hand (see tests/goldens/README.md)."""

from __future__ import annotations

import importlib
import io
import json
import math
import re
from pathlib import Path

import pytest

from technical_drawings_for_agents.cli import main
from technical_drawings_for_agents.dfs import run_file
from technical_drawings_for_agents.dfs.pipeline import sort_key
from technical_drawings_for_agents.dfs.render_dxf import dxf_from_document
from technical_drawings_for_agents.dfs.render_pdf import pdf_from_normalised
from technical_drawings_for_agents.dfs.render_svg import svg_from_normalised

from .dxf_reader import read_normalised

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "drawings" / "example"
GOLDENS = Path(__file__).resolve().parent / "goldens"

DOCS = {
    "EXA-CIV-SEC-001": EXAMPLES / "simple-section" / "EXA-CIV-SEC-001.drawing.json",
    "SYN-PSK-PID-001": EXAMPLES / "synthetic-pid" / "SYN-PSK-PID-001.drawing.json",
}


def norm(number):
    result = run_file(DOCS[number])
    assert result["result"] == "ok", result
    return result["normalised"]


def entity(number, **match):
    found = [e for e in norm(number)["entities"] if all(e.get(k) == v for k, v in match.items())]
    assert len(found) == 1, (match, len(found))
    return found[0]


# --------------------------------------------------------------------------- #
# they build
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("number", DOCS)
def test_the_example_validates_and_is_a_drawing_document(number):
    assert main(["validate", str(DOCS[number])]) == 0
    document = json.loads(DOCS[number].read_text(encoding="utf-8"))
    assert document["standard_version"] == "1.0"


@pytest.mark.parametrize("number", DOCS)
def test_the_example_matches_its_goldens(number):
    n = norm(number)
    assert n == json.loads((GOLDENS / f"{number}.normalised.json").read_text(encoding="utf-8"))
    assert svg_from_normalised(n, embed_font=False) == (GOLDENS / f"{number}.svg").read_text(encoding="utf-8")


@pytest.mark.parametrize("number", DOCS)
def test_the_committed_outputs_are_what_a_build_writes(number, tmp_path):
    out_dir = DOCS[number].parent / "out"
    assert main(["build", str(DOCS[number]), "--out", str(tmp_path)]) == 0
    for suffix in (".normalised.json", ".svg"):
        assert (tmp_path / f"{number}{suffix}").read_bytes() == (out_dir / f"{number}{suffix}").read_bytes()
    for suffix in (".pdf", ".dxf"):
        assert (out_dir / f"{number}{suffix}").stat().st_size > 1000


@pytest.mark.parametrize("number", DOCS)
def test_the_dxf_of_the_example_reads_back_to_the_same_content(number):
    document = json.loads(DOCS[number].read_text(encoding="utf-8"))
    n = norm(number)
    text, warnings = dxf_from_document(document, n, DOCS[number].parent)
    assert warnings == []
    back = sorted(read_normalised(text)["entities"], key=sort_key)
    assert len(back) == len(n["entities"])
    from technical_drawings_for_agents.dfs.conformance import _same

    assert all(_same(a, b) for a, b in zip(back, sorted(n["entities"], key=sort_key)))


@pytest.mark.parametrize("number", DOCS)
def test_the_pdf_of_the_example_embeds_the_font(number):
    pypdf = pytest.importorskip("pypdf")
    reader = pypdf.PdfReader(io.BytesIO(pdf_from_normalised(norm(number))))
    font = reader.pages[0]["/Resources"]["/Font"]["/F1"]["/FontDescriptor"]
    assert "/FontFile2" in font


# --------------------------------------------------------------------------- #
# the key numbers, worked by hand
# --------------------------------------------------------------------------- #
# EXA-CIV-SEC-001: frame (20, 10, 320, 176), window centre (0, 0.6), scale 1:20 (50 mm per metre).
# paper x = 180 + 50 x, paper y = 128 - 50 y.


def test_section_the_lining_outline_is_where_the_map_puts_it():
    lining = entity("EXA-CIV-SEC-001", origin="viewport.main", id="lining")
    assert lining["points_mm"] == [[65, 78], [140, 138], [220, 138], [295, 78], [285, 78], [210, 128], [150, 128], [75, 78]]
    assert lining["closed"] is True
    assert (lining["width_mm"], lining["line_type"]) == (0.35, "continuous")  # OUTLINE is thick; A3 group 0.35


def test_section_the_bed_dimension_is_1_20_and_sits_18_mm_below_the_invert():
    parts = [e for e in norm("EXA-CIV-SEC-001")["entities"] if e["origin"] == "viewport.main.dimension"]
    texts = {e["text"]: e for e in parts if e["type"] == "text"}
    assert set(texts) == {"1.20", "1.00"}
    assert texts["1.20"]["anchor_mm"] == [180, 145.1]
    line = next(e for e in parts if e["type"] == "line" and e["from_mm"] == [150, 146])
    assert line["to_mm"] == [210, 146]


def test_section_the_depth_dimension_reads_upwards_on_the_right():
    text = next(e for e in norm("EXA-CIV-SEC-001")["entities"] if e["origin"] == "viewport.main.dimension" and e.get("text") == "1.00")
    assert text["rotation_deg"] == 90 and text["anchor_mm"] == [311.1, 103]


def test_section_the_scale_bar_is_one_metre_of_50_mm():
    bar = next(e for e in norm("EXA-CIV-SEC-001")["entities"] if e["origin"] == "viewport.main.scale_bar" and e["type"] == "polyline")
    assert bar["points_mm"] == [[26, 168], [76, 168], [76, 171], [26, 171]]
    labels = [e["text"] for e in norm("EXA-CIV-SEC-001")["entities"] if e["origin"] == "viewport.main.scale_bar" and e["type"] == "text"]
    assert sorted(labels) == sorted(["0", "0.25", "0.5", "0.75", "1 m", "1:20"])


def test_section_the_leader_to_the_water_level_is_right_aligned():
    parts = [e for e in norm("EXA-CIV-SEC-001")["entities"] if e["origin"] == "viewport.main.leader"]
    text = next(e for e in parts if e.get("text") == "DESIGN WL (NOMINAL)")
    assert text["align_h"] == "right" and text["anchor_mm"] == [86.6, 68]
    assert any(e["type"] == "line" and e["from_mm"] == [87.5, 68] and e["to_mm"] == [93.75, 90.5] for e in parts)


def test_section_the_title_block_and_register():
    n = norm("EXA-CIV-SEC-001")
    cells = {e["text"]: e for e in n["entities"] if e["origin"] == "sheet.title_block" and e["type"] == "text"}
    assert cells["CHANNEL TYPICAL SECTION"]["anchor_mm"] == [231, 250] and cells["CHANNEL TYPICAL SECTION"]["height_mm"] == 5
    assert cells["EXA-CIV-SEC-001"]["anchor_mm"] == [341, 244]
    assert cells["1:20"]["anchor_mm"] == [376, 272] and cells["CONCEPT"]["anchor_mm"] == [341, 272]
    register = {e["text"]: e for e in n["entities"] if e["origin"] == "sheet.revision_register" and e["type"] == "text"}
    assert register["A"]["anchor_mm"] == [231, 230] and register["2026-07-15"]["anchor_mm"] == [243, 230]
    assert "APPROVED BY" in cells and not any(t in cells for t in ("EF", "CD"))  # no approver was written by anyone


def test_section_the_watermark_and_the_notes_strip():
    n = norm("EXA-CIV-SEC-001")
    mark = next(e for e in n["entities"] if e["origin"] == "sheet.status_watermark")
    assert mark["text"] == "CONCEPT - NOT FOR CONSTRUCTION" and mark["anchor_mm"] == [215, 148.5]
    assert mark["rotation_deg"] == pytest.approx(math.degrees(math.atan2(277, 390)), abs=0.001)
    note = next(e for e in n["entities"] if e["origin"] == "sheet.notes" and e.get("text", "").startswith("1."))
    assert note["anchor_mm"] == [342, 20]


def test_section_the_concrete_hatch_makes_its_own_marks_and_draws_no_outline():
    n = norm("EXA-CIV-SEC-001")
    assert any(e["type"] == "hatch" and e["pattern"] == "concrete" for e in n["entities"])
    marks = [e for e in n["entities"] if e["origin"] == "viewport.main.hatch"]
    assert {e["type"] for e in marks} == {"circle", "polyline"} and len(marks) > 100


# SYN-PSK-PID-001: window centre (5.1, 2.3), scale 1:50 (20 mm per metre).
# paper x = 78 + 20 x, paper y = 144 - 20 y.


def test_pid_the_tank_symbol_is_12_by_34_mm_at_the_place_of_its_insert():
    tank = next(e for e in norm("SYN-PSK-PID-001")["entities"] if e["origin"] == "viewport.main.symbol" and e["type"] == "polyline" and e["points_mm"][0] == [94, 117])
    assert tank["points_mm"] == [[94, 117], [106, 117], [106, 83], [94, 83]] and tank["width_mm"] == 0.35


def test_pid_a_pipe_starts_and_ends_at_ports():
    spine = entity("SYN-PSK-PID-001", origin="viewport.main", id="L-SPINE")
    assert spine["points_mm"] == [[238, 100], [260, 100]]  # FCV-01 port e, BL-01 port conn
    assert spine["width_mm"] == 0.7  # PIPE is extra thick
    first = entity("SYN-PSK-PID-001", origin="viewport.main", id="L-S-A")
    assert first["points_mm"][0] == [106, 100]  # T-01 port e


def test_pid_a_label_stands_at_the_label_slot():
    label = next(e for e in norm("SYN-PSK-PID-001")["entities"] if e["origin"] == "viewport.main.symbol" and e.get("text") == "FCV-01")
    assert label["anchor_mm"] == [234, 105] and label["align_v"] == "top" and label["height_mm"] == 2.5


def test_pid_the_signal_lines_have_their_own_line_types():
    types = {e["id"]: e["line_type"] for e in norm("SYN-PSK-PID-001")["entities"] if e.get("id", "").startswith("L-F")}
    assert types == {"L-FE": "dashed", "L-FC": "chain"}


def test_pid_the_symbol_library_is_a_file_next_to_the_document():
    document = json.loads(DOCS["SYN-PSK-PID-001"].read_text(encoding="utf-8"))
    assert document["symbol_libraries"] == [{"id": "pid", "path": "library.json"}]
    assert (DOCS["SYN-PSK-PID-001"].parent / "library.json").is_file()


# --------------------------------------------------------------------------- #
# the clean break
# --------------------------------------------------------------------------- #


def test_no_source_py_route_remains():
    assert not list(ROOT.rglob("source.py")) or all(".venv" in str(p) or ".claude" in str(p) for p in ROOT.rglob("source.py"))
    package = ROOT / "src" / "technical_drawings_for_agents"
    names = {p.stem for p in package.glob("*.py")}
    assert names == {"__init__", "backdrop", "cli", "describe", "ingest"}  # the removed modules are gone


@pytest.mark.parametrize("name", ["render", "plot", "svg", "sheet", "validate", "titleblock", "dxf", "layers", "components", "legibility"])
def test_the_old_modules_cannot_be_imported(name):
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(f"technical_drawings_for_agents.{name}")


def test_the_cli_has_only_the_new_commands(capsys):
    with pytest.raises(SystemExit):
        main(["render", "x"])
    err = capsys.readouterr().err
    assert "invalid choice" in err


def test_the_readme_example_builds(tmp_path):
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    block = re.search(r"```json\n(.*?)\n```", text, re.DOTALL).group(1)
    path = tmp_path / "hello.drawing.json"
    path.write_text(block, encoding="utf-8")
    assert main(["build", str(path), "--out", str(tmp_path / "out")]) == 0
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == ["hello.dxf", "hello.normalised.json", "hello.pdf", "hello.svg"]
    n = json.loads((tmp_path / "out" / "hello.normalised.json").read_text())
    assert "8.00" in [e["text"] for e in n["entities"] if e["type"] == "text"]


def test_the_readme_mentions_no_removed_route():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    for word in ("render source", "ViewBox", "svg_hatch", "technical_drawings_for_agents plot"):
        assert word not in text
