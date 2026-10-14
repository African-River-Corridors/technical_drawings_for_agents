# Spec 0001: Drawing Format Standard

Status: **approved by the maintainer, 2026-10-14.**

## Problem Statement

`technical_drawings_for_agents` draws from Python code. A drawing is a `source.py` file that calls
the library. There is no data format that describes a drawing, so nothing outside Python can produce
the same drawing.

A desktop app for barge design (Open Vessel Concept Drawings) needs to draw General Arrangements
without Python. Its decision record says there is one **Drawing Format Standard** with two
implementations: Python here, for agents and scripts, and Rust in the app. Both must give the same
drawings. The standard does not exist yet.

The tool also has gaps a standard must close:

- The DXF holds model space only. The sheet (border, title block, revision register) exists only in
  the SVG and PDF, through a separate code path. A drafter who opens the DXF gets no sheet.
- Line widths, layer colours and dash patterns are proposals, not a settled standard. Three layers
  share one CAD colour.
- The sheet sizes, margins and title block are house defaults. None is taken from a published
  standard.
- Fonts are not embedded, so text width, and therefore title block fit, depends on the machine.
- The rules for scales, rounding, revision registers and layers are written down, but not in this
  repository.

## Solution

Write the **Drawing Format Standard** in this repository, based on the ISO drawing standards, and
make this tool its reference implementation.

1. **The standard** is a document in this repository. It defines a **drawing document**: one JSON
   file that describes a whole sheet. That covers the sheet, viewports at stated scales, layers,
   geometry in model metres, text, hatches, dimensions, leaders, symbols, and the sheet furniture.
   A published **JSON Schema** validates it in any language.
2. **ISO base.** Sheets and borders follow ISO 5457, title block fields ISO 7200, line types and
   widths ISO 128, lettering ISO 3098, and dimensioning ISO 129. Each value the standard takes from
   an ISO part is cited to its clause. The current house defaults are replaced.
3. **Same drawing, two implementations.** "Same" means the same **normalised content**. Rendering
   a drawing document gives a list of entities: layer, geometry rounded to the standard's precision,
   text, line type and line width. Two implementations give the same list. SVG, DXF and PDF bytes
   may differ. A **conformance suite** of drawing documents and their expected normalised content is
   part of the standard.
4. **Outputs.** One drawing document gives the SVG, the PDF and a DXF with the full sheet: model
   space in metres, and a paper-space layout with border, title block, revision register and
   viewports at the stated scales.
5. **This tool becomes the reference implementation, with a clean break.** Version 1.0 reads only
   drawing documents. The `source.py` way of drawing is removed. The examples in this repository are
   rewritten once as drawing documents.

The Rust implementation is a separate spec in the app's repository. It must pass the same
conformance suite.

## User Stories

### Agents and scripts
1. As an agent, I want to describe a whole drawing in one JSON file, so that I can make a drawing without writing drawing code.
2. As an agent, I want a JSON Schema for that file, so that I can check my file before I build it.
3. As an agent, I want schema errors that name the field and the rule, so that I can fix my file.
4. As an agent, I want every coordinate in model metres and every paper size in millimetres, stated by the schema, so that I never mix units.
5. As an agent, I want one command that builds a drawing document into SVG, PDF and DXF, so that I get all outputs from one source.
6. As an agent, I want a command that prints a drawing document's normalised content, so that I can compare two builds.
7. As an agent, I want a build to fail when a stated value disagrees with what is drawn, so that I never issue a wrong drawing.

### Sheet
8. As a drafter, I want ISO 5457 sheet sizes from A0 to A4 in either orientation, so that sheets print on standard paper.
9. As a drafter, I want the border, filing margin and grid reference marks of ISO 5457, so that the sheet is laid out as I expect.
10. As a drafter, I want several viewports on one sheet, each at its own stated scale, so that a plan and a section can share a sheet.
11. As a drafter, I want each viewport's scale to be a preferred ISO scale or a declared fit, so that scales are standard.
12. As a drafter, I want the scale bar length and divisions derived from the viewport scale, so that the scale bar is always right.
13. As a drafter, I want the north arrow's direction derived from the drawing's rotation, so that it always points north.
14. As a drafter, I want a status watermark (draft, concept, issued) with a hard gate on issued, so that an unchecked drawing cannot pass as issued.
15. As a drafter, I want notes and references tables placed in declared sheet strips, so that the sheet furniture does not overlap the drawing.

