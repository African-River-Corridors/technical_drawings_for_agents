"""P9 acceptance tests 1-7, 24, 25 — the paper-space title and revision blocks.

Every geometric claim is asserted **mechanically**, off the emitted
``data-extents-mm`` / ``data-field`` attributes, at A3/A2/A1/A0 in both
orientations. Nothing here is verified by eye.
"""

from __future__ import annotations

import datetime
import xml.etree.ElementTree as ET

import pytest

from technical_drawings_for_agents.revisions import Revision, RevisionRegister
from technical_drawings_for_agents.titleblock import (
    ADVANCE_FACTOR,
    ISO7200_180MM,
    PAD_MM,
    PaperFrame,
    TitleBlockError,
    TitleBlockFields,
    iso7200_title_block,
    revision_block,
    revision_column_capacity,
    title_block_extents_mm,
)

from .p9_frames import ORIENTATIONS, SIZES, FakeFrame, NarrowFrame


def _fields(**overrides: str) -> TitleBlockFields:
    base = dict(
        identification_number="EXA-CIV-SEC-001",
        title="Drainage Channel Typical Section",
        revision="B",
        date_of_issue="2026-07-22",
        document_type="SECTION",
        document_status="CONCEPT — NOT FOR CONSTRUCTION",
        legal_owner="drawings-kit example",
        responsible_dept="Civil",
        technical_reference="TR-001",
        created_by="AB",
        checked_by="CD",
        approved_by="",
        sheet="1 / 1",
        provenance="abc1234",
    )
    base.update(overrides)
    return TitleBlockFields(**base)  # type: ignore[arg-type]


def _by_class(root: ET.Element, class_name: str) -> list[ET.Element]:
    return [
        element
        for element in root.iter()
        if class_name in str(element.attrib.get("class", "")).split()
    ]


def _extents(element: ET.Element) -> tuple[float, float, float, float]:
    return tuple(float(part) for part in element.attrib["data-extents-mm"].split())


def _entry(token: str, day: int, description: str | None = None) -> Revision:
    return Revision(
        rev=token,
        date=datetime.date(2026, 7, day),
        description=description or f"Revision {token} change note",
        by="AB",
        chk="CD",
    )


def _register(count: int) -> RevisionRegister:
    tokens = "ABCDEFGHIJKLMNOP"
    return RevisionRegister(
        tuple(_entry(tokens[index], index + 1) for index in range(count))
    )


def _cases() -> list[FakeFrame]:
    return [
        FakeFrame(size, orientation) for size in SIZES for orientation in ORIENTATIONS
    ]


# --------------------------------------------------------------------------- #
# 1-5: the block is 180 x 56 mm, flush bottom-right, at every size
# --------------------------------------------------------------------------- #


def test_title_block_extents_are_180x56mm_at_every_iso_size():
    """Test 1. Eight cases; the block is the same physical size in all of them."""
    seen = []
    for frame in _cases():
        root = ET.fromstring(iso7200_title_block(frame, _fields()))
        blocks = _by_class(root, "title-block")
        assert len(blocks) == 1, (frame.size, frame.orientation)
        x, y, w, h = _extents(blocks[0])
        assert w == pytest.approx(180.0, abs=1e-9)
        assert h == pytest.approx(56.0, abs=1e-9)
        fx, fy, fw, fh = frame.frame_mm()
        assert x == pytest.approx(fx + fw - 180.0, abs=1e-9)
        assert y == pytest.approx(fy + fh - 56.0, abs=1e-9)
        assert blocks[0].attrib["data-sheet"] == frame.size
        assert blocks[0].attrib["data-orientation"] == frame.orientation
        seen.append((x, y))
    assert len(_cases()) == 8
    # The anchor is the only thing that changes with the sheet.
    assert len(set(seen)) == 8


def test_title_block_sits_inside_the_frame_at_every_iso_size():
    """Test 2. Flush to the frame's bottom-right by design, inside it everywhere else."""
    for frame in _cases():
        root = ET.fromstring(iso7200_title_block(frame, _fields()))
        x, y, w, h = _extents(_by_class(root, "title-block")[0])
        fx, fy, fw, fh = frame.frame_mm()
        assert x + w == pytest.approx(fx + fw, abs=1e-9)
        assert y + h == pytest.approx(fy + fh, abs=1e-9)
        assert x >= fx - 1e-9
        assert y >= fy - 1e-9


