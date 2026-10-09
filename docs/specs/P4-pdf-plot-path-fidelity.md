# P4 — one tested PDF/plot path, with a render-fidelity check

**Issue:** upstream #56 · **Owner:** `senior-engineer` ·
**Status:** specification (not implemented) · **Spec date:** 2026-07-24

> This is an implementation specification. It is written to be built from without further context, and
> precisely enough that two independent implementations agree on observable behaviour. Where the issue's
> problem statement is factually wrong, §2 corrects it with reproducible evidence and §4 explains what
> changes as a result.

---

## 0. Hard constraints (restated — these bind every decision below)

1. **Backward compatibility is sacred.** Existing render output must not change unless a caller opts in.
   `render_dxf`, `render_svg`, `render_py`, `render_source`, `select_backend`, `dxf_has_inserts` and
   `find_soffice` keep their current signatures *and their current observable behaviour*. Every existing
   test in `technical_drawings_for_agents/tests/` passes unmodified. New behaviour is reached only through a new module, a
   new CLI verb, or an explicit opt-in flag.
2. **Never invent a capability.** Every claim about a backend in this spec was produced by running it on
   this machine on 2026-07-24 (macOS 25.5.0, `technical_drawings_for_agents/.venv`, ezdxf 1.4.4, matplotlib 3.11.0,
   LibreOffice `soffice` at `/opt/homebrew/bin/soffice`, `rsvg-convert` 2.62.3, poppler `pdftoppm`).
   Reproduction commands are given inline. An implementer who cannot reproduce a claim must stop and say
   so, not code around it.
3. **The ISSUED gate is untouchable.** Nothing in this change may set, imply, infer or default
   `status: ISSUED` or `for_construction: true`. The plot path is strictly read-only with respect to
   `meta.yaml` (see `src/technical_drawings_for_agents/meta.py:74-79`). A review bundle carries whatever watermark the
   sheets already carry; the bundler never adds, removes or rewrites a status watermark, and `--review`
   refuses nothing on the basis of status — it just never edits it.
4. **Determinism and loud failure.** An incomplete or degraded review artifact is worse than no artifact,
   because it will be trusted and signed against. No code path may write a file that *looks like* a
   complete review PDF when it is not. Where the spec must choose between "emit something imperfect" and
   "emit nothing and exit non-zero", it always chooses the latter.
5. **House style.** Match `src/technical_drawings_for_agents/components/layout.py`: `from __future__ import annotations`,
   frozen dataclasses for value objects, exactly **one** module-specific error class, config validated on
   load with precise messages naming the offending key/path, module docstring that states the *contract*
   and not just the mechanics, tests in `tests/test_*.py` named for the behaviour they assert.

---

## 1. Intent

Every generator in `technical_drawings_for_agents` — `render`, `bfd`, `pid`, `layout`, `component`, the site/GA sheet
assembler, and a new QGIS-free map plot — ends its run at exactly one place: a single `plot` entry point
that turns drawing geometry into a paginated, review-grade PDF through one declared, dependency-checked,
tested backend chain. Before that PDF is written, a deterministic **fidelity check** proves the plot
contains everything the source says is visible: every visible entity, every block `INSERT` recursively
exploded, on the layout that actually holds the drawing. If anything is missing, or the sheet would come
out blank, or a required backend is absent, the run **fails with a named, actionable error and writes no
canonical artifact**. `--review` collates the sheets of a set into one ordered multi-page PDF, and refuses
to produce a bundle that is quietly short a sheet.

The failure this prevents is the one that actually happened: a reviewer opens a PDF, sees a title block,
a frame and some text, signs off on the arrangement — and the piping, nozzles and half the geometry were
never drawn. Today that is not hypothetical, it is the *default* behaviour of the backend the README calls
"faithful" (§2.3), and the only guard against it is an assertion that the output file is larger than zero
bytes — which a completely blank 1,294-byte PDF passes (§2.4). "Good" is: a review PDF is either complete
or it does not exist, and the difference is decided by code, not by a human squinting at a page.

---

## 2. Current state (read the code; here is what it actually does)

### 2.1 The routing logic

`src/technical_drawings_for_agents/render.py` is the whole render surface. It is 312 lines and has no error class of its own.

| Location | What is there |
|---|---|
| `render.py:11-23` (docstring) | Documents the claim: matplotlib "silently drops block-insert detail", so INSERT-bearing DXFs go to LibreOffice; if LibreOffice is missing it "falls back to matplotlib with a warning". |
| `render.py:54-70` | `find_soffice()` — `SOFFICE_BIN` env → PATH (`soffice`, `libreoffice`) → macOS app bundle. |
| `render.py:73-75` | `dxf_has_inserts(doc)` — `len(doc.modelspace().query("INSERT")) > 0`. **Modelspace only.** |
| `render.py:78-87` | `select_backend(has_inserts, soffice_available)` — pure routing. Returns `"libreoffice"` only when both are true; otherwise `"matplotlib"`. Docstring: "so rendering never hard-fails". |
| `render.py:93-106` | `_render_dxf_matplotlib` — `plt.figure()`, `add_axes([0,0,1,1])`, `Frontend(...).draw_layout(msp, finalize=True)`, `savefig(dpi=150)` for `pdf` then `png`. No page size, no background control, no check. |
| `render.py:109-149` | `_render_dxf_libreoffice` — `soffice --headless --convert-to pdf|png`. The **only** success criterion is `target.exists()` (`render.py:144-147`). |
| `render.py:152-201` | `render_dxf(...)` — reads the DXF, counts INSERTs, picks a backend, logs, dispatches. `render.py:176-183` is the degrade-with-a-warning: it emits `log.warning(...)` and then renders anyway. |
| `render.py:204-227` | `_render_svg_libreoffice` — same shape, same exists-only check. |
| `render.py:230-269` | `render_svg` — backend order **cairosvg → LibreOffice → sibling `.dxf`**, then `RuntimeError`. |
| `render.py:272-298` | `render_py` — `runpy.run_path` the generator, then render every `*.dxf` found in `out_dir`. **`*.svg` the generator wrote is never rendered.** |
| `render.py:301-311` | `render_source` — dispatch on suffix; `ValueError` for anything else. |

Consumers: `cli.py:23-42` (`_cmd_render`, exit 2 for missing source, 1 for any exception, 0 otherwise —
including 0 when nothing at all was produced, `cli.py:36-38`) and `tests/test_example_smoke.py:22-26`.

### 2.2 What is *not* wired to a PDF at all

- `bfd.build()` (`bfd.py:406-428`) returns **SVG paths only**. `_cmd_bfd` (`cli.py:247-263`) prints them.
- `pid.build()` (`pid.py:335-381`) returns `[out]` — one **SVG**. Same for the CLI (`cli.py:228-244`).
- `component` / `layout` (`components/cli.py:37`, `:104`) emit `geojson | svg | dxf`. No PDF.
- There is **no `sitemap` verb**. The issue's "sitemap" is the out-of-tool project script
  `03-Resources/DEMO Water Project/drawings/basin-site/source.py`, which writes its SVG and then shells
  out itself (`source.py:278-283`):
  ```python
  pdf_path = OUT / "STA-SITE-GA-001.pdf"
  result = subprocess.run(["rsvg-convert", "-f", "pdf", "-o", str(pdf_path), str(svg_path)],
                          capture_output=True, text=True)
  if result.returncode != 0:
      sys.exit(f"rsvg-convert failed: {result.stderr}")
  ```
  Note it *does* check the return code — the defect is that the dependency is undeclared and the
  capability lives in a project script instead of the toolkit. That script's header already logs the gap
  (`source.py:29-32`: "CAPABILITY GAP (tracked as a workflows issue)").
- There is no map plot. The GIS review path is "open QGIS".

### 2.3 The issue's central claim is **backwards**. Evidence.

The README (`README.md:66-75`) and the module docstring both assert that the ezdxf/matplotlib backend
silently drops block-INSERT detail and that LibreOffice is the faithful one. Rendering
`tests/synthetic.py::make_dxf_with_insert` (a block holding a LINE, a CIRCLE and blue TEXT "PUMP",
inserted at (5,5), plus a modelspace TEXT "SHEET NOTE") through both backends and rasterising at 200 dpi
with `pdftoppm`:

| Backend | Result |
|---|---|
| **ezdxf/matplotlib** | **Faithful.** All three block children drawn at the right places (`PUMP` text, the circle, the line) plus the modelspace text. |
| **LibreOffice** `--convert-to pdf` | **Only the two TEXT entities**, drawn *on top of each other* at the same page position. **Every line, circle and polyline is gone.** |

Recording the frontend's backend calls directly (`ezdxf.addons.drawing.recorder.Recorder`, ezdxf 1.4.4)
confirms the frontend *does* traverse into blocks — ops are emitted for the block's children and
attributed to the `INSERT`'s own handle:

