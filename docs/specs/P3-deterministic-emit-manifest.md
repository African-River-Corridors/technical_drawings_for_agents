# P3 — Deterministic emit + output manifest + provenance stamp

**Issue:** upstream #55 · absorbs review items 2, 3, 7
**Status:** specification (not implemented)
**Owner:** `senior-engineer`
**Applies to:** `technical_drawings_for_agents` (`src/technical_drawings_for_agents/`)
**Depends on:** nothing
**Consumed by:** P2 (#54, build DAG — staleness interface), P1 (#53, paper space — stamp placement)

> Line references in this document are against `origin/main` at
> `0c907b7d356f7f869fa7f11947acc5a765e75128`. Every empirical claim below was measured
> on `ezdxf 1.4.4` / `matplotlib 3.11.1` / CPython 3.12.12 / macOS arm64, with
> Graphviz 15.1.0 and LibreOffice 26 (`/opt/homebrew/bin/soffice`). Where the issue text
> is wrong, §2.9 says so.

---

## Constraints this specification is bound by (restated, binding)

1. **Backward compatibility is sacred.** No output changes for inputs that do not opt in.
   This is the PR that *makes byte-parity testing possible*, so its own parity story must be
   airtight: every behaviour in §3.2 is reachable only through an explicit opt-in
   (§3.1), and §5 test 11 asserts current bytes are unchanged without it. The single
   exception is the encoding/newline fix in decision **D3**, which is byte-neutral on the
   reference platform and byte-*correcting* elsewhere; it carries its own parity test.
2. **Never invent a capability.** Where this spec does not know something, §8 records it as an
   open question. An implementer must not guess; a guess is a defect.
3. **The ISSUED gate is untouchable.** Nothing here may let a tool, a CI run, or an agent flip a
   drawing to `ISSUED FOR CONSTRUCTION`. **A provenance stamp is not an approval.** The stamp
   records *what produced these bytes*; it says nothing about whether anyone signed them off.
   `DrawingMeta.validate()`'s `for_construction`/`ISSUED` rule (`meta.py:74-79`) is not read,
   not written, and not weakened by any code in this change. §5 test 15 asserts it.
4. **Determinism: prefer loud failure over a silent no-op.** If an output cannot be made
   reproducible, the tool must *say so in the manifest* (`reproducible: false` with a reason
   code) rather than pretend. If canonical emit is asked for and cannot be delivered
   (unresolvable date, non-finite float, input outside the manifest root), raise
   `ProvenanceError` — never degrade quietly.
5. **House style.** Match `components/layout.py`: frozen dataclasses, exactly one
   module-specific error class, config validated on load with precise messages naming the
   offending file and key, tests named for the behaviour they assert.

---

## 1. Intent

A `technical_drawings_for_agents` build should be an *idempotent function of declared inputs*: given the same
input bytes and the same tool version, two runs on two machines produce the same output bytes,
and every output carries a machine-readable record — `<output>.manifest.json` — of exactly which
input files (by SHA256) and which tool produced it, plus a short provenance hash printed on the
sheet itself. "Good" is when the question **"is this PDF stale?"** is answered by reading eight
characters off the paper and one JSON file, with no rebuild and no eyeballing; and when
"regenerate and diff" is a *real* answer, because a no-change rebuild produces a zero-byte diff
rather than a diff full of timestamps, GUIDs and 17-digit float noise.

The failure this prevents is the one that already happened: a `site-plan.yaml` Rev B landed and
the Basin GA generator sat dead for a week, so a sheet on screen silently described a superseded
design, and nobody could tell — because the sheet recorded nothing about its inputs and a rebuild
diff was pure noise. Secondarily it prevents a subtler class: a rotation recomputed on a different
libm, or a dict ordered by a different `PYTHONHASHSEED`, producing a "changed" register or
GeoJSON that encodes no design change at all — training reviewers to ignore drawing diffs, which
is how a real change gets waved through.

---

## 2. Current state — audited sources of non-determinism

Nothing in `src/technical_drawings_for_agents/` calls `datetime`, `time`, `random` or `uuid`. Every wall-clock
value in an output today arrives from a **dependency** or from a **hand-typed literal in a
generator**. That distinction shapes the whole design.

### 2.1 Wall-clock timestamps and random GUIDs written by `ezdxf` — *proven*

`dxf.py:157` (`self.doc.saveas(path)`) is the only DXF write path. `ezdxf.document.Drawing`
`_update_metadata()` unconditionally stamps the wall clock and re-rolls a random GUID on every
save. Two saves of geometrically identical documents, 1.1 s apart, differ in **6 lines of 13158**:

| DXF item | run A | run B |
|---|---|---|
| `$TDCREATE` (line 495) | `2461246.969513889` | `2461246.969525463` |
| `$TDUPDATE` (line 503) | `2461246.969513889` | `2461246.969525463` |
| `$FINGERPRINTGUID` (line 935) | `{27AFE759-…}` | `{8F1DBC0D-…}` |
| `$VERSIONGUID` (line 939) | `{B93B11E3-…}` | `{E9EB9B13-…}` |
| ezdxf marker XRECORD ×2 (lines 6591, 13153) | `1.4.4 @ 2026-07-24T22:16:06.345933+00:00` | `1.4.4 @ 2026-07-24T22:16:07.458754+00:00` |

So the issue's "no wall-clock timestamps" requirement is *not* satisfiable by auditing our own
code — the timestamps are ezdxf's. **Confirmed fix:** `ezdxf` ships a supported option,
`ezdxf.options.write_fixed_meta_data_for_testing` (read in `ezdxf/document.py`
`_update_metadata` and `ezdxf_marker_string`). With it set, both `$TD*` vars become
`2451545.0` (2000-01-01), both GUIDs become `{00000000-0000-0000-0000-000000000000}`, and the
marker becomes a constant. Measured: **two saves 1.1 s apart are byte-identical**
(`sha256[:16] = f55f5f75238c5163`, 0 differing lines). `$HANDSEED` is already stable (`91`)
because handle allocation is a pure function of construction order.

The DXF is ASCII (`0` CRLF, `12986` LF, `0` non-ASCII bytes) with `$DWGCODEPAGE = ANSI_1252`, so
it is amenable to text canonicalisation as a fallback — but the option above is cleaner and is
what §3.2.6 mandates. A post-hoc textual scrub of *only* the ezdxf marker string is **not**
sufficient (measured: still differs, because the GUIDs and `$TD*` remain).

Every committed example DXF carries a real wall-clock stamp today — e.g.
`drawings/example/simple-section/out/EXA-CIV-SEC-001.dxf:494-496` holds
`$TDCREATE / 40 / 2461237.6464236113`. Non-reproducibility is already baked into the repo's
artifacts.

### 2.2 Unbounded float repr

Three distinct classes, all reaching bytes:

* **`ezdxf` writes `str(float)` verbatim.** Measured via `TagWriter.write_tag2(20, …)`:
  `1.5666666666` → `1.5666666666`; `0.1+0.2` → `0.30000000000000004`; `1/3` →
  `0.3333333333333333`; `-0.0` → `-0.0`. So every coordinate handed to `dxf.py:49-66,
  110-151` lands at full shortest-repr precision (up to 17 significant digits). A stray
  `0.010000000000000002` already exists in the committed example DXF at line 4058.
* **GeoJSON writes raw floats.** `components/emit.py:188-193` (`_coord`, `_coords`) returns
  `[point[0], point[1]]` with no rounding, and both writers —
  `components/cli.py:73` and `components/cli.py:262-272` — call
  `json.dumps(collection, indent=2)`. UTM eastings/northings pass through
  `components/place.py:45-46`, which applies `math.cos` / `math.sin`. libm is **not**
  bit-identical across platforms (macOS Accelerate vs glibc), so a rotated coordinate can
  differ in its last ulp between machines and surface as a phantom 17-digit diff. This is
  the strongest argument for rounding at the emit boundary (§3.2.2), not merely for tidiness.
* **SVG f-strings with no format spec.** `isosheet.py:34` (`text`) and `isosheet.py:38`
  (`rect`) interpolate `x`, `y`, `w`, `h` with **no** format spec, so any computed float lands
  at full repr. Every ISO-sheet call site is a candidate: `bfd.py:308-312` (`ty = cy -
  (len(lines) - 1) * 4.8`, then `ty + i * 9.5 + 3`), `bfd.py:337-341` (`cx`/`cyl`),
  `bfd.py:359` (`ly = yb + LANEH / 2 - (len(rows_txt) - 1) * 7`), `bfd.py:373-388` (edge
  routing arithmetic: `sy + NH / 2`, `(sx + NW / 2 + tx - NW / 2) / 2`), `isosheet.py:152`
  (`ax, ay, aw, ah`), `isosheet.py:157-158` (`SW / 2`), `isosheet.py:165` (`tbx, tby`),
  `isosheet.py:104-107` (`cx0 + half / 2`, `y + h / 2 - 1`). The current literals happen to
  keep most of these at 1–2 decimals; nothing enforces it, and one changed constant is
  enough to spray 17-digit values across a sheet.

  `svg.py` is better but not uniform: geometry uses `:.1f` (`svg.py:171, 179, 186, 193, 199,
  208, 210`), but `stroke_width` (`svg.py:172, 180, 187, 194, 200, 204`) and `font_size`
  (`svg.py:210`) are interpolated bare, and `svg_wrap` (`svg.py:637-641`) interpolates
  `width`/`height` bare. `_fmt_measure` (`svg.py:159-162`) uses `:g`, i.e. **6 significant
  digits** — a distinct and inconsistent rule, used for scale-bar tick labels
  (`svg.py:486`). `svg_status_watermark` (`svg.py:619`) computes `font_size` from
  `min(width, height) / max(8, len(label)) * 1.6` and formats `:.1f` (fine), and the rotation
  angle from `math.atan2` at `:.1f` (fine, but libm-derived — see D2).

### 2.3 Dict / set iteration order reaching output — *proven reachable*

`svg.py:151-156` `_pattern_names` does `list(patterns)` for any non-`str` iterable, and
`svg.py:224-240` emits one `<pattern id=…>` per name **in that order**. A caller passing a
`set` therefore gets `<defs>` in `PYTHONHASHSEED`-dependent order. Measured with
`{'concrete','water','soil','rockfill'}`:

```
PYTHONHASHSEED=1 -> ['water', 'rockfill', 'soil', 'concrete']
PYTHONHASHSEED=2 -> ['concrete', 'rockfill', 'water', 'soil']
PYTHONHASHSEED=7 -> ['water', 'soil', 'concrete', 'rockfill']
```

No in-repo caller passes a set today (the only call is
`drawings/example/simple-section/source.py:104`, `patterns="concrete"`), so this is **latent,
not live**. It is still a defect: the signature invites it. §3.2.4 closes it.

Everything else that iterates a mapping into output iterates a **module-level literal or an
insertion-ordered dict built from an ordered input**, which is deterministic in CPython ≥3.7 but
only *incidentally*: `dxf.py:44` (`LAYERS.items()` — layer creation order, hence handle order,
hence `$HANDSEED`), `svg.py:224` with `patterns=None` (`list(HATCH_PATTERNS)`), `pid.py:326`
(`placed.values()`), `pid.py:351-352` and `bfd.py:415-417` (`logos.items()`),
`components/emit.py:141-152` (`_properties`), `isa.py:139, 154` (`_DIRV.items()`,
`local_ports.items()`). `bfd.py:266-277` and `bfd.py:322-330` use sets **for membership only**
(`(d, lane) in occupied`), never for iteration — safe. `render.py:295` and `validate.py:103,
112` already sort their globs.

### 2.4 Hand-typed dates in generators — *the issue is right*

* `drawings/example/simple-section/meta.yaml:9` — `date: 2026-07-15`
* `drawings/example/synthetic-pid/meta.yaml:9` — `date: 2026-07-16`
* `…/basin-site/source.py:114` — `date="2026-07-24"`, typed straight into the title block,
  bypassing `meta.yaml` entirely.

These are *editorial* dates, not build timestamps — see decision **D6**, which keeps them so.
A related latent bug: `DrawingMeta.date` is annotated `str` (`meta.py:26`) but `yaml.safe_load`
of `date: 2026-07-15` yields a `datetime.date`, which reaches
`svg_title_block` and is stringified by `textwrap.wrap(str(value))` (`svg.py:572`). Deterministic
today, but the annotation lies, and any future `date.isoformat()` call would `AttributeError` on
a quoted date. §3.3 pins the manifest's own date typing explicitly.

### 2.5 Embedded timestamps in PDF — *proven, format-dependent*

Three PDF backends are in play, with three different answers:

| Backend | Reached from | Reproducible? | Evidence |
|---|---|---|---|
| matplotlib PDF | `render.py:96-105` | **Yes, with `SOURCE_DATE_EPOCH`** | Two runs differ *only* in `/CreationDate (D:20260724231609+01'00')`. With `SOURCE_DATE_EPOCH=1451606400`: `/CreationDate (D:20160101000000Z)` and **byte-identical** (`5ff1d307648f14ba`). matplotlib honours the variable natively; we do not implement date handling for PDF. |
| LibreOffice PDF | `render.py:109-149`, `render.py:204-227` | **No** | Two runs of the same SVG: same length (15991 B), **95 differing bytes**. `/CreationDate(D:20260724231855+01'00')` *and* `/ID [ <A5157325…> …]` (a document ID derived from creation time). `SOURCE_DATE_EPOCH` is **not** honoured — set to `1451606400`, the output still carried the wall clock and still differed. |
| `rsvg-convert` PDF | `…/basin-site/source.py:279` — a *project script*, not the toolkit | **Unknown** | Not installed in the probe environment. Cairo-backed PDF writers normally emit `/CreationDate`. Recorded as open question **Q1**; the tool must not claim it either way. |

matplotlib PNG (`render.py:96-105`, `ext == "png"`) is **already byte-identical run to run**
(`ad3561797c3f89c0` twice); its chunks are `IHDR`, `tEXt`, `pHYs`, and the `tEXt` is
`Software: Matplotlib version…` — no timestamp. It does pin the matplotlib version into the
bytes, so PNG identity holds across runs but not across toolchain versions (see D8).

Both matplotlib backends additionally write `/Producer (Matplotlib pdf backend v3.11.1)` and
`/Creator (Matplotlib v3.11.1, …)`, so PDF byte-identity is only claimed **for a pinned
toolchain**. That is why the manifest records the toolchain (§3.3).

### 2.6 Base64 of a raster

`…/basin-site/source.py:120` embeds a 3.93 MB ortho as one inline
`<image href="data:image/png;base64,…">`; `isosheet.py:55-60` (`img_data_uri`) does the same for
logos, reached from `bfd.py:412-417` and `pid.py:348-352`. Base64 is a pure function of the
bytes, so this is **not itself** non-deterministic. The real hazards are:

* the raster is **gitignored** (`…/basin-site/.gitignore:3`, `geo/`), so the input that
  dominates the output bytes is not in version control at all — exactly what the manifest must
  capture (§3.3, `tracked: false`);
* `…/basin-site/.gitignore:6` also ignores `out/*.svg`, so the *only* committed artifact is the
  non-reproducible `rsvg-convert` PDF;
* if the raster is regenerated by GDAL with different parameters the whole SVG changes for
  reasons no diff can explain without a manifest. (Inspected `geo/ga_backdrop.png`: chunks are
  `IHDR` + `IDAT` only — no `tIME`, no `tEXt` — so the raster itself is at least free of
  embedded timestamps. Raster regeneration determinism is **P8's** problem, not ours.)

### 2.7 Text writes without explicit encoding or newline

`…/basin-site/source.py:275` — `svg_path.write_text(d.svg())`, with neither `encoding=` nor
`newline=`. Python then uses `locale.getpreferredencoding(False)` and translates `\n` to
`os.linesep`. On a non-UTF-8 locale this mojibakes or raises; on Windows it doubles every
newline. The toolkit's own writers are better (`components/cli.py:73, 78, 158-165, 231-233,
262-272`; `bfd.py:228, 233, 402`; `pid.py:379` all pass `encoding="utf-8"`) but none pass
`newline="\n"`, so they inherit `os.linesep` translation. See **D3**.

### 2.8 Register rounding — already correct, and a precedent to match

`components/cli.py:237-250` (`_placement_instance`) already does `round(x, 3)` on origin, size
and rotation before `dump_placements_register` (`components/layout.py:452-470`) writes
`yaml.safe_dump(..., sort_keys=False, default_flow_style=None, width=100)`. That is exactly the
mm/millidegree rule this spec generalises, and its choice of `round()` is the precedent §3.2.2
adopts verbatim so the register's bytes do not move.

The register header (`layout.py:460-466`) interpolates `source` — supplied as `export.name` or
`f"{layout.source.name} (effective: post-snap)"` (`components/cli.py:159-164, 232`), i.e. a bare
filename, not an absolute path. Machine-independent already. Good; keep it.

### 2.9 Where the issue text is wrong — corrections

1. **"sorted attributes" is wrong and would break parity.** XML attribute order in this
   codebase is fixed *by the f-strings that write it* (`svg.py:168-212`, `isosheet.py:32-38`) —
   it is already deterministic. Sorting attributes would rewrite the bytes of every existing
   SVG for zero determinism gain. The real ordering defect is **element** order from an
   unordered container (§2.3), not attribute order. This spec therefore **forbids sorting
   attributes** (decision **D4**) and instead forbids unordered containers reaching output.
2. **"Wall-clock dates … in generators" conflates two things.** There are no wall-clock calls
   in the toolkit; the dates in generators are hand-typed *editorial* dates (§2.4), while the
   wall clock enters from `ezdxf` and the PDF backends (§2.1, §2.5). The fixes are
   different and must not be merged: **D6** keeps editorial dates editorial; §3.2.6 and §3.2.7
   neutralise the dependency timestamps.
3. **"Every output gets `<name>.manifest.json`" needs a rule for what "output" means.** A
   single generator run emits SVG + DXF + PDF + PNG + GeoJSON. Decision **D5** settles it.
4. **"the resolved build graph node"** (issue, Change bullet 2) cannot be produced by this PR
   — the build graph is P2 (#54) and does not exist yet. This spec defines the *field and its
   contract* (`build.node`, nullable, §3.3) and leaves it `null` until P2 populates it. Naming a
   field we cannot fill is honest; inventing a graph to fill it is not.

---

## 3. Design

One new module, `src/technical_drawings_for_agents/provenance.py`, with one error class
`ProvenanceError(ValueError)` — matching `LayoutError` in `components/layout.py:40-42`. No
existing module gains a second error class. Canonical formatters live in the same module so
there is no import cycle between "how we format" and "how we fail".

### 3.1 The opt-in surface

Canonical emit and manifest writing are reached **only** by explicitly constructing an
`EmitPolicy` and passing it. There is no global switch, no `TECHNICAL_DRAWINGS_FOR_AGENTS_*` byte-affecting
environment variable, and no implicit default (decision **D1**).

```python
@dataclass(frozen=True)
class EmitPolicy:
    """How to canonicalise bytes on write. Constructing one is the opt-in."""

    precision_m: int = 3          # metres      -> mm
    precision_mm: int = 2         # paper mm    -> 10 um
    precision_px: int = 1         # SVG user units (matches today's ":.1f")
    precision_deg: int = 3        # bearings / rotations (matches register today)
    precision_ratio: int = 3      # opacities, stroke widths, dimensionless
    precision_font: int = 2       # font-size
    source_date_epoch: int | None = None   # resolved, never read from env here
    normalise_negative_zero: bool = True
    fixed_dxf_metadata: bool = True
    trailing_newline: bool = True

    def __post_init__(self) -> None: ...   # validate; see below
```

Validation on construction, with precise messages (house style):

* every `precision_*` must be an `int` in `0..9` → otherwise
  `ProvenanceError("EmitPolicy.precision_m must be an int in 0..9, got 3.5")`;
* `source_date_epoch`, if not `None`, must be an `int >= 0` → otherwise
  `ProvenanceError("EmitPolicy.source_date_epoch must be a non-negative int (seconds since "
  "the Unix epoch, UTC), got '1451606400'")` — note a `str` is rejected, not coerced.

`EmitPolicy.from_environment()` is the *only* place `SOURCE_DATE_EPOCH` is read. It returns a
policy with `source_date_epoch` resolved per §3.2.8 and raises `ProvenanceError` on a malformed
value. A caller that never calls it never sees the variable.

`DrawingMeta` gains one optional key, `deterministic: bool = False`
(add to `meta.py:29` field list and to the `known` set at `meta.py:40-52`). It is a *declaration
by the drawing*, read by the CLI and by P2 to decide whether to construct a default
`EmitPolicy()` for that drawing. The library never reads it implicitly. Absent → `False` →
today's behaviour, byte for byte.

### 3.2 Canonical emit rules

#### 3.2.1 The one formatting primitive

```python
def fmt(value: float, decimals: int, *, normalise_negative_zero: bool = True) -> str:
    """Canonical fixed-point text for a float. The ONLY float->text path in canonical emit."""
```

Semantics, exactly:

1. If `value` is not finite (`math.isfinite` is `False`) raise
   `ProvenanceError("cannot canonically emit non-finite value nan")`. Never write `nan`,
   `inf`, `Infinity` — `json.dumps` emits bare `NaN`/`Infinity`, which is not valid JSON, and a
   drawing containing `inf` is a defect that must surface (measured: `json.dumps(float('nan'))`
   → `NaN`).
2. Compute `text = format(float(value), f".{decimals}f")` — CPython's `format` on a `float`,
   nothing else.
3. If `normalise_negative_zero` and `text` matches `-0(\.0*)?$`, strip the leading `-`.
4. Return `text`. Always fixed-point: `format(5.0, ".3f")` → `"5.000"`, never `5` or `5e0`.

```python
def q(value: float, decimals: int, *, normalise_negative_zero: bool = True) -> float:
    """Canonical rounded float for numeric (JSON/YAML/ezdxf) output."""
```

1. Same non-finite check as `fmt`.
2. `out = round(float(value), decimals)` — the CPython builtin, nothing else.
3. If `normalise_negative_zero` and `out == 0.0`, return `0.0` (this converts `-0.0` to `0.0`;
   measured: `round(-0.0001, 3)` is `-0.0`, and both `json.dumps` and `yaml.safe_dump` write
   `-0.0`).
4. Return `out`.

`fmt` and `q` must agree numerically: `float(fmt(v, n)) == q(v, n)` for every finite `v`.
Measured over 200 000 uniform samples in `[-1e6, 1e6]` at `n=3`: **0 mismatches**. An
implementation whose `fmt` and `q` disagree is wrong; §5 test 9 asserts it.

#### 3.2.2 Precision per unit

| Quantity | Decimals | Where | Rationale |
|---|---|---|---|
| model metres | 3 (mm) | DXF coordinates, GeoJSON coordinates, register `origin_utm`/`size_m` | Review item 7. mm is finer than any survey input and coarser than libm's last ulp, so it absorbs §2.2's platform drift. Matches `components/cli.py:242-246` exactly. |
| paper millimetres | 2 (10 µm) | P1's paper space | 10 µm is ~1/2500 of a plotter dot at 600 dpi; two decimals keeps mm values short. Reserved field — P1 uses it, P3 does not emit paper mm. |
| SVG user units | 1 | all `svg.py` / `isosheet.py` geometry | **Chosen to match today's `:.1f`** so canonicalising an SVG that already uses `svg.py` primitives is a no-op on those attributes. Parity by construction. |
| degrees | 3 | rotations, bearings, `transform="rotate(...)"` | Matches `round(placement.rotation_deg, 3)` (`components/cli.py:243`) and `SNAP_REPORT_MIN_DEG = 1e-3` (`components/layout.py:200`) — 1 millidegree is already the codebase's "worth reporting" threshold. |
| ratios / stroke widths / opacities | 3 | `stroke-width`, `opacity` | Closes §2.2's bare interpolations without inventing a new scale. |
| font sizes | 2 | `font-size` | Today's literals include `7.5` and `8.5`; 2 dp preserves them and bounds a computed one (`svg.py:619`). |
| areas, volumes, quantities | — | — | **Out of scope.** Quantities emit is review item 29, not absorbed here. Do not add a precision for them; open an issue. |

`_fmt_measure` (`svg.py:159-162`, `:g`, 6 significant digits) is a *label* formatter for
human-readable scale-bar ticks, not a geometry formatter. It is **not** replaced (D9): P1 (#53)
owns derived scale-bar labels.

#### 3.2.3 Encoding, newlines, trailing newline

Every canonical text write goes through:

```python
def write_text_canonical(path: Path, text: str, policy: EmitPolicy) -> Path:
    """UTF-8, no BOM, LF-only, optional single trailing newline. Creates parents."""
```

* encoding `utf-8`, never a locale default; no BOM;
* `newline="\n"` explicitly, so no `os.linesep` translation on any platform;
* if `policy.trailing_newline`, ensure exactly one trailing `\n` (append if missing, collapse a
  run of trailing newlines to one);
* `path.parent.mkdir(parents=True, exist_ok=True)` first, matching `dxf.py:156`;
* writes are **atomic**: write to `path.with_suffix(path.suffix + ".tmp")` in the same
  directory, then `os.replace`. Rationale: a build interrupted mid-write must not leave a
  truncated sheet next to a valid manifest that claims a digest for it.

Non-canonical (default) paths keep `write_text(..., encoding="utf-8")` as today, **with
`newline="\n"` added** — see **D3**.

#### 3.2.4 Ordering

* **Element order** is the order the caller supplies. Any public function that accepts a
  collection of names/features and emits one element each must reject an unordered
  container: `svg_pattern_defs` / `_pattern_names` (`svg.py:151-156, 218-305`) raises
  `ValueError("patterns must be a str or an ordered sequence, not a set — set iteration order "
  "varies with PYTHONHASHSEED and would make the <defs> block non-deterministic")` on `set` or
  `frozenset`. This is a *new rejection of input that was previously accepted*: it is safe
  because no in-repo caller passes a set (§2.3) and because it converts a silent
  non-determinism into a loud error, which rule 4 requires. It is `ValueError`, not
  `ProvenanceError`, because it must fire whether or not the caller opted in — `svg.py` must
  not import `provenance.py`.
* **Attribute order is NOT sorted** (D4).
* **JSON object keys**: manifest and canonical GeoJSON are written with `sort_keys=True`.
  Existing GeoJSON writers keep `sort_keys=False` unless the caller opts in (D10).
* **YAML**: `sort_keys=False` everywhere, unchanged — `dump_placements_register`
  (`components/layout.py:467-469`) relies on declaration order for readability, and P10 (#62)
  owns canonical register *sorting*. Do not touch it.

#### 3.2.5 SVG

`canonical_svg(text: str, policy) -> str` is **not** a re-serialiser. It does not parse or
rewrite markup — a regex or XML round-trip over generated SVG is a large behavioural risk for a
small gain. Instead:

* the `svg.py` / `isosheet.py` primitives gain an **optional** `policy: EmitPolicy | None =
  None` keyword. When `None` (the default), the f-string is byte-for-byte what it is today. When
  a policy is passed, every interpolated float goes through `fmt` at the precision from
  §3.2.2. This is the mechanism by which §2.2's bare interpolations get bounded, and it is
  opt-in per call site.
* `Drawing` (`svg.py:648-691`) gains `policy: EmitPolicy | None = None`; `Drawing.render()`
  passes it to `svg_border`, `svg_status_watermark`, `svg_title_block`, `svg_wrap` and to the
  provenance stamp (§3.2.9). Elements the caller `add()`ed as raw strings are passed through
  untouched — the toolkit cannot canonicalise a string a generator hand-built, and must not
  pretend to. Generators that hand-build markup (`…/basin-site/source.py:123, 149, 224, 228`)
  are the migration surface in §7.

#### 3.2.6 DXF

`DxfBuilder` (`dxf.py:32-158`) gains `policy: EmitPolicy | None = None` on `__init__`.

* When a policy is present, **every coordinate is quantised on entry** to the builder — in
  `line`, `polyline`, `circle`, `text`, `linear_dim`, `insert` — via `q(v, policy.precision_m)`
  for positions and lengths and `q(v, policy.precision_deg)` for `rotation`. Quantise on entry,
  not on save, because `ezdxf` computes derived geometry (e.g. `dim.render()` at `dxf.py:89`
  builds a dimension block from the points) and it must derive from the values that will be
  written. `radius` and `height` are metres → `precision_m`. `scale` in `insert` is a ratio →
  `precision_ratio`.
* `save()` (`dxf.py:154-158`) sets `ezdxf.options.write_fixed_meta_data_for_testing = True`
  for the duration of the save **only**, restoring the prior value in a `finally` (it is a
  process-global option; leaking it would silently change every other DXF written in the same
  process, including by another tool in the same run). Implement as a small
  `contextlib.contextmanager`. Guard the attribute with `hasattr` and, if absent, raise
  `ProvenanceError("this ezdxf build (x.y.z) has no options.write_fixed_meta_data_for_testing; "
  "a reproducible DXF cannot be produced — pin ezdxf>=1.1")` — loud, not silent.
* When `policy is None`, `save()` touches no global and quantises nothing. Byte-identical to
  today.
* `pyproject.toml` gains no new runtime dependency.

#### 3.2.7 PDF / PNG

`render.py` gains `policy: EmitPolicy | None = None` on `render_dxf`, `render_svg`,
`render_py`, `render_source`, threaded to the backends.

* **matplotlib** (`render.py:93-106`): when a policy with a `source_date_epoch` is present,
  set `SOURCE_DATE_EPOCH` in the process environment around the `fig.savefig` calls
  (save/restore in a `finally`), because matplotlib reads it at save time. Proven sufficient
  (§2.5). Do **not** additionally pass `metadata={"CreationDate": ...}`; one mechanism, and it
  is the standard one.
* **LibreOffice** (`render.py:109-149, 204-227`): PDF from this backend is **declared
  non-reproducible**. The manifest records
  `output.reproducible: false` with `reason: "libreoffice-pdf-embeds-creationdate-and-docid"`
  (see D8 for why we declare rather than normalise). PNG from this backend: **unknown** — open
  question **Q2**; until measured, it is declared `reproducible: null` with
  `reason: "unmeasured"`, which reads as "we do not know", not as "yes".
* **matplotlib PNG**: reproducible (measured) — `reproducible: true`.

#### 3.2.8 How dates are sourced

Two different clocks, never conflated (D6):

* **The editorial date** on the sheet (`meta.date`, `svg_title_block(date=...)`) is the
  responsible engineer's date. `provenance.py` never sets it, never overrides it, never
  defaults it to today. If `meta.date` is empty the title block shows an empty DATE field, as
  today.
* **The build timestamp** embedded in output *formats* is `policy.source_date_epoch`.
  `EmitPolicy.from_environment()` resolves it, in order:
  1. `os.environ["SOURCE_DATE_EPOCH"]` if set. Must match `^[0-9]+$` after `.strip()`,
     otherwise `ProvenanceError("SOURCE_DATE_EPOCH must be a non-negative integer number of "
     "seconds since the Unix epoch, got 'yesterday'")`. Per the reproducible-builds
     convention the value is UTC seconds.
  2. otherwise the newest **committer** date across the manifest's inputs:
     `git -C <root> log -1 --format=%ct -- <path>` for each input, take the maximum. An input
     that is untracked or not in a work tree contributes nothing.
  3. otherwise `None` — which means "no build timestamp available". A backend that needs one
     (matplotlib PDF) then writes its own wall clock and the manifest records
     `reproducible: false, reason: "no-source-date-epoch"`. **It does not silently invent
     one**, and it does not fail the build: a wall-clock PDF that *declares itself*
     non-reproducible is more useful than no PDF (rule 4 requires declaring, not aborting; and
     the standard's rule 6 requires that every run ends in a reviewable PDF).

`git` is invoked as `subprocess.run([...], capture_output=True, text=True, timeout=10,
check=False)` with a fixed environment addition of `GIT_OPTIONAL_LOCKS=0`. A non-zero exit, a
timeout, or `git` not on `PATH` is **not** an error — it degrades to "no git provenance", which
the manifest records explicitly as `null` fields, never as an absent key.

#### 3.2.9 The provenance stamp

`stamp_text(build_digest: str, *, dirty: bool) -> str` returns
`"P:" + build_digest[:8] + ("+" if dirty else "")`.

* `P:` is a fixed, greppable prefix.
* **8 lowercase hex characters** (32 bits) as the issue specifies. Rationale: it must be
  transcribable off a printed sheet by a human reading it into a search box; 8 characters is
  the git-short-hash convention people already have muscle memory for. Collision risk is
  irrelevant here because the stamp is never used as a *lookup key* — it is compared against
  one specific manifest (`manifest.build_digest[:8]`), which carries the full 64 characters.
* The `+` suffix means "built from a dirty tree" (see D7). It is **never omitted**; a dirty
  build that printed a clean-looking stamp would be the worst possible outcome of this whole
  change.

`svg_provenance_stamp(width, height, text, *, margin=24, policy=None) -> str` returns

```html
<g class="provenance-stamp"><text …>P:1a2b3c4d+</text></g>
```

* `class="provenance-stamp"` follows the existing marker convention that `validate.py:23-27`
  greps for (`class="title-block"`, `class="scale-bar"`, `class="status-watermark"`), so tests
  and `validate` can find it with a substring match and no XML parsing.
* Position: **immediately below the title block's bottom-left corner** when a title block is
  present, else **bottom-left inside the border** (`margin + 6, height - margin - 6`),
  anchored `start`, `font-size` 7, colour `COL_CAD_DIM`. It is an *additional* element, not a
  new title-block row: adding a row would change `block_height` at `svg.py:555` and move every
  existing title block's geometry, which parity forbids. **P1 (#53) owns final placement** —
  §6 defines the seam.
* No-title-block case: still stamped, at the fallback position, and the manifest records
  `stamp.placement: "fallback-bottom-left"` (vs `"below-title-block"`) so a reviewer can see
  *why* it is where it is (D11).
* `Drawing.render()` inserts the stamp last, after the title block, when
  `Drawing.provenance_stamp` is set (a plain `str | None`, default `None`). Default `None` →
  no element → parity.

DXF stamp: `DxfBuilder.provenance_stamp(text, position, height=0.12)` adds one `TEXT` entity
on the **existing** `TITLEBLOCK` layer (`dxf.py:27`). It does **not** invent a `PROVENANCE`
layer — the layer table is P7's (#59) domain, and adding a key to `LAYERS` changes layer
creation order, hence handle allocation, hence `$HANDSEED` and every subsequent handle in every
existing DXF. Explicit, opt-in call only.

The stamp is **not** written into PDF or PNG directly; it is present because it was in the SVG
or DXF that was rendered.

### 3.3 The manifest

One file per output artifact, `<output-with-extension>.manifest.json` — e.g.
`out/STA-SITE-GA-001.svg.manifest.json` (D5). JSON, UTF-8, `sort_keys=True`, `indent=2`,
`ensure_ascii=False`, one trailing newline, written by `write_text_canonical`.

```json
{
  "schema": "technical_drawings_for_agents/manifest@1",
  "target": {
    "name": "STA-SITE-GA-001.svg",
    "format": "svg",
    "drawing_number": "STA-SITE-GA-001",
    "revision": "B"
  },
  "inputs": [
    {"path": "03-Resources/.../basin-site/site-plan.yaml",
     "sha256": "9f2c…", "bytes": 12422, "tracked": true},
    {"path": "03-Resources/.../basin-site/geo/ga_backdrop.png",
     "sha256": "4ad1…", "bytes": 3933477, "tracked": false}
  ],
  "params": {"view": "plan", "snap": true},
  "policy": {"precision_deg": 3, "precision_font": 2, "precision_m": 3,
             "precision_mm": 2, "precision_px": 1, "precision_ratio": 3,
             "normalise_negative_zero": true, "fixed_dxf_metadata": true,
             "trailing_newline": true, "source_date_epoch": 1451606400},
  "tool": {"name": "technical_drawings_for_agents", "version": "0.1.0",
           "commit": "0c907b7d356f7f869fa7f11947acc5a765e75128", "dirty": false},
  "build": {
    "digest": "1a2b3c4d…(64 hex)",
    "root": "/home/user/project",
    "root_vcs": {"kind": "git", "commit": "ddcfdd3…", "dirty": true},
    "node": null
  },
  "output": {
    "sha256": "e3b0c4…", "bytes": 5241983,
    "reproducible": true, "reason": null
  },
  "stamp": {"text": "P:1a2b3c4d+", "placement": "below-title-block"},
  "environment": {
    "host": "build-host.local", "user": "engineer",
    "platform": "macOS-15.5-arm64", "python": "3.12.12",
    "libraries": {"ezdxf": "1.4.4", "matplotlib": "3.11.1"},
    "generated_at": "2026-07-24T22:16:06Z"
  }
}
```

Field contract — types, validation, and whether the field is in the digest preimage:

| Field | Type | In digest? | Validation |
|---|---|---|---|
| `schema` | `str`, exactly `"technical_drawings_for_agents/manifest@1"` | **yes** | Any other value on read → `ProvenanceError("unsupported manifest schema 'x'; this build understands 'technical_drawings_for_agents/manifest@1'")`. Never guess-parse a future schema. |
| `target.name` | `str`, non-empty, the output's basename | **yes** | Basename only, no separators. |
| `target.format` | `str`, one of `svg dxf pdf png geojson yaml json dot` | **yes** | Lower-case; unknown → `ProvenanceError` naming the allowed set. |
| `target.drawing_number` | `str \| null` | **yes** | From `DrawingMeta.number` when known; `null` when the target has no `meta.yaml` (e.g. a bare `layout --emit geojson`). Format is **not** validated here — drawing-number rules are P9/#43. |
| `target.revision` | `str \| null` | **yes** | From `DrawingMeta.revision`. |
| `inputs` | `list[object]`, **sorted by `path`**, ≥ 1 | **yes** | Empty list → `ProvenanceError("manifest for 'x.svg' declares no inputs; an output with no declared inputs cannot be checked for staleness")`. Duplicate `path` → `ProvenanceError`. |
| `inputs[].path` | `str`, POSIX, relative to `build.root` | **yes** | Built with `PurePosixPath`, so `/` on every platform. May contain `..` only if the target itself is above some inputs; a path that escapes `build.root` → `ProvenanceError("input '…' is outside the manifest root '…'; widen root= so the manifest stays machine-independent")`. |
| `inputs[].sha256` | `str`, 64 lower-case hex | **yes** | SHA256 of the file's raw bytes, streamed in 1 MiB blocks. A directory or missing file → `ProvenanceError` naming it. Symlinks are resolved and the *target's* bytes hashed. |
| `inputs[].bytes` | `int >= 0` | **yes** | `stat().st_size`. Cheap corroboration of the hash; a mismatch on re-check localises corruption. |
| `inputs[].tracked` | `bool \| null` | **no** | `true`/`false` from `git ls-files --error-unmatch`; `null` when there is no git. Excluded from the digest because tracked-ness is a property of the repo, not of the bytes — but it is **surfaced** by `manifest --check` as a warning, because an untracked input (§2.6) means the output is not reproducible from git alone. |
| `params` | `object`, JSON-scalar leaves only | **yes** | Caller-declared logical build parameters (a view name, a `--no-snap` flag). Values restricted to `str \| int \| float \| bool \| None` and nested `list`/`dict` thereof. A non-JSON-serialisable value → `ProvenanceError`. Floats inside are passed through `q(v, precision_ratio)` before hashing, so a caller's float parameter cannot inject repr noise. |
| `policy` | `object` | **yes** | The `EmitPolicy` fields as written above. Present so the digest is a function of *how* we formatted, not only of what we formatted. |
| `tool.name` | `str`, `"technical_drawings_for_agents"` | **yes** | |
| `tool.version` | `str` | **yes** | `technical_drawings_for_agents.__version__` (`__init__.py:10`). |
| `tool.commit` | `str \| null`, 40 hex | **no** (D7) | `git -C <package dir> rev-parse HEAD`; `null` if the package is not in a work tree (installed wheel). |
| `tool.dirty` | `bool \| null` | **no** (D7) | `git -C <package dir> status --porcelain` non-empty. `null` when `commit` is `null`. |
| `build.digest` | `str`, 64 lower-case hex | — (it *is* the digest) | §3.4. |
| `build.root` | `str`, absolute POSIX path | **no** | Recorded for a human; excluded from the digest so two clones at different paths agree. |
| `build.root_vcs.kind` | `"git" \| null` | **no** | |
| `build.root_vcs.commit` | `str \| null`, 40 hex | **no** | HEAD of the *input* repo. |
| `build.root_vcs.dirty` | `bool \| null` | **no** | Uncommitted changes in the input repo. Feeds the stamp's `+` (D7). |
| `build.node` | `str \| null` | **yes** | **Reserved for P2 (#54).** The build-DAG node name that produced this target. `null` until P2 exists. In the digest because, once P2 populates it, two different DAG nodes producing the same bytes from the same inputs are genuinely different builds. |
| `output.sha256` | `str`, 64 hex | **no** (circular — §3.4) | Of the bytes actually written. |
| `output.bytes` | `int >= 0` | **no** | |
| `output.reproducible` | `bool \| null` | **no** | `true` measured-reproducible; `false` known non-reproducible; `null` unmeasured. `null` must never be rendered to a human as "yes". |
| `output.reason` | `str \| null` | **no** | Required non-`null` when `reproducible` is `false` or `null`. A short kebab-case code from a closed set: `libreoffice-pdf-embeds-creationdate-and-docid`, `no-source-date-epoch`, `external-renderer`, `unmeasured`. `reproducible: false` with `reason: null` → `ProvenanceError` on write. |
| `stamp.text` | `str \| null` | **no** | Derived from `build.digest`; `null` when the target carries no stamp (a GeoJSON has nowhere to put one). |
| `stamp.placement` | `"below-title-block" \| "fallback-bottom-left" \| "dxf-titleblock-layer" \| null` | **no** | |
| `environment.*` | see below | **no** | `host` (`socket.gethostname()`), `user` (`getpass.getuser()`), `platform` (`platform.platform()`), `python` (`platform.python_version()`), `libraries` (a `dict[str, str]` of the versions that actually wrote bytes), `generated_at` (ISO 8601 UTC, second precision, `Z` suffix — **wall clock, deliberately**). |

`environment` is diagnostic and **entirely excluded from the digest**. This is the single most
important structural decision in the manifest (D2): if `host` were hashed, two machines with
identical inputs would stamp different sheets and the stamp would answer "who built it" instead
of "what went into it" — destroying its purpose.

`environment.generated_at` is a wall clock inside a file about determinism. That is
intentional and safe: the manifest is *never* an input to a digest, and it is never rendered
into a drawing. It is what tells a reviewer when someone last ran the build. §5 test 3 asserts
that the manifest's wall clock changing does not change the digest, the stamp, or the output.

### 3.4 The digest algorithm

Two digests, deliberately distinct, because the obvious single digest is circular: the stamp is
printed *into* the sheet, so the sheet's bytes depend on the stamp; therefore the stamp cannot
depend on the sheet's bytes.

* **`build.digest`** — a function of the *inputs and how they are processed*. Computable
  **before** any byte is emitted. This is what the stamp is drawn from.
* **`output.sha256`** — the hash of the bytes actually written, recorded **after** the write.
  Not part of `build.digest`.

`build.digest` is computed as:

1. Build the **preimage object**: take the manifest object and keep *exactly* the keys marked
   "in digest = yes" in §3.3, dropping every other key at every level. Concretely:

   ```python
   preimage = {
       "schema": m["schema"],
       "target": {k: m["target"][k]
                  for k in ("name", "format", "drawing_number", "revision")},
       "inputs": [{"path": i["path"], "sha256": i["sha256"], "bytes": i["bytes"]}
                  for i in m["inputs"]],            # already sorted by path
       "params": m["params"],
       "policy": m["policy"],
       "tool": {"name": m["tool"]["name"], "version": m["tool"]["version"]},
       "build": {"node": m["build"]["node"]},
   }
   ```

   No other key, at any depth, contributes. An implementation that "helpfully" includes one
   more field produces a different stamp and fails §5 test 4.

2. **Sort `inputs` by `path`** using plain Python `str` comparison on the POSIX path
   (i.e. code-point order, `sorted(inputs, key=lambda i: i["path"])`). No locale collation, no
   case folding. Sorting happens once, before hashing, and the manifest stores the sorted order,
   so a reader can recompute the digest from the file as written.

3. **Canonically serialise:**

   ```python
   blob = json.dumps(preimage, sort_keys=True, ensure_ascii=False,
                     separators=(",", ":"), allow_nan=False).encode("utf-8")
   ```

   — recursive key sorting, no insignificant whitespace, real UTF-8 (not `\uXXXX` escapes),
   and `allow_nan=False` so a non-finite float raises rather than emitting invalid JSON.
   Floats inside `params` were already quantised in step 0 (§3.3), so their `repr` is
   short and platform-stable.

4. `build.digest = hashlib.sha256(blob).hexdigest()` — 64 lower-case hex characters.

5. `stamp.text = stamp_text(build.digest, dirty=bool(build.root_vcs.dirty) or
   bool(tool.dirty))`.

**Verifiability is the point.** `verify_digest(manifest: dict) -> bool` recomputes step 1–4 from
a manifest *as read from disk* and compares to `build.digest`. Two independent implementations
that agree on this function agree on every stamp. §5 test 4 pins one full worked vector
(a byte-exact preimage and its digest) into the test suite so a second implementation can be
checked against it without running the first.

`ProvenanceError` is raised by `verify_digest` only on malformed input; a *mismatch* returns
`False`, because a mismatch is a finding to report, not a crash.

### 3.5 Public Python API

New module `src/technical_drawings_for_agents/provenance.py`:

```python
class ProvenanceError(ValueError):
    """Raised when an emit policy, a manifest, or a provenance record is malformed."""


# ---- canonical formatting ------------------------------------------------
@dataclass(frozen=True)
class EmitPolicy: ...                                  # §3.1
    @classmethod
    def from_environment(cls, **overrides) -> "EmitPolicy": ...
    def as_manifest_dict(self) -> dict[str, Any]: ...  # the "policy" block, sorted

def fmt(value: float, decimals: int, *, normalise_negative_zero: bool = True) -> str: ...
def q(value: float, decimals: int, *, normalise_negative_zero: bool = True) -> float: ...
def write_text_canonical(path: Path, text: str, policy: EmitPolicy) -> Path: ...
def write_json_canonical(path: Path, obj: Any, policy: EmitPolicy) -> Path: ...


# ---- provenance records -------------------------------------------------
@dataclass(frozen=True)
class InputRecord:
    path: str            # POSIX, relative to root
    sha256: str
    bytes: int
    tracked: bool | None

@dataclass(frozen=True)
class ToolRecord:
    name: str
    version: str
    commit: str | None
    dirty: bool | None

@dataclass(frozen=True)
class VcsRecord:
    kind: str | None     # "git" | None
    commit: str | None
    dirty: bool | None

@dataclass(frozen=True)
class Manifest:
    schema: str
    target_name: str
    target_format: str
    drawing_number: str | None
    revision: str | None
    inputs: tuple[InputRecord, ...]          # sorted by path
    params: dict[str, Any]
    policy: EmitPolicy
    tool: ToolRecord
    root: Path
    root_vcs: VcsRecord
    node: str | None
    digest: str                              # 64 hex, §3.4
    output_sha256: str | None                # None until the bytes exist
    output_bytes: int | None
    reproducible: bool | None
    reason: str | None
    stamp_text: str | None
    stamp_placement: str | None
    environment: dict[str, Any]

    # -- construction --------------------------------------------------
    @classmethod
    def plan(
        cls,
        *,
        target: str | Path,
        target_format: str | None = None,   # inferred from suffix when None
        inputs: Sequence[str | Path],
        root: str | Path | None = None,     # default: nearest git work tree of target, else target.parent
        params: dict[str, Any] | None = None,
        policy: EmitPolicy | None = None,   # default: EmitPolicy.from_environment()
        meta: "DrawingMeta | None" = None,
        node: str | None = None,
        reproducible: bool | None = True,
        reason: str | None = None,
    ) -> "Manifest":
        """Hash the inputs and compute the digest+stamp BEFORE the output exists."""

    def with_output(self, path: str | Path) -> "Manifest":
        """Return a copy with output_sha256/output_bytes filled from the written file."""

    # -- serialisation --------------------------------------------------
    def to_dict(self) -> dict[str, Any]: ...
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Manifest": ...
    @classmethod
    def load(cls, path: str | Path) -> "Manifest": ...
    def write(self, path: str | Path | None = None) -> Path:
        """Write <target>.manifest.json (default: beside the target). Atomic."""

    # -- interfaces for P2 (#54) --------------------------------------
    @staticmethod
    def path_for(target: str | Path) -> Path:
        """<target>.manifest.json — the one place this filename is constructed."""

    def stale_findings(self) -> tuple["Finding", ...]:
        """Re-hash inputs and the output; return zero findings when current."""


@dataclass(frozen=True)
class Finding:
    severity: str        # "error" | "warn"   (mirrors components/layout.py:111-118)
    code: str            # closed set, see below
    message: str
    def __str__(self) -> str: ...


def verify_digest(data: dict[str, Any]) -> bool: ...
def digest_preimage(data: dict[str, Any]) -> bytes: ...   # exposed for cross-impl testing
def stamp_text(build_digest: str, *, dirty: bool) -> str: ...
def git_provenance(path: str | Path) -> VcsRecord: ...
def sha256_file(path: str | Path) -> tuple[str, int]: ...
```

`Finding.code` is a closed set: `INPUT-MISSING`, `INPUT-CHANGED`, `OUTPUT-MISSING`,
`OUTPUT-CHANGED`, `DIGEST-MISMATCH`, `STAMP-MISMATCH`, `SCHEMA-UNSUPPORTED` (all `error`);
`INPUT-UNTRACKED`, `TOOL-COMMIT-CHANGED`, `TOOL-DIRTY`, `NOT-REPRODUCIBLE`,
`REPRODUCIBILITY-UNMEASURED` (all `warn`). Reusing `Finding` with a `severity`/`code`/`message`
shape mirrors `components/layout.py:111-118` rather than inventing a second reporting vocabulary.

New in `svg.py`: `svg_provenance_stamp(...)`; `Drawing.policy` and
`Drawing.provenance_stamp` fields. Both exported from `__init__.py` alongside the existing
`svg_*` names, and `EmitPolicy` / `Manifest` / `ProvenanceError` added to `__all__`.

New in `dxf.py`: `DxfBuilder(policy=...)`, `DxfBuilder.provenance_stamp(...)`.

### 3.6 CLI surface

One new subcommand, registered in `cli.py:build_parser` next to `validate`:

```
technical_drawings_for_agents manifest <target> [--check] [--json]
```

* `<target>` is an output file (or a directory, in which case every file in it that has a
  sibling `.manifest.json` is processed, sorted by name).
* Default (no flags): print the manifest as a short human report — target, digest, stamp,
  input count, and every input with its short hash.
* `--check`: re-run `stale_findings()`. Exit **0** when there are no findings, **1** when there
  is at least one `error`, **0** with the warnings printed to stdout when only `warn` findings
  exist. Errors go to `stderr`, matching `_cmd_validate` (`cli.py:53-57`) and
  `run_layout` (`cli.py`/`components/cli.py:179-191`).
* `--json`: emit the manifest (or the findings, with `--check`) as canonical JSON on stdout, for
  P2 and CI to consume.
* A missing manifest is exit **2** with
  `error: no manifest beside 'out/x.pdf' (expected out/x.pdf.manifest.json)` — matching the
  existing exit-code convention (2 = usage/not-found, 1 = the checked thing failed).

The existing commands gain **no new flags in this PR** except one on `layout`, which already has
an emit path and is the concrete consumer:

```
technical_drawings_for_agents layout <config> --emit geojson --out <path> --manifest
```

`--manifest` (default off) writes `<out>.manifest.json` with inputs = the layout config, the
placements register, and every component spec resolved by `load_layout`
(`components/layout.py:150-162` already collects them into `Layout.types`), and
`params = {"view": …, "snap": not args.no_snap}`. Without `--manifest`, `layout` behaves exactly
as today. `render`, `bfd`, `pid`, `component` gain nothing yet — see §6.

---

## 4. Behaviour decisions, with rationale

Each decision states what was rejected, because that is what stops two implementers diverging.

### D1 — Opt-in is an object, not an environment variable or a config default

**Decision.** Canonical emit happens only when a caller constructs an `EmitPolicy` and passes
it. There is no `TECHNICAL_DRAWINGS_FOR_AGENTS_DETERMINISTIC=1`.

**Rejected: a global environment switch.** It is the fastest way to violate rule 1 —
CI sets the variable, every existing drawing's bytes change, and the parity test that was
supposed to catch it is running under the same variable. An environment variable that changes
output bytes is precisely the hazard this PR exists to remove.

**Rejected: default `EmitPolicy()` on all emit paths.** Same problem, worse: it changes bytes
for callers who never asked.

`SOURCE_DATE_EPOCH` is the one variable read, and only inside
`EmitPolicy.from_environment()`, which a non-opted-in caller never reaches. That is consistent:
the variable is an industry convention *for builds that have opted into reproducibility*.

### D2 — The digest covers inputs and processing, never the environment

**Decision.** `build.digest` excludes `host`, `user`, `platform`, `python`, library versions,
`build.root`, `environment.generated_at`, and `output.sha256`. §3.4 lists the included keys
exhaustively.

**Rationale.** The stamp must answer *"what went into this sheet?"*. If the machine were in the
digest, two engineers with identical inputs would print different stamps and the answer would
become "who built it" — useless for staleness. Conversely, if the output bytes were in the
digest, the stamp could not be computed before the sheet is drawn (§3.4's circularity).

**Rejected: hash the whole manifest file.** Attractive ("digest of the manifest", as the issue
words it) but it makes the digest a function of the wall clock in
`environment.generated_at` — so a no-change rebuild would change the stamp, i.e. exactly the
noise this PR removes. The issue's phrase is honoured in spirit by `verify_digest`: the digest
*is* recomputable from the manifest alone, because every contributing field is stored in it.

### D3 — Encoding and newline: the one non-opt-in change

**Decision.** Every text write in `technical_drawings_for_agents` passes `encoding="utf-8", newline="\n"`
unconditionally, opt-in or not. Affected: `components/cli.py:73, 78, 158-165, 231-233,
262-272`; `bfd.py:228, 233, 402`; `pid.py:379`.

**Rationale.** These already pass `encoding="utf-8"`; adding `newline="\n"` produces
**byte-identical** output on POSIX (`os.linesep == "\n"`) and *correct* output elsewhere. It
converts an unstated platform dependency into a stated one. Rule 1 is about not changing output
for existing inputs on the platform they are built on — this does not.

**Rejected: gate it behind the policy.** That would leave the default path writing
`\r\n` on Windows forever, and a "deterministic" mode whose determinism depends on the platform
is not determinism.

**Risk, stated:** a hypothetical existing consumer on Windows currently receives CRLF and would
receive LF. There is no such consumer (CI is Linux, authoring is macOS), and CRLF in an ASCII
DXF or an SVG is not required by either format. §5 test 12 pins the reference-platform parity.

### D4 — Attributes are **not** sorted

**Decision.** Contrary to the issue's "sorted attributes", XML attribute order is left exactly
as the f-strings write it.

**Rationale.** §2.9(1): attribute order is already deterministic — it is literal text in a
format string, not a dict traversal. Sorting would rewrite every byte of every existing SVG for
zero determinism gain, and would require an XML round-trip (or a regex over generated markup)
whose risk of mangling `transform`, `d` and `href` values dwarfs the benefit. The requirement
the issue was reaching for is *element* order, which §3.2.4 addresses at its actual source
(unordered containers).

### D5 — One manifest per output artifact, named `<output>.manifest.json`

**Decision.** `out/X.svg` → `out/X.svg.manifest.json`; `out/X.pdf` →
`out/X.pdf.manifest.json`. The full output filename including its extension, plus
`.manifest.json`.

**Rejected: one manifest per build** (`out/build.manifest.json`). A build produces SVG + DXF +
PDF + PNG, and the *stamp on a sheet* must be checkable against *that sheet's* record. A single
per-build file makes "which manifest describes this PDF?" a lookup instead of a sibling, and
makes a partial rebuild ambiguous. Per-build records are P2's (#54) business — it can aggregate
these.

**Rejected: `X.manifest.json` (stem only).** `X.svg` and `X.pdf` would collide on
`X.manifest.json` — a real collision, since `render` emits both from one stem
(`render.py:96-105`).

**Rejected: a hidden `.tdfa/` sidecar directory.** The manifest is a reviewable artifact; a
reviewer should see it next to the PDF in the folder and in the git diff.

`Manifest.path_for()` is the single place this name is constructed, so P2 cannot drift from it.

### D6 — The engineer's date and the build timestamp are different clocks

**Decision.** The title-block DATE keeps coming from `meta.date` (or the generator's literal).
`provenance.py` never writes it. `SOURCE_DATE_EPOCH` drives only *format-embedded* timestamps
(matplotlib's `/CreationDate`, ezdxf's `$TD*`).

**Rejected: derive the title-block date from git or from `SOURCE_DATE_EPOCH`.** Review item 3
says "take the date from git/manifest", and for *build* timestamps this spec does exactly that
(§3.2.8 step 2). But the DATE field on a drawing is an engineering record — the date the sheet
was prepared/checked — and silently rewriting it from a commit date would mean a `git rebase`
changes what a drawing claims about itself. Worse, a sheet whose DATE moved without an engineer
touching it edges toward the territory rule 3 protects. If the sheet has no date, it shows no
date; §8 Q3 asks whether a derived date is wanted, as a separate, visible decision.

### D7 — A dirty tree is visible in the stamp and excluded from the digest

**Decision.** `build.root_vcs.dirty` (input repo) and `tool.dirty` (tool repo) are both recorded
in the manifest. `stamp_text` appends `+` when **either** is true. Neither is in the digest
preimage; `tool.commit` is not either.

**Rationale (why visible).** A build from uncommitted inputs is not reproducible by anyone else,
and that fact must survive onto paper. Omitting it — or recording it only in a JSON file
somebody might not open — would let a dirty-tree sheet look identical to a clean one, which is
the failure mode most likely to embarrass us in front of a counterparty.

**Rationale (why not in the digest).** Including `dirty` would give a bare boolean the power to
change the stamp while the inputs' *hashes* (which are already in the digest and already capture
every uncommitted byte) say nothing changed. The hashes are the ground truth about content;
`dirty` is metadata about the repo. Including `tool.commit` would churn the stamp on every
unrelated tool commit — a docs typo in `technical_drawings_for_agents` would restamp every sheet in the estate —
destroying the stamp's value as "did *my inputs* change?".

**The hole, stated openly.** Two different tool commits at the same `__version__` can produce
different bytes under the same stamp. Mitigations, all present: `output.sha256` in the manifest
catches it (`OUTPUT-CHANGED`), `tool.commit` is recorded and compared by `--check`
(`TOOL-COMMIT-CHANGED`, `warn`), and the repo's own convention is to bump and pin versions
(`CLAUDE.md`, "Version + pin"). **Rejected alternative:** put `tool.commit` in the digest and
accept estate-wide restamping — rejected as strictly worse for the stamp's primary job.

### D8 — LibreOffice PDF is **declared** non-reproducible, not normalised

**Decision.** `output.reproducible: false`, `reason:
"libreoffice-pdf-embeds-creationdate-and-docid"`. The bytes are left alone.

**Evidence** (§2.5): two runs of the same SVG produce PDFs of identical length differing in 95
bytes — `/CreationDate` plus a `/ID` pair — and `SOURCE_DATE_EPOCH` is ignored.

**Rejected: post-normalise the PDF** by same-length byte substitution of `/CreationDate` and
`/ID` (both are fixed-length, so xref offsets would survive). Tempting and probably workable,
but it means shipping a hand-rolled PDF mutator with no PDF parser, on a path whose output is
the *review artifact an engineer signs against*. A rewrite that corrupts one xref produces a
PDF that opens in one viewer and not another — a far worse failure than an honest
`reproducible: false`. It is recorded as follow-up **F3** in §6, where it can be built with a
real PDF library and its own tests.

**Rejected: refuse to emit a LibreOffice PDF at all.** LibreOffice is the *only* backend that
renders block `INSERT`s faithfully (`render.py:16-23` documents that matplotlib silently drops
them). Refusing would trade a reproducibility gap for a correctness gap. Rule 4 says declare;
it does not say abort.

**Consequence to state in the PR:** the acceptance criterion "building twice produces
byte-identical outputs" holds for SVG, DXF, GeoJSON, YAML register, `.dot`, matplotlib PDF (with
`source_date_epoch`) and matplotlib PNG — and **does not** hold for LibreOffice PDF, by
declaration. §5 test 1 is parameterised per format and asserts exactly this split, including
asserting that the non-reproducible case is *declared* rather than merely failing.

### D9 — Rounding is CPython `round()` / `format()`, i.e. round-half-even on the exact binary value

**Decision.** `q` uses `round(v, n)`; `fmt` uses `format(v, f".{n}f")`. `decimal.Decimal` is
forbidden in the emit path.

**Rationale, and why this is the decision most likely to split two implementers.** Both CPython
primitives are correctly rounded from the *exact* binary value of the double, using
David Gay's algorithm — so they are deterministic and identical on every CPython build on every
platform, and they agree with each other (measured: 0 mismatches in 200 000 samples). A naive
`Decimal(v).quantize(Decimal("0.001"), ROUND_HALF_UP)` looks equally principled and **differs**:

| value | `round(v,3)` / `format(v,'.3f')` | `Decimal` `ROUND_HALF_UP` |
|---|---|---|
| `0.0625` | `0.062` | **`0.063`** |
| `0.3125` | `0.312` | **`0.313`** |
| `2.0625` | `2.062` | **`2.063`** |
| `788416.0625` | `788416.062` | **`788416.063`** |
| `-0.0625` | `-0.062` | **`-0.063`** |

Ties at mm precision occur exactly when the metre value is an odd multiple of 1/16 m (62.5 mm),
which *is* representable in binary and *does* occur in real work (a 2.0625 m offset). So this is
not a theoretical divergence. **The rule is: half-even, via the CPython builtins. Not
`ROUND_HALF_UP`. Not `math.floor(x*1000+0.5)/1000`** (which is half-up *and* introduces a second
rounding error in the multiply). §5 test 10 pins the table above as a test vector.

`-0.0` is normalised to `0.0` / `"0.000"` because it is numerically zero, it is a distinct byte
sequence, and its sign depends on which side of zero the arithmetic approached from — pure noise.
(Measured: `round(-0.0001, 3)` is `-0.0`, and both `json.dumps` and `yaml.safe_dump` preserve
the minus sign; `format(-0.04, ".1f")` is `"-0.0"`, so the existing SVGs already contain such
values, which is why normalisation is opt-in.)

`_fmt_measure`'s `:g` (`svg.py:159-162`) is left alone: it formats *label text* for humans, and
P1 (#53) owns derived scale-bar labels.

### D10 — Existing GeoJSON and register writers change only under `--manifest`/policy

**Decision.** `components/cli.py:73` and `components/cli.py:262-272` keep
`json.dumps(..., indent=2)` with `sort_keys=False` and unrounded coordinates unless a policy is
supplied. With a policy: coordinates pass through `q(v, precision_m)`, keys are sorted, and the
file is written by `write_json_canonical`.

**Rationale.** Rounding coordinates and sorting keys both change the bytes of the Basin
`basin_layout.geojson` that `…/basin-site/source.py:78` reads. That file is a live input to a
live sheet; changing it under a caller who did not ask is exactly what rule 1 forbids. The
opt-in path is available immediately via `layout --manifest`.

**Note:** `_placement_instance` (`components/cli.py:237-250`) already rounds to 3 dp, so the
YAML register is *already* canonical in the sense this spec means. It is left byte-identical.
Canonical *ordering* of the register is P10 (#62); do not reorder it here.

### D11 — A sheet with no title block is still stamped

**Decision.** If `Drawing.title_block is None` (the field defaults to `None`,
`svg.py:659`) the stamp is drawn at bottom-left inside the border and the manifest records
`stamp.placement: "fallback-bottom-left"`.

**Rejected: omit the stamp when there is no title block.** Silent omission is the exact failure
rule 4 forbids: a sheet with no stamp is indistinguishable from a sheet built by an older tool,
so the absence carries no information.

**Rejected: hard-fail when there is no title block.** `validate` already requires a title block
on every sheet (`validate.py:23-29`); duplicating that gate inside the emit path means a
generator mid-development cannot get a stamped draft. One gate, in `validate`, is enough.

For an output with nowhere to put a stamp at all (GeoJSON, the register YAML, `.dot`),
`stamp.text` is `null` and the manifest still exists — the manifest is the record, the stamp is
a convenience for paper.

### D12 — `ezdxf`'s global option is set inside a restoring context manager

**Decision.** `write_fixed_meta_data_for_testing` is toggled only around `doc.saveas(...)` and
restored in a `finally`.

**Rationale.** It is a process-global on `ezdxf.options`. `render_py` (`render.py:272-298`)
executes a generator **in-process** via `runpy`, and a single `technical_drawings_for_agents build` (P2) will save
many documents in one process — some opted in, some not. Setting it once at import, or leaving
it set, would silently fix the metadata of DXFs whose callers did not opt in, breaking parity in
the least visible way possible.

### D13 — `git` is best-effort; its absence is recorded, never inferred

**Decision.** Every `git` call is `check=False` with a 10 s timeout. Failure yields `null`
fields (`commit`, `dirty`, `tracked`, `kind`) — never `false`, never an omitted key, never a
raised error.

**Rationale.** `null` means "we do not know"; `false` means "we checked and it is clean". A
build in an exported tarball with no `.git` must not report a clean tree. Distinguishing them is
the difference between a manifest you can trust and one you cannot. `--check` renders `null` as
`unknown`, never as `clean`.

---

## 5. Acceptance tests

Tests live in `technical_drawings_for_agents/tests/`, one new file `tests/test_provenance.py` for the module and
targeted additions to `tests/test_toolkit.py` (SVG/DXF emit) and `tests/test_layout.py`
(the `layout --manifest` path). Names assert behaviour, per house style. All use `tmp_path`.

1. **`test_second_build_with_no_input_change_is_byte_identical_per_format`**
   *Setup:* copy `drawings/example/simple-section/` to `tmp_path`; build once with
   `EmitPolicy(source_date_epoch=1451606400)`; record `sha256` of every artifact; `sleep(1.1)`
   (so a wall clock would differ); build again into a second directory.
   *Assert, per format:*
   * `.svg` — identical.
   * `.dxf` — identical (this is the test that would fail today: §2.1 measured 6 differing lines).
   * `.png` (matplotlib) — identical.
   * `.pdf` (matplotlib) — identical.
   * `.geojson`, register `.yaml`, `.dot` — identical.
   * LibreOffice-rendered `.pdf` — **not** asserted identical; instead assert its manifest has
     `output.reproducible is False` and
     `output.reason == "libreoffice-pdf-embeds-creationdate-and-docid"`. Skip with
     `pytest.mark.skipif(find_soffice() is None)`.
   *Rationale:* the split is the deliverable, not a caveat.

2. **`test_changing_one_input_byte_changes_the_manifest_digest_and_the_stamp`**
   *Setup:* build; capture `build.digest` and `stamp.text`. Change `inputs.yaml`
   `bed_width_m: 2.0` → `2.001` (a real 1 mm change). Rebuild.
   *Assert:* the new `build.digest != old`, `stamp.text != old`, the SVG contains the new
   stamp string and not the old one, and exactly one `inputs[].sha256` changed.

3. **`test_rebuilding_at_a_later_wall_clock_changes_only_the_manifest_timestamp`**
   *Setup:* build, `sleep(1.1)`, build again with the same `source_date_epoch`.
   *Assert:* `environment.generated_at` differs; `build.digest`, `stamp.text`,
   `output.sha256` and the output bytes are all identical. This is the test that pins D2.

4. **`test_digest_matches_the_pinned_cross_implementation_vector`**
   *Setup:* a hard-coded manifest `dict` literal in the test file (two inputs, fixed hashes,
   fixed policy, `node: null`).
   *Assert:* `digest_preimage(m)` equals a pinned `bytes` literal **exactly** (so key order,
   separators and UTF-8 handling are all pinned), and
   `Manifest.from_dict(m).digest == "<pinned 64 hex>"`, and `verify_digest(m) is True`.
   *Assert also:* mutating `m["environment"]["host"]`, `m["build"]["root"]`,
   `m["tool"]["commit"]`, `m["tool"]["dirty"]`, `m["inputs"][0]["tracked"]` or
   `m["output"]["sha256"]` leaves `verify_digest(m) is True`; mutating
   `m["inputs"][0]["sha256"]`, `m["params"]`, `m["policy"]["precision_m"]`,
   `m["tool"]["version"]`, `m["target"]["name"]` or `m["build"]["node"]` makes it `False`.
   *Rationale:* this single test is what makes two independent implementations agree.

5. **`test_the_stamp_on_the_sheet_equals_its_manifest_digest_prefix`**
   *Setup:* build the example with a stamp.
   *Assert:* the SVG contains `class="provenance-stamp"`; the text node inside it equals
   `"P:" + manifest["build"]["digest"][:8]` (plus `+` iff either dirty flag is true); the
   `stamp.text` field equals that same string.

6. **`test_a_dirty_input_tree_marks_the_stamp_and_the_manifest`**
   *Setup:* `git init` in `tmp_path`, commit the example, then modify `inputs.yaml` **without**
   committing. Build.
   *Assert:* `build.root_vcs.dirty is True`; `stamp.text.endswith("+")`; the SVG contains the
   `+`; `manifest --check` reports a `TOOL-DIRTY`-class warning and still exits 0.
   *And the negative:* commit the change, rebuild → `dirty is False`, no `+`.

7. **`test_missing_git_records_unknown_rather_than_clean`**
   *Setup:* build in a `tmp_path` with **no** `.git` anywhere above it (assert that
   precondition first).
   *Assert:* `build.root_vcs == {"kind": None, "commit": None, "dirty": None}`; `tool.commit`
   may be non-null (the installed package may be in a work tree); the stamp has no `+` from the
   root; `manifest --check --json` renders the root VCS as `"unknown"`, and the string
   `"clean"` appears nowhere in its output.

8. **`test_manifest_check_reports_a_changed_input_and_exits_one`**
   *Setup:* build with `--manifest`; then append a byte to one input.
   *Assert:* `technical_drawings_for_agents manifest <out> --check` exits `1`, `stderr` contains
   `INPUT-CHANGED` and the offending relative path. Then restore the input and touch the
   *output* instead → exit `1` with `OUTPUT-CHANGED`. Then delete the manifest → exit `2` with
   the expected-path message.

9. **`test_fmt_and_q_agree_and_are_platform_independent`**
   *Setup:* a fixed list of 64 values spanning UTM magnitudes (`788416.1234999`,
   `322373.5555`), small values (`1e-5`), negatives, exact halves, and `-0.0001`; plus 20 000
   `random.Random(20260724)` samples.
   *Assert:* for every value and `n in (1, 2, 3)`, `float(fmt(v, n)) == q(v, n)`;
   `fmt` output matches `^-?\d+\.\d{n}$`; no output starts with `-0.0` followed only by zeros;
   `q(-0.0001, 3)` is `0.0` and `repr` is `"0.0"` (not `"-0.0"`).

10. **`test_rounding_ties_use_half_even_not_half_up`**
    *Setup:* the exact table in D9.
    *Assert:* `fmt(0.0625, 3) == "0.062"`, `fmt(0.3125, 3) == "0.312"`,
    `fmt(2.0625, 3) == "2.062"`, `fmt(788416.0625, 3) == "788416.062"`,
    `fmt(-0.0625, 3) == "-0.062"`, and each `q(...)` equals the float of the same.
    *Rationale:* an implementation using `Decimal(...).quantize(..., ROUND_HALF_UP)` produces
    `0.063` here and fails. This test is the guard against the most plausible divergence.

11. **`test_existing_generators_produce_unchanged_bytes_when_not_opted_in`** *(backward-compat / parity)*
    *Setup:* commit golden `sha256` values for `drawings/example/simple-section/out/*.svg` and
    `*.dxf` and `drawings/example/synthetic-pid/out/*.svg` as generated by `origin/main` **with
    the DXF's volatile lines masked** (a helper `mask_dxf_volatiles(text)` that replaces the
    six items in §2.1's table with fixed placeholders — because an unmasked DXF cannot be a
    golden file today; that is the very problem being fixed).
    *Action:* run each generator with **no** `EmitPolicy` anywhere.
    *Assert:* SVG bytes hash to the golden value exactly; masked DXF text hashes to the golden
    value exactly; no `provenance-stamp` marker appears; no `.manifest.json` is written; and
    `ezdxf.options.write_fixed_meta_data_for_testing` is `False` after the run (D12).
    *Rationale:* this is the airtight parity story rule 1 demands, and the masking helper is
    itself the evidence that P3 is the change that makes real golden tests possible.

12. **`test_text_writes_are_utf8_lf_regardless_of_locale`**
    *Setup:* write a register and a BFD SVG containing a non-ASCII character (`Ø`, `—`) through
    the toolkit's writers.
    *Assert:* the raw bytes decode as UTF-8, contain no `\r`, contain no BOM, and — for the
    non-opted-in path — are byte-identical to the same content written by
    `write_text(..., encoding="utf-8")` on this platform (pinning D3's parity claim).

13. **`test_set_of_hatch_patterns_is_rejected_rather_than_ordered_arbitrarily`**
    *Setup:* `svg_pattern_defs(vb, patterns={"concrete", "water"})`.
    *Assert:* `ValueError` whose message mentions `PYTHONHASHSEED`. And
    `svg_pattern_defs(vb, patterns=["water", "concrete"])` emits the two `<pattern>` elements in
    that list order (not sorted, not reordered).

14. **`test_non_finite_and_unserialisable_values_fail_loudly`**
    *Assert:* `fmt(float("nan"), 3)`, `q(float("inf"), 3)` and
    `Manifest.plan(..., params={"x": float("nan")})` each raise `ProvenanceError` naming the
    offending value; the message never contains the word "skip"; and no partial output or
    manifest file is left on disk.

15. **`test_provenance_never_alters_the_issued_gate`**
    *Setup:* an example with `status: CONCEPT`, `for_construction: false`; build with a full
    policy, manifest and stamp.
    *Assert:* `meta.yaml` on disk is byte-identical after the build; the emitted sheet's
    watermark is still `CONCEPT — NOT FOR CONSTRUCTION`; `DrawingMeta.load(...).status` is
    `"CONCEPT"` and `.for_construction` is `False`; the manifest contains no key named
    `issued`, `approved`, `for_construction` or `signed`; and
    `validate_drawing_dir(work).ok` is `True` with the same `problems` list as an unstamped
    build. *Rationale:* rule 3, made mechanical — a provenance stamp is not an approval.

16. **`test_input_outside_the_manifest_root_is_a_loud_error`**
    *Setup:* `Manifest.plan(target=tmp_path/"a/out.svg", inputs=[tmp_path/"b/x.yaml"],
    root=tmp_path/"a")`.
    *Assert:* `ProvenanceError` naming the input and mentioning `root=`; no manifest written.

17. **`test_untracked_input_is_recorded_and_warned_not_hidden`**
    *Setup:* `git init`, commit one input, leave a second (a stand-in for
    `geo/ga_backdrop.png`) untracked and `.gitignore`d. Build.
    *Assert:* that input's `tracked is False`; its `sha256` is still recorded;
    `manifest --check` emits an `INPUT-UNTRACKED` warning naming it and exits 0; the digest is
    unchanged if only the `tracked` flag is edited in the manifest (pinning §3.3).

18. **`test_layout_manifest_records_config_register_and_every_component_spec`**
    *Setup:* the existing `test_cli_layout_round_trips_geojson_export_to_register_and_emits`
    fixture (`tests/test_layout.py:456`) plus `--manifest`.
    *Assert:* the manifest's `inputs` paths are exactly the layout config, the placements
    register and every spec in `Layout.types`, sorted by path, with no duplicates;
    `params == {"snap": True, "view": "plan"}`; and running without `--manifest` writes no
    manifest and produces byte-identical GeoJSON to today (D10).

---

## 6. Out of scope — do not build these here

An implementer must **not**:

* **Build the build DAG.** P2 (#54) owns `drawing-set.yaml`, staleness-driven scheduling,
  `--check` over a set, and `--target`/`--force`. This PR provides exactly three seams for it and
  nothing more: `Manifest.path_for(target)`, `Manifest.stale_findings()`, and the nullable
  `build.node` field. Do not add a scheduler, a graph type, or a `build` subcommand.
* **Place the stamp in a real title block.** P1 (#53) owns paper space, the ISO 7200 title
  block and where the stamp sits within it. This PR provides
  `stamp_text()` (the string), `svg_provenance_stamp()` (a marked-up element), the
  `stamp.placement` enum, and the fallback rule (D11). Do not add ISO sheet sizes, plot scales,
  paper-mm geometry, or a title-block row.
* **Reorder or re-key the placements register.** Canonical ordering and stable placement ids
  are P10 (#62), review item 26. `dump_placements_register` keeps `sort_keys=False` and source
  order.
* **Replace `_fmt_measure` or derive scale-bar labels.** P1 (#53), review item 20.
* **Add a layer/lineweight table, or a `PROVENANCE` DXF layer.** P7 (#59), review item 16. Use
  the existing `TITLEBLOCK` layer.
* **Rewrite PDF bytes.** See D8 and follow-up F3.
* **Touch `render.py`'s backend routing.** P4 owns the single tested PDF path, the fidelity
  check and `rsvg-convert`'s status (review items 4, 12, 30). This PR only threads a policy
  through the existing routing and records what each backend can promise.
* **Emit quantities, or add a precision for areas/volumes.** Review item 29.
* **Change `meta.yaml` semantics** beyond adding the optional `deterministic: bool = False`
  key. In particular, do not touch `for_construction`, `status`, or `validate()`.
* **Read a byte-affecting environment variable other than `SOURCE_DATE_EPOCH`** (D1).

Follow-up issues to open (do not implement):

* **F1 — Canonicalise the Basin generator.** `…/basin-site/source.py` writes with
  `write_text` (no encoding, §2.7), hand-builds markup with bare float interpolation
  (lines 123, 149, 224, 228), hand-types `date="2026-07-24"` (line 114), and shells out to
  `rsvg-convert` (line 279). Migrate it to `EmitPolicy` + `--manifest` once this lands.
* **F2 — Reproducibility of `rsvg-convert` PDF** (open question Q1): measure, then either
  honour `SOURCE_DATE_EPOCH` or declare it; and decide whether the toolkit should own that
  path at all (overlaps P4).
* **F3 — Optional PDF timestamp normalisation** for the LibreOffice path, with a real PDF
  library and its own test suite (D8).
* **F4 — A `--reproducibility-report` over a drawing set**, listing every artifact's
  `reproducible`/`reason` so the estate's non-reproducible surface is visible in one place.
* **F5 — Fix `DrawingMeta.date`'s type** (§2.4): annotate and normalise the YAML-date /
  string ambiguity.
* **F6 — Golden-byte fixtures for every example sheet**, now possible because of this PR;
  wire them into CI so any future byte change is a deliberate, reviewed diff.

---

## 7. Risks, and the migration

| Risk | Who breaks | Likelihood | Mitigation |
|---|---|---|---|
| A consumer diffs `out/*.dxf` in git and now sees the fixed `$TDCREATE = 2451545.0` (year 2000) as a "wrong date". | Anyone reading a DXF header for the drawing date. | Medium | The DXF header date was *never* the drawing date — the drawing date is `meta.date` and the title block. Document the fixed sentinel in `README.md` and in the DXF's own metadata. Only opted-in DXFs are affected. |
| `ezdxf` renames or removes `options.write_fixed_meta_data_for_testing` (the name says "for testing"). | Opted-in DXF emit. | Low–Medium | `hasattr` guard with a `ProvenanceError` that names the ezdxf version and says a reproducible DXF cannot be produced (loud, never silent). The ASCII-text fallback in §2.1 is a documented plan B; do not implement it pre-emptively. Pin `ezdxf>=1.1` as now. |
| `--manifest`-produced GeoJSON has rounded coordinates; a downstream consumer relied on full precision. | `…/basin-site/source.py:78` reads `basin_layout.geojson`. | Low | mm is finer than any survey input; and the change is opt-in (D10), so the Basin sheet keeps its current bytes until F1 migrates it deliberately. |
| `svg_pattern_defs` now rejects a `set`. | A caller outside this repo. | Very low | No in-repo caller passes a set (§2.3, grep). The error message says exactly what to pass instead. |
| Newline change (D3) alters bytes on a non-POSIX platform. | A hypothetical Windows consumer. | Very low | None exists (CI Linux, authoring macOS). Test 12 pins reference-platform parity. |
| Manifest files add noise to git diffs (one per artifact, each containing a wall clock). | Reviewers of drawing-repo PRs. | Medium | `environment.generated_at` is the only churning field; everything else is content-derived, so a no-change rebuild yields a one-line diff. Recommend (do not enforce) that drawing repos commit manifests for committed artifacts. If the churn proves annoying, the fix is to drop `generated_at`, not to hash it — record that as the escape hatch. |
| The 8-character stamp collides. | Nobody, in practice. | Negligible | The stamp is compared against one named manifest, never used as a lookup key; the manifest holds all 64 characters. |
| Someone reads the stamp as an approval. | Reviewers, counterparties. | Low but serious | Rule 3. The stamp is `P:xxxxxxxx` — no words like "approved", "checked", "issued". `README.md` must state plainly: *the stamp records what produced these bytes; it is not a signature and confers no authority to issue.* Test 15 asserts the gate is untouched. |

**Migration.** Three ordered steps, each independently mergeable:

1. **This PR.** `provenance.py`, the optional `policy=` keywords, `svg_provenance_stamp`,
   `manifest` subcommand, `layout --manifest`, and tests 1–18. **Zero output changes** for
   existing generators (test 11).
2. **F6.** Add golden-byte fixtures for the two example drawings, using the DXF masking helper
   from test 11, and wire them into CI. From this point a byte change anywhere is a reviewed
   diff.
3. **F1.** Migrate `…/basin-site/source.py` to `EmitPolicy` + `--manifest` and accept its
   one-time byte diff as a deliberate, reviewed commit — with before/after renders attached, per
   the standard's workflow step 3.

---

## 8. Open questions (gaps, not guesses)

* **Q1 — Is `rsvg-convert` PDF reproducible?** Not installed in the probe environment, so
  unmeasured. Cairo-backed writers normally emit `/CreationDate`, but this spec will not claim
  it. Until measured, any `rsvg-convert` output gets `reproducible: null`,
  `reason: "external-renderer"`. Overlaps P4 (#56). → F2.
* **Q2 — Is LibreOffice **PNG** reproducible?** Only the LibreOffice PDF was measured
  (non-reproducible). PNG from the same backend is unmeasured →
  `reproducible: null`, `reason: "unmeasured"`. A ten-line probe settles it; it was not run, so
  it is not claimed.
* **Q3 — Should the title-block DATE be derivable?** D6 keeps it editorial. If the owner wants
  `date: auto` to mean "the committer date of the newest input", that is a visible product
  decision with an engineering-record consequence, not an implementation detail. Ask before
  building it.
* **Q4 — Should drawing repos commit `*.manifest.json`?** Committing them makes staleness
  reviewable in a PR diff; not committing them keeps diffs clean. This spec makes both
  possible and takes no position. P2 (#54) is the natural place to decide, since it owns
  `--check` in CI.
* **Q5 — Is `tool.version` alone a strong enough identity?** D7's stated hole: two tool commits
  at `0.1.0` can produce different bytes under one stamp. The clean fix is release discipline
  (bump `__version__` on any byte-affecting change, per `CLAUDE.md`'s "Version + pin"). Whether
  to *enforce* that with a CI check — e.g. fail a PR that changes emit code without bumping the
  version — is a separate decision.
* **Q6 — Should `params` be schema-validated per command?** This spec restricts `params` to
  JSON scalars and quantises its floats, but does not declare, per subcommand, which keys are
  expected. A typo'd key would silently change the digest and be reported as a legitimate
  difference. A per-command `params` schema is probably right; it needs P2's command inventory
  to design against.
