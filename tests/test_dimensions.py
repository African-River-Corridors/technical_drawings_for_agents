"""P6 acceptance tests — dimensions and setting-out computed from placed geometry.

Every test is numbered against ``docs/specs/P6-computed-dimensions.md`` §5 and named
for the behaviour it asserts. The two load-bearing ones are 1/2 (a sheet cannot
contradict its own ``clear-spacing`` check, asserted against ``check_layout``'s own
output rather than a literal) and 6 (the witness invariant, tested rather than trusted).
"""

from __future__ import annotations

import hashlib
import inspect
import json
import math
import random
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

import technical_drawings_for_agents
from technical_drawings_for_agents.cli import main
from technical_drawings_for_agents.components.dimensions import (
    NOT_SURVEYED,
    AnnotationBox,
    DimensionError,
    check_dimensions,
    dump_measurements,
    dump_setting_out,
    format_value,
    load_dimensions,
    measure,
    placement_key,
    project_onto_route,
    render_dimensions,
    resolve,
    setting_out_rows,
    setting_out_table,
)
from technical_drawings_for_agents.components.layout import (
    Layout,
    LayoutError,
    Placement,
    _point_segment_distance,
    _project,
    check_layout,
    footprint,
    load_layout,
    polygon_gap,
)
from technical_drawings_for_agents.meta import DrawingMeta
from technical_drawings_for_agents.svg import ViewBox, svg_dimension_h, svg_dimension_v

from .test_layout import _write_layout

EXAMPLES = (
    Path(technical_drawings_for_agents.__file__).resolve().parent / "components" / "examples"
)
EXAMPLE_DIMENSIONS = EXAMPLES / "site_dimensions.yaml"
EXAMPLE_LAYOUT = EXAMPLES / "site_layout.yaml"

# The real post-snap Basin poses, from basin.effective.yaml.
STA_A = (788603.55, 322347.143)
STA_B = (788608.266, 322341.571)
BASIN_ROT = 40.252
BASIN_SIZE = [8.3, 5.3]
P5_POINT = [788609.589, 322341.151]
P5_SOURCE = "STB-STA-STC-FOUND-001 §2.2 (Geomars DCP P5, approved WTP location)"


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #


def _pair(centre_offset: float, rot_deg: float = 40.0, size=(8.3, 5.3)) -> list[dict]:
    """Two identical rafts, long axes parallel, centres ``centre_offset`` apart across
    the short axis — the ``tests/test_layout.py`` clear-spacing fixture."""

    angle = math.radians(rot_deg)
    return [
        {
            "type": "unit",
            "origin_utm": [0.0, 0.0],
            "rotation_deg": rot_deg,
            "size_m": list(size),
            "tag": "A",
        },
        {
            "type": "unit",
            "origin_utm": [-math.sin(angle) * centre_offset, math.cos(angle) * centre_offset],
            "rotation_deg": rot_deg,
            "size_m": list(size),
            "tag": "B",
        },
    ]


def _basin_clarifiers() -> list[dict]:
    return [
        {
            "type": "clarifier",
            "origin_utm": list(STA_A),
            "rotation_deg": BASIN_ROT,
            "size_m": BASIN_SIZE,
            "tag": "STA-A",
        },
        {
            "type": "clarifier",
            "origin_utm": list(STA_B),
            "rotation_deg": BASIN_ROT,
            "size_m": BASIN_SIZE,
            "tag": "STA-B",
        },
    ]


def _basin_ten() -> list[dict]:
    """The real 10-placement Basin shape: two tagged clarifiers, eight untagged."""

    rest = [
        ("big-pump", [788467.087, 322319.207], 38.687, [2.4, 1.7]),
        ("water-tank", [788610.597, 322335.83], 44.366, [1.5, 1.5]),
        ("transformer", [788472.197, 322324.016], 39.852, [2.8, 2.0]),
        ("small-pump", [788589.614, 322343.578], 126.989, [1.15, 0.55]),
        ("panel-slab", [788469.967, 322321.715], 41.699, [2.6, 0.8]),
        ("dosing-skid", [788662.616, 322262.035], 43.034, [1.6, 1.0]),
        ("dosing-skid", [788588.287, 322345.27], 125.061, [1.6, 1.0]),
        ("dosing-skid", [788666.593, 322259.423], 43.034, [1.6, 1.0]),
    ]
    return [
        *_basin_clarifiers(),
        *(
            {"type": name, "origin_utm": origin, "rotation_deg": rot, "size_m": size}
            for name, origin, rot, size in rest
        ),
    ]


def _write_dimensions(tmp_path: Path, layout_config: Path, body: dict, name: str = "d") -> Path:
    document = {
        "dimension_set": {"id": "TEST-DIMS", "layout": layout_config.name},
        **body,
    }
    path = tmp_path / f"{name}.dimensions.yaml"
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return path


def _one(tmp_path: Path, placements: list[dict], dimension: dict, **extra) -> tuple:
    """Write a layout + a one-dimension file, and return ``(dset, layout)``."""

    config = _write_layout(
        tmp_path,
        placements,
        checks=extra.pop("checks", None),
        types=extra.pop("types", None),
    )
    dims = _write_dimensions(tmp_path, config, {"dimensions": [dimension], **extra})
    return load_dimensions(dims), load_layout(config)


def _viewbox() -> ViewBox:
    return ViewBox(-30.0, 30.0, -30.0, 30.0, 1000.0, 700.0, padding=60.0)


# --------------------------------------------------------------------------- #
# 1, 2 — a sheet cannot contradict its own clear-spacing check
# --------------------------------------------------------------------------- #


def test_clearance_reports_exactly_what_the_clear_spacing_check_computes(tmp_path):
    """Test 1. ``==``, not ``approx``: it is the same function on the same inputs."""

    dset, layout = _one(
        tmp_path,
        _pair(7.3),
        {"id": "DIM-001", "kind": "clearance", "from": {"placement": "A"},
         "to": {"placement": "B"}},
        checks=[{"check": "clear-spacing", "within": "unit", "min_m": 2.0}],
    )
    first, second = layout.placements

    measurement = measure(dset, layout)[0]
    gap = polygon_gap(footprint(first), footprint(second))

    assert measurement.value_m == gap
    assert gap == pytest.approx(2.0, abs=1e-12)
    assert check_layout(layout) == []
    assert measurement.text == "2.000 m"


def test_clearance_matches_the_gap_quoted_in_a_failing_clear_spacing_finding(tmp_path):
    """Test 2. The anti-contradiction property, asserted against check_layout's own text."""

    dset, layout = _one(
        tmp_path,
        _pair(6.8),
        {"id": "DIM-001", "kind": "clearance", "from": {"placement": "A"},
         "to": {"placement": "B"}},
        checks=[{"check": "clear-spacing", "within": "unit", "min_m": 2.0}],
    )

    finding = check_layout(layout)[0]
    measurement = measure(dset, layout)[0]

    assert finding.check == "clear-spacing"
    assert f"{measurement.value_m:.3f} m" in finding.message
    assert measurement.text == "1.500 m"


