"""Deterministic emit policy, sidecar manifests, and the provenance stamp.

A build should be an idempotent function of its *declared inputs*: the same input
bytes and the same tool version produce the same output bytes, and every output
carries a machine-readable record of exactly which inputs (by SHA256) produced
it, plus a short hash printed on the sheet so "is this drawing stale?" is
answered by reading eight characters off the paper.

Two digests, deliberately distinct (the obvious single digest is circular — the
stamp is printed *into* the sheet, so the sheet's bytes depend on the stamp):

* ``build.digest`` — over declared input **content**, params, policy and tool.
  Computable before a byte is emitted. The stamp is drawn from it.
* ``output.sha256`` — the bytes actually written, recorded after the write.

The digest covers **content, never location**. Input paths are recorded in the
manifest for human traceability and excluded from the preimage, so a build in CI
and the same build on a laptop agree, and moving a project directory does not
make every artifact look stale.

Canonical emit is reached only by constructing an :class:`EmitPolicy` and passing
it. There is no global switch and no byte-affecting environment variable other
than ``SOURCE_DATE_EPOCH``, which is read in exactly one place
(:meth:`EmitPolicy.from_environment`). A caller that never opts in gets today's
bytes, byte for byte.

**A provenance stamp is not an approval.** It records what produced these bytes
and says nothing about whether anyone signed them off; nothing here reads or
writes the ISSUED gate.
"""

from __future__ import annotations

import getpass
import hashlib
import json
import math
import os
import platform
import re
import socket
import subprocess
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Sequence

SCHEMA = "technical_drawings_for_agents/manifest@1"
TARGET_FORMATS = {"svg", "dxf", "pdf", "png", "geojson", "yaml", "json", "dot"}
REASONS = {
    "libreoffice-pdf-embeds-creationdate-and-docid",
    "no-source-date-epoch",
    "external-renderer",
    "unmeasured",
}
STAMP_PLACEMENTS = {
    "below-title-block",
    "fallback-bottom-left",
    "dxf-titleblock-layer",
}
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
NEGATIVE_ZERO = re.compile(r"^-0(\.0*)?$")
PRECISION_FIELDS = (
    "precision_m",
    "precision_mm",
    "precision_px",
    "precision_deg",
    "precision_ratio",
    "precision_font",
)
POLICY_FLAG_FIELDS = ("normalise_negative_zero", "fixed_dxf_metadata", "trailing_newline")
FINDING_CODES = {
    "INPUT-MISSING",
    "INPUT-CHANGED",
    "OUTPUT-MISSING",
    "OUTPUT-CHANGED",
    "DIGEST-MISMATCH",
    "STAMP-MISMATCH",
    "SCHEMA-UNSUPPORTED",
    "INPUT-UNTRACKED",
    "TOOL-COMMIT-CHANGED",
    "TOOL-DIRTY",
    "NOT-REPRODUCIBLE",
    "REPRODUCIBILITY-UNMEASURED",
}


class ProvenanceError(ValueError):
    """Raised when an emit policy, a manifest, or a provenance record is malformed."""


# --------------------------------------------------------------------------- #
# canonical formatting
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class EmitPolicy:
    """How to canonicalise bytes on write. Constructing one is the opt-in."""

    precision_m: int = 3          # metres      -> mm
    precision_mm: int = 2         # paper mm    -> 10 um
    precision_px: int = 1         # SVG user units (matches today's ":.1f")
    precision_deg: int = 3        # bearings / rotations
    precision_ratio: int = 3      # opacities, stroke widths, dimensionless
    precision_font: int = 2       # font-size
    source_date_epoch: int | None = None   # resolved, never read from env here
    normalise_negative_zero: bool = True
    fixed_dxf_metadata: bool = True
    trailing_newline: bool = True

    def __post_init__(self) -> None:
        for field_name in PRECISION_FIELDS:
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 9:
                raise ProvenanceError(
                    f"EmitPolicy.{field_name} must be an int in 0..9, got {value!r}"
                )
        epoch = self.source_date_epoch
        if epoch is not None and (
            isinstance(epoch, bool) or not isinstance(epoch, int) or epoch < 0
        ):
            raise ProvenanceError(
                "EmitPolicy.source_date_epoch must be a non-negative int "
                f"(seconds since the Unix epoch, UTC), got {epoch!r}"
            )
        for field_name in POLICY_FLAG_FIELDS:
            value = getattr(self, field_name)
            if not isinstance(value, bool):
                raise ProvenanceError(f"EmitPolicy.{field_name} must be a bool, got {value!r}")

    @classmethod
    def from_environment(cls, **overrides: Any) -> "EmitPolicy":
        """Build a policy, resolving ``SOURCE_DATE_EPOCH`` only for opted-in callers."""

        kwargs = dict(overrides)
        raw = os.environ.get("SOURCE_DATE_EPOCH")
        if "source_date_epoch" not in kwargs and raw is not None:
            stripped = raw.strip()
            if not re.fullmatch(r"[0-9]+", stripped):
                raise ProvenanceError(
                    "SOURCE_DATE_EPOCH must be a non-negative integer number of seconds since "
                    f"the Unix epoch, got {raw!r}"
                )
            kwargs["source_date_epoch"] = int(stripped)
        return cls(**kwargs)

    def as_manifest_dict(self) -> dict[str, Any]:
        """Return the manifest ``policy`` block."""

        return {
            "precision_deg": self.precision_deg,
            "precision_font": self.precision_font,
            "precision_m": self.precision_m,
            "precision_mm": self.precision_mm,
            "precision_px": self.precision_px,
            "precision_ratio": self.precision_ratio,
            "normalise_negative_zero": self.normalise_negative_zero,
            "fixed_dxf_metadata": self.fixed_dxf_metadata,
            "trailing_newline": self.trailing_newline,
            "source_date_epoch": self.source_date_epoch,
        }

    def fmt(self, value: float, decimals: int) -> str:
        """Canonical fixed-point text using this policy's negative-zero rule."""

        return fmt(value, decimals, normalise_negative_zero=self.normalise_negative_zero)

    def q(self, value: float, decimals: int) -> float:
        """Canonical rounded float using this policy's negative-zero rule."""

        return q(value, decimals, normalise_negative_zero=self.normalise_negative_zero)


