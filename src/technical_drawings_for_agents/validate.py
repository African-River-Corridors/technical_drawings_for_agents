"""Validation checks for a generated drawing sheet.

Enforces the standard's "every sheet carries" rule. For a **scale-true** sheet
(civil section / GA) that is a **title block**, a **scale bar**, and a **status
watermark**. For a **schematic** — a BFD or an ISA-5.1 **P&ID** — a scale bar is
meaningless (the drawing is NTS), so only the title block + status watermark are
required.

For a P&ID the data model is validated too (via :mod:`technical_drawings_for_agents.pid`): every
symbol has a tag, and every line has a known type and resolves to declared
ports. Also validates ``meta.yaml`` if present (including the for-construction /
ISSUED safety gate).

A **paper-space sheet** (:mod:`technical_drawings_for_agents.sheet`) additionally describes itself in
its own output, so :func:`check_sheet_svg` can recompute every scale claim from that
record — validation works on *files* and must never re-run a generator. Those checks
are gated on the record being present: a legacy sheet cannot acquire a new problem or
a new warning. New checks only ever **append**; nothing here can clear a problem
raised by ``DrawingMeta.validate()``, which remains the sole authority on the
for-construction / ISSUED gate.
"""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .layers import LayerTable, resolve_layer_table
from .meta import DrawingMeta
from .sheet import (
    ISO_PREFERRED_DENOMINATORS,
    PAPER_SIZES_MM,
    SHEET_METADATA_ID,
    SheetError,
    parse_sheet_data_value,
    read_sheet_data_attr,
    read_sheet_metadata,
    scale_bar_tick_label,
)

# Markers emitted by the toolkit's furniture helpers.
_MARK = {
    "title block": 'class="title-block"',
    "scale bar": 'class="scale-bar"',
    "status watermark": 'class="status-watermark"',
}
# Scale-true engineered sheets carry all three.
REQUIRED_MARKERS = dict(_MARK)
# Schematics (BFD / P&ID) are NTS — no scale bar.
SCHEMATIC_MARKERS = {k: v for k, v in _MARK.items() if k != "scale bar"}


@dataclass
class ValidationResult:
    target: Path
    problems: list[str] = field(default_factory=list)
    checked: list[str] = field(default_factory=list)
    #: Non-fatal notices. ``ok`` ignores them; ``validate --strict`` promotes them.
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def validate_svg(
    svg_path: str | Path,
    require: dict | None = None,
    meta: DrawingMeta | None = None,
    *,
    legibility: bool = False,
    strict: bool = False,
) -> ValidationResult:
    svg_path = Path(svg_path)
    result = ValidationResult(target=svg_path)
    if not svg_path.exists():
        result.problems.append(f"SVG not found: {svg_path}")
        return result
    text = svg_path.read_text(encoding="utf-8")
    for name, marker in (require or REQUIRED_MARKERS).items():
        result.checked.append(name)
        if marker not in text:
            result.problems.append(f"missing {name} (expected marker {marker!r})")
    if SHEET_METADATA_ID in text:
        result.checked.append("sheet record (paper size, plot scale, scale bar, north)")
    problems, warnings = check_sheet_svg(text, meta=meta)
    result.problems.extend(problems)
    result.warnings.extend(warnings)
    problems, warnings = check_revision_titleblock_svg(text, meta=meta)
    result.problems.extend(problems)
    result.warnings.extend(warnings)
    if legibility:
        from .legibility import check_svg

        report = check_svg(svg_path, meta=meta)
        result.checked.extend(f"legibility: {check}" for check in report.checked)
        result.problems.extend(str(finding) for finding in report.errors)
        result.warnings.extend(str(finding) for finding in report.warnings)
    return result


