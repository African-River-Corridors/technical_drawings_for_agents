"""technical_drawings_for_agents: engineering drawings as data, for AI agents.

One JSON **drawing document** describes a whole sheet. The reference implementation turns it into
normalised content, and from that into SVG, PDF and DXF. See ``docs/standard/`` for the Drawing Format
Standard and ``technical_drawings_for_agents.dfs`` for the code.
"""

from __future__ import annotations

__version__ = "0.1.0"

from .dfs import normalise_document, run_document, run_file  # noqa: E402,F401

__all__ = ["__version__", "normalise_document", "run_document", "run_file"]
