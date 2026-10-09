"""Acceptance tests for P5 legibility checks."""

from __future__ import annotations

import hashlib
import inspect
import os
import random
import re
import runpy
import shutil
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from technical_drawings_for_agents import Drawing, DrawingMeta, ViewBox
from technical_drawings_for_agents.cli import main
from technical_drawings_for_agents.legibility import (
    CAD_PX_DEFAULTS,
    Declarations,
    Finding,
    LegibilityError,
    LegibilityReport,
    LegendDeclaration,
    LegendEntry,
    VerifyDeclaration,
    VerifyItem,
    check_drawing_dir,
    check_svg,
    gate,
    load_config,
    sheet_model,
    text_box,
)
from technical_drawings_for_agents.svg import (
    svg_border,
    svg_dimension_v,
    svg_rect,
    svg_scale_bar,
    svg_status_watermark,
    svg_text,
    svg_title_block,
    svg_wrap,
)

from .legibility_fixtures import (
    fixture_callout_region,
    fixture_fix_induced_collision,
    fixture_inset,
    fixture_legend_over_swatches,
    fixture_marker_occlusion,
    fixture_basin_furniture,
    meta,
    minimal_svg,
    no_watermark_svg,
    write_yaml,
)

REPO = Path(__file__).resolve().parents[1]
BASELINE = REPO / "tests" / "legibility_baseline.yaml"


def test_legend_labels_over_swatches_are_caught():
    sheet, m = fixture_legend_over_swatches(anchor=None)
    report = check_svg(sheet, meta=m)
    findings = [finding for finding in report.findings if finding.check == "overprint"]

    assert len(findings) == 7
    first = next(f for f in findings if "WTP treatment units" in f.message)
    assert first.severity == "error"
    assert "#d9463b" in first.message
    text = next(item for item in report.items if item.text == "WTP treatment units")
    swatch = next(item for item in report.items if item.fill == "#d9463b")
    dx, dy = text.box.overlap(swatch.box)
    assert dx == pytest.approx(14.0, abs=0.2)
    assert dy == pytest.approx(6.9, abs=0.2)

    fixed, fixed_meta = fixture_legend_over_swatches(anchor="start")
    assert check_svg(fixed, meta=fixed_meta).findings == ()


def test_scale_bar_intruding_on_detail_inset_is_caught():
    sheet, m = fixture_basin_furniture(fixed=False)
    report = check_svg(sheet, meta=m)
    findings = [finding for finding in report.findings if finding.check == "panel-clear"]

    assert len(findings) == 1
    assert "scale-bar" in findings[0].message
    assert "panel:auto" in findings[0].message
    clearance = float(re.search(r"clearance (-?\d+\.\d)", findings[0].message).group(1))
    assert -3.0 <= clearance <= -2.0
    panel = next(item for item in report.items if item.role == "panel")
    assert panel.box.area == pytest.approx(380 * 340)
    assert panel.box.area / (1552 * 982) == pytest.approx(0.085, abs=0.002)

    bars_only, bars_meta = fixture_basin_furniture(fixed=False, panel=False)
    assert not [
        f for f in check_svg(bars_only, meta=bars_meta).findings if f.check == "panel-clear"
    ]

    fixed, fixed_meta = fixture_basin_furniture(fixed=True)
    assert check_svg(fixed, meta=fixed_meta).findings == ()


def test_detail_inset_extent_four_times_content_is_flagged():
    sheet, m, declarations = fixture_inset(extent_m=(72, 52), content_m=(38, 25))
    report = check_svg(sheet, meta=m, declarations=declarations)
    finding = next(f for f in report.findings if f.check == "inset-fill")

    assert finding.severity == "warn"
    assert "0.254 < 0.50" in finding.message
    assert "38.0 x 25.0 m" in finding.message
    assert "72.0 x 52.0 m" in finding.message

    cfg = replace(CAD_PX_DEFAULTS, severities={"inset-fill": "error"})
    promoted = check_svg(sheet, meta=m, declarations=declarations, config=cfg)
    assert next(f for f in promoted.findings if f.check == "inset-fill").severity == "error"
    assert gate(promoted) == 1

    fixed, fixed_meta, fixed_declarations = fixture_inset(
        extent_m=(38, 25),
        content_m=(38, 25),
    )
    assert check_svg(fixed, meta=fixed_meta, declarations=fixed_declarations).findings == ()

    pre, pre_meta, _ = fixture_inset(declared=False, fallback_ratio=0.15)
    pre_report = check_svg(pre, meta=pre_meta)
    assert "0.150 < 0.45" in next(f.message for f in pre_report.findings if f.check == "inset-fill")
    post, post_meta, _ = fixture_inset(declared=False, fallback_ratio=0.58)
    assert check_svg(post, meta=post_meta).findings == ()