def check_revision_titleblock_svg(
    svg_text: str, meta: DrawingMeta | None = None
) -> tuple[list[str], list[str]]:
    """Check an emitted ISO 7200 title block and revision table (P9 / #61).

    Returns ``(problems, warnings)``. Gated on the P9 markup being present, so a
    legacy sheet — and every sheet in this repo before this change — gets
    ``([], [])`` and cannot acquire a new problem or a new warning. New checks only
    ever append.

    Three checks, none of which needs a rasteriser or a margin convention:

    * **V8** — the rendered table's ``data-latest-rev`` equals ``meta.revision``, so
      the sheet in someone's hand and the register in the file agree;
    * **V9** — the block lies on the paper implied by ``data-sheet`` /
      ``data-orientation``. Deliberately checked against the **paper** rectangle,
      not against a frame: the frame needs a margin set, and the ISO 5457 margin
      convention is an open question (O-3). Asserting containment inside an
      invented margin set would be inventing the standard here;
    * **V10** — the revision table is flush to the title block: same width, same
      right edge, and its bottom edge is the block's top edge. That is the layout
      contract, and it is checkable from the emitted attributes alone.
    """
    if 'class="revision-block"' not in svg_text and 'class="title-block"' not in svg_text:
        return [], []
    if "data-extents-mm" not in svg_text and "data-latest-rev" not in svg_text:
        # A legacy pixel-space title block: no P9 attributes, nothing to check.
        return [], []
    try:
        root = ET.fromstring(svg_text)
    except ET.ParseError as exc:
        return [f"title-block: SVG is not parseable XML: {exc}"], []

    problems: list[str] = []
    warnings: list[str] = []

    revision_blocks = _all_by_class(root, "revision-block")
    for block in revision_blocks:
        latest = block.attrib.get("data-latest-rev")
        if latest is None:
            problems.append('title-block: <g class="revision-block"> has no data-latest-rev')
        elif meta is not None and latest != str(meta.revision).strip().upper():
            problems.append(
                f"meta: the rendered revision block states rev {latest!r} but "
                f"meta.revision is {str(meta.revision)!r} — re-render the sheet"
            )

    for block in _all_by_class(root, "title-block"):
        extents = _parse_extents(block.attrib.get("data-extents-mm"))
        if extents is None:
            continue
        sheet = block.attrib.get("data-sheet")
        orientation = block.attrib.get("data-orientation")
        if sheet is None or orientation is None:
            problems.append(
                "title-block: data-extents-mm is present but data-sheet / "
                "data-orientation are missing"
            )
            continue
        paper = _paper_rect_mm(sheet, orientation)
        if paper is None:
            problems.append(
                f"title-block: data-sheet={sheet!r} data-orientation={orientation!r} "
                f"do not name a supported ISO 216 size (one of "
                f"{', '.join(PAPER_SIZES_MM)}) and an orientation"
            )
            continue
        overflow = _frame_overflow(_bbox(extents), paper)
        if overflow > 1e-6:
            problems.append(
                f"title-block: data-extents-mm {_fmt_extents(extents)} lies "
                f"{overflow:.3f} mm outside the {sheet} {orientation} paper"
            )
        problems.extend(_check_revision_block_alignment(extents, revision_blocks))

    return problems, warnings


def _check_revision_block_alignment(
    title_extents: tuple[float, float, float, float],
    revision_blocks: list[ET.Element],
) -> list[str]:
    """V10: the revision table is the block's width, flush right, sitting on top of it."""
    problems: list[str] = []
    tx, ty, tw, _th = title_extents
    for block in revision_blocks:
        extents = _parse_extents(block.attrib.get("data-extents-mm"))
        if extents is None:
            continue
        rx, ry, rw, rh = extents
        if not _close(rw, tw):
            problems.append(
                f"title-block: the revision block is {rw:.3f} mm wide but the title "
                f"block is {tw:.3f} mm — they must share a width"
            )
        if not _close(rx + rw, tx + tw):
            problems.append(
                "title-block: the revision block's right edge does not coincide with "
                "the title block's"
            )
        if not _close(ry + rh, ty):
            problems.append(
                f"title-block: the revision block's bottom edge is at {ry + rh:.3f} mm "
                f"but the title block's top edge is at {ty:.3f} mm"
            )
    return problems


