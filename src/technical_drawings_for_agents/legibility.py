"""Deterministic legibility checks for emitted drawing sheets.

This module checks the SVG artifact a reviewer actually reads. The drawing
helpers in this package mostly emit strings, and project generators may add raw
SVG too, so a model-time registry would miss the assembly mistakes that make a
sheet unreadable. Parsing the emitted SVG keeps the checker byte-neutral: it
never changes a drawing, never rewrites metadata, and a clean report means only
"no checked legibility defect was found". It is not approval to issue a
drawing for construction.

The checker is precision-first. It estimates text extents with deterministic
tables and thresholds chosen from printed-sheet legibility, skips checks whose
data is genuinely absent, and raises on SVG constructs it cannot model rather
than pretending they are harmless.
"""

from __future__ import annotations

import json
import math
import re
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Iterable, Literal

import yaml

from .meta import DrawingMeta
from .style import STATUS_ORDER, STATUS_WATERMARKS

Severity = Literal["error", "warn"]

CHECK_NAMES = {
    "overprint",
    "frame-containment",
    "title-block-clear",
    "panel-clear",
    "inset-fill",
    "marker-occlusion",
    "status-coherence",
    "issued-gate",
    "revision-coherence",
    "revision-coherence:table",
    "legend-complete",
    "verify-annotated",
}
CONFIG_KEYS = {
    "units",
    "profile",
    "min_overlap_px",
    "min_clearance_px",
    "frame_tol_px",
    "opaque_alpha",
    "pointer_area_px2",
    "badge_tol_px",
    "min_panel_area_frac",
    "min_panel_side_px",
    "inset_extent_fill_min",
    "inset_content_fill_min",
    "marker_cluster_min",
    "max_items",
    "verify_marker",
    "severities",
    "require",
    "allow",
    "baseline",
}
DECLARATION_KEYS = {"version", "panels", "legend", "verify", "furniture", "frame_px"}
PANEL_DECLARATION_KEYS = {"name", "box_px", "extent_m", "kind"}
DECLARED_REGION_TAGS = {"declared-panel", "declared-callout"}
ROOT_KEYS = CONFIG_KEYS | DECLARATION_KEYS | {"config", "declarations"}
SKIP_SUBTREES = {
    "defs",
    "pattern",
    "marker",
    "clipPath",
    "mask",
    "symbol",
    "linearGradient",
    "radialGradient",
    "style",
    "metadata",
    "title",
    "desc",
}
SUPPORTED_SHAPES = {"rect", "circle", "ellipse", "line", "polygon", "polyline", "path", "image"}
SeverityRank = {"error": 0, "warn": 1}
# A numbered schedule marker's label: a schedule number, optionally with a
# single suffix letter or a trailing period ("1", "12", "3a", "7."). Deliberately
# narrow: an ISA instrument tag ("FIC-101") or an equipment label inside a box is
# not a numbered marker, so those bubbles are not clustered.
_MARKER_NUMBER = re.compile(r"\d{1,3}[A-Za-z.]?")


class LegibilityError(ValueError):
    """Raised when legibility input cannot be read as specified."""


@dataclass(frozen=True)
class Box:
    x0: float
    y0: float
    x1: float
    y1: float

    def __post_init__(self) -> None:
        x0, x1 = sorted((float(self.x0), float(self.x1)))
        y0, y1 = sorted((float(self.y0), float(self.y1)))
        object.__setattr__(self, "x0", x0)
        object.__setattr__(self, "y0", y0)
        object.__setattr__(self, "x1", x1)
        object.__setattr__(self, "y1", y1)

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    @property
    def area(self) -> float:
        return self.width * self.height

    def overlap(self, other: "Box") -> tuple[float, float]:
        return (
            min(self.x1, other.x1) - max(self.x0, other.x0),
            min(self.y1, other.y1) - max(self.y0, other.y0),
        )

    def clearance(self, other: "Box") -> float:
        dx, dy = self.overlap(other)
        if dx > 0 and dy > 0:
            return -min(dx, dy)
        if dx <= 0 and dy <= 0:
            return math.hypot(dx, dy)
        if dx <= 0:
            return -dx
        return -dy

    def contains(self, other: "Box", tol: float = 0.0) -> bool:
        return (
            other.x0 >= self.x0 - tol
            and other.y0 >= self.y0 - tol
            and other.x1 <= self.x1 + tol
            and other.y1 <= self.y1 + tol
        )

    def union(self, other: "Box") -> "Box":
        return Box(
            min(self.x0, other.x0),
            min(self.y0, other.y0),
            max(self.x1, other.x1),
            max(self.y1, other.y1),
        )

    def inflate(self, by: float) -> "Box":
        return Box(self.x0 - by, self.y0 - by, self.x1 + by, self.y1 + by)

    @classmethod
    def from_xywh(cls, x: float, y: float, w: float, h: float) -> "Box":
        return cls(float(x), float(y), float(x) + float(w), float(y) + float(h))

    def to_dict(self) -> dict[str, float]:
        return {
            "x0": round(self.x0, 3),
            "y0": round(self.y0, 3),
            "x1": round(self.x1, 3),
            "y1": round(self.y1, 3),
        }


@dataclass(frozen=True)
class Item:
    role: str
    box: Box
    scope: str
    tag: str
    path: str
    text: str = ""
    fill: str = ""
    alpha: float = 0.0
    confidence: str = "exact"

    def selector_text(self) -> str:
        return self.text or self.fill or self.path


@dataclass(frozen=True)
class Finding:
    severity: Severity
    check: str
    message: str
    location: Box | None = None
    scope: str = "sheet"

    def __str__(self) -> str:
        return f"{self.severity.upper()}: {self.check}: {self.message}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "check": self.check,
            "message": self.message,
            "location": None if self.location is None else self.location.to_dict(),
            "scope": self.scope,
        }


@dataclass(frozen=True)
class Skip:
    check: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {"check": self.check, "reason": self.reason}


@dataclass(frozen=True)
class LegibilityReport:
    target: Path | None
    frame: Box | None
    findings: tuple[Finding, ...]
    skipped: tuple[Skip, ...]
    checked: tuple[str, ...]
    items: tuple[Item, ...] = ()
    baselined: tuple[Finding, ...] = ()

    @property
    def errors(self) -> tuple[Finding, ...]:
        return tuple(finding for finding in self.findings if finding.severity == "error")

    @property
    def warnings(self) -> tuple[Finding, ...]:
        return tuple(finding for finding in self.findings if finding.severity == "warn")

    @property
    def ok(self) -> bool:
        """A clean report is not approval to issue; it only means no checked error."""

        return not self.errors

    def to_dict(self, *, show_items: bool = False) -> dict[str, Any]:
        data = {
            "target": None if self.target is None else str(self.target),
            "frame": None if self.frame is None else self.frame.to_dict(),
            "findings": [finding.to_dict() for finding in self.findings],
            "baselined": [finding.to_dict() for finding in self.baselined],
            "skipped": [skip.to_dict() for skip in self.skipped],
            "checked": list(self.checked),
            "ok": self.ok,
        }
        if show_items:
            data["items"] = [
                {
                    "role": item.role,
                    "box": item.box.to_dict(),
                    "scope": item.scope,
                    "tag": item.tag,
                    "path": item.path,
                    "text": item.text,
                    "fill": item.fill,
                    "alpha": round(item.alpha, 3),
                    "confidence": item.confidence,
                }
                for item in self.items
            ]
        return data


@dataclass(frozen=True)
class Allowance:
    a: str
    b: str
    reason: str


@dataclass(frozen=True)
class BaselineEntry:
    check: str
    message: str
    reason: str
    issue: str


@dataclass(frozen=True)
class LegibilityConfig:
    units: str = "px"
    profile: str = "cad"
    min_overlap_px: float = 1.5
    min_clearance_px: float = 2.0
    frame_tol_px: float = 0.5
    opaque_alpha: float = 0.85
    pointer_area_px2: float = 60.0
    badge_tol_px: float = 1.0
    min_panel_area_frac: float = 0.03
    min_panel_side_px: float = 100.0
    inset_extent_fill_min: float = 0.50
    inset_content_fill_min: float = 0.45
    marker_cluster_min: int = 2
    max_items: int = 5000
    verify_marker: str = "(v)"
    severities: Mapping[str, Severity] = field(default_factory=dict)
    require: tuple[str, ...] = ()
    allow: tuple[Allowance, ...] = ()
    baseline: tuple[BaselineEntry, ...] = ()


CAD_PX_DEFAULTS = LegibilityConfig(profile="cad")
ISO_PX_DEFAULTS = LegibilityConfig(profile="iso")


PANEL_KINDS = ("detail", "callout")


@dataclass(frozen=True)
class PanelDeclaration:
    """A declared region of the sheet.

    ``kind`` distinguishes the two region shapes that exist in practice:

    ``detail``
        A detail panel: an opaque inset that replaces the main view inside its
        box. Nothing from outside may intrude on it, so ``panel-clear`` applies.
    ``callout``
        An in-view callout: a box drawn *over* the main view to frame a cluster
        of features. Main-view geometry legitimately runs across its boundary, so
        ``panel-clear`` does not apply to it.

    ``inset-fill`` applies to both, because a region that claims far more extent
    than its content covers is misleading whichever shape it is.
    """

    name: str
    box: Box
    extent_m: tuple[float, float] | None = None
    kind: str = "detail"


@dataclass(frozen=True)
class LegendEntry:
    key: str
    label: str
    colour: str = ""


@dataclass(frozen=True)
class LegendDeclaration:
    entries: tuple[LegendEntry, ...]
    used_keys: tuple[str, ...]


@dataclass(frozen=True)
class VerifyItem:
    id: str | None = None
    label: str = ""
    count: int | None = None


@dataclass(frozen=True)
class VerifyDeclaration:
    marker: str
    items: tuple[VerifyItem, ...]


@dataclass(frozen=True)
class FurnitureDeclaration:
    role: str
    box: Box


