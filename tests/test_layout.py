from __future__ import annotations

import json
import math
from pathlib import Path

import pytest
import yaml

from technical_drawings_for_agents.components.layout import (
    LayoutError,
    build_layout,
    check_layout,
    check_register,
    derive_placements,
    dump_placements_register,
    footprint_pose,
    layout_instance_properties,
    load_layout,
    snap_groups,
)

EXAMPLE_SPEC = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "technical_drawings_for_agents"
    / "components"
    / "examples"
    / "packaged_unit.yaml"
)


def _rect(cx: float, cy: float, long_m: float, short_m: float, rot_deg: float) -> list[list[float]]:
    """A closed GeoJSON ring for a rotated rectangle, long axis at rot_deg."""

    angle = math.radians(rot_deg)
    cos_a, sin_a = math.cos(angle), math.sin(angle)
    half_long, half_short = long_m / 2.0, short_m / 2.0
    ring = []
    for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
        lx, ly = sx * half_long, sy * half_short
        ring.append([cx + lx * cos_a - ly * sin_a, cy + lx * sin_a + ly * cos_a])
    ring.append(list(ring[0]))
    return ring


def _geojson(*features: tuple[str, list[list[float]], str | None]) -> dict:
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"type": type_name, "tag": tag},
                "geometry": {"type": "Polygon", "coordinates": [ring]},
            }
            for type_name, ring, tag in features
        ],
    }


def _write_layout(
    tmp_path: Path,
    placements: object,
    checks: list[dict] | None = None,
    editor: dict | None = None,
    types: list[str] | None = None,
) -> Path:
    """Write a layout config whose components.root holds one symlinked example spec.

    ``types`` names extra placement types to map onto the same example spec, for
    fixtures that mirror a real multi-type site.
    """

    root = tmp_path / "components"
    root.mkdir(parents=True, exist_ok=True)
    (root / "unit.yaml").write_text(EXAMPLE_SPEC.read_text(encoding="utf-8"), encoding="utf-8")

    config = {
        "layout": {"id": "TEST-SITE", "crs": "EPSG:32630"},
        "components": {
            "root": "components",
            "types": {name: ["unit.yaml"] for name in sorted(types or ["unit"])},
        },
        "placements": placements,
    }
    if checks is not None:
        config["checks"] = checks
    if editor is not None:
        config["editor"] = editor
    path = tmp_path / "layout.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# pose derivation
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("rot", [0.0, 12.5, 40.0, 89.9, 130.0, 179.0])
def test_footprint_pose_round_trips_centroid_rotation_and_size(rot):
    ring = _rect(788609.589, 322341.151, 8.3, 5.3, rot)

    (cx, cy), rotation, (long_m, short_m) = footprint_pose(ring)

    assert cx == pytest.approx(788609.589, abs=1e-6)
    assert cy == pytest.approx(322341.151, abs=1e-6)
    assert rotation == pytest.approx(rot % 180.0, abs=1e-6)
    assert long_m == pytest.approx(8.3, abs=1e-9)
    assert short_m == pytest.approx(5.3, abs=1e-9)


def test_footprint_pose_normalises_reverse_bearing_to_same_pose():
    """A rectangle has no front — 40° and 220° are the same pose."""

    _, forward, _ = footprint_pose(_rect(0, 0, 8.3, 5.3, 40.0))
    _, reverse, _ = footprint_pose(_rect(0, 0, 8.3, 5.3, 220.0))

    assert forward == pytest.approx(reverse, abs=1e-9)


def test_footprint_pose_rejects_non_rectangle():
    with pytest.raises(LayoutError, match="4-corner rectangle"):
        footprint_pose([[0, 0], [1, 0], [1, 1], [0.5, 1.5], [0, 1], [0, 0]])


# --------------------------------------------------------------------------- #
# register derivation
# --------------------------------------------------------------------------- #


def test_derive_placements_reads_type_tag_pose_and_size():
    geojson = _geojson(("unit", _rect(100.0, 200.0, 8.3, 5.3, 40.0), "STA-A"))

    instances = derive_placements(geojson)

    assert instances == [
        {
            "type": "unit",
            "origin_utm": [100.0, 200.0],
            "rotation_deg": 40.0,
            "size_m": [8.3, 5.3],
            "tag": "STA-A",
        }
    ]


