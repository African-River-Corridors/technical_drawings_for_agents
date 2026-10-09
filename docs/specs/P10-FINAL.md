# P10 — FINAL specification and implementation plan

**Status:** authoritative. Supersedes `P10-stable-ids-idempotence.md` **only where it says so below**;
everything not corrected here stands as written in that document.

**Provenance.** Base spec by an Opus agent (PR #65). Implemented independently by variant **A**
(upstream #79, 1488+/53−) and variant **B**
(upstream #80, 1389+/44−), both CI-green, same eight files.

Read this **with** the base spec. This is a delta.

---

## 1. The pair converged almost completely — and that is the headline

The base spec was precise enough that the two implementations produced:

- a **byte-identical** rewrite of the shipped example register
  (`components/examples/site_placements.yaml`) — same reordering, same new
  `# ids: 2 of 2 placements carry a durable id.` header line, identical blob hash;
- the **identical one-line migration** of the single existing assertion the canonical sort invalidates,
  `["EX-A", "EX-B"]` → `["EX-B", "EX-A"]` — which the base spec had already predicted, naming
  `tests/test_layout.py:753` as the only such assertion;
- the **identical resolution** of a spec defect neither could satisfy as written (§2).

Two independent implementations agreeing byte-for-byte on a data migration is the strongest available
evidence that the migration is correct. It is settled; do not revisit it.

**Note on the changed assertion.** This is a *legitimate, spec-predicted migration*, not a weakened test:
the generated register's record order genuinely changed, and the assertion tracks it with a comment
explaining why. Both variants also left the comment in. Keep it.

---

## 2. Spec defect — the argparse requirement is unsatisfiable as written (both variants)

The base spec asks for an `argparse.add_mutually_exclusive_group` while **also** requiring that
`--from-geojson` combine with `--check-register`, and that `--canonicalise-register` reject both. A single
argparse mutually-exclusive group cannot express that relationship: it is a flat "at most one of these" and
cannot encode "A may pair with B but C excludes both".

**CORRECTION (binding).** Enforce the exclusions **explicitly in `run_layout`**, not via an argparse group.
Both variants did exactly this, independently. Requirements:

- `--from-geojson` + `--check-register` → **valid** (refresh, then compare against what was on disk).
- `--canonicalise-register` with either of the above → **error, exit 2**, naming both conflicting flags.
- The error message must name the flags, not the internal state.
- Add a test asserting the exclusion, so a future refactor to an argparse group (which would silently
  loosen or break it) fails loudly.

---

## 3. Implementation basis — variant A

**A is the base.** Its core is equivalent to B's, but it carries two tests B lacks, both of which guard real
defect classes rather than restating the spec:

1. **`test_an_id_never_leaks_into_emitted_feature_properties`** — asserts a placement's id is emitted as
   `placed_id` and that a bare `id` key is **absent** from feature properties. This generalises a bug that
   actually bit this codebase: instance properties override feature properties in `emit._properties`, so
   writing an instance tag to `tag` previously erased every component feature's own tag (all 34 FA-130
   nozzles came out labelled `STA-A`). The same trap exists for `id`. A closed it before it opened.
   **This test is mandatory in the final build.**
2. **`test_the_real_basin_register_shape_still_loads_and_places_unchanged`** — exercises the genuine Basin
   shape (2 tagged, 8 untagged) rather than a synthetic register, and asserts placement is unchanged. This is
   the backward-compat guarantee the whole PR rests on, tested against real data.

**Graft from B:** nothing material. B is effectively a subset; its distinct contribution was independent
confirmation of §2 and of the migration, which is exactly what a second implementation is for.

---

## 4. Behaviour change to bless explicitly

Variant A changed register refresh to **reject a derived type that is missing from `components.types`
*before* writing the register**, where the current flow writes the file and then fails on load.

**This is adopted as intended behaviour.** It is the loud-failure rule applied correctly: the old flow left a
bad generated artifact on disk after failing, so a subsequent run could load a register that had never
validated. Failing before the first byte is written is strictly better.

Because it is a behaviour change, the final build must:

- state it in the README under the `layout` verb;
- add a test asserting **no register file exists** after a refresh that fails on an unmapped type;
- confirm no existing test depended on the write-then-fail ordering (neither variant's CI flagged one).

---

## 5. What stays out, and the P3 dependency

`test_two_consecutive_full_builds_are_byte_identical` **must remain in the suite and must not be deleted or
skipped in committed code.** Neither variant could run it locally — it emits and reads DXF, and their
sandboxes had no real `ezdxf` — but **CI ran it and both passed.**

It depends on P3 (#55) for byte-reproducible DXF: `ezdxf` re-rolls two random GUIDs and writes `$TDCREATE`
on every save, so DXF byte-identity requires P3's `ezdxf.options.write_fixed_meta_data_for_testing` policy.
The interface P10 needs from P3 is a single function:

```python
emit_digest(path: Path) -> str    # canonical digest of an emitted artifact
```

**P3's final spec must provide it.** Until then the test asserts byte-identity for the register, the
effective register and GeoJSON (all reproducible today), and a *semantic* digest for DXF. Do not widen it to
PDF — the base spec correctly declares PDF unasserted, and P4 (#56) found LibreOffice PDFs are
non-deterministic regardless.

---

## 6. Unchanged and binding from the base spec

The base spec's central finding stands and must not be softened by the implementer:

> **No identity derivable from the editor export alone is both stable under a move and non-renumbering under
> a delete.** Determinism therefore comes from the **canonical sort**, not from a synthesised identity; a
> durable join key must be **authored**. The tool makes the id field first-class, unique, validated, and its
> absence loud and counted — it does not fake one.

Consequences to preserve exactly: identity precedence `editor.id_field` → `tag` → none; case-insensitive
uniqueness with a collision raising and naming **both** poses; sort key `type` → rounded easting → northing →
rotation → size → **identity last** (so populating or correcting an id moves no record); `-0.0` normalised;
a full-key tie is a duplicate pose and a hard error; canonicalise at the **write** boundary only, so
`load_layout` keeps file order and `order: as-listed` keeps its plain meaning.

## 7. Constraints (absolute)

- **Backward compatibility:** the real Basin registers keep loading and the emitted layout *geometry* is
  unchanged. Record order in generated text does change — that is the one-commit migration in §1, already
  proven byte-identical across both variants.
- **The ISSUED gate is untouchable.** Neither variant touched any status/approver path; the final build must
  not either.
- **Never invent an identity.** An id-less placement is reported and counted, never synthesised.
- **Loud failure over a silent no-op** — a silently reordered register is the exact failure being fixed.
- **House style:** `components/layout.py` is both the reference pattern and the file being changed.

---

## 8. CORRECTION to §3 of this document — my comparison was wrong

Found by the build agent (PR #91) and verified: **§3's claim that variant B lacks the two mandatory tests is
false. B has both**, in `tests/test_layout_ids.py`:

- `test_an_id_never_leaks_into_emitted_feature_properties` — `test_layout_ids.py:379`, asserting
  `properties["placed_id"] == "RAFT-01"` and `"id" not in properties`.
- `test_the_real_basin_register_shape_still_loads_and_places_unchanged` — `test_layout_ids.py:220`, and it is
  **stronger than A's**: it writes a real register *file* from `BASIN_REGISTER_TEXT`, asserts identity
  resolution for both clarifiers, and counts durable ids (2 of 10, 8 without). B also carries an extra
  `test_canonicalising_the_basin_register_changes_order_only` that A does not.

**How I got it wrong.** I compared the variants' test coverage by diffing only the **pre-existing**
`tests/test_layout.py`. B put its new tests in a **new file** I never opened. So both "A carries two tests B
lacks" and "B is effectively a subset" are unfounded.

**What still stands:** both tests are mandatory and both are in the final build; the two implementations
converged on everything material (§1, §2). **What does not stand:** the *reason* given for choosing A as the
base. On the evidence the choice between them was close to arbitrary, and B's Basin test was the better of the
two. Recording this rather than quietly deleting §3, because a judgement whose stated reasoning was wrong is
worth knowing about even when the outcome was harmless.

### Process lesson (generalises beyond P10)

**Enumerate a variant's full test inventory across all files — never infer coverage from the diff of a
pre-existing test file.** This is the same class of error as diffing an agent branch against current `main`
instead of its merge base (which earlier made a variant appear to delete nine spec files). Both mistakes come
from reading a narrow slice and generalising. Two for two: check the whole surface.

## 9. Two further spec gaps the build found

### 9.1 §3.3 under-specifies where the `# ids:` header line goes

"Immediately after the CRS/pose lines" admits placing it either before or after `# Regenerate with:`. Both
Codex variants chose **before**, and that convergence is *precisely why* the migration came out
byte-identical. A future implementer reading only §3.3 could reasonably put it last and silently change every
register's bytes. **Binding: the `# ids:` line goes immediately before the `# Regenerate with:` line.**

### 9.2 The 16-test list leaves guarantee **G2** unpinned

The base spec names G2 as a guarantee but no listed test asserts it. The build added one. Keep it: a named
guarantee with no test is a claim, not a property.
