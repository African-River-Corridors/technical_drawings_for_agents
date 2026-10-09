"""Supplier-DWG ingest — the ``.dwg``-in boundary.

`technical_drawings_for_agents` core is an *authoring* toolkit (parametric ``source.py`` →
SVG/DXF/PDF for drawings we draw ourselves). This module is the opposite
direction: take an existing supplier **DWG** and produce a clean, editable,
layout-insertable DXF. It generalises verified reference scripts (FA-130) into
project-agnostic helpers. The pipeline (verified end-to-end):

    DWG ─[convert_dwg / ODA]→ DXF ─[load_clean / ezdxf]→ doc ─[improve]→ clean doc
                                                                   │
                              (render for review with LibreOffice) ─┘

We **extract, never redraw** — the converter preserves the vendor's real
dimensions, so ingest honours the "never invent a dimension" rule.

Notes on the external tools:

* **ODA File Converter** (DWG↔DXF converter of record) is proprietary and not
  present on CI. ``convert_dwg`` locates it and skips cleanly when absent.
  LibreDWG's ``dwg2dxf`` is *not* used — its output is malformed.
* ODA runs headless on the **``cocoa``** Qt platform on macOS (``offscreen``
  is not bundled); folder-in / folder-out; filenames are ASCII-staged to dodge
  CJK-path issues.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import tempfile
from collections import Counter
from pathlib import Path

import ezdxf
from ezdxf import bbox, recover
from ezdxf.tools.text import plain_mtext

log = logging.getLogger("technical_drawings_for_agents.ingest")

# Text-bearing entity types (recoloured / consolidated during clean-up).
TEXT_TYPES = ("TEXT", "MTEXT", "ATTRIB", "ATTDEF")

# DXF $INSUNITS codes.
_UNIT_CODES = {"mm": 4, "cm": 5, "m": 6, "in": 1, "ft": 2, "unitless": 0}

# Title-block markers — bilingual strings that appear only inside a drawing's
# title block. Used to decide whether a gap-separated cluster is a *sheet*
# (has its own title block) or merely another view-group of one sheet.
TITLE_MARKERS = ("图号", "审定", "审核", "Approval", "Jinyi", "Edition No", "图名")

# Vendor CAD-template artifacts that render as stray text: title-block-generator
# tags left behind by the template's automation (e.g. ``!GENTITLE-INSERT``).
_TEMPLATE_TAG_RE = re.compile(r"(!?\s*GEN[-_ ]?TITLE)|GENST|^!\s*GEN", re.I)


# --------------------------------------------------------------------------
# 1. Convert — ODA File Converter wrapper
# --------------------------------------------------------------------------
def find_oda() -> str | None:
    """Locate the ODA File Converter executable, or return None.

    Honours ``ODA_CONVERTER``; then the macOS app bundle; then PATH.
    """
    override = os.environ.get("ODA_CONVERTER")
    if override and Path(override).exists():
        return override
    mac = Path("/Applications/ODAFileConverter.app/Contents/MacOS/ODAFileConverter")
    if mac.exists():
        return str(mac)
    for name in ("ODAFileConverter", "ODAFileConverter.exe"):
        found = shutil.which(name)
        if found:
            return found
    return None


def _ascii_name(path: Path, index: int) -> str:
    """An ASCII-safe ``*.dwg`` filename (ODA chokes on CJK paths)."""
    stem = path.stem.encode("ascii", "ignore").decode("ascii").strip()
    stem = "".join(c for c in stem if c.isalnum() or c in ("_", "-")) or f"dwg_{index}"
    return f"{stem}.dwg"


def convert_dwg(
    dwg_or_dir: str | Path,
    out_dir: str | Path,
    version: str = "ACAD2018",
    oda: str | None = None,
):
    """Convert a DWG (or a folder of DWGs) to clean DXF via ODA File Converter.

    Returns the clean DXF ``Path`` for a single-file input, or a list of Paths
    for a directory input. Raises ``FileNotFoundError`` if ODA is unavailable
    (callers/tests should guard with :func:`find_oda`).

    ODA is a folder-in/folder-out tool; we ASCII-stage the input filenames into
    a temp folder and run headless on the ``cocoa`` Qt platform.
    """
    oda = oda or find_oda()
    if oda is None:
        raise FileNotFoundError(
            "ODA File Converter not found. Install it (/Applications/"
            "ODAFileConverter.app) or set ODA_CONVERTER. It is proprietary and "
            "not available on CI."
        )

    src = Path(dwg_or_dir)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    single = src.is_file()
    if single:
        sources = [src]
    else:
        sources = sorted(
            p for p in src.iterdir() if p.suffix.lower() == ".dwg"
        )
        if not sources:
            raise FileNotFoundError(f"no .dwg files found in {src}")

    with tempfile.TemporaryDirectory() as stage:
        stage_dir = Path(stage)
        for i, d in enumerate(sources):
            shutil.copy(d, stage_dir / _ascii_name(d, i))

        env = {**os.environ, "QT_QPA_PLATFORM": "cocoa"}
        # ODAFileConverter <in_dir> <out_dir> <ver> <fmt> <recurse> <audit> [filter]
        cmd = [oda, str(stage_dir), str(out_dir), version, "DXF", "0", "1", "*.DWG"]
        log.info("Converting %d DWG(s) via ODA File Converter -> %s", len(sources), out_dir)
        subprocess.run(
            cmd, env=env, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=600
        )

    dxfs = sorted(p for p in out_dir.iterdir() if p.suffix.lower() == ".dxf")
    if not dxfs:
        raise RuntimeError(f"ODA produced no DXF in {out_dir}")
    return dxfs[0] if single else dxfs


# --------------------------------------------------------------------------
# 2. Load — ezdxf with recover fallback + audit
# --------------------------------------------------------------------------
def load_clean(dxf: str | Path):
    """Load a (converted) DXF; return ``(doc, audit)``.

    Uses ``ezdxf.readfile``; on failure falls back to ``ezdxf.recover.readfile``.
    ``audit`` reports entity/dimension/insert/text/block counts (+ a per-type
    breakdown) so ingest is auditable — we can see the vendor's real dimensions
    survived the conversion.
    """
    dxf = Path(dxf)
    try:
        doc = ezdxf.readfile(dxf)
    except Exception as exc:  # noqa: BLE001
        log.warning("readfile failed (%s); retrying with ezdxf.recover", exc)
        doc, auditor = recover.readfile(dxf)
        if auditor.has_errors:
            log.warning("recover: %d DXF error(s) fixed", len(auditor.errors))

    msp = doc.modelspace()
    by_type: Counter[str] = Counter(e.dxftype() for e in msp)
    audit = {
        "entities": len(msp),
        "dimensions": len(msp.query("DIMENSION")),
        "inserts": len(msp.query("INSERT")),
        "text": sum(by_type[t] for t in TEXT_TYPES),
        "block_definitions": sum(1 for _ in doc.blocks),
        "by_type": dict(by_type),
    }
    return doc, audit


# --------------------------------------------------------------------------
# 3. Recolour helpers (vendor drawings arrive in arbitrary ACI colours)
# --------------------------------------------------------------------------
def recolor(
    doc,
    to_aci: int,
    types: tuple[str, ...] | None = None,
    from_aci: int | None = None,
    include_blocks: bool = True,
) -> int:
    """Recolour entities to ``to_aci`` (an AutoCAD Color Index).

    Filter by ``types`` (dxftypes) and/or ``from_aci`` (only recolour entities
    currently that colour). Applies in modelspace and — when ``include_blocks``
    — inside every block definition (vendor text is often BYBLOCK / in blocks).
    Returns the number of entities recoloured.
    """
    count = 0

    def apply(container):
        nonlocal count
        for e in container:
            if types is not None and e.dxftype() not in types:
                continue
            if from_aci is not None and e.dxf.color != from_aci:
                continue
            e.dxf.color = to_aci
            count += 1

    apply(doc.modelspace())
    if include_blocks:
        for blk in doc.blocks:
            apply(blk)
    return count


def normalise_text(doc, to_aci: int = 7, include_blocks: bool = True) -> int:
    """Recolour all text entities to ``to_aci`` (default black = ACI 7).

    The common case: vendor text arrives blue/cyan (e.g. ACI 131) and reads
    badly on white. Applies in modelspace and inside block definitions.
    """
    return recolor(doc, to_aci=to_aci, types=TEXT_TYPES, include_blocks=include_blocks)


# --------------------------------------------------------------------------
# 3b. Strip vendor CAD-template artifacts
# --------------------------------------------------------------------------
def strip_tags(doc) -> dict:
    """Delete vendor CAD-template artifacts that render as stray text.

    Vendor drawings are drawn on a CAD template whose title-block *generator*
    leaves automation placeholders behind:

    * floating **ATTDEF** entities in modelspace — attribute *definitions*
      (placeholders), not real content;
    * **TEXT/MTEXT** that are title-block-generator tags — ``!GENTITLE-INSERT``,
      ``GEN-TITLE-*``, ``*GENST*`` — which render as literal stray text (caught
      on a vendor pump GA, where a literal ``!GENTITLE-INSERT`` was rendering).

    These are never real drawing content, so removing them invents nothing. Only
    modelspace is touched (block *definitions* are left intact). Returns an audit
    dict of what was stripped.
    """
    msp = doc.modelspace()
    attdefs = tags = 0
    for e in list(msp):
        t = e.dxftype()
        if t == "ATTDEF":
            msp.delete_entity(e)
            attdefs += 1
            continue
        if t in ("TEXT", "MTEXT"):
            s = e.dxf.text if t == "TEXT" else e.text
            if s and _TEMPLATE_TAG_RE.search(s):
                msp.delete_entity(e)
                tags += 1
    log.info("strip_tags: removed %d ATTDEF(s) + %d template tag(s)", attdefs, tags)
    return {"attdefs": attdefs, "template_tags": tags, "total": attdefs + tags}


# --------------------------------------------------------------------------
# 4. Improve — consolidate styles, set units/base, recolour text, set extents
# --------------------------------------------------------------------------
def improve(
    doc,
    font: str = "simhei.ttf",
    units: str = "mm",
    recolor_text_black: bool = True,
    set_extents: bool = True,
) -> dict:
    """In-place clean-up of a loaded vendor DXF (no geometry invented).

    * consolidate every text style to one clean TTF (Latin + CJK);
    * set ``$INSUNITS`` (from ``units``), ``$LUNITS=decimal``, ``$INSBASE=0``
      so the drawing inserts into a layout at true scale;
    * recolour text to black in modelspace **and inside block definitions**;
    * recompute ``$EXTMIN``/``$EXTMAX`` via ``ezdxf.bbox.extents``.

    Returns an audit dict of what changed.
    """
    msp = doc.modelspace()
    result: dict = {}

    unit_code = _UNIT_CODES.get(units)
    if unit_code is None:
        raise ValueError(f"unknown units {units!r}; expected one of {sorted(_UNIT_CODES)}")
    doc.header["$INSUNITS"] = unit_code
    doc.header["$LUNITS"] = 2  # decimal
    try:
        doc.header["$INSBASE"] = (0.0, 0.0, 0.0)
    except Exception as exc:  # noqa: BLE001
        log.debug("could not set $INSBASE: %s", exc)
    result["units"] = units

    n_styles = 0
    for s in doc.styles:
        s.dxf.font = font
        try:
            s.dxf.bigfont = ""
        except Exception:  # noqa: BLE001
            pass
        n_styles += 1
    result["styles_remapped"] = n_styles

    if recolor_text_black:
        result["text_recoloured"] = normalise_text(doc, to_aci=7)

    if set_extents:
        box = bbox.extents(msp, fast=True)
        if box.has_data:
            doc.header["$EXTMIN"] = (box.extmin.x, box.extmin.y, 0.0)
            doc.header["$EXTMAX"] = (box.extmax.x, box.extmax.y, 0.0)
            result["extents"] = (
                (box.extmin.x, box.extmin.y),
                (box.extmax.x, box.extmax.y),
            )
    return result


# --------------------------------------------------------------------------
# 5. Isolate a single sheet (multi-sheet tiled files)
# --------------------------------------------------------------------------
def _rep_point(e):
    """A representative (x, y) point for an entity, or None if unknown.

    For ``INSERT`` the point is the centre of the block's *rendered geometry*
    (``ezdxf.bbox.extents``), NOT the insert/definition point — vendor block
    insert-points can sit far from where the block renders (FA-130 manhole
    covers insert at y≈89025 while the geometry renders at the tank top), so an
    insert-point test would wrongly bin them and drop the block.
    """
    t = e.dxftype()
    try:
        if t == "LINE":
            s, en = e.dxf.start, e.dxf.end
            return ((s.x + en.x) / 2, (s.y + en.y) / 2)
        if t == "INSERT":
            try:
                c = bbox.extents([e]).center
                return (c.x, c.y)
            except Exception:  # noqa: BLE001
                return (e.dxf.insert.x, e.dxf.insert.y)
        if t in ("TEXT", "ATTRIB", "ATTDEF", "MTEXT"):
            return (e.dxf.insert.x, e.dxf.insert.y)
        if t in ("CIRCLE", "ARC", "ELLIPSE"):
            return (e.dxf.center.x, e.dxf.center.y)
        if t == "LWPOLYLINE":
            pts = e.get_points()
            return (pts[0][0], pts[0][1]) if pts else None
        if t == "SPLINE":
            cp = e.control_points
            return (cp[0].x, cp[0].y) if cp else None
        if t == "DIMENSION":
            return (e.dxf.defpoint.x, e.dxf.defpoint.y)
        if t == "POINT":
            return (e.dxf.location.x, e.dxf.location.y)
    except Exception:  # noqa: BLE001
        return None
    return None


def _entity_text(e) -> str:
    """Plain text of a TEXT/MTEXT entity (MTEXT inline codes stripped)."""
    t = e.dxftype()
    try:
        if t == "TEXT":
            return e.dxf.text or ""
        if t == "MTEXT":
            return plain_mtext(e.text) or ""
    except Exception:  # noqa: BLE001
        return ""
    return ""


def _has_title_block(cluster) -> bool:
    """True if any TEXT/MTEXT in the cluster carries a title-block marker."""
    return any(
        e.dxftype() in ("TEXT", "MTEXT") and any(m in _entity_text(e) for m in TITLE_MARKERS)
        for _, _, e in cluster
    )


def _english_score(cluster) -> int:
    """Count of ASCII-alpha characters in the cluster's text (English weight)."""
    return sum(
        sum(ch.isascii() and ch.isalpha() for ch in _entity_text(e))
        for _, _, e in cluster
        if e.dxftype() in ("TEXT", "MTEXT")
    )


