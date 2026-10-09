# technical_drawings_for_agents — engineering drawings as code, for AI agents

`technical_drawings_for_agents` lets code and AI agents produce **dimensioned 2D engineering
drawing sheets from text**: SVG, DXF and PDF, with dimensions, hatching, scale bars, north arrows,
title blocks, revision registers and status watermarks. Drawings live in git, so every change is a
reviewable diff, and nothing depends on a proprietary CAD seat. (Formerly `sankofa-draw`.)

The **source of truth is text** (a parametric `source.py`, a YAML data file, or an ASCII DXF); the
CAD file and the PDF/PNG renders are **build artifacts**. A `git diff` of the source is the change
log; the rendered sheet is what a human reviews. The toolkit refuses to let a drawing *assert*
something it did not *derive*: plot scale, scale bar and north arrow are computed from geometry,
and a build fails when a stated scale disagrees with what is drawn.

Maintained by African River Corridors Limited. Licensed under Apache-2.0 — see `LICENSE` and
`NOTICE`. Contributions are welcome: see `CONTRIBUTING.md` and `ROADMAP.md`.

## Install

```bash
pip install git+https://github.com/African-River-Corridors/technical_drawings_for_agents
# with the plot path and its checks:
pip install "technical_drawings_for_agents[plot,verify] @ git+https://github.com/African-River-Corridors/technical_drawings_for_agents"
```

Requires Python 3.10+. Core dependencies: `ezdxf`, `PyYAML`, `matplotlib`.

System tools (not pip packages):

- `rsvg-convert` — SVG to PDF for the `plot` path. macOS: `brew install librsvg`;
  Debian/Ubuntu: `apt-get install librsvg2-bin`.
- Optional: poppler (`pdftoppm`, `pdfunite`) for `--ink-check` and PDF merge fallback;
  LibreOffice and the ODA File Converter for the legacy `render` path and DWG `ingest`.

Extras: `cad3d` (build123d), `ingest` (PyMuPDF, numpy), `plot` (pypdf), `plot-cairo` (cairosvg),
`verify` (Pillow, numpy), `dev` (pytest, ruff).

## Worked example — a dimensioned, hatched section

```python
from pathlib import Path
from technical_drawings_for_agents import (
    Drawing, ViewBox, svg_dimension_h, svg_dimension_v, svg_hatch,
    svg_pattern_defs, svg_scale_bar,
)

# A 6 m wide, 2 m high retaining wall footing; world metres -> 920 x 580 px canvas.
vb = ViewBox(-1.0, 9.0, -1.5, 3.0, 920, 580, padding=90)
dwg = Drawing(
    width=920, height=580, viewbox=vb, status="CONCEPT",
    title_block=dict(project="Example", title="Footing — section A-A",
                     drawing_no="EXA-CIV-SEC-002", revision="A"),
)
dwg.add_defs(svg_pattern_defs(vb, patterns="concrete"))
dwg.add(svg_hatch(vb, [(0, 0), (6, 0), (6, 0.6), (3.6, 0.6), (3.6, 2.4),
                       (2.4, 2.4), (2.4, 0.6), (0, 0.6)], pattern="concrete"))
dwg.add(svg_dimension_h(vb, 0.0, 0.0, 6.0, "6.00 m", offset_px=40))
dwg.add(svg_dimension_v(vb, 6.0, 0.0, 0.6, "0.60 m", offset_px=30))
dwg.add(svg_scale_bar(vb, -0.6, -1.2, 2.0, divisions=4, unit="m"))

Path("out").mkdir(exist_ok=True)
Path("out/EXA-CIV-SEC-002.svg").write_text(dwg.render(), encoding="utf-8")
print("wrote out/EXA-CIV-SEC-002.svg")
```

Then plot it to a paper-size PDF and check it:

```bash
technical_drawings_for_agents plot out/EXA-CIV-SEC-002.svg --out pdf
technical_drawings_for_agents validate out/EXA-CIV-SEC-002.svg   # title block, scale bar, watermark
```

The scale bar is drawn from the geometry; the title block carries no typed scale, because a
drawing must not assert what it has not derived (see *Paper-space sheets* below for an asserted,
checked plot scale).

The repository's own runnable example is `drawings/example/simple-section/source.py`:

```bash
git clone https://github.com/African-River-Corridors/technical_drawings_for_agents
cd technical_drawings_for_agents
pip install -e ".[plot,verify]"
python drawings/example/simple-section/source.py      # writes out/EXA-CIV-SEC-001.svg + .dxf
technical_drawings_for_agents plot drawings/example/simple-section/out/EXA-CIV-SEC-001.svg \
    --out /tmp/pdf                                    # paper-size, fidelity-checked PDF
technical_drawings_for_agents check drawings/example/simple-section   # legibility checks
```

**Have drawing code of your own?** `contrib/` is a reviewed inbox for it — hand over a working
script and its example drawings without shaping them to fit this toolkit first. See
[`contrib/README.md`](contrib/README.md).

Design specifications for each subsystem (paper space, build driver, deterministic emit, plot
fidelity, legibility, computed dimensions, layers, geo backdrops, revisions, stable IDs, chains) are
in `docs/specs/`. They are the design record behind the code and tests; issue numbers in them
refer to the upstream project this package was extracted from.

## What's in the toolkit (`import technical_drawings_for_agents`)

- `ViewBox` — maps real-world **metres → SVG canvas** (scale-true; a scale bar goes on every sheet).
- `sheet` — **paper space**: an ISO 216 size in millimetres with an *asserted* plot scale 1:N
  (`Sheet`, `SheetDrawing`, `PlotScale`, `SheetFrame`, `load_sheet_config`). See
  [Paper-space sheets](#paper-space-sheets-a-declared-paper-size-and-an-asserted-plot-scale).
- Primitives — `svg_line` / `rect` / `circle` / `ellipse` / `polygon` / `path` / `text`.
- Furniture — `svg_dimension_h/v`, `svg_hatch` (+ `HATCH_PATTERNS`: concrete / rockfill / soil / water),
  `svg_centerline`, `svg_leader`, `svg_scale_bar`, `svg_north_arrow`, `svg_title_block`, `svg_border`,
  and **`svg_status_watermark`** (DRAFT / CONCEPT / ISSUED).
- `Drawing` — a sheet assembler that stamps the border, status watermark and title block for you.
- `DrawingMeta` — loads/validates `meta.yaml` (incl. the for-construction / ISSUED safety gate).
- `DxfBuilder` — an `ezdxf` exporter writing geometry in **real metres** (model space) for a drafter.
- `layers` — a validated layer / lineweight / linetype table that can drive DXF layer records and
  matching SVG pens when a drawing opts in.
- `backdrop` — load a `tdfa.geo/1` raster manifest (written by an external GIS tool) and emit a checked, relative SVG `<image>`
  link for a pre-clipped orthophoto/DTM sidecar. Reads JSON with the standard library only:
  **`technical_drawings_for_agents` never gains a GDAL dependency.**
- `components` — to-scale, georeferenceable component specs: one YAML geometry source that can emit
  GeoJSON for GIS and SVG/DXF for drawings.
- `components.layout` — whole-**site** layout: read a GIS editor's footprints back into a git-tracked
  placements register (centroid + long-axis bearing + as-placed size), stamp each placement's
  components, and check the result (parallelism, clear spacing, overlap, envelope).
- `isa` — the **ISA-5.1 symbol library**: parametric, port-exposing symbol functions
  (`pump`, `valve`, `instrument`, `vessel`/`tank`/`filter`, `dosing_skid`, `tie_in`, `flow_arrow`) and
  `build_symbol(type, x, y, **params) -> Symbol`. Pure geometry — reusable beyond P&IDs (single-line
  diagrams later).
- `isosheet` — the shared ISO house-sheet chrome (frame / title block / legend / notes / logo strip),
  used by both the `bfd` and `pid` generators.

## CLI

```bash
technical_drawings_for_agents plot <source.dxf | sheet.svg | source.py ...> [--out DIR] [--page 'ISO A3'] [--portrait] [--margin MM] [--format pdf|png|svg] [--dpi N] [--layout NAME] [--fidelity strict|warn|off] [--svg-backend auto|rsvg|cairosvg] [--dark] [--ink-check] [--review OUT.pdf [--allow-missing]] [--json]
technical_drawings_for_agents map  <plot.yaml> [--out DIR]                                        # map sheet from GeoJSON — no QGIS, no GDAL
technical_drawings_for_agents render <source.py | sheet.svg | sheet.dxf> [--out DIR] [--plot]
technical_drawings_for_agents validate <drawing-dir | sheet.svg | data.yaml> [--strict] [--layers PATH|default] [--legibility]
technical_drawings_for_agents layers show [--layers PATH|default] [--plot-scale N]                    # inspect layer pens and DXF records
technical_drawings_for_agents check <drawing-dir | sheet.svg> [--config legibility.yaml] [--strict] [--require a,b] [--format text|json] [--baseline file] [--show-items]
technical_drawings_for_agents manifest <output-file | dir> [--check] [--json]                      # read/check an output's provenance sidecar
technical_drawings_for_agents sheet <meta.yaml | config.yaml> [--json] [--check]                  # resolve a paper-space sheet (read-only)
technical_drawings_for_agents revision list <drawing-dir>                                         # read the meta.yaml revision register
technical_drawings_for_agents revision add  <drawing-dir> --rev C --description "…" --by AB [--chk CD] [--date YYYY-MM-DD]
technical_drawings_for_agents revision seal <drawing-dir> --rev C                                 # interactive terminal only; refuses under CI
technical_drawings_for_agents build <drawing-set.yaml> [--target NAME]... [--check] [--force] [--dry-run] [--timings]
technical_drawings_for_agents ingest <supplier.dwg | converted.dxf> [--out DIR] [--isolate] [--window x0,y0,x1,y1] [--corrections c.yaml] [--no-strip-tags]
technical_drawings_for_agents bfd  <data.yaml> [--out DIR] [--view block|swimlane|profile|all]   # topology schematic (Graphviz auto-layout)
technical_drawings_for_agents pid  <data.pid.yaml> [--out DIR]                                    # ISA-5.1 P&ID (positional layout)
technical_drawings_for_agents component <spec.yaml> --view plan --emit geojson|svg|dxf --origin E,N --rot DEG --out out.file [--layers PATH|default] [--plot-scale N]
technical_drawings_for_agents layout <layout.yaml> [--from-geojson export.geojson] [--check-register] [--canonicalise-register] [--register-source TEXT] [--require-ids] [--emit geojson|dxf --out F] [--emit-register F] [--check-only] [--warn-only] [--no-snap] [--layers PATH|default] [--plot-scale N]
```

- **plot** — **the** review path, and the one to use. Geometry → SVG (ezdxf `SVGBackend` on a real
  `layout.Page`) → PDF/PNG (`rsvg-convert`, else `cairosvg`). Before a byte is written, a DXF source is
  **fidelity-checked**; if the plot cannot be proved complete, no canonically-named artifact is written
  and the exit code says why. See *The plot path* below.
- **map** — a map sheet straight from GeoJSON: no QGIS, no GDAL, no OGR, no `pyproj`, no reprojection
  and no clipping. See *Map sheets* below.
- **render** — **legacy, frozen.** Turns a source into **PDF + PNG**. A `.py` generator is executed (it
  writes its own `out/`), then any DXF it produced is rendered; a `.dxf` is rendered directly; a `.svg`
  is rasterised via `cairosvg` if installed, else via headless **LibreOffice** (`soffice`), else its
  sibling DXF is rendered. Default `--out` is `<source-dir>/out`. `--plot` routes the same source
  through `plot` instead; without that flag the output is byte-for-byte what it has always been.

  **Render backend routing — the README used to have this backwards.** Measured 2026-07-24 on ezdxf
  1.4.4, on this repo's own `tests/synthetic.py::make_dxf_with_insert` fixture:
  - the **ezdxf/matplotlib** backend is **faithful**. It traverses into block definitions and draws
    every child, attributing each backend call to the `INSERT`'s own handle (4 recorded draw ops on that
    fixture). The old claim that it "silently drops block-insert detail" does not hold at ezdxf 1.4.4.
  - **LibreOffice**'s DXF import filter **loses every line, circle and polyline** and stacks the
    surviving text: 705 non-white pixels of an 842×596 page. Its PDFs are also non-deterministic — they
    embed `/CreationDate` and a random `/ID` and ignore `SOURCE_DATE_EPOCH`.
  - So `render`'s auto-routing sends block-heavy *vendor* drawings to the **worse** backend, and its
    "falls back to matplotlib with a warning" degrade path actually *improves* the output. The routing is
    kept anyway, because changing it would change an existing caller's output; choosing LibreOffice now
    emits a `DeprecationWarning` pointing at `plot`. Force a backend with
    `render_dxf(..., backend="matplotlib"|"libreoffice")`.
  - Two further silent-loss mechanisms live in `render`: it plots `doc.modelspace()` only, so a
    **paper-space** drawing yields a blank PDF at exit 0; and `render_py` globs `*.dxf` only, so an SVG a
    generator wrote never reaches a PDF. `plot` fixes both.
