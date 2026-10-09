"""P9 acceptance tests 15-19 — the ISSUED gate and the approver cell.

This file is the safety surface of P9. Every test in it exists to make one
sentence mechanically true: **no code path in this toolkit can flip a drawing to
ISSUED FOR CONSTRUCTION or write a value into an approver field.**

The gate only ever gets stricter, and only in one direction. What it must never
do is get weaker, and what P9 must never do is change the shipped gate's verdict
for a drawing that has no revision register — which is why
``test_the_gate_is_unchanged_for_a_register_less_drawing`` sits alongside the new
requirements.
"""

from __future__ import annotations

import datetime
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
import yaml

from technical_drawings_for_agents.cli import build_parser, main
from technical_drawings_for_agents.meta import DrawingMeta
from technical_drawings_for_agents.revisions import RevisionRegister
from technical_drawings_for_agents.titleblock import TitleBlockFields, iso7200_title_block

from .p9_frames import FakeFrame

REPO = Path(__file__).resolve().parents[1]
APPROVER = "R. Atimbire"
GATE_MESSAGE = (
    "meta: for_construction=true is only allowed once status is "
    "ISSUED (engineer sign-off required)"
)


def _row(rev="A", day=15, **overrides) -> dict:
    row = {
        "rev": rev,
        "date": datetime.date(2026, 7, day),
        "description": f"Revision {rev}",
        "by": "AB",
    }
    row.update(overrides)
    return row


def _write(tmp_path, **overrides) -> Path:
    data = {
        "number": "EXA-CIV-SEC-001",
        "title": "Typical Section",
        "revision": "A",
        "status": "CONCEPT",
        "for_construction": False,
    }
    data.update(overrides)
    path = tmp_path / "meta.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def _sealed_rows(rows: list[dict]) -> list[dict]:
    register = RevisionRegister.from_list(rows, ctx="meta")
    out = [dict(row) for row in rows]
    out[-1]["seal"] = register.digests()[-1]
    return out


# --------------------------------------------------------------------------- #
# 15 — for_construction requires a named approver and a valid seal
# --------------------------------------------------------------------------- #


def test_for_construction_requires_a_named_approver_and_a_seal(tmp_path):
    """Test 15. The one deliberate compatibility break, argued in §4.9."""
    issued = dict(revision="A", status="ISSUED", for_construction=True)

    # app empty -> the gate names the approver and says no tool may supply it.
    no_app = DrawingMeta.load(_write(tmp_path, revisions=[_row()], **issued))
    problems = no_app.validate()
    assert any("named approver" in p and "no tool may supply it" in p for p in problems)
    assert any("revisions[-1].app" in p for p in problems)

    # app set, seal absent -> the gate names the seal.
    no_seal = DrawingMeta.load(
        _write(tmp_path, revisions=[_row(app=APPROVER)], **issued)
    )
    problems = no_seal.validate()
    assert len(problems) == 1, problems
    assert "valid seal" in problems[0]
    assert "revisions[-1].seal" in problems[0]

    # app set, seal valid -> clean.
    sealed = DrawingMeta.load(
        _write(tmp_path, revisions=_sealed_rows([_row(app=APPROVER)]), **issued)
    )
    assert sealed.validate() == []

    # app set, seal present but stale -> both the tamper message and the gate.
    rows = _sealed_rows([_row(app=APPROVER)])
    rows[0]["description"] = "Edited after sealing"
    stale = DrawingMeta.load(_write(tmp_path, revisions=rows, **issued))
    problems = stale.validate()
    assert any("must not be edited in place" in p for p in problems)
    assert any("does not match the entry's content" in p for p in problems)


def test_the_pre_existing_gate_message_is_unchanged_verbatim(tmp_path):
    """The shipped message text, asserted character for character."""
    meta = DrawingMeta.load(
        _write(
            tmp_path,
            status="CONCEPT",
            for_construction=True,
            revisions=[_row(app=APPROVER)],
        )
    )
    assert GATE_MESSAGE in meta.validate()


