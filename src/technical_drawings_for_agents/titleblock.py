"""ISO 7200-style title and revision blocks in paper millimetres (P9 / #61).

**The core design decision: the title block is a fixed physical size — 180 mm ×
56 mm — anchored to the frame's bottom-right corner. It does not scale with the
sheet.** That single sentence is why *one* implementation is correct at A3, A2,
A1 and A0 in both orientations: on paper a title block is a physical artifact of
a fixed size, exactly like the text height on it. A percentage-of-sheet block —
which is what a pixel block on a variable canvas amounts to — is guaranteed to be
wrong at every size but one. Sheet size affects only the anchor point, which
comes from the frame.

180 mm is the frame width of A4 portrait under the 10 mm house margin set
(``210 − 2 × 10 = 190``, and 180 leaves a margin inside that), so the block fits
the narrowest frame we could draw on. Confirming 180 mm and the ISO 7200
mandatory-field list against copies of ISO 5457 / ISO 7200:2004 is **open
question O-3**: the field *vocabulary* below is taken from
:mod:`technical_drawings_for_agents.isosheet`, which is ISO-7200-*style*, not a transcription of
the standard.

Two things this module deliberately does **not** do:

* it does not implement paper space. It consumes the 5-member :class:`PaperFrame`
  protocol read-only, so it works against the shipped :mod:`technical_drawings_for_agents.sheet`
  model and against a test ``FakeFrame`` identically, and it changes nothing that
  model owns;
* it does not ellipsise. Content that will not fit its cell raises
  :class:`TitleBlockError` naming the field, the length and the limit. A silently
  truncated title is invisible in code review, which is the whole defect.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from html import escape
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from .style import STATUS_WATERMARKS

if TYPE_CHECKING:
    from .meta import DrawingMeta
    from .revisions import Revision, RevisionRegister


class TitleBlockError(ValueError):
    """Raised when title-block content will not fit its cell, or the frame is too small."""


@runtime_checkable
class PaperFrame(Protocol):
    """The five members P9 needs from a paper-space model, and nothing more.

    All lengths are millimetres of paper. Origin is the paper's top-left corner,
    ``+x`` right, ``+y`` down (matching SVG). ``frame_mm`` is the *inner* frame
    rectangle that drawing content and the title block must sit inside.

    P9 reads this protocol and never reaches into a frame's internals, so the
    sheet model's own validation surface is untouched by anything here.
    """

    size: str
    orientation: str

    def frame_mm(self) -> tuple[float, float, float, float]:
        ...

    def to_device(self, x_mm: float, y_mm: float) -> tuple[float, float]:
        ...

    def device_per_mm(self) -> float:
        ...


#: Cell padding, paper mm.
PAD_MM = 1.5
#: Mean advance / cap height for the Helvetica/Arial stack. Pinned by a test so
#: the capacity table below is reproducible rather than a magic number.
ADVANCE_FACTOR = 0.55

_INK = "#111111"
_FILL = "#ffffff"
_MUTED = "#555555"
#: Empty cells render an em-dash: a blank cell is ambiguous between "not
#: applicable" and "renderer bug"; ``—`` states "deliberately not filled".
DASH = "—"


@dataclass(frozen=True)
class Cell:
    """One title-block cell, positioned relative to the block's top-left corner."""

    field: str
    label: str
    x_mm: float
    y_mm: float
    w_mm: float
    h_mm: float
    text_mm: float = 3.5
    max_lines: int = 1
    pair_date: bool = True
    """Whether a ``revision`` cell also carries the date of issue.

    True keeps the ISO block's behaviour, where REV / DATE is one two-line cell.
    A block with a narrow REV cell (``EXAMPLE_A3``'s is 20 mm) sets it False and
    carries the date in its revisions table instead; the capacity check would
    otherwise reject the 10-character date."""

    def capacity(self) -> int:
        """Characters this cell holds per line, from its width and text height."""
        return math.floor((self.w_mm - 2 * PAD_MM) / (self.text_mm * ADVANCE_FACTOR))


