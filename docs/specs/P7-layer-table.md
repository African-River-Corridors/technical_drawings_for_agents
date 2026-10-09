# P7 — layer / lineweight / linetype table applied to DXF and PDF pens

Spec for GitHub issue **#59** (milestone *drawing-workflow hardening*). Owner: `senior-engineer`.
This is an implementation specification, not code. Two independent implementations are expected;
anything a reasonable implementer could decide two ways is decided here, with the reason.

All API and DXF claims below were verified against **ezdxf 1.4.4** (the version resolved by
`technical_drawings_for_agents/pyproject.toml`, `ezdxf>=1.1`) and against the checked-in artifacts in
`technical_drawings_for_agents/drawings/example/simple-section/out/`. Where the issue text is wrong, §2 says so.

---

## 0. Hard constraints (restated — these bind the implementer)

1. **Backward compatibility is sacred.** With **no layer table supplied**, DXF and SVG output must be
   **byte-identical to today's**. New behaviour is opt-in and parity-tested. Test 15 and test 16 are
   required, not optional.
2. **Never invent a standard value.** Every ACI number, lineweight and dash length proposed in §3.4 is
   labelled with its basis (common CAD convention / preserved from today's code). Anything that is a
   convention rather than a decision is listed in §8 as an open question for the owner to confirm. Do not
   silently promote a proposal to settled fact.
3. **The ISSUED gate is untouchable.** Nothing in this change reads, writes, infers or influences
   `meta.status`. No layer, pen, colour or check may cause a drawing to become
   `ISSUED FOR CONSTRUCTION`. `WATERMARK` is a layer like any other; it carries no authority.
4. **Determinism: prefer loud failure over a silent no-op.** An undeclared layer, an out-of-range
   lineweight, a custom linetype with no plot scale, a duplicate layer name — all raise. Nothing is
   silently coerced, and nothing is silently skipped.
5. **House style.** Match `technical_drawings_for_agents/src/technical_drawings_for_agents/components/layout.py`: frozen dataclasses, one
   module-specific error class, config validated on load with precise messages that name the file and
   the offending key, tests in `tests/test_*.py` named for the behaviour they assert.

---

## 1. Intent

A drafter opens our DXF and gets a drawing that already looks like a drawing: foundations green and
heavy, annotation thin, hidden work dashed, everything on a named layer that exists in the layer
table with a colour and a lineweight their plot styles can key off. A reviewer opens the PDF and sees
the same weights and the same relative emphasis; a reviewer opening the SVG in a browser sees it too.
Three artifacts, one declared truth — a **layer table** that lives in git as text, is validated on
load, and is applied deterministically to every output. The engineer's judgement ("a foundation
outline is heavier than a nozzle") is recorded once, in a persistent generative object, instead of
being re-decided per sheet in a hard-coded hex string.

The failure it prevents is the one we have today: a DXF whose *entities* reference layers `FDN`,
`EQUIP`, `NOZZLE` and `TEXT` while the DXF's **layer table contains only `0` and `Defpoints`** (§2.3,
verified) — so every entity plots on host defaults; an interchange artifact that looks professional
in a file listing and is close to worthless on a drafter's screen. And its sibling failure: the same
geometry rendered three ways with three unrelated palettes — dark-navy house tokens in SVG
(`style.py:15-22`), `#111111` on white for component SVG (`emit.py:45`), raw ACI on AutoCAD-black in
the DXF-derived PDF (`render.py:93-106`) — so "does it look right?" has no single answer.

---

## 2. Current state (read the code; cited)

### 2.1 `DxfBuilder` — what it writes today

`technical_drawings_for_agents/src/technical_drawings_for_agents/dxf.py`

| Line(s) | What it does |
|---|---|
| `dxf.py:20-29` | `LAYERS` — a module-level dict of **8** names → `{"color": ACI}`: `OUTLINE` 7, `OBJECT` 8, `DIMENSIONS` 3, `CENTRE` 6, `HATCH` 9, `TEXT` 7, `TITLEBLOCK` 7, `WATERMARK` 1. Colour only; no other property is expressible. |
| `dxf.py:39-42` | `ezdxf.new(dxfversion="R2018", setup=True)`; `$INSUNITS = 6` (metres). `setup=True` is load-bearing: it is what populates the linetype and dimstyle tables at all. |
| `dxf.py:44-46` | Loop: `self.doc.layers.add(name, color=attribs.get("color", 7))`. **This is the entire layer-table code in the toolkit.** No `lineweight`, no `linetype`, no `plot`, no `true_color`, no `description`. |
| `dxf.py:49-66` | `line` / `polyline` / `circle` / `text` — each takes `layer="OUTLINE"`-style free strings and passes them straight into `dxfattribs`. **No validation of any kind**; the docstring at `dxf.py:36` explicitly says "any string is accepted". |
| `dxf.py:68-90` | `linear_dim` — native `DIMENSION` on layer `DIMENSIONS`, with dimstyle overrides for metre-scale text/arrows. Calls `dim.render()`, which materialises an anonymous block (see §2.5). |
| `dxf.py:110-151` | `add_block_from_dxf` / `insert` — vendor geometry as blocks; `insert` defaults to layer `OBJECT`. |
| `dxf.py:154-158` | `save` — plain `doc.saveas`. No header or table post-pass. |

Nothing anywhere in the toolkit sets `$LWDISPLAY`, an entity or layer `lineweight`, a non-`Continuous`
`linetype`, a layer `plot` flag, a layer `description`, or a layer `true_color`.

### 2.2 What the shipped DXF actually contains

Read back with ezdxf from `drawings/example/simple-section/out/EXA-CIV-SEC-001.dxf`:

```
'0'          color 7  lineweight -3  linetype Continuous  plot 1
'Defpoints'  color 7  lineweight -3  linetype Continuous  plot 0
'OUTLINE'    color 7  lineweight -3  linetype Continuous  plot 1
'OBJECT'     color 8  lineweight -3  linetype Continuous  plot 1
'DIMENSIONS' color 3  lineweight -3  linetype Continuous  plot 1
'CENTRE'     color 6  ...   'HATCH' color 9 ...  'TEXT' color 7 ...
'TITLEBLOCK' color 7  ...   'WATERMARK' color 1 ...
$LWDISPLAY 0   $INSUNITS 6   $LTSCALE 1.0
modelspace entities: OUTLINE 1, OBJECT 1, DIMENSIONS 1, WATERMARK 1
```

**Correction to the issue.** The issue says DXF export "writes layer **names** only — no ACI colour".
That is wrong for the eight built-in layers: their **ACI colours are written** (`dxf.py:46`). What is
missing for them is `lineweight` (`-3` = DXF *default*, i.e. host-decided), `linetype` (all
`Continuous`), `plot`, `description`, and `$LWDISPLAY` (`0`, so AutoCAD does not even display
lineweights). The issue's claim is *literally* true — and worse than stated — for every layer the
component/project pipeline actually uses: see §2.3.

### 2.3 `components/emit.py` — how layer names travel, and where they are lost

`components/emit.py:104-138` (`to_dxf`): `layer = feature.layer or "0"` (`emit.py:113`), then the string
is handed to the `DxfBuilder` primitives. `Feature.layer` comes from the component YAML with default
`"0"` (`components/spec.py:71`, loader default at `components/spec.py:160`), and is carried through
placement unchanged (`components/place.py:59`).

Verified ezdxf behaviour: **adding an entity on a layer that is not in the layer table does not create
a layer record, and `ezdxf.audit` does not flag it** (0 errors, 0 fixes). A component DXF written today
therefore has entities on `FDN` / `EQUIP` / `NOZZLE` / `TEXT` and a layer table containing exactly
`['0', 'Defpoints']`. AutoCAD invents those layers on load with host defaults. Meanwhile the eight
layers that *do* have records (`OUTLINE`…`WATERMARK`) are largely unused by the component path. The two
halves of the toolkit do not share a layer vocabulary at all.

`components/emit.py:42-101` (`to_svg`): colours are chosen **independently of the layer** —
one `stroke` for everything (default `"#111111"`, `emit.py:45`), one `stroke_width` (default `1.5`,
`emit.py:47`), and the only differentiation is a dash driven by `source_status == "verify"`
(`emit.py:55`, `emit.py:212-215`). `feature.layer` is emitted to GeoJSON properties (`emit.py:144`) but
never influences an SVG pen. `components/cli.py:78` then wraps the SVG on a **white** background,
while `svg.py:630-642` / `style.py:15` default to the dark house background — so the same primitives
are used on two opposite backgrounds with one hard-coded ink.

### 2.4 `style.py`, `svg.py`, `render.py`

* `style.py:15-29` — colour tokens (`COL_CAD_OUTLINE` … `COL_CAD_WATER`), tuned for the dark sheet.
* `style.py:31-36` — line weights **in SVG px**: `LW_OUTLINE_THICK 1.8`, `LW_OUTLINE 1.2`,
  `LW_THIN 0.7`, `LW_DIMENSION 0.7`, `LW_CENTER 0.8`. There is no millimetre anywhere in the toolkit.
