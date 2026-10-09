"""Acceptance tests for the paper-space sheet model (P1 / issue #53).

Numbered against the specification's acceptance list (spec §6). Test 1 (byte-identical
legacy output) lives in ``test_example_smoke.py`` next to the other example-build
assertions; tests 20 and 21 (CLI) live in ``test_sheet_cli.py``.

No test in this file fabricates sheet structure. Where a sheet needs a title block for
validation to pass, the fixture *reserves* one in its YAML and ``SheetDrawing`` emits the
real reservation placeholder — a hand-spliced ``class="title-block"`` marker would mean
asserting against a doctored artifact.
"""

from __future__ import annotations

import copy
import inspect
import json
import math
import random
import re
from pathlib import Path

import pytest
import yaml

from technical_drawings_for_agents.cli import main
from technical_drawings_for_agents.meta import DrawingMeta
from technical_drawings_for_agents.sheet import (
    SCALE_BAR_INSET_MM,
    SHEET_METADATA_ID,
    SHEET_METADATA_SCHEMA,
    PlotScale,
    Sheet,
    SheetDrawing,
    SheetError,
    SheetFrame,
    Viewport,
    load_sheet_config,
    read_sheet_metadata,
    resolve_sheet,
    sheet_frame,
    sheet_scale_bar,
)
from technical_drawings_for_agents.svg import svg_text
from technical_drawings_for_agents.validate import check_sheet_svg, validate_svg, validate_target

EXAMPLE = Path(__file__).resolve().parents[1] / "drawings" / "example" / "simple-section"
SHEET_SOURCE = Path(__file__).resolve().parents[1] / "src" / "technical_drawings_for_agents" / "sheet.py"

BASIN_EXTENT_M = [788392, 322125, 788702, 322395]  # 310 x 270 m, UTM30N
#: Reserving the title block is real config, not a test fixture: it is what makes
#: `SheetDrawing` emit the reservation placeholder the marker check looks for.
TITLE_BLOCK_RESERVE = [{"name": "title-block", "edge": "bottom", "size_mm": 40}]


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #


def _config(
    scale: int | str = 1000,
    extent: list[float] | None = None,
    *,
    size: str = "A3",
    orientation: str = "landscape",
    reserve: list[dict] | None = None,
    **viewport: object,
) -> dict:
    config: dict = {
        "size": size,
        "orientation": orientation,
        "margins_mm": 10,
        "viewport": {
            "extent_m": list(extent if extent is not None else [0, 0, 100, 100]),
            "scale": scale,
            **viewport,
        },
    }
    if reserve is not None:
        config["reserve"] = copy.deepcopy(reserve)
    return config


def _basin(scale: int | str = 1250, **kwargs: object) -> dict:
    return _config(scale, list(BASIN_EXTENT_M), **kwargs)


def _render(sheet: Sheet, *, status: str | None = "CONCEPT") -> str:
    return SheetDrawing(sheet=sheet, status=status).render()


def _write_svg(tmp_path: Path, svg: str, name: str = "sheet.svg") -> Path:
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(svg, encoding="utf-8")
    return path


def _write_drawing_dir(
    root: Path, config: dict, *, meta_scale: str | None = None, **meta_overrides: object
) -> Path:
    out_dir = root / "out"
    out_dir.mkdir(parents=True)
    meta: dict = {
        "number": "X",
        "title": "Test sheet",
        "revision": "A",
        "status": "CONCEPT",
        "for_construction": False,
        "sheet": config,
    }
    if meta_scale is not None:
        meta["scale"] = meta_scale
    meta.update(meta_overrides)
    (root / "meta.yaml").write_text(yaml.safe_dump(meta, sort_keys=False), encoding="utf-8")
    (out_dir / "X.svg").write_text(_render(resolve_sheet(config)), encoding="utf-8")
    return root


def _scale_bar_group(svg: str) -> str:
    match = re.search(r'<g class="scale-bar"[^>]*>.*?</g>', svg, flags=re.DOTALL)
    assert match is not None
    return match.group(0)


def _rewrite_metadata(svg: str, mutate) -> str:
    payload = read_sheet_metadata(svg)
    assert payload is not None
    mutate(payload)
    replacement = (
        f'<metadata id="{SHEET_METADATA_ID}">'
        f'{json.dumps(payload, sort_keys=True, separators=(",", ":"))}</metadata>'
    )
    return re.sub(
        rf'<metadata\b[^>]*\bid="{SHEET_METADATA_ID}"[^>]*>.*?</metadata>',
        replacement,
        svg,
        count=1,
        flags=re.DOTALL,
    )


def _frame_overflow_mm(drawn: list[float], frame: list[float]) -> tuple[float, float]:
    """Per-axis overflow of a drawn bbox beyond the viewport frame, in paper mm."""
    fx, fy, fw, fh = frame
    over_x = max(fx - drawn[0], drawn[2] - (fx + fw), 0.0)
    over_y = max(fy - drawn[1], drawn[3] - (fy + fh), 0.0)
    return over_x, over_y


# --------------------------------------------------------------------------- #
# 2, 3 — the headline round trips
# --------------------------------------------------------------------------- #


