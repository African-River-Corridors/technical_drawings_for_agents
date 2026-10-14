# Drawing Format Standard

Version: 1.0 (draft, written under spec 0001, see `docs/specs/0001-drawing-format-standard.md`).

This is the standard for the **drawing document**: one JSON file that describes one whole sheet.
Any tool can read it. Any tool that follows this standard gives the same drawing from the same file.

## 0 How to read this standard

**0.1** Every rule has a number such as `7.4.2`. Tests, error messages and code cite these numbers.
A number never changes meaning inside one version of the standard.

**0.2** Rules marked **Basis** say where a value comes from. There are two kinds of basis.

- *Pending ISO check.* The value is taken from an ISO part, but the text of that part was not at
  hand when this clause was written. The value is a proposal. It is listed in
  [`iso-checklist.md`](iso-checklist.md) with a number `P-nn`. Until the checklist item is closed,
  nobody may call the value "from ISO".
- *House choice.* ISO does not set the value, or this standard chooses a simpler rule. The reason is
  given. The reason is listed in the checklist with a number `H-nn`.

**0.3** This standard does not quote ISO text. It states its own rules in its own words.

**0.4** Clauses 1 to 8 are complete in this draft. Clause 11 is partly written. Clauses 9, 10 and 12 to 18 are headings only. Each heading
names the ticket of spec 0001 that writes it.

## 1 Scope

**1.1** This standard defines the drawing document, its units and precision, the sheet, the
viewports and their scales, and (in later clauses) layers, lines, geometry, text, dimensions,
symbols, sheet furniture, the outputs, and the normalised content that two implementations must
agree on.

**1.2** This standard does not define what a drawing of a given kind must show. It does not define
geographic backdrops, build graphs, or any private title block.

## 2 Conformance and language

**2.1** The words MUST, MUST NOT, SHOULD and MAY have their usual standards meaning. MUST is a
requirement. SHOULD is a recommendation. MAY is a permission.

**2.2** A **conforming implementation** reads a drawing document, checks it, and produces the
**normalised content** of clause 16. It conforms when it gives the expected normalised content, or
the expected error, for every case of the conformance suite (clause 17).

**2.3** SVG, PDF and DXF bytes MAY differ between implementations. Only the normalised content and
the findings are compared.

## 3 Terms

**3.1 Drawing document.** One JSON file that describes one sheet and follows this standard.

**3.2 Model space.** The space where the real object is described. The unit is the metre.

**3.3 Paper space.** The space of the printed sheet. The unit is the millimetre.

**3.4 Trimmed sheet.** The sheet after it is cut to its final size. All sheet sizes in this standard
are trimmed sizes.

**3.5 Drawing space.** The part of the trimmed sheet inside the border.

**3.6 Viewport.** A rectangle of the drawing space that shows a window of model space at one scale.

**3.7 Finding.** A message from a check. A finding is an **error** or a **warning** (clause 4.6).

## 4 The drawing document

**4.1** A drawing document is one JSON text in UTF-8. The root is an object.

**4.2** The root object has these members. The JSON Schema of this standard (clause 18) is the
final word on member names. This clause says what each member means.

| Member | Meaning | Clause |
|---|---|---|
| `standard_version` | The version of this standard that the file follows. | 18 |
| `sheet` | Size, orientation and line group. | 6, 8 |
| `title_block` | The title block template and its field values. | 14 |
| `revisions` | The revision register. | 14 |
| `status` | The drawing status: draft, concept or issued. | 14 |
| `layers` | The layers in use. | 8 |
| `symbol_libraries` | References to symbol libraries. | 13 |
| `viewports` | One or more viewports, each with its entities. | 7, 9 to 13 |
| `notes`, `references` | The notes table and the references table. | 14 |

**4.3** A reader MUST reject a root with a member that this standard does not define. This applies
to every object in the document. A mistyped key is an error. It is never ignored.
*Basis: house choice H-01. A key that is silently ignored can leave a drawing at a different scale
from the one its author wrote.*

**4.4** A reader MUST reject a document that holds a repeated key in one object.

**4.5** Every number in a document is a finite JSON number. The unit of a number comes from the end
of its member name:

| Ending | Unit |
|---|---|
| `_m` | metres in model space |
| `_mm` | millimetres on paper |
| `_deg` | degrees |

A number whose member name has none of these endings has no unit. Examples are counts and the scale
denominator. The schema states which members are whole numbers.
*Basis: house choice H-02. The rule keeps the two length units visible at every number.*

**4.6** A check gives **findings**. An **error** stops the build: no output is written. A
**warning** is reported and the build goes on. Every finding carries the number of the clause it
enforces. Example: error `7.4.3`.
*Basis: house choice H-03. A clause number lets tests and users find the rule.*

**4.7** Array order is kept as written. Order does not change the normalised content, because
clause 16 sorts it. The only exception is any array that this standard says is ordered.

## 5 Units and precision

**5.1 Model space.** Model coordinates are in metres. The axes are right-handed: +x to the right,
+y up. Version 1.0 draws in two dimensions. There is no z.

**5.2 Paper space.** Paper coordinates are in millimetres. The origin is the top-left corner of the
trimmed sheet. +x runs to the right. +y runs **down**.
*Basis: house choice H-04. SVG and PDF viewers use the same sense, so no output step flips a sign.
Only the map from model to paper (clause 7.4) turns +y up into +y down.*

