"""Manifest-backed raster backdrops for SVG sheets.

The drawing package deliberately does not know how to run GDAL. It only trusts
the small geo manifest (schema ``tdfa.geo/1``) written by an external GIS tool's ``geo``
step and checks the linked sidecar before emitting an SVG ``<image>``. That check closes the silent-blank
PDF failure mode introduced by linking instead of base64 inlining.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import html
import json
import os
from pathlib import Path
from typing import Any
import warnings


class BackdropError(ValueError):
    """Raised when a linked backdrop manifest or sidecar is not safe to emit."""


#: The geo manifest schema id this package reads.
GEO_SCHEMA = "tdfa.geo/1"
#: The pre-release id for the same schema. Still read, with a DeprecationWarning, so
#: manifests written by older GIS tooling keep loading. Never written.
LEGACY_GEO_SCHEMA = "sankofa.geo/1"


#: ``rsvg-convert`` (librsvg 2.62.3, measured) will only load a linked raster from
#: the SVG's own directory or a subdirectory of it. A parent-escaping ``../x.png``,
#: an absolute path and a ``file://`` URI are all silently dropped: exit 0, empty
#: stderr, and a ~955-byte PDF with a blank backdrop -- indistinguishable at a
#: glance from a rendered sheet. So an href that escapes the SVG directory is
#: refused here rather than deferred to a renderer that will not complain.
LIBRSVG_HREF_NOTE = (
    "rsvg-convert only loads a linked raster from the SVG's own directory or below "
    "it; a parent-escaping href renders a blank backdrop at exit 0"
)


@dataclass(frozen=True)
class Backdrop:
    """A pre-clipped, georeferenced raster sidecar, read from its geo manifest."""

    manifest_path: Path
    image_path: Path
    bbox: tuple[float, float, float, float]
    crs: str
    size_px: tuple[int, int]
    sha256: str
    coverage: float
    opacity: float
    target: str = ""
    warn_coverage: float | None = None
    coverage_warning: bool = False


def load_backdrop(manifest_path: str | Path) -> Backdrop:
    """Load the linked raster sidecar declared by a geo manifest."""

    path = Path(manifest_path).expanduser().resolve()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise BackdropError(f"could not read backdrop manifest {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise BackdropError(f"could not parse backdrop manifest {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise BackdropError(f"{path.name}: manifest must be a JSON object")
    schema = raw.get("schema")
    if schema == LEGACY_GEO_SCHEMA:
        warnings.warn(
            f"{path.name}: geo manifest schema {LEGACY_GEO_SCHEMA!r} is deprecated; "
            f"write {GEO_SCHEMA!r}. The legacy id is still read and will be removed "
            "in a future release.",
            DeprecationWarning,
            stacklevel=2,
        )
    elif schema != GEO_SCHEMA:
        raise BackdropError(f"{path.name}: unsupported schema {schema!r}")

    link_output = _find_output(raw, "link")
    if link_output is None:
        raise BackdropError(f"{path.name}: manifest has no output with role 'link'")
    image_rel = _required_str(link_output, "path", f"{path.name}.outputs[link].path")
    image_path = Path(image_rel)
    if not image_path.is_absolute():
        image_path = path.parent / image_path

    frame = _required_mapping(raw, "frame", path.name)
    coverage = _required_mapping(raw, "coverage", path.name)
    link = raw.get("link") if isinstance(raw.get("link"), dict) else {}
    size_px = _size_px(link_output, raw)
    bbox = _bbox(frame.get("bbox"), f"{path.name}.frame.bbox")
    return Backdrop(
        manifest_path=path,
        image_path=image_path.resolve(),
        bbox=bbox,
        crs=_required_str(frame, "crs", f"{path.name}.frame"),
        size_px=size_px,
        sha256=_required_str(link_output, "sha256", f"{path.name}.outputs[link]"),
        coverage=_required_float(coverage, "fraction", f"{path.name}.coverage"),
        opacity=_optional_float(link, "opacity", 1.0, f"{path.name}.link"),
        target=str(raw.get("target", "")),
        warn_coverage=_optional_float_or_none(coverage, "warn_below", f"{path.name}.coverage"),
        coverage_warning=bool(coverage.get("warning", False)),
    )


def backdrop_image_element(
    bd: Backdrop,
    vb: Any,
    svg_dir: Path,
    *,
    tolerance_m: float = 0.001,
    allow_outside_svg_dir: bool = False,
) -> str:
    """Return a checked SVG ``<image>`` element for ``bd`` in ``vb``.

    ``allow_outside_svg_dir`` downgrades the parent-escaping-href refusal
    (``LIBRSVG_HREF_NOTE``) to a warning, for a renderer without librsvg's
    resource policy -- ``cairosvg``, which ``technical_drawings_for_agents`` itself uses, resolves
    ``../`` normally. It is off by default because the PDF path is the one that
    fails silently.
    """

    _assert_image_current(bd)
    _assert_frame_matches(bd, vb, tolerance_m)
    sheet_crs = getattr(vb, "crs", None) or getattr(vb, "model_crs", None)
    # TODO(open question): today's ViewBox has no CRS field; compare when a sheet/viewport
    # object supplies one, but do not invent a CRS argument in the public API.
    if sheet_crs is not None and bd.crs != sheet_crs:
        raise BackdropError(
            f"backdrop '{_name(bd)}' crs {bd.crs} != sheet frame crs {sheet_crs}; "
            "regenerate the geo manifest"
        )
    if bd.coverage <= 0.0:
        raise BackdropError(
            f"backdrop '{_name(bd)}' coverage is 0.0; regenerate the geo manifest"
        )
    if bd.coverage_warning or (
        bd.warn_coverage is not None and bd.coverage < bd.warn_coverage
    ):
        threshold = bd.warn_coverage if bd.warn_coverage is not None else 0.0
        warnings.warn(
            f"backdrop '{_name(bd)}' coverage {bd.coverage:.6f} "
            f"< warn_coverage {threshold:.6f}",
            stacklevel=2,
        )

    xmin, ymin, xmax, ymax = bd.bbox
    href = _relative_href(bd, svg_dir, allow_outside_svg_dir=allow_outside_svg_dir)
    if _is_paper_viewport(vb):
        # Paper-space sheet: millimetres, and the viewport may be rotated. The raster
        # is axis-aligned in the model, so place it unrotated about the extent centre
        # and rotate it on paper. Viewport rotation is counter-clockwise in a y-up
        # model; SVG's rotate() is clockwise in y-down paper, hence the sign.
        cx_mm, cy_mm = vb.point((xmin + xmax) / 2.0, (ymin + ymax) / 2.0)
        w_mm = vb.length_mm(xmax - xmin)
        h_mm = vb.length_mm(ymax - ymin)
        rot = float(getattr(vb, "rotation_deg", 0.0) or 0.0)
        transform = (
            f' transform="rotate({-rot:.3f} {cx_mm:.3f} {cy_mm:.3f})"' if rot else ""
        )
        return (
            f'<image href="{html.escape(href, quote=True)}" '
            f'x="{cx_mm - w_mm / 2.0:.3f}" y="{cy_mm - h_mm / 2.0:.3f}" '
            f'width="{w_mm:.3f}" height="{h_mm:.3f}" '
            f'preserveAspectRatio="none" opacity="{float(bd.opacity)}"{transform}/>'
        )
    return (
        f'<image href="{html.escape(href, quote=True)}" '
        f'x="{vb.x(xmin):.1f}" y="{vb.y(ymax):.1f}" '
        f'width="{vb.length(xmax - xmin):.1f}" height="{vb.length(ymax - ymin):.1f}" '
        f'preserveAspectRatio="none" opacity="{float(bd.opacity)}"/>'
    )


def _relative_href(bd: Backdrop, svg_dir: Path, *, allow_outside_svg_dir: bool) -> str:
    base = Path(svg_dir).expanduser().resolve()
    href = os.path.relpath(bd.image_path, base).replace(os.sep, "/")
    if href.split("/")[0] != "..":
        return href
    message = (
        f"backdrop '{_name(bd)}' href {href!r} escapes the SVG directory {base}: "
        f"{LIBRSVG_HREF_NOTE}. Emit the sheet from a directory at or above "
        f"{bd.image_path.parent}, or place the sidecar under {base}"
    )
    if not allow_outside_svg_dir:
        raise BackdropError(message)
    warnings.warn(message, stacklevel=3)
    return href


def _assert_image_current(bd: Backdrop) -> None:
    if not bd.image_path.exists():
        raise BackdropError(
            f"backdrop '{_name(bd)}' image is missing: {bd.image_path}; "
            "regenerate the geo manifest"
        )
    actual = _sha256_file(bd.image_path)
    if actual != bd.sha256:
        raise BackdropError(
            f"backdrop '{_name(bd)}' image digest mismatch: {actual} != {bd.sha256}; "
            "regenerate the geo manifest"
        )


def _is_paper_viewport(vb: Any) -> bool:
    """A paper-space ``technical_drawings_for_agents.sheet.Viewport`` (mm) rather than a pixel ``ViewBox``.

    Duck-typed on the two members the emit needs, so this module still imports
    nothing from ``sheet`` and the px/mm modules stay decoupled.
    """
    return hasattr(vb, "extent_m") and hasattr(vb, "point") and hasattr(vb, "length_mm")


def _assert_frame_matches(bd: Backdrop, vb: Any, tolerance_m: float) -> None:
    if _is_paper_viewport(vb):
        sheet = tuple(float(v) for v in vb.extent_m)
    else:
        sheet = (
            float(vb.real_min_x),
            float(vb.real_min_y),
            float(vb.real_max_x),
            float(vb.real_max_y),
        )
    labels = ("xmin", "ymin", "xmax", "ymax")
    for label, backdrop_value, sheet_value in zip(labels, bd.bbox, sheet):
        delta = abs(backdrop_value - sheet_value)
        if delta > tolerance_m:
            raise BackdropError(
                f"backdrop '{_name(bd)}' frame {label} {backdrop_value} != "
                f"sheet frame {label} {sheet_value} "
                f"(delta {delta:.3f} m > {tolerance_m:.3f} m); "
                "regenerate the geo manifest"
            )


def _find_output(raw: dict[str, Any], role: str) -> dict[str, Any] | None:
    outputs = raw.get("outputs")
    if not isinstance(outputs, list):
        return None
    for output in outputs:
        if isinstance(output, dict) and output.get("role") == role:
            return output
    return None


def _size_px(output: dict[str, Any], raw: dict[str, Any]) -> tuple[int, int]:
    size = output.get("size_px")
    if size is None:
        clip = _find_output(raw, "clip")
        size = clip.get("size_px") if clip else None
    if not isinstance(size, list | tuple) or len(size) != 2:
        raise BackdropError("backdrop output size_px must be a two-integer list")
    return int(size[0]), int(size[1])


def _bbox(raw: Any, where: str) -> tuple[float, float, float, float]:
    if not isinstance(raw, list | tuple) or len(raw) != 4:
        raise BackdropError(f"{where} must be a four-number list")
    return tuple(float(value) for value in raw)  # type: ignore[return-value]


def _required_mapping(data: dict[str, Any], key: str, where: str) -> dict[str, Any]:
    value = data.get(key)
    if not isinstance(value, dict):
        raise BackdropError(f"{where}.{key} must be a mapping")
    return value


def _required_str(data: dict[str, Any], key: str, where: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise BackdropError(f"{where}.{key} must be a non-empty string")
    return value


def _required_float(data: dict[str, Any], key: str, where: str) -> float:
    if key not in data:
        raise BackdropError(f"{where}.{key} is required")
    return float(data[key])


def _optional_float(data: dict[str, Any], key: str, default: float, where: str) -> float:
    if key not in data or data[key] is None:
        return default
    try:
        return float(data[key])
    except (TypeError, ValueError) as exc:
        raise BackdropError(f"{where}.{key} must be a number") from exc


def _optional_float_or_none(data: dict[str, Any], key: str, where: str) -> float | None:
    if key not in data or data[key] is None:
        return None
    return _optional_float(data, key, 0.0, where)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _name(bd: Backdrop) -> str:
    return bd.target or bd.image_path.stem
