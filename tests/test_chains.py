"""P11 acceptance tests — a chain may not contradict its own overall.

Every test is numbered against ``docs/specs/P11-chain-consistency.md`` §5. The
load-bearing one is 3: the check compares the **displayed** values, so its fixture is a
chain whose model values sum *exactly* (asserted with ``==`` on the floats) while its
printed values do not. An implementation that compares model values passes every other
test here and is worthless; that one fails it.

The fixtures are synthetic and depend on no project file. The Basin numbers in
``_basin_clarifiers`` are the live post-snap poses copied from
``basin.effective.yaml`` — the same constants ``test_dimensions.py`` already carries — so
test 1 exercises the real geometry without reading the vault.
"""

from __future__ import annotations

import math
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest
import yaml

import technical_drawings_for_agents
from technical_drawings_for_agents.cli import main
from technical_drawings_for_agents.components.dimensions import (
    Chain,
    DimensionError,
    chain_tolerance_mm,
    check_chains,
    check_dimensions,
    displayed_value,
    load_dimensions,
    measure,
    render_dimensions,
    setting_out_rows,
    setting_out_table,
)
from technical_drawings_for_agents.components.layout import load_layout
from technical_drawings_for_agents.svg import ViewBox

from .test_layout import _write_layout

GOLDENS = Path(__file__).resolve().parent / "goldens"
EXAMPLES = Path(technical_drawings_for_agents.__file__).resolve().parent / "components" / "examples"
EXAMPLE_DIMENSIONS = EXAMPLES / "site_dimensions.yaml"

# The real post-snap Basin clarifier poses, from basin.effective.yaml.
STA_A = (788603.55, 322347.143)
STA_B = (788608.266, 322341.571)
BASIN_ROT = 40.252
BASIN_SIZE = [8.3, 5.3]

SRC = "P11 test fixture — synthetic control point, not survey"

#: Three bays of this display 2.499 each at 3 dp; three of them are 7.4985, which
#: displays 7.498. The 1 mm contradiction issue #130 is about, with no float fuzz:
#: ``2.4995 * 3 == 7.4985`` exactly in IEEE 754 (asserted in test 2).
BAY_M = 2.4995
RUN_M = 7.4985

#: 1/16 m is exactly representable and is a genuine round-half-even tie at mm precision,
#: so ``0.0625`` displays ``0.062`` — the neighbourhood P3's docstring calls out.
TIE_M = 0.0625


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #


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


def _build(
    tmp_path: Path,
    *,
    dimensions: list[dict],
    chains: list[dict] | None = None,
    datums: list[dict] | None = None,
    placements: list[dict] | None = None,
    defaults: dict | None = None,
    name: str = "d",
) -> tuple:
    """Write a layout + dimensions file and load both. Returns ``(dset, layout)``."""

    config = _write_layout(
        tmp_path,
        placements if placements is not None else [{"type": "unit", "origin_utm": [0.0, 0.0]}],
        types=["clarifier", "unit"],
    )
    document: dict = {"dimension_set": {"id": "TEST-CHAINS", "layout": config.name}}
    if defaults is not None:
        document["defaults"] = defaults
    if datums is not None:
        document["datums"] = datums
    document["dimensions"] = dimensions
    if chains is not None:
        document["chains"] = chains
    path = tmp_path / f"{name}.dimensions.yaml"
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return load_dimensions(path), load_layout(config)


def _span(name: str, length_m: float, row: float, **extra) -> tuple[list[dict], dict]:
    """A ``centres`` dimension of exactly ``length_m``, between two datums on one row.

    ``math.dist`` over a purely eastward offset returns the offset itself, bit for bit, so
    the fixture's model value is the literal float written in the YAML and every rounding
    assertion in this file is exact rather than approximate.
    """

    datums = [
        {"name": f"{name}-w", "point": [0.0, row], "source": SRC},
        {"name": f"{name}-e", "point": [length_m, row], "source": SRC},
    ]
    dimension = {
        "id": name,
        "kind": "centres",
        "from": {"datum": f"{name}-w"},
        "to": {"datum": f"{name}-e"},
        **extra,
    }
    return datums, dimension


