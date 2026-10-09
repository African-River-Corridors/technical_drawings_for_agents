"""Declarative drawing-set builds: one graph, no remembered build order.

The driver exists to move a pipeline's ordering out of an operator's memory and
into tested code. It validates the whole graph before running anything, decides
staleness from input *content* (never mtime), runs the stale nodes serially in a
deterministic order, and stops at the first blocked or failed node so a
mixed-vintage drawing set can never look successful.

Three things here are load-bearing and easy to get wrong:

* **Output identity is a key, not a path object.** Every declared output is keyed
  ``<root-relative POSIX path>[:<gpkg layer>]``. That string is what uniqueness is
  validated on, what appears in error messages, and what is persisted in state —
  so it must never be machine-specific (the same reason ``provenance`` keeps paths
  out of a digest).
* **A GeoPackage is a container, not an output.** The unit of production is the
  layer. Two nodes writing different layers of one ``.gpkg`` is legal; two nodes
  writing the same layer is a hard error. The human-edited ``placements`` layer is
  an input the build may never write, and ``--force`` cannot override that.
* **Sheet-ness is declared, never inferred.** A node carrying ``review: pdf``
  owes a PDF; a node without the marker owes nothing. Nothing in this module
  guesses which artifact a human is meant to look at.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from collections import defaultdict, deque
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, Protocol

import yaml

from . import __version__
from .provenance import (
    EmitPolicy,
    ProvenanceError,
    emit_digest,
    sha256_file,
    write_text_canonical,
)

DigestMode = Literal["content", "size-mtime"]
Runner = Literal[
    "verb:render",
    "verb:validate",
    "verb:bfd",
    "verb:pid",
    "verb:component",
    "verb:layout",
    "script",
    "command",
    "external",
]
NodeState = Literal["current", "stale", "unbuilt", "missing-output", "blocked"]
Action = Literal["built", "skipped", "blocked", "failed"]
Review = Literal["pdf"]

RUNNERS: frozenset[str] = frozenset(
    {
        "verb:render",
        "verb:validate",
        "verb:bfd",
        "verb:pid",
        "verb:component",
        "verb:layout",
        "script",
        "command",
        "external",
    }
)
#: The only accepted ``review:`` markers. ``review`` is what makes a target a
#: *reviewable sheet*; the build never infers it from a suffix or a directory.
REVIEWS: frozenset[str] = frozenset({"pdf"})
SCHEMA_VERSION = 1
STATE_DIRNAME = ".technical_drawings_for_agents"
STATE_FILENAME = "build-state.json"
STATE_VERSION = 2

#: Sentinel digest for a declared, non-optional input that is absent when the
#: digest is computed (an upstream output that has not been produced yet, or was
#: deleted). It differs from every real digest and from ``optional``'s ``-``, so
#: the node reads *stale* instead of raising ``FileNotFoundError`` mid-plan.
MISSING_VALUE = "!missing"

_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_TOP_KEYS = frozenset({"version", "set", "inputs", "nodes", "targets"})
_SET_KEYS = frozenset({"id", "description", "root"})
_INPUT_KEYS = frozenset({"path", "digest", "optional"})
_NODE_KEYS = frozenset(
    {
        "run",
        "description",
        "inputs",
        "outputs",
        "needs",
        "expand_inputs",
        "args",
        "timeout_s",
        "review",
    }
)
_RUNNER_ARG_KEYS: dict[str, frozenset[str]] = {
    "verb:layout": frozenset(
        {
            "config",
            "view",
            "from_geojson",
            "emit",
            "out",
            "emit_register",
            "check_only",
            "warn_only",
            "no_snap",
        }
    ),
    "verb:render": frozenset({"source", "out"}),
    "verb:validate": frozenset({"target"}),
    "verb:bfd": frozenset({"data", "out", "view"}),
    "verb:pid": frozenset({"data", "out"}),
    "verb:component": frozenset({"spec", "view", "emit", "out", "origin", "place", "rot"}),
    "script": frozenset({"path", "argv", "interpreter", "cwd", "tools"}),
    "command": frozenset({"argv", "tool", "cwd"}),
    "external": frozenset({"reason", "howto", "command_hint"}),
}

#: The GeoPackage layer a human authors by hand in QGIS. It is the head of the
#: dependency chain and no build may write it, under any flag.
HUMAN_AUTHORED_LAYERS: frozenset[str] = frozenset({"placements"})

#: Runners that produce no artifact. They have nothing to be stale about, so they
#: run whenever selected, nothing is cached about their verdict, and ``--check``
#: reports them "would run" rather than counting them as not-current — otherwise a
#: set containing one could never pass the gate it exists to be.
CHECK_RUNNERS: frozenset[str] = frozenset({"verb:validate"})

#: Attributes the ``verb:layout`` / ``verb:component`` runners set on the
#: ``argparse.Namespace`` they hand to ``components.cli``. Anything the CLI
#: declares that is absent here would ``AttributeError`` deep inside a node, so
#: ``tests/test_build_graph.py`` asserts the parser's dests are covered. A new
#: sibling flag therefore fails a test rather than a build.
_LAYOUT_CLI_DEFAULTS: dict[str, Any] = {
    "layers": None,
    "plot_scale": None,
    "pdf": False,
    "manifest": False,
    "require_ids": False,
    "check_register": False,
    "canonicalise_register": False,
    "register_source": None,
}
_COMPONENT_CLI_DEFAULTS: dict[str, Any] = {
    "layers": None,
    "plot_scale": None,
    "pdf": False,
}


class BuildError(ValueError):
    """Raised when a drawing set, its graph, or a build precondition is malformed."""


@dataclass(frozen=True)
class InputRef:
    path: Path
    digest: DigestMode = "content"
    optional: bool = False
    name: str | None = None
    implicit: bool = False
    layer: str | None = None


@dataclass(frozen=True)
class OutputRef:
    path: Path
    layer: str | None = None


@dataclass(frozen=True)
class Node:
    name: str
    run: Runner
    inputs: tuple[InputRef, ...]
    outputs: tuple[Path, ...]
    needs: tuple[str, ...]
    args: Mapping[str, Any]
    description: str = ""
    expand_inputs: bool = False
    timeout_s: int | None = None
    output_refs: tuple[OutputRef, ...] = ()
    review: Review | None = None

    @property
    def review_pdfs(self) -> tuple[OutputRef, ...]:
        """Declared PDF outputs this node owes a reviewer (empty unless marked)."""

        if self.review != "pdf":
            return ()
        return tuple(ref for ref in self.output_refs if ref.path.suffix.lower() == ".pdf")


@dataclass(frozen=True)
class DrawingSet:
    id: str
    source: Path
    root: Path
    nodes: Mapping[str, Node]
    targets: Mapping[str, tuple[str, ...]]
    description: str = ""
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class NodeRecord:
    """One node's last successful build. The unit of exchange with a StateStore."""

    node: str
    input_digest: str
    outputs: Mapping[str, str]
    fast: Mapping[str, tuple[int, int, str]]
    tool_version: str
    #: label -> digest value, so *which* input changed survives a new process.
    inputs: Mapping[str, str] = MappingProxyType({})
    #: digest of the defaulted node definition, so an arg change is attributable.
    node_def: str = ""


class StateStore(Protocol):
    """Where a build's per-node records live. P3's manifests can back this."""

    def read(self, node: str) -> NodeRecord | None: ...
    def write(self, record: NodeRecord) -> None: ...
    def flush(self) -> None: ...