@dataclass(frozen=True)
class TitleBlockLayout:
    id: str
    width_mm: float
    height_mm: float
    cells: tuple[Cell, ...]
    rev_row_mm: float = 5.0
    rev_columns: tuple[tuple[str, float, float], ...] = ()
    max_rev_rows: int = 8
    label_mm: float = 2.5
    rev_text_mm: float = 2.5
    pad_mm: float = PAD_MM

    def cell(self, field: str) -> Cell:
        for cell in self.cells:
            if cell.field == field:
                return cell
        raise TitleBlockError(f"layout {self.id} has no cell named {field!r}")


#: The layout table. This constant is the single source of truth for the block's
#: geometry; the acceptance tests assert the emitted geometry equals it.
ISO7200_180MM = TitleBlockLayout(
    id="ISO7200_180MM",
    width_mm=180.0,
    height_mm=56.0,
    cells=(
        Cell("responsible_dept", "RESPONSIBLE DEPT.", 0.0, 0.0, 76.0, 14.0),
        Cell("technical_reference", "TECHNICAL REFERENCE", 76.0, 0.0, 54.0, 14.0),
        Cell("created_by", "CREATED BY", 130.0, 0.0, 25.0, 14.0),
        Cell("approved_by", "APPROVED BY", 155.0, 0.0, 25.0, 14.0),
        Cell("document_type", "DOCUMENT TYPE", 0.0, 14.0, 76.0, 14.0),
        Cell("document_status", "DOCUMENT STATUS", 76.0, 14.0, 54.0, 14.0, max_lines=2),
        Cell("checked_by", "CHECKED BY", 130.0, 14.0, 25.0, 14.0),
        Cell("sheet", "SHEET", 155.0, 14.0, 25.0, 14.0),
        Cell("title", "TITLE", 0.0, 28.0, 108.0, 20.0, text_mm=5.0, max_lines=2),
        Cell("identification_number", "DOC. No.", 108.0, 28.0, 45.0, 20.0),
        Cell("revision", "REV / DATE", 153.0, 28.0, 27.0, 20.0, max_lines=2),
        Cell("legal_owner", "LEGAL OWNER", 0.0, 48.0, 108.0, 8.0),
        Cell("provenance", "PROVENANCE", 108.0, 48.0, 72.0, 8.0),
    ),
    rev_columns=(
        ("rev", 0.0, 14.0),
        ("date", 14.0, 30.0),
        ("description", 44.0, 88.0),
        ("by", 132.0, 16.0),
        ("chk", 148.0, 16.0),
        ("app", 164.0, 16.0),
    ),
)

REVISION_HEADINGS = {
    "rev": "REV",
    "date": "DATE",
    "description": "DESCRIPTION",
    "by": "BY",
    "chk": "CHK",
    "app": "APP",
}

_CONTINUATION_REV = "+earlier"


def revision_column_capacity(
    column: str, *, layout: TitleBlockLayout = ISO7200_180MM
) -> int:
    """Characters a revision-table column holds. One formula, no duplicated literals."""
    for name, _x_mm, w_mm in layout.rev_columns:
        if name == column:
            return math.floor((w_mm - 2 * layout.pad_mm) / (layout.rev_text_mm * ADVANCE_FACTOR))
    raise TitleBlockError(f"layout {layout.id} has no revision column named {column!r}")


