"""Project-agnostic SVG engineering-drawing toolkit.

Generalised from an earlier vessel-design ``svg_utils.py``. Provides:

* :class:`ViewBox` — maps real-world metres to SVG canvas pixels (scale-true).
* Primitives — ``svg_line`` / ``rect`` / ``circle`` / ``polygon`` / ``path`` / ``text``.
* Drawing furniture — dimension lines, hatches, centrelines, leaders, scale
  bar, north arrow, title block, border, and a **status watermark**.
* :class:`Drawing` — a thin sheet assembler.

Domain-specific geometry (barges, vessels, tanks) is deliberately NOT here —
the core stays generic. See ``drawings/example/`` for a worked domain shape.
"""

from __future__ import annotations

import math
import textwrap
from dataclasses import dataclass, field
from typing import Any

from .style import (
    COL_CAD_BG,
    COL_CAD_CENTER,
    COL_CAD_DIM,
    COL_CAD_DIM_TEXT,
    COL_CAD_OBJECT,
    COL_CAD_OUTLINE,
    COL_CAD_TEXT,
    COL_CAD_CONCRETE,
    COL_CAD_ROCK,
    COL_CAD_SOIL,
    COL_CAD_WATER,
    DASH_CENTERLINE,
    LW_CENTER,
    LW_THIN,
    STATUS_WATERMARKS,
)


# --------------------------------------------------------------------------
# Hatch pattern catalogue
# --------------------------------------------------------------------------
HATCH_PATTERNS = {
    "concrete": {
        "label": "Concrete",
        "kind": "diagonal_cross",
        "spacing_m": 0.45,
        "color": COL_CAD_CONCRETE,
        "background": "rgba(154,160,180,0.10)",
        "stroke_width": 0.7,
    },
    "rockfill": {
        "label": "Rockfill",
        "kind": "rock",
        "spacing_m": 0.85,
        "color": COL_CAD_ROCK,
        "background": "rgba(200,135,60,0.14)",
        "stroke_width": 0.8,
    },
    "soil": {
        "label": "Soil",
        "kind": "earth",
        "spacing_m": 0.65,
        "color": COL_CAD_SOIL,
        "background": "rgba(107,91,62,0.16)",
        "stroke_width": 0.7,
    },
    "water": {
        "label": "Water",
        "kind": "water",
        "spacing_m": 0.75,
        "color": COL_CAD_WATER,
        "background": "rgba(68,136,255,0.14)",
        "stroke_width": 0.8,
    },
}


# --------------------------------------------------------------------------
# Coordinate scaling
# --------------------------------------------------------------------------
@dataclass
class ViewBox:
    """Maps real-world coordinates (metres) to SVG pixel coordinates."""

    real_min_x: float
    real_max_x: float
    real_min_y: float
    real_max_y: float
    svg_width: float
    svg_height: float
    padding: float = 60.0

    @property
    def scale(self) -> float:
        usable_w = self.svg_width - 2 * self.padding
        usable_h = self.svg_height - 2 * self.padding
        range_x = self.real_max_x - self.real_min_x
        range_y = self.real_max_y - self.real_min_y
        if range_x <= 0 or range_y <= 0:
            return 1.0
        return min(usable_w / range_x, usable_h / range_y)

    def x(self, real_x: float) -> float:
        return self.padding + (real_x - self.real_min_x) * self.scale

    def y(self, real_y: float) -> float:
        """Convert real Y to SVG Y (inverted so +Y is up)."""
        return self.svg_height - self.padding - (real_y - self.real_min_y) * self.scale

    def length(self, real_length: float) -> float:
        return abs(real_length * self.scale)

    def point(self, real_x: float, real_y: float):
        """Return an SVG coordinate pair for a real-world point."""
        return self.x(real_x), self.y(real_y)

    def snap_px(self, value: float, step: float = 0.5) -> float:
        """Snap a pixel coordinate/length to a stable SVG increment."""
        if step <= 0:
            return value
        return round(value / step) * step


# --------------------------------------------------------------------------
# Escaping / id helpers
# --------------------------------------------------------------------------
def _svg_escape(value) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _svg_id(value) -> str:
    token = []
    for ch in str(value):
        token.append(ch if ch.isalnum() or ch in "-_" else "-")
    return "".join(token).strip("-") or "item"


def _pattern_id(pattern, prefix="hatch") -> str:
    pattern_id = _svg_id(pattern)
    prefix_id = _svg_id(prefix) if prefix else ""
    return f"{prefix_id}-{pattern_id}" if prefix_id else pattern_id


