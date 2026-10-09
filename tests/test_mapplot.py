"""A map sheet with no QGIS, no GDAL, and no quiet reprojection.

Acceptance tests T8 and T9, plus the validation surface: an unsupported geometry
type, a wrong-CRS layer and a wrong-units layer are all named errors rather than
silently skipped features.
"""

from __future__ import annotations

import json
import shutil
import struct
import subprocess
import sys
import zlib
from pathlib import Path

import pytest

from technical_drawings_for_agents import mapplot
from technical_drawings_for_agents import plot as plot_module
from technical_drawings_for_agents.cli import main
from technical_drawings_for_agents.plot import PlotError

needs_rsvg = pytest.mark.skipif(
    shutil.which("rsvg-convert") is None, reason="rsvg-convert (librsvg) not installed"
)

EXTENT = [788392.0, 322125.0, 788702.0, 322395.0]
GEO_TOOLS = ("ogr2ogr", "gdalwarp", "gdal_translate", "qgis_process")
GEO_MODULES = ("qgis", "osgeo", "gdal", "fiona", "pyproj")

_CONFIG = """
map:
  crs: EPSG:32630
  extent: [788392, 322125, 788702, 322395]
  layers:
    - name: rafts
      geojson: rafts.geojson
      style: {stroke: "#123456", fill: none, stroke_width: 1.2}
      label: tag
    - name: pipes
      geojson: pipes.geojson
      style: {stroke: "#884400", stroke_width: 0.8}
meta:
  number: STA-SITE-GA-001
  title: Site general arrangement
  status: CONCEPT
  scale: "1:500"
"""


def _polygons() -> dict:
    return {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": "EPSG:32630"}},
        "features": [
            {
                "type": "Feature",
                "properties": {"tag": "RAFT-1"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [788412, 322145],
                            [788472, 322145],
                            [788472, 322195],
                            [788412, 322195],
                            [788412, 322145],
                        ]
                    ],
                },
            },
            {
                "type": "Feature",
                "properties": {"tag": "RAFT-2"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [788512, 322245],
                            [788572, 322245],
                            [788572, 322295],
                            [788512, 322295],
                            [788512, 322245],
                        ]
                    ],
                },
            },
        ],
    }


def _lines() -> dict:
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {},
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[788412, 322155], [788612, 322355]],
                },
            }
        ],
    }


def _png(path: Path, width: int = 8, height: int = 8) -> Path:
    """A minimal valid PNG, written with the standard library only."""

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    raw = b"".join(b"\x00" + b"\xc8" * (width * 3) for _ in range(height))
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )
    return path


def _project(tmp_path: Path, *, config: str = _CONFIG) -> Path:
    (tmp_path / "rafts.geojson").write_text(json.dumps(_polygons()), encoding="utf-8")
    (tmp_path / "pipes.geojson").write_text(json.dumps(_lines()), encoding="utf-8")
    config_path = tmp_path / "plot.yaml"
    config_path.write_text(config, encoding="utf-8")
    return config_path


# --------------------------------------------------------------------------- #
# T8
# --------------------------------------------------------------------------- #


@needs_rsvg
def test_a_map_plot_pdf_is_produced_with_no_qgis_and_no_gdal(tmp_path, monkeypatch, capsys):
    """T8 — the GIS review path stops being "open QGIS", without acquiring GDAL."""
    config = _project(tmp_path)
    out = tmp_path / "out"

    spawned: list[str] = []
    real_run = subprocess.run

    def recording_run(cmd, *args, **kwargs):
        spawned.append(Path(str(cmd[0])).name if isinstance(cmd, (list, tuple)) else str(cmd))
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(plot_module.subprocess, "run", recording_run)
    for name in GEO_MODULES:
        monkeypatch.delitem(sys.modules, name, raising=False)

    assert main(["map", str(config), "--out", str(out)]) == 0

    assert (out / "STA-SITE-GA-001.svg").exists()
    assert (out / "STA-SITE-GA-001.pdf").exists()

    markup = (out / "STA-SITE-GA-001.svg").read_text(encoding="utf-8")
    assert 'class="scale-bar"' in markup
    assert 'class="north-arrow"' in markup
    assert 'class="status-watermark"' in markup
    assert "CONCEPT" in markup
    assert "EPSG:32630" in markup

    # per-feature coverage: 3 input features, every one drawn
    stdout = capsys.readouterr().out
    assert "3/3 visible units covered" in stdout

    assert not any(name in sys.modules for name in GEO_MODULES), sorted(
        name for name in GEO_MODULES if name in sys.modules
    )
    assert not any(tool in spawned for tool in GEO_TOOLS), spawned
    assert "rsvg-convert" in spawned  # the only subprocess a map plot needs


def test_the_coverage_report_counts_every_input_feature(tmp_path):
    config = _project(tmp_path)
    parsed = mapplot.load_map_config(config)
    _svg, features = mapplot.build_map_svg(parsed, tmp_path / "out")
    assert len(features) == 3
    assert all(feature.elements for feature in features)


