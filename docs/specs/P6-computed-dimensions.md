# P6 — dimensions and clearances computed from tagged features

**Issue:** upstream #58 (absorbs review items 17, 18)
**Status of this document:** implementation specification. No code in this PR.
**Owner:** `senior-engineer`. Implemented twice by Codex agents, judged, then built.
**Depends on / consumed by:** P5 #57 (annotation collision), P10 #62 (stable placement ids),
P1 #53 (paper space), P7 #59 (layer/lineweight table), P3 #55 (deterministic emit).

---

## 0. Hard constraints (restated — these bind the implementer)

1. **Backward compatibility is sacred.** The existing coordinate-taking `svg_dimension_h` /
   `svg_dimension_v` (`src/technical_drawings_for_agents/svg.py:360`, `:381`) must keep working **unchanged, byte for
   byte**. They are not re-implemented on top of the new primitive, not deprecated, not re-signatured.
   A parity test pins their output (§5, test 20).
2. **Never invent a dimension or a spec value.** Every number that reaches a sheet is either computed
   from a declared input (the placements register, a sourced datum, a declared route) or is an
   explicit, visible open question. There is **no field anywhere in this design that lets an author
   type the value that gets displayed.** This is the single most important rule in this PR.
3. **The ISSUED gate is untouchable.** Nothing here writes `meta.yaml`, sets `status`, or touches
   `for_construction`. A fully dimensioned, setting-out-tabled sheet is still `CONCEPT — NOT FOR
   CONSTRUCTION` until the responsible engineer signs it. `DrawingMeta.validate`
   (`src/technical_drawings_for_agents/meta.py:63`) remains the only gate and is not modified.
4. **Determinism: prefer loud failure over a silent no-op.** A dimension that cannot be computed
   raises. It never renders blank, never renders `0.000` as a stand-in for "unknown", never falls back
   to a nearby placement, and never quietly drops itself from the sheet.
5. **House style.** `src/technical_drawings_for_agents/components/layout.py` is the reference pattern: frozen
   dataclasses, **one** module-specific error class, config validated on load with precise messages
   naming the offending key, tests in `tests/test_*.py` named for the behaviour they assert,
   `ruff` line-length 100.

---

## 1. Intent

**What good looks like.** An author declares *what* is to be dimensioned — "the clear gap between raft
`STA-A` and raft `STA-B`", "the overall envelope of the clarifier group across its own bearing", "the
easting offset of `STA-A` from the surveyed point P5", "the run length of the raw-water interconnect"
— and the tool computes *the number* from the same placed geometry the drawing draws, using the same
distance code the layout checks use. The value on the sheet is therefore a **derived fact with a
provenance chain** (editor → register → snap rule → footprint → distance function → text), not a
transcription. Move a placement in QGIS, rebuild, and every dimension, the setting-out table and the
pipe-run lengths change together, with no edit to any dimension declaration. Nothing on the sheet can
disagree with anything else on the sheet, because there is only one source for each number.

**The failure it prevents.** `svg_dimension_h/v` today take literal metre coordinates, so the drawn
geometry and the printed number are two independent facts maintained by hand. The moment a placement
moves, the annotation is silently wrong — and a wrong dimension is worse than a missing one, because
it is *believed*. It gets set out on site. This is the classic way a drawing becomes wrong, and it is
invisible to review: the sheet looks immaculate. A second, quieter instance of the same failure is the
setting-out table: retyped coordinates in a table are a second copy of the register that drifts from
the first. The cure for both is the same — a dimension names *features*, and the number is computed.

Concretely, this unblocks Track A item 1: the dimensioned compound GA for the Basin WTP (raft
clearances, interconnect pipe-run lengths, cable routes) feeding a spatial bill of quantities, and it
closes the last line of `site-plan.yaml`'s own gap register: *"Interconnect pipe-run lengths + cable
routes + clearances — to be dimensioned on the GA (spatial BOQ)."*

---

## 2. Current state (read, with citations)

### 2.1 The annotation primitives take literal coordinates

| Function | Location | Signature | What it takes |
|---|---|---|---|
| `svg_dimension_h` | `src/technical_drawings_for_agents/svg.py:360` | `(vb, real_y, real_x1, real_x2, label, offset_px=25)` | three literal metre coordinates **and a literal label string** |
| `svg_dimension_v` | `src/technical_drawings_for_agents/svg.py:381` | `(vb, real_x, real_y1, real_y2, label, offset_px=25, color=COL_CAD_DIM)` | ditto |
| `svg_leader` | `src/technical_drawings_for_agents/svg.py:402` | `(vb, target, elbow, label_point, label, …)` | three literal metre points + label |
| `svg_centerline` | `src/technical_drawings_for_agents/svg.py:330` | `(vb, real_x1, real_y1, real_x2, real_y2, extension_m=0.0, …)` | two literal metre points |
| `ViewBox` | `src/technical_drawings_for_agents/svg.py:82` | `(real_min_x, real_max_x, real_min_y, real_max_y, svg_width, svg_height, padding=60)` | uniform `scale` (`:95`), `y` inverted (`:107`), `snap_px` (`:118`) |

The critical detail: **`label` is a free string with no relationship to `real_x1`/`real_x2`.**
`svg_dimension_h` draws witness lines at `x1`/`x2` and then prints whatever text it was handed. The
only in-repo caller proves the point — `drawings/example/simple-section/source.py:135`:

```python
dwg.add(svg_dimension_h(vb, 0.0, -b / 2.0, b / 2.0, f"BED {b:g} m", offset_px=42))
```

Here the label happens to be right because `b` is used for both, in one expression. Nothing enforces
that. There are **only two dimension call sites in the whole repo** (both in that example) — so the
blast radius of adding a new path is small, and the compatibility surface to preserve is tiny and
exactly pinnable.

Also relevant: `svg_text` (`:207`) hard-codes `font-family="monospace"` (so text width is estimable),
`_fmt_measure` (`:159`) exists but only for scale-bar tick labels, and `Drawing.add`
(`:670`) filters falsy elements — a renderer that returns `""` for a dimension it could not compute
would be **silently swallowed**, which is precisely the failure mode constraint 4 forbids.

### 2.2 The geometry engine to reuse

`src/technical_drawings_for_agents/components/layout.py` (862 lines) already contains the exact rotated-rectangle
geometry this feature needs. Reuse it; do not re-derive it.

| Symbol | Location | Contract |
|---|---|---|
| `Placement` | `:49` | frozen: `type`, `origin: Point`, `rotation_deg`, `tag: str \| None`, `size_m: tuple \| None`, `properties: dict`; `.label` (`:60`) returns `tag or type` |
| `Layout` | `:80` | frozen: `id`, `crs`, `components_root`, `types`, `placements`, `checks`, `source`, `editor`, `register`, `groups` |
| `Finding` | `:111` | frozen: `severity: "error"\|"warn"`, `check: str`, `message: str`; `__str__` = `"ERROR: check: message"` |
| `load_layout` | `:121` | validates on load, resolves paths relative to the config, raises `LayoutError` with the file name in the message |
| `snap_groups` | `:478` | returns `(Layout, report)`; the **effective** post-snap poses |
| `build_layout` | `:604` | `list[tuple[Placement, PlacedFeature]]` in world metres |
| `check_layout` | `:649` | dispatch over `CHECK_KINDS` (`:36`) |
| `_check_clear_spacing` | `:712` | `gap = _polygon_gap(_footprint(first), _footprint(second))`; fails when `gap + 1e-9 < minimum - tol` |
| `_footprint` | `:776` | rotated-rectangle corners from `size_m` + `rotation_deg`; **raises `LayoutError` when `size_m` is `None`** — "a check that needs one says so rather than assuming a dimension" |
| `_polygon_gap` | `:838` | minimum clear distance between two convex polygons; **returns `0.0` if they overlap** (`:841`) |
| `_point_segment_distance` | `:853` | point-to-segment distance, clamped `t ∈ [0,1]`; computes the projection parameter internally but **does not return it** |
| `_rects_overlap` | `:814` | separating-axis test, exact for rectangles, `1e-9` slack |
| `_project` | `:833` | dot-product extent of a polygon on a unit axis |
| `_angle_delta` | `:807` | smallest angle between undirected bearings, `[0, 90]` |
| `_mean_bearing` | `:557` | circular mean with period 180° |

`components/place.py:14` `PlacedFeature` is frozen with `component`, `role`, `layer`, `tag`,
`source_status`, `hatch`, `kind`, `coords`, `radius`, `text`. `place()` (`:28`) rotates about the local
origin then translates. `components/spec.py:68` `Feature` carries the per-feature `role`, `layer`,
`tag`, `source_status` — so a **component feature** (a nozzle `N1`, a `role: slab`) is separately
addressable from the **placement** that stamped it. `SOURCE_STATUSES` (`spec.py:16`) =
`{verified, sourced, as-received, verify}`.

CLI conventions to match (`components/cli.py:127` `run_layout`): **exit 2** for a load/config error,
**exit 1** for an `error`-severity finding, **exit 0** clean or warnings only; `--warn-only` downgrades;
findings print to `stderr` when any is an error, `stdout` otherwise (`:179`).

### 2.3 The real data this must annotate — and the real join problem

`.../components/basin-wtp/basin.layout.yaml`: `layout.id: BASIN-WTP-SITE`, `crs: EPSG:32630`, nine
types, one `groups:` snap rule (*FA-130 rafts*, `bearing: mean`, `pitch: spec`, `clear_m: 2.0`), and
four checks including `{check: clear-spacing, within: clarifier, min_m: 2.0, tol_m: 0.05}`.

`basin.placements.yaml` / `basin.effective.yaml` (GENERATED, post-snap) hold **10 placements**:

| # | type | tag | `size_m` | note |
|---|---|---|---|---|
| 1 | clarifier | **STA-A** | 8.3 × 5.3 | `snapped_by: FA-130 rafts` |
| 2 | clarifier | **STA-B** | 8.3 × 5.3 | `snapped_by: FA-130 rafts` |
| 3 | big-pump | — | 2.4 × 1.7 | |
| 4 | water-tank-pre-fab-… | — | 1.5 × 1.5 | |
| 5 | transformer | — | 2.8 × 2.0 | |
| 6 | small-pump | — | 1.15 × 0.55 | |
| 7 | panel-slab | — | 2.6 × 0.8 | |
| 8 | dosing-skid | — | 1.6 × 1.0 | one of **three** |
| 9 | dosing-skid | — | 1.6 × 1.0 | |
| 10 | dosing-skid | — | 1.6 × 1.0 | |

**The issue's framing is right and I am confirming it with the numbers, plus one correction and one
addition:**