def _run(
    tmp_path: Path,
    *,
    bay_m: float = BAY_M,
    overall_m: float = RUN_M,
    bays: int = 3,
    chain: dict | None = None,
    overall_extra: dict | None = None,
    defaults: dict | None = None,
    name: str = "run",
) -> tuple:
    """``bays`` components of ``bay_m`` plus an overall of ``overall_m``, as one chain."""

    datums: list[dict] = []
    dimensions: list[dict] = []
    ids: list[str] = []
    for index in range(bays):
        bay_datums, dimension = _span(f"bay-{index}", bay_m, 10.0 * index)
        datums.extend(bay_datums)
        dimensions.append(dimension)
        ids.append(dimension["id"])
    overall_datums, overall = _span("run-overall", overall_m, -50.0, **(overall_extra or {}))
    datums.extend(overall_datums)
    dimensions.append(overall)
    declared = {"id": "the-run", "overall": "run-overall", "components": ids}
    declared.update(chain or {})
    return _build(
        tmp_path,
        dimensions=dimensions,
        chains=[declared],
        datums=datums,
        defaults=defaults,
        name=name,
    )


def _findings(dset, layout) -> list:
    return check_chains(dset, {m.spec.id: m.value_m for m in measure(dset, layout)})


def _values(dset, layout) -> dict[str, float]:
    return {m.spec.id: m.value_m for m in measure(dset, layout)}


# --------------------------------------------------------------------------- #
# 1 — the real Basin cross-axis is consistent
# --------------------------------------------------------------------------- #


def _basin_cross_axis(tmp_path) -> tuple:
    """Two 5.3 m raft widths either side of the 2.0 m clearance, against the envelope."""

    return _build(
        tmp_path,
        placements=_basin_clarifiers(),
        dimensions=[
            {"id": "raft-a-width", "kind": "envelope", "axis": "principal-cross",
             "of": {"placements": ["STA-A"]}},
            {"id": "raft-clear", "kind": "clearance", "from": {"placement": "STA-A"},
             "to": {"placement": "STA-B"}},
            {"id": "raft-b-width", "kind": "envelope", "axis": "principal-cross",
             "of": {"placements": ["STA-B"]}},
            {"id": "envelope-cross", "kind": "envelope", "axis": "principal-cross",
             "of": {"placements": ["STA-A", "STA-B"]}},
        ],
        chains=[
            {
                "id": "raft-cross-axis",
                "overall": "envelope-cross",
                "components": ["raft-a-width", "raft-clear", "raft-b-width"],
                "note": "5.3 + 2.0 + 5.3 across the platform",
            }
        ],
        name="basin",
    )


def test_a_consistent_chain_passes(tmp_path):
    """Test 1. The live Basin numbers: 5.300 + 2.000 + 5.300 against 12.600."""

    dset, layout = _basin_cross_axis(tmp_path)
    measurements = {m.spec.id: m for m in measure(dset, layout)}

    assert [measurements[i].text for i in
            ("raft-a-width", "raft-clear", "raft-b-width", "envelope-cross")] == [
        "5.300 m", "2.000 m", "5.300 m", "12.600 m",
    ]
    # The exact model values do NOT sum: 5.3 + 1.99985... + 5.3 != 12.59985...
    assert measurements["raft-clear"].value_m == pytest.approx(1.9998519772665881, abs=1e-9)
    assert measurements["envelope-cross"].value_m == pytest.approx(12.599851977, abs=1e-6)

    assert _findings(dset, layout) == []
    assert [f for f in check_dimensions(dset, measure(dset, layout))
            if f.check == "chain-consistency"] == []


# --------------------------------------------------------------------------- #
# 2, 15 — the three-bay 2.4995 failure, and what the message must carry
# --------------------------------------------------------------------------- #


def test_rounded_components_not_summing_to_the_rounded_overall_is_an_error(tmp_path):
    """Test 2. Issue #130's worked example, end to end."""

    dset, layout = _run(tmp_path)
    values = _values(dset, layout)

    # The premise: at mm precision each bay reads 2.499 and the run reads 7.498.
    assert BAY_M * 3 == RUN_M  # the model agrees exactly; only the display does not
    assert [values[f"bay-{i}"] for i in range(3)] == [BAY_M, BAY_M, BAY_M]
    assert values["run-overall"] == RUN_M
    assert [displayed_value(s, values[s.id])[0] for s in dset.dimensions] == [
        "2.499", "2.499", "2.499", "7.498",
    ]

    findings = check_chains(dset, values)

    assert len(findings) == 1
    finding = findings[0]
    assert (finding.severity, finding.check) == ("error", "chain-consistency")
    assert "1.0 mm" in finding.message
    assert "components sum to 7.497 m" in finding.message
    assert "displays 7.498 m" in finding.message
    for index in range(3):
        assert f"bay-{index} 2.499" in finding.message