**5.3 Angles.** Angles are in degrees.

- A model angle is measured counter-clockwise from +x, with +y up.
- A rotation of a viewport is positive counter-clockwise (clause 7.4).
- A compass bearing and a paper direction are measured clockwise (clause 7.10).

**5.4 Precision.** Each quantity has a fixed number of decimal places. A value is rounded to that
number of places once, at the end, as clause 5.5 says. Never round a value that will be used in a
later calculation.

| Quantity | Decimal places | Smallest step |
|---|---|---|
| Model length, in metres | 6 | 1 micrometre |
| Paper length, in millimetres | 3 | 1 micrometre |
| Angle, in degrees | 3 | 0.001 degree |
| Scale denominator | 0 (whole number) | 1 |

*Basis: house choice H-05. One micrometre is far below any drawing need and far above the
rounding noise of 64-bit arithmetic.*

**5.5 Rounding.** Rounding uses the **half-even** rule, applied to the exact value of the number:

1. Take the exact decimal value of the 64-bit binary number. Do not first convert it to text.
2. Find the two numbers with the required decimal places that are on each side of it.
3. Pick the nearer one.
4. If the value is exactly half way between them, pick the one whose last digit is even.
5. If the result is a negative zero, write it as zero.

*Basis: house choice H-06. One rule that is the same in every language.*

**5.5.1** Worked numbers. The first five rows are exact ties in 64-bit binary. The last three rows
show why step 1 says "exact value".

| Value | Places | Result | Why |
|---|---|---|---|
| 0.0625 | 3 | 0.062 | half way between 0.062 and 0.063; 2 is even |
| 0.1875 | 3 | 0.188 | half way between 0.187 and 0.188; 8 is even |
| -0.0625 | 3 | -0.062 | the sign does not change the rule |
| 2.5 | 0 | 2 | half way between 2 and 3 |
| 3.5 | 0 | 4 | half way between 3 and 4 |
| -0.0004 | 3 | 0 | the result is negative zero, so it is written as 0 |
| 1.0005 | 3 | 1.000 | the 64-bit number nearest 1.0005 is a little below it, so it is not a tie |
| 0.0005 | 3 | 0.001 | the 64-bit number nearest 0.0005 is a little above it, so it is not a tie |

**5.6 Arithmetic.** All arithmetic is in 64-bit binary floating point (IEEE 754 binary64). Two
implementations MAY differ in the last bit of a result. The conformance suite is built so that this
never changes a rounded value (clause 17).

**5.6.1** The sine and cosine of a multiple of 90 degrees MUST be exact: 0, 1 or -1. An
implementation MUST NOT use a library value such as 6.1e-17 for the cosine of 90 degrees.

**5.7 Fit tolerance.** A test that asks "does this fit" accepts a value that is larger than the limit
by not more than **0.000001 mm** (1e-6 mm). No other comparison in this standard has a tolerance.
*Basis: house choice H-07. The tolerance absorbs binary rounding and nothing more. A larger one
would hide a real overflow.*

## 6 Sheets

**6.1 Sizes.** A sheet has one of five trimmed sizes. A sheet of any other size is an error (6.1.3).

| Size | Short edge, mm | Long edge, mm |
|---|---|---|
| A0 | 841 | 1189 |
| A1 | 594 | 841 |
| A2 | 420 | 594 |
| A3 | 297 | 420 |
| A4 | 210 | 297 |

*Basis: pending ISO check, ISO 5457 (clause to be confirmed), checklist P-01.*

**6.1.1** Only trimmed sizes are used. The standard has no rule for untrimmed sheets.

**6.1.2** The standard does not define sizes larger than A0 or smaller than A4.

**6.1.3** An unsupported size is error `6.1.3`.

**6.2 Orientation.** The orientation is `landscape` or `portrait`. In landscape the long edge is
horizontal. In portrait the short edge is horizontal. The default is `landscape`. Either
orientation is allowed for every size.
*Basis: house choice H-08. The standard sets no rule that ties a size to one orientation.*

**6.2.1** Any other orientation value is error `6.2.1`.

**6.3 Border and drawing space.** The **border** is a rectangle inset from the trimmed edge. The
space inside it is the drawing space. The insets are:

| Size | Left (filing margin) | Top | Right | Bottom |
|---|---|---|---|---|
| A0 | 20 mm | 20 mm | 20 mm | 20 mm |
| A1 | 20 mm | 20 mm | 20 mm | 20 mm |
| A2 | 20 mm | 10 mm | 10 mm | 10 mm |
| A3 | 20 mm | 10 mm | 10 mm | 10 mm |
| A4 | 20 mm | 10 mm | 10 mm | 10 mm |

*Basis: pending ISO check, ISO 5457 (clause to be confirmed), checklist P-02.*

**6.3.1** "Left" is the left edge as the sheet is viewed. It does not change with orientation. The
insets are the same in portrait and in landscape.

**6.3.2** Worked numbers. The drawing space is the trimmed sheet less the insets.

| Sheet | Drawing space, width x height | Left x, top y of drawing space |
|---|---|---|
| A3 landscape | 390 x 277 mm | 20, 10 |
| A3 portrait | 267 x 400 mm | 20, 10 |
| A4 portrait | 180 x 277 mm | 20, 10 |
| A0 landscape | 1149 x 801 mm | 20, 20 |

