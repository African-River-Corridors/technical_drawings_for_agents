"""Tests for the JSON Schema of the Drawing Format Standard (clause 18).

The valid and invalid example documents are files under docs/standard/schema/examples/. The
expected error of each invalid document is in `invalid/expected.json`. That file was written by
hand. It is never regenerated from the validator.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

jsonschema = pytest.importorskip("jsonschema")

DOCS = Path(__file__).resolve().parents[1] / "docs" / "standard"
SCHEMA_PATH = DOCS / "schema" / "drawing-document-1.0.schema.json"
SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
VALID = sorted((DOCS / "schema" / "examples" / "valid").glob("*.json"))
INVALID_DIR = DOCS / "schema" / "examples" / "invalid"
EXPECTED = json.loads((INVALID_DIR / "expected.json").read_text(encoding="utf-8"))
STANDARD = (DOCS / "drawing-format-standard.md").read_text(encoding="utf-8")
STYLES = json.loads((DOCS / "data" / "line-styles.json").read_text(encoding="utf-8"))

VALIDATOR = jsonschema.Draft202012Validator(SCHEMA)


def clause_numbers() -> set[str]:
    found = set(re.findall(r"^\*\*(\d+(?:\.\d+)*)[ *]", STANDARD, re.MULTILINE))
    return found | set(re.findall(r"^#{2,3} (\d+(?:\.\d+)*) ", STANDARD, re.MULTILINE))


def pointer(error) -> str:
    return "".join(f"/{part}" for part in error.absolute_path)


def walk(node):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from walk(value)


def test_the_schema_is_a_valid_draft_2020_12_schema():
    jsonschema.Draft202012Validator.check_schema(SCHEMA)
    assert SCHEMA["$schema"] == "https://json-schema.org/draft/2020-12/schema"


def test_every_x_clause_names_a_real_clause():
    numbers = clause_numbers()
    used = {node["x-clause"] for node in walk(SCHEMA) if "x-clause" in node}
    assert used
    assert used <= numbers, sorted(used - numbers)


def test_every_object_in_the_schema_rejects_unknown_keys():
    for node in walk(SCHEMA):
        if node.get("type") == "object" and "properties" in node and "allOf" not in node:
            assert node.get("additionalProperties") is False, node.get("x-clause")
    assert "unevaluatedProperties" not in json.dumps(SCHEMA)


# --------------------------------------------------------------------------- #
# valid documents
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("path", VALID, ids=lambda p: p.name)
def test_every_valid_example_validates(path):
    document = json.loads(path.read_text(encoding="utf-8"))
    errors = [f"{pointer(e)}: {e.message}" for e in VALIDATOR.iter_errors(document)]
    assert errors == []
    assert document["standard_version"] == "1.0"


def test_the_valid_examples_cover_every_sheet_size_and_every_entity_type():
    documents = [json.loads(p.read_text(encoding="utf-8")) for p in VALID]
    sizes = {d["sheet"]["size"] for d in documents}
    assert sizes == {"A0", "A1", "A2", "A3", "A4"}
    types = {e["type"] for d in documents for v in d["viewports"] for e in v["entities"]}
    assert types == {"line", "polyline", "arc", "circle", "centre_line", "hatch", "text", "dimension", "leader"}
    assert any(len(d["viewports"]) == 2 for d in documents)
    assert any("aci" in layer for d in documents for layer in d["layers"])


# --------------------------------------------------------------------------- #
# invalid documents
# --------------------------------------------------------------------------- #


def test_every_invalid_example_has_an_expected_error_and_the_reverse():
    files = {p.stem for p in INVALID_DIR.glob("*.json") if p.name != "expected.json"}
    assert files == set(EXPECTED)
    assert len(files) >= 25


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_every_invalid_example_fails_with_the_expected_error(name):
    document = json.loads((INVALID_DIR / f"{name}.json").read_text(encoding="utf-8"))
    expected = EXPECTED[name]
    errors = list(VALIDATOR.iter_errors(document))
    assert errors, f"{name} should be invalid"
    matches = [
        e
        for e in errors
        if pointer(e) == expected["path"]
        and e.validator == expected["keyword"]
        and expected["message_contains"] in e.message
    ]
    assert matches, [(pointer(e), e.validator, e.message) for e in errors]
    assert matches[0].schema.get("x-clause") == expected["clause"]


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_every_error_of_an_invalid_example_can_be_traced_to_a_clause(name):
    """The reader can say which clause failed: every failing schema node has an x-clause."""
    document = json.loads((INVALID_DIR / f"{name}.json").read_text(encoding="utf-8"))
    for error in VALIDATOR.iter_errors(document):
        assert error.schema.get("x-clause"), (pointer(error), error.validator)


# --------------------------------------------------------------------------- #
# the schema agrees with the standard
# --------------------------------------------------------------------------- #


def test_scale_values_equal_the_preferred_ratios_of_clause_7_3_1():
    enum = SCHEMA["$defs"]["viewport"]["properties"]["scale"]["enum"]
    preferred = [1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000, 50000, 100000]
    house = [25, 250, 1250, 2500]
    assert enum == [*sorted(preferred + house), "fit"]
    listed = re.search(r"^> (1, 2, 5, .*100000)$", STANDARD, re.MULTILINE).group(1)
    assert [int(x) for x in listed.split(",")] == preferred
    listed_house = re.search(r"^> (25, 250, 1250, 2500)$", STANDARD, re.MULTILINE).group(1)
    assert [int(x) for x in listed_house.split(",")] == house


def test_text_heights_equal_clause_11_2():
    enum = SCHEMA["$defs"]["entity_text"]["properties"]["height_mm"]["enum"]
    assert enum == [2.5, 3.5, 5, 7, 10, 14, 20]


def test_sheet_sizes_and_line_groups_equal_clauses_6_and_8():
    props = SCHEMA["properties"]["sheet"]["properties"]
    assert props["size"]["enum"] == ["A0", "A1", "A2", "A3", "A4"]
    assert props["line_group"]["enum"] == list(STYLES["line_groups"])


def test_layer_names_roles_and_line_types_equal_the_line_style_data():
    layer = SCHEMA["$defs"]["layer_declaration"]
    standard = layer["if"]["properties"]["name"]["not"]["enum"]
    assert standard == [row["name"] for row in STYLES["layers"]]
    assert SCHEMA["$defs"]["role"]["enum"] == STYLES["roles"]
    assert SCHEMA["$defs"]["line_type"]["enum"] == list(STYLES["line_types"])


def test_every_number_member_names_its_unit_clause_4_5():
    suffixes = ("_m", "_mm", "_deg")
    allowed_bare = {"divisions", "aci", "decimals"}  # counts and CAD colour numbers have no unit
    for node in walk(SCHEMA):
        for name, prop in node.get("properties", {}).items() if isinstance(node.get("properties"), dict) else []:
            if isinstance(prop, dict) and prop.get("type") in ("number", "integer"):
                assert name.endswith(suffixes) or name in allowed_bare, name