- **validate** — checks a sheet carries the mandatory elements — **title block + scale bar + status
  watermark** — and (for a drawing directory) validates `meta.yaml`, including the hard rule that
  `for_construction: true` is only allowed once `status: ISSUED`. **Schematics (BFD / P&ID) are NTS**,
  so a scale bar is not required for them; a `*.pid.yaml` (or a directory containing one) also has its
  **P&ID data model** validated — every symbol has a tag, every line has a known type and resolves to
  declared ports. A sheet carrying a `sheet:` block additionally has **every scale claim recomputed**
  from the record in its own SVG (see below). `--strict` promotes warnings to failures (exit 1); it is
  monotone — it can only *add* problems, never clear one, and in particular never clears the
  for-construction / ISSUED gate. `--layers PATH|default` adds the DXF layer-table check; without a
  named table, DXF layers are not checked and legacy drawings keep the same validation result.
  `--legibility` opts in to the `check` rules below and appends their
  error findings to validation problems; without that flag, legacy validation output and exit codes are
  unchanged.
- **check** — parses emitted SVG and runs deterministic legibility checks: text/text collisions, text
  over opaque fills, frame containment, title-block clearance, detail-panel clearance and fill,
  co-located numbered markers, watermark/status coherence, title-block revision/number coherence,
  declared legend completeness, and
  declared verify-marker annotation. Exit **0** no error findings, **1** error findings (or warnings /
  skipped checks with `--strict`), **2** unreadable target, invalid config, or an unsupported SVG
  construct. The command is read-only and a clean report is **not** approval to issue.
  `--require a,b` makes named skipped checks fatal, `--format json` prints the deterministic report for
  CI/build tooling, `--show-items` includes parsed boxes in JSON, and `--baseline file` removes known
  accepted findings by exact check/message while warning when a relevant baseline goes stale.
- **layers show** — prints the resolved layer table: layer name, ACI, paper-mm lineweight, DXF
  lineweight enum, linetype, plot flag, resolved pen ink and description. With `--plot-scale N`, it
  also shows custom linetype paper-mm patterns converted to drawing units for a 1:N plot.
- **manifest** — read an output's `<output>.manifest.json` sidecar, or `--check` it: re-hash every
  declared input and the output itself and report what moved. Exit **0** current (or warnings only),
  **1** at least one error finding, **2** no manifest beside the target. Never writes, and a provenance
  stamp is **not** an approval — see *Deterministic emit* below.
- **sheet** — resolve a `sheet:` block and report the binding: paper size, margins, drawing frame,
  reserved regions, viewport frame, extent, required millimetres, the derived scale and scale bar, and
  the north bearing. `--json` prints the sheet record; `--check` resolves silently for CI. Exit **0**
  resolved and fits, **1** a sheet defect (it does not fit at the stated scale, or the block is
  invalid), **2** a usage error (file missing, unreadable YAML, or no `sheet:` block). It is
  **read-only** — it writes no file and has no `--write` / `--fix` / `--migrate`.
- **bfd** — a **topology** schematic (block-flow / swimlane / hydraulic profile). Layout is delegated
  to Graphviz `dot` (rank-based, collision-free) — right for topology, **wrong for a real P&ID**.
- **pid** — an **ISA-5.1 P&ID** with symbol geometry and **explicit positional layout** (see below).
- **ingest** — convert + clean a supplier drawing (see below).
- **component** — place a real-metre component spec once and emit GeoJSON, SVG or DXF (see below).
- **dimensions** — compute dimensions, clearances, route lengths and a setting-out table from a
  placed layout (see below). Read-only, and there is no field in its schema that supplies a
  displayed number.
- **build** — drive a whole declared drawing set in dependency order (see below). It is the only verb
  that knows what comes before what.

## Building a drawing set (`technical_drawings_for_agents build`)

**Before your first build, add `.technical_drawings_for_agents/` to the consuming project's `.gitignore`.** The build
cache lands at `<root>/.technical_drawings_for_agents/build-state.json`, it holds machine-specific `st_mtime_ns` values,
and it must never be committed. Deleting it is always safe — the next build rebuilds everything.

A drawing set is one text file. `build` reads it, computes a dependency graph from declared inputs and
outputs, works out from **content hashes** which targets are stale, runs exactly those in one
deterministic order, and stops with an attributable error the first time anything is missing, cyclic or
broken. Nobody has to remember that step 3 comes before step 4, because nothing asks them to.

```bash
technical_drawings_for_agents build drawings/example/drawing-set.yaml              # build every stale node
technical_drawings_for_agents build drawings/example/drawing-set.yaml --dry-run    # explain: what would run, and why
technical_drawings_for_agents build drawings/example/drawing-set.yaml --check      # assert: is this current? writes NOTHING
technical_drawings_for_agents build drawings/example/drawing-set.yaml --target section-sheet
```

`drawings/example/drawing-set.yaml` is a worked, commented set that the test suite builds and `--check`s
on every CI run — start from it.

### Exit codes

| Code | Meaning |
|---|---|
| 0 | success: everything selected is current (including "nothing to do"), or `--check` says current, or `--dry-run` printed the plan |
| 1 | a node failed, a selected `external` node is blocked, or a `review: pdf` target produced no PDF |
| 2 | configuration or precondition error — bad set file, a cycle, a duplicate output claim, a missing source input, a missing tool, an open GeoPackage. **Nothing was built.** |
| 3 | `--check` only: not current or not buildable *here* — stale, missing, blocked, or a tool this box lacks |

`--check` reports a missing tool as 3, not 2: it is answering a question about *this machine*, and
"the set is fine, this box lacks `rsvg-convert`" is an answer, not a broken config.

### The schema, in one screen

```yaml
version: 1                       # required, must be 1
set:
  id: BASIN-SITE                 # ^[A-Za-z0-9][A-Za-z0-9._-]*$
  description: …
  root: .                        # all relative paths resolve against this (default: this file's dir)

inputs:                          # optional named inputs, referenced as "@name"
  ortho: {path: geo/site_ortho_s2.tif, digest: size-mtime}   # 484 MB: opt out of content hashing
  maybe: {path: optional.geojson, optional: true}

nodes:
  <name>:
    run: verb:render | verb:validate | verb:bfd | verb:pid | verb:component
       | verb:layout | script | command | external      # exactly these nine
    description: …
    inputs:  [path, "@name", "site.gpkg:layer"]
    outputs: [path, "site.gpkg:layer"]                  # required except for verb:validate
    needs:   [other-node]                               # ordering that is not a file dependency
    review:  pdf                                        # this target owes a reviewable PDF
    expand_inputs: true                                 # verb:layout only (its default)
    timeout_s: 600                                      # script / command only; no default
    args: {…}                                           # runner-specific, fully validated on load

targets:
  site-ga: [site-sheet]          # a label for a set of nodes; --target takes either
```

### Rules that are not negotiable

- **The ISSUED gate is untouchable.** Declaring any `meta.yaml` as an output is a configuration error.
  A build reads the gate and never writes it: promotion to ISSUED FOR CONSTRUCTION is an engineer's
  signature, not a build product.
- **A GeoPackage is a container, not an output.** The unit of production is the layer, declared
  `path.gpkg:layer`. Two nodes writing *different* layers of one file is legal; two nodes claiming the
  same `path.gpkg:layer` is a hard error naming both nodes and quoting the key. The key is the
  set-relative POSIX path plus the layer name, so you can grep the set file for the exact string in the
  message.
- **The human-authored `placements` layer is an input the build may never write**, and `--force` cannot
  override it.
- **An open GeoPackage is a hard error.** A live `-wal`/`-shm` sidecar means a QGIS session owns the
  file; OGR does *not* refuse it, so the build does. Hashing SQLite mid-transaction records a digest
  that changes at the next checkpoint — phantom staleness from outside the graph.
- **No pipeline step may require a GUI.** A human or GUI step is an explicit `external` node with a
  `reason` (and optionally a `howto`). The build never runs one, under any flag. When a selected
  external node is not satisfied it is `BLOCKED`, nothing after it runs, and the exit code is 1 —
  because building the downstream of a step that has not happened is how a stale sheet gets issued
  with a green log.
- **Content hashes decide staleness, never mtime.** A `git checkout` rewrites every mtime; a size+mtime
  pair is only ever a cache key for a digest already computed. `digest: size-mtime` is an explicit
  opt-out for very large binaries and is **reported on every run**.
- **Missing tools fail before the first byte.** Every `command.tool` and every `script.args.tools` entry
  is resolved with `shutil.which` in pre-flight; if any is absent, nothing is built. A build that dies
  half-way leaves a mixed-vintage drawing set, which is worse than not building.
- **A failed node leaves no half-written output.** Anything the failed run touched is moved to
  `<path>.partial`; every declared output either does not exist or holds exactly its pre-run bytes.
- **Serial execution, lexicographic tie-break.** Reordering the `nodes:` block is cosmetic and cannot
  change the build order or a single log line.

### `review: pdf` — how the pipeline contract's rule 6 is enforced

The *Engineering Drawings as Code* pipeline contract says a pipeline emits standard interchange
artifacts **and always finishes by producing a PDF**. `build` enforces that **per declared review
target**, because only the set file knows which of its targets is a sheet:

- A node marked `review: pdf` must declare at least one `.pdf` output — a marker with no PDF to produce
  is a configuration error.
- Every marked target must produce its PDF, or the build **fails naming the target**. It never succeeds
  having emitted only intermediates.
- `--check` verifies each marked target's PDF is present and current, and writes nothing doing it.
- An **unmarked** target owes nothing. Intermediates, GeoJSON, staged layers and synthetic nodes are
  unaffected: sheet-ness is declared, never inferred from a suffix or a directory.

A real project's `drawing-set.yaml` must therefore mark its sheet targets. `technical_drawings_for_agents plot` (P4) is
the only supported PDF path, so a sheet node is usually a `command` node invoking it.

### `external` nodes and adoption

An `external` node models a step no tool can run — a person dragging footprints in QGIS, a hand export
with no recorded command. Its outputs are hashed and it participates in ordering and staleness like any
other node; it is simply never *run*.

That leaves one case the naive rule cannot answer: an output that exists but has never been recorded.
Since the build can never run the node, "no state record means unbuilt" would block the graph forever.
So when an external node's declared output **exists** with no state record, the build **records its
digest and treats it as current** — and **logs the adoption**, because it is a state change the
operator should see, not a silent promotion. The same applies when a human edits the output again: it is
**re-adopted** and the downstream rebuilds, because hand-authoring is the entire point of the node. If
the output does **not** exist, the build fails naming the node and telling you what to produce.

### `command` is a bridge, not the destination

`command` runs an argv list **without a shell** (a `shell:` key is a configuration error: a shell string
can pipe, redirect and glob its way into inputs and outputs the set file never mentions, which silently
defeats the graph). It exists so a real pipeline is expressible today. As GDAL work moves behind
an external GIS tool's `geo` step, those `command` nodes should disappear from the set file.

### What `build` deliberately does not do

`ingest` is not a runner: it is a human-supervised, one-way conversion whose outputs are named after the
converted file rather than declared up front. There is no `include:`/merge mechanism for set files — a
set file *references* the existing configs by path, which is what "no implicit relationships" asks for.
There is no `--jobs`: determinism is the deliverable, and in-process verbs share process-global state.
And `build` never writes a `*.manifest.json` — provenance sidecars belong to
`technical_drawings_for_agents.provenance`, whose `emit_digest` this driver uses for every output digest it can.

## The plot path (`technical_drawings_for_agents plot`)

One entry point, `technical_drawings_for_agents.plot.plot(PlotRequest)`, used by every generator. The contract is one
sentence: **`<stem>.pdf` means "passed every check"**.

```
geometry --(ezdxf SVGBackend on a layout.Page)--> SVG --(rsvg-convert | cairosvg)--> PDF/PNG
```

Stage 1 has exactly one implementation and is not selectable. Stage 2 is a two-link chain with **no
fallback past the end of it** — a missing backend is a named error listing both remedies, never a
degraded artifact. LibreOffice is not in the chain, for either DXF or SVG (see the measurements above).

### The fidelity check

Before any file is written, the frontend is recorded into ezdxf's `Recorder` with the *same*
`Frontend`/`RenderContext`/`Configuration` the SVG backend will receive, and that recording is then
**replayed** into the backend — so the check measures the bytes that actually ship, not a model of them.
Four checks:

| ID | Check | Rule |
|---|---|---|
| **F1** | coverage | every expected-visible drawable unit produced ≥1 backend call |
| **F2** | explosion | per `INSERT`, `recorded_ops >= exploded_leaves` |
| **F3** | extent | the plotted bounding box is not degenerate |
| **F4** | layout | the plotted layout is the one holding the drawing |

A *drawable unit* is an entity in the plotted layout, or an `ATTRIB` an `INSERT` in it owns (ezdxf keys
an ATTRIB's ops to the ATTRIB's own handle, so folding them into the INSERT would fail on every tagged
block). Visibility is `RenderContext.resolve_visible` — the frontend's own predicate — so **frozen
layers, off layers and the `invisible` flag are excluded from the expectation by construction**. A
frozen layer does not plot; that is correct CAD semantics, not lost geometry.

