"""Tiny synthetic DXF fixtures built with ezdxf.

No vendor content — these are hand-built DXFs used to prove render routing and
the ingest clean-up logic (entity/colour/unit assertions), never real supplier
drawings (which are confidential / S3-private and must not be committed).
"""

from __future__ import annotations

from pathlib import Path

import ezdxf

BLUE = 5  # an "arbitrary vendor colour" for text (reads badly on white)


def make_dxf_with_insert(path: str | Path) -> Path:
    """A DXF with a block definition (containing blue text + geometry) and an
    INSERT of it in modelspace — plus one blue text directly in modelspace.

    Exercises: render routing (INSERT present -> LibreOffice) and the
    recolour-inside-block-definitions behaviour.
    """
    path = Path(path)
    doc = ezdxf.new("R2018", setup=True)
    blk = doc.blocks.new(name="EQUIP")
    blk.add_line((0, 0), (2, 0))
    blk.add_circle((1, 1), 0.5)
    blk.add_text("PUMP", height=0.3, dxfattribs={"color": BLUE}).set_placement((0, 1))
    msp = doc.modelspace()
    msp.add_blockref("EQUIP", (5, 5), dxfattribs={"xscale": 1, "yscale": 1})
    msp.add_text("SHEET NOTE", height=0.3, dxfattribs={"color": BLUE}).set_placement((5, 0))
    doc.saveas(path)
    return path


def make_dxf_no_insert(path: str | Path) -> Path:
    """A born-as-code style DXF: plain geometry, no INSERTs.

    Exercises: render routing (no INSERT -> matplotlib).
    """
    path = Path(path)
    doc = ezdxf.new("R2018", setup=True)
    msp = doc.modelspace()
    msp.add_line((0, 0), (10, 0))
    msp.add_lwpolyline([(0, 0), (10, 0), (10, 5), (0, 5)], close=True)
    msp.add_text("TYPICAL SECTION", height=0.3).set_placement((1, 6))
    doc.saveas(path)
    return path


def make_two_sheet_dxf(path: str | Path) -> Path:
    """Two clusters of geometry far apart in x (a 'tiled' multi-sheet file).

    Left cluster near x=0, right cluster near x=1000 — exercises isolate_sheet.
    """
    path = Path(path)
    doc = ezdxf.new("R2018", setup=True)
    msp = doc.modelspace()
    # Left sheet
    msp.add_lwpolyline([(0, 0), (100, 0), (100, 100), (0, 100)], close=True)
    msp.add_text("LEFT", height=5).set_placement((10, 10))
    # Right sheet
    msp.add_lwpolyline(
        [(1000, 0), (1100, 0), (1100, 100), (1000, 100)], close=True
    )
    msp.add_text("RIGHT", height=5).set_placement((1010, 10))
    doc.saveas(path)
    return path


def _fill_cluster(msp, x0: int, n_lines: int = 30, x_span: int = 90, label: str | None = None):
    """Sprinkle geometry + text lines across a cluster so it holds a substantial
    share of entities (adaptive isolation needs each side >= 15% of entities)."""
    for i in range(n_lines):
        y = 5 + i * 3
        msp.add_line((x0 + 5, y), (x0 + 5 + x_span, y))
    if label:
        msp.add_text(label, height=5).set_placement((x0 + 10, 10))


def make_two_sheet_titled_dxf(path: str | Path) -> Path:
    """Two genuine SHEETS side by side — each has its own title block.

    Left = the Chinese sheet (title marker 图号 + little English); right = the
    English sheet (title marker 'Approval' + lots of ASCII text). Adaptive
    isolate_sheet must detect two title-blocks and keep the English (right) one.
    """
    path = Path(path)
    doc = ezdxf.new("R2018", setup=True)
    msp = doc.modelspace()
    # Left (Chinese) sheet with a title block.
    _fill_cluster(msp, 0)
    msp.add_lwpolyline([(0, 0), (100, 0), (100, 100), (0, 100)], close=True)
    msp.add_text("图号 SL-01", height=5).set_placement((10, 95))  # title-block marker
    msp.add_text("泵", height=5).set_placement((10, 50))  # Chinese label (no ASCII)
    # Right (English) sheet with a title block + lots of English.
    _fill_cluster(msp, 1000)
    msp.add_lwpolyline([(1000, 0), (1100, 0), (1100, 100), (1000, 100)], close=True)
    msp.add_text("Approval DRAWING", height=5).set_placement((1010, 95))  # marker
    msp.add_text("CLEAN WATER PUMP GENERAL ARRANGEMENT", height=5).set_placement((1010, 60))
    msp.add_text("SECTION AND PLAN VIEWS", height=5).set_placement((1010, 40))
    doc.saveas(path)
    return path


