"""Behaviour of ``technical_drawings_for_agents build`` — the acceptance tests, named for the claim.

Every test uses a synthetic set under ``tmp_path``. The two that read the repo's
own example drawings copy them first and never assert byte-equality against the
committed ``drawings/example/*/out/`` artifacts (issue #101).
"""

from __future__ import annotations

import os
import shutil
import sqlite3

import pytest
import yaml

from technical_drawings_for_agents.build import (
    BuildError,
    JsonStateStore,
    build_set,
    load_drawing_set,
    plan,
)

from .build_fixtures import (
    PID_EXAMPLE,
    SIMPLE,
    abcd_set,
    copy_script,
    file_record,
    make_gpkg,
    run,
    script_node,
    set_body,
    tree_snapshot,
    write_set,
    write_yaml,
)

#: The committed example set: this repo's own `--check` gate (the vault is a
#: separate local repo CI cannot see, so it is the only set CI can gate).
REPO_EXAMPLE = SIMPLE.parent


# --------------------------------------------------------------------------- #
# staleness and incremental rebuilds
# --------------------------------------------------------------------------- #


def test_touching_one_input_rebuilds_only_affected_targets(tmp_path, capsys):
    set_file = abcd_set(tmp_path)
    assert run(capsys, str(set_file))[0] == 0
    before = {name: file_record(tmp_path / name) for name in ("b.out", "d.out")}

    (tmp_path / "a.in").write_text("A-changed", encoding="utf-8")
    code, out, err = run(capsys, str(set_file))

    assert code == 0, err
    assert "BUILD  a" in out
    assert "SKIP  b" in out
    assert "build: 2 built, 2 skipped, 0 blocked, 0 failed" in out
    # Untouched chains keep their bytes AND their mtimes: nothing re-wrote them.
    assert before == {name: file_record(tmp_path / name) for name in ("b.out", "d.out")}

    report = build_set(load_drawing_set(set_file))
    assert report.built == ()


def test_rebuild_is_a_noop_when_nothing_changed(tmp_path, capsys):
    set_file = abcd_set(tmp_path)
    assert run(capsys, str(set_file))[0] == 0
    before = tree_snapshot(tmp_path)

    code, out, err = run(capsys, str(set_file))

    assert code == 0, err
    assert out.count("SKIP") == 4
    assert "build: 0 built, 4 skipped, 0 blocked, 0 failed" in out
    assert tree_snapshot(tmp_path) == before


def test_rewriting_an_input_with_identical_bytes_does_not_rebuild(tmp_path, capsys):
    set_file = abcd_set(tmp_path)
    assert run(capsys, str(set_file))[0] == 0

    (tmp_path / "a.in").write_text("A", encoding="utf-8")  # same bytes, new mtime
    code, out, err = run(capsys, str(set_file))

    assert code == 0, err
    assert "build: 0 built, 4 skipped, 0 blocked, 0 failed" in out


def test_changing_a_node_arg_rebuilds_that_node(tmp_path, capsys):
    """The ``node_def`` term of the digest: no input file is touched.

    The base spec pinned this on ``verb:bfd``'s ``view``, but ``bfd.build()`` names
    its outputs *after* the view, so the change alters which file is produced rather
    than the content of the declared target — and ``bfd`` needs Graphviz, which CI
    does not have. A ``script`` node's ``argv`` changes what the node does while its
    declared output path stays put, and needs nothing but an interpreter.
    """

    copy_script(tmp_path)
    (tmp_path / "a.in").write_text("A", encoding="utf-8")
    (tmp_path / "b.in").write_text("B", encoding="utf-8")
    set_file = write_set(
        tmp_path,
        {
            "a": {
                "run": "script",
                "inputs": ["a.in", "b.in"],
                "outputs": ["a.out"],
                "args": {"path": "copy.py", "argv": ["a.in", "a.out"]},
            }
        },
    )
    assert run(capsys, str(set_file))[0] == 0
    assert (tmp_path / "a.out").read_text(encoding="utf-8") == "A"
    before = {name: file_record(tmp_path / name) for name in ("a.in", "b.in")}

    raw = yaml.safe_load(set_file.read_text(encoding="utf-8"))
    raw["nodes"]["a"]["args"]["argv"] = ["b.in", "a.out"]
    write_yaml(set_file, raw)

    code, out, err = run(capsys, str(set_file))

    assert code == 0, err
    assert "BUILD  a" in out
    assert "input changed: node definition (args/outputs/needs)" in out
    assert (tmp_path / "a.out").read_text(encoding="utf-8") == "B"
    # Not one input file was touched: only the declaration changed.
    assert before == {name: file_record(tmp_path / name) for name in ("a.in", "b.in")}


@pytest.mark.skipif(not shutil.which("dot"), reason="graphviz not installed")
def test_changing_a_verb_arg_rebuilds_that_node(tmp_path, capsys):
    """The same claim through a real verb: ``view: all`` -> ``profile``.

    Both views write ``*_profile.svg``, so the declared target is arg-invariant and
    the rebuild is observable. Graphviz-gated, because ``bfd`` shells out to ``dot``.
    """

    write_yaml(
        tmp_path / "bfd.yaml",
        {
            "meta": {"number": "TST-BFD-001", "title": "BFD", "status": "DRAFT"},
            "nodes": [
                {"id": "a", "label": "A", "type": "source", "elev": 1, "chainage": 0},
                {"id": "b", "label": "B", "type": "sink", "elev": 2, "chainage": 1},
            ],
            "edges": [{"from": "a", "to": "b"}],
        },
    )
    set_file = write_set(
        tmp_path,
        {
            "bfd": {
                "run": "verb:bfd",
                "inputs": ["bfd.yaml"],
                "outputs": ["out/TST-BFD-001_profile.svg"],
                "args": {"data": "bfd.yaml", "out": "out", "view": "all"},
            }
        },
    )
    assert run(capsys, str(set_file))[0] == 0

    raw = yaml.safe_load(set_file.read_text(encoding="utf-8"))
    raw["nodes"]["bfd"]["args"]["view"] = "profile"
    write_yaml(set_file, raw)

    code, out, err = run(capsys, str(set_file))

    assert code == 0, err
    assert "BUILD  bfd" in out
    assert "input changed: node definition" in out