def test_derive_placements_skips_declared_parked_zones():
    geojson = _geojson(
        ("unit", _rect(100.0, 200.0, 8.3, 5.3, 0.0), "placed"),
        ("unit", _rect(500.0, 900.0, 8.3, 5.3, 0.0), "parked"),
    )

    instances = derive_placements(
        geojson, exclude=[{"name": "palette row", "bbox": [400, 850, 600, 950]}]
    )

    assert [i["tag"] for i in instances] == ["placed"]


def test_derive_placements_errors_on_missing_type_rather_than_skipping():
    """An unclassified footprint is a defect, not something to silently drop."""

    geojson = _geojson(("unit", _rect(0, 0, 2, 1, 0), None))
    geojson["features"][0]["properties"].pop("type")

    with pytest.raises(LayoutError, match="missing 'type'"):
        derive_placements(geojson)


def test_derive_placements_errors_on_non_polygon_geometry():
    geojson = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"type": "unit"},
                "geometry": {"type": "Point", "coordinates": [1, 2]},
            }
        ],
    }

    with pytest.raises(LayoutError, match="expected Polygon"):
        derive_placements(geojson)


def test_dump_placements_register_is_reloadable_and_marked_generated():
    instances = derive_placements(_geojson(("unit", _rect(1, 2, 8.3, 5.3, 40.0), "T")))

    text = dump_placements_register(instances, source="export.geojson", crs="EPSG:32630")

    assert "do NOT hand-edit" in text
    assert "export.geojson" in text
    assert yaml.safe_load(text)["instances"] == instances


# --------------------------------------------------------------------------- #
# config loading
# --------------------------------------------------------------------------- #


def test_load_layout_accepts_inline_placements(tmp_path):
    config = _write_layout(
        tmp_path, [{"type": "unit", "origin_utm": [10.0, 20.0], "rotation_deg": 40.0}]
    )

    layout = load_layout(config)

    assert layout.id == "TEST-SITE"
    assert layout.crs == "EPSG:32630"
    assert len(layout.placements) == 1
    assert layout.placements[0].rotation_deg == 40.0
    assert layout.register is None


def test_load_layout_reads_an_external_register(tmp_path):
    config = _write_layout(tmp_path, "placements.yaml")
    (tmp_path / "placements.yaml").write_text(
        yaml.safe_dump({"instances": [{"type": "unit", "origin_utm": [0, 0]}]}), encoding="utf-8"
    )

    layout = load_layout(config)

    assert len(layout.placements) == 1
    assert layout.register == (tmp_path / "placements.yaml").resolve()


def test_load_layout_rejects_unmapped_placement_type(tmp_path):
    config = _write_layout(tmp_path, [{"type": "small-transformer", "origin_utm": [0, 0]}])

    with pytest.raises(LayoutError, match="no components.types entry: small-transformer"):
        load_layout(config)


def test_load_layout_rejects_missing_component_spec(tmp_path):
    config = _write_layout(tmp_path, [{"type": "unit", "origin_utm": [0, 0]}])
    data = yaml.safe_load(config.read_text(encoding="utf-8"))
    data["components"]["types"]["unit"] = ["nope.yaml"]
    config.write_text(yaml.safe_dump(data), encoding="utf-8")

    with pytest.raises(LayoutError, match="spec not found"):
        load_layout(config)


def test_load_layout_rejects_unknown_check_kind(tmp_path):
    config = _write_layout(
        tmp_path, [{"type": "unit", "origin_utm": [0, 0]}], checks=[{"check": "vibes"}]
    )

    with pytest.raises(LayoutError, match="checks\\[0\\].check must be one of"):
        load_layout(config)


def test_load_layout_rejects_missing_origin(tmp_path):
    config = _write_layout(tmp_path, [{"type": "unit", "rotation_deg": 0}])

    with pytest.raises(LayoutError, match="missing origin_utm"):
        load_layout(config)


# --------------------------------------------------------------------------- #
# build
# --------------------------------------------------------------------------- #