**F2 is a lower bound, never equality**, and that is deliberate. Measured entity→op fan-out at ezdxf
1.4.4: a 4-vertex `LWPOLYLINE` merges to **1** op, an `MTEXT` fans to **3**, a rendered `DIMENSION` to
**6** (leaf walk yields 9), a 3×4 `MINSERT` to **24** (leaf walk yields 2). Equality is provably
impossible; the bound is still exactly the assertion that catches a backend dropping a block's children.

There is deliberately **no tolerance knob**. "How much of the drawing may be missing" is not a dial an
engineering review pipeline should have.

### What happens on a mismatch

| `--fidelity` | Result |
|---|---|
| `strict` (default) | raises, **exit 3**, and **no artifact at the canonical path** |
| `warn` | the artifact lands as `<stem>.degraded.pdf` — never `<stem>.pdf` — and a `WARNING` is logged |
| `off` | no check runs; the artifact lands as `<stem>.unchecked.pdf` (triage of a known-broken file) |

The degradation is encoded in the thing the reviewer actually sees. A log line is not enough: the old
path already logged a warning and still wrote the incomplete PDF to the canonical name.

`--ink-check` additionally rasterises the produced PDF (`pdftoppm` + Pillow) and requires ink on every
page. It runs **before** the PDF is promoted to its canonical name, and it is opt-in because a
pre-raster check cannot prove the converter honoured the geometry while a raster check can only catch
*blank*, not *incomplete*.

### Exit codes

Existing codes keep their meaning; every new one is ≥3.

| Code | Meaning |
|---|---|
| 0 | every requested artifact was produced and passed every enabled check |
| 1 | unexpected failure, or a generator raised |
| 2 | bad usage or input not found (also `PlotError(kind="input")`) |
| 3 | `fidelity` — the plot was not faithful; no canonical artifact |
| 4 | `bundle` — `--review` could not produce a complete bundle |
| 5 | `backend` — a required backend is missing or failed |
| 6 | `unsupported` — unsupported source type or page spec |

### `--review`: bundling a set

```bash
technical_drawings_for_agents plot drawings/basin-wtp --review drawings/basin-wtp/out/REVIEW.pdf
```

Sheet order comes from `meta.yaml`'s optional `sheets:` list (verbatim — the explicit, reviewable
ordering hook), else from the PDFs in `<dir>/out/` if any, else the SVGs, ordered by
`(drawing_number, sheet_index, filename)`. The resolved order is printed to stdout before bundling. The
produced page count is **read back** from the file and asserted; a mismatch is exit 4 with no bundle.

A listed-but-absent or empty sheet makes the set incomplete: by default nothing is written (exit 4,
every missing sheet named). `--allow-missing` writes `<name>.partial.pdf` — never `<name>.pdf` — with a
`SHEET MISSING … NOT FOR REVIEW` placeholder page in each gap, and **still exits 4**.

The bundler copies pages. It never renders a watermark, never reads `for_construction`, and never
writes `meta.yaml`.

### Determinism (and one correction)

The SVG stage is byte-identical across runs, unconditionally. The **PDF stage is not**, unless a
`SOURCE_DATE_EPOCH` is resolved: measured on librsvg 2.62.3 / cairo 1.18.4, two conversions of a
byte-identical SVG more than a second apart differ, and cairo *does* honour the epoch. `plot` forwards
P3's resolved epoch (`EmitPolicy.from_environment()`) into the converter's environment, so:

```bash
SOURCE_DATE_EPOCH=1700000000 technical_drawings_for_agents plot sheet.dxf --svg-backend rsvg
```

is byte-reproducible end to end. Without it, `provenance.FORMAT_REPRODUCIBILITY["pdf"]` correctly
declares the PDF non-reproducible and `emit_digest()` refuses to hand out a digest for one.
`PlotResult.svg_backend` records which converter made the bytes — a PDF is not reproducible unless you
know that.

### Optional dependencies

| Thing | Install | Needed for |
|---|---|---|
| `rsvg-convert` | macOS `brew install librsvg`; Debian/Ubuntu `apt-get install librsvg2-bin` | SVG→PDF/PNG (preferred). Or set `RSVG_CONVERT_BIN`. |
| `cairosvg` | `pip install 'technical_drawings_for_agents[plot-cairo]'` | alternative SVG→PDF/PNG (needs system libcairo) |
| `pypdf` | `pip install 'technical_drawings_for_agents[plot]'` | multi-page PDF merge for `--review`; falls back to poppler's `pdfunite` |
| `pdftoppm`, `pdfinfo`, `pdfunite` | macOS `brew install poppler`; Debian/Ubuntu `apt-get install poppler-utils` | `--ink-check`, bundle fallbacks |
| Pillow | `pip install 'technical_drawings_for_agents[verify]'` | `--ink-check` |

## Map sheets (`technical_drawings_for_agents map`)

```yaml
# plot.yaml
map:
  crs: EPSG:32630                 # recorded and asserted, never transformed
  extent: [788392, 322125, 788702, 322395]   # xmin, ymin, xmax, ymax in `crs`
  backdrop:                       # OPTIONAL. Prefer the manifest form:
    manifest: geo/basin.geo.json  #   a 'tdfa.geo/1' manifest from a GIS tool's `geo` step
    # image: out/geo/clip.png     #   plain form: must already be clipped to `extent`
    # extent: [788392, 322125, 788702, 322395]
  layers:
    - name: rafts
      geojson: ../components/basin-wtp/basin_layout.geojson
      style: {stroke: "#123456", fill: none, stroke_width: 1.2}
      label: tag
sheet: {width_px: 1400, height_px: 990, background: "#1a1a2e"}   # optional
meta: {number: STA-SITE-GA-001, title: Site general arrangement, status: CONCEPT, scale: "1:500"}
```

GeoJSON is read with the standard library. Every layer must **already** be in `map.crs` and inside
`map.extent`; a layer that declares a different CRS, or whose coordinates sit more than one extent span
outside it, is an error pointing at the `geo` verb. `mapplot` never reprojects and never clips.

A `manifest:` backdrop is delegated wholly to `technical_drawings_for_agents.backdrop` (digest re-hash, frame match,
declared opacity, href policy). A plain `image:` backdrop must align with `map.extent` to within 1e-6 of
its span, and its href must not escape the sheet directory — librsvg silently drops a `../` link (exit
0, blank backdrop), so that is refused rather than deferred to a renderer that will not complain.

Supported geometry: `Point`, `LineString`, `Polygon`, `MultiLineString`, `MultiPolygon`. Anything else
is an error naming the feature index — a silently skipped feature is a defect, not a default. Map
fidelity is the invariant that **every input feature produced at least one element in the sheet body**,
counted during emission and reported in the same `FidelityReport` shape.

## Layer tables: DXF layer records and matching SVG pens

Layer tables are opt-in. With no table named, `DxfBuilder()` still writes the historical eight layer
records (`OUTLINE`, `OBJECT`, `DIMENSIONS`, `CENTRE`, `HATCH`, `TEXT`, `TITLEBLOCK`, `WATERMARK`) with
their existing ACI colours only, and component SVG output still uses its single `#111111` / `1.5` px
pen. A `layers.yaml` sitting next to a drawing does nothing until a config or CLI flag names it.

Use the packaged proposal table with:

```bash
technical_drawings_for_agents layers show --layers default --plot-scale 200
technical_drawings_for_agents component unit.yaml --emit dxf --out unit.dxf --layers default --plot-scale 200
technical_drawings_for_agents component unit.yaml --emit svg --out unit.svg --layers default
technical_drawings_for_agents layout site_layout.yaml --emit dxf --out out/site.dxf --layers default --plot-scale 200
technical_drawings_for_agents validate drawings/example/simple-section --layers default
```

A drawing `meta.yaml` or site-layout config may also opt in:

```yaml
layers: default          # or a path resolved relative to this YAML file
```

Minimal table shape:

```yaml
layer_table:
  version: 1
  default_lineweight_mm: 0.25
  undeclared: error      # error | warn
  background: light      # light | dark, for ACI 7 foreground ink

linetypes:
  - name: SK_HIDDEN
    pattern_mm: [3.0, -2.0]       # paper mm: dash, gap
    description: "Hidden detail - 3 mm dash, 2 mm gap on paper"

layers:
  - name: FDN
    aci: 3
    lineweight_mm: 0.35
    linetype: CONTINUOUS
    description: "Foundations, slabs, plinths"
  - name: ZONE
    aci: 9
    lineweight_mm: 0.18
    linetype: SK_HIDDEN
    description: "Keep-clear / access zone (non-physical)"
```

Every layer needs an ACI because most drawing offices plot with CTB/STB rules keyed from indexed
colour. Optional `true_color: "#RRGGBB"` is written in addition to ACI, not instead of it. Optional
`pen: "#RRGGBB"` affects SVG/PDF pen ink only. ACI 7 resolves as foreground ink (`#111111` on light,
the CAD outline colour on dark) so white/black CAD layers do not disappear on a white SVG background.

Lineweights are authored in paper millimetres and snapped to the nearest valid DXF lineweight enum,
ties to the thinner value; out-of-range values fail instead of clamping. Custom linetypes are authored
in paper millimetres and require `--plot-scale N` for DXF so their patterns can be converted to model
metres. SVG uses the same table through `to_svg(..., pens=table)`, converting paper millimetres with
`DEFAULT_PX_PER_MM = 96/25.4` unless a caller supplies a sheet-specific factor.

Unknown keys, duplicate layer names (case-insensitive), reserved names (`0`, `Defpoints`), bad ACIs,
unknown linetypes and undeclared layers all fail loudly by default. `undeclared: warn` is the migration
escape hatch: it logs each undeclared layer once and creates an ACI-7 fallback record. The layer table
does not read or write drawing status; `WATERMARK` is just a layer and cannot promote a drawing to
ISSUED.

## Legibility checks

`technical_drawings_for_agents check` works on the emitted SVG in `out/`, not on a generator's internal model. That
lets it catch raw-string assembly mistakes and keeps existing emitters byte-identical. It uses only
standard-library XML parsing plus deterministic text metrics; no font files, rasteriser, or network
access are involved. If a sheet uses an SVG construct the parser cannot model (`<use>`, `skewX`,
unit-suffixed coordinates, positioned `tspan`, `slice`, etc.), the command fails with exit 2 and names
the offending element path.

Optional `legibility.yaml` in a drawing directory can carry thresholds and declarations. Prefer an
`out/<number>.legibility.json` sidecar written by the generator for declarations that come from the
same variables as the drawing; hand-written YAML duplicates facts and can drift.

```yaml
# legibility.yaml
min_overlap_px: 1.5
min_clearance_px: 2.0
severities:
  inset-fill: error
require: [legend-complete, verify-annotated]

panels:
  - name: "WTP COMPOUND detail"
    box_px: [96, 600, 456, 900]
    extent_m: [38, 25]
    kind: detail            # detail (default) | callout
legend:
  entries:
    - {key: clarifier, label: "Clarifier raft", colour: "#d9463b"}
  used_keys: [clarifier, dosing-skid]
verify:
  marker: "(v)"
  items:
    - {id: "STA-WTP-01", label: "FA-130 raft A"}
    - {id: null, count: 8}
```

Baselines are for known defects being carried temporarily, not for hiding new ones:

```yaml
baseline:
  - check: overprint
    message: "text 'Long value' overlaps text 'Next cell' by 5.0 x 7.0 px"
    reason: "Existing ISO title-block truncation defect; P9 owns the fix"
    issue: "#61"
```

`issued-gate` severity cannot be downgraded. No config, CLI flag, CI run, or checker result can move a
drawing to `ISSUED FOR CONSTRUCTION` or populate an approver field.

### Declared regions: `kind`, and how the fill ratio is computed

A `panels:` entry declares a **region** of the sheet. `kind` says which of the two shapes it is, and
that is the only thing it changes:

| `kind` | What it is | `panel-clear` | `inset-fill` |
|---|---|---|---|
| `detail` (default) | An opaque inset that replaces the main view inside its box | applies — nothing may intrude | applies |
| `callout` | A box drawn *over* the main view to frame a cluster of features | **does not apply** — main-view geometry legitimately runs across its boundary | applies |

`inset-fill` applies to **any** declared region, because a region that claims far more extent than its
content covers is misleading whichever shape it is.

**How `extent_m` becomes a ratio.** The declaration carries a box in pixels and an extent in metres,
but no panel scale, so the content's extent in metres is *inferred* from the two boxes:

```
content_extent_m = content_bbox / panel_box * declared_extent_m
```

The ratio the rule tests is `content_extent_area / declared_extent_area`, which under that inference
reduces exactly to the pixel-area ratio `content_bbox.area / panel_box.area` — so the declared and
undeclared forms of the rule are one number computed one way, not two that can disagree. `extent_m` is
still required for the declared form, because it is what lets the finding report the miss in metres
("content 38.0 x 25.0 m inside declared 87.0 x 48.0 m") instead of a bare fraction. Thresholds differ
between the two forms (`inset_extent_fill_min` 0.50 vs `inset_content_fill_min` 0.45) because an
undeclared panel's inner padding is legitimately unusable.