def test_a_hand_edited_output_is_stale_not_trusted(tmp_path, capsys):
    set_file = abcd_set(tmp_path)
    assert run(capsys, str(set_file))[0] == 0

    (tmp_path / "a.out").write_text("hand-edited", encoding="utf-8")
    code, out, err = run(capsys, str(set_file))

    assert code == 0, err
    assert "output modified outside the build: a.out" in out
    assert (tmp_path / "a.out").read_text(encoding="utf-8") == "A"


def test_size_mtime_digest_mode_is_reported_and_is_weaker(tmp_path, capsys):
    copy_script(tmp_path)
    (tmp_path / "a.in").write_text("A", encoding="utf-8")
    set_file = write_yaml(
        tmp_path / "drawing-set.yaml",
        set_body(
            {
                "a": {
                    "run": "script",
                    "inputs": ["@big"],
                    "outputs": ["a.out"],
                    "args": {"path": "copy.py", "argv": ["a.in", "a.out"]},
                }
            },
            inputs={"big": {"path": "a.in", "digest": "size-mtime"}},
        ),
    )

    code, out, err = run(capsys, str(set_file))
    assert code == 0, err
    assert "note: 1 input(s) tracked by size+mtime, not content: a.in" in out

    # Under size-mtime, an mtime bump alone IS stale — the documented weaker
    # guarantee, in contrast with content mode (see the identical-bytes test).
    stat = (tmp_path / "a.in").stat()
    os.utime(tmp_path / "a.in", ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    code, out, err = run(capsys, str(set_file))
    assert code == 0, err
    assert "BUILD  a" in out


def test_tool_version_change_notes_but_does_not_rebuild(tmp_path, capsys):
    set_file = abcd_set(tmp_path)
    assert run(capsys, str(set_file))[0] == 0
    state = tmp_path / ".technical_drawings_for_agents" / "build-state.json"
    state.write_text(state.read_text(encoding="utf-8").replace('"0.1.0"', '"0.0.1"'), "utf-8")

    code, out, err = run(capsys, str(set_file))

    assert code == 0, err
    assert "state written by technical_drawings_for_agents 0.0.1, running" in out
    assert "build: 0 built, 4 skipped" in out


# --------------------------------------------------------------------------- #
# --check, --dry-run, --force, --target
# --------------------------------------------------------------------------- #


def test_check_exits_3_and_names_the_stale_target(tmp_path, capsys):
    set_file = abcd_set(tmp_path)
    assert run(capsys, str(set_file))[0] == 0
    (tmp_path / "a.in").write_text("A-changed", encoding="utf-8")
    before = tree_snapshot(tmp_path)

    code, out, err = run(capsys, str(set_file), "--check")

    assert code == 3
    assert "CHECK  a" in out
    assert "STALE: input changed: a.in" in out
    assert "check: NOT CURRENT" in err
    assert tree_snapshot(tmp_path) == before


def test_check_exits_0_when_current_and_writes_nothing(tmp_path, capsys):
    set_file = abcd_set(tmp_path)
    assert run(capsys, str(set_file))[0] == 0
    before = tree_snapshot(tmp_path)

    code, out, err = run(capsys, str(set_file), "--check")

    assert code == 0, err
    assert "check: current (4 node(s))" in out
    assert tree_snapshot(tmp_path) == before


def test_check_creates_no_state_directory_on_a_never_built_set(tmp_path, capsys):
    set_file = abcd_set(tmp_path)

    code, out, _err = run(capsys, str(set_file), "--check")

    assert code == 3
    # S3 precedes S4: on a clean tree the outputs are missing, which is the more
    # specific and more useful answer than "never built".
    assert "MISSING: output missing: a.out" in out
    assert not (tmp_path / ".technical_drawings_for_agents").exists()


def test_unbuilt_is_reported_when_the_outputs_are_present_but_unrecorded(tmp_path, capsys):
    copy_script(tmp_path)
    (tmp_path / "a.in").write_text("A", encoding="utf-8")
    (tmp_path / "a.out").write_text("stale leftover", encoding="utf-8")
    set_file = write_set(tmp_path, {"a": script_node("a", "a.in", "a.out")})

    code, out, _err = run(capsys, str(set_file), "--check")

    assert code == 3
    assert "STALE: never built" in out


def test_check_reports_a_missing_output_as_not_current(tmp_path, capsys):
    set_file = abcd_set(tmp_path)
    assert run(capsys, str(set_file))[0] == 0
    (tmp_path / "c.out").unlink()

    code, out, _err = run(capsys, str(set_file), "--check")

    assert code == 3
    assert "MISSING: output missing: c.out" in out


def test_check_exits_3_when_a_required_tool_is_absent(tmp_path, capsys):
    (tmp_path / "a.in").write_text("A", encoding="utf-8")
    set_file = write_set(
        tmp_path,
        {
            "y": {
                "run": "command",
                "inputs": ["a.in"],
                "outputs": ["y.out"],
                "args": {
                    "argv": ["definitely-not-a-real-binary-9f3a"],
                    "tool": "definitely-not-a-real-binary-9f3a",
                },
            }
        },
    )

    code, out, err = run(capsys, str(set_file), "--check")

    assert code == 3
    assert "CHECK  y" in out
    assert "MISSING TOOL: definitely-not-a-real-binary-9f3a" in out
    assert "1 missing tool(s)" in err


def test_dry_run_writes_nothing_and_exits_zero_when_stale(tmp_path, capsys):
    set_file = abcd_set(tmp_path)
    assert run(capsys, str(set_file))[0] == 0
    (tmp_path / "a.in").write_text("A-changed", encoding="utf-8")
    before = tree_snapshot(tmp_path)

    code, out, err = run(capsys, str(set_file), "--dry-run")

    assert code == 0, err
    assert out.index("BUILD  a") < out.index("BUILD  c")
    assert tree_snapshot(tmp_path) == before


def test_check_and_dry_run_and_force_are_mutually_exclusive(tmp_path, capsys):
    set_file = abcd_set(tmp_path)

    code, _out, err = run(capsys, str(set_file), "--check", "--force")
    assert code == 2
    assert "--check and --force are mutually exclusive" in err

    code, _out, err = run(capsys, str(set_file), "--check", "--dry-run")
    assert code == 2
    assert "--check and --dry-run are mutually exclusive" in err


def test_force_rebuilds_everything_selected(tmp_path, capsys):
    set_file = abcd_set(tmp_path)
    assert run(capsys, str(set_file))[0] == 0

    report = build_set(load_drawing_set(set_file), force=True)

    assert report.built == ("a", "b", "c", "d")
    assert report.skipped == ()


def test_target_selects_dependencies_only_and_names_stranded_dependents(tmp_path, capsys):
    set_file = abcd_set(tmp_path)
    assert run(capsys, str(set_file))[0] == 0
    before = file_record(tmp_path / "c.out")
    (tmp_path / "a.in").write_text("A-changed", encoding="utf-8")

    code, out, err = run(capsys, str(set_file), "--target", "a")

    assert code == 0, err
    assert "OK  a" in out
    assert "note: 1 node(s) became stale and were not selected: c" in out
    assert file_record(tmp_path / "c.out") == before


def test_target_accepts_a_target_label_and_pulls_in_dependencies(tmp_path, capsys):
    copy_script(tmp_path)
    (tmp_path / "a.in").write_text("A", encoding="utf-8")
    set_file = write_yaml(
        tmp_path / "drawing-set.yaml",
        set_body(
            {
                "a": script_node("a", "a.in", "a.out"),
                "c": script_node("c", "a.out", "c.out"),
                "z": script_node("z", "a.in", "z.out"),
            },
            targets={"chain": ["c"]},
        ),
    )

    code, out, err = run(capsys, str(set_file), "--target", "chain")

    assert code == 0, err
    assert "2 selected" in out
    assert not (tmp_path / "z.out").exists()


def test_unknown_target_and_empty_selection_are_errors(tmp_path, capsys):
    set_file = abcd_set(tmp_path)

    code, _out, err = run(capsys, str(set_file), "--target", "nope")
    assert code == 2
    assert "known targets: -" in err
    assert "known nodes: a, b, c, d" in err

    bad = write_yaml(
        tmp_path / "empty-target.yaml",
        set_body({"a": script_node("a", "a.in", "a.out")}, targets={"nothing": []}),
    )
    code, _out, err = run(capsys, str(bad))
    assert code == 2
    assert "targets.nothing must be a non-empty list" in err


# --------------------------------------------------------------------------- #
# failure, pre-flight and the half-written-output guarantee
# --------------------------------------------------------------------------- #


def test_build_exits_2_and_builds_nothing_when_a_tool_is_absent(tmp_path, capsys):
    copy_script(tmp_path)
    (tmp_path / "a.in").write_text("A", encoding="utf-8")
    set_file = write_set(
        tmp_path,
        {
            "x": script_node("x", "a.in", "x.out"),
            "y": {
                "run": "command",
                "inputs": ["a.in"],
                "outputs": ["y.out"],
                "args": {
                    "argv": ["definitely-not-a-real-binary-9f3a"],
                    "tool": "definitely-not-a-real-binary-9f3a",
                },
            },
        },
    )

    code, _out, err = run(capsys, str(set_file))

    assert code == 2
    assert "required tool not found on PATH: definitely-not-a-real-binary-9f3a" in err
    assert "pre-flight failed - nothing was built" in err
    # 'x' sorts first and has nothing missing: pre-flight still precedes it.
    assert not (tmp_path / "x.out").exists()


def test_a_missing_source_input_is_an_error_and_never_a_partial_build(tmp_path, capsys):
    set_file = abcd_set(tmp_path, missing_b=True)

    code, _out, err = run(capsys, str(set_file))

    assert code == 2
    assert "node 'b': declared input not found:" in err
    assert "b.in" in err
    assert not any(tmp_path.glob("*.out"))


def test_an_input_that_is_another_nodes_output_need_not_exist_yet(tmp_path, capsys):
    set_file = abcd_set(tmp_path)

    code, out, err = run(capsys, str(set_file))

    assert code == 0, err
    assert "build: 4 built, 0 skipped, 0 blocked, 0 failed" in out


def test_a_failing_node_leaves_no_half_written_output(tmp_path, capsys):
    (tmp_path / "w.in").write_text("in", encoding="utf-8")
    good = "import pathlib\npathlib.Path('w.out').write_text('GOOD', encoding='utf-8')\n"
    (tmp_path / "gen.py").write_text(good, encoding="utf-8")
    set_file = write_set(
        tmp_path,
        {
            "w": {
                "run": "script",
                "inputs": ["w.in"],
                "outputs": ["w.out"],
                "args": {"path": "gen.py"},
            }
        },
    )
    assert run(capsys, str(set_file))[0] == 0
    assert (tmp_path / "w.out").read_text(encoding="utf-8") == "GOOD"

    partial = (
        "import pathlib\nimport sys\n"
        "pathlib.Path('w.out').write_text('PARTIAL', encoding='utf-8')\nsys.exit(3)\n"
    )
    (tmp_path / "gen.py").write_text(partial, encoding="utf-8")

    code, _out, err = run(capsys, str(set_file))

    assert code == 1
    assert "FAIL  w  exit 3" in err
    assert "quarantined: w.out -> w.out.partial" in err
    assert not (tmp_path / "w.out").exists()
    assert (tmp_path / "w.out.partial").read_text(encoding="utf-8") == "PARTIAL"
    # No state record was written for the failed run.
    assert run(capsys, str(set_file), "--check")[0] == 3


def test_a_failing_node_that_writes_nothing_leaves_the_old_output_untouched(tmp_path, capsys):
    (tmp_path / "w.in").write_text("in", encoding="utf-8")
    (tmp_path / "gen.py").write_text(
        "import pathlib\npathlib.Path('w.out').write_text('GOOD', encoding='utf-8')\n", "utf-8"
    )
    set_file = write_set(
        tmp_path,
        {
            "w": {
                "run": "script",
                "inputs": ["w.in"],
                "outputs": ["w.out"],
                "args": {"path": "gen.py"},
            }
        },
    )
    assert run(capsys, str(set_file))[0] == 0
    (tmp_path / "gen.py").write_text("import sys\nsys.exit(4)\n", encoding="utf-8")

    code, _out, err = run(capsys, str(set_file))

    assert code == 1
    assert "exit 4" in err
    assert (tmp_path / "w.out").read_text(encoding="utf-8") == "GOOD"
    assert not (tmp_path / "w.out.partial").exists()


def test_a_zero_exit_with_a_missing_declared_output_is_a_failure(tmp_path, capsys):
    (tmp_path / "w.in").write_text("in", encoding="utf-8")
    (tmp_path / "gen.py").write_text("print('did nothing')\n", encoding="utf-8")
    set_file = write_set(
        tmp_path,
        {
            "w": {
                "run": "script",
                "inputs": ["w.in"],
                "outputs": ["w.out"],
                "args": {"path": "gen.py"},
            }
        },
    )

    code, _out, err = run(capsys, str(set_file))

    assert code == 1
    assert "declared output not produced: w.out" in err


def test_a_failing_node_stops_the_build(tmp_path, capsys):
    (tmp_path / "w.in").write_text("in", encoding="utf-8")
    (tmp_path / "gen.py").write_text("import sys\nsys.exit(5)\n", encoding="utf-8")
    copy_script(tmp_path)
    set_file = write_set(
        tmp_path,
        {
            "w": {
                "run": "script",
                "inputs": ["w.in"],
                "outputs": ["w.out"],
                "args": {"path": "gen.py"},
            },
            "z": script_node("z", "w.out", "z.out"),
            "zz": script_node("zz", "w.in", "zz.out"),
        },
    )

    code, _out, err = run(capsys, str(set_file))

    assert code == 1
    assert "error: node 'w' failed; 0 further node(s) run" in err
    assert not (tmp_path / "z.out").exists()
    assert not (tmp_path / "zz.out").exists()


def test_a_timeout_is_a_failure_that_names_the_limit(tmp_path, capsys):
    (tmp_path / "w.in").write_text("in", encoding="utf-8")
    (tmp_path / "gen.py").write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
    set_file = write_set(
        tmp_path,
        {
            "w": {
                "run": "script",
                "inputs": ["w.in"],
                "outputs": ["w.out"],
                "timeout_s": 1,
                "args": {"path": "gen.py"},
            }
        },
    )

    code, _out, err = run(capsys, str(set_file))

    assert code == 1
    assert "timeout after 1s" in err


def test_a_failing_node_surfaces_its_stderr_verbatim(tmp_path, capsys):
    (tmp_path / "w.in").write_text("in", encoding="utf-8")
    (tmp_path / "gen.py").write_text(
        "import sys\n"
        "print('dyld: Library not loaded: libx265.215.dylib', file=sys.stderr)\n"
        "sys.exit(1)\n",
        encoding="utf-8",
    )
    set_file = write_set(
        tmp_path,
        {
            "w": {
                "run": "script",
                "inputs": ["w.in"],
                "outputs": ["w.out"],
                "args": {"path": "gen.py"},
            }
        },
    )

    code, _out, err = run(capsys, str(set_file))

    assert code == 1
    assert "    dyld: Library not loaded: libx265.215.dylib" in err


# --------------------------------------------------------------------------- #
# external nodes: the human steps the graph must know about
# --------------------------------------------------------------------------- #


def _external_set(root):
    copy_script(root)
    return write_set(
        root,
        {
            "e": {
                "run": "external",
                "outputs": ["e.out"],
                "args": {"reason": "a human does this"},
            },
            "f": script_node("f", "e.out", "f.out"),
        },
    )


def test_an_external_node_is_never_run_and_blocks_downstream(tmp_path, capsys, monkeypatch):
    set_file = _external_set(tmp_path)
    calls = []
    import subprocess

    real_run = subprocess.run
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: (calls.append(a), real_run(*a, **k))[1]
    )

    code, out, _err = run(capsys, str(set_file))

    assert code == 1
    assert "BLOCKED  e  external: a human does this" in out
    assert "produce e.out" in out
    assert not (tmp_path / "f.out").exists()
    assert calls == []


