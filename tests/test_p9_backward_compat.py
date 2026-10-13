"""P9 acceptance tests 20-23 — backward compatibility, and the new validate checks.

Backward compatibility is the constraint P9 is most able to break, so it is
asserted three ways: the pre-migration ``meta.yaml`` bytes still validate and
still produce the identical ``title_block()`` dict; the legacy pixel renderers
still emit identical bytes; and the migrated example drawings validate clean with
no new warnings.

Byte comparisons go through P3's ``write_text_canonical`` on both sides. Byte
parity means bytes, and the trailing-newline rule is part of the canonical
text-write contract — comparing a raw ``render()`` string against a stored file is
how a sibling implementation lost a whole PR to one ``\\n``.
"""

from __future__ import annotations

import datetime
import shutil
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
import yaml

from technical_drawings_for_agents import Drawing, ViewBox, isosheet, pid
from technical_drawings_for_agents.meta import DrawingMeta
from technical_drawings_for_agents.provenance import EmitPolicy, write_text_canonical
from technical_drawings_for_agents.titleblock import TitleBlockFields
from technical_drawings_for_agents.validate import check_revision_titleblock_svg, validate_target

REPO = Path(__file__).resolve().parents[1]
GOLDENS = Path(__file__).resolve().parent / "goldens"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
EXAMPLES = (
    REPO / "drawings" / "example" / "simple-section",
    REPO / "drawings" / "example" / "synthetic-pid",
)


def _canonical_bytes(text: str, name: str) -> bytes:
    """Round-trip ``text`` through P3's canonical writer and read the bytes back."""
    tmp = Path(tempfile.mkdtemp()) / name
    write_text_canonical(tmp, text, EmitPolicy())
    return tmp.read_bytes()


# --------------------------------------------------------------------------- #
# 20 — the migrated examples still validate clean
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda p: p.name)
def test_existing_example_drawings_validate_after_migration(example: Path):
    """Test 20. Both example numbers already conform, so opting in changes nothing."""
    result = validate_target(example)
    assert result.problems == [], result.problems
    assert result.warnings == [], result.warnings
    assert result.ok is True

    meta = DrawingMeta.load(example / "meta.yaml")
    register = meta.register()
    assert register is not None
    assert register.latest.rev == str(meta.revision).upper()
    assert meta.numbering_scheme().id == "shape-only"
    assert meta.validate() == []
    # The migration adds coverage, not an approver.
    assert register.latest.app is None
    assert register.latest.chk is None


def test_the_migration_did_not_move_the_example_svg_bytes():
    """The register is metadata; the shipped sheets are byte-for-byte unchanged."""
    committed = (EXAMPLES[0] / "out" / "EXA-CIV-SEC-001.svg").read_bytes()
    assert committed == (GOLDENS / "EXA-CIV-SEC-001.svg").read_bytes()


# --------------------------------------------------------------------------- #
# 21 — the pre-P9 meta.yaml, pinned
# --------------------------------------------------------------------------- #


def test_pre_p9_meta_yaml_still_validates_unchanged():
    """Test 21. **The backward-compat test.**

    ``date`` is asserted as a ``datetime.date`` *object*, not the string
    ``"2026-07-15"``: ``meta.py`` annotates it ``str`` but ``yaml.safe_load``
    returns a date and nothing converts it, so ``title_block()["date"]`` has always
    been a date object. Asserting the string would fail, and "fixing" the runtime
    value would change every rendered sheet.
    """
    meta = DrawingMeta.load(FIXTURES / "meta_pre_p9.yaml")

    assert meta.validate() == []
    assert meta.warnings() == []
    assert meta.register() is None
    assert meta.numbering_scheme() is None
    assert meta.title_block() == {
        "project": "drawings-kit example",
        "title": "Concrete-Lined Drainage Channel — Typical Section",
        "drawing_no": "EXA-CIV-SEC-001_RevA",
        "scale": "1:50",
        "date": datetime.date(2026, 7, 15),
        "revision": "A",
    }
    assert isinstance(meta.title_block()["date"], datetime.date)

    # The fixture is the pre-migration file, byte for byte.
    pre = (FIXTURES / "meta_pre_p9.yaml").read_text(encoding="utf-8")
    now = (EXAMPLES[0] / "meta.yaml").read_text(encoding="utf-8")
    assert now.startswith(pre)
    assert "revisions:" not in pre
    assert "numbering:" not in pre


