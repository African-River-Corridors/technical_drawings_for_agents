# P8 — FINAL specification and implementation plan

**Status:** authoritative. Supersedes `P8-gdal-geo-pipeline.md` where it says so below.

**Provenance.** Base spec by an Opus agent (PR #73). Implemented independently by variant **A**
(upstream #86, 4052+/0−, 17 files) and variant **B**
(upstream #88, 3920+/0−).

Both failed the **qgis** CI job. A: `2 failed, 40 passed, 1 skipped`. B: `2 failed, 22 passed`.
**Both failed the same test** — the third pair in this programme to do so, and again it is a spec defect.

---

## 1. Spec defect — a CRS transform is required, but no tool that can do one is sanctioned

Both variants failed `test_coverage_below_min_fails_and_partial_coverage_is_recorded` with
`AssertionError: Regex pattern did not match` — the expected `GeoCoverageError` naming
`does not intersect frame bbox` never fired. B failed the same way at two call sites in
`test_geo_integration.py`.

Variant A diagnosed it in its own report:

> Pre-flight bbox intersection is strict for same-CRS sources. The spec requires transforming source bbox to
> target CRS, but only lists GDAL tools that do not include `gdaltransform`.

The base spec sanctions `gdalinfo`, `gdalwarp`, `gdal_translate` and `ogr2ogr`. **None of them transforms a
coordinate pair.** So the specified pre-flight check is unimplementable with the specified toolset, and both
implementations degraded it to a same-CRS-only comparison that cannot fail for a differing-CRS source.

### CORRECTION 1 (binding) — sanction `gdaltransform`

Add **`gdaltransform`** to the allowed GDAL binaries. It ships with GDAL, so this adds **no dependency**, and
it keeps the CLI-first rule intact (no `pyproj`, no `osgeo` Python bindings). Use it to transform the source
bbox corners into the target CRS before the intersection test. Locate it by the same discovery rule as the
other binaries — leading with `Path(sys.prefix)`, which lands on the conda-forge GDAL because `gis-tool`
is pip-installed into that env.

### CORRECTION 2 (binding) — coverage is the gate; bbox intersection is only a fast fail

**A passing bbox intersection must never be read as "we have imagery."** The real Basin case proves it, and
the base spec already measured the number: the sheet frame lies **inside** the ortho's bounding box, yet
**only 33.1% of the frame has data** and all four corners are nodata, because the frame runs across the drone
flight edge. Bounding boxes cannot see holes.

Therefore:

- Pre-flight bbox intersection is a **cheap early failure** for the grossly-wrong case (a source nowhere near
  the frame). Its message must say `does not intersect frame bbox`.
- **Post-warp coverage measurement is the authoritative check**, via `gdalinfo -json -stats` on the alpha
  band (the base spec verified alpha mean/255 = 0.111557 against numpy's 0.111556, so no Python bindings are
  needed).
- The two checks are **not interchangeable** and the spec must say so in one sentence, because conflating
  them is exactly how a 30 MB all-white raster passed unnoticed for eight days (see #75).
- Retain B's demonstrated `warn_coverage` behaviour: coverage below `warn_coverage` but above
  `min_coverage` emits a `UserWarning` naming the backdrop and both numbers (B's CI output shows
  `backdrop 'bd' coverage 0.994518 < warn_coverage 0.999000` — correct).

---

## 2. Variant A is the base, with one regression to fix

**A is the base.** 40 passing tests against B's 22, a richer implementation, and its test split is correct:
GIS behaviour under `gis-tool/tests` (which the `qgis` CI job runs) and backdrop reading under
`technical_drawings_for_agents/tests` (which the `python` job runs). B put more in one place and covered less.

**A's second failure is a real backward-compat regression:** `test_stage_manifest_shape_remains_legacy`. A
changed the shape of the existing `gis-tool stage` manifest. As with variant B on P3, credit for writing
the test that caught it — the regression still stands, and compatibility is sacred.

### CORRECTION 3 (binding) — freeze the legacy `stage` manifest shape

- The existing `<gpkg>.manifest.json` shape emitted by `stage` is **frozen**. New provenance fields go in a
  **separate** `geo` manifest, not by extending or reshaping the staging one.
- Commit the current legacy shape as a **golden fixture** and assert against it, so the shape cannot drift
  again unnoticed.
- Rationale: `stage`'s manifest is already consumed by the live Basin project
  (`qgis/basin-site.gpkg.manifest.json`). Reshaping it silently invalidates a committed artifact.

---

## 3. Carried forward unchanged — the findings that shaped the design

The base spec measured each of these; none may be softened:

- **`README_regen.md`'s recipe was geometrically wrong.** `-te … -tr 0.12 0.12` yields
  `xmax = 788701.960` — **4 cm short** of the declared frame — because the integer pixel count wins and the
  extent follows it. `-te` with **`-ts`** honours the extent exactly. Hence the central rule: **extent is
  sacred, resolution is derived.** *(Already fixed in the live project; the tool must now enforce it.)*
- **`gdal_translate -of PNG` silently drops CRS and affine** — the shipped `ga_backdrop.png` reported
  `Upper Left (0.0, 0.0)`. A PNG backdrop must be written with a **world file**.
- **`gdalwarp` exits 0 when the target extent lies entirely outside the source**, which is how a 30 MB
  all-white, fully-transparent raster came to sit in `geo/` unnoticed. Never trust the exit code; measure.
- **Homebrew GDAL is broken on this machine and fails with exit 0** — the dyld error goes to stderr while the
  shell reports success. The probe must require a **parseable version banner**, not a zero exit.
- **`rsvg-convert` exits 0 with a ~1 kB blank PDF when a linked `href` is missing.** Linking trades a 5.7 MB
  SVG for a *worse* silent failure unless guarded — so the sidecar existence + digest checks and a PDF-size
  floor are mandatory, not polish.
- **OGR does not refuse a GeoPackage with live `-wal`/`-shm` sidecars** — an overwrite succeeded with both
  fabricated. The guard is ours to write. (Basin has both sidecars present right now.)
- **GeoPackages are not byte-reproducible** (`gpkg_contents.last_change`) but `OGR_CURRENT_DATE` pins them.
  GeoTIFF / PNG / GeoJSON are reproducible and GDAL embeds no version or timestamp.
- **`technical_drawings_for_agents` never gains a GDAL dependency.** All GDAL work lives in `gis-tool`; `backdrop.py` reads
  the sidecar's manifest JSON with the standard library only.
- The frame-match guarantee is a **cross-check** (manifest bbox vs sheet viewport, failing loudly), not a
  shared config field — so it also catches a stale sidecar left from a previous frame.

---

## 4. Boundary with #51 and P1

- **#51** owns the GeoPackage↔text placements bridge (`placements export` / `placements seed`). P8 owns
  raster preparation, the linked backdrop, and the `-wal`/`-shm` write guard. State the split in the README.
- **PyQGIS stays confined to authoring the `.qgs`.** Do not rewrite that path.
- **P1 (#53)** owns paper space. `backdrop.py` consumes a viewport interface for the target DPI; it must not
  compute paper geometry itself. Today's `ViewBox` has no CRS field, so the CRS cross-check applies only when
  the passed object exposes `crs`/`model_crs` — keep A's `getattr` approach and leave the gap stated.

## 5. Implementation plan

1. Base on variant A.
2. Apply corrections 1–3.
3. Fix the `stage` manifest regression and add the golden fixture.
4. Full suite green in **both** CI jobs. Predecessor state is A: `2 failed, 40 passed, 1 skipped`; target
   `0 failed`.
5. Tests must run without the confidential rasters — synthetic fixtures only. Keep A's assertion that the
   surveyed P5 (`E 788609.589, N 322341.151`, EPSG:32630) lands at the arithmetically-derived pixel.

## 6. Constraints (absolute)

- **Never commit confidential data.** Site imagery, point clouds and terrain stay outside git, absolutely.
- **Backward compatibility is sacred** — existing `stage`/`build` behaviour unchanged for existing configs.
- **CLI-first:** no pipeline step may require QGIS or any GUI. GDAL binaries only; no `osgeo`, no `pyproj`.
- **Never trust an exit code** where GDAL is concerned. Measure the output.
- **The ISSUED gate is untouchable.**

---

## 7. CORRECTIONS to this document, from the build (PR #92)

### 7.1 Correction 3's diagnosis was WRONG — variant A made no compat regression

§2 stated that variant A "reshaped the existing `stage` manifest" and called it a backward-compat regression.
**That is false, and it unfairly characterised A's work.** Verified two ways:

- A's PR touches no `stage` file at all (`gh pr view 86 --json files` → no match for `stage`).
- `stage` records the **source's own SRS**, not the project CRS (`stage.py:97`, `crs = _srs_id(source_srs)`).

The failing `test_stage_manifest_shape_remains_legacy` had a **wrong expectation**: it asserted
`crs: EPSG:32630` when `stage` reports the source's SRS, and OGR reports a CRS-less GeoJSON as **EPSG:4326**.
The build confirmed this against unmodified `main`.

**So the failure was a bad test, not a regression.** The remedy — freeze the shape with a committed golden
fixture — is still right and is retained, plus a test that `geo` leaves `<gpkg>.manifest.json` byte- and
mtime-identical. But the *reason* given in §2 was wrong, and the "as with variant B on P3" comparison does not
apply. Recorded rather than deleted.

### 7.2 Correction 1 was incomplete — the real root cause was a CRS-parsing bug

Sanctioning `gdaltransform` was necessary but **not sufficient**. The build found the actual reason the
pre-flight never fired, even on A's own same-CRS disjoint fixture: `_extract_crs` matched
`ID["EPSG",(\d+)]` and took the **first** hit — but **WKT2 nests the base *geographic* CRS first**, so a
UTM 30N raster's first EPSG id is **4326**, not 32630. Verified directly on the real Basin backdrop:

```
$ gdalinfo ga_backdrop.tif | grep -oE 'ID\["EPSG",[0-9]+\]'
ID["EPSG",4326]      <- base geographic CRS, matched first
ID["EPSG",9807]
...
$ gdalinfo -json ga_backdrop.tif | jq .stac.'"proj:epsg"'
32630                <- the actual CRS
```

**Every projected source therefore read as geographic**, so `source_crs in ("", cfg.crs)` was false and the
check was skipped. **Binding: read the CRS from `gdalinfo`'s `stac.proj:epsg`, never by regex over WKT.**

This is why the manifest must record `preflight.method` (`same_crs` | `gdaltransform` |
`skipped_unknown_source_crs`) — a **skipped** check must never be readable as a **passed** one.

### 7.3 New finding — `rsvg-convert` silently refuses a parent-escaping `href`

librsvg 2.62.3 loads a linked raster only from the SVG's own directory **or below**. A relative
`../geo/bd.png` — **the base spec's own §3.7 example, and its §7 Basin migration layout** — yields
**exit 0, empty stderr, and a 955-byte blank PDF.** `--unlimited` does not help.

**Binding, and it changes the Basin migration plan:** the sheet must be emitted **at or above the sidecar's
directory**. `backdrop_image_element` now refuses a parent-escaping href (opt-out for `cairosvg`, which does
not share the restriction). This is the third instance of the same hazard class in this workstream — a GDAL or
rendering tool exiting 0 while producing a useless artifact.

### 7.4 New finding — nodata coverage was fabricated

The base spec's `-nomd` flag on the post-write `gdalinfo` **suppresses `STATISTICS_VALID_PERCENT`**, so
variant A returned a hard-coded `1.0`. **A wholly blank DTM would have passed any coverage floor** — exactly
the failure that produced the dead 30 MB all-white raster (#75). Now measured for real (0.9952 on the DTM
fixture); when it cannot be measured, it **raises** rather than assuming full coverage.

### 7.5 Verified outcome

`technical_drawings_for_agents` 191 → **231 passed**; `gis-tool` 8 → **48 passed** locally with real GDAL + PyQGIS
(CI: 46 passed, 2 skipped where `rsvg-convert` is absent). Both confirmed by the coordinator, not taken on
the agent's word. No test deleted or weakened; no binary or confidential raster added.