def test_force_does_not_run_an_external_node(tmp_path, capsys):
    set_file = _external_set(tmp_path)

    code, out, _err = run(capsys, str(set_file), "--force")

    assert code == 1
    assert "note: --force does not run external node(s): e" in out
    assert "BLOCKED  e" in out
    assert not (tmp_path / "f.out").exists()


def test_an_existing_external_output_is_adopted_into_state_and_logged(tmp_path, capsys):
    """Correction 3: S4 and A18 deadlock, so adopt — and say so."""

    set_file = _external_set(tmp_path)
    assert run(capsys, str(set_file))[0] == 1
    (tmp_path / "e.out").write_text("exported by hand", encoding="utf-8")

    code, out, err = run(capsys, str(set_file))

    assert code == 0, err
    assert "adopted existing external output into state: e.out" in out
    assert (tmp_path / "f.out").read_text(encoding="utf-8") == "exported by hand"
    assert "build: 1 built, 1 skipped, 0 blocked, 0 failed" in out

    # Adoption spawns nothing and is idempotent.
    code, out, err = run(capsys, str(set_file))
    assert code == 0, err
    assert "build: 0 built, 2 skipped, 0 blocked, 0 failed" in out


def test_a_rewritten_external_output_is_re_adopted_and_cascades(tmp_path, capsys):
    """Hand-editing an external output is the point of the node, not tampering."""

    set_file = _external_set(tmp_path)
    (tmp_path / "e.out").write_text("v1", encoding="utf-8")
    assert run(capsys, str(set_file))[0] == 0

    (tmp_path / "e.out").write_text("v2", encoding="utf-8")
    code, out, err = run(capsys, str(set_file))

    assert code == 0, err
    assert "re-adopted hand-authored external output into state: e.out" in out
    assert (tmp_path / "f.out").read_text(encoding="utf-8") == "v2"