### Title block and revisions
16. As a drafter, I want the ISO 7200 title block fields, so that the title block holds the data the standard requires.
17. As a drafter, I want the title block layout to come from a title block template file, so that a house title block can be used.
18. As an organisation, I want to use my own house title block as a private template file, so that my sheet is produced without publishing my template.
19. As a drafter, I want a build to fail when title block content does not fit its field, so that text is never cut off.
20. As a drafter, I want a revision register (oldest first, newest next to the title block, sealed entries fixed), so that revisions are traceable.
21. As a drafter, I want the approver taken only from the latest revision entry, never defaulted, so that approval is never invented.

### Lines, layers and colour
22. As a drafter, I want ISO 128 line types (continuous, dashed, chain, chain double-dashed) with their dash patterns in paper millimetres, so that lines read the same on every sheet.
23. As a drafter, I want layers to name a line role (extra thick, thick, thin) rather than a width, so that widths follow the line group.
24. As a drafter, I want the line group chosen by sheet size (A4 and A3: 0.35; A2 and A1: 0.5; A0: 0.7), so that line weight suits the sheet.
25. As a drafter, I want an undeclared layer to be an error, so that every entity is on a known layer.
26. As a drafter, I want the PDF to print in black, so that the sheet reads as an ISO drawing.
27. As a CAD user, I want each DXF layer to carry its own CAD colour, with no two standard layers sharing one, so that I can tell layers apart.
28. As a CAD user, I want DXF line widths snapped to the nearest CAD line width, ties to the thinner, so that the DXF matches the PDF.

### Geometry, hatches and text
29. As a drafter, I want lines, polylines, arcs, circles and centre lines, so that I can draw any outline.
30. As a drafter, I want named hatch patterns (including concrete, rockfill, soil and water), so that materials are shown the standard way.
31. As a drafter, I want text with ISO 3098 lettering in the standard heights, so that text is legible and standard.
32. As a drafter, I want one open-licence ISO 3098 font, embedded in the PDF and named in the DXF, so that text width is the same on every machine.

### Dimensions and leaders
33. As a drafter, I want linear, aligned, radius, diameter and angle dimensions to ISO 129, so that dimensions look standard.
34. As a drafter, I want each dimension's value computed from the geometry it points at, so that the number always equals the drawn length.
35. As a drafter, I want a build to fail when chained dimensions, once rounded, do not add up to the rounded overall, so that totals agree.
36. As a drafter, I want leaders with labels, so that I can name parts.

### Symbols
37. As a drafter, I want a symbol library format (geometry in paper or model units, insertion point, ports, label slot), so that symbols are drawn once and reused.
38. As a drafter, I want to place a symbol by reference with a position, rotation and scale, so that a drawing stays short.
39. As a CAD user, I want symbols written as DXF blocks and inserts, so that I can edit them as symbols in CAD.

### DXF and PDF
40. As a CAD user, I want model space in metres and a paper-space layout with the whole sheet, so that the DXF is a complete drawing.
41. As a CAD user, I want paper-space viewports at the stated scales, so that the DXF plots at scale.
42. As a reviewer, I want the PDF built from the same normalised content as the DXF, so that the two cannot disagree.

### Determinism and conformance
43. As a maintainer, I want fixed rounding (half-even) and fixed precision per quantity (model metres, paper millimetres, degrees), so that output is stable.
44. As a maintainer, I want the same drawing document to give the same normalised content every time, so that builds are reproducible.
45. As a maintainer, I want a conformance suite of drawing documents with their expected normalised content, so that any implementation can prove it follows the standard.
46. As a maintainer, I want the suite to include the worked numbers of the standard (scale bar fit, line width snap, title block capacity), so that the edge cases are pinned.
47. As the maintainer of a second implementation, I want to run the suite without Python, so that the Rust implementation is checked on its own.
48. As a maintainer, I want the standard and the schema versioned together, with each drawing document naming the version it follows, so that old files stay readable or fail clearly.

### Migration
49. As a maintainer, I want every example in this repository rewritten as a drawing document, so that the examples show the new way.
50. As a maintainer, I want a list of every visible change from the house defaults to ISO values, so that users know what moved.

## Implementation Decisions

### The standard
- **One document** in this repository, written in plain English, with numbered clauses so that tests and code can cite them. It covers: the drawing document; units and precision; sheets; viewports and scales; layers, line types, line groups and colour; geometry; hatches; lettering and font; dimensions and leaders; symbols; sheet furniture (border, title block, revision register, scale bar, north arrow, status watermark, notes and references); output mapping to SVG, PDF and DXF; normalised content; conformance.
- **ISO values are transcribed from the ISO parts and cited by clause** while the standard is written. No value is taken from memory or from the current house defaults without a citation. A value the ISO parts leave open is marked as a house choice, with the reason.
- **Neutral rules from the tool's internal design notes** (paper-space sheet and scales, deterministic output, layer table, revision register and title block, computed dimensions, chain checks) are restated in plain words. No client names, title blocks, site data or links to private material.

