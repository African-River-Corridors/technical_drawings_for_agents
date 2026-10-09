"""CLI acceptance tests for the paper-space sheet model (spec §6, tests 20 and 21)."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from technical_drawings_for_agents.cli import main
from technical_drawings_for_agents.sheet import SheetDrawing, load_sheet_config, resolve_sheet

BASIN_EXTENT_M = [788392, 322125, 788702, 322395]
TITLE_BLOCK_RESERVE = [{"name": "title-block", "edge": "bottom", "size_mm": 40}]


def _config(scale: int | str = 1250, extent: list[float] | None = None) -> dict:
    return {
        "size": "A3",
        "orientation": "landscape",
        "margins_mm": 10,
        "reserve": [dict(item) for item in TITLE_BLOCK_RESERVE],
        "viewport": {
            "extent_m": list(extent if extent is not None else BASIN_EXTENT_M),
            "scale": scale,
        },
        "north": {"model_bearing_deg": 0.0, "label": "N"},
    }


def _write_config(path: Path, config: dict | None, **extra: object) -> Path:
    data: dict = {"number": "X", "title": "CLI sheet", **extra}
    if config is not None:
        data["sheet"] = config
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def _snapshot(root: Path) -> dict[Path, bytes]:
    return {path: path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}


def test_cli_sheet_reports_the_resolved_binding(tmp_path, capsys):
    good = _write_config(tmp_path / "good.yaml", _config())
    before = _snapshot(tmp_path)

    assert main(["sheet", str(good), "--json"]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    sheet = load_sheet_config(good)
    assert sheet is not None
    assert json.loads(captured.out) == json.loads(sheet.metadata_json())

    # The human block names every derived number.
    assert main(["sheet", str(good)]) == 0
    report = capsys.readouterr().out
    for fragment in ("A3 landscape", "1:1250", "400 × 277 mm", "title-block", "north:"):
        assert fragment in report

    # --check: resolve and say nothing (exit 0, empty stdout) for CI use.
    assert main(["sheet", str(good), "--check"]) == 0
    assert capsys.readouterr().out == ""

    # A sheet that does not fit at the stated scale is a failure, not a silent refit.
    bad = _write_config(tmp_path / "bad.yaml", _config(scale=500))
    assert main(["sheet", str(bad), "--json"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("error: ")
    # Named, not guessed: this sheet reserves 40 mm for its title block, so the viewport
    # frame is 400 x 237 mm and 1:1000 (310 x 270 mm) would not fit either.
    assert "Smallest preferred scale that fits: 1:1250" in captured.err

    # Usage errors: a missing file, and a YAML with no sheet: block.
    assert main(["sheet", str(tmp_path / "nope.yaml")]) == 2
    assert "not found" in capsys.readouterr().err

    legacy = _write_config(tmp_path / "legacy.yaml", None, scale="1:50")
    assert main(["sheet", str(legacy)]) == 2
    assert "has no sheet: block" in capsys.readouterr().err

    # The command is read-only: it writes only to stdout/stderr.
    after = _snapshot(tmp_path)
    assert set(after) - set(before) == {tmp_path / "bad.yaml", tmp_path / "legacy.yaml"}
    assert all(after[path] == blob for path, blob in before.items())


def test_cli_validate_strict_promotes_warnings_to_failure(tmp_path, capsys):
    drawing = tmp_path / "drawing"
    (drawing / "out").mkdir(parents=True)
    config = _config()  # A3 1:1250 — one warning: not an ISO 5455 preferred ratio
    _write_config(drawing / "meta.yaml", config, status="CONCEPT", for_construction=False)
    (drawing / "out" / "X.svg").write_text(
        SheetDrawing(sheet=resolve_sheet(config), status="CONCEPT").render(), encoding="utf-8"
    )

    assert main(["validate", str(drawing)]) == 0
    captured = capsys.readouterr()
    assert "OK: " in captured.out
    warn_lines = [line for line in captured.out.splitlines() if line.startswith("warn: ")]
    assert len(warn_lines) == 1
    assert "not an ISO 5455 preferred ratio" in warn_lines[0]

    assert main(["validate", str(drawing), "--strict"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "FAIL: " in captured.err
    assert "not an ISO 5455 preferred ratio" in captured.err

    # A clean legacy directory reads identically with and without --strict.
    example = Path(__file__).resolve().parents[1] / "drawings" / "example" / "simple-section"
    assert main(["validate", str(example)]) == 0
    plain = capsys.readouterr().out
    assert main(["validate", str(example), "--strict"]) == 0
    assert capsys.readouterr().out == plain
