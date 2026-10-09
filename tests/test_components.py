from __future__ import annotations

from pathlib import Path

import pytest

from technical_drawings_for_agents import DxfBuilder, ViewBox
from technical_drawings_for_agents.components import load_component, place, to_dxf, to_geojson, to_svg
from technical_drawings_for_agents.components.spec import ComponentSpecError


EXAMPLE = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "technical_drawings_for_agents"
    / "components"
    / "examples"
    / "packaged_unit.yaml"
)


def test_load_component_example():
    component = load_component(EXAMPLE)

    assert component.id == "EXAMPLE-PACKAGED-UNIT"
    assert component.name == "Example packaged unit"
    assert component.units == "m"
    assert len(component.view("plan")) == 6
    assert len(component.view("section")) == 2
    assert component.view("plan")[0].layer == "FDN"
    assert component.view("plan")[0].hatch == "concrete"


@pytest.mark.parametrize(
    ("body", "match"),
    [
        (
            """
component: {id: BAD, name: Bad, units: ft}
views: {plan: {features: []}}
""",
            "units",
        ),
        (
            """
component: {id: BAD, name: Bad, units: m}
views:
  plan:
    features:
      - {role: raft}
""",
            "shape",
        ),
        (
            """
component: {id: BAD, name: Bad, units: m}
views:
  plan:
    features:
      - role: raft
        shape:
          rect: {size: [2, 1], center: [0, 0]}
          point: [0, 0]
""",
            "exactly one",
        ),
        (
            """
component: {id: BAD, name: Bad, units: m}
views:
  plan:
    features:
      - role: raft
        source_status: guessed
        shape:
          rect: {size: [2, 1], center: [0, 0]}
""",
            "source_status",
        ),
        (
            """
component: {id: BAD, name: Bad, units: m}
views:
  plan:
    features:
      - role: raft
        shape:
          rect: {size: [2], center: [0, 0]}
""",
            "coordinate",
        ),
    ],
)
def test_load_component_validation_errors(tmp_path, body, match):
    path = tmp_path / "bad.yaml"
    path.write_text(body, encoding="utf-8")

    with pytest.raises(ComponentSpecError, match=match):
        load_component(path)


def test_place_rotates_rect_about_anchor_then_translates(tmp_path):
    path = tmp_path / "rect.yaml"
    path.write_text(
        """
component: {id: RECT, name: Rect, units: m}
views:
  plan:
    features:
      - role: raft
        shape:
          rect: {size: [4, 2], center: [0, 0]}
""",
        encoding="utf-8",
    )
    component = load_component(path)

    placed = place(component, "plan", origin=(10.0, 20.0), rotation_deg=90.0)

    assert placed[0].kind == "polygon"
    assert placed[0].coords == pytest.approx(
        [(11.0, 18.0), (11.0, 22.0), (9.0, 22.0), (9.0, 18.0)]
    )


def test_to_geojson_structure_and_properties():
    placed = place(load_component(EXAMPLE), "plan", origin=(100.0, 200.0))

    geojson = to_geojson(placed, crs="EPSG:32630", instance={"tag": "PKG-01"})

    assert geojson["type"] == "FeatureCollection"
    assert geojson["crs"] == {"type": "name", "properties": {"name": "EPSG:32630"}}
    assert len(geojson["features"]) == 6
    assert {f["geometry"]["type"] for f in geojson["features"]} == {
        "LineString",
        "Point",
        "Polygon",
    }
    raft = geojson["features"][0]
    assert raft["properties"]["role"] == "raft"
    assert raft["properties"]["layer"] == "FDN"
    assert raft["properties"]["source_status"] == "sourced"
    assert raft["properties"]["hatch"] == "concrete"
    assert raft["properties"]["component"] == "EXAMPLE-PACKAGED-UNIT"
    assert raft["properties"]["tag"] == "PKG-01"


def test_to_svg_uses_existing_primitives():
    placed = place(load_component(EXAMPLE), "plan", origin=(0.0, 0.0))
    vb = ViewBox(-4, 4, -3, 3, 800, 600, padding=40)

    elements = to_svg(placed, vb)

    assert any(element.startswith("<polygon") for element in elements)
    assert any(element.startswith("<line") for element in elements)
    assert any(element.startswith("<circle") for element in elements)
    assert any(element.startswith("<text") for element in elements)
    assert any("stroke-dasharray" in element for element in elements)


def test_to_dxf_writes_expected_entities():
    placed = place(load_component(EXAMPLE), "plan", origin=(0.0, 0.0))
    dxf = DxfBuilder(units="m")

    to_dxf(placed, dxf)

    assert len(dxf.msp.query("LWPOLYLINE")) == 2
    assert len(dxf.msp.query("LINE")) == 3
    assert len(dxf.msp.query("TEXT")) == 2
    assert len(list(dxf.msp)) == 7