def test_oversized_callout_region_is_flagged():
    """P5-FINAL 4.1: the fill-ratio rule applies to any declared region."""
    sheet, m, declarations = fixture_callout_region(extent_m=(87, 48), content_m=(38, 25))
    report = check_svg(sheet, meta=m, declarations=declarations)

    finding = next(f for f in report.findings if f.check == "inset-fill")
    assert finding.severity == "warn"
    # 950 m2 of cluster inside 4176 m2 of claimed extent.
    assert "0.227 < 0.50" in finding.message
    assert "38.0 x 25.0 m" in finding.message
    assert "87.0 x 48.0 m" in finding.message
    assert "callout" in finding.message

    # A callout is drawn over the main view, so main-view geometry crossing its
    # boundary is normal. panel-clear must not fire on it.
    assert [f for f in report.findings if f.check == "panel-clear"] == []
    assert report.findings == (finding,)

    # The real fix: tighten the callout around its cluster, not move the cluster.
    tightened, tight_meta, tight_declarations = fixture_callout_region(
        extent_m=(42, 28),
        content_m=(38, 25),
    )
    assert check_svg(tightened, meta=tight_meta, declarations=tight_declarations).findings == ()


def test_co_located_markers_are_caught():
    """P5-FINAL 4.2: N numbered markers within R px is a finding."""
    sheet, m = fixture_marker_occlusion()
    report = check_svg(sheet, meta=m)

    occlusions = [f for f in report.findings if f.check == "marker-occlusion"]
    assert len(occlusions) == 2
    assert all(f.severity == "error" for f in occlusions)

    counts = sorted(int(f.message.split()[0]) for f in occlusions)
    assert counts == [2, 3]
    triple = next(f for f in occlusions if f.message.startswith("3 "))
    assert "'1', '2', '3'" in triple.message
    pair = next(f for f in occlusions if f.message.startswith("2 "))
    assert "'4', '5'" in pair.message

    # The lone control marker is never reported: one marker cannot occlude itself.
    assert "'6'" not in triple.message and "'6'" not in pair.message

    fixed, fixed_meta = fixture_marker_occlusion(fixed=True)
    assert check_svg(fixed, meta=fixed_meta).findings == ()


def test_an_ISA_instrument_bubble_is_not_a_numbered_marker():
    """The precision guard for marker-occlusion, measured on the shipped P&ID.

    isa.py draws a tag on two lines ("FIC" over "101") and a shared-display
    symbol as a circle inscribed in a square. Neither the two-line tag nor the
    concentric pair may be read as stacked schedule markers.
    """
    from technical_drawings_for_agents.legibility import ISO_PX_DEFAULTS, _marker_badges

    pid = REPO / "drawings" / "example" / "synthetic-pid" / "out" / "SYN-PSK-PID-001.svg"
    model = sheet_model(pid, config=ISO_PX_DEFAULTS)
    assert _marker_badges(model, ISO_PX_DEFAULTS) == []
    report = check_svg(pid, config=ISO_PX_DEFAULTS)
    assert [f for f in report.findings if f.check == "marker-occlusion"] == []


def test_fix_induced_collision_is_caught():
    """P5-FINAL 4.3: a by-eye fix that resolves one overlap and creates another.

    The finding count is 1 before and 1 after, so a human counting defects sees
    no change. Only the identity of the colliding pair reveals the regression.
    """
    before, before_meta = fixture_fix_induced_collision(0)
    first = check_svg(before, meta=before_meta).findings
    assert len(first) == 1
    assert first[0].check == "overprint"
    assert "'X pump house'" in first[0].message and "'A raw water main'" in first[0].message

    regressed, regressed_meta = fixture_fix_induced_collision(1)
    second = check_svg(regressed, meta=regressed_meta).findings
    assert len(second) == 1
    assert second[0].check == "overprint"
    # X is clear now, but the two labels that were moved landed on each other.
    assert "'X pump house'" not in second[0].message
    assert "'A raw water main'" in second[0].message and "'B dosing skid'" in second[0].message

    # The regression is a *new* pair, not the original one persisting.
    assert second[0].message != first[0].message
    assert {str(f) for f in second}.isdisjoint({str(f) for f in first})

    fixed, fixed_meta = fixture_fix_induced_collision(2)
    assert check_svg(fixed, meta=fixed_meta).findings == ()


