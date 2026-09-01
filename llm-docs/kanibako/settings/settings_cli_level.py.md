# The CLI Level — the one flag→key table, the one builder, the one guard

`settings_cli_level` owns spec §1A's **COMMAND LINE level**: the highest input level in settings
resolution, above everything. The spec states it as a GENERAL rule rather than a carve-out for
particular flags — *"ANY flag that shadows a key overrides that key for the launch"* — and this
module is where that generality lives. It declares the FLAG→KEY table, builds the level for one
launch, and guards it, so the level is ONE mechanism rather than N checks scattered across the
launch.

P7 built the first instance of that level for exactly one key: `system.agent`, the resolved agent
selection. P8 generalises it into the builder and the guard here (P8 also being the boundary-cost
principle the split pays for).

**PURE:** no I/O and no plugin import at module load, like `kanibako.settings.settings_keyspace`.
The set of valid agent NAMES is INJECTED by the caller rather than discovered here; the per-agent
leaf VOCABULARY is the opposite and the guard section says why.

## What the level IS, and is not

* **EPHEMERAL, always** (spec §1A). A flag applies to ONE launch and NEVER mutates a stored value.
  Nothing here is written to any settings file; the level exists only as the top entry of one merge.
* **No recompute**, unlike prefs (§2h). CLI values are known BEFORE resolution starts, so they seed
  it as the highest-precedence input and resolve once. A flag cannot pull in a new file carrying new
  flags, so the termination argument prefs need does not arise here.
* **Not a scope.** The level carries keys of ANY scope; it is not subject to §0's directional
  (own-scope) enforcement, which governs what a settings FILE may contribute.
* **Not a `pref`** (§2h). It is a separate input level that outranks every settings file AND every
  pref.

## Why the guard exists

Spec §1A: *"Because the CLI is not a pref, the §2h forbidden tiers do NOT automatically cover it, so
a flag that could set a LOCATOR-class value (one that relocates a cascade-input file) needs its OWN
guard, or the standing guarantee that CLI values are applied only as an initial-value overlay that
never triggers a re-read."*

BOTH halves hold here, and the explicit guard is implemented anyway:

* the **standing guarantee** is true today — the cascade's input FILE paths come from
  `paths.box_workset_settings_paths`, a runtime treewalk that consults no settings key, so a CLI
  value cannot relocate a cascade input and cannot trigger a re-read;
* the **explicit guard** is still written, because that guarantee is an invariant of code living
  elsewhere, and because a locator-class CLI value would corrupt derived PATHS even without a
  re-read: a CLI `workset.boxes` re-points `meta.box.path` → the `box.bindings.rw.home` host_src →
  the §2c mount-over-the-box-home catastrophe, silently.

⚑ `system.agent` is deliberately PERMITTED even though it selects a cascade-input file
(`meta.agent.<agent>.settings`). It is excluded from `settings_prefs.LOCATOR_CLOSURE` for the reason
recorded there — an agent file may not carry prefs, so re-selecting one introduces no new requests —
and the same argument covers the CLI level. It is the whole point of the feature; a guard that
refused it would break P7.

## `SELECTION_KEY`

The key naming the agent a box runs (spec §2g). It is re-exported from the selection seam's own
spelling so the level and the selection cannot drift apart.

## The flag→key table — WIRED ENTRIES ONLY

`CLI_SHADOWED_KEYS` is the DECLARED table (spec §1A). Its value is a display TEMPLATE (`<agent>` =
the active discriminator), used by tests and by humans; the builder is the executable form. The
table lists only entries this module actually wires.

### Why `-S` / `-A` are NOT listed

The spec's enumeration also names `-S`/`-A` (`access` — `-S` selects the `restricted` tier, `-A` the
`full` one). Listing an unwired entry would read as a contract this module honours — the same reason
`settings_keyspace` deleted its unused `CATEGORY_SCOPES` rather than keeping it.

The `access` key is read TWICE, deliberately. The CASCADE tier feeds `deliver_panel_permissions` /
`deliver_directive_hook`, which WRITE it onto the box's own persisted agent config surface (§1A's
PROJECTED-SURFACE EXCEPTION, a structural class rule), while the FLAG-folded tier feeds the
ephemeral launch argv/env. Installing the flag at this level would make an ephemeral flag mutate a
stored value — exactly what §1A forbids.

