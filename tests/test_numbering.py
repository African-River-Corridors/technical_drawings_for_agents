"""P9 acceptance tests 12-14 — the drawing-number grammar, and its opt-in gate."""

from __future__ import annotations

import pytest
import yaml

from technical_drawings_for_agents.meta import DrawingMeta
from technical_drawings_for_agents.numbering import (
    SHAPE_ONLY,
    NumberingError,
    NumberingScheme,
)

#: The §3.4 project scheme. It is a *fixture*, not a shipped default: the segment
#: semantics it encodes are open question O-1 and are the owner's to confirm.
DEMO_WATER_V1 = {
    "id": "demo-water-v1",
    "source": "Engineering Drawings as Code §Repo layout (PROPOSED, not adopted)",
    "serial_digits": 3,
    "segments": [
        {"name": "site", "pattern": "[A-Z]{3}", "vocabulary": ["STA", "STB", "STC", "ARC"]},
        {"name": "facility", "pattern": "[A-Z]{2,5}"},
        {
            "name": "drawing_type",
            "pattern": "[A-Z]{2,5}",
            "vocabulary": ["GA", "SEC", "PID", "BFD", "DET", "CSL", "FOUND"],
        },
    ],
}


@pytest.mark.parametrize(
    ("number", "valid", "why"),
    [
        ("ARC-CSL-001", True, "the standard's named 'existing style'"),
        ("STA-WTP-GA-001", True, "the standard's own example, bare"),
        ("EXA-CIV-SEC-001", True, "in-repo example"),
        ("SYN-PSK-PID-001", True, "in-repo example"),
        ("STB-STA-STC-FOUND-001", True, "4 segments, one of 5 chars"),
        ("TST-BFD-001", True, "existing test fixture"),
        ("STA-WTP-GA-999", True, "serial upper bound"),
        ("STA-123-GA-001", True, "digits are legal inside a segment"),
        ("X-1", False, "1-char segment, 1-digit serial (tests/test_toolkit.py)"),
        ("sta-wtp-ga-001", False, "lower-case"),
        ("STA-WTP-GA-1", False, "serial not 3 digits"),
        ("STA-WTP-GA-0001", False, "serial 4 digits"),
        ("STA-WTP-GA-001_RevB", False, "revision suffix in the number"),
        ("STA_WTP_GA_001", False, "underscores as separators"),
        ("STA-WTP-GA-001-", False, "trailing separator"),
        ("STA-WTP-001-GA", False, "serial not last"),
        ("BASINWTP-GA-001", False, "8-char segment"),
        ("WTP-001", False, "only 1 segment before the serial"),
        ("AAA-BBB-CCC-DDD-EEE-001", False, "5 segments before the serial (max is 4)"),
        ("", False, "empty"),
        ("STA-WTP-GA-001 ", False, "trailing space; the stored ID is not stripped"),
    ],
)
def test_drawing_number_grammar(number: str, valid: bool, why: str):
    """Test 12. The whole table, both directions, against the built-in shape."""
    problems = SHAPE_ONLY.validate(number)
    if valid:
        assert problems == [], f"{number!r} should be valid ({why}): {problems}"
        matched = SHAPE_ONLY.match(number)
        assert matched["serial"] == number.rsplit("-", 1)[1]
        assert 2 <= len(matched) - 1 <= 4
    else:
        assert len(problems) == 1, f"{number!r} should be invalid ({why})"
        assert repr(number) in problems[0]
        assert "shape-only" in problems[0]
        with pytest.raises(NumberingError):
            SHAPE_ONLY.match(number)


def test_a_revision_suffix_in_the_number_points_at_meta_revision():
    """The stored number is the bare stable ID; ``_RevB`` is a display composition."""
    problems = SHAPE_ONLY.validate("STA-WTP-GA-001_RevB")
    assert len(problems) == 1
    assert "meta.revision" in problems[0]


def test_named_scheme_enforces_segment_vocabulary():
    """Test 13. Named segments, controlled vocabularies, and a required source."""
    scheme = NumberingScheme.from_dict(DEMO_WATER_V1, ctx="numbering.yaml")
    assert scheme.validate("STA-WTP-GA-001") == []
    assert scheme.match("STA-WTP-GA-001") == {
        "site": "STA",
        "facility": "WTP",
        "drawing_type": "GA",
        "serial": "001",
    }

    problems = scheme.validate("ZZZ-WTP-GA-001")
    assert len(problems) == 1
    assert "site" in problems[0]
    assert "STA, STB, STC, ARC" in problems[0]

    problems = scheme.validate("STA-WTP-XX-001")
    assert len(problems) == 1
    assert "drawing_type" in problems[0]

    sourceless = {k: v for k, v in DEMO_WATER_V1.items() if k != "source"}
    with pytest.raises(NumberingError, match="source is required"):
        NumberingScheme.from_dict(sourceless, ctx="numbering.yaml")


