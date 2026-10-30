# Roadmap

Short and living. Each item becomes a GitHub issue labelled `roadmap`; comment there to help shape it.

- **PyPI publication** ([#1](https://github.com/African-River-Corridors/drawing-format-standard/issues/1)) — `pip install drawing-format-standard`, released from tags by trusted publishing.
- **Agent harness integration** ([#2](https://github.com/African-River-Corridors/drawing-format-standard/issues/2)) — drive drawing builds from the agent harness, with review bundles as outputs.
- **More drawing types** ([#3](https://github.com/African-River-Corridors/drawing-format-standard/issues/3)) — longitudinal profiles, cross-section galleries on one sheet, plan-and-profile
  sheets, and richer P&ID symbol libraries.
- **A public example on open data** ([#4](https://github.com/African-River-Corridors/drawing-format-standard/issues/4)) — a worked river example on a downloadable open DEM.
- **A build driver over drawing documents** ([#34](https://github.com/African-River-Corridors/drawing-format-standard/issues/34)) — nodes that build drawing documents in dependency order, with declared outputs and a `--check` mode.
- **Legibility checks on the normalised content** ([#35](https://github.com/African-River-Corridors/drawing-format-standard/issues/35)) — text collisions and fill ratio, defined in the standard so every implementation can run them.
- **A Rust implementation** (in the repository of the app that needs it) — it must pass the conformance suite in `docs/standard/conformance/`.
