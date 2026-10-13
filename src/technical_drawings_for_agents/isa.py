"""ISA-5.1 symbol library — pure, parametric symbol geometry.

Each symbol is a **function** that returns a :class:`Symbol`: an SVG group
anchored at a point, plus a set of **named connection ports** (the points where
lines attach) and their outward directions. Nothing here knows about sheets,
YAML, or P&IDs — it is a clean geometry library, reusable for single-line
electrical diagrams and other schematics later.

Conventions
-----------
* **Monochrome, black-on-white** (matches the ISO house sheet). No fills except
  where a symbol is genuinely solid.
* **Local coordinates** are centred on the anchor, in SVG orientation (**+y is
  down**). A symbol is placed with an SVG ``translate/rotate/scale`` transform;
  its ports are transformed in Python so they stay exact under rotation/scale.
* **Every symbol exposes the four edge ports** ``n`` / ``e`` / ``s`` / ``w``
  (compass, in sheet space after rotation) plus **semantic aliases** where they
  help (``suction`` / ``discharge`` on a pump, ``in`` / ``out`` on a valve).
  A P&ID line references a port as ``<equipment-id>.<port>``.

The library draws what it is told; it never invents a tag. Tags/designations
come from the data (the project's shared tag register), not from this code.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

# Monochrome print tokens (this drawing class is black-on-white).
BLACK = "#000000"
WHITE = "#ffffff"
GREY = "#555555"

# Compass unit vectors in SVG space (+y down).
_DIRV = {"N": (0.0, -1.0), "E": (1.0, 0.0), "S": (0.0, 1.0), "W": (-1.0, 0.0)}


# --------------------------------------------------------------------------
# Symbol value type
# --------------------------------------------------------------------------
@dataclass
class Symbol:
    """A placed ISA symbol: its SVG, its named ports, and its extent.

    ``ports`` maps a port name to an absolute ``(x, y)`` sheet coordinate;
    ``port_dirs`` maps the same name to a compass direction (``N/E/S/W``) the
    connecting line should leave in. ``bbox`` is ``(x0, y0, x1, y1)`` absolute.
    """

    svg: str
    ports: dict[str, tuple[float, float]] = field(default_factory=dict)
    port_dirs: dict[str, str] = field(default_factory=dict)
    bbox: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)

    def port(self, name: str) -> tuple[float, float]:
        if name not in self.ports:
            raise KeyError(f"no such port '{name}' (have: {', '.join(sorted(self.ports))})")
        return self.ports[name]


# --------------------------------------------------------------------------
# Low-level SVG emitters (local, black-on-white, Helvetica to match the sheet)
# --------------------------------------------------------------------------
def _esc(s) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _line(x1, y1, x2, y2, sw=1.4, dash=None):
    d = f' stroke-dasharray="{dash}"' if dash else ""
    return (
        f'<line x1="{x1:.2f}" y1="{y1:.2f}" x2="{x2:.2f}" y2="{y2:.2f}" '
        f'stroke="{BLACK}" stroke-width="{sw}"{d}/>'
    )


def _circle(cx, cy, r, fill=WHITE, sw=1.4):
    return (
        f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="{r:.2f}" '
        f'fill="{fill}" stroke="{BLACK}" stroke-width="{sw}"/>'
    )


def _rect(x, y, w, h, fill=WHITE, sw=1.4, dash=None, rx=0):
    d = f' stroke-dasharray="{dash}"' if dash else ""
    r = f' rx="{rx}" ry="{rx}"' if rx else ""
    return (
        f'<rect x="{x:.2f}" y="{y:.2f}" width="{w:.2f}" height="{h:.2f}" '
        f'fill="{fill}" stroke="{BLACK}" stroke-width="{sw}"{d}{r}/>'
    )


def _poly(points, fill=WHITE, sw=1.4, closed=True):
    pts = " ".join(f"{x:.2f},{y:.2f}" for x, y in points)
    tag = "polygon" if closed else "polyline"
    f = fill if closed else "none"
    return f'<{tag} points="{pts}" fill="{f}" stroke="{BLACK}" stroke-width="{sw}"/>'


def _path(d, fill=WHITE, sw=1.4):
    return f'<path d="{d}" fill="{fill}" stroke="{BLACK}" stroke-width="{sw}"/>'


def _text(x, y, s, size=9, anchor="middle", weight="normal", fill=BLACK):
    w = ' font-weight="bold"' if weight == "bold" else ""
    return (
        f'<text x="{x:.2f}" y="{y:.2f}" font-size="{size}" text-anchor="{anchor}" '
        f'font-family="Helvetica, Arial, sans-serif" fill="{fill}"{w} '
        f'dominant-baseline="middle">{_esc(s)}</text>'
    )


# --------------------------------------------------------------------------
# Transform helpers (place a local shape; map its ports to sheet space)
# --------------------------------------------------------------------------
def _group(body: str, x: float, y: float, rotation: float, scale: float) -> str:
    xf = [f"translate({x:.2f},{y:.2f})"]
    if rotation:
        xf.append(f"rotate({rotation:g})")
    if scale != 1.0:
        xf.append(f"scale({scale:g})")
    return f'<g transform="{" ".join(xf)}">{body}</g>'


def _map(lx: float, ly: float, x: float, y: float, rotation: float, scale: float):
    a = math.radians(rotation)
    rx = (lx * math.cos(a) - ly * math.sin(a)) * scale
    ry = (lx * math.sin(a) + ly * math.cos(a)) * scale
    return (x + rx, y + ry)


def _rot_dir(direction: str, rotation: float) -> str:
    vx, vy = _DIRV[direction]
    a = math.radians(rotation)
    rx = vx * math.cos(a) - vy * math.sin(a)
    ry = vx * math.sin(a) + vy * math.cos(a)
    # snap to the nearest compass point
    best, bestdot = "E", -2.0
    for name, (dx, dy) in _DIRV.items():
        dot = rx * dx + ry * dy
        if dot > bestdot:
            best, bestdot = name, dot
    return best


def _finish(body, local_ports, x, y, rotation, scale, extent):
    """Assemble a :class:`Symbol` from local geometry + local ports.

    ``local_ports`` is ``{name: (lx, ly, dir)}``. ``extent`` is the local
    half-extent used to compute an (approximate) axis-aligned bbox.
    """
    ports: dict[str, tuple[float, float]] = {}
    dirs: dict[str, str] = {}
    for name, (lx, ly, d) in local_ports.items():
        ports[name] = _map(lx, ly, x, y, rotation, scale)
        dirs[name] = _rot_dir(d, rotation)
    ex = extent * scale
    bbox = (x - ex, y - ex, x + ex, y + ex)
    return Symbol(svg=_group(body, x, y, rotation, scale), ports=ports, port_dirs=dirs, bbox=bbox)


def _edge_ports(hw: float, hh: float, extra: dict | None = None) -> dict:
    """The four compass edge ports for a symbol half-width/half-height."""
    ports = {
        "n": (0.0, -hh, "N"),
        "e": (hw, 0.0, "E"),
        "s": (0.0, hh, "S"),
        "w": (-hw, 0.0, "W"),
    }
    if extra:
        ports.update(extra)
    return ports


# --------------------------------------------------------------------------
# Symbols
# --------------------------------------------------------------------------
def pump(x, y, scale=1.0, rotation=0.0, **_) -> Symbol:
    """Centrifugal pump — a circle with an inscribed triangle (apex = discharge).

    The triangle apex points E, so ``suction`` is W (the flat back) and
    ``discharge`` is E (the apex) — a horizontal train reads left-to-right. Use
    ``rotation`` to re-aim (e.g. ``rotation: -90`` for a top discharge). Ports:
    ``suction`` / ``discharge`` with ``in`` / ``out`` aliases, plus ``n/e/s/w``.
    """
    r = 16.0
    tri = [(-r * 0.55, -r * 0.6), (-r * 0.55, r * 0.6), (r * 0.75, 0.0)]
    body = _circle(0, 0, r) + _poly(tri, fill=WHITE)
    ports = _edge_ports(
        r,
        r,
        {
            "suction": (-r, 0.0, "W"),
            "discharge": (r, 0.0, "E"),
            "in": (-r, 0.0, "W"),
            "out": (r, 0.0, "E"),
        },
    )
    return _finish(body, ports, x, y, rotation, scale, r)


_VALVE_KINDS = {"gate", "globe", "butterfly", "check"}
_ACTUATORS = {None, "none", "manual", "diaphragm", "motor", "solenoid"}


def valve(x, y, scale=1.0, rotation=0.0, kind="gate", actuator=None, **_) -> Symbol:
    """A bow-tie valve body. ``kind`` = gate / globe / butterfly / check.

    Optional ``actuator`` = manual / diaphragm / motor / solenoid draws the
    actuator on the valve stem (north). Ports: ``in`` (W) / ``out`` (E) + n/e/s/w.
    """
    if kind not in _VALVE_KINDS:
        raise ValueError(f"unknown valve kind '{kind}' (one of {sorted(_VALVE_KINDS)})")
    if actuator not in _ACTUATORS:
        raise ValueError(f"unknown actuator '{actuator}' (one of {sorted(x for x in _ACTUATORS if x)})")
    hw, hh = 14.0, 10.0
    parts = []
    if kind == "butterfly":
        # inline disc: a circle with a diameter line
        parts.append(_circle(0, 0, hh))
        parts.append(_line(0, -hh, 0, hh, sw=1.4))
    else:
        left = [(-hw, -hh), (-hw, hh), (0, 0)]
        right = [(hw, -hh), (hw, hh), (0, 0)]
        parts.append(_poly(left, fill=WHITE))
        parts.append(_poly(right, fill=WHITE))
        if kind == "globe":
            parts.append(_circle(0, 0, 4.0, fill=BLACK, sw=0))
        elif kind == "check":
            # flow-direction arrow (allowed direction = W->E)
            parts.append(_poly([(-4, -5), (-4, 5), (5, 0)], fill=BLACK, sw=0))

    # actuator on the north stem
    if actuator and actuator != "none":
        stem_top = -hh - 12.0
        parts.append(_line(0, -hh, 0, stem_top, sw=1.2))
        if actuator == "manual":
            parts.append(_line(-8, stem_top, 8, stem_top, sw=1.4))
        elif actuator == "diaphragm":
            parts.append(_path(f"M -9 {stem_top:.1f} A 9 9 0 0 1 9 {stem_top:.1f} Z", fill=WHITE))
        elif actuator == "motor":
            parts.append(_circle(0, stem_top - 3, 8))
            parts.append(_text(0, stem_top - 3, "M", size=9, weight="bold"))
        elif actuator == "solenoid":
            parts.append(_rect(-8, stem_top - 9, 16, 12))
            parts.append(_text(0, stem_top - 3, "S", size=9, weight="bold"))

    ports = _edge_ports(
        hw if kind != "butterfly" else hh,
        hh,
        {"in": (-hw if kind != "butterfly" else -hh, 0.0, "W"),
         "out": (hw if kind != "butterfly" else hh, 0.0, "E")},
    )
    return _finish("".join(parts), ports, x, y, rotation, scale, max(hw, hh) + 12)


_MOUNTS = {"field", "panel", "dcs"}


def instrument(x, y, scale=1.0, rotation=0.0, mount="field", variable="", functions="",
               number="", tag="", **_) -> Symbol:
    """ISA-5.1 instrument bubble.

    ``mount``: ``field`` (plain circle), ``panel`` (main panel-front — circle
    with a centre line), ``dcs`` (shared display/DCS — circle inscribed in a
    square). The top text is the **measured variable + function letters**
    (e.g. ``FT``); the bottom text is the **loop number** (e.g. ``101``).

    If ``variable``/``functions``/``number`` are not given they are parsed from
    ``tag`` (e.g. ``FT-101`` -> top ``FT``, bottom ``101``). Ports: n/e/s/w
    (leads attach on any edge; a P&ID typically drops the lead from ``s``).
    """
    if mount not in _MOUNTS:
        raise ValueError(f"unknown mount '{mount}' (one of {sorted(_MOUNTS)})")
    r = 15.0
    top = f"{variable}{functions}"
    bottom = str(number)
    if (not top or not bottom) and tag:
        head = str(tag).split("-", 1)
        letters = "".join(c for c in head[0] if c.isalpha())
        digits = "".join(c for c in tag if c.isdigit())
        top = top or letters
        bottom = bottom or digits

    parts = []
    if mount == "dcs":
        parts.append(_rect(-r, -r, 2 * r, 2 * r))
    parts.append(_circle(0, 0, r))
    if mount in ("panel", "dcs"):
        parts.append(_line(-r, 0, r, 0, sw=1.0))
    if top:
        parts.append(_text(0, -r * 0.42, top, size=10, weight="bold"))
    if bottom:
        parts.append(_text(0, r * 0.45, bottom, size=9))
    ext = r * 1.5 if mount == "dcs" else r
    return _finish("".join(parts), _edge_ports(ext, ext), x, y, rotation, scale, ext)


_VESSEL_KINDS = {"vessel", "tank", "filter"}


def vessel(x, y, w=54.0, h=90.0, scale=1.0, rotation=0.0, kind="vessel", **_) -> Symbol:
    """Vessel / tank / filter, anchored at its centre.

    ``kind``: ``tank`` (plain rectangle), ``vessel`` (capsule — rounded top &
    bottom), ``filter`` (rectangle with a mesh hatch). Ports: ``in`` (N),
    ``out`` (S) + n/e/s/w.
    """
    if kind not in _VESSEL_KINDS:
        raise ValueError(f"unknown vessel kind '{kind}' (one of {sorted(_VESSEL_KINDS)})")
    hw, hh = w / 2.0, h / 2.0
    parts = []
    if kind == "vessel":
        rr = hw
        d = (
            f"M {-hw:.1f} {-hh + rr:.1f} A {rr:.1f} {rr:.1f} 0 0 1 {hw:.1f} {-hh + rr:.1f} "
            f"L {hw:.1f} {hh - rr:.1f} A {rr:.1f} {rr:.1f} 0 0 1 {-hw:.1f} {hh - rr:.1f} Z"
        )
        parts.append(_path(d, fill=WHITE))
    else:
        parts.append(_rect(-hw, -hh, w, h))
        if kind == "filter":
            for i in range(1, 5):
                yy = -hh + i * h / 5.0
                parts.append(_line(-hw, yy, hw, yy - h / 5.0 * 0.6, sw=0.8))
    ports = _edge_ports(hw, hh, {"in": (0.0, -hh, "N"), "out": (0.0, hh, "S")})
    return _finish("".join(parts), ports, x, y, rotation, scale, max(hw, hh))


def dosing_skid(x, y, w=140.0, h=90.0, scale=1.0, rotation=0.0, label="", **_) -> Symbol:
    """A package / skid boundary — a dashed rectangle enclosing its equipment.

    Anchored at its centre. Ports n/e/s/w on the boundary edges. ``label`` is
    drawn small at the top-left inside the boundary.
    """
    hw, hh = w / 2.0, h / 2.0
    parts = [_rect(-hw, -hh, w, h, fill="none", sw=1.2, dash="6 4")]
    if label:
        parts.append(_text(-hw + 6, -hh + 9, label, size=8, anchor="start", fill=GREY))
    return _finish("".join(parts), _edge_ports(hw, hh), x, y, rotation, scale, max(hw, hh))


def tie_in(x, y, scale=1.0, rotation=0.0, label="", **_) -> Symbol:
    """Tie-in / battery-limit (off-page) connector — a home-plate pentagon.

    The tip points E (rotate to re-aim). Port ``conn`` (and ``w``) is the flat
    back where the process line attaches; ``label`` names the destination.
    """
    w, h = 34.0, 26.0
    hw, hh = w / 2.0, h / 2.0
    pts = [(-hw, -hh), (hw * 0.4, -hh), (hw, 0.0), (hw * 0.4, hh), (-hw, hh)]
    parts = [_poly(pts, fill=WHITE)]
    if label:
        parts.append(_text(-hw * 0.15, 0, label, size=7.5, weight="bold"))
    ports = _edge_ports(hw, hh, {"conn": (-hw, 0.0, "W")})
    return _finish("".join(parts), ports, x, y, rotation, scale, max(hw, hh))


def flow_arrow(x, y, scale=1.0, rotation=0.0, **_) -> Symbol:
    """A small filled flow-direction arrow (points E; rotate to re-aim).

    A decoration placed on a line. Ports ``in`` (W) / ``out`` (E) let it be
    chained if desired.
    """
    a = 7.0
    pts = [(-a, -a * 0.8), (-a, a * 0.8), (a, 0.0)]
    body = _poly(pts, fill=BLACK, sw=0)
    ports = _edge_ports(a, a, {"in": (-a, 0.0, "W"), "out": (a, 0.0, "E")})
    return _finish(body, ports, x, y, rotation, scale, a)


# --------------------------------------------------------------------------
# Registry / dispatch
# --------------------------------------------------------------------------
SYMBOLS = {
    "pump": pump,
    "valve": valve,
    "instrument": instrument,
    "vessel": vessel,
    "tank": lambda *a, **k: vessel(*a, kind="tank", **{kk: vv for kk, vv in k.items() if kk != "kind"}),
    "filter": lambda *a, **k: vessel(*a, kind="filter", **{kk: vv for kk, vv in k.items() if kk != "kind"}),
    "dosing_skid": dosing_skid,
    "skid": dosing_skid,
    "tie_in": tie_in,
    "flow_arrow": flow_arrow,
}


def build_symbol(sym_type: str, x: float, y: float, **params) -> Symbol:
    """Instantiate a symbol by type name at ``(x, y)`` with keyword parameters.

    ``sym_type`` selects the symbol function (``pump``/``valve``/...); a
    symbol's own ``kind`` parameter (e.g. a valve's gate/globe) is passed
    through ``params`` and is distinct from the type name.
    """
    if sym_type not in SYMBOLS:
        raise ValueError(f"unknown symbol type '{sym_type}' (one of {', '.join(sorted(SYMBOLS))})")
    return SYMBOLS[sym_type](x, y, **params)