def test_a1_landscape_1_to_200_measures_a_known_model_distance():
    sheet = resolve_sheet(_config(200, [0, 0, 160, 100], size="A1"))

    assert sheet.paper.width_mm == 841.0
    assert sheet.paper.height_mm == 594.0
    assert sheet.frame == SheetFrame(10.0, 10.0, 821.0, 574.0)
    assert sheet.viewport.scale.mm_per_m() == 5.0
    assert sheet.viewport.length_mm(40.0) == 200.0
    assert sheet.viewport.required_mm == (800.0, 500.0)
    assert sheet.viewport.fits is True
    assert sheet.scale_text == "1:200"

    svg = _render(sheet, status=None)
    assert svg.startswith(
        '<svg xmlns="http://www.w3.org/2000/svg" width="841mm" height="594mm" '
        'viewBox="0 0 841 594"'
    )
    x0, _ = sheet.viewport.point(0, 0)
    x1, _ = sheet.viewport.point(40, 0)
    assert x1 - x0 == pytest.approx(200.0, abs=1e-9)


def test_a3_landscape_1_to_1250_measures_a_known_model_distance():
    """The live Basin case, done right: 50 m of world is 40.000 mm of paper."""
    sheet = resolve_sheet(_basin())

    assert (sheet.paper.width_mm, sheet.paper.height_mm) == (420.0, 297.0)
    assert sheet.frame == SheetFrame(10.0, 10.0, 400.0, 277.0)
    assert sheet.viewport.scale.mm_per_m() == 0.8
    assert sheet.viewport.required_mm == (248.0, 216.0)
    assert sheet.viewport.fits is True
    assert sheet.viewport.length_mm(50.0) == 40.0

    bar = sheet_scale_bar(sheet.viewport, sheet.viewport.frame, length_m=50, divisions=5)
    assert "total_mm=40.000" in bar
    assert ">1:1250<" in _render(sheet)

    # Derived length: 0.25 x 400 mm = 100 mm -> 125 m target -> largest {1,2,5}x10^n
    # at or below 125 is 100 m, which plots 80.000 mm.
    derived = sheet_scale_bar(sheet.viewport, sheet.viewport.frame, divisions=5)
    assert "length_m=100;" in derived
    assert "total_mm=80.000" in derived


# --------------------------------------------------------------------------- #
# 4 — the map itself
# --------------------------------------------------------------------------- #


def test_viewport_point_round_trips_model_to_paper_and_back():
    rng = random.Random(20260724)
    frame = SheetFrame(10.0, 10.0, 800.0, 500.0)
    extent = (10.0, 20.0, 160.0, 120.0)
    for denominator in (1, 200, 1250):
        for rotation in (0.0, 40.25, 90.0, 217.5):
            viewport = Viewport(extent, PlotScale(denominator), frame, rotation_deg=rotation)
            assert viewport.point(*viewport.centre_m) == pytest.approx((410.0, 260.0), abs=1e-9)
            for _ in range(20):
                a = (rng.uniform(extent[0], extent[2]), rng.uniform(extent[1], extent[3]))
                b = (rng.uniform(extent[0], extent[2]), rng.uniform(extent[1], extent[3]))
                model_distance = math.dist(a, b)
                paper_distance = math.dist(viewport.point(*a), viewport.point(*b))
                assert viewport.model_m(viewport.length_mm(model_distance)) == pytest.approx(
                    model_distance, abs=1e-9
                )
                assert paper_distance == pytest.approx(
                    model_distance * 1000.0 / denominator, abs=1e-9
                )


# --------------------------------------------------------------------------- #
# 5, 6 — the record is checkable, and tampering with it fails
# --------------------------------------------------------------------------- #


def test_stated_scale_contradicting_the_viewport_fails_validation(tmp_path):
    directory = _write_drawing_dir(
        tmp_path / "decorated", _basin(reserve=TITLE_BLOCK_RESERVE), meta_scale="1:1250 (A3)"
    )
    result = validate_target(directory)
    assert result.ok is False
    assert len(result.problems) == 1
    assert "meta: scale '1:1250 (A3)'" in result.problems[0]
    assert "derived scale '1:1250'" in result.problems[0]

    directory = _write_drawing_dir(
        tmp_path / "wrong", _basin(reserve=TITLE_BLOCK_RESERVE), meta_scale="1:500"
    )
    result = validate_target(directory)
    assert result.ok is False
    assert any("contradicts" in problem for problem in result.problems)

    # The recommended migration: delete meta.scale, because it is derived.
    directory = _write_drawing_dir(tmp_path / "absent", _basin(reserve=TITLE_BLOCK_RESERVE))
    assert validate_target(directory).ok is True


@pytest.mark.parametrize(
    ("mutate", "fragment"),
    [
        (lambda payload: payload["scale"].update({"denominator": 1157}), "denominator 1157"),
        (lambda payload: payload["scale_bar"].update({"total_mm": 43.216}), "43.216 mm wide"),
        (
            lambda payload: payload["viewport"].update({"required_mm": [200, 180]}),
            "200.000×180.000",
        ),
    ],
)
def test_tampered_sheet_metadata_fails_every_derivable_check(tmp_path, mutate, fragment):
    svg = _render(resolve_sheet(_basin(reserve=TITLE_BLOCK_RESERVE)))
    path = _write_svg(tmp_path, _rewrite_metadata(svg, mutate))

    result = validate_svg(path)

    assert len(result.problems) == 1, result.problems
    assert fragment in result.problems[0]