def test_the_gate_is_unchanged_for_a_register_less_drawing():
    """The compatibility boundary of the strengthened gate, stated as a test.

    A drawing with no ``revisions:`` block keeps exactly the verdict it had before
    P9 — the additional approver/seal requirement is gated on a register existing,
    because the shipped ``test_meta_for_construction_gate`` requires an ISSUED,
    for-construction, register-less drawing to validate clean. That is a real
    residual gap and it is recorded in the PR body, not papered over here.
    """
    assert DrawingMeta(
        number="X-1", title="t", status="ISSUED", for_construction=True
    ).validate() == []
    assert DrawingMeta(
        number="X-1", title="t", status="CONCEPT", for_construction=True
    ).validate() == [GATE_MESSAGE]


# --------------------------------------------------------------------------- #
# 16 — no code path writes status, for_construction or app
# --------------------------------------------------------------------------- #


def test_no_toolkit_code_path_writes_status_for_construction_or_app(tmp_path, capsys):
    """Test 16. `revision add` for real, then a line-level diff of what it touched."""
    drawing = tmp_path / "drawing"
    path = _write(
        drawing,
        revision="A",
        status="CONCEPT",
        for_construction=False,
        revisions=[_row()],
    )
    before = path.read_text(encoding="utf-8")

    assert main(
        [
            "revision", "add", str(drawing),
            "--rev", "C",
            "--description", "Channel invert lowered to 54.20 m MSL",
            "--by", "AB",
            "--date", "2026-07-20",
        ]
    ) == 0
    capsys.readouterr()

    after = path.read_text(encoding="utf-8")
    reloaded = DrawingMeta.load(path)
    assert reloaded.status == "CONCEPT"
    assert reloaded.for_construction is False
    assert reloaded.revision == "C"
    latest = reloaded.register().latest
    assert (latest.rev, latest.chk, latest.app) == ("C", None, None)
    assert reloaded.validate() == []

    removed = [line for line in before.splitlines() if line not in after.splitlines()]
    added = [line for line in after.splitlines() if line not in before.splitlines()]
    assert removed == ["revision: A"]
    assert added == [
        "revision: C",
        "- rev: C",
        "  date: 2026-07-20",
        '  description: "Channel invert lowered to 54.20 m MSL"',
        '  by: "AB"',
        "  chk:",
        "  app:",
    ]
    # Written empty, not omitted: the cell exists, is visible, and is blank.
    assert added[-1].strip() == "app:"
    assert APPROVER not in after
    # The appended entry matches the indentation the file already used.
    assert "\n- rev: C\n" in after


def test_revision_add_refuses_a_token_that_would_reorder_the_register(tmp_path, capsys):
    drawing = tmp_path / "drawing"
    _write(drawing, revision="B", revisions=[_row("A", 15), _row("B", 16)])
    assert main(
        ["revision", "add", str(drawing), "--rev", "A", "--description", "x", "--by", "N"]
    ) == 2
    assert "already in revisions" in capsys.readouterr().err


def test_revision_list_reads_the_register_and_writes_nothing(tmp_path, capsys):
    drawing = tmp_path / "drawing"
    path = _write(drawing, revision="A", revisions=[_row(app=APPROVER)])
    before = path.read_bytes()
    assert main(["revision", "list", str(drawing)]) == 0
    out = capsys.readouterr().out
    assert "A    2026-07-15" in out
    assert APPROVER in out
    assert path.read_bytes() == before


# --------------------------------------------------------------------------- #
# 17 — approved_by comes only from an explicit app value
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("app_value", [None, "", "   "])
def test_approved_by_comes_only_from_an_explicit_app_value(tmp_path, app_value):
    """Test 17. `by` and `chk` are set; nothing leaks into the approver cell."""
    row = _row(by="AB", chk="CD")
    if app_value is not None:
        row["app"] = app_value
    meta = DrawingMeta.load(_write(tmp_path, revisions=[row]))

    fields = TitleBlockFields.from_meta(meta)
    assert fields.approved_by == ""
    assert fields.created_by == "AB"
    assert fields.checked_by == "CD"

    svg = iso7200_title_block(FakeFrame("A1", "landscape"), fields)
    root = ET.fromstring(svg)
    cell = [
        element
        for element in root.iter()
        if element.attrib.get("data-field") == "approved_by"
        and "tb-cell" in str(element.attrib.get("class", "")).split()
    ][0]
    text = "".join(cell.itertext())
    assert "—" in text
    assert "AB" not in text
    assert "CD" not in text