def fmt(value: float, decimals: int, *, normalise_negative_zero: bool = True) -> str:
    """Canonical fixed-point text for a float — the only float->text emit path.

    Uses CPython's ``format``, i.e. round-half-**even** on the exact binary value.
    Never ``Decimal(...).quantize(..., ROUND_HALF_UP)``: the two genuinely differ
    at ties (``0.0625`` -> ``0.062`` here versus ``0.063`` there), and ties at mm
    precision are odd multiples of 1/16 m, which occur in real work.
    """

    _check_decimals(decimals)
    number = float(value)
    if not math.isfinite(number):
        raise ProvenanceError(f"cannot canonically emit non-finite value {number}")
    text = format(number, f".{decimals}f")
    if normalise_negative_zero and NEGATIVE_ZERO.match(text):
        text = text[1:]
    return text


def q(value: float, decimals: int, *, normalise_negative_zero: bool = True) -> float:
    """Canonical rounded float for numeric JSON/YAML/ezdxf output (half-even)."""

    _check_decimals(decimals)
    number = float(value)
    if not math.isfinite(number):
        raise ProvenanceError(f"cannot canonically emit non-finite value {number}")
    out = round(number, decimals)
    if normalise_negative_zero and out == 0.0:
        return 0.0
    return out


def write_text_canonical(path: Path, text: str, policy: EmitPolicy) -> Path:
    """Write UTF-8, LF-only, no-BOM text atomically, honouring the trailing-newline rule.

    Atomic because a build interrupted mid-write must not leave a truncated sheet
    beside a manifest that claims a digest for it.
    """

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    normalised = text.replace("\r\n", "\n").replace("\r", "\n")
    if policy.trailing_newline:
        normalised = normalised.rstrip("\n") + "\n"
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(normalised)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()
    return path


def write_json_canonical(path: Path, obj: Any, policy: EmitPolicy) -> Path:
    """Write canonical JSON — sorted keys, real UTF-8, no NaN — for manifests and GeoJSON."""

    try:
        text = json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ProvenanceError(f"cannot canonically serialise JSON: {exc}") from exc
    return write_text_canonical(path, text, policy)


def _check_decimals(decimals: int) -> None:
    if isinstance(decimals, bool) or not isinstance(decimals, int) or decimals < 0:
        raise ProvenanceError(f"decimals must be a non-negative int, got {decimals!r}")


# --------------------------------------------------------------------------- #
# what each format can promise, and the digest of an emitted artifact
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class FormatReproducibility:
    """What the tool *declares* about one output format, measured — never guessed.

    ``digest`` is how :func:`emit_digest` derives an artifact's identity:

    * ``"bytes"``  — sha256 of the file bytes;
    * ``"masked"`` — sha256 of canonically-masked text, for a container that
      embeds unavoidable non-determinism (DXF GUIDs and ``$TD*`` timestamps), so
      the digest reflects the drawing rather than the container's metadata;
    * ``"none"``   — no meaningful digest exists; :func:`emit_digest` raises.
    """

    digest: str
    reproducible: bool | None
    reason: str | None


# Measured on ezdxf 1.4.4 / matplotlib 3.11.1 / LibreOffice 26. PDF is declared
# non-reproducible rather than normalised: a hand-rolled PDF mutator on the path
# producing the artifact an engineer signs against is the worse risk. A sidecar
# manifest that declares a *particular* PDF reproducible (matplotlib with a
# resolved SOURCE_DATE_EPOCH) overrides this default — see emit_digest.
FORMAT_REPRODUCIBILITY: dict[str, FormatReproducibility] = {
    "svg": FormatReproducibility("bytes", True, None),
    "geojson": FormatReproducibility("bytes", True, None),
    "json": FormatReproducibility("bytes", True, None),
    "yaml": FormatReproducibility("bytes", True, None),
    "dot": FormatReproducibility("bytes", True, None),
    "png": FormatReproducibility("bytes", True, None),
    "dxf": FormatReproducibility("masked", True, None),
    "pdf": FormatReproducibility(
        "none", False, "libreoffice-pdf-embeds-creationdate-and-docid"
    ),
}

# The six items that differ between two saves of a geometrically identical ezdxf
# document (measured: 6 differing lines of 13476, 0 with
# options.write_fixed_meta_data_for_testing). Masking them is what makes a DXF
# comparable when the writing caller did not opt into fixed metadata.
_DXF_VOLATILES = (
    (re.compile(r"(\$TDCREATE\n\s*40\n)[^\n]*"), r"\1<TDCREATE>"),
    (re.compile(r"(\$TDUPDATE\n\s*40\n)[^\n]*"), r"\1<TDUPDATE>"),
    (re.compile(r"(\$TDINDWG\n\s*40\n)[^\n]*"), r"\1<TDINDWG>"),
    (re.compile(r"(\$TDUSRTIMER\n\s*40\n)[^\n]*"), r"\1<TDUSRTIMER>"),
    (re.compile(r"(\$FINGERPRINTGUID\n\s*2\n)[^\n]*"), r"\1<FINGERPRINTGUID>"),
    (re.compile(r"(\$VERSIONGUID\n\s*2\n)[^\n]*"), r"\1<VERSIONGUID>"),
    (re.compile(r"\d+\.\d+(\.\d+)? @ \d{4}-\d\d-\d\dT[0-9:.+\-]+"), "<EZDXF-MARKER>"),
)

