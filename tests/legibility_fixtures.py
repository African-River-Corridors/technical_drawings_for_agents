"""SVG fixtures for P5 legibility acceptance tests."""

from __future__ import annotations

import math
from pathlib import Path

import yaml

from technical_drawings_for_agents import Drawing, DrawingMeta, ViewBox
from technical_drawings_for_agents.svg import (
    svg_border,
    svg_circle,
    svg_line,
    svg_rect,
    svg_scale_bar,
    svg_status_watermark,
    svg_text,
    svg_title_block,
    svg_wrap,
)


def meta(**overrides) -> DrawingMeta:
    values = {
        "number": "STA-SITE-GA-001",
        "title": "WTP Site General Arrangement",
        "revision": "B",
        "scale": "1:250",
        "date": "2026-07-24",
        "status": "CONCEPT",
        "project": "Basin",
        "for_construction": False,
    }
    values.update(overrides)
    return DrawingMeta(**values)


def drawing(width: int = 920, height: int = 580, *, status: str = "CONCEPT") -> Drawing:
    return Drawing(
        width,
        height,
        ViewBox(0, 100, 0, 100, width, height, padding=60),
        title_block=meta().title_block(),
        status=status,
    )


def fixture_legend_over_swatches(anchor: str | None = None) -> tuple[str, DrawingMeta]:
    m = meta(number="STA-SITE-GA-001", title="Basin GA", revision="B", scale="1:1250")
    width, height = 1600, 1030
    vb = ViewBox(0, 100, 0, 100, width, height, padding=80)
    dwg = Drawing(width, height, vb, title_block=m.title_block(), status=m.status_key)
    dwg.add(svg_scale_bar(vb, 5, 5, 20, divisions=4, label="Scale (concept)"))
    entries = [
        ("#d9463b", "WTP treatment units"),
        ("#f59e0b", "Chemical dosing skids"),
        ("#2563eb", "Raw-water pipework"),
        ("#16a34a", "Access platforms"),
        ("#7c3aed", "Cable containment"),
        ("#64748b", "Existing structures"),
        ("#0ea5e9", "Drainage and washdown"),
    ]
    lx0, ly0 = width - 300, 92
    for index, (colour, label) in enumerate(entries):
        y = ly0 + 18 * index
        dwg.add(svg_rect(lx0, y, 14, 11, fill=colour, stroke="#d9e2ec", stroke_width=0.7))
        if anchor is None:
            dwg.add(svg_text(lx0 + 22, y + 9, label, font_size=9))
        else:
            dwg.add(svg_text(lx0 + 22, y + 9, label, font_size=9, anchor=anchor))
    return dwg.render(), m


def fixture_basin_furniture(*, fixed: bool = False, panel: bool = True) -> tuple[str, DrawingMeta]:
    m = meta(number="STA-SITE-GA-001", title="Basin GA", revision="B", scale="1:1250")
    width, height = 1600, 1030
    fxmin, fxmax = 788392, 788702
    fymin, fymax = 322125, 322395
    vb = ViewBox(fxmin, fxmax, fymin, fymax, width, height, padding=74)
    dwg = Drawing(width, height, vb, title_block=m.title_block(), status=m.status_key)
    if panel:
        px, py, pw, ph = (96, 600, 360, 300) if fixed else (96, 560, 380, 340)
        inner_vb = ViewBox(0, 100, 0, 100, pw, ph, padding=26)
        panel_parts = [
            f'<g transform="translate({px},{py})">',
            svg_rect(0, 0, pw, ph, fill="#0f1626", stroke="#d9e2ec", stroke_width=1.2),
            svg_rect(30, 42, min(300, pw - 60), min(240, ph - 84), fill="none"),
            svg_scale_bar(inner_vb, 5, 8, 30, divisions=3, label="Detail scale"),
            "</g>",
        ]
        dwg.add("\n".join(panel_parts))
    real_x = fxmax - 72 if fixed else fxmin + 12
    dwg.add(
        svg_scale_bar(
            vb,
            real_x,
            fymin + 14,
            50,
            divisions=5,
            label="Main view scale",
        )
    )
    return dwg.render(), m


def fixture_inset(
    *,
    extent_m: tuple[float, float] = (72, 52),
    content_m: tuple[float, float] = (38, 25),
    declared: bool = True,
    fallback_ratio: float | None = None,
):
    from technical_drawings_for_agents.legibility import Declarations, PanelDeclaration, Box

    m = meta(number="STA-SITE-GA-001", title="Inset", revision="B")
    width, height = 920, 580
    dwg = drawing(width, height)
    panel = Box(96, 180, 456, 480)
    if fallback_ratio is None:
        cw = panel.width * content_m[0] / extent_m[0]
        ch = panel.height * content_m[1] / extent_m[1]
    else:
        factor = math.sqrt(fallback_ratio)
        cw = panel.width * factor
        ch = panel.height * factor
    x = panel.x0 + (panel.width - cw) / 2.0
    y = panel.y0 + (panel.height - ch) / 2.0
    dwg.add(
        "\n".join(
            [
                f'<g transform="translate({panel.x0},{panel.y0})">',
                svg_rect(0, 0, panel.width, panel.height, fill="#0f1626"),
                svg_rect(
                    x - panel.x0,
                    y - panel.y0,
                    cw,
                    ch,
                    fill="none",
                    stroke="#d9e2ec",
                ),
                "</g>",
            ]
        )
    )
    declarations = None
    if declared:
        declarations = Declarations(
            panels=(
                PanelDeclaration(
                    name="WTP COMPOUND detail",
                    box=panel,
                    extent_m=extent_m,
                ),
            )
        )
    return dwg.render(), m, declarations