def test_adoption_never_applies_to_a_non_external_node(tmp_path, capsys):
    copy_script(tmp_path)
    (tmp_path / "a.in").write_text("A", encoding="utf-8")
    (tmp_path / "a.out").write_text("pre-existing", encoding="utf-8")
    set_file = write_set(tmp_path, {"a": script_node("a", "a.in", "a.out")})

    code, out, err = run(capsys, str(set_file))

    assert code == 0, err
    assert "BUILD  a  never built" in out
    assert (tmp_path / "a.out").read_text(encoding="utf-8") == "A"


def test_an_external_node_blocks_when_its_own_input_changed(tmp_path, capsys):
    copy_script(tmp_path)
    (tmp_path / "src.txt").write_text("v1", encoding="utf-8")
    set_file = write_set(
        tmp_path,
        {
            "e": {
                "run": "external",
                "inputs": ["src.txt"],
                "outputs": ["e.out"],
                "args": {"reason": "a human exports it"},
            },
            "f": script_node("f", "e.out", "f.out"),
        },
    )
    (tmp_path / "e.out").write_text("exported", encoding="utf-8")
    assert run(capsys, str(set_file))[0] == 0

    (tmp_path / "src.txt").write_text("v2", encoding="utf-8")
    code, out, _err = run(capsys, str(set_file))

    assert code == 1
    assert "BLOCKED  e" in out
    assert "input changed: src.txt" in out


