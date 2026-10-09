"""Unit tests for the core technical_drawings_for_agents toolkit."""

from __future__ import annotations

import pytest

from technical_drawings_for_agents import (
    Drawing,
    DrawingMeta,
    DxfBuilder,
    ViewBox,
    svg_scale_bar,
    svg_status_watermark,
    svg_title_block,
)
from technical_drawings_for_agents.style import STATUS_WATERMARKS


def test_viewbox_scale_true_and_inverted_y():
    vb = ViewBox(0, 10, 0, 5, 400, 300, padding=50)
    # Origin maps to bottom-left inside the padding; +Y is up (inverted SVG Y).
    assert vb.x(0) == pytest.approx(50)
    assert vb.y(0) == pytest.approx(250)
    assert vb.y(5) < vb.y(0)
    # Equal metres map to equal pixels in x and y (uniform scale).
    assert vb.length(1) == pytest.approx(vb.scale)


def test_status_watermark_text_and_marker():
    svg = svg_status_watermark(800, 600, "CONCEPT")
    assert "CONCEPT" in svg
    assert 'class="status-watermark"' in svg
    with pytest.raises(ValueError):
        svg_status_watermark(800, 600, "BOGUS")


def test_scale_bar_and_title_block_markers():
    vb = ViewBox(0, 10, 0, 5, 400, 300)
    assert 'class="scale-bar"' in svg_scale_bar(vb, 0, 0, 5)
    assert 'class="title-block"' in svg_title_block(400, 300, title="X")


def test_drawing_render_carries_required_furniture():
    vb = ViewBox(0, 10, 0, 5, 600, 400)
    dwg = Drawing(600, 400, vb, title_block={"title": "T"}, status="CONCEPT")
    dwg.add(svg_scale_bar(vb, 0, 0, 5))
    out = dwg.render()
    assert out.startswith("<svg")
    for marker in ('class="title-block"', 'class="scale-bar"', 'class="status-watermark"'):
        assert marker in out


def test_meta_for_construction_gate():
    ok = DrawingMeta(number="X-1", title="t", status="ISSUED", for_construction=True)
    assert ok.validate() == []
    bad = DrawingMeta(number="X-1", title="t", status="CONCEPT", for_construction=True)
    problems = bad.validate()
    assert any("ISSUED" in p for p in problems)


def test_meta_requires_number_and_title():
    problems = DrawingMeta(number="", title="").validate()
    assert any("number" in p for p in problems)
    assert any("title" in p for p in problems)


def test_all_status_watermarks_defined():
    for key in ("DRAFT", "CONCEPT", "ISSUED"):
        assert key in STATUS_WATERMARKS
        assert STATUS_WATERMARKS[key]["text"]


def test_dxf_builder_writes_real_metres(tmp_path):
    dxf = DxfBuilder(units="m")
    dxf.line((0, 0), (1.2, 0)).polyline([(0, 0), (1, 0), (1, 1)], closed=True)
    dxf.linear_dim((0, 0), (1.2, 0), distance=-0.4)
    out = dxf.save(tmp_path / "t.dxf")
    assert out.exists()
    import ezdxf

    doc = ezdxf.readfile(out)
    assert doc.header["$INSUNITS"] == 6  # metres
    lines = doc.modelspace().query("LINE")
    assert len(lines) == 1
    assert lines[0].dxf.end.x == pytest.approx(1.2)