def test_shipped_examples_produce_only_baselined_findings():
    baseline = load_config(BASELINE).baseline
    # One entry since the 2026-10-09 rename: the synthetic programme name shortened
    # and no longer overprints the CREATED cell.
    assert len(baseline) == 1
    cfg = replace(CAD_PX_DEFAULTS, baseline=baseline)

    simple = REPO / "drawings" / "example" / "simple-section"
    simple_report = check_drawing_dir(simple, config=cfg)
    assert simple_report.findings == ()
    assert simple_report.baselined == ()

    pid = REPO / "drawings" / "example" / "synthetic-pid"
    pid_report = check_drawing_dir(pid, config=cfg)
    assert pid_report.findings == ()
    assert len(pid_report.baselined) == 1
    assert all(finding.check == "overprint" for finding in pid_report.baselined)


def test_out_of_frame_text_is_caught():
    m = meta(number="EXA-CIV-SEC-001", title="Overflow")
    dwg = Drawing(920, 580, ViewBox(0, 100, 0, 100, 920, 580), m.title_block(), "CONCEPT")
    dwg.add(svg_text(910, 300, "overflow", font_size=10, anchor="start"))
    report = check_svg(dwg.render(), meta=m)
    finding = next(f for f in report.findings if f.check == "frame-containment")
    assert finding.severity == "error"
    assert "right 62.0 px" in finding.message

    clean = Drawing(920, 580, ViewBox(0, 100, 0, 100, 920, 580), m.title_block(), "CONCEPT")
    clean.add(svg_text(700, 300, "overflow", font_size=10, anchor="start"))
    assert check_svg(clean.render(), meta=m).findings == ()

    flush = Drawing(920, 580, ViewBox(0, 100, 0, 100, 920, 580), m.title_block(), "CONCEPT")
    assert not [
        finding for finding in check_svg(flush.render(), meta=m).findings
        if finding.check == "frame-containment"
    ]


def test_text_over_title_block_is_caught():
    m = meta()
    dwg = Drawing(920, 580, ViewBox(0, 100, 0, 100, 920, 580), m.title_block(), "CONCEPT")
    dwg.add(svg_text(620, 452, "late schedule row", font_size=10, anchor="start"))
    findings = check_svg(dwg.render(), meta=m).findings
    assert [f.check for f in findings] == ["title-block-clear"]
    assert "late schedule row" in findings[0].message

    moved = Drawing(920, 580, ViewBox(0, 100, 0, 100, 920, 580), m.title_block(), "CONCEPT")
    moved.add(svg_text(620, 390, "late schedule row", font_size=10, anchor="start"))
    assert check_svg(moved.render(), meta=m).findings == ()


def test_watermark_must_match_meta_status(tmp_path):
    m = meta(status="CONCEPT")
    assert check_svg(_drawing_with_status("CONCEPT", m).render(), meta=m).findings == ()

    issued = check_svg(_drawing_with_status("ISSUED", m).render(), meta=m)
    finding = next(f for f in issued.findings if f.check == "status-coherence")
    assert "CONCEPT" in finding.message and "ISSUED" in finding.message
    assert "issued-gate" in finding.message

    issued_meta = meta(status="ISSUED", for_construction=True)
    draft = check_svg(_drawing_with_status("DRAFT", issued_meta).render(), meta=issued_meta)
    assert "issued-gate" in next(f.message for f in draft.findings)

    bad = write_yaml(tmp_path / "bad.yaml", {"severities": {"issued-gate": "warn"}})
    with pytest.raises(LegibilityError, match="ISSUED gate"):
        load_config(bad)

    missing_svg, missing_meta = no_watermark_svg()
    assert next(f for f in check_svg(missing_svg, meta=missing_meta).findings).severity == "error"

    no_meta = check_svg(_drawing_with_status("CONCEPT", m).render())
    assert no_meta.findings == ()
    assert any(skip.check == "status-coherence" for skip in no_meta.skipped)


