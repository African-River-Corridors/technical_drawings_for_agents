# P2 — `technical_drawings_for_agents build`: declarative drawing set + dependency DAG

**Issue:** upstream #54 · **Owner:** `senior-engineer` ·
**Status:** specification (not implemented) · **Milestone:** drawing-workflow hardening

Source of requirements: issue #54, itself derived from
`03-Resources/DEMO Water Project/drawings/Drawing workflow — 30-change robustness review.md`
(review items 1, 24, 25) and `03-Resources/Standards/Engineering Drawings as Code.md`
§Pipeline contract rule 1 ("one build driver, no human build order").

---

## 0. Hard constraints (restated, binding on the implementer)

1. **Backward compatibility is sacred.** Every existing verb — `render`, `validate`, `ingest`, `bfd`,
   `pid`, `component`, `layout` — keeps working exactly as it does now: same arguments, same defaults,
   same stdout, same exit codes, same output bytes. `build` is purely additive. No existing module's
   observable behaviour changes. If a change to an existing module looks necessary, it is out of scope —
   open a follow-up issue instead (§6).
2. **Never invent a capability or a project fact.** Where the real project has a gap, this spec states it
   as an open question (§8) and the implementation must fail loudly rather than paper over it. No node,
   path, command or CRS may be guessed at.
3. **The ISSUED gate is untouchable.** `build` must never write or modify a `meta.yaml`, never set or
   change `status` or `for_construction`, and never promote a drawing towards
   `ISSUED FOR CONSTRUCTION`. Declaring a `meta.yaml` as a node **output** is a configuration error
   (§3.9, test A14). The gate lives in `meta.py:74-79` and `style.py:67`; `build` only ever reads it.
4. **Determinism is the whole point.** For a given `drawing-set.yaml` and a given filesystem state, the
   selected node set, the build order, and the per-node stale/current verdicts are a pure function of the
   declared graph and the input bytes. No agent, no heuristic, no wall-clock, no dict-insertion order.
5. **Prefer loud failure over a silent no-op.** Any ambiguity — unknown key, unsupported version, missing
   tool, empty selection, a cycle, an unstable input — is a non-zero exit with a message that names the
   node and the offending value. The one legitimate no-op is "everything selected is already current",
   and it says so.
6. **House style.** Frozen dataclasses; one module-specific error class (`BuildError`, mirroring
   `components/layout.py:40-41` `LayoutError`); config validated fully on load with messages that name
   the file, the key path and the accepted values (`components/layout.py:155`, `:160`, `:219`,
   `:228-234`); `from __future__ import annotations`; lazy imports of heavy deps inside command functions
   (`cli.py:24`, `:61`, `:229`); tests in `tests/test_*.py` named for the behaviour they assert.

---

## 1. Intent

**Good** looks like this: a drawing set is one text file. `technical_drawings_for_agents build drawing-set.yaml` reads it,
computes a dependency graph from declared inputs and outputs, works out from content hashes which targets
are stale, runs exactly those in one deterministic order, and stops with a clear, attributable error the
first time anything is missing, cyclic, or broken. `technical_drawings_for_agents build --check drawing-set.yaml` answers
"is this set current and buildable?" with an exit code and a named list, writing nothing — usable from
pre-commit and CI. Two engineers on two machines, given the same inputs, get the same build order, the
same set of rebuilt targets, and the same verdicts. Nobody has to remember that step 3 comes before step
4, because nothing asks them to.

**The failure it prevents** is the silently stale sheet. Today the Basin build order lives only in prose
and in an agent's working memory: export the placements layer → `technical_drawings_for_agents layout --from-geojson` →
`python3 source.py` → `rsvg-convert` → reload the GeoJSON into the GeoPackage → `gis-tool stage` →
`gis-tool build`. Miss a step and every downstream artifact keeps its old bytes while looking
finished. That happened: `site-plan.yaml` went to Rev B and the Basin GA generator sat dead for a week
without a single error message. An agent is currently the build system, and an agent that forgets a step
produces a drawing that is confidently wrong — the worst possible output of an engineering pipeline.
`build` moves that ordering out of memory and into tested code, and makes "is this sheet current?" a
question a machine answers.

---

## 2. Current state (read from the code, with citations)

### 2.1 The CLI today — seven verbs, no driver

`src/technical_drawings_for_agents/cli.py` builds one `argparse` parser with `required=True` subcommands
(`cli.py:154-225`) and dispatches on `args.func` (`cli.py:266-270`). The verbs:

| Verb | Parser | Handler | In-process entry point | Exit codes today |
|---|---|---|---|---|
| `render` | `cli.py:162-165` | `_cmd_render` `cli.py:23-42` | `render.render_source(source, out_dir)` `render.py:301-311` | 0 ok (**including "produced no PDF/PNG"**, `cli.py:36-38`), 1 render raised, 2 source missing |
| `validate` | `cli.py:167-171` | `_cmd_validate` `cli.py:45-57` | `validate.validate_target(target)` `validate.py:122-133` | 0 pass, 1 problems found, 2 raised (e.g. unsupported target type) |
| `ingest` | `cli.py:173-200` | `_cmd_ingest` `cli.py:60-151` | `ingest.*` | 0 ok, 1 failed, 2 missing source / missing ODA / bad `--window` |
| `bfd` | `cli.py:202-211` | `_cmd_bfd` `cli.py:247-263` | `bfd.build(data, out_dir, view=)` `bfd.py:406` | 0 ok, 1 failed, 2 data missing |
| `pid` | `cli.py:213-218` | `_cmd_pid` `cli.py:228-244` | `pid.build(data, out_dir)` | 0 ok, 1 failed, 2 data missing |
| `component` | `components/cli.py:30-43` | `components/cli.py:46-90` | `spec.load_component` + `place.place` + `emit.to_*` | 0 ok, 1 failed |
| `layout` | `components/cli.py:93-124` | `run_layout` `components/cli.py:127-191` | `layout.load_layout` / `snap_groups` / `build_layout` / `check_layout` | 0 ok (or `--warn-only`), 1 an `error`-severity finding (`:189-190`), 2 `LayoutError`/`ComponentSpecError` or `--emit` without `--out` (`:137-139`, `:169-171`) |

There is **no** `build` verb, no notion of a set, no staleness, no graph, no state. The CLI is a set of
independent one-shot commands.

### 2.2 Two facts in `render.py` that decide a core design question

* `render.render_py` (`render.py:272-298`) executes a project generator **in-process** via
  `runpy.run_path` (`render.py:288`) after mutating `sys.path` (`:283-291`), then globs `out_dir` for
  `*.dxf` (`:295-297`) and renders those. It does not capture the script's stdout and does not know what
  the script actually wrote.
* `_cmd_render` catches `Exception` only (`cli.py:33`). A project script that calls `sys.exit(...)` raises
  `SystemExit`, which is a `BaseException` — it escapes `render_py`, `render_source` **and**
  `_cmd_render`, killing the process. The real Basin generator does exactly that
  (`drawings/basin-site/source.py:74-76`, `:281-282`). In-process execution of project scripts therefore
  cannot be made survivable by a driver. This is why §3.6 runs project scripts as subprocesses.

