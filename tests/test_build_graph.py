"""Schema validation, graph construction, ordering and cycle reporting.

These are the checks that must all pass *before* a single byte is written, so
each test asserts both the message and that nothing was built.
"""

from __future__ import annotations

import pytest

from technical_drawings_for_agents.build import (
    RUNNERS,
    BuildError,
    load_drawing_set,
    node_definition_digest,
    select,
    topological_order,
)
from technical_drawings_for_agents.cli import build_parser

from .build_fixtures import (
    abcd_set,
    copy_script,
    diamond_nodes,
    run,
    script_node,
    set_body,
    write_set,
    write_yaml,
)

# --------------------------------------------------------------------------- #
# ordering and determinism
# --------------------------------------------------------------------------- #


def _diamond(root, *, reverse: bool = False):
    copy_script(root)
    (root / "a.in").write_text("A", encoding="utf-8")
    nodes = diamond_nodes()
    if reverse:
        nodes = dict(reversed(list(nodes.items())))
    return write_set(root, nodes)


def test_build_order_is_independent_of_declaration_order(tmp_path, capsys):
    forward = _diamond(tmp_path / "forward")
    reverse = _diamond(tmp_path / "reverse", reverse=True)

    order_forward = topological_order(load_drawing_set(forward))
    order_reverse = topological_order(load_drawing_set(reverse))

    assert order_forward == ("a", "b", "c", "d") == order_reverse

    code_a, out_a, _err = run(capsys, str(forward))
    code_b, out_b, _err = run(capsys, str(reverse))

    assert code_a == 0 and code_b == 0
    # Byte-identical stdout with --timings off: reordering the YAML is cosmetic.
    assert out_a.replace("forward", "X") == out_b.replace("reverse", "X")


def test_a_cycle_is_a_clear_error_and_builds_nothing(tmp_path, capsys):
    def build_cycle(root, *, reverse: bool = False):
        copy_script(root)
        nodes = {
            "p": script_node("p", "r.out", "p.out"),
            "q": script_node("q", "p.out", "q.out"),
            "r": script_node("r", "q.out", "r.out"),
        }
        if reverse:
            nodes = dict(reversed(list(nodes.items())))
        return write_set(root, nodes)

    forward = build_cycle(tmp_path / "forward")
    reverse = build_cycle(tmp_path / "reverse", reverse=True)
    expected = "dependency cycle: p -> q -> r -> p"

    code, _out, err = run(capsys, str(forward))
    assert code == 2
    assert expected in err
    assert not list((tmp_path / "forward").glob("*.out"))

    with pytest.raises(BuildError, match=expected):
        load_drawing_set(forward)
    with pytest.raises(BuildError, match=expected):
        load_drawing_set(reverse)


def test_needs_expresses_an_order_that_is_not_a_file_dependency(tmp_path):
    copy_script(tmp_path)
    (tmp_path / "a.in").write_text("A", encoding="utf-8")
    (tmp_path / "b.in").write_text("B", encoding="utf-8")
    set_file = write_set(
        tmp_path,
        {
            "a": script_node("a", "a.in", "a.out"),
            "zz": {**script_node("zz", "b.in", "zz.out"), "needs": ["a"]},
        },
    )

    assert topological_order(load_drawing_set(set_file)) == ("a", "zz")


def test_a_directory_output_creates_an_edge_for_files_inside_it(tmp_path):
    copy_script(tmp_path)
    (tmp_path / "a.in").write_text("A", encoding="utf-8")
    set_file = write_set(
        tmp_path,
        {
            "gen": {
                "run": "script",
                "inputs": ["a.in"],
                "outputs": ["staged"],
                "args": {"path": "copy.py", "argv": ["a.in", "staged/thing.txt"]},
            },
            "consume": script_node("consume", "staged/thing.txt", "final.txt"),
        },
    )

    assert topological_order(load_drawing_set(set_file)) == ("consume", "gen")[::-1]


def test_select_returns_dependencies_not_dependents(tmp_path):
    dset = load_drawing_set(abcd_set(tmp_path))

    assert select(dset, ["c"]) == ("a", "c")
    assert select(dset, None) == ("a", "b", "c", "d")


def test_node_definition_digest_is_stable_and_arg_sensitive(tmp_path):
    dset = load_drawing_set(abcd_set(tmp_path))
    node = dset.nodes["a"]

    first = node_definition_digest(node, dset.root)
    assert first == node_definition_digest(node, dset.root)

    from dataclasses import replace

    assert first != node_definition_digest(replace(node, needs=("b",)), dset.root)