def check_sheet_svg(
    svg_text: str, meta: DrawingMeta | None = None
) -> tuple[list[str], list[str]]:
    """Recompute a paper-space sheet's scale claims from its own emitted record.

    Returns ``(problems, warnings)``. A file with no sheet record is a legacy sheet and
    gets ``([], [])`` — nothing about legacy validation changes.

    A stated scale that contradicts the viewport (V8) is a **problem**, not a warning:
    a sheet is measured off, and a warning is a thing CI prints and humans stop reading.
    Containment (V9) and non-preferred ratios (V10) are warnings, because annotation
    legitimately reaches a few millimetres past a plan extent and because a tool that
    refuses 1:1250 — the scale the job actually uses — gets worked around.
    """
    try:
        record = read_sheet_metadata(svg_text)
    except SheetError as exc:
        return [f"sheet: {exc}"], []
    if record is None:
        return [], []

    try:
        root = ET.fromstring(svg_text)
    except ET.ParseError as exc:
        return [f"sheet: SVG is not parseable XML: {exc}"], []

    problems: list[str] = []
    warnings: list[str] = []

    paper = record["paper"]
    width_mm = float(paper["width_mm"])
    height_mm = float(paper["height_mm"])

    # V1 — the root carries real paper units matching the recorded size.
    _check_root_size(root, "width", width_mm, problems)
    _check_root_size(root, "height", height_mm, problems)

    # V2 — the viewBox is the paper box, so one user unit is one millimetre.
    view_box = root.attrib.get("viewBox", "")
    if not _viewbox_matches(view_box, width_mm, height_mm):
        problems.append(
            f"sheet: viewBox {view_box!r} does not match the {paper['name']} "
            f"{paper['orientation']} paper box "
            f"'0 0 {_fmt_g(width_mm)} {_fmt_g(height_mm)}'"
        )

    # V3 — the record is self-consistent about its own scale. If it is not, every
    # downstream recomputation is meaningless, so stop here.
    scale = record["scale"]
    denominator = int(scale["denominator"])
    scale_text = f"1:{denominator}"
    if scale.get("text") != scale_text:
        problems.append(
            f"sheet: recorded scale text {scale.get('text')!r} disagrees with "
            f"denominator {denominator}"
        )
        return problems, warnings
    if not _close(float(scale["mm_per_m"]), 1000.0 / denominator):
        problems.append(
            f"sheet: recorded mm_per_m {float(scale['mm_per_m']):.6f} disagrees with "
            f"denominator {denominator} (1000/{denominator} = "
            f"{1000.0 / denominator:.6f})"
        )
        return problems, warnings

    viewport = record["viewport"]
    extent = tuple(float(v) for v in viewport["extent_m"])
    rotation_deg = float(viewport["rotation_deg"])
    frame = tuple(float(v) for v in viewport["frame_mm"])
    required = _required_mm(extent, rotation_deg, denominator)

    # V4 — the recorded requirement is what the extent actually needs.
    recorded_required = tuple(float(v) for v in viewport["required_mm"])
    if not (
        _close(required[0], recorded_required[0])
        and _close(required[1], recorded_required[1])
    ):
        problems.append(
            f"sheet: viewport requires {required[0]:.3f}×{required[1]:.3f} mm but the "
            f"record claims {recorded_required[0]:.3f}×{recorded_required[1]:.3f} mm"
        )

    # V5 — and it fits the frame it was given.
    if required[0] > frame[2] + 1e-6:
        problems.append(
            f"sheet: the model extent needs {required[0]:.3f} mm of paper width; "
            f"the viewport frame is {frame[2]:.3f} mm"
        )
    if required[1] > frame[3] + 1e-6:
        problems.append(
            f"sheet: the model extent needs {required[1]:.3f} mm of paper height; "
            f"the viewport frame is {frame[3]:.3f} mm"
        )

    # V6 — the bar is drawn the width its own length and scale imply.
    scale_bar = record["scale_bar"]
    bar_length_m = float(scale_bar["length_m"])
    expected_total_mm = bar_length_m * 1000.0 / denominator
    recorded_total_mm = float(scale_bar["total_mm"])
    if not _close(recorded_total_mm, expected_total_mm):
        problems.append(
            f"sheet: the scale bar is drawn {recorded_total_mm:.3f} mm wide; "
            f"{_fmt_measure(bar_length_m)} m at 1:{denominator} is "
            f"{expected_total_mm:.3f} mm"
        )

    # V7 — the bar's text nodes are exactly the derived set. This is what makes a
    # hand-spaced literal caption mechanically detectable in an emitted file.
    problems.extend(_check_scale_bar_text(root, scale_bar, scale_text))

    # V8 — a hand-typed meta.scale must agree with the derived value, or go away.
    if meta is not None and str(meta.scale or "").strip():
        declared = str(meta.scale)
        if declared != scale_text:
            problems.append(
                f"meta: scale {declared!r} contradicts the sheet's derived scale "
                f"{scale_text!r} — remove meta.scale (it is derived) or fix the sheet"
            )

    # V9 — clipping is on, so the overflow must be said out loud (warning: see docstring).
    drawn = viewport.get("drawn_extent_mm")
    if drawn is not None:
        overflow = _frame_overflow(tuple(float(v) for v in drawn), frame)
        if overflow > 1e-6:
            warnings.append(
                f"sheet: drawn geometry extends {overflow:.1f} mm beyond the viewport "
                "frame (clipped)"
            )

    # V10 — non-preferred ratios are allowed, and reported.
    if denominator not in ISO_PREFERRED_DENOMINATORS:
        nearest = ", ".join(
            f"1:{value}" for value in _nearest_preferred(denominator) if value is not None
        )
        warnings.append(
            f"sheet: 1:{denominator} is not an ISO 5455 preferred ratio "
            f"(nearest preferred: {nearest})"
        )

    # V11 — a declared north must be drawn at the derived bearing.
    north = record.get("north")
    if north is not None:
        expected_deg = float(north["paper_deg"])
        arrow = _first_by_class(root, "north-arrow")
        if arrow is None:
            problems.append(
                f"sheet: north is recorded at {expected_deg:.3f}° but no "
                'class="north-arrow" group is drawn'
            )
        else:
            drawn_deg = _rotate_angle(arrow.attrib.get("transform", ""))
            if drawn_deg is None or not math.isclose(drawn_deg, expected_deg, abs_tol=1e-3):
                actual = "no rotate(...)" if drawn_deg is None else f"{drawn_deg:.3f}°"
                problems.append(
                    f"sheet: the north arrow is drawn at {actual} but the record "
                    f"derives {expected_deg:.3f}°"
                )

    return problems, warnings


