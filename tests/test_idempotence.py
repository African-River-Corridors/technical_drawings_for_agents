"""Whole-pipeline idempotence: regenerate-and-diff must be a real answer.

The harness drives the CLI twice over a list of **stages** —
``derive -> load -> snap -> build -> emit(geojson) -> emit(dxf)`` — each stage
contributing artifacts to a "compare these files across two runs" list. It stops
at emit because the site GA sheet step does not exist yet (#50); when it lands,
add it as one more artifact entry here plus one CLI flag, rather than rewriting
the harness.

Every test also asserts non-emptiness (ten records, a non-zero feature count,
exit code 0) before asserting equality: a test proving two empty files match is
worse than no test.

The fixture mirrors the live Basin shape — ten placements, two carrying a tag and
eight carrying nothing, across several types with three of one type separable only
by pose. It is built by a helper rather than committed as a data file, so it
cannot drift from the assertions, and the permutations are explicit constants
because an RNG has no place in a determinism test.
"""

from __future__ import annotations

import copy
import difflib
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import yaml

EXAMPLE_SPEC = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "technical_drawings_for_agents"
    / "components"
    / "examples"
    / "packaged_unit.yaml"
)

PERMUTATION = [7, 2, 0, 9, 4, 1, 6, 3, 8, 5]


# --------------------------------------------------------------------------- #
# G1/G4: order independence and two-run byte identity
# --------------------------------------------------------------------------- #


def test_shuffling_the_export_feature_order_leaves_the_register_byte_identical(tmp_path):
    """Re-exporting the QGIS layer in another feature order must change no bytes."""

    features = _site_features()
    variants = [features, list(reversed(features)), [features[index] for index in PERMUTATION]]
    registers = []

    for index, variant in enumerate(variants):
        work = tmp_path / f"run-{index}"
        config, export = _write_site(work, variant)

        assert _main(["layout", str(config), "--from-geojson", str(export)]) == 0

        text = (work / "placements.yaml").read_text(encoding="utf-8")
        assert len(yaml.safe_load(text)["instances"]) == 10
        registers.append(text)

    assert registers[0] == registers[1] == registers[2]


def test_two_consecutive_full_builds_are_byte_identical(tmp_path):
    """Text artifacts are byte-compared; the DXF goes through P3's ``emit_digest``.

    The register, the effective register and the emitted GeoJSON are all ours, all
    plain text, and all deterministic once the sort is fixed, so those three are
    asserted byte-for-byte.

    The DXF is compared via :func:`technical_drawings_for_agents.emit_digest` — P3's (#55) canonical
    digest of an emitted artifact, which masks the six items ``ezdxf`` re-rolls on
    every save (two GUIDs, ``$TDCREATE``/``$TDUPDATE``, and its two marker
    strings), so the digest answers "is this the same drawing?" rather than "was it
    saved at the same instant?". A **raw byte-identity** assertion is added
    alongside it for the opted-in path, where P3's ``fixed_dxf_metadata`` policy
    removes the noise at source rather than masking it after the fact. This
    emit is not opted in, so only the masked digest can be byte-asserted here —
    ``emit_digest`` is the seam that will let P2 widen it.

    PDF stays deliberately unasserted: P3 **declares** the LibreOffice path
    non-reproducible (time-derived ``/CreationDate`` plus a ``/ID`` pair, and
    ``SOURCE_DATE_EPOCH`` ignored), and ``emit_digest`` raises for it rather than
    hand back a value this test could appear to pass on.
    """

    from technical_drawings_for_agents import emit_digest

    config, export = _write_site(tmp_path, _site_features())
    artifacts = []
    opted_in_dxf_bytes = []

    for _ in range(2):
        geojson = tmp_path / "layout.geojson"
        effective = tmp_path / "effective.yaml"
        dxf = tmp_path / "layout.dxf"

        assert (
            _main(
                [
                    "layout",
                    str(config),
                    "--from-geojson",
                    str(export),
                    "--emit",
                    "geojson",
                    "--out",
                    str(geojson),
                    "--emit-register",
                    str(effective),
                ]
            )
            == 0
        )
        assert _main(["layout", str(config), "--emit", "dxf", "--out", str(dxf)]) == 0

        # ...and once more through P3's canonical emit, where the DXF's own bytes
        # are reproducible because the metadata noise never reaches them.
        opted_in = tmp_path / "opted-in.dxf"
        assert (
            _main(
                ["layout", str(config), "--emit", "dxf", "--out", str(opted_in), "--manifest"]
            )
            == 0
        )
        opted_in_dxf_bytes.append(opted_in.read_bytes())

        register = tmp_path / "placements.yaml"
        assert len(yaml.safe_load(register.read_text(encoding="utf-8"))["instances"]) == 10
        assert json.loads(geojson.read_text(encoding="utf-8"))["features"]
        artifacts.append(
            {
                "placements": register.read_text(encoding="utf-8"),
                "effective": effective.read_text(encoding="utf-8"),
                "geojson": geojson.read_text(encoding="utf-8"),
                "dxf": emit_digest(dxf),
                "dxf_local_semantic": _dxf_digest(dxf),
            }
        )

    assert artifacts[0] == artifacts[1]
    # the raw byte-identity assertion the base spec deferred to P3
    assert opted_in_dxf_bytes[0]
    assert opted_in_dxf_bytes[1] == opted_in_dxf_bytes[0]