def test_the_finding_names_every_component_with_its_displayed_value(tmp_path):
    """Test 15. Pinned so a refactor cannot quietly reduce it to a bare total."""

    dset, layout = _run(tmp_path, chain={"note": "three equal bays, 2.4995 each"})

    message = check_chains(dset, _values(dset, layout))[0].message

    assert "chain 'the-run'" in message
    assert "overall 'run-overall'" in message
    assert "components: bay-0 2.499 + bay-1 2.499 + bay-2 2.499" in message
    assert "at 3 dp" in message
    assert "note: three equal bays, 2.4995 each" in message
    # Declaration order is the order a reader adds them up in.
    assert message.index("bay-0 2.499") < message.index("bay-1 2.499") < message.index(
        "bay-2 2.499"
    )


# --------------------------------------------------------------------------- #
# 3 — THE test: displayed, not model
# --------------------------------------------------------------------------- #


def test_the_check_compares_displayed_values_not_model_values(tmp_path):
    """Test 3. Model values sum EXACTLY; displayed values are 1 mm apart.

    ``0.0625`` is exactly representable and is a round-half-even tie at 3 dp, so it
    displays ``0.062``: two of them print 0.124 while the run prints 0.125. There is no
    float slack anywhere in this fixture — ``0.0625 + 0.0625 == 0.125`` is exact — so a
    model-value comparison cannot fail it, and must therefore report clean on a sheet that
    contradicts itself. That is the defect this test exists to catch.
    """

    dset, layout = _run(tmp_path, bay_m=TIE_M, overall_m=TIE_M * 2, bays=2, name="tie")
    values = _values(dset, layout)

    model_sum = values["bay-0"] + values["bay-1"]
    assert model_sum == values["run-overall"]  # exact, not approx
    assert [displayed_value(s, values[s.id])[0] for s in dset.dimensions] == [
        "0.062", "0.062", "0.125",
    ]

    findings = check_chains(dset, values)

    assert len(findings) == 1
    assert "components sum to 0.124 m" in findings[0].message
    assert "displays 0.125 m" in findings[0].message
    assert "1.0 mm apart" in findings[0].message


# --------------------------------------------------------------------------- #
# 4 — the tolerance is derived, not hard-coded
# --------------------------------------------------------------------------- #


def test_tolerance_is_derived_from_decimals_not_hard_coded_mm(tmp_path):
    """Test 4. The same discrepancy passes at 2 dp and fails at 3 dp."""

    assert chain_tolerance_mm(3) == 0.5
    assert chain_tolerance_mm(2) == 5.0
    assert chain_tolerance_mm(4) == 0.05

    at_three, layout_three = _run(tmp_path, name="three")
    at_two, layout_two = _run(tmp_path, defaults={"decimals": 2}, name="two")

    assert [displayed_value(s, 2.4995)[0] for s in at_two.dimensions][:1] == ["2.50"]
    assert len(_findings(at_three, layout_three)) == 1
    assert _findings(at_two, layout_two) == []


# --------------------------------------------------------------------------- #
# 5-11 — everything a chain can get wrong is a load error
# --------------------------------------------------------------------------- #


def test_mixed_decimals_within_a_chain_is_a_load_error(tmp_path):
    with pytest.raises(DimensionError) as excinfo:
        _run(tmp_path, overall_extra={"decimals": 2}, name="mixed")

    message = str(excinfo.value)
    assert "the-run" in message
    assert "mixed decimals" in message
    assert "3 dp" in message and "2 dp" in message


def test_chain_with_fewer_than_two_components_is_a_load_error(tmp_path):
    with pytest.raises(DimensionError) as excinfo:
        _run(tmp_path, bays=1, name="one")

    assert "components lists 1 id(s), needs at least 2" in str(excinfo.value)
    assert "the-run" in str(excinfo.value)


@pytest.mark.parametrize("field", ["overall", "components"])
def test_chain_naming_an_unknown_dimension_id_is_a_load_error(tmp_path, field):
    override = (
        {"overall": "no-such-dim"}
        if field == "overall"
        else {"components": ["bay-0", "no-such-dim", "bay-2"]}
    )

    with pytest.raises(DimensionError) as excinfo:
        _run(tmp_path, chain=override, name=f"unknown-{field}")

    message = str(excinfo.value)
    assert "the-run" in message
    assert "no-such-dim" in message
    assert "bay-0" in message  # the known ids, so the author can see the typo


