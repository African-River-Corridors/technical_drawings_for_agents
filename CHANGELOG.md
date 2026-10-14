# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- `docs/standard/drawing-format-standard.md`: the Drawing Format Standard (spec 0001). Clauses 1 to 8
  are written: the drawing document, units and precision, sheets, viewports, scales, the scale bar
  and the north arrow. Clause 8 adds layers, line types, line groups and colour, with the
  machine-readable table `docs/standard/data/line-styles.json`. Clauses 9 (geometry), 10 (hatches,
  partly), 11 (lettering heights, partly) and 18 (versions and the schema) are added.
- Clauses 16 (normalised content) and 17 (conformance), and the first conformance suite in
  `docs/standard/conformance/`: 75 cases (documents and unit checks) with hand-checked expected
  content, and a language-neutral runner contract.
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