# --------------------------------------------------------------------------- #
# one physical edit, one record's worth of diff
# --------------------------------------------------------------------------- #


def test_adding_one_placement_is_a_single_record_diff(tmp_path):
    """Inserted at the FRONT of the export, to prove source position is irrelevant."""

    config, export = _write_site(tmp_path, _site_features())
    assert _main(["layout", str(config), "--from-geojson", str(export)]) == 0
    before = (tmp_path / "placements.yaml").read_text(encoding="utf-8")

    moved = copy.deepcopy(_site_features())
    moved.insert(0, _feature("big-pump", 8.0, 4.0, 2.4, 1.7, 10.0))
    _write_export(export, moved)

    assert _main(["layout", str(config), "--from-geojson", str(export)]) == 0
    after = (tmp_path / "placements.yaml").read_text(encoding="utf-8")
    diff = list(difflib.unified_diff(before.splitlines(), after.splitlines()))
    removed = [line for line in diff if line.startswith("-") and not line.startswith("---")]

    assert sum(1 for line in diff if line.startswith("@@")) == 1  # exactly one hunk
    assert removed == [
        "-# ids: 2 of 10 placements carry a durable id (8 identified by pose only)."
    ]
    assert "# ids: 2 of 11 placements carry a durable id (9 identified by pose only)." in after
    for record in _record_texts(before):
        assert record in after  # every pre-existing record's text is unchanged


def test_deleting_a_placement_does_not_renumber_or_rewrite_the_others(tmp_path):
    """Nothing renumbers because nothing is numbered — the property a sequence id forfeits."""

    features = _site_features()
    config, export = _write_site(tmp_path, features)
    assert _main(["layout", str(config), "--from-geojson", str(export)]) == 0
    before = (tmp_path / "placements.yaml").read_text(encoding="utf-8")
    # one of the three same-type placements separable only by pose
    dropped = features[7]
    dropped_record = _record_for_feature(before, dropped)

    _write_export(export, [f for f in features if f is not dropped])

    assert _main(["layout", str(config), "--from-geojson", str(export)]) == 0
    after = (tmp_path / "placements.yaml").read_text(encoding="utf-8")
    diff = list(difflib.unified_diff(before.splitlines(), after.splitlines()))
    removed = [line[1:] for line in diff if line.startswith("-") and not line.startswith("---")]

    assert "# ids: 2 of 9 placements carry a durable id (7 identified by pose only)." in after
    assert removed == [
        "# ids: 2 of 10 placements carry a durable id (8 identified by pose only).",
        *dropped_record.splitlines(),
    ]
    remaining = [r for r in _record_texts(before) if r != dropped_record]
    assert remaining == _record_texts(after)  # same nine records, same order, byte for byte