- **Confirmed:** 8 of 10 placements carry **no tag**. Only the two clarifiers are tag-addressable.
- **Correction to the issue's phrasing.** The issue says the dimension spec references
  "tags/features/placements". At Basin, *tag* and *feature* are not sufficient and *type* is not
  a discriminator either: three placements share `type: dosing-skid` with identical `size_m`. There is
  **no author-visible stable handle at all** for 8 of 10 placements today. Any design that quietly
  falls back to "the *n*th placement of this type" inherits P10 #62's exact defect (register order is
  GeoJSON export order, so a reorder silently re-points every dimension at different equipment). This
  spec therefore **forbids ordinal references** and defines the identity interface it needs from P10
  (§4.1). Until then, dimensioning an untagged placement is a **hard error with an actionable message**
  — not a guess.
- **Addition the issue does not mention:** the current sheet already has this defect in a milder form.
  `drawings/basin-site/source.py` numbers its schedule with `for num, item in enumerate(register, 1)` —
  the marker numbers on the plan **are** register ordinals — and its own sheet note says
  *"8 of 10 placements carry NO tag — cannot be joined to the site-plan equipment register (gap)"*.
  That note is honest, and this spec must not replace honesty with a fabricated key.
  *(That vault file is edited live; cite it by symbol, not by line.)*

`drawings/basin-site/site-plan.yaml` supplies the datum material:
`platform.approved_wtp_point_P5_utm: [788609.589, 322341.151]`, **Z 56.947 m MSL**, sourced to
*"STB-STA-STC-FOUND-001 §2.2 (Geomars DCP P5, approved WTP location)"* (`:59`–`:60`);
`platform.raft.clear_spacing_m: 2.0` sourced to the FA-130 foundation drawing (`:66`);
`meta.vertical_datum: MSL (m)` (`:24`); and `gaps:` (`:178`) which includes *"P5 datum role: … confirm
whether the surveyed P5 is the platform centre, a raft corner, or the DCP hole"*.