# --------------------------------------------------------------------------- #
# schema errors — precise, before anything runs
# --------------------------------------------------------------------------- #


def _bad(tmp_path, body, name="bad.yaml"):
    copy_script(tmp_path)
    (tmp_path / "a.in").write_text("A", encoding="utf-8")
    return write_yaml(tmp_path / name, body)


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        pytest.param(
            lambda body: body.update({"version": 2}),
            "version 2 not supported by technical_drawings_for_agents",
            id="unsupported-version",
        ),
        pytest.param(
            lambda body: body.pop("version"),
            "missing version (supported: 1)",
            id="missing-version",
        ),
        pytest.param(
            lambda body: body.update({"nodez": {}}),
            "unknown key 'nodez'",
            id="unknown-top-level-key",
        ),
        pytest.param(
            lambda body: body["nodes"]["a"].update({"run": "verb:ingest"}),
            "must be one of ['command', 'external', 'script', 'verb:bfd', 'verb:component', "
            "'verb:layout', 'verb:pid', 'verb:render', 'verb:validate']",
            id="ingest-is-not-a-runner",
        ),
        pytest.param(
            lambda body: body["nodes"]["a"]["args"].update({"output": "x"}),
            "args: unknown key 'output' (accepted: argv, cwd, interpreter, path, tools)",
            id="unknown-arg-key",
        ),
        pytest.param(
            lambda body: body["nodes"]["a"].update({"review": "png"}),
            "review must be one of ['pdf'], got 'png'",
            id="unknown-review-marker",
        ),
        pytest.param(
            lambda body: body["nodes"]["a"].update({"needs": ["laayout"]}),
            "needs names unknown node(s): laayout",
            id="unknown-needs",
        ),
        pytest.param(
            lambda body: body["nodes"]["a"].update({"timeout_s": 0}),
            "timeout_s must be an int > 0",
            id="non-positive-timeout",
        ),
        pytest.param(
            lambda body: body["nodes"]["a"].update({"expand_inputs": True}),
            "expand_inputs is not supported for 'script'",
            id="expansion-on-a-runner-without-one",
        ),
        pytest.param(
            lambda body: body["nodes"]["a"].update({"outputs": []}),
            "outputs must be a non-empty list",
            id="no-declared-output",
        ),
        pytest.param(
            lambda body: body["nodes"]["a"].update({"inputs": ["@nope"]}),
            "references unknown input @nope",
            id="unknown-named-input",
        ),
        pytest.param(
            lambda body: body["nodes"]["a"].update({"inputs": ["a.in", "a.in"]}),
            "duplicate input: a.in",
            id="duplicate-input",
        ),
        pytest.param(
            lambda body: body["nodes"]["a"].update({"outputs": ["a.in"]}),
            "path is both an input and an output: a.in",
            id="input-is-also-output",
        ),
        pytest.param(
            lambda body: body["nodes"]["a"].update({"outputs": ["drawings/x/meta.yaml"]}),
            "a build may never write meta.yaml",
            id="meta-yaml-as-output",
        ),
        pytest.param(
            lambda body: body["set"].update({"id": "not a name"}),
            "set.id must match",
            id="bad-set-id",
        ),
        pytest.param(
            lambda body: body["set"].update({"root": "nowhere"}),
            "set.root not a directory",
            id="missing-root",
        ),
    ],
)
def test_schema_errors_are_precise(tmp_path, capsys, mutate, expected):
    body = set_body({"a": script_node("a", "a.in", "a.out")})
    mutate(body)
    set_file = _bad(tmp_path, body)

    code, _out, err = run(capsys, str(set_file))

    assert code == 2
    assert expected in err
    assert not (tmp_path / "a.out").exists()


def test_a_shell_key_on_a_command_node_is_rejected_with_a_reason(tmp_path, capsys):
    body = set_body(
        {
            "a": {
                "run": "command",
                "inputs": ["a.in"],
                "outputs": ["a.out"],
                "args": {"argv": ["true"], "shell": True},
            }
        }
    )
    set_file = _bad(tmp_path, body)

    code, _out, err = run(capsys, str(set_file))

    assert code == 2
    assert "args.shell is not supported" in err
    assert "a shell string is not a declared graph" in err


def test_timeout_on_a_verb_node_is_rejected(tmp_path, capsys):
    body = set_body(
        {
            "a": {
                "run": "verb:validate",
                "timeout_s": 10,
                "args": {"target": "a.in"},
            }
        }
    )
    set_file = _bad(tmp_path, body)

    code, _out, err = run(capsys, str(set_file))

    assert code == 2
    assert "timeout_s is only accepted for script and command nodes" in err