def test_an_explicit_app_value_is_read_verbatim(tmp_path):
    meta = DrawingMeta.load(_write(tmp_path, revisions=[_row(app=APPROVER)]))
    assert TitleBlockFields.from_meta(meta).approved_by == APPROVER


def test_no_fixture_or_example_meta_yaml_in_the_repo_has_a_non_empty_app():
    """Test 17, second half. A plausible approver name in a fixture is a forged
    signature waiting for a copy-paste."""
    candidates = sorted(REPO.glob("drawings/**/meta.yaml")) + sorted(
        REPO.glob("tests/fixtures/*.yaml")
    )
    assert candidates, "expected example drawings to exist"
    for path in candidates:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for index, row in enumerate(data.get("revisions") or []):
            assert not (row.get("app") or "").strip(), f"{path}: revisions[{index}].app"


def test_titleblock_source_names_no_approver_fallback():
    """By source inspection: the module cannot reach an identity even by accident."""
    source = (REPO / "src" / "technical_drawings_for_agents" / "titleblock.py").read_text(encoding="utf-8")
    code = "\n".join(
        line for line in source.splitlines() if not line.strip().startswith("#")
    )
    # Strip the module and class docstrings before looking for identity lookups, so
    # the prose that *explains* the rule does not satisfy the check for it.
    code = code.replace('"""', "\x00").split("\x00")
    code = "".join(code[::2])
    for forbidden in ("getenv", "environ", "getpass", "getuser", "subprocess", "Popen"):
        assert forbidden not in code, forbidden
    # approved_by is assigned exactly once, and only from latest.app.
    assignments = [
        line.strip() for line in code.splitlines() if line.strip().startswith("approved_by=")
    ]
    assert len(assignments) == 1, assignments
    assert "latest.app" in assignments[0]


# --------------------------------------------------------------------------- #
# 18 — the parser cannot grow an --app flag
# --------------------------------------------------------------------------- #


def _revision_subparsers():
    parser = build_parser()
    top = parser._subparsers._group_actions[0].choices  # noqa: SLF001
    assert "revision" in top
    sub = top["revision"]._subparsers._group_actions[0].choices  # noqa: SLF001
    return sub


def test_revision_add_has_no_app_flag():
    """Test 18. Introspects argparse, so a future well-meaning `--app` fails at once."""
    sub = _revision_subparsers()
    assert set(sub) == {"list", "add", "seal"}
    forbidden = ("app", "approve", "approver", "sign", "issue", "force")
    for name, subparser in sub.items():
        options = [
            option
            for action in subparser._actions  # noqa: SLF001
            for option in action.option_strings
        ]
        for option in options:
            lowered = option.lower()
            for word in forbidden:
                assert word not in lowered, f"revision {name} grew {option}"
    add_options = [
        option
        for action in sub["add"]._actions  # noqa: SLF001
        for option in action.option_strings
    ]
    assert set(add_options) == {"-h", "--help", "--rev", "--description", "--by", "--chk", "--date"}


def test_there_is_no_revision_edit_or_unseal_verb():
    assert set(_revision_subparsers()) == {"list", "add", "seal"}


# --------------------------------------------------------------------------- #
# 19 — sealing refuses under CI or without a TTY
# --------------------------------------------------------------------------- #