def test_moving_a_placement_changes_the_dimension_text_with_no_spec_edit(tmp_path):
    """Test 3. Only the register moves; the dimensions file bytes are pinned by hash."""

    config = _write_layout(tmp_path, _pair(7.3))
    dims = _write_dimensions(
        tmp_path,
        config,
        {
            "dimensions": [
                {
                    "id": "DIM-001",
                    "kind": "clearance",
                    "from": {"placement": "A"},
                    "to": {"placement": "B"},
                }
            ]
        },
    )
    before_hash = hashlib.sha256(dims.read_bytes()).hexdigest()
    text_before = measure(load_dimensions(dims), load_layout(config))[0].text

    _write_layout(tmp_path, _pair(8.0))  # rewrites layout.yaml's inline placements only
    text_after = measure(load_dimensions(dims), load_layout(config))[0].text

    assert text_before == "2.000 m"
    assert text_after == "2.700 m"
    assert hashlib.sha256(dims.read_bytes()).hexdigest() == before_hash


def test_a_dimension_referencing_an_unknown_tag_is_a_hard_error(tmp_path):
    """Test 4. An exception — not a finding, not a blank, never a nearby guess."""

    dset, layout = _one(
        tmp_path,
        _basin_clarifiers(),
        {
            "id": "DIM-004",
            "kind": "clearance",
            "from": {"placement": "STA-A"},
            "to": {"placement": "STA-Z"},
        },
        types=["clarifier"],
    )

    with pytest.raises(DimensionError) as excinfo:
        measure(dset, layout)

    message = str(excinfo.value)
    assert "STA-Z" in message
    assert "STA-A" in message and "STA-B" in message
    assert "--from-geojson" in message


def test_rotated_footprint_clearance_is_correct_for_a_hand_computed_edge_case(tmp_path):
    """Test 5. The edge branch centres the dimension in the gap, not at a corner."""

    dset, layout = _one(
        tmp_path,
        _pair(7.3),
        {"id": "DIM-001", "kind": "clearance", "from": {"placement": "A"},
         "to": {"placement": "B"}},
    )
    first, second = layout.placements

    measurement = measure(dset, layout)[0]

    assert measurement.value_m == pytest.approx(2.0, abs=1e-12)
    assert measurement.witness_mode == "edge"
    assert abs(measurement.drawn_length_m - measurement.value_m) < 1e-9
    midpoint = (
        (first.origin[0] + second.origin[0]) / 2.0,
        (first.origin[1] + second.origin[1]) / 2.0,
    )
    witness_mid = (
        (measurement.anchor_a[0] + measurement.anchor_b[0]) / 2.0,
        (measurement.anchor_a[1] + measurement.anchor_b[1]) / 2.0,
    )
    assert witness_mid[0] == pytest.approx(midpoint[0], abs=1e-9)
    assert witness_mid[1] == pytest.approx(midpoint[1], abs=1e-9)


def test_vertex_to_vertex_clearance_uses_the_corner_pair_not_an_edge_normal(tmp_path):
    """Test 5b. Max edge-normal separation here is 2.0; the true gap is 2*sqrt(2).

    A single-branch axis rule would draw a 2.0 m line labelled 2.828 m.
    """

    dset, layout = _one(
        tmp_path,
        [
            {"type": "unit", "origin_utm": [0, 0], "rotation_deg": 0, "size_m": [4, 2], "tag": "A"},
            {"type": "unit", "origin_utm": [6, 4], "rotation_deg": 0, "size_m": [4, 2], "tag": "B"},
        ],
        {"id": "DIM-001", "kind": "clearance", "from": {"placement": "A"},
         "to": {"placement": "B"}},
    )

    measurement = measure(dset, layout)[0]

    assert measurement.value_m == pytest.approx(2.8284271247461903, abs=1e-12)
    assert measurement.witness_mode == "vertex"
    assert measurement.anchor_a == pytest.approx((2.0, 1.0))
    assert measurement.anchor_b == pytest.approx((4.0, 3.0))
    assert measurement.text == "2.828 m"


@pytest.mark.parametrize("rot", [0.0, 17.0, 40.0, 89.0, 90.0, 133.0, 179.0])
@pytest.mark.parametrize("offset", [7.3, 12.0])
def test_witness_geometry_always_spans_the_value_it_labels(tmp_path, rot, offset):
    """Test 6. The §3.5 invariant, over both branches and seven rotations."""

    angle = math.radians(rot)
    diagonal = [
        {"type": "unit", "origin_utm": [0, 0], "rotation_deg": rot, "size_m": [4, 2], "tag": "A"},
        {
            "type": "unit",
            "origin_utm": [6.0 + math.cos(angle), 4.0 + math.sin(angle)],
            "rotation_deg": rot,
            "size_m": [4, 2],
            "tag": "B",
        },
    ]
    for name, placements in (("edge", _pair(offset, rot)), ("vertex", diagonal)):
        dset, layout = _one(
            tmp_path / f"{name}-{rot}-{offset}",
            placements,
            {
                "id": "DIM-001",
                "kind": "clearance",
                "from": {"placement": "A"},
                "to": {"placement": "B"},
            },
        )
        measurement = measure(dset, layout)[0]
        assert abs(measurement.drawn_length_m - abs(measurement.value_m)) < 1e-9
        assert measurement.witness_mode in ("edge", "vertex")
        # render_dimensions asserts the same invariant and would raise here.
        elements, _ = render_dimensions(_viewbox(), [measurement])
        assert elements and elements[0]


def test_overlapping_footprints_are_a_hard_error_not_a_zero_clearance(tmp_path):
    """Test 7. polygon_gap collapses overlap and contact, so 0.000 would be a lie."""

    dset, layout = _one(
        tmp_path,
        [
            {"type": "unit", "origin_utm": [0, 0], "rotation_deg": 0, "size_m": [8.3, 5.3],
             "tag": "A"},
            {"type": "unit", "origin_utm": [2, 1], "rotation_deg": 30, "size_m": [8.3, 5.3],
             "tag": "B"},
        ],
        {"id": "DIM-001", "kind": "clearance", "from": {"placement": "A"},
         "to": {"placement": "B"}},
    )

    with pytest.raises(DimensionError) as excinfo:
        measure(dset, layout)

    message = str(excinfo.value)
    assert "A" in message and "B" in message
    assert "overlap" in message
    assert "0.000" not in message
    assert "no-overlap" in message


