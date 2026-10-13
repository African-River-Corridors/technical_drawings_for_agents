"""The stage-2 backend chain: no fallback past the end of it, and no silent degrade.

Acceptance tests T7, T13, T14, plus the page-spec surface. Every backend-dependent
test is ``skipif``-guarded with a reason, so ``pytest -q -rs`` shows a skip list
rather than a false green — a PR where T13 skips has not been tested.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from technical_drawings_for_agents import plot as plot_module
from technical_drawings_for_agents.plot import (
    PageSpec,
    PlotError,
    PlotRequest,
    find_svg_backend,
    plot,
)
from technical_drawings_for_agents.provenance import ProvenanceError, emit_digest
from technical_drawings_for_agents.render import find_soffice

from .synthetic import make_dxf_block_heavy

RSVG = shutil.which("rsvg-convert")
needs_rsvg = pytest.mark.skipif(RSVG is None, reason="rsvg-convert (librsvg) not installed")
needs_poppler_and_pillow = pytest.mark.skipif(
    shutil.which("pdftoppm") is None, reason="pdftoppm (poppler) not installed"
)


def _no_backends(monkeypatch) -> None:
    """Simulate a machine with neither rsvg-convert nor an importable cairosvg."""
    monkeypatch.delenv("RSVG_CONVERT_BIN", raising=False)
    monkeypatch.setattr(plot_module.shutil, "which", lambda _name: None)
    monkeypatch.setitem(sys.modules, "cairosvg", None)  # makes `import cairosvg` raise


# --------------------------------------------------------------------------- #
# T7
# --------------------------------------------------------------------------- #


def test_a_missing_svg_to_pdf_backend_gives_an_actionable_error(tmp_path, monkeypatch):
    """T7 — name both remedies, and be explicit that the SVG exists but the PDF does not."""
    src = make_dxf_block_heavy(tmp_path / "BLK-001.dxf")
    out = tmp_path / "out"
    _no_backends(monkeypatch)

    with pytest.raises(PlotError) as excinfo:
        plot(PlotRequest(source=src, out_dir=out))

    assert excinfo.value.kind == "backend"
    message = str(excinfo.value)
    for expected in ("brew install librsvg", "librsvg2-bin", "RSVG_CONVERT_BIN"):
        assert expected in message, message
    assert str(out / "BLK-001.svg") in message

    assert (out / "BLK-001.svg").exists()  # the sheet is still viewable in a browser
    assert list(out.glob("*.pdf")) == []
    assert list(out.glob("*.png")) == []


def test_a_missing_backend_exits_5_through_the_cli(tmp_path, monkeypatch):
    """T7, CLI half."""
    from technical_drawings_for_agents.cli import main

    src = make_dxf_block_heavy(tmp_path / "BLK-001.dxf")
    _no_backends(monkeypatch)
    assert main(["plot", str(src), "--out", str(tmp_path / "out")]) == 5


def test_an_explicit_rsvg_request_never_silently_tries_cairosvg(monkeypatch):
    """T7, second half — an explicit backend request is honoured or it fails."""
    monkeypatch.delenv("RSVG_CONVERT_BIN", raising=False)
    monkeypatch.setattr(plot_module.shutil, "which", lambda _name: None)
    monkeypatch.setitem(sys.modules, "cairosvg", object())  # "installed" and importable

    with pytest.raises(PlotError) as excinfo:
        find_svg_backend("rsvg")
    assert excinfo.value.kind == "backend"
    message = str(excinfo.value)
    assert "rsvg" in message
    assert "Not falling back to cairosvg" in message


def test_an_explicit_cairosvg_request_names_cairosvg_when_absent(monkeypatch):
    monkeypatch.setitem(sys.modules, "cairosvg", None)
    with pytest.raises(PlotError) as excinfo:
        find_svg_backend("cairosvg")
    assert excinfo.value.kind == "backend"
    assert "plot-cairo" in str(excinfo.value)


@needs_rsvg
def test_rsvg_convert_bin_is_honoured(tmp_path, monkeypatch):
    """Mirrors the SOFFICE_BIN / ODA_CONVERTER convention already in the toolkit."""
    monkeypatch.setattr(plot_module.shutil, "which", lambda _name: None)
    monkeypatch.setenv("RSVG_CONVERT_BIN", RSVG)
    name, handle = find_svg_backend("auto")
    assert (name, handle) == ("rsvg", RSVG)


@needs_rsvg
def test_a_nonzero_rsvg_return_code_is_never_recoverable(tmp_path, monkeypatch):
    """rc != 0 quotes stderr verbatim and fails; it is never treated as a warning."""
    out = tmp_path / "out"
    out.mkdir()
    broken = out / "broken.svg"
    broken.write_text("this is not SVG at all", encoding="utf-8")
    with pytest.raises(PlotError) as excinfo:
        plot(PlotRequest(source=broken, out_dir=out, stem="broken"))
    assert excinfo.value.kind == "backend"
    assert "rsvg-convert failed" in str(excinfo.value)
    assert list(out.glob("*.pdf")) == []


# --------------------------------------------------------------------------- #
# T13
# --------------------------------------------------------------------------- #


@needs_rsvg
def test_the_svg_stage_is_byte_deterministic(tmp_path):
    """T13a — ``SVGBackend.get_string`` is byte-identical across runs, unconditionally.

    Compared through P3's ``emit_digest``, which is the canonical identity for an
    emitted artifact (svg -> sha256 of the bytes).
    """
    src = make_dxf_block_heavy(tmp_path / "BLK-001.dxf")
    first = plot(PlotRequest(source=src, out_dir=tmp_path / "a", formats=("svg",)))
    second = plot(PlotRequest(source=src, out_dir=tmp_path / "b", formats=("svg",)))
    assert emit_digest(first.outputs[0]) == emit_digest(second.outputs[0])
    assert first.outputs[0].read_bytes() == second.outputs[0].read_bytes()


@needs_rsvg
def test_the_pdf_stage_is_byte_deterministic_under_a_resolved_source_date_epoch(
    tmp_path, monkeypatch
):
    """T13b — with the epoch pinned, two plots of one DXF give byte-identical PDFs.

    **The spec's §2.7 claim that ``rsvg-convert -f pdf`` has "no timestamp in
    output" is wrong**, and this test is written to the measurement rather than to
    the claim. Measured here on librsvg 2.62.3 / cairo 1.18.4: two conversions of a
    byte-identical SVG **1.6 s apart differ**; the spec's "identical sha256" came
    from two runs inside the same wall-clock second. Setting ``SOURCE_DATE_EPOCH``
    — which cairo honours — makes them byte-identical, which is why ``plot``
    forwards P3's resolved epoch into the converter's environment. The companion
    test below pins the *un*pinned case.
    """
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1700000000")
    src = make_dxf_block_heavy(tmp_path / "BLK-001.dxf")
    first = plot(PlotRequest(source=src, out_dir=tmp_path / "a", svg_backend="rsvg"))
    second = plot(PlotRequest(source=src, out_dir=tmp_path / "b", svg_backend="rsvg"))
    assert first.svg_backend == "rsvg" and second.svg_backend == "rsvg"
    pdfs = [
        next(path for path in result.outputs if path.suffix == ".pdf")
        for result in (first, second)
    ]
    assert pdfs[0].read_bytes() == pdfs[1].read_bytes()


@needs_rsvg
def test_an_unpinned_rsvg_pdf_carries_time_derived_bytes(tmp_path, monkeypatch):
    """The measurement behind the test above, pinned so the claim cannot silently rot.

    Without ``SOURCE_DATE_EPOCH``, cairo writes a time-derived document id, so two
    conversions of one SVG differ once the wall clock moves. Asserting *inequality*
    directly would need a sleep, so this asserts the mechanism instead: pinning two
    *different* epochs gives two different PDFs from identical input bytes.
    """
    src = make_dxf_block_heavy(tmp_path / "BLK-001.dxf")
    digests = []
    for index, epoch in enumerate(("1700000000", "1800000000")):
        monkeypatch.setenv("SOURCE_DATE_EPOCH", epoch)
        result = plot(
            PlotRequest(source=src, out_dir=tmp_path / f"e{index}", svg_backend="rsvg")
        )
        svg = next(path for path in result.outputs if path.suffix == ".svg")
        pdf = next(path for path in result.outputs if path.suffix == ".pdf")
        digests.append((svg.read_bytes(), pdf.read_bytes()))
    assert digests[0][0] == digests[1][0], "the SVG stage must not depend on the clock"
    assert digests[0][1] != digests[1][1], "the PDF stage embeds the resolved epoch"


def test_p3_refuses_to_hand_out_a_pdf_digest_by_default(tmp_path):
    """P3's interlock, asserted rather than assumed (``FORMAT_REPRODUCIBILITY['pdf']``).

    ``emit_digest`` **raises** for a format declared non-reproducible instead of
    returning a value a caller could wrongly assert byte-identity on. That is why
    the PDF assertions above compare bytes directly and say why in prose, rather
    than pretending a digest exists.
    """
    fake = tmp_path / "sheet.pdf"
    fake.write_bytes(b"%PDF-1.7\n")
    with pytest.raises(ProvenanceError):
        emit_digest(fake)


@pytest.mark.skipif(find_soffice() is None, reason="LibreOffice (soffice) not installed")
def test_libreoffice_pdfs_differ_between_two_identical_runs(tmp_path):
    """Documenting, not asserting-good: why LibreOffice is excluded from the plot path.

    Spec §2.3/§4.1. The legacy backend embeds ``/CreationDate`` and a random
    ``/ID`` and ignores ``SOURCE_DATE_EPOCH``, so it can never sit on a
    byte-reproducible path. Keeping the reason *in the suite* means it cannot be
    lost when the prose is rewritten.
    """
    soffice = find_soffice()
    src = make_dxf_block_heavy(tmp_path / "BLK-001.dxf")
    digests = []
    for index in range(2):
        out = tmp_path / f"lo{index}"
        out.mkdir()
        with tempfile.TemporaryDirectory() as profile:
            subprocess.run(
                [
                    soffice, "--headless", "--nologo", "--nofirststartwizard",
                    f"-env:UserInstallation=file://{profile}",
                    "--convert-to", "pdf", "--outdir", str(out), str(src),
                ],
                check=True,
                capture_output=True,
                timeout=180,
                env={**os.environ, "SOURCE_DATE_EPOCH": "1700000000"},
            )
        digests.append((out / "BLK-001.pdf").read_bytes())
    assert digests[0] != digests[1], (
        "LibreOffice produced byte-identical PDFs — if this ever becomes true, "
        "revisit spec §4.1's determinism argument"
    )


# --------------------------------------------------------------------------- #
# T14
# --------------------------------------------------------------------------- #


_BLANK_SVG = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<svg xmlns="http://www.w3.org/2000/svg" width="200mm" height="100mm" '
    'viewBox="0 0 200 100"><rect width="200" height="100" fill="#ffffff"/></svg>\n'
)


@needs_rsvg
@needs_poppler_and_pillow
def test_the_ink_check_catches_a_blank_raster(tmp_path):
    """T14 — a pre-raster check cannot prove the converter honoured the geometry."""
    pytest.importorskip("PIL", reason="Pillow not installed")
    out = tmp_path / "out"
    out.mkdir()
    blank = out / "BLANK-001.svg"
    blank.write_text(_BLANK_SVG, encoding="utf-8")

    with pytest.raises(PlotError) as excinfo:
        plot(PlotRequest(source=blank, out_dir=out, fidelity="off", ink_check=True))
    assert excinfo.value.kind == "fidelity"
    assert "plotted page 1 of 1 contains no ink" in str(excinfo.value)
    assert list(out.glob("*.pdf")) == []  # never promoted to a canonical name


@needs_rsvg
def test_ink_check_without_its_tools_is_a_backend_error(tmp_path, monkeypatch):
    """T14, second half — an explicitly requested check is never silently skipped."""
    out = tmp_path / "out"
    out.mkdir()
    blank = out / "BLANK-001.svg"
    blank.write_text(_BLANK_SVG, encoding="utf-8")
    real_which = shutil.which
    monkeypatch.setattr(
        plot_module.shutil,
        "which",
        lambda name: None if name == "pdftoppm" else real_which(name),
    )
    with pytest.raises(PlotError) as excinfo:
        plot(PlotRequest(source=blank, out_dir=out, fidelity="off", ink_check=True))
    assert excinfo.value.kind == "backend"
    assert "pdftoppm" in str(excinfo.value)


@needs_rsvg
@needs_poppler_and_pillow
def test_the_ink_check_passes_a_real_sheet(tmp_path):
    pytest.importorskip("PIL", reason="Pillow not installed")
    src = make_dxf_block_heavy(tmp_path / "BLK-001.dxf")
    result = plot(PlotRequest(source=src, out_dir=tmp_path / "out", ink_check=True))
    assert (tmp_path / "out" / "BLK-001.pdf").exists()
    assert result.fidelity is not None and result.fidelity.ok


# --------------------------------------------------------------------------- #
# page spec
# --------------------------------------------------------------------------- #


def test_page_specs_parse_and_orient():
    assert PageSpec.parse("ISO A3").size_mm() == (420.0, 297.0)
    assert PageSpec.parse("iso a3", landscape=False).size_mm() == (297.0, 420.0)
    assert PageSpec.parse("420x297").name == "custom"
    assert PageSpec.parse("custom:200x100").size_mm() == (200.0, 100.0)
    assert PageSpec.parse("ANSI D").name == "ANSI D"


def test_an_unknown_page_spec_is_an_unsupported_error():
    with pytest.raises(PlotError) as excinfo:
        PageSpec.parse("ISO A9")
    assert excinfo.value.kind == "unsupported"
    assert "ISO A3" in str(excinfo.value)


def test_a_margin_that_swallows_the_sheet_is_rejected():
    with pytest.raises(PlotError) as excinfo:
        PageSpec(name="custom", width_mm=20, height_mm=10, margin_mm=6).to_page()
    assert excinfo.value.kind == "unsupported"
    assert "no drawable area" in str(excinfo.value)


def test_an_unsupported_source_type_is_an_unsupported_error(tmp_path):
    bogus = tmp_path / "sheet.dwg"
    bogus.write_bytes(b"not a dxf")
    with pytest.raises(PlotError) as excinfo:
        plot(PlotRequest(source=bogus, out_dir=tmp_path / "out"))
    assert excinfo.value.kind == "unsupported"
    assert excinfo.value.exit_code == 6


def test_every_new_exit_code_is_at_least_three():
    """0/1/2 keep their meaning for every script that already checks them."""
    from technical_drawings_for_agents.plot import EXIT_CODES, PLOT_ERROR_KINDS

    assert set(EXIT_CODES) == set(PLOT_ERROR_KINDS)
    new = {kind: code for kind, code in EXIT_CODES.items() if kind != "input"}
    assert all(code >= 3 for code in new.values()), new
    assert EXIT_CODES["input"] == 2  # matches cli.py's existing bad-input convention
    assert sorted(new.values()) == [3, 4, 5, 6]


def test_a_plot_error_kind_outside_the_closed_set_is_a_programming_error():
    with pytest.raises(ValueError):
        PlotError("nope", kind="mystery")


@needs_rsvg
def test_a_page_spec_on_an_svg_source_is_warned_about_not_silently_ignored(
    tmp_path, caplog
):
    """Stage 1 (and ``layout.Page``) does not run for an SVG source — the SVG is the sheet.

    ``--page`` therefore has no effect there. A silently ignored flag is the same
    class of defect as a silently dropped entity, so it is logged.
    """
    out = tmp_path / "out"
    out.mkdir()
    sheet = out / "S-001.svg"
    sheet.write_text(_BLANK_SVG, encoding="utf-8")
    with caplog.at_level("WARNING", logger="technical_drawings_for_agents.plot"):
        plot(PlotRequest(source=sheet, out_dir=out, page=PageSpec.parse("ISO A1")))
    assert any("does not apply to the SVG source" in r.getMessage() for r in caplog.records)

    caplog.clear()
    with caplog.at_level("WARNING", logger="technical_drawings_for_agents.plot"):
        plot(PlotRequest(source=sheet, out_dir=out))
    assert not [r for r in caplog.records if "--page" in r.getMessage()]


def test_svg_only_output_needs_no_conversion_backend(tmp_path, monkeypatch):
    """``--format svg`` is a real answer on a machine with no converter at all."""
    src = make_dxf_block_heavy(tmp_path / "BLK-001.dxf")
    _no_backends(monkeypatch)
    result = plot(PlotRequest(source=src, out_dir=tmp_path / "out", formats=("svg",)))
    assert result.svg_backend == "none"
    assert [Path(path).suffix for path in result.outputs] == [".svg"]
