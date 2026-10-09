# P11 — chain consistency: rounded components must sum to the rounded overall

**Issue:** #130. **Owner:** `senior-engineer`. **Depends on:** P6 (#58, shipped — owns `dimensions:`),
P3 (#55, shipped — owns rounding), P5 (#57, shipped — owns the finding shape).

---

## 1. Intent

A drawing that dimensions both the parts of a run **and** its overall length must not contradict itself.
Displaying at millimetre precision is correct — it is the precision things are built to — but rounding each
component independently can make the components stop summing to the overall. The drawing then carries two
mutually inconsistent statements, both reading as authoritative, and the discrepancy is invisible in any
single number: you only see it by adding the chain up.

**Good** is: a declared chain whose displayed components sum exactly to its displayed overall, or a loud
failure naming the run and the discrepancy in millimetres. The tool makes the contradiction impossible to ship
silently. It does **not** choose the resolution — that is a drafting decision.

## 2. Why rounding is not the bug, and must not be changed

P6 already displays at `decimals: 3` (millimetres), and that is right. Verified against the live Basin values:

| exact | displayed |
|---|---|
| 1.9998519771777388 | **2.000** |
| 7.299852053263156 | **7.300** |
| 12.599851977312937 | **12.600** |

So the model stays exact and the label is honest at buildable precision. **Do not round the model** — that
accumulates error. **Do not change `provenance.fmt`** — P3 owns it, it is half-even, and its docstring forbids
the `Decimal` path.

The failure this spec addresses is the one rounding *introduces*:

```
three bays, each 2.4995 m  ->  each displays 2.499,  sum of displayed = 7.497
true overall 7.4985 m      ->  displays 7.498
=> the drawing contradicts itself by 1 mm
```

On the current Basin numbers the chain happens to be consistent (`5.300 + 2.000 + 5.300 = 12.600`, overall
also `12.600`) — that is the luck of these values, not a property. Ties at the displayed precision are where
it bites.

## 3. Design

### 3.1 Schema — a new top-level `chains:` block

`components/dimensions.py` currently has `_TOP_LEVEL_KEYS = {dimension_set, defaults, datums, naming, routes,
dimensions, setting_out}`. Add **`chains`**.

```yaml
chains:
  - id: raft-cross-axis                  # required, unique, matches the id pattern used by dimensions
    overall: envelope-cross              # required: id of ONE existing dimension
    components: [raft-a-width,           # required: >=2 ids of existing dimensions, IN ORDER
                 raft-clear,
                 raft-b-width]
    severity: error                      # optional, default error (see 4.3)
    note: "5.3 + 2.0 + 5.3 across the platform"   # optional, free text for the failure message
```

Rules, all validated **on load**, each error naming the offending YAML path:

1. `id` — required, non-empty, unique across `chains`. Reuse P6's existing id validator.
2. `overall` — required; must name exactly one `dimensions[].id` that exists. An unknown id is a hard error
   naming both the chain and the missing dimension.
3. `components` — required; a list of **at least two** existing `dimensions[].id`s. Order is significant and
   preserved (it is the order a reader adds them up in). Fewer than two is an error: a one-component chain is
   not a chain and silently accepting it would hide a truncated declaration.
4. A dimension id may appear in `components` of at most one chain, and may not be both `overall` and a
   component of the same chain. Both are hard errors.
5. Unknown keys inside a chain entry are rejected, matching P6's existing `_reject_unknown` style.

### 3.2 The check

For each declared chain, after every dimension's value is computed:

```
displayed(d)      = the string P6 would print for dimension d, parsed back to a float
sum_components    = sum(displayed(c) for c in components)
displayed_overall = displayed(overall)
discrepancy_mm    = (sum_components - displayed_overall) * 1000
```

**Compare at the displayed precision, not the model precision.** That is the entire point: the model agrees
and the display does not. Comparing model values would always pass and the check would be worthless.

Pass when `abs(discrepancy_mm) < 0.5` — i.e. the two agree to the last displayed digit. Use a tolerance rather
than exact float equality because summing rounded floats reintroduces binary representation error
(`2.499 + 2.499 + 2.499 != 7.497` exactly in IEEE 754); the tolerance is half a display unit, derived from the
precision, not a magic number.

**Derive the tolerance from `decimals`**, do not hard-code millimetres: a chain of dimensions displayed at
`decimals: 2` must compare at half a centimetre. If the components and the overall have **different**
`decimals`, that is a hard error on load — comparing across precisions is meaningless.

### 3.3 The finding

Reuse P5's `Finding` shape. On mismatch:

```
ERROR: chain-consistency: chain 'raft-cross-axis': components sum to 7.497 m but
  overall 'envelope-cross' displays 7.498 m — 1.0 mm apart at 3 dp.
  components: raft-a-width 2.499 + raft-clear 2.499 + raft-b-width 2.499
  note: 5.3 + 2.0 + 5.3 across the platform
```

It must name the chain id, both totals, the discrepancy **in millimetres**, and every component with its
displayed value — so a reader can see immediately which one to adjust. Include `note` when present.

### 3.4 Public API

```python
@dataclass(frozen=True)
class Chain:
    id: str
    overall: str
    components: tuple[str, ...]
    severity: str = "error"
    note: str = ""

def check_chains(spec: DimensionSpec, values: Mapping[str, float]) -> list[Finding]:
    """Assert every declared chain's displayed components sum to its displayed overall."""
```

`values` is the already-computed model value per dimension id, so the check does not recompute geometry and
cannot disagree with what the sheet drew. Follow P6's house pattern: frozen dataclass, module error class,
validation on load with precise paths.

### 3.5 CLI

The check runs as part of the existing `dimensions` verb and is reported with its other findings. An `error`
finding exits non-zero, consistent with every other check in the toolkit. No new verb.

## 4. Behaviour decisions, with rationale

**4.1 Compare displayed, not model.** Stated above; it is the whole point and the most likely thing an
implementer gets wrong. Add a test that would fail if model values were compared (i.e. one where the model sums
correctly and the display does not).

**4.2 Tolerance is half a display unit, derived from `decimals`.** Not hard-coded mm. A `decimals: 2` chain
compares at 5 mm. Mixed `decimals` within a chain is a load error.

**4.3 Severity defaults to `error`, not `warn`.** An internally inconsistent drawing is worse than a missing
dimension because it reads as authoritative and a builder may act on either number. `severity: warn` is
allowed for a CONCEPT sheet where the drafter knows and has not yet resolved it, but the default must make it
stop the build.

**4.4 The tool never adjusts a component to make the sum work.** Silently absorbing the discrepancy into one
bay would hide a real ambiguity from the person building it. Standard drafting practice is for a human to
either carry the discrepancy in a nominated bay or dimension the overall only — the tool reports, the drafter
decides. **Do not add an `auto_adjust` option.**

**4.5 Do not infer chains from geometry.** A reader's mental chain is a drafting intention, not a geometric
fact — collinear dimensions are not necessarily a run, and a run may not be collinear. Inferring would produce
false positives on unrelated dimensions, which is how a check gets switched off (P5's precision-over-recall
stance). Chains are **declared**.

**4.6 A chain over dimensions of different `kind` is allowed.** A real run mixes `clearance` and `envelope`
(the Basin cross-axis is two widths and a clearance against an envelope). Do not restrict by kind.

## 5. Acceptance tests

1. `test_a_consistent_chain_passes` — the real Basin cross-axis (`5.3 + 2.0 + 5.3` against `12.6`) yields no
   finding. Use the live values from `basin.effective.yaml`.
2. `test_rounded_components_not_summing_to_the_rounded_overall_is_an_error` — the three-bay 2.4995 case;
   assert exactly one `chain-consistency` finding, severity `error`, message containing `1.0 mm`, both totals,
   and all three component values.
3. `test_the_check_compares_displayed_values_not_model_values` — construct a chain whose **model** values sum
   exactly but whose **displayed** values do not. Must fail. This is the test that catches the wrong
   implementation.
4. `test_tolerance_is_derived_from_decimals_not_hard_coded_mm` — the same discrepancy passes at `decimals: 2`
   and fails at `decimals: 3`.
5. `test_mixed_decimals_within_a_chain_is_a_load_error` — components at 3 dp, overall at 2 dp → `LayoutError`
   (or P6's error class) naming the chain.
6. `test_chain_with_fewer_than_two_components_is_a_load_error`.
7. `test_chain_naming_an_unknown_dimension_id_is_a_load_error` — for both `overall` and a component; the
   message names the chain and the missing id.
8. `test_a_dimension_cannot_be_both_overall_and_component_of_one_chain`.
9. `test_a_dimension_cannot_appear_in_two_chains_components`.
10. `test_duplicate_chain_ids_are_a_load_error`.
11. `test_unknown_key_in_a_chain_entry_is_rejected`.
12. `test_severity_warn_reports_but_does_not_fail` — exit code 0 with the finding present.
13. `test_a_chain_over_mixed_dimension_kinds_is_allowed` — clearance + envelope in one chain, no finding.
14. `test_no_chains_declared_means_no_findings_and_no_behaviour_change` — **the backward-compat test.** A
    dimension spec with no `chains:` block produces byte-identical output to before.
15. `test_the_finding_names_every_component_with_its_displayed_value` — assert the message content, so a
    future refactor cannot quietly reduce it to a bare total.

## 6. Constraints — absolute

- **Backward compatibility is sacred.** A spec with no `chains:` block behaves exactly as today, byte-identical.
  Test 14 is required.
- **Do not change `provenance.fmt` or P6's rounding.** P3 owns rounding; it is correct.
- **Do not modify anything a shipped sibling owns.** P1 (`sheet.py`), P3 (`provenance.py`), P5
  (`legibility.py`), P7 (`layers.py`), P9 (`revisions.py`, `titleblock.py`). A sibling variant was
  disqualified earlier in this programme for breaking P1's ISSUED-gate test. Consume public interfaces; if a
  sibling's test looks wrong, **stop and say so** rather than editing it.
- **Never assert byte-equality against `drawings/example/*/out/`** — those predate P3 and carry six volatile
  fields, so such a test is unsatisfiable by construction (issue #101). Compare
  `provenance.mask_dxf_volatiles(fresh)` against `tests/goldens/*.masked`.
- **Never pin a literal output `sha256`** — it cannot distinguish "we regressed" from "a dependency updated".
- **The ISSUED gate is untouchable.** A clean chain check is not approval to issue.
- **Loud failure over a silent no-op.** An unresolvable id, a mixed-precision chain, a one-component chain:
  all hard errors naming the offending path.
- **House style:** `components/dimensions.py` is both the reference pattern and the file being changed —
  frozen dataclasses, one module error class, validation on load with precise messages, no bare `except`.

## 7. Out of scope

- Inferring chains from geometry (4.5).
- Auto-adjusting components (4.4).
- Two-dimensional chain grids (a chain is one run; a grid is several chains).
- Changing any rounding behaviour.
- Rendering a chain differently on the sheet — this is a **check**, not a new dimension kind.

---

## 8. CORRECTIONS from the build (PR #134)

Three errors in this spec, found during implementation. Two are straightforward mistakes of mine; the third is
a genuine deepening of the argument.

### 8.1 §3.4's signature was unimplementable — my error

I wrote `check_chains(spec: DimensionSpec, values)`. `DimensionSpec` is **one dimension**; it holds neither
`chains` nor its siblings' `decimals`, both of which the check needs. Shipped correctly as `DimensionSet`.

### 8.2 §3.1 rule 1 cited a validator that does not exist — my error

"Reuse P6's existing id validator" — P6 has no such helper and no id pattern; it inlines the checks. The build
mirrored them inline rather than refactoring shipped P6 code, which was the right call: a spec should not
provoke a refactor of a sibling to satisfy a citation that was wrong in the first place.

### 8.3 §2's worked example is knife-edge in binary — and this strengthens the case for the check

`2.4995` written as a **literal** displays `2.499`. But the *same nominal bay* computed as a **difference of
running grid coordinates** — `7.4985 − 4.999` — is `2.4995000000000003`, which displays `2.500` and cancels the
discrepancy entirely. The build's first demonstration fixture hit exactly this and passed.

Both sheets are internally consistent **as printed**, so both must pass, and they do. But the implication is
sharper than §2 suggested: **whether a chain contradicts itself can flip on the last bit of a coordinate**,
depending on whether a bay arrives as a literal or as a subtraction. That is not something a human reviewer
could ever predict from looking at the drawing, which is a better argument for having the check than the one I
wrote. Pinned by a dedicated test so a future tidy-up cannot quietly turn a failing fixture into a passing one.

### 8.4 Verified independently

`708 → 732 passed`, delta **+24**, 0 failed, green under three `PYTHONHASHSEED` values; `chain_tolerance_mm`
confirmed as 0.5 mm at 3 dp and 5.0 mm at 2 dp — derived, not hard-coded. The live Basin cross-axis confirms
the design end to end: its **model** values do not sum (`12.599851977 != 12.599851977` by float), its
**displayed** values do (`12.600 == 12.600`), and the chain passes. Comparing model values would have been
worthless, exactly as §4.1 warned.

The build also proved the wrong implementations wrong *before* fixing them: comparing model values failed
tests 2, 3, 4, 12, 15; hard-coding the tolerance to 0.5 failed test 4 alone. That is the discipline this spec
asked for.