def test_rederiving_from_an_unchanged_export_is_a_reported_no_op(tmp_path, capsys):
    """The write is skipped so the mtime holds — P2's (#54) staleness DAG keys on it."""

    config, export = _write_site(tmp_path, _site_features())
    register = tmp_path / "placements.yaml"

    assert _main(["layout", str(config), "--from-geojson", str(export)]) == 0
    before = register.read_text(encoding="utf-8")
    mtime = register.stat().st_mtime_ns

    assert _main(["layout", str(config), "--from-geojson", str(export)]) == 0

    assert register.read_text(encoding="utf-8") == before
    assert register.stat().st_mtime_ns == mtime
    # reported, not silent — idleness is fine, silence is the defect
    assert "register: unchanged (10 placement(s))" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# --check-register, and the flag exclusions
# --------------------------------------------------------------------------- #


def test_check_register_detects_a_hand_edit_and_a_stale_export_and_never_writes(tmp_path, capsys):
    config, export = _write_site(tmp_path, _site_features())
    register = tmp_path / "placements.yaml"
    assert _main(["layout", str(config), "--from-geojson", str(export)]) == 0
    clean = register.read_text(encoding="utf-8")
    assert _main(["layout", str(config), "--check-register"]) == 0

    # (a) a hand-edit that reorders two records
    data = yaml.safe_load(clean)
    data["instances"][0], data["instances"][1] = data["instances"][1], data["instances"][0]
    hand_edit = _header(clean) + yaml.safe_dump(
        data, sort_keys=False, default_flow_style=None, width=100
    )
    register.write_text(hand_edit, encoding="utf-8")
    hand_mtime = register.stat().st_mtime_ns

    assert _main(["layout", str(config), "--check-register"]) == 1
    assert register.read_text(encoding="utf-8") == hand_edit  # never writes
    assert register.stat().st_mtime_ns == hand_mtime
    output = capsys.readouterr().out
    assert "--- placements.yaml" in output and "+++ placements.yaml" in output
    # drift is the tool disagreeing with its own input, so --warn-only cannot wave it through
    assert _main(["layout", str(config), "--check-register", "--warn-only"]) == 1

    # (b) a stale register against a moved feature in the export
    register.write_text(clean, encoding="utf-8")
    restored_mtime = register.stat().st_mtime_ns
    stale = copy.deepcopy(_site_features())
    stale[0] = _move_feature(stale[0], dx=1.0, dy=0.0)
    moved_export = tmp_path / "moved.geojson"
    _write_export(moved_export, stale)

    assert (
        _main(["layout", str(config), "--from-geojson", str(moved_export), "--check-register"]) == 1
    )
    assert register.read_text(encoding="utf-8") == clean
    assert register.stat().st_mtime_ns == restored_mtime
    assert (
        _main(
            [
                "layout",
                str(config),
                "--from-geojson",
                str(moved_export),
                "--check-register",
                "--warn-only",
            ]
        )
        == 1
    )


def test_the_register_flag_exclusions_are_enforced_in_run_layout_not_by_argparse(tmp_path, capsys):
    """A single argparse mutually-exclusive group cannot express this relationship.

    ``--from-geojson`` *may* pair with ``--check-register`` (refresh, then compare),
    while ``--canonicalise-register`` excludes both. An argparse group is a flat
    "at most one of these", so a future refactor to one would either loosen the
    exclusion or break the valid pairing. This test fails loudly if that happens:
    argparse rejects by raising ``SystemExit(2)`` from ``parse_args``, whereas the
    explicit check in ``run_layout`` *returns* 2.
    """

    config, export = _write_site(tmp_path, _site_features())
    assert _main(["layout", str(config), "--from-geojson", str(export)]) == 0
    capsys.readouterr()

    # the valid pairing must stay valid
    assert _main(["layout", str(config), "--from-geojson", str(export), "--check-register"]) == 0

    for extra in (["--from-geojson", str(export)], ["--check-register"]):
        # a returned 2, not a raised SystemExit: the check lives in run_layout
        assert _main(["layout", str(config), "--canonicalise-register", *extra]) == 2
        error = capsys.readouterr().err
        assert "--canonicalise-register" in error and extra[0] in error  # names the flags

    assert (
        _main(
            [
                "layout",
                str(config),
                "--canonicalise-register",
                "--from-geojson",
                str(export),
                "--check-register",
            ]
        )
        == 2
    )
    error = capsys.readouterr().err
    assert "--from-geojson" in error and "--check-register" in error

    # --register-source is meaningless without --canonicalise-register
    assert _main(["layout", str(config), "--register-source", "x"]) == 2
    assert "--register-source" in capsys.readouterr().err