**6.3.3** The border line is a continuous line of the extra thick role (clause 8). It is centred on
the border rectangle. Its width is never less than 0.7 mm.
*Basis for the minimum width: pending ISO check, ISO 5457 (clause to be confirmed), checklist P-03.
Basis for centring: house choice H-09. It is the simplest rule, and the line is at most 1.4 mm wide.*

**6.4 Centring marks.** There are four centring marks. One is at the middle of each side of the
trimmed sheet. Each mark is a straight line 0.5 mm wide. It runs at a right angle to its side, from
the trimmed edge to a point 5 mm inside the drawing space.
*Basis: pending ISO check, ISO 5457 (clause to be confirmed), checklist P-04. The width, the
position and the length of the mark are all unconfirmed.*

**6.5 Grid reference.** Each side of the drawing space is divided into equal **zones**. The number
of zones along the long side and along the short side comes from the size:

| Size | Zones along the long side | Zones along the short side |
|---|---|---|
| A0 | 24 | 16 |
| A1 | 16 | 12 |
| A2 | 12 | 8 |
| A3 | 8 | 6 |
| A4 | 6 | 4 |

*Basis: pending ISO check, ISO 5457 (clause to be confirmed), checklist P-05.*

**6.5.1** A zone is one equal part of the border side. Its length is the side length divided by the
number of zones. In landscape, the long side is horizontal. In portrait, the counts swap.

**6.5.2** Zones along the horizontal sides are numbered 1, 2, 3 and so on, from left to right.
Zones along the vertical sides are lettered from top to bottom: A, B, C, D, E, F, G, H, J, K, L, M,
N, P, Q, R. The letters I and O are never used, because they look like 1 and 0.
*Basis: pending ISO check, ISO 5457 (clause to be confirmed), checklist P-06.*

**6.5.3** A **zone mark** is a straight line, 0.35 mm wide, that crosses the margin strip between
the border and the trimmed edge, at each boundary between two zones. A zone **label** is the number
or the letter of the zone. It is 3.5 mm high. It is centred on the zone, and centred across the
margin strip. Labels appear on both horizontal sides and on both vertical sides.
*Basis: pending ISO check. The mark width and the label place are from ISO 5457 (clause to be
confirmed), checklist P-06. The text height is a standard ISO 3098 height (clause to be confirmed),
checklist P-08.*

**6.5.4** Worked numbers. A3 landscape has a drawing space 390 mm wide, 8 zones across. Each zone is
48.75 mm wide. The boundaries are at x = 20, 68.75, 117.5, 166.25, 215, 263.75, 312.5, 361.25 and 410.
It has 6 zones down a height of 277 mm. Each zone is 46.1666... mm, which rounds to 46.167 mm (clause
5.5). The label of the first zone down is `A`. The label of the sixth is `F`.

**6.6 Reserved regions.** A drawing document MAY declare **reserved regions** (clause 14). A reserved
region is a named rectangle inside the drawing space, with its position and size in paper
millimetres. The title block, the notes table and the references table are reserved regions.

**6.6.1** The drawing space minus the reserved regions is the space where viewports may sit
(clause 7.2).

## 7 Viewports and scales

**7.1 What a viewport is.**

**7.1.1** A viewport is a rectangle on the paper (its **frame**) that shows a
window of model space at one scale. A sheet has one or more viewports. A sheet with none is error
`7.1.1`.

A viewport has these members. The schema is final for names.

| Member | Meaning |
|---|---|
| `frame_mm` | Left x, top y, width and height of the frame on paper, in millimetres. |
| `window_m` | The model window: xmin, ymin, xmax, ymax, in metres. |
| `scale` | A preferred ratio, or `fit` (clause 7.3 and 7.5). |
| `rotation_deg` | How far the model turns on the paper, in degrees (clause 7.4). Default 0. |
| `scale_bar` | An optional scale bar (clause 7.9). |
| `north` | An optional north arrow (clause 7.10). |

**7.2 Placement.**

**7.2.1** The width and the height of a frame MUST be more than zero. Error `7.2.1`.

**7.2.2** A frame MUST lie inside the drawing space (clause 6.3). Error `7.2.2`.

**7.2.3** Two frames MUST NOT overlap. Frames that only touch along an edge do not overlap. Error
`7.2.3`.

**7.2.4** A frame MUST NOT overlap a reserved region (clause 6.6). Error `7.2.4`.

**7.2.5** The tolerance of clause 5.7 applies to the tests in 7.2.2 to 7.2.4.

**7.3 Scale.** A scale is a ratio 1:N. N is a whole number of 1 or more. One metre of model space
is 1000 / N millimetres on paper. The value 1000 / N is called `mm_per_m`.

**7.3.1** The **preferred ratios** are those where N is one of:

> 1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000, 50000, 100000

*Basis: pending ISO check, ISO 5455 (clause to be confirmed), checklist P-07. ISO 5455 may have
been replaced by a later part. The check must find the part that now holds the list of scales. The
extension up to 1:100000 follows the rule that the list may be extended by powers of ten.*

**7.3.2** A scale that is neither a preferred ratio nor `fit` is error `7.3.2`. This includes
1:25, 1:250 and 1:1250. Enlargement ratios such as 2:1 are not part of version 1.0.
*Basis: house choice H-10. The standard has one list of scales, so every scale on a sheet can be set
on a scale rule.*

**7.3.3** A viewport has one scale. Both axes use it. A view with exaggerated height is not
possible in version 1.0.
*Basis: house choice H-11. A wrong exaggeration is worse than none.*