def test_the_x_1_fixture_keeps_validating_clean():
    """``tests/test_toolkit.py``'s ``X-1`` is load-bearing evidence, not a typo to fix."""
    assert DrawingMeta(number="X-1", title="t").validate() == []
    assert DrawingMeta(number="X-1", title="t").warnings() == []
    assert DrawingMeta(number="X-1", title="t").register() is None


def test_legacy_drawing_render_is_byte_identical():
    """Test 21's golden half. ``svg.py`` gained a docstring and nothing else."""
    vb = ViewBox(0, 10, 0, 5, 600, 400)
    rendered = Drawing(600, 400, vb, title_block={"title": "T"}, status="CONCEPT").render()
    assert _canonical_bytes(rendered, "legacy.svg") == (
        GOLDENS / "legacy_drawing_render_pre_p9.svg"
    ).read_bytes()
    assert 'class="title-block"' in rendered


# --------------------------------------------------------------------------- #
# 22 — isosheet is untouched
# --------------------------------------------------------------------------- #


def test_pid_sheet_output_is_byte_identical(tmp_path):
    """Test 22. Pins §4.4: the new block coexists, it does not replace isosheet."""
    from .test_pid import _mini

    data = tmp_path / "TST-PID-001.pid.yaml"
    data.write_text(yaml.safe_dump(_mini(), sort_keys=False), encoding="utf-8")
    out = pid.build(data, tmp_path / "out")[0]
    assert _canonical_bytes(out.read_text(encoding="utf-8"), "pid.svg") == (
        GOLDENS / "TST-PID-001.svg"
    ).read_bytes()


def test_isosheet_titleblock_output_is_byte_identical():
    """The second legacy title block, pinned directly rather than via graphviz."""
    block = isosheet.titleblock(
        900,
        800,
        {
            "number": "TST-BFD-001",
            "title": "Test BFD",
            "revision": "A",
            "date": "2026-07-15",
            "doctype": "BFD",
            "status": "DRAFT",
            "client": "ARC",
            "programme": "DEMO Water",
        },
    )
    wrapped = '<svg xmlns="http://www.w3.org/2000/svg">\n' + block + "\n</svg>"
    assert _canonical_bytes(wrapped, "isosheet.svg") == (
        GOLDENS / "isosheet_titleblock.svg"
    ).read_bytes()
    # The precedent P9 makes structural: the legacy block already refuses to name
    # an approver and prints an em-dash instead.
    assert "AI-assisted" in block
    assert "(Eng. —)" in block


def test_p9_did_not_touch_the_shipped_sheet_model_or_its_gate():
    """Correction 1, as a source-level assertion.

    P9 consumes the ``PaperFrame`` protocol. It must not have reached into the
    sheet model, and the sheet model must still know nothing about the lifecycle.
    """
    sheet_source = (REPO / "src" / "technical_drawings_for_agents" / "sheet.py").read_text(encoding="utf-8")
    for identifier in ("for_construction", "STATUS_ORDER", "status_key"):
        assert identifier not in sheet_source, identifier
    for identifier in ("revisions", "titleblock", "revision_block", "numbering"):
        assert identifier not in sheet_source, identifier
    titleblock_source = (
        REPO / "src" / "technical_drawings_for_agents" / "titleblock.py"
    ).read_text(encoding="utf-8")
    assert "from .sheet import" not in titleblock_source
    assert "import sheet" not in titleblock_source


# --------------------------------------------------------------------------- #
# 23 — one data model behind three renderers
# --------------------------------------------------------------------------- #


