# Goldens

`<number>.normalised.json` and `<number>.svg` are the output of the two example drawings in
`drawings/example/`. The SVG is made without the embedded font, so that it stays small.

They were **checked by hand**: the key numbers (the corners of the lining, the scale bar, the cell texts of the
title block, the ports of the P&ID) are worked out again in `tests/test_examples.py` from the clauses of the
standard, and the sheets were looked at as PDF.

**Do not regenerate a golden to make a test pass.** If the build and a golden disagree, find out why. If the
change is right, say so in the pull request and update the golden together with the hand check in the test.