def test_revision_and_number_coherence():
    drawn = meta(number="STA-SITE-GA-001", revision="B")
    checked = meta(number="STA-SITE-GA-001", revision="C")
    report = check_svg(_drawing_with_status("CONCEPT", drawn).render(), meta=checked)
    finding = next(f for f in report.findings if f.check == "revision-coherence")
    assert "'B'" in finding.message and "'C'" in finding.message

    clean = check_svg(_drawing_with_status("CONCEPT", drawn).render(), meta=drawn)
    assert clean.findings == ()
    assert any(
        skip.check == "revision-coherence:table" and "#61" in skip.reason
        for skip in clean.skipped
    )


def test_legend_completeness_both_ways():
    sheet = _text_sheet("Alpha label", "Bravo label", "Charlie label")
    declarations = Declarations(
        legend=LegendDeclaration(
            entries=(
                LegendEntry("a", "Alpha label"),
                LegendEntry("b", "Bravo label"),
                LegendEntry("c", "Charlie label"),
            ),
            used_keys=("a", "b", "d"),
        )
    )
    report = check_svg(sheet, declarations=declarations)
    messages = [finding.message for finding in report.findings]
    assert sum("no legend entry" in message for message in messages) == 1
    assert sum("declared but never used" in message for message in messages) == 1

    missing_label = Declarations(
        legend=LegendDeclaration(
            entries=(LegendEntry("b", "Not on sheet"),),
            used_keys=("b",),
        )
    )
    assert "is not drawn" in check_svg(sheet, declarations=missing_label).findings[0].message

    required = replace(CAD_PX_DEFAULTS, require=("legend-complete",))
    no_decl = check_svg(sheet, config=required)
    assert any(f.check == "legend-complete" and f.severity == "error" for f in no_decl.findings)
    assert gate(no_decl) == 1


def test_verify_items_must_be_annotated():
    declarations = Declarations(
        verify=VerifyDeclaration(marker="(v)", items=(VerifyItem(id="STA-WTP-01"),))
    )
    report = check_svg(_text_sheet("STA-WTP-01 FA-130 raft"), declarations=declarations)
    assert "STA-WTP-01" in report.findings[0].message

    clean = check_svg(
        _text_sheet("1. STA-WTP-01 FA-130 raft (v)", "NOTE: (v) field verify before issue"),
        declarations=declarations,
    )
    assert clean.findings == ()

    untagged = Declarations(
        verify=VerifyDeclaration(marker="(v)", items=(VerifyItem(id=None, count=8),))
    )
    five = check_svg(_text_sheet(*[f"item {i} (v)" for i in range(5)]), declarations=untagged)
    assert "5 marker-bearing" in five.findings[0].message
    eight = check_svg(
        _text_sheet(*[f"item {i} (v)" for i in range(8)], "NOTE: (v) field verify before issue"),
        declarations=untagged,
    )
    assert any(
        "untagged" in finding.message and finding.severity == "warn"
        for finding in eight.findings
    )

    undefined = check_svg(_text_sheet("item (v)"), declarations=untagged)
    assert any("not defined" in finding.message for finding in undefined.findings)


