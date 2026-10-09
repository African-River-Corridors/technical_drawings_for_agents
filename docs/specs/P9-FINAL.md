# P9 — FINAL specification and implementation plan

**Status:** authoritative. Supersedes `P9-revision-titleblock-numbering.md` where it says so below.

**Provenance.** Base spec by an Opus agent (PR #68). Implemented independently by variant **A**
(upstream #98, **2 failed / 634 passed**) and variant **B**
(upstream #99, **4 failed / 633 passed**).

**P9 is the first change to land on top of P1's shipped sheet model, and both variants broke something.**
That is the central lesson of this pair and it shapes every correction below.

---

## 1. Variant B is disqualified — it broke P1's shipped model, including the ISSUED gate

B's four failures are **not P9 tests**. Every one belongs to P1, which has already shipped:

```
test_stated_scale_contradicting_the_viewport_fails_validation
test_a_drawing_without_a_sheet_block_gains_no_new_checks
test_the_issued_gate_is_untouched_by_the_sheet_model
test_cli_validate_strict_promotes_warnings_to_failure
```

The third is the **ISSUED-gate assertion** — the single hardest line in every brief in this programme, and the
one constraint that is never negotiable. Breaking it is disqualifying on its own; breaking it alongside three
other shipped-feature tests means B modified P1's validation surface rather than consuming it.

### CORRECTION 1 (binding) — P9 consumes P1 read-only

- P9 reads P1's frame through the declared **`PaperFrame` protocol** (5 members) and **must not modify**
  `sheet.py`, P1's `validate` checks V1–V11, the `--strict` promotion behaviour, or the ISSUED-gate logic.
- The title block is positioned **from the frame**; it does not reach into P1's internals.
- Any change to a P1 test is a **defect in P9**, not a migration. If a P1 test seems wrong, stop and say so
  rather than editing it.

---

## 2. Variant A is the base, with two real compat breaks to fix

A's two failures are both on P9's own surface and are precisely diagnosable.

### CORRECTION 2 (binding) — numbering enforcement is *strictly* opt-in

A broke `assert DrawingMeta(number="X-1", title="t").validate() == []`.

The base spec **already flagged this exact test** (`tests/test_toolkit.py:54`) and specified numbering as
"shape-only by default, enforcement opt-in per drawing". A's implementation enforced it globally.

- With **no numbering grammar configured**, `DrawingMeta.validate()` must return `[]` for **any** non-empty
  number, including `X-1`. That test must keep passing untouched.
- Enforcement activates **only** when a project declares a grammar.
- Rationale beyond compatibility: the base spec established that the vault standard's *entire* written
  convention is two examples plus one sentence — and **its two examples disagree on field count**
  (`ARC-CSL-001` = 2 segments, `STA-WTP-GA-001` = 3), with `Standards/_index.md:48` recording the numbering
  half as still *proposed*. Enforcing a grammar globally would impose a scheme the standard has not decided.

### CORRECTION 3 (binding) — use P3's canonical text writer; the trailing newline is part of the contract

A's parity failure is a **single trailing newline**:

```
assert rendered == golden
E   assert '<svg …>\n</g></svg>' == '<svg …>\n</g></svg>\n'
```

Not a substantive regression — but byte-parity means bytes. P3 shipped a canonical text-write policy
(**UTF-8, LF, no BOM**, with a defined trailing-newline rule). **Route every sheet/schematic write through
P3's `write_text_canonical`** rather than a bespoke `write_text`, and the class of failure disappears.

---

## 3. The ISSUED gate — carried forward verbatim, and now load-bearing

The base spec's §5 is the most safety-sensitive design in the programme. Every element stands:

- **`app` is read verbatim** from `revisions[-1].app`, with **no fallback** to `by`, `chk`, `$USER`,
  git-author, or config. The tool never fills in an approver.
- **`revision add` has no `--app` flag**, and a test **introspects argparse** to keep it that way.
- **`revision seal` refuses** under `CI` or without a TTY.
- **Immutability is enforced four ways:** a seal covering the entry *plus its index plus the digest of all
  preceding entries*, so reorder, delete and insert are all caught; an append-only frozen API; no edit/unseal
  verb; and the strengthened gate.
- **One deliberate, argued compatibility break:** `for_construction: true` additionally requires a named
  approver and a valid seal. Blast radius today is **zero** — nothing in either repo is ISSUED.

Given that variant B broke the ISSUED-gate test, the final build must treat this section as the acceptance
criterion it is: **`test_the_issued_gate_is_untouched_by_the_sheet_model` must pass, and P9's own gate tests
must pass alongside it.**

---

## 4. Carried forward unchanged from the base spec

- **`meta.revision` stays a separate field that must *agree* with `revisions[-1].rev`** — not derived.
  Deriving it deletes the only check that catches the real-world error (bump the row, forget the sheet's rev,
  or vice versa) and breaks the `bfd`/`pid` paths, which read a free-form `meta` mapping rather than
  `DrawingMeta`.
- **The title block is a fixed physical 180 × 56 mm block** anchored to the frame's bottom-right corner. That
  is *why* one implementation is correct at A3/A2/A1/A0 in both orientations — sheet size affects only the
  anchor. 180 mm = the A4-portrait frame width (210 − 20 − 10). Text in **ISO 3098 mm sizes**, so it is the
  same physical height on A0 as on A3.
- **Tests run against a `FakeFrame`**, so P9 is independently testable.

### Incidental repo findings the base spec recorded (all verified, all still true)

- **`meta.py:54` sweeps unknown keys into `extra`**, so adding `revisions:` to a `meta.yaml` today is a
  **silent no-op**. That is the bug this PR closes.
- **`svg.py:572-576` silently ellipsises the drawing title.**
- **`meta.py:26` annotates `date: str`** but `yaml.safe_load` returns a `datetime.date` and nothing converts
  it — so the backward-compat test must assert the object, not a string.
- The vault's `source.py:112` derives a drawing number by `.replace("WTP-GA", "SITE-GA")` — a hack, flagged as
  a follow-up, out of scope here.

---

## 5. Implementation plan

1. Base on variant A.
2. Apply Correction 1: touch nothing P1 owns. Consume `PaperFrame` only.
3. Apply Correction 2: numbering enforcement strictly opt-in; `DrawingMeta(number="X-1", title="t")` validates
   clean with no grammar configured.
4. Apply Correction 3: all sheet/schematic writes via P3's `write_text_canonical`.
5. Compare against **P3's masked goldens**, never against `drawings/example/*/out/` — see **#101**, the trap
   that felled both P7 variants.
6. Full suite green. Predecessor states: A `2 failed / 634 passed`, B `4 failed / 633 passed`. Target
   **0 failed**, with P1's four tests passing untouched.
7. Assert title-block correctness **mechanically** at A3/A2/A1/A0 in both orientations — not by eye.

## 6. Constraints (absolute)

- **The ISSUED gate is untouchable, and this PR is closest to it.** No tool, CI run, or agent may flip a
  drawing to ISSUED FOR CONSTRUCTION or populate an approver.
- **Backward compatibility is sacred.** Existing drawings keep validating; new requirements are opt-in or
  shipped with a migration in the same change.
- **Never invent a standard.** The numbering grammar must trace to what the vault standard actually says;
  anything you must choose is an open question for the owner, not a decision.
- **Loud failure over a silent no-op** — the `revisions:`-into-`extra` silent no-op is exactly what this PR
  exists to end.
