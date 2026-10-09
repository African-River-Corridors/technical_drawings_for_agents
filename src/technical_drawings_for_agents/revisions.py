"""Revision registers for drawing metadata (P9 / #61).

A drawing revision is not a letter in a title block. It is a dated, authored
change record that has to survive being copied out of git, mailed as a PDF, and
read on site. This module keeps that record small, frozen and explicit: parse
YAML into immutable values, reject unknown keys loudly, apply the ordering rules
R1-R6, and *seal* issued rows so an issued revision cannot be edited, reordered,
deleted or interleaved without validation saying so.

Two rules in here are safety rules rather than data-modelling preferences, and
neither may be relaxed:

* ``app`` — the approver, the responsible engineer's signature — is only ever a
  value read verbatim from the YAML a human authored. Nothing in this module
  writes it, defaults it, or derives it from ``by``, ``chk``, the environment or
  git. :meth:`RevisionRegister.append` takes a constructed :class:`Revision` and
  never fabricates one.
* the seal covers the entry's own fields **plus its index plus the digest of
  every preceding entry**, so a reorder, a deletion and an insertion are all
  detected, not just an in-place edit.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any


class RevisionError(ValueError):
    """Raised when a revision register is malformed or violates an ordering rule."""


#: The default revision-token scheme. ``alpha`` because that is what every
#: observed drawing uses; it is permissive (all 26 letters) because the vault
#: standard is silent on whether ``I``/``O``/``Q`` are skipped (open question O-2)
#: and a permissive default cannot break a drawing that validates today.
DEFAULT_REV_SCHEME = "alpha"

#: The only description accepted for a revision whose change note is genuinely
#: unrecoverable, and only alongside ``pre_register: true``. It states an absence
#: instead of fabricating a change note.
PRE_REGISTER_SENTINEL = "NOT RECORDED (pre-register revision)"

#: Longest ``by``/``chk``/``app`` a *register* accepts. The rendered cell is
#: narrower (9 chars at 2.5 mm); the stricter of the two applies at render time.
#: The register is looser so a history can be authored before a sheet size is.
MAX_INITIALS = 12

_INITIAL_DIGEST = ""
_SEAL_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_REVISION_KEYS = ("rev", "date", "description", "by", "chk", "app", "seal", "pre_register")

#: Sentinel for "no date was authored". ``date.min`` rather than ``None`` keeps
#: :class:`Revision` annotated honestly as a ``date`` while still letting
#: :meth:`RevisionRegister.validate` report the omission as a problem rather than
#: raising from a constructor a caller cannot see into.
NO_DATE = _dt.date.min


@dataclass(frozen=True)
class RevisionTokenScheme:
    """The syntax and the ordering for revision tokens under one named scheme."""

    id: str
    pattern: re.Pattern[str]
    kind: str

    def normalise(self, value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip().upper()

    def accepts(self, token: str) -> bool:
        return self.pattern.fullmatch(token) is not None

    def key(self, token: str) -> tuple[int, int]:
        """Sort key for ``token``. Alpha tokens sort before numeric ones."""
        if not self.accepts(token):
            raise RevisionError(
                f"revision token {token!r} does not match scheme {self.id!r}"
            )
        if self.kind == "alpha":
            return (0, _alpha_index(token))
        if self.kind == "numeric":
            return (0, int(token))
        return (0, _alpha_index(token)) if token.isalpha() else (1, int(token))


REV_SCHEMES: dict[str, RevisionTokenScheme] = {
    "alpha": RevisionTokenScheme("alpha", re.compile(r"^[A-Z]+$"), "alpha"),
    "numeric": RevisionTokenScheme("numeric", re.compile(r"^[0-9]+$"), "numeric"),
    "alpha-then-numeric": RevisionTokenScheme(
        "alpha-then-numeric", re.compile(r"^(?:[A-Z]+|[0-9]+)$"), "alpha-then-numeric"
    ),
}


@dataclass(frozen=True)
class Revision:
    """One row of a drawing's revision history. Frozen: there is no edit path."""

    rev: str
    date: _dt.date
    description: str
    by: str
    chk: str | None = None
    app: str | None = None
    seal: str | None = None
    pre_register: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "rev", _required_text(self.rev).upper())
        object.__setattr__(self, "date", _date_value(self.date, "revision.date"))
        object.__setattr__(self, "description", _required_text(self.description))
        object.__setattr__(self, "by", _required_text(self.by))
        object.__setattr__(self, "chk", _optional_text(self.chk))
        object.__setattr__(self, "app", _optional_text(self.app))
        object.__setattr__(self, "seal", _optional_text(self.seal))
        if not isinstance(self.pre_register, bool):
            raise RevisionError("revision.pre_register must be true or false")

    @property
    def is_sealed(self) -> bool:
        return self.seal is not None

    @property
    def is_approved(self) -> bool:
        """True only when a human wrote a name into ``app``."""
        return self.app is not None and bool(self.app.strip())

    def canonical(self, *, index: int, prior_digest: str) -> str:
        """Deterministic sorted-key JSON of the frozen fields, excluding ``seal``.

        ``index`` and ``prior_digest`` are inside the payload deliberately: that
        is what makes the seal detect a reorder, a deletion or an insertion and
        not merely an in-place edit of this row.
        """
        payload = {
            "app": self.app,
            "by": self.by,
            "chk": self.chk,
            "date": self.date.isoformat(),
            "description": self.description,
            "index": index,
            "pre_register": self.pre_register,
            "prior_digest": prior_digest,
            "rev": self.rev,
        }
        return json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )

    def digest(self, *, index: int, prior_digest: str) -> str:
        """``sha256:<64 hex>`` over :meth:`canonical`."""
        body = self.canonical(index=index, prior_digest=prior_digest).encode("utf-8")
        return "sha256:" + hashlib.sha256(body).hexdigest()


