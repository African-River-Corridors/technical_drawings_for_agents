"""Drawing metadata (``meta.yaml``) model and helpers.

A drawing directory carries a ``meta.yaml`` recording its formal number,
title, revision, scale, units, date, status and generating tool, plus a hard
``for_construction`` flag. Per the standard, ``for_construction`` may only be
true once status is ISSUED (engineer sign-off).

P9 (#61) adds three **opt-in** keys — ``revisions:``, ``revision_scheme:`` and
``numbering:`` — and closes a silent no-op: before P9, ``revisions:`` fell through
``from_dict``'s whitelist into :attr:`DrawingMeta.extra`, so an author who added a
revision register got no error, no register and no revision table. Unknown
top-level keys still route to ``extra`` (the P&ID and BFD data files rely on it,
and P1's ``sheet:`` block is one of them); unknown keys *inside* the new
structures are rejected loudly, because a typo'd ``descripton:`` would otherwise
leave an issued revision with no recorded change note.

Everything new is gated on one of those keys being present, with one exception:
the strengthened for-construction gate. A drawing with no ``revisions:`` block
validates exactly as it did before this change, and renders byte-identically.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .numbering import BUILTIN_SCHEMES, NumberingError, NumberingScheme
from .revisions import DEFAULT_REV_SCHEME, RevisionError, RevisionRegister
from .style import STATUS_ORDER
from .titleblock import (
    ISO7200_180MM,
    PaperFrame,
    TitleBlockError,
    TitleBlockFields,
    iso7200_title_block,
    revision_block,
    title_block_extents_mm,
)


@dataclass
class DrawingMeta:
    number: str
    title: str
    revision: str = "A"
    scale: str = ""
    units: str = "m"
    #: Annotated ``str | datetime.date`` because ``yaml.safe_load`` of
    #: ``date: 2026-07-15`` yields a :class:`datetime.date` and nothing converts it.
    #: Widening the annotation is an honesty fix; changing the runtime value would
    #: alter every rendered sheet, so it is deliberately not done.
    date: str | datetime.date = ""
    status: str = "DRAFT"
    tool: str = "technical_drawings_for_agents"
    for_construction: bool = False
    deterministic: bool = False
    discipline: str = ""
    project: str = ""
    extra: dict = field(default_factory=dict)
    #: ``None`` means the key is absent (no register). ``[]`` means an author wrote
    #: ``revisions: []``, which is a mistake and reported as one — the distinction
    #: is why this is not ``field(default_factory=list)``.
    revisions: list | None = None
    revision_scheme: str = ""
    #: A built-in scheme id or an inline mapping. Empty means **not enforced**.
    numbering: Any = ""

    @property
    def status_key(self) -> str:
        return str(self.status).strip().upper()

    @property
    def sheet_config(self) -> dict | None:
        """The optional ``sheet:`` block (:mod:`technical_drawings_for_agents.sheet`), or ``None``.

        Unknown keys already route to :attr:`extra`, so a ``sheet:`` block needs no
        change to :meth:`from_dict` and none to :meth:`title_block`.
        """
        block = self.extra.get("sheet")
        return block if isinstance(block, dict) else None

    @classmethod
    def from_dict(cls, data: dict) -> "DrawingMeta":
        known = {
            "number",
            "title",
            "revision",
            "scale",
            "units",
            "date",
            "status",
            "tool",
            "for_construction",
            "deterministic",
            "discipline",
            "project",
            "revisions",
            "revision_scheme",
            "numbering",
        }
        kwargs = {k: v for k, v in data.items() if k in known}
        kwargs["extra"] = {k: v for k, v in data.items() if k not in known}
        return cls(**kwargs)

    @classmethod
    def load(cls, path: str | Path) -> "DrawingMeta":
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        return cls.from_dict(data)

    def validate(self) -> list[str]:
        """Return a list of human-readable problems (empty = valid).

        The four original checks run first, in their original order, with their
        original message text. Everything after them is additive and gated on an
        opt-in key — except the for-construction gate, which only ever gets
        *stricter* (see :meth:`_gate_problems`).
        """
        problems = []
        if not self.number:
            problems.append("meta: 'number' is required (stable drawing ID)")
        if not self.title:
            problems.append("meta: 'title' is required")
        if self.status_key not in STATUS_ORDER:
            problems.append(
                f"meta: status '{self.status}' not one of {STATUS_ORDER}"
            )
        # The hard safety rule: for-construction only after ISSUED sign-off.
        if self.for_construction and self.status_key != "ISSUED":
            problems.append(
                "meta: for_construction=true is only allowed once status is "
                "ISSUED (engineer sign-off required)"
            )
        problems.extend(self._p9_problems())
        return problems

    def warnings(self) -> list[str]:
        """Non-blocking observations: pre-register rows, overflow, future dates."""
        register, structural = self._register_or_problem()
        if register is None:
            return []
        _, warnings = register.validate(revision=self.revision)
        if structural is None and len(register.entries) > ISO7200_180MM.max_rev_rows:
            warnings.append(
                f"meta: revisions has {len(register.entries)} entries; the revision "
                f"block renders {ISO7200_180MM.max_rev_rows - 1} rows plus a "
                "continuation row"
            )
        return warnings

    def register(self) -> RevisionRegister | None:
        """The revision register, or ``None`` when ``revisions:`` is absent.

        Raises :class:`~technical_drawings_for_agents.revisions.RevisionError` on a structural
        problem. :meth:`validate` reports those as problem strings instead, so a
        caller asking for problems never has to catch.
        """
        if self.revisions is None:
            return None
        scheme = str(self.revision_scheme or DEFAULT_REV_SCHEME)
        return RevisionRegister.from_list(self.revisions, ctx="meta", scheme=scheme)

    def numbering_scheme(self) -> NumberingScheme | None:
        """The opt-in numbering scheme, or ``None`` when enforcement is off.

        ``None`` is the default and what every pre-P9 ``meta.yaml`` produces, so a
        legacy or non-conforming number is not a problem unless a project has
        declared a grammar to hold it to.
        """
        if self.numbering is None:
            return None
        if isinstance(self.numbering, str):
            key = self.numbering.strip()
            if not key:
                return None
            if key in BUILTIN_SCHEMES:
                return BUILTIN_SCHEMES[key]
            raise NumberingError(
                f"meta: numbering {key!r} is not a built-in scheme "
                f"(available: {', '.join(BUILTIN_SCHEMES)})"
            )
        if isinstance(self.numbering, dict):
            return NumberingScheme.from_dict(self.numbering, ctx="meta")
        raise NumberingError(
            f"meta: numbering must be a scheme id or a mapping, got "
            f"{type(self.numbering).__name__}"
        )

    def title_block_fields(self) -> TitleBlockFields:
        """Fields for the paper-space ISO 7200 title block."""
        return TitleBlockFields.from_meta(self)

    def title_block(self) -> dict:
        """Fields for :func:`technical_drawings_for_agents.svg.svg_title_block`. Unchanged by P9."""
        return {
            "project": self.project or self.discipline,
            "title": self.title,
            "drawing_no": f"{self.number}_Rev{self.revision}" if self.revision else self.number,
            "scale": self.scale,
            "date": self.date,
            "revision": self.revision,
        }

    # ----------------------------------------------------------------- #
    # P9 additive checks
    # ----------------------------------------------------------------- #

    def _register_or_problem(self) -> tuple[RevisionRegister | None, str | None]:
        """``(register, structural_problem)``. Never raises.

        Structural problems (unknown key, unparseable date) come back as a problem
        *string* rather than an exception so ``validate()`` keeps its contract of
        returning problems — and so a typo is reported loudly next to every other
        problem instead of aborting the run. Messages already carry the ``meta:``
        context, because the register is parsed with ``ctx="meta"``.
        """
        try:
            return self.register(), None
        except RevisionError as exc:
            return None, str(exc)

    def _p9_problems(self) -> list[str]:
        problems: list[str] = []

        register, structural = self._register_or_problem()
        if structural is not None:
            problems.append(structural)
        elif register is not None:
            # V1/V2/V3/V7 — field rules, ordering rules R1-R6, and every seal.
            register_problems, _ = register.validate(revision=self.revision)
            problems.extend(register_problems)

        # V4 — numbering, only when a project declared a grammar.
        try:
            scheme = self.numbering_scheme()
        except NumberingError as exc:
            problems.append(str(exc))
        else:
            if scheme is not None:
                problems.extend(
                    f"meta: {problem}" for problem in scheme.validate(str(self.number))
                )

        # V5 — the block P9 would actually render fits its frame and its cells.
        #
        # Gated on the register, not merely on `sheet:`. P9 renders the paper-space
        # block only for a drawing that carries a register, and the shipped sheet
        # model asserts that adding a `sheet:` block changes nothing about
        # `validate()`'s verdict. A check keyed on `sheet:` alone would break that
        # guarantee for a drawing P9 does not draw a title block for.
        if register is not None and structural is None:
            problems.extend(self._render_problems(register))

        # V6 — the strengthened for-construction gate.
        problems.extend(self._gate_problems(register if structural is None else None))
        return problems

    def _render_problems(self, register: RevisionRegister) -> list[str]:
        try:
            frame = self._paper_frame()
        except TitleBlockError as exc:
            return [f"meta: {exc}"]
        if frame is None:
            return []
        try:
            title_block_extents_mm(frame, rev_rows=min(
                len(register.entries), ISO7200_180MM.max_rev_rows
            ))
            iso7200_title_block(frame, self.title_block_fields())
            revision_block(frame, register)
        except TitleBlockError as exc:
            return [f"meta: {exc}"]
        return []

    def _gate_problems(self, register: RevisionRegister | None) -> list[str]:
        """V6. Strictly one-directional: it can only ever *add* a requirement.

        ``for_construction: true`` already requires ``status: ISSUED``. Where a
        register exists, it additionally requires a **named approver** and a valid
        seal on the latest revision — because two agent-writable booleans with
        nobody's name on them is a weaker gate than it looks. No code path in this
        toolkit can satisfy either requirement: ``app`` is only ever a value a human
        typed into the YAML, and sealing refuses to run without a terminal.
        """
        if not (self.for_construction and self.status_key == "ISSUED"):
            return []
        if register is None:
            return []
        problems: list[str] = []
        if not register.entries:
            problems.append(
                "meta: for_construction=true requires a revision register with at "
                "least one entry"
            )
            return problems
        latest = register.latest
        if not latest.is_approved:
            problems.append(
                "meta: for_construction=true requires a named approver in "
                "revisions[-1].app (the responsible engineer's signature) — no tool "
                "may supply it"
            )
        expected = register.digests()[-1]
        if not latest.is_sealed:
            problems.append(
                f"meta: for_construction=true requires a valid seal on "
                f"revisions[-1].seal (rev {latest.rev}); run 'technical_drawings_for_agents revision "
                "seal' at a terminal"
            )
        elif latest.seal != expected:
            problems.append(
                f"meta: for_construction=true requires a valid seal on "
                f"revisions[-1].seal (rev {latest.rev}) but the stored seal does not "
                "match the entry's content"
            )
        return problems

    def _paper_frame(self) -> PaperFrame | None:
        """A read-only :class:`PaperFrame` view of P1's resolved sheet, or ``None``."""
        if self.sheet_config is None:
            return None
        from .sheet import SheetError, resolve_sheet

        try:
            sheet = resolve_sheet(self.sheet_config)
        except SheetError as exc:
            raise TitleBlockError(str(exc)) from exc
        return _SheetPaperFrame(sheet)


@dataclass(frozen=True)
class _SheetPaperFrame:
    """Adapts a resolved :class:`technical_drawings_for_agents.sheet.Sheet` to :class:`PaperFrame`.

    Read-only by construction: it reads five values off the sheet and writes
    nothing back. One SVG user unit is one millimetre of paper on a P1 sheet, so
    ``to_device`` is the identity and ``device_per_mm`` is 1.
    """

    sheet: Any

    @property
    def size(self) -> str:
        return self.sheet.paper.name

    @property
    def orientation(self) -> str:
        return self.sheet.paper.orientation

    def frame_mm(self) -> tuple[float, float, float, float]:
        frame = self.sheet.frame
        return (frame.x_mm, frame.y_mm, frame.width_mm, frame.height_mm)

    def to_device(self, x_mm: float, y_mm: float) -> tuple[float, float]:
        return (x_mm, y_mm)

    def device_per_mm(self) -> float:
        return 1.0
