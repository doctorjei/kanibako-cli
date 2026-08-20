# Descriptor Assembly — turning a PluginDescriptor into an argv, an env overlay, and mounts

`targets/assembly` is the data-driven core of the descriptor-only plugin system. Given a plugin's
`PluginDescriptor` (`targets/base.py`), the resolved host `AgentInstall`, and a handful of
per-launch knobs, it assembles the agent argv, the container env overlay, and the host->box
mounts. It superseded the per-plugin `build_cli_args` / `binary_mounts` hooks, which were removed
for the public release: divergent LOGIC stays behind the plugin `Target` hook methods, while
everything a plugin merely DECLARES flows through here.

Every function is agent-agnostic — no plugin names appear anywhere in the module — and everything
is pure and side-effect-free apart from filesystem *existence* checks in `descriptor_mounts` and
`resolve_binding_source`, which only call `Path.exists()` and never read, write, or mutate
anything.

## Where it sits in a launch

LIVE: the module is wired into `commands/start.py` for every descriptor-bearing target, which
today means all three first-party agents.

* `resolve_mode` + `assemble_argv` build the launch argv.
* `assemble_env` + `env_realization_drivers` say which variables the descriptor REALIZES and
  which settings key drives each; the launch turns them into agent-scope settings keys (MBR-1
  P4c-2).
* `descriptor_mounts` emits the delivery binds.

The only descriptor-less target is `NoAgentTarget`, the `kanibako shell` fallback. It launches a
plain shell with no agent argv and no delivery binds.

## `BindingSourceError`

Raised by `descriptor_mounts` so the caller can fail fast with a clean, actionable kanibako error
instead of letting the runtime (crun) crash on a dangling bind source. It is the declarative
replacement for `start.py`'s existing "mount source disappeared before launch" safe-fail.

## The entrypoint / argv split

The descriptor's `command` is the full box argv prefix (for example `("claude",)`). Its first
element is the program podman launches via `--entrypoint`, and that is what `entrypoint()`
returns. The remaining elements, `command[1:]`, are agent args and are produced by
`assemble_argv`.

So the list `assemble_argv` returns is everything passed to the container AFTER the
`--entrypoint` program: it EXCLUDES `descriptor.command[0]` and starts at `descriptor.command[1:]`.
This mirrors how `start.py` uses `cli_args` after setting `--entrypoint` separately, and
corresponds to the old `build_cli_args + state_args` tail.

## `resolve_mode` — selecting the interactive launch mode key

The function lifts what used to be claude's `build_cli_args` logic into agent-agnostic form.
*available_modes* is the set of mode keys the agent's launch grammar declares, read off the
snapshot's `meta.agent.<a>.mode` table (B5, spec §2d), where the descriptor materialized it.

Resolution order:

1. `-R` resume picker: if *resume_mode* and `"resume"` is available -> `"resume"`.
2. `skip_continue` is true when a new session was forced (*new_session* / *is_new_project*) or
   the user passed `--resume` / `-r` in *extra_args*.
3. If not *skip_continue* and `"continue"` is available -> `"continue"`.
4. Otherwise -> `"start"`.

A descriptor that declares only `{"start", "continue"}` (no picker) makes *resume_mode* fall
through past step 1. With no new-session or `--resume`-in-extra forcing, step 3 then yields
`"continue"` (continue-last) — the sane mapping for an agent that has no dedicated resume picker,
such as goose or codex.

## The access tiers — `resolve_access_tier` and `effective_access`

`resolve_access_tier` validates a CASCADE-resolved `access` value into a permission TIER. `None`
(unset) yields `access_default()` — `full`, R-41's ruled default: today's behaviour preserved,
because the box is the containment boundary. That default is DECLARED in `core-defaults.yaml` and
is not spelled in this module. Anything else must be a member of `ACCESS_TIERS` EXACTLY.

