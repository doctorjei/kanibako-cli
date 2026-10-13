# The Bootstrap Config File — loading, writing, and the flat merged object

This module owns `kanibako.cfg` and the two flat dataclasses either side of it —
`BootstrapConfig` (Layer 1, what that file holds) and `KanibakoConfig` (Layer 2, the merged box
scalars) — plus the
YAML read/write primitives, the built-in defaults, the box-scalar floor and field typing that
`settings_launch`'s keyspace resolve uses, and a handful of
DIRECT readers (`workset.kuid`, `workset.skip_kuid_check`, `system.agent`,
`system.setup_completed`, `system.helpers.*`, and — for the AUTHORING TIER only —
`box.enable_vault`) that must answer BEFORE a cascade snapshot exists. It also carries two
arbiters that are not config reads at all and live here only because their inputs do:
`setup_compat_gate` and `resolve_agent`, and the shared refusal predicates every door asks — see
"The null, identity and ordering refusals" below.

⚑ **`system.agent` and `system.setup_completed` read the SYSTEM SETTINGS file, not
`kanibako.cfg`** — they live in this module for their pre-cascade TIMING, not for their
file; see `read_setup_completed`.

It is the OLDEST tier in the settings stack and the most nearly-retired. Almost everything a box
resolves at launch goes through the keyspace cascade, not through here; what remains is (a) the
bootstrap PATH tables the cascade itself needs in order to locate its files, and (b) the
pre-cascade readers, each of which documents why it cannot wait for a snapshot.

Authority: spec `~/canon/workbook/specs/settings-keyspace-1.8.0.md` §0 (closed keyspace), §1/§1A (the Layer-1 `config.*`
foundation + the CLI level), §2b (`box.agent_name` retired), §2c (the box/workset settings-file
derivation), §2g (the Layer-2 `system.*` path settings + `system.agent`), §2h (`pref.*` requests).

## The two file sets, and why they are not the same thing

Two DIFFERENT file families are read here and confusing them is the standing hazard:

* **The CONFIG (bootstrap PATH) set** — `/etc/kanibako/base.cfg` < the user's
  `$XDG_CONFIG_HOME/kanibako.cfg`. It carries the Layer-1 `[config]` foundation keys and
  NOTHING ELSE, and it is what tells everything else where the settings files LIVE. Read by
  `load_config` into `BootstrapConfig.config_paths`.
  ⚑⚑ **THE RULE IS IN THE READ's SHAPE, and this is the passage most likely to be remembered
  wrong.** It is not a `config.`-prefix filter: `bootstrap_config_paths` walks IN through the
  `config:` table, so `config.`-prefixed is all it can produce, and its return type has nowhere to
  put a settings value. A settings table in that file is REFUSED, naming the file and the keys
  (`messages.ERR_CONFIG_LAYER1_SETTINGS`). The settings file's own `system:` path table has
  its own reader, `system_path_set_values`.
* **The SETTINGS (behavior) set** — `/etc/kanibako/settings_base.yaml` < `global/settings.yaml` <
  the agent file < the workset tier < the box tier. This is the real 6-level cascade; nothing in
  this module resolves it; the box scalars resolve in `settings_launch.resolve_box_scalars`.

`config_base_path` and `settings_base_path` are the two `/etc` floors, one per family. A missing
file is treated as an empty level in both cases, so absence preserves current behavior.

## The `None` sentinel — absent is not the same as present-`None`

A present `None` (YAML `null` / `~` / a bare `foo:`) is a VALUE, never a request for the default
(spec §2h). `_present_scalar_fields` keeps it and omits an absent key. In the resolve
(`settings_launch.resolve_box_scalars`) a winning `None` reaches the field as `None`, or is refused
where `refuses_null_box_scalar` says the key's default is a value (every box scalar but
`box.shell`). `load_project_overrides` counts neither an absent key nor a present `None` as an
override.

## B6 — the box scalars are KEYSPACE-resolved

`box.image` / `box.share_images` / `box.shell` / `box.enable_vault` (`_BOX_SCALAR_FIELDS`) resolve
through the keyspace in `settings_launch`: `load_merged_config` runs `resolve_box_scalars` and lands
each resolved value on its `KanibakoConfig` field. Path resolution reads `box.enable_vault` through
`paths.resolve_box_enable_vault`. This module keeps what they share: the field map, the
declared-default floor, the field typing (`_typed_box_scalar`), and the null rule
(`refuses_null_box_scalar`).