def test_build_layout_places_every_spec_for_every_placement(tmp_path):
    config = _write_layout(
        tmp_path,
        [
            {"type": "unit", "origin_utm": [0.0, 0.0], "rotation_deg": 0.0, "tag": "A"},
            {"type": "unit", "origin_utm": [50.0, 0.0], "rotation_deg": 90.0, "tag": "B"},
        ],
    )
    layout = load_layout(config)

    placed = build_layout(layout)

    assert len(placed) == 12  # 6 plan features per instance
    assert {placement.tag for placement, _ in placed} == {"A", "B"}


def test_build_layout_rotation_moves_geometry_into_world_coords(tmp_path):
    config = _write_layout(
        tmp_path, [{"type": "unit", "origin_utm": [1000.0, 2000.0], "rotation_deg": 90.0}]
    )
    layout = load_layout(config)

    placed = build_layout(layout)
    raft = next(feature for _, feature in placed if feature.role == "raft")
    xs = [x for x, _ in raft.coords]
    ys = [y for _, y in raft.coords]

    # example raft is 6.0 x 4.0; rotated 90° the world extents swap
    assert max(xs) - min(xs) == pytest.approx(4.0, abs=1e-9)
    assert max(ys) - min(ys) == pytest.approx(6.0, abs=1e-9)
    assert sum(xs) / 4 == pytest.approx(1000.0, abs=1e-9)
    assert sum(ys) / 4 == pytest.approx(2000.0, abs=1e-9)


def test_build_layout_reports_a_missing_view_loudly(tmp_path):
    config = _write_layout(tmp_path, [{"type": "unit", "origin_utm": [0, 0]}])
    layout = load_layout(config)

    with pytest.raises(LayoutError, match="no 'elevation' view"):
        build_layout(layout, view="elevation")


# --------------------------------------------------------------------------- #
# checks
# --------------------------------------------------------------------------- #


def test_parallel_check_catches_rafts_out_of_parallel(tmp_path):
    """The live Basin case: two rafts placed 2.7° apart with a 1° tolerance."""

    config = _write_layout(
        tmp_path,
        [
            {"type": "unit", "origin_utm": [0.0, 0.0], "rotation_deg": 38.9, "tag": "STA-A"},
            {"type": "unit", "origin_utm": [0.0, 20.0], "rotation_deg": 41.6, "tag": "STA-B"},
        ],
        checks=[{"check": "parallel", "within": "unit", "tol_deg": 1.0}],
    )

    findings = check_layout(load_layout(config))

    assert len(findings) == 1
    assert findings[0].check == "parallel"
    assert findings[0].severity == "error"
    assert "2.70° off STA-A" in findings[0].message


def test_parallel_check_passes_when_snapped(tmp_path):
    config = _write_layout(
        tmp_path,
        [
            {"type": "unit", "origin_utm": [0.0, 0.0], "rotation_deg": 40.25},
            {"type": "unit", "origin_utm": [0.0, 20.0], "rotation_deg": 40.25},
        ],
        checks=[{"check": "parallel", "within": "unit", "tol_deg": 1.0}],
    )

    assert check_layout(load_layout(config)) == []


def test_clear_spacing_check_measures_the_real_gap_between_rotated_rects(tmp_path):
    """Two 8.3 x 5.3 rafts, long axes parallel, centres 7.3 m apart across the short
    axis => 2.0 m clear. Nudged to 6.8 m apart the 2.0 m rule must fail."""

    def placements(centre_offset: float) -> list[dict]:
        angle = math.radians(40.0)
        # offset perpendicular to the long axis
        dx = -math.sin(angle) * centre_offset
        dy = math.cos(angle) * centre_offset
        return [
            {
                "type": "unit",
                "origin_utm": [0.0, 0.0],
                "rotation_deg": 40.0,
                "size_m": [8.3, 5.3],
                "tag": "A",
            },
            {
                "type": "unit",
                "origin_utm": [dx, dy],
                "rotation_deg": 40.0,
                "size_m": [8.3, 5.3],
                "tag": "B",
            },
        ]

    ok = _write_layout(
        tmp_path / "ok",
        placements(7.3),
        checks=[{"check": "clear-spacing", "within": "unit", "min_m": 2.0}],
    )
    tight = _write_layout(
        tmp_path / "tight",
        placements(6.8),
        checks=[{"check": "clear-spacing", "within": "unit", "min_m": 2.0}],
    )

    assert check_layout(load_layout(ok)) == []
    findings = check_layout(load_layout(tight))
    assert len(findings) == 1
    assert findings[0].check == "clear-spacing"
    assert "1.500 m" in findings[0].message


