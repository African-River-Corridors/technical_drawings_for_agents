"""The sheet furniture that sits beside the title block, from a YAML settings file.

Many house sheets carry three objects that are **not** title-block cells: a
references table, a revisions table, and a right-hand notes column. They are drawn
here.

Why a separate module rather than more cells:

* **They are outside the block.** In the shipped example the references and
  revisions tables span ``-380 .. -200`` mm from the sheet's right edge, left of the
  200 mm title block, and the notes column sits *above* the block. A block-relative
  layout cannot express any of them without placing them where they do not sit.
* **Their text belongs to the layout, not the drawing.** A cell's value comes from
  ``getattr(fields, cell.field)``, which is right for a title or an approver and
  wrong for a table heading. Adding headings as ``TitleBlockFields`` attributes
  would put constant strings into the mechanism that deliberately refuses to
  default an approver's name.

Geometry comes from a settings file, not from constants here, because these are
per-house furniture: a neutral example ships as
``docs/specs/sheet-furniture.example.yaml`` and your own sheet standard is your own
settings file rather than another module.

Coordinates in the settings file are measured from the frame's bottom-right corner —
x left from the frame's right edge (so ``<= 0``), y up from its bottom (``>= 0``) —
so a reviewer can check a number against a template without doing arithmetic. The
conversion to the frame's own top-left device space happens here, once.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as dataclass_field
from itertools import pairwise
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from .titleblock import PaperFrame, TitleBlockError, _fmt3, _rect, _text

#: Ink and rule weights, matched to the title block so the strip reads as one object.
_INK = "#111111"
_FILL = "#ffffff"
_RULE_MM = 0.25
_PAD_MM = 1.5

#: Character advance as a fraction of text height — the same 0.55 the title block
#: uses, so a capacity computed here and there agree.
_ADVANCE = 0.55


class SheetFurnitureError(TitleBlockError):
    """A furniture defect. Subclasses TitleBlockError so one except clause catches
    everything a sheet's paper-space furniture can raise."""