**7.4 The map from model to paper.** Let the window be `xmin, ymin, xmax, ymax`, the frame be
`x, y, w, h`, the scale denominator be N and the rotation be theta.

1. The model centre is `cx = (xmin + xmax) / 2` and `cy = (ymin + ymax) / 2`.
2. The frame centre is `fx = x + w / 2` and `fy = y + h / 2`.
3. For a model point `(px, py)`, let `dx = px - cx` and `dy = py - cy`.
4. Turn the offset by theta, counter-clockwise, with +y up:
   `rx = dx cos(theta) - dy sin(theta)` and `ry = dx sin(theta) + dy cos(theta)`.
5. The paper point is `(fx + rx * k, fy - ry * k)`, where `k = 1000 / N`.

**7.4.1** The map acts on coordinates only. A line width, a dash length and a text height are
paper values. They are never multiplied by `k`.
*Basis: house choice H-12. A scale applied to the whole drawing would also scale every line width
by 1/N.*

**7.4.2** A rotation is taken modulo 360 degrees. A rotation of -10 degrees is 350 degrees.

**7.4.3** Worked numbers. Frame `20, 10, 390, 277` (centre 215, 148.5). Window `0, 0, 100, 50`
(centre 50, 25). Scale 1:500, so `k = 2`.

| Model point | Rotation | Offset (dx, dy) | Paper point |
|---|---|---|---|
| (100, 50) | 0 | (50, 25) | (315, 98.5) |
| (100, 50) | 90 | (50, 25) turns to (-25, 50) | (165, 48.5) |
| (50, 25) | any | (0, 0) | (215, 148.5) |
| (0, 0) | 0 | (-50, -25) | (115, 198.5) |

**7.4.4** Model +y maps to paper "up" (-y) when the rotation is 0. When the rotation is 90 degrees,
model +y maps to paper left.

**7.5 Fit.**

**7.5.1** The window MUST fit the frame at the scale. The window is turned by theta. Its
bounding box has the paper size:

- width: `(|w_m cos(theta)| + |h_m sin(theta)|) * k`
- height: `(|w_m sin(theta)| + |h_m cos(theta)|) * k`

where `w_m` and `h_m` are the window width and height in metres. The window **fits** when both
numbers are not larger than the frame width and height (clause 5.7). If it does not fit, that is
error `7.5.1`. The message MUST name the largest preferred scale that does fit, if there is one.

**7.5.2** Declared fit. When `scale` is `fit`, the scale is the first preferred ratio in the list
of clause 7.3.1, from 1:1 upward, at which the window fits. The first ratio is the largest scale.
If no preferred ratio fits, that is error `7.5.2`. The resolved ratio, not the word `fit`, is the
scale of the viewport in every later rule and every output.

**7.5.3** Worked numbers. Frame 390 x 277 mm.

| Window (w_m x h_m) | Rotation | Scale | Paper size needed | Result |
|---|---|---|---|---|
| 200 x 100 | 0 | 1:500 | 400 x 200 mm | error 7.5.1; the message names 1:1000 |
| 200 x 100 | 0 | 1:1000 | 200 x 100 mm | fits |
| 200 x 100 | 0 | fit | | 1:1000 |
| 100 x 300 | 90 | 1:500 | 600 x 200 mm | error 7.5.1 |
| 100 x 300 | 90 | fit | 300 x 100 mm at 1:1000 | 1:1000 |
| 390 x 277 | 0 | 1:1000 | 390 x 277 mm | fits exactly |

**7.6 Window.**

**7.6.1** `xmax` MUST be more than `xmin`. `ymax` MUST be more than `ymin`. Error `7.6.1`.

**7.7 Entities stay in the frame.**

**7.7.1** After the map of clause 7.4, every point of every entity of a
viewport MUST lie inside the frame (clause 5.7). The bounding box of a text entity is given by
clause 11. Error `7.7.1`.
*Basis: house choice H-13. A stray point is a loud error. It is never clipped without a word.*

**7.8 Scale and the sheet.** The viewport scale is the only scale a rule refers to. The title block
scale field is derived from the viewport scales (clause 14). It is never typed.

**7.9 Scale bar.** A viewport MAY have a scale bar. The bar is derived from the scale. Its length,
its ticks, its labels and its place are never typed. The only inputs are `length_m`, `divisions` and
`unit`, all optional.

**7.9.1 Constants.** All values are paper millimetres.

| Name | Value | Use |
|---|---|---|
| inset | 6 | distance from the frame edge to the bar block |
| bar height | 3 | height of the bar |
| tick length | 1.5 | the tick below the bar |
| tick label baseline | 5.0 | below the bottom of the bar |
| ratio baseline | 9.0 | below the bottom of the bar |
| text height | 2.5 | the tick labels and the ratio text |

*Basis: house choice H-14 for every row. ISO does not set the size of a scale bar. The text height
is a standard ISO 3098 height, pending ISO check, checklist P-08. The baselines leave 1 mm between
the tick and the label, and 1.5 mm between the two lines of text.*

**7.9.2 Place.** The bar is fixed to the **frame**, never to a model point. Its left end is at
`x + 6`. The bottom of the bar is `15` mm above the bottom of the frame. The top of the bar is
therefore 18 mm above the bottom of the frame. The ratio baseline is 6 mm above the bottom of the
frame.