def _pattern_names(patterns):
    if patterns is None:
        return list(HATCH_PATTERNS)
    if isinstance(patterns, str):
        return [patterns]
    if isinstance(patterns, (set, frozenset)):
        raise ValueError(
            "patterns must be a str or an ordered sequence, not a set — set iteration order "
            "varies with PYTHONHASHSEED and would make the <defs> block non-deterministic"
        )
    return list(patterns)


def _fmt_measure(value: float) -> str:
    if math.isclose(value, round(value), abs_tol=1e-9):
        return str(int(round(value)))
    return f"{value:g}"


def _emit_number(
    policy: Any | None,
    value,
    precision_name: str,
    default_format: str | None = None,
) -> str:
    if policy is None:
        return format(value, default_format) if default_format else str(value)
    return policy.fmt(float(value), getattr(policy, precision_name))


def _px(policy: Any | None, value) -> str:
    return _emit_number(policy, value, "precision_px", ".1f")


def _ratio(policy: Any | None, value) -> str:
    return _emit_number(policy, value, "precision_ratio")


def _font(policy: Any | None, value) -> str:
    return _emit_number(policy, value, "precision_font")


def _deg(policy: Any | None, value) -> str:
    return _emit_number(policy, value, "precision_deg")


# --------------------------------------------------------------------------
# Primitives
# --------------------------------------------------------------------------
def svg_line(x1, y1, x2, y2, stroke=COL_CAD_OBJECT, stroke_width=1, dash=None, policy=None):
    d = f' stroke-dasharray="{dash}"' if dash else ""
    if policy is not None:
        return (
            f'<line x1="{_px(policy, x1)}" y1="{_px(policy, y1)}" '
            f'x2="{_px(policy, x2)}" y2="{_px(policy, y2)}" '
            f'stroke="{stroke}" stroke-width="{_ratio(policy, stroke_width)}"{d}/>'
        )
    return (
        f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
        f'stroke="{stroke}" stroke-width="{stroke_width}"{d}/>'
    )


def svg_rect(
    x,
    y,
    w,
    h,
    fill="none",
    stroke=COL_CAD_OBJECT,
    stroke_width=1.5,
    rx=0,
    ry=0,
    policy=None,
):
    if policy is not None:
        r = f' rx="{_px(policy, rx)}" ry="{_px(policy, ry)}"' if rx or ry else ""
        return (
            f'<rect x="{_px(policy, x)}" y="{_px(policy, y)}" '
            f'width="{_px(policy, w)}" height="{_px(policy, h)}" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="{_ratio(policy, stroke_width)}"{r}/>'
        )
    r = f' rx="{rx:.1f}" ry="{ry:.1f}"' if rx or ry else ""
    return (
        f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" '
        f'fill="{fill}" stroke="{stroke}" stroke-width="{stroke_width}"{r}/>'
    )


def svg_circle(cx, cy, r, fill="none", stroke=COL_CAD_OBJECT, stroke_width=1.5, policy=None):
    if policy is not None:
        return (
            f'<circle cx="{_px(policy, cx)}" cy="{_px(policy, cy)}" r="{_px(policy, r)}" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="{_ratio(policy, stroke_width)}"/>'
        )
    return (
        f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r:.1f}" '
        f'fill="{fill}" stroke="{stroke}" stroke-width="{stroke_width}"/>'
    )


def svg_ellipse(cx, cy, rx, ry, fill="none", stroke=COL_CAD_OBJECT, stroke_width=1.5, policy=None):
    if policy is not None:
        return (
            f'<ellipse cx="{_px(policy, cx)}" cy="{_px(policy, cy)}" '
            f'rx="{_px(policy, rx)}" ry="{_px(policy, ry)}" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="{_ratio(policy, stroke_width)}"/>'
        )
    return (
        f'<ellipse cx="{cx:.1f}" cy="{cy:.1f}" rx="{rx:.1f}" ry="{ry:.1f}" '
        f'fill="{fill}" stroke="{stroke}" stroke-width="{stroke_width}"/>'
    )


def svg_polygon(points, fill="none", stroke=COL_CAD_OBJECT, stroke_width=1.5, policy=None):
    if policy is not None:
        pts = " ".join(f"{_px(policy, x)},{_px(policy, y)}" for x, y in points)
        return (
            f'<polygon points="{pts}" fill="{fill}" stroke="{stroke}" '
            f'stroke-width="{_ratio(policy, stroke_width)}"/>'
        )
    pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
    return (
        f'<polygon points="{pts}" fill="{fill}" stroke="{stroke}" '
        f'stroke-width="{stroke_width}"/>'
    )


