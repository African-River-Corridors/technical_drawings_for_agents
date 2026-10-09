"""The neutral EXAMPLE_A3 title block, and the user-supplied-layout mechanism it shows."""

from __future__ import annotations

import pytest

from technical_drawings_for_agents.revisions import RevisionRegister
from technical_drawings_for_agents.titleblock import (
    EXAMPLE_A3,
    ISO7200_180MM,
    Cell,
    TitleBlockError,
    TitleBlockFields,
    TitleBlockLayout,
    assert_layout_fits,
    iso7200_title_block,
    revision_block,
)

from .p9_frames import FakeFrame

SAMPLE = TitleBlockFields(
    identification_number="EXA-CIV-GA-001",
    title="GENERAL ARRANGEMENT",
    revision="B",
    date_of_issue="2026-01-15",
    document_status="FOR REVIEW",
    project="EXAMPLE PROJECT",
    created_by="A. DRAFTER",
    checked_by="C. CHECKER",
    approved_by="E. APPROVER",
    sheet="1 OF 1",
    scale="1:500",
)


def test_the_example_block_has_the_generic_fields():
    labels = {cell.field: cell.label for cell in EXAMPLE_A3.cells}
    assert labels == {
        "project": "PROJECT",
        "title": "TITLE",
        "identification_number": "DRAWING No.",
        "revision": "REV",
        "scale": "SCALE",
        "created_by": "DRAWN",
        "checked_by": "CHECKED",
        "approved_by": "APPROVED",
        "sheet": "SHEET",
        "document_status": "STATUS",
    }


@pytest.mark.parametrize("size", ["A3", "A2", "A1", "A0"])
@pytest.mark.parametrize("orientation", ["landscape", "portrait"])
def test_it_fits_a3_and_larger(size, orientation):
    assert_layout_fits(FakeFrame(size, orientation), EXAMPLE_A3)


def test_a4_portrait_is_refused_rather_than_overhanging_the_sheet():
    with pytest.raises(TitleBlockError) as exc:
        assert_layout_fits(FakeFrame("A4", "portrait"), EXAMPLE_A3)
    message = str(exc.value)
    assert "at least 200 mm wide" in message
    assert "190" in message


def test_the_iso_block_still_fits_a4_portrait():
    assert_layout_fits(FakeFrame("A4", "portrait"), ISO7200_180MM)


def test_rendering_enforces_the_fit_check():
    with pytest.raises(TitleBlockError):
        iso7200_title_block(FakeFrame("A4", "portrait"), SAMPLE, layout=EXAMPLE_A3)


def test_every_sample_value_renders():
    markup = iso7200_title_block(FakeFrame("A3", "landscape"), SAMPLE, layout=EXAMPLE_A3)
    assert 'class="title-block"' in markup
    for value in ("EXA-CIV-GA-001", "GENERAL ARRANGEMENT", "EXAMPLE PROJECT", "1:500",
                  "A. DRAFTER", "C. CHECKER", "E. APPROVER", "FOR REVIEW"):
        assert value in markup
    # pair_date=False: the date lives in the revisions table, not beside REV.
    assert "2026-01-15" not in markup


def test_an_overlong_value_is_reported_rather_than_trimmed():
    overlong = TitleBlockFields(
        identification_number="EXA-" + "X" * 120, title="T", revision="1",
        date_of_issue="2026-01-15",
    )
    with pytest.raises(TitleBlockError, match="identification_number"):
        iso7200_title_block(FakeFrame("A3", "landscape"), overlong, layout=EXAMPLE_A3)


def test_approved_by_is_never_defaulted():
    unapproved = TitleBlockFields(
        identification_number="EXA-CIV-GA-001", title="GA", revision="B",
        date_of_issue="2026-01-15", created_by="A. DRAFTER",
    )
    markup = iso7200_title_block(FakeFrame("A3", "landscape"), unapproved, layout=EXAMPLE_A3)
    assert "A. DRAFTER" in markup
    assert "APPROVER" not in markup


def test_every_cell_can_physically_hold_its_label_and_lines():
    for cell in EXAMPLE_A3.cells:
        needed = EXAMPLE_A3.label_mm + cell.max_lines * cell.text_mm
        assert needed <= cell.h_mm + 1e-6, cell.field


def test_cells_tile_the_block_without_overlap():
    area = sum(c.w_mm * c.h_mm for c in EXAMPLE_A3.cells)
    assert area == pytest.approx(EXAMPLE_A3.width_mm * EXAMPLE_A3.height_mm)
    for cell in EXAMPLE_A3.cells:
        assert cell.x_mm + cell.w_mm <= EXAMPLE_A3.width_mm + 1e-6
        assert cell.y_mm + cell.h_mm <= EXAMPLE_A3.height_mm + 1e-6


def test_the_revisions_table_renders_above_the_example_block():
    register = RevisionRegister.from_list(
        [{"rev": "A", "date": "2026-01-01", "description": "FIRST ISSUE",
          "by": "AD", "chk": "CC", "app": "EA"}],
        ctx="test",
    )
    markup, warnings = revision_block(FakeFrame("A3", "landscape"), register, layout=EXAMPLE_A3)
    assert warnings == []
    assert "FIRST ISSUE" in markup and "2026-01-01" in markup


def test_a_user_supplied_layout_is_honoured():
    """The mechanism the example demonstrates: any TitleBlockLayout can be passed in."""
    mine = TitleBlockLayout(
        id="MY_HOUSE",
        width_mm=120.0,
        height_mm=20.0,
        cells=(
            Cell("title", "DRAWING TITLE", 0.0, 0.0, 80.0, 20.0, text_mm=3.0),
            Cell("identification_number", "NUMBER", 80.0, 0.0, 40.0, 20.0, text_mm=3.0),
        ),
    )
    markup = iso7200_title_block(FakeFrame("A3", "landscape"), SAMPLE, layout=mine)
    assert "DRAWING TITLE" in markup and "GENERAL ARRANGEMENT" in markup
    assert "PROJECT" not in markup
