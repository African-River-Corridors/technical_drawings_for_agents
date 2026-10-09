"""Identity and canonical-order rules for the placements register.

The central finding these tests pin: **no identity derivable from an editor
export alone is both stable under a move and non-renumbering under a delete**.
Determinism therefore comes from the canonical sort, and a durable join key has
to be authored. So the rules under test are (a) the sort is total and stable,
(b) an identity is validated and unique but never invented, and (c) every
ambiguity is a loud ``LayoutError``, not a heuristic.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest
import yaml

from technical_drawings_for_agents.components.layout import (
    LayoutError,
    canonical_instances,
    canonical_sort_key,
    canonicalise_register,
    check_layout,
    derive_placements,
    dump_placements_register,
    id_coverage,
    load_layout,
    placement_instance,
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

# The live Basin shape: two tagged clarifiers, eight records carrying nothing but
# a type and a pose, three of them mutually distinguishable only by coordinates.
BASIN_SHAPE = [
    {
        "type": "clarifier",
        "origin_utm": [788603.652, 322347.176],
        "rotation_deg": 38.876,
        "size_m": [8.3, 5.3],
        "tag": "STA-A",
    },
    {
        "type": "clarifier",
        "origin_utm": [788608.164, 322341.538],
        "rotation_deg": 41.627,
        "size_m": [8.3, 5.3],
        "tag": "STA-B",
    },
    {
        "type": "big-pump",
        "origin_utm": [788467.087, 322319.207],
        "rotation_deg": 38.687,
        "size_m": [2.4, 1.7],
    },
    {
        "type": "water-tank-pre-fab-with-metal-bars-20cm",
        "origin_utm": [788610.597, 322335.83],
        "rotation_deg": 44.366,
        "size_m": [1.5, 1.5],
    },
    {
        "type": "transformer",
        "origin_utm": [788472.197, 322324.016],
        "rotation_deg": 39.852,
        "size_m": [2.8, 2.0],
    },
    {
        "type": "small-pump",
        "origin_utm": [788589.614, 322343.578],
        "rotation_deg": 126.989,
        "size_m": [1.15, 0.55],
    },
    {
        "type": "panel-slab",
        "origin_utm": [788469.967, 322321.715],
        "rotation_deg": 41.699,
        "size_m": [2.6, 0.8],
    },
    {
        "type": "dosing-skid",
        "origin_utm": [788662.616, 322262.035],
        "rotation_deg": 43.034,
        "size_m": [1.6, 1.0],
    },
    {
        "type": "dosing-skid",
        "origin_utm": [788588.287, 322345.27],
        "rotation_deg": 125.061,
        "size_m": [1.6, 1.0],
    },
    {
        "type": "dosing-skid",
        "origin_utm": [788666.593, 322259.423],
        "rotation_deg": 43.034,
        "size_m": [1.6, 1.0],
    },
]


def _rect(cx: float, cy: float, long_m: float, short_m: float, rot_deg: float) -> list[list[float]]:
    angle = math.radians(rot_deg)
    cos_a, sin_a = math.cos(angle), math.sin(angle)
    half_long, half_short = long_m / 2.0, short_m / 2.0
    ring = []
    for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
        lx, ly = sx * half_long, sy * half_short
        ring.append([cx + lx * cos_a - ly * sin_a, cy + lx * sin_a + ly * cos_a])
    ring.append(list(ring[0]))
    return ring


def _feature(
    type_name: str,
    cx: float,
    cy: float,
    *,
    tag: str | None = None,
    placement_id: str | None = None,
) -> dict:
    properties: dict[str, str] = {"type": type_name}
    if tag is not None:
        properties["tag"] = tag
    if placement_id is not None:
        properties["id"] = placement_id
    return {
        "type": "Feature",
        "properties": properties,
        "geometry": {"type": "Polygon", "coordinates": [_rect(cx, cy, 6.0, 4.0, 0.0)]},
    }


def _geojson(*features: dict) -> dict:
    return {"type": "FeatureCollection", "features": list(features)}


def _write_layout(
    tmp_path: Path,
    placements: object,
    *,
    checks: list[dict] | None = None,
    groups: list[dict] | None = None,
    type_names: set[str] | None = None,
) -> Path:
    root = tmp_path / "components"
    root.mkdir(parents=True, exist_ok=True)
    (root / "unit.yaml").write_text(EXAMPLE_SPEC.read_text(encoding="utf-8"), encoding="utf-8")
    if type_names is None:
        type_names = (
            {str(item["type"]) for item in placements}
            if isinstance(placements, list)
            else {"unit"}
        )
    config: dict[str, object] = {
        "layout": {"id": "TEST-SITE", "crs": "EPSG:32630"},
        "components": {
            "root": "components",
            "types": {name: ["unit.yaml"] for name in sorted(type_names)},
        },
        "placements": placements,
    }
    if checks is not None:
        config["checks"] = checks
    if groups is not None:
        config["groups"] = groups
    path = tmp_path / "layout.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return path


def _sorted_mappings(items: list[dict]) -> list[str]:
    return sorted(yaml.safe_dump(item, sort_keys=True) for item in items)


# --------------------------------------------------------------------------- #
# identity: validated, unique, never invented
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "features",
    [
        # the copy-paste case: the paste carries the source's tag
        [_feature("unit", 0, 0, tag="STA-A"), _feature("unit", 10, 0, tag="STA-A")],
        # an explicit id colliding with another record's tag-derived identity
        [_feature("unit", 0, 0, tag="STA-A"), _feature("unit", 10, 0, placement_id="STA-A")],
        # case-only difference: macOS, Excel and QGIS all blur case
        [_feature("unit", 0, 0, tag="STA-A"), _feature("unit", 10, 0, tag="sta-a")],
    ],
)
def test_a_duplicate_id_is_a_hard_error(tmp_path, capsys, features):
    """Two physical units may never share one identity — and nothing auto-suffixes."""

    from technical_drawings_for_agents.cli import main

    with pytest.raises(LayoutError, match="duplicate") as exc:
        derive_placements(_geojson(*features))

    message = str(exc.value)
    assert "STA-A" in message
    assert "0.000" in message and "10.000" in message  # both poses named

    config = _write_layout(tmp_path, "placements.yaml")
    export = tmp_path / "export.geojson"
    export.write_text(json.dumps(_geojson(*features)), encoding="utf-8")

    assert main(["layout", str(config), "--from-geojson", str(export)]) == 2
    assert not (tmp_path / "placements.yaml").exists()  # nothing written
    assert "duplicate" in capsys.readouterr().err


def test_duplicate_pose_is_an_error_not_a_silent_dedupe():
    """A full-key tie is a pasted copy that was never moved, not a record to dedupe."""

    geojson = _geojson(
        _feature("dosing-skid", 788662.616, 322262.035),
        _feature("dosing-skid", 788662.616, 322262.035),
    )

    with pytest.raises(LayoutError, match="same pose") as exc:
        derive_placements(geojson)

    message = str(exc.value)
    assert "dosing-skid" in message
    assert "788662.616" in message and "322262.035" in message


@pytest.mark.parametrize("bad", ["-LEADING", "has space", "with/slash", "a" * 65, "é-accent"])
def test_an_identity_that_would_not_survive_yaml_csv_or_a_path_is_refused(bad):
    with pytest.raises(LayoutError, match="invalid identity"):
        canonical_instances(
            [{"type": "unit", "origin_utm": [0.0, 0.0], "rotation_deg": 0.0, "tag": bad}]
        )


def test_an_id_less_placement_is_counted_never_synthesised():
    """The tool reports the identity gap; it does not fill it with a sequence number."""

    instances = canonical_instances(BASIN_SHAPE)

    assert id_coverage(instances) == (2, 10)
    assert [item.get("id") for item in instances] == [None] * 10
    assert sum(1 for item in instances if item.get("tag")) == 2
    text = dump_placements_register(instances, source="export.geojson")
    assert "# ids: 2 of 10 placements carry a durable id (8 identified by pose only)." in text


# --------------------------------------------------------------------------- #
# canonical order
# --------------------------------------------------------------------------- #


def test_register_key_order_and_number_formatting_are_fixed():
    instances = [
        {
            "zeta": "last",
            "alpha": "first",
            "type": "unit",
            "origin_utm": [-0.0001, 2.34567],
            "rotation_deg": -0.0001,
            "size_m": [1.23456, -0.0001],
            "tag": "TAG-1",
            "id": "ID-1",
        }
    ]

    canonical = canonical_instances(instances)
    text = dump_placements_register(canonical, source="x")
    loaded = yaml.safe_load(text)["instances"]

    assert list(loaded[0]) == [
        "type",
        "origin_utm",
        "rotation_deg",
        "size_m",
        "tag",
        "id",
        "alpha",
        "zeta",
    ]
    assert "-0.0" not in text  # -0.0 and 0.0 compare equal but emit different bytes
    assert loaded == canonical
    assert instances[0]["origin_utm"] == [-0.0001, 2.34567]  # input was not mutated


def test_populating_an_identity_moves_no_record_because_identity_sorts_last():
    """The whole reason identity is the last tiebreak: closing the gap is not a reshuffle."""

    before = canonical_instances(BASIN_SHAPE)
    with_more_ids = [dict(item) for item in BASIN_SHAPE]
    with_more_ids[7]["id"] = "DOS-01"
    after = canonical_instances(with_more_ids)

    assert [(item["type"], item["origin_utm"]) for item in before] == [
        (item["type"], item["origin_utm"]) for item in after
    ]


def test_canonical_sort_key_orders_type_then_pose_then_size_then_identity():
    absent_size = {"type": "unit", "origin_utm": [0.0, 0.0], "rotation_deg": 0.0}
    present_size = {**absent_size, "size_m": [1.0, 1.0]}
    with_identity = {**present_size, "tag": "A"}

    assert canonical_sort_key(absent_size) < canonical_sort_key(present_size)
    assert canonical_sort_key(present_size) < canonical_sort_key(with_identity)
    assert canonical_sort_key({**absent_size, "origin_utm": [1.0, 0.0]}) > canonical_sort_key(
        {**absent_size, "origin_utm": [0.0, 9.0]}
    )
    assert canonical_sort_key({**absent_size, "type": "aaa"}) < canonical_sort_key(
        {**absent_size, "type": "zzz", "origin_utm": [-9.0, -9.0]}
    )


def test_dump_placements_register_rejects_non_canonical_input():
    """Silent reordering inside a function named `dump` is how a round-trip drifts."""

    instances = [
        {"type": "unit", "origin_utm": [10.0, 0.0], "rotation_deg": 0.0},
        {"type": "unit", "origin_utm": [0.0, 0.0], "rotation_deg": 0.0},
    ]

    with pytest.raises(LayoutError, match="canonical"):
        dump_placements_register(instances, source="x")


def test_the_register_round_trip_reaches_its_fixed_point_in_one_pass(tmp_path):
    """dump(canon(load(dump(canon(x))))) == dump(canon(x)) — no second-pass drift.

    This is the guarantee that pins the reverse serialiser: an `id` emitted out of
    key position, or an extra property re-emitted from `properties`, would show up
    here as a second pass that differs from the first.
    """

    seeded = [dict(item) for item in BASIN_SHAPE]
    seeded[0]["id"] = "RAFT-01"  # id and tag both present, and different
    seeded[2]["snapped_by"] = "FA-130 rafts"  # an extra property, as the live data has
    first = dump_placements_register(canonical_instances(seeded), source="export.geojson")

    register = tmp_path / "placements.yaml"
    register.write_text(first, encoding="utf-8")
    config = _write_layout(
        tmp_path, "placements.yaml", type_names={item["type"] for item in BASIN_SHAPE}
    )
    layout = load_layout(config)
    second = dump_placements_register(
        canonical_instances([placement_instance(p) for p in layout.placements]),
        source="export.geojson",
    )

    assert second == first

    raft = next(p for p in layout.placements if p.id == "RAFT-01")
    assert raft.identity == "RAFT-01"  # an explicit id wins over the tag...
    assert raft.tag == "STA-A"  # ...and the equipment tag survives alongside it
    assert raft.properties == {}  # `id` is reserved, so it never leaks into properties
    extra = next(p for p in layout.placements if p.properties)
    assert extra.properties == {"snapped_by": "FA-130 rafts"}  # a real extra still round-trips


def test_canonicalising_the_basin_register_changes_order_only(tmp_path):
    register = tmp_path / "basin.placements.yaml"
    original = (
        "# GENERATED placements register — do NOT hand-edit.\n"
        "# Derived from: placements_export.geojson\n"
        "# CRS: EPSG:32630. origin_utm = footprint centroid; rotation_deg = long-axis\n"
        "# bearing from east, normalised to [0, 180). size_m = [long, short] as placed.\n"
        "# Regenerate with: technical_drawings_for_agents layout <layout.yaml> --from-geojson <export.geojson>\n"
        + yaml.safe_dump({"instances": BASIN_SHAPE}, sort_keys=False, default_flow_style=None)
    )
    register.write_text(original, encoding="utf-8")
    before = yaml.safe_load(original)["instances"]

    assert canonicalise_register(register, crs="EPSG:32630") is True

    after_text = register.read_text(encoding="utf-8")
    after = yaml.safe_load(after_text)["instances"]

    assert _sorted_mappings(after) == _sorted_mappings(before)  # every value preserved
    assert [item["type"] for item in after] != [item["type"] for item in before]  # order moved
    assert "# Derived from: placements_export.geojson" in after_text  # provenance preserved
    assert "# ids: 2 of 10" in after_text
    # a second pass is a no-op: one pass reaches the fixed point
    assert canonicalise_register(register, crs="EPSG:32630") is False
    assert register.read_text(encoding="utf-8") == after_text


def test_canonicalising_a_register_with_no_provenance_line_needs_register_source(tmp_path):
    register = tmp_path / "hand.placements.yaml"
    register.write_text(
        yaml.safe_dump({"instances": BASIN_SHAPE[:2]}, sort_keys=False), encoding="utf-8"
    )

    with pytest.raises(LayoutError, match="Derived from"):
        canonicalise_register(register, crs="EPSG:32630")

    assert canonicalise_register(register, crs="EPSG:32630", source="hand-authored") is True
    assert "# Derived from: hand-authored" in register.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# the ids-present check, and order: as-listed
# --------------------------------------------------------------------------- #


def test_ids_present_check_reports_every_id_less_placement_and_is_opt_in(tmp_path):
    from technical_drawings_for_agents.cli import main

    config = _write_layout(tmp_path, BASIN_SHAPE)
    layout = load_layout(config)

    assert check_layout(layout) == []  # declared only, never implicit

    with_check = _write_layout(
        tmp_path / "with-check", BASIN_SHAPE, checks=[{"check": "ids-present"}]
    )
    findings = check_layout(load_layout(with_check))

    assert len(findings) == 8
    assert {finding.check for finding in findings} == {"ids-present"}
    assert {finding.severity for finding in findings} == {"error"}
    assert all("has no durable id" in finding.message for finding in findings)
    assert all(" at (" in finding.message and ") @" in finding.message for finding in findings)

    assert main(["layout", str(config), "--check-only", "--require-ids"]) == 1

    warn = _write_layout(
        tmp_path / "warn", BASIN_SHAPE, checks=[{"check": "ids-present", "severity": "warn"}]
    )
    assert main(["layout", str(warn), "--check-only"]) == 0


def test_as_listed_group_order_is_refused_against_a_generated_register(tmp_path):
    """A generated register's order is the canonical sort, which is nobody's intent."""

    placements = [
        {
            "type": "unit",
            "origin_utm": [0.0, 0.0],
            "rotation_deg": 0.0,
            "size_m": [6.0, 4.0],
            "tag": "A",
        },
        {
            "type": "unit",
            "origin_utm": [0.0, 10.0],
            "rotation_deg": 0.0,
            "size_m": [6.0, 4.0],
            "tag": "B",
        },
    ]
    register = tmp_path / "placements.yaml"
    register.write_text(
        dump_placements_register(canonical_instances(placements), source="export.geojson"),
        encoding="utf-8",
    )
    groups = [
        {
            "name": "row",
            "within": "unit",
            "snap": {"bearing": "first", "pitch": "spec", "clear_m": 2.0, "order": "as-listed"},
        }
    ]
    config = _write_layout(tmp_path, "placements.yaml", groups=groups)

    with pytest.raises(LayoutError, match="as-listed"):
        load_layout(config)

    # ...but an inline list keeps `as-listed` meaning exactly what the author wrote
    inline = _write_layout(tmp_path / "inline", placements, groups=groups)
    snapped, _ = snap_groups(load_layout(inline))

    assert [placement.tag for placement in snapped.placements] == ["A", "B"]
    assert snapped.placements[0].origin[1] > snapped.placements[1].origin[1]