Content excludes the region's own boundary or backing rect, its title text, and its scale bar — those
are furniture, not detail.

### `marker-occlusion`

Numbered schedule markers drawn on top of each other. A *marker* is a badge: an opaque shape carrying
exactly the number that sits inside it. Two markers occlude when their ink boxes overlap by more than
`min_overlap_px`, so the radius is set by the markers' own geometry plus the one print-physics
threshold already derived here (1.5 px = 0.4 mm at the A3 canvas) rather than a new absolute distance.
`marker_cluster_min` (default **2**, minimum 2) is how many markers at one placement make a finding;
one marker cannot occlude itself.

The check is deliberately narrow so it stays quiet on real sheets. A badge must enclose *nothing but*
marker numbers, which is why an ISA instrument bubble — whose tag is drawn on two lines, `FIC` over
`101` — is never read as a stack of markers, and why concentric shapes of one symbol count once.
`overprint` cannot cover this case: its badge rule deliberately exempts a number sitting inside a
bubble, so it cannot see a second bubble stacked on the first.

## Paper-space sheets: a declared paper size and an asserted plot scale

A `ViewBox` sheet is a **pixel canvas**: it auto-fits the extent into whatever canvas you chose, so
the px/m ratio is an accident of the canvas size and the ratio printed in the title block is a claim
nobody re-checks. That is not a hypothetical failure — a live site GA claimed `1:1250 (A3)` while
plotting at about **1:1157** on a page that was **not A3**. Read 43.2 mm off that sheet at the stated
1:1250 and you get **54.0 m** where the model says **50.0 m**: an 8 % error, invisible to review.

`technical_drawings_for_agents.sheet` makes the scale a **derived fact** instead of a typed string. Adding a `sheet:`
block to a drawing's `meta.yaml` is the *only* opt-in switch; a drawing without one behaves exactly as
before, byte for byte.

```yaml
# meta.yaml — everything outside `sheet:` is unchanged.
number: STA-SITE-GA-001
title: WTP Site General Arrangement
revision: B
status: CONCEPT
for_construction: false
# scale: omitted on purpose — it is DERIVED once `sheet:` is present.

sheet:
  size: A3                       # required: A0 | A1 | A2 | A3 | A4 (ISO 216)
  orientation: landscape         # optional, default landscape
  margins_mm: 10                 # optional; a number, or {left, right, top, bottom}
  reserve:                       # optional; strips cut off the frame IN LISTED ORDER
    - {name: title-block, edge: bottom, size_mm: 60}
    - {name: legend,      edge: right,  size_mm: 80}
  viewport:                      # required
    extent_m: [788392, 322125, 788702, 322395]   # [xmin, ymin, xmax, ymax], model metres
    scale: 1250                  # positive integer denominator, or the string "fit"
    rotation_deg: 0.0            # optional; counter-clockwise about the extent centre
    on_overflow: error           # optional, default "error"; or "fit"
    align: center                # optional; only "center" in v1
  north:                         # optional; omit and no arrow is derived or required
    model_bearing_deg: 0.0       # bearing of north from model +y, clockwise positive
    label: "N"
  scale_bar:                     # optional
    length_m: null               # null => derived as a round {1,2,5}x10ⁿ length
    divisions: 5
    unit: m                      # m | km
```

```bash
technical_drawings_for_agents sheet drawings/basin-site/meta.yaml
```

```
paper:            A3 landscape 420 × 297 mm
margins_mm:       left 10, right 10, top 10, bottom 10   (house default 10 mm — not an ISO 5457 citation)
drawing frame:    x 10, y 10, 400 × 277 mm
reservations:
  - title-block: x 10, y 227, 400 × 60 mm
  - legend: x 330, y 10, 80 × 217 mm
viewport frame:   x 10, y 10, 320 × 217 mm
extent_m:         [788392, 322125, 788702, 322395]
rotation_deg:     0
required_mm:      248.000 × 216.000   (fits: True)
scale:            1:1250   (ISO 5455 preferred: False)
mm per model m:   0.8
scale bar:        100 m = 80.000 mm, 5 divisions, unit m
north:            0.000° on paper (model bearing 0°, label 'N')
```

### What it guarantees

- **One SVG user unit is one millimetre of paper.** The root is
  `width="420mm" height="297mm" viewBox="0 0 420 297"` — no dpi, no `preserveAspectRatio` guesswork.
- **`mm = m * 1000 / N`, in Python.** Never an SVG `transform="scale()"`, which would divide every
  stroke width and font size by N too.
- **The printed ratio, the bar geometry, the tick labels and the north bearing are all computed.** The
  new `sheet_scale_bar` has **no caption parameter at all** — the free `label=` slot on the legacy
  `svg_scale_bar` is exactly how a hand-spaced `"0                50 m"` fake tick row got painted over
  a correct one, so the slot is gone and validation rejects any extra text node in the bar group.
- **Loud failure, never silent degradation.** An extent that does not fit at the stated scale raises,
  naming the smallest scale that *would* fit. It never clips and never auto-fits unasked; if you want
  a refit, say `on_overflow: fit` and the resolved (round) scale is what gets printed and recorded.
  Degenerate input — inverted or zero-width extents, `nan`, `scale: 0`, a `true` where a number
  belongs, an unknown key — is an error naming the offending YAML path, never a "sensible default".
- **The sheet describes itself**, in one `<metadata id="technical_drawings_for_agents-sheet">` element of sorted,
  whitespace-free JSON with no timestamp, path or version in it. That is what lets `validate` check a
  *file* without re-running its generator.

### What `validate` then checks

Only when that record is present — so a legacy sheet can never acquire a new problem or warning:

| | Check | Severity |
|---|---|---|
| V1–V2 | root `width`/`height` carry `mm` and match the recorded paper size; `viewBox` is the paper box | problem |
| V3 | the record agrees with itself about its own scale (`text`, `mm_per_m`, `denominator`) | problem |
| V4–V5 | the extent's required millimetres, recomputed, match the record **and** fit the viewport frame | problem |
| V6–V7 | the bar is drawn the width its length and scale imply, and its text nodes are *exactly* the derived tick set plus the derived ratio | problem |
| V8 | a hand-typed `meta.scale` equals the derived `"1:N"` exactly (`"1:1250 (A3)"` does not) | problem |
| V9 | drawn geometry stays inside the viewport frame | **warning** |
| V10 | the ratio is an ISO 5455 preferred (1/2/5-decade) one | **warning** |
| V11 | a declared `north:` is drawn at the derived paper bearing | problem |

V8's fix direction is deliberate: the derived value wins, and the right resolution is normally to
**delete** the hand-typed string. Nothing here rewrites your `meta.yaml` — a tool silently editing a
drawing's own record is the same category of act as a tool flipping its status.

### Writing a generator against it

```python
from technical_drawings_for_agents.sheet import SheetDrawing, load_sheet_config
from technical_drawings_for_agents.svg import svg_rect

sheet = load_sheet_config("meta.yaml")          # None when there is no sheet: block
drawing = SheetDrawing(sheet=sheet, status="CONCEPT")
x0, y0 = drawing.vp.point(788462, 322195)       # model metres -> paper millimetres
x1, y1 = drawing.vp.point(788522, 322235)
drawing.add(svg_rect(x0, y1, x1 - x0, y0 - y1, stroke="#111111", stroke_width=0.35))
drawing.add_model((788462, 322195), (788522, 322235))   # record points for the containment check
open("out/STA-SITE-GA-001.svg", "w").write(drawing.render())
```

