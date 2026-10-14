# Agent Selection — which agent a box runs, and why the answer is installed

`agent_select` is the ONE seam every command uses to answer *"which agent is this box's?"*. It
resolves the settings side of the question with a narrow pre-pass, applies `--agent` on top, and
hands the winner to `kanibako.settings.config.resolve_agent`, which owns everything that is NOT a
key: name validation against the installed set and persona-ref canonicalization.

🛑 **IF NOTHING NAMES AN AGENT, NOTHING LAUNCHES** (keyspec §2b). The installed-agent COUNT decides
nothing — not even one installed plugin is picked for the user — and the two no-name states get two
DIFFERENT refusals:

| `system.agent` | means | raised |
|---|---|---|
| UNSET | setup has never chosen one | `AgentUnsetError`, naming `kanibako setup` |
| present-`None` | a settings file declined to set a default | `AgentNoDefaultError`, asking for a name |

⚑ A third failure is easy to confuse with them and must stay distinct: YAML reads `None`/`none` as
STRINGS, so those spellings request an agent by that name and fail as `AgentNotInstalledError`.
⚑ The null state is reachable BY TYPO — `null`, `Null`, `NULL`, `~` and a key left blank after its
colon all read as Python `None`.

The module is deliberately thin. It contains one dataclass, two context builders
(`launch_resolve_ctx` and its box-less body `host_resolve_ctx`), and one resolve function; the
interesting content is the set of rules below, all of which are spec rules
(`specs/settings-keyspace-1.8.0.md` §1A / §2b / §2g / §2h) rather than local choices.

## The selection order, and where each tier is settled

Selection order (spec §2h, least to most specific):

```
system.agent  <  workset pref  <  box pref  <  --agent (the §1A CLI level)
```

The first three are settled INSIDE the settings cascade. `system.agent` is the stored default, and
a `pref.system.agent` request from the workset or box file overrides it, box beating workset by
assignment order. This module resolves that much with a NARROW pre-pass,
`kanibako.settings.settings_launch.resolve_selected_agent`, and then applies `--agent` on top.

`SELECTION_KEY` is the key naming the agent a box runs (spec §2g), re-exported from here so callers
spell it once.

## `box.agent_name` is retired; a box requests its agent

`box.agent_name` is RETIRED (spec §2b): as a box key it would make the L4.1 anchors
`meta.box.auth.workset_path` and `meta.box.agent.auth.share_support` derive from a SETTABLE key at
their own level, which §0's bootstrap rule forbids. A box or workset selects its agent with the §2h
REQUEST `pref.system.agent`, and the system default is `system.agent` (§2g), in the `system:` table
of the system settings file.

## The selection LEVEL, and why the resolved answer is installed unconditionally

Whatever wins, `select_agent` reports it and the launch installs it at `system.agent` as the §1A
top-most level (`build_launch_snapshot(cli_level=…)`). That is not tidiness — keys dereference
`{system.agent}` (e.g. the §2c anchors `meta.box.auth.workset_path` and
`meta.box.agent.auth.share_support`), so the snapshot MUST agree with the process that runs.

Without the install there are two ways to disagree:

* `--agent claude` with `pref.system.agent: goose` — the snapshot says goose while claude runs;
* a persona ref stored as `nav+claude` — the running node is the canonical `nav℘claude`.

Suppressing the pref when `--agent` is given fixes neither: it only changes WHICH wrong value the
snapshot reports. One rule — *the resolved selection is installed at the top* — covers both, is a
no-op when the cascade already said it, and is the mechanism every key-shadowing flag uses.

## The CLI level is built by `settings_cli_level.build_cli_level`

The level is built by `kanibako.settings.settings_cli_level.build_cli_level`, which owns the
flag→key table and carries this selection alongside the launch's ephemeral flag values, and it is
validated by `guard_cli_level` inside `build_launch_snapshot`.

`AgentSelection.selection_level` keeps its name because it really is only the selection — it is
this module's INPUT to that builder.

Which resolves see the flags is a separate rule, stated at `build_launch_snapshot`: the selection
rides EVERY resolve, the flags ride only the launch resolve, and no resolve whose output is written
to disk sees a flag.

## Why the selection key is excluded from §2h's LOCATOR CLOSURE