def test_a_scheme_can_be_loaded_from_a_file(tmp_path):
    path = tmp_path / "numbering.yaml"
    path.write_text(yaml.safe_dump({"numbering": DEMO_WATER_V1}), encoding="utf-8")
    scheme = NumberingScheme.load(path)
    assert scheme.id == "demo-water-v1"
    assert scheme.validate("STB-WTP-SEC-001") == []


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ({"serial_digits": 0}, "serial_digits must be a positive integer"),
        ({"serial_digits": True}, "serial_digits must be a positive integer"),
        ({"max_segments": 1, "min_segments": 2}, "max_segments must be >="),
        ({"typo": 1}, "unknown key"),
    ],
)
def test_a_malformed_scheme_config_names_the_field(mutation, message):
    raw = {**DEMO_WATER_V1, **mutation}
    with pytest.raises(NumberingError, match=message):
        NumberingScheme.from_dict(raw, ctx="numbering.yaml")


def test_an_unknown_key_in_a_segment_is_rejected():
    raw = {
        **DEMO_WATER_V1,
        "segments": [{"name": "site", "pattern": "[A-Z]{3}", "vocab": ["STA"]}],
    }
    with pytest.raises(NumberingError, match="unknown key\\(s\\): vocab"):
        NumberingScheme.from_dict(raw, ctx="numbering.yaml")


# --------------------------------------------------------------------------- #
# 14 — enforcement is strictly opt-in
# --------------------------------------------------------------------------- #


def _write(tmp_path, **overrides):
    data = {"number": "X-1", "title": "t", "revision": "A", "status": "CONCEPT"}
    data.update(overrides)
    path = tmp_path / "meta.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return DrawingMeta.load(path)


def test_numbering_enforcement_is_opt_in(tmp_path):
    """Test 14. **The compatibility test.** No grammar declared, no grammar enforced.

    A global regex would break ``tests/test_toolkit.py``'s ``X-1`` fixture on the
    day it merged — and, more importantly, would impose a convention the vault
    standard records as still *proposed*, whose two written examples disagree on
    field count.
    """
    off = _write(tmp_path)
    assert off.numbering_scheme() is None
    assert off.validate() == []

    on = _write(tmp_path, numbering="shape-only")
    problems = on.validate()
    assert len(problems) == 1, problems
    assert "'X-1'" in problems[0]
    assert "shape-only" in problems[0]


def test_any_non_empty_number_validates_with_no_grammar_configured():
    """Beyond ``X-1``: with no scheme declared, shape is simply not this tool's business."""
    for number in ("X-1", "sketch 3", "STA-WTP-GA-001_RevB", "1", "ARC-CSL-1"):
        assert DrawingMeta(number=number, title="t").validate() == []
    # Still required to be *present* — that check predates P9 and is unchanged.
    assert any("number" in p for p in DrawingMeta(number="", title="t").validate())


def test_an_inline_numbering_mapping_is_accepted_as_well_as_a_scheme_id(tmp_path):
    meta = _write(tmp_path, number="STA-WTP-GA-001", numbering=DEMO_WATER_V1)
    assert meta.validate() == []
    assert meta.numbering_scheme().id == "demo-water-v1"

    bad = _write(tmp_path, number="ZZZ-WTP-GA-001", numbering=DEMO_WATER_V1)
    problems = bad.validate()
    assert len(problems) == 1
    assert "demo-water-v1" in problems[0]


def test_an_unknown_builtin_scheme_name_fails_loudly(tmp_path):
    meta = _write(tmp_path, numbering="house-style-v2")
    problems = meta.validate()
    assert len(problems) == 1
    assert "is not a built-in scheme" in problems[0]
    assert "shape-only" in problems[0]


def test_the_builtin_scheme_cites_its_source():
    """A scheme with no citable source is a scheme somebody invented."""
    assert "Engineering Drawings as Code" in SHAPE_ONLY.source
    assert "PROPOSED" in SHAPE_ONLY.source
    assert SHAPE_ONLY.segments == (), "the built-in names no segments; the sources name none"