def test_an_external_howto_is_named_when_the_node_blocks(tmp_path, capsys):
    (tmp_path / "howto.md").write_text("drag the footprints\n", encoding="utf-8")
    set_file = write_set(
        tmp_path,
        {
            "e": {
                "run": "external",
                "outputs": ["e.out"],
                "args": {"reason": "a human does this", "howto": "howto.md"},
            }
        },
    )

    code, out, _err = run(capsys, str(set_file))

    assert code == 1
    assert "see howto.md" in out


# --------------------------------------------------------------------------- #
# Correction 2 — review: pdf
# --------------------------------------------------------------------------- #


SHEET_SCRIPT = """
import pathlib
import sys

out = pathlib.Path("out")
out.mkdir(parents=True, exist_ok=True)
(out / "SHT-001.svg").write_text("<svg/>\\n", encoding="utf-8")
if "--no-pdf" not in sys.argv:
    (out / "SHT-001.pdf").write_bytes(b"%PDF-1.4\\n%%EOF\\n")
print("sheet done")
"""


def _review_set(root, *, argv=None):
    (root / "sheet.py").write_text(SHEET_SCRIPT, encoding="utf-8")
    (root / "in.yaml").write_text("x: 1\n", encoding="utf-8")
    args: dict = {"path": "sheet.py"}
    if argv:
        args["argv"] = list(argv)
    return write_set(
        root,
        {
            "sheet": {
                "run": "script",
                "review": "pdf",
                "inputs": ["in.yaml"],
                "outputs": ["out/SHT-001.svg", "out/SHT-001.pdf"],
                "args": args,
            }
        },
    )


def test_a_review_pdf_target_must_produce_its_pdf_or_the_build_fails_naming_it(tmp_path, capsys):
    set_file = _review_set(tmp_path, argv=["--no-pdf"])

    code, _out, err = run(capsys, str(set_file))

    assert code == 1
    assert "node 'sheet': review: pdf declared but no PDF was produced: out/SHT-001.pdf" in err


def test_a_blocked_build_does_not_bury_the_block_under_a_review_complaint(tmp_path, capsys):
    """A review target the build never reached is not the story; the block is."""

    (tmp_path / "sheet.py").write_text(SHEET_SCRIPT, encoding="utf-8")
    set_file = write_set(
        tmp_path,
        {
            "export": {
                "run": "external",
                "outputs": ["in.yaml"],
                "args": {"reason": "a human exports it from the GUI"},
            },
            "sheet": {
                "run": "script",
                "review": "pdf",
                "inputs": ["in.yaml"],
                "outputs": ["out/SHT-001.svg", "out/SHT-001.pdf"],
                "args": {"path": "sheet.py"},
            },
        },
    )

    code, out, err = run(capsys, str(set_file))

    assert code == 1
    assert "BLOCKED  export" in out
    assert "review: pdf declared but no PDF was produced" not in err


def test_a_review_pdf_target_builds_and_then_checks_current(tmp_path, capsys):
    set_file = _review_set(tmp_path)

    code, out, err = run(capsys, str(set_file))
    assert code == 0, err
    assert (tmp_path / "out" / "SHT-001.pdf").exists()

    code, out, err = run(capsys, str(set_file), "--check")
    assert code == 0, err
    assert "check: current" in out


def test_check_names_a_review_target_whose_pdf_is_gone_and_writes_nothing(tmp_path, capsys):
    set_file = _review_set(tmp_path)
    assert run(capsys, str(set_file))[0] == 0
    (tmp_path / "out" / "SHT-001.pdf").unlink()
    before = tree_snapshot(tmp_path)

    code, out, err = run(capsys, str(set_file), "--check")

    assert code == 3
    assert "NO PDF: review: pdf declared but absent: out/SHT-001.pdf" in out
    assert "1 missing review PDF(s)" in err
    assert tree_snapshot(tmp_path) == before


def test_an_unmarked_target_owes_no_pdf(tmp_path, capsys):
    """Intermediates, GeoJSON, staged layers and synthetic nodes have no obligation."""

    set_file = abcd_set(tmp_path)

    code, out, err = run(capsys, str(set_file))

    assert code == 0, err
    assert "review" not in out
    assert not any(tmp_path.rglob("*.pdf"))


def test_review_pdf_requires_a_declared_pdf_output(tmp_path, capsys):
    (tmp_path / "sheet.py").write_text(SHEET_SCRIPT, encoding="utf-8")
    (tmp_path / "in.yaml").write_text("x: 1\n", encoding="utf-8")
    set_file = write_set(
        tmp_path,
        {
            "sheet": {
                "run": "script",
                "review": "pdf",
                "inputs": ["in.yaml"],
                "outputs": ["out/SHT-001.svg"],
                "args": {"path": "sheet.py"},
            }
        },
    )

    code, _out, err = run(capsys, str(set_file))

    assert code == 2
    assert "review: pdf requires a declared .pdf output" in err
    assert "out/SHT-001.svg" in err


# --------------------------------------------------------------------------- #
# GeoPackage: the layer is the unit of production
# --------------------------------------------------------------------------- #