def test_a_refresh_that_fails_on_an_unmapped_type_writes_no_register(tmp_path, capsys):
    """Reject before the first byte: the old flow left an unvalidated artifact on disk.

    Writing then failing on load meant a later run could load a register that had
    never validated. Failing early is strictly better — and it must not clobber a
    good register either.
    """

    features = _site_features()
    config, export = _write_site(tmp_path, features)
    register = tmp_path / "placements.yaml"

    unmapped = [*copy.deepcopy(features), _feature("mystery-vessel", 60.0, 60.0, 3.0, 2.0, 0.0)]
    _write_export(export, unmapped)

    assert _main(["layout", str(config), "--from-geojson", str(export)]) == 2
    assert not register.exists()  # nothing written at all
    assert "mystery-vessel" in capsys.readouterr().err

    # and a pre-existing good register is left untouched, not half-rewritten
    _write_export(export, features)
    assert _main(["layout", str(config), "--from-geojson", str(export)]) == 0
    good = register.read_text(encoding="utf-8")
    good_mtime = register.stat().st_mtime_ns
    _write_export(export, unmapped)

    assert _main(["layout", str(config), "--from-geojson", str(export)]) == 2
    assert register.read_text(encoding="utf-8") == good
    assert register.stat().st_mtime_ns == good_mtime


def test_check_register_refuses_to_be_asked_to_write(tmp_path):
    """--check-register never writes, so pairing it with an emit is a usage error."""

    config, export = _write_site(tmp_path, _site_features())
    assert _main(["layout", str(config), "--from-geojson", str(export)]) == 0

    assert (
        _main(
            [
                "layout",
                str(config),
                "--check-register",
                "--emit",
                "geojson",
                "--out",
                str(tmp_path / "out.geojson"),
            ]
        )
        == 2
    )
    assert not (tmp_path / "out.geojson").exists()


# --------------------------------------------------------------------------- #
# fixture helpers
# --------------------------------------------------------------------------- #


def _main(args: list[str]) -> int:
    from technical_drawings_for_agents.cli import main

    return main(args)