def _split_by_largest_gap(pts, min_frac: float = 0.15):
    """Split points at the largest x-gap that yields two *substantial* clusters.

    Each side must hold ≥ ``min_frac`` of the points, so stray outliers can't
    define a spurious gap. Returns ``(clusters, split_x)`` where ``clusters`` is
    a list of one (no split) or two point-lists and ``split_x`` is the cut (or
    None). Pure-Python (no numpy) so it runs anywhere the core package installs.
    """
    n = len(pts)
    xs = sorted(p[0] for p in pts)
    # Candidate gaps, largest first.
    gaps = sorted(
        (xs[i + 1] - xs[i], (xs[i] + xs[i + 1]) / 2.0) for i in range(n - 1)
    )
    for _, thr in reversed(gaps):
        left = sum(1 for x in xs if x < thr)
        if min(left, n - left) >= min_frac * n:
            clusters = [
                [p for p in pts if p[0] < thr],
                [p for p in pts if p[0] >= thr],
            ]
            return clusters, thr
    return [list(pts)], None


def _crop_to_box(msp, target_pts, margin: float) -> dict:
    """Delete everything outside the padded bbox of ``target_pts``; reset extents."""
    tx = [p[0] for p in target_pts]
    ty = [p[1] for p in target_pts]
    minx, maxx, miny, maxy = min(tx), max(tx), min(ty), max(ty)
    mx, my = (maxx - minx) * margin, (maxy - miny) * margin
    box = (minx - mx, miny - my, maxx + mx, maxy + my)

    kept = deleted = unknown = 0
    for e in list(msp):
        pt = _rep_point(e)
        if pt is None:
            unknown += 1
            continue
        if box[0] <= pt[0] <= box[2] and box[1] <= pt[1] <= box[3]:
            kept += 1
        else:
            msp.delete_entity(e)
            deleted += 1
    return {"box": box, "kept": kept, "deleted": deleted, "kept_unknown": unknown}


