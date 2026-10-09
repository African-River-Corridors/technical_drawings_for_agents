# P6 — FINAL specification and implementation plan

**Status:** authoritative. Supersedes `P6-computed-dimensions.md` where it says so below.

**Provenance.** Base spec by an Opus agent (PR #67). Two implementation attempts: variant **A**
(upstream #112, 1753+/0−) and variant **B**
(upstream #109, 319+/0−). **Both are CI-green and both are
incomplete.** Neither is a usable base.

---

## 1. Both variants are green because they delivered no tests

This is the most instructive failure in the programme, and it is the opposite shape of every other one.

| | A (#112) | B (#109) |
|---|---|---|
| Files | `components/dimensions.py` **only** | `components/layout.py`, `svg.py` |
| Lines | 1753+ / 0− | 319+ / 0− |
| Test files added | **none** | **none** |
| `+def test_` occurrences | **0** | **0** |
| CI | green | green |
| Repo-wide count | unchanged | 635 passed — **identical to `main`** |

**A PR that adds no tests cannot fail the suite.** Both are green for that reason and no other. B's repo-wide
count is byte-identical to `main`'s, which is the tell: nothing new is being exercised at all.

Both briefs required *"Every acceptance test from the spec's numbered list, as pytest tests … named for the
behaviour it asserts"*, and the base spec lists **29** numbered acceptance tests. Neither variant wrote one.
Neither delivered CLI wiring or README documentation either.

### The lesson, recorded because it generalises

**CI green does not mean delivered.** Every other pair in this programme announced its problems in red; this
one announced nothing. The coordinator's own diff review — one of the five conditions on the Tier-B merge bar —
is what caught it, and it is the only thing that could have. A reviewer who trusted the check marks would have
merged two empty features.

**Binding for the final build:** a green suite is necessary and **not** sufficient. Report the **delta** in test
count, not just the absolute, and state which numbered acceptance tests are implemented. A delta of zero on a
feature PR is a red flag regardless of colour.

---

## 2. Starting point for the build

Neither variant is a base, but A's work is not worthless:

- **A's `components/dimensions.py` (1753 lines)** is a substantial module and the only attempt at the spec's
  §3 design. **Read it, keep what matches the spec, and discard anything unverifiable** — with no tests, none
  of its behaviour has been demonstrated. Treat every line as unproven.
- **B's approach is not viable as a base:** 319 lines spread into `layout.py` and `svg.py`, i.e. it modified
  two files P10 and P1/P7 have work in, rather than adding a module. Given P10 has shipped stable ids and P7
  has shipped the layer table into `svg.py`'s pen path, edits there need a much stronger justification than
  B offered (it offered none — its report was empty).
- **Prefer a new module** (`components/dimensions.py`) over edits to `layout.py`, so the geometry reuse is an
  import rather than an entanglement.

---

## 3. The spec's design stands, and its central constraint is now sharper

The base spec's core finding is unchanged and is the reason this PR is hard:

> Tag *and* type are both insufficient at Basin: **8 of 10 placements are untagged, and three of those share
> `type: dosing-skid` with identical `size_m`** — so there is no author-visible handle for them at all.

Therefore, still binding:

- **Ordinal / "nth-of-type" / nearest-match references are forbidden.** They would inherit P10's exact defect:
  register order was GeoJSON export order, so a reorder silently re-points a dimension at different equipment.
- **An unresolvable reference is a hard error with an actionable message** — never a guess, never a blank.
- **P10 has now shipped**, so `Placement` carries a real identity (`editor.id_field` → `tag` → none) and a
  canonical sort. Use it directly rather than the `getattr` shim the base spec specified when it did not exist.
  Consequence, accepted: **only `STA-A` and `STA-B` are dimensionable today** — that is a data gap
  (issue #52 / #69), not a tooling one, and the tool must say so rather than invent a handle.

### The witness invariant is the heart of it

`polygon_gap` returns a scalar; a dimension needs **two points**, and for parallel rectangles the
minimum-distance pair is **non-unique**. The base spec's resolution stands:

- the scalar always comes from `polygon_gap` (the same function `check_layout`'s `clear-spacing` uses, so a
  sheet **cannot** contradict its own check);
- the witness pair comes from a two-branch rule (facing-overlap midpoint / vertex pair with an explicit
  tie-break);
- and a **witness invariant** — `drawn_length == value`, else raise — makes it *structurally impossible* to
  ship a line spanning one distance labelled with another.

That invariant is the single most important line in the spec. Keep it, and test it.

### The setting-out table cannot have a model-derived Z

`Placement` has no elevation and `derive_placements` reads a 2-D centroid; the surveyed P5 level
(56.947 m MSL) is the only level in the entire dataset. So `z:` is a **mandatory explicit policy**, and
`z: {source: none}` renders the literal `NOT SURVEYED` with a required note. No default can exist that does not
either invent a level or hide its absence.

### Anti-invention is enforceable, not aspirational

No author-supplied value anywhere, and a `label` matching `\d[.,]\d` is **rejected at load** — pointing the
author at `expect_m`, which is *checked* and never printed. This is the mechanism that makes "never invent a
dimension" a property rather than a hope.

### Reference values computed from the real register

For the acceptance tests, from `basin.effective.yaml`: clearance **1.99985** (not 2.0 — the register rounds to
3 dp, which is where the `expect_tol_m: 0.002` default comes from), centres **7.29985**, envelope
**8.301 / 12.600**, P5→STA-A **6.039 W / 5.992 N / 8.507 direct**.

---

## 4. Implementation plan

1. Start from the **base spec**, using A's `dimensions.py` as reference material only — nothing in it is
   proven.
2. Implement all **29** numbered acceptance tests. State which numbers map to which test functions.
3. Consume, do not rebuild: **P10's** identity and canonical sort · **P5's** collision checks for text
   placement · **P3's** rounding and `write_text_canonical` · **P1's** paper-space for witness-line geometry ·
   `layout.py`'s `polygon_gap` for every distance.
4. Add CLI wiring and README documentation — both variants omitted these.
5. **Report the test-count delta**, not just the absolute, and run the suite under at least three
   `PYTHONHASHSEED` values.
6. Prove the anti-contradiction property by asserting a dimension's value **against `check_layout`'s own
   `clear-spacing` output** for the same pair, not against a literal.

## 5. Constraints (absolute)

- **Never invent a dimension.** Every number is computed from a declared input or is a stated open question.
  This is the most important rule for this PR.
- **A dimension must never contradict the geometry it annotates** — the witness invariant enforces it.
- **Never modify what a shipped sibling owns.** P1, P3, P7, P8, P10 have landed; a P9 variant was disqualified
  for breaking P1's ISSUED-gate test.
- **The existing coordinate-taking `svg_dimension_h/v` keeps working unchanged.**
- **The ISSUED gate is untouchable** — a dimensioned drawing is still CONCEPT until the responsible engineer
  signs it.
- **Never assert byte-equality against `drawings/example/*/out/`** (issue #101).
- **A zero test-count delta is a failure**, whatever CI says.

---

## 6. Corrections the build found (appended at merge, 2026-07-25)

This spec was written before the build ran and then **never merged** — it sat on a local branch while every
sibling FINAL spec landed. Merged now for completeness, with the build's findings appended in the house shape.

### CORRECTION 1 (binding) — the base spec asserted the OPPOSITE of what P3 shipped

Base spec §4.4 mandated `Decimal(ROUND_HALF_UP)` **and claimed that was "what P3's byte-identical requirement
needs".** P3 shipped half-**even**. Verified against shipped code:

```
fmt(0.0625, 3) = 0.062      <- half-EVEN
fmt(2.0625, 3) = 2.062
half-UP would give 0.063 / 2.063
```

and `provenance.fmt`'s docstring **explicitly forbids** the `Decimal` path. §4 above already said "consume P3's
rounding, do not re-implement", so the final spec and the base spec it amends were in direct conflict — with the
base spec's version supported by a **false claim about a sibling**.

Had the build followed the spec instead of checking the shipped code, dimensions would round differently from
every other number in the toolkit at ties — two values for one measurement on the same drawing, appearing only
at odd multiples of 1/16 m, which do occur in real work (`788416.0625` is in P3's own analysis).

**Generalised rule:** a spec is authoritative about **intent**, never about what a sibling **actually shipped**.
Check the code. Tracked as upstream #123, closed.

### CORRECTION 2 — the zero-test finding generalised beyond this pair

§1 called this "the most instructive failure in the programme". It turned out not to be a one-off. The same
signature appeared in **P4 variant B** (#110): 1200+ lines, zero tests, CI-green, no README. Test-function
deltas against dispatch time:

| Dispatched | Variants | Tests added |
|---|---|---|
| Before the outage | P5-a, P9-a, P2-a, P2-b | 20, 26, 36, 37 |
| **During the Codex `503 circuit_open` window** | **P6-a, P6-b, P4-b** | **0, 0, 0** |

A degraded backend does not fail loudly — it **truncates**, dropping the last step of the brief. §1's binding
requirement (report the test-count **delta**) is the only control that catches it, and it is now a standing rule
for every agent PR in this repo, not just this one.

### What shipped

**#121**, on the base spec with A's module as unproven reference material only. The witness invariant and the
`\d[.,]\d` label rejection both survived into the shipped code. **P11**
(upstream #134) then built on it: a declared chain's rounded
components may not contradict its rounded overall — the failure mode rounding *introduces*, which this spec's
§3 did not anticipate.
