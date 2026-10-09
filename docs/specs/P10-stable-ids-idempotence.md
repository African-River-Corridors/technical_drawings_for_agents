# P10 — stable placement ids + whole-pipeline idempotence

Implementation specification. **Refs:** the upstream repo #62 (this change), #52 (join key /
site-plan de-duplication), #55 (P3 deterministic emit — a named dependency), #50 (site GA sheet — not yet built).
Absorbs review items 26 and 27 of `03-Resources/DEMO Water Project/drawings/Drawing workflow — 30-change
robustness review.md`.

Status: **specification only**. This document is what two independent implementers must be able to build
from and arrive at the same code. It contains no code changes.

---

## 0. Binding constraints (restated, and they override anything below)

1. **Backward compatibility is sacred.** The two existing Basin registers
   (`03-Resources/DEMO Water Project/components/basin-wtp/basin.placements.yaml` and `basin.effective.yaml`)
   must keep loading. **No emitted geometry value may change** — no coordinate, rotation, size or feature
   property. Record *order* in generated text artifacts does change; that is the point of the change, it is
   handled by a one-off migration commit (§7), and it is called out honestly rather than papered over (§8).
2. **Never invent a capability or a project fact.** Where this spec needs a fact it does not have, it is an
   Open Question in §9, not a guess. In particular: no id, tag, bearing or dimension is fabricated by the tool.
3. **The ISSUED gate is untouchable.** Nothing in P10 reads or writes `meta.yaml` status, watermarks, or
   `for_construction`. No tool, CI run, or agent may flip a drawing to ISSUED FOR CONSTRUCTION.
4. **Determinism is the whole point: prefer loud failure over a silent no-op.** A silently reordered register
   is the exact failure mode being fixed. Every ambiguity in this design resolves to a `LayoutError` with a
   precise message, never to a heuristic guess or a quiet skip. Where a no-op is genuinely correct (nothing
   changed), it is *reported*, not silent.
5. **House style.** `src/technical_drawings_for_agents/components/layout.py` is the reference pattern: frozen dataclasses, one
   module-specific error class (`LayoutError`, `layout.py:40`), config validated on load with precise messages
   naming the offending path, tests in `tests/test_*.py` named for the behaviour they assert. `pyproject.toml`
   sets `line-length = 100`; Python `>=3.10`.

---

## 1. Intent