def test_tampered_root_width_fails_the_declared_paper_check(tmp_path):
    """Split out from the metadata mutations so a failure isolates to the root element."""
    svg = _render(resolve_sheet(_basin(reserve=TITLE_BLOCK_RESERVE)))
    path = _write_svg(tmp_path, svg.replace('width="420mm"', 'width="1600"', 1))

    result = validate_svg(path)

    assert len(result.problems) == 1, result.problems
    assert "root width='1600'" in result.problems[0]


# --------------------------------------------------------------------------- #
# 7, 8, 9 — fit, overflow and the round-scale search
# --------------------------------------------------------------------------- #


def test_extent_that_does_not_fit_fails_with_the_smallest_scale_that_would():
    with pytest.raises(SheetError) as excinfo:
        resolve_sheet(_basin(scale=500))

    message = str(excinfo.value)
    assert "620.000 × 540.000 mm" in message
    assert "400.000 × 277.000 mm" in message
    assert "1:1000" in message


def test_on_overflow_fit_resolves_to_a_round_scale_and_says_so():
    sheet = resolve_sheet(_basin(scale=500, on_overflow="fit"))

    assert sheet.viewport.scale.denominator == 1000
    assert sheet.scale_text == "1:1000"
    assert sheet.warnings == (
        "sheet: 1:500 does not fit; on_overflow: fit resolved to 1:1000",
    )
    # The word "fit" never reaches the sheet, the record or the title block.
    assert json.loads(sheet.metadata_json())["scale"]["denominator"] == 1000
    assert "fit" not in sheet.metadata_json()


def test_fit_scale_picks_the_smallest_preferred_denominator_that_fits():
    # A1 landscape frame is 821 x 574 mm. 1:100 needs 1600 x 1000 mm; 1:200 needs 800 x 500.
    sheet = resolve_sheet(_config("fit", [0, 0, 160, 100], size="A1"))
    assert sheet.viewport.scale.denominator == 200
    assert json.loads(sheet.metadata_json())["scale"]["text"] == "1:200"

    # 1:2000 would need 1000 x 500 mm — wider than the 821 mm frame — so the search
    # lands on the round 1:2500, never on an unrounded value.
    sheet = resolve_sheet(_config("fit", [0, 0, 2000, 1000], size="A1"))
    assert sheet.viewport.scale.denominator == 2500


# --------------------------------------------------------------------------- #
# 10 — the real defect: a hand-spaced literal in the bar group
# --------------------------------------------------------------------------- #


def test_a_hand_spaced_literal_scale_bar_label_is_rejected(tmp_path):
    sheet = resolve_sheet(_basin(reserve=TITLE_BLOCK_RESERVE))
    svg = _render(sheet)
    assert validate_svg(_write_svg(tmp_path, svg, "clean.svg")).ok is True

    # Exactly what the live Basin generator did: a second, hand-spaced tick row
    # painted through the legacy bar's free `label=` caption slot.
    group = _scale_bar_group(svg)
    spliced = svg.replace(
        group, group.replace("</g>", f'{svg_text(120, 250, "0                50 m")}\n</g>', 1), 1
    )
    result = validate_svg(_write_svg(tmp_path, spliced, "literal.svg"))

    assert result.ok is False
    assert "not a derived tick label" in result.problems[0]
    assert "0                50 m" in result.problems[0]
    # And the slot that allowed it does not exist on the new bar.
    assert "label" not in inspect.signature(sheet_scale_bar).parameters


# --------------------------------------------------------------------------- #
# 11, 12 — rotation: what turns and what does not
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("rotation_deg", "model_bearing_deg", "paper_deg"),
    [(0, 0, 0.0), (40.25, 0, 319.75), (0, 12.5, 347.5), (90, 90, 180.0), (-10, 0, 10.0)],
)
def test_north_arrow_bearing_is_derived_from_rotation_and_declared_north(
    rotation_deg, model_bearing_deg, paper_deg
):
    config = _config(1000, rotation_deg=rotation_deg)
    config["north"] = {"model_bearing_deg": model_bearing_deg, "label": "N"}
    svg = _render(resolve_sheet(config), status=None)

    record = read_sheet_metadata(svg)
    assert record is not None
    assert record["north"]["paper_deg"] == pytest.approx(paper_deg, abs=1e-9)
    assert f'<g class="north-arrow" transform="rotate({paper_deg:.3f}' in svg
    # The arrow must agree with the VIEWPORT, not with the expression that produced it.
    # Rotate "up" by the claimed paper bearing and check it lands where the viewport
    # actually puts model north. The old test restated the formula and so could never
    # have caught its sign being inverted.
    if model_bearing_deg == 0:
        vp = resolve_sheet(_config(1000, rotation_deg=rotation_deg)).viewport
        x0, y0 = vp.point(500.0, 500.0)
        xn, yn = vp.point(500.0, 600.0)
        actual = math.degrees(math.atan2(xn - x0, -(yn - y0))) % 360.0
        assert actual == pytest.approx(paper_deg, abs=1e-6), (
            f"arrow claims {paper_deg} deg but the viewport puts model north at {actual}"
        )
    # The glyph turns; the letter counter-rotates about its own centre and stays upright.
    counter = 0.0 if paper_deg == 0.0 else -paper_deg
    assert f'transform="rotate({counter:.3f}' in svg
    assert check_sheet_svg(svg) == ([], [])

    # With no `north:` block nothing is derived, nothing is drawn, and V11 does not run.
    plain = _render(resolve_sheet(_config(1000)), status=None)
    assert 'class="north-arrow"' not in plain
    assert check_sheet_svg(plain) == ([], [])