def validate_pid_data(data_path: str | Path) -> ValidationResult:
    """Validate a P&ID data YAML (the data model) and its rendered sheet if present."""
    from . import pid

    data_path = Path(data_path)
    result = ValidationResult(target=data_path)
    if not data_path.exists():
        result.problems.append(f"P&ID data not found: {data_path}")
        return result
    data = pid.load(data_path)
    result.checked.append("P&ID data model (tags, line types, ports)")
    result.problems.extend(pid.validate_data(data))

    # Check a rendered sheet if one exists next to the data (schematic markers).
    stem = str(data.get("meta", {}).get("number", "pid")).replace(" ", "_")
    for cand in (data_path.parent / "out" / f"{stem}.svg", data_path.with_suffix(".svg")):
        if cand.exists():
            svg_res = validate_svg(cand, require=SCHEMATIC_MARKERS)
            result.checked.extend(f"{cand.name}: {c}" for c in svg_res.checked)
            result.problems.extend(f"{cand.name}: {p}" for p in svg_res.problems)
            result.warnings.extend(f"{cand.name}: {w}" for w in svg_res.warnings)
            break
    return result


def validate_dxf_layers(
    dxf_path: str | Path, table: LayerTable | None = None
) -> ValidationResult:
    """Validate DXF entity layers against an explicitly supplied layer table."""

    dxf_path = Path(dxf_path)
    result = ValidationResult(target=dxf_path)
    if table is None:
        return result
    result.checked.extend(["geometry on a declared layer", "declared layer table"])
    if not dxf_path.exists():
        result.problems.append(f"DXF not found: {dxf_path}")
        return result

    import ezdxf

    doc = ezdxf.readfile(dxf_path)
    result.problems.extend(table.check_doc(doc))
    return result


