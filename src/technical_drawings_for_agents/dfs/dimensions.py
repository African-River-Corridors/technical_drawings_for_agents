"""Dimensions and leaders (clause 12): computed from the geometry that they point at."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from decimal import ROUND_HALF_EVEN, Decimal

from .entities import _arc, _hatch, _line, _text
from .numbers import cos_sin
from .rules import arc_sweep, text_box


@dataclass
class Context:
    mapper: object  # rules.Mapper
    rotation: float
    unit_mm: float  # the hatch unit u (10.5.1)
    width: float  # width of the role of the entity (8.7)
    origin: str  # viewport.<id>
    index: dict = field(default_factory=dict)  # entity id -> entity of the viewport


@dataclass
class Result:
    entities: list = field(default_factory=list)
    errors: set = field(default_factory=set)
    chain: dict | None = None


def _left(v):
    return (v[1], -v[0])


def _add(a, b, k=1.0):
    return (a[0] + k * b[0], a[1] + k * b[1])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1]


def _unit(v):
    n = math.hypot(*v)
    return (v[0] / n, v[1] / n)


def _visual_angle(v) -> float:
    return math.degrees(math.atan2(-v[1], v[0])) % 360.0


def reading_rotation(v) -> float:
    """12.3.3."""
    theta = _visual_angle(v)
    return theta if (theta <= 90 or theta > 270) else (theta - 180) % 360.0


def format_value(value: float, decimals: int) -> str:
    q = Decimal(value).quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_EVEN)
    text = format(q, "f")
    return text[1:] if text.startswith("-") and Decimal(text) == 0 else text


def model_point(spec, ctx):
    """12.2: the model point of a point spec, or None when the reference is wrong."""
    if "point_m" in spec:
        return tuple(spec["point_m"])
    e = ctx.index.get(spec["ref"])
    if e is None:
        return None
    at, t = spec["at"], e["type"]
    if t in ("line", "centre_line") and at in ("start", "end"):
        return tuple(e["from_m"] if at == "start" else e["to_m"])
    if t == "polyline" and at.startswith("vertex:"):
        i = int(at.split(":")[1])
        return tuple(e["points_m"][i]) if i < len(e["points_m"]) else None
    if t == "circle" and at == "centre":
        return tuple(e["centre_m"])
    if t == "arc":
        c, r = e["centre_m"], e["radius_m"]
        if at == "centre":
            return tuple(c)
        if at in ("start", "end"):
            a = math.radians(e["start_deg"] if at == "start" else e["end_deg"])
            return (c[0] + r * math.cos(a), c[1] + r * math.sin(a))
    return None


def _arrow(origin, layer, tip, e, ctx):
    u = ctx.unit_mm
    a = 2 * u
    n = _left(e)
    base = _add(tip, e, -a)
    return _hatch(origin, layer, [tip, _add(base, n, u / 4), _add(base, n, -u / 4)], [], "solid", 0, ctx.width)


def _dim_text(origin, layer, text, anchor_mid, v, height, ctx):
    rho = reading_rotation(v)
    c, s = cos_sin(rho)
    pos = _add(anchor_mid, (-s, -c), ctx.unit_mm / 2)
    return _text(origin, layer, pos, text, height, rho, "centre", "baseline")


def _parts(layer, origin, ctx):
    return lambda a, b: _line(origin, layer, a, b, "continuous", ctx.width)


def dimension(e, ctx: Context) -> Result:
    res = Result()
    layer = e["layer"]
    origin = ctx.origin + ".dimension"
    u = ctx.unit_mm
    a = 2 * u
    kind = e["kind"]
    unit = e.get("unit", "m")
    decimals = e.get("decimals", 1 if kind == "angle" else (2 if unit == "m" else 0))
    height = e.get("text_height_mm", 3.5)
    factor = 1000 if unit == "mm" else 1
    mp = ctx.mapper
    line = _parts(layer, origin, ctx)

    if kind in ("linear", "aligned"):
        m1, m2 = model_point(e["from"], ctx), model_point(e["to"], ctx)
        if m1 is None or m2 is None:
            res.errors.add("12.2")
            return res
        if kind == "linear":
            axis = 0 if e["orientation"] == "horizontal" else 1
            value = abs(m2[axis] - m1[axis])
        else:
            value = math.hypot(m2[0] - m1[0], m2[1] - m1[1])
        if value == 0:
            res.errors.add("12.4.1")
            return res
        p1, p2 = mp.point(m1), mp.point(m2)
        if kind == "linear":
            cr, sr = cos_sin(ctx.rotation)
            d = (cr, -sr) if e["orientation"] == "horizontal" else (-sr, -cr)
        else:
            d = _unit((p2[0] - p1[0], p2[1] - p1[1]))
        if _dot((p2[0] - p1[0], p2[1] - p1[1]), d) < 0:
            d = (-d[0], -d[1])
        left = _left(d)
        ref = _add(((p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2), left, e["offset_mm"])
        s1, s2 = _dot((ref[0] - p1[0], ref[1] - p1[1]), left), _dot((ref[0] - p2[0], ref[1] - p2[1]), left)
        q1, q2 = _add(p1, left, s1), _add(p2, left, s2)
        if abs(s1) <= u / 2 or abs(s2) <= u / 2 or math.hypot(q2[0] - q1[0], q2[1] - q1[1]) < 2 * a:
            res.errors.add("12.4.3")
            return res
        for p, q, s in ((p1, q1, s1), (p2, q2, s2)):
            v = left if s > 0 else (-left[0], -left[1])
            res.entities.append(line(_add(p, v, u / 2), _add(q, v, u)))
        res.entities.append(line(q1, q2))
        res.entities.append(_arrow(origin, layer, q1, (-d[0], -d[1]), ctx))
        res.entities.append(_arrow(origin, layer, q2, d, ctx))
        text = format_value(value * factor, decimals)
        mid = ((q1[0] + q2[0]) / 2, (q1[1] + q2[1]) / 2)
        res.entities.append(_dim_text(origin, layer, text, mid, d, height, ctx))
    elif kind in ("radius", "diameter"):
        target = ctx.index.get(e["of"])
        if target is None or target["type"] not in ("circle", "arc"):
            res.errors.add("12.2")
            return res
        phi_model = e.get("direction_deg", 45)
        if target["type"] == "arc":
            sweep = arc_sweep(target["start_deg"], target["end_deg"])
            if sweep is None or (phi_model - target["start_deg"]) % 360.0 > sweep:
                res.errors.add("12.5.1")
                return res
        c = mp.point(target["centre_m"])
        radius = target["radius_m"] * mp.k
        cphi, sphi = cos_sin(phi_model + ctx.rotation)
        ev = (cphi, -sphi)
        value = target["radius_m"] * (1 if kind == "radius" else 2)
        text = ("R" if kind == "radius" else "Ø") + format_value(value * factor, decimals)
        if kind == "radius":
            if radius < 2 * a:
                res.errors.add("12.4.3")
                return res
            tip = _add(c, ev, radius)
            res.entities.append(line(c, tip))
            res.entities.append(_arrow(origin, layer, tip, ev, ctx))
            res.entities.append(_dim_text(origin, layer, text, _add(c, ev, radius / 2), ev, height, ctx))
        else:
            if radius < a:
                res.errors.add("12.4.3")
                return res
            t1, t2 = _add(c, ev, -radius), _add(c, ev, radius)
            res.entities.append(line(t1, t2))
            res.entities.append(_arrow(origin, layer, t1, (-ev[0], -ev[1]), ctx))
            res.entities.append(_arrow(origin, layer, t2, ev, ctx))
            res.entities.append(_dim_text(origin, layer, text, c, ev, height, ctx))
    else:  # angle
        mv, ma, mb = (model_point(e[k], ctx) for k in ("vertex", "from", "to"))
        if mv is None or ma is None or mb is None:
            res.errors.add("12.2")
            return res
        if ma == mv or mb == mv:
            res.errors.add("12.5.4")
            return res
        ta = math.degrees(math.atan2(ma[1] - mv[1], ma[0] - mv[0]))
        tb = math.degrees(math.atan2(mb[1] - mv[1], mb[0] - mv[0]))
        value = (tb - ta) % 360.0
        if value == 0:
            res.errors.add("12.5.4")
            return res
        rho = e["arc_radius_mm"]
        if rho < 2 * a:
            res.errors.add("12.4.3")
            return res
        v = mp.point(mv)
        aa, ab = ta + ctx.rotation, tb + ctx.rotation

        def w(alpha):
            c, s = cos_sin(alpha)
            return (c, -s)

        def tangent(alpha):
            c, s = cos_sin(alpha)
            return (-s, -c)

        for alpha in (aa, ab):
            res.entities.append(line(_add(v, w(alpha), u / 2), _add(v, w(alpha), rho + u)))
        res.entities.append(_arc(origin, layer, v, rho, aa, value, "continuous", ctx.width))
        wa, wb = w(aa), w(ab)
        ta_vec = tangent(aa)
        res.entities.append(_arrow(origin, layer, _add(v, wa, rho), (-ta_vec[0], -ta_vec[1]), ctx))
        res.entities.append(_arrow(origin, layer, _add(v, wb, rho), tangent(ab), ctx))
        text = format_value(value, decimals) + "°"
        res.entities.append(_text(origin, layer, _add(v, w(aa + value / 2), rho + height), text, height, 0, "centre", "middle"))

    if "chain" in e:
        res.chain = {"id": e["chain"], "role": e["chain_role"], "text": text, "unit": unit, "decimals": decimals}
    return res


def leader(e, ctx: Context) -> Result:
    res = Result()
    layer = e["layer"]
    origin = ctx.origin + ".leader"
    u = ctx.unit_mm
    m = model_point(e["from"], ctx)
    if m is None:
        res.errors.add("12.2")
        return res
    t = ctx.mapper.point(m)
    lp = ctx.mapper.point(e["label_at_m"])
    if t == lp:
        res.errors.add("12.6.1")
        return res
    res.entities.append(_line(origin, layer, lp, t, "continuous", ctx.width))
    res.entities.append(_arrow(origin, layer, t, _unit((t[0] - lp[0], t[1] - lp[1])), ctx))
    right = lp[0] >= t[0]
    anchor = (lp[0] + (u / 2 if right else -u / 2), lp[1])
    res.entities.append(_text(origin, layer, anchor, e["text"], e.get("text_height_mm", 3.5), 0, "left" if right else "right", "middle"))
    return res


def check_chains(infos: list[dict]) -> bool:
    """12.8: True when every chain is well formed and its printed numbers add up."""
    chains: dict[str, list[dict]] = {}
    for info in infos:
        chains.setdefault(info["id"], []).append(info)
    for items in chains.values():
        overall = [i for i in items if i["role"] == "overall"]
        parts = [i for i in items if i["role"] == "part"]
        if len(overall) != 1 or len(parts) < 2:
            return False
        if len({(i["unit"], i["decimals"]) for i in items}) != 1:
            return False
        if sum(Decimal(i["text"]) for i in parts) != Decimal(overall[0]["text"]):
            return False
    return True


def part_points(entity) -> list[tuple[float, float]]:
    """The points of a generated part that must lie inside the frame (7.7.1)."""
    t = entity["type"]
    if t == "line":
        return [tuple(entity["from_mm"]), tuple(entity["to_mm"])]
    if t == "hatch":
        return [tuple(p) for p in entity["boundary_mm"]]
    if t == "text":
        return [tuple(p) for p in text_box(entity["anchor_mm"], entity["text"], entity["height_mm"], entity["rotation_deg"], entity["align_h"], entity["align_v"])]
    if t == "arc":
        cx, cy = entity["centre_mm"]
        r, start, sweep = entity["radius_mm"], entity["start_deg"], entity["sweep_deg"]
        angles = [start, start + sweep] + [x for x in (0, 90, 180, 270) if (x - start) % 360.0 <= sweep]
        out = []
        for ang in angles:
            c, s = cos_sin(ang)
            out.append((cx + r * c, cy - r * s))
        return out
    return []
