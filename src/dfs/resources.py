"""Find the data files of the standard.

A checkout has them under ``docs/standard/``. An installed wheel has copies under ``dfs/data/``
with the same relative paths (see ``pyproject.toml``). The font is always in ``data/fonts/`` of
the package.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent
FONT_DIR = PACKAGE / "data" / "fonts"
FONT_FILE = FONT_DIR / "BarlowSemiCondensed-Regular.ttf"


def _roots() -> list[Path]:
    roots = [PACKAGE / "data"]
    for parent in PACKAGE.parents:
        if (parent / "docs" / "standard").is_dir():
            roots.append(parent / "docs" / "standard")
            break
    return roots


def find(*parts: str) -> Path:
    """Return the first existing file ``parts`` under the data roots, or raise."""
    for root in _roots():
        candidate = root.joinpath(*parts)
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("standard data file not found: " + "/".join(parts))


@cache
def _json(*parts: str):
    return json.loads(find(*parts).read_text(encoding="utf-8"))


def schema() -> dict:
    return _json("schema", "drawing-document-1.0.schema.json")


def line_styles() -> dict:
    return _json("data", "line-styles.json")


def font_widths() -> dict:
    return _json("data", "font-widths.json")


def suite_dir() -> Path:
    return find("conformance", "suite.json").parent


def hatch_patterns() -> dict:
    return _json("data", "hatch-patterns.json")


def title_block_iso7200() -> dict:
    return _json("data", "title-block-iso7200.json")
