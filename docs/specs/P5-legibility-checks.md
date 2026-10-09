# P5 — Legibility checks: annotation collision + frame containment

**Issue:** upstream #57 (absorbs review items 22, 21)
**Owner:** `senior-engineer` · **Status:** **implemented** — see `src/technical_drawings_for_agents/legibility.py`

> **Read `P5-FINAL.md` first; it is authoritative and wins on conflict.** This document is the base
> spec: everything it says still stands *except* where a ⚠️ **CORRECTED** note appears inline. The three
> corrections are written into the text at §2.6 (Correction 3 — the P&ID calibration numbers), §4.2
> (Correction 1 — title-block exemptions), and §4.5 (Correction 2 — the declared `inset-fill` formula),
> so no future implementer has to re-derive them. The three additional fixtures from live use are
> specified at §4.5a, §4.5b, §5.3a, §5.3b and §5.3c.
**Source review:** `03-Resources/DEMO Water Project/drawings/Drawing workflow — 30-change robustness review.md`
**Spec date:** 2026-07-24 · **Repo:** `technical_drawings_for_agents/`

---

## 0. Hard constraints (restated — these bind the implementer)

1. **Backward compatibility is sacred.** Adding these checks must not change a single byte of any
   drawing's emitted output, and must not change the behaviour or exit code of any existing
   `technical_drawings_for_agents` invocation. No emitter (`svg.py`, `isosheet.py`, `bfd.py`, `pid.py`) gains, loses or
   reorders an attribute in this PR. New behaviour is opt-in (a new subcommand, and a new opt-in flag
   on `validate`).
2. **Never invent a capability.** An SVG string has no layout engine. Where a check cannot be done
   reliably on today's architecture, this spec says so, says what would be needed, and makes the
   check **report itself as skipped** — never silently pass.
3. **The ISSUED gate is untouchable.** No part of this checker may set, propose or imply a status
   change. Passing every legibility check is **not** approval to issue. The checker never writes
   `meta.yaml`. `status-coherence`'s ISSUED sub-rule is the one finding whose severity cannot be
   downgraded by config.
4. **Determinism: prefer loud failure over a silent no-op.** No font files are loaded, no rasteriser
   is invoked, nothing is sampled. Identical inputs give byte-identical findings on every machine.
   An element form the parser does not understand raises, it does not get skipped.
5. **House style.** Frozen dataclasses; one module-specific error class (`LegibilityError`); config
   validated on load with precise, file-prefixed messages; the `Finding` shape mirrors
   `components/layout.py:111-118`; tests in `tests/test_*.py` named for the behaviour they assert.

---

## 1. Intent

A sheet is *good* when everything a reviewer must read can actually be read: no label sits on top of
another label or on top of an opaque shape that hides it, nothing has drifted outside the sheet frame
or over the title block, a detail panel is filled by its detail rather than by empty space, and the
sheet's own claims about itself — the status watermark, the revision, the legend, the flagged
verify items — agree with the data that produced it. Today all of that is established by a human (or
an LLM) rasterising the PDF and squinting at it. In one session that process found three real
defects; this change turns the squint into arithmetic, so the next three are found by CI in
milliseconds and cannot be missed by a tired reviewer at 23:00.

**The failure it prevents:** a sheet that *looks* authoritative — correct geometry, correct numbers —
but is unreadable or self-contradictory in the exact places a reviewer forms their first judgement.
That is the fastest way to lose technical credibility with a counterparty, and it is the one class of
defect our current pipeline is structurally blind to. Secondary, and just as important: it removes an
LLM from the execution path. A checker that returns "3 findings at these coordinates" is
reproducible; "I looked at the PDF and it seems fine" is not.

---

## 2. Current state (read the code, honestly)

### 2.1 What `validate` checks today

`src/technical_drawings_for_agents/validate.py` is a **substring search over the emitted SVG text**:

* `validate.py:22-31` — `_MARK` maps three human names to three literal marker strings:
  `class="title-block"`, `class="scale-bar"`, `class="status-watermark"`.
* `validate.py:45-56` — `validate_svg()` reads the file and appends a problem per marker **not found
  as a substring**. There is no XML parse, no geometry, no coordinates.
* `validate.py:34-42` — `ValidationResult(target, problems: list[str], checked: list[str])`, `ok`
  is `not problems`. Findings are bare strings with no severity and no location.
* `validate.py:83-119` — `validate_drawing_dir()` adds `meta.yaml` validation
  (`DrawingMeta.validate()`), P&ID data-model validation, and "no generated SVG found in out/".
* `validate.py:122-133` — `validate_target()` dispatches on directory / `.yaml` / `.svg`.
* `cli.py:45-57` — `_cmd_validate` prints and returns `0` (ok), `1` (problems), `2` (exception).

So today the checker knows *that* a scale bar exists. It knows nothing about **where** it is, and
nothing about whether the watermark it found says the same thing as `meta.status`.

### 2.2 Can a checker know where text landed? (the crux)

**No — not from the model as it exists.** The toolkit's helpers are *string builders*:

* `svg.py:207-212` — `svg_text(x, y, text, font_size=11, fill=…, anchor="middle", rotate=0)` returns
  a `<text …>` string. **`anchor` defaults to `"middle"`** — this default is the direct cause of
  defect (a) below.
* `svg.py:648-690` — `Drawing` holds `elements: list` of **strings** (`svg.py:663`) and `render()`
  concatenates them in a fixed order: defs, border, elements, watermark, title block
  (`svg.py:678-687`). There is no element registry, no geometry list, no bounding boxes.
* `isosheet.py:143-169` — `sheet()` builds a complete SVG document as a string and nests the inner
  drawing in a second `<svg x y width height viewBox preserveAspectRatio>` element
  (`isosheet.py:159`). `bfd.py` and `pid.py` both go through it, so those sheets never touch
  `Drawing` at all.
* Project sheets are worse (and are where all three defects lived): the Basin GA
  (`…/drawings/basin-site/source.py`) hand-assembles raw f-strings, and puts an entire detail inset
  into one `<g transform="translate(96,600)">` string (`source.py:241`).

Therefore **bounding boxes must be obtained by parsing the emitted SVG**, plus a *font-metrics
estimate* for text extents. The three options and why one wins:

| Option | Verdict |
|---|---|
| **Parse the emitted SVG** (`xml.etree.ElementTree`, stdlib) and estimate text boxes from font metrics | **Chosen.** Works on every sheet that exists today — `Drawing`-built, `isosheet`-built and hand-assembled — with zero refactor and zero output change. It checks the artifact the human actually reviews, so it also catches assembly bugs (a stray f-string, a `translate` that lands off-sheet) that a model-level checker cannot see. All three real defects were assembly bugs. |
| **Structured element registry recorded at draw time** | Rejected as the *primary* route. It requires changing the string-returning API (or wrapping every `Drawing.add`), it is blind to raw strings a project script writes, and `isosheet`/`pid`/`bfd` sheets bypass `Drawing` entirely — so it would cover the *least* defect-prone path and miss the most. Kept in a reduced, byte-neutral form as **declarations** (§3.3) for facts the SVG genuinely cannot carry. |
| **Rasterise and inspect pixels** | Rejected. Needs `cairosvg`/`rsvg-convert` (an optional dep today, `render.py`), is non-deterministic across renderer/font versions, cannot name *which* two items collided, and re-introduces exactly the "look at the picture" step we are removing. |
| **Real text shaping (`fontTools`/HarfBuzz)** | Rejected. Heavy dependency; findings would depend on which fonts a machine has installed, which breaks CI reproducibility. Unnecessary, per §2.3. |

### 2.3 Accuracy limits of text extents (stated precisely)

* **The toolkit's own CAD text is monospace** — `svg.py:211` hard-codes
  `font-family="monospace"`. Every monospace font in practical use has an advance width of
  0.600–0.6021 em (Liberation Mono 0.600, DejaVu Sans Mono 1233/2048 = 0.6021, Courier 0.600). Using
  **0.60 em per character** the *width* estimate is accurate to better than **±0.5 %** — this is
  arithmetic, not a guess.
* **`isosheet` text is proportional** — `isosheet.py:154` sets
  `font-family="Helvetica, Arial, sans-serif"` on the root, inherited by every `<text>`. Arial and
  Liberation Sans are metric-compatible with Helvetica, so a static AFM-derived advance table (widths
  per 1000 em) is **exact** for the fonts actually used, and the table ships with the module (no
  font files, no I/O).
* **Unknown families** fall back to 0.62 em/char (deliberately wider than Helvetica's ~0.55 em
  average for mixed-case Latin) and are marked lower-confidence, which raises the evidence bar before
  a finding is emitted (§4.2).
* **Non-Latin** (CJK, Hangul, full-width forms, detected by codepoint range) uses 1.0 em/char. This
  matters for ingested vendor sheets (vendor drawings carry Chinese text); without it every CJK
  string would be under-measured by ~40 % and every collision missed.