@dataclass(frozen=True)
class TitleBlockFields:
    """The one data model every title-block renderer draws its values from."""

    identification_number: str
    title: str
    revision: str
    date_of_issue: str
    document_type: str = ""
    document_status: str = ""
    legal_owner: str = ""
    responsible_dept: str = ""
    technical_reference: str = ""
    supplementary_title: str = ""
    created_by: str = ""
    checked_by: str = ""
    approved_by: str = ""
    sheet: str = ""
    provenance: str = ""
    project: str = ""
    """The project name, from ``meta.project``. ISO 7200 has no project cell; house
    blocks such as ``EXAMPLE_A3`` do."""
    scale: str = ""
    """The plot scale, e.g. "1:500".

    ISO 7200's field list has no scale cell; many house blocks (and ``EXAMPLE_A3``)
    do, which is why this is here rather than in the ISO vocabulary. It is DERIVED — from paper size and
    viewport extent — so a caller that knows the sheet supplies it, and a drawing
    never types it. Absent renders as an em-dash rather than as a guess."""

    @classmethod
    def from_meta(cls, meta: DrawingMeta) -> "TitleBlockFields":
        """Build fields from ``meta.yaml``.

        ``approved_by`` is populated **only** from ``revisions[-1].app``, read
        verbatim. There is no fallback to ``by``, no fallback to ``chk``, no
        ``$USER``, no git author, no config, no default string. Absent means
        ``""``, which the renderer draws as an em-dash. This is the single most
        important line in the module: an approver a tool can supply turns the
        responsible engineer's signature into a rendering default.
        """
        register = meta.register()
        latest = register.latest if register is not None and register.entries else None
        status = STATUS_WATERMARKS.get(meta.status_key, {}).get("text", str(meta.status))
        extra = meta.extra
        return cls(
            identification_number=str(meta.number),
            title=str(meta.title),
            revision=str(latest.rev if latest is not None else meta.revision),
            date_of_issue=str(latest.date if latest is not None else meta.date),
            document_type=str(
                extra.get("document_type") or extra.get("doctype") or meta.discipline
            ),
            document_status=status,
            legal_owner=str(extra.get("legal_owner") or meta.project),
            responsible_dept=str(extra.get("responsible_dept") or meta.discipline),
            technical_reference=str(extra.get("technical_reference") or ""),
            supplementary_title=str(extra.get("supplementary_title") or ""),
            created_by=str(latest.by if latest is not None else ""),
            checked_by=str(latest.chk or "" if latest is not None else ""),
            approved_by=str(latest.app or "" if latest is not None else ""),
            sheet=str(extra.get("sheet_number") or ""),
            provenance=str(extra.get("provenance") or ""),
            project=str(meta.project or ""),
            # Only ever the value the sheet model derived. `meta.yaml` deliberately
            # omits a top-level `scale:` once `sheet:` is present, so there is
            # nothing to read here when paper space owns the number — the renderer's
            # caller injects it. Never fall back to a typed string.
            scale=str(getattr(meta, "scale", "") or ""),
        )


#: A neutral example of a house title block, for A3 and larger sheets.
#:
#: It shows how to supply your own layout: build a :class:`TitleBlockLayout` with
#: your cells and pass it as ``layout=`` to :func:`iso7200_title_block` and
#: :func:`revision_block`. Nothing in the module is specific to this layout.
#:
#: At 200 x 36 mm it is wider than A4 portrait's 190 mm frame, so
#: :func:`assert_layout_fits` refuses it there — the same check protects any
#: user layout that is too big for the sheet it is drawn on.
#:
#: ``revision`` sets ``pair_date=False``: its 20 mm cell holds a short revision
#: code, and the date lives in the revisions table above the block.
EXAMPLE_A3 = TitleBlockLayout(
    id="EXAMPLE_A3",
    width_mm=200.0,
    height_mm=36.0,
    cells=(
        Cell("project", "PROJECT", 0.0, 0.0, 120.0, 9.0, text_mm=2.5),
        Cell("created_by", "DRAWN", 120.0, 0.0, 40.0, 9.0, text_mm=2.5),
        Cell("checked_by", "CHECKED", 160.0, 0.0, 40.0, 9.0, text_mm=2.5),
        Cell("title", "TITLE", 0.0, 9.0, 120.0, 27.0, text_mm=3.5, max_lines=3),
        Cell("approved_by", "APPROVED", 120.0, 9.0, 40.0, 9.0, text_mm=2.5),
        # DERIVED, never typed: the caller injects the scale the sheet computed.
        Cell("scale", "SCALE", 160.0, 9.0, 40.0, 9.0, text_mm=2.5),
        Cell("identification_number", "DRAWING No.", 120.0, 18.0, 60.0, 9.0, text_mm=2.5),
        Cell("revision", "REV", 180.0, 18.0, 20.0, 9.0, text_mm=2.5, pair_date=False),
        Cell("sheet", "SHEET", 120.0, 27.0, 40.0, 9.0, text_mm=2.5),
        Cell("document_status", "STATUS", 160.0, 27.0, 40.0, 9.0, text_mm=2.0),
    ),
    label_mm=2.0,
    rev_row_mm=5.0,
    rev_columns=(
        ("rev", 0.0, 14.0),
        ("date", 14.0, 26.0),
        ("description", 40.0, 112.0),
        ("by", 152.0, 16.0),
        ("chk", 168.0, 16.0),
        ("app", 184.0, 16.0),
    ),
)