A generated placements register is only worth having if `git diff` answers "what changed on site?" in one
screen. Today it does not: the register is written in GeoJSON feature order (`layout.py:405`, `layout.py:434`),
so re-exporting the QGIS layer after a rebuild, a re-order, or a delete-and-re-add can rewrite all ten records
while nothing physical moved. That is worse than having no register, because it *looks* like a change log and
lies. **Good** is: one physical edit produces one record's worth of diff; zero physical edits produce zero
bytes of diff; and running the loop twice on unchanged inputs produces byte-identical artifacts, so
"regenerate and diff" is a real answer to "did anything change?" — the fifth rule of the *Engineering Drawings
as Code* §Pipeline contract. The failure this prevents is the one that already happened on this project: the
FA-130 rafts sat at 38.9°/41.6° in the placed layer while `site-plan.yaml` said 0°, and it went unnoticed for
a week (#52) because nobody could tell signal from noise in the files that were supposed to show it.

There is a hard limit on how much of that this change can buy, and naming it is half the value of this
specification: **you cannot manufacture durable identity out of an export that carries none.** The register
can be made canonically ordered and provably idempotent with no identity at all — and that is what P10
delivers. Durable identity has to be *authored* (a `tag` or an `id` typed into the QGIS attribute form), and
P10's job is to make that field first-class, unique, validated, and its absence **loud and counted in the
artifact** — not to fake it with a sequence number or a geometry hash that breaks the moment something moves.

---

## 2. Current state (read from the code and the live data)

### 2.1 The register is emitted in source order, with no identity

- `derive_placements` (`layout.py:384-435`) iterates `geojson["features"]` in source order (`for index,
  feature in enumerate(features)`, `layout.py:405`) and appends each derived instance in that order
  (`layout.py:434`). There is no sort anywhere in the function.
- The emitted instance mapping is fixed at `type, origin_utm, rotation_deg, size_m` plus `tag` when the export
  carries a non-blank one (`layout.py:425-433`). Values are rounded with `round(v, round_to)`, `round_to=3`.
- `dump_placements_register` (`layout.py:452-470`) writes a 5-line comment header then
  `yaml.safe_dump({"instances": instances}, sort_keys=False, default_flow_style=None, width=100)`. Because
  `sort_keys=False`, record key order is the dict insertion order — so key order is already stable, but record
  order is entirely the caller's.
- `_refresh_register` (`components/cli.py:194-234`) reads the config shallowly, derives, and **writes
  unconditionally** (`cli.py:231-233`) — so a no-change re-run rewrites the file and bumps its mtime even when
  the bytes are identical.
- `_load_placements_block` (`layout.py:270-283`) reads `instances:` and maps them through `_placement(item,
  index)` **in file order**; `Layout.placements` is therefore file order, and nothing re-sorts it.
- `Placement` (`layout.py:49-62`) has `type, origin, rotation_deg, tag, size_m, properties`. **There is no
  `id` field.** `label` is `tag or type` (`layout.py:60-62`) — which is why eight of the ten Basin records
  render in findings as the bare word `dosing-skid` / `big-pump`, indistinguishable from each other.
- `_placement`'s `reserved` set is `{"type", "origin_utm", "rotation_deg", "size_m", "tag"}` (`layout.py:305`).
  Anything else in a record lands in `Placement.properties`, and `layout_instance_properties`
  (`layout.py:627-641`) copies `properties` into every emitted GeoJSON feature. So an `id:` key in a register
  today would silently become a per-feature GeoJSON property — a trap the implementer must close (§3.6).
- `_placement_instance` (`cli.py:237-250`) is the reverse serialiser used by `--emit-register`; it emits
  `type, origin_utm, rotation_deg, size_m, tag` then `instance.update(placement.properties)` (`cli.py:249`).
  Its rounding (3 dp) and key order match `derive_placements` — good, but they are **two separate
  implementations of the same contract**, which is precisely how the two ends of a round-trip drift apart.
- `run_layout` (`cli.py:127-191`) exit codes today: **2** for a `LayoutError`/`ComponentSpecError`
  (`cli.py:139`), **1** for any `severity: error` finding unless `--warn-only` (`cli.py:189-191`), else **0**.
- `build_layout` (`layout.py:604-624`) and `_emit_layout` (`cli.py:253-277`) both walk
  `layout.placements` in order, so the emitted GeoJSON `features` array order is a direct function of register
  record order.
- Nothing in `tests/test_layout.py` (771 lines) asserts register order, byte stability, or a second-run
  no-op. `test_snap_is_idempotent` (`tests/test_layout.py:629-638`) proves only that `snap_groups` is a fixed
  point on *poses*, with `pytest.approx`, not on bytes.

### 2.2 The real Basin data

`components/basin-wtp/basin.placements.yaml` — 10 instances. **Two carry a `tag`** (`STA-A`, `STA-B`, both
`type: clarifier`); **eight carry nothing but a type and a pose**, and three of those eight are
`type: dosing-skid`, mutually distinguishable *only* by coordinates. `basin.effective.yaml` is the same ten
post-snap, the two clarifiers additionally carrying `snapped_by: FA-130 rafts`. Neither file contains an `id:`
key (verified by grep, as does `src/technical_drawings_for_agents/components/examples/site_placements.yaml`), so adding the
field breaks nothing.

The editor layer, read directly out of `drawings/basin-site/qgis/basin-site.gpkg`:

```sql
CREATE TABLE "placements" ("fid" INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                           "geom" POLYGON, "type" TEXT, "tag" TEXT, "notes" TEXT);
```

11 features, fids `1,2,4,5,6,7,8,10,11,12,13` — **gaps at 3 and 9: features have already been deleted from
this layer.** Attribute state: only fids 1 and 2 have a `tag`; the other nine have `NULL`. The register has 10
records because fid 10 (`control-station-admin-stores-accommodation`, centroid `788639.000, 322380.000`) falls
inside the declared parked bbox `[788312, 322377, 788912, 322395]` in `basin.layout.yaml` and is excluded by
`derive_placements`' `exclude` path (`layout.py:421-423`). The register's record order is exactly ascending
`fid` order — i.e. today's register order is an artifact of the editor's internal autoincrement counter.

### 2.3 How placements are actually authored — what constrains identity

`drawings/basin-site/qgis/placements — how to use.md` is explicit, and it is the binding constraint on any id
scheme: *"**Don't hand-draw new rectangles** (dimensions would drift) — always **copy** a correctly-sized one
and move/rotate it"*, then *"Set **type** (must match the list below) and optional **tag** / **notes** in the
attribute form."* So the authoring primitives are **copy → paste → move → rotate → set attributes**, and the
tag is documented as **optional**. Three consequences:

- **Move and rotate are the normal operations.** Any identity derived from geometry is destroyed by the normal
  operation.
- **Copy-paste duplicates attributes.** A pasted feature arrives carrying the source feature's `type`, `tag`
  and `notes`. It gets a fresh `fid`, but any author-visible id field is duplicated by construction.
- **Identity is optional today, by documented design.** So enforcing it is a change to the authoring
  instructions, not only to the tool — and it needs a migration, not a flag day.

### 2.4 The export carries no feature id

Every OGR-written GeoJSON in the vault that was produced by exporting a layer —
`drawings/basin-site/geo/roads.geojson`, `drawings/basin-site/qgis/layers/equipment.geojson` — has features
whose keys are exactly `["type", "properties", "geometry"]`: **no top-level `id` member and no `fid`
property.** An fid-based identity is therefore not merely fragile (§4.1), it is *not present in the input at
all* without changing the export step. `derive_placements` reads only `properties` and `geometry`
(`layout.py:406-433`) and would have nothing to read.

### 2.5 Corrections to the issues

- **#62 "8 of 10 Basin placements carry no tag"** — correct for the *register*. For completeness: the *editor
  layer* holds 11 features of which 9 carry no tag; the eleventh is excluded as parked.
- **#62 "export → register → layout → sheet"** — there is **no site sheet step in the toolkit yet**. The site
  GA sheet generator is #50, still open. The longest loop that exists today is
  `export → register → layout → emit (geojson|dxf)`, driven by `technical_drawings_for_agents layout`
  (`cli.py:93-124`). The idempotence harness in §5 therefore ends at *emit*, and §3.5 states the seam where
  the sheet step slots in when #50 lands. Claiming to test a sheet that does not exist would be inventing a
  capability.
- **#62 "a stable id … deterministically derived and then persisted"** — this spec **rejects** the derived
  branch and says why (§4.2). Derivation cannot give an id that is simultaneously stable under a move and
  non-renumbering under a delete, which is the pair of properties the acceptance criteria demand. The issue's
  acceptance criteria are all met by canonical ordering, which needs no id at all.
- **#52 "8 of 10 … cannot be matched at all"** — correct, and unfixable by tooling. P10 supplies the
  convention and the enforcement mechanism; populating the field is an authoring task (§6.4, §9).
- Note for scope hygiene: `components/basin-wtp/clarifier.placements.yaml` and
  `components/basin-wtp/generate_layout.py` are **not** layout registers. The former is the
  `component --place` format (no `type:` key, loaded by `cli.py:_load_placements`, `cli.py:285-308`); the
  latter is a superseded hand-rolled script with its own hard-coded parked-zone band. P10 touches neither.

---

## 3. Design

### 3.1 The identity scheme: author-supplied, never invented

**One rule.** A placement's identity is the first of these that exists, and nothing else:

| Precedence | Source | Written to the register as |
|---|---|---|
| 1 | an explicit id attribute in the editor export, named by `editor.id_field` (default `"id"`), or an `id:` key in a hand-written register / inline list | its own `id:` key |
| 2 | the `tag` (`editor.tag_field`, default `"tag"`) | nothing new — the `tag:` key already carries it |
| 3 | *(none)* — the placement has **no durable identity** | nothing; counted in the header (§3.3) |

`Placement.identity` returns `self.id or self.tag or None`. `Placement.has_durable_id` is
`self.identity is not None`. Precedence 2 exists because a tag *is* an equipment identity — reusing it takes
Basin from 0/10 to 2/10 covered for free, and it keeps one fact in one home (no derived `id:` key duplicating
a `tag:` key three lines above it, which is exactly the two-homes-for-one-fact defect #52 is about).

**Nothing derives an identity.** No sequence numbers, no geometry hashes, no fids, no nearest-neighbour
matching against the previous register. §4.1–§4.3 defend this against each alternative.

**Syntax and uniqueness.** Validated once, at both boundaries, because ids will end up as GeoJSON properties,
DXF text, CSV join keys and possibly filenames — constraining them at the door beats sanitising them in five
emitters:

- `ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")` — non-empty, starts alphanumeric, ≤ 64
  chars, safe unquoted in YAML/CSV/DXF/paths. A violation is a `LayoutError` quoting the offending value and
  the record's type + pose.
- Identities are unique **within one layout**, in a **flat namespace** (not per type) — a BOQ or reconcile
  join key must not need the type to disambiguate.
- Uniqueness is **case-insensitive**: `STA-A` and `sta-a` collide. macOS, Excel and QGIS all blur case (the
  vault's own `CLAUDE.md` calls this out for paths); a case-only difference is a mistake, not a design.
- Collisions are a **hard `LayoutError`** naming the duplicated identity and *both* records' type and pose,
  plus the fix. Enforced in `derive_placements` (export side) **and** in `load_layout` (register / inline-list
  side), because a hand-edit can collide too.
- An explicit `id` and a `tag` may both be present and differ — `id` wins as the identity, `tag` remains the
  equipment tag. But an explicit `id` that collides with *another* record's tag-derived identity is a
  collision like any other.

**What happens to an identity when…**

- **…a placement is moved or rotated in QGIS.** Geometry changes; attributes do not; the identity survives
  intact. The record's `origin_utm`/`rotation_deg` change, and the record may relocate within its type block
  under the canonical sort (§3.2). Diff cost: one changed record, plus at most one relocation hunk.
- **…a placement is deleted.** Its record disappears. **Nothing renumbers, because nothing is numbered** — no
  other record's bytes change. (This is the property a `<type>#NN` sequence scheme forfeits; see §4.2.)
- **…a new one is copy-pasted from it in QGIS.** The paste carries the source's attributes. If the source had
  an identity, the pair now collides → **hard error** on the next derive, naming both poses and instructing
  the author to clear or change the pasted feature's `id`/`tag`. This is deliberate: silently de-duplicating,
  auto-suffixing (`STA-A-2`), or preferring one of the two would let two physical units share one identity —
  the same class of latent divergence that hid the raft bearing error for a week. It costs a five-second
  attribute edit and only affects placements that already have identities (today: 2 of 11).
- **…the GeoPackage is rebuilt, repacked, or re-imported.** Fids are renumbered; identities, being attribute
  data, are not. This is the decisive argument against the fid (§4.1).

### 3.2 Canonical order — exact, total, tie-broken

One function, one implementation, over the **emitted instance mapping** (not over `Placement`), so that
`derive_placements`' output and `_placement_instance`'s output sort through identical code. Two
implementations of this key is the single largest divergence risk in this change.

```python
REGISTER_ROUND_DP = 3          # unchanged from today's round_to=3
REGISTER_KEY_ORDER = ("type", "origin_utm", "rotation_deg", "size_m", "tag", "id")
```

`canonical_sort_key(instance)` returns, in order:

1. `instance["type"]` — a plain string compare. Types first: the file reads as blocks of like equipment, which
   is how a reviewer reads it, and it makes an added item of an existing type land next to its siblings.
2. `round_reg(instance["origin_utm"][0])` — easting.
3. `round_reg(instance["origin_utm"][1])` — northing. (A west-to-east sweep, north/south within it.)
4. `round_reg(instance["rotation_deg"])` — as stored; **not** re-normalised (re-normalising would change
   stored values, i.e. output).
5. `(instance.get("size_m") is not None, round_reg(long), round_reg(short))` — absent size sorts **before**
   present (`False < True`); `long`/`short` are `0.0` when absent, never `None` (mixing `None` and `float` in
   a sort key raises `TypeError`).
6. `(identity is not None, (identity or "").casefold(), identity or "")` — absent identity sorts **before**
   present; case-folded first so the order does not depend on the platform's collation, then the raw string as
   the final discriminator.

`round_reg(v)` is `round(float(v), REGISTER_ROUND_DP)`, **plus one normalisation: `if r == 0.0: r = 0.0`** so
`-0.0` never reaches YAML (`-0.0` and `0.0` compare equal, so they cannot be ordered relative to each other,
but they serialise to *different bytes* — the exact class of bug this PR exists to kill). No other numeric
change: rounding stays `round()` as today, not `Decimal`, so no existing value moves. Verified: every Basin
value is a positive magnitude, so the negative-zero normalisation is a no-op on the live data.

**Totality.** Two records that tie on the *entire* key are two items of the same type, size, identity and
identical pose to the millimetre. That is physically impossible and overwhelmingly likely to be a stray
copy-paste left sitting on its source. It is a **hard `LayoutError`** — `duplicate placement: 2 × 'dosing-skid'
at the same pose (788662.616, 322262.035) @ 43.034° — a pasted copy was probably never moved` — not a silent
dedupe and not left to the optional `no-overlap` check to maybe catch. The sort is therefore total on every
input it accepts, so two conforming implementations produce byte-identical files.

**Where canonicalisation is applied: at the register-write boundary only.** `load_layout` continues to
preserve file order exactly (`layout.py:270-283`) — see §4.5 for why that matters to `snap_groups`. Since
every *generated* register file is canonical, load order equals canonical order for generated registers,
while an inline `placements:` list keeps meaning what the author wrote.

**Record key order** is fixed at `REGISTER_KEY_ORDER`, then any remaining properties in **sorted key order**.
`yaml.safe_dump(..., sort_keys=False)` (`layout.py:467-469`) is retained, so insertion order is the emitted
order. Verified no-change on the live data: `derive_placements` and `_placement_instance` already emit
`type, origin_utm, rotation_deg, size_m, tag`; `id` slots in after `tag` (absent everywhere today); the only
extra property in the wild is `snapped_by`, and a one-key mapping is identical sorted or not.

### 3.3 Register header: the identity gap becomes a tracked line

`dump_placements_register` gains **one header line**, immediately after the CRS/pose lines:

```
# ids: 2 of 10 placements carry a durable id (8 identified by pose only).
```

When coverage is complete, the line reads `# ids: 10 of 10 placements carry a durable id.` This is the
"persist over judgement" move: the gap stops being a fact in an agent's head or a reviewer's memory and
becomes a diffable line in a git-tracked artifact that moves when the gap closes. It is a YAML comment, so
`yaml.safe_load` and `load_layout` are unaffected.

The header's existing `# Derived from: <source>` line (`layout.py:461`) becomes **load-bearing provenance**:
`--canonicalise-register` (§3.7) parses it with `^# Derived from: (?P<source>.*)$` and preserves it verbatim,
so canonicalising in place never destroys the record of which export produced the file. A register with no
such line is a `LayoutError` telling the author to pass `--register-source <text>` or regenerate with
`--from-geojson` — a generated register with no provenance is not something to silently rubber-stamp. (Both
live Basin registers carry the line.)

### 3.4 Idempotence and round-trip guarantees

Let `derive` = `derive_placements`, `canon` = `canonical_instances`, `dump` =
`dump_placements_register`, `load` = the `instances` list obtained via `load_layout` +
`_placement_instance`. The change must establish, each as a named test in §5:

- **G1 — order independence.** For any permutation `π` of the export's `features`:
  `dump(canon(derive(π(export)))) == dump(canon(derive(export)))`, byte for byte.
- **G2 — serialisation fixed point.** `dump(canon(load(dump(canon(x))))) == dump(canon(x))`. One pass reaches
  the fixed point; there is no second-pass drift.
- **G3 — derive is a no-op on an unchanged export.** Re-running `--from-geojson` on an unchanged export
  produces identical bytes, and `_refresh_register` **skips the write** when the bytes are unchanged
  (replacing the unconditional write at `cli.py:231-233`), printing
  `register: unchanged (10 placement(s)) -> <path>`. Skipping the write keeps the mtime stable, which P2's
  (#54) staleness DAG will depend on. This is a *reported* no-op, not a silent one, so it does not violate
  constraint 0.4.
- **G4 — two consecutive full builds are byte-identical**, for: the placements register, the effective
  register (`--emit-register`), and the emitted layout **GeoJSON**.
- **G5 — canonicalisation preserves every value.** Round-tripping the live Basin register through
  `--canonicalise-register` changes record order and the header only: the multiset of records, compared as
  parsed YAML mappings, is identical.

**What G4 cannot assert yet, and the interface required from P3 (#55).** DXF and PDF are not byte-reproducible
today (full-repr floats, wall-clock dates, unsorted attributes — #55's problem statement). P10 must not fix
that; #55 owns it, parity-tested. So until #55 lands, the harness asserts, for DXF:

- a **normalised semantic digest** computed in the test module (not the library — it is a test tool that #55
  will make redundant): `sha256` over the sorted tuple of
  `(layer, dxftype, tuple(round(c, 6) for c in flattened_coords))` for every model-space entity, read with
  `ezdxf`. Two runs must produce the same digest.

**Interface required from P3 (#55), stated so #55 can build it:** a public
`technical_drawings_for_agents.emit_digest(path: Path) -> str` (or equivalently `manifest_for(path) -> Manifest` exposing a
`content_digest`) that returns a canonical content digest for a DXF/PDF/SVG output, excluding wall-clock
timestamps and any generator-version field, and honouring `SOURCE_DATE_EPOCH`. When it exists, the
idempotence test swaps its local `_dxf_digest` for `emit_digest` **and adds a raw byte-identity assertion**
for DXF and PDF. Until then the DXF assertion is semantic and the PDF is not asserted at all — stated in the
test's docstring rather than left as an unexplained gap. P10 changes no emit float formatting.

### 3.5 Where the sheet step attaches

#62's acceptance sketch names a `sheet` stage. It does not exist (§2.5). The harness is written as a
parametrised list of *stages* — `derive → load → snap → build → emit(geojson) → emit(dxf)` — each contributing
one or more artifacts to a "compare these files across two runs" list. Adding the site GA sheet when #50 lands
is one entry in that list plus one CLI flag; no restructuring. Say this in the module docstring so the next
implementer extends rather than rewrites.

### 3.6 Public Python API (exact)

In `src/technical_drawings_for_agents/components/layout.py`. `LayoutError` (`layout.py:40`) remains the **one** module error
class — no new exception types.

```python
REGISTER_ROUND_DP: int = 3
REGISTER_KEY_ORDER: tuple[str, ...] = ("type", "origin_utm", "rotation_deg", "size_m", "tag", "id")
ID_PATTERN: re.Pattern[str] = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

@dataclass(frozen=True)
class Placement:
    type: str
    origin: Point
    rotation_deg: float
    tag: str | None = None
    id: str | None = None                      # NEW — author-supplied durable id, never derived
    size_m: tuple[float, float] | None = None
    properties: dict[str, Any] = field(default_factory=dict)

    @property
    def label(self) -> str: ...                # UNCHANGED: self.tag or self.type
    @property
    def identity(self) -> str | None: ...      # NEW: self.id or self.tag or None
    @property
    def has_durable_id(self) -> bool: ...      # NEW: self.identity is not None

@dataclass(frozen=True)
class Editor:                                  # layout.py:71-77
    type_field: str = "type"
    tag_field: str = "tag"
    id_field: str = "id"                       # NEW
    parked: list[dict[str, Any]] = field(default_factory=list)

@dataclass(frozen=True)
class RegisterDrift:                           # NEW — what --check-register found
    register: Path
    on_disk: str
    expected: str
    @property
    def clean(self) -> bool: ...               # on_disk == expected
    def unified_diff(self) -> list[str]: ...   # difflib.unified_diff, register name as both labels

def canonical_sort_key(instance: Mapping[str, Any]) -> tuple[Any, ...]: ...
def canonical_instances(instances: Sequence[Mapping[str, Any]], *,
                        dp: int = REGISTER_ROUND_DP) -> list[dict[str, Any]]: ...
    # rounds (incl. -0.0 normalisation), reorders keys to REGISTER_KEY_ORDER + sorted extras,
    # validates id syntax, enforces case-insensitive identity uniqueness, enforces no duplicate
    # pose, sorts by canonical_sort_key. Raises LayoutError. Pure: input is not mutated.

def id_coverage(instances: Sequence[Mapping[str, Any]]) -> tuple[int, int]: ...   # (with_identity, total)

def derive_placements(geojson: dict[str, Any], *, type_field: str = "type", tag_field: str = "tag",
                      id_field: str = "id", exclude: Iterable[dict[str, Any]] = (),
                      round_to: int = 3) -> list[dict[str, Any]]: ...
    # NEW id_field kwarg. Emits an `id` key only when the export carries a non-blank id attribute.
    # Return order stays SOURCE ORDER (see §4.6) — this stays a pure reader; canonicalisation is
    # the writer's job. Identity syntax + uniqueness ARE enforced here.

def dump_placements_register(instances: Sequence[Mapping[str, Any]], *, source: str,
                             crs: str = "EPSG:32630", assert_canonical: bool = True) -> str: ...
    # NEW assert_canonical: raises LayoutError("register instances are not in canonical order; "
    # "call canonical_instances() first") when given unsorted input. NEW `# ids: n of m ...` header
    # line. Signature otherwise unchanged; existing keyword callers keep working.

def check_register(layout: Layout, *, instances: Sequence[Mapping[str, Any]] | None = None
                   ) -> RegisterDrift: ...
    # Never writes. `instances=None` -> re-canonicalise what load_layout read (catches non-canonical
    # order and hand-edits). `instances=<derived from an export>` -> also catches staleness vs the
    # editor. Raises LayoutError if layout.register is None (an inline placements list has no
    # register to check).

def canonicalise_register(path: Path, *, crs: str, source: str | None = None) -> bool: ...
    # Loads, canonicalises, re-emits; writes ONLY if the bytes differ; returns True iff it wrote.
    # `source=None` -> preserve the file's own `# Derived from:` line; missing -> LayoutError.
```

New check kind — `CHECK_KINDS` (`layout.py:36`) gains `"ids-present"`:

```python
def _check_ids_present(layout: Layout, params: dict[str, Any]) -> list[Finding]: ...
    # config: {check: ids-present, within: <type|list|omitted>, severity: error|warn}
    # One Finding per placement with no identity: "<type> at (E, N) @ R° has no durable id
    # (set `tag` or `id` on the editor feature)". Reuses _selected() (layout.py:675) and
    # _severity() (layout.py:668). Default severity: error, as with every other check.
```

Exports: add `canonical_instances`, `canonical_sort_key`, `canonicalise_register`, `check_register`,
`id_coverage`, `RegisterDrift`, `REGISTER_ROUND_DP` to `components/__init__.py`'s import block and `__all__`
(alphabetical, matching the existing list).

**Three traps the implementer must close:**

1. `_placement`'s `reserved` set (`layout.py:305`) **must gain `"id"`**, or an `id:` key falls through into
   `Placement.properties`, then into `layout_instance_properties` (`layout.py:640`), then onto every emitted
   GeoJSON feature — and `_placement_instance`'s `instance.update(properties)` (`cli.py:249`) re-emits it in
   the wrong key position, breaking G2.
2. `_placement_instance` (`cli.py:237-250`) must emit `id` after `tag`, must sort the remaining properties,
   and must **not** re-emit `id`/`tag` from `properties`.
3. `layout_instance_properties` (`layout.py:627-641`) emits `placed_id` — **only when
   `placement.id` is set** — alongside the existing `placed_tag`, for exactly the reason its docstring already
   gives for `placed_tag`: instance properties override feature properties downstream, so a bare `id` key
   would clobber every component feature's own. No live input carries an `id`, so no live output changes.

`Placement`'s new field is inserted after `tag`, before `size_m`. Verified safe: the only two in-tree
constructions (`layout.py:306`, `layout.py:523`) are keyword-only. The implementer must re-grep before
committing and keep it that way.

### 3.7 CLI surface and exit codes

`technical_drawings_for_agents layout` (`cli.py:93-124`) gains three flags. No other subcommand changes.

| Flag | Behaviour |
|---|---|
| `--check-register` | **Never writes.** Compares the register on disk with what the pipeline would write. Without `--from-geojson`: re-canonicalises what was loaded (catches non-canonical order, hand-edits, a missing `# ids:` line). With `--from-geojson`: derives from the export and compares **instead of writing** — the CI staleness gate. Prints a unified diff on drift. |
| `--canonicalise-register` | Rewrites the register in canonical order **in place**, from its own contents, with no export. The §7 migration tool. Writes only if bytes differ; prints `register: canonicalised` or `register: already canonical`. Mutually exclusive with `--from-geojson` and with `--check-register` (argparse `add_mutually_exclusive_group`) — deriving and canonicalising-in-place are different intents and combining them silently is how a stale export overwrites a good register. |
| `--register-source TEXT` | Provenance text to stamp into `# Derived from:` when canonicalising a register that has no such line. Without it, that case is an error. |
| `--require-ids` | Appends an `ids-present` check at `severity: error` to the loaded layout, so CI can enforce identity coverage without editing project YAML. Equivalent to declaring `{check: ids-present}` in `checks:`. |

**Exit codes** (unchanged semantics, extended):

| Code | Meaning |
|---|---|
| 0 | Success; or checks clean; or `--check-register` found no drift; or error findings with `--warn-only`. |
| 1 | An `error`-severity finding (existing, `cli.py:189-191`); **or `--check-register` found drift.** |
| 2 | `LayoutError` / `ComponentSpecError` — malformed config, register, or export; duplicate identity; duplicate pose; bad id syntax; usage error such as `--emit` without `--out` (existing, `cli.py:139`, `cli.py:170`). |

**`--warn-only` does not downgrade register drift.** A stale or non-canonical register is not a layout
*finding* about the design — it is the tool's output disagreeing with the tool's input, and CI must not be
able to wave it through. Document that in the flag's help text.

### 3.8 Config surface

```yaml
editor:
  type_field: type
  tag_field: tag
  id_field: id          # NEW, optional, default "id"

checks:
  - {check: ids-present, severity: warn}     # NEW kind; declared only, never implicit
```

Both additive and both opt-in. A config that declares neither behaves exactly as today — the same parity
argument that `test_no_groups_means_no_change_at_all` (`tests/test_layout.py:706-715`) makes for `groups:`.

---

## 4. Behaviour decisions, with rationale

### 4.1 Identity from a GeoPackage feature id — **rejected**

The fid survives a move (attributes and geometry are separate columns) and a paste gets a fresh fid, so it
looks attractive. Three reasons it fails:

- **It is not in the input.** Every OGR-written GeoJSON in this vault has no `id` member and no `fid` property
  (§2.4). You would have to change the export step *and* the human's instructions before the tool could see
  it — an unforced dependency on a step outside the tool's control.
- **A layer rebuild renumbers it.** The live layer already shows fid gaps at 3 and 9 (§2.2). An `ogr2ogr`
  copy, a "Save As", a repack, or a re-import from a fresh GeoPackage renumbers every feature — and the whole
  register churns on a change that moved nothing. That is exactly the failure being fixed, reintroduced by a
  different route.
- **It is an editor's internal counter.** Putting it in a git-tracked engineering register makes the register's
  primary key an implementation detail of a GUI's SQLite autoincrement. It carries no engineering meaning, so
  it cannot double as the #52 join key against `site-plan.yaml`, which is keyed on equipment tags.

### 4.2 Identity derived from geometry, or as a per-type sequence number — **rejected**

- **Geometry hash / rounded-pose key.** Breaks on the single most common authoring operation. The workflow doc
  is unambiguous: *move* and *rotate* are how placements are created and adjusted (§2.3). A pose-keyed id
  turns every nudge into a delete-plus-add: worst possible diff, and worse than the status quo because it
  would *also* destroy any downstream cross-reference (a BOQ line, a reconcile row, a `notes` annotation)
  every time someone dragged a slab 200 mm.
- **`<type>#NN` positional sequence.** Deterministic from the export alone and stable under a move — but it
  **renumbers on delete**, which #62's own acceptance criteria forbid ("deleting a placement does not renumber
  the others"). Deleting the first of three `dosing-skid`s rewrites the other two. Also, a sequence number is
  a fake identity: it *looks* durable in a diff and silently means something different next week, which is
  more dangerous than an honest blank.
- **Derived-once-then-persisted, matched forward by nearest neighbour.** The tempting middle ground, and #62's
  own suggested route. Rejected on three grounds. (i) It makes the register a function of `(export, previous
  register)`, so the output depends on hidden state and can no longer be regenerated from inputs alone —
  breaking the very property this change exists to establish. (ii) The forward match must be fuzzy (a radius
  in metres) because a move is the thing you are matching across; a tolerance that cannot be derived from any
  project fact would have to be invented, and the pipeline contract's first rule is determinism over
  inference. (iii) A large move, two same-type items swapping places, or a delete-and-re-add are all
  genuinely ambiguous, and every resolution is either a guess or an error the author must fix by hand — at
  which point they might as well have typed a tag. Complexity with a heuristic at its heart, buying an
  identity that is still not trustworthy.

### 4.3 Author-supplied and *required* — **rejected in that strict form; adopted as author-supplied and enforceable**

Required-from-day-one gives the best diffs and the cleanest #52 join key, and it is the honest scheme: the
only durable identity is one a human asserts. But making it a hard error on load would:

- **break the live Basin registers immediately** — eight of ten records have nothing to fill in — which
  violates constraint 0.1 outright, and there is no migration that does not involve inventing eight ids;
- **regress the authoring ergonomics the workflow doc deliberately chose**: copy-paste-move-rotate with the
  tag documented as *optional* (§2.3). Turning every paste into a mandatory attribute edit is a real cost paid
  by the one person doing the placing.

So: **author-supplied, first-class, validated, counted — and enforceable per project when that project is
ready.** The `ids-present` check is declared in the config (or forced with `--require-ids`), so Basin can run
at `severity: warn` today, close the gap in QGIS, and flip to `error` in the same commit that finishes the
job. New sites can declare `error` on day one. This is the "new behaviour is opt-in and parity-tested" rule
applied to a data-quality gate.

### 4.4 An id-less placement is a **warning**, not an error and not silent

Three graded surfaces, chosen so the gap cannot be *forgotten* even where it cannot yet be *fixed*:

1. The `# ids: 2 of 10 …` header line, in the artifact, in git, moving when the gap closes.
2. The `ids-present` check — declared, severity-configurable, one `Finding` per id-less placement naming its
   type and pose.
3. `--require-ids` for CI.

Rejected: (a) **hard error** — breaks the live data, no honest migration (§4.3); (b) **silence** — the current
behaviour, and the reason #52 spent a week not noticing a 40° divergence; (c) **auto-assignment** — §4.2.
Backward-compat consequence, stated plainly: the existing untagged Basin placements keep working unchanged;
they are counted in the header, and they will produce eight `ids-present` findings **only if** that check is
declared, which `basin.layout.yaml` will not do until the tags are populated (§9 Q3).

### 4.5 The sort key: `type` first, then pose, identity last — and why the sort is not id-primary

- **Not id-primary.** Identity is sparse (2 of 10). An id-primary sort over a mixed file needs a rule for the
  id-less majority anyway, and — worse — *adding one id would reshuffle the whole file*. Under the chosen key
  the order is a function of the physical layout, so populating, correcting or removing an identity **does not
  move a single record**. That is a strictly better diff property, and it is why identity is the *last*
  tiebreak rather than the first.
- **Not pure-pose (no `type` component).** Sorting by easting alone interleaves clarifiers, pumps and skids by
  accident of geography. Grouping by type is how the file is read, it keeps like items adjacent so an added
  item lands beside its siblings, and it costs nothing in stability (a placement's type does not change under
  a move; if someone *does* retype a feature, that is a real change and a relocation hunk is the correct
  diff).
- **Pose before identity, and pose in E-then-N order.** Any total order would do for correctness; this one is
  legible (a west-to-east sweep) and it is stated exactly here so two implementations agree. The cost — a move
  can relocate a record within its type block — is at most one extra diff hunk on top of the value change the
  move already caused. Accepted (§4.2's alternatives cost far more).
- **Comparisons are on the rounded, as-emitted values.** Sorting on unrounded floats and *then* rounding can
  transpose two records that round to equal values, producing two different byte streams from one input. Round
  first, always.

### 4.6 `derive_placements` keeps returning source order; the **writer** canonicalises

`derive_placements` is a library reader with a documented pure-function contract and a public test surface
(`tests/test_layout.py:123-136`). Flipping its default output order is a silent behaviour change for any
caller, which the pipeline contract's backward-compat rule forbids. Meanwhile the *file* is the artifact whose
determinism is the point. So: derivation stays order-preserving, and `canonical_instances()` is called
explicitly by the two writers (`_refresh_register`, `cli.py:194-234`; and the `--emit-register` branch,
`cli.py:155-166`).

`dump_placements_register` then **asserts** its input is canonical rather than quietly sorting it
(`assert_canonical=True`). Silent reordering inside a function named `dump` is how the two ends of the
round-trip drift; a precise `LayoutError` naming the fix is house style (validate at the boundary, fail with a
message that says what to do). This is the one intentional API tightening in the change: an out-of-tree caller
passing unsorted instances now gets an error instead of a noisy register. No such caller exists in the vault
(`components/basin-wtp/generate_layout.py` hand-rolls its own emit and never calls this function).

### 4.7 `snap_groups` and `order: north-to-south` must not fight the canonical sort

`_order_members` (`layout.py:589-601`) handles `north-to-south`/`south-to-north` by **re-sorting members by
their projection onto the group's cross axis**, so those two modes are already **invariant to input order** —
a canonical sort cannot change their result. Good.

`order: as-listed` (`layout.py:592-593`) is the collision: it is *defined* as input order, and input order is
`load_layout`'s file order, which for a generated register is now the canonical sort. The group's emitted
**geometry** would then depend on a sort order the author never chose — a silent geometry change, the worst
category of defect in this codebase.

Resolution, in two parts:

1. **Canonicalise at the write boundary only** (§3.2). `load_layout` still preserves file order, so
   `as-listed` keeps its plain meaning: "the order you can see in the file".
2. **`order: as-listed` combined with a generated register is a hard `LayoutError`** at load:
   `groups[i].snap.order 'as-listed' cannot be used with a GENERATED placements register (<path>): member
   order would follow the canonical sort, not your intent — use north-to-south / south-to-north, or an inline
   placements list`. Detected by the register file's `# GENERATED` header marker plus `Layout.register` being
   set. Verified zero live impact: `basin.layout.yaml` uses `order: north-to-south`, and
   `examples/site_layout.yaml` likewise; `as-listed` appears in no project config, only in
   `tests/test_layout.py`'s inline-placement cases, which are unaffected. Loud failure over a silent geometry
   change — and it costs nobody anything today.

Also note: because the *effective* register is canonicalised on **post-snap** poses, its record order can
differ from the placements register's. That is correct (they describe different poses) but it means the two
files cannot be read side by side line-for-line. Consumers must join on identity — one more concrete reason to
populate the field, and the `# ids:` line on both files says how far off that is.

### 4.8 What the idempotence test can assert today

Split by what is reproducible now versus what #55 owns (§3.4): **byte-identity** for the register, the
effective register and the emitted GeoJSON (all three are ours, all three are plain text, all three are
deterministic once the sort is fixed); **semantic digest** for DXF; **nothing** for PDF until #55 lands, with
the gap named in the test docstring and the exact required interface written down (§3.4). Asserting PDF
byte-identity today would give a red test that says nothing about P10 and would tempt an implementer into
#55's territory unparity-tested.

### 4.9 A no-change re-run skips the write

`_refresh_register` writes unconditionally today (`cli.py:231-233`). Under P10 it computes the bytes, compares
with the file, and skips an identical write — because P2's (#54) staleness DAG will key on mtimes, and a
rewrite-in-place makes every downstream artifact spuriously stale. This is a *reported* no-op
(`register: unchanged (N placement(s))`), which is the distinction constraint 0.4 draws: silence is the
defect, not idleness.

---

## 5. Acceptance tests

Mechanically checkable. Names are final — they are the contract. Unit-level id/order rules go in
`tests/test_layout_ids.py`; the loop tests go in `tests/test_idempotence.py`; the two backward-compat tests go
in the existing `tests/test_layout.py` beside their neighbours.

**Shared fixture** (`tests/test_idempotence.py`): a synthetic site in `tmp_path` built from
`src/technical_drawings_for_agents/components/examples/packaged_unit.yaml` (as `_write_layout` already does,
`tests/test_layout.py:60-83`), with **ten placements deliberately mirroring the live Basin shape: two carrying
a `tag`, eight carrying nothing**, across at least three types, including three of one type distinguishable
only by pose. Built by a helper, not a committed data file, so the fixture cannot drift from the assertions.
Permutations are **explicit constants** (`reversed(features)` and one hard-coded index permutation) — no RNG
in a determinism test.

1. **`test_shuffling_the_export_feature_order_leaves_the_register_byte_identical`** — *Setup:* the shared
   10-feature export. *Action:* run `main(["layout", cfg, "--from-geojson", export])` three times, once with
   the features as authored, once reversed, once under the fixed permutation, each into a fresh `tmp_path`.
   *Expected:* all three register files are **equal as strings**, including the header, and exit code 0.

2. **`test_two_consecutive_full_builds_are_byte_identical`** — *Setup:* the shared site. *Action:* run
   `layout … --from-geojson … --emit geojson --out o.geojson --emit-register eff.yaml` twice, copying the
   artifacts aside after run 1. *Expected:* `placements.yaml`, `eff.yaml` and `o.geojson` are byte-identical
   across runs; the DXF's `_dxf_digest` is equal across runs; exit code 0 both times.

3. **`test_adding_one_placement_is_a_single_record_diff`** — *Setup:* build once from the 10-feature export.
   *Action:* append one feature of an existing type to the export (at the **front** of the `features` list, to
   prove source position is irrelevant) and rebuild. *Expected:* `difflib.unified_diff` of the two register
   files contains **exactly one added hunk** and **zero removed lines other than the `# ids:` counter line**;
   every pre-existing record's text appears unchanged; the counter line reads `2 of 11`.

4. **`test_deleting_a_placement_does_not_renumber_or_rewrite_the_others`** — *Setup:* build from the
   10-feature export. *Action:* remove one of the three same-type-distinguished-by-pose features and rebuild.
   *Expected:* the diff removes exactly that record's lines plus the `# ids:` counter line; the remaining nine
   records are textually identical, byte for byte, in the same order.

5. **`test_rederiving_from_an_unchanged_export_is_a_reported_no_op`** — *Setup:* build once; record the
   register's bytes and `st_mtime_ns`. *Action:* rebuild from the same export. *Expected:* bytes identical,
   **`st_mtime_ns` unchanged** (the write was skipped), stdout contains `register: unchanged`, exit code 0.

6. **`test_a_duplicate_id_is_a_hard_error`** — *Setup:* an export where two features carry `tag: STA-A`
   (the copy-paste case of §3.1). *Action:* `derive_placements(...)`. *Expected:* `pytest.raises(LayoutError,
   match="duplicate")`, the message naming `STA-A` and **both** poses; via the CLI, **exit code 2** and no
   register written. Parametrised to also cover: an explicit `id` colliding with another record's `tag`; and
   `STA-A` vs `sta-a` (case-insensitive collision).

7. **`test_the_real_basin_register_shape_still_loads_and_places_unchanged`** — *(backward compat, the
   important one.)* *Setup:* a register **literally reproducing the live Basin shape** — ten records, two
   tagged (`STA-A`, `STA-B`), eight untagged, three `dosing-skid`s at distinct poses, values copied verbatim
   from `basin.placements.yaml`. *Action:* `load_layout` → `snap_groups` → `build_layout` → `check_layout`.
   *Expected:* loads without error; ten placements; `Placement.identity` is `"STA-A"`/`"STA-B"` for the two
   clarifiers and `None` for the other eight; `has_durable_id` False for those eight; **no findings unless
   `ids-present` is declared**; and the placed feature coordinates are identical (`pytest.approx`, `abs=1e-9`)
   to those produced by the pre-P10 code path — captured as an explicit expected-coordinates constant in the
   test, not recomputed by the code under test.

8. **`test_canonicalising_the_basin_register_changes_order_only`** — *Setup:* the §7 register text as a
   fixture. *Action:* `canonicalise_register(path, crs="EPSG:32630")`. *Expected:* returns `True`; the parsed
   `instances` multiset before and after is **equal** (compare as sorted lists of mappings); the
   `# Derived from: placements_export.geojson` line is preserved verbatim; a `# ids: 2 of 10` line is present;
   a second call returns `False` and leaves the bytes untouched.

9. **`test_check_register_detects_a_hand_edit_and_a_stale_export_and_never_writes`** — *Setup:* a built,
   canonical register. *Action (a):* reorder two records by hand, run `layout … --check-register`.
   *Action (b):* move one feature in the export, run `layout … --from-geojson … --check-register`.
   *Expected:* both exit **1**, both print a unified diff naming the register, **both leave the register's
   bytes and mtime untouched**; `--warn-only` does **not** change either exit code; a clean register exits 0.

10. **`test_ids_present_check_reports_every_id_less_placement_and_is_opt_in`** — *Setup:* the shared
    2-of-10 site. *Action:* `check_layout` with and without `{check: ids-present}` declared, plus
    `main([... "--require-ids"])`. *Expected:* without the check, zero findings from it; with it,
    **exactly 8** findings, each `check == "ids-present"`, `severity == "error"`, each message naming the
    type and pose; `--require-ids` exits **1**; `severity: warn` exits 0.

11. **`test_register_key_order_and_number_formatting_are_fixed`** — *Setup:* instances with an `id`, a `tag`,
    a `size_m`, two extra properties inserted in reverse-alphabetical order, and a value that rounds to
    `-0.0`. *Action:* `dump_placements_register(canonical_instances(...), source="x")`. *Expected:* the
    per-record key sequence is exactly `type, origin_utm, rotation_deg, size_m, tag, id`, then the extras
    alphabetically; the text contains no `-0.0`; `yaml.safe_load` round-trips to the same mappings.

12. **`test_dump_placements_register_rejects_non_canonical_input`** — *Action:* pass a deliberately unsorted
    list. *Expected:* `pytest.raises(LayoutError, match="canonical")`.

13. **`test_duplicate_pose_is_an_error_not_a_silent_dedupe`** — *Setup:* two features of one type at an
    identical pose and size (the never-moved paste). *Action:* `derive_placements`. *Expected:*
    `LayoutError` matching `same pose`, naming the type and the coordinates.

14. **`test_as_listed_group_order_is_refused_against_a_generated_register`** — *Setup:* a layout whose
    `placements:` is a register file carrying the `# GENERATED` header, with a group declaring
    `order: as-listed`. *Action:* `load_layout`. *Expected:* `pytest.raises(LayoutError, match="as-listed")`.
    Companion: the same group against an **inline** placements list loads fine and snaps in list order —
    proving the existing behaviour is preserved where it is meaningful.

15. **`test_an_id_never_leaks_into_emitted_feature_properties`** — *Setup:* one placement with
    `id: RAFT-01`, `tag: STA-A`. *Action:* `build_layout` → `layout_instance_properties` → `to_geojson`.
    *Expected:* the component feature's own `tag` survives (`"N1"` for the nozzle, as
    `tests/test_layout.py:500-518` already asserts), `placed_tag == "STA-A"`, `placed_id == "RAFT-01"`, and
    there is **no bare `id` key** in the feature properties.

16. **`test_shipped_worked_example_register_is_canonical`** — *Action:* `check_register` on
    `examples/site_layout.yaml`. *Expected:* `clean is True`. The shipped example is the executable spec for
    the config schema (`tests/test_layout.py:747-755`); it must not be the one non-canonical register in the
    repo. Requires regenerating `examples/site_placements.yaml` in this change: both records are
    `packaged-unit`, and easting `36.144` (`EX-B`) sorts before `40.0` (`EX-A`), so the file's record order
    **does** change. Consequence the implementer must handle: `test_shipped_worked_example_builds_and_checks_clean`
    asserts `[p.tag for p in layout.placements] == ["EX-A", "EX-B"]` (`tests/test_layout.py:753`) and must be
    updated to `["EX-B", "EX-A"]`, with a one-line comment saying the order is now the canonical sort. That is
    a fixture change inside this PR, not a project artifact — and it is the only existing assertion in the
    771-line suite that the canonical sort invalidates (checked: every other order-sensitive assertion uses
    inline placements, whose load order is unchanged, or is order-insensitive).

---

## 6. Migration

Three commits, in this order, so each diff is reviewable on its own.

**6.1 — Tooling.** `layout.py`, `components/cli.py`, `components/__init__.py`, the new tests, the new flags in
`README.md`'s CLI block, and the regenerated `examples/site_placements.yaml`. Behaviour for every existing
input is unchanged except the three deliberate new hard errors (duplicate identity, duplicate pose,
`as-listed` + generated register) — none of which any live input triggers.

**6.2 — Canonicalise the Basin registers (vault repo, separate change, order-only).**

```bash
technical_drawings_for_agents layout "03-Resources/DEMO Water Project/components/basin-wtp/basin.layout.yaml" \
    --canonicalise-register
technical_drawings_for_agents layout "…/basin.layout.yaml" --emit geojson --out "…/basin_layout.geojson" \
    --emit-register "…/basin.effective.yaml"
```

One commit containing the reordered `basin.placements.yaml`, the regenerated `basin.effective.yaml`, and the
regenerated `basin_layout.geojson`, with a message stating that **no value changed — record order only**. This
is the one noisy diff; it happens once, and after it every subsequent diff is signal. Before committing,
verify the parity claim mechanically (test 8's assertion, run against the real files): the parsed instance
multiset is identical before and after, and the emitted GeoJSON's feature *set* is unchanged with per-feature
coordinates identical — only the `features` array order moves (§8.2).

**6.3 — Reconcile the register with the editor.** `technical_drawings_for_agents layout … --from-geojson <fresh export>
--check-register` against a fresh export of the `placements` layer, to find out whether the register is
actually current. It may not be: the layer holds 11 features and the register 10, and fid 10 sits inside the
parked bbox (§2.2, §9 Q4). Whatever that produces is a **finding to report, not a fix to apply** — if the
control-station is genuinely placed rather than parked, the parked bbox or the placement is wrong, and that is
the DEMO Water Lead's call.

**6.4 — Not in this change:** populating the eight missing tags/ids in QGIS, and flipping `basin.layout.yaml`
to `{check: ids-present, severity: error}`. That is an authoring task with an engineering convention behind it
(§9 Q3), and it belongs in a follow-up issue, not a tooling PR.

---

## 7. Out of scope — do not do these

An implementer who touches any of the following has exceeded this specification. Open a follow-up issue
instead.

1. **The `site-plan.yaml` restructure (#52's engineering half).** Do **not** remove `pos_utm` or
   `rotation_deg` from `drawings/basin-site/site-plan.yaml`, and do not "reconcile" the two by editing the
   engineering document. #52 is explicit that this is *"an edit to a project engineering document, so it wants
   the Lead's sign-off, not a tooling PR"* — an **engineering call for the DEMO Water Lead**, to be recorded
   as a `type: decision` note in the F9 ledger when made. P10 supplies the join key convention; it does not
   spend it.
2. **`layout --reconcile <site-plan.yaml>` (#52's tooling half).** A separate change, to be built *after*
   this one and *on top of* `Placement.identity`. Not here.
3. **The `platform.envelope_m` axis-alignment question** (an axis-aligned 10×15 m box recorded against rafts
   at ~40°, #52). A geometry/engineering question, not an identity one.
4. **Deterministic DXF/PDF bytes, manifests, provenance stamps, `SOURCE_DATE_EPOCH`** — all #55 (P3). Do not
   change float formatting, attribute ordering or date handling in any emitter. State the interface (§3.4) and
   consume it later.
5. **Reordering the emitted layout GeoJSON `features` array to a canonical order of its own.** Its order
   follows the register (§2.1) and that is sufficient; canonical *output* emit is #55's remit.
6. **Writing ids back into the GeoPackage.** Stamping an `id` column into the editable layer is genuinely the
   durable long-term answer — the tool assigns once, copy-paste carries it forward, duplicates become the hard
   error of §3.1 — but it means the pipeline **writing to the human's editor artifact**, needs GDAL/OGR in the
   loop, and needs the Lead's agreement about a column in their working layer. **Open this as a follow-up
   issue** with that reasoning; do not sneak it in.
7. **A build DAG, staleness checks, or a `drawing-set.yaml`** — #54 (P2). The harness drives the CLI directly
   and twice; that is enough to prove idempotence.
8. **A site GA sheet step** — #50, not built (§2.5, §3.5).
9. **The `component --place` placements format** (`cli.py:285-308`, e.g.
   `components/basin-wtp/clarifier.placements.yaml`, which has no `type:` key). Different format, different
   loader, untouched.
10. **`components/basin-wtp/generate_layout.py`.** A superseded hand-rolled script with its own hard-coded
    parked-zone band. Deleting or rewriting it is a vault-side cleanup, not this PR.
11. **Anything touching drawing status.** No reads or writes of `meta.yaml` status, watermarks, or
    `for_construction`. The ISSUED gate is the responsible engineer's signature (constraint 0.3).

---

## 8. Risks and how the migration handles them

**8.1 One large, meaningless-looking register diff (6.2).** Every record in `basin.placements.yaml` moves.
*Mitigation:* a dedicated commit that changes nothing else, a message saying "order only", and the mechanical
parity check of test 8 run against the real file before committing. Reviewed once; never again.

**8.2 The emitted layout GeoJSON's `features` array reorders.** `build_layout` and `_emit_layout` walk
`layout.placements` in register order, so a reordered register reorders the output array. *This is the one
place the "output must not change" constraint is genuinely touched, and it is stated rather than hidden:* **no
coordinate, rotation, size or property value changes — only the position of features within the `features`
array.** RFC 7946 assigns no meaning to the order of a `FeatureCollection`'s members, and both consumers here
(QGIS layer load into `basin-site.gpkg`; `gis-tool` staging) are order-insensitive. *Mitigation:* the parity assertion
is set-equality of features plus per-feature coordinate identity keyed on
`(placed_type, placed_tag, role, component)`; and the regenerated GeoJSON ships in the same commit as the
reordered register so one act explains both diffs.

**8.3 Out-of-tree callers of `dump_placements_register` now hit a `LayoutError`.** *Mitigation:* the only
non-test caller in either repo is `components/cli.py`; `generate_layout.py` hand-rolls its own emit. Called
out in the PR body as the one intentional API tightening. It is the loud-failure choice by design (§4.6).

**8.4 Three new hard errors could bite an input nobody has looked at.** Duplicate identity, duplicate pose,
and `as-listed` + generated register. *Mitigation:* all three verified against the live data — Basin has no
duplicate tags, no coincident poses, and uses `order: north-to-south`; the shipped example likewise. Each has
a message that names the offending records and the fix, so the failure is actionable in seconds. If a fourth
site later trips the duplicate-pose rule legitimately (genuinely stacked equipment, e.g. a slab and a plinth
recorded as two placements at one centre), that is a real design question to raise, not a rule to relax by
default — and it is already answerable by giving the two records distinct ids, since identity is part of the
sort key.

**8.5 A future consumer keys on the tag-derived identity, then someone corrects a tag.** The identity changes,
and cross-references break. *Mitigation:* documented in §3.1 — a tag correction *is* a re-identification, and
it shows as a delete-plus-add, which is the honest diff. Projects wanting an identity that outlives tag
corrections should populate an explicit `id`, which is exactly the field precedence 1 provides.

**8.6 `Placement` gains a field.** Positional construction with five or more arguments would break.
*Mitigation:* both in-tree constructions (`layout.py:306`, `layout.py:523`) are keyword-only; re-grep before
committing.

**8.7 The idempotence test could pass for the wrong reason** — e.g. a build that silently produces nothing
twice. *Mitigation:* every idempotence test also asserts non-emptiness (ten records, a non-zero feature count,
exit code 0) before asserting equality. A test that proves two empty files match is worse than no test.

---

## 9. Open questions — to be answered, not guessed

1. **Does the export the Lead actually produces carry a feature id?** Every OGR-written GeoJSON in the vault
   does not (§2.4). P10 does not depend on the answer, but it should be confirmed once and written into
   `placements — how to use.md` so nobody builds on the assumption later. *Owner: senior-engineer,
   one command.*
2. **Is a tag such as `STA-A` unique across all three sites (Basin / Site B / vendor), or only within a
   site?** This spec enforces uniqueness **within one layout** only. If per-site registers are ever merged, or
   a cross-site BOQ joins on identity, site-uniqueness becomes load-bearing and the convention needs a site
   prefix. *Owner: DEMO Water Lead. Not decidable by the tool.*
3. **What convention should fill the eight missing identities, and who fills them?** Equipment tag numbers
   from `site-plan.yaml`, or a placement-local scheme? #52 asks for this to be *"decided once, in the tool,
   for all three sites"* — P10 decides the **mechanism** (field, precedence, syntax, uniqueness); the
   **naming convention** is an engineering call. *Owner: DEMO Water Lead, then a follow-up issue to populate
   the layer and flip `ids-present` to `error`.*
4. **Is `control-station-admin-stores-accommodation` (fid 10, centroid `788639.000, 322380.000`) genuinely
   parked, or is the palette bbox `[788312, 322377, 788912, 322395]` swallowing a real placement?** It is
   3 m north of the bbox's southern edge and it is the only feature in the "palette row", which the workflow
   doc says should hold *one of every type*. Either the palette has been consumed and this item is a leftover,
   or a placed building is being silently dropped from every drawing. Surfaced by §6.3; **a data question for
   the DEMO Water Lead, not something the tool should resolve.**
5. **Will the site GA sheet (#50) need identity as a hard requirement** — e.g. to label items or to key a
   drawing schedule? If yes, `ids-present` moves from opt-in warning to a prerequisite for that sheet, and
   §6.4 becomes a blocker rather than a follow-up. *Owner: whoever specs #50.*
6. **Does anything downstream already key on the register's record order?** Nothing found in either repo
   (`build_layout`, `_emit_layout`, `check_layout` and `snap_groups` are all order-insensitive except
   `order: as-listed`, handled in §4.7). If the Lead has a spreadsheet or a BOQ built by row position against
   `basin.placements.yaml`, the 6.2 reorder would break it — worth one question before committing.