def test_touching_footprints_render_zero_with_a_warning_not_silently(tmp_path):
    """Test 8. Abutting slabs are legal geometry; a zero on a GA is still worth flagging."""

    dset, layout = _one(
        tmp_path,
        [
            {"type": "unit", "origin_utm": [0, 0], "rotation_deg": 0, "size_m": [4, 2], "tag": "A"},
            {"type": "unit", "origin_utm": [4, 0], "rotation_deg": 0, "size_m": [4, 2], "tag": "B"},
        ],
        {"id": "DIM-001", "kind": "clearance", "from": {"placement": "A"},
         "to": {"placement": "B"}},
    )

    measurement = measure(dset, layout)[0]
    findings = check_dimensions(dset, [measurement])

    assert measurement.text == "0.000 m"
    assert len(findings) == 1
    assert (findings[0].severity, findings[0].check) == ("warn", "dimension-zero")
    elements, boxes = render_dimensions(_viewbox(), [measurement])
    assert len(elements) == 1 and elements[0].strip()
    assert len(boxes) == 1


def test_zero_length_centres_dimension_fails_by_default_and_leaders_when_declared(tmp_path):
    """Test 9. The leader escape exists so a deliberate coincidence can be *stated*."""

    coincident = [
        {"type": "unit", "origin_utm": [0, 0], "rotation_deg": 0, "size_m": [4, 2], "tag": "A"},
        {"type": "unit", "origin_utm": [0, 0], "rotation_deg": 30, "size_m": [1, 1], "tag": "B"},
    ]
    spec = {
        "id": "DIM-002",
        "kind": "centres",
        "from": {"placement": "A"},
        "to": {"placement": "B"},
    }

    strict_set, strict_layout = _one(tmp_path / "strict", coincident, dict(spec))
    with pytest.raises(DimensionError, match="share one origin"):
        measure(strict_set, strict_layout)

    lenient_set, lenient_layout = _one(
        tmp_path / "lenient", coincident, {**spec, "on_zero": "leader"}
    )
    measurement = measure(lenient_set, lenient_layout)[0]
    findings = check_dimensions(lenient_set, [measurement])

    assert measurement.text == "0.000 m (coincident)"
    assert [(f.severity, f.check) for f in findings] == [("warn", "dimension-zero")]


def test_envelope_of_the_clarifier_group_measures_across_its_own_bearing(tmp_path):
    """Test 10. The real Basin poses, both principal axes."""

    for axis, expected, text in (
        ("principal-cross", 12.599851977312937, "12.600 m"),
        ("principal", 8.301053300267085, "8.301 m"),
    ):
        dset, layout = _one(
            tmp_path / axis,
            _basin_clarifiers(),
            {"id": "DIM-010", "kind": "envelope", "of": {"type": "clarifier"}, "axis": axis},
            types=["clarifier"],
        )
        measurement = measure(dset, layout)[0]
        assert measurement.value_m == pytest.approx(expected, abs=1e-9)
        assert measurement.text == text
        assert measurement.witness_mode == "axis"
        assert abs(measurement.drawn_length_m - measurement.value_m) < 1e-9


def test_offset_from_a_sourced_datum_is_signed_and_shows_a_compass_token(tmp_path):
    """Test 11. The sign is rendered as a compass token, never dropped."""

    cases = (
        ("easting", -6.0389999999897555, "6.039 m W", "W"),
        ("northing", 5.992000000085682, "5.992 m N", "N"),
        ("direct", 8.507266599848807, "8.507 m", ""),
    )
    for axis, expected, text, token in cases:
        dset, layout = _one(
            tmp_path / axis,
            _basin_clarifiers(),
            {
                "id": "DIM-020",
                "kind": "offset",
                "from": {"datum": "P5"},
                "to": {"placement": "STA-A"},
                "axis": axis,
            },
            types=["clarifier"],
            datums=[{"name": "P5", "point": P5_POINT, "z": 56.947, "source": P5_SOURCE,
                     "status": "sourced"}],
        )
        measurement = measure(dset, layout)[0]
        assert measurement.value_m == pytest.approx(expected, abs=1e-9)
        assert measurement.text == text
        assert measurement.direction_token == token
        assert abs(measurement.drawn_length_m - abs(measurement.value_m)) < 1e-9


def test_a_datum_without_a_source_is_rejected_at_load(tmp_path):
    """Test 12. And a dimension may never carry a literal coordinate at all."""

    config = _write_layout(tmp_path, _basin_clarifiers(), types=["clarifier"])
    unsourced = _write_dimensions(
        tmp_path,
        config,
        {
            "datums": [{"name": "P5", "point": P5_POINT}],
            "dimensions": [
                {"id": "DIM-020", "kind": "offset", "from": {"datum": "P5"},
                 "to": {"placement": "STA-A"}, "axis": "easting"}
            ],
        },
        name="unsourced",
    )
    with pytest.raises(DimensionError, match=r"datums\[0\]\.source"):
        load_dimensions(unsourced)

    literal = _write_dimensions(
        tmp_path,
        config,
        {
            "dimensions": [
                {"id": "DIM-020", "kind": "offset", "from": {"point": P5_POINT},
                 "to": {"placement": "STA-A"}, "axis": "easting"}
            ]
        },
        name="literal",
    )
    with pytest.raises(DimensionError) as excinfo:
        load_dimensions(literal)
    assert "literal 'point'" in str(excinfo.value)
    assert "datums:" in str(excinfo.value)


def test_chainage_sums_the_route_and_annotates_with_a_leader_not_a_straight_dimension(tmp_path):
    """Test 13. A path length is not the distance between its endpoints."""

    datums = [{"name": "P5", "point": P5_POINT, "z": 56.947, "source": P5_SOURCE}]
    straight_set, straight_layout = _one(
        tmp_path / "straight",
        _basin_clarifiers(),
        {"id": "DIM-030", "kind": "chainage", "route": "R-1"},
        types=["clarifier"],
        routes=[
            {"name": "R-1", "kind": "pipe",
             "points": [{"placement": "STA-A"}, {"placement": "STA-B"}]}
        ],
    )
    straight = measure(straight_set, straight_layout)[0]
    assert straight.value_m == pytest.approx(7.299852053263156, abs=1e-9)
    assert straight.text == "7.300 m"

    bent_set, bent_layout = _one(
        tmp_path / "bent",
        _basin_clarifiers(),
        {"id": "DIM-030", "kind": "chainage", "route": "R-2"},
        types=["clarifier"],
        datums=datums,
        routes=[
            {"name": "R-2", "kind": "pipe",
             "points": [{"placement": "STA-A"}, {"datum": "P5"}, {"placement": "STA-B"}]}
        ],
    )
    bent = measure(bent_set, bent_layout)[0]

    legs = sum(math.dist(a, b) for a, b in zip(bent.path, bent.path[1:]))
    assert bent.value_m == pytest.approx(legs, abs=1e-12)
    assert bent.value_m > math.dist(bent.path[0], bent.path[-1]) + 1e-6
    assert bent.witness_mode == "path"

    elements, _ = render_dimensions(_viewbox(), [bent])
    assert "<polygon" not in elements[0]      # no dimension line, no arrowheads
    assert "<circle" in elements[0]           # the leader dot


