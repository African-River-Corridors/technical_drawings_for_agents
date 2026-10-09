# P4 — FINAL specification and implementation plan

**Status:** authoritative. Supersedes `P4-pdf-plot-path-fidelity.md` where it says so below.

**Provenance.** Base spec by an Opus agent (PR #70). Variant **B** (upstream #110, Codex, 1200+/0−, **0 test functions**). Variant **A** (upstream #116, **Claude**, 4946+/46−, **85 test functions**, 346 → 434 passed, delta **+88**).

**Backend substitution, disclosed.** Codex began returning `503 … biscuit_baker_service_me_circuit_open` mid-programme, and two attempts at a Codex variant A produced no changes at all. Rather than block, variant A was produced by a **Claude** agent. The design that matters — two independent implementations of one spec, judged against each other — is intact; only the backend differs. This is recorded rather than quietly swapped.

---

## 1. Variant B is unusable: 1200 lines, zero tests

B added **no test functions**. It is CI-green for exactly one reason: **a PR that adds no tests cannot fail the suite.** The base spec lists 15 numbered acceptance tests; B implemented none, and delivered no README either.

### This is a backend-degradation signature, not a one-off

Test-function deltas across every Codex variant in this programme:

| Variant | Dispatched | tests added |
|---|---|---|
| P5-a | before the outage | 20 |
| P9-a | before | 26 |
| P2-a | before | 36 |
| P2-b | before | 37 |
| **P6-a** | **during the 503 window** | **0** |
| **P6-b** | **during** | **0** |
| **P4-b** | **during** | **0** |

Clean correlation: Codex wrote tests properly until the outage, then produced substantial, plausible, **CI-green, test-free** code. A degraded backend does not fail loudly — it **truncates**, dropping the last step of the brief.

**Operational rule, earned:** the only defence is the coordinator's diff review, specifically the **test-count delta**. Check it on every agent PR, always, and treat a zero delta on a feature PR as a failure regardless of CI colour. This is now the second lesson in the programme pointing at the same control (the first came from P6).

---

## 2. Variant A is the base — and it is build-quality already

Green, all 15 spec tests, **+88 net tests**, and three real spec corrections. Two design choices exceed the spec:

- **The fidelity check replays the `Recorder` recording into the SVG backend**, so the ops asserted on are *literally the ops serialised*. The spec permitted either; replay is strictly stronger and should be the requirement.
- **`plot()` is one entry point** with `PlotError` carrying `.kind`/`.exit_code`, frozen value objects, no LibreOffice, and no fallback past the chain's end.

### Process note — no separate Phase-4 build

Because Codex was unavailable, variant A was produced by an **Opus** agent working to build standards (real venv, real runs, full test suite, spec corrections escalated). A separate Phase-4 build pass would be redundant re-work on already-verified code. **A is adopted as the build** after coordinator verification. Stating this plainly rather than performing a step for its own sake.

---

## 3. Three spec defects A found

### CORRECTION 1 (binding) — the determinism table is wrong; `rsvg-convert -f pdf` is NOT deterministic

Two conversions of a **byte-identical** SVG, 1.6 s apart, differ (`f1f68891…` vs `6bf61af3…`): cairo embeds a time-derived id in a compressed stream. It **does** honour `SOURCE_DATE_EPOCH`.

The base spec measured two runs **inside one wall-clock second**, which is why it recorded the path as deterministic. **Acceptance test T13 as written is flaky.**

**Resolution:** forward **P3's resolved epoch** into the converter environment; assert the **SVG stage unconditionally** and the PDF stage **pinned to that epoch**; and pin the mechanism so the claim cannot rot silently.

This is the same class of error as P3's own "0 differing lines" measurement, which had been taken with the ezdxf option set globally before document creation. **A determinism claim is only as good as the interval it was measured over.**

### CORRECTION 2 (binding) — §3.10's backdrop design builds the silent-blank bug this PR exists to prevent

`os.path.relpath` on the spec's **own example** yields `../geo/x.png`. P8's shipped `backdrop.py` records that librsvg **silently drops a parent-escaping href**: exit 0, empty stderr, blank backdrop.

A's report is worth quoting: *"I wrote that bug first and my own test passed while the PDF was wrong."*

**Resolution:** `map.backdrop` accepts `manifest:` and delegates **wholly** to `technical_drawings_for_agents.backdrop` (P8's shipped module); the plain form enforces the same href policy. **Consume the sibling rather than re-deriving its hard-won constraint.**

This is the fourth instance in the programme of a tool exiting 0 while producing a useless artifact. The standing rule holds: **never trust an exit code; measure the output.**

### CORRECTION 3 (binding) — `expected_exploded` is under-specified, and F2 must stay per-INSERT

T2's `==12` and T6's `==1` reconcile **only** if the count is leaves of `INSERT` units *only*. This matters concretely: a rendered `DIMENSION` yields **6 ops from a 9-leaf walk**, so an *aggregate* `recorded_ops >= expected_exploded` assertion would fail on **any dimensioned drawing** — which is precisely what P6 is building.

**F2 stays a per-INSERT one-sided bound.** Never aggregate it.

---

## 4. Carried forward — the base spec's measured corrections to the issue

All verified, all binding:

- **Issue #56's central premise was false, both halves.** The ezdxf frontend traverses blocks correctly (4 recorded ops on the repo's own `make_dxf_with_insert`); **LibreOffice loses the geometry** (705 non-white px of 842×596, LINE and CIRCLE absent) and is non-deterministic. `render.py` routed block-heavy **vendor** drawings to the worse backend. Live defect **#72**. LibreOffice is dropped from the new path and must not be reinstated.
- **`render.py:170` plots `doc.modelspace()` only**, so a paperspace drawing yields a blank PDF at exit 0.
- **The old guard asserted `st_size > 0`**, which a blank PDF passes — green while asserting nothing.
- **`PyMuPdfBackend` is rejected: PyMuPDF is AGPL** while the package declares `Proprietary` (**#74**).
- **Frozen/off layers and the `invisible` flag are honoured by ezdxf** — correct CAD semantics, so excluded from the expectation rather than "fixed".
- **A degraded artifact may only ever land as `*.degraded.pdf`**, so `<stem>.pdf` *means* "passed every check".

## 5. Open items A flagged (not blocking)

- **§3.8.3's non-greedy ordering regex** orders `B-010` before `B-002_S2`. Implemented to the letter; the wart is documented in the test. Needs a decision on intended sheet ordering.
- **`--review` page-count verification is unsatisfiable without `pypdf`/poppler**, so the `plot` extra is effectively **required** for `--review`, not optional. Declare it as such.
- **Default sheet colour differs by source** — white for DXF plots, the house dark theme for SVG-authored sheets, from the same verb. Inconsistent; needs a house decision.

## 6. Constraints (absolute)

- **Never pass off a degraded artifact as success.** An incomplete review artifact is worse than none, because it will be trusted.
- **Backward compatibility:** `render.py` behaviour frozen; new exit codes ≥ 3 so 0/1/2 keep their meaning.
- **Never assert byte-equality against `drawings/example/*/out/`** (#101); never pin a literal output `sha256`.
- **The ISSUED gate is untouchable.**
- **Report the test-count delta.** A zero delta on a feature PR is a failure whatever CI says.
