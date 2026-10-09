# P7 — FINAL specification and implementation plan

**Status:** authoritative. Supersedes `P7-layer-table.md` where it says so below.

**Provenance.** Base spec by an Opus agent (PR #66). Implemented independently by variant **A**
(upstream #95, 1952+/35−, **1 failed / 631 passed**) and variant
**B** (upstream #96, 1866+/35−, **3 failed / 629 passed**).

Both failed the `python` job on the same test. **This is the fourth pair in the programme to fail identically**
— and this time the cause is not in the spec's design but in a repo-level trap that neither variant could have
known about.

---

## 1. The shared failure is not P7's fault

Both wrote `test_committed_example_dxf_regenerates_unchanged`, comparing a fresh render against the
**committed** `drawings/example/simple-section/out/EXA-CIV-SEC-001.dxf`. Verified on **clean `main` with no P7
change present**, that comparison fails in exactly **six lines of 13,532**:

```
496, 504     $TDCREATE / $TDUPDATE     2461237.6464236113 -> 2461247.3949189815
936, 940     $FINGERPRINTGUID / $VERSIONGUID   (ezdxf re-rolls both every save)
6966, 13528  "1.4.4 @ 2026-07-15T14:30:51" -> "1.4.4 @ 2026-07-25T08:28:41"
```

Those are precisely the six volatile fields P3 catalogued. The committed artifact predates P3, and the
example's `source.py` does not opt into P3's deterministic policy, so a fresh render always carries fresh
metadata. **The assertion is unsatisfiable by construction; neither variant regressed anything.**

### CORRECTION 1 (binding) — compare masked content against P3's golden

P3 shipped the right target. Verified: a fresh render passed through
`provenance.mask_dxf_volatiles()` is **byte-identical** to `tests/goldens/EXA-CIV-SEC-001.dxf.masked`.

- **Do:** assert `mask_dxf_volatiles(fresh) == tests/goldens/EXA-CIV-SEC-001.dxf.masked`.
- **Do:** prove backward-compat parity as a **code A/B against `origin/main`** on the same toolchain, which is
  what P3's own build did (five generator paths byte-identical).
- **Do not** assert byte-equality against anything under `drawings/example/*/out/`.
- **Do not** pin a literal `sha256` (P3-FINAL Correction 4: a hard-coded digest cannot distinguish "we
  regressed" from "a dependency updated").

Tracked repo-wide as **#101**, because a note in P3's spec did not stop a sibling PR walking into it — two of
two variants did.

---

## 2. Variant A is the base

A: **1 failure**, and it is the trap above. B: **3 failures**, including
`test_pdf_pens_match_the_layer_table` — a failure of P7's *own* central claim, that the three artifacts agree.

**Basis: variant A.** Fix Correction 1 and A is green.

### Graft from A's judgement calls (all sound, adopt as specified)

- **`DxfBuilder` signature compatibility:** `layer_table` and `plot_scale` are added **after** the existing
  `policy` parameter, preserving positional compatibility for every current caller.
- **The packaged example's `EQUIPMENT` layer is left unchanged**, so no-table output is untouched. Opting into
  the default table still fails loudly on the `EQUIPMENT`/`EQUIP` mismatch, exactly as the base spec requires.
- **Reuse `tests/goldens/`** — do not create a second golden directory.

---

## 3. Spec defect — the linetype `add()` call shape contradicts ezdxf

### CORRECTION 2 (binding)

The base spec's wording for registering a custom linetype conflicts with `ezdxf`'s documented **simple
pattern** format. `ezdxf` requires `pattern=[total_pattern_length, elem1, elem2, …]` — the **total length
first**, then the dash/gap elements. The base spec's call shape omits the total, yet its own expected DXF
group-`40`/`49` tags require it.

**Follow `ezdxf`'s documented format** (variant A did, citing the ezdxf linetype tutorial). The base spec's
expected tags are correct; only its call shape was wrong.

---

## 4. Carried forward unchanged — the base spec's measured findings

The base spec verified each of these empirically against **ezdxf 1.4.4**; none may be softened:

- **The issue's "no ACI colour" claim was wrong** for the eight built-in layers (`dxf.py:44-46` does write
  ACI). What is genuinely missing for them is `lineweight` (all `-3` = host default), `linetype` (all
  `Continuous`), `plot`, `description`, and `$LWDISPLAY` (`0`, so AutoCAD does not even display lineweights).
- **It is worse than the issue stated for the layers we actually use.** ezdxf creates **no layer record** for
  an entity on an undeclared layer and `ezdxf.audit` does not flag it — so shipped component/project DXFs have
  geometry on `FDN` / `EQUIP` / `NOZZLE` / `TEXT` while the layer table holds only `['0', 'Defpoints']`.
- **`ezdxf.new(setup=True)` ships no `HIDDEN` linetype**, though the issue names a `HIDDEN` layer.
- **Stock linetypes are unusable in a metre model space:** `DASHED` is a **1.27-metre** dash, so it renders
  solid on a 0.28 m pump. Patterns are therefore authored in **paper mm** and converted at plot scale for DXF,
  and via px-per-mm for SVG.
- **`aci2rgb(7)` is pure white** — invisible on the white background `components/cli.py:78` already writes.
  ACI 7 resolves to "foreground", never literal white.
- **The layer-0 rule must skip `*`/`_` block definitions.** The shipped example's dimension block legitimately
  holds three `LINE`s on layer `0` plus `_ARCHTICK`, so a naive check fires on every dimensioned drawing. Keep
  the dedicated false-positive test.
- **`RenderContext.resolve_all` already reads the layer table** (returning `lineweight == 0.35` mm and
  `#00ff00` from a layer record), so populating the table *is* most of the PDF-pen work — and the
  "PDF pens match" test can assert at that level rather than on a raster.
- **Byte-identical backward compat needs `ezdxf.options.write_fixed_meta_data_for_testing`** — now redundant
  with P3 shipped, so **use P3's policy rather than setting the option directly.**

---

## 5. Open question retained for the owner

The base spec listed eight. The one that still needs an answer:

**`EQUIPMENT` vs `EQUIP` is a real collision** — the packaged example uses `EQUIPMENT`; the live plant component
specs use `EQUIP` (×12), alongside `NOZZLE` (×21), `FDN` (×19) and `TEXT` (×6). The base spec deliberately
offers **no alias mechanism**, so one of the two must change. That is a project-data decision, not a tooling
one. Until it is answered, opting the packaged example into the default table fails loudly — which is the
correct behaviour, not a bug to work around.

Every ACI number and lineweight in the default table remains labelled "preserved from today's code" or
"common CAD convention". **None is presented as an ISO citation.**

## 6. Implementation plan

1. Base on variant A.
2. Apply Correction 1 (masked golden, per #101) and Correction 2 (ezdxf pattern format).
3. Use P3's deterministic policy rather than toggling `ezdxf.options` directly.
4. Full suite green — predecessor state A: `1 failed, 631 passed`; target `0 failed`.
5. Verify the DXF by **reading it back with `ezdxf`** (layer records, ACI, lineweight, linetype table), and the
   PDF pens via `RenderContext.resolve_all` — not by eye.

## 7. Constraints (absolute)

- **Backward compatibility is sacred.** With **no layer table supplied**, DXF and SVG output must be
  byte-identical to today. This is explicitly testable and is a required test.
- **Never invent a standard.** Flag anything the owner must confirm as an open question.
- **The ISSUED gate is untouchable.**
- **Loud failure over a silent no-op** — an undeclared layer is a finding, not an auto-create.
