"""P9 acceptance tests 8-11 — the revision register, its rules, and its seals."""

from __future__ import annotations

import dataclasses
import datetime

import pytest
import yaml

from technical_drawings_for_agents.meta import DrawingMeta
from technical_drawings_for_agents.revisions import (
    PRE_REGISTER_SENTINEL,
    Revision,
    RevisionError,
    RevisionRegister,
)

TODAY = datetime.date(2026, 7, 25)


def _meta(tmp_path, **overrides) -> DrawingMeta:
    data = {
        "number": "EXA-CIV-SEC-001",
        "title": "Typical Section",
        "revision": "A",
        "status": "CONCEPT",
        "for_construction": False,
    }
    data.update(overrides)
    path = tmp_path / "meta.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return DrawingMeta.load(path)


def _row(rev="A", day=15, **overrides) -> dict:
    row = {
        "rev": rev,
        "date": datetime.date(2026, 7, day),
        "description": f"Revision {rev}",
        "by": "AB",
    }
    row.update(overrides)
    return row


def _register(*rows: dict) -> RevisionRegister:
    return RevisionRegister.from_list(list(rows), ctx="meta")


# --------------------------------------------------------------------------- #
# 8 — required fields
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("description", [None, "", "   "])
def test_revision_without_description_fails_validation(tmp_path, description):
    """Test 8. Strip-then-check: whitespace is not a change note."""
    row = _row()
    if description is None:
        row.pop("description")
    else:
        row["description"] = description
    meta = _meta(tmp_path, revisions=[row])

    problems = meta.validate()
    assert len(problems) == 1, problems
    assert "revisions[0].description" in problems[0]
    assert "is required" in problems[0]
    assert "a revision with no description is not a revision" in problems[0]


def test_the_pre_register_sentinel_is_gated_on_the_pre_register_flag(tmp_path):
    """Test 8, second half. Honest about an absence; loud about it on every run."""
    ungated = _meta(tmp_path, revisions=[_row(description=PRE_REGISTER_SENTINEL)])
    problems = ungated.validate()
    assert len(problems) == 1, problems
    assert "pre-register sentinel" in problems[0]
    assert "pre_register is not true" in problems[0]

    gated = _meta(
        tmp_path,
        revisions=[_row(description=PRE_REGISTER_SENTINEL, pre_register=True)],
    )
    assert gated.validate() == []
    warnings = gated.warnings()
    assert len(warnings) == 1
    assert "pre_register: true" in warnings[0]
    assert "revisions[0]" in warnings[0]


def test_a_description_longer_than_the_cell_is_an_error_naming_the_limit(tmp_path):
    meta = _meta(tmp_path, revisions=[_row(description="X" * 62)])
    problems = meta.validate()
    assert len(problems) == 1, problems
    assert "62 chars" in problems[0]
    assert "DESCRIPTION cell holds 61" in problems[0]


def test_an_unknown_key_in_a_revision_entry_is_reported_not_swallowed(tmp_path):
    """The defect this PR exists to close, in its second form.

    A typo'd key inside a revision entry would leave an issued revision with no
    recorded change note. `extra`-style tolerance is exactly wrong here.
    """
    meta = _meta(tmp_path, revisions=[_row(descripton="oops")])
    problems = meta.validate()
    assert any("unknown key(s): descripton" in problem for problem in problems)
    with pytest.raises(RevisionError, match="unknown key"):
        meta.register()


def test_revisions_added_to_meta_yaml_are_no_longer_swallowed_into_extra(tmp_path):
    """Before P9, `revisions:` fell into `DrawingMeta.extra` and did nothing."""
    meta = _meta(tmp_path, revisions=[_row()])
    assert "revisions" not in meta.extra
    register = meta.register()
    assert register is not None
    assert register.latest.rev == "A"


# --------------------------------------------------------------------------- #
# 9 — meta.revision must agree with the register
# --------------------------------------------------------------------------- #


def test_meta_revision_absent_from_register_fails_validation(tmp_path):
    """Test 9. Three cases, and V2's message is distinct from V3's."""
    absent = _meta(tmp_path, revision="D", revisions=[_row("A", 15), _row("B", 16)])
    problems = absent.validate()
    assert len(problems) == 1, problems
    assert "'D'" in problems[0]
    assert "absent from revisions" in problems[0]
    assert "known: A, B" in problems[0]

    stale = _meta(tmp_path, revision="A", revisions=[_row("A", 15), _row("B", 16)])
    problems = stale.validate()
    assert len(problems) == 1, problems
    assert "is not the latest" in problems[0]
    assert "latest is 'B'" in problems[0]

    agreeing = _meta(tmp_path, revision="B", revisions=[_row("A", 15), _row("B", 16)])
    assert agreeing.validate() == []