def isolate_sheet(
    doc,
    window: tuple[float, float, float, float] | None = None,
    margin: float = 0.03,
    min_cluster_frac: float = 0.15,
) -> dict:
    """Isolate a single sheet from a file that tiles several — but **keep a
    single sheet whole** when it merely has gapped view-groups.

    Two modes:

    * **Adaptive (default, ``window=None``)** — sheet-count-aware. Split the
      drawing at the largest x-gap that yields two *substantial* clusters (each
      ≥ ``min_cluster_frac`` of entities, so a stray point can't define a
      spurious gap). A cluster counts as a *sheet* only if it carries a
      title-block marker (see :data:`TITLE_MARKERS`). **Isolate only when ≥2
      such title-bearing clusters exist**, keeping the one with the most English
      (ASCII-alpha) text. Otherwise the whole drawing is kept.

      This fixes the dropped-view bug: a single sheet with several view-groups
      (e.g. vendor pump GAs — one sheet, multiple views + a plan) also
      gap-splits, but the extra group has no title block. A naive gap-split
      bisected it and dropped a view. Erring toward keep-all is the safe failure
      mode — ingest never silently drops views.

    * **Legacy (``window`` given)** — keep the tight bbox of entities inside the
      guard box ``(xmin, ymin, xmax, ymax)``; delete the rest.

    Entity positions use each entity's real geometry (``_rep_point`` resolves
    INSERT via ``bbox.extents``). Entities with no representative point (HATCH
    etc.) are kept. Resets ``$EXTMIN``/``$EXTMAX`` to the kept box. Returns an
    audit dict; the adaptive path adds ``mode``/``clusters``/``title_clusters``.
    """
    msp = doc.modelspace()

    # ---- Legacy window path (explicit guard box) ------------------------
    if window is not None:
        wx0, wy0, wx1, wy1 = window
        inside = [
            pt
            for e in msp
            for pt in [_rep_point(e)]
            if pt is not None and wx0 < pt[0] < wx1 and wy0 < pt[1] < wy1
        ]
        if not inside:
            raise ValueError("no entities found inside the guard window")
        result = _crop_to_box(msp, inside, margin)
        box = result["box"]
        doc.header["$EXTMIN"] = (box[0], box[1], 0.0)
        doc.header["$EXTMAX"] = (box[2], box[3], 0.0)
        return result

    # ---- Adaptive sheet-count-aware path --------------------------------
    pts = [(pt[0], pt[1], e) for e in msp for pt in [_rep_point(e)] if pt is not None]
    if not pts:
        raise ValueError("no positionable entities to isolate")

    clusters, split_x = _split_by_largest_gap(pts, min_frac=min_cluster_frac)
    title_clusters = [c for c in clusters if _has_title_block(c)]

    if len(clusters) > 1 and len(title_clusters) >= 2:
        # Genuinely two (or more) sheets: keep the most-English title-bearing one.
        target = max(title_clusters, key=_english_score)
        mode = "isolate"
    else:
        # Single sheet (possibly with gapped view-groups): keep the whole drawing.
        target = [p for c in clusters for p in c]
        mode = "keep_all"

    result = _crop_to_box(msp, target, margin)
    box = result["box"]
    doc.header["$EXTMIN"] = (box[0], box[1], 0.0)
    doc.header["$EXTMAX"] = (box[2], box[3], 0.0)
    result.update(
        {
            "mode": mode,
            "clusters": len(clusters),
            "title_clusters": len(title_clusters),
            "split_x": None if split_x is None else round(split_x, 3),
            "scores": [_english_score(c) for c in clusters],
        }
    )
    log.info(
        "isolate_sheet: %s (clusters=%d, title_clusters=%d); kept %d, deleted %d",
        mode, len(clusters), len(title_clusters), result["kept"], result["deleted"],
    )
    return result