def validate_drawing_dir(
    dir_path: str | Path,
    *,
    layers: str | Path | None = None,
    legibility: bool = False,
    strict: bool = False,
) -> ValidationResult:
    """Validate a drawing directory: its meta.yaml and its generated SVG(s)."""
    dir_path = Path(dir_path)
    result = ValidationResult(target=dir_path)

    meta_path = dir_path / "meta.yaml"
    meta: DrawingMeta | None = None
    if meta_path.exists():
        try:
            meta = DrawingMeta.load(meta_path)
            result.checked.append("meta.yaml")
            result.problems.extend(meta.validate())
            # Warnings only; `checked` deliberately gains no entry. The sheet model
            # asserts the exact `checked` list for a legacy drawing directory, and a
            # register is not a separate artifact to report having inspected.
            result.warnings.extend(meta.warnings())
        except Exception as exc:  # noqa: BLE001
            result.problems.append(f"meta.yaml unreadable: {exc}")
    else:
        result.problems.append("missing meta.yaml")

    layer_table = None
    layer_spec = layers if layers is not None else (meta.extra.get("layers") if meta else None)
    if layer_spec is not None:
        layer_table = resolve_layer_table(layer_spec, base=None if layers is not None else dir_path)

    # A P&ID directory carries a *.pid.yaml — validate its data model, and treat
    # its sheet as a schematic (no scale bar required).
    from . import pid

    pid_files = sorted(dir_path.glob("*.pid.yaml"))
    require = REQUIRED_MARKERS
    if pid_files:
        require = SCHEMATIC_MARKERS
        for pf in pid_files:
            result.checked.append(f"{pf.name}: P&ID data model")
            result.problems.extend(f"{pf.name}: {p}" for p in pid.validate_data(pid.load(pf)))

    out_dir = dir_path / "out"
    svgs = sorted(out_dir.glob("*.svg")) if out_dir.exists() else []
    if not svgs:
        result.problems.append("no generated SVG found in out/ (run render first)")
    for svg in svgs:
        svg_res = validate_svg(svg, require=require, meta=meta, legibility=False)
        result.checked.extend(f"{svg.name}: {c}" for c in svg_res.checked)
        result.problems.extend(f"{svg.name}: {p}" for p in svg_res.problems)
        result.warnings.extend(f"{svg.name}: {w}" for w in svg_res.warnings)

    if layer_table is not None:
        dxfs = sorted(out_dir.glob("*.dxf")) if out_dir.exists() else []
        for dxf in dxfs:
            dxf_res = validate_dxf_layers(dxf, layer_table)
            result.checked.extend(f"{dxf.name}: {c}" for c in dxf_res.checked)
            result.problems.extend(f"{dxf.name}: {p}" for p in dxf_res.problems)

    if legibility:
        from .legibility import check_drawing_dir

        report = check_drawing_dir(dir_path)
        result.checked.extend(f"legibility: {check}" for check in report.checked)
        result.problems.extend(str(finding) for finding in report.errors)
        result.warnings.extend(str(finding) for finding in report.warnings)
    return result


def validate_target(
    target: str | Path,
    *,
    layers: str | Path | None = None,
    legibility: bool = False,
    strict: bool = False,
) -> ValidationResult:
    target = Path(target)
    if target.is_dir():
        return validate_drawing_dir(
            target, layers=layers, legibility=legibility, strict=strict
        )
    if target.suffix.lower() in (".yaml", ".yml"):
        return validate_pid_data(target)
    if target.suffix.lower() == ".svg":
        return validate_svg(target, legibility=legibility, strict=strict)
    raise ValueError(
        f"cannot validate '{target}': expected a drawing directory, a P&ID "
        "data .yaml, or an .svg file"
    )


# --------------------------------------------------------------------------- #
# paper-space helpers (used only by check_sheet_svg)
# --------------------------------------------------------------------------- #


def _check_root_size(
    root: ET.Element, attr: str, expected_mm: float, problems: list[str]
) -> None:
    raw = root.attrib.get(attr, "")
    actual: float | None = None
    if raw.endswith("mm"):
        try:
            actual = float(raw[:-2])
        except ValueError:
            actual = None
    if actual is None or not _close(actual, expected_mm):
        problems.append(
            f"sheet: root {attr}={raw!r} is not the declared paper {attr} "
            f"{expected_mm:.1f}mm"
        )


def _viewbox_matches(view_box: str, width_mm: float, height_mm: float) -> bool:
    try:
        values = [float(part) for part in view_box.split()]
    except ValueError:
        return False
    return len(values) == 4 and all(
        _close(actual, expected)
        for actual, expected in zip(values, (0.0, 0.0, width_mm, height_mm))
    )


def _required_mm(
    extent: tuple[float, float, float, float], rotation_deg: float, denominator: int
) -> tuple[float, float]:
    xmin, ymin, xmax, ymax = extent
    w_m, h_m = xmax - xmin, ymax - ymin
    angle = math.radians(rotation_deg)
    rw = abs(w_m * math.cos(angle)) + abs(h_m * math.sin(angle))
    rh = abs(w_m * math.sin(angle)) + abs(h_m * math.cos(angle))
    return (rw * 1000.0 / denominator, rh * 1000.0 / denominator)


