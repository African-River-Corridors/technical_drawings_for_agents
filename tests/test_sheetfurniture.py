from __future__ import annotations

from pathlib import Path

import pytest

from technical_drawings_for_agents.sheetfurniture import (
    SheetFurnitureError,
    assert_strip_fits,
    load_settings,
    render_notes_column,
    render_strip_table,
)

from .p9_frames import FakeFrame

SETTINGS = Path(__file__).resolve().parents[1] / "docs" / "specs" / "sheet-furniture.example.yaml"

# A sample revision row, sized to the example settings' columns.
REV_ROW = {
    "rev": "0",
    "by": "AD",
    "date": "2026-01-15",
    "description": "ISSUED FOR CONSTRUCTION",
    "chk": "CC",
    "app": "EA",
}


@pytest.fixture
def furniture():
    return load_settings(SETTINGS)


def test_the_shipped_example_settings_load_with_their_geometry(furniture):
    assert furniture.id == "EXAMPLE_SHEET_FURNITURE"
    assert furniture.strip_width_mm == 380.0
    assert furniture.table("references").width_mm == pytest.approx(90.0)
    assert furniture.table("revisions").width_mm == pytest.approx(90.0)
    assert furniture.notes.width_mm == pytest.approx(60.0)


def test_the_two_tables_do_not_overlap_each_other(furniture):
    """They sit side by side on one strip, so an overlap would draw one over the
    other rather than over blank paper."""
    references = furniture.table("references")
    revisions = furniture.table("revisions")
    assert references.x_to_mm <= revisions.x_from_mm + 1e-6


def test_the_live_revision_row_renders(furniture):
    markup = render_strip_table(FakeFrame("A2", "landscape"), furniture.table("revisions"), [REV_ROW],
                                pad_mm=furniture.pad_mm)

    assert 'class="sheet-furniture"' in markup
    assert "ISSUED FOR CONSTRUCTION" in markup
    assert "2026-01-15" in markup
    assert ">REVISIONS<" in markup  # the neutral heading


def test_an_overlong_value_is_refused_not_ellipsised(furniture):
    """Same rule as the title block: a value that will not fit raises, naming the
    column, the length and the limit."""
    row = dict(REV_ROW, description="X" * 400)

    with pytest.raises(SheetFurnitureError) as exc:
        render_strip_table(FakeFrame("A2", "landscape"), furniture.table("revisions"), [row],
                               pad_mm=furniture.pad_mm)

    message = str(exc.value)
    assert "revisions.description" in message
    assert "400 chars" in message


def test_more_rows_than_the_table_holds_is_an_error_not_a_silent_drop(furniture):
    """Dropping the oldest revision silently is how a drawing loses its history."""
    rows = [REV_ROW, REV_ROW, REV_ROW]

    with pytest.raises(SheetFurnitureError, match="holds 2 row"):
        render_strip_table(FakeFrame("A2", "landscape"), furniture.table("revisions"), rows,
                           pad_mm=furniture.pad_mm)


def test_the_strip_does_not_fit_a4_landscape(furniture):
    """380 mm of furniture against a 277 mm frame is refused, not overhung."""
    with pytest.raises(SheetFurnitureError) as exc:
        assert_strip_fits(FakeFrame("A4", "landscape"), furniture)

    message = str(exc.value)
    assert "380" in message and "277" in message
    assert "widen the frame" in message  # the fix is margins, not narrower tables


@pytest.mark.parametrize("size", ["A3", "A2"])
def test_the_strip_fits_a3_and_a2_landscape(furniture, size):
    assert_strip_fits(FakeFrame(size, "landscape"), furniture)


def test_notes_render_only_what_has_a_value(furniture):
    """An absent coordinate system is a gap in the drawing, not a field to dash."""
    markup = render_notes_column(
        FakeFrame("A2", "landscape"),
        furniture.notes,
        {"coordinate_system": "UTM Zone 30N — WGS 84"},
    )

    assert "UTM Zone 30N" in markup
    assert "COORDINATE SYSTEM" in markup
    assert "CONTOUR INTERVALS" not in markup  # no value supplied, so no label either


def test_columns_that_overrun_their_table_are_rejected_at_load(tmp_path):
    """A column overhanging its table draws over the next table along the strip, so
    it fails at load rather than on paper."""
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "id: BAD\n"
        "bottom_strip: {total_width_mm: 100}\n"
        "tables:\n"
        "  revisions:\n"
        "    x_from_mm: -100.0\n"
        "    x_to_mm: -50.0\n"
        "    heading: R\n"
        "    row_height_mm: 4.0\n"
        "    max_rows: 1\n"
        "    columns:\n"
        "      - {field: rev, label: REV, x_mm: -100.0, w_mm: 80.0}\n",
        encoding="utf-8",
    )

    with pytest.raises(SheetFurnitureError, match="overruns the table's right edge"):
        load_settings(bad)


def test_overlapping_columns_are_rejected_at_load(tmp_path):
    bad = tmp_path / "overlap.yaml"
    bad.write_text(
        "id: BAD\n"
        "bottom_strip: {total_width_mm: 100}\n"
        "tables:\n"
        "  t:\n"
        "    x_from_mm: -100.0\n"
        "    x_to_mm: -0.0\n"
        "    heading: T\n"
        "    row_height_mm: 4.0\n"
        "    max_rows: 1\n"
        "    columns:\n"
        "      - {field: a, x_mm: -100.0, w_mm: 60.0}\n"
        "      - {field: b, x_mm: -50.0, w_mm: 10.0}\n",
        encoding="utf-8",
    )

    with pytest.raises(SheetFurnitureError, match="overlap"):
        load_settings(bad)


def test_a_missing_required_number_is_named(tmp_path):
    bad = tmp_path / "missing.yaml"
    bad.write_text(
        "id: BAD\ntables:\n  t:\n    x_from_mm: -10.0\n    heading: T\n"
        "    row_height_mm: 4.0\n",
        encoding="utf-8",
    )

    with pytest.raises(SheetFurnitureError, match="'x_to_mm' is required"):
        load_settings(bad)