> ⚠️ **The unit trap, and it is the most likely first mistake.** The legacy `svg_*` primitives default
> to `stroke_width=1` / `1.5` and `font_size=11` — **pixel** defaults, about 0.26 mm at 96 dpi. In a
> millimetre sheet those become 1.0–1.5 mm lines and 11 mm text, i.e. 4× to 40× too heavy. Pass
> explicit millimetre widths and font sizes. `LW_DEFAULT_MM = 0.25` covers this module's own furniture
> only; the real lineweight table is a follow-up (#59).

`Drawing` (pixels) and `SheetDrawing` (millimetres) are deliberately **two types with no shared base
class**: the most dangerous mistake available here is a number in the wrong unit, and two types keep
the unit visible at every call site. `Viewport` is re-exported at package level as `SheetViewport` for
the same reason — sitting next to `ViewBox` under its own name it would invite exactly that confusion.
Import it as `technical_drawings_for_agents.sheet.Viewport` when you want the plain name.

### Not settled, and not guessed at

- **Margins.** The 10 mm default is a **house default**, not an ISO 5457 citation — that standard's
  frame margins (including the wider filing edge) are not in hand. A sheet that cares should state
  `margins_mm` explicitly.
- **Furniture dimensions** — bar height 3 mm, tick overshoot 1.5 mm, the label baselines, the 18 mm
  north arrow — are house constants of the module, stated so two implementations agree. Not from a
  drafting standard.
- **Grid vs true north.** `model_bearing_deg: 0.0` means "model +y is taken as north", which in a
  projected CRS is *grid* north — up to about 3° from true north in a UTM zone. It is a declared
  assumption, not a computed fact; meridian convergence is #60's.
- **The title block is #61's.** `SheetDrawing` refuses `title_block=` fields. When the sheet reserves
  a `title-block` region it emits an **empty** placeholder group carrying that rectangle, so P9 can
  fill it without moving the reservation; with no reservation nothing is emitted and `validate` reports
  its usual missing-title-block problem. A px-sized stub block in a mm sheet would be a wrong
  artifact, and a wrong artifact is worse than a missing one.
- **One viewport per sheet** in v1. Detail insets at a second scale (each needing its own derived bar
  and asserted ratio), anisotropic/vertically exaggerated views, non-A-series formats, DXF paper space
  and `align:` corner options are all tracked follow-ups.

## Components (to-scale, georeferenceable equipment)

The `components` engine is for real-metre equipment geometry, not schematic ISA symbols. A component is
declared once in YAML, placed at a drawing-local origin or a projected GIS coordinate, and emitted to
both worlds: GeoJSON for GIS/QGIS workflows, and SVG/DXF for drawing outputs. The local convention is
GIS-friendly: origin at the component anchor, `+x` east and `+y` north.

Project-specific component specs live in project folders outside this repo. The package only carries a
neutral worked example at
`src/technical_drawings_for_agents/components/examples/packaged_unit.yaml`.

Minimal schema:

```yaml
component:
  id: EXAMPLE-PACKAGED-UNIT
  name: Example packaged unit
  units: m
views:
  plan:
    features:
      - role: raft
        layer: FDN
        source_status: sourced        # verified | sourced | as-received | verify
        hatch: concrete               # metadata only; the engine does not apply hatches
        shape:
          rect: {size: [6.0, 4.0], center: [0.0, 0.0]}
      - role: nozzle
        layer: NOZZLE
        tag: N1
        shape:
          point: [2.5, 0.0]
      - role: label
        text: PACKAGED UNIT
        at: [0.0, 0.25]
```

Primitive shapes are `rect`, `circle`, `line`, `polyline`, and `point`; a feature's `shape` must
contain exactly one primitive. Labels use `role: label` with `text` and `at` instead of `shape`.

Supplier DXF geometry can be referenced directly with `from_dxf`. The DXF file is resolved relative
to the component spec file when `file` is not absolute. The extractor keeps only supported entities
fully inside `region`, converts coordinates to local metres with `(x - origin.x) * scale` and
`(y - origin.y) * scale`, and materialises ordinary `polyline`/`circle` features before placement or
emission. DXF `+y` stays local `+y`.

```yaml
- role: vendor-geometry
  layer: EQUIP
  source_status: sourced
  tag: null
  from_dxf:
    file: vendor_fixture.dxf
    region: [0, 0, 4000, 3000]       # DXF units; entity vertices must all be inside
    units: mm                         # mm | cm | m | in | ft
    origin: [1000, 500]               # DXF point that maps to local [0, 0]
    include: [line, lwpolyline, arc, circle]
    layers: [VENDOR]                  # or null for all layers
    min_length_m: 0.15
    arc_segments: 24
    cleanup:
      - {drop: diagonal, angle_tol_deg: 3}
      - {drop: shorter_than, length_m: 0.05}
      - {drop: outside, footprint_m: [6.0, 4.0], margin_m: 0.1}
      - {drop: layers, layers: [DIMS, CALLOUTS]}
      - {drop: region, region_local_m: [2.4, -0.4, 3.0, 0.4], margin_m: 0.0}
      - reclassify:
          match: {kind: circle, dxf_layer: VENDOR, r_min_m: 0.09, r_max_m: 0.11}
          set: {role: manhole, layer: ACCESS, source_status: verify}
```

`LINE` and `ARC` become open polylines, `LWPOLYLINE` becomes an open polyline or polygon depending
on its closed flag, and `CIRCLE` remains a circle with a metric diameter. The feature's semantic
fields (`role`, `layer`, `tag`, `source_status`, and `hatch`) are copied to every extracted entity.

`cleanup` is optional. When present, it is an ordered list applied after DXF extraction and local-metre
conversion, before placement. The original DXF layer is kept as cleanup-only metadata named
`dxf_layer`; the feature `layer` still comes from the component spec unless a `reclassify` rule sets it.

Cleanup rules:

- `{drop: diagonal, angle_tol_deg: 3}` drops 2-point line features that are not near horizontal or
  vertical within the tolerance. Multi-vertex polylines, polygons, arcs, and circles are untouched.
- `{drop: shorter_than, length_m: 0.05}` drops line/polyline features whose path length is below the
  threshold. Circles are untouched.
- `{drop: outside, footprint_m: [w, h], margin_m: 0.1}` drops features with any vertex outside the
  centred footprint box. Use `region_local_m: [x0, y0, x1, y1]` instead of `footprint_m` for an
  explicit local-metre box.
- `{drop: layers, layers: [DIMS]}` drops features whose source `dxf_layer` is in the list.
- `{drop: region, region_local_m: [x0, y0, x1, y1], margin_m: 0}` drops features with all vertices
  inside the local-metre box.
- `{reclassify: {match: {...}, set: {...}}}` updates matching survivors. `match` supports `kind`
  (`polygon`, `polyline`, `line`, `circle`), `dxf_layer`, circle radius bounds `r_min_m`/`r_max_m`,
  and `region_local_m` for features fully inside a box. `set` may override `role`, `layer`, `tag`, and
  `source_status`.

CLI examples:

```bash
technical_drawings_for_agents component src/technical_drawings_for_agents/components/examples/packaged_unit.yaml \
  --view plan --emit geojson --origin 788609.589,322341.151 --rot 0 --out /tmp/pkg.geojson

technical_drawings_for_agents component src/technical_drawings_for_agents/components/examples/packaged_unit.yaml \
  --view plan --emit svg --origin 0,0 --out /tmp/pkg.svg
```

Add `--layers PATH|default` to opt into layer-table SVG/DXF pens. DXF custom linetypes also need
`--plot-scale N`, the denominator in 1:N, so paper-mm dash patterns can be written in model metres.

For many instances of **one** component, pass `--place placements.yaml`:

```yaml
instances:
  - {tag: PKG-01, origin_utm: [788609.589, 322341.151], rotation_deg: 0}
  - {tag: PKG-02, origin_utm: [788622.000, 322345.000], rotation_deg: 90}
```

For a whole **site** — many component *types*, laid out in a GIS editor and checked
against layout rules — use `technical_drawings_for_agents layout` below.

## Site layouts (`technical_drawings_for_agents layout`)

The workflow this exists for: a human drags real-metre footprints around in QGIS over
an orthophoto (seeing actual shapes and orientations against the imagery, which is the
only honest way to site equipment), and everything downstream — drawings, 3D, spatial
BOQ — regenerates from that. The rule that makes it reviewable: **the GIS layer is an
editor, not the source of truth.** The truth is text in git.

```
QGIS editable layer  ──export──►  export.geojson
                                       │  --from-geojson
                                       ▼
                            placements.yaml  ◄── GENERATED, git-tracked, diffable
                                       │
                        layout.yaml ───┤ type ──► component spec(s)
                                       ▼
                        placed geometry + layout findings ──► GeoJSON / DXF
```

```bash
# refresh the register from the editor, place everything, check, emit
technical_drawings_for_agents layout site_layout.yaml --from-geojson export.geojson \
    --emit geojson --out out/layout.geojson

# just re-check what's committed (CI-friendly)
technical_drawings_for_agents layout site_layout.yaml --check-only
```

`--from-geojson` derives, per footprint, its **centroid** (the anchor), its **long-axis
bearing** normalised to `[0, 180)` (a rectangle has no front, so a bearing and its
reverse are one pose) and its **as-placed size** — then writes the register. There is no
rotation attribute to keep in sync: pose is read from geometry.

Worked example: `src/technical_drawings_for_agents/components/examples/site_layout.yaml`.

```yaml
layout: {id: EXAMPLE-SITE, crs: EPSG:32630}
components:
  root: .
  types:
    packaged-unit: [packaged_unit.yaml]     # one type -> the specs stamped at that pose
editor:
  type_field: type
  tag_field: tag
  id_field: id                              # optional author-supplied durable id
  parked:
    - {name: palette row, bbox: [0, 900, 200, 1100]}   # not-yet-placed items
placements: site_placements.yaml            # generated register (or an inline list)
checks:
  - {check: parallel, within: packaged-unit, tol_deg: 1.0}
  - {check: clear-spacing, within: packaged-unit, min_m: 2.0, tol_m: 0.05}
  - {check: no-overlap}
  - {check: within-envelope, bbox: [0, 0, 100, 100], severity: warn}
  # Optional identity gate. Run it at severity: warn while an old register still
  # has gaps, then flip to error in the commit that finishes populating them.
  - {check: ids-present, severity: warn}
```

**Checks.** `parallel` (bearings agree within `tol_deg`), `clear-spacing` (true minimum
gap between rotated footprints ≥ `min_m`, less `tol_m` of placement slop),
`no-overlap` (separating-axis test), `within-envelope` (all four corners inside a bbox),
`ids-present` (every selected placement carries a durable `id` or `tag`).
Each takes `within:` to scope it to one or more types and `severity: error|warn`. An
`error` finding exits **1** — a layout that breaks a stated spec cannot quietly reach a
drawing. `--warn-only` downgrades that for exploratory runs.

**Groups — meet the spec by construction, not by hand.** Checks tell you a rule is
broken; a `groups:` snap rule stops it being broken. The human places the group roughly
where it belongs on the imagery, and the rule derives the spec-compliant pose from that
on **every** build — so the correction survives the next nudge, and survives being
cloned to the next site.

```yaml
groups:
  - name: FA-130 rafts
    within: clarifier         # or a list of types
    snap:
      bearing: mean           # mean (circular) | first
      pitch: spec             # spec = short edge + clear_m | as-placed
      clear_m: 2.0
      order: north-to-south   # north-to-south | south-to-north | as-listed
```

Members end up on one common bearing, evenly pitched along the axis perpendicular to it,
about **the group's own centroid** — the rule fixes the group's internals and never
relocates the group. It is idempotent, and every member that moved is **reported** with
how far and how much it turned: a silent correction would hide a placement that was
further out than its author realised. `parallel` and `clear-spacing` then pass by
construction and stay on as guards. `--no-snap` reviews the raw as-placed layout.

The mean bearing is *circular* — 179° and 1° average to 0°, not 90°.

`--emit-register` writes the **effective** (post-snap) placements. Use it whenever a
sheet, 3D build or BOQ reads poses as well as geometry, so both come from the same
place — reading the input register alongside snapped geometry would mix pre- and
post-snap positions.

**Stable register order — the diff is the change log.** A generated register is written
in a **canonical order**: `type`, then easting, northing, rotation, size, and identity
**last**. So re-exporting the editor layer in a different feature order changes no bytes;
one physical edit is one record's worth of diff; a delete renumbers nothing (nothing is
numbered); and populating or correcting an `id` moves no record, because identity is the
final tiebreak rather than the first. Two records tied on the *whole* key are two items at
one pose to the millimetre — a pasted copy that was never moved — and that is a hard
error, not a silent dedupe.

**Identity is authored, never invented.** No identity derivable from an editor export
alone is both stable under a move and non-renumbering under a delete, so the tool does not
fake one: determinism comes from the canonical sort, and the durable join key has to be
typed into the attribute form. A placement's identity is `id` if present, else `tag`, else
nothing. Identities are validated (`[A-Za-z0-9][A-Za-z0-9._-]{0,63}`) and unique within a
layout, case-insensitively, in a flat namespace — a copy-paste that duplicates a tag is a
hard error naming both poses. A placement with no identity is still allowed, but it is
**counted in the artifact**, so the gap is a diffable line that moves when it closes:

```text
# ids: 2 of 10 placements carry a durable id (8 identified by pose only).
```

| Flag | What it does |
|---|---|
| `--check-register` | Compares the register on disk with what the pipeline would write, and **never writes**. Alone it re-canonicalises what it loaded (catching a hand-edit or a stale order); with `--from-geojson` it compares against a fresh export instead of writing — the CI staleness gate. Prints a unified diff and exits **1** on drift. |
| `--canonicalise-register` | Rewrites the register in canonical order in place, from its own contents, with no export — the one-off migration for a pre-canonical register. Writes only if the bytes differ. Preserves the `# Derived from:` provenance line. Cannot be combined with `--from-geojson` or `--check-register`. |
| `--register-source TEXT` | Provenance to stamp into `# Derived from:` when canonicalising a register that has no such line (without it, that case is an error — a generated register with no provenance is not something to rubber-stamp). |
| `--require-ids` | Appends an `ids-present` check at `severity: error`, so CI can enforce identity coverage without editing the project YAML. |
| `--layers PATH|default` | Overrides or supplies the layer table used when emitting DXF. A `layers:` key in the layout YAML is resolved relative to that file. |
| `--plot-scale N` | Required for DXF output when the resolved layer table uses custom paper-mm linetypes. |

`--warn-only` does **not** downgrade register drift. A stale or non-canonical register is
not a finding about the design — it is the tool's output disagreeing with the tool's
input, and CI must not be able to wave that through.

Re-running `--from-geojson` on an unchanged export **skips the write** and reports
`register: unchanged (N placement(s))`, so the mtime holds steady for staleness checks. A
reported no-op is fine; a silent one is the defect.

**Behaviour change (was: write, then fail).** `--from-geojson` now rejects a derived
`type` that has no `components.types` entry **before writing a single byte**. The old flow
wrote the register and only failed later, on load — leaving a generated artifact on disk
that had never validated, which a subsequent run could then load. A failed refresh now
leaves no register, and never clobbers a good one.

**Nothing is skipped silently.** A placement whose `type` has no `components.types`
entry is an error, not a shrug; so is a non-polygon footprint, a missing `type`
attribute, a non-rectangular footprint, or a spacing check on a placement with no
recorded size. Parked items are excluded only by a **named zone you declared**.

Spacing and overlap checks need the as-placed footprint (`size_m`), which
`--from-geojson` records. A hand-written register without it can still be placed — it
just can't be spacing-checked, and says so.

**Emitted properties.** Each feature carries its component's own `role` / `layer` / `tag`
/ `source_status` / `hatch`, plus the instance's `placed_type`, `placed_tag` and
`placed_id` (the last two only when set). The
instance tag is deliberately *not* written to `tag`: instance properties override
feature properties, so doing that would erase every component feature's own tag (an
FA-130's nozzle `N1` would come out carrying the unit's tag). Both facts matter, so
they get separate keys — style GIS layers on `role`, join equipment registers on
`placed_tag` / `placed_id`. An `id` gets the same treatment for the same reason: it is
emitted as `placed_id`, never as a bare `id`.

## Dimensions and setting-out (`technical_drawings_for_agents dimensions`)

A dimension declares **what** to measure — "the clear gap between raft `STA-A` and raft `STA-B`",
"the clarifier group's extent across its own bearing", "the easting offset of `STA-A` from the
surveyed point P5", "the run length of the raw-water interconnect". The tool computes **the number**
from the same placed geometry the sheet draws, using the same distance code the layout checks use. The
value on the sheet is a derived fact with a provenance chain (editor → register → snap rule →
footprint → distance function → text), not a transcription.

```bash
technical_drawings_for_agents dimensions STA-SITE-GA-001.dimensions.yaml --check-only
technical_drawings_for_agents dimensions STA-SITE-GA-001.dimensions.yaml \
    --emit svg --out out/dims.svg --setting-out out/setting-out.csv
technical_drawings_for_agents dimensions STA-SITE-GA-001.dimensions.yaml --emit json --out out/measurements.json
```

Worked example: `src/technical_drawings_for_agents/components/examples/site_dimensions.yaml`.

### The failure this prevents

`svg_dimension_h/v` take literal metre coordinates *and* a literal label string, so the drawn
geometry and the printed number are two independent facts maintained by hand. The moment a placement
moves, the annotation is silently wrong — and a wrong dimension is worse than a missing one, because
it is *believed*, and it gets set out on site. Both primitives keep working unchanged (pinned to
golden bytes and signatures); this is an additional path, not a replacement.

### Three properties, not three good intentions

**One source per number.** A `clearance` scalar is `polygon_gap(footprint(a), footprint(b))` — the
*same function objects* `check_layout`'s `clear-spacing` rule calls, re-exported from `layout.py` as
public aliases precisely so nobody writes a local copy. A sheet therefore cannot contradict its own
check, and the test asserts it against `check_layout`'s own finding text rather than against a
literal.