def test_no_overlap_check_catches_overlapping_footprints(tmp_path):
    config = _write_layout(
        tmp_path,
        [
            {"type": "unit", "origin_utm": [0, 0], "rotation_deg": 0, "size_m": [8.3, 5.3]},
            {"type": "unit", "origin_utm": [2, 1], "rotation_deg": 30, "size_m": [8.3, 5.3]},
        ],
        checks=[{"check": "no-overlap"}],
    )

    findings = check_layout(load_layout(config))

    assert [f.check for f in findings] == ["no-overlap"]


def test_no_overlap_check_passes_for_separated_footprints(tmp_path):
    config = _write_layout(
        tmp_path,
        [
            {"type": "unit", "origin_utm": [0, 0], "rotation_deg": 0, "size_m": [8.3, 5.3]},
            {"type": "unit", "origin_utm": [40, 40], "rotation_deg": 30, "size_m": [8.3, 5.3]},
        ],
        checks=[{"check": "no-overlap"}],
    )

    assert check_layout(load_layout(config)) == []


def test_within_envelope_check_flags_corners_outside(tmp_path):
    config = _write_layout(
        tmp_path,
        [
            {"type": "unit", "origin_utm": [5, 5], "rotation_deg": 0, "size_m": [8.3, 5.3]},
            {"type": "unit", "origin_utm": [99, 99], "rotation_deg": 0, "size_m": [8.3, 5.3]},
        ],
        checks=[{"check": "within-envelope", "bbox": [0, 0, 20, 20], "severity": "warn"}],
    )

    findings = check_layout(load_layout(config))

    assert len(findings) == 1
    assert findings[0].severity == "warn"
    assert "4 of 4 footprint corners" in findings[0].message


def test_spacing_check_without_size_is_a_loud_error_not_a_guess(tmp_path):
    config = _write_layout(
        tmp_path,
        [
            {"type": "unit", "origin_utm": [0, 0]},
            {"type": "unit", "origin_utm": [3, 0]},
        ],
        checks=[{"check": "clear-spacing", "within": "unit", "min_m": 2.0}],
    )

    with pytest.raises(LayoutError, match="has no size_m"):
        check_layout(load_layout(config))


def test_check_within_rejects_unknown_type(tmp_path):
    config = _write_layout(
        tmp_path,
        [{"type": "unit", "origin_utm": [0, 0]}],
        checks=[{"check": "parallel", "within": "clarifier"}],
    )

    with pytest.raises(LayoutError, match="unknown type"):
        check_layout(load_layout(config))


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def test_cli_layout_round_trips_geojson_export_to_register_and_emits(tmp_path, capsys):
    from technical_drawings_for_agents.cli import main

    config = _write_layout(
        tmp_path,
        "placements.yaml",
        checks=[{"check": "parallel", "within": "unit", "tol_deg": 1.0}],
        editor={"parked": [{"name": "palette", "bbox": [900, 900, 1100, 1100]}]},
    )
    export = tmp_path / "export.geojson"
    export.write_text(
        json.dumps(
            _geojson(
                ("unit", _rect(0.0, 0.0, 8.3, 5.3, 40.25), "STA-A"),
                ("unit", _rect(0.0, 7.3, 8.3, 5.3, 40.25), "STA-B"),
                ("unit", _rect(1000.0, 1000.0, 8.3, 5.3, 0.0), "palette-item"),
            )
        ),
        encoding="utf-8",
    )
    out = tmp_path / "layout.geojson"

    code = main(
        [
            "layout",
            str(config),
            "--from-geojson",
            str(export),
            "--emit",
            "geojson",
            "--out",
            str(out),
        ]
    )

    assert code == 0
    register = yaml.safe_load((tmp_path / "placements.yaml").read_text(encoding="utf-8"))
    assert [i["tag"] for i in register["instances"]] == ["STA-A", "STA-B"]  # palette excluded
    emitted = json.loads(out.read_text(encoding="utf-8"))
    assert emitted["crs"]["properties"]["name"] == "EPSG:32630"
    assert {f["properties"]["placed_tag"] for f in emitted["features"]} == {"STA-A", "STA-B"}
    assert "layout checks: clean" in capsys.readouterr().out