def test_envelope_projection_agrees_with_the_layout_modules_own_projection(tmp_path):
    """Test 14. Pins the one piece of geometry not literally shared."""

    rng = random.Random(20260725)
    for trial in range(6):
        placements = [
            {
                "type": "unit",
                "origin_utm": [rng.uniform(-40, 40), rng.uniform(-40, 40)],
                "rotation_deg": 37.5,
                "size_m": [6.0, 4.0],
                "tag": f"T{index}",
            }
            for index in range(3)
        ]
        dset, layout = _one(
            tmp_path / f"proj{trial}",
            placements,
            {"id": "DIM-010", "kind": "envelope", "of": {"all": True}, "axis": "principal"},
            checks=[],
        )
        measurement = measure(dset, layout)[0]
        unit = tuple(measurement.provenance["axis_unit"])
        corners = [c for p in layout.placements for c in footprint(p)]
        low, high = _project(corners, unit)

        assert measurement.value_m == pytest.approx(high - low, abs=1e-12)
        for anchor, bound in ((measurement.anchor_a, low), (measurement.anchor_b, high)):
            assert anchor[0] * unit[0] + anchor[1] * unit[1] == pytest.approx(bound, abs=1e-9)


def test_route_station_projection_agrees_with_point_segment_distance():
    """Test 15. The new projection helper is pinned to the module's own maths."""

    rng = random.Random(58)
    for _ in range(200):
        start = (rng.uniform(-50, 50), rng.uniform(-50, 50))
        end = (rng.uniform(-50, 50), rng.uniform(-50, 50))
        if math.dist(start, end) < 1e-6:
            continue
        query = (rng.uniform(-60, 60), rng.uniform(-60, 60))

        index, t, point = project_onto_route(query, (start, end))

        assert index == 0
        assert 0.0 <= t <= 1.0
        assert math.dist(query, point) == pytest.approx(
            _point_segment_distance(query, start, end), abs=1e-12
        )


def test_setting_out_table_matches_the_register_exactly(tmp_path):
    """Test 16. The table is a rendering of the register, never a second copy of it."""

    dset, layout = _one(
        tmp_path,
        _basin_clarifiers(),
        {"id": "DIM-001", "kind": "clearance", "from": {"placement": "STA-A"},
         "to": {"placement": "STA-B"}},
        types=["clarifier"],
        setting_out={
            "include": ["placements"],
            "of": {"type": "clarifier"},
            "z": {"source": "none"},
            "note": "Z NOT SURVEYED per placement. Only P5 carries a level.",
        },
    )

    rows = setting_out_rows(dset, layout)
    csv = dump_setting_out(rows, dset.setting_out, crs=layout.crs, fmt="csv")

    assert [row.id for row in rows] == ["STA-A", "STA-B"]
    by_key = {placement_key(p): p for p in layout.placements}
    for row in rows:
        assert (row.easting, row.northing) == by_key[row.id].origin
        assert row.z is None
    data_lines = [line for line in csv.splitlines() if not line.startswith("#")]
    assert data_lines[0].startswith("ID,E (m),N (m),Z (m)")
    for row, line in zip(rows, data_lines[1:]):
        cells = line.split(",")
        assert cells[0] == row.id
        assert cells[1] == format_value(row.easting, decimals=3, units_suffix="")
        assert cells[2] == format_value(row.northing, decimals=3, units_suffix="")
        assert cells[3] == NOT_SURVEYED
    assert "Only P5 carries a level" in csv
    assert layout.crs in csv


def test_setting_out_without_a_declared_z_source_is_a_hard_error(tmp_path):
    """Test 17. No default can exist that does not invent a level or hide its absence."""

    config = _write_layout(tmp_path, _basin_clarifiers(), types=["clarifier"])
    dimension = {
        "id": "DIM-001",
        "kind": "clearance",
        "from": {"placement": "STA-A"},
        "to": {"placement": "STA-B"},
    }

    missing = _write_dimensions(
        tmp_path, config,
        {"dimensions": [dimension], "setting_out": {"of": {"type": "clarifier"}}},
        name="missing-z",
    )
    with pytest.raises(DimensionError) as excinfo:
        load_dimensions(missing)
    message = str(excinfo.value)
    assert "setting_out" in message
    for source in ("property", "datum", "none"):
        assert source in message

    no_note = _write_dimensions(
        tmp_path, config,
        {"dimensions": [dimension],
         "setting_out": {"of": {"type": "clarifier"}, "z": {"source": "none"}}},
        name="no-note",
    )
    with pytest.raises(DimensionError, match="setting_out.note is required"):
        load_dimensions(no_note)

    partial = _write_dimensions(
        tmp_path, config,
        {"dimensions": [dimension],
         "setting_out": {"of": {"type": "clarifier"},
                         "z": {"source": "property", "property": "level_m"},
                         "vertical_datum": "MSL (m)"}},
        name="partial-z",
    )
    with pytest.raises(DimensionError) as excinfo:
        setting_out_rows(load_dimensions(partial), load_layout(config))
    assert "level_m" in str(excinfo.value)
    assert "STA-A" in str(excinfo.value)


def test_an_untagged_placement_cannot_reach_the_setting_out_table(tmp_path):
    """Test 18. The honesty test: the table is either complete and keyed, or it fails."""

    types = ["clarifier", "big-pump", "water-tank", "transformer", "small-pump", "panel-slab",
             "dosing-skid"]
    setting_out = {
        "include": ["placements"],
        "z": {"source": "none"},
        "note": "Z NOT SURVEYED. Only the DCP point P5 carries a level (56.947 m MSL).",
    }
    dimension = {
        "id": "DIM-001",
        "kind": "clearance",
        "from": {"placement": "STA-A"},
        "to": {"placement": "STA-B"},
    }

    every_set, every_layout = _one(
        tmp_path / "all", _basin_ten(), dict(dimension), types=types,
        setting_out={**setting_out, "of": {"all": True}}, checks=[],
    )
    with pytest.raises(DimensionError) as excinfo:
        setting_out_rows(every_set, every_layout)
    message = str(excinfo.value)
    assert "8 of 10" in message
    assert "tag" in message and "naming:" in message
    assert "STA-A, STA-B" in message

    two_set, two_layout = _one(
        tmp_path / "clarifiers", _basin_ten(), dict(dimension), types=types,
        setting_out={**setting_out, "of": {"type": "clarifier"}}, checks=[],
    )
    rows = setting_out_rows(two_set, two_layout)
    assert [row.id for row in rows] == ["STA-A", "STA-B"]


