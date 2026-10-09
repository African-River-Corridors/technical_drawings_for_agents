"""Deterministic emit, sidecar manifests, and the provenance stamp.

The determinism guarantee is asserted as **four independent properties**, never as
one test, because each has a different failure mode and a test that bundles them
cannot diagnose itself:

1. :func:`test_rebuild_in_place_is_byte_identical` — same inputs, same directory;
2. :func:`test_rebuild_after_a_time_gap_is_byte_identical` — no wall-clock leakage;
3. :func:`test_rebuild_in_a_different_directory_is_byte_identical` — relocatability,
   which is what a build identity keyed on input *paths* silently forfeits;
4. :func:`test_non_reproducible_formats_are_declared_not_asserted` — a format the
   tool cannot make reproducible is *declared*, never quietly asserted.

A format enters a byte-identity assertion only where the tool declares it
reproducible. No test pins a literal output digest: a hard-coded ``sha256`` of an
artifact cannot distinguish "we regressed" from "matplotlib updated". The single
exception is :func:`digest_preimage`, a pure function of declared inputs with no
dependency surface, whose byte-exact vector is what makes two independent
implementations agree.
"""

from __future__ import annotations

import copy
import hashlib
import json
import random
import re
import runpy
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml

from technical_drawings_for_agents import Drawing, DrawingMeta, ViewBox, svg_scale_bar
from technical_drawings_for_agents.cli import main
from technical_drawings_for_agents.provenance import (
    FORMAT_REPRODUCIBILITY,
    EmitPolicy,
    Manifest,
    ProvenanceError,
    digest_preimage,
    emit_digest,
    fmt,
    format_reproducibility,
    mask_dxf_volatiles,
    q,
    sha256_file,
    stamp_text,
    verify_digest,
    write_json_canonical,
    write_text_canonical,
)
from technical_drawings_for_agents.svg import svg_pattern_defs

REPO = Path(__file__).resolve().parents[1]
EXAMPLE = REPO / "drawings" / "example" / "simple-section"
GOLDENS = Path(__file__).resolve().parent / "goldens"

# A fixed epoch (2016-01-01Z) so the reference build never consults the wall clock.
EPOCH = 1451606400

# The one pinned vector in this suite: digest_preimage is a pure function of the
# manifest's declared fields, so it has no dependency surface to drift under.
# Note the pinned manifest lists its inputs in PATH order while the preimage
# emits them in CONTENT order — that inversion is the point (see Correction 2).
# Re-pinned once, 2026-10-09, when the package took its current name: the
# schema and tool name are in the preimage, so the rename moved the digest.
PINNED_PREIMAGE = (
    b'{"build":{"node":null},"inputs":[{"bytes":34,'
    b'"sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},{"bytes":12,'
    b'"sha256":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}],'
    b'"params":{"label":"\xc3\x98\xe2\x80\x941","ratio":0.125,"snap":true,"view":"plan"},'
    b'"policy":{"fixed_dxf_metadata":true,"normalise_negative_zero":true,"precision_deg":3,'
    b'"precision_font":2,"precision_m":3,"precision_mm":2,"precision_px":1,'
    b'"precision_ratio":3,"source_date_epoch":1451606400,"trailing_newline":true},'
    b'"schema":"technical_drawings_for_agents/manifest@1","target":{"drawing_number":"GA-001",'
    b'"format":"svg","name":"GA-001.svg","revision":"B"},'
    b'"tool":{"name":"technical_drawings_for_agents","version":"0.1.0"}}'
)
PINNED_DIGEST = "cce9ecba97e1ce43249f7479d8a40d1b25b629799c319b2def0ac55bc821c8a9"

# Every artifact of the reference build, and whether the tool declares its bytes
# reproducible. The split IS the deliverable, not a caveat.
REPRODUCIBLE_ARTIFACTS = (
    "EXA-CIV-SEC-001.svg",
    "EXA-CIV-SEC-001.dxf",
    "EXA-CIV-SEC-001.pdf",   # matplotlib, with source_date_epoch resolved
    "EXA-CIV-SEC-001.png",   # matplotlib
    "layout.geojson",
    "placements.yaml",
    "layout.dot",
)


def _policy() -> EmitPolicy:
    return EmitPolicy(source_date_epoch=EPOCH)


def _meta() -> DrawingMeta:
    return DrawingMeta(
        number="TST-CIV-GA-001",
        title="Provenance Test Sheet",
        revision="A",
        status="CONCEPT",
        for_construction=False,
    )


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --------------------------------------------------------------------------- #
# the reference opted-in build
# --------------------------------------------------------------------------- #


def _build_reference(work: Path, root: Path | None = None) -> dict[str, Path]:
    """Build every artifact class the toolkit emits, fully opted in.

    ``root`` defaults to ``work``; passing a *shallower* root changes every
    declared input path without changing a byte of input content, which is how
    :func:`test_rebuild_in_a_different_directory_is_byte_identical` proves the
    digest is keyed on content rather than location.
    """

    from technical_drawings_for_agents.render import render_dxf

    shutil.copytree(EXAMPLE, work)
    manifest_root = Path(root) if root is not None else work
    out = work / "out"
    policy = _policy()

    source_py = work / "source.py"
    module = runpy.run_path(str(source_py))
    cfg = module["load_inputs"]()["channel"]
    meta = DrawingMeta.load(work / "meta.yaml")
    stem = meta.number
    declared = [work / "inputs.yaml", work / "meta.yaml", source_py]

    svg_path = out / f"{stem}.svg"
    svg_manifest = Manifest.plan(
        target=svg_path, inputs=declared, root=manifest_root, policy=policy, meta=meta
    )
    write_text_canonical(
        svg_path,
        module["build_svg"](meta, cfg, policy=policy, provenance_stamp=svg_manifest.stamp_text),
        policy,
    )
    svg_manifest.with_output(svg_path).write()

    dxf_path = out / f"{stem}.dxf"
    dxf_manifest = Manifest.plan(
        target=dxf_path, inputs=declared, root=manifest_root, policy=policy, meta=meta
    )
    module["build_dxf"](
        cfg, policy=policy, provenance_stamp=dxf_manifest.stamp_text
    ).save(dxf_path)
    dxf_manifest.with_output(dxf_path).write()

    # matplotlib PDF + PNG, with SOURCE_DATE_EPOCH resolved from the policy.
    for rendered in render_dxf(dxf_path, out, stem=stem, backend="matplotlib", policy=policy):
        Manifest.plan(
            target=rendered, inputs=declared, root=manifest_root, policy=policy, meta=meta
        ).with_output(rendered).write()

    write_json_canonical(
        out / "layout.geojson",
        {"type": "FeatureCollection", "features": [{"x": policy.q(0.1 + 0.2, 3)}]},
        policy,
    )
    write_text_canonical(
        out / "placements.yaml",
        yaml.safe_dump({"instances": [{"origin_utm": [1.0, 2.0]}]}, sort_keys=False),
        policy,
    )
    write_text_canonical(out / "layout.dot", "digraph G {\n  A -> B\n}", policy)

    return {name: out / name for name in REPRODUCIBLE_ARTIFACTS}


def _bytes_of(artifacts: dict[str, Path]) -> dict[str, bytes]:
    return {name: path.read_bytes() for name, path in artifacts.items()}