#: Frame-width floor per layout, mm, for a layout that needs more frame than its
#: own width (e.g. room for furniture beside it). A layout absent from this mapping
#: imposes no floor beyond its own width.
_MIN_FRAME_WIDTH_MM: dict[str, float] = {}


def assert_layout_fits(frame: PaperFrame, layout: TitleBlockLayout) -> None:
    """Raise if `layout` cannot sit inside `frame`.

    It exists because a house block can be wider than A4 portrait's frame. The
    module anchors to whatever frame it is given, so a 200 mm block on a 190 mm
    frame would otherwise silently overhang the sheet edge.

    Checked against the layout's own width rather than the mapping alone, so a
    future layout is covered the moment it is defined.
    """
    _, _, w_mm, h_mm = frame.frame_mm()
    floor = max(_MIN_FRAME_WIDTH_MM.get(layout.id, 0.0), layout.width_mm)
    if w_mm + 1e-6 < floor:
        raise TitleBlockError(
            f"layout {layout.id} needs a frame at least {floor:g} mm wide; "
            f"{frame.size} {frame.orientation} gives {w_mm:g} mm"
        )
    if h_mm + 1e-6 < layout.height_mm:
        raise TitleBlockError(
            f"layout {layout.id} needs a frame at least {layout.height_mm:g} mm tall; "
            f"{frame.size} {frame.orientation} gives {h_mm:g} mm"
        )


def iso7200_title_block(
    frame: PaperFrame,
    fields: TitleBlockFields,
    *,
    layout: TitleBlockLayout = ISO7200_180MM,
) -> str:
    """Render the fixed-mm title block, anchored flush to the frame's bottom-right.

    ``class="title-block"`` is mandatory and non-negotiable — validation greps for
    that literal, so every existing drawing depends on it.
    """
    assert_layout_fits(frame, layout)
    x_mm, y_mm, w_mm, h_mm = _title_extents(frame, layout=layout)
    parts = [
        (
            f'<g class="title-block" data-extents-mm="{_extents((x_mm, y_mm, w_mm, h_mm))}" '
            f'data-sheet="{escape(str(frame.size))}" '
            f'data-orientation="{escape(str(frame.orientation))}" '
            'font-family="Helvetica, Arial, sans-serif">'
        ),
        _rect(frame, x_mm, y_mm, w_mm, h_mm, fill=_FILL, stroke=_INK, stroke_width_mm=0.25),
    ]
    for cell in layout.cells:
        parts.append(_render_cell(frame, fields, cell, x_mm, y_mm, layout))
    parts.append("</g>")
    return "\n".join(parts)