def test_a_dimension_cannot_be_both_overall_and_component_of_one_chain(tmp_path):
    with pytest.raises(DimensionError) as excinfo:
        _run(
            tmp_path,
            chain={"components": ["bay-0", "bay-1", "run-overall"]},
            name="self",
        )

    message = str(excinfo.value)
    assert "run-overall" in message
    assert "the-run" in message
    assert "cannot be one of its own parts" in message


def test_a_dimension_cannot_appear_in_two_chains_components(tmp_path):
    datums: list[dict] = []
    dimensions: list[dict] = []
    for index, name in enumerate(("bay-0", "bay-1", "run-a", "run-b")):
        span_datums, dimension = _span(name, BAY_M, 10.0 * index)
        datums.extend(span_datums)
        dimensions.append(dimension)

    with pytest.raises(DimensionError) as excinfo:
        _build(
            tmp_path,
            dimensions=dimensions,
            datums=datums,
            chains=[
                {"id": "chain-a", "overall": "run-a", "components": ["bay-0", "bay-1"]},
                {"id": "chain-b", "overall": "run-b", "components": ["bay-1", "bay-0"]},
            ],
            name="twice",
        )

    message = str(excinfo.value)
    assert "bay-1" in message
    assert "already a component of chain 'chain-a'" in message


def test_duplicate_chain_ids_are_a_load_error(tmp_path):
    datums: list[dict] = []
    dimensions: list[dict] = []
    for index, name in enumerate(("bay-0", "bay-1", "bay-2", "bay-3", "run-a", "run-b")):
        span_datums, dimension = _span(name, BAY_M, 10.0 * index)
        datums.extend(span_datums)
        dimensions.append(dimension)

    with pytest.raises(DimensionError) as excinfo:
        _build(
            tmp_path,
            dimensions=dimensions,
            datums=datums,
            chains=[
                {"id": "same", "overall": "run-a", "components": ["bay-0", "bay-1"]},
                {"id": "same", "overall": "run-b", "components": ["bay-2", "bay-3"]},
            ],
            name="dupe",
        )

    assert "duplicate chain id 'same'" in str(excinfo.value)


def test_unknown_key_in_a_chain_entry_is_rejected(tmp_path):
    """Test 11. Including ``auto_adjust``, which §4.4 forbids outright."""

    with pytest.raises(DimensionError) as excinfo:
        _run(tmp_path, chain={"auto_adjust": True}, name="autoadjust")

    message = str(excinfo.value)
    assert "unknown key(s) auto_adjust" in message
    assert "chains[0]" in message


# --------------------------------------------------------------------------- #
# 12 — warn reports without failing the build
# --------------------------------------------------------------------------- #


def test_severity_warn_reports_but_does_not_fail(tmp_path, capsys):
    """Test 12. A CONCEPT sheet whose drafter knows and has not resolved it yet."""

    strict, layout = _run(tmp_path, name="strict")
    lenient, _ = _run(tmp_path, chain={"severity": "warn"}, name="lenient")

    assert [f.severity for f in _findings(strict, layout)] == ["error"]
    assert [f.severity for f in _findings(lenient, layout)] == ["warn"]

    assert main(["dimensions", str(strict.source), "--check-only"]) == 1
    captured = capsys.readouterr()
    assert "chain-consistency" in captured.err
    assert "1 chain(s)" in captured.out  # only printed when a chain is declared
    assert main(["dimensions", str(lenient.source), "--check-only"]) == 0
    assert "chain-consistency" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# 13 — a chain may mix dimension kinds
# --------------------------------------------------------------------------- #


def test_a_chain_over_mixed_dimension_kinds_is_allowed(tmp_path):
    """Test 13. A real run is widths and a clearance against an envelope (§4.6)."""

    dset, layout = _basin_cross_axis(tmp_path)
    kinds = {s.id: s.kind for s in dset.dimensions}
    chain = dset.chains[0]

    assert {kinds[name] for name in chain.components} == {"envelope", "clearance"}
    assert kinds[chain.overall] == "envelope"
    assert _findings(dset, layout) == []