def test_instance_tag_does_not_clobber_a_component_features_own_tag(tmp_path):
    """Instance properties override feature properties downstream, so the
    instance tag must land on its own key or every nozzle tag is destroyed."""

    from technical_drawings_for_agents.components import to_geojson

    config = _write_layout(
        tmp_path, [{"type": "unit", "origin_utm": [0, 0], "tag": "STA-A"}]
    )
    layout = load_layout(config)
    placed = build_layout(layout)

    nozzle_placement, nozzle = next((p, f) for p, f in placed if f.role == "nozzle")
    properties = to_geojson(
        [nozzle], instance=layout_instance_properties(nozzle_placement)
    )["features"][0]["properties"]

    assert properties["tag"] == "N1"          # the component's own tag survives
    assert properties["placed_tag"] == "STA-A"  # and the instance tag is still there


def test_an_id_never_leaks_into_emitted_feature_properties(tmp_path):
    """The identity is instance metadata, so it lands on `placed_id`, never on `id`.

    Same trap as the tag above: instance properties override feature properties
    in emit, so a bare `id` key would clobber every component feature's own.
    """

    from technical_drawings_for_agents.components import to_geojson

    config = _write_layout(
        tmp_path, [{"type": "unit", "origin_utm": [0, 0], "tag": "STA-A", "id": "RAFT-01"}]
    )
    layout = load_layout(config)
    placed = build_layout(layout)

    nozzle_placement, nozzle = next((p, f) for p, f in placed if f.role == "nozzle")
    properties = to_geojson(
        [nozzle], instance=layout_instance_properties(nozzle_placement)
    )["features"][0]["properties"]

    assert properties["tag"] == "N1"
    assert properties["placed_tag"] == "STA-A"
    assert properties["placed_id"] == "RAFT-01"
    assert "id" not in properties


# --------------------------------------------------------------------------- #
# group snap
# --------------------------------------------------------------------------- #

BASIN_RAFTS = [
    {
        "type": "unit",
        "origin_utm": [788603.652, 322347.176],
        "rotation_deg": 38.876,
        "size_m": [8.3, 5.3],
        "tag": "STA-A",
    },
    {
        "type": "unit",
        "origin_utm": [788608.164, 322341.538],
        "rotation_deg": 41.627,
        "size_m": [8.3, 5.3],
        "tag": "STA-B",
    },
]
RAFT_SNAP = [
    {
        "name": "FA-130 rafts",
        "within": "unit",
        "snap": {"bearing": "mean", "pitch": "spec", "clear_m": 2.0},
    }
]
RAFT_CHECKS = [
    {"check": "parallel", "within": "unit", "tol_deg": 1.0},
    {"check": "clear-spacing", "within": "unit", "min_m": 2.0, "tol_m": 0.05},
]


def _write_snap_layout(tmp_path, placements, groups, checks=RAFT_CHECKS):
    config = _write_layout(tmp_path, placements, checks=checks)
    data = yaml.safe_load(config.read_text(encoding="utf-8"))
    data["groups"] = groups
    config.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return config


def test_snap_makes_the_failing_basin_case_pass_by_construction(tmp_path):
    """The real defect: rafts 2.75° out of parallel at 1.725 m against a 2.0 m spec."""

    config = _write_snap_layout(tmp_path, BASIN_RAFTS, RAFT_SNAP)
    layout = load_layout(config)

    assert len(check_layout(layout)) == 2  # both rules fail as placed

    snapped, report = snap_groups(layout)

    assert check_layout(snapped) == []
    bearings = {round(p.rotation_deg, 6) for p in snapped.placements}
    assert len(bearings) == 1  # one common bearing
    assert 38.876 < bearings.pop() < 41.627  # between the two as-placed bearings
    assert any("moved" in line for line in report)


def test_snap_holds_the_spec_pitch_exactly(tmp_path):
    config = _write_snap_layout(tmp_path, BASIN_RAFTS, RAFT_SNAP)

    snapped, _ = snap_groups(load_layout(config))
    a, b = snapped.placements

    # centre-to-centre = short edge (5.3) + clear (2.0)
    assert math.dist(a.origin, b.origin) == pytest.approx(7.3, abs=1e-9)


