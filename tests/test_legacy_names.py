"""Pre-release rename: new names are written, legacy names are still read.

`dfs.geo/1` is the geo manifest schema id. It replaced `tdfa.geo/1`, which replaced `sankofa.geo/1`.
Files written by older tooling must keep loading, with a DeprecationWarning, until the legacy ids are removed.
"""

from __future__ import annotations

import hashlib
import json
import warnings
from pathlib import Path

import pytest

from dfs.backdrop import (
    GEO_SCHEMA,
    LEGACY_GEO_SCHEMAS,
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
    assert GEO_SCHEMA == "dfs.geo/1"
    assert LEGACY_GEO_SCHEMAS == ("tdfa.geo/1", "sankofa.geo/1")


def test_a_dfs_geo_manifest_loads_without_a_warning(tmp_path):
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        bd = load_backdrop(_manifest(tmp_path, "dfs.geo/1"))
    assert bd.bbox == (0.0, 0.0, 10.0, 10.0)


@pytest.mark.parametrize("legacy", ["tdfa.geo/1", "sankofa.geo/1"])
def test_a_legacy_geo_manifest_loads_with_a_deprecation_warning(tmp_path, legacy):
    with pytest.warns(DeprecationWarning, match=legacy):
        bd = load_backdrop(_manifest(tmp_path, legacy))
    assert bd.bbox == (0.0, 0.0, 10.0, 10.0)


def test_an_unknown_geo_schema_is_still_refused(tmp_path):
    with pytest.raises(BackdropError, match="unsupported schema"):
        load_backdrop(_manifest(tmp_path, "other.geo/1"))