**The witness invariant.** `polygon_gap` returns a scalar, but a dimension needs two *points*, and for
two parallel rectangles the minimum-distance pair is **not unique** — a naive `argmin` lands the drawn
line at whichever corner the loop visited first and moves it when the input order changes. So the pair
comes from a deterministic two-branch rule: if the maximum edge-normal separation equals the gap, the
closest features are edge-to-edge and both anchors sit at the **midpoint of the facing overlap** (where
a drafter would put them); otherwise the pair is vertex-to-vertex, re-enumerated exactly as
`polygon_gap` itself enumerates, so the tie-break is `(polygon order, edge index, point index)` —
determined by the data, never by float noise. `render_dimensions` then asserts
`drawn_length == abs(value)` and **raises**. Shipping a line that spans one distance while its text
states another is not a bug to be caught in review; it is not expressible.

**Never invent a dimension.** Literal coordinates are legal *only* inside a `datums:` entry, and a
datum with no `source:` is rejected — so every literal in the file carries a citation and `git diff`
on `datums:` is a review of survey inputs. A `label` is a *prefix* (`CLEAR`, `DN200`, `3 off`) and is
**rejected at load if it matches `\d[.,]\d`**, because a decimal inside a caption is the one construct
that smuggles a hand-typed measurement onto a sheet. The rejection points at `expect_m`, which is
*checked* and never printed:

```
ERROR: dimension-expectation: DIM-001 (placement 'A' -> placement 'B') computed 1.943 m,
expected 2 m ±0.002 (FA-130 foundation slab dwg). The sheet shows 1.943 m
```

The spec value is the thing under test, not the thing printed — so "the drawing says 2.0 m because it
is supposed to say 2.0 m" cannot be expressed.

### The five kinds

| kind | keys | value |
|---|---|---|
| `clearance` | `from`, `to` (footprints) | minimum clear distance, from `polygon_gap` |
| `centres` | `from`, `to` (points) | straight-line distance between two origins |
| `envelope` | `of` (selector), `axis` | overall extent of the selection, projected on `axis` |
| `offset` | `from`, `to`, `axis` | **signed** component along `axis`; the drawn line *is* the component, not the hypotenuse |
| `chainage` | `route`, optional `from`/`to` stations | path length along a declared polyline |

`axis` is `easting`, `northing`, `principal`, `principal-cross`, `direct` (offset only), or
`bearing:<deg>`. Prefer `principal*`, which reads the bearing from the register so the dimension
follows the layout when it is re-snapped; a typed `bearing:` is supported (a survey baseline is a
legitimate declared input) but warns, because it usually duplicates a fact that already lives in the
register.

**Chainage is a leader note, never a straight dimension line.** A path length is not the distance
between its endpoints, so drawing a straight line between a route's ends and labelling it with the
route length would reproduce the same defect in a new form. There is also **no auto-routing**: a
router would invent geometry no source states, and its length would be a fabricated quantity feeding a
bill of quantities. A route is the polyline through the vertices you listed, in order.

### Referencing a placement, and the Basin data gap

A placement is referenced by its **stable id**, its **tag**, or an author-declared `naming` name bound
by a *position-independent* predicate (`type` plus register `properties`). There is deliberately **no
ordinal, index, "nth of type" or nearest-match fallback**, and `naming.where` rejects `origin_utm` /
`size_m` / `rotation_deg`: a positional binding breaks on exactly the move that a computed dimension
exists to survive. An unresolvable reference is a hard error that says what to do:

```
DIM-001: no placement keyed 'dosing-skid-2'. Known keys: STA-A, STA-B. 8 of 10 placements
have neither a stable id nor a tag and cannot be dimensioned. Fix by (a) setting the 'tag'
attribute in the QGIS placements layer and re-running `technical_drawings_for_agents layout --from-geojson`,
or (b) declaring a naming: entry with a property predicate
```

At Basin today only `STA-A` and `STA-B` are dimensionable, because only 2 of 10 placements carry a
durable id. That is a **data gap, not a tooling one**, and the tool says so rather than inventing a
handle — a partial drawing is better than a fabricated key.

### The setting-out table has no model-derived Z

Generated from the register, never retyped: the SVG table and the CSV/YAML sidecar are two renderings
of one `list[SettingOutRow]`, and `svg_setting_out_table` takes already-formatted **strings** and does
no arithmetic, so they cannot drift. Row ids are the *same* identity the dimensions use, so a
dimension and a table row can never mean different things by one name — and a placement with no
id/tag is a hard error, never a blank or an ordinal, because a blank in a setting-out table is a number
someone will set out.

`Placement` carries no elevation and `derive_placements` reads a 2-D centroid, so **`z:` is a
mandatory explicit policy with no default** — every possible default either invents a level or
silently omits one:

| `z.source` | Behaviour |
|---|---|
| `none` | every `Z` cell renders the literal `NOT SURVEYED`; `note:` is **required** and printed under the table; a `warn` finding makes the omission visible in the build log too |
| `property: <name>` | `Z` read from `placement.properties[<name>]`; any row missing it is a hard error naming the row. There is no partial column |
| `datum: <name>` | every row takes that datum's `z`, and the datum's source is printed in the caption |

At Basin the only honest declaration today is `z: {source: none}` citing P5's 56.947 m MSL and the
open platform-level gap — a stated open question on the face of the drawing, which is strictly better
than a plausible-looking number.

### Rounding, precision and text placement

`decimals` defaults to 3 and is **capped at 4**, because the register itself is 3 dp: a dimension can
never claim more precision than its input has. Formatting delegates to `provenance.fmt`, the package's
one float-to-text path (round-half-even on the exact binary value, negative zero normalised) — there
is deliberately no second rounding implementation. The rounded string is for the sheet only; findings
and the `--emit json` measurement register carry the full-precision `value_m`, so a rounded `2.000`
can never become an input to a later calculation.

`expect_tol_m` defaults to **0.002**, derived rather than chosen: the effective register rounds each
origin to 3 dp per axis, so a two-placement gap inherits ~1.4 mm of rounding. The real Basin raft gap
sits at 1.99985 m against a 2.0 m target — 1.5 mm short from rounding alone. A tighter default would
fail a conforming layout, and a check that cries wolf gets switched off.

Text placement is a **deterministic default plus author hints** (`text.side`, `text.along_px`,
`text.rotate`, `text.leader`) and never automatic relocation: auto-nudging would make a one-line data
change produce a diffuse SVG diff, would hide genuine crowding, and would duplicate the legibility
checks. `render_dimensions` instead returns an `AnnotationBox` per dimension (plus one for the table),
sized with the legibility module's own text metric, for those checks to judge.

The `*_px` style knobs are **paper-space** lengths in the projector's own units — an extension line
must be the same physical size on the sheet whatever the plot scale. `render_dimensions` accepts any
projector exposing `point(x, y)` with y increasing down the page, so both `svg.ViewBox` (SVG pixels)
and the paper-space `sheet.Viewport` (millimetres) work unchanged.

### Degenerate geometry fails loudly