def test_revision_seal_refuses_without_a_tty_or_under_ci(tmp_path, monkeypatch, capsys):
    """Test 19. A signature a workflow file can apply is not a signature."""
    drawing = tmp_path / "drawing"
    path = _write(
        drawing, revision="A", status="ISSUED", revisions=[_row(app=APPROVER)]
    )
    before = path.read_bytes()

    # (a) CI set.
    monkeypatch.setenv("CI", "1")
    monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)
    assert main(["revision", "seal", str(drawing), "--rev", "A"]) == 2
    assert "CI" in capsys.readouterr().err
    assert path.read_bytes() == before

    # (b) CI unset, stdin not a terminal.
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False, raising=False)
    assert main(["revision", "seal", str(drawing), "--rev", "A"]) == 2
    assert "terminal" in capsys.readouterr().err
    assert path.read_bytes() == before

    # (c) a terminal, but app is empty in the file.
    empty_app = tmp_path / "empty-app"
    empty_path = _write(empty_app, revision="A", status="ISSUED", revisions=[_row()])
    empty_before = empty_path.read_bytes()
    monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)
    monkeypatch.setattr("builtins.input", lambda *_a: "A")
    assert main(["revision", "seal", str(empty_app), "--rev", "A"]) == 2
    assert "app" in capsys.readouterr().err
    assert empty_path.read_bytes() == empty_before

    # (d) a terminal, approved, but not ISSUED.
    concept = tmp_path / "concept"
    concept_path = _write(
        concept, revision="A", status="CONCEPT", revisions=[_row(app=APPROVER)]
    )
    concept_before = concept_path.read_bytes()
    assert main(["revision", "seal", str(concept), "--rev", "A"]) == 2
    assert "ISSUED" in capsys.readouterr().err
    assert concept_path.read_bytes() == concept_before

    # (e) a terminal, approved, ISSUED, but the confirmation does not match.
    monkeypatch.setattr("builtins.input", lambda *_a: "yes")
    assert main(["revision", "seal", str(drawing), "--rev", "A"]) == 2
    assert "confirmation did not match" in capsys.readouterr().err
    assert path.read_bytes() == before


def test_revision_seal_writes_only_the_seal_when_a_human_confirms(
    tmp_path, monkeypatch, capsys
):
    """The success path, so the refusals above are not vacuously green."""
    drawing = tmp_path / "drawing"
    path = _write(
        drawing,
        revision="A",
        status="ISSUED",
        for_construction=True,
        revisions=[_row(app=APPROVER)],
    )
    before = path.read_text(encoding="utf-8")
    assert any("valid seal" in p for p in DrawingMeta.load(path).validate())

    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)
    monkeypatch.setattr("builtins.input", lambda *_a: "a")
    assert main(["revision", "seal", str(drawing), "--rev", "A"]) == 0
    capsys.readouterr()

    after = path.read_text(encoding="utf-8")
    added = [line for line in after.splitlines() if line not in before.splitlines()]
    assert len(added) == 1
    assert added[0].strip().startswith("seal: sha256:")
    reloaded = DrawingMeta.load(path)
    assert reloaded.status_key == "ISSUED"
    assert reloaded.for_construction is True
    assert reloaded.validate() == []
    assert reloaded.register().latest.app == APPROVER


def test_the_seal_command_cannot_supply_an_approver(tmp_path, monkeypatch, capsys):
    """Sealing an unapproved row is refused even with a terminal and a confirmation."""
    drawing = tmp_path / "drawing"
    path = _write(drawing, revision="A", status="ISSUED", revisions=[_row()])
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)
    monkeypatch.setattr("builtins.input", lambda *_a: "A")
    assert main(["revision", "seal", str(drawing), "--rev", "A"]) == 2
    capsys.readouterr()
    assert "seal:" not in path.read_text(encoding="utf-8")
    assert APPROVER not in path.read_text(encoding="utf-8")
    assert DrawingMeta.load(path).register().latest.app is None


def test_no_cli_command_writes_status_or_for_construction():
    """By source inspection over the whole CLI: there is no writer of either field."""
    source = (REPO / "src" / "technical_drawings_for_agents" / "cli.py").read_text(encoding="utf-8")
    for forbidden in (
        '"status"',
        "'status'",
        "for_construction=",
        '"for_construction"',
        "'for_construction'",
        "--force-issue",
    ):
        assert forbidden not in source, forbidden
