"""LEGACY render path. Frozen behaviour — use :mod:`technical_drawings_for_agents.plot` instead.

The source of truth is text (a ``source.py`` generator, an SVG, or an ASCII
DXF). Humans review the *rendered sheet*, not the geometry text — so this
module turns a source into PDF/PNG.

**Correction (measured 2026-07-24, ezdxf 1.4.4; this docstring previously
asserted the opposite).** The routing below is backwards, and it is tracked as a
live defect. Rendering ``tests/synthetic.py::make_dxf_with_insert`` through both
backends and rasterising at 200 dpi:

* the **ezdxf/matplotlib** backend is **faithful** — it traverses into blocks,
  draws every child, and attributes each op to the ``INSERT``'s own handle (4
  recorded draw ops on that fixture);
* **LibreOffice** ``--convert-to pdf`` **loses every line, circle and polyline**
  and stacks the surviving text: 705 non-white pixels of an 842x596 page. It is
  also non-deterministic (differing sha256 across identical runs; it embeds
  ``/CreationDate`` and ``/ID`` and ignores ``SOURCE_DATE_EPOCH``).

So ``select_backend`` routes block-heavy *vendor* drawings to the **worse**
backend, and the "falls back to matplotlib with a warning" degrade path actually
improves the output. Two further silent-loss mechanisms live here: ``render_dxf``
plots ``doc.modelspace()`` only, so a **paper-space** drawing yields a blank PDF
at exit 0; and ``render_py`` globs ``*.dxf`` only, so an SVG a generator wrote
never reaches a PDF.

None of that is fixed here **on purpose**: this module's observable behaviour is
frozen for backward compatibility, and ``tests/test_render_routing.py`` pins the
routing. :mod:`technical_drawings_for_agents.plot` is the replacement — a pure-Python
``SVGBackend`` -> ``rsvg-convert`` chain on a real paper size, with a fidelity
check that refuses to write a canonically-named artifact it cannot prove
complete. Retiring the LibreOffice route and repointing ``render`` at ``plot``
is a separate, parity-tested change.

Rendering strategy as it stands (all headless, CI-friendly):

* ``.py``  — executed in-process; it writes its ``out/`` artifacts. Any DXF it
  produced is then rendered to PDF/PNG. SVGs are **not** rendered.
* ``.dxf`` — rendered directly; the backend is chosen by content
  (``INSERT``-bearing -> LibreOffice if available, else matplotlib), and the
  choice is logged. Choosing LibreOffice now also emits a ``DeprecationWarning``
  pointing at ``technical_drawings_for_agents plot``. The artifacts are byte-for-byte unchanged.
* ``.svg`` — rasterised via ``cairosvg`` if installed; else LibreOffice; else a
  sibling DXF is rendered instead (the SVG remains viewable in any browser).
"""

from __future__ import annotations

import logging
import os
import runpy
import shutil
import subprocess
import sys
import tempfile
import warnings
from contextlib import contextmanager
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

import ezdxf  # noqa: E402
from ezdxf.addons.drawing import RenderContext, Frontend  # noqa: E402
from ezdxf.addons.drawing.matplotlib import MatplotlibBackend  # noqa: E402

log = logging.getLogger("technical_drawings_for_agents.render")


# --------------------------------------------------------------------------
# Backend discovery + selection
# --------------------------------------------------------------------------
def find_soffice() -> str | None:
    """Locate the LibreOffice ``soffice`` binary, or return None.

    Honours ``SOFFICE_BIN``; then PATH (``soffice`` / ``libreoffice``); then the
    macOS app-bundle location.
    """
    override = os.environ.get("SOFFICE_BIN")
    if override and Path(override).exists():
        return override
    for name in ("soffice", "libreoffice"):
        found = shutil.which(name)
        if found:
            return found
    mac = Path("/Applications/LibreOffice.app/Contents/MacOS/soffice")
    if mac.exists():
        return str(mac)
    return None


def dxf_has_inserts(doc) -> bool:
    """True if the DXF modelspace contains any block ``INSERT`` entities."""
    return len(doc.modelspace().query("INSERT")) > 0


def select_backend(has_inserts: bool, soffice_available: bool) -> str:
    """Pure routing decision. Returns ``"libreoffice"``, ``"matplotlib"``.

    Block-insert drawings want LibreOffice; born-as-code sheets want matplotlib.
    If LibreOffice is wanted but unavailable we degrade to matplotlib (the
    caller logs a warning) so rendering never hard-fails.
    """
    if has_inserts and soffice_available:
        return "libreoffice"
    return "matplotlib"


