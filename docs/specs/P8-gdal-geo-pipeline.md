# P8 — GDAL-only geo pipeline: `geo.yaml` + linked pre-clipped backdrop

**Issue:** upstream #60 · **Owner:** `senior-engineer` ·
**Milestone:** drawing-workflow hardening · **Status:** SPEC — not implemented
**Related:** #51 (gpkg↔text bridge, boundary defined in §6), #53 (P1 paper space), #55 (P3 manifest)

Everything asserted about GDAL behaviour in this spec was **measured** on this machine against
conda-forge GDAL 3.10.2 before being written down. Measured facts are marked **[verified]** with the
command that produced them. Nothing here is inferred from documentation.

---

## Hard constraints (restated — these bound every decision below)

1. **Backward compatibility is sacred.** Existing `gis-tool stage` / `build` / `validate` behaviour
   must not change for any config that exists today. `geo` is a *new verb*; the `geo:` /
   `rasters:` / `vectors:` keys are new *top-level* keys in a *new file*. No existing config gains a
   required field, loses a default, or changes output.
2. **Never commit confidential data.** Site imagery, orthomosaics, point clouds and terrain stay
   outside git, unconditionally. Every test in §5 runs on synthetic fixtures generated in `tmp_path`.
3. **Never invent a capability or a coordinate.** See the preamble. The one real-world coordinate used
   in tests (Basin control point **P5**) is cited to its source document.
4. **The ISSUED gate is untouchable.** The `geo` verb has no knowledge of drawing status, does not read
   or write a title block, and cannot emit or mutate a sheet. It produces rasters, vectors and JSON.
5. **Determinism and CLI-first.** No step in this pipeline may import `qgis`, launch a GUI, or require
   a QGIS session. Prefer loud failure over a silent no-op — this is the single most load-bearing rule
   in this spec, because §2 shows that *both* GDAL and librsvg silently succeed on empty output.

---

## 1. Intent

Good looks like this: a `geo.yaml` beside the drawing declares every source raster and vector, the
target CRS, the named sheet frames, and how each derived artifact is produced — and one command,
`gis-tool geo geo.yaml`, regenerates the whole derived `geo/` directory byte-for-byte from those
declarations using nothing but `gdalwarp`, `gdal_translate`, `ogr2ogr` and `gdalinfo`. Each output
carries a committed manifest recording the exact argv, the source digests, the resolved frame and the
measured data coverage. The sheet then **links** the pre-clipped backdrop at the sheet's own target DPI
instead of base64-inlining it, and refuses to emit if the sidecar's recorded frame disagrees with the
sheet's viewport by more than a tolerance.

The failure it prevents is the one already sitting in the Basin project directory, in three forms.
First, **prose as a build step**: `geo/README_regen.md` is a shell snippet in a Markdown file, so the
backdrop's parameters exist only as a human instruction — nobody can answer "what produced this file?"
without trusting a comment. Second, **silent geometric error**: the recipe in that file produces a clip
4 cm narrower than the frame it is declared to fill, and the sheet stretches it to fit — a scale error
that no eyeball will ever catch. Third, **silent empty output**: `geo/site_ortho.tif` is a 30 MB
all-white, fully-transparent raster produced by a warp that exited 0, and the current backdrop covers
only 33% of the sheet frame with imagery — a fact that appears nowhere in any file. This spec turns all
three into deterministic, tested, manifest-recorded facts.

---

## 2. Current state (measured)

### 2.1 `gis-tool` today

- `gis-tool/src/gis_tool/cli.py:24-43` — exactly three verbs: `build`, `stage`, `validate`.
  There is **no `geo` verb** and no raster-processing code path anywhere in the package.
- `cli.py:17-21` — every error is collapsed to **exit 1**:
  `except (BuildError, ConfigError, StageError) as exc: print(f"error: {exc}"); return 1`.
- `config.py:15` — one module error class, `ConfigError(ValueError)`; `config.py:89-117` validates on
  load with positional messages (`layers[2].source must contain exactly one of …`);
  `config.py:290-294` normalises CRS to `EPSG:<int>` and rejects anything else;
  `config.py:304-308` resolves relative paths against the config's own directory.
  This is the house pattern the new loader must match.
- `config.py:19-64` — frozen dataclasses for every config node; `GisConfig` (`:57-64`) is the root and
  exposes `resolve_gpkg()`, `resolve_raster()`, `resolve_stage_source()`.
- `config.py:33-37` — `SourceConfig.kind` is `gpkg_layer | raster | xyz`. A raster source is a **path
  to a full-resolution file**; `build.py:154` hands it straight to `QgsRasterLayer`. Nothing clips.
- `stage.py:42-47` — vector staging uses the **`osgeo` Python bindings** (`from osgeo import gdal`),
  guarded with an ImportError → `StageError("The stage command requires GDAL/OGR from the QGIS conda
  environment")`. `stage.py:111-118` calls `gdal.VectorTranslate`.
