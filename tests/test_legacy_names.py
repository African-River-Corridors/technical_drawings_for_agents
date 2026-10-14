"""Pre-release rename: new names are written, legacy names are still read.

`tdfa.geo/1` replaced `sankofa.geo/1` as the geo manifest schema id. Files written by older tooling
must keep loading, with a DeprecationWarning, until the legacy name is removed.
"""

from __future__ import annotations

import hashlib
import json
import warnings
from pathlib import Path

import pytest

from technical_drawings_for_agents.backdrop import (
    GEO_SCHEMA,
    LEGACY_GEO_SCHEMA,
    BackdropError,
    load_backdrop,
)


def _manifest(tmp_path: Path, schema: str) -> Path:
    image = tmp_path / "bd.png"
    image.write_bytes(b"png bytes")
    path = tmp_path / "bd.geo.json"
    path.write_text(
        json.dumps(
            {
                "schema": schema,
                "target": "ga_backdrop",
                "outputs": [
                    {
                        "role": "link",
                        "path": "bd.png",
                        "sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
                        "bytes": image.stat().st_size,
                        "driver": "PNG",
                        "size_px": [10, 10],
                    }
                ],
                "frame": {"name": "f", "crs": "EPSG:32630", "bbox": [0, 0, 10, 10]},
                "coverage": {"fraction": 1.0, "warn_below": 0.999, "warning": False},
                "link": {"format": "png", "opacity": 1.0},
            }
        ),
        encoding="utf-8",
    )
    return path


def test_the_geo_schema_ids():
    assert GEO_SCHEMA == "tdfa.geo/1"
    assert LEGACY_GEO_SCHEMA == "sankofa.geo/1"


def test_a_tdfa_geo_manifest_loads_without_a_warning(tmp_path):
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        bd = load_backdrop(_manifest(tmp_path, "tdfa.geo/1"))
    assert bd.bbox == (0.0, 0.0, 10.0, 10.0)


def test_a_legacy_sankofa_geo_manifest_loads_with_a_deprecation_warning(tmp_path):
    with pytest.warns(DeprecationWarning, match="sankofa.geo/1"):
        bd = load_backdrop(_manifest(tmp_path, "sankofa.geo/1"))
    assert bd.bbox == (0.0, 0.0, 10.0, 10.0)


def test_an_unknown_geo_schema_is_still_refused(tmp_path):
    with pytest.raises(BackdropError, match="unsupported schema"):
        load_backdrop(_manifest(tmp_path, "other.geo/1"))
