# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

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
