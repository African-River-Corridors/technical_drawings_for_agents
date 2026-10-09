# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased] — pre-release

### Changed
- Geo manifest schema id is now `tdfa.geo/1`. SVG sheet furniture now writes `data-tdfa-*`
  attributes and the `tdfa-viewport` clip-path id.
- Specs refer to the external GIS tool that writes geo manifests as `gis-tool`.

- The house title-block example is now the neutral `EXAMPLE_A3` layout (Project, Title,
  Drawing No., Rev, Scale, Drawn, Checked, Approved, Sheet, Status; "REVISIONS" table). The
  sheet-furniture example is now `docs/specs/sheet-furniture.example.yaml`.

- Test fixtures, docs and examples no longer carry real site coordinates. Every UTM coordinate
  taken from a real survey frame was moved by one fixed offset to a synthetic location that is
  not near any real site. Relative geometry (distances, bearings, gaps) is unchanged; one pinned
  full-precision gap moved in its last digits (float rounding at the new magnitude).

- Examples, tests and docs use neutral site codes (`STA`, `STB`, `STC`) and neutral initials
  (`AB`, `CD`). The `isosheet` default client is now "Example Client".

### Removed
- The client-specific `ART_ROTULO_214` title-block layout and its sheet-furniture settings
  (`docs/specs/sheet-furniture.settings.yaml`). Supply your own layout with
  `TitleBlockLayout(...)` and `layout=`, and your own furniture with a settings file.

### Deprecated
- The `sankofa.geo/1` schema id and the `data-sankofa-*` SVG attributes. Both are still read, with
  a `DeprecationWarning`, and will be removed in a future release. See README, "Deprecated names".

### Added
- `sheet.read_sheet_data_attr` and `sheet.parse_sheet_data_value`: read a sheet data attribute
  under either spelling.
- `TitleBlockFields.project`, filled from `meta.project`.
- `validate` checks that a scale bar's data attribute agrees with the sheet metadata.