`-M` is likewise barred from the codex config-projection RESOLVE (that consumer reads the cascade,
never the CLI level), though the flag itself is wired here for the launch argv.

### Why `--image` / `--share-images` ARE listed

They are WIRED (B6, R-11a(a)): `box.image` / `box.share_images` resolve through the keyspace (the
box-scalar resolve in `config.load_merged_config`), agent-lessly — a box-scope flag needs no active-
agent discriminator.

Their PERSISTENCE at box CREATION is NOT this level's doing. §1A's CREATE EXCEPTION is implemented
as the ONE gate `kanibako.settings.config.persist_creation_flags`, called by `create` and by the
launch-materialization path alike; this level stays ephemeral.

## `_FORBIDDEN_HEADS` — namespaces a CLI value may never target

Spec §2h's CATEGORICAL tier, which the CLI does not inherit and therefore restates:

* `meta` — RO by contract (§0); a CLI set would be a backdoor around RO.
* `config` — the Layer-1 foundation, resolved BEFORE the cascade this level splices into, so a value
  here could not take effect and must not pretend to.
* `pref` — a REQUEST is not a value; a CLI request-of-a-request has no termination argument.

## Building the level

`build_cli_level` returns the level for one launch, or `None` when it would be empty.

**`selection`** is P7's resolved-agent level (`{"system.agent": node}` from
`kanibako.settings.agent_select.AgentSelection.selection_level`, or `None` for a NO-AGENT box). It is
carried through VERBATIM: it is installed on EVERY resolve, whichever of its three sources won, so
`@system.agent` equals the node that actually runs. See `kanibako.settings.agent_select` for why
that is load-bearing.

**`active_agent`** is the discriminator the agent-scope keys are spelled against. The agent-scope
entries are emitted ONLY when it is truthy — a NO-AGENT / `--entrypoint` launch resolves against the
`"general"` template slot, which is not an agent, so a flag there is simply not applicable and is
dropped rather than fabricating `agent.general.*`.

⚑ **The agent-scope spelling is `agent.<active>.<leaf>`, never `agent.default.<leaf>`.**
`effective_behavior` performs the §2d active-over-default pick AFTER the cascade merge, so a value at
`agent.default.<leaf>` loses to any file's `agent.<active>.<leaf>` even from level index 0 — which
would contradict "the highest, above everything". The spec writes these keys bare (`model`,
`continue_mode`); the discriminated active spelling is the only reading under which the CLI actually
outranks the files (spec §0: there is no bare `agent.<key>`).

### The per-flag rules

**`model`** (`-M`/`--model`): a non-empty string installs `agent.<active>.model`. `None` or `""`
installs NOTHING — absent ≠ `""`, and `""` is a terminal value the resolver treats as meaningful, so
a flag-not-given must not be laundered into one.

**`new_session` / `continue_session` / `resume`** (`-N` / `-C` / `-R`, an argparse MUTUALLY EXCLUSIVE
group) fold into `agent.<active>.continue_mode`:

* `-N` → `False` (start fresh);
* `-C` → `True` (continue the last conversation);
* `-R` → `True`;
* none of them → the key is ABSENT and the stored/default value stands.

⚑ **`-R` installing `True` is LOAD-BEARING, not cosmetic.** `assembly.resolve_mode` falls through its
picker arm for a descriptor with no `resume` mode (goose/codex) and then keys on `skip_continue`. The
retired `resolve_new_session` returned "not fresh" for `-R` regardless of the stored key, so a
picker-less `-R` yielded `"continue"`. If `-R` installed nothing, a box with a stored
`continue_mode: false` would flip to `"start"`.

**`image`** (`--image`, B6/R-11a(a)): a non-empty string installs `box.image`. `None` or `""` installs
NOTHING (absent ≠ `""`, same rule as `model`). BOX-scope — installed independently of `active_agent`,
because a box selects its rig whether or not an agent launches (`kanibako shell` included).

**`share_images`** (`--share-images`): `True` installs `box.share_images = True`; `False` installs
NOTHING. The flag is argparse `store_true` — there is no negative spelling — so an un-given flag
arrives here as `False` and must mean ABSENT (the stored value stands), never an explicit override to
`False`.

