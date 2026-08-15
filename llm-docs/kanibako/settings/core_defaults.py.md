# Core Defaults — the shipped launch-floor tables, and the box-create canon skeleton

_thin reader of `kanibako.data/core-defaults.yaml`, plus the `~/canon` skeleton J-7 specifies_

⚠️ **RELOCATION PASS, 2026-08-11.** Every explanatory paragraph that used to live in
`src/kanibako/settings/core_defaults.py` is here; the source keeps one-line descriptors and `⚑`
markers only. Absence of a symbol below means "nothing was displaced from it", never "does not
exist". **Eleven claims were found FALSE against the code and were DROPPED rather than moved** —
each is recorded under [Dropped as false](#dropped-as-false) so nobody re-derives them from git
history.

## What the module is

The STATIC, non-agent-specific launch-floor tables live as declarative data in
:mod:`kanibako.data` (`core-defaults.yaml`), mirroring how the image baseline ships
(:mod:`kanibako.runtime.baseline`) and how containerfiles/templates ship via
:mod:`importlib.resources`. This module reads that file and emits its entries through the existing
category seam, so nothing enters a box except through the keyspace.

The shipped file declares EIGHT CATEGORY-TABLE families (seven BIND families plus `masks`, which is a mask table, not a bind) and this module has a producer for each:

| block | producer | arm(s) |
|---|---|---|
| `masks` | `vault_mask_default` | `<scope>.masks` (empty today) |
| `channels` | `channel_default_categories` | `box.bindings.rw` |
| `core` | `core_default_categories` | `box.bindings.{ro,rw}` |
| `kani` | `kani_default_categories` | `box.bindings.ro` |
| `kickoff` | `kickoff_default_categories` | `box.bindings.ro` |
| `canon` | `canon_default_categories` | `box.bindings.ro` + `agent.<node>.canon` scalars |
| `helpers` | `helper_default_categories` | `box.bindings.{ro,rw}` |
| `images` | `image_default_categories` | `box.bindings.ro` + `box.images_store` scalar |

Since the DEFAULTS-1 pass the file also carries TWO NON-BIND SCALAR sections. They are flat maps
keyed by setting name, not dest-keyed category tables, and they are why the sentence above says
*bind* families:

| block | producer | emits |
|---|---|---|
| `agent_default` | `behavior_defaults` / `behavior_default` | the `agent.default.<key>` BEHAVIOR floor (spec §2d) — `access` · `allow_helpers` · `continue_mode` · `bootstrap` |
| `env` | `env_default_categories` | STATIC `<scope>.env.<VAR>` floor keys; ships exactly one — `box.env.COLORTERM` |

Two reading traps in that pair:

* `behavior_default` (singular) is the FAIL-CLOSED single-key read and it is the ONE spelling of
  that read — `commands.start._declared_behavior` and
  :func:`kanibako.settings.settings_keyspace.access_default` both come here. `access` in particular
  has NO constant any more: the retired `ACCESS_DEFAULT` was a second spelling of `full`.
* `env_default_categories` is NOT `commands.start._core_env_default_categories`. That one emits the
  launch-DERIVED `KANIBAKO_*` stamps and its docstring forbids new entries; this one emits literals
  a file can hold. The derived table merges AFTER this one, so a stamp wins a contested VAR.

⚑ **`env_default_categories` FAILS CLOSED on the key it builds** (`_check_env_key`, MBR-2/D1-4): the
emitted `<scope>.env.<VAR>` is matched against `settings_categories.ENV_KEY_RE`, so a typo'd scope
head or a VAR that is not an env-name RAISES instead of entering `default_categories` as a key
nothing downstream recognises. The regex is IMPORTED rather than re-spelled — it is the keyspace's
own declaration of the family — through a function-local import, the `add_bind` pattern that keeps
this module's MODULE scope free of the settings stack.

⚑ **`box.env.COLORTERM` is the section's first and only entry, and its SCOPE is load-bearing.** A
variable declared here at one scope and written by a user at ANOTHER refuses the launch
(`store_collapse.collapse_env`), so moving it to `system:` would break exactly the users who had
already stored their own. It replaced a first-run WRITE in `cli.py`; the write seam is now guarded
by `tests/test_settings/test_defaults_enforcement.py`.

Two things in the module are NOT table producers and should not be read as such:

* `packaged_data_dir` — the packaging primitive, also imported by
  :mod:`kanibako.launch.templates`.
* the **canon SKELETON** (`canon_skeleton_rels` → `materialize_canon_skeleton` →
  `_protect_canon_skeleton` → `_warn_unprotected`) — a filesystem MUTATOR that runs at box create,
  after a box-home copy, and as a per-launch re-assert. It shells out to `podman unshare` and
  chowns a real tree. It lives here because it is the exact mirror image of the canon BINDS above
  and is derived from the same constants.

## STATIC / DYNAMIC / CONDITIONAL

The split is documented in the YAML header too, and it is the whole reason the file can be
declarative at all:

* **STATIC** — box-side destinations, per-entry mount options, and the structural shape (which
  keys exist, their category, their per-mode scope). Read straight from the file.
* **DYNAMIC** — host SOURCES that are runtime-PROBED (the channel host roots come from
  :class:`~kanibako.settings.paths.StandardPaths` /
  :func:`kanibako.channels.channels.box_channel_addresses`; the core mounts from `ProjectPaths`;
  the helper socket/log from the caller). The loader injects each probed source into its keyed
  entry at the seam; the file names the source SYMBOLICALLY so the structure stays declarative.
* **CONDITIONAL** — per-mode and per-state gates, applied at the INJECTION SITE, never in the
  file: the workset-local channel binds are emitted only for PRIMARY/NAMED boxes (standalone has
  no workset channel paths), the vault binds only when the vault is enabled, the helper binds only
  when their source `.exists()`, and the kickoff bind only when no plugin is delivering one.

## Where these tables land in the cascade

Every table this module returns is folded by
:func:`kanibako.settings.settings_launch.build_launch_snapshot` into **ONE base-level floor** — the
LEAST-specific cascade level. That is what makes a `box:` / `workset:` / `system:` file value
override a shipped default by name, and it is why `image_default_categories` can hand the probed
podman graph root over as `box.images_store`'s *default* rather than as its value.

⚑ Do not confuse this with the AGENT-scope tables. Those come from the PLUGINS
(:mod:`kanibako.settings.agent_defaults`) and arrive already discriminated as
`agent.<node>.<category>`; the tables here are `box.*`-scoped and carry no agent segment.

## The floor table shape

`BindArmTable = dict[str, dict[str, tuple[str, ...]]]` — a dest-keyed floor bind table,
`{"box.bindings.ro": {box_dest: (src[, opts])}}`.

The OUTER key is the TERMINAL arm key (R-5) and the INNER key is the normalized box DESTINATION
(R-11); the value is the RAW tuple the floor parser turns into a
:class:`~kanibako.settings.kb_store.BindEntry`. **Floor tables are raw** — they are parsed by
`settings_assemble.dotted_partial`, never here.

Per spec §2a a binding is STRUCTURED, never a colon-joined string, so no escaping of a literal `:`
in a host path is ever needed.

### RETIRED — the set-time floor registry

`FLOOR_PLACEHOLDER_SRC` and `core_default_bind_keys` USED TO LIVE HERE. They built a context-light
SET-TIME floor registry (F10): the core box-mount bind keys with static box_dest+options and a
placeholder host_src, folded into `config_interface._category_set_lookups` so a source-only repoint
of a launch-only bind would pass the must-exist-in-the-cascade gate. R-9 retired both bind CLI
write routes and the registry could no longer change any outcome, so the whole thread was deleted
in one subtractive pass.

⚑ The LAUNCH floor producers in this module are a DIFFERENT, LIVE mechanism: they are host-probed,
feed `build_launch_snapshot`, and never used the sentinel. Do not reintroduce a set-time registry
when you find a gate that wants one.

## Packaging

```packaged_data_dir(*parts: str) -> Traversable```
Resolve a path inside the packaged `kanibako.data` tree.

Single source of truth for `importlib.resources.files("kanibako.data")` joined with `*parts` —
returns the same `Traversable` the inline `files("kanibako.data").joinpath(*parts)` expression
produced (callers wrap it in `Path(str(...))` as before). Notably it centralizes the rom-root
subpath literal :data:`ROM_ROOT_PARTS` (`("global", "rom")`) that is resolved in both this module
and :mod:`kanibako.launch.templates`.

```CORE_DEFAULTS_FILENAME = "core-defaults.yaml"```
Filename of the shipped system/core defaults, inside `kanibako.data`.

```_load_doc() -> dict[str, Any]```
Read and parse the bundled system/core defaults file.

## The masks default

```vault_mask_default() -> list[str]```
Return the default masked DESTINATIONS — now EMPTY (no default mask).

Per spec §2a `masks` is a map keyed by box destination (`dict[box_dest -> bool|None]`, the
3-state), so the shipped file spells a MAP and this reader hands back its keys — the destinations,
in file order.

The old vestigial `~/workspace/vault` default was DROPPED: the vault moved OUT of `~/workspace` in
1.6.0, so there is nothing in the workspace to hide behind a tmpfs. The seam is kept (so a box may
still declare masks via `box.masks` / `<scope>.masks`) but the shipped default is empty
(decision B).

## The ONE bind constructor

```add_bind(binds, category, box_dest, host_src, options=None, *, scope="box") -> None```
Install ONE dest-keyed entry into the `<scope>.<category>` arm of *binds*.

The single constructor every floor producer — in this module and in the plugin loader
(:mod:`kanibako.settings.agent_defaults`) — goes through, so the arm key, the R-11 destination
normalization and the act-once refusal are written ONCE rather than at every call site (disk-store
rework R-3/R-6/R-11).

* *category* is any TERMINAL bind-shaped category — the ARMED `bindings.ro` / `bindings.rw`, or
  (since 2026-08-08c) `caches` / `seeded` / `common` / `synced`. `{scope}.{category}` is the WHOLE
  key and the destination is NOT part of it. ⚑ It works for all six because it only ever JOINS the
  two, never parses them: a category with a dot in it is an arm and one without is not, and nothing
  here needs to know which.
  ⚑ `seeded` / `synced` are COPIES and stay copies — this constructor writes an entry DOWN; what is
  DONE with it is the delivery table's answer (`settings_categories._DELIVERY`), not this
  function's.
* *box_dest* becomes the map KEY, normalized by
  :func:`~kanibako.settings.settings_resolve.normalize_bind_dest` — ⚑ the DEST only. *host_src* is
  stored exactly as given: a source is resolved on its own later (spec §2a), and canonicalizing it
  would bake this machine's home into a value other machines read.
* *options* omitted → a 1-element entry, meaning "use the category default".

**Two entries at ONE destination inside ONE category map RAISES.** For a `bindings` arm the reason
is act-once: it cannot be a legitimate overlay. For the other four the reason is the DEST-KEYED
SHAPE itself — the second entry would simply replace the first in the dict, with nothing downstream
able to see the loss, which is the unrepresentable-collision case R-8 says to refuse LOUDLY rather
than absorb. (Two entries at one dest in DIFFERENT arms or different categories are still two
different keys and still reach the resolved-`box_dest` collision table in `settings_categories` —
that check is untouched, design §2b-CAVEAT.)

## The channel binds

```channel_default_categories(std: StandardPaths, proj: ProjectPaths) -> BindArmTable```
Build the per-mode channel bind table as `default_categories` (§2c/§2f).

Fills the TERMINAL `box.bindings.rw` arm with ONE `box_dest -> (host_src,)` entry per channel
surfaced into THIS box. The box-side destinations and the structure come from the declarative file;
the host SOURCES are runtime-probed here and injected into each entry (the file names them
symbolically).

Under dest-keying (R-3/R-5) the DESTINATION is the map key and the value is the 1-or-2 element
`(host_src[, options])` tuple that :func:`~kanibako.settings.settings_resolve.unpack_bind_entry`
consumes; these channel entries omit options, taking the category default.

**ALL MODES (system scope):** the four system channel type roots (common / chat / share /
mailboxes) plus this box's own inbox double-bind — the SAME host source visible at both
`~/channels/inbox` (its own bind) and `~/channels/mailboxes/<ws>/<self>` (via the mailboxes root
bind) — A2.
**PRIMARY + NAMED** additionally get the three workset-local type roots under
`~/channels/workset/`; **STANDALONE OMITS** them (A10 — gated by the absence of workset channel
paths, which is what makes the `source` lookup miss and drop the entry).

⚑ B2: an entry with a `meta_ref` is ROUTED through that `@meta.*` reference (spec §2c) — the
host_src is the `@`-ref STRING, which `expand` resolves to the SAME materialized identity literal
as the runtime-probed source (byte-identical, JC-B2-4). The `source` gate still applies, so a
workset-scoped meta_ref entry on a standalone box is still omitted.

## The core box mounts

```core_default_categories(std, proj, *, enable_vault: bool, mode: str, guarantee_create: bool = True) -> BindArmTable```
Build the core box mounts (workspace + vault) as `default_categories` (step 3).

🛑 **NO HOME ROW SINCE CUTOVER 6-H.** The box home does NOT route through `bindings.rw` (spec
`:1015`): it is pid 0, the FOUNDATION the whole set folds over, built at the assembly seam
(`commands/start.py._install_assembly_collapse`) from the RO DERIVED key `meta.box.home`. Its mount
options are seam machinery and live with the seam. A `key: home` row here would be a SECOND bind at
the foundation's point, which the collapse refuses by name — do not re-add one.

Fills the TERMINAL `box.bindings.ro` / `box.bindings.rw` arms with one
`box_dest -> (host_src, options)` entry per CORE box mount. These are the box's own
workspace/vault binds — TODAY's hardwired podman `-v` routed through the category resolver so
nothing is bound into a box except through the keyspace. The box-side destinations, per-entry mount
options, and armed category come from the declarative file (`core:` list); the host SOURCES are
runtime-probed from *proj* here and injected into each entry.

Under dest-keying (R-3/R-5) the DESTINATION is the map key — R-11-absolutized by `add_bind`, so the
file's `~` is stored as `/home/agent/...` — and the per-entry mount OPTIONS are the value's OPTIONAL
2nd slot, consumed by :func:`~kanibako.settings.settings_resolve.unpack_bind_entry`. **Present
options OVERRIDE the category default for that entry**, so the `ro` vault bind keeps `ro` and the
`Z,U` binds keep `Z,U` regardless of the category's own default.

workspace is UNCONDITIONAL (every box mode). The vault binds (`scope: vault` in the file)
are UNIVERSAL UNLESS DISABLED: emitted whenever *enable_vault* is true, with the probed source dir
CREATED IF MISSING here so the bind is ALWAYS emitted rather than silently dropped when the source
happens to be absent. Only an explicitly DISABLED vault omits them.

⚑ *guarantee_create* False suppresses ONLY that mkdir — the bind is still emitted with the same
host_src, so a read-only consumer sees exactly what a launch would mount without making it so. It
exists because `box show --effective` resolves this same table (`commands/box/_parser.py`), and a
DISPLAY verb must not write to disk.

⚑ **The @-ref routing has TWO shapes and the per-mode one is now the vault's alone.** An entry
routed through an `@`-ref carries either a single `meta_ref` (MODE-INDEPENDENT — workspace) OR a
`mode_meta_ref` PER-MODE map. The per-mode form serves the VAULT binds ONLY, for a
real reason: primary/named take the per-box `/@meta.box.name` subdir that a lone standalone box does
not have (spec §2c). Both vault arms root at the SAME `@workset.vault_*` anchor. home needs
no arm at all and no row either — `meta.box.home` roots at `@meta.box.path`, which is where the
per-mode variation lives. The
host_src is the `@`-ref STRING, which `expand` resolves to the SAME runtime-probed literal
(byte-identical) because the `workset.*` / `meta.workset.path` anchors resolve to the launch's own
roots. An un-routed entry falls back to the probed source.

## The kanibako CLI binds

```kani_default_categories() -> BindArmTable```
Build the kanibako CLI binds as `default_categories` (Phase B).

Fills the TERMINAL `box.bindings.ro` arm with one `box_dest -> (host_src, options)` entry for each
of the THREE unconditional core binds — the in-box kanibako package, the entry script, and the
universal `profile.d` secret-export snippet. TODAY's hardwired `_kanibako_mounts` `-v` list routed
through the category resolver so nothing is bound into a box except through the keyspace. The
box-side destinations + options come from the declarative file (`kani:` list); the host SOURCES are
import-resolved here and injected into each entry (the file names them SYMBOLICALLY). All three are
UNCONDITIONAL (every box mode) — the YAML calls them the unconditional trio, which is why the
kickoff bind, the one core bind with a gate, is its own family.

## The KICKOFF loader — the directive-chain entry slot

Spec §2c, P-5.

```KICKOFF_PACKAGED_PARTS = ("global", "KICKOFF.md")```
The packaged kickoff loader, relative to the `kanibako.data` root.

It sits FLAT under `global/` beside the two other shipped content trees (`global/rom`, the RO canon;
`global/template`, the writable seed) because it is NEITHER of them: it is not canon TEXT (it never
lands under `~/canon` and the box never reads it as a directive — the flattener CONSUMES it) and it
is not seeded (it is bound RO so it version-follows the package instead of freezing at create).

```_kickoff_entry() -> dict[str, Any]```
The one declarative `kickoff:` entry, or RAISE if the shipped file lost it.

The block is a LIST like every other family in the file (`kani` / `helpers` / `images`) and carries
EXACTLY ONE entry: there is one directive-chain entry slot, and two would mean two files racing for
one dest. Enforced rather than assumed — a second entry would otherwise be silently ignored here
while the `KANIBAKO_DIRECTIVE_SEED` env var kept naming the first.

```kickoff_box_dest() -> str```
The `~`-spelled box-side kickoff slot (`~/.config/kanibako/kickoff.md`).

SINGLE SOURCE OF TRUTH, read from the declarative file: the bind's dest, the
`KANIBAKO_DIRECTIVE_SEED` container env var and the transition gate's dest comparison are all the
SAME path, and a second literal spelling of it anywhere is a drift waiting to happen.

```kickoff_guest_dest() -> str```
:func:`kickoff_box_dest` as an ABSOLUTE guest path.

Two consumers need the expanded form rather than the `~` spelling: the `KANIBAKO_DIRECTIVE_SEED` env
var (it is read by a hook shell and at exec time, where `~` would resolve against whoever's HOME) and
the transition gate (descriptor box_dests are `$GUEST_HOME`-expanded by the defaults loader, so a
`~`-spelled dest would never match one).

```kickoff_default_categories(descriptor: "PluginDescriptor | None" = None) -> BindArmTable```
Build the core KICKOFF bind as `default_categories` (spec §2c, P-5).

One entry in the terminal `box.bindings.ro` arm, keyed by destination:

```
/home/agent/.config/kanibako/kickoff.md = (<packaged global/KICKOFF.md>, ro)
```

The directive-chain ENTRY SLOT: the flattener reads this file at box start, follows its
`@~/canon/COLLECTION.md` import to full depth, and writes the flat result into the harness's native
instruction slot. INTERNAL, like `kani_pkg` and `images_conf`: `config set` refuses it (R-9 retired
the `bindings.{ro,rw}` write route at every scope); not repointable (spec §0's test — a user has
nothing to configure here, the file is generated content at a fixed location, and repointing it
would redirect the entire directive chain).

### ⚑⚑ THE TRANSITION GATE — core YIELDS to a plugin that still ships a kickoff

P-5 moved the kickoff CONTENT from the three agent plugins into core. The plugins publish
INDEPENDENTLY of the base and pin no base version, so a NEW base beside an OLD plugin is not just
reachable, it is the ordinary `pip install -U kanibako-cli` outcome — and both would deliver a file
to the SAME dest: core's bind here plus the plugin's descriptor binding (`managed_pointer` in all
three first-party plugins). Two CONCRETE bindings at one box_dest is a row-1 collision in spec §0's
identical-dest table, i.e. a HARD LAUNCH ERROR (`CategoryCollisionError`). Refusing to launch a box
because its base got newer is not an acceptable upgrade experience, so core defers: with a
plugin-supplied kickoff present, this emitter yields NOTHING and the plugin's file is delivered
exactly as before.

⚑ **REMOVAL CONDITION** — delete the gate (and this section) once every published agent plugin has
dropped its own kickoff delivery: `data/KICKOFF.md` + the `managed_pointer` descriptor binding gone
from `kanibako-agent-claude`, `-codex` and `-goose`, with those releases PUBLISHED (not merely
merged). After that the gate can only ever be false, and an ungated unconditional bind is the honest
shape. Until then the gate is what keeps the base and the plugins independently upgradable. Recorded
in migration M-12. *(Verified still needed 2026-08-11: all three plugins ship `data/KICKOFF.md` and
declare `managed_pointer`.)*

*descriptor* is the descriptor whose bindings the SAME resolve represents in the launch snapshot
(see :func:`kanibako.settings.agent_representation.agent_default_partial`). Passing `None` (a
no-agent box, or a narrow resolve that carries no agent bindings) means nothing else can be
delivering a kickoff, so the bind is emitted.

**FAIL-CLOSED: the packaged loader must exist.** A box whose kickoff is missing has NO directive
chain at all — the flattener finds no source, the launch shim's `|| true` swallows it, and every
session runs with an empty instruction set. That is precisely the silent degradation the canon work
exists to end, so a missing packaged file RAISES here rather than emitting a bind whose source
`_emit_category_mounts` would drop with a one-line warning.

## The packaged ROM canon — constants

```ROM_ROOT_PARTS = ("global", "rom")```
The packaged rom root — the READ-ONLY built-in CANON content (the BIBLE, plus the `COLLECTION.md`
index that enters it).

A module constant, symmetric with :func:`templates._packaged_base_template`'s hardcoded
`("global","template")` writable-seed root: rom is the RO-bind DUAL of that writable template seed.

```CANON_GUEST_ROOT = "canon"```
The guest canon root, `~/canon`.

⚑ The packaged rom tree is FLAT — `rom/{COLLECTION.md, bible/**}`, with NO `canon/` wrapper level
(J-7, 2026-07-31). It therefore NO LONGER mirrors the guest layout, so a rom-relative path is NOT
its own `~/`-dest: every guest dest is built by :func:`_canon_dest`, which prefixes the guest canon
root.

```ROM_COLLECTION_REL · ROM_BIBLE_REL · ROM_CONTENTS_REL```
The rom-ROOT-relative posix paths of the packaged CANON bind SOURCES (spec §2c).

```HANDBOOK_REL = "handbook"```
The handbook BOOK root, guest-only (nothing packages a handbook).

Declared beside `ROM_BIBLE_REL` for symmetry and because the managed-region deny list needs both
book roots.

```ROM_GUIDE_REL = "bible/general/directives/ROM_GENERAL.md"```
The load-bearing box guide (the bible's GENERAL chapter), rom-root-relative.

It MUST ship whenever the rom root is populated (fail-closed guard) — a box launched without the
guide is a silent degradation of EVERY box.

```ROM_BIBLE_CHAPTERS = ("general", "workset", "box")```
The bible chapters core PACKAGES, one whole-directory sibling bind each.

⚑ There is deliberately no `agent` here: J-7 retired the packaged placeholder chapter along with the
nested-bind model that needed it (a wheel cannot ship an empty directory, and a mountpoint must never
live inside a bind SOURCE).

```BIBLE_AGENT_CHAPTER = "agent"```
The bible's PLUGIN chapter. Guest-only: it is a mountpoint the box-create skeleton materialises in
the box home, never a packaged directory.

```PLUGIN_CHAPTER_MARKER_REL = "directives/ROM_AGENT.md"```
The plugin-rom EMISSION GATE marker, relative to a plugin's `data/rom` chapter root: a plugin gets a
bible chapter bind ONLY if it actually ships one.

```CANON_SEED_DENY_PREFIXES```
The MANAGED CANON REGION that no template seed may write into, as `~`-relative prefixes.

Spec §2c: *"a template MUST NOT seed into `canon/COLLECTION.md`, `canon/bible/…` or
`canon/handbook/…`; seeds target `canon/{notebook,workbook}` ONLY"*.

⚑ **WHY PREFIXES AND NOT THE LITERAL BIND DESTS.** Under J-7's SIBLING binds `canon/bible` is no
longer itself a bind dest — only its chapters are — so passing the literal dests would silently stop
rejecting a seed at `canon/bible/agent/x.md`. That seed is still forbidden, and under J-7 doubly so:
the whole region is root-owned, so the copy would fail with EACCES at create rather than merely be
shadowed at launch. The deny list therefore names the managed REGION, which is what §2c actually
states.

⚑ **`canon/handbook` is in the list on the SKELETON's authority, not the bind's.** The trigger is not
"is it bound?" but "does the box-create SKELETON own it?" — and it does:
`materialize_canon_skeleton` creates `canon/handbook/` + its four chapter mountpoints +
`SYS_CONTENTS.md` and makes them root-owned. A template seeding there would fail with EACCES
regardless of what is bound. *(It is also bound today, by
[`canon_default_categories`](#the-handbook-binds--the-scopecanon-floor).)*

## The packaged ROM canon — functions

```_canon_dest(rel: str) -> str```
Return the `~`-relative guest dest for a canon path *rel*.

*rel* is spelled relative to the BOOK ROOT (`COLLECTION.md`, `bible/general`, …), which is the
rom-root-relative spelling for packaged sources and the home-relative-under-`canon` spelling for
everything else.

```assert_canon_bind_seed_disjoint(bind_dests: Iterable[str], seed_rels: Iterable[str]) -> None```
RAISE if any template SEED lands at or under a MANAGED `~/canon` path.

Both arguments are `~`-RELATIVE posix paths (`canon/bible`, `canon/notebook/MY_CONTENTS.md`, …):
*bind_dests* are the managed canon prefixes (:data:`CANON_SEED_DENY_PREFIXES` — the BOOK ROOTS,
which under J-7's sibling binds are a superset of the literal bind dests; see that constant for
why), *seed_rels* the files a seed layer would copy to the box home.

⚑ **SCOPE OF WHAT IS ACTUALLY CHECKED TODAY.** The only caller (:func:`rom_default_categories`)
passes the PACKAGED BOX-HOME template walk (`template/box/home`) — i.e. layer 1 of the three in
:func:`kanibako.launch.templates.template_seed_defaults`. The AGENT and WORKSET layers
(`@agent.<a>.template` / `@workset.template`, both user-repointable and both resolved at seed time,
not here) are NOT covered, and neither are a plugin's `default_seeds()`. This function does not
decide that scope, it only enforces what it is handed — **WIDENING THE INPUTS IS THE CALLER'S JOB**,
which is exactly why the bind dests and seed rels are parameters rather than computed inside.

⚑⚑ **BOTH SIDES MUST BE HOME-RELATIVE, and that is easy to break silently.** The `~`-relative bind
prefixes (`canon/bible`, `canon/handbook`) can only be compared against seed rels spelled the same
way. Before the canon restructure the packaged template ROOT happened to BE the home-relative root,
so passing its walk worked by coincidence; it no longer is (`box/home/...`), and passing the root
walk today would make every comparison a guaranteed miss — a guard that runs, passes, and checks
nothing. Hence :func:`kanibako.launch.templates.packaged_box_home_template`, which names the level
that IS home-relative.

**PREFIX CONTAINMENT, not set intersection.** The managed region is root-owned from create, so a
seed does not have to hit an exact path to fail: anything under it — `canon/bible/general/x.md` no
less than `canon/bible` itself — fails with EACCES at create. Second, and weaker: where a copy could
land at all, spec §0's copy-vs-mount rule makes the mount's shadowing of it ORDER-INDEPENDENT and
SILENT. Hence a guard rather than a runtime resolution.

```rom_default_categories() -> BindArmTable```
Build the FIVE read-only packaged-CANON binds as `default_categories`.

All five are ENTRIES of the ONE terminal `box.bindings.ro` arm, keyed by DESTINATION
(R-3/R-5/R-11 — the `canon_*` names in the code are gone from the data and survive only as local
labels):

```
/home/agent/canon/COLLECTION.md          = (<rom>/COLLECTION.md,         ro)
/home/agent/canon/bible/ROM_CONTENTS.md  = (<rom>/bible/ROM_CONTENTS.md, ro)
/home/agent/canon/bible/general          = (<rom>/bible/general,         ro)
/home/agent/canon/bible/workset          = (<rom>/bible/workset,         ro)
/home/agent/canon/bible/box              = (<rom>/bible/box,             ro)
```

The sixth canon bind, the plugin's `~/canon/bible/agent` chapter, is emitted separately — see
:func:`rom_agent_default_categories`.

All INTERNAL/generated binds, not user keys: `config set` refuses them exactly as it does `kani_pkg`
and `images_conf` — as of R-9 it refuses EVERY `bindings.{ro,rw}` spelling at every scope, so the
rule no longer turns on any per-entry registry membership. Spec §0's test — *"could a user
reasonably want to override it?"* — answers itself here: the one book a user cannot edit is also the
one they cannot repoint, and `COLLECTION.md` is the INDEX that defines the canon's shape and load
order, so a repointable index would mean no guaranteed structure.

⚑⚑ **SIBLINGS, NOT A WHOLE-DIR BOOK** (J-7, 2026-07-31 — REPLACES R1's single `canon_bible`
directory bind, which shipped only in the unreleased `93b9a9d`). Every entry is its own bind onto a
mountpoint that ALREADY EXISTS in the box home, materialised by :func:`materialize_canon_skeleton` at
box create. Nothing nests inside anything, so no mountpoint ever has to live inside a bind SOURCE —
which is what killed the whole-dir model: the plugin chapter's mountpoint would have had to exist
inside site-packages (where a wheel cannot ship an empty directory and no runtime may write), and
the handbook chapters' inside the user's own stores. The nested-mount physics PASSED on real podman;
the model was retired anyway because of where it forced the mountpoints to live.

`COLLECTION.md` and `ROM_CONTENTS.md` are FILE binds (file-onto-file, over the 0-byte mountpoints the
skeleton creates). They stay BINDS rather than seeded copies because they are rom TRUTH: a copy would
freeze at create and drift from the installed package.

**FAIL-CLOSED guards** (a mis-pathed or half-shipped canon must RAISE, never silently launch a box
with no directives):

* **(a)** the guide is physically on disk but absent from the shipped-file walk → the
  over-broad-filter / empty-glob / broken-walk class. Anchoring the guard to the guide's on-disk
  presence (NOT to a non-empty filtered list) is what catches it: the walk silently returning
  nothing while the guide still ships must RAISE, never short-circuit to a guide-less launch
  (MEMORY: *"check the file COUNT, never just rc"*).
* **(b)** the rom root is POPULATED but any EMITTED BIND'S SOURCE is missing — plus the guide, which
  has no bind of its own (it rides the `general` chapter's). Now that every source is its own bind,
  "the required members" and "the emitted sources" are the same list, so the guard is GENERATED from
  the bind list rather than restated. A half-shipped canon is a PACKAGING defect, and a bind with a
  missing source would otherwise be silently DROPPED by `_emit_category_mounts` with only a
  per-launch warning.

⚑ `bible/agent/` is deliberately NOT required (and must NOT ship): J-7 retired the packaged
placeholder chapter together with the nesting that needed it.

An absent or genuinely EMPTY rom root yields an empty dict — a no-rom install, which is fine. That
branch is reached only when the guide is NOT on disk; guard (a) has already raised if it was.

⚑ **ONE declaration drives BOTH the completeness guard and the emission**, so the guard cannot drift
away from what is actually bound.

**DISJOINTNESS:** delegated to :func:`assert_canon_bind_seed_disjoint` (prefix containment against
the template seed tree). ⚑ **RE-ANCHORED to the BOX-HOME template root** (`template/box/home`), NOT
the template ROOT. Both sides must be HOME-RELATIVE for the prefix comparison to mean anything, and
after the canon restructure a root-relative walk yields `box/home/...` / `workset/...` /
`handbook/...` — none of which can ever match a `canon/...` prefix. Left un-re-anchored the guard
would still run, still pass, and check NOTHING.

```rom_agent_default_categories(target: "Target") -> BindArmTable```
Build the PLUGIN's bible chapter bind — the SIXTH canon bind (spec §2c).

One entry in the terminal `box.bindings.ro` arm, keyed by destination:

```
/home/agent/canon/bible/agent = (<plugin pkg>/data/rom, ro)
```

Emitted by CORE from the RESOLVED *target*, beside the five core canon binds — NOT by the plugin,
and NOT through the agent-scope descriptor route. That choice is the whole design: an
`agent.<node>.bindings.ro` entry would have ridden the per-node descriptor floor into the set-time
cascade and made the bible's agent chapter the SOLE repointable page of an otherwise unrepointable
book, and it would discriminate on the NODE (a persona) while the content is a property of the
HARNESS PACKAGE. (R-9 has since retired the bind CLI write route at every scope, so no page of the
book is repointable from the CLI — the asymmetry the choice avoided cannot arise at all now.) As a
box-scoped INTERNAL bind there is no discriminator at all, which is spec §2d's *"storage is varied,
binding is not"*.

⚑ `bible/agent` = per-HARNESS (packaged, one per plugin). `handbook/agent` = per-AGENT-NODE (host,
`agent.<agent>.canon`, personas included). A persona has no package, so it has no bible chapter;
what it can have is a handbook chapter. Two books, two cardinalities, no overlap.

⚑ **A SIBLING, not a nested bind** (J-7, 2026-07-31 — REPLACES R1's shadow model). Its dest no
longer sits inside another bind's: `~/canon/bible` is not bound at all, only its chapters are, and
this one lands on a mountpoint the box-create skeleton already made. Nothing shadows anything, so
the ascending mount depth-sort is not load-bearing here any more.

**GATE** — emit ONLY when the plugin actually ships a chapter (`rom_root` exists AND contains
`directives/ROM_AGENT.md`). With no packaged placeholder chapter left to shadow, an ungated bare
`data/rom/` would bind an EMPTY directory over the mountpoint: visibly identical to emitting
nothing, but paid for with a per-launch missing-source WARNING from `_emit_category_mounts` — the
wrong signal for the perfectly ordinary "this plugin has no chapter". Gate-false is the honest
shape: an empty root-owned mountpoint plus ONE dangling-import warning from the flattener, which is
exactly what J-7 accepts.

## The HANDBOOK binds + the `<scope>.canon` floor

Spec §2c/§2b/§2d/§2g.

```CANON_ACTIVE_AGENT_TOKEN = "<active>"```
The ACTIVE-AGENT placeholder in the declarative `canon:` rows.

The bind source for the agent chapter discriminates on the ACTIVE AGENT NODE, which no static file
can spell; the loader substitutes it. A literal that could never occur in a real key (`<`/`>` are
not key characters), so the substitution cannot collide.

```canon_optional_bind_keys() -> frozenset[str]```
The SKIP-IF-ABSENT canon bind keys, read from the same declarative rows.

Fed to :func:`kanibako.settings.settings_launch.snapshot_category_entries` as `optional_keys` at the
ONE launch aggregation site (spec §2c "SKIP-IF-ABSENT"). Derived from the file rather than restated:
a row that gains or loses `optional: true` moves both the declaration and this set at once.
🛑 The SILENT DROP itself is no longer this set's doing — the emitter reads
:func:`canon_optional_bind_dests`, the DEST-spelled view of the same rows.

⚑ **H6 — RE-DERIVED FROM THE DESTINATION** (R-10/R-11). The emitted `CategoryEntry.key` is now
`box.<category>.<box_dest>`, because a dest-keyed arm's map key IS the destination; matching on
`entry['key']` here would silently never hit, and a missing workset chapter would go back to warning
on every launch of almost every box. Normalized with the SAME function the producer uses, so the two
spellings cannot drift.

```_canon_optional_rows() -> list[Any]```
The `canon:` rows carrying `optional: true` — ONE filter, two views.

```canon_optional_bind_dests() -> frozenset[str]```
The SKIP-IF-ABSENT canon binds as normalized box DESTS — the EMITTER's view.

⚑ The DEST basis, not the key basis: it is handed to `commands.start._emit_category_mounts` as
`skip_if_absent`, and that decision is made against a destination (see
`llm-docs/kanibako/commands/start.py.md`). Normalized with the SAME function that keys the arm
(:func:`add_bind`), so the two spellings cannot drift — the failure `critical_keys` already paid for
once, where a key-spelled set matched NOTHING and silently degraded every entry to the default
policy.

```canon_default_categories(std: StandardPaths, agent_name: str | None) -> dict[str, object]```
Build the HANDBOOK binds + the agent-scope `canon` floor (spec §2c).

Returns a MIXED table — the terminal `box.bindings.ro` arm holding the five `~/canon/handbook/*`
entries PLUS the agent-scope SCALAR keys their `@`-refs resolve against. Mixing the two is the
established shape (:func:`kanibako.launch.templates.template_seed_defaults` does the same for the
seed layers and their source keys): both land in the SAME snapshot floor, the scalar resolves the
ref, and a user override of the scalar wins by cascade precedence and reroutes the bind.

The other three `<scope>.canon` keys are floored elsewhere, each beside the anchor it is spelled
against: `system.canon` in the resolved `system.*` tier (`StandardPaths.canon`), `workset.canon` and
`box.canon` in :func:`kanibako.settings.settings_launch.workset_anchor_floor`.

⚑ **THE REPOINT ROUTES DIFFER PER SCOPE**, and the difference is inherited, not chosen here:
`workset.canon` / `box.canon` are ordinary `config set` keys (wired like `workset.template`),
`agent.<a>.canon` is settable at the SYSTEM scope only (the per-persona agent-leaf rule
`agent.<a>.template` already follows), and `system.canon` is CLI-REFUSED as a structural path key —
it is a `SYSTEM_PATH_DEFAULTS` member, so it lives in the hand-edited `[system]` table of
`kanibako_config.yaml`, exactly like `system.template`.

⚑⚑ **THE AGENT TIER** — J-1 option (a) (*"the `agent.default` tier must be able to win"*),
implemented against a resolver that has NO agent-tier fallback:

* `agent.default.canon` is ALWAYS floored at `@config.agents/default/canon`.
* the ACTIVE NODE's `agent.<node>.canon` is floored too, but its FLOOR VALUE is chosen by whether
  that node's own store actually PROVIDES a canon dir — `@config.agents/<node>/canon` when it does,
  and the REF `@agent.default.canon` when it does not. So a plugin agent whose install stamped its
  own chapter binds that chapter, and a PERSONA (no package, no stamped store) falls back to the
  DEFAULT store — which is precisely the beneficiary case J-1 names.
* being a FLOOR entry, any `agent.<node>.canon` a plugin or the user sets in the cascade OVERRIDES
  it.

A literal reading of option (a) — flooring ONLY `agent.default.canon` — is not implementable here:
`settings_expand._lookup_raw` walks the merged snapshot with no per-tier fallback, and
`@agent.<node>.canon/handbook` is an EMBEDDED ref, so an undeclared node key coerces to `""`
(spec §6b) and yields the degenerate host path `/handbook` rather than falling back to the default
tier.

A NO-AGENT box (*agent_name* None) emits NEITHER the agent scalar NOR the `canon_hb_agent` entry —
deliberately not an entry with an empty ref, which would produce exactly that degenerate path.

## The box-create CANON SKELETON (J-7)

```HANDBOOK_CHAPTERS = ("general", "agent", "workset", "box")```
```HANDBOOK_CONTENTS_REL```
The handbook's chapters and its table of contents.

Their MOUNTPOINTS are part of this one closed skeleton: J-7 specifies the skeleton as a single set,
an absent chapter is REQUIRED to show as an empty root-owned dir, and creating them later would mean
mkdir-ing into an already-555 tree. (Their BINDS are
[`canon_default_categories`](#the-handbook-binds--the-scopecanon-floor)'s.)

```HANDBOOK_FALLBACK_ENTRIES```
⚑⚑ THE IMPORT-FALLBACK FILES (seeds-gate F1, 2026-08-01) — the per-scope chapters whose ENTRY FILE
the skeleton pre-creates 0-byte, keyed chapter → entry filename.

**WHY THEY EXIST, and why skip-if-absent did NOT already cover it:** the packaged `SYS_CONTENTS.md`
imports all FOUR chapters UNCONDITIONALLY, and skip-if-absent governs the BIND, not the INDEX. So on
a box with no workset chapter — i.e. every primary box — the flattener printed `unresolved import
@workset/directives/SYS_WORKSET.md` on EVERY launch. That is the warning-noise failure the
skip-if-absent work exists to prevent, arriving through the other door.

With a 0-byte entry file already inside the mountpoint: an UNBOUND chapter RESOLVES-TO-EMPTY (no
warning, no content), and a BOUND one has its whole directory replaced by the mount, so the store's
real file SHADOWS the fallback. No branch, no gate, no second mechanism.

⚑ `general` is deliberately ABSENT: the system store always supplies it, so a fallback there would
mask a genuinely missing system handbook — which is exactly what `canon_hb_general` being
NON-optional exists to surface.

⚑ **MACHINERY, NOT CONTENT.** Jei's D2 cut stands: the global handbook STORE still ships `general`
only. These files live in the BOX's skeleton, are root-owned like the rest of it, and are never
installed anywhere.

```HANDBOOK_DIRECTIVES_DIRNAME = "directives"```
The directory each chapter's entry file sits in — the `@<chapter>/directives/...` spelling
`SYS_CONTENTS.md` imports.

```UNSHARE_BOX_ROOT_UID = 1 · UNSHARE_BOX_ROOT_GID = 1```
⚑⚑ THE OWNER THAT APPEARS AS ROOT INSIDE A BOX — deliberately NOT 0.

J-7's prose says `podman unshare chown 0:0`, but 0 does not do what it reads like. `podman unshare`
enters the rootless INTERMEDIATE user namespace, whose mapping is `ns-uid 0 -> the real host user`
and `ns-uid 1.. -> the host user's subuid range`. kanibako runs every box with
`--userns=keep-id:uid=1000,gid=1000` (:data:`kanibako.runtime.container.KEEP_ID_USERNS`), under which
the real host user appears IN-BOX as uid 1000 — the agent. So `chown 0:0` inside unshare produces an
AGENT-OWNED skeleton, the exact opposite of J-7's stated effect (*"in-box: root-owned, unwritable"*).
Container uid 0 under keep-id is the host user's FIRST SUBUID, which inside `podman unshare` is
ns-uid 1 — hence 1.

The property J-7 actually needs is *owner != the host user*, which holds for ANY subuid; 0 is the one
value that provably breaks it. Named constants so a bifrost measurement corrects this in one place.

⚑ **CAVEAT:** in a DEGENERATE configuration — a host `/etc/subuid` range shorter than GUEST_UID (so
keep-id cannot fill container 0..999 from subuids at all) — ns-uid 1 may not render as container-root
in-box. The SAFETY property is unaffected: the owner is still a subuid and still not the host user,
so the books stay unwritable by the agent; only the cosmetic "shows as uid 0" would differ.

```CANON_SKELETON_DIR_MODE = "555" · CANON_SKELETON_FILE_MODE = "444"```
⚑ TWO MODES, NOT ONE (spec J-7 banner, amended 2026-07-31).

**DIRS `r-xr-xr-x`:** unwritable by everyone, but the SEARCH bit stays set so crun's openat2
destination resolution can still traverse `~/canon` and `~/canon/bible` to reach the chapter
mountpoints — a 444 directory would break every canon bind.

**FILE mountpoints `r--r--r--`:** the search bit is meaningless on a file, and 555 would mark a
0-byte `.md` executable for no reason. Applied as a SEPARATE chmod call precisely because the two
sets need different modes.

```canon_skeleton_rels() -> tuple[tuple[str, bool], ...]```
The canon skeleton as `(home-relative posix path, is_dir)` pairs (J-7).

⚑ **DERIVED FROM THE SAME CONSTANTS AS THE BIND DESTS, never restated:** the skeleton IS the mirror
image of the canon binds, so one edit to :data:`ROM_BIBLE_CHAPTERS` / :data:`HANDBOOK_CHAPTERS` moves
both sides at once. A hand-kept second list is exactly the duplicated-shared-data class the design
principles forbid — and a skeleton that drifts from the binds is a mountpoint podman then creates
itself, which is the whole failure J-7 exists to remove.

Ordered PARENTS-FIRST so a caller can create them in sequence.

`canon/notebook` and `canon/workbook` are ABSENT by design: they are SEEDED, agent-owned and
writable, and become undeletable only because their parent is 555 — which is intended, not a side
effect.

⚑ `agent` is ALWAYS pre-created among the bible chapters, emission gate or not (J-7): a gate-false
launch must show an EMPTY root-owned mountpoint, not a missing directory.

⚑ **THE CHAPTER MOUNTPOINTS ARE NOT EMPTY (F1):** three of them carry a 0-byte IMPORT-FALLBACK entry
file, so `SYS_CONTENTS.md`'s unconditional imports resolve-to-empty instead of warning on every
launch of every box that has no workset or box chapter. A BOUND chapter replaces the whole directory,
fallback included. Their `directives/` parents are part of the skeleton too — root-owned like
everything else here, so nothing in the books is agent-creatable. See
:data:`HANDBOOK_FALLBACK_ENTRIES`.

```materialize_canon_skeleton(shell_path: Path, *, logger=None, quiet: bool = False) -> None```
Create the canon SKELETON in a box home and make it root-owned + unwritable.

The J-7 assembly model in one function. Callers today:

* **box create** — `commands/box/_parser.py` and the connect-flow first launch in
  `commands/start.py`, in both cases AFTER the seed and INSIDE the create-journal window.
* **after any box-home COPY** — `commands/box/_lifecycle.py`, `commands/box/_duplicate.py`,
  `commands/restore.py`.
* **a per-launch re-assert** — `commands/start.py`'s post-start hook, with `quiet=True`.

⚑ **ORDER IS LOAD-BEARING: seed FIRST, protect SECOND.** The seeds/handbook half will seed
`canon/notebook` + `canon/workbook`, which live UNDER `canon/`; if the 555 landed first those copies
would fail with EACCES.

⚑ **IDEMPOTENT, BUT NOT EXTENSIBLE ONCE PROTECTED.** Re-running over an already-materialised skeleton
is a no-op (every `mkdir`/`touch` is create-if-absent) and the ownership pass is a plain re-assert,
so calling this after a box-home COPY restores what the copy could not carry. It does NOT, however,
let a FUTURE release add a new mountpoint to :func:`canon_skeleton_rels` and have existing boxes pick
it up: creating a new entry inside an already-root-owned parent fails with EACCES, and this function
swallows that (per-path `OSError` is debug-logged and skipped, because one unmakeable mountpoint must
not cost a box all the others — podman's own error at launch is the honest signal).
⇒ **Growing the skeleton is a MIGRATION, not a redeploy.** That is why the handbook mountpoints are
created NOW, ahead of the binds that use them.

⚑ **WHY OWNERSHIP AND NOT MODE ALONE.** A 555 directory the agent OWNS is not protection — the owner
can `chmod +w` it back. Only an owner the in-box agent is not can make `~/canon` un-litterable.

**DEGRADED PATH** (no container runtime, docker, or a failing `unshare`): the skeleton is left
agent-owned and writable and ONE warning is logged. Box create must not hard-fail on a missing
runtime — creating a box works today with no podman installed — and the skeleton is what makes the
binds land, so a box in this state is fully functional, just not litter-proof.

```materialize_canon_skeleton_if_present(shell_path: Path, *, logger=None) -> None```
Re-assert an EXISTING canon skeleton; do nothing if the home has none.

The post-start re-protect for homes that are NOT box homes — helper boxes today
(`channels/helper_listener.py`). :func:`materialize_canon_skeleton` would CREATE the skeleton, which
is wrong here: a helper home is not a box and gaining canon mountpoints from a launch would be a
silent layout change made by the wrong seam. Re-asserting what is already there is always right,
because `:U` re-chowns whatever the bind source holds.

```_protect_canon_skeleton(dirs, files, log, *, quiet: bool = False) -> None```
Make the skeleton root-owned + unwritable from inside the user namespace.

THREE `podman unshare` calls: one `chown` over everything, then a `chmod` per mode class — dirs `555`
(the search bit is what lets crun traverse `~/canon` to reach the chapter mountpoints) and file
mountpoints `444`.

⚑ **NEVER `-R`:** a recursive sweep of `canon/` would take the SEEDED, agent-owned `notebook/` +
`workbook/` with it, which must stay writable. The skeleton is a closed, enumerated set, so an
explicit list is both safer and no harder.

```_warn_unprotected(root, log, reason, agent_owned, quiet: bool = False) -> None```
Report a skeleton that did not get its full lockdown.

⚑ *quiet* demotes the report to DEBUG. The POST-START caller sets it: that hook runs while the
user's terminal is being handed to the agent (tmux, a TUI), and on a docker host — or anywhere
`unshare` cannot work — this fires on EVERY launch, so at WARNING it would paint over the session,
forever, for a condition the user already learned about at box create. Create reports it loudly; the
per-launch re-assert does not repeat it.

⚑ **The two arms are genuinely different and must not share wording.** If the CHOWN did not happen
(*agent_owned*), the tree is the agent's and fully writable in-box. If the chown SUCCEEDED and only a
chmod failed, the tree is root-owned at its default mode (0755/0644) — the agent CANNOT write it, but
the world-traversal and file modes are not the declared ones, and a later chmod re-assert is still
owed.

## The helper hub binds

```helper_default_categories(*, socket_path: Path, log_path: Path) -> BindArmTable```
Build the helper hub binds as `default_categories` (Phase B).

Fills the TERMINAL `box.bindings.rw` / `box.bindings.ro` arms with one
`box_dest -> (host_src, options)` entry for the live helper unix SOCKET and the per-box helper
message LOG respectively — TODAY's hardwired `_HMount` appends inside the `helpers_enabled` block
routed through the category resolver.

Both box-side destinations are STATIC and carried by the declarative file as `~`-spelled dests under
the fixed pinned root (:data:`~kanibako.settings.settings_resolve.BOX_PINNED_ROOT_RELPATH`),
absolutized to `GUEST_HOME` by
:func:`~kanibako.settings.settings_resolve.normalize_bind_dest` inside :func:`add_bind` (R-11) —
exactly like every other declared dest in the file. They carry no `$XDG_STATE_HOME` token: a mount
dest is written into the runtime's arguments BEFORE the box is live, so resolving XDG host-side
bought only a four-way hand-held agreement that had already drifted; the box's real XDG location is
served after boot by `box_supervisor.project_pinned_xdg`.

Only the host SOURCES (*socket_path* / *log_path*) are runtime-probed and injected at the seam, GATED
on `.exists()` here — reproducing the old skip-if-missing appends: a missing socket/log simply omits
its key.

⚠ **`helper_sock` options MUST be `""` (empty):** it is a LIVE unix socket the hub listens on; a
`Z`/`U` relabel/chown would break the shared socket topology. The per-entry EMPTY options value
carries that through the bind entry's optional 2nd slot.

⚑ **B2b: `helper_log` routes through the spec's own formula** `@workset.logs/@{meta.box.name}.jsonl`
(§2c) — byte-identical to the probed `src_path` in all three modes, since `workset.logs` and
`meta.box.name` resolve to exactly what `helper_log_path(std, proj)` builds (gated by a before/after
comparison of the resolved bind, PHASE R). ⚑ The `.exists()` gate keys off the PROBED path while the
emitted host_src is the FORMULA, so a user repointing `workset.logs` moves the MOUNT but not the
hub's WRITER — see migration M-14.

⚑ **`helper_sock` is NOT routed:** its host path is the LENGTH-BOUNDED (hashable) socket name
`bounded_socket_name(<box>-<ws>, run_dir)`, which the spec form `@system.runtime/<box>-<ws>.sock`
cannot reproduce when the name is hashed for the AF_UNIX `sun_path` limit (JC-B2b-3) — so it keeps
its probed literal host_src (the `.exists()` gate is unchanged either way).

## The image-sharing binds

```image_default_categories(*, graph_root: Path | None, storage_conf_path: Path) -> dict[str, object]```
Build the image-sharing binds as `default_categories` (Phase B, D-M8).

Returns a MIXED table: the TERMINAL `box.bindings.ro` arm carrying one `box_dest -> (host_src,
options)` entry for the host image graph root and one for the GENERATED `storage.conf`, routed
through the category resolver (the sole route; the old hardwired Mounts are gone), PLUS the
`box.images_store` floor SCALAR. The box-side destinations + options come from the declarative file
(`images:` list); the host SOURCES (the runtime-probed *graph_root* and the already-GENERATED
*storage_conf_path*) are injected here.

⚑ **B3** (spec §2b / §2c, D-M8): the store bind — the arm's `/var/lib/shared-images` entry — is
ROUTED THROUGH THE USER KEY. Its shipped host_src is the `@`-ref `@box.images_store` (the file's
`meta_ref`, helper_log parity), and the runtime-probed *graph_root* enters the keyspace HERE as that
key's DEFAULT — the manifest §2b row spells that default as *"&lt;runtime-probed podman
graphroot&gt;"*, and this is where it is supplied: a floor scalar in the returned table. The floor
is the least-specific cascade level
(`base`), so a `box:` / `workset:` / `system:` FILE value for `images_store` overrides it by name and
the ONE expand pass resolves the bind's host_src to the winning value. `images_conf` stays an
INTERNAL bind and NOT a key (fixed location, generated content — spec §0's test).

⚑ **11a (2026-08-02): the PROBE feeds ONLY that default.** *graph_root* may be `None` (probe failed),
in which case NO floor scalar is emitted and the `@box.images_store` host_src resolves solely from a
set tier value (or propagates ABSENT, dropping the bind). The caller's gate is now just
"image-sharing requested" — the code realization of the spec's `%if @box.share_images%` condition on
the `images` row; whether the RESOLVED store exists is decided after the resolve, at the seam.

⚑ `meta_ref` (when declared) is the emitted host_src — the spec's own `@`-ref spelling; the symbolic
`source` stays the probed-literal fallback (`helper_default_categories` parity), resolved LAZILY so a
probe-fail `None` only raises if an entry actually needs the probed literal.

---

## Dropped as false

Eleven prose claims in the pre-pass source were checked against the code and found FALSE. They were
**deleted, not relocated** — relocating a drifted claim launders it into a document that reads as
current. Recorded here so nobody restores them from git history.

| # | site | the claim | what the code does |
|---|---|---|---|
| 1 | module docstring | the shipped file holds *"the `box.masks` default … and the per-mode channel bind table"* | it declares EIGHT families (`masks` · `channels` · `core` · `kani` · `kickoff` · `canon` · `helpers` · `images`), and the module additionally owns the box-create canon SKELETON, which is not a default at all |
| 2 | module docstring | the launch path injects them as *"the AGENT-level `default_categories`"* | `build_launch_snapshot` folds them into ONE **base-level** floor; only the PLUGIN tables arrive agent-discriminated. `image_default_categories`'s own prose said `base` — the two contradicted each other inside one file |
| 3 | `channel_default_categories` | *"the five system channel type roots (common/chat/share/mailboxes)"* | FOUR type roots; the fifth `scope: system` row is the inbox, which the same sentence names separately |
| 4 | `kani_default_categories` | *"the in-box kanibako package + entry script"* / *"Both binds are UNCONDITIONAL"* | THREE rows — `kani_pkg`, `kani_bin`, `secret_export`. The code's own `sources` dict has three keys and the YAML calls them "the unconditional trio" |
| 5 | `materialize_canon_skeleton` | *"the LAUNCH path never touches it"* | `commands/start.py` calls it on the post-start hook of EVERY launch (`quiet=True`) and on the connect-flow first launch. `_warn_unprotected`'s own prose ("The POST-START caller sets it") contradicted it in the same file |
| 6 | `materialize_canon_skeleton` | *"must not cost a box the other thirteen"* | `canon_skeleton_rels()` returns **20** entries (14 dirs + 6 files); "thirteen" implies a 14-entry skeleton, i.e. pre-F1. Replaced with a direction, not a distance |
| 7 | `HANDBOOK_CHAPTERS` comment | their binds are *"the seeds/handbook half's … which does not exist yet"* | `canon_default_categories`, in this module, emits all five `~/canon/handbook/*` binds today and floors the `agent.*.canon` keys they resolve against |
| 8 | `CANON_SEED_DENY_PREFIXES` comment | *"`canon/handbook` IS ALREADY IN THE LIST, even though nothing BINDS it until the seeds half lands"* | same — it IS bound today. The skeleton-ownership rationale (the part that is still true) was kept |
| 9 | `assert_canon_bind_seed_disjoint` | *"the SEEDS/handbook half appends `canon/handbook` to it, with no edit to the rom emitter"* | `canon/handbook` is already a member of `CANON_SEED_DENY_PREFIXES`; there is nothing left to append |
| 10 | `helper_default_categories` | *"The per-entry empty-options 3rd slot carries that through `unpack_bind`"* | under dest-keying (R-6) `add_bind` writes `(host_src, options)`, so options is the **2nd** slot, and dest-keyed entries are consumed by `unpack_bind_entry` (`settings_assemble.py`), not the legacy name-keyed `unpack_bind`. The same docstring said `box_dest -> (host_src, options)` two paragraphs earlier |
| 11 | `add_bind` | *"rather than at ten call sites"* | **eleven** today (nine here + two in `settings/agent_defaults.py`). A distance, not a direction; replaced with "at every call site" |

⚑ The same two stale claims as #4 and #10 also live in `src/kanibako/data/core-defaults.yaml` (the
shipped DATA file, a different seam): its `core:` header says options are *"the OPTIONAL 3rd slot …
consumed by `settings_resolve.unpack_bind`"* and its `helpers:` header repeats the 3rd-slot wording.
Boarded, not fixed here.