# --------------------------------------------------------------------------
# 6. Crop a rendered PDF page to its content
# --------------------------------------------------------------------------
def crop_pdf(pdf: str | Path, out: str | Path, margin: float = 10.0, zoom: int = 3) -> Path:
    """Crop the first page of ``pdf`` tight to its non-white content.

    Rasterises the page, finds the non-white bounding box, and sets the page
    crop-box. Requires PyMuPDF + numpy (the ``ingest`` optional dependency).
    """
    try:
        import fitz  # PyMuPDF
        import numpy as np
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "crop_pdf needs PyMuPDF and numpy: pip install 'technical_drawings_for_agents[ingest]'"
        ) from exc

    pdf = Path(pdf)
    out = Path(out)
    doc = fitz.open(pdf)
    page = doc[0]
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    gray = img[..., :3].mean(axis=2)
    ys, xs = np.where(gray < 250)
    if len(xs) == 0:
        raise ValueError(f"{pdf.name}: page is blank — nothing to crop")
    rect = fitz.Rect(
        xs.min() / zoom - margin,
        ys.min() / zoom - margin,
        xs.max() / zoom + margin,
        ys.max() / zoom + margin,
    ) & page.rect
    page.set_cropbox(rect)
    doc.save(out)
    doc.close()
    return out