# --------------------------------------------------------------------------
# Backends
# --------------------------------------------------------------------------
@contextmanager
def _source_date_epoch(policy):
    if policy is None or policy.source_date_epoch is None:
        yield
        return
    previous = os.environ.get("SOURCE_DATE_EPOCH")
    os.environ["SOURCE_DATE_EPOCH"] = str(policy.source_date_epoch)
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("SOURCE_DATE_EPOCH", None)
        else:
            os.environ["SOURCE_DATE_EPOCH"] = previous


def _render_dxf_matplotlib(doc, out_dir: Path, stem: str, policy=None) -> list[Path]:
    msp = doc.modelspace()
    outputs = []
    with _source_date_epoch(policy):
        for ext in ("pdf", "png"):
            fig = plt.figure()
            ax = fig.add_axes([0, 0, 1, 1])
            ctx = RenderContext(doc)
            out_backend = MatplotlibBackend(ax)
            Frontend(ctx, out_backend).draw_layout(msp, finalize=True)
            target = out_dir / f"{stem}.{ext}"
            fig.savefig(target, dpi=150)
            plt.close(fig)
            outputs.append(target)
    return outputs


def _render_dxf_libreoffice(
    dxf_path: Path, out_dir: Path, stem: str, soffice: str
) -> list[Path]:
    """Render a DXF to PDF + PNG using headless LibreOffice.

    LibreOffice loads block INSERTs faithfully (unlike the matplotlib backend).
    Output files are named after the input stem by soffice; we rename to
    ``stem`` if it differs.
    """
    outputs: list[Path] = []
    with tempfile.TemporaryDirectory() as profile:
        for fmt in ("pdf", "png"):
            cmd = [
                soffice,
                "--headless",
                "--nologo",
                "--nofirststartwizard",
                f"-env:UserInstallation=file://{profile}",
                "--convert-to",
                fmt,
                "--outdir",
                str(out_dir),
                str(dxf_path),
            ]
            subprocess.run(
                cmd,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=180,
            )
            produced = out_dir / f"{dxf_path.stem}.{fmt}"
            target = out_dir / f"{stem}.{fmt}"
            if produced != target and produced.exists():
                produced.replace(target)
            if not target.exists():
                raise RuntimeError(
                    f"LibreOffice did not produce {target.name} from {dxf_path.name}"
                )
            outputs.append(target)
    return outputs