⚑ `system.agent` DOES select a cascade-input file (`meta.agent.<agent>.settings`), which normally
puts a key in §2h's locator closure. It is deliberately excluded there, and the same argument covers
the CLI level (§1A's locator caveat): an AGENT file may not carry prefs, so a re-selected agent file
cannot introduce new requests, and the selection level is an initial-value overlay that triggers no
re-read. See `settings_prefs.LOCATOR_CLOSURE`.

## `AgentSelection` — the two field vocabularies

*node* is the canonical agent NODE-name (`persona℘harness`; a bare agent is byte-identical to its
harness). `select_agent` never returns `""` (see `has_agent`); a plain-shell box is the node `shell`
(keyspec §2b, §2d).

*source* answers *"why did system.agent resolve to that?"* without reading files:

| `source` | meaning |
|---|---|
| `"cli"` | `--agent` |
| `"settings"` | the stored key, or a pref |

🛑 **Those are the only two.** Nothing auto-picks an agent, and `pref.system.agent: null` is a
REFUSAL, so a returning `select_agent` call always carries a name somebody wrote.

## `has_agent` — `""` means OPPOSITE things on the two sides of the target seam

Does this box run an agent AT ALL? (spec §2b D-M6.)

⚑⚑ **USE THIS — never `bool(selection.node)` at a call site, and NEVER pass an empty node on to
`resolve_target`.** Two vocabularies meet at that seam:

* to SELECTION, `node == ""` means *this box runs NO agent*;
* to `kanibako.targets.resolve_target`, an empty/absent name means *no name was given —
  AUTO-DETECT one*, which is its documented contract for other callers and is not a bug there.

Handing the first to the second LAUNDERS a no-agent box into auto-detection: bifrost measured one
launching claude, with claude's binary, commons and CREDENTIALS delivered. 🛑 **`pref.system.agent:
null` produces no node-less selection** — it refuses — so no production path reaches this guard.

⚑ **It is KEPT, and NOT because `shell` will arrive through it.** Keyspec §2b expresses the
plain-shell box as an effective `{system.agent}` of `shell`, and §2d gives that pseudo-agent a name
(`meta.agent.shell.name` = `"shell"`), so a D2 selection is `node="shell"` — `has_agent` is TRUE for
it and it resolves a target like any other agent. Nothing on the board is waiting to produce a
node-less selection. The guard stays for the two reasons that do not expire: `""` means opposite
things on the two sides of this seam whatever produces it, and the shape costs one boolean against a
measured incident. It is a floor, not a reservation.

Route every selection→target conversion through the idiom below. ⚑ **There is no helper
that does it for you** — the `has_agent` guard IS the translation, spelled at each seam, so look for
the guard and not for a function.

⚑ The predicate is `bool(self.node)` and nothing else.

**The idiom, at every seam that needs a target:**

```python
target = resolve_target(harness_of(sel.node), path) if sel.has_agent else None
```

`None` is the target-less shape `kanibako shell` and `--entrypoint` launches produce, and every
downstream gate keys on `target is None` (no agent binds, no agent config, no credential delivery,
no `KANIBAKO_AGENT` stamp, `agent_id` = `"shell"`, `agent_ref.GENERAL_SLOT`).

⚑ Deliberately NOT `ShellTarget()`: that is a RESOLVED target and earns a `KANIBAKO_AGENT` stamp.
A box gets `ShellTarget` by selecting `shell` by name, or from `resolve_target`'s own
auto-detect-found-nothing case — never from a selection that named nothing.

## `selection_level`

The §1A top-most level to install (`{"system.agent": node}`), or `None` for an empty node. A launch
with no selection at all pins `system.agent: shell` itself (`start._launch_selection_level`, keyspec
§2b D-M6).

## `launch_resolve_ctx` — the ONE ctx builder, and the resolver split

Builds the host-side `kanibako.settings.settings_resolve.ResolveCtx`. It is the one ctx builder for
every snapshot resolve — `settings_launch.resolve_inputs` calls this too, so the selection
pre-pass and the launch snapshot cannot drift in what `@config.*` / `$XDG_*` / `~` mean.

Its body is `host_resolve_ctx(std, group, agent_name)`, keyed on the working set rather than the
box, which the box-less `resolve_inputs` subjects call directly: *group* is a `ProjectGroup` or a
`Workset` (anything with `name` and `is_default`), or `None` for the system scope. The primary
working set is spelled like `None`, because `ResolveCtx.workset_name` names a NAMED working set only.

**Resolver SPLIT (spec §1A / JC-2):** the Layer-1 `config.*` foundation goes into `ctx.config` (so
`{config.*}` category refs route THERE, not the snapshot); the Layer-2 `system.*` path settings stay
folded into the snapshot floor so `{system.*}` resolves from it.

The xdg map is the canonical FULL host map — a data-home-only partial map raised on stored
`{$XDG_CACHE_HOME}/...` values — anchored on the resolved `std.data_home`.

⚑⚑ **BOTH HALVES OF THE CTX ARE DERIVED BUILDERS, NEVER HAND LISTS.** `config=` is
`settings/paths.host_config_map(std)`, the Layer-1 twin of `system_path_floor` and the `config=`
twin of `host_xdg_map`. A hand list misses a key `CONFIG_PATH_DEFAULTS` declares: `config set` then
accepts the key and `{config.<key>}` resolves at set time, but it reaches `_ABSENT` at launch and the
referring key is dropped with no message and rc 0. `tests/test_settings/test_path_tier_parity.py`
compares the carriers, and it is EFFECT-based on both sides.

*agent_name* is `None` for the SELECTION pass: no agent is known yet, so a `{$AGENT}` anywhere
resolves to a refusal rather than to a silent `""`. That refusal is recorded (never raised) because
selection expands LENIENTLY; see `kanibako.settings.settings_launch.resolve_selected_agent`.

## `select_agent` — what it raises, and who is allowed to swallow it

It raises `kanibako.errors.AgentNoDefaultError` for a present-`None` selection — its OWN raise, the
one `resolve_agent` cannot make because it cannot see the difference — plus the typed
`AgentResolutionError` subclasses `config.resolve_agent` raises (`AgentUnsetError` when nothing set
the key, `AgentNotInstalledError` when the name resolved but its adapter is missing), the ref-grammar
`ConfigError` for an *explicit_agent* that is malformed or blank, a
`kanibako.settings.settings_resolve.SettingsError` when the selection key itself does not resolve,
and the retired-key refusal when a settings file still carries `box.agent_name` /
`system.default_agent` (`MIGRATION.md` § *Settings keys renamed or retired*).

Informational callers that must degrade rather than fail (`box info`, the `config --effective`
display) keep their existing `try/except` — that is what makes the launch loud and the read verbs
quiet, deliberately.

### The retired-key sweep, tier by tier

RETIRED spellings are refused by name BEFORE anything is resolved (`MIGRATION.md` § *Settings keys
renamed or retired*). `select_agent` runs the check as the `SELECT` purpose of
`settings_assemble.read_settings_files`, not inside `assemble_levels` (the `NARROW` read), because a
raise there would also break `config set` — the very command the message prescribes.

⚑ EVERY tier the cascade reads is checked, BASE included
(`/etc/kanibako/settings_base.yaml`): a site admin's stale `box.agent_name` would default DOWN into
every box on the machine, which is the worst version of the silent-wrong-agent failure, not an
exempt one.

The cure is LEVEL-APPROPRIATE — a pref is not writable at base/system, so those levels are told to
REMOVE the key rather than to run a `box set` that cannot fix them.

⚑ The BOX-level cure names the box explicitly, because the bare form only works from a resolvable
cwd: `select_agent` passes `box_ref=project_cure_ref(proj) if proj.name else None` to
`read_settings_files(..., purpose=ReadPurpose.SELECT)`, which hands it to the box level only. A
NAMELESS box (`proj.name` falsy — the short-hash fallback `settings/paths.py` uses for display is
NOT an addressable `box set` positional) gets the bare cure line rather than an argument that would
not resolve.

### PRESENT-`None` is not `__MISSING__`

Only the cascade can decline or supply, so `--agent` short-circuits the pre-pass entirely and a
launch that names its agent pays for no extra resolve.

⚑ **GIVEN is `is not None`, never truthiness.** A blank `--agent ""` short-circuits the pre-pass
too, and goes through to `resolve_agent` to be refused by the ref grammar's own message. Under a
truthy test a value the user TYPED was handed back to the files to answer — and since the pre-pass
answers the whole question on its own, that answer never reached the arbiter at all: `--agent ""`
at a box carrying `pref.system.agent: null` came back as a no-agent box, silently.

When the pre-pass returns PRESENT-`None`, a settings file set `pref.system.agent: null` — **NO
DEFAULT IS SET** (keyspec §2b), so the seam raises `AgentNoDefaultError` asking for a name. ⚑ It
must be kept distinct from `__MISSING__` — never collapse them with a falsiness test — because
`__MISSING__` means *setup has never run* and gets the OTHER refusal. Collapsing the two spellings
loses the only distinction that decides which sentence the user reads.

⚑ **The plain-shell box is reachable through settings, by NAME:** `shell` is the built-in
target seeded by `kanibako-cli` itself (no entry point — `pyproject.toml` carries none for it), so
`discover_targets()` always contains it and `resolve_agent(explicit_agent=None, requested="shell")`
returns `"shell"`. `ShellTarget` has no `descriptor`, so `targets.base.has_plugin` is false for it
and `commands/start.py` computes `shell_launch = not entrypoint and not has_plugin(target)` — true
without `--entrypoint` — and runs the resolved `box.shell`. `--agent shell` and `pref.system.agent:
shell` both work. The retired `no_agent` spelling is refused, never translated
(MIGRATION.md § "The plain-shell store is").