def test_scale_bar_does_not_rotate_with_the_viewport():
    """The map is isotropic, so rotation cannot change how many mm a metre is."""
    upright = _scale_bar_group(_render(resolve_sheet(_config(1000)), status=None))
    rotated = _scale_bar_group(
        _render(resolve_sheet(_config(1000, rotation_deg=40.25)), status=None)
    )

    assert upright == rotated
    assert "total_mm=100.000" in upright


# --------------------------------------------------------------------------- #
# 13 — reservations
# --------------------------------------------------------------------------- #


def test_reservations_cut_the_frame_in_declared_order():
    config = _config(
        200,
        [0, 0, 100, 50],
        size="A1",
        reserve=[
            {"name": "title-block", "edge": "bottom", "size_mm": 60},
            {"name": "legend", "edge": "right", "size_mm": 80},
        ],
    )

    sheet = resolve_sheet(config)
    assert sheet.regions["title-block"] == SheetFrame(10.0, 524.0, 821.0, 60.0)
    assert sheet.regions["legend"] == SheetFrame(751.0, 10.0, 80.0, 514.0)
    assert sheet.viewport.frame == SheetFrame(10.0, 10.0, 741.0, 514.0)

    # Order is honoured, not normalised: right-then-bottom gives different strips
    # (and the same final viewport frame).
    reversed_config = copy.deepcopy(config)
    reversed_config["reserve"].reverse()
    reversed_sheet = resolve_sheet(reversed_config)
    assert reversed_sheet.regions["legend"] == SheetFrame(751.0, 10.0, 80.0, 574.0)
    assert reversed_sheet.regions["title-block"] == SheetFrame(10.0, 524.0, 741.0, 60.0)
    assert reversed_sheet.viewport.frame == SheetFrame(10.0, 10.0, 741.0, 514.0)


# --------------------------------------------------------------------------- #
# 14, 15 — parity and record stability
# --------------------------------------------------------------------------- #


def test_a_drawing_without_a_sheet_block_gains_no_new_checks():
    result = validate_target(EXAMPLE)

    assert result.problems == []
    assert result.checked == [
        "meta.yaml",
        "EXA-CIV-SEC-001.svg: title block",
        "EXA-CIV-SEC-001.svg: scale bar",
        "EXA-CIV-SEC-001.svg: status watermark",
    ]
    assert result.warnings == []
    assert load_sheet_config(EXAMPLE / "meta.yaml") is None
    committed = (EXAMPLE / "out" / "EXA-CIV-SEC-001.svg").read_text(encoding="utf-8")
    assert read_sheet_metadata(committed) is None


def test_sheet_metadata_json_is_byte_stable_and_carries_no_provenance():
    first = resolve_sheet(_basin()).metadata_json()
    second = resolve_sheet(_basin()).metadata_json()

    assert first == second
    assert " " not in first
    payload = json.loads(first)
    assert first == json.dumps(payload, sort_keys=True, separators=(",", ":"))
    assert payload["schema"] == SHEET_METADATA_SCHEMA
    for forbidden in ("date", "time", "Z", "commit", "/Users", "version"):
        assert forbidden not in first


