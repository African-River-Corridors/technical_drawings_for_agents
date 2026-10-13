from __future__ import annotations

import ezdxf
import pytest

from technical_drawings_for_agents.describe import (
    DescribeError,
    compare_description,
    describe_dxf,
)


def _sheet_dxf(
    path,
    *,
    insunits: int = 6,
    view_height: float = 311.25,
    with_pseudo_viewport: bool = False,
):
    """A minimal one-sheet DXF with a real paper-space viewport.

    Defaults give the canonical case: metres in model space, a 249 mm-high
    viewport showing 311.25 m, which is 311.25 * 1000 / 249 = exactly 1:1250.

    `ezdxf.new(setup=True)` creates a default `Layout1`; it is removed so the
    fixture has exactly the one sheet the tests talk about.

    `with_pseudo_viewport` adds the id-1 VIEWPORT that real CAD files carry for
    the sheet itself. ezdxf does not write one, so without this the exclusion
    test would pass against a file that never had anything to exclude.

    Returns (path, viewport_id) — the id is assigned by ezdxf and is not 2, so
    hard-coding it in the tests would only encode a version detail.
    """
    doc = ezdxf.new(setup=True)
    doc.header["$INSUNITS"] = insunits
    msp = doc.modelspace()
    msp.add_line((0, 0), (10, 0), dxfattribs={"layer": "ANK-PIPE-OUT"})
    msp.add_line((0, 1), (10, 1), dxfattribs={"layer": "ANK-PIPE-OUT"})
    msp.add_circle((5, 5), radius=2, dxfattribs={"layer": "ANK-POND"})

    layout = doc.layouts.new("GA-01")
    viewport = layout.add_viewport(
        center=(200, 140),
        size=(341.2, 249.0),
        view_center_point=(5, 5),
        view_height=view_height,
    )
    viewport_id = int(viewport.dxf.id)
    if with_pseudo_viewport:
        pseudo = layout.add_viewport(
            center=(231, 163.35),
            size=(462.0, 326.7),
            view_center_point=(231, 163.35),
            view_height=326.7,
        )
        pseudo.dxf.id = 1
    layout.add_text("TITLE", dxfattribs={"layer": "ANK-SHEET-TEXT"}).set_placement((10, 10))
    doc.layouts.delete("Layout1")
    doc.saveas(path)
    return path, viewport_id


def test_describes_layers_layouts_and_the_actual_plot_scale(tmp_path):
    path, _ = _sheet_dxf(tmp_path / "sheet.dxf")

    description = describe_dxf(path)

    assert description.insunits_name == "m"
    assert description.modelspace_entities == 3
    assert dict(description.layers)["ANK-PIPE-OUT"] == 2
    assert dict(description.entity_types) == {"CIRCLE": 1, "LINE": 2}

    assert [layout.name for layout in description.layouts] == ["GA-01"]
    (layout,) = description.layouts
    (viewport,) = layout.viewports
    # The whole point: the scale is measured from viewport geometry, not read from
    # any text the drawing happens to carry.
    assert viewport.plot_scale == pytest.approx(1250.0)
    assert description.stated_scales == (pytest.approx(1250.0),)


def test_layout_pseudo_viewport_is_excluded(tmp_path):
    """Every paper-space layout carries a VIEWPORT with id 1 standing for the sheet
    itself. Its view_height equals its height, so counting it would report a
    meaningless 1:1 alongside every real scale."""
    path, vp_id = _sheet_dxf(tmp_path / "sheet.dxf", with_pseudo_viewport=True)

    description = describe_dxf(path)

    (layout,) = description.layouts
    # Only the real window survives, and the 1:1 the pseudo-viewport would have
    # contributed is absent.
    assert [vp.viewport_id for vp in layout.viewports] == [vp_id]
    assert all(vp.viewport_id != 1 for vp in layout.viewports)
    assert 1.0 not in description.stated_scales
    assert description.stated_scales == (pytest.approx(1250.0),)


def test_unitless_header_reports_no_scale_rather_than_guessing(tmp_path):
    """$INSUNITS 0 means model lengths have no unit, so no conversion to paper mm
    exists. Assuming metres would invent the number this module exists to verify."""
    path, _ = _sheet_dxf(tmp_path / "unitless.dxf", insunits=0)

    description = describe_dxf(path)

    (layout,) = description.layouts
    (viewport,) = layout.viewports
    assert viewport.plot_scale is None
    assert description.stated_scales == ()
    # The geometry is still reported — only the derived claim is withheld.
    assert viewport.paper_height_mm == pytest.approx(249.0)


def test_description_is_deterministic_across_reads(tmp_path):
    path, _ = _sheet_dxf(tmp_path / "sheet.dxf")

    assert describe_dxf(path).to_dict() == describe_dxf(path).to_dict()


def test_missing_file_raises_describe_error(tmp_path):
    with pytest.raises(DescribeError):
        describe_dxf(tmp_path / "absent.dxf")


# --------------------------------------------------------------------------- #
# comparison — a check that cannot fail is not a check
# --------------------------------------------------------------------------- #