def test_title_block_fields_agree_across_the_three_renderers(tmp_path):
    """Test 23. Pins the §4.4 mitigation for three coexisting implementations."""
    path = tmp_path / "meta.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "number": "EXA-CIV-SEC-001",
                "title": "Typical Section",
                "project": "drawings-kit example",
                "discipline": "Civil",
                "revision": "B",
                "scale": "1:50",
                "status": "CONCEPT",
                "revisions": [
                    {"rev": "A", "date": datetime.date(2026, 7, 15),
                     "description": "First issue", "by": "AB"},
                    {"rev": "B", "date": datetime.date(2026, 7, 22),
                     "description": "Invert lowered", "by": "AB", "chk": "CD"},
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    meta = DrawingMeta.load(path)
    fields = TitleBlockFields.from_meta(meta)
    legacy = meta.title_block()

    assert fields.title == legacy["title"]
    assert fields.revision == legacy["revision"].upper()
    assert legacy["drawing_no"] == f"{fields.identification_number}_Rev{fields.revision}"
    assert fields.date_of_issue == str(meta.register().latest.date)
    assert fields.legal_owner == legacy["project"]
    assert fields.document_status == "CONCEPT — NOT FOR CONSTRUCTION"
    assert fields.created_by == "AB"
    assert fields.checked_by == "CD"
    assert fields.approved_by == ""


# --------------------------------------------------------------------------- #
# The new validate checks (V8-V10) are additive and gated
# --------------------------------------------------------------------------- #


def test_a_legacy_svg_gains_no_new_title_block_checks():
    """The gate on the new checks: no P9 attributes, no P9 problems, no warnings."""
    legacy = (GOLDENS / "EXA-CIV-SEC-001.svg").read_text(encoding="utf-8")
    assert 'class="title-block"' in legacy
    assert check_revision_titleblock_svg(legacy) == ([], [])
    assert check_revision_titleblock_svg(legacy, meta=DrawingMeta.load(
        EXAMPLES[0] / "meta.yaml"
    )) == ([], [])
    for path in sorted(REPO.glob("drawings/**/out/*.svg")):
        text = path.read_text(encoding="utf-8")
        assert check_revision_titleblock_svg(text) == ([], []), path


def _p9_sheet(size="A1", orientation="landscape", entries=2, latest=None):
    from technical_drawings_for_agents.revisions import Revision, RevisionRegister
    from technical_drawings_for_agents.titleblock import iso7200_title_block, revision_block

    from .p9_frames import FakeFrame

    frame = FakeFrame(size, orientation)
    register = RevisionRegister(
        tuple(
            Revision(
                rev="ABCDEFGH"[index],
                date=datetime.date(2026, 7, index + 1),
                description=f"Revision {'ABCDEFGH'[index]}",
                by="AB",
            )
            for index in range(entries)
        )
    )
    fields = TitleBlockFields(
        identification_number="EXA-CIV-SEC-001",
        title="Typical Section",
        revision=latest or register.latest.rev,
        date_of_issue="2026-07-02",
    )
    table, _warnings = revision_block(frame, register)
    return (
        '<svg xmlns="http://www.w3.org/2000/svg">\n'
        + iso7200_title_block(frame, fields)
        + "\n"
        + table
        + "\n</svg>"
    )


def test_the_rendered_revision_must_agree_with_meta_revision():
    """V8. A stale sheet beside a bumped register is exactly the error to catch."""
    svg = _p9_sheet(entries=2)
    agreeing = DrawingMeta(number="EXA-CIV-SEC-001", title="t", revision="B")
    assert check_revision_titleblock_svg(svg, meta=agreeing) == ([], [])

    stale = DrawingMeta(number="EXA-CIV-SEC-001", title="t", revision="C")
    problems, warnings = check_revision_titleblock_svg(svg, meta=stale)
    assert warnings == []
    assert len(problems) == 1
    assert "'B'" in problems[0]
    assert "'C'" in problems[0]
    assert "re-render" in problems[0]


@pytest.mark.parametrize("size", ["A3", "A2", "A1", "A0"])
@pytest.mark.parametrize("orientation", ["landscape", "portrait"])
def test_the_emitted_block_is_on_the_paper_and_flush_to_the_table(size, orientation):
    """V9 + V10, at every size and orientation, from the emitted attributes alone."""
    svg = _p9_sheet(size, orientation, entries=3)
    assert check_revision_titleblock_svg(svg) == ([], [])

    root = ET.fromstring(svg)
    title = [
        element
        for element in root.iter()
        if "title-block" in str(element.attrib.get("class", "")).split()
    ][0]
    assert title.attrib["data-sheet"] == size
    assert title.attrib["data-orientation"] == orientation


def test_a_block_drawn_off_the_paper_is_reported():
    """V9 fires on a tampered file, so the check is not vacuous."""
    svg = _p9_sheet("A3", "landscape").replace(
        'data-sheet="A3"', 'data-sheet="A4"', 1
    )
    problems, _warnings = check_revision_titleblock_svg(svg)
    assert any("outside the A4 landscape paper" in problem for problem in problems)


def test_a_detached_revision_table_is_reported():
    """V10 fires when the table stops sitting on the title block."""
    svg = _p9_sheet("A1", "landscape", entries=2)
    root = ET.fromstring(svg)
    block = [
        element
        for element in root.iter()
        if "revision-block" in str(element.attrib.get("class", "")).split()
    ][0]
    x, y, w, h = (float(part) for part in block.attrib["data-extents-mm"].split())
    tampered = svg.replace(
        block.attrib["data-extents-mm"], f"{x:.3f} {y - 20:.3f} {w:.3f} {h:.3f}"
    )
    problems, _warnings = check_revision_titleblock_svg(tampered)
    assert any("bottom edge" in problem for problem in problems)


def test_v5_fires_only_for_a_drawing_that_has_a_register(tmp_path):
    """Correction 1's boundary: a ``sheet:`` block alone adds no P9 check.

    The shipped sheet model asserts that ``validate()`` returns the same list with
    and without a ``sheet:`` block. P9's render check is therefore keyed on the
    register — the thing P9 actually draws a block for — not on ``sheet:``.
    """
    sheet_config = {
        "size": "A3",
        "orientation": "landscape",
        "margins_mm": 10,
        "viewport": {"extent_m": [0, 0, 100, 100], "scale": 1000},
    }
    long_title = "X" * 400

    without = DrawingMeta(number="EXA-CIV-SEC-001", title=long_title)
    with_sheet = DrawingMeta(
        number="EXA-CIV-SEC-001", title=long_title, extra={"sheet": sheet_config}
    )
    assert with_sheet.validate() == without.validate() == []

    # Add a register and the same over-long title is now a reported problem,
    # because a block would actually be drawn.
    with_register = DrawingMeta(
        number="EXA-CIV-SEC-001",
        title=long_title,
        revision="A",
        extra={"sheet": sheet_config},
        revisions=[
            {"rev": "A", "date": datetime.date(2026, 7, 15), "description": "First",
             "by": "AB"}
        ],
    )
    problems = with_register.validate()
    assert len(problems) == 1, problems
    assert "title is 400 chars" in problems[0]


def test_the_real_sheet_model_satisfies_the_paper_frame_protocol():
    """The ``FakeFrame`` is not the only implementation: P1's ``Sheet`` works too."""
    from technical_drawings_for_agents.titleblock import PaperFrame, title_block_extents_mm

    meta = DrawingMeta(
        number="EXA-CIV-SEC-001",
        title="Typical Section",
        revision="A",
        extra={
            "sheet": {
                "size": "A1",
                "orientation": "landscape",
                "margins_mm": 10,
                "viewport": {"extent_m": [0, 0, 100, 100], "scale": 1000},
            }
        },
    )
    frame = meta._paper_frame()
    assert isinstance(frame, PaperFrame)
    assert (frame.size, frame.orientation) == ("A1", "landscape")
    x, y, w, h = title_block_extents_mm(frame)
    assert (w, h) == (180.0, 56.0)
    fx, fy, fw, fh = frame.frame_mm()
    assert x + w == pytest.approx(fx + fw)
    assert y + h == pytest.approx(fy + fh)


def test_a_drawing_directory_with_a_register_reports_warnings_but_no_new_checks(tmp_path):
    """A register produces warnings; it never adds an entry to ``checked``."""
    drawing = tmp_path / "drawing"
    shutil.copytree(EXAMPLES[0], drawing)
    baseline = validate_target(drawing)
    assert baseline.warnings == []

    data = yaml.safe_load((drawing / "meta.yaml").read_text(encoding="utf-8"))
    data["revisions"][0]["pre_register"] = True
    data["revisions"][0]["description"] = "NOT RECORDED (pre-register revision)"
    (drawing / "meta.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    result = validate_target(drawing)
    assert result.problems == []
    assert result.ok is True
    assert result.checked == baseline.checked
    assert len(result.warnings) == 1
    assert "pre_register: true" in result.warnings[0]