**7.9.3 Available width.** The paper length of the bar MUST NOT be more than the frame width less
twice the inset: `available = w - 12`. A bar that touches the border has no clearance and cannot be
printed as drawn. If the bar is longer, that is error `7.9.3`. The message MUST give both the
required and the available length in millimetres.

**7.9.4 Length.** If `length_m` is given, the bar has that length, and the rule of 7.9.3 applies. If
it is not given, the length is the largest number in the series `{1, 2, 5} x 10^n` metres, with n a
whole number from -3 to 6, that meets both of these:

1. It is not more than the **target**. The target is the model length that is a quarter of the frame
   width on paper: `target = (w / 4) / k` metres.
2. Its paper length meets clause 7.9.3.

If no number meets both, that is error `7.9.4`.
*Basis: house choice H-15. A quarter of the frame keeps the bar small. The series 1, 2, 5 gives a
round length that can be read against a scale rule.*

**7.9.5 Division.** `divisions` is a whole number of 1 or more. The default is 5. The bar is split
into that many segments of equal paper length. The bar outline is a rectangle with the full
length and the bar height. A division line crosses the bar at each inner boundary. Segments 0, 2, 4
and so on (counting from the left) are solid black. The others are not filled.
*Basis: house choice H-14. Solid and empty segments read in black print.*

**7.9.6 Ticks and labels.** A tick is at every boundary, including both ends. A tick runs down from
the bottom of the bar by the tick length. Tick `i` runs from 0 to `divisions`. Its label is centred
on the tick, on the tick label baseline. The label text is the value `length * i / divisions`.

**7.9.7 Label text.** The value is in the bar unit (`m`, the default, or `km`). For `km`, divide the
value by 1000. Round it to 6 decimal places (clause 5.5). Write it with no trailing zeros and no
trailing decimal point. The last label has the unit after it, with one space. Below the bar, centred
on it, on the ratio baseline, the text is the scale, written `1:N`.

**7.9.8 The bar does not turn.** The rotation of the viewport never changes the bar. A map has one
scale in every direction.

**7.9.9 Worked numbers.** Frame width 390 mm. The available width is 378 mm.

| Scale | Target | Derived length | Bar on paper | Segments (5) | Labels |
|---|---|---|---|---|---|
| 1:100 | 9.75 m | 5 m | 50 mm | 10 mm | 0, 1, 2, 3, 4, 5 m |
| 1:200 | 19.5 m | 10 m | 50 mm | 10 mm | 0, 2, 4, 6, 8, 10 m |
| 1:500 | 48.75 m | 20 m | 40 mm | 8 mm | 0, 4, 8, 12, 16, 20 m |
| 1:1000 | 97.5 m | 50 m | 50 mm | 10 mm | 0, 10, 20, 30, 40, 50 m |
| 1:2000 | 195 m | 100 m | 50 mm | 10 mm | 0, 20, 40, 60, 80, 100 m |

The `m` follows the last label only. For a 2 m bar at 5 divisions the labels are 0, 0.4, 0.8, 1.2,
1.6 and 2 m. For a bar of 2000 m in km at 4 divisions they are 0, 0.5, 1, 1.5 and 2 km.

With `length_m` given, at 1:1000 in a 390 mm frame: 378 m needs 378 mm, which fits exactly. 379 m
needs 379 mm and is error 7.9.3. 390 m needs 390 mm and is error 7.9.3. The error for 390 m states
"required 390 mm, available 378 mm".

**7.9.10** A viewport that has a scale bar MUST have a frame at least 46 mm high (so that the bar and
the north arrow of clause 7.10 do not touch), and wide enough for clause 7.9.3. Error `7.9.10`.
*Basis: house choice H-16.*

**7.10 North arrow.** A viewport MAY have a north arrow. The arrow direction is derived. It is never
typed. The inputs are `model_bearing_deg` (default 0) and `label` (default `N`).

**7.10.1 The bearing input.** `model_bearing_deg` is the compass bearing, measured clockwise from
north, of the model +y axis. When it is 0, model +y points north.
*Basis: house choice H-17. The README of the earlier tool worded this the opposite way round. The
tool's own tests and its output follow the rule given here.*

**7.10.2 Direction.** The paper direction of north, measured clockwise from paper "up" (the
direction of -y), is:

`paper_deg = (-rotation_deg - model_bearing_deg) mod 360`

The result is in the range 0 up to, but not including, 360. It is rounded as clause 5.4 says.

**7.10.3** The standard does not turn grid north into true north. A projected grid can differ from
true north by a few degrees. The input `model_bearing_deg` states which north is meant.

**7.10.4 Worked numbers.**

| rotation_deg | model_bearing_deg | paper_deg | North points |
|---|---|---|---|
| 0 | 0 | 0 | up |
| 90 | 0 | 270 | left |
| 90 | 90 | 180 | down |
| 40.25 | 0 | 319.75 | up and a little left |
| 0 | 12.5 | 347.5 | up and a little left |
| -10 | 0 | 10 | up and a little right |

The row for 90 degrees agrees with clause 7.4.4. The viewport puts model +y at paper left, and the
arrow points left.

**7.10.5 Size and place.** The arrow is 18 mm tall. Its centre `(cx, cy)` is at `x + 10 + 9` and
`y + 10 + 9`, where `x, y` is the top-left corner of the frame. Drawn with north at the top, the
parts are:

- the **head**: a closed polygon with four points: the tip at `(cx, cy - 9)`, then
  `(cx - 4.14, cy + 5.76)`, then `(cx, cy + 0.9)`, then `(cx + 4.14, cy + 5.76)`;
- the **shaft**: a line from `(cx, cy + 0.9)` to `(cx, cy + 9)`;
- the **label**: text 3.5 mm high, centred at `(cx, cy - 11.5)` on its baseline.

Then the head, the shaft and the label position all turn clockwise by `paper_deg` about `(cx, cy)`.
The label text itself does not turn. It stays upright.
*Basis: house choice H-18 for the size, the inset and the proportions (the head width is 0.23 of the
height each side, the head base is 0.32 of the height below the centre, and the inner point is 0.05
of the height below it). The label height is a standard ISO 3098 height, pending ISO check,
checklist P-08.*

**7.10.6** Worked numbers. Frame `20, 10, 390, 277`. The centre is `(39, 29)`. With `paper_deg = 0` the
tip is at `(39, 20)`. With `paper_deg = 90` the tip turns to `(48, 29)`. With `paper_deg = 270` the
tip turns to `(30, 29)`.

**7.10.7** The frame MUST be at least 38 mm wide and 38 mm high, so that the arrow and its inset
fit. Error `7.10.7`.
*Basis: house choice H-16.*

**7.11** In version 1.0 the standard does not check whether the scale bar or the north arrow
overlaps the entities of the viewport. The author places the window so that they are clear.

## 8 Layers, line types, line groups and colour

The machine-readable form of this clause is `docs/standard/data/line-styles.json`. The text and the
file MUST agree. A test checks it.

**8.1 Line roles.** A line has one of three **roles**: `extra_thick`, `thick` or `thin`. A role is a
relative weight. It is not a width. The sheet turns a role into a width (clause 8.2).

**8.1.1** In one line group, the thick width is twice the thin width, and the extra thick width is
twice the thick width, each taken to the nearest value of the width series. The widths are given in
the table in clause 8.2. They are not computed.
*Basis: pending ISO check, ISO 128 (clause to be confirmed), checklist P-10.*

**8.2 Line groups.** A **line group** is a set of three widths, one for each role. The group is
named by its thick width. The sheet size sets the group. All widths are paper millimetres.

| Line group | Sheet sizes | Thin | Thick | Extra thick |
|---|---|---|---|---|
| 0.35 | A4, A3 | 0.18 | 0.35 | 0.7 |
| 0.5 | A2, A1 | 0.25 | 0.5 | 1.0 |
| 0.7 | A0 | 0.35 | 0.7 | 1.4 |

*Basis: the widths are from the ISO 128 series of line widths, pending ISO check (clause to be
confirmed), checklist P-09. The choice of group by sheet size is pending ISO check (clause to be
confirmed), checklist P-11.*

**8.2.1** The `sheet` object MAY name the line group in the member `line_group`. The value is the
group name: `0.35`, `0.5` or `0.7`. If it is missing, the group is the one in the table for the sheet
size.

**8.2.2** A `line_group` that is not the group of the sheet size is error `8.2.2`.
*Basis: house choice H-19. One rule gives one set of widths for one size. It keeps drawings on the
same size alike.*

**8.2.3** Worked numbers. Half of 0.35 is 0.175, which is not in the width series. The thin width
of the group is therefore the table value 0.18. This is why the table is used and the widths are not
computed.

**8.3 Line widths.** The width of a line is the width of its role in the line group of the sheet.
It is in paper millimetres. It is never scaled (clause 7.4.1).

**8.3.1 CAD line widths.** A CAD file can hold only a fixed set of line widths. The set, in
millimetres, is:

> 0.05, 0.09, 0.13, 0.15, 0.18, 0.20, 0.25, 0.30, 0.35, 0.40, 0.50, 0.53, 0.60, 0.70, 0.80, 0.90,
> 1.00, 1.06, 1.20, 1.40, 1.58, 2.00, 2.11

Every width in the table of clause 8.2 is in the set.
*Basis: house choice H-20. This is the set of line widths that the DXF format can store. It is a
format rule, not an ISO value.*

**8.3.2 Snap.** A width that does not come from the table of clause 8.2 (for example a width in a
symbol library) is **snapped** to the set of clause 8.3.1 before it enters the normalised content.
The rule is:

1. Round the width to 3 decimal places (clause 5.5). Write it as a whole number of micrometres.
2. If the result is 0, or more than 2110, that is error `8.3.2`.
3. Pick the member of the set that is nearest. Compare whole micrometres.
4. If two members are equally near, pick the **thinner** one.

*Basis: house choice H-20. The comparison is on whole numbers, so every implementation picks the
same member. A comparison on binary fractions can pick a different member at a tie.*

**8.3.3** Worked numbers.

| Width, mm | Micrometres | Nearest members | Result, mm | Why |
|---|---|---|---|---|
| 0.36 | 360 | 350 (10 away), 400 (40 away) | 0.35 | nearest |
| 0.12 | 120 | 90 (30 away), 130 (10 away) | 0.13 | nearest |
| 0.175 | 175 | 150 (25 away), 180 (5 away) | 0.18 | nearest |
| 0.55 | 550 | 530 (20 away), 600 (50 away) | 0.53 | nearest |
| 0.165 | 165 | 150 and 180, each 15 away | 0.15 | tie, so the thinner |
| 0.275 | 275 | 250 and 300, each 25 away | 0.25 | tie, so the thinner |
| 0.45 | 450 | 400 and 500, each 50 away | 0.40 | tie, so the thinner |
| 0.7 | 700 | 700 (0 away) | 0.7 | already in the set |
| 0.02 | 20 | 50 (30 away) | 0.05 | nearest |
| 2.2 | 2200 | none | error 8.3.2 | more than 2110 |