⚑ An unknown value RAISES, naming the key and the legal values. It is NEVER coerced and NEVER
falls back to the default: on a permission axis a typo must not decide whether the agent prompts.
The old boolean read did exactly that — `coerce_bool("flase")` returned `None`, which selected the
permissive default — which is why the set-time guard exists. That guard stays; this validation is
a SECOND FENCE for a value that reached the file some other way, such as a hand edit.

⚑ The EMPTY STRING is not unset. It is an INVALID VALUE and it refuses like any other; only `None`
— the key absent from the cascade — takes the default. Both set paths already refuse `""`, so the
only way it reaches this function is the hand edit described above, which is exactly the case the
second fence exists for. Treating it as unset would make the ONE reachable route to this
function's permissive arm a route the validators never approved (R-41: an unknown stored value is
rejected, never treated as permissive).

`effective_access` returns the tier this launch's ARGV/ENV should run at, folding the per-launch
flags over the cascade (spec §2d + §1A; R-41 replaced the old boolean `effective_safe_mode_off`):

* *secure* (`-S`) -> `"restricted"` — wins over everything.
* *autonomous* (`-A`) -> `"full"` — the per-launch override.
* else the cascade-resolved *access* key, defaulting to `full` when unset, via
  `resolve_access_tier`, which REFUSES an unknown value.

⚑ Those flags are EPHEMERAL and apply to the launch argv/env ONLY. The PROJECTED surfaces — claude
`settings.json`, codex `config.toml`, goose `config.yaml` — resolve the tier from the CASCADE
alone. That is spec §1A's projected-surface exception, because a projection outlives the launch.
Both values therefore exist at a launch and they are deliberately NOT the same read.

## `access_row` — the un-rendered-tier rule and version skew

`access_row` returns the descriptor's realization of a tier, or `None` when the descriptor declares
no `access_realization` at all (an agent with no permission surface).

It RAISES when the descriptor HAS an `access_realization` but cannot render the requested tier.
That is the un-rendered-tier rule: the launch stops and names the tiers this agent CAN render,
rather than substituting a neighbouring one. Never silently permissive, never silently stricter.
The worked case is goose's missing `editing` tier: substituting `auto` would over-permit, while
substituting `approve` would deliver prompt-on-every-edit while reporting success — both are lies
about what the user asked for.

⚑ A descriptor that declares an `access_realization` with ZERO rows is diagnosed SEPARATELY as
PLUGIN VERSION SKEW, because that is what it actually is. A harness with a permission surface
always realizes at least one tier, so no rows means the block was parsed from a plugin that
predates the `access` tiers: the retired PRE-TIER BODY (`flag` / `secure_flag` with no `tiers:`)
loads to an empty `AccessRealization`. Reporting "this agent cannot render that tier" there would
blame the agent for an install problem and send the user looking for a capability limit that does
not exist. The refusal message instead tells the user to upgrade the `kanibako-agent-*` packages
to match the base, since they are released together.

⚑ SPELLING, old versus new: the block is named `access_realization:` in this release; it was named
`safe_bypass:` before. A descriptor still using the OLD KEY never reaches this function — it is
refused at descriptor load by `settings/agent_defaults.load_descriptor`, because an unknown
descriptor key would otherwise be ignored and leave the agent with NO permission surface at all.
So the only pre-tier shape that survives to this point is the old BODY under the NEW key, which is
what the version-skew message describes.

## Why `resolve_new_session` is gone

`resolve_new_session` was DELETED in P8 (v1.8.0). Its whole body was the fold *"the per-launch
`-N` / `-C` / `-R` flags over the persisted `continue_mode` key"* — one hand-rolled precedence
chain for one flag family.

Spec §1A makes the COMMAND LINE its own LEVEL, the highest, so that fold now happens ONCE,
declaratively, in `settings/settings_cli_level.build_cli_level`: `-N` implies `continue_mode`
False, `-C` / `-R` imply True. The launch simply reads the resolved key —
`effective_new_session = not continue_default`.

⚑ Do NOT reintroduce it. Two places folding the same flags from two different inputs is the "two
forms that mean the same thing" failure, and the second one would be the one nobody tested.