A measurement that cannot be computed **raises**; one that is computable but suspicious renders with a
finding. Nothing renders blank, nothing renders `0.000` as a stand-in for "unknown", and nothing
quietly drops itself from the sheet (which `Drawing.add`'s falsy filter would otherwise allow).

- **Overlapping footprints** in a `clearance`: hard error. `polygon_gap` collapses interpenetration
  and contact to the same value, so a zero clearance there would state that they touch when they do
  not.
- **Touching footprints** (a real 0 mm gap): renders `0.000 m` as a leader note plus a
  `dimension-zero` warning. Abutting slabs are legal geometry; a zero on a GA is still usually a slip.
- **Coincident origins / a zero axis component**: `on_zero: error` (default) raises;
  `on_zero: leader` states it as a note. Neither choice is silent.
- **An empty `envelope` selection**: hard error naming the selector. An empty extent is not `0.000`;
  it is a selector that matched nothing.
- **A non-parallel group with `axis: principal`**: hard error quoting the worst deviation. "The
  group's own bearing" is undefined for a non-parallel group, and a mean would produce a foreshortened
  extent that looks plausible.

### Exits and scope

Exactly the `layout` contract: **0** clean, **1** computed and wrong (an `error`-severity finding;
`--warn-only` downgrades), **2** could not compute (a load, validation or geometry failure, or
`--emit` without `--out`).

`snap: effective` is the default, so the measured poses are the post-snap ones the sheet draws.
`--no-snap` / `snap: as-placed` is for review only and is stamped into the console line and every
measurement's provenance; measuring pre-snap while drawing post-snap is not expressible.

The command is **read-only** on the register, the layout config and `meta.yaml`. It never nudges a
placement to make a dimension pretty, and it never touches `status` or `for_construction`: a fully
dimensioned, setting-out-tabled sheet is still `CONCEPT — NOT FOR CONSTRUCTION` until the responsible
engineer signs it. Out of scope here: quantities aggregation (`--emit json` is the interface it will
consume), DXF `DIMENSION` entities, radial/angular/ordinate/chain dimensions, and automatic
dimensioning — which dimensions a drawing carries is an engineering judgement about what a builder
needs.

## Ingesting supplier DWGs (`technical_drawings_for_agents.ingest`)

The `.dwg`-in boundary: take an existing supplier `.dwg` and produce a clean, editable,
**layout-insertable** DXF. We **extract, never redraw** — the converter preserves the vendor's real
dimensions, so ingest honours "never invent a dimension". Pipeline (verified end-to-end on the vendor
FA-130):

```
DWG ─[convert_dwg / ODA]→ DXF ─[load_clean / ezdxf]→ doc ─[improve]→ clean DXF ─[render]→ PDF/PNG
```

```bash
# needs ODA File Converter for a .dwg; pass an already-converted .dxf to skip that step
technical_drawings_for_agents ingest FA-130.dwg --out clean/ --isolate --corrections clean/corrections.yaml
technical_drawings_for_agents render clean/FA-130_clean.dxf     # INSERT-heavy -> LibreOffice automatically
```

CLI pipeline order: **load → improve (clean) → strip-tags → isolate → corrections → save.**
`strip-tags` runs by default (`--no-strip-tags` to keep them); `--isolate` and `--corrections` are opt-in.

Module API (`from technical_drawings_for_agents import ...`):

- `convert_dwg(dwg_or_dir, out_dir, version="ACAD2018")` — **ODA File Converter** wrapper (the
  converter of record; LibreDWG's DXF export is malformed). Locates `/Applications/ODAFileConverter.app`,
  runs headless on the **`cocoa`** Qt platform, folder in/out, ASCII-stages filenames (CJK-safe).
  Returns the clean DXF path.
- `load_clean(dxf)` — `ezdxf.readfile` with `ezdxf.recover.readfile` fallback; returns `(doc, audit)`
  where `audit` counts entities/dimensions/inserts/text/blocks.
- `improve(doc, font=…, units="mm", recolor_text_black=True, set_extents=True)` — consolidate text
  styles → one TTF; set `$INSUNITS`/`$INSBASE`; recompute extents (`ezdxf.bbox.extents`); recolour text
  to black **in modelspace and inside block definitions** (vendor text is often BYBLOCK).
- `strip_tags(doc)` — delete vendor CAD-template artifacts that render as stray text: floating
  **ATTDEF**s in modelspace and title-block-generator tags (`!GENTITLE-INSERT`, `GEN-TITLE-*`,
  `*GENST*`). These are automation placeholders, never real content (caught on the Basin pump GA).
- `isolate_sheet(doc, window=None)` — **sheet-count-aware** isolation. By default (no `window`) it
  splits at the largest x-gap yielding two *substantial* clusters, counts how many carry a title-block
  marker (图号 / 审定 / Approval / …), and **isolates the English sheet only when ≥2 title-blocks
  exist** — otherwise it **keeps the whole drawing**. This fixes the dropped-view bug: a single sheet
  with gapped view-groups (e.g. a pump GA with a detached plan) also gap-splits, but the extra group has
  no title block, so a naive split dropped a view. Erring toward keep-all is the safe failure mode.
  Pass `window=(x0,y0,x1,y1)` for the legacy explicit-guard-box path. `crop_pdf(pdf, out)` — tight
  non-white crop of a rendered page (needs the `ingest` extra).
- `apply_corrections(doc, corrections)` — apply a **versioned corrections overlay** (see below).
- `recolor(...)` / `normalise_text(...)` — recolour helpers (vendor drawings arrive in arbitrary ACI
  colours).
- `DxfBuilder.add_block_from_dxf(dxf)` + `.insert(name, point, scale, rotation)` + `DxfBuilder.read_dxf(path)`
  — read a cleaned vendor DXF and drop it into our layout as a **block** at a point/scale/rotation.

### Corrections overlay (`corrections.yaml`)

The vendor DWG stays the **untouched boundary source**. Any edit we make is captured as a git-tracked,
reviewable **data overlay** — a YAML file in the project folder beside the drawing — applied by the code
after clean/isolate and **before render**. A `git diff` of the overlay is the change log; the
"text is the source of truth" ethos of the standard extends to our corrections.

```yaml
drawing: "PT-500 flocculant dosing device"   # human label
source:  "絮凝剂加药装置PT-500.dwg"            # the untouched vendor file
corrections:
  - {op: replace_text, find: "30L/h、50m、60w", with: "30 L/h, 50 m, 60 W",
     note: "normalise Chinese comma + unit case (no value change)"}
  - {op: add_text, at: [7595, 13860], text: "SLS200-250GB", height: 120, layer: TEXT,
     note: "item-3 model from vendor packing list p.2 — VERIFY"}
  - {op: hide, near: [1234, 5678], note: "stray template dot"}
```

Ops: **`replace_text {find, with}`** (translate / normalise existing text), **`add_text {at:[x,y],
text, height?, layer?, color?, rotation?}`** (fill a blank cell / add a note in real drawing-unit
coords), **`hide {near:[x,y]}`** (delete the nearest entity — drop a stray mark).

**Never invent a value.** Every op is explicit human-authored data and must carry a `note` recording
intent/provenance. `add_text` is the invention-risky op (it introduces a value not in the vendor file),
so its `note` is **required** and must cite the real source (packing list / supplier confirmation) and
flag `VERIFY` where unconfirmed — mirroring the "drawing-verification item" rule. No geometry is ever
fabricated. Pipeline order: MTEXT→TEXT normalisation (if used) runs *before* corrections so
`replace_text` matches plain strings; corrections run *before* render.

**External tools & availability.** `convert_dwg` needs **ODA File Converter** (proprietary — install it
or set `ODA_CONVERTER`); the LibreOffice render path needs **`soffice`** on PATH (or set `SOFFICE_BIN`).
The PDF-crop helper needs the extras: `pip install -e "technical_drawings_for_agents[ingest]"` (PyMuPDF + numpy).

## ISA-5.1 P&IDs (`technical_drawings_for_agents pid`)

A real P&ID needs **symbol geometry + explicit positional layout** — which DOT can't do (it only
places *topology*). So `pid` works differently from `bfd`: **you** place every symbol at a coordinate,
and lines connect **named ports**. Three concerns stay separated — **content** (a data YAML),
**geometry** (the `technical_drawings_for_agents.isa` symbol library), and **sheet** (the shared ISO house chrome in
`technical_drawings_for_agents.isosheet`, identical to `bfd`, plus the required status watermark).

```bash
technical_drawings_for_agents pid drawings/example/synthetic-pid/SYN-PSK-PID-001.pid.yaml
technical_drawings_for_agents render drawings/example/synthetic-pid/out/SYN-PSK-PID-001.svg
technical_drawings_for_agents validate drawings/example/synthetic-pid/SYN-PSK-PID-001.pid.yaml
```

See the worked, **synthetic** example in
[`drawings/example/synthetic-pid/`](drawings/example/synthetic-pid/): duty/standby pump skids feeding a
transmission spine to a battery limit — the same *shape* the first real drawing (`STA-PSK-PID-001`)
will take. It is **made-up data, not real Basin content**, and renders end-to-end (SVG + PDF/PNG) and
passes `validate`.

### The `pid` YAML schema

Coordinates are in **inner drawing units**, origin top-left, **+y downward** (SVG convention). Layout
is fully **positional and deterministic** — nothing is auto-placed. Cloning to another site is a data
edit (change `at:` coords / tags), never a code change.

```yaml
meta:
  number: SYN-PSK-PID-001                 # stable drawing ID (also the output filename stem)
  title: Synthetic Pump Skids …
  subtitle: ISA-5.1 P&ID — worked example
  doctype: P&ID (ISA-5.1)                  # title-block DOCUMENT TYPE
  status: CONCEPT — NOT FOR CONSTRUCTION   # title-block DOCUMENT STATUS text (shown in red)
  watermark: CONCEPT                        # diagonal watermark key: DRAFT | CONCEPT | ISSUED
  rev: A
  date: 2026-07-16
  client: Example Client                  # optional title-block fields
  programme: Water Supply
  logos: {owner: ../../assets/owner.png, client: ../../assets/client.png}   # optional, embedded as data URIs
  # logo_cells: [[owner, OWNER], [client, CLIENT]]   # optional: keys + placeholder labels

sheet:  [1600, 1000]     # outer ISO sheet size (px); default [1600, 1000]
canvas: [1010, 560]      # inner drawing extent (units); omit to auto-fit from symbol bboxes

equipment:               # placed symbols with a tag drawn externally
  - {id: P-01A, type: pump, tag: "=SYN +S1 -P01A", at: [400, 250], caption: Duty}
  - {id: VG-01A, type: valve, tag: "=SYN +S1 -VG01A", at: [300, 250], kind: gate, actuator: diaphragm}
  - {id: T-01, type: tank, tag: "=SYN +BT -T01", at: [110, 340], w: 64, h: 170}
  - {id: DS-01, type: dosing_skid, tag: "=SYN +DOS -PKG", at: [235, 150], w: 150, h: 86, label: DOSING SKID}
  - {id: BL-01, type: tie_in, tag: "=SYN +TX -BL01", at: [910, 340], label: plant}

instruments:             # ISA bubbles; tag is drawn INSIDE the bubble
  - {id: PT-01, type: instrument, tag: PT-101, mount: field, at: [590, 150]}
  - {id: FIC-01, type: instrument, tag: FIC-101, mount: dcs, at: [780, 150], variable: F, functions: IC, number: 101}

lines:                   # connect PORTS: "<equipment-id>.<port>"
  - {id: L-A1, type: process, from: VG-01A.e, to: P-01A.suction}
  - {id: L-D-A, type: process, from: VG-02A.e, to: FCV-01.w, waypoints: [[700, 250], [700, 340]]}
  - {id: L-FC, type: pneumatic, from: FIC-01.s, to: FCV-01.n}

loops:                   # optional control-loop grouping (validated; members must exist)
  - {id: FIC-101, tag: FIC-101, members: [PT-01, FIC-01, FCV-01], description: Flow control loop}

notes: [ … ]             # free-text notes band
```

**Symbol types** (`type:`) and their parameters (all sizes in drawing units; every symbol also takes
`scale` and `rotation` in degrees):

| type | key params | notes |
|------|-----------|-------|
| `pump` | — | centrifugal; triangle apex = discharge (E) |
| `valve` | `kind` = gate / globe / butterfly / check; `actuator` = manual / diaphragm / motor / solenoid | bow-tie body |
| `instrument` | `mount` = field / panel / dcs; `variable`, `functions`, `number` (else parsed from `tag`) | ISA bubble |
| `vessel` / `tank` / `filter` | `w`, `h` | vessel = capsule, filter = meshed box |
| `dosing_skid` (`skid`) | `w`, `h`, `label` | dashed package boundary |
| `tie_in` | `label` | battery-limit / off-page pentagon |
| `flow_arrow` | — | decoration (auto-added mid-line on process lines) |

**Port convention.** Every symbol exposes the four compass edge ports **`n` / `e` / `s` / `w`** plus
**semantic aliases** where they help: a pump has `suction` (W) and `discharge` (E) with `in`/`out`
aliases; a valve/vessel has `in` / `out`; a tie-in has `conn`. A line endpoint is written
`<id>.<port>` (e.g. `P-01A.discharge`). Ports rotate/scale with their symbol, so a rotated symbol's
ports stay correct. Query them in code via `technical_drawings_for_agents.isa.build_symbol(type, x, y, **params).ports`.

**Line types** (`type:`) are drawn distinctly per ISA-5.1: **`process`** (heavy solid + flow arrow),
**`signal`** (thin solid instrument-to-process lead), **`electric`** (dashed), **`pneumatic`** (solid
with `//` cross-ticks). **Routing is orthogonal**: without `waypoints` a single L-bend is inserted
between the two port stubs; with `waypoints: [[x,y], …]` you control the mid-route explicitly.

### Tags come from the register, not from code

The tool **draws the tags the data declares — it never mints one.** In production the tags in a
`*.pid.yaml` must reference the project's **shared tag register** (e.g.
`parts-db/<project>.tags.yaml`) using IEC `=system +location -component` designations; keeping the register as the single
source means the P&ID, the parts DB and the schedule all agree. The synthetic example uses `=SYN …`
placeholders precisely so it can't be mistaken for real, register-backed tags.

## Drawing-directory layout

```
drawings/<discipline>/<drawing-id>/
  meta.yaml     # number, title, revision, scale, units, date, status, tool, for_construction flag
  source.py     # the versioned source of truth (script) — or an ASCII .dxf / .dot
  inputs.yaml   # design-basis parameters the generator reads (if script-driven)
  out/          # generated SVG / PDF / DXF  (build artifacts)
```

- **Numbering & revision:** a formal number + rev (e.g. `STA-WTP-GA-001_RevB`). The number is the
  stable ID — never reuse it for a different drawing.
- **Units:** SI, metres, explicit. The `ViewBox` maps real→canvas so the SVG is scale-true; a scale
  bar goes on every sheet (never trust on-screen size).
- **Every sheet carries:** border + title block + scale bar + north arrow (plan) + status watermark.

See the worked example in [`drawings/example/simple-section/`](drawings/example/simple-section/): a
concrete-lined drainage-channel typical section generated end-to-end (SVG + DXF + PDF/PNG), carrying
the `CONCEPT — NOT FOR CONSTRUCTION` watermark.

```bash
python drawings/example/simple-section/source.py          # -> out/EXA-CIV-SEC-001.svg + .dxf
technical_drawings_for_agents render drawings/example/simple-section/source.py
technical_drawings_for_agents validate drawings/example/simple-section
```

## Status flow — DRAFT → CONCEPT → ISSUED

The status watermark is a **hard requirement** on every sheet and is recorded in `meta.yaml`:

| status   | watermark                        | meaning |
|----------|----------------------------------|---------|
| `DRAFT`  | `DRAFT`                          | work in progress, not yet a coherent concept |
| `CONCEPT`| `CONCEPT — NOT FOR CONSTRUCTION` | a coherent concept/draft; **agent/code-generated drawings stay here** |
| `ISSUED` | `ISSUED FOR CONSTRUCTION`        | released for build — **only after the responsible engineer signs off** |

`ISSUED` (and `for_construction: true`) flips **only after engineer sign-off**. Safety-bearing content
(structural, pressure, electrical, lifting geometry) is **never** auto-issued.

## Revision register, ISO 7200 title block, and drawing numbers

A `revision: B` with no date, no description and no initials cannot be reconciled against a site
copy — nobody can tell whether the rev B in someone's hand is *this* rev B. `meta.yaml` therefore
carries the drawing's actual history, and all three keys are **opt-in**: a `meta.yaml` without them
validates and renders exactly as it did before.

```yaml
# meta.yaml
number: STA-WTP-GA-001
revision: B                 # MUST equal revisions[-1].rev — the redundancy IS the check
revision_scheme: alpha      # alpha | numeric | alpha-then-numeric
numbering: shape-only       # omit to disable number enforcement entirely
revisions:
  - rev: A
    date: 2026-07-18
    description: NOT RECORDED (pre-register revision)
    by: AB
    pre_register: true      # honest about an absence; warns on every run
  - rev: B
    date: 2026-07-24
    description: Ponds relocated south of the access road
    by: AB                 # required — a revision nobody authored did not happen
    chk: CD                 # optional — a DRAFT legitimately has no second pair of eyes
    app:                    # the APPROVER. Never written by any tool. See below.
```

Unknown keys **inside** a revision entry are an error, not swallowed into `extra`: a typo'd
`descripton:` would otherwise leave an issued revision with no recorded change note.

### `revision list | add | seal`

```bash
technical_drawings_for_agents revision list basin-ga
technical_drawings_for_agents revision add  basin-ga --rev C --description "Weir crest raised to 54.60 m MSL" \
                                    --by AB [--chk CD] [--date 2026-07-25]
technical_drawings_for_agents revision seal basin-ga --rev C     # interactive terminal only
```

`add` writes `revisions` and `revision` and **nothing else** — never `status`, never
`for_construction`, never `app`. There is **no `revision edit`** and **no `revision unseal`**:
correcting a sealed row means a human editing YAML and consciously re-sealing, which leaves a diff in
git with a commit author on it.

### Your own title block

The paper-space title block is data, not code. `ISO7200_180MM` is the default layout.
`EXAMPLE_A3` is a neutral example of a house block (200 × 36 mm: Project, Title, Drawing No., Rev,
Scale, Drawn, Checked, Approved, Sheet, Status, with a REVISIONS table above it). To use your own,
define a `TitleBlockLayout` and pass it in:

```python
from technical_drawings_for_agents import Cell, TitleBlockLayout, iso7200_title_block

MY_BLOCK = TitleBlockLayout(
    id="MY_BLOCK", width_mm=150.0, height_mm=24.0,
    cells=(
        Cell("title", "TITLE", 0.0, 0.0, 100.0, 24.0, text_mm=3.5, max_lines=2),
        Cell("identification_number", "DRAWING No.", 100.0, 0.0, 50.0, 12.0),
        Cell("revision", "REV / DATE", 100.0, 12.0, 50.0, 12.0, text_mm=2.5, max_lines=2),
    ),
)
svg = iso7200_title_block(frame, fields, layout=MY_BLOCK)
```