# --------------------------------------------------------------------------- #
# 16, 17 — hostile input fails loudly
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        (lambda cfg: cfg.pop("size"), r"sheet\.size"),
        (lambda cfg: cfg.update({"size": "A6"}), r"sheet\.size"),
        (lambda cfg: cfg.update({"size": 42}), r"sheet\.size"),
        (lambda cfg: cfg.update({"orientation": "diagonal"}), r"sheet\.orientation"),
        (lambda cfg: cfg.update({"margins_mm": -1}), r"sheet\.margins_mm"),
        (lambda cfg: cfg.update({"margins_mm": 500}), r"left\+right"),
        (lambda cfg: cfg.update({"margins_mm": {"bogus": 3}}), r"sheet\.margins_mm.*bogus"),
        (lambda cfg: cfg.pop("viewport"), r"sheet needs a viewport"),
        (
            lambda cfg: cfg["viewport"].update({"extent_m": [0, 0, 1]}),
            r"sheet\.viewport\.extent_m",
        ),
        (lambda cfg: cfg["viewport"].update({"extent_m": [1, 1, 1, 2]}), r"xmax"),
        (lambda cfg: cfg["viewport"].update({"extent_m": [2, 0, 1, 1]}), r"xmax"),
        (lambda cfg: cfg["viewport"].update({"extent_m": [0, 0, math.inf, 1]}), r"extent_m"),
        (lambda cfg: cfg["viewport"].update({"extent_m": [0, 0, True, 1]}), r"extent_m"),
        (lambda cfg: cfg["viewport"].update({"scale": 0}), r"sheet\.viewport\.scale"),
        (lambda cfg: cfg["viewport"].update({"scale": -200}), r"sheet\.viewport\.scale"),
        (lambda cfg: cfg["viewport"].update({"scale": 1250.0}), r"sheet\.viewport\.scale"),
        (lambda cfg: cfg["viewport"].update({"scale": "1250"}), r"sheet\.viewport\.scale"),
        (lambda cfg: cfg["viewport"].update({"scale": "1:1250"}), r"sheet\.viewport\.scale"),
        (lambda cfg: cfg["viewport"].update({"scale": "auto"}), r"sheet\.viewport\.scale"),
        (lambda cfg: cfg["viewport"].update({"rotation_deg": "40"}), r"rotation_deg"),
        (lambda cfg: cfg["viewport"].update({"on_overflow": "clip"}), r"on_overflow"),
        (lambda cfg: cfg["viewport"].update({"align": "top-left"}), r"align"),
        (lambda cfg: cfg.update({"scale_bar": {"divisions": 0}}), r"scale_bar\.divisions"),
        (lambda cfg: cfg.update({"scale_bar": {"length_m": 0}}), r"scale_bar\.length_m"),
        (lambda cfg: cfg.update({"scale_bar": {"length_m": 500}}), r"scale_bar\.length_m"),
        (lambda cfg: cfg.update({"scale_bar": {"unit": "ft"}}), r"scale_bar\.unit"),
        (
            lambda cfg: cfg.update(
                {
                    "reserve": [
                        {"name": "legend", "edge": "right", "size_mm": 10},
                        {"name": "legend", "edge": "bottom", "size_mm": 10},
                    ]
                }
            ),
            r"reserve\[1\]\.name",
        ),
        (
            lambda cfg: cfg.update(
                {"reserve": [{"name": "legend", "edge": "right", "size_mm": 400}]}
            ),
            r"leaves no room",
        ),
        (lambda cfg: cfg["viewport"].update({"plot_scale": 1000}), r"plot_scale"),
        (lambda cfg: cfg.clear() or cfg.update({"sheet": []}), r"sheet must be a mapping"),
    ],
)
def test_degenerate_and_hostile_sheet_configs_all_raise_sheeterror(mutate, match):
    config = _config(1000)
    mutate(config)

    with pytest.raises(SheetError, match=match):
        resolve_sheet(config)


def test_a_scale_bar_must_fit_inside_its_own_inset_not_flush_to_the_frame():
    """The corrected fit rule (P1-FINAL §2), with the arithmetic that forced it.

    A3 landscape less two 10 mm margins is a 400.000 mm viewport frame, and 500 m at
    1:1250 is exactly 400.000 mm. The base spec's ``<= frame width`` admitted that bar;
    acceptance test 16 requires it to raise. A bar flush to the frame edge has no
    clearance from the border and is not printable as drawn, so the usable width is the
    frame less the 6 mm inset on both sides: 388.000 mm.
    """
    sheet = resolve_sheet(_basin())
    assert sheet.viewport.frame.width_mm == 400.0
    assert sheet.viewport.length_mm(500) == 400.0

    too_wide = _basin()
    too_wide["scale_bar"] = {"length_m": 500}
    with pytest.raises(SheetError) as excinfo:
        resolve_sheet(too_wide)
    message = str(excinfo.value)
    assert "sheet.scale_bar.length_m" in message
    assert "400.000 mm" in message  # required
    assert "388.000 mm" in message  # available
    assert SCALE_BAR_INSET_MM == 6.0

    # 485 m plots 388.000 mm — exactly the available width — and is accepted.
    at_the_limit = _basin()
    at_the_limit["scale_bar"] = {"length_m": 485}
    resolved = resolve_sheet(at_the_limit)
    assert resolved.viewport.length_mm(485) == pytest.approx(388.0, abs=1e-9)


def test_plotscale_parse_accepts_only_canonical_ratios():
    assert PlotScale.parse("1:200").denominator == 200
    assert PlotScale.parse("  1:1250  ").denominator == 1250
    for text in ("1:1250 (A3)", "1/200", "200", "1:200.5", "1:0", "1:-5", "NTS", ""):
        with pytest.raises(SheetError, match="cannot parse plot scale"):
            PlotScale.parse(text)
    # A non-string YAML value raises SheetError, not TypeError.
    with pytest.raises(SheetError, match="cannot parse plot scale"):
        PlotScale.parse(1250.0)  # type: ignore[arg-type]

    assert PlotScale(200).text == "1:200"
    assert PlotScale(200).is_preferred is True
    assert PlotScale(1250).is_preferred is False
    # `isinstance(True, int)` is True in Python, so `scale: true` must not mean 1:1.
    with pytest.raises(SheetError, match="positive integer"):
        PlotScale(True)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# 18, 19 — the warning channel
# --------------------------------------------------------------------------- #