def test_meta_revision_stays_a_separate_field_that_must_agree(tmp_path):
    """The redundancy *is* the check: a derived field could not catch a half-edit."""
    meta = _meta(tmp_path, revision="b", revisions=[_row("A", 15), _row("B", 16)])
    # Comparison is post-normalisation, so case is not a failure.
    assert meta.validate() == []
    assert meta.revision == "b"
    assert meta.register().latest.rev == "B"


# --------------------------------------------------------------------------- #
# 10 — ordering and uniqueness (R1-R6)
# --------------------------------------------------------------------------- #


def test_register_ordering_and_uniqueness_rules(tmp_path):
    """Test 10. R1, R2, R3, R6 and the future-date warning, in one table."""
    duplicate = _meta(tmp_path, revision="A", revisions=[_row("A", 15), _row("A", 16)])
    problems = duplicate.validate()
    assert any(
        "revisions[1].rev 'A' duplicates revisions[0].rev" in problem
        for problem in problems
    )

    backwards_date = _meta(
        tmp_path, revision="B", revisions=[_row("A", 16), _row("B", 15)]
    )
    problems = backwards_date.validate()
    assert len(problems) == 1, problems
    assert "revisions[1].date 2026-07-15 is earlier than revisions[0].date" in problems[0]

    backwards_token = _meta(
        tmp_path, revision="B", revisions=[_row("A", 15), _row("C", 16), _row("B", 17)]
    )
    problems = backwards_token.validate()
    assert any("must increase after revisions[1].rev 'C'" in p for p in problems)

    # A gap is legitimate — a revision can be abandoned before issue.
    gap = _meta(tmp_path, revision="C", revisions=[_row("A", 15), _row("C", 16)])
    assert gap.validate() == []

    # Equal dates are legitimate — two revisions can be issued the same day.
    same_day = _meta(tmp_path, revision="B", revisions=[_row("A", 15), _row("B", 15)])
    assert same_day.validate() == []

    empty = _meta(tmp_path, revisions=[])
    problems = empty.validate()
    assert len(problems) == 1, problems
    assert "at least one entry" in problems[0]
    # Omitting the key entirely stays legal — that is backward compatibility.
    assert _meta(tmp_path).validate() == []
    assert _meta(tmp_path).register() is None


def test_a_future_dated_revision_warns_and_does_not_block():
    register = _register(_row("A", 15))
    problems, warnings = register.validate(
        revision="A", today=datetime.date(2026, 7, 1)
    )
    assert problems == []
    assert len(warnings) == 1
    assert "is in the future" in warnings[0]


def test_the_loader_does_not_sort_the_register(tmp_path):
    """R1: the file order *is* the order. A silent re-sort hides an authoring mistake."""
    register = _register(_row("C", 20), _row("A", 15))
    assert [entry.rev for entry in register.entries] == ["C", "A"]
    problems, _warnings = register.validate()
    assert problems, "an out-of-order register must be reported, not repaired"


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ("not-a-list", "must be a list"),
        (["not-a-mapping"], "must be a mapping"),
    ],
)
def test_structural_problems_raise_from_the_loader(raw, message):
    with pytest.raises(RevisionError, match=message):
        RevisionRegister.from_list(raw, ctx="meta")


def test_an_unparseable_date_names_the_offending_path_and_value():
    with pytest.raises(RevisionError) as excinfo:
        RevisionRegister.from_list([_row(date="15/07/2026")], ctx="meta")
    assert "meta: revisions[0].date" in str(excinfo.value)
    assert "15/07/2026" in str(excinfo.value)


def test_an_iso_8601_string_date_is_accepted_as_well_as_a_yaml_date():
    register = _register(_row(date="2026-07-15"))
    assert register.latest.date == datetime.date(2026, 7, 15)


# --------------------------------------------------------------------------- #
# 11 — immutability, four ways
# --------------------------------------------------------------------------- #


def _sealed_meta(tmp_path, rows: list[dict], **overrides) -> DrawingMeta:
    """Build a register, seal entry 0 with the real chained digest, and reload."""
    register = RevisionRegister.from_list(rows, ctx="meta")
    rows = [dict(row) for row in rows]
    rows[0]["seal"] = register.digests()[0]
    return _meta(tmp_path, revisions=rows, **overrides)