## The guard

`guard_cli_level` refuses an illegal CLI-level key, NAMING it (spec §1A, §0). It is called from
INSIDE `kanibako.settings.settings_launch.build_launch_snapshot`, before the splice, so no call site
can bypass it. It is a no-op for `None` / an empty level.

It refuses in this order, each arm reporting the key and the section that bars it:

1. **Closed keyspace** — anything `kanibako.settings.settings_keyspace.key_validity` rejects. §0: an
   undeclared key is an ERROR that names it, never a silent accept and never a fabricated default.
2. **Categorical** — `meta.*` / `config.*` / `pref.*` (`_FORBIDDEN_HEADS`).
3. **Locator closure** — `kanibako.settings.settings_prefs.LOCATOR_CLOSURE`. This is the arm §1A
   explicitly asks for.

### The agent NAMES are injected

`valid_agents` injects the agent-discriminator set for arm 1, and `active_agent` is UNIONED into it —
the active agent is valid BY CONSTRUCTION, having just been resolved by
`kanibako.settings.agent_select.select_agent`.

When `valid_agents` is `None` the set is exactly `{active_agent}`: every agent-scope key this module
builds is spelled against that same discriminator, so plugin discovery would be pure cost — a
flag-free launch keeps paying nothing, the same trade `settings_prefs.apply_prefs` makes. The union
is not a bypass: a key naming any OTHER agent is still refused.

### The leaf VOCABULARY is sourced, and that asymmetry is the point

Arm 1 also needs to know what an agent's leaves may be CALLED, and that question is not the caller's
to answer. This door is the TWIN of `config_keys.agent_key_reason`: names injected narrowly (there,
the on-disk store dir; here, the agent the launch just resolved), vocabulary read from
`config_keys.AGENT_LEAF_MAP`.

It was a PARAMETER until 2026-09-01 and **no caller ever passed one**, so the door judged every agent
against core's §2d table alone. `agent.goose.provider` — a real goose `setting_descriptor` leaf — was
refused at the CLI level while the config verb and the pref door both accepted it. That is a §0
breach in the STRICT direction, which is why it went unnoticed for as long as it did: the failure
mode is a user being told no. It was harmless only because `build_cli_level` emits nothing but core
leaves; it would have stopped being harmless the first time a plugin leaf got a flag, and a rule a
caller can forget is not a rule (P15 — the shape is now unavailable, not merely discouraged).

Two properties of the map are load-bearing, and neither survives being "simplified":

* **It is a thing to ASK, not a value.** `AgentVocabulary` consults core's table first and reaches the
  map only for a leaf core cannot answer, so `-M` and `-N`/`-C`/`-R` still import no plugin. Handing
  the guard a materialised map instead would change no verdict and restore the whole cost silently
  (measured at 73% of a settings resolve) — `test_a_CORE_leaf_costs_no_plugin_discovery` is the only
  thing that would notice.
* **It CONCEDES rather than refuses** what discovery could not read (`[R150]`, spec §0: *"Where an
  agent's vocabulary cannot be read — its plugin is not installed — the leaf is CONCEDED, never
  refused."*). A bare `{}` or a
  `getattr`-with-a-fallback here would swap the strict signal for the permissive one in one
  direction and, for an uninstalled agent, refuse a leaf that names a real value in the other.

⚑ The vocabulary is per-AGENT, never a union: goose declaring `provider` does not make
`agent.claude.provider` a key. That partition is `[R150]`'s content and `key_class` enforces it; this
door only has to hand it the map.

### The dotted-key split

⚑ A dotted key is split on `.`, so an agent NODE whose name contains a dot (`a.b℘claude`) is refused
by arm 1 rather than silently mis-parsed. As of 2026-08-04 such a node can no longer be CONSTRUCTED —
`agent_ref` rejects `.` in a persona/harness segment for exactly this ambiguity — so this arm is now
defence in depth against a node that reached here by some other route, not a live case. It stays
because it is free and because every dotted-key builder in the launch (`agent_defaults`,
`meta_agent_path_floor`, `dotted_partial`) splits the same way and would never re-find the node;
refusing loudly beats mis-resolving silently.
