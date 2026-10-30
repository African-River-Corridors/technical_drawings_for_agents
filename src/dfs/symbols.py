"""Symbols and libraries (clause 13)."""

from __future__ import annotations

import json
import math
from pathlib import Path

import jsonschema

from . import resources
from .entities import _hatch, _line, _polyline, _pt, _text
from .numbers import angle, cos_sin, mm
from .rules import arc_sweep

LIBRARY_SCHEMA_NAME = "symbol-library-1.0.schema.json"


def library_schema() -> dict:
    return resources._json("schema", LIBRARY_SCHEMA_NAME)


def _parts_ok(symbol) -> bool:
    """13.2.2: the rules of 9.3 to 9.6 and 10.3 for the parts of a symbol."""
    from .pipeline import _hatch_ok  # local import: pipeline imports this module

    for p in symbol["geometry"]:
        t = p["type"]
        if t == "line" and p["from"] == p["to"]:
            return False
        if t == "polyline":
            pts = p["points"]
            if any(pts[i] == pts[i + 1] for i in range(len(pts) - 1)) or (p.get("closed") and pts[0] == pts[-1]):
                return False
        if t == "arc" and arc_sweep(p["start_deg"], p["end_deg"]) is None:
            return False
        if t == "hatch" and not _hatch_ok(p["boundary"], []):
            return False
    ids = [port["id"] for port in symbol.get("ports", [])]
    return len(ids) == len(set(ids))


def _check_symbol_set(symbols) -> bool:
    return all(_parts_ok(sym) for sym in symbols.values())


def load_libraries(doc, base_dir) -> tuple[dict, set[str]]:
    """Return ``(libraries, errors)``: ``libraries`` maps a library id to its symbols (13.1, 13.3.1)."""
    libs: dict[str, dict] = {}
    errors: set[str] = set()
    schema_validator = jsonschema.Draft202012Validator(library_schema())
    seen = set()
    for item in doc.get("symbol_libraries", []):
        if item["id"] in seen:
            errors.add("13.3.1")
            continue
        seen.add(item["id"])
        if "path" in item:
            path = Path(base_dir or ".") / item["path"]
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                errors.add("13.1")
                continue
            if any(True for _ in schema_validator.iter_errors(data)) or data.get("standard_version", "1.0").split(".")[0] != "1":
                errors.add("13.1")
                continue
            symbols = data["symbols"]
        else:
            symbols = item["symbols"]
        if not _check_symbol_set(symbols):
            errors.add("13.1")
            continue
        libs[item["id"]] = symbols
    return libs, errors


class SymbolContext:
    """What ``parts`` needs to know about the viewport and the layer of the insert."""

    def __init__(self, mapper, rotation, denominator, group, layer_role, layer_line_type, origin):
        self.mapper, self.rotation, self.denominator = mapper, rotation, denominator
        self.group, self.layer_role, self.layer_line_type, self.origin = group, layer_role, layer_line_type, origin


class Placement:
    """The transform of one insert (13.4): symbol coordinates -> model coordinates."""

    def __init__(self, insert, symbol, denominator):
        self.insert, self.symbol = insert, symbol
        # paper_mm: a symbol of h mm on the paper is h * N / 1000 metres in model space (13.4).
        self.factor = insert.get("scale", 1) * (denominator / 1000 if symbol["units"] == "paper_mm" else 1)
        self.origin = symbol.get("insertion", [0, 0])
        self.rotation = insert.get("rotation_deg", 0.0)
        self.cos, self.sin = cos_sin(self.rotation)
        self.position = insert["position_m"]

    def model(self, q):
        dx, dy = (q[0] - self.origin[0]) * self.factor, (q[1] - self.origin[1]) * self.factor
        return (self.position[0] + dx * self.cos - dy * self.sin, self.position[1] + dx * self.sin + dy * self.cos)


def port_point(insert, symbol, port_id, denominator):
    """13.6: the model point of a port, or None."""
    for port in symbol.get("ports", []):
        if port["id"] == port_id:
            return Placement(insert, symbol, denominator).model(port["at"])
    return None


def parts(insert, symbol, ctx) -> tuple[list[dict], set[str]]:
    """The normalised entities of one insert (13.7) and its errors (13.5.1).

    ``ctx`` has ``mapper``, ``rotation`` (of the viewport), ``denominator``, ``group``, ``layer_role``,
    ``layer_line_type`` and ``origin``.
    """
    errors: set[str] = set()
    out: list[dict] = []
    pl = Placement(insert, symbol, ctx.denominator)
    mapper, vp_rot, k = ctx.mapper, ctx.rotation, ctx.mapper.k
    origin, layer = ctx.origin + ".symbol", insert["layer"]
    for part in symbol["geometry"]:
        t = part["type"]
        role = part.get("role") or insert.get("role") or ctx.layer_role
        width = ctx.group[role]
        line_type = part.get("line_type") or ctx.layer_line_type
        pm = lambda q: mapper.point(pl.model(q))  # noqa: E731
        if t == "line":
            out.append(_line(origin, layer, pm(part["from"]), pm(part["to"]), line_type, width))
        elif t == "polyline":
            out.append(_polyline(origin, layer, [pm(q) for q in part["points"]], part.get("closed", False), line_type, width))
        elif t == "hatch":
            out.append(_hatch(origin, layer, [pm(q) for q in part["boundary"]], [], "solid", 0, width))
        else:
            c = pm(part["centre"])
            item = {"type": t, "origin": origin, "layer": layer, "centre_mm": _pt(c), "radius_mm": mm(part["radius"] * pl.factor * k)}
            if t == "arc":
                item["start_deg"] = angle(part["start_deg"] + pl.rotation + vp_rot)
                item["sweep_deg"] = mm(arc_sweep(part["start_deg"], part["end_deg"]))
            item["line_type"], item["width_mm"] = line_type, width
            out.append(item)
    if "label" in insert:
        slot = symbol.get("label_slot")
        if slot is None:
            errors.add("13.5.1")
        else:
            out.append(_text(origin, layer, pm_anchor(pl, mapper, slot["at"]), insert["label"], slot.get("height_mm", 3.5), 0,
                             slot.get("align_h", "centre"), slot.get("align_v", "middle")))
    return out, errors


def pm_anchor(pl, mapper, q):
    return mapper.point(pl.model(q))


_ = math
