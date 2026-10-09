"""Smoke tests for the block-flow / profile generator."""
import shutil

import pytest

from technical_drawings_for_agents import bfd

DATA = {
    "meta": {"number": "TST-BFD-001", "title": "Test BFD", "status": "DRAFT", "wrap": 3},
    "sheet": [1500, 950],
    "nodes": [
        {"id": "src", "label": "Source", "type": "source", "elev": 10, "chainage": 0},
        {"id": "p1", "label": "Pond 1", "type": "pond", "elev": 20, "chainage": 500},
        {"id": "u1", "label": "Unit", "type": "process"},
        {"id": "p2", "label": "Pond 2", "type": "pond", "elev": 40, "chainage": 2000},
        {"id": "snk", "label": "Sink", "type": "sink", "elev": 55, "chainage": 3000},
        {"id": "dose", "label": "Dose", "type": "dose"},
    ],
    "edges": [
        {"from": "src", "to": "p1"},
        {"from": "p1", "to": "u1"},
        {"from": "u1", "to": "p2"},
        {"from": "p2", "to": "snk"},
        {"from": "dose", "to": "u1", "kind": "dose"},
    ],
    "notes": ["a note"],
}


def test_spine_order():
    assert bfd._spine(DATA) == ["src", "p1", "u1", "p2", "snk"]


def test_to_dot_wraps_and_escapes():
    dot = bfd.to_dot(DATA)
    assert "rankdir=TB" in dot  # wrap -> top-to-bottom serpentine
    assert "rank=same" in dot
    assert dot.count("->") >= 5


@pytest.mark.skipif(not shutil.which("dot"), reason="graphviz not installed")
def test_build_block_produces_svg(tmp_path):
    out = bfd.build_block(DATA, tmp_path, "TST-BFD-001")
    svg = out.read_text(encoding="utf-8")
    assert svg.startswith("<svg")
    assert "Test BFD" in svg
    assert "LEGEND" in svg and "NOTES" in svg
    # well-formed XML (no bare & etc.)
    import xml.dom.minidom as m

    m.parseString(svg)


def test_profile_view(tmp_path):
    outs = bfd.build(_write_yaml(tmp_path), tmp_path / "out", view="profile")
    assert outs and outs[0].exists()
    txt = outs[0].read_text(encoding="utf-8")
    assert "HYDRAULIC LONG-SECTION" in txt


def _write_yaml(tmp_path):
    import yaml

    p = tmp_path / "d.yaml"
    p.write_text(yaml.safe_dump(DATA), encoding="utf-8")
    return p
