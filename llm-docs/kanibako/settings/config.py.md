# The Bootstrap Config File — loading, writing, and the flat merged object

This module owns `kanibako_config.yaml` and the flat `KanibakoConfig` dataclass built from it: the
YAML read/write primitives, the built-in defaults, the layer-overlay merge, and a handful of
DIRECT readers (`box.enable_vault`, `workset.kuid`, `workset.skip_kuid_check`, `system.agent`,
`system.setup_completed`) that must answer BEFORE a cascade snapshot exists. It also carries two
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
  the Layer-2 `[system]` path settings, and it is what tells everything else where the settings
  files LIVE. Read by `load_config` into `KanibakoConfig.config_paths`. ⚑ Since 2026-08-26 the
  CONFIG member of that pair contributes `config.*` ONLY — `paths.load_system_config` filters its
  config-file reads, so a `system:` table there reaches no path. `load_config` itself stays a
  GENERAL document reader (it reads the settings file too, where `system.*` is exactly what is
  wanted), so the filter lives at the Layer-1 read sites, not in the reader.
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

## B6 — the three box scalars are KEYSPACE-resolved

`box.image` / `box.share_images` / `box.shell` (`_BOX_SCALAR_FIELDS`) are no longer the flat
overlay's product. `load_merged_config` runs `_resolve_box_scalars`, which builds a real cascade
snapshot and overwrites the flat fields with the resolved values. Every caller — the launch,
`kanibako shell` (agent-less), and the box-less sites (`rig` / `diagnose` / `setup` / `baseline`,
which pass no project) — reads the SAME resolve through the SAME fields, so there is ONE live
source.

The flat overlay walk still runs underneath it. It owns `paths_project_toml` and the corner
semantics the resolve falls back to (present-`None` reset; `""`).

