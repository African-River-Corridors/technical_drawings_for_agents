"""Shared ISO house-sheet furniture (monochrome, print-oriented).

The ISO 5457-style double frame, ISO 7200-style title block, logo strip, legend
and notes band that wrap a *nested* inner drawing. Both the block-flow generator
(:mod:`technical_drawings_for_agents.bfd`) and the P&ID generator (:mod:`technical_drawings_for_agents.pid`) build
their content as an inner ``<svg>`` body and hand it to :func:`sheet`.

This is the "reuse the existing furniture" seam: the sheet chrome lives here once
rather than being copied per drawing class. It is deliberately black-on-white
(this drawing class prints), distinct from the dark-theme CAD furniture in
:mod:`technical_drawings_for_agents.svg` (which serves the scale-true engineered sheets).
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from .svg import svg_status_watermark

BLACK = "#000000"
WHITE = "#ffffff"
GREY = "#555555"
RED = "#b00000"


def esc(s) -> str:
    """XML-escape text content."""
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _num(policy: Any | None, value, precision_name="precision_px") -> str:
    if policy is None:
        return str(value)
    return policy.fmt(float(value), getattr(policy, precision_name))


def text(x, y, s, size=9, anchor="start", weight="normal", fill=BLACK, policy=None) -> str:
    w = ' font-weight="bold"' if weight == "bold" else ""
    return (
        f'<text x="{_num(policy, x)}" y="{_num(policy, y)}" '
        f'font-size="{_num(policy, size, "precision_font")}" text-anchor="{anchor}" '
        f'fill="{fill}"{w}>{esc(s)}</text>'
    )


def rect(x, y, w, h, sw=1.0, fill="none", policy=None) -> str:
    return (
        f'<rect x="{_num(policy, x)}" y="{_num(policy, y)}" '
        f'width="{_num(policy, w)}" height="{_num(policy, h)}" fill="{fill}" '
        f'stroke="black" stroke-width="{_num(policy, sw, "precision_ratio")}"/>'
    )


def wrap(s: str, width: int) -> list[str]:
    """Greedy word-wrap to a character width (no truncation)."""
    out, cur = [], ""
    for w in str(s).split():
        if cur and len(cur) + len(w) + 1 > width:
            out.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        out.append(cur)
    return out or [""]


def img_data_uri(path: Path) -> str:
    """Read an image file and return a self-contained base64 data URI."""
    mime = {
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "svg": "image/svg+xml",
        "gif": "image/gif",
    }.get(path.suffix.lower().lstrip("."), "image/png")
    b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{b64}"


def titleblock(x, y, meta, policy=None) -> str:
    """ISO 7200-style title block. Carries ``class="title-block"`` so
    :mod:`technical_drawings_for_agents.validate` can confirm the sheet has one."""
    W, H = 500, 96
    p = [
        '<g class="title-block" font-family="Helvetica, Arial, sans-serif">',
        rect(x, y, W, H, 1.0, policy=policy),
    ]
    p += [
        f'<line x1="{_num(policy, x)}" y1="{_num(policy, y+28)}" '
        f'x2="{_num(policy, x+W)}" y2="{_num(policy, y+28)}" '
        f'stroke="black" stroke-width="{_num(policy, 0.6, "precision_ratio")}"/>',
        f'<line x1="{_num(policy, x)}" y1="{_num(policy, y+58)}" '
        f'x2="{_num(policy, x+W)}" y2="{_num(policy, y+58)}" '
        f'stroke="black" stroke-width="{_num(policy, 0.6, "precision_ratio")}"/>',
        f'<line x1="{_num(policy, x+210)}" y1="{_num(policy, y)}" '
        f'x2="{_num(policy, x+210)}" y2="{_num(policy, y+28)}" '
        f'stroke="black" stroke-width="{_num(policy, 0.6, "precision_ratio")}"/>',
        f'<line x1="{_num(policy, x+350)}" y1="{_num(policy, y)}" '
        f'x2="{_num(policy, x+350)}" y2="{_num(policy, y+28)}" '
        f'stroke="black" stroke-width="{_num(policy, 0.6, "precision_ratio")}"/>',
        f'<line x1="{_num(policy, x+300)}" y1="{_num(policy, y+58)}" '
        f'x2="{_num(policy, x+300)}" y2="{_num(policy, y+H)}" '
        f'stroke="black" stroke-width="{_num(policy, 0.6, "precision_ratio")}"/>',
        f'<line x1="{_num(policy, x+370)}" y1="{_num(policy, y+58)}" '
        f'x2="{_num(policy, x+370)}" y2="{_num(policy, y+H)}" '
        f'stroke="black" stroke-width="{_num(policy, 0.6, "precision_ratio")}"/>',
    ]
    def lbl(dx, dy, s):
        return text(x + dx, y + dy, s, 7, fill=GREY, policy=policy)

    def val(dx, dy, s, **k):
        return text(
            x + dx,
            y + dy,
            s,
            k.get("size", 10),
            weight=k.get("w", "normal"),
            fill=k.get("f", BLACK),
            policy=policy,
        )

    p += [
        lbl(6, 11, "RESPONSIBLE DEPT."),
        lbl(216, 11, "TECHNICAL REFERENCE"),
        lbl(356, 11, "CREATED / APPR."),
        val(6, 24, meta.get("client", "Example Client")),
        val(216, 24, meta.get("programme", "")),
        val(356, 24, "AI-assisted / (Eng. —)", size=8),
        lbl(6, 41, "DOCUMENT TYPE"),
        lbl(216, 41, "DOCUMENT STATUS"),
        val(6, 54, meta.get("doctype", "SCHEMATIC"), w="bold"),
        val(216, 54, meta.get("status", "DRAFT / CONCEPT"), w="bold", f=RED),
        lbl(6, 71, "TITLE / LEGAL OWNER"),
        lbl(306, 71, "DOC. No."),
        lbl(376, 71, "REV / DATE"),
        val(6, 88, meta.get("title", ""), size=10, w="bold"),
        val(306, 88, meta.get("number", ""), size=9, w="bold"),
        val(376, 88, f'{meta.get("rev","A")} · {meta.get("date","")}', size=8),
    ]
    p.append("</g>")
    return "\n".join(p)


def logo_strip(x, y, w, meta, policy=None) -> str:
    """Two logo cells above the title block. Embeds an image when
    ``meta['logos']`` supplies a path/data-URI; otherwise a labelled placeholder.

    The cells default to ``owner`` and ``client``; ``meta['logo_cells']`` overrides
    them as a list of ``[key, label]`` pairs (keys index ``meta['logos']``)."""
    h, half = 46, w / 2
    logos = meta.get("logos", {}) or {}
    p = ['<g font-family="Helvetica, Arial, sans-serif">']
    cells = meta.get("logo_cells") or [("owner", "OWNER"), ("client", "CLIENT")]
    for i, (key, label) in enumerate(cells[:2]):
        cx0 = x + i * half
        p.append(rect(cx0, y, half, h, 0.8, policy=policy))
        if logos.get(key):
            p.append(f'<image x="{_num(policy, cx0+10)}" y="{_num(policy, y+6)}" '
                     f'width="{_num(policy, half-20)}" height="{_num(policy, h-12)}" '
                     f'href="{logos[key]}" preserveAspectRatio="xMidYMid meet"/>')
        else:
            p.append(
                text(
                    cx0 + half / 2,
                    y + h / 2 - 1,
                    label,
                    13,
                    anchor="middle",
                    weight="bold",
                    policy=policy,
                )
            )
            p.append(
                text(
                    cx0 + half / 2,
                    y + h / 2 + 12,
                    "[logo — supply asset]",
                    7,
                    anchor="middle",
                    fill=GREY,
                    policy=policy,
                )
            )
    p.append("</g>")
    return "\n".join(p)


def legend(x, y, items, policy=None) -> str:
    """A legend box. ``items`` is a list of ``(glyph_fn, text)`` where
    ``glyph_fn(x, y)`` returns SVG for a small sample glyph."""
    p = [
        '<g font-family="Helvetica, Arial, sans-serif">',
        rect(x, y, 300, 20 + 18 * len(items), 0.8, policy=policy),
        text(x + 10, y + 15, "LEGEND", 11, weight="bold", policy=policy),
    ]
    yy = y + 34
    for glyph, label in items:
        p.append(glyph(x + 12, yy - 6))
        p.append(text(x + 44, yy, label, 8.5, policy=policy))
        yy += 18
    p.append("</g>")
    return "\n".join(p)


def notes_block(x, y, notes, policy=None) -> str:
    rows = []
    for i, n in enumerate(notes, 1):
        wrapped = wrap(f"{i}. {n}", 92)
        for j, ln in enumerate(wrapped):
            rows.append(("    " + ln if j else ln))
    p = [
        '<g font-family="Helvetica, Arial, sans-serif">',
        rect(x, y, 470, 26 + 15 * len(rows), 0.8, policy=policy),
        text(x + 10, y + 16, "NOTES", 11, weight="bold", policy=policy),
    ]
    yy = y + 34
    for ln in rows:
        p.append(text(x + 10, yy, ln, 8.4, policy=policy))
        yy += 15
    p.append("</g>")
    return "\n".join(p)


def sheet(
    inner_body,
    vb,
    meta,
    legend_items,
    notes,
    sheet_size=(1500, 950),
    watermark=None,
    policy=None,
) -> str:
    """Assemble the full ISO sheet around a nested, auto-scaled inner drawing.

    ``vb`` is ``[minx, miny, gw, gh]`` — the inner drawing's own viewBox, which
    is scaled to fit the sheet's drawing band. ``watermark`` (a status key such
    as ``CONCEPT``) stamps the diagonal status watermark the standard requires
    for P&IDs; pass ``None`` to omit it.
    """
    SW, SH = sheet_size
    ax, ay, aw, ah = 44, 96, SW - 88, SH - 96 - 300
    _, _, gw, gh = vb
    p = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {_num(policy, SW)} '
        f'{_num(policy, SH)}" font-family="Helvetica, Arial, sans-serif">',
        f'<rect x="0" y="0" width="{_num(policy, SW)}" height="{_num(policy, SH)}" '
        'fill="white"/>',
        rect(10, 10, SW - 20, SH - 20, 0.5, policy=policy),
        rect(34, 34, SW - 68, SH - 68, 1.4, policy=policy),
        text(SW / 2, 66, meta.get("title", ""), 15, anchor="middle", weight="bold", policy=policy),
        text(SW / 2, 84, meta.get("subtitle", ""), 10, anchor="middle", fill=GREY, policy=policy),
        f'<svg x="{_num(policy, ax)}" y="{_num(policy, ay)}" '
        f'width="{_num(policy, aw)}" height="{_num(policy, ah)}" '
        f'viewBox="0 0 {_num(policy, gw)} {_num(policy, gh)}" '
        'preserveAspectRatio="xMidYMin meet">',
        inner_body,
        "</svg>",
        notes_block(46, SH - 292, notes, policy=policy),
        legend(540, SH - 292, legend_items, policy=policy),
    ]
    if watermark:
        p.append(svg_status_watermark(SW, SH, watermark, policy=policy))
    tbx, tby = SW - 34 - 8 - 500, SH - 34 - 8 - 96
    p.append(logo_strip(tbx, tby - 52, 500, meta, policy=policy))
    p.append(titleblock(tbx, tby, meta, policy=policy))
    p.append("</svg>")
    return "\n".join(p)