**Consequence for the setting-out table, stated bluntly:** the register has **no Z**. `Placement` has
no elevation field; `derive_placements` (`layout.py:384`) reads a 2-D GeoJSON centroid. **P5 is the
only point in the whole dataset with a level.** A setting-out table with an `Z` column therefore
cannot be filled from the model, and inventing one (from the DTM, from the pond design level 55.65, by
copying P5's 56.947 to every row) would violate constraint 2. §3.6 makes the Z source an explicit,
mandatory, author-declared policy whose honest value at Basin today is "not surveyed".

### 2.4 Reference values, computed from the real register

Computed with `layout.py`'s own `_footprint` / `_polygon_gap` / `_project` arithmetic on
`basin.effective.yaml`, for use as spec-side and test-side reference numbers:

| Quantity | Computed | At 3 dp |
|---|---|---|
| `STA-A` ↔ `STA-B` clear gap | `1.9998519771777388` | **2.000** |
| `STA-A` ↔ `STA-B` centre-to-centre | `7.299852053263156` | **7.300** |
| clarifier-group extent along its own bearing (40.252°) | `8.301053300267085` | **8.301** |
| clarifier-group extent across its bearing (130.252°) | `12.599851977312937` | **12.600** |
| P5 → `STA-A` easting component | `-6.0389999999897555` | **6.039 W** |
| P5 → `STA-A` northing component | `+5.992000000085682` | **5.992 N** |
| P5 → `STA-A` direct | `8.507266599848807` | **8.507** |

Note the first row: the snap rule targets exactly 2.0 m, and the computed value is **1.99985 m** —
1.5 mm short — because the effective register is rounded to 3 dp per axis (`cli.py:242`) before the
footprint is rebuilt. That is real, it is not noise to be hidden, and it sets the default expectation
tolerance in §3.3. It is also the cleanest possible illustration of why a declared spec value may
never be printed in place of a computed one.

---

## 3. Design

### 3.0 Where the code goes

| File | Change |
|---|---|
| `src/technical_drawings_for_agents/components/dimensions.py` | **new** — the whole semantic layer: schema loading, reference resolution, measurement, findings, setting-out rows. Sits beside `layout.py` because a dimension is a fact *about a placed layout*. |
| `src/technical_drawings_for_agents/svg.py` | **additive only** — `svg_dimension_between`, `svg_dimension_leader_note`, `svg_setting_out_table`. Pixels live here; semantics do not. `svg_dimension_h/v` untouched. |
| `src/technical_drawings_for_agents/components/layout.py` | **additive only** — two public delegating wrappers, `footprint()` and `polygon_gap()` (§3.4). No behaviour change. |
| `src/technical_drawings_for_agents/components/cli.py` | `add_dimensions_parser` + `run_dimensions`, mirroring `add_layout_parser` / `run_layout`. |
| `src/technical_drawings_for_agents/cli.py` | register the new subparser in `build_parser` (one line, next to `add_layout_parser(sub)` at `:223`). |
| `src/technical_drawings_for_agents/components/__init__.py` | export the new public names. |
| `src/technical_drawings_for_agents/components/examples/site_dimensions.yaml` | **new** neutral worked example beside `site_layout.yaml`. |
| `tests/test_dimensions.py` | **new** — §5. |
| `README.md` | a `## Dimensions and setting-out (technical_drawings_for_agents dimensions)` section. |

`cli.py` and `components/cli.py` are also touched by sibling PRs (P2 #54 in particular). Keep the diff
to those two files to the minimum shown above so the merge is mechanical.

### 3.1 The `dimensions:` file — full schema

One YAML file per drawing, named `<drawing>.dimensions.yaml`, git-tracked, sitting beside the sheet
source. Unknown keys are **rejected** at every level (mirroring `spec.py:536` `_reject_unknown`) — a
typo must not be a silent default.

```yaml
# STA-SITE-GA-001.dimensions.yaml — what to dimension. Never how much it measures.
dimension_set:
  id: STA-SITE-GA-001-DIMS
  layout: ../../components/basin-wtp/basin.layout.yaml
  snap: effective

defaults:
  decimals: 3
  units_suffix: " m"
  offset_px: 25.0
  witness_gap_px: 3.0
  witness_over_px: 4.0
  text_gap_px: 4.0
  arrow_px: 5.0
  expect_tol_m: 0.002
  on_zero: error
  parallel_tol_deg: 1.0

datums:
  - name: P5
    point: [788609.589, 322341.151]
    z: 56.947
    source: "STB-STA-STC-FOUND-001 §2.2 (Geomars DCP P5, approved WTP location)"
    status: sourced

naming:
  - {name: W-D3, where: {type: dosing-skid, properties: {duty: hypochlorite}}}

routes:
  - name: R-RAW-A
    kind: pipe
    points: [{datum: P5}, {placement: STA-A}]
    attributes: {dn: 200, service: raw water}

dimensions:
  - id: DIM-001
    kind: clearance
    from: {placement: STA-A}
    to: {placement: STA-B}
    label: CLEAR
    expect_m: 2.0
    expect_source: "FA-130 foundation slab dwg (site-plan.yaml platform.raft.clear_spacing_m)"
  - id: DIM-010
    kind: envelope
    of: {type: clarifier}
    axis: principal-cross
  - id: DIM-020
    kind: offset
    from: {datum: P5}
    to: {placement: STA-A}
    axis: easting
  - id: DIM-030
    kind: chainage
    route: R-RAW-A

setting_out:
  include: [placements, datums]
  of: {all: true}
  z: {source: none}
  note: >-
    Z NOT SURVEYED per placement. Only the DCP point P5 carries a level
    (56.947 m MSL, FOUND-001 §2.2). Platform level survey is an open gap.
  order: id
  decimals: 3
```

#### `dimension_set` (required mapping)

| Key | Type | Default | Validation |
|---|---|---|---|
| `id` | str | — **required** | non-empty after strip; used in output filenames and provenance |
| `layout` | str path | — required unless `--layout` given | resolved **relative to this file**; must be a readable file; loaded via `load_layout` |
| `snap` | enum `effective` \| `as-placed` | `effective` | `effective` applies `snap_groups` before measuring; `as-placed` does not |

`snap: effective` is the default because the sheet draws post-snap geometry (`source.py:61` reads
`basin.effective.yaml`). Measuring pre-snap poses while drawing post-snap geometry is the same class of
bug this PR exists to kill; `as-placed` exists only to mirror `layout --no-snap` for review, and when
used it **must** be stamped in the output header and every measurement's provenance.

#### `defaults` (optional mapping — every key also settable per dimension)

| Key | Type | Default | Validation |
|---|---|---|---|
| `decimals` | int | `3` | `0 ≤ n ≤ 4`. > 4 is rejected: the register itself is 3 dp, so a 4th decimal is already noise and a 5th is a lie about precision |
| `units_suffix` | str | `" m"` | any string; `""` allowed |
| `offset_px` | float | `25.0` | `> 0`; matches the existing `svg_dimension_h` default |
| `witness_gap_px` | float | `3.0` | `≥ 0` |
| `witness_over_px` | float | `4.0` | `≥ 0` |
| `text_gap_px` | float | `4.0` | `≥ 0` |
| `arrow_px` | float | `5.0` | `> 0`; matches the existing arrowhead size |
| `expect_tol_m` | float | `0.002` | `> 0` (justified in §3.3) |
| `on_zero` | enum `error` \| `leader` | `error` | §4.6 |
| `parallel_tol_deg` | float | `1.0` | `> 0`; used by `axis: principal*` |

All `*_px` names are deliberate: P1 #53 will introduce mm-on-paper, and keeping the suffix makes that
migration a mechanical rename plus a conversion, with no ambiguity about which units a field is in
today.

#### `datums` (optional list) — **the only place a literal coordinate may enter**

| Key | Type | Default | Validation |
|---|---|---|---|
| `name` | str | required | non-empty, unique across `datums`, and must not collide with any placement key (§4.1) |
| `point` | `[E, N]` | required | two numbers (`bool` rejected, as `layout._coord` does) |
| `z` | float | `None` | number if present |
| `source` | str | **required** | non-empty. A datum with no cited source is rejected — this is the invention-risky construct, so it carries the same rule as `ingest`'s `add_text` note (`README.md:384`) |
| `status` | enum | `verify` | one of `SOURCE_STATUSES` from `spec.py:16`, reusing that vocabulary rather than inventing a second one |

#### `naming` (optional list) — author-declared identity bridge

| Key | Type | Validation |
|---|---|---|
| `name` | str | non-empty, unique, must not collide with any tag, id or datum name |
| `where` | mapping | keys limited to `type` (str) and `properties` (mapping of register property → exact value). **Position keys are rejected** (`origin_utm`, `size_m`, `rotation_deg`) — see §4.1 |

`where` must match **exactly one** placement. Zero matches and two-or-more matches are both hard
errors, and the message lists how many matched and the `type`/`properties` of the candidates.

#### `routes` (optional list)

| Key | Type | Default | Validation |
|---|---|---|---|
| `name` | str | required | non-empty, unique |
| `kind` | str | required | free vocabulary (`pipe`, `cable`, `duct`, …); required so the BOQ consumer can group without guessing |
| `points` | list of `Ref` | required | ≥ 2 entries; each is a `Ref` (below); consecutive resolved points must differ by > `1e-6` m |
| `attributes` | mapping | `{}` | opaque pass-through; not read by the renderer, emitted verbatim in the measurement register (§6) |

**There is no auto-routing.** A route is the polyline through the points the author listed, in order.
An orthogonal or shortest-path router would invent geometry that no source states, and its length would
then be a fabricated quantity feeding a BOQ.

#### `Ref` — a reference to one point or one footprint

A `Ref` is a mapping with **exactly one** selector key (more than one, or none, is a hard error naming
the keys found):

| Form | Resolves to | Notes |
|---|---|---|
| `{placement: <key>}` | the `Placement` whose key is `<key>` | key = stable id, tag, or `naming` name (§4.1). Point = `placement.origin`; footprint = `footprint(placement)` |
| `{feature: {placement: <key>, role: <r>, tag: <t>, component: <c>}}` | one `PlacedFeature` from `build_layout` | `role`/`tag`/`component` are optional filters; must select **exactly one** feature or it is an error listing the matches. Point = the feature's coords (a `point`/`circle`/`label`) or its centroid (a `polygon`); footprint = the polygon itself |
| `{datum: <name>}` | a `Datum` | point only; no footprint |
| `{station: <spec>}` | a point on a route (chainage only) | `start`, `end`, `vertex:<n>` (0-based), or `project:<Ref>` |

#### `dimensions` (required non-empty list)

Common keys, all kinds:

| Key | Type | Default | Validation |
|---|---|---|---|
| `id` | str | required | non-empty, **unique across the file**; appears in every finding, in the SVG element `id`, and in the measurement register |
| `kind` | enum | required | `clearance` \| `centres` \| `envelope` \| `offset` \| `chainage` |
| `label` | str | `""` | prefix printed before the value. **Rejected if it matches `\d[.,]\d`** (§4.4) |
| `expect_m` | float | `None` | when present, `expect_source` becomes required |
| `expect_source` | str | required iff `expect_m` present | non-empty citation |
| `expect_tol_m` | float | from `defaults` | `> 0` |
| `severity` | enum `error` \| `warn` | `error` | severity of an expectation mismatch finding; reuses `layout._severity`'s vocabulary |
| `decimals`, `units_suffix`, `offset_px`, `witness_gap_px`, `witness_over_px`, `text_gap_px`, `arrow_px`, `on_zero` | | from `defaults` | as above |
| `text` | mapping | `{}` | `side`: `above`\|`below` (default `above`, relative to the dimension direction); `along_px`: float offset along the dimension line (default `0.0`, i.e. midpoint); `rotate`: `auto`\|`none` (default `auto`); `leader`: bool (default `false`) — §4.5 |
| `layer` | str | `DIM` | carried into the emitted element metadata for P7 #59; no rendering effect yet |

Per-kind keys:

| kind | keys | meaning |
|---|---|---|
| `clearance` | `from: Ref`, `to: Ref` (both required, both must resolve to a **footprint**) | minimum clear distance between two footprints |
| `centres` | `from: Ref`, `to: Ref` (points) | straight-line distance between two points |
| `envelope` | `of: Selector` (required), `axis` (required) | overall extent of every footprint in the selection, projected on `axis` |
| `offset` | `from: Ref`, `to: Ref` (points), `axis` (required) | signed component of `to − from` along `axis` |
| `chainage` | `route: <name>` (required), `from: Ref` (default `{station: start}`), `to: Ref` (default `{station: end}`) | path length along the route between two stations |

`Selector` (for `envelope`, `setting_out.of`) is a mapping with exactly one of:
`{all: true}`, `{type: <t>}`, `{types: [<t>, …]}`, `{placements: [<key>, …]}`. Unknown type names are
rejected against `layout.types`, exactly as `layout._selected` (`:675`) does. An **empty selection is a
hard error** — never an empty extent, never `0.000`.

`axis` grammar (a string):

| Value | Meaning |
|---|---|
| `easting` | unit `(1, 0)` in the layout CRS |
| `northing` | unit `(0, 1)` |
| `principal` | the common bearing of the selection (`_mean_bearing`), **error** if any member deviates by more than `parallel_tol_deg` (via `_angle_delta`) |
| `principal-cross` | `principal` + 90° |
| `direct` | `offset` only: the straight-line direction `to − from` (magnitude of the vector) |
| `bearing:<deg>` | an explicit bearing in degrees CCW from east. **Allowed but discouraged**, and the loader emits a `warn` finding: a bearing typed here duplicates a fact that lives in the register and will drift from it (§4.9) |

### 3.2 Public Python API (exact signatures)

```python
# src/technical_drawings_for_agents/components/dimensions.py
"""Dimensions and setting-out computed from placed layout geometry.

A dimension declares WHAT to measure — two tags, a group, a datum, a route —
and this module computes the value from the same placed geometry the sheet
draws, with the same distance code the layout checks use. There is no field
anywhere in the schema that supplies the displayed number.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, Iterable, Literal

from .layout import (
    Finding, Layout, Placement, Severity,
    footprint, polygon_gap,          # public wrappers added in layout.py (§3.4)
    load_layout, snap_groups, build_layout,
    _angle_delta, _mean_bearing, _point_segment_distance, _rects_overlap,
)
from .place import PlacedFeature
from .spec import Point, SOURCE_STATUSES

DIMENSION_KINDS = {"clearance", "centres", "envelope", "offset", "chainage"}
AXIS_WORDS = {"easting", "northing", "principal", "principal-cross", "direct"}
ZERO_EPS_M = 1e-6            # below this a dimension line cannot be drawn honestly
WITNESS_TOL_M = 1e-9         # drawn-length vs computed-value invariant
TEXT_WIDTH_PER_CHAR = 0.60   # monospace advance / font_size — ESTIMATE, P5 #57 owns refining it


class DimensionError(ValueError):
    """Raised when a dimensions file, a reference, or a measurement is invalid."""
```

Frozen dataclasses:

```python
@dataclass(frozen=True)
class Datum:
    name: str
    point: Point
    source: str
    z: float | None = None
    status: str = "verify"


@dataclass(frozen=True)
class Ref:
    """A resolved-at-load reference. Exactly one selector is populated."""
    kind: Literal["placement", "feature", "datum", "station"]
    key: str | None = None
    filters: dict[str, str] = field(default_factory=dict)
    station: str | None = None
    inner: "Ref | None" = None            # station: project:<Ref>

    def describe(self) -> str: ...        # e.g. "placement 'STA-A'" — used in every message


@dataclass(frozen=True)
class Route:
    name: str
    kind: str
    points: tuple[Ref, ...]
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Style:
    decimals: int = 3
    units_suffix: str = " m"
    offset_px: float = 25.0
    witness_gap_px: float = 3.0
    witness_over_px: float = 4.0
    text_gap_px: float = 4.0
    arrow_px: float = 5.0
    expect_tol_m: float = 0.002
    on_zero: Literal["error", "leader"] = "error"
    parallel_tol_deg: float = 1.0
    text_side: Literal["above", "below"] = "above"
    text_along_px: float = 0.0
    text_rotate: Literal["auto", "none"] = "auto"
    text_leader: bool = False
    layer: str = "DIM"


@dataclass(frozen=True)
class DimensionSpec:
    id: str
    kind: str
    style: Style
    from_ref: Ref | None = None
    to_ref: Ref | None = None
    of: dict[str, Any] | None = None
    axis: str | None = None
    route: str | None = None
    label: str = ""
    expect_m: float | None = None
    expect_source: str | None = None
    severity: Severity = "error"


@dataclass(frozen=True)
class SettingOutSpec:
    include: tuple[str, ...] = ("placements",)
    of: dict[str, Any] = field(default_factory=lambda: {"all": True})
    z_source: Literal["none", "property", "datum"] = "none"
    z_property: str | None = None
    z_datum: str | None = None
    note: str | None = None
    vertical_datum: str | None = None
    order: Literal["id", "register", "northing"] = "id"
    decimals: int = 3


@dataclass(frozen=True)
class DimensionSet:
    id: str
    source: Path
    layout_path: Path
    snap: Literal["effective", "as-placed"]
    defaults: Style
    datums: tuple[Datum, ...]
    naming: tuple[tuple[str, dict[str, Any]], ...]
    routes: tuple[Route, ...]
    dimensions: tuple[DimensionSpec, ...]
    setting_out: SettingOutSpec | None = None


@dataclass(frozen=True)
class Measurement:
    """One computed dimension: the value, the geometry that proves it, the text."""
    spec: DimensionSpec
    value_m: float                        # signed for `offset`; otherwise >= 0
    anchor_a: Point                       # world metres — witness point A
    anchor_b: Point                       # world metres — witness point B
    text: str                             # exactly what is printed
    path: tuple[Point, ...] = ()           # chainage: the measured route polyline
    direction_token: str = ""             # "E"/"W"/"N"/"S" for axis-signed offsets
    witness_mode: Literal["edge", "vertex", "axis", "point", "path"] = "point"
    provenance: dict[str, Any] = field(default_factory=dict)

    @property
    def drawn_length_m(self) -> float:
        return math.dist(self.anchor_a, self.anchor_b)


@dataclass(frozen=True)
class AnnotationBox:
    """A text box handed to P5 #57's collision check. P6 never resolves collisions."""
    id: str
    kind: str                             # "dimension-text" | "setting-out-table"
    x: float                              # SVG px, top-left
    y: float
    width: float
    height: float
    rotation_deg: float = 0.0
    text: str = ""
    estimated: bool = True                # width is a monospace estimate, not a measured advance


@dataclass(frozen=True)
class SettingOutRow:
    id: str
    easting: float
    northing: float
    z: float | None
    kind: str                             # "placement" | "datum" | "route-point"
    type_name: str = ""
    source: str = ""
```

Functions:

```python
def load_dimensions(path: str | Path, *, layout_path: str | Path | None = None) -> DimensionSet:
    """Load and fully validate a dimensions file. Raises DimensionError on any defect."""

def resolve(dset: DimensionSet, layout: Layout) -> "Resolver":
    """Build the key -> Placement index and the datum/route indexes. Raises on duplicates."""

def measure(dset: DimensionSet, layout: Layout) -> list[Measurement]:
    """Compute every dimension, in declaration order. Raises DimensionError on a
    reference that cannot be resolved or a geometry that cannot be dimensioned."""

def check_dimensions(dset: DimensionSet, measurements: Iterable[Measurement]) -> list[Finding]:
    """Expectation, zero-gap and axis-duplication findings. Reuses layout.Finding."""

def setting_out_rows(dset: DimensionSet, layout: Layout) -> list[SettingOutRow]:
    """Rows straight from the register/datums. Raises when an id or a declared Z is missing."""

def dump_setting_out(rows: Iterable[SettingOutRow], spec: SettingOutSpec, *,
                     crs: str, fmt: Literal["csv", "yaml"] = "csv") -> str:
    """Serialise the table. The SVG table and this text come from the SAME rows."""

def dump_measurements(dset: DimensionSet, measurements: Iterable[Measurement]) -> str:
    """The machine-readable measurement register (JSON) — the BOQ interface (§6)."""

def placement_key(placement: Placement) -> str:
    """The stable handle: Placement.id if P10 has landed, else tag. Raises otherwise (§4.1)."""

def format_value(value_m: float, *, decimals: int, units_suffix: str = " m",
                 direction_token: str = "") -> str:
    """ROUND_HALF_UP fixed-point formatting. The ONLY permitted value transform (§4.4)."""
```

SVG layer (additive, in `svg.py`):

```python
def svg_dimension_between(vb, p1, p2, label, *, offset_px=25.0, witness_gap_px=3.0,
                          witness_over_px=4.0, text_gap_px=4.0, arrow_px=5.0,
                          side="above", text_along_px=0.0, rotate="auto",
                          color=COL_CAD_DIM, text_fill=COL_CAD_DIM_TEXT,
                          font_size=10, element_id=None) -> tuple[str, dict]:
    """An oblique linear dimension between two real-world points.

    Returns (svg, box) where box describes the placed text for the collision
    check. p1/p2 are real-world (E, N); every *_px value is sheet pixels.
    """

def svg_dimension_leader_note(vb, target, label, *, elbow_px=(18.0, -14.0),
                              run_px=52.0, ...) -> tuple[str, dict]:
    """A leader-and-note annotation (chainage, degenerate-zero, crowded text)."""

def svg_setting_out_table(x, y, header, rows, *, col_px=(84, 96, 96, 72),
                          row_px=13.0, font_size=8, ...) -> tuple[str, dict]:
    """Render a pre-formatted table. Takes STRINGS — it does no arithmetic."""
```

`render_dimensions` lives in `dimensions.py` (it is orchestration, not pixels) and is the single call a
sheet makes:

```python
def render_dimensions(vb, measurements: Iterable[Measurement]
                      ) -> tuple[list[str], list[AnnotationBox]]:
    """SVG elements plus the annotation boxes for P5 #57. Never returns "" for a
    measurement — a measurement that reached here is drawable by construction."""
```

### 3.3 How each value is computed (exact code reuse)

| kind | Computation |
|---|---|
| `clearance` | `a, b = footprint(pa), footprint(pb)`; if `_rects_overlap(a, b)` → **raise** (§4.6); else `value_m = polygon_gap(a, b)`. `polygon_gap` is the *same function object* `_check_clear_spacing` calls (`layout.py:721`) — that is what makes test 1 an identity, not a coincidence |
| `centres` | `value_m = math.dist(point_a, point_b)` |
| `envelope` | `axis_unit = unit vector from axis`; over every member's `footprint()` corners, `lo = min(dot)`, `hi = max(dot)`, `value_m = hi - lo`. The projection arithmetic is identical to `layout._project` (`:833`) and test 14 asserts agreement with it |
| `offset` | `d = (to − from)`; `value_m = d · axis_unit` (**signed**); for `axis: direct`, `value_m = math.dist(from, to)` and there is no sign |
| `chainage` | resolve the two stations to `(segment_index, t)`; `value_m = Σ math.dist` over whole intervening segments plus the two partial segments. The station projection helper `project_onto_route` returns `(index, t, point)`; test 15 asserts `math.dist(query, point) == _point_segment_distance(query, seg_start, seg_end)` for every segment, so the new code is pinned to the module's own point-segment maths |

All arithmetic is float64 in metres. Nothing is rounded before display.

**`expect_tol_m` default `0.002`.** The effective register rounds each origin to 3 dp per axis
(`components/cli.py:242`), i.e. up to ±0.5 mm per coordinate per placement, so a two-placement gap
inherits up to ~1.4 mm of rounding. The real Basin case (§2.4) sits at 1.5 mm from its 2.0 m target,
and a 2 mm tolerance passes it while still catching a real 50 mm placement error. A tighter default
would make the shipped Basin layout fail on rounding alone — a check that cries wolf gets switched off,
which is worse than a 2 mm band. This is documented in the schema table, not buried.

### 3.4 The two additions to `layout.py`

```python
def footprint(placement: Placement) -> list[Point]:
    """Public alias for the placed footprint rectangle (see _footprint)."""
    return _footprint(placement)


def polygon_gap(a: list[Point], b: list[Point]) -> float:
    """Public alias for the minimum clear distance between two convex polygons."""
    return _polygon_gap(a, b)
```

Pure delegation, exported from `components/__init__.py`. **Rationale:** the alternative — importing
`_footprint`/`_polygon_gap` across modules — works but invites a future maintainer to "tidy up" by
writing a local copy, which recreates exactly the divergence this issue is about. Two three-line public
aliases make the shared dependency explicit and reviewable. They are additive, so no existing caller
changes. `_rects_overlap`, `_point_segment_distance`, `_angle_delta`, `_mean_bearing` are imported
privately (they are decision helpers, not the measured value) and their agreement is pinned by tests
14 and 15 rather than by promoting more surface on a file three sibling PRs also touch.

### 3.5 Witness lines, dimension line, leader, text

All offsets are computed in **SVG pixel space**, after `vb.point()`, because `ViewBox.scale` (`:95`) is
uniform but `y` is inverted (`:107`) — doing perpendicular offsets in world metres and then mapping
would flip the "above" side for vertical dimensions. Let `A = vb.point(*anchor_a)`,
`B = vb.point(*anchor_b)`, `u = unit(B − A)` in px, and `n = (u.y, −u.x)` (the "above" normal in
px space, i.e. up-screen for a left-to-right dimension). `s = +1` for `side: above`, `−1` for `below`.

```
              text  (centred, rotated to u, reading L→R)
   ─────────────────────────────────────────  dimension line at  A + s·n·offset_px
   │                                       │
   │  witness (extension) lines            │   from  P + s·n·witness_gap_px
   │                                       │   to    P + s·n·(offset_px + witness_over_px)
   ●───────────── measured length ─────────●   anchors A, B on the geometry
```

- **Witness line**, per anchor `P`: `svg_line(P + s·n·witness_gap_px, P + s·n·(offset_px +
  witness_over_px))`, `stroke_width=0.5`, `COL_CAD_DIM` — the same weight/colour
  `svg_dimension_h` uses (`svg.py:368`), so old and new dimensions look identical on one sheet.
- **Dimension line**: between the two offset anchors, `stroke_width=0.7`, with the **same arrowhead
  polygons** as `svg_dimension_h` (`svg.py:371`–`:374`): a triangle `arrow_px` long and
  `arrow_px/2` half-height, apex on the anchor, rotated to `u`.
- **Short dimensions.** If the dimension line is shorter than `4 · arrow_px` px (default 20 px), the
  arrowheads are drawn **outside**, pointing inward, and the text is placed beyond the `B` end
  (`anchor="start"`), still deterministically. It is never shrunk, never dropped.
- **Text**: baseline at `midpoint + s·n·text_gap_px + u·text_along_px`, `anchor="middle"`,
  `font_size=10`, `COL_CAD_DIM_TEXT` — again matching `svg_dimension_h` (`:377`). With
  `rotate: auto`, rotation is `atan2` of `u` in px space normalised into `(-90°, 90°]` so text always
  reads left-to-right (the existing `svg_dimension_v` hard-codes `rotate=-90`; the new primitive
  derives it). With `rotate: none`, text is horizontal.
- **Leader form** (`text.leader: true`, `kind: chainage`, or the `on_zero: leader` path): a dot at the
  anchor, a kinked leader, and the text at the end — reusing the existing `svg_leader` (`svg.py:402`)
  rather than a second leader implementation.
- **Element ids**: every dimension is wrapped in `<g id="dim-<spec.id>" class="dimension"
  data-kind="…" data-layer="…">` so P5's checks and P7's pen table can address it, and so a diff of two
  SVGs is readable.

Per-kind anchor rules:

| kind | `anchor_a`, `anchor_b` | `witness_mode` |
|---|---|---|
| `clearance` | the two facing points across the gap, per §4.3 | `edge` or `vertex` |
| `centres` | the two origins; a small cross is drawn at each (`arrow_px` half-length, `COL_CAD_CENTER`) | `point` |
| `envelope` | the two extreme corner points achieving `lo`/`hi`, each projected onto the axis line through the selection centroid | `axis` |
| `offset` | `from` point, and `from + value_m · axis_unit` (so the drawn line **is** the measured component, not the hypotenuse) | `axis` |
| `chainage` | `path[0]`, `path[-1]`, but **no dimension line is drawn** — §4.7 | `path` |

**The witness invariant (this is the load-bearing safety net).** For every non-`chainage`
measurement, `render_dimensions` asserts `abs(m.drawn_length_m − abs(m.value_m)) ≤ WITNESS_TOL_M`. If
it fails, it raises `DimensionError`. This makes it structurally impossible to ship a sheet whose
dimension line spans one distance while its text states another — the exact failure in the issue
title. It is an invariant, not a finding, because a violation means a code defect, not a data defect.

### 3.6 The setting-out table

Generated from the register, never retyped. Columns: **`ID`, `E (m)`, `N (m)`, `Z (m)`** — plus a
header line carrying `layout.crs`, the vertical datum, and the `snap` mode.

| Key | Type | Default | Validation |
|---|---|---|---|
| `include` | list of `placements` \| `datums` \| `route-points` | `[placements]` | non-empty, no duplicates |
| `of` | `Selector` | `{all: true}` | as §3.1; empty selection is an error |
| `z` | mapping `{source: none\|property\|datum, …}` | — **required** | see below |
| `note` | str | `None` | **required when `z.source == none`** |
| `vertical_datum` | str | `None` | **required when `z.source != none`** (e.g. `"MSL (m)"`) |
| `order` | `id` \| `register` \| `northing` | `id` | canonical sort; `id` default for diff stability |
| `decimals` | int | `3` | `0 ≤ n ≤ 4` |

**Z policy — the anti-invention rule made structural.** Declaring `setting_out:` without a `z:` key is
a **hard error**: `"setting_out: declare a z source — z: {source: property, property: level_m} | {source: datum, datum: P5} | {source: none} (the register carries no elevation)"`. There is no default,
because every possible default either invents a level or silently omits one.

| `z.source` | Behaviour |
|---|---|
| `none` | every `Z` cell renders the literal token **`NOT SURVEYED`**; `note` is required and printed under the table; `setting_out_rows` sets `z=None`; a `warn` finding `setting-out-z` is emitted so the omission is visible in the build log, not just on paper |
| `property: <name>` | `Z` is read from `placement.properties[<name>]`; **any row missing it is a hard error** listing the offending ids. No partial column |
| `datum: <name>` | every row takes that datum's `z` (a declared platform/formation level); the datum must carry both `z` and `source`, and the datum's `source` is printed in the table note |

At Basin today the only honest declaration is `z: {source: none}` with a note citing P5's 56.947 m and
the open platform-level gap. That is a **stated open question on the face of the drawing** — which is
what constraint 2 requires, and strictly better than a plausible-looking number.

Other rules:

- `id` is `placement_key(placement)` — the *same* identity the dimensions use, so a dimension and a
  table row can never refer to different things by the same name. A placement with no id/tag is a
  **hard error** (§4.1), never a blank or an ordinal.
- `E`/`N` come from `placement.origin` of the **effective** layout when `snap: effective`, formatted
  with `format_value` at `decimals`.
- Datums contribute rows with `kind="datum"` and their `source` in the source column of the sidecar.
- `svg_setting_out_table` takes **already-formatted strings** and does no arithmetic — so the SVG table
  and the CSV sidecar are two renderings of one `list[SettingOutRow]` and cannot diverge.
- The table is one `AnnotationBox` for P5's frame-containment and collision checks.

### 3.7 CLI surface

```
technical_drawings_for_agents dimensions <dimensions.yaml>
    [--layout <layout.yaml>]              # overrides dimension_set.layout
    [--emit svg|csv|yaml|json] [--out FILE]
    [--setting-out FILE]                  # write the setting-out sidecar (fmt from suffix)
    [--check-only]
    [--warn-only]
    [--no-snap]                           # forces snap: as-placed, and stamps it in the output
```

Console output mirrors `run_layout` (`components/cli.py:141`):

```
dimensions STA-SITE-GA-001-DIMS: 4 dimension(s), 1 route(s), 12 setting-out row(s)
  snap: effective (2 placement(s) snapped by 'FA-130 rafts')
  DIM-001  clearance   STA-A -> STA-B          2.000 m   (expect 2.0 ±0.002 — ok)
  DIM-010  envelope    type:clarifier          12.600 m
  DIM-020  offset      P5 -> STA-A (easting)   6.039 m W
  DIM-030  chainage    R-RAW-A                 8.507 m
dimension checks: 1 finding(s)
  WARN: setting-out-z: 10 row(s) have no surveyed level (z source 'none')
```

| Exit | Condition |
|---|---|
| **0** | ran; no `error`-severity findings (or `--warn-only`) |
| **1** | at least one `error`-severity finding (expectation mismatch, `bearing:` duplication when escalated) |
| **2** | load/validation/geometry failure — `DimensionError`, `LayoutError`, `ComponentSpecError`, `OSError`, or `--emit` without `--out` |

Exactly the `run_layout` contract: **2 = I could not compute, 1 = I computed and it is wrong,
0 = clean.** No new codes; a caller that already distinguishes 1 from 2 keeps working.

---

## 4. Behaviour decisions, with rationale

### 4.1 What a dimension references when a placement has no tag

**Decision.** A placement is referenced by exactly one of: (a) a **stable id** (`Placement.id`, P10
#62), (b) its **tag**, (c) an author-declared **`naming` name** bound by a position-independent
predicate. `placement_key()` resolves in that order. **There is no ordinal, index, "nth of type", or
nearest-match fallback.** A dimension or a setting-out row that names an unresolvable placement is a
`DimensionError` whose message names the reference, lists the available keys, and says what to do:

```
DIM-004: no placement keyed 'dosing-skid-2'. Known keys: STA-A, STA-B.
8 of 10 placements have neither a stable id nor a tag and cannot be dimensioned.
Fix by (a) setting the 'tag' attribute in the QGIS placements layer and re-running
`technical_drawings_for_agents layout --from-geojson`, or (b) declaring a naming: entry with a
property predicate, or (c) landing stable placement ids (#62).
```

**Rejected: ordinal / index references** (`{placement_index: 8}`, `{type: dosing-skid, nth: 2}`).
Register order is GeoJSON export order (`derive_placements`, `layout.py:405` iterates `features` as
given). #62 exists precisely because that order is unstable. An ordinal reference would mean that
reordering features in QGIS silently re-points a dimension at different equipment while the sheet still
renders and still validates — a wrong number that looks right. That is the exact defect class this PR
exists to eliminate, so it must not be re-introduced as a convenience.

**Rejected: nearest-placement matching** (`{near: [E, N]}`). It is position-based, so it breaks the
moment the thing moves — the opposite of the required behaviour — and its failure mode is silent
re-binding to a neighbour.

**Rejected: coordinate predicates in `naming`.** Tempting for Basin's three identical dosing skids
(`where: {origin_utm: [788662.616, 322262.035]}`), and explicitly forbidden: acceptance test 2 requires
that moving a placement changes the dimension **with no spec edit**, and a coordinate binding breaks on
exactly that move. `naming.where` therefore accepts `type` and register `properties` only, and rejects
`origin_utm` / `size_m` / `rotation_deg` with a message pointing at the tag route.

**Interface required from P10 #62** (state it here so P10 can be implemented against it):

1. `Placement` gains `id: str | None` as a field, populated from the register's `id:` key.
2. Ids are **unique** within a layout, **stable** across GeoJSON feature reordering, and **stable**
   across re-derivation of an unchanged placement.
3. The id is **persisted in the register** (so it is a git-tracked fact, not a runtime hash).
4. `Layout` gains no required new method — `placement_key()` reads `getattr(placement, "id", None)`,
   so P6 works before *and* after P10 with **no P6 code change**, and the day P10 lands all 10 Basin
   placements become referenceable.

**Consequence, accepted deliberately.** Today only `STA-A` and `STA-B` can be dimensioned by
reference. The first real dimensioned Basin GA will therefore carry raft clearances and P5 offsets but
not (say) pump-to-skid clearances, until tags are added in the editor or #62 lands. That is the correct
outcome: the tool tells the truth about what it can and cannot identify, and the pressure lands on
fixing identity rather than on faking it. The current sheet's own note ("8 of 10 placements carry NO
tag") already says this out loud; this design keeps that honesty instead of papering over it.

### 4.2 Only one place a raw coordinate may enter

**Decision.** Literal coordinates are legal **only** inside a `datums:` entry, and a datum requires a
non-empty `source:`. A dimension itself can never carry `[E, N]`.

**Why.** The whole defect is "a number typed next to geometry it does not describe". If a dimension
could take a coordinate, the old failure returns through the new door. Confining coordinates to named,
sourced datums means: every literal in the file has a citation, every literal has a name that appears
on the sheet, and `git diff` on `datums:` is a review of survey inputs. P5 enters this way, carrying
`STB-STA-STC-FOUND-001 §2.2` — which is exactly how a surveyed control point *should* enter a drawing.

**Rejected:** allowing `{point: [E, N]}` as a general `Ref` with an optional note. An optional
provenance field is an unenforced one; the `ingest` corrections overlay already learned this and made
`add_text`'s note mandatory (`README.md:384`). Same rule, same reason.

### 4.3 Which edges "edge-to-edge" means for rotated footprints, and where the witness lines go

`_polygon_gap` returns a **scalar**. A dimension needs two **points**. For two parallel rectangles the
minimum-distance point pair is not unique — any pair across the facing overlap achieves it — so a naive
`argmin` picks whichever corner the loop happened to visit first, and the drawn line lands at an
arbitrary end of the gap and moves when the input order changes.

**Decision — a two-branch, deterministic rule, with the scalar always from `polygon_gap`:**

1. **Value first, always.** `value_m = polygon_gap(a, b)`. The witness rule never influences the number.
2. **Direction.** Compute, over the 8 edge normals of the two rectangles (the same axis set
   `_rects_overlap` uses), the maximum projection separation `sep` and the axis achieving it.
3. **Edge branch** — if `abs(sep − value_m) ≤ 1e-9`, the closest features are edge-to-edge (parallel or
   facing). The gap direction *is* that axis. Project both rectangles onto the perpendicular, take the
   **overlap interval** of the two projections, and place both anchors at the **midpoint of that
   overlap** — one on each facing edge. `witness_mode = "edge"`. This is stable under input order and
   under vertex relabelling, and it puts the dimension where a drafter would: centred in the gap.
4. **Vertex branch** — otherwise the closest features are vertex-to-vertex (or vertex-to-edge beyond an
   edge's extent), where no edge normal is the true direction. Re-enumerate exactly as `_polygon_gap`
   does — `for polygon, other in ((a, b), (b, a))`, edges by ascending index, `other`'s points in
   order — and take the first pair within `1e-12` of `value_m`. Tie-break is therefore
   `(polygon_order, edge_index, point_index)`: fully determined by the data, not by float noise.
   `witness_mode = "vertex"`, and the witness lines are drawn perpendicular to the connecting line.
5. **Invariant.** `abs(math.dist(anchor_a, anchor_b) − value_m) ≤ 1e-9`, or raise (§3.5).

**Worked check of the branch split** (also test 5b): 4 × 2 rectangles at rotation 0, centred `(0,0)`
and `(6,4)`. Max edge-normal separation is `2.0` (x: 4−2; y: 3−1), but the true gap is the
corner-to-corner `2√2 = 2.8284271247461903`. `2.0 ≠ 2.828…`, so the vertex branch is taken — and the
drawn line correctly runs corner `(2,1)` → corner `(4,3)`. A single-branch axis rule would have drawn
a 2.828 m label on a 2.0 m line.

**Rejected: "use the nearest pair of parallel edges".** Undefined when nothing is parallel, and for
the Basin rafts (parallel by snap construction) it still leaves the along-edge position free.

**Rejected: centroid-to-centroid direction.** Simple and wrong: for two offset rectangles the centroid
line is not perpendicular to the facing edges, so the drawn line is longer than the gap it labels.

**Rejected: computing the witness pair inside `polygon_gap` and returning both.** That changes a
function three checks depend on, breaking constraint 1's spirit for no gain.

### 4.4 Rounding, displayed precision, and whether the displayed value may differ from the computed one

**Decision.** Displayed text = `format_value(value_m, decimals, units_suffix, direction_token)`, which
does exactly two things, both declared:

1. **Fixed-point rounding to `decimals`**, via
   `Decimal(repr(value_m)).quantize(Decimal(10) ** -decimals, rounding=ROUND_HALF_UP)`.
2. **Sign rendered as a compass token** for `offset` with `axis: easting|northing`: `-6.039` prints as
   `6.039 m W`.

Nothing else. No "nice number" snapping, no unit switching, no per-dimension text override, no
tolerance-based substitution of an intended value.

**May a displayed value ever differ from the computed one? Yes — and only by rounding, and it must be
argued rather than assumed.** Rounding is unavoidable: the computed value is a float64 with ~16
significant digits and a sheet prints 3 decimals. The real question is whether that difference can
mislead. Three guards make it safe:

- **The precision floor is honest.** Max `decimals` is 4, and the *default* is 3, because the register
  itself is 3 dp (`components/cli.py:242`). A dimension can never claim more precision than its input
  has. (`decimals: 4` exists only for a small local detail on an inset sheet.)
- **The rounding mode is pinned and platform-independent.** Python's built-in `round()` is
  round-half-**even** (`round(2.0005, 3) → 2.0` sometimes, `2.001` others depending on the binary
  representation) and `f"{x:.3f}"` inherits the same surprise. Engineers expect half-up. Using
  `Decimal` with `ROUND_HALF_UP` makes the printed string a pure function of the float, identical on
  every platform — which P3 #55's byte-identical-output requirement also needs.
- **The rounded value is never fed back into anything.** Findings and the measurement register carry
  the full-precision `value_m`; only the sheet string is rounded. So a rounded 2.000 can never become
  an input to a later calculation.

**Rejected outright: any author-supplied displayed value.** No `text:` override, no `value:`, no
`display_m:`. This is the whole point of the PR. To make the boundary enforceable rather than
aspirational, `label` (a *prefix*, e.g. `CLEAR`, `DN200`, `3 off`) is **rejected at load if it matches
`\d[.,]\d`** — a decimal number inside a label is the one construct that lets a hand-typed measurement
onto a sheet disguised as a caption. The rejection message points at `expect_m`:

```
DIM-001.label: labels may not contain a decimal number ('CLEAR 2.0 m').
A dimension prints the COMPUTED value; declare a specified value as
expect_m: 2.0 with expect_source, and it will be checked, not printed.
```

**`expect_m` never overrides a computation.** It creates a `Finding` (`check="dimension-expectation"`,
severity from the spec, default `error`) when `abs(value_m − expect_m) > expect_tol_m`. The sheet shows
the **computed** value in that case, and the build fails. Message form:

```
ERROR: dimension-expectation: DIM-001 (STA-A -> STA-B) computed 1.943 m,
expected 2.0 m ±0.002 (FA-130 foundation slab dwg). The sheet shows 1.943 m.
```

This inverts the usual temptation. The spec value is the *thing under test*, not the thing printed —
so "the drawing says 2.0 m because the drawing is supposed to say 2.0 m" becomes impossible to express.

### 4.5 Is text placement automatic, hinted, or explicit?

**Decision: deterministic default + author hints, never automatic relocation.** Default is centred on
the dimension line, `text_gap_px` above it, rotated to read left-to-right. Hints: `text.side`,
`text.along_px`, `text.rotate`, `text.leader`.

**Collision handling: P6 produces evidence, P5 #57 judges, the author decides.** `render_dimensions`
returns `list[AnnotationBox]` alongside the SVG. P5's collision and frame-containment checks consume
those boxes exactly as they consume legend/scale-bar/title-block boxes. P6 **does not** detect or
resolve collisions.

**Rejected: automatic collision avoidance (nudge / auto-flip / auto-leader).** Three reasons.
(1) *Non-locality*: adding an unrelated dimension could shift an existing one, so a one-line data
change produces a diffuse SVG diff — hostile to P3 #55's byte-identical goal and to review. (2) *It
hides crowding*: auto-placement's success is indistinguishable from a sheet that is too busy, and the
right fix for a crowded GA is a bigger scale or an inset, which only a human can choose. (3) *It
duplicates P5*: two collision engines would drift, and P5 is explicitly "the most direct substitution
of deterministic code for LLM eyeballing in the whole review" — it should own the one implementation.

Text-width estimation: `width ≈ TEXT_WIDTH_PER_CHAR · font_size · len(text)` with
`TEXT_WIDTH_PER_CHAR = 0.60`, valid because `svg_text` hard-codes `font-family="monospace"`
(`svg.py:211`). Every `AnnotationBox` carries `estimated=True`, so P5 can tighten the metric (or
measure real advances) without P6 changing, and so no one mistakes the estimate for a measurement.

### 4.6 Geometrically degenerate dimensions

| Situation | Behaviour | Rationale |
|---|---|---|
| **Overlapping footprints**, `clearance` | **Hard error.** `"DIM-001: STA-A and STA-B overlap — there is no clear gap to dimension. `polygon_gap` returns 0.0 for overlap and for contact alike, so a '0.000 m' clearance here would be a lie. Fix the layout (the no-overlap check should have caught this)."` | `_polygon_gap` collapses overlap and contact to the same `0.0` (`layout.py:841`), so a clearance dimension physically cannot distinguish them. Printing `0.000` would state "they touch" when they interpenetrate. Loud failure, and it points at the existing `no-overlap` check |
| **Touching footprints** (gap `0.0`, not overlapping) | Renders `0.000 m`; emits `WARN: dimension-zero`. Anchors come from the edge branch (§4.3), so the "line" is a degenerate point — the renderer switches to the **leader** form so the value is still legible | Abutting slabs are legal geometry and a real 0 mm gap is information. But a zero dimension on a GA is nearly always a modelling slip, so it is flagged |
| **Zero-length `centres`** (coincident origins) | `on_zero: error` (default) → hard error. `on_zero: leader` → leader note `"0.000 m (coincident)"` + `WARN: dimension-zero` | Two placements at the same origin is a data defect. The `leader` escape exists so a deliberate coincidence (an item mounted on another's centre) can be *stated*, never silently blanked |
| **Zero `offset` component** (e.g. `axis: easting` for two points on one easting) | Same `on_zero` policy. `leader` prints `"0.000 m E"` — a legitimate, useful fact | A dimension line of zero length cannot be drawn; a leader note can. The author chooses, and neither choice is silent |
| **`envelope` of an empty selection** | Hard error naming the selector | An empty extent is not `0.000`; it is a selector that matched nothing, i.e. a typo |
| **`envelope` of one placement** | Allowed — that placement's own extent on the axis | Legitimate: the overall size of a single raft |
| **`envelope` with `axis: principal`** and members out of parallel beyond `parallel_tol_deg` | Hard error quoting the worst `_angle_delta` and both bearings | "The group's own bearing" is undefined for a non-parallel group; a mean bearing would produce a foreshortened extent that looks plausible |
| **`size_m is None`** on a placement used by `clearance`/`envelope` | `LayoutError` from `_footprint` (`layout.py:784`), re-raised as `DimensionError` prefixed with the dimension id | The existing message is already right ("regenerate the register with `--from-geojson`"); adding the dimension id makes it actionable |
| **Route with a repeated vertex** or fewer than 2 distinct points | Hard error naming the route and the duplicate index | A zero-length segment makes chainage ambiguous and station projection undefined |
| **Chainage stations out of order** (`from` after `to` along the route) | Hard error | A negative chainage is almost always a reversed reference; silently taking `abs()` would hide it |
| **NaN / inf anywhere in a computed value** | Hard error | Belt and braces: a NaN formats as `"nan m"`, which would print on a sheet |

The general rule, restated: **a measurement that cannot be computed raises; a measurement that is
computable but suspicious renders with a finding.** Nothing renders blank, and nothing silently
disappears from the sheet (which `Drawing.add`'s falsy filter at `svg.py:671` would otherwise let
happen).

### 4.7 Chainage is drawn as a leader note, never as a straight dimension line

A path length along a polyline is not the distance between its endpoints. Drawing a straight dimension
line between the ends of a route and labelling it with the route length would reproduce the issue's
core defect in a new form — a line that spans one distance carrying a number that states another.

**Decision.** `chainage` renders as `svg_leader` attached at the **arc-length midpoint** of the
measured path (deterministic: walk the path to `value_m / 2`), with the text `"<label> <value> m"`.
Optionally (`text.leader: false`) the label is placed inline along the segment containing the
arc-length midpoint, rotated to that segment. The measured path is exposed as `Measurement.path` so the
sheet can draw the route itself; the witness invariant (§3.5) is skipped for `chainage` and replaced by
`abs(sum(segment lengths) − value_m) ≤ 1e-9`.

### 4.8 Measure the same poses the sheet draws

**Decision.** `snap: effective` is the default: `snap_groups` is applied before measuring, matching
what `--emit-register` writes and what `basin-site/source.py` reads (`REGISTER =
LAYOUT_DIR / "basin.effective.yaml"  # EFFECTIVE (post-snap) poses`). `--no-snap` / `snap: as-placed`
is available for review and is **stamped into the output** (console line, SVG comment, and every
`Measurement.provenance["snap"]`) so an as-placed measurement can never be mistaken for the design
value. Mixing (measuring pre-snap while drawing post-snap) is not expressible.

### 4.9 A bearing typed in the dimensions file is a duplicated fact

`axis: bearing:130.252` is supported but the loader emits
`WARN: dimension-axis: DIM-010 axis 'bearing:130.252' duplicates the placement bearing that lives in
the register; prefer 'principal-cross' so the dimension follows the layout when it is re-snapped.`
Prefer `principal` / `principal-cross`, which read the bearing from the register (`_mean_bearing` over
the selection). The Basin bearing is itself a `verify` item adopted from the placement
(`basin.layout.yaml:62`) — re-typing it into a second file is exactly the duplication `site-plan.yaml`
and the register were separated to prevent (`basin.layout.yaml:16`–`:18`). It stays *supported* because
a genuinely externally-specified bearing (a survey baseline) is a legitimate declared input; it stays
*warned* because it is usually a copy.

### 4.10 Findings vocabulary (reusing `layout.Finding`)

| `check` | Default severity | Meaning |
|---|---|---|
| `dimension-expectation` | `error` | computed value outside `expect_m ± expect_tol_m` |
| `dimension-zero` | `warn` | a computed value of 0 rendered as a leader note |
| `dimension-axis` | `warn` | `bearing:<deg>` used where `principal*` would follow the register |
| `setting-out-z` | `warn` | `z.source: none` — rows carry `NOT SURVEYED` |

Reusing `Finding` (not a parallel type) means the CLI prints layout and dimension findings with one
formatter, and a future `build` (P2 #54) aggregates one list.

---

## 5. Acceptance tests

`tests/test_dimensions.py`, pytest, `tmp_path` fixtures, helpers reused from `tests/test_layout.py`
(`_rect`, `_geojson`, `_write_layout`). Every test name states the behaviour it asserts.

1. **`test_clearance_reports_exactly_what_the_clear_spacing_check_computes`**
   *Setup:* layout with two 8.3 × 5.3 placements at `rotation_deg: 40.0`, centres 7.3 m apart across
   the short axis (the fixture from `tests/test_layout.py:337`), tagged `A`/`B`, plus
   `{check: clear-spacing, within: unit, min_m: 2.0}`. Dimensions file with one `clearance` A→B.
   *Action:* `m = measure(...)[0]`; `gap = polygon_gap(footprint(pa), footprint(pb))`.
   *Expected:* `m.value_m == gap` **exactly** (`==`, not `pytest.approx` — it is the same function on
   the same inputs); `gap == pytest.approx(2.0, abs=1e-12)`; `check_layout(layout) == []`; and
   `m.text == "2.000 m"`.

2. **`test_clearance_matches_the_gap_quoted_in_a_failing_clear_spacing_finding`**
   *Setup:* same, centres nudged to 6.8 m and `min_m: 2.0`.
   *Action:* `finding = check_layout(layout)[0]`; `m = measure(...)[0]`.
   *Expected:* `f"{m.value_m:.3f} m"` is a substring of `finding.message` (which reads
   `"… clear gap 1.500 m < required 2 m"`). *The sheet cannot contradict the check, in either
   direction.*

3. **`test_moving_a_placement_changes_the_dimension_text_with_no_spec_edit`**
   *Setup:* as test 1. Record `text_before`. Rewrite **only** the register (centres 7.3 → 8.0 m,
   `size_m` unchanged); the dimensions file bytes are unchanged (assert by hashing it before and after).
   *Action:* reload the layout, `measure` again.
   *Expected:* `text_before == "2.000 m"`, `text_after == "2.700 m"`, and the dimensions-file hash is
   identical.

4. **`test_a_dimension_referencing_an_unknown_tag_is_a_hard_error`**
   *Setup:* dimensions file referencing `{placement: STA-Z}` against a layout with `STA-A`/`STA-B`.
   *Action:* `pytest.raises(DimensionError)` around `measure`.
   *Expected:* exit-path is an exception (not a finding, not a blank); the message contains `STA-Z`
   and both known keys `STA-A`, `STA-B`.

5. **`test_rotated_footprint_clearance_is_correct_for_a_hand_computed_edge_case`**
   *Setup:* two 8.3 × 5.3 rectangles at 40.0°, centres 7.3 m apart perpendicular to the long axis.
   *Expected:* `value_m == pytest.approx(2.0, abs=1e-12)`; `witness_mode == "edge"`;
   `abs(m.drawn_length_m − m.value_m) < 1e-9`; and the two anchors are symmetric about the midpoint of
   the two origins (to `1e-9`) — i.e. the dimension is centred in the gap, not at an arbitrary corner.

5b. **`test_vertex_to_vertex_clearance_uses_the_corner_pair_not_an_edge_normal`**
   *Setup:* 4 × 2 rectangles at rotation 0, centred `(0,0)` and `(6,4)`.
   *Expected:* `value_m == pytest.approx(2.8284271247461903, abs=1e-12)` (`2√2`);
   `witness_mode == "vertex"`; `anchor_a == (2.0, 1.0)`, `anchor_b == (4.0, 3.0)`;
   `m.text == "2.828 m"`. *(The max edge-normal separation here is 2.0 — this test is what stops a
   single-branch implementation from drawing a 2.0 m line labelled 2.828 m.)*

6. **`test_witness_geometry_always_spans_the_value_it_labels`**
   *Setup:* parametrised over rotations `0, 17, 40, 89, 90, 133, 179°` and both branch types.
   *Expected:* for every measurement, `abs(m.drawn_length_m − abs(m.value_m)) < 1e-9`. *(The
   invariant from §3.5, tested rather than trusted.)*

7. **`test_overlapping_footprints_are_a_hard_error_not_a_zero_clearance`**
   *Setup:* the overlap fixture from `tests/test_layout.py:381` (`(0,0)@0°` and `(2,1)@30°`).
   *Expected:* `DimensionError`; message names both labels and the word `overlap`; **`"0.000"` does not
   appear** in it.

8. **`test_touching_footprints_render_zero_with_a_warning_not_silently`**
   *Setup:* two 4 × 2 rectangles at rotation 0, centres exactly 2.0 m apart on x (edges coincident).
   *Expected:* `m.text == "0.000 m"`; `check_dimensions` yields exactly one
   `Finding(severity="warn", check="dimension-zero", …)`; `render_dimensions` returns a non-empty
   element for it.

9. **`test_zero_length_centres_dimension_fails_by_default_and_leaders_when_declared`**
   *Setup:* two placements at the same origin; one dimensions file with default `on_zero`, one with
   `on_zero: leader`.
   *Expected:* default → `DimensionError`; `leader` → `m.text == "0.000 m (coincident)"` plus a
   `dimension-zero` warn finding.

10. **`test_envelope_of_the_clarifier_group_measures_across_its_own_bearing`**
    *Setup:* the real Basin effective poses — `STA-A (788603.55, 322347.143)`,
    `STA-B (788608.266, 322341.571)`, both `40.252°`, `8.3 × 5.3` — with
    `{kind: envelope, of: {type: clarifier}, axis: principal-cross}`.
    *Expected:* `value_m == pytest.approx(12.599851977312937, abs=1e-9)`; `m.text == "12.600 m"`.
    With `axis: principal`: `pytest.approx(8.301053300267085, abs=1e-9)` → `"8.301 m"`.

11. **`test_offset_from_a_sourced_datum_is_signed_and_shows_a_compass_token`**
    *Setup:* datum `P5` at `[788609.589, 322341.151]` with a `source:`; `STA-A` as above;
    `{kind: offset, from: {datum: P5}, to: {placement: STA-A}, axis: easting}`.
    *Expected:* `value_m == pytest.approx(-6.0389999999897555, abs=1e-9)`;
    `m.text == "6.039 m W"`; with `axis: northing` → `"5.992 m N"`; with `axis: direct` →
    `pytest.approx(8.507266599848807, abs=1e-9)` → `"8.507 m"` and `direction_token == ""`.

12. **`test_a_datum_without_a_source_is_rejected_at_load`**
    *Expected:* `DimensionError` naming `datums[0].source`. Same test asserts a dimension carrying a
    literal `point:` is rejected (`Ref` has no `point` selector).

13. **`test_chainage_sums_the_route_and_annotates_with_a_leader_not_a_straight_dimension`**
    *Setup:* route `R-1` through `{placement: STA-A}` → `{placement: STA-B}` (real poses), then a
    three-vertex L-shaped route via a datum.
    *Expected:* two-point route → `pytest.approx(7.299852053263156, abs=1e-9)` → `"7.300 m"`;
    L-shaped route → the sum of the two segment lengths, **strictly greater** than
    `math.dist(first, last)`; `witness_mode == "path"`; the emitted SVG for it contains no
    dimension-line/arrowhead element (assert on the element structure, e.g. `<polygon` absent from that
    group) and does contain the leader dot.

14. **`test_envelope_projection_agrees_with_the_layout_modules_own_projection`**
    *Action:* for a random-but-seeded set of poses, compare the dimension module's axis projection with
    `layout._project(points, axis)`.
    *Expected:* identical to `1e-12` on both bounds. *(Pins the one piece of geometry not literally
    shared.)*

15. **`test_route_station_projection_agrees_with_point_segment_distance`**
    *Action:* for seeded query points and segments, `project_onto_route` → `(i, t, point)`.
    *Expected:* `math.dist(query, point) == pytest.approx(_point_segment_distance(query, s, e), abs=1e-12)`
    and `0.0 <= t <= 1.0`.

16. **`test_setting_out_table_matches_the_register_exactly`**
    *Setup:* layout with tagged placements at known origins; `setting_out: {z: {source: none},
    note: "…"}`.
    *Action:* `rows = setting_out_rows(...)`; `csv = dump_setting_out(rows, …, fmt="csv")`.
    *Expected:* one row per selected placement, `order: id` sorted; for every row
    `(row.easting, row.northing) == layout_placement.origin` **exactly** (no re-rounding of the
    underlying float); every parsed `E`/`N` string equals `format_value(origin, decimals=3)`; every
    `Z` cell is the literal `NOT SURVEYED`; the note is present; and the row ids are exactly the
    `placement_key` values.

17. **`test_setting_out_without_a_declared_z_source_is_a_hard_error`**
    *Expected:* `DimensionError` mentioning `setting_out.z` and listing the three legal sources;
    `z: {source: property, property: level_m}` with one placement missing `level_m` → `DimensionError`
    naming that id; `z: {source: none}` **without** `note` → `DimensionError`.

18. **`test_an_untagged_placement_cannot_reach_the_setting_out_table`**
    *Setup:* the real 10-placement Basin shape — two tagged clarifiers, eight untagged.
    *Expected:* `of: {all: true}` → `DimensionError` naming the count of unkeyed placements and citing
    the tag / `naming` / #62 remedies; `of: {type: clarifier}` → exactly two rows, `STA-A`, `STA-B`.
    *(This is the honesty test: the table is either complete and keyed, or it fails.)*

19. **`test_expect_m_is_checked_never_printed`**
    *Setup:* a real gap of 1.943 m with `expect_m: 2.0`, `expect_source: "…"`, `expect_tol_m: 0.002`.
    *Expected:* `m.text == "1.943 m"` (**not** `2.000`); `check_dimensions` yields one
    `Finding(severity="error", check="dimension-expectation")` whose message contains `1.943`, `2.0`
    and the `expect_source`; the CLI exits **1**. With the gap at `1.9998519771777388` (the real Basin
    value) and the default tolerance → **no finding**, exit **0**.

20. **`test_existing_coordinate_dimensions_are_byte_identical`** — *the backward-compat test*
    *Action:* call `svg_dimension_h(vb, 0.0, -2.0, 2.0, "BED 4 m", offset_px=42)` and
    `svg_dimension_v(vb, 3.0, 0.0, 1.5, "DEPTH 1.5 m", offset_px=30)` on a fixed `ViewBox`.
    *Expected:* the returned strings equal golden literals committed in the test file; the golden
    strings are generated from `main` **before** any change lands. Also assert
    `inspect.signature(svg_dimension_h)` and `…_v` are unchanged (parameter names, order and defaults),
    that `technical_drawings_for_agents.__all__` still exports both, and that
    `drawings/example/simple-section/source.py` still renders (via the existing
    `tests/test_example_smoke.py` path) with **no edit to that file**.

21. **`test_layout_command_output_is_unchanged_by_this_feature`**
    *Action:* run the shipped `examples/site_layout.yaml` through `layout --check-only` and
    `--emit geojson`.
    *Expected:* stdout and the emitted GeoJSON are byte-identical to a golden captured before the
    change. *(`layout.py` gains only two delegating functions; this proves it.)*

22. **`test_two_identical_runs_produce_identical_bytes`**
    *Action:* `dimensions --emit svg --out a.svg` twice into different files.
    *Expected:* identical bytes (no timestamps, no dict-order leakage, `ROUND_HALF_UP` determinism);
    the setting-out CSV likewise. *(Interlocks with P3 #55.)*

23. **`test_duplicate_placement_keys_are_rejected_at_load`**
    *Setup:* a register with two placements tagged `STA-A`.
    *Expected:* `DimensionError` naming the duplicated key and both types. *(A duplicated key makes
    every reference to it ambiguous; only the dimensions loader indexes keys, so `layout` behaviour is
    unaffected.)*

24. **`test_a_label_containing_a_decimal_number_is_rejected`**
    *Expected:* `label: "CLEAR 2.0 m"` → `DimensionError` pointing at `expect_m`;
    `label: "DN200"`, `"Ø200"`, `"3 off"`, `"CLEAR"` all load. *(§4.4's enforceable boundary.)*

25. **`test_naming_binds_by_property_never_by_position`**
    *Setup:* three same-type placements, one carrying `properties: {duty: hypochlorite}`.
    *Expected:* the `naming` entry resolves to exactly that one; a `naming` entry with
    `where: {origin_utm: [...]}` → `DimensionError`; a `where` matching two placements →
    `DimensionError` reporting the count.

26. **`test_render_emits_an_annotation_box_per_dimension_for_the_legibility_checks`**
    *Expected:* `render_dimensions` returns one `AnnotationBox` per measurement plus one for the
    setting-out table; each has positive `width`/`height`, `estimated is True`, and an `id` matching
    its dimension id. *(The P5 #57 hand-off, asserted so it cannot silently disappear.)*

27. **`test_cli_exit_codes_follow_the_layout_command_contract`**
    *Expected:* clean → **0**; expectation mismatch → **1**; `--warn-only` with a mismatch → **0**;
    unknown tag / malformed file / `--emit` without `--out` → **2**; and every failure prints
    `error: …` to **stderr**.

28. **`test_the_dimensions_command_never_touches_drawing_status`**
    *Setup:* a drawing directory with `meta.yaml` at `status: CONCEPT, for_construction: false`.
    *Action:* run the full `dimensions --emit svg --setting-out …` flow.
    *Expected:* `meta.yaml` bytes unchanged; the emitted SVG contains no `ISSUED` token; a
    `DrawingMeta.load(...).validate()` result unchanged. *(Constraint 3, mechanically.)*

29. **`test_shipped_worked_example_measures_and_checks_clean`**
    *Setup:* `examples/site_dimensions.yaml` against `examples/site_layout.yaml`.
    *Expected:* `measure` returns one measurement per declaration, `check_dimensions` returns no
    `error` findings, and the example is exercised by `--emit svg`. *(Mirrors
    `tests/test_layout.py:747`.)*

---

## 6. Out of scope

**Do not build these in this PR.** Each is a separate issue.

| Not now | Why / where instead |
|---|---|
| **Quantities / BOQ emit** | Review item 29, not in this set. **Open a new issue.** Interface it will consume, which P6 *does* provide: `dump_measurements()` writes a JSON register of `{id, kind, value_m, decimals, text, refs (resolved keys), route: {name, kind, attributes}, path, provenance: {layout, snap, register}}`, and `setting_out_rows()` provides `{id, E, N, Z, kind, type_name, source}`. A BOQ pass takes `list[Measurement]`, filters `kind == "chainage"`, groups by `route.attributes` (e.g. `dn`, `service`) and sums `value_m`. **No aggregation, no rates, no quantity tables here.** |
| **Paper space / mm-on-paper / asserted plot scale** | P1 #53. All geometry parameters stay `*_px` so the migration is mechanical. |
| **Annotation collision detection / frame containment / inset fill ratio** | P5 #57. P6 emits `AnnotationBox`es and stops. |
| **Stable placement ids and canonical register sort** | P10 #62. P6 defines the interface (§4.1) and reads `getattr(placement, "id", None)`. Do not mint ids in P6 — a locally derived id that is not persisted in the register is exactly the instability #62 is about. |
| **Layer / lineweight / linetype table** | P7 #59. P6 records `layer` on each dimension as metadata only. |
| **DXF `DIMENSION` entities (associative dims for a drafter)** | Follow-up issue: emit real `ezdxf` dimension entities via `DxfBuilder` so a drafter opening the DXF gets associativity. P6 is SVG-only. |
| **Radial, diameter, angular, ordinate, running/baseline and chain dimensions** | Follow-up issue. P6 ships five linear kinds; the `kind` enum and `DIMENSION_KINDS` set are the extension point. |
| **Automatic dimensioning** ("dimension everything") | Deliberately never. Which dimensions a drawing carries is an engineering judgement about what a builder needs; a tool that decides would produce an unreadable sheet and imply the set was reviewed. |
| **Auto-routing of pipe/cable routes** | Deliberately never. A router invents geometry, and its length would be a fabricated BOQ quantity. Routes are explicit vertex lists. |
| **Tolerance stacking, fit/limit notation, GD&T** | Out of scope for a site GA. |
| **Editing the layout** | `dimensions` is strictly **read-only** on the register, the layout config and `site-plan.yaml`. It never nudges a placement to make a dimension pretty. |
| **Touching `meta.yaml` / status / `for_construction`** | Constraint 3. Not now, not ever, in this command. |
| **Changing `svg_dimension_h/v`** | Constraint 1. Not even a docstring tweak that would change output — the golden test in test 20 will catch it. |
| **A second collision/geometry implementation** | If you find yourself writing a point-in-polygon, a rectangle overlap or a point-segment distance, stop: it exists in `layout.py`. |

---

## 7. Risks and migration

| Risk | Severity | Mitigation |
|---|---|---|
| **`svg.py` change breaks existing sheets.** It is the most-imported module in the package. | High if mishandled | Additive only: three new functions, no edits to existing ones. Test 20 pins `svg_dimension_h/v` output to golden strings and their signatures via `inspect`; test 21 pins the `layout` command's bytes. The only in-repo dimension call sites are `drawings/example/simple-section/source.py:135`–`:136` and they must not be edited. |
| **`layout.py` edit collides with a sibling PR.** P2/P3/P10 also touch the components package. | Medium | The `layout.py` diff is exactly two 3-line delegating functions appended to the geometry-helpers section, plus two names in `components/__init__.py`. No existing line changes, so the merge is trivial in either order. |
| **`cli.py` / `components/cli.py` collide with P2 #54** (which restructures the CLI into a build driver) | Medium | Keep the P6 CLI addition to one `add_dimensions_parser(sub)` line in `build_parser` and one self-contained `run_dimensions` function. If P2 lands first, `run_dimensions` becomes a build step with no change to `dimensions.py`. |
| **The vault sheet must be migrated.** `.../drawings/basin-site/source.py` hand-assembles its markers and schedule and has **no dimensions at all** today; the new sheet content is a vault change, in a different repo, reviewed separately. | Medium | Migration: (1) land this PR; (2) in the vault, add `STA-SITE-GA-001.dimensions.yaml` beside `site-plan.yaml` declaring the raft clearance, the P5 offsets, the clarifier envelope and the interconnect routes; (3) `source.py` calls `measure` + `render_dimensions` + `svg_setting_out_table` instead of adding any literal-coordinate annotation; (4) re-render and re-review; (5) status **stays CONCEPT**. No existing sheet output changes until step 3 is made, so the vault is not broken by the merge. |
| **Only 2 of 10 Basin placements are dimensionable today**, so the first dimensioned GA is partial. | Medium (product, not code) | Accepted and stated on the sheet, exactly as the existing sheet note already states the join gap. The unblock is a tag in the QGIS layer (a one-attribute edit in the tool where identity belongs) or #62. A fabricated key would be worse than a partial drawing. |
| **Rounded text on a 1:1250 sheet gets crowded** — `"12.600 m"` at 10 pt over a 12.6 m raft group. | Medium | Per-dimension `decimals` and `text.*` hints; author guidance in the README (2 dp on the 1:1250 GA, 3 dp on the ~1:250 inset); P5 #57 reports the collision rather than P6 hiding it. Note the guidance is *guidance* — the default is not silently changed by scale, because a scale-dependent default would make the same data print different numbers on two sheets. |
| **`expect_tol_m` default hides a real error** at 2 mm. | Low | 2 mm is derived from register rounding (§3.3), documented in the schema table, and per-dimension overridable. A structural clearance that matters at <2 mm needs a detail sheet, not a 1:1250 GA. |
| **Register rounding means a dimension can never be better than 3 dp**, which may mislead a reader into thinking 1 mm setting-out accuracy is implied. | Low | `decimals` capped at 4 with the reason stated; the setting-out table header carries the CRS and the snap mode; the Z column says `NOT SURVEYED` where it is. The drawing does not claim survey accuracy it does not have. |
| **Someone "tidies up" by inlining a copy of `polygon_gap`** in a later refactor, re-opening the divergence. | Low but permanent | Test 1 asserts `m.value_m == polygon_gap(...)` with `==`, and test 2 asserts the finding text matches. A local copy that drifts by one epsilon fails both. |
| **`Drawing.add` silently drops a falsy element** (`svg.py:671`), so a bug that returns `""` for a dimension would vanish from the sheet unnoticed. | Medium | `render_dimensions` never returns an empty string for a measurement, and the witness invariant raises before that point. Test 8 asserts a non-empty element even for the degenerate `0.000` case. |

---

## Appendix — reference numbers for implementers

From the real `basin.effective.yaml` (post-snap), computed with `layout.py`'s own arithmetic:

```
STA-A  origin (788603.55,  322347.143)  rot 40.252°  size 8.3 x 5.3
STA-B  origin (788608.266, 322341.571)  rot 40.252°  size 8.3 x 5.3
P5     (788609.589, 322341.151)  Z 56.947 m MSL   [FOUND-001 §2.2]

clear gap          1.9998519771777388   -> "2.000 m"
centre-to-centre   7.299852053263156    -> "7.300 m"
extent @  40.252°  8.301053300267085    -> "8.301 m"
extent @ 130.252°  12.599851977312937   -> "12.600 m"
P5 -> STA-A dE    -6.0389999999897555   -> "6.039 m W"
P5 -> STA-A dN    +5.992000000085682    -> "5.992 m N"
P5 -> STA-A direct 8.507266599848807    -> "8.507 m"

synthetic edge branch:   8.3 x 5.3 @ 40.0°, centres 7.3 m across  -> gap 2.0 (1e-15)
synthetic vertex branch: 4 x 2 @ 0°, centres (0,0) and (6,4)       -> gap 2.8284271247461903 = 2*sqrt(2)
                         (max edge-normal separation is 2.0 — the branch test)
```