def make_single_sheet_gapped_dxf(path: str | Path) -> Path:
    """ONE sheet whose views are separated by a large x-gap — the pump-GA case.

    A single title block (image left), a section view beside it, and a PLAN view
    far to the right (the gapped view-group). Only ONE title-block marker exists,
    so adaptive isolate_sheet must KEEP THE WHOLE DRAWING (never drop the plan).
    The regression guard for the dropped-pump-view bug.
    """
    path = Path(path)
    doc = ezdxf.new("R2018", setup=True)
    msp = doc.modelspace()
    # Main view + the ONLY title block (near x=0).
    _fill_cluster(msp, 0, label="PUMP SECTION")
    msp.add_lwpolyline([(0, 0), (100, 0), (100, 100), (0, 100)], close=True)
    msp.add_text("图号 GA-PUMP-01", height=5).set_placement((10, 95))  # sole title marker
    # A second view-group far to the right — a real view, NO title block.
    _fill_cluster(msp, 1000, label="PLAN VIEW")
    msp.add_lwpolyline([(1000, 0), (1100, 0), (1100, 100), (1000, 100)], close=True)
    doc.saveas(path)
    return path


# --------------------------------------------------------------------------
# P4 plot-path fixtures (additive — the fixtures above are unchanged).
#
# Real vendor drawings are confidential and must never be committed, so these
# reproduce their *structure*: block-heavy sheets, paper-space-only content,
# frozen layers inside block definitions, MINSERT arrays and tagged blocks.
# Expected-visible unit counts and exploded-leaf counts are known by
# construction, and are asserted as literals in tests/test_plot_fidelity.py.
# --------------------------------------------------------------------------

#: The circle inside ``EQUIP`` sits on its own layer so a test can simulate a
#: backend that drops one class of geometry without guessing which recorded op is
#: which. The layer is neither frozen nor off, so it stays in the expectation.
BLOCK_HEAVY_CIRCLE_LAYER = "EQUIP-CIRCLE"


def make_dxf_block_heavy(path: str | Path) -> Path:
    """Three INSERTs of a four-entity block, plus a frame and a note.

    Expected-visible units: 5 (3 INSERTs + frame LWPOLYLINE + TEXT).
    Exploded visible leaves: 12 (3 x [LINE, CIRCLE, LWPOLYLINE, TEXT]).
    """
    path = Path(path)
    doc = ezdxf.new("R2018", setup=True)
    doc.layers.add(BLOCK_HEAVY_CIRCLE_LAYER)
    blk = doc.blocks.new(name="EQUIP")
    blk.add_line((0, 0), (2, 0))
    blk.add_circle((1, 1), 0.5, dxfattribs={"layer": BLOCK_HEAVY_CIRCLE_LAYER})
    blk.add_lwpolyline([(0, 0), (2, 0), (2, 2), (0, 2)], close=True)
    blk.add_text("PUMP", height=0.3).set_placement((0, 2.4))
    msp = doc.modelspace()
    for origin, rotation in (((0, 0), 0), ((10, 0), 30), ((0, 10), 90)):
        msp.add_blockref("EQUIP", origin, dxfattribs={"rotation": rotation})
    msp.add_lwpolyline([(-3, -3), (20, -3), (20, 20), (-3, 20)], close=True)
    msp.add_text("FRAME NOTE", height=0.6).set_placement((-2, -2))
    doc.saveas(path)
    return path


def make_dxf_paperspace_only(path: str | Path) -> Path:
    """Modelspace EMPTY; ``Layout1`` holds an INSERT plus a TEXT. (Drop mechanism D2.)

    The legacy ``render_dxf`` plots ``doc.modelspace()`` only, so this file yields
    a blank PDF at exit 0 there. Expected-visible units on ``Layout1``: 2.
    """
    path = Path(path)
    doc = ezdxf.new("R2018", setup=True)
    blk = doc.blocks.new(name="PS_EQUIP")
    blk.add_line((0, 0), (5, 0))
    blk.add_circle((2, 2), 1)
    layout = doc.layouts.new("Layout1") if "Layout1" not in doc.layouts.names() else (
        doc.layouts.get("Layout1")
    )
    layout.add_blockref("PS_EQUIP", (10, 10))
    layout.add_text("PAPERSPACE NOTE", height=2).set_placement((10, 5))
    doc.saveas(path)
    return path