def render_dxf(
    dxf_path: str | Path,
    out_dir: str | Path,
    stem: str | None = None,
    backend: str = "auto",
    policy=None,
) -> list[Path]:
    """Render a DXF file to PDF and PNG in ``out_dir``. Returns the paths.

    ``backend`` is ``"auto"`` (choose by content — the default and the fix),
    ``"matplotlib"``, or ``"libreoffice"``. Auto routes block-insert drawings to
    LibreOffice and born-as-code sheets to matplotlib; the choice is logged.
    """
    dxf_path = Path(dxf_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = stem or dxf_path.stem

    doc = ezdxf.readfile(dxf_path)
    n_inserts = len(doc.modelspace().query("INSERT"))
    has_inserts = n_inserts > 0
    soffice = find_soffice()

    if backend == "auto":
        chosen = select_backend(has_inserts, soffice is not None)
        if has_inserts and soffice is None:
            log.warning(
                "%s has %d block INSERT(s) but LibreOffice (soffice) was not found; "
                "falling back to the matplotlib backend, which drops block-insert "
                "detail — install LibreOffice to render this drawing faithfully.",
                dxf_path.name,
                n_inserts,
            )
    else:
        chosen = backend
        if chosen == "libreoffice" and soffice is None:
            raise RuntimeError(
                "backend='libreoffice' requested but soffice was not found "
                "(set SOFFICE_BIN or install LibreOffice)."
            )

    log.info(
        "Rendering %s via %s backend (%d INSERT entities detected).",
        dxf_path.name,
        chosen,
        n_inserts,
    )

    if chosen == "libreoffice":
        if backend == "auto":
            # A warning is not a behaviour change: the artifacts are identical. It is
            # the only signal this module is allowed to add, because the LibreOffice
            # DXF filter is measurably lossy (see the module docstring).
            warnings.warn(
                f"render_dxf routed {dxf_path.name} to the LibreOffice DXF backend, which "
                "drops all vector geometry and is non-deterministic (measured 2026-07-24). "
                "Use `technical_drawings_for_agents plot` (technical_drawings_for_agents.plot) for a fidelity-checked sheet.",
                DeprecationWarning,
                stacklevel=2,
            )
            log.info(
                "The LibreOffice DXF route is deprecated; `technical_drawings_for_agents plot %s` renders "
                "it faithfully and checks the result.",
                dxf_path,
            )
        return _render_dxf_libreoffice(dxf_path, out_dir, stem, soffice)  # type: ignore[arg-type]
    return _render_dxf_matplotlib(doc, out_dir, stem, policy=policy)


def _render_svg_libreoffice(svg_path: Path, out_dir: Path, stem: str, soffice: str) -> list[Path]:
    """Render an SVG to PDF + PNG using headless LibreOffice.

    Serves the born-as-code *schematic* sheets (bfd / pid), whose source is an
    SVG rather than a DXF, so they get a PDF/PNG review artifact on CI (which
    ships LibreOffice) without a cairosvg/Cairo system dependency.
    """
    outputs: list[Path] = []
    with tempfile.TemporaryDirectory() as profile:
        for fmt in ("pdf", "png"):
            cmd = [
                soffice, "--headless", "--nologo", "--nofirststartwizard",
                f"-env:UserInstallation=file://{profile}",
                "--convert-to", fmt, "--outdir", str(out_dir), str(svg_path),
            ]
            subprocess.run(
                cmd,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=180,
            )
            produced = out_dir / f"{svg_path.stem}.{fmt}"
            target = out_dir / f"{stem}.{fmt}"
            if produced != target and produced.exists():
                produced.replace(target)
            if not target.exists():
                raise RuntimeError(
                    f"LibreOffice did not produce {target.name} from {svg_path.name}"
                )
            outputs.append(target)
    return outputs


def render_svg(
    svg_path: str | Path,
    out_dir: str | Path,
    stem: str | None = None,
    policy=None,
) -> list[Path]:
    """Rasterise an SVG to PDF/PNG.

    Backend order: **cairosvg** if installed (lightest); else headless
    **LibreOffice** (``soffice`` — present on CI, imports SVG and exports
    PDF/PNG); else a sibling ``.dxf`` if one sits next to the SVG. Raises
    RuntimeError only if none of those is possible (the SVG itself is always
    directly viewable in any browser).
    """
    svg_path = Path(svg_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = stem or svg_path.stem

    try:
        import cairosvg  # type: ignore
    except Exception:
        cairosvg = None

    if cairosvg is not None:
        pdf = out_dir / f"{stem}.pdf"
        png = out_dir / f"{stem}.png"
        cairosvg.svg2pdf(url=str(svg_path), write_to=str(pdf))
        cairosvg.svg2png(url=str(svg_path), write_to=str(png), output_width=1400)
        return [pdf, png]

    soffice = find_soffice()
    if soffice is not None:
        log.info("Rendering %s via LibreOffice backend (SVG -> PDF/PNG).", svg_path.name)
        return _render_svg_libreoffice(svg_path, out_dir, stem, soffice)

    sibling = svg_path.with_suffix(".dxf")
    if sibling.exists():
        return render_dxf(sibling, out_dir, stem=stem, policy=policy)

    raise RuntimeError(
        "cannot rasterise SVG: install the 'cairosvg' package or LibreOffice "
        f"(soffice), or place a sibling DXF next to {svg_path.name} to render "
        "instead. The SVG itself is directly viewable in any browser."
    )


def render_py(py_path: str | Path, out_dir: str | Path, policy=None) -> list[Path]:
    """Execute a source.py generator, then render any DXF it produced.

    The generator is expected to write its artifacts (SVG/DXF) into its own
    ``out/`` directory. We run it in-process via runpy with its directory on
    ``sys.path`` so it can import a local ``inputs``/helpers if needed.
    """
    py_path = Path(py_path).resolve()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    added = str(py_path.parent)
    inserted = added not in sys.path
    if inserted:
        sys.path.insert(0, added)
    try:
        runpy.run_path(str(py_path), run_name="__main__")
    finally:
        if inserted:
            sys.path.remove(added)

    # Render every DXF the generator emitted into out_dir.
    outputs = []
    dxfs = sorted(out_dir.glob("*.dxf"))
    for dxf in dxfs:
        outputs.extend(render_dxf(dxf, out_dir, stem=dxf.stem, policy=policy))
    return outputs


def render_source(source: str | Path, out_dir: str | Path, policy=None) -> list[Path]:
    """Dispatch on the source extension. Returns rendered artifact paths."""
    source = Path(source)
    suffix = source.suffix.lower()
    if suffix == ".py":
        return render_py(source, out_dir, policy=policy)
    if suffix == ".dxf":
        return render_dxf(source, out_dir, policy=policy)
    if suffix == ".svg":
        return render_svg(source, out_dir, policy=policy)
    raise ValueError(f"unsupported source type '{suffix}' (expect .py, .svg or .dxf)")