def test_snap_preserves_the_group_centroid(tmp_path):
    """The human decides where the group goes; the rule only fixes its internals."""

    config = _write_snap_layout(tmp_path, BASIN_RAFTS, RAFT_SNAP)
    layout = load_layout(config)
    before = (
        sum(p.origin[0] for p in layout.placements) / 2,
        sum(p.origin[1] for p in layout.placements) / 2,
    )

    snapped, _ = snap_groups(layout)
    after = (
        sum(p.origin[0] for p in snapped.placements) / 2,
        sum(p.origin[1] for p in snapped.placements) / 2,
    )

    assert after[0] == pytest.approx(before[0], abs=1e-9)
    assert after[1] == pytest.approx(before[1], abs=1e-9)


def test_snap_order_north_to_south_keeps_the_northern_member_north(tmp_path):
    config = _write_snap_layout(tmp_path, BASIN_RAFTS, RAFT_SNAP)

    snapped, _ = snap_groups(load_layout(config))
    by_tag = {p.tag: p for p in snapped.placements}

    # STA-A was the northern raft as placed and must stay northern
    assert by_tag["STA-A"].origin[1] > by_tag["STA-B"].origin[1]


def test_snap_reports_every_move_rather_than_correcting_silently(tmp_path):
    config = _write_snap_layout(tmp_path, BASIN_RAFTS, RAFT_SNAP)

    _, report = snap_groups(load_layout(config))

    moves = [line for line in report if "moved" in line]
    assert len(moves) == 2
    assert all("turned" in line and "bearing" in line for line in moves)


def test_snap_is_idempotent(tmp_path):
    config = _write_snap_layout(tmp_path, BASIN_RAFTS, RAFT_SNAP)

    once, _ = snap_groups(load_layout(config))
    twice, second_report = snap_groups(once)

    for first, again in zip(once.placements, twice.placements):
        assert again.origin == pytest.approx(first.origin, abs=1e-9)
        assert again.rotation_deg == pytest.approx(first.rotation_deg, abs=1e-9)
    assert not [line for line in second_report if "moved" in line]


def test_snap_mean_bearing_is_circular_not_arithmetic(tmp_path):
    """Bearings 179° and 1° are 2° apart, not 178° — the mean is 0°, not 90°."""

    placements = [
        {"type": "unit", "origin_utm": [0, 0], "rotation_deg": 179.0, "size_m": [8.3, 5.3]},
        {"type": "unit", "origin_utm": [0, 7.3], "rotation_deg": 1.0, "size_m": [8.3, 5.3]},
    ]
    config = _write_snap_layout(tmp_path, placements, RAFT_SNAP, checks=[])

    snapped, _ = snap_groups(load_layout(config))
    bearing = snapped.placements[0].rotation_deg

    assert min(bearing, 180.0 - bearing) == pytest.approx(0.0, abs=1e-6)


def test_snap_as_placed_pitch_preserves_the_human_spacing(tmp_path):
    groups = [
        {"name": "row", "within": "unit", "snap": {"bearing": "mean", "pitch": "as-placed"}}
    ]
    config = _write_snap_layout(tmp_path, BASIN_RAFTS, groups, checks=[])
    as_placed_gap = math.dist(BASIN_RAFTS[0]["origin_utm"], BASIN_RAFTS[1]["origin_utm"])

    snapped, _ = snap_groups(load_layout(config))
    a, b = snapped.placements

    assert math.dist(a.origin, b.origin) == pytest.approx(as_placed_gap, abs=1e-9)


def test_snap_spec_pitch_rejects_mismatched_member_sizes(tmp_path):
    placements = [
        dict(BASIN_RAFTS[0]),
        {**BASIN_RAFTS[1], "size_m": [8.3, 4.0]},
    ]
    config = _write_snap_layout(tmp_path, placements, RAFT_SNAP, checks=[])

    with pytest.raises(LayoutError, match="members of one size"):
        snap_groups(load_layout(config))


def test_snap_spec_pitch_requires_clear_m(tmp_path):
    groups = [{"name": "row", "within": "unit", "snap": {"pitch": "spec"}}]
    config = _write_snap_layout(tmp_path, BASIN_RAFTS, groups, checks=[])

    with pytest.raises(LayoutError, match="pitch 'spec' needs clear_m"):
        load_layout(config)