def test_drawn_extent_beyond_the_viewport_warns_but_does_not_fail(tmp_path):
    """Direction-independent by construction (P1-FINAL §4).

    The base spec said "a point 40 m outside the extent", which overflows in one axis
    only and therefore passes or fails on which axis the implementer picks. The probe
    here is outside in **both** axes, at an offset computed from the sheet under test
    (spare paper + 10 m), and both axes of ``drawn_extent_mm`` are asserted — so the
    test cannot pass on a single-axis accident, and cannot silently stop testing
    anything if the frame or margins change.

    A 1:1000 sheet is used because 1:1250 always carries the non-preferred-ratio
    warning, which would make the "exactly one warning" assertion unreachable.
    """
    sheet = resolve_sheet(_config(1000, reserve=TITLE_BLOCK_RESERVE))
    viewport = sheet.viewport
    required_w, required_h = viewport.required_mm
    spare_x_m = viewport.model_m((viewport.frame.width_mm - required_w) / 2.0)
    spare_y_m = viewport.model_m((viewport.frame.height_mm - required_h) / 2.0)
    offset_m = max(spare_x_m, spare_y_m) + 10.0
    xmax, ymax = viewport.extent_m[2], viewport.extent_m[3]

    drawing = SheetDrawing(sheet=sheet, status="CONCEPT")
    drawing.add_model((xmax + offset_m, ymax + offset_m))
    svg = drawing.render()
    path = _write_svg(tmp_path, svg)

    record = read_sheet_metadata(svg)
    assert record is not None
    over_x, over_y = _frame_overflow_mm(
        record["viewport"]["drawn_extent_mm"], record["viewport"]["frame_mm"]
    )
    assert over_x > 0.0, "probe must leave the frame horizontally"
    assert over_y > 0.0, "probe must leave the frame vertically"

    result = validate_svg(path)
    assert result.ok is True
    assert len(result.warnings) == 1, result.warnings
    assert "beyond the viewport frame" in result.warnings[0]
    assert main(["validate", str(path), "--strict"]) == 1


def test_a_single_axis_probe_only_warns_in_the_axis_that_runs_out_of_paper(tmp_path):
    """The negative companion that pins the geometry (P1-FINAL §4).

    On the real Basin frame — A3 landscape, 400.0 x 277.0 mm, a 310 x 270 m extent
    plotting 248.0 x 216.0 mm at 1:1250 — the spare paper is 76.0 mm each side
    horizontally and 30.5 mm vertically, i.e. **95.0 m** and **38.125 m** of ground.
    So a point 40 m outside the extent is still comfortably on the paper horizontally
    and off it vertically. That asymmetry is what made the original one-axis fixture
    ambiguous; asserting both directions here means the ambiguity cannot return
    silently.
    """
    sheet = resolve_sheet(_basin(reserve=None))
    viewport = sheet.viewport
    xmin, ymin, xmax, ymax = viewport.extent_m
    required_w, required_h = viewport.required_mm

    spare_x_m = viewport.model_m((viewport.frame.width_mm - required_w) / 2.0)
    spare_y_m = viewport.model_m((viewport.frame.height_mm - required_h) / 2.0)
    assert spare_x_m == pytest.approx(95.0, abs=1e-9)
    assert spare_y_m == pytest.approx(38.125, abs=1e-9)

    def warnings_for(point: tuple[float, float], name: str) -> list[str]:
        drawing = SheetDrawing(sheet=resolve_sheet(_basin(reserve=None)), status="CONCEPT")
        drawing.add_model(point)
        return [
            warning
            for warning in validate_svg(_write_svg(tmp_path, drawing.render(), name)).warnings
            if "beyond the viewport frame" in warning
        ]

    # 40 m < 95.0 m of horizontal headroom: still on the paper, so no warning.
    assert warnings_for(((xmax + 40.0), (ymin + ymax) / 2.0), "horizontal.svg") == []
    # 40 m > 38.125 m of vertical headroom: off the paper, so it warns.
    assert len(warnings_for(((xmin + xmax) / 2.0, ymax + 40.0), "vertical.svg")) == 1


def test_non_preferred_ratio_warns_and_still_passes(tmp_path):
    """An A3 1:1250 sheet "validates clean" == ok is True with exactly one warning.

    1:1250 is in daily survey use but is not an ISO 5455 1/2/5-decade ratio, so it
    *always* carries this warning. Anything asserting a warning count on a 1:1250 sheet
    must budget for it — spelled out here because two acceptance tests in the base spec
    contradicted each other over exactly this.
    """
    path = _write_svg(tmp_path, _render(resolve_sheet(_basin(reserve=TITLE_BLOCK_RESERVE))))

    result = validate_svg(path)
    assert result.ok is True
    assert len(result.warnings) == 1, result.warnings
    assert "not an ISO 5455 preferred ratio" in result.warnings[0]
    assert "1:1000" in result.warnings[0]
    assert "1:2000" in result.warnings[0]
    assert main(["validate", str(path), "--strict"]) == 1

    preferred = _write_svg(
        tmp_path, _render(resolve_sheet(_config(1000, reserve=TITLE_BLOCK_RESERVE))), "ok.svg"
    )
    assert validate_svg(preferred).warnings == []


# --------------------------------------------------------------------------- #
# 22, 23 — the untouchable gate, and the title block that is not ours
# --------------------------------------------------------------------------- #