- `stage.py:57-71` — the only manifest today: `<gpkg>.manifest.json`, `{gpkg, layers:[{into, source,
  crs, feature_count}]}`. No digests, no tool version, no argv, no timestamps. This is the seed the
  P8 manifest extends (and the shape P3 #55 will generalise).
- `build.py:48-59` — `init_qgis()` boots a headless `QgsApplication`. `build`/`validate` are
  PyQGIS-only; `stage` is GDAL-only. That split already exists and P8 keeps it.
- `gis-tool/README.md:17-31` — install is `micromamba create -n qgis -c conda-forge qgis python=3.12`
  then `micromamba run -n qgis python -m pip install -e gis-tool`. **The package is installed into
  the conda-forge QGIS environment.** This is the fact that makes GDAL discovery easy (§3.4).
- `the GIS tool's README` and `.github/workflows/build.yml` — two CI jobs: `python`
  (ubuntu, plain pip, `pytest -q -rs` over the whole repo) and `qgis`
  (`mamba-org/setup-micromamba@v1`, conda-forge qgis, `pip install -e gis-tool`,
  `pytest -q -rs gis-tool`). §5 assigns each test to a job.

### 2.2 The real project — the defect, in files

`<project>/drawings/basin-site/`

**`geo/README_regen.md:5-13` — the core defect. Raster prep is prose for a human:**

```sh
gdalwarp -t_srs EPSG:32630 -te 788392 322125 788702 322395 -tr 0.12 0.12 -r bilinear -dstalpha \
  "$OD/GIS/Basin/.../20260609_Basin S2_Orthomosaic.tif" ga_backdrop.tif
gdal_translate -of PNG ga_backdrop.tif ga_backdrop.png
ogr2ogr -f GeoJSON -t_srs EPSG:32630 ponds.geojson "$OD/GIS/Basin/Basin Ponds and embankment footprint.kmz"
```

Three separate defects are latent in those three lines, all **[verified]**:

1. **`-te` + `-tr` does not honour the requested extent.** Re-running that exact warp against a
   synthetic source produces `2583 x 2250` px with `Upper Right (788701.960, 322395.000)` — the clip
   is **4 cm short of the declared `788702`** because `310 / 0.12 = 2583.33` is truncated to an integer
   pixel count and the extent follows the pixels, not the request.
   `source.py:64-65` then declares `FXMIN, FYMIN, FXMAX, FYMAX = 788392, 322125, 788702, 322395` and
   `source.py:119-124` stretches the image across the full frame rect with
   `preserveAspectRatio="none"` — a silent 1.000129 x-scale error applied to the whole backdrop.
   *Verified:* `gdalwarp -te 788392 322125 788702 322395 -tr 0.12 0.12 … ; gdalinfo`.
2. **`gdal_translate -of PNG` throws the georeferencing away.**
   `gdalinfo geo/ga_backdrop.png` reports `Upper Left (0.0, 0.0)` / `Lower Right (2583.0, 2250.0)` —
   **pixel coordinates, no CRS, no affine.** The only surviving tie between those pixels and the ground
   is the hard-coded constant at `source.py:65` plus the prose in `README_regen.md`. **[verified]**
3. **The warp silently produces a mostly-empty raster and says nothing.**
   `geo/ga_backdrop.png` alpha coverage = **0.3303** — 33% of the sheet frame has imagery; **all four
   frame corners are nodata**. Warping the real S2 ortho over the GA frame independently reproduces
   **0.331262** coverage. The ortho's own bbox is `788282..788832 E / 322014..322564 N`, so the frame is
   *inside* the bbox — the missing two-thirds is the drone flight edge, invisible to a bbox check.
   **[verified]** (`gdal.Warp` to MEM over the GA frame, alpha band mean).

**`geo/site_ortho.tif` is a 30 MB fully-blank derived artifact.** Bands 1–3 are constant 255, band 4
(alpha) is constant 0 across all 2750×2750 px. Some earlier warp exited 0 and wrote nothing usable, and
it has been sitting in the directory ever since. **[verified]** This is the silent-no-op failure mode,
already realised, in the very directory P8 replaces.

**`source.py:29-32` — the capability gap, self-documented in the drawing:**

> `CAPABILITY GAP (tracked as a workflows issue): technical_drawings_for_agents has no georeferenced raster-backdrop
> helper … so the ortho is added here as one inline <image> positioned via ViewBox`

**`source.py:38, 119-125` — the base64 hack and its cost:**

```python
b64 = base64.b64encode(BACKDROP.read_bytes()).decode()
d.add(f'<image href="data:image/png;base64,{b64}" x="{ix:.1f}" y="{iy:.1f}" '
      f'width="{iw:.1f}" height="{ih:.1f}" preserveAspectRatio="none" opacity="0.92"/>')
```

Measured sizes: `geo/ga_backdrop.png` **3,933,477 B** → base64 **5,244,636 B** →
`out/STA-SITE-GA-001.svg` **5,721,744 B**, `out/STA-SITE-GA-001.pdf` **5,189,026 B**. The 3.9 MB PNG is
re-read and re-encoded on **every** `python3 source.py`, and `.gitignore` has to exclude `out/*.svg`
because of it ("large SVG w/ embedded raster is regenerable via source.py").

**`qgis/basin-site.qgisproj.yaml:46-47`** — the ortho layer is a hard-coded absolute OneDrive path
(`<cloud-drive>/…`), at full 484 MB resolution.
`qgis/README.md:15` acknowledges it: "Absolute path (synced on this machine)."

**`qgis/README.md:18-31`** — the documented build path is
`micromamba run -n qgis env QT_QPA_PLATFORM=offscreen gis-tool stage|build`, i.e. the conda-forge
env is *already* the sanctioned execution context.

**A live QGIS session is holding the GeoPackage right now.** `qgis/basin-site.gpkg-shm` (32,768 B) and
`qgis/basin-site.gpkg-wal` (0 B) are both present, and `.gitignore` documents them: "SQLite WAL/SHM
sidecars — live QGIS session state, never source". Both `basin-site.gpkg` and `site-plan.yaml` show as
modified in `git status`. **[verified]**

**`.gitignore` (basin-site)** already encodes the confidentiality rule this spec must preserve:

```
# geo/ = derived from OneDrive source data (rasters + reprojected vectors) — regenerable, kept out of git.
# Confidential site imagery/terrain stay out; source of truth = the OneDrive GIS + site-plan.yaml + source scripts.
geo/
```

### 2.3 The local GDAL toolchain — and the issue's one omission

The issue does not mention it, and it is the single biggest constraint on the implementation:

**Homebrew GDAL is broken on this machine.** `/opt/homebrew/bin/ogrinfo --version`:

```
dyld[…]: Library not loaded: /opt/homebrew/opt/x265/lib/libx265.215.dylib
  Referenced from: /opt/homebrew/Cellar/libheif/1.20.2/lib/libheif.1.20.2.dylib
  Reason: tried: '/opt/homebrew/Cellar/x265/4.2/lib/libx265.215.dylib' (no such file) …
```

**The documented symlink workaround is dead.** The installed keg is `x265 4.2`, which ships
`libx265.216.dylib` — there is no `.215` anywhere on the machine and no `x265 4.1` keg to point at.
A `215 → 216` symlink would be an ABI lie, not a fix. (The real repair is `brew reinstall libheif`
against x265 4.2 — **out of scope**, and *the tool must not depend on it happening*.) **[verified]**

**Worse: the failure is `exit 0`.** The dyld error goes to stderr and the shell reports success, so a
naive `shutil.which("gdalwarp")` + `subprocess.run(...)` will pick the broken binary and can be read as
having worked. **[verified]** (`ogrinfo --version 2>&1; echo $?` → dyld error, `0`.)

**The working GDAL is conda-forge, inside the `qgis` micromamba env:**

| | Homebrew | conda-forge (`qgis` env) |
|---|---|---|
| prefix | `/opt/homebrew` | `~/.local/share/mamba/envs/qgis` |
| `gdalwarp` | present, **fails to load** | present, works |
| version | 3.12.1 (unusable) | **GDAL 3.10.2** (2025-02-11) |
| Python bindings | n/a here | `osgeo` 3.10.2 importable |

And the decisive fact: `gis-tool` is pip-installed *into* that env (README:24), so at run time
`sys.prefix == ~/.local/share/mamba/envs/qgis` and
`os.path.exists(sys.prefix + "/bin/gdalwarp")` is **True**. **[verified]** §3.4 builds discovery on that.

### 2.4 GDAL behaviour measured for this spec

All against conda-forge GDAL 3.10.2 in the `qgis` env, on synthetic fixtures.

| # | Question | Measured answer |
|---|---|---|
| V1 | `-te` + `-tr` honours the extent? | **No.** 310 m / 0.12 → 2583 px, xmax = 788701.96 (4 cm short). |
| V2 | `-te` + `-ts` honours the extent? | **Yes, exactly.** `-te 788392 322125 788702 322395 -ts 2929 2552` → `Upper Right (788702.000, 322395.000)`, pixel size 0.105838 × 0.105799 (slightly anisotropic). |
| V3 | GTiff `TILED=YES COMPRESS=DEFLATE` byte-reproducible? | **Yes.** Two identical warps → identical SHA256 (`2134f2c7…`). |
| V4 | Does GDAL stamp a version or timestamp into the GeoTIFF? | **No.** The bytes contain no `GDAL`, no `3.10`, no `2025`/`2026` substring; metadata is `{AREA_OR_POINT: Area}` only. |
| V5 | Multithreaded warp byte-identical to single-threaded? | **Yes.** `multithread=True, NUM_THREADS=ALL_CPUS` → identical SHA256. |
| V6 | PNG output byte-reproducible? Worldfile? | **Yes**, identical SHA256 across runs; `-co WORLDFILE=YES` writes a 6-line `.wld` (pixel-centre origin `788392.0529…`, `322394.9471…`); a `.png.aux.xml` is also written unless PAM is disabled. |
| V7 | `GDAL_PAM_ENABLED=NO` suppresses `.aux.xml` but keeps the worldfile? | **Yes** — `q.png` + `q.wld` written, no `q.png.aux.xml`. |
| V8 | Does `gdalinfo -stats` mutate the raster? | **No** — `.tif` SHA256 unchanged; stats land in `.aux.xml`, and with `GDAL_PAM_ENABLED=NO` no sidecar is written at all. |
| V9 | Can coverage be measured without Python bindings? | **Yes.** `gdalinfo -json -stats` alpha-band `mean / 255` = `0.111557` vs numpy-measured `0.111556`. Bindings are not needed anywhere. |
| V10 | Warp to an extent **entirely outside** the source? | **Exit 0**, full-size output, alpha coverage `0.0`. Completely silent. |
| V11 | Warp to an extent **partly outside**? | **Exit 0**, coverage `0.111556`, no warning. |
| V12 | GeoPackage byte-reproducible? | **No** by default (`gpkg_contents.last_change` = wall clock, e.g. `2026-07-24T22:21:27.323Z`). |
| V13 | Can it be pinned? | **Yes.** `OGR_CURRENT_DATE=2020-01-01T00:00:00.000Z` → `last_change` fixed, two writes byte-identical. |
| V14 | Does OGR refuse to write a `.gpkg` with `-wal`/`-shm` present? | **No.** With both sidecars fabricated, `VectorTranslate(..., accessMode="overwrite")` **succeeded**. The guard must be ours. |
| V15 | GeoJSON byte-reproducible? | **Yes**, with `-lco COORDINATE_PRECISION=3` (emits `xy_coordinate_resolution: 0.001` and fixed-precision coords). |
| V16 | Does `rsvg-convert` resolve a **relative** `href` to a sibling PNG? | **Yes** — `rsvg-convert 2.62.3`, resolved against the **SVG file's own directory**, not the cwd (verified by running from `/tmp`). PDF 3,840 B, both runs identical. |
| V17 | What does `rsvg-convert` do when the linked PNG is **missing**? | **Exit 0**, writes a 1,085-byte PDF with a blank backdrop. **Silent.** This is the new failure mode linking introduces and §3.7/§5 must close. |

---

## 3. Design

### 3.1 Where the code lives, and why

Two packages, one boundary, chosen so that **`technical_drawings_for_agents` never needs GDAL**:

| Component | Package | Module | Needs GDAL? | CI job |
|---|---|---|---|---|
| `geo.yaml` loader + validation | `gis-tool` | `gis_tool/geoconfig.py` | no | `python` |
| GDAL discovery + probe | `gis-tool` | `gis_tool/gdaltools.py` | to *run*, not to import | `python` (unit) + `qgis` (integration) |
| the `geo` verb (warp/translate/ogr) | `gis-tool` | `gis_tool/geo.py` | yes | `qgis` |
| gpkg lock guard | `gis-tool` | `gis_tool/gpkglock.py` | no | `python` |
| sidecar-manifest reader + `<image>` emit | `technical_drawings_for_agents` | `technical_drawings_for_agents/backdrop.py` | **no** (stdlib `json` only) | `python` |

Rationale: GDAL already lives in `gis-tool` (`stage.py:42-47`) and its CI job (`build.yml`, `qgis`)
already provisions conda-forge GDAL. `technical_drawings_for_agents` is installed in the plain `python` job with no
conda; giving it a GDAL dependency would either break that job or force every drawing author into
micromamba. The *only* thing a sheet needs is the sidecar's **manifest JSON** — a path, a pixel size,
a frame bbox and a digest. That is pure stdlib, so `technical_drawings_for_agents/backdrop.py` reads JSON and emits an
`<image>` element, and knows nothing about rasters.

### 3.2 `geo.yaml` schema

One file per drawing directory, conventionally `geo/geo.yaml`, **committed** (it is text, not imagery).
Loaded by `load_geo_config()` into frozen dataclasses; every field validated at load time with a
positional message in the `config.py:89-117` style. Unknown keys at any level are a **hard error**
(a typo'd `resamlpe:` silently taking a default is exactly the class of defect this spec exists to
kill). Paths are resolved relative to the `geo.yaml`'s own directory, matching `config.py:304-308`.

```yaml
version: 1
crs: EPSG:32630
out_dir: .

gdal:
  prefix: null
  min_version: "3.6"
  pam: false

defaults:
  resample: cubic
  compress: DEFLATE
  nodata: alpha
  dpi: 300
  warn_coverage: 0.999
  min_coverage: 0.0

frames:
  ga-001:
    bbox: [788392, 322125, 788702, 322395]
    plot_scale: 1250
    dpi: 300

rasters:
  - name: ga_backdrop
    source: "${PROJECT_GIS}/Basin/Basin S2 survey 2026-06-09/20260609_Basin S2_Orthomosaic.tif"
    frame: ga-001
    kind: imagery
    resample: cubic
    nodata: alpha
    min_coverage: 0.30
    warn_coverage: 0.999
    link: {format: png, zlevel: 9}

vectors:
  - name: ponds
    source: "${PROJECT_GIS}/Basin/Basin Ponds and embankment footprint.kmz"
    to: geojson
    t_srs: EPSG:32630
    coordinate_precision: 3
```

#### Top level

| Field | Type | Default | Validation |
|---|---|---|---|
| `version` | int | **required** | must be `1`; any other value → `GeoConfigError` naming the supported versions. Forward-compat hook. |
| `crs` | str | **required** | `^EPSG:\d+$` after strip, normalised to `EPSG:<int>` — reuse `config._normalise_crs` (`config.py:290-294`). This is the default `t_srs` for every target and the CRS of every `frames[*].bbox`. |
| `out_dir` | str | `"."` | resolved against the `geo.yaml` dir; created with `parents=True, exist_ok=True`. Must not be inside `.git/`. |
| `gdal` | mapping | `{}` | see below |
| `defaults` | mapping | `{}` | any raster-level or vector-level field except `name`/`source`/`frame`; a target-level value always wins. Validated against the same rules as the per-target field, so a bad default fails at load, not at use. |
| `frames` | mapping | **required, non-empty** | keys match `^[a-z0-9][a-z0-9_-]*$`; every `rasters[*].frame` must name one; an unreferenced frame is a **warning**, not an error (a frame may exist for a sheet not yet built). |
| `rasters` | list | `[]` | `rasters` and `vectors` may not both be empty → `GeoConfigError("geo.yaml declares no rasters and no vectors")`. |
| `vectors` | list | `[]` | as above |

#### `gdal`

| Field | Type | Default | Validation |
|---|---|---|---|
| `prefix` | str \| null | `null` | if set, must be a directory containing `bin/gdalwarp`; skips discovery (§3.4). Non-existent → exit 2 with the path echoed. |
| `min_version` | str | `"3.6"` | `^\d+\.\d+$`; the probed GDAL must be `>=` this, compared as an integer tuple. 3.6 is the floor for the `-ts`/`-te` and `gdalinfo -json` behaviour relied on here; the verified environment is 3.10.2. |
| `pam` | bool | `false` | `false` → run every GDAL child with `GDAL_PAM_ENABLED=NO`, suppressing `.aux.xml` (V7/V8). Set `true` only if a consumer genuinely needs PAM sidecars; they are then excluded from digest comparison. |

#### `frames[<name>]`

The **single home of a clip extent**. Exactly one of `bbox` or `from_sheet` (mutually exclusive,
`has_a == has_b` → error, mirroring `config.py:143-144`).

| Field | Type | Default | Validation |
|---|---|---|---|
| `bbox` | [num × 4] | one-of | `[xmin, ymin, xmax, ymax]` in top-level `crs`; exactly 4 numeric; `xmin < xmax`, `ymin < ymax` (reuse the `config.py:149-157` messages verbatim). |
| `from_sheet` | str | one-of | Path to a P1 (#53) sheet-descriptor JSON; the frame is read from its viewport model extent. See §3.8. Unreadable/absent → exit 3. |
| `plot_scale` | int | none | `> 0`. Required **iff** pixel count is derived from `dpi` (below). `1250` means 1:1250. |
| `dpi` | int | `defaults.dpi` (300) | `50 <= dpi <= 2400`. |
| `pixels` | [int × 2] | none | `[nx, ny]`, both `>= 1`. **Mutually exclusive with `plot_scale`+`dpi`.** Escape hatch for round-number test fixtures and for frames with no paper meaning. |
| `max_pixels` | int | `40_000_000` | `nx * ny` above this → error naming both numbers. Guards against a typo'd dpi producing a 40 GB warp. |

**Pixel-count derivation** (the interface with P1 #53, §3.8):

```
paper_mm_x = (xmax - xmin) * 1000 / plot_scale
paper_mm_y = (ymax - ymin) * 1000 / plot_scale
nx = round(paper_mm_x / 25.4 * dpi)          # banker's rounding is fine; it is recorded
ny = round(paper_mm_y / 25.4 * dpi)
res_x = (xmax - xmin) / nx                    # DERIVED, never declared
res_y = (ymax - ymin) / ny
```

Worked, for `ga-001` at 1:1250 / 300 dpi: `310 m → 248.0 mm → 2929.13 → nx = 2929`;
`270 m → 216.0 mm → 2551.18 → ny = 2551`; `res_x = 0.105838 m`, `res_y = 0.105841 m`.
Both `nx, ny >= 1` or error. Non-square pixels (here ~3 parts per million) are **accepted** — see D3.

#### `rasters[i]`

| Field | Type | Default | Validation |
|---|---|---|---|
| `name` | str | **required** | `^[a-z0-9][a-z0-9_-]*$`, unique across `rasters` **and** `vectors` (they share an output namespace). Duplicate → `rasters[3].name duplicates target name 'ga_backdrop'`. |
| `source` | str | **required** | after `${VAR}` expansion (§3.3) must be an existing readable file, else exit 3. May be a GDAL connection string; if it is not an existing path, `gdalinfo` is used to decide readability rather than `Path.exists`. |
| `frame` | str | **required** | must be a key of `frames`. |
| `kind` | enum | `imagery` | `imagery \| dtm \| hillshade \| mask`. Selects the *default* `resample` and `nodata` (D5) and is recorded in the manifest. Purely a defaults selector — no hidden behaviour. |
| `resample` | enum | by `kind` | `near \| bilinear \| cubic \| cubicspline \| lanczos \| average \| mode \| max \| min \| med \| q1 \| q3` — the `gdalwarp -r` set. Anything else → error listing the accepted values. |
| `nodata` | str | by `kind` | `alpha` → `-dstalpha`; `value:<num>` → `-dstnodata <num>`; `none` → neither. Regex `^(alpha|none|value:-?\d+(\.\d+)?)$`. |
| `src_nodata` | num \| null | `null` | `-srcnodata`. Only for sources that fail to declare their own. |
| `bands` | [int] \| null | `null` | 1-based, each `>= 1`; → `-b` per band. Non-empty if given. |
| `compress` | enum | `DEFLATE` | `DEFLATE \| LZW \| NONE`. **`JPEG` is rejected** for the authoritative clip (D2). |
| `predictor` | enum | `auto` | `auto \| 1 \| 2 \| 3`. `auto` → `2` for integer types, `3` for Float32/64, `1` for `COMPRESS=NONE`. Data type comes from `gdalinfo -json` on the source. |
| `min_coverage` | float | `0.0` | `0.0 <= v <= 1.0`. Measured coverage **strictly below** → **exit 4** (D6). |
| `warn_coverage` | float | `0.999` | `min_coverage <= v <= 1.0`. Coverage below → warning on stderr + `coverage_warning: true` in the manifest. |
| `link` | mapping \| null | `null` | present → also emit a sheet-linkable sidecar. |
| `link.format` | enum | `png` | `png \| jpeg`. |
| `link.zlevel` | int | `9` | `1..9`, PNG only; error if given with `format: jpeg`. |
| `link.quality` | int | `90` | `1..100`, JPEG only; error if given with `format: png`. |
| `link.opacity` | float | `1.0` | `0.0..1.0`. Recorded in the manifest for the sheet to apply; **not** baked into pixels. |

#### `vectors[i]`

| Field | Type | Default | Validation |
|---|---|---|---|
| `name` | str | **required** | as `rasters[i].name`, same namespace. |
| `source` | str | **required** | `${VAR}`-expanded; supports the existing `path\|layername=x` spec — **reuse `config.split_ogr_layer_spec` (`config.py:120-127`)** rather than re-parsing. |
| `to` | enum | `geojson` | `geojson \| gpkg`. |
| `gpkg` | str | none | **required iff** `to: gpkg`; error if present with `to: geojson`. |
| `layer` | str | none | **required iff** `to: gpkg`. |
| `t_srs` | str \| `keep` | top-level `crs` | `EPSG:<n>` or the literal `keep` (no `-t_srs`; preserves native CRS, matching the `stage` contract in README:109-110). |
| `coordinate_precision` | int | `3` | `0..9`; `-lco COORDINATE_PRECISION`. GeoJSON only; error with `to: gpkg`. |
| `where` | str \| null | `null` | `-where`. |
| `select` | [str] \| null | `null` | `-select a,b,c`; non-empty if given. |
| `promote_to_multi` | bool | `false` | `-nlt PROMOTE_TO_MULTI`. |

### 3.3 `${VAR}` source roots

`source` strings support `${NAME}` expansion from the process environment, `NAME` matching
`^[A-Z][A-Z0-9_]*$`. An unset variable is a **hard error at load**:
`rasters[0].source references ${PROJECT_GIS}, which is not set in the environment`. No fallback, no
partial expansion, no `${VAR:-default}`.

Two reasons, both concrete. It removes the hard-coded absolute OneDrive path
(`qgis/basin-site.qgisproj.yaml:47`) from a committed file, so the config is portable to another
machine. And it keeps a home-directory string out of the committed manifest — the manifest records
the **declared** (unexpanded) source string plus its digest; the resolved absolute path goes only into
a gitignored `*.local.json` (§3.6).

This applies to `geo.yaml` **only**. `gis-tool stage`/`build` configs are untouched (constraint 1).

### 3.4 Locating GDAL

Given §2.3, discovery is an ordered chain, and the winner must **pass a probe** before use. First
candidate that probes clean wins.

1. `--gdal-prefix <dir>` (CLI flag) — highest precedence, for CI and for pinning.
2. `geo.yaml` `gdal.prefix`.
3. `GIS_TOOL_GDAL_PREFIX` env var.
4. **`Path(sys.prefix)`** — the running interpreter's own environment. Because `gis-tool` is
   pip-installed into the conda-forge `qgis` env (README:24), this resolves to
   `…/envs/qgis/bin/gdalwarp` with **zero configuration** on this machine. **[verified]**
5. `CONDA_PREFIX`, then `MAMBA_ROOT_PREFIX + "/envs/qgis"`.
6. `shutil.which("gdalwarp")` → its parent's parent. **Last** resort, deliberately, because on this
   machine PATH resolves to the broken Homebrew install.

**The probe** — run `<prefix>/bin/gdalwarp --version` with a 20 s timeout and require *all* of:
non-zero-length stdout matching `^GDAL (\d+)\.(\d+)\.(\d+)`; version `>= gdal.min_version`; **empty
stderr apart from known-benign lines**; and `gdalinfo`, `gdal_translate`, `ogr2ogr` all present and
executable in the same `bin/`. The Homebrew install fails on the stdout-match and stderr checks
(the dyld error goes to stderr and stdout is empty), which is exactly the point: **`exit 0` is not
sufficient evidence that a GDAL binary works** (§2.3, V-note). Ignoring the exit code and requiring a
parseable version banner is the only check that catches it.

On total failure → **exit 2** with every candidate listed, in order, each with the reason it was
rejected, and the fix:

```
error: no usable GDAL toolchain found. Tried, in order:
  1. sys.prefix        /usr/local/…                  no bin/gdalwarp
  2. PATH              /opt/homebrew/bin/gdalwarp    probe failed: no version banner on stdout;
                       stderr: dyld[…]: Library not loaded: …/libx265.215.dylib
Install conda-forge GDAL and run inside it:
  micromamba create -n qgis -c conda-forge qgis python=3.12
  micromamba run -n qgis gis-tool geo geo.yaml
or pin one explicitly: --gdal-prefix /path/to/env
```

The probe result (prefix, version, each binary's absolute path) is resolved **once per run** and
recorded in the manifest, so a byte difference between two machines is traceable to a toolchain
difference rather than being a mystery.

### 3.5 GDAL invocations — exact argv

Every child process is `subprocess.run(argv, capture_output=True, text=True, env=child_env, timeout=…)`
with `argv[0]` an **absolute path** from the probed prefix (never a bare name — no PATH lookup at call
time). `child_env` = `os.environ` plus:

- `GDAL_PAM_ENABLED=NO` unless `gdal.pam` (V7, V8)
- `OGR_CURRENT_DATE=2020-01-01T00:00:00.000Z` for every `ogr2ogr` writing a GeoPackage (V13)
- `CPL_LOG_ERRORS=ON`, `GDAL_NUM_THREADS=ALL_CPUS` (V5: safe for byte-identity)
- `LC_ALL=C`, `LANG=C` so `gdalinfo` text parsing is locale-independent

Non-zero exit → **exit 6**, echoing the full argv and the child's stderr verbatim.

**A. Source inspection** (before every warp):

```
gdalinfo -json -nomd <source>
```

Yields size, `geoTransform`, `cornerCoordinates`, band count, per-band type and `colorInterpretation`,
and the source CRS. Used for the pre-flight bbox check (§3.7) and for `predictor: auto`.

**B. Raster clip** — the authoritative georeferenced output, `<out_dir>/<name>.tif`:

```
gdalwarp -overwrite
         -t_srs EPSG:32630
         -te 788392 322125 788702 322395
         -ts 2929 2551
         -r cubic
         -dstalpha
         -of GTiff
         -co TILED=YES -co BLOCKXSIZE=256 -co BLOCKYSIZE=256
         -co COMPRESS=DEFLATE -co ZLEVEL=6 -co PREDICTOR=2
         -co BIGTIFF=IF_SAFER
         -q
         <source> <out_dir>/<name>.tif
```

`-te` + **`-ts`** — never `-tr` — is the whole point (V1 vs V2): the **extent is sacred, resolution is
derived**. `-te` values are formatted with `repr`-grade precision (`f"{v!r}"` on a float) so no
rounding is introduced at the argv boundary. Written to `<name>.tif.tmp` and `os.replace`'d on success,
so an interrupted run never leaves a half-written clip behind (§2.2's blank `site_ortho.tif`).

**C. Sheet-linkable sidecar** (only when `link:` is present), `<out_dir>/<name>.png`:

```
gdal_translate -of PNG -co WORLDFILE=YES -co ZLEVEL=9 -q \
               <out_dir>/<name>.tif <out_dir>/<name>.png
```

Derived from the clip, **never re-warped from the source** — so the PNG and the TIFF cannot disagree,
and `<name>.wld` carries the affine that `gdal_translate -of PNG` otherwise destroys (§2.2 defect 2;
V6). JPEG variant: `-of JPEG -co QUALITY=<q> -co WORLDFILE=YES`.

**D. Post-write verification** (§3.7):

```
gdalinfo -json -stats -nomd <out_dir>/<name>.tif
```

**E. Vector to GeoJSON**, `<out_dir>/<name>.geojson`:

```
ogr2ogr -overwrite -f GeoJSON -t_srs EPSG:32630 \
        -lco COORDINATE_PRECISION=3 -lco RFC7946=NO \
        <out_dir>/<name>.geojson <source> [<layer>]
```

**F. Vector into a GeoPackage** (after the lock guard, §3.9):

```
ogr2ogr -update -overwrite -f GPKG -t_srs EPSG:32630 -nln <layer> \
        <gpkg> <source> [<src-layer>]
```

### 3.6 The parameter manifest (interface with P3 #55)

P8 **writes** a manifest; it does not build P3's generalised manifest machinery. What P8 needs from
P3, stated so P3 can absorb it: a `to_manifest_dict()`-style contract, a canonical JSON serialiser
(sorted keys, `separators=(",", ":")` for digesting / `indent=2` for the on-disk file, LF, trailing
newline, no wall-clock fields), and a shared `sha256_file()` with a `(path, size, mtime_ns)`-keyed
cache — digesting the 484 MB ortho on every run is not acceptable. Until P3 lands, P8 implements
these privately in `gis_tool/geo.py` with exactly those semantics, so P3 can lift them.

**Per target: `<out_dir>/<name>.geo.json`** — committed (small, non-confidential text):

```json
{
  "schema": "tdfa.geo/1",
  "target": "ga_backdrop",
  "kind": "imagery",
  "outputs": [
    {"role": "clip", "path": "ga_backdrop.tif", "sha256": "…", "bytes": 4812345,
     "driver": "GTiff", "size_px": [2929, 2551], "bands": 4,
     "band_color_interp": ["Red", "Green", "Blue", "Alpha"], "data_type": "Byte"},
    {"role": "link", "path": "ga_backdrop.png", "sha256": "…", "bytes": 1203456,
     "driver": "PNG", "size_px": [2929, 2551]},
    {"role": "worldfile", "path": "ga_backdrop.wld", "sha256": "…", "bytes": 89}
  ],
  "sources": [
    {"declared": "${PROJECT_GIS}/Basin/…/20260609_Basin S2_Orthomosaic.tif",
     "sha256": "…", "bytes": 484066966,
     "crs": "EPSG:32630", "size_px": [11000, 11000],
     "geo_transform": [788282.0, 0.05, 0.0, 322564.0, 0.0, -0.05]}
  ],
  "frame": {
    "name": "ga-001", "crs": "EPSG:32630",
    "bbox": [788392.0, 322125.0, 788702.0, 322395.0],
    "pixels": [2929, 2551],
    "resolution_m": [0.10583817, 0.10584084],
    "plot_scale": 1250, "dpi": 300,
    "paper_mm": [248.0, 216.0],
    "derived_from": "plot_scale+dpi"
  },
  "geo_transform": [788392.0, 0.10583817, 0.0, 322395.0, 0.0, -0.10584084],
  "coverage": {"fraction": 0.331262, "method": "alpha_band_mean",
               "min_required": 0.30, "warn_below": 0.999, "warning": true},
  "params": {"resample": "cubic", "nodata": "alpha", "compress": "DEFLATE",
             "zlevel": 6, "predictor": 2, "tiled": true, "block": [256, 256]},
  "argv": [
    ["gdalwarp", "-overwrite", "-t_srs", "EPSG:32630", "-te", "788392.0", "322125.0",
     "788702.0", "322395.0", "-ts", "2929", "2551", "-r", "cubic", "-dstalpha", "…"],
    ["gdal_translate", "-of", "PNG", "-co", "WORLDFILE=YES", "-co", "ZLEVEL=9", "…"]
  ],
  "toolchain": {"gdal_version": "3.10.2", "gdal_prefix": "<prefix>",
                "discovered_by": "sys.prefix"},
  "config": {"path": "geo.yaml", "sha256": "…", "version": 1},
  "link": {"format": "png", "opacity": 1.0, "relative_to_sheet": null}
}
```

Notes that matter:

- `argv[i][0]` is stored as the **basename**, and the absolute prefix separately in `toolchain`, so the
  committed manifest is machine-independent while remaining exactly reconstructible.
- No `generated_at`, no `hostname`, no `user`. The manifest is a function of its inputs only, so it is
  itself byte-reproducible and its git diff is the change log.
- `sha256` of an output makes the reproducibility test (§5 T1) a one-line comparison and makes staleness
  answerable without rebuilding.
- Resolved absolute source paths, the digest cache, and any local-only diagnostics go in
  `<out_dir>/<name>.local.json`, which the implementation **must** add to `.gitignore`.

**Per run: `<out_dir>/geo.manifest.json`** — `{schema, config: {path, sha256}, toolchain,
targets: [{name, kind, manifest: "<name>.geo.json", sha256_of_manifest}], warnings: [...]}`. This is
the node P3's build graph consumes.

### 3.7 The linked-backdrop contract

**Sidecar path.** For a raster named `N` with `link:`, the sidecar is
`<out_dir>/N.<ext>` with its `<out_dir>/N.wld` and `<out_dir>/N.geo.json`. The sheet references it by a
path **relative to the emitted SVG's own directory** (D8), computed with `os.path.relpath`, always
`/`-separated. Verified safe: `rsvg-convert` resolves a relative `href` against the SVG file's
directory, independent of cwd (V16).

**How the sheet references it.** `technical_drawings_for_agents/backdrop.py` — no GDAL, stdlib only:

```python
@dataclass(frozen=True)
class Backdrop:
    """A pre-clipped, georeferenced raster sidecar, read from its geo manifest."""
    manifest_path: Path
    image_path: Path
    bbox: tuple[float, float, float, float]
    crs: str
    size_px: tuple[int, int]
    sha256: str
    coverage: float
    opacity: float

def load_backdrop(manifest_path: str | Path) -> Backdrop: ...

def backdrop_image_element(bd: Backdrop, vb: ViewBox, svg_dir: Path,
                           *, tolerance_m: float = 0.001) -> str: ...
```

`backdrop_image_element` raises `BackdropError` (one module error class, house style) unless **all** of:

1. `bd.image_path` exists and its SHA256 matches the manifest — else the emitted PDF would be silently
   blank (V17) or silently stale.
2. `bd.bbox` equals the ViewBox's model extent (`vb.real_min_x, real_min_y, real_max_x, real_max_y`)
   within `tolerance_m` (default 1 mm ground). Message names both bboxes and the offending edge:
   `backdrop 'ga_backdrop' frame xmax 788701.96 != sheet frame xmax 788702.0 (Δ 0.040 m > 0.001 m);
   re-run 'gis-tool geo geo.yaml'`. **This single assertion is what makes the old 4 cm error
   impossible** — it would have failed loudly on the existing `ga_backdrop.png`.
3. `bd.crs` equals the sheet's declared model CRS.
4. `bd.coverage > 0.0` — a fully-empty backdrop is never emitted silently (V10; §2.2's
   `site_ortho.tif`). Below `warn_coverage` it emits with a warning naming the fraction.

It then emits, with the image rect derived from the ViewBox exactly as `source.py:121-124` does today:

```xml
<image href="../geo/ga_backdrop.png" x="74.0" y="…" width="…" height="…"
       preserveAspectRatio="none" opacity="1.0"/>
```

`preserveAspectRatio="none"` is retained deliberately: because the extent is now *exact* (V2), the rect
aspect and the pixel aspect agree to within the derived-resolution rounding, so `none` is a no-op
rather than the silent stretch it is today. The check in (2) is what earns the right to keep it.

**Guaranteeing the clip matches the sheet frame.** Not by convention — by a cross-check that holds
whichever way the frame is declared. The geo manifest records the resolved frame bbox; the sheet
asserts equality against its own viewport at emit time (check 2 above) and **fails the build** on
mismatch. That is strictly better than sharing a config field, because it also catches a stale sidecar
from a *previous* frame — the exact situation `source.py:64` ("must match the backdrop clip extent —
see geo/README_regen.md") currently entrusts to a comment.

**Target DPI derivation** is in §3.2; the sheet does not choose it, it *validates* it: the manifest
carries `paper_mm` and `dpi`, and `backdrop_image_element` warns when the effective device resolution
implied by the sheet differs from the manifest `dpi` by more than 20% — undersized backdrops print
soft, oversized ones waste tens of MB.

**Interface with P1 #53 (paper space).** P1 owns `Sheet`/`Viewport` and the paper-mm model; P8 owns the
raster. The contract between them is exactly three values, and P8 accepts them from either side of the
transition:

| P8 needs | Pre-P1 (today) | Post-P1 |
|---|---|---|
| model extent | `frames.<n>.bbox` | `frames.<n>.from_sheet` → the sheet JSON's viewport model extent |
| plot scale | `frames.<n>.plot_scale` | same JSON |
| paper size / margins | not needed (paper mm derived from extent ÷ scale) | same JSON, cross-checked |

P8 **must not** define a `Sheet` class, an ISO-216 table, or a scale-string formatter. When P1 lands,
`from_sheet` becomes the recommended form, `bbox` stays supported, and the §3.7 check-2 assertion is
what keeps them honest. If they disagree, the build fails — it does not pick a winner.

### 3.8 Frame from a sheet descriptor

`from_sheet: <path>` reads a JSON file and expects `viewport.model_extent` (`[xmin, ymin, xmax, ymax]`),
`viewport.crs`, `viewport.plot_scale`. Missing key → exit 3 naming the key and the file. `crs` must
equal the top-level `crs` or → exit 1. Until #53 lands, this path is implemented and tested against a
**fixture** JSON of that shape, so P1 has a written target to satisfy.

### 3.9 GeoPackage write guard

`gpkglock.py`, deliberately small and reused (#51 will import it, §6):

```python
class GpkgLockedError(RuntimeError):
    """Raised when a GeoPackage appears to be held open by a live QGIS session."""

def assert_gpkg_writable(gpkg: Path) -> None: ...
def gpkg_sidecars(gpkg: Path) -> list[Path]: ...   # existing -wal / -shm
```

`assert_gpkg_writable` raises if `<gpkg>-wal` **or** `<gpkg>-shm` exists — regardless of size
(`basin-site.gpkg-wal` is 0 bytes right now and the session is nonetheless live). Message:

```
error: refusing to write /…/qgis/basin-site.gpkg — a live QGIS session appears to hold it
  (found basin-site.gpkg-wal, basin-site.gpkg-shm).
  Close the project in QGIS (or save and close it) and re-run. Read-only exports are unaffected.
```

This guard is **ours to build**: OGR does not refuse (V14) — with both sidecars present,
`VectorTranslate(accessMode="overwrite")` succeeded. The hazard is not GDAL corrupting the file; it is
QGIS's in-memory edit buffer being flushed on top of a layer we just replaced, silently discarding one
side. Only `vectors[*].to: gpkg` is gated; GeoJSON and raster targets never touch a GeoPackage and are
always safe. There is **no `--force`** (D9).

### 3.10 CLI surface

```
gis-tool geo <config> [--only NAME]... [--dry-run] [--check]
                         [--gdal-prefix DIR] [--out-dir DIR] [-v|--verbose]
```

| Flag | Behaviour |
|---|---|
| `--only NAME` | Repeatable; process just these targets. Unknown name → exit 1 listing valid names. |
| `--dry-run` | Resolve config, probe GDAL, inspect sources, run the pre-flight bbox check, print every argv it *would* run. Writes nothing. Exit 0 if the plan is valid. |
| `--check` | Verify without regenerating: every declared output exists, every SHA256 matches its manifest, every source digest matches. Exit 0 clean, **exit 7** if stale/missing (prints what changed). This is the CI answer to "is this sheet stale?". |
| `--gdal-prefix DIR` | Highest-precedence GDAL prefix (§3.4). |
| `--out-dir DIR` | Overrides `out_dir` (mirrors `build --out`, `cli.py:30`). |
| `-v` | `logging.INFO` — one line per target with its coverage and output sizes. |

Output on success, in the terse style of `cli.py:53-62`:

```
gdal: 3.10.2 (<prefix>, via sys.prefix)
- ga_backdrop: clip=ga_backdrop.tif 2929x2551 res=0.1058x0.1058 coverage=0.331 link=ga_backdrop.png (1.2 MB)
  WARNING ga_backdrop: coverage 0.331 < warn_coverage 0.999 — 66.9% of the frame has no imagery
- ponds: ponds.geojson features=7 crs=EPSG:32630
manifest: geo.manifest.json
```

**Exit codes.** Existing verbs keep returning **1** for every error (`cli.py:19-21`) — unchanged,
constraint 1. The `geo` verb adds distinct codes so CI can branch:

| Code | Meaning |
|---|---|
| 0 | success (warnings may have been printed) |
| 1 | config invalid (`GeoConfigError`) |
| 2 | no usable GDAL toolchain (§3.4) |
| 3 | a declared source or `from_sheet` file is missing/unreadable |
| 4 | measured coverage below `min_coverage` |
| 5 | refused: GeoPackage held by a live session (§3.9) |
| 6 | a GDAL child process failed (argv + stderr echoed) |
| 7 | `--check` found a stale or missing output |

### 3.11 Documentation

`gis-tool/README.md` gains a `## geo` section: the schema, the discovery chain with the *actual*
Homebrew failure quoted (so the next person does not lose an hour to it), and the exit codes. In the
Basin project, `geo/README_regen.md` is **replaced by** `geo/geo.yaml` plus a two-line header comment
pointing at the command — prose instructions are the defect, so no prose recipe survives.

---

## 4. Behaviour decisions, with rationale

**D1 — Subprocess CLI, not the GDAL Python bindings. Decided: subprocess.**

`stage.py:42-47` uses `from osgeo import gdal`, so bindings are the incumbent and consistency argues for
them. Against that:

- **Bindings and binaries can be different GDALs.** On this machine `osgeo` is only importable inside
  the conda-forge env while `PATH` yields a broken Homebrew 3.12.1. A tool that mixes them can warp with
  one and inspect with the other. Subprocess with an absolute path from a **probed** prefix makes the
  toolchain a single, recorded, verified fact.
- **The manifest must record what actually ran.** `gdal.Warp(**kwargs)` has no faithful textual form;
  an argv list is exactly reproducible by hand — which is the whole point of replacing a prose recipe.
  Someone must be able to paste `argv` into a shell and get the same bytes.
- **The broken install must be *detectable*.** An `ImportError` tells you nothing about whether
  `gdalwarp` works. The version-banner probe (§3.4) catches the `exit 0` + dyld-error case; no
  binding-level check can.
- **Bindings are not needed.** The only non-trivial read is coverage, and `gdalinfo -json -stats`
  gives it to 6 decimal places from the alpha-band mean (V9: `0.111557` vs numpy `0.111556`). So the
  entire verb — including its checks — needs no `osgeo` import at all.
- **Testability.** `geoconfig.py`, `gdaltools.py` (discovery/probe) and `gpkglock.py` import nothing
  from GDAL, so their tests run in the plain `python` CI job. With bindings, all of it would be
  `importorskip("osgeo")` and effectively untested on most PRs.

Rejected alternatives: *bindings for consistency* (loses the toolchain probe and a faithful argv);
*bindings with a subprocess fallback* (two code paths, two byte-output risks, twice the tests, and the
manifest can no longer say which ran). `stage.py` keeps its bindings — rewriting it would violate
constraint 1 for zero user-visible benefit.

**D2 — Byte-identical clips: the exact creation options.**

Rasters are reproducible because GDAL embeds no clock and no version. **[verified]** (V4: no `GDAL`,
`3.10`, `2025` or `2026` substring in the output bytes; metadata is `{AREA_OR_POINT: Area}`.) The
options that make it hold:

- `-of GTiff -co TILED=YES -co BLOCKXSIZE=256 -co BLOCKYSIZE=256` — a fixed tile grid, so the byte
  layout does not depend on a default that could change.
- `-co COMPRESS=DEFLATE -co ZLEVEL=6 -co PREDICTOR=<explicit>` — every compression parameter pinned
  explicitly rather than defaulted. Verified byte-identical across runs (V3).
- `-co BIGTIFF=IF_SAFER` — deterministic given the size; `IF_NEEDED` flips format at a threshold.
- **`COMPRESS=JPEG` is rejected** for the clip: lossy, and libjpeg-turbo vs libjpeg produce different
  bytes for identical input, so byte-identity would silently depend on the build. JPEG is allowed for
  the *link* sidecar only, and the manifest records the format so a mismatch is explainable.
- `GDAL_PAM_ENABLED=NO` — no `.aux.xml` polluting the derived directory, and `gdalinfo -stats` cannot
  leave a stats sidecar that differs between a fresh and a warm run (V7, V8).
- `GDAL_NUM_THREADS=ALL_CPUS` is **safe**: multithreaded and single-threaded warps are byte-identical
  (V5), because each output pixel is computed independently.
- `SOURCE_DATE_EPOCH` is honoured if set, for P3 alignment, but nothing in this pipeline reads a clock.

**Honest scope of the claim:** byte-identity holds for a **fixed toolchain**. DEFLATE output depends on
the zlib build, so a GDAL or zlib upgrade may legitimately change bytes. That is why `toolchain.gdal_version`
is in the manifest, and why the reproducibility test (T1) is "delete the directory and re-run **in the
same environment**", not "matches a committed golden hash". Fixtures that must be toolchain-independent
use `compress: NONE`, removing zlib from the equation entirely.

**GeoPackages are a separate case: not byte-reproducible by default** (V12 — `gpkg_contents.last_change`
is wall-clock, e.g. `2026-07-24T22:21:27.323Z`) but **pinnable**: `OGR_CURRENT_DATE=2020-01-01T00:00:00.000Z`
makes two writes byte-identical **[verified]** (V13). So the env var is set for every GPKG write.
GeoJSON is byte-reproducible with `-lco COORDINATE_PRECISION=<n>` (V15).

**D3 — Extent is sacred; resolution is derived; non-square pixels are accepted.**

V1 vs V2 settles this: `-te` + `-tr` gives you the resolution you asked for and an extent 4 cm off;
`-te` + `-ts` gives you the extent exactly and a resolution derived from it. A backdrop's job is to
register with the sheet frame, so the extent is the invariant that matters and `-ts` is the only correct
choice. The cost is pixels that may be non-square by one part in `nx` (`0.105838` vs `0.105841`,
~3 ppm for `ga-001`). Rejected: (a) *`-tap`* — aligns to a resolution grid by **expanding** the extent,
so it cannot honour an arbitrary sheet frame; (b) *snapping the frame to a round resolution* — moves
the drawing to suit the raster, backwards; (c) *forcing square pixels by adjusting `ny`* — reintroduces
a sub-pixel extent error on one axis, i.e. the original bug at 1/2700 scale. The manifest records
`resolution_m` for both axes, and `.wld` carries the real anisotropic affine, so nothing downstream has
to guess.

**D4 — Derived `geo/` stays gitignored; provenance lives in committed manifests.**

The imagery is confidential site data and `geo/` is already gitignored with that reason written into the
file. Nothing in P8 changes that — the repo rule is absolute (constraint 2). What changes is that
"regenerable" stops being an aspiration: today the only recipe is prose in `README_regen.md`, so a lost
`geo/` is a lost artifact reconstructed by hand. After P8, three things are **committed** and small:
`geo.yaml` (the declaration), `<name>.geo.json` per target (argv, source digests, output digests,
resolved frame, coverage, toolchain), and `geo.manifest.json` (the run). That is strictly more
provenance than committing the bytes would give — bytes tell you *what*, the manifest tells you *what
from what, how, with which tool, and how complete*. `--check` turns it into an assertion: outputs match
their manifests and sources match their digests, or exit 7. `*.local.json` (resolved absolute paths,
digest cache) is gitignored so no user home path or OneDrive tree structure is committed.
Rejected: *committing the derived PNG because it is "only 3.9 MB"* — it is confidential imagery, and
size is not the criterion; *git-lfs* — same confidentiality problem, plus a new dependency.

**D5 — Resample defaults by `kind`.**

| `kind` | default `resample` | default `nodata` | why |
|---|---|---|---|
| `imagery` | `cubic` | `alpha` | A 4.7 cm ortho downsampled to ~10 cm is a >2× reduction; `bilinear` samples only 2×2 and aliases visibly on hard edges (roof lines, road markings). `cubic` (4×4) is the standard choice for photographic downsampling. The existing recipe uses `bilinear` — this is a deliberate, opt-out-able improvement, and because it changes bytes it is exactly why `resample` is explicit in `geo.yaml` and recorded in the manifest. |
| `dtm` | `bilinear` | `value:-9999` | A DTM is a continuous surface, so nearest would produce stair-stepped contours. `cubic` can **overshoot** near a break-line and invent elevations that never existed — unacceptable in a terrain model feeding volumes. `bilinear` never exceeds the local min/max. Nodata must be a value, not alpha: `geo/site_dtm.tif` is Float32 with `NoData=-9999` **[verified]**, and an alpha band on a Float32 elevation raster is meaningless. GDAL excludes declared nodata from the interpolation weights, so bilinear does not smear `-9999` across the valid edge — which is precisely why declaring it is mandatory here. |
| `hillshade` | `cubic` | `value:0` | Derived visual product; treat as imagery, but 0 is the conventional shaded-relief nodata. |
| `mask` | `near` | `none` | A category/boolean raster must not be interpolated: averaging class codes 1 and 3 into 2 invents a class. Any resample other than `near`/`mode` for a mask is a defect. |

For `float` types with `resample` in `{cubic, cubicspline, lanczos}`, the loader emits a warning naming
the overshoot risk. It does not refuse — a hillshade legitimately wants cubic.

**D6 — Clip extent partly outside the source: warn + record by default, fail only on a declared floor.**

The real Basin case forces this. A hard failure would be principled but **wrong**: the frame is
entirely inside the ortho's bbox (`788282..788832 / 322014..322564`) yet only **33.1%** of it has
imagery, and all four corners are nodata **[verified]** — the drone flight simply did not cover the
compound. That sheet is legitimate and must keep building. A pure warning is also wrong, because
`gdalwarp` already exits 0 on a **100% empty** output (V10) and that is how a 30 MB blank
`geo/site_ortho.tif` came to exist.

So: **measure, always; record, always; fail against a declared threshold.**

1. **Pre-flight** — intersect the source's bbox (transformed to the target CRS) with the frame. Zero
   intersection → **exit 4** *before warping*, with both bboxes printed. This is the cheap, unambiguous,
   certainly-a-mistake case, caught in milliseconds instead of after a multi-minute warp.
2. **Post-warp** — measure coverage from `gdalinfo -json -stats` (alpha-band `mean / 255`, or the
   valid-pixel fraction for a nodata-value raster). Below `min_coverage` → **exit 4**, output deleted
   (never leave a rejected artifact on disk). Below `warn_coverage` → stderr warning + `coverage.warning:
   true` in the manifest. Always written to the manifest, pass or fail.
3. Coverage `0.0` is **always** an error regardless of `min_coverage`, including if someone sets
   `min_coverage: 0.0`. A wholly empty raster is never a legitimate output.

Defaults: `min_coverage: 0.0` (nothing existing starts failing — constraint 1) and
`warn_coverage: 0.999` (any nodata at all is surfaced). Basin sets `min_coverage: 0.30`, which both
documents the known gap and catches a future re-flight or bad path that drops coverage further.
Rejected: *always fail on any nodata* (breaks the real sheet); *warn only* (V10 shows warnings get
ignored — the blank TIFF is the evidence); *auto-shrink the frame to the data* (silently changes the
drawing's extent — a far worse sin than a gap in a backdrop).

**D7 — Two artifacts per raster: an authoritative GeoTIFF and a derived link sidecar.**

The georeferenced truth must be a format that can hold a CRS and an affine; PNG cannot, and
`gdal_translate -of PNG` silently drops both (`ga_backdrop.png` reports `Upper Left (0.0, 0.0)`
**[verified]**). But SVG and `rsvg-convert` cannot render TIFF. So: warp once to GeoTIFF (the record),
then `gdal_translate` **from that TIFF** to PNG (the link) plus a `.wld`. Deriving the PNG from the
clip rather than re-warping from the source makes it impossible for the two to disagree, and one extra
`gdal_translate` on an already-small clip is cheap. Rejected: *PNG only* (loses georeferencing — the
current bug); *TIFF only* (breaks the PDF path); *two independent warps* (two chances to diverge).

**D8 — The sheet links a relative path.**

Relative, computed from the emitted SVG's directory. Three reasons: `rsvg-convert` resolves it against
the SVG's own directory and is **cwd-independent** **[verified]** (V16), so the sheet is portable; an
absolute path would bake a home-directory path into a committed SVG, repeating the
`qgisproj.yaml:47` mistake in a *shared* artifact; and a relative link survives moving the drawing
directory, which is how these projects actually get reorganised. Rejected: *absolute* (non-portable,
leaks the home path); *`file://` URI* (same, plus escaping); *a copy of the PNG next to the SVG*
(duplicates confidential imagery into `out/`, which is not gitignored for PNGs).

**The cost of linking, and the mitigation.** Linking introduces a failure mode base64 did not have:
`rsvg-convert` with a **missing** `href` exits **0** and writes a 1,085-byte PDF with a blank backdrop
**[verified]** (V17). Trading a 5.7 MB SVG for a silently-blank PDF would be a bad trade. Hence the
mandatory §3.7 checks (existence **and** digest, before emit) and acceptance test T7 (a PDF whose byte
size proves the raster is actually embedded). Loud failure over silent no-op, applied to the change this
spec itself introduces.

**D9 — No `--force` on the GeoPackage lock.**

Every `--force` gets typed reflexively. The mitigation is genuinely cheap (close the project in QGIS),
the loss is a human's unsaved layout edits, and `--dry-run`/GeoJSON targets already cover every
legitimate "I just want to see what it would do". If a real unattended need appears, it gets its own
issue and its own justification.

**D10 — Unknown keys are errors, not warnings.**

`resamlpe: near` silently taking `cubic` is precisely the class of defect P8 exists to remove. `geo.yaml`
is new, so strictness costs no existing user anything. Existing `stage`/`build` configs keep their
current tolerance (constraint 1) — `config.py` does not reject unknown keys today and P8 does not change
that.

**D11 — Idempotent by default; no content-based skipping in v1.**

Every run re-warps. `--check` answers "is it stale?" without writing. A `--if-changed` fast path is
tempting but the correctness bar is high (source digest **and** config digest **and** toolchain version
**and** every output digest must match) and getting it wrong means silently serving a stale backdrop.
Regenerating a ~3 MB clip takes seconds. Deferred to a follow-up issue with `--check` as its foundation.

---

## 5. Acceptance tests

House style: `gis-tool/tests/test_geo_*.py` and `technical_drawings_for_agents/tests/test_backdrop.py`, one behaviour
per test, named for the behaviour asserted (`config.py` / `test_config.py` precedent).

**CI job assignment** (`.github/workflows/build.yml`):

- **`python` job** (no GDAL): T2, T5, T6, T8, T10, T11, T12 — config validation, GDAL discovery/probe
  (with fake prefixes on disk), the lock guard, and the `technical_drawings_for_agents` backdrop reader. These must be
  the majority, so most PRs actually test this code.
- **`qgis` job** (conda-forge GDAL, already provisioned, runs `pytest -q -rs gis-tool`): T1, T3, T4,
  T7, T9 — anything that invokes a GDAL binary. Guarded with
  `pytest.importorskip`-equivalent for the toolchain: a module-level
  `pytest.mark.skipif(find_gdal() is None, reason=…)`, so `-rs` makes the skip visible rather than
  silently green — matching the `pytest.importorskip("qgis")` pattern in `tests/test_build.py:4`.

**Synthetic fixtures — no confidential data, ever** (constraint 2). `gis-tool/tests/geofixtures.py`
builds every raster in `tmp_path` with `gdal_translate`/`gdalwarp` from a generated pattern (a
deterministic 20 px checkerboard) — no numpy dependency, no committed binaries:

- `synth_ortho.tif` — 4-band Byte, 0.05 m, EPSG:32630, extent `788312..788762 E / 322085..322435 N`,
  with a **diagonal alpha edge** so coverage is `< 1.0` (mimics a flight boundary). Measured coverage
  over the test frame: **0.994606** **[verified]**.
- `synth_dtm.tif` — 1-band Float32, 1 m, `NoData=-9999`, same footprint.
- `synth_far.tif` — same construction, extent shifted to `789412..789712 / 323095..323395` (no overlap
  with the test frame).
- **Test frame `ga-test`**: `bbox: [788392, 322125, 788702, 322395]`, `pixels: [3100, 2700]` → exactly
  **0.1 m** pixels, so every coordinate assertion is round-number arithmetic. **[verified]**: the written
  clip's geotransform is exactly `(788392.0, 0.1, 0.0, 322395.0, 0.0, -0.1)`.

These are the *real* Basin frame coordinates (public survey grid values, not imagery), so the tests
exercise the actual geometry with none of the confidential pixels.

---

**T1 — `test_deleting_derived_dir_and_rerunning_reproduces_byte_identical_clips`** *(qgis job)*
**Setup:** `geo.yaml` with `crs: EPSG:32630`, frame `ga-test`, one raster `{name: bd, source:
synth_ortho.tif, kind: imagery, resample: cubic, link: {format: png}}`, one vector to GeoJSON.
**Action:** run `geo`; record SHA256 of `bd.tif`, `bd.png`, `bd.wld`, `ponds.geojson`, `bd.geo.json`,
`geo.manifest.json`. `shutil.rmtree(out_dir)`. Run `geo` again; recompute.
**Expected:** exit 0 both times; **all six digests identical**; no `.aux.xml` anywhere under `out_dir`
(`GDAL_PAM_ENABLED=NO`); `bd.geo.json` contains no `generated_at`/`hostname`/`user` key and no
absolute path outside `toolchain`. *(Basis: V3, V6, V7, V15 — GTiff, PNG and GeoJSON all verified
byte-stable on this toolchain.)*

**T2 — `test_unknown_key_and_bad_field_values_fail_at_load_with_positional_messages`** *(python job)*
**Setup:** table-driven bad configs: `resamlpe: near`; `resample: sinc`; `bbox: [3, 4]`;
`bbox: [788702, 322125, 788392, 322395]`; `crs: 32630`; `version: 2`; `frames: {}`; a raster whose
`frame` names no frame; duplicate `name` between a raster and a vector; `plot_scale` **and** `pixels`
together; `link.quality` with `format: png`; `nx*ny` over `max_pixels`; `${NOT_SET_VAR}` in a source.
**Action:** `load_geo_config` on each.
**Expected:** every case raises `GeoConfigError`; each message contains the **positional path**
(`rasters[0].resample`, `frames.ga-test.bbox`) and, for enums, the accepted values. No case raises
`KeyError`, `TypeError`, or a bare `ValueError`. Asserted with `pytest.raises(..., match=…)`.

**T3 — `test_clip_extent_matches_frame_exactly_at_surveyed_control_point_P5`** *(qgis job)*
The anti-offset test. Control point **P5** — `E 788609.589, N 322341.151`, EPSG:32630 — surveyed
(Geomars DCP), cited at `site-plan.yaml:60` to `STB-STA-STC-FOUND-001 §2.2`.
**Setup:** frame `ga-test` (`788392/322125/788702/322395`, `pixels: [3100, 2700]`).
**Action:** run `geo`; read the written `bd.tif`'s geotransform and size via `gdalinfo -json`.
**Expected, all exact:**
- `geoTransform == [788392.0, 0.1, 0.0, 322395.0, 0.0, -0.1]` (each term within `1e-9`)
- `size == [3100, 2700]`
- `gt[0] + gt[1]*nx == 788702.0` and `gt[3] + gt[5]*ny == 322125.0` (within `1e-6` m)
- P5 → `col = (788609.589 - gt[0]) / gt[1] == 2175.89` and
  `row = (322341.151 - gt[3]) / gt[5] == 538.49` (within `1e-6`), i.e. pixel index **(2175, 538)**
- the manifest's `frame.bbox` equals the declared bbox **exactly**
**[verified]**: all six values reproduced on the synthetic fixture before this spec was written.
**Regression guard (same test):** repeat the *old* recipe — `-te 788392 322125 788702 322395 -tr 0.12 0.12`
— and assert its `xmax == 788701.96 != 788702.0`, i.e. the historical defect is real and the `-ts`
contract is what fixes it. A pixel/coordinate assertion, never a rendered comparison.

**T4 — `test_coverage_below_min_fails_and_partial_coverage_is_recorded`** *(qgis job)*
**Setup:** three cases against frame `ga-test`: (a) `synth_ortho.tif` with `min_coverage: 0.30`;
(b) the same with `min_coverage: 0.999`; (c) `synth_far.tif` (disjoint) with `min_coverage: 0.0`.
**Expected:** (a) exit 0; `bd.geo.json` `coverage.fraction == 0.994606 ± 1e-4`, `coverage.method ==
"alpha_band_mean"`, `coverage.warning is True` (below the 0.999 default); (b) **exit 4**; stderr names
the measured fraction and the threshold; **`bd.tif` does not exist** (rejected output deleted);
(c) **exit 4** from the **pre-flight** bbox check — asserted by the absence of any `gdalwarp` in the
recorded argv, proving no multi-minute warp was attempted; message prints both bboxes.
*(Basis: V10, V11 — GDAL exits 0 on both, so this is entirely our check. V9 — coverage from
`gdalinfo -json -stats`.)*

**T5 — `test_gpkg_write_refused_while_wal_or_shm_present`** *(python job — no GDAL needed)*
**Setup:** `tmp_path/site.gpkg` (an empty file is enough — the guard is filesystem-level). Parametrised:
only `-wal`, only `-shm`, both, and a **zero-byte** `-wal` (the live Basin case).
**Action:** `assert_gpkg_writable(gpkg)`; and end-to-end, `geo` with a `to: gpkg` vector target.
**Expected:** all four raise `GpkgLockedError`; the message names the gpkg **and every sidecar found**;
CLI **exit 5**; the gpkg's mtime and bytes are unchanged. With no sidecars: no raise, and a
`to: geojson` target in the same config still runs (read-only/GeoJSON paths are never gated).
*(Basis: V14 — OGR itself does not refuse, so without this guard the write goes through.)*

**T6 — `test_gdal_discovery_prefers_sys_prefix_and_rejects_a_binary_with_no_version_banner`** *(python job)*
**Setup:** two fake prefixes in `tmp_path`: `good/bin/gdalwarp` (+ `gdalinfo`, `gdal_translate`,
`ogr2ogr`) printing `GDAL 3.10.2, released 2025/02/11` to stdout and exiting 0; `broken/bin/*` printing
a dyld-style error to **stderr**, nothing to stdout, and **exiting 0** — the real Homebrew behaviour.
Monkeypatch the chain sources.
**Expected:**
- `good` on PATH only, `broken` absent → discovered, `discovered_by == "PATH"`.
- `broken` at `sys.prefix` and `good` on PATH → **`good` chosen**; the rejection reason for `broken`
  mentions the missing version banner.
- `broken` only → **exit 2**; the message lists every candidate with its reason and includes the
  `micromamba create -n qgis -c conda-forge qgis` remedy.
- a fake reporting `GDAL 3.2.1` with `min_version: "3.6"` → exit 2 naming both versions.
- `--gdal-prefix` pointing at a directory with no `bin/gdalwarp` → exit 2 echoing the path.
This is the test that encodes §2.3: **exit 0 is not proof a GDAL binary works.**

**T7 — `test_emitted_sheet_links_a_sidecar_and_svg_shrinks_by_orders_of_magnitude`** *(qgis job)*
**Setup:** a minimal generator producing the same sheet twice from the same synthetic clip — once
base64-inlined (today's `source.py:119-124` pattern), once via `backdrop_image_element`.
**Expected:**
- linked SVG contains exactly one `<image ` whose `href` is a **relative** path ending `bd.png`, and
  contains **no** `data:image` and no `base64` substring;
- `len(linked_svg) < len(inlined_svg) / 100` — the real ratio is 5.72 MB → a few tens of kB, so
  100× is a conservative floor (**[verified]** sizes: SVG 5,721,744 B; PNG 3,933,477 B → base64
  5,244,636 B);
- `rsvg-convert -f pdf` on the linked SVG (skipped if `rsvg-convert` is absent) exits 0 and the PDF is
  **> 20 kB** — proof the raster was actually embedded, because a missing `href` yields a
  **1,085-byte** PDF at **exit 0** **[verified]** (V17). Without this size floor the test would pass on
  a blank sheet.
- with the sidecar **deleted** before emit: `BackdropError` is raised at emit time, and **no** SVG is
  written. The failure happens in our code, before `rsvg-convert` gets a chance to succeed silently.

**T8 — `test_backdrop_frame_mismatch_is_a_loud_error`** *(python job — stdlib only)*
**Setup:** a hand-written `bd.geo.json` fixture plus a small PNG whose digest matches, with
`frame.bbox = [788392, 322125, 788701.96, 322395]` — **the historical 4 cm error**.
**Action:** `backdrop_image_element(load_backdrop(...), ViewBox(788392, 788702, 322125, 322395, …), svg_dir)`.
**Expected:** `BackdropError`; message names the edge (`xmax`), both values, the delta `0.040 m`, the
tolerance, and the remedy (`re-run 'gis-tool geo geo.yaml'`). Parametrised also for: a CRS mismatch;
a digest mismatch (stale sidecar); `coverage.fraction == 0.0`; and a missing image file. With the bbox
corrected to `788702.0`, the same call returns an `<image …>` string. **This test is the one that would
have caught the live Basin defect.**

**T9 — `test_dtm_uses_bilinear_and_preserves_nodata_without_smearing`** *(qgis job)*
**Setup:** `synth_dtm.tif` (Float32, `NoData=-9999`) with a rectangular nodata block; a raster target
`{kind: dtm}`.
**Expected:** the recorded argv contains `-r bilinear`, `-dstnodata -9999`, and
`-co PREDICTOR=3` (float); the output's declared NoData is `-9999`; and **no** output pixel lies
strictly between the valid data's min and `-9999` — i.e. no interpolated value between real elevations
and the nodata sentinel. Asserted from `gdalinfo -json -stats` band minimum, so no bindings are needed.

**T10 — `test_manifest_records_argv_source_digests_and_resolved_frame`** *(python job, with a stubbed runner)*
**Setup:** inject a fake GDAL runner that records argv and writes tiny placeholder outputs, so this runs
without GDAL.
**Expected:** `bd.geo.json` validates against the §3.6 shape; `argv[0][0] == "gdalwarp"` (**basename**,
not an absolute path); the absolute prefix appears only under `toolchain.gdal_prefix`;
`sources[0].declared` is the **unexpanded** `${PROJECT_GIS}/…` string and contains no home-directory path;
`sources[0].sha256` matches an independent digest of the fixture; `frame.pixels == [3100, 2700]`;
`frame.resolution_m == [0.1, 0.1]`; `frame.derived_from == "pixels"`. Changing one source byte changes
`sources[0].sha256` **and** the run manifest digest. Serialising the manifest twice yields identical
bytes.

**T11 — `test_existing_stage_and_build_behaviour_is_unchanged`** *(python + qgis jobs — the parity test)*
**Setup:** the existing `tests/test_config.py` and `tests/test_build.py` fixtures, untouched.
**Expected:** `load_config` accepts every existing config and produces identical `GisConfig` values;
`gis-tool --help` still lists `build`, `stage`, `validate` with unchanged help text and now also
`geo`; a deliberately invalid legacy config still exits **1** (not 2–7); `stage` on a fixture produces
a byte-identical `<gpkg>.manifest.json` to the pre-change tool. Constraint 1, mechanised.

**T12 — `test_check_flag_detects_stale_and_missing_outputs`** *(python job, stubbed runner)*
**Setup:** run `geo`; then, in separate cases, (a) truncate `bd.png`, (b) delete `bd.tif`, (c) append a
byte to the source fixture, (d) edit `geo.yaml`.
**Expected:** `--check` exits **0** immediately after a clean run; **exit 7** in all four cases, naming
which artifact or input changed and quoting both digests. `--check` writes nothing in any case (asserted
by comparing every mtime under `out_dir`).

---

## 6. Out of scope

An implementer of #60 must **not** do any of the following. Each gets its own issue.

1. **Do not rewrite `.qgs` authoring.** `build.py:48-59, 62-126` stays PyQGIS; `README.md:15` ("`.qgs`
   files are authored only through PyQGIS, not hand-written XML") is a standing decision. CLI-first
   governs the **pipeline**, not the QGIS project file. Do not hand-write QGIS XML, and do not attempt
   to replace `init_qgis()`.
2. **Do not rewrite `stage.py`.** It uses the `osgeo` bindings (`stage.py:42-47`) and keeps them.
   D1 chooses subprocess for **new** raster/vector work only. Converting `stage` would risk
   constraint 1 for no benefit; if it is ever wanted, that is a separate parity-tested issue.
3. **The #51 boundary.** #51 owns the *placements* round-trip: `gis-tool placements export`,
   `placements seed <register.yaml>`, `stage` support for loading a generated layout GeoJSON into
   `layout_poly`/`layout_line`/`layout_pt` by geometry type, and making `stage` stop wiping the editable
   layer.
   **P8 owns** the general `geo` verb, the `geo.yaml` schema, GDAL discovery/probing, the raster
   pipeline, the manifest, and the **shared lock primitive** `gpkglock.assert_gpkg_writable`.
   Both issues state the `-wal`/`-shm` safety rule; **P8 builds it once** and #51 **imports** it — #51
   must not write a second lock check. P8 does **not** add `placements` verbs, does not touch the
   editable-layer semantics, and does not implement the register↔GeoJSON round-trip. If #51 lands
   first with its own guard, P8's job is to *consolidate* to one implementation, not add a third.
4. **Do not implement P1's paper-space model** (#53): no `Sheet`, no ISO-216 table, no scale-string
   derivation, no scale bar. P8 consumes a sheet descriptor via `from_sheet` (§3.8) and validates
   against it (§3.7).
5. **Do not build P3's generalised manifest/provenance system** (#55): no `SOURCE_DATE_EPOCH`-wide
   emit refactor, no provenance stamp in the title block, no repo-wide build graph. P8 writes its own
   manifest to the §3.6 shape and states what it needs from P3.
6. **Do not modify the vault project files.** `basin-site/source.py`, `site-plan.yaml`,
   `qgis/basin-site.qgisproj.yaml` and `geo/README_regen.md` live in a different repository. The tool
   change ships first; migrating Basin onto it (§7) is a separate, reviewed step in that repo.
7. **Do not add a content-based skip / incremental mode** (D11), a `--force` for the lock guard (D9),
   or `COMPRESS=JPEG` for the authoritative clip (D2).
8. **Do not add COG, overviews/pyramids, mosaicking of multiple sources into one target, tiling a large
   raster across sheets, or contour/hillshade *derivation*.** All plausible, all separate issues. P8
   clips and reprojects what is declared; one source per raster target.
9. **Do not touch the ISSUED gate.** No status field, no title-block write, no `--issue`-shaped flag,
   nothing that could ever flip a drawing to ISSUED FOR CONSTRUCTION.
10. **Do not commit any real raster, orthomosaic, DTM, point cloud, or `.gpkg` containing site data**,
    not even "a small crop for the tests". Synthetic fixtures only (§5).
11. **Do not attempt to repair the Homebrew GDAL install** from code — no symlink creation, no
    `brew` invocation, no `DYLD_*` manipulation. Detect, report, and let a human fix it (§3.4).

---

## 7. Risks and migration

**R1 — Existing consumers of `gis-tool`.** The only public surfaces are the three verbs
(`cli.py:24-43`) and `load_config`. P8 adds a verb and a new module; it must not touch `config.py`'s
existing parse functions except to *reuse* `_normalise_crs` (`config.py:290-294`) and
`split_ogr_layer_spec` (`config.py:120-127`) — reuse means **call**, not modify. If either needs a
behaviour change, extract a new helper instead. T11 is the mechanised guard.
*Migration:* none required; `geo` is opt-in and nothing calls it until a `geo.yaml` exists.

**R2 — `resample: cubic` for imagery changes bytes vs the current `bilinear` recipe.** Deliberate (D5),
and the first `geo` run for Basin will therefore produce a different backdrop from
`geo/ga_backdrop.png`. Since `geo/` is gitignored, no committed artifact changes — but the sheet's
appearance shifts subtly.
*Migration:* the first Basin `geo.yaml` may pin `resample: bilinear` to reproduce today's backdrop
exactly, then switch to `cubic` as a separate, visible change with a before/after PDF.

**R3 — Byte-identity is toolchain-scoped.** A GDAL or zlib upgrade can legitimately change DEFLATE
output. A test asserting a committed golden hash would then fail mysteriously.
*Mitigation:* T1 is delete-and-re-run **within one environment**, never a golden hash;
`toolchain.gdal_version` is in every manifest, so a diff is explainable in one look; fixtures needing
cross-toolchain stability use `compress: NONE`.

**R4 — The `qgis` CI job is the only place GDAL exists.** If conda-forge solving breaks or slows, the
GDAL-touching tests stop running. Mitigated by design: §3.1 puts config validation, GDAL discovery, the
lock guard and the whole `technical_drawings_for_agents` backdrop reader in modules that import no GDAL, so the majority
of tests (T2, T5, T6, T8, T10, T11, T12) run in the fast `python` job. A conda outage degrades coverage,
it does not blind CI.

**R5 — Linking replaces a loud failure with a silent one, if the checks are skipped.** Today a missing
backdrop crashes `source.py` at `BACKDROP.read_bytes()` (`source.py:120`). After linking, a missing
sidecar yields a **1,085-byte PDF at exit 0** **[verified]** (V17). The §3.7 existence + digest checks
and T7's PDF-size floor are therefore **not optional polish** — they are the price of admission for
this change. An implementation that links without them is a **regression** and must be rejected at
review, however small the SVG gets.

**R6 — `${VAR}` expansion is a new required environment dependency.** `PROJECT_GIS` unset means the tool
refuses to run.
*Mitigation:* the error names the variable, the target and the config line. `bbox`-style literal
absolute paths remain valid in `geo.yaml`, so `${VAR}` is a convention, not a hard requirement; the
documented setup adds one `export` line, and `--dry-run` surfaces the problem without doing work.

**R7 — Migrating Basin is a real, staged piece of work** (separate from this change, §6.6):
(1) write `geo/geo.yaml` reproducing today's outputs, with `min_coverage: 0.30` recording the known
33% coverage; (2) run `gis-tool geo` and diff the new clip's geotransform against
`ga_backdrop.png`'s — expect the **4 cm** correction and record it as an intentional change;
(3) switch `source.py:119-124` to `backdrop_image_element`, delete the `base64` import and the
`BACKDROP` constant, and drop the `CAPABILITY GAP` note at `source.py:29-32`; (4) confirm the SVG
drops from 5.7 MB to tens of kB **and** the PDF stays >5 MB (raster still embedded — not a blank sheet);
(5) delete `geo/README_regen.md` and the blank `geo/site_ortho.tif`; (6) add `geo/*.local.json` to
`.gitignore` while keeping `geo/` ignored for imagery — the manifests must be committed, so the
existing blanket `geo/` rule needs a `!geo/*.geo.json`, `!geo/geo.manifest.json`, `!geo/geo.yaml`
negation set. **Do not blanket-unignore `geo/`.**
The **ISSUED gate is untouched throughout**; the sheet stays `CONCEPT` and nothing in this pipeline can
change that.
