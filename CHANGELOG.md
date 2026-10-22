# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added (ARC open library look)
- The DFS mark and lockup at the top of the README, and a docs page in `website/`, served at
  https://www.africanrivercorridors.com/dfs once GitHub Pages is on. The `website` workflow checks its links and
  deploys it.
- `TRADEMARKS.md`, the ARC open-source trademark policy.

### Added (standard 1.2, ticket 46)
- Vertical exaggeration (clause 7.3.6). A viewport may name `vertical_exaggeration`, a whole number from 2. Its
  `scale` is the horizontal scale; the vertical scale is N / E and must be a ratio of clause 7.3. For long sections.
- The viewport shows a vertical scale bar at the vertical scale, `Vertical 1:N` above it, and the words
  `Vertical exaggeration ×E` under the scale bar (clause 7.9.11). Words, never initials. The scale bar is required.
- An exaggerated viewport may not turn, may not hold an image, a circle, an arc or a symbol, and may not have a north
  arrow. Its dimensions are horizontal linear ones only (clauses 7.3.6.3 to 7.3.6.9).
- The title block scale of a sheet with an exaggerated viewport is `AS SHOWN` (clause 14.4.4).
- The DXF writes the entities of an exaggerated viewport in paper space, as drawn; model space stays true to scale
  (clause 15.4.13).
- House choice H-11 records the change (iso-checklist part G). H-60 to H-64 are new.
- 17 conformance cases (234 in all), and output tests. A 1.0 or 1.1 document has the same normalised content, SVG,
  PDF and DXF as before. The case `err-version-minor` now names version 1.3, since 1.2 is readable.

### Added (standard 1.1, ticket 43)
- The `image` entity (clause 9.10): a JPEG named by its SHA-256 and placed over a model box. It is for satellite
  imagery, orthophotos and shaded relief that a GIS tool has already clipped and projected. The SVG and the PDF embed
  the JPEG bytes as they are (`DCTDecode`). The DXF links the image with IMAGE and IMAGEDEF. Images are drawn first.
- The colour print profile (clauses 8.6.4 to 8.6.7). `sheet.print_profile: colour` lets a layer declare
  `print_colour`, `fill_opacity` and `halo_colour`. The furniture stays black. Under the colour profile, the scale bar
  and the north arrow of a viewport that holds an image stand on white plates.
- Map ratios 1:200000 to 1:10000000 (clause 7.3.5).
- Clause 18.2.1: a document is held to the version it names. A 1.0 document has the same result as before, and
  its SVG, PDF and DXF are unchanged.
- 17 conformance cases (217 in all), and output tests for images and colour.