def revision_block(
    frame: PaperFrame,
    register: RevisionRegister,
    *,
    layout: TitleBlockLayout = ISO7200_180MM,
) -> tuple[str, list[str]]:
    """Render the revision table above the title block. Returns ``(svg, warnings)``.

    Oldest entry at the top, newest at the bottom adjacent to the title block, so
    a reviewer's eye lands on the newest description and the ``REV / DATE`` cell
    together. The table grows *upward* from a fixed bottom edge, so the title
    block never moves as revisions accumulate. An empty register renders nothing
    at all — a register-less drawing's sheet is unchanged.

    Overflow is visible, never silent: beyond ``max_rev_rows`` the oldest rows are
    replaced by one continuation row stating how many were dropped, and a warning
    is returned.
    """
    if not register.entries:
        return "", []

    entries = list(register.entries)
    warnings: list[str] = []
    rendered = entries
    continuation: str | None = None
    if len(entries) > layout.max_rev_rows:
        keep = layout.max_rev_rows - 1
        omitted = len(entries) - keep
        rendered = entries[-keep:]
        continuation = f"+ {omitted} EARLIER REVISIONS — SEE meta.yaml"
        warnings.append(
            f"revision-block: {len(entries)} revisions exceed max_rev_rows "
            f"{layout.max_rev_rows}; the oldest {omitted} are replaced by a "
            "continuation row"
        )

    data_rows = len(rendered) + (1 if continuation is not None else 0)
    # Raises if the combined block will not fit — the frame check happens before
    # any markup is produced, so a too-small frame yields no partial output.
    x_mm, y_mm, _, _ = title_block_extents_mm(frame, layout=layout, rev_rows=data_rows)
    h_mm = (1 + data_rows) * layout.rev_row_mm

    parts = [
        (
            f'<g class="revision-block" data-extents-mm="'
            f'{_extents((x_mm, y_mm, layout.width_mm, h_mm))}" '
            f'data-rows="{data_rows}" data-latest-rev="{escape(register.latest.rev)}" '
            'font-family="Helvetica, Arial, sans-serif">'
        ),
        _revision_cells(
            frame, x_mm, y_mm, REVISION_HEADINGS, layout, row_class="revision-header"
        ),
    ]
    row_y = y_mm + layout.rev_row_mm
    if continuation is not None:
        parts.append(
            _revision_cells(
                frame,
                x_mm,
                row_y,
                {
                    "rev": "",
                    "date": "",
                    "description": continuation,
                    "by": "",
                    "chk": "",
                    "app": "",
                },
                layout,
                row_class="revision-row continuation",
                row_attrs=f' data-rev="{_CONTINUATION_REV}"',
                check_capacity=False,
            )
        )
        row_y += layout.rev_row_mm
    for entry in rendered:
        parts.append(_revision_row(frame, x_mm, row_y, entry, layout))
        row_y += layout.rev_row_mm
    parts.append("</g>")
    return "\n".join(parts), warnings


def title_block_extents_mm(
    frame: PaperFrame,
    *,
    layout: TitleBlockLayout = ISO7200_180MM,
    rev_rows: int = 0,
) -> tuple[float, float, float, float]:
    """``(x, y, w, h)`` in mm of the combined title + revision block.

    Raises :class:`TitleBlockError` when it will not fit the frame. It does not
    clamp, scale down or drop cells: a silently shrunk title block is illegible
    and its illegibility is invisible in review.
    """
    title_x, title_y, width, height = _title_extents(frame, layout=layout)
    rev_height = (1 + rev_rows) * layout.rev_row_mm if rev_rows else 0.0
    y_mm = title_y - rev_height
    total_height = height + rev_height
    fx, fy, fw, fh = frame.frame_mm()
    if total_height > fh + 1e-9 or y_mm < fy - 1e-9:
        raise TitleBlockError(
            f"title block plus {rev_rows} revision row(s) needs {total_height:g} mm of "
            f"height but the {frame.size} {frame.orientation} frame is {fh:g} mm high"
        )
    return (title_x, y_mm, width, total_height)


# --------------------------------------------------------------------------- #
# internals
# --------------------------------------------------------------------------- #