def svg_path(d, fill="none", stroke=COL_CAD_OBJECT, stroke_width=1.5, policy=None):
    if policy is not None:
        return (
            f'<path d="{d}" fill="{fill}" stroke="{stroke}" '
            f'stroke-width="{_ratio(policy, stroke_width)}"/>'
        )
    return f'<path d="{d}" fill="{fill}" stroke="{stroke}" stroke-width="{stroke_width}"/>'


def svg_text(x, y, text, font_size=11, fill=COL_CAD_TEXT, anchor="middle", rotate=0, policy=None):
    if policy is not None:
        r = (
            f' transform="rotate({_deg(policy, rotate)},{_px(policy, x)},{_px(policy, y)})"'
            if rotate else ""
        )
        return (
            f'<text x="{_px(policy, x)}" y="{_px(policy, y)}" '
            f'font-size="{_font(policy, font_size)}" fill="{fill}" '
            f'text-anchor="{anchor}" font-family="monospace"{r}>{_svg_escape(text)}</text>'
        )
    r = f' transform="rotate({rotate},{x:.1f},{y:.1f})"' if rotate else ""
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" font-size="{font_size}" fill="{fill}" '
        f'text-anchor="{anchor}" font-family="monospace"{r}>{_svg_escape(text)}</text>'
    )


# --------------------------------------------------------------------------
# Hatch patterns
# --------------------------------------------------------------------------
def svg_pattern_defs(
    vb: ViewBox | None = None,
    patterns=None,
    prefix="hatch",
    policy=None,
    *,
    tile: float | None = None,
    stroke_width: float | None = None,
) -> str:
    """Return SVG ``<defs>`` for the named engineering hatch patterns.

    If ``vb`` is supplied, hatch spacing is converted from metres via
    ``ViewBox.length`` so the pattern density follows the drawing scale.

    ``tile`` / ``stroke_width`` are the **paper-space override**: on a millimetre
    sheet (:class:`technical_drawings_for_agents.sheet.SheetDrawing`) a hatch is a paper quantity,
    not a model one — ISO 128-50 spaces hatch lines in millimetres of paper, and a
    0.65 m "soil" spacing at 1:2000 would be a 0.3 mm tile, i.e. a grey smear. Pass
    the tile size and line width in the sheet's own user units (mm) and ``vb`` is
    ignored for sizing. Without them the legacy pixel behaviour is byte-identical.
    """
    names = _pattern_names(patterns)
    parts = ["<defs>"]
    for name in names:
        if name not in HATCH_PATTERNS:
            available = ", ".join(sorted(HATCH_PATTERNS))
            raise ValueError(f"unknown hatch pattern '{name}' (available: {available})")

        spec = HATCH_PATTERNS[name]
        spacing = spec["spacing_m"]
        if tile is not None:
            if not (tile > 0):
                raise ValueError(f"svg_pattern_defs: tile must be > 0, got {tile!r}")
            tile_size = float(tile)
        else:
            tile_size = max(0.1, vb.length(spacing)) if vb is not None else spacing * 18.0
            if vb is None:
                tile_size = max(4.0, tile_size)
        half = tile_size / 2.0
        lw = stroke_width if stroke_width is not None else spec.get("stroke_width", 0.7)
        tile = tile_size  # noqa: PLW2901 — local rename keeps the emit code below unchanged
        color = spec["color"]
        background = spec.get("background", "none")
        pid = _pattern_id(name, prefix)

        parts.append(
            f'<pattern id="{pid}" patternUnits="userSpaceOnUse" '
            f'width="{_px(policy, tile)}" height="{_px(policy, tile)}">'
        )
        if background and background != "none":
            parts.append(
                f'<rect width="{_px(policy, tile)}" height="{_px(policy, tile)}" '
                f'fill="{background}"/>'
            )

        kind = spec.get("kind", "diagonal")
        if kind == "diagonal_cross":
            parts.append(
                f'<path d="M 0 {_px(policy, tile)} L {_px(policy, tile)} 0 M 0 0 '
                f'L {_px(policy, tile)} {_px(policy, tile)}" stroke="{color}" '
                f'stroke-width="{_ratio(policy, lw)}" opacity="{_ratio(policy, 0.85)}"/>'
            )
        elif kind == "rock":
            r = max(1.0, tile * 0.075)
            pts = (
                f'{_px(policy, tile * 0.58)},{_px(policy, tile * 0.20)} '
                f'{_px(policy, tile * 0.78)},{_px(policy, tile * 0.30)} '
                f'{_px(policy, tile * 0.70)},{_px(policy, tile * 0.52)} '
                f'{_px(policy, tile * 0.50)},{_px(policy, tile * 0.48)}'
            )
            parts.append(
                f'<circle cx="{_px(policy, tile * 0.28)}" '
                f'cy="{_px(policy, tile * 0.34)}" r="{_px(policy, r)}" '
                f'fill="none" stroke="{color}" stroke-width="{_ratio(policy, lw)}"/>'
            )
            parts.append(
                f'<polygon points="{pts}" fill="none" stroke="{color}" '
                f'stroke-width="{_ratio(policy, lw)}"/>'
            )
            parts.append(
                f'<path d="M {_px(policy, tile * 0.16)} {_px(policy, tile * 0.78)} '
                f'L {_px(policy, tile * 0.40)} {_px(policy, tile * 0.66)} '
                f'L {_px(policy, tile * 0.54)} {_px(policy, tile * 0.88)}" fill="none" '
                f'stroke="{color}" stroke-width="{_ratio(policy, lw)}"/>'
            )
        elif kind == "earth":
            parts.append(
                f'<path d="M 0 {_px(policy, half)} L {_px(policy, tile)} 0 '
                f'M 0 {_px(policy, tile)} L {_px(policy, tile)} {_px(policy, half)}" '
                f'stroke="{color}" stroke-width="{_ratio(policy, lw)}" '
                f'opacity="{_ratio(policy, 0.85)}"/>'
            )
            parts.append(
                f'<circle cx="{_px(policy, tile * 0.25)}" '
                f'cy="{_px(policy, tile * 0.75)}" '
                f'r="{_px(policy, max(0.8, tile * 0.045))}" fill="{color}" '
                f'opacity="{_ratio(policy, 0.8)}"/>'
            )
            parts.append(
                f'<circle cx="{_px(policy, tile * 0.78)}" '
                f'cy="{_px(policy, tile * 0.42)}" '
                f'r="{_px(policy, max(0.8, tile * 0.04))}" fill="{color}" '
                f'opacity="{_ratio(policy, 0.8)}"/>'
            )
        elif kind == "water":
            y = tile * 0.55
            parts.append(
                f'<path d="M 0 {_px(policy, y)} C {_px(policy, tile * 0.18)} '
                f'{_px(policy, tile * 0.28)}, {_px(policy, tile * 0.33)} '
                f'{_px(policy, tile * 0.82)}, {_px(policy, half)} {_px(policy, y)} '
                f'S {_px(policy, tile * 0.82)} {_px(policy, tile * 0.28)}, '
                f'{_px(policy, tile)} {_px(policy, y)}" fill="none" stroke="{color}" '
                f'stroke-width="{_ratio(policy, lw)}" opacity="{_ratio(policy, 0.9)}"/>'
            )
        else:
            parts.append(
                f'<path d="M 0 {_px(policy, tile)} L {_px(policy, tile)} 0" '
                f'stroke="{color}" stroke-width="{_ratio(policy, lw)}"/>'
            )

        parts.append("</pattern>")
    parts.append("</defs>")
    return "\n".join(parts)