```
recorded ops: 4
  PointsRecord       props.handle='90'   # block child  (LINE, drawn as points/path)
  PathRecord         props.handle='90'   # block child  (CIRCLE)
  FilledPathsRecord  props.handle='90'   # block child  (TEXT "PUMP")
  FilledPathsRecord  props.handle='92'   # modelspace TEXT "SHEET NOTE"
bbox: [(5.0, -0.0051), (7.6556, 6.5)]
```

Further ezdxf-1.4.4 behaviour, all verified: `MINSERT` arrays are expanded (3×4 array of a 2-entity block →
**24** ops); nested blocks are traversed (block-in-block → 2 ops); `ATTRIB`s attached to an `INSERT` are
drawn (under their *own* handles, not the INSERT's — this matters in §3.4); an `INSERT` referencing a
missing block definition raises `DXFStructureError` **loudly**.

So the mechanism blamed in the issue does not exist at ezdxf 1.4.4. The behaviours that *do* silently lose
geometry are different, and worse:

| # | Real drop mechanism | Verified behaviour | Correct semantics? |
|---|---|---|---|
| **D1** | **LibreOffice's DXF import filter drops all vector geometry** and mis-places text. | Only TEXT survives, stacked. | **No — a defect.** This is the "faithful" backend. |
| **D2** | **Content lives in a paperspace layout.** `render_dxf` only ever draws `doc.modelspace()` (`render.py:170`, `render.py:101`). | modelspace ops = 0, `Layout1` ops = 3. A **blank** PDF is written, exit code 0, no warning. | **No — a silent blank sheet.** |
| **D3** | **Entities on a frozen or off layer.** ezdxf's `Frontend` honours layer visibility. | Block children on a frozen layer → 0 ops. `RenderContext.resolve_visible()` returns `False`. | **Yes — correct CAD semantics** (frozen layers do not plot). Must be *excluded from the expectation*, not "fixed". |
| **D4** | **`invisible` flag on an entity.** | `resolve_visible()` → `False`, 0 ops. | Yes — correct. |
| **D5** | **Unrendered `DIMENSION`** (no geometry block; `.render()` never called). | Frontend crashes: `AttributeError: 'NoneType' object has no attribute 'z'` in `ezdxf/entities/dimension.py:806`. | Loud, but an ugly traceback rather than a named error. |

Reproduce D1/D2 with `soffice --headless --convert-to pdf` then `pdftoppm -r 200 -png` and count pixels
below 250 grey: `frozen_layer.dxf` and `paperspace_only.dxf` both give **0 ink pixels** in a 6.9 KB PDF,
return code **0**.

### 2.4 The existing tests cannot catch any of this

`tests/test_render_routing.py` is five tests. The strongest artifact assertion in the file is
(`test_render_routing.py:55-62`):

```python
@needs_soffice
def test_insert_renders_via_libreoffice(tmp_path):
    out = render_dxf(src, tmp_path / "out")          # auto
    assert exts == {".pdf", ".png"}
    assert all(p.exists() and p.stat().st_size > 0 for p in out)
```

`size > 0` is the entire fidelity bar. Measured: an **entirely blank** matplotlib PDF from an empty
modelspace is **1,294 bytes**; a LibreOffice PDF containing zero ink is **6,925 bytes**. Both pass. This
test is currently green on CI while asserting nothing about the drawing.

`tests/test_example_smoke.py:22-26` likewise asserts only that a `.pdf` and a `.png` appeared.

### 2.5 Declared dependencies vs. reality

`pyproject.toml` declares `ezdxf>=1.1`, `PyYAML>=6.0`, `matplotlib>=3.6`; extras `cad3d`, `ingest`
(`PyMuPDF`, `numpy`), `dev`. The comment at `pyproject.toml:20-22` correctly notes ODA and LibreOffice are
external binaries, not pip deps. **`rsvg-convert` and `cairosvg` are declared nowhere**, and `cairosvg`
is a live code path (`render.py:244-254`).

`.github/workflows/build.yml` installs **only** `libreoffice-draw` as a render dependency (with a good
comment about `libreoffice-core` lacking the DXF filter). It does not install `librsvg2-bin`,
`poppler-utils`, or `cairosvg`. Tests run `pytest -q -rs`, so `@needs_soffice`-style skips stay visible.

### 2.6 What is genuinely available on this machine

```
$ which rsvg-convert soffice pdftoppm libreoffice inkscape qgis_process
/opt/homebrew/bin/rsvg-convert
/opt/homebrew/bin/soffice
/opt/homebrew/bin/pdftoppm
libreoffice not found ; inkscape not found ; qgis_process not found
$ which pdfunite            -> /opt/homebrew/bin/pdfunite      (pdftk, qpdf: not found)
$ python -c "import cairosvg"  -> ModuleNotFoundError          (NOT installed)
$ python -c "import fitz"      -> ModuleNotFoundError          (PyMuPDF NOT installed)
$ python -c "import pypdf"     -> ModuleNotFoundError          (NOT installed)
venv: ezdxf 1.4.4, matplotlib 3.11.0, numpy + Pillow present, Python 3.14
```

So `cairosvg` — the *first* choice in `render_svg` — is not installed here, and `rsvg-convert` — which the
project script depends on — is installed but undeclared and absent from CI. The one path that is both
declared and installed everywhere is the one that produces the worst output.

### 2.7 Capabilities in ezdxf 1.4.4 that nobody is using

All verified importable and working in `.venv`:

| Module | Capability |
|---|---|
| `ezdxf.addons.drawing.svg.SVGBackend` | **Pure-Python DXF → SVG.** No system libraries. `get_string(page, settings=..., render_box=...)`. |
| `ezdxf.addons.drawing.layout` | `Page(width, height, units, margins, max_width, max_height)`, `Margins`, `Settings(fit_page, scale, page_alignment, crop_at_margins, …)`, `PAGE_SIZES` — `ISO A0..A4`, `ANSI A..E`, `ARCH C..E1`. **Real paper sizes.** Verified: `PAGE_SIZES["ISO A3"] == (420, 297, Units.mm)`, and an A3 page rasterises to 2481×1754 at 150 dpi. |
| `ezdxf.addons.drawing.recorder.Recorder` / `Player` | Records every backend call with its `BackendProperties` (**including `handle`**) and gives `player.bbox()`. **This is the fidelity-check primitive.** |
| `ezdxf.addons.drawing.config` | `Configuration(background_policy=BackgroundPolicy.WHITE, color_policy=ColorPolicy.BLACK, …)`. Verified: turns the default dark-slate sheet into a white sheet with black ink (ink pixels 4,348,805 → 28,900 on the same drawing). |
| `ezdxf.addons.drawing.pymupdf.PyMuPdfBackend` | Native PDF/PNG — but requires **PyMuPDF, which is AGPL** (ezdxf prints `Python module PyMuPDF (AGPL!) is required` on import). `pyproject.toml` says `license = { text = "Proprietary" }`. |
| `ezdxf.addons.drawing.dxf` / `.hpgl2` / `.json` | Other backends, not needed here. |
| `ezdxf.addons.drawing.pyside`/`.pyqt` | **Unavailable** — `ImportError: no Qt binding found, tried PySide6 and PyQt5`. Do not plan around Qt. |

Determinism, measured:

| Step | Two runs, same input | Verdict |
|---|---|---|
| `SVGBackend.get_string(...)` | sha256 identical (`0f62df87…` both) | **Deterministic** |
| `rsvg-convert -f pdf` | sha256 identical | **Deterministic** — no timestamp in output |
| `soffice --convert-to pdf` | sha256 `fcb11ac5…` vs `f7a878ac…`; `CreationDate` present in the file | **Non-deterministic** |
| `rsvg-convert -f pdf -o out.pdf a.svg b.svg c.svg` | rc 0, `pdfinfo` → `Pages: 3`, in argument order | **Multi-page bundling with zero extra dependencies** |

---

## 3. Design

### 3.1 Shape and files

One new module plus a thin CLI layer. Nothing in `render.py` changes behaviour.

```
src/technical_drawings_for_agents/plot.py            NEW — the single plot entry point + fidelity check + bundler
src/technical_drawings_for_agents/mapplot.py         NEW — QGIS-free map plot (SVG sheet from GeoJSON + optional backdrop)
src/technical_drawings_for_agents/cli.py             EDIT — add `plot` and `map` verbs; add opt-in `--pdf` to bfd/pid/
                                    component/layout; add opt-in `--plot` to render. No default changes.
src/technical_drawings_for_agents/render.py          EDIT — docstring correction + one DeprecationWarning. No behaviour change.
README.md                           EDIT — replace the false "Render backend routing" claim with §2.3.
pyproject.toml                      EDIT — new extras (§3.9)
.github/workflows/build.yml          EDIT — install librsvg2-bin + poppler-utils; install the new extras
tests/test_plot_fidelity.py         NEW
tests/test_plot_backends.py         NEW
tests/test_plot_review_bundle.py    NEW
tests/test_plot_generators.py       NEW
tests/test_mapplot.py               NEW
tests/synthetic.py                  EDIT — additive fixtures only (§5.0)
```

`plot.py` gets exactly one error class, in the style of `LayoutError` (`components/layout.py:39-40`):

```python
class PlotError(RuntimeError):
    """Raised when a plot request is malformed, a required backend is missing, or the
    rendered plot is not a faithful, complete representation of its source."""
```

`PlotError` subclasses `RuntimeError` (not `ValueError`) because the dominant failure is environmental or
fidelity-related, and because `cli.py` already maps bare exceptions to exit 1. Sub-classing is **not**
used; the distinction between failure kinds travels in `PlotError.kind` (a `str` from a closed set) so
callers and tests can branch without an exception hierarchy:

```python
PLOT_ERROR_KINDS = frozenset({"input", "backend", "fidelity", "bundle", "unsupported"})
```

### 3.2 Value objects (frozen dataclasses)

```python
Format = Literal["pdf", "png", "svg"]
FidelityMode = Literal["strict", "warn", "off"]
SvgBackendName = Literal["auto", "rsvg", "cairosvg"]

@dataclass(frozen=True)
class PageSpec:
    """A paper size. Either a PAGE_SIZES key or explicit millimetres."""
    name: str = "ISO A3"                  # key into ezdxf PAGE_SIZES, or "custom"
    width_mm: float | None = None         # required when name == "custom"
    height_mm: float | None = None
    margin_mm: float = 10.0
    landscape: bool = True

    @classmethod
    def parse(cls, text: str) -> "PageSpec": ...
    def to_page(self) -> "ezdxf.addons.drawing.layout.Page": ...

@dataclass(frozen=True)
class PlotRequest:
    source: Path                          # .dxf | .svg | .py
    out_dir: Path
    stem: str | None = None               # default: source.stem
    layout: str | None = None             # DXF layout name; None => auto (§3.5)
    page: PageSpec = PageSpec()
    formats: tuple[Format, ...] = ("pdf",)
    fidelity: FidelityMode = "strict"
    svg_backend: SvgBackendName = "auto"
    white_background: bool = True
    ink_check: bool = False               # optional raster verification (§3.7)

@dataclass(frozen=True)
class FidelityFinding:
    check: Literal["coverage", "explosion", "extent", "layout"]
    severity: Literal["error", "warn"]    # same vocabulary as components/layout.py Severity
    message: str                          # names the handle(s)/DXF type(s)/layout involved

@dataclass(frozen=True)
class FidelityReport:
    source: Path
    layout: str
    expected_units: int                   # visible drawable units (§3.4)
    covered_units: int
    expected_exploded: int                # recursive virtual_entities() leaf count
    recorded_ops: int
    extent_mm: tuple[float, float] | None # plotted bbox size, None if empty
    findings: tuple[FidelityFinding, ...] = ()

    @property
    def ok(self) -> bool:                 # no finding with severity == "error"
    def summary(self) -> str:             # one line, for stdout and the log

@dataclass(frozen=True)
class PlotResult:
    request: PlotRequest
    outputs: tuple[Path, ...]
    svg_backend: str                      # "rsvg" | "cairosvg" | "none" (svg-only output)
    fidelity: FidelityReport | None       # None for non-DXF sources
    pages: int
```

### 3.3 The one entry point

```python
def plot(request: PlotRequest) -> PlotResult:
    """THE plot entry point. Every generator ends here.

    Pipeline, unconditionally: geometry -> SVG -> (PDF, PNG).
      * .dxf  -> ezdxf SVGBackend on a real Page, fidelity-checked, then converted
      * .svg  -> converted as-is (it *is* the sheet)
      * .py   -> executed via render.render_py's mechanism, then every .svg AND .dxf
                 the generator wrote is plotted
    Raises PlotError on a missing backend, a fidelity error, or a bad request.
    Writes nothing to out_dir unless every check for that artifact passed.
    """

def plot_source(source, out_dir, **kw) -> PlotResult:
    """Keyword convenience wrapper building a PlotRequest. Generators call this."""

def review_bundle(sheets: Sequence[Path], out_pdf: Path, *,
                  allow_missing: bool = False) -> Path:
    """Collate sheets into one ordered multi-page PDF. See §3.8."""

def check_fidelity(doc, layout_name: str, *, config=None) -> FidelityReport:
    """Pure: records the frontend for `layout_name` and compares against the source.
    No file I/O. Callable standalone — this is what the tests drive."""

def discover_sheets(target: Path) -> tuple[Path, ...]:
    """Ordered sheet discovery for --review. See §3.8."""

def find_svg_backend(preference: SvgBackendName = "auto") -> tuple[str, object]:
    """Resolve the SVG->PDF backend. Returns (name, handle) or raises PlotError(kind="backend")."""
```

### 3.4 The fidelity check — exact definition

Run **before** any file is written, on the in-memory `ezdxf` document, using
`ezdxf.addons.drawing.recorder.Recorder` as the backend. The `Recorder` receives the identical
`Frontend`/`RenderContext`/`Configuration` that the `SVGBackend` will receive, so it measures the real
plot, not a model of it. Record once; replay the same `Player` into the `SVGBackend` if the implementation
wants to avoid a second traversal (`Player.replay(backend)`), or traverse twice — either is conforming,
because `SVGBackend.get_string` output was verified byte-identical across runs.

**Definitions.**

- A **drawable unit** is (a) an entity directly in the plotted layout, or (b) an `ATTRIB` owned by an
  `INSERT` in that layout. Units are keyed by DXF `handle`. `ATTRIB`s are units in their own right because
  ezdxf attributes their backend ops to the `ATTRIB`'s own handle, **not** the owning `INSERT`'s — verified:
  a block with one LINE plus one auto-filled `ATTRIB` produced 1 op under the INSERT handle and 1 op under
  the ATTRIB handle.
- A unit is **expected visible** iff `RenderContext.resolve_visible(entity)` is `True`. This is the exact
  predicate the frontend itself uses, so frozen layers, off layers and the `invisible` flag are excluded by
  construction and can never be reported as missing (verified against D3/D4 in §2.3).
- **Exploded leaves** for a unit: walk `entity.virtual_entities()` recursively, descending into any nested
  `INSERT`, and count every non-`INSERT` result for which `resolve_visible()` is `True`. Recursion depth is
  capped at 16; exceeding it is a `PlotError(kind="fidelity")` naming the block, not a silent truncation.
  Exploding an `INSERT` whose block definition is missing raises `DXFStructureError` from ezdxf; catch it
  and re-raise as `PlotError(kind="fidelity")` quoting the block name.
- **Recorded ops** for a unit: the number of recorded backend calls whose `BackendProperties.handle`
  equals that unit's handle.

**The four checks.**

| ID | Check | Rule | Tolerance | Severity |
|---|---|---|---|---|
| **F1** | **Coverage** | Every expected-visible unit has `recorded_ops >= 1`. | **Zero.** A drawable unit that produced no backend call was not drawn. | error |
| **F2** | **INSERT explosion** | For every expected-visible `INSERT` unit *u*: `recorded_ops[u] >= exploded_leaves[u]`. | **One-sided bound, no numeric tolerance** (see rationale below). | error |
| **F3** | **Extent** | If `expected_units >= 1` then `player.bbox()` must have finite extent with width **and** height > 0 (a genuinely degenerate single-line drawing is permitted to have one zero dimension — require `width > 0 or height > 0`). | Zero. | error |
| **F4** | **Layout** | If the plotted layout yields `expected_units == 0` but some other layout in the document yields `> 0`, fail and **name that layout**. | Zero. | error |

F2 is a **lower bound, not equality**, and this is deliberate — the issue asks for a count that "matches",
and equality is provably impossible. Measured entity→op fan-out at ezdxf 1.4.4:

| Entity | ops | Entity | ops |
|---|---|---|---|
| LINE, CIRCLE, ARC, ELLIPSE, SPLINE, POINT, SOLID, HATCH, TEXT | 1 each | LWPOLYLINE (4 vertices) | **1** (segments merged into one path) |
| MTEXT (3 lines) | **3** | rendered DIMENSION | **6** (leaf walk yields 9) |
| MINSERT 3×4 of a 2-entity block | **24** (leaf walk yields 2 — `virtual_entities()` does not expand the array) | | |

So ops both under- and over-count relative to any per-primitive notion of "entities". What held in **every**
probed case is `recorded_ops >= exploded_leaves`, per handle and in aggregate. That is the strongest sound
assertion available, and it is exactly the one that catches the failure mode we care about: a backend that
drops a block's children drives `recorded_ops` for that INSERT below its leaf count.

**On mismatch.** F1–F4 findings are aggregated into one `FidelityReport`. Then:

- `fidelity="strict"` (**default**): if `not report.ok`, raise `PlotError(kind="fidelity")` whose message is
  `report.summary()` followed by up to 20 findings, each naming handle, DXF type, layer and block name.
  **No PDF, PNG or SVG is written for that source.** If a partial file was already created (it should not
  be — write to a temp path and `os.replace` on success), it is removed.
- `fidelity="warn"`: every error-severity finding is logged at `WARNING`, the artifact **is** written, and
  its filename is suffixed `.degraded` before the extension — `STA-001.degraded.pdf`, never `STA-001.pdf`.
  The canonical name is reserved for artifacts that passed. This is what makes "not silently passing"
  structural rather than a matter of reading logs.
- `fidelity="off"`: no check runs; `PlotResult.fidelity is None`; the artifact is written with a
  `.unchecked` infix. Intended only for triage of a file that is already known-broken.

`FidelityReport` is returned on the `PlotResult` in all modes it ran, so P3 (#55) can serialise it into the
output manifest without this spec having to know the manifest format.

### 3.5 Backend chain and the selection rule

**Stage 1 — geometry to SVG. Exactly one implementation. Not selectable.**

`ezdxf.addons.drawing.svg.SVGBackend` with:
- `Page` from `PageSpec.to_page()` (`PAGE_SIZES` lookup; swap width/height per `landscape`;
  `Margins.all(margin_mm)`).
- `Settings(fit_page=True)` — default; `scale` is honoured if a caller sets it.
- `Configuration(background_policy=BackgroundPolicy.WHITE, color_policy=ColorPolicy.BLACK)` when
  `white_background=True` (the default for the new path).
- `xml_declaration=True`.

SVG sources skip stage 1 — they already *are* the sheet.

**Stage 2 — SVG to PDF/PNG. A two-link chain with a fixed preference order.**

```
def find_svg_backend(preference="auto"):
    "auto"      -> rsvg if shutil.which("rsvg-convert") else cairosvg if importable
                   else PlotError(kind="backend")
    "rsvg"      -> require rsvg-convert on PATH, else PlotError(kind="backend")
    "cairosvg"  -> require import cairosvg, else PlotError(kind="backend")
```

Honour `RSVG_CONVERT_BIN` for an explicit path, mirroring the `SOFFICE_BIN`/`ODA_CONVERTER` convention
already in `render.py:60` and `ingest.find_oda`.

- `rsvg`: `rsvg-convert -f pdf -o <out> <svg>`; PNG via `-f png --dpi-x N --dpi-y N`. Non-zero return code
  or an absent/zero-byte output → `PlotError(kind="backend")` quoting stderr verbatim (truncated to 500
  chars). **Never** treat rc≠0 as recoverable.
- `cairosvg`: `cairosvg.svg2pdf(url=..., write_to=...)`, `svg2png(..., dpi=N)`.

There is **no third link and no fallback past the end of the chain.** When neither backend is present:

```
PlotError(kind="backend"):
  no SVG->PDF backend available. Install ONE of:
    * rsvg-convert   — macOS: brew install librsvg      Debian/Ubuntu: apt-get install librsvg2-bin
    * cairosvg       — pip install 'technical_drawings_for_agents[plot-cairo]'
  or set RSVG_CONVERT_BIN to an rsvg-convert binary.
  The SVG sheet at <path> was written and is viewable in any browser; no PDF was produced.
```

That message is the contract for acceptance test **T7**: exit non-zero, name both remedies, and be explicit
that the SVG exists but the PDF does not. Writing the intermediate SVG is *not* a degraded artifact — an SVG
is not a PDF and cannot be mistaken for one.

**LibreOffice is not in the new chain, for either DXF or SVG.** See §4.1.

### 3.6 CLI surface

New verb, added in `build_parser()` alongside the existing subparsers (`cli.py:154-225`):

```
technical_drawings_for_agents plot SOURCE [SOURCE ...]
    --out DIR                 default: <first-source-dir>/out
    --page SPEC               default "ISO A3". "ISO A1" | "ANSI D" | "420x297" | "custom:420x297"
    --portrait                default landscape
    --margin MM               default 10
    --format {pdf,png,svg}    repeatable; default pdf
    --dpi N                   PNG only; default 300
    --layout NAME             DXF layout to plot; default auto (§3.5 / F4)
    --fidelity {strict,warn,off}   default strict
    --svg-backend {auto,rsvg,cairosvg}   default auto
    --dark                    keep ezdxf's default dark sheet (default: white)
    --ink-check               additionally verify the raster is not blank (§3.7)
    --review OUT.pdf          bundle the plotted sheets into one multi-page PDF (§3.8)
    --json                    emit a machine-readable result on stdout instead of prose
```

Additions to existing verbs, **all opt-in, all defaulting off**:

- `render --plot` — route through `plot()` instead of `render_source()`. Without it, `render` is
  byte-for-byte what it is today.
- `bfd --pdf`, `pid --pdf`, `component --pdf`, `layout --pdf` — after the SVG/DXF is written, additionally
  plot it. `build()` return values do **not** change (§4.6).
- `map` — new verb, §3.10.

**Exit codes.** The existing convention (`cli.py`: 2 = bad input, 1 = failure, 0 = ok) is preserved and
extended. New codes are all ≥3, so no existing script that checks `== 0` or `!= 0` changes meaning:

| Code | Meaning |
|---|---|
| 0 | Every requested artifact was produced and passed every enabled check. |
| 1 | Unexpected failure (traceback-class bug), or a generator raised. |
| 2 | Bad usage or input not found — matches `cli.py:28`, `cli.py:74`. |
| 3 | `PlotError(kind="fidelity")` — a plot was not faithful. No canonical artifact written. |
| 4 | `PlotError(kind="bundle")` — `--review` could not produce a complete bundle. |
| 5 | `PlotError(kind="backend")` — a required backend is missing or failed. |
| 6 | `PlotError(kind="unsupported")` — unsupported source type or page spec. |

**stdout / stderr contract.**

- **stdout** carries results a human or script consumes: one line per artifact,
  `PLOT <format> <path> (<backend>, <pages>p, fidelity <ok|degraded|unchecked>)`, then one summary line
  `plotted N artifact(s) from M source(s); fidelity: X ok, Y degraded, Z unchecked`. With `--json`, stdout
  is **exactly one** JSON object (`{"results": [...], "ok": bool, "exit": int}`) and nothing else — no log
  lines, no banners. Under `--json` the root logger's level is raised to `WARNING` and logs go to stderr.
- **stderr** carries diagnostics: `logging` output (existing format `%(name)s: %(message)s`,
  `cli.py:267`), warnings, and error messages. Every error line is prefixed `error: ` to match
  `cli.py:28`, `cli.py:34`, `cli.py:54`.
- Fidelity findings go to **stderr** (they are diagnostics), and the `PLOT ... fidelity degraded` marker
  goes to stdout (it is a property of the result).
- The chosen SVG backend is logged once per process at `INFO`, matching `render.py:192-197`.

### 3.7 Optional ink check (`--ink-check`)

The fidelity check is a *pre-raster* check: it proves the frontend emitted the geometry. It cannot prove the
converter honoured it. `--ink-check` closes that gap and is **off by default** because it needs two extra
dependencies:

- rasterise the produced PDF with `pdftoppm -r 72 -png` (poppler),
- load with Pillow, convert to `L`, count pixels `< 250`,
- require `ink_pixels > 0` on **every** page when `expected_units >= 1`.

Missing `pdftoppm` or Pillow with `--ink-check` given explicitly → `PlotError(kind="backend")`. Without the
flag, the tools are never invoked. Rationale for opt-in: rasterising every sheet is slow, resolution-
dependent, and the check is coarse (it catches *blank*, not *incomplete*). The precise check is F1–F4.

### 3.8 `--review` — bundling a set into one PDF

**Discovery.** `discover_sheets(target)`:

1. If `target` is a **file**, it is the only sheet.
2. If `target` is a **directory** containing `meta.yaml` with a `sheets:` key (a list of paths relative to
   the directory), that list **is** the order, verbatim. This is the explicit, reviewable ordering hook;
   nothing else in `meta.yaml` is read or written (`meta.py` is untouched — an unknown `sheets:` key is
   already tolerated by `DrawingMeta.from_mapping`, which reads a fixed field list at `meta.py:38-56`;
   the implementer must confirm this and, if `DrawingMeta` rejects extras, read `sheets:` from the raw
   YAML mapping instead of through `DrawingMeta`).
3. Otherwise, sheets are the PDFs in `<target>/out/` if any, else the SVGs in `<target>/out/` — the same
   location `validate_drawing_dir` uses (`validate.py:112`) — ordered by
   `(drawing_number, sheet_index, filename)` where number and index are parsed from the filename if it
   matches `^(?P<number>.+?)(?:[_-]S?(?P<index>\d+))?$`, falling back to case-sensitive lexicographic
   order on the filename. Ordering must be a total order and must not depend on filesystem iteration order.
4. Multiple `--review` sources on the command line are concatenated in **command-line order**, each source
   internally ordered by the rules above.

**Bundling.** If every sheet is an SVG: one `rsvg-convert -f pdf -o OUT s1.svg s2.svg …` call — verified to
produce an N-page PDF in argument order with no extra dependency. If any sheet is a PDF: merge with `pypdf`
(`PdfWriter.append`), a pure-Python BSD dependency (§3.9); if `pypdf` is absent, fall back to `pdfunite`
(poppler) if present; if neither, `PlotError(kind="backend")`.

**Missing or failing sheets.** A sheet that is listed in `sheets:` but absent, or that fails its own
fidelity check, or that yields zero pages, makes the bundle **incomplete**:

- default: **no bundle is written**, `PlotError(kind="bundle")`, exit **4**, message listing every missing
  or failed sheet by path and reason.
- `--allow-missing`: the bundle **is** written, but (a) to `<name>.partial.pdf`, never `<name>.pdf`;
  (b) with a generated placeholder page in each gap carrying the text
  `SHEET MISSING — <path> — NOT FOR REVIEW`; (c) exit **4** anyway. The artifact exists for triage; the
  exit code and the filename both still say "incomplete". Nothing about it can be mistaken for a complete
  review set.

**Page count.** `PlotResult.pages` for a bundle equals the number of sheets bundled (plus placeholders).
The implementation must *verify* the produced page count (via `pypdf.PdfReader(...).pages` or
`pdfinfo`) and raise `PlotError(kind="bundle")` on a mismatch rather than trusting the converter.

**Status.** The bundler copies pages. It never renders a watermark, never reads `for_construction`, never
writes `meta.yaml`. Constraint 0.3 holds by construction.

### 3.9 Dependencies

`pyproject.toml`:

```toml
dependencies = [
    "ezdxf>=1.4",          # was >=1.1. SVGBackend/layout.Page/recorder are required and
                           # verified at 1.4.4; do not claim 1.1 support without testing it.
    "PyYAML>=6.0",
    "matplotlib>=3.6",     # STAYS — the legacy render path still uses it (§4.2)
]

[project.optional-dependencies]
plot = ["pypdf>=4.0"]                        # multi-page PDF merge (BSD)
plot-cairo = ["cairosvg>=2.7"]               # alternative SVG->PDF (needs system libcairo)
verify = ["Pillow>=10.0", "numpy>=1.24"]     # --ink-check only
cad3d = ["build123d>=0.5"]
ingest = ["PyMuPDF>=1.23", "numpy>=1.24"]
dev = ["pytest>=7.0", "ruff>=0.5"]
```

External binaries, documented in the README next to the existing ODA/LibreOffice note
(`pyproject.toml:20-22` sets the precedent) and **not** pip dependencies:

| Binary | Purpose | macOS | Debian/Ubuntu |
|---|---|---|---|
| `rsvg-convert` | SVG → PDF/PNG (preferred) | `brew install librsvg` | `apt-get install librsvg2-bin` |
| `pdftoppm`, `pdfunite` | `--ink-check`, bundle fallback | `brew install poppler` | `apt-get install poppler-utils` |
| `soffice` | **legacy** render path only | — | `libreoffice-draw` (already installed by CI) |

`.github/workflows/build.yml` — extend the existing install step (keep `libreoffice-draw`, because the
legacy tests still exercise it):

```yaml
- name: Install render/plot backends
  run: |
    sudo apt-get update
    sudo apt-get install -y libreoffice-draw librsvg2-bin poppler-utils
```

and add to the tooling step: `pip install -e 'technical_drawings_for_agents[plot,verify,dev]'`. The generic
`pip install -e` loop already in the workflow does not install extras, so this must be an explicit line.
Keep `pytest -q -rs` so any dependency-gated skip stays visible.

### 3.10 `map` — a map plot with no QGIS

`mapplot.py` reads a small `plot.yaml`, builds an SVG sheet with the **existing** furniture in `svg.py`
(`ViewBox`, `svg_polygon`, `svg_line`, `svg_circle`, `svg_text`, `svg_scale_bar`, `svg_north_arrow`,
`svg_title_block`, `svg_status_watermark`) and hands it to `plot()`. Deliberately narrow:

```yaml
map:
  crs: EPSG:32630                 # recorded and asserted, never transformed
  extent: [788392, 322125, 788702, 322395]   # xmin, ymin, xmax, ymax in `crs`
  backdrop:                       # OPTIONAL, and must ALREADY be clipped to `extent`
    image: geo/ga_backdrop.png
    extent: [788392, 322125, 788702, 322395]
  layers:
    - name: rafts
      geojson: ../components/basin-wtp/basin_layout.geojson
      style: {stroke: "#123", fill: none, stroke_width: 1.2}
      label: tag
meta: {number: STA-SITE-GA-001, title: Site general arrangement, status: CONCEPT, scale: "1:500"}
```

Rules, all validated on load with a message naming the offending key (house style):

- GeoJSON is read with the standard-library `json` module. **No GDAL, no OGR, no PyQGIS, no `ogr2ogr`.**
- Every layer's GeoJSON must be in `map.crs`. If a `crs` member is present and disagrees, or coordinates
  fall outside `extent` by more than the extent's own span, raise `PlotError(kind="input")` telling the
  caller to run the P8 `geo` verb. **`mapplot` never reprojects and never clips.**
- `backdrop.extent` must equal `map.extent` to within 1e-6 of the extent span. If it does not,
  `PlotError(kind="input")` — a misaligned backdrop is exactly the kind of plausible-looking wrong artifact
  constraint 0.4 forbids. Preparing a correctly clipped backdrop is P8's job (#60).
- The backdrop is referenced as a **relative `xlink:href` path**, not base64-embedded (P8 #60 owns that
  decision; this spec must not re-embed and must not regress it). If the referenced file is missing:
  `PlotError(kind="input")`.
- Supported GeoJSON geometry: `Point`, `LineString`, `Polygon`, `MultiLineString`, `MultiPolygon`. Anything
  else, including `GeometryCollection`, raises `PlotError(kind="unsupported")` naming the feature index and
  type. A silently skipped feature is a defect — the same rule `components/layout.py` states in its
  docstring ("A silently-skipped placement is a defect, not a default").
- Fidelity for a map plot: F1–F4 do not apply (there is no DXF). Instead assert the SVG-level invariant
  **every input feature produced at least one element in the SVG body**, counted during emission, and fail
  with the feature index on a shortfall. Report it in the same `FidelityReport` shape with
  `check="coverage"`.

---

## 4. Behaviour decisions, with rationale

### 4.1 Keep LibreOffice, or go pure-Python? — **Pure-Python for the new path; LibreOffice stays only in the frozen legacy path.**

The issue and the README assume LibreOffice is the faithful backend and matplotlib the lossy one. §2.3
shows the opposite, measured on the repo's own fixture: matplotlib/ezdxf draws all block children
correctly; LibreOffice drops **every** line, circle and polyline and stacks the surviving text. It is also
non-deterministic (differing sha256 across identical runs; embeds `CreationDate`), which puts it in direct
conflict with P3 (#55). And its failure mode is invisible: rc 0, a 6.9 KB PDF, zero ink.

`ezdxf.addons.drawing` gives a complete pure-Python path: `SVGBackend` for geometry (byte-identical across
runs), `layout.Page` for real paper sizes, `Configuration` for a white review sheet, and `Recorder` for the
fidelity check. `rsvg-convert` finishes the job deterministically. So:

- The **new** path never invokes LibreOffice, for DXF or for SVG.
- The **legacy** `render_*` functions keep calling it, unchanged, because constraint 0.1 forbids changing
  their output and because `test_render_routing.py` asserts the routing.
- `render_dxf` emits a `DeprecationWarning` (and an `INFO` log line) when `backend == "auto"` resolves to
  `"libreoffice"`, pointing at `technical_drawings_for_agents plot`. A warning is not a behaviour change: the artifacts are
  identical.
- **Follow-up issue to open** (do not do it here): "technical_drawings_for_agents: retire the LibreOffice DXF render
  backend; make `plot` the default for `render`", carrying §2.3's evidence, a parity test, and a minor
  version bump.

*Rejected: `PyMuPdfBackend`.* It is the most direct DXF→PDF route, but PyMuPDF is **AGPL** (ezdxf says so
on import) and `pyproject.toml` declares this package `Proprietary`. It already sits in the `ingest` extra,
which is arguably a latent problem, but this spec will not put an AGPL dependency on the *default* review
path. If licensing is later cleared, adding `PyMuPdfBackend` as a third link in stage 2 is a small change.

*Rejected: the Qt (`pyside`/`pyqt`) backends.* Verified unimportable here (`no Qt binding found`), and a Qt
dependency on CI for a headless PDF is unjustifiable.

*Rejected: matplotlib for the new path.* It renders faithfully, but `plt.figure()` +
`add_axes([0,0,1,1])` (`render.py:97-98`) has no notion of paper size — the fixture came out 800×1960 px,
an arbitrary aspect with the drawing crammed into a strip, on a dark background. It is a plotting library
being used as a plotter. `SVGBackend` + `layout.Page` is the same ezdxf frontend with a real sheet under it.
matplotlib stays as a *declared dependency* only because the legacy path needs it.

### 4.2 Is a degraded render an error or a warning? — **Error by default; a warning only when explicitly requested, and then the filename says so.**

The issue's requirement is "never silently partial". Three things could satisfy "not silently": a log line,
a non-zero exit, or a different filename. A log line is not enough — `render.py:176-183` already logs a
`WARNING` and the incomplete PDF still lands at the canonical path where a reviewer will open it a week
later with no log in sight. So:

- `--fidelity strict` is the **default**: a fidelity error raises, exit 3, and **no artifact is written at
  the canonical path**. Write to a temp file in `out_dir` and `os.replace` only after checks pass, so a
  crash mid-write cannot leave a plausible-looking file.
- `--fidelity warn` exists because triage needs it, but the artifact is renamed `*.degraded.pdf`. The
  degradation is encoded in the thing the reviewer actually sees, not in a stream they do not read.
- `--fidelity off` writes `*.unchecked.pdf`.

The canonical name `<stem>.pdf` therefore *means* "passed every check", everywhere, always. That invariant
is worth more than any amount of documentation.

*Rejected: warn-by-default with a red banner drawn on the sheet.* Tempting, but it mutates the drawing —
and a tool that draws on a sheet is one step from a tool that stamps a status on one, which constraint 0.3
forbids. Filenames and exit codes are outside the drawing.

### 4.3 Is the fidelity check always on, or opt-in? — **Always on for DXF sources on the new path; opt-out only, never opt-in.**

A check you must remember to enable is a check that is off when it matters. The cost is a second frontend
traversal into a `Recorder`, which is in-memory and cheap relative to the SVG serialisation and the
`rsvg-convert` subprocess — and it can be avoided entirely by recording once and replaying into the
`SVGBackend`. There is no performance argument for opt-in.

It does **not** run for SVG sources (there is no source-of-truth entity set to compare against — the SVG
*is* the drawing), and it does not run on the legacy `render_*` functions at all (constraint 0.1: adding a
check that can raise would change their behaviour). `--ink-check` is separately opt-in, for the reasons in
§3.7.

### 4.4 How to compare counts when a backend legitimately merges geometry — **compare per-handle presence exactly, and per-handle counts as a lower bound. Never equality.**

Measured fan-out (§3.4 table) is many-to-many: a 4-vertex `LWPOLYLINE` collapses to one path op, an
`MTEXT` explodes to three, a rendered `DIMENSION` to six, a 3×4 `MINSERT` to twenty-four from a leaf walk
that reports two. Any implementation asserting `recorded == expected` will be wrong on real drawings and
will be "fixed" by widening a tolerance until it catches nothing. So:

- **Presence** (F1) is exact and needs no tolerance: an entity that produced *zero* backend calls was not
  drawn. There is no legitimate merge that makes an entity contribute nothing, because ops are keyed by the
  contributing entity's own handle.
- **Counts** (F2) are asserted only as `recorded_ops >= exploded_leaves`, per handle. Merging can push
  `recorded` *up* (fan-out) but a dropped child pushes it *down* — which is precisely the direction the
  bound catches.
- `ATTRIB`s are keyed to their own handles, because that is where ezdxf attributes their ops (verified). An
  implementation that folds them into the owning `INSERT` will produce a spurious F2 failure on every
  tagged block.
- No global percentage tolerance is offered, and no `--fidelity-tolerance` flag exists. A tolerance on
  "how much of the drawing may be missing" is not a knob an engineering review pipeline should have.

*Rejected: comparing rasterised ink between two backends.* Not comparable — the same drawing gave 4.3 M
ink pixels on the dark default sheet and 28,900 on the white one, and 705 through LibreOffice at A4 scale
versus 202,816 through matplotlib at figure scale. Ink is only usable as a *blank / not blank* signal,
which is what `--ink-check` restricts it to.

*Rejected: exploding INSERTs into a copy of the document and re-rendering to compare op totals.* Tried;
`msp.add_foreign_entity` rejects entities from the same document (`DXFValueError: entity from same DXF
document`), so it needs a real round-trip through `ezdxf.explode`, mutates geometry, and would have to be
kept in step with ezdxf's explode semantics. The recursive `virtual_entities()` walk gets the same
information with no mutation.

### 4.5 SVG → PDF via cairosvg, rsvg, LibreOffice, or a re-emit? — **rsvg-convert first, cairosvg second, and nothing after. This differs from the legacy order, on purpose.**

| Option | Verdict |
|---|---|
| **`rsvg-convert`** | **Preferred.** Deterministic (identical sha256 across runs, no embedded timestamp), handles the embedded-base64 `<image>` the site GA sheet uses, supports multi-page PDF from multiple SVG inputs in one call, packaged on both platforms (`librsvg`/`librsvg2-bin`), and is already what the project script depends on — this change *declares* the existing dependency rather than inventing one. |
| **`cairosvg`** | Second. Pip-installable, so it works where no system package manager is available. But it needs system `libcairo`, which on macOS is exactly the Homebrew-drift class of breakage already recorded for this toolchain, and it is **not installed here** despite being the legacy path's first choice. |
| **LibreOffice** | Rejected. Non-deterministic, and its DXF filter is demonstrably lossy (§2.3); its SVG import is closer (15,553 ink px vs rsvg's 14,652 on the same sheet) but it is a 400 MB office suite in the review path with no fidelity guarantee. |
| **Re-emit (draw the sheet straight to PDF)** | Rejected as the general answer. It would mean a PDF emitter for `svg.py`'s furniture — a large new surface, duplicating what the sheet already is. It *is* effectively what `SVGBackend` + `Page` does for DXF, which is why stage 1 is fixed and only stage 2 is a chain. |

**Determinism consequence, for P3 (#55).** With `SVGBackend` → `rsvg-convert`, the whole pipeline is
byte-deterministic: both stages were measured byte-identical across runs, so identical inputs give an
identical PDF with no `SOURCE_DATE_EPOCH` handling needed at this layer. With `cairosvg` this is not
asserted here and must not be assumed. With LibreOffice it is provably false. Therefore: **P3's
byte-identical-output test must pin `--svg-backend rsvg`**, and `plot()` records the resolved backend name
in `PlotResult.svg_backend` so P3's manifest can capture which converter produced the bytes. A PDF is not
reproducible unless you know which converter made it.

### 4.6 Do generators change their return values? — **No.**

`bfd.build()` / `pid.build()` keep returning SVG paths only. PDFs are produced by the CLI layer (or by a
caller invoking `plot_source`) and reported separately. Reason: `build()` is a public function whose
`list[Path]` is consumed by `cli.py:241`/`cli.py:260` and by tests; appending PDFs would change the meaning
of `len(outputs)` in output a human reads. Additive artifacts are also gated behind `--pdf` rather than
default-on, because "no new files appear in `out/` unless asked" is the cleanest reading of constraint 0.1
and costs one flag.

### 4.7 Which DXF layout gets plotted? — **Auto-detect, and fail loudly on ambiguity.**

`render_dxf` hard-codes `doc.modelspace()` (`render.py:170`), which silently produces a blank sheet for a
paperspace drawing (D2). The new path:

1. `--layout NAME` given → plot exactly that; unknown name → `PlotError(kind="input")` listing the
   document's layout names.
2. Otherwise plot modelspace **if** it has ≥1 expected-visible unit.
3. Otherwise, if exactly one other layout has units, plot that one and log at `INFO` which and why.
4. Otherwise (nothing anywhere, or ≥2 candidate layouts with modelspace empty) →
   `PlotError(kind="fidelity")` via F4, naming every layout and its unit count.

Rule 3 is a convenience with a visible log line; rule 4 refuses to guess. Neither can produce a blank PDF.

### 4.8 Do we fix `render_py` not rendering SVGs? — **Only on the new path.**

`render_py` (`render.py:293-297`) renders only `*.dxf` from `out_dir`, so a generator that writes an SVG
(most of them) gets no PDF from `render`. Changing that would add files to `out/` for existing callers, so
`render_py` is untouched. `plot()` on a `.py` source plots **both** the `*.svg` and the `*.dxf` the
generator wrote, in sorted order. This is the behaviour the site GA script's hand-rolled `rsvg-convert`
call was working around.

### 4.9 Naming: `plot`, not `render2`

`render` is taken and its semantics are frozen. "Plot" is the correct engineering word for putting a
drawing on a sheet at a scale on a paper size, and it is what the issue calls it. The module is
`plot.py`, the verb is `plot`, the entry point is `plot()`.

---

## 5. Acceptance tests

Mechanically checkable, one behaviour per test, named for the behaviour asserted (house style). Tests that
need an external binary use `pytest.mark.skipif` with a reason, following `test_render_routing.py:17-18`,
so `pytest -rs` shows them.

**5.0 New fixtures (additive to `tests/synthetic.py`; do not modify existing ones).**

| Fixture | Content |
|---|---|
| `make_dxf_block_heavy(path)` | Block `EQUIP` with a LINE, a CIRCLE, an LWPOLYLINE and a TEXT; **three** INSERTs of it at different origins/rotations; plus one modelspace LWPOLYLINE frame and one TEXT. Expected-visible units and exploded-leaf counts are known by construction. |
| `make_dxf_paperspace_only(path)` | Modelspace **empty**; `Layout1` holds an INSERT plus a TEXT. (D2.) |
| `make_dxf_frozen_block_children(path)` | Block whose LINE is on a frozen layer `PIPING` and whose CIRCLE is on layer `0`; one INSERT; a visible frame. (D3 — must *pass*, not fail.) |
| `make_dxf_minsert(path)` | 3×4 `MINSERT` array of a 2-entity block. |
| `make_dxf_insert_with_attrib(path)` | Block with a LINE and an `ATTDEF`; INSERT with `add_auto_attribs({"TAG1": "P-101"})`. |

---

**T1 — every generator produces a PDF through the same entry point.**
*Setup:* the packaged BFD data, P&ID data, `component` example spec
(`src/technical_drawings_for_agents/components/examples/packaged_unit.yaml`), a `layout` config, the worked example
`drawings/example/simple-section/source.py`, and a minimal `map` `plot.yaml`, each copied into `tmp_path`.
*Action:* for each, invoke the CLI with `--pdf` (or `plot` for the `.py` source) while monkeypatching
`technical_drawings_for_agents.plot.plot` with a counting wrapper that delegates to the real function.
*Expected:* exit 0 for all; a `.pdf` exists for each; the wrapper recorded **≥1 call per generator** — i.e.
no generator reaches a PDF by any other route. Additionally `grep`-equivalent assertion: no module other
than `plot.py` and `render.py` contains the strings `rsvg-convert`, `soffice`, or `cairosvg`.
*(Skip the PDF assertions, not the routing assertion, if no SVG→PDF backend is present.)*

**T2 — a DXF with block INSERTs plots faithfully.**
*Setup:* `make_dxf_block_heavy`.
*Action:* `check_fidelity(doc, "Model")`, then `plot()` with defaults.
*Expected:* `report.ok is True`; `report.expected_units == 5` (3 INSERTs + frame + text);
`report.covered_units == report.expected_units`; `report.recorded_ops >= report.expected_exploded`;
`report.expected_exploded == 12` (3 × 4 block children); the PDF exists at the canonical
`<stem>.pdf` (no `.degraded`/`.unchecked` infix); `PlotResult.pages == 1`.

**T3 — a dropped INSERT child is caught, and the canonical artifact is not written.**
*Setup:* `make_dxf_block_heavy`. Monkeypatch the frontend used by `check_fidelity` (or inject a recording
filter) so that exactly one block child — the CIRCLE — emits no op, simulating a lossy backend.
*Action:* `plot()` with `fidelity="strict"`.
*Expected:* `PlotError` with `kind == "fidelity"`; the message names the affected `INSERT` handle **and**
the shortfall (`recorded_ops < exploded_leaves`); `list(out_dir.glob("*.pdf")) == []`; no temp file left
behind. Via the CLI: exit code **3**.

**T4 — the same input with `--fidelity warn` produces a differently named artifact.**
*Setup/monkeypatch as T3. *Action:* `plot()` with `fidelity="warn"`.
*Expected:* no exception; `out_dir / f"{stem}.degraded.pdf"` exists; `out_dir / f"{stem}.pdf"` does **not**
exist; `report.ok is False`; a `WARNING` was logged. Via the CLI: exit 0, stdout contains
`fidelity degraded`.

**T5 — a paperspace-only DXF never yields a blank sheet.**
*Setup:* `make_dxf_paperspace_only`.
*Action a:* `plot(..., layout=None)`.
*Expected a:* `Layout1` is auto-selected (rule §4.7.3); PDF written; `report.expected_units == 2`.
*Action b:* `plot(..., layout="Model")`.
*Expected b:* `PlotError(kind="fidelity")`; the message contains `Layout1` and its unit count (F4); no PDF.
*Regression guard:* `render_dxf(src, out)` — the legacy path — still returns a `.pdf` and `.png` and still
does not raise, proving constraint 0.1.

**T6 — frozen and off layers are not reported as missing geometry.**
*Setup:* `make_dxf_frozen_block_children`.
*Action:* `plot()` with `fidelity="strict"`.
*Expected:* `report.ok is True`; the frozen LINE appears in **neither** `expected_units` nor
`expected_exploded`; `report.expected_exploded == 1` (the CIRCLE only). A test that expects `2` here has
mis-implemented visibility resolution.

**T7 — a missing SVG→PDF backend gives an actionable error, not a degraded artifact.**
*Setup:* `make_dxf_block_heavy`. Monkeypatch `shutil.which` to return `None` for `rsvg-convert`, clear
`RSVG_CONVERT_BIN`, and make `import cairosvg` raise `ImportError`.
*Action:* `plot()`.
*Expected:* `PlotError` with `kind == "backend"`; the message contains `brew install librsvg`,
`librsvg2-bin`, `RSVG_CONVERT_BIN`, and the path of the SVG that *was* written; `out_dir` contains the
`.svg` and **no** `.pdf` and **no** `.png`. Via the CLI: exit code **5**.
*And:* with `--svg-backend rsvg` while `rsvg-convert` is absent, the error names `rsvg` explicitly and does
**not** silently try `cairosvg`.

**T8 — a map plot PDF is produced with no QGIS and no GDAL.**
*Setup:* a `plot.yaml` with an extent, two GeoJSON layers (one Polygon, one LineString) written inline in
the test, no backdrop; `meta` with `status: CONCEPT`.
*Action:* `technical_drawings_for_agents map plot.yaml --out out`.
*Expected:* exit 0; `out/<number>.svg` and `out/<number>.pdf` exist; the SVG contains a scale bar, a north
arrow and the `CONCEPT` watermark (assert on the emitted markup, as
`test_example_smoke.py:31-32` does); the per-feature coverage count equals the number of input features;
`sys.modules` contains no `qgis`, `osgeo`, `gdal` or `fiona` entry after the call, and no subprocess named
`ogr2ogr`/`gdalwarp`/`qgis_process` was spawned (assert by monkeypatching `subprocess.run` to record
argv[0]).

**T9 — a misaligned backdrop is rejected.**
*Setup:* as T8 plus `backdrop.extent` offset by 5 m from `map.extent`, and a real PNG on disk.
*Expected:* `PlotError(kind="input")`; the message names both extents and points at the P8 `geo` verb;
no SVG and no PDF written.

**T10 — `--review` bundles N sheets into one N-page PDF, in the declared order.**
*Setup:* a drawing directory with `meta.yaml` carrying
`sheets: [out/A-001.svg, out/A-002.svg, out/A-003.svg]` and those three SVGs, each containing a distinct
text token (`SHEET-ONE`, `SHEET-TWO`, `SHEET-THREE`).
*Action:* `technical_drawings_for_agents plot <dir> --review out/REVIEW.pdf`.
*Expected:* exit 0; `out/REVIEW.pdf` exists; its page count is exactly **3** (read back, not assumed);
extracting text per page (or rasterising per page with `pdftoppm` and comparing against per-sheet
single-page renders) shows the tokens in declared order. With `meta.yaml`'s `sheets:` removed, the order is
`A-001, A-002, A-003` by the §3.8.3 rule.

**T11 — `--review` refuses to bundle an incomplete set.**
*Setup:* as T10 but delete `out/A-002.svg`.
*Action a:* `--review out/REVIEW.pdf`.
*Expected a:* `PlotError(kind="bundle")`; exit **4**; message names `out/A-002.svg`;
`out/REVIEW.pdf` does **not** exist.
*Action b:* add `--allow-missing`.
*Expected b:* exit **4** still; `out/REVIEW.partial.pdf` exists with **3** pages, page 2 containing
`SHEET MISSING`; `out/REVIEW.pdf` does **not** exist.

**T12 — backward compatibility.**
*Setup:* nothing new.
*Action:* run the whole existing suite unmodified, plus these explicit assertions:
1. `inspect.signature` of `render_dxf`, `render_svg`, `render_py`, `render_source`, `select_backend`,
   `dxf_has_inserts`, `find_soffice` are unchanged from the values recorded in the test as literals.
2. `select_backend(True, False) == "matplotlib"` — the degrade rule still holds
   (`test_render_routing.py:25`).
3. `render_source(example/source.py, out)` produces exactly the same **set of filenames** as
   `git stash`-clean `main` does for the worked example — i.e. no new files appear in `out/`.
4. `render_dxf` on a DXF with INSERTs and no `soffice` still returns paths and does **not** raise, and
   emits the existing `log.warning` (assert with `caplog`).
5. `technical_drawings_for_agents render <source>` with no new flags exits 0 and its stdout matches the existing
   `Rendered N artifact(s) into ...` shape.
6. `technical_drawings_for_agents bfd <data>` **without** `--pdf` produces no `.pdf` in `out/`, and `bfd.build()` returns
   only `.svg` paths.
*Expected:* all pass. Any failure here blocks the change regardless of the rest.

**T13 — the plot pipeline is byte-deterministic (P3 #55 interlock).**
*Setup:* `make_dxf_block_heavy`.
*Action:* `plot()` twice into two directories with `svg_backend="rsvg"`.
*Expected:* the two `.svg` files are byte-identical and the two `.pdf` files are byte-identical.
*Skip* if `rsvg-convert` is absent. **Do not** assert this for `cairosvg` — unverified.
*Companion (documenting, not asserting-good):* a test marked
`skipif(find_soffice() is None)` that converts the same DXF twice with `soffice` and asserts the two PDFs
**differ**, with a comment citing §2.3/§4.1 — so the reason LibreOffice is excluded is in the test suite,
not only in prose.

**T14 — optional ink check catches a blank raster.**
*Setup:* an SVG containing only a `<rect fill="#ffffff">` (visually blank), plotted with
`fidelity="off"` (there is no DXF to check) and `ink_check=True`.
*Expected:* `PlotError(kind="fidelity")`, message `plotted page 1 of 1 contains no ink`; no canonical PDF.
*Skip* if `pdftoppm` or Pillow is absent; with `--ink-check` given and the tools absent, assert
`PlotError(kind="backend")` instead.

**T15 — the ISSUED gate is untouched.**
*Setup:* a drawing directory whose `meta.yaml` has `status: CONCEPT`, `for_construction: false`, and three
sheets.
*Action:* `plot ... --review`, then `--fidelity warn`, then `--allow-missing` with a sheet deleted.
*Expected:* after every invocation, `meta.yaml` is **byte-identical** to before, and no code path in
`plot.py`/`mapplot.py` references `for_construction` or assigns to `status` (assert by source inspection in
the test: read the two module sources and assert the tokens are absent).

---

## 6. Out of scope

An implementer must **not** do these. Where a follow-up is warranted, the issue to open is named.

1. **Do not change any existing render default.** Not the auto-routing, not the backend order in
   `render_svg`, not `render_py`'s DXF-only globbing, not the artifact filenames. → follow-up issue
   *"retire the LibreOffice DXF render backend; make `plot` the default for `render`"* (§4.1).
2. **Do not delete or rewrite `render.py`'s backends.** Docstring correction and one `DeprecationWarning`
   only.
3. **Do not touch the raster/geo preparation pipeline.** **P8 (#60) owns** `geo.yaml`, `gdalwarp`,
   `gdal_translate`, `ogr2ogr`, the pre-clipped backdrop sidecar and the "no base64 embed" decision. This
   spec owns the **plot** — it *consumes* an already-clipped, already-projected backdrop and refuses a
   misaligned one (§3.10, T9). Do not add GDAL, OGR, `pyproj`, `fiona` or PyQGIS to `technical_drawings_for_agents`'s
   dependencies here.
4. **Do not implement the output manifest, the provenance stamp, float rounding or `SOURCE_DATE_EPOCH`
   handling.** **P3 (#55) owns** those. Do expose `PlotResult` / `FidelityReport` as frozen dataclasses P3
   can serialise, and do record the resolved backend name (§4.5).
5. **Do not implement the build driver / DAG or sheet-set orchestration.** P2 (#54) territory. `--review`
   collates artifacts that already exist; it does not build them, and it does not decide staleness.
6. **Do not add an AGPL dependency** (`PyMuPDF`) to any default path (§4.1).
7. **Do not add a fidelity tolerance knob.** `strict|warn|off` is the whole surface (§4.4).
8. **Do not draw anything new on a sheet** — no "DEGRADED" banner, no status stamp, no provenance hash
   (that is P3's, and it goes in the title block via the title-block furniture, not via the plotter).
   The one exception is the `--allow-missing` placeholder **page**, which is a generated page, never an
   edit to a real sheet.
9. **Do not add a `sitemap` verb.** The site GA sheet is still an out-of-tool project script; migrating it
   into the toolkit is a separate piece of work. What *this* change gives it is a supported `plot()` /
   `technical_drawings_for_agents plot out/STA-SITE-GA-001.svg` to call instead of its own `rsvg-convert` shell-out. →
   follow-up issue *"migrate the basin-site GA sheet onto technical_drawings_for_agents's plot + a site-sheet assembler"*
   (the script already flags this at `source.py:29-32`).
10. **Do not weaken or delete `tests/test_render_routing.py`.** Add new tests in new files.

---

## 7. Risks and migration

| # | Risk | Likelihood | Mitigation |
|---|---|---|---|
| R1 | **CI goes red because `librsvg2-bin` is not yet installed** and the new tests need it. | High if the workflow edit is forgotten | The `build.yml` edit (§3.9) ships in the **same** commit as the tests. Every backend-dependent test is `skipif`-guarded with a reason and `pytest -q -rs` already logs skips — so a forgotten install shows as a visible skip list, not a red build *and* not a silent green. Reviewers must check the skip list; a PR where T2/T10/T13 all skip has not been tested. |
| R2 | **`ezdxf>=1.1` → `>=1.4` breaks an environment pinned lower.** | Low | Verified only at 1.4.4, so claiming 1.1 would violate constraint 0.2. Add an import-time guard in `plot.py`: if `ezdxf.version < (1, 4)`, raise `PlotError(kind="backend")` naming the installed version and the requirement. `render.py` keeps working on older ezdxf because it does not import the new modules. |
| R3 | **The site GA script keeps its own `rsvg-convert` call** and drifts from the toolkit. | Medium | Out of scope to migrate (§6.9), but this change makes `rsvg-convert` a *declared* dependency with a documented install line, so the script stops being the only thing that knows about it. Follow-up issue opened. |
| R4 | **A real ingested vendor drawing fails the new fidelity check** for a reason the synthetic fixtures do not cover (proxy entities, unrendered `DIMENSION` per D5, deeply nested xrefs). | Medium-high — this is the point of the check | This is the check working, not a bug. `--fidelity warn` gives a triage artifact immediately, named `.degraded`. D5 specifically must be caught and re-raised as `PlotError(kind="fidelity")` naming the dimension handle, instead of the raw `AttributeError` from `ezdxf/entities/dimension.py:806`. Real vendor drawings are confidential and must **not** be committed as fixtures (`tests/synthetic.py:3-6`); reproduce their *structure* synthetically. |
| R5 | **The `--review` ordering rule surprises someone** whose filenames do not match the number/index regex. | Medium | Ordering is deterministic and total in every branch, `discover_sheets()` is public and unit-tested, and the CLI prints the resolved sheet order to stdout before bundling — so the order is inspectable without reading the code. `meta.yaml`'s `sheets:` is the explicit override. |
| R6 | **`meta.yaml` gains a `sheets:` key that `DrawingMeta` might reject.** | Low | §3.8.2 requires the implementer to *verify* `DrawingMeta.from_mapping` tolerates unknown keys (it reads a fixed field list, `meta.py:38-56`) and, if not, to read `sheets:` from the raw YAML instead. `meta.py` and `validate.py` are not modified either way. T15 asserts `meta.yaml` is never written. |
| R7 | **`plot` and `render` diverging confuses users** — two verbs that both make PDFs. | Medium | Documented in the README as an explicit transition: `render` = legacy, frozen, LibreOffice-routed; `plot` = the tested path with the fidelity check. The `DeprecationWarning` on the LibreOffice route points at `plot`. The follow-up issue (§4.1) closes the divergence in one deliberate step with a parity test, rather than drifting. |
| R8 | **A reviewer trusts a `.degraded.pdf`** because they did not notice the infix. | Low-medium | The infix is only reachable by explicitly passing `--fidelity warn`, the CLI exits with a `fidelity degraded` line on stdout, and the canonical name is never produced in that mode. If this proves insufficient in practice, the next step is to remove `warn` entirely — not to add a banner to the drawing (§4.2). |

**Migration.** Nothing to migrate on day one: every existing caller keeps working untouched, because
`plot` is additive and every opt-in defaults off. The intended sequence is (1) land this, (2) move the site
GA script and the generators onto `--pdf`/`plot` one at a time, each with a parity check that the SVG it
already produced is unchanged, (3) once every consumer is on `plot`, land the follow-up that retires the
LibreOffice DXF route and repoints `render` at `plot`. Step 3 is a separate issue with its own parity
tests, and it is the only step that changes anybody's output.
