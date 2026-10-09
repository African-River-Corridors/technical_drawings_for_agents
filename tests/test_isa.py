"""Unit tests for the ISA-5.1 symbol library (pure geometry + ports)."""

from __future__ import annotations

import math
import xml.dom.minidom as minidom

import pytest

from technical_drawings_for_agents import isa


def _wellformed(svg_group: str):
    # wrap so the group is a valid standalone document
    minidom.parseString(f'<svg xmlns="http://www.w3.org/2000/svg">{svg_group}</svg>')


def test_every_symbol_has_edge_ports_and_wellformed_svg():
    factories = {
        "pump": {},
        "valve": {"kind": "gate"},
        "instrument": {"mount": "field", "variable": "F", "functions": "T"},
        "vessel": {},
        "tank": {},
        "filter": {},
        "dosing_skid": {"label": "SKID"},
        "tie_in": {"label": "OSBL"},
        "flow_arrow": {},
    }
    for kind, kw in factories.items():
        sym = isa.build_symbol(kind, 100.0, 100.0, **kw)
        for edge in ("n", "e", "s", "w"):
            assert edge in sym.ports, f"{kind} missing port {edge}"
            assert sym.port_dirs[edge] in {"N", "E", "S", "W"}
        _wellformed(sym.svg)


def test_pump_semantic_ports():
    p = isa.pump(200, 200)
    assert p.ports["suction"] == p.ports["in"]
    assert p.ports["discharge"] == p.ports["out"]
    # suction is west of the anchor, discharge east (triangle apex points E)
    assert p.ports["suction"][0] < 200 < p.ports["discharge"][0]
    assert p.port_dirs["suction"] == "W" and p.port_dirs["discharge"] == "E"


def test_rotation_transforms_ports_and_directions():
    # A pump rotated 180° sends its west suction port to the east side.
    p0 = isa.pump(0, 0)
    p180 = isa.pump(0, 0, rotation=180)
    sx0 = p0.ports["suction"][0]
    sx180 = p180.ports["suction"][0]
    assert sx0 < 0 and sx180 > 0
    assert math.isclose(sx0, -sx180, abs_tol=1e-6)
    assert p180.port_dirs["suction"] == "E"


def test_scale_scales_ports():
    small = isa.pump(0, 0, scale=0.5)
    full = isa.pump(0, 0, scale=1.0)
    assert math.isclose(small.ports["suction"][0], full.ports["suction"][0] * 0.5, abs_tol=1e-6)


def test_valve_kinds_and_actuators():
    for kind in ("gate", "globe", "butterfly", "check"):
        v = isa.valve(0, 0, kind=kind)
        assert "in" in v.ports and "out" in v.ports
        _wellformed(v.svg)
    for act in ("manual", "diaphragm", "motor", "solenoid"):
        v = isa.valve(0, 0, kind="gate", actuator=act)
        _wellformed(v.svg)


def test_instrument_parses_tag():
    # variable/number derived from the tag when not given explicitly
    ins = isa.instrument(0, 0, mount="field", tag="FT-101")
    assert ">FT<" in ins.svg and ">101<" in ins.svg


def test_unknown_type_and_kind_raise():
    with pytest.raises(ValueError):
        isa.build_symbol("nonsense", 0, 0)
    with pytest.raises(ValueError):
        isa.valve(0, 0, kind="rotary-ball")
    with pytest.raises(ValueError):
        isa.valve(0, 0, kind="gate", actuator="hydraulic")


def test_symbol_port_lookup_error():
    p = isa.pump(0, 0)
    with pytest.raises(KeyError):
        p.port("nonexistent")
