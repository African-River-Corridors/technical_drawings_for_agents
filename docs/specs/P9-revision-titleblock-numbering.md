# P9 — revision register + ISO 7200 title block + enforced numbering

**Issue:** upstream #61 (absorbs review items 14, 15, 13;
supersedes the numbering scope of upstream #43)
**Depends on:** upstream #53 (P1 — paper-space sheet model)
**Owner:** `senior-engineer` · **Status:** specification for review · **Author:** spec agent, 2026-07-24

This is a specification, not an implementation. It is written to be implemented twice, independently,
and for the two results to be indistinguishable in behaviour.

---

## 0. Hard constraints (restated — these bind the implementer)

1. **Backward compatibility is sacred.** Every drawing that validates today must still validate after
   this change, unchanged, byte-for-byte in its rendered output. New requirements are **opt-in** or are
   **accompanied by a migration in the same PR**. There is exactly one deliberate exception, argued in
   §4.9: the ISSUED gate gets *stricter*, and no drawing in either repo is currently ISSUED.
2. **Never invent a standard.** The numbering grammar in §3.4 is derived *only* from what the vault
   standard and the real project data actually contain, quoted verbatim. Everything the sources do not
   say is an **open question for the owner** (§4.11, §8), carried in config with a conservative default —
   never baked into code and never described as settled.
3. **The ISSUED gate is untouchable, and this is the PR closest to it.** No tool, CLI command, CI run,
   test, or agent may flip a drawing to `ISSUED FOR CONSTRUCTION`, and the tool must **never** write a
   value into `app` (approver). §4.8 and §5 state the mechanisms; §6.11–§6.14 are the tests that pin
   them. If an implementer finds themselves adding an `--app` flag, a `--force-issue`, a default
   approver, or a CI-runnable seal, they have misread this spec.
4. **Determinism: prefer loud failure over a silent no-op.** Notably: today, adding `revisions:` to a
   `meta.yaml` is *silently swallowed* into `DrawingMeta.extra` (`meta.py:54`) and the current title
   block *silently ellipsises the drawing title* (`svg.py:572-576`). Both are the failure mode this
   change exists to remove; neither may be reproduced in new code.
5. **House style.** `src/technical_drawings_for_agents/components/layout.py` is the reference: frozen dataclasses, one
   module-specific error class per module, config validated on load with precise
   `"<file>: <field> must be …"` messages, no silent defaults for a value that must be authored,
   tests in `tests/test_*.py` named for the behaviour they assert.

---

## 1. Intent

A reviewer opening one of our sheets should be able to answer, from the bottom-right corner alone and
without asking anyone: *what is this drawing, which revision am I holding, what changed since the last
one, who drew it, who checked it, who — if anyone — has approved it, and is it safe to build from?*
"Good" is a title block that is the **same physical size and layout on A0 as on A3**, filled from **one
data model** with no hand-typed strings, sitting above a **revision table that is the drawing's actual
history** rather than a single letter with no provenance; a **drawing number that a machine has
checked** against a written convention; and an approver cell that is **blank until a named human signs
it**, with no code path anywhere in the toolkit capable of filling it in.

The failures this prevents are the ones that discredit an otherwise-correct drawing on sight. A sheet
whose `revision: B` (vault `site-plan.yaml:27`) has no date, no description and no initials cannot be
reconciled against a site copy — nobody can tell whether the rev B in someone's hand is this rev B. A
title block laid out in pixels for a single canvas (`svg.py:536`, `isosheet.py:67`) is either unreadably
small or grotesque at any other size, and today silently truncates the title it is meant to state. A
drawing number produced by string surgery on another number (vault `source.py:112`:
`plan["meta"]["drawing_no"].replace("WTP-GA", "SITE-GA")`) is not a stable identifier — it is a
coincidence. And an approver field a tool can populate is worse than no approver field at all: it
converts the responsible engineer's signature into a rendering default. This spec closes all four.

---

## 2. Current state (read from the code, with citations)

### 2.1 `meta.yaml` model — `src/technical_drawings_for_agents/meta.py` (91 lines)

`DrawingMeta` is a plain (non-frozen) dataclass with a flat field list (`meta.py:19-32`):
`number`, `title`, `revision: str = "A"` (`meta.py:23`), `scale`, `units`, `date`, `status`, `tool`,
`for_construction`, `discipline`, `project`, `extra`.

- **Revision is a bare scalar with a default.** `meta.py:23` gives `revision` the default `"A"`, so a
  `meta.yaml` that omits it silently becomes rev A. There is no history, date, description, or
  by/checked/approved anywhere in the model.
- **Unknown keys are swallowed, not rejected.** `from_dict` (`meta.py:38-55`) whitelists eleven keys
  and sweeps everything else into `extra` (`meta.py:54`). Consequence: **a `revisions:` block added to a
  `meta.yaml` today is silently ignored.** An author would get no error and no revision table. This is
  the exact silent-no-op the pipeline contract forbids, and it is why §3.1 must also decide how unknown
  keys behave.
- **`validate()` (`meta.py:63-80`) checks four things:** `number` non-empty, `title` non-empty, `status`
  in `STATUS_ORDER` (`style.py:67` — `["DRAFT", "CONCEPT", "ISSUED"]`), and the safety gate.
- **The for-construction / ISSUED gate is `meta.py:74-79`:**
  ```python
  # The hard safety rule: for-construction only after ISSUED sign-off.
  if self.for_construction and self.status_key != "ISSUED":
      problems.append(
          "meta: for_construction=true is only allowed once status is "
          "ISSUED (engineer sign-off required)"
      )
  ```
  Note what it does **not** check: nothing requires a named approver, nothing records *who* issued it,
  and nothing prevents an ISSUED sheet's content from being edited afterwards. The gate is a
  consistency check between two fields in the same file, both of which an agent could write.
- **`title_block()` (`meta.py:82-91`)** returns a six-key dict for `svg_title_block`, and composes the
  displayed number at `meta.py:87`:
  `f"{self.number}_Rev{self.revision}" if self.revision else self.number`. So the `_RevB` suffix from
  the standard's example is a *display* composition — the stored `number` is bare. That is the correct
  behaviour and §3.4 preserves it.
- **`date` is annotated `str` but is actually a `datetime.date`.** `meta.py:26` declares
  `date: str = ""`, but `yaml.safe_load` of `date: 2026-07-15` yields `datetime.date(2026, 7, 15)`
  (verified against `drawings/example/simple-section/meta.yaml`), and `from_dict` (`meta.py:53`) passes it
  through unconverted. So `title_block()["date"]` is a `date` object today, stringified downstream by
  `svg_text`. Nothing validates the annotation. **Consequence for this spec:** §3.1 must accept a native
  YAML date *and* an ISO-8601 string for `revisions[].date`, and test 21 (§6.21) must assert the
  `datetime.date` object, not `"2026-07-15"` — asserting the string would fail. Widening the annotation
  to `str | datetime.date` is a one-line honesty fix and is in scope; **changing the runtime value is
  not** (it would alter every rendered sheet).

### 2.2 `validate.py` (132 lines)

- Validation is **marker-string grep over the emitted SVG** (`validate.py:23-31`): a sheet "has a title
  block" iff the literal `class="title-block"` appears in the file. Any replacement renderer must keep
  emitting that class or every existing drawing fails.
- `validate_drawing_dir` (`validate.py:83-119`) loads `meta.yaml` if present and appends
  `meta.validate()` (`validate.py:88-97`); a missing `meta.yaml` is a problem; a missing `out/*.svg` is a
  problem. Scale-true sheets require all three markers, schematics drop the scale bar
  (`validate.py:29-31`).
- `validate_target` (`validate.py:122-132`) dispatches on directory / `.yaml` / `.svg`.
- **There is no revision check, no numbering check, and no check that the watermark matches
  `meta.status`.** (The watermark/status check is review item 21 — out of scope here, see §7.)

### 2.3 `svg.py::svg_title_block` (`svg.py:526-592`) — the px-positioned legacy block

- Signature takes `width, height` in **pixels** and hard-codes `block_width=280`, `row_height=18`
  (`svg.py:536-537`). Position is `x0 = width - margin - block_width`,
  `y0 = height - margin - block_height` (`svg.py:556-557`) — bottom-right of a **pixel canvas**. There is
  no notion of paper size, so on a larger canvas the block occupies proportionally less of the sheet and
  its 7–9 px text becomes physically tiny; on a smaller one it collides with the frame.
- Six rows only: PROJECT / TITLE / DRAWING NO. / SCALE / DATE / REV. (`svg.py:547-554`). No creator, no
  checker, **no approver**, no document type, no document status, no legal owner, no sheet number.
- **It silently truncates.** `svg.py:572-576`:
  ```python
  lines = textwrap.wrap(str(value), width=value_chars) or [""]
  max_lines = 2 if label_text == "TITLE" else 1
  if len(lines) > max_lines:
      lines = lines[:max_lines]
      lines[-1] = lines[-1][: max(0, value_chars - 3)] + "..."
  ```
  `value_chars` is derived from a magic `5.7` px-per-character estimate (`svg.py:566`). A long drawing
  title is quietly ellipsised on the sheet with no error anywhere.
- Consumed by `Drawing.render()` (`svg.py:678-687`), which calls
  `svg_title_block(self.width, self.height, **self.title_block)` (`svg.py:685-686`) whenever
  `Drawing.title_block` (`svg.py:659`) is not `None`.

### 2.4 `isosheet.py` (169 lines) — the ISO chrome used by `bfd` / `pid`

- `titleblock(x, y, meta)` (`isosheet.py:63-91`) is a **second, independent** title-block
  implementation, also in px, also fixed size: `W, H = 500, 96` (`isosheet.py:67`). All cell dividers
  are literal px offsets (`isosheet.py:68-73`).
- Its field labels are already ISO 7200 vocabulary (`isosheet.py:80-89`): `RESPONSIBLE DEPT.`,
  `TECHNICAL REFERENCE`, `CREATED / APPR.`, `DOCUMENT TYPE`, `DOCUMENT STATUS`,
  `TITLE / LEGAL OWNER`, `DOC. No.`, `REV / DATE`. **§3.3 reuses this vocabulary** so the new renderer
  produces a recognisably identical document.
- `isosheet.py:82` is important precedent: the creator/approver cell renders the literal
  `"AI-assisted / (Eng. —)"` — i.e. the existing code already refuses to name an approver and prints an
  em-dash. The new model keeps that behaviour and makes it structural rather than a string constant.
- `sheet(...)` (`isosheet.py:143-169`) assembles a px canvas defaulting to `sheet_size=(1500, 950)`
  (`isosheet.py:143`), with the drawing band and title-block anchor computed by px arithmetic
  (`isosheet.py:152`, `isosheet.py:165`: `tbx, tby = SW - 34 - 8 - 500, SH - 34 - 8 - 96`).