# --------------------------------------------------------------------------
# 7. Corrections overlay — versioned, sourced value corrections
# --------------------------------------------------------------------------
# The vendor DWG is the untouched boundary source. Any edit we make is captured
# as a git-tracked, reviewable *data overlay* (a YAML file in the project folder
# beside the drawing), applied by this code AFTER clean/isolate and BEFORE
# render. This keeps the "text is the source of truth" ethos: a ``git diff`` of
# the overlay is the change log.
#
# Schema (``corrections.yaml``)::
#
#     drawing: "PT-500 flocculant dosing device"   # human label
#     source:  "絮凝剂加药装置PT-500.dwg"            # the untouched vendor file
#     corrections:
#       - {op: replace_text, find: "30L/h、50m、60w", with: "30 L/h, 50 m, 60 W",
#          note: "normalise Chinese comma + unit case (no value change)"}
#       - {op: add_text, at: [7595, 13860], text: "…", height: 120, layer: TEXT,
#          note: "item-3 model from vendor packing list p.2 — VERIFY"}
#       - {op: hide, near: [1234, 5678], note: "stray template dot"}
#
# Ops:
#   * ``replace_text {find, with}``  — translate / normalise existing text.
#   * ``add_text {at:[x,y], text, height?, layer?, color?, rotation?}`` — fill a
#     blank cell / add a note, in real drawing-unit coords.
#   * ``hide {near:[x,y]}``          — delete the nearest entity (drop a stray).
#
# **Never invent a value.** Every op is explicit human-authored data and MUST
# carry a ``note`` recording intent/provenance. ``add_text`` is the
# invention-risky op (it introduces a value that was not in the vendor file), so
# its ``note`` is *required* and must cite the real source (packing list /
# supplier confirmation) and flag ``VERIFY`` where the value is unconfirmed —
# mirroring the "drawing-verification item" rule. No geometry is ever fabricated.
#
# Pipeline order: run MTEXT→TEXT normalisation (if used) *before* corrections so
# ``replace_text`` matches plain strings, and corrections *before* render.

