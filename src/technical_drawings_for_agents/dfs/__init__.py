"""The reference implementation of the Drawing Format Standard (docs/standard/).

One pipeline: drawing document -> checks -> normalised content -> SVG and PDF.
The clause numbers in the comments are those of ``docs/standard/drawing-format-standard.md``.
"""

from __future__ import annotations

from .pipeline import normalise_document, run_document, run_file

__all__ = ["normalise_document", "run_document", "run_file"]