def _capacity(w_mm: float, text_mm: float, pad_mm: float = _PAD_MM) -> int:
    return int((w_mm - 2 * pad_mm) // (text_mm * _ADVANCE))


@dataclass(frozen=True)
class Column:
    """One column of a bottom-strip table."""

    x_mm: float
    w_mm: float
    field: str | None = None
    label: str = ""
    role: str = "field"

    @property
    def is_gutter(self) -> bool:
        return self.role == "gutter" or self.field is None


@dataclass(frozen=True)
class StripTable:
    """A table on the sheet's bottom strip: references, or revisions."""

    name: str
    x_from_mm: float
    x_to_mm: float
    heading: str
    row_height_mm: float
    max_rows: int
    columns: tuple[Column, ...]
    label_text_mm: float = 2.0
    value_text_mm: float = 2.0

    @property
    def width_mm(self) -> float:
        return self.x_to_mm - self.x_from_mm


@dataclass(frozen=True)
class NotesColumn:
    """The right-hand stack of drawing-wide notes, above the title block."""

    x_from_mm: float
    x_to_mm: float
    divider_y_mm: float | None
    entries: tuple[dict[str, Any], ...] = dataclass_field(default_factory=tuple)

    @property
    def width_mm(self) -> float:
        return self.x_to_mm - self.x_from_mm


@dataclass(frozen=True)
class SheetFurniture:
    """Everything on a sheet that is neither the drawing nor the title block."""

    id: str
    strip_width_mm: float
    pad_mm: float
    tables: tuple[StripTable, ...]
    notes: NotesColumn | None

    def table(self, name: str) -> StripTable:
        for table in self.tables:
            if table.name == name:
                return table
        raise SheetFurnitureError(f"no table named {name!r} in {self.id}")


# --------------------------------------------------------------------------- #
# loading
# --------------------------------------------------------------------------- #


def load_settings(path: str | Path) -> SheetFurniture:
    """Load a furniture settings file.

    Raises rather than defaulting on anything load-bearing. A furniture file with a
    missing width would otherwise draw a table of width zero, which renders as a
    line and reads as a design choice.
    """
    import yaml

    path = Path(path)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SheetFurnitureError(f"cannot read {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise SheetFurnitureError(f"{path}: expected a mapping at the top level")

    strip = raw.get("bottom_strip") or {}
    tables = tuple(
        _table(name, spec, path)
        for name, spec in (raw.get("tables") or {}).items()
    )
    notes_raw = raw.get("notes_column")
    notes = None
    if notes_raw:
        notes = NotesColumn(
            x_from_mm=_number(notes_raw, "x_from_mm", f"{path}: notes_column"),
            x_to_mm=_number(notes_raw, "x_to_mm", f"{path}: notes_column"),
            divider_y_mm=notes_raw.get("divider_y_mm"),
            entries=tuple(notes_raw.get("entries") or ()),
        )
    return SheetFurniture(
        id=str(raw.get("id") or path.stem),
        strip_width_mm=float(strip.get("total_width_mm") or 0.0),
        pad_mm=float(raw.get("pad_mm", _PAD_MM)),
        tables=tables,
        notes=notes,
    )


def _number(mapping: dict[str, Any], key: str, where: str) -> float:
    if key not in mapping or mapping[key] is None:
        raise SheetFurnitureError(f"{where}: '{key}' is required")
    try:
        return float(mapping[key])
    except (TypeError, ValueError) as exc:
        raise SheetFurnitureError(
            f"{where}: '{key}' must be a number, found {mapping[key]!r}"
        ) from exc


def _table(name: str, spec: dict[str, Any], path: Path) -> StripTable:
    where = f"{path}: tables.{name}"
    if not isinstance(spec, dict):
        raise SheetFurnitureError(f"{where}: expected a mapping")
    columns = []
    for index, col in enumerate(spec.get("columns") or ()):
        if not isinstance(col, dict):
            raise SheetFurnitureError(f"{where}.columns[{index}]: expected a mapping")
        columns.append(
            Column(
                x_mm=_number(col, "x_mm", f"{where}.columns[{index}]"),
                w_mm=_number(col, "w_mm", f"{where}.columns[{index}]"),
                field=col.get("field"),
                label=str(col.get("label") or ""),
                role=str(col.get("role") or ("gutter" if not col.get("field") else "field")),
            )
        )
    table = StripTable(
        name=name,
        x_from_mm=_number(spec, "x_from_mm", where),
        x_to_mm=_number(spec, "x_to_mm", where),
        heading=str(spec.get("heading") or ""),
        row_height_mm=_number(spec, "row_height_mm", where),
        max_rows=int(spec.get("max_rows") or 1),
        columns=tuple(columns),
    )
    _assert_columns_span(table, where)
    return table


def _assert_columns_span(table: StripTable, where: str) -> None:
    """Every column must sit inside the table, and they must not overlap.

    A column list that silently overhangs its table draws over whatever is next
    along the strip, which is usually another table rather than blank paper.
    """
    if not table.columns:
        return
    ordered = sorted(table.columns, key=lambda c: c.x_mm)
    for col in ordered:
        if col.x_mm < table.x_from_mm - 1e-6:
            raise SheetFurnitureError(
                f"{where}: column at {col.x_mm:g} starts left of the table's "
                f"{table.x_from_mm:g}"
            )
        if col.x_mm + col.w_mm > table.x_to_mm + 1e-6:
            raise SheetFurnitureError(
                f"{where}: column at {col.x_mm:g} + {col.w_mm:g} overruns the "
                f"table's right edge {table.x_to_mm:g}"
            )
    for left, right in pairwise(ordered):
        if left.x_mm + left.w_mm > right.x_mm + 1e-6:
            raise SheetFurnitureError(
                f"{where}: columns at {left.x_mm:g} and {right.x_mm:g} overlap"
            )


# --------------------------------------------------------------------------- #
# rendering
# --------------------------------------------------------------------------- #


def assert_strip_fits(frame: PaperFrame, furniture: SheetFurniture) -> None:
    """Raise if the bottom strip is wider than the frame.

    A strip wider than the frame is a margin decision (or a bigger sheet), not
    something to fix by shrinking a table.
    """
    _, _, frame_w_mm, _ = frame.frame_mm()
    if furniture.strip_width_mm > frame_w_mm + 1e-6:
        raise SheetFurnitureError(
            f"bottom strip is {furniture.strip_width_mm:g} mm but {frame.size} "
            f"{frame.orientation} gives a {frame_w_mm:g} mm frame; widen the frame "
            "(smaller margins) rather than narrowing the tables"
        )


def _to_frame(frame: PaperFrame, x_right_mm: float, y_up_mm: float) -> tuple[float, float]:
    """Settings coordinates (right/bottom origin) -> frame coordinates (top-left)."""
    fx_mm, fy_mm, fw_mm, fh_mm = frame.frame_mm()
    return (fx_mm + fw_mm + x_right_mm, fy_mm + fh_mm - y_up_mm)


def render_strip_table(
    frame: PaperFrame,
    table: StripTable,
    rows: list[dict[str, Any]] | None = None,
    *,
    pad_mm: float = _PAD_MM,
) -> str:
    """Render one bottom-strip table. `rows` beyond `max_rows` are refused, not dropped."""
    rows = list(rows or [])
    if len(rows) > table.max_rows:
        raise SheetFurnitureError(
            f"table '{table.name}' holds {table.max_rows} row(s) but {len(rows)} "
            "were given; drop the oldest deliberately rather than silently"
        )

    header_h = table.row_height_mm
    total_h = header_h + table.max_rows * table.row_height_mm
    x_mm, y_mm = _to_frame(frame, table.x_from_mm, total_h)
    parts = [
        (
            f'<g class="sheet-furniture" data-table="{escape(table.name)}" '
            f'data-extents-mm="{_fmt3(table.x_from_mm)} {_fmt3(table.width_mm)}" '
            'font-family="Helvetica, Arial, sans-serif">'
        ),
        _rect(frame, x_mm, y_mm, table.width_mm, total_h,
              fill=_FILL, stroke=_INK, stroke_width_mm=_RULE_MM),
    ]
    if table.heading:
        hx, hy = _to_frame(frame, table.x_from_mm + pad_mm, total_h - table.label_text_mm)
        parts.append(_text(frame, hx, hy, table.heading, table.label_text_mm,
                           field=f"{table.name}.heading", fill=_INK, weight="bold"))

    for col in table.columns:
        if col.is_gutter:
            continue
        cx, cy = _to_frame(frame, col.x_mm + pad_mm,
                           total_h - header_h - table.label_text_mm)
        if col.label:
            _assert_fits(col, col.label, table.label_text_mm, table.name, pad_mm)
            parts.append(_text(frame, cx, cy, col.label, table.label_text_mm,
                               field=f"{table.name}.{col.field}.label", fill=_INK))
        for row_index, row in enumerate(rows):
            value = row.get(col.field)
            if value in (None, ""):
                continue
            _assert_fits(col, str(value), table.value_text_mm, table.name, pad_mm)
            vy_up = total_h - header_h - (row_index + 1) * table.row_height_mm
            vx, vy = _to_frame(frame, col.x_mm + pad_mm, vy_up + table.value_text_mm * 0.2)
            parts.append(_text(frame, vx, vy, str(value), table.value_text_mm,
                               field=f"{table.name}.{col.field}", fill=_INK))
    parts.append("</g>")
    return "\n".join(parts)


def _assert_fits(col: Column, value: str, text_mm: float, table_name: str,
                 pad_mm: float = _PAD_MM) -> None:
    """Never ellipsise, exactly as the title block never does."""
    limit = _capacity(col.w_mm, text_mm, pad_mm)
    if len(value) > limit:
        raise SheetFurnitureError(
            f"{table_name}.{col.field}: value is {len(value)} chars but the column "
            f"holds {limit} at {text_mm:g} mm text"
        )


def render_notes_column(frame: PaperFrame, notes: NotesColumn,
                        values: dict[str, Any] | None = None) -> str:
    """Render the right-hand notes stack. A note with no value is omitted, not dashed:
    an empty coordinate system is a gap in the drawing, not a field to decorate."""
    values = values or {}
    parts = [
        (
            '<g class="sheet-furniture" data-notes-column="true" '
            'font-family="Helvetica, Arial, sans-serif">'
        )
    ]
    if notes.divider_y_mm is not None:
        dx, dy = _to_frame(frame, notes.x_from_mm, notes.divider_y_mm)
        parts.append(_rect(frame, dx, dy, notes.width_mm, _RULE_MM,
                           fill=_INK, stroke=_INK, stroke_width_mm=_RULE_MM))
    for entry in notes.entries:
        name = str(entry.get("field") or "")
        value = values.get(name)
        if value in (None, ""):
            continue
        text_mm = float(entry.get("text_mm") or 2.8)
        y_up = float(entry.get("y_mm") or 0.0)
        label = str(entry.get("label") or "")
        x_mm, y_mm = _to_frame(frame, notes.x_from_mm + _PAD_MM, y_up)
        if label:
            parts.append(_text(frame, x_mm, y_mm, label, text_mm,
                               field=f"notes.{name}.label", fill=_INK, weight="bold"))
            x_mm, y_mm = _to_frame(frame, notes.x_from_mm + _PAD_MM, y_up - text_mm * 1.2)
        parts.append(_text(frame, x_mm, y_mm, str(value), text_mm,
                           field=f"notes.{name}", fill=_INK))
    parts.append("</g>")
    return "\n".join(parts)
