# P1 — paper-space sheet model + asserted plot scale

**Issue:** upstream #53 · **Milestone:** drawing-workflow
hardening · **Owner:** `senior-engineer` · **Status of this document:** implementation specification.
Absorbs 30-change-review items 5, 6 and 20.

This document is the requirements source for the implementation. It is written so that two
independent implementers produce the same observable behaviour. Where a fact is not in hand it is
recorded in §8 *Open questions* — **it is never guessed at**.

---

## 0. Hard constraints (restated, binding on every section below)

1. **Backward compatibility is sacred.** `technical_drawings_for_agents` is a shared CLI. No input that does not
   opt into the new sheet model may produce a single different output byte. The new behaviour is
   opt-in, and the parity is asserted by test (§6.1, §6.14).
2. **Never invent** a dimension, a spec value, or a capability. ISO 216 paper sizes are quoted
   because they are unambiguous and verifiable; ISO 5457 frame margins and the ISO 128/9175 pen
   series are **not** encoded here because the standards are not in hand (§8.1, §8.4). A gap is an
   open question, not a default dressed up as a citation.
3. **The ISSUED gate is untouchable.** Nothing in this change reads, writes, derives, relaxes or
   routes around `meta.status` / `meta.for_construction`. See §5.6 for the explicit argument.
4. **Determinism.** Every number in this feature is computed by tested code from declared inputs.
   No LLM participates in a calculation, a build order, or output assembly. Prefer loud failure to
   a silent no-op or a degraded artifact.
5. **House style.** Frozen dataclasses; one module-specific error class (`SheetError`); config
   validated on load with precise, contextual messages; tests in `tests/test_*.py` named for the
   behaviour they assert. `src/technical_drawings_for_agents/components/layout.py` is the reference pattern.

---

## 1. Intent

A sheet should be a **piece of paper**: an ISO 216 size in millimetres, with a frame inside declared
margins, carrying one or more viewports each of which binds a model extent to a **plot scale 1:N**
so that one metre of the world is an exactly known number of millimetres of paper. The printed scale
string, the scale-bar geometry, the scale-bar tick labels and the north arrow are then **derived
facts** — no one types them, so no one can mistype them. `validate` recomputes them from the sheet's
own machine-readable record and fails when a claimed scale disagrees with the geometry at the
declared paper size. "A1 at 1:200" becomes expressible, and a scale rule laid on the printed sheet
measures the world correctly.

The failure it prevents is a drawing that **misstates its own scale**, which is the fastest way for a
drawing to fail scrutiny — and it is happening today. The live Basin site GA claims `1:1250 (A3)` in
its title block while its geometry plots at approximately **1:1157** on a page that is **not A3**
(§2.6). An engineer measuring 43.2 mm on that sheet and applying the stated 1:1250 reads **54.0 m**
where the model says **50.0 m** — an 8 % error, silently, on a sheet whose numbers are otherwise
sound. The inset on the same sheet is labelled "≈1:250" and plots at about **1:466**. Both defects
are arithmetic, both are invisible to review, and both become impossible once the scale is derived
rather than typed.

---

## 2. Current state

Every claim below cites the file and line in the repository at `origin/main` (`0c907b7`).

### 2.1 There is no paper. There are pixels.

`ViewBox` (`src/technical_drawings_for_agents/svg.py:82-122`) maps model metres to **SVG pixel** coordinates. Its
fields are `real_min_x/real_max_x/real_min_y/real_max_y`, `svg_width`, `svg_height` and a single
scalar `padding: float = 60.0` (`svg.py:86-92`). The mapping is an **auto-fit**: `scale` is
`min(usable_w / range_x, usable_h / range_y)` (`svg.py:94-102`) — whatever px/m happens to make the
extent fit the canvas. There is no plot scale, no millimetre anywhere, and no paper size.

Consequences, all present in the code as written:

- `ViewBox.scale` returns **`1.0` silently** when either range is non-positive (`svg.py:100-101`).
  A degenerate extent therefore produces a plausible-looking sheet at an arbitrary scale instead of
  an error. This violates "prefer loud failure"; it cannot be changed without breaking compatibility,
  so the new model must not repeat it (§5.9).
- `ViewBox` is a **mutable** dataclass (`svg.py:82`), against the frozen-dataclass house pattern
  used in `components/layout.py:49`, `:65`, `:71`, `:80`, `:94`, `:111`.
- `padding` is a single scalar, so asymmetric margins (a filing edge) are not expressible.

### 2.2 The sheet is a pixel canvas with a pixel border

`Drawing` (`svg.py:648-690`) takes `width: float`, `height: float` in pixels and
`border_margin: float = 24.0` pixels (`svg.py:656-662`). `Drawing.render()` (`svg.py:678-687`) emits
the border, elements, watermark and title block and wraps them with `svg_wrap`
(`svg.py:630-642`), whose root is `viewBox="0 0 {width} {height}" width="{width}"
height="{height}"` — **unitless user units**, i.e. CSS pixels. The page size of any PDF made from
that SVG is therefore whatever dots-per-inch convention the rasteriser applies, not a paper size the
drawing declared.