`render_dxf` (`render.py:152-201`) routes DXFs to LibreOffice when block `INSERT`s are present and
**degrades to matplotlib with a warning** when `soffice` is absent (`:176-183`) — a non-fatal
fidelity loss. `build` inherits that behaviour unchanged; hardening it is P4 (#56).

### 2.3 `components/layout.py` — the reference pattern to match

Frozen dataclasses for every config object (`Placement` `:49-63`, `Check` `:65-69`, `Editor` `:71-78`,
`Layout` `:80-92`, `Group` `:94-109`, `Finding` `:111-118`); one error class `LayoutError(ValueError)`
(`:40-41`); `load_layout` (`:121-190`) validates the whole config eagerly, resolving every path relative
to the config file and erroring with the file name plus the key path; `_read_yaml` (`:334-340`) wraps
`OSError`/`yaml.YAMLError` into `LayoutError`. Unmapped inputs are hard errors, never skips (`:172-177`,
module docstring `:19-21`). `build` follows this exactly.

Two behaviours of `layout` matter to the graph:

* `--from-geojson` **rewrites the placements register before loading the config**
  (`components/cli.py:129-130`, `_refresh_register` `:194-234`) — so one `layout` invocation both
  consumes a GeoJSON export and overwrites a git-tracked generated register.
* `--emit-register` writes the **effective, post-snap** poses (`components/cli.py:155-166`), which is what
  the Basin sheet actually reads. Snap rules are applied by `snap_groups` (`layout.py:478-554`) unless
  `--no-snap`.

### 2.4 The real project this must drive

`<project>/`. **Note that this project lives
in a different git repository from `technical_drawings_for_agents` — a local vault repo with no remote.** CI in the
`workflows` repo cannot see it (§7.4).

**The five configs, three directories** that review item 25 complains about, all verified present:

| Config | What it owns |
|---|---|
| `components/basin-wtp/basin.layout.yaml` | placement types → component specs, editor read-back fields, parked palette zone, the FA-130 raft snap group, four layout checks |
| `components/basin-wtp/basin.placements.yaml` | GENERATED as-placed register (header: "do NOT hand-edit", "Derived from: placements_export.geojson") |
| `drawings/basin-site/site-plan.yaml` | 187 lines; `meta`, `base`, `ponds`, `platform`, `equipment`, `buildings`, `removed`, `remote_sections`, `gaps`. Rev B, `status: CONCEPT — NOT FOR CONSTRUCTION` |
| `drawings/basin-site/source.py` | 285-line sheet generator for `STA-SITE-GA-001` |
| `drawings/basin-site/geo/README_regen.md` | prose recipe for two of the eight `geo/` artifacts |
| `drawings/basin-site/qgis/basin-site.qgisproj.yaml` | `gis-tool` project config: 7 layers, 1 staged vector |

`basin.layout.yaml:22-41` declares **9 placement types resolving to 10 component spec files**
(`clarifier` → `slabs/clarifier.yaml` + `FA-130.yaml`; the other eight → one spec each), rooted at
`components/` (`root: ..`, `:27`). `basin.layout.yaml:52` points `placements:` at
`basin.placements.yaml`. Checks at `:73-83`; the snap group at `:65-71`.

`basin-site/source.py` reads:
`site-plan.yaml` (`:72`), `../../components/basin-wtp/basin.effective.yaml` (`:61`, `:77`),
`../../components/basin-wtp/basin_layout.geojson` (`:62`, `:78`), `geo/ponds.geojson` (`:128`),
`geo/ga_backdrop.png` (`:66`, base64-inlined at `:120-124`). It **hard-exits** if the register or the
layout GeoJSON is missing (`:74-76`). It writes `out/STA-SITE-GA-001.svg` (`:274-276`) then shells out to
`rsvg-convert` for the PDF (`:279-283`), exiting non-zero if that binary fails. `rsvg-convert` is not a
declared dependency of `technical_drawings_for_agents` (`pyproject.toml` has `ezdxf`, `PyYAML`, `matplotlib` only) — review
item 4, owned by P4.

`geo/README_regen.md:6-13` records exactly two recipes: `gdalwarp` + `gdal_translate` clipping the
OneDrive S2 orthomosaic to the GA frame (`788392 322125 788702 322395 @0.12 m`, matching
`source.py:65`) → `ga_backdrop.tif` → `ga_backdrop.png`; and `ogr2ogr` converting the pond KMZ →
`ponds.geojson`.

`qgis/basin-site.qgisproj.yaml:2-3` records the build commands
(`micromamba run -n qgis gis-tool stage|build`). Its `stage:` block (`:52-53`) stages **only**
`layers/ponds.geojson` into gpkg layer `ponds`. The `placements` layer is deliberately **not** staged so
human edits survive rebuilds (`:15`, and `qgis/placements — how to use.md` §Notes). `gis-tool stage`
writes `basin-site.gpkg` plus `basin-site.gpkg.manifest.json` (verified on disk: three staged layers,
`equipment`/`slab`/`ponds`, with source paths, CRS and feature counts) — a precedent worth noting for the
P3 manifest interface.

### 2.5 The real dependency graph, as it actually is

Node kinds below: **[tool]** = a command the pipeline can run; **[external]** = a human or a GUI or
another environment.

```
  OneDrive S2 orthomosaic .tif  ──[tool: gdalwarp]──► geo/ga_backdrop.tif
                                       └──[tool: gdal_translate]──► geo/ga_backdrop.png ──┐
  OneDrive pond .kmz ──[tool: ogr2ogr]──► geo/ponds.geojson ────────────────────────────┐  │
                                                                                        │  │
  the owner drags footprints in QGIS ──[external]──► qgis/basin-site.gpkg (layer placements) │  │
          │                                                                             │  │
          └──[external: hand export]──► <placements-export>.geojson                      │  │
                        │                                                               │  │
                        ▼                                                               │  │
  basin.layout.yaml + 10 component specs ──[tool: technical_drawings_for_agents layout]──►               │  │
        basin.placements.yaml  (as-placed register, git-tracked)                        │  │
        basin.effective.yaml   (post-snap register)  ─────────────────────────┐          │  │
        basin_layout.geojson   (placed geometry)  ──────────────────────┐     │          │  │
                                                                       │     │          │  │
  site-plan.yaml ───────────────────────────────────────────────────┐  │     │          │  │
                                                                   ▼  ▼     ▼          ▼  ▼
                      [tool: python3 source.py]  ◄─────────────────────────────────────────┘
                                 │
                                 ├──► out/STA-SITE-GA-001.svg   (gitignored)
                                 └──[tool: rsvg-convert]──► out/STA-SITE-GA-001.pdf  (git-tracked)

  basin_layout.geojson ──[external: no recorded command]──► gpkg layers layout_poly/line/pt
  layers/ponds.geojson + basin-site.qgisproj.yaml ──[tool: micromamba run -n qgis gis-tool stage]──►
        basin-site.gpkg + basin-site.gpkg.manifest.json
  basin-site.qgisproj.yaml + gpkg ──[tool: … gis-tool build]──► basin-site.qgs   (no PDF — item 30)
```

### 2.6 Where issue #54 is wrong or incomplete — corrections

The issue's step list is `export the GeoPackage layer → layout --from-geojson → source.py →
rsvg-convert → gis-tool stage → gis-tool build`. The order is right. These details are not:

1. **"export the GeoPackage layer" is not a recorded `ogr2ogr` command anywhere in the project.** The
   only `ogr2ogr` invocation recorded in the whole plant project is the pond KMZ → GeoJSON conversion in
   `geo/README_regen.md:12`. The *placements* export has been done by hand from the QGIS GUI, to a
   throwaway path: `basin.placements.yaml`'s header says "Derived from: placements_export.geojson"
   (no directory), and the superseded `components/basin-wtp/generate_layout.py:2` header says
   "export placements -> /tmp/placements_now.geojson". **There is no reproducible artifact for this step
   today.** Review item 8 calls this "the gpkg↔text bridge is hand-run `ogr2ogr`" — accurate as a
   description of intent, but there is no command to lift into the graph. This is open question OQ-1.
2. **`rsvg-convert` is not a separate pipeline step.** It is shelled out to from inside
   `source.py:279-282`, not run by a human between steps. In the graph it is a *declared tool
   requirement of the sheet node*, not a node.
3. **The sheet does not read `basin.placements.yaml`.** It reads `basin.effective.yaml`, the post-snap
   register (`source.py:61`, `:77`), plus `basin_layout.geojson` (`:62`). The as-placed register is an
   intermediate. A graph wired to the as-placed register would be wrong.
4. **There is also a step before the export**: a human moving and rotating footprints in the QGIS
   `placements` layer, which mutates `qgis/basin-site.gpkg`. That is the true head of the chain and must
   be an explicit external node, otherwise the graph silently starts one step downstream of its real
   source of truth.
5. **`geo/README_regen.md` documents 2 of the 8 artifacts in `geo/`.** On disk: `ga_backdrop.png`,
   `ponds.geojson`, `roads.geojson`, `site_contours.geojson`, `site_dtm.tif`, `site_hillshade.tif`,
   `site_ortho.tif`, `site_ortho_s2.tif` (484 MB). Six have no recorded recipe. Also,
   `ga_backdrop.tif` — the documented intermediate — **does not exist on disk**; only the `.png` derived
   from it survives. A graph that declares the documented recipe will therefore report the `.tif` node as
   MISSING, correctly. OQ-2.
6. **`drawings/basin-site/` has no `meta.yaml`.** `validate_drawing_dir` requires one
   (`validate.py:88-97`) so `technical_drawings_for_agents validate drawings/basin-site` fails today with
   "missing meta.yaml". A `verb:validate` node on that directory therefore fails. That is correct
   behaviour and P2 must not soften it; adding the `meta.yaml` is a project change (OQ-3).
7. **`components/basin-wtp/generate_layout.py` is a superseded one-off, not a build step.** It hardcodes
   `/tmp/placements_now.geojson` and `/tmp/layout.geojson`, an absolute `DEST`, `sys.path.insert(0,
   "technical_drawings_for_agents/src")`, and re-implements the parked-palette exclusion as a magic latitude band
   (`322378<cy<322382`) rather than the declared `editor.parked` bbox
   (`basin.layout.yaml:46-50`). It is functionally replaced by `technical_drawings_for_agents layout --from-geojson`. It
   must **not** appear in the graph.
8. **The `.gpkg` on disk is not stable while QGIS is open.** `qgis/basin-site.gpkg-wal` and
   `.gpkg-shm` are present right now (both gitignored, `drawings/basin-site/.gitignore:11-12`), meaning a
   live editing session. Hashing a SQLite file mid-transaction records a digest that changes on the next
   checkpoint, producing phantom staleness. §4.11 makes this a hard error.
9. **Several outputs are gitignored, so a fresh clone has them MISSING, not stale.**
   `drawings/basin-site/.gitignore`: `geo/` (`:3`), `out/*.svg` (`:6`). The PDF is tracked, the SVG that
   produced it is not. This is legitimate (a 5.2 MB SVG with an embedded raster) but it means the graph's
   verdict differs between a working machine and a fresh clone. That is honest and must be reported
   plainly, not smoothed over.
10. **The GDAL CLI is on `PATH` but broken on this machine.** `ogr2ogr` and `gdalwarp` resolve to
    `/opt/homebrew/bin`, but `ogrinfo` dies with
    `dyld: Library not loaded: /opt/homebrew/opt/x265/lib/libx265.215.dylib` (via `libheif`). "On PATH"
    is therefore not "works": tool nodes must be judged by exit code and must surface captured stderr
    verbatim (§3.8). Neither `technical_drawings_for_agents` nor `gis-tool` is on the ambient `PATH` at all.

---

## 3. Design

New module `src/technical_drawings_for_agents/build.py` (graph + runners + state) and one new CLI subparser in
`cli.py`. Nothing else is modified.

### 3.1 `drawing-set.yaml` — schema

Top level is a mapping. Unknown top-level keys are a configuration error naming the key and listing the
accepted keys. All relative paths resolve against `root` (§3.1.2).

#### 3.1.1 `version` (required)

| Field | Type | Default | Validation |
|---|---|---|---|
| `version` | int | — | Required. Must equal `1`. Anything else: `error: <file>: version <v> not supported by technical_drawings_for_agents <ver> (supported: 1)`. A missing `version` is an error, not an assumed `1`. |

#### 3.1.2 `set` (required)

| Field | Type | Default | Validation |
|---|---|---|---|
| `set.id` | str | — | Required, non-empty after strip, must match `^[A-Za-z0-9][A-Za-z0-9._-]*$`. Used in stdout and in state. |
| `set.description` | str | `""` | Free text. Not validated. |
| `set.root` | str (path) | the set file's own directory | Directory that all relative paths in the file resolve against, itself resolved relative to the set file's directory. Must exist and be a directory. |

#### 3.1.3 `inputs` (optional) — named inputs, for reuse

A mapping of `name` → input declaration. Names match `^[A-Za-z0-9][A-Za-z0-9._-]*$`. A node may reference
a named input as `"@name"` in its `inputs:` list.

| Field | Type | Default | Validation |
|---|---|---|---|
| `path` | str (path) | — | Required. Resolved against `root`. |
| `digest` | `content` \| `size-mtime` | `content` | See §4.2. `size-mtime` is an explicit, reported opt-out for very large binaries. |
| `optional` | bool | `false` | If `true`, a non-existent path is not an error; it contributes the literal `-` to the digest (so its appearance or disappearance still changes the digest). |

Named inputs are purely a convenience: a node may equally list a bare path string. There is **no** implicit
input; every input is either declared in a node's `inputs:` list or derived by declared expansion (§3.5).

#### 3.1.4 `nodes` (required, non-empty)

A mapping of node name → node. Node names match `^[A-Za-z0-9][A-Za-z0-9._-]*$`. Node names must not
collide with target names (§3.1.5).

| Field | Type | Default | Validation |
|---|---|---|---|
| `run` | str | — | Required. One of `verb:render`, `verb:validate`, `verb:bfd`, `verb:pid`, `verb:component`, `verb:layout`, `script`, `command`, `external`. Any other value: error listing the nine accepted runners. Note `ingest` is deliberately absent (§3.4). |
| `description` | str | `""` | Free text, echoed in `--dry-run` output. |
| `inputs` | list[str] | `[]` | Each item is a path relative to `root`, or `"@name"` referencing `inputs:`. An unknown `@name`: error. Duplicates within one node: error. |
| `outputs` | list[str] | `[]` | Paths relative to `root`. Required non-empty for every runner **except** `verb:validate` (which produces no artifact) — an empty `outputs` on any other runner is an error, because a node with no declared output can never be judged stale. |
| `needs` | list[str] | `[]` | Extra node names this node must follow, for ordering that is not expressible as a file dependency. Unknown name: error. |
| `expand_inputs` | bool | `true` for `verb:layout`, `false` for every other runner | Whether the driver derives additional inputs from the node's own config (§3.5). |
| `args` | mapping | `{}` | Runner-specific; fully validated on load (§3.2–3.7). Unknown key: error naming the key and listing accepted keys for that runner. |
| `timeout_s` | int > 0 | none | Optional wall-clock limit for `script`/`command` nodes. Not permitted on other runners. See §4.8. |

#### 3.1.5 `targets` (optional)

A mapping of target name → non-empty list of node names. A target is a label for a set of nodes, so
`--target site-ga` can mean "the sheet and everything it needs". Unknown node name: error. A target name
that collides with a node name: error (`--target` accepts either, so the namespaces must be disjoint).

If `targets` is absent or empty, `--target` accepts node names only.

#### 3.1.6 Worked example — the real Basin set

This is the set file the plant project would carry. It is illustrative of the schema; it is **not** part of
this PR (§6). `external` nodes carry the honest truth about steps no tool can run.

```yaml
version: 1
set:
  id: BASIN-SITE
  description: Basin WTP site GA (STA-SITE-GA-001) + the QGIS viewing project
  root: .                       # this file sits in "DEMO Water Project/"

inputs:
  site-plan:      {path: drawings/basin-site/site-plan.yaml}
  layout-config:  {path: components/basin-wtp/basin.layout.yaml}
  qgis-config:    {path: drawings/basin-site/qgis/basin-site.qgisproj.yaml}
  # 484 MB raster: tracked by size+mtime, reported as such on every run.
  ortho-s2:       {path: drawings/basin-site/geo/site_ortho_s2.tif, digest: size-mtime}

nodes:
  placements-edit:
    run: external
    description: the owner moves/rotates footprints in the QGIS `placements` layer over the ortho
    outputs: [drawings/basin-site/qgis/basin-site.gpkg]
    args:
      reason: a human positions equipment on imagery; QGIS is an authoring GUI, never a pipeline step
      howto: drawings/basin-site/qgis/placements — how to use.md

  placements-export:
    run: external
    description: export the `placements` layer to GeoJSON for `layout --from-geojson`
    inputs: [drawings/basin-site/qgis/basin-site.gpkg]
    outputs: [components/basin-wtp/placements_export.geojson]
    args:
      reason: >-
        no reproducible command is recorded for this export (OQ-1); it has been done from the
        QGIS GUI to a /tmp path. Until a command exists this stays external.
      howto: drawings/basin-site/qgis/placements — how to use.md

  layout:
    run: verb:layout
    inputs: ["@layout-config", components/basin-wtp/placements_export.geojson]
    outputs:
      - components/basin-wtp/basin.placements.yaml
      - components/basin-wtp/basin.effective.yaml
      - components/basin-wtp/basin_layout.geojson
    args:
      config: components/basin-wtp/basin.layout.yaml
      from_geojson: components/basin-wtp/placements_export.geojson
      emit: geojson
      out: components/basin-wtp/basin_layout.geojson
      emit_register: components/basin-wtp/basin.effective.yaml
    # expand_inputs defaults true: the 10 component specs + the register are added automatically

  site-sheet:
    run: script
    inputs:
      - "@site-plan"
      - components/basin-wtp/basin.effective.yaml
      - components/basin-wtp/basin_layout.geojson
      - drawings/basin-site/geo/ponds.geojson
      - drawings/basin-site/geo/ga_backdrop.png
    outputs:
      - drawings/basin-site/out/STA-SITE-GA-001.svg
      - drawings/basin-site/out/STA-SITE-GA-001.pdf
    args:
      path: drawings/basin-site/source.py
      tools: [rsvg-convert]      # shelled out to from inside the script (source.py:279)

  gpkg-reload:
    run: external
    description: load basin_layout.geojson back into the gpkg as layout_poly/line/pt
    inputs: [components/basin-wtp/basin_layout.geojson]
    outputs: [drawings/basin-site/qgis/basin-site.gpkg]
    args:
      reason: no recorded command (OQ-1); basin.layout.yaml:12-13 describes it in prose only

  gis-build:
    run: command
    inputs: ["@qgis-config", drawings/basin-site/qgis/basin-site.gpkg]
    outputs: [drawings/basin-site/qgis/basin-site.qgs]
    args:
      argv: [micromamba, run, -n, qgis, gis-tool, build, basin-site.qgisproj.yaml]
      cwd: drawings/basin-site/qgis
      tool: micromamba

targets:
  site-ga: [site-sheet]
  qgis:    [gis-build]
```

Note that `placements-edit` and `gpkg-reload` both declare `basin-site.gpkg` as an output — which §3.9
rejects. That is not a schema flaw; it is the project's real hazard surfacing as a loud error the first
time anyone declares it. OQ-4 records the choice the project must make.

### 3.2 Runner: `verb:layout`

| Arg | Type | Default | Maps to |
|---|---|---|---|
| `config` | path | — (required) | the positional `config` |
| `view` | str | `plan` | `--view` |
| `from_geojson` | path | none | `--from-geojson` |
| `emit` | `geojson` \| `dxf` | none | `--emit` |
| `out` | path | none; **required when `emit` is set** | `--out` |
| `emit_register` | path | none | `--emit-register` |
| `check_only` | bool | `false` | `--check-only` |
| `warn_only` | bool | `false` | `--warn-only` |
| `no_snap` | bool | `false` | `--no-snap` |

Invoked **in-process** by calling `components/layout.py` functions in the same sequence as
`components/cli.py:127-191`: `_refresh_register`-equivalent when `from_geojson` is set, then
`load_layout`, `snap_groups` (unless `no_snap`), `build_layout`, `check_layout`, then emit. The node
**fails** when any finding has severity `error` and `warn_only` is false — matching
`components/cli.py:189-190` exactly.

The driver must not shell out to `technical_drawings_for_agents layout`, and must not re-enter `cli.main()`. Rationale in
§4.5.

### 3.3 Runners: `verb:render`, `verb:validate`, `verb:bfd`, `verb:pid`, `verb:component`

* `verb:render` — `args`: `source` (path, required), `out` (dir, default `<source-dir>/out`). Calls
  `render.render_source`. **`source` must have suffix `.svg` or `.dxf`.** A `.py` source is a
  configuration error: `error: node '<n>': verb:render cannot take a .py source; use run: script
  (see spec §4.6)`. Rationale §4.6.
* `verb:validate` — `args`: `target` (path, required). Calls `validate.validate_target`. Fails when
  `result.ok` is false, printing each problem — matching `cli.py:54-57`. `outputs` must be empty for this
  runner; it is always run when selected (it has nothing to be stale about) and its verdict is never
  cached in state.
* `verb:bfd` — `args`: `data` (path, required), `out` (dir, default `<data-dir>/out`), `view` (one of
  `block`, `swimlane`, `profile`, `all`; default `block`). Calls `bfd.build`.
* `verb:pid` — `args`: `data` (path, required), `out` (dir, default `<data-dir>/out`). Calls `pid.build`.
* `verb:component` — `args`: `spec` (required), `view` (default `plan`), `emit` (`geojson`|`svg`|`dxf`,
  required), `out` (required), and exactly one of `origin` (`"E,N"`) or `place` (path) — both is an error,
  mirroring the mutually-exclusive group at `components/cli.py:39-41` — plus `rot` (float, default `0.0`).

All five run **in-process**.

### 3.4 `ingest` is not a runner

`ingest` (`cli.py:60-151`) is a one-way, human-supervised conversion of a supplier DWG with several
interactive judgement calls (`--isolate` mode selection, `--window` guard boxes, a corrections overlay),
it depends on external binaries installed outside pip (ODA File Converter, LibreOffice), and its outputs
are named after the *converted* file, not declared up front (`cli.py:144`). Nothing in the plant graph
re-runs ingest; the FA-130 was ingested once and the cleaned DXF is now a committed input. Making it a
node would mean declaring outputs the verb chooses for itself. It is therefore excluded, and a
`run: verb:ingest` is a configuration error naming the nine accepted runners. Re-ingesting a supplier
drawing stays a deliberate manual act.

### 3.5 Declared input expansion (`expand_inputs`)

For `run: verb:layout` with `expand_inputs: true` (the default), the driver loads the node's `config` with
`layout.load_layout` and adds as implicit `content`-digest inputs:

* every resolved component spec path in `Layout.types` (`layout.py:150-162`) — for Basin, 10 files;
* `Layout.register` if set (`layout.py:164-166`) — `basin.placements.yaml`.

It reports the count on stdout: `layout: +11 implicit input(s) from basin.layout.yaml`. If
`load_layout` raises, that is a configuration error (exit 2) naming the node and the `LayoutError`
message verbatim.

An implicit input that is also a declared **output** of the same node (Basin's `basin.placements.yaml`, which
`--from-geojson` rewrites) is **dropped from the input set** with a reported note
(`layout: 1 implicit input is also an output of this node, not tracked as an input:
basin.placements.yaml`). Rationale §4.10.

`expand_inputs` is `false` by default for every other runner, and setting it `true` on a runner with no
defined expansion is a configuration error rather than a silent no-op.

### 3.6 Runner: `script`

| Arg | Type | Default | Notes |
|---|---|---|---|
| `path` | path | — (required) | the script |
| `argv` | list[str] | `[]` | appended after `path` |
| `interpreter` | list[str] | `[sys.executable]` | the launcher |
| `cwd` | path | `path`'s parent directory | working directory |
| `tools` | list[str] | `[]` | extra binaries the script itself shells out to; each must resolve via `shutil.which` in the pre-flight (§3.11) |

Run as a **subprocess**: `subprocess.run([*interpreter, str(path), *argv], cwd=cwd, env=env,
capture_output=True, text=True, timeout=timeout_s)`. `env` is a copy of `os.environ`; the driver adds
nothing and removes nothing except that it passes `SOURCE_DATE_EPOCH` through unchanged if present (P3
will set it). The driver never injects `PYTHONPATH`: a script that needs `technical_drawings_for_agents` must be run by an
interpreter that has it installed. Failure = non-zero exit code, or a timeout, or a declared output
missing after a zero exit.

### 3.7 Runner: `command`

| Arg | Type | Default | Notes |
|---|---|---|---|
| `argv` | list[str] | — (required, non-empty, every item a str) | executed **without a shell** |
| `tool` | str | `argv[0]` | the binary the pre-flight resolves |
| `cwd` | path | `root` | working directory |

`shell: true`, a string `argv`, or any shell metacharacter interpretation is **not supported**; a `shell`
key is a configuration error. Rationale: a shell string is not a declared dependency graph — it can pipe,
redirect and glob its way into inputs and outputs the set file never mentions, which is the class of
invisible coupling this PR exists to kill.

`command` exists so the real graph is expressible today (§4.4). It is the escape hatch, not the
destination: P8 (#60) replaces the GDAL invocations with a `geo` verb and a `geo.yaml`, and the set file
then drops those `command` nodes. The `build` README section must say so.

### 3.8 Runner: `external`

| Arg | Type | Default | Notes |
|---|---|---|---|
| `reason` | str | — (required, non-empty) | why no tool can run this |
| `howto` | path | none | instructions for the operator; must exist if given |
| `command_hint` | str | none | a human-readable hint, **never executed**, never parsed |

An `external` node is a first-class member of the graph: its outputs are hashed, it participates in
staleness and ordering, and downstream nodes depend on it. The driver **never runs it**, under any flag,
including `--force`. When an `external` node is selected and not current, the node's state is `BLOCKED`
and the build stops before running anything downstream of it (§3.12).

This is the explicit representation of "a human drags placements in QGIS" and "somebody exports the
layer by hand". The alternative — leaving those steps out of the file — is the silent gap that produced
the week-long stale sheet.

### 3.9 Graph construction, validation and ordering

Performed by `load_drawing_set` + `topological_order`, entirely before any node runs.

**Edges.** Node `B` depends on node `A` iff either:
1. some input path of `B` equals an output path of `A`, or is a descendant of an output path of `A` that
   is a directory; or
2. `A` is named in `B.needs`.

Path comparison is on `Path.resolve()`d absolute paths. Symlinks resolve. Case is compared as the
filesystem reports it after `resolve()` — on macOS (case-insensitive) two paths differing only in case
resolve to the same file, which is correct and intended.

**Validation, in this order, all before any execution:**

| # | Rule | Error message shape |
|---|---|---|
| V1 | `version == 1` | `error: <file>: version 2 not supported by technical_drawings_for_agents 0.1.0 (supported: 1)` |
| V2 | Schema: types, unknown keys, enum values, required fields, name patterns | `error: <file>: nodes.site-sheet.args: unknown key 'output' (accepted: path, argv, interpreter, cwd, tools)` |
| V3 | Every `needs` and every `targets` entry names a known node | `error: <file>: nodes.site-sheet.needs names unknown node(s): laayout` |
| V4 | No output path is declared by two nodes | `error: <file>: output claimed by 2 nodes (gpkg-reload, placements-edit): drawings/basin-site/qgis/basin-site.gpkg` |
| V5 | No node declares the same path as both an input and an output (after §3.5 expansion drops) | `error: <file>: node 'x': path is both an input and an output: <p>` |
| V6 | No path named `meta.yaml` (any directory) appears in any `outputs` | `error: <file>: node 'x': a build may never write meta.yaml — the ISSUED gate is the engineer's signature, not a build product` |
| V7 | The graph is acyclic | `error: <file>: dependency cycle: a -> b -> c -> a` |
| V8 | Every **source input** — an input that is no node's output — exists, unless `optional: true` | `error: node 'site-sheet': declared input not found: /abs/path/site-plan.yaml` |
| V9 | Every `howto` path exists | `error: <file>: node 'x': args.howto not found: <p>` |

An input that *is* some node's output need not exist at validation time; it will be produced. This is
what makes a first build from a clean tree possible.

**Cycle reporting** (V7) must be deterministic: run Kahn's algorithm with the frontier held in a
lexicographically sorted structure; if nodes remain, find a cycle by depth-first search starting from the
lexicographically smallest remaining node, exploring successors in sorted order, and report it rotated to
begin at the lexicographically smallest node in the cycle. The same broken file always yields the same
cycle string.

**Order** is Kahn's algorithm over the selected subgraph, popping the lexicographically smallest ready
node at each step. Insertion order of the YAML mapping is never consulted. Reordering the `nodes:` block
does not change the build order (test A11).

### 3.10 Staleness

Evaluated per node, in this exact order; the first rule that fires wins and supplies the reason string:

| # | Condition | State | Reason string |
|---|---|---|---|
| S1 | runner is `external` **and** any of S2–S6 would fire | `blocked` | `external: <reason>` |
| S2 | `--force` and the node is selected | `stale` | `forced` |
| S3 | a declared output path does not exist | `missing-output` | `output missing: <first missing path>` |
| S4 | no state record for the node | `unbuilt` | `never built` |
| S5 | recorded `input_digest` ≠ freshly computed digest | `stale` | `input changed: <up to 3 changed paths, sorted>` |
| S6 | any output's on-disk content digest ≠ recorded digest | `stale` | `output modified outside the build: <path>` |
| S7 | any upstream node is not `current` | `stale` | `upstream <node> is <state>` |
| — | otherwise | `current` | `up to date` |

`verb:validate` nodes have no outputs and are never `current`: when selected they are always run (state
`stale`, reason `check node — always run`), and no state is written for them.

**The input digest.** Both implementations must compute this identically:

```
node_def = canonical_json({
    "runner":  node.run,
    "args":    node.args,                      # after defaulting; paths as posix, relative to root
    "outputs": sorted(posix_rel(p) for p in node.outputs),
    "needs":   sorted(node.needs),
})
lines = b"".join(
    f"{posix_rel(p)}\0{mode}\0{value}\n".encode("utf-8")
    for p, mode, value in sorted(entries, key=lambda e: e[0])
)
input_digest = sha256(sha256(node_def.encode()).hexdigest().encode() + b"\n" + lines).hexdigest()
```

where `canonical_json` is `json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)`,
`posix_rel(p)` is `p` relative to `root` as a POSIX string (a path outside `root` uses its absolute POSIX
form), and per entry:

* `mode == "content"` → `value` = `sha256` hex of the file's bytes, read in binary, or `-` if the input is
  `optional` and absent;
* `mode == "size-mtime"` → `value` = `f"{st_size}:{st_mtime_ns}"`, or `-` if optional and absent.

Including `node_def` in the digest is essential: changing `view: block` to `view: swimlane`, or changing a
node's declared outputs, must rebuild.

**The size+mtime fast path.** A state record may carry, per input, an advisory
`(st_size, st_mtime_ns) → digest` pair. When both match on a later run, the recorded digest is reused
without re-reading the file. **`st_mtime` is never used to decide staleness** — it is only a cache key for
a digest already computed. A mismatch means "re-read and hash", never "stale".

### 3.11 Pre-flight (before the first byte is written)

Once the plan is computed and before any node runs, `build` resolves every tool required by every node it
intends to run (`command.tool`, `script.args.tools`) via `shutil.which`. If any is unresolvable, **nothing
is built** and the command exits 2:

```
error: node 'site-sheet': required tool not found on PATH: rsvg-convert
error: pre-flight failed — nothing was built
```

Rationale: a build that dies half-way leaves a mixed-vintage drawing set, which is precisely the failure
mode this PR exists to remove. Note §2.6(10): resolving on `PATH` does not prove the tool works, so a tool
that resolves and then fails is a normal node failure with its stderr surfaced verbatim.

### 3.12 Execution

Selected nodes run **serially**, in the order from §3.9. For each:

* `current` → `SKIP`, nothing runs.
* `blocked` → `BLOCKED`; the node does not run; **execution stops** — no node ordered after a blocked node
  runs, and the command exits 1. Rationale: running the downstream of a step that has not happened is
  exactly how a stale sheet gets produced with a green log.
* otherwise → `BUILD`, then run.

Around every run:

1. **Pre-run snapshot.** For each declared output that exists, record `(path, size, sha256)`.
2. **Run**, capturing stdout and stderr. In-process verb nodes are captured with
   `contextlib.redirect_stdout` / `redirect_stderr` into buffers so their output is handled identically to
   a subprocess node's (§3.13).
3. **Post-run check.** A node has succeeded iff it did not raise / exited zero **and** every declared
   output path now exists. A zero exit with a missing declared output is a failure:
   `FAIL site-sheet declared output not produced: out/STA-SITE-GA-001.pdf`.
4. **On success:** compute each output's digest, write the state record, print `OK` with the output list.
5. **On failure: quarantine.** For each declared output path, compare with the pre-run snapshot. If the
   bytes differ (or the path is new), `os.replace` it to `<path>.partial`, overwriting any existing
   `.partial`, and report it. Outputs byte-identical to the snapshot are left untouched. No state record
   is written. Then **stop** — no further node runs — and exit 1.

**The half-written-output guarantee** (test A15): *after a failed build, for every path in the failed
node's `outputs`, the path either does not exist or is byte-identical to its content before the run; any
bytes the failed run produced are at `<path>.partial`.* Achieved by quarantine alone; no copying of large
files is required. If a quarantine `os.replace` itself fails (permissions), the driver says so explicitly
on stderr and still exits 1 — it never reports success.

When a node is built and some node that depends on it was **not** selected, the driver prints a note
naming them, so a partial build never looks complete:
`note: 2 node(s) became stale and were not selected: gpkg-reload, gis-build`.

### 3.13 CLI surface

```
technical_drawings_for_agents build <drawing-set.yaml> [--target NAME]... [--check] [--force]
                                      [--dry-run] [--timings]
```

| Flag | Meaning |
|---|---|
| *(none)* | build every stale node in the whole set |
| `--target NAME` | build `NAME` (a target or a node) plus its transitive **dependencies**, and nothing else. Repeatable; the selection is the union of closures. Dependents are *not* included. Unknown name → exit 2 listing known targets then known nodes, both sorted. |
| `--check` | evaluate and report; **write nothing at all**, not even the state file; run no node. Exit per §3.14. |
| `--force` | treat every selected non-`external` node as stale. Does not make `external` nodes runnable; when `--force` is given and the selection contains an `external` node the driver says so: `note: --force does not run external node(s): placements-edit`. |
| `--dry-run` | print the plan (order + per-node state + reason) and exit 0 unless the configuration is broken. Runs nothing, writes nothing. |
| `--timings` | append `  (<n.n>s)` to each `OK` line. Off by default so stdout is byte-stable and diffable in tests. |

`--check` with `--force` is a configuration error (`--force` asserts staleness, `--check` measures it):
`error: --check and --force are mutually exclusive`. `--check` with `--dry-run` is likewise an error.

`build` accepts no `--out`: a set declares its own outputs.

### 3.14 Exit codes

| Code | Meaning |
|---|---|
| 0 | Success. For `build`: every selected node is now current (including "nothing to do"). For `--check`: the set is current and buildable. For `--dry-run`: the plan printed. |
| 1 | A node failed, or a selected `external` node is not current (`BLOCKED`). Something was attempted and did not finish. |
| 2 | Configuration or precondition error: unreadable/invalid set file, unsupported `version`, unknown key/runner/name, V3–V9 violations, a cycle, a missing source input, mutually exclusive flags, an empty selection, pre-flight tool resolution failure, an unstable `.gpkg` input (§4.11). **Nothing was built.** |
| 3 | `--check` only: the set is not current or not buildable here — stale, missing, blocked, or a required tool is absent. |

`--check` reports a missing tool as 3 rather than 2 on purpose: it is answering a question about *this
machine*, and "the set is fine, this box lacks `rsvg-convert`" is an answer, not a broken config. `build`
reports the same condition as 2, because there it is a precondition failure that stopped the build before
it started. Both are documented so no implementer has to guess.

An empty selection is exit 2, not 0: `error: --target 'qgis' selected 0 node(s)` — a build command that
silently does nothing is the failure mode this PR exists to remove.

### 3.15 stdout / stderr contract

Following `cli.py:51-57` and `components/cli.py:179-191`: the report goes to stdout, failures go to
stderr.

**stdout**, in this exact shape (status token left-padded to a 7-column field, then two spaces, then the
node name padded to the longest selected node name, then two spaces, then the reason or the outputs):

```
technical_drawings_for_agents build BASIN-SITE: 6 node(s), 4 selected
layout: +11 implicit input(s) from basin.layout.yaml
   SKIP  layout      up to date
  BUILD  site-sheet  input changed: drawings/basin-site/site-plan.yaml
    ... captured node output, every line indented by 4 spaces ...
     OK  site-sheet  -> drawings/basin-site/out/STA-SITE-GA-001.svg, drawings/basin-site/out/STA-SITE-GA-001.pdf
BLOCKED  gpkg-reload  external: no recorded command (OQ-1)
note: 1 node(s) became stale and were not selected: gis-build
build: 1 built, 1 skipped, 1 blocked, 0 failed
```

The status token comes from a closed set: `BUILD`, `OK`, `SKIP`, `FAIL`, `BLOCKED`, `CHECK`. Output paths
on an `OK` line are POSIX, relative to `root`, sorted. The final summary line is always printed, always
exactly `build: <n> built, <n> skipped, <n> blocked, <n> failed`.

`--check` prints one `CHECK` line per selected node and one verdict:

```
technical_drawings_for_agents build --check BASIN-SITE: 6 node(s), 6 selected
  CHECK  layout       current
  CHECK  site-sheet   STALE: input changed: drawings/basin-site/site-plan.yaml
  CHECK  gpkg-reload  BLOCKED: external: no recorded command (OQ-1)
```
then, to **stderr**, `check: NOT CURRENT — 1 stale, 1 blocked, 0 missing tool(s)` and exit 3; or, to
stdout, `check: current (6 node(s))` and exit 0.

**stderr** carries: all driver diagnostics prefixed `error: `; the captured output of a failing node,
indented 4 spaces, verbatim, unmodified (including a broken GDAL's `dyld` message); and the `--check`
not-current verdict. A failing node's report:

```
   FAIL  site-sheet  exit 1
    error: STA-SITE-GA-001.pdf missing — run `technical_drawings_for_agents layout` first
  quarantined: drawings/basin-site/out/STA-SITE-GA-001.svg -> STA-SITE-GA-001.svg.partial
error: node 'site-sheet' failed; 0 further node(s) run
```

### 3.16 Public Python API

`src/technical_drawings_for_agents/build.py`. Every dataclass frozen; every mapping exposed as an immutable view; every
sequence a tuple.

```python
class BuildError(ValueError):
    """Raised when a drawing set, its graph, or a build precondition is malformed."""


DigestMode = Literal["content", "size-mtime"]
Runner = Literal[
    "verb:render", "verb:validate", "verb:bfd", "verb:pid", "verb:component",
    "verb:layout", "script", "command", "external",
]
NodeState = Literal["current", "stale", "unbuilt", "missing-output", "blocked"]
Action = Literal["built", "skipped", "blocked", "failed"]

RUNNERS: frozenset[str]          # the nine accepted values, for error messages
SCHEMA_VERSION: int = 1
STATE_DIRNAME: str = ".technical_drawings_for_agents"
STATE_FILENAME: str = "build-state.json"


@dataclass(frozen=True)
class InputRef:
    path: Path                   # absolute, resolved
    digest: DigestMode = "content"
    optional: bool = False
    name: str | None = None      # the `inputs:` key, when referenced as "@name"
    implicit: bool = False       # added by expand_inputs (§3.5)


@dataclass(frozen=True)
class Node:
    name: str
    run: Runner
    inputs: tuple[InputRef, ...]         # sorted by posix path
    outputs: tuple[Path, ...]            # absolute, resolved, sorted
    needs: tuple[str, ...]               # sorted
    args: Mapping[str, Any]              # validated + defaulted; read-only
    description: str = ""
    expand_inputs: bool = False
    timeout_s: int | None = None


@dataclass(frozen=True)
class DrawingSet:
    id: str
    source: Path                         # the drawing-set.yaml, resolved
    root: Path
    nodes: Mapping[str, Node]            # read-only; iterate sorted(nodes)
    targets: Mapping[str, tuple[str, ...]]
    description: str = ""


@dataclass(frozen=True)
class NodeRecord:
    """One node's last successful build. The unit of exchange with a StateStore."""
    node: str
    input_digest: str                                   # §3.10
    outputs: Mapping[str, str]                          # posix-rel path -> sha256 hex
    fast: Mapping[str, tuple[int, int, str]]            # posix-rel -> (size, mtime_ns, sha256)
    tool_version: str                                   # technical_drawings_for_agents.__version__ at write time


class StateStore(Protocol):
    """Where a build's per-node records live. P3 (#55) replaces the default with manifests."""
    def read(self, node: str) -> NodeRecord | None: ...
    def write(self, record: NodeRecord) -> None: ...
    def flush(self) -> None: ...


class JsonStateStore:
    """Default store: one JSON file at <root>/.technical_drawings_for_agents/build-state.json."""
    def __init__(self, root: Path) -> None: ...
    # read/write/flush per StateStore


@dataclass(frozen=True)
class NodeStatus:
    node: str
    state: NodeState
    reason: str
    changed_inputs: tuple[str, ...] = ()     # posix-rel, sorted; only for state == "stale"


@dataclass(frozen=True)
class NodeResult:
    node: str
    action: Action
    outputs: tuple[Path, ...] = ()
    stdout: str = ""
    stderr: str = ""
    error: str | None = None
    exit_code: int | None = None             # subprocess runners only
    duration_s: float | None = None
    quarantined: tuple[tuple[Path, Path], ...] = ()   # (original, .partial)


@dataclass(frozen=True)
class BuildReport:
    set_id: str
    order: tuple[str, ...]
    results: tuple[NodeResult, ...]

    @property
    def ok(self) -> bool: ...            # no failed, no blocked
    @property
    def built(self) -> tuple[str, ...]: ...
    @property
    def skipped(self) -> tuple[str, ...]: ...
    @property
    def blocked(self) -> tuple[str, ...]: ...
    @property
    def failed(self) -> tuple[str, ...]: ...


# --- the five public functions ---

def load_drawing_set(path: str | Path) -> DrawingSet:
    """Load + fully validate a drawing-set.yaml (V1-V9, §3.9). Raises BuildError."""

def select(dset: DrawingSet, targets: Iterable[str] | None = None) -> tuple[str, ...]:
    """Node names in the selection: the transitive dependency closure of `targets`
    (target names or node names), or every node when `targets` is None. Sorted.
    Raises BuildError on an unknown name or an empty selection."""

def topological_order(dset: DrawingSet, selected: Iterable[str] | None = None) -> tuple[str, ...]:
    """Deterministic build order over the selected subgraph (Kahn, smallest-name-first).
    Raises BuildError with a rotated cycle path if the graph is cyclic."""

def plan(
    dset: DrawingSet,
    *,
    targets: Iterable[str] | None = None,
    force: bool = False,
    state: StateStore | None = None,
) -> tuple[NodeStatus, ...]:
    """Per-node staleness in build order (§3.10). Reads only; writes nothing."""

def build_set(
    dset: DrawingSet,
    *,
    targets: Iterable[str] | None = None,
    force: bool = False,
    state: StateStore | None = None,
) -> BuildReport:
    """Run the stale nodes in order (§3.12). Stops at the first failure or block.
    Raises BuildError for pre-flight failures (nothing is built)."""
```

`state=None` means "construct a `JsonStateStore(dset.root)`". `check` is `plan(...)` plus tool resolution;
the CLI layer, not the API, owns exit-code mapping — mirroring how `_cmd_validate` (`cli.py:45-57`) maps a
`ValidationResult` to a code.

### 3.17 CLI wiring

`cli.py` gains one function and one parser block, and nothing else changes:

```python
def _cmd_build(args: argparse.Namespace) -> int:
    from .build import BuildError, build_set, load_drawing_set, plan   # lazy, per house style
    ...
```

registered in `build_parser()` alongside the existing subparsers (`cli.py:154-225`). No new package
dependency: `hashlib`, `json`, `subprocess`, `shutil`, `contextlib` are stdlib and `PyYAML` is already a
declared dependency.

---

## 4. Behaviour decisions, with rationale

### 4.1 Content hashing, not mtime

**Decision:** staleness is decided by SHA-256 of input bytes plus a digest of the node's own definition.
`mtime` appears only as a cache key for a digest already computed (§3.10).

**Rejected — mtime comparison** (make's model). A `git checkout`, a branch switch, or a fresh clone
rewrites every mtime, so every target would be stale after any branch change: the pipeline's most common
operation would trigger a full rebuild of a 484 MB-raster-backed sheet. Worse, it interacts badly with the
project's design intent: the generated registers are *meant* to be byte-stable (P3 #55), so a re-run of
`layout` that changes nothing must not cascade into a sheet rebuild. mtime cannot express that; a content
hash can.

**Rejected — mtime with a content-hash tiebreak only for "suspicious" cases.** Two rules means two
implementations disagree about which case is suspicious. One rule.

**Cost, and the mitigation:** hashing `geo/site_ortho_s2.tif` (484 MB) on every `--check` is
unacceptable in a pre-commit hook. Mitigated two ways: (a) the size+mtime fast path, which reuses a
recorded digest without re-reading when both match — correctness is unaffected because the hash remains
the authority and a mismatch only means "re-read"; and (b) an explicit per-input
`digest: size-mtime` opt-out, which the driver **reports on every run**
(`note: 1 input(s) tracked by size+mtime, not content: drawings/basin-site/geo/site_ortho_s2.tif`) so the
weaker guarantee is never invisible.

### 4.2 The P3 (#55) interface: this PR does not build manifests

P3 owns `<output>.manifest.json` — input paths and SHA256s, tool version, git commit, the resolved graph
node — plus the provenance stamp. P2 needs the same digests one step earlier, to decide staleness.

**Decision:** P2 defines the `StateStore` protocol and `NodeRecord` (§3.16) and ships exactly one
implementation, `JsonStateStore`, writing a **build cache** to `<root>/.technical_drawings_for_agents/build-state.json`.
P2 **must not** write any `*.manifest.json`, must not stamp anything onto a sheet, and must not add a
`git` dependency.

The contract P3 will implement: P3 supplies a `StateStore` whose `read`/`write` are backed by per-output
manifests, and P2's `build_set(..., state=...)` accepts it unchanged. `NodeRecord.outputs` is
deliberately a `{posix-rel-path: sha256}` mapping because that is the subset of a manifest P2 needs, and
`NodeRecord.tool_version` exists so P3 has somewhere to put provenance without a schema change. The
resolved graph node identity P3 wants to record is exactly `NodeRecord.input_digest`'s `node_def`
component, so P2 must expose it: `build.node_definition_digest(node: Node) -> str`.

**Rejected — P2 writes manifests itself.** It would collide with P3 on file format, and a build cache and
a provenance record have different lifecycles: the cache is machine-local, mtime-bearing and gitignored;
the manifest is an artifact that ships with the PDF. Conflating them means either machine-specific noise
in git or losing the fast path.

**Rejected — no state file at all** (recompute everything, compare outputs to a recipe). Without a record
of what the inputs were at the last successful build, a changed input is indistinguishable from a
first-ever build, and S6 ("output modified outside the build") is not detectable at all.

`.technical_drawings_for_agents/` must be added to the consuming project's `.gitignore` (§7.2).

### 4.3 Detecting hand-edited outputs (S6)

**Decision:** an output whose on-disk digest differs from the recorded one is **stale**, reported as
`output modified outside the build`.

Rationale: the project's generated files carry "GENERATED — do NOT hand-edit" headers
(`basin.placements.yaml`, `basin.effective.yaml`, and `dump_placements_register`'s header at
`layout.py:460-466`). If somebody edits one anyway, the honest response is to regenerate and let the diff
show what was lost — silently trusting a hand-edited generated file is how a drawing comes to disagree
with its own inputs. **Rejected:** ignoring output digests (cheaper, but blind to exactly the tampering
the headers warn about).

### 4.4 `command` nodes: expressible now, replaced by P8

**Decision:** ship a `command` runner (argv list, no shell) so the real Basin graph — including
`ogr2ogr`, `gdalwarp`/`gdal_translate` and `micromamba run -n qgis gis-tool build` — is expressible in
P2.

**Rejected — verbs only, no `command`.** The `geo` verb does not exist yet (P8 #60) and `gis-tool`
lives in a different Python environment (`micromamba run -n qgis`, `qgisproj.yaml:2-3`); without
`command`, more than half the real graph would have to be `external`, i.e. outside the build. A build
driver that can only drive half a pipeline reintroduces the human build order for the other half — the
exact defect being fixed.

**Rejected — a shell string.** A shell string can pipe, redirect and glob into inputs and outputs the set
never declares, which silently defeats the dependency graph.

The README must state that `command` is a bridge: P8 replaces the GDAL nodes with a `geo` verb, and P2
should not be read as blessing shell-outs as the destination.

### 4.5 technical_drawings_for_agents verbs run in-process — but not through `cli.main()`

**Decision:** verb nodes call the module-level functions (`layout.load_layout`/`snap_groups`/
`build_layout`/`check_layout`, `bfd.build`, `pid.build`, `render.render_source`,
`validate.validate_target`) directly.

Rationale: no interpreter start-up per node (a set has many nodes and `--check` runs often); exceptions
arrive with structure instead of as parsed stderr; and `cli.main()` is not a library entry point —
it calls `logging.basicConfig` (`cli.py:267`) which is process-global and idempotent-by-first-caller, and
its subparsers are `required=True` (`cli.py:160`) so argument construction is brittle. Re-entering it
per node would make node behaviour depend on invocation order.

**Rejected — subprocess every node** (`[sys.executable, "-m", "technical_drawings_for_agents.cli", ...]`). Uniform, and
tempting for isolation, but it makes the driver's error reporting a stderr-parsing exercise, costs an
interpreter plus a matplotlib import per node, and gains nothing for verbs the toolkit itself owns and
tests.

**Rejected — re-entering `cli.main(argv)` in-process.** It looks like the smallest change and is the most
fragile: it couples node execution to argparse's exit behaviour and to global logging configuration.

### 4.6 A project `source.py` is a subprocess, and `verb:render` refuses `.py`

**Decision:** `run: script` executes `[sys.executable, path]` as a **subprocess**, cwd = the script's
directory. `verb:render` accepts only `.svg` and `.dxf` sources; a `.py` source is a configuration error
that points at `run: script`.

Rationale, from the code:

* `render.render_py` runs the script with `runpy.run_path` **in-process** (`render.py:288`) and mutates
  `sys.path` around it (`:283-291`). One script's imports, globals and `matplotlib` state then leak into
  the next node.
* The real generator calls `sys.exit(...)` on a missing prerequisite (`source.py:74-76`) and on a
  `rsvg-convert` failure (`:281-282`). `SystemExit` is a `BaseException`, so it escapes `render_py`,
  `render_source`, and `_cmd_render`'s `except Exception` (`cli.py:33`) — in-process, a single failing
  project script terminates the whole build driver, mid-set, with no per-node attribution and no
  quarantine. That is unfixable without changing `render.py`, which §0.1 forbids.
* `render_py` also glob-discovers outputs (`render.py:295-297`) rather than being told them, and the real
  generator writes to its own hard-coded `out/` (`source.py:59`) regardless of the `out_dir` argument —
  so for the Basin sheet `render_py` returns `[]` and `_cmd_render` prints "render ran but produced no
  PDF/PNG" and **exits 0** (`cli.py:36-38`). A build driver cannot treat that as success.

A subprocess gives an exit code, captured stderr, an enforceable `timeout_s`, and per-node isolation.
The cost — interpreter start-up per script node — is irrelevant next to `rsvg-convert` on a 5.7 MB SVG.

**Declaring a script's outputs:** they are declared **explicitly** in the node's `outputs:`. The driver
does not glob and does not infer. A zero exit with a declared output missing is a failure (§3.12.3), which
is precisely what would have caught the dead Rev-B generator. **Rejected — glob the script's `out/`
directory**: it cannot distinguish a fresh artifact from last week's, and it makes the graph's edges
depend on directory contents rather than on the text.

### 4.7 A missing tool fails before anything is built

**Decision:** resolve every required tool (`command.tool`, `script.args.tools`) with `shutil.which` in a
pre-flight pass; if any is missing, exit 2 and build nothing. Under `--check`, report it and exit 3.

Rationale: the alternative — discovering at node 4 of 6 that `rsvg-convert` is absent — leaves a set whose
artifacts come from two different vintages, which is worse than not building. **Rejected — skip nodes
whose tool is missing, with a warning.** A warning in a build log is a silent gap; the whole point is that
a missing step must not produce a plausible-looking result. **Rejected — auto-install or auto-fall-back.**
`render_dxf`'s existing matplotlib fallback (`render.py:176-183`) is a documented, tested degradation
inside one verb; inventing new fallbacks is P4's remit, not the driver's.

Note that `shutil.which` success is not proof of function (§2.6.10: `ogr2ogr` resolves and then dies on a
`dyld` error). A tool that resolves and fails is an ordinary node failure and its stderr is surfaced
verbatim — no interpretation, no summarising.

### 4.8 No default timeout

**Decision:** `timeout_s` is opt-in per node; there is no default.

Rationale: `gdalwarp` over a 484 MB orthomosaic, or LibreOffice converting a block-heavy DXF
(`render.py:138` already allows 180 s per conversion), legitimately take minutes. A wrong default turns
slow into flaky, and a timeout kill is indistinguishable from a real failure in a CI log. **Rejected — a
900 s default:** it would be a guess about hardware, and this spec does not guess.

### 4.9 Serial execution only, deterministic order

**Decision:** nodes run one at a time, in Kahn order with a lexicographic-smallest-name tie-break.

Rationale for serial: determinism is the deliverable. In-process verbs share process-global state
(`matplotlib.use("Agg")` at `render.py:41`, the root logger, `sys.path`); parallel execution would
interleave captured output nondeterministically and make a flaky node look like a graph bug. Rationale for
the lexicographic tie-break rather than YAML declaration order: reordering the `nodes:` block is a
cosmetic edit and must not change build order or any log line (test A11). `yaml.safe_load` preserves
insertion order in CPython, which is exactly the trap — it would make order *look* stable while being a
property of the file's formatting.

**Rejected — `--jobs N`.** Real value once nodes are isolated, but it needs per-node output isolation and
a defined output-interleaving rule first. Follow-up issue (§6).

### 4.10 A node that rewrites one of its own inputs

`layout --from-geojson` overwrites `basin.placements.yaml` (`components/cli.py:194-234`), which
`load_layout` also reads (`layout.py:164-166`) — so §3.5's expansion would make it both an input and an
output of the same node, which V5 rejects.

**Decision:** an **implicit** input (from expansion) that is also a declared output of the same node is
dropped from the input set, with a printed note. An **explicitly listed** input that is also an output of
the same node stays a V5 error.

Rationale: the register is a *product* of this node, not a fact it depends on — treating it as an input
would make the node permanently self-staling (S6 would fire on the file the node just wrote). Dropping it
silently, though, would hide a genuine modelling question, so it is reported. Requiring the project to
hand-remove it is worse: the expansion exists precisely so nobody maintains that list by hand.

### 4.11 An open GeoPackage is a hard error, not a warning

**Decision:** if a declared **input** path ends in `.gpkg` and a sibling `<name>.gpkg-wal` or
`<name>.gpkg-shm` exists, exit 2 before building anything:

```
error: node 'gis-build': drawings/basin-site/qgis/basin-site.gpkg has an open SQLite WAL sidecar
  (basin-site.gpkg-wal) — a QGIS session is live. Save the layer and close QGIS, then rebuild.
```

Rationale: both sidecars exist on disk in the plant project right now (and are gitignored,
`.gitignore:11-12`). Hashing a SQLite file with uncommitted WAL contents records a digest that changes at
the next checkpoint, so the node appears stale on the next run for no reason the operator can see —
non-determinism from outside the graph. Failing with an instruction is 10 lines of code and removes the
class. **Rejected — warn and continue:** a warning in a log is the silent gap this PR exists to close.
**Rejected — ignore the sidecars:** it makes phantom staleness a permanent feature of the project's most
important input.

### 4.12 `--check` treats a never-built output as not current

**Decision:** a declared output that does not exist is `missing-output`, which is not current: `--check`
exits 3 and names it.

Rationale: `--check` is the pre-commit / CI gate (review item 24). If a deleted or never-built PDF passed
the gate, the gate would allow exactly the state it exists to prevent. **Rejected — "not yet built" is
neither current nor stale, exit 0.** It sounds tidier and it is the failure mode.

Consequence, stated plainly: because `geo/` and `out/*.svg` are gitignored (§2.6.9), a fresh clone of the
project reports several nodes `missing-output`. That is the truth about a fresh clone and must be reported
as such, not smoothed away. It is also the argument for P8's `geo.yaml`: once the geo nodes are `command`
nodes with recorded parameters, `build` can actually produce them.

### 4.13 `--check` writes nothing; `--dry-run` explains; they are different flags

**Decision:** `--check` opens files read-only, does not create `.technical_drawings_for_agents/`, does not write a state
record, and runs no node — its whole output is a report and an exit code. `--dry-run` prints the same plan
but always exits 0 (barring a broken config).

Rationale: a gate that mutates state is not usable in a pre-commit hook or a read-only CI checkout, and a
gate whose exit code sometimes means "fine, I fixed it" is not a gate. Two flags because conflating them
forces one exit-code convention onto two different questions: "is this current?" (an assertion) and "what
would you do?" (an explanation). A human running `--check` to see the plan and getting exit 3 would
reasonably read it as an error.

### 4.14 A blocked external node stops the build

**Decision:** when a selected `external` node is not current, it is reported `BLOCKED`, nothing ordered
after it runs, and the exit code is 1.

Rationale: the whole point of representing the human steps is that the graph knows when it is standing on
a step that has not happened. Building the downstream of a stale hand-export produces a sheet from an old
export — with a green log. **Rejected — skip the blocked node and build what is buildable:** that is
today's behaviour with extra ceremony. **Rejected — a `--skip-external` flag:** it would be reached for
under time pressure, which is exactly when a stale sheet gets issued.

`--force` explicitly does **not** override this. `--force` means "distrust the staleness cache"; it cannot
mean "pretend a human did something".

### 4.15 The tool version is recorded, not compared

**Decision:** `NodeRecord.tool_version` records `technical_drawings_for_agents.__version__` at write time. A mismatch on a
later run prints a note and does **not** force a rebuild:
`note: state written by technical_drawings_for_agents 0.1.0, running 0.2.0 — pass --force to rebuild`.

Rationale: comparing it would make every patch bump rebuild every sheet in every set, which is noise, and
would fight P3's byte-reproducibility work (a rebuild that produces identical bytes is pure cost).
**Rejected — include the version in the input digest:** simpler to implement, and it would make `--check`
fail on every version bump, training people to ignore it. Provenance across versions is P3's manifest,
where it belongs.

### 4.16 One entry point, but no YAML include/merge

Issue #54 asks for "the existing configs as includes" (review item 25: five configs, three directories).

**Decision:** the set file **references** the existing configs by path — as node `args` and as declared
inputs — and there is **no** mechanism for textually including or merging another YAML file.

Rationale: review item 25's complaint is "*implicit relationships*", and a single file that names all five
configs and the relationships between them answers it fully. A merge mechanism, by contrast, demands
decisions no implementer can make consistently (does a later fragment override or append? are paths
relative to the fragment or the including file? can a fragment redefine a node?), and nothing in the real
project needs it — Basin is one set. This is a deliberate, recorded deviation from the issue's wording;
open a follow-up if a second site wants shared fragments (§6).

### 4.17 `--target` builds dependencies, not dependents

**Decision:** `--target X` selects `X` plus its transitive dependencies. Dependents are excluded, and if
building the selection leaves an unselected dependent stale, the driver names them
(`note: 2 node(s) became stale and were not selected: …`).

Rationale: "build this sheet" must not silently rebuild the QGIS project. But leaving the operator
believing the set is coherent afterwards would be a silent gap, hence the note. **Rejected — include
dependents:** `--target layout` would then rebuild everything, making the flag useless. **Rejected —
say nothing:** the whole failure mode is somebody believing a set is current when part of it is not.

---

## 5. Acceptance tests

Mechanically checkable, in `tests/test_build.py` (behaviour) and `tests/test_build_graph.py` (schema,
order, cycles), named for the behaviour asserted. All use synthetic sets under `tmp_path` — no test
touches the vault, no test needs GDAL, QGIS, LibreOffice or `rsvg-convert`. Where a test needs a node that
"builds" something, it uses a `run: script` node whose script is a two-line Python file writing a declared
output, or a `run: command` node invoking `sys.executable`.

**A1 — `test_touching_one_input_rebuilds_only_affected_targets`.**
Setup: a set with four nodes: `a` (in `a.in` → out `a.out`), `b` (in `b.in` → out `b.out`), `c`
(in `a.out` → out `c.out`), `d` (in `b.out` → out `d.out`). Build once; all four `OK`. Record each output's
mtime and bytes. Action: rewrite `a.in` with different content; build again.
Expected: report `built == ("a", "c")` and `skipped == ("b", "d")`; `b.out` and `d.out` are byte-identical
**and** mtime-identical to before; exit 0; stdout contains `BUILD  a` and `SKIP  b`; the summary line is
`build: 2 built, 2 skipped, 0 blocked, 0 failed`.

**A2 — `test_rebuild_is_a_noop_when_nothing_changed`.**
Setup: A1's set, built once. Action: build again with no changes.
Expected: exit 0; `built == ()`; four `SKIP` lines; every output mtime unchanged; stdout contains
`build: 0 built, 4 skipped, 0 blocked, 0 failed`.

**A3 — `test_rewriting_an_input_with_identical_bytes_does_not_rebuild`.**
Setup: A1's set, built once. Action: rewrite `a.in` with *the same* bytes (new mtime), build again.
Expected: exit 0, `built == ()`. Locks §4.1 — mtime alone never causes a rebuild.

**A4 — `test_changing_a_node_arg_rebuilds_that_node`.**
Setup: a `verb:bfd` node with `view: block`, built once. Action: change `args.view` to `swimlane`; build.
Expected: that node rebuilds with reason `input changed` (the `node_def` component of the digest changed);
no input file was touched. Locks the `node_def` term in §3.10.

**A5 — `test_check_exits_3_and_names_the_stale_target`.**
Setup: A1's set, built once; then `a.in` modified. Action: `build --check`.
Expected: exit **3**; stdout contains `CHECK  a` with `STALE: input changed: a.in`; stderr contains
`check: NOT CURRENT`; **no file anywhere under `tmp_path` is created or modified** (assert by comparing a
full recursive `{path: (size, mtime_ns, sha256)}` snapshot before and after, including the absence of
`.technical_drawings_for_agents/`).

**A6 — `test_check_exits_0_when_current`.** Setup: A1's set, built. Action: `--check`.
Expected: exit 0; stdout `check: current (4 node(s))`; no writes (same snapshot assertion).

**A7 — `test_check_reports_a_missing_output_as_not_current`.**
Setup: A1's set, built; delete `c.out`. Action: `--check`.
Expected: exit 3; the `c` line reads `MISSING: output missing: c.out`. Locks §4.12.

**A8 — `test_check_exits_3_when_a_required_tool_is_absent`.**
Setup: a `command` node with `tool: definitely-not-a-real-binary-9f3a`. Action: `--check`.
Expected: exit 3; the report names the node and the tool; stderr's verdict counts `1 missing tool(s)`.

**A9 — `test_build_exits_2_and_builds_nothing_when_a_tool_is_absent`.**
Setup: two nodes, `x` (a working script node) then `y` (the fake-tool `command` node); nothing built yet.
Action: `build`.
Expected: exit **2**; stderr contains `required tool not found on PATH: definitely-not-a-real-binary-9f3a`
and `pre-flight failed — nothing was built`; **`x.out` does not exist**. Locks §4.7 — pre-flight precedes
execution even for a node ordered first.

**A10 — `test_a_cycle_is_a_clear_error_and_builds_nothing`.**
Setup: `p` (in `r.out` → out `p.out`), `q` (in `p.out` → out `q.out`), `r` (in `q.out` → out `r.out`).
Action: `build`.
Expected: exit 2; stderr matches exactly `error: <file>: dependency cycle: p -> q -> r -> p` (rotated to
the lexicographically smallest node); no output file exists; `load_drawing_set` raises `BuildError` with
the same message. Assert the string is identical across two runs and across a version of the file with the
`nodes:` block reordered.

**A11 — `test_build_order_is_independent_of_declaration_order`.**
Setup: two set files, A and B, with identical nodes but the `nodes:` mapping written in reverse order in B,
and a diamond graph (`a` → `b`, `a` → `c`, `b`+`c` → `d`) so the tie-break is exercised.
Expected: `topological_order` returns the identical tuple for both — `("a", "b", "c", "d")` — and the two
CLI runs produce byte-identical stdout (with `--timings` off). Locks §4.9.

**A12 — `test_a_missing_source_input_is_an_error_and_never_a_partial_build`.**
Setup: A1's set, nothing built, with `b.in` absent. Action: `build`.
Expected: exit 2; stderr `error: node 'b': declared input not found: <abs>/b.in`; **no output file exists
at all**, including `a.out` (which has no missing inputs and is ordered first). Locks "validate the whole
set before running anything" (§3.9 V8).

**A13 — `test_an_input_that_is_another_nodes_output_need_not_exist_yet`.**
Setup: A1's set on a clean tree (`a.out` … `d.out` absent, `a.in`/`b.in` present). Action: `build`.
Expected: exit 0; all four built. Complements A12.

**A14 — `test_declaring_meta_yaml_as_an_output_is_rejected`.**
Setup: a node with `outputs: [drawings/x/meta.yaml]`. Action: `load_drawing_set` / `build`.
Expected: `BuildError` / exit 2, message containing
`a build may never write meta.yaml`. Also assert the negative: a set that lists `meta.yaml` under
`inputs:` loads fine. Locks §0.3.

**A15 — `test_a_failing_node_leaves_no_half_written_output`.**
Setup: node `w` whose script writes `w.out` with the bytes `PARTIAL`, then `sys.exit(3)`. Pre-seed `w.out`
with the bytes `GOOD` and build a state record for it (build once with a script that exits 0, then swap the
script). Action: build.
Expected: exit 1; `w.out` **does not exist**; `w.out.partial` exists with the bytes `PARTIAL`; stdout/err
report `FAIL  w  exit 3` and the quarantine line; **no state record was written for `w`** (assert by
`--check` afterwards reporting `w` not current). Then assert the general invariant for a second variant
whose script exits non-zero *without* writing: `w.out` still holds `GOOD`, no `.partial` exists.

**A16 — `test_a_failing_node_stops_the_build`.**
Setup: `w` (fails) ordered before `z` (would succeed, depends on `w.out`), plus an independent node `zz`
ordered after `w` that does *not* depend on it.
Expected: exit 1; neither `z.out` nor `zz.out` exists; stderr contains
`error: node 'w' failed; 0 further node(s) run`. Locks "stop, don't soldier on".

**A17 — `test_an_external_node_is_never_run_and_blocks_downstream`.**
Setup: `e` (`run: external`, `outputs: [e.out]`, `reason: "a human does this"`) and `f` (in `e.out` → out
`f.out`). `e.out` absent.
Expected: exit 1; stdout `BLOCKED  e  external: a human does this`; `f.out` does not exist; **no process
was spawned** (assert via a `monkeypatch` on `subprocess.run` that records calls, or by asserting `e` has
no `exit_code` in the report). Repeat with `--force`: identical result plus
`note: --force does not run external node(s): e`.

**A18 — `test_an_external_node_that_is_current_does_not_block`.**
Setup: A17's set, with `e.out` present and a state record for `e` (write one via `--force`-free first
build after touching `e.out`… concretely: build once, which blocks; create `e.out`; build again).
Expected: exit 0; `e` reports `SKIP`/`OK` per §3.10 (an `external` node whose outputs exist and match
becomes `current`) and `f` builds.

**A19 — `test_target_selects_dependencies_only_and_names_stranded_dependents`.**
Setup: A1's set, all current; modify `a.in`. Action: `build --target a`.
Expected: exit 0; `built == ("a",)`; `c.out` unchanged; stdout contains
`note: 1 node(s) became stale and were not selected: c`. Locks §4.17.

**A20 — `test_unknown_target_and_empty_selection_are_errors`.**
Action (a): `build --target nope`. Expected: exit 2; stderr lists known targets then known nodes, each
sorted. Action (b): a set whose `targets:` maps a name to `[]`. Expected: exit 2 at load
(V2: non-empty list required).

**A21 — `test_force_rebuilds_everything_selected`.**
Setup: A1's set, all current. Action: `build --force`.
Expected: exit 0; `built` is all four in topological order; `skipped == ()`.

**A22 — `test_dry_run_writes_nothing_and_exits_zero_when_stale`.**
Setup: A1's set, built; `a.in` modified. Action: `build --dry-run`.
Expected: exit **0**; the plan lists `a` and `c` as stale in order; full-tree snapshot unchanged. Contrast
with A5. Also: `--check --dry-run` → exit 2, mutually exclusive.

**A23 — `test_layout_node_expands_component_specs_as_implicit_inputs`.**
Setup: a `verb:layout` node over a synthetic layout config in the shape `tests/test_layout.py:60-83`
builds (one component spec, one register), with `emit: geojson`. Build once. Action: modify the **component
spec** only — a file the set file never lists — and build again.
Expected: the layout node rebuilds, reason `input changed: components/unit.yaml`; stdout contains
`+N implicit input(s) from layout.yaml`. Then set `expand_inputs: false` on the node, rebuild to establish
state, modify the spec again: the node is **current** (proving the expansion is what tracked it, and that
the opt-out works). Locks §3.5.

**A24 — `test_an_implicit_input_that_is_also_an_output_is_dropped_and_reported`.**
Setup: a `verb:layout` node with `from_geojson` set and the register (`layout.py:164`) among its declared
`outputs`.
Expected: loads without error; stdout contains `1 implicit input is also an output of this node`; building
twice with no other change yields `built` then `skipped` (i.e. the node is not permanently self-staling).
Locks §4.10.

**A25 — `test_two_nodes_claiming_one_output_is_an_error`.**
Setup: two nodes both declaring `shared.out`. Expected: exit 2; message names both node names, sorted, and
the path. Locks V4 — and it is the error the real Basin set will hit on `basin-site.gpkg` (OQ-4).

**A26 — `test_an_open_geopackage_input_is_an_error`.**
Setup: a node with input `x.gpkg` (any bytes) and a sibling `x.gpkg-wal`. Expected: exit 2; message names
the node, the gpkg, the sidecar, and says to close QGIS; nothing built. Repeat with `x.gpkg-shm`. Then
delete both sidecars: the set builds. Locks §4.11.

**A27 — `test_unsupported_version_and_unknown_keys_are_precise_errors`.**
Cases, each asserting exit 2 and the exact message shape: `version: 2`; missing `version`; a top-level
`nodez:` key; `run: verb:ingest` (message lists the nine runners, sorted); `run: verb:render` with a `.py`
source (message names `run: script`); an `args` key not accepted by the runner (message lists the accepted
keys, sorted); `shell: true` on a `command` node; `timeout_s` on a `verb:layout` node; `--check --force`.

**A28 — `test_state_is_written_under_root_and_is_the_only_thing_written`.**
Setup: A1's set on a clean tree. Action: build.
Expected: the set of created paths is exactly the four declared outputs plus
`.technical_drawings_for_agents/build-state.json`. Nothing named `*.manifest.json` exists anywhere (locks the P3 boundary,
§4.2), and no file outside `root` was written.

**A29 — `test_size_mtime_digest_mode_is_reported`.**
Setup: an input with `digest: size-mtime`. Expected: every run prints
`note: 1 input(s) tracked by size+mtime, not content: <path>`; touching the file's mtime without changing
content **does** mark the node stale under this mode (the documented, weaker guarantee), whereas the same
file under `content` mode does not (cross-check with A3).

**A30 — `test_tool_version_change_notes_but_does_not_rebuild`.**
Setup: A1's set, built; then rewrite the state file's `tool_version` to `0.0.1`. Action: build.
Expected: exit 0; `built == ()`; stdout contains `state written by technical_drawings_for_agents 0.0.1, running <ver>`.
Locks §4.15.

### Backward-compatibility tests (a build must change nothing that exists)

**B1 — `test_every_existing_verb_still_runs_standalone`.** For each of `render`, `validate`, `bfd`, `pid`,
`component`, `layout`: invoke `cli.main([...])` exactly as the current `tests/test_example_smoke.py`,
`tests/test_bfd.py`, `tests/test_pid.py`, `tests/test_components.py`, `tests/test_layout.py` and
`tests/test_toolkit.py` do, and assert the same exit code and the same outputs. Concretely: the existing
test suite must pass **unmodified** — the PR adds test files and must not edit one. `ingest` is covered by
the existing `tests/test_ingest.py` / `tests/test_from_dxf.py` unchanged.

**B2 — `test_help_lists_build_and_every_previous_verb`.** `build_parser()` exposes subcommands
`{render, validate, ingest, bfd, pid, component, layout, build}` — assert the set equality, so a future
edit cannot drop a verb while adding one.

**B3 — `test_example_drawings_are_byte_identical_after_a_build_of_the_same_source`.** Take
`drawings/example/simple-section/`, render it with the existing `render` verb into `tmp_path`, then build
the same source through a one-node `run: script` drawing set into another `tmp_path`, and assert the
generated `.svg` and `.dxf` are byte-identical between the two paths. Locks "the driver changes no
output".

**B4 — `test_build_never_writes_a_meta_yaml`.** Build the repo's two example drawing directories through a
set and assert every `meta.yaml` under them is byte-identical and mtime-identical before and after.
Complements A14 at the behavioural level.

---

## 6. Out of scope — do NOT do these in this PR

1. **Manifests, provenance stamps, byte-reproducible emit, `SOURCE_DATE_EPOCH` handling** — P3 (#55).
   P2 defines the `StateStore` interface (§4.2) and nothing more. Do not write `*.manifest.json`. Do not
   add a `git` or `gitpython` dependency.
2. **A `geo` verb, `geo.yaml`, `gdalwarp`/`ogr2ogr` wrappers, a linked backdrop** — P8 (#60). Express geo
   steps as `command` nodes for now.
3. **Paper space, plot scale, ISO sheet sizes, asserted scale strings** — P1 (#53).
4. **A single tested PDF path, a render-fidelity check, a `--review` bundle** — P4 (#56). Do not touch
   `render.py`'s backend selection or its matplotlib fallback.
5. **New `validate` checks** (collision, frame containment, watermark-vs-status, revision tables) — P5
   (#57), P9 (#61). `build` only *calls* `validate` as a node.
6. **Any change to `gis-tool`.** It is invoked as a `command` node from a different Python
   environment. Do not import it, do not add it as a dependency, do not modify it.
7. **Any change to an existing `technical_drawings_for_agents` module's behaviour.** Additive-only: a new `build.py`, a new
   subparser block in `cli.py`, new test files. In particular do not "fix" `render_py`'s in-process
   `runpy` or `_cmd_render`'s exit-0-on-no-artifacts — both are worked around by design (§4.6) and
   changing them is a separate, parity-tested PR.
8. **Parallel execution (`--jobs`), a file watcher, a remote/shared build cache, remote execution.** Open
   a follow-up issue: *"technical_drawings_for_agents build: parallel node execution with isolated output capture"*.
9. **An `include:`/merge mechanism for set files** — deliberately rejected (§4.16). Open a follow-up if a
   second project needs shared fragments: *"drawing-set.yaml: shared fragments across sites"*.
10. **`run: verb:ingest`** — deliberately excluded (§3.4).
11. **Writing the plant project's `drawing-set.yaml`, adding
    `drawings/basin-site/meta.yaml`, or recording the missing `ogr2ogr` export command.** Those are vault
    changes in a different repository, and OQ-1 … OQ-4 are unresolved. This PR ships the tool plus a
    documented example set under `technical_drawings_for_agents/drawings/example/`. Open a vault-side task:
    *"plant Basin: author drawing-set.yaml, add basin-site/meta.yaml, record the placements-export
    command"*.
12. **A GitHub Actions workflow that gates the vault's drawing sets.** The vault is a separate local repo
    with no remote (§2.4) — CI in `workflows` cannot see it. In scope: a `--check` recipe documented in the
    README, and a CI step that runs `--check` against the repo's own example set. Out of scope: anything
    that assumes the vault is reachable from CI.
13. **Auto-generating a set file by scanning a project directory.** Inferring a graph from directory
    contents is exactly the guessing this PR replaces with a declaration.

---

## 7. Risks and migration

### 7.1 Existing consumers

`build` is a new subcommand. Nobody's current invocation changes; the seven existing verbs are untouched
(B1–B4). The only shared surface is `cli.build_parser()`, which gains a subparser. Risk: **low**. The
realistic regression is an accidental refactor of `render.py` or `components/cli.py` while wiring the
driver — guarded by requiring the existing test suite to pass unmodified (B1).

### 7.2 The state file

`build` creates `<root>/.technical_drawings_for_agents/build-state.json`. In the plant project `root` is inside the vault,
so:

* the consuming project must add `.technical_drawings_for_agents/` to its `.gitignore` **before** the first build, or the
  cache lands in git — and the vault's own rule against `git add -A`
  (`CLAUDE.md` §"Working alongside other agents") makes an accidental commit of it likely if it is not
  ignored. The README's build section must say this in the first paragraph.
* the file holds `st_mtime_ns` values, which are machine-specific — another reason it must never be
  committed. All paths in it are POSIX and relative to `root`, so a stray copy is at least not
  machine-pinned by path.
* deleting it is always safe: the next build rebuilds everything (state `unbuilt`).

### 7.3 Cost of hashing large inputs

`geo/site_ortho_s2.tif` is 484 MB and `geo/ga_backdrop.png` is 3.9 MB. First build hashes them; later runs
hit the size+mtime fast path (§4.1). If a project still finds `--check` too slow in a pre-commit hook, the
documented answer is `digest: size-mtime` on that input, with its weaker guarantee reported on every run
(A29). Risk: **medium**, mitigated and visible.

### 7.4 The vault is a different repository

`technical_drawings_for_agents` ships in `workflows` (remote the upstream repo); the plant project lives in the
local the vault vault with **no git remote**. Consequences: CI in `workflows` can only gate the
example sets in this repo; the real gate for Basin is a local pre-commit hook or a session-triggered
`--check`. Do not write a CI job that assumes otherwise (§6.12).

### 7.5 The first real set file will hit V4

Declaring the Basin graph honestly gives `basin-site.gpkg` two producers — the human editing footprints
and the `layout_*` reload — plus a third writer in `gis-tool stage` (which stages `ponds` into the same
file, `qgisproj.yaml:52-53`, while deliberately not staging `placements`, `:15`). V4 will reject that set.
This is the design working: the project has a genuine three-writers-one-file hazard that has been invisible
because nothing ever declared it. Migration is a project decision (OQ-4), not a tool change — the tool must
not soften V4 to accommodate it.

### 7.6 Migration path for the plant project (sequence, once OQ-1…OQ-4 are answered)

1. Add `.technical_drawings_for_agents/` to `03-Resources/DEMO Water Project/.gitignore` (or the vault root's).
2. Author `drawing-set.yaml` at the project root, with every step that has no recorded command declared
   `external` and its `reason` naming the open question.
3. Run `build --dry-run`: expect several `missing-output` verdicts for gitignored `geo/` artifacts and
   `BLOCKED` for the human steps. That first report *is* the honest inventory of the pipeline's gaps.
4. Run `build --force` once with QGIS closed to populate state and confirm the tool graph.
5. Wire `build --check` into a local pre-commit hook.
6. As P8 lands, convert the geo `external`/`command` nodes to `geo` verb nodes and delete the prose in
   `geo/README_regen.md`.

---

## 8. Open questions (gaps, not guesses)

**OQ-1 — how is the `placements` layer exported, reproducibly, and to where?** No command is recorded
anywhere in the project (§2.6.1); the historical destinations were `placements_export.geojson` (no
directory) and `/tmp/placements_now.geojson`. Until an answer exists, the node is `external`. Two candidate
answers, both needing the owner's decision: (a) a recorded `ogr2ogr -f GeoJSON <out> basin-site.gpkg
placements` command, making it a `command` node — but §4.11's WAL check means QGIS must be closed first;
(b) a new `gis-tool export` verb, which is a `gis-tool` change and out of scope here. The same
question covers the reverse direction, `basin_layout.geojson` → gpkg `layout_poly`/`layout_line`/
`layout_pt`, described in prose only at `basin.layout.yaml:12-13`.

**OQ-2 — what produces the six undocumented `geo/` artifacts?** `roads.geojson`,
`site_contours.geojson`, `site_dtm.tif`, `site_hillshade.tif`, `site_ortho.tif`, `site_ortho_s2.tif` have
no recipe in `geo/README_regen.md` (§2.6.5). Also, the documented intermediate `ga_backdrop.tif` is absent
from disk while its derived `.png` is present, so a faithful graph reports it `missing-output`. Until the
recipes exist these are declared `external` or omitted; P8 (#60) is where they get recorded properly.

**OQ-3 — should `drawings/basin-site/` get a `meta.yaml`?** Without one,
`technical_drawings_for_agents validate drawings/basin-site` fails (`validate.py:88-97`), so a `verb:validate` node on that
directory fails. The sheet's title-block fields currently come from `site-plan.yaml:meta`
(`source.py:110-116`), which duplicates what `meta.yaml` is for. This is a project fix and interacts with
P9 (#61).

**OQ-4 — who owns `basin-site.gpkg`?** Three writers (§7.5), one file, and V4 permits one. Candidate
resolutions: split the human-edited `placements` layer into its own GeoPackage; or make the reload and the
stage a single `command` node with the human edit as its `external` predecessor; or treat the gpkg as an
`external` output only and never let a tool write it. Needs the owner's decision; it is a real design choice
about where the editing boundary sits, not a tool detail.

**OQ-5 — should `verb:bfd` / `verb:pid` expand implicit inputs?** `drawings/basin-bfd/` references four
logo assets under `assets/`. Whether `bfd.load` resolves asset paths in a way the driver can derive was not
established from the code, so `expand_inputs` defaults `false` for those runners (§3.5) and their assets
must be listed explicitly. Determine before implementing; if `bfd` does resolve them, add the expansion in
a follow-up rather than guessing here.

**OQ-6 — what is the intended `--check` cadence?** Review item 24 asks for a "CI/pre-commit gate". Given
§7.4 (no remote on the vault), is the gate a local `pre-commit` hook, a `technical_drawings_for_agents` invocation inside
the vault's existing session-triggered workflows, or both? This changes only documentation, not code.

---

# ADDENDUM — coordinator resolutions (binding), 2026-07-25

Added by the senior-engineer coordinator before dispatching implementation. These settle open questions the
spec correctly refused to guess at. **They are binding: do not re-litigate them, and do not implement an
alternative.**

## A1 — OQ-4 resolved: "one output, one producer" applies at LAYER granularity

The spec observes that three things write `basin-site.gpkg` — the human's QGIS edits to `placements`, the
`layout_*` reload, and `gis-tool stage` writing `ponds` — and that a file-granularity "one output, one
producer" rule would reject the first honest Basin drawing set. Correct observation; the rule is at the wrong
granularity.

**Resolution:**

1. **A GeoPackage is a *container*, not an output.** The unit of production is the **layer**. The rule is
   *one producer per `gpkg:layer`*, and two nodes writing different layers of the same file is legal.
2. **A node declares its outputs as `<gpkg-path>:<layer>`**, and the graph validates uniqueness on that
   compound key. Two nodes declaring the same `path:layer` is a hard error naming both.
3. **The human-edited `placements` layer is an `external` INPUT the build never writes.** It is the head of
   the dependency chain — the point where a human exercises judgement on imagery — and no `build` invocation
   may produce it. A set declaring `placements` as an output is a hard error. `--force` cannot override this.
4. **Reads are unconstrained.** Any number of nodes may read a layer.

Rationale: the physical file is an implementation detail of GeoPackage; the *layer* is what a step actually
produces. Enforcing at file granularity would either reject a legitimate project or push the project into
one-gpkg-per-layer, which loses the single-artifact property that makes a GeoPackage worth using.

## A2 — the `-wal`/`-shm` guard is a precondition of any node that writes a GeoPackage

P8 (#60, shipped) established that OGR does **not** refuse a GeoPackage with live `-wal`/`-shm` sidecars — an
overwrite succeeded with both present, and the live Basin project has both right now. Any build node writing a
`gpkg:layer` must therefore check for those sidecars in **pre-flight**, before the first byte is written, and
fail naming the file. This is a build-driver concern, not only a `geo`-verb concern.

## A3 — P3 (#55) has shipped; its interface is real, not a protocol to stub

The spec defines a `StateStore` protocol and correctly declines to build manifests. **P3 has since landed.**
Use the real thing:

- `technical_drawings_for_agents.provenance` provides manifests, a two-digest model (`build.digest` over input **content**,
  never paths, and `output.sha256`), and **`emit_digest(path) -> str`**, which **raises** for a format declared
  non-reproducible rather than returning a value a caller could wrongly compare.
- **Staleness is content-hash based**, per P3's canonical policy. Keep the spec's size+mtime fast path as an
  optimisation only — never as the authority.
- Do **not** re-implement digesting, rounding, or canonical text writing. P3 owns them.

## A4 — every build must end in a reviewable PDF

From the vault standard *Engineering Drawings as Code* §Pipeline contract rule 6: a pipeline emits standard
interchange artifacts **and always finishes by producing a PDF**. A `build` that leaves nothing visually
reviewable is not finished. Therefore:

- A set whose targets include a sheet must produce that sheet's PDF as part of a default `build`.
- `--check` is exempt (it writes nothing by design).
- If a declared PDF target cannot be produced, the build **fails** — it does not succeed quietly having
  emitted only intermediates.