def svg_hatch(
    vb: ViewBox | None,
    points,
    pattern="concrete",
    prefix="hatch",
    stroke=COL_CAD_OUTLINE,
    stroke_width=LW_THIN,
    outline=True,
    policy=None,
):
    """Draw a hatch-filled polygon from real-world points."""
    if pattern not in HATCH_PATTERNS:
        available = ", ".join(sorted(HATCH_PATTERNS))
        raise ValueError(f"unknown hatch pattern '{pattern}' (available: {available})")
    mapped = [vb.point(x, y) if vb is not None else (x, y) for x, y in points]
    return svg_polygon(
        mapped,
        fill=f"url(#{_pattern_id(pattern, prefix)})",
        stroke=stroke if outline else "none",
        stroke_width=stroke_width if outline else 0,
        policy=policy,
    )


def svg_centerline(
    vb: ViewBox,
    real_x1,
    real_y1,
    real_x2,
    real_y2,
    extension_m=0.0,
    stroke=COL_CAD_CENTER,
    stroke_width=LW_CENTER,
    dash=DASH_CENTERLINE,
    policy=None,
):
    """Draw a metric centreline between two real-world points."""
    dx = real_x2 - real_x1
    dy = real_y2 - real_y1
    length = math.hypot(dx, dy)
    if extension_m and length:
        ux = dx / length
        uy = dy / length
        real_x1 -= ux * extension_m
        real_y1 -= uy * extension_m
        real_x2 += ux * extension_m
        real_y2 += uy * extension_m
    x1, y1 = vb.point(real_x1, real_y1)
    x2, y2 = vb.point(real_x2, real_y2)
    return svg_line(
        x1,
        y1,
        x2,
        y2,
        stroke=stroke,
        stroke_width=stroke_width,
        dash=dash,
        policy=policy,
    )


