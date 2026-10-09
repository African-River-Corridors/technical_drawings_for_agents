"""The fidelity check: a plot is complete, or the canonically-named file does not exist.

Acceptance tests T2, T3, T4, T5, T6 of ``docs/specs/P4-pdf-plot-path-fidelity.md``,
plus the measured fan-out cases (MINSERT, ATTRIB, unrendered DIMENSION) that make
the one-sided F2 bound the only sound assertion available.
"""

from __future__ import annotations

import shutil

import ezdxf
import pytest
from ezdxf.addons.drawing.recorder import Recorder

from technical_drawings_for_agents import plot as plot_module
from technical_drawings_for_agents.plot import (
    PlotError,
    PlotRequest,
    check_fidelity,
    plot,
    resolve_layout,
)
from technical_drawings_for_agents.render import render_dxf

from .synthetic import (
    BLOCK_HEAVY_CIRCLE_LAYER,
    make_dxf_block_heavy,
    make_dxf_empty,
    make_dxf_frozen_block_children,
    make_dxf_insert_with_attrib,
    make_dxf_minsert,
    make_dxf_paperspace_only,
    make_dxf_unrendered_dimension,
)

needs_rsvg = pytest.mark.skipif(
    shutil.which("rsvg-convert") is None, reason="rsvg-convert (librsvg) not installed"
)


class _CircleDroppingRecorder(Recorder):
    """A backend that loses one class of geometry — precisely what LibreOffice does.

    The circle inside ``EQUIP`` is the only entity on
    ``BLOCK_HEAVY_CIRCLE_LAYER``, so dropping ops on that layer removes exactly one
    child of each INSERT without guessing which recorded path is which. Because the
    checked recording is *replayed* into the SVG backend, the emitted sheet loses
    the circle too: this simulates a real lossy backend, not just a lying check.
    """

    def draw_path(self, path, properties):
        if properties.layer == BLOCK_HEAVY_CIRCLE_LAYER:
            return
        super().draw_path(path, properties)


def _lossy(monkeypatch) -> None:
    monkeypatch.setattr(plot_module, "Recorder", _CircleDroppingRecorder)


# --------------------------------------------------------------------------- #
# T2
# --------------------------------------------------------------------------- #


@needs_rsvg
def test_a_dxf_with_block_inserts_plots_faithfully(tmp_path):
    """T2 — the case issue #56 claims is broken is the case that works."""
    src = make_dxf_block_heavy(tmp_path / "BLK-001.dxf")
    doc = ezdxf.readfile(src)

    report = check_fidelity(doc, "Model")
    assert report.ok, [str(finding) for finding in report.findings]
    assert report.expected_units == 5  # 3 INSERTs + frame LWPOLYLINE + TEXT
    assert report.covered_units == report.expected_units
    assert report.expected_exploded == 12  # 3 x 4 visible block children
    assert report.recorded_ops >= report.expected_exploded

    out = tmp_path / "out"
    result = plot(PlotRequest(source=src, out_dir=out))
    assert result.pages == 1
    assert (out / "BLK-001.pdf").exists()
    assert not (out / "BLK-001.degraded.pdf").exists()
    assert not (out / "BLK-001.unchecked.pdf").exists()
    assert result.fidelity is not None and result.fidelity.ok


def test_the_recorded_ops_are_attributed_to_the_insert_handle(tmp_path):
    """Each INSERT's four children are recorded under the INSERT's own handle."""
    src = make_dxf_block_heavy(tmp_path / "BLK-001.dxf")
    doc = ezdxf.readfile(src)
    report = check_fidelity(doc, "Model")
    # 12 block-child ops + frame + note. Equality here is a property of *this*
    # fixture, not a general rule — see the MINSERT and MTEXT cases below.
    assert report.recorded_ops == 14


# --------------------------------------------------------------------------- #
# T3
# --------------------------------------------------------------------------- #


def test_a_dropped_insert_child_is_caught_and_no_canonical_artifact_is_written(
    tmp_path, monkeypatch
):
    """T3 — the whole point: a lossy backend fails loudly and leaves nothing behind."""
    _lossy(monkeypatch)
    src = make_dxf_block_heavy(tmp_path / "BLK-001.dxf")
    out = tmp_path / "out"

    with pytest.raises(PlotError) as excinfo:
        plot(PlotRequest(source=src, out_dir=out, fidelity="strict"))

    assert excinfo.value.kind == "fidelity"
    message = str(excinfo.value)
    doc = ezdxf.readfile(src)
    handles = [e.dxf.handle for e in doc.modelspace().query("INSERT")]
    assert all(handle in message for handle in handles), message
    assert "recorded_ops < exploded_leaves" in message
    assert "3 backend call(s)" in message and "4 " in message

    assert list(out.glob("*.pdf")) == []
    assert list(out.glob("*.plot-tmp")) == []
    assert list(out.glob("*.plot-staged")) == []