- `pid.py:372` reads `meta["status_key"] or meta["watermark"] or "CONCEPT"` for the watermark while the
  P&ID data's `meta.status` is a **display string** (`SYN-PSK-PID-001.pid.yaml:25`:
  `status: CONCEPT — NOT FOR CONSTRUCTION`). So `status` means a lifecycle *key* in `DrawingMeta` and a
  *display string* in the isosheet meta dict. §4.10 resolves this for the new field model without
  changing either existing path.

### 2.5 Real consumers

**In-repo examples** (`technical_drawings_for_agents/drawings/example/`):

| Drawing | number | revision | status | notes |
|---|---|---|---|---|
| `simple-section/meta.yaml` | `EXA-CIV-SEC-001` | `A` | `CONCEPT` | scale-true; `source.py:100` passes `title_block=meta.title_block()`, `source.py:182` `DrawingMeta.load` |
| `synthetic-pid/meta.yaml` | `SYN-PSK-PID-001` | `A` | `CONCEPT` | schematic; renders via `isosheet.sheet` |

Both carry `for_construction: false` and neither has any `revisions:` key. Test fixtures use
`TST-BFD-001` (`tests/test_bfd.py:9`) and `TST-PID-001` (`tests/test_pid.py:22`), and
`tests/test_toolkit.py:54-56` constructs `DrawingMeta(number="X-1", ...)` — **`X-1` does not conform to
any plausible numbering grammar.** This single line is the strongest evidence for opt-in enforcement
(§4.11).

**Out-of-repo consumer** — `<project>/drawings/basin-site/`:

- `site-plan.yaml:26-27`:
  ```yaml
  drawing_no: STA-WTP-GA-001
  revision: B
  ```
  A bare `B`. No date, no description, no initials, and no record of what rev A was or what changed.
  This is review item 14 in the wild.