class JsonStateStore:
    """Default store: one JSON file at ``<root>/.technical_drawings_for_agents/build-state.json``.

    Reading never creates anything: ``--check`` must be usable in a read-only
    checkout and must not leave a directory behind as evidence it ran.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self.path = self.root / STATE_DIRNAME / STATE_FILENAME
        self._records: dict[str, NodeRecord] | None = None

    def read(self, node: str) -> NodeRecord | None:
        self._load()
        assert self._records is not None
        return self._records.get(node)

    def write(self, record: NodeRecord) -> None:
        self._load()
        assert self._records is not None
        self._records[record.node] = record

    def flush(self) -> None:
        self._load()
        assert self._records is not None
        data = {
            "version": STATE_VERSION,
            "nodes": {
                name: {
                    "node": record.node,
                    "input_digest": record.input_digest,
                    "node_def": record.node_def,
                    "inputs": dict(sorted(record.inputs.items())),
                    "outputs": dict(sorted(record.outputs.items())),
                    "fast": {
                        key: [value[0], value[1], value[2]]
                        for key, value in sorted(record.fast.items())
                    },
                    "tool_version": record.tool_version,
                }
                for name, record in sorted(self._records.items())
            },
        }
        # Every text write in this module goes through provenance's canonical
        # writer: one newline convention, one atomic-replace path, no second
        # opinion about how bytes reach the disk.
        write_text_canonical(
            self.path,
            json.dumps(data, sort_keys=True, indent=2, ensure_ascii=True),
            EmitPolicy(),
        )

    def _load(self) -> None:
        if self._records is not None:
            return
        self._records = {}
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise BuildError(f"cannot read {self.path}: {exc}") from exc
        nodes = data.get("nodes", {})
        if not isinstance(nodes, dict):
            raise BuildError(f"{self.path}: nodes must be a mapping")
        for name, raw in nodes.items():
            if not isinstance(raw, dict):
                raise BuildError(f"{self.path}: nodes.{name} must be a mapping")
            ctx = f"{self.path}: nodes.{name}"
            fast_raw = raw.get("fast", {})
            if not isinstance(fast_raw, dict):
                raise BuildError(f"{ctx}.fast must be a mapping")
            fast: dict[str, tuple[int, int, str]] = {}
            for key, value in fast_raw.items():
                if (
                    not isinstance(key, str)
                    or not isinstance(value, list)
                    or len(value) != 3
                    or any(isinstance(item, bool) for item in value[:2])
                    or not isinstance(value[0], int)
                    or not isinstance(value[1], int)
                    or not isinstance(value[2], str)
                ):
                    raise BuildError(f"{ctx}.fast.{key} is malformed")
                fast[key] = (value[0], value[1], value[2])
            self._records[str(name)] = NodeRecord(
                node=_required_string(raw.get("node"), f"{ctx}.node"),
                input_digest=_required_string(raw.get("input_digest"), f"{ctx}.input_digest"),
                outputs=MappingProxyType(_string_mapping(raw.get("outputs", {}), f"{ctx}.outputs")),
                fast=MappingProxyType(fast),
                tool_version=_required_string(raw.get("tool_version"), f"{ctx}.tool_version"),
                inputs=MappingProxyType(_string_mapping(raw.get("inputs", {}), f"{ctx}.inputs")),
                node_def=str(raw.get("node_def", "")),
            )


@dataclass(frozen=True)
class NodeStatus:
    node: str
    state: NodeState
    reason: str
    changed_inputs: tuple[str, ...] = ()
    #: True when this is an ``external`` node whose output exists with no state
    #: record: a build records its digest and treats it as current, spawning
    #: nothing. Surfaced so the adoption can be *logged*, never silent.
    adopt: bool = False


@dataclass(frozen=True)
class NodeResult:
    node: str
    action: Action
    outputs: tuple[Path, ...] = ()
    stdout: str = ""
    stderr: str = ""
    error: str | None = None
    exit_code: int | None = None
    duration_s: float | None = None
    quarantined: tuple[tuple[Path, Path], ...] = ()


@dataclass(frozen=True)
class BuildReport:
    set_id: str
    order: tuple[str, ...]
    results: tuple[NodeResult, ...]
    stranded: tuple[str, ...] = ()
    #: (node, label) pairs for external outputs adopted into state this run.
    adopted: tuple[tuple[str, str], ...] = ()
    #: (node, label) pairs for ``review: pdf`` targets with no PDF on disk.
    review_failures: tuple[tuple[str, str], ...] = ()

    @property
    def ok(self) -> bool:
        return not self.failed and not self.blocked and not self.review_failures

    @property
    def built(self) -> tuple[str, ...]:
        return tuple(result.node for result in self.results if result.action == "built")

    @property
    def skipped(self) -> tuple[str, ...]:
        return tuple(result.node for result in self.results if result.action == "skipped")

    @property
    def blocked(self) -> tuple[str, ...]:
        return tuple(result.node for result in self.results if result.action == "blocked")

    @property
    def failed(self) -> tuple[str, ...]:
        return tuple(result.node for result in self.results if result.action == "failed")


# --------------------------------------------------------------------------- #
# loading + validation
# --------------------------------------------------------------------------- #


def load_drawing_set(path: str | Path) -> DrawingSet:
    """Load and fully validate a ``drawing-set.yaml`` (V1-V9). Raises BuildError."""

    source = Path(path).resolve()
    data = _read_yaml(source)
    if not isinstance(data, dict):
        raise BuildError(f"{source.name}: top level must be a mapping")
    _reject_unknown_keys(data, _TOP_KEYS, source.name)

    if "version" not in data:
        raise BuildError(f"{source.name}: missing version (supported: {SCHEMA_VERSION})")
    version = data["version"]
    if version != SCHEMA_VERSION:
        raise BuildError(
            f"{source.name}: version {version} not supported by technical_drawings_for_agents {__version__} "
            f"(supported: {SCHEMA_VERSION})"
        )

    set_block = data.get("set")
    if not isinstance(set_block, dict):
        raise BuildError(f"{source.name}: missing set: {{id, root}}")
    _reject_unknown_keys(set_block, _SET_KEYS, f"{source.name}: set")
    set_id = _name(set_block.get("id"), f"{source.name}: set.id")
    description = set_block.get("description", "")
    if not isinstance(description, str):
        raise BuildError(f"{source.name}: set.description must be a string")
    raw_root = set_block.get("root", ".")
    if not isinstance(raw_root, str) or not raw_root.strip():
        raise BuildError(f"{source.name}: set.root must be a non-empty path string")
    root = (source.parent / raw_root).resolve()
    if not root.is_dir():
        raise BuildError(f"{source.name}: set.root not a directory: {root}")

    named_inputs = _load_named_inputs(data.get("inputs"), source.name, root)
    raw_nodes = data.get("nodes")
    if not isinstance(raw_nodes, dict) or not raw_nodes:
        raise BuildError(f"{source.name}: nodes must be a non-empty mapping")
    raw_targets = data.get("targets", {}) or {}
    if not isinstance(raw_targets, dict):
        raise BuildError(f"{source.name}: targets must be a mapping")

    nodes: dict[str, Node] = {}
    for node_name, raw in sorted(raw_nodes.items(), key=lambda item: str(item[0])):
        name = _name(node_name, f"{source.name}: nodes key")
        if name in nodes:
            raise BuildError(f"{source.name}: duplicate node name: {name}")
        nodes[name] = _load_node(name, raw, source.name, root, named_inputs)

    nodes, notes = _expand_layout_inputs(nodes, root, source.name)

    targets: dict[str, tuple[str, ...]] = {}
    for target_name, raw in sorted(raw_targets.items(), key=lambda item: str(item[0])):
        name = _name(target_name, f"{source.name}: targets key")
        if name in nodes:
            raise BuildError(f"{source.name}: target name collides with node name: {name}")
        if not isinstance(raw, list) or not raw:
            raise BuildError(f"{source.name}: targets.{name} must be a non-empty list")
        values = tuple(_required_string(item, f"{source.name}: targets.{name}") for item in raw)
        unknown = sorted(set(values) - set(nodes))
        if unknown:
            raise BuildError(
                f"{source.name}: targets.{name} names unknown node(s): {', '.join(unknown)}"
            )
        targets[name] = tuple(sorted(set(values)))

    _validate_node_references(source.name, nodes)
    _validate_outputs(source.name, root, nodes)
    _validate_graph_acyclic(source.name, root, nodes)
    _validate_source_inputs(nodes)
    _validate_external_howtos(source.name, root, nodes)

    return DrawingSet(
        id=set_id,
        source=source,
        root=root,
        nodes=MappingProxyType(dict(sorted(nodes.items()))),
        targets=MappingProxyType(dict(sorted(targets.items()))),
        description=description,
        notes=notes,
    )


def _load_named_inputs(raw: Any, ctx: str, root: Path) -> dict[str, InputRef]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise BuildError(f"{ctx}: inputs must be a mapping")
    named: dict[str, InputRef] = {}
    for key, value in sorted(raw.items(), key=lambda item: str(item[0])):
        name = _name(key, f"{ctx}: inputs key")
        if not isinstance(value, dict):
            raise BuildError(f"{ctx}: inputs.{name} must be a mapping")
        _reject_unknown_keys(value, _INPUT_KEYS, f"{ctx}: inputs.{name}")
        if "path" not in value:
            raise BuildError(f"{ctx}: inputs.{name}.path is required")
        path, layer = _parse_artifact(value["path"], root, f"{ctx}: inputs.{name}.path")
        digest = value.get("digest", "content")
        if digest not in ("content", "size-mtime"):
            raise BuildError(
                f"{ctx}: inputs.{name}.digest must be one of ['content', 'size-mtime']"
            )
        optional = value.get("optional", False)
        if not isinstance(optional, bool):
            raise BuildError(f"{ctx}: inputs.{name}.optional must be a bool")
        named[name] = InputRef(path=path, digest=digest, optional=optional, name=name, layer=layer)
    return named


def _load_node(
    name: str,
    raw: Any,
    ctx: str,
    root: Path,
    named_inputs: Mapping[str, InputRef],
) -> Node:
    if not isinstance(raw, dict):
        raise BuildError(f"{ctx}: nodes.{name} must be a mapping")
    _reject_unknown_keys(raw, _NODE_KEYS, f"{ctx}: nodes.{name}")
    run = raw.get("run")
    if run not in RUNNERS:
        raise BuildError(f"{ctx}: nodes.{name}.run must be one of {sorted(RUNNERS)}, got {run!r}")
    runner: Runner = run  # type: ignore[assignment]
    description = raw.get("description", "")
    if not isinstance(description, str):
        raise BuildError(f"{ctx}: nodes.{name}.description must be a string")

    review = raw.get("review")
    if review is not None and review not in REVIEWS:
        raise BuildError(
            f"{ctx}: nodes.{name}.review must be one of {sorted(REVIEWS)}, got {review!r}"
        )

    expand = raw.get("expand_inputs", runner == "verb:layout")
    if not isinstance(expand, bool):
        raise BuildError(f"{ctx}: nodes.{name}.expand_inputs must be a bool")
    if expand and runner != "verb:layout":
        raise BuildError(f"{ctx}: nodes.{name}.expand_inputs is not supported for {runner!r}")

    timeout = raw.get("timeout_s")
    if timeout is not None:
        if runner not in ("script", "command"):
            raise BuildError(
                f"{ctx}: nodes.{name}.timeout_s is only accepted for script and command nodes"
            )
        if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout <= 0:
            raise BuildError(f"{ctx}: nodes.{name}.timeout_s must be an int > 0")

    inputs = _node_inputs(raw.get("inputs", []), ctx, name, root, named_inputs)
    output_refs = _node_outputs(raw.get("outputs", []), ctx, name, root)
    if runner == "verb:validate":
        if output_refs:
            raise BuildError(f"{ctx}: nodes.{name}.outputs must be empty for verb:validate")
    elif not output_refs:
        raise BuildError(f"{ctx}: nodes.{name}.outputs must be a non-empty list")

    if review == "pdf" and not any(
        ref.path.suffix.lower() == ".pdf" for ref in output_refs
    ):
        raise BuildError(
            f"{ctx}: node '{name}': review: pdf requires a declared .pdf output "
            f"(declared: {', '.join(_output_label(ref, root) for ref in output_refs) or '-'})"
        )

    needs = raw.get("needs", [])
    if not isinstance(needs, list):
        raise BuildError(f"{ctx}: nodes.{name}.needs must be a list")
    need_values = tuple(
        sorted({_required_string(item, f"{ctx}: nodes.{name}.needs") for item in needs})
    )
    if name in need_values:
        raise BuildError(f"{ctx}: nodes.{name}.needs names itself")

    args = _load_args(runner, raw.get("args", {}), ctx, name, root)
    if runner == "script":
        # A generator is part of its own recipe: the dead Rev-B sheet script is
        # exactly the failure a content hash over the script would have caught.
        script_input = InputRef(path=_resolve_arg_path(args["path"], root), implicit=True)
        if _input_key(script_input) not in {_input_key(item) for item in inputs}:
            inputs = (*inputs, script_input)

    return Node(
        name=name,
        run=runner,
        inputs=tuple(sorted(inputs, key=_input_key)),
        outputs=tuple(ref.path for ref in output_refs),
        needs=need_values,
        args=MappingProxyType(args),
        description=description,
        expand_inputs=expand,
        timeout_s=timeout,
        output_refs=tuple(sorted(output_refs, key=_output_key)),
        review=review,
    )


def _node_inputs(
    raw: Any,
    ctx: str,
    node_name: str,
    root: Path,
    named_inputs: Mapping[str, InputRef],
) -> tuple[InputRef, ...]:
    if raw is None:
        raw = []
    if not isinstance(raw, list):
        raise BuildError(f"{ctx}: nodes.{node_name}.inputs must be a list")
    inputs: list[InputRef] = []
    seen: set[str] = set()
    for index, value in enumerate(raw):
        if not isinstance(value, str):
            raise BuildError(f"{ctx}: nodes.{node_name}.inputs[{index}] must be a path string")
        if value.startswith("@"):
            ref_name = value[1:]
            if ref_name not in named_inputs:
                raise BuildError(
                    f"{ctx}: nodes.{node_name}.inputs[{index}] references unknown "
                    f"input @{ref_name}"
                )
            input_ref = named_inputs[ref_name]
        else:
            path, layer = _parse_artifact(value, root, f"{ctx}: nodes.{node_name}.inputs[{index}]")
            input_ref = InputRef(path=path, layer=layer)
        key = _input_key(input_ref)
        if key in seen:
            raise BuildError(f"{ctx}: node '{node_name}': duplicate input: {value}")
        seen.add(key)
        inputs.append(input_ref)
    return tuple(inputs)


def _node_outputs(raw: Any, ctx: str, node_name: str, root: Path) -> tuple[OutputRef, ...]:
    if raw is None:
        raw = []
    if not isinstance(raw, list):
        raise BuildError(f"{ctx}: nodes.{node_name}.outputs must be a list")
    outputs: list[OutputRef] = []
    seen: set[str] = set()
    for index, value in enumerate(raw):
        path, layer = _parse_artifact(value, root, f"{ctx}: nodes.{node_name}.outputs[{index}]")
        ref = OutputRef(path=path, layer=layer)
        key = _output_key(ref)
        if key in seen:
            raise BuildError(f"{ctx}: node '{node_name}': duplicate output: {value}")
        seen.add(key)
        outputs.append(ref)
    return tuple(outputs)


def _load_args(runner: str, raw: Any, ctx: str, node_name: str, root: Path) -> dict[str, Any]:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise BuildError(f"{ctx}: nodes.{node_name}.args must be a mapping")
    accepted = _RUNNER_ARG_KEYS[runner]
    for key in raw:
        if key in accepted:
            continue
        if runner == "command" and key == "shell":
            raise BuildError(
                f"{ctx}: nodes.{node_name}.args.shell is not supported; command.argv must be "
                "a list executed without a shell (a shell string is not a declared graph)"
            )
        raise BuildError(
            f"{ctx}: nodes.{node_name}.args: unknown key {key!r} "
            f"(accepted: {', '.join(sorted(accepted))})"
        )
    loaders = {
        "verb:layout": _args_layout,
        "verb:render": _args_render,
        "verb:validate": _args_validate,
        "verb:bfd": _args_bfd,
        "verb:pid": _args_pid,
        "verb:component": _args_component,
        "script": _args_script,
        "command": _args_command,
        "external": _args_external,
    }
    return loaders[runner](raw, ctx, node_name, root)


def _args_layout(raw: Mapping[str, Any], ctx: str, node: str, root: Path) -> dict[str, Any]:
    emit = raw.get("emit")
    if emit is not None and emit not in ("geojson", "dxf"):
        raise BuildError(f"{ctx}: nodes.{node}.args.emit must be one of ['dxf', 'geojson']")
    out = _arg_path(raw, "out", ctx, node, root) if raw.get("out") is not None else None
    if emit is not None and out is None:
        raise BuildError(f"{ctx}: nodes.{node}.args.out is required when args.emit is set")
    return {
        "config": _arg_path(raw, "config", ctx, node, root, required=True),
        "view": _string_default(raw, "view", "plan", ctx, node),
        "from_geojson": (
            _arg_path(raw, "from_geojson", ctx, node, root)
            if raw.get("from_geojson") is not None
            else None
        ),
        "emit": emit,
        "out": out,
        "emit_register": (
            _arg_path(raw, "emit_register", ctx, node, root)
            if raw.get("emit_register") is not None
            else None
        ),
        "check_only": _bool_default(raw, "check_only", False, ctx, node),
        "warn_only": _bool_default(raw, "warn_only", False, ctx, node),
        "no_snap": _bool_default(raw, "no_snap", False, ctx, node),
    }


def _args_render(raw: Mapping[str, Any], ctx: str, node: str, root: Path) -> dict[str, Any]:
    source = _arg_path(raw, "source", ctx, node, root, required=True)
    suffix = Path(source).suffix.lower()
    if suffix == ".py":
        raise BuildError(
            f"node '{node}': verb:render cannot take a .py source; use run: script "
            "(see spec 4.6)"
        )
    if suffix not in (".svg", ".dxf"):
        raise BuildError(f"{ctx}: nodes.{node}.args.source must have suffix .svg or .dxf")
    return {
        "source": source,
        "out": (
            _arg_path(raw, "out", ctx, node, root)
            if raw.get("out") is not None
            else _default_out(source)
        ),
    }


def _args_validate(raw: Mapping[str, Any], ctx: str, node: str, root: Path) -> dict[str, Any]:
    return {"target": _arg_path(raw, "target", ctx, node, root, required=True)}


def _args_bfd(raw: Mapping[str, Any], ctx: str, node: str, root: Path) -> dict[str, Any]:
    data = _arg_path(raw, "data", ctx, node, root, required=True)
    view = _string_default(raw, "view", "block", ctx, node)
    if view not in ("block", "swimlane", "profile", "all"):
        raise BuildError(
            f"{ctx}: nodes.{node}.args.view must be one of "
            "['all', 'block', 'profile', 'swimlane']"
        )
    return {
        "data": data,
        "out": (
            _arg_path(raw, "out", ctx, node, root)
            if raw.get("out") is not None
            else _default_out(data)
        ),
        "view": view,
    }


def _args_pid(raw: Mapping[str, Any], ctx: str, node: str, root: Path) -> dict[str, Any]:
    data = _arg_path(raw, "data", ctx, node, root, required=True)
    return {
        "data": data,
        "out": (
            _arg_path(raw, "out", ctx, node, root)
            if raw.get("out") is not None
            else _default_out(data)
        ),
    }


def _args_component(raw: Mapping[str, Any], ctx: str, node: str, root: Path) -> dict[str, Any]:
    emit = raw.get("emit")
    if emit not in ("geojson", "svg", "dxf"):
        raise BuildError(f"{ctx}: nodes.{node}.args.emit must be one of ['dxf', 'geojson', 'svg']")
    has_origin = raw.get("origin") is not None
    has_place = raw.get("place") is not None
    if has_origin and has_place:
        raise BuildError(
            f"{ctx}: nodes.{node}.args needs exactly one of origin or place, not both"
        )
    if not has_origin and not has_place:
        raise BuildError(f"{ctx}: nodes.{node}.args needs exactly one of origin or place")
    rot = raw.get("rot", 0.0)
    if isinstance(rot, bool) or not isinstance(rot, (int, float)):
        raise BuildError(f"{ctx}: nodes.{node}.args.rot must be a number")
    return {
        "spec": _arg_path(raw, "spec", ctx, node, root, required=True),
        "view": _string_default(raw, "view", "plan", ctx, node),
        "emit": emit,
        "out": _arg_path(raw, "out", ctx, node, root, required=True),
        "origin": _optional_string(raw.get("origin"), f"{ctx}: nodes.{node}.args.origin"),
        "place": (
            _arg_path(raw, "place", ctx, node, root) if raw.get("place") is not None else None
        ),
        "rot": float(rot),
    }


def _args_script(raw: Mapping[str, Any], ctx: str, node: str, root: Path) -> dict[str, Any]:
    argv = raw.get("argv", [])
    interpreter = raw.get("interpreter", [sys.executable])
    tools = raw.get("tools", [])
    if not isinstance(argv, list) or not all(isinstance(item, str) for item in argv):
        raise BuildError(f"{ctx}: nodes.{node}.args.argv must be a list of strings")
    if (
        not isinstance(interpreter, list)
        or not interpreter
        or not all(isinstance(item, str) for item in interpreter)
    ):
        raise BuildError(
            f"{ctx}: nodes.{node}.args.interpreter must be a non-empty list of strings"
        )
    if not isinstance(tools, list) or not all(isinstance(item, str) for item in tools):
        raise BuildError(f"{ctx}: nodes.{node}.args.tools must be a list of strings")
    path = _arg_path(raw, "path", ctx, node, root, required=True)
    cwd = (
        _arg_path(raw, "cwd", ctx, node, root)
        if raw.get("cwd") is not None
        else _posix_rel(_resolve_arg_path(path, root).parent, root)
    )
    return {
        "path": path,
        "argv": tuple(argv),
        "interpreter": tuple(interpreter),
        "cwd": cwd,
        "tools": tuple(tools),
    }


def _args_command(raw: Mapping[str, Any], ctx: str, node: str, root: Path) -> dict[str, Any]:
    argv = raw.get("argv")
    if not isinstance(argv, list) or not argv or not all(isinstance(item, str) for item in argv):
        raise BuildError(f"{ctx}: nodes.{node}.args.argv must be a non-empty list of strings")
    tool = raw.get("tool", argv[0])
    if not isinstance(tool, str) or not tool.strip():
        raise BuildError(f"{ctx}: nodes.{node}.args.tool must be a non-empty string")
    cwd = _arg_path(raw, "cwd", ctx, node, root) if raw.get("cwd") is not None else "."
    return {"argv": tuple(argv), "tool": tool, "cwd": cwd}


def _args_external(raw: Mapping[str, Any], ctx: str, node: str, root: Path) -> dict[str, Any]:
    reason = raw.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise BuildError(f"{ctx}: nodes.{node}.args.reason must be a non-empty string")
    howto = _arg_path(raw, "howto", ctx, node, root) if raw.get("howto") is not None else None
    hint = raw.get("command_hint")
    if hint is not None and not isinstance(hint, str):
        raise BuildError(f"{ctx}: nodes.{node}.args.command_hint must be a string")
    return {"reason": reason, "howto": howto, "command_hint": hint}


def _expand_layout_inputs(
    nodes: Mapping[str, Node], root: Path, ctx: str
) -> tuple[dict[str, Node], tuple[str, ...]]:
    expanded = dict(nodes)
    notes: list[str] = []
    for node in sorted(nodes.values(), key=lambda item: item.name):
        if node.run != "verb:layout" or not node.expand_inputs:
            continue
        from .components.layout import LayoutError, load_layout

        config = _resolve_arg_path(node.args["config"], root)
        try:
            layout = load_layout(config)
        except LayoutError as exc:
            raise BuildError(f"node '{node.name}': {exc}") from exc
        implicit: list[InputRef] = []
        for specs in layout.types.values():
            for spec_path in specs:
                implicit.append(InputRef(path=Path(spec_path).resolve(), implicit=True))
        if layout.register is not None:
            implicit.append(InputRef(path=Path(layout.register).resolve(), implicit=True))
        notes.append(f"{node.name}: +{len(implicit)} implicit input(s) from {config.name}")

        output_keys = {_output_key(ref) for ref in node.output_refs}
        kept = [item for item in implicit if _input_key(item) not in output_keys]
        dropped = sorted(
            (item for item in implicit if _input_key(item) in output_keys), key=_input_key
        )
        if dropped:
            labels = ", ".join(_input_label(item, root) for item in dropped)
            verb = "is" if len(dropped) == 1 else "are"
            notes.append(
                f"{node.name}: {len(dropped)} implicit input {verb} also an output of this "
                f"node, not tracked as an input: {labels}"
            )
        merged = list(node.inputs)
        seen = {_input_key(item) for item in merged}
        for item in kept:
            key = _input_key(item)
            if key not in seen:
                merged.append(item)
                seen.add(key)
        expanded[node.name] = replace(node, inputs=tuple(sorted(merged, key=_input_key)))
    return expanded, tuple(notes)


def _validate_node_references(ctx: str, nodes: Mapping[str, Node]) -> None:
    known = set(nodes)
    for node in sorted(nodes.values(), key=lambda item: item.name):
        unknown = sorted(set(node.needs) - known)
        if unknown:
            raise BuildError(
                f"{ctx}: nodes.{node.name}.needs names unknown node(s): {', '.join(unknown)}"
            )


def _validate_outputs(ctx: str, root: Path, nodes: Mapping[str, Node]) -> None:
    """V4/V5/V6 plus the two rules the addendum added at layer granularity.

    Every message quotes the output **key** verbatim — ``<set-relative POSIX
    path>[:<layer>]`` — because the operator's next move is to grep the set file
    for that exact string.
    """

    claims: dict[str, list[str]] = defaultdict(list)
    for node in sorted(nodes.values(), key=lambda item: item.name):
        explicit_inputs = {_input_key(item) for item in node.inputs if not item.implicit}
        for output in node.output_refs:
            label = _output_label(output, root)
            if output.path.name == "meta.yaml":
                raise BuildError(
                    f"{ctx}: node '{node.name}': a build may never write meta.yaml - "
                    "the ISSUED gate is the engineer's signature, not a build product"
                )
            if output.layer is not None and output.layer.casefold() in HUMAN_AUTHORED_LAYERS:
                raise BuildError(
                    f"{ctx}: node '{node.name}': a build may never write the "
                    f"human-authored GeoPackage layer '{output.layer}': {label} - it is an "
                    "input a person authors in QGIS, and --force cannot override that"
                )
            claims[label].append(node.name)
            if _output_key(output) in explicit_inputs:
                raise BuildError(
                    f"{ctx}: node '{node.name}': path is both an input and an output: {label}"
                )
    for label, owners in sorted(claims.items()):
        if len(owners) > 1:
            raise BuildError(
                f"{ctx}: output claimed by {len(owners)} nodes "
                f"({', '.join(sorted(owners))}): {label}"
            )


def _validate_graph_acyclic(ctx: str, root: Path, nodes: Mapping[str, Node]) -> None:
    probe = DrawingSet(
        id="probe",
        source=Path(ctx),
        root=root,
        nodes=MappingProxyType(dict(nodes)),
        targets=MappingProxyType({}),
    )
    topological_order(probe)


def _validate_source_inputs(nodes: Mapping[str, Node]) -> None:
    produced = {_output_key(ref) for node in nodes.values() for ref in node.output_refs}
    # A declared directory output produces everything under it, so a file inside
    # one is not a *source* input and need not exist before the build.
    produced_dirs = [
        ref.path
        for node in nodes.values()
        for ref in node.output_refs
        if ref.layer is None and ref.path.suffix == ""
    ]
    for node in sorted(nodes.values(), key=lambda item: item.name):
        for input_ref in node.inputs:
            if _input_key(input_ref) in produced:
                continue
            if input_ref.layer is None and any(
                _is_descendant(input_ref.path, directory) for directory in produced_dirs
            ):
                continue
            if not input_ref.path.exists():
                if input_ref.optional:
                    continue
                raise BuildError(
                    f"node '{node.name}': declared input not found: {input_ref.path}"
                )
            if input_ref.layer is not None:
                _ensure_gpkg_layer(input_ref.path, input_ref.layer, f"node '{node.name}'")


def _validate_external_howtos(ctx: str, root: Path, nodes: Mapping[str, Node]) -> None:
    for node in sorted(nodes.values(), key=lambda item: item.name):
        if node.run != "external":
            continue
        howto = node.args.get("howto")
        if howto is None:
            continue
        path = _resolve_arg_path(howto, root)
        if not path.exists():
            raise BuildError(f"{ctx}: node '{node.name}': args.howto not found: {path}")


# --------------------------------------------------------------------------- #
# graph
# --------------------------------------------------------------------------- #


def dependencies(dset: DrawingSet) -> Mapping[str, frozenset[str]]:
    """node -> the nodes it must follow. Pure function of the declaration."""

    return _dependencies(dset.nodes)


def _dependencies(nodes: Mapping[str, Node]) -> Mapping[str, frozenset[str]]:
    producers: dict[str, str] = {}
    directories: list[tuple[Path, str]] = []
    for node in nodes.values():
        for output in node.output_refs:
            producers[_output_key(output)] = node.name
            # A declared output with no suffix is treated as a directory, so a
            # node reading a file inside another node's out/ dir gets an edge.
            if output.layer is None and output.path.suffix == "":
                directories.append((output.path, node.name))
    deps: dict[str, set[str]] = {name: set(node.needs) for name, node in nodes.items()}
    for node in nodes.values():
        for input_ref in node.inputs:
            producer = producers.get(_input_key(input_ref))
            if producer is not None and producer != node.name:
                deps[node.name].add(producer)
                continue
            if input_ref.layer is not None:
                continue
            for directory, owner in directories:
                if owner != node.name and _is_descendant(input_ref.path, directory):
                    deps[node.name].add(owner)
    return MappingProxyType({name: frozenset(value) for name, value in deps.items()})


def select(dset: DrawingSet, targets: Iterable[str] | None = None) -> tuple[str, ...]:
    """Selected node names: the transitive dependency closure of ``targets``."""

    if targets is None:
        selected = set(dset.nodes)
    else:
        deps = _dependencies(dset.nodes)
        selected = set()
        for name in targets:
            if name in dset.targets:
                roots: tuple[str, ...] = dset.targets[name]
            elif name in dset.nodes:
                roots = (name,)
            else:
                raise BuildError(
                    f"unknown target or node {name!r} (known targets: "
                    f"{', '.join(sorted(dset.targets)) or '-'}; known nodes: "
                    f"{', '.join(sorted(dset.nodes)) or '-'})"
                )
            for start in roots:
                stack = [start]
                while stack:
                    item = stack.pop()
                    if item in selected:
                        continue
                    selected.add(item)
                    stack.extend(sorted(deps[item]))
    if not selected:
        raise BuildError("selection contains 0 node(s)")
    return tuple(sorted(selected))


def topological_order(dset: DrawingSet, selected: Iterable[str] | None = None) -> tuple[str, ...]:
    """Deterministic build order (Kahn, lexicographically-smallest ready node).

    The frontier is re-sorted at every step, so declaration order in the YAML is
    never consulted and reordering the ``nodes:`` block cannot change a log line.
    """

    chosen = set(selected) if selected is not None else set(dset.nodes)
    unknown = sorted(chosen - set(dset.nodes))
    if unknown:
        raise BuildError(f"selected unknown node(s): {', '.join(unknown)}")
    deps = _dependencies(dset.nodes)
    indegree = {name: 0 for name in chosen}
    successors: dict[str, set[str]] = {name: set() for name in chosen}
    for name in chosen:
        for dep in deps[name]:
            if dep in chosen:
                indegree[name] += 1
                successors[dep].add(name)

    ready = deque(sorted(name for name, degree in indegree.items() if degree == 0))
    order: list[str] = []
    while ready:
        node = ready.popleft()
        order.append(node)
        for succ in sorted(successors[node]):
            indegree[succ] -= 1
            if indegree[succ] == 0:
                ready.append(succ)
        ready = deque(sorted(ready))
    if len(order) != len(chosen):
        remaining = {name for name, degree in indegree.items() if degree > 0}
        cycle = _find_cycle(successors, remaining)
        raise BuildError(f"{dset.source.name}: dependency cycle: {' -> '.join(cycle)}")
    return tuple(order)


def _find_cycle(successors: Mapping[str, set[str]], remaining: set[str]) -> list[str]:
    """A deterministic cycle: DFS from the smallest remaining node, sorted edges."""

    start = min(remaining)
    stack: list[str] = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> list[str] | None:
        visiting.add(node)
        stack.append(node)
        for succ in sorted(successors[node]):
            if succ not in remaining:
                continue
            if succ in visiting:
                return _rotate_cycle(stack[stack.index(succ) :] + [succ])
            if succ not in visited:
                found = visit(succ)
                if found:
                    return found
        visiting.discard(node)
        visited.add(node)
        stack.pop()
        return None

    return visit(start) or [start, start]


def _rotate_cycle(cycle: list[str]) -> list[str]:
    body = cycle[:-1]
    index = body.index(min(body))
    rotated = body[index:] + body[:index]
    return [*rotated, rotated[0]]


# --------------------------------------------------------------------------- #
# staleness
# --------------------------------------------------------------------------- #


def plan(
    dset: DrawingSet,
    *,
    targets: Iterable[str] | None = None,
    force: bool = False,
    state: StateStore | None = None,
) -> tuple[NodeStatus, ...]:
    """Per-node staleness in build order. Reads only; writes nothing."""

    selected = select(dset, targets)
    _guard_open_geopackages(dset, selected)
    order = topological_order(dset, selected)
    store = state if state is not None else JsonStateStore(dset.root)
    deps = _dependencies(dset.nodes)
    statuses: dict[str, NodeStatus] = {}
    out: list[NodeStatus] = []
    for name in order:
        status = _node_status(dset, dset.nodes[name], deps[name], statuses, force=force, state=store)
        statuses[name] = status
        out.append(status)
    return tuple(out)


def _node_status(
    dset: DrawingSet,
    node: Node,
    node_deps: frozenset[str],
    upstream: Mapping[str, NodeStatus],
    *,
    force: bool,
    state: StateStore,
) -> NodeStatus:
    external = node.run == "external"

    # A check node has no artifact, so it has nothing to be stale about.
    if node.run in CHECK_RUNNERS:
        return NodeStatus(node=node.name, state="stale", reason="check node - always run")

    def blocked(reason: str) -> NodeStatus:
        howto = node.args.get("howto")
        hint = node.args.get("command_hint")
        detail = f"external: {node.args['reason']}"
        if reason:
            detail = f"{detail} [{reason}]"
        if howto:
            detail = f"{detail}; see {_posix_rel(_resolve_arg_path(howto, dset.root), dset.root)}"
        elif hint:
            detail = f"{detail}; hint: {hint}"
        return NodeStatus(node=node.name, state="blocked", reason=detail)

    missing = next((ref for ref in node.output_refs if not _output_exists(ref)), None)
    if missing is not None:
        label = _output_label(missing, dset.root)
        if external:
            # Correction 3, second half: nothing can produce this but a person.
            return blocked(f"produce {label}")
        return NodeStatus(node=node.name, state="missing-output", reason=f"output missing: {label}")

    for dep in sorted(node_deps):
        dep_status = upstream.get(dep)
        if dep_status is not None and dep_status.state != "current":
            if external:
                return blocked(f"upstream {dep} is {dep_status.state}")
            return NodeStatus(
                node=node.name, state="stale", reason=f"upstream {dep} is {dep_status.state}"
            )

    record = state.read(node.name)

    def adopt(reason: str) -> NodeStatus:
        return NodeStatus(node=node.name, state="current", reason=reason, adopt=True)

    if record is None:
        if external:
            # Correction 3: S4 ("no record means unbuilt") and A18 ("an external
            # output becomes current once the human makes it") deadlock, because an
            # external node can never be run. The output exists, so record its
            # digest and treat it as current — logged, never a silent promotion.
            labels = ", ".join(_output_label(ref, dset.root) for ref in node.output_refs)
            return adopt(f"adopted existing external output into state: {labels}")
        if force:
            return NodeStatus(node=node.name, state="stale", reason="forced")
        return NodeStatus(node=node.name, state="unbuilt", reason="never built")

    if force and not external:
        # --force means "distrust the staleness cache". It cannot mean "pretend a
        # human did something", so an external node keeps its real verdict — which
        # is what makes `build --force` usable for populating state on a set that
        # legitimately contains satisfied external nodes.
        return NodeStatus(node=node.name, state="stale", reason="forced")

    digest, changed, _, node_def = _input_digest(dset, node, old_record=record)
    changed_outputs = [
        _output_label(ref, dset.root)
        for ref in node.output_refs
        if record.outputs.get(_output_label(ref, dset.root)) != _artifact_digest(ref)
    ]

    if external and changed_outputs:
        # An external output is whatever the human last authored — editing it by
        # hand is the entire point of the node, not tampering. So a changed
        # external output is *re-adopted* (logged), and the nodes downstream see a
        # changed input and rebuild. Blocking here instead would deadlock exactly
        # as S4/A18 did: the build can never run the node, so nothing the operator
        # does would ever clear it.
        return adopt(
            "re-adopted hand-authored external output into state: "
            f"{', '.join(changed_outputs)}"
        )

    if digest != record.input_digest:
        if changed and (node_def == record.node_def or not record.node_def):
            reason = f"input changed: {', '.join(changed[:3])}"
        else:
            reason = "input changed: node definition (args/outputs/needs)"
        if external:
            # The human's own inputs moved and their artifact has not been
            # refreshed: only they can redo the step, so say so and stop.
            return blocked(reason)
        return NodeStatus(
            node=node.name, state="stale", reason=reason, changed_inputs=tuple(changed)
        )

    if changed_outputs:
        # A generated file carrying "do NOT hand-edit" was hand-edited. Regenerate
        # and let the diff show what was lost.
        return NodeStatus(
            node=node.name,
            state="stale",
            reason=f"output modified outside the build: {changed_outputs[0]}",
        )

    return NodeStatus(node=node.name, state="current", reason="up to date")


def _input_digest(
    dset: DrawingSet,
    node: Node,
    *,
    old_record: NodeRecord | None = None,
) -> tuple[str, tuple[str, ...], dict[str, tuple[int, int, str]], str]:
    """Return (input_digest, changed labels, fast-path cache, node_def digest).

    The digest is a pure function of the node's defaulted definition plus the
    content of its declared inputs. ``st_mtime`` appears only as a cache key for a
    digest already computed — never as the authority on staleness.
    """

    entries: list[tuple[str, str, str]] = []
    fast: dict[str, tuple[int, int, str]] = {}
    previous_fast = old_record.fast if old_record is not None else {}
    previous_values = old_record.inputs if old_record is not None else {}
    values: dict[str, str] = {}

    for input_ref in node.inputs:
        label = _input_label(input_ref, dset.root)
        mode = input_ref.digest
        if not input_ref.path.exists():
            value = "-" if input_ref.optional else MISSING_VALUE
        elif input_ref.layer is not None:
            value = _gpkg_layer_digest(input_ref.path, input_ref.layer)
        elif mode == "size-mtime":
            stat = input_ref.path.stat()
            value = f"{stat.st_size}:{stat.st_mtime_ns}"
            fast[label] = (stat.st_size, stat.st_mtime_ns, value)
        else:
            stat = input_ref.path.stat()
            cached = previous_fast.get(label)
            if cached is not None and cached[0] == stat.st_size and cached[1] == stat.st_mtime_ns:
                value = cached[2]
            else:
                value = sha256_file(input_ref.path)[0]
            fast[label] = (stat.st_size, stat.st_mtime_ns, value)
        entries.append((label, mode, value))
        values[label] = value

    changed = sorted(
        label
        for label in set(values) | set(previous_values)
        if values.get(label) != previous_values.get(label)
    )
    node_def_digest = hashlib.sha256(
        _canonical_json(_node_definition(node, dset.root)).encode()
    ).hexdigest()
    # The preimage is UTF-8 (str.encode's default), NUL-separated, sorted by label:
    # two implementations must be able to reproduce this byte-for-byte.
    lines = b"".join(
        f"{label}\0{mode}\0{value}\n".encode()
        for label, mode, value in sorted(entries, key=lambda item: item[0])
    )
    digest = hashlib.sha256(node_def_digest.encode() + b"\n" + lines).hexdigest()
    return digest, tuple(changed), fast, node_def_digest


def _record_for_node(dset: DrawingSet, node: Node, *, state: StateStore) -> NodeRecord:
    digest, _, fast, node_def = _input_digest(dset, node, old_record=state.read(node.name))
    inputs = {
        _input_label(ref, dset.root): value
        for ref, value in (
            (ref, _input_value(dset, ref, fast)) for ref in node.inputs
        )
    }
    outputs = {
        _output_label(ref, dset.root): _artifact_digest(ref)
        for ref in sorted(node.output_refs, key=lambda item: _output_label(item, dset.root))
    }
    return NodeRecord(
        node=node.name,
        input_digest=digest,
        outputs=MappingProxyType(dict(sorted(outputs.items()))),
        fast=MappingProxyType(fast),
        tool_version=__version__,
        inputs=MappingProxyType(dict(sorted(inputs.items()))),
        node_def=node_def,
    )


def _input_value(
    dset: DrawingSet, ref: InputRef, fast: Mapping[str, tuple[int, int, str]]
) -> str:
    label = _input_label(ref, dset.root)
    cached = fast.get(label)
    if cached is not None:
        return cached[2]
    if not ref.path.exists():
        return "-" if ref.optional else MISSING_VALUE
    if ref.layer is not None:
        return _gpkg_layer_digest(ref.path, ref.layer)
    return sha256_file(ref.path)[0]


def _artifact_digest(output: OutputRef) -> str:
    """P3's ``emit_digest`` first; a raw SHA-256 build-cache fallback second.

    ``emit_digest`` *raises* for a format it will not vouch for (a PDF, an
    undeclared extension) rather than returning a value a caller could wrongly
    assert byte-identity on. The build still needs *some* stable value to detect
    a hand-edit, so it falls back to the file's SHA-256 — used only as a cache
    key, never presented as a reproducibility claim.
    """

    if output.layer is not None:
        return _gpkg_layer_digest(output.path, output.layer)
    try:
        return emit_digest(output.path)
    except ProvenanceError:
        return sha256_file(output.path)[0]


# --------------------------------------------------------------------------- #
# GeoPackage handling: the container is not the output, the layer is
# --------------------------------------------------------------------------- #


def _open_gpkg_readonly(path: Path) -> sqlite3.Connection:
    """Open a GeoPackage for reading **without leaving a trace**.

    ``mode=ro`` alone is not enough: opening a **WAL-mode** database read-only
    still creates its ``-wal`` and ``-shm`` sidecars (measured on a live
    GeoPackage, which QGIS put in WAL mode). That would be a self-inflicted
    deadlock — the first build digests a layer, the sidecars appear, and every
    later build's pre-flight reads them as "a QGIS session is live" and refuses.

    ``immutable=1`` promises SQLite the file cannot change, so it neither consults
    nor creates a WAL. That promise is exactly what the pre-flight guard has
    already established: it errors out if a sidecar exists, so by the time we open
    the file there is no uncommitted WAL content to miss.
    """

    return sqlite3.connect(f"file:{path.as_posix()}?immutable=1", uri=True)


def _gpkg_layer_digest(path: Path, layer: str) -> str:
    """A deterministic digest of one GeoPackage layer's schema, registry and rows.

    Hashing the whole ``.gpkg`` file would make every layer stale whenever any
    layer changed — and SQLite page layout is not stable across writes anyway.
    """

    _ensure_gpkg_layer(path, layer, str(path))
    quoted = _quote_sql_identifier(layer)
    digest = hashlib.sha256()
    with contextlib.closing(_open_gpkg_readonly(path)) as conn:
        digest.update(b"schema\0")
        for row in conn.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master WHERE tbl_name = ? "
            "ORDER BY type, name, sql",
            (layer,),
        ):
            digest.update(_canonical_json(_jsonable_sqlite_row(row)).encode("utf-8") + b"\n")
        digest.update(b"registry\0")
        for table in ("gpkg_contents", "gpkg_geometry_columns"):
            if not _sqlite_table_exists(conn, table):
                continue
            columns = [
                item[1] for item in conn.execute(f"PRAGMA table_info({_quote_sql_identifier(table)})")
            ]
            col_sql = ", ".join(_quote_sql_identifier(name) for name in sorted(columns))
            for row in conn.execute(
                f"SELECT {col_sql} FROM {_quote_sql_identifier(table)} WHERE table_name = ?",
                (layer,),
            ):
                digest.update(_canonical_json(_jsonable_sqlite_row(row)).encode("utf-8") + b"\n")
        digest.update(b"rows\0")
        columns = [item[1] for item in conn.execute(f"PRAGMA table_info({quoted})")]
        col_sql = ", ".join(_quote_sql_identifier(name) for name in columns)
        for row in conn.execute(f"SELECT {col_sql} FROM {quoted} ORDER BY rowid"):
            digest.update(_canonical_json(_jsonable_sqlite_row(row)).encode("utf-8") + b"\n")
    return digest.hexdigest()


def _ensure_gpkg_layer(path: Path, layer: str, ctx: str) -> None:
    if not path.exists():
        raise BuildError(f"{ctx}: GeoPackage not found: {path}")
    sidecar = _open_gpkg_sidecar(path)
    if sidecar is not None:
        # The single point where a GeoPackage is opened, so the guard belongs here
        # too: no call order can read a file a live session still owns. `plan`'s
        # pre-flight is the early, better-worded version of the same refusal.
        raise BuildError(
            f"{ctx}: {path.name} has an open SQLite WAL sidecar ({sidecar.name}) - a QGIS "
            "session is live. Save the layer and close QGIS, then rebuild."
        )
    try:
        with contextlib.closing(_open_gpkg_readonly(path)) as conn:
            if not _sqlite_table_exists(conn, layer):
                raise BuildError(f"{ctx}: GeoPackage layer not found: {path}:{layer}")
    except sqlite3.DatabaseError as exc:
        raise BuildError(f"{ctx}: cannot read GeoPackage {path}: {exc}") from exc


def _sqlite_table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
            (name,),
        ).fetchone()
        is not None
    )


def _quote_sql_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _jsonable_sqlite_row(row: Sequence[Any]) -> list[Any]:
    out: list[Any] = []
    for value in row:
        if isinstance(value, bytes):
            out.append({"__bytes_sha256__": hashlib.sha256(value).hexdigest(), "bytes": len(value)})
        else:
            out.append(value)
    return out


def _guard_open_geopackages(dset: DrawingSet, selected: Iterable[str]) -> None:
    """Pre-flight: refuse to read or write a GeoPackage a QGIS session still owns.

    OGR does **not** refuse a ``.gpkg`` with live ``-wal``/``-shm`` sidecars, and
    a live project has both, so this has to be the driver's check. Hashing
    a SQLite file mid-transaction records a digest that changes at the next
    checkpoint: phantom staleness from outside the graph.
    """

    for name in sorted(selected):
        node = dset.nodes[name]
        candidates = [
            *(
                (ref.path, ref.layer, "input")
                for ref in node.inputs
                if ref.path.suffix.lower() == ".gpkg"
            ),
            *(
                (ref.path, ref.layer, "output")
                for ref in node.output_refs
                if ref.path.suffix.lower() == ".gpkg"
            ),
        ]
        for path, layer, role in candidates:
            sidecar = _open_gpkg_sidecar(path)
            if sidecar is None:
                continue
            label = _posix_rel(path, dset.root)
            if layer is not None:
                label = f"{label}:{layer}"
            raise BuildError(
                f"node '{node.name}': {label} ({role}) has an open SQLite WAL sidecar "
                f"({sidecar.name}) - a QGIS session is live. Save the layer and close QGIS, "
                "then rebuild."
            )


def _open_gpkg_sidecar(path: Path) -> Path | None:
    for suffix in ("-wal", "-shm"):
        sidecar = path.with_name(path.name + suffix)
        if sidecar.exists():
            return sidecar
    return None


# --------------------------------------------------------------------------- #
# execution
# --------------------------------------------------------------------------- #


def build_set(
    dset: DrawingSet,
    *,
    targets: Iterable[str] | None = None,
    force: bool = False,
    state: StateStore | None = None,
) -> BuildReport:
    """Run the stale nodes in order. Stops at the first failure or block."""

    selected = select(dset, targets)
    store = state if state is not None else JsonStateStore(dset.root)
    statuses = plan(dset, targets=selected, force=force, state=store)

    missing_tools = required_tool_failures(dset, statuses)
    if missing_tools:
        raise BuildError(
            "\n".join(
                [
                    *(
                        f"node '{node}': required tool not found on PATH: {tool}"
                        for node, tool in missing_tools
                    ),
                    "pre-flight failed - nothing was built",
                ]
            )
        )

    order = tuple(status.node for status in statuses)
    results: list[NodeResult] = []
    adopted: list[tuple[str, str]] = []
    for status in statuses:
        node = dset.nodes[status.node]
        if status.state == "current":
            if status.adopt:
                store.write(_record_for_node(dset, node, state=store))
                store.flush()
                for ref in node.output_refs:
                    adopted.append((node.name, _output_label(ref, dset.root)))
            results.append(NodeResult(node=node.name, action="skipped"))
            continue
        if status.state == "blocked":
            results.append(NodeResult(node=node.name, action="blocked", error=status.reason))
            break

        snapshot = _snapshot_outputs(node)
        started = time.perf_counter()
        outcome = _run_node(dset, node)
        duration = time.perf_counter() - started

        if outcome.action == "built" and _outputs_exist(node):
            if node.run not in CHECK_RUNNERS:
                store.write(_record_for_node(dset, node, state=store))
                store.flush()
            results.append(
                replace(
                    outcome,
                    outputs=tuple(ref.path for ref in node.output_refs),
                    duration_s=duration,
                )
            )
            continue

        error = outcome.error
        if outcome.action == "built":
            absent = next(ref for ref in node.output_refs if not _output_exists(ref))
            error = f"declared output not produced: {_output_label(absent, dset.root)}"
        quarantined, quarantine_errors = _quarantine_changed_outputs(node, snapshot)
        if quarantine_errors:
            joined = "\n".join(quarantine_errors)
            error = f"{error}\n{joined}" if error else joined
        results.append(
            replace(
                outcome,
                action="failed",
                error=error,
                duration_s=duration,
                quarantined=quarantined,
            )
        )
        break

    return BuildReport(
        set_id=dset.id,
        order=order,
        results=tuple(results),
        stranded=_stranded_dependents(dset, set(selected), results, store),
        adopted=tuple(adopted),
        # Only over the nodes the build actually reached. A review target the build
        # never got to is already accounted for by the block or failure that stopped
        # it; naming its absent PDF as well would bury the real cause.
        review_failures=review_failures(
            dset, [result.node for result in results if result.action != "blocked"]
        ),
    )


def review_failures(dset: DrawingSet, selected: Iterable[str]) -> tuple[tuple[str, str], ...]:
    """``review: pdf`` targets in ``selected`` with no PDF on disk.

    Correction 2: sheet-ness is declared. Every marked target owes its PDF and the
    build fails naming the target; an unmarked target — an intermediate, a staged
    layer, a GeoJSON, a synthetic test node — owes nothing.
    """

    failures: list[tuple[str, str]] = []
    for name in sorted(selected):
        node = dset.nodes[name]
        for ref in node.review_pdfs:
            if not _output_exists(ref):
                failures.append((name, _output_label(ref, dset.root)))
    return tuple(failures)


def review_targets(dset: DrawingSet, selected: Iterable[str] | None = None) -> tuple[str, ...]:
    """Names of the selected nodes that declare ``review: pdf``, sorted."""

    names = set(dset.nodes) if selected is None else set(selected)
    return tuple(sorted(name for name in names if dset.nodes[name].review == "pdf"))


def required_tool_failures(
    dset: DrawingSet, statuses: Sequence[NodeStatus]
) -> tuple[tuple[str, str], ...]:
    """Tools missing on PATH for the nodes the build intends to run."""

    failures: list[tuple[str, str]] = []
    for status in statuses:
        if status.state == "blocked":
            break
        if status.state == "current":
            continue
        node = dset.nodes[status.node]
        for tool in _required_tools(node):
            if shutil.which(tool) is None:
                failures.append((node.name, tool))
    return tuple(sorted(failures))


def _required_tools(node: Node) -> tuple[str, ...]:
    if node.run == "command":
        return (str(node.args["tool"]),)
    if node.run == "script":
        return tuple(str(tool) for tool in node.args["tools"])
    return ()


def size_mtime_inputs(dset: DrawingSet) -> tuple[str, ...]:
    """Inputs explicitly tracked with the weaker size+mtime mode."""

    labels = {
        _input_label(ref, dset.root)
        for node in dset.nodes.values()
        for ref in node.inputs
        if ref.digest == "size-mtime"
    }
    return tuple(sorted(labels))


def state_version_notes(
    dset: DrawingSet, statuses: Sequence[NodeStatus], state: StateStore | None = None
) -> tuple[str, ...]:
    """A tool-version mismatch is recorded and noted — never a forced rebuild."""

    store = state if state is not None else JsonStateStore(dset.root)
    notes: list[str] = []
    seen: set[str] = set()
    for status in statuses:
        record = store.read(status.node)
        if record is None or record.tool_version == __version__ or record.tool_version in seen:
            continue
        seen.add(record.tool_version)
        notes.append(
            f"state written by technical_drawings_for_agents {record.tool_version}, running {__version__} - "
            "pass --force to rebuild"
        )
    return tuple(notes)


def node_definition_digest(node: Node, root: Path | None = None) -> str:
    """Digest of the defaulted node definition — P3's 'resolved graph node' identity."""

    base = root if root is not None else _common_node_root(node)
    return hashlib.sha256(_canonical_json(_node_definition(node, base)).encode("utf-8")).hexdigest()