The width 0.275 mm is a tie that binary arithmetic can get wrong: 0.275 times 100 is
27.500000000000004 in 64-bit binary, which is nearer to 30 than to 25. Rule step 1 prevents this.

**8.3.4** A width in a CAD file is stored in hundredths of a millimetre. The width 0.35 mm is 35.

**8.4 Line types.** There are four line types. Each has an identifier, an ISO 128 type number, and
a pattern of dashes and gaps. A pattern is a list of numbers, in units of `d`, the width of the line
(clause 8.3). A positive number is a dash that is drawn. A negative number is a gap. The pattern
repeats along the line.

| Identifier | ISO 128 type | Pattern, in units of d |
|---|---|---|
| `continuous` | 01.1 | no pattern |
| `dashed` | 02.1 | 12, -3 |
| `chain` | 04.1 | 24, -3, 0.5, -3 |
| `chain_double_dashed` | 05.1 | 24, -3, 0.5, -3, 0.5, -3 |

*Basis: pending ISO check, ISO 128 (clause to be confirmed), checklist P-12.*

**8.4.1** The **length in millimetres** of one element is the number in the table times `d`, rounded
to 3 decimal places (clause 5.5). The pattern depends on the width of the line, so thin lines have
shorter dashes than thick lines.

**8.4.2** A pattern starts with a dash at the first point of the line. It does not change when the
sheet is scaled, because it is in paper millimetres (clause 7.4.1).

**8.4.3** The normalised content (clause 16) holds the identifier of the line type. It does not hold
the pattern. The pattern is a rule for the output step (clause 15).

**8.4.4** Worked numbers. All lengths are paper millimetres.

| Line type | d = 0.18 | d = 0.35 | d = 0.5 |
|---|---|---|---|
| `dashed` | 2.16, -0.54 | 4.2, -1.05 | 6, -1.5 |
| `chain` | 4.32, -0.54, 0.09, -0.54 | 8.4, -1.05, 0.175, -1.05 | 12, -1.5, 0.25, -1.5 |
| `chain_double_dashed` | 4.32, -0.54, 0.09, -0.54, 0.09, -0.54 | 8.4, -1.05, 0.175, -1.05, 0.175, -1.05 | 12, -1.5, 0.25, -1.5, 0.25, -1.5 |

**8.5 Layers.** Every entity is on one layer. A layer has a name, a role, a line type, a CAD colour
number and a use.

**8.5.1 Declaration.** The document lists the layers it uses in the member `layers`. An entity that
names a layer that is not listed is error `8.5.1`. The standard never creates a layer without a
declaration.
*Basis: house choice H-21. A drawing with a layer that nobody declared hides a mistake.*

**8.5.2 Standard layers.** A document declares a standard layer by its name alone. The role, the
line type and the colour then come from this table.

| Layer | Role | Line type | CAD colour | Use |
|---|---|---|---|---|
| OUTLINE | thick | continuous | 7 | Primary section or plan outline |
| OBJECT | thin | continuous | 8 | Secondary object lines |
| HIDDEN | thin | dashed | 244 | Edges hidden in this view |
| CENTRE | thin | chain | 6 | Centre lines and axes of symmetry |
| DIMENSIONS | thin | continuous | 3 | Dimension, extension and projection lines |
| ANNO | thin | continuous | 174 | Leaders, tags and callouts (not text) |
| TEXT | thin | continuous | 30 | Annotation text |
| HATCH | thin | continuous | 9 | Hatch and fill line work |
| GRID | thin | chain | 194 | Setting-out grid |
| TITLEBLOCK | thick | continuous | 54 | Border, title block, revision register and other sheet furniture |
| WATERMARK | thick | continuous | 1 | Status watermark |
| FDN | thick | continuous | 34 | Foundations, slabs and plinths |
| EQUIP | thick | continuous | 5 | Equipment outlines |
| NOZZLE | thin | continuous | 4 | Nozzles and connection points |
| PIPE | extra thick | continuous | 150 | Pipework |
| ELEC | thin | continuous | 214 | Electrical equipment and cable routes |
| WATER | thin | continuous | 134 | Water bodies, ponds and channels |
| ZONE | thin | dashed | 70 | Keep-clear and access zones (not physical) |
| ACCESS | thin | dashed | 94 | Access routes and hardstanding edges |

*Basis: house choice H-22. ISO sets no layer names. The names are those of the reference tool. The
roles follow clause 8.1. The line types follow clause 8.4.*

**8.5.3 Redeclaration.** A document that repeats a standard layer name and gives a role, a line type
or a colour that differs from the table is error `8.5.3`.

**8.5.4 Custom layers.** A document MAY declare a layer that is not in the table. It gives the name,
the role, the line type and the CAD colour number. The name MUST start with a capital letter A to Z
and MUST hold only capital letters A to Z, digits and the underscore, up to 31 characters in all.
Any other name is error `8.5.4`.
*Basis: house choice H-23. The rule keeps names valid in every CAD program, and the same in every
case.*

