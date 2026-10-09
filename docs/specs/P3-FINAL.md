# P3 — FINAL specification and implementation plan

**Status:** authoritative. Supersedes `P3-deterministic-emit-manifest.md` where it says so below.

**Provenance.** Base spec by an Opus agent (PR #71). Implemented independently by variant **A**
(upstream #83, 3060+/220−) and variant **B**
(upstream #82, 2446+/206−).

**Both variants FAILED CI**, with identical counts — `2 failed, 427 passed, 4 skipped`. Both failed the same
central test. **This is a spec defect, not implementer error**, and correcting it is the substance of this
document. P3 is the dependency root for P2 and P10, so it must be right before either proceeds.

---

## 1. What both failed, and why it matters

Both variants failed `test_second_build_with_no_input_change_is_byte_identical_per_format` — **the single
deliverable of P3.** Two independent implementations of the same spec, both green on 427 other tests, both
failing the one assertion the PR exists to satisfy. The spec's determinism model is incomplete.

The test, as written by A, does three things at once: it rebuilds in a **different directory**, after a
**1.1 s sleep**, and asserts byte-identity across **seven formats** including a PDF and a PNG. Each of those
is a separate guarantee with a different failure mode, and conflating them produces a test that cannot
diagnose itself.

### CORRECTION 1 (binding) — decompose the determinism guarantee

Replace the single test with four, each asserting one property:

| Test | Property | Formats |
|---|---|---|
| `test_rebuild_in_place_is_byte_identical` | same inputs, same directory, immediate | all declared-reproducible |
| `test_rebuild_after_a_time_gap_is_byte_identical` | no wall-clock leakage (sleep ≥ 1.1 s) | all declared-reproducible |
| `test_rebuild_in_a_different_directory_is_byte_identical` | **relocatability** | all declared-reproducible |
| `test_non_reproducible_formats_are_declared_not_asserted` | a format the tool declares non-reproducible must be *marked*, never silently asserted | LibreOffice PDF |

A format enters a byte-identity assertion **only** if the tool declares it reproducible. The base spec
already established that LibreOffice PDFs are not (time-derived `/CreationDate` and `/ID`,
`SOURCE_DATE_EPOCH` ignored); the failing test asserted on PDF and PNG anyway.

### CORRECTION 2 (binding) — the digest must not include input paths

Variant A's `digest_preimage` (provenance.py:670-673) hashes `item["path"]` alongside `item["sha256"]`:

```python
"inputs": [
    {"path": item["path"], "sha256": item["sha256"], "bytes": item["bytes"]}
    for item in sorted(data["inputs"], key=lambda item: item["path"])
],
```

**A build's identity must not depend on where it was built.** Consequences of the current design:
- CI and a local build of identical inputs produce different digests, so the stamp can never be cross-checked;
- moving a project directory makes every artifact appear stale;
- the "is this sheet current?" question becomes machine-specific, which is precisely what P3 exists to end.

**The digest preimage is computed over input *content* only** — the sorted multiset of
`(sha256, bytes)` — plus `target` identity, `params`, `policy` and `tool`. Paths stay in the **manifest** for
human traceability and are **excluded from the digest**. Sort inputs by `sha256` (content), not by path.

This is the most likely direct cause of the shared failure, and it is a correctness fix regardless.

### CORRECTION 3 (binding) — YAML writes must not escape non-ASCII

Variant A's `test_text_writes_are_utf8_lf_regardless_of_locale` failed, exposing a **pre-existing bug in
`components/layout.py::dump_placements_register`**. Verified directly:

```
tag 'Ø—1' is written as:   tag: "\xD8—1"
```

`yaml.safe_dump` defaults to `allow_unicode=False`. An escaped register is not human-reviewable, which
defeats the entire text-as-truth rationale for having the register — and this is live risk, not theoretical:
Ghanaian and Portuguese names and equipment tags carry accents routinely.

**Every YAML write in the toolkit passes `allow_unicode=True`**, and the canonical text-write policy is
**UTF-8, LF, no BOM**, asserted regardless of locale. A's test is correct and must be kept; the *code* was
wrong. Fix `dump_placements_register` in the same change.

---

## 2. Variant B is disqualified as the base

B failed `test_existing_generators_produce_unchanged_bytes_when_not_opted_in` — its **own backward-compat
parity test**. Credit for writing a test sharp enough to catch its own regression; the regression stands.
Backward compatibility is the one constraint labelled sacred in every brief, and B changed existing output.

B also pinned literal `sha256` vectors in tests (`assert _sha(svg) == "387f24…"`). Two of those mismatched on
CI. Hard-coded digests break on any dependency bump, so a mismatch cannot distinguish "we regressed" from
"matplotlib updated" — the opposite of a useful signal.

**CORRECTION 4 (binding):** no test may pin a literal output digest. Assert **equality between two builds**,
or against a **committed golden artifact** that a dependency bump would visibly change in review. The base
spec's "byte-exact preimage vector pinned as a test" applies **only** to `digest_preimage` — a pure function
of declared inputs with no dependency surface — and nowhere else.

**Basis: variant A**, with corrections 1–4 applied. Its parity test passes, and its two failures are both
*discoveries* (the path-in-digest consequence, and a real pre-existing encoding bug) rather than regressions.

---

## 3. Carried forward unchanged from the base spec

These stand and must not be re-litigated — the base spec measured each one:

- **Two digests, not one.** `build.digest` (pre-emit, over inputs+params+policy+tool) versus
  `output.sha256` (post-write). The stamp is printed *into* the sheet, so it cannot depend on the sheet's own
  bytes. This is also why "hash the manifest" is rejected.
- **Rounding is `round()`/`format()` half-even, never `Decimal(ROUND_HALF_UP)`.** They genuinely differ:
  `0.0625` → `0.062` vs `0.063`, likewise `2.0625` and `788416.0625`. Ties at mm precision are odd multiples
  of 1/16 m and do occur in real work.
- **`environment` is excluded from the digest** — otherwise the stamp answers "who built it" rather than
  "what went into it".
- **A dirty git tree appends `+` to the stamp**, never silently omitted; both dirty flags stay out of the
  digest, with the resulting hole stated openly.
- **`ezdxf.options.write_fixed_meta_data_for_testing = True`** is required for DXF byte-identity — `ezdxf`
  re-rolls two random GUIDs and writes `$TDCREATE`/`$TDUPDATE` on every save. Measured: 6 differing lines of
  13158 without it, 0 with it.
- **"Sorted attributes" from the issue was wrong** and is not adopted: attribute order is already fixed by
  f-strings. The real defect is *element* order from unordered containers (`svg.py:151-156`).
- **LibreOffice PDF is declared non-reproducible, not normalised.** A hand-rolled PDF mutator on the path
  producing the artifact an engineer signs against is the worse risk.

---

## 4. Interface P10 requires (must ship in this PR)

P10-FINAL §5 holds `test_two_consecutive_full_builds_are_byte_identical` open pending one function. Provide it:

```python
def emit_digest(path: Path) -> str:
    """Canonical digest of an emitted artifact.

    For a declared-reproducible format this is the sha256 of the file bytes.
    For a format whose container embeds unavoidable non-determinism (DXF GUIDs and
    timestamps) it is the sha256 of the canonically-masked content, so the digest
    reflects the drawing rather than the container's metadata.
    Raises ProvenanceError for a format declared non-reproducible — the caller must
    not silently receive a meaningless value.
    """
```

The last clause matters: returning *something* for a non-reproducible format would let a caller assert
byte-identity on a PDF and appear to pass. Raise instead.

---

## 5. Implementation plan

1. Start from variant A's structure (`src/technical_drawings_for_agents/provenance.py` plus the additive emit-path changes).
2. Apply corrections 1–4.
3. Fix `dump_placements_register` to pass `allow_unicode=True`. **Coordinate:** another change may be in
   flight in `components/layout.py`; if it conflicts, rebase onto it rather than reverting its work.
4. Ship `emit_digest` per §4.
5. Full suite green — **no exceptions.** The predecessor state is `2 failed, 427 passed, 4 skipped`; the
   target is `0 failed`. If a spec'd test cannot pass, leave it failing and say so, but a determinism PR that
   cannot demonstrate determinism is not landable.

## 6. Constraints (absolute)

- **Backward compatibility is sacred**, and this is the PR that makes parity testing possible for everything
  after it, so its own parity story must be airtight. B's failure here is exactly why it is not the base.
- **Never invent** a reproducibility claim. If a format cannot be made reproducible, the tool **declares** it
  and `emit_digest` raises. Never pretend.
- **The ISSUED gate is untouchable.** A provenance stamp is not an approval and must not gate or imply one.
- **Loud failure over silent degradation.**

---

## 7. CORRECTIONS from the build (PR #93) — including my root-cause diagnosis

The build **succeeded where both predecessors failed**: 231 → **275 passed, 0 failed**, stable across six
`PYTHONHASHSEED` values, CI green (588 passed repo-wide vs 544 on main). Independently re-verified by the
coordinator at seeds 0, 1, 7, 42, 999 — 275 every time, including the seeds that previously failed.

It found three things, and the first corrects §1 of this document.

### 7.1 My root-cause diagnosis was wrong — the real cause was a `set` in the DXF `CLASSES` section

§1 named the path-in-digest issue as "the most likely direct cause of the shared failure". The hedge was
warranted: **it was not the cause.**

`ezdxf`'s `add_required_classes()` iterates `entitydb.dxf_types_in_use()` — **a `set`** — so the `CLASSES`
section's record order varies with `PYTHONHASHSEED` and process history. **Fixed metadata alone is therefore
insufficient for DXF byte-identity**, which neither the base spec nor this document knew.

The symptom explains everything about how the failure presented: a test **passing alone and failing in the
full suite**, and passing on seeds 7 and 999 while failing on 0 and 1. Two Codex agents, each running once at
whatever seed they got, would see an unreproducible failure and have no way to attribute it. Fixed by sorting
at source under a policy, and normalising in `mask_dxf_volatiles` for files that have not opted in;
`audit()`-clean and still rendering.

**Correction 2 (paths out of the digest) stands on its own merits** — an artifact's identity must not depend
on where it was built — but it was not what broke CI.

### 7.2 `ezdxf` writes TWO wall-clock markers; base-spec D12 silences only one

`CREATED_BY_EZDXF` is stamped during `ezdxf.new()`; `WRITTEN_BY_EZDXF` during `saveas()`. Base-spec decision
**D12 mandates scoping `write_fixed_meta_data_for_testing` to the save alone**, which leaves the *creation*
marker carrying the clock — **1 differing line in 13,562**. The base spec's measured "0 differing lines" was
obtained with the option set **globally before document creation**, which D12 then forbade. This document
carried D12 forward without noticing the contradiction.

**Binding:** toggle the option around **document creation as well as save**, restoring it immediately in both
cases.

### 7.3 Correction 3's reproduction went stale mid-programme

`tag: Ø—1` no longer reaches the writer: **P10 landed an ASCII identity validator** that rejects it first, and
P10 merged after this document was written. The `allow_unicode` bug is real and was fixed, but its live
surface is now **free-form property values** — arguably the worse surface, since that is exactly where
Ghanaian and Portuguese names sit.

Lesson for a multi-PR programme: **a reproduction case pinned in a spec can be invalidated by a sibling PR
landing first.** Cite the mechanism, not only the example.

### 7.4 The repo's committed golden artifacts are stale w.r.t. the pinned `ezdxf`

Backward compatibility had to be proven as a direct **A/B against `origin/main`'s code on the same
toolchain** (five generator paths byte-identical), *not* against the committed artifacts under
`drawings/example/*/out/`, which no longer match what the pinned `ezdxf` produces. Recorded in
`tests/goldens/README.md`.

**This matters beyond P3:** any test asserting byte-equality against those committed outputs is asserting
against a stale baseline. Correction 4's "or a committed golden" escape hatch is only sound for goldens
**generated under the pinned toolchain and refreshed when it moves** — which is what this PR did.