def test_expect_m_is_checked_never_printed(tmp_path):
    """Test 19. The spec value is the thing under test, not the thing printed."""

    dimension = {
        "id": "DIM-001",
        "kind": "clearance",
        "from": {"placement": "A"},
        "to": {"placement": "B"},
        "expect_m": 2.0,
        "expect_source": "FA-130 foundation slab dwg",
        "expect_tol_m": 0.002,
    }
    bad_set, bad_layout = _one(tmp_path / "bad", _pair(7.243), dict(dimension))
    measurement = measure(bad_set, bad_layout)[0]
    findings = check_dimensions(bad_set, [measurement])

    assert measurement.text == "1.943 m"
    assert measurement.text != "2.000 m"
    assert len(findings) == 1
    assert (findings[0].severity, findings[0].check) == ("error", "dimension-expectation")
    assert "1.943" in findings[0].message
    assert "2" in findings[0].message
    assert "FA-130 foundation slab dwg" in findings[0].message

    # The real Basin gap: 1.5 mm short of its 2.0 m target through register rounding,
    # inside the 2 mm default tolerance that rounding is what justifies.
    good_set, good_layout = _one(
        tmp_path / "good", _basin_clarifiers(),
        {**dimension, "from": {"placement": "STA-A"}, "to": {"placement": "STA-B"}},
        types=["clarifier"],
    )
    real = measure(good_set, good_layout)[0]
    assert real.value_m == pytest.approx(1.9998519772665881, abs=1e-12)
    assert real.text == "2.000 m"
    assert check_dimensions(good_set, [real]) == []


# --------------------------------------------------------------------------- #
# 20, 21 — backward compatibility
# --------------------------------------------------------------------------- #

# Generated from origin/main BEFORE this feature landed. `svg_dimension_h/v` are not
# re-implemented on top of the new primitive, not deprecated and not re-signatured.
_H_GOLDEN = (
    '<line x1="128.0" y1="208.0" x2="128.0" y2="253.0" stroke="#888888" stroke-width="0.5"/>\n'
    '<line x1="272.0" y1="208.0" x2="272.0" y2="253.0" stroke="#888888" stroke-width="0.5"/>\n'
    '<line x1="128.0" y1="250.0" x2="272.0" y2="250.0" stroke="#888888" stroke-width="0.7"/>\n'
    '<polygon points="128.0,250.0 133.0,247.5 133.0,252.5" fill="#888888"/>\n'
    '<polygon points="272.0,250.0 267.0,247.5 267.0,252.5" fill="#888888"/>\n'
    '<text x="200.0" y="246.0" font-size="10" fill="#bbbbbb" text-anchor="middle" '
    'font-family="monospace">BED 4 m</text>'
)
_V_GOLDEN = (
    '<line x1="308.0" y1="208.0" x2="341.0" y2="208.0" stroke="#888888" stroke-width="0.5"/>\n'
    '<line x1="308.0" y1="154.0" x2="341.0" y2="154.0" stroke="#888888" stroke-width="0.5"/>\n'
    '<line x1="338.0" y1="208.0" x2="338.0" y2="154.0" stroke="#888888" stroke-width="0.7"/>\n'
    '<polygon points="338.0,208.0 335.5,203.0 340.5,203.0" fill="#888888"/>\n'
    '<polygon points="338.0,154.0 335.5,159.0 340.5,159.0" fill="#888888"/>\n'
    '<text x="350.0" y="184.0" font-size="10" fill="#bbbbbb" text-anchor="middle" '
    'font-family="monospace" transform="rotate(-90,350.0,184.0)">DEPTH 1.5 m</text>'
)
_SIG_H = "(vb: 'ViewBox', real_y, real_x1, real_x2, label, offset_px=25, policy=None)"
_SIG_V = (
    "(vb: 'ViewBox', real_x, real_y1, real_y2, label, offset_px=25, color='#888888', policy=None)"
)


def test_existing_coordinate_dimensions_are_byte_identical():
    """Test 20. The backward-compat test: output bytes, signatures and exports."""

    vb = ViewBox(-5.0, 5.0, -2.0, 3.0, 400.0, 300.0, padding=20.0)

    assert svg_dimension_h(vb, 0.0, -2.0, 2.0, "BED 4 m", offset_px=42) == _H_GOLDEN
    assert svg_dimension_v(vb, 3.0, 0.0, 1.5, "DEPTH 1.5 m", offset_px=30) == _V_GOLDEN
    assert str(inspect.signature(svg_dimension_h)) == _SIG_H
    assert str(inspect.signature(svg_dimension_v)) == _SIG_V
    assert "svg_dimension_h" in technical_drawings_for_agents.__all__
    assert "svg_dimension_v" in technical_drawings_for_agents.__all__

    # The only in-repo dimension call sites, and they are not edited by this feature.
    source = (
        Path(technical_drawings_for_agents.__file__).resolve().parents[2]
        / "drawings" / "example" / "simple-section" / "source.py"
    )
    text = source.read_text(encoding="utf-8")
    assert "svg_dimension_h(" in text
    assert "svg_dimension_between" not in text
    assert "dimensions.yaml" not in text


# Captured from origin/main before this feature landed (see test 21's docstring).
_LAYOUT_GOLDEN_STDOUT = (
    "layout EXAMPLE-SITE: 2 placement(s), 12 feature(s), 4 check(s)\n"
    "  snap: packaged-unit pair: 2 member(s) on bearing 40.000°, pitch 6.000 m (spec)\n"
    "  - packaged-unit: 2\n"
    "layout checks: clean\n"
)
_LAYOUT_GOLDEN_GEOJSON_SHA256 = (
    "dd58317c1538b4a013b1396ded1a0835cb1255218281a2d33f94d10a97010a07"
)


def test_layout_command_output_is_unchanged_by_this_feature(tmp_path, capsys):
    """Test 21. layout.py gains only two delegating functions; this proves it.

    The goldens were captured by running the same two commands against a pristine
    ``git archive origin/main`` checkout before any of this landed.
    """

    assert main(["layout", str(EXAMPLE_LAYOUT), "--check-only"]) == 0
    captured = capsys.readouterr()
    assert captured.out == _LAYOUT_GOLDEN_STDOUT
    assert captured.err == ""

    out = tmp_path / "layout.geojson"
    assert main(["layout", str(EXAMPLE_LAYOUT), "--emit", "geojson", "--out", str(out)]) == 0
    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    assert digest == _LAYOUT_GOLDEN_GEOJSON_SHA256