# The CLASSES section is filled by iterating entitydb.dxf_types_in_use(), which is
# a **set** — so its record order varies with PYTHONHASHSEED and with the set's
# insertion history, and two machines write the same drawing with different bytes.
# Ordering carries no drawing information (readers load the registry into a map),
# so masking normalises it. DxfBuilder removes the variance at source under a
# policy; this handles a DXF written by a caller who did not opt in.
# Matched on ezdxf's exact writer output (ClassesSection.export_dxf writes
# "  0\nSECTION\n  2\nCLASSES\n" ... "  0\nENDSEC\n"). Deliberately exact rather
# than tolerant: a DXF from another writer simply does not match and is left
# untouched, which is far better than a loose pattern reassembling it wrongly.
_DXF_CLASSES_SECTION = re.compile(
    r"(  0\nSECTION\n  2\nCLASSES\n)(.*?)(  0\nENDSEC\n)", re.DOTALL
)
_DXF_CLASS_RECORD = "  0\nCLASS\n"


def format_reproducibility(target_format: str) -> FormatReproducibility:
    """Return what the tool declares about ``target_format``. Never guesses."""

    key = str(target_format).lower().lstrip(".")
    try:
        return FORMAT_REPRODUCIBILITY[key]
    except KeyError:
        raise ProvenanceError(
            f"no reproducibility declaration for format {target_format!r}; "
            f"this build declares {sorted(FORMAT_REPRODUCIBILITY)}"
        ) from None


def mask_dxf_volatiles(text: str) -> str:
    """Canonicalise everything about a DXF that varies between two identical saves.

    Two independent sources of variance, both container metadata rather than
    drawing content:

    * the six per-save items ezdxf re-rolls — two GUIDs, ``$TDCREATE`` /
      ``$TDUPDATE``, and its two marker strings — replaced with placeholders;
    * the CLASSES section's record order, which comes from iterating a ``set``
      and so varies with ``PYTHONHASHSEED`` and process history — sorted.

    This is what lets a DXF written *without* ``fixed_dxf_metadata`` be compared
    for drawing equality across machines, and what lets a DXF be a golden file at
    all. Newlines are normalised first, so a CRLF-translated copy masks identically.
    """

    normalised = text.replace("\r\n", "\n").replace("\r", "\n")
    for pattern, replacement in _DXF_VOLATILES:
        normalised = pattern.sub(replacement, normalised)
    return _DXF_CLASSES_SECTION.sub(_sort_dxf_class_records, normalised)


def _sort_dxf_class_records(match: re.Match[str]) -> str:
    head, body, tail = match.group(1), match.group(2), match.group(3)
    # Each record is normalised to exactly one trailing newline before sorting, so
    # reassembly cannot depend on which record happened to land last.
    records = [record.strip("\n") for record in body.split(_DXF_CLASS_RECORD)]
    records = [record for record in records if record]
    if not records:
        return match.group(0)
    body = "".join(f"{_DXF_CLASS_RECORD}{record}\n" for record in sorted(records))
    return head + body + tail


def emit_digest(path: str | Path) -> str:
    """Canonical digest of an emitted artifact.

    Reproducible format -> sha256 of the file bytes. Container-noise format (DXF
    GUIDs and timestamps) -> sha256 of canonically-masked content, so the digest
    reflects the drawing rather than the container metadata. Format declared
    non-reproducible -> raises :class:`ProvenanceError`; a caller must never
    receive a value it could mistakenly assert byte-identity on.

    A sidecar ``<path>.manifest.json`` is the authority when present, because
    only the build knows which backend wrote the bytes and whether a build
    timestamp was resolved: a manifest declaring ``reproducible: true`` promotes
    a format whose *default* declaration is non-reproducible, and a manifest
    declaring ``false``/``null`` raises even for a format that is normally fine.
    """

    target = Path(path)
    declared = format_reproducibility(target.suffix)
    mode = declared.digest
    reason = declared.reason

    sidecar = Manifest.path_for(target)
    if sidecar.is_file():
        manifest = Manifest.load(sidecar)
        if manifest.reproducible is not True:
            state = "non-reproducible" if manifest.reproducible is False else "unmeasured"
            raise ProvenanceError(
                f"{target.name}: its manifest declares this output {state} "
                f"({manifest.reason}); there is no digest a caller may assert "
                "byte-identity on"
            )
        # The build vouched for these bytes; keep masking where the container
        # carries noise regardless of who wrote it.
        mode = "bytes" if mode == "none" else mode
    elif declared.reproducible is not True:
        state = "non-reproducible" if declared.reproducible is False else "unmeasured"
        raise ProvenanceError(
            f"{target.name}: '{target.suffix.lstrip('.').lower()}' is declared {state} "
            f"({reason}); emit_digest refuses to return a value a caller could assert "
            f"byte-identity on. Write {sidecar.name} from a build that declares these "
            "particular bytes reproducible, or compare the artifact another way"
        )

    if mode == "masked":
        try:
            text = target.read_text(encoding="utf-8", errors="surrogateescape")
        except OSError as exc:
            raise ProvenanceError(f"cannot read {target}: {exc}") from exc
        masked = mask_dxf_volatiles(text).encode("utf-8", errors="surrogateescape")
        return hashlib.sha256(masked).hexdigest()
    return sha256_file(target)[0]


# --------------------------------------------------------------------------- #
# records
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class InputRecord:
    path: str            # POSIX, relative to root — recorded, NOT hashed
    sha256: str
    bytes: int
    tracked: bool | None


@dataclass(frozen=True)
class ToolRecord:
    name: str
    version: str
    commit: str | None
    dirty: bool | None


@dataclass(frozen=True)
class VcsRecord:
    kind: str | None     # "git" | None
    commit: str | None
    dirty: bool | None