# --------------------------------------------------------------------------
# Dimension lines
# --------------------------------------------------------------------------
def svg_dimension_h(vb: ViewBox, real_y, real_x1, real_x2, label, offset_px=25, policy=None):
    """Horizontal dimension line below the reference Y position."""
    x1 = vb.x(real_x1)
    x2 = vb.x(real_x2)
    y_ref = vb.y(real_y)
    y = y_ref + offset_px

    parts = [
        svg_line(x1, y_ref, x1, y + 3, stroke=COL_CAD_DIM, stroke_width=0.5, policy=policy),
        svg_line(x2, y_ref, x2, y + 3, stroke=COL_CAD_DIM, stroke_width=0.5, policy=policy),
        svg_line(x1, y, x2, y, stroke=COL_CAD_DIM, stroke_width=0.7, policy=policy),
        f'<polygon points="{_px(policy, x1)},{_px(policy, y)} '
        f'{_px(policy, x1 + 5)},{_px(policy, y - 2.5)} '
        f'{_px(policy, x1 + 5)},{_px(policy, y + 2.5)}" fill="{COL_CAD_DIM}"/>',
        f'<polygon points="{_px(policy, x2)},{_px(policy, y)} '
        f'{_px(policy, x2 - 5)},{_px(policy, y - 2.5)} '
        f'{_px(policy, x2 - 5)},{_px(policy, y + 2.5)}" fill="{COL_CAD_DIM}"/>',
    ]
    mx = (x1 + x2) / 2
    parts.append(svg_text(mx, y - 4, label, font_size=10, fill=COL_CAD_DIM_TEXT, policy=policy))
    return "\n".join(parts)


def svg_dimension_v(
    vb: ViewBox,
    real_x,
    real_y1,
    real_y2,
    label,
    offset_px=25,
    color=COL_CAD_DIM,
    policy=None,
):
    """Vertical dimension line to the right of the reference X position."""
    x_ref = vb.x(real_x)
    x = x_ref + offset_px
    y1 = vb.y(real_y1)
    y2 = vb.y(real_y2)

    parts = [
        svg_line(x_ref, y1, x + 3, y1, stroke=color, stroke_width=0.5, policy=policy),
        svg_line(x_ref, y2, x + 3, y2, stroke=color, stroke_width=0.5, policy=policy),
        svg_line(x, y1, x, y2, stroke=color, stroke_width=0.7, policy=policy),
        f'<polygon points="{_px(policy, x)},{_px(policy, y1)} '
        f'{_px(policy, x - 2.5)},{_px(policy, y1 - 5)} '
        f'{_px(policy, x + 2.5)},{_px(policy, y1 - 5)}" fill="{color}"/>',
        f'<polygon points="{_px(policy, x)},{_px(policy, y2)} '
        f'{_px(policy, x - 2.5)},{_px(policy, y2 + 5)} '
        f'{_px(policy, x + 2.5)},{_px(policy, y2 + 5)}" fill="{color}"/>',
    ]
    my = (y1 + y2) / 2
    parts.append(
        svg_text(
            x + 12,
            my + 3,
            label,
            font_size=10,
            fill=COL_CAD_DIM_TEXT,
            rotate=-90,
            policy=policy,
        )
    )
    return "\n".join(parts)


def svg_leader(
    vb: ViewBox,
    target,
    elbow,
    label_point,
    label,
    dot_radius=3.0,
    stroke=COL_CAD_DIM,
    stroke_width=LW_THIN,
    text_fill=COL_CAD_DIM_TEXT,
    font_size=10,
    anchor="start",
    policy=None,
):
    """Draw a dot, kinked leader line, and label from real-world coordinates."""
    tx, ty = vb.point(*target)
    ex, ey = vb.point(*elbow)
    lx, ly = vb.point(*label_point)
    text_dx = 5 if anchor == "start" else -5 if anchor == "end" else 0
    parts = [
        svg_circle(tx, ty, dot_radius, fill=stroke, stroke=stroke, stroke_width=0, policy=policy),
        svg_line(tx, ty, ex, ey, stroke=stroke, stroke_width=stroke_width, policy=policy),
        svg_line(ex, ey, lx, ly, stroke=stroke, stroke_width=stroke_width, policy=policy),
    ]
    for i, line in enumerate(str(label).splitlines() or [""]):
        parts.append(
            svg_text(
                lx + text_dx,
                ly - 4 + i * (font_size + 2),
                line,
                font_size=font_size,
                fill=text_fill,
                anchor=anchor,
                policy=policy,
            )
        )
    return "\n".join(parts)


