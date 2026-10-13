"""Worked example — a concrete-lined drainage channel typical section.

Demonstrates the ``technical_drawings_for_agents`` toolkit end-to-end:
  * real-metre geometry via ViewBox (scale-true)
  * concrete hatch, dimension lines, centreline, scale bar, title block
  * the CONCEPT — NOT FOR CONSTRUCTION status watermark
  * an ezdxf DXF export of the same geometry in real metres

Geometry is a trapezoidal channel with a uniform concrete lining. ALL numbers
come from ``inputs.yaml`` and are NOMINAL / ILLUSTRATIVE concept values — the
generator never invents a dimension (see the provenance note in inputs.yaml).
Run directly (``python source.py``) or via ``technical_drawings_for_agents render source.py``.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from technical_drawings_for_agents import (
    Drawing,
    DrawingMeta,
    DxfBuilder,
    ViewBox,
    svg_centerline,
    svg_dimension_h,
    svg_dimension_v,
    svg_hatch,
    svg_leader,
    svg_line,
    svg_pattern_defs,
    svg_scale_bar,
)
from technical_drawings_for_agents.style import COL_CAD_BG, COL_CAD_OUTLINE, COL_CAD_WATER

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"


def load_inputs() -> dict:
    with open(HERE / "inputs.yaml", "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def channel_profiles(cfg: dict):
    """Return (inner_profile, concrete_polygon) point lists in real metres.

    Coordinate frame: x horizontal (0 = channel centreline), y vertical
    (0 = invert / channel bed, +y up).
    """
    b = cfg["bed_width_m"]
    d = cfg["depth_m"]
    s = cfg["side_slope_h_to_v"]
    t = cfg["lining_thickness_m"]

    half_bed = b / 2.0
    top_half = half_bed + s * d  # top half-width of the open channel

    # Inner (open channel) profile, left top -> down -> across -> up -> right top.
    il_top = (-top_half, d)
    il_bot = (-half_bed, 0.0)
    ir_bot = (half_bed, 0.0)
    ir_top = (top_half, d)
    inner = [il_top, il_bot, ir_bot, ir_top]

    # Outer (back face of concrete) profile, offset outward by the lining t.
    ol_top = (-top_half - t, d)
    ol_bot = (-half_bed - t, -t)
    or_bot = (half_bed + t, -t)
    or_top = (top_half + t, d)

    # Concrete annulus as one closed polygon: outer perimeter, then back along
    # the inner perimeter (the channel is open at the top).
    concrete = [ol_top, ol_bot, or_bot, or_top, ir_top, ir_bot, il_bot, il_top]
    return inner, concrete


def build_svg(meta: DrawingMeta, cfg: dict, policy=None, provenance_stamp=None) -> str:
    b = cfg["bed_width_m"]
    d = cfg["depth_m"]
    s = cfg["side_slope_h_to_v"]
    t = cfg["lining_thickness_m"]
    fb = cfg["freeboard_m"]

    inner, concrete = channel_profiles(cfg)
    top_half = b / 2.0 + s * d

    width, height = 920, 580
    real_min_x = -(top_half + t) - 0.6
    real_max_x = (top_half + t) + 0.6
    real_min_y = -t - 0.5
    real_max_y = d + 0.7
    vb = ViewBox(real_min_x, real_max_x, real_min_y, real_max_y, width, height, padding=90)

    dwg = Drawing(
        width=width,
        height=height,
        viewbox=vb,
        title_block=meta.title_block(),
        status=meta.status_key,
        background=COL_CAD_BG,
        policy=policy,
        provenance_stamp=provenance_stamp,
    )
    dwg.add_defs(svg_pattern_defs(vb, patterns="concrete", policy=policy))

    # Concrete lining (hatched) then the open channel outline.
    dwg.add(
        svg_hatch(
            vb,
            concrete,
            pattern="concrete",
            stroke=COL_CAD_OUTLINE,
            stroke_width=1.2,
            policy=policy,
        )
    )
    # Inner channel outline (open at top, so just the wetted profile).
    inner_px = [vb.point(x, y) for x, y in inner]
    for (x1, y1), (x2, y2) in zip(inner_px, inner_px[1:]):
        dwg.add(svg_line(x1, y1, x2, y2, stroke=COL_CAD_OUTLINE, stroke_width=1.8, policy=policy))

    # Design water level (nominal) — freeboard below top of lining.
    wl = d - fb
    dwg.add(
        svg_centerline(vb, real_min_x + 0.5, wl, real_max_x - 0.5, wl,
                       stroke=COL_CAD_WATER, dash="6,4", policy=policy)
    )
    wlx, wly = vb.point(real_max_x - 0.5, wl)
    dwg.add(
        svg_leader(
            vb,
            target=(b / 2.0 + s * (d - fb), wl),
            elbow=(top_half + t + 0.2, wl),
            label_point=(top_half + t + 0.35, wl + 0.15),
            label=f"Design WL (nominal)\nfreeboard {fb:g} m",
            anchor="end",
            stroke=COL_CAD_WATER,
            policy=policy,
        )
    )

    # Centreline of the channel.
    dwg.add(svg_centerline(vb, 0, -t - 0.3, 0, d + 0.4, extension_m=0, policy=policy))

    # Dimensions — bed width, depth, lining thickness.
    dwg.add(
        svg_dimension_h(
            vb,
            0.0,
            -b / 2.0,
            b / 2.0,
            f"BED {b:g} m",
            offset_px=42,
            policy=policy,
        )
    )
    dwg.add(
        svg_dimension_v(
            vb,
            top_half + t,
            0.0,
            d,
            f"DEPTH {d:g} m",
            offset_px=30,
            policy=policy,
        )
    )
    dwg.add(
        svg_leader(
            vb,
            target=(-(b / 2.0) - t / 2.0, -t / 2.0),
            elbow=(-top_half - 0.4, -t - 0.2),
            label_point=(-top_half - 0.55, -t - 0.2),
            label=f"Concrete lining\n{t:g} m (nom.)",
            anchor="end",
            policy=policy,
        )
    )
    # Side-slope call-out.
    dwg.add(
        svg_leader(
            vb,
            target=(-(b / 2.0 + s * d / 2.0), d / 2.0),
            elbow=(-top_half - 0.4, d / 2.0),
            label_point=(-top_half - 0.55, d / 2.0),
            label=f"{s:g}H:1V",
            anchor="end",
            policy=policy,
        )
    )

    # Scale bar (true to scale) anchored bottom-left inside the border.
    dwg.add(svg_scale_bar(vb, real_min_x + 0.4, real_min_y + 0.15, 1.0, divisions=4, unit="m",
                          label="Scale (concept)", policy=policy))

    return dwg.render()


def build_dxf(cfg: dict, policy=None, provenance_stamp=None) -> DxfBuilder:
    b = cfg["bed_width_m"]
    d = cfg["depth_m"]
    inner, concrete = channel_profiles(cfg)
    dxf = DxfBuilder(dxfversion="R2018", units="m", policy=policy)
    dxf.polyline([(x, y) for x, y in concrete], closed=True, layer="OUTLINE")
    dxf.polyline([(x, y) for x, y in inner], closed=False, layer="OBJECT")
    # Native linear dimension for the bed width, in real metres.
    dxf.linear_dim((-b / 2.0, 0.0), (b / 2.0, 0.0), distance=-0.4, layer="DIMENSIONS")
    dxf.text((0.0, d + 0.3), "CONCEPT - NOT FOR CONSTRUCTION", height=0.12, layer="WATERMARK")
    if provenance_stamp:
        dxf.provenance_stamp(provenance_stamp, (-b / 2.0, d + 0.5), height=0.12)
    return dxf


def main() -> None:
    cfg_all = load_inputs()
    cfg = cfg_all["channel"]
    meta = DrawingMeta.load(HERE / "meta.yaml")

    OUT.mkdir(parents=True, exist_ok=True)
    stem = meta.number

    svg = build_svg(meta, cfg)
    (OUT / f"{stem}.svg").write_text(svg, encoding="utf-8")

    dxf = build_dxf(cfg)
    dxf.save(OUT / f"{stem}.dxf")

    print(f"wrote {OUT / f'{stem}.svg'}")
    print(f"wrote {OUT / f'{stem}.dxf'}")


if __name__ == "__main__":
    main()