def _with_backdrop(image: str, extent: str = "[788392, 322125, 788702, 322395]") -> str:
    return _CONFIG.replace(
        "  layers:",
        f"  backdrop:\n    image: {image}\n    extent: {extent}\n  layers:",
    )


@needs_rsvg
def test_an_aligned_backdrop_is_referenced_by_relative_path_never_embedded(tmp_path):
    """P8 (#60) owns the no-base64-embed decision; this must not regress it."""
    out = tmp_path / "out"
    (out / "geo").mkdir(parents=True)
    _png(out / "geo" / "ga_backdrop.png")
    config = _project(tmp_path, config=_with_backdrop("out/geo/ga_backdrop.png"))
    result = mapplot.build(config, out)
    markup = result.svg.read_text(encoding="utf-8")
    assert 'href="geo/ga_backdrop.png"' in markup
    assert "base64" not in markup


def test_a_parent_escaping_backdrop_href_is_refused(tmp_path):
    """P8 measured that librsvg silently drops a ``../`` link: exit 0, blank backdrop.

    A relative href that escapes the sheet directory is therefore refused here
    rather than deferred to a renderer that will not complain — the same rule
    ``technical_drawings_for_agents.backdrop`` already enforces for manifest-backed backdrops. A
    naive ``os.path.relpath`` would have produced exactly the silent-loss artifact
    this whole module exists to prevent.
    """
    geo = tmp_path / "geo"
    geo.mkdir()
    _png(geo / "ga_backdrop.png")
    config = _project(tmp_path, config=_with_backdrop("geo/ga_backdrop.png"))
    with pytest.raises(PlotError) as excinfo:
        mapplot.build(config, tmp_path / "out")
    assert excinfo.value.kind == "input"
    message = str(excinfo.value)
    assert "escapes the sheet directory" in message
    assert "blank backdrop at exit 0" in message
    assert "manifest:" in message