### The drawing document
- **JSON, validated by a JSON Schema** (draft 2020-12) published with the standard. Unknown keys are errors.
- **Top level:** standard version, sheet (size, orientation, line group), title block (template reference and field values), revision register, status, layers in use, symbol library references, viewports, notes and references.
- **Viewport:** paper position and size, scale (preferred ISO scale or declared fit), model window, rotation, and its entities.
- **Entities:** line, polyline, arc, circle, centre line, hatch (boundary and named pattern), text, dimension (linear, aligned, radius, diameter, angle; references geometry, value computed), leader, symbol insert.
- **Units:** model coordinates in metres, +y up; paper in millimetres. The schema names the unit of every number.

### Title block templates
- A **title block template** file defines a block's fields, their positions and sizes in paper millimetres, and text heights. The standard ships the ISO 7200 template. Other templates are separate files a user supplies; private ones never enter this repository.

### Line groups and colour
- Layers name a line role. The line group, by sheet size, turns roles into widths: A4 and A3 group 0.35, A2 and A1 group 0.5, A0 group 0.7, with thin lines half of thick, per ISO 128.
- The PDF and SVG print in black. DXF layers carry CAD colour numbers, one per standard layer.

### Font
- One open-licence font drawn to ISO 3098, adopted only after its licence is checked to allow embedding and redistribution. It ships with the tool, is embedded in every PDF, and is named in the DXF. Its width table is part of the standard, so text fit is computed the same way in every implementation.

### Normalised content
- The standard defines a canonical, sorted list of entities after rendering: layer, entity type, geometry in paper millimetres rounded to the standard's precision, line type, line width, text and font size. Two implementations conform when they give the same list for every conformance case.

### This tool as the reference implementation
- **Version 1.0 reads only drawing documents.** The `source.py` route, the pixel-based legacy drawing path and the separate SVG sheet path are removed. One pipeline: drawing document → normalised content → SVG, PDF and DXF.
- Commands: build a drawing document; validate it; print its normalised content; run the conformance suite.
- The DXF is written with a full paper-space layout.
- The existing checks (scale agreement, title block fit, legibility, computed dimensions, chain sums) run on the normalised content, so every implementation can run them.

## Testing Decisions

- **The seam is the conformance suite**: a drawing document goes in, normalised content comes out. That is the one seam both implementations share, and the main test of this spec.
- **A good test** checks that seam or a check's verdict on it, not the internals of a renderer.
- **The suite** holds drawing documents with their expected normalised content: one per entity type, one per sheet size and line group, the ISO title block, a full revision register, a multi-viewport sheet, symbols, and the worked numbers of the standard. Expected content is written by hand or checked by hand once. It is never regenerated to make a test pass.
- **The JSON Schema** is tested with valid and invalid documents; each invalid one names the error expected.
- **Output tests:** for each conformance case, the DXF and PDF read back give the same normalised content as the build. DXF read-back uses a library other than the one that wrote it.
- **Prior art:** the tool's golden tests (files, not pinned hashes) and the worked numbers in its current scale-bar, line-width and title block tests.

## Out of Scope

- The Rust implementation (a separate spec in the app's repository).
- Vessel General Arrangement content: which views a barge needs and what goes on them. That belongs to the app.
- Geo backdrops and the GDAL pipeline, and drawing-set build graphs. They stay as they are, outside the standard.
- P&ID symbol content beyond what the conformance suite needs. The library format is in scope; a full symbol set is not.
- Any private title block template.
- Copies of this tool in other repositories. They are not changed by this spec.

## Further Notes

- **Suggested tickets, in order:** (1) the standard's skeleton and clause numbering, with sheets, units and precision; (2) layers, line types, line groups and colour; (3) font choice and licence check, and the lettering clauses; (4) the JSON Schema for sheet, viewports and core geometry; (5) normalised content and the first conformance cases; (6) the reference pipeline in Python for core geometry, SVG and PDF; (7) DXF with paper space; (8) dimensions and leaders; (9) symbols and blocks; (10) title block templates, revision register and sheet furniture; (11) rewrite the examples and remove the old routes; (12) the list of visible changes and release 1.0.
- The Rust implementation in the app can start once tickets 4 and 5 give it a schema and a first set of conformance cases.