def test_snap_group_rejects_unknown_type(tmp_path):
    groups = [
        {"name": "row", "within": "clarifier", "snap": {"pitch": "spec", "clear_m": 2.0}}
    ]
    config = _write_snap_layout(tmp_path, BASIN_RAFTS, groups, checks=[])

    with pytest.raises(LayoutError, match="unknown type"):
        load_layout(config)


def test_snap_with_a_single_member_is_reported_not_silently_skipped(tmp_path):
    config = _write_snap_layout(tmp_path, [BASIN_RAFTS[0]], RAFT_SNAP, checks=[])

    _, report = snap_groups(load_layout(config))

    assert any("nothing to snap" in line for line in report)


def test_no_groups_means_no_change_at_all(tmp_path):
    """Backward compatibility: a config without groups: behaves exactly as before."""

    config = _write_layout(tmp_path, BASIN_RAFTS, checks=RAFT_CHECKS)
    layout = load_layout(config)

    snapped, report = snap_groups(layout)

    assert snapped is layout
    assert report == []


# The live Basin register, verbatim — including its pre-P10 record order and its
# missing `# ids:` header line. Loading this file unchanged is the backward-compat
# guarantee the whole change rests on: 2 tagged, 8 carrying nothing but a pose,
# three dosing-skids distinguishable only by coordinates.
BASIN_REGISTER_TEXT = """\
# GENERATED placements register — do NOT hand-edit.
# Derived from: placements_export.geojson
# CRS: EPSG:32630. origin_utm = footprint centroid; rotation_deg = long-axis
# bearing from east, normalised to [0, 180). size_m = [long, short] as placed.
# Regenerate with: technical_drawings_for_agents layout <layout.yaml> --from-geojson <export.geojson>
instances:
- type: clarifier
  origin_utm: [788603.652, 322347.176]
  rotation_deg: 38.876
  size_m: [8.3, 5.3]
  tag: STA-A
- type: clarifier
  origin_utm: [788608.164, 322341.538]
  rotation_deg: 41.627
  size_m: [8.3, 5.3]
  tag: STA-B
- type: big-pump
  origin_utm: [788467.087, 322319.207]
  rotation_deg: 38.687
  size_m: [2.4, 1.7]
- type: water-tank-pre-fab-with-metal-bars-20cm
  origin_utm: [788610.597, 322335.83]
  rotation_deg: 44.366
  size_m: [1.5, 1.5]
- type: transformer
  origin_utm: [788472.197, 322324.016]
  rotation_deg: 39.852
  size_m: [2.8, 2.0]
- type: small-pump
  origin_utm: [788589.614, 322343.578]
  rotation_deg: 126.989
  size_m: [1.15, 0.55]
- type: panel-slab
  origin_utm: [788469.967, 322321.715]
  rotation_deg: 41.699
  size_m: [2.6, 0.8]
- type: dosing-skid
  origin_utm: [788662.616, 322262.035]
  rotation_deg: 43.034
  size_m: [1.6, 1.0]
- type: dosing-skid
  origin_utm: [788588.287, 322345.27]
  rotation_deg: 125.061
  size_m: [1.6, 1.0]
- type: dosing-skid
  origin_utm: [788666.593, 322259.423]
  rotation_deg: 43.034
  size_m: [1.6, 1.0]
"""

BASIN_TYPES = [
    "big-pump",
    "clarifier",
    "dosing-skid",
    "panel-slab",
    "small-pump",
    "transformer",
    "water-tank-pre-fab-with-metal-bars-20cm",
]

# Captured from the pre-P10 code path, not recomputed by the code under test.
EXPECTED_BASIN_RAFTS = {
    "STA-A": [
        (788602.5717556633, 322343.7360767198),
        (788607.2427923881, 322347.5018987969),
        (788604.7322443367, 322350.6159232801),
        (788600.0612076119, 322346.8501012031),
    ],
    "STA-B": [
        (788607.2501016421, 322338.0501941292),
        (788611.7350124798, 322342.0358653121),
        (788609.0778983579, 322345.0258058707),
        (788604.5929875202, 322341.0401346878),
    ],
}