def _title_extents(
    frame: PaperFrame, *, layout: TitleBlockLayout
) -> tuple[float, float, float, float]:
    fx, fy, fw, fh = frame.frame_mm()
    if layout.width_mm > fw + 1e-9:
        raise TitleBlockError(
            f"the {frame.size} {frame.orientation} frame is {fw:g} mm wide but layout "
            f"{layout.id} needs {layout.width_mm:g} mm"
        )
    if layout.height_mm > fh + 1e-9:
        raise TitleBlockError(
            f"the {frame.size} {frame.orientation} frame is {fh:g} mm high but layout "
            f"{layout.id} needs {layout.height_mm:g} mm"
        )
    return (
        fx + fw - layout.width_mm,
        fy + fh - layout.height_mm,
        layout.width_mm,
        layout.height_mm,
    )


def _render_cell(
    frame: PaperFrame,
    fields: TitleBlockFields,
    cell: Cell,
    block_x: float,
    block_y: float,
    layout: TitleBlockLayout,
) -> str:
    x_mm = block_x + cell.x_mm
    y_mm = block_y + cell.y_mm
    value_lines = _cell_value_lines(fields, cell)
    parts = [
        (
            f'<g class="tb-cell" data-field="{escape(cell.field)}" '
            f'data-extents-mm="{_extents((x_mm, y_mm, cell.w_mm, cell.h_mm))}">'
        ),
        _rect(
            frame,
            x_mm,
            y_mm,
            cell.w_mm,
            cell.h_mm,
            fill="none",
            stroke=_INK,
            stroke_width_mm=0.18,
        ),
        _text(
            frame,
            x_mm + layout.pad_mm,
            y_mm + layout.pad_mm + layout.label_mm,
            cell.label,
            layout.label_mm,
            field=cell.field,
            fill=_MUTED,
        ),
    ]
    first_y = y_mm + cell.h_mm - layout.pad_mm
    if len(value_lines) > 1:
        first_y -= (len(value_lines) - 1) * (cell.text_mm + 0.6)
    bold = cell.field in {"title", "identification_number"}
    for index, line in enumerate(value_lines):
        parts.append(
            _text(
                frame,
                x_mm + layout.pad_mm,
                first_y + index * (cell.text_mm + 0.6),
                line,
                cell.text_mm,
                field=cell.field,
                fill=_INK,
                weight="bold" if bold else "normal",
            )
        )
    parts.append("</g>")
    return "\n".join(parts)


def _cell_value_lines(fields: TitleBlockFields, cell: Cell) -> list[str]:
    """The value lines for one cell. Never ellipsises; raises instead."""
    if cell.field == "title" and fields.supplementary_title:
        lines = [str(fields.title), str(fields.supplementary_title)]
        _assert_lines_fit(cell, lines)
        return [_dash(line) for line in lines]
    if cell.field == "revision" and cell.pair_date:
        lines = [str(fields.revision), str(fields.date_of_issue)]
        _assert_lines_fit(cell, lines)
        return [_dash(line) for line in lines]
    return [_dash(line) for line in _wrap_or_error(cell, str(getattr(fields, cell.field)))]


def _wrap_or_error(cell: Cell, value: str) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return [""]
    cap = cell.capacity()
    limit = cap * cell.max_lines
    if len(text) > limit:
        raise TitleBlockError(
            f"{cell.field} is {len(text)} chars but the {cell.label} cell holds "
            f"{cell.max_lines} × {cap} = {limit} — shorten it or move the tail to "
            "supplementary_title"
        )
    if cell.max_lines == 1 or len(text) <= cap:
        return [text]
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if current and len(candidate) > cap:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    if len(lines) <= cell.max_lines and all(len(line) <= cap for line in lines):
        return lines
    # A single unbreakable run longer than one line: split on the character
    # boundary rather than dropping the tail.
    return [text[:cap].rstrip(), text[cap:].lstrip()]


def _assert_lines_fit(cell: Cell, lines: list[str]) -> None:
    cap = cell.capacity()
    if len(lines) > cell.max_lines:
        raise TitleBlockError(
            f"{cell.field} needs {len(lines)} lines but the {cell.label} cell holds "
            f"{cell.max_lines}"
        )
    for line in lines:
        if len(str(line)) > cap:
            raise TitleBlockError(
                f"{cell.field} line is {len(str(line))} chars but the {cell.label} "
                f"cell holds {cap} per line"
            )