GPKG_WRITER = """
import pathlib
import sqlite3
import sys

path = pathlib.Path(sys.argv[1])
layer = sys.argv[2]
value = pathlib.Path(sys.argv[3]).read_text(encoding="utf-8").strip()
con = sqlite3.connect(path)
con.execute(f'CREATE TABLE IF NOT EXISTS "{layer}" (fid INTEGER PRIMARY KEY, name TEXT)')
con.execute(f'DELETE FROM "{layer}"')
con.execute(f'INSERT INTO "{layer}" VALUES (1, ?)', (value,))
con.commit()
con.close()
print(f"wrote {path}:{layer}")
"""


def test_geopackage_output_ownership_is_at_layer_granularity(tmp_path, capsys):
    """Correction 1: the duplicate-claim error quotes the key verbatim."""

    make_gpkg(tmp_path / "site.gpkg", {"layout_poly": [(1, "tank")]})
    (tmp_path / "seed.txt").write_text("x", encoding="utf-8")
    node = {
        "run": "command",
        "inputs": ["seed.txt"],
        "outputs": ["site.gpkg:layout_poly"],
        "args": {"argv": ["true"]},
    }
    set_file = write_set(tmp_path, {"a": node, "b": dict(node)})

    code, _out, err = run(capsys, str(set_file))

    assert code == 2
    assert "output claimed by 2 nodes (a, b): site.gpkg:layout_poly" in err
    assert ".:layout_poly" not in err


def test_two_nodes_writing_different_layers_of_one_geopackage_is_legal(tmp_path, capsys):
    make_gpkg(tmp_path / "site.gpkg", {"layout_poly": [(1, "tank")], "ponds": [(1, "pond")]})
    (tmp_path / "seed.txt").write_text("x", encoding="utf-8")
    set_file = write_set(
        tmp_path,
        {
            "a": {
                "run": "command",
                "inputs": ["seed.txt"],
                "outputs": ["site.gpkg:layout_poly"],
                "args": {"argv": ["true"]},
            },
            "b": {
                "run": "command",
                "inputs": ["seed.txt"],
                "outputs": ["site.gpkg:ponds"],
                "args": {"argv": ["true"]},
            },
        },
    )

    code, out, err = run(capsys, str(set_file), "--dry-run")

    assert code == 0, err
    assert "2 selected" in out


def test_a_build_may_never_write_the_placements_layer_even_with_force(tmp_path, capsys):
    make_gpkg(tmp_path / "site.gpkg", {"placements": [(1, "FA-130")]})
    (tmp_path / "seed.txt").write_text("x", encoding="utf-8")
    set_file = write_set(
        tmp_path,
        {
            "a": {
                "run": "command",
                "inputs": ["seed.txt"],
                "outputs": ["site.gpkg:placements"],
                "args": {"argv": ["true"]},
            }
        },
    )

    for extra in ((), ("--force",)):
        code, _out, err = run(capsys, str(set_file), *extra)
        assert code == 2
        assert "may never write the human-authored GeoPackage layer 'placements'" in err
        assert "site.gpkg:placements" in err


def test_a_gpkg_layer_is_staleness_tracked_per_layer(tmp_path, capsys):
    make_gpkg(tmp_path / "site.gpkg", {"layout_poly": [(1, "tank")], "ponds": [(1, "pond")]})
    (tmp_path / "writer.py").write_text(GPKG_WRITER, encoding="utf-8")
    (tmp_path / "poly.txt").write_text("tank\n", encoding="utf-8")
    (tmp_path / "downstream.py").write_text(
        "import pathlib\npathlib.Path('report.txt').write_text('ok', encoding='utf-8')\n", "utf-8"
    )
    set_file = write_set(
        tmp_path,
        {
            "write-poly": {
                "run": "script",
                "inputs": ["poly.txt"],
                "outputs": ["site.gpkg:layout_poly"],
                "args": {"path": "writer.py", "argv": ["site.gpkg", "layout_poly", "poly.txt"]},
            },
            "read-ponds": {
                "run": "script",
                "inputs": ["site.gpkg:ponds"],
                "outputs": ["report.txt"],
                "args": {"path": "downstream.py"},
            },
        },
    )
    assert run(capsys, str(set_file))[0] == 0

    # Changing layout_poly must not stale the node that reads ponds.
    (tmp_path / "poly.txt").write_text("clarifier\n", encoding="utf-8")
    code, out, err = run(capsys, str(set_file))
    assert code == 0, err
    assert "BUILD  write-poly" in out
    assert "SKIP  read-ponds" in out

    # Changing ponds does stale it.
    connection = sqlite3.connect(tmp_path / "site.gpkg")
    connection.execute("UPDATE ponds SET name = 'settling'")
    connection.commit()
    connection.close()
    code, out, err = run(capsys, str(set_file))
    assert code == 0, err
    assert "BUILD  read-ponds" in out


@pytest.mark.parametrize("sidecar", ["-wal", "-shm"])
def test_an_open_geopackage_is_an_error_before_the_first_byte(tmp_path, capsys, sidecar):
    make_gpkg(tmp_path / "site.gpkg", {"ponds": [(1, "pond")]})
    (tmp_path / "downstream.py").write_text(
        "import pathlib\npathlib.Path('report.txt').write_text('ok', encoding='utf-8')\n", "utf-8"
    )
    set_file = write_set(
        tmp_path,
        {
            "read": {
                "run": "script",
                "inputs": ["site.gpkg:ponds"],
                "outputs": ["report.txt"],
                "args": {"path": "downstream.py"},
            }
        },
    )
    marker = tmp_path / f"site.gpkg{sidecar}"
    marker.write_bytes(b"")

    code, _out, err = run(capsys, str(set_file))

    assert code == 2
    assert f"has an open SQLite WAL sidecar (site.gpkg{sidecar})" in err
    assert "close QGIS" in err
    assert not (tmp_path / "report.txt").exists()

    marker.unlink()
    assert run(capsys, str(set_file))[0] == 0