# --------------------------------------------------------------------------
# Sheet furniture
# --------------------------------------------------------------------------
def svg_scale_bar(
    vb: ViewBox,
    real_x,
    real_y,
    length_m,
    divisions=4,
    unit="m",
    label=None,
    height_px=8,
    stroke=COL_CAD_DIM,
    text_fill=COL_CAD_DIM_TEXT,
    fill_a="rgba(217,226,236,0.18)",
    fill_b="rgba(26,26,46,0.20)",
    policy=None,
):
    """Draw a true-to-scale metric scale bar anchored at a real-world point."""
    if divisions < 1:
        raise ValueError("divisions must be >= 1")
    x0, y0 = vb.point(real_x, real_y)
    total_w = vb.length(length_m)
    seg_w = total_w / divisions
    parts = ['<g class="scale-bar">']
    for i in range(divisions):
        parts.append(
            svg_rect(
                x0 + i * seg_w,
                y0,
                seg_w,
                height_px,
                fill=fill_a if i % 2 == 0 else fill_b,
                stroke=stroke,
                stroke_width=LW_THIN,
                policy=policy,
            )
        )
    parts.append(
        svg_line(
            x0,
            y0 + height_px,
            x0 + total_w,
            y0 + height_px,
            stroke=stroke,
            stroke_width=LW_THIN,
            policy=policy,
        )
    )
    for i in range(divisions + 1):
        x = x0 + i * seg_w
        parts.append(
            svg_line(
                x,
                y0 + height_px,
                x,
                y0 + height_px + 5,
                stroke=stroke,
                stroke_width=LW_THIN,
                policy=policy,
            )
        )
        value = length_m * i / divisions
        suffix = f" {unit}" if i == divisions else ""
        parts.append(
            svg_text(
                x,
                y0 + height_px + 18,
                f"{_fmt_measure(value)}{suffix}",
                font_size=9,
                fill=text_fill,
                policy=policy,
            )
        )
    if label:
        parts.append(
            svg_text(
                x0 + total_w / 2,
                y0 - 5,
                label,
                font_size=10,
                fill=text_fill,
                policy=policy,
            )
        )
    parts.append("</g>")
    return "\n".join(parts)


def svg_north_arrow(
    vb: ViewBox,
    real_x,
    real_y,
    size_m=None,
    size_px=42,
    label="N",
    stroke=COL_CAD_DIM_TEXT,
    fill=COL_CAD_DIM_TEXT,
    text_fill=COL_CAD_DIM_TEXT,
    policy=None,
):
    """Draw a plan north arrow centred on a real-world point."""
    cx, cy = vb.point(real_x, real_y)
    size = vb.length(size_m) if size_m is not None else size_px
    size = max(12.0, size)
    tip_y = cy - size / 2.0
    base_y = cy + size * 0.32
    inner_y = cy + size * 0.05
    wing = size * 0.23
    parts = [
        svg_text(
            cx,
            tip_y - 6,
            label,
            font_size=12,
            fill=text_fill,
            anchor="middle",
            policy=policy,
        ),
        svg_polygon(
            [(cx, tip_y), (cx - wing, base_y), (cx, inner_y), (cx + wing, base_y)],
            fill=fill,
            stroke=stroke,
            stroke_width=LW_THIN,
            policy=policy,
        ),
        svg_line(
            cx,
            inner_y,
            cx,
            cy + size / 2.0,
            stroke=stroke,
            stroke_width=LW_THIN,
            policy=policy,
        ),
    ]
    return "\n".join(parts)