### Removed (the clean break, ticket 16)
- The `source.py` way of drawing and its API (`Drawing`, `ViewBox`, the `svg_*` primitives, `DxfBuilder`), the
  pixel-based SVG path, the separate SVG sheet path (title block, sheet furniture, sheet validation and the
  legibility checks that read an SVG), the SVG-to-PDF plot path, layer tables in YAML, the component and layout
  engine, the P&ID and block-flow generators, map sheets, the old build driver and the provenance stamps.
  Version 1.0 reads only drawing documents. Two things that went with them are open tickets: a build driver over
  drawing documents (#34) and legibility checks on the normalised content (#35).
- The `matplotlib` dependency, and the `cad3d`, `plot`, `plot-cairo` and `verify` extras. `rsvg-convert` is no
  longer needed.

### Changed
- The status is a small line of text, not a diagonal watermark (clause 14.9, house choice H-53). The layer `WATERMARK`
  and the origin `sheet.status_watermark` are removed; the origin is `sheet.status_line`. `issued` shows no line.
- The examples in `drawings/example/` are drawing documents, with their SVG, PDF, DXF and normalised content.
- The commands are `validate`, `normalise`, `run`, `build`, `seal` and `conformance`, then `describe` and
  `ingest`. The README describes only this way.
- Goldens (`tests/goldens/`) are the two examples' normalised content and SVG, checked by hand.

### Added
- `docs/standard/drawing-format-standard.md`: the Drawing Format Standard (spec 0001). Clauses 1 to 8
  are written: the drawing document, units and precision, sheets, viewports, scales, the scale bar
  and the north arrow. Clause 8 adds layers, line types, line groups and colour, with the
  machine-readable table `docs/standard/data/line-styles.json`. Clauses 9 (geometry), 10 (hatches,
  partly), 11 (lettering heights, partly) and 18 (versions and the schema) are added.
- Clauses 16 (normalised content) and 17 (conformance), and the first conformance suite in
  `docs/standard/conformance/`: 75 cases (documents and unit checks) with hand-checked expected
  content, and a language-neutral runner contract.
- The font of the standard: Barlow Semi Condensed Regular (SIL OFL 1.1) with its licence in
  `src/technical_drawings_for_agents/data/fonts/`, the width table `docs/standard/data/font-widths.json`,
  clauses 11.6 and 11.7 (font, width table, text box) and their conformance cases.
- `technical_drawings_for_agents.dfs`: the reference implementation of the standard. One pipeline:
  drawing document, checks (with clause numbers), normalised content, SVG and PDF (the font embedded,
  repeat builds identical). Commands: `technical_drawings_for_agents doc validate|normalise|run|build|conformance`.
  It passes every conformance case. `jsonschema` is now a runtime dependency.
- Hatch marks (clause 10.5, `docs/standard/data/hatch-patterns.json`): the spacing, angles and shapes of
  `lines`, `soil`, `water`, `concrete` and `rockfill`, tied to the line group, clipped to the boundary
  and holes. The marks are entities of the normalised content. Ten new conformance cases.
- DXF output from a drawing document (clause 15.4): model space in metres, a paper-space layout `SHEET`
  with the border, furniture, hatch marks and one viewport for each viewport at its stated scale, layers
  with colours and snapped widths, line types in paper millimetres, and the font named. `doc build`
  writes it. A test reads it back with a reader that does not use ezdxf.
- Dimensions and leaders (clause 12): linear, aligned, radius, diameter and angle dimensions and leaders,
  computed from the geometry that they point at, with a chain-sum check. Schema, reference pipeline,
  23 conformance cases and unit checks.
- Symbols (clause 13): a library format (`docs/standard/schema/symbol-library-1.0.schema.json`) with
  geometry in paper millimetres or model metres, an insertion point, ports and a label slot; placement by
  reference with position, rotation and scale; ports that dimensions can point at; DXF blocks and inserts.
  The reference pipeline, 25 conformance cases and unit checks.
- Sheet furniture (clause 14), all opt-in: title block templates (`title-block-template-1.0.schema.json`) with
  the shipped ISO 7200 template and private templates by path; derived fields (the approver only from the
  latest revision entry); a fit check that never cuts text; the revision register (oldest first, newest by
  the block, overflow row, sealed entries); the status watermark with the issued gate; notes and references
  strips. `doc seal` prints the seals and writes nothing. 48 conformance cases and unit checks.
- `docs/standard/schema/drawing-document-1.0.schema.json`: the JSON Schema (draft 2020-12) for the
  drawing document, with valid and invalid example documents and their expected errors.
- `docs/standard/iso-checklist.md`: every value taken from an ISO part is marked "pending ISO check"
  here until it is verified. House choices are listed with their reasons.

## [0.1.0] — first public version

### Added
- Paper-space sheets, the build driver, deterministic emit, the PDF plot path, legibility checks,
  computed dimensions and chains, the layer table, geo backdrops, revisions and numbering, stable
  placement ids, P&ID and block-flow diagrams, and supplier DWG ingest.
- `sheet.read_sheet_data_attr` and `sheet.parse_sheet_data_value`: read a sheet data attribute
  under either spelling.
- `TitleBlockFields.project`, filled from `meta.project`.
- `validate` checks that a scale bar's data attribute agrees with the sheet metadata.
- A neutral `EXAMPLE_A3` title-block layout and `docs/specs/sheet-furniture.example.yaml`.
- `tools/release_guard.py`: a tree and history scan for embargoed terms, coordinate-like pairs and
  machine-local paths.

### Deprecated
- Two legacy spellings from the toolkit's pre-release name — a geo manifest schema id and a
  `data-*` SVG attribute prefix — are still read, with a `DeprecationWarning`, and will be removed
  in a future release. See README, "Deprecated names".

All examples, fixtures and goldens use synthetic data.