def test_title_block_cell_geometry_equals_the_layout_table():
    """Test 3. Every cell, exactly the layout table — no missing cell, no extra."""
    frame = FakeFrame("A1", "landscape")
    root = ET.fromstring(iso7200_title_block(frame, _fields()))
    block_x, block_y, _, _ = _extents(_by_class(root, "title-block")[0])
    cells = _by_class(root, "tb-cell")

    assert {cell.attrib["data-field"] for cell in cells} == {
        cell.field for cell in ISO7200_180MM.cells
    }
    assert len(cells) == len(ISO7200_180MM.cells)
    by_field = {cell.attrib["data-field"]: _extents(cell) for cell in cells}
    for cell in ISO7200_180MM.cells:
        x, y, w, h = by_field[cell.field]
        assert x == pytest.approx(block_x + cell.x_mm, abs=1e-9), cell.field
        assert y == pytest.approx(block_y + cell.y_mm, abs=1e-9), cell.field
        assert w == pytest.approx(cell.w_mm, abs=1e-9), cell.field
        assert h == pytest.approx(cell.h_mm, abs=1e-9), cell.field


def test_title_block_text_is_physically_identical_across_sheet_sizes():
    """Test 4. The property that makes one implementation correct everywhere.

    A0 is rendered at 2 device units per mm and A3 at 1, so the emitted
    ``font-size`` values genuinely differ — and the millimetre heights and the
    text are identical.
    """
    a3 = FakeFrame("A3", "landscape", scale=1.0)
    a0 = FakeFrame("A0", "landscape", scale=2.0)

    def triples(frame: PaperFrame) -> list[tuple[str, str, float]]:
        root = ET.fromstring(iso7200_title_block(frame, _fields()))
        return [
            (
                node.attrib["data-field"],
                node.text or "",
                float(node.attrib["data-size-mm"]),
            )
            for node in root.iter()
            if node.tag == "text"
        ]

    assert triples(a3) == triples(a0)

    def device_sizes(frame: PaperFrame) -> list[str]:
        root = ET.fromstring(iso7200_title_block(frame, _fields()))
        return [node.attrib["font-size"] for node in root.iter() if node.tag == "text"]

    # The device font sizes differ everywhere — the millimetre heights do not.
    assert all(
        float(a) * 2.0 == pytest.approx(float(b))
        for a, b in zip(device_sizes(a3), device_sizes(a0))
    )
    assert device_sizes(a3) != device_sizes(a0)
    # The ISO 3098 nominal mm sizes this layout uses.
    assert {size for _f, _t, size in triples(a3)} == {2.5, 3.5, 5.0}


def test_title_block_carries_the_marker_validate_greps_for():
    """Test 5. `validate.py` greps for this literal; every drawing depends on it."""
    svg = iso7200_title_block(FakeFrame("A2", "portrait"), _fields())
    assert 'class="title-block"' in svg
    assert 'data-sheet="A2"' in svg
    assert 'data-orientation="portrait"' in svg


# --------------------------------------------------------------------------- #
# 6-7: loud failure instead of a clamp or an ellipsis
# --------------------------------------------------------------------------- #


def test_title_block_raises_when_the_frame_is_narrower_than_the_layout():
    """Test 6. It does not clamp, scale down, or drop cells."""
    frame = NarrowFrame()
    with pytest.raises(TitleBlockError) as excinfo:
        title_block_extents_mm(frame)
    assert "150" in str(excinfo.value)
    assert "180" in str(excinfo.value)
    with pytest.raises(TitleBlockError):
        iso7200_title_block(frame, _fields())