**8.5.5 Duplicates.** Two declarations of one layer name in one document are error `8.5.5`, even
when they are alike.

**8.5.6 Entity overrides.** An entity MAY name a role, or a line type, or both. The name then
replaces the one of its layer for that entity. This is how the border of the sheet is extra thick on
a layer whose role is thick. The entity never names a width.
*Basis: house choice H-24. Layers name a role, not a width. A role override keeps that rule.*

**8.6 Colour.**

**8.6.1 Print colour.** The SVG and the PDF are drawn in black (RGB 0, 0, 0), on the unprinted
white of the paper. There are no grey or coloured lines, text or fills in them. Fills, where this
standard asks for a solid fill, are black.
*Basis: house choice H-25. An ISO drawing is black on white. Layer colour is a CAD aid and not a
print colour.*

**8.6.2 CAD colour.** Each layer has one CAD colour number, in the range 1 to 255. The table of
clause 8.5.2 gives it. In a DXF, an entity takes the colour of its layer. No two standard layers
have the same number. The number 7 is shown by CAD programs as black or white, whichever is seen
against the background.
*Basis: house choice H-26. The numbers are 19 different numbers chosen so that the layers differ in
the common CAD palette. The standard makes no other claim about the hue.*

**8.6.3** A custom layer MUST NOT have the same colour number as any other layer declared in the
same document. Error `8.6.3`.

**8.7 What an entity gets.** For an entity on a declared layer, in this order:

1. The role is the role of the entity if it has one, else the role of its layer.
2. The line type is the line type of the entity if it has one, else the line type of its layer.
3. The width is the width of the role in the line group of the sheet (clause 8.2), snapped if it is
   not from the table (clause 8.3.2).
4. A text entity has no line width. A hatch entity is described in clause 10.

**8.7.1** Worked numbers. On an A3 sheet (group 0.35), a line on `OUTLINE` has width 0.35 and line
type `continuous`. A line on `HIDDEN` has width 0.18 and line type `dashed`, so its dash is 2.16 mm
and its gap is 0.54 mm. A line on `PIPE` has width 0.7. On an A0 sheet (group 0.7) the same three
lines have widths 0.7, 0.35 and 1.4. The sheet border (extra thick, clause 6.3.3) has width 0.7 on A3
and 1.4 on A0.

## 9 Geometry

Written in the schema and conformance tickets (#9, #10 and later).

## 10 Hatches

Written in a later ticket.

## 11 Lettering and font

This clause is **partly written**. The font, its width table and the text box (clauses 11.6 and
11.7) wait for the licence check of ticket #8. See clause 11.6.

**11.1 Style.** Text is drawn in upright ISO 3098 lettering of type B. In type B the line thickness
of a letter is one tenth of the letter height. Inclined lettering is not part of version 1.0.
*Basis: pending ISO check, ISO 3098 (clause to be confirmed), checklist P-13. Basis for upright
only: house choice H-27. One style keeps the text width table to one table.*

**11.2 Heights.** The **height** `h` of text is the height of a capital letter, in paper
millimetres. It is one of:

> 2.5, 3.5, 5, 7, 10, 14, 20

Any other height is an error `11.2`.
*Basis: pending ISO check, ISO 3098 (clause to be confirmed), checklist P-08.*

**11.3 The text entity.** A text entity has one line of text, an anchor point in model space, a
height (clause 11.2), a rotation, a horizontal alignment and a vertical alignment.

**11.3.1 Alignment.** The horizontal alignment is `left`, `centre` or `right`. The default is `left`.
The vertical alignment is `baseline`, `middle` or `top`. The default is `baseline`. The anchor is the
point of the text box that the alignment names. `middle` is at half the capital height above the
baseline. `top` is at the full capital height above the baseline.

**11.3.2 Rotation.** The rotation is in degrees on the paper, counter-clockwise from the paper
horizontal. The default is 0. It is rounded as clause 5.4 says. Text with rotation 0 reads left to
right on the sheet, whatever the rotation of the viewport.
*Basis: house choice H-28. A rotated map keeps its labels readable.*

**11.3.3** The text is one line. It holds no line break. An empty text is error `11.3.3`.

**11.4 Scale.** The height of text is a paper value. The viewport scale never changes it
(clause 7.4.1).

**11.5 Containment.** Until clause 11.7 is written, the test of clause 7.7.1 is made on the anchor
point of a text entity only.
*Basis: house choice H-29. The text box needs the width table, and the width table needs the
font.*

**11.6 Font.** No font is adopted yet. The candidate was checked and not adopted. The reason is
recorded on ticket #8 and in the maintainer report. The text of clause 11.6 will name one font,
its source, its licence and the place where the licence text is kept.

**11.7 Width table and text box.** To be written with clause 11.6. The text box of a text entity
will be a rectangle. Its height will be the capital height `h`. Its width will be the sum of the
character widths of the width table, times `h`.

## 12 Dimensions and leaders

Written in a later ticket.

## 13 Symbols

Written in a later ticket.

## 14 Sheet furniture

Written in a later ticket. Clause 7.9 (scale bar) and clause 7.10 (north arrow) are already
complete.

## 15 Output mapping

Written in a later ticket.

## 16 Normalised content

Written in ticket #10.

## 17 Conformance

Written in ticket #10.

## 18 Versions and the schema

Written in ticket #9.
