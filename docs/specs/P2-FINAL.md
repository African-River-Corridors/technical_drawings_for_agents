# P2 — FINAL specification and implementation plan

**Status:** authoritative. Supersedes `P2-build-driver-dag.md` **and its ADDENDUM** where it says so below.

**Provenance.** Base spec by an Opus agent (PR #64), plus a coordinator addendum (PR #97). Implemented
independently by variant **A** (upstream #108, 3423+/0−,
**2 failed / 622 passed**) and variant **B** (upstream #105,
3214+/0−, **2 failed / 623 passed**).

**Both failed, and two of the causes are defects in my own addendum.** P2 is the largest change in the
programme — it is what removes the agent from the build path — so the corrections below are the substance.

---

## 1. My addendum A1 was incomplete — the output key needs a normalisation rule

Variant A failed `test_geopackage_output_ownership_is_at_layer_granularity`:

```
Expected regex: 'site.gpkg:layout_poly'
Actual message: 'drawing-set.yaml: output claimed by 2 nodes (a, b): .:layout_poly'
```

The duplicate-claim detection worked — two nodes *were* caught claiming one layer. But the path collapsed to
`.`, because addendum A1 said outputs are keyed `<gpkg-path>:<layer>` **without specifying how the path
component is derived**.

### CORRECTION 1 (binding) — the output key is `<set-relative POSIX path>:<layer>`

- The path component is the target's path **relative to the drawing-set file's directory**, in **POSIX form**
  (forward slashes), with no `./` prefix and no absolute paths.
- The layer component is the layer name verbatim.
- Uniqueness is validated on that exact string, and **the error message must quote it verbatim** so a human
  can grep the set file for it.
- Two nodes claiming the same key is a hard error naming **both node ids and the key**.

Rationale for set-relative rather than absolute: the key appears in error messages and in state, and an
absolute path makes both machine-specific — the same mistake P3-FINAL Correction 2 corrected in the digest.

## 2. My addendum A4 was under-specified — "a sheet" was never defined

Both variants independently declined to enforce "every build ends in a reviewable PDF" globally, and **both
were right to.** A: *"The PDF addendum is under-specified for synthetic non-sheet test sets."* B: *"lacks a
schema marker for 'this target is a sheet'. Enforcing it globally would break non-sheet/intermediate builds."*

I wrote a requirement without giving the build any way to tell a sheet from an intermediate.

### CORRECTION 2 (binding) — sheet-ness is declared, never inferred

- A target may carry **`review: pdf`** in the set file. That marker, and only that marker, makes it a
  review target.
- **Every `review: pdf` target must produce its PDF** in a default `build`; if it cannot, the build **fails**
  naming the target. It never succeeds having emitted only intermediates.
- A target **without** the marker has no PDF obligation. Intermediates, GeoJSON, staged layers and synthetic
  test nodes are unaffected.
- **`build --check`** verifies each `review: pdf` target's PDF is present and current, and writes nothing.
- The standard's rule 6 ("emit standard artifacts *and* always finish with a PDF") is then enforced **per
  declared review target**, which is what it always meant — a set with no sheets has no PDF to produce.

`drawing-set.yaml` for a real project must mark its sheet targets `review: pdf`. Document this in the README as
the mechanism by which the pipeline contract's rule 6 is satisfied.

## 3. Base-spec defect — A18 and S4 deadlock on external nodes

Both variants hit this and **both invented the same escape**, which is the signal that it must be specified.

- **S4:** a node with no state record is `unbuilt`.
- **A18:** an `external` node's output, once the human has created it, must become *current*.
- **But external nodes cannot be run by the build** (that is their whole point).

So an external output that exists but has no state record is permanently `unbuilt` and blocks everything
downstream. A: *"I implemented that adoption behavior."* B: *"Recording existing external outputs is the only
workable path."*

### CORRECTION 3 (binding) — adopt an existing external output into state, without running anything

- When an `external` node's declared output **exists** and has **no state record**, the build **records its
  current digest into state** and treats it as current. No process is spawned.
- Adoption is **logged** — it is a state change the operator should see, not a silent promotion.
- If the output does **not** exist, the build fails naming the external node and telling the human what to
  produce (this is the QGIS-placements case: the human drags footprints, the build cannot).
- Adoption never applies to a non-external node: for those, absent state still means `unbuilt`.

## 4. Base-spec nit — the `verb:bfd` argument-change fixture

Acceptance test A4 changes a `bfd` node's arg `block → swimlane`, but `bfd.build()` **changes the output
filename by view**, so the change alters *which file is produced* rather than its content — the test cannot
observe a rebuild of the same target.

**Resolution:** keep the intent (changing a validated node arg rebuilds that node without touching inputs) and
pin it on a verb whose output path is arg-invariant. Variant B did exactly this. Do not contort `bfd`.

---

## 5. Implementation basis

**Neither variant is a clean base.** They are within one test of each other (A: 2 failed / 622 passed;
B: 2 failed / 623 passed) and their failures are different:

- **A** failed the layer-granularity test (Correction 1) and a byte-parity test between `render` and `build`
  (`At index 2585 diff: b'2' != b'3'`) — a **one-character** difference, which given P3's canonical-write
  policy is almost certainly a route that bypasses `write_text_canonical`, exactly as P9's variant A did.
- **B** failed `test_changing_a_node_arg_rebuilds_that_node` — the §4 fixture defect.

**Take A as the structural base** (larger, and its GeoPackage handling is closer to Correction 1: it
implemented layer uniqueness plus a deterministic SQLite table digest for `gpkg:layer`), and **graft B's A4
fixture resolution** (§4) and B's explicit statement of the CLI-inventory drift.

**Graft from B:** its observation that the base spec's CLI inventory **predates the current `manifest` and
`sheet` verbs**. The final build must expect **all current verbs plus `build`** — the spec's list is stale
because P1 and P3 landed after it was written.

**Adopt from both (they agreed):** use **P3's `emit_digest()` first** for input/output digests, with a raw
SHA-256 **local build-cache fallback only** for formats `provenance` refuses to assert (PDFs, intermediates).
That is the correct reading of P3-FINAL §4 — `emit_digest` raises rather than returning a meaningless value, so
the caller needs a declared fallback for cache purposes that is never presented as a reproducibility claim.

---

## 6. Carried forward unchanged from the base spec

The base spec's corrections to the original issue were all verified and stand:

- **"Export the GeoPackage layer" is not a recorded command anywhere** — the only `ogr2ogr` in the project is
  the pond KMZ conversion; the placements export has been a **QGIS GUI action to a `/tmp` path**, so it has no
  reproducible artifact. Now partly addressed by P8, but the set file must still model it honestly.
- **`rsvg-convert` is not a pipeline step** — it is shelled out to from *inside* `source.py:279`.
- **The sheet reads `basin.effective.yaml` (post-snap), not `basin.placements.yaml`.** A graph wired to the
  as-placed register would be wrong.
- **There is a human step upstream of the export** — dragging footprints in QGIS, which mutates the gpkg. That
  is the real head of the chain and is an `external` node.
- **`generate_layout.py` is a superseded one-off and must not become a node.**
- **`drawings/basin-site/` has no `meta.yaml`**, so a `validate` node on it fails today — correctly.
- **Project `source.py` runs as a subprocess**, justified because `SystemExit` is a `BaseException` that
  escapes `_cmd_render`'s `except Exception`: in-process, one failing script kills the driver.
- **Serial execution with a lexicographic tie-break**, so reordering the YAML cannot change build order.
- **Missing tools fail before the first byte is written.**
- **Content hashing, not mtime**, with a size+mtime fast path as an optimisation only (the 484 MB ortho).

## 7. Implementation plan

1. Base on variant A; graft §4 and §5 from B.
2. Apply Corrections 1–3.
3. Route **all** text writes through P3's `write_text_canonical` — that is almost certainly A's one-character
   parity failure.
4. Full suite green. Predecessor states: A `2 failed / 622 passed`, B `2 failed / 623 passed`. Target
   **0 failed**.
5. **Run `build` for real on the actual Basin drawing set** and paste the output: a cold build, a no-op rebuild,
   a single-input touch rebuilding exactly the affected targets, `--check` on a stale target exiting non-zero
   and naming it, and the `placements`-as-output refusal.

## 8. Constraints (absolute)

- **Every existing verb keeps working standalone, unchanged.**
- **The `placements` layer is an input the build may never write**, and `--force` cannot override it.
- **No pipeline step may require a GUI.** Human/GUI steps are explicit `external` nodes that block the build.
- **The ISSUED gate is untouchable** — a build must never promote a drawing's status. A sibling variant was
  disqualified for breaking P1's gate test.
- **Loud failure over silent degradation** — a cycle, a missing input, or a duplicate output claim is a clear
  error, never a partial build.
- **Never assert byte-equality against `drawings/example/*/out/`** (issue #101).
