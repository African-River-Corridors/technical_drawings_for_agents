from __future__ import annotations

from pathlib import Path

import ezdxf
import pytest

from technical_drawings_for_agents.components import load_component
from technical_drawings_for_agents.components.spec import ComponentSpecError


def test_from_dxf_cleanup_rules_drop_and_reclassify_in_order(tmp_path):
    dxf_path = tmp_path / "cleanup_fixture.dxf"
    _write_cleanup_dxf(dxf_path)

    raw = load_component(_write_spec(tmp_path, name="raw.yaml")).view("plan")
    empty_cleanup = load_component(
        _write_spec(tmp_path, cleanup=[], name="empty.yaml")
    ).view("plan")

    assert len(raw) == 9
    assert empty_cleanup == raw
    assert [feature.dxf_layer for feature in raw].count("DIMS") == 2
    assert all(feature.layer == "EQUIP" for feature in raw)

    cleaned = load_component(
        _write_spec(tmp_path, cleanup=_cleanup_rule_items(), name="cleaned.yaml")
    ).view("plan")

    assert len(cleaned) == 3
    assert [feature.role for feature in cleaned] == [
        "vendor-geometry",
        "vendor-geometry",
        "manhole",
    ]
    assert [feature.layer for feature in cleaned] == ["EQUIP", "EQUIP", "MH"]
    assert {feature.dxf_layer for feature in cleaned} == {"WALLS", "FEATURES"}
    assert all(feature.role != "dimension" for feature in cleaned)

    wall_a, wall_b, manhole = cleaned
    assert wall_a.shape.kind == "polyline"
    assert wall_a.shape.data["points"] == pytest.approx([(-0.9, -0.9), (0.9, -0.9)])
    assert wall_b.shape.kind == "polyline"
    assert wall_b.shape.data["points"] == pytest.approx([(-0.9, -0.9), (-0.9, 0.9)])
    assert manhole.shape.kind == "circle"
    assert manhole.shape.data["center"] == pytest.approx((0.7, 0.7))
    assert manhole.shape.data["diameter"] == pytest.approx(0.2)
    assert manhole.tag == "MH-1"
    assert manhole.source_status == "verified"


def test_from_dxf_cleanup_rule_counts_by_prefix(tmp_path):
    _write_cleanup_dxf(tmp_path / "cleanup_fixture.dxf")
    rules = _cleanup_rule_items()

    counts = []
    for end in range(len(rules) + 1):
        cleanup = None if end == 0 else rules[:end]
        features = load_component(
            _write_spec(tmp_path, cleanup=cleanup, name=f"prefix_{end}.yaml")
        ).view("plan")
        counts.append(len(features))

    assert counts == [9, 8, 7, 6, 4, 3, 3, 3]


@pytest.mark.parametrize(
    ("cleanup", "match"),
    [
        (["- drop: squiggle"], "drop"),
        (["- drop: diagonal\n  extra: true"], "unknown key"),
        (
            [
                "- drop: outside\n"
                "  footprint_m: [2.0, 2.0]\n"
                "  region_local_m: [-1.0, -1.0, 1.0, 1.0]"
            ],
            "exactly one",
        ),
        (["- reclassify:\n    match: {kind: spline}\n    set: {role: thing}"], "kind"),
        (
            ["- reclassify:\n    match: {kind: circle}\n    set: {source_status: guessed}"],
            "source_status",
        ),
        (["- drop: shorter_than\n  length_m: -0.1"], "length_m"),
    ],
)
def test_from_dxf_cleanup_schema_validation_errors(tmp_path, cleanup, match):
    spec_path = _write_spec(tmp_path, cleanup=cleanup, name="bad.yaml")

    with pytest.raises(ComponentSpecError, match=match):
        load_component(spec_path)


def _write_cleanup_dxf(path: Path) -> None:
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    msp.add_line((-900, -900), (900, -900), dxfattribs={"layer": "WALLS"})
    msp.add_line((-900, -900), (-900, 900), dxfattribs={"layer": "WALLS"})
    msp.add_line((0, 0), (500, 500), dxfattribs={"layer": "WALLS"})
    msp.add_line((200, 200), (230, 200), dxfattribs={"layer": "WALLS"})
    msp.add_line((1200, 0), (1600, 0), dxfattribs={"layer": "WALLS"})
    msp.add_line((-500, 700), (500, 700), dxfattribs={"layer": "DIMS"})
    msp.add_circle((0, 700), 100, dxfattribs={"layer": "DIMS"})
    msp.add_line((400, 0), (600, 0), dxfattribs={"layer": "WALLS"})
    msp.add_circle((700, 700), 100, dxfattribs={"layer": "FEATURES"})
    doc.saveas(path)


def _write_spec(
    tmp_path: Path,
    cleanup: list[str] | None = None,
    name: str = "component.yaml",
) -> Path:
    cleanup_block = ""
    if cleanup is not None:
        cleanup_block = "          cleanup: []\n" if not cleanup else (
            "          cleanup:\n" + _indent("\n".join(cleanup), 12) + "\n"
        )

    spec_path = tmp_path / name
    spec_path.write_text(
        f"""
component:
  id: CLEANUP
  name: Cleanup
  units: m
views:
  plan:
    features:
      - role: vendor-geometry
        layer: EQUIP
        source_status: sourced
        tag: VENDOR
        from_dxf:
          file: cleanup_fixture.dxf
          region: [-1000, -1000, 2000, 2000]
          units: mm
          origin: [0, 0]
          include: [line, circle]
{cleanup_block}""",
        encoding="utf-8",
    )
    return spec_path


def _cleanup_rule_items() -> list[str]:
    return [
        "- drop: diagonal\n  angle_tol_deg: 3",
        "- drop: shorter_than\n  length_m: 0.05",
        "- drop: outside\n  footprint_m: [2.0, 2.0]\n  margin_m: 0.0",
        "- drop: layers\n  layers: [DIMS]",
        "- drop: region\n  region_local_m: [0.35, -0.05, 0.65, 0.05]\n  margin_m: 0.0",
        (
            "- reclassify:\n"
            "    match:\n"
            "      kind: circle\n"
            "      dxf_layer: FEATURES\n"
            "      r_min_m: 0.09\n"
            "      r_max_m: 0.11\n"
            "      region_local_m: [0.6, 0.6, 0.8, 0.8]\n"
            "    set:\n"
            "      role: manhole\n"
            "      layer: MH\n"
            "      tag: MH-1\n"
            "      source_status: verified"
        ),
        (
            "- reclassify:\n"
            "    match: {dxf_layer: DIMS}\n"
            "    set: {role: dimension, tag: SHOULD-NOT-APPEAR}"
        ),
    ]


def _indent(text: str, spaces: int) -> str:
    prefix = " " * spaces
    return "\n".join(prefix + line if line else line for line in text.splitlines())