def test_the_issued_gate_is_untouched_by_the_sheet_model(tmp_path):
    config = _config(1000, reserve=TITLE_BLOCK_RESERVE)
    directory = _write_drawing_dir(tmp_path / "gated", config, for_construction=True)

    result = validate_target(directory)
    gate = (
        "meta: for_construction=true is only allowed once status is ISSUED "
        "(engineer sign-off required)"
    )
    assert result.ok is False
    assert gate in result.problems
    # --strict is monotone: it adds problems, it can never clear one.
    assert main(["validate", str(directory)]) == 1
    assert main(["validate", str(directory), "--strict"]) == 1

    # A `sheet:` block in `extra` changes nothing about the gate's verdict.
    plain = DrawingMeta(number="X", title="t", status="CONCEPT", for_construction=True)
    with_sheet = DrawingMeta(
        number="X",
        title="t",
        status="CONCEPT",
        for_construction=True,
        extra={"sheet": config},
    )
    assert with_sheet.validate() == plain.validate()
    assert with_sheet.sheet_config == config

    # By source inspection: sheet.py knows nothing about the lifecycle at all.
    source = SHEET_SOURCE.read_text(encoding="utf-8")
    for identifier in ("for_construction", "STATUS_ORDER", "status_key"):
        assert identifier not in source


def test_the_sheet_command_is_read_only():
    """No `--write` / `--fix` / `--migrate` may ever appear on this command."""
    from technical_drawings_for_agents.cli import build_parser

    actions = build_parser()._subparsers._group_actions  # noqa: SLF001
    choices = actions[0].choices  # type: ignore[attr-defined]
    assert "sheet" in choices
    options = [
        option for action in choices["sheet"]._actions for option in action.option_strings
    ]
    assert sorted(options) == ["--check", "--help", "--json", "-h"]
    for forbidden in ("write", "fix", "migrate"):
        assert not any(forbidden in option for option in options)


def test_paper_space_title_block_is_refused_until_issue_61(tmp_path):
    """P9/#61 owns the ISO 7200 block; P1 reserves the rectangle and stops there.

    Passing fields raises. What *is* emitted, when the sheet reserves a title-block
    region, is an empty placeholder group carrying the reservation geometry: a real
    element for validation to find (so no test has to splice a marker in), and nothing
    drawn — a px-sized stub block in a mm sheet would be a wrong artifact. With no
    reservation there is no rectangle to place, nothing is emitted, and the existing
    missing-title-block problem keeps the gap visible.
    """
    reserved = resolve_sheet(_config(1000, reserve=TITLE_BLOCK_RESERVE))

    with pytest.raises(SheetError, match="#61"):
        SheetDrawing(sheet=reserved, title_block={"title": "T"}).render()

    svg = _render(reserved)
    region = reserved.regions["title-block"]
    assert region == SheetFrame(10.0, 247.0, 400.0, 40.0)
    assert (
        '<g class="title-block" data-tdfa-placeholder="true" '
        'data-tdfa-frame="x=10.000;y=247.000;width=400.000;height=40.000"></g>'
    ) in svg
    # The placeholder is genuinely empty — it reserves, it does not draw.
    assert re.search(r'<g class="title-block"[^>]*></g>', svg) is not None
    assert validate_svg(_write_svg(tmp_path, svg, "reserved.svg")).ok is True

    unreserved = _render(resolve_sheet(_config(1000)))
    assert 'class="title-block"' not in unreserved
    result = validate_svg(_write_svg(tmp_path, unreserved, "unreserved.svg"))
    assert any("missing title block" in problem for problem in result.problems)


# --------------------------------------------------------------------------- #
# 24 — exact geometry
# --------------------------------------------------------------------------- #


def test_sheet_frame_and_scale_bar_geometry_are_exact_to_a_micron():
    sheet = resolve_sheet(_basin())

    assert 'x="10.000" y="10.000" width="400.000" height="277.000"' in sheet_frame(sheet)

    bar = sheet_scale_bar(sheet.viewport, sheet.viewport.frame, length_m=50, divisions=5)
    widths = [
        float(value) for value in re.findall(r'<rect x="[^"]+" y="[^"]+" width="([0-9.]+)"', bar)
    ]
    assert widths == [8.0, 8.0, 8.0, 8.0, 8.0]
    assert sum(widths) == pytest.approx(40.0, abs=1e-9)


def test_a_resolved_sheet_satisfies_p9s_paperframe_protocol():
    """P1 and P9 must actually connect.

    P9's ISO 7200 title block consumes a paper-space model through a five-member
    `PaperFrame` protocol, and was built and tested against a `FakeFrame` so it could
    land independently of P1. P1 never implemented that protocol — so
    `iso7200_title_block(sheet, ...)` could not be called on a real sheet at all, and
    the professional title block was unreachable from the paper-space model it was
    designed for. This pins the seam.
    """
    import technical_drawings_for_agents.titleblock as tb
    from technical_drawings_for_agents.sheet import resolve_sheet

    sheet = resolve_sheet(
        {
            "size": "A3",
            "orientation": "landscape",
            "viewport": {"extent_m": [0, 0, 310, 270], "scale": 1250},
        },
        context="test/sheet",
    )

    assert isinstance(sheet, tb.PaperFrame)
    assert sheet.size == "A3"
    assert sheet.orientation == "landscape"
    assert sheet.frame_mm() == (10.0, 10.0, 400.0, 277.0)
    # the sheet emits a mm viewBox, so device units are millimetres
    assert sheet.device_per_mm() == 1.0
    assert sheet.to_device(12.5, 34.5) == (12.5, 34.5)

    # and the title block actually renders against it
    rendered = tb.iso7200_title_block(
        sheet,
        tb.TitleBlockFields(
            identification_number="EXA-CIV-SEC-001",
            title="Integration probe",
            revision="A",
            date_of_issue="2026-07-25",
        ),
    )
    assert rendered.startswith("<g")
    assert "EXA-CIV-SEC-001" in rendered