def test_two_identical_runs_produce_identical_bytes(tmp_path, capsys):
    """Test 22. No timestamps, no dict-order leakage, one rounding path."""

    first, second = tmp_path / "a.svg", tmp_path / "b.svg"
    csv_a, csv_b = tmp_path / "a.csv", tmp_path / "b.csv"
    for svg, csv in ((first, csv_a), (second, csv_b)):
        assert main(
            ["dimensions", str(EXAMPLE_DIMENSIONS), "--emit", "svg", "--out", str(svg),
             "--setting-out", str(csv)]
        ) == 0
    capsys.readouterr()

    assert first.read_bytes() == second.read_bytes()
    assert csv_a.read_bytes() == csv_b.read_bytes()
    assert first.read_bytes()


def test_duplicate_placement_keys_are_rejected_at_load(tmp_path):
    """Test 23. Now gated twice: P10's register rule, then the dimensions index.

    The base spec noted "only the dimensions loader indexes keys, so layout behaviour
    is unaffected". P10 has since landed ``_validate_identities_are_unique`` inside
    ``canonical_instances``, which ``load_layout`` calls — so the file route now fails
    earlier, in ``LayoutError``. Both layers are asserted: the outer gate for the real
    path, and ``resolve``'s own guard for a caller that builds a ``Layout`` directly.
    """

    duplicated = [
        {"type": "unit", "origin_utm": [0, 0], "rotation_deg": 0, "size_m": [4, 2],
         "tag": "STA-A"},
        {"type": "unit", "origin_utm": [9, 9], "rotation_deg": 0, "size_m": [4, 2],
         "tag": "STA-A"},
    ]
    config = _write_layout(tmp_path, duplicated)
    with pytest.raises(LayoutError, match="duplicate placement identity"):
        load_layout(config)

    clean = _write_layout(tmp_path / "clean", _pair(7.3))
    dims = _write_dimensions(
        tmp_path / "clean",
        clean,
        {"dimensions": [{"id": "DIM-001", "kind": "clearance",
                         "from": {"placement": "A"}, "to": {"placement": "B"}}]},
    )
    dset = load_dimensions(dims)
    loaded = load_layout(clean)
    forged = Layout(
        id=loaded.id,
        crs=loaded.crs,
        components_root=loaded.components_root,
        types=loaded.types,
        placements=[
            Placement(type="unit", origin=(0.0, 0.0), rotation_deg=0.0, tag="STA-A",
                      size_m=(4.0, 2.0)),
            Placement(type="unit", origin=(9.0, 9.0), rotation_deg=0.0, tag="STA-A",
                      size_m=(4.0, 2.0)),
        ],
        checks=[],
        source=loaded.source,
    )
    with pytest.raises(DimensionError) as excinfo:
        resolve(dset, forged)
    assert "STA-A" in str(excinfo.value)
    assert "unit" in str(excinfo.value)


@pytest.mark.parametrize("label", ["CLEAR 2.0 m", "2,5 m", "gap 1.5"])
def test_a_label_containing_a_decimal_number_is_rejected(tmp_path, label):
    """Test 24. §4.4's enforceable boundary against a hand-typed measurement."""

    config = _write_layout(tmp_path, _pair(7.3))
    path = _write_dimensions(
        tmp_path,
        config,
        {"dimensions": [{"id": "DIM-001", "kind": "clearance", "label": label,
                         "from": {"placement": "A"}, "to": {"placement": "B"}}]},
        name=f"bad-{abs(hash(label))}",
    )

    with pytest.raises(DimensionError) as excinfo:
        load_dimensions(path)
    assert "expect_m" in str(excinfo.value)
    assert "expect_source" in str(excinfo.value)


@pytest.mark.parametrize("label", ["DN200", "Ø200", "3 off", "CLEAR", ""])
def test_a_label_without_a_decimal_number_loads(tmp_path, label):
    """Test 24 (converse). A prefix is a caption, and captions are allowed."""

    config = _write_layout(tmp_path, _pair(7.3))
    path = _write_dimensions(
        tmp_path,
        config,
        {"dimensions": [{"id": "DIM-001", "kind": "clearance", "label": label,
                         "from": {"placement": "A"}, "to": {"placement": "B"}}]},
        name=f"ok-{abs(hash(label))}",
    )

    assert load_dimensions(path).dimensions[0].label == label


def test_naming_binds_by_property_never_by_position(tmp_path):
    """Test 25. A coordinate binding breaks on exactly the move test 3 requires to work."""

    skids = [
        {"type": "dosing-skid", "origin_utm": [10.0, 10.0], "rotation_deg": 43.0,
         "size_m": [1.6, 1.0], "duty": "hypochlorite"},
        {"type": "dosing-skid", "origin_utm": [20.0, 10.0], "rotation_deg": 43.0,
         "size_m": [1.6, 1.0], "duty": "coagulant"},
        {"type": "dosing-skid", "origin_utm": [30.0, 10.0], "rotation_deg": 43.0,
         "size_m": [1.6, 1.0], "duty": "polymer"},
    ]
    dimension = {
        "id": "DIM-040",
        "kind": "centres",
        "from": {"placement": "W-D3"},
        "to": {"placement": "W-D9"},
    }

    dset, layout = _one(
        tmp_path / "ok", skids, dict(dimension), types=["dosing-skid"], checks=[],
        naming=[
            {"name": "W-D3", "where": {"type": "dosing-skid",
                                       "properties": {"duty": "hypochlorite"}}},
            {"name": "W-D9", "where": {"type": "dosing-skid",
                                       "properties": {"duty": "polymer"}}},
        ],
    )
    resolver = resolve(dset, layout)
    assert resolver.placements["W-D3"].properties["duty"] == "hypochlorite"
    assert measure(dset, layout)[0].value_m == pytest.approx(20.0, abs=1e-9)

    config = _write_layout(tmp_path / "pos", skids, types=["dosing-skid"], checks=[])
    positional = _write_dimensions(
        tmp_path / "pos", config,
        {"naming": [{"name": "W-D3", "where": {"origin_utm": [10.0, 10.0]}}],
         "dimensions": [dict(dimension)]},
        name="positional",
    )
    with pytest.raises(DimensionError) as excinfo:
        load_dimensions(positional)
    assert "origin_utm" in str(excinfo.value)
    assert "breaks the moment the thing moves" in str(excinfo.value)

    ambiguous_set, ambiguous_layout = _one(
        tmp_path / "amb", skids, dict(dimension), types=["dosing-skid"], checks=[],
        naming=[{"name": "W-D3", "where": {"type": "dosing-skid"}}],
    )
    with pytest.raises(DimensionError) as excinfo:
        resolve(ambiguous_set, ambiguous_layout)
    assert "matched 3 placement(s)" in str(excinfo.value)