def _run_node(dset: DrawingSet, node: Node) -> NodeResult:
    try:
        if node.run == "script":
            return _run_subprocess(dset, node, [*node.args["interpreter"], *_script_argv(dset, node)])
        if node.run == "command":
            return _run_subprocess(dset, node, list(node.args["argv"]))
        return _run_verb(dset, node)
    except Exception as exc:  # noqa: BLE001 - a node's failure is data, not a traceback
        return NodeResult(node=node.name, action="failed", error=str(exc))


def _script_argv(dset: DrawingSet, node: Node) -> list[str]:
    return [str(_resolve_arg_path(node.args["path"], dset.root)), *node.args["argv"]]


def _run_subprocess(dset: DrawingSet, node: Node, argv: list[str]) -> NodeResult:
    cwd = _resolve_arg_path(node.args["cwd"], dset.root)
    try:
        completed = subprocess.run(
            argv,
            cwd=cwd,
            env=os.environ.copy(),
            capture_output=True,
            text=True,
            timeout=node.timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return NodeResult(
            node=node.name,
            action="failed",
            stdout=exc.stdout if isinstance(exc.stdout, str) else "",
            stderr=exc.stderr if isinstance(exc.stderr, str) else "",
            error=f"timeout after {node.timeout_s}s",
        )
    except OSError as exc:
        return NodeResult(node=node.name, action="failed", error=f"cannot run {argv[0]}: {exc}")
    action: Action = "built" if completed.returncode == 0 else "failed"
    return NodeResult(
        node=node.name,
        action=action,
        stdout=completed.stdout,
        stderr=completed.stderr,
        error=None if action == "built" else f"exit {completed.returncode}",
        exit_code=completed.returncode,
    )


def _run_verb(dset: DrawingSet, node: Node) -> NodeResult:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = _dispatch_verb(dset, node)
    action: Action = "built" if code == 0 else "failed"
    return NodeResult(
        node=node.name,
        action=action,
        stdout=out.getvalue(),
        stderr=err.getvalue(),
        error=None if action == "built" else f"exit {code}",
        exit_code=code,
    )


def _dispatch_verb(dset: DrawingSet, node: Node) -> int:
    """Call the verb's module-level entry point in-process.

    Never ``cli.main()``: it configures the root logger process-globally and its
    subparsers are ``required=True``, so re-entering it would make one node's
    behaviour depend on another's invocation order.
    """

    root = dset.root
    if node.run == "verb:render":
        from .render import render_source

        render_source(_resolve_arg_path(node.args["source"], root), _resolve_arg_path(node.args["out"], root))
        return 0
    if node.run == "verb:validate":
        from .validate import validate_target

        result = validate_target(_resolve_arg_path(node.args["target"], root))
        if result.ok:
            print(f"OK: {result.target} passed validation ({len(result.checked)} checks).")
            for warning in result.warnings:
                print(f"warn: {warning}")
            return 0
        print(f"FAIL: {result.target} has {len(result.problems)} problem(s):", file=sys.stderr)
        for problem in result.problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    if node.run == "verb:bfd":
        from . import bfd

        bfd.build(
            _resolve_arg_path(node.args["data"], root),
            _resolve_arg_path(node.args["out"], root),
            view=node.args["view"],
        )
        return 0
    if node.run == "verb:pid":
        from . import pid

        pid.build(
            _resolve_arg_path(node.args["data"], root),
            _resolve_arg_path(node.args["out"], root),
        )
        return 0
    if node.run == "verb:component":
        from .components.cli import run as run_component

        return run_component(_verb_namespace(_COMPONENT_CLI_DEFAULTS, {
            "spec": str(_resolve_arg_path(node.args["spec"], root)),
            "view": node.args["view"],
            "emit": node.args["emit"],
            "out": str(_resolve_arg_path(node.args["out"], root)),
            "origin": node.args["origin"],
            "place": (
                str(_resolve_arg_path(node.args["place"], root))
                if node.args["place"] is not None
                else None
            ),
            "rot": node.args["rot"],
        }))
    if node.run == "verb:layout":
        from .components.cli import run_layout

        return run_layout(_verb_namespace(_LAYOUT_CLI_DEFAULTS, {
            "config": str(_resolve_arg_path(node.args["config"], root)),
            "view": node.args["view"],
            "from_geojson": (
                str(_resolve_arg_path(node.args["from_geojson"], root))
                if node.args["from_geojson"] is not None
                else None
            ),
            "emit": node.args["emit"],
            "out": (
                str(_resolve_arg_path(node.args["out"], root))
                if node.args["out"] is not None
                else None
            ),
            "emit_register": (
                str(_resolve_arg_path(node.args["emit_register"], root))
                if node.args["emit_register"] is not None
                else None
            ),
            "check_only": node.args["check_only"],
            "warn_only": node.args["warn_only"],
            "no_snap": node.args["no_snap"],
        }))
    raise BuildError(f"unhandled runner {node.run!r}")


def _verb_namespace(defaults: Mapping[str, Any], values: Mapping[str, Any]):
    """Namespace for a verb's own ``run*`` function: P2's args over CLI defaults.

    P2 deliberately does not re-expose every CLI flag as a node arg — the flags a
    sibling PR owns (``--layers``, ``--plot-scale``, ``--pdf``, ``--manifest``)
    keep their CLI defaults here. ``tests/test_build_graph.py`` asserts the
    parser's dests are all covered, so a new sibling flag fails a test instead of
    raising ``AttributeError`` half-way through somebody's build.
    """

    from argparse import Namespace

    return Namespace(**{**defaults, **values})


def _snapshot_outputs(node: Node) -> dict[Path, tuple[int, str]]:
    snapshot: dict[Path, tuple[int, str]] = {}
    for ref in node.output_refs:
        if ref.path.is_file():
            digest, size = sha256_file(ref.path)
            snapshot[ref.path] = (size, digest)
    return snapshot


def _quarantine_changed_outputs(
    node: Node, snapshot: Mapping[Path, tuple[int, str]]
) -> tuple[tuple[tuple[Path, Path], ...], tuple[str, ...]]:
    """Move any output the failed run touched aside to ``<path>.partial``.

    Quarantine rather than copy-and-restore: a 5.7 MB SVG (or a 484 MB raster)
    must not be duplicated to make a failure safe, and ``os.replace`` is atomic.
    """

    quarantined: list[tuple[Path, Path]] = []
    errors: list[str] = []
    seen: set[Path] = set()
    for ref in sorted(node.output_refs, key=lambda item: item.path.as_posix()):
        path = ref.path
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        digest, size = sha256_file(path)
        if snapshot.get(path) == (size, digest):
            continue
        partial = path.with_name(path.name + ".partial")
        try:
            os.replace(path, partial)
            quarantined.append((path, partial))
        except OSError as exc:
            errors.append(f"quarantine failed: {path} -> {partial}: {exc}")
    return tuple(quarantined), tuple(errors)


def _outputs_exist(node: Node) -> bool:
    return all(_output_exists(ref) for ref in node.output_refs)


def _output_exists(ref: OutputRef) -> bool:
    if not ref.path.exists():
        return False
    if ref.layer is None:
        return True
    try:
        _ensure_gpkg_layer(ref.path, ref.layer, str(ref.path))
    except BuildError:
        return False
    return True


def _stranded_dependents(
    dset: DrawingSet,
    selected: set[str],
    results: Sequence[NodeResult],
    state: StateStore,
) -> tuple[str, ...]:
    """Unselected nodes a partial build just made stale. A silent gap otherwise."""

    built = {result.node for result in results if result.action == "built"}
    if not built:
        return ()
    deps = _dependencies(dset.nodes)
    candidates = sorted(
        name for name, node_deps in deps.items() if name not in selected and (node_deps & built)
    )
    if not candidates:
        return ()
    try:
        statuses = {status.node: status for status in plan(dset, state=state)}
    except BuildError:
        return tuple(candidates)
    return tuple(
        name
        for name in candidates
        if name in statuses and statuses[name].state != "current"
    )


# --------------------------------------------------------------------------- #
# keys, labels, small helpers
# --------------------------------------------------------------------------- #


def _parse_artifact(value: Any, root: Path, ctx: str) -> tuple[Path, str | None]:
    """Parse ``path`` or ``path.gpkg:layer`` into (absolute path, layer or None)."""

    if not isinstance(value, str) or not value.strip():
        raise BuildError(f"{ctx} must be a non-empty path string")
    raw = value.strip()
    layer: str | None = None
    text = raw
    if ":" in raw:
        before, after = raw.rsplit(":", 1)
        if before.lower().endswith(".gpkg"):
            if not after.strip():
                raise BuildError(f"{ctx}: GeoPackage layer name must be non-empty")
            text, layer = before, after.strip()
    path = Path(text)
    if not path.is_absolute():
        path = root / path
    return path.resolve(), layer


def _input_key(ref: InputRef) -> str:
    return _artifact_key(ref.path, ref.layer)


def _output_key(ref: OutputRef) -> str:
    return _artifact_key(ref.path, ref.layer)


def _artifact_key(path: Path, layer: str | None) -> str:
    """Identity for edge-building: absolute, so two spellings of one file collide."""

    resolved = Path(path).resolve().as_posix()
    return resolved if layer is None else f"{resolved}:{layer}"


def _input_label(ref: InputRef, root: Path) -> str:
    return _artifact_label(ref.path, ref.layer, root)


def _output_label(ref: OutputRef, root: Path) -> str:
    return _artifact_label(ref.path, ref.layer, root)


def _artifact_label(path: Path, layer: str | None, root: Path) -> str:
    """The set-relative POSIX key an operator reads, greps and finds in state.

    Correction 1: never absolute (it would pin state and every message to one
    machine, the mistake P3 corrected in the digest), never ``.`` (that was the
    bug: a root derived from the paths themselves collapses when the set has one
    output). ``root`` is always the set's declared root, which defaults to the
    directory holding the drawing-set file — the directory every relative path in
    the file is written against.
    """

    label = _posix_rel(path, root)
    return label if layer is None else f"{label}:{layer}"


def _posix_rel(path: Path, root: Path) -> str:
    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        # Outside the set root: an absolute POSIX path is the honest label. It is
        # machine-specific, which is exactly why a set should keep its inputs in
        # (or under) its root.
        return resolved.as_posix()


def _node_definition(node: Node, root: Path) -> dict[str, Any]:
    return {
        "runner": node.run,
        "args": _jsonable_args(node.args),
        "outputs": sorted(_output_label(ref, root) for ref in node.output_refs),
        "needs": sorted(node.needs),
        "review": node.review,
    }


def _jsonable_args(args: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: list(args[key]) if isinstance(args[key], tuple) else args[key]
        for key in sorted(args)
    }


def _canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _read_yaml(path: Path) -> Any:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise BuildError(f"cannot read {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise BuildError(f"invalid YAML in {path}: {exc}") from exc


def _reject_unknown_keys(raw: Mapping[Any, Any], accepted: frozenset[str], ctx: str) -> None:
    for key in raw:
        if key not in accepted:
            raise BuildError(
                f"{ctx}: unknown key {key!r} (accepted: {', '.join(sorted(accepted))})"
            )


def _name(value: Any, ctx: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BuildError(f"{ctx} must be a non-empty string")
    if not _NAME_PATTERN.fullmatch(value):
        raise BuildError(f"{ctx} must match {_NAME_PATTERN.pattern}")
    return value


def _required_string(value: Any, ctx: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BuildError(f"{ctx} must be a non-empty string")
    return value


def _optional_string(value: Any, ctx: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise BuildError(f"{ctx} must be a string")
    return value


def _string_mapping(value: Any, ctx: str) -> dict[str, str]:
    if not isinstance(value, dict):
        raise BuildError(f"{ctx} must be a mapping")
    out: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not isinstance(item, str):
            raise BuildError(f"{ctx} must map strings to strings")
        out[key] = item
    return out


def _arg_path(
    raw: Mapping[str, Any],
    key: str,
    ctx: str,
    node: str,
    root: Path,
    *,
    required: bool = False,
) -> str:
    if key not in raw or raw[key] is None:
        if required:
            raise BuildError(f"{ctx}: nodes.{node}.args.{key} is required")
        return ""
    value = raw[key]
    if not isinstance(value, str) or not value.strip():
        raise BuildError(f"{ctx}: nodes.{node}.args.{key} must be a non-empty path string")
    path = Path(value)
    if not path.is_absolute():
        path = root / path
    return _posix_rel(path, root)


def _resolve_arg_path(value: Any, root: Path) -> Path:
    if value is None:
        raise BuildError("internal error: cannot resolve None path")
    path = Path(str(value))
    if not path.is_absolute():
        path = root / path
    return path.resolve()


def _string_default(raw: Mapping[str, Any], key: str, default: str, ctx: str, node: str) -> str:
    value = raw.get(key, default)
    if not isinstance(value, str) or not value.strip():
        raise BuildError(f"{ctx}: nodes.{node}.args.{key} must be a non-empty string")
    return value


def _bool_default(raw: Mapping[str, Any], key: str, default: bool, ctx: str, node: str) -> bool:
    value = raw.get(key, default)
    if not isinstance(value, bool):
        raise BuildError(f"{ctx}: nodes.{node}.args.{key} must be a bool")
    return value


def _default_out(path_text: str) -> str:
    return (Path(path_text).parent / "out").as_posix()


def _common_node_root(node: Node) -> Path:
    paths = [*(ref.path for ref in node.inputs), *(ref.path for ref in node.output_refs)]
    if not paths:
        return Path.cwd()
    return Path(os.path.commonpath([str(path.parent) for path in paths]))


def _is_descendant(path: Path, parent: Path) -> bool:
    resolved, base = Path(path).resolve(), Path(parent).resolve()
    if resolved == base:
        return False
    try:
        resolved.relative_to(base)
    except ValueError:
        return False
    return True