⚑ The Layer-1 file carries no settings (*"kanibako_config.yaml <-- cannot have settings.
Period."*), so the scalars START at the declared defaults and no Layer-1 value reaches them.

### The floor

⚑⚑ **THE FLOOR IS THE DECLARED DEFAULTS.** `config.box_scalar_defaults_floor()` builds it from
`KanibakoConfig`'s own field defaults; without it `@box.image` would dangle at launch AND at set
time. `box_scalar_defaults_floor` is the ONE recipe, folded
by `settings_launch.fold_floor` into every resolve, by `paths._narrow_box_scalar_cascade`, and by
`config_interface._category_set_lookups`, so the launch floor and the set-time floor cannot drift. `box.shell`'s field default is `None`,
the declared `<None>` (spec §2b `box.shell | <None>`), and the floor SUPPLIES it as a present
`None` ([R177]). It is not
`""`: a `default_categories` fold drops a `""` as a suppression (`if val == "": continue`), which
would leave `@box.shell` dangling. A whole-value ref to it resolves to `None` (§0). A winning `None`
lands on the flat `box_shell` field as
`None`, and `launch.shells.resolve_box_shell` auto-detects.

⚑ The floor is the DECLARED DEFAULTS, folded into the base level, so a workset or box value
cannot masquerade as the system-stored default — it enters the resolve at its OWN tier instead.

A `box:` table in `global/settings.yaml` — where `kanibako system set box.image=…` writes —
resolves too.

### There is no machine-wide third file

There is no `/etc/kanibako/kanibako.yaml` (spec §2). The admin authority is exactly the
`base.cfg` / `settings_base.yaml` base tiers, resolved on the PATH side; this scalar
loader starts from the built-in defaults.

## The pre-cascade readers, and the rule that does not follow from them

`read_box_enable_vault`, `read_workset_kuid`, `read_workset_skip_kuid_check`,
`read_system_agent` and `read_system_helpers` all read a DECLARED key DIRECTLY out of a settings
file rather than through the resolver, because each has a caller that runs before a snapshot
exists. That is the reason these functions exist.

⚑⚑ **WHAT DOES NOT FOLLOW FROM IT IS *"the DEFAULT lives in the reader, not in a cascade
floor"*.** The premise ("a caller runs before a snapshot exists") is about a FULL launch snapshot.
A NARROW resolve needs only FILE PATHS, and every one of these callers computes them a line or two
above the read. A default living only in the reader would cost twice:

* the declared default would reach NO cascade floor, so a whole-value `@`-reference to the key
  would resolve to `__MISSING__` in every launch snapshot; and
* the reader would define the resolution ORDER by which files it happened to open, so a tier it
  did not open would be silently dropped.

So every such default has a cascade floor (*"If you are asking if we should avoid a carve out, the
answer is yes"*): `workset.skip_kuid_check` and `workset.kuid` in
`settings_launch.workset_anchor_floor`, and `box.enable_vault` in `_BOX_SCALAR_FIELDS` /
`box_scalar_defaults_floor`. 🛑 **A reader keeping a literal default is not the same thing as a
reader OWNING it** — `read_workset_skip_kuid_check` and `read_box_enable_vault` return `True`, and
each carrier is held to the manifest cell by a kinemata view — the readers by `reader-defaults`, the
floors by `workset-anchor-floor` and `box-scalar-floor` — so the two carriers cannot drift.

⚑ `read_box_enable_vault` is NARROW. Its question is *which TIER authored this value*, which a
merge structurally cannot answer, and which lifecycle ops need so they never pin an inherited
workset default as a box-scope override at a destination.

## The null, identity and ordering refusals

The predicates every door asks — the `set` door, the read-time refusals and the launch — so each
rule has one carrier:

* **null path keys** — `refuses_null_path_key(canonical)` is the membership (derived from the
  readers: both path tiers' defaults tables, plus `workset.boxes` and `workset.registry`), and
  `null_path_keys_error(path, keys, ...)` THE message builder, with a read-time lead that names the
  file and a `set` door lead that cannot. The one exclusion is `STANDARD_BIND_SOURCE_KEYS`: the
  `system.*` path keys a `<None>` OMITS rather than refuses, because each sources a standard bind.
* **null box scalars** — `refuses_null_box_scalar(canonical)`: true iff the key's own declared
  default is a value, so a null has nothing to say (every box scalar but `box.shell`).
* **the box store** — `usable_box_store_value(value)` is the launch's own test of a box-store root,
  and `refuses_box_store_value(canonical, value)` adds the key membership for the `set` door.
* **per-owner identity** (keyspec §0 "Per-owner resources") — `key_owner(key)` reads the owner from
  `config_keys.KEY_OWNERS`; `reaches_identity(...)` asks whether a value's `@`-chain reaches its
  owner's `meta.*` identity in a box mode (`kb_store.IDENTITY_*`); `identity_gap(...)` returns the
  `IdentityGap` (cure, who, sharers) a refusal prints; `chain_reaches(...)` is the one transitive
  walk under both.
* **ordering** — `ref_order_error(canonical, value)` refuses an `@`-ref naming a key set resolved
  after the key's own (`kb_store.RESOLUTION_ORDER`); `system_path_ref_error(canonical, value)`
  holds a `system.*` path value to the system path tier.
* **the system table** — `system_table_set_values(settings_path, doc)` is `system_path_set_values`
  over an already-read document.

## Functions

```coerce_bool(value: object) -> bool | None```
Coerce a config value to a real bool using the shared truth table.

Returns the bool, or `None` if *value* is not a recognized bool literal. Already-bool values pass
through. The truth tables (`_BOOL_TRUE` / `_BOOL_FALSE`) are shared by the typed `config set`
writer (`config_interface`) AND the box.meta writer so both round-trip identically.


```class BootstrapConfig```
The Layer-1 bootstrap file's WHOLE content: the `config.*` foundation, and nothing else.

⚑⚑ **THE TYPE IS THE RULE (P3/P4).** It has no settings field, so no filter can be needed and none
exists. `load_config` returns this; `load_std_paths`, `resolve_project`
and every pass-through signature down through `commands/box/_lifecycle.py` and
`launch/box_resolve.py` are annotated with it, so a `KanibakoConfig` handed to one of them is a
type error rather than a value that silently carries the wrong layer.


```class KanibakoConfig```
The flat merged SETTINGS object.

`settings_launch.load_merged_config` builds it: the CLI overrides that name a field, then each
`box.*` scalar from the keyspace resolve (see "B6" above), which beats them.

⚑ `box_enable_vault`'s field default is THE carrier of `box.enable_vault`'s declared default —
that is what `box_scalar_defaults_floor` publishes, and what gives `box show --effective` a row for
the key (the display iterates `fields(cfg)`).

⚑ There is no agent field: `box.agent_name` is RETIRED (spec §2b) and a box selects its agent with
the REQUEST `pref.system.agent` (§2h), resolved off the launch snapshot by
:mod:`kanibako.settings.agent_select` — the selection is a KEY.

⚑ **THERE IS NO `config_paths` FIELD HERE.** The Layer-1 `config.*` set-values are
`BootstrapConfig.config_paths`, and the Layer-2 `system.*` path settings have their own reader, so
one read never answers two layers' questions.

⚑ `BOX_META_FILE` (`"box.yaml"`) is the per-box construct-time metadata + box-tier settings
cascade file (spec §2c, `meta.box.*`).


```_scalar_value(value: object) -> object```
A settings-file scalar as the flat object carries it.

Booleans are preserved (`str(False)` is the truthy `"False"`); `None` is preserved as a value (see
"The `None` sentinel" above); other scalars are stringified.


```config_file_path(config_home: Path) -> Path```
The bootstrap config file `$XDG_CONFIG_HOME/kanibako.cfg`.

CLEAN BREAK (JC-1): the old `kanibako.yaml` name is NOT read.


```config_base_path() -> Path```
The machine-wide CONFIG base file (`/etc/kanibako/base.cfg`).

The least-specific layer of the bootstrap-PATH file set: a site admin supplies overridable
defaults that the user's `~/.config/kanibako.cfg` can still beat. Missing file → treated
as an empty level.


```settings_base_path() -> Path```
The machine-wide SETTINGS base file (`/etc/kanibako/settings_base.yaml`).

The LEAST-specific (bottom) layer of the SETTINGS (behavior) cascade — below every scope
(`system` / `agent` / `workset` / `box`): a site admin supplies overridable behavior defaults that
any scope can still beat. Missing file → treated as an empty level, so its absence preserves
current behavior.


```_present_scalar_fields(path: Path) -> dict[str, object]```
The DECLARED box scalars PRESENT in a SETTINGS file, as field-name → value.

`None` is preserved as a VALUE, not a request for the default (spec §2h); callers must
distinguish it from an absent key (which simply will not appear in the returned dict).

⚑⚑ **IT WALKS IN THROUGH `_BOX_SCALAR_FIELDS`' DOTTED SPELLINGS — the closed keyspace.** A
flattened, underscore-joined namespace would COLLIDE with the `KanibakoConfig` field names, so an
undeclared top-level `box_image:` would resolve identically to the declared `box: image:` (spec §0:
an undeclared key is not a key). Reading in through the declared spelling makes the flat one
UNREACHABLE rather than refused by a list (P4): a table the walk never enters cannot leak.


```_layer1_settings_keys(data: dict) -> list[str]```
Every SETTINGS entry a Layer-1 document carries, dotted and sorted; empty ⇒ the file is clean.

The message's key list. ⚑⚑ **A TABLE WITH NO LEAF IS NAMED BY ITS TABLE NAME**, which is what the
`or [name]` fallback is for. The three empty spellings a user reads as identical — `box:` with
nothing under it (**YAML parses that to `None`, not `{}`**), an explicit `box: {}`, and a `box:`
whose only leaf is itself an empty table — all three are settings tables that do not belong in
this file, so all three refuse (convention 0: one rule, one answer).

⚑ The fallback is `str(name)`, not `name` — see `_flatten_leaves`: a YAML key need not be a string,
and this list is sorted and joined.


```bootstrap_config_paths(path: Path) -> dict[str, str]```
The Layer-1 file's `config.*` foundation, read from its `config:` table ALONE.

⚑ **NO FILTER, AND THAT IS THE POINT (P4).** The walk STARTS at the `config:` table, so a `config.`
prefix is the only thing it can produce; the rule is in the shape of the read rather than in a test
applied after it. 🛑 A settings table here RAISES `ConfigError` naming the file and the keys — it is
not dropped. That refusal is what a user with a stale `[box]` or `[system]` table sees instead
of silently running something other than what their file says.

⚑⚑ **AN UNDECLARED LEAF INSIDE `config:` REFUSES TOO.** Starting the walk at the table makes the
`config.` PREFIX unfakeable and says nothing about the TAIL. 🛑 **An undeclared leaf would NOT be
inert.** `resolve_config_paths` walks `CONFIG_PATH_DEFAULTS` for its **output**, but its inner
`lookup` resolves `@`-refs through `LevelView("config", values=dict(set_values), …)`, i.e. **the
file's set-values**. So `data: "@config.nonsense/kanibako"` beside `nonsense: /srv/elsewhere` would
root the store at `/srv/elsewhere/kanibako`: an undeclared NAME the resolver would follow, a §0
breach.
Spec §1: *"The Layer-1 set is exactly the config keys in the table below."* The admitted set is
`CONFIG_PATH_DEFAULTS`, **the table `resolve_config_paths` itself iterates**, so ACCEPTED HERE ⇒
RESOLVED THERE holds by construction and a key joining §1 carries its own admission (P13). It is the
same six spellings as `settings_keyspace.DECLARED_CONFIG_LEAVES`, pinned equal through the manifest
(`test_manifest_conformance`'s `TestKeySetConformance` holds the leaves to the manifest rows, the
`bootstrap-path-defaults` kinemata view holds the table to them); the Layer-1 table is what this
reader takes because Layer 1 resolves
through the flat resolver, NOT the keyspace pipeline (spec §1).

⚑ A `config:` entry that is **not a table** refuses as well; reading it as `{}` would put the whole
store back at its default location for a user whose one line meant to move it. A
`config:` with NOTHING under it is not that case — `write_global_config` writes zero bytes, so
absent, `config:` and `config: {}` must agree, and all three read as the empty foundation.

⚑ A `null` leaf refuses by name (`_refuse_null_paths`, through `null_path_keys_error`), never read
as the text `None`.

⚑ A stale `[system] templates_stamp` or `[system] setup_completed` is in neither band — it is a
`system:` table, so it refuses as settings; see "The retired template-stamp gate" below.


```system_path_set_values(settings_path: Path) -> dict[str, str | None]```
A SETTINGS file's `system.*` set-values, dotted — the Layer-2 half of the path tier.

Its own reader, separate from the Layer-1 read, so neither layer's file speaks for the other.
Nested sub-keys
(e.g. `system.channels.common`) become dotted keys. ⚑ NOT filtered to the path tier: that is
`paths.load_system_config`'s own P13 job, and this file's `system:` table legitimately holds
`system.agent` and the category families too. That is also why the `null` refusal
(`_refuse_null_paths`) is scoped to `SYSTEM_PATH_DEFAULTS`: `system.agent: null` means "no default
agent" (spec §2b).

⚑ A NON-TABLE `system:` reads as `{}` here, where the Layer-1 read refuses a non-table `config:`.
The tiers differ because this file is a keyspace tier: the launch resolve already refuses
`system: /x` by name as a scalar at a namespace (spec §0), so a refusal here would be a second
carrier. Layer 1 is outside the keyspace, and no later read would catch it.


```load_config(path: Path) -> BootstrapConfig```
Read the LAYER-1 bootstrap file — the one reader of `kanibako.cfg`.

⚑⚑ **IT RETURNS A `BootstrapConfig`:** a Layer-1 read has no settings field to return, so the
Layer-1 file cannot hand back a `box.image` it may not carry. The box scalars are read from
SETTINGS files by `settings_launch.load_merged_config`; a settings
file's `system.*` path set-values by `system_path_set_values`.


### `_BOX_SCALAR_FIELDS` and `_typed_box_scalar`

`settings_launch.resolve_box_scalars` and `settings_launch.load_merged_config` resolve the box
scalars; this module keeps the field map and the typing they use.

⚑ `_BOX_SCALAR_FIELDS` maps the box-scope SCALAR keys that resolve through the KEYSPACE (B6,
R-11a(a)): dotted key → the flat `KanibakoConfig` field it lands on. `box.shell` rides the same
resolve — it lives on the same object and the same `box:` tables (consumer-map risk 4).
`box.enable_vault` is one of them; see "The pre-cascade readers" above.

⚑ `_typed_box_scalar` lands each resolved value on its field's own type: a field whose DATACLASS
DEFAULT is a bool goes through :func:`coerce_bool` (falling back to `bool(value)` for an
unrecognized literal), a `None` on a field whose default is `None` stays `None`, and everything
else is stringified. The arm
is selected off the default rather than a hand-kept name list, because adding a bool field is
exactly the edit that would otherwise ship a stringified `"False"` — which is truthy, i.e. a stored
`enable_vault: false` silently reading as ON.


```write_global_config(path: Path) -> None```
Create the bootstrap config file EMPTY — it may carry `config.*` and nothing else.

⚑⚑ **THE FILE CANNOT HAVE SETTINGS** (*"kanibako_config.yaml <-- cannot have settings.
Period."*). It is created **EMPTY — zero bytes**, always.

⚑ **THERE IS NO `cfg` PARAMETER, and that is the rule in the signature.** A
`KanibakoConfig` *is* settings, so there is nothing it could legitimately contribute here; keeping
it and ignoring it would be a silent no-op for every caller that passed one. A non-default
`box.image` belongs in a SETTINGS file — which is where `kanibako system set box.image=…` has
always written it.

⚑ **WHY AN EMPTY FILE MOVES NO RESOLVED VALUE.** `paths.resolve_config_paths` /
`resolve_system_paths` take the defaults tables as their `LevelView(defaults=…)` and layer STORED
values OVER them, so a file storing exactly the defaults would be another carrier that changes
nothing — while making every default edit need a matching edit here, BY HAND.
`tests/test_settings/test_config.py::test_sparse_file_resolves_identically_to_the_old_verbatim_defaults`
is the pin. This generalizes the rule spec §2b states for one key, `box.enable_vault`.

⚑ **THE FILE IS STILL CREATED.** `cli._ensure_initialized` uses its EXISTENCE as the "already
initialized" test, so an absent file would re-run first-run init — packaged-template install and
all — on every command forever. **Zero bytes, not `{}`**: this file is the hand-edit surface the
`config.*` refusal sends users to (`config_keys._config_key_refusal`), and a leading `{}` makes an
appended `config:` block a YAML error. It goes through the same atomic writer `dump_doc` delegates
to, so the create is atomic either way.

⚑ **NO `agent_name` row**: `box.agent_name` is RETIRED (§2b), and writing a BOX key into the CONFIG
file was wrong even while it existed — nothing ever read it back from here. Stale copies in existing
`kanibako.cfg` files are documentation-only (`MIGRATION.md` § *Settings keys renamed or retired*).


```persist_creation_flags(box_settings_path, *, materializing, image=None, share_images=None) -> None```
The §1A **CREATE EXCEPTION** — the ONE gate through which a shadowing CLI flag's value ever
PERSISTS.

R-11a. Spec §1A: a flag applies to ONE launch and NEVER mutates
an EXISTING stored value — *"at box CREATION only, a shadowing flag's value PERSISTS — it
INITIALIZES the box's stored config."*

Launch-MATERIALIZATION counts as creation: the one signal is *materializing* — "is this box being
materialized by THIS invocation?" — which `kanibako create` and the launch path both read off their
resolve's `proj.is_new`. Every caller routes through THIS gate; there is no per-path persist logic,
so `create`, the first launch of a `workset connect`-ed box (connect registers
the box and creates its dir but never seeds, so that launch IS the materialization), and a plain
`start --image` on an EXISTING box (strictly ephemeral) all get the rule from one place.

⚑ A launch of a REGISTERED box whose directory was deleted never reaches here: MBR-6 refuses it at
the launch gate — it is a repair, not a creation.

Only EXPLICITLY-GIVEN flag values persist: an absent flag (`None`; `""` for *image* — absent ≠
`""`) writes NOTHING, so a no-flag create bakes NO default into the box tier and the box resolves
the live cascade (single source of truth; the stored default stays at its own tier). No flags → no
write at all — no empty `box.yaml` is materialized (the :func:`write_box_enable_vault` rule).

*box_settings_path* is the BOX-TIER settings file from `box_workset_settings_paths` — the same file
`box set box.image=…` writes and the launch cascade reads as the box tier (`MIGRATION.md` §
*Standalone boxes: reads got truthful*). *share_images* is a real bool or `None` (absent); it is
written as a bool, matching the `KEY_TYPES` coercion `config set box.share_images` applies.


```write_box_enable_vault(path: Path, enable_vault: bool = True) -> None```
Sparsely persist the box-scope `box.enable_vault` key at *path*.

The single writer for `box.enable_vault` at box create/move time. Create emits no `project:` /
`resolved:` section: box identity lives in the registries (`box_resolve`), not on disk. Sparse,
matching
`config set box.enable_vault`:

* `enable_vault` explicitly `False` → write `box.enable_vault = False` into the `box:` table
  (created + merged beside `box.image`);
* the default `True` → write NOTHING, and DROP any stale `box.enable_vault` override. An empty
  `box:` table is never materialized, and a would-be no-op leaves the file untouched — so a
  default-vault primary/named box gets no `box.yaml` written here.

⚑ WHICH value a caller hands in is the resolver's business, not this writer's: PRIMARY and NAMED
pass the BOX-AUTHORED read, never the workset-resolved one (spec §2b's `box.enable_vault` row keeps
the key sparse); STANDALONE passes the resolved one on purpose — that write IS the migration
(`MIGRATION.md` § *Standalone boxes: reads got truthful*).

Paired reader: :func:`read_box_enable_vault`.


```read_box_enable_vault(path: Path) -> bool```
What the BOX ITSELF authored for `box.enable_vault` at *path* — one file, no cascade (default
`True`).

⚑⚑ **THE AUTHORED READER, AND ONLY THAT.** It sources the flag DIRECTLY from the `box:` table of
the box-tier `box.yaml`; an absent file, an absent `box:` table or an absent key all give the
built-in `True`. What it answers is the question a MERGE STRUCTURALLY CANNOT — *which tier carried
this value* (`settings_launch.py`, in its own words: *"Which of them carried it is not knowable
here, because the snapshot is the MERGE of all of them"*).

Its five callers write a box's authored value: `paths.py` (×2, the create and initialize paths),
`commands/box/_lifecycle.py` (×2, feeding `ProjectState.box_authored_vault`) and
`commands/box/_duplicate.py` (×1, feeding `establish_standalone`). 🛑 **Do NOT give this a
workset-tier fallback and do NOT route it through the cascade** — either one pins an INHERITED
workset default as a box-scope override at the destination, the exact corruption
`carried_box_settings` exists to prevent.

⚑ The WORKSET-tier downward default (`box` ⊂ `workset`; spec §0 *Directional view/set across
CONTAINMENT levels*) — `workset create --no-vault` reaching contained boxes, the primary workset
defaulting its boxes like any other (spec §2c) — lives in `resolve_box_enable_vault`, where the
workset tier is one cascade level among four instead of a second hand-opened file.

Box identity derives entirely from the registries (`box_resolve`) — there is no on-disk `project:`
identity section — while `enable_vault` stays a plain box-settings read: the
two concerns are decoupled. Paired writer: :func:`write_box_enable_vault`.


```carried_box_settings(box_tier: Path) -> dict```
The box-scope settings a LIFECYCLE op carries from a source box.

`convert` / `move` / `duplicate` all make a NEW box that inherits the source's box-scope settings.
Those live in the source's BOX TIER, so that file's content is carried verbatim (including
non-`box:` sections such as agent config). Returns the DOC to write at the DESTINATION's box tier;
`{}` when the source carries nothing.

### The box tier, and nothing else

*"we should copy/persist only those elements that are within the box settings."* The function
reads ONE file.

A `box.*` key at the WORKSET tier is not the box's — it is an OVERRIDABLE DOWNWARD DEFAULT for the
boxes that workset contains (`resolve_box_enable_vault`, spec §0 *Directional view/set across
CONTAINMENT levels*). Persisting it into a destination's box tier would PIN it: an override at the
most authoritative scope in the bracket (`… < workset < box`), which later edits to the workset
could no longer reach, and which the arriving user never set. So it stays where it was authored.
A box that STAYS in the workset keeps resolving it through the cascade; a box that LEAVES stops —
because the value was the workset's, and that is what a downward default means.

⚑ Every lifecycle destination is a new scope in this sense. `convert`/`move`/`duplicate` to
standalone all run `establish_standalone`, which writes the destination ROOT fresh, so a value the
source authored at ITS workset tier does not reach the destination by any route. Pinned by
`test_a_workset_default_resolves_inside_and_is_never_persisted_on_the_way_out` and its three
siblings in `tests/test_commands/test_lifecycle_cmd.py`, plus
`test_duplicate_does_not_pin_a_root_stored_value_at_the_box_tier`
(`tests/test_commands/test_config_cmd.py`). The workset tier's `box:` subtree is never laid under
the box tier's.

### `workset:` is never carried

Workset-scope keys are the source's OWN identity (`workset.kuid`); the destination establishes its
own.

⚑ This is HYGIENE, not a hazard fix: a stray `workset.kuid` sitting in a BOX TIER is INERT,
because the kuid is read directly from the ROOT file, never resolved through the cascade — pinned
by `test_kuid_is_read_from_the_root_file_not_the_box_tier`
(`tests/test_settings/test_paths.py`) and verified experimentally.

⚑ The read is box-tier-only, so the strip is DEFENSIVE: a `workset:` section can only reach a
`box.yaml` by hand. It is kept because a hand-authored one should not travel, not because a code
path produces one.


```read_workset_kuid(path: Path) -> str```
The stored `workset.kuid` value at *path*, defaulting to the SENTINEL.

The reader for the settable `workset.kuid` key: it sources the kuid
DIRECTLY from the `workset:` table of a box's `workset.yaml` — for a STANDALONE box that single
file plays the WORKSET tier. An absent file / `workset:` table / key yields the reserved
:data:`kanibako.kuid.SENTINEL` (`"00000"`), the primary/named default and the "no real kuid yet"
marker.

⚑ THE LITERAL IS HERE, BUT IT IS NOT THE CARRIER. `settings_launch.workset_anchor_floor` emits
`kuid.SENTINEL` by reference, so the keyspace answers this key from the floor and this reader is
the PRE-SNAPSHOT route to the same value. A conformance case asserts the pair equal. See "The
pre-cascade readers" above.


```read_workset_skip_kuid_check(path: Path) -> bool```
The stored `workset.skip_kuid_check` bool at *path*, defaulting to `True`.

The reader for the settable key (spec default `true` — the advisory "invalid KUID" warning is
OPT-IN strictness, INVERTING the old D9). Sourced from the `workset:` table of a box's
`workset.yaml`. An absent file / table / key yields `True` (checking OFF).

⚑ SAME SHAPE AS `read_workset_kuid` ABOVE: `workset_anchor_floor` emits this key too, so the
literal here is the PRE-SNAPSHOT carrier and not the one the keyspace answers
from; the `reader-defaults` and `workset-anchor-floor` kinemata views hold each to the manifest
cell, so the two cannot part without a red.


```_split_config_key(flat_key: str) -> tuple[str, str]```
Split a flat config key into `(section, key)`.

* `"box_image"` → `("box", "image")`
* `"paths_dot_path"` → `("paths", "dot_path")`
* `"some_scalar"` → `("", "some_scalar")` (top-level scalar field)

A flat key with no recognized section prefix is a TOP-LEVEL scalar field; it returns an empty
section rather than raising. The typed writer in `config_interface` is the routed set/get/reset
path — this helper only serves the few remaining flat-key callers and must never crash on an
advertised key.


```unset_project_config_key(path: Path, flat_key: str) -> bool```
Remove a single key from a `box.yaml`; `True` if it was found and removed.

An emptied section is cleaned up rather than left as a bare `{}`.


```load_project_overrides(path: Path) -> dict[str, object]```
The project-level overrides in a `box.yaml` — flat_key → value for keys that differ from
defaults.


```read_agent_settings(path: Path, agent_name: str) -> dict[str, str]```
Read agent-keyed agent-state overrides from a config file's `agent` table.

Override sections are keyed per agent under `agent.<agent_name>`, layered over the reserved
any-agent `agent.default` tier (the agent-specific value wins WITHIN a single file). This stops an
override set while a box is on one agent (e.g. `model` under `agent.claude`) from bleeding onto
another agent after the box is switched (e.g. to `goose`).

The agent SELECTION is not here either — it is the request `pref.system.agent` (spec §2h).

`agent.default` is RESERVED as the any-agent default tier; no real agent may be named `default`.

**No pass-1 migration.** A legacy FLAT `[agent]` table (scalar values written directly under
`agent`, e.g. `agent.model`) is treated as UNSET — only nested per-agent dicts (`agent.default` /
`agent.<agent_name>`) are honored. Configs are hand-edited to the new shape. The common no-config
case (absent file, or absent/empty `agent` table) still returns `{}` unchanged.

```agent_settings_of(data: dict, agent_name: str) -> dict[str, str]```
`read_agent_settings` over a settings doc already in hand — the same read, one body. It exists for
`config_interface.show_config`, which hands in the file AS THE CASCADE READS IT
(`config_interface._noun_stored_view`, the `DISPLAY` read of
`settings_assemble.read_settings_files`): re-reading the path would see an `agent:` table that
directional enforcement drops (a box or workset file), and print its leaves as overrides.

Every row is produced by `config_display.flatten_under`, the ONE show walk, relative to
`agent.<node>.`: a scalar leaf is its bare row (`model = opus`), and a table-valued leaf is walked,
so a category map prints `caches./home/agent/c = uv` — destination normalized, entry `src` /
`src  [options]` — never the dict repr `caches = {'~/c/': ['uv']}`.


```read_system_agent(system_path: Path | None) -> str | None```
The stored `system.agent` SETTING from the system settings tier; `None` when unset or empty.

`system.agent` (spec §2g) is the CURRENT agent's name — a system-scope SETTINGS key (behavior, not
a config path), so it lives in the `system:` table of the system settings file `@config.settings` =
`@config.data/global/settings.yaml` (the `std.settings` path), exactly where `assemble_levels`
reads the system tier from. Callers pass that settings-file path as *system_path*, NOT
`~/.config/kanibako.cfg`, which holds only the bootstrap PATH tables.

`None` means "no system default" — the caller REFUSES rather than picking one (keyspec §2b).

⮕ A store still carrying the retired `system.default_agent` leaf (`MIGRATION.md` § *Settings keys
renamed or retired*) is REFUSED by name at the selection read (`settings_assemble`'s retired-key
check).

⚑ This is the PRE-CASCADE reader, kept for the one caller that needs the stored value before a
snapshot exists (`start`'s box-independent persona pre-flight). The
LAUNCH does not use it: agent selection resolves `system.agent` off the snapshot, prefs included
(:mod:`kanibako.settings.agent_select`).


```read_setup_completed(settings_path: Path | None) -> str | None```
The `system.setup_completed` marker from the SYSTEM SETTINGS file; `None` when absent or empty.

`system.setup_completed` is a host-global `system.*` value recording the build version at which
`kanibako setup` last succeeded (W1). `None` means "setup never run" — the gate then re-nudges.

⚑⚑ **ITS FILE IS `@config.settings` (`<data>/global/settings.yaml`)** — the same file
`read_system_agent` reads and the launch cascade's system tier assembles from, NOT `kanibako.cfg`
(*"there is no reason whatsoever that `system.setup_completed` should go in the config. It should
not. It should go in the global settings file."*). That is what spec §2g declares — a Layer-2
`system.*` SETTINGS key — while spec §1 gives Layer 1 the `config.*`
bootstrap paths ALONE. It is exactly one file: **there is no fallback read of the old location**, and
adding one would be the deprecation window this release refuses.

⚑ **A FRESH install has no settings file at all**, so the marker reads absent and the gate answers
its NON-BLOCKING nudge — never "already set up", and never a block. Measured on a real store: the
advisory goes to stderr and the command proceeds to its own outcome unchanged.

⚑ **Why a RAW reader is required.** The gate runs PRE-CASCADE, before any snapshot exists. A marker
left in `kanibako.cfg` REFUSES, as any `system:` table there does.

```read_system_helpers(settings_path: Path | None) -> dict[str, int]```
The helper-hub SPAWN BUDGET — the two declared `system.helpers.*` leaves (spec §2g), read out of the
`system: helpers:` table of a settings document. `SYSTEM_HELPERS_SECTION` is exported beside it and
the leaf set is `settings_keyspace.DECLARED_SYSTEM_HELPERS_LEAVES`: the one spelling of that slot,
shared with the per-child document `channels/helpers.py` writes. `system_helpers_leaf_int` is the
one per-leaf refusal, for this reader and the start's resolved budget.

🛑 **There is deliberately NO writer here.** The only thing that writes a budget is a parent handing
one to its child, and its target is `helpers/<N>/spawn.yaml` — a DELIVERY document mounted RO into
that helper, read back by explicit path, assembled by no cascade. It is written in
`channels/helpers.write_spawn_budget` with `dump_doc`, NOT through `config_io.write_nested_key`:
that seam is allowlisted precisely so a runtime-computed default cannot be persisted into a cascade
settings file, and a child's budget is runtime-computed. Putting the write here, next to a reader
whose other caller really is `@config.settings`, would invite exactly that.

⚑ **The budget is a DECLARED key**, read from settings documents; there is no bespoke `spawn:`
table (the §0 violation `system.templates_stamp` was retired for, next section).

⚑ **TWO TIERS, ONE READER, which is why the reader takes a PATH rather than resolving one.**
`kanibako box helper spawn` consults the budget a PARENT handed it (the RO document at
`~/spawn.yaml`, mounted into a helper by the hub) and then its own `@config.settings`. Both are
ordinary settings documents carrying the same two keys; a second shape would be a second keyspace.

🛑 **A non-numeric leaf is REFUSED BY NAME, never coerced.** A fabricated default would answer a
budget question with a number the user never wrote, and the set-time type guard
(`config_keys.KEY_TYPES`) cannot reach a hand-edited file.

⚑ **Why a RAW reader is required.** The spawn verb decides whether it may spawn at all, IN BOX,
before any snapshot exists — the same timing argument the readers above make.


```system_settings_path() -> Path```
THE system SETTINGS file (`@config.settings`), resolved from Layer 1 alone.

⚑ Deliberately NOT `paths.load_std_paths`: that one MATERIALIZES the store and RAISES when no
Layer-1 file exists, neither of which a pre-cascade reader may do to a box that has never been set
up. The returned path need not exist — every reader here treats an absent file as "unset", which is
exactly what a fresh install is.

⮕ It is the shared spelling of a derivation `commands/setup_cmd.py` and `commands/system_cmd.py`
still carry their own (materializing) copies of; consolidating those is a separate change.

### The retired template-stamp gate

⚑ There is no template-stamp gate (R-38): `system.templates_stamp` is not a declared key, and a
hard gate on a packaged-content digest would false-block hosts whose only sin is a digest the
config never recorded.

The protection folds into :func:`setup_compat_gate`: a template CONTENT change ⇒ `SETUP_FCV` bump
(nudge), a STRUCTURAL/breaking one ⇒ `SETUP_BCV` bump (hard block), with a CI check comparing the
packaged-template digest against the previous tag to REQUIRE the bump.

ACCEPTED LOSS (ruled): drift WITHIN one version — a dev build, or a plugin pip-installed after
first run — is not detected; the cure is `kanibako setup`.

🛑 A stored `[system] templates_stamp` leaf REFUSES, and not as a special case: it is a `system:`
table in the Layer-1 file, which that file may not carry at all, so the read names it like any
other stale settings key. The cure is the hand-edit `MIGRATION.md` § "Anything in `kanibako.cfg`
that is not a declared `config.*` key stops the command" prescribes.


```setup_compat_gate(settings_path: Path | None, legacy_config: Path | None = None) -> str | None```
Run the 5-band setup/config compatibility gate against the marker stored in *settings_path*.

⚑ *settings_path* is the SYSTEM SETTINGS file (`@config.settings`), the marker's home — see
`read_setup_completed`. ⚑ *legacy_config* is a ONE-RELEASE MIGRATION GUARD (v1.8.0): with no marker
in *settings_path*, an existing v1.7-era *legacy_config* (`kanibako_config.yaml`) BLOCKS until it is
renamed and setup runs; only its existence is read. `cli._setup_nudge` resolves *settings_path* with
`paths.load_system_config` (deliberately NOT `load_std_paths`, which MATERIALIZES the store — a
non-blocking advisory must not create directories).

Compares the recorded `system.setup_completed` marker (ConfigVer) against the running build
(CurrentVer = `__version__`) and the two build constants `SETUP_BCV` / `SETUP_FCV`. All comparisons
are by BASE version (PEP 440 `packaging.version.Version` — the project's own versions, e.g.
`1.6.0.dev25` / `1.6.0-rc1`, are PEP 440), so a dev/rc build of the same base as the released
marker reads as `==`, not "from the future".

The bands (design `~/canon/notebook/archives/plans/2026-06-23-setup-version-tiers-NEXT.md`):

* `ConfigVer > CurrentVer` → **raise** :class:`~kanibako.errors.ConfigError` (config from a NEWER
  build than is running).
* `ConfigVer == CurrentVer` → `None` (fully current; no message).
* `FCV <= ConfigVer < CurrentVer` → **silently bump** the marker forward to CurrentVer ONCE (via
  `config_interface.write_system_value`), return `None`. This is the FORWARD-COMPATIBLE band —
  `SETUP_FCV` is by definition "the oldest version whose setup is COMPLETELY compatible (nothing
  new since)" (`kanibako/__init__.py`), which is what makes a silent advance safe rather than a
  papering-over. A failed bump write (e.g. read-only config) is swallowed so the gate never blocks
  a command.
* `BCV <= ConfigVer < FCV` → return the NUDGE string (non-blocking; re-run `kanibako setup`).
* `ConfigVer < BCV` → **raise** :class:`~kanibako.errors.ConfigError` (too old to auto-fill; must
  re-run `kanibako setup`).
* absent marker → return the first-run nudge (or raise, for a *legacy_config* that exists).
* unparseable marker → `None` (a hand-edited value; assume the user knows what they are doing —
  don't nag and don't block).

The two `raise` bands are the only blocking outcomes; the CLI surfaces them as rc1. Returning a
string is a NON-BLOCKING advisory the caller prints to stderr before continuing.


```resolve_agent(*, explicit_agent: str | None, requested: str | None = None) -> str```
Validate the effective agent name against the installed set, or REFUSE.

⮕ **The CASCADE is not here.** `system.agent` and the `pref.system.agent` requests of the
workset/box files are resolved off the launch snapshot by
:func:`kanibako.settings.agent_select.select_agent`, which passes the winner here as *requested*.
What stays here is what is NOT a key: name VALIDATION against the installed set and persona-ref
canonicalization.

Precedence: *explicit_agent* (the §1A CLI level) > *requested* (whatever the settings cascade
resolved). The FIRST **PRESENT** one resolves a name — never the first non-EMPTY one. `None` is
the only spelling of absence either argument has (no `--agent` at all; `__MISSING__` at the
selection key), so a present `""` is a VALUE: spec §2h keeps `present-None`, terminal `""` (**≠
unset**) and the COPY-disable sentinel apart as three idioms. A blank tier therefore reaches
`parse_agent_address` and is refused as an empty ref, instead of falling through to the next
tier (a typed `--agent ""` silently taking the cascade's agent).

A resolved name is validated against the installed set — the keys of `targets.discover_targets`,
i.e. the DISCOVERED PLUGINS:

* installed → return it;
* not installed → raise :class:`~kanibako.errors.AgentNotInstalledError` (actionable: names the
  agent + how to install it).

Nothing resolved → raise :class:`~kanibako.errors.AgentUnsetError`, naming `kanibako setup`.

🛑 **THE INSTALLED COUNT IS NOT CONSULTED HERE, AND THE SET IS NOT EVEN IN SCOPE.** Nothing
auto-picks an agent; `discover_targets` is called INSIDE the named-ref branch above, so a count
rule on this path would mean
re-adding that call where a reader can see it (P3/P4). The sibling state — present-`None`, *no
default set* — never reaches this function at all: `select_agent` raises
:class:`~kanibako.errors.AgentNoDefaultError` for it, because the two states must print different
sentences.

### Canonicalization before validation

Each ref source may be a persona ref (`persona+harness`). The winning tier is canonicalized to its
node-name (`persona℘harness`; a bare ref stays byte-identical) so callers see a uniform node-name,
and the same call VALIDATES the ref shape (raises `ConfigError` on a malformed segment).

⚑ The HARNESS — right of `℘`, the whole name when bare — is what must be an installed target, NOT
the composite node-name: a persona's name segment is free-form.

⚑ The parse is `agent_ref.parse_agent_address`, the ADDRESS grammar: a ref naming the shell
pseudo-agent (any case) returns the `shell` node WITHOUT consulting the installed set — it is the
built-in occupying its own slot (`[R175]`), selectable by name (keyspec §2b). `default` still
refuses as a reserved name. The grammar owns the `shell` fold, so `box create`'s `--agent`
validation cannot disagree with it.

⚑ A named harness validates against the FULL `installed` set. The keyspec's pseudo-agents
(`default`, `shell` — `agent_ref.PSEUDO_AGENT_NAMES`, the RESERVED names, spec §2d) are the only
pseudo-agents. `general` is NOT a target any distribution registers — the agent-less slot is
`shell` (`GENERAL_SLOT`) — so `--agent general` raises `AgentNotInstalledError`.

⚑ The imports inside the function are lazy on purpose: `kanibako.targets` imports `paths` / this
module indirectly, so importing it at module scope risks a cycle. This mirrors `discover_targets`'
use elsewhere.


```_flatten_leaves(data: dict, prefix: str = "", *, with_nodes: bool = False) -> dict[str, object]```
Flatten nested dict into DOTTED-key form, each leaf AS STORED. ⚑ THE ONE WALK: `_flatten_dotted`
below is this with its leaves stringified, so the keys one names are the keys the other names. The
raw leaf serves the SETTINGS files' `config:` refusal and its stored view
(`settings_assemble.stored_config_entries`), which read that table exactly as the Layer-1 read
would and DISPLAY the values — `str()` would print a stored `null` as `None`, a spelling the file
never held. ⚑ *with_nodes* also reports each `dict` position as its own entry before descending,
so a map is visible at its own key.

```_flatten_dotted(data: dict, prefix: str = "") -> dict[str, str]```
Flatten nested dict into DOTTED-key form, stringifying scalar leaves (`_flatten_leaves`, the walk).

`{"system": {"bindings": {"rw": {"foo": "h:g"}}}}` → `{"system.bindings.rw.foo": "h:g"}`.

⚑ Despite the illustrative example, this is NOT a scope-category helper. Its callers are the
Layer-1 `config:` read, the Layer-2 `system:` path-tier read, and the Layer-1 refusal that names its
keys. The scope categories
live in `settings_categories` / `settings_keyspace`, and their keys are TERMINAL — a destination is
DATA, not a key segment — so nothing here flattens one.

⚑ A `null` leaf becomes the string `"None"`, so both path reads call `_refuse_null_paths` first.

⚑ **`str(k)` ON THE UNPREFIXED ARM (in `_flatten_leaves`): a YAML key need not be a string.** A
top-level `1:` / `true:` / `~:` would otherwise hand an `int`/`bool`/`None` to
`_layer1_settings_keys`, which sorts and joins — a `TypeError` traceback in the one file whose
whole purpose is that a hand-editing user finds out.

```_refuse_null_paths(path: Path, table: dict, prefix: str, path_keys: Iterable[str]) -> None```
Refuse a `null` at any of *path_keys* in *table*, naming *path* and the keys. It raises through
`null_path_keys_error`, with membership from `refuses_null_path_key` — the same set the `set` door
asks. Callers: `bootstrap_config_paths` (`CONFIG_PATH_DEFAULTS`) and `system_path_set_values`
(`SYSTEM_PATH_DEFAULTS`).