def test_verb_render_refuses_a_py_source_and_points_at_run_script(tmp_path, capsys):
    body = set_body(
        {
            "a": {
                "run": "verb:render",
                "inputs": ["a.in"],
                "outputs": ["out/x.svg"],
                "args": {"source": "gen.py"},
            }
        }
    )
    set_file = _bad(tmp_path, body)

    code, _out, err = run(capsys, str(set_file))

    assert code == 2
    assert "verb:render cannot take a .py source; use run: script" in err


def test_verb_validate_must_declare_no_outputs(tmp_path, capsys):
    body = set_body(
        {"a": {"run": "verb:validate", "outputs": ["x.out"], "args": {"target": "a.in"}}}
    )
    set_file = _bad(tmp_path, body)

    code, _out, err = run(capsys, str(set_file))

    assert code == 2
    assert "outputs must be empty for verb:validate" in err


def test_a_meta_yaml_input_loads_fine(tmp_path):
    copy_script(tmp_path)
    (tmp_path / "meta.yaml").write_text("number: X\n", encoding="utf-8")
    set_file = write_set(tmp_path, {"a": script_node("a", "meta.yaml", "a.out")})

    assert load_drawing_set(set_file).nodes["a"].inputs[0].path.name in {"copy.py", "meta.yaml"}


def test_an_external_howto_that_does_not_exist_is_an_error(tmp_path, capsys):
    body = set_body(
        {
            "e": {
                "run": "external",
                "outputs": ["e.out"],
                "args": {"reason": "a human", "howto": "missing.md"},
            }
        }
    )
    set_file = _bad(tmp_path, body)

    code, _out, err = run(capsys, str(set_file))

    assert code == 2
    assert "args.howto not found:" in err


def test_a_target_name_may_not_collide_with_a_node_name(tmp_path, capsys):
    body = set_body({"a": script_node("a", "a.in", "a.out")}, targets={"a": ["a"]})
    set_file = _bad(tmp_path, body)

    code, _out, err = run(capsys, str(set_file))

    assert code == 2
    assert "target name collides with node name: a" in err


def test_an_external_node_requires_a_reason(tmp_path, capsys):
    body = set_body({"e": {"run": "external", "outputs": ["e.out"], "args": {}}})
    set_file = _bad(tmp_path, body)

    code, _out, err = run(capsys, str(set_file))

    assert code == 2
    assert "args.reason must be a non-empty string" in err


# --------------------------------------------------------------------------- #
# backward compatibility and the sibling-flag guard
# --------------------------------------------------------------------------- #


def test_help_lists_build_and_every_other_verb():
    """Set equality, so a future edit cannot drop a verb while adding one.

    The base spec's inventory was written before ``manifest``/``sheet`` (P3, P1),
    before ``plot``/``map``/``check``/``layers``/``revision`` (P4, P5, P7, P9) and
    before ``dimensions`` (P6). Add a verb here when you add one to the CLI.
    """

    verbs = set(build_parser()._subparsers._group_actions[0].choices)

    assert verbs == {
        "render",
        "plot",
        "map",
        "validate",
        "layers",
        "check",
        "describe",
        "manifest",
        "sheet",
        "revision",
        "ingest",
        "bfd",
        "pid",
        "component",
        "layout",
        "dimensions",
        "build",
    }


def test_the_runner_inventory_is_the_nine_the_spec_froze():
    assert RUNNERS == {
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


@pytest.mark.parametrize("verb", ["layout", "component"])
def test_every_cli_flag_of_a_wrapped_verb_is_covered_by_the_runner(verb):
    """A new sibling flag must fail a test, not AttributeError inside a build.

    The ``verb:layout`` / ``verb:component`` runners call ``components.cli``'s own
    ``run*`` function with a Namespace. P2 exposes only the args the spec froze and
    supplies the CLI's defaults for the rest — so every ``dest`` the parser
    declares has to be accounted for somewhere in this module.
    """

    from technical_drawings_for_agents import build

    parser = build_parser()._subparsers._group_actions[0].choices[verb]
    dests = {action.dest for action in parser._actions} - {"help", "func"}

    defaults = (
        build._LAYOUT_CLI_DEFAULTS if verb == "layout" else build._COMPONENT_CLI_DEFAULTS
    )
    exposed = set(build._RUNNER_ARG_KEYS[f"verb:{verb}"])
    covered = set(defaults) | exposed

    assert dests <= covered, f"verb:{verb} runner does not set: {sorted(dests - covered)}"