def test_matching_expectation_yields_no_findings(tmp_path):
    path, vp_id = _sheet_dxf(tmp_path / "sheet.dxf")
    actual = describe_dxf(path).to_dict()

    findings = compare_description(
        {
            "insunits": 6,
            "modelspace_entities": 3,
            "layers": {"ANK-PIPE-OUT": 2},
            "layouts": [
                {
                    "name": "GA-01",
                    "viewports": [{"viewport_id": vp_id, "plot_scale": 1250.0}],
                }
            ],
        },
        actual,
    )

    assert findings == []


def test_a_sheet_that_plots_at_the_wrong_scale_is_caught(tmp_path):
    """The defect this exists for, reproduced against real geometry.

    A viewport built to show 288.0 m in 249 mm plots at 1:1156.6 — the 1:1157 of
    the original defect — while the sheet still claims 1:1250. Corrupting the input
    and watching the check fail is the only evidence the check works at all.
    """
    path, vp_id = _sheet_dxf(tmp_path / "wrong.dxf", view_height=288.0)
    actual = describe_dxf(path).to_dict()

    findings = compare_description(
        {
            "layouts": [
                {
                    "name": "GA-01",
                    "viewports": [{"viewport_id": vp_id, "plot_scale": 1250.0}],
                }
            ]
        },
        actual,
    )

    assert len(findings) == 1
    assert "expected 1:1250.0" in findings[0]
    assert "plots at 1:1156" in findings[0]


def test_lost_entities_are_named_with_their_layer(tmp_path):
    """Why entity counts rather than a pixel diff: the finding says which layer."""
    path, _ = _sheet_dxf(tmp_path / "sheet.dxf")
    actual = describe_dxf(path).to_dict()

    findings = compare_description({"layers": {"ANK-PIPE-OUT": 5}}, actual)

    assert findings == ["layers: 'ANK-PIPE-OUT' expected 5, found 2"]


def test_absent_layer_and_absent_layout_are_distinct_findings(tmp_path):
    path, _ = _sheet_dxf(tmp_path / "sheet.dxf")
    actual = describe_dxf(path).to_dict()

    findings = compare_description(
        {"layers": {"ANK-GONE": 1}, "layouts": [{"name": "GA-99"}]}, actual
    )

    assert "layers: 'ANK-GONE' expected 1, absent" in findings
    assert "layout 'GA-99': expected, absent" in findings


def test_partial_expectation_asserts_only_what_it_declares(tmp_path):
    """A golden file that had to be exhaustive would go stale on the first
    legitimate change and then be switched off. Silence on a key is not a pass
    for that key — it is an absence of assertion, which is the useful default."""
    path, _ = _sheet_dxf(tmp_path / "sheet.dxf")
    actual = describe_dxf(path).to_dict()

    assert compare_description({"insunits": 6}, actual) == []
    assert compare_description({}, actual) == []


def test_scale_tolerance_is_explicit_and_absolute(tmp_path):
    path, vp_id = _sheet_dxf(tmp_path / "sheet.dxf")
    actual = describe_dxf(path).to_dict()
    expected = {
        "layouts": [
            {"name": "GA-01", "viewports": [{"viewport_id": vp_id, "plot_scale": 1250.4}]}
        ]
    }

    assert compare_description(expected, actual, scale_tolerance=0.5) == []
    assert len(compare_description(expected, actual, scale_tolerance=0.1)) == 1


def test_expected_scale_against_an_unitless_drawing_is_a_finding(tmp_path):
    """Asking for a scale from a drawing that cannot express one must fail loudly,
    not pass because both sides are 'unknown'."""
    path, vp_id = _sheet_dxf(tmp_path / "unitless.dxf", insunits=0)
    actual = describe_dxf(path).to_dict()

    findings = compare_description(
        {
            "layouts": [
                {"name": "GA-01", "viewports": [{"viewport_id": vp_id, "plot_scale": 1250.0}]}
            ]
        },
        actual,
    )

    assert len(findings) == 1
    assert "not computable" in findings[0]


def test_block_resident_entities_are_counted_separately(tmp_path):
    """Modelspace alone can understate a drawing enormously.

    Measured on a real vendor sheet: 3,089 modelspace entities against 259,762
    inside block definitions. Reporting only the first would be a number a reader
    reasonably mistakes for "the size of the drawing".
    """
    path = tmp_path / "blocky.dxf"
    # setup=False: setup=True ships standard dimension-arrow blocks, which would
    # make the counts below about ezdxf's defaults rather than about this drawing.
    doc = ezdxf.new(setup=False)
    doc.header["$INSUNITS"] = 6
    block = doc.blocks.new(name="WIDGET")
    for i in range(7):
        block.add_line((0, i), (1, i))
    msp = doc.modelspace()
    msp.add_blockref("WIDGET", (0, 0))
    msp.add_line((0, 0), (1, 0))
    doc.saveas(path)

    description = describe_dxf(path)

    # Two INSERT-and-a-line in modelspace; seven lines living in the definition.
    assert description.modelspace_entities == 2
    assert description.block_resident_entities == 7
    assert description.block_count == 1


def test_block_resident_count_is_assertable(tmp_path):
    path, _ = _sheet_dxf(tmp_path / "sheet.dxf")
    actual = describe_dxf(path).to_dict()

    found = actual["block_resident_entities"]
    assert compare_description({"block_resident_entities": 99}, actual) == [
        f"block_resident_entities: expected 99, found {found}"
    ]
    # and the true value compares clean
    assert compare_description({"block_resident_entities": found}, actual) == []