# --------------------------------------------------------------------------- #
# 14 — the backward-compatibility test
# --------------------------------------------------------------------------- #


def _example_render() -> str:
    """The shipped example's measurements, table, findings, boxes and SVG, as text.

    The golden this is compared against was produced by running THIS function's body
    against ``origin/main`` — pre-P11 code, via ``PYTHONPATH`` — exactly as
    ``goldens/README.md`` describes for the P3 and P9 goldens. It therefore answers the
    only question backward compatibility asks: are the bytes the same as before?
    """

    dset = load_dimensions(EXAMPLE_DIMENSIONS)
    layout = load_layout(dset.layout_path)
    measurements = measure(dset, layout)
    rows = setting_out_rows(dset, layout)
    table = setting_out_table(rows, dset.setting_out, crs=layout.crs, snap=dset.snap)
    vb = ViewBox(-30.0, 30.0, -30.0, 30.0, 1000.0, 700.0, padding=60.0)
    elements, boxes = render_dimensions(vb, measurements, setting_out=table)
    findings = check_dimensions(dset, measurements, rows=rows)

    out: list[str] = ["# measurements"]
    for m in measurements:
        out.append(f"{m.spec.id}\t{m.spec.kind}\t{m.value_m!r}\t{m.text}\t{m.witness_mode}")
    out.append("# setting out")
    out.append(table.caption)
    for row in table.rows:
        out.append("\t".join(row))
    out.append("# findings")
    out.extend(str(f) for f in findings)
    out.append("# annotation boxes")
    for box in boxes:
        out.append(f"{box.id}\t{box.x!r}\t{box.y!r}\t{box.width!r}\t{box.height!r}\t{box.text}")
    out.append("# svg")
    out.extend(elements)
    return "\n".join(out) + "\n"


def test_no_chains_declared_means_no_findings_and_no_behaviour_change():
    """Test 14. The shipped example has no ``chains:`` and must be byte-identical."""

    dset = load_dimensions(EXAMPLE_DIMENSIONS)
    layout = load_layout(dset.layout_path)
    measurements = measure(dset, layout)

    assert dset.chains == ()
    assert check_chains(dset, {m.spec.id: m.value_m for m in measurements}) == []
    assert [
        f.check for f in check_dimensions(dset, measurements) if f.check == "chain-consistency"
    ] == []
    assert _example_render() == (
        GOLDENS / "dimensions_example_pre_p11.txt"
    ).read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# beyond the numbered fifteen: the judgement calls the spec left open
# --------------------------------------------------------------------------- #


def test_a_repeated_component_id_within_one_chain_is_a_load_error(tmp_path):
    """§3.1 rule 4 covers two chains; the same id twice in ONE chain is the same defect."""

    with pytest.raises(DimensionError) as excinfo:
        _run(tmp_path, chain={"components": ["bay-0", "bay-0", "bay-1"]}, name="repeat")

    assert "already appears in chain 'the-run'" in str(excinfo.value)


def test_a_nested_chain_may_reuse_an_overall_as_a_component(tmp_path):
    """A sub-run's overall IS a bay of the bigger run, so this is allowed deliberately."""

    datums: list[dict] = []
    dimensions: list[dict] = []
    for index, (name, length) in enumerate(
        (("bay-0", TIE_M), ("bay-1", TIE_M), ("sub-run", TIE_M * 2),
         ("bay-2", TIE_M * 2), ("whole-run", TIE_M * 4))
    ):
        span_datums, dimension = _span(name, length, 10.0 * index)
        datums.extend(span_datums)
        dimensions.append(dimension)

    dset, layout = _build(
        tmp_path,
        dimensions=dimensions,
        datums=datums,
        chains=[
            {"id": "sub", "overall": "sub-run", "components": ["bay-0", "bay-1"]},
            {"id": "whole", "overall": "whole-run", "components": ["sub-run", "bay-2"]},
        ],
        name="nested",
    )

    findings = check_chains(dset, _values(dset, layout))

    # 'sub' contradicts itself (0.062 + 0.062 vs 0.125); 'whole' does not (0.125 + 0.125).
    assert [f.message.split(":")[0] for f in findings] == ["chain 'sub'"]