@dataclass(frozen=True)
class Manifest:
    schema: str
    target_name: str
    target_format: str
    drawing_number: str | None
    revision: str | None
    inputs: tuple[InputRecord, ...]          # sorted by path
    params: dict[str, Any]
    policy: EmitPolicy
    tool: ToolRecord
    root: Path
    root_vcs: VcsRecord
    node: str | None
    digest: str
    output_sha256: str | None
    output_bytes: int | None
    reproducible: bool | None
    reason: str | None
    stamp_text: str | None
    stamp_placement: str | None
    environment: dict[str, Any]
    target_path: Path

    @classmethod
    def plan(
        cls,
        *,
        target: str | Path,
        target_format: str | None = None,
        inputs: Sequence[str | Path],
        root: str | Path | None = None,
        params: dict[str, Any] | None = None,
        policy: EmitPolicy | None = None,
        meta: Any | None = None,
        node: str | None = None,
        reproducible: bool | None = True,
        reason: str | None = None,
    ) -> "Manifest":
        """Hash inputs and compute the build digest before the output exists."""

        target_path = Path(target)
        target_name = target_path.name
        _validate_target_name(target_name)
        fmt_name = _target_format(target_path, target_format)
        if not inputs:
            raise ProvenanceError(
                f"manifest for {target_name!r} declares no inputs; an output with no declared "
                "inputs cannot be checked for staleness"
            )

        root_path = _resolve_root(target_path, root)
        records = _input_records(inputs, root_path)
        resolved_policy = policy or EmitPolicy.from_environment()
        if resolved_policy.source_date_epoch is None:
            epoch = _newest_committer_epoch(root_path, records)
            if epoch is not None:
                resolved_policy = replace(resolved_policy, source_date_epoch=epoch)
        if (
            fmt_name == "pdf"
            and reproducible is True
            and reason is None
            and resolved_policy.source_date_epoch is None
        ):
            # No build timestamp available, so a matplotlib PDF writes its own
            # wall clock. Declare it, do not fail the build and do not pretend.
            reproducible = False
            reason = "no-source-date-epoch"

        target_block = _target_from_meta(target_name, fmt_name, meta)
        params_block = _normalise_params(params or {}, resolved_policy)
        root_vcs = git_provenance(root_path)
        tool = _tool_record()
        environment = _environment(fmt_name)
        _validate_reproducibility(reproducible, reason)

        base = {
            "schema": SCHEMA,
            "target": target_block,
            "inputs": [_input_dict(record) for record in records],
            "params": params_block,
            "policy": resolved_policy.as_manifest_dict(),
            "tool": _tool_dict(tool),
            "build": {
                "digest": "",
                "root": root_path.as_posix(),
                "root_vcs": _vcs_dict(root_vcs),
                "node": node,
            },
            "output": {
                "sha256": None,
                "bytes": None,
                "reproducible": reproducible,
                "reason": reason,
            },
            "stamp": {"text": None, "placement": None},
            "environment": environment,
        }
        digest = _compute_digest(base)
        dirty = bool(root_vcs.dirty) or bool(tool.dirty)
        stamp, placement = _default_stamp(fmt_name, meta, digest, dirty)
        return cls(
            schema=SCHEMA,
            target_name=target_name,
            target_format=fmt_name,
            drawing_number=target_block["drawing_number"],
            revision=target_block["revision"],
            inputs=records,
            params=params_block,
            policy=resolved_policy,
            tool=tool,
            root=root_path,
            root_vcs=root_vcs,
            node=node,
            digest=digest,
            output_sha256=None,
            output_bytes=None,
            reproducible=reproducible,
            reason=reason,
            stamp_text=stamp,
            stamp_placement=placement,
            environment=environment,
            target_path=target_path,
        )

    def with_output(self, path: str | Path) -> "Manifest":
        """Return a copy with output digest and byte count filled from the written file."""

        digest, size = sha256_file(path)
        return replace(
            self,
            output_sha256=digest,
            output_bytes=size,
            target_path=Path(path),
            target_name=Path(path).name,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "target": {
                "name": self.target_name,
                "format": self.target_format,
                "drawing_number": self.drawing_number,
                "revision": self.revision,
            },
            "inputs": [_input_dict(record) for record in self.inputs],
            "params": self.params,
            "policy": self.policy.as_manifest_dict(),
            "tool": _tool_dict(self.tool),
            "build": {
                "digest": self.digest,
                "root": self.root.as_posix(),
                "root_vcs": _vcs_dict(self.root_vcs),
                "node": self.node,
            },
            "output": {
                "sha256": self.output_sha256,
                "bytes": self.output_bytes,
                "reproducible": self.reproducible,
                "reason": self.reason,
            },
            "stamp": {"text": self.stamp_text, "placement": self.stamp_placement},
            "environment": self.environment,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Manifest":
        """Validate a manifest dictionary and return the typed record."""

        if not isinstance(data, dict):
            raise ProvenanceError("manifest must be a JSON object")
        schema = data.get("schema")
        if schema != SCHEMA:
            raise ProvenanceError(
                f"unsupported manifest schema {schema!r}; this build understands {SCHEMA!r}"
            )
        target = _mapping(data.get("target"), "target")
        target_name = _string(target.get("name"), "target.name")
        _validate_target_name(target_name)
        target_format = _string(target.get("format"), "target.format")
        _validate_target_format(target_format)
        drawing_number = _optional_string(target.get("drawing_number"), "target.drawing_number")
        revision = _optional_string(target.get("revision"), "target.revision")

        inputs = _read_inputs(data.get("inputs"), target_name)
        params = _validate_json_value(data.get("params", {}), "params")
        policy = _policy_from_dict(_mapping(data.get("policy"), "policy"))
        tool = _tool_from_dict(_mapping(data.get("tool"), "tool"))
        build = _mapping(data.get("build"), "build")
        digest = _hex64(_string(build.get("digest"), "build.digest"), "build.digest")
        root = Path(_string(build.get("root"), "build.root"))
        root_vcs = _vcs_from_dict(_mapping(build.get("root_vcs"), "build.root_vcs"))
        node = _optional_string(build.get("node"), "build.node")
        output = _mapping(data.get("output"), "output")
        output_sha = _optional_hex64(output.get("sha256"), "output.sha256")
        output_bytes = _optional_nonnegative_int(output.get("bytes"), "output.bytes")
        reproducible = output.get("reproducible")
        if reproducible is not None and not isinstance(reproducible, bool):
            raise ProvenanceError(
                f"output.reproducible must be true, false or null, got {reproducible!r}"
            )
        reason = _optional_string(output.get("reason"), "output.reason")
        _validate_reproducibility(reproducible, reason)
        stamp = _mapping(data.get("stamp"), "stamp")
        stamp_value = _optional_string(stamp.get("text"), "stamp.text")
        stamp_placement = _optional_string(stamp.get("placement"), "stamp.placement")
        if stamp_placement is not None and stamp_placement not in STAMP_PLACEMENTS:
            raise ProvenanceError(
                "stamp.placement must be one of "
                f"{sorted(STAMP_PLACEMENTS)}, got {stamp_placement!r}"
            )
        environment = _mapping(data.get("environment"), "environment")
        return cls(
            schema=SCHEMA,
            target_name=target_name,
            target_format=target_format,
            drawing_number=drawing_number,
            revision=revision,
            inputs=inputs,
            params=params,
            policy=policy,
            tool=tool,
            root=root,
            root_vcs=root_vcs,
            node=node,
            digest=digest,
            output_sha256=output_sha,
            output_bytes=output_bytes,
            reproducible=reproducible,
            reason=reason,
            stamp_text=stamp_value,
            stamp_placement=stamp_placement,
            environment=dict(environment),
            target_path=root / target_name,
        )

    @classmethod
    def load(cls, path: str | Path) -> "Manifest":
        manifest_path = Path(path)
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise ProvenanceError(f"cannot read {manifest_path}: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise ProvenanceError(f"invalid JSON in {manifest_path}: {exc}") from exc
        manifest = cls.from_dict(data)
        return replace(manifest, target_path=_target_from_manifest_path(manifest_path))

    def write(self, path: str | Path | None = None) -> Path:
        """Write ``<target>.manifest.json`` beside the target unless a path is supplied."""

        if self.output_sha256 is None or self.output_bytes is None:
            raise ProvenanceError(
                f"manifest for {self.target_name!r} has no output hash; call with_output() first"
            )
        _validate_reproducibility(self.reproducible, self.reason)
        target = Path(path) if path is not None else self.path_for(self.target_path)
        return write_json_canonical(target, self.to_dict(), self.policy)

    @staticmethod
    def path_for(target: str | Path) -> Path:
        """``<target>.manifest.json`` — the one place this filename is constructed."""

        target_path = Path(target)
        return target_path.with_name(target_path.name + ".manifest.json")

    def stale_findings(self) -> tuple["Finding", ...]:
        """Re-hash inputs and output; return no findings when the manifest is current."""

        findings: list[Finding] = []
        try:
            if not verify_digest(self.to_dict()):
                findings.append(
                    Finding(
                        "error",
                        "DIGEST-MISMATCH",
                        f"{self.target_name}: build digest does not match manifest preimage",
                    )
                )
        except ProvenanceError as exc:
            findings.append(Finding("error", "SCHEMA-UNSUPPORTED", str(exc)))

        for record in self.inputs:
            path = self.root / Path(*PurePosixPath(record.path).parts)
            if not path.is_file():
                findings.append(
                    Finding("error", "INPUT-MISSING", f"{record.path}: input file is missing")
                )
                continue
            current_hash, current_bytes = sha256_file(path)
            if current_hash != record.sha256 or current_bytes != record.bytes:
                findings.append(
                    Finding(
                        "error",
                        "INPUT-CHANGED",
                        f"{record.path}: input changed since manifest was written",
                    )
                )
            if record.tracked is False:
                findings.append(
                    Finding(
                        "warn",
                        "INPUT-UNTRACKED",
                        f"{record.path}: input is not tracked by git",
                    )
                )

        if not self.target_path.is_file():
            findings.append(
                Finding("error", "OUTPUT-MISSING", f"{self.target_name}: output file is missing")
            )
        elif self.output_sha256 is not None and self.output_bytes is not None:
            current_hash, current_bytes = sha256_file(self.target_path)
            if current_hash != self.output_sha256 or current_bytes != self.output_bytes:
                findings.append(
                    Finding(
                        "error",
                        "OUTPUT-CHANGED",
                        f"{self.target_name}: output changed since manifest was written",
                    )
                )
            if self.stamp_text and self.target_format in {"svg", "dxf"}:
                try:
                    text = self.target_path.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    text = ""
                if self.stamp_text not in text:
                    findings.append(
                        Finding(
                            "error",
                            "STAMP-MISMATCH",
                            f"{self.target_name}: output does not contain manifest stamp",
                        )
                    )

        if self.stamp_text is not None:
            expected_stamp = stamp_text(
                self.digest,
                dirty=bool(self.root_vcs.dirty) or bool(self.tool.dirty),
            )
            if expected_stamp != self.stamp_text:
                findings.append(
                    Finding(
                        "error",
                        "STAMP-MISMATCH",
                        f"{self.target_name}: stamp {self.stamp_text!r} does not match "
                        "build digest",
                    )
                )

        current_tool = _tool_record()
        if self.tool.commit and current_tool.commit and self.tool.commit != current_tool.commit:
            findings.append(
                Finding(
                    "warn",
                    "TOOL-COMMIT-CHANGED",
                    "technical_drawings_for_agents tool commit differs from the manifest",
                )
            )
        if self.tool.dirty or self.root_vcs.dirty:
            findings.append(Finding("warn", "TOOL-DIRTY", "manifest was built from a dirty tree"))
        if self.reproducible is False:
            findings.append(
                Finding(
                    "warn",
                    "NOT-REPRODUCIBLE",
                    f"{self.target_name}: output is declared non-reproducible ({self.reason})",
                )
            )
        if self.reproducible is None:
            findings.append(
                Finding(
                    "warn",
                    "REPRODUCIBILITY-UNMEASURED",
                    f"{self.target_name}: reproducibility is unmeasured ({self.reason})",
                )
            )
        return tuple(findings)


@dataclass(frozen=True)
class Finding:
    """A staleness finding — same severity/code/message shape as ``components.layout``."""

    severity: str        # "error" | "warn"
    code: str            # closed set, FINDING_CODES
    message: str

    def __post_init__(self) -> None:
        if self.severity not in {"error", "warn"}:
            raise ProvenanceError(
                f"finding severity must be 'error' or 'warn', got {self.severity!r}"
            )
        if self.code not in FINDING_CODES:
            raise ProvenanceError(f"unknown finding code {self.code!r}")

    def __str__(self) -> str:
        return f"{self.severity.upper()}: {self.code}: {self.message}"


# --------------------------------------------------------------------------- #
# digest + stamp
# --------------------------------------------------------------------------- #


def digest_preimage(data: dict[str, Any]) -> bytes:
    """Return the canonical bytes hashed to produce ``build.digest``.

    Input **content** only: the sorted multiset of ``(sha256, bytes)``, plus the
    target's identity, ``params``, ``policy``, ``tool`` name/version and
    ``build.node``. Input *paths* are deliberately absent — a build's identity
    must not depend on where it was built, or CI and a laptop could never agree
    on a stamp and moving a project would make every artifact look stale. Paths
    stay in the manifest for human traceability.
    """

    if not isinstance(data, dict):
        raise ProvenanceError("manifest must be a JSON object")
    if data.get("schema") != SCHEMA:
        raise ProvenanceError(
            f"unsupported manifest schema {data.get('schema')!r}; "
            f"this build understands {SCHEMA!r}"
        )
    try:
        contents = sorted(
            ({"sha256": item["sha256"], "bytes": item["bytes"]} for item in data["inputs"]),
            key=lambda item: (item["sha256"], item["bytes"]),
        )
        preimage = {
            "schema": data["schema"],
            "target": {
                key: data["target"][key]
                for key in ("name", "format", "drawing_number", "revision")
            },
            "inputs": contents,
            "params": data["params"],
            "policy": data["policy"],
            "tool": {"name": data["tool"]["name"], "version": data["tool"]["version"]},
            "build": {"node": data["build"]["node"]},
        }
        return json.dumps(
            preimage,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except KeyError as exc:
        raise ProvenanceError(f"manifest missing digest field {exc.args[0]!r}") from exc
    except (TypeError, ValueError) as exc:
        raise ProvenanceError(f"cannot compute manifest digest: {exc}") from exc


def verify_digest(data: dict[str, Any]) -> bool:
    """Return true when the stored build digest matches the manifest preimage.

    A mismatch returns ``False`` — it is a finding to report, not a crash. Only
    malformed input raises.
    """

    digest = _compute_digest(data)
    try:
        stored = data["build"]["digest"]
    except KeyError as exc:
        raise ProvenanceError(f"manifest missing digest field {exc.args[0]!r}") from exc
    if not isinstance(stored, str):
        raise ProvenanceError(f"build.digest must be a string, got {stored!r}")
    return digest == stored


def stamp_text(build_digest: str, *, dirty: bool) -> str:
    """``P:`` + eight hex characters, plus ``+`` when built from a dirty tree.

    The ``+`` is never omitted: a dirty-tree sheet that printed a clean-looking
    stamp is the worst possible outcome of this whole change.
    """

    _hex64(build_digest, "build.digest")
    return "P:" + build_digest[:8] + ("+" if dirty else "")


def _compute_digest(data: dict[str, Any]) -> str:
    return hashlib.sha256(digest_preimage(data)).hexdigest()


# --------------------------------------------------------------------------- #
# git + files
# --------------------------------------------------------------------------- #


def git_provenance(path: str | Path) -> VcsRecord:
    """Best-effort git HEAD and dirty state for the work tree containing ``path``.

    ``None`` means "we do not know"; ``False`` means "we checked and it is
    clean". A build from an exported tarball must never report a clean tree.
    """

    root = _git_root(Path(path))
    if root is None:
        return VcsRecord(kind=None, commit=None, dirty=None)
    commit_proc = _git(["rev-parse", "HEAD"], root)
    commit = (
        commit_proc.stdout.strip()
        if commit_proc is not None and commit_proc.returncode == 0
        else None
    )
    if commit is not None and not HEX40.match(commit):
        commit = None
    status_proc = _git(["status", "--porcelain"], root)
    dirty = None
    if status_proc is not None and status_proc.returncode == 0:
        dirty = bool(status_proc.stdout.strip())
    return VcsRecord(kind="git", commit=commit, dirty=dirty)


def sha256_file(path: str | Path) -> tuple[str, int]:
    """Return SHA256 and byte count for a file's raw bytes, streamed in 1 MiB blocks."""

    file_path = Path(path)
    if not file_path.is_file():
        raise ProvenanceError(f"input {file_path} is not a file")
    digest = hashlib.sha256()
    size = 0
    with open(file_path, "rb") as handle:
        while True:
            block = handle.read(1024 * 1024)
            if not block:
                break
            size += len(block)
            digest.update(block)
    return digest.hexdigest(), size


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str] | None:
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}
    try:
        return subprocess.run(
            ["git", "-C", str(cwd), *args],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def _git_root(path: Path) -> Path | None:
    start = path if path.is_dir() else path.parent
    if not start.exists():
        start = start.parent if start.parent != start else start
    proc = _git(["rev-parse", "--show-toplevel"], start)
    if proc is None or proc.returncode != 0:
        return None
    text = proc.stdout.strip()
    return Path(text).resolve() if text else None


def _is_tracked(root: Path, path: Path) -> bool | None:
    if _git_root(root) is None:
        return None
    try:
        rel = path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return None
    proc = _git(["ls-files", "--error-unmatch", "--", rel], root)
    if proc is None:
        return None
    return proc.returncode == 0


def _newest_committer_epoch(root: Path, records: tuple[InputRecord, ...]) -> int | None:
    epochs: list[int] = []
    if _git_root(root) is None:
        return None
    for record in records:
        if record.tracked is not True:
            continue
        proc = _git(["log", "-1", "--format=%ct", "--", record.path], root)
        if proc is None or proc.returncode != 0:
            continue
        text = proc.stdout.strip()
        if text.isdigit():
            epochs.append(int(text))
    return max(epochs) if epochs else None


# --------------------------------------------------------------------------- #
# validation + conversion helpers
# --------------------------------------------------------------------------- #


def _resolve_root(target: Path, root: str | Path | None) -> Path:
    if root is not None:
        return Path(root).resolve()
    git_root = _git_root(target)
    if git_root is not None:
        return git_root
    return target.parent.resolve()


def _input_records(inputs: Sequence[str | Path], root: Path) -> tuple[InputRecord, ...]:
    records: list[InputRecord] = []
    seen: set[str] = set()
    for raw in inputs:
        path = Path(raw)
        resolved = path.resolve()
        if not resolved.is_file():
            raise ProvenanceError(f"input {path} is not a file")
        try:
            rel = resolved.relative_to(root.resolve())
        except ValueError as exc:
            raise ProvenanceError(
                f"input {path} is outside the manifest root {root.as_posix()!r}; widen root= "
                "so the manifest stays machine-independent"
            ) from exc
        posix = PurePosixPath(rel).as_posix()
        if posix in seen:
            raise ProvenanceError(f"manifest input path {posix!r} is declared more than once")
        seen.add(posix)
        digest, size = sha256_file(resolved)
        records.append(InputRecord(posix, digest, size, _is_tracked(root, resolved)))
    return tuple(sorted(records, key=lambda item: item.path))


def _target_format(target: Path, explicit: str | None) -> str:
    fmt_name = explicit or target.suffix.lower().lstrip(".")
    if not fmt_name:
        raise ProvenanceError("target.format could not be inferred from the output suffix")
    fmt_name = fmt_name.lower()
    _validate_target_format(fmt_name)
    return fmt_name


def _validate_target_format(value: str) -> None:
    if value not in TARGET_FORMATS:
        raise ProvenanceError(
            f"target.format must be one of {sorted(TARGET_FORMATS)}, got {value!r}"
        )


def _validate_target_name(value: str) -> None:
    if not value or "/" in value or "\\" in value:
        raise ProvenanceError(f"target.name must be a non-empty basename, got {value!r}")


def _target_from_meta(target_name: str, target_format: str, meta: Any | None) -> dict[str, Any]:
    return {
        "name": target_name,
        "format": target_format,
        "drawing_number": getattr(meta, "number", None) if meta is not None else None,
        "revision": getattr(meta, "revision", None) if meta is not None else None,
    }


def _normalise_params(value: Any, policy: EmitPolicy) -> dict[str, Any]:
    normalised = _normalise_json_value(value, "params", policy)
    if not isinstance(normalised, dict):
        raise ProvenanceError("params must be a JSON object")
    return normalised


def _normalise_json_value(value: Any, ctx: str, policy: EmitPolicy) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return policy.q(value, policy.precision_ratio)
    if isinstance(value, (list, tuple)):
        return [_normalise_json_value(item, f"{ctx}[]", policy) for item in value]
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ProvenanceError(f"{ctx}: JSON object keys must be strings, got {key!r}")
            out[key] = _normalise_json_value(item, f"{ctx}.{key}", policy)
        return out
    raise ProvenanceError(f"{ctx}: value {value!r} is not JSON-serialisable")


def _validate_json_value(value: Any, ctx: str) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ProvenanceError(f"{ctx}: non-finite float {value!r} is not valid JSON")
        return value
    if isinstance(value, list):
        return [_validate_json_value(item, f"{ctx}[]") for item in value]
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ProvenanceError(f"{ctx}: JSON object keys must be strings, got {key!r}")
            out[key] = _validate_json_value(item, f"{ctx}.{key}")
        return out
    raise ProvenanceError(f"{ctx}: value {value!r} is not JSON-serialisable")


def _read_inputs(value: Any, target_name: str) -> tuple[InputRecord, ...]:
    if not isinstance(value, list) or not value:
        raise ProvenanceError(
            f"manifest for {target_name!r} declares no inputs; an output with no declared "
            "inputs cannot be checked for staleness"
        )
    records: list[InputRecord] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        block = _mapping(item, f"inputs[{index}]")
        path = _string(block.get("path"), f"inputs[{index}].path")
        if path in seen:
            raise ProvenanceError(f"manifest input path {path!r} is declared more than once")
        seen.add(path)
        digest = _hex64(
            _string(block.get("sha256"), f"inputs[{index}].sha256"),
            f"inputs[{index}].sha256",
        )
        size = _nonnegative_int(block.get("bytes"), f"inputs[{index}].bytes")
        tracked = block.get("tracked")
        if tracked is not None and not isinstance(tracked, bool):
            raise ProvenanceError(f"inputs[{index}].tracked must be true, false or null")
        records.append(InputRecord(path, digest, size, tracked))
    sorted_records = tuple(sorted(records, key=lambda item: item.path))
    if tuple(record.path for record in records) != tuple(
        record.path for record in sorted_records
    ):
        raise ProvenanceError("inputs must be sorted by path")
    return sorted_records


def _policy_from_dict(data: dict[str, Any]) -> EmitPolicy:
    allowed = set(PRECISION_FIELDS) | {"source_date_epoch", *POLICY_FLAG_FIELDS}
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ProvenanceError(f"policy contains unknown key(s): {', '.join(unknown)}")
    missing = sorted(allowed - set(data))
    if missing:
        raise ProvenanceError(f"policy missing key(s): {', '.join(missing)}")
    return EmitPolicy(**data)


def _tool_record() -> ToolRecord:
    from . import __version__

    vcs = git_provenance(Path(__file__).resolve().parent)
    return ToolRecord("technical_drawings_for_agents", __version__, vcs.commit, vcs.dirty)


def _tool_from_dict(data: dict[str, Any]) -> ToolRecord:
    name = _string(data.get("name"), "tool.name")
    if name != "technical_drawings_for_agents":
        raise ProvenanceError(f"tool.name must be 'technical_drawings_for_agents', got {name!r}")
    version = _string(data.get("version"), "tool.version")
    commit = _optional_hex40(data.get("commit"), "tool.commit")
    dirty = data.get("dirty")
    if dirty is not None and not isinstance(dirty, bool):
        raise ProvenanceError(f"tool.dirty must be true, false or null, got {dirty!r}")
    return ToolRecord(name, version, commit, dirty)


def _vcs_from_dict(data: dict[str, Any]) -> VcsRecord:
    kind = data.get("kind")
    if kind is not None and kind != "git":
        raise ProvenanceError(f"root_vcs.kind must be 'git' or null, got {kind!r}")
    commit = _optional_hex40(data.get("commit"), "root_vcs.commit")
    dirty = data.get("dirty")
    if dirty is not None and not isinstance(dirty, bool):
        raise ProvenanceError(f"root_vcs.dirty must be true, false or null, got {dirty!r}")
    return VcsRecord(kind, commit, dirty)


def _validate_reproducibility(reproducible: bool | None, reason: str | None) -> None:
    if reproducible is not None and not isinstance(reproducible, bool):
        raise ProvenanceError(
            f"output.reproducible must be true, false or null, got {reproducible!r}"
        )
    if reproducible is not True:
        if reason is None:
            raise ProvenanceError(
                "output.reason is required when output.reproducible is false or null"
            )
        if reason not in REASONS:
            raise ProvenanceError(
                f"output.reason must be one of {sorted(REASONS)}, got {reason!r}"
            )
    elif reason is not None:
        raise ProvenanceError("output.reason must be null when output.reproducible is true")


def _default_stamp(
    target_format: str, meta: Any | None, digest: str, dirty: bool
) -> tuple[str | None, str | None]:
    if target_format == "svg":
        placement = "below-title-block" if meta is not None else "fallback-bottom-left"
        return stamp_text(digest, dirty=dirty), placement
    if target_format == "dxf" and meta is not None:
        return stamp_text(digest, dirty=dirty), "dxf-titleblock-layer"
    return None, None


def _input_dict(record: InputRecord) -> dict[str, Any]:
    return {
        "path": record.path,
        "sha256": record.sha256,
        "bytes": record.bytes,
        "tracked": record.tracked,
    }


def _tool_dict(record: ToolRecord) -> dict[str, Any]:
    return {
        "name": record.name,
        "version": record.version,
        "commit": record.commit,
        "dirty": record.dirty,
    }


def _vcs_dict(record: VcsRecord) -> dict[str, Any]:
    return {"kind": record.kind, "commit": record.commit, "dirty": record.dirty}


def _environment(target_format: str) -> dict[str, Any]:
    libraries: dict[str, str] = {}
    from importlib import metadata

    wanted = []
    if target_format == "dxf":
        wanted.append("ezdxf")
    if target_format in {"pdf", "png"}:
        wanted.append("matplotlib")
    for name in wanted:
        try:
            libraries[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            continue
    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    return {
        "host": socket.gethostname(),
        "user": getpass.getuser(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "libraries": libraries,
        "generated_at": generated_at.replace("+00:00", "Z"),
    }


def _target_from_manifest_path(path: Path) -> Path:
    name = path.name
    suffix = ".manifest.json"
    if name.endswith(suffix):
        return path.with_name(name[: -len(suffix)])
    return path


def _mapping(value: Any, ctx: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProvenanceError(f"{ctx} must be a mapping")
    return value


def _string(value: Any, ctx: str) -> str:
    if not isinstance(value, str) or not value:
        raise ProvenanceError(f"{ctx} must be a non-empty string")
    return value


def _optional_string(value: Any, ctx: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ProvenanceError(f"{ctx} must be a string or null")
    return value


def _nonnegative_int(value: Any, ctx: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ProvenanceError(f"{ctx} must be a non-negative int, got {value!r}")
    return value


def _optional_nonnegative_int(value: Any, ctx: str) -> int | None:
    if value is None:
        return None
    return _nonnegative_int(value, ctx)


def _hex64(value: str, ctx: str) -> str:
    if not HEX64.match(value):
        raise ProvenanceError(f"{ctx} must be 64 lower-case hex characters, got {value!r}")
    return value


def _optional_hex64(value: Any, ctx: str) -> str | None:
    if value is None:
        return None
    return _hex64(_string(value, ctx), ctx)


def _optional_hex40(value: Any, ctx: str) -> str | None:
    if value is None:
        return None
    text = _string(value, ctx)
    if not HEX40.match(text):
        raise ProvenanceError(
            f"{ctx} must be 40 lower-case hex characters or null, got {text!r}"
        )
    return text
