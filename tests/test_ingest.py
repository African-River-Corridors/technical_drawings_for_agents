"""Ingest pipeline: load/audit, improve (incl. inside blocks), isolate, and the
DxfBuilder block-insert round-trip. Vendor-content-free — synthetic DXFs only.
"""

from __future__ import annotations

import pytest

from technical_drawings_for_agents import DxfBuilder
from technical_drawings_for_agents.ingest import (
    apply_corrections,
    convert_dwg,
    find_oda,
    improve,
    isolate_sheet,
    load_clean,
    normalise_text,
    strip_tags,
)
from technical_drawings_for_agents.ingest import TEXT_TYPES

from .synthetic import (
    make_dxf_no_insert,
    make_dxf_with_insert,
    make_single_sheet_gapped_dxf,
    make_template_tags_dxf,
    make_two_sheet_dxf,
    make_two_sheet_titled_dxf,
)


def _all_text_colours(doc):
    """Every text entity's ACI colour, in modelspace AND block definitions."""
    colours = []
    for container in [doc.modelspace(), *list(doc.blocks)]:
        for e in container:
            if e.dxftype() in TEXT_TYPES:
                colours.append(e.dxf.color)
    return colours


def test_load_clean_audit(tmp_path):
    src = make_dxf_with_insert(tmp_path / "ins.dxf")
    doc, audit = load_clean(src)
    assert audit["inserts"] == 1
    assert audit["text"] >= 1  # the modelspace SHEET NOTE
    assert audit["block_definitions"] >= 1
    assert "by_type" in audit and audit["by_type"]


def test_improve_recolours_text_inside_blocks(tmp_path):
    # The block's "PUMP" text starts blue; improve must blacken it INSIDE the
    # block definition (the whole point of the fix), not only in modelspace.
    src = make_dxf_with_insert(tmp_path / "ins.dxf")
    doc, _ = load_clean(src)
    before = set(_all_text_colours(doc))
    assert 7 not in before or len(before) > 1  # sanity: some non-black text present

    result = improve(doc, units="mm", recolor_text_black=True, set_extents=True)

    after = _all_text_colours(doc)
    assert after and all(c == 7 for c in after), f"text not all black: {after}"
    assert result["text_recoloured"] >= 2  # block PUMP + modelspace SHEET NOTE
    # Units + extents applied.
    assert doc.header["$INSUNITS"] == 4  # mm
    assert "extents" in result


def test_normalise_text_only(tmp_path):
    src = make_dxf_with_insert(tmp_path / "ins.dxf")
    doc, _ = load_clean(src)
    n = normalise_text(doc, to_aci=7)
    assert n >= 2
    assert all(c == 7 for c in _all_text_colours(doc))


def test_isolate_sheet_keeps_one_cluster(tmp_path):
    src = make_two_sheet_dxf(tmp_path / "two.dxf")
    doc, _ = load_clean(src)
    before = len(doc.modelspace())
    # Guard window around the RIGHT cluster only (legacy explicit-window path).
    audit = isolate_sheet(doc, window=(900, -100, 1200, 300))
    assert audit["deleted"] >= 1
    assert audit["kept"] >= 1
    after = len(doc.modelspace())
    assert after < before
    # The surviving text should be RIGHT, not LEFT.
    texts = [e.dxf.text for e in doc.modelspace().query("TEXT")]
    assert "RIGHT" in texts and "LEFT" not in texts


def test_strip_tags_removes_template_artifacts(tmp_path):
    src = make_template_tags_dxf(tmp_path / "tags.dxf")
    doc, _ = load_clean(src)
    audit = strip_tags(doc)
    # ATTDEF + the three GEN* tags gone; real content survives.
    assert audit["attdefs"] == 1
    assert audit["template_tags"] == 3
    assert audit["total"] == 4
    msp = doc.modelspace()
    assert not list(msp.query("ATTDEF"))
    remaining = {e.dxf.text for e in msp.query("TEXT")}
    assert "PUMP 001" in remaining
    assert not any("GEN" in t for t in remaining)
    # MTEXT GEN-TITLE-BLOCK is gone too.
    assert not [e for e in msp.query("MTEXT") if "GEN" in e.text]


def test_isolate_adaptive_two_sheets_keeps_english(tmp_path):
    # Two genuine title-blocks -> isolate, keep the English (right) sheet.
    src = make_two_sheet_titled_dxf(tmp_path / "titled.dxf")
    doc, _ = load_clean(src)
    before = len(doc.modelspace())
    audit = isolate_sheet(doc)
    assert audit["mode"] == "isolate"
    assert audit["title_clusters"] >= 2
    assert audit["deleted"] >= 1
    after = len(doc.modelspace())
    assert after < before
    texts = [e.dxf.text for e in doc.modelspace().query("TEXT")]
    assert any("CLEAN WATER PUMP" in t for t in texts)  # English sheet kept
    assert not any("图号 SL-01" in t for t in texts)  # Chinese sheet dropped