`resolve_mode` still takes the raw *resume_mode*, because `-R` selects a launch GRAMMAR (the
resume mode fragment), which is not a key.

## `assemble_argv` — build order

⚑ B5 (spec §2d): the launch-grammar fragments are PARAMETERS, not descriptor reads. The live
caller reads them off the ONE launch snapshot — `meta.agent.<a>.mode[mode_key]` for
*mode_fragment* and `meta.agent.<a>.exec` for *op_fragment*, via
`settings/settings_launch.meta_agent_grammar` — where the descriptor MATERIALIZED them
(`settings/settings_launch.meta_agent_grammar_floor`). This function must NOT read
`descriptor.mode` / `descriptor.operations`: the descriptor feeds the KEYSPACE only, and a second,
descriptor-direct source for the same argv fragment is the drift shape B5 exists to kill.

Build order, after `command[1:]`:

1. If *op_fragment* is set: the standalone operation fragment (`meta.agent.<a>.exec`); NO
   interactive mode is added — the two are MUTUALLY EXCLUSIVE at this argv slot (spec §2d).
2. Else if *mode_fragment* is set: the interactive mode fragment
   (`meta.agent.<a>.mode[mode_key]` for the resolved *mode_key*).
3. If the descriptor's `access_realization` is FLAG-channel: emit the `flag` of its row for the
   *access* TIER (R-41). An EMPTY row emits nothing — that is the claude/codex `restricted`
   realization, since their own default already prompts. A MISSING row RAISES (`access_row`, the
   un-rendered-tier rule), so no tier can ever fall through to a different tier's emission.
4. For each FLAG-channel `SettingArg` WITH A `flag` whose value in *setting_values* is truthy:
   `flag + [value]`.
5. *extra_args*, appended last.

⚑ The `and s.flag` test in step 4 is the exact twin of `assemble_env`'s `and s.env_var`, and
exists for a sharper reason. Without it a flagless FLAG entry extends by `()` and then appends the
value, so the value lands as a BARE POSITIONAL — for claude, the initial PROMPT. A setting's value
silently becoming the text the agent is asked to act on is far worse than the value going
undelivered, so the emission is withheld. The DECLARATION itself is refused one level up, at
descriptor load (`settings/agent_defaults._build_setting_arg`), which is where a plugin author
gets told the file and the field; the guard in this module is the containment for a
`PluginDescriptor` hand-built in code, which never passes through that loader.

*access* is the tier this LAUNCH runs at, `-S` / `-A`-folded — see `effective_access`. *agent*
names the agent in the refusal message. ENV-channel access rows and settings are NOT argv and are
emitted by `assemble_env` instead.

## `assemble_env` — realizations only

⚑ REALIZATIONS ONLY. A plugin's STATIC variables are settings keys (`agent.<agent>.env.<VAR>`,
`Target.default_envs`) and are FLOOR values that need no launch to compute, so they never come
through here. What this function builds is the per-launch translation of RESOLVED values onto the
ENV channel:

* If the descriptor's `access_realization` is ENV-channel with an `env_var`: set it to the
  `env_value` of the row for the *access* TIER (R-41; goose `GOOSE_MODE=auto` at `full`,
  `approve` at `restricted`). An EMPTY row emits nothing; a MISSING row RAISES (`access_row`).
  ⚑ For an agent whose UNSET env default is itself permissive — goose's `GOOSE_MODE` defaults to
  `auto` — every renderable tier must carry a value. Emitting nothing there would BE the bypass,
  which is why the un-rendered tier refuses instead of falling through.
* For each ENV-channel `SettingArg` with an `env_var` and a truthy value in *setting_values*: set
  `env_var` to that value.

FLAG-channel access rows and settings are argv and are emitted by `assemble_argv` instead.

⚑⚑ THE RETURN VALUE IS NOT APPLIED TO ANYTHING (MBR-1 P4c-2). It used to be pasted onto the
finished container env. The launch now installs it as `agent.<node>.env.<VAR>` KEYS before the
collapse, so these variables are arbitrated, overridable and refusable like every other one. This
function stayed PURE through that move and must stay pure: it says what a descriptor realizes, and
nothing about where the answer goes. See `commands/start._install_realized_env`.

