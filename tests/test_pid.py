"""Tests for the P&ID generator: data model, routing, build, and the example."""

from __future__ import annotations

import shutil
import xml.dom.minidom as minidom
from pathlib import Path

import pytest
import yaml

from technical_drawings_for_agents import pid
from technical_drawings_for_agents.render import find_soffice, render_source
from technical_drawings_for_agents.validate import validate_drawing_dir, validate_pid_data

EXAMPLE = Path(__file__).resolve().parents[1] / "drawings" / "example" / "synthetic-pid"
DATA_FILE = EXAMPLE / "SYN-PSK-PID-001.pid.yaml"


def _mini():
    return {
        "meta": {"number": "TST-PID-001", "title": "Mini", "watermark": "CONCEPT"},
        "canvas": [400, 240],
        "equipment": [
            {"id": "T-1", "type": "tank", "tag": "=T +A -1", "at": [70, 120], "w": 40, "h": 90},
            {"id": "P-1", "type": "pump", "tag": "=P +A -1", "at": [200, 120]},
            {"id": "BL-1", "type": "tie_in", "tag": "=B +A -1", "at": [330, 120], "label": "X"},
        ],
        "instruments": [
            {"id": "PT-1", "type": "instrument", "tag": "PT-101", "mount": "field", "at": [200, 40]},
        ],
        "lines": [
            {"id": "L1", "type": "process", "from": "T-1.e", "to": "P-1.suction"},
            {"id": "L2", "type": "process", "from": "P-1.discharge", "to": "BL-1.conn"},
            {"id": "L3", "type": "signal", "from": "PT-1.s", "to": "P-1.n"},
        ],
        "loops": [{"id": "PIC-1", "members": ["PT-1"]}],
        "notes": ["synthetic"],
    }


# ---------------------------------------------------------------- data model
def test_valid_data_has_no_problems():
    assert pid.validate_data(_mini()) == []


def test_missing_tag_flagged():
    d = _mini()
    del d["equipment"][1]["tag"]
    probs = pid.validate_data(d)
    assert any("P-1" in p and "tag" in p for p in probs)


def test_unknown_line_type_flagged():
    d = _mini()
    d["lines"][0]["type"] = "telepathy"
    assert any("unknown type" in p for p in pid.validate_data(d))


def test_unresolved_port_flagged():
    d = _mini()
    d["lines"][0]["to"] = "P-1.does_not_exist"
    assert any("no port" in p for p in pid.validate_data(d))

    d2 = _mini()
    d2["lines"][0]["to"] = "NOPE.w"
    assert any("unknown symbol" in p for p in pid.validate_data(d2))


def test_loop_unknown_member_flagged():
    d = _mini()
    d["loops"][0]["members"] = ["ghost"]
    assert any("ghost" in p for p in pid.validate_data(d))


def test_duplicate_id_raises_on_place():
    d = _mini()
    d["equipment"].append({"id": "P-1", "type": "pump", "tag": "x", "at": [10, 10]})
    assert any("duplicate" in p for p in pid.validate_data(d))


# ------------------------------------------------------------------- routing
def _orthogonal(points):
    return all(
        abs(a[0] - b[0]) < 0.5 or abs(a[1] - b[1]) < 0.5
        for a, b in zip(points, points[1:])
    )


def test_route_is_orthogonal():
    pts = pid.route((0, 0), "E", (100, 60), "W")
    assert _orthogonal(pts)
    assert pts[0] == (0, 0) and pts[-1] == (100, 60)


def test_route_honours_waypoints():
    pts = pid.route((0, 0), "E", (100, 100), "W", waypoints=[[50, 0], [50, 100]])
    assert (50, 0) in pts and (50, 100) in pts
    assert _orthogonal(pts)


def test_route_straight_when_aligned():
    pts = pid.route((0, 50), "E", (100, 50), "W")
    assert _orthogonal(pts)
    assert all(abs(p[1] - 50) < 0.5 for p in pts)


# --------------------------------------------------------------------- build
def test_build_produces_wellformed_sheet(tmp_path):
    data = _mini()
    src = tmp_path / "m.pid.yaml"
    src.write_text(yaml.safe_dump(data), encoding="utf-8")
    outs = pid.build(src, tmp_path / "out")
    assert outs and outs[0].exists()
    svg = outs[0].read_text(encoding="utf-8")
    minidom.parseString(svg)  # well-formed XML
    assert 'class="title-block"' in svg
    assert 'class="status-watermark"' in svg
    assert "CONCEPT" in svg


def test_build_rejects_invalid_data(tmp_path):
    data = _mini()
    del data["equipment"][1]["tag"]
    src = tmp_path / "bad.pid.yaml"
    src.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(ValueError):
        pid.build(src, tmp_path / "out")


# ------------------------------------------------------- committed example
def test_example_data_validates():
    result = validate_pid_data(DATA_FILE)
    assert result.ok, result.problems


def test_example_builds_renders_and_validates(tmp_path):
    work = tmp_path / "synthetic-pid"
    shutil.copytree(EXAMPLE, work)
    shutil.rmtree(work / "out", ignore_errors=True)

    data_file = work / DATA_FILE.name
    outs = pid.build(data_file, work / "out")
    svg = outs[0]
    assert svg.exists() and svg.suffix == ".svg"

    # The drawing directory (meta.yaml + *.pid.yaml + out/) validates.
    result = validate_drawing_dir(work)
    assert result.ok, result.problems

    # PDF/PNG round-trip via whatever rasteriser is available (soffice on CI).
    try:
        import cairosvg  # type: ignore  # noqa: F401
        has_raster = True
    except Exception:
        has_raster = find_soffice() is not None
    if not has_raster:
        pytest.skip("no SVG rasteriser (cairosvg / LibreOffice) available")
    rendered = render_source(svg, work / "out")
    exts = {p.suffix for p in rendered}
    assert ".pdf" in exts and ".png" in exts