@dataclass(frozen=True)
class Declarations:
    version: int = 1
    panels: tuple[PanelDeclaration, ...] = ()
    legend: LegendDeclaration | None = None
    verify: VerifyDeclaration | None = None
    furniture: tuple[FurnitureDeclaration, ...] = ()
    frame: Box | None = None

    def write_json(self, path: str | Path) -> Path:
        data: dict[str, Any] = {"version": self.version}
        if self.panels:
            data["panels"] = [
                {
                    "name": panel.name,
                    "box_px": [
                        panel.box.x0,
                        panel.box.y0,
                        panel.box.x1,
                        panel.box.y1,
                    ],
                    **({} if panel.extent_m is None else {"extent_m": list(panel.extent_m)}),
                    **({} if panel.kind == "detail" else {"kind": panel.kind}),
                }
                for panel in self.panels
            ]
        if self.legend is not None:
            data["legend"] = {
                "entries": [asdict(entry) for entry in self.legend.entries],
                "used_keys": list(self.legend.used_keys),
            }
        if self.verify is not None:
            data["verify"] = {
                "marker": self.verify.marker,
                "items": [asdict(item) for item in self.verify.items],
            }
        if self.furniture:
            data["furniture"] = [
                {
                    "role": item.role,
                    "box_px": [item.box.x0, item.box.y0, item.box.x1, item.box.y1],
                }
                for item in self.furniture
            ]
        if self.frame is not None:
            data["frame_px"] = [self.frame.x0, self.frame.y0, self.frame.x1, self.frame.y1]
        out = Path(path)
        out.write_text(
            json.dumps(data, sort_keys=True, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return out


@dataclass(frozen=True)
class SheetModel:
    target: Path | None
    root: Box
    frame: Box | None
    items: tuple[Item, ...]
    declarations: Declarations | None = None
    panel_parents: Mapping[str, str] = field(default_factory=dict)
    declared_frame_mismatch: str | None = None


@dataclass(frozen=True)
class _Style:
    font_family: str = "monospace"
    font_size: float = 11.0
    text_anchor: str = "start"
    dominant_baseline: str = "auto"
    fill: str = "#000000"
    opacity: float = 1.0
    fill_opacity: float = 1.0


Matrix = tuple[float, float, float, float, float, float]
IDENTITY: Matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def load_config(path: str | Path) -> LegibilityConfig:
    config_path = Path(path)
    data = _read_mapping(config_path)
    block = data.get("config", data)
    if not isinstance(block, Mapping):
        raise LegibilityError(f"{config_path.name}: config must be a mapping")
    unknown = sorted(str(key) for key in data if key not in ROOT_KEYS)
    if unknown:
        raise LegibilityError(f"{config_path.name}: unknown key(s): {', '.join(unknown)}")
    return _load_config_block(block, config_path.name)


def load_declarations(path: str | Path) -> Declarations:
    source = Path(path)
    data = _read_mapping(source)
    block = data.get("declarations", data)
    if not isinstance(block, Mapping):
        raise LegibilityError(f"{source.name}: declarations must be a mapping")
    unknown = sorted(str(key) for key in data if key not in ROOT_KEYS)
    if unknown:
        raise LegibilityError(f"{source.name}: unknown key(s): {', '.join(unknown)}")
    return _load_declarations_block(block, source.name)


def sheet_model(
    svg: str | Path,
    *,
    config: LegibilityConfig = CAD_PX_DEFAULTS,
    declarations: Declarations | None = None,
) -> SheetModel:
    target, svg_text = _svg_input(svg)
    try:
        root = ET.fromstring(svg_text)
    except ET.ParseError as exc:
        label = str(target) if target is not None else "SVG"
        raise LegibilityError(f"{label}: SVG is not parseable XML: {exc}") from exc
    if _local_name(root.tag) != "svg":
        raise LegibilityError("SVG: root element must be <svg>")

    detected_units = _detected_units(root)
    if config.units != detected_units:
        raise LegibilityError(
            f"SVG: config.units {config.units!r} does not match detected root units "
            f"{detected_units!r}"
        )
    root_box = _root_box(root)
    parser = _Parser(config=config, root=root_box)
    items = parser.parse(root)
    detected_frame = _detect_frame(items, root_box)
    declared_frame = declarations.frame if declarations and declarations.frame else None
    frame = declared_frame or detected_frame
    mismatch = None
    if declared_frame is not None and detected_frame is not None and not _same_box(
        declared_frame, detected_frame, 0.5
    ):
        mismatch = (
            f"declared frame {fmt_box(declared_frame)} differs from detected frame "
            f"{fmt_box(detected_frame)}"
        )
    if frame is None:
        raise LegibilityError(
            "SVG: no frame detected; supply frame_px in out/<number>.legibility.json "
            "or legibility.yaml"
        )

    items, panel_parents = _add_group_and_declared_items(
        items, root_box, frame, config=config, declarations=declarations
    )
    if len(items) > config.max_items:
        raise LegibilityError(
            f"SVG: parsed {len(items)} ink items, exceeding max_items={config.max_items}"
        )
    return SheetModel(
        target=target,
        root=root_box,
        frame=frame,
        items=items,
        declarations=declarations,
        panel_parents=panel_parents,
        declared_frame_mismatch=mismatch,
    )


def check_sheet(
    model: SheetModel,
    *,
    meta: DrawingMeta | None = None,
    config: LegibilityConfig = CAD_PX_DEFAULTS,
) -> LegibilityReport:
    findings: list[Finding] = []
    skipped: list[Skip] = []
    checked: list[str] = []
    allowance_matches: set[int] = set()

    if model.declared_frame_mismatch:
        findings.append(
            Finding(
                "warn",
                "frame-containment",
                model.declared_frame_mismatch,
                model.frame,
            )
        )

    checked.append("overprint")
    findings.extend(_check_overprint(model, config, allowance_matches))
    checked.append("frame-containment")
    findings.extend(_check_frame_containment(model, config))
    checked.append("title-block-clear")
    findings.extend(_check_title_block_clear(model, config))
    panel_clear, panels = _check_panel_clear(model, config)
    checked.append("panel-clear")
    findings.extend(panel_clear)

    checked.append("marker-occlusion")
    findings.extend(_check_marker_occlusion(model, config))

    inset_findings, inset_skip = _check_inset_fill(model, panels, config)
    if inset_skip is None:
        checked.append("inset-fill")
        findings.extend(inset_findings)
    else:
        skipped.append(inset_skip)

    status_findings, status_skip = _check_status_coherence(model, meta, config)
    if status_skip is None:
        checked.append("status-coherence")
        findings.extend(status_findings)
    else:
        skipped.append(status_skip)

    revision_findings, revision_skips = _check_revision_coherence(model, meta, config)
    if revision_findings:
        checked.append("revision-coherence")
        findings.extend(revision_findings)
    for skip in revision_skips:
        skipped.append(skip)

    legend_findings, legend_skip = _check_legend_complete(model, config)
    if legend_skip is None:
        checked.append("legend-complete")
        findings.extend(legend_findings)
    else:
        skipped.append(legend_skip)

    verify_findings, verify_skip = _check_verify_annotated(model, config)
    if verify_skip is None:
        checked.append("verify-annotated")
        findings.extend(verify_findings)
    else:
        skipped.append(verify_skip)

    findings.extend(_stale_allowances(config, allowance_matches))
    findings.extend(_required_skips(skipped, config))
    findings, baselined = _apply_baseline(findings, config)
    findings.extend(_stale_baselines(config, baselined, model.items))

    ordered = _sort_findings(findings)
    return LegibilityReport(
        target=model.target,
        frame=model.frame,
        findings=tuple(ordered),
        skipped=tuple(sorted(skipped, key=lambda skip: skip.check)),
        checked=tuple(sorted(dict.fromkeys(checked))),
        items=model.items,
        baselined=tuple(_sort_findings(baselined)),
    )


def check_svg(
    svg: str | Path,
    *,
    meta: DrawingMeta | None = None,
    declarations: Declarations | None = None,
    config: LegibilityConfig = CAD_PX_DEFAULTS,
) -> LegibilityReport:
    model = sheet_model(svg, config=config, declarations=declarations)
    return check_sheet(model, meta=meta, config=config)


def check_drawing_dir(
    dir_path: str | Path,
    *,
    config: LegibilityConfig | None = None,
) -> LegibilityReport:
    drawing_dir = Path(dir_path)
    if not drawing_dir.is_dir():
        raise LegibilityError(f"target is not a drawing directory: {drawing_dir}")

    yaml_config, yaml_declarations = _load_legibility_yaml(drawing_dir / "legibility.yaml")
    cfg = config or yaml_config or _profile_for_dir(drawing_dir)
    meta_path = drawing_dir / "meta.yaml"
    meta = DrawingMeta.load(meta_path) if meta_path.exists() else None
    svgs = sorted((drawing_dir / "out").glob("*.svg")) if (drawing_dir / "out").exists() else []
    if not svgs:
        raise LegibilityError(f"{drawing_dir}: no generated SVG found in out/")

    reports: list[LegibilityReport] = []
    for svg_path in svgs:
        declarations = _merge_declarations(
            yaml_declarations,
            _load_sidecar_declarations(svg_path),
        )
        report = check_svg(svg_path, meta=meta, declarations=declarations, config=cfg)
        reports.append(_prefix_report(report, svg_path.name))
    return _merge_reports(drawing_dir, reports)


def gate(report: LegibilityReport, *, strict: bool = False) -> int:
    if report.errors:
        return 1
    if strict and (report.warnings or report.skipped):
        return 1
    return 0


def text_box(
    text: str,
    x: float,
    y: float,
    font_size: float,
    *,
    family: str = "monospace",
    anchor: str = "start",
    baseline: str = "auto",
) -> tuple[Box, str]:
    width, confidence = _text_width(str(text), float(font_size), family)
    ascent, descent = _vertical_metrics(str(text), float(font_size))
    height = ascent + descent
    if anchor == "middle":
        x0 = float(x) - width / 2.0
    elif anchor == "end":
        x0 = float(x) - width
    elif anchor == "start":
        x0 = float(x)
    else:
        raise LegibilityError(f"text-anchor must be start, middle or end, got {anchor!r}")
    if baseline in {"middle", "central"}:
        y0 = float(y) - height / 2.0
        y1 = float(y) + height / 2.0
    else:
        y0 = float(y) - ascent
        y1 = float(y) + descent
    return Box(x0, y0, x0 + width, y1), confidence


def report_json(report: LegibilityReport, *, show_items: bool = False) -> str:
    return (
        json.dumps(
            report.to_dict(show_items=show_items),
            sort_keys=True,
            indent=2,
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    )


def fmt_box(box: Box | None) -> str:
    if box is None:
        return "-"
    return f"[{box.x0:.1f},{box.y0:.1f} {box.x1:.1f},{box.y1:.1f}]"


class _Parser:
    def __init__(self, *, config: LegibilityConfig, root: Box) -> None:
        self.config = config
        self.root = root
        self.items: list[Item] = []
        self.group_boxes: dict[str, Box] = {}
        self.group_roles: dict[str, str] = {}

    def parse(self, root: ET.Element) -> tuple[Item, ...]:
        style = _style_from(root.attrib, _Style(), "svg")
        self._walk(root, "svg", IDENTITY, style, "sheet", is_root=True)
        return tuple(self.items)

    def _walk(
        self,
        element: ET.Element,
        path: str,
        matrix: Matrix,
        style: _Style,
        scope: str,
        *,
        is_root: bool = False,
    ) -> None:
        tag = _local_name(element.tag)
        if tag in SKIP_SUBTREES:
            return
        if tag == "use":
            raise LegibilityError(f"{path}: unsupported element <use>")
        if tag == "tspan":
            for attr in ("x", "y", "dx", "dy"):
                if attr in element.attrib:
                    raise LegibilityError(f"{path}: unsupported tspan attribute {attr}")
        if tag != "svg" and "style" in element.attrib and tag in SUPPORTED_SHAPES:
            raise LegibilityError(f"{path}: unsupported style attribute")

        current = _style_from(element.attrib, style, path)
        current_matrix = matrix
        if not is_root and tag == "svg":
            current_matrix = _mat_mul(current_matrix, _nested_svg_matrix(element, path))
        if "transform" in element.attrib:
            current_matrix = _mat_mul(
                current_matrix, _parse_transform(element.attrib["transform"], path)
            )

        current_scope = scope
        classes = set(str(element.attrib.get("class", "")).split())
        role_scope = None
        if "title-block" in classes:
            role_scope = "title-block"
        elif "scale-bar" in classes:
            role_scope = _numbered_scope("scale-bar", len(self.group_roles) + 1)
        elif "status-watermark" in classes:
            role_scope = "status-watermark"
        if role_scope is not None:
            current_scope = role_scope
            self.group_roles[path] = role_scope.split("#", 1)[0]

        item = self._item_for(element, tag, path, current_matrix, current, current_scope)
        if item is not None:
            self.items.append(item)
            self._record_group_boxes(path, item.box)

        if tag == "text":
            self._validate_text_children(element, path)
            return

        counts: dict[str, int] = {}
        for child in list(element):
            child_tag = _local_name(child.tag)
            counts[child_tag] = counts.get(child_tag, 0) + 1
            child_path = f"{path}/{child_tag}[{counts[child_tag]}]"
            self._walk(child, child_path, current_matrix, current, current_scope)

    def _item_for(
        self,
        element: ET.Element,
        tag: str,
        path: str,
        matrix: Matrix,
        style: _Style,
        scope: str,
    ) -> Item | None:
        if tag == "text":
            for attr in ("textLength", "lengthAdjust", "dx", "dy"):
                if attr in element.attrib:
                    raise LegibilityError(f"{path}: unsupported text attribute {attr}")
            x = _num_attr(element, "x", path, 0.0)
            y = _num_attr(element, "y", path, 0.0)
            text = "".join(element.itertext())
            box, confidence = text_box(
                text,
                x,
                y,
                style.font_size,
                family=style.font_family,
                anchor=style.text_anchor,
                baseline=style.dominant_baseline,
            )
            mapped = _map_box(box, matrix)
            role = "watermark" if scope == "status-watermark" else "text"
            return Item(
                role=role,
                box=mapped,
                scope=scope,
                tag=tag,
                path=path,
                text=text,
                fill=style.fill,
                alpha=_fill_alpha(style.fill) * style.opacity * style.fill_opacity,
                confidence=confidence,
            )
        if tag == "g" or tag == "svg":
            return None
        if tag not in SUPPORTED_SHAPES:
            return None
        box = _shape_box(element, tag, path)
        if box is None:
            return None
        mapped = _map_box(box, matrix)
        fill = "none" if tag == "line" else element.attrib.get("fill", style.fill)
        stroke = element.attrib.get("stroke", "")
        alpha = _fill_alpha(fill) * style.opacity * style.fill_opacity
        if _no_ink(fill, stroke, style.opacity):
            return None
        role = _shape_role(tag, mapped, fill, alpha, scope, self.root, self.config)
        return Item(
            role=role,
            box=mapped,
            scope=scope,
            tag=tag,
            path=path,
            fill=str(fill),
            alpha=alpha,
            confidence="curve-hull" if tag == "path" and _path_has_curve(element) else "exact",
        )

    def _record_group_boxes(self, path: str, box: Box) -> None:
        bits = path.split("/")
        for index in range(1, len(bits)):
            group_path = "/".join(bits[:index])
            if group_path in self.group_roles:
                prior = self.group_boxes.get(group_path)
                self.group_boxes[group_path] = box if prior is None else prior.union(box)

    def _validate_text_children(self, element: ET.Element, path: str) -> None:
        for child_index, child in enumerate(list(element), 1):
            if _local_name(child.tag) != "tspan":
                continue
            child_path = f"{path}/tspan[{child_index}]"
            for attr in ("x", "y", "dx", "dy"):
                if attr in child.attrib:
                    raise LegibilityError(f"{child_path}: unsupported tspan attribute {attr}")


def _load_config_block(raw: Mapping[str, Any], ctx: str) -> LegibilityConfig:
    unknown = sorted(
        str(key) for key in raw if key not in CONFIG_KEYS and key not in DECLARATION_KEYS
    )
    if unknown:
        raise LegibilityError(f"{ctx}: unknown config key(s): {', '.join(unknown)}")
    values: dict[str, Any] = {}
    numeric = {
        "min_overlap_px",
        "min_clearance_px",
        "frame_tol_px",
        "opaque_alpha",
        "pointer_area_px2",
        "badge_tol_px",
        "min_panel_area_frac",
        "min_panel_side_px",
        "inset_extent_fill_min",
        "inset_content_fill_min",
    }
    for key in numeric:
        if key in raw:
            values[key] = _number(raw[key], f"{ctx}: {key}")
    if "max_items" in raw:
        max_items = raw["max_items"]
        if isinstance(max_items, bool) or not isinstance(max_items, int) or max_items <= 0:
            raise LegibilityError(f"{ctx}: max_items must be a positive integer")
        values["max_items"] = max_items
    if "marker_cluster_min" in raw:
        cluster_min = raw["marker_cluster_min"]
        if isinstance(cluster_min, bool) or not isinstance(cluster_min, int) or cluster_min < 2:
            raise LegibilityError(
                f"{ctx}: marker_cluster_min must be an integer >= 2 "
                "(one marker cannot occlude itself)"
            )
        values["marker_cluster_min"] = cluster_min
    for key in ("units", "profile", "verify_marker"):
        if key in raw:
            value = raw[key]
            if not isinstance(value, str) or not value.strip():
                raise LegibilityError(f"{ctx}: {key} must be a non-empty string")
            values[key] = value
    if values.get("units", "px") not in {"px", "mm"}:
        raise LegibilityError(f"{ctx}: units must be 'px' or 'mm'")
    if values.get("profile", "cad") not in {"cad", "iso"}:
        raise LegibilityError(f"{ctx}: profile must be 'cad' or 'iso'")
    for key in ("min_overlap_px", "min_clearance_px", "frame_tol_px", "pointer_area_px2"):
        if key in values and values[key] < 0:
            raise LegibilityError(f"{ctx}: {key} must be non-negative")
    for key in (
        "opaque_alpha",
        "min_panel_area_frac",
        "inset_extent_fill_min",
        "inset_content_fill_min",
    ):
        if key in values and not (0 < values[key] <= 1):
            raise LegibilityError(f"{ctx}: {key} must be in (0, 1]")
    if "severities" in raw:
        values["severities"] = _load_severities(raw["severities"], ctx)
    if "require" in raw:
        values["require"] = _check_name_tuple(raw["require"], f"{ctx}: require")
    if "allow" in raw:
        values["allow"] = tuple(_load_allowances(raw["allow"], ctx))
    if "baseline" in raw:
        values["baseline"] = tuple(_load_baseline_entries(raw["baseline"], ctx))
    return replace(CAD_PX_DEFAULTS, **values)


def _load_declarations_block(raw: Mapping[str, Any], ctx: str) -> Declarations:
    unknown = sorted(
        str(key) for key in raw if key not in DECLARATION_KEYS and key not in CONFIG_KEYS
    )
    if unknown:
        raise LegibilityError(f"{ctx}: unknown declaration key(s): {', '.join(unknown)}")
    version = raw.get("version", 1)
    if isinstance(version, bool) or not isinstance(version, int):
        raise LegibilityError(f"{ctx}: version must be an integer")
    panels = tuple(
        _load_panel(item, index, ctx) for index, item in enumerate(raw.get("panels", []))
    )
    legend = _load_legend(raw.get("legend"), ctx)
    verify = _load_verify(raw.get("verify"), ctx)
    furniture = tuple(
        _load_furniture(item, index, ctx) for index, item in enumerate(raw.get("furniture", []))
    )
    frame = _box4(raw["frame_px"], f"{ctx}: frame_px") if "frame_px" in raw else None
    return Declarations(
        version=version,
        panels=panels,
        legend=legend,
        verify=verify,
        furniture=furniture,
        frame=frame,
    )


def _read_mapping(path: Path) -> Mapping[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise LegibilityError(f"cannot read {path}: {exc}") from exc
    try:
        if path.suffix.lower() == ".json":
            data = json.loads(text)
        else:
            data = yaml.safe_load(text) or {}
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        raise LegibilityError(f"invalid legibility file {path}: {exc}") from exc
    if not isinstance(data, Mapping):
        raise LegibilityError(f"{path.name}: top level must be a mapping")
    return data


def _number(value: Any, ctx: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LegibilityError(f"{ctx} must be a number")
    if not math.isfinite(float(value)):
        raise LegibilityError(f"{ctx} must be finite")
    return float(value)


def _check_name_tuple(value: Any, ctx: str) -> tuple[str, ...]:
    if isinstance(value, str):
        names = [part.strip() for part in value.split(",") if part.strip()]
    elif isinstance(value, list):
        names = [str(item) for item in value]
    else:
        raise LegibilityError(f"{ctx} must be a comma-separated string or list")
    unknown = sorted(set(names) - CHECK_NAMES)
    if unknown:
        raise LegibilityError(f"{ctx} names unknown check(s): {', '.join(unknown)}")
    return tuple(names)


def _load_severities(value: Any, ctx: str) -> dict[str, Severity]:
    if not isinstance(value, Mapping):
        raise LegibilityError(f"{ctx}: severities must be a mapping")
    out: dict[str, Severity] = {}
    for key, severity in value.items():
        check = str(key)
        if check not in CHECK_NAMES:
            raise LegibilityError(f"{ctx}: severities.{check} names an unknown check")
        if severity not in ("error", "warn"):
            raise LegibilityError(f"{ctx}: severities.{check} must be error or warn")
        if check == "issued-gate" and severity != "error":
            raise LegibilityError(f"{ctx}: ISSUED gate severity cannot be downgraded")
        out[check] = severity
    return out


def _load_allowances(raw: Any, ctx: str) -> list[Allowance]:
    if not isinstance(raw, list):
        raise LegibilityError(f"{ctx}: allow must be a list")
    out: list[Allowance] = []
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise LegibilityError(f"{ctx}: allow[{index}] must be a mapping")
        reason = str(item.get("reason", "")).strip()
        if not reason:
            raise LegibilityError(f"{ctx}: allow[{index}].reason must be non-empty")
        for key in ("a", "b"):
            if not isinstance(item.get(key), str) or not str(item.get(key)).strip():
                raise LegibilityError(f"{ctx}: allow[{index}].{key} must be a non-empty selector")
        out.append(Allowance(a=str(item["a"]), b=str(item["b"]), reason=reason))
    return out


def _load_baseline_entries(raw: Any, ctx: str) -> list[BaselineEntry]:
    if not isinstance(raw, list):
        raise LegibilityError(f"{ctx}: baseline must be a list")
    out: list[BaselineEntry] = []
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise LegibilityError(f"{ctx}: baseline[{index}] must be a mapping")
        for key in ("check", "message", "reason", "issue"):
            if not isinstance(item.get(key), str) or not str(item.get(key)).strip():
                raise LegibilityError(f"{ctx}: baseline[{index}].{key} must be non-empty")
        out.append(
            BaselineEntry(
                check=str(item["check"]),
                message=str(item["message"]),
                reason=str(item["reason"]),
                issue=str(item["issue"]),
            )
        )
    return out


def _box4(value: Any, ctx: str) -> Box:
    if not isinstance(value, list) or len(value) != 4:
        raise LegibilityError(f"{ctx} must be [x0, y0, x1, y1]")
    return Box(*(_number(item, f"{ctx}[{index}]") for index, item in enumerate(value)))


def _coord2(value: Any, ctx: str) -> tuple[float, float]:
    if not isinstance(value, list) or len(value) != 2:
        raise LegibilityError(f"{ctx} must be [x, y]")
    return (_number(value[0], f"{ctx}[0]"), _number(value[1], f"{ctx}[1]"))


def _load_panel(raw: Any, index: int, ctx: str) -> PanelDeclaration:
    if not isinstance(raw, Mapping):
        raise LegibilityError(f"{ctx}: panels[{index}] must be a mapping")
    name = str(raw.get("name", f"panel-{index}")).strip()
    if not name:
        raise LegibilityError(f"{ctx}: panels[{index}].name must be non-empty")
    if "box_px" not in raw:
        raise LegibilityError(f"{ctx}: panels[{index}].box_px is required")
    extent = (
        _coord2(raw["extent_m"], f"{ctx}: panels[{index}].extent_m")
        if "extent_m" in raw
        else None
    )
    kind = str(raw.get("kind", "detail")).strip()
    if kind not in PANEL_KINDS:
        raise LegibilityError(
            f"{ctx}: panels[{index}].kind must be one of {', '.join(PANEL_KINDS)}, got {kind!r}"
        )
    unknown = sorted(str(key) for key in raw if key not in PANEL_DECLARATION_KEYS)
    if unknown:
        raise LegibilityError(f"{ctx}: panels[{index}] unknown key(s): {', '.join(unknown)}")
    return PanelDeclaration(
        name=name,
        box=_box4(raw["box_px"], f"{ctx}: panels[{index}].box_px"),
        extent_m=extent,
        kind=kind,
    )


def _load_legend(raw: Any, ctx: str) -> LegendDeclaration | None:
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise LegibilityError(f"{ctx}: legend must be a mapping")
    entries_raw = raw.get("entries", [])
    if not isinstance(entries_raw, list):
        raise LegibilityError(f"{ctx}: legend.entries must be a list")
    entries: list[LegendEntry] = []
    for index, item in enumerate(entries_raw):
        if not isinstance(item, Mapping):
            raise LegibilityError(f"{ctx}: legend.entries[{index}] must be a mapping")
        key = str(item.get("key", "")).strip()
        label = str(item.get("label", "")).strip()
        if not key or not label:
            raise LegibilityError(f"{ctx}: legend.entries[{index}] needs key and label")
        entries.append(LegendEntry(key=key, label=label, colour=str(item.get("colour", ""))))
    used = raw.get("used_keys", [])
    if not isinstance(used, list):
        raise LegibilityError(f"{ctx}: legend.used_keys must be a list")
    return LegendDeclaration(entries=tuple(entries), used_keys=tuple(str(key) for key in used))


def _load_verify(raw: Any, ctx: str) -> VerifyDeclaration | None:
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise LegibilityError(f"{ctx}: verify must be a mapping")
    marker = str(raw.get("marker", "(v)"))
    items_raw = raw.get("items", [])
    if not isinstance(items_raw, list):
        raise LegibilityError(f"{ctx}: verify.items must be a list")
    items: list[VerifyItem] = []
    for index, item in enumerate(items_raw):
        if not isinstance(item, Mapping):
            raise LegibilityError(f"{ctx}: verify.items[{index}] must be a mapping")
        item_id = item.get("id")
        if item_id is not None and not isinstance(item_id, str):
            raise LegibilityError(f"{ctx}: verify.items[{index}].id must be a string or null")
        count = item.get("count")
        if count is not None and (
            isinstance(count, bool) or not isinstance(count, int) or count < 0
        ):
            raise LegibilityError(
                f"{ctx}: verify.items[{index}].count must be a non-negative integer"
            )
        items.append(VerifyItem(id=item_id, label=str(item.get("label", "")), count=count))
    return VerifyDeclaration(marker=marker, items=tuple(items))


def _load_furniture(raw: Any, index: int, ctx: str) -> FurnitureDeclaration:
    if not isinstance(raw, Mapping):
        raise LegibilityError(f"{ctx}: furniture[{index}] must be a mapping")
    role = str(raw.get("role", "")).strip()
    if not role:
        raise LegibilityError(f"{ctx}: furniture[{index}].role must be non-empty")
    if "box_px" not in raw:
        raise LegibilityError(f"{ctx}: furniture[{index}].box_px is required")
    return FurnitureDeclaration(
        role=role,
        box=_box4(raw["box_px"], f"{ctx}: furniture[{index}].box_px"),
    )


def _svg_input(svg: str | Path) -> tuple[Path | None, str]:
    if isinstance(svg, Path):
        return svg, svg.read_text(encoding="utf-8")
    text = str(svg)
    path = Path(text)
    if "\n" not in text and path.suffix.lower() == ".svg" and path.exists():
        return path, path.read_text(encoding="utf-8")
    return None, text


def _root_box(root: ET.Element) -> Box:
    view_box = root.attrib.get("viewBox")
    if not view_box:
        width = _num_attr(root, "width", "svg", 0.0)
        height = _num_attr(root, "height", "svg", 0.0)
        return Box(0.0, 0.0, width, height)
    values = _number_list(view_box, "svg", "viewBox")
    if len(values) != 4:
        raise LegibilityError("svg: viewBox must contain four numbers")
    return Box(values[0], values[1], values[0] + values[2], values[1] + values[3])


def _detected_units(root: ET.Element) -> str:
    width = str(root.attrib.get("width", ""))
    height = str(root.attrib.get("height", ""))
    if width.endswith("mm") or height.endswith("mm"):
        return "mm"
    return "px"


def _style_from(attrib: Mapping[str, str], parent: _Style, path: str) -> _Style:
    font_size = parent.font_size
    if "font-size" in attrib:
        font_size = _num_value(attrib["font-size"], path, "font-size")
    opacity = parent.opacity
    if "opacity" in attrib:
        opacity *= _num_value(attrib["opacity"], path, "opacity")
    fill_opacity = parent.fill_opacity
    if "fill-opacity" in attrib:
        fill_opacity *= _num_value(attrib["fill-opacity"], path, "fill-opacity")
    return _Style(
        font_family=str(attrib.get("font-family", parent.font_family)),
        font_size=font_size,
        text_anchor=str(attrib.get("text-anchor", parent.text_anchor)),
        dominant_baseline=str(attrib.get("dominant-baseline", parent.dominant_baseline)),
        fill=str(attrib.get("fill", parent.fill)),
        opacity=opacity,
        fill_opacity=fill_opacity,
    )


def _num_attr(element: ET.Element, attr: str, path: str, default: float | None = None) -> float:
    if attr not in element.attrib:
        if default is None:
            raise LegibilityError(f"{path}: missing {attr}")
        return float(default)
    return _num_value(element.attrib[attr], path, attr)


def _num_value(value: Any, path: str, attr: str) -> float:
    raw = str(value).strip()
    if not re.fullmatch(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?", raw):
        raise LegibilityError(f"{path}: unsupported unit or value for {attr}: {value!r}")
    number = float(raw)
    if not math.isfinite(number):
        raise LegibilityError(f"{path}: {attr} must be finite")
    return number


def _number_list(value: str, path: str, attr: str) -> list[float]:
    parts = [part for part in re.split(r"[\s,]+", str(value).strip()) if part]
    return [_num_value(part, path, attr) for part in parts]


def _nested_svg_matrix(element: ET.Element, path: str) -> Matrix:
    x = _num_attr(element, "x", path, 0.0)
    y = _num_attr(element, "y", path, 0.0)
    width = _num_attr(element, "width", path, None)
    height = _num_attr(element, "height", path, None)
    view_box = element.attrib.get("viewBox")
    if not view_box:
        return _translate(x, y)
    vb = _number_list(view_box, path, "viewBox")
    if len(vb) != 4:
        raise LegibilityError(f"{path}: viewBox must contain four numbers")
    align, meet = _parse_preserve_aspect(
        element.attrib.get("preserveAspectRatio", "xMidYMid meet"),
        path,
    )
    if meet != "meet":
        raise LegibilityError(f"{path}: unsupported preserveAspectRatio mode {meet!r}")
    sx = width / vb[2]
    sy = height / vb[3]
    scale = min(sx, sy)
    extra_x = width - vb[2] * scale
    extra_y = height - vb[3] * scale
    ax = {"xMin": 0.0, "xMid": 0.5, "xMax": 1.0}[align[:4]]
    ay = {"YMin": 0.0, "YMid": 0.5, "YMax": 1.0}[align[4:]]
    tx = x + extra_x * ax - vb[0] * scale
    ty = y + extra_y * ay - vb[1] * scale
    return (scale, 0.0, 0.0, scale, tx, ty)


def _parse_preserve_aspect(raw: str, path: str) -> tuple[str, str]:
    if raw.strip() == "none":
        raise LegibilityError(f"{path}: preserveAspectRatio='none' is unsupported for nested svg")
    parts = raw.split()
    align = parts[0] if parts else "xMidYMid"
    meet = parts[1] if len(parts) > 1 else "meet"
    if len(parts) > 2:
        raise LegibilityError(f"{path}: unsupported preserveAspectRatio {raw!r}")
    if align not in {
        "xMinYMin",
        "xMidYMin",
        "xMaxYMin",
        "xMinYMid",
        "xMidYMid",
        "xMaxYMid",
        "xMinYMax",
        "xMidYMax",
        "xMaxYMax",
    }:
        raise LegibilityError(f"{path}: unsupported preserveAspectRatio alignment {align!r}")
    if meet not in {"meet", "slice"}:
        raise LegibilityError(f"{path}: unsupported preserveAspectRatio mode {meet!r}")
    return align, meet


def _parse_transform(raw: str, path: str) -> Matrix:
    matrix = IDENTITY
    pos = 0
    for match in re.finditer(r"([A-Za-z][A-Za-z0-9]*)\(([^)]*)\)", raw):
        if raw[pos:match.start()].strip():
            raise LegibilityError(f"{path}: unsupported transform syntax {raw!r}")
        pos = match.end()
        name = match.group(1)
        args = _transform_args(match.group(2), path, name)
        if name == "translate":
            if len(args) not in (1, 2):
                raise LegibilityError(f"{path}: translate expects one or two arguments")
            func = _translate(args[0], args[1] if len(args) == 2 else 0.0)
        elif name == "scale":
            if len(args) not in (1, 2):
                raise LegibilityError(f"{path}: scale expects one or two arguments")
            func = (args[0], 0.0, 0.0, args[1] if len(args) == 2 else args[0], 0.0, 0.0)
        elif name == "rotate":
            if len(args) not in (1, 3):
                raise LegibilityError(f"{path}: rotate expects one or three arguments")
            func = (
                _rotate(args[0], args[1], args[2])
                if len(args) == 3
                else _rotate(args[0], 0.0, 0.0)
            )
        elif name == "matrix":
            if len(args) != 6:
                raise LegibilityError(f"{path}: matrix expects six arguments")
            func = tuple(args)  # type: ignore[assignment]
        else:
            raise LegibilityError(f"{path}: unsupported transform function {name}")
        matrix = _mat_mul(matrix, func)
    if raw[pos:].strip():
        raise LegibilityError(f"{path}: unsupported transform syntax {raw!r}")
    return matrix


def _transform_args(raw: str, path: str, name: str) -> list[float]:
    if not raw.strip():
        return []
    values = [part for part in re.split(r"[\s,]+", raw.strip()) if part]
    return [_num_value(value, path, f"transform.{name}") for value in values]


def _translate(x: float, y: float) -> Matrix:
    return (1.0, 0.0, 0.0, 1.0, float(x), float(y))


def _rotate(angle_deg: float, cx: float, cy: float) -> Matrix:
    angle = math.radians(angle_deg)
    c = math.cos(angle)
    s = math.sin(angle)
    r = (c, s, -s, c, 0.0, 0.0)
    return _mat_mul(_mat_mul(_translate(cx, cy), r), _translate(-cx, -cy))


def _mat_mul(first: Matrix, second: Matrix) -> Matrix:
    a1, b1, c1, d1, e1, f1 = first
    a2, b2, c2, d2, e2, f2 = second
    return (
        a1 * a2 + c1 * b2,
        b1 * a2 + d1 * b2,
        a1 * c2 + c1 * d2,
        b1 * c2 + d1 * d2,
        a1 * e2 + c1 * f2 + e1,
        b1 * e2 + d1 * f2 + f1,
    )


def _map_point(x: float, y: float, matrix: Matrix) -> tuple[float, float]:
    a, b, c, d, e, f = matrix
    return (a * x + c * y + e, b * x + d * y + f)


def _map_box(box: Box, matrix: Matrix) -> Box:
    points = [
        _map_point(box.x0, box.y0, matrix),
        _map_point(box.x1, box.y0, matrix),
        _map_point(box.x1, box.y1, matrix),
        _map_point(box.x0, box.y1, matrix),
    ]
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return Box(min(xs), min(ys), max(xs), max(ys))


def _shape_box(element: ET.Element, tag: str, path: str) -> Box | None:
    if tag == "rect":
        return Box.from_xywh(
            _num_attr(element, "x", path, 0.0),
            _num_attr(element, "y", path, 0.0),
            _num_attr(element, "width", path, None),
            _num_attr(element, "height", path, None),
        )
    if tag == "circle":
        cx = _num_attr(element, "cx", path, 0.0)
        cy = _num_attr(element, "cy", path, 0.0)
        r = _num_attr(element, "r", path, None)
        return Box(cx - r, cy - r, cx + r, cy + r)
    if tag == "ellipse":
        cx = _num_attr(element, "cx", path, 0.0)
        cy = _num_attr(element, "cy", path, 0.0)
        rx = _num_attr(element, "rx", path, None)
        ry = _num_attr(element, "ry", path, None)
        return Box(cx - rx, cy - ry, cx + rx, cy + ry)
    if tag == "line":
        return Box(
            _num_attr(element, "x1", path, 0.0),
            _num_attr(element, "y1", path, 0.0),
            _num_attr(element, "x2", path, 0.0),
            _num_attr(element, "y2", path, 0.0),
        )
    if tag in {"polygon", "polyline"}:
        return _points_box(str(element.attrib.get("points", "")), path)
    if tag == "path":
        return _path_box(str(element.attrib.get("d", "")), path)
    if tag == "image":
        return Box.from_xywh(
            _num_attr(element, "x", path, 0.0),
            _num_attr(element, "y", path, 0.0),
            _num_attr(element, "width", path, None),
            _num_attr(element, "height", path, None),
        )
    return None


def _points_box(raw: str, path: str) -> Box:
    values = _number_list(raw, path, "points")
    if len(values) < 4 or len(values) % 2:
        raise LegibilityError(f"{path}: points must contain x,y pairs")
    xs = values[0::2]
    ys = values[1::2]
    return Box(min(xs), min(ys), max(xs), max(ys))


def _path_box(raw: str, path: str) -> Box:
    if not raw.strip():
        raise LegibilityError(f"{path}: path d must be non-empty")
    # The emitters use numeric path commands. Taking every coordinate/control
    # pair deliberately over-estimates curves and never under-estimates them.
    values = [
        float(value)
        for value in re.findall(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?", raw)
    ]
    if len(values) < 2:
        raise LegibilityError(f"{path}: path d contains no coordinates")
    if len(values) % 2:
        values = values[:-1]
    xs = values[0::2]
    ys = values[1::2]
    return Box(min(xs), min(ys), max(xs), max(ys))


def _path_has_curve(element: ET.Element) -> bool:
    return any(ch in str(element.attrib.get("d", "")) for ch in "CcSsQqTtAa")


def _shape_role(
    tag: str,
    box: Box,
    fill: str,
    alpha: float,
    scope: str,
    root: Box,
    config: LegibilityConfig,
) -> str:
    if box.area >= root.area * 0.98:
        return "background"
    if tag == "image":
        return "image"
    if scope == "status-watermark":
        return "watermark"
    if _is_pattern_fill(fill):
        return "geometry"
    if alpha >= config.opaque_alpha and box.area >= config.pointer_area_px2:
        return "opaque-fill"
    return "geometry"


def _no_ink(fill: str, stroke: str, opacity: float) -> bool:
    if opacity <= 0:
        return True
    fill_none = not fill or str(fill).strip().lower() == "none"
    stroke_none = not stroke or str(stroke).strip().lower() == "none"
    return fill_none and stroke_none


def _fill_alpha(fill: str) -> float:
    raw = str(fill).strip()
    if not raw or raw.lower() == "none" or _is_pattern_fill(raw):
        return 0.0
    if raw.startswith("#"):
        if len(raw) == 9:
            return int(raw[-2:], 16) / 255.0
        return 1.0
    match = re.fullmatch(r"rgba\(([^)]*)\)", raw, flags=re.IGNORECASE)
    if match:
        parts = [part.strip() for part in match.group(1).split(",")]
        if len(parts) == 4:
            try:
                return max(0.0, min(1.0, float(parts[3])))
            except ValueError:
                return 1.0
    if raw.lower().startswith("rgb("):
        return 1.0
    return 1.0


def _is_pattern_fill(fill: str) -> bool:
    return str(fill).strip().startswith("url(")


def _detect_frame(items: Iterable[Item], root: Box) -> Box | None:
    candidates = []
    for item in items:
        if _is_frame_candidate(item, root):
            candidates.append(item.box)
    return min(candidates, key=lambda box: box.area) if candidates else None


def _is_frame_candidate(item: Item, root: Box) -> bool:
    if item.tag != "rect" or item.fill.strip().lower() != "none":
        return False
    if item.path.count("/") != 1:
        return False
    if item.box.area < root.area * 0.50:
        return False
    cx = (root.x0 + root.x1) / 2.0
    cy = (root.y0 + root.y1) / 2.0
    icx = (item.box.x0 + item.box.x1) / 2.0
    icy = (item.box.y0 + item.box.y1) / 2.0
    return abs(icx - cx) <= 1.0 and abs(icy - cy) <= 1.0


def _add_group_and_declared_items(
    items: tuple[Item, ...],
    root: Box,
    frame: Box,
    *,
    config: LegibilityConfig,
    declarations: Declarations | None,
) -> tuple[tuple[Item, ...], dict[str, str]]:
    out = list(items)
    group_items = _group_items(items)
    out.extend(group_items)
    panels, panel_parents = _auto_panel_items(items, frame, config)
    if declarations is not None and declarations.panels:
        # A declared panel SUPERSEDES an auto-detected one covering the same
        # rectangle. Without this, declaring a panel merely *adds* a second panel
        # item for one physical rect: the declared one carries `declared-callout`
        # and is exempt from panel-clear, while the auto duplicate still fires — so
        # declaring a callout could never suppress the finding it exists to declare.
        # Found on the first real project sheet (a site GA).
        declared_boxes = [panel.box for panel in declarations.panels]
        panels = [
            panel
            for panel in panels
            if not any(_boxes_are_same_region(panel.box, box) for box in declared_boxes)
        ]
    out.extend(panels)
    if declarations is not None:
        for panel in declarations.panels:
            path = f"declarations/panels/{panel.name}"
            out.append(
                Item(
                    role="panel",
                    box=panel.box,
                    scope=f"panel:{panel.name}",
                    tag="declared-panel" if panel.kind == "detail" else "declared-callout",
                    path=path,
                )
            )
        for item in declarations.furniture:
            out.append(
                Item(
                    role=item.role,
                    box=item.box,
                    scope=f"furniture:{item.role}",
                    tag="declared-furniture",
                    path=f"declarations/furniture/{item.role}",
                )
            )
    frame_items = []
    for item in out:
        if item.tag == "rect" and (
            _same_box(item.box, frame, 0.5) or _is_frame_candidate(item, root)
        ):
            frame_items.append(replace(item, role="frame"))
        else:
            frame_items.append(item)
    return tuple(frame_items), panel_parents


def _group_items(items: tuple[Item, ...]) -> list[Item]:
    boxes: dict[str, Box] = {}
    roles: dict[str, str] = {}
    for item in items:
        prefixes = _path_prefixes(item.path)
        for prefix in prefixes:
            role = None
            if item.scope == "title-block":
                role = "title-block"
            elif item.scope.startswith("scale-bar"):
                role = "scale-bar"
            elif item.scope == "status-watermark":
                role = "watermark"
            if role is None:
                continue
            boxes[prefix] = item.box if prefix not in boxes else boxes[prefix].union(item.box)
            roles[prefix] = role
            break
    out: list[Item] = []
    counters: dict[str, int] = {}
    for path, box in boxes.items():
        role = roles[path]
        counters[role] = counters.get(role, 0) + 1
        scope = role if role != "scale-bar" else f"scale-bar#{counters[role]}"
        out.append(Item(role=role, box=box, scope=scope, tag="g", path=path))
    return out


def _path_prefixes(path: str) -> list[str]:
    parts = path.split("/")
    return ["/".join(parts[:index]) for index in range(len(parts) - 1, 0, -1)]


def _boxes_are_same_region(a: Box, b: Box, tol_px: float = 2.0) -> bool:
    """True when two boxes describe the same rectangle within a pixel tolerance.

    Used to let a declared panel supersede an auto-detected one. The tolerance
    absorbs stroke-width and rounding differences between the emitted rect and a
    hand-written declaration; it is deliberately tight so two genuinely different
    nested panels are never conflated.
    """

    return (
        abs(a.x0 - b.x0) <= tol_px
        and abs(a.y0 - b.y0) <= tol_px
        and abs(a.x1 - b.x1) <= tol_px
        and abs(a.y1 - b.y1) <= tol_px
    )


def _auto_panel_items(
    items: tuple[Item, ...], frame: Box, config: LegibilityConfig
) -> tuple[list[Item], dict[str, str]]:
    panels: list[Item] = []
    parents: dict[str, str] = {}
    min_area = frame.area * config.min_panel_area_frac
    index = 0
    for item in items:
        if item.role != "opaque-fill" or item.tag != "rect" or item.scope == "title-block":
            continue
        if item.box.area < min_area:
            continue
        if item.box.width < config.min_panel_side_px or item.box.height < config.min_panel_side_px:
            continue
        index += 1
        parent = item.path.rsplit("/", 1)[0]
        panel = Item(
            role="panel",
            box=item.box,
            scope=f"panel:auto#{index}",
            tag="rect",
            path=item.path,
            fill=item.fill,
            alpha=item.alpha,
        )
        panels.append(panel)
        parents[panel.path] = parent
    return panels, parents


def _check_overprint(
    model: SheetModel, config: LegibilityConfig, allowance_matches: set[int]
) -> list[Finding]:
    findings: list[Finding] = []
    texts = [item for item in model.items if item.role == "text"]
    opaque = [item for item in model.items if item.role == "opaque-fill"]
    for index, first in enumerate(texts):
        for second in texts[index + 1 :]:
            if first.scope.startswith("scale-bar") and first.scope == second.scope:
                continue
            dx, dy = first.box.overlap(second.box)
            threshold = config.min_overlap_px
            if first.confidence == "estimated" or second.confidence == "estimated":
                threshold *= 2.0
            if dx > threshold and dy > threshold:
                if _is_allowed(first, second, config, allowance_matches):
                    continue
                findings.append(
                    Finding(
                        _severity(config, "overprint", "error"),
                        "overprint",
                        (
                            f"text {first.text!r} overlaps text {second.text!r} "
                            f"by {dx:.1f} x {dy:.1f} px"
                        ),
                        first.box.union(second.box),
                        first.scope,
                    )
                )
    for text in texts:
        for shape in opaque:
            if shape.scope == text.scope and text.scope != "sheet":
                continue
            if shape.scope == "title-block":
                continue
            if _panel_for_backing(model, shape):
                continue
            dx, dy = text.box.overlap(shape.box)
            if dx > config.min_overlap_px and dy > config.min_overlap_px:
                if shape.box.contains(text.box, config.badge_tol_px):
                    continue
                if _is_allowed(text, shape, config, allowance_matches):
                    continue
                findings.append(
                    Finding(
                        _severity(config, "overprint", "error"),
                        "overprint",
                        (
                            f"text {text.text!r} overlaps opaque fill {shape.fill!r} "
                            f"by {dx:.1f} x {dy:.1f} px"
                        ),
                        text.box.union(shape.box),
                        text.scope,
                    )
                )
    return findings


def _panel_for_backing(model: SheetModel, shape: Item) -> bool:
    return any(panel.role == "panel" and panel.path == shape.path for panel in model.items)


def _check_frame_containment(model: SheetModel, config: LegibilityConfig) -> list[Finding]:
    findings: list[Finding] = []
    frame = model.frame
    if frame is None:
        return findings
    for item in model.items:
        if item.role in {"background", "frame", "watermark", "image"}:
            continue
        if item.tag in {"g", "declared-furniture"} or item.tag in DECLARED_REGION_TAGS:
            continue
        if frame.contains(item.box, config.frame_tol_px):
            continue
        left = max(frame.x0 - item.box.x0, 0.0)
        top = max(frame.y0 - item.box.y0, 0.0)
        right = max(item.box.x1 - frame.x1, 0.0)
        bottom = max(item.box.y1 - frame.y1, 0.0)
        parts = [
            f"{name} {value:.1f} px"
            for name, value in (
                ("left", left),
                ("top", top),
                ("right", right),
                ("bottom", bottom),
            )
            if value > 0
        ]
        severity = (
            "warn"
            if item.confidence == "curve-hull"
            else _severity(config, "frame-containment", "error")
        )
        note = " (path curve control-point hull)" if item.confidence == "curve-hull" else ""
        label = f"text {item.text!r}" if item.text else f"{item.role} {item.tag}"
        findings.append(
            Finding(
                severity,
                "frame-containment",
                f"{label} overflows frame by {', '.join(parts)}{note}",
                item.box,
                item.scope,
            )
        )
    return findings


def _check_title_block_clear(model: SheetModel, config: LegibilityConfig) -> list[Finding]:
    title_blocks = [item for item in model.items if item.role == "title-block"]
    if not title_blocks:
        return []
    findings: list[Finding] = []
    roles = {"text", "scale-bar", "panel", "opaque-fill"}
    for block in title_blocks:
        for item in model.items:
            if item.role not in roles:
                continue
            if item.scope == "title-block" or item.path == block.path:
                continue
            dx, dy = block.box.overlap(item.box)
            if dx > config.min_overlap_px and dy > config.min_overlap_px:
                label = f"text {item.text!r}" if item.text else item.role
                findings.append(
                    Finding(
                        _severity(config, "title-block-clear", "error"),
                        "title-block-clear",
                        f"{label} overlaps the title block by {dx:.1f} x {dy:.1f} px",
                        item.box.union(block.box),
                        item.scope,
                    )
                )
    return findings


def _check_panel_clear(
    model: SheetModel, config: LegibilityConfig
) -> tuple[list[Finding], list[Item]]:
    panels = [item for item in model.items if item.role == "panel"]
    findings: list[Finding] = []
    for panel in panels:
        # An in-view callout frames main-view features; that view's geometry
        # legitimately runs across its boundary, so intrusion is not a defect there.
        # It is still subject to inset-fill (P5-FINAL 4.1).
        if panel.tag == "declared-callout":
            continue
        for item in model.items:
            if item.path == panel.path or item.role in {
                "background",
                "frame",
                "watermark",
                "image",
            }:
                continue
            if item.role != "scale-bar" and item.scope.startswith("scale-bar"):
                continue
            if item.role == "panel":
                continue
            if _is_panel_descendant(model, panel, item, config):
                continue
            dx, dy = panel.box.overlap(item.box)
            if item.role in {"scale-bar", "title-block", "north-arrow"}:
                clearance = panel.box.clearance(item.box)
                if clearance < config.min_clearance_px:
                    findings.append(
                        Finding(
                            _severity(config, "panel-clear", "error"),
                            "panel-clear",
                            (
                                f"{item.role} intrudes on {panel.scope} "
                                f"(clearance {clearance:.1f} px; required "
                                f"{config.min_clearance_px:.1f} px)"
                            ),
                            item.box.union(panel.box),
                            panel.scope,
                        )
                    )
            elif dx > config.min_overlap_px and dy > config.min_overlap_px:
                label = f"text {item.text!r}" if item.text else item.role
                findings.append(
                    Finding(
                        _severity(config, "panel-clear", "error"),
                        "panel-clear",
                        f"{label} intrudes on {panel.scope} by {dx:.1f} x {dy:.1f} px",
                        item.box.union(panel.box),
                        panel.scope,
                    )
                )
    return findings, panels


def _is_panel_descendant(
    model: SheetModel, panel: Item, item: Item, config: LegibilityConfig
) -> bool:
    parent = model.panel_parents.get(panel.path)
    if parent and item.path.startswith(parent + "/"):
        return True
    if panel.tag in DECLARED_REGION_TAGS and panel.box.contains(item.box, config.badge_tol_px):
        return True
    return False


def _check_inset_fill(
    model: SheetModel, panels: list[Item], config: LegibilityConfig
) -> tuple[list[Finding], Skip | None]:
    if not panels:
        return [], Skip(
            "inset-fill",
            "no panels declared or detected: supply panels via out/<number>.legibility.json "
            "or legibility.yaml for declared extent checks",
        )
    findings: list[Finding] = []
    declared = {
        f"panel:{panel.name}": panel
        for panel in (model.declarations.panels if model.declarations else ())
    }
    for panel in panels:
        content = _panel_content(model, panel, config)
        if content is None:
            # A callout whose every enclosed item is coincident with its own
            # boundary cannot be told from a callout that frames its cluster
            # exactly -- they are the same bytes. Precision-first: say nothing.
            # An empty *detail* panel is unambiguous, and stays an error.
            if panel.tag == "declared-callout":
                continue
            findings.append(
                Finding(
                    _severity(config, "inset-fill", "error"),
                    "inset-fill",
                    f"{panel.scope} has no detail content",
                    panel.box,
                    panel.scope,
                )
            )
            continue
        declaration = declared.get(panel.scope)
        if declaration and declaration.extent_m:
            # The declaration carries no panel scale, so the content's model
            # extent is inferred from the pixel boxes (P5-FINAL Correction 2):
            #     content_extent_m = content_bbox / panel_box * declared_extent_m
            # The declared fill ratio content_extent_area / declared_extent_area
            # then reduces exactly to the pixel-area ratio below, so the two
            # forms of the spec's rule are one number, not two.
            ratio = content.area / panel.box.area if panel.box.area else 0.0
            cw = declaration.extent_m[0] * (content.width / panel.box.width)
            ch = declaration.extent_m[1] * (content.height / panel.box.height)
            if ratio < config.inset_extent_fill_min:
                findings.append(
                    Finding(
                        _severity(config, "inset-fill", "warn"),
                        "inset-fill",
                        (
                            f"{panel.scope} {declaration.kind} extent fill {ratio:.3f} < "
                            f"{config.inset_extent_fill_min:.2f}: content {cw:.1f} x {ch:.1f} m "
                            f"inside declared {declaration.extent_m[0]:.1f} x "
                            f"{declaration.extent_m[1]:.1f} m"
                        ),
                        content,
                        panel.scope,
                    )
                )
        else:
            ratio = content.area / panel.box.area if panel.box.area else 0.0
            if ratio < config.inset_content_fill_min:
                findings.append(
                    Finding(
                        _severity(config, "inset-fill", "warn"),
                        "inset-fill",
                        (
                            f"{panel.scope} content fill {ratio:.3f} < "
                            f"{config.inset_content_fill_min:.2f}"
                        ),
                        content,
                        panel.scope,
                    )
                )
    return findings, None


def _panel_content(model: SheetModel, panel: Item, config: LegibilityConfig) -> Box | None:
    content: Box | None = None
    for item in model.items:
        if item.path == panel.path or item.role in {"panel", "frame", "background", "watermark"}:
            continue
        if _is_region_furniture(item, panel):
            continue
        if item.role == "scale-bar":
            continue
        if item.role == "text" and item.text and _looks_like_panel_title(item, panel):
            continue
        if not _is_panel_descendant(model, panel, item, config):
            continue
        if item.tag in {"g", "declared-furniture"} or item.tag in DECLARED_REGION_TAGS:
            continue
        content = item.box if content is None else content.union(item.box)
    return content


def _check_marker_occlusion(model: SheetModel, config: LegibilityConfig) -> list[Finding]:
    """Numbered markers drawn on top of each other at co-located placements.

    A *marker* is a badge: an opaque shape large enough to be a background
    (``pointer_area_px2``) carrying exactly one short numeric label it fully
    contains. That is the same structural test the ``overprint`` badge rule uses
    to whitelist a legitimate bubble, which is precisely why this check is
    needed: ``overprint`` exempts a number sitting inside a bubble, so it cannot
    see a second bubble stacked on the first.

    Two markers occlude when their ink boxes overlap by more than
    ``min_overlap_px`` in both axes. The radius R in "N markers within R px" is
    therefore set by the markers' own geometry plus the one print-physics
    threshold this module already derives (1.5 px = 0.4 mm at the A3 canvas) --
    no new absolute distance is invented. ``marker_cluster_min`` defaults to 2
    because a single marker cannot occlude itself; it is arithmetic, not taste.
    """
    markers = _marker_badges(model, config)
    if len(markers) < config.marker_cluster_min:
        return []

    parent = list(range(len(markers)))

    def find(node: int) -> int:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    for first in range(len(markers)):
        for second in range(first + 1, len(markers)):
            dx, dy = markers[first][0].box.overlap(markers[second][0].box)
            if dx > config.min_overlap_px and dy > config.min_overlap_px:
                root_a, root_b = find(first), find(second)
                if root_a != root_b:
                    parent[max(root_a, root_b)] = min(root_a, root_b)

    clusters: dict[int, list[int]] = {}
    for index in range(len(markers)):
        clusters.setdefault(find(index), []).append(index)

    findings: list[Finding] = []
    for root in sorted(clusters):
        members = clusters[root]
        if len(members) < config.marker_cluster_min:
            continue
        box = markers[members[0]][0].box
        for index in members[1:]:
            box = box.union(markers[index][0].box)
        labels = ", ".join(repr(markers[index][1].text) for index in members)
        findings.append(
            Finding(
                _severity(config, "marker-occlusion", "error"),
                "marker-occlusion",
                (
                    f"{len(members)} numbered markers occlude each other at one placement "
                    f"({labels}): only the last drawn is readable"
                ),
                box,
                markers[members[0]][0].scope,
            )
        )
    return findings


def _marker_badges(model: SheetModel, config: LegibilityConfig) -> list[tuple[Item, Item]]:
    """(badge shape, its number label) pairs, in deterministic document order.

    A marker is keyed on the *number it carries*, not on each shape that happens
    to enclose it. Two structural rules keep real sheets quiet, and both were
    derived from false positives measured on the shipped P&ID:

    * Every text run a badge encloses must be a marker number. An ISA instrument
      bubble carries its tag on two lines ("FIC" over "101"), and "FIC" is not a
      number, so its loop number is never mistaken for a schedule marker's.
      Stacked markers, by contrast, enclose nothing but numbers -- which is why
      this is not simply "exactly one enclosed run": that rule would have
      excluded the very defect the check exists to find.
    * Where several shapes enclose the same number -- an ISA shared-display
      symbol is a circle inscribed in a square, and stacked markers enclose each
      other's numbers -- the number pairs with the nearest-centred, smallest
      enclosing shape. One symbol is one marker, not two.
    """
    texts = [item for item in model.items if item.role == "text"]
    numbers = [item for item in texts if _MARKER_NUMBER.fullmatch(item.text.strip())]
    if not numbers:
        return []
    numeric_paths = {number.path for number in numbers}
    candidates = [
        shape
        for shape in model.items
        if shape.role == "opaque-fill"
        and shape.scope != "title-block"
        and shape.box.area >= config.pointer_area_px2
        and not _panel_for_backing(model, shape)
        and all(
            text.path in numeric_paths
            for text in texts
            if shape.box.contains(text.box, config.badge_tol_px)
        )
    ]
    out: list[tuple[Item, Item]] = []
    for number in numbers:
        enclosing = [
            shape for shape in candidates if shape.box.contains(number.box, config.badge_tol_px)
        ]
        if not enclosing:
            continue
        badge = min(
            enclosing,
            key=lambda shape: (_centre_gap(shape.box, number.box), shape.box.area, shape.path),
        )
        out.append((badge, number))
    return out


def _centre_gap(first: Box, second: Box) -> float:
    return math.hypot(
        (first.x0 + first.x1) / 2.0 - (second.x0 + second.x1) / 2.0,
        (first.y0 + first.y1) / 2.0 - (second.y0 + second.y1) / 2.0,
    )


def _is_region_furniture(item: Item, panel: Item) -> bool:
    """Is this item the region's own boundary rather than its detail content?

    A detail panel's backing plate is an opaque rect coincident with the panel,
    which is what the check has always skipped. An in-view callout's boundary is
    a coincident *stroked* rect, so for a callout any coincident rect is the
    outline. Accepted false negative: content that exactly fills a callout is
    read as the outline, but that is the ratio-1.0 case the rule would pass
    anyway.
    """
    if not _same_box(item.box, panel.box, 0.5):
        return False
    if panel.tag == "declared-callout":
        return item.tag == "rect"
    return item.role == "opaque-fill"


def _looks_like_panel_title(item: Item, panel: Item) -> bool:
    text = item.text.lower()
    return (
        ("detail" in text or "inset" in text)
        and item.box.y0 <= panel.box.y0 + panel.box.height * 0.18
    )


def _check_status_coherence(
    model: SheetModel, meta: DrawingMeta | None, config: LegibilityConfig
) -> tuple[list[Finding], Skip | None]:
    if meta is None:
        return [], Skip(
            "status-coherence",
            "no meta.yaml: cannot compare the watermark with meta.status; pass meta= "
            "or check a drawing directory",
        )
    texts = [
        item.text.strip()
        for item in model.items
        if item.scope == "status-watermark" and item.text.strip()
    ]
    if not texts:
        return [
            Finding(
                _severity(config, "status-coherence", "error"),
                "status-coherence",
                'missing status watermark (expected marker class="status-watermark")',
                None,
            )
        ], None
    actual = " ".join(texts)
    expected = STATUS_WATERMARKS.get(meta.status_key, {}).get("text")
    if expected is None:
        return [
            Finding(
                "error",
                "status-coherence",
                f"meta status {meta.status!r} is not one of {STATUS_ORDER}",
                None,
            )
        ], None
    issued_text = STATUS_WATERMARKS["ISSUED"]["text"]
    matching_status = next(
        (key for key, spec in STATUS_WATERMARKS.items() if spec["text"] == actual),
        None,
    )
    if (
        issued_text in actual
        and meta.status_key != "ISSUED"
        or meta.for_construction
        and actual != issued_text
    ):
        other = matching_status or "custom"
        return [
            Finding(
                "error",
                "status-coherence",
                (
                    f"issued-gate: watermark status {other} ({actual!r}) contradicts "
                    f"meta status {meta.status_key}; ISSUED FOR CONSTRUCTION cannot be implied"
                ),
                None,
            )
        ], None
    if actual == expected:
        return [], None
    if matching_status is not None:
        return [
            Finding(
                _severity(config, "status-coherence", "error"),
                "status-coherence",
                (
                    f"watermark status {matching_status} ({actual!r}) does not match "
                    f"meta status {meta.status_key} ({expected!r})"
                ),
                None,
            )
        ], None
    return [
        Finding(
            _severity(config, "status-coherence", "warn"),
            "status-coherence",
            "custom watermark text "
            f"{actual!r} cannot be verified against meta status {meta.status_key}",
            None,
        )
    ], None


def _check_revision_coherence(
    model: SheetModel, meta: DrawingMeta | None, config: LegibilityConfig
) -> tuple[list[Finding], list[Skip]]:
    skips: list[Skip] = []
    findings: list[Finding] = []
    if meta is None:
        return [], [
            Skip(
                "revision-coherence",
                "no meta.yaml: cannot compare title-block revision and drawing number; "
                "pass meta= or check a drawing directory",
            )
        ]
    texts = [item for item in model.items if item.role == "text" and item.scope == "title-block"]
    labels = [item.text.strip() for item in texts]
    if "REV." not in labels or "DRAWING NO." not in labels:
        skips.append(
            Skip(
                "revision-coherence",
                "title-block field labels REV. and DRAWING NO. were not found in the "
                "supported Drawing title-block layout",
            )
        )
    else:
        rev = _field_after_label(texts, "REV.")
        number = _field_after_label(texts, "DRAWING NO.")
        if rev is not None and rev.strip() != str(meta.revision):
            findings.append(
                Finding(
                    _severity(config, "revision-coherence", "error"),
                    "revision-coherence",
                    f"title-block revision {rev!r} does not match meta.revision {meta.revision!r}",
                    None,
                    "title-block",
                )
            )
        if number is not None and str(meta.number) not in number:
            findings.append(
                Finding(
                    _severity(config, "revision-coherence", "error"),
                    "revision-coherence",
                    "title-block drawing number "
                    f"{number!r} does not contain meta.number {meta.number!r}",
                    None,
                    "title-block",
                )
            )
    if not isinstance(meta.extra.get("revisions"), list):
        skips.append(
            Skip(
                "revision-coherence:table",
                "no revisions: in meta.yaml and no revision table on the sheet - P9 (#61)",
            )
        )
    return findings, skips


def _field_after_label(texts: list[Item], label: str) -> str | None:
    for index, item in enumerate(texts):
        if item.text.strip() == label:
            for candidate in texts[index + 1 :]:
                if candidate.text.strip() and candidate.box.x0 > item.box.x0:
                    return candidate.text.strip()
    return None


def _check_legend_complete(
    model: SheetModel, config: LegibilityConfig
) -> tuple[list[Finding], Skip | None]:
    declaration = model.declarations.legend if model.declarations else None
    if declaration is None:
        return [], Skip(
            "legend-complete",
            "no legend declared: supply legend.entries and legend.used_keys via "
            "out/<number>.legibility.json or legibility.yaml",
        )
    findings: list[Finding] = []
    entries = {entry.key: entry for entry in declaration.entries}
    used = set(declaration.used_keys)
    labels = [item.text for item in model.items if item.role == "text"]
    for key in sorted(used - set(entries)):
        findings.append(
            Finding(
                _severity(config, "legend-complete", "error"),
                "legend-complete",
                f"legend key {key!r} is used on the sheet but has no legend entry",
                None,
            )
        )
    for key in sorted(set(entries) - used):
        findings.append(
            Finding(
                _severity(config, "legend-complete", "warn"),
                "legend-complete",
                f"legend entry {key!r} is declared but never used",
                None,
            )
        )
    for entry in declaration.entries:
        if not any(entry.label in label for label in labels):
            findings.append(
                Finding(
                    _severity(config, "legend-complete", "error"),
                    "legend-complete",
                    f"legend entry {entry.key!r} label {entry.label!r} is not drawn on the sheet",
                    None,
                )
            )
    return findings, None


def _check_verify_annotated(
    model: SheetModel, config: LegibilityConfig
) -> tuple[list[Finding], Skip | None]:
    declaration = model.declarations.verify if model.declarations else None
    if declaration is None:
        return [], Skip(
            "verify-annotated",
            "no verify declaration: supply verify.items and verify.marker via "
            "out/<number>.legibility.json or legibility.yaml",
        )
    marker = declaration.marker or config.verify_marker
    texts = [item.text for item in model.items if item.role == "text"]
    marker_runs = [text for text in texts if marker in text]
    findings: list[Finding] = []
    for item in declaration.items:
        if item.id:
            if not any(item.id in text and marker in text for text in texts):
                findings.append(
                    Finding(
                        _severity(config, "verify-annotated", "error"),
                        "verify-annotated",
                        f"verify item {item.id!r} is not annotated with marker {marker!r}",
                        None,
                    )
                )
        elif item.count is not None:
            if len(marker_runs) < item.count:
                findings.append(
                    Finding(
                        _severity(config, "verify-annotated", "error"),
                        "verify-annotated",
                        (
                            f"{len(marker_runs)} marker-bearing text runs found for "
                            f"{item.count} untagged verify items"
                        ),
                        None,
                    )
                )
            else:
                findings.append(
                    Finding(
                        _severity(config, "verify-annotated", "warn"),
                        "verify-annotated",
                        f"{item.count} verify items are untagged and cannot be "
                        "matched individually",
                        None,
                    )
                )
    if marker_runs and not any(_defines_marker(text, marker) for text in marker_runs):
        findings.append(
            Finding(
                _severity(config, "verify-annotated", "warn"),
                "verify-annotated",
                f"verify marker {marker!r} appears on the sheet but is not defined in a notes line",
                None,
            )
        )
    return findings, None


def _defines_marker(text: str, marker: str) -> bool:
    if marker not in text:
        return False
    rest = text.replace(marker, " ")
    return len(re.findall(r"[A-Za-z0-9]+", rest)) >= 3


def _severity(config: LegibilityConfig, check: str, default: Severity) -> Severity:
    return config.severities.get(check, default)  # type: ignore[return-value]


def _is_allowed(first: Item, second: Item, config: LegibilityConfig, matches: set[int]) -> bool:
    for index, allowance in enumerate(config.allow):
        if (
            _selector_matches(allowance.a, first)
            and _selector_matches(allowance.b, second)
            or _selector_matches(allowance.a, second)
            and _selector_matches(allowance.b, first)
        ):
            matches.add(index)
            return True
    return False


def _selector_matches(selector: str, item: Item) -> bool:
    if ":" in selector:
        role, needle = selector.split(":", 1)
        return item.role == role and needle in item.selector_text()
    return item.role == selector


def _stale_allowances(config: LegibilityConfig, matches: set[int]) -> list[Finding]:
    findings: list[Finding] = []
    for index, allowance in enumerate(config.allow):
        if index not in matches:
            findings.append(
                Finding(
                    "warn",
                    "overprint",
                    f"stale allowance {allowance.a!r} / {allowance.b!r} matched no finding",
                    None,
                )
            )
    return findings


def _required_skips(skipped: list[Skip], config: LegibilityConfig) -> list[Finding]:
    required = set(config.require)
    return [
        Finding("error", skip.check, f"required check skipped: {skip.reason}", None)
        for skip in skipped
        if skip.check in required
    ]


def _apply_baseline(
    findings: list[Finding], config: LegibilityConfig
) -> tuple[list[Finding], list[Finding]]:
    remaining: list[Finding] = []
    baselined: list[Finding] = []
    entries = {(entry.check, entry.message) for entry in config.baseline}
    for finding in findings:
        if (finding.check, finding.message) in entries:
            baselined.append(finding)
        else:
            remaining.append(finding)
    return remaining, baselined


def _stale_baselines(
    config: LegibilityConfig,
    baselined: list[Finding],
    items: tuple[Item, ...],
) -> list[Finding]:
    matched = {(finding.check, finding.message) for finding in baselined}
    findings: list[Finding] = []
    for entry in config.baseline:
        if (entry.check, entry.message) not in matched:
            if not _baseline_relevant(entry, items):
                continue
            findings.append(
                Finding(
                    "warn",
                    entry.check,
                    f"stale baseline entry for {entry.check}: {entry.message} - remove it",
                    None,
                )
            )
    return findings


def _baseline_relevant(entry: BaselineEntry, items: tuple[Item, ...]) -> bool:
    quoted = re.findall(r"'([^']+)'", entry.message)
    if not quoted:
        return True
    haystack = "\n".join(item.selector_text() for item in items)
    return any(token in haystack for token in quoted)


def _sort_findings(findings: Iterable[Finding]) -> list[Finding]:
    return sorted(
        findings,
        key=lambda finding: (
            SeverityRank[finding.severity],
            finding.check,
            round(finding.location.y0, 3) if finding.location else -1.0,
            round(finding.location.x0, 3) if finding.location else -1.0,
            finding.message,
        ),
    )


def _merge_reports(target: Path, reports: list[LegibilityReport]) -> LegibilityReport:
    frame = reports[0].frame if len(reports) == 1 else None
    findings = tuple(finding for report in reports for finding in report.findings)
    skipped = tuple(skip for report in reports for skip in report.skipped)
    checked = tuple(sorted({check for report in reports for check in report.checked}))
    items = tuple(item for report in reports for item in report.items)
    baselined = tuple(finding for report in reports for finding in report.baselined)
    return LegibilityReport(
        target=target,
        frame=frame,
        findings=tuple(_sort_findings(findings)),
        skipped=tuple(sorted(skipped, key=lambda skip: (skip.check, skip.reason))),
        checked=checked,
        items=items,
        baselined=tuple(_sort_findings(baselined)),
    )


def _prefix_report(report: LegibilityReport, name: str) -> LegibilityReport:
    return LegibilityReport(
        target=report.target,
        frame=report.frame,
        findings=tuple(
            replace(finding, message=f"{name}: {finding.message}") for finding in report.findings
        ),
        skipped=tuple(
            Skip(skip.check, f"{name}: {skip.reason}") for skip in report.skipped
        ),
        checked=tuple(f"{name}: {check}" for check in report.checked),
        items=report.items,
        baselined=tuple(
            replace(finding, message=f"{name}: {finding.message}") for finding in report.baselined
        ),
    )


def _load_legibility_yaml(path: Path) -> tuple[LegibilityConfig | None, Declarations | None]:
    if not path.exists():
        return None, None
    return load_config(path), load_declarations(path)


def _load_sidecar_declarations(svg_path: Path) -> Declarations | None:
    sidecar = svg_path.with_suffix(".legibility.json")
    return load_declarations(sidecar) if sidecar.exists() else None


def _merge_declarations(
    first: Declarations | None,
    second: Declarations | None,
) -> Declarations | None:
    if first is None:
        return second
    if second is None:
        return first
    return Declarations(
        version=second.version,
        panels=second.panels or first.panels,
        legend=second.legend or first.legend,
        verify=second.verify or first.verify,
        furniture=second.furniture or first.furniture,
        frame=second.frame or first.frame,
    )


def _profile_for_dir(drawing_dir: Path) -> LegibilityConfig:
    if list(drawing_dir.glob("*.pid.yaml")):
        return ISO_PX_DEFAULTS
    return CAD_PX_DEFAULTS


def _same_box(first: Box, second: Box, tol: float) -> bool:
    return (
        abs(first.x0 - second.x0) <= tol
        and abs(first.y0 - second.y0) <= tol
        and abs(first.x1 - second.x1) <= tol
        and abs(first.y1 - second.y1) <= tol
    )


def _numbered_scope(prefix: str, number: int) -> str:
    return f"{prefix}#{number}"


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


_CJK_RANGES = (
    (0x3400, 0x4DBF),
    (0x4E00, 0x9FFF),
    (0x3040, 0x30FF),
    (0xAC00, 0xD7AF),
    (0xFF00, 0xFFEF),
)

_HELVETICA_WIDTHS = {
    " ": 278,
    "!": 278,
    '"': 355,
    "#": 556,
    "$": 556,
    "%": 889,
    "&": 667,
    "'": 191,
    "(": 333,
    ")": 333,
    "*": 389,
    "+": 584,
    ",": 278,
    "-": 333,
    ".": 278,
    "/": 278,
    "0": 556,
    "1": 556,
    "2": 556,
    "3": 556,
    "4": 556,
    "5": 556,
    "6": 556,
    "7": 556,
    "8": 556,
    "9": 556,
    ":": 278,
    ";": 278,
    "<": 584,
    "=": 584,
    ">": 584,
    "?": 556,
    "@": 1015,
    "A": 667,
    "B": 667,
    "C": 722,
    "D": 722,
    "E": 667,
    "F": 611,
    "G": 778,
    "H": 722,
    "I": 278,
    "J": 500,
    "K": 667,
    "L": 556,
    "M": 833,
    "N": 722,
    "O": 778,
    "P": 667,
    "Q": 778,
    "R": 722,
    "S": 667,
    "T": 611,
    "U": 722,
    "V": 667,
    "W": 944,
    "X": 667,
    "Y": 667,
    "Z": 611,
    "[": 278,
    "\\": 278,
    "]": 278,
    "^": 469,
    "_": 556,
    "`": 333,
    "a": 556,
    "b": 556,
    "c": 500,
    "d": 556,
    "e": 556,
    "f": 278,
    "g": 556,
    "h": 556,
    "i": 222,
    "j": 222,
    "k": 500,
    "l": 222,
    "m": 833,
    "n": 556,
    "o": 556,
    "p": 556,
    "q": 556,
    "r": 333,
    "s": 500,
    "t": 278,
    "u": 556,
    "v": 500,
    "w": 722,
    "x": 500,
    "y": 500,
    "z": 500,
    "{": 334,
    "|": 260,
    "}": 334,
    "~": 584,
    "\u2014": 1000,
    "\u00b7": 278,
    "\u2013": 556,
}


def _text_width(text: str, font_size: float, family: str) -> tuple[float, str]:
    if any(_is_cjk(char) for char in text):
        total = sum(1.0 if _is_cjk(char) else 0.60 for char in text)
        return total * font_size, "estimated"
    fam = family.lower()
    if "mono" in fam or "courier" in fam:
        return len(text) * 0.60 * font_size, "exact"
    if "helvetica" in fam or "arial" in fam or "sans-serif" in fam:
        return sum(_HELVETICA_WIDTHS.get(char, 556) for char in text) / 1000.0 * font_size, "table"
    return len(text) * 0.62 * font_size, "estimated"


def _is_cjk(char: str) -> bool:
    code = ord(char)
    return any(lo <= code <= hi for lo, hi in _CJK_RANGES)


def _vertical_metrics(text: str, font_size: float) -> tuple[float, float]:
    tall = any(ch.isupper() or ch.isdigit() or ch in "bdfhklt[]{}()" for ch in text)
    desc = any(ch in "gjpqy,;()[]{}/@|_" for ch in text)
    ascent = (0.75 if tall else 0.53) * font_size
    descent = (0.21 if desc else 0.02) * font_size
    return ascent, descent