@pytest.mark.parametrize(
    "field",
    ["title", "identification_number", "legal_owner", "responsible_dept"],
)
def test_over_long_content_is_an_error_not_an_ellipsis(field: str):
    """Test 7. The anti-`svg.py:572-576` test, over several cells."""
    frame = FakeFrame("A1", "landscape")
    cell = ISO7200_180MM.cell(field)
    limit = cell.capacity() * cell.max_lines
    with pytest.raises(TitleBlockError) as excinfo:
        iso7200_title_block(frame, _fields(**{field: "X" * 400}))
    message = str(excinfo.value)
    assert field in message
    assert "400" in message
    assert str(limit) in message

    # And no output of the new renderer ever contains an ellipsis, for any input.
    for length in range(0, cell.capacity() * cell.max_lines + 1, 7):
        svg = iso7200_title_block(frame, _fields(**{field: "Wx" * (length // 2)}))
        assert "..." not in svg
        assert "…" not in svg


def test_cell_capacities_reproduce_the_specified_table():
    """Pins ADVANCE_FACTOR and PAD_MM: change either and this table moves."""
    assert (PAD_MM, ADVANCE_FACTOR) == (1.5, 0.55)
    assert ISO7200_180MM.cell("title").capacity() == 38
    assert ISO7200_180MM.cell("identification_number").capacity() == 21
    assert revision_column_capacity("description") == 61
    for column in ("by", "chk", "app"):
        assert revision_column_capacity(column) == 9
    assert sum(w for _c, _x, w in ISO7200_180MM.rev_columns) == ISO7200_180MM.width_mm


# --------------------------------------------------------------------------- #
# 24-25: the revision table
# --------------------------------------------------------------------------- #


def test_revision_block_renders_upward_from_the_title_block():
    """Test 24. Flush to the title block, header on top, newest row adjacent to it."""
    frame = FakeFrame("A1", "landscape")
    register = _register(3)
    svg, warnings = revision_block(frame, register)
    assert warnings == []

    root = ET.fromstring(svg)
    block = _by_class(root, "revision-block")[0]
    rx, ry, rw, rh = _extents(block)
    assert rw == pytest.approx(180.0, abs=1e-9)
    assert rh == pytest.approx(4 * ISO7200_180MM.rev_row_mm, abs=1e-9)
    assert block.attrib["data-rows"] == "3"
    assert block.attrib["data-latest-rev"] == "C"

    tx, ty, tw, _th = title_block_extents_mm(frame)
    assert ry + rh == pytest.approx(ty, abs=1e-9)
    assert rx + rw == pytest.approx(tx + tw, abs=1e-9)

    rows = _by_class(root, "revision-row")
    assert [row.attrib["data-rev"] for row in rows] == ["A", "B", "C"]
    # Newest row is the lowest on the paper, i.e. adjacent to the title block.
    tops = [_extents(_by_class(row, "revision-cell")[0])[1] for row in rows]
    assert tops == sorted(tops)
    assert tops[-1] + ISO7200_180MM.rev_row_mm == pytest.approx(ty, abs=1e-9)

    # C8 — no register, no table, and nothing emitted at all.
    empty, empty_warnings = revision_block(frame, RevisionRegister(()))
    assert (empty, empty_warnings) == ("", [])
    assert "revision-block" not in empty


def test_revision_block_overflow_is_visible():
    """Test 25. Nothing is dropped without the continuation row saying so."""
    frame = FakeFrame("A1", "landscape")
    register = _register(12)
    svg, warnings = revision_block(frame, register)

    root = ET.fromstring(svg)
    block = _by_class(root, "revision-block")[0]
    assert block.attrib["data-rows"] == "8"
    rows = _by_class(root, "revision-row")
    assert len(rows) == 8
    assert rows[0].attrib["data-rev"] == "+earlier"
    assert "+ 5 EARLIER REVISIONS" in "".join(rows[0].itertext())
    assert [row.attrib["data-rev"] for row in rows[1:]] == list("FGHIJKL")
    assert rows[-1].attrib["data-rev"] == "L" == register.latest.rev
    assert len(warnings) == 1
    assert "12" in warnings[0]
    assert "8" in warnings[0]


def test_empty_revision_cells_render_an_em_dash_never_a_substituted_value():
    """C7 / test 17's rendering half: a blank cell is ambiguous, an em-dash is not."""
    frame = FakeFrame("A3", "landscape")
    register = RevisionRegister(
        (Revision(rev="A", date=datetime.date(2026, 7, 1), description="First", by="AB"),)
    )
    svg, _warnings = revision_block(frame, register)
    root = ET.fromstring(svg)
    row = _by_class(root, "revision-row")[0]
    cells = {cell.attrib["data-col"]: "".join(cell.itertext()).strip() for cell in
             _by_class(row, "revision-cell")}
    assert cells["by"] == "AB"
    assert cells["chk"] == "—"
    assert cells["app"] == "—"

    block = ET.fromstring(iso7200_title_block(frame, _fields(approved_by="")))
    approved = [
        cell for cell in _by_class(block, "tb-cell")
        if cell.attrib["data-field"] == "approved_by"
    ][0]
    assert "—" in "".join(approved.itertext())


def test_the_layout_fits_the_narrowest_frame_we_could_draw_on():
    """A4 portrait at the 10 mm house margin set: 190 mm of frame for a 180 mm block."""
    frame = FakeFrame("A4", "portrait")
    assert frame.frame_mm()[2] == pytest.approx(190.0)
    x, y, w, h = title_block_extents_mm(frame)
    assert (w, h) == (180.0, 56.0)
    assert x >= frame.frame_mm()[0]