def _svg_manifest(artifacts: dict[str, Path]) -> dict:
    path = Manifest.path_for(artifacts["EXA-CIV-SEC-001.svg"])
    return json.loads(path.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# CORRECTION 1 — the determinism guarantee, decomposed into four properties
# --------------------------------------------------------------------------- #


def test_rebuild_in_place_is_byte_identical(tmp_path):
    """Property 1: same inputs, same directory, immediately. The floor."""

    first = _bytes_of(_build_reference(tmp_path / "work"))
    shutil.rmtree(tmp_path / "work")
    second = _bytes_of(_build_reference(tmp_path / "work"))

    assert set(second) == set(REPRODUCIBLE_ARTIFACTS)
    for name in REPRODUCIBLE_ARTIFACTS:
        assert first[name], f"{name}: built empty — an equality of nothings is worse than no test"
        assert second[name] == first[name], f"{name}: rebuild in place changed bytes"


def test_rebuild_after_a_time_gap_is_byte_identical(tmp_path):
    """Property 2: no wall-clock leakage.

    The gap is > 1 s so that ezdxf's ``$TDCREATE``/``$TDUPDATE`` (Julian days at
    ~1e-5 resolution), its two per-save GUIDs, and matplotlib's ``/CreationDate``
    would all differ if any of them reached the bytes.
    """

    first = _bytes_of(_build_reference(tmp_path / "before"))
    time.sleep(1.1)
    second = _bytes_of(_build_reference(tmp_path / "after"))

    for name in REPRODUCIBLE_ARTIFACTS:
        assert second[name] == first[name], f"{name}: a wall clock reached the bytes"


def test_rebuild_in_a_different_directory_is_byte_identical(tmp_path):
    """Property 3: relocatability — a build's identity must not depend on where it ran.

    The two builds declare the *same input content* under **different relative
    paths** ("inputs.yaml" versus "nested/proj/inputs.yaml"), because the manifest
    root sits at a different depth. A digest that hashed input paths would differ
    here — so CI and a laptop could never agree on a stamp, and moving a project
    directory would make every artifact in it look stale.
    """

    shallow = _build_reference(tmp_path / "alpha")
    deep_root = tmp_path / "beta"
    (deep_root / "nested").mkdir(parents=True)
    deep = _build_reference(deep_root / "nested" / "proj", root=deep_root)

    shallow_manifest = _svg_manifest(shallow)
    deep_manifest = _svg_manifest(deep)

    # the declared paths genuinely differ, or this test proves nothing
    shallow_paths = [item["path"] for item in shallow_manifest["inputs"]]
    deep_paths = [item["path"] for item in deep_manifest["inputs"]]
    assert shallow_paths == ["inputs.yaml", "meta.yaml", "source.py"]
    assert deep_paths == [
        "nested/proj/inputs.yaml",
        "nested/proj/meta.yaml",
        "nested/proj/source.py",
    ]
    # ...while the content is identical
    assert [item["sha256"] for item in shallow_manifest["inputs"]] == [
        item["sha256"] for item in deep_manifest["inputs"]
    ]

    assert deep_manifest["build"]["digest"] == shallow_manifest["build"]["digest"]
    assert deep_manifest["stamp"]["text"] == shallow_manifest["stamp"]["text"]
    assert deep_manifest["build"]["root"] != shallow_manifest["build"]["root"]

    first, second = _bytes_of(shallow), _bytes_of(deep)
    for name in REPRODUCIBLE_ARTIFACTS:
        assert second[name] == first[name], f"{name}: not relocatable"


def test_non_reproducible_formats_are_declared_not_asserted(tmp_path):
    """Property 4: what the tool cannot promise, it declares — and refuses to digest.

    LibreOffice writes a time-derived ``/CreationDate`` *and* a ``/ID`` pair into
    every PDF and ignores ``SOURCE_DATE_EPOCH``, so those bytes cannot be made
    reproducible without shipping a hand-rolled PDF mutator on the path producing
    the artifact an engineer signs against. The tool says so instead.
    """

    assert format_reproducibility("pdf").reproducible is False
    assert format_reproducibility(".PDF").reason == (
        "libreoffice-pdf-embeds-creationdate-and-docid"
    )

    work = tmp_path / "lo"
    work.mkdir()
    source = work / "sheet.svg"
    source.write_text("<svg/>\n", encoding="utf-8")
    pdf = work / "sheet.pdf"
    pdf.write_bytes(b"%PDF-1.4 pretend-libreoffice-output\n")

    manifest = (
        Manifest.plan(
            target=pdf,
            inputs=[source],
            root=work,
            policy=_policy(),
            reproducible=False,
            reason="libreoffice-pdf-embeds-creationdate-and-docid",
        )
        .with_output(pdf)
    )
    manifest.write()

    data = json.loads(Manifest.path_for(pdf).read_text(encoding="utf-8"))
    assert data["output"]["reproducible"] is False
    assert data["output"]["reason"] == "libreoffice-pdf-embeds-creationdate-and-docid"

    # declared, and therefore never silently digested
    with pytest.raises(ProvenanceError, match="non-reproducible"):
        emit_digest(pdf)

    # --check surfaces it as a warning and still exits 0: an honest
    # non-reproducible PDF is more useful than no PDF.
    assert main(["manifest", str(pdf), "--check"]) == 0

    # and no reproducibility claim is invented for a format nobody measured
    with pytest.raises(ProvenanceError, match="no reproducibility declaration"):
        format_reproducibility("dwg")


# --------------------------------------------------------------------------- #
# CORRECTION 2 — the digest is keyed on content, never on location
# --------------------------------------------------------------------------- #


def _pinned_manifest() -> dict:
    return {
        "schema": "technical_drawings_for_agents/manifest@1",
        "target": {
            "name": "GA-001.svg",
            "format": "svg",
            "drawing_number": "GA-001",
            "revision": "B",
        },
        "inputs": [
            {"path": "drawings/a.yaml", "sha256": "b" * 64, "bytes": 12, "tracked": True},
            {"path": "drawings/Ø/input.yaml", "sha256": "a" * 64, "bytes": 34, "tracked": False},
        ],
        "params": {"label": "Ø—1", "ratio": 0.125, "snap": True, "view": "plan"},
        "policy": {
            "precision_deg": 3,
            "precision_font": 2,
            "precision_m": 3,
            "precision_mm": 2,
            "precision_px": 1,
            "precision_ratio": 3,
            "normalise_negative_zero": True,
            "fixed_dxf_metadata": True,
            "trailing_newline": True,
            "source_date_epoch": EPOCH,
        },
        "tool": {"name": "technical_drawings_for_agents", "version": "0.1.0", "commit": "c" * 40, "dirty": False},
        "build": {
            "digest": PINNED_DIGEST,
            "root": "/tmp/root",
            "root_vcs": {"kind": "git", "commit": "d" * 40, "dirty": True},
            "node": None,
        },
        "output": {"sha256": "e" * 64, "bytes": 56, "reproducible": True, "reason": None},
        "stamp": {"text": "P:5bdcd015+", "placement": "below-title-block"},
        "environment": {
            "host": "alpha",
            "user": "beta",
            "platform": "test",
            "python": "3.12.12",
            "libraries": {},
            "generated_at": "2026-07-24T22:16:06Z",
        },
    }


def test_digest_matches_the_pinned_cross_implementation_vector():
    """The one pinned vector: key order, separators and real-UTF-8 handling, exactly."""

    manifest = _pinned_manifest()

    assert digest_preimage(manifest) == PINNED_PREIMAGE
    assert Manifest.from_dict(manifest).digest == PINNED_DIGEST
    assert verify_digest(manifest) is True
    # ensure_ascii=False, not \uXXXX escapes
    assert "Ø—1".encode() in PINNED_PREIMAGE


def test_the_digest_hashes_input_content_and_ignores_input_paths():
    """Correction 2, pinned: content decides identity, location is only recorded.

    A build's identity must not depend on where it was built. If it did, a local
    build and a CI build of identical inputs would print different stamps (so the
    stamp could never be cross-checked), and moving a project directory would
    make every artifact in it appear stale.
    """

    manifest = _pinned_manifest()
    assert b"drawings/a.yaml" not in digest_preimage(manifest)
    assert b"path" not in digest_preimage(manifest)

    relocated = copy.deepcopy(manifest)
    for index, new_path in enumerate(("elsewhere/x.yaml", "z/deeply/nested/y.yaml")):
        relocated["inputs"][index]["path"] = new_path
    assert verify_digest(relocated) is True

    # ...and content still decides
    changed = copy.deepcopy(manifest)
    changed["inputs"][0]["sha256"] = "2" * 64
    assert verify_digest(changed) is False
    resized = copy.deepcopy(manifest)
    resized["inputs"][0]["bytes"] = 13
    assert verify_digest(resized) is False


def test_the_digest_answers_what_went_in_not_who_built_it():
    """Environment, root, tool commit, dirty flags and output hash are all excluded."""

    manifest = _pinned_manifest()

    ignored = [
        ("environment", "host", "omega"),
        ("environment", "generated_at", "2030-01-01T00:00:00Z"),
        ("build", "root", "/different/root"),
        ("tool", "commit", "f" * 40),
        ("tool", "dirty", True),
        ("output", "sha256", "1" * 64),
    ]
    for block, key, value in ignored:
        changed = copy.deepcopy(manifest)
        changed[block][key] = value
        assert verify_digest(changed) is True, f"{block}.{key} must not change the digest"
    changed = copy.deepcopy(manifest)
    changed["inputs"][0]["tracked"] = False
    assert verify_digest(changed) is True

    included = [
        ("params", None, "view", "section"),
        ("policy", None, "precision_m", 2),
        ("tool", None, "version", "0.1.1"),
        ("target", None, "name", "GA-002.svg"),
        ("target", None, "revision", "C"),
        ("build", None, "node", "layout:ga"),
    ]
    for block, index, key, value in included:
        changed = copy.deepcopy(manifest)
        changed[block][key] = value
        assert verify_digest(changed) is False, f"{block}.{key} must change the digest"


def test_changing_one_input_byte_changes_the_manifest_digest_and_the_stamp(tmp_path):
    source = tmp_path / "inputs.yaml"
    source.write_text("bed_width_m: 2.0\n", encoding="utf-8")
    old = _build_svg_with_manifest(tmp_path, tmp_path / "old" / "sheet.svg", source)

    source.write_text("bed_width_m: 2.001\n", encoding="utf-8")  # a real 1 mm change
    new = _build_svg_with_manifest(tmp_path, tmp_path / "new" / "sheet.svg", source)

    assert new.digest != old.digest
    assert new.stamp_text != old.stamp_text
    svg = (tmp_path / "new" / "sheet.svg").read_text(encoding="utf-8")
    assert new.stamp_text in svg
    assert old.stamp_text not in svg
    changed = [
        (before, after)
        for before, after in zip(old.inputs, new.inputs)
        if before.sha256 != after.sha256
    ]
    assert len(changed) == 1


def test_rebuilding_at_a_later_wall_clock_changes_only_the_manifest_timestamp(tmp_path):
    """``environment.generated_at`` is a wall clock inside a file about determinism.

    That is deliberate and safe — the manifest is never an input to a digest and
    never rendered into a drawing — and this test is what keeps it safe.
    """

    source = tmp_path / "inputs.yaml"
    source.write_text("bed_width_m: 2.0\n", encoding="utf-8")

    first = _build_svg_with_manifest(tmp_path, tmp_path / "first" / "sheet.svg", source)
    first_bytes = (tmp_path / "first" / "sheet.svg").read_bytes()
    time.sleep(1.1)
    second = _build_svg_with_manifest(tmp_path, tmp_path / "second" / "sheet.svg", source)

    assert first.environment["generated_at"] != second.environment["generated_at"]
    assert first.digest == second.digest
    assert first.stamp_text == second.stamp_text
    assert first.output_sha256 == second.output_sha256
    assert first_bytes == (tmp_path / "second" / "sheet.svg").read_bytes()


# --------------------------------------------------------------------------- #
# CORRECTION 3 — text writes are UTF-8, LF, no BOM, and never escaped
# --------------------------------------------------------------------------- #


def test_the_register_writes_non_ascii_as_real_utf8_not_escapes(tmp_path):
    """``yaml.safe_dump`` defaults to ``allow_unicode=False`` — which escaped the register.

    A free-form property reading "Décanteur Ø—1" landed on disk as
    ``"D\\xE9canteur \\xD8\\u20141"``. An escaped register is not human-reviewable,
    which defeats the entire text-as-truth rationale for generating one, and this
    is live risk: Ghanaian and Portuguese names and descriptions carry accents
    routinely.
    """

    from technical_drawings_for_agents.components.layout import canonical_instances, dump_placements_register

    instances = canonical_instances(
        [
            {
                "type": "clarifier",
                "origin_utm": [788416.062, 322373.5],
                "rotation_deg": 38.876,
                "tag": "STA-A",
                "note": "Décanteur Ø—1",
                "operator": "Nazaire Bâ",
            }
        ]
    )
    text = dump_placements_register(instances, source="Ø-export.geojson")

    assert "note: Décanteur Ø—1" in text
    assert "operator: Nazaire Bâ" in text
    assert "\\x" not in text and "\\u" not in text

    register = tmp_path / "placements.yaml"
    register.write_text(text, encoding="utf-8", newline="\n")
    raw = register.read_bytes()
    assert "Décanteur Ø—1".encode() in raw
    assert b"\r" not in raw
    assert not raw.startswith(b"\xef\xbb\xbf")
    # and it still parses back to the same values
    parsed = yaml.safe_load(register.read_text(encoding="utf-8"))
    assert parsed["instances"][0]["note"] == "Décanteur Ø—1"


def test_a_register_round_trips_non_ascii_through_the_cli_unescaped(tmp_path):
    """End-to-end: the effective-register write path keeps real UTF-8 bytes."""

    spec = (
        REPO / "src" / "technical_drawings_for_agents" / "components" / "examples" / "packaged_unit.yaml"
    ).read_text(encoding="utf-8")
    root = tmp_path / "components"
    root.mkdir()
    (root / "unit.yaml").write_text(spec, encoding="utf-8")

    register = tmp_path / "placements.yaml"
    register.write_text(
        yaml.safe_dump(
            {
                "instances": [
                    {
                        "type": "unit",
                        "origin_utm": [0.0, 0.0],
                        "rotation_deg": 0.0,
                        "note": "Décanteur Ø—1",
                    }
                ]
            },
            sort_keys=False,
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    config = tmp_path / "layout.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "layout": {"id": "ENC", "crs": "EPSG:32630"},
                "components": {"root": "components", "types": {"unit": ["unit.yaml"]}},
                "placements": "placements.yaml",
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    effective = tmp_path / "effective.yaml"
    assert main(["layout", str(config), "--emit-register", str(effective)]) == 0

    raw = effective.read_bytes()
    assert "Décanteur Ø—1".encode() in raw
    assert b"\\x" not in raw and b"\\u" not in raw
    assert b"\r" not in raw
    assert not raw.startswith(b"\xef\xbb\xbf")


def test_text_writes_are_utf8_lf_regardless_of_locale(tmp_path):
    from technical_drawings_for_agents import bfd

    data = {
        "meta": {
            "number": "BFD-001",
            "title": "Ø test — profile",
            "status": "CONCEPT — NOT FOR CONSTRUCTION",
            "watermark": "CONCEPT",
        },
        "lanes": [{"id": "a", "label": "Ø Lane"}],
        "nodes": [{"id": "n1", "type": "process", "label": "Ø Node", "lane": "a"}],
        "edges": [],
    }
    data_path = tmp_path / "bfd.yaml"
    data_path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    out = bfd.build(data_path, tmp_path / "out", view="swimlane")[0]

    raw = out.read_bytes()
    assert raw.decode("utf-8")
    assert "Ø Node".encode() in raw
    assert b"\r" not in raw
    assert not raw.startswith(b"\xef\xbb\xbf")

    # D3's parity claim: adding newline="\n" is byte-neutral on this platform
    reference = tmp_path / "reference.svg"
    reference.write_text(out.read_text(encoding="utf-8"), encoding="utf-8")
    assert reference.read_bytes() == raw


def test_write_text_canonical_normalises_newlines_and_the_trailing_newline(tmp_path):
    path = tmp_path / "a" / "b.svg"
    written = write_text_canonical(path, "one\r\ntwo\rthree\n\n\n", _policy())

    assert written.read_bytes() == b"one\ntwo\nthree\n"
    assert not list(path.parent.glob("*.tmp"))  # atomic: no temp file left behind

    no_trailing = EmitPolicy(source_date_epoch=EPOCH, trailing_newline=False)
    assert write_text_canonical(tmp_path / "c.svg", "x", no_trailing).read_bytes() == b"x"


# --------------------------------------------------------------------------- #
# CORRECTION 4 / P10 interface — emit_digest
# --------------------------------------------------------------------------- #


def test_emit_digest_hashes_bytes_for_a_reproducible_format(tmp_path):
    path = tmp_path / "sheet.svg"
    path.write_bytes(b"<svg/>\n")
    assert emit_digest(path) == sha256_file(path)[0]
    assert re.fullmatch(r"[0-9a-f]{64}", emit_digest(path))


def test_emit_digest_masks_dxf_container_noise_so_it_digests_the_drawing(tmp_path):
    """A DXF written *without* fixed metadata still has a stable drawing identity.

    ezdxf re-rolls two GUIDs and rewrites ``$TDCREATE``/``$TDUPDATE`` and its own
    marker string on every save (measured: 6 differing lines of ~13.5k). Masking
    those six items is what lets ``emit_digest`` answer "is this the same
    drawing?" rather than "was this saved at the same instant?".
    """

    pytest.importorskip("ezdxf")
    from technical_drawings_for_agents.dxf import DxfBuilder

    def build(path: Path) -> Path:
        builder = DxfBuilder(units="m")          # NO policy: raw ezdxf metadata
        builder.polyline([(0.0, 0.0), (2.0, 0.0), (2.0, 1.0)], closed=True)
        builder.text((0.0, 1.5), "SECTION A-A", height=0.12)
        return builder.save(path)

    first = build(tmp_path / "one.dxf")
    time.sleep(1.1)
    second = build(tmp_path / "two.dxf")

    assert first.read_bytes() != second.read_bytes()   # the container noise is real
    assert emit_digest(first) == emit_digest(second)   # the drawing is not

    masked = mask_dxf_volatiles(first.read_text(encoding="utf-8"))
    for placeholder in ("<TDCREATE>", "<TDUPDATE>", "<FINGERPRINTGUID>", "<VERSIONGUID>"):
        assert placeholder in masked
    assert "<EZDXF-MARKER>" in masked

    # a real geometry change still moves the digest
    changed = DxfBuilder(units="m")
    changed.polyline([(0.0, 0.0), (2.5, 0.0), (2.5, 1.0)], closed=True)
    changed.text((0.0, 1.5), "SECTION A-A", height=0.12)
    changed.save(tmp_path / "three.dxf")
    assert emit_digest(tmp_path / "three.dxf") != emit_digest(first)


def test_the_dxf_classes_section_is_ordered_rather_than_left_to_a_set(tmp_path):
    """The second, deeper source of DXF non-determinism — in ezdxf, not in our code.

    ``ClassesSection.add_required_classes()`` ends by iterating
    ``entitydb.dxf_types_in_use()``, a **set**, into the ordered CLASSES registry.
    Its record order therefore varies with ``PYTHONHASHSEED`` and with the set's
    insertion history, so two machines emit the same drawing with different bytes
    even with ``write_fixed_meta_data_for_testing`` set. Fixed metadata alone is
    *not* sufficient for DXF byte-identity, which is why this exists.

    Under a policy the registry is sorted at source, so the bytes are reproducible;
    ``mask_dxf_volatiles`` normalises the same ordering for a DXF whose writer did
    not opt in, so ``emit_digest`` is stable either way.
    """

    pytest.importorskip("ezdxf")
    import ezdxf

    from technical_drawings_for_agents.dxf import DxfBuilder

    def build(path: Path, policy) -> Path:
        builder = DxfBuilder(units="m", policy=policy)
        builder.polyline([(0.0, 0.0), (2.0, 0.0), (2.0, 1.0)], closed=True)
        builder.text((0.0, 1.5), "SECTION A-A", height=0.12)
        builder.linear_dim((0.0, 0.0), (2.0, 0.0), distance=-0.4)
        builder.circle((1.0, 0.5), 0.2)
        return builder.save(path)

    opted_in = build(tmp_path / "sorted.dxf", _policy())
    names = _dxf_class_names(opted_in.read_text(encoding="utf-8"))
    assert len(names) > 5
    assert names == sorted(names), "the CLASSES section is not in a determined order"

    # ...and the file is still a valid, complete DXF, not merely a stable one
    doc = ezdxf.readfile(opted_in)
    assert doc.audit().errors == []
    assert len(list(doc.modelspace())) == 4
    assert len(doc.classes.classes) == len(names)

    # the default path is untouched, and masking normalises it for comparison
    default = build(tmp_path / "default.dxf", None)
    masked = mask_dxf_volatiles(default.read_text(encoding="utf-8"))
    assert _dxf_class_names(masked) == sorted(_dxf_class_names(masked))
    assert mask_dxf_volatiles(masked) == masked          # idempotent
    assert masked.count("  0\nCLASS\n") == len(names)    # no record lost or glued


def _dxf_class_names(text: str) -> list[str]:
    section = re.search(
        r"  0\nSECTION\n  2\nCLASSES\n(.*?)  0\nENDSEC\n", text, re.DOTALL
    )
    assert section, "no CLASSES section"
    return re.findall(r"  0\nCLASS\n  1\n([^\n]+)\n", section.group(1))


def test_emit_digest_raises_rather_than_returning_a_meaningless_value(tmp_path):
    """Returning *something* for a non-reproducible format is the trap being closed.

    A caller handed a value would assert byte-identity on a LibreOffice PDF and
    believe the result either way. Raising is the only honest answer.
    """

    pdf = tmp_path / "sheet.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    with pytest.raises(ProvenanceError) as raised:
        emit_digest(pdf)
    message = str(raised.value)
    assert "non-reproducible" in message
    assert "libreoffice-pdf-embeds-creationdate-and-docid" in message
    assert "byte-identity" in message

    unknown = tmp_path / "sheet.dwg"
    unknown.write_bytes(b"AC1032")
    with pytest.raises(ProvenanceError, match="no reproducibility declaration"):
        emit_digest(unknown)

    missing = tmp_path / "absent.svg"
    with pytest.raises(ProvenanceError, match="is not a file"):
        emit_digest(missing)


def test_a_sidecar_manifest_is_the_authority_on_what_a_format_promised(tmp_path):
    """Only the build knows which backend wrote the bytes; the manifest records it."""

    source = tmp_path / "in.yaml"
    source.write_text("x: 1\n", encoding="utf-8")

    # matplotlib PDF with a resolved epoch: the build declares it reproducible,
    # which promotes a format whose default declaration is not.
    good = tmp_path / "good.pdf"
    good.write_bytes(b"%PDF-1.4 matplotlib\n")
    Manifest.plan(
        target=good, inputs=[source], root=tmp_path, policy=_policy()
    ).with_output(good).write()
    assert emit_digest(good) == sha256_file(good)[0]

    # and a manifest declaring a normally-fine format non-reproducible still raises
    bad = tmp_path / "bad.svg"
    bad.write_bytes(b"<svg/>\n")
    Manifest.plan(
        target=bad,
        inputs=[source],
        root=tmp_path,
        policy=_policy(),
        reproducible=None,
        reason="unmeasured",
    ).with_output(bad).write()
    with pytest.raises(ProvenanceError, match="unmeasured"):
        emit_digest(bad)


def test_a_pdf_with_no_resolvable_build_timestamp_declares_itself(tmp_path):
    """No epoch means matplotlib writes its own wall clock — declared, not invented."""

    source = tmp_path / "in.yaml"
    source.write_text("x: 1\n", encoding="utf-8")
    pdf = tmp_path / "out.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")

    manifest = Manifest.plan(
        target=pdf, inputs=[source], root=tmp_path, policy=EmitPolicy()
    )
    assert manifest.policy.source_date_epoch is None
    assert manifest.reproducible is False
    assert manifest.reason == "no-source-date-epoch"


# --------------------------------------------------------------------------- #
# formatting primitives
# --------------------------------------------------------------------------- #


def test_fmt_and_q_agree_and_are_platform_independent():
    values = [
        788416.1234999,
        322373.5555,
        1e-5,
        -1e-5,
        -0.0001,
        0.0625,
        0.3125,
        2.0625,
        -0.0625,
        0.1 + 0.2,
        -999999.9995,
        5.0,
    ]
    rng = random.Random(20260724)
    values.extend(rng.uniform(-1_000_000, 1_000_000) for _ in range(20_000))
    for value in values:
        for decimals in (1, 2, 3):
            text = fmt(value, decimals)
            assert float(text) == q(value, decimals)
            assert re.match(rf"^-?\d+\.\d{{{decimals}}}$", text)
            assert not re.match(r"^-0\.0*$", text)
    assert fmt(5.0, 3) == "5.000"          # always fixed-point, never 5 or 5e0
    assert q(-0.0001, 3) == 0.0
    assert repr(q(-0.0001, 3)) == "0.0"    # not "-0.0": sign of zero is pure noise


def test_rounding_ties_use_half_even_not_half_up():
    """The most plausible divergence between two implementers, pinned.

    ``Decimal(v).quantize(Decimal("0.001"), ROUND_HALF_UP)`` looks equally
    principled and produces ``0.063`` for every row below. Ties at mm precision
    are odd multiples of 1/16 m — a 2.0625 m offset is real work, not a corner.
    """

    for value, expected in (
        (0.0625, "0.062"),
        (0.3125, "0.312"),
        (2.0625, "2.062"),
        (788416.0625, "788416.062"),
        (-0.0625, "-0.062"),
    ):
        assert fmt(value, 3) == expected
        assert q(value, 3) == float(expected)


def test_non_finite_and_unserialisable_values_fail_loudly(tmp_path):
    with pytest.raises(ProvenanceError, match="non-finite value nan") as nan:
        fmt(float("nan"), 3)
    assert "skip" not in str(nan.value).lower()
    with pytest.raises(ProvenanceError, match="non-finite value inf") as inf:
        q(float("inf"), 3)
    assert "skip" not in str(inf.value).lower()

    source = tmp_path / "input.txt"
    source.write_text("x", encoding="utf-8")
    with pytest.raises(ProvenanceError, match="non-finite value nan"):
        Manifest.plan(
            target=tmp_path / "out.json",
            inputs=[source],
            root=tmp_path,
            params={"x": float("nan")},
            policy=_policy(),
        )
    with pytest.raises(ProvenanceError, match="not JSON-serialisable"):
        Manifest.plan(
            target=tmp_path / "out.json",
            inputs=[source],
            root=tmp_path,
            params={"x": object()},
            policy=_policy(),
        )
    assert not Manifest.path_for(tmp_path / "out.json").exists()


def test_an_emit_policy_validates_on_construction_and_names_the_field():
    with pytest.raises(ProvenanceError, match=r"EmitPolicy\.precision_m must be an int in 0\.\.9"):
        EmitPolicy(precision_m=3.5)
    with pytest.raises(ProvenanceError, match=r"EmitPolicy\.precision_deg must be an int in 0\.\.9"):
        EmitPolicy(precision_deg=10)
    with pytest.raises(ProvenanceError, match="non-negative int"):
        EmitPolicy(source_date_epoch="1451606400")
    with pytest.raises(ProvenanceError, match=r"EmitPolicy\.trailing_newline must be a bool"):
        EmitPolicy(trailing_newline=1)


def test_source_date_epoch_is_read_in_exactly_one_place(monkeypatch):
    monkeypatch.setenv("SOURCE_DATE_EPOCH", " 1451606400 ")
    assert EmitPolicy.from_environment().source_date_epoch == EPOCH
    # a plain constructor never consults the environment
    assert EmitPolicy().source_date_epoch is None

    monkeypatch.setenv("SOURCE_DATE_EPOCH", "yesterday")
    with pytest.raises(ProvenanceError, match="SOURCE_DATE_EPOCH must be a non-negative integer"):
        EmitPolicy.from_environment()


def test_set_of_hatch_patterns_is_rejected_rather_than_ordered_arbitrarily():
    with pytest.raises(ValueError, match="PYTHONHASHSEED"):
        svg_pattern_defs(patterns={"concrete", "water"})
    ordered = svg_pattern_defs(patterns=["water", "concrete"])
    assert ordered.index('id="hatch-water"') < ordered.index('id="hatch-concrete"')


# --------------------------------------------------------------------------- #
# the stamp, git provenance, and the ISSUED gate
# --------------------------------------------------------------------------- #


def _build_svg_with_manifest(root: Path, out: Path, source: Path, policy: EmitPolicy | None = None):
    policy = policy or _policy()
    meta = _meta()
    manifest = Manifest.plan(
        target=out,
        inputs=[source],
        root=root,
        policy=policy,
        meta=meta,
        params={"view": "test"},
    )
    vb = ViewBox(0, 10, 0, 5, 500, 320)
    drawing = Drawing(
        500,
        320,
        vb,
        title_block=meta.title_block(),
        status=meta.status_key,
        policy=policy,
        provenance_stamp=manifest.stamp_text,
    )
    drawing.add(svg_scale_bar(vb, 0, 0, 5, policy=policy))
    write_text_canonical(out, drawing.render(), policy)
    manifest = manifest.with_output(out)
    manifest.write()
    return manifest


def _build_json_with_manifest(root: Path, out: Path, source: Path):
    policy = _policy()
    manifest = Manifest.plan(
        target=out,
        inputs=[source],
        root=root,
        policy=policy,
        params={"view": "plan"},
    )
    write_json_canonical(out, {"type": "FeatureCollection", "features": []}, policy)
    manifest = manifest.with_output(out)
    manifest.write()
    return manifest


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _git_commit_all(cwd: Path, message: str) -> None:
    subprocess.run(["git", "add", "."], cwd=cwd, check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-m",
            message,
        ],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


def test_the_stamp_on_the_sheet_equals_its_manifest_digest_prefix(tmp_path):
    source = tmp_path / "inputs.yaml"
    source.write_text("x: 1\n", encoding="utf-8")
    manifest = _build_svg_with_manifest(tmp_path, tmp_path / "sheet.svg", source)
    data = manifest.to_dict()

    expected = "P:" + data["build"]["digest"][:8]
    if data["build"]["root_vcs"]["dirty"] or data["tool"]["dirty"]:
        expected += "+"

    svg = (tmp_path / "sheet.svg").read_text(encoding="utf-8")
    assert 'class="provenance-stamp"' in svg
    assert expected in svg
    assert data["stamp"]["text"] == expected
    assert data["stamp"]["placement"] == "below-title-block"
    # the stamp is a hash, never a word that could read as an approval
    for word in ("approved", "checked", "issued", "signed"):
        assert word not in expected.lower()


def test_a_dirty_input_tree_marks_the_stamp_and_the_manifest(tmp_path, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    source = repo / "inputs.yaml"
    source.write_text("bed_width_m: 2.0\n", encoding="utf-8")
    _git_commit_all(repo, "initial")

    source.write_text("bed_width_m: 2.001\n", encoding="utf-8")  # uncommitted
    out = tmp_path / "artifacts" / "dirty.svg"
    manifest = _build_svg_with_manifest(repo, out, source)

    assert manifest.root_vcs.dirty is True
    assert manifest.stamp_text and manifest.stamp_text.endswith("+")
    assert manifest.stamp_text in out.read_text(encoding="utf-8")
    assert main(["manifest", str(out), "--check"]) == 0
    assert "TOOL-DIRTY" in capsys.readouterr().out

    _git_commit_all(repo, "commit the change")
    # Same target basename and same input content, so the ONLY thing that moved is
    # the dirty flag — which is metadata about the repo, not about the bytes.
    clean = _build_svg_with_manifest(repo, tmp_path / "committed" / "dirty.svg", source)
    assert clean.root_vcs.dirty is False
    assert clean.digest == manifest.digest
    if clean.tool.dirty is not True:
        assert clean.stamp_text and not clean.stamp_text.endswith("+")
        assert clean.stamp_text != manifest.stamp_text  # same digest, no "+"


def test_missing_git_records_unknown_rather_than_clean(tmp_path, capsys):
    assert not any((parent / ".git").exists() for parent in (tmp_path, *tmp_path.parents))
    source = tmp_path / "input.txt"
    source.write_text("x", encoding="utf-8")
    out = tmp_path / "out.json"
    manifest = _build_json_with_manifest(tmp_path, out, source)

    assert (manifest.root_vcs.kind, manifest.root_vcs.commit, manifest.root_vcs.dirty) == (
        None,
        None,
        None,
    )
    assert main(["manifest", str(out), "--check", "--json"]) == 0
    text = capsys.readouterr().out
    assert '"root_vcs": "unknown"' in text
    assert "clean" not in text  # null means "we did not check", never "it is clean"


def test_stamp_text_rejects_anything_that_is_not_a_full_digest():
    digest = "a" * 64
    assert stamp_text(digest, dirty=False) == "P:aaaaaaaa"
    assert stamp_text(digest, dirty=True) == "P:aaaaaaaa+"
    with pytest.raises(ProvenanceError, match="64 lower-case hex"):
        stamp_text("abc", dirty=False)


def test_provenance_never_alters_the_issued_gate(tmp_path):
    """Rule 3, made mechanical: a provenance stamp is not an approval."""

    from technical_drawings_for_agents.validate import validate_drawing_dir

    drawing = tmp_path / "drawing"
    out_dir = drawing / "out"
    out_dir.mkdir(parents=True)
    meta_path = drawing / "meta.yaml"
    meta_text = (
        "number: TST-CIV-GA-001\n"
        "title: Provenance Gate Test\n"
        "revision: A\n"
        "status: CONCEPT\n"
        "for_construction: false\n"
    )
    meta_path.write_text(meta_text, encoding="utf-8")
    source = drawing / "inputs.yaml"
    source.write_text("x: 1\n", encoding="utf-8")

    meta = DrawingMeta.load(meta_path)
    vb = ViewBox(0, 10, 0, 5, 500, 320)
    unstamped = Drawing(500, 320, vb, title_block=meta.title_block(), status=meta.status_key)
    unstamped.add(svg_scale_bar(vb, 0, 0, 5))
    (out_dir / "TST-CIV-GA-001.svg").write_text(
        unstamped.render(), encoding="utf-8", newline="\n"
    )
    before = validate_drawing_dir(drawing).problems

    manifest = _build_svg_with_manifest(drawing, out_dir / "TST-CIV-GA-001.svg", source)
    after = validate_drawing_dir(drawing)

    assert meta_path.read_text(encoding="utf-8") == meta_text
    svg = (out_dir / "TST-CIV-GA-001.svg").read_text(encoding="utf-8")
    assert "CONCEPT — NOT FOR CONSTRUCTION" in svg
    reloaded = DrawingMeta.load(meta_path)
    assert reloaded.status == "CONCEPT"
    assert reloaded.for_construction is False
    forbidden = {"issued", "approved", "for_construction", "signed"}
    assert forbidden.isdisjoint(_all_keys(manifest.to_dict()))
    assert after.ok is True
    assert after.problems == before


# --------------------------------------------------------------------------- #
# manifest validation, --check, and the layout seam
# --------------------------------------------------------------------------- #


def test_input_outside_the_manifest_root_is_a_loud_error(tmp_path):
    root = tmp_path / "a"
    root.mkdir()
    outside = tmp_path / "b" / "x.yaml"
    outside.parent.mkdir()
    outside.write_text("x: 1\n", encoding="utf-8")

    with pytest.raises(ProvenanceError, match="root="):
        Manifest.plan(target=root / "out.svg", inputs=[outside], root=root, policy=_policy())
    assert not Manifest.path_for(root / "out.svg").exists()


def test_a_manifest_with_no_declared_inputs_is_refused(tmp_path):
    with pytest.raises(ProvenanceError, match="declares no inputs"):
        Manifest.plan(target=tmp_path / "out.svg", inputs=[], root=tmp_path, policy=_policy())


def test_an_unsupported_schema_is_refused_rather_than_guess_parsed():
    data = _pinned_manifest()
    data["schema"] = "technical_drawings_for_agents/manifest@2"
    with pytest.raises(ProvenanceError, match="unsupported manifest schema"):
        Manifest.from_dict(data)
    with pytest.raises(ProvenanceError, match="unsupported manifest schema"):
        digest_preimage(data)


def test_a_false_reproducible_flag_with_no_reason_is_refused(tmp_path):
    source = tmp_path / "in.yaml"
    source.write_text("x: 1\n", encoding="utf-8")
    with pytest.raises(ProvenanceError, match="output.reason is required"):
        Manifest.plan(
            target=tmp_path / "out.svg",
            inputs=[source],
            root=tmp_path,
            policy=_policy(),
            reproducible=False,
        )
    with pytest.raises(ProvenanceError, match="output.reason must be one of"):
        Manifest.plan(
            target=tmp_path / "out.svg",
            inputs=[source],
            root=tmp_path,
            policy=_policy(),
            reproducible=False,
            reason="because",
        )


def test_manifest_check_reports_a_changed_input_and_exits_one(tmp_path, capsys):
    source = tmp_path / "input.txt"
    source.write_text("abc", encoding="utf-8")
    out = tmp_path / "out.json"
    _build_json_with_manifest(tmp_path, out, source)
    assert main(["manifest", str(out), "--check"]) == 0
    capsys.readouterr()

    source.write_text("abcd", encoding="utf-8")
    assert main(["manifest", str(out), "--check"]) == 1
    err = capsys.readouterr().err
    assert "INPUT-CHANGED" in err
    assert "input.txt" in err

    source.write_text("abc", encoding="utf-8")
    out.write_text('{"changed": true}\n', encoding="utf-8")
    assert main(["manifest", str(out), "--check"]) == 1
    assert "OUTPUT-CHANGED" in capsys.readouterr().err

    Manifest.path_for(out).unlink()
    assert main(["manifest", str(out), "--check"]) == 2
    assert f"expected {Manifest.path_for(out)}" in capsys.readouterr().err


def test_untracked_input_is_recorded_and_warned_not_hidden(tmp_path, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    tracked = repo / "tracked.yaml"
    tracked.write_text("x: 1\n", encoding="utf-8")
    _git_commit_all(repo, "tracked")
    ignored = repo / "geo" / "ga_backdrop.png"
    ignored.parent.mkdir()
    ignored.write_bytes(b"png")
    (repo / ".gitignore").write_text("geo/\n", encoding="utf-8")

    out = tmp_path / "artifacts" / "out.json"
    policy = _policy()
    manifest = Manifest.plan(
        target=out, inputs=[tracked, ignored], root=repo, policy=policy, params={"view": "plan"}
    )
    write_json_canonical(out, {"ok": True}, policy)
    manifest = manifest.with_output(out)
    manifest.write()

    records = {record.path: record for record in manifest.inputs}
    assert records["geo/ga_backdrop.png"].tracked is False
    assert records["geo/ga_backdrop.png"].sha256 == sha256_file(ignored)[0]
    assert main(["manifest", str(out), "--check"]) == 0
    stdout = capsys.readouterr().out
    assert "INPUT-UNTRACKED" in stdout
    assert "geo/ga_backdrop.png" in stdout

    data = copy.deepcopy(manifest.to_dict())
    data["inputs"][0]["tracked"] = not data["inputs"][0]["tracked"]
    assert verify_digest(data) is True   # tracked-ness is a repo fact, not a content fact


def test_manifest_report_prints_the_digest_the_stamp_and_every_input(tmp_path, capsys):
    source = tmp_path / "input.txt"
    source.write_text("abc", encoding="utf-8")
    out = tmp_path / "out.json"
    manifest = _build_json_with_manifest(tmp_path, out, source)

    assert main(["manifest", str(out)]) == 0
    report = capsys.readouterr().out
    assert manifest.digest in report
    assert "input.txt" in report
    assert "reproducible: yes" in report


def test_a_drawing_opts_itself_in_through_its_own_meta_yaml(tmp_path):
    """The opt-in is a reviewable line in the drawing's ``meta.yaml``, not a CI flag.

    An environment variable that changes output bytes is the exact hazard this PR
    exists to remove, so ``render`` reads the declaration from the drawing itself.
    """

    from technical_drawings_for_agents.cli import _policy_from_meta

    drawing = tmp_path / "drawing"
    drawing.mkdir()
    meta_path = drawing / "meta.yaml"
    base = "number: TST-CIV-GA-001\ntitle: Opt-in Test\nrevision: A\nstatus: CONCEPT\n"

    assert _policy_from_meta(tmp_path / "no-such-dir") is None
    meta_path.write_text(base, encoding="utf-8")
    assert _policy_from_meta(drawing) is None                       # absent -> off

    meta_path.write_text(base + "deterministic: false\n", encoding="utf-8")
    assert _policy_from_meta(drawing) is None

    meta_path.write_text(base + "deterministic: true\n", encoding="utf-8")
    policy = _policy_from_meta(drawing)
    assert isinstance(policy, EmitPolicy)
    assert DrawingMeta.load(meta_path).deterministic is True

    # a non-bool is a loud config error naming the offending file, not a truthy guess
    meta_path.write_text(base + "deterministic: yes-please\n", encoding="utf-8")
    with pytest.raises(ProvenanceError, match="deterministic must be true or false"):
        _policy_from_meta(drawing)

    # ...and the declaration does not touch the ISSUED gate
    meta_path.write_text(base + "deterministic: true\n", encoding="utf-8")
    reloaded = DrawingMeta.load(meta_path)
    assert reloaded.status == "CONCEPT"
    assert reloaded.for_construction is False


def test_declared_formats_cover_every_manifest_target_format():
    """No format may be manifestable without a measured reproducibility declaration."""

    from technical_drawings_for_agents.provenance import TARGET_FORMATS

    assert set(FORMAT_REPRODUCIBILITY) == TARGET_FORMATS
    for name, declared in FORMAT_REPRODUCIBILITY.items():
        assert declared.digest in {"bytes", "masked", "none"}, name
        if declared.reproducible is not True:
            assert declared.reason, f"{name}: a non-reproducible format must say why"


def _all_keys(value) -> set[str]:
    if isinstance(value, dict):
        keys = set(value)
        for item in value.values():
            keys.update(_all_keys(item))
        return keys
    if isinstance(value, list):
        keys: set[str] = set()
        for item in value:
            keys.update(_all_keys(item))
        return keys
    return set()


# --------------------------------------------------------------------------- #
# backward compatibility — the parity story this whole PR rests on
# --------------------------------------------------------------------------- #


def test_existing_generators_produce_unchanged_bytes_when_not_opted_in(tmp_path):
    """Rule 1, mechanised: no EmitPolicy anywhere means no byte moves.

    Compared against committed **golden artifacts** rather than pinned ``sha256``
    vectors, so a dependency bump fails with a readable diff instead of a hash
    mismatch that cannot say whether we regressed or matplotlib did. The goldens
    were generated from ``origin/main`` on the pinned toolchain — see
    ``tests/goldens/README.md``.

    The DXF is compared through ``mask_dxf_volatiles`` because an *unmasked* DXF
    cannot be a golden file at all today, which is precisely the defect P3 fixes.
    """

    pytest.importorskip("ezdxf")
    import ezdxf

    simple = tmp_path / "simple-section"
    shutil.copytree(EXAMPLE, simple)
    _run_source(simple / "source.py")

    svg = simple / "out" / "EXA-CIV-SEC-001.svg"
    assert svg.read_bytes() == (GOLDENS / "EXA-CIV-SEC-001.svg").read_bytes()
    masked = mask_dxf_volatiles(
        (simple / "out" / "EXA-CIV-SEC-001.dxf").read_text(encoding="utf-8", errors="ignore")
    )
    assert masked == (GOLDENS / "EXA-CIV-SEC-001.dxf.masked").read_text(encoding="utf-8")

    assert "provenance-stamp" not in svg.read_text(encoding="utf-8")
    assert not list((simple / "out").glob("*.manifest.json"))
    # D12: the ezdxf process-global must not leak out of an opted-in save
    assert ezdxf.options.write_fixed_meta_data_for_testing is False

    from technical_drawings_for_agents import pid

    pid_work = tmp_path / "synthetic-pid"
    shutil.copytree(REPO / "drawings" / "example" / "synthetic-pid", pid_work)
    pid.build(pid_work / "SYN-PSK-PID-001.pid.yaml", pid_work / "out")
    pid_svg = pid_work / "out" / "SYN-PSK-PID-001.svg"
    assert pid_svg.read_bytes() == (GOLDENS / "SYN-PSK-PID-001.svg").read_bytes()
    assert "provenance-stamp" not in pid_svg.read_text(encoding="utf-8")
    assert not list((pid_work / "out").glob("*.manifest.json"))


def test_the_committed_example_svgs_still_match_their_generators(tmp_path):
    """The checked-in SVG artifacts are current; only the DXFs have drifted.

    Kept separate from the golden comparison above so the two failure modes stay
    distinguishable: this one says "a committed artifact is stale", that one says
    "emit changed". The committed ``*.dxf`` files are deliberately *not* asserted
    — they were written by an earlier ezdxf whose ``CLASSES`` section ordered
    ``LAYOUT`` before ``ACDBPLACEHOLDER``, which is pre-existing drift in
    checked-in artifacts and not something P3 introduced or should mask over.
    """

    simple = tmp_path / "simple-section"
    shutil.copytree(EXAMPLE, simple)
    _run_source(simple / "source.py")
    assert (simple / "out" / "EXA-CIV-SEC-001.svg").read_bytes() == (
        EXAMPLE / "out" / "EXA-CIV-SEC-001.svg"
    ).read_bytes()


def test_the_fixed_dxf_metadata_option_is_restored_even_when_a_save_raises(tmp_path):
    """It is a process-global: leaking it would silently fix every later DXF's metadata."""

    pytest.importorskip("ezdxf")
    import ezdxf

    from technical_drawings_for_agents.dxf import DxfBuilder

    assert ezdxf.options.write_fixed_meta_data_for_testing is False
    builder = DxfBuilder(units="m", policy=_policy())
    builder.polyline([(0.0, 0.0), (1.0, 0.0)], closed=False)
    with pytest.raises((OSError, ValueError)):
        builder.save(tmp_path / "no-such-dir" / "x" / "\0bad.dxf")
    assert ezdxf.options.write_fixed_meta_data_for_testing is False

    builder.save(tmp_path / "ok.dxf")
    assert ezdxf.options.write_fixed_meta_data_for_testing is False


def test_layout_emits_identical_geojson_without_manifest_and_a_manifest_with_it(tmp_path):
    """The opt-in seam: ``--manifest`` adds a sidecar and canonical bytes; nothing else does."""

    spec = (
        REPO / "src" / "technical_drawings_for_agents" / "components" / "examples" / "packaged_unit.yaml"
    ).read_text(encoding="utf-8")
    root = tmp_path / "components"
    root.mkdir()
    (root / "unit.yaml").write_text(spec, encoding="utf-8")
    register = tmp_path / "placements.yaml"
    register.write_text(
        yaml.safe_dump(
            {"instances": [{"type": "unit", "origin_utm": [788416.0625, 322373.5], "rotation_deg": 0.0}]},
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    config = tmp_path / "layout.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "layout": {"id": "SEAM", "crs": "EPSG:32630"},
                "components": {"root": "components", "types": {"unit": ["unit.yaml"]}},
                "placements": "placements.yaml",
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    plain = tmp_path / "plain.geojson"
    assert main(["layout", str(config), "--emit", "geojson", "--out", str(plain)]) == 0
    assert not Manifest.path_for(plain).exists()

    manifested = tmp_path / "manifested.geojson"
    assert (
        main(
            [
                "layout",
                str(config),
                "--emit",
                "geojson",
                "--out",
                str(manifested),
                "--manifest",
            ]
        )
        == 0
    )
    sidecar = Manifest.path_for(manifested)
    assert sidecar.exists()

    manifest = Manifest.load(sidecar)
    assert [record.path for record in manifest.inputs] == sorted(
        record.path for record in manifest.inputs
    )
    assert {Path(record.path).name for record in manifest.inputs} == {
        "layout.yaml",
        "placements.yaml",
        "unit.yaml",
    }
    assert manifest.params == {"snap": True, "view": "plan"}
    assert manifest.stamp_text is None      # a GeoJSON has nowhere to put a stamp
    assert manifest.stale_findings() == () or all(
        finding.severity == "warn" for finding in manifest.stale_findings()
    )
    assert main(["manifest", str(manifested), "--check"]) == 0

    # --manifest without an emit target is a usage error, not a silent no-op
    assert main(["layout", str(config), "--manifest"]) == 2


def test_layout_manifest_geojson_is_relocatable_and_rebuild_stable(tmp_path):
    """Two runs, two directories, same declared content -> one digest, one set of bytes."""

    def build(work: Path) -> tuple[bytes, str]:
        spec = (
            REPO / "src" / "technical_drawings_for_agents" / "components" / "examples" / "packaged_unit.yaml"
        ).read_text(encoding="utf-8")
        (work / "components").mkdir(parents=True)
        (work / "components" / "unit.yaml").write_text(spec, encoding="utf-8")
        (work / "placements.yaml").write_text(
            yaml.safe_dump(
                {
                    "instances": [
                        {
                            "type": "unit",
                            "origin_utm": [788416.0625, 322373.5555],
                            "rotation_deg": 38.876,
                        }
                    ]
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        (work / "layout.yaml").write_text(
            yaml.safe_dump(
                {
                    "layout": {"id": "SEAM", "crs": "EPSG:32630"},
                    "components": {"root": "components", "types": {"unit": ["unit.yaml"]}},
                    "placements": "placements.yaml",
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        out = work / "out" / "layout.geojson"
        assert (
            main(
                [
                    "layout",
                    str(work / "layout.yaml"),
                    "--emit",
                    "geojson",
                    "--out",
                    str(out),
                    "--manifest",
                ]
            )
            == 0
        )
        return out.read_bytes(), Manifest.load(Manifest.path_for(out)).digest

    first_bytes, first_digest = build(tmp_path / "one")
    time.sleep(1.1)
    second_bytes, second_digest = build(tmp_path / "two" / "deeper")

    assert first_bytes and second_bytes == first_bytes
    assert second_digest == first_digest

    # every emitted coordinate is quantised to mm — no 17-digit repr noise, which is
    # what makes a rotated UTM coordinate comparable across two libm implementations
    collection = json.loads(first_bytes.decode("utf-8"))
    coordinates = list(_walk_numbers(collection["features"]))
    assert coordinates
    for value in coordinates:
        assert value == round(value, 3), f"{value!r} is not quantised to mm"


def _walk_numbers(value):
    if isinstance(value, bool):
        return
    if isinstance(value, float):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _walk_numbers(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_numbers(item)


def _run_source(path: Path) -> None:
    added = str(path.parent)
    sys.path.insert(0, added)
    try:
        runpy.run_path(str(path), run_name="__main__")
    finally:
        sys.path.remove(added)