def _write_site(tmp_path: Path, features: list[dict[str, Any]]) -> tuple[Path, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    root = tmp_path / "components"
    root.mkdir(parents=True, exist_ok=True)
    (root / "unit.yaml").write_text(EXAMPLE_SPEC.read_text(encoding="utf-8"), encoding="utf-8")
    type_names = sorted({feature["properties"]["type"] for feature in features})
    config = {
        "layout": {"id": "IDEMPOTENCE-SITE", "crs": "EPSG:32630"},
        "components": {
            "root": "components",
            "types": {name: ["unit.yaml"] for name in type_names},
        },
        "editor": {"type_field": "type", "tag_field": "tag", "id_field": "id"},
        "placements": "placements.yaml",
    }
    config_path = tmp_path / "layout.yaml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    export = tmp_path / "export.geojson"
    _write_export(export, features)
    return config_path, export


def _write_export(path: Path, features: list[dict[str, Any]]) -> None:
    path.write_text(
        json.dumps({"type": "FeatureCollection", "features": features}), encoding="utf-8"
    )


def _site_features() -> list[dict[str, Any]]:
    """Ten features mirroring the live Basin shape: 2 tagged, 8 carrying nothing."""

    return [
        _feature("clarifier", 0.0, 30.0, 8.3, 5.3, 38.876, tag="STA-A"),
        _feature("clarifier", 6.0, 24.0, 8.3, 5.3, 41.627, tag="STA-B"),
        _feature("big-pump", 20.0, 10.0, 2.4, 1.7, 10.0),
        _feature("water-tank", 15.0, 18.0, 1.5, 1.5, 0.0),
        _feature("transformer", 5.0, 5.0, 2.8, 2.0, 90.0),
        _feature("small-pump", 25.0, 7.0, 1.15, 0.55, 30.0),
        _feature("panel-slab", 18.0, 2.0, 2.6, 0.8, 0.0),
        _feature("dosing-skid", 30.0, 0.0, 1.6, 1.0, 43.034),
        _feature("dosing-skid", 32.0, 3.0, 1.6, 1.0, 43.034),
        _feature("dosing-skid", 34.0, 6.0, 1.6, 1.0, 43.034),
    ]


def _feature(
    type_name: str,
    cx: float,
    cy: float,
    long_m: float,
    short_m: float,
    rot_deg: float,
    *,
    tag: str | None = None,
    placement_id: str | None = None,
) -> dict[str, Any]:
    properties: dict[str, Any] = {"type": type_name}
    if tag is not None:
        properties["tag"] = tag
    if placement_id is not None:
        properties["id"] = placement_id
    return {
        "type": "Feature",
        "properties": properties,
        "geometry": {"type": "Polygon", "coordinates": [_rect(cx, cy, long_m, short_m, rot_deg)]},
    }


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


def _move_feature(feature: dict[str, Any], *, dx: float, dy: float) -> dict[str, Any]:
    moved = copy.deepcopy(feature)
    moved["geometry"]["coordinates"][0] = [
        [x + dx, y + dy] for x, y in moved["geometry"]["coordinates"][0]
    ]
    return moved


def _record_texts(register: str) -> list[str]:
    """Split a register body into its per-record text blocks."""

    records: list[str] = []
    current: list[str] = []
    for line in register.splitlines():
        if line.startswith("- "):
            if current:
                records.append("\n".join(current))
            current = [line]
        elif current and line.startswith("  "):
            current.append(line)
    if current:
        records.append("\n".join(current))
    return records


def _record_for_feature(register: str, feature: dict[str, Any]) -> str:
    cx, cy = _centroid(feature)
    for record in _record_texts(register):
        if f"type: {feature['properties']['type']}" in record and (
            f"origin_utm: [{cx:.1f}, {cy:.1f}]" in record
        ):
            return record
    raise AssertionError(f"no register record for {feature['properties']['type']} at {cx}, {cy}")


def _centroid(feature: dict[str, Any]) -> tuple[float, float]:
    points = feature["geometry"]["coordinates"][0][:-1]
    return (
        sum(point[0] for point in points) / len(points),
        sum(point[1] for point in points) / len(points),
    )


def _header(register: str) -> str:
    return register.split("instances:\n", maxsplit=1)[0]


def _dxf_digest(path: Path) -> str:
    """A normalised *semantic* digest of model space, kept alongside ``emit_digest``.

    P3 (#55) landed ``emit_digest``, which is now the assertion of record. This one
    is retained as an independent second opinion: it reads model space through
    ``ezdxf`` and digests entity geometry, so it would still catch a drawing change
    that a purely textual mask happened to hide. Two digests derived by different
    routes agreeing is worth more than either alone.

    Imported plainly, never via ``importorskip``: this test must not be skippable.
    """

    import ezdxf

    doc = ezdxf.readfile(path)
    rows = sorted(
        (entity.dxf.layer, entity.dxftype(), _entity_coords(entity))
        for entity in doc.modelspace()
    )
    assert rows, f"{path.name}: no model-space entities to digest"
    return hashlib.sha256(repr(rows).encode("utf-8")).hexdigest()


def _entity_coords(entity) -> tuple[float, ...]:
    kind = entity.dxftype()
    coords: list[float] = []
    if kind == "LINE":
        coords.extend(entity.dxf.start)
        coords.extend(entity.dxf.end)
    elif kind == "LWPOLYLINE":
        for x, y, *_ in entity.get_points():
            coords.extend([x, y])
    elif kind == "CIRCLE":
        coords.extend(entity.dxf.center)
        coords.append(entity.dxf.radius)
    elif kind == "TEXT":
        coords.extend(entity.dxf.insert)
        coords.append(entity.dxf.height)
        coords.append(entity.dxf.rotation)
    return tuple(round(float(coord), 6) for coord in coords)