def svg_title_block(
    width,
    height,
    project="",
    title="",
    drawing_no="",
    scale="",
    date="",
    revision="",
    margin=24,
    block_width=280,
    row_height=18,
    fill=COL_CAD_BG,
    stroke=COL_CAD_DIM,
    text_fill=COL_CAD_DIM_TEXT,
    title_fill=COL_CAD_OUTLINE,
    label_fill=COL_CAD_DIM,
    policy=None,
):
    """Draw a bottom-right title block with standard engineering fields.

    **Legacy pixel-space block.** Its size is fixed in *pixels* on a variable
    canvas, so it is physically the wrong size on any paper but one, and it
    **silently ellipsises** an over-long value (see the ``value_chars`` wrap
    below). Retained unchanged for byte-identical compatibility with every
    existing ``Drawing`` consumer; no ``DeprecationWarning`` is raised, because a
    warning in every existing consumer's output buys nothing while the paper-space
    path is still landing.

    Paper-space sheets should use :func:`technical_drawings_for_agents.titleblock.iso7200_title_block`,
    which is a fixed *millimetre* block and raises rather than truncating.
    """
    label_w = 78
    title_h = row_height * 2
    rows = [
        ("PROJECT", project, row_height),
        ("TITLE", title, title_h),
        ("DRAWING NO.", drawing_no, row_height),
        ("SCALE", scale, row_height),
        ("DATE", date, row_height),
        ("REV.", revision, row_height),
    ]
    block_height = sum(row[2] for row in rows)
    x0 = width - margin - block_width
    y0 = height - margin - block_height
    parts = [
        '<g class="title-block">',
        svg_rect(
            x0,
            y0,
            block_width,
            block_height,
            fill=fill,
            stroke=stroke,
            stroke_width=LW_THIN,
            policy=policy,
        ),
        svg_line(
            x0 + label_w,
            y0,
            x0 + label_w,
            y0 + block_height,
            stroke=stroke,
            stroke_width=LW_THIN,
            policy=policy,
        ),
    ]

    y = y0
    value_w = block_width - label_w - 12
    value_chars = max(8, int(value_w / 5.7))
    for i, (label_text, value, row_h) in enumerate(rows):
        if i:
            parts.append(
                svg_line(
                    x0,
                    y,
                    x0 + block_width,
                    y,
                    stroke=stroke,
                    stroke_width=LW_THIN,
                    policy=policy,
                )
            )
        parts.append(
            svg_text(
                x0 + 6,
                y + 12,
                label_text,
                font_size=7,
                fill=label_fill,
                anchor="start",
                policy=policy,
            )
        )

        lines = textwrap.wrap(str(value), width=value_chars) or [""]
        max_lines = 2 if label_text == "TITLE" else 1
        if len(lines) > max_lines:
            lines = lines[:max_lines]
            lines[-1] = lines[-1][: max(0, value_chars - 3)] + "..."
        line_gap = 11 if max_lines > 1 else 0
        start_y = y + 12 if max_lines == 1 else y + 11
        for j, line in enumerate(lines):
            parts.append(
                svg_text(
                    x0 + label_w + 6,
                    start_y + j * line_gap,
                    line,
                    font_size=8 if max_lines == 1 else 9,
                    fill=title_fill if label_text == "TITLE" else text_fill,
                    anchor="start",
                    policy=policy,
                )
            )
        y += row_h
    parts.append("</g>")
    return "\n".join(parts)


def svg_border(width, height, margin=24, stroke=COL_CAD_DIM, stroke_width=LW_THIN, policy=None):
    """Draw a simple drawing-sheet border."""
    return svg_rect(
        margin,
        margin,
        width - 2 * margin,
        height - 2 * margin,
        fill="none",
        stroke=stroke,
        stroke_width=stroke_width,
        policy=policy,
    )


def svg_status_watermark(
    width,
    height,
    status,
    text=None,
    color=None,
    font_size=None,
    opacity=0.14,
    policy=None,
):
    """Draw a large diagonal status watermark across the sheet.

    ``status`` is one of ``DRAFT`` / ``CONCEPT`` / ``ISSUED`` (case-insensitive);
    it selects the standard text and colour from :data:`STATUS_WATERMARKS`.
    ``text`` / ``color`` override the defaults. The watermark is a hard
    requirement of the Engineering-Drawings-as-Code standard and is what
    ``technical_drawings_for_agents validate`` checks for.
    """
    key = str(status).strip().upper()
    spec = STATUS_WATERMARKS.get(key)
    if spec is None and text is None:
        available = ", ".join(STATUS_WATERMARKS)
        raise ValueError(f"unknown status '{status}' (available: {available})")
    label = text if text is not None else spec["text"]
    col = color if color is not None else (spec["color"] if spec else "#ff4444")
    cx, cy = width / 2.0, height / 2.0
    # Size the text to roughly span the sheet diagonal.
    if font_size is None:
        font_size = max(18.0, min(width, height) / max(8, len(label)) * 1.6)
    angle = -math.degrees(math.atan2(height, width))
    if policy is not None:
        return (
            f'<g class="status-watermark" opacity="{_ratio(policy, opacity)}" '
            f'pointer-events="none"><text x="{_px(policy, cx)}" y="{_px(policy, cy)}" '
            f'font-size="{_font(policy, font_size)}" fill="{col}" text-anchor="middle" '
            f'dominant-baseline="middle" font-family="monospace" font-weight="bold" '
            f'transform="rotate({_deg(policy, angle)},{_px(policy, cx)},{_px(policy, cy)})">'
            f'{_svg_escape(label)}</text></g>'
        )
    return (
        f'<g class="status-watermark" opacity="{opacity}" pointer-events="none">'
        f'<text x="{cx:.1f}" y="{cy:.1f}" font-size="{font_size:.1f}" fill="{col}" '
        f'text-anchor="middle" dominant-baseline="middle" font-family="monospace" '
        f'font-weight="bold" transform="rotate({angle:.1f},{cx:.1f},{cy:.1f})">'
        f'{_svg_escape(label)}</text></g>'
    )


