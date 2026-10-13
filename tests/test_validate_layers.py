from __future__ import annotations

import shutil
from pathlib import Path

from technical_drawings_for_agents.layers import LayerSpec, LayerTable, default_layer_table
from technical_drawings_for_agents.validate import validate_dxf_layers


def _table(*layers: LayerSpec) -> LayerTable:
    return LayerTable(
        layers=layers
        or (
            LayerSpec(
                name="FDN",
                aci=3,
                lineweight_mm=0.35,
                description="Foundations, slabs, plinths",
            ),
        ),
        source=Path("test-layers.yaml"),
    )


def test_geometry_on_layer_zero_is_a_finding(tmp_path):
    from technical_drawings_for_agents.dxf import DxfBuilder

    dxf = DxfBuilder()
    dxf.line((0, 0), (1, 0), layer="FDN")
    dxf.line((0, 1), (1, 1), layer="FDN")
    dxf.line((0, 2), (1, 2), layer="0")
    out = dxf.save(tmp_path / "layer-zero.dxf")

    result = validate_dxf_layers(out, _table())

    assert result.ok is False
    assert len(result.problems) == 1
    assert "layer '0'" in result.problems[0]
    assert "1" in result.problems[0]
    assert "geometry on a declared layer" in result.checked


def test_dimension_arrowhead_blocks_on_layer_zero_are_not_a_finding(tmp_path):
    from technical_drawings_for_agents.dxf import DxfBuilder

    table = _table(LayerSpec(name="DIMENSIONS", aci=3, lineweight_mm=0.18))
    dxf = DxfBuilder(layer_table=table)
    dxf.linear_dim((0, 0), (1.2, 0), distance=-0.4, layer="DIMENSIONS")
    out = dxf.save(tmp_path / "dimension.dxf")

    result = validate_dxf_layers(out, table)

    assert result.ok is True


def test_undeclared_layer_in_an_exported_dxf_is_a_finding(tmp_path):
    from technical_drawings_for_agents.dxf import DxfBuilder

    dxf = DxfBuilder()
    dxf.line((0, 0), (1, 0), layer="EQUIPMENT")
    out = dxf.save(tmp_path / "undeclared.dxf")

    result = validate_dxf_layers(out, default_layer_table())

    assert len(result.problems) == 1
    assert "'EQUIPMENT'" in result.problems[0]
    assert "not declared" in result.problems[0]
    assert str(default_layer_table().source) in result.problems[0]


def test_validate_without_a_layer_table_reports_nothing(tmp_path):
    from technical_drawings_for_agents.dxf import DxfBuilder

    dxf = DxfBuilder()
    dxf.line((0, 0), (1, 0), layer="0")
    out = dxf.save(tmp_path / "legacy.dxf")

    result = validate_dxf_layers(out, table=None)

    assert result.ok is True
    assert result.checked == []


def test_a_drawing_dir_joins_the_layer_check_only_when_its_meta_names_a_table(tmp_path):
    """The opt-in seam, exercised end to end on the shipped example drawing.

    Without a ``layers:`` key the directory's DXFs are not checked at all — a
    drawing that has not opted in cannot fail a check that did not exist. With
    ``layers: default`` the same directory is checked, the shipped sheet passes
    (its only layer-0 geometry lives in the ``*D1`` / ``_ARCHTICK`` dimension
    blocks, which are excluded by design), and an added DXF on an undeclared
    layer is reported against its own file name.
    """

    from technical_drawings_for_agents.dxf import DxfBuilder
    from technical_drawings_for_agents.validate import validate_drawing_dir

    example = Path(__file__).resolve().parents[1] / "drawings" / "example" / "simple-section"
    work = tmp_path / "simple-section"
    shutil.copytree(example, work)

    without = validate_drawing_dir(work)
    assert not any("EXA-CIV-SEC-001.dxf" in check for check in without.checked)

    meta = work / "meta.yaml"
    meta.write_text(meta.read_text(encoding="utf-8") + "layers: default\n", encoding="utf-8")

    opted_in = validate_drawing_dir(work)
    assert "EXA-CIV-SEC-001.dxf: geometry on a declared layer" in opted_in.checked
    assert not [p for p in opted_in.problems if "EXA-CIV-SEC-001.dxf" in p]

    stray = DxfBuilder()
    stray.line((0, 0), (1, 0), layer="EQUIPMENT")
    stray.save(work / "out" / "STRAY.dxf")

    with_stray = validate_drawing_dir(work)
    problems = [p for p in with_stray.problems if p.startswith("STRAY.dxf:")]
    assert len(problems) == 1
    assert "'EQUIPMENT'" in problems[0]
    assert "not declared" in problems[0]
