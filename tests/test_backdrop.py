from __future__ import annotations

import ast
from dataclasses import dataclass
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "src" / "technical_drawings_for_agents" / "backdrop.py"
SPEC = importlib.util.spec_from_file_location("_test_technical_drawings_for_agents_backdrop", MODULE_PATH)
assert SPEC and SPEC.loader
backdrop = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = backdrop
SPEC.loader.exec_module(backdrop)


@dataclass
class ViewBox:
    real_min_x: float
    real_max_x: float
    real_min_y: float
    real_max_y: float
    svg_width: float = 1000.0
    svg_height: float = 800.0
    padding: float = 40.0
    crs: str = "EPSG:32630"

    @property
    def scale(self) -> float:
        return min(
            (self.svg_width - 2 * self.padding) / (self.real_max_x - self.real_min_x),
            (self.svg_height - 2 * self.padding) / (self.real_max_y - self.real_min_y),
        )

    def x(self, value: float) -> float:
        return self.padding + (value - self.real_min_x) * self.scale

    def y(self, value: float) -> float:
        return self.svg_height - self.padding - (value - self.real_min_y) * self.scale

    def length(self, value: float) -> float:
        return abs(value * self.scale)


@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("bbox", "xmax 788701.96 != sheet frame xmax 788702.0.*delta 0.040"),
        ("crs", "crs EPSG:32630 != sheet frame crs EPSG:32631"),
        ("digest", "digest mismatch"),
        ("coverage", "coverage is 0.0"),
        ("missing", "image is missing"),
    ],
)
def test_backdrop_frame_mismatch_is_a_loud_error(
    tmp_path: Path,
    case: str,
    message: str,
) -> None:
    bbox = [788392, 322125, 788701.96, 322395] if case == "bbox" else [
        788392,
        322125,
        788702,
        322395,
    ]
    coverage = 0.0 if case == "coverage" else 0.99
    manifest = _write_manifest(tmp_path, bbox=bbox, coverage=coverage)
    bd = backdrop.load_backdrop(manifest)
    vb = ViewBox(788392, 788702, 322125, 322395)
    if case == "crs":
        vb.crs = "EPSG:32631"
    if case == "digest":
        (tmp_path / "geo" / "bd.png").write_bytes(b"stale")
    if case == "missing":
        (tmp_path / "geo" / "bd.png").unlink()

    with pytest.raises(backdrop.BackdropError, match=message):
        backdrop.backdrop_image_element(bd, vb, tmp_path)


def test_backdrop_with_matching_manifest_emits_relative_image_element(tmp_path: Path) -> None:
    manifest = _write_manifest(
        tmp_path,
        bbox=[788392, 322125, 788702, 322395],
        coverage=1.0,
    )
    bd = backdrop.load_backdrop(manifest)

    element = backdrop.backdrop_image_element(
        bd,
        ViewBox(788392, 788702, 322125, 322395),
        tmp_path,
    )

    assert "<image " in element
    assert 'href="geo/bd.png"' in element
    assert 'preserveAspectRatio="none"' in element
    assert "data:image" not in element
    assert "base64" not in element


def test_href_escaping_the_svg_directory_is_refused_by_default(tmp_path: Path) -> None:
    """librsvg silently renders a parent-escaping href as a blank backdrop.

    Measured (rsvg-convert 2.62.3): exit 0, empty stderr, ~955-byte PDF. The
    matching end-to-end measurement lives in the qgis job's
    ``test_parent_escaping_href_is_refused_because_rsvg_renders_it_blank``.
    """

    manifest = _write_manifest(tmp_path, bbox=[788392, 322125, 788702, 322395], coverage=1.0)
    bd = backdrop.load_backdrop(manifest)
    vb = ViewBox(788392, 788702, 322125, 322395)
    svg_dir = tmp_path / "out"
    svg_dir.mkdir()

    with pytest.raises(backdrop.BackdropError, match="escapes the SVG directory"):
        backdrop.backdrop_image_element(bd, vb, svg_dir)

    with pytest.warns(UserWarning, match="renders a blank backdrop"):
        element = backdrop.backdrop_image_element(bd, vb, svg_dir, allow_outside_svg_dir=True)
    assert 'href="../geo/bd.png"' in element


def test_coverage_between_thresholds_warns_and_still_emits(tmp_path: Path) -> None:
    manifest = _write_manifest(
        tmp_path,
        bbox=[788392, 322125, 788702, 322395],
        coverage=0.994518,
        warning=True,
    )
    bd = backdrop.load_backdrop(manifest)

    with pytest.warns(UserWarning, match="coverage 0.994518 < warn_coverage 0.999000"):
        element = backdrop.backdrop_image_element(
            bd,
            ViewBox(788392, 788702, 322125, 322395),
            tmp_path,
        )

    assert "<image " in element


def test_backdrop_reader_imports_only_the_standard_library(tmp_path: Path) -> None:
    """technical_drawings_for_agents must never gain a GDAL dependency; the manifest is plain JSON."""

    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imported.add(node.module.split(".")[0])

    assert imported <= set(sys.stdlib_module_names)
    assert not imported & {"osgeo", "gdal", "pyproj", "subprocess", "rasterio"}
    manifest = _write_manifest(tmp_path, bbox=[788392, 322125, 788702, 322395], coverage=1.0)
    assert backdrop.load_backdrop(manifest).size_px == (3100, 2700)


def _write_manifest(
    tmp_path: Path,
    *,
    bbox: list[float],
    coverage: float,
    warning: bool = False,
) -> Path:
    geo = tmp_path / "geo"
    geo.mkdir()
    image = geo / "bd.png"
    image.write_bytes(b"png bytes")
    digest = hashlib.sha256(image.read_bytes()).hexdigest()
    manifest = geo / "bd.geo.json"
    manifest.write_text(
        json.dumps(
            {
                "schema": "tdfa.geo/1",
                "target": "ga_backdrop",
                "outputs": [
                    {
                        "role": "link",
                        "path": "bd.png",
                        "sha256": digest,
                        "bytes": image.stat().st_size,
                        "driver": "PNG",
                        "size_px": [3100, 2700],
                    }
                ],
                "frame": {"name": "ga-test", "crs": "EPSG:32630", "bbox": bbox},
                "coverage": {
                    "fraction": coverage,
                    "method": "alpha_band_mean",
                    "warn_below": 0.999,
                    "warning": warning,
                },
                "link": {"format": "png", "opacity": 1.0},
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return manifest
