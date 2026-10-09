from __future__ import annotations

from pathlib import Path

import ezdxf
import pytest

from technical_drawings_for_agents.components import load_component, place, to_geojson
from technical_drawings_for_agents.components.spec import ComponentSpecError


def test_from_dxf_expands_supplier_geometry_to_local_metres(tmp_path):
    dxf_path = tmp_path / "vendor_fixture.dxf"
    _write_vendor_dxf(dxf_path)
    spec_path = _write_spec(tmp_path, layers=["VENDOR"], min_length_m=0.15)

    component = load_component(spec_path)
    features = component.view("plan")

    assert len(features) == 3
    assert all(feature.from_dxf is None for feature in features)
    assert [feature.shape.kind for feature in features] == ["polyline", "polyline", "circle"]

    rectangle = features[0].shape
    assert rectangle.data["closed"] is True
    assert rectangle.data["points"] == pytest.approx(
        [(0.0, 0.0), (2.0, 0.0), (2.0, 1.0), (0.0, 1.0)]
    )

    long_line = features[1].shape
    assert long_line.data["closed"] is False
    assert long_line.data["points"] == pytest.approx([(0.0, 0.0), (0.0, 2.0)])

    circle = features[2].shape
    assert circle.data["center"] == pytest.approx((1.0, 0.5))
    assert circle.data["diameter"] == pytest.approx(0.5)


def test_from_dxf_layers_filter_selects_only_named_layers(tmp_path):
    dxf_path = tmp_path / "vendor_fixture.dxf"
    _write_vendor_dxf(dxf_path)
    spec_path = _write_spec(tmp_path, layers=["OTHER"], min_length_m=0.0)

    features = load_component(spec_path).view("plan")

    assert len(features) == 1
    assert features[0].shape.kind == "polyline"
    assert features[0].shape.data["points"] == pytest.approx([(0.0, 0.5), (0.5, 0.5)])


def test_from_dxf_place_and_geojson_use_expanded_features(tmp_path):
    dxf_path = tmp_path / "vendor_fixture.dxf"
    _write_vendor_dxf(dxf_path)
    spec_path = _write_spec(tmp_path, layers=["VENDOR"], min_length_m=0.15)
    component = load_component(spec_path)

    placed = place(component, "plan", origin=(1000.0, 2000.0), rotation_deg=90.0)

    assert placed[0].kind == "polygon"
    assert placed[0].coords == pytest.approx(
        [(1000.0, 2000.0), (1000.0, 2002.0), (999.0, 2002.0), (999.0, 2000.0)]
    )
    assert placed[1].kind == "polyline"
    assert placed[1].coords == pytest.approx([(1000.0, 2000.0), (998.0, 2000.0)])
    assert placed[2].kind == "circle"
    assert placed[2].coords == pytest.approx((999.5, 2001.0))
    assert placed[2].radius == pytest.approx(0.25)

    geojson = to_geojson(placed)

    assert geojson["type"] == "FeatureCollection"
    assert len(geojson["features"]) == 3
    assert [feature["geometry"]["type"] for feature in geojson["features"]] == [
        "Polygon",
        "LineString",
        "Polygon",
    ]


def test_from_dxf_missing_file_is_materialization_error(tmp_path):
    spec_path = _write_spec(tmp_path, file_name="missing.dxf")

    component = load_component(spec_path)

    with pytest.raises(ComponentSpecError, match="from_dxf file not found"):
        component.view("plan")


@pytest.mark.parametrize(
    ("from_dxf", "match"),
    [
        (
            """
file: vendor_fixture.dxf
region: [0, 0, 0, 1000]
units: mm
origin: [0, 0]
""",
            "x0 < x1",
        ),
        (
            """
file: vendor_fixture.dxf
region: [0, 0, 1000, 1000]
units: yd
origin: [0, 0]
""",
            "units",
        ),
        (
            """
file: vendor_fixture.dxf
region: [0, 0, 1000, 1000]
units: mm
origin: [0, 0]
include: [spline]
""",
            "include",
        ),
        (
            """
file: vendor_fixture.dxf
region: [0, 0, 1000, 1000]
units: mm
origin: [0, 0]
arc_segments: 3
""",
            "arc_segments",
        ),
    ],
)
def test_from_dxf_schema_validation_errors(tmp_path, from_dxf, match):
    spec_path = tmp_path / "bad.yaml"
    spec_path.write_text(
        f"""
component: {{id: BAD, name: Bad, units: m}}
views:
  plan:
    features:
      - role: vendor-geometry
        from_dxf:
{_indent(from_dxf, 10)}
""",
        encoding="utf-8",
    )

    with pytest.raises(ComponentSpecError, match=match):
        load_component(spec_path)


def _write_vendor_dxf(path: Path) -> None:
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    msp.add_lwpolyline(
        [(1000, 500), (3000, 500), (3000, 1500), (1000, 1500)],
        close=True,
        dxfattribs={"layer": "VENDOR"},
    )
    msp.add_line((1000, 500), (1000, 2500), dxfattribs={"layer": "VENDOR"})
    msp.add_line((1200, 500), (1250, 500), dxfattribs={"layer": "VENDOR"})
    msp.add_line((1000, 1000), (1500, 1000), dxfattribs={"layer": "OTHER"})
    msp.add_circle((2000, 1000), 250, dxfattribs={"layer": "VENDOR"})
    msp.add_line((3900, 2900), (4100, 2900), dxfattribs={"layer": "VENDOR"})
    doc.saveas(path)


def _write_spec(
    tmp_path: Path,
    file_name: str = "vendor_fixture.dxf",
    layers: list[str] | None = None,
    min_length_m: float = 0.15,
) -> Path:
    layers_yaml = "null" if layers is None else "[" + ", ".join(layers) + "]"
    spec_path = tmp_path / "component.yaml"
    spec_path.write_text(
        f"""
component:
  id: FROM-DXF
  name: From DXF
  units: m
views:
  plan:
    features:
      - role: vendor-geometry
        layer: EQUIP
        source_status: sourced
        tag: VENDOR
        from_dxf:
          file: {file_name}
          region: [0, 0, 4000, 3000]
          units: mm
          origin: [1000, 500]
          layers: {layers_yaml}
          min_length_m: {min_length_m}
""",
        encoding="utf-8",
    )
    return spec_path


def _indent(text: str, spaces: int) -> str:
    prefix = " " * spaces
    return "\n".join(prefix + line if line else line for line in text.strip().splitlines())