def test_a_dropped_insert_child_exits_3_through_the_cli(tmp_path, monkeypatch):
    """T3, CLI half — exit code 3, and every new code is >= 3 by construction."""
    from technical_drawings_for_agents.cli import main

    _lossy(monkeypatch)
    src = make_dxf_block_heavy(tmp_path / "BLK-001.dxf")
    assert main(["plot", str(src), "--out", str(tmp_path / "out")]) == 3
    assert list((tmp_path / "out").glob("*.pdf")) == []


# --------------------------------------------------------------------------- #
# T4
# --------------------------------------------------------------------------- #


@needs_rsvg
def test_fidelity_warn_produces_a_differently_named_artifact(tmp_path, monkeypatch, caplog):
    """T4 — the degradation is encoded in the filename, not in a log nobody reads."""
    _lossy(monkeypatch)
    src = make_dxf_block_heavy(tmp_path / "BLK-001.dxf")
    out = tmp_path / "out"

    with caplog.at_level("WARNING", logger="technical_drawings_for_agents.plot"):
        result = plot(PlotRequest(source=src, out_dir=out, fidelity="warn"))

    assert (out / "BLK-001.degraded.pdf").exists()
    assert not (out / "BLK-001.pdf").exists()
    assert result.fidelity is not None and result.fidelity.ok is False
    assert any(record.levelname == "WARNING" for record in caplog.records)


@needs_rsvg
def test_fidelity_warn_exits_0_and_says_degraded_on_stdout(tmp_path, monkeypatch, capsys):
    """T4, CLI half — exit 0 (it was asked for) but the result line says degraded."""
    from technical_drawings_for_agents.cli import main

    _lossy(monkeypatch)
    src = make_dxf_block_heavy(tmp_path / "BLK-001.dxf")
    code = main(
        ["plot", str(src), "--out", str(tmp_path / "out"), "--fidelity", "warn"]
    )
    assert code == 0
    assert "fidelity degraded" in capsys.readouterr().out


@needs_rsvg
def test_fidelity_off_writes_an_unchecked_artifact(tmp_path, monkeypatch):
    """``off`` is for triage of an already-known-broken file, and says so in the name."""
    _lossy(monkeypatch)
    src = make_dxf_block_heavy(tmp_path / "BLK-001.dxf")
    out = tmp_path / "out"
    result = plot(PlotRequest(source=src, out_dir=out, fidelity="off"))
    assert result.fidelity is None
    assert (out / "BLK-001.unchecked.pdf").exists()
    assert not (out / "BLK-001.pdf").exists()


# --------------------------------------------------------------------------- #
# T5
# --------------------------------------------------------------------------- #


@needs_rsvg
def test_a_paperspace_only_dxf_never_yields_a_blank_sheet(tmp_path):
    """T5a — modelspace is empty, so Layout1 is auto-selected instead of plotted blank."""
    src = make_dxf_paperspace_only(tmp_path / "PS-001.dxf")
    out = tmp_path / "out"
    result = plot(PlotRequest(source=src, out_dir=out))
    assert result.fidelity is not None
    assert result.fidelity.layout == "Layout1"
    assert result.fidelity.expected_units == 2
    assert (out / "PS-001.pdf").exists()


def test_plotting_the_empty_modelspace_of_a_paperspace_drawing_fails_and_names_layout1(
    tmp_path,
):
    """T5b — F4: asking for the wrong layout is an error naming the right one."""
    src = make_dxf_paperspace_only(tmp_path / "PS-001.dxf")
    out = tmp_path / "out"
    with pytest.raises(PlotError) as excinfo:
        plot(PlotRequest(source=src, out_dir=out, layout="Model"))
    assert excinfo.value.kind == "fidelity"
    message = str(excinfo.value)
    assert "Layout1" in message
    assert "Layout1=2" in message  # the unit count, not just the name
    assert not out.exists() or list(out.glob("*.pdf")) == []


def test_the_legacy_render_path_still_does_not_raise_on_a_paperspace_drawing(tmp_path):
    """T5 regression guard — constraint 0.1. The legacy blank PDF is still produced.

    This asserts the *defect* is preserved, because preserving it is the contract:
    ``render_dxf`` plots ``doc.modelspace()`` only. Fixing it here would change the
    output of an existing caller. ``plot`` is the fix.
    """
    src = make_dxf_paperspace_only(tmp_path / "PS-001.dxf")
    outputs = render_dxf(src, tmp_path / "legacy-out")
    assert {path.suffix for path in outputs} == {".pdf", ".png"}
    assert all(path.exists() for path in outputs)


def test_a_document_with_nothing_drawable_anywhere_is_refused(tmp_path):
    """The 1,294-byte blank PDF the existing ``st_size > 0`` guard passes."""
    src = make_dxf_empty(tmp_path / "EMPTY-001.dxf")
    with pytest.raises(PlotError) as excinfo:
        plot(PlotRequest(source=src, out_dir=tmp_path / "out"))
    assert excinfo.value.kind == "fidelity"
    assert "no layout holds a visible entity" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# T6
