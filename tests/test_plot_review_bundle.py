"""``--review``: N sheets in, an N-page PDF out, or nothing. Plus the ISSUED gate.

Acceptance tests T10, T11, T15. The page count is always read back from the
produced file, never inferred from the number of arguments handed to a converter.
"""

from __future__ import annotations

import ast
import shutil
import subprocess
from pathlib import Path

import pytest

from technical_drawings_for_agents import mapplot
from technical_drawings_for_agents import plot as plot_module
from technical_drawings_for_agents.cli import main
from technical_drawings_for_agents.plot import PlotError, discover_sheets, review_bundle

needs_rsvg = pytest.mark.skipif(
    shutil.which("rsvg-convert") is None, reason="rsvg-convert (librsvg) not installed"
)

_META = """number: A-001
title: Review set
revision: A
status: CONCEPT
for_construction: false
sheets:
  - out/A-001.svg
  - out/A-002.svg
  - out/A-003.svg
"""

_META_NO_SHEETS = """number: A-001
title: Review set
revision: A
status: CONCEPT
for_construction: false
"""

TOKENS = ("SHEET-ONE", "SHEET-TWO", "SHEET-THREE")


def _sheet_svg(token: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<svg xmlns="http://www.w3.org/2000/svg" width="420mm" height="297mm" '
        'viewBox="0 0 420 297"><rect width="420" height="297" fill="#ffffff"/>'
        f'<text x="40" y="150" font-family="sans-serif" font-size="20" '
        f'fill="#000000">{token}</text></svg>\n'
    )


def _drawing_dir(tmp_path: Path, *, meta: str = _META) -> Path:
    work = tmp_path / "review-set"
    (work / "out").mkdir(parents=True)
    (work / "meta.yaml").write_text(meta, encoding="utf-8")
    for index, token in enumerate(TOKENS, start=1):
        (work / "out" / f"A-00{index}.svg").write_text(_sheet_svg(token), encoding="utf-8")
    return work


def _page_count(pdf: Path) -> int:
    from pypdf import PdfReader

    return len(PdfReader(str(pdf)).pages)


