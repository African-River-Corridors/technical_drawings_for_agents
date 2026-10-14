"""Run the conformance suite of the standard (clause 17) against this implementation."""

from __future__ import annotations

import json
from pathlib import Path

from . import dimensions, rules, symbols
from .numbers import mm, round_half_even
from .pipeline import run_file


def _same(a, b) -> bool:
    """16.9: parsed values are equal; 2 and 2.0 are the same number."""
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a == b
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_same(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))
    return a == b


def compare(result: dict, expected: dict) -> list[str]:
    """17.4: the differences between a runner result and an expected file (empty when equal)."""
    problems: list[str] = []
    if result.get("result") != expected["result"]:
        return [f"result is {result.get('result')!r}, expected {expected['result']!r}: {result.get('errors')}"]
    if expected["result"] == "error":
        got = {e["clause"] for e in result["errors"]}
        want = {e["clause"] for e in expected["errors"]}
        return [] if got == want else [f"errors {sorted(got)} != {sorted(want)}"]
    norm = result["normalised"]
    for key in ("sheet", "viewports"):
        if key in expected and not _same(norm[key], expected[key]):
            problems.append(f"{key} differs")
    if "entities" in expected:
        entities = norm["entities"]
        prefixes = expected.get("origin_prefixes")
        if prefixes:
            entities = [e for e in entities if any(e["origin"].startswith(p) for p in prefixes)]
        if not _same(entities, expected["entities"]):
            problems.append(_first_entity_difference(entities, expected["entities"]))
    return problems


def _first_entity_difference(got, want) -> str:
    if len(got) != len(want):
        return f"entities: {len(got)} found, {len(want)} expected"
    for i, (g, w) in enumerate(zip(got, want)):
        if not _same(g, w):
            return f"entities[{i}] differs: {json.dumps(g)} != {json.dumps(w)}"
    return "entities differ"


# unit functions (17.5) -----------------------------------------------------------------------


def _vector(theta_deg):
    """A paper vector at the visual angle theta (counter-clockwise from the horizontal)."""
    c, s = rules.cos_sin(theta_deg)
    return (c, -s)


def _frame(f):
    return (f["x_mm"], f["y_mm"], f["width_mm"], f["height_mm"])


def _err(clause, value):
    return {"error": clause} if value is None else value


def run_unit(function: str, i: dict):
    if function == "round_half_even":
        return round_half_even(i["value"], i["places"])
    if function == "snap_width":
        return _err("8.3.2", rules.snap_width(i["width_mm"]))
    if function == "drawing_space":
        _, _, (x, y, w, h) = rules.drawing_space(i["size"], i["orientation"])
        return {"x_mm": x, "y_mm": y, "width_mm": w, "height_mm": h}
    if function == "map_point":
        m = rules.Mapper(_frame(i["frame"]), i["window"], i["scale"], i["rotation_deg"] % 360.0)
        return [mm(c) for c in m.point(i["point_m"])]
    if function == "window_fits":
        return rules.window_fits(_frame(i["frame"]), i["window"], i["scale"], i["rotation_deg"] % 360.0)
    if function == "fit_scale":
        return rules.fit_scale(_frame(i["frame"]), i["window"], i["rotation_deg"] % 360.0)
    if function == "bar_length":
        return _err("7.9.4", rules.derived_bar_length(i["frame_width_mm"], i["scale"]))
    if function == "bar_labels":
        return rules.bar_labels(i["length_m"], i["divisions"], i["unit"])
    if function == "north_deg":
        return round_half_even(rules.north_deg(i["rotation_deg"], i["model_bearing_deg"]), 3)
    if function == "line_pattern":
        return rules.line_pattern(i["line_type"], i["width_mm"])
    if function == "arc_sweep":
        return _err("9.5.1", rules.arc_sweep(i["start_deg"], i["end_deg"]))
    if function == "text_width":
        return rules.text_width(i["text"], i["height_mm"])
    if function == "text_box":
        return rules.rounded_box(i["anchor_mm"], i["text"], i["height_mm"], i["rotation_deg"], i["align_h"], i["align_v"])
    if function == "symbol_point":
        sym = {"units": i["units"], "insertion": i["insertion"]}
        pl = symbols.Placement({"position_m": i["position_m"], "rotation_deg": i["rotation_deg"], "scale": i["scale"]}, sym, i["denominator"])
        return [round_half_even(c, 6) for c in pl.model(i["q"])]
    if function == "dimension_text":
        factor = 1000 if i["unit"] == "mm" else 1
        return dimensions.format_value(i["value_m"] * factor, i["decimals"])
    if function == "reading_rotation":
        return round_half_even(dimensions.reading_rotation(_vector(i["theta_deg"])), 3)
    if function == "chain_ok":
        info = [{"id": "c", "role": "part", "text": t, "unit": "m", "decimals": 2} for t in i["parts"]]
        info.append({"id": "c", "role": "overall", "text": i["overall"], "unit": "m", "decimals": 2})
        return dimensions.check_chains(info)
    raise ValueError(f"unknown unit function {function!r}")


def run_suite(suite_dir: Path) -> list[tuple[str, list[str]]]:
    """Run every case. Returns ``(case id, problems)``; a case passes when its list is empty."""
    suite = json.loads((suite_dir / "suite.json").read_text(encoding="utf-8"))
    report = []
    for case in suite["cases"]:
        if case["kind"] == "document":
            case_dir = suite_dir / case["dir"]
            expected = json.loads((case_dir / "expected.json").read_text(encoding="utf-8"))
            problems = compare(run_file(case_dir / "input.json"), expected)
        else:
            unit = json.loads((suite_dir / case["file"]).read_text(encoding="utf-8"))
            problems = []
            for check in unit["checks"]:
                got = run_unit(unit["function"], check["input"])
                if not _same(got, check["expected"]):
                    problems.append(f"{check['id']}: got {got!r}, expected {check['expected']!r}")
        report.append((case["id"], problems))
    return report
