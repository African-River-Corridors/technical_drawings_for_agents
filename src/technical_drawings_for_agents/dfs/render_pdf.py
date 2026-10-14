"""PDF from normalised content, written directly so that the font is embedded and the bytes repeat.

One page, the trimmed sheet. The page space is millimetres, y down, through one ``cm`` matrix.
Text uses the embedded font (clause 11.6.4) as a simple TrueType font in WinAnsi encoding.
"""

from __future__ import annotations

import math
import zlib

from . import resources, rules

PT_PER_MM = 72 / 25.4
# Values of the font file (a test compares them with the file): head, hhea and OS/2 tables.
FONT_NAME = "BarlowSemiCondensed-Regular"
FONT_BBOX = (-592, -212, 1047, 1079)
FONT_ASCENT, FONT_DESCENT, FONT_CAP_HEIGHT, FONT_STEM_V = 1000, -200, 700, 80


def _n(value: float) -> str:
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def _stream(data: bytes, extra: str = "") -> bytes:
    packed = zlib.compress(data, 9)
    head = f"<< /Length {len(packed)} /Filter /FlateDecode {extra}>>".encode()
    return head + b"\nstream\n" + packed + b"\nendstream"


def _widths() -> list[int]:
    advance = resources.font_widths()["advance"]
    widths = [0] * 224  # codes 32 to 255
    for ch, value in advance.items():
        widths[ch.encode("cp1252")[0] - 32] = value
    return widths


def _hex(text: str) -> str:
    return "<" + text.encode("cp1252").hex().upper() + ">"


def _arc_ops(e) -> list[str]:
    cx, cy = e["centre_mm"]
    r = e["radius_mm"]
    start = math.radians(e["start_deg"])
    sweep = math.radians(e["sweep_deg"])
    pieces = max(1, math.ceil(sweep / (math.pi / 2) - 1e-9))
    step = sweep / pieces
    k = 4 / 3 * math.tan(step / 4)

    def p(a):
        return cx + r * math.cos(a), cy - r * math.sin(a)

    def tangent(a):
        return -math.sin(a), -math.cos(a)

    x, y = p(start)
    ops = [f"{_n(x)} {_n(y)} m"]
    for i in range(pieces):
        a0, a1 = start + i * step, start + (i + 1) * step
        (x0, y0), (x1, y1) = p(a0), p(a1)
        (tx0, ty0), (tx1, ty1) = tangent(a0), tangent(a1)
        ops.append(
            f"{_n(x0 + k * r * tx0)} {_n(y0 + k * r * ty0)} {_n(x1 - k * r * tx1)} {_n(y1 - k * r * ty1)} {_n(x1)} {_n(y1)} c"
        )
    return ops


def _circle_ops(e) -> list[str]:
    cx, cy = e["centre_mm"]
    r = e["radius_mm"]
    k = 0.5522847498307936 * r
    return [
        f"{_n(cx + r)} {_n(cy)} m",
        f"{_n(cx + r)} {_n(cy - k)} {_n(cx + k)} {_n(cy - r)} {_n(cx)} {_n(cy - r)} c",
        f"{_n(cx - k)} {_n(cy - r)} {_n(cx - r)} {_n(cy - k)} {_n(cx - r)} {_n(cy)} c",
        f"{_n(cx - r)} {_n(cy + k)} {_n(cx - k)} {_n(cy + r)} {_n(cx)} {_n(cy + r)} c",
        f"{_n(cx + k)} {_n(cy + r)} {_n(cx + r)} {_n(cy + k)} {_n(cx + r)} {_n(cy)} c",
        "h",
    ]


def _loop_ops(points) -> list[str]:
    ops = [f"{_n(points[0][0])} {_n(points[0][1])} m"]
    ops += [f"{_n(x)} {_n(y)} l" for x, y in points[1:]]
    ops.append("h")
    return ops


def _content(norm: dict) -> bytes:
    sheet = norm["sheet"]
    out = [f"{_n(PT_PER_MM)} 0 0 {_n(-PT_PER_MM)} 0 {_n(sheet['height_mm'] * PT_PER_MM)} cm", "0 G 0 g", "0 J 0 j 10 M"]
    for e in norm["entities"]:
        t = e["type"]
        if t == "text":
            h = e["height_mm"]
            corners = rules.text_box(e["anchor_mm"], e["text"], h, e["rotation_deg"], e["align_h"], e["align_v"])
            c, s = (math.cos(math.radians(e["rotation_deg"])), math.sin(math.radians(e["rotation_deg"])))
            c, s = (round(c, 12), round(s, 12))
            x0, y0 = corners[0]
            size = h * 1000 / rules.CAP_HEIGHT_UNITS
            out.append(f"BT /F1 {_n(size)} Tf {_n(c)} {_n(-s)} {_n(-s)} {_n(-c)} {_n(x0)} {_n(y0)} Tm {_hex(e['text'])} Tj ET")
            continue
        if t == "hatch" and e["pattern"] == "solid":
            ops = [op for loop in [e["boundary_mm"], *e["holes_mm"]] for op in _loop_ops(loop)]
            out += ops + ["f*"]
            continue
        out.append(f"{_n(e['width_mm'])} w")
        line_type = e.get("line_type", "continuous")
        if t == "hatch" or line_type == "continuous":  # marks of the other patterns are not defined yet (ticket 27): the outline
            out.append("[] 0 d")
        else:
            pattern = [abs(v) for v in rules.line_pattern(line_type, e["width_mm"])]
            out.append("[" + " ".join(_n(v) for v in pattern) + "] 0 d")
        if t == "line":
            (x1, y1), (x2, y2) = e["from_mm"], e["to_mm"]
            out += [f"{_n(x1)} {_n(y1)} m", f"{_n(x2)} {_n(y2)} l", "S"]
        elif t == "polyline":
            ops = _loop_ops(e["points_mm"])
            if not e["closed"]:
                ops = ops[:-1]
            out += ops + ["S"]
        elif t == "circle":
            out += _circle_ops(e) + ["S"]
        elif t == "arc":
            out += _arc_ops(e) + ["S"]
        elif t == "hatch":
            out += [op for loop in [e["boundary_mm"], *e["holes_mm"]] for op in _loop_ops(loop)] + ["S"]
    return "\n".join(out).encode("ascii")


def pdf_from_normalised(norm: dict) -> bytes:
    sheet = norm["sheet"]
    wpt, hpt = sheet["width_mm"] * PT_PER_MM, sheet["height_mm"] * PT_PER_MM
    font_bytes = resources.FONT_FILE.read_bytes()
    widths = " ".join(str(w) for w in _widths())
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {_n(wpt)} {_n(hpt)}] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>".encode(),
        _stream(_content(norm)),
        (
            f"<< /Type /Font /Subtype /TrueType /BaseFont /{FONT_NAME} /FirstChar 32 /LastChar 255 "
            f"/Widths [{widths}] /Encoding /WinAnsiEncoding /FontDescriptor 6 0 R >>"
        ).encode(),
        (
            f"<< /Type /FontDescriptor /FontName /{FONT_NAME} /Flags 32 /FontBBox [{' '.join(str(v) for v in FONT_BBOX)}] "
            f"/ItalicAngle 0 /Ascent {FONT_ASCENT} /Descent {FONT_DESCENT} /CapHeight {FONT_CAP_HEIGHT} /StemV {FONT_STEM_V} /FontFile2 7 0 R >>"
        ).encode(),
        _stream(font_bytes, f"/Length1 {len(font_bytes)} "),
    ]
    out = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode() + b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)