`svg_border` (`svg.py:595-597`) is a single rectangle inset by `margin` px. `svg_title_block`
(`svg.py:526-592`) positions itself from `width`/`height` with a **px** `block_width=280`,
`row_height=18` and `margin=24`, and estimates text capacity as `value_chars = max(8, int(value_w /
5.7))` (`svg.py:566`) — a px-space glyph-width guess. It only lays out correctly near one canvas
size. (P9/#61 owns fixing that; §7 defines the interface it needs.)

### 2.3 The scale bar is scale-true to the *ViewBox*, and it accepts a free label

`svg_scale_bar` (`svg.py:442-491`) is anchored at a **model** point (`real_x, real_y`) and its total
width is `vb.length(length_m)` (`svg.py:459-461`) — so its length is true to the ViewBox's arbitrary
px/m, whatever that is. Its `height_px=8` and its label offsets (`+18`, `-5`) are pixels
(`svg.py:449`, `:486`, `:489`).

**The issue is wrong on one point, and it matters.** Issue #53 says "The scale bar label is a
hand-spaced literal: `label="0                50 m"`". In fact `svg_scale_bar` **already computes**
its tick labels: `value = length_m * i / divisions`, formatted by `_fmt_measure` with the unit
suffixed on the last tick only (`svg.py:483-487`). The `label` parameter (`svg.py:450`) is a separate
optional **caption** drawn once, centred, above the bar (`svg.py:488-489`). So the real defect in the
Basin sheet is not "labels are not computed" — it is that a caller has used the free caption slot to
paint a **second, hand-spaced, fake tick row** on top of a correctly computed one
(`basin-site/source.py:207-208`). The literal is doubly wrong: the spacing is monospace-guessed, and
the "50 m" it asserts is a *measurement claim* the bar's own geometry may contradict. The
correction changes the design: we do not need to compute the tick labels (they already are). We need
to **remove the slot that lets a caller assert an un-derived measurement**, and to make that
detectable in an emitted file (§3.5, §5.4, §6.10).

### 2.4 The north arrow is hard-coded "up"

`svg_north_arrow` (`svg.py:494-523`) computes its centre from a model point and then draws a fixed
upward glyph: `tip_y = cy - size / 2.0`, wings at `± size * 0.23` (`svg.py:509-521`). There is no
rotation parameter. It therefore asserts "model +y is north and north is up on the paper" — true for
the current sheets, unverifiable in general, and wrong the moment a viewport is rotated.

### 2.5 `validate` greps for marker strings; it checks no geometry

`validate_svg` (`src/technical_drawings_for_agents/validate.py:45-56`) reads the SVG as text and asserts three
substrings are present: `class="title-block"`, `class="scale-bar"`, `class="status-watermark"`
(`validate.py:23-31`). There is no check that any of them is *correct*. `validate_drawing_dir`
(`validate.py:83-119`) adds `meta.yaml` loading plus `DrawingMeta.validate()`, and the P&ID data
model where a `*.pid.yaml` is present. `validate_target` (`validate.py:122-132`) dispatches on
directory / `.yaml` / `.svg`. `ValidationResult` (`validate.py:34-42`) has `problems` and `checked`
and an `ok` property — **no warnings channel**.

`meta.scale` is a **free-text string** with no structure and no consumer other than the title block:
declared at `src/technical_drawings_for_agents/meta.py:24`, passed through `DrawingMeta.title_block()`
(`meta.py:82-91`) into `svg_title_block(scale=...)`. Nothing validates it. The example sheet says
`scale: "1:50"` (`drawings/example/simple-section/meta.yaml`) — nothing anywhere confirms that.

The **ISSUED gate** lives at `meta.py:74-79`: `for_construction` true with `status_key != "ISSUED"`
is a problem. It is reached only via `DrawingMeta.validate()`, called from `validate.py:92`.

### 2.6 The real consumer, measured

`<project>/drawings/basin-site/source.py`
is the honest test of this design. Its facts:

| Fact | Value | Citation |
|---|---|---|
| Model frame (UTM30N) | `788392, 322125 → 788702, 322395` = **310 m × 270 m** | `source.py:65` |
| Canvas | `W, H = 1600, 1030` px, `PAD = 74` px | `source.py:68-69` |
| ViewBox | `ViewBox(FXMIN, FXMAX, FYMIN, FYMAX, W, H, padding=PAD)` | `source.py:70` |
| Claimed scale | `scale="1:1250 (A3)"` — hand-typed into the title block | `source.py:113` |
| Scale bar | `svg_scale_bar(vb, …, 50, divisions=5, unit="m", label="0                50 m")` | `source.py:207-208` |
| North arrow | `svg_north_arrow(vb, …, size_px=46)` — fixed "up" | `source.py:209` |
| Inset | second `ViewBox(788582, 788620, 322329, 322354, 360, 300, padding=26)`, captioned **"≈1:250"** as literal text | `source.py:222`, `:226` |
| PDF | produced by an out-of-band `rsvg-convert` call in the generator, not by the toolkit | `source.py:278-283` |

Derived (arithmetic from `svg.py:96-102`, reproducible):

- usable canvas `1452 × 882` px; `min(1452/310, 882/270)` → the **height** binds →
  **3.266667 px/m**.
- At the CSS-pixel convention librsvg applies (96 dpi, `1 px = 25.4/96 mm = 0.264583 mm`) the page is
  **423.33 × 272.52 mm** — *not* A3 (297 × 420 mm; 420 × 297 landscape) — and one model metre is
  **0.864306 mm**, i.e. **1 : 1157.0**.
- Printed instead fit-to-A3-landscape, the width binds (canvas aspect 1.553 vs A3 1.414) giving
  **1 : 1166.2**; fit-to-height would give **1 : 1061.6**.
- Therefore the claimed **1:1250 is unachievable by any plausible px→paper mapping**, and *which*
  wrong value you get depends on the rasteriser. The 50 m scale bar measures **43.215 mm**; read at
  the stated 1:1250 that is **54.02 m** (+8.0 %).
- The inset: usable `308 × 248` px over `38 m × 25 m` → `min(8.105, 9.920)` = **8.105 px/m** =
  2.1445 mm/m = **1 : 466.3**, against its literal caption "≈1:250" (86 % overstated). Note the
  claimed *ratio* between main view and inset (1250:250 = 5.0) is also wrong — the true ratio is 2.48.

Both numbers were typed by a careful author using a good toolkit. That is the point: the defect is
structural, not careless.

### 2.7 `isosheet.py` — schematic chrome, deliberately not in scope

`isosheet.sheet()` (`src/technical_drawings_for_agents/isosheet.py:143-169`) assembles the black-on-white ISO house
chrome around a **nested, auto-scaled** inner `<svg>`: `sheet_size=(1500, 950)` px, drawing band
`ax, ay, aw, ah = 44, 96, SW-88, SH-96-300` (`isosheet.py:152`), inner viewport
`preserveAspectRatio="xMidYMin meet"` (`isosheet.py:159`). Its consumers — `bfd` and `pid` — are
**NTS schematics**, which `validate` already exempts from the scale-bar requirement
(`validate.py:31`, `SCHEMATIC_MARKERS`). Nothing in this change touches it (§7).

### 2.8 Also true, and load-bearing for the design

- `DxfBuilder` (`src/technical_drawings_for_agents/dxf.py:32-158`) writes **real metres** in model space
  (`$INSUNITS = 6`, `dxf.py:42`). It has no paper-space/layout concept. Model space stays metres:
  the plot scale is a *sheet* fact, not a geometry fact.
- The `components` engine (`components/spec.py`, `place.py`, `emit.py`) is entirely in world metres
  and is unaffected.
- `render.py` chooses PDF/PNG backends by content (`render.py:78-87`, `:152-201`) and knows nothing
  about page size. P4/#56 owns the plot path; this spec makes the page size *declared and emitted*
  so P4 has something true to honour.

---

## 3. Design

### 3.0 Shape of the change

One new module, `src/technical_drawings_for_agents/sheet.py`, plus additive-only edits to `validate.py`, `meta.py`,
`cli.py` and `__init__.py`. **`svg.py` and `isosheet.py` are not modified at all** — that is the
cheapest possible guarantee of byte-identical legacy output, and it is a requirement, not a
preference (§6.1).

```
sheet.py     PaperSize · Orientation · Margins · Reservation · SheetFrame
             PlotScale · Viewport · Sheet · SheetDrawing · SheetError
             sheet_frame() · sheet_scale_bar() · sheet_north_arrow()
             load_sheet_config() · sheet_metadata_json() · read_sheet_metadata()
meta.py      + optional `sheet:` block  (additive; absent ⇒ nothing changes)
validate.py  + `warnings` field on ValidationResult; + check_sheet_svg()
cli.py       + `technical_drawings_for_agents sheet` subcommand; + `validate --strict`
```

### 3.1 Two exact mappings, kept apart

The whole feature is two multiplications that must never be conflated.

**(a) millimetre → device.** The emitted root element declares real paper units and a 1:1 user-unit
mapping:

```
<svg xmlns="http://www.w3.org/2000/svg" width="841mm" height="594mm" viewBox="0 0 841 594" …>
```

**One SVG user unit is exactly one millimetre of paper.** There is no dpi anywhere and no
`preserveAspectRatio` scaling to guess at: the viewBox aspect equals the physical aspect by
construction. Every furniture coordinate, stroke width, font size and dash length emitted by
`sheet.py` is a paper millimetre.

**(b) model metre → paper millimetre.** For plot scale 1:N,

```
mm_paper = m_model * 1000.0 / N
```

`N` is a positive integer (§5.2), so `1000.0 / N` is one exact division and no accumulated
tolerance. Model coordinates are metres throughout (unchanged, matching `DxfBuilder`). The two
mappings compose only inside `Viewport.point()`; nothing else may multiply them.

> **Anti-requirement.** The model→paper map must **not** be implemented as an SVG
> `transform="scale(...)"` on a group. A `scale(1/200)` group would multiply every stroke width,
> font size and dash array by 1/200 and a `scale(s, -s)` would mirror all text. Lineweights and text
> heights are *paper* quantities. The map is arithmetic in Python, exactly as `ViewBox` does it
> today; the viewport uses a `<clipPath>`, never a scale transform.

### 3.2 Data model — the `sheet:` block

The block is **optional** and lives in the drawing's existing `meta.yaml`. Its presence is the sole
opt-in switch for every behaviour in this spec. It is also loadable standalone from any YAML file
with a top-level `sheet:` key, for tests and for generators that keep sheet config separate.

```yaml
# meta.yaml — everything outside `sheet:` is unchanged.
number: STA-SITE-GA-001
title: WTP Site General Arrangement
revision: B
units: m
status: CONCEPT
for_construction: false
# scale: omitted — DERIVED once `sheet:` is present (see below)

sheet:
  size: A3                       # required
  orientation: landscape         # optional, default: landscape
  margins_mm: 10                 # optional, default: 10 (house default — see §8.1)
  reserve:                       # optional, default: []
    - {name: title-block, edge: bottom, size_mm: 60}
    - {name: legend,      edge: right,  size_mm: 80}
  viewport:                      # required
    extent_m: [788392, 322125, 788702, 322395]   # [xmin, ymin, xmax, ymax], model units
    scale: 1250                  # int denominator, or the string "fit"
    rotation_deg: 0.0            # optional, default 0.0
    on_overflow: error           # optional, default "error"; or "fit"
    align: center                # optional, default "center"
  north:                         # optional; omit ⇒ no north arrow is required or derived
    model_bearing_deg: 0.0       # optional, default 0.0 — see §3.6
    label: "N"                   # optional, default "N"
  scale_bar:                     # optional; omit ⇒ defaults below
    length_m: null               # optional, default null ⇒ derived (§3.5)
    divisions: 5                 # optional, default 5
    unit: m                      # optional, default "m"; one of {m, km}
```

#### Field table

| Path | Type | Default | Validation (all violations raise `SheetError`) |
|---|---|---|---|
| `sheet` | mapping | absent | Absent ⇒ **all new behaviour off**. Present but not a mapping ⇒ `"meta.yaml: sheet must be a mapping"`. |
| `sheet.size` | str | **required** | Case-insensitive, must be a key of `PAPER_SIZES_MM` (§3.3). Unknown ⇒ `"sheet.size 'A6' is not a supported ISO 216 size (available: A0, A1, A2, A3, A4)"`. |
| `sheet.orientation` | str | `"landscape"` | One of `{"portrait", "landscape"}`, case-insensitive. |
| `sheet.margins_mm` | number \| mapping | `10` | Number ⇒ all four edges. Mapping keys ⊆ `{left, right, top, bottom}`, unlisted edges default to `10`. Each `> 0`, finite, and `left+right < paper width`, `top+bottom < paper height`; else `"sheet.margins_mm: left+right (900.0) exceeds the A3 width (420.0)"`. Booleans rejected (`isinstance(v, bool)` guard, as `layout.py:236`). |
| `sheet.reserve` | list | `[]` | Each item a mapping with `name` (non-empty str, unique across the list), `edge` ∈ `{left, right, top, bottom}`, `size_mm` > 0. Applied **in listed order** (§3.4). Duplicate name ⇒ `"sheet.reserve[2].name 'legend' is already used"`. |
| `sheet.viewport` | mapping | **required** | Missing ⇒ `"meta.yaml: sheet needs a viewport: {extent_m, scale}"`. |
| `…viewport.extent_m` | `[xmin, ymin, xmax, ymax]` | **required** | Exactly 4 finite numbers, booleans rejected; `xmax > xmin` and `ymax > ymin` strictly, else `"sheet.viewport.extent_m: xmax (788702.0) must be greater than xmin (788702.0)"`. **Never silently substituted** (contrast `svg.py:100-101`). |
| `…viewport.scale` | int \| `"fit"` | **required** | Integer ≥ 1 (a plain `int`; `1250.0` and `"1250"` are rejected with `"sheet.viewport.scale must be a positive integer denominator (1:N) or the string 'fit', got 1250.0"`), or the exact lowercase string `"fit"`. |
| `…viewport.rotation_deg` | number | `0.0` | Finite; normalised to `[0, 360)` by `value % 360.0`. |
| `…viewport.on_overflow` | str | `"error"` | One of `{"error", "fit"}` (§5.3). |
| `…viewport.align` | str | `"center"` | `"center"` only in v1; any other value ⇒ `"sheet.viewport.align: only 'center' is supported (see issue #53 follow-ups)"`. Reserved so a later corner alignment is not a breaking change. |
| `sheet.north` | mapping | absent | Absent ⇒ no north arrow derived and none required. Present ⇒ `model_bearing_deg` finite (normalised `% 360`), `label` non-empty str. |
| `sheet.scale_bar.length_m` | number \| null | `null` | `null` ⇒ derived (§3.5). Given ⇒ `> 0`, finite, and must satisfy `length_m * 1000 / N ≤ viewport_frame.width_mm`, else `"sheet.scale_bar.length_m 500 plots 400.0 mm at 1:1250, wider than the 248.0 mm viewport"`. |
| `sheet.scale_bar.divisions` | int | `5` | Integer ≥ 1 (mirrors `svg.py:457-458`). |
| `sheet.scale_bar.unit` | str | `"m"` | `"m"` or `"km"`. `"km"` divides tick values by 1000; the mapping (a) is unchanged. |
| `scale` (top level) | str | `""` | **If `sheet:` is present**: must be empty/absent, or exactly equal the derived canonical string (§3.7). Any other value is a validation **problem** (§5.1). |

Unknown keys anywhere under `sheet:` are an **error**, not ignored:
`"sheet.viewport: unknown key 'plot_scale' (expected: align, extent_m, on_overflow, rotation_deg, scale)"`.
Rationale: a typo'd `scale_denominator:` that is silently dropped would leave the sheet at a
different scale than its author wrote — exactly the class of defect this issue closes.

### 3.3 ISO 216 sizes, in millimetres

```python
PAPER_SIZES_MM: dict[str, tuple[float, float]] = {   # (short_edge, long_edge)
    "A0": (841.0, 1189.0),
    "A1": (594.0, 841.0),
    "A2": (420.0, 594.0),
    "A3": (297.0, 420.0),
    "A4": (210.0, 297.0),
}
```

These are the ISO 216 A-series trim dimensions. **Portrait** is `width = short, height = long`;
**landscape** swaps them. So A1 landscape is `841.0 × 594.0` mm and A3 landscape is `420.0 × 297.0`
mm. Values are exact integers in millimetres and are stored as floats so the arithmetic never
promotes.

Issue #53 names A3/A2/A1/A0. **A4 is included** because it is ISO 216, is unambiguous, and detail
and schedule sheets need it; the addition costs one table row and cannot mislead. Anything outside
this table — A5, the B/C series, ISO 5457 elongated formats, arbitrary sizes — is **rejected loudly**
and is a follow-up (§7.3), because supporting an unbounded size means inventing frame conventions we
do not have.

### 3.4 Frames and reservations

```python
@dataclass(frozen=True)
class SheetFrame:
    """An axis-aligned rectangle in paper millimetres, origin top-left, +y down."""
    x_mm: float
    y_mm: float
    width_mm: float
    height_mm: float
```

`+y down` matches the SVG coordinate sense, so no furniture code flips a sign. (Model +y up is
handled once, inside `Viewport.point()`.)

Resolution order, fully deterministic:

1. **Paper rect** — `(0, 0, paper_width_mm, paper_height_mm)` from §3.3 and `orientation`.
2. **Drawing frame** — the paper rect inset by `margins_mm`. This is what `sheet_frame()` strokes and
   what P9's title block anchors to.
3. **Free rect** — starts equal to the drawing frame; each `reserve` entry, **in listed order**, cuts
   `size_mm` off the named edge of the current free rect and yields that strip as a named
   `SheetFrame`. A reservation that would leave a free rect with non-positive width or height is an
   error: `"sheet.reserve[1] ('legend', right, 400.0 mm) leaves no room: free width would be -12.0 mm"`.
4. **Viewport frame** — the final free rect.

Order-dependence is real (bottom-then-right ≠ right-then-bottom) and is therefore **declared in the
data**, in list order, rather than resolved by a solver. A solver would be another thing to
reimplement identically; a list is not.

Reserved regions are exposed as `Sheet.regions: dict[str, SheetFrame]` (insertion-ordered) for P9 and
for legend/notes/schedule furniture that other issues own. **This spec draws nothing in a reserved
region** — it allocates the rectangle and stops there.

### 3.5 The scale bar, derived end to end

`sheet_scale_bar(viewport, frame, *, length_m=None, divisions=5, unit="m") -> str`.

- **Length.** When `length_m` is `None` it is derived: take `target_mm = 0.25 *
  viewport_frame.width_mm`, convert to model metres (`target_m = target_mm * N / 1000`), and pick the
  largest value from the round series `{1, 2, 5} × 10ⁿ` for integer `n ∈ [-3, 6]` that is
  `≤ target_m`. If no member qualifies (an extremely small frame), that is an error, not a fallback:
  `"cannot derive a round scale-bar length for a 3.0 mm viewport at 1:1250; set sheet.scale_bar.length_m"`.
  A round bar length is the point of a scale bar — nobody measures against 43 m.
- **Geometry.** Total bar width is `length_m * 1000 / N` mm. Divisions are equal:
  `seg_mm = total_mm / divisions`. Bar height is `3.0` mm; tick overshoot `1.5` mm; tick-label
  baseline `4.5` mm below the bar; ratio-text baseline `8.5` mm below the bar. These are house
  furniture dimensions of this module, stated here so both implementations agree, and are *not*
  claimed to be from any standard (§8.4).
- **Labels.** Tick `i ∈ [0, divisions]` is labelled with the value `length_m * i / divisions`,
  formatted by the same rule as `svg.py:159-162` (`_fmt_measure`: integral values as integers, else
  `%g`), with the unit suffixed **on the last tick only** — the existing behaviour, kept
  deliberately so the two bars read alike. The derived ratio string (`"1:1250"`) is drawn centred
  below.
- **No caption parameter exists.** There is no `label=`. The bar's every glyph is computed. See §5.4.
- **Placement** is in paper millimetres inside the viewport frame: bottom-left of the frame, inset
  `6.0` mm from the left and bottom edges. The bar is **never** anchored at a model point (contrast
  `svg.py:459`) — a piece of sheet furniture whose position depends on the model extent moves when
  the extent is nudged, and that is how the Basin sheet's two scale bars ended up nearly colliding.
- **Markers.** The group is
  `<g class="scale-bar" data-tdfa-bar="length_m=50;divisions=5;unit=m;total_mm=40.000">`.
  `class="scale-bar"` is retained verbatim so the existing `validate` marker check
  (`validate.py:25`) passes unchanged.

### 3.6 The north arrow, derived from the viewport

`sheet_north_arrow(viewport, frame, *, label="N", size_mm=18.0) -> str`.

The paper-space bearing of north is computed, never assumed:

```
north_paper_deg = (viewport.rotation_deg - north.model_bearing_deg) % 360.0
```

where `north.model_bearing_deg` is the bearing of **north** measured from **model +y**, clockwise
positive. With both zero the arrow points to the top of the sheet — identical in intent to today's
fixed glyph (`svg.py:509-521`), so a sheet with no rotation and no declared bearing looks the way it
does now. The glyph is emitted at its neutral "up" geometry inside a
`<g class="north-arrow" transform="rotate({north_paper_deg:.3f} {cx:.3f} {cy:.3f})">`; only the
arrow rotates, and the label glyph is counter-rotated about its own centre so the letter stays
upright and readable at any bearing. Placed top-left of the viewport frame, inset `10.0` mm.

`model_bearing_deg` defaults to `0.0`, meaning "model +y is taken as north". For a projected CRS
that is **grid** north, which differs from true north by the meridian convergence (up to
about 3° in a UTM zone). The default is therefore a declared assumption, not a computed fact, and
computing convergence from the CRS is an open question owned by P8/#60 (§8.2). This spec does not
compute it and does not silently label grid north as true north: with a non-zero
`model_bearing_deg` the emitted metadata records the value so a reviewer can see what was claimed.

### 3.7 Public Python API

```python
# src/technical_drawings_for_agents/sheet.py

class SheetError(ValueError):
    """Raised when a sheet config, plot scale or viewport binding is malformed."""


PAPER_SIZES_MM: dict[str, tuple[float, float]]      # §3.3
PREFERRED_DENOMINATORS: tuple[int, ...]             # §5.2
SHEET_METADATA_SCHEMA = "technical_drawings_for_agents/sheet/1"
SHEET_METADATA_ID = "technical_drawings_for_agents-sheet"
LW_DEFAULT_MM = 0.25                                # provisional; P7/#59 owns the pen table


@dataclass(frozen=True)
class Margins:
    left_mm: float = 10.0
    right_mm: float = 10.0
    top_mm: float = 10.0
    bottom_mm: float = 10.0


@dataclass(frozen=True)
class PaperSize:
    name: str                 # "A1"
    orientation: str          # "landscape" | "portrait"

    @property
    def width_mm(self) -> float: ...
    @property
    def height_mm(self) -> float: ...


@dataclass(frozen=True)
class Reservation:
    name: str
    edge: str                 # left | right | top | bottom
    size_mm: float


@dataclass(frozen=True)
class SheetFrame:            # §3.4
    x_mm: float
    y_mm: float
    width_mm: float
    height_mm: float

    @property
    def right_mm(self) -> float: ...
    @property
    def bottom_mm(self) -> float: ...
    def contains_mm(self, x_mm: float, y_mm: float, tol_mm: float = 0.0) -> bool: ...


@dataclass(frozen=True)
class PlotScale:
    """An exact 1:N plot scale. N is a positive integer denominator."""
    denominator: int

    @property
    def text(self) -> str:                       # "1:200" — the canonical printed string
        return f"1:{self.denominator}"
    @property
    def is_preferred(self) -> bool: ...          # §5.2
    def mm_per_m(self) -> float:                 # 1000.0 / denominator
        return 1000.0 / self.denominator
    def paper_mm(self, model_m: float) -> float: ...
    def model_m(self, paper_mm: float) -> float: ...

    @classmethod
    def parse(cls, text: str) -> "PlotScale": ...
        # Accepts exactly "1:N" with an integer N >= 1, optional surrounding
        # whitespace. Anything else -> SheetError with the offending text quoted.
        # "1:1250 (A3)" is REJECTED: "cannot parse plot scale '1:1250 (A3)':
        # expected '1:N' with an integer N".


@dataclass(frozen=True)
class Viewport:
    """Binds a model extent to a plot scale inside a paper frame."""
    extent_m: tuple[float, float, float, float]  # xmin, ymin, xmax, ymax
    scale: PlotScale
    frame: SheetFrame                            # paper mm, from Sheet resolution
    rotation_deg: float = 0.0

    # --- the model -> paper map (the only place the two mappings compose) ---
    def point(self, model_x: float, model_y: float) -> tuple[float, float]: ...
    def length_mm(self, model_m: float) -> float: ...       # == scale.paper_mm(model_m)
    def model_m(self, paper_mm: float) -> float: ...
    def contains(self, model_x: float, model_y: float, tol_m: float = 0.0) -> bool: ...

    # --- derived facts ---
    @property
    def required_mm(self) -> tuple[float, float]: ...       # rotated-bbox size on paper
    @property
    def fits(self) -> bool: ...
    @property
    def centre_m(self) -> tuple[float, float]: ...
    @property
    def scale_text(self) -> str: ...                        # == self.scale.text


@dataclass(frozen=True)
class Sheet:
    paper: PaperSize
    margins: Margins
    viewport: Viewport
    regions: dict[str, SheetFrame]                # reserved, insertion-ordered
    north: NorthRef | None = None
    scale_bar: ScaleBarSpec = ScaleBarSpec()
    source: Path | None = None                    # the meta.yaml it was loaded from

    @property
    def frame(self) -> SheetFrame: ...            # drawing frame, inside the margins
    @property
    def scale_text(self) -> str: ...              # "1:200" — for P9's SCALE field
    def metadata_json(self) -> str: ...           # §3.8


def load_sheet_config(path: str | Path) -> Sheet | None:
    """Load the `sheet:` block from a YAML file. Returns None when absent.

    Absence is the opt-out and is never an error. A present-but-invalid block
    always raises SheetError with a message naming the offending path.
    """


def resolve_sheet(config: dict, *, context: str = "sheet") -> Sheet:
    """Build a Sheet from an already-parsed mapping (the testable core)."""


def sheet_frame(sheet: Sheet, *, stroke_mm: float = 0.35) -> str: ...
def sheet_scale_bar(viewport, frame, *, length_m=None, divisions=5, unit="m") -> str: ...
def sheet_north_arrow(viewport, frame, *, label="N", size_mm=18.0) -> str: ...
def sheet_metadata_json(sheet: Sheet) -> str: ...
def read_sheet_metadata(svg_text: str) -> dict | None: ...


@dataclass(frozen=True)
class NorthRef:
    model_bearing_deg: float = 0.0
    label: str = "N"


@dataclass(frozen=True)
class ScaleBarSpec:
    length_m: float | None = None
    divisions: int = 5
    unit: str = "m"


@dataclass
class SheetDrawing:
    """Paper-space sheet assembler. Mutable, mirroring `Drawing` (svg.py:648)."""
    sheet: Sheet
    status: str | None = None
    title_block: dict | None = None
    background: str = "#ffffff"
    elements: list[str] = field(default_factory=list)
    defs: list[str] = field(default_factory=list)

    @property
    def vp(self) -> Viewport: ...                 # mirrors Drawing.vb (svg.py:666)
    def add(self, *elements: str) -> "SheetDrawing": ...
    def add_defs(self, *defs: str) -> "SheetDrawing": ...
    def add_model(self, *points_and_elements) -> "SheetDrawing": ...
    def render(self) -> str: ...
    def svg(self) -> str: ...                     # alias, mirrors svg.py:689
```

`Viewport.point()` is exactly:

```python
mm_per_m = 1000.0 / self.scale.denominator
cx, cy = self.centre_m
dx, dy = model_x - cx, model_y - cy
if self.rotation_deg:
    a = math.radians(self.rotation_deg)
    dx, dy = dx * math.cos(a) - dy * math.sin(a), dx * math.sin(a) + dy * math.cos(a)
fx = self.frame.x_mm + self.frame.width_mm / 2.0
fy = self.frame.y_mm + self.frame.height_mm / 2.0
return (fx + dx * mm_per_m, fy - dy * mm_per_m)      # -dy: model +y up, paper +y down
```

Rotation is **counter-clockwise positive about the extent centre**, and the extent centre maps to
the viewport-frame centre (`align: center`). `required_mm` is the axis-aligned bounding box of the
rotated extent:

```python
w_m, h_m = xmax - xmin, ymax - ymin
a = radians(rotation_deg)
rw = abs(w_m * cos(a)) + abs(h_m * sin(a))
rh = abs(w_m * sin(a)) + abs(h_m * cos(a))
required = (rw * mm_per_m, rh * mm_per_m)
```

`fits` is `required_mm[0] <= frame.width_mm + FIT_TOL_MM and required_mm[1] <= frame.height_mm +
FIT_TOL_MM`, with `FIT_TOL_MM = 1e-6` to absorb float representation only (§5.3).

`SheetDrawing.render()` emits, in this fixed order:

1. root `<svg>` with `width="{W}mm" height="{H}mm" viewBox="0 0 {W} {H}"` (formatted `%g` so `841mm`
   not `841.000mm`), then `<title>` omitted, then the metadata element (§3.8);
2. the background `<rect width="{W}" height="{H}" fill="{background}"/>`;
3. `<defs>` — the caller's defs, then the viewport `<clipPath id="tdfa-viewport">`;
4. `sheet_frame(...)`;
5. `<g class="viewport" clip-path="url(#tdfa-viewport)">` + the caller's elements + `</g>`;
6. the derived scale bar; the derived north arrow if `sheet.north` is set;
7. `svg_status_watermark(W, H, status)` when `status is not None` — reused unchanged from
   `svg.py:600`, called with **millimetre** width/height (it is dimensionless in its own units, so
   the diagonal text sizes correctly);
8. the title block **only if** `title_block` is not None, via the P9 interface (§7.1). Until P9
   lands, `SheetDrawing` renders no title block and `title_block` must be `None`; passing a dict
   raises `SheetError("title block rendering in paper space is issue #61; pass title_block=None")`.
   A stub that draws a px-sized block into a mm sheet would be a wrong artifact, and a wrong artifact
   is worse than a missing one.

Numeric formatting inside `sheet.py` is `f"{value:.3f}"` (1 µm on paper), with `-0.0` normalised to
`0.000` so output is stable across platforms. Callers mapping model geometry through
`Viewport.point()` into the legacy `svg_line`/`svg_rect`/… primitives get those primitives'
`:.1f` (`svg.py:171-212`), i.e. 0.1 mm on paper — acceptable, documented, and the reason the
acceptance tolerance for caller geometry is ±0.05 mm while furniture is checked to ±0.001 mm.

> **Trap, stated loudly in the module docstring and the README.** The legacy `svg_*` primitives
> default to `stroke_width=1` / `1.5` (`svg.py:168`, `:176`) and `font_size=11` (`svg.py:207`) —
> **pixel** defaults, about 0.26 mm at 96 dpi. In a millimetre sheet those become 1.0–1.5 mm lines
> and 11 mm text, i.e. 4× to 40× too heavy. A generator moving to `SheetDrawing` must pass explicit
> millimetre widths and font sizes. `sheet.py` exports `LW_DEFAULT_MM = 0.25` for its own furniture
> only; the real lineweight/linetype table is P7/#59 (§7.2).

### 3.8 The emitted sheet record (what makes `validate` possible)

`validate` operates on **files** (`validate.py:45-56`), never by re-running a generator — it must
not, because re-running is neither cheap nor side-effect-free. So the sheet must describe itself in
its own output. `SheetDrawing.render()` emits exactly one element:

```xml
<metadata id="technical_drawings_for_agents-sheet">{"model_units":"m","north":{"label":"N","model_bearing_deg":0.0,"paper_deg":0.0},"paper":{"height_mm":297.0,"name":"A3","orientation":"landscape","width_mm":420.0},"paper_units":"mm","scale":{"denominator":1250,"is_preferred":false,"mm_per_m":0.8,"text":"1:1250"},"scale_bar":{"divisions":5,"length_m":50.0,"total_mm":40.0,"unit":"m"},"schema":"technical_drawings_for_agents/sheet/1","viewport":{"drawn_extent_mm":[86.0,40.5,334.0,256.5],"extent_m":[788392.0,322125.0,788702.0,322395.0],"frame_mm":[10.0,10.0,400.0,277.0],"required_mm":[248.0,216.0],"rotation_deg":0.0}}</metadata>
```

Serialised with `json.dumps(payload, sort_keys=True, separators=(",", ":"))` — sorted keys, no
whitespace, so it is byte-stable and diffable. Floats are rounded to 6 decimals before dumping.
There are **no timestamps, paths, hostnames or version strings** in it: identical inputs give
identical bytes, and it does not pre-empt P3/#55's manifest and provenance stamp, which own
provenance.

`drawn_extent_mm` is the axis-aligned bounding box, in paper mm, of every point the sheet actually
mapped through `Viewport.point()` during construction (tracked by `Viewport` via a small mutable
recorder held by `SheetDrawing`; `Viewport` itself stays frozen). It is `null` when nothing was
mapped. It exists so containment is *checkable* — see §5.5.

`read_sheet_metadata(svg_text)` extracts and parses it, returning `None` when absent (a legacy
sheet) and raising `SheetError` when present but unparseable or of an unknown `schema`.

### 3.9 `validate` additions

`ValidationResult` (`validate.py:34-42`) gains one additive field:

```python
warnings: list[str] = field(default_factory=list)
```

`ok` still means `not self.problems` — **warnings never change the exit code** unless `--strict` is
passed. Existing constructor calls and attribute reads are unaffected.

New function `check_sheet_svg(svg_text, meta=None) -> tuple[list[str], list[str]]` returning
`(problems, warnings)`, wired into `validate_svg` and `validate_drawing_dir`. It runs **only** when
`read_sheet_metadata()` returns a record; on `None` it returns `([], [])` and nothing about legacy
validation changes.

Checks, in this order:

| # | Check | Severity | Message shape |
|---|---|---|---|
| V1 | Root `width`/`height` carry the `mm` unit and equal the recorded paper size to 1e-6 | problem | `"sheet: root width='1600' is not the declared paper width 420.0mm"` |
| V2 | Root `viewBox` is `0 0 {width_mm} {height_mm}` | problem | `"sheet: viewBox '0 0 1600 1030' does not match the A3 landscape paper box '0 0 420 297'"` |
| V3 | `scale.mm_per_m == 1000 / denominator` and `scale.text == f"1:{denominator}"` (record self-consistency) | problem | `"sheet: recorded scale text '1:1250' disagrees with denominator 1157"` |
| V4 | `viewport.required_mm` recomputed from `extent_m`, `rotation_deg` and `denominator` equals the recorded value to 1e-6 | problem | `"sheet: viewport requires 248.000×216.000 mm but the record claims 200.000×180.000 mm"` |
| V5 | `viewport.required_mm` fits `viewport.frame_mm` | problem | `"sheet: the model extent needs 512.000 mm of paper width; the viewport frame is 400.000 mm"` |
| V6 | `scale_bar.total_mm == length_m * 1000 / denominator` to 1e-6 | problem | `"sheet: the scale bar is drawn 43.216 mm wide; 50 m at 1:1250 is 40.000 mm"` |
| V7 | The `<g class="scale-bar">` group's text nodes are **exactly** the derived tick-label set plus the derived ratio string | problem | `"sheet: the scale bar carries 1 text element that is not a derived tick label: '0                50 m'"` |
| V8 | `meta.scale`, when non-empty, equals the recorded `scale.text` exactly | problem | `"meta: scale '1:1250 (A3)' contradicts the sheet's derived scale '1:1157' — remove meta.scale (it is derived) or fix the sheet"` |
| V9 | `viewport.drawn_extent_mm` lies within `viewport.frame_mm` | **warning** | `"sheet: drawn geometry extends 12.4 mm beyond the viewport frame (clipped)"` |
| V10 | `scale.is_preferred` | **warning** | `"sheet: 1:1250 is not an ISO 5455 preferred ratio (nearest preferred: 1:1000, 1:2000)"` |
| V11 | `north` present ⇒ a `class="north-arrow"` group exists, and its `rotate(...)` angle equals the recorded `north.paper_deg` to 1e-3 | problem | `"sheet: the north arrow is drawn at 0.000° but the record derives 38.900°"` |

V8 is the check issue #53 asks for. Note the direction of the fix in its wording: the derived value
wins, and the right resolution is normally to **delete** the hand-typed string.

### 3.10 CLI surface

**`technical_drawings_for_agents sheet <meta-or-config.yaml>`** — new, read-only, resolve-and-report.

```
technical_drawings_for_agents sheet <config.yaml> [--json] [--check]
```

| Flag | Effect |
|---|---|
| *(none)* | Print a human block to stdout: paper, orientation, margins, drawing frame, reservations, viewport frame, extent, required mm, resolved scale, mm/m, scale-bar length and total mm, north paper bearing. |
| `--json` | Print `Sheet.metadata_json()` (§3.8) and nothing else to stdout. |
| `--check` | Resolve and report only; suppress stdout on success (exit 0, no output) for CI use. |

Exit codes: **0** resolved and fits; **1** resolved but does not fit at the requested scale, or a
`SheetError` from validation of the block (message on stderr, prefixed `error: `); **2** usage error
— file missing, unreadable YAML, or **no `sheet:` block present** (`error: <path> has no sheet: block`).
The command **never writes any file** (§5.6).

**`technical_drawings_for_agents validate <target> [--strict]`** — one new flag. Behaviour unchanged without it.
`--strict` promotes every warning to a problem, so exit 1. Output contract:

- success, no warnings: `OK: <target> passed validation (<n> checks).` (unchanged, `cli.py:52`)
- success with warnings: the same `OK:` line, then one `warn: <text>` line **per warning on stdout**.
- failure: unchanged — `FAIL: …` plus `  - <problem>` lines on **stderr** (`cli.py:54-57`).

Exit codes unchanged: 0 pass, 1 problems, 2 unusable target. `render` and `ingest` are not touched.

---

## 4. Backward compatibility

Guaranteed structurally, not by care:

- `svg.py` and `isosheet.py` are **not edited**. `ViewBox`, `Drawing`, `svg_scale_bar` (including its
  `label=`), `svg_north_arrow`, `svg_title_block`, `svg_border`, `svg_wrap` behave identically, byte
  for byte. The existing committed artifacts
  (`drawings/example/simple-section/out/EXA-CIV-SEC-001.svg`,
  `drawings/example/synthetic-pid/out/SYN-PSK-PID-001.svg`) regenerate byte-identically (§6.1).
- No `meta.yaml` in the repo gains a `sheet:` block in this change. `DrawingMeta.from_dict`
  (`meta.py:38-55`) already routes unknown keys to `extra`, so `sheet:` lands in `extra` and
  `title_block()` (`meta.py:82-91`) is untouched. `DrawingMeta` gains one convenience property,
  `sheet_config -> dict | None`, returning `extra.get("sheet")`.
- Every new `validate` check is gated on the metadata element being present. A legacy sheet cannot
  acquire a new problem or a new warning.
- `ValidationResult.warnings` is a new field with a default factory; positional construction
  (`ValidationResult(target=…)`, `validate.py:47`) is unaffected.
- No new required dependency. `json`, `math`, `dataclasses`, `pathlib`, `yaml` are all already used.

---

## 5. Behaviour decisions, with rationale

### 5.1 A stated scale that contradicts the viewport is an **error**, never a warning

Issue #53 requires it, and the reason survives inspection: a sheet is a legal-ish document that is
measured off. A warning is a thing CI prints and humans stop reading; the whole defect class here is
"a number nobody re-checked". So V8 is a problem and `technical_drawings_for_agents validate` exits 1.

The corollary is that the printed scale is **derived, not accepted**. `meta.scale` becomes redundant
once `sheet:` is present, and the recommended migration is to delete it. It is not *forbidden* —
existing sheets carry it, some reviewers like seeing it in the file — but if present it must match
the canonical `f"1:{N}"` exactly. `"1:1250 (A3)"` does not match: the paper size belongs to
`sheet.size`, not smuggled into a scale string, and accepting decorated variants means writing a
parser for prose, which is how "1:1250 (A3)" got to be authoritative in the first place.

Rejected alternative: *auto-rewrite `meta.scale` to the derived value.* Rejected because a tool
silently editing the drawing's metadata record is the same category of act as a tool flipping a
status — the author must see the change in a diff they made.

### 5.2 "Fit" scales are allowed as an **input mode**, never as an **output**

`scale: fit` is permitted. It resolves, deterministically, to the smallest **round** denominator
that makes the extent fit:

```python
PREFERRED_DENOMINATORS = (1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500,
                          1000, 1250, 2000, 2500, 5000, 10000, 20000, 25000, 50000, 100000)
```

`fit` searches this tuple in ascending order and takes the first entry whose `required_mm` fits the
viewport frame; exhausting the tuple is an error naming the required denominator
(`"no preferred scale fits: the extent needs 1:143000 or smaller; widen the paper or set an explicit scale"`).

Two points, both deliberate:

- **The resolved value is what is recorded and printed.** The word `fit` never reaches the sheet,
  the metadata, or the title block. A sheet must state a ratio a person can set on a scale rule.
- **The series includes 1250 and 2500.** ISO 5455's preferred ratios are the 1/2/5 decades; 1:1250
  and 1:2500 are not in them but are in daily survey and site-plan use (and 1:1250 is what the Basin
  sheet asks for). So `PREFERRED_DENOMINATORS` is a *round-numbers* series for `fit`, while
  `PlotScale.is_preferred` reports membership of the strict 1/2/5-decade set and drives warning V10.
  Two concepts, two names, no fudge.

An **explicit** `scale: N` accepts **any** positive integer, including non-round ones, and warns
(V10) when it is not a 1/2/5 decade. Rejected alternative: *reject non-preferred ratios outright.*
Rejected because it would reject 1:1250, and a tool that refuses the scale the job actually uses
gets worked around, which is worse than a warning that gets read.

### 5.3 An extent that does not fit the paper at the requested scale **fails**

Default `on_overflow: error`. `Sheet` construction raises `SheetError`:

```
sheet.viewport: the extent (310.0 × 270.0 m) needs 620.000 × 540.000 mm at 1:500,
but the A3 landscape viewport frame is 400.000 × 277.000 mm.
Smallest preferred scale that fits: 1:1000. Set sheet.viewport.scale, enlarge
sheet.size, or set on_overflow: fit.
```

Rejected: **clip.** Silently cropping engineering geometry is the worst available outcome — the sheet
looks finished and content is gone.
Rejected: **auto-fit by default.** Quietly overriding a stated scale is the very substitution this
issue exists to eliminate, even when the tool then reports the new number honestly. The author asked
for 1:500; the answer is "no", not "here is 1:1000 instead".
Provided: **`on_overflow: fit`**, opt-in, explicit in the data, which resolves as §5.2 and emits a
warning naming both scales (`"sheet: 1:500 does not fit; on_overflow: fit resolved to 1:1000"`). The
resolved scale is what is printed, so the sheet stays truthful either way.

The fit comparison uses `FIT_TOL_MM = 1e-6` — enough to absorb IEEE-754 representation of exact
millimetre arithmetic, small enough that "it just fits" means it just fits. No percentage slack: a
tolerance that hides a 1 mm overflow is a tolerance that hides a mistake.

### 5.4 The literal scale-bar label is **rejected on paper-space sheets** and **deprecated in prose** for legacy sheets

Three options were on the table.

1. *Reject in `svg_scale_bar` itself.* **Rejected** — it changes behaviour for existing callers, and
   backward compatibility is sacred (§0.1). It would also break the legitimate use of `label` as a
   caption (`drawings/example/simple-section/source.py:161` passes `label="Scale (concept)"`, which
   asserts no measurement and is fine).
2. *Keep a `caption` parameter on the new bar, validated to contain no digits.* **Rejected** — the
   rule is arbitrary ("Scale 1:200" is a legitimate caption containing digits) and arbitrary rules
   diverge between implementations.
3. **Chosen:** the new `sheet_scale_bar` has **no caption slot at all**, and V7 makes the defect
   mechanically detectable in any emitted paper-space sheet: the text nodes inside
   `<g class="scale-bar">` must be *exactly* the derived tick-label set plus the derived ratio
   string. `label="0                50 m"` shows up as an extra text node and fails by name (§6.10).
   A caller who wants a note next to the bar adds a `svg_text` element like any other annotation —
   outside the scale-bar group, where it is annotation rather than a measurement claim.

Legacy `svg_scale_bar(label=...)` keeps working, and its docstring gains a deprecation note pointing
at `sheet_scale_bar` and at this defect. It is not removed here; removing it is a follow-up (§7.4).

### 5.5 Clipping is on, and the containment check is the loud half

The viewport emits a `<clipPath>`, so geometry beyond the extent cannot overprint the title block or
the margins. That is a *visual* decision and it does hide things — so it is paired with two
non-silent mechanisms: the declared extent must fit (§5.3, a hard error), and the actually-mapped
bbox is emitted as `drawn_extent_mm` and checked by V9 as a **warning**.

Warning, not error, because a symbol or dimension text legitimately reaches a few millimetres past a
plan extent, and a hard error would make the feature unusable on real sheets. Turning containment
into a proper error needs annotation-aware bounding boxes for text and symbols, which is exactly
P5/#57's job; `Viewport.contains()` and `drawn_extent_mm` are the primitives it consumes (§7.2).
`--strict` already lets CI treat V9 as fatal today.

### 5.6 The ISSUED gate stays exactly where it is

- Nothing in `sheet.py` reads or writes `status`, `for_construction`, or any lifecycle field.
  `SheetDrawing.status` is passed straight to the unmodified `svg_status_watermark` (`svg.py:600`)
  purely to stamp a watermark, and an unknown value still raises there (`svg.py:610-613`).
- `DrawingMeta.validate()` (`meta.py:63-80`) is **unmodified**, and remains the only authority on
  the gate. New checks can only **append** to `problems`; there is no code path in this change that
  removes, downgrades, or bypasses a problem produced by it.
- `--strict` is monotone: it can only turn warnings into problems. There is no flag, env var or YAML
  key introduced anywhere in this change that can make a failing validation pass.
- The new `technical_drawings_for_agents sheet` command is **read-only**: it opens YAML for reading and writes only to
  stdout/stderr. It cannot create or edit `meta.yaml`, and must not gain a `--write` flag (§7.5).
- Deriving a *correct* scale makes a sheet more reviewable; it says nothing about whether the sheet
  is ISSUED. That remains the responsible engineer's signature, recorded by a human edit to
  `meta.yaml`.

### 5.7 Rotation: how it interacts with the north arrow and the scale bar

- **North arrow — rotates, and its label counter-rotates.** The arrow's paper bearing is derived
  (§3.6). Rotating the glyph but leaving "N" upright is the drafting convention and the only
  readable option; a rotated letter at 217° is unreadable, and a *non*-rotated arrow on a rotated
  view is simply a lie. V11 asserts the drawn angle equals the derived one.
- **Scale bar — does not rotate.** It measures paper distance, and the model→paper map is
  **isotropic** (one `mm_per_m`, §3.1), so rotation cannot change how many millimetres a metre is.
  A rotated scale bar would also be harder to lay a rule against. It stays axis-aligned, anchored in
  paper millimetres.
- **Isotropy is required, and asserted by construction.** `Viewport` has a single `PlotScale`, so
  anisotropic (vertically exaggerated) views are *not expressible* — deliberately. Exaggerated
  long-sections are real and needed (`bfd`'s `profile` view is one), but they need two scales, two
  scale bars and an explicit "VERTICAL EXAGGERATION ×10" annotation, and getting that wrong is worse
  than not having it. Follow-up (§7.3).
- Rotation is normalised to `[0, 360)` and any real value is allowed. Restricting to multiples of 90°
  was considered and rejected: the Basin rafts sit on a 40.25° bearing (`source.py:266`), and aligning
  a viewport to the works is a normal thing to want.

### 5.8 The `Sheet` model coexists with `Drawing`; it does not replace it

Two assemblers, one deliberately: `Drawing` (px canvas, dark CAD theme, `svg.py:648`) and
`SheetDrawing` (mm paper, white default, `sheet.py`). No shared base class, no adapter, no
`Drawing(paper=…)` overload.

Rationale: a base class would put px and mm behaviour behind one type, and the single most dangerous
mistake available in this feature is a number in the wrong unit. Two types make the unit visible at
every call site and at review. `SheetDrawing` mirrors `Drawing`'s method names (`add`, `add_defs`,
`render`, `svg`, and `vp` alongside `vb`) so the authoring feel is familiar, and reuses `svg.py`'s
primitives and `svg_status_watermark` so there is one hatch catalogue and one watermark.

Rejected: *make `ViewBox` a subclass or special case of `Viewport`.* Rejected because `ViewBox`'s
auto-fit and its silent `1.0` degenerate fallback (`svg.py:100-101`) are behaviours the new model
must not inherit, and because touching `svg.py` risks the parity guarantee.

Migration is per sheet, opt-in, one generator at a time, and the two can coexist in one repository
indefinitely.

### 5.9 Degenerate and hostile input fails loudly

Every case that `ViewBox` currently absorbs is an error here — this is the single clearest
improvement in the module and it is worth naming: zero-width or inverted extents, non-finite numbers
(`nan`/`inf` rejected via `math.isfinite`), `scale: 0`, negative margins, margins exceeding the
paper, a reservation larger than the free rect, `divisions: 0`, a `scale_bar.length_m` wider than the
viewport, an unknown paper size, an unknown key. Booleans are rejected wherever a number is expected
(`isinstance(v, bool)` first, as `layout.py:236`, `:296`). There is no "sensible default" path
anywhere: a sheet that cannot be resolved does not get drawn.

---

## 6. Acceptance tests

All in `technical_drawings_for_agents/tests/test_sheet.py` unless stated. Named for the behaviour asserted, house
style (`tests/test_layout.py`). "Exact" means `==` on floats where the arithmetic is exact in binary
(all the mm values below are), otherwise `pytest.approx` with the stated tolerance.

**1. `test_legacy_sheets_render_byte_identically`** *(backward compatibility — the load-bearing one;
lives in `tests/test_example_smoke.py`)*
Setup: the committed golden `drawings/example/simple-section/out/EXA-CIV-SEC-001.svg`.
Action: run `drawings/example/simple-section/source.py` into a `tmp_path` out dir.
Expect: the produced `.svg` bytes equal the committed golden bytes. Additionally assert
`svg_scale_bar(ViewBox(0, 10, 0, 5, 400, 300), 0, 0, 5, label="Scale (concept)")` still contains
`>Scale (concept)<`, and that `Drawing(600, 400, vb, …).render()` starts with
`'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 600 400" width="600" height="400"'`.

**2. `test_a1_landscape_1_to_200_measures_a_known_model_distance`** *(the headline round-trip)*
Setup: `sheet: {size: A1, orientation: landscape, margins_mm: 10, viewport: {extent_m: [0, 0, 160, 100], scale: 200}}`.
Expect, exactly: `sheet.paper.width_mm == 841.0`, `height_mm == 594.0`; `sheet.frame ==
SheetFrame(10.0, 10.0, 821.0, 574.0)`; `viewport.scale.mm_per_m() == 5.0`;
**`viewport.length_mm(40.0) == 200.0`**; `viewport.required_mm == (800.0, 500.0)`;
`viewport.fits is True`; `sheet.scale_text == "1:200"`. In the rendered SVG: root starts
`<svg xmlns="http://www.w3.org/2000/svg" width="841mm" height="594mm" viewBox="0 0 841 594"`, and
`viewport.point(0, 0)` and `viewport.point(40, 0)` differ in x by `200.0` mm to 1e-9.

**3. `test_a3_landscape_1_to_1250_measures_a_known_model_distance`** *(the Basin case, done right)*
Setup: `{size: A3, orientation: landscape, margins_mm: 10, viewport: {extent_m: [788392, 322125, 788702, 322395], scale: 1250}}`.
Expect, exactly: paper `420.0 × 297.0`; frame `SheetFrame(10.0, 10.0, 400.0, 277.0)`;
`mm_per_m() == 0.8`; `required_mm == (248.0, 216.0)`; `fits is True`;
**`viewport.length_mm(50.0) == 40.0`**; the derived scale bar's `data-tdfa-bar` contains
`total_mm=40.000`; the rendered ratio text is `>1:1250<`. Also: the derived (`length_m: None`) bar
length is `100` m — `0.25 × 400 mm = 100 mm` → `125 m` target → largest `{1,2,5}×10ⁿ` ≤ 125 is
`100` — plotting `80.000` mm.

**4. `test_viewport_point_round_trips_model_to_paper_and_back`**
For scales 1, 200, 1250 and rotations 0, 40.25, 90, 217.5: for 20 pseudo-random model points inside
the extent (fixed `random.Random(20260724)`), `viewport.model_m(...)` inverts distances and
`point()` preserves distance ratios — `dist_mm(p, q) == dist_m(a, b) * 1000 / N` to 1e-9.
Also `viewport.point(*viewport.centre_m)` equals the viewport-frame centre to 1e-9 at every rotation.

**5. `test_stated_scale_contradicting_the_viewport_fails_validation`** *(the error path #53 names)*
Setup: `tmp_path` drawing dir with `meta.yaml` carrying a valid `sheet:` at `scale: 1250` **and**
`scale: "1:1250 (A3)"`, plus `out/X.svg` rendered from that sheet.
Action: `validate_target(dir)`.
Expect: `result.ok is False`; exactly one problem, containing `meta: scale '1:1250 (A3)'` and
`derived scale '1:1250'`. Second case: `scale: "1:500"` against a `1250` sheet → problem containing
`contradicts`. Third case: `scale:` absent → `result.ok is True`.

**6. `test_tampered_sheet_metadata_fails_every_derivable_check`**
Setup: render a valid A3 1:1250 sheet, then rewrite the emitted `<metadata>` JSON in place — one
mutation per sub-case: `scale.denominator → 1157`; `scale_bar.total_mm → 43.216`;
`viewport.required_mm → [200, 180]`; root `width="420mm" → width="1600"`.
Expect: each produces exactly the V3 / V6 / V4 / V1 problem, matched on its message fragment.

**7. `test_extent_that_does_not_fit_fails_with_the_smallest_scale_that_would`**
Setup: A3 landscape, extent `310 × 270` m, `scale: 500`.
Expect: `SheetError` whose message contains `620.000 × 540.000 mm`, `400.000 × 277.000 mm` and
`1:1000`. Nothing is rendered and no file is written.

**8. `test_on_overflow_fit_resolves_to_a_round_scale_and_says_so`**
Same setup with `on_overflow: fit`.
Expect: `sheet.viewport.scale.denominator == 1000`; `sheet.scale_text == "1:1000"`; the resolution
warning is present and names both `1:500` and `1:1000`; the emitted metadata records `1000` and
never the string `fit`.

**9. `test_fit_scale_picks_the_smallest_preferred_denominator_that_fits`**
A1 landscape (frame `821 × 574`), extent `160 × 100` m, `scale: fit`.
Expect: `denominator == 200` (1:100 needs `1600 × 1000` mm — too big; 1:200 needs `800 × 500` mm —
fits). Emitted `scale.text == "1:200"`. And with extent `2000 × 1000` m the search resolves to
`1:2500` (`800 × 400` mm — 1:2000 would need `1000 × 500` mm, wider than the `821` mm frame), not to
an unrounded value.

**10. `test_a_hand_spaced_literal_scale_bar_label_is_rejected`** *(reproduces the real defect)*
Setup: render a valid A3 1:1250 sheet; then splice `svg_text(120, 250, "0                50 m")`
**inside** the `<g class="scale-bar">…</g>` group of the emitted SVG, exactly as
`basin-site/source.py:207-208` puts it there.
Action: `validate_svg(path)`.
Expect: `ok is False`; a problem containing `not a derived tick label` and the literal
`0                50 m`. Also assert `inspect.signature(sheet_scale_bar).parameters` has **no**
`label` key, and that the un-spliced sheet validates clean.

**11. `test_north_arrow_bearing_is_derived_from_rotation_and_declared_north`**
Cases `(rotation_deg, model_bearing_deg) → paper_deg`: `(0, 0) → 0.0`; `(40.25, 0) → 40.25`;
`(0, 12.5) → 347.5`; `(90, 90) → 0.0`; `(-10, 0) → 350.0`.
Expect: metadata `north.paper_deg` equals the expectation to 1e-9; the emitted
`<g class="north-arrow" transform="rotate(...)">` carries the same angle to 1e-3; the `N` glyph
carries a counter-rotation of the same magnitude; V11 passes. With `sheet.north` absent, no
`class="north-arrow"` group is emitted and V11 does not run.

**12. `test_scale_bar_does_not_rotate_with_the_viewport`**
Two sheets identical but for `rotation_deg` 0 and 40.25.
Expect: the `<g class="scale-bar">` substring is byte-identical between them, and
`data-tdfa-bar` reports the same `total_mm`.

**13. `test_reservations_cut_the_frame_in_declared_order`**
A1 landscape, margins 10, `reserve: [{title-block, bottom, 60}, {legend, right, 80}]`.
Expect: `regions["title-block"] == SheetFrame(10.0, 524.0, 821.0, 60.0)`;
`regions["legend"] == SheetFrame(751.0, 10.0, 80.0, 514.0)`;
`viewport.frame == SheetFrame(10.0, 10.0, 741.0, 514.0)`. Reversing the list gives a *different*,
also-asserted result — `regions["legend"] == SheetFrame(751.0, 10.0, 80.0, 574.0)` and
`regions["title-block"] == SheetFrame(10.0, 524.0, 741.0, 60.0)`, same final viewport frame —
proving order is honoured, not normalised.

**14. `test_a_drawing_without_a_sheet_block_gains_no_new_checks`** *(second parity test)*
Setup: `drawings/example/simple-section/` as committed.
Expect: `validate_target(dir)` returns the same `problems` and the same `checked` list as on
`origin/main` (captured as a literal expectation in the test), `warnings == []`, and
`load_sheet_config(meta.yaml) is None`. `read_sheet_metadata(committed_svg_text) is None`.

**15. `test_sheet_metadata_json_is_byte_stable_and_carries_no_provenance`**
Expect: two `metadata_json()` calls on equal `Sheet`s give equal strings; keys sorted; no
whitespace; `json.loads` round-trips; the string contains none of `date`, `time`, `Z`, `commit`,
`/Users`, `version`; `schema == "technical_drawings_for_agents/sheet/1"`.

**16. `test_degenerate_and_hostile_sheet_configs_all_raise_sheeterror`** *(parametrised)*
Each case asserts `pytest.raises(SheetError, match=…)`: missing `size`; `size: A6`; `size: 42`;
`orientation: diagonal`; `margins_mm: -1`; `margins_mm: 500` on A3; `margins_mm: {bogus: 3}`;
missing `viewport`; `extent_m` of length 3; `extent_m: [1, 1, 1, 2]` (zero width);
`extent_m: [2, 0, 1, 1]` (inverted); `extent_m` containing `.inf`; `extent_m` containing `true`;
`scale: 0`; `scale: -200`; `scale: 1250.0`; `scale: "1250"`; `scale: "1:1250"`; `scale: "auto"`;
`rotation_deg: "40"`; `on_overflow: clip`; `align: top-left`; `divisions: 0`;
`scale_bar.length_m: 0`; `scale_bar.length_m: 500` on the A3 1:1250 sheet; `scale_bar.unit: ft`;
`reserve` with a duplicate `name`; `reserve` oversized for the frame; unknown key
`viewport.plot_scale`; `sheet: []`. Every message must name the offending YAML path.

**17. `test_plotscale_parse_accepts_only_canonical_ratios`**
`PlotScale.parse("1:200").denominator == 200`; `"  1:1250  "` accepted. `SheetError` for
`"1:1250 (A3)"`, `"1/200"`, `"200"`, `"1:200.5"`, `"1:0"`, `"1:-5"`, `"NTS"`, `""`.
`PlotScale(200).text == "1:200"`; `PlotScale(200).is_preferred is True`;
`PlotScale(1250).is_preferred is False`.

**18. `test_drawn_extent_beyond_the_viewport_warns_but_does_not_fail`**
Setup: A3 1:1250 sheet; `add_model` a point 40 m outside the declared extent.
Expect: `render()` succeeds; metadata `drawn_extent_mm` exceeds `frame_mm`; `validate_svg` gives
`ok is True` with one warning containing `beyond the viewport frame`; the same target under
`--strict` exits 1.

**19. `test_non_preferred_ratio_warns_and_still_passes`**
A3 1:1250. Expect `ok is True`, one warning containing `not an ISO 5455 preferred ratio` and both
`1:1000` and `1:2000`; `--strict` exits 1. A 1:1000 sheet emits no such warning.

**20. `test_cli_sheet_reports_the_resolved_binding`** *(in `tests/test_sheet_cli.py`)*
`main(["sheet", str(cfg), "--json"])` → exit 0, stdout parses as JSON equal to
`Sheet.metadata_json()`, stderr empty. Non-fitting config → exit 1, stderr starts `error: `, stdout
empty. Missing file → exit 2. A YAML with no `sheet:` block → exit 2 with `has no sheet: block`.
`--check` on a good config → exit 0 with empty stdout. Assert no file is created or modified in
`tmp_path` by any invocation.

**21. `test_cli_validate_strict_promotes_warnings_to_failure`** *(in `tests/test_sheet_cli.py`)*
A directory whose sheet emits exactly one warning: `main(["validate", d])` → 0, stdout has the `OK:`
line and one `warn: ` line; `main(["validate", d, "--strict"])` → 1 with the same text as a problem
on stderr. A clean legacy directory gives identical stdout with and without `--strict`.

**22. `test_the_issued_gate_is_untouched_by_the_sheet_model`**
Setup: `meta.yaml` with a valid `sheet:` block, `status: CONCEPT`, `for_construction: true`.
Expect: `validate_target(dir)` reports the `meta.py:74-79` problem verbatim, `ok is False`, and
`--strict` does not remove it. Assert `DrawingMeta(number="X", title="t", status="CONCEPT",
for_construction=True).validate()` is unchanged by the presence of `sheet:` in `extra`. Assert by
source inspection that `sheet.py` contains none of the identifiers `for_construction`,
`STATUS_ORDER`, `status_key`.

**23. `test_paper_space_title_block_is_refused_until_issue_61`**
`SheetDrawing(sheet=…, title_block={"title": "T"})` → `SheetError` naming `#61`. With
`title_block=None` the sheet renders and contains no `class="title-block"` — and
`validate_svg` therefore reports the *existing* missing-title-block problem
(`validate.py:25`, `:55`), unchanged. The gap is visible, not papered over.

**24. `test_sheet_frame_and_scale_bar_geometry_are_exact_to_a_micron`**
A3 1:1250: assert the `sheet_frame` rect attributes are `x="10.000" y="10.000" width="400.000"
height="277.000"`, and that every segment rect of a 5-division 50 m bar has `width="8.000"`
(`40.000 / 5`), summing to `40.000` to 1e-9 — proving `:.3f` formatting and no drift.

---

## 7. Out of scope

An implementer must **not** do any of the following in this change.

### 7.1 P9/#61 owns the ISO 7200 title block — define the interface, don't build it

`SheetDrawing` must not render a title block (§3.7 item 8). What P1 owes P9, and must provide:

- `Sheet.paper: PaperSize` — `name`, `orientation`, `width_mm`, `height_mm`.
- `Sheet.frame -> SheetFrame` — the drawing frame in paper mm, `+y` down.
- `Sheet.regions: dict[str, SheetFrame]` — reserved rectangles by name; a block declaring
  `reserve: [{name: title-block, edge: bottom, size_mm: 60}]` gets `regions["title-block"]` and may
  lay itself out inside that rectangle at any paper size.
- `Sheet.scale_text: str` — the **derived** canonical `"1:N"` for the SCALE field. P9 must render
  this and must not accept a scale string from `meta.yaml`.
- A rendering contract: P9 supplies
  `render_title_block(frame: SheetFrame, fields: Mapping[str, str]) -> str` emitting millimetre
  geometry carrying `class="title-block"` (so `validate.py:25` keeps passing), and `SheetDrawing`
  calls it last. Until it exists, `title_block` must be `None`.
- `LW_DEFAULT_MM` for provisional stroke widths, to be replaced by P7's table.

### 7.2 Other issues' seams — expose the primitive, stop there

- **P7/#59 (pen table).** Do not define a lineweight, linetype or colour table. `LW_DEFAULT_MM =
  0.25` is furniture-local and provisional; P7 will supply a mm-based resolver that `sheet.py`
  consumes.
- **P5/#57 (legibility).** Do not turn V9 into an error and do not build text/symbol bounding boxes.
  Provide `Viewport.contains()`, `SheetFrame.contains_mm()` and `drawn_extent_mm`; that is the whole
  contribution.
- **P3/#55 (determinism/manifest).** Do not add a manifest, a git commit, a `SOURCE_DATE_EPOCH`
  reading, or a provenance stamp. The sheet metadata is deliberately provenance-free (§3.8) so P3
  owns that surface uncontested.
- **P4/#56 (plot path).** Do not change `render.py`, do not add a PDF page-size argument, do not
  touch backend selection. This change makes the page size *declared*; honouring it in the PDF is
  P4's.
- **P8/#60 (geo).** Do not add raster backdrops, GDAL calls, CRS handling, or grid-vs-true-north
  convergence.
- **P2/#54 (build).** Do not add a build graph, a drawing-set config, or multi-sheet orchestration.
  One `sheet:` block describes one sheet; a set is P2's.
- **#50 (site-GA generator).** Do not write a reusable site-sheet assembler. Do not modify the Basin
  `source.py` — it lives in the vault, not this repo, and its migration is tracked separately (§9.3).

### 7.3 Open follow-up issues to file (do not implement)

1. **Anisotropic viewports / vertical exaggeration** — two scales, two bars, a mandatory
   "VERTICAL EXAGGERATION ×n" annotation, and a validate check that the annotation matches the
   ratio. Needed by hydraulic long-sections (`bfd`'s `profile` view). Blocked on §5.7's isotropy
   decision being deliberately narrow.
2. **Multiple viewports and detail insets on one sheet** — the Basin inset (`source.py:219-241`) is
   a second viewport at a second scale. `Sheet` holds exactly **one** `Viewport` in v1; multiple
   named viewports, each with its own derived scale bar and its own asserted ratio, is the follow-up
   that kills the "≈1:250" defect (§2.6).
3. **Non-A-series and ISO 5457 elongated formats**, plus roll/continuous sizes.
4. **DXF paper space** — emit a DXF `Layout` with the plot scale set, so a drafter opening the DXF
   gets the same sheet. `DxfBuilder` stays model-space metres (§2.8).
5. **`align:` corner/edge options** beyond `center`.
6. **Migrate `isosheet.py` to paper millimetres**, so `bfd`/`pid` schematics print at a declared
   paper size (still NTS — no plot scale). Coupled to P9.

### 7.4 Do not touch

`svg.py`, `isosheet.py`, `render.py`, `ingest.py`, `dxf.py`, `bfd.py`, `pid.py`, `isa.py`,
`components/*`, and every committed artifact under `drawings/*/out/`. Do not remove or change
`svg_scale_bar`'s `label` parameter. Do not migrate any existing `meta.yaml`. Do not rename
`ValidationResult` fields. Do not add a dependency.

### 7.5 Explicitly forbidden

A `--write`, `--fix`, or `--migrate` flag on any command in this change; a tool-side edit of
`meta.yaml`; any code path that can clear a problem raised by `DrawingMeta.validate()`; a
`preserveAspectRatio` scaling trick as a substitute for a real plot scale; an SVG `transform:
scale()` implementation of the model→paper map (§3.1); a silent fallback of any kind.

---

## 8. Open questions (gaps, recorded rather than guessed)

1. **ISO 5457 frame margins.** The standard specifies drawing-sheet frame margins, including a wider
   filing edge, and its values are **not in hand**. The `10 mm` default in §3.2 is an explicitly
   labelled **house default**, not a citation. Once the standard is available, the default should
   become the standard's values per size and the change flagged as behaviour-affecting for any sheet
   that omitted `margins_mm`. Until then a sheet that cares must state its margins.
2. **Grid vs true north.** Should `north.model_bearing_deg` be derivable from the CRS meridian
   convergence (up to ~3° in a UTM zone), and should the arrow then be labelled "GRID N" vs "N"?
   Needs a decision from P8/#60. Not computed here.
3. **Where the sheet block should live long-term.** This spec puts it in `meta.yaml` because that is
   the file `validate` already loads (`validate.py:88-92`). P2/#54's drawing-set config may want to
   own sheet definitions so a set shares one paper size. If so, `load_sheet_config()` gains a second
   source and `meta.yaml` stays valid — no breaking change either way, but P2 should decide before
   many sheets are written.
4. **Furniture dimensions.** The scale-bar height (3 mm), tick overshoot (1.5 mm), label baselines
   and the 18 mm north arrow (§3.5, §3.6) are stated so implementations agree; they are **not**
   sourced from a drafting standard. If ISO 5457/ISO 128 prescribes any of them, they should be
   corrected in one PR with the golden test outputs updated.
5. **Font metrics on paper.** Text sizes are now millimetres, but SVG `font-size` in user units with
   `font-family="monospace"` (`svg.py:211`) gives a renderer-dependent glyph width. Any check that
   text *fits* a millimetre box (P5/#57, P9/#61) needs a real metric source. Out of scope here; noted
   because `svg_title_block`'s `value_w / 5.7` guess (`svg.py:566`) is the same problem in px.
6. **Tick-label collision at fine divisions.** A 10-division bar can overprint its own labels. V7
   checks the label *set*, not their spacing; spacing is P5/#57's collision check.

---

## 9. Risks and migration

### 9.1 Where this could break existing consumers

- **It cannot change legacy output.** `svg.py`/`isosheet.py` are untouched and every new check is
  gated on the metadata element. Tests 1 and 14 are the guards; both must be in the same PR.
- **The one real risk is a *new* consumer using millimetres with pixel defaults** — `svg_line(...,
  stroke_width=1)` inside a `SheetDrawing` is a 1 mm line, roughly 4× the intended weight, and
  `font_size=11` is 11 mm text. This is not hypothetical; it is the most likely first mistake. It is
  mitigated by the docstring/README warning (§3.7), `LW_DEFAULT_MM`, and — properly — by P7/#59's
  table. Consider filing a follow-up for a `sheet.py` linting check that flags stroke widths above
  ~2 mm in a paper-space sheet.
- **`meta.scale` becoming validated** looks like a compatibility risk but is not: V8 runs only when a
  `sheet:` block exists, and no committed `meta.yaml` has one.
- **`ValidationResult` gaining a field** could affect an external caller that constructs it
  positionally with three arguments. Grep confirms the only constructors are in `validate.py`
  (`:47`, `:63`, `:86`) and all use keywords; the field has a default factory. Low risk, stated.
- **`technical_drawings_for_agents sheet` colliding with a future name.** `sheet` is a plausible name for other
  things (P2's drawing set). If P2 wants it, this command should be renamed before either is widely
  used; it is read-only and trivially renameable.

### 9.2 Migration for the repository

None required. No file is migrated in this change. The example drawings keep their pixel canvases and
their free-text `scale:` strings until someone opts them in, which is a separate PR with a
regenerated golden.

### 9.3 Migration for the Basin sheet (the motivating consumer, in the vault, not this repo)

Recorded here so the work is scoped, **not done here**:

1. Its main view becomes `sheet: {size: A3, orientation: landscape, viewport: {extent_m: [788392, 322125, 788702, 322395], scale: 1250}}` — which **fits** (`248 × 216` mm inside a `400 × 277` mm frame,
   test 3), so the claimed scale becomes true rather than being abandoned.
2. `scale="1:1250 (A3)"` (`source.py:113`) is deleted; the title block takes `Sheet.scale_text`.
3. `label="0                50 m"` (`source.py:207-208`) is deleted; the bar derives its own labels.
4. `svg_north_arrow(vb, …, size_px=46)` (`source.py:209`) becomes `sheet_north_arrow(...)` with a
   declared `north:` block.
5. The inset (`source.py:219-241`) and its literal "≈1:250" (`source.py:226`) **wait for multiple
   viewports** (§7.3 item 2). Until then the honest interim is to delete the invented ratio from the
   caption rather than print a number that is wrong by 86 %.
6. The out-of-band `rsvg-convert` call (`source.py:278-283`) waits for P4/#56; with `width="420mm"`
   on the root, the page it produces will already be A3.

Expect the migrated sheet to look *smaller*: at a true 1:1250 the plan occupies 248 mm of a 400 mm
frame, where today it fills the canvas at ~1:1157. That is the correction, not a regression — and it
is the visible sign that the number on the sheet is now the number on the paper.