⚑⚑ **`load_merged_config` NO LONGER READS SETTINGS OUT OF THE LAYER-1 FILE.** *"kanibako_config.yaml
<-- cannot have settings. Period."* — that file was the least-specific FILE source of the scalar
overlay, and its `[box]` table overrode the declared defaults; the scalars now START at those
defaults and the first thing that can move them is the WORKSET tier. The file's `config.*`
foundation still loads into `config_paths` (that is the file's whole job, spec §1) and is FILTERED,
so a `system:` table hand-written into the bootstrap file cannot ride along either. Stopping the
WRITE alone would not have been enough: a hand-written table, or one left by an older build, would
have gone on silently overriding the defaults.

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

⚑ The floor is captured from the `load_config` read of *global_path* BEFORE any overlay, so a
workset or box value cannot masquerade as the system-stored default — it enters the resolve at its
OWN tier instead. `""` entries drop out of the fold (absent ≡ no default) and the flat fallback
then applies the built-in default, preserving the `""` corner byte-identically.

A `box:` table in `global/settings.yaml` — where `kanibako system set box.image=…` has always
written — now resolves too. It was silently stranded before B6.

### The old machine-wide third file is DELETED

`/etc/kanibako/kanibako.yaml` is gone (spec §2). The admin authority is exactly the
`config_base.yaml` / `settings_base.yaml` base tiers, resolved on the PATH side; this scalar
loader starts from the built-in defaults.

## The pre-cascade readers, and the rule they share

`read_box_enable_vault`, `read_workset_kuid`, `read_workset_skip_kuid_check` and
`read_system_agent` all read a DECLARED key DIRECTLY out of a settings file rather than through
the resolver, because each has a caller that runs before a snapshot exists. They share one
consequence: **the DEFAULT lives in the reader, not in a cascade floor.** That is the P2/P6d
reader-default pattern, and it is why each of their docstrings spells its own default.

## Functions

```coerce_bool(value: object) -> bool | None```
Coerce a config value to a real bool using the shared truth table.

Returns the bool, or `None` if *value* is not a recognized bool literal. Already-bool values pass
through. The truth tables (`_BOOL_TRUE` / `_BOOL_FALSE`) are shared by the typed `config set`
writer (`config_interface`) AND the box.meta writer so both round-trip identically.


```class KanibakoConfig```
The flat merged configuration object.

Precedence over the FILE layers, least → most authoritative: hardcoded defaults <
`kanibako_config.yaml` (user global) < the workset tier < the box tier < CLI overrides. ⚑ The
three `box.*` scalars do NOT resolve this way any more — see "B6" above; `load_merged_config`
overwrites them from the keyspace after the overlay walk.

⚑ `box_agent_name` is GONE (P7, spec §2b). `box.agent_name` is RETIRED and a box selects its agent
with the REQUEST `pref.system.agent` (§2h), resolved off the launch snapshot by
:mod:`kanibako.settings.agent_select`. There is no flat-scalar agent field any more — the
selection is a KEY.

`config_paths` holds the bootstrap PATH set-values keyed by full dotted name: the MERGED Layer-1
`config.<leaf>` foundation keys (from the `[config]` table) AND the Layer-2 `system.<leaf>` path
settings (from the `[system]` table), read from `kanibako_config.yaml`. It is CONFIG-FILE-ONLY —
project and workset configs never supply it.

⚑ `BOX_META_FILE` (`"box.yaml"`) is the per-box construct-time metadata + box-tier settings
cascade file (spec §2c, `meta.box.*`).


```_flatten_toml(data: dict, prefix: str = "") -> dict[str, object]```
Flatten nested config dict into underscore-joined keys.

`{"paths": {"boxes": "x"}}` → `{"paths_boxes": "x"}`. Booleans are preserved; `None` is preserved
as the reset sentinel (see "The `None` sentinel" above); other scalars are stringified.


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
The scalar/bool fields actually PRESENT in a config file, as field-name → value.

`None` is preserved as the reset sentinel; callers must distinguish it from an absent key (which
simply will not appear in the returned dict).

The `[config]` (Layer-1) and `[system]` (Layer-2) tables are POPPED before flattening: they are the
bootstrap-PATH tier, handled by `load_config`'s `config_paths` extraction, and must not leak into
the scalar field overlay. The dict field (`config_paths`) is likewise NOT included here; it keeps
its own dedicated parsing/merge logic.


```load_config(path: Path) -> KanibakoConfig```
Read a single config file and return a `KanibakoConfig` with defaults filled in.

The bootstrap-PATH tables are extracted first: the Layer-1 `[config]` foundation keys
(`config.<leaf>`) and the Layer-2 `[system]` path settings (`system.<leaf>`), merged into ONE
`config_paths` set keyed by full dotted name. Each table is flattened so nested sub-keys (e.g.
`system.channels.common`) become dotted keys while scalar leaves (e.g. `config.data`) stay flat.

Scalar/bool fields follow: a present key sets the field; a present `None` resets it to the
built-in default.

⚑ The extraction is UNFILTERED — every leaf under `[config]` / `[system]` lands in `config_paths`
under its dotted name, including leaves this build has never heard of. Nothing downstream consults
it by iteration (`resolve_system_paths` walks `SYSTEM_PATH_DEFAULTS`, never the file's set-values),
so an unknown leaf is orphaned-ignored rather than rejected. This is the mechanism that makes a
stale `[system] templates_stamp` inert — see "The retired template-stamp gate" below — and, since
2026-08-26, the one that makes a stale `[system] setup_completed` inert too, that leaf's storage
having moved to the settings file.


```_resolve_box_scalars(global_path, *, workset_path, box_path, cli_overrides) -> dict[str, object]```
Resolve the three box scalars (:data:`_BOX_SCALAR_FIELDS`) through the KEYSPACE.

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

⚑ `_BOX_SCALAR_FIELDS` maps the three box-scope SCALAR keys the merged loader resolves through the
KEYSPACE (B6, R-11a(a)): dotted key → the flat `KanibakoConfig` field it lands on. `box.shell`
rides the same resolve — it lives on the same object and the same `box:` tables (consumer-map
risk 4).


```load_merged_config(global_path, project_path=None, *, workset_path=None, cli_overrides=None) -> KanibakoConfig```
Load global config, overlay workset, then project, then CLI overrides — then run the B6 resolve.

Start from the user global config (the least-specific FILE source now that the machine third file
is deleted), then overlay the workset and project layers so the most-specific PRESENT value wins.
Finally the keyspace resolve for the three box scalars: a resolved value wins; an ABSENT resolve
keeps the flat value. See "B6" above for the whole shape, and "The old machine-wide third file"
for what was removed.

The nested `_overlay_scalars` applies one file layer's PRESENT scalar/bool fields. Presence-based,
per "The `None` sentinel" above. `config_paths` is config-file-only and handled separately, so it
never appears there.

⚑ `box_share_images` is coerced through :func:`coerce_bool` on the way back out of the resolve;
the other two are stringified. A resolved value that is not a recognized bool literal falls back to
`bool(value)`.


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


```read_box_enable_vault(path: Path, *, default_from: Path | None = None) -> bool```
The box-scope `box.enable_vault` value stored at *path* (default `True`).

The single reader for the settable box-scope key (P2 clean break): it sources the flag DIRECTLY
from the `box:` table of the box-tier `box.yaml`. An absent file, an absent `box:` table, or
an absent key all fall through to *default_from* (when given), then to the built-in default `True`
(vault on).

*default_from* is the WORKSET-tier settings file, consulted ONLY when the key is absent from the
box tier — the R2 downward-default (`box` ⊂ `workset`: a `box.*` key stored at the workset tier is
an overridable default for the box). This key is NOT cascade-resolved — it is read directly, off
the launch path — so the fallback has to be spelled here rather than falling out of the resolver.

⚑ ALL THREE resolvers pass it, each load-bearing for its own reason. STANDALONE: its ROOT
`workset.yaml` WAS its box file before the box tier moved to `box_data/box.yaml` (M-8), and is its
workset tier after — the fallback lets an existing standalone box keep a stored
`box.enable_vault: false` with ZERO migration. NAMED: `workset create --no-vault` writes the key at
the workset tier, and without the fallback that flag is a silent no-op. PRIMARY: the primary workset
is a workset like any other (spec §2c), so a key stored there defaults its boxes the same way.

Box identity derives entirely from the registries (`box_resolve`) — there is no on-disk `project:`
identity section (P8b sparse create) — while `enable_vault` stays a plain box-settings read: the
two concerns are decoupled. Paired writer: :func:`write_box_enable_vault`.


```carried_box_settings(box_tier: Path, workset_tier: Path | None) -> dict```
The box-scope settings a LIFECYCLE op carries from a source box.

`convert` / `move` / `duplicate` all make a NEW box that inherits the source's box-scope settings.
Post-P2 those live in the source's BOX TIER, so that file's content is carried verbatim (including
non-`box:` sections such as agent config). Returns the DOC to write at the DESTINATION's box tier;
`{}` when the source carries nothing.

### The legacy underlay

A standalone box created BEFORE the box tier existed wrote its `box.*` keys into its ROOT file —
which is its WORKSET tier now (M-8). Its box tier is therefore absent or partial, so the workset
tier's `box:` subtree is underlaid beneath the box tier's (box tier WINS, per R2). Without this,
every pre-P2 standalone box silently loses `box.image` and friends the first time it is converted,
moved or duplicated.

### `workset:` is never carried

Workset-scope keys are the source's OWN identity (`workset.kuid`); the destination establishes its
own.

⚑ This is HYGIENE, not a hazard fix: a stray `workset.kuid` sitting in a BOX TIER is INERT,
because the kuid is read directly from the ROOT file, never resolved through the cascade — pinned
by `test_kuid_is_read_from_the_root_file_not_the_box_tier`
(`tests/test_settings/test_paths.py`) and verified experimentally.

An earlier version of this code claimed carrying it would OVERRIDE the destination's fresh kuid,
and used that to justify dropping the legacy underlay entirely. That claim was wrong on both
counts, and dropping the underlay is what caused the loss described above.


```read_workset_kuid(path: Path) -> str```
The stored `workset.kuid` value at *path*, defaulting to the SENTINEL.

The reader for the settable `workset.kuid` key (settings-conformance P6d): it sources the kuid
DIRECTLY from the `workset:` table of a box's `workset.yaml` — for a STANDALONE box that single
file plays the WORKSET tier. An absent file / `workset:` table / key yields the reserved
:data:`kanibako.kuid.SENTINEL` (`"00000"`), the primary/named default and the "no real kuid yet"
marker.

Mirrors :func:`read_box_enable_vault` (the P2 reader-default pattern): the DEFAULT lives here, not
in a cascade floor.


```read_workset_skip_kuid_check(path: Path) -> bool```
The stored `workset.skip_kuid_check` bool at *path*, defaulting to `True`.

The reader for the settable key (P6d; spec default `true` — the advisory "invalid KUID" warning is
OPT-IN strictness, INVERTING the old D9). Sourced from the `workset:` table of a box's
`workset.yaml`. An absent file / table / key yields `True` (checking OFF). Mirrors
:func:`read_box_enable_vault` — the DEFAULT lives here, not a cascade floor.


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
`load_config` captured the leaf into `KanibakoConfig.config_paths` — the bootstrap-PATH set, whose
only consumer `resolve_system_paths` iterates `SYSTEM_PATH_DEFAULTS` and never the file's
set-values — so the captured leaf reached nothing.)

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

A stored `[system] templates_stamp` leaf on an existing host is ORPHANED-IGNORED, by the unfiltered
extraction described under `load_config` above (verified 2026-08-02, re-verified 2026-08-11): an
unknown `system.*` leaf reaches no consumer and raises nothing. Migration record: M-23.


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

⚑ Despite the illustrative example, this is NOT a scope-category helper. Its only two callers are
`load_config`'s extraction of the bootstrap `[config]` and `[system]` tables. The scope categories
live in `settings_categories` / `settings_keyspace`, and their keys are TERMINAL — a destination is
DATA, not a key segment — so nothing here flattens one.

*(A section banner above this function used to read "Scope categories (settings-framework
{scope}.&lt;category&gt;.\* — the unified masks/bindings/caches/seeded/shared/synced/env
primitive)". It was wrong three ways — `shared` is the RETIRED spelling of `common`, the
`<category>.<name>` key shape went terminal on 2026-08-08c, and the section contains no category
code at all — so it was dropped rather than moved. The live set is
`settings_keyspace.TERMINAL_CATEGORY_TAILS`.)*