def test_render_emits_an_annotation_box_per_dimension_for_the_legibility_checks(tmp_path):
    """Test 26. The P5 hand-off, asserted so it cannot silently disappear."""

    dset = load_dimensions(EXAMPLE_DIMENSIONS)
    layout = load_layout(dset.layout_path)
    measurements = measure(dset, layout)
    rows = setting_out_rows(dset, layout)
    table = setting_out_table(rows, dset.setting_out, crs=layout.crs, snap=dset.snap)

    elements, boxes = render_dimensions(_viewbox(), measurements, setting_out=table)

    assert len(elements) == len(measurements) + 1
    assert len(boxes) == len(measurements) + 1
    assert all(isinstance(box, AnnotationBox) for box in boxes)
    assert [box.id for box in boxes[:-1]] == [m.spec.id for m in measurements]
    assert boxes[-1].kind == "setting-out-table"
    for box in boxes:
        assert box.width > 0 and box.height > 0
        assert box.estimated is True


def test_cli_exit_codes_follow_the_layout_command_contract(tmp_path, capsys):
    """Test 27. 2 = I could not compute, 1 = I computed and it is wrong, 0 = clean."""

    assert main(["dimensions", str(EXAMPLE_DIMENSIONS), "--check-only"]) == 0
    capsys.readouterr()

    config = _write_layout(tmp_path, _pair(7.243))
    mismatch = _write_dimensions(
        tmp_path, config,
        {"dimensions": [{"id": "DIM-001", "kind": "clearance", "from": {"placement": "A"},
                         "to": {"placement": "B"}, "expect_m": 2.0,
                         "expect_source": "FA-130 foundation slab dwg"}]},
        name="mismatch",
    )
    assert main(["dimensions", str(mismatch), "--check-only"]) == 1
    assert "dimension-expectation" in capsys.readouterr().err

    assert main(["dimensions", str(mismatch), "--check-only", "--warn-only"]) == 0
    capsys.readouterr()

    unknown = _write_dimensions(
        tmp_path, config,
        {"dimensions": [{"id": "DIM-001", "kind": "clearance", "from": {"placement": "A"},
                         "to": {"placement": "NOPE"}}]},
        name="unknown",
    )
    assert main(["dimensions", str(unknown), "--check-only"]) == 2
    assert "error: dimensions failed" in capsys.readouterr().err

    broken = tmp_path / "broken.dimensions.yaml"
    broken.write_text("dimension_set: [not, a, mapping]\n", encoding="utf-8")
    assert main(["dimensions", str(broken)]) == 2
    assert "error: dimensions failed" in capsys.readouterr().err

    assert main(["dimensions", str(EXAMPLE_DIMENSIONS), "--emit", "svg"]) == 2
    assert "--emit requires --out" in capsys.readouterr().err


def test_the_dimensions_command_never_touches_drawing_status(tmp_path, capsys):
    """Test 28. Constraint 3, mechanically: a dimensioned sheet is still CONCEPT."""

    drawing = tmp_path / "drawing"
    drawing.mkdir()
    meta_path = drawing / "meta.yaml"
    meta_path.write_text(
        "number: STA-SITE-GA-001\n"
        "title: Basin WTP compound GA\n"
        "revision: A\n"
        "scale: 1:1250\n"
        "units: m\n"
        "date: 2026-07-25\n"
        "status: CONCEPT\n"
        "for_construction: false\n"
        "tool: technical_drawings_for_agents\n",
        encoding="utf-8",
    )
    before = meta_path.read_bytes()
    before_validate = DrawingMeta.load(meta_path).validate()

    svg = drawing / "out" / "dims.svg"
    assert main(
        ["dimensions", str(EXAMPLE_DIMENSIONS), "--emit", "svg", "--out", str(svg),
         "--setting-out", str(drawing / "out" / "setting-out.csv")]
    ) == 0
    capsys.readouterr()

    assert meta_path.read_bytes() == before
    assert DrawingMeta.load(meta_path).validate() == before_validate
    assert DrawingMeta.load(meta_path).status == "CONCEPT"
    assert DrawingMeta.load(meta_path).for_construction is False
    assert "ISSUED" not in svg.read_text(encoding="utf-8")

    # By source inspection: this feature writes neither field, anywhere.
    module = Path(technical_drawings_for_agents.__file__).resolve().parent / "components" / "dimensions.py"
    text = module.read_text(encoding="utf-8")
    assert "for_construction" not in text
    assert "meta.yaml" not in text.replace("``meta.yaml``", "")


def test_shipped_worked_example_measures_and_checks_clean(tmp_path, capsys):
    """Test 29. The shipped neutral example, exercised through every emit target."""

    dset = load_dimensions(EXAMPLE_DIMENSIONS)
    layout = load_layout(dset.layout_path)

    measurements = measure(dset, layout)
    findings = check_dimensions(dset, measurements, rows=setting_out_rows(dset, layout))

    assert len(measurements) == len(dset.dimensions) == 5
    assert [f for f in findings if f.severity == "error"] == []
    assert {m.spec.kind for m in measurements} == {
        "clearance", "centres", "envelope", "offset", "chainage",
    }
    for emit, suffix in (("svg", "svg"), ("csv", "csv"), ("yaml", "yaml"), ("json", "json")):
        out = tmp_path / f"example.{suffix}"
        assert main(["dimensions", str(EXAMPLE_DIMENSIONS), "--emit", emit, "--out", str(out)]) == 0
        assert out.read_text(encoding="utf-8").strip()
    capsys.readouterr()
    payload = json.loads((tmp_path / "example.json").read_text(encoding="utf-8"))
    assert [item["id"] for item in payload["dimensions"]] == [s.id for s in dset.dimensions]
    assert payload["snap"] == "effective"


# --------------------------------------------------------------------------- #
# extra guards for the two consumption decisions P6-FINAL left implicit
# --------------------------------------------------------------------------- #


def test_value_formatting_uses_p3s_single_float_to_text_path_not_a_second_one():
    """P6-FINAL says consume P3's rounding; the base spec mandated ROUND_HALF_UP.

    P3 shipped round-half-even with a documented reason (ties at mm precision are odd
    multiples of 1/16 m), and its docstring explicitly forbids the Decimal path. Two
    rounding paths in one package is the divergence this module exists to prevent, so
    this pins the delegation at the one value where the two answers differ.
    """

    from technical_drawings_for_agents.provenance import fmt

    assert format_value(0.0625, decimals=3, units_suffix="") == fmt(0.0625, 3) == "0.062"
    assert format_value(-0.0, decimals=3, units_suffix="") == "0.000"
    assert format_value(-6.039, decimals=3, direction_token="W") == "6.039 m W"
    with pytest.raises(DimensionError):
        format_value(float("nan"), decimals=3)