* `style.py:38-40` — `DASH_CENTERLINE = "10,4,2,4"`, `DASH_HIDDEN = "5,3"`: px dash strings, unrelated
  to any DXF linetype.
* `style.py:42-46` — `STYLE_OUTLINE` etc., convenience dicts pairing a colour with a px width.
* `svg.py:82-122` — `ViewBox` maps **model metres → px** (`scale`, `x`, `y`, `length`). There is no
  paper-space concept and no px-per-mm: this is exactly the gap P1 (#53) fills.
* `svg.py:168-212` — primitives take `stroke` / `stroke_width` in px; every caller passes literals.
* `render.py:93-106` — the PDF/PNG path for DXF sources:
  `Frontend(RenderContext(doc), MatplotlibBackend(ax)).draw_layout(msp)`. Verified:
  `RenderContext.resolve_all(entity)` resolves **BYLAYER** colour and lineweight from the layer
  record — a line on a layer with `lineweight=35` resolves to `lineweight == 0.35` (mm) and ACI 3 to
  `#00ff00`; an entity on layer `0` resolves to `#ffffff` / `0.25` mm (ezdxf's default). So *the PDF
  pen path already reads the layer table* — populating it is most of the work.
  `Configuration.lineweight_policy` defaults to `LineweightPolicy.ABSOLUTE` with
  `lineweight_scaling = 1.0`.
* `render.py:109-149` — the LibreOffice path (needed for block INSERTs); it honours DXF layer
  properties as any CAD importer does.
* `validate.py:23-31, 83-119` — validation is **string-marker matching on the SVG** plus `meta.yaml`.
  There is no DXF check at all today, so a layer check is a new capability, not a modification.

`bfd.py`, `pid.py`, `isa.py`, `isosheet.py` contain **no DXF code** (grep: zero `dxf` references) —
schematics are SVG-only. They are unaffected except through the shared pen table if they opt in.

### 2.5 Dimension blocks put geometry on layer `0` — legitimately

Block-definition contents of the shipped example DXF:

```
*Model_Space : LWPOLYLINE/OUTLINE, LWPOLYLINE/OBJECT, DIMENSION/DIMENSIONS, TEXT/WATERMARK
_ARCHTICK    : LWPOLYLINE on layer '0'
_CLOSEDFILLED: SOLID      on layer '0'
_CLOSEDBLANK : LWPOLYLINE on layer '0'
*D1          : LINE ×3 on layer '0', INSERT ×2 on DIMENSIONS, MTEXT on DIMENSIONS, POINT ×3 on Defpoints
```

This is standard CAD practice: arrowhead blocks are defined on layer `0` so they inherit from the
INSERT. A naive "any entity on layer `0` is a finding" check would fire on **every dimensioned
drawing**. §3.7 handles this explicitly.

### 2.6 Layer names actually in use (complete enumeration)

Drawing-layer namespace (what this spec governs):

| Layer | Where | Count / note |
|---|---|---|
| `OUTLINE`, `OBJECT`, `DIMENSIONS`, `CENTRE`, `HATCH`, `TEXT`, `TITLEBLOCK`, `WATERMARK` | `dxf.py:20-29`; used by `drawings/example/simple-section/source.py:171-175` | the toolkit's 8 built-ins |
| `FDN` | `components/examples/packaged_unit.yaml:9,18`; **real project** `03-Resources/DEMO Water Project/components/*.yaml` | 19 occurrences in the real project |
| `NOZZLE` | packaged example; real project | **21** occurrences in the real project |
| `EQUIP` | `components/examples/from_dxf_vendor_unit.yaml:11`; real project | 12 occurrences in the real project |
| `EQUIPMENT` | `components/examples/packaged_unit.yaml:15,43` | packaged example only — **collides in meaning with `EQUIP`** |
| `TEXT` | packaged example, real project, `ingest.py:658` (default layer for `add_text`) | 6 in the real project |
| `ZONE` | `components/examples/packaged_unit.yaml:20` | non-physical keep-clear zone |
| `WATER` | `components/examples/packaged_unit.yaml:48` | pond / water body |
| `ACCESS` | `components/examples/from_dxf_vendor_unit.yaml:43` (reclassify target), README:156 | |
| `MH` | `tests/test_cleanup.py:164` | reclassify target in a test |

Source-side (vendor) layer names — `VENDOR`, `DIMS`, `CALLOUTS`, `WALLS`, `FEATURES`
(`from_dxf_vendor_unit.yaml:19,31,38`, `tests/test_from_dxf.py:157-163`) — are read as
`Feature.dxf_layer` (`spec.py:79`), used only for cleanup/reclassify matching, and are **explicitly
out of this table's scope**: they describe someone else's drawing, not ours.

`layer:` keys in `03-Resources/DEMO Water Project/drawings/basin-site/site-plan.yaml`
(`ponds`, `placements`, `layout_pt`, `layout_line`, `layout_poly`) are **QGIS layer names** — a
different namespace entirely (P8 / #60). This spec must not touch them.

Also note `03-Resources/.../basin-site/source.py:81,170` — that sheet colours geometry by **placement
type** (`TYPE_COL`), a deliberate presentation semantic that is *not* a layer palette. §4.10 decides
what happens to it (nothing).

### 2.7 What ezdxf gives us (verified, ezdxf 1.4.4)

* `doc.layers.add(name, *, color=256, true_color=None, linetype="Continuous", lineweight=-1, plot=True, transparency=None, dxfattribs=None) -> Layer` — every field we need is a first-class argument.
* Layer DXF attributes present after round-trip: `color`, `linetype`, `lineweight`, `plot`,
  `plotstyle_handle`, `material_handle`, `name`. `true_color` is supported (`is_supported('true_color')`
  → True; written as group 420, reads back `0x336699`, `layer.rgb == RGB(51,102,153)`).
* `layer.description` is a Python property (XDATA `AcAecLayerStandard`) and **round-trips** through
  `saveas`/`readfile` — verified `'Foundations / concrete'`.
* `layer.dxf.plot` is an `int` (0/1). Layer *off* is a different thing (negative `color`, via
  `layer.off()`) and is out of scope.
* Valid DXF lineweights, `ezdxf.lldxf.const.VALID_DXF_LINEWEIGHT_VALUES` (hundredths of a mm, plus
  three sentinels): `-3` (default), `-2` (byblock), `-1` (bylayer), then
  **`0, 5, 9, 13, 15, 18, 20, 25, 30, 35, 40, 50, 53, 60, 70, 80, 90, 100, 106, 120, 140, 158, 200, 211`**.
  Max real value 211 = 2.11 mm.
* `$LWDISPLAY` exists in a fresh R2018 header with value `0`; setting it to `1` round-trips.
* `doc.linetypes.add(name, pattern, *, description="", length=0.0, dxfattribs=None)`; pattern elements
  are **drawing units** (positive = dash, negative = gap, `0` = dot).
* `ezdxf.new(setup=True)` ships: `Continuous`, `CENTER(2/X2)`, `DASHDOT(2/X2)`, `DASHED(2/X2)`,
  `DIVIDE(2/X2)`, `DOT(2/X2)`, `PHANTOM(2/X2)`, plus `ByBlock`/`ByLayer`.
  **`HIDDEN` does not exist** (`doc.linetypes.get('HIDDEN')` → `KeyError`) even though the issue names a
  `HIDDEN` *layer*. Second correction to the issue: a `HIDDEN` linetype must be declared by us.
* The stock patterns are in **inch-derived millimetre magnitudes** interpreted as drawing units:
  `DASHED` total `1.524` with dash `1.27`, gap `0.254`. Our model space is **metres**
  (`dxf.py:42`, `$INSUNITS 6`), so stock `DASHED` on a 0.28 m pump circle means a 1.27 **metre** dash —
  i.e. it renders solid. Verified via `RenderContext.resolve_all`: `linetype_pattern == (1.27, 0.254)`.
  This is why §3.3 declares dash patterns in **paper millimetres** and converts at plot scale.
* `ezdxf.colors.aci2rgb(7)` → `RGB(255,255,255)`: ACI 7 is *white*, invisible on the white background
  `components/cli.py:78` uses. Mechanically mapping ACI → RGB for SVG pens produces an invisible
  drawing; §4.2 handles it.
* **DXF output is not byte-reproducible by default.** Two identical builds differ in exactly four
  places: `$FINGERPRINTGUID`, `$VERSIONGUID`, and two `DictionaryVariables` strings of the form
  `1.4.4 @ 2026-07-24T22:17:48.592657+00:00`. Setting
  **`ezdxf.options.write_fixed_meta_data_for_testing = True`** makes two builds byte-identical
  (verified). Test 15 depends on this.
* Entities written without a `lineweight` attribute are BYLAYER: `resolve_all` on a plain line on a
  `lineweight=35` layer returns `0.35`. **No entity-level change is needed** to make lineweights work.

---

## 3. Design

New module `technical_drawings_for_agents/src/technical_drawings_for_agents/layers.py` (peer of `style.py`; no dependency on
`components/`), plus a packaged default table
`technical_drawings_for_agents/src/technical_drawings_for_agents/data/layers.default.yaml` (shipped via
`[tool.hatch.build.targets.wheel] packages = ["src/technical_drawings_for_agents"]`, which already includes data
files under the package).

### 3.1 Config file format

One file, two blocks. Example (this *is* the proposed shipped default, abridged):

```yaml
# layers.yaml — drawing layer table (P7). Paper millimetres throughout.
layer_table:
  version: 1                      # int, required, must be 1
  default_lineweight_mm: 0.25      # float, optional, default 0.25
  undeclared: error                # error | warn — default error
  background: light                # light | dark — how ACI 7 resolves to ink; default light

linetypes:                         # optional; patterns in PAPER millimetres
  - name: SK_HIDDEN
    pattern_mm: [3.0, -2.0]        # + dash, - gap, 0 dot
    description: "Hidden detail — 3 mm dash, 2 mm gap on paper"
  - name: SK_CENTRE
    pattern_mm: [12.0, -2.0, 1.0, -2.0]
    description: "Centreline — long/short chain"

layers:
  - {name: OUTLINE,    aci: 7, lineweight_mm: 0.35, description: "Primary section/plan outline"}
  - {name: FDN,        aci: 3, lineweight_mm: 0.35, description: "Foundations, slabs, plinths"}
  - {name: ZONE,       aci: 9, lineweight_mm: 0.18, linetype: SK_HIDDEN, description: "Keep-clear / access zone (non-physical)"}
  # …
```

Field-by-field contract:

**`layer_table` block**

| Key | Type | Default | Validation |
|---|---|---|---|
| `version` | int | *required* | must equal `1`; anything else → `LayerTableError` naming the file and the supported version |
| `default_lineweight_mm` | float | `0.25` | same range rule as `lineweight_mm` |
| `undeclared` | str | `"error"` | one of `error`, `warn`; anything else lists the allowed values |
| `background` | str | `"light"` | one of `light`, `dark`; only affects pen ink resolution for ACI 7 (§4.2) |

**`layers[]`**

| Key | Type | Default | Validation |
|---|---|---|---|
| `name` | str | *required* | 1–255 chars after strip; may not contain any of `< > / \ " : ; ? * \| = ,` (AutoCAD-invalid); may not be `0` or `Defpoints` (reserved — declaring them is a config error, not a silent skip); **case-insensitively unique** within the table (DXF layer names are case-insensitive; two entries differing only in case are a config error). Stored verbatim, matched case-insensitively. |
| `aci` | int | *required* | `1 <= aci <= 255`. `0` (ByBlock) and `256` (ByLayer) are rejected as meaningless on a layer record; negatives are rejected because negative colour is the DXF encoding for a *layer off* and would be an unreadable way to express visibility. |
| `true_color` | str \| null | `null` | `#RRGGBB`, case-insensitive; written as DXF group 420 **in addition to** `aci` (§4.1) |
| `lineweight_mm` | float | table `default_lineweight_mm` | `0.0 <= v <= 2.11`. `0.0` is legal and means "thinnest the device supports" (DXF `0`). Outside the range → `LayerTableError` (never clamped). Snapping rule: §4.3. |
| `linetype` | str | `"CONTINUOUS"` | must resolve case-insensitively to a stock ezdxf linetype (§2.7) **or** to a `name` in this file's `linetypes:` block. An unresolvable name is an error listing what is available (same phrasing style as `svg.py:228-229`). |
| `plot` | bool | `true` | must be a real bool (YAML `yes`/`no` accepted by the parser is fine; a string is an error) |
| `description` | str | `""` | ≤ 255 chars |
| `pen` | str \| null | `null` | `#RRGGBB` — the ink used for **SVG/PDF pens** when it must differ from the plotted ACI (§4.2) |

**`linetypes[]`**

| Key | Type | Default | Validation |
|---|---|---|---|
| `name` | str | *required* | same character rules as a layer name; case-insensitively unique; **may not shadow a stock ezdxf linetype name** (shadowing would make the same name mean different things in two drawings) |
| `pattern_mm` | list[float] | *required* | 2–12 elements; first element must be `> 0` (a pattern must start with a dash — AutoCAD requirement); at least one negative element (a pattern with no gap is `CONTINUOUS`, so declaring it is an error); each `abs(v) <= 100.0` mm |
| `description` | str | `""` | ≤ 255 chars |

Unknown keys anywhere are **rejected**, not ignored — the same discipline as
`components/spec.py:_reject_unknown` (`spec.py:341`).

### 3.2 How a table is supplied — precedence

1. **Explicit path** — CLI `--layers <path.yaml>`, or the literal `--layers default` for the packaged
   table; in Python, `load_layer_table(path)` / `default_layer_table()`.
2. **Per-project / per-drawing config key** — a `layers:` key in a site-layout config
   (`components/layout.py` `load_layout`) or in a drawing's `meta.yaml`, holding either a path
   (resolved relative to the config file, exactly like `components.root`, `layout.py:143`) or the
   literal `default`.
3. **Package default** — only when explicitly named (`default`), never automatically.

**There is no filesystem auto-discovery.** A `layers.yaml` merely *sitting* next to a drawing changes
nothing. Rejected alternative in §4.9.

Merging: a project table **replaces** the default table; it does not merge with it. A project that
wants the default plus two layers references the packaged file and adds `extends: default`? — no:
**no inheritance in v1** (§4.9). One file, one complete table, fully readable in a diff.

### 3.3 Public Python API

```python
# technical_drawings_for_agents/layers.py
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

Background = Literal["light", "dark"]
UndeclaredPolicy = Literal["error", "warn"]


class LayerTableError(ValueError):
    """Raised when a layer table is malformed, or a drawing uses a layer it does not declare."""
    # The module's single error class — house rule (LayoutError, layout.py:40;
    # ComponentSpecError, spec.py:38).


@dataclass(frozen=True)
class LinetypeSpec:
    name: str
    pattern_mm: tuple[float, ...]
    description: str = ""

    def pattern_in_units(self, plot_scale: float) -> tuple[float, ...]:
        """Paper mm -> drawing units (metres) at 1:``plot_scale``.  element * scale / 1000."""

    def dash_mm(self) -> tuple[float, ...]:
        """SVG-ready absolute paper-mm run lengths (dash, gap, ...), signs dropped."""


@dataclass(frozen=True)
class LayerSpec:
    name: str
    aci: int
    lineweight_mm: float = 0.25
    linetype: str = "CONTINUOUS"
    plot: bool = True
    description: str = ""
    true_color: str | None = None
    pen: str | None = None

    @property
    def dxf_lineweight(self) -> int:
        """``lineweight_mm`` snapped to a valid DXF lineweight (hundredths of a mm). §4.3."""

    def ink(self, background: Background = "light") -> str:
        """The #RRGGBB pen colour: ``pen`` -> ``true_color`` -> ACI (7 == foreground). §4.2."""


@dataclass(frozen=True)
class Pen:
    """One resolved pen: what SVG/PDF should draw this layer with."""
    layer: str
    ink: str                       # #RRGGBB
    width_mm: float
    dash_mm: tuple[float, ...] = ()   # () == solid

    def width_px(self, px_per_mm: float) -> float: ...
    def dash_px(self, px_per_mm: float) -> tuple[float, ...]: ...
    def svg_dash(self, px_per_mm: float) -> str | None:
        """``"11.3,7.6"`` or None — the string ``svg_line(dash=...)`` already accepts (svg.py:169)."""


@dataclass(frozen=True)
class ApplyReport:
    layers_created: tuple[str, ...] = ()
    layers_updated: tuple[str, ...] = ()
    linetypes_created: tuple[str, ...] = ()
    lineweights_snapped: tuple[tuple[str, float, float], ...] = ()  # (layer, asked_mm, written_mm)
    undeclared: tuple[str, ...] = ()   # only reachable under undeclared="warn"


@dataclass(frozen=True)
class LayerTable:
    layers: tuple[LayerSpec, ...]
    linetypes: tuple[LinetypeSpec, ...] = ()
    version: int = 1
    default_lineweight_mm: float = 0.25
    undeclared: UndeclaredPolicy = "error"
    background: Background = "light"
    source: Path | None = None            # None for the in-code default

    # -- lookup -------------------------------------------------------------
    def names(self) -> tuple[str, ...]: ...
    def has(self, name: str) -> bool: ...                 # case-insensitive
    def get(self, name: str) -> LayerSpec: ...            # raises LayerTableError, lists near matches
    def pen(self, name: str) -> Pen: ...                  # raises LayerTableError if undeclared
    def pens(self) -> dict[str, Pen]: ...                 # keyed by upper-cased name

    # -- application --------------------------------------------------------
    def apply(self, doc, *, plot_scale: float | None = None) -> ApplyReport: ...
    def check_doc(self, doc) -> tuple[str, ...]: ...      # findings, no mutation (§3.7)


# module-level entry points
def load_layer_table(path: str | Path) -> LayerTable: ...
def default_layer_table() -> LayerTable: ...              # parses the packaged YAML; cached
def resolve_layer_table(spec: str | Path | None, *, base: Path | None = None) -> LayerTable | None:
    """``None`` in -> ``None`` out (the legacy path). ``"default"`` -> packaged table.
    Anything else is a path resolved against ``base``."""

DEFAULT_PX_PER_MM: float = 96.0 / 25.4   # 3.779528 — CSS px per mm, until P1 (#53) supplies the sheet
```

Exported from `technical_drawings_for_agents/__init__.py` alongside the existing names (`__init__.py:12-55`):
`LayerTable`, `LayerSpec`, `LinetypeSpec`, `Pen`, `LayerTableError`, `load_layer_table`,
`default_layer_table`.

### 3.4 The proposed default table

**Basis, stated plainly.** Two rules generated this table, and neither is an invention:

* **Rule A — the eight existing layers keep their exact ACI.** `OUTLINE` 7, `OBJECT` 8,
  `DIMENSIONS` 3, `CENTRE` 6, `HATCH` 9, `TEXT` 7, `TITLEBLOCK` 7, `WATERMARK` 1 — copied from
  `dxf.py:20-29`. Opting into the default table must not recolour an existing sheet; it may only *add*
  lineweight/linetype/plot information. This is a hard constraint on the implementer.
* **Rule B — new layers use the common AutoCAD ACI convention** (1 red, 2 yellow, 3 green, 4 cyan,
  5 blue, 6 magenta, 7 white/black, 8 dark grey, 9 light grey) and the standard AutoCAD lineweight
  ladder (0.13 / 0.18 / 0.25 / 0.35 / 0.50 / 0.70 mm), with the discipline "physical and primary =
  heavy, annotation and non-physical = thin". This is **convention, not a house standard** — every
  value marked ⚠ below is an open question in §8.

| name | aci | lw mm | linetype | plot | description | basis |
|---|---|---|---|---|---|---|
| `OUTLINE` | 7 | 0.35 | CONTINUOUS | ✔ | Primary section / plan outline | ACI: Rule A. lw ⚠ |
| `OBJECT` | 8 | 0.25 | CONTINUOUS | ✔ | Secondary object lines | ACI: Rule A. lw ⚠ |
| `DIMENSIONS` | 3 | 0.18 | CONTINUOUS | ✔ | Dimensions, extension and witness lines | ACI: Rule A. lw ⚠ |
| `CENTRE` | 6 | 0.13 | `SK_CENTRE` | ✔ | Centrelines, axes of symmetry | ACI: Rule A. lt/lw ⚠ |
| `HATCH` | 9 | 0.13 | CONTINUOUS | ✔ | Hatch and fill line work | ACI: Rule A. lw ⚠ |
| `TEXT` | 7 | 0.18 | CONTINUOUS | ✔ | Annotation text and labels | ACI: Rule A. lw ⚠ |
| `TITLEBLOCK` | 7 | 0.25 | CONTINUOUS | ✔ | Sheet border, title block, revision table | ACI: Rule A. lw ⚠ |
| `WATERMARK` | 1 | 0.50 | CONTINUOUS | ✔ | Status watermark (DRAFT / CONCEPT / ISSUED) | ACI: Rule A. lw ⚠ |
| `FDN` | 3 | 0.35 | CONTINUOUS | ✔ | Foundations, slabs, plinths | in use ×19; ACI/lw ⚠ |
| `EQUIP` | 5 | 0.35 | CONTINUOUS | ✔ | Mechanical equipment outlines | in use ×12; ACI/lw ⚠ |
| `NOZZLE` | 4 | 0.25 | CONTINUOUS | ✔ | Nozzles and connection points | in use ×21; ACI/lw ⚠ |
| `PIPE` | 4 | 0.50 | CONTINUOUS | ✔ | Process pipework | named in #59; ⚠ (see §8 — same ACI as NOZZLE) |
| `ELEC` | 6 | 0.25 | CONTINUOUS | ✔ | Electrical equipment and cable routes | named in #59; ⚠ |
| `WATER` | 4 | 0.25 | CONTINUOUS | ✔ | Water bodies, ponds, channels | in use (packaged example); ⚠ |
| `ZONE` | 9 | 0.18 | `SK_HIDDEN` | ✔ | Keep-clear / access zone (non-physical) | in use; dashed because it is not a built thing ⚠ |
| `ACCESS` | 9 | 0.18 | `SK_HIDDEN` | ✔ | Access routes and hardstanding edges | in use (reclassify target); ⚠ |
| `GRID` | 8 | 0.13 | `SK_CENTRE` | ✔ | Setting-out grid | named in #59; ⚠ |
| `ANNO` | 7 | 0.18 | CONTINUOUS | ✔ | Leaders, tags, callouts (non-text annotation) | named in #59; ⚠ |
| `HIDDEN` | 8 | 0.25 | `SK_HIDDEN` | ✔ | Work hidden in this view | named in #59; needs `SK_HIDDEN` (§2.7) ⚠ |
| `DEFPOINTS`-like helpers | — | — | — | — | not declared | reserved names are rejected (§3.1) |

Linetypes shipped with it (paper mm ⚠, magnitudes from common practice — a 3 mm dash reads at any
plot scale):

| name | `pattern_mm` | meaning |
|---|---|---|
| `SK_HIDDEN` | `[3.0, -2.0]` | 3 mm dash, 2 mm gap |
| `SK_CENTRE` | `[12.0, -2.0, 1.0, -2.0]` | long-short chain |
| `SK_PHANTOM` | `[12.0, -2.0, 1.0, -2.0, 1.0, -2.0]` | phantom / future work |

`EQUIPMENT` (used only by `components/examples/packaged_unit.yaml:15,43`) is **deliberately absent** —
see §4.7.

### 3.5 Applying the table on DXF export

`LayerTable.apply(doc, *, plot_scale=None)`, in this order:

1. `doc.header["$LWDISPLAY"] = 1` — otherwise AutoCAD ignores the lineweights we just wrote.
2. **Linetypes.** For each `LinetypeSpec` referenced by at least one declared layer: if
   `plot_scale is None` → `LayerTableError`:
   `"layer table <src>: layer 'ZONE' uses custom linetype 'SK_HIDDEN', whose pattern is declared in paper mm; pass plot_scale=<1:N denominator> so it can be converted to drawing units"`.
   Otherwise `doc.linetypes.add(name, pattern=spec.pattern_in_units(plot_scale), description=spec.description)`,
   skipping names already present in the doc (an ingested doc may already carry one; we never
   overwrite an existing linetype definition — that would silently restyle vendor geometry).
   Unreferenced custom linetypes are **not** written (a table is a standard; a DXF should carry only
   what it uses).
3. **Layer records,** in table order (authored order, preserved by the loader). For each `LayerSpec`:
   if `doc.layers.has_entry(name)` → update `color`, `lineweight`, `linetype`, `plot` in place and set
   `.description`; else `doc.layers.add(name, color=aci, lineweight=spec.dxf_lineweight, linetype=resolved_linetype, plot=spec.plot, true_color=<int or omitted>)` then set `.description`.
4. Return the `ApplyReport`.

Entity-level attributes are **not** touched: entities stay BYLAYER, which is what makes a drafter's
layer overrides work (§2.7, verified).

**Wiring into `DxfBuilder`** (`dxf.py`):

```python
def __init__(self, dxfversion: str = "R2018", units: str = "m",
             layer_table: LayerTable | None = None, plot_scale: float | None = None): ...
```

* `layer_table is None` → **exactly today's code path**, unchanged, including the `LAYERS` loop
  (`dxf.py:44-46`). The `LAYERS` dict stays where it is, byte-for-byte. No deprecation warning is
  emitted (a warning on stderr is an output change for CLI consumers).
* `layer_table` given → the `LAYERS` loop is **skipped entirely** and `layer_table.apply(...)` runs
  instead. The table is authoritative; there is no silent union with the eight built-ins (that is why
  Rule A exists: the default table already contains all eight).
* With a table, every primitive (`line`, `polyline`, `circle`, `text`, `linear_dim`, `insert`)
  validates its `layer=` argument through `table.get(layer)` **before** writing, so a bad layer name
  raises at the offending call site, naming the layer and the closest declared names — not three
  stages later at save time. `undeclared="warn"` downgrades this to one
  `logging.warning` per distinct layer name (`log = logging.getLogger("technical_drawings_for_agents.layers")`, the
  pattern at `render.py:48`) plus an auto-created record using ACI 7, `default_lineweight_mm`,
  `CONTINUOUS`, and a description naming the source file.
* `plot_scale` is stored and passed to `apply`.

### 3.6 Mirroring to SVG and PDF pens

**One source of truth, three consumers.** `LayerTable.pen(layer)` returns a `Pen` in paper
millimetres. Each artifact converts:

| Artifact | Ink | Width | Dash |
|---|---|---|---|
| DXF | `aci` (+ optional `true_color`) | `dxf_lineweight` (1/100 mm) | linetype record, pattern in drawing units at `plot_scale` |
| SVG | `Pen.ink` | `Pen.width_px(px_per_mm)` | `Pen.svg_dash(px_per_mm)` |
| PDF from DXF (`render.py`) | resolved by `RenderContext` from the layer record | ditto (`Properties.lineweight`, mm, `ABSOLUTE` policy) | resolved linetype pattern |
| PDF from SVG (`render.py:204-227`) | inherited from the SVG | ditto | ditto |

So the PDF-from-DXF path needs **no new code**: populating the layer table is what makes its pens
correct, and that is mechanically assertable through `RenderContext.resolve_all` (test 11) rather than
by pixel-peeping a raster.

**SVG.** `components/emit.py:to_svg` gains one keyword:

```python
def to_svg(placed, viewbox, stroke="#111111", fill="none", stroke_width=1.5,
           point_radius=0.08, label_size=12, *,
           pens: LayerTable | None = None, px_per_mm: float = DEFAULT_PX_PER_MM) -> list[str]:
```

* `pens is None` → today's behaviour, unchanged, byte-for-byte.
* `pens` given → for each feature, `pen = pens.pen(feature.layer)`; `stroke = pen.ink`,
  `stroke_width = pen.width_px(px_per_mm)` rounded to 2 dp, `dash = pen.svg_dash(px_per_mm)`.
  The existing `source_status == "verify"` dash (`emit.py:55`) **still wins** where it applies — an
  unverified dimension is a provenance signal that outranks styling, and it is already relied on by
  the shipped sheets. `to_svg` documents that precedence.
* Passing `pens` **together with** a non-default `stroke` or `stroke_width` raises `LayerTableError`
  ("pens= and stroke=/stroke_width= are mutually exclusive") — two sources of truth for one pen is
  precisely the bug this change exists to remove.
* `svg.py` primitives are untouched: `svg_line`/`svg_polygon`/… already accept `stroke`,
  `stroke_width`, `dash` (`svg.py:168-204`).

`style.py` gains nothing but a cross-reference comment: its px constants stay for existing callers.

### 3.7 The `validate` rule

New function in `technical_drawings_for_agents/validate.py`, reusing the existing `ValidationResult`
(`validate.py:34-42`):

```python
def validate_dxf_layers(dxf_path, table: LayerTable | None = None) -> ValidationResult
```

* `table is None` → the function returns a result with **no findings and no checks recorded**
  (`ValidationResult.checked` stays empty). No table, no opinion: a drawing that has not opted in
  cannot fail a check that did not exist.
* Scan scope: **modelspace and every paperspace layout**. Block *definitions* are scanned **only** if
  their name does not start with `*` (anonymous: `*D1`, `*Model_Space`) or `_` (arrowheads:
  `_ARCHTICK`, `_CLOSEDFILLED`, `_CLOSEDBLANK`) — evidence in §2.5. This is a stated exclusion, not an
  accident, and test 13 pins it.
* Findings (each becomes one string in `result.problems`; `result.checked` records the two check
  names):
  1. `geometry on layer '0': 3 entit(ies) (LINE ×2, CIRCLE ×1) in modelspace — every entity must live on a declared layer`
  2. `layer 'EQUIPMENT' is not declared in the layer table (src/technical_drawings_for_agents/data/layers.default.yaml)`
* `POINT` entities on `Defpoints` are ignored (dimension definition points; §2.5).
* Declared-but-unused layers are **not** a finding: a layer table is a standard, not a manifest of
  this sheet.
* `validate_drawing_dir` (`validate.py:83-119`) calls it for each `out/*.dxf` **only when the drawing's
  `meta.yaml` names a layer table** (§3.2 precedence). Findings are prefixed with the file name, the
  same way SVG findings are (`validate.py:117-118`).

### 3.8 CLI surface

Minimal, additive, and read-only except where it already writes:

* `technical_drawings_for_agents layers show [--layers PATH|default] [--plot-scale N]` — new subcommand
  (`cli.py:build_parser`, alongside `p_validate`). Prints one aligned row per layer: name, ACI,
  lineweight mm, DXF lineweight, linetype, plot, pen ink, description; then the linetype block with
  both paper-mm and (if `--plot-scale` given) drawing-unit patterns. Exit 0; exit 2 on a bad table
  with the loader's message. This is the "what will my drafter see" answer, in one command.
* `--layers PATH|default` added to `component` (`components/cli.py:30-43`), `layout`
  (`components/cli.py:93-124`) and `validate` (`cli.py:167-171`).
* `--plot-scale N` added to `component` and `layout`; required only when the resolved table uses a
  custom linetype (loud error otherwise, §3.5 step 2).
* Absent flags ⇒ absent table ⇒ today's behaviour and today's bytes.

No `layers apply <dxf>` retro-fit command in this change (§6).

---

## 4. Behaviour decisions, with rationale

### 4.1 ACI indexed colour is the primary; true colour is optional and additive

**Decision.** `aci` is required on every layer; `true_color` is optional and, when present, is written
*in addition to* (never instead of) the ACI.

**Why.** A drafter's plot style table — CTB (colour-dependent) or STB — is the mechanism by which a
consultant's drawing office turns a screen drawing into a plotted sheet, and the CTB keys off the
**ACI index**. Hand them a true-colour-only DXF and their "colour 3 plots 0.35 mm black" rule matches
nothing, so our lineweights get overridden by whatever the pen table says for "other". ACI is also
what our own `render.py` path resolves today (§2.7) and what the eight existing layers already use, so
ACI-primary is the choice that is simultaneously most useful to the recipient and closest to today.
True colour is kept because a 256-colour palette cannot express a house colour, and DXF lets both
coexist (verified: `color 3` and `true_color 0x336699` round-trip together, with true colour winning
on screen).

**Rejected:** *true colour only* — breaks CTB plotting, the single most common thing a drafter does.
*ACI only* — forces the SVG/PDF ink to be one of 256 fixed hues, which is how you get pure `#00ff00`
foundations in a review PDF.

### 4.2 Pen ink: `pen` → `true_color` → ACI, with ACI 7 meaning "foreground"

**Decision.** `LayerSpec.ink(background)` resolves in that order, and **ACI 7 resolves to the
background's foreground ink** — `#111111` on `light`, `COL_CAD_OUTLINE` (`#d9e2ec`, `style.py:16`) on
`dark` — rather than to `aci2rgb(7)`'s pure white.

**Why.** ACI 7 is *defined* as "white/black": in every CAD viewer it renders as whatever contrasts
with the canvas, and it plots black on paper. Mapping it mechanically to `#ffffff` (verified in §2.7)
would make `OUTLINE`, `TEXT` and `TITLEBLOCK` invisible on the white background that
`components/cli.py:78` already writes. Honouring the convention is not magic; ignoring it is a bug. All
other ACI values map through `ezdxf.colors.aci2rgb`, so there is exactly one special case, and an
explicit `pen` overrides everything.

**Rejected:** *requiring `pen` on every layer* — verbose, and duplicates information ACI already
carries. *A separate light/dark pen pair per layer* — doubles the table for a problem one
`background` switch solves; revisit if a real sheet needs it.

### 4.3 mm → DXF lineweight enum: nearest value, ties to the thinner, out-of-range is an error

DXF stores lineweight as an index into a fixed set of hundredths of a millimetre (§2.7):
`0, 5, 9, 13, 15, 18, 20, 25, 30, 35, 40, 50, 53, 60, 70, 80, 90, 100, 106, 120, 140, 158, 200, 211`.

**Rule.**
1. `v = lineweight_mm * 100`, compared with `math.isclose(..., abs_tol=1e-9)` for exactness.
2. Exact member → used as-is.
3. Otherwise → the **nearest** member by absolute difference; on an exact tie, the **thinner**.
4. `lineweight_mm < 0` or `> 2.11` → `LayerTableError` naming the layer, the value and the max. Never
   clamped.
5. Any snap larger than `0.001` mm emits one `logging.warning` at load time
   (`"layer 'FDN': lineweight 0.36 mm is not a DXF lineweight; writing 0.35 mm"`) and is recorded in
   `ApplyReport.lineweights_snapped`.

Worked examples the implementer must reproduce: `0.25 → 25`; `0.35 → 35`; `0.36 → 35` (1 vs 4);
`0.45 → 40` (tie 5/5 → thinner); `0.515 → 50` (tie 1.5/1.5 → thinner); `0.0 → 0`; `2.11 → 211`;
`2.5 → LayerTableError`.

**Why ties go thinner.** A line that plots heavier than authored eats adjacent detail and quietly
changes emphasis; one that plots a hair thinner is still legible. And "never silently heavier than you
asked for" is the easier rule to hold in your head. Ties are vanishingly rare — the value of the rule
is that both implementations agree.

**Rejected:** *error on any non-enum value* — would force authors to memorise 24 magic numbers to
write a config; the warning already makes the rounding visible. *Silent snap with no warning* —
a typo (`0.36`) should be visible. *A `strict_lineweights` flag* — a knob for a case the warning
already covers; more branches, no new capability.

### 4.4 Lineweight mm → SVG stroke width: `mm × px_per_mm`, default 96/25.4, and the interface P1 must give me

**Decision.** `Pen.width_px(px_per_mm) = width_mm * px_per_mm`, with
`DEFAULT_PX_PER_MM = 96/25.4 ≈ 3.7795` (CSS reference pixel), overridable per call.

**Why this and not a guess.** SVG has no paper space until P1 (#53) lands: `ViewBox` (`svg.py:82-122`)
maps *model metres* to px and nothing maps *paper mm* to px. Inventing a paper size here would collide
with P1 head-on. So the conversion is expressed as a single explicit factor with a defensible default,
and the numbers it produces land on top of today's look: 0.18 mm → 0.68 px vs today's
`LW_THIN = 0.7` (`style.py:34`); 0.25 mm → 0.94 px; 0.35 mm → 1.32 px vs today's `LW_OUTLINE = 1.2`.
That agreement is evidence the default is right, not luck: the existing px constants were eyeballed at
roughly 96 dpi.

**Interface required from P1 (#53)** — stated so P1's implementer can provide it rather than me
guessing at it:

```python
Sheet.px_per_paper_mm: float      # device px per paper millimetre for the rendered SVG canvas
Viewport.plot_scale_denominator: float   # the N in 1:N
```

When P1 lands, `to_svg(..., px_per_mm=sheet.px_per_paper_mm)` and
`DxfBuilder(..., plot_scale=viewport.plot_scale_denominator)` become the wiring, the explicit
parameters keep working, and `DEFAULT_PX_PER_MM` becomes a fallback for un-sheeted SVGs. **No part of
this change may assume a paper size, a sheet margin, or a plot scale of its own.**

**Rejected:** *deriving px-per-mm from `ViewBox.scale`* — that is model-metres-per-px, a completely
different quantity; using it would make a line's *width* depend on the drawing's *zoom*, which is
exactly the bug that paper space exists to prevent. *Hard-coding 3.78* — same number, no name, no
override.

### 4.5 Dash patterns stay visually equivalent because both sides derive from paper millimetres

**Decision.** A custom linetype's pattern is authored in **paper mm**. DXF gets
`element_mm * plot_scale / 1000` **drawing units** (metres); SVG gets `abs(element_mm) * px_per_mm`
**px**. Neither artifact is the source; the paper is.

Worked example, `SK_HIDDEN = [3.0, -2.0]` at 1:200: DXF pattern `(0.6, -0.4)` metres — a 0.6 m dash
that measures 3 mm on a 1:200 print; SVG `stroke-dasharray="11.34,7.56"` px — 3 mm at 96 dpi. Same
plotted appearance, from one declaration.

**Why not the stock linetypes.** Verified in §2.7: ezdxf's `DASHED` is a 1.27-**metre** dash in our
metre model space, which renders solid on a 0.28 m pump. Stock linetypes remain *allowed* (a drafter
knows what `CENTER` means, and an author may want it) but the default table uses `SK_*` patterns for
everything dashed, and the docs warn that stock patterns are metre-scale in our model space.

**Rejected:** *`$LTSCALE` gymnastics* — one global scalar for all patterns, invisible in the config,
and it interacts with `$PSLTSCALE` and per-entity `ltscale` in ways no test would pin. Leave
`$LTSCALE` at `1.0` (its current value) and put the scale in the pattern, where it is diffable.
*Authoring patterns in metres* — then every reuse at a different plot scale silently changes
appearance, which is the bug.

### 4.6 An undeclared layer is an **error** by default; `warn` is opt-in per table

**Decision.** With a table supplied, writing geometry on a layer the table does not declare raises
`LayerTableError` at the call site. `undeclared: warn` in the table downgrades it to one warning per
distinct name plus an auto-created ACI-7 record.

**Why.** Auto-creating on demand is precisely the drift that produced today's state: a typo (`FND` for
`FDN`) becomes a real layer, plots on defaults, and nobody notices until a drafter asks. Loud failure
over silent no-op is the pipeline contract. The `warn` escape exists for one honest case — the first
pass over a large ingested vendor drawing, where you want the drawing out and the list of layers to
declare — and it is a *declared* choice sitting in the config, in git, in the diff.

**Rejected:** *warn as the default* — makes the check advisory, and advisory checks decay.
*Error with no escape* — would make the ingest path unusable until someone hand-enumerates a vendor's
40 layers.

### 4.7 Existing hard-coded colours: `emit.to_svg` gets an opt-in path; project scripts are left alone

**Decision.**
* `dxf.py`'s `LAYERS` dict and `emit.to_svg`'s `#111111`/`1.5` defaults **stay exactly as they are** as
  the no-table path (constraint 1). They are not deprecated in this change.
* `style.py` px tokens stay; nothing is removed.
* `03-Resources/.../basin-site/source.py`'s `TYPE_COL` palette (`source.py:81,170`) is **not
  migrated**. It colours by *placement type* to answer "which unit is this?" — a legend semantic, not a
  layer semantic. Forcing it through the layer table would delete information from that sheet.
* The packaged example `components/examples/packaged_unit.yaml:15,43` uses `EQUIPMENT` where the real
  project uses `EQUIP` (§2.6). The default table declares only `EQUIP`, so the example fails loudly
  the moment it opts in. The implementer **is** permitted to change those two lines to `EQUIP` (and the
  `tests/test_components.py` assertions that ride on them) — that is the intended signal working as
  designed. The implementer must **not** add an alias mechanism.

**Why.** Backward compatibility is sacred, and a presentation palette is not a layer palette. A
migration that quietly restyles a sheet the owner has already reviewed costs more trust than it saves
duplication.

**Rejected:** *aliases (`EQUIPMENT → EQUIP`)* — hides exactly the drift we are trying to surface, and
doubles the lookup surface. *A repo-wide colour migration in this PR* — unreviewable diff; follow-up
issue instead (§6).

### 4.8 `$LWDISPLAY = 1` only when a table is applied

Setting it changes what AutoCAD shows on screen, so it is part of the opt-in, not a global default.
Without `$LWDISPLAY = 1` a drafter sees no lineweights at all and reasonably concludes we did not set
any — so it is not optional *within* the opt-in.

### 4.9 No auto-discovery, no inheritance, no per-entity overrides (v1)

* **No filesystem auto-discovery** of `layers.yaml`. A file appearing on disk must not change output —
  that is an unreviewable action at a distance, and it makes "byte-identical without a table"
  contingent on the working directory. Opt in by naming it.
* **No `extends:` / no merging.** One file is the whole table, so a diff shows the whole truth. If
  three projects share 18 layers, they reference the packaged default; if one differs, it copies and
  the copy is visible.
* **No per-entity lineweight/colour overrides.** Everything is BYLAYER, which is what makes a
  recipient's layer manipulation work. Per-entity overrides are how CAD files become unmanageable.
* **No layer freeze / off / lock, no viewport overrides, no transparency** in v1. Each is a separate
  semantic with its own failure modes; none is needed to make a DXF usable.

### 4.10 Determinism

Layer records are written **in authored table order**, linetypes before layers, so ezdxf handle
assignment is a pure function of the table file. The loader preserves file order and rejects
duplicates, so there is no ordering ambiguity to resolve later. Broader output determinism (manifest,
provenance stamp) is P3 (#55); this change must not pre-empt its decisions, only avoid contradicting
them.

### 4.11 The ISSUED gate

`WATERMARK` is an ordinary layer in the table. Nothing in `layers.py` reads `meta.status`, and
`validate_dxf_layers` returns findings only — it cannot pass, promote or approve anything. A drawing
with a perfect layer table and a `CONCEPT` watermark stays `CONCEPT`.

---

## 5. Acceptance tests

New file `technical_drawings_for_agents/tests/test_layers.py` (plus the noted additions to
`tests/test_components.py` and a new `tests/test_validate_layers.py`). Names assert behaviour, house
style. Every test is mechanically checkable; no visual inspection.

Shared helpers the tests need:
`_table(**overrides) -> LayerTable` (small in-line table: `FDN` aci 3 / 0.35 mm / CONTINUOUS,
`ZONE` aci 9 / 0.18 mm / `SK_HIDDEN` `[3.0, -2.0]`, `TEXT` aci 7 / 0.18 mm);
`_normalise_dxf(text) -> str` (blanks the `$FINGERPRINTGUID` and `$VERSIONGUID` values and any
`<version> @ <ISO-8601>` DictionaryVariables string — the four known non-deterministic lines, §2.7).

1. **`test_default_layer_table_loads_and_declares_every_layer_in_use`**
   *Setup:* none. *Action:* `default_layer_table()`. *Expect:* returns a `LayerTable`;
   `set(t.names())` is a superset of `{"OUTLINE","OBJECT","DIMENSIONS","CENTRE","HATCH","TEXT","TITLEBLOCK","WATERMARK","FDN","EQUIP","NOZZLE","TEXT","ZONE","WATER","ACCESS"}`;
   every `LayerSpec.linetype` resolves (stock or declared in the same table); `t.version == 1`.

2. **`test_default_table_preserves_the_existing_aci_colours`**
   *Setup:* `default_layer_table()` and `technical_drawings_for_agents.dxf.LAYERS`. *Action:* compare.
   *Expect:* for each of the eight names in `LAYERS`, `table.get(name).aci == LAYERS[name]["color"]`
   (7, 8, 3, 6, 9, 7, 7, 1). This is the guard on constraint "opting in must not recolour".

3. **`test_applied_dxf_has_expected_layer_records`**
   *Setup:* `DxfBuilder(layer_table=_table(), plot_scale=200)`; draw a line on `FDN`, a polyline on
   `ZONE`, text on `TEXT`; `save(tmp_path/"a.dxf")`. *Action:* `ezdxf.readfile`.
   *Expect:* `doc.layers` contains `FDN`, `ZONE`, `TEXT`; for `FDN`: `dxf.color == 3`,
   `dxf.lineweight == 35`, `dxf.linetype == "CONTINUOUS"`, `dxf.plot == 1`,
   `layer.description == "Foundations, slabs, plinths"`; for `ZONE`: `dxf.lineweight == 18` and
   `dxf.linetype == "SK_HIDDEN"`.

4. **`test_applied_dxf_enables_lineweight_display`**
   *Action:* as test 3. *Expect:* `doc.header["$LWDISPLAY"] == 1`. And for a builder with **no** table:
   `doc.header["$LWDISPLAY"] == 0`.

5. **`test_true_colour_is_written_alongside_aci`**
   *Setup:* a table whose `FDN` has `true_color: "#336699"`. *Expect:* after readback
   `layer.dxf.color == 3` **and** `layer.dxf.true_color == 0x336699`.

6. **`test_lineweight_snaps_to_nearest_dxf_value`** (parametrised, house style
   `tests/test_components.py:34`)
   *Cases:* `(0.25, 25), (0.35, 35), (0.36, 35), (0.45, 40), (0.515, 50), (0.0, 0), (2.11, 211)`.
   *Action:* `LayerSpec(name="X", aci=1, lineweight_mm=v).dxf_lineweight`. *Expect:* the paired int.
   Tie cases `0.45` and `0.515` specifically assert the thinner branch.

7. **`test_lineweight_outside_dxf_range_is_an_error`**
   *Cases:* `2.5`, `-0.1`. *Expect:* `LayerTableError` whose message contains the layer name, the
   offending value and `2.11`. No clamping, no warning-only path.

8. **`test_snapped_lineweight_is_reported`**
   *Setup:* table with `lineweight_mm: 0.36`. *Action:* `apply`. *Expect:*
   `report.lineweights_snapped == (("FDN", 0.36, 0.35),)` and a captured warning
   (`caplog`) naming the layer.

9. **`test_custom_linetype_pattern_is_written_in_drawing_units_at_plot_scale`**
   *Setup:* `SK_HIDDEN` `[3.0, -2.0]`, `plot_scale=200`, a polyline on `ZONE`; save + read back.
   *Expect:* `doc.linetypes.get("SK_HIDDEN")` exists; its pattern tags carry
   `40 → 1.0` (total), `49 → 0.6`, `49 → -0.4` (i.e. `3.0*200/1000` and `-2.0*200/1000`) within `1e-9`;
   `dxf.description == "Hidden detail — 3 mm dash, 2 mm gap on paper"`.
   At `plot_scale=50` the same table yields `(0.15, -0.10)`.

10. **`test_custom_linetype_without_plot_scale_is_an_error`**
    *Setup:* the same table, `DxfBuilder(layer_table=..., plot_scale=None)`. *Expect:* `LayerTableError`
    mentioning `SK_HIDDEN`, `paper mm` and `plot_scale`. (A table using only stock/CONTINUOUS linetypes
    must **not** raise without a plot scale — assert that in the same test.)

11. **`test_pdf_pens_match_the_layer_table`**
    *Setup:* the DXF from test 3. *Action:* for each entity,
    `RenderContext(doc).resolve_all(entity)`. *Expect:* the line on `FDN` resolves
    `properties.lineweight == pytest.approx(0.35)` and `properties.color == "#00ff00"`
    (`aci2rgb(3)`); the `ZONE` polyline resolves `lineweight == 0.18` and
    `linetype_pattern == (0.6, 0.4)` (element magnitudes at 1:200). This asserts the *pen* the PDF
    backends consume — deterministic, unlike inspecting a raster.

12. **`test_svg_pens_match_the_layer_table`**
    *Setup:* placed features on `FDN` and `ZONE`; `to_svg(placed, vb, pens=_table())`.
    *Expect:* the `FDN` element carries `stroke="#00ff00"` (ACI 3 via `aci2rgb`) and
    `stroke-width="1.32"` (`0.35 * 96/25.4`, 2 dp); the `ZONE` element carries
    `stroke-dasharray="11.34,7.56"`. With `px_per_mm=10.0`, widths become `3.5` — asserting the factor
    is honoured, not baked in.

13. **`test_svg_pen_for_aci7_follows_the_background`**
    *Action:* `LayerSpec(name="TEXT", aci=7, ...).ink("light")` and `.ink("dark")`.
    *Expect:* `"#111111"` and `"#d9e2ec"` respectively — never `"#ffffff"` on light.

14. **`test_pens_and_explicit_stroke_are_mutually_exclusive`**
    *Action:* `to_svg(placed, vb, pens=_table(), stroke="#ff0000")`. *Expect:* `LayerTableError`
    naming both parameters. Same for `stroke_width`.

15. **`test_geometry_on_layer_zero_is_a_finding`**
    *Setup:* a DXF with two lines on `FDN` and one on `0`. *Action:*
    `validate_dxf_layers(path, table)`. *Expect:* `result.ok is False`; exactly one problem, containing
    `"layer '0'"` and the count `1`; `"geometry on a declared layer"` recorded in `result.checked`.

16. **`test_dimension_arrowhead_blocks_on_layer_zero_are_not_a_finding`**
    *Setup:* `DxfBuilder(layer_table=table_including_DIMENSIONS)`, one `linear_dim`, no other geometry
    (this reproduces `*D1` with three LINEs on layer `0` and `_ARCHTICK` — §2.5). *Action:*
    `validate_dxf_layers`. *Expect:* `result.ok is True`. This is the false-positive guard; without it
    the check is unusable.

17. **`test_undeclared_layer_in_an_exported_dxf_is_a_finding`**
    *Setup:* a DXF (built without a table) carrying entities on `EQUIPMENT`. *Action:*
    `validate_dxf_layers(path, default_layer_table())`. *Expect:* one problem containing
    `"'EQUIPMENT'"`, `"not declared"`, and the table's source path.

18. **`test_writing_to_an_undeclared_layer_raises_at_the_call_site`**
    *Setup:* `DxfBuilder(layer_table=_table())`. *Action:* `.line((0,0),(1,1), layer="FND")`.
    *Expect:* `LayerTableError` mentioning `'FND'` and listing declared names; the document is
    unchanged (`len(msp) == 0`). With `undeclared="warn"`: no raise, one logged warning, the entity is
    written, a record for `FND` exists with `color == 7`, and `report.undeclared == ("FND",)`.

19. **`test_dxf_export_without_a_layer_table_is_byte_identical_to_the_legacy_writer`** — **required
    backward-compat test.**
    *Setup:* `ezdxf.options.write_fixed_meta_data_for_testing = True` (restored in a fixture
    teardown). A `_legacy_build()` helper inside the test file replicates the pre-change constructor
    exactly: `ezdxf.new("R2018", setup=True)`, `$INSUNITS = 6`, then
    `doc.layers.add(name, color=...)` for the eight `LAYERS` entries in dict order, then the same
    geometry calls. *Action:* build the same drawing through `DxfBuilder()` (no `layer_table`) and
    through `_legacy_build()`; write both with `doc.write(StringIO())`.
    *Expect:* **the two strings are equal** — `assert new == legacy`. Additionally: the eight expected
    layer records are present with `lineweight == -3` and `linetype == "Continuous"`, and
    `$LWDISPLAY == 0`, i.e. nothing leaked from the new path.

20. **`test_committed_example_dxf_regenerates_unchanged`**
    *Setup:* `ezdxf.options.write_fixed_meta_data_for_testing = True`. *Action:* re-run
    `drawings/example/simple-section/source.py` into a `tmp_path` out-dir; compare
    `_normalise_dxf(new_text)` with `_normalise_dxf(committed_text)` for
    `drawings/example/simple-section/out/EXA-CIV-SEC-001.dxf`. *Expect:* equal. (Normalisation covers
    only the four documented non-deterministic lines from §2.7 — anything else differing is a real
    regression.)

21. **`test_component_svg_without_pens_is_byte_identical`** — **required backward-compat test.**
    *Setup:* the placed features from `components/examples/packaged_unit.yaml`.
    *Action:* `"\n".join(to_svg(placed, vb))` with no `pens=`. *Expect:* equal to a committed
    golden string fixture (`tests/golden/packaged_unit_plan.svg`, generated from the pre-change code).
    SVG emission is pure string building, so this is exactly byte-identical, no normalisation.

22. **`test_layer_table_rejects_bad_config`** (parametrised, message-matched — the
    `tests/test_components.py:34-53` pattern)
    Cases and the substring each error must contain: duplicate name differing only by case →
    `"duplicate"`; `name: "0"` → `"reserved"`; `name: "Defpoints"` → `"reserved"`; `name: "A/B"` →
    `"invalid character"`; `aci: 0` → `"aci"`; `aci: 256` → `"aci"`; `aci: 300` → `"aci"`;
    `linetype: NOPE` → `"unknown linetype"` + the available list; `version: 2` → `"version"`;
    `undeclared: maybe` → `"error, warn"`; unknown key `colour:` → `"unknown key"`;
    `pattern_mm: [-3.0, 2.0]` (starts with a gap) → `"must start with a dash"`;
    `pattern_mm: [3.0, 2.0]` (no gap) → `"at least one gap"`; a custom linetype named `DASHED` →
    `"shadows"`.

23. **`test_layers_show_prints_the_resolved_table`**
    *Action:* `main(["layers", "show", "--layers", "default", "--plot-scale", "200"])`.
    *Expect:* exit `0`; stdout contains `FDN`, `3`, `0.35`, `CONTINUOUS`, and for `SK_HIDDEN` both
    `3.0` (paper mm) and `0.6` (drawing units). Bad table → exit `2` with the loader message on stderr.

24. **`test_validate_without_a_layer_table_reports_nothing`**
    *Action:* `validate_dxf_layers(path_with_layer_zero_geometry, table=None)`.
    *Expect:* `result.ok is True` and `result.checked == []` — no table, no opinion, and no change to
    `technical_drawings_for_agents validate`'s exit code for drawings that have not opted in.

---

## 6. Out of scope

An implementer must **not**, in this change:

* Touch `meta.py`, `meta.yaml` status handling, or anything near the ISSUED gate beyond reading an
  optional `layers:` key.
* Introduce a paper-space/sheet model, a plot-scale *derivation*, or a sheet size — P1 (#53). Consume
  `plot_scale` / `px_per_mm` as parameters only (§4.4).
* Change the PDF/PNG backend chain, the LibreOffice/matplotlib routing, the plot background, or the
  monochrome/black-on-white policy — P4 (#56). This change makes the *pens* right; P4 owns the plot.
* Add an output manifest, provenance stamp, or global determinism pass — P3 (#55).
* Add legibility/collision checks — P5 (#57). Computed dimensions — P6 (#58). GIS/QGIS layer styling
  and `site-plan.yaml`'s `layer:` keys — P8 (#60). Title-block/revision work — P9 (#61).
* Restyle `03-Resources/.../basin-site/source.py` or any project script (§4.7).
* Add an alias mechanism, table inheritance/merging, filesystem auto-discovery, per-entity style
  overrides, layer freeze/off/lock, viewport overrides, or transparency (§4.9).
* Export a plot-style table (CTB/STB), or write hatch-pattern styling into the layer table (the SVG
  hatch catalogue at `svg.py:43-76` stays as it is).
* Retro-fit a table onto an ingested vendor DXF (`technical_drawings_for_agents layers apply`) or modify `ingest.py`'s
  `recolor`/`improve` behaviour.

Follow-up issues to open instead (do not fold these in):

1. `technical_drawings_for_agents layers apply <dxf> --layers … --out …` — retro-fit our table onto an ingested vendor
   drawing, with a layer-name mapping file.
2. Export a plot-style table (CTB or STB) next to the DXF so a recipient's plot matches ours.
3. Repo-wide layer-name audit + migrate `EQUIPMENT` → `EQUIP` everywhere, and decide `TEXT` vs `ANNO`.
4. Once P1 (#53) lands: wire `Sheet.px_per_paper_mm` / `Viewport.plot_scale_denominator` through and
   demote `DEFAULT_PX_PER_MM` to a fallback.
5. Generate a sheet **legend** from the layer table (name, pen swatch, description) so the legend
   cannot drift from the drawing.
6. `validate` finding for a *declared but unused* layer, if that ever proves useful (deliberately not
   a finding today, §3.7).

---

## 7. Risks and migration

| Risk | Who it hits | Mitigation |
|---|---|---|
| A consumer's script constructs `DxfBuilder()` and depends on today's exact bytes | project `source.py` files, the committed example artifacts | Table is opt-in; tests 19–21 pin byte-identity. `LAYERS` and the constructor's no-table branch are untouched. |
| Opting in **recolours** an existing sheet | any drawing adopting the default table | Rule A (§3.4) freezes the eight existing ACIs; test 2 enforces it. Only lineweight/linetype/plot are added. |
| PDF lines look far too heavy after opting in | reviewers of `render.py` output | `Properties.lineweight` is mm under `LineweightPolicy.ABSOLUTE` with `lineweight_scaling = 1.0`, and the matplotlib figure has no declared paper size until P4/P1. Test 11 asserts the *pen value*, not the raster. If the raster looks wrong, that is a P4 plot-geometry defect — file it there rather than fudging the mm here. |
| A drafter's CTB overrides our lineweights | external recipients | Expected and correct: ACI-primary (§4.1) is what makes their pen table work. Note it in the README section that ships with this change. |
| `$LWDISPLAY = 1` surprises someone used to the current look | internal | Only inside the opt-in; documented. |
| Custom `SK_*` linetypes are unknown to a recipient's template | external | They are written into the DXF's own linetype table (verified round-trip), so they travel with the file. |
| A project drops in a `layers.yaml` and expects it to take effect | internal | No auto-discovery (§4.9); `layers show` makes the resolved table visible, and the config key is the single opt-in. |
| Layer-name case drift (`Fdn` vs `FDN`) | internal | Case-insensitive uniqueness at load (test 22) and case-insensitive lookup; names are emitted verbatim. |
| `validate` starts failing CI for drawings mid-migration | internal | `validate_dxf_layers` is inert without a table (test 24); a drawing joins the check when it names a table. |
| ezdxf upgrade changes stock linetypes or the lineweight enum | everyone | Test 6 pins the enum mapping; test 9 pins our own patterns; §2.7 records that the checks were made against 1.4.4. |

**Migration path.** (1) Ship `layers.py` + the packaged default + `layers show`, all opt-in; nothing
changes. (2) the owner confirms the §8 values; update the packaged YAML (a data-file diff, no code). (3)
Opt in the packaged component example and the `simple-section` example, regenerating their committed
artifacts in one reviewable commit whose diff is exactly the styling. (4) Opt in the plant project
configs, fixing whatever undeclared layers the errors surface. (5) Only then consider making
`validate`'s layer check part of a CI gate.

---

## 8. Open questions for the owner (do **not** treat the proposals as settled)

1. **ACI colours for the new layers** — `FDN` 3, `EQUIP` 5, `NOZZLE` 4, `PIPE` 4, `ELEC` 6, `WATER` 4,
   `ZONE`/`ACCESS` 9, `GRID` 8, `ANNO` 7, `HIDDEN` 8. These are common-convention picks, not a house
   standard. Note `PIPE`, `NOZZLE` and `WATER` all land on cyan (4) — three things that will appear on
   the same GA in the same colour. Which two should move?
2. **Lineweights** — the proposal is 0.13 / 0.18 / 0.25 / 0.35 / 0.50 mm assigned as in §3.4. Is there
   a house or client/consultant standard (ISO 128 line groups?) we should match instead?
3. **`EQUIP` vs `EQUIPMENT`** — confirm `EQUIP` as canonical (21 real-project uses vs 2 in an example),
   and that the example may be edited to match.
4. **`TEXT` vs `ANNO`** — keep both (text vs leaders/tags), or collapse to one?
5. **Dash lengths** — 3 mm dash / 2 mm gap for hidden, 12-2-1-2 for centreline. Confirm, or give the
   office standard.
6. **Plot background** — do drafters expect our DXF-derived PDFs black-on-white (the plot convention)
   rather than today's AutoCAD-dark? It is P4's decision, but the answer determines whether the
   default table's `background:` should be `light`.
7. **`true_color`** — do we want house colours (§4.1) on any layer, or is ACI-only fine for v1?
8. **Default `plot: true` on `WATERMARK`** — a CONCEPT watermark should plot; confirm there is no case
   where it should be screen-only.

---

## 9. Files touched (expected shape of the implementation)

| File | Change |
|---|---|
| `src/technical_drawings_for_agents/layers.py` | **new** — `LayerTable`, `LayerSpec`, `LinetypeSpec`, `Pen`, `ApplyReport`, `LayerTableError`, loader, default, resolver |
| `src/technical_drawings_for_agents/data/layers.default.yaml` | **new** — the packaged default table (§3.4) |
| `src/technical_drawings_for_agents/dxf.py` | `DxfBuilder.__init__(layer_table=None, plot_scale=None)`; layer validation in the primitives; **no change to the no-table path** |
| `src/technical_drawings_for_agents/components/emit.py` | `to_svg(..., pens=None, px_per_mm=DEFAULT_PX_PER_MM)`; **no change to the no-pens path** |
| `src/technical_drawings_for_agents/validate.py` | `validate_dxf_layers`; wired into `validate_drawing_dir` only when a table is named |
| `src/technical_drawings_for_agents/cli.py` | `layers show` subcommand; `--layers` on `validate` |
| `src/technical_drawings_for_agents/components/cli.py` | `--layers` / `--plot-scale` on `component` and `layout` |
| `src/technical_drawings_for_agents/components/layout.py` | optional `layers:` key in the layout config (loader + `Layout` field), validated like `components.root` |
| `src/technical_drawings_for_agents/__init__.py` | export the new public names |
| `tests/test_layers.py`, `tests/test_validate_layers.py`, `tests/golden/packaged_unit_plan.svg` | **new** — §5 |
| `README.md` | a "Layer table" section: the schema, the default table, the ACI-primary rationale, and the opt-in |