@dataclass(frozen=True)
class RevisionRegister:
    """An append-only, ordered history. No ``update``, ``remove`` or ``__setitem__``."""

    entries: tuple[Revision, ...]
    scheme: str = DEFAULT_REV_SCHEME
    source: str = "meta"

    def __post_init__(self) -> None:
        if self.scheme not in REV_SCHEMES:
            raise RevisionError(
                f"{self.source}: revision_scheme {self.scheme!r} must be one of "
                f"{sorted(REV_SCHEMES)}"
            )
        object.__setattr__(self, "entries", tuple(self.entries))

    @property
    def token_scheme(self) -> RevisionTokenScheme:
        return REV_SCHEMES[self.scheme]

    @property
    def latest(self) -> Revision:
        if not self.entries:
            raise RevisionError(f"{self.source}: {_EMPTY_REGISTER}")
        return self.entries[-1]

    def get(self, rev: str) -> Revision | None:
        token = self.token_scheme.normalise(rev)
        return next((entry for entry in self.entries if entry.rev == token), None)

    def digests(self) -> tuple[str, ...]:
        """The expected seal for every entry, in order (the chained digest)."""
        prior = _INITIAL_DIGEST
        out: list[str] = []
        for index, entry in enumerate(self.entries):
            prior = entry.digest(index=index, prior_digest=prior)
            out.append(prior)
        return tuple(out)

    def validate(
        self, *, revision: str | None = None, today: _dt.date | None = None
    ) -> tuple[list[str], list[str]]:
        """Return ``(problems, warnings)``: R1-R6 plus every field rule. Never raises."""
        from .titleblock import revision_column_capacity

        today = today or _dt.date.today()
        problems: list[str] = []
        warnings: list[str] = []
        if not self.entries:
            problems.append(f"{self.source}: {_EMPTY_REGISTER}")
            return problems, warnings

        scheme = self.token_scheme
        description_cap = revision_column_capacity("description")
        seen: dict[str, int] = {}
        prior_key: tuple[int, int] | None = None
        prior_date: _dt.date | None = None
        expected_seals = self.digests()
        known = [entry.rev for entry in self.entries]

        for index, entry in enumerate(self.entries):
            ctx = f"{self.source}: revisions[{index}]"

            # R2/R3 — unique and monotonically increasing tokens.
            if not entry.rev:
                problems.append(f"{ctx}.rev is required")
            elif not scheme.accepts(entry.rev):
                problems.append(
                    f"{ctx}.rev {entry.rev!r} does not match revision_scheme "
                    f"{self.scheme!r}"
                )
            else:
                key = scheme.key(entry.rev)
                if prior_key is not None and key <= prior_key:
                    problems.append(
                        f"{ctx}.rev {entry.rev!r} must increase after "
                        f"revisions[{index - 1}].rev {self.entries[index - 1].rev!r}"
                    )
                prior_key = key
            if entry.rev and entry.rev in seen:
                problems.append(
                    f"{ctx}.rev {entry.rev!r} duplicates revisions[{seen[entry.rev]}].rev"
                )
            elif entry.rev:
                seen[entry.rev] = index

            # R1 — dates are non-decreasing; a future date is a warning, not a block.
            if entry.date == NO_DATE:
                problems.append(f"{ctx}.date is required")
            else:
                if prior_date is not None and entry.date < prior_date:
                    problems.append(
                        f"{ctx}.date {entry.date.isoformat()} is earlier than "
                        f"revisions[{index - 1}].date {prior_date.isoformat()}"
                    )
                if entry.date > today:
                    warnings.append(
                        f"{ctx}.date {entry.date.isoformat()} is in the future "
                        f"relative to {today.isoformat()}"
                    )
                prior_date = entry.date

            # description — required, capped, and the sentinel is gated.
            if not entry.description:
                problems.append(
                    f"{ctx}.description is required "
                    "(a revision with no description is not a revision)"
                )
            elif entry.description == PRE_REGISTER_SENTINEL and not entry.pre_register:
                problems.append(
                    f"{ctx}.description uses the pre-register sentinel "
                    f"{PRE_REGISTER_SENTINEL!r} but pre_register is not true"
                )
            elif len(entry.description) > description_cap:
                problems.append(
                    f"{ctx}.description is {len(entry.description)} chars but the "
                    f"DESCRIPTION cell holds {description_cap}"
                )
            if entry.pre_register:
                warnings.append(
                    f"{ctx} (rev {entry.rev}) carries pre_register: true — its change "
                    "note is not recorded"
                )

            # by / chk / app — by is required; none of them is ever tool-supplied.
            if not entry.by:
                problems.append(f"{ctx}.by is required (the revision author)")
            elif len(entry.by) > MAX_INITIALS:
                problems.append(
                    f"{ctx}.by is {len(entry.by)} chars; maximum is {MAX_INITIALS}"
                )
            for name in ("chk", "app"):
                value: str | None = getattr(entry, name)
                if value is not None and len(value) > MAX_INITIALS:
                    problems.append(
                        f"{ctx}.{name} is {len(value)} chars; maximum is {MAX_INITIALS}"
                    )
            # NB: the *rendered* BY/CHK/APP cells hold fewer characters than
            # MAX_INITIALS (9 at 2.5 mm in a 16 mm column). That narrower limit is
            # deliberately NOT applied here: a register must be authorable before a
            # sheet size is declared, so 10-12 characters parse and validate, and
            # only fail when a sheet actually renders them. `revision_block` raises,
            # and `DrawingMeta` surfaces it as a problem for a drawing with a sheet.

            # R5/V7 — the chained seal.
            if entry.seal is not None:
                if _SEAL_RE.fullmatch(entry.seal) is None:
                    problems.append(f"{ctx}.seal must be sha256:<64 lowercase hex>")
                elif entry.seal != expected_seals[index]:
                    problems.append(
                        f"{ctx} (rev {entry.rev}) is sealed but its content has "
                        "changed — an ISSUED revision must not be edited in place; "
                        "add a new revision instead"
                    )

        # R4 — meta.revision agrees with the register.
        if revision is not None:
            current = scheme.normalise(revision)
            if current not in seen:
                problems.append(
                    f"{self.source}: revision {current!r} is absent from revisions: "
                    f"(known: {', '.join(known)})"
                )
            elif current != self.entries[-1].rev:
                problems.append(
                    f"{self.source}: revision {current!r} is not the latest entry in "
                    f"revisions: (latest is {self.entries[-1].rev!r}); if {current} is "
                    "new, add it to revisions:"
                )

        return problems, warnings

    @classmethod
    def from_list(
        cls, raw: Any, *, ctx: str, scheme: str = DEFAULT_REV_SCHEME
    ) -> "RevisionRegister":
        """Parse a YAML ``revisions:`` list.

        Raises :class:`RevisionError` on a *structural* problem — not a list, an
        entry that is not a mapping, an unknown key, an unparseable date. Semantic
        problems come back from :meth:`validate` as strings so one run reports all
        of them. Unknown keys are rejected rather than swept aside: a typo'd
        ``descripton:`` would otherwise leave an issued revision with no recorded
        change note, silently.
        """
        if scheme not in REV_SCHEMES:
            raise RevisionError(
                f"{ctx}: revision_scheme {scheme!r} must be one of {sorted(REV_SCHEMES)}"
            )
        if not isinstance(raw, list):
            raise RevisionError(f"{ctx}: revisions: must be a list of revision entries")
        entries: list[Revision] = []
        for index, item in enumerate(raw):
            item_ctx = f"{ctx}: revisions[{index}]"
            if not isinstance(item, dict):
                raise RevisionError(f"{item_ctx} must be a mapping")
            unknown = sorted(set(item) - set(_REVISION_KEYS))
            if unknown:
                raise RevisionError(
                    f"{item_ctx} has unknown key(s): {', '.join(unknown)} "
                    f"(allowed: {', '.join(_REVISION_KEYS)})"
                )
            pre_register = item.get("pre_register", False)
            if not isinstance(pre_register, bool):
                raise RevisionError(f"{item_ctx}.pre_register must be true or false")
            entries.append(
                Revision(
                    rev=REV_SCHEMES[scheme].normalise(item.get("rev", "")),
                    date=_date_value(item.get("date"), f"{item_ctx}.date"),
                    description=item.get("description", ""),
                    by=item.get("by", ""),
                    chk=item.get("chk"),
                    app=item.get("app"),
                    seal=item.get("seal"),
                    pre_register=pre_register,
                )
            )
        return cls(tuple(entries), scheme=scheme, source=ctx)

    def append(self, entry: Revision) -> "RevisionRegister":
        """Return a **new** register with ``entry`` appended. Never mutates.

        Refuses when the appended token would duplicate or precede an existing
        one, and when the sealed prefix would change. Never sets ``app``: it takes
        a constructed :class:`Revision` and does not build one.
        """
        if not isinstance(entry, Revision):
            raise RevisionError(
                f"{self.source}: revisions.append takes a Revision, not "
                f"{type(entry).__name__}"
            )
        scheme = self.token_scheme
        if not scheme.accepts(entry.rev):
            raise RevisionError(
                f"{self.source}: revision {entry.rev!r} does not match "
                f"revision_scheme {self.scheme!r}"
            )
        existing = self.get(entry.rev)
        if existing is not None:
            raise RevisionError(
                f"{self.source}: revision {entry.rev!r} is already in revisions:"
            )
        if self.entries:
            last = self.entries[-1]
            if scheme.accepts(last.rev) and scheme.key(entry.rev) <= scheme.key(last.rev):
                raise RevisionError(
                    f"{self.source}: revision {entry.rev!r} must increase after the "
                    f"latest entry {last.rev!r}"
                )
            if entry.date != NO_DATE and last.date != NO_DATE and entry.date < last.date:
                raise RevisionError(
                    f"{self.source}: revision {entry.rev!r} is dated "
                    f"{entry.date.isoformat()}, earlier than {last.rev!r} "
                    f"({last.date.isoformat()})"
                )
        candidate = RevisionRegister(
            self.entries + (entry,), scheme=self.scheme, source=self.source
        )
        prefix = candidate.digests()[: len(self.entries)]
        for index, existing_entry in enumerate(self.entries):
            if existing_entry.is_sealed and existing_entry.seal != prefix[index]:
                raise RevisionError(
                    f"{self.source}: revisions[{index}] (rev {existing_entry.rev}) is "
                    "sealed and appending would invalidate its seal"
                )
        return candidate

    def to_list(self) -> list[dict[str, Any]]:
        """Round-trippable YAML-ready rows, keys in the schema's declared order."""
        rows: list[dict[str, Any]] = []
        for entry in self.entries:
            row: dict[str, Any] = {
                "rev": entry.rev,
                "date": entry.date,
                "description": entry.description,
                "by": entry.by,
                "chk": entry.chk,
                "app": entry.app,
            }
            if entry.seal is not None:
                row["seal"] = entry.seal
            if entry.pre_register:
                row["pre_register"] = True
            rows.append(row)
        return rows


_EMPTY_REGISTER = (
    "revisions: must contain at least one entry (or omit the key entirely)"
)


def _alpha_index(token: str) -> int:
    """Excel-column order: ``A``=1 … ``Z``=26, ``AA``=27."""
    value = 0
    for char in token:
        value = value * 26 + (ord(char) - ord("A") + 1)
    return value


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _required_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _date_value(value: Any, ctx: str) -> _dt.date:
    """Accept a YAML native date, a ``datetime``, or an ISO-8601 ``YYYY-MM-DD``."""
    if isinstance(value, _dt.datetime):
        return value.date()
    if isinstance(value, _dt.date):
        return value
    if value is None:
        return NO_DATE
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return NO_DATE
        try:
            return _dt.date.fromisoformat(stripped)
        except ValueError as exc:
            raise RevisionError(f"{ctx} must be YYYY-MM-DD, got {value!r}") from exc
    raise RevisionError(
        f"{ctx} must be a YAML date or a YYYY-MM-DD string, got {value!r}"
    )