def svg_provenance_stamp(
    width,
    height,
    text,
    *,
    margin=24,
    policy=None,
    placement="fallback-bottom-left",
    block_width=280,
) -> str:
    """Return a small marked provenance hash for a drawing sheet.

    ``class="provenance-stamp"`` follows the marker convention ``validate.py``
    greps for, so tests and ``validate`` find it by substring with no XML parsing.

    ``below-title-block`` puts the stamp in the margin strip just outside the
    border, aligned with the title block's left edge — the conventional home for
    plot metadata. It is deliberately *not* a new title-block row: adding a row
    would change ``block_height`` and move every existing title block's geometry,
    which parity forbids. P1 (#53) owns final placement in paper space.
    """

    if placement == "below-title-block":
        x = width - margin - block_width
        y = height - margin + 9
    else:
        x = margin + 6
        y = height - margin - 6
    return (
        f'<g class="provenance-stamp"><text x="{_px(policy, x)}" y="{_px(policy, y)}" '
        f'font-size="{_font(policy, 7)}" fill="{COL_CAD_DIM}" text-anchor="start" '
        f'font-family="monospace">{_svg_escape(text)}</text></g>'
    )


def svg_wrap(content, width, height, background=COL_CAD_BG, policy=None):
    """Wrap SVG elements in an ``<svg>`` root with an explicit size.

    Explicit width/height attributes are required: with only width="100%" an
    ``<img>`` element has no intrinsic aspect ratio and renders at 0 height.
    """
    if policy is not None:
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'viewBox="0 0 {_px(policy, width)} {_px(policy, height)}" '
            f'width="{_px(policy, width)}" height="{_px(policy, height)}" '
            f'preserveAspectRatio="xMidYMid meet" '
            f'style="background:{background}; border-radius:6px;">'
            f'<rect width="{_px(policy, width)}" height="{_px(policy, height)}" '
            f'fill="{background}"/>'
            f"{content}</svg>"
        )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}" preserveAspectRatio="xMidYMid meet" '
        f'style="background:{background}; border-radius:6px;">'
        f'<rect width="{width}" height="{height}" fill="{background}"/>'
        f"{content}</svg>"
    )


# --------------------------------------------------------------------------
# Sheet assembler
# --------------------------------------------------------------------------
@dataclass
class Drawing:
    """A thin SVG engineering-sheet assembler.

    Set ``status`` to one of DRAFT/CONCEPT/ISSUED to stamp the required status
    watermark automatically at render time.
    """

    width: float
    height: float
    viewbox: ViewBox
    title_block: dict = None
    status: str = None
    border_margin: float = 24.0
    background: str = COL_CAD_BG
    elements: list = field(default_factory=list)
    defs: list = field(default_factory=list)
    policy: Any | None = None
    provenance_stamp: str | None = None

    @property
    def vb(self):
        return self.viewbox

    def add(self, *elements):
        self.elements.extend(element for element in elements if element)
        return self

    def add_defs(self, *defs):
        self.defs.extend(defn for defn in defs if defn)
        return self

    def render(self):
        parts = []
        parts.extend(self.defs)
        parts.append(
            svg_border(
                self.width,
                self.height,
                margin=self.border_margin,
                policy=self.policy,
            )
        )
        parts.extend(self.elements)
        if self.status is not None:
            parts.append(
                svg_status_watermark(
                    self.width,
                    self.height,
                    self.status,
                    policy=self.policy,
                )
            )
        if self.title_block is not None:
            parts.append(
                svg_title_block(
                    self.width,
                    self.height,
                    **self.title_block,
                    policy=self.policy,
                )
            )
        if self.provenance_stamp is not None:
            block_width = 280
            if isinstance(self.title_block, dict):
                block_width = self.title_block.get("block_width", block_width)
            parts.append(
                svg_provenance_stamp(
                    self.width,
                    self.height,
                    self.provenance_stamp,
                    margin=self.border_margin,
                    policy=self.policy,
                    placement=(
                        "below-title-block"
                        if self.title_block is not None else "fallback-bottom-left"
                    ),
                    block_width=block_width,
                )
            )
        return svg_wrap(
            "\n".join(parts),
            self.width,
            self.height,
            background=self.background,
            policy=self.policy,
        )

    def svg(self):
        return self.render()