- `source.py:107-117` hand-builds the title block:
  ```python
  d = Drawing(
      W, H, vb, status="CONCEPT",
      title_block=dict(
          project="DEMO Water — Basin",
          title="WTP Site General Arrangement",
          drawing_no=plan["meta"]["drawing_no"].replace("WTP-GA", "SITE-GA"),
          scale="1:1250 (A3)",
          date="2026-07-24",
          revision=plan["meta"]["revision"],
      ),
  )
  ```
  Three defects visible in seven lines: the drawing number is **derived by string replacement from a
  different drawing's number** (`source.py:112`), the scale is a **hand-typed literal** including the
  paper size (`source.py:113` — P1 #53's problem), and the date is hard-coded. This sheet is not
  reachable by `DrawingMeta` at all — it bypasses `meta.yaml` entirely. §3.7 and §7 say what P9 does and
  does not do about it.

### 2.6 What the vault standard actually says about numbering

the house standard *Engineering Drawings as Code* (lines 63-64),
verbatim and in full — this is the **entire** written convention:

> - **Numbering & revision:** a formal drawing number + rev, extending the existing `ARC-CSL-001` style
>   (e.g. `STA-WTP-GA-001_RevB`). The number is the stable ID; never reuse it for a different drawing.

And `03-Resources/Standards/_index.md:48`:

> **Engineering doc & data control:** **ISO 19650** (lite) + a formal **document/drawing numbering
> convention** (extend the existing `ARC-CSL-001` style), specified inside [[Engineering Drawings as
> Code]]. *(… the numbering-convention half stays proposed.)*

**Both issues need correcting on this point, and I am correcting them:**

- **#43** says "Finalise the drawing-numbering convention … currently *proposed* in the vault standard".
  It reads as though a convention exists and merely needs ratifying. It does not. Two examples and one
  sentence is not a convention: the sources **never name the segments**, never state how many there are,
  never give a controlled vocabulary, and never state the serial width. `ARC-CSL-001` has two alpha
  segments; `STA-WTP-GA-001` has three. The standard's own two examples disagree on field count.
- **#61** says "Enforce the drawing-numbering convention in `meta.py` / `validate` — malformed or missing
  numbers fail." Taken literally and globally that breaks `tests/test_toolkit.py:54` (`X-1`) and any
  consumer with a legacy number, violating constraint 1. §4.11 makes enforcement opt-in.
- Both issues therefore under-scope the work: **the deliverable is not "add a regex", it is "make the
  grammar a declared, versioned, per-project config with a shape-only default"**, plus a written
  open question that lets the owner promote *proposed → adopted* in the standard afterwards. P9 must not
  promote it; that is a vault change and the owner's call.

Observed identifiers, which is all the evidence there is:

| Identifier | Where | Segments before serial | Note |
|---|---|---|---|
| `ARC-CSL-001` | `Commissioning Plan …md:13` (`Ground Works/ARC-CSL-001_Slab_Drawing.dxf`) | 2 (`ARC`, `CSL`) | the "existing style" the standard names; a received slab drawing |
| `STA-WTP-GA-001` | vault `site-plan.yaml:26`; standard's example | 3 (`STA`, `WTP`, `GA`) | site · facility · drawing type |
| `EXA-CIV-SEC-001` | `drawings/example/simple-section/meta.yaml` | 3 (`EXA`, `CIV`, `SEC`) | project · discipline · type |
| `SYN-PSK-PID-001` | `drawings/example/synthetic-pid/meta.yaml` | 3 | project · unit · doc type |
| `STA-PSK-PID-001` | `SYN-PSK-PID-001.pid.yaml:6` (comment, planned real sheet) | 3 | |
| `STB-STA-STC-FOUND-001` | vault `site-plan.yaml:59` | 4 (`STB`, `STA`, `STC`, `FOUND`) | a *report* number, not a drawing; 5-char segment; why the grammar must allow 2–4 segments and >3 chars |
| `TST-BFD-001`, `TST-PID-001` | `tests/test_bfd.py:9`, `tests/test_pid.py:22` | 2 | conform to the shape |
| `X-1` | `tests/test_toolkit.py:54` | 1, 1-digit serial | **does not conform** |

Everything a grammar could say beyond "uppercase alnum segments, hyphen-separated, zero-padded numeric
serial last" is unsupported by the sources.

---

## 3. Design

### 3.0 Module layout

Two new modules and additive changes to two existing ones. Nothing is deleted.

```
src/technical_drawings_for_agents/
  revisions.py     NEW  Revision, RevisionRegister, RevisionError          (~200 lines)
  titleblock.py    NEW  TitleBlockFields, TitleBlockLayout, PaperFrame,
                        iso7200_title_block, revision_block, TitleBlockError (~350 lines)
  numbering.py     NEW  Segment, NumberingScheme, NumberingError, SHAPE_ONLY (~180 lines)
  meta.py          EDIT additive fields + additive validate() checks
  validate.py      EDIT additive checks in validate_drawing_dir
  cli.py           EDIT new `revision` subcommand group
  svg.py           UNCHANGED except a deprecation note in svg_title_block's docstring
  isosheet.py      UNCHANGED (see §4.4)
```

One module-specific error class each — `RevisionError`, `TitleBlockError`, `NumberingError`, all
subclassing `ValueError`, matching `LayoutError` (`components/layout.py:38-39`).

### 3.1 The `revisions:` schema

```yaml
# meta.yaml
number: EXA-CIV-SEC-001
title: Concrete-Lined Drainage Channel — Typical Section
revision: B                 # MUST equal revisions[-1].rev  (see §4.1)
revision_scheme: alpha      # optional; alpha | numeric | alpha-then-numeric   (§4.2)
revisions:
  - rev: A
    date: 2026-07-15
    description: First issue for internal review
    by: AB
    chk:                    # optional — absent means not checked
    app:                    # optional — NEVER written by the tool   (§4.8)
  - rev: B
    date: 2026-07-22
    description: Channel invert lowered to 54.20 m MSL; hatch legend added
    by: AB
    chk: CD
    app:
```

Field-by-field. "Required" means: present, of the right type, and non-empty after `str.strip()`.

| Field | Type | Default | Required | Validation rule (exact) |
|---|---|---|---|---|
| `rev` | `str` | — | **yes** | Non-empty after strip. Normalised with `.strip().upper()`. Must match the active scheme's token pattern (§4.2). Must be **unique** within the register (compared post-normalisation). |
| `date` | `datetime.date` | — | **yes** | YAML native date, or an ISO-8601 `YYYY-MM-DD` string parsed with `datetime.date.fromisoformat`. Any other form is `RevisionError`. Must be **non-decreasing** relative to the previous entry (equal dates allowed — two revisions can be issued the same day). Must not be in the future relative to `date.today()` at validate time → **warning**, not error (clock skew and forward-dated issues are legitimate; a future date is worth surfacing, not blocking). |
| `description` | `str` | — | **yes** | Non-empty after strip. Length must fit the DESCRIPTION cell at the drawing's declared sheet size (§3.3 capacity formula) — over-long is an **error** naming the limit, never a silent ellipsis. The literal sentinel `NOT RECORDED (pre-register revision)` is accepted **only** when the same entry carries `pre_register: true` (§3.7). |
| `by` | `str` | — | **yes** | Non-empty after strip. Free text, 1–12 chars; convention is initials. This is the drafter/author. |
| `chk` | `str \| None` | `None` | no | If present, non-empty after strip, ≤12 chars. `None`/absent/empty-string all normalise to `None`. The checker. |
| `app` | `str \| None` | `None` | no | Same shape rules as `chk`. **The approver — the responsible engineer's signature.** No code path in the toolkit may set this to anything other than a value read verbatim from the YAML the human authored (§4.8). |
| `seal` | `str \| None` | `None` | no | `sha256:<64 hex>` over the entry's canonical form (§3.6). Present only on sealed (issued) entries. Recomputed and compared by `validate`; mismatch is an error. |
| `pre_register` | `bool` | `False` | no | Migration marker only (§3.7). Relaxes the `description` sentinel rule. `validate` emits a warning for every entry carrying it. |

Unknown keys inside a revision entry are a **`RevisionError`**, not swallowed — the opposite of
`meta.py:54`, and deliberately so (§4.7). Note this is *stricter* than `components/layout.py`, which
validates the keys it knows with precise messages but ignores ones it does not; §4.7 argues why the new
structures go further.

**Ordering and uniqueness rules:**

- **R1 — order is chronological, oldest first.** `revisions[0]` is the earliest. The file order *is* the
  order; the loader does not sort. If dates decrease between consecutive entries, that is an error
  naming both indices. Rationale: a loader that silently re-sorts hides an authoring mistake and makes
  the rendered table disagree with the diff a reviewer read.
- **R2 — `rev` tokens are unique** post-normalisation. Duplicate → error naming both indices.
- **R3 — `rev` tokens increase monotonically** under the active scheme's ordering (§4.2). Gaps are
  allowed (skipping `C` is legitimate — a revision can be abandoned before issue); going backwards is
  not.
- **R4 — the last entry is the current revision** and `revisions[-1].rev` must equal
  `meta.revision.strip().upper()` (§4.1).
- **R5 — a non-empty register may not be shortened or reordered once any entry is sealed.** Enforced by
  the seal covering the entry's index and the digest of all preceding entries (§3.6).
- **R6 — an empty `revisions: []`** is an error (`"revisions: must contain at least one entry (or omit the key entirely)"`).
  Omitting the key is legal (backward compat); declaring it empty is a mistake.

### 3.2 The revision-block rendering contract

The revision table sits **immediately above the title block**, right-aligned to it, same width, growing
**upward**. Oldest entry at the top, **newest at the bottom, adjacent to the title block** — so a
reviewer's eye lands on the newest description and the title block's `REV / DATE` cell together.

```
        ┌──────┬────────────┬─────────────────────────────┬─────┬─────┬─────┐   ← grows upward
        │  A   │ 2026-07-15 │ First issue for internal …  │ AB │  —  │  —  │
        │  B   │ 2026-07-22 │ Channel invert lowered …    │ AB │ CD  │  —  │   ← newest
        ├──────┴────────────┴──────────┬─────────┬────────┴─────┴─────┴─────┤
        │ RESPONSIBLE DEPT.            │ TECH…   │ CREATED BY │ APPROVED BY │
        │ …title block…                                                    │
        └──────────────────────────────────────────────────────────────────┘
                                                     frame bottom-right corner ┘
```

Contract, all dimensions in **millimetres of paper**:

- **C1** Width is exactly the title-block width (`layout.width_mm`, 180 mm — §3.3). Right edge coincides
  with the title block's right edge, which coincides with the frame's right edge.
- **C2** Header row (`REV | DATE | DESCRIPTION | BY | CHK | APP`) is at the **top** of the table, above
  the oldest entry. Row height `layout.rev_row_mm` (5 mm); header row same height.
- **C3** Column widths are a fixed mm table summing to `width_mm` (§3.3), shared with the header.
- **C4** Bottom edge of the table = top edge of the title block (they share a line; the shared line is
  drawn once, by the title block, so the emitted SVG has no coincident duplicate strokes — a
  determinism requirement, coincident strokes render darker and differ between rasterisers).
- **C5** Total table height = `(1 + n_rendered) * rev_row_mm`. It grows upward from a fixed bottom, so
  the title block never moves as revisions accumulate.
- **C6 — overflow is visible, never silent.** If `n > max_rows` (default 8, config), render the **most
  recent `max_rows - 1`** entries plus, as the topmost data row, a continuation row spanning the
  DESCRIPTION column: `+ N EARLIER REVISIONS — SEE meta.yaml`. `validate` emits a **warning** naming the
  count. Rejected alternative: drop the oldest silently (hides history) or grow without bound (collides
  with the drawing at 20 revisions on A3).
- **C7 — empty cells render an em-dash `—`**, never blank and never a substituted value. A blank cell is
  ambiguous between "not applicable" and "renderer bug"; `—` states "deliberately not filled". This
  matches the existing `isosheet.py:82` precedent.
- **C8 — no register, no table.** If `revisions:` is absent the revision block is not emitted at all and
  the title block renders identically to a single-revision sheet. Backward compat.
- **C9 — machine-readable extents.** The emitted group is
  `<g class="revision-block" data-extents-mm="x y w h" data-rows="n" data-latest-rev="B">`, with each
  data row as `<g class="revision-row" data-rev="B">` and each cell as
  `<g class="revision-cell" data-col="app">`. **`data-rows` counts rendered rows *excluding* the header**
  (so a 3-entry register gives `data-rows="3"` and a total height of `(1 + 3) * rev_row_mm`); a
  continuation row (C6) counts as one row. This is what makes §6 mechanical rather than visual.

### 3.3 ISO 7200-style title block, one implementation correct at every size

**The core design decision, stated plainly: the title block is a fixed physical size in millimetres —
180 mm × 56 mm — anchored to the frame's bottom-right corner. It does not scale with the sheet.**

That single sentence is why one implementation is correct at A3, A2, A1 and A0 in both orientations: on
paper, a title block is a physical artifact of a fixed size, exactly like the text height on it. A
percentage-of-sheet block (which is what a px block on a variable canvas amounts to) is *guaranteed* to
be wrong at every size but one. Sheet size affects **only the anchor point**, which comes from P1's
frame.

**Why 180 mm.** ISO 5457 constrains the title block to lie inside the frame at the bottom-right, and
180 mm is the frame width of **A4 portrait** under the common 20 mm filing edge + 10 mm elsewhere margin
set (`210 − 20 − 10 = 180`). Choosing 180 makes the block exactly fill the narrowest frame we could ever
draw on and fit with room to spare on every larger one. **Open question O-3 (§8):** confirm 180 mm and
the margin set against a copy of ISO 5457 before the standard is updated; and confirm the ISO 7200
mandatory-field list against a copy of ISO 7200:2004 Table 1. I have **not** got either standard's text
and am not going to paraphrase it from memory — the field *vocabulary* below is taken from
`isosheet.py:80-89`, which the owner already accepted as "ISO 7200-style", not from the standard document.

**Layout table** — `ISO7200_180MM`, a frozen dataclass constant. All values mm; `x` from the block's
left edge, `y` from its top edge. This table is the single source of truth; §6.1 asserts the emitted
geometry equals it.

| Band | y | h | Cells (x → x, label, field) |
|---|---|---|---|
| 1 | 0 | 14 | 0–76 `RESPONSIBLE DEPT.` → `responsible_dept` · 76–130 `TECHNICAL REFERENCE` → `technical_reference` · 130–155 `CREATED BY` → `created_by` · 155–180 `APPROVED BY` → `approved_by` |
| 2 | 14 | 14 | 0–76 `DOCUMENT TYPE` → `document_type` · 76–130 `DOCUMENT STATUS` → `document_status` · 130–155 `CHECKED BY` → `checked_by` · 155–180 `SHEET` → `sheet` |
| 3 | 28 | 20 | 0–108 `TITLE` → `title` (+ `supplementary_title` on line 2) · 108–153 `DOC. No.` → `identification_number` · 153–180 `REV / DATE` → `revision` + `date_of_issue` |
| 4 | 48 | 8 | 0–108 `LEGAL OWNER` → `legal_owner` · 108–180 `PROVENANCE` → `provenance` |

Total height **56 mm**. Revision-table columns, same 180 mm total:
`REV 0–14 · DATE 14–44 · DESCRIPTION 44–132 · BY 132–148 · CHK 148–164 · APP 164–180`.

**Text heights** are ISO 3098 nominal sizes in mm — labels **2.5**, values **3.5**, TITLE **5.0**,
revision-table body **2.5**. Because the block is physically fixed, text is the *same physical height on
A0 as on A3*, which is the point: legibility is a property of the paper, not of the canvas.

**Cell capacity is computed, and over-long content is an error.** Per-cell character capacity:

```
capacity(cell) = floor((cell.w_mm - 2 * PAD_MM) / (text_mm * ADVANCE_FACTOR))
PAD_MM = 1.5
ADVANCE_FACTOR = 0.55     # mean advance / cap height for the Helvetica/Arial stack; pinned by a test
```

Worked capacities from the §3.3 table, which an implementer should reproduce exactly:

| Cell | w_mm | text_mm | max_lines | capacity/line | total |
|---|---|---|---|---|---|
| `title` | 108 | 5.0 | 2 | `floor(105 / 2.75)` = **38** | 76 |
| `identification_number` | 45 | 3.5 | 1 | `floor(42 / 1.925)` = **21** | 21 |
| revision-table `DESCRIPTION` | 88 | 2.5 | 1 | `floor(85 / 1.375)` = **61** | 61 |
| revision-table `BY`/`CHK`/`APP` | 16 | 2.5 | 1 | `floor(13 / 1.375)` = **9** | 9 |

(The 61-char description limit is why §3.1 caps descriptions and why the migrated example descriptions in
§3.7 are kept short. The 9-char initials limit is looser than the 12-char field rule in §3.1 — the
**stricter of the two applies**, i.e. a `by` of 10–12 chars parses but fails V5 at render; the field rule
allows 12 so that a register can be authored before a sheet size is declared.)

If a value exceeds `capacity * max_lines` for its cell, the renderer raises `TitleBlockError` naming the
field, the value length, and the limit — and `validate` reports the same as a problem. **No ellipsis,
ever.** This is the direct replacement for `svg.py:572-576`.

**Emitted markup contract:**

```xml
<g class="title-block" data-extents-mm="661.0 769.0 180.0 56.0" data-sheet="A1"
   data-orientation="landscape" font-family="Helvetica, Arial, sans-serif">
  <g class="tb-cell" data-field="approved_by" data-extents-mm="816.0 769.0 25.0 14.0">
    <text …>APPROVED BY</text><text …>—</text>
  </g>
  …
</g>
```

`class="title-block"` is mandatory and non-negotiable: `validate.py:24` greps for it.
`data-extents-mm` and `data-field` are the mechanical assertion surface for §6 and for P5's future
collision checks.

**Degradation when optional fields are absent (§4.7):** the cell is **always drawn** — border, label,
and `—` for the value. The block's geometry is therefore identical for every drawing at a given sheet
size regardless of how much metadata exists. A missing *required* field is a validation error, not a
dash.

### 3.4 The drawing-numbering grammar

**Traceability first.** The default grammar encodes exactly two things the sources actually state — the
`ARC-CSL-001` shape and "the number is the stable ID" — and nothing more. Field *semantics* are an open
question (§8, O-1/O-2), so the default scheme is deliberately **shape-only**: it names no segments and
enforces no vocabulary.

```python
SHAPE_ONLY_PATTERN = r"^[A-Z0-9]{2,5}(?:-[A-Z0-9]{2,5}){1,3}-[0-9]{3}$"
```

Field-by-field:

| Element | Pattern | Why exactly this, from the sources |
|---|---|---|
| Leading segment | `[A-Z0-9]{2,5}` | `ARC`, `STA`, `EXA`, `SYN`, `STB`, `TST` are all 3; `FOUND` (in `STB-STA-STC-FOUND-001`) is 5. Min 2 rejects the degenerate `X-1`. Upper-case only: every observed identifier is upper-case. Digits allowed because nothing forbids them and equipment prefixes like `4435` appear in supplier data (standard, line 133). |
| Further segments | `(?:-[A-Z0-9]{2,5}){1,3}` | Between **1 and 3** more, i.e. **2 to 4 segments total** before the serial. Lower bound 2 = `ARC-CSL-001` (the named "existing style"). Upper bound 4 = `STB-STA-STC-FOUND-001`, the widest real identifier in the project data. Not 5+: no evidence. |
| Separator | `-` | Every observed identifier. Underscore is reserved for the display rev suffix (`STA-WTP-GA-001_RevB`, standard line 64) and is therefore **not** a segment separator. |
| Serial | `[0-9]{3}` | `001` in all seven observed identifiers. Zero-padded, exactly 3 digits, so numbers sort lexically. Configurable per scheme (`serial_digits`), default 3. |
| Revision suffix | **absent** | The stored `number` is the bare stable ID; `_RevB` is a *display* composition, already implemented at `meta.py:87`. A number containing `_Rev` is an **error** with the message pointing at `meta.revision`. |

**Regex is not the whole design — the scheme is config.** A project declares its own, which is how the
semantics become sayable without P9 inventing them:

```yaml
# drawings/numbering.yaml  (or an inline `numbering:` mapping in meta.yaml)
numbering:
  id: demo-water-v1
  source: "Engineering Drawings as Code §Repo layout & conventions (PROPOSED, not adopted)"
  serial_digits: 3
  segments:
    - name: site
      pattern: "[A-Z]{3}"
      vocabulary: [STA, STB, STC, ARC]          # optional; empty = pattern only
    - name: facility
      pattern: "[A-Z]{2,5}"
    - name: drawing_type
      pattern: "[A-Z]{2,5}"
      vocabulary: [GA, SEC, PID, BFD, DET, CSL, FOUND]
```

`source:` is **required** on any non-default scheme — a free-text provenance string saying where the
convention is written down. A scheme with no citable source is a scheme somebody invented, and the
loader says so: `"numbering.source is required — name the document this convention comes from"`.

Matching a number against a named scheme yields a dict of segment name → value, which the title block
may display and a future drawing register may index. Vocabulary violations are errors listing the
allowed values, in the house message style of `components/layout.py:225-234`.

### 3.5 Public Python API

```python
# src/technical_drawings_for_agents/revisions.py
from __future__ import annotations
import datetime
from dataclasses import dataclass
from typing import Any, Iterable

class RevisionError(ValueError):
    """Raised when a revision register is malformed or violates an ordering rule."""

REV_SCHEMES: dict[str, "RevisionTokenScheme"]        # "alpha" | "numeric" | "alpha-then-numeric"
DEFAULT_REV_SCHEME: str = "alpha"
PRE_REGISTER_SENTINEL: str = "NOT RECORDED (pre-register revision)"

@dataclass(frozen=True)
class Revision:
    rev: str
    date: datetime.date
    description: str
    by: str
    chk: str | None = None
    app: str | None = None
    seal: str | None = None
    pre_register: bool = False

    @property
    def is_sealed(self) -> bool: ...
    @property
    def is_approved(self) -> bool:                   # app is a non-empty string
        ...
    def canonical(self, *, index: int, prior_digest: str) -> str:
        """Deterministic, sorted-key JSON of the frozen fields (seal excluded)."""
    def digest(self, *, index: int, prior_digest: str) -> str:
        """``sha256:<64 hex>`` of ``canonical(...)``."""

@dataclass(frozen=True)
class RevisionRegister:
    entries: tuple[Revision, ...]
    scheme: str = DEFAULT_REV_SCHEME
    source: str = ""                                 # e.g. "meta.yaml"

    @property
    def latest(self) -> Revision: ...
    def get(self, rev: str) -> Revision | None: ...  # normalised lookup
    def validate(self, *, revision: str | None = None,
                 today: datetime.date | None = None) -> tuple[list[str], list[str]]:
        """Return ``(problems, warnings)``. Applies R1-R6 and the field rules of §3.1.
        When ``revision`` is given, also asserts R4."""

    @classmethod
    def from_list(cls, raw: Any, *, ctx: str, scheme: str = DEFAULT_REV_SCHEME) -> "RevisionRegister":
        """Parse a ``revisions:`` list. Raises RevisionError on a structural problem
        (not a mapping, unknown key, unparseable date); *semantic* problems come
        back from validate() as strings so validate can report them all at once."""

    def append(self, entry: Revision) -> "RevisionRegister":
        """Return a new register with ``entry`` appended. Raises RevisionError if
        the register's last entry is sealed and ``entry`` would not follow it, or
        if R2/R3 would be violated. Never mutates. Never sets ``app``."""

    def to_list(self) -> list[dict]:
        """Round-trippable YAML-ready form; key order matches §3.1's table."""
```

```python
# src/technical_drawings_for_agents/numbering.py
class NumberingError(ValueError):
    """Raised when a numbering scheme config or a drawing number is malformed."""

@dataclass(frozen=True)
class Segment:
    name: str
    pattern: str                       # one segment, unanchored
    vocabulary: tuple[str, ...] = ()

@dataclass(frozen=True)
class NumberingScheme:
    id: str
    source: str
    segments: tuple[Segment, ...] = ()  # empty = shape-only
    serial_digits: int = 3
    min_segments: int = 2
    max_segments: int = 4

    @property
    def pattern(self) -> "re.Pattern[str]": ...
    def match(self, number: str) -> dict[str, str]:
        """Segment name → value (``seg0``… when unnamed, plus ``serial``).
        Raises NumberingError with the pattern and the offending number."""
    def validate(self, number: str) -> list[str]:
        """Problem strings; empty = conformant. Never raises."""
    @classmethod
    def load(cls, path: str | Path) -> "NumberingScheme": ...
    @classmethod
    def from_dict(cls, raw: Any, *, ctx: str) -> "NumberingScheme": ...

SHAPE_ONLY: NumberingScheme            # id="shape-only", source=<the standard's line 63-64 quote>
BUILTIN_SCHEMES: dict[str, NumberingScheme] = {"shape-only": SHAPE_ONLY}
```

```python
# src/technical_drawings_for_agents/titleblock.py
class TitleBlockError(ValueError):
    """Raised when title-block content will not fit its cell or the frame is too small."""

@runtime_checkable
class PaperFrame(Protocol):
    """THE INTERFACE P9 NEEDS FROM P1 (#53). P9 does not implement paper space.

    All lengths are millimetres of paper. Origin is the paper's top-left corner,
    +x right, +y down (matching SVG). ``frame_mm`` is the *inner* frame rectangle
    that drawing content and the title block must sit inside.
    """
    size: str                                   # "A0".."A4"
    orientation: str                            # "landscape" | "portrait"
    def frame_mm(self) -> tuple[float, float, float, float]:   # (x, y, w, h)
        ...
    def to_device(self, x_mm: float, y_mm: float) -> tuple[float, float]:
        ...
    def device_per_mm(self) -> float:
        ...

@dataclass(frozen=True)
class Cell:
    field: str
    label: str
    x_mm: float; y_mm: float; w_mm: float; h_mm: float
    text_mm: float = 3.5
    max_lines: int = 1
    def capacity(self) -> int: ...

@dataclass(frozen=True)
class TitleBlockLayout:
    id: str
    width_mm: float
    height_mm: float
    cells: tuple[Cell, ...]
    rev_row_mm: float = 5.0
    rev_columns: tuple[tuple[str, float, float], ...] = ()   # (col, x_mm, w_mm)
    max_rev_rows: int = 8
    label_mm: float = 2.5
    pad_mm: float = 1.5

ISO7200_180MM: TitleBlockLayout          # the §3.3 table

@dataclass(frozen=True)
class TitleBlockFields:
    identification_number: str
    title: str
    revision: str
    date_of_issue: str
    document_type: str = ""
    document_status: str = ""
    legal_owner: str = ""
    responsible_dept: str = ""
    technical_reference: str = ""
    supplementary_title: str = ""
    created_by: str = ""                 # from revisions[-1].by
    checked_by: str = ""                 # from revisions[-1].chk
    approved_by: str = ""                # from revisions[-1].app — ONLY (§4.8)
    sheet: str = ""                      # e.g. "1 / 1"
    provenance: str = ""                 # P3's short hash when available; else ""

    @classmethod
    def from_meta(cls, meta: "DrawingMeta") -> "TitleBlockFields":
        """Never derives ``approved_by`` from anything but an explicit ``app``."""

def iso7200_title_block(frame: PaperFrame, fields: TitleBlockFields, *,
                        layout: TitleBlockLayout = ISO7200_180MM) -> str: ...

def revision_block(frame: PaperFrame, register: "RevisionRegister", *,
                   layout: TitleBlockLayout = ISO7200_180MM) -> tuple[str, list[str]]:
    """Return ``(svg, warnings)``. Empty register → ``("", [])``."""

def title_block_extents_mm(frame: PaperFrame, *,
                           layout: TitleBlockLayout = ISO7200_180MM,
                           rev_rows: int = 0) -> tuple[float, float, float, float]:
    """(x, y, w, h) in mm of the combined title + revision block. Used by tests and
    by callers reserving space. Raises TitleBlockError if it will not fit the frame."""
```

```python
# src/technical_drawings_for_agents/meta.py  — ADDITIVE ONLY
@dataclass
class DrawingMeta:
    # ... all eleven existing fields unchanged, same order, same defaults ...
    revisions: list = field(default_factory=list)      # raw list; register built lazily
    revision_scheme: str = ""                          # "" = DEFAULT_REV_SCHEME
    numbering: str = ""                                # "" = NOT ENFORCED (§4.11)
    sheet: str = ""                                    # ISO size, for P1
    orientation: str = ""                              # for P1

    def register(self) -> RevisionRegister | None:
        """None when no `revisions:` key. Raises RevisionError on a structural problem."""
    def numbering_scheme(self) -> NumberingScheme | None:
        """None when `numbering:` is empty — enforcement is opt-in."""
    def title_block_fields(self) -> TitleBlockFields: ...
    def title_block(self) -> dict:
        """UNCHANGED. Same six keys, same values, for svg_title_block."""
    def validate(self) -> list[str]:
        """The four existing checks, in the same order and with the same message
        text, then the additive checks of §3.6."""
    def warnings(self) -> list[str]:
        """New. Non-blocking observations (pre_register rows, revision overflow,
        future-dated revisions)."""
```

CLI (`cli.py`) — a new `revision` subcommand group. **Note what is absent.**

```
technical_drawings_for_agents revision list <drawing-dir>
technical_drawings_for_agents revision add  <drawing-dir> --rev C --description "…" --by AB [--chk CD]
                                         [--date YYYY-MM-DD]
technical_drawings_for_agents revision seal <drawing-dir> --rev C
```

- `revision add` appends an entry and rewrites `meta.revision` to match. It **never** edits an existing
  entry, **never** writes `status`, **never** writes `for_construction`, and **has no `--app` flag at
  all** (§6.13 asserts the flag's absence by introspecting the parser). It refuses (exit 2) if the
  current last entry is sealed and the drawing's `status` is `ISSUED` without a new `--rev`.
- `revision seal` computes the §3.6 digest for one entry and writes it. It **refuses** (exit 2, no file
  written) unless *all* of: `meta.status == "ISSUED"`, the entry's `app` is a non-empty string that was
  already in the file before this command ran, `stdin.isatty()` is true, and the environment variable
  `CI` is unset. It prints the entry and requires the operator to type the rev token to confirm.
  Sealing is a human act performed at a terminal; §4.8 argues why.

### 3.6 `validate` rules

`DrawingMeta.validate()` keeps its four existing checks verbatim and in order, then adds — **every new
check gated on the opt-in key being present, except V6**:

| # | Fires when | Check | Message shape |
|---|---|---|---|
| V1 | `revisions:` present | Register parses and satisfies R1–R6 and every field rule of §3.1 | `meta: revisions[2].description is required (a revision with no description is not a revision)` |
| V2 | `revisions:` present | R4: `meta.revision` equals `revisions[-1].rev` | `meta: revision 'C' is not the latest entry in revisions: (latest is 'B'); if C is new, add it to revisions:` |
| V3 | `revisions:` present | `meta.revision` appears **somewhere** in the register (a distinct, more specific message than V2 when it appears nowhere at all) | `meta: revision 'D' is absent from revisions: (known: A, B, C)` |
| V4 | `numbering:` non-empty | `number` matches the named scheme; segment vocabularies satisfied | `meta: number 'STA-WTP-GA-1' does not match numbering scheme 'demo-water-v1' (expected …); serial must be 3 digits` |
| V5 | `sheet:` non-empty | `title_block_extents_mm` fits the frame; every cell's content fits its capacity | `meta: title is 96 chars but the TITLE cell holds 2 × 38 = 76 at A1 — shorten it or move the tail to supplementary_title` |
| V6 | **always** | **Strengthened ISSUED gate** (§4.9): `for_construction: true` requires `status == ISSUED` **and**, when a register exists, `revisions[-1].app` non-empty and `revisions[-1].seal` valid | `meta: for_construction=true requires a named approver in revisions[-1].app (the responsible engineer's signature) — no tool may supply it` |
| V7 | `revisions:` present | Every sealed entry's recomputed digest matches its stored `seal` | `meta: revisions[1] (rev B) is sealed but its content has changed — an ISSUED revision must not be edited in place; add a new revision instead` |

`validate_drawing_dir` (`validate.py:83-119`) additionally:

- **V8** — if any `out/*.svg` contains `class="revision-block"`, its `data-latest-rev` must equal
  `meta.revision` (the rendered sheet and the register agree).
- **V9** — the `<g class="title-block">`'s `data-extents-mm` must lie inside the frame implied by
  `data-sheet`/`data-orientation` (a cheap geometric check that needs no rasteriser).

Warnings are reported on a new `ValidationResult.warnings: list[str]` field
(`validate.py:34-42`), printed by the CLI, and **do not affect `ok`** or the exit code. `ok` remains
`not self.problems`.

### 3.7 Migration

**In this PR (workflows repo):**

1. `drawings/example/simple-section/meta.yaml` — add
   ```yaml
   revision_scheme: alpha
   numbering: shape-only
   revisions:
     - rev: A
       date: 2026-07-15        # = the file's existing `date:`
       description: First issue — worked example for the technical_drawings_for_agents toolkit
       by: AB
       chk:
       app:
   ```
   `EXA-CIV-SEC-001` already conforms to `shape-only`, so opting in changes nothing but adds coverage.
2. `drawings/example/synthetic-pid/meta.yaml` — the same, `date: 2026-07-16`,
   `description: First issue — synthetic P&ID demonstrator (illustrative)` (56 chars, inside the 61-char
   DESCRIPTION capacity of §3.3).
3. **`tests/test_toolkit.py:54-56` is left exactly as it is.** `DrawingMeta(number="X-1", …)` must keep
   validating clean, and that it does is itself the backward-compat test (§6.15). Do **not** "fix" the
   fixture to a conformant number — it is load-bearing evidence that enforcement is opt-in.
4. `svg.py::svg_title_block` gains a docstring note: legacy px-space block, retained for
   backward compatibility, superseded by `titleblock.iso7200_title_block` for paper-space sheets. **No
   `DeprecationWarning` is raised** — a warning would fire in every existing consumer's output for no
   benefit while the paper-space path is still landing.

**Not in this PR — required companion change in the vault repo** (out-of-repo, different repository,
and it needs an answer from the owner):

- `03-Resources/DEMO Water Project/drawings/basin-site/site-plan.yaml:26-27` needs a `revisions:` block
  covering rev A and rev B. **The implementer must not invent the descriptions.** Two rev-B-era changes
  are recoverable from git history of that file, but rev A's intent is not recorded anywhere. The
  migration therefore writes, for any entry whose description cannot be sourced:
  ```yaml
  - rev: A
    date: 2026-07-??
    description: NOT RECORDED (pre-register revision)
    by: AB
    pre_register: true
  ```
  `validate` accepts the sentinel **only** with `pre_register: true` and emits a warning naming the row.
  This is honest (it states an absence rather than fabricating a change note), loud (a warning on every
  run until someone fills it), and non-blocking. **Ask for the owner (O-4, §8):** the real rev A/B dates and
  descriptions for `STA-WTP-GA-001`.
- The same file's `source.py:112` derives a drawing number by `.replace("WTP-GA", "SITE-GA")`. That
  sheet needs its own allocated number. **Follow-up issue**, §7.

---

## 4. Behaviour decisions, with rationale

### 4.1 `meta.revision` stays a separate field that must agree with `revisions[-1].rev`

**Decision:** keep `revision` as an explicit scalar; `validate` requires it to equal the last register
entry's `rev` (V2/V3). Do **not** derive it and do **not** remove it.

**Why.** Three reasons, in order of weight.

1. **Removing it is a breaking API change to a shared CLI.** `DrawingMeta.revision` is read at
   `meta.py:87-90`, and the vault's `source.py:115` reads `plan["meta"]["revision"]` out of a *different*
   YAML schema entirely. Deriving `revision` from a register that most existing drawings do not have
   would either resurrect the `"A"` default silently or start raising on files that validate today.
   Constraint 1 forbids both.
2. **The redundancy is the check, not the defect.** The real-world error this whole issue exists to
   catch is a half-finished edit: someone adds a revision row and forgets to bump the sheet's stated
   rev, or bumps the rev and forgets the row. If `revision` is derived, that error becomes
   *unrepresentable and therefore undetectable* — the tool would happily render whatever the register
   said, and the mismatch between the drawing and the change note would survive to site. Two
   independently authored statements plus an equality check is strictly more informative than one
   statement.
3. **Not every sheet has a register.** `bfd` and `pid` build their title block from a free-form `meta`
   mapping in the drawing data (`pid.py:345`, `bfd.py:230`), not from `DrawingMeta`. A single scalar is
   the only field those paths can rely on.

**Rejected — derive `revision` from the register.** Neater data model, but it deletes the check that
catches the actual failure and breaks every register-less drawing. **Rejected — allow disagreement with
a warning.** A sheet whose printed revision differs from its own history is precisely the
"fails-scrutiny-instantly" class; it must be an error.

### 4.2 Revision identifier scheme — alphabetic before issue, numeric after, **and the standard is silent**

**What the sources say:** nothing. The standard's line 63-64 quotes `RevB` and the two live drawings use
`A` and `B`. There is no statement anywhere in the vault about what happens at first issue.

**Decision:** ship three schemes, default to the one that matches the existing data, and flag the
convention as an open question rather than presenting a choice as settled.

| Scheme id | Tokens | Ordering |
|---|---|---|
| `alpha` (**default**) | `A`, `B`, … `Z`, then `AA`, `AB`, … | Excel-column order |
| `numeric` | `0`, `1`, `2`, … (no padding) | integer order |
| `alpha-then-numeric` | `A`…`Z` **before** first issue, then `0`, `1`, `2` after | all alpha tokens sort before all numeric tokens |

Default is `alpha` because that is what `EXA-CIV-SEC-001`, `SYN-PSK-PID-001` and `STA-WTP-GA-001`
already use, so no existing drawing changes meaning. **`alpha-then-numeric` is my recommendation** —
it is common drafting practice (pre-issue revisions lettered, post-issue numbered) and it makes "has
this ever been issued for construction?" answerable from the revision token alone, which is a real
safety property. But I am not making it the default and I am not writing it into the standard:
**O-2 (§8)** asks the owner to confirm the house convention, including whether `I`, `O` and `Q` are skipped
(widely done, to avoid confusion with `1`/`0`; the vault says nothing). Until answered, `alpha` accepts
all 26 letters — a *permissive* default cannot break a drawing, whereas a restrictive guess can.

### 4.3 Where does the register live — `meta.yaml`, not a separate file

**Decision:** `revisions:` is a key in `meta.yaml`.

**Why.** `meta.yaml` is already "the stable record for this sheet" (its own first comment line) and is
already what `validate_drawing_dir` loads (`validate.py:88-91`). One file to read, one diff to review,
and the register cannot drift out of the directory it describes. **Rejected — `revisions.yaml` beside
it:** a second file that can be deleted, forgotten, or left behind by a copy-paste of a drawing
directory, for no benefit. **Rejected — derive the register from git history:** attractive (the history
is already there) but wrong: git records *file* changes, not *drawing revisions*, the two are not the
same granularity, descriptions would be commit messages written for developers, and it would make the
revision table unavailable to anyone who receives only a PDF. The register is drawing content.

### 4.4 Three title-block implementations will briefly coexist, and that is the honest choice

After this PR the repo contains `svg.py::svg_title_block` (legacy px, dark theme, scale-true sheets),
`isosheet.py::titleblock` (legacy px, print theme, `bfd`/`pid`), and the new
`titleblock.iso7200_title_block` (mm, paper space).

**Decision:** land the new one, change neither old one, and consolidate the *data model* only —
`TitleBlockFields` becomes the single source of the field values all three paths draw from.

**Why.** Porting `isosheet.sheet` to paper space means replacing its px canvas
(`isosheet.py:143-169`), which is P1 #53's and P4's work, not P9's; doing it here would make this PR's
output non-byte-identical for `bfd` and `pid` and blow constraint 1. Porting `Drawing.render()`
(`svg.py:678-687`) means every scale-true sheet including the vault's Basin GA changes output. **The
alternative — hold P9 until P1 lands and then change all three at once — is worse:** it couples the
safety-critical part of this change (the approver cell, the ISSUED gate, the revision register) to a
large rendering refactor. The register and the gate are valuable on their own and should not wait.

**Named risk, with the mitigation:** three implementations is a real smell and an invitation for them to
drift. Mitigation: (a) one data model, `TitleBlockFields`, and a test asserting all three paths agree on
the fields they share (§6.16); (b) a follow-up issue, opened in this PR, to port `isosheet.sheet` and
`Drawing.render` onto the paper-space block once #53 is merged (§7).

### 4.5 Fixed-mm block, not a proportional one

Already stated in §3.3; recorded here as a decision because it is the one an implementer is most likely
to second-guess.

**Rejected — scale the block with the sheet** (e.g. block width = 22 % of frame width). It "looks
right" at every size in a browser and is wrong on paper at every size: the text height would change
with the sheet, so an A0 title block would carry 8 mm labels and an A3 one 2 mm labels, and neither
matches the ISO 3098 sizes that make a print legible. A title block is a physical form, not a layout
percentage. **Rejected — per-size layout tables** (one for A3, one for A1, …). Four tables is four
things to keep in sync and three chances for them to disagree; the issue explicitly asks for *one*
implementation correct at every size, and a fixed physical size is what makes that possible.

### 4.6 Frame too small → loud `TitleBlockError`, not a shrunken block

If `frame_mm()` is narrower than `layout.width_mm`, `title_block_extents_mm` raises
`TitleBlockError` naming both numbers. It does **not** clamp, scale down, or drop cells.

**Why.** A silently shrunk title block is illegible and its illegibility is invisible in code review —
exactly the class of defect the pipeline contract calls a defect. Under the margin set §3.3 assumes,
A4 portrait is the only size where the block exactly fills the frame; every size in P9's test matrix
(A3–A0) has room. If P1 #53 lands with wider margins such that A4 portrait no longer fits, the correct
response is a narrow layout variant (`ISO7200_A4_NARROW`) in a follow-up (§7), not a clamp here.

### 4.7 Unknown keys are rejected in new structures, tolerated in `DrawingMeta`

**Decision:** unknown keys inside a `revisions:` entry or a `numbering:` mapping are a hard error;
unknown top-level `meta.yaml` keys keep going to `extra` (`meta.py:54`) unchanged.

**Why the asymmetry.** Rejecting unknown top-level keys would break the P&ID and BFD data files, which
carry free-form meta (`subtitle`, `doctype`, `logos`, `programme`, `client`, `wrap`) — constraint 1.
But *inside* the new structures there is no legacy to protect, and the failure mode is severe: a
typo'd `descripton:` would leave a revision with no description, silently, which is the exact defect
review item 14 identifies.

**This is one step stricter than the house reference, deliberately.** `components/layout.py` validates
every key it knows, with precise messages and enumerated allowed values
(`layout.py:225-234`: `"groups[{index}].snap.bearing must be one of {sorted(BEARING_MODES)}"`), but it
does **not** reject keys it does not recognise. The message style is the pattern to copy; the
unknown-key rejection is new. Justification: `layout.py`'s configs are large and evolving, where
tolerance is cheap, whereas a revision entry has exactly eight keys and a mistyped one silently
suppresses a mandatory field. Where the cost of a typo is "a revision with no recorded description on an
issued drawing", tolerance is the wrong default.

### 4.8 `by` / `chk` / `app` — and why the tool may never write `app`

**Meanings**, fixed here so two implementers cannot diverge:

- **`by`** — the drafter/author of *this revision's* content. **Required.** A revision nobody authored
  did not happen. It is legitimate for `by` to name an AI-assisted workflow (the house is open about
  using AI assistants), and `isosheet.py:82` already prints `AI-assisted`.
- **`chk`** — the checker: a second person who reviewed this revision's content. **Optional**, because
  an internal DRAFT legitimately has no independent check yet. Blank renders `—`.
- **`app`** — the **approver**: the named responsible engineer who takes professional responsibility for
  the drawing. **Optional in the schema, required by V6 for `for_construction: true`.**

**`app` is never written by any code path in this toolkit. This is the single most important sentence in
this spec.** Concretely, all of the following are requirements on the implementation:

1. `TitleBlockFields.approved_by` is populated **only** from `revisions[-1].app`, read verbatim. There
   is no fallback to `by`, no fallback to `chk`, no `os.getenv("USER")`, no git author, no
   `meta.project`, no default string. Absent → `""` → the cell renders `—` (§3.3, C7).
2. `RevisionRegister.append` takes a `Revision` and does not construct one; the `revision add` CLI
   constructs it with `app=None` and **has no flag that can set it** (§6.13 asserts this by
   introspecting `argparse`).
3. `revision seal` refuses unless `app` was **already non-empty in the file on disk before the command
   ran**, and additionally refuses when `stdin` is not a TTY or `CI` is set in the environment (§6.14).
   A signature that CI can apply is not a signature.
4. No default, template, example, or test fixture in this PR contains a non-empty `app`. The migrated
   example drawings ship `app:` empty (§3.7). A fixture with a plausible approver name is how a
   copy-paste turns into a forged signature.

**Rejected — let the tool fill `app` from a configured "responsible engineer" setting.** It is the
convenient design and it is the one that destroys the gate: the moment an approver can come from
config, an agent editing config can approve a drawing. **Rejected — drop `app` from the model and keep
approval purely out-of-band** (a signed PDF). Then the sheet cannot state who approved it, which is the
one thing a site copy most needs to say. The right shape is: the field exists, it is visible, it is
blank by default, and only a human at a keyboard can fill it.

### 4.9 An ISSUED revision may never be edited in place — and how that is enforced

**Decision:** once a revision is sealed, its content is frozen. A change to an issued drawing is a
**new revision**, always. This is enforced by four mechanisms, not one.

1. **Content seal (§3.6, V7).** A sealed entry carries `seal: sha256:<64 hex>` over the canonical
   serialisation of its own frozen fields **plus its index plus the digest of all preceding entries**
   (`Revision.digest(index=…, prior_digest=…)`). So the seal detects: editing that entry's date,
   description, by, chk or app; reordering the register; deleting an earlier entry; and inserting one
   before it (R5). `validate` recomputes every seal on every run and fails with the entry index and rev
   token.
2. **Append-only API.** `RevisionRegister` is a frozen dataclass with a `tuple` of frozen `Revision`s.
   There is no `update`, no `__setitem__`, no `remove`. `append` returns a new register and refuses to
   produce one whose sealed prefix differs.
3. **No CLI verb that edits.** The subcommands are `list`, `add`, `seal`. There is no `revision edit`
   and no `revision unseal`. Correcting a genuine mistake in a sealed row means a human editing YAML by
   hand and consciously re-sealing — which leaves a diff, in git, with a commit author.
4. **Strengthened `for_construction` gate (V6).** The existing check (`meta.py:74-79`) only compares two
   fields. V6 adds: `for_construction: true` requires a **named approver** and a **valid seal** on the
   latest revision. Without those, `validate` fails.

**This is the one place I am deliberately not backward compatible, and here is the argument.** V6 can
in principle turn a passing drawing into a failing one. It cannot in practice: `for_construction: true`
requires `status: ISSUED` today, and **no drawing in either repo is ISSUED** — both examples are
`CONCEPT`, and the vault's Basin sheet is `CONCEPT — NOT FOR CONSTRUCTION`
(`site-plan.yaml:25`). So the blast radius today is zero. And where the two principles collide,
tightening a safety gate wins over compatibility: the whole point of constraint 3 is that this gate does
not get weaker, and a gate that can be satisfied by two agent-writable booleans with nobody's name on it
is weaker than it looks. §6.17 pins that this is the only intentional compatibility break.

**Rejected — enforce immutability via git** (compare the register against the version at the release
tag). It is the strongest possible check and it is unavailable: `validate` must work on a drawing
directory copied out of the repo, in a tarball, on site, with no `.git`. Seals travel with the file.
**Rejected — a cryptographic signature over the approver's identity.** Correct in the long run, far
beyond this PR, and it needs a key-management answer nobody has asked for. Follow-up (§7).

### 4.10 `document_status` is derived for display, and the status/watermark check stays out

`TitleBlockFields.document_status` is derived from `meta.status_key` via
`STATUS_WATERMARKS[key]["text"]` (`style.py:51-64`) — so `CONCEPT` prints
`CONCEPT — NOT FOR CONSTRUCTION`, matching what the P&ID data already hand-writes at
`SYN-PSK-PID-001.pid.yaml:25`. `from_meta` never accepts a hand-typed status *string* for the new
renderer: the lifecycle key is the input, the display text is derived.

Checking that the *rendered watermark* agrees with `meta.status` is review item 21 and is **out of
scope** (§7) — but deriving the title-block status text here means the title block and the watermark
now share one source, which is most of what item 21 needs.

### 4.11 Numbering enforcement is opt-in per drawing, via a project-level scheme

**Decision:** `validate` enforces numbering **only** when `meta.numbering:` names a scheme. Empty (the
default, and what every existing file has) → the existing `number` non-empty check only.

**Why opt-in and not global.** A global regex breaks `tests/test_toolkit.py:54` (`X-1`) on the day it
merges — that is not a hypothetical, it is a line of code in this repo. More importantly, §2.6
establishes that **there is no adopted convention to enforce**: the vault register explicitly says the
numbering half "stays proposed". Enforcing a grammar this spec largely chose, globally, on every
consumer, would be exactly the "invent a standard and present it as settled" failure that constraint 2
forbids. Opt-in gets the mechanism built and tested now, lets the plant project adopt it immediately
(all its numbers already conform), and leaves the vault-standard promotion where it belongs — with
the owner, informed by a working implementation.

**Rejected — global enforcement with a `--legacy` escape hatch.** Inverts the default so that
compatibility requires action, and the first thing every existing consumer would do is set the escape
hatch. **Rejected — warn globally, error on opt-in.** Tempting, and I nearly specified it. Against it: a
warning on every `validate` run of every drawing, for a convention that is not adopted, trains people
to ignore warnings — and we need warnings to mean something (§3.6 uses them for real signals like
`pre_register` rows). **Recommended follow-up:** once the owner answers O-1/O-2 and the standard is promoted
to *adopted*, a second PR flips the default to the adopted scheme with a one-line migration per drawing.
That sequencing is the point of opt-in.

### 4.12 Sealing requires a TTY and refuses under CI

Recorded separately because it is unusual and an implementer might read it as over-caution.
`revision seal` is the only command in the toolkit adjacent to a signature. Constraint 3 says CI may not
issue a drawing. The cheapest mechanical expression of that is: refuse when `stdin` is not a terminal,
and refuse when `CI` is set. It costs one `if`, it is trivially testable (§6.14), and it makes
"a workflow file could seal a drawing" a thing that cannot happen by accident.

---

## 5. How this design keeps the ISSUED gate intact — summary for a reviewer

A reviewer should be able to check this section against the diff in five minutes.

| Property | Mechanism | Test |
|---|---|---|
| No code path writes `status: ISSUED` | `revision add` writes only `revisions` and `revision`; `revision seal` writes only `seal`. No writer of `status` exists. | §6.11 |
| No code path writes `for_construction: true` | Same — no writer exists in the toolkit. | §6.11 |
| No code path writes `app` | `approved_by` reads `revisions[-1].app` verbatim, no fallbacks; `revision add` has no `--app` flag; `revision seal` requires `app` to pre-exist on disk. | §6.12, §6.13 |
| CI cannot seal / issue | `revision seal` refuses when `CI` is set or `stdin` is not a TTY. | §6.14 |
| `for_construction` needs a human's name | V6 requires `revisions[-1].app` non-empty **and** a valid seal. | §6.9 |
| An issued revision cannot be quietly altered | Seal covers the entry, its index, and the digest of the preceding entries; append-only frozen API; no `edit`/`unseal` verb. | §6.10 |
| No fixture teaches the wrong thing | No default, example, template or test in this PR carries a non-empty `app`. | §6.12 |

The gate gets **stronger**, in one direction only, and only for drawings that would set
`for_construction: true` — of which there are currently none.

---

## 6. Acceptance tests

Mechanically checkable. Each is one `pytest` function in the named file, named for the behaviour it
asserts (house style). "Fails validation" means the named string appears in
`DrawingMeta.validate()` / `ValidationResult.problems`, or the named exception is raised — never
"looks wrong".

Tests use a `FakeFrame` implementing the `PaperFrame` protocol (§3.5) with ISO 216 mm sizes and a
declared margin set, so P9's tests do not wait on #53. When #53 lands, the same tests are re-run
against the real `Sheet` — a parametrised fixture, one line to switch.

### Title block at every size and orientation

**1. `test_title_block_extents_are_180x56mm_at_every_iso_size`** (`tests/test_titleblock.py`)
*Setup:* `FakeFrame` for the eight cases A3/A2/A1/A0 × landscape/portrait; a fully populated
`TitleBlockFields`.
*Action:* `iso7200_title_block(frame, fields)`; parse with `xml.etree.ElementTree`; read
`data-extents-mm` off the single `<g class="title-block">`.
*Expect:* in all eight cases `w == 180.0` and `h == 56.0` exactly (`pytest.approx`, abs=1e-9). The
`(x, y)` differ per case and equal `(frame_x + frame_w - 180, frame_y + frame_h - 56)`.

**2. `test_title_block_sits_inside_the_frame_at_every_iso_size`**
*Setup:* as test 1.
*Action:* compute the block's mm bounding box; compute the frame's.
*Expect:* block right edge `== frame` right edge and block bottom edge `== frame` bottom edge (abs=1e-9,
they are flush by design); block left `>= frame` left; block top `>= frame` top. Eight cases, all pass.

**3. `test_title_block_cell_geometry_equals_the_layout_table`**
*Setup:* one A1 landscape frame.
*Action:* for every `<g class="tb-cell">`, read `data-field` and `data-extents-mm`.
*Expect:* the set of `data-field` values equals `{c.field for c in ISO7200_180MM.cells}` exactly (no
missing cell, no extra cell), and each cell's mm extents equal its `Cell` entry offset by the block
anchor (abs=1e-9). This is how "renders correctly" is asserted without looking at a picture.

**4. `test_title_block_text_is_physically_identical_across_sheet_sizes`**
*Setup:* the same `fields` on A3 landscape and A0 landscape.
*Action:* extract each `<text>`'s content and its font size converted back to mm.
*Expect:* the ordered list of `(data-field, text, size_mm)` triples is **identical** between the two
sheets. Only the device coordinates differ. This is the property that makes one implementation correct
everywhere.

**5. `test_title_block_carries_the_marker_validate_greps_for`**
*Expect:* the literal `class="title-block"` is in the output (pins the `validate.py:24` contract), and
`data-sheet` / `data-orientation` are present.

**6. `test_title_block_raises_when_the_frame_is_narrower_than_the_layout`**
*Setup:* a `FakeFrame` with a 150 mm-wide frame.
*Action:* `title_block_extents_mm(frame)`.
*Expect:* `TitleBlockError`, message containing `150` and `180`. No SVG returned, nothing clamped.

**7. `test_over_long_title_is_an_error_not_an_ellipsis`**
*Setup:* `TitleBlockFields(title="X" * 400, …)` on A1.
*Action:* `iso7200_title_block(...)`.
*Expect:* `TitleBlockError` naming `title`, `400`, and the computed capacity. Assert `"..."` and `"…"`
are **not** present in any output of the new renderer, for any input (the anti-`svg.py:572-576` test).

### Revision register

**8. `test_revision_without_description_fails_validation`** (`tests/test_revisions.py`)
*Setup:* `meta.yaml` with `revisions: [{rev: A, date: 2026-07-15, by: AB}]`, `revision: A`.
*Action:* `DrawingMeta.load(...).validate()`.
*Expect:* exactly one problem, containing `revisions[0].description` and `is required`. Repeat with
`description: ""` and `description: "   "` — same result (strip-then-check). Also assert
`description: NOT RECORDED (pre-register revision)` **without** `pre_register: true` fails, and **with**
it passes while producing one warning.

**9. `test_meta_revision_absent_from_register_fails_validation`**
*Setup:* register `[A, B]`, `revision: D`.
*Expect:* one problem containing `'D'`, `absent from revisions`, and the known tokens `A, B` (V3).
Second case: `revision: A` with register `[A, B]` → a problem containing `is not the latest` and
`latest is 'B'` (V2, the distinct message). Third case: `revision: B` with register `[A, B]` → zero
problems.

**10. `test_register_ordering_and_uniqueness_rules`**
Parametrised: duplicate `rev` → error naming both indices (R2); decreasing dates → error naming both
indices (R1); `rev` going backwards `[A, C, B]` → error (R3); gap `[A, C]` → **no** error;
`revisions: []` → error containing `at least one entry` (R6); a future date → **warning**, zero
problems.

**11. `test_sealed_revision_cannot_be_mutated`**
*Setup:* a register with rev A sealed (seal computed by `Revision.digest`), `status: ISSUED`,
`app: "R. Atimbire"`, `for_construction: true`. Assert it validates clean.
*Action (a):* change `description` in the YAML, re-validate.
*Expect:* a problem containing `revisions[0]`, `sealed`, `must not be edited in place`, and
`add a new revision`.
*Action (b):* reorder `[A, B]` → `[B, A]` with A sealed → seal mismatch problem (R5).
*Action (c):* insert a new entry before the sealed one → seal mismatch problem.
*Action (d):* `RevisionRegister.append` an entry whose token precedes the sealed one → `RevisionError`.
*Action (e):* assert `RevisionRegister` and `Revision` have no `update`/`edit`/`remove`/`__setitem__`
attribute, and that both are `dataclasses.fields`-frozen (`FrozenInstanceError` on assignment).

### Numbering

**12. `test_drawing_number_grammar`** (`tests/test_numbering.py`) — table-driven.

| Number | `SHAPE_ONLY` | Why |
|---|---|---|
| `ARC-CSL-001` | **valid** | the standard's named "existing style" |
| `STA-WTP-GA-001` | **valid** | the standard's own example (bare, no rev) |
| `EXA-CIV-SEC-001` | **valid** | in-repo example |
| `SYN-PSK-PID-001` | **valid** | in-repo example |
| `STB-STA-STC-FOUND-001` | **valid** | 4 segments, one of 5 chars |
| `TST-BFD-001` | **valid** | existing test fixture |
| `STA-WTP-GA-999` | **valid** | serial upper bound |
| `X-1` | invalid | 1-char segment, 1-digit serial — `tests/test_toolkit.py:54` |
| `sta-wtp-ga-001` | invalid | lower-case |
| `STA-WTP-GA-1` | invalid | serial not 3 digits |
| `STA-WTP-GA-0001` | invalid | serial 4 digits |
| `STA-WTP-GA-001_RevB` | invalid | rev in the number; message must name `meta.revision` |
| `STA_WTP_GA_001` | invalid | underscores as separators |
| `STA-WTP-GA-001-` | invalid | trailing separator |
| `STA-WTP-001-GA` | invalid | serial not last |
| `BASINWTP-GA-001` | invalid | 8-char segment |
| `WTP-001` | invalid | only 1 segment before the serial |
| `AAA-BBB-CCC-DDD-EEE-001` | invalid | 5 segments before the serial (max is 4) |
| `STA-123-GA-001` | **valid** | digits are legal inside a segment (supplier prefixes like `4435`, standard line 133) |
| `""` | invalid | empty |
| `STA-WTP-GA-001 ` | invalid | trailing space (no implicit strip on the stored ID) |

*Expect:* `NumberingScheme.validate` returns `[]` for every valid row and exactly one problem naming the
number and the scheme id for every invalid one; `match()` raises `NumberingError` on invalid rows and
returns the segment dict on valid ones.

**13. `test_named_scheme_enforces_segment_vocabulary`**
*Setup:* the §3.4 `demo-water-v1` scheme.
*Expect:* `STA-WTP-GA-001` passes; `ZZZ-WTP-GA-001` fails with a problem naming `site` and listing
`STA, STB, STC, ARC`; `STA-WTP-XX-001` fails naming `drawing_type`; a scheme dict with no `source:`
raises `NumberingError` containing `source is required`.

**14. `test_numbering_enforcement_is_opt_in`**
*Setup (a):* `meta.yaml` with `number: X-1` and **no** `numbering:` key.
*Expect:* `validate()` returns `[]`.
*Setup (b):* the same plus `numbering: shape-only`.
*Expect:* exactly one problem naming `X-1` and `shape-only`.

### The ISSUED gate

**15. `test_for_construction_requires_a_named_approver_and_a_seal`** (`tests/test_issued_gate.py`)
Parametrised on `for_construction: true`, `status: ISSUED`, register present:
`app` empty → problem containing `named approver` and `no tool may supply it`;
`app` set but `seal` absent → problem containing `seal`;
`app` set and `seal` valid → zero problems.
Plus the existing behaviour, unchanged: `for_construction: true` with `status: CONCEPT` → the **same
message text as today** (`meta.py:76-79`), asserted verbatim.

**16. `test_no_toolkit_code_path_writes_status_for_construction_or_app`**
*Action:* run `revision add --rev C --description "…" --by AB` against a temp drawing dir whose
`meta.yaml` is `status: CONCEPT`, `for_construction: false`.
*Expect:* the rewritten YAML has an unchanged `status`, unchanged `for_construction`, `revision: C`, a
new entry with `chk: None` and `app: None`; and a byte-level diff of the file touches only the
`revision:` line and the appended block.

**17. `test_approved_by_comes_only_from_an_explicit_app_value`**
Parametrised over registers where `by` and `chk` are set and `app` is `None` / absent / `""` / `"  "`:
*Expect:* `TitleBlockFields.from_meta(meta).approved_by == ""` in every case, the rendered
`data-field="approved_by"` cell contains the em-dash `—` and does **not** contain the `by` or `chk`
value. Also assert no fixture or example `meta.yaml` in the repo has a non-empty `app`
(walk `drawings/**/meta.yaml` and `tests/`).

**18. `test_revision_add_has_no_app_flag`**
*Action:* build the CLI parser (`cli.build_parser()`), walk to the `revision add` subparser, collect
every `option_strings` entry.
*Expect:* no option string matches `app`, `approve`, `approver`, `sign`, or `issue`
(case-insensitive). This test exists so a future well-meaning PR that adds `--app` fails immediately
with a pointer to §4.8.

**19. `test_revision_seal_refuses_without_a_tty_or_under_ci`**
*Setup:* a drawing dir with `status: ISSUED` and `app: "R. Atimbire"` already in the file.
*Action (a):* invoke `revision seal --rev A` with `CI=1` in `monkeypatch.setenv`.
*Expect:* exit code 2, stderr naming `CI`, and the `meta.yaml` **bytes unchanged**.
*Action (b):* `CI` unset, `sys.stdin.isatty()` patched `False`.
*Expect:* exit code 2, stderr naming a terminal, bytes unchanged.
*Action (c):* `app` empty in the file, TTY simulated, confirmation supplied.
*Expect:* exit code 2, stderr naming `app`, bytes unchanged.

### Backward compatibility and migration

**20. `test_existing_example_drawings_validate_after_migration`** (`tests/test_example_smoke.py`)
*Setup:* the migrated `drawings/example/simple-section` and `drawings/example/synthetic-pid`.
*Action:* `validate_target(dir)` on each (after `render`).
*Expect:* `result.ok is True`, `result.problems == []`, `result.warnings == []`, and the same number of
`checked` entries as before this PR plus the new register/numbering checks.

**21. `test_pre_p9_meta_yaml_still_validates_unchanged`** — **the backward-compat test.**
*Setup:* a `meta.yaml` whose bytes are the **pre-migration** `simple-section/meta.yaml`, committed to
`tests/fixtures/meta_pre_p9.yaml` (no `revisions:`, no `numbering:`, no `sheet:`).
*Action:* `DrawingMeta.load(...)`; `.validate()`; `.title_block()`; `.register()`.
*Expect:* `validate() == []`; `warnings() == []`; `register() is None`; and `title_block()` equals, key
for key, the exact dict
```python
{"project": "drawings-kit example",
 "title": "Concrete-Lined Drainage Channel — Typical Section",
 "drawing_no": "EXA-CIV-SEC-001_RevA",
 "scale": "1:50",
 "date": datetime.date(2026, 7, 15),   # a date OBJECT, not a string — see §2.1
 "revision": "A"}
```
— pinning `meta.py:82-91` behaviour verbatim, including the `datetime.date` that `yaml.safe_load`
produces. Additionally: `DrawingMeta(number="X-1", title="t").validate() == []`
(the `tests/test_toolkit.py:54` fixture keeps passing), and a golden-file assertion that
`Drawing(600, 400, vb, title_block={"title": "T"}, status="CONCEPT").render()` is **byte-identical** to a
stored pre-PR golden.

**22. `test_bfd_and_pid_sheet_output_is_byte_identical`**
*Setup:* the fixtures at `tests/test_bfd.py:9` and `tests/test_pid.py:22`.
*Action:* render both to strings.
*Expect:* byte-identical to goldens captured before this PR. Pins §4.4 — `isosheet.py` is untouched.

**23. `test_title_block_fields_agree_across_the_three_renderers`**
*Setup:* one `DrawingMeta`.
*Action:* build `TitleBlockFields.from_meta(meta)`, `meta.title_block()`, and the isosheet meta dict.
*Expect:* where the three name the same thing they carry the same value — `identification_number` vs
`drawing_no` (modulo the `_Rev` display composition of `meta.py:87`), `title`, `revision`,
`date_of_issue` vs `date`. Pins the §4.4 mitigation.

**24. `test_revision_block_renders_upward_from_the_title_block`** (`tests/test_titleblock.py`)
*Setup:* A1 landscape, a 3-entry register.
*Expect:* `revision-block` `data-extents-mm` has `w == 180.0`, `h == 4 * 5.0` (header + 3 rows), its
bottom edge `==` the title block's top edge (abs=1e-9), and its right edge `==` the title block's right
edge. Row order top→bottom is `A, B, C`, `data-latest-rev == "C"`, and the `C` row is the one adjacent
to the title block. An empty/absent register → `revision_block` returns `("", [])` and the emitted sheet
contains no `revision-block` (C8).

**25. `test_revision_block_overflow_is_visible`**
*Setup:* a 12-entry register, `max_rev_rows=8`.
*Expect:* 7 data rows plus one continuation row whose DESCRIPTION cell text contains
`+ 5 EARLIER REVISIONS`; `data-rows="8"`; the newest entry is present and adjacent to the title block;
one warning naming `12` and `8`. No entry is dropped without the continuation row appearing.

---

## 7. Out of scope

An implementer must **not** do any of the following in this PR. Where a follow-up is wanted, open the
issue and reference it from the PR body.

1. **Do not implement paper space.** Sheet sizes in mm, margins, plot scale, the scale bar and the north
   arrow are **P1 #53**. P9 consumes the `PaperFrame` protocol (§3.5) and tests against a `FakeFrame`.
   If #53's real interface differs, adapt the protocol — do not build a second paper-space model.
2. **Do not port `isosheet.sheet` or `Drawing.render` onto the paper-space title block.** §4.4.
   → **Open follow-up:** "Port `isosheet.sheet` and `Drawing.render` onto P1 paper space + the ISO 7200
   block; retire `svg_title_block`" (depends on #53 and this PR).
3. **Do not delete or `DeprecationWarning` `svg.py::svg_title_block`.** A docstring note only (§3.7).
4. **Do not promote the numbering convention in the vault standard.** `Engineering Drawings as Code.md`
   line 63-64 and `Standards/_index.md:48` say *proposed*. Changing them is the owner's call, in the vault,
   after O-1/O-2 are answered. → **Open follow-up:** "Promote drawing-numbering convention
   proposed → adopted in *Engineering Drawings as Code*" (blocked on O-1/O-2).
5. **Do not touch the vault repo from this PR.** The `basin-site` migration (§3.7) is a separate change
   in the vault, and it needs O-4 answered first. → **Open follow-up:** "Migrate
   `basin-site/site-plan.yaml` to a revision register; allocate a real number for the SITE-GA sheet
   (stop `.replace('WTP-GA','SITE-GA')` at `source.py:112`)".
6. **Do not add a status/watermark consistency check.** That is review item 21. §4.10 gives it a shared
   source; the check itself is a separate issue. → **Open follow-up.**
7. **Do not build a cross-drawing drawing register** (uniqueness of `number` across a set, an index
   sheet, sheet `n of m` computation). P9 validates one drawing at a time. Cross-drawing uniqueness
   belongs with P2's `drawing-set.yaml`. → **Open follow-up:** "Assert drawing-number uniqueness across
   a drawing set".
8. **Do not implement cryptographic signing, key management, or an identity system** for the approver.
   §4.9. → **Open follow-up**, low priority.
9. **Do not add legibility/collision checks** (text overlapping geometry, annotation outside the frame).
   That is **P5**. P9 emits `data-extents-mm` so P5 can do it cheaply; P9 does not do it.
10. **Do not add the provenance hash.** `TitleBlockFields.provenance` is a pass-through that renders `—`
    when empty. Computing it is **P3** (manifest + deterministic bytes).
11. **Do not change `meta.py`'s `extra` behaviour for top-level keys** (§4.7).
12. **Do not add an `--app`, `--approve`, `--sign`, `--issue`, or `--force` flag anywhere.** §4.8. Test
    18 fails if you do.

---

## 8. Risks, open questions, and the migration

### Risks to existing consumers

| Risk | Likelihood | Mitigation |
|---|---|---|
| A new required field breaks an existing `meta.yaml` | **Low** — every new check is gated on an opt-in key (§3.6) | Test 21 pins the pre-P9 file, byte-identical `title_block()` dict, and `X-1` still validating |
| `isosheet`/`bfd`/`pid` output changes | **Low** — `isosheet.py` untouched | Test 22 golden-file byte comparison |
| `Drawing.render()` output changes | **Low** — `svg.py` untouched but for a docstring | Test 21 golden |
| Three title-block implementations drift apart | **Medium** — the real risk of this PR | One data model (§4.4), test 23, and the §7.2 follow-up to consolidate once #53 lands |
| P1 #53's real interface differs from the `PaperFrame` protocol | **Medium** — #53 is unmerged | The protocol is 3 methods + 2 attributes, all of which any paper-space model must have; tests use a `FakeFrame` so P9 lands and is verified independently; adapting is a small edit in one module |
| V6 fails a drawing someone was about to issue | **Very low** — nothing is ISSUED in either repo | Argued and accepted in §4.9; the failure message tells the engineer exactly what to add |
| The `SHAPE_ONLY` grammar rejects a legitimate future number | **Medium** — the evidence base is seven identifiers | Enforcement is opt-in (§4.11), the grammar is config (§3.4), and widening it is a config edit, not a code change |
| Someone "tidies" the `X-1` fixture and hides the opt-in guarantee | Medium | §3.7 item 3 says leave it; test 21 asserts it |

### Open questions for the owner — **flagged, not decided**

- **O-1 — What do the segments of a drawing number *mean*?** The standard gives two examples with
  different field counts (`ARC-CSL-001`, `STA-WTP-GA-001`) and names no fields. Proposed for the plant
  set: `site · facility · drawing_type · serial`, i.e. `STA-WTP-GA-001`. Needs confirmation, plus the
  controlled vocabularies (sites: `STA`/`STB`/`STC`/`ARC`? drawing types: `GA`/`SEC`/`PID`/`BFD`/`DET`?).
  Until answered, the default scheme is shape-only and names nothing.
- **O-2 — Revision identifier convention.** The vault is silent. Is it letters throughout, or letters
  before first issue and numbers after (`alpha-then-numeric`, my recommendation, §4.2)? Are `I`, `O`,
  `Q` skipped? Default is permissive `alpha` until answered.
- **O-3 — ISO 5457 / ISO 7200 particulars.** Confirm the 180 mm title-block width and the margin set
  against a copy of ISO 5457, and the mandatory-vs-optional field list against ISO 7200:2004 Table 1.
  Neither standard's text is in the repo; the field vocabulary in §3.3 is taken from `isosheet.py:80-89`,
  which is *ISO-7200-style*, not a transcription of the standard. If a copy is available the layout
  table should be checked against it before the vault standard is updated.
- **O-4 — The Basin GA's real revision history.** `site-plan.yaml:27` says `revision: B`. What were rev
  A and rev B, when, and by/checked by whom? Needed for the vault migration (§3.7); until answered the
  migration writes the `NOT RECORDED (pre-register revision)` sentinel with a warning on every run.
- **O-5 — Does the SITE-GA sheet get its own number?** `source.py:112` manufactures one from
  `STA-WTP-GA-001` by string replacement. It should be allocated properly (§7.5).

### Migration summary

- **This PR, `workflows`:** two example `meta.yaml` files gain a one-entry `revisions:` block plus
  `numbering: shape-only` and `revision_scheme: alpha`; both numbers already conform, so validation
  outcomes and rendered bytes are unchanged. `tests/test_toolkit.py` is untouched. One new fixture
  (`tests/fixtures/meta_pre_p9.yaml`) plus three goldens.
- **Companion change, the vault (not this PR):** `basin-site/site-plan.yaml` gains a register
  (blocked on O-4); the SITE-GA number is allocated (O-5); `source.py:112`'s string surgery is removed.
- **Later, blocked on O-1/O-2:** promote the numbering convention in the vault standard from *proposed*
  to *adopted*, then a second PR in `workflows` flips the default `numbering:` scheme from empty to the
  adopted scheme, one line per drawing.