def test_isolate_adaptive_single_gapped_sheet_kept_whole(tmp_path):
    # Regression: one sheet with a gapped view-group (the pump-GA case) has only
    # ONE title block -> keep the WHOLE drawing (never drop the plan view).
    src = make_single_sheet_gapped_dxf(tmp_path / "gapped.dxf")
    doc, _ = load_clean(src)
    before = len(doc.modelspace())
    # The right view-group is far in x — a naive gap-split would bisect + drop it.
    plan_lines_before = sum(
        1 for e in doc.modelspace().query("LINE") if e.dxf.start.x >= 900
    )
    assert plan_lines_before > 0  # sanity: there IS a far view-group
    audit = isolate_sheet(doc)
    assert audit["mode"] == "keep_all"
    assert audit["title_clusters"] == 1
    assert audit["deleted"] == 0
    after = len(doc.modelspace())
    assert after == before  # nothing dropped
    plan_lines_after = sum(
        1 for e in doc.modelspace().query("LINE") if e.dxf.start.x >= 900
    )
    assert plan_lines_after == plan_lines_before  # the plan view survives


def test_apply_corrections_replace_and_add(tmp_path):
    src = make_dxf_no_insert(tmp_path / "plain.dxf")
    doc, _ = load_clean(src)
    spec = {
        "corrections": [
            {
                "op": "replace_text",
                "find": "TYPICAL SECTION",
                "with": "TYPICAL SECTION A-A",
                "note": "clarify view label",
            },
            {
                "op": "add_text",
                "at": [2, 2],
                "text": "NOTE 1",
                "height": 0.3,
                "note": "from vendor packing list p.2 — VERIFY",
            },
        ]
    }
    audit = apply_corrections(doc, spec)
    assert audit["count"] == 2
    texts = [e.dxf.text for e in doc.modelspace().query("TEXT")]
    assert "TYPICAL SECTION A-A" in texts
    assert "NOTE 1" in texts


def test_apply_corrections_add_text_requires_note(tmp_path):
    src = make_dxf_no_insert(tmp_path / "plain.dxf")
    doc, _ = load_clean(src)
    # add_text without a sourcing note must be rejected (never invent a value).
    with pytest.raises(ValueError, match="note"):
        apply_corrections(doc, [{"op": "add_text", "at": [1, 1], "text": "X"}])


def test_apply_corrections_unknown_op_raises(tmp_path):
    src = make_dxf_no_insert(tmp_path / "plain.dxf")
    doc, _ = load_clean(src)
    with pytest.raises(ValueError, match="unknown correction op"):
        apply_corrections(doc, [{"op": "delete_everything"}])


def test_apply_corrections_from_yaml_file(tmp_path):
    src = make_dxf_no_insert(tmp_path / "plain.dxf")
    doc, _ = load_clean(src)
    yaml_path = tmp_path / "corrections.yaml"
    yaml_path.write_text(
        "drawing: test\n"
        "source: synthetic.dxf\n"
        "corrections:\n"
        "  - op: replace_text\n"
        "    find: TYPICAL SECTION\n"
        "    with: SECTION A-A\n"
        "    note: normalise label\n",
        encoding="utf-8",
    )
    audit = apply_corrections(doc, yaml_path)
    assert audit["count"] == 1
    texts = [e.dxf.text for e in doc.modelspace().query("TEXT")]
    assert "SECTION A-A" in texts


def test_dxfbuilder_block_insert_round_trip(tmp_path):
    # Author a "vendor" DXF, then insert it as a block via DxfBuilder and prove
    # the geometry survives a save/reload cycle.
    vendor = make_dxf_no_insert(tmp_path / "vendor.dxf")

    b = DxfBuilder(units="m")
    name = b.add_block_from_dxf(vendor, name="VENDOR")
    assert name == "VENDOR"
    assert "VENDOR" in b.doc.blocks
    # Block definition carries the vendor geometry (a LINE + an LWPOLYLINE).
    block_types = {e.dxftype() for e in b.doc.blocks.get("VENDOR")}
    assert "LINE" in block_types

    b.insert("VENDOR", (100, 50), scale=2.0, rotation=90.0)
    out = b.save(tmp_path / "layout.dxf")

    doc = DxfBuilder.read_dxf(out)
    inserts = doc.modelspace().query("INSERT")
    assert len(inserts) == 1
    ins = inserts[0]
    assert ins.dxf.name == "VENDOR"
    assert ins.dxf.insert.x == pytest.approx(100)
    assert ins.dxf.xscale == pytest.approx(2.0)
    assert ins.dxf.rotation == pytest.approx(90.0)


def test_load_clean_recover_fallback(tmp_path):
    # A perfectly valid DXF still loads through load_clean (recover path is the
    # fallback; here we just prove the happy path + audit on a no-insert file).
    src = make_dxf_no_insert(tmp_path / "plain.dxf")
    doc, audit = load_clean(src)
    assert audit["inserts"] == 0
    assert audit["entities"] >= 2


@pytest.mark.skipif(find_oda() is None, reason="ODA File Converter not installed (proprietary)")
def test_convert_dwg_smoke(tmp_path):
    # ODA is proprietary + absent on CI. When present, we can't synthesise a
    # real DWG without AutoCAD, so this only asserts the wrapper raises cleanly
    # on a missing input rather than committing any vendor DWG.
    with pytest.raises((FileNotFoundError, RuntimeError)):
        convert_dwg(tmp_path / "does-not-exist", tmp_path / "out")
