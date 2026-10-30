<picture>
  <source media="(prefers-color-scheme: dark)" srcset="website/assets/dfs-lockup-dark.svg">
  <img alt="DFS, Drawing Format Standard: an ARC open library" src="website/assets/dfs-lockup.svg" width="440">
</picture>

**Engineering drawings as data, for AI agents.** This repository holds the Drawing Format Standard (DFS)
and its Python reference implementation, `drawing-format-standard` (`import dfs`, command `dfs`).
[Docs page](https://www.africanrivercorridors.com/dfs) · [The standard](docs/standard/drawing-format-standard.md) · [Rust implementation (OTD)](https://www.africanrivercorridors.com/otd)

One JSON file describes a whole drawing sheet. The tool checks it and gives you an **SVG**, a **PDF** and a
**DXF** with the full sheet: model space in metres, and a paper-space layout with the border, the title block,
the revision register and the viewports at their stated scales.

The format is a published standard, the **Drawing Format Standard**, in [`docs/standard/`](docs/standard/).
This package is its reference implementation. A second implementation (in Rust, in another repository) must pass
the same [conformance suite](docs/standard/conformance/).

- **For agents and scripts.** A drawing is data. You write JSON, check it against a JSON Schema, and build it.
  You never write drawing code.
- **Same drawing, any implementation.** Two implementations are the same when they give the same *normalised
  content*: a sorted list of entities in paper millimetres. SVG, PDF and DXF bytes may differ.
- **Loud failure.** A value that disagrees with the drawing, a text that does not fit its cell, an undeclared
  layer, a window that does not fit its scale: each is an error that names the clause of the standard.

## Install

```bash
pip install drawing-format-standard                # from PyPI, once published
# or, from a checkout:
python -m venv .venv && .venv/bin/pip install -e '.[dev]'
```

No system tool is needed. The PDF is written directly, with the font embedded.

## A first drawing

Save this as `hello.drawing.json`:

```json
{
  "standard_version": "1.0",
  "sheet": { "size": "A3", "orientation": "landscape" },
  "layers": [ { "name": "OUTLINE" }, { "name": "DIMENSIONS" } ],
  "viewports": [
    {
      "id": "main",
      "frame": { "x_mm": 20, "y_mm": 10, "width_mm": 390, "height_mm": 277 },
      "window": { "xmin_m": 0, "ymin_m": 0, "xmax_m": 10, "ymax_m": 8 },
      "scale": 100,
      "scale_bar": {},
      "entities": [
        { "type": "line", "layer": "OUTLINE", "id": "base", "from_m": [1, 1], "to_m": [9, 1] },
        { "type": "dimension", "layer": "DIMENSIONS", "kind": "linear", "orientation": "horizontal",
          "from": { "ref": "base", "at": "start" }, "to": { "ref": "base", "at": "end" }, "offset_mm": -10 }
      ]
    }
  ]
}
```

Build it:

```bash
dfs build hello.drawing.json --out out/
# wrote out/hello.normalised.json, out/hello.svg, out/hello.pdf, out/hello.dxf
```

The dimension text is `8.00`. You did not type it: it is computed from the line that the dimension points at.
Coordinates in a viewport are in **model metres** (+y up). Everything on paper is in **millimetres**. The name of
every number ends in its unit: `_m`, `_mm`, `_deg`.

## Commands

| Command | What it does |
|---|---|
| `validate FILE` | Check a drawing document. Exit 1, and print the clause numbers, on any error. |
| `normalise FILE` | Print the normalised content (the thing that two implementations compare). |
| `build FILE --out DIR` | Write the normalised content, the SVG, the PDF and the DXF. Writes nothing if there is an error. |
| `run FILE` | The runner of the conformance contract (standard clause 17.3): print one result as JSON. |
| `conformance` | Run the conformance suite against this implementation. |
| `seal FILE` | Print the seals of the revision register. It writes nothing. |
| `describe FILE.dxf` | Describe a finished DXF: layers, layouts, and the plot scale each viewport holds. |
| `ingest FILE.dwg` | Convert and clean a supplier DWG or DXF (needs the `ingest` extra). |

Errors look like this, and each number is a clause of the standard:

```text
$ dfs validate bad.drawing.json
error: clause 7.5.1        # the window does not fit the frame at the stated scale
error: clause 14.6         # a text is wider than its title block cell
```

## What a drawing document holds

| Member | Meaning | Clause |
|---|---|---|
| `standard_version`, `sheet` | The version, the ISO 5457 size A0 to A4, the orientation. | 6, 18 |
| `layers` | The layers in use. A standard layer is declared by name. A layer names a line **role**, not a width. | 8 |
| `viewports` | Each has a frame on the paper, a model window, a scale (a preferred ISO ratio or `fit`), an optional rotation, scale bar and north arrow, and its entities. From version 1.2, a long section may state a `vertical_exaggeration`: it gets a vertical scale bar and the words "Vertical exaggeration ×N". | 7 |
| entities | `line`, `polyline`, `arc`, `circle`, `centre_line`, `hatch`, `text`, `dimension`, `leader`, `symbol`, and from version 1.1 `image` (a georeferenced JPEG). | 9 to 13 |
| `sheet.print_profile` | `monochrome` (the default: black on white) or, from version 1.1, `colour`. Under `colour`, a layer may set `print_colour`, `fill_opacity` and `halo_colour`. | 8.6 |
| `symbol_libraries` | Symbols drawn once and placed many times, in a file or in the document. | 13 |
| `title_block`, `revisions`, `status`, `strips`, `notes`, `references` | The title block (ISO 7200 by default, or a template file of your own), the revision register, the status and its status line, and the notes strips. | 14 |

The standard is plain English with numbered clauses: [`docs/standard/drawing-format-standard.md`](docs/standard/drawing-format-standard.md).
The JSON Schema is [`docs/standard/schema/drawing-document-1.0.schema.json`](docs/standard/schema/drawing-document-1.0.schema.json).

### Rules that are never relaxed

- The **approver** in the title block is taken only from the `app` of the latest revision entry. No tool writes it
  and nothing defaults it.
- `status: issued` needs a title block and a register whose latest entry has an approver and a valid seal. An agent
  cannot set a flag to pass as issued.
- A text that does not fit is an error. Nothing is cut, shortened or shrunk.
- An undeclared layer, an unknown key, a repeated key and a non-standard scale are errors.

### Your own title block

A title block is a template file. The standard ships the ISO 7200 data fields in one layout. Your house title
block is a private file that you pass by path. It never has to enter any repository:

```json
{ "title_block": { "template_path": "my-title-block.json", "fields": { "title": "..." } } }
```

## Examples

| Folder | What it shows |
|---|---|
| [`drawings/example/simple-section/`](drawings/example/simple-section/) | A concrete-lined channel typical section: hatch, outline, centre line, dimensions, leaders, scale bar, title block, register, notes. |
| [`drawings/example/synthetic-pid/`](drawings/example/synthetic-pid/) | A synthetic P&ID built from a symbol library: pumps, valves, tank, instruments, with pipes that join ports. |

Each has an `out/` folder with the SVG, PDF, DXF and normalised content that `build` writes. Both are synthetic.
The values are made up and are not engineered.

## The standard, ISO, and what is checked

The standard bases sheets and borders on ISO 5457, scales on ISO 5455, line types and widths on ISO 128, title
block fields on ISO 7200, lettering on ISO 3098 and dimensioning on ISO 129. **The ISO texts were not at hand
when it was written.** Every value taken from an ISO part is marked *pending ISO check* in
[`docs/standard/iso-checklist.md`](docs/standard/iso-checklist.md). House choices are listed there with their
reasons. The font (Barlow Semi Condensed, SIL OFL 1.1) is close to ISO 3098 lettering and is not certified.

## The conformance suite

[`docs/standard/conformance/`](docs/standard/conformance/) holds data only: drawing documents, their expected
normalised content (or the expected clause numbers of an error), and unit checks. It needs no Python. The expected
values are checked by hand, and are never regenerated from an implementation.

```bash
dfs conformance        # 194 cases or more, all pass
```

## Other tools in this package

- `dfs.backdrop` reads a georeferenced backdrop manifest. The GDAL pipeline that makes
  backdrops is outside the standard.
- `ingest` and `describe`, in the table above, work on supplier DXF files.

## Removed in 1.0

The `source.py` way of drawing, the pixel-based SVG path, the separate SVG sheet path (title block, sheet
furniture and legibility checks that read an SVG), the SVG-to-PDF plot path, the component and layout engine, the
P&ID and block-flow generators, the old build driver and the provenance stamps are removed. Version 1.0 reads only
drawing documents. Two things that went with them are open tickets: a
[build driver over drawing documents](https://github.com/African-River-Corridors/drawing-format-standard/issues/34)
and [legibility checks on the normalised content](https://github.com/African-River-Corridors/drawing-format-standard/issues/35).

## Contributing and licence

See [CONTRIBUTING.md](CONTRIBUTING.md). Use synthetic data only: no client drawings or site data. Apache-2.0. The
font file is under the SIL Open Font License 1.1 (see `NOTICE`), and so are the fonts under `website/fonts/`
(licences beside them). The project names and logos are covered by [TRADEMARKS.md](TRADEMARKS.md).

Hosted by African River Corridors.