def make_dxf_frozen_block_children(path: str | Path) -> Path:
    """A block whose LINE is on a FROZEN layer and whose CIRCLE is on layer 0. (D3.)

    A frozen layer does not plot — that is correct CAD semantics — so the LINE must
    appear in **neither** the expected units nor the exploded-leaf count. Expected
    exploded leaves: 1 (the CIRCLE). A test expecting 2 has mis-implemented
    visibility resolution.
    """
    path = Path(path)
    doc = ezdxf.new("R2018", setup=True)
    doc.layers.add("PIPING")
    doc.layers.get("PIPING").freeze()
    blk = doc.blocks.new(name="FROZEN_EQUIP")
    blk.add_line((0, 0), (2, 0), dxfattribs={"layer": "PIPING"})
    blk.add_circle((1, 1), 0.5, dxfattribs={"layer": "0"})
    msp = doc.modelspace()
    msp.add_blockref("FROZEN_EQUIP", (0, 0))
    msp.add_lwpolyline([(-2, -2), (5, -2), (5, 5), (-2, 5)], close=True)
    doc.saveas(path)
    return path


def make_dxf_minsert(path: str | Path) -> Path:
    """A 3x4 ``MINSERT`` array of a two-entity block.

    ``virtual_entities()`` does not expand the array (2 leaves) while the frontend
    emits 24 ops — the measured proof that the F2 count check must be a one-sided
    bound and never an equality.
    """
    path = Path(path)
    doc = ezdxf.new("R2018", setup=True)
    blk = doc.blocks.new(name="ARRAY_CELL")
    blk.add_line((0, 0), (1, 0))
    blk.add_circle((0, 0), 0.3)
    msp = doc.modelspace()
    insert = msp.add_blockref("ARRAY_CELL", (0, 0))
    insert.dxf.row_count = 3
    insert.dxf.column_count = 4
    insert.dxf.row_spacing = 5
    insert.dxf.column_spacing = 5
    doc.saveas(path)
    return path


def make_dxf_insert_with_attrib(path: str | Path) -> Path:
    """A block with a LINE and an ATTDEF; the INSERT carries an auto-filled ATTRIB.

    ezdxf attributes the ATTRIB's ops to the **ATTRIB's own** handle, not the
    INSERT's, so an ATTRIB is a drawable unit in its own right.
    """
    path = Path(path)
    doc = ezdxf.new("R2018", setup=True)
    blk = doc.blocks.new(name="TAGGED")
    blk.add_line((0, 0), (2, 0))
    blk.add_attdef("TAG1", (0, 1), height=0.3)
    msp = doc.modelspace()
    insert = msp.add_blockref("TAGGED", (0, 0))
    insert.add_auto_attribs({"TAG1": "P-101"})
    doc.saveas(path)
    return path


def make_dxf_unrendered_dimension(path: str | Path) -> Path:
    """A DIMENSION that was never ``.render()``ed. (Drop mechanism D5.)

    ezdxf's frontend dies on it with a bare ``AttributeError`` from
    ``dimension.py``; the plot path must name it instead of showing a traceback.
    """
    path = Path(path)
    doc = ezdxf.new("R2018", setup=True)
    msp = doc.modelspace()
    msp.add_line((0, 0), (5, 0))
    msp.add_linear_dim(base=(0, -2), p1=(0, -3), p2=(5, -3))  # deliberately not rendered
    doc.saveas(path)
    return path


def make_dxf_empty(path: str | Path) -> Path:
    """A structurally valid DXF with nothing drawable anywhere.

    The legacy path writes a 1,294-byte blank PDF for this at exit 0, which the
    existing ``st_size > 0`` guard passes. The plot path must refuse it.
    """
    path = Path(path)
    ezdxf.new("R2018", setup=True).saveas(path)
    return path


def make_template_tags_dxf(path: str | Path) -> Path:
    """A DXF littered with vendor CAD-template artifacts + real content.

    Contains: a floating ATTDEF, TEXT '!GENTITLE-INSERT', MTEXT 'GEN-TITLE-BLOCK',
    TEXT 'GENST_01', plus a real 'PUMP 001' label and a real geometry line.
    Exercises strip_tags (must drop the 3 tags + ATTDEF, keep the real content).
    """
    path = Path(path)
    doc = ezdxf.new("R2018", setup=True)
    msp = doc.modelspace()
    msp.add_line((0, 0), (10, 0))  # real geometry
    msp.add_text("PUMP 001", height=2).set_placement((1, 1))  # real content
    # Template artifacts:
    msp.add_attdef(tag="GENTAG", insert=(2, 2), height=2)  # floating ATTDEF
    msp.add_text("!GENTITLE-INSERT", height=2).set_placement((3, 3))
    msp.add_mtext("GEN-TITLE-BLOCK").set_location((4, 4))
    msp.add_text("GENST_01", height=2).set_placement((5, 5))
    doc.saveas(path)
    return path
