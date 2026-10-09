"""Drawing-numbering schemes (P9 / #61, #43).

**Enforcement is strictly opt-in, and that is a substantive decision, not a
convenience.** The vault standard's *entire* written numbering convention is two
examples plus one sentence — and its two examples disagree on field count
(``ARC-CSL-001`` has two segments, ``STA-WTP-GA-001`` has three), with the
standards register recording the numbering half as still *proposed*. There is no
adopted convention to enforce. Enforcing one globally would impose a scheme the
standard has not decided, and would break a drawing that validates today.

So this module ships a mechanism, not a verdict:

* :data:`SHAPE_ONLY` encodes only what the sources actually state — uppercase
  alphanumeric segments, hyphen-separated, a zero-padded numeric serial last, and
  "the number is the stable ID" (no ``_Rev`` suffix in the stored number). It names
  no segments and enforces no vocabulary, because the sources name none;
* a project may declare its own scheme with named segments and vocabularies, and
  such a scheme **must** carry a ``source:`` naming the document the convention is
  written down in. A scheme with no citable source is a scheme somebody invented.

Nothing here runs unless a drawing declares ``numbering:``. Promoting the
convention from *proposed* to *adopted* in the house standard is out of scope: it
is a house-standard change and the owner's call, informed by a working implementation (open
questions O-1 / O-2).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


class NumberingError(ValueError):
    """Raised when a numbering scheme config or a drawing number is malformed."""


#: The shape the observed identifiers share, and nothing beyond it. ``{2,5}``
#: because every observed segment is 3 chars except ``FOUND`` (5); min 2 rejects
#: the degenerate one-character segment. Digits are allowed inside a segment
#: because supplier equipment prefixes are numeric and nothing forbids them.
SHAPE_ONLY_PATTERN = r"^[A-Z0-9]{2,5}(?:-[A-Z0-9]{2,5}){1,3}-[0-9]{3}$"

_SEGMENT_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SCHEME_KEYS = ("id", "source", "serial_digits", "segments", "min_segments", "max_segments")
_SEGMENT_KEYS = ("name", "pattern", "vocabulary")


@dataclass(frozen=True)
class Segment:
    """One named segment of a drawing number, with an optional controlled vocabulary."""

    name: str
    pattern: str
    vocabulary: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        name = str(self.name)
        if not _SEGMENT_NAME_RE.fullmatch(name):
            raise NumberingError(
                f"numbering.segments[].name {name!r} is not a valid field name"
            )
        pattern = str(self.pattern)
        try:
            re.compile(f"^(?:{pattern})$")
        except re.error as exc:
            raise NumberingError(
                f"numbering.segments.{name}.pattern is invalid: {exc}"
            ) from exc
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "pattern", pattern)
        object.__setattr__(
            self, "vocabulary", tuple(str(value) for value in (self.vocabulary or ()))
        )


@dataclass(frozen=True)
class NumberingScheme:
    """A drawing-number grammar. Empty ``segments`` means shape-only."""

    id: str
    source: str
    segments: tuple[Segment, ...] = ()
    serial_digits: int = 3
    min_segments: int = 2
    max_segments: int = 4

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id.strip():
            raise NumberingError("numbering.id must be a non-empty string")
        if not isinstance(self.source, str) or not self.source.strip():
            raise NumberingError(
                "numbering.source is required — name the document this convention "
                "comes from"
            )
        _positive_int(self.serial_digits, "numbering.serial_digits")
        _positive_int(self.min_segments, "numbering.min_segments")
        _positive_int(self.max_segments, "numbering.max_segments")
        if self.max_segments < self.min_segments:
            raise NumberingError(
                "numbering.max_segments must be >= numbering.min_segments"
            )
        object.__setattr__(self, "id", self.id.strip())
        object.__setattr__(self, "source", self.source.strip())
        object.__setattr__(self, "segments", tuple(self.segments))

    @property
    def pattern(self) -> re.Pattern[str]:
        serial_digits = self.serial_digits
        if self.segments:
            parts = [f"(?P<{segment.name}>{segment.pattern})" for segment in self.segments]
            parts.append(f"(?P<serial>[0-9]{{{serial_digits}}})")
            return re.compile("^" + "-".join(parts) + "$")
        body = (
            rf"[A-Z0-9]{{2,5}}(?:-[A-Z0-9]{{2,5}})"
            rf"{{{self.min_segments - 1},{self.max_segments - 1}}}"
            rf"-[0-9]{{{serial_digits}}}"
        )
        return re.compile("^" + body + "$")

    def match(self, number: str) -> dict[str, str]:
        """Segment name to value plus ``serial``. Raises :class:`NumberingError`."""
        if not isinstance(number, str) or not number:
            raise NumberingError(
                f"number {number!r} does not match numbering scheme {self.id!r} "
                f"(expected {self.pattern.pattern})"
            )
        if "_REV" in number.upper():
            raise NumberingError(
                f"number {number!r} does not match numbering scheme {self.id!r}: it "
                "carries a revision suffix. The stored number is the bare stable ID — "
                "put the revision token in meta.revision"
            )
        match = self.pattern.fullmatch(number)
        if match is None:
            raise NumberingError(
                f"number {number!r} does not match numbering scheme {self.id!r} "
                f"(expected {self.pattern.pattern}); the serial must be "
                f"{self.serial_digits} digits"
            )
        if self.segments:
            values = match.groupdict()
            for segment in self.segments:
                value = values[segment.name]
                if segment.vocabulary and value not in segment.vocabulary:
                    raise NumberingError(
                        f"number {number!r} segment {segment.name}={value!r} is not "
                        f"allowed by numbering scheme {self.id!r} "
                        f"(allowed: {', '.join(segment.vocabulary)})"
                    )
            return values
        parts = number.split("-")
        unnamed = {f"seg{index}": value for index, value in enumerate(parts[:-1])}
        unnamed["serial"] = parts[-1]
        return unnamed

    def validate(self, number: str) -> list[str]:
        """Problem strings; empty means conformant. Never raises."""
        try:
            self.match(number)
        except NumberingError as exc:
            return [str(exc)]
        return []

    @classmethod
    def load(cls, path: str | Path) -> "NumberingScheme":
        config_path = Path(path)
        data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        return cls.from_dict(data, ctx=str(config_path))

    @classmethod
    def from_dict(cls, raw: Any, *, ctx: str) -> "NumberingScheme":
        if not isinstance(raw, dict):
            raise NumberingError(f"{ctx}: numbering must be a mapping")
        if "numbering" in raw and "id" not in raw:
            block = raw["numbering"]
            if not isinstance(block, dict):
                raise NumberingError(f"{ctx}: numbering must be a mapping")
            raw = block
        unknown = sorted(set(raw) - set(_SCHEME_KEYS))
        if unknown:
            raise NumberingError(
                f"{ctx}: numbering has unknown key(s): {', '.join(unknown)} "
                f"(allowed: {', '.join(_SCHEME_KEYS)})"
            )
        if not raw.get("source"):
            raise NumberingError(
                f"{ctx}: numbering.source is required — name the document this "
                "convention comes from"
            )
        raw_segments = raw.get("segments") or ()
        if not isinstance(raw_segments, (list, tuple)):
            raise NumberingError(f"{ctx}: numbering.segments must be a list")
        segments = tuple(
            _segment(item, index, ctx) for index, item in enumerate(raw_segments)
        )
        return cls(
            id=str(raw.get("id", "project")),
            source=str(raw["source"]),
            segments=segments,
            serial_digits=raw.get("serial_digits", 3),
            min_segments=raw.get("min_segments", 2),
            max_segments=raw.get("max_segments", 4),
        )


def _positive_int(value: Any, ctx: str) -> None:
    # ``isinstance(True, int)`` is True in Python, so guard the bool first.
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise NumberingError(f"{ctx} must be a positive integer, got {value!r}")


def _segment(raw: Any, index: int, ctx: str) -> Segment:
    item_ctx = f"{ctx}: numbering.segments[{index}]"
    if not isinstance(raw, dict):
        raise NumberingError(f"{item_ctx} must be a mapping")
    unknown = sorted(set(raw) - set(_SEGMENT_KEYS))
    if unknown:
        raise NumberingError(
            f"{item_ctx} has unknown key(s): {', '.join(unknown)} "
            f"(allowed: {', '.join(_SEGMENT_KEYS)})"
        )
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        raise NumberingError(f"{item_ctx}.name must be a non-empty string")
    pattern = raw.get("pattern")
    if not isinstance(pattern, str) or not pattern.strip():
        raise NumberingError(f"{item_ctx}.pattern must be a non-empty string")
    vocabulary = raw.get("vocabulary") or ()
    if not isinstance(vocabulary, (list, tuple)):
        raise NumberingError(f"{item_ctx}.vocabulary must be a list")
    return Segment(name=name.strip(), pattern=pattern, vocabulary=tuple(vocabulary))


#: The conservative built-in: shape only, no segment semantics, no vocabulary.
SHAPE_ONLY = NumberingScheme(
    id="shape-only",
    source=(
        "Engineering Drawings as Code (vault standard, PROPOSED not adopted): "
        "'a formal drawing number + rev, extending the existing ARC-CSL-001 style "
        "(e.g. STA-WTP-GA-001_RevB). The number is the stable ID; never reuse it for "
        "a different drawing.'"
    ),
    serial_digits=3,
    min_segments=2,
    max_segments=4,
)

BUILTIN_SCHEMES: dict[str, NumberingScheme] = {"shape-only": SHAPE_ONLY}
