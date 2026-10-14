"""Title block, revision register, status line, notes and references (clause 14)."""

from __future__ import annotations

import datetime
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

import jsonschema

from . import resources
from .entities import _line, _polyline, _text
from .numbers import FIT_TOL_MM
from .rules import text_width

FURNITURE_LAYER = "TITLEBLOCK"
# 14.9: the status line. `issued` has none.
STATUS_LINE = {"draft": "DRAFT \u2013 NOT FOR CONSTRUCTION", "concept": "CONCEPT \u2013 NOT FOR CONSTRUCTION"}
STATUS_HEIGHT, STATUS_PAD, STATUS_REGION_HEIGHT = 2.5, 2.0, 6.5
STRIP_PAD, STRIP_HEADING, STRIP_ITEM, STRIP_PITCH = 2.0, 3.5, 2.5, 4.5
GIVEN = ("owner", "title", "supplementary_title", "document_number", "document_type", "language", "department")


@dataclass
class Plan:
    entities: list = field(default_factory=list)
    errors: set = field(default_factory=set)
    regions: list = field(default_factory=list)  # (name, (x, y, w, h))


# ------------------------------------------------------------------------------------------------
# template


def template_schema() -> dict:
    return resources._json("schema", "title-block-template-1.0.schema.json")


def load_template(block, base_dir):
    """14.2: the template of a title block, or ``(None, {"14.2"})``."""
    try:
        if "template" in block:
            if block["template"] != "iso7200":
                return None, {"14.2"}
            data = resources.title_block_iso7200()
        else:
            data = json.loads((Path(base_dir or ".") / block["template_path"]).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, {"14.2"}
    if any(True for _ in jsonschema.Draft202012Validator(template_schema()).iter_errors(data)):
        return None, {"14.2"}
    if data["standard_version"].split(".")[0] != "1":
        return None, {"14.2"}
    return data, set()


def _fits(text: str, height: float, width: float) -> bool:
    return text_width(text, height) <= width + FIT_TOL_MM


def template_ok(t) -> bool:
    """14.2.1."""
    w, h, pad = t["width_mm"], t["height_mm"], t.get("pad_mm", 1)
    for ln in t["lines"]:
        for x, y in (ln["from_mm"], ln["to_mm"]):
            if not (-FIT_TOL_MM <= x <= w + FIT_TOL_MM and -FIT_TOL_MM <= y <= h + FIT_TOL_MM):
                return False
    fields = [c["field"] for c in t["cells"]]
    if len(fields) != len(set(fields)):
        return False
    for c in t["cells"]:
        if c["x_mm"] < 0 or c["y_mm"] < 0 or c["x_mm"] + c["width_mm"] > w + FIT_TOL_MM or c["y_mm"] + c["height_mm"] > h + FIT_TOL_MM:
            return False
        label_h = c.get("label_height_mm", 2.5)
        if label_h + c["text_height_mm"] + 3 * pad > c["height_mm"] + FIT_TOL_MM:
            return False
        if c["label"] and not _fits(c["label"], label_h, c["width_mm"] - 2 * pad):
            return False
    cells = t["cells"]
    for i in range(len(cells)):
        for j in range(i + 1, len(cells)):
            a, b = cells[i], cells[j]
            ox = min(a["x_mm"] + a["width_mm"], b["x_mm"] + b["width_mm"]) - max(a["x_mm"], b["x_mm"])
            oy = min(a["y_mm"] + a["height_mm"], b["y_mm"] + b["height_mm"]) - max(a["y_mm"], b["y_mm"])
            if ox > FIT_TOL_MM and oy > FIT_TOL_MM:
                return False
    reg = t["register"]
    if abs(sum(c["width_mm"] for c in reg["columns"]) - w) > FIT_TOL_MM:
        return False
    if reg["row_height_mm"] < reg["text_height_mm"] + 2 * pad - FIT_TOL_MM:
        return False
    if len({c["field"] for c in reg["columns"]}) != 6:
        return False
    return all(not c["label"] or _fits(c["label"], reg["text_height_mm"], c["width_mm"] - 2 * pad) for c in reg["columns"])


# ------------------------------------------------------------------------------------------------
# revisions


def _alpha_index(token: str) -> int:
    n = 0
    for ch in token:
        n = n * 26 + (ord(ch) - 64)
    return n


def _token_key(scheme: str, token: str):
    if scheme == "alpha":
        return _alpha_index(token) if token.isalpha() and token.isupper() else None
    return int(token) if token.isdigit() else None


def canonical(entry, index: int, prior: str) -> str:
    """14.8."""
    payload = {
        "app": entry.get("app"), "by": entry["by"], "chk": entry.get("chk"), "date": entry["date"],
        "description": entry["description"], "index": index, "prior_digest": prior, "rev": entry["rev"],
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def seals(entries) -> list[str]:
    """14.8: the expected seal of every entry."""
    out, prior = [], ""
    for i, entry in enumerate(entries):
        prior = "sha256:" + hashlib.sha256(canonical(entry, i, prior).encode("utf-8")).hexdigest()
        out.append(prior)
    return out


def revision_errors(revisions) -> set[str]:
    errors: set[str] = set()
    scheme, entries = revisions.get("scheme", "alpha"), revisions["entries"]
    prior_key, prior_date = None, None
    for entry in entries:
        key = _token_key(scheme, entry["rev"])
        if key is None or (prior_key is not None and key <= prior_key):
            errors.add("14.7.4")
        else:
            prior_key = key
        try:
            date = datetime.date.fromisoformat(entry["date"])
        except ValueError:
            errors.add("14.7.5")
            date = None
        if date is not None:
            if prior_date is not None and date < prior_date:
                errors.add("14.7.5")
            prior_date = date
    expected = seals(entries)
    for entry, seal in zip(entries, expected):
        if "seal" in entry and entry["seal"] != seal:
            errors.add("14.7.6")
    return errors


def register_rows(entries, max_rows):
    """14.7.2: the rows (dicts of cell texts), oldest first, with the overflow row on top when needed."""
    if len(entries) <= max_rows:
        shown, rows = entries, []
    else:
        shown = entries[-(max_rows - 1):]
        rows = [{"description": f"{len(entries) - (max_rows - 1)} EARLIER REVISIONS NOT SHOWN"}]
    rows += [{"rev": e["rev"], "date": e["date"], "description": e["description"], "by": e["by"], "chk": e.get("chk", ""), "app": e.get("app", "")} for e in shown]
    return rows


# ------------------------------------------------------------------------------------------------
# the plan


def _overlap(a, b) -> bool:
    ox = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
    oy = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
    return ox > FIT_TOL_MM and oy > FIT_TOL_MM


def _inside(r, ds) -> bool:
    return r[0] >= ds[0] - FIT_TOL_MM and r[1] >= ds[1] - FIT_TOL_MM and r[0] + r[2] <= ds[0] + ds[2] + FIT_TOL_MM and r[1] + r[3] <= ds[1] + ds[3] + FIT_TOL_MM


def plan(doc, size, ds, group, scales, base_dir) -> Plan:
    """All the furniture of clause 14, or its errors."""
    out = Plan()
    if "revisions" in doc:
        out.errors |= revision_errors(doc["revisions"])
    entries = doc.get("revisions", {}).get("entries", [])
    block = doc.get("title_block")
    template = None
    if block:
        template, errs = load_template(block, base_dir)
        out.errors |= errs
        if template is not None and not template_ok(template):
            out.errors.add("14.2.1")
            template = None
    if template is not None:
        _title_block(doc, block, template, entries, size, ds, group, scales, out)
    band_top = min((r[1][1] for r in out.regions), default=ds[1] + ds[3])
    out.errors |= _strips(doc, (ds[0], ds[1], ds[2], band_top - ds[1]), group, out)
    status = doc.get("status")
    if status == "issued":
        latest = entries[-1] if entries else None
        if block is None or latest is None or not latest.get("app", "").strip() or "seal" not in latest or latest["seal"] != seals(entries)[-1]:
            out.errors.add("14.9")
    if status in STATUS_LINE:
        _status_line(doc, status, ds, out)
    regions = out.regions
    for i in range(len(regions)):
        if not _inside(regions[i][1], ds):
            out.errors.add("14.5")
        for j in range(i + 1, len(regions)):
            if _overlap(regions[i][1], regions[j][1]):
                out.errors.add("14.5")
    return out


def _status_line(doc, status, ds, out: Plan):
    """14.9: one line of small text, in the notes strip when there is one, else in a reserved region."""
    text = STATUS_LINE[status]
    notes = next((s["id"] for s in doc.get("strips", []) if s["content"] == "notes"), None)
    rect = next((r for name, r in out.regions if name == notes), None) if notes is not None else None
    if notes is not None and rect is None:
        return  # the strip has an error of its own
    if rect is not None:
        x, y, w, h = rect
        anchor = (x + STATUS_PAD, y + h - STATUS_PAD)
        if not _fits(text, STATUS_HEIGHT, w - 2 * STATUS_PAD):
            out.errors.add("14.6")
    else:
        width = text_width(text, STATUS_HEIGHT)
        region = (ds[0], ds[1] + ds[3] - STATUS_REGION_HEIGHT, width + 2 * STATUS_PAD, STATUS_REGION_HEIGHT)
        out.regions.append(("status", region))
        anchor = (ds[0] + STATUS_PAD, ds[1] + ds[3] - STATUS_PAD)
    out.entities.append(_text("sheet.status_line", FURNITURE_LAYER, anchor, text, STATUS_HEIGHT, 0, "left", "baseline"))


def _derived(doc, entries, size, scales):
    latest = entries[-1] if entries else {}
    block_fields = doc.get("title_block", {}).get("fields", {})
    values = {k: block_fields.get(k, "") for k in GIVEN}
    values.update(
        revision=latest.get("rev", ""), date_of_issue=latest.get("date", ""), created_by=latest.get("by", ""),
        checked_by=latest.get("chk", ""), approved_by=latest.get("app", ""), status=doc.get("status", "").upper(),
        scale=(f"1:{scales[0]}" if scales and None not in scales and len(set(scales)) == 1 else ("AS SHOWN" if scales and None not in scales else "")),
        sheet=(f"{block_fields['sheet_number']}/{block_fields['sheet_count']}" if "sheet_number" in block_fields and "sheet_count" in block_fields else ""),
        sheet_size=size,
    )
    return values


def _title_block(doc, block, t, entries, size, ds, group, scales, out: Plan):
    W, H, pad = t["width_mm"], t["height_mm"], t.get("pad_mm", 1)
    bx, by = ds[0] + ds[2] - W, ds[1] + ds[3] - H
    out.regions.append(("title_block", (bx, by, W, H)))
    values = _derived(doc, entries, size, scales)
    cell_fields = {c["field"] for c in t["cells"]}
    for key in block["fields"]:
        if (key in GIVEN and key not in cell_fields) or (key in ("sheet_number", "sheet_count") and "sheet" not in cell_fields):
            out.errors.add("14.4.3")
    origin, lay = "sheet.title_block", FURNITURE_LAYER
    for ln in t["lines"]:
        a, b = ln["from_mm"], ln["to_mm"]
        out.entities.append(_line(origin, lay, (bx + a[0], by + a[1]), (bx + b[0], by + b[1]), "continuous", group[ln["role"]]))
    for c in t["cells"]:
        value = values.get(c["field"], "")
        if c.get("required") and not value:
            out.errors.add("14.4.3")
        cx, cy, cw, ch = bx + c["x_mm"], by + c["y_mm"], c["width_mm"], c["height_mm"]
        label_h = c.get("label_height_mm", 2.5)
        if c["label"]:
            out.entities.append(_text(origin, lay, (cx + pad, cy + pad + label_h), c["label"], label_h, 0, "left", "baseline"))
        if value:
            if not _fits(value, c["text_height_mm"], cw - 2 * pad):
                out.errors.add("14.6")
            align = c.get("align_h", "left")
            x = {"left": cx + pad, "centre": cx + cw / 2, "right": cx + cw - pad}[align]
            out.entities.append(_text(origin, lay, (x, cy + ch - pad), value, c["text_height_mm"], 0, align, "baseline"))
    if entries:
        _register(entries, t, bx, by, W, pad, group, out)


def _register(entries, t, bx, by, W, pad, group, out: Plan):
    reg = t["register"]
    rows = register_rows(entries, reg["max_rows"])
    rh, th = reg["row_height_mm"], reg["text_height_mm"]
    height = (1 + len(rows)) * rh
    ry = by - height
    out.regions.append(("register", (bx, ry, W, height)))
    origin, lay = "sheet.revision_register", FURNITURE_LAYER
    out.entities.append(_polyline(origin, lay, [(bx, ry), (bx + W, ry), (bx + W, by), (bx, by)], True, "continuous", group["thick"]))
    for i in range(1, len(rows) + 1):
        out.entities.append(_line(origin, lay, (bx, ry + i * rh), (bx + W, ry + i * rh), "continuous", group["thin"]))
    x = bx
    for col in reg["columns"][:-1]:
        x += col["width_mm"]
        out.entities.append(_line(origin, lay, (x, ry), (x, by), "continuous", group["thin"]))
    table = [{c["field"]: c["label"] for c in reg["columns"]}] + rows
    for r, row in enumerate(table):
        x = bx
        for col in reg["columns"]:
            text = row.get(col["field"], "")
            if text:
                if not _fits(text, th, col["width_mm"] - 2 * pad):
                    out.errors.add("14.6")
                out.entities.append(_text(origin, lay, (x + pad, ry + (r + 1) * rh - pad), text, th, 0, "left", "baseline"))
            x += col["width_mm"]


def _strips(doc, area, group, out: Plan) -> set[str]:
    errors: set[str] = set()
    strips = doc.get("strips", [])
    ids = [s["id"] for s in strips]
    contents = [s["content"] for s in strips]
    if len(ids) != len(set(ids)) or len(contents) != len(set(contents)):
        errors.add("14.10")
    for member in ("notes", "references"):
        if member in doc and member not in contents:
            errors.add("14.10")
    free = area
    for s in strips:
        x, y, w, h = free
        size = s["size_mm"]
        if s["edge"] in ("left", "right"):
            if size >= w:
                errors.add("14.10")
                continue
            rect = (x, y, size, h) if s["edge"] == "left" else (x + w - size, y, size, h)
            free = (x + size, y, w - size, h) if s["edge"] == "left" else (x, y, w - size, h)
        else:
            if size >= h:
                errors.add("14.10")
                continue
            rect = (x, y, w, size) if s["edge"] == "top" else (x, y + h - size, w, size)
            free = (x, y + size, w, h - size) if s["edge"] == "top" else (x, y, w, h - size)
        out.regions.append((s["id"], rect))
        _strip_entities(doc, s, rect, group, out)
    return errors


def _strip_entities(doc, strip, rect, group, out: Plan):
    x, y, w, h = rect
    kind = strip["content"]
    origin, lay = f"sheet.{kind}", FURNITURE_LAYER
    out.entities.append(_polyline(origin, lay, [(x, y), (x + w, y), (x + w, y + h), (x, y + h)], True, "continuous", group["thin"]))
    out.entities.append(_text(origin, lay, (x + STRIP_PAD, y + STRIP_PAD + STRIP_HEADING), kind.upper(), STRIP_HEADING, 0, "left", "baseline"))
    # 14.10.1: the status line stands on the last line of a notes strip
    reserve = STRIP_PITCH if kind == "notes" and doc.get("status") in STATUS_LINE else 0.0
    if reserve and y + STRIP_PAD + STRIP_HEADING > y + h - STRIP_PAD - reserve + FIT_TOL_MM:
        out.errors.add("14.6")
    if kind == "notes":
        items = [f"{i}. {n}" for i, n in enumerate(doc.get("notes", []), start=1)]
    else:
        items = ["  ".join(p for p in (r["document_number"], r.get("revision", ""), r["title"]) if p) for r in doc.get("references", [])]
    for i, text in enumerate(items, start=1):
        baseline = y + STRIP_PAD + STRIP_HEADING + STRIP_PITCH * i
        if baseline > y + h - STRIP_PAD - reserve + FIT_TOL_MM or not _fits(text, STRIP_ITEM, w - 2 * STRIP_PAD):
            out.errors.add("14.6")
        out.entities.append(_text(origin, lay, (x + STRIP_PAD, baseline), text, STRIP_ITEM, 0, "left", "baseline"))
