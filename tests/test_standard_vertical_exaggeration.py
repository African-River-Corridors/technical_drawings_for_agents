"""Standard 1.2: the vertical exaggeration of a viewport (clauses 7.3.6, 7.9.11, 15.4.13, 16.6.5), in the outputs."""

from __future__ import annotations

import copy
import json

import pytest

from technical_drawings_for_agents.cli import main
from technical_drawings_for_agents.dfs import resources
from technical_drawings_for_agents.dfs.pipeline import run_document, run_file
from technical_drawings_for_agents.dfs.rules import (
    ALL_RATIOS_1_1,
    Mapper,
    exaggeration_words,
    text_box,
)

from .dxf_reader import records

CASES = resources.suite_dir() / "documents"
CASE = CASES / "exaggeration-long-section"


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("ve")
    assert main(["build", str(CASE / "input.json"), "--out", str(out)]) == 0
    return out


def test_without_an_exaggeration_the_map_is_the_one_of_version_1_1():
    """18.2.2: E = 1 gives k_v = k, bit for bit, so no 1.0 or 1.1 point moves."""
    frame, window = (20, 10, 390, 277), {"xmin_m": 0, "ymin_m": 0, "xmax_m": 100, "ymax_m": 50}
    for n in (1, 25, 333, 1250, 100000):
        m = Mapper(frame, window, n, 0)
        assert m.kv == m.k == 1000 / n


def test_the_words_are_in_full_and_the_font_has_the_multiplication_sign():
    assert exaggeration_words(10) == "Vertical exaggeration ×10"
    assert "×" in resources.font_widths()["advance"]  # 11.6.2


def test_a_1_1_document_has_no_new_member_and_no_vertical_bar():
    norm = run_file(CASES / "exaggeration-absent-1-1" / "input.json")["normalised"]
    assert all("vertical_exaggeration" not in vp for vp in norm["viewports"])
    assert not [e for e in norm["entities"] if e["origin"].endswith(".vertical_scale_bar")]


def test_svg_prints_the_words_and_both_ratios(built):
    svg = (built / "input.svg").read_text(encoding="utf-8")
    assert "Vertical exaggeration ×10" in svg
    assert "Vertical 1:500" in svg and "1:5000" in svg
    assert "V.E." not in svg


def test_dxf_writes_the_exaggerated_viewport_in_paper_space(built):
    """15.4.13: nothing of the viewport in model space; its entities in paper space with their origin."""
    recs = records((built / "input.dxf").read_text())
    model = [r for r in recs if r.section == "ENTITIES" and r.get(67) != "1" and r.xdata().get("vp") == "main"]
    assert model == []
    paper = {r.xdata().get("origin") for r in recs if r.xdata().get("origin")}
    assert {"viewport.main", "viewport.main.vertical_scale_bar"} <= paper
    vp = next(r for r in recs if r.kind == "VIEWPORT" and r.xdata().get("id") == "main")
    assert vp.xdata()["vertical_exaggeration"] == "10" and vp.xdata()["scale"] == "5000"


def _box(e):
    """The paper box of a furniture entity: (x0, y0, x1, y1)."""
    if e["type"] == "text":
        pts = text_box(e["anchor_mm"], e["text"], e["height_mm"], e["rotation_deg"], e["align_h"], e["align_v"])
    elif e["type"] == "line":
        pts = [e["from_mm"], e["to_mm"]]
    elif e["type"] == "polyline":
        pts = e["points_mm"]
    else:
        pts = e["boundary_mm"]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def _overlap(a, b) -> bool:
    return min(a[2], b[2]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[1], b[1])


def _document(height_mm, scale, ex):
    doc = json.loads((CASE / "input.json").read_text(encoding="utf-8"))
    vp = doc["viewports"][0]
    vp["frame"]["height_mm"] = height_mm
    vp["scale"], vp["vertical_exaggeration"], vp["entities"] = scale, ex, []
    vp["window"] = {"xmin_m": 0, "ymin_m": 0, "xmax_m": 0.01, "ymax_m": 0.01}
    return doc


PAIRS = [(n, e) for n in ALL_RATIOS_1_1 for e in (2, 4, 5, 10, 20, 50, 100, 500, 1000) if n % e == 0 and n // e in ALL_RATIOS_1_1]


@pytest.mark.parametrize("height_mm", [100, 137, 200, 277])
def test_the_vertical_bar_never_touches_the_scale_bar(height_mm):
    """7.9.11: for every frame height of 7.9.12 and every pair of ratios, the boxes are apart."""
    for n, e in PAIRS:
        result = run_document(copy.deepcopy(_document(height_mm, n, e)))
        assert result["result"] == "ok", (n, e, result)
        ents = result["normalised"]["entities"]
        vertical = [_box(x) for x in ents if x["origin"].endswith(".vertical_scale_bar") and not x.get("text", "").startswith("Vertical exag")]
        words = [_box(x) for x in ents if x.get("text", "").startswith("Vertical exag")]
        others = [_box(x) for x in ents if x["origin"].endswith(".scale_bar")]
        assert vertical and words and others
        for v in vertical + words:
            assert not any(_overlap(v, o) for o in others), (height_mm, n, e, v)
        bar_top = min(b[1] for b in vertical if b[2] - b[0] == 3)  # the outline of the vertical bar
        assert bar_top >= 10 + 37 - 1e-9  # clause 7.9.11: the top end is at least 37 mm below the frame top


def test_a_dimension_in_an_exaggerated_viewport_states_the_model_length():
    norm = run_file(CASES / "exaggeration-dimension-leader" / "input.json")["normalised"]
    texts = [e["text"] for e in norm["entities"] if e["origin"] == "viewport.main.dimension" and e["type"] == "text"]
    assert texts == ["1200.00"]


@pytest.mark.parametrize("bearing", [0, 90, 30])
def test_an_exaggerated_viewport_has_no_north_arrow_at_any_bearing(bearing):
    """7.3.6.9: a section has no north, and an exaggerated plan distorts every bearing."""
    doc = _document(200, 5000, 10)
    doc["viewports"][0]["north"] = {"model_bearing_deg": bearing}
    assert run_document(doc) == {"result": "error", "errors": [{"clause": "7.3.6.9"}]}
