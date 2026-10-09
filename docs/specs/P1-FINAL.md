# P1 — FINAL specification and implementation plan

**Status:** authoritative. Supersedes `P1-paper-space-sheet-scale.md` **only where it says so below**;
everything not corrected here stands as written in that document.

**Provenance.** The base spec was written by an Opus agent (PR #63). Two Codex agents then implemented it
independently — variant **A** (upstream #77) and variant **B**
(upstream #78), both CI-green. This document records the
comparison, the three spec defects the pair exposed, and the resulting no-regrets basis for the build.

Read this **with** the base spec. This is a delta, not a replacement — the base spec's 1257 lines of design,
data model, API, CLI surface and rationale are unchanged and remain the implementer's primary reference.

---

## 1. Why two implementations were worth it

The two variants landed at 2336 and 2320 additions across **the same nine files**, and their `PlotScale`
class is **character-for-character identical** apart from one defensive `str()` and a
constant-versus-function lookup. That convergence is the base spec's success: it was precise enough that two
independent implementations agreed on observable behaviour.

The divergence is where the value was. **Neither variant found all three spec defects.**

| Defect | A | B |
|---|---|---|
| Scale-bar fit rule contradicts test 16 | **found** | **found — identical fix** |
| Acceptance tests 18 and 19 mutually unsatisfiable | **found**, fixed by changing the fixture | **found**, "fixed" by reinterpreting the assertion |
| "40 m outside the extent" is direction-sensitive | **missed** | **found** |

A single implementation would have shipped with at least one of these unresolved.

---

## 2. Spec defect 1 — the scale-bar fit rule (both variants, identical fix)

**Base spec §3.2** permits a scale bar where `length_m * 1000 / N <= viewport_frame.width_mm`.
**Acceptance test 16** requires `scale_bar.length_m: 500` on an A3 1:1250 sheet to **raise**.

These cannot both hold. Verified:

```
A3 width                 420.0 mm
less 2 x 10 mm margins   400.0 mm   <- viewport_frame.width_mm
500 m at 1:1250          400.0 mm   <- exactly equal
400.0 <= 400.0           True       -> §3.2 admits it; test 16 demands it raise
```

**CORRECTION (binding).** A scale bar must fit within its own inset, not flush to the frame edge. The rule is:

```
length_m * 1000 / N  <=  viewport_frame.width_mm - 2 * SCALE_BAR_INSET_MM
```

with `SCALE_BAR_INSET_MM = 6.0`. At A3 1:1250 that leaves 388.0 mm, so a 400.0 mm bar raises, satisfying
test 16. Both variants arrived at this independently, which is why it is adopted verbatim rather than
re-litigated. It is also the physically correct answer: a bar drawn flush to the frame edge has no clearance
from the border and is not printable as drawn.

**Error message must name the offending path** (`sheet.viewport.scale_bar.length_m`) and state both the
required and the available width in mm.

---

## 3. Spec defect 2 — acceptance tests 18 and 19 are mutually unsatisfiable

**Test 18** sets up an **A3 1:1250** sheet and requires *exactly one* warning containing
`beyond the viewport frame`. **Test 19** states that an **A3 1:1250** sheet *always* warns
`not an ISO 5455 preferred ratio`. So test 18's single-warning assertion is unreachable at that scale.

**A's resolution:** run the drawn-extent case at **1:1000** — a preferred ratio, so no second warning.
**B's resolution:** keep 1:1250 and redefine "validates clean" as "no problems / `ok is True`", tolerating
the extra warning.

**CORRECTION (binding): take A's.** B's reinterpretation leaves the test's literal assertion ("one warning")
unmet and makes the test insensitive to a spurious *second* warning appearing later — exactly the regression
the assertion exists to catch. A's fix removes the ambiguity instead of redefining around it.

- **Test 18** uses a **1:1000** A3 sheet. Assertion stays `exactly one warning`.
- **Test 19** keeps 1:1250 and asserts the non-preferred warning, as written.
- Where the base spec says an A3 1:1250 sheet "validates clean", read it as **`ok is True` with exactly one
  warning (the non-preferred-ratio warning)**. State this explicitly in the docstring so the next reader
  does not hit the same contradiction.

---

## 4. Spec defect 3 — the overflow test is direction-sensitive (B only)

**Base spec test 18** says "`add_model` a point 40 m outside the declared extent". B found that this only
overflows in one axis. Verified against the real Basin frame:

```
A3 landscape frame            400.0 x 277.0 mm
extent 310 x 270 m at 1:1250  248.0 x 216.0 mm
spare paper each side          76.0 mm horizontal,  30.5 mm vertical
= ground headroom              95.0 m  horizontal,  38.1 m  vertical

point 40 m outside horizontally:  40 <  95.0  -> STILL ON PAPER, no warning
point 40 m outside vertically:    40 >  38.1  -> overflows by 1.9 m, warns
```

The base spec's test therefore passes or fails depending on which axis the implementer chooses, and B's
vertical fix survives on a **1.9 m margin** — one change to the frame or margins and it silently stops
testing anything.

**CORRECTION (binding), stronger than either variant.** The overflow fixture must be **direction-independent
and not marginal**:

- Place the probe point outside the declared extent in **both axes simultaneously**, at an offset of
  **at least `max(spare_x, spare_y) + 10 m`** computed from the sheet under test — not a hard-coded 40 m.
- Assert the warning **and** assert `drawn_extent_mm` exceeds `frame_mm` in **both** dimensions, so the test
  cannot pass on a single-axis accident.
- Add a **negative** companion test: a point 40 m outside the extent *horizontally only* must **not** warn on
  this frame, with a comment stating why (95 m of spare paper). This pins the geometry that made the original
  test ambiguous, so the ambiguity can never silently return.

This is the one place the final spec goes beyond both variants. Neither is wrong; both are fragile.

---

## 5. Implementation basis and grafts

**Base: variant A (#77).** Same file set as B, cleaner resolution of defect 2, and its
`test_legacy_sheets_render_byte_identically` parity test is the right shape — it re-renders the shipped
`simple-section` example and asserts **byte equality** against the committed golden SVG, plus asserts the
legacy `svg_scale_bar(label=…)` slot and the `Drawing` SVG root are unchanged.

**Graft from B:**

1. **`PlotScale.parse` wraps its argument in `str()`.** Semantics are unchanged — `str(1250.0)` is
   `"1250.0"`, which still fails the regex and raises — but it turns a `TypeError` on a non-string YAML value
   into the intended `SheetError`. Strictly better.
2. **Prefer a module constant** (`ISO_PREFERRED_DENOMINATORS`) over A's `_strict_preferred_denominators()`
   function for `is_preferred`. Simpler, and the set is static.
3. **B's split of test 6** into metadata mutations plus a root-width tamper, for failure isolation.
4. **B's direction-sensitivity insight**, implemented as §4 above rather than as B wrote it.

**Reject from B:** the redefinition of "clean" (see §3).

**Carry from A but fix:** A injects a test-only `class="title-block"` marker into otherwise-clean
paper-space SVGs, because the base spec deliberately refuses to render P9's title block. That means those
tests assert against a hand-doctored artifact. **Instead:** `SheetDrawing` must emit a real, minimal
**title-block reservation placeholder** — an empty `<g class="title-block" data-tdfa-placeholder="true">`
at the reserved rectangle — so validation has a genuine element to find and P9 later replaces its contents
without touching the reservation geometry. No test may fabricate sheet structure.

---

## 6. No-regrets code — adopt verbatim

Both variants produced this independently and identically (modulo the two grafts in §5). It is settled; do
not redesign it.

```python
@dataclass(frozen=True)
class PlotScale:
    """An exact 1:N plot scale. N is a positive integer denominator."""

    denominator: int

    def __post_init__(self) -> None:
        if isinstance(self.denominator, bool) or not isinstance(self.denominator, int):
            raise SheetError("plot scale denominator must be a positive integer")
        if self.denominator < 1:
            raise SheetError("plot scale denominator must be a positive integer")

    @property
    def text(self) -> str:
        return f"1:{self.denominator}"

    @property
    def is_preferred(self) -> bool:
        return self.denominator in ISO_PREFERRED_DENOMINATORS

    def mm_per_m(self) -> float:
        return 1000.0 / self.denominator

    def paper_mm(self, model_m: float) -> float:
        return float(model_m) * self.mm_per_m()

    def model_m(self, paper_mm: float) -> float:
        return float(paper_mm) / self.mm_per_m()

    @classmethod
    def parse(cls, text: str) -> "PlotScale":
        match = re.fullmatch(r"\s*1:([1-9][0-9]*)\s*", str(text))
        if match is None:
            raise SheetError(
                f"cannot parse plot scale {text!r}: expected '1:N' with an integer N"
            )
        return cls(int(match.group(1)))
```

Note the deliberate `bool` guard: `isinstance(True, int)` is `True` in Python, so `scale: true` would
otherwise be accepted as `1:1`. Both variants guarded it. Keep it.

The **parity test** is likewise settled — keep A's shape, asserting all three of: golden-SVG byte equality,
the legacy `label=` caption slot still emits, and the `Drawing` SVG root string is unchanged.

---

## 7. Implementation plan

1. Start from the base spec. Implement `src/technical_drawings_for_agents/sheet.py` per its §3.7 API.
2. Adopt §6 verbatim.
3. Apply the three corrections: the `- 2 * SCALE_BAR_INSET_MM` fit rule (§2), test 18 at 1:1000 (§3), and
   the direction-independent overflow fixture plus its negative companion (§4).
4. Emit the real title-block placeholder (§5) — no test-only markers.
5. Additive-only changes to `validate.py`, `cli.py`, `meta.py`, `__init__.py`, README. **Do not touch
   `svg.py` or `isosheet.py`** — backward compatibility in this PR is structural, not careful.
6. Tests 1–24 plus the negative companion from §4, in `tests/test_sheet.py` / `tests/test_sheet_cli.py`,
   and the parity test appended to `tests/test_example_smoke.py`.
7. CI is the gate. A local run needs `ezdxf`; do not stub it in committed code.

## 8. Constraints (unchanged, restated because they are absolute)

- **Backward compatibility is structural.** No existing sheet's bytes change. The parity test proves it.
- **The ISSUED gate is untouchable.** `sheet.py` contains no `for_construction` / status logic;
  `DrawingMeta.validate()` remains the sole authority; new checks may only *append* problems; the `sheet`
  command is read-only and must never gain `--write` or `--fix`.
- **Never invent a standard.** The 10 mm margin and the furniture dimensions remain labelled house defaults,
  not ISO citations. Base-spec open questions 1 and 4 stay open.
- **Loud failure over silent degradation.** An extent that does not fit fails, naming the smallest scale that
  would work. It never clips silently and never auto-fits without being asked.

---

## 9. Corrections to THIS document, found during the build (PR #89)

The build agent found two defects in this final spec. Recorded rather than silently patched, because the
point of the ledger is an honest account of who found what.

### 9.1 §2's YAML path was wrong

§2 names the offending path as `sheet.viewport.scale_bar.length_m`. The schema puts `scale_bar:` as a
**sibling** of `viewport:`, so the correct path is **`sheet.scale_bar.length_m`**. My error; the implemented
error message uses the correct path.

### 9.2 §5's placeholder requirement contradicted base-spec test 23 — and the build's resolution is better

§5 required `SheetDrawing` to emit a real `class="title-block"` group. Base-spec **test 23** asserts that a
sheet with `title_block=None` contains **no** title block. Those cannot both hold: my §5 was written to kill
variant A's test-only marker fabrication, but I over-specified it into an unconditional emit.

**Adopted resolution (better than what §5 said):** scope the placeholder to the **reservation**.

- Reserve a `title-block` region → an empty placeholder carrying that rectangle is emitted, so `validate`
  has a genuine element to find and P9 later fills it without touching the reservation geometry.
- Reserve nothing → nothing is emitted, so `validate`'s missing-title-block problem still fires and base-spec
  §7.1's "the gap is visible" property survives.

Both halves are asserted. The original intent — **no test may fabricate sheet structure** — is preserved,
which was the part that mattered.

### 9.3 Open concern carried forward

The build flagged an **asymmetric `FIT_TOL_MM`** between `Viewport.fits` and check V5: they agree today but
could drift apart. Not a defect yet; tracked so a future change to either does not silently create one.