def test_sealed_revision_cannot_be_mutated(tmp_path):
    """Test 11. Edit, reorder, delete-by-insert, append, and the frozen API."""
    rows = [_row("A", 15, app="R. Atimbire")]
    clean = _sealed_meta(
        tmp_path, rows, revision="A", status="ISSUED", for_construction=True
    )
    assert clean.validate() == []

    # (a) editing the sealed entry's content in place.
    edited = dict(clean.revisions[0])
    edited["description"] = "Something else"
    tampered = _meta(
        tmp_path,
        revision="A",
        status="ISSUED",
        for_construction=True,
        revisions=[edited],
    )
    problems = tampered.validate()
    assert any(
        "revisions[0]" in p
        and "sealed" in p
        and "must not be edited in place" in p
        and "add a new revision" in p
        for p in problems
    ), problems

    # (b) reordering: the seal covers the index, so [A, B] -> [B, A] is caught.
    two = [_row("A", 15, app="R. Atimbire"), _row("B", 16)]
    sealed_two = _sealed_meta(
        tmp_path, two, revision="B", status="ISSUED", for_construction=True
    )
    reordered = _meta(
        tmp_path, revision="A", revisions=[sealed_two.revisions[1], sealed_two.revisions[0]]
    )
    assert any("sealed but its content has changed" in p for p in reordered.validate())

    # (c) inserting an entry before the sealed one: the prior digest changes.
    inserted = _meta(
        tmp_path,
        revision="B",
        revisions=[_row("AA", 14), *sealed_two.revisions],
    )
    assert any("sealed but its content has changed" in p for p in inserted.validate())

    # (d) appending a token that precedes the latest entry, or duplicates one.
    register = RevisionRegister.from_list(sealed_two.revisions, ctx="meta")
    with pytest.raises(RevisionError, match="already in revisions"):
        register.append(
            Revision(rev="B", date=datetime.date(2026, 7, 20), description="x", by="AB")
        )
    gapped = RevisionRegister.from_list(
        [_row("A", 15, app="R. Atimbire"), _row("D", 16)], ctx="meta"
    )
    with pytest.raises(RevisionError, match="must increase after the latest entry"):
        gapped.append(
            Revision(rev="C", date=datetime.date(2026, 7, 20), description="x", by="AB")
        )
    with pytest.raises(RevisionError, match="earlier than"):
        gapped.append(
            Revision(rev="E", date=datetime.date(2026, 7, 1), description="x", by="AB")
        )

    # (e) the API is append-only and frozen.
    for attribute in ("update", "edit", "remove", "pop", "__setitem__", "__delitem__"):
        assert not hasattr(RevisionRegister, attribute), attribute
        assert not hasattr(Revision, attribute), attribute
    assert dataclasses.fields(RevisionRegister)
    with pytest.raises(dataclasses.FrozenInstanceError):
        register.entries = ()  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        register.entries[0].app = "somebody"  # type: ignore[misc]
    assert isinstance(register.entries, tuple)


def test_append_returns_a_new_register_and_never_mutates():
    register = _register(_row("A", 15))
    grown = register.append(
        Revision(rev="B", date=datetime.date(2026, 7, 20), description="Rev B", by="AB")
    )
    assert len(register.entries) == 1
    assert len(grown.entries) == 2
    assert grown is not register
    assert grown.entries[1].app is None


def test_the_seal_chains_over_the_index_and_every_preceding_entry():
    """Why a per-entry hash would not be enough."""
    a = Revision(rev="A", date=datetime.date(2026, 7, 15), description="First", by="AB")
    b = Revision(rev="B", date=datetime.date(2026, 7, 16), description="Second", by="AB")
    forward = RevisionRegister((a, b)).digests()
    swapped = RevisionRegister((b, a)).digests()
    assert forward[0] != swapped[1], "the same entry at a different index must differ"
    assert a.digest(index=0, prior_digest="") != a.digest(index=1, prior_digest="")
    assert a.digest(index=0, prior_digest="") != a.digest(index=0, prior_digest="x")
    assert forward[1].startswith("sha256:")
    assert len(forward[1]) == len("sha256:") + 64


def test_a_malformed_seal_string_is_reported():
    register = _register(_row("A", 15, seal="sha256:not-hex"))
    problems, _warnings = register.validate()
    assert any("sha256:<64 lowercase hex>" in p for p in problems)


def test_there_is_no_unseal_or_edit_path_on_the_register():
    """Correcting a sealed row means a human editing YAML, which leaves a git diff."""
    for attribute in ("unseal", "reseal", "set_seal", "clear_seal"):
        assert not hasattr(RevisionRegister, attribute), attribute
        assert not hasattr(Revision, attribute), attribute