def test_reading_a_wal_mode_geopackage_leaves_no_sidecars(tmp_path, capsys):
    """The build must not create the very sidecars its pre-flight refuses.

    A GeoPackage QGIS has touched is in WAL mode, and opening a WAL database even
    with ``mode=ro`` creates its ``-wal``/``-shm`` sidecars. That would deadlock the
    driver against itself: build once, sidecars appear, every later build refuses.
    """

    gpkg = make_gpkg(tmp_path / "site.gpkg", {"ponds": [(1, "pond")]})
    connection = sqlite3.connect(gpkg)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.commit()
    connection.close()
    for suffix in ("-wal", "-shm"):
        sidecar = tmp_path / f"site.gpkg{suffix}"
        if sidecar.exists():
            sidecar.unlink()
    (tmp_path / "downstream.py").write_text(
        "import pathlib\npathlib.Path('report.txt').write_text('ok', encoding='utf-8')\n", "utf-8"
    )
    set_file = write_set(
        tmp_path,
        {
            "read": {
                "run": "script",
                "inputs": ["site.gpkg:ponds"],
                "outputs": ["report.txt"],
                "args": {"path": "downstream.py"},
            }
        },
    )

    for _ in range(3):
        code, out, err = run(capsys, str(set_file))
        assert code == 0, err + out
        assert not (tmp_path / "site.gpkg-wal").exists()
        assert not (tmp_path / "site.gpkg-shm").exists()

    assert run(capsys, str(set_file), "--check")[0] == 0


def test_a_node_writing_a_gpkg_layer_is_guarded_too(tmp_path, capsys):
    """Addendum A2: OGR does not refuse a live WAL, so the driver must."""

    make_gpkg(tmp_path / "site.gpkg", {"layout_poly": [(1, "tank")]})
    (tmp_path / "writer.py").write_text(GPKG_WRITER, encoding="utf-8")
    (tmp_path / "poly.txt").write_text("tank\n", encoding="utf-8")
    set_file = write_set(
        tmp_path,
        {
            "write": {
                "run": "script",
                "inputs": ["poly.txt"],
                "outputs": ["site.gpkg:layout_poly"],
                "args": {"path": "writer.py", "argv": ["site.gpkg", "layout_poly", "poly.txt"]},
            }
        },
    )
    (tmp_path / "site.gpkg-shm").write_bytes(b"")

    code, _out, err = run(capsys, str(set_file))

    assert code == 2
    assert "(output) has an open SQLite WAL sidecar" in err


# --------------------------------------------------------------------------- #
# state, the P3 boundary, and the ISSUED gate
# --------------------------------------------------------------------------- #


def test_state_is_written_under_root_and_is_the_only_thing_written(tmp_path, capsys):
    set_file = abcd_set(tmp_path)
    expected = {
        "a.in",
        "b.in",
        "copy.py",
        "drawing-set.yaml",
        "a.out",
        "b.out",
        "c.out",
        "d.out",
        ".technical_drawings_for_agents",
        ".technical_drawings_for_agents/build-state.json",
    }

    assert run(capsys, str(set_file))[0] == 0

    assert set(tree_snapshot(tmp_path)) == expected
    assert not list(tmp_path.rglob("*.manifest.json"))


def test_deleting_the_state_file_is_always_safe(tmp_path, capsys):
    set_file = abcd_set(tmp_path)
    assert run(capsys, str(set_file))[0] == 0
    shutil.rmtree(tmp_path / ".technical_drawings_for_agents")

    code, out, err = run(capsys, str(set_file))

    assert code == 0, err
    assert "build: 4 built" in out


def test_the_state_file_holds_no_absolute_paths(tmp_path, capsys):
    """A key that appears in state must not be machine-specific (Correction 1)."""

    set_file = abcd_set(tmp_path)
    assert run(capsys, str(set_file))[0] == 0

    text = (tmp_path / ".technical_drawings_for_agents" / "build-state.json").read_text(encoding="utf-8")

    assert str(tmp_path) not in text
    assert '"a.in"' in text


def test_build_never_writes_a_meta_yaml(tmp_path, capsys):
    simple = tmp_path / "simple"
    pid_work = tmp_path / "pid"
    shutil.copytree(SIMPLE, simple)
    shutil.copytree(PID_EXAMPLE, pid_work)
    before = {
        path: file_record(path) for path in (simple / "meta.yaml", pid_work / "meta.yaml")
    }
    set_file = write_set(
        tmp_path,
        {
            "simple": {
                "run": "script",
                "inputs": ["simple/inputs.yaml"],
                "outputs": [
                    "simple/out/EXA-CIV-SEC-001.svg",
                    "simple/out/EXA-CIV-SEC-001.dxf",
                ],
                "args": {"path": "simple/source.py"},
            },
            "pid": {
                "run": "verb:pid",
                "inputs": ["pid/SYN-PSK-PID-001.pid.yaml"],
                "outputs": ["pid/out/SYN-PSK-PID-001.svg"],
                "args": {"data": "pid/SYN-PSK-PID-001.pid.yaml", "out": "pid/out"},
            },
        },
    )

    assert run(capsys, str(set_file), "--force")[0] == 0

    assert before == {
        path: file_record(path) for path in (simple / "meta.yaml", pid_work / "meta.yaml")
    }


def test_the_driver_changes_no_output_bytes(tmp_path, capsys):
    """The same source rendered by ``render`` and built by ``build`` agrees.

    The SVG is compared byte-for-byte. The DXF is compared through P3's
    ``mask_dxf_volatiles``, because ezdxf re-rolls ``$TDCREATE``/``$TDUPDATE`` and
    two GUIDs on every save: a raw byte comparison of two DXFs written seconds
    apart asserts something the format does not offer, and P3 declares ``dxf``
    digest mode ``masked`` for exactly this reason.
    """

    from technical_drawings_for_agents.provenance import mask_dxf_volatiles
    from technical_drawings_for_agents.render import render_source

    render_work = tmp_path / "render"
    build_work = tmp_path / "build"
    shutil.copytree(SIMPLE, render_work)
    shutil.copytree(SIMPLE, build_work)
    shutil.rmtree(render_work / "out")
    shutil.rmtree(build_work / "out")

    render_source(render_work / "source.py", render_work / "out")
    set_file = write_set(
        build_work,
        {
            "sheet": {
                "run": "script",
                "inputs": ["inputs.yaml"],
                "outputs": ["out/EXA-CIV-SEC-001.svg", "out/EXA-CIV-SEC-001.dxf"],
                "args": {"path": "source.py"},
            }
        },
    )
    assert run(capsys, str(set_file))[0] == 0

    name = "EXA-CIV-SEC-001.svg"
    assert (render_work / "out" / name).read_bytes() == (build_work / "out" / name).read_bytes()
    name = "EXA-CIV-SEC-001.dxf"
    assert mask_dxf_volatiles(
        (render_work / "out" / name).read_text(encoding="utf-8", errors="surrogateescape")
    ) == mask_dxf_volatiles(
        (build_work / "out" / name).read_text(encoding="utf-8", errors="surrogateescape")
    )