def _revision_row(
    frame: PaperFrame,
    x_mm: float,
    y_mm: float,
    entry: Revision,
    layout: TitleBlockLayout,
) -> str:
    values = {
        "rev": entry.rev,
        "date": entry.date.isoformat(),
        "description": entry.description,
        "by": entry.by,
        "chk": entry.chk or "",
        "app": entry.app or "",
    }
    return _revision_cells(
        frame,
        x_mm,
        y_mm,
        values,
        layout,
        row_class="revision-row",
        row_attrs=f' data-rev="{escape(entry.rev)}"',
    )


def _revision_cells(
    frame: PaperFrame,
    x_mm: float,
    y_mm: float,
    values: dict[str, str],
    layout: TitleBlockLayout,
    *,
    row_class: str,
    row_attrs: str = "",
    check_capacity: bool = True,
) -> str:
    parts = [f'<g class="{row_class}"{row_attrs}>']
    for col, col_x, col_w in layout.rev_columns:
        value = _dash(values[col])
        if check_capacity and value != DASH:
            cap = revision_column_capacity(col, layout=layout)
            if len(value) > cap:
                raise TitleBlockError(
                    f"revision {col} is {len(value)} chars but the "
                    f"{REVISION_HEADINGS[col]} cell holds {cap}"
                )
        parts.extend(
            (
                (
                    f'<g class="revision-cell" data-col="{escape(col)}" '
                    f'data-extents-mm="'
                    f'{_extents((x_mm + col_x, y_mm, col_w, layout.rev_row_mm))}">'
                ),
                _rect(
                    frame,
                    x_mm + col_x,
                    y_mm,
                    col_w,
                    layout.rev_row_mm,
                    fill="none",
                    stroke=_INK,
                    stroke_width_mm=0.14,
                ),
                _text(
                    frame,
                    x_mm + col_x + layout.pad_mm,
                    y_mm + layout.rev_row_mm - layout.pad_mm,
                    value,
                    layout.rev_text_mm,
                    field=col,
                    fill=_INK,
                ),
                "</g>",
            )
        )
    parts.append("</g>")
    return "\n".join(parts)


def _rect(
    frame: PaperFrame,
    x_mm: float,
    y_mm: float,
    w_mm: float,
    h_mm: float,
    *,
    fill: str,
    stroke: str,
    stroke_width_mm: float,
) -> str:
    x0, y0 = frame.to_device(x_mm, y_mm)
    x1, y1 = frame.to_device(x_mm + w_mm, y_mm + h_mm)
    return (
        f'<rect x="{_fmt3(x0)}" y="{_fmt3(y0)}" width="{_fmt3(x1 - x0)}" '
        f'height="{_fmt3(y1 - y0)}" fill="{escape(fill)}" stroke="{escape(stroke)}" '
        f'stroke-width="{_fmt3(stroke_width_mm * frame.device_per_mm())}"/>'
    )


def _text(
    frame: PaperFrame,
    x_mm: float,
    y_mm: float,
    value: str,
    size_mm: float,
    *,
    field: str,
    fill: str,
    weight: str = "normal",
) -> str:
    x, y = frame.to_device(x_mm, y_mm)
    weight_attr = ' font-weight="bold"' if weight == "bold" else ""
    return (
        f'<text data-field="{escape(field)}" x="{_fmt3(x)}" y="{_fmt3(y)}" '
        f'font-size="{_fmt3(size_mm * frame.device_per_mm())}" '
        f'data-size-mm="{_fmt3(size_mm)}" fill="{escape(fill)}"'
        f"{weight_attr}>{escape(str(value))}</text>"
    )


def _dash(value: object) -> str:
    text = str(value).strip()
    return text or DASH


def _extents(values: tuple[float, float, float, float]) -> str:
    return " ".join(_fmt3(value) for value in values)


def _fmt3(value: float) -> str:
    return f"{float(value):.3f}"