def test_severity_gate_and_exit_codes(tmp_path, capsys):
    clean = LegibilityReport(None, None, (), (), ())
    warning = LegibilityReport(None, None, (Finding("warn", "inset-fill", "warn"),), (), ())
    error = LegibilityReport(None, None, (Finding("error", "overprint", "bad"),), (), ())
    assert gate(clean) == 0
    assert gate(error) == 1
    assert gate(warning) == 0
    assert gate(warning, strict=True) == 1

    sheet, _, _ = fixture_inset(declared=False, fallback_ratio=0.58)
    svg = tmp_path / "clean.svg"
    svg.write_text(sheet, encoding="utf-8")
    assert main(["check", str(svg)]) == 0

    warn_sheet, _warn_meta, declarations = fixture_inset()
    warn_svg = tmp_path / "warn.svg"
    warn_svg.write_text(warn_sheet, encoding="utf-8")
    config = write_yaml(
        tmp_path / "legibility.yaml",
        {
            "panels": [
                {
                    "name": declarations.panels[0].name,
                    "box_px": [96, 180, 456, 480],
                    "extent_m": [72, 52],
                }
            ]
        },
    )
    assert main(["check", str(warn_svg), "--config", str(config)]) == 0
    assert main(["check", str(warn_svg), "--config", str(config), "--strict"]) == 1

    bad = write_yaml(tmp_path / "bad.yaml", {"min_overlap_px": "wide"})
    assert main(["check", str(svg), "--config", str(bad)]) == 2
    assert "bad.yaml" in capsys.readouterr().err
    assert main(["check", str(tmp_path / "missing.svg")]) == 2

    collision, _ = fixture_legend_over_swatches(anchor=None)
    collision_svg = tmp_path / "collision.svg"
    collision_svg.write_text(collision, encoding="utf-8")
    assert main(["validate", str(collision_svg)]) == 0
    assert main(["validate", str(collision_svg), "--legibility"]) == 1


def test_skipped_checks_are_reported_never_silent(tmp_path, capsys):
    sheet, m = fixture_legend_over_swatches(anchor="start")
    report = check_svg(sheet, meta=m)
    skipped = {skip.check: skip.reason for skip in report.skipped}
    for name in ("inset-fill", "legend-complete", "verify-annotated"):
        assert name in skipped
        assert "legibility.yaml" in skipped[name] or "legibility.json" in skipped[name]
    assert "overprint" in report.checked

    target = tmp_path / "sheet.svg"
    target.write_text(sheet, encoding="utf-8")
    assert main(["check", str(target)]) == 0
    assert "Skipped:" in capsys.readouterr().out


def test_transforms_and_nested_svg_are_mapped():
    translated = minimal_svg('<g transform="translate(96,600)">'
                             '<text x="12" y="20" font-size="10">T</text></g>',
                             width=400, height=760)
    item = next(i for i in sheet_model(translated).items if i.text == "T")
    assert item.box.x0 == pytest.approx(108, abs=0.1)
    assert item.box.y0 == pytest.approx(612.5, abs=0.1)

    nested = minimal_svg(
        '<svg x="44" y="96" width="1412" height="554" viewBox="0 0 1000 400" '
        'preserveAspectRatio="xMidYMin meet"><text x="0" y="20" font-size="10">'
        "N</text></svg>",
        width=1600,
        height=1000,
    )
    n = next(i for i in sheet_model(nested).items if i.text == "N")
    assert n.box.x0 == pytest.approx(57.5, abs=0.1)
    assert n.box.y0 == pytest.approx(96 + (20 - 7.5) * 1.385, abs=0.2)

    vb = ViewBox(0, 10, 0, 10, 300, 300)
    rotated = minimal_svg(
        svg_dimension_v(vb, 5, 2, 8, "DEPTH", offset_px=20),
        width=300,
        height=300,
    )
    label = next(i for i in sheet_model(rotated).items if i.text == "DEPTH")
    assert label.box.height > label.box.width

    middle = minimal_svg(
        '<text x="100" y="100" font-size="10" dominant-baseline="middle">Apply</text>'
    )
    mid = next(i for i in sheet_model(middle).items if i.text == "Apply")
    assert (mid.box.y0 + mid.box.y1) / 2 == pytest.approx(100)


def test_unsupported_svg_constructs_raise():
    cases = [
        ('<g transform="skewX(10)"><text x="1" y="1">x</text></g>', "transform"),
        (
            '<svg x="10" y="10" width="100" height="100" viewBox="0 0 10 10" '
            'preserveAspectRatio="xMidYMid slice"><text>x</text></svg>',
            "preserveAspectRatio",
        ),
        ('<text x="10mm" y="10">x</text>', "x"),
        ("<use href=\"#x\"/>", "use"),
        ('<text x="1" y="1"><tspan x="2">x</tspan></text>', "tspan"),
    ]
    for body, message in cases:
        with pytest.raises(LegibilityError, match=message):
            sheet_model(minimal_svg(body))


