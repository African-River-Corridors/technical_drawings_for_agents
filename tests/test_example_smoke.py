"""Smoke test: build the worked example end-to-end and validate it."""

from __future__ import annotations

import shutil
from pathlib import Path

from technical_drawings_for_agents import Drawing, ViewBox, svg_scale_bar
from technical_drawings_for_agents.render import render_source
from technical_drawings_for_agents.validate import validate_drawing_dir

EXAMPLE = Path(__file__).resolve().parents[1] / "drawings" / "example" / "simple-section"


def test_example_builds_and_validates(tmp_path):
    # Copy the example into a temp dir so the test never dirties the repo.
    work = tmp_path / "simple-section"
    shutil.copytree(EXAMPLE, work)
    shutil.rmtree(work / "out", ignore_errors=True)

    outputs = render_source(work / "source.py", work / "out")
    # PDF + PNG produced from the generated DXF.
    exts = {p.suffix for p in outputs}
    assert ".pdf" in exts and ".png" in exts

    out = work / "out"
    assert (out / "EXA-CIV-SEC-001.svg").exists()
    assert (out / "EXA-CIV-SEC-001.dxf").exists()

    # SVG carries the concept watermark and the never-invent provenance holds.
    svg_text = (out / "EXA-CIV-SEC-001.svg").read_text(encoding="utf-8")
    assert "CONCEPT" in svg_text

    result = validate_drawing_dir(work)
    assert result.ok, result.problems


def test_legacy_sheets_render_byte_identically(tmp_path):
    """Backward compatibility, proved rather than promised (spec §6 test 1).

    The paper-space sheet model (``technical_drawings_for_agents.sheet``) does not touch ``svg.py`` or
    ``isosheet.py`` at all, so no existing sheet can change by a single byte. This test
    is the guard: re-render the shipped example and compare against the committed golden
    SVG, then assert the two legacy surfaces the new model deliberately did not remove —
    the free ``label=`` caption slot on ``svg_scale_bar`` (legitimate as a caption; only
    the *derived* bar refuses it) and the pixel ``Drawing`` root element.
    """
    work = tmp_path / "simple-section"
    shutil.copytree(EXAMPLE, work)
    shutil.rmtree(work / "out", ignore_errors=True)

    render_source(work / "source.py", work / "out")

    produced = (work / "out" / "EXA-CIV-SEC-001.svg").read_bytes()
    golden = (EXAMPLE / "out" / "EXA-CIV-SEC-001.svg").read_bytes()
    assert produced == golden

    assert ">Scale (concept)<" in svg_scale_bar(
        ViewBox(0, 10, 0, 5, 400, 300), 0, 0, 5, label="Scale (concept)"
    )
    assert Drawing(600, 400, ViewBox(0, 10, 0, 5, 600, 400)).render().startswith(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 600 400" '
        'width="600" height="400"'
    )