def _check_scale_bar_text(root: ET.Element, scale_bar: dict, scale_text: str) -> list[str]:
    """V7: the text nodes inside the bar group are exactly the derived set."""
    group = _first_by_class(root, "scale-bar")
    if group is None:
        return ["sheet: no scale-bar group is drawn for the recorded sheet"]
    length_m = float(scale_bar["length_m"])
    divisions = int(scale_bar["divisions"])
    unit = str(scale_bar["unit"])
    # The bar's own data attribute (``data-tdfa-bar``, or the legacy
    # ``data-sankofa-bar``) must agree with the recorded metadata when present.
    bar_attr = read_sheet_data_attr(group.attrib, "bar")
    if bar_attr is not None:
        drawn = parse_sheet_data_value(bar_attr)
        try:
            agrees = (
                math.isclose(float(drawn.get("length_m", "nan")), length_m, abs_tol=1e-9)
                and int(drawn.get("divisions", "-1")) == divisions
                and drawn.get("unit") == unit
            )
        except ValueError:
            agrees = False
        if not agrees:
            return [
                f"sheet: the scale bar's data attribute {bar_attr!r} disagrees with the "
                f"recorded scale bar (length_m={length_m:g}, divisions={divisions}, "
                f"unit={unit})"
            ]
    expected = Counter(
        scale_bar_tick_label(length_m, divisions, index, unit)
        for index in range(divisions + 1)
    )
    expected[scale_text] += 1
    actual = Counter(
        text
        for text in (
            "".join(element.itertext()).strip()
            for element in group.iter()
            if _local_name(element.tag) == "text"
        )
        if text
    )
    extras = list((actual - expected).elements())
    if extras:
        noun = "element" if len(extras) == 1 else "elements"
        quoted = ", ".join(repr(text) for text in extras)
        return [
            f"sheet: the scale bar carries {len(extras)} text {noun} that is not a "
            f"derived tick label: {quoted}"
        ]
    missing = list((expected - actual).elements())
    if missing:
        return [f"sheet: the scale bar is missing derived tick label(s): {missing!r}"]
    return []


def _first_by_class(root: ET.Element, class_name: str) -> ET.Element | None:
    for element in root.iter():
        if class_name in str(element.attrib.get("class", "")).split():
            return element
    return None


def _all_by_class(root: ET.Element, class_name: str) -> list[ET.Element]:
    return [
        element
        for element in root.iter()
        if class_name in str(element.attrib.get("class", "")).split()
    ]


def _parse_extents(raw: str | None) -> tuple[float, float, float, float] | None:
    if raw is None:
        return None
    try:
        values = tuple(float(part) for part in raw.split())
    except ValueError:
        return None
    if len(values) != 4:
        return None
    return values  # type: ignore[return-value]


def _bbox(extents: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    x, y, w, h = extents
    return (x, y, x + w, y + h)


def _paper_rect_mm(
    sheet: str, orientation: str
) -> tuple[float, float, float, float] | None:
    """The paper rectangle for an ISO 216 size, as ``(x, y, w, h)`` from its corner.

    The *paper*, not the frame: deriving a frame needs a margin set, and the ISO
    5457 margin convention is an open question (spec O-3). Containment on paper is
    checkable without deciding it.
    """
    name = str(sheet).strip().upper()
    orient = str(orientation).strip().lower()
    if name not in PAPER_SIZES_MM or orient not in {"landscape", "portrait"}:
        return None
    short, long = PAPER_SIZES_MM[name]
    width = long if orient == "landscape" else short
    height = short if orient == "landscape" else long
    return (0.0, 0.0, width, height)


def _fmt_extents(values: tuple[float, float, float, float]) -> str:
    return " ".join(f"{value:g}" for value in values)


def _rotate_angle(transform: str) -> float | None:
    match = re.search(r"rotate\(\s*([-+]?\d+(?:\.\d+)?)", transform)
    return None if match is None else float(match.group(1)) % 360.0


def _frame_overflow(
    bbox: tuple[float, float, float, float], frame: tuple[float, float, float, float]
) -> float:
    """How far, in mm, the drawn bbox reaches outside the frame (0.0 when inside)."""
    xmin, ymin, xmax, ymax = bbox
    fx, fy, fw, fh = frame
    return max(fx - xmin, fy - ymin, xmax - (fx + fw), ymax - (fy + fh), 0.0)


def _nearest_preferred(denominator: int) -> tuple[int | None, int | None]:
    lower = max(
        (value for value in ISO_PREFERRED_DENOMINATORS if value < denominator), default=None
    )
    upper = min(
        (value for value in ISO_PREFERRED_DENOMINATORS if value > denominator), default=None
    )
    return lower, upper


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _fmt_measure(value: float) -> str:
    if math.isclose(value, round(value), abs_tol=1e-9):
        return str(int(round(value)))
    return f"{value:g}"


def _fmt_g(value: float) -> str:
    return f"{value:g}"


def _close(first: float, second: float, tol: float = 1e-6) -> bool:
    return math.isclose(first, second, abs_tol=tol)
