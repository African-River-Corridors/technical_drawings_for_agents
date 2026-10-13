"""Render backend routing: INSERT drawings -> LibreOffice, else matplotlib."""

from __future__ import annotations

import ezdxf
import pytest

from technical_drawings_for_agents.render import (
    dxf_has_inserts,
    find_soffice,
    render_dxf,
    select_backend,
)

from .synthetic import make_dxf_no_insert, make_dxf_with_insert

SOFFICE = find_soffice()
needs_soffice = pytest.mark.skipif(SOFFICE is None, reason="LibreOffice (soffice) not installed")


def test_select_backend_pure_logic():
    # INSERTs present + LibreOffice available -> LibreOffice.
    assert select_backend(has_inserts=True, soffice_available=True) == "libreoffice"
    # INSERTs present but no LibreOffice -> degrade to matplotlib.
    assert select_backend(has_inserts=True, soffice_available=False) == "matplotlib"
    # No INSERTs -> matplotlib regardless.
    assert select_backend(has_inserts=False, soffice_available=True) == "matplotlib"
    assert select_backend(has_inserts=False, soffice_available=False) == "matplotlib"


def test_dxf_has_inserts_detection(tmp_path):
    ins = make_dxf_with_insert(tmp_path / "ins.dxf")
    noins = make_dxf_no_insert(tmp_path / "plain.dxf")
    assert dxf_has_inserts(ezdxf.readfile(ins)) is True
    assert dxf_has_inserts(ezdxf.readfile(noins)) is False


def test_no_insert_renders_via_matplotlib(tmp_path):
    # No INSERTs -> matplotlib path; must produce PDF + PNG without LibreOffice.
    src = make_dxf_no_insert(tmp_path / "plain.dxf")
    out = render_dxf(src, tmp_path / "out")
    exts = {p.suffix for p in out}
    assert exts == {".pdf", ".png"}
    assert all(p.exists() and p.stat().st_size > 0 for p in out)


def test_forced_matplotlib_backend(tmp_path):
    # Even with INSERTs, backend='matplotlib' must render (no soffice needed).
    src = make_dxf_with_insert(tmp_path / "ins.dxf")
    out = render_dxf(src, tmp_path / "out", backend="matplotlib")
    assert {p.suffix for p in out} == {".pdf", ".png"}
    assert all(p.exists() for p in out)


@needs_soffice
def test_insert_renders_via_libreoffice(tmp_path):
    # INSERTs present -> auto routes to LibreOffice; produces PDF + PNG.
    src = make_dxf_with_insert(tmp_path / "ins.dxf")
    out = render_dxf(src, tmp_path / "out")  # auto
    exts = {p.suffix for p in out}
    assert exts == {".pdf", ".png"}
    assert all(p.exists() and p.stat().st_size > 0 for p in out)
