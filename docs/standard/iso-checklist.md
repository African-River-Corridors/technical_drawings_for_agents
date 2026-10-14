# ISO check list and house choices

This file belongs to the [Drawing Format Standard](drawing-format-standard.md).

The ISO texts (ISO 128, 5455, 5457, 7200, 3098 and 129) were not at hand when the standard was
written. Every value that the standard takes from an ISO part is a **proposal**. It is listed in
part A. Nobody may call a value "from ISO" until its item is closed.

To close an item: read the clause of the ISO part, compare the value, then write the clause number,
the date and the result in the "Closed" column. Do not copy ISO text into this repository.

The "Recall" column says how sure the author is of the proposed value. It is a guide to where to
look first. It is not evidence.

## A Pending ISO checks

| ID | ISO part | Clause | Proposed value | Used in | Recall | Closed |
|---|---|---|---|---|---|---|
| P-01 | ISO 5457 (A-series sizes are also in ISO 216) | to be confirmed | Trimmed sizes: A0 841 x 1189, A1 594 x 841, A2 420 x 594, A3 297 x 420, A4 210 x 297 mm | 6.1 | high | open |
| P-02 | ISO 5457 | to be confirmed | Border inset from the trimmed edge: left (filing margin) 20 mm on every size. Other three edges 20 mm on A0 and A1, 10 mm on A2, A3 and A4 | 6.3 | medium | open |
| P-03 | ISO 5457 | to be confirmed | Border line is continuous and at least 0.7 mm wide | 6.3.3 | medium | open |
| P-04 | ISO 5457 | to be confirmed | Four centring marks at the middle of each side. Each is 0.5 mm wide. Each runs from the trimmed edge to 5 mm inside the drawing space | 6.4 | low | open |
| P-05 | ISO 5457 | to be confirmed | Grid zones (long side x short side): A0 24 x 16, A1 16 x 12, A2 12 x 8, A3 8 x 6, A4 6 x 4. Equal division of the border side | 6.5 | medium (A0 is the least sure) | open |
| P-06 | ISO 5457 | to be confirmed | Zone numbers along the horizontal sides, zone letters along the vertical sides, letters I and O not used. Zone marks 0.35 mm wide | 6.5.2, 6.5.3 | medium | open |
| P-07 | ISO 5455 (check whether a later part has replaced it) | to be confirmed | Preferred ratios 1:1, 1:2, 1:5 and their powers of ten (the standard uses 1:1 to 1:100000) | 7.3.1 | high | open |
| P-08 | ISO 3098 (the clause for the height series) | to be confirmed | Standard letter heights in millimetres include 2.5 and 3.5. The standard uses 2.5 mm for scale bar text and 3.5 mm for zone labels and the north label | 6.5.3, 7.9.1, 7.10.5 | high | open |

## B House choices

A house choice is a value or a rule that ISO does not set, or that this standard makes simpler on
purpose. It needs a reason. It does not need an ISO check.

| ID | Choice | Reason | Used in |
|---|---|---|---|
| H-01 | An unknown key in a document is an error | A silently ignored key can leave a drawing at a different scale from the one written | 4.3 |
| H-02 | The unit of a number comes from its member name ending (`_m`, `_mm`, `_deg`) | The two length units stay visible at every number | 4.5 |
| H-03 | Every finding names the clause it enforces | Tests and users can find the rule | 4.6 |
| H-04 | Paper origin top-left, +y down | SVG and PDF viewers use the same sense, so no output step flips a sign | 5.2 |
| H-05 | Precision: 6 places for model metres, 3 for paper millimetres, 3 for degrees | One micrometre is far below any drawing need and far above 64-bit rounding noise | 5.4 |
| H-06 | Half-even rounding on the exact binary value | One rule that is the same in every language | 5.5 |
| H-07 | Fit tolerance 1e-6 mm, on fit tests only | Absorbs binary rounding and nothing more | 5.7 |
| H-08 | Either orientation for every size | The standard sets no rule that ties a size to one orientation | 6.2 |
| H-09 | The border line is centred on the border rectangle | The simplest rule. The line is at most 1.4 mm wide | 6.3.3 |
| H-10 | Only preferred ratios and `fit`. No 1:25, 1:250, 1:1250, and no enlargements | One list of scales, so every scale can be set on a scale rule | 7.3.2 |
| H-11 | One scale per viewport, no exaggerated height | A wrong exaggeration is worse than none | 7.3.3 |
| H-12 | The scale applies to coordinates only, never to widths or text | A scale on the whole drawing would also scale every line width by 1/N | 7.4.1 |
| H-13 | An entity outside its viewport frame is an error | A stray point is a loud error, never a silent clip | 7.7 |
| H-14 | Scale bar constants: inset 6, bar height 3, tick 1.5, label baseline 5.0, ratio baseline 9.0, alternate segments solid | ISO sets no size for a scale bar. The baselines leave clear gaps for 2.5 mm text | 7.9.1, 7.9.5 |
| H-15 | Derived bar length: largest of 1, 2 or 5 times a power of ten that is at most a quarter of the frame width | A quarter keeps the bar small. A round length can be read against a scale rule | 7.9.4 |
| H-16 | Minimum frame sizes: 46 mm high with a scale bar, 38 mm in both directions with a north arrow | The bar and the arrow must fit and must not touch | 7.9.10, 7.10.7 |
| H-17 | `model_bearing_deg` is the compass bearing of the model +y axis | Matches the tool's tested output. The old README worded it the opposite way | 7.10.1 |
| H-18 | North arrow: 18 mm tall, inset 10 mm, head and shaft proportions as in 7.10.5 | ISO sets no size for a north arrow | 7.10.5 |