_CORRECTION_TEXT_TYPES = ("TEXT", "MTEXT")


def _set_text(e, value: str) -> None:
    if e.dxftype() == "TEXT":
        e.dxf.text = value
    else:
        e.text = value


def apply_corrections(doc, corrections) -> dict:
    """Apply a corrections overlay to a loaded DXF ``doc`` in place.

    ``corrections`` is either a path to a ``corrections.yaml`` file, a loaded
    spec dict (``{"corrections": [...]}``), or a bare list of op dicts. Returns
    an audit dict ``{"applied": [...], "count": n}``.

    Enforces "never invent a value": ``add_text`` requires a non-empty ``note``
    citing the source (raises ``ValueError`` otherwise). Unknown ops raise.
    """
    if isinstance(corrections, (str, Path)):
        import yaml

        spec = yaml.safe_load(Path(corrections).read_text(encoding="utf-8")) or {}
        ops = spec.get("corrections", [])
    elif isinstance(corrections, dict):
        ops = corrections.get("corrections", [])
    else:
        ops = list(corrections)

    msp = doc.modelspace()
    applied: list[str] = []

    def texts():
        return [e for e in msp if e.dxftype() in _CORRECTION_TEXT_TYPES]

    for c in ops:
        op = c.get("op")
        if op == "replace_text":
            find, repl = c["find"], c["with"]
            hits = 0
            for e in texts():
                s = e.dxf.text if e.dxftype() == "TEXT" else e.text
                if s and find in s:
                    _set_text(e, s.replace(find, repl))
                    hits += 1
            applied.append(f"replace_text {find!r}->{repl!r} x{hits}")
        elif op == "add_text":
            note = (c.get("note") or "").strip()
            if not note:
                raise ValueError(
                    "add_text correction must carry a 'note' citing the value's "
                    "source (never invent a value): " + repr(c)
                )
            x, y = c["at"]
            msp.add_text(
                c["text"],
                height=c.get("height", 100),
                dxfattribs={
                    "layer": c.get("layer", "TEXT"),
                    "color": c.get("color", 7),
                    "rotation": c.get("rotation", 0),
                },
            ).set_placement((x, y))
            applied.append(f"add_text {c['text']!r} @({x},{y}) [{note}]")
        elif op == "hide":
            x, y = c["near"]
            best, best_d = None, float("inf")
            for e in msp:
                pt = _rep_point(e)
                if pt is None:
                    continue
                d = (pt[0] - x) ** 2 + (pt[1] - y) ** 2
                if d < best_d:
                    best, best_d = e, d
            if best is not None:
                msp.delete_entity(best)
                applied.append(f"hide near ({x},{y})")
        else:
            raise ValueError(f"unknown correction op {op!r}")

    log.info("apply_corrections: applied %d correction(s)", len(applied))
    for a in applied:
        log.info("  - %s", a)
    return {"applied": applied, "count": len(applied)}