* **Height** is estimated per string from its own characters, not from a fixed ratio:
  ascent = 0.75 em if the string contains a capital, digit, tall lowercase (`bdfhklt`) or a bracket,
  else 0.53 em (x-height); descent = 0.21 em if the string contains a descender
  (`gjpqy,;()[]{}/@|_`), else 0.02 em. Calibration showed this matters: the generous fixed
  0.80/0.22 box produced one false positive on the shipped P&ID (an all-caps `DN250` label 2 px above
  an all-caps `plant` tag, neither of which has ink in the other's box) that the per-string ink box
  correctly does not report.
* **Known residual error:** kerning and `letter-spacing` are ignored (neither emitter sets them);
  `textLength`, `dx`/`dy` arrays, `tspan` positioning and `xml:space` are not supported (no emitter
  produces them — the parser **raises** on them rather than mis-measuring, §4.9).

### 2.4 What the emitted SVG *can* and *cannot* tell a checker

**Can** (verified against the two shipped example sheets):

* The title block, scale bars and the watermark are identifiable — `class="title-block"`
  (`svg.py:559`, `isosheet.py:67`), `class="scale-bar"` (`svg.py:462`),
  `class="status-watermark"` (`svg.py:622`).
* The watermark's **text content** is present, so it can be compared with
  `STATUS_WATERMARKS[meta.status_key]["text"]` (`style.py:51-64`) — a check that does not exist today.
* The title block's **field values** are present as text, so `REV.` can be compared with
  `meta.revision` (`svg.py:547-553`, `meta.py:82-91`).
* The sheet frame is detectable deterministically (§4.3), including `isosheet`'s double frame
  (`isosheet.py:156`).
* Group nesting and `transform` give scopes: the Basin inset is a `<g transform="translate(…)">`.

**Cannot**, today:

* **The north arrow has no marker.** `svg_north_arrow` (`svg.py:494-523`) emits a bare polygon, a
  line and a `"N"` text with no class. Its label is checkable (it is text); its glyph is
  indistinguishable from drawing geometry. Adding `class="north-arrow"` would change emitted bytes →
  forbidden here (§0.1). Follow-up issue F1 (§6).
* **Legend semantics.** `isosheet.legend()` (`isosheet.py:113-124`) and the Basin legend
  (`source.py:244-250`) emit swatch + label pairs with no key, and nothing in the SVG records which
  symbols the *drawing* used. Legend completeness therefore needs a data-side input (§3.3, §4.6).
* **`source_status: verify` flags.** They live in component specs and in the layout GeoJSON
  (`source.py:171`), never in the SVG. Needs a data-side input (§4.7).
* **A detail panel's declared model extent.** The SVG records the panel's pixel box but not the
  metres it claims to cover, so the *sharp* form of the inset-fill check needs a declaration
  (§4.5).
* **Revision history.** `revision` is a bare string (`meta.py:23`); there is no revision table.
  P9 (#61) owns it (§6).

### 2.5 Where the issue text needs correcting

The issue says the legend defect was "`svg_text` defaults to `anchor="middle"` and an explicit
`anchor="start"` had been dropped". That is exactly right about the mechanism, and `svg.py:207`
confirms the default. One correction of record: in the *committed* history the surviving
anchor-default defects are on the pond and platform labels (`source.py:118-126` at commit `58c5d2a`,
fixed to `anchor="start"` in `f358350`); the legend-over-swatch instance was fixed inside the same
working session, and is described in `f358350`'s commit message ("*fixed text anchors (svg_text
defaults to middle, so the legend/schedule ran right-aligned over their swatches)*") and in review
item 22. The fixture in §5.1 therefore reconstructs the legend from the committed geometry with the
anchor argument removed — mechanically the identical defect, and it reproduces to the pixel (§5.1).

Second correction: the issue's "two colliding scale bars" is, measured, a **main-view scale bar
overlapping the detail-inset panel** — the two bars' own ink boxes end up ~25 px apart, while the
bar/panel overlap is 2.2 px (§5.2). The check that catches it is therefore `panel-clear`, not a bar-to-bar
rule, and the spec names it that way so the implementer does not build a rule for a geometry that
does not exist.

### 2.6 Calibration results (run during specification; the implementer must reproduce them)

A throwaway parser implementing §4.1–§4.3 was run over both shipped sheets. This is evidence, not
guesswork, and it changes three design decisions:

| Sheet | `overprint` | `frame-containment` | Verdict |
|---|---|---|---|
| `drawings/example/simple-section/out/EXA-CIV-SEC-001.svg` | 0 | **2 (true positives)** | The `svg_leader` water-level label runs past the frame: `Design WL (nominal)` occupies x 803.1→917.1 against a frame right edge of x 896.0 — a **21.1 px overflow** (2.3 % of the sheet width), plus its second line `freeboard 0.25 m` at 899.1. Caused by the *input* (`simple-section/source.py:120-129`, `label_point` too close to the right edge), not by the toolkit. |
| `drawings/example/synthetic-pid/out/SYN-PSK-PID-001.svg` | 0 | 0 | but **2 true-positive `overprint` text/text findings**: title-block values overrun their cell dividers — `Water Supply (SYNTHETIC)` runs across into `AI-assisted / (Eng. —)` by **17.2 × 7.7 px**, and `SYN-PSK-PID-001` runs across into `A · 2026-07-16` by **5.5 × 6.2 px**. Cause: `isosheet.titleblock`'s `val()` (`isosheet.py:78`) writes values with **no truncation**, unlike `svg.svg_title_block`, which wraps and ellipsises to the cell width (`svg.py:566-576`). |

> ⚠️ **CORRECTED by P5-FINAL Correction 3.** An earlier draft of this table quoted **26.5 px** and
> **10.4 px** for these two overlaps. Those numbers are not what this spec's own stated Helvetica-AFM
> method (§2.3) produces; the AFM-derived measurements are **17.2 × 7.7 px** and **5.5 × 6.2 px**, and
> they are what `tests/legibility_baseline.yaml` pins. **The measured values win.** A spec that pins a
> number its stated method does not produce is asserting a result rather than deriving one — the same
> failure mode as a hand-typed scale string, which is what this programme exists to eliminate. Any
> future change to the metrics table must re-measure and update the baseline, never re-assert.

Consequences, all reflected below:

1. `<defs>`/`<pattern>` subtrees **must be skipped** — the concrete hatch pattern tile
   (`svg.py:242-254`) otherwise reports as geometry at (0,0) outside the frame.
2. Containment must **not** inflate boxes by stroke width, and needs a 0.5 px tolerance:
   `svg_title_block` places its block flush to the same margin the border uses
   (`svg.py:556-557` vs `svg.py:597`), so a hairline inflation would make **every** `Drawing` sheet
   fail containment on day one. This single detail is the difference between a checker that gets
   adopted and one that gets switched off in week two.
3. "Zero findings on the shipped examples" is **not** achievable by tuning, because two of the four
   findings are real defects in the shipped examples. Resolution in §4.10 and §5.4: fix the
   `simple-section` **input** in this PR (an input change, so no toolkit behaviour changes), and
   **baseline** the two P&ID findings against P9, which owns the ISO title block.

---

## 3. Design

### 3.1 Architecture

```
                       ┌──────────────── emitted sheet (out/*.svg) ── the artifact a human reviews
                       │
  parse (stdlib ET) ───┼──> SheetModel: frame + tuple[Item]  (role, box, scope, text, confidence)
                       │
  meta.yaml ───────────┤
  declarations ────────┘        (optional, byte-neutral: panels, legend keys, verify ids)
                       │
                       └──> check_sheet(model, config) ──> LegibilityReport(findings, skipped, checked)
                                                                  │
                                                 gate() ──> exit code 0 / 1 / 2
```

New module: **`src/technical_drawings_for_agents/legibility.py`** (single module, no package — it is ~600 lines and has
one job). Wired in at three seams: a new `check` CLI verb, an opt-in `validate --legibility` flag,
and a programmatic entry point for P2's build driver.

**Checks run on the emitted SVG, not on the model.** Rationale: it is the artifact under review; it
covers all three sheet-construction paths including hand-assembled ones; it cannot change output
because it only reads; and every defect we are chasing was created *between* the tested primitives,
which is precisely the region a model-level check cannot see. Where the SVG is genuinely
information-poor, declarations supply the missing fact — they are Python objects / a JSON sidecar in
`out/`, so **no byte of any sheet changes**.

### 3.2 Scopes and roles

Every ink-bearing element becomes one `Item` with a **role** and a **scope**.

*Scope* is the nearest enclosing "container" ancestor: the sheet root, a marked furniture group
(`class="title-block"`, `class="scale-bar"`, `class="status-watermark"`), a detected/declared panel
group, or a nested `<svg>` viewport. Scope does two things: it exempts an item from being compared
with its own siblings inside tested furniture (see §4.2), and it labels findings usefully
(`scope=inset:WTP COMPOUND — detail`).

Roles, in precedence order (first match wins):

| Role | How identified | In the readable set? |
|---|---|---|
| `background` | element box ≥ 98 % of the root viewBox area | no (excluded everywhere) |
| `frame` | frame candidates per §4.3 | no (exempt from containment) |
| `watermark` | inside `class="status-watermark"` | no (own check only) |
| `title-block` | inside `class="title-block"` | yes (as one group box) |
| `scale-bar` | inside `class="scale-bar"` | yes (as one group box) |
| `panel` | detected or declared detail panel (§4.5) | yes (as one group box) |
| `text` | `<text>` | yes |
| `opaque-fill` | shape with fill alpha ≥ `opaque_alpha`, not a `url(#…)` pattern, not `<image>`, box area ≥ `pointer_area_px2` | participates in `overprint` |
| `image` | `<image>` | no (raster backdrops are meant to be annotated over — P8 owns backdrops) |
| `geometry` | everything else with ink | containment only |

### 3.3 Declarations (the byte-neutral seam)

```yaml
# out/<number>.legibility.json  — written by the sheet's own generator, or
# legibility.yaml               — hand-written in the drawing dir (second best)
version: 1
panels:
  - name: "WTP COMPOUND — detail (≈1:250)"
    box_px: [96, 600, 456, 900]      # x0, y0, x1, y1 in root user units
    extent_m: [38, 25]               # what the region claims to cover
    kind: detail                     # detail (default) | callout — see §4.5a
legend:
  entries:
    - {key: clarifier, label: "FA-130 water purifier on 8.3×5.3 m raft", colour: "#d9463b"}
  used_keys: [clarifier, dosing-skid, big-pump]
verify:
  marker: "(v)"
  items: [{id: "STA-WTP-01", label: "FA-130 raft A"}, {id: null, count: 8}]
furniture:
  - {role: north-arrow, box_px: [149, 129, 195, 175]}
frame_px: [24, 24, 1576, 1006]       # optional override of frame detection
```

Preferred production route: the generator writes the sidecar **from the same variables it drew
with** (`Declarations.write_json(path)`), so there is no second copy of a number that can drift —
one fact, one home. A hand-written `legibility.yaml` is supported for sheets whose generator has not
been updated, and is explicitly labelled in the docs as duplication that can rot. Absent both, the
checks that need them are **skipped with a reason**, never silently passed.

### 3.4 Data model (all frozen dataclasses)

```python
# src/technical_drawings_for_agents/legibility.py

Severity = Literal["error", "warn"]          # same vocabulary as components/layout.py:37

class LegibilityError(ValueError):
    """Raised when a legibility config, declaration file or sheet cannot be read as specified."""

@dataclass(frozen=True)
class Box:
    x0: float; y0: float; x1: float; y1: float        # normalised so x0<=x1, y0<=y1
    @property
    def width(self) -> float: ...
    @property
    def height(self) -> float: ...
    @property
    def area(self) -> float: ...
    def overlap(self, other: "Box") -> tuple[float, float]      # (dx, dy); <=0 means separated
    def clearance(self, other: "Box") -> float                  # >0 gap; <0 = overlap depth
    def contains(self, other: "Box", tol: float = 0.0) -> bool
    def union(self, other: "Box") -> "Box"
    def inflate(self, by: float) -> "Box"
    @classmethod
    def from_xywh(cls, x, y, w, h) -> "Box": ...

@dataclass(frozen=True)
class Item:
    role: str                 # see §3.2
    box: Box
    scope: str                # "sheet" | "title-block" | "scale-bar#1" | "panel:<name>" | ...
    tag: str                  # svg element tag, e.g. "text", "rect"
    path: str                 # document path for messages, e.g. "svg/g[3]/text[2]"
    text: str = ""            # text content (role == "text" only)
    fill: str = ""            # literal fill attribute as emitted
    alpha: float = 0.0        # parsed fill alpha
    confidence: str = "exact" # "exact" (monospace) | "table" (Helvetica AFM) | "estimated"

@dataclass(frozen=True)
class Finding:                                   # shape mirrors components/layout.py:111-118
    severity: Severity
    check: str
    message: str
    location: Box | None = None
    scope: str = "sheet"
    def __str__(self) -> str:                    # "ERROR: overprint: <message>"
        return f"{self.severity.upper()}: {self.check}: {self.message}"

@dataclass(frozen=True)
class Skip:
    check: str
    reason: str                                  # must name what is missing and how to supply it

@dataclass(frozen=True)
class LegibilityReport:
    target: Path | None
    frame: Box | None
    findings: tuple[Finding, ...]                # canonically ordered, §4.11
    skipped: tuple[Skip, ...]
    checked: tuple[str, ...]                     # names of checks that actually ran
    items: tuple[Item, ...] = ()                 # kept for --json / debugging
    @property
    def errors(self) -> tuple[Finding, ...]: ...
    @property
    def warnings(self) -> tuple[Finding, ...]: ...
    @property
    def ok(self) -> bool:                        # no ERROR findings. NOT "approved to issue".
        return not self.errors
```

Config, with defaults and their justification in §4:

```python
@dataclass(frozen=True)
class LegibilityConfig:
    units: str = "px"                    # root user units; "mm" once P1 lands
    profile: str = "cad"                 # "cad" (Drawing sheets) | "iso" (isosheet/pid/bfd sheets)
    min_overlap_px: float = 1.5          # text/text and overprint evidence threshold
    min_clearance_px: float = 2.0        # required gap between furniture groups
    frame_tol_px: float = 0.5            # containment slack (hairline / flush title block)
    opaque_alpha: float = 0.85
    pointer_area_px2: float = 60.0       # arrowheads / leader dots are pointers, not overprints
    badge_tol_px: float = 1.0            # a shape may carry text it fully contains
    min_panel_area_frac: float = 0.03    # of frame area, for panel auto-detection
    min_panel_side_px: float = 100.0
    inset_extent_fill_min: float = 0.50  # content bbox area / declared extent area
    inset_content_fill_min: float = 0.45 # content bbox area / panel box area (no declaration)
    max_items: int = 5000                # guard: raise rather than degrade silently
    verify_marker: str = "(v)"
    severities: Mapping[str, Severity] = field(default_factory=dict)   # per-check override
    require: tuple[str, ...] = ()        # checks whose Skip becomes an error
    allow: tuple[Allowance, ...] = ()    # whitelisted deliberate overlaps
    baseline: tuple[BaselineEntry, ...] = ()

@dataclass(frozen=True)
class Allowance:
    a: str            # role, or "role:text substring"
    b: str
    reason: str       # REQUIRED and non-empty — LegibilityError otherwise

@dataclass(frozen=True)
class BaselineEntry:
    check: str
    message: str      # exact match
    reason: str       # REQUIRED
    issue: str        # REQUIRED, e.g. "#61"
```

`CAD_PX_DEFAULTS` and `ISO_PX_DEFAULTS` are the two shipped profiles (they differ only in expected
frame margin sanity-checking, §4.3). `load_config()` validates every key and raises
`LegibilityError` with the file name and key path on any problem — unknown key, non-numeric
threshold, negative clearance, `inset_*_fill_min` outside `(0, 1]`, unknown check name in
`severities`/`require`, severity not in `("error", "warn")`, an `allow`/`baseline` entry with an
empty `reason`. Mirrors `components/layout.py`'s loader style (`layout.py:121-190`).

### 3.5 Public API

```python
def load_config(path: str | Path) -> LegibilityConfig
def load_declarations(path: str | Path) -> Declarations
def sheet_model(svg: str | Path, *, config: LegibilityConfig = CAD_PX_DEFAULTS,
                declarations: Declarations | None = None) -> SheetModel
def check_sheet(model: SheetModel, *, meta: DrawingMeta | None = None,
                config: LegibilityConfig = CAD_PX_DEFAULTS) -> LegibilityReport
def check_svg(svg: str | Path, *, meta: DrawingMeta | None = None,
              declarations: Declarations | None = None,
              config: LegibilityConfig = CAD_PX_DEFAULTS) -> LegibilityReport
def check_drawing_dir(dir_path: str | Path, *,
                      config: LegibilityConfig | None = None) -> LegibilityReport
def gate(report: LegibilityReport, *, strict: bool = False) -> int      # 0 | 1
def text_box(text: str, x: float, y: float, font_size: float, *, family: str = "monospace",
             anchor: str = "start", baseline: str = "auto") -> tuple[Box, str]   # (box, confidence)
```

`check_drawing_dir` resolves, in order: `legibility.yaml` in the dir (config + declarations), then
`out/<number>.legibility.json` (declarations, which win over the YAML for the keys they carry),
`meta.yaml` via `DrawingMeta.load`, and the profile (`iso` when the dir holds a `*.pid.yaml` or a
BFD data YAML — reusing the discriminator `validate.py:99-110` already applies, so the two modules
agree by construction). It checks every `out/*.svg`, and merges per-sheet reports with the sheet name
prefixed into each message, exactly as `validate_drawing_dir` does today (`validate.py:116-118`).

`validate_target()` gains a keyword-only `legibility: bool = False`; when true, **error**-severity
legibility findings are appended to `ValidationResult.problems` (as `str(finding)`) and check names
to `checked`. Default `False` ⇒ every existing call site is bit-for-bit unchanged.

### 3.6 CLI

```
technical_drawings_for_agents check <target> [--config legibility.yaml] [--strict] [--require a,b]
                            [--format text|json] [--baseline file] [--show-items]
```

* `<target>`: a drawing directory, or a single `.svg`.
* `--strict`: warnings count as failures (for a pre-issue review pass); also promotes every `Skip`.
* `--require`: names checks whose skip is an error (for CI on sheets that *must* declare their data).
* `--format json`: the whole `LegibilityReport` as deterministic JSON (sorted keys, fixed float
  formatting) — this is what P2's build driver and CI annotations consume.
* Exit codes, matching `cli.py:45-57`: **0** no error findings; **1** error findings present (or
  warnings with `--strict`); **2** usage / unreadable target / invalid config / unsupported SVG
  construct. Text output prints one line per finding, `severity`, check, message and box, followed
  by a one-line summary and, always, the skipped list.

`validate` gains `--legibility` (opt-in, default off) and `--strict`. `render` is untouched.

For P2 (#54): the stable programmatic entry is `check_drawing_dir(dir) -> LegibilityReport`, and a
`checks: [legibility]` step in `drawing-set.yaml` maps to it. P5 does **not** add build wiring.

---

## 4. Behaviour decisions, with rationale

### 4.1 Parsing rules (must be exact, must be loud)

* Parse with `xml.etree.ElementTree` (stdlib; no new dependency). Strip the SVG namespace from tags.
* **Skip entirely**: `<defs>`, `<pattern>`, `<marker>`, `<clipPath>`, `<mask>`, `<symbol>`,
  `<linearGradient>`, `<radialGradient>`, `<style>`, `<metadata>`, `<title>`, `<desc>`. *Calibrated:*
  without this the concrete hatch tile reports as out-of-frame geometry at (0,0) on the shipped
  `simple-section` sheet.
* Inherit `font-family`, `font-size`, `text-anchor`, `dominant-baseline`, `fill` and `opacity` from
  ancestors (`isosheet.py:154` sets the family once on the root, so inheritance is not optional).
* Supported `transform` functions: `translate`, `rotate`, `scale`, `matrix`, composed left-to-right
  as 2×3 matrices. Supported nested `<svg>`: `x`, `y`, `width`, `height`, `viewBox` and
  `preserveAspectRatio` with `meet` and any `xMin|xMid|xMax` / `YMin|YMid|YMax` alignment — required,
  because `isosheet.py:159` uses `xMidYMin meet` and *every* BFD/P&ID item lives inside it.
* **Anything else raises `LegibilityError`** naming the element path and the offending
  attribute — `skewX`, `slice`, `textLength`, `tspan` with its own `x`/`y`, `dx`/`dy` lists, `<use>`,
  CSS `style="…"` geometry, percentage or unit-suffixed coordinates. Rationale: the toolkit emits a
  small, known set of forms. Anything outside it means the checker is out of date with the emitter,
  and silently dropping an element would create a false *negative* in a check whose whole purpose is
  to be trusted. Loud failure over a silent no-op.
* Shape boxes: `rect`, `circle`, `ellipse`, `line`, `polygon`, `polyline`, `image` exactly;
  `path` from the hull of its **coordinate/control points** (an over-estimate for curve segments,
  never an under-estimate — see §4.3 for how that is handled).
* Ink filter: an element with `fill="none"`/`fill:none` **and** no stroke, or `opacity="0"`, has no
  ink and is dropped.
* Stroke width inflates a box by `stroke-width/2` for **collision** purposes only, never for
  containment (§4.3).
* `> max_items` elements ⇒ `LegibilityError` (a 6000-element sheet means either a pathological input
  or an O(n²) blow-up; both deserve a message, not a 40-second hang).

### 4.2 `overprint` — text must be readable (catches defect (a))

Rule, evaluated for every `role == "text"` item T:

1. **text vs text** — T must not overlap another text item by more than `min_overlap_px` in *both*
   axes. Two labels overlapping is never deliberate. Severity **error**.
2. **text vs opaque fill** — for every `opaque-fill` item S: a finding is emitted iff
   `overlap(T, S) > min_overlap_px` in both axes **and not** `S.box.contains(T.box, badge_tol_px)`.
   Severity **error**.

Exemptions, each with its reason:

* **Same scope — scale-bar internals and title-block *fills* only.** ⚠️ **CORRECTED by P5-FINAL
  Correction 1.** An earlier draft of this section granted a blanket same-scope exemption to every
  marked furniture group. That contradicted §2.6/§4.10, which require the two P&ID title-block cell
  overflows to be **baselined** — an exempted overlap produces no finding to baseline, so the two
  rules could not both hold. The binding resolution:

  * `svg_scale_bar` places its tick labels 18 px below the bar top (`svg.py:486`) so the label ink
    overlaps the tick zone by design ⇒ **scale-bar internals are exempt** (text/text within one
    `scale-bar#n` scope is not compared).
  * `svg_title_block` paints an opaque background and writes its labels on top
    (`svg.py:560, 570`) ⇒ **title-block fills are exempt** (text-vs-opaque-fill inside the
    title block is not compared).
  * **Title-block text/text overlaps are NOT exempt.** They are real defects —
    `isosheet.titleblock`'s `val()` writes values with no truncation, unlike `svg.svg_title_block`,
    which wraps and ellipsises to the cell width (`svg.py:566-576`) — so they are detected, reported,
    and baselined against P9 rather than silently swallowed. Making them visible and owned is the
    whole point; an exemption here would have hidden the defect this calibration found.
* **The badge rule** (`S.contains(T)`) is how deliberate overlap is whitelisted *structurally*
  rather than by configuration: a numbered marker bubble (`source.py:197-198` — an opaque circle
  with its number centred inside) and an ISA instrument bubble with its tag inside both pass, while a
  legend label sitting across a 14×11 swatch does not, because the swatch cannot contain a 103 px
  label. Rejected alternatives: (i) *concentricity* — nearly equivalent but fails for legitimately
  off-centre badge text; (ii) *WCAG contrast* — the legend defect scores 3.27:1 against
  `#d9e2ec`/`#d9463b`, so it needs a 4.5:1 threshold to fire, and colour theory is the wrong
  argument for a geometry defect; a tag that *spills out of its bubble* is a real defect and the
  containment form catches it, contrast does not. Contrast belongs to a separate print-legibility
  issue (§6, F6).
* **Pointer marks** — opaque shapes with box area < `pointer_area_px2` (60 px²) are pointers, not
  backgrounds: dimension arrowheads are 5×5 = 25 px² (`svg.py:371-374`) and leader dots are r = 3 ⇒
  36 px² (`svg.py:421`). CAD convention lets an arrowhead touch its own label; a 25 px² triangle
  under one character does not make a label unreadable. Accepted false negative.
* **Patterns and images.** `fill="url(#hatch-concrete)"` is treated as transparent — the pattern's
  own background is `rgba(154,160,180,0.10)` (`svg.py:48`), so annotating over hatch is normal
  practice; evaluating pattern coverage is out of scope (accepted FN). `<image>` is exempt because
  labelling over a georeferenced ortho backdrop is what a site GA *is* (the Basin sheet has ~150 such
  labels). P8 (#60) owns backdrops.
* **Watermark** — by design it runs across everything (`svg.py:600-627`). Excluded from `overprint` and
  from containment; its own coherence check is §4.4.
* **Confidence gate** — where either text item's width `confidence == "estimated"` (unknown font
  family), the overlap must exceed `min_overlap_px * 2` before a finding is emitted. We demand more
  evidence where we are less sure of the width. Accepted trade-off: a 1.5–3 px kiss of
  unknown-family text goes unreported; at 3.81 px/mm that is under 0.8 mm and invisible on paper.

**False-positive/false-negative stance, stated once:** this checker is **precision-first**. Every
threshold defaults to the side that reports less. A checker that cries wolf gets disabled, at which
point its recall is zero; a checker that misses a 1 px kiss still catches the defects that matter.
The measured margins support this: the legend defect overlaps by **14.0 px**, the panel defect by
**2.7 px**, the frame overflow by **21.0 px** — all far above the thresholds, so precision costs us
nothing on the cases we care about.

`min_overlap_px = 1.5` because 1 px = 0.2625 mm on the current 1600 px-wide A3 sheets (1600 / 420 mm
= 3.81 px/mm), so 1.5 px ≈ 0.4 mm — about the smallest overlap a reviewer can see in print.

### 4.3 `frame-containment` and `title-block-clear`

**Frame detection** (deterministic, no configuration needed): candidates are **root-level** `<rect>`
elements with `fill="none"`, centred on the root viewBox within 1 px in both axes, and with area
≥ 50 % of it; the frame is the **innermost** (smallest-area) candidate. Verified: `simple-section`
yields exactly one candidate (`svg_border`, margin 24 ⇒ `[24,24 896,556]`); the shipped P&ID yields
two (`isosheet.py:156`, margins 10 and 34) and correctly selects the inner one
(`[34,34 1566,966]`) — which is the frame content must respect. If a declaration or `frame_px`
config is present it wins, and a mismatch with detection is reported as a `warn` (so a wrong
declaration is loud). If nothing is detected and nothing declared ⇒ **`LegibilityError`**: a sheet
with no findable frame cannot be containment-checked, and pretending otherwise is the silent no-op we
refuse. All frame candidates are themselves exempt from containment.

**Containment rule:** every ink item except `background`, `frame`, `watermark` and `image`
backdrops that lie within the canvas must satisfy `frame.contains(item.box, frame_tol_px)`.
Findings name the overflow in px per side. Severity **error** — except items whose box came from a
`path` with curve segments, which are **warn** and whose message states that the box is a
control-point hull (an over-estimate).

**No stroke inflation, and `frame_tol_px = 0.5`.** Non-negotiable, and calibrated:
`svg_title_block` computes `x0 = width - margin - block_width`, `y0 = height - margin - block_height`
(`svg.py:556-557`) with `margin=24` default, and `svg_border` draws at exactly that margin
(`svg.py:597`) — the block sits **flush** with the frame. Inflating by the 0.7 px hairline would put
every `Drawing` sheet 0.35 px outside its own frame and produce a guaranteed false positive on every
sheet the toolkit has ever made.

**`title-block-clear`:** no `text`, `scale-bar`, `panel` or `opaque-fill` item from *outside* the
title-block scope may overlap the title-block group box at all (tolerance `min_overlap_px`).
Severity **error**. The title block is the one region a reviewer reads first and it is where the
drawing states its own identity; anything over it is a defect regardless of contrast. The watermark
is exempt (it passes under the block by construction — `svg.py:683-686` draws the block last).

### 4.4 `panel-clear` — nothing may intrude on a detail panel (catches defect (b))

**Panel identification.** A *panel* is an opaque-filled rectangle that backs a group: box area
≥ `min_panel_area_frac` (3 %) of the frame area **and** both sides ≥ `min_panel_side_px` (100 px),
which is not the sheet background and not inside `class="title-block"`. Declared panels always win.
*Calibrated so auto-detection is safe:* on the shipped P&ID the largest non-frame opaque shape is a
70×185 vessel = 0.81 % of the canvas, **0.91 %** of the frame (and it fails the 100 px side test
twice over), while the Basin detail inset is 360×300 = **7.1 %** of the frame — a factor-of-8
separation, so no ISA symbol or equipment
box can be mistaken for a panel. Without both floors, every white-filled vessel would become a
"panel" and every line label crossing it a false positive; that would have been a fatal design error.

**Rule:** no ink item whose scope is *outside* a panel may overlap that panel's box, and furniture
groups (`scale-bar`, `north-arrow`, `title-block`, other panels) must additionally keep
`min_clearance_px` from it. Severity **error**.

This is the rule that catches defect (b), and the measured numbers are the fixture's assertions
(§5.2): before the fix, the main-view scale bar occupies x 113.2→276.5 with ink from y 897.8 (its
`label` baseline at y0−5 = 905.3 minus a 0.75 em ascent) down to y 936.5, while the inset panel
occupies x 96→476, y 560→900 — a **2.2 px overlap in y** (2.8 px once the panel's 1.2 px stroke is
counted) across 163 px of x, i.e. clearance ≈ **−2.2 px** against a required +2.0. After the fix (bar
moved to `FXMAX-72`, panel to `96,600,360,300`) the bar sits at x 851.5→1014.8 and there is no x
overlap at all: **clean**.

`min_clearance_px = 2.0` (≈0.5 mm on A3 at the current canvas size) because 0.5 mm is roughly the
smallest gap that reads as a gap on a printed sheet; below it two pieces of furniture look like one.

**Rejected:** a "one scale bar per sheet" or bar-to-bar proximity rule. The two bars end up 21.6 px
apart, which is a legitimate distance for two unrelated items; a rule tuned to fire at 21 px would
fire constantly elsewhere. The real defect is that main-view furniture had drifted into the panel's
footprint, and `panel-clear` states exactly that.

### 4.5 `inset-fill` — a detail panel must be filled by its detail (catches defect (c))

Content = union of the boxes of the panel's descendants excluding the panel rect itself, the panel's
own title text, and its scale bar (those are furniture, not detail). Two measures:

* **Declared** (preferred, sharp): `ratio = content_extent_m_area / declared_extent_m_area`, using
  the panel's declared `extent_m`. For the real defect: content ≈ 38 × 25 m = 950 m² against a
  declared extent of 72 × 52 m = 3744 m² ⇒ **0.254**, i.e. the panel claimed 3.94× its content —
  the issue's "4x". Threshold `inset_extent_fill_min = 0.50`.

  ⚠️ **CORRECTED by P5-FINAL Correction 2 — this formula was under-specified.** The declaration
  schema (§3.3) carries a box in pixels and an extent in metres but **no panel scale**, so
  `content_extent_m_area` cannot be computed from the declaration alone. The binding inference:

  ```
  content_extent_m = content_bbox / panel_box * declared_extent_m
  ```

  Note the consequence, which removes the ambiguity entirely rather than papering over it: under that
  inference `content_extent_area / declared_extent_area` reduces **algebraically** to the pixel-area
  ratio `content_bbox.area / panel_box.area`. The declared and undeclared forms of this rule are
  therefore one number computed one way — they cannot disagree, and a second implementer deriving
  either expression lands on the same result. `extent_m` remains required for the declared form
  because it is what lets the finding state the miss in metres ("content 38.0 × 25.0 m inside declared
  72.0 × 52.0 m") rather than a bare fraction, and because the two forms keep different thresholds
  (0.50 vs 0.45; see below).
* **Undeclared** (fallback, from the SVG alone): `ratio = content_box_area / panel_box_area`.
  Threshold `inset_content_fill_min = 0.45`, lower because the inner `ViewBox` padding is
  legitimately unusable: at padding = 26 on a 360×300 panel the best achievable ratio is
  (308×248)/108000 = **0.71**, and the fixed Basin inset measures **0.58**, so 0.45 leaves ~29 %
  headroom for a well-composed panel while still catching 0.15 (the pre-fix panel).

Severity **warn** by default, `error` when the ratio is 0 or there is no content at all (an empty
panel is not a judgement call, it is a broken sheet). Rationale for warn: the threshold encodes
composition taste, and taste must not fail a build on day one — a project that wants it hard sets
`severities: {inset-fill: error}`. The acceptance test asserts the finding *exists*, which is what
"caught" means; §5.3 also asserts the promoted-to-error path.

### 4.5a Declared regions: `kind`, and `inset-fill` on a callout (P5-FINAL §4.1)

`inset-fill` applies to **any declared region**, not only detail panels. The live case (#57) was an
in-view callout box spanning **87 × 48 m** around a cluster occupying about **38 × 25 m** ⇒ ratio
950 / 4176 = **0.227**, well under the 0.50 threshold.

A callout is not a detail panel, and one behaviour must differ: a callout is drawn *over* the main view
to frame a cluster, so main-view geometry legitimately **runs across its boundary**. Applying `panel-clear`
to it would report a finding for every feature that enters or leaves the callout — a false-positive
storm on exactly the sheet type that motivated the rule. `kind` therefore selects between them:

| `kind` | What it is | `panel-clear` | `inset-fill` |
|---|---|---|---|
| `detail` (default) | An opaque inset replacing the main view inside its box | applies | applies |
| `callout` | A box over the main view framing a cluster | **does not apply** | applies |

`detail` is the default, so every existing declaration keeps its current meaning.

**Region furniture.** Content excludes the region's own boundary: for a `detail` panel that is the
coincident opaque backing plate; for a `callout` it is any coincident rect, because the callout's
stroked outline and a content rect that exactly fills the callout are *the same bytes* and cannot be
told apart from the SVG. Known false negative, accepted under precision-first: a callout whose content
exactly fills it is read as having no distinguishable content and reports nothing — which is the
ratio-1.0 pass anyway. An empty **detail** panel is unambiguous and remains an `error`.

### 4.5b `marker-occlusion` — numbered markers drawn on top of each other (P5-FINAL §4.2)

Observed live (#57): numbered schedule markers at co-located placements — three at one location, two at
another. **N markers within R px is a finding.**

**Why `overprint` cannot cover this.** §4.2's badge rule deliberately exempts a number sitting inside
its bubble, which is what makes a legitimate marker pass. That exemption is precisely why `overprint`
is blind to a *second* bubble stacked on the first: the intruding bubble contains the number, so the
pair is whitelisted. A separate rule is structurally necessary, not a convenience.

**A marker is a badge**, identified structurally and keyed on the number it carries: an opaque shape of
area ≥ `pointer_area_px2` that encloses a marker number (`\d{1,3}[A-Za-z.]?`) and encloses **nothing
but** marker numbers. Where several shapes enclose one number, the nearest-centred smallest is the
badge, so one symbol counts once.

**R is not a new threshold.** Two markers occlude when their ink boxes overlap by more than
`min_overlap_px` in both axes, so R is set by the markers' own geometry plus the single print-physics
number this spec already derives (1.5 px = 0.4 mm at the A3 canvas, §4.2). Inventing an absolute radius
would have violated §7's "never invent a threshold". `marker_cluster_min` defaults to **2** and is
rejected below 2 at config load: one marker cannot occlude itself, so this is arithmetic, not taste.
Clustering is the transitive closure of pairwise overlap, so three mutually stacked markers are one
finding naming three markers, not three pairwise findings. Severity **error**.

**Two false positives were measured on the shipped P&ID and fixed structurally, not by tuning:**

1. `isa.py` draws an instrument tag on **two lines** — `FIC` over `101` — so the loop number looked
   like a schedule marker. Resolved by requiring that a badge enclose *nothing but* numbers. Note this
   is **not** "exactly one enclosed run": that rule also excludes stacked markers, which enclose each
   other's numbers, and so would have disabled the check for the very defect it exists to find.
2. An ISA shared-display symbol is a **circle inscribed in a square**, and both shapes enclose the same
   number — counted as two stacked markers. Resolved by keying a marker on its number and picking one
   enclosing badge.

After both, the shipped P&ID yields **zero** marker badges and zero findings.

### 4.6 `status-coherence` — the sheet's watermark must match `meta.status`

* No `class="status-watermark"` ⇒ **error** (parity with today's `validate`).
* Watermark text ≠ `STATUS_WATERMARKS[meta.status_key]["text"]` (`style.py:51-64`) ⇒ **error**,
  message quoting both strings.
* Watermark text matches a *different* status key's canonical text ⇒ **error**, message naming both
  statuses.
* Watermark text contains `"ISSUED FOR CONSTRUCTION"` while `meta.status_key != "ISSUED"`, **or**
  `meta.for_construction` is true while the watermark is not the ISSUED text ⇒ **error** under check
  name `status-coherence`, sub-rule `issued-gate`. **This severity cannot be overridden**: a
  `severities` entry attempting to downgrade `issued-gate` raises `LegibilityError` at config load.
  The gate is the responsible engineer's signature; a config file may not soften it.
* Custom watermark text (`svg_status_watermark(text=…)`, `svg.py:600`) that matches no status ⇒
  **warn**, because the override is a supported feature but an unverifiable claim.
* No `meta.yaml` / no `DrawingMeta` passed ⇒ `Skip("status-coherence", "no meta.yaml: cannot compare
  the watermark with meta.status; pass meta= or check a drawing directory")`.

Note for the record: this check reads status and compares it. It never writes it, and there is no API
in this module that can.

### 4.7 `revision-coherence`

* Layer 1 (works today): the `class="title-block"` group's `REV.` field text must equal
  `meta.revision`, and the `DRAWING NO.` field must contain `meta.number`
  (`svg.py:547-553`, `meta.py:82-91`). Mismatch ⇒ **error**. Field extraction is positional within
  the title-block group's text runs; if the field labels cannot be located (a different title-block
  implementation, e.g. `isosheet.titleblock`), ⇒ `Skip` with a reason naming the layout it found.
* Layer 2 (activates when P9 (#61) lands): the sheet must carry a revision-table row for
  `meta.revision` with a non-empty date and description. Until P9 provides a marked revision block
  and a `revisions:` model, ⇒ `Skip("revision-coherence:table", "no revisions: in meta.yaml and no
  revision table on the sheet — P9 (#61)")`.

### 4.8 `legend-complete` (both directions) and `verify-annotated`

**`legend-complete`**, from declarations:

* a used key with no entry ⇒ **error** (a symbol on the sheet the reader cannot decode);
* an entry whose key is not used ⇒ **warn** (misleading, but nothing is unreadable — a reviewer hunts
  for a symbol that is not there);
* a declared entry whose `label` does not appear as a text run on the sheet ⇒ **error** (the legend
  was declared but not drawn, or drawn truncated — a real class of bug: the Basin schedule truncates
  every row to 56 characters, `source.py:257`);
* no legend declaration ⇒ `Skip("legend-complete", "no legend declared: supply legend.entries and
  legend.used_keys via out/<n>.legibility.json or legibility.yaml")`.

Colour-set inference (compare solid stroke colours used in the drawing scope with swatch fills in
the legend region) is **not** in v1: swatch fills and feature strokes are only accidentally the same
value (the Basin sheet washes fills to `rgba(…,0.30)` and keeps the solid colour on the stroke,
`source.py:175, 184-186`), and dimension grey, hatch colours and the backdrop border all pollute the
used-colour set. It would be a warn-only heuristic pretending to be a check. Follow-up F3 (§6).

**`verify-annotated`**, from declarations (`verify.items`, `verify.marker`):

* every verify item **with an id**: some text run on the sheet must contain that id *and* the marker
  ⇒ else **error** naming the id;
* **untagged** verify items (a `count`): the number of distinct text runs carrying the marker must be
  ≥ the count ⇒ else **error**; if equal or greater, additionally emit a **warn** stating that N
  verify items are untagged and therefore cannot be matched individually. This is the honest
  treatment of a real situation — 8 of 10 Basin placements carry no tag (`source.py:260-266`) — and
  it reports the limitation instead of pretending the check is stronger than it is;
* the marker must be *defined* somewhere in the sheet text (a notes line containing the marker and at
  least three more words) ⇒ else **warn** (a `(v)` nobody can decode);
* no verify declaration ⇒ `Skip` naming what to supply.

### 4.9 What "caught" means, and the severity table

| Check | Sub-rule | Default severity |
|---|---|---|
| `overprint` | text over text | error |
| `overprint` | text over non-badge opaque fill | error |
| `frame-containment` | ink outside the frame | error (warn for curve-path hulls) |
| `title-block-clear` | ink over the title block | error |
| `panel-clear` | intrusion into a detail panel | error |
| `inset-fill` | ratio below threshold (any declared region) | **warn** |
| `inset-fill` | detail panel with no content | error |
| `marker-occlusion` | ≥ `marker_cluster_min` numbered markers at one placement | error |
| `status-coherence` | watermark missing / text ≠ status | error |
| `status-coherence` | `issued-gate` | error, **not overridable** |
| `status-coherence` | unrecognised custom watermark text | warn |
| `revision-coherence` | title-block rev ≠ `meta.revision` | error |
| `legend-complete` | used key with no entry / entry label not drawn | error |
| `legend-complete` | entry never used | warn |
| `verify-annotated` | verify item not annotated | error |
| `verify-annotated` | marker undefined / untagged items unmatchable | warn |
| any | `Skip` | warn (error if named in `require`) |

Principle: **error = something cannot be read, or the sheet contradicts its own data. warn =
judgement, composition, or a limitation of the check.** Errors fail the build; warnings are for the
pre-issue review pass (`--strict`).

### 4.10 Allowances and baseline (the two escape hatches, both loud)

* `allow:` whitelists a *pair* by role/text selector and **requires a non-empty `reason`**
  (`LegibilityError` otherwise). Every allowance that matched nothing in a run is reported as a
  `warn` ("stale allowance") so the file cannot rot into a blanket suppression.
* `baseline:` records *known, accepted* findings by exact `(check, message)` with a mandatory
  `reason` and `issue`. Baselined findings are removed from `findings` and listed separately in the
  report and in the CLI output. A baseline entry that no longer matches is a **warn** ("stale
  baseline entry — remove it"). This is how a checker is introduced to an existing corpus without
  either lying about it or blocking everything.
* Rejected: any form of in-SVG suppression comment or a global `--ignore` flag. Both make suppression
  invisible in review.

Concretely (see §2.6): the two `isosheet` title-block cell overflows on
`drawings/example/synthetic-pid` go into `tests/legibility_baseline.yaml` with
`reason: "isosheet.titleblock writes values with no truncation, unlike svg.svg_title_block; the ISO
title block is P9's scope"` and `issue: "#61"`. The `simple-section` frame overflow is **fixed in
this PR by changing the example's input** (`drawings/example/simple-section/source.py`: bring the
water-level leader's `label_point` inside the frame, or set `anchor="end"`), and its committed
`out/` artifacts are regenerated. That is an *input* change: the toolkit's output for any unchanged
input is untouched, so the sacred rule holds. Do **not** "fix" it by widening the frame tolerance.

### 4.11 Determinism

* Findings sort by `(severity_rank, check, round(y0, 3), round(x0, 3), message)` — stable regardless
  of document order, so shuffling element order cannot change the report.
* Floats in messages and JSON are formatted with a fixed precision (`%.1f` for px, `%.3f` for
  ratios). No wall-clock time, no paths outside the target, no dict iteration order.
* No font loading, no rasteriser, no network, no locale-dependent formatting.

### 4.12 Units, and the P1 handoff

All boxes and thresholds are in **root user units** (today: px, since `svg_wrap` writes
`viewBox="0 0 W H"` with matching `width`/`height`, `svg.py:637-639`). `config.units` must equal the
detected unit system or `load_config` raises — thresholds are never silently rescaled. When P1 (#53)
introduces mm paper space, ship `PAPER_MM_DEFAULTS` with the same thresholds expressed in mm
(0.4 mm overlap, 0.5 mm clearance — the px defaults were derived from exactly these physical
numbers at 3.81 px/mm) and take the frame from `Sheet` instead of detecting it.

**What P5 needs from P1 (#53):** `Sheet.frame` as a `Box` in the emitted document's user units,
`Sheet.units`, and `Viewport.extent_m` per viewport (which makes `inset-fill`'s declared form
automatic and retires the sidecar for panels). **From P9 (#61):** a marked revision block whose rows
expose rev/date/description as text, and a title block whose field values are addressable by label.
P5 builds none of these; it consumes them and skips loudly until they exist.

---

## 5. Acceptance tests

All in `tests/test_legibility.py`, with sheet builders in `tests/legibility_fixtures.py` (mirroring
`tests/synthetic.py`'s role). **Fixtures must build their sheets through the real toolkit helpers**
(`Drawing`, `svg_text`, `svg_rect`, `svg_scale_bar`, `ViewBox`) so a fixture cannot drift from the
emitter it is meant to police. Every expected value below was computed from the real geometry and
must be asserted, not approximated away.

### 5.1 `test_legend_labels_over_swatches_are_caught` — real defect (a)

*Setup.* `fixture_legend_over_swatches(anchor)`: a 1600×1030 `Drawing` (`status="CONCEPT"`, title
block from a `DrawingMeta`), plus the pre-fix Basin legend at `lx0 = W-300 = 1300`, `ly0 = 92`,
seven entries, each `svg_rect(lx0, y, 14, 11, fill=<colour>)` and
`svg_text(lx0+22, y+9, label, font_size=9, anchor=anchor)` with `y = ly0 + 18*i`.

*Action.* `check_svg(sheet, meta=meta)` with `anchor` omitted (i.e. `svg_text`'s `"middle"` default).

*Expected.* Exactly **7** `overprint` findings, severity `error`, one per row. For row 1
(`"WTP treatment units"`, 19 chars at font 9 ⇒ width 102.6): the label box is
x 1270.7→1373.3, y 94.25→101.18 (baseline 101, ascent 0.75 em, no descenders), the swatch box is
x 1300→1314, y 92→103, so the x overlap is **14.0 px** (the swatch lies wholly inside the label box)
and the y overlap is **6.9 px**; assert both to `abs=0.2`. Each message must
name the label text and the swatch fill. Re-running the identical fixture with `anchor="start"`
(label box 1322→1424.6) ⇒ **`report.findings == ()`**. This is the whole point of the check: the
defect and its fix are one argument apart, and the checker sees the difference.

### 5.2 `test_scale_bar_intruding_on_detail_inset_is_caught` — real defect (b)

*Setup.* `fixture_basin_furniture(bar_at, panel)` with
`ViewBox(788392, 788702, 322125, 322395, 1600, 1030, padding=74)` (scale 3.2667 px/m): a detail-inset
panel drawn as `<g transform="translate(96,560)">` whose first child is
`svg_rect(0, 0, 380, 340, fill="#0f1626")` containing a `svg_scale_bar` of its own, plus a main-view
`svg_scale_bar(vb, FXMIN+12, FYMIN+14, 50, divisions=5)`.

*Action.* `check_svg(...)`.

*Expected.* One `panel-clear` **error** naming the `scale-bar` and the panel, with
`-3.0 <= clearance <= -2.0` px: the bar's ink box is x 113.2→276.5, y 897.8→936.5 and the panel is
x 96→476, y 560→900 (900.6 with its stroke). Assert the panel was auto-detected (`panel` role
present, area 380×340 = 129 200 px² = 8.5 % of the frame area 1552×982) with **no declaration
supplied**. Assert that the two scale bars alone produce **no** finding (their own ink boxes are
~25 px apart — documents §4.4's rejected rule). Then the post-fix
geometry — bar at `FXMAX-72` (x 851.5→1014.8), panel `translate(96,600)` at 360×300 — gives
**`report.findings == ()`**.

### 5.3 `test_detail_inset_extent_four_times_content_is_flagged` — real defect (c)

*Setup.* `fixture_inset(extent_m, content_m)`: a declared panel `box_px=[96,600,456,900]` with
`extent_m=[72,52]` and content occupying 38 × 25 m of it (the pre-fix Basin numbers).

*Action.* `check_svg(..., declarations=…)`.

*Expected.* One `inset-fill` finding, severity **warn**, ratio **0.254** (assert `abs=0.005`),
message quoting `0.254 < 0.50` and both extents. With
`config = replace(cfg, severities={"inset-fill": "error"})` the same fixture yields severity `error`
and `gate(report) == 1`. With `extent_m=[38,25]` (the fix) the ratio is 1.0 and
`report.findings == ()`. A third case with no declaration falls back to the content/panel measure and
asserts ratios 0.15 (pre-fix) and 0.58 (post-fix) against the 0.45 threshold.

### 5.3a `test_oversized_callout_region_is_flagged` — live defect (d), P5-FINAL §4.1

*Setup.* `fixture_callout_region(extent_m, content_m)`: a declared region `kind: callout`,
`box_px=[96,180,456,480]`, `extent_m=[87,48]`, with a cluster occupying 38 × 25 m of it, plus a
main-view line that **runs across the callout boundary**.

*Expected.* Exactly **one** `inset-fill` finding, severity `warn`, ratio **0.227**, message quoting
`0.227 < 0.50`, `38.0 x 25.0 m`, `87.0 x 48.0 m` and the word `callout`. **Zero** `panel-clear`
findings — the crossing line is the regression test for applying detail-panel intrusion rules to a
callout (§4.5a). Tightening the callout to `extent_m=[42,28]` around the same cluster (ratio 0.808) ⇒
`report.findings == ()`. Note the fix is to **shrink the region**, not to move the content.

### 5.3b `test_co_located_markers_are_caught` and
### `test_an_ISA_instrument_bubble_is_not_a_numbered_marker` — live defect (e), P5-FINAL §4.2

*Setup.* `fixture_marker_occlusion()`: six numbered markers (opaque circle r = 11 with its number
centred), three stacked at one placement, two at another, and **one alone as the control**.

*Expected.* Exactly **2** `marker-occlusion` errors — one naming 3 markers (`'1', '2', '3'`), one
naming 2 (`'4', '5'`) — and the lone marker `'6'` in neither. Separating all six ⇒
`report.findings == ()`.

The precision guard is a separate test and is **not optional**: on
`drawings/example/synthetic-pid`, `_marker_badges(...) == []` and `marker-occlusion` yields no finding,
because an ISA two-line tag and a circle-in-square symbol must not read as stacked markers (§4.5b).
Both false positives were measured, not hypothesised.

### 5.3c `test_fix_induced_collision_is_caught` — live defect (f), P5-FINAL §4.3

*Setup.* `fixture_fix_induced_collision(round_)`, three rounds of by-eye tuning against a fixed label
`X pump house`: round 0 has label `A` on `X`; round 1 moves `A` **and** `B` clear of `X` and lands them
on each other; round 2 clears every pair.

*Expected.* Round 0 ⇒ one `overprint` naming `X`/`A`. Round 1 ⇒ one `overprint` naming `A`/`B`, with
`X` no longer named. Round 2 ⇒ `report.findings == ()`.

The assertion that matters is that the round-1 finding is **disjoint** from round 0's, not merely that a
finding exists: the *count* is 1 before and 1 after, so a human counting defects sees no change and
concludes the fix worked. Only the identity of the colliding pair reveals the regression. This is the
single strongest argument for the checker existing — a human moving labels cannot see what they break.

### 5.4 `test_shipped_examples_produce_only_baselined_findings` — the false-positive gate

*Setup.* Parametrised over every `drawings/example/*/out/*.svg` with the profile
`check_drawing_dir` would choose, and the repo baseline `tests/legibility_baseline.yaml`.

*Expected.* `report.findings == ()` after baselining, **and** the baseline contains exactly the two
`isosheet` title-block overflows recorded in §4.10 (so nothing else can be smuggled in), **and** no
baseline entry is stale. `drawings/example/simple-section` must produce zero findings **and no
baseline entries** — its frame overflow is fixed in this PR by changing its input (§4.10). If an
implementer finds a *new* finding on a shipped example, the only permitted responses are: fix the
example's input, fix the rule if it is genuinely wrong, or add a baseline entry with a reason and an
issue number. Loosening a threshold to make this test pass is a spec violation — the thresholds are
derived from physical print legibility (§4.2, §4.4), not from what makes the suite green.

### 5.5 `test_out_of_frame_text_is_caught`

Fixture: a `Drawing` with `svg_text(910, 300, "overflow", font_size=10, anchor="start")` on a
920×580 canvas with `border_margin=24` (frame right edge 896). The box is x 910→958 (8 chars ×
0.6 em × 10), so expect one `frame-containment` **error** whose message states a **62.0 px**
overflow to the right of the frame. Same text at x = 700 ⇒
no findings. Additionally: a title block placed flush at the default margin
(`svg_title_block(920, 580, …)`, `svg.py:556-557`) produces **no** finding — the regression test for
the stroke-inflation trap in §4.3.

### 5.6 `test_text_over_title_block_is_caught`

Fixture: a schedule column whose last row is drawn at y inside the title-block box (x0 = W−24−280,
y0 = H−24−126). Expect one `title-block-clear` **error** naming the row's text. Moving the row 40 px
up ⇒ clean. Assert the watermark, which runs across the block by design, produces no finding.

### 5.7 `test_watermark_must_match_meta_status`

(a) `meta.status = "CONCEPT"` + `svg_status_watermark(..., "CONCEPT")` ⇒ no findings.
(b) `meta.status = "CONCEPT"` + `svg_status_watermark(..., "ISSUED")` ⇒ one `status-coherence`
**error** whose message names both statuses and mentions the ISSUED gate.
(c) `meta.for_construction = True, status = "ISSUED"` with a DRAFT watermark ⇒ `issued-gate` error.
(d) `load_config` on a config containing `severities: {issued-gate: warn}` raises `LegibilityError`
mentioning that the ISSUED gate cannot be downgraded.
(e) A sheet with no watermark ⇒ error (parity with `validate.py:52-55`).
(f) No meta ⇒ `Skip("status-coherence", …)` and `report.findings == ()`.

### 5.8 `test_revision_and_number_coherence`

Title block rendered from `DrawingMeta(number="STA-SITE-GA-001", revision="B")` against
`meta.revision = "C"` ⇒ one `revision-coherence` **error** quoting `"B"` and `"C"`. Matching
revisions ⇒ clean. With no `revisions:` in `meta.yaml`, `report.skipped` contains
`revision-coherence:table` naming #61.

### 5.9 `test_legend_completeness_both_ways`

Declarations: entries `{a, b, c}`, used keys `{a, b, d}`. Expect exactly one **error** for `d` (used,
no entry) and one **warn** for `c` (entry, never used). A fourth case where entry `b`'s label does
not appear as a text run on the sheet ⇒ **error**. No declaration ⇒ `Skip`, and with
`require=("legend-complete",)` ⇒ **error** and `gate() == 1`.

### 5.10 `test_verify_items_must_be_annotated`

(a) One tagged verify item `STA-WTP-01`, sheet text lacks `(v)` ⇒ **error** naming the id.
(b) Same with `"1.  STA-WTP-01  FA-130 raft  (v)"` present and a notes line defining `(v)` ⇒ clean.
(c) `items: [{id: null, count: 8}]` with 5 marker-bearing runs ⇒ **error** (5 < 8); with 8 ⇒ a
**warn** stating that 8 verify items are untagged and cannot be matched individually.
(d) Marker present but never defined in the notes ⇒ **warn**.

### 5.11 `test_severity_gate_and_exit_codes`

`gate()` and `cli.main(["check", …])`: clean ⇒ 0; any error finding ⇒ 1; warnings only ⇒ 0;
warnings only with `--strict` ⇒ 1; malformed `legibility.yaml` ⇒ 2 with the file name and key in
stderr; missing target ⇒ 2. `validate` without `--legibility` on a sheet with a known collision
still ⇒ 0 (proves the opt-in), and with `--legibility` ⇒ 1.

### 5.12 `test_skipped_checks_are_reported_never_silent`

A sheet with no declarations: `report.skipped` names `inset-fill`, `legend-complete` and
`verify-annotated`, each with a reason that names the file to supply; `report.checked` names the
checks that did run; the text CLI output prints the skipped list even on success.

### 5.13 `test_transforms_and_nested_svg_are_mapped`

(a) `<g transform="translate(96,600)">` containing `svg_text(12, 20, "T", …)` ⇒ box origin
(108, 620) ± 0.1. (b) An `isosheet`-shaped nested `<svg x="44" y="96" width="1412" height="554"
viewBox="0 0 1000 400" preserveAspectRatio="xMidYMin meet">` with a text at inner (0, 20):
scale = min(1412/1000, 554/400) = 1.385, x-offset = 44 + (1412 − 1385)/2 = 57.5, y-offset = 96
(`YMin`) ⇒ assert the mapped box. (c) `svg_dimension_v`'s `rotate(-90, x, y)` label
(`svg.py:398`) ⇒ width and height swap. (d) `dominant-baseline="middle"` (`svg.py:624`,
`isa.py` bubble tags) centres the box on `y`.

### 5.14 `test_unsupported_svg_constructs_raise`

`transform="skewX(10)"`, `preserveAspectRatio="… slice"`, `x="10mm"`, a `<use>` element and a
`<tspan x="…">` each raise `LegibilityError` naming the element path and the attribute. No silent
skip. (This is the test that keeps the checker honest as the emitters evolve.)

### 5.15 `test_text_metrics`

`text_box("MMMMMMMMMM", 0, 0, 10, family="monospace")` ⇒ width exactly 60.0, confidence `"exact"`.
A Helvetica string ⇒ confidence `"table"` and a width within 2 % of its AFM sum. A CJK string ⇒
1.0 em/char. `"acorn"` (no ascender, no descender) ⇒ height 0.55 em; `"Apply"` ⇒ 0.96 em. The three
anchors put the same string at x0 = x, x − w/2 and x − w respectively (the direct root-cause test for
defect (a)).

### 5.16 `test_findings_are_deterministic`

Shuffle the element order of a fixture with four findings ⇒ identical `tuple(map(str, findings))`,
and byte-identical `--format json` output across two runs in different processes
(`PYTHONHASHSEED` varied).

### 5.17 `test_allowance_requires_reason_and_stale_allowance_warns`

An `allow` entry without `reason` ⇒ `LegibilityError` at load. A valid allowance suppresses exactly
its pair. An allowance matching nothing ⇒ **warn** "stale allowance". Same three assertions for
`baseline` entries (missing `reason`/`issue` ⇒ error; stale ⇒ warn).

### 5.18 `test_no_emitter_output_changed`

Regenerate `drawings/example/synthetic-pid` and (post-fix) `drawings/example/simple-section` and
assert the SVG bytes equal the committed `out/*.svg`. Then assert that importing and running
`legibility` over a sheet does not modify any file: snapshot mtimes and hashes of the drawing
directory before and after `check_drawing_dir`. Guards §0.1 mechanically.

### 5.19 `test_module_cannot_touch_the_issued_gate`

`legibility.py` contains no write path: assert no `open(..., "w")`/`write_text`/`safe_dump` in
`inspect.getsource(legibility)` except inside `Declarations.write_json`, and that the module never
references `for_construction` as an assignment target. `LegibilityReport` exposes `ok` only, with a
docstring stating that a clean report is not approval to issue.

### 5.20 `test_max_items_guard_raises`

A synthetic sheet with `max_items + 1` elements ⇒ `LegibilityError` naming the count and the config
key. No silent truncation, no 40-second hang.

---

## 6. Out of scope (do NOT do these here)

An implementer must not:

1. **Change any emitter.** No new attributes, classes, `data-*` hooks, reordering or reformatting in
   `svg.py`, `isosheet.py`, `bfd.py`, `pid.py`, `isa.py`. Not even a `class="north-arrow"`, however
   tempting — it would change every existing sheet's bytes.
2. **Add a rasteriser, a font library or any new dependency.** Stdlib + the existing PyYAML only.
3. **Auto-fix anything.** No moving labels, no re-anchoring text, no resizing insets. A checker that
   also rearranges the sheet becomes an inference engine, and the whole point of P5 is to take
   inference *out* of the pipeline. Report and stop.
4. **Build the paper-space sheet model, plot-scale assertion or scale-bar derivation** — P1 (#53).
   Consume `Sheet`/`Viewport` per §4.12.
5. **Build the ISO 7200 title block, the revision table or drawing-number enforcement** — P9 (#61).
   Consume them per §4.7. In particular: do **not** fix `isosheet.titleblock`'s missing truncation
   here, even though this spec's calibration found the defect. Baseline it and hand it to P9.
6. **Wire this into a build DAG, a manifest or CI** — P2 (#54), P3 (#55). Expose
   `check_drawing_dir` and `--format json` and stop.
7. **Touch the PDF/plot path or check the rasterised output** — P4 (#56).
8. **Check dimensions against geometry** — P6.
9. **Flip, propose or record a status change.** Ever.

Follow-up issues to open (do not implement):

* **F1** — *Opt-in furniture markers*: `class="north-arrow" / "legend" / "notes" / "schedule" /
  "revision-table"`, behind an opt-in (`Drawing(mark_furniture=True)`), parity-tested and coordinated
  with P3's manifest. Unlocks north-arrow and legend-region collision checks without declarations.
* **F2** — *`Drawing.inset()`*: a real inset helper that emits the panel and records its box and
  `extent_m`, so `inset-fill` needs no sidecar. Coordinate with P1's viewports.
* **F3** — *Colour/symbol inference for legend completeness*, so a legend can be checked without
  declarations (warn-only, needs a used-symbol registry).
* **F4** — *`isosheet.titleblock` cell overflow* (the two baselined findings): truncate/wrap values
  to cell width as `svg.svg_title_block` already does. Assign to P9's scope.
* **F5** — *Glyph-accurate metrics as an optional extra* (`fontTools`, `[legibility]` extra), used
  only to tighten, never to relax, and never in CI.
* **F6** — *Print legibility*: minimum font size at plot scale, text/background contrast ratio,
  minimum line weight. Different rules, different thresholds, same report shape.
* **F7** — *Unified verify register* (review item 28): one source of verify/gap facts, which would
  let `verify-annotated` drop its declaration input.

---

## 7. Risks

**R1 — False positives are the existential risk.** A checker that reports a defect on a good sheet
gets `# noqa`'d into irrelevance within a fortnight, and then its recall is zero. Mitigations, all
already built into the design: only the *readable layer* is checked, never geometry against geometry
or a label against the thing it labels; same-scope furniture internals are exempt; the badge rule
excludes deliberate overlays structurally rather than by configuration; pointer marks are exempt;
patterns and raster backdrops are transparent; thresholds are derived from print physics (0.4 mm
overlap, 0.5 mm clearance) with 5–10× measured headroom on the real defects; the shipped-examples
test (§5.4) makes an FP-producing rule unmergeable; and the two escape hatches both require a written
reason and both complain when stale. Calibration during specification found **zero** false positives
on both shipped sheets and four true positives — that ratio is the thing to protect.

**R2 — False negatives breed false confidence.** The known ones are written down (patterns treated as
transparent, curve paths over-estimated, pointer marks exempt, sub-threshold kisses, unknown-font
text needing 2× evidence, north-arrow glyph invisible without a declaration). The mitigation is
cultural and structural: `report.skipped` is printed on every run including successes, `--require`
can make a skip fatal in CI, and the module docstring states plainly that a clean report means "no
*checked* defect found", not "the sheet is good".

**R3 — Drift between emitter and checker.** If `svg.py` starts emitting a construct the parser does
not model, the parser **raises** rather than skipping (§4.9, §5.14). That converts a silent loss of
coverage into a failing build with a precise message. It will occasionally be annoying. That is the
correct trade.

**R4 — Declarations rot.** A hand-written `legibility.yaml` duplicating the generator's numbers can
drift. Mitigations: the sidecar route (generator-written, single source) is documented as preferred;
the frame declaration is cross-checked against detection and warns on mismatch; panels auto-detect,
so declarations are an enhancement rather than a requirement for the collision checks.

**R5 — Threshold creep.** Every threshold in `LegibilityConfig` is a place someone can hide a defect.
Mitigations: physical justification recorded for each default, in this document and in the module
docstring; overrides live in a git-tracked config, so loosening one is a reviewable diff; and the
`issued-gate` severity cannot be overridden at all.

**R6 — Someone reads a green check as approval.** The report's `ok` property, the CLI summary and the
module docstring all state it: passing every legibility check is **not** approval to issue. Only the
responsible engineer's signature moves a drawing to ISSUED FOR CONSTRUCTION, and no tool, CI run or
agent may do it.
