# P5 — FINAL specification and implementation plan

**Status:** authoritative. Supersedes `P5-legibility-checks.md` where it says so below.

**Provenance.** Base spec by an Opus agent (PR #76). Implemented independently by variant **A**
(upstream #103, 3442+/12−, **CI green, both jobs**) and variant
**B** (upstream #100, 3550+/13−, **5 failed / 607 passed**).

**This is the highest-leverage change in the programme.** It is what replaces "a human or an LLM rasterises the
PDF and squints at it" with deterministic checks. It is also the first pair where one variant is clean and the
other is not.

---

## 1. Variant B is disqualified on two independent grounds

B's five failures:

```
test_legacy_sheets_render_byte_identically                        (P1's parity test)
test_existing_generators_produce_unchanged_bytes_when_not_opted_in (P3's parity test)
test_no_emitter_output_changed                                     (its own)
test_the_committed_example_svgs_still_match_their_generators        (its own)
test_findings_are_deterministic                                    (its own)
```

**Ground 1 — a checker changed the drawings.** Four of the five are backward-compat/parity failures, including
two belonging to already-shipped features. A legibility *checker* inspects output; it must not alter a single
byte of it. B did.

**Ground 2 — and this one is fatal on its own — `test_findings_are_deterministic` failed.** A check whose
findings vary between runs cannot replace human review; it *is* the thing it was built to eliminate. A
non-deterministic checker is worse than no checker, because it will be disabled the first time it cries wolf.

---

## 2. Variant A is the base, and it is the strongest implementation in the programme

Green on both CI jobs. All twenty acceptance tests (5.1–5.20) implemented, none weakened. On the shipped
examples: `simple-section` **0 findings**, `synthetic-pid` **2 baselined findings** — exactly what the base
spec predicted.

**The decisive thing A did right:** the base spec found that the shipped `simple-section` example has a real
defect — its water-level leader label runs 21.1 px past the frame — and ruled that the resolution is to **fix
the example's input**, not to relax a threshold. A did precisely that (`anchor="end"` in the example's
`source.py`, with the committed SVG and golden updated in the same change). That is the difference between a
check that enforces a standard and a check that accommodates whatever it finds.

Adopt A's four judgement calls as specified:

1. **Title-block text/text overlaps are not exempt**, because the spec separately requires baselining the
   P&ID title-block cell overflows — the two rules would otherwise contradict. Exemptions are retained only
   for scale-bar internals and title-block fills.
2. **Stale baselines are relevance-based:** a P&ID baseline does not warn "stale" on an unrelated
   `simple-section` sheet unless the quoted baseline text/fill is actually present. This keeps the baseline
   mechanism from becoming noise, which is how check suites die.
3. **For a declared `inset-fill`**, content extent is inferred as
   `content_bbox / panel_box * declared_extent_m`.
4. **Baseline the *measured* Helvetica-AFM numbers**, not the spec's quoted ones (see Correction 3).

---

## 3. Three spec defects A found

### CORRECTION 1 (binding) — the spec contradicts itself on title-block exemptions

The base spec grants a same-scope exemption for title-block overlaps **and** separately requires the P&ID
title-block cell overflows to be baselined as known findings. Those cannot both hold: an exempted overlap
produces no finding to baseline.

**Resolution (A's, adopted):** title-block **text/text** overlaps are **not** exempt, so the P&ID overflows
are detected and baselined. Exemptions cover **scale-bar internals** and **title-block fills** only.

Rationale: the P&ID overflows are real defects — `isosheet.titleblock`'s `val()` has no truncation, unlike
`svg.svg_title_block` (`svg.py:566-576`) — so they should be visible and owned by P9, not silently exempted.

### CORRECTION 2 (binding) — the declared `inset-fill` formula is under-specified

The declaration schema carries no panel scale or content extent, so the fill ratio cannot be computed from the
declaration alone. **Adopt A's inference** (`content_bbox / panel_box * declared_extent_m`) and **record it in
the schema documentation** so a second implementer derives the same number.

### CORRECTION 3 (binding) — the spec's P&ID calibration numbers are wrong

The base spec quotes larger overlap dimensions than its **own** stated Helvetica-AFM method produces. A
measured `17.2 × 7.7 px` and `5.5 × 6.2 px` from the AFM table.

**The measured values win.** A spec that pins a number its stated method does not produce is asserting a
result rather than deriving one — the same failure mode as a hand-typed scale string, which is what this whole
programme exists to eliminate. Baseline the AFM-derived values.

---

## 4. Additional fixtures required (from live use, 2026-07-24)

Beyond the base spec's three fixtures, these three patterns were observed while hand-tuning a real sheet and
are recorded on #57. They must be in the check set, because they are the *whack-a-mole* pattern rather than
one-off slips:

1. **Oversized callout region** — an in-view callout box spanning 87 × 48 m around a cluster occupying about
   38 × 25 m. The inset fill-ratio rule must apply to **any declared region**, not only detail panels.
2. **Marker occlusion** — numbered schedule markers drawing on top of each other at co-located placements
   (three at one location, two at another). **N markers within R px is a finding.**
3. **Fix-induced collision** — moving two labels to resolve one overlap created a *new* overlap between them.
   Three rounds of by-eye tuning produced one regression. This is the single strongest argument for the
   checker existing: a human moving labels cannot see what they break.

---

## 5. Carried forward unchanged — the base spec's measured findings

- **Bounding boxes come from parsing the emitted SVG** with stdlib `ElementTree` plus a font-metrics estimate.
  This is the right call and must not be redesigned: the toolkit's helpers are **string builders**
  (`svg_text` returns a `<text>` string, `svg.py:207-212`; `Drawing.elements` is a `list` of strings,
  `svg.py:663`), so no model knows where text landed. Parsing the output covers all three sheet paths that
  exist — `Drawing`, `isosheet`/`pid`/`bfd` (which bypass `Drawing` entirely), and hand-assembled project
  `source.py` — with **zero refactor and zero byte change**.
- **Text width is defensible, not hand-waved:** `svg_text` hard-codes `font-family="monospace"`
  (`svg.py:211`) and every practical mono font advances 0.600–0.6021 em, so the estimate is accurate to
  **±0.5 %**.
- **`<defs>`/`<pattern>` must be skipped** — the hatch tile otherwise reports as out-of-frame geometry at
  (0, 0).
- **Containment must not inflate by stroke width** — `svg_title_block` sits flush with `svg_border` at the
  same margin (`svg.py:556-557` vs `597`), so a hairline inflation would fail every `Drawing` sheet ever made.
- **Precision over recall.** A noisy checker gets disabled, so deliberate overlaps are whitelisted
  *structurally*: an opaque shape may carry text it fully contains (bubbles pass; a 14 × 11 swatch cannot
  contain a 103 px label). Concentricity and WCAG contrast were considered and rejected with reasons.
- **Thresholds derive from print physics** — 1 px = 0.2625 mm at the current A3 canvas, so 1.5 px overlap
  ≈ 0.4 mm.
- **`issued-gate` severity is non-downgradable and the module has no write path.**

---

## 6. Implementation plan

1. Base on variant A — it is green; the corrections below are refinements, not repairs.
2. Apply Corrections 1–3 (they are already A's behaviour; **write them into the spec text** so a future
   implementer does not re-derive them).
3. Add the three §4 fixtures.
4. Keep A's input fix to the shipped example. **Do not** relax a threshold to accommodate a real defect.
5. Full suite green, and **zero findings on every shipped example** apart from the explicitly baselined P&ID
   title-block overflows — otherwise the checker will be ignored.
6. Compare against **P3's masked goldens**, never `drawings/example/*/out/` — see **#101**.

## 7. Constraints (absolute)

- **A checker must not change a single byte of any drawing's output.** This is what disqualified B.
- **Findings must be deterministic.** Same input, same findings, same order, every run.
- **Precision beats recall.** A false positive costs more than a missed finding, because it gets the whole
  suite switched off.
- **The ISSUED gate is untouchable.** Passing every legibility check is **not** approval to issue — that
  remains the responsible engineer's signature.
- **Never invent a threshold.** Every number traces to print physics or a measured font metric.