Furniture beside the block (a references table, a revisions strip, a notes column) comes from a
settings file read by `sheetfurniture.load_settings`. `docs/specs/sheet-furniture.example.yaml` is a
neutral example sized to sit beside `EXAMPLE_A3` on A3.

### The approver cell, and why no tool may fill it

`app` is the named responsible engineer's signature. It is populated **only** from what a human typed
into `meta.yaml`, read verbatim — there is no fallback to `by`, no fallback to `chk`, no `$USER`, no
git author, no config, no default string. `revision add` has no `--app` flag and a test introspects
`argparse` to keep it that way. `revision seal` refuses when `CI` is set or when `stdin` is not a
terminal: a signature CI can apply is not a signature. No example, template or test fixture in this
repo carries a non-empty `app`, because a plausible approver name in a fixture is a forged signature
waiting for a copy-paste.

`for_construction: true` therefore requires, where a register exists: `status: ISSUED`, **a named
approver**, and **a valid seal**.

### Immutability, four ways

A change to an issued drawing is a **new revision**, always:

1. the **seal** is `sha256` over the entry's fields *plus its index plus the digest of every
   preceding entry* — so an in-place edit, a reorder, a deletion and an insertion are all detected;
2. `RevisionRegister` is a frozen dataclass holding a tuple of frozen `Revision`s; `append` returns a
   new register and refuses to change a sealed prefix;
3. no CLI verb edits or unseals;
4. the `for_construction` gate above.

### The title block is 180 × 56 mm of paper, at every sheet size

`technical_drawings_for_agents.titleblock.iso7200_title_block(frame, fields)` draws a **fixed physical** block
anchored flush to the frame's bottom-right corner, in ISO 3098 millimetre text sizes. Sheet size
affects only the anchor, which is why one implementation is correct at A3, A2, A1 and A0 in both
orientations — a percentage-of-sheet block is guaranteed to be wrong at every size but one. The
revision table sits immediately above it, same width, growing **upward** so the title block never
moves, newest row adjacent to the block. Empty cells render `—`, never blank.

Content that will not fit its cell raises `TitleBlockError` naming the field, the length and the
limit. **It never ellipsises** — that is the whole point, and the direct replacement for the legacy
`svg_title_block`, which silently truncates a long title on the sheet.

The emitted markup is the mechanical assertion surface:

```xml
<g class="title-block" data-extents-mm="651.000 528.000 180.000 56.000"
   data-sheet="A1" data-orientation="landscape">
  <g class="tb-cell" data-field="approved_by" data-extents-mm="806.000 528.000 25.000 14.000">…</g>
<g class="revision-block" data-extents-mm="651.000 508.000 180.000 20.000"
   data-rows="3" data-latest-rev="C">
```

`validate` uses those attributes to check that the rendered `data-latest-rev` agrees with
`meta.revision`, that the block lies on the paper its `data-sheet` declares, and that the revision
table is flush to the title block. All three are gated on the P9 attributes being present, so a
legacy sheet gains no problem and no warning.

### Drawing numbers: `numbering:` is opt-in, deliberately

The built-in `shape-only` scheme encodes only what the house standard actually states — uppercase
alphanumeric segments (2–4 of them, 2–5 chars each), hyphen-separated, a 3-digit zero-padded serial
last, and no `_Rev` suffix in the stored number. It names **no** segments, because the sources name
none: the standard's entire written convention is two examples plus one sentence, its two examples
disagree on field count (`ARC-CSL-001` has two segments, `STA-WTP-GA-001` has three), and the
standards register records the numbering half as still *proposed*.

A project may declare its own grammar, and a non-default scheme **must** cite a `source:` — a scheme
with no citable source is a scheme somebody invented:

```yaml
numbering:
  id: demo-water-v1
  source: "Engineering Drawings as Code §Repo layout (PROPOSED, not adopted)"
  segments:
    - {name: site,         pattern: "[A-Z]{3}",   vocabulary: [STA, STB, STC, ARC]}
    - {name: facility,     pattern: "[A-Z]{2,5}"}
    - {name: drawing_type, pattern: "[A-Z]{2,5}", vocabulary: [GA, SEC, PID, BFD, DET]}
```

With `numbering:` absent — which is every pre-existing drawing — the only check is the pre-existing
one that `number` is non-empty. Promoting the convention from *proposed* to *adopted* is a house-standard
change and the owner's call.

## Deterministic emit, output manifests, and the provenance stamp

"Regenerate and diff" is only a real answer if a no-change rebuild produces a zero-byte diff. It
did not: `ezdxf` re-rolled GUIDs and timestamps on every save, floats reached bytes at 17 digits,
and nothing recorded which inputs produced a sheet. So a `site-plan.yaml` Rev B could land while a
stale sheet sat on screen describing a superseded design, with no way to tell.

**Opting in.** Canonical emit happens only when a caller constructs an `EmitPolicy` and passes it.
There is no global switch and no byte-affecting environment variable except `SOURCE_DATE_EPOCH`,
read in exactly one place (`EmitPolicy.from_environment()`). Without a policy, every generator's
bytes are what they always were — asserted against committed goldens in `tests/goldens/`.

```python
from technical_drawings_for_agents import DxfBuilder, EmitPolicy, Manifest

policy = EmitPolicy.from_environment()          # mm / millidegree quantisation, LF, UTF-8
manifest = Manifest.plan(                       # hashed BEFORE a byte is written
    target=out / "STA-SITE-GA-001.svg",
    inputs=[site_plan, meta_path, register],
    params={"view": "plan", "snap": True},
    policy=policy,
    meta=meta,
)
drawing = Drawing(..., policy=policy, provenance_stamp=manifest.stamp_text)
write_text_canonical(out / "STA-SITE-GA-001.svg", drawing.render(), policy)
manifest.with_output(out / "STA-SITE-GA-001.svg").write()
```

Or from the CLI, for the one emit path that already had one:

```bash
technical_drawings_for_agents layout site.yaml --emit geojson --out out/layout.geojson --manifest
technical_drawings_for_agents manifest out/layout.geojson --check     # is this artifact still current?
```

**Two digests, deliberately.** `build.digest` covers declared input **content** plus params, policy
and tool, and is computable *before* emit — which it must be, because the stamp is printed into the
sheet. `output.sha256` records the bytes actually written. Hashing the manifest instead would make
the digest a function of its own wall clock.

**The digest is keyed on content, never on location.** Input paths are recorded for traceability and
excluded from the preimage, so a CI build and a laptop build of identical inputs print the same
stamp, and moving a project directory does not make every artifact in it look stale. Likewise
`environment` (host, user, platform) is excluded: the stamp answers *what went into this sheet*,
not *who built it*.

**The stamp.** `P:1a2b3c4d` — a fixed prefix and eight hex characters, transcribable off paper into
a search box, checked against one named manifest that carries all 64. A build from an uncommitted
tree gets a `+`, and that suffix is never omitted. **A provenance stamp is not an approval**: it
records what produced these bytes and says nothing about sign-off. The ISSUED gate is untouched.

**Rounding is `round()` / `format()`, i.e. half-even on the exact binary value.** Not
`Decimal(ROUND_HALF_UP)`: the two differ at real values (`2.0625` → `2.062` versus `2.063`), and
ties at mm precision are odd multiples of 1/16 m.

**What is reproducible, and what is only declared.** Never guessed — `emit_digest(path)` raises
rather than hand back a value a caller could wrongly assert byte-identity on:

| Format | Reproducible? | How `emit_digest` answers |
|---|---|---|
| SVG, GeoJSON, JSON, YAML, `.dot`, PNG (matplotlib) | yes | sha256 of the bytes |
| DXF | yes, opted in | sha256 of canonically-masked text (see below) |
| PDF (matplotlib, with `SOURCE_DATE_EPOCH`) | yes — its manifest says so | sha256 of the bytes |
| PDF (LibreOffice) | **no** — time-derived `/CreationDate` and `/ID`, and it ignores `SOURCE_DATE_EPOCH` | **raises** |
| PNG (LibreOffice) | unmeasured | **raises** |

A sidecar manifest is the authority when present, because only the build knows which backend wrote
the bytes. LibreOffice PDFs are *declared* non-reproducible rather than normalised: shipping a
hand-rolled PDF mutator on the path producing the artifact an engineer signs against is the worse
risk.

**Two things make a DXF reproducible, and both are needed.** `ezdxf` stamps the wall clock and
re-rolls GUIDs on save, and `ezdxf.options.write_fixed_meta_data_for_testing` fixes most of it — but
not all:

1. it writes **two** marker records, one at `ezdxf.new()` and one at `saveas()`, so the option has to
   be set around document *creation* as well as the save (scoping it to the save alone leaves one
   wall clock in the bytes);
2. the **CLASSES section** is filled by iterating `entitydb.dxf_types_in_use()`, which is a `set`, so
   its record order varies with `PYTHONHASHSEED` and process history — two machines emit the same
   drawing with different bytes. Under a policy the registry is sorted at source; `mask_dxf_volatiles()`
   normalises the same ordering for a DXF whose writer did not opt in.

Both are toggled inside a restoring context manager and never leak: the ezdxf option is a
process-global, and leaving it set would silently fix the metadata of DXFs whose callers never
opted in. `$TDCREATE` becomes the year-2000 sentinel `2451545.0` in an opted-in DXF — the DXF header
date was never the drawing date; that is `meta.date` and the title block.

## Provenance & safety (non-negotiable)

- **Source, don't invent.** Never fabricate a dimension. A value that can only come from a survey or a
  supplier drawing is a **drawing-verification item** — flagged, not guessed. The example's
  `inputs.yaml` documents this and marks every value as nominal/illustrative.
- **Engineer sign-off before ISSUED FOR CONSTRUCTION** — enforced by `validate` via the `meta.yaml`
  gate.
- **Confidentiality:** supplier drawings inherit their source's sensitivity ceiling.

## Georeferenced backdrops

Large site rasters are prepared outside `technical_drawings_for_agents`, because all GDAL work lives in
an external GIS tool (shown here as `gis-tool`; any tool that writes a `tdfa.geo/1` manifest works):

```bash
micromamba run -n qgis gis-tool geo geo.yaml
```

That writes a small `<name>.geo.json` manifest beside the clipped raster sidecar. Sheets link
the sidecar through `technical_drawings_for_agents.backdrop`, which reads that manifest with nothing but the
standard library:

```python
from technical_drawings_for_agents.backdrop import backdrop_image_element, load_backdrop

bd = load_backdrop("geo/bd.geo.json")
d.add(backdrop_image_element(bd, vb, SHEET_DIR))
```

Before emitting the `<image>` it checks that the sidecar exists, that its SHA256 matches the
manifest, that the manifest's frame bbox matches the sheet `ViewBox` within 1 mm ground, that the
CRS agrees (when the viewport exposes one — today's `ViewBox` has no CRS field, so that check is
`getattr`-gated and the gap is deliberate, pending P1 #53), and that coverage is not zero. A
missing or stale sidecar fails in Python rather than producing a silently blank PDF. Coverage
between `min_coverage` and `warn_coverage` emits a `UserWarning` naming the backdrop and both
numbers, and still emits.

The frame check is what makes the old 4 cm clip error impossible, and because it compares the
*manifest* against the *sheet* it also catches a sidecar left over from a previous frame — which a
shared config field would not.

### Where the SVG must live relative to the sidecar

Linking instead of base64-inlining trades a 5.7 MB SVG for a worse silent failure, so this one is
worth knowing: **`rsvg-convert` (librsvg 2.62.3, measured) loads a linked raster only from the
SVG's own directory or a subdirectory of it.** A parent-escaping `../geo/bd.png`, an absolute path
and a `file://` URI are all dropped silently — exit 0, empty stderr, and a ~955-byte PDF with a
blank backdrop. A missing file behaves identically.

So `backdrop_image_element` refuses an href that escapes the SVG's directory. Emit the sheet from
a directory at or above the sidecar (`geo/bd.png` from the drawing root works; `../geo/bd.png` from
`out/` does not). `allow_outside_svg_dir=True` downgrades the refusal to a warning for a renderer
without that policy — `cairosvg`, which this package's own `render` path uses, resolves `../`
normally.

## Deprecated names

Two identifiers were renamed before the first public release. Writers emit only the new names.
Readers still accept the old ones, with a `DeprecationWarning`, so files made by older tooling keep
loading. The old names will be removed in a future release.

| What | New (written) | Legacy (still read, deprecated) |
|------|---------------|---------------------------------|
| Geo manifest schema id | `tdfa.geo/1` | `sankofa.geo/1` |
| SVG data attributes on sheet furniture | `data-tdfa-bar`, `data-tdfa-placeholder`, `data-tdfa-frame` | `data-sankofa-*` |
| Sheet viewport clip-path id | `tdfa-viewport` | — (not read) |

`technical_drawings_for_agents.sheet.read_sheet_data_attr(attrib, name)` reads either spelling.
`validate` uses it to check that a scale bar's `data-tdfa-bar` (or legacy `data-sankofa-bar`)
agrees with the sheet metadata. To clear the warning, re-render the drawing, or set the manifest's
`schema` to `tdfa.geo/1`.
