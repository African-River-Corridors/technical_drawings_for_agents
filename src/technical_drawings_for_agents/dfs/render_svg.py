"""SVG from normalised content (clause 15; one SVG unit is one millimetre of paper).

Everything is drawn in black (8.6.1), or under the colour profile with the colour members of an
entity (8.6.6). Images are drawn first, embedded as JPEG data (9.10.7). The text is placed by the width table, so its place does not
depend on the machine; the font is embedded as a data URI when ``embed_font`` is true.
"""

from __future__ import annotations

import base64
from xml.sax.saxutils import escape

from . import resources, rules

FONT_FAMILY = "Barlow Semi Condensed"


def _n(value: float) -> str:
    text = f"{value:.3f}".rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def _dash(entity) -> str:
    if entity.get("line_type", "continuous") == "continuous":
        return ""
    pattern = [abs(v) for v in rules.line_pattern(entity["line_type"], entity["width_mm"])]
    return f' stroke-dasharray="{" ".join(_n(v) for v in pattern)}"'


def _colour(entity) -> str:
    return entity.get("print_colour", "#000")


def _stroke(entity) -> str:
    return f'fill="none" stroke="{_colour(entity)}" stroke-width="{_n(entity["width_mm"])}"{_dash(entity)}'


def _loop(points) -> str:
    return "M" + " L".join(f"{_n(x)} {_n(y)}" for x, y in points) + " Z"


def arc_path(entity) -> str:
    import math

    cx, cy = entity["centre_mm"]
    r = entity["radius_mm"]
    a0 = math.radians(entity["start_deg"])
    a1 = math.radians(entity["start_deg"] + entity["sweep_deg"])
    p0 = (cx + r * math.cos(a0), cy - r * math.sin(a0))
    p1 = (cx + r * math.cos(a1), cy - r * math.sin(a1))
    large = 1 if entity["sweep_deg"] > 180 else 0
    return f"M{_n(p0[0])} {_n(p0[1])} A{_n(r)} {_n(r)} 0 {large} 0 {_n(p1[0])} {_n(p1[1])}"


def _text(entity) -> str:
    h = entity["height_mm"]
    corners = rules.text_box(entity["anchor_mm"], entity["text"], h, entity["rotation_deg"], entity["align_h"], entity["align_v"])
    x0, y0 = corners[0]
    width = rules.text_width_exact(entity["text"], h)
    rot = entity["rotation_deg"]
    transform = f' transform="rotate({_n(-rot)} {_n(x0)} {_n(y0)})"' if rot else ""
    halo = ""
    if "halo_colour" in entity:  # 8.6.6: a stroke of 0.2 h, under the fill
        halo = f' stroke="{entity["halo_colour"]}" stroke-width="{_n(0.2 * h)}" stroke-linejoin="round" paint-order="stroke"'
    return (
        f'<text x="{_n(x0)}" y="{_n(y0)}" font-size="{_n(h * 1000 / rules.CAP_HEIGHT_UNITS)}" '
        f'textLength="{_n(width)}" lengthAdjust="spacing" fill="{_colour(entity)}"{halo}{transform}>{escape(entity["text"])}</text>'
    )


def _image(entity, attrs, images) -> str:
    x0, y0, x1, y1 = entity["box_mm"]
    data = images.get(entity["sha256"]) if images else None
    if data is None:
        raise ValueError(f"9.10: no bytes for the image {entity['sha256']}")
    href = "data:image/jpeg;base64," + base64.b64encode(data).decode("ascii")
    opacity = f' opacity="{_n(entity["opacity"])}"' if entity["opacity"] != 1 else ""
    return (f'<image {attrs} x="{_n(x0)}" y="{_n(y0)}" width="{_n(x1 - x0)}" height="{_n(y1 - y0)}" '
            f'preserveAspectRatio="none"{opacity} href="{href}"/>')


def svg_from_normalised(norm: dict, *, embed_font: bool = True, images: dict | None = None) -> str:
    sheet = norm["sheet"]
    w, h = sheet["width_mm"], sheet["height_mm"]
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{_n(w)}mm" height="{_n(h)}mm" viewBox="0 0 {_n(w)} {_n(h)}">',
    ]
    style = f"text{{font-family:'{FONT_FAMILY}',sans-serif}}"
    if embed_font:
        data = base64.b64encode(resources.FONT_FILE.read_bytes()).decode("ascii")
        style = f"@font-face{{font-family:'{FONT_FAMILY}';src:url(data:font/ttf;base64,{data}) format('truetype')}}" + style
    parts.append(f"<style>{style}</style>")
    ordered = [e for e in norm["entities"] if e["type"] == "image"] + [e for e in norm["entities"] if e["type"] != "image"]
    for e in ordered:
        t = e["type"]
        attrs = f'data-origin="{e["origin"]}" data-layer="{e["layer"]}"'
        if t == "image":
            parts.append(_image(e, attrs, images))
        elif t == "line":
            (x1, y1), (x2, y2) = e["from_mm"], e["to_mm"]
            parts.append(f'<line {attrs} x1="{_n(x1)}" y1="{_n(y1)}" x2="{_n(x2)}" y2="{_n(y2)}" {_stroke(e)}/>')
        elif t == "polyline":
            d = _loop(e["points_mm"]) if e["closed"] else "M" + " L".join(f"{_n(x)} {_n(y)}" for x, y in e["points_mm"])
            parts.append(f'<path {attrs} d="{d}" {_stroke(e)}/>')
        elif t == "circle":
            cx, cy = e["centre_mm"]
            parts.append(f'<circle {attrs} cx="{_n(cx)}" cy="{_n(cy)}" r="{_n(e["radius_mm"])}" {_stroke(e)}/>')
        elif t == "arc":
            parts.append(f'<path {attrs} d="{arc_path(e)}" {_stroke(e)}/>')
        elif t == "hatch":
            d = " ".join(_loop(loop) for loop in [e["boundary_mm"], *e["holes_mm"]])
            if e["pattern"] == "solid":
                op = f' fill-opacity="{_n(e["fill_opacity"])}"' if "fill_opacity" in e else ""
                parts.append(f'<path {attrs} d="{d}" fill="{_colour(e)}"{op} fill-rule="evenodd" stroke="none"/>')
            # the other patterns draw only their marks, which are entities of their own (10.5.8)

        elif t == "text":
            parts.append(_text(e).replace("<text ", f"<text {attrs} ", 1))
    parts.append("</svg>")
    return "\n".join(parts) + "\n"