def test_a_dxf_output_is_not_stale_from_its_own_container_timestamps(tmp_path, capsys):
    """P3's masked digest, used as the output digest: a re-save is not a hand-edit."""

    work = tmp_path / "simple"
    shutil.copytree(SIMPLE, work)
    shutil.rmtree(work / "out")
    set_file = write_set(
        work,
        {
            "sheet": {
                "run": "script",
                "inputs": ["inputs.yaml"],
                "outputs": ["out/EXA-CIV-SEC-001.svg", "out/EXA-CIV-SEC-001.dxf"],
                "args": {"path": "source.py"},
            }
        },
    )
    assert run(capsys, str(set_file))[0] == 0

    # Re-run the generator behind the build's back: same drawing, new timestamps.
    assert run(capsys, str(set_file), "--force")[0] == 0
    code, out, err = run(capsys, str(set_file))

    assert code == 0, err
    assert "build: 0 built, 1 skipped, 0 blocked, 0 failed" in out


# --------------------------------------------------------------------------- #
# verbs as nodes
# --------------------------------------------------------------------------- #


def test_a_validate_node_is_always_run_and_never_cached(tmp_path, capsys):
    work = tmp_path / "simple"
    shutil.copytree(SIMPLE, work)
    set_file = write_set(
        tmp_path, {"check": {"run": "verb:validate", "args": {"target": "simple"}}}
    )

    for _ in range(2):
        code, out, err = run(capsys, str(set_file))
        assert code == 0, err
        assert "check node - always run" in out
        assert "OK  check  -> (no artifact)" in out
    # Nothing about a check node's verdict is cached, so no state exists at all.
    assert not (tmp_path / ".technical_drawings_for_agents").exists()


def test_check_reports_a_check_node_as_would_run_and_still_passes_the_gate(tmp_path, capsys):
    """A node with no artifact is neither current nor stale.

    The base spec makes a ``verb:validate`` node permanently ``stale`` (correct: it
    has nothing to be stale about) *and* makes ``--check`` exit 3 on anything not
    current — which together would mean no set containing a validate node could
    ever pass the gate ``--check`` exists to be. It is reported "would run", named
    so nobody can read the verdict as "the validation passed".
    """

    shutil.copytree(SIMPLE, tmp_path / "simple")
    set_file = write_set(
        tmp_path, {"check": {"run": "verb:validate", "args": {"target": "simple"}}}
    )

    code, out, err = run(capsys, str(set_file), "--check")

    assert code == 0, err
    assert "WOULD RUN: check node - always run" in out
    assert "check: current" in out


def test_a_validate_node_failure_fails_the_build(tmp_path, capsys):
    (tmp_path / "empty").mkdir()
    set_file = write_set(
        tmp_path, {"check": {"run": "verb:validate", "args": {"target": "empty"}}}
    )

    code, _out, err = run(capsys, str(set_file))

    assert code == 1
    assert "FAIL  check" in err


def test_a_pid_verb_node_builds_in_process(tmp_path, capsys):
    work = tmp_path / "pid"
    shutil.copytree(PID_EXAMPLE, work)
    shutil.rmtree(work / "out", ignore_errors=True)
    set_file = write_set(
        tmp_path,
        {
            "pid": {
                "run": "verb:pid",
                "inputs": ["pid/SYN-PSK-PID-001.pid.yaml"],
                "outputs": ["pid/out/SYN-PSK-PID-001.svg"],
                "args": {"data": "pid/SYN-PSK-PID-001.pid.yaml", "out": "pid/out"},
            }
        },
    )

    code, _out, err = run(capsys, str(set_file))

    assert code == 0, err
    assert (work / "out" / "SYN-PSK-PID-001.svg").exists()


def test_a_script_node_rebuilds_when_its_own_script_changes(tmp_path, capsys):
    (tmp_path / "w.in").write_text("in", encoding="utf-8")
    (tmp_path / "gen.py").write_text(
        "import pathlib\npathlib.Path('w.out').write_text('v1', encoding='utf-8')\n", "utf-8"
    )
    set_file = write_set(
        tmp_path,
        {
            "w": {
                "run": "script",
                "inputs": ["w.in"],
                "outputs": ["w.out"],
                "args": {"path": "gen.py"},
            }
        },
    )
    assert run(capsys, str(set_file))[0] == 0

    (tmp_path / "gen.py").write_text(
        "import pathlib\npathlib.Path('w.out').write_text('v2', encoding='utf-8')\n", "utf-8"
    )
    code, out, err = run(capsys, str(set_file))

    assert code == 0, err
    assert "input changed: gen.py" in out
    assert (tmp_path / "w.out").read_text(encoding="utf-8") == "v2"


# --------------------------------------------------------------------------- #
# the shipped example set is the repo's own gate
# --------------------------------------------------------------------------- #


def test_the_shipped_example_set_builds_and_then_checks_current(tmp_path, capsys):
    work = tmp_path / "example"
    shutil.copytree(REPO_EXAMPLE, work)
    for out_dir in work.rglob("out"):
        shutil.rmtree(out_dir, ignore_errors=True)
    set_file = work / "drawing-set.yaml"

    code, out, err = run(capsys, str(set_file))
    assert code == 0, err + out
    assert "0 blocked, 0 failed" in out

    code, out, err = run(capsys, str(set_file), "--check")
    assert code == 0, err + out
    assert "check: current" in out


def test_plan_and_check_are_pure_functions_of_the_declaration(tmp_path):
    set_file = abcd_set(tmp_path)
    dset = load_drawing_set(set_file)
    store = JsonStateStore(dset.root)

    first = plan(dset, state=store)
    second = plan(dset, state=store)

    assert first == second
    assert not (tmp_path / ".technical_drawings_for_agents").exists()


def test_load_drawing_set_raises_build_error_for_a_missing_file(tmp_path):
    with pytest.raises(BuildError, match="cannot read"):
        load_drawing_set(tmp_path / "nope.yaml")