@needs_rsvg
def test_a_manifest_backed_backdrop_is_delegated_to_p8(tmp_path):
    """The preferred form: every check comes from ``technical_drawings_for_agents.backdrop``."""
    import hashlib

    out = tmp_path / "out"
    out.mkdir(parents=True)
    image = _png(out / "clip.png")
    digest = hashlib.sha256(image.read_bytes()).hexdigest()
    manifest = tmp_path / "basin.geo.json"
    manifest.write_text(
        json.dumps(
            {
                "schema": "tdfa.geo/1",
                "target": "basin",
                "frame": {"crs": "EPSG:32630", "bbox": EXTENT},
                "coverage": {"fraction": 1.0},
                "link": {"opacity": 0.85},
                "outputs": [
                    {
                        "role": "link",
                        "path": "out/clip.png",
                        "sha256": digest,
                        "size_px": [8, 8],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    config = _project(
        tmp_path,
        config=_CONFIG.replace(
            "  layers:", "  backdrop:\n    manifest: basin.geo.json\n  layers:"
        ),
    )
    result = mapplot.build(config, out)
    markup = result.svg.read_text(encoding="utf-8")
    assert 'href="clip.png"' in markup
    assert 'opacity="0.85"' in markup
    assert result.config.backdrop is not None
    assert result.config.backdrop.manifest == manifest


def test_a_manifest_whose_image_digest_moved_is_refused(tmp_path):
    """P8's staleness check, reached through the map path."""
    out = tmp_path / "out"
    out.mkdir(parents=True)
    _png(out / "clip.png")
    manifest = tmp_path / "basin.geo.json"
    manifest.write_text(
        json.dumps(
            {
                "schema": "tdfa.geo/1",
                "frame": {"crs": "EPSG:32630", "bbox": EXTENT},
                "coverage": {"fraction": 1.0},
                "outputs": [
                    {"role": "link", "path": "out/clip.png", "sha256": "00" * 32,
                     "size_px": [8, 8]}
                ],
            }
        ),
        encoding="utf-8",
    )
    config = _project(
        tmp_path,
        config=_CONFIG.replace(
            "  layers:", "  backdrop:\n    manifest: basin.geo.json\n  layers:"
        ),
    )
    with pytest.raises(PlotError) as excinfo:
        mapplot.build(config, out)
    assert excinfo.value.kind == "input"
    assert "digest mismatch" in str(excinfo.value)


def test_manifest_and_image_are_mutually_exclusive(tmp_path):
    config = _project(
        tmp_path,
        config=_CONFIG.replace(
            "  layers:",
            "  backdrop:\n    manifest: basin.geo.json\n    image: x.png\n  layers:",
        ),
    )
    with pytest.raises(PlotError) as excinfo:
        mapplot.load_map_config(config)
    assert "never both" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# T9
# --------------------------------------------------------------------------- #


def test_a_misaligned_backdrop_is_rejected(tmp_path):
    """T9 — a backdrop 5 m out draws a plausible-looking wrong sheet. Refuse it."""
    geo = tmp_path / "geo"
    geo.mkdir()
    _png(geo / "ga_backdrop.png")
    config = _project(
        tmp_path, config=_with_backdrop("geo/ga_backdrop.png", "[788397, 322130, 788707, 322400]")
    )
    with pytest.raises(PlotError) as excinfo:
        mapplot.build(config, tmp_path / "out")
    assert excinfo.value.kind == "input"
    message = str(excinfo.value)
    assert "788397" in message and "788392" in message  # both extents named
    assert "geo" in message and "#60" in message  # points at the P8 geo verb
    assert not (tmp_path / "out" / "STA-SITE-GA-001.svg").exists()
    assert not (tmp_path / "out" / "STA-SITE-GA-001.pdf").exists()


def test_a_missing_backdrop_image_is_rejected(tmp_path):
    config = _project(tmp_path, config=_with_backdrop("geo/absent.png"))
    with pytest.raises(PlotError) as excinfo:
        mapplot.load_map_config(config)
    assert excinfo.value.kind == "input"
    assert "#60" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# validation: nothing is ever silently skipped
# --------------------------------------------------------------------------- #


def test_an_unsupported_geometry_type_names_the_feature_index(tmp_path):
    config = _project(tmp_path)
    collection = _polygons()
    collection["features"].append(
        {
            "type": "Feature",
            "properties": {},
            "geometry": {"type": "GeometryCollection", "geometries": []},
        }
    )
    (tmp_path / "rafts.geojson").write_text(json.dumps(collection), encoding="utf-8")
    with pytest.raises(PlotError) as excinfo:
        mapplot.build(config, tmp_path / "out")
    assert excinfo.value.kind == "unsupported"
    message = str(excinfo.value)
    assert "GeometryCollection" in message
    assert "feature 2" in message
    assert "silently skipped feature is a defect" in message


def test_a_layer_declaring_a_different_crs_is_rejected(tmp_path):
    config = _project(tmp_path)
    collection = _polygons()
    collection["crs"] = {"type": "name", "properties": {"name": "EPSG:4326"}}
    (tmp_path / "rafts.geojson").write_text(json.dumps(collection), encoding="utf-8")
    with pytest.raises(PlotError) as excinfo:
        mapplot.build(config, tmp_path / "out")
    assert excinfo.value.kind == "input"
    assert "EPSG:4326" in str(excinfo.value)
    assert "never reprojects" in str(excinfo.value)


def test_a_layer_in_the_wrong_units_is_rejected(tmp_path):
    """A lat/lon layer with no declared CRS lands orders of magnitude off the extent."""
    config = _project(tmp_path)
    collection = _polygons()
    collection.pop("crs")
    collection["features"][0]["geometry"]["coordinates"] = [
        [[-2.0, 5.5], [-2.001, 5.5], [-2.001, 5.501], [-2.0, 5.501], [-2.0, 5.5]]
    ]
    (tmp_path / "rafts.geojson").write_text(json.dumps(collection), encoding="utf-8")
    with pytest.raises(PlotError) as excinfo:
        mapplot.build(config, tmp_path / "out")
    assert excinfo.value.kind == "input"
    assert "extent span outside map.extent" in str(excinfo.value)


def test_an_empty_feature_collection_is_rejected(tmp_path):
    config = _project(tmp_path)
    (tmp_path / "pipes.geojson").write_text(
        json.dumps({"type": "FeatureCollection", "features": []}), encoding="utf-8"
    )
    with pytest.raises(PlotError) as excinfo:
        mapplot.build(config, tmp_path / "out")
    assert excinfo.value.kind == "input"
    assert "no features" in str(excinfo.value)


@pytest.mark.parametrize(
    ("mutation", "fragment"),
    [
        ("  crs: EPSG:32630\n", "map.crs is required"),
        ("  extent: [788392, 322125, 788702, 322395]\n", "map.extent must be"),
        ("      geojson: rafts.geojson\n", ".geojson is required"),
    ],
)
def test_every_config_error_names_the_offending_key(tmp_path, mutation, fragment):
    config = _project(tmp_path, config=_CONFIG.replace(mutation, ""))
    with pytest.raises(PlotError) as excinfo:
        mapplot.load_map_config(config)
    assert fragment in str(excinfo.value)
    assert "plot.yaml" in str(excinfo.value)


def test_the_scale_bar_length_is_derived_not_typed():
    """Round numbers, from the extent — 1/2/5 x a power of ten."""
    assert mapplot._scale_bar_length(310.0) == 50.0
    assert mapplot._scale_bar_length(1000.0) == 200.0
    assert mapplot._scale_bar_length(47.0) == 5.0


def test_mapplot_imports_no_geo_stack():
    """The narrowness is the design: assert it over the module source."""
    source = Path(mapplot.__file__).read_text(encoding="utf-8")
    for banned in ("import osgeo", "from osgeo", "import fiona", "import pyproj", "qgis."):
        assert banned not in source, banned