# --------------------------------------------------------------------------- #


@needs_rsvg
def test_frozen_layers_are_not_reported_as_missing_geometry(tmp_path):
    """T6 — a frozen layer does not plot. That is correct CAD semantics, not a loss.

    A test expecting ``expected_exploded == 2`` here has mis-implemented visibility
    resolution: the frozen LINE must appear in neither the unit set nor the leaf count.
    """
    src = make_dxf_frozen_block_children(tmp_path / "FRZ-001.dxf")
    doc = ezdxf.readfile(src)
    report = check_fidelity(doc, "Model")

    assert report.ok, [str(finding) for finding in report.findings]
    assert report.expected_exploded == 1  # the CIRCLE only
    assert report.expected_units == 2  # the INSERT + the visible frame

    frozen_handles = {
        entity.dxf.handle
        for entity in doc.blocks.get("FROZEN_EQUIP")
        if entity.dxf.layer == "PIPING"
    }
    assert not any(
        handle in str(finding) for finding in report.findings for handle in frozen_handles
    )
    result = plot(PlotRequest(source=src, out_dir=tmp_path / "out"))
    assert (tmp_path / "out" / "FRZ-001.pdf").exists()
    assert result.fidelity is not None and result.fidelity.ok


# --------------------------------------------------------------------------- #
# the fan-out cases that force a one-sided bound
# --------------------------------------------------------------------------- #


def test_a_minsert_array_passes_the_one_sided_bound(tmp_path):
    """24 ops from a leaf walk reporting 2. Equality would fail; the bound holds."""
    src = make_dxf_minsert(tmp_path / "MIN-001.dxf")
    doc = ezdxf.readfile(src)
    report = check_fidelity(doc, "Model")
    assert report.ok, [str(finding) for finding in report.findings]
    assert report.recorded_ops == 24
    assert report.expected_exploded == 2
    assert report.recorded_ops > report.expected_exploded


def test_an_attrib_is_a_drawable_unit_in_its_own_right(tmp_path):
    """ezdxf keys an ATTRIB's ops to the ATTRIB's handle, not the owning INSERT's.

    Folding them into the INSERT would produce a spurious F2 failure on every
    tagged block — which is most real vendor geometry.
    """
    src = make_dxf_insert_with_attrib(tmp_path / "ATT-001.dxf")
    doc = ezdxf.readfile(src)
    report = check_fidelity(doc, "Model")
    assert report.ok, [str(finding) for finding in report.findings]
    assert report.expected_units == 2  # the INSERT and its ATTRIB
    assert report.expected_exploded == 1  # the LINE; the ATTDEF is not a leaf


def test_an_unrendered_dimension_is_a_named_error_not_a_traceback(tmp_path):
    """D5 — ezdxf dies with a bare AttributeError from dimension.py. Name it instead."""
    src = make_dxf_unrendered_dimension(tmp_path / "DIM-001.dxf")
    with pytest.raises(PlotError) as excinfo:
        plot(PlotRequest(source=src, out_dir=tmp_path / "out"))
    assert excinfo.value.kind == "fidelity"
    message = str(excinfo.value)
    assert "DIMENSION" in message
    assert "AttributeError" in message
    assert list((tmp_path / "out").glob("*.pdf")) == [] if (tmp_path / "out").exists() else True


# --------------------------------------------------------------------------- #
# layout resolution
# --------------------------------------------------------------------------- #


def test_an_unknown_layout_name_lists_the_documents_layouts(tmp_path):
    src = make_dxf_block_heavy(tmp_path / "BLK-001.dxf")
    doc = ezdxf.readfile(src)
    with pytest.raises(PlotError) as excinfo:
        resolve_layout(doc, "Sheet7")
    assert excinfo.value.kind == "input"
    assert "'Model'" in str(excinfo.value)


def test_modelspace_wins_when_it_holds_geometry(tmp_path):
    src = make_dxf_block_heavy(tmp_path / "BLK-001.dxf")
    doc = ezdxf.readfile(src)
    assert resolve_layout(doc, None) == "Model"


def test_two_candidate_paperspace_layouts_refuse_to_guess(tmp_path):
    src = make_dxf_paperspace_only(tmp_path / "PS-001.dxf")
    doc = ezdxf.readfile(src)
    second = doc.layouts.new("Layout2")
    second.add_line((0, 0), (5, 5))
    doc.saveas(tmp_path / "PS-002.dxf")
    reloaded = ezdxf.readfile(tmp_path / "PS-002.dxf")
    with pytest.raises(PlotError) as excinfo:
        resolve_layout(reloaded, None)
    assert excinfo.value.kind == "fidelity"
    assert "refusing to guess" in str(excinfo.value)
