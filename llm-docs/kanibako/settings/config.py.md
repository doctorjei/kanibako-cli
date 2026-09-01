# The Bootstrap Config File — loading, writing, and the flat merged object

This module owns `kanibako_config.yaml` and the two flat dataclasses either side of it —
`BootstrapConfig` (Layer 1, what that file holds) and `KanibakoConfig` (Layer 2, the merged box
scalars) — plus the
YAML read/write primitives, the built-in defaults, the layer-overlay merge, and a handful of
DIRECT readers (`workset.kuid`, `workset.skip_kuid_check`, `system.agent`,
`system.setup_completed`, and — for the AUTHORING TIER only — `box.enable_vault`) that must answer
BEFORE a cascade snapshot exists. It also carries two
arbiters that are not config reads at all and live here only because their inputs do:
`setup_compat_gate` and `resolve_agent`.

⚑ **`system.agent` and `system.setup_completed` read the SYSTEM SETTINGS file, not
`kanibako_config.yaml`** — they live in this module for their pre-cascade TIMING, not for their
file. The marker joined its sibling on 2026-08-26; see `read_setup_completed`.

It is the OLDEST tier in the settings stack and the most nearly-retired. Almost everything a box
resolves at launch goes through the keyspace cascade, not through here; what remains is (a) the
bootstrap PATH tables the cascade itself needs in order to locate its files, and (b) the
pre-cascade readers, each of which documents why it cannot wait for a snapshot.

Authority: spec `settings-keyspace-1.8.0.md` §0 (closed keyspace), §1/§1A (the Layer-1 `config.*`
foundation + the CLI level), §2b (`box.agent_name` retired), §2c (the box/workset settings-file
derivation), §2g (the Layer-2 `system.*` path settings + `system.agent`), §2h (`pref.*` requests).

## The two file sets, and why they are not the same thing

Two DIFFERENT file families are read here and confusing them is the standing hazard:

* **The CONFIG (bootstrap PATH) set** — `/etc/kanibako/config_base.yaml` < the user's
  `$XDG_CONFIG_HOME/kanibako_config.yaml`. It carries the Layer-1 `[config]` foundation keys and
  NOTHING ELSE, and it is what tells everything else where the settings files LIVE. Read by
  `load_config` into `BootstrapConfig.config_paths`.
  ⚑⚑ **THE RULE IS IN THE READ's SHAPE since 2026-08-31 (Jei), and this is the passage most
  likely to be remembered wrong.** It was a `config.`-PREFIX FILTER applied at each of the four
  Layer-1 read sites, over a `KanibakoConfig` that also carried the box scalars — `load_config`
  being a GENERAL document reader that read the settings file too. So a Layer-1 read still
  RETURNED settings (`load_config(<file with a box: table>).box_image` was the file's value) and
  the filter dropped the rest in silence. Now `bootstrap_config_paths` walks IN through the
  `config:` table, so `config.`-prefixed is all it can produce, and its return type has nowhere to
  put a settings value. A settings table in that file is REFUSED, naming the file and the keys
  (`paths_defaults.ERR_CONFIG_LAYER1_SETTINGS`). The settings file's own `system:` path table has
  its own reader, `system_path_set_values`.
* **The SETTINGS (behavior) set** — `/etc/kanibako/settings_base.yaml` < `global/settings.yaml` <
  the agent file < the workset tier < the box tier. This is the real 6-level cascade; nothing in
  this module resolves it except `_resolve_box_scalars`, which delegates.

`config_base_path` and `settings_base_path` are the two `/etc` floors, one per family. A missing
file is treated as an empty level in both cases, so absence preserves current behavior.

## The `None` sentinel — absent is not the same as present-`None`

Throughout the scalar-overlay path a value of `None` (YAML `null` / `~` / a bare `foo:`) is
PRESERVED as the "reset to built-in default" sentinel; an ABSENT key simply does not appear in the
returned mapping. Every overlay step distinguishes the two: present-`None` resets the field to its
built-in default, absent leaves the underlying value untouched, and any other present value
(INCLUDING `""`) sets it. Collapsing the two would make a deliberate reset indistinguishable from
a key nobody wrote.

## B6 — the box scalars are KEYSPACE-resolved

`box.image` / `box.share_images` / `box.shell` / `box.enable_vault` (`_BOX_SCALAR_FIELDS`) are no
longer the flat
overlay's product. `load_merged_config` runs `_resolve_box_scalars`, which builds a real cascade
snapshot and overwrites the flat fields with the resolved values. Every caller — the launch,
`kanibako shell` (agent-less), and the box-less sites (`rig` / `diagnose` / `setup` / `baseline`,
which pass no project) — reads the SAME resolve through the SAME fields, so there is ONE live
source.

The flat overlay walk still runs underneath it. It owns the corner semantics the resolve falls
back to (present-`None` reset; `""`). ⚑ It no longer owns `paths_project_toml`: `paths.project_toml`
is not a declared key (spec §0), no caller ever read it, and the declared-key walk described under
`_present_scalar_fields` cannot reach it — so the field went with the read that produced it.

⚑⚑ **`load_merged_config` NO LONGER READS SETTINGS OUT OF THE LAYER-1 FILE.** *"kanibako_config.yaml
<-- cannot have settings. Period."* — that file was the least-specific FILE source of the scalar
overlay, and its `[box]` table overrode the declared defaults; the scalars now START at those
defaults and the first thing that can move them is the WORKSET tier. Stopping the WRITE alone would
not have been enough: a hand-written table, or one left by an older build, would have gone on
silently overriding the defaults. ⚑ *global_path* is still a parameter and is not dead — it is what
`_resolve_box_scalars` locates the SYSTEM tier from — but the `config_paths` field this function
used to fill from it is gone: a settings object never carried Layer 1 legitimately.

### The floor, and why it is captured before the overlays

⚑⚑ **THE FLOOR IS THE DECLARED DEFAULTS, AND IT USED TO BE THE LAYER-1 FILE'S `[box]` TABLE.**
That table was written at init on every install, the settings cascade did not read it, and its
values would therefore have been STRANDED (consumer-map risk 1) — so they were mapped in as the
resolve's floor. Jei's 2026-08-26 ruling took settings out of that file entirely, so there is
nothing left to strand, and `config.box_scalar_defaults_floor()` builds the floor from
`KanibakoConfig`'s own field defaults instead.

🛑 **The floor was SEPARATED from the file read, not deleted.** The old expression
(`getattr(load_config(cf), field)`) fused two things: the file's value when the file spoke, the
declared default when it did not. Only the first is the violation; deleting both would make
`@box.image` dangle at launch AND at set time. `box_scalar_defaults_floor` is the ONE recipe, shared
with `config_interface._category_set_lookups` so the launch floor and the set-time floor cannot
drift. A `""` default (`box.shell`) is dropped as a SUPPRESSION — `build_launch_snapshot`'s own rule
(`if val == "": continue`) — so an unset `@box.shell` still refuses BY NAME rather than resolving to
blank.

⚑ The floor is the DECLARED DEFAULTS and is built before any overlay, so a workset or box value
cannot masquerade as the system-stored default — it enters the resolve at its OWN tier instead.
`""` entries drop out of the fold (absent ≡ no default) and the flat fallback then applies the
built-in default, preserving the `""` corner byte-identically.

A `box:` table in `global/settings.yaml` — where `kanibako system set box.image=…` has always
written — now resolves too. It was silently stranded before B6.

### The old machine-wide third file is DELETED

`/etc/kanibako/kanibako.yaml` is gone (spec §2). The admin authority is exactly the
`config_base.yaml` / `settings_base.yaml` base tiers, resolved on the PATH side; this scalar
loader starts from the built-in defaults.

## The pre-cascade readers, and the rule that no longer follows from it

`read_box_enable_vault`, `read_workset_kuid`, `read_workset_skip_kuid_check` and
`read_system_agent` all read a DECLARED key DIRECTLY out of a settings file rather than through
the resolver, because each has a caller that runs before a snapshot exists. That much is still
true, and still the reason these functions exist.

⚑⚑ **WHAT DOES NOT FOLLOW FROM IT — AND USED TO BE WRITTEN HERE AS IF IT DID — IS *"the DEFAULT
lives in the reader, not in a cascade floor"*.** The premise ("a caller runs before a snapshot
exists") is about a FULL launch snapshot. A NARROW resolve needs only FILE PATHS, and every one of
these callers already computes them a line or two above the read, so the conclusion was never
licensed by the premise. Its cost was paid twice:

* the declared default reached NO cascade floor, so a whole-value `@`-reference to the key resolved
  to `__MISSING__` in every launch snapshot; and
* the reader defined the resolution ORDER by which files it happened to open, so tiers it did not
  open were silently dropped — a `kanibako system set box.enable_vault=false` returned 0, persisted,
  and was then ignored by every box.

The pattern is **RETIRED** (user ruling, 2026-08-29: *"If you are asking if we should avoid a carve
out, the answer is yes"*). Its four members left one at a time: `workset.skip_kuid_check` and
`workset.kuid` to `settings_launch.workset_anchor_floor`, and `box.enable_vault` to
`_BOX_SCALAR_FIELDS` / `box_scalar_defaults_floor`. 🛑 **A reader keeping a literal default is not
the same thing as a reader OWNING it** — `read_workset_skip_kuid_check` still returns `True` and
`read_box_enable_vault` still returns `True`, and in both cases a conformance case in
`tests/test_settings/test_manifest_conformance.py` asserts the reader and the floor equal so the
two carriers cannot drift.

⚑ `read_box_enable_vault` did not become redundant; it became NARROWER. Its question is now *which
TIER authored this value*, which a merge structurally cannot answer, and which lifecycle ops need
so they never pin an inherited workset default as a box-scope override at a destination.

## Functions

```coerce_bool(value: object) -> bool | None```
Coerce a config value to a real bool using the shared truth table.

Returns the bool, or `None` if *value* is not a recognized bool literal. Already-bool values pass
through. The truth tables (`_BOOL_TRUE` / `_BOOL_FALSE`) are shared by the typed `config set`
writer (`config_interface`) AND the box.meta writer so both round-trip identically.


```class BootstrapConfig```
The Layer-1 bootstrap file's WHOLE content: the `config.*` foundation, and nothing else.

⚑⚑ **THE TYPE IS THE RULE (P3/P4).** It has no settings field, so no filter can be needed and none
exists — the code that used to enforce *"`kanibako_config.yaml` cannot have settings"* at four call
sites is DELETED rather than moved. `load_config` returns this; `load_std_paths`, `resolve_project`
and every pass-through signature down through `commands/box/_lifecycle.py` and
`launch/box_resolve.py` are annotated with it, so a `KanibakoConfig` handed to one of them is a
type error rather than a value that silently carries the wrong layer.


```class KanibakoConfig```
The flat merged SETTINGS object.

Precedence over the FILE layers, least → most authoritative: hardcoded defaults < the workset
tier < the box tier < CLI overrides. ⚑ The
four `box.*` scalars do NOT resolve this way any more — see "B6" above; `load_merged_config`
overwrites them from the keyspace after the overlay walk.

⚑ `box_enable_vault` JOINED 2026-08-29 as the fourth. Its field default is now THE carrier of
`box.enable_vault`'s declared default — that is what `box_scalar_defaults_floor` publishes, and
what makes `box show --effective` grow a row for the key (the display iterates `fields(cfg)`, so
there was no row at all while the default lived inside a reader).

⚑ `box_agent_name` is GONE (P7, spec §2b). `box.agent_name` is RETIRED and a box selects its agent
with the REQUEST `pref.system.agent` (§2h), resolved off the launch snapshot by
:mod:`kanibako.settings.agent_select`. There is no flat-scalar agent field any more — the
selection is a KEY.

⚑ **THERE IS NO `config_paths` FIELD HERE** (2026-08-31). It used to hold the bootstrap PATH
set-values — the Layer-1 `config.<leaf>` foundation AND the Layer-2 `system.<leaf>` path settings,
merged into one set — which is exactly how one read came to answer two layers' questions. It is
`BootstrapConfig.config_paths` now, and it carries `config.*` alone.

⚑ `BOX_META_FILE` (`"box.yaml"`) is the per-box construct-time metadata + box-tier settings
cascade file (spec §2c, `meta.box.*`).


```_scalar_value(value: object) -> object```
A settings-file scalar as the flat object carries it.

Booleans are preserved (`str(False)` is the truthy `"False"`); `None` is preserved as the reset
sentinel (see "The `None` sentinel" above); other scalars are stringified.

⚑ It replaced `_flatten_toml`, which flattened a whole document into underscore-joined names
(`{"paths": {"boxes": "x"}}` → `{"paths_boxes": "x"}`) — the namespace that collided with the
`KanibakoConfig` field names. Only the per-leaf coercion survived the change; see
`_present_scalar_fields`.


```config_file_path(config_home: Path) -> Path```
The bootstrap config file `$XDG_CONFIG_HOME/kanibako_config.yaml`.

CLEAN BREAK (JC-1): the old `kanibako.yaml` name is NOT read-compat (pre-release; Jei's own data).


```config_base_path() -> Path```
The machine-wide CONFIG base file (`/etc/kanibako/config_base.yaml`).

The least-specific layer of the bootstrap-PATH file set: a site admin supplies overridable
defaults that the user's `~/.config/kanibako_config.yaml` can still beat. Missing file → treated
as an empty level.


```settings_base_path() -> Path```
The machine-wide SETTINGS base file (`/etc/kanibako/settings_base.yaml`).

The LEAST-specific (bottom) layer of the SETTINGS (behavior) cascade — below every scope
(`system` / `agent` / `workset` / `box`): a site admin supplies overridable behavior defaults that
any scope can still beat. Missing file → treated as an empty level, so its absence preserves
current behavior.


```_present_scalar_fields(path: Path) -> dict[str, object]```
The DECLARED box scalars PRESENT in a SETTINGS file, as field-name → value.

`None` is preserved as the reset sentinel; callers must distinguish it from an absent key (which
simply will not appear in the returned dict).

⚑⚑ **IT WALKS IN THROUGH `_BOX_SCALAR_FIELDS`' DOTTED SPELLINGS, and that is the closed-keyspace
half of the 2026-08-31 change.** It used to flatten the whole document into underscore-joined names
and keep whichever matched a `KanibakoConfig` FIELD name — a namespace that COLLIDES with those
names, so an undeclared top-level `box_image:` resolved identically to the declared `box: image:`
(spec §0: an undeclared key is not a key). Reading in through the declared spelling makes the flat
one UNREACHABLE rather than refused by a list (P4), and it is also why the `[config]`/`[system]`
pops are gone: a table the walk never enters cannot leak.


```_layer1_settings_keys(data: dict) -> list[str]```
Every SETTINGS entry a Layer-1 document carries, dotted and sorted; empty ⇒ the file is clean.

The message's key list. ⚑⚑ **A TABLE WITH NO LEAF IS NAMED BY ITS TABLE NAME**, which is what the
`or [name]` fallback is for. The three empty spellings a user reads as identical — `box:` with
nothing under it (**YAML parses that to `None`, not `{}`**), an explicit `box: {}`, and a `box:`
whose only leaf is itself an empty table — used to give TWO different answers: the first was
refused as a bare `box`, the other two were silently ACCEPTED. Convention 0 forbids that pair, and
the silent arm was the only thing in this rule that behaved like a carve-out. All three are
settings tables that do not belong in this file, so all three refuse.


```bootstrap_config_paths(path: Path) -> dict[str, str]```
The Layer-1 file's `config.*` foundation, read from its `config:` table ALONE.

⚑ **NO FILTER, AND THAT IS THE POINT (P4).** The walk STARTS at the `config:` table, so a `config.`
prefix is the only thing it can produce; the rule is in the shape of the read rather than in a test
applied after it. 🛑 A settings table here RAISES `ConfigError` naming the file and the keys — it is
not dropped. That refusal is what a user with a stale `[box]` or `[system]` table now sees instead
of silently running something other than what their file says.

⚑ The extraction inside `config:` is unfiltered by leaf name: an unrecognised `config.<leaf>` lands
in the set under its dotted name. Nothing downstream consults it by iteration
(`resolve_system_paths` walks `CONFIG_PATH_DEFAULTS`, never the file's set-values), so an unknown
`config.*` leaf is orphaned-ignored. ⚑ A stale `[system] templates_stamp` or `[system]
setup_completed` is NOT in that band — it is a `system:` table, so it refuses; see "The retired
template-stamp gate" below.


```system_path_set_values(settings_path: Path) -> dict[str, str]```
A SETTINGS file's `system.*` set-values, dotted — the Layer-2 half of the path tier.

Its own reader since 2026-08-31. This was `load_config(path).config_paths`, the very call the
LAYER-1 read used, over one field that held `config.*` and `system.*` together — one function
answering two layers' questions is what let each layer's file speak for the other. Nested sub-keys
(e.g. `system.channels.common`) become dotted keys. ⚑ NOT filtered to the path tier: that is
`paths.load_system_config`'s own P13 job, and this file's `system:` table legitimately holds
`system.agent` and the category families too.


```load_config(path: Path) -> BootstrapConfig```
Read the LAYER-1 bootstrap file — the one reader of `kanibako_config.yaml`.

⚑⚑ **IT RETURNS A `BootstrapConfig`, AND THAT IS THE WHOLE OF THE 2026-08-31 RULING:** a Layer-1
read has no settings field to return. It was a GENERAL document reader — the same call read the
settings file — which is how the Layer-1 file came to hand back a `box.image` it may not carry. The
box scalars are read from SETTINGS files by `load_merged_config`; a settings file's `system.*` path
set-values by `system_path_set_values`.


```_resolve_box_scalars(global_path, *, workset_path, box_path, cli_overrides) -> dict[str, object]```
Resolve the box scalars (:data:`_BOX_SCALAR_FIELDS`) through the KEYSPACE.

The ONE resolve behind `load_merged_config` (B6, option (b)). A focused, AGENT-LESS
`build_launch_snapshot` — the `"general"` slot, the proven `_effective_bootstrap` shape, so
`kanibako shell` and every box-less caller resolve without an agent — over the real cascade files:

```
floor(declared box-scalar defaults) < /etc settings_base.yaml < system
(global/settings.yaml) < workset < box < CLI level
```

⚑ **THE FLOOR WAS `kanibako_config.yaml`'s `[box]` TABLE UNTIL 2026-08-26**, when Jei ruled that
file cannot carry settings at all. It is `config.box_scalar_defaults_floor()` now — the DECLARED
defaults, shared with `config_interface._category_set_lookups` so the launch floor and the set-time
floor cannot drift. Nothing else about the chain moved.

The floor really does sit UNDER the `/etc` base file: `assemble_levels` folds *floor* beneath the
base file's content within the single `base` level, so a base-FILE set-value beats the floor at the
same key and the floor is the ultimate fallback.

The CLI flags ride the §1A LEVEL: *cli_overrides* (flat field names, the historical transport) are
translated through the ONE builder
:func:`~kanibako.settings.settings_cli_level.build_cli_level` and guarded inside
`build_launch_snapshot` — not overlaid ad hoc.

Returns `{dotted key: resolved leaf}` with ABSENT keys omitted; the caller falls back to the flat
value, which owns the `None`-reset / built-in default corner semantics.

⚑ **The system settings path is resolved with `load_system_config`, deliberately NOT
`load_std_paths`** (which materializes the store). Not literally mkdir-free, though: with
`XDG_RUNTIME_DIR` unset, `resolve_system_paths`' fallback CREATES its replacement runtime dir
(`paths._fallback_runtime_dir`, once per process, cached) — the single directory this call can
make.

⚑ **NO PERSONA TIER, deliberately** (the six-call-site audit). This resolve is AGENT-LESS by
construction — it runs in the `"general"` slot, for box-less callers with no agent selected — and
it reads back exactly the three `box.*` scalars in `_BOX_SCALAR_FIELDS`. A persona bundle spells
only `agent.<node>.*` leaves (`endpoint` / `model` / `secret_path.<VAR>` / `env.<VAR>`), so it
could not touch a `box.*` scalar even if an agent were known here. Nothing to thread, and no seam
to thread it from.

⚑ **The imports are lazy throughout, and must stay that way.** `paths` imports this module at
module load, and `settings_assemble` does too — which is how `settings_launch` reaches it
transitively. Hoisting any of these to module scope closes the cycle.

⚑ `_BOX_SCALAR_FIELDS` maps the box-scope SCALAR keys the merged loader resolves through the
KEYSPACE (B6, R-11a(a)): dotted key → the flat `KanibakoConfig` field it lands on. `box.shell`
rides the same resolve — it lives on the same object and the same `box:` tables (consumer-map
risk 4). `box.enable_vault` joined 2026-08-29; see "The pre-cascade readers" above for why it was
not there from the start and what that cost.


```load_merged_config(global_path, project_path=None, *, workset_path=None, cli_overrides=None) -> KanibakoConfig```
Load global config, overlay workset, then project, then CLI overrides — then run the B6 resolve.

Start from the user global config (the least-specific FILE source now that the machine third file
is deleted), then overlay the workset and project layers so the most-specific PRESENT value wins.
Finally the keyspace resolve for the box scalars: a resolved value wins; an ABSENT resolve
keeps the flat value. See "B6" above for the whole shape, and "The old machine-wide third file"
for what was removed.

The nested `_overlay_scalars` applies one file layer's PRESENT scalar/bool fields. Presence-based,
per "The `None` sentinel" above. Layer 1 cannot appear there at all: `_present_scalar_fields` walks
in through the declared `box.*` spellings and never enters a `config:` table.

⚑ Each resolved value lands on its field's own type via `_typed_box_scalar`: a field whose
DATACLASS DEFAULT is a bool goes through :func:`coerce_bool` (falling back to `bool(value)` for an
unrecognized literal), everything else is stringified. ⚑ The bool arm is selected off the default's
TYPE rather than a hand-kept name list, because `box.enable_vault` joining as the second bool
(2026-08-29) is exactly the edit that would otherwise ship a stringified `"False"` — which is
truthy, i.e. a stored `enable_vault: false` silently reading as ON.


```write_global_config(path: Path) -> None```
Create the bootstrap config file EMPTY — it may carry `config.*` and nothing else.

⚑⚑ **THE FILE CANNOT HAVE SETTINGS** (Jei, 2026-08-26: *"kanibako_config.yaml <-- cannot have
settings. Period."* — the general form of the `system.setup_completed` ruling of the same day). It
is created **EMPTY — zero bytes**, always.

Until then it wrote THREE tables:

* `[config]` — the six Layer-1 foundation keys (spec §1), a VERBATIM copy of
  `paths_defaults.CONFIG_PATH_DEFAULTS`;
* `[system]` — six of the eleven Layer-2 `system.*` path SETTINGS (spec §2g), a verbatim copy of
  that slice of `paths_defaults.SYSTEM_PATH_DEFAULTS`;
* `[box]` — `image` and `share_images` at their own `KanibakoConfig` field defaults.

The first was Layer-1's own content at its own default. The other two were **SETTINGS** (spec §2g /
§2b) sitting in the Layer-1 file, which is the thing the ruling forbids outright.

⚑ **THERE IS NO `cfg` PARAMETER ANY MORE, and that is the ruling in the signature.** A
`KanibakoConfig` *is* settings, so there is nothing it could legitimately contribute here; keeping
it and ignoring it would be a silent no-op for every caller that passed one. A non-default
`box.image` belongs in a SETTINGS file — which is where `kanibako system set box.image=…` has
always written it.

⚑ **WHY DROPPING THEM MOVES NO RESOLVED VALUE.** `paths.resolve_config_paths` /
`resolve_system_paths` take those two tables as their `LevelView(defaults=…)` and layer STORED
values OVER them, so a file storing exactly the defaults was a fourth carrier that changed nothing
— while making every default edit need a matching edit here, in lock-step, BY HAND. Measured
against two real stores: the old file (568 bytes) and the new one (0 bytes) resolve to a
byte-identical 21-key system path map, and to identical flat scalars.
`tests/test_settings/test_config.py::test_sparse_file_resolves_identically_to_the_old_verbatim_defaults`
is the standing pin. This generalises the rule spec `:868` already stated for one key,
`box.enable_vault`.

⚑ **THE FILE IS STILL CREATED.** `cli._ensure_initialized` uses its EXISTENCE as the "already
initialized" test, so an absent file would re-run first-run init — packaged-template install and
all — on every command forever. **Zero bytes, not `{}`**: this file is the hand-edit surface the
`config.*` refusal sends users to (`config_keys._config_key_refusal`), and a leading `{}` makes an
appended `config:` block a YAML error. It goes through the same atomic writer `dump_doc` delegates
to, so the create is atomic either way.

⚑ **NO `agent_name` row** (P7): `box.agent_name` is RETIRED (§2b), and writing a BOX key into the
CONFIG file was wrong even while it existed — nothing ever read it back from here. Stale copies in
existing `kanibako_config.yaml` files are documentation-only (migration M-4).


```write_project_config(path: Path, image: str) -> None```
Write or update a `box.yaml` with the given image.


```persist_creation_flags(box_settings_path, *, materializing, image=None, share_images=None) -> None```
The §1A **CREATE EXCEPTION** — the ONE gate through which a shadowing CLI flag's value ever
PERSISTS.

R-11a; materialization ruling 2026-08-02. Spec §1A: a flag applies to ONE launch and NEVER mutates
an EXISTING stored value — *"at box CREATION only, a shadowing flag's value PERSISTS — it
INITIALIZES the box's stored config."*

Launch-MATERIALIZATION counts as creation (Jei, 2026-08-02): the one signal is *materializing* —
"is this box being materialized by THIS invocation?" — which `kanibako create` and the launch path
both read off their resolve's `proj.is_new`. Every caller routes through THIS gate; there is no
per-path persist logic (the former `start._persist_image_override` and its deferred-arm replay
collapsed into it), so `create`, the first launch of a `workset connect`-ed box (connect registers
the box and creates its dir but never seeds, so that launch IS the materialization), and a plain
`start --image` on an EXISTING box (strictly ephemeral) all get the rule from one place.

⚑ A launch that rebuilt a REGISTERED box whose directory had been deleted used to reach here too;
MBR-6 refuses that case at the launch gate now — it is a repair, not a creation.

Only EXPLICITLY-GIVEN flag values persist: an absent flag (`None`; `""` for *image* — absent ≠
`""`) writes NOTHING, so a no-flag create bakes NO default into the box tier and the box resolves
the live cascade (single source of truth; the stored default stays at its own tier). No flags → no
write at all — no empty `box.yaml` is materialized (the :func:`write_box_enable_vault` rule).

*box_settings_path* is the BOX-TIER settings file from `box_workset_settings_paths` — the same file
`box set box.image=…` writes and the launch cascade reads as the box tier (M-8). *share_images* is
a real bool or `None` (absent); it is written as a bool, matching the `KEY_TYPES` coercion
`config set box.share_images` applies.


```write_box_enable_vault(path: Path, enable_vault: bool = True) -> None```
Sparsely persist the box-scope `box.enable_vault` key at *path*.

The single writer for `box.enable_vault` at box create/move time (P8b — extracted from the retired
`write_project_meta` identity write so create no longer emits a `project:` / `resolved:` section:
box identity lives in the registries (`box_resolve`), not on disk — Option A). Sparse, matching
`config set box.enable_vault`:

* `enable_vault` explicitly `False` → write `box.enable_vault = False` into the `box:` table
  (created + merged beside `box.image`);
* the default `True` → write NOTHING, and DROP any stale `box.enable_vault` override. An empty
  `box:` table is never materialized, and a would-be no-op leaves the file untouched — so a
  default-vault primary/named box gets no `box.yaml` written here.

⚑ WHICH value a caller hands in is the resolver's business, not this writer's: PRIMARY and NAMED
pass the BOX-AUTHORED read, never the workset-resolved one (spec `:868` keeps the key sparse);
STANDALONE passes the resolved one on purpose — that write IS the M-8 migration.

Paired reader: :func:`read_box_enable_vault`.


```resolve_box_enable_vault(global_path: Path, *, box_path: Path, workset_path: Path | None) -> bool```
`box.enable_vault` through the FULL cascade — base < system < workset < box.

**THE RESOLVED VALUE, and the only thing that should ever fill `ProjectPaths.enable_vault`.** It
runs the same narrow, agent-less `_resolve_box_scalars` that backs `load_merged_config`, so the
BASE floor and the SYSTEM tier are cascade LEVELS rather than files nobody thought to open.

⚑ WHY THE LAUNCH DOES NOT JUST READ `load_merged_config`. It could — on the live path
`load_merged_config` (`commands/start.py`) runs before `_core_default_categories`, so the answer
would be available in time. But the three `paths.py` resolvers run BEFORE it and are what fill
`ProjectPaths.enable_vault`, which `settings/core_defaults.py` reads to decide whether the vault
bind rows are emitted at all. Reading the finished launch snapshot to decide what goes INTO it is
circular; this function answers from FILE PATHS, which is all a narrow resolve needs, at the point
those paths first exist (two lines below `_box_settings_files`).

⚑ COST: one extra narrow resolve, measured at 2.70 ms/call against 223-619 ms commands.

🛑 NOT the authored value. See `read_box_enable_vault`.


```_narrow_box_scalar_cascade(global_path, *, workset_path, box_path) -> KeyStore```
The box scalars' cascade WITHOUT the launch snapshot's whole-tree §0 audit.

⚑⚑ **THE ONE THING THAT DISTINGUISHES IT FROM `_resolve_box_scalars`, WHICH RESOLVES THE SAME
KEYS OFF THE SAME FILES.** That function ends in `build_launch_snapshot`, whose last step is
`_refuse_undeclared_snapshot` — a whole-tree audit that RAISES when any settings file in the
cascade carries an entry the keyspace does not declare. That refusal is correct for a launch and
for `box show --effective` (the refusal's own message names that command). It is **wrong for path
resolution**: this resolve runs inside `paths.resolve_project`, which every verb goes through —
including plain `kanibako box show`, the ONE surface designed to still answer for a box whose file
carries an undeclared entry, so that it can print the offending line. Routing path resolution
through the launch audit turned that diagnostic into a refusal (measured: it red
`test_box_show_marks_a_hand_written_undeclared_entry`).

⚑ THE SHAPE IS `settings_launch.resolve_selected_agent`'s — that module's own named *"narrow
resolve that precedes the launch snapshot"* — with `box_scalar_defaults_floor()` under the base
file. Nothing here is a second opinion about the cascade: `assemble_levels`, `merge` and the floor
builder are the same single carriers `_resolve_box_scalars` uses.

⚑ **THE TWO ROUTES ARE PINNED EQUAL** by `test_config.py::TestTheTwoBoxScalarResolvesAgree`, over
six tier combinations, so the split cannot quietly become two answers.

⚑ NO PREF RUNGS, and that is measured: `settings_prefs.ALLOWLIST` is `("system.agent",
"agent.*.**")`, so no §2h request can name a `box.*` key. Splicing the overlays in would move no
answer and would import `apply_prefs`' raise into path resolution.

⚑ NO `expand`: `box.enable_vault` is `type: bool` in the manifest and cannot carry an `@`-ref, and
a whole-tree expansion here would import the failure `resolve_selected_agent` had to go LENIENT to
avoid — an unrelated defective leaf aborting a resolve that never needed it.

⚑ A CONSEQUENCE WORTH STATING: `resolve_project` does NOT enforce the closed keyspace. That is the
status quo — it never did — and the enforcement still fires at every seam that had it before.


```read_box_enable_vault(path: Path) -> bool```
What the BOX ITSELF authored for `box.enable_vault` at *path* — one file, no cascade (default
`True`).

⚑⚑ **THE AUTHORED READER, AND ONLY THAT.** It sources the flag DIRECTLY from the `box:` table of
the box-tier `box.yaml`; an absent file, an absent `box:` table or an absent key all give the
built-in `True`. What it answers is the question a MERGE STRUCTURALLY CANNOT — *which tier carried
this value* (`settings_launch.py`, in its own words: *"Which of them carried it is not knowable
here, because the snapshot is the MERGE of all of them"*).

Its three callers are all lifecycle destination writes: `commands/box/_lifecycle.py` (×2, feeding
`ProjectState.box_authored_vault`) and `commands/box/_duplicate.py` (×1, feeding
`establish_standalone`). 🛑 **Do NOT give this a workset-tier fallback again and do NOT route it
through the cascade** — either one pins an INHERITED workset default as a box-scope override at the
destination, the exact corruption `carried_box_settings` exists to prevent.

⚑ IT HAD A *default_from* PARAMETER UNTIL 2026-08-29 — the WORKSET-tier R2 downward-default
(`box` ⊂ `workset`; spec §0 *Directional view/set across CONTAINMENT levels*), which all three
`paths.py` resolvers passed and which made `workset create --no-vault` reach contained boxes,
let a pre-M-8 standalone box keep a stored `box.enable_vault: false` with zero migration, and let
the primary workset default its boxes like any other workset (spec §2c). **That capability did not
go — it MOVED to `resolve_box_enable_vault`**, where the workset tier is one cascade level among
four instead of a second hand-opened file. The parameter went with it because the only thing it
could still do here is the corruption above.

Box identity derives entirely from the registries (`box_resolve`) — there is no on-disk `project:`
identity section (P8b sparse create) — while `enable_vault` stays a plain box-settings read: the
two concerns are decoupled. Paired writer: :func:`write_box_enable_vault`.


```carried_box_settings(box_tier: Path) -> dict```
The box-scope settings a LIFECYCLE op carries from a source box.

`convert` / `move` / `duplicate` all make a NEW box that inherits the source's box-scope settings.
Those live in the source's BOX TIER, so that file's content is carried verbatim (including
non-`box:` sections such as agent config). Returns the DOC to write at the DESTINATION's box tier;
`{}` when the source carries nothing.

### The box tier, and nothing else

Jei, 2026-08-26: *"we should copy/persist only those elements that are within the box settings."*
The function reads ONE file.

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
(`tests/test_commands/test_config_cmd.py`).

⚑ HISTORY, so it is not re-litigated: this function used to underlay the workset tier's `box:`
subtree beneath the box tier's, to keep a box created before the box tier existed from losing
`box.image`. Jei's ruling above settles it the other way, and his same-day ruling that there is no
installed base (*"there's not been real deployment before me up to now"*) leaves that rescue with
no population to rescue.

### `workset:` is never carried

Workset-scope keys are the source's OWN identity (`workset.kuid`); the destination establishes its
own.

⚑ This is HYGIENE, not a hazard fix: a stray `workset.kuid` sitting in a BOX TIER is INERT,
because the kuid is read directly from the ROOT file, never resolved through the cascade — pinned
by `test_kuid_is_read_from_the_root_file_not_the_box_tier`
(`tests/test_settings/test_paths.py`) and verified experimentally.

⚑ Now that the read is box-tier-only, the strip is DEFENSIVE and no test exercises it: a
`workset:` section can only reach a `box.yaml` by hand. Removing it leaves the whole suite green.
It is kept because a hand-authored one should not travel, not because a code path produces one.


```read_workset_kuid(path: Path) -> str```
The stored `workset.kuid` value at *path*, defaulting to the SENTINEL.

The reader for the settable `workset.kuid` key (settings-conformance P6d): it sources the kuid
DIRECTLY from the `workset:` table of a box's `workset.yaml` — for a STANDALONE box that single
file plays the WORKSET tier. An absent file / `workset:` table / key yields the reserved
:data:`kanibako.kuid.SENTINEL` (`"00000"`), the primary/named default and the "no real kuid yet"
marker.

⚑ THE LITERAL IS STILL HERE, BUT IT IS NO LONGER THE CARRIER. `settings_launch.workset_anchor_floor`
emits `kuid.SENTINEL` by reference (2026-08-29, `da2050a1`), so the keyspace answers this key from
the floor and this reader is the PRE-SNAPSHOT route to the same value. A conformance case asserts
the pair equal. See "The pre-cascade readers" above for why the old *"the DEFAULT lives in the
reader"* rule was retired.


```read_workset_skip_kuid_check(path: Path) -> bool```
The stored `workset.skip_kuid_check` bool at *path*, defaulting to `True`.

The reader for the settable key (P6d; spec default `true` — the advisory "invalid KUID" warning is
OPT-IN strictness, INVERTING the old D9). Sourced from the `workset:` table of a box's
`workset.yaml`. An absent file / table / key yields `True` (checking OFF).

⚑ SAME SHAPE AS `read_workset_kuid` ABOVE: `workset_anchor_floor` emits this key too (2026-08-29,
`da2050a1`), so the literal here is the PRE-SNAPSHOT carrier and not the one the keyspace answers
from; `test_the_skip_kuid_check_floor_equals_the_pre_snapshot_reader` pins the two equal.


```_split_config_key(flat_key: str) -> tuple[str, str]```
Split a flat config key into `(section, key)`.

* `"box_image"` → `("box", "image")`
* `"paths_dot_path"` → `("paths", "dot_path")`
* `"some_scalar"` → `("", "some_scalar")` (top-level scalar field)

A flat key with no recognised section prefix is a TOP-LEVEL scalar field; it returns an empty
section rather than raising. The typed writer in `config_interface` is the routed set/get/reset
path — this helper only serves the few remaining flat-key callers and must never crash on an
advertised key.


```write_project_config_key(path: Path, flat_key: str, value: str) -> None```
Write or update a single key in a `box.yaml`.

*flat_key* is the underscore-joined config name (e.g. `"box_image"`).


```unset_project_config_key(path: Path, flat_key: str) -> bool```
Remove a single key from a `box.yaml`; `True` if it was found and removed.

An emptied section is cleaned up rather than left as a bare `{}`.


```load_project_overrides(path: Path) -> dict[str, str]```
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


```read_system_agent(system_path: Path | None) -> str | None```
The stored `system.agent` SETTING from the system settings tier; `None` when unset or empty.

`system.agent` (spec §2g) is the CURRENT agent's name — a system-scope SETTINGS key (behavior, not
a config path), so it lives in the `system:` table of the system settings file `@config.settings` =
`@config.data/global/settings.yaml` (the `std.settings` path), exactly where `assemble_levels`
reads the system tier from. Callers pass that settings-file path as *system_path*, NOT
`~/.config/kanibako_config.yaml`, which holds only the bootstrap PATH tables.

`None` means "no system default" — callers fall through to the installed-count rule.

⮕ **RENAMED + RELOCATED (P7, spec §2g).** Was `read_default_agent`, reading `system.default_agent`
out of the reserved any-agent `agent.default` table under the leaf `default_agent` — a location
that made the stored default an UNDECLARED key riding the AGENT tier of the real cascade. A store
still carrying the old leaf is migration M-4 (documentation only) and is REFUSED by name at
assembly (`settings_assemble`'s retired-key check).

⚑ This is the PRE-CASCADE reader, kept for the two callers that need the stored value before a
snapshot exists (`start`'s box-independent persona pre-flight, and `setup`'s round-trip). The
LAUNCH does not use it: agent selection resolves `system.agent` off the snapshot, prefs included
(:mod:`kanibako.settings.agent_select`).


```read_setup_completed(settings_path: Path | None) -> str | None```
The `system.setup_completed` marker from the SYSTEM SETTINGS file; `None` when absent or empty.

`system.setup_completed` is a host-global `system.*` value recording the build version at which
`kanibako setup` last succeeded (W1). `None` means "setup never run" — the gate then re-nudges.

⚑⚑ **ITS FILE IS `@config.settings` (`<data>/global/settings.yaml`) SINCE 2026-08-26** — the same
file `read_system_agent` reads and the launch cascade's system tier assembles from, NOT
`kanibako_config.yaml`. Jei: *"there is no reason whatsoever that `system.setup_completed` should go
in the config. It should not. It should go in the global settings file."* That is also what spec §2g
has always declared — a Layer-2 `system.*` SETTINGS key — while spec §1 gives Layer 1 the `config.*`
bootstrap paths ALONE. It is exactly one file: **there is no fallback read of the old location**, and
adding one would be the deprecation window this release refuses.

⚑ **A FRESH install has no settings file at all**, so the marker reads absent and the gate answers
its NON-BLOCKING nudge — never "already set up", and never a block. Measured on a real store: the
advisory goes to stderr and the command proceeds to its own outcome unchanged.

⚑ **Why a RAW reader is required.** The gate runs PRE-CASCADE, before any snapshot exists.
(Historically there was a second reason, now moot: while the marker lived in `kanibako_config.yaml`,
`load_config` captured the leaf into the bootstrap-PATH set, whose only consumer
`resolve_system_paths` iterates `SYSTEM_PATH_DEFAULTS` and never the file's set-values — so the
captured leaf reached nothing. Since 2026-08-31 a marker left in that file does not reach nothing:
it REFUSES. `setup_compat_gate` still reads through `load_doc`, not `load_config`, so the gate
itself is unaffected — but every verb that resolves a path first will have refused already.)

*(The older wording here claimed the typed loader "maps only KNOWN system leaves and ignores
unknown ones". It has no known/unknown filter at all — measured 2026-08-11 — so that mechanism was
dropped rather than moved. The conclusion it supported is unchanged.)*

### The retired template-stamp gate

⚑ `read_templates_stamp` + `template_staleness_gate` lived in this module and are RETIRED (R-38,
2026-08-01). `system.templates_stamp` was a LIVE but UNDECLARED key — a §0 closed-keyspace
violation — and its HARD gate false-blocked hosts whose only sin was a packaged-content digest the
config had never recorded.

The protection folds into :func:`setup_compat_gate`: a template CONTENT change ⇒ `SETUP_FCV` bump
(nudge), a STRUCTURAL/breaking one ⇒ `SETUP_BCV` bump (hard block), with a CI check comparing the
packaged-template digest against the previous tag to REQUIRE the bump.

ACCEPTED LOSS (ruled): drift WITHIN one version — a dev build, or a plugin pip-installed after
first run — is no longer detected; the cure is the same `kanibako setup` the gate used to demand.

A stored `[system] templates_stamp` leaf on an existing host was ORPHANED-IGNORED until
2026-08-31 — an unknown `system.*` leaf reached no consumer and raised nothing. 🛑 **It now
REFUSES**, and not as a special case: it is a `system:` table in the Layer-1 file, which that file
may not carry at all, so the read names it like any other stale settings key. The cure is the same
hand-edit `MIGRATION.md` § *2.67 A settings table in `kanibako_config.yaml` stops the command,
instead of being ignored* prescribes. Migration records: M-23, and §2.67.


```setup_compat_gate(settings_path: Path | None) -> str | None```
Run the 5-band setup/config compatibility gate against the marker stored in *settings_path*.

⚑ *settings_path* is the SYSTEM SETTINGS file (`@config.settings`), the marker's home since
2026-08-26 — see `read_setup_completed`. The gate knows exactly ONE file, as it always did; which
file that is changed, and the arity did not. `cli._setup_nudge` resolves it with
`paths.load_system_config` (deliberately NOT `load_std_paths`, which MATERIALIZES the store — a
non-blocking advisory must not create directories).

Compares the recorded `system.setup_completed` marker (ConfigVer) against the running build
(CurrentVer = `__version__`) and the two build constants `SETUP_BCV` / `SETUP_FCV`. All comparisons
are by BASE version (PEP 440 `packaging.version.Version` — the project's own versions, e.g.
`1.6.0.dev25` / `1.6.0-rc1`, are PEP 440), so a dev/rc build of the same base as the released
marker reads as `==`, not "from the future".

The bands (design `plans/2026-06-23-setup-version-tiers-NEXT.md`):

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
* absent marker → return the first-run nudge (Jei 2026-06-23).
* unparseable marker → `None` (a hand-edited value; assume the user knows what they are doing —
  don't nag and don't block).

The two `raise` bands are the only blocking outcomes; the CLI surfaces them as rc1. Returning a
string is a NON-BLOCKING advisory the caller prints to stderr before continuing.


```resolve_agent(*, explicit_agent: str | None, requested: str | None = None, project_path: Path | None = None) -> str```
Validate/arbitrate the effective agent name, plus the installed-count rule.

⮕ **P7: the CASCADE moved out.** `system.agent` and the `pref.system.agent` requests of the
workset/box files are resolved off the launch snapshot by
:func:`kanibako.settings.agent_select.select_agent`, which passes the winner here as *requested*.
What stays here is what is NOT a key: name VALIDATION against the installed set, persona-ref
canonicalisation, and the installed-count rule. (Was: `explicit_agent > box_agent_name >
workset_agent > system default`, with `box.agent_name` — RETIRED, spec §2b — as the box tier.)

Precedence: *explicit_agent* (the §1A CLI level) > *requested* (whatever the settings cascade
resolved). The FIRST non-empty one "resolves a name".

A resolved name is validated against the installed set — the keys of `targets.discover_targets`,
i.e. the DISCOVERED PLUGINS:

* installed → return it;
* not installed → raise :class:`~kanibako.errors.AgentNotInstalledError` (actionable: names the
  agent + how to install it).

Nothing resolved → the installed-count rule (NO ordering, NO tie-break):

* exactly 1 installed → return that name;
* 0 installed → raise :class:`~kanibako.errors.NoAgentInstalledError` (Gate-2b);
* 2+ installed → raise :class:`~kanibako.errors.NoAgentSelectedError` (Gate-2a).

### Canonicalisation before validation

Each ref source may be a persona ref (`persona+harness`). The winning tier is canonicalised to its
node-name (`persona℘harness`; a bare ref stays byte-identical) so callers see a uniform node-name,
and the same call VALIDATES the ref shape (raises `ConfigError` on a malformed segment).

⚑ The HARNESS — right of `℘`, the whole name when bare — is what must be an installed target, NOT
the composite node-name: a persona's name segment is free-form.

### The pseudo-agent discount

The implicit installed-count rule (1 → use / 0 → error / 2+ → error) considers only REAL launchable
agents. `_PSEUDO_AGENTS` (`no_agent`, `general`) is subtracted, so a host with exactly one real
agent plus the built-in shell fallback is unambiguous (not "2+"), and a host with zero real agents
reports Gate-2b (not "use no_agent"). An explicitly-named harness validates against the FULL
`installed` set, so `no_agent` stays explicitly selectable (`--agent no_agent` /
`pref.system.agent: no_agent`).

⚑ The two members are NOT symmetric. `no_agent` is a real shipped target (`targets/no_agent.py`,
exported from `targets/__init__`). `general` is NOT a target any distribution registers — it is the
agent-LESS SLOT NAME `_resolve_box_scalars` passes as `agent_name`. So `--agent general` raises
`AgentNotInstalledError` today; its membership here is defensive, guarding against a plugin ever
claiming the name.

⚑ The imports inside the function are lazy on purpose: `kanibako.targets` imports `paths` / this
module indirectly, so importing it at module scope risks a cycle. This mirrors `discover_targets`'
use elsewhere.


```write_agent_setting(path: Path, key: str, value: str, agent_name: str) -> None```
Write a single agent-state override under `agent.<agent_name>`.

Preserves all other sections and other agents' agent subsections. Pass the reserved `"default"`
agent name to target the any-agent default tier.


```_flatten_dotted(data: dict, prefix: str = "") -> dict[str, str]```
Flatten nested dict into DOTTED-key form, stringifying scalar leaves.

`{"system": {"bindings": {"rw": {"foo": "h:g"}}}}` → `{"system.bindings.rw.foo": "h:g"}`.

⚑ Despite the illustrative example, this is NOT a scope-category helper. Its callers are the
Layer-1 `config:` read, the Layer-2 `system:` path-tier read, and the Layer-1 refusal that names its
keys. The scope categories
live in `settings_categories` / `settings_keyspace`, and their keys are TERMINAL — a destination is
DATA, not a key segment — so nothing here flattens one.

*(A section banner above this function used to read "Scope categories (settings-framework
{scope}.&lt;category&gt;.\* — the unified masks/bindings/caches/seeded/shared/synced/env
primitive)". It was wrong three ways — `shared` is the RETIRED spelling of `common`, the
`<category>.<name>` key shape went terminal on 2026-08-08c, and the section contains no category
code at all — so it was dropped rather than moved. The live set is
`settings_keyspace.TERMINAL_CATEGORY_TAILS`.)*