def test_the_same_nominal_run_expressed_as_running_coordinates_does_not_trip_the_check(tmp_path):
    """The check reports what the sheet says, not what the author meant — and that is right.

    Issue #130's example is knife-edge in binary. ``2.4995`` as a literal is *below* the
    decimal tie, so it displays ``2.499``; but the same nominal bay obtained as a
    difference of running grid coordinates (``7.4985 - 4.999``) lands a hair *above* it and
    displays ``2.500``, which cancels the discrepancy and reports clean. Both sheets are
    internally consistent as printed, so both must pass — pinned here because it is exactly
    the trap a later "tidy-up" of these fixtures would fall into, and because a discrepancy
    that flips on the last bit of a coordinate is not something a reader can predict by
    hand. That is the argument for the check, not against it.
    """

    running = [0.0, BAY_M, BAY_M + BAY_M, RUN_M]
    assert [f"{running[i + 1] - running[i]:.3f}" for i in range(3)] == [
        "2.499", "2.499", "2.500",
    ]

    datums: list[dict] = []
    dimensions: list[dict] = []
    for index in range(3):
        datums.extend(
            [
                {"name": f"g{index}", "point": [running[index], 0.0], "source": SRC},
                {"name": f"g{index}-e", "point": [running[index + 1], 0.0], "source": SRC},
            ]
        )
        dimensions.append(
            {"id": f"bay-{index}", "kind": "centres",
             "from": {"datum": f"g{index}"}, "to": {"datum": f"g{index}-e"}}
        )
    overall_datums, overall = _span("run-overall", RUN_M, -50.0)
    datums.extend(overall_datums)
    dimensions.append(overall)

    dset, layout = _build(
        tmp_path,
        dimensions=dimensions,
        datums=datums,
        chains=[
            {"id": "running", "overall": "run-overall",
             "components": ["bay-0", "bay-1", "bay-2"]}
        ],
        name="running",
    )

    assert [m.text for m in measure(dset, layout)] == [
        "2.499 m", "2.499 m", "2.500 m", "7.498 m",
    ]
    assert _findings(dset, layout) == []


def test_an_empty_chains_list_is_rejected_rather_than_ignored(tmp_path):
    datums, dimension = _span("only", 1.0, 0.0)

    with pytest.raises(DimensionError) as excinfo:
        _build(tmp_path, dimensions=[dimension], datums=datums, chains=[], name="empty")

    assert "chains must be a non-empty list" in str(excinfo.value)


def test_check_chains_fails_loudly_on_a_partial_value_map(tmp_path):
    """A missing value would otherwise report a chain clean without adding it up."""

    dset, layout = _run(tmp_path, name="partial")
    values = _values(dset, layout)
    del values["bay-1"]

    with pytest.raises(DimensionError) as excinfo:
        check_chains(dset, values)

    assert "no computed value for dimension 'bay-1'" in str(excinfo.value)


def test_the_chain_declaration_is_frozen_and_preserves_order(tmp_path):
    dset, _ = _run(tmp_path, name="frozen")
    chain = dset.chains[0]

    assert isinstance(chain, Chain)
    assert chain.components == ("bay-0", "bay-1", "bay-2")
    assert (chain.severity, chain.note) == ("error", "")
    with pytest.raises(FrozenInstanceError):
        chain.components = ()  # type: ignore[misc]


def test_a_chain_never_adjusts_a_component_to_make_the_sum_work(tmp_path):
    """§4.4, mechanically: the reported values are the measured ones, unchanged."""

    dset, layout = _run(tmp_path)
    before = _values(dset, layout)

    check_chains(dset, before)

    assert _values(dset, layout) == before
    assert [m.text for m in measure(dset, layout)] == [
        "2.499 m", "2.499 m", "2.499 m", "7.498 m",
    ]


def test_the_displayed_value_helper_reproduces_what_the_sheet_prints(tmp_path):
    """The check reads the printed number, including P6's compass convention."""

    dset, layout = _build(
        tmp_path,
        datums=[
            {"name": "west", "point": [10.0, 0.0], "source": SRC},
            {"name": "east", "point": [3.961, 0.0], "source": SRC},
        ],
        dimensions=[
            {"id": "back", "kind": "offset", "axis": "easting",
             "from": {"datum": "west"}, "to": {"datum": "east"}},
        ],
        name="signed",
    )
    measurement = measure(dset, layout)[0]
    text, shown = displayed_value(measurement.spec, measurement.value_m)

    assert measurement.value_m < 0
    assert measurement.text == "6.039 m W"
    assert (text, shown) == ("6.039", 6.039)
    assert math.isclose(shown, 6.039)