def test_the_real_basin_register_shape_still_loads_and_places_unchanged(tmp_path):
    """Backward compat: the live 2-tagged/8-untagged register loads and places identically."""

    (tmp_path / "basin.placements.yaml").write_text(BASIN_REGISTER_TEXT, encoding="utf-8")
    config = _write_layout(tmp_path, "basin.placements.yaml", types=BASIN_TYPES)

    layout = load_layout(config)
    snapped, report = snap_groups(layout)
    placed = build_layout(snapped)

    assert len(layout.placements) == 10
    # load_layout preserves file order, so a pre-migration register reads as authored
    assert [p.identity for p in layout.placements[:2]] == ["STA-A", "STA-B"]
    assert [p.identity for p in layout.placements[2:]] == [None] * 8
    assert [p.has_durable_id for p in layout.placements] == [True] * 2 + [False] * 8
    assert check_layout(layout) == []  # no findings unless ids-present is declared
    assert snapped is layout and report == []  # no groups: nothing snapped

    rafts = {
        placement.tag: feature
        for placement, feature in placed
        if feature.role == "raft" and placement.tag in EXPECTED_BASIN_RAFTS
    }
    for tag, expected in EXPECTED_BASIN_RAFTS.items():
        for actual, wanted in zip(rafts[tag].coords, expected):
            assert actual == pytest.approx(wanted, abs=1e-9)


def test_cli_emit_register_writes_the_post_snap_poses(tmp_path):
    """Sheet/BOQ consumers must read the same poses the geometry was built from."""

    from technical_drawings_for_agents.cli import main

    config = _write_snap_layout(tmp_path, BASIN_RAFTS, RAFT_SNAP)
    effective = tmp_path / "effective.yaml"

    assert main(["layout", str(config), "--check-only", "--emit-register", str(effective)]) == 0

    instances = yaml.safe_load(effective.read_text(encoding="utf-8"))["instances"]
    assert {i["rotation_deg"] for i in instances} == {40.252}   # snapped, not as-placed
    assert all(i["snapped_by"] == "FA-130 rafts" for i in instances)
    # and it round-trips: reloadable as a register
    (tmp_path / "rt.yaml").write_text(effective.read_text(encoding="utf-8"), encoding="utf-8")
    reloaded = load_layout(_write_layout(tmp_path / "rt", instances, checks=RAFT_CHECKS))
    assert check_layout(reloaded) == []


def test_cli_no_snap_flag_reviews_the_as_placed_layout(tmp_path, capsys):
    from technical_drawings_for_agents.cli import main

    config = _write_snap_layout(tmp_path, BASIN_RAFTS, RAFT_SNAP)

    assert main(["layout", str(config), "--check-only"]) == 0        # snapped: clean
    assert main(["layout", str(config), "--check-only", "--no-snap"]) == 1  # as placed: fails
    assert "SKIPPED (--no-snap)" in capsys.readouterr().out


def test_shipped_worked_example_builds_and_checks_clean():
    """The neutral example is the executable spec for the config schema."""

    layout = load_layout(EXAMPLE_SPEC.parent / "site_layout.yaml")

    assert layout.id == "EXAMPLE-SITE"
    # Generated registers now load in canonical order (type, then easting), so
    # EX-B at easting 36.144 precedes EX-A at 40.0. Order only — no value moved.
    assert [p.tag for p in layout.placements] == ["EX-B", "EX-A"]
    assert len(build_layout(layout)) == 12
    assert check_layout(layout) == []


def test_shipped_worked_example_register_is_canonical():
    """The shipped example is the executable spec — it must not be the one stale register."""

    layout = load_layout(EXAMPLE_SPEC.parent / "site_layout.yaml")

    assert check_register(layout).clean is True


def test_cli_layout_exits_nonzero_on_an_error_finding(tmp_path):
    from technical_drawings_for_agents.cli import main

    config = _write_layout(
        tmp_path,
        [
            {"type": "unit", "origin_utm": [0.0, 0.0], "rotation_deg": 38.9, "tag": "A"},
            {"type": "unit", "origin_utm": [0.0, 20.0], "rotation_deg": 41.6, "tag": "B"},
        ],
        checks=[{"check": "parallel", "within": "unit", "tol_deg": 1.0}],
    )

    assert main(["layout", str(config), "--check-only"]) == 1
    assert main(["layout", str(config), "--check-only", "--warn-only"]) == 0
