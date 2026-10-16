"""Standard 1.1: the image entity (clause 9.10) and the colour print profile (clauses 8.6.4 to 8.6.6), in the outputs."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest
from pypdf import PdfReader

from technical_drawings_for_agents.cli import main
from technical_drawings_for_agents.dfs import resources
from technical_drawings_for_agents.dfs.image import jpeg_info
from technical_drawings_for_agents.dfs.pipeline import run_file

CASE = resources.suite_dir() / "documents" / "img-colour-basic"


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("img")
    assert main(["build", str(CASE / "input.json"), "--out", str(out)]) == 0
    return out


def test_jpeg_frame_header_gives_size_and_components():
    data = (CASE / "backdrop.jpg").read_bytes()
    info = jpeg_info(data)
    assert (info.width_px, info.height_px, info.components) == (4, 2, 3)
    assert jpeg_info(b"GIF89a") is None
    assert jpeg_info(data[:20]) is None  # cut before the frame header


def test_pdf_holds_the_jpeg_bytes_as_they_are(built):
    data = (CASE / "backdrop.jpg").read_bytes()
    pdf = (built / "input.pdf").read_bytes()
    assert data in pdf  # DCTDecode pass-through (15.3)
    page = PdfReader(str(built / "input.pdf")).pages[0]
    xobjects = page["/Resources"]["/XObject"]
    assert len(xobjects) == 1
    im = next(iter(xobjects.values())).get_object()
    assert (im["/Width"], im["/Height"], im["/Filter"]) == (4, 2, "/DCTDecode")


def test_pdf_draws_the_image_first_and_uses_the_colours(built):
    content = PdfReader(str(built / "input.pdf")).pages[0].get_contents().get_data().decode("latin-1")
    first_draw = min(content.index(" Do "), content.index(" S\n") if " S\n" in content else 10**9)
    assert content.index(" Do ") == first_draw  # 9.10.7
    assert "1 0 0 RG 1 0 0 rg" in content  # ROUTE, #FF0000
    assert re.search(r"/GS\d gs", content)  # fill opacity 0.4 and image opacity 0.8
    assert "1 Tr" in content  # the halo stroke of the text (8.6.6)


def test_svg_embeds_the_image_under_everything_and_colours_the_route(built):
    svg = (built / "input.svg").read_text()
    body = svg[svg.index("</style>"):]
    assert body.index("<image ") < body.index("<line ")
    assert 'stroke="#FF0000"' in svg and 'fill-opacity="0.4"' in svg and 'paint-order="stroke"' in svg
    assert 'opacity="0.8"' in svg


def test_furniture_stays_black_under_the_colour_profile():
    norm = run_file(CASE / "input.json")["normalised"]
    for e in norm["entities"]:
        if not e["origin"].startswith("viewport.") or e["origin"].endswith((".scale_bar", ".north_arrow")):
            assert not {"print_colour", "fill_opacity", "halo_colour"} & set(e), e


def test_the_hash_ties_the_drawing_to_its_file():
    doc = json.loads((CASE / "input.json").read_text())
    image = doc["viewports"][0]["entities"][0]
    assert image["sha256"] == hashlib.sha256((CASE / image["path"]).read_bytes()).hexdigest()


def test_a_monochrome_document_has_no_colour_or_image_in_its_pdf(tmp_path):
    case = resources.suite_dir() / "documents" / "ent-line"
    assert main(["build", str(case / "input.json"), "--out", str(tmp_path)]) == 0
    pdf = (tmp_path / "input.pdf").read_bytes()
    assert b"/XObject" not in pdf and b"/ExtGState" not in pdf


def test_dxf_links_the_image_and_reads_back(built):
    text = (built / "input.dxf").read_text()
    assert "IMAGEDEF" in text and "backdrop.jpg" in text
    assert Path(built / "input.dxf").exists()