def fixture_callout_region(
    *,
    extent_m: tuple[float, float] = (87, 48),
    content_m: tuple[float, float] = (38, 25),
):
    """An in-view callout box that claims far more extent than its cluster fills.

    P5-FINAL 4.1: the fill-ratio rule must apply to any declared region, not
    only detail panels. The callout is drawn over the main view, so main-view
    geometry legitimately runs across its boundary -- the fixture includes such a
    crossing line to prove ``panel-clear`` does not fire on a callout.
    """
    from technical_drawings_for_agents.legibility import Box, Declarations, PanelDeclaration

    m = meta(number="STA-SITE-GA-001", title="Callout", revision="B")
    width, height = 920, 580
    dwg = drawing(width, height)
    region = Box(96, 180, 456, 480)
    cw = region.width * content_m[0] / extent_m[0]
    ch = region.height * content_m[1] / extent_m[1]
    x = region.x0 + (region.width - cw) / 2.0
    y = region.y0 + (region.height - ch) / 2.0
    # The callout boundary: a stroked box over the main view, no backing fill.
    dwg.add(svg_rect(region.x0, region.y0, region.width, region.height, fill="none"))
    # The cluster the callout is meant to frame.
    dwg.add(svg_rect(x, y, cw, ch, fill="none", stroke="#d9e2ec"))
    # Main-view geometry crossing the callout boundary: normal, not a defect.
    dwg.add(svg_line(60, 240, 520, 240))
    declarations = Declarations(
        panels=(
            PanelDeclaration(
                name="WTP COMPOUND callout",
                box=region,
                extent_m=extent_m,
                kind="callout",
            ),
        )
    )
    return dwg.render(), m, declarations


MARKER_RADIUS = 11.0


def _marker(cx: float, cy: float, number: str) -> str:
    """A numbered schedule marker: an opaque bubble with its number inside."""
    return "\n".join(
        [
            svg_circle(cx, cy, MARKER_RADIUS, fill="#0f1626", stroke="#d9e2ec", stroke_width=0.8),
            svg_text(cx, cy + 3, number, font_size=9, anchor="middle"),
        ]
    )


def fixture_marker_occlusion(*, fixed: bool = False) -> tuple[str, DrawingMeta]:
    """Numbered markers stacked at co-located placements.

    Observed on a real sheet (#57): three markers at one location and two at
    another. The lone marker is the control -- it must never be reported.
    """
    m = meta(number="STA-SITE-GA-001", title="Marker occlusion", revision="B")
    width, height = 920, 580
    dwg = drawing(width, height)
    if fixed:
        placements = [
            (200.0, 200.0, "1"),
            (240.0, 200.0, "2"),
            (280.0, 200.0, "3"),
            (200.0, 260.0, "4"),
            (240.0, 260.0, "5"),
            (200.0, 320.0, "6"),
        ]
    else:
        placements = [
            # three markers on one placement
            (200.0, 200.0, "1"),
            (203.0, 202.0, "2"),
            (201.0, 204.0, "3"),
            # two markers on another
            (500.0, 300.0, "4"),
            (504.0, 301.0, "5"),
            # the control: one marker on its own
            (700.0, 200.0, "6"),
        ]
    for cx, cy, number in placements:
        dwg.add(_marker(cx, cy, number))
    return dwg.render(), m


def fixture_fix_induced_collision(round_: int) -> tuple[str, DrawingMeta]:
    """Three rounds of by-eye label tuning, one of which regresses.

    Round 0 -- label A sits on the fixed label X: the original defect.
    Round 1 -- A and B are both nudged clear of X by eye, and land on each
               other. One finding before, one finding after, but a *different*
               pair: the regression a human moving labels cannot see.
    Round 2 -- placement that clears every pair.
    """
    m = meta(number="STA-SITE-GA-001", title="Fix-induced collision", revision="B")
    width, height = 920, 580
    dwg = drawing(width, height)
    # X never moves: it is the label the tuning was trying to clear.
    dwg.add(svg_text(300, 300, "X pump house", font_size=10, anchor="start"))
    if round_ == 0:
        a = (300, 296)
        b = (300, 340)
    elif round_ == 1:
        # Both nudged up and away from X; now A and B collide with each other.
        a = (300, 264)
        b = (296, 260)
    elif round_ == 2:
        a = (300, 264)
        b = (300, 220)
    else:  # pragma: no cover - guards the fixture's own contract
        raise ValueError(f"round_ must be 0, 1 or 2; got {round_}")
    dwg.add(svg_text(a[0], a[1], "A raw water main", font_size=10, anchor="start"))
    dwg.add(svg_text(b[0], b[1], "B dosing skid", font_size=10, anchor="start"))
    return dwg.render(), m


def minimal_svg(*parts: str, width: int = 920, height: int = 580) -> str:
    return svg_wrap("\n".join((svg_border(width, height), *parts)), width, height)


def no_watermark_svg() -> tuple[str, DrawingMeta]:
    m = meta()
    return minimal_svg(svg_title_block(920, 580, **m.title_block())), m


def write_yaml(path: Path, data: dict) -> Path:
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path