def test_frame_mm_returns_the_drawing_frame_not_the_reserved_viewport():
    """The title block sits inside the DRAWING frame, and may occupy a region
    reserved away from the viewport. Returning the reserved-down viewport rectangle
    from frame_mm() would push the title block off its own reservation."""
    from technical_drawings_for_agents.sheet import resolve_sheet

    sheet = resolve_sheet(
        {
            "size": "A3",
            "orientation": "landscape",
            "reserve": [{"name": "title-block", "edge": "bottom", "size_mm": 56}],
            "viewport": {"extent_m": [0, 0, 310, 200], "scale": 1250},
        },
        context="test/sheet",
    )

    # viewport frame is reserved down to 277 - 56 = 221 mm high...
    assert sheet.viewport.frame.height_mm == 221.0
    # ...but frame_mm(), which the title block uses, is the full 277 mm drawing frame
    assert sheet.frame_mm() == (10.0, 10.0, 400.0, 277.0)


def test_add_paper_renders_outside_the_viewport_clip():
    """Reserved-region furniture must not be clipped away.

    `add()` places content in the clipped viewport group — right for the drawing view.
    But a legend, schedule or title block lives in a region the sheet itself RESERVES,
    outside the viewport, and `add()` clipped those away silently. The failure mode was
    quiet in the worst way: the elements stayed in the emitted DOM, so a DOM-parsing
    checker reported the sheet clean while the rendered PDF was missing its legend and
    title block entirely. Observed on the first real project sheet.
    """
    from technical_drawings_for_agents.sheet import SheetDrawing, resolve_sheet

    sheet = resolve_sheet(
        {
            "size": "A3",
            "orientation": "landscape",
            "reserve": [{"name": "legend", "edge": "right", "size_mm": 108}],
            "viewport": {"extent_m": [0, 0, 310, 200], "scale": 1250},
        },
        context="test/sheet",
    )
    legend = sheet.regions["legend"]
    # the legend region starts beyond the viewport frame's right edge
    assert legend.x_mm >= sheet.viewport.frame.x_mm + sheet.viewport.frame.width_mm

    d = SheetDrawing(sheet)
    d.add('<text id="in-view">view</text>')
    d.add_paper('<text id="in-legend">legend</text>')
    svg = d.svg()

    view_at = svg.index('id="in-view"')
    legend_at = svg.index('id="in-legend"')
    clip_open = svg.index('<g class="viewport"')
    clip_close = svg.index("</g>", clip_open)
    paper_open = svg.index('<g class="paper">')

    # the view element is inside the clipped group; the legend element is not
    assert clip_open < view_at < clip_close
    assert legend_at > paper_open > clip_close


def test_add_paper_emits_nothing_when_unused():
    """Backward compatibility: a sheet that never calls add_paper is unchanged."""
    from technical_drawings_for_agents.sheet import SheetDrawing, resolve_sheet

    sheet = resolve_sheet(
        {"size": "A3", "orientation": "landscape",
         "viewport": {"extent_m": [0, 0, 310, 200], "scale": 1250}},
        context="test/sheet",
    )
    assert '<g class="paper">' not in SheetDrawing(sheet).svg()


# --------------------------------------------------------------------------- #
# Pre-release rename: data-tdfa-* is written; legacy data-sankofa-* is still read
# --------------------------------------------------------------------------- #


def test_sheet_furniture_writes_only_the_tdfa_attribute_names():
    svg = _render(resolve_sheet(_basin(reserve=TITLE_BLOCK_RESERVE)))

    assert "data-tdfa-bar=" in svg
    assert "data-tdfa-placeholder=" in svg
    assert 'clip-path="url(#tdfa-viewport)"' in svg
    assert "sankofa" not in svg


def test_a_legacy_data_sankofa_drawing_still_validates_with_a_deprecation_warning(tmp_path):
    svg = _render(resolve_sheet(_basin(reserve=TITLE_BLOCK_RESERVE)))
    legacy = svg.replace("data-tdfa-", "data-sankofa-").replace("tdfa-viewport", "sankofa-viewport")
    assert "data-tdfa-" not in legacy

    with pytest.warns(DeprecationWarning, match="data-sankofa-bar"):
        result = validate_svg(_write_svg(tmp_path, legacy, "legacy.svg"))

    assert result.ok is True, result.problems


def test_a_scale_bar_attribute_that_disagrees_with_the_metadata_is_caught(tmp_path):
    svg = _render(resolve_sheet(_basin(reserve=TITLE_BLOCK_RESERVE)))
    tampered = re.sub(r'data-tdfa-bar="length_m=[^;]+;', 'data-tdfa-bar="length_m=999;', svg, count=1)
    assert tampered != svg

    result = validate_svg(_write_svg(tmp_path, tampered, "tampered.svg"))

    assert result.ok is False
    assert any("disagrees with the recorded scale bar" in p for p in result.problems)