def _page_text(pdf: Path, page: int) -> str:
    """Read text from one page. pdftotext if poppler is present, else pypdf."""
    pdftotext = shutil.which("pdftotext")
    if pdftotext is not None:
        completed = subprocess.run(
            [pdftotext, "-f", str(page), "-l", str(page), str(pdf), "-"],
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
        return completed.stdout
    from pypdf import PdfReader

    return PdfReader(str(pdf)).pages[page - 1].extract_text()


# --------------------------------------------------------------------------- #
# T10
# --------------------------------------------------------------------------- #


@needs_rsvg
def test_review_bundles_three_sheets_into_one_three_page_pdf_in_declared_order(tmp_path):
    """T10 — the page count is read back from the file, and the order is the declared one."""
    pytest.importorskip("pypdf")
    work = _drawing_dir(tmp_path)
    bundle = work / "out" / "REVIEW.pdf"

    assert main(["plot", str(work), "--review", str(bundle)]) == 0
    assert bundle.exists()
    assert _page_count(bundle) == 3
    for page, token in enumerate(TOKENS, start=1):
        assert token in _page_text(bundle, page), f"page {page} should carry {token}"


@needs_rsvg
def test_without_a_sheets_key_the_order_comes_from_the_filename_rule(tmp_path):
    """T10, second half — §3.8.3: a total order that never depends on directory iteration."""
    pytest.importorskip("pypdf")
    work = _drawing_dir(tmp_path, meta=_META_NO_SHEETS)
    assert [path.name for path in discover_sheets(work)] == [
        "A-001.svg",
        "A-002.svg",
        "A-003.svg",
    ]
    bundle = work / "out" / "REVIEW.pdf"
    assert main(["plot", str(work), "--review", str(bundle)]) == 0
    assert _page_count(bundle) == 3


def test_the_sheet_order_is_total_and_ignores_filesystem_order(tmp_path):
    """§3.8.3's regex, pinned exactly — including a wart worth knowing about.

    The spec's pattern is ``^(?P<number>.+?)(?:[_-]S?(?P<index>\\d+))?$`` with a
    **non-greedy** number, so the index group eats the trailing digits of the
    drawing number itself: ``B-010`` parses as ``("B", 10)`` and ``B-002_S2`` as
    ``("B-002", 2)``. Two schemes in one directory therefore interleave in a way a
    human would not predict — ``B-010`` sorts *before* ``B-002_S2``. Implemented to
    the letter anyway, because the spec's requirement is a **total, filesystem-
    independent** order that two independent implementations agree on, and it is
    that. ``meta.yaml``'s ``sheets:`` is the explicit override; see the PR body.
    """
    work = tmp_path / "set"
    (work / "out").mkdir(parents=True)
    for name in ("B-010.svg", "B-002.svg", "B-002_S2.svg", "A-001.svg"):
        (work / "out" / name).write_text(_sheet_svg("X"), encoding="utf-8")
    assert [path.name for path in discover_sheets(work)] == [
        "A-001.svg",
        "B-002.svg",
        "B-010.svg",
        "B-002_S2.svg",
    ]
    # Whatever the order is, it is a function of the names alone.
    assert discover_sheets(work) == discover_sheets(work)


@needs_rsvg
def test_a_previous_review_bundle_is_never_treated_as_a_sheet(tmp_path):
    """A bundle written into out/ must not become page 1 of the next bundle."""
    pytest.importorskip("pypdf")
    work = _drawing_dir(tmp_path, meta=_META_NO_SHEETS)
    bundle = work / "out" / "REVIEW.pdf"
    assert main(["plot", str(work), "--review", str(bundle)]) == 0
    assert main(["plot", str(work), "--review", str(bundle)]) == 0
    assert _page_count(bundle) == 3


def test_an_empty_sheet_set_is_a_bundle_error(tmp_path):
    with pytest.raises(PlotError) as excinfo:
        review_bundle([], tmp_path / "REVIEW.pdf")
    assert excinfo.value.kind == "bundle"


def test_a_directory_without_review_is_rejected(tmp_path, capsys):
    """A directory is a plot target only with --review; it is never built."""
    work = _drawing_dir(tmp_path)
    assert main(["plot", str(work)]) == 2
    assert "only with --review" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# T11
# --------------------------------------------------------------------------- #


@needs_rsvg
def test_review_refuses_to_bundle_an_incomplete_set(tmp_path, capsys):
    """T11a — no bundle at all, exit 4, and the missing sheet named by path."""
    work = _drawing_dir(tmp_path)
    (work / "out" / "A-002.svg").unlink()
    bundle = work / "out" / "REVIEW.pdf"

    assert main(["plot", str(work), "--review", str(bundle)]) == 4
    assert not bundle.exists()
    assert "out/A-002.svg" in capsys.readouterr().err


@needs_rsvg
def test_allow_missing_writes_a_partial_bundle_and_still_exits_4(tmp_path):
    """T11b — the artifact exists for triage; the filename and the exit code both say so."""
    pytest.importorskip("pypdf")
    work = _drawing_dir(tmp_path)
    (work / "out" / "A-002.svg").unlink()
    bundle = work / "out" / "REVIEW.pdf"

    assert main(["plot", str(work), "--review", str(bundle), "--allow-missing"]) == 4
    partial = work / "out" / "REVIEW.partial.pdf"
    assert partial.exists()
    assert not bundle.exists()
    assert _page_count(partial) == 3
    assert "SHEET MISSING" in _page_text(partial, 2)
    assert "NOT FOR REVIEW" in _page_text(partial, 2)


@needs_rsvg
def test_a_page_count_mismatch_is_a_bundle_error_not_a_shrug(tmp_path, monkeypatch):
    """The converter is never trusted about how many pages it wrote."""
    pytest.importorskip("pypdf")
    work = _drawing_dir(tmp_path)
    monkeypatch.setattr(plot_module, "_page_count", lambda _pdf: 2)
    bundle = work / "out" / "REVIEW.pdf"
    with pytest.raises(PlotError) as excinfo:
        review_bundle(discover_sheets(work), bundle)
    assert excinfo.value.kind == "bundle"
    assert "page count mismatch" in str(excinfo.value)
    assert not bundle.exists()


def test_a_non_pdf_review_target_is_rejected(tmp_path):
    work = _drawing_dir(tmp_path)
    with pytest.raises(PlotError) as excinfo:
        review_bundle(discover_sheets(work), work / "out" / "REVIEW.svg")
    assert excinfo.value.kind == "bundle"


def test_a_malformed_sheets_key_names_the_offending_file(tmp_path):
    work = _drawing_dir(tmp_path)
    (work / "meta.yaml").write_text("number: A-001\nsheets: 3\n", encoding="utf-8")
    with pytest.raises(PlotError) as excinfo:
        discover_sheets(work)
    assert excinfo.value.kind == "input"
    assert "meta.yaml" in str(excinfo.value)
    assert "non-empty list" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# T15 — the ISSUED gate
# --------------------------------------------------------------------------- #


@needs_rsvg
def test_the_issued_gate_is_untouched_by_the_plot_path(tmp_path):
    """T15 — ``meta.yaml`` is byte-identical after every invocation, including failures."""
    pytest.importorskip("pypdf")
    work = _drawing_dir(tmp_path)
    meta = work / "meta.yaml"
    before = meta.read_bytes()

    main(["plot", str(work), "--review", str(work / "out" / "REVIEW.pdf")])
    assert meta.read_bytes() == before

    main(
        [
            "plot",
            str(work / "out" / "A-001.svg"),
            "--out",
            str(work / "out"),
            "--fidelity",
            "warn",
        ]
    )
    assert meta.read_bytes() == before

    (work / "out" / "A-002.svg").unlink()
    main(
        [
            "plot",
            str(work),
            "--review",
            str(work / "out" / "REVIEW2.pdf"),
            "--allow-missing",
        ]
    )
    assert meta.read_bytes() == before


def test_no_plot_code_path_reads_for_construction_or_assigns_status():
    """T15, source inspection — asserted over the **AST**, not over the file text.

    A substring grep would be satisfied by deleting the docstrings that promise
    this, which is the wrong incentive. So: parse both modules, strip docstrings,
    and assert no name, attribute, key or literal touches the gate, and that
    nothing assigns to a ``status`` attribute or key anywhere.
    """
    for module in (plot_module, mapplot):
        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        docstrings = {
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
        }
        for node in ast.walk(tree):
            if node in docstrings:
                continue
            if isinstance(node, (ast.Name, ast.Attribute)):
                label = getattr(node, "id", None) or getattr(node, "attr", None)
                assert label not in ("for_construction", "ISSUED"), (
                    f"{module.__name__} touches {label}"
                )
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert node.value not in ("for_construction", "ISSUED"), (
                    f"{module.__name__} names {node.value!r} outside a docstring"
                )
            targets: list[ast.expr] = []
            if isinstance(node, ast.Assign):
                targets = list(node.targets)
            elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
                targets = [node.target]
            for target in targets:
                if isinstance(target, ast.Attribute):
                    assert target.attr not in ("status", "for_construction")
                if isinstance(target, ast.Subscript) and isinstance(
                    target.slice, ast.Constant
                ):
                    assert target.slice.value not in ("status", "for_construction")


def test_the_bundler_never_draws_a_watermark():
    """§6.8 — the only page the bundler generates is the MISSING placeholder."""
    source = Path(plot_module.__file__).read_text(encoding="utf-8")
    assert "svg_status_watermark" not in source
    assert "svg_provenance_stamp" not in source