def test_text_metrics():
    box, confidence = text_box("MMMMMMMMMM", 0, 0, 10, family="monospace")
    assert box.width == pytest.approx(60.0)
    assert confidence == "exact"

    helv, confidence = text_box("SYN-PSK-PID-001", 0, 0, 9, family="Helvetica")
    assert confidence == "table"
    assert helv.width == pytest.approx(74.25, rel=0.02)

    cjk, _ = text_box("泵站", 0, 0, 10, family="Helvetica")
    assert cjk.width == pytest.approx(20.0)

    assert text_box("acorn", 0, 0, 10)[0].height == pytest.approx(5.5)
    assert text_box("Apply", 0, 0, 10)[0].height == pytest.approx(9.6)

    start, _ = text_box("abc", 100, 0, 10, anchor="start")
    middle, _ = text_box("abc", 100, 0, 10, anchor="middle")
    end, _ = text_box("abc", 100, 0, 10, anchor="end")
    assert start.x0 == pytest.approx(100)
    assert middle.x0 == pytest.approx(91)
    assert end.x0 == pytest.approx(82)


def test_findings_are_deterministic(tmp_path):
    elements = [
        svg_rect(100, 100, 14, 11, fill="#d9463b"),
        svg_text(122, 109, "WTP treatment units", font_size=9),
        svg_rect(100, 130, 14, 11, fill="#f59e0b"),
        svg_text(122, 139, "Chemical dosing skids", font_size=9),
        svg_text(910, 260, "overflow", font_size=10, anchor="start"),
        svg_text(910, 280, "overflow", font_size=10, anchor="start"),
    ]
    first = _sheet_from_elements(elements)
    shuffled = list(elements)
    random.Random(42).shuffle(shuffled)
    second = _sheet_from_elements(shuffled)
    assert tuple(map(str, check_svg(first, meta=meta()).findings)) == tuple(
        map(str, check_svg(second, meta=meta()).findings)
    )

    target = tmp_path / "deterministic.svg"
    target.write_text(first, encoding="utf-8")
    outputs = []
    for seed in ("1", "2"):
        env = {**os.environ, "PYTHONHASHSEED": seed}
        proc = subprocess.run(
            [sys.executable, "-m", "technical_drawings_for_agents.cli", "check", str(target), "--format", "json"],
            cwd=REPO,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        assert proc.returncode == 1
        outputs.append(proc.stdout)
    assert outputs[0] == outputs[1]


def test_allowance_requires_reason_and_stale_allowance_warns(tmp_path):
    missing = write_yaml(tmp_path / "missing.yaml", {"allow": [{"a": "text", "b": "opaque-fill"}]})
    with pytest.raises(LegibilityError, match="reason"):
        load_config(missing)

    sheet, m = fixture_legend_over_swatches(anchor=None)
    config = replace(
        CAD_PX_DEFAULTS,
        allow=(load_config(write_yaml(
            tmp_path / "allow.yaml",
            {
                "allow": [
                    {
                        "a": "text:WTP treatment units",
                        "b": "opaque-fill:#d9463b",
                        "reason": "known overlap while testing",
                    }
                ]
            },
        )).allow),
    )
    remaining = [
        f for f in check_svg(sheet, meta=m, config=config).findings if f.check == "overprint"
    ]
    assert len(remaining) == 6
    stale = check_svg(fixture_legend_over_swatches(anchor="start")[0], meta=m, config=config)
    assert any("stale allowance" in finding.message for finding in stale.findings)

    bad_baseline = write_yaml(
        tmp_path / "bad-baseline.yaml",
        {"baseline": [{"check": "overprint", "message": "x", "reason": "r"}]},
    )
    with pytest.raises(LegibilityError, match="issue"):
        load_config(bad_baseline)

    first = next(f for f in check_svg(sheet, meta=m).findings if "WTP treatment units" in f.message)
    baseline_cfg = replace(
        CAD_PX_DEFAULTS,
        baseline=(load_config(write_yaml(
            tmp_path / "baseline.yaml",
            {
                "baseline": [
                    {
                        "check": first.check,
                        "message": first.message,
                        "reason": "accepted fixture overlap",
                        "issue": "#57",
                    }
                ]
            },
        )).baseline),
    )
    assert len(check_svg(sheet, meta=m, config=baseline_cfg).baselined) == 1
    stale_baseline = check_svg(
        fixture_legend_over_swatches(anchor="start")[0],
        meta=m,
        config=baseline_cfg,
    )
    assert any("stale baseline" in finding.message for finding in stale_baseline.findings)


def test_no_emitter_output_changed(tmp_path):
    simple = tmp_path / "simple-section"
    shutil.copytree(REPO / "drawings" / "example" / "simple-section", simple)
    runpy.run_path(str(simple / "source.py"), run_name="__main__")
    assert (simple / "out" / "EXA-CIV-SEC-001.svg").read_bytes() == (
        REPO / "drawings" / "example" / "simple-section" / "out" / "EXA-CIV-SEC-001.svg"
    ).read_bytes()

    from technical_drawings_for_agents import pid

    pid_dir = tmp_path / "synthetic-pid"
    shutil.copytree(REPO / "drawings" / "example" / "synthetic-pid", pid_dir)
    pid.build(pid_dir / "SYN-PSK-PID-001.pid.yaml", pid_dir / "out")
    assert (pid_dir / "out" / "SYN-PSK-PID-001.svg").read_bytes() == (
        REPO / "drawings" / "example" / "synthetic-pid" / "out" / "SYN-PSK-PID-001.svg"
    ).read_bytes()

    before = _snapshot(pid_dir)
    check_drawing_dir(pid_dir)
    assert _snapshot(pid_dir) == before


def test_module_cannot_touch_the_issued_gate():
    import technical_drawings_for_agents.legibility as legibility

    source = inspect.getsource(legibility)
    source_without_writer = re.sub(
        r"def write_json\(.*?^\s*return out",
        "",
        source,
        flags=re.S | re.M,
    )
    assert "write_text" not in source_without_writer
    assert "safe_dump" not in source
    assert not re.search(r"for_construction\s*=", source)
    assert "not approval to issue" in inspect.getdoc(LegibilityReport.ok)


def test_max_items_guard_raises():
    circles = "\n".join(
        f'<circle cx="{40 + i}" cy="40" r="1" fill="#000"/>' for i in range(12)
    )
    cfg = replace(CAD_PX_DEFAULTS, max_items=5)
    with pytest.raises(LegibilityError, match="max_items=5"):
        sheet_model(minimal_svg(circles), config=cfg)


def _drawing_with_status(status: str, m: DrawingMeta) -> Drawing:
    return Drawing(920, 580, ViewBox(0, 100, 0, 100, 920, 580), m.title_block(), status)


def _text_sheet(*texts: str) -> str:
    parts = [svg_text(80, 80 + index * 18, text, font_size=10, anchor="start")
             for index, text in enumerate(texts)]
    return _sheet_from_elements(parts)


def _sheet_from_elements(elements: list[str]) -> str:
    m = meta()
    body = "\n".join(
        [
            svg_border(920, 580),
            *elements,
            svg_status_watermark(920, 580, "CONCEPT"),
            svg_title_block(920, 580, **m.title_block()),
        ]
    )
    return svg_wrap(body, 920, 580)


def _snapshot(root: Path) -> dict[str, tuple[int, str]]:
    out: dict[str, tuple[int, str]] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            out[str(path.relative_to(root))] = (path.stat().st_mtime_ns, digest)
    return out


def test_a_declared_panel_supersedes_the_auto_detected_one_for_the_same_rect(tmp_path):
    """Declaring a callout must actually suppress panel-clear.

    Regression for the first real project sheet (Basin site GA): auto-detection ran
    unconditionally and declared panels were *appended*, so one physical rectangle
    produced two panel items — a `declared-callout` (exempt) plus an `auto#N`
    duplicate that still fired. Declaring a callout could therefore never suppress
    the finding it exists to declare.
    """
    from technical_drawings_for_agents.legibility import _boxes_are_same_region, Box

    # the predicate itself: same rect within tolerance, different rects not
    a = Box(96.0, 600.0, 456.0, 900.0)
    assert _boxes_are_same_region(a, Box(96.0, 600.0, 456.0, 900.0))
    assert _boxes_are_same_region(a, Box(97.0, 601.0, 455.0, 899.0))  # within 2 px
    assert not _boxes_are_same_region(a, Box(96.0, 600.0, 456.0, 880.0))  # 20 px short
    assert not _boxes_are_same_region(a, Box(500.0, 600.0, 860.0, 900.0))  # elsewhere