def test_the_same_dimension_renders_through_p1s_paper_space_viewport(tmp_path):
    """The projector is duck-typed, so a paper-mm Viewport works with no P6 change."""

    from technical_drawings_for_agents.sheet import PlotScale, SheetFrame, Viewport

    dset, layout = _one(
        tmp_path,
        _pair(7.3),
        {"id": "DIM-001", "kind": "clearance", "from": {"placement": "A"},
         "to": {"placement": "B"}},
    )
    measurement = measure(dset, layout)[0]
    viewport = Viewport(
        extent_m=(-20.0, -20.0, 20.0, 20.0),
        scale=PlotScale(250),
        frame=SheetFrame(20.0, 20.0, 380.0, 250.0),
    )

    elements, boxes = render_dimensions(viewport, [measurement])

    assert len(elements) == 1 and "2.000 m" in elements[0]
    assert boxes[0].width > 0


def test_the_witness_invariant_raises_rather_than_shipping_a_mismatched_line(tmp_path):
    """A measurement whose geometry and text disagree can never reach a sheet."""

    from dataclasses import replace as dc_replace

    dset, layout = _one(
        tmp_path,
        _pair(7.3),
        {"id": "DIM-001", "kind": "clearance", "from": {"placement": "A"},
         "to": {"placement": "B"}},
    )
    good = measure(dset, layout)[0]
    tampered = dc_replace(good, value_m=good.value_m + 0.5)

    with pytest.raises(DimensionError) as excinfo:
        render_dimensions(_viewbox(), [tampered])
    assert "witness geometry spans" in str(excinfo.value)
    assert "may never contradict" in str(excinfo.value)


def test_a_typed_bearing_axis_warns_that_it_duplicates_the_register(tmp_path):
    """§4.9. Supported, because a survey baseline is a legitimate declared input."""

    dset, layout = _one(
        tmp_path,
        _basin_clarifiers(),
        {"id": "DIM-010", "kind": "envelope", "of": {"type": "clarifier"},
         "axis": "bearing:130.252"},
        types=["clarifier"],
    )

    measurement = measure(dset, layout)[0]
    findings = check_dimensions(dset, [measurement])

    assert measurement.value_m == pytest.approx(12.599851977312937, abs=1e-9)
    assert [(f.severity, f.check) for f in findings] == [("warn", "dimension-axis")]
    assert "principal-cross" in findings[0].message


def test_an_empty_selection_is_an_error_and_a_non_parallel_group_is_too(tmp_path):
    """§4.6. An empty extent is a typo, not 0.000; a mean bearing would foreshorten."""

    dset, layout = _one(
        tmp_path,
        _basin_clarifiers(),
        {"id": "DIM-010", "kind": "envelope", "of": {"placements": ["STA-A"]},
         "axis": "principal"},
        types=["clarifier"],
    )
    assert measure(dset, layout)[0].value_m == pytest.approx(8.3, abs=1e-9)

    skew = [
        {"type": "unit", "origin_utm": [0, 0], "rotation_deg": 0.0, "size_m": [4, 2], "tag": "A"},
        {"type": "unit", "origin_utm": [0, 20], "rotation_deg": 25.0, "size_m": [4, 2],
         "tag": "B"},
    ]
    skew_set, skew_layout = _one(
        tmp_path / "skew", skew,
        {"id": "DIM-010", "kind": "envelope", "of": {"all": True}, "axis": "principal"},
        checks=[],
    )
    with pytest.raises(DimensionError, match="not parallel"):
        measure(skew_set, skew_layout)

    typo_set, typo_layout = _one(
        tmp_path / "typo", skew,
        {"id": "DIM-010", "kind": "envelope", "of": {"type": "nope"}, "axis": "easting"},
        checks=[],
    )
    with pytest.raises(DimensionError, match="unknown placement type"):
        measure(typo_set, typo_layout)


def test_unknown_schema_keys_are_rejected_so_a_typo_is_never_a_silent_default(tmp_path):
    """§3.1. Rejection at every level, mirroring the component spec loader."""

    config = _write_layout(tmp_path, _pair(7.3))
    for name, body in (
        ("top", {"dimensionz": []}),
        ("set", {"dimension_set_extra": True}),
        ("dim", {"dimensions": [{"id": "D", "kind": "clearance", "from": {"placement": "A"},
                                 "to": {"placement": "B"}, "offset_pxx": 3}]}),
        ("ref", {"dimensions": [{"id": "D", "kind": "clearance",
                                 "from": {"placement": "A", "datum": "P5"},
                                 "to": {"placement": "B"}}]}),
    ):
        document = {"dimension_set": {"id": "T", "layout": config.name}, **body}
        if "dimensions" not in body:
            document["dimensions"] = [
                {"id": "D", "kind": "clearance", "from": {"placement": "A"},
                 "to": {"placement": "B"}}
            ]
        path = tmp_path / f"{name}.dimensions.yaml"
        path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
        with pytest.raises(DimensionError):
            load_dimensions(path)


_HASH_SEED_SCRIPT = (
    "from technical_drawings_for_agents.cli import main; "
    "raise SystemExit(main(['dimensions', {dims!r}, '--emit', 'json', '--out', {out!r}]))"
)


def test_the_suite_runs_under_a_hostile_hash_seed(tmp_path):
    """No set-iteration order reaches an emitted byte (interlocks with P3)."""

    outputs = []
    for seed in ("0", "1", "12345"):
        result = subprocess.run(
            [sys.executable, "-c", _HASH_SEED_SCRIPT.format(
                dims=str(EXAMPLE_DIMENSIONS), out=str(tmp_path / f"h{seed}.json")
            )],
            env={"PYTHONHASHSEED": seed, "PATH": "/usr/bin:/bin"},
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        outputs.append((tmp_path / f"h{seed}.json").read_bytes())
    assert outputs[0] == outputs[1] == outputs[2]


def test_the_measurement_register_carries_full_precision_not_the_rounded_string(tmp_path):
    """§6. The rounded value is for the sheet and is never an input to a calculation."""

    dset, layout = _one(
        tmp_path,
        _basin_clarifiers(),
        {"id": "DIM-001", "kind": "clearance", "from": {"placement": "STA-A"},
         "to": {"placement": "STA-B"}},
        types=["clarifier"],
    )
    measurements = measure(dset, layout)

    payload = json.loads(dump_measurements(dset, measurements))
    entry = payload["dimensions"][0]

    assert entry["value_m"] == 1.9998519772665881
    assert entry["text"] == "2.000 m"
    assert entry["provenance"]["snap"] == "effective"
    assert entry["witness_mode"] == "edge"