## `env_realization_drivers` — the twin walk

This is the DECLARATION map: every variable the descriptor can realize, mapped to the setting key
that DRIVES it. It is unconditional where `assemble_env` is conditional — it names every variable
that function *could* emit, whatever this launch resolved.

⚑ It exists because the emitted `{var: value}` map cannot carry provenance, and a caller that has
to say WHY a variable is set — the launch's refusal when a settings key names a realized variable
— needs the key that produces it, not the value.

⚑⚑ IT IS THE TWIN WALK OF `assemble_env` AND MUST STAY BESIDE IT. Both read the same two
declaration sites in the same order: the ENV-channel `access_realization`, then the ENV-channel
`SettingArg`s. A variable one of them knows about and the other does not is a variable that either
reaches the box unexplained, or is explained but never set.

A row's setting key may be EMPTY — an `access_realization` driven only by the per-launch `-S` /
`-A` flags. It is carried through as the empty string rather than dropped: the variable IS
realized, and a caller reporting it needs to know there is no key to point the user at.

## `resolve_binding_source` — where a binding's host source comes from

No existence check happens here; that is `descriptor_mounts`'s job.

A non-empty *override* always wins and is returned as `Path(override)`. It is a caller-supplied
host source repoint; the user-cascade equivalent is `agent.<name>.bindings.{ro,rw}.<key>`,
hand-authored in the node's settings file and resolved upstream in the launch snapshot. R-9
retired its CLI set route, so the parameter is now used by tests only.

Otherwise the source is derived from `binding.origin`:

* `LAUNCHER` -> `install.launcher`, falling back to `install.binary`.
* `INSTALL_DIR` -> `install.install_dir`.
* `BINARY` -> `install.binary`.
* `LITERAL` -> `binding.literal_src`.

`None` is returned when the source cannot be resolved.

## `declares_box_dest` — a declaration-level predicate

True when the descriptor declares a delivery binding at a given box_dest.

It asks the DECLARATION-level question — "does this plugin deliver something to that box-side
path?" — and it is asked of the box_dest rather than of a key NAME on purpose. The thing that
collides in a box is the DESTINATION: spec §0's identical-dest table errors on two concrete
bindings at one dest, and a plugin is free to name its key whatever it likes. A key-name test
would pass a third-party plugin's identically-destined binding straight into that error.

Declaration-level, not resolution-level: it does not touch the filesystem and does not care
whether the source resolves. That matches
`settings/agent_representation.agent_default_partial`, which represents a binding in the launch
snapshot with no existence check — so a caller gating on this predicate sees exactly what the
snapshot will carry.

*descriptor* may be `None` (the no-agent target has none), which answers False. *box_dest* must be
the ABSOLUTE guest path: descriptor box_dests are `$GUEST_HOME`-expanded by the defaults loader,
so a `~`-spelled dest never matches and callers must expand first.

Sole caller today: `settings/core_defaults.kickoff_default_categories`, the P-5 transition gate.

## `descriptor_mounts` — the delivery binds

For each `Binding`, in descriptor order:

* Its host source is resolved via `resolve_binding_source`, with any per-key value in *overrides*
  taking precedence.
* `AGENT_CRITICAL` (binary / launcher / share): the source MUST resolve and exist, else
  `BindingSourceError` is raised — the clean safe-fail that replaces a crun crash on a dangling
  bind source. It is then bound as-is; podman inode-pins it at mount time.
* `AGENT` (best-effort): a source that is unresolvable or missing is skipped with a debug log,
  since a missing or suppressed agent share is fine; otherwise it is appended. No shipped plugin
  currently declares an AGENT binding — agent-scope shared dirs flow through the category
  resolver — but the branch is kept for the general binding contract.

Mount options are `"ro"` when `binding.ro` is set, else `""` (rw).

⚑ Clearing any pre-existing dest symlink in the box is the CALLER's job
(`start._precreate_mount_stubs`), not this function's.
