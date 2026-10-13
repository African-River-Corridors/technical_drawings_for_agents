"""Hydraulic long-section (elevation vs chainage) — the *profile* view of a cascade.

Reads the same data model as :mod:`technical_drawings_for_agents.bfd`; uses nodes that carry ``elev``
(m a.s.l.) and ``chainage`` (m from source). Produces a monochrome ISO sheet with an
elevation/chainage grid, the pipeline profile line, and station/pond markers.
"""
from __future__ import annotations

from pathlib import Path

from . import bfd as B


def build_profile(data: dict, out_dir: Path, stem: str) -> Path:
    meta = dict(data["meta"])
    meta["doctype"] = "HYDRAULIC LONG-SECTION"
    pts = [n for n in data["nodes"] if "elev" in n and "chainage" in n]
    pts.sort(key=lambda n: n["chainage"])
    SW, SH = 1500, 950
    # plot area
    px0, py0, pw, ph = 130, 150, SW - 260, 480
    chs = [n["chainage"] for n in pts]
    els = [n["elev"] for n in pts]
    cmin, cmax = min(chs), max(chs)
    emin, emax = min(els) - 5, max(els) + 12

    def X(c):
        return px0 + (c - cmin) / (cmax - cmin) * pw

    def Y(e):
        return py0 + ph - (e - emin) / (emax - emin) * ph

    p = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {SW} {SH}" font-family="Helvetica, Arial, sans-serif">',
         f'<rect x="0" y="0" width="{SW}" height="{SH}" fill="white"/>',
         B._rect(10, 10, SW - 20, SH - 20, 0.5), B._rect(34, 34, SW - 68, SH - 68, 1.4),
         B._t(SW / 2, 66, meta.get("title", "") + " — HYDRAULIC LONG-SECTION", 15, anchor="middle", weight="bold"),
         B._t(SW / 2, 84, "Elevation vs chainage · NTS horizontal · static head shown to scale", 10, anchor="middle", fill=B.GREY)]

    # axes + gridlines
    p.append(f'<line x1="{px0}" y1="{py0}" x2="{px0}" y2="{py0+ph}" stroke="black" stroke-width="1"/>')
    p.append(f'<line x1="{px0}" y1="{py0+ph}" x2="{px0+pw}" y2="{py0+ph}" stroke="black" stroke-width="1"/>')
    e = int(emin // 10 * 10)
    while e <= emax:
        y = Y(e)
        p.append(f'<line x1="{px0}" y1="{y}" x2="{px0+pw}" y2="{y}" stroke="#ccc" stroke-width="0.5"/>')
        p.append(B._t(px0 - 8, y + 3, f"{e}", 8, anchor="end"))
        e += 10
    p.append(B._t(px0 - 8, py0 - 8, "m a.s.l.", 8, anchor="end", fill=B.GREY))
    for c in range(0, int(cmax) + 1, 2000):
        x = X(c)
        p.append(f'<line x1="{x}" y1="{py0+ph}" x2="{x}" y2="{py0+ph+5}" stroke="black" stroke-width="0.6"/>')
        p.append(B._t(x, py0 + ph + 18, f"{c/1000:.0f} km", 8, anchor="middle"))

    # profile line through stations
    d = "M" + " L".join(f"{X(n['chainage'])},{Y(n['elev'])}" for n in pts)
    p.append(f'<path d="{d}" fill="none" stroke="black" stroke-width="2"/>')

    # markers + labels (alternate above/below to avoid collisions)
    for i, n in enumerate(pts):
        x, y = X(n["chainage"]), Y(n["elev"])
        p.append(f'<circle cx="{x}" cy="{y}" r="4" fill="white" stroke="black" stroke-width="1.2"/>')
        above = i % 2 == 0
        ly = y - 14 if above else y + 22
        lab = n.get("label", n["id"]).split("\\n")[0]  # first line only — keep it short
        # keep end labels inside the frame
        anchor = "end" if x > px0 + pw - 120 else ("start" if x < px0 + 120 else "middle")
        p.append(B._t(x, ly, lab, 8, anchor=anchor, weight="bold"))
        p.append(B._t(x, ly + 11, f"{n['elev']} m · {n['chainage']/1000:.2f} km", 7.5, anchor=anchor, fill=B.GREY))

    # notes + title block
    p.append(B._notes(46, SH - 292, data.get("profile_notes", data.get("notes", []))[:6]))
    p.append(B._iso_titleblock(SW - 546, SH - 292 + 184, meta))
    p.append("</svg>")
    out = out_dir / f"{stem}.svg"
    out.write_text("\n".join(p), encoding="utf-8", newline="\n")
    return out
