# Changelog

All notable changes to kanibako are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

> Releases before v1.3.0 are not yet backfilled here. For their notes and full
> changelogs, see the [GitHub releases](https://github.com/doctorjei/kanibako-cli/releases).

## [Unreleased]

### Added

- **A persona endpoint that is not a well-formed URL is refused at the store boundary, naming the
  file and the cure.** Kanibako checked only that an endpoint was *present*; a scheme-less value
  such as `myhost:8080/v1` passed create-side preflight and every launch gate, reached the harness
  verbatim, and died inside it with an opaque `API Error: Invalid URL`. The network probe did not
  catch it either — a malformed URL fails at the transport layer, which the probe folds in with
  "the server is briefly down" and treats as inconclusive, i.e. warn-and-proceed. The endpoint is
  now validated where the persona store's harness-native config becomes a cascade value, so both
  `create` and `launch` refuse it from one place. The check is deliberately minimal — a recognised
  scheme (`http`/`https`) and a non-empty host, and nothing about path, port or query — because a
  persona endpoint is a base URL the harness appends its own routes to, and a false refusal here
  would break a working box.

- **A persona's bearer token — and now its model — may be declared explicitly absent, for an
  endpoint that genuinely needs neither.** `agent.<node>.secret_path.<VAR>` and
  `agent.<node>.model` used to be two-state: configured, or not. A self-hosted endpoint that
  requires no bearer token could not be expressed at all — every launch gate refused it as if the
  key had simply been forgotten, and the only workaround was a dummy token file the gate never
  actually read. Both keys are three-state now: unset still refuses exactly as before, a real value
  still works unchanged, and an explicit `null` (`kanibako system set --null
  agent.<node>.secret_path.<VAR>`, or the equivalent hand-edit) means *this endpoint needs none* —
  the launch proceeds with nothing mounted, nothing exported, and the persona verify probe still
  runs, sent with the credential or the model field simply omitted so the server itself decides.
  The two keys differ in one respect: a config-file harness (codex) generates a provider block that
  cannot express "no model" at all, so a null model there is refused as a declared conflict between
  what the persona asks for and what the harness can deliver, naming both; an ENV-delivery harness
  (claude, goose) has no such limit, and a null model there simply suppresses the harness's own
  "a model is required" default.

- **`kanibako system defaults` lists every default kanibako ships, and says where each one is
  declared.** `show --effective` resolves the cascade but never names an artefact — it can mark a
  value you stored `(override)`, but nothing it prints tells you where a default is written down.
  The new command answers the other half: one line per shipped
  default with its **key, value, scope and the file that declares it** (`core-defaults.yaml
  (agent_default:)`, `paths_defaults.py (system tier)`, `goose plugin defaults (env:)`, and so on).
  It is install-wide and static — it takes no box, resolves nothing, reads none of your settings,
  and works before `kanibako setup` has ever run. Three sections: the 65 declared keys, the 33 bind
  and copy entries (internal ones included and marked, since a box gets them too), and the
  environment variables, which are gathered from kanibako's own defaults file plus every agent
  plugin you have installed — the footer names the agent targets consulted and which of them
  declared anything, so a cli-only install (which still carries kanibako's own `no_agent` target)
  reads as *nothing declared* rather than as *no variables exist*. The `KANIBAKO_*` variables
  kanibako derives per launch are deliberately not listed, because two of the four are not emitted
  at all for an unnamed project or an agentless box; the output
  says so and points at `kanibako box show --effective`.

- **Two rules were added to the handbook kanibako ships, and only NEW boxes get them.** The
  bundled handbook template gained one rule in each of two files: `CANON.md`'s editing guidance
  now closes with **"An empty tome section is a slot, not cruft."** — it already said a directory
  that does not yet exist is not missing, but said nothing about reading one that is there and
  empty, which reads as cruft to tidy away. And `DATAPOLICY.md`'s git-safety list gained **"Never
  drop or clear a stash you did not create."** — it barred committing and pushing unasked and
  named the paths never to commit, but a stash holds work that is in no commit, and is often not
  the work of whoever finds it. ⚑ **The handbook is seeded once, at `create`.** Boxes you already
  have will never see either rule; only boxes created on this version and later carry them. To
  give an existing box the same text, copy the two lines into its own canon by hand — kanibako
  will not re-seed over a live handbook, by design.

- **`--image` and `--share-images` are cascade-level entries, and a `create` that did not name
  one no longer pins it.** Both flags join `-M` and `-N`/`-C`/`-R` in the declared flag-to-key
  table (see **[1.8.0]**, *The command line is its own cascade level*): `--image` spells
  `box.image` and `--share-images` spells `box.share_images`, installed at the command-line level
  for that launch and nowhere else. Two consequences you will see. **First, `kanibako create`
  with no `--image` now writes nothing.** It used to resolve the default rig and bake the
  resolved name into the new box's own settings file, so every box was pinned at creation to
  whatever the default happened to be that day. Only a flag you actually typed persists now —
  the one create-time exception to "a flag is ephemeral" — and `--share-images` persists the same
  way. ⚠️ `kanibako box get <newbox> box.image` prints `(not set)` for a box created without the
  flag, and it genuinely is not set: that box follows the host-wide default and moves when the
  default moves, while every box created by an earlier version keeps its stored value and stays
  on the rig it was born with. Nothing in the CLI distinguishes the two; read the box's settings
  file if you need to know which kind you have. **Second, passing either flag at a box that
  already exists now says so.** `kanibako start <box> --image X` has always applied the image to
  that launch alone and left the box's stored setting untouched — silently, which reads exactly
  like a setting that took. Every such launch now prints a `Notice:` naming the flag or flags you
  passed, saying the box's stored image or image-sharing setting is unchanged, and giving the
  exact `kanibako box set <path> box.image=X` that would persist it. One notice however many of
  the two you passed, and the box's *path* is the subject of that cure rather than its name,
  because a `box set` with only a `key=value` applies to whatever box your shell is standing in —
  and a standalone box cannot be addressed by name at all.

### Changed

- **A leaf only one plugin declares is no longer a key on the all-agents tier, and a plugin's own
  floor now lands under the agent that declares it.** `agent.default.*` is the universal
  vocabulary — a leaf there is a key on every agent — so a leaf only goose declares, `provider`,
  was never meant to live at that tier. The spelling is `agent.goose.provider`. Two things follow.
  A refusal for an undeclared `agent.default.<leaf>` now lists core's own table rather than the
  union of every installed plugin, which at that tier is the honest list. And a plugin's declared
  defaults are keyed under the agent that declared them, which on a goose box removes a
  resolve-time refusal that — sitting behind the shared settings load — reached very nearly every
  kanibako command, not just `start`. ⚠️ **If a settings file of yours carries
  `agent: default: provider:`, move it under `agent: goose:`** — see `MIGRATION.md`,
  *`agent.default.<plugin-leaf>` is no longer a key*. Core-declared leaves — `model`, `endpoint`,
  `transform`, `access`, `allow_helpers`, `bootstrap`, `continue_mode`, `run_args`, `template`,
  `canon`, `transform_settings` — are unaffected at `agent.default`, including where a plugin
  declares one too. Precedence is unchanged: a settings file at any scope still outranks a
  floored value.

- **A setting an agent plugin declares is a key on that agent alone, at every door.** The rule the
  entry above applies to the all-agents tier applies to the named tier too: every leaf other than
  the universal ones is legal only under the agent — or harness — whose plugin declared it.
  Kanibako pooled the installed plugins' declarations into one list and judged every agent against
  all of it, so goose declaring `provider` made `agent.claude.provider` a key that nothing read.
  The vocabulary is now supplied per harness, which also closed two faults a pooled list could not
  express. The gate that reads an agent's own settings file could not concede at all, so a goose
  box on a machine where the goose plugin had been removed refused to start — measured, and the
  concession has always been the documented behaviour everywhere else. And the surface deciding
  where an `agent.default.<leaf>` value is *stored* still read the pooled list after the classifier
  had stopped, so the two disagreed. Four surfaces judge this question and none of them had a test
  asking two of them about one key; there is one now. A refusal also names the agent and lists that
  agent's own vocabulary, rather than offering a cure drawn from a plugin you were not using.
  ⚠️ **If a settings file of yours sets `provider` under an agent other than goose, move it** — see
  `MIGRATION.md`, *A plugin's setting is a key on its own agent, and on no other*. Core-declared
  leaves are unaffected on every agent, including where a plugin declares one too.

- **`diagnose` now quotes a settings error once and names every check it broke.** Each settings
  load reports its own failure, and that half is deliberate — a check that swallowed the reason
  would be back to a bland `cannot check`. But each one also quoted the entire error underneath its
  own line, and one root cause routinely reaches several checks: `rig diagnose` resolves settings
  twice, so a single undeclared key printed the §0 refusal at both `Configured image` and
  `Baseline` — twelve lines for one problem — and one malformed config file broke Image, Storage
  and Journal in `system diagnose`, printing the same parse error three times. Every failing check
  still prints its own `[!!]` line; what moved is the detail, which now lands in one
  `Settings errors:` section at the end of the run, one entry per distinct error, each naming the
  checks it affected (`affects: Configured image, Baseline`). Two errors in two different places
  still print as two entries — entries are grouped by the error text, and both kinds of settings
  error already name their own file and location. A run with no settings error prints no section
  at all.

- **A path setting written as a bare relative path is now refused instead of being anchored
  somewhere.** `workset.channelroot: comms` used to mean *under the workset root* for the workset
  directory keys, and *under whatever directory you happened to run the command from* for every
  other path key — two answers to one question, and neither of them stated anywhere you would see
  it. Both readings are defensible, and that is exactly the problem: the reason to set one of these
  keys at all is to move the directory *off* its default, so "keep it with the workset" assumes the
  very intent you are overriding. A wrong guess here does not produce a confusing message; it
  produces a directory that gets created in the wrong place and then holds your data. Every
  path-typed key now requires a value that says on its own where it points — an absolute path,
  `~/…`, `$XDG_*/…`, or an `@`-ref — and a bare relative is refused with **both readings spelled
  out**, so you can see the two directories and say which one you meant. The root-relative reading
  is still expressible: `@meta.workset.path/comms` *is* it, said out loud. This also closes a mount
  hole, because a bind source such as `@box.canon/handbook` is self-resolving where it is declared
  and only becomes relative once the key is read — and podman reads a mount source beginning with
  neither `.` nor `/` as the name of a *named volume*, so a box would have quietly received an
  empty volume in place of the directory you pointed at. ⚠️ **If you had set one of these keys to
  a bare relative path, kanibako now refuses rather than guessing** — see `MIGRATION.md`, *A bare
  relative path in a settings key is refused*, for the four legal spellings and how to pick one.

- **…and `set` refuses it too, so the value never reaches the file.** The refusal above is what
  you get when kanibako *reads* a settings file, which is the right place to catch a value you
  hand-edited in — but it meant `kanibako workset set workset.channelroot=comms` still succeeded
  at exit 0, wrote the ambiguous value, and only failed the next time anything read it. Every set
  route now refuses first: `system set`, `workset set`, `box set`, `agent set` and a
  `pref.<target>` request naming a path key all exit **1** with the same message naming the same
  two readings, and nothing is written. `secret_path.<VAR>` is covered at every scope. ⚠️ **A
  script that sets a path key to a bare relative path will now stop at that line instead of
  carrying on** — and the same script fixed to the `@`-ref spelling works, which it did not
  before: `@meta.workset.path/comms` and `@meta.box.path/canon` are the spellings this refusal
  offers as the cure, and `set` used to reject them as dangling references because the set-time
  check could not see the workset or box root. It can now.

- **Repointing `workset.boxes` or `workset.logs` now actually moves the box store and the helper
  logs.** Both are settings you have always been allowed to write, and until now kanibako only
  half-honoured them. It would *find* a workset whose store you had moved — the directory walk that
  identifies a workset root resolved both settings correctly — and then create, move and delete
  that workset's box trees under the default `boxes/` anyway, and write each box's helper log under
  the default `logs/`, because the code that composed those paths spelled the directory names by
  hand instead of reading your setting. The result was the quieter kind of broken: the workset was
  recognised, so nothing looked wrong, and the files simply went somewhere else. Every path that
  creates, moves, duplicates, converts, purges or removes a box tree now reads the setting, and so
  does the primary workset's own store and log root and the helper-log writer — in all three modes,
  standalone included, whose log directory is the box's own `box_data/` by default and follows the
  setting when you move it. Forking a box that is not in the workset's membership also stopped
  recognising the store on a repointed workset, and so silently gave the fork no source metadata;
  it now compares against the resolved directory rather than the name `boxes`. ⚠️
  **If you had repointed either setting, kanibako has been writing to the default directory all
  along** — see `MIGRATION.md`, *A repointed `workset.boxes` or `workset.logs` now takes effect*,
  for how to tell and what to move. Two limits are stated rather than fixed, both of them a purge
  declining to follow a path you pointed outside the tree it owns: box trees under a
  `workset.boxes` you pointed *outside* the workset root survive `kanibako workset rm --purge`,
  which deletes the root and nothing beyond it; and a standalone box's helper log, once repointed
  out of `box_data/`, survives `kanibako box rm --purge`, which removes `box_data/` whole. Both are
  yours to delete by hand.

- **`agent reset default <key>` now refuses instead of quietly reporting nothing to do.** `default`
  is the reserved any-agent tier, not a persona you can name, and `agent set default model=...` has
  refused it for a while — but the matching `reset` still accepted it, printed `No override for
  <key>` and exited 0, whether or not a store directory of that name existed. It now exits 1 and
  says which spelling to use instead. This is the last verb that addressed the per-agent settings
  file on its own rather than through the shared setter, so the two halves finally agree; every
  other `reset` message is unchanged.

- **A `synced` destination that no mount covers now refuses the launch instead of being copied
  into nowhere.** A `synced` entry is applied last, after the mount set is final, and it resolves
  *through* the mount containing its destination — that is what puts the file where the box can see
  it. With nothing bound at or above the destination there is no such mount, so the copy went into
  the container's own ephemeral storage and vanished the moment the box stopped. Kanibako logged one
  warning and carried on. That is the wrong trade for this category: a `synced` entry is a
  credential more often than not, so the old behaviour handed you a box that started cleanly and
  then failed to authenticate *inside* the agent, naming nothing you could act on. It is refused at
  assembly now, before a single row is written, and the message names the source, the destination
  and the cure. **The fix is a binding**: declare `box.bindings.rw` (or any mount) at or above the
  destination and the copy lands on the host and persists — or move the destination under `~`, which
  is always covered, because the home binding is the foundation every box is built on. Two things
  are deliberately unchanged: a `synced` destination that sits *at* a binding's exact path is still
  accepted and still writes through into that binding's source, and a destination covered by a
  `masks` entry is still judged by the mask rules that already existed, not by this one.

- **A launch refused for two mounts at one destination now names the settings KEY behind each
  participant, not just their sources and paths.** All four of the collapse's mount refusals —
  a binding over a binding, a binding inside a mask, a mask on a mask, and a mask at or above home
  — told you *what* collided and *where*, and then told you to set the unwanted key to null. They
  never said which key. On a box whose declarations come from four scopes and an agent node that is
  often somebody else's file, that left you to find the offending entry by grepping for a path. Each
  refusal now reads `the binding declared by 'box.bindings.rw./home/agent/x' … collides with …
  '/home/agent/x' ('/h/sys' declared by 'system.bindings.rw./home/agent/x')`. The mask-on-mask case
  gains the most: neither participant has a host source, so before this it named two bare
  destinations and nothing you could match to a file you had written. ⚑ One refusal names a key for
  only one side, and now says why: nothing declares the home binding — it is the foundation the box
  is built on — so there is no key to suppress on that side.

- **`kanibako workset share list --effective` and `kanibako box show --effective` now name the mask
  that swallowed a declaration, by its settings key.** When a mask covers a destination, both
  listings print the declaration in declaration form with a reason beneath it, and that reason could
  only give the mask's destination: *"the mask at /opt/x covers this destination"*. A mask has no
  host source, so unlike every other loss there was nothing in the line to match against a key you
  had written — and where the mask sits **above** the declaration, `/opt/x` is not even a path your
  own key spells, so the row named nothing you could act on. It now reads *"the mask declared by
  'workset.masks.~/x' at /home/agent/x covers this destination"*: the key is the thing to go and
  edit, and it is the key of the mask that actually **survived** the collapse, not merely one
  that names that destination. ⚑ The same holds where a **binding** takes a destination from
  another declaration, and in both halves of `box show --effective`'s category block.

- **The agent liveness-marker hooks are a script call now, not a line of shell inside your
  config.** Kanibako seeds a `SessionStart` and a `SessionEnd` hook into a box's
  `~/.claude/settings.json`, and a `SessionStart` hook into its `~/.codex/config.toml`, so the
  in-box supervisor can tell a live agent session from a dead one. Each used to be a compressed
  line of shell — `d="${KANIBAKO_AGENT_MARKERS_DIR:-/tmp/kanibako/agents}"; mkdir -p "$d" && …` —
  sitting in a file you read and hand-edit. They now call
  `~/canon/bible/general/scripts/util/pid-add.sh` and `pid-rm.sh`, which kanibako already ships
  and already binds into every box, codex included. One behaviour widens with the move: those
  scripts also maintain `/tmp/kanibako/agent.pid`, the shared pidfile a box's own hooks already
  keep, which the inline shell never touched. ⚑ **On a box that predates this change the old
  inline hooks stay in your `~/.claude/settings.json`** — kanibako identifies its own hook by the
  exact command text, so the previous spelling is not one it knows to remove. Both fire, both
  write the same marker, and nothing misbehaves; delete the two groups whose command starts
  `d="${KANIBAKO_AGENT_MARKERS_DIR` if you would rather not carry them. A codex box needs
  nothing — its managed region, hook and trust hash together, is regenerated at every start.

- **BREAKING: a `.` is no longer legal in an agent or persona name.** Agent names admitted
  letters, digits, `-`, `_` and `.`; a dot is now refused in every segment of an agent ref, and
  the refusal says why rather than only listing what is allowed: `.` is the settings key-path
  separator, so a node spelled `kimi.k3` could not be addressed as `agent.kimi.k3.model` without
  the keyspace reading it as three segments. ⚠️ **An agent node you already have with a dot in
  its name stops working at every command that parses an agent ref** — `start`, `create`,
  `--agent`, `agent get`/`set`, `reauth` — with an error naming the ref. There is no automatic
  rename: choose a dot-free name, rename the store directory under `<data>/agents/` to match,
  and update any `pref.system.agent` or `system.agent` that named the old spelling.

- **BREAKING: a persona's store directory is now named `<persona>+<harness>`.** A persona node
  has two spellings — `navigator+claude`, which you type, and `navigator℘claude`, which kanibako
  uses inside a settings key. Only the second can appear in a key: a key path is split on `.`
  into name segments and `+` is not one of them, so `agent.navigator+claude.model` would be read
  as the key `agent.navigator` and quietly resolve the wrong thing. That reason applies to keys
  and to nothing else, yet the directory carried it too — v1.7.2 put a persona's own store at
  `<data>/agents/navigator℘claude/`, a path you cannot type without pasting a character that is
  on no keyboard. Every place kanibako composes that directory now writes the `+` form: the
  agent file, the per-node `canon` and `template` stores, and the symlinks that share the
  harness's plugins and cache. **Key names are unchanged** — `agent.<node>.*` still canonicalises
  to `℘` internally, and both spellings still work on the command line and reach the same store.
  One key's *value* moves with the directory it names: `meta.agent.<node>.name` now reads
  `navigator+claude`, which is what the spec's own formula
  (`meta.agent.<node>.path` = `@config.agents/@meta.agent.<node>.name`) requires of it.
  ⚠️ **If you already have a persona store, rename its directory before your first launch on
  this version**: `mv '<data>/agents/<persona>℘<harness>' '<data>/agents/<persona>+<harness>'`.
  Every store path is create-if-absent, so nothing will complain — kanibako makes a fresh empty
  directory beside the old one and the persona starts over with no settings and no canon.

- **`$KANIBAKO_AGENT` inside a persona's box now reads `navigator+claude`, not `navigator℘claude`.**
  The variable is how an agent learns which agent it is — kanibako's own shipped directive tells it
  to read `$KANIBAKO_AGENT` and never to guess — so the value it finds should be the one a person
  writes. v1.7.2 stamped the internal spelling instead, which meant the one place the separator was
  guaranteed to be seen by a reader was the one place it had no business being. An environment
  variable is not a settings key, and `℘` exists only so a node can sit inside a key path.
  Everything kanibako reads back off the stamp — `kanibako stop`'s credential writeback, `kanibako
  code`'s extension seed, the credential watcher, and a `kanibako start` that reattaches to a
  running box — converts it back before using it, and accepts either spelling, so **a box already
  running when you upgrade keeps working unchanged**. A bare agent such as `claude` is unaffected:
  it has no separator to swap. ⚠️ **If your own scripts or in-box directives match
  `$KANIBAKO_AGENT` against a literal persona name, update the separator** — or match on the
  harness alone (`${KANIBAKO_AGENT##*+}`), which does not care.

- **A `box:` table at the system tier reaches every box.** `box.image`, `box.share_images`,
  `box.shell` and their siblings resolve through the settings cascade now, and the system tier is
  a level of that cascade — so a `box:` table in the system settings file
  (`<data>/global/settings.yaml`) is a host-wide default that every box inherits and any box,
  workset or command-line flag can override. Until now the launch read those values off the
  bootstrap config object instead, so the cascade's copy was assembled and then dropped: writing
  one at the system tier changed nothing, silently. `kanibako system set box.image=<rig>` is
  therefore a real machine-wide default now, where before it was a value you could store and read
  back and never see take effect. ⚑ This is the settings file, not `kanibako_config.yaml` — a
  `box:` table left in *that* file is dead (see *Fixed*, `kanibako_config.yaml` holds bootstrap
  paths and nothing else).

- **Image sharing follows a `box.images_store` you set, even when the host probe fails.**
  `box.share_images` used to be gated entirely on the runtime probe finding a podman graph root:
  probe fails, no mounts, and a warning that blamed detection and offered nothing to do about it.
  The resolved store decides now — a value you set at any scope is enough — so a host whose probe
  fails but whose store path you know shares its images instead of silently going without. ⚠️ On
  such a host the box gains read-only mounts it did not have before; unset the key if you do not
  want them. When there is no set value *and* the probe fails, the warning names the key and the
  two cures instead of stopping at the diagnosis: `image sharing is enabled (box.share_images) but
  the host image store probe failed and no box.images_store is set. Continuing without image
  sharing. To share images, set box.images_store to the host store path or fix the podman storage
  probe.`

- **Three host-side state stores follow `config.data`'s directory name.** The pre-launch warning
  file (`launch-issues.<box>`), the shadowed-flag record (`launch-shadows.<box>`) and the
  `kanibako code --remote` connection store all lived under a hardcoded
  `$XDG_STATE_HOME/kanibako/`; they now sit under `$XDG_STATE_HOME/<leaf of config.data>/`, so a
  store that repointed `config.data` gets its own state instead of sharing one. **A default
  install is unaffected** — the leaf is `kanibako` either way, and nothing moves. ⚠️ **If you did
  repoint `config.data` to a differently-named directory**, the old files are not migrated:
  warnings recorded before this version are never surfaced, and saved `code --remote` contexts
  read as absent, so the next `kanibako code --remote` re-establishes the tunnel from scratch.
  Move `$XDG_STATE_HOME/kanibako/` to the new leaf name to keep them.

- **Two retired keys in an agent plugin's defaults file are refused by name at load.** Plugin
  descriptor keys are read individually, so an unrecognised one is simply never read — which for
  these two meant a plugin that loaded *successfully* and then behaved as though it had declared
  nothing. `safe_bypass:` (renamed to `access_realization:`, same shape) left the agent with no
  permission realization at all, so the launch emitted none and the harness ran at its own
  permissive default; `container_env:` under `descriptor:` left the agent with none of its
  required environment variables, which now belong in the file's top-level `env:` section where
  they become `agent.<agent>.env.<VAR>` keys a user can override by name. Both now raise at
  descriptor load, naming the file, the retired key, its replacement and what silence would have
  cost. ⚠️ **A plugin still spelling either key fails to load** — this is the load-time half of
  the plugin/base version pairing; upgrade the `kanibako-agent-*` packages with the base.

### Removed

- **`Target.apply_state()`, the last of the per-method launch hooks.** A target used to translate
  its agent-state values into `(cli_args, env_vars)` in Python: claude's turned `model` into
  `--model <value>`, goose's turned `provider` and `model` into `GOOSE_PROVIDER` and `GOOSE_MODEL`.
  The descriptor took that job over — a state key reaches the box as the `SettingArg` the plugin
  declares for it, carrying the value the settings cascade resolved — and core stopped dispatching
  the hook before v1.8.0, leaving a concrete method on the `Target` ABC that returned `[], {}` and
  that nothing called. Both translations still happen, from the descriptor: `claude-defaults.yaml`
  declares `model` on the flag channel as `--model`, and `goose-defaults.yaml` declares `model` and
  `provider` on the env channel. Nothing a box receives changes, and no exit code or printed line
  moves. ⚑ **If you maintain a target of your own and implemented `apply_state`, your translation
  was already not running** — the "For plugin authors" section of [MIGRATION.md](MIGRATION.md) says
  what to declare instead.

- **The launch-time notice about stale `env` files.** v1.8.0 stopped reading `<data>/env`, the
  workset's `env` and the box's `env`, and every launch checked all three and printed a stderr block
  naming any that still had content, with the cure for its tier. The reader it warned about was
  already deleted, so this was a migration notice and nothing more — and with no installed base to
  migrate, the file it looks for cannot exist. Launching no longer stats those three paths, and
  nothing is printed. ⚑ **If you are carrying a pre-1.8.0 `env` file, you now get no reminder:**
  [MIGRATION.md](MIGRATION.md) §2.19 lists the three locations and what to move where. Exit codes
  are unchanged — the notice was informational.

- **The pre-1.7 `kanibako/` → `boxes/` workset rename, and the `Migrated workset:` line it printed.**
  Loading a registered workset renamed an old `kanibako/` subdirectory to `boxes/` on disk and said so
  on stderr. It was the last piece of kanibako that mutated your files on a plain read, it ran on
  every load, and it ran *before* the refusal that rejects a pre-1.7 workset identity — so a workset
  old enough to have such a directory was renamed and then refused anyway. v1.8.0 opens no migration
  path from that era. ⚑ **It also removes a hazard that had nothing to do with age:** the rename
  matched the literal name `kanibako` but compared against the *default* `boxes` leaf, so a workset
  that had legitimately repointed `workset.boxes` to `kanibako` would have had its live boxes
  directory renamed out from under it and then resolved to an empty one.

- **The legacy `data/template` location for a plugin's packaged agent-store payload.** A Target
  plugin ships the payload that seeds its agent store under `data/base`; before the rename it was
  `data/template`, and kanibako kept a fallback arm that accepted the old spelling and re-rooted the
  copy so an unconverted plugin still seeded correctly. All three bundled plugins have shipped
  `data/base` since the rename, so the arm was already unreachable — resolving `data/template`
  through the plugin loader returns nothing for every one of them. ⚑ **This is for third-party
  plugin authors, not for boxes:** a plugin that still ships `data/template` now contributes nothing
  to its agent store rather than being silently re-rooted, and nothing names the omission. If you
  maintain a plugin, rename the directory to `data/base`; the payload's internal layout is
  unchanged.

- **The stderr notice about a leftover `<data>/settings.yaml` is gone; a legacy settings file is now
  ignored in silence.** 1.7.0 moved the primary workset's settings to
  `@config.primary_workset/settings.yaml` and stopped reading the old path, and printed a one-shot
  warning naming the stale file whenever it sat there without the new one. Nothing else changes:
  the file was already never read and never touched, and it still is — you simply no longer hear
  about it. v1.8.0 renames every tier's settings file and opens no deprecation window for any of
  them (see [MIGRATION.md](MIGRATION.md) §2.45, which lists each old path and its new name); a
  notice that survived for one of those paths alone would be a rule kanibako applies to one legacy
  file and refuses to the rest. If you have such a file, move the values you still want into
  `@config.primary_workset/workset.yaml` or re-set them with `kanibako workset set default
  <key>=<value>`.

- **The `kanibako.deprecation` module, and the two `SystemPaths` properties that survived only to
  explain themselves.** `kanibako.deprecation` held a registry, a `@deprecated` decorator and the
  CI gate that failed a build once a record's `remove_at` version arrived. Its registry has been
  empty since the clean break that emptied it, and v1.8.0 is itself a clean break with no
  deprecation window, so it was a mechanism with nothing to track; it is gone from the wheel.
  Separately, `SystemPaths.share_ro` and `SystemPaths.share_rw` — deleted in the `system.*` reorg
  and kept on as properties that raised `NotImplementedError` naming their replacements
  (`@workset.vault_ro` / `@workset.vault_rw`) — are gone too. ⚑ **Both are import-facing, so this
  is for plugin and script authors, not for boxes:** an `import kanibako.deprecation` now raises
  `ModuleNotFoundError`, and `std.share_ro` raises a plain `AttributeError` rather than the
  sentence that told you what to use instead. Nothing at runtime consulted either.

### Fixed

- **A new claude box's VS Code panel loaded a model named `default`, and every message failed.** The
  seeded `~/.claude/settings.json` carried `"model": "default"`, pasted in wholesale with a working
  box's real settings. `default` is not one of Claude Code's model aliases, so it went to the
  endpoint verbatim, no such model existed, and the panel answered every prompt with *"There's an
  issue with the selected model (default). It may not exist or you may not have access to it."* A
  fresh box was unusable from its first screen. The seed no longer names a model at all: a static
  template value cannot track the model kanibako resolves through `agent.<node>.model`, and with the
  key absent Claude Code picks, in the panel, the default the account actually has. (A box's CLI
  session is unaffected either way — kanibako passes it a resolved `--model`, which is why only the
  panel broke.) The seeded `effortLevel` moved from `xhigh` to `high` in the same pass. **This
  repairs boxes created from now on, and only those.** A box home is seeded once, at create, and the
  home bind owns the file afterwards, so upgrading rewrites nothing — a box you already have keeps
  the bad line. Cure it from inside the box: `/model <name>` in the panel fixes the current session,
  and deleting the `"model"` line from `~/.claude/settings.json` fixes it for good. Only boxes
  created by `v1.8.0-rc2`, or by a dev build cut after 2026-08-17, carry it; `v1.8.0-rc1` and 1.7.2
  seeded no model.

- **PID 1 now writes its decisions to `podman logs`.** The box supervisor logged every consequential
  thing it does — self-heal restart attempts, panel-watch entry, teardown, agent-marker reaps — and
  emitted none of it: nothing configured the `kanibako` logger inside the box, so Python discarded
  everything below WARNING and `podman logs <box>` was empty on a box that had just self-healed or
  forked a second agent. The supervisor now configures logging itself, before it parses its own
  arguments. Marker reaps in particular say *which* pid was reaped and *why* — a dead process, or a
  live one whose command line did not match the agent launch grammar, with the grammar test that
  failed — so a box that stops seeing a running agent leaves a readable trail.

- **`kanibako create --help` told you your `--agent` choice was thrown away; it was being saved.**
  One help string served all five commands that take the blanket `--agent`, and it read *"for this
  invocation (ephemeral; top of the resolution cascade, not persisted)"* — true for `start`, `box
  start` and `agent reauth`, false for `create` and `box create`, which write the choice into the
  new box's own `pref.system.agent` so a later bare `kanibako start` runs that agent. The two
  spellings of `create` now say they save it (and how to change it afterwards); the other three say
  the choice lasts one run. The refusal you get for `--agent` on an unrelated command no longer
  repeats the "for this invocation" claim either.

- **A persona whose model was a tier alias could not be launched at all, and the error blamed a
  token that was perfectly valid.** Claude Code accepts `sonnet`, `opus`, `haiku` and `fable` as
  aliases and resolves each through an environment variable — `ANTHROPIC_DEFAULT_SONNET_MODEL` and
  its siblings — before it puts anything on the wire. Kanibako's pre-launch probe did not: it sent
  the alias itself. An endpoint that serves only its own catalogue answered `403 team not allowed to
  access model`, kanibako read the 403 as an auth reject, and the launch was refused with a message
  telling you to fix the token. The box, had it been allowed to start, would have sent the resolved
  model and worked. The probe now resolves the alias through the persona store entry's own
  environment block first, so a mapping written there puts the same question to the endpoint that
  the box will ask. **It resolves only a mapping you wrote, and never invents one:** a model
  with no such variable is sent exactly as configured, and a persona that names no model is still
  probed with the field omitted. **The fix has a limit, and it is worth knowing before you configure
  around it:** the probe reads the persona-grata **store entry's** environment block. A tier-alias
  mapping written with `kanibako agent set <agent> env.<VAR>=…`, or at the workset or box scope, or
  on a persona configured entirely through the keyspace with no store entry, is still not visible to
  the probe — those launches can still be refused on the endpoint's 403. The box itself receives the
  variable normally; it is only the pre-launch check that does not see it.

- **A refused persona probe told you to fix the token, when a 401 or 403 never says which input was
  at fault.** The message asserted `the endpoint rejected the token` and pointed at the persona's
  `.secret_path` — the one thing a refusal does *not* identify. It now names the refusal and hands
  you the evidence instead: the HTTP status, the endpoint, the model actually sent (or `(omitted)`),
  the token path, and the provider's own error text, followed by a plain statement that the status
  does not say which input was at fault. **What you will see** on a refused launch:

  ```
  Error: persona 'navigator+claude' cannot be loaded — the endpoint refused the probe with HTTP 403.
    endpoint  https://api.example.edu
    model     sonnet
    token     ~/tokens/navigator
    provider: team not allowed to access model. This team can only access models=[…]
    An HTTP 403 means the endpoint refused this request — it does not say which input was at fault.
  ```

  The same block is appended to the create-time warning and to an inconclusive launch warning when
  the endpoint answered something. Your token value is scrubbed out of the provider's text before it
  is printed, and the provider's text is truncated. An endpoint that could not be reached prints no
  block — there is nothing it could report.

- **`kanibako system set` refused to point an agent setting at that agent's own directory.**
  Every per-agent setting can be written two ways — `kanibako agent set claude canon=…` or
  `kanibako system set agent.claude.canon=…` — and `@meta.agent.claude.path` is the reference that
  names where claude's own files live. The first spelling accepted it; the second answered
  `dangling @-reference '@meta.agent.claude.path' (no such config key in the keyspace)` and exited
  1, though the reference is perfectly good and resolves at launch. Kanibako checks such a value
  against a snapshot of the settings it can see, and that snapshot only ever included an agent's own
  directory when the command said which agent it was about — which `agent set` knows and `system
  set` was not working out for itself. It reads the agent's name out of the key now. **What you will
  see:** `kanibako system set agent.<agent>.canon=@meta.agent.<agent>.path/canon` is accepted and
  written, where it used to be refused. A genuinely broken reference is still refused by name, and
  so is a reference to a *different* agent's directory than the one you are writing to — the check
  is anchored to the agent whose file the value lands in, not loosened for the whole family.

- **A freshly set up agent carried a setting you never wrote, and `kanibako agent reset --all`
  said so.** Every agent settings file kanibako seeds was written with `run_args: []` in it — an
  empty argument list, materialized as if you had set one. Every other empty section is left out of
  the file on purpose, precisely so that what is in it is what you put there; this one was written
  unconditionally. The visible consequence was the reset count: `kanibako agent reset --all` counts
  the overrides it removes, and on an untouched agent it removed that empty list and reported
  `Reset 1 override(s).` **What you will see:** on an agent set up by this version, the same
  command now prints `No overrides to reset.`, which is the truth. Nothing else changes — an agent
  with real `run_args` still stores them, still starts with them, and still counts as one override.
  Agent files already on disk keep their `run_args: []` until something rewrites them; one
  `kanibako agent reset --all` clears it for good.

- **A hand-written `enable_vault: "false"` in quotes was read as ON.** kanibako has two readers for
  this setting: one resolves it through the full cascade, the other asks the narrower question of
  what *this box's own file* says — which is what the box lifecycle commands need, so that a
  workset's default is not silently frozen onto a box as an override of its own. The cascade reader
  converted what it found to a true or false; the box-file reader handed back whatever the file
  stored. Settings files are meant to be hand-editable, and in YAML a quoted `"false"` is text, not
  a boolean — so the two readers disagreed for the rest of that one command, the box-file half
  treating the text as *on*. It was easy to miss because it healed itself: the next command to
  write the file stored a real boolean and the disagreement vanished. Both readers convert now.
  **What you will see:** `kanibako box convert --standalone` on a box whose file says
  `enable_vault: "false"` no longer treats the vault as enabled for the remainder of the convert.
  Unquoted `enable_vault: false` was always read correctly and is unaffected.

- **`kanibako agent set` stored a value the same setting refused everywhere else.** Every other
  command that writes a setting checks the value first: an `@`-reference that points at no key, or
  a `$VAR`/`@ref` that is not even well formed, is refused by name and nothing is written. This one
  command reached the agent's settings file through a writer of its own, so none of that ran —
  `kanibako agent set claude canon=@bogus.ref` printed `Set canon=@bogus.ref` and exited 0, while
  `kanibako system set agent.claude.canon=@bogus.ref` — the same setting, the other spelling —
  refused it. The stored reference then resolved to nothing at launch, silently: a key pointed at
  an empty string rather than at the directory you named. `agent set` writes through the shared
  setter now, so one setting gets one answer whichever command you use, and the refusal is the
  wording the other command already prints. **What you will see:** a value that cannot resolve is
  refused with a non-zero exit and the file is untouched, where it used to be accepted. Values with
  no `@` or `$` in them are unaffected. `name` is the one thing this command still writes directly,
  because it is not a setting at all — it is the agent's display name, held in the same file.
  See MIGRATION.md, "`kanibako agent set` now validates the value you give it".

- **Converting a box to standalone moved kanibako's own directories into your workspace.** A
  standalone box keeps its live workspace in a subdirectory of the project root, so `kanibako box
  convert --standalone` sweeps everything else at the root down into it. What it left behind was a
  list of six literal names — and a name cannot describe a directory you have moved. If
  `workset.vault_ro` pointed at `store/ro`, the sweep saw a directory called `store`, matched
  nothing in the list, and moved your vault into the workspace; the box then opened a vault that
  was empty. `workset.canon` was worse, because it was never in the list at all: converting a box
  out of standalone and back moved the canon tree into the workspace with no repointed setting
  involved. And a setting pointed at an absolute path could not be described by that list under any
  spelling — with `workset.workspaces` pointed outside the root, the convert filled a `workspace/`
  directory the box never opens and your files were left where nothing binds them. The sweep now
  resolves `workset.workspaces`, `workset.vault_ro`, `workset.vault_rw` and `workset.canon` and
  compares directories, so a repointed one is recognised as kanibako's wherever you put it, and the
  workspace is filled at the path the box actually reads. **What you will see:** a directory kept
  because one of these settings points into it is now reported by name on standard error — `Note:
  left /path/store at the standalone root — workset.vault_ro resolves inside it.` The default
  layout keeps behave exactly as before and say nothing. A `workset.yaml` at the root carrying a
  setting kanibako cannot resolve now stops the convert and names the key, instead of guessing
  which of your directories to move; nothing has been touched at that point. If you already ran a
  convert that displaced a directory, it was moved and not deleted — it is under the workspace
  subdirectory, and moving it back to the root restores the layout.

- **Converting a box *out* of standalone emptied and deleted the workspace directory
  `workset.workspaces` named, when you had pointed that setting somewhere of your own.** A
  standalone box keeps its live workspace in a subdirectory, so converting to any other mode lifts
  the files back up — and the directory they were lifted *into* was counted off the workspace's
  own path rather than read off the box. In the default layout the two are the same directory, so
  nothing showed. With `workset.workspaces` pointed one level deeper, the files landed in the
  in-between directory instead of at the project root, and the box was registered there. With it
  pointed at an **absolute** path, the lift emptied the directory you had named, deleted it, and
  left your files loose in its parent — a directory kanibako was never given. The root is now read
  off the box, and an absolute workspace is kept where you put it: it needs no move at all, since
  every other mode's workspace is simply its project directory, wherever that is. **What you will
  see:** converting out of standalone with an absolute `workset.workspaces` now reports the keep on
  standard error — `Note: left the workspace at /path/work — workset.workspaces pointed it outside
  /path/root, so it is yours and the box keeps it as its project directory.` — and the box is
  registered at that path. An in-root repoint lifts to the project root as it always meant to, and
  the empty directory the repoint interposed is cleaned up; if it still holds files of yours, it
  stays.

- **Renaming a standalone box in place built a second box inside its own workspace and tore the
  first one out.** `kanibako box convert --standalone --name <new>` on a box that is already
  standalone was handed the box's workspace where the box's root belongs. It laid a complete second
  standalone tree — `box_data/`, `workset.yaml`, another workspace directory — one level down
  inside your files, then removed the original's `box_data/` (with the box's home in it) and vault,
  because from where it stood those belonged to a different box. A rename is now what it says: the
  identity changes, and the root, home, vault and workspace are left exactly as they were.

- **`kanibako system set agent.claude.run_args="--verbose"` reported success and the agent never
  got the arguments.** Two commands write this setting, and they stored two different things.
  `kanibako agent set claude run_args="--verbose"` split the value into a list of words, which is
  the shape kanibako reads; the full spelling stored the line verbatim as one string, and the
  reader took a list or nothing — so the string was thrown away. The command printed `Set
  agent.claude.run_args=--verbose` and exited 0, and every box then started with no extra
  arguments at all. The one visible tell was a disagreement between two commands that read the same
  file: `kanibako agent get claude run_args` echoed the value back while `kanibako agent show
  claude` printed no `run_args` line, because `show` reads the record the launch reads. The split
  now lives in the boundary both commands write through, so both store the words. **What you will
  see:** a value written by the full spelling reaches the agent's command line. A `run_args`
  already sitting in an agent settings file as a string is read as the arguments it spells —
  nothing to migrate and nothing to re-enter — and the next time kanibako writes that file it is
  rewritten as a list. Splitting is on whitespace and there is no quoting, exactly as before: an
  argument that must contain a space is written into the list by hand. `run_args=` with nothing
  after it still means *no arguments*, and stays distinct from a key you never set.

- **`run_args` was printed back at you as a Python list.** `kanibako agent show claude` printed
  `run_args = ['--a', '--b']` and `kanibako system get agent.claude.run_args` answered the same —
  a spelling you cannot type back in. Both print `--a --b` now, the line you gave them. Nothing
  stored changes; this is the display only.

- **A setting an agent plugin declares — `agent.goose.provider`, say — was a key to `kanibako agent`
  and an unknown key to `kanibako system`.** Most agent settings are the same for every agent and
  kanibako declares them itself, but an agent plugin may declare settings of its own; goose declares
  `provider`. `kanibako agent set goose provider=openrouter` stored one and `kanibako agent get
  goose provider` read it back, exactly as documented. The same key spelled out in full went to a
  different door and got a different answer: `kanibako system set agent.goose.provider=openrouter`
  failed with `Error: unknown config key: agent.goose.provider`, and `kanibako system get
  agent.goose.provider` answered `(not set)` — successfully, with the real value sitting in the
  agent's own settings file the whole time. The cause was two lists of what an agent setting can be
  called: the one that judges a key had the plugin's settings folded in and the one that recognises
  the `agent.<agent>.<setting>` spelling did not. There is one list now, so both doors give the same
  answer. **What you will see:** `set` and `reset` at these keys now succeed where they used to
  fail, and `get` returns the stored value where it used to report nothing. A setting no agent
  declares is refused exactly as before, and by name. Nothing stored changes, and nothing you could
  already do stops working — a value written by `kanibako agent set` was always read at launch; it
  was only these three commands that could not see it.

- **`kanibako system set box.enable_vault=false` was accepted, stored, echoed back — and ignored by
  every box.** The setting is a declared, settable key, and writing it at the system scope is
  something kanibako allows by design. The command returned success, wrote the value to the global
  settings file, and `kanibako system get box.enable_vault` read it back as `false`. Every box then
  started with the vault created and mounted anyway. The cause was that this one key never went
  through the settings cascade at all: it was read straight out of two files — the box's own
  settings and its workset's — so a value stored at any other level was invisible to the code that
  actually decides whether the vault exists. It now resolves the way its siblings `box.image`,
  `box.share_images` and `box.shell` always have, through the full cascade, and the more specific
  level still wins: a value on the box overrides one on the workset, which overrides one set
  system-wide. Nothing about a value already stored on a box or a workset changes — `workset create
  --no-vault` and a per-box `box.enable_vault: false` behave exactly as before.

- **`box.enable_vault` was missing from `kanibako box show --effective`.** The effective view lists
  the resolved box settings, and this one had no row — not an empty one, no line at all — so the
  disagreement above was invisible from the one place you would look for it. It is listed now,
  beside `box_image` and `box_share_images`.

- **`box.enable_vault` was declared with a default that nothing ever installed, so
  `@box.enable_vault` resolved to nothing at launch.** Same defect as the five `workset.*` rows
  below, and the last one left: the manifest promises the key defaults to `true`, and the value
  lived only inside the function that read the file, never in a cascade floor — so a setting written
  as `%if @box.enable_vault: …%` saw nothing to test. The key's declared default is now carried
  where the other box scalars carry theirs, and every declared default in the keyspace is reachable
  for a box that already exists. ⚑ `kanibako system defaults` names that new home in the source
  column — the row reads `config.py (KanibakoConfig field)` where it used to read `config.py
  (read-with-default)`, beside `box.image` and `box.share_images`. The key, value, scope and the
  total of 65 declared defaults are unchanged.

- **`workset.workspaces` was declared with real defaults that nothing ever installed, so
  `@workset.workspaces` resolved to nothing at launch.** The key names the directory a workset's
  member workspaces live under — `<workset>/workspaces` for a named workset, `<root>/workspace` for
  a standalone box — and it is settable, honored on the detection side, and referenced by
  `meta.box.workspace`. But no floor ever put the value into the keyspace, so a setting written as
  `@workset.workspaces/mine` expanded to `/mine` in both of those modes, and the key that references
  it referenced nothing. The workset anchor floor now emits it, from the directory the pre-launch
  resolution already reached — so a `workset.workspaces` repoint carries into the keyspace instead
  of being silently dropped. ⚑ **A PRIMARY box deliberately gets no value**, because the key
  declares none for that mode; that is unchanged, and now enforced rather than incidental. This
  closes the last of the four keys in this class: `workset.channelroot`, `workset.registry`,
  `workset.kuid`, `workset.skip_kuid_check` and `workset.template` preceded it.

- **`kanibako system defaults` reported `workset.workspaces` as having no artifact behind it.** The
  source column said the value was built by joining path components at the point of use, with no
  declaration anywhere to point at. That is no longer true — the launch writes the resolved
  directory out — and the row now names the code that derives it. The listing is unchanged in count
  and in value; only the provenance was wrong.

- **`workset.kuid`, `workset.registry` and `workset.skip_kuid_check` were declared with defaults
  that nothing ever installed, so an `@`-reference to any of them resolved to nothing at launch.**
  Each is a manifest row with a stated default, and each was reachable only through a Python
  accessor consulted before the launch snapshot exists — no floor emitted the value, so a setting
  written as `@workset.registry` expanded to an empty string rather than the registry path, in
  every mode. This is the same defect `workset.channelroot` had: a key the manifest promises and
  the keyspace cannot answer. All three are now emitted by the workset anchor floor, from the value
  the pre-snapshot path already produced rather than from a fresh literal. Two declared *absences*
  are preserved and now tested as absences: a standalone box gets neither `workset.registry` (it
  has no registry tier) nor `workset.kuid` (its identifier is minted into its own settings file at
  creation, so a placeholder here would fabricate an identity on a half-created box).

- **`kanibako system defaults` reported the wrong source for four keys.** The column names the
  artifact a default comes from, so you can go read it. `workset.template` was listed as having no
  literal anywhere to point at when one exists; `workset.registry`, `workset.kuid` and
  `workset.skip_kuid_check` were attributed to the accessors that used to be their only carrier.
  The rows are unchanged in count and value — only the provenance was wrong, and it is the part of
  that command a reader acts on.

- **`workset.vault_ro` and `workset.vault_rw` were settable and ignored — the value was written,
  accepted, and the vault was still created and mounted at the default location.** Both keys are
  declared for every box mode: a workset's vault is `@meta.workset.path/vault/{ro,rw}` unless you
  say otherwise. Nothing on the path side read either one. Every site that produced a vault
  directory composed `<root>/vault/ro` as a literal instead — the PRIMARY workset's two roots, the
  per-box directories a named workset creates and removes, and a standalone box's own tree — so
  `kanibako workset set workset.vault_ro=/mnt/big/ro` wrote the file, reported nothing wrong, and
  left your vault exactly where it had been. A refusal would have been kinder than that: a refusal
  confesses, while a value that is accepted teaches you the key works. Both keys now resolve
  through the same route the other workset directory keys use, so they accept the same values
  those do — an absolute path, `~`, `$XDG_*`, or `@meta.workset.path` — and a value needing
  anything else is refused by name rather than becoming a directory called `@config.registry`.
  ⚑ **If you had already set either key, this MOVES the directory kanibako uses.** Whatever is
  under the old default location is not migrated: move it across yourself, or unset the key to
  keep the old path. ⚑ The two arms are independent — repointing `vault_ro` alone leaves
  `vault_rw` at its default — and one small behaviour follows the move: the `.gitignore` that
  keeps `rw/` out of version control belongs to the workset's own `vault/` directory, so pointing
  an arm outside the workset root no longer drops that file beside whatever you pointed it at.
  Nothing else about the vault changes. It is still gated on `box.enable_vault`, still nests
  `ro`/`rw` above the box name for a primary or named box, and still has no per-box subdirectory
  for a standalone one.
  ⚑ **The verbs that DELETE a vault follow the key too, and they are careful about it.** A key
  that is honoured everywhere except where a directory is removed is worse than one that was never
  honoured: your real vault would be orphaned while a directory the box never used was the thing
  taken. `box rm --purge`, `kanibako clean --purge`, `box move` and `box convert` now resolve the
  vault before they delete anything, and they draw a line the earlier code had no reason to. For a
  primary or named box only the per-box `<box-name>` directory is ever removed, under whichever arm
  it actually lives — never the shared arm above it. For a standalone box the arm IS the vault,
  with no per-box directory beneath it, so an arm you pointed **outside** the box's own root is
  treated as yours rather than kanibako's: it is left in place and named on screen (`Kept vault:
  …`) instead of being deleted. An arm inside the root goes with the box, as it always did.
  ⚑ One refusal is new. If either key holds a value that cannot be resolved, a purge or a move now
  stops and names the key **before** removing anything, rather than deleting half the box and then
  failing. Fix the value — or unset it — and run the command again.

- **`kanibako archive` built a vault path out of the workspace directory for a box whose workspace
  had gone missing.** `<workspace>/vault/ro` has not been where a vault lives in any box mode since
  the vault moved out of the workspace, and when the workspace path was unknown the composition
  produced a *relative* `(unknown-<box>)/vault/ro`. Nothing read the field, so no archive was ever
  wrong because of it; it is now the same per-box path every other command resolves.

- **An alias refused a flag its canonical spelling accepted: `box inspect --box mybox` failed
  where `box info --box mybox` worked.** `box info`/`box inspect`, `box move`/`box mv` and
  `box rm`/`box delete` are each ONE command registered under two names, but the check that
  decides whether `--box` applies to a command read the name you *typed* — so one spelling of the
  pair ran and the other exited 2 with "`--box` is not valid for 'box inspect'". `--help` made it
  worse by advertising `--box` under both spellings, promising exactly what one of them then
  refused. Relevance now follows the parser that actually ran, so an alias inherits its canonical
  form's answer. That is one rule over all sixteen aliases in the command tree rather than a
  second list of spellings to keep in step, and it does not loosen anything: `box ls` still
  refuses `--box`, because `box list` does.

- **`kanibako rm --box` and `kanibako register --box` were refused where `kanibako box rm --box`
  and `kanibako box register --box` were not.** The two top-level shortcuts are separate parsers
  that dispatch to the same handlers, and those handlers already read `--box`; the refusal came
  from the relevance table alone, which declared both spellings of `start`, `stop` and `shell` but
  only the `box` spelling of these two. Both are declared now, and `--help` offers the flag on
  both. ⚑ **This does not make `--box` a substitute for the positional.** `rm` and `register`
  still require their target argument, on the shortcut and on the `box` verb alike, so `--box` is
  only ever a second spelling of a subject you also typed — matching, it warns and continues;
  differing, it is a conflict. That is unchanged, and it is what `box rm` already did.

- **`kanibako code --box mybox` exited 2, though `code` had read `--box` all along.** `code` takes
  the same optional project positional the launch verbs take, and its handler reconciled that
  positional with `--box` through the same shared function `start`, `stop` and `shell` use — but
  `code` was never added to the table that decides which commands the flag applies to, so the
  invocation was refused before the handler ever ran and `--help` did not offer the flag either.
  `code` is declared now, on both legs (`--remote` included). ⚑ **This does not make `--box` a
  substitute for the positional**, and it does not loosen anything: `code --box` is a second
  spelling of the subject you could already type, matching it warns and continues, differing it is
  a conflict, and `rig list --box` is still refused. Unlike the two entries above this one was
  reachable by neither an alias nor a shortcut rule — there is no `box code` — so the check that
  now guards it is the general one: every command whose handler reads a blanket flag must be
  declared as taking it, asserted over the whole command tree.

- **A refusal named a command you cannot type: `--box is not valid for 'rig list'` listed `reauth`
  among the commands that do take it, and `kanibako reauth --box mybox` then failed.** Both blanket
  flags refuse an unrelated command by enumerating the commands they DO apply to, and `reauth` was
  declared in both of those lists — but there is no top-level `reauth` command. `kanibako reauth`
  parses as `start reauth`, taking the word as the name of a box to launch, so `kanibako reauth
  --help` printed `start`'s usage and `kanibako reauth` stopped with "no box at reauth". The entry
  is gone from both lists. Nothing else moves: `kanibako agent reauth` is a separate entry in both,
  and still takes `--box` and `--agent` exactly as before, with both still offered in its `--help`.
  ⚑ **This removes no way to re-authenticate.** `kanibako agent reauth` is the spelling, and it
  always was; what is removed is a list entry that advertised a second one that never existed. The
  counterpart of the check in the entry above now guards this direction too: every command a
  relevance list names must be one the parser can actually reach, asserted over the whole tree.

- **Bindings sourced at `@system.backup`, `@system.cache` or `@system.runtime` were accepted and
  then silently dropped.** All three are settings keys kanibako declares, gives a default, and lets
  you set — `kanibako system set system.cache=…` has always worked. But the map that `@system.*`
  references actually resolve against when a box starts was written out by hand and named only
  eight of the eleven, so a binding, seed or environment value sourced at one of those three
  resolved when you set it, reached nothing at launch, and was discarded with no message and exit
  0. The same row simply did not appear in `workset share list --effective` either, so the listing
  a user checks their configuration against agreed with the drop instead of exposing it. The map is
  now derived from the declared table at both sites, so those three resolve, mount and display like
  any other path key, and one declared later arrives without an edit. **Nothing else changes:** the
  eight keys that already resolved resolve to the same values, no key became settable that was not
  settable before, and no new value is written to any file.

- **A value sourced at `@config.journal` was accepted and then silently dropped at launch.**
  Kanibako's Layer-1 config foundation declares six keys, but the map that `@config.*` references
  actually resolve against was written out by hand — five string literals, in the launch path and
  again in `workset share list --effective`. `config.journal` was the one left out: declared, with
  a default, and settable. So a binding, seed or environment value naming it resolved when you set
  it, reached nothing when the box started, and was discarded with no message at exit 0 — and the
  same row simply did not appear in `workset share list --effective`. A reference that set time
  accepts and launch discards is worse than one that is consistently refused, because it never
  confesses. Both call sites now derive the map from the declared table, so a Layer-1 key added
  later reaches them without an edit. **Nothing else changes:** the five keys that already resolved
  resolve to the same values, and no key became settable that was not settable before.

- **`system get agent.default.<key>` answered `(not set)` for a value `system get <key>` had just
  returned.** The any-agent tier has two spellings of one key — the bare `model` the CLI serves, and
  the full `agent.default.model` the settings registry declares — and only the bare one read. The
  full spelling was routed as if `default` were an agent node, hit the reserved-tier guard that
  exists to refuse a *write* there, and came back with nothing for the read to use. Both spellings
  now resolve to the same slot in the same file, for all eleven declared agent leaves. **The write
  side is unchanged and still refuses the full spelling by name**, with the cure naming the bare
  key. Only the read moved.

- **`set` and `reset` told you a declared key was not a key.** `kanibako system set
  system.masks=/tmp` answered *"unknown config key: system.masks"*. The refusal itself is right — a
  category table is keyed by box destination, so there is nothing for a scalar `set` to write, and
  the registry declares these keys file-only — but the message denied the key's existence instead
  of naming the rule. All seven categories were affected at every scope that carries them, on both
  verbs. They are refused by name now, saying the value is a destination-keyed table, naming the
  file to author it in and the command that reads it back. **Nothing became settable.** The
  per-name spelling `<scope>.<category>.<name>` keeps its own message: that route was *retired*,
  whereas the whole-key spelling never had one.

- **`box get` and `workset get` answered `(not set)` for a per-agent key that was set.** A key
  naming an agent node — `agent.<node>.model`, `agent.<node>.endpoint`,
  `agent.<node>.secret_path.<VAR>` — is stored in that node's own `agents/<node>/agent.yaml`, and
  the read finds that file through the agents root. `system get` passed the agents root; the `box`
  and `workset` handlers did not, so every such read resolved to nothing and printed `(not set)` at
  exit 0 — for a value `kanibako system get` reported correctly, on the same key, in the same
  install. Three nouns over one keyspace, giving two different answers. Both handlers now thread
  the agents root the way `system get` always has, so the three agree. Nothing else moves: a node
  key that genuinely is not set still answers `(not set)` at exit 0, and the write verbs are
  untouched — `set` and `reset` refuse an `agent.*` key from the box or workset scope by name, as
  they always have, because a config set never writes upward.

- **`--help` advertised `--agent` on 96 commands that refuse it, and `--box` on 82.** `box set`,
  `system get`, `rig list` and most of the rest of the tree listed both flags in the usage line and
  the options list; passing one exited 2 with *"--agent is not valid for 'system get'"*. The
  refusal is the correct half. `--agent` picks the agent for an invocation that runs one, and
  `--box` names the subject box for a command that acts on one, so a read like `box get` or a
  registry listing like `rig list` has nothing to do with either. What was wrong was offering them
  first: help that lists a flag the command answers with exit 2 promises a refusal. Each flag is
  now shown only where it applies — `--agent` on `start`, `create`, `reauth` and their `box` /
  `agent` spellings; `--box` on the commands that take a subject box. Two commands stop advertising
  a flag they had merely been ignoring, too: `shell` and `box shell` never launch an agent, and
  their `--agent` prints a note and moves on. **Nothing about what is accepted changed.** Every
  command still parses both flags, so a misplaced one still gets the error that enumerates where it
  does apply, with its exit code, instead of argparse's bare *"unrecognized arguments"*. To choose
  the agent a box uses, set it rather than passing it: `kanibako box set pref.system.agent=<agent>`.

- **A claude box leaked one agent liveness marker per session start, and hid a failing hook layer
  while doing it.** The `~/.claude/settings.json` kanibako seeds into a claude box invoked each of
  the bible session hooks as a compound command — `…/scripts/hooks/startup.sh || true` — and both
  failures follow from that one `|| true`. A compound command forces the hook shell to survive and
  evaluate the right-hand side, so it cannot `exec` the script: `$PPID` inside the hook was that
  transient wrapper rather than the agent, and the marker written under `/tmp/kanibako/agents/`
  named a process that was already dead. `SessionEnd` then ran under a **different** wrapper, so
  its remove targeted a filename that had never existed and the stale marker stayed — one leak per
  session start, on the signal the in-box supervisor reads to tell a live agent session from a dead
  one. Separately, `|| true` swallowed the exit status of your own handbook or notebook hook layer.
  The cascade scripts already distinguish the two cases the blanket `|| true` could not: a layer
  you never created is absent and stays silent, while a layer that exists and exits non-zero is a
  bug in your own hook and has to stay visible. Each seeded hook now passes `"$PPID"` explicitly —
  the hook shell expands it, and there it is the agent — and none of them carries `|| true`. The
  bible hooks take that pid as their first argument and fall back to their own `$PPID` only for a
  caller that wires one of them as the hook command directly. ⚑ **A box created before this change
  keeps the old hooks**: `~/.claude/settings.json` is seeded once at create and is yours to edit
  afterwards, so kanibako does not rewrite it. To get the fixed behaviour, replace `|| true` with
  `"$PPID"` on each `~/canon/bible/general/scripts/hooks/*.sh` command, and drop it outright from
  the `stop.sh` one, which needs no pid. Markers left behind by the old spelling need nothing done
  to them: the supervisor reaps a marker whose process is gone on its first scan.

- **A leaked agent liveness marker no longer outlives the agent that wrote it, and can no longer
  start an agent you did not ask for.** Each agent session writes a marker file named for its pid
  under `/tmp/kanibako/agents/` and removes it on exit, and the in-box supervisor reads that
  directory to tell a live agent session from a dead one. An agent that is killed, crashes, or
  loses the box out from under it never runs the removal, so the file stays behind naming a dead
  process — and nothing ever deleted it, which made a one-off leak permanent. Two things followed.
  In `--warm-only` panel-watch mode the supervisor read "a marker whose process is gone" as *the
  panel agent just died*, and with the VS Code panel still connected that is its cue to start a CLI
  agent in tmux as a fallback — so a marker left behind days earlier launched an agent into a box
  whose panel was working fine, and did it again every time the box had no live tmux agent.
  Separately, under the experimental single-writer takeover (`KANIBAKO_SESSION_TAKEOVER`, off by
  default), a marker naming a live process that was not the agent read as a *second agent* holding
  the session, and the supervisor evicted the real, running agent to make room for it. The
  supervisor now removes a marker as it scans: one whose process is gone, and one whose process is
  alive but is not the agent session — checked by reading `/proc/<pid>/cmdline` and comparing how
  that process was started against how this box launches its agent. A stale marker is therefore
  seen once rather than forever, so a genuine panel-agent death still self-heals a CLI on the tick
  it happens and never again on the same corpse. ⚑ **The test is the session, not the program
  name.** An agent runs helper processes under its own binary — claude runs a daemon, a background
  pty host and a spare — so matching on the name alone would call every one of them an agent, and a
  helper's marker would read as exactly that phantom second agent. What is compared is the program
  plus its subcommand, which is all that survives a real launch: a box started with `--continue`
  runs as `--resume`, and your own flags and model overrides move everything after. ⚑ **A marker is
  only ever removed on a positive judgement.** If `/proc` cannot be read, if the process is
  something else that merely names the agent, or if the supervisor has no launch grammar to compare
  against, the marker is left alone — deleting a live agent's marker would blind the supervisor to
  an agent that is running, which is worse than the leak it fixes. A genuine second agent session is
  still detected exactly as before. Boxes carrying markers from before this change need nothing done
  to them; the leftovers go on the first scan.

- **`kanibako box show --effective` no longer prints a binding as a live mount when the box
  receives nothing at that destination.** The block rendered each `<scope>.bindings.{ro,rw}` row
  straight off the stored declaration, with no arbitration applied and masks never shown at all. So
  a `box.bindings.ro./opt/arb` that a `box.masks./opt/arb` had taken printed as
  `/src -> /opt/arb`, at exit code 0, with the mask that swallowed it appearing nowhere — the
  display that exists to answer *what does my box actually get* asserting a mount the box does not
  have. The same happened when a mask sat **above** the binding: the sweep leaves the binding's own
  destination absent from the collapsed map entirely, so nothing was there to notice its absence.
  Concrete rows are now paired against the arbitrated map through the same function the abstract
  declarations and `workset share list --effective` already use. A binding that is delivered prints
  exactly as before — including one that legitimately **supersedes** a lower-scope mask, which is
  a real mount and not a loss. A binding that is not delivered keeps its key (that key is what you
  edit) and is printed in declaration form with the reason beneath it, naming the destination that
  took it:

      box.bindings.ro./opt/arb = /opt/arb  (declared: /src)
        (no mount — the mask declared by 'box.masks./opt/arb' at /opt/arb covers this
        destination, and a mask has no host source: the box sees nothing at that path)

  A destination you spelled with a variable — `$XDG_CACHE_HOME/models`, say — is arbitrated like
  any other, and a mask over one is reported like any other. The key is still printed the way you
  wrote it, because that is the line you edit; the reason beneath a loss names the resolved path,
  because that is where the collision happened.

- **A `box.enable_vault` published by a workset stays the workset's — `box remap`, `box move` and
  `box convert` no longer resolve it inconsistently, nor harden it into the box that inherited it.**
  A `box.*` key stored at a workset tier is an overridable downward default for the boxes that
  workset contains, which is how `workset create --no-vault` reaches them. Two things went wrong with
  it. First, `remap` gave two different answers for one store: it resolves through the ordinary path
  when the recorded workspace directory is still on disk, and through a registered-metadata fallback
  when it is gone — and only the first consulted the workset tier. With a workset-tier
  `box.enable_vault: false`, remapping a box whose directory you had already moved created the vault
  the workset had switched off; remapping one whose directory was still there did not. Second, every
  lifecycle op then persisted the **resolved** value at the destination's box tier, so a box that had
  merely inherited `false` came out of a `remap` carrying `box.enable_vault: false` as its own
  override — one the publishing workset could no longer reach — and `box move --workset` carried that
  same inherited value into a workset that had never declared it. Both paths now resolve through the
  same downward default, and what is written at the destination is only what the **box itself**
  authored. A box that leaves a workset loses the workset's value, because the value was the
  workset's; a box that set `box.enable_vault` for itself keeps it across every hop, unchanged.

- **`box duplicate` was a fourth route to the same defect, and gave the copy an override it never
  authored.** The `box.enable_vault` entry in this release covers `box remap`, `box move` and `box
  convert`; `box duplicate --to standalone` reaches the destination's box tier through a different
  function and went on persisting the **resolved** value. Duplicating a box out of a workset that
  publishes `box.enable_vault: false` wrote `box.enable_vault: false` into the copy's own
  `box_data/box.yaml` — and a duplicate is a fresh workset scope, minted with a new identity, so no
  workset published that value and no workset edit could reach it afterwards; `kanibako vault` on the
  copy answered "Vault is disabled for this project." forever. The duplicate now records only what
  the **source box itself** authored, which is the same rule and the same outcome as the three
  sibling routes: an inherited value stays with the workset that published it, and a
  `box.enable_vault` the source box set for itself still travels. ⚑ **This creates and removes no
  vault.** A duplicate has never carried the source's vault and still does not, and the source's is
  untouched; what changes is the value the copy stores, and therefore the one it resolves.

- **`kanibako system get` refuses a key this scope cannot read, instead of answering `(not set)` and
  exiting 0.** It checked only whether the argument was key-*shaped* — the test that tells a key from
  a project name — never whether it was a declared key readable at this noun. A name that passed fell
  through to an ordinary read, found nothing, and was reported as unset: an answer invented for
  something that is not a key here, which `box get` and `workset get` were already fixed to refuse.
  All three nouns now call one function, so they cannot drift into three answers for one key. Four
  spellings change, each now exiting 1 and naming the key you typed: **`transform_settings`**, whose
  value is a table no scalar read can carry — the refusal points at `kanibako agent get <agent>
  transform_settings`, which does answer it; **an entry of a terminal agent category** such as
  `agent.<node>.caches.<name>`, whose entries are box destinations inside the value rather than keys
  of their own; **a misspelled agent node** such as `agent.nosuchagent.model`, which now names the
  bad segment and lists the real agents; and the **retired `box.agent.<key>` mirror**, which points
  at its `pref.agent.<agent>.<key>` replacement. ⚑ **Nothing that returned a value before returns
  anything different.** Bare agent settings — `model`, `access`, `template` and the rest — are this
  scope's own any-agent tier and still read and set exactly as they did; a hand-authored
  `agent.<node>.bindings.{ro,rw}.<name>` still reads back, so you can still confirm that the hand
  edit its write refusal prescribes took effect; and scope-level category entries such as
  `system.synced.<name>` still read. An undeclared name is refused as it always was, though the
  wording changed once more — see the entry below.

- **`kanibako system get` no longer reports seven declared keys as `unknown config key`.**
  `system.masks` and the six terminal category keys — `system.bindings.ro`, `system.bindings.rw`,
  `system.caches`, `system.seeded`, `system.common`, `system.synced` — are declared, are stored in
  your system settings file, and are read fine by the engine; only `system get` refused them, and
  refused them by claiming they do not exist. The cause was that this one verb ran *two*
  vocabularies over its argument: the closed-keyspace check every other read uses, and, ahead of it,
  an older key-shaped-or-project-name test that was never a model of what a key is. The older test
  is gone from the read path — it still does the job it was written for, telling `kanibako box
  <name>` apart from `kanibako box <key>` — so these seven now read their value like any other key.
  ⚑ This is the **same-scope** read only, and reading a *foreign* scope's category key
  (`system get box.caches`) stays refused — see the next entry. ⚑ Writing is unchanged: a terminal
  category key is still authored in YAML, and `system set system.masks=…` still refuses.

- **A key belonging to another scope is refused by name at a `get`, instead of being called an
  unknown key.** `kanibako system get box.caches` is a question the CLI has no reason to answer —
  the noun already names the scope — and it now says so: it names `box.caches` as a declared
  *box*-scope key **whose value is merged entry by entry across tiers**, so this noun holds at most
  a fragment of it and never the value, and it points at `kanibako box get <box> box.caches`.
  Previously it answered `Error: unknown config key`, which was wrong about a declared key.
  ⚑ A foreign-scope **scalar** is deliberately **not** in this: `kanibako system set box.image=…`
  is a legal downward default that lands in the system settings file, and a scalar is held whole by
  one tier, so `system get box.image` is a complete answer and still reads it back. The rule is
  about what a read can honestly *mean*, not about the spelling matching the noun — a category
  table is assembled from every tier, so no single tier's copy is the answer.
  ⚑ `meta.*` keys are refused the same way and for a plainer reason:
  they are derived per box when it launches and are stored in no settings file at all, so the
  refusal points at `kanibako box show <box> --effective`, which resolves them against a real box.

  One knock-on wording change: an **undeclared** name at `system get` now gets the closed-keyspace
  refusal — still exit 1, still naming what you typed, and now also listing the declared
  alternatives — in place of the flat `unknown config key`.

- **A *per-agent* category table asked for at a file-scope noun is now refused with the noun that
  answers it, instead of being reported as unset.** `agent.<agent>.caches` — and `masks`, `seeded`,
  `synced`, `common`, `bindings.ro`, `bindings.rw` beside it — is a declared key whose value lives in
  that agent's own `agents/<agent>/agent.yaml`, a file `system get`, `box get` and `workset get` do
  not open. Asked there it now exits 1 naming the key and pointing at `kanibako agent get <agent>
  <category>`, which reads the table. Previously the same question got `Error: unknown config key`,
  which was wrong about a declared key; without this refusal it would instead have answered
  `(not set)` over a table sitting on disk, which is worse — an invented answer rather than a wrong
  one. ⚑ Three things are deliberately *not* in this: `agent.default.<category>`, the any-agent tier,
  is stored in the system settings file and still reads there; a single hand-authored
  `agent.<agent>.bindings.{ro,rw}.<dest>` still reads back, which is how you confirm the hand edit
  its write refusal prescribes; and every scalar agent key (`agent.<agent>.model` and the rest) reads
  exactly as before.

- **A persona agent seeds its boxes from its own template store, shared with its harness by symlink —
  so a persona can now have a template of its own.** A persona is a distinct agent node
  (`navigator+claude`) with its own store directory, but the seed-layer-2 template source was spelled
  with the *harness's* store, so every persona of a harness necessarily seeded identically and there
  was no way to give one its own. The source is now the node's own store, matching what the manifest
  and the keyspace already declared, and `agents/<node>/template` is created as a symlink to
  `agents/<harness>/template` — so the harness's template still reaches every persona by default,
  with nothing to keep in sync. **To give a persona its own template, delete that symlink and put a
  real directory in its place;** kanibako never replaces a real directory, or a link you have
  repointed yourself. Sharing by link rather than by copy is deliberate: a copy would go stale
  against the harness, and a link cannot. The seed reads *through* the link and copies by value, so
  boxes are seeded with real files, never a link. ⚑ For a bare (non-persona) agent nothing changes —
  node and harness are the same name, so both spellings were always the same directory.

- **A persona agent now receives every directory its harness's plugin declares — shared with the
  harness by symlink, exactly as its template store is.** A plugin declares its agent-scope binds,
  caches and copy-once seeds against the *harness* name, and the settings cascade reads them under
  the *active node*, so a persona (`navigator+claude`) never saw them. That was fixed in 1.8.0 for
  the plugins/cache directories and nowhere else: a plugin's `default_seeds()` and
  `default_category_binds()` declarations were still read under the harness name at three separate
  places — the launch, and again when a box is created — and the failure was **silent**, with no
  mount, no copy, no warning and exit 0. Each declared source now resolves through the persona's
  own store, and `agents/<node>/<entry>` is created as a symlink to the harness's, so the harness's
  content still reaches every persona by default. **To give a persona its own, delete the symlink
  and put a real directory in its place;** kanibako never replaces a real directory, or a link you
  have repointed yourself. ⚑ No agent kanibako ships declares either of those two hooks, so no
  claude, goose or codex box changes — what this fixes is third-party agent plugins, and anything
  kanibako declares here in future. ⚑ One arrangement that used to launch now refuses: a plugin
  declaring the **same in-box destination** in both `default_common()` and
  `default_category_binds()` was already refused for a bare agent and slipped through for a persona,
  because the two landed under different keys there. They land under one key now, so the launch
  refuses naming both — the same refusal, on the launch shape that used to escape it.

- **Duplicating a standalone box no longer buries a copy of the source — carrying the source's
  identity — inside the new box.** The copy took the standalone source's *root* as its source
  directory, so the destination's `box_data/` came out holding the source's `workspace`, `vault`,
  `canon`, a nested `box_data/`, and the source's `workset.yaml`. That last file mattered: a
  directory counts as a standalone box when it holds both a `box_data/` directory and a
  `workset.yaml` file, and the stray one satisfied both. Running any kanibako command from inside the
  duplicate's `box_data/workspace/` would therefore have detected `box_data/` itself as a box and
  imported it — **registering a second box under the source's kuid.** The duplicate now copies the
  source's box metadata directory, the same guard the convert and move paths already apply, so the
  destination holds its own metadata and nothing else. Duplicates made before this fix have a stray
  `box_data/box_data/` and `box_data/workset.yaml` you can delete.

- **A directory named `kanibako` or `.kanibako` in your workspace is no longer silently dropped when
  a box is copied.** Converting or absorbing a box excluded those two names from the copy so that a
  pre-1.7 marker directory at a standalone root would not travel. The exclusion matched by
  *basename, at every depth*, so it also swallowed ordinary content — a checkout of the kanibako
  source tree itself being the obvious case — with no message. The legacy marker names are gone from
  both the copy filter and the standalone-root artifact list; the box's own metadata directory is
  still excluded, and the in-box `.kanibako` runtime root is a guest path underneath it and was never
  what these names protected. Such directories are now copied and consolidated as ordinary user
  content.

- **`kanibako workset share list --effective` says what a launch would actually mount, instead of
  listing every declaration.** It resolved each share the way a launch does and then stopped, so the
  mask arm, path containment and the collision rows were never applied: a working set that also
  declared `workset.masks` over a share's destination listed that share as a live mount while the box
  received nothing there, at rc 0 and with no message. The listing now runs the launch's own collapse.
  A share the box receives prints as `source -> dest [mode]` exactly as before; one that a mask or
  another binding swallows keeps its row and gains the reason it produces no mount — a row that simply
  vanished would be the same silent answer in a new place. If the working set's own declarations
  collide, `--effective` now exits 1 with the refusal a launch gives, introduced by a line saying that
  is why it cannot answer.
- **`kanibako_config.yaml` holds bootstrap paths and nothing else, and `kanibako init` writes it
  empty.** It used to be created with three tables — the six `config.*` bootstrap paths, six
  `system.*` paths, and `box.image` / `box.share_images` — every value of which is already a built-in
  default, so the file was a fourth copy of things the code, the defaults table and the manifest each
  declare. Worse, two of those tables were *read back* as settings: a `system:` table there reached
  every resolved host path, and a `box:` table entered the cascade. A setting written into that file
  now does nothing; only `config.*` is read from it. Defaults are unchanged, so a store that never
  edited the file resolves every path exactly as before.

- **`system.setup_completed` moved to the global settings file** — `<data>/global/settings.yaml`,
  beside `system.agent`, which is where a `system.*` settings key belongs. `kanibako setup` writes it
  there, and `system get` / `set` / `reset` all name that file. A marker left in the old location is
  not read: kanibako reports that setup has not run, prints its usual one-line nudge, and otherwise
  works normally — re-running `kanibako setup` records it in the new place.

- **`box show --effective` shows each abstract declaration beside the binding it derives, instead of
  `(binding derivations temporarily unavailable)`.** The `common`, `caches` and `seeded` half of the
  block had been disabled since the declaration/delivery pairing was routed through a stub. It now
  prints the declaration and, under it, what the box actually receives — ending `(mount)` or `(copy)`,
  or naming why there is neither: a mask covering the destination, a sweep above it, or a collision
  the declaration lost. Exit codes are unchanged.
  The distinction that makes the line trustworthy is that it reports what the box **receives**, not
  what was declared: a declaration whose binding is masked or swept now says so, where an earlier
  attempt at this display would have reported a mount that does not exist. What it still cannot do is
  name the *key* of the mask that covered a destination — only the destination itself.

- **A workset-tier `box.enable_vault` reaches the boxes in that workset.** `kanibako workset create
  --no-vault NAME` wrote the setting and nothing read it back, so boxes in that workset kept their
  vault: the flag had no effect on anything it was meant to affect. Both named and default-mode
  worksets now resolve it as an overridable default, so a box that sets its own value still wins.
  Nothing changes unless a workset explicitly carries `box.enable_vault: false` — an absent file, a
  workset with no `box` table, or the key set to `true` all behave exactly as before. A value
  inherited from the workset is **not** written into the box's own settings file, so later workset
  edits keep reaching it. The same holds for `box convert`, `box move` and `box duplicate`: they carry
  a box's own settings, never a default it was merely inheriting — so a box that leaves a workset
  leaves that workset's defaults behind, and one that stays keeps resolving them.

- **The Claude status line reports context from exact token counts instead of a rounded percentage,
  and prints `—` when it has no reading yet.** It derived used tokens from the integer
  `used_percentage`, so at a 1M window — where each 1% is 10,000 tokens — the figure was rounded to
  the nearest 10k; it then added a 3,000-token compaction buffer that does not apply when
  auto-compaction is off. With no usage data yet it printed that constant as `3k`, indistinguishable
  from a real reading. It now sums the three input-side fields the harness already supplies
  (`input_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`), which is exact, and
  renders `—` until there is something to report.

- **`kanibako setup` stops when settings will not resolve, instead of naming a wrong cause and
  finishing at rc 0 — and `system diagnose`, `rig diagnose` and `kanibako code` report the refusal
  they were swallowing.** Five places resolved settings inside a catch-all that reported every
  failure as something else. The costly one was `setup`: against the closed-keyspace refusal of an
  undeclared key it answered `Cannot check (configuration not initialized yet)` — the inverse of
  the truth, since the configuration is initialized and is the broken thing — promised a rig pull
  that would not happen, ran on through the agent write and the template refresh, and closed with
  `Setup Complete` / `You're ready to go!` at **rc 0** over a store no command could resolve. It
  also wrote the `setup_completed` marker, which is the thing that lifts the upgrade gate written
  to block exactly such a store. **`setup` now stops at Step 3 and exits 1, having written
  nothing**: no system agent, no template refresh, no marker. `system diagnose` and `rig diagnose`
  print the refusal under a `[!!]` row in place of `cannot check (not configured)`, still at rc 0
  like every other failed check. `kanibako code` warns at the default log level that VS Code will
  attach without the box's workspace folder or agent extension, and still launches, rc 0 unchanged.
  The `Storage` and `Journal` rows report the same way, and their trigger is wider than a settings
  refusal: a config file that is not valid YAML raised an error naming the file and the cure, and
  both rows printed `cannot check` over it. A single malformed `kanibako_config.yaml` could produce
  one honest line and two bland ones in a single run.
  Only errors kanibako raises deliberately — the ones whose text is already written for a user —
  are reported this way; an unforeseen failure still produces the old `cannot check` line, and
  `setup` still runs on past it to its summary. See [MIGRATION.md](MIGRATION.md) §2.49.

- **A reserved name in a settings file is refused on one line instead of crashing with a Python
  traceback.** A file containing `box: get:` — or `items:`, or any other name the settings store
  reserves because a real attribute already answers to it, at any tier — was refused by the store
  exactly as intended, and then escaped every handler in the CLI, because the exception it raised
  was not part of the error hierarchy kanibako catches. What the user got was a stack trace. It now
  exits 1 with the refusal alone: `Error: key 'get' is reserved: it would shadow a real attribute
  on the settings store. Reserved names: [...]`, the full list included so the name can be
  recognised rather than guessed at. **The refusal also names the file that carries the name**, as
  `(in settings file <path>)` after the reason, so a name reserved in one of several files in the
  cascade does not have to be hunted for. The same address is now appended to every refusal the
  settings parse raises — a retired entry shape, a bare relative path, a wrong number of arguments —
  at every tier that reads a file, including agent files and a `pref:` table.

- **`box get` and `workset get` say what `transform_settings` is, instead of denying the key
  exists.** It is a declared agent leaf, but a table-valued one, and §2a admits only scalars at a
  file scope — so there is no bare spelling for it there. The refusal said `'transform_settings' is
  not a declared namespace`, which is true of the token and misleading about the key, and then
  prescribed hand-editing the entry out of a settings file where it is legitimate one scope up. It
  now names the shape and where the value lives: `read it at the agent noun: kanibako agent get
  <agent> transform_settings`. No value was ever returned at that spelling and none is now; the
  exit code is unchanged.

- **A binding sourced at `@system.channels.broadcast` produces a mount.** Five `system.channels.*`
  leaves are declared and the launch floor supplied four, so that one resolved to nothing: a
  `set` was accepted, `get` read it back, and no mount appeared — no warning, rc 0.
  The floor is now derived from the declared system path defaults rather than listed by hand, so a
  leaf cannot go missing this way again.

- **`kanibako workset share list --effective` shows bindings sourced at a `system.channels.*` key.**
  It carried none of them, so a binding that mounts correctly at launch printed nothing at all — the
  display was wrong about live mounts, not only about the broken key. Both the launch and the
  display now read one shared builder.

- **`box move` and `box convert` relocate a repointed channel partition instead of the default
  one.** If either workset repointed `workset.channels.mailboxes` or `.share_global`, the relocation
  moved the default directory while the box was already mounted at the repointed address, so mail
  was left behind. Each side's own workset root is now used. If a channel key cannot be resolved the
  step warns and skips rather than aborting: the files have already moved by then, so the
  alternative is a half-completed operation. Exit codes are unchanged.

- **`kanibako code` says so when it cannot write VS Code's attached-container config, instead of
  attaching silently without it.** If the config home was unwritable — a read-only or full
  directory — the write failed inside a catch-all that reported it at debug level only, so at the
  default level nothing was printed: VS Code opened without the box's workspace folder and without
  its agent extension, and the cause was visible only under `-v`. It now warns at the default level,
  naming both the consequence and the underlying error with its path, and still launches at rc 0.
  Both legs behave the same way, including `--remote`, which writes the *local* config home keyed by
  the remote box's image and so fails and recovers identically.

- **Refusal messages for a settings key that is not a key no longer invent a trailing dot, and no
  longer offer a list of leaves from the wrong tier.** Asking about a namespace — `box`, `meta.box`,
  `agent.<name>` — answered as though you had named a key and mistyped it: `kanibako box set
  box=…` replied `'box.' is not a declared box key`, quoting a `box.` that you never wrote and that
  cannot exist. Interior paths deeper than one segment were worse: `meta.box.agent.auth` was told
  `'auth' is not a declared agent key` and handed the agent-scope leaf list, which does not contain
  and cannot contain `auth`, so the message pointed at the wrong tier entirely. The validator now
  distinguishes a namespace from an undeclared key rather than collapsing both into "not a key", and
  says which it is: `'box' names the box scope, which is a namespace, not a key`. Seventy-four
  messages changed. **No key changed status** — every path that was accepted is still accepted and
  every path that was refused is still refused, verified by replaying both the old and new validator
  over every declared key, every proper prefix, and a cross-product of category tokens, reserved
  names and fabrications; only the wording of refusals moved.

- **A `synced` copy declared under a mask now stops the launch with an error naming it, instead of
  being silently dropped.** The settings spec gives a `synced` copy exactly two refusals — a mask
  that is a **parent** of the destination, and a copy of a **directory** at a mask's own point — and
  both were implemented as a log warning followed by a skip. In practice a `synced` row is usually a
  credential, so the effect was a box that started perfectly and then failed to authenticate inside
  the agent, with nothing anywhere naming the configuration that caused it; the warning went to a
  log the user had no reason to read. Both are now errors, raised before any `synced` row is copied,
  so a refused declaration leaves nothing half-delivered — and each one names the destination you
  declared, the mask covering it, and the cure. This applies to `create` as well as to every launch.
  ⚠️ **If you have a `synced` entry sitting under a `masks` entry, the box will now refuse to start
  until you move the copy out of the mask or drop the mask.** It was never being delivered.
  Unchanged, and deliberately: a copy of a **file** at a mask's own point is still accepted and
  replaces the mask, and the three cases the spec does *not* call refusals — a destination no
  binding covers, a destination inside a read-only binding, and a source file that does not exist —
  still warn and skip exactly as before.

- **The eleven `system.*` path settings — `template`, `canon`, `backup`, `cache`, `runtime`,
  `channelroot` and the five `channels.*` type-roots — are settable from the CLI again.**
  `kanibako system set system.template=…` answered *"'system.template' is a structural config key
  and cannot be set from the CLI"* and sent you to hand-edit `kanibako_config.yaml`. These are not
  structural config keys: they are ordinary Layer-2 settings keys, and the two keys named in the
  same breath as `system.template` in the settings spec — `workset.vault_ro` and
  `agent.<agent>.canon` — always set fine, which is what made the refusal look like a rule rather
  than the mistake it was. The refusal came from a family membership test that swept the whole
  system path table in with the genuinely file-only `config.*` bootstrap keys. All eleven now route
  exactly like their `workset.*` twins: a set lands in the `system:` table of the system settings
  file, `get` reads it back from the same place, `reset` clears it and names what becomes effective,
  and the value gets the identical set-time resolution check — a dangling `@`-reference is refused
  with the same message `workset.vault_ro` gives. Repointing the handbook root with
  `system set system.canon=…`, which the settings documentation has always described as available,
  works for the first time.

- **…and those same eleven repoints now actually move the directories they name.** Making them
  settable was only half the repair. `kanibako system set system.canon=/srv/canon` stored the value
  and the launch cascade honoured it — binds, seeds and `show --effective` all moved — but the
  host-side path resolver read the bootstrap config file *only*, so every part of kanibako that
  asks for "the canon root" directly kept handing back the default. A `system.template` repoint did
  not move the seed source; a `system.channelroot` repoint did not move the channel tree. The set
  was accepted, persisted, and half-effective, and nothing said so — which is worse than the
  refusal it replaced, because a refusal at least tells you it did not work. The resolver now reads
  the system settings file as the top layer, so a repoint reaches both halves, and derived keys
  follow it (repointing `channelroot` moves all five `channels.*` type-roots with it). Values
  hand-written into `kanibako_config.yaml`'s `system:` table still work and still sit underneath, as
  the floor they always were.

- **All six `workset.channels.*` settings now do what setting them says they do — three of them did
  nothing at all.** The workset channel family declares `common`, `chat`, `share`, `broadcast`,
  `mailboxes` and `share_global`, and kanibako was reading none of them: it took the resolved
  `workset.channelroot` and joined the directory names on by hand. Because those joins *are* each
  key's documented default, everything looked right until you changed one.
  `broadcast`, `mailboxes` and `share_global` had no reader whatsoever — a set was accepted, the
  value was written to `workset.yaml`, `workset get` read it straight back, and not one byte moved.
  `mailboxes` was the one that could actually mislead you: repointing it looked for all the world
  like you had relocated your box's own inbox, and `~/channels/inbox` stayed exactly where it was.
  `chat` and `share` were worse in a quieter way, because half of kanibako honoured them: the
  `~/channels/workset/chat` mount followed your override while the launch kept creating and
  rotating `general.md` and `broadcast.md` in the *old* directory — which is mounted nowhere. A box
  whose workset repointed `chat` therefore had an empty `~/channels/workset/chat`, and a growing
  pile of chat logs on the host that nothing could read. ⚠️ **If you repointed `workset.channels.chat`,
  your existing logs are in `<channelroot>/chat` and will not be picked up automatically — move
  them into the directory the key names.** Every leaf is now resolved through its own key, in the
  one place the workset directory keys have always been resolved, so a repoint reaches the mount,
  the seeded log files, and the `meta.box.inbox` / `meta.box.share_global` / `meta.box.share_workset`
  addresses together. `workset.channelroot` is now a value the launch resolves too: it carried a
  documented default that nothing supplied, so a settings file could reference `@workset.channelroot`
  and get nothing back. See [MIGRATION.md](MIGRATION.md) §2.51.

- **The any-agent defaults `template`, `canon`, `run_args` and `transform` are settable, and the
  refusal that pointed at them stopped lying.** Six agent behaviour keys were settable by their bare
  names — `model`, `access`, `endpoint`, `bootstrap`, `allow_helpers`, `continue_mode` — and the
  rest of the declared set was not, though the settings spec declares all of them alike. Typing the
  full `agent.default.template` got you a refusal saying *"set the any-agent default with the bare
  key (e.g. 'template')"*, and `template` then answered `unknown config key`: a cure that prescribed
  a command that fails. `run_args` and `transform` had no working spelling at all. All four take a
  set now, at the bare name, exactly as `model` always has, and the per-agent forms
  (`agent.<agent>.run_args`, `agent.<agent>.transform`) work too. The settable surface is derived
  from the declared key list rather than hand-listed beside it, so it cannot fall behind again.
  `transform_settings` is the one member that stays unwritable from the command line — its value is
  a table, not a scalar — but it is now refused by name, explaining the shape and pointing at the
  settings file, instead of being denied as a key that does not exist. ⚠️ That refusal also closed a
  second door: `kanibako box set pref.agent.<agent>.transform_settings=…` used to be accepted, and a
  pref is installed at its target during resolution, so it delivered a plain string where the
  harness expects a map. It is refused now with the same explanation. If you already have one stored
  in a box or workset settings file, remove it — it was never going to work.

- **`system.setup_completed` is settable and resettable, as the settings spec has always said it
  is.** The setup version marker is described in the spec as persisting and user-resettable, and
  every verb refused it: *"'system.setup_completed' is a structural config key and cannot be set
  from the CLI"*, with advice to hand-edit `kanibako_config.yaml` instead. Hand-editing that file is
  exactly what the CLI now does for you — the same table, the same absence of validation — so the
  refusal bought no safety; it only withheld `reset`, which is the supported way back to *"setup has
  never run"*. `get` was refused too, so a value you could see in the file could not be read by the
  tool that stores it. All three verbs work now and all three name the same table, which is the one
  the setup staleness gate reads. ⚠️ Setting a marker newer than your installed kanibako will make
  the next command stop and tell you to upgrade or re-run `kanibako setup`; that was already true of
  a hand-edit.

- **`set box_image=…` wrote a different file than `set box.image=…`, and `get` refused the spelling
  both of them had just stored.** Every underscore-joined form of a routed key — `box_image`,
  `box_enable_vault`, `system_agent`, `workset_channels_broadcast` — was accepted by `set` and
  `reset` as a second spelling of the dotted key. It was never a declared key, and no other verb
  served it: `get` answered `unknown config key: box_image` for the exact string a successful `set`
  had printed back at you, because the confirmation echoed the undeclared form rather than the one
  you typed. The two spellings also disagreed about *where the value went*. The rule that picks the
  destination file reads the scope off the key as typed, and `box_image`'s first dotted segment is
  the whole string — so the flat form landed in the `[box]` table of `kanibako_config.yaml`, the
  bootstrap floor beneath every tier, while `box.image` landed in the system settings tier above
  it. Picking a spelling silently picked a precedence, and neither spelling said so. The keyspace
  is closed and a key has exactly one spelling: the flat form is gone from every verb and is
  refused by name like any other key that does not exist, the destination rule can no longer fall
  through to a second file, and `set`/`reset` confirmations name the dotted key you can retype.
  Only the CLI spelling changed — nothing about stored files, the `[box]` table `setup` writes, or
  the cascade moved.

- **Writing a workset directory key the way the documentation spells it created a directory called
  `@meta.workset.path`.** Settings files store their entries unresolved — `@`-references, `$XDG_*`
  and `~` are kept verbatim and resolved per launch — and the shipped default for all five workset
  directory keys (`workset.workspaces`, `boxes`, `logs`, `channelroot`, `registry`) is written
  `@meta.workset.path/<name>`. But the code that reads those keys outside a launch, to find your
  workset and lay out its tree, expanded only `~`: a copy of the documented default put your box
  store in a literal directory named `@meta.workset.path`, and `$XDG_DATA_HOME/worksets` landed in
  one named `$XDG_DATA_HOME`. Because a launch resolved the same key correctly, the two disagreed
  about where a workset's boxes lived. All five keys now resolve through one route that shares the
  launch grammar, so `@meta.workset.path`, `$XDG_*` and `~` mean the same thing on both sides. That
  route runs before the launch snapshot exists, so `@meta.workset.path` — the workset's own root,
  and the anchor of every one of those defaults — is the only reference it can resolve; any other
  one is now refused by name, quoting the value and the file it came from, rather than becoming a
  directory.

- **A standalone project never received its workset handbook chapter.** `workset.canon` is declared
  uniform in every mode — a lone box has a workset tier exactly as a named workset does — but the
  directory that supplies it was only ever created on the `workset create` path, so a standalone root
  had no `canon/handbook` and the chapter bind was silently omitted. Standalone roots are stamped now,
  and stamped with the canon half only: a workset template seeds *future* boxes, and a standalone root
  will never have a second one, so no `template/` directory is created there. The chapter is stamped
  where `workset.canon` resolves to rather than at a literal `canon/` — a standalone root is a
  directory you already had, so it can already carry a `workset.yaml` that repoints the key, and a
  chapter stamped anywhere else is one the bind never reads. `workset.template` is resolved the same
  way on the `workset create` path.

- **A workset that repointed `workset.boxes` or `workset.logs` stopped being recognised as a
  workset at all.** The ancestor walk identifies a named workset root by the directories it is made
  of, but it tested three of those four against hardcoded names rather than against the keys that
  declare them — so repointing `workset.boxes` or `workset.logs` moved the real directory while
  detection went on looking for `boxes/` and `logs/`, found nothing, and resolved the root as an
  ordinary primary-mode directory. Only `workset.workspaces` was resolved correctly. All three are
  resolved now: the walk reads the root's `workset.yaml` when one is present and falls back to the
  declared defaults when it is not, so detection uses the layout you configured without ever
  depending on that file existing. `vault/` remains a fixed name, deliberately — no key names it;
  it is only the default parent of `workset.vault_ro` and `workset.vault_rw`.
  ⚑ Known gap, unchanged by this fix: a repointed `workset.boxes` root is now found, but box trees
  are still created under `<root>/boxes`. Repointing that key is not yet safe end-to-end.

- **`workset create --no-vault` did nothing, and `--image` discarded the rest of the `box:` table.**
  The optional write at the end of `workset create` put `enable_vault` at the top level of the
  workset file, while the code that reads it looks at `box.enable_vault` — so the flag was recorded
  where nothing would ever find it. It is now written where the reader looks. `--image` assigned the
  whole `box:` table rather than setting `box.image` inside it, discarding any other `box.*` key
  already in the file; it now merges.
  ⚑ Known gap, unchanged by this fix: a box **inside a named workset** still will not see
  `box.enable_vault` from its workset file — that resolver reads the box tier only, by design — so
  `--no-vault` takes effect for a standalone project and not yet for a named workset member.
  ⚑ Both values were also top-level keys in a workset-tier file that are not scope names, so they
  were carried into the merged settings snapshot as undeclared keys.
  ⚠️ **`workset create --standalone` is now REFUSED (exit 1), where it used to be accepted.** Its
  only action was writing a top-level `standalone: true` that nothing has ever read; that write is
  gone rather than replaced, because no declared key expresses "this workset's boxes default to
  standalone mode" — nor could one, since a box's mode is detected from its own directory and never
  stored. What the flag asked for does not exist either: a standalone box's workset root *is* its
  project directory and its workset partition is `__STANDALONE__`, so it belongs to no working set
  and a working set cannot have standalone members. The flag stays declared, so the refusal names it
  and hands back `kanibako box create --standalone [path]` rather than argparse's bare "unrecognized
  arguments"; `--help` says the same. A flag that is meaningless for a command is a user error, not
  a silent no-op — the same rule the blanket `--agent` / `--box` flags already follow.

- **Disconnecting an in-tree workset box left its membership row behind, and the orphan then locked
  that workspace out of its own workset.** `workset disconnect` dropped the `boxes:` row only when
  the recorded path was OUTSIDE the workset root. For a box living in the workset's own
  `workspaces/` tree the row survived the disconnect, so the box went on appearing in
  `workset info` and `kanibako list` as a member of a workset it had left — and because a workspace
  path maps to exactly one box name, nothing could register that directory again under any name.
  Hand-editing `registry.yaml` was the only way out. The drop is unconditional now.

- **Connecting a directory inside a workset recorded a path the box never ran on.** `workset
  connect <dir-inside-the-workset>` creates the box's real workspace at `workspaces/<name>` and
  runs it there, but recorded the directory you named instead. `workset info` and `box info` then
  reported that directory as the project path, and the registry held two different paths for one
  box. An in-tree member's row now records `workspaces/<name>`; an external connect records the
  external directory, unchanged.

- **A key the launch path writes on every resolve was not recognised as declared.**
  `meta.box.agent.*` is the read-only mirror of the effective agent subtree, and it carries an
  `auth.*` sub-namespace the agent scope itself does not have — `meta.box.agent.auth.share_support`
  is its own declared row, mirroring the plugin-set capability on the `meta.agent` tier. Key
  validation had no branch for it, so it fell through to the agent-tail rule, which judged it
  against the wrong declared set. An undeclared leaf under that namespace now refuses by name and
  lists the leaves that are declared, and the declared one is accepted where the launch path
  materialises it.

- **A `box: agent:` table left in a settings file is now refused by name, instead of being
  silently discarded.** `box.agent` was retired in 1.8.0 in two different senses — as a scalar it
  was the old agent-*selection* key (`box.crab` → `box.agent` → `box.agent_name`), and as a table
  it was the settable *mirror* of an agent's settings. The write verbs refuse both, but a file
  that already carried one was simply inert: the box launched on whatever the cascade resolved,
  and nothing said the stored intent had been dropped. `box.agent` joins the retired spellings
  that are refused at launch, alongside `box.agent_name` and `system.default_agent`. Which
  refusal you get is decided by the value's shape, because the two retirements have different
  cures — a scalar points at `pref.system.agent`, a table at `pref.agent.<agent>.<key>`, with
  your own stored keys and values interpolated so each line is copy-pasteable. Boxes that carry
  the table have been running the agent's untweaked settings all along; see `MIGRATION.md`
  § *Settings keys renamed or retired*.

- **`box set box.agent.<key>` now always says the key is retired, instead of sometimes
  complaining about its value first.** `box.agent.<key>` — the settable box-scoped mirror of an
  agent's settings — was retired in 1.8.0, and the refusal names the replacement
  (`pref.agent.<agent>.<key>`). But the set-time resolution probe, which checks that a value's
  `@`-references and `$`-variables actually resolve, ran *before* that refusal. So
  `kanibako box set box.agent.model=@some.missing.key` reported the dangling reference and said
  nothing about the key being retired, sending you off to fix a value on a key that no longer
  exists. The retirement is now checked in the same preamble as the other retired spellings, ahead
  of anything that looks at the value. Nothing was ever written to your settings file in either
  case; only the message changes.

- **A manual dispatch can no longer trigger the production promote.** The `promote` job gated on the
  ref shape alone, so dispatching `release.yml` at a bare `v<ver>` tag ran the production publish —
  the dispatch path exists for a branch (dev) or an rc tag (rc), and neither of those is a promote.
  The job now requires a `push` event as well, so **pushing the tag is the only way to promote**,
  which is what the pipeline's one-tag-drives-a-release model already claimed. It was previously
  documented as a pitfall in `docs/RELEASING.md` rather than prevented.
- **A prerelease tag can no longer reach production PyPI.** The `promote` job's guard excluded only
  `-rc`, so a `v1.8.0-dev2`, `-beta` or `-alpha` tag satisfied it: the job started, claimed the
  production publishing environment, and was rejected only afterwards by its own tag-shape check.
  The guard now excludes every hyphenated tag, which is exactly the shape that check enforces, so
  the case cannot arise at all.
- **Development version numbers count published cuts, not workflow runs.** The `dev` job stamped
  `<base>.dev<run_number>`, and that counter increments on every run of the release workflow —
  including build-only validations that upload nothing. The second development cut of 1.8.0 was
  therefore published as `1.8.0.dev98`. The number is now one past the highest `<base>.devN` already
  on PyPI, with a floor of 100 so it stays above the run-number-era versions; if PyPI cannot be
  reached the job fails rather than guessing a number.

- **Edited directives now reach a running box, instead of waiting for the next box start.** The
  flattened instruction file (`~/.claude/CLAUDE.md`, `~/.codex/AGENTS.md`,
  `~/.config/goose/.additionalContext.md`) was written once per agent launch, so any directive source
  you edited mid-session left that file **stale and silent** — nothing announced it, and the agent
  kept reading the older text. The flattener now records a manifest of every file it collected
  (content hashes, plus the paths an import *failed* to resolve), and the box supervisor re-flattens
  when any of them changes — including when a previously missing file appears. Writes are atomic, so
  a harness never reads a half-written instruction file, and an identical render does not rewrite it.
  A file you edited by hand is left alone rather than clobbered.

- **A settings file that is not valid YAML is refused on one line instead of crashing.** Every
  config document kanibako reads went through one loader, and that loader let the parser's own
  exception escape — so a stray tab, an unclosed quote or a mis-indented block in any settings file,
  at any tier, produced a Python traceback with the parse error buried in it and no indication of
  *which* file was at fault. The loader now catches the parse failure where it happens, which is the
  one place that knows the filename, and raises kanibako's own error: `the config file <path> is not
  valid YAML: <the parser's complaint>. Fix or remove the file, then retry.` — one line, exit 1.
  It reaches every command that reads settings, and it is what lets `system diagnose`'s `Storage` and
  `Journal` rows report a malformed `kanibako_config.yaml` instead of shrugging (see the `setup`
  entry above).

- **The missing-vault warning names the vault, not the directory above it.** A box with
  `box.enable_vault` on but no vault directory on disk still launches, and says so — but the
  `(expected at …)` path it printed was the vault's *parent*, so following the advice created a
  directory one level up from where kanibako looks and the warning came back on the next launch.
  It names `box.enable_vault`'s own read-write vault path now, which is the directory to create.
  Advisory as before: nothing fails, and `box.enable_vault=false` still silences it.

- **`config.journal`, `workset.workspaces` and `workset.channelroot` are keys the CLI recognises.**
  All three are declared, resolved and used, and all three answered `Error: unknown config key` —
  values kanibako reads at every launch that the tool storing them could not name.
  `config.journal` now reads back like its five `config.*` siblings (`kanibako system get
  config.journal` prints the resolved lifecycle-journal path; `set` and `reset` refuse it with the
  same bootstrap-file message the rest of Layer 1 gets, because that file is edited by hand).
  `workset.workspaces` and `workset.channelroot` are ordinary settable workset keys —
  `kanibako workset get <workset> workset.workspaces` answers, and `workset set` writes. What each
  of them *does* once set is covered by the entries above on the workset directory keys and the
  `workset.channels.*` family; this is the half that made them addressable at all.

### Changed

- **BREAKING: each settings file is now named for the tier it belongs to.** Every cascade level used
  the same filename, `settings.yaml`, and which tier a given file belonged to was something you had to
  work out from where it sat. The per-tier files are now `box.yaml`, `workset.yaml` and `agent.yaml`:
  a box's settings live at `<box dir>/box.yaml` (for a standalone project, `box_data/box.yaml`), a
  workset's at `<workset root>/workset.yaml` (this is also the root file of a standalone project), and
  an agent's at `<data>/agents/<agent>/agent.yaml`. **The system tier is deliberately unchanged and
  stays at `<data>/global/settings.yaml`** — it is the only one of its kind, so nothing about it was
  ambiguous.
  ⚠️ **Clean break, no compatibility read: a file left under the old name is not read and not
  reported.** Rename yours before launching a box, or it will start on defaults as though the file
  were not there. See `MIGRATION.md` for the per-tier list of what to rename.

- **BREAKING: a workset root no longer carries an identity table anywhere, and a v1.6/v1.7 root
  refuses until you remove the one it has.** A workset has always been *identified* by the global
  registry — `workset create` writes a `name → root` entry into its `worksets:` section, and that
  entry is what `workset list` reads and what resolves a bare workset name. v1.6.0 and v1.7.x also
  wrote a copy of the name, a `created` stamp and a `projects` list into the workset root's own
  `settings.yaml` as `workset.meta`. That copy is gone. A workset root now holds at most two files:
  `registry.yaml`, carrying its box MEMBERSHIP as flat `name: path` rows under `boxes:` and nothing
  else, and `workset.yaml`, carrying SETTINGS ONLY — sparse, optional, and not written at all by
  `workset create`. A brand-new workset root contains four directories and no files. `created` is
  dropped rather than relocated, so `workset info` no longer prints a `Created:` line; nothing
  records when a workset was made. Both retired spellings (`workset.meta`, and the `meta.workset`
  one an unreleased dev build wrote) hard-refuse by name, with the fold-and-delete cure in the
  message; **MIGRATION.md §2.43** is the guide. A per-workset `registry.yaml` still carrying the
  `workset:` or `projects:` sections that same dev build wrote refuses too, for the same reason:
  the `projects:` map held a second copy of every member path, and the two copies drifted.

- **A workset tree you move or copy is still re-discovered, but it now comes back under its
  DIRECTORY's name.** Dropping an unregistered workset tree onto a new machine re-registers it on
  first resolve, as before. What changed is where the name comes from: the tree used to carry one,
  and no longer does, so the import uses the workset root's directory basename — the same default
  `kanibako workset create` has always applied when you give it a path and no `--name`. A workset
  you created without `--name` therefore comes back under exactly the name it had. One you named
  explicitly comes back under its directory's name instead, so rename the directory before you move
  it if you want the old name kept. Two cases to know about: if that name already belongs to a
  DIFFERENT registered workset the import is refused, the tree is left untouched on disk, and the
  error says so — rename or relocate one of the two by hand. If it belongs to a primary BOX, the
  import goes ahead and warns: the bare name resolves to the box, and the workset stays reachable as
  `kanibako workset <cmd> <name>`.

- **BREAKING: every kickoff now carries ONE import, so the base package and the agent plugins must
  be upgraded together.** The kickoff is the file that boots a box's whole instruction chain. For
  one transition release the three plugin kickoffs carried a second, pre-canon import
  (`@~/playbook/…`) alongside the canon entry point, so a plugin build kept working against a base
  that still bound the retired pre-canon layout. That layout is gone — its content moved into the
  canon, which `@~/canon/COLLECTION.md` already reaches — so the second line addressed nothing and
  printed one `unresolved import` warning on stderr at every box launch. It is deleted, from all
  three plugins. **A plugin now requires a base that binds the canon: `kanibako-cli` 1.8.0 or
  newer.** Either half upgraded alone leaves a box whose kickoff resolves nothing and whose
  directives silently stop loading, so upgrade through the `kanibako` meta package, or upgrade the
  plugins and the base in one step. The launch warning is gone with the line, which makes any
  future one a real signal rather than expected noise. See [MIGRATION.md](MIGRATION.md) §2.6.

- **A spawned helper's entrypoint script moved out of `playbook/`.** A helper box's directory
  layout carried a `playbook/scripts/` directory holding `helper-init.sh` — `playbook` being the
  pre-canon name for what is now the canon handbook, and the wrapper level carrying nothing else.
  A helper now gets a flat `~/helpers/<n>/scripts/`, and a **parent's** own override copy of the
  script is read from `~/canon/notebook/scripts/helper-init.sh`, the canon's own address for a
  reusable script. If you never customized `helper-init.sh` there is nothing to do; if you did,
  move it, because a copy left at the old path is read by nothing and warned about by nothing. The
  two sides are addressed differently on purpose: a parent is a real box with a canon, while a
  helper home has no canon binds at all, and giving one a `canon/` directory would make the launch
  materialize a canon skeleton it was never meant to have. See [MIGRATION.md](MIGRATION.md) §2.44.

- **BREAKING: the flattened directives file has a new link format, and generated section headers
  are gone.** Kanibako assembles your directive tree into one file for the agent to read. That file
  used to label every imported chapter with a machine-generated `## canon_bible_general_…_md`
  heading and point every reference at it. Chapters are now emitted under **their own** headings,
  so the flattened document reads as a single coherent outline instead of a list of slugs.

  A new import form, `[Display Text](@path/to/file.md)`, both includes a file **and** links to it.
  In a numbered list the row's number becomes part of the generated heading (`## 1.1 Identity &
  Environment`), and the link resolves to it; outside a list the display text is used. Heading
  depth follows the heading enclosing the list plus the row's own nesting, so an included chapter
  always sits beneath the section that included it. **The bare `@path` form is unchanged** — it
  still includes the file, it simply produces no link and no heading.

  ⚠️ **If you hand-wrote a link to a generated anchor** (`[see](#canon_handbook_general_…_md)`) in
  your own directives, that anchor no longer exists. Point it at the chapter's own heading instead.
  Nothing else in your directive sources needs to change.

- **A chapter that contains nothing no longer produces an empty section.** A directive file that is
  only comments and whitespace — the stock `ROM_AGENT.md` / `SYS_AGENT.md` placeholders are exactly
  this — used to contribute a heading with no body under it. Such files, and index files that
  contain nothing but imports, are now left out of the flattened output entirely; their imports are
  still followed, so anything they pull in still appears. **When a linked chapter is left out, its
  table-of-contents row is dropped and the surviving rows renumber**, so the numbering a reader sees
  never points at a section that is not there. The files are still read and still watched: give one
  real content and it reappears on the next reload.

- **The size warning no longer names a specific agent.** The flattener warns when the assembled
  directives file passes 32 KiB. That message said codex would truncate it; harnesses differ (codex
  stops at 32 KiB, others allow more), so on a roomier harness it was a false alarm naming the wrong
  tool. It now reads *"WARNING: agent directives file exceeds 32KiB, the limit for some harnesses /
  agents."* — the same conservative threshold, stated as what it is.

- **BREAKING: `COLORTERM=truecolor` is a declared default now, and nothing writes it into your
  settings.** v1.7.2 seeded the value into the global `env` file on first run, and 1.8.0 development
  briefly wrote it as a settings key instead; **neither happens.** It is declared at **box** scope in
  kanibako's own defaults file, so it resolves for every box with nothing stored anywhere —
  including the installs the old first-run write never reached, since that write fired only on a
  genuinely fresh host. A `box.env.COLORTERM` of your own still wins, by the ordinary cascade.
  **What breaks is turning it off: there is no longer a line to delete.** Disabling truecolor now
  takes an explicit override — `kanibako box set <box> --null box.env.COLORTERM` leaves the variable
  unset in the box, and `kanibako box set <box> box.env.COLORTERM=` sets it to the empty string.
  ⚑ And because kanibako now declares the variable at box scope, a `COLORTERM` key of your own at
  **any other** scope is a contested slot and refuses the launch (§2.33) — re-spell it
  `box.env.COLORTERM`. The launch notice about retired `env` files says so too: `COLORTERM` was the
  one line kanibako itself put in them, and it is the one line that must be deleted rather than
  migrated. See [MIGRATION.md](MIGRATION.md) §2.42.

- **`kanibako box show --effective` lists the behavior defaults it always applied.**
  `allow_helpers`, `continue_mode`, `bootstrap` and `access` were literals inside the launch code, so
  with nothing stored the effective view had no row for them at all while the launch went on using
  them. They are declared defaults in kanibako's own defaults file now, and the display reads the
  same floor the launch does — so the four rows appear, carrying the values that were always in
  force. **No behavior changed; only what you can see.**

- **BREAKING: `create --standalone` no longer registers the box — `--register` opts in.** A
  standalone box carries its whole identity inside its own directory, and the global
  `registry.standalone` entry buys exactly one thing: addressing the box by name *from another
  directory*. Writing that entry at create assigned a global name to a box whose point is to
  move freely, so **it is now something you ask for**: `kanibako create --standalone --register`.
  **`--name` is ignored without `--register`** (with it, `--name` sources the entry's name), and
  a box created independent is adopted later by `kanibako box register <path>` — index-only and
  seed-free, so nothing is re-seeded. Nothing about the box itself changed: same layout, same
  identity, same `workset.kuid`, and working *inside* it never needed the entry — `kanibako
  start` from the box's own directory resolves it from its in-tree marker as before. What breaks
  is a bare **name** used from elsewhere (`kanibako start <name>`, `box info <name>`, `--box
  <name>`), which reports the token as unresolvable until the box is indexed. `--register` is
  standalone-only: a default-mode box's registration is its workset membership, which is not
  optional. See [MIGRATION.md](MIGRATION.md) §2.41.

- **BREAKING: the variables kanibako derives for an agent are settings entries now, and a key
  naming one refuses the launch.** Five environment variables are *computed* from an agent's
  resolved settings rather than written by hand: goose's `GOOSE_MODE` (from the permission tier),
  `GOOSE_MODEL`, `GOOSE_PROVIDER` and `OPENAI_HOST` (from `model`, `provider` and `endpoint`), and
  claude's `ANTHROPIC_BASE_URL` (from `endpoint`). They used to be pasted onto the container's
  environment after everything else had been decided; **they are ordinary agent-scope entries
  now**, arriving through the same channel as every other variable — so `-e` overrides one for a
  launch like it overrides any key, and a variable set two different ways is a **refusal instead
  of a silent overwrite**. (`kanibako box show --effective` still does not list them: it reports
  stored configuration, and a realization depends on the flags of a launch that has not happened.) **A settings key naming one of these five now stops the launch**, at any
  scope, naming both the key you wrote and the key that *drives* the variable. `GOOSE_MODE` is
  derived on **every** goose launch, so any `env.GOOSE_MODE` will refuse unconditionally — set the
  `access` key, or use `-S` / `-A`. The other four refuse only when their driving key resolves to a
  value: set `model`, `provider` or `endpoint` instead, or pass `-e VAR=value` for one launch.
  Nothing kanibako ships declares any of the five as a key, so a default install cannot hit this.
  See [MIGRATION.md](MIGRATION.md) §2.40.

- **BREAKING: an environment variable may be declared at one scope only.** Declaring the same
  variable at two scopes — `system.env.EDITOR` and `box.env.EDITOR`, say — used to start the box
  with the innermost scope's value and say nothing about the declaration it discarded. **That
  arrangement now refuses the launch, naming both keys and the cure.** A variable is a slot with
  one value, and kanibako assembles a box by letting each scope act in turn from the outside in,
  the first to claim a place keeping it — the same rule two bindings at one destination have
  always followed. Give the variable one owner and delete the other key. **Overriding a value is
  unaffected and works exactly as before:** the *same* key written in more than one file is the
  ordinary cascade, so a system file may set `box.env.EDITOR` as a default for every box and a
  box's own file may set `box.env.EDITOR` and win. Nothing kanibako ships declares an `env` entry
  at two scopes, so a default install cannot hit this. See
  [MIGRATION.md](MIGRATION.md) §2.33.

- **A `synced` copy may now share a destination with a binding, and both are delivered.** Until
  now, declaring a `synced` copy whose destination was exactly a binding's destination refused the
  launch outright: *"a 'synced' copy and a 'binding' mount target the same destination"*. That
  refusal is gone. The arrangement was never broken — a `synced` copy is written *through* the
  binding that covers its destination, into that binding's host source, so a copy at the exact
  destination lands in the bound directory itself. **It overwrites content there; it does not
  replace the mount, and the rest of the binding is untouched.** Copying onto a binding is a thing
  you may legitimately want, so kanibako no longer second-guesses it. A `synced` copy under a
  `masks` destination is still skipped (a tmpfs has no host source to write into), as is a copy of a
  *directory* at a mask's own destination — it would leave the mask half-populated. **A single
  *file* copied at a mask's own destination is delivered, and that mask is then not mounted for the
  box at all**: one file filling one void is total, so nothing is left half-hidden. A copy whose
  covering binding is read-only is still skipped with a warning. See
  [MIGRATION.md](MIGRATION.md) §2.29.

- **BREAKING: the environment variables an agent plugin sets are now ordinary settings, and you
  can override them.** Each plugin used to hand its variables straight to the container —
  claude's `DISABLE_AUTOUPDATER`, goose's `GOOSE_DISABLE_KEYRING` and `CONTEXT_FILE_NAMES`, the
  per-agent `KANIBAKO_DIRECTIVE_FINAL` slot — on a private path that sat above your whole
  configuration. **They are now declared defaults at the agent scope**
  (`agent.<agent>.env.<VAR>`), so you can override one by writing the same key in a settings file
  the way you would any other setting. **The breaking half:** because they are ordinary keys, they
  take part in the one-owner rule above. If you had set the *same* variable at another scope — say
  `box.env.DISABLE_AUTOUPDATER` — your value used to be silently discarded in favour of the
  plugin's; that configuration now **refuses the launch and names both keys.** The cure is the
  same one owner: drop your key and override the plugin's key instead, at whatever scope you like.
  See [MIGRATION.md](MIGRATION.md) §2.34.

- **BREAKING: an agent's own environment variables are ordinary settings now, resolving where the
  agent scope resolves — and arrangements that launch today will refuse: a twin of a variable at
  another scope, an unexpanded `$NAME` in a value, and an `env:` or `secret_path:` table nested
  under a second `<agent>:` level.**
  A variable set in an agent's settings file — `kanibako agent set claude env.EDITOR=vim`, or an
  `env:` block in `agents/<node>/agent.yaml` — was delivered to the box on a path of its own,
  *underneath* every `<scope>.env.<VAR>` value instead of at the position an agent-scope key has in
  the cascade. Four things followed from that, none of them announced: a `system.env.EDITOR` beat
  it, though the agent scope outranks system; the plugin's own declared default beat it, so
  `agent set` could not in fact override one; a persona's stored value beat it, though the agent
  file is meant to win as the only place your own edits live; and a `~` or `$VAR` in the value
  reached the box as literal text, while the identical value written in a system or box file
  arrived expanded. **The agent file's `env` table is an ordinary `agent.<node>.env.<VAR>` key
  now** — it resolves above `system` and below `workset`, it takes the same expansion every other
  setting takes, and it overrides a plugin default by simply being the same key in a nearer file.
  **Four arrangements that used to launch will now refuse.** An agent-file variable *and* a twin
  of it at another scope (`box.env.EDITOR`) are two keys for one slot, which is the one-owner rule
  above — the box used to take the other scope's value silently. An agent-file value containing a
  name kanibako's own namespace does not carry, `$HOME` being the likely one, is refused by name
  the way it always has been in a system, workset or box file; escape it (`\$HOME`) to have it
  delivered as written. And an `env:` or a `secret_path:` table nested under a second `<agent>:`
  level in an agent settings file (`self: claude: env:`, `self: claude: secret_path:`) is refused
  by name, with the key the spelling actually reads: **`self:` is not a key, it is an alias for
  `agent.claude`**, so a `claude:` level under it reads `agent.claude.claude.env` — the node named
  twice, which is not a key and never was. Both used to resolve as though written the short way,
  and in a file carrying both spellings the flat table replaced the nested one wholesale, so an
  entry spelled only there vanished without a word. Move them up one level, to `self: env:` /
  `self: secret_path:` (an all-agents entry to the system file's `agent: default: <category>:`);
  `kanibako agent set <agent> env.VAR=…` writes that shape for you. ⚑ `secret_path` is worth
  checking even if you never hand-edited an agent file — that nesting predates the move to the
  flat table. `bindings:` moved with them; the entry below has the whole file shape. See
  [MIGRATION.md](MIGRATION.md) §2.35.

- **BREAKING: an agent's settings file has one level — every category is written directly under
  `self:`, and a nested `self: <agent>:` table refuses the launch.** `self:` is not a key: it is
  an alias standing for `agent.<that agent>`, and the file already belongs to one agent, so its
  root already *is* that node. A table under a second level therefore names the node twice —
  `self: claude: bindings:` reads `agent.claude.claude.bindings`, which is not a key and never
  was. `env` and `secret_path` were flattened first (above); **every remaining category follows:
  `bindings`, `caches`, `seeded`, `common`, `synced` and `masks` are read flat now, and the
  agent's behaviour keys (`model`, `access`, `endpoint`, …) sit directly under the root beside
  them.** The nested spelling is **refused by name**, with the key the spelling actually reads and
  the flat table to write instead. This affects files you may never have hand-edited: `bindings:`
  was the last table still written the nested way, so an agent settings file untouched since
  v1.7.2 will carry it. Two more consequences, both of arrangements that used to launch: **the
  all-agents `self: default:` level has no agent-file spelling at all** — that tier is written in
  the system file as `agent: default: <category>:`, which is what the refusal's cure names; and
  **a nested behaviour key** (`self: claude: model:`) refuses too, where a flat `model:` in the
  same file used to beat it silently. The reason for refusing rather than continuing to accept: it
  was never one spelling but two, and a file carrying both lost the nested table *wholesale* —
  entries spelled only there were absent, not overridden, with nothing said. See
  [MIGRATION.md](MIGRATION.md) §2.37.

- **BREAKING: the `agent` verbs joined the closed keyspace — `agent set`, `get` and `reset` refuse
  what is not a key, and an undeclared scalar in an agent file refuses the launch.** `agent set`
  used to accept nearly anything (`shell=zsh`, `self.model=opus`, `anything.at.all=x` — rc 0,
  stored), and two accepted spellings actively broke the file: a `bindings.*` write stored a shape
  the launch refuses, and a scalar `transform_settings` crashed every later `agent` command. Now:
  an undeclared key refuses by name with the file unchanged (the live keys — state, `name`,
  `run_args`, `env.<VAR>`, `secret_path.<VAR>`, plugin-declared leaves — still write); the
  bind-shaped categories refuse with the retirement message (hand-edit is the route, and the
  message shows the shape); a table-valued key given a scalar refuses naming the expected shape;
  `get` and `reset` speak the same vocabulary as `set` (one read carve-out: `agent get <agent>
  bindings.ro.<dest>` still answers, agreeing with `get`); and the launch snapshot's old
  "forward-compat" passthrough is closed — an undeclared scalar already in the file refuses the
  launch by name, while `agent list`/`info` still display the file and `agent reset --all`
  remains the recovery. Also fixed: a dotted destination reads back whole (`agent get claude
  "bindings.ro.~/.cache/uv"`), where it used to print "(not set)". See
  [MIGRATION.md](MIGRATION.md) §2.38.

- **BREAKING: the permission axis is a tier, not a boolean — `auto_approve` is now `access`.** The
  agent-scope `auto_approve: true|false` is retired and replaced by `access`, which takes
  `restricted` (everything prompts), `editing` (free for contained edit-class work, ask at the
  boundaries) or `full` (today's bypass, and the default). A boolean could not express the middle
  tier, and the middle tier is the one most people actually want. **Your stored value maps
  `true` → `full` and `false` → `restricted`**, and kanibako will not do it for you: a settings
  file whose cascade contribution still carries `auto_approve` refuses the launch, quotes your own
  value, names the tier it means and hands over the command to write it. What the file
  *contributes* is the whole of it — an `agent:` table in a `box.yaml` is dropped before the merge
  and set no tier to begin with, so it is dropped with a warning rather than refused, by the same
  rule the closed-keyspace entry below states. Refusing rather than ignoring is deliberate and
  specific to this key — an undeclared key is not read at all, so a box you had deliberately set
  to `auto_approve: false` would otherwise have come up at the permissive default with nothing
  said. An unrecognised tier is rejected at both ends, `set` time and launch, and never treated as
  permissive. See [MIGRATION.md](MIGRATION.md) §2.1.

- **BREAKING: an undeclared key in ANY settings file now stops the command, naming every one it
  found.** The keyspace is closed, and *setting* a key kanibako does not declare was already an
  error that named it (so was *reading* one at `system get`) — but *resolving* one was not, at any
  scope. A `box.yaml` carrying `box: {zippity: wibble}` parsed, merged and came out of the cascade
  as `wibble`, after which nothing read it: no error, no warning, and nothing in `box show`
  marking the line as dead. Every command that builds the resolved snapshot now refuses —
  measured: `start`, `shell`, `box info`, `box show --effective`, `system show --effective`,
  `rig list` — with one message listing **every** offending entry, the reason for each, and the
  settings files that resolve loaded (which of them carried the entry is not knowable from the
  merged snapshot). `box show` without `--effective` never resolves and so never carries THIS
  message — it marks the line instead (see the `box get` / `workset get` entry below), and
  `setup`/`system diagnose`/`rig diagnose` print it in full, `setup` stopping at rc 1 (see the
  `kanibako setup` entry under **Fixed**) — see [MIGRATION.md](MIGRATION.md) §2.47 for which is
  which. **The cure is a hand-edit and the message says so**: `box reset` cannot remove what is
  not a key, and `box show --effective` resolves through the same seam, so it refuses as well.
  Two deliberate non-refusals: an agent whose plugin is not installed here is not judged at all —
  neither its table nor the keys under it, because an agent's keys are its plugin's to declare and
  without the plugin there is no list to check them against — and data addressed inside a
  declared key (a bind or copy destination, a `masks` entry) is a value rather than a key path of
  its own and is not judged as one. The first one is bounded by what could *be* an agent, not by
  what is installed: `agent: common:`, `agent: env:`, `agent: seeded:` and every other category
  spelling are judged wherever an `agent:` table is read at all, because kanibako declares that
  list itself and an agent can never be named from it. (Which files read one is a separate
  question with its own answer — see [MIGRATION.md](MIGRATION.md) §2.11.) **The cost that remains, stated: a name kanibako has simply never heard of is
  indistinguishable from a harness you have not installed**, so both `agent: goose: zippity:` and a
  typo'd `agent: clade: zippity:` resolve on a machine without goose, and `zippity` refuses on one
  with it. There is no list of every agent that will ever exist to check a name against.
  `agent: default:` is judged everywhere. §2.38 closed this same
  passthrough for the per-agent `agent.yaml` file; this is the same rule over every settings file
  and the whole resolved snapshot. See [MIGRATION.md](MIGRATION.md) §2.47.
  **A key kanibako RETIRED still gets the message written for it**, not this generic one: before
  printing, the refusal asks whether the files it loaded carry a spelling it has a cure for — asking
  only of the tables those files actually contribute, so a table your settings drop before the merge
  (an `agent:` block in a `box.yaml`, a `pref:` block outside a workset or box file) cannot answer
  for a key it has nothing to do with. **Every seam that judges a settings file for a retired
  spelling reads it that way** — the launch's permission check and the agent-selection check as
  well as this one, so a dropped table produces no cure at any of the three.
  Without that, arming the resolve took the tailored refusals away from the users they were
  written for — the retired agent-selection keys (`box.agent_name`, the scalar and table spellings
  of `box.agent`, `system.default_agent`) and the retired permission boolean `auto_approve`, each
  of which explains what changed and hands over a command to paste. One thing is lost at this
  earlier seam and is stated rather than hidden: it runs before kanibako settles which box it is
  looking at, so a cure that names a `box set` / `workset set` subject carries the `<box>` /
  `<workset>` placeholder instead of the name. See [MIGRATION.md](MIGRATION.md) §2.1.

- **BREAKING: `box get` and `workset get` refuse a name that is not a key, and the stored view
  lists the entries your settings file carries that are not keys.** Both verbs answered
  `(not set)` at rc 0 for anything at all — a typo, a key this release retired and a real key you
  had not set were one answer. That was the last hole in the *reading* third of the closed
  keyspace, which `system get` and (§2.38) `agent get` already enforced: an undeclared name is now
  refused at rc 1, naming the key and the reason the keyspace gives for it. `(not set)` at rc 0
  now means what it says — a **declared** key with nothing stored at this noun. Three reads are
  deliberately untouched: `pref.*` and `config.*` keys, and the hand-authored bind and category
  entries `<scope>.bindings.{ro,rw}.<dest>` and `<scope>.{caches,seeded,common,synced}.<dest>`,
  whose CLI write route retired in 1.8.0 but whose read spec §0 keeps — *"refuse the write; keep
  the read honest"* — because the retirement message prescribes a hand edit and a hand edit is
  only checkable if the read-back works. (`<scope>.masks.<dest>` is not in that group and never
  was: `masks` never had entry names, so it takes the ordinary refusal.) **Paired with it, and in
  the same change because one is unusable without the other**: `box show` / `workset show` /
  `system show` — the stored view, no `--effective` — now print any entry the keyspace does not
  declare under their own heading, naming the file to open. The cure for such an entry is a hand
  edit and nothing else, so a user who cannot see the line cannot follow the cure. That block
  displays FILE CONTENT rather than reading a key: nothing is resolved, no default is invented,
  and the value is echoed as the file spells it. It marks only what the keyspace refuses — data
  inside a declared key stays unmarked, and so does a table that is declared but that this file's
  tier may not set (an `agent:` table in a `box.yaml`), which is a different fact.
  See [MIGRATION.md](MIGRATION.md) §2.48.

- **BREAKING: the four `KANIBAKO_*` variables kanibako sets for itself are ordinary settings now,
  and a twin of one at another scope will refuse the launch.** `KANIBAKO_NAME`, `KANIBAKO_AGENT`,
  `KANIBAKO_DIRECTIVE_SEED` and `KANIBAKO_AGENT_MARKERS_DIR` — the box's name, the agent it runs,
  the in-box path of your kickoff file and the directory agent sessions write liveness markers to —
  used to be written onto the container after your settings had been resolved, above every settings
  file and above `-e`. **They are `system.env.<VAR>` keys now**, derived at launch and entered at
  the system scope's floor, reaching the box through the same channel as every other variable; they
  appear in `kanibako box show --effective` among the box's environment variables, listed as `env
  KANIBAKO_NAME = …` (the bare variable name — those rows report the merged environment, not a
  key). **The breaking half:** because they are ordinary keys, they take part in the
  one-owner rule above, so a `box.env.KANIBAKO_NAME` alongside kanibako's own key is two keys for
  one slot and **now refuses the launch and names both** — where it used to launch with your value
  overwritten a moment later and nothing said. The cure is the same one owner: drop the other key
  and write `system.env.KANIBAKO_NAME` instead. **Two things that were impossible now work:**
  overriding one by writing the same key in a nearer settings file (the ordinary cascade, no
  refusal), and `-e` — `kanibako start -e KANIBAKO_NAME=scratch` wins for that launch, where the
  flag used to be accepted and silently have no effect. ⚑ Three of the four are read back by
  kanibako itself (`kanibako stop`, `kanibako code` and the credential watcher inspect
  `KANIBAKO_AGENT`; the in-box supervisor watches `KANIBAKO_AGENT_MARKERS_DIR`; the flatten step at
  agent start opens `KANIBAKO_DIRECTIVE_SEED`), so overriding one is telling kanibako something
  about the box that has to be true. See [MIGRATION.md](MIGRATION.md) §2.36.

- **BREAKING: `-e` overrides the key that owns the variable, and a malformed `-e` item now stops
  the launch.** `kanibako start -e VAR=value` used to be pasted onto the container's environment
  after your settings had been resolved — a last layer on top of the finished result, above every
  file and outside the settings system entirely. **It is the CLI level of the cascade now**:
  the value overrides *the key that owns that variable*, for that launch only, applied while
  kanibako is deciding the box's variables rather than after. A working `-e` behaves exactly as
  before, at any scope, the `system.env.KANIBAKO_*` stamps above included; a `-e` naming a variable
  no key owns still injects it for the launch, as an ephemeral CLI-level entry belonging to no
  settings file. It writes nothing and `kanibako box show --effective`, which reports stored
  configuration, does not show it. **The breaking half:** a malformed item used to be dropped in
  silence — `-e JUST_A_NAME` (no `=`) was skipped, `-e =value` set a variable whose name was the
  empty string, and an illegal name (`-e 2FA=x`, `-e A-B=x`, `-e A.B=x`) went straight to the
  container runtime. **Each of those now refuses the launch, naming the offending item, before the
  box is touched** — a flag that overrides a key must not look accepted and do nothing. A variable
  name is a letter or underscore followed by letters, digits or underscores, the same shape an
  `<scope>.env.<VAR>` key is held to; an empty value is still legal (`-e QUIET=`). See
  [MIGRATION.md](MIGRATION.md) §2.39.

- **BREAKING: a bare-relative host source is refused where it is declared, and an abstract
  category's bare leaf is rooted where it is declared — at all four scopes and in your own settings
  files.** v1.8.0 announced this rule ("category sources are rooted at their declaration, not at
  assembly", in the 1.8.0 notes below) and exactly one loader implemented it: the one that reads an
  agent plugin's own bundled file. Every other path stored what the author typed and handed it to
  podman unchanged — and **podman reads a source beginning with neither `.` nor `/` as the name of
  a named volume, and creates it**, which `--rm` never removes. So such an entry bound nothing: it
  made an empty volume in the user's rootless store and mounted that at the destination instead of
  their directory, and the `rw` arm additionally created the relative path as a directory in
  whatever directory `kanibako` was run from. The two copy categories (`seeded`, `synced`) never
  reach podman, so theirs went the quieter way: read as a path under the process CWD, and usually
  not there at all. **The three concrete categories — `bindings.ro`, `bindings.rw`, `synced` — now
  refuse such a source by name**, at every scope, a `./x` spelling included, because they take no
  declaration root anywhere and so no later layer may supply the one a relative source needs.
  **The three abstract categories — `common`, `caches`, `seeded` — root a bare leaf under the
  declaring scope's store instead** (`@config.data`, `@meta.agent.<agent>.path`,
  `@meta.workset.path`, `@meta.box.path`, each plus the category's own subdirectory), so what is
  stored resolves on its own and those entries start reading a real directory. A source that
  already resolves — absolute, `~`, `$var` or an `@`-ref — is stored exactly as written; the root
  is a default for a relative source, not a prefix applied to everything. `masks` and `secret_path`
  are untouched: a mask declares no source and a secret pointer is a scalar, so neither goes
  through this rule. ⚑ **This refuses input that was accepted before**, and nothing kanibako ships
  teaches the spelling — no shipped default, example or doc; claude's own `common` entries are the
  one place a bare leaf appears, and they go through the loader that already rooted it — so it
  reaches only a settings file written by hand. See
  [MIGRATION.md](MIGRATION.md) §2.50.

### Removed

- **BREAKING: the four flat compatibility shims are deleted — `kanibako.agent_config`,
  `kanibako.agent_defaults`, `kanibako.settings_resolve` and `kanibako.vscode_config` no longer
  exist.** Package-ification moved them to `kanibako.settings.*` / `kanibako.vscode.*`, and
  development builds briefly kept re-export aliases at the old flat paths that worked and emitted
  a `FutureWarning`. **Those aliases do not ship.** v1.8.0 is a deliberate clean break, and an
  alias that keeps working *is* the deprecation window this release declined to open; the removed
  code is preserved in git history, not in the wheel. Importing a legacy path now raises
  `ModuleNotFoundError`. **This reaches users, not only plugin authors:** the agent plugins pin no
  upper bound on `kanibako-cli`, so an old plugin beside a new core is what an unpinned upgrade
  produces by default. Every `kanibako-agent-claude` from `1.7.0` through `1.8.0rc1`, and
  `kanibako-agent-codex` / `-goose` through `0.3.0`, import at least one removed path;
  `kanibako-agent-claude` `1.8.0.dev95`+, `-codex` `0.6.0` and `-goose` `0.5.0` are clean. Nothing
  crashes — a plugin that cannot import is reported by name on standard error and skipped, and
  every other agent plus `kanibako setup` keeps working (see the discovery fix below). The cure is
  to upgrade that plugin, or to install the `kanibako` meta package, which pins a compatible set.
  See [MIGRATION.md](MIGRATION.md) §3.1, which lists the affected versions.

### Fixed

- **One broken agent plugin could make the entire CLI unusable, including the command that fixes
  it.** Agent adapters are discovered by importing every registered plugin, and that import was
  unguarded. A plugin built against a different version of kanibako raises `ImportError` from its own
  module body, and that escaped discovery as a raw traceback — killing whatever command you ran, even
  though you weren't using that agent, because resolving *which* agent to launch enumerates all of
  them. `kanibako setup` uses the same discovery, so the documented cure died the same way and
  editing installed files by hand was the only way back in. A plugin that fails to load is now
  reported by name on standard error, with what still works and what to do about it, and then
  skipped. Every other agent keeps working. The two fallback discovery paths already tolerated a
  failing plugin; the main one now matches them.

- **`kanibako-agent-goose` is now 0.5.0 and `kanibako-agent-codex` is now 0.6.0; older ones are
  refused.** Their published `0.3.0` packages predate the `access_realization` and top-level `env:`
  changes, so the current kanibako cannot load them — it refuses them by name, which is correct but
  left no version to upgrade *to*, since `0.3.0` was the newest published. Both were republished at
  `0.4.0`, and again at `0.5.0` when their plugin code and seeded canon changed further. Codex moved
  once more, to `0.6.0`: the published `0.5.0` calls a probe helper that this release replaces, so
  that wheel would fail to import against the new CLI. Goose stayed at `0.5.0` — its content has not
  moved since it was published. The meta package requires the current pair. **If you installed the
  meta package and saw errors about `safe_bypass`, `container_env`, or `BindDefault`, upgrading is
  the fix.**
  Unlike the CLI and the Claude plugin, these two are **not** stamped with the release train — they
  carry their own version, so a change to either has to be published under a new number or it cannot
  ship at all. The release now refuses to build when their content has moved and the version has
  not, rather than uploading under the old number and silently keeping the old files.

- **Installing a pre-release of the meta package could pull an older Claude plugin than the one it
  was built with.** The meta pins the CLI to the exact version it shipped with, so the two can never
  be mixed while PyPI's index catches up — but `kanibako-agent-claude`, which is released from the
  same version stamp, was left as a range. Python's version ordering puts a release candidate *above*
  a development build, so installing a `.dev` meta resolved the Claude plugin to an older published
  `rc` and paired it with a CLI it was never built against, producing an `ImportError` on a
  completely clean install. Both halves of the stamped set are now pinned together. The goose and
  codex plugins keep their ranges, because they genuinely are released on their own schedule.

- **A box could come up at a bare shell prompt instead of starting its agent, and nothing said
  why.** PID 1 of an agent box checks that it can import the supervisor before running it, and falls
  back to a plain shell keep-alive if it cannot — insurance for an older image that ships no
  supervisor. That check ran exactly once, at the busiest moment of a box's startup, and discarded
  the reason it failed. So a transient failure produced a box sitting at a shell with the agent
  never started, no explanation anywhere, and `kanibako start` still reporting success. The check
  now runs twice before giving up, records why it failed to `~/.kanibako/supervisor-fallback.log`
  inside the box, and announces the fallback on standard error, where `podman logs` will show it.
  The fallback itself is unchanged — a degraded box is still better than no box — but it can no
  longer happen silently.

- **A read-only directory anywhere in your vault could stop a box from starting at all.** Kanibako
  snapshots `vault/rw` before each launch and prunes the oldest snapshots to stay inside the
  retention limit. Deleting a directory requires write permission on the directory that contains
  it, so a read-only directory copied into the vault — a reference tree, an archived checkout,
  anything with its permissions preserved — produced a snapshot that could not be removed. Pruning
  raised `PermissionError` from inside the launch, and because that happens before the container is
  created, **the box could not be started until the offending directories were moved out by hand.**
  Pruning now widens directory permissions when, and only when, an ordinary removal has already
  failed, and a snapshot that still cannot be reclaimed is reported and skipped rather than
  cancelling your launch. Housekeeping is no longer able to refuse to start a box.

- **Helper boxes inherited the director's browser endpoint by accident of timing.** With
  `--browser`, kanibako starts a headless browser sidecar and gives the box its address as
  `BROWSER_WS_ENDPOINT`. The helper hub had been handed the box's environment as a live reference a
  moment *before* that address was written, so the write reached back into it and every helper
  spawned afterwards carried the variable too — while a helper on a box whose sidecar failed to come
  up carried nothing, from the same code. The hub now takes its own copy of the environment when it
  starts, so a helper gets exactly the environment the box was described with and nothing that was
  added later. **If you were relying on helpers reaching the director's browser sidecar, they no
  longer do.**

- **Your own binds were mounted twice on any box with helpers or image sharing on.** kanibako
  resolves two small extra passes at launch — one for the helper socket and message log, one for the
  shared image store — and each of those passes read your whole settings cascade, not just its own
  two entries. Every `bindings.*`, `caches` and `common` destination you had declared was therefore
  emitted a second time, from unresolved rows: a duplicate `-v` for each, and a `masks` entry that
  should have hidden one of them was defeated, because the second emission never saw the mask.
  Each pass now emits **only the destinations it owns**; everything you declare is emitted once, from
  the one place that resolves it. Two mounts landing on one of those internal destinations (from
  your settings and from kanibako's own) is now refused by name rather than resolved silently — the
  same rule, and the same message, as everywhere else in 1.8.0. See
  [MIGRATION.md](MIGRATION.md) §2.2.

- **A settings key named for one of the store's own members was accepted, then unreadable.**
  `insert_segments` is a public method on the resolved store, and it was not a reserved leaf
  name — so `box.env.insert_segments` (and the like) was accepted and stored. Attribute reads
  returned the method rather than your value, because attribute lookup finds a real class member
  before it ever consults stored keys; the value survived only by subscript and looked simply
  absent everywhere else. `insert_segments` and `RESERVED_KEY_NAMES` now join the `dict` method
  names already refused at write time. **A key spelled either way is now refused by name instead
  of silently swallowed** — rename it.

- **Two reserved-key refusals gave a reason that was not true.** Both explained themselves as a
  clash with a `dict` method name. That was never the whole reason, and for the two names above
  it is simply wrong — neither is a `dict` method. The messages now state the actual rule: the
  name would shadow a real attribute on the store, and dunder names are the store's attribute
  space rather than key space.

- **A mask did not hide anything — it made the path read-only.** The tmpfs a `masks` entry mounts
  was emitted with podman's default `tmpcopyup`, which copies whatever already sits at the
  destination up into the fresh tmpfs. Everything that was there stayed plainly visible inside the
  box (read-only), which is the opposite of what a mask is for: a mask is a void, and there is
  nothing inside it. Masks are now mounted `notmpcopyup` and show empty. **This changes what an
  existing mask does at your next launch** — content you could read through a mask disappears. See
  [MIGRATION.md](MIGRATION.md) §2.25.

- **A box with the vault disabled silently got no masks at all.** Turning the vault off
  (`box.enable_vault`) also discarded every `masks` entry the box declared — no tmpfs, no warning,
  nothing in the log — because the mask mounts were still emitted from inside the block that used to
  hold the vault's own mounts, back when the only mask was a single hardcoded tmpfs over
  `~/workspace/vault`. That mask was dropped and the vault's mounts moved out to the category
  resolver; only the wrapper stayed, gating an ordinary user key on an unrelated setting. A declared
  mask is now emitted regardless. **This changes what a vault-disabled box sees at your next
  launch** — a path you asked to hide, which has been readable all along, becomes empty. See
  [MIGRATION.md](MIGRATION.md) §2.26.

- **A `masks` list in a settings file was silently dropped.** `box.masks: ["~/secret"]` — the
  spelling v1.7.x used — reached the launch as a plain list, missed the shape guard that emits the
  tmpfs, and produced no mount and no message: the host path you had asked to hide stayed plainly
  readable inside the box. `masks` is a map keyed by box destination, so a list at `<scope>.masks`
  is now **refused by name**, printing the shape it should have been written in
  (`{box_dest: true}`). A category that vanishes without a word is the one outcome the closed
  keyspace forbids. The shipped defaults file no longer spells its own (empty) `masks` default as a
  list either. See
  [MIGRATION.md](MIGRATION.md) §2.24.

- **A blocked template seed blamed the wrong thing.** Seeding into the managed canon region
  (`canon/COLLECTION.md`, `canon/bible/…`, `canon/handbook/…`) is refused, but the refusal said the
  seeded content "would be silently invisible — never merged, never an error", which describes a
  file that quietly loses to a mount. That is not what happens: box create materialises that region
  root-owned, so the copy fails with `EACCES` and stops the create outright. The message now leads
  with the permission failure and keeps the shadowing as the secondary reason, so the cure you reach
  for matches the failure you actually hit.

- **A refused read said the key was "not settable" and told you to re-run setup.** `system get` on a
  structural config key answered with the wording for a write, then prescribed a write cure — for an
  operation that never attempted one. Each verb now states what it actually refused, and the read
  points at the config file holding the value instead of at `kanibako setup`. `reset` was borrowing
  the same wording and now states its own.

- **A category entry could not be read back when its destination contained a dot.**
  A `get` of `<scope>.<category>.<dest>` split the key on `.` to find where the value lives, so a
  real destination — `~/.cache/uv`, `~/.claude/plugins` — was cut apart mid-path and the read
  answered `(not set)` for an entry the launch mounts. Destinations are spelled guest-side now, so
  the dotted case is the common one. A destination is DATA: the key stops at the category and
  everything after it is one destination. This also makes good on the promise the refusal for these
  keys prints — *"Reading it back with `kanibako box get <box> …` still works."* — which until now
  held only for a destination with no dot in it.

- **`box show --effective` prescribed a command that does not work for undoing a suppression.** A
  suppressed entry was reported with *"Undo with 'reset pref.<…>.<dest>'"*; there is no way to
  address one entry of a dest-keyed key from the CLI, so that reset was refused. The line now names
  the edit instead: remove the entry from the `pref:` table of the settings file at the scope that
  set it. Which scope that is deliberately stays unnamed — the merged snapshot no longer records
  which file wrote the request, so naming one would be a guess.

- **The in-box helper client could hand a caller someone else's message.** `recv()` and the
  request path shared a receive buffer and a socket timeout, but only the request path locked
  them, so the concurrent send-and-receive the client is built for could return a peer's push
  where a response belonged. Locking alone would not have been enough: the hub multiplexes
  responses and peer pushes onto one socket, and a push is written by the sending box's own
  thread, so it can arrive between a request going out and its answer coming back. The socket
  now has a single owner that reads it and routes each message to the caller waiting for it.

### Changed

- **An ambiguity at a destination that is also masked is no longer silent.** When two of `caches`,
  `common` (or one of each) target the same destination in ONE scope, kanibako keeps the existing
  ordering and warns — every launch — so the ambiguity stays visible until you fix it. A `masks`
  entry at that same destination used to suppress that warning entirely, from any scope: the mask
  takes the destination over, so nothing was said about the two declarations underneath it. It is
  not suppressed now. They are still ambiguous, and a mask over them says nothing about which of
  the two you meant. **No warning was removed and none is duplicated** — one ambiguity is still one
  line per launch. (A launch that is refused because two mounts claim one destination may now print
  this warning before the refusal; what is refused, and its message, are unchanged.)

- **Two of the refusals for a key that collides with a reserved name are reworded.** A settings key
  may not be named after a Python dunder (`__init__`) or after a dictionary method (`get`, `keys`,
  `items`, …); both would shadow part of the structure the resolved settings live in, so both are
  refused when the value is validated. The two refusals are shorter now. Nothing about what they
  refuse has changed and neither was removed — only the wording. As with the path messages below,
  this is called out because the text is what you see, and because anything matching on these
  strings (a script grepping output, a test pinning a message) will need updating:
  `is reserved: dunder-pattern names (__x__) are not allowed (they are Python data-model
  attributes)` is now `is reserved: dunder names (__x__) not allowed (dict attributes).`, and
  `is reserved: it shadows a dict method. Reserved names: …` is now
  `is reserved: (dict method name). Reserved names: …`.

- **The warnings and errors from path resolution are reworded.** The messages about XDG variables,
  workset and box discovery, vault location, and the refusals that protect `$HOME` from being used as
  a project root are shorter and more plainly punctuated; a few name their subject more precisely.
  No message was removed, and with one exception nothing they report has changed — the exception is
  the missing-vault warning, whose `(expected at …)` path was wrong and is corrected under *Fixed*.
  This is called out because the text is what you see, and because anything matching on these strings
  (a script grepping output, a test pinning a message) will need updating. The one that is most likely
  to be matched on: `Refusing to create a project rooted at $HOME` is now `Refusing to create project
  rooted at $HOME`.

- **A mount set that cannot be assembled now stops the launch instead of being quietly abandoned.**
  A box's mounts are assembled by folding every scope's declarations over the box home in scope
  order, and that fold has always had rules about what an arrangement may be: a binding may nest
  inside another but not take its point or sit above it; a binding may not sit inside a mask; a mask
  may not land on another mask, nor at or above home; a `seeded` destination must be inside home; a
  `synced` entry may not take a binding's exact destination; and a binding's options may not
  contradict its arm. Until now, breaking one of those abandoned the fold silently and the launch
  fell back to the older mount-resolution route — so the rule was not a rule, and which mount set the
  box received depended on which route ran. Each of them is now a launch error naming the
  participants and the cure. **A default install cannot reach any of them**: every one requires a
  binding, mask, seed or sync entry you wrote yourself. `kanibako box show --effective` reports the
  same refusal without starting anything. Relatedly, nothing may now be bound at the box home — home
  is the foundation the rest of the set folds over rather than a binding among them, so an entry at
  `~` is a second claim on one place and refuses (see the next entry). The refusal for two bindings
  at one destination also states the cure the same way its sibling does — suppress the entry you do
  not want, since an override is not enough. See [MIGRATION.md](MIGRATION.md) §2.31.

- **A box's home is no longer a binding, and can no longer be repointed with one.** Writing an entry
  at `~` in a settings file used to override the home binding kanibako ships and win — the
  documented way to give a box a custom home. There is no such binding any more: the box home is the
  *foundation* the whole mount set folds over, established from the box's own store before anything
  else is considered, so an entry at `~` is a second claim on that one place and **the launch now
  refuses it by name**. Nothing else moves — a binding at a destination *inside* home (`~/work`) is
  unaffected, and the mount a box actually receives at `~` is byte-identical to before. **The cure
  is `workset.boxes`**, the workset-scope key naming where box stores live; a box's home is derived
  from it. Home also leaves the per-scope `bindings.*` listing in `kanibako box show --effective`
  and appears at the top of that block as a labelled foundation line, so the one mount every box has
  is still visible in the view that exists to show what a box gets. See
  [MIGRATION.md](MIGRATION.md) §2.32.

- **A mask now hides the binds nested under it.** A box's mounts are assembled by folding every
  scope's declarations over the box home in scope order, and in that fold a `masks` entry clears
  everything at or inside its destination — which is what a mask means. Until now a bind declared
  *inside* a masked directory was mounted anyway and stayed visible through the mask, so the mask hid
  only whatever nothing else had claimed. **This changes what a box with a mask over a bind sees at
  your next launch, with no config change.** Nothing else about the mount set moves: the same
  destinations, from the same sources. One ordering note: the whole mount set is now emitted
  shallowest-first — the agent's own delivery binds (its binary, launcher and shared install dir)
  included, where they used to be emitted ahead of everything else — so a nested mount always
  follows the mount it sits inside. Mount options for read-write binds now
  spell `rw` explicitly (`Z,U,rw` rather than `Z,U`) — podman's default either way — and a bind
  dropped for a missing source names its destination rather than the destination as you spelled it
  (`/home/agent/canon`, not `~/canon`) — as do the two warnings on the create-time seed path, which
  is assembled by the same fold. See [MIGRATION.md](MIGRATION.md) §2.27.

  A mask and a bind that name the **same** destination are decided by that same fold now: the one
  declared at the more specific scope takes the destination (`system` → `agent` → `workset` → `box`),
  so a bind declared for your box replaces a mask inherited from the agent or the workset, where the
  mask used to win that destination outright. The tmpfs masks a box receives come from the assembled
  mount set itself, so a mask that lost its destination is no longer mounted over the bind that took
  it. ⚑ The reverse nesting — a mask declared at a *broader* scope than the bind sitting inside it —
  is not enforced: that arrangement still delivers both, as it always has. Treat it as unsupported
  rather than as a way to keep a bind inside a mask.

- **A missing bind source is handled by destination, not by who declared the bind.** There are three
  answers to "the host source is not there at launch": the launch stops with a clean error (the
  agent's own delivery binds — its binary, launcher and shared install dir), the bind is dropped
  silently (the optional canon chapters, and the agent's best-effort shares), or the bind is dropped
  with a warning (everything else). Which one a bind got used to depend on how it was declared rather
  than on where it lands: the agent's delivery binds were emitted by a separate emitter carrying its
  own rule, and the silent case was a flag on the declaration. The whole mount set is assembled in one
  place now, so the answer follows the **destination** and applies to whichever declaration wins that
  destination, at any scope. The practical difference: if you point one of the agent's delivery
  destinations at a source of your own (`box.bindings.ro` at `~/.local/bin/<agent>`, say) and that
  source is missing, the launch now stops with `Error: <agent> mount source disappeared before
  launch` instead of warning and starting a box with no agent binary in it. See
  [MIGRATION.md](MIGRATION.md) §2.28.

- **A `synced` entry lands inside the bind that covers its destination.** `<scope>.synced` files are
  re-copied into the box on every launch — this is how host credentials get there — and their
  destination is written box-side (`~/…`). Until now exactly two destinations resolved correctly:
  anything under `~/workspace` went to the project directory and everything else under `~` went to
  the box home. A synced destination inside any *other* bind — a vault path, a channel path, one of
  your own — was written underneath that mount rather than into it, so the box never saw the file and
  nothing said so. The destination now resolves through the assembled mount set, and the copy lands
  in the covering bind's host source. Where there is nothing to deliver to — the covering bind is
  read-only, or is a `masks` entry, or no bind covers the destination at all — the entry is skipped
  with a warning naming the bind that decided it, in place of the single `guest_dest … is outside
  /home/agent` message. Synced entries are also applied later in the launch now: after the mount set
  is final, after the plugin's credential sync (so a `synced` entry aimed at the same host file wins
  where it used to lose), and after the three checks that can abort a launch, so a launch that fails
  one of them no longer refreshes your synced files on the way out. See
  [MIGRATION.md](MIGRATION.md) §2.29.

- **`synced` entries are now written once when the box is created, and a `seeded` entry at the same
  destination is no longer discarded.** Two changes that only make sense together. A `synced` entry
  is re-copied on every launch, but only when the host source is newer than the copy already in the
  box — and that comparison only means anything if the copy in the box was written by the sync in the
  first place. Nothing made that true: `seeded` files are written once when the box is created, with
  the source file's own timestamp preserved, so a seed source that happened to be newer than the sync
  source pinned the *seed's* content at a synced destination for the life of the box, silently, and
  most often at a credential. `box create` now writes every `synced` entry once, unconditionally,
  immediately after seeding — into the bind that covers the destination, exactly as a launch does.
  Because the destination then holds sync-written content from the start, the launch-time check
  compares against the sync's own previous write and the problem cannot arise. The consequence you
  may notice: a destination declared under **both** `seeded` and `synced` now keeps both entries —
  the seed is applied first and the sync overwrites it — where the seed entry used to be dropped
  outright. See [MIGRATION.md](MIGRATION.md) §2.30.

- **`agent.<agent>.transform` now decides whether a binary transform runs.** The key names WHICH
  transform an agent uses; until now nothing read it, and the tweakcc patch ran for any agent whose
  settings merely carried a `transform_settings` dict — including agents tweakcc cannot patch.
  `transform_settings` is that transform's CONFIG INPUT, never the switch. The claude plugin
  declares `tweakcc`, so a claude box behaves exactly as before; a goose or codex box that had
  picked up a `transform_settings` table no longer has claude's patcher run against its binary. Set
  `agent.claude.transform` to the empty value to turn patching off, or set it at any scope
  (`pref.agent.claude.transform` in a box or workset) to override. A transform named that this
  kanibako cannot run, and a `transform_settings` set with no transform named, are both reported at
  launch rather than passing silently.

- **New fixed box directory `~/.kanibako/`; the helper socket and message log moved into it.**
  Inside a box they are now mounted at `~/.kanibako/state/helper.sock` and
  `~/.kanibako/state/helpers.jsonl` instead of under `$XDG_STATE_HOME/kanibako/`. A mount
  destination has to be concrete before the box is running, so honoring a box's XDG settings
  host-side meant guessing at them in four separate places — and those places had already
  drifted apart. `~/.kanibako/` is one fixed location for anything in that class.
  **XDG still works:** once the box is up, `$XDG_STATE_HOME/kanibako` is symlinked onto
  `~/.kanibako/state`, so the old path keeps resolving. ⚑ One exception, for boxes created
  before this release: they already have a real `~/.local/state/kanibako` directory, which
  kanibako will not delete — remove it inside the box once to get the symlink. See
  [MIGRATION.md](MIGRATION.md) §2.22.

- **Every bind-shaped category entry is now written keyed by its DESTINATION, and entry names are
  gone.** `caches`, `seeded`, `common` and `synced` join `bindings.ro` / `bindings.rw`: the category
  is a single key whose value is a map from box destination to `[host_src]`, so
  `<scope>.<category>.<name>` is no longer a key at any scope. **This is a stored-format change —
  every settings file, and every plugin that declares agent-scope defaults, has to be re-spelled.**
  There is no shim and no deprecation window; a file still in the old shape is refused loudly,
  naming the entry. A `get` of `<scope>.<category>` now reads the whole map (it also reads
  `<scope>.bindings.{ro,rw}` and `<scope>.masks`, which had silently answered `(not set)` since
  they went dest-keyed). See [MIGRATION.md](MIGRATION.md) §2.23.
  ⚑ `seeded` and `synced` are still **copies**, not mounts. Sharing a way of writing an entry down
  says nothing about what is done with it.

  **⚠️ Known limitation.** A dest-keyed category is one key with many facets inside one value, and
  there is no settled surface yet for reading or writing *one facet* of such a key. The category
  read above works at all three file-scope nouns when you name the subject
  (`kanibako box get <box> box.caches`, `kanibako system get system.caches`). A per-agent
  category table is read at the **agent** noun instead (`kanibako agent get <agent> caches`);
  asking a file-scope noun for `agent.<agent>.caches` is refused and points there. A readable
  form is planned and its shape is not decided, so treat today's behaviour as provisional. See
  [MIGRATION.md](MIGRATION.md) §2.23 for how to verify an edit meanwhile.
- **Seed and sync destinations are spelled guest-side.** The three template seed layers target
  `~/` rather than a host path under the box store, and kanibako resolves that to the box store when
  the copy runs. Nothing about *where the files land* changes; the spelling is now the same one
  every other category uses. See [MIGRATION.md](MIGRATION.md) §2.23.

### Removed

- **`set` / `reset` on every bind-shaped category.** `caches`, `seeded`, `common` and
  `synced` join `bindings.ro` / `bindings.rw` in refusing a write at every scope, including the
  source-only repoint that changed an entry's host source without touching its destination. All six
  are now **YAML-only**: edit the settings file for the scope you want and re-launch.
  The categories are *not* retired — they are still declared, still read by the launch cascade so
  every existing entry keeps being delivered, and **`get` still reads them** (at the
  category key; see the dest-key entry above). Only the write verb is gone.
  See [MIGRATION.md](MIGRATION.md) §2.20.
  Rationale: these categories are now a single key whose value is a map keyed by the mount
  destination, so there is no per-entry key left for `set` to name — and keeping the route for four
  categories while two refused would have been two rules for one shape.

## [1.8.0] - 2026-08-01

This release completes the **settings-keyspace rework** and lands the **canon books**.
The keyspace is now genuinely closed and single-routed: a box can *request* values at
keys that resolve before it (`pref.*`), the agent that runs is itself a key selected
before the cascade resolves, category sources resolve at their declaration instead of
being prefixed at assembly time, and a contested mount destination is decided by an
explicit collision table rather than a silent rank order. The canon replaces the in-box
`~/playbook` tree with four books under one root —
`~/canon/{bible,handbook,notebook,workbook}` — delivered as read-only sibling binds over
a protected skeleton.

v1.8.0 is a deliberate **clean break: no aliases, no deprecation window, and no
migration code.** Four released config surfaces are removed outright
(`box.agent_name`, `system.default_agent`, the `shared` mount category and
`system.base_template`), and directory layouts move both on the host and inside boxes.

> ⚑ **Read [MIGRATION.md](MIGRATION.md) before upgrading.** The short version: your
> first `start` / `create` / `reauth` after upgrading hard-errors until you run
> `kanibako setup`; every box that ever chose an agent refuses to launch until its
> agent key is replaced; the agent plugins must be upgraded **with** the base (install
> the `kanibako` meta-package) or every box's directive chain is silently lost; and two
> host directory moves — `agents/claude/{plugins,cache}` and the `commons` channel dir
> — lose visible content *silently* if skipped.

### Added

- **`pref.*` — a box or workset can request a value at a key that resolves before it.**
  A `pref:` table in a box or workset settings file installs values at strictly
  earlier-resolving keys (`pref.system.agent`, `pref.agent.<agent>.<key>`). Requests are
  collected and validated *before* the cascade assembles, so every key derived from a
  requested value sees the requested value. Three independent filters check each request
  (is the target a key at all, is it on the allowlist, does it sit in a forbidden tier)
  and a rejection names key, level and rule. Writing a `pref` is bounded to workset and
  box files, and the written value is validated against its target when it is typed — a
  request that would fail every future launch is refused at `set` time.
- **`--null` writes a suppression.** `kanibako <scope> set --null <key>` stores a real
  YAML `null` wherever the store is a nested document — the one channel a box has to
  remove an entry it inherits. The sibling `reset` verb *removes* the entry instead.
  Where a store cannot represent a suppression the flag is refused with the reason and
  the cure (see *Fixed*).
- **A box can opt out of an agent entirely.** `kanibako box set --null pref.system.agent`
  gives a plain-shell box *even when a host-wide default is set*: no agent binds, no
  credentials delivered, no agent template layer, no `KANIBAKO_AGENT` stamp — and `stop`
  writes nothing back for it. The state itself was reachable in 1.7.2 via the `no_agent`
  pseudo-agent (`--agent no_agent` / `box.agent_name`); what is new is this spelling.
- **The canon books — one root for everything a box reads.** The in-box instruction tree
  is now four books under `~/canon/`, entered at `~/canon/COLLECTION.md`:
  - `bible/` — packaged core guidance as per-scope chapters (`general/`, `workset/`,
    `box/`) plus a per-agent chapter shipped by the agent plugin. Read-only, from the
    installed packages.
  - `handbook/` — host-side guidance, assembled from each scope's own contribution:
    `general` from the system store, and `agent` / `workset` / `box` chapters from the
    agent store, the workset, and the box store's `canon/handbook`. Read-only in-box;
    edit it host-side. Per-scope chapters are skip-if-absent. The **box** chapter is
    started at `create` from the three template roots' `box/canon/handbook/` subtrees
    (system, then agent, then workset; later overlays earlier, per file,
    create-if-absent), copied host-side into `box.canon`'s `handbook/` — the same
    directory the read-only chapter bind then reads. Repoint the sources with
    `system.template` / `agent.<agent>.template` / `workset.template` and the
    destination with `box.canon`; there is no separate seed key for it
    (MIGRATION.md §2.5(c)).
  - `notebook/` and `workbook/` — box-owned and writable: box directives/procedures, and
    box working state (devnotes, tasks, plans). Seeded once at `create`.

  `box create` materialises a root-owned, mode-555 skeleton of book roots, chapter
  mountpoints and file mountpoints, and launch mounts each chapter as an individual
  read-only **sibling** — no mountpoint ever lives inside a bind source, and neither
  `~/canon` nor `~/canon/bible` is ever bound whole. Protection is re-asserted after
  every container start (the home bind's `:U` re-chowns the source), and the lifecycle
  verbs escalate to delete or copy a protected tree. If a host cannot root-own the
  skeleton, `create` says so loudly and the box works normally, unprotected.
  **New boxes only** — an existing box keeps launching and gains the new bible, but its
  own `~/playbook` directives stop being loaded (MIGRATION.md §2.4 has the recipe).
- **`<scope>.canon` keys** — `system.canon`, `agent.<agent>.canon`, `workset.canon` and
  `box.canon` name each scope's handbook-contribution root; repointing a scope's
  contribution goes through its key. `workset.canon` / `box.canon` are CLI-settable,
  `agent.<agent>.canon` at system scope only, and `system.canon` is a structural path
  key in `kanibako_config.yaml` (like `system.template`).
- **The base package ships the kickoff.** `~/.config/kanibako/kickoff.md` — the file that
  boots a box's instruction chain — is now core-owned and delivered by an internal
  `box.bindings.ro.kickoff` bind pointing at the canon. Because every published plugin
  still ships its own kickoff at the same destination, the core bind **yields** to a
  plugin-supplied one for this release (keyed on the destination, so the two can never
  collide into a launch error). The plugin-side deletion lands one release later,
  together with a base-version floor.
- **The abstract categories record what each declaration derived.** `common`, `caches` and
  `seeded` expand into concrete bindings and copies as the settings collapse, and that
  pairing — a declaration and the mount or copy it produced — is materialised into the
  launch snapshot instead of being discarded. It is written at a reserved internal node
  spelled `binding_derivations`, and that node is **not a key**: it cannot be set, reset,
  read back or referenced, the closed keyspace refuses the spelling like any other name it
  does not declare, and a `binding_derivations:` table written into a settings file is
  dropped with a warning. What it is *for* is the display — `kanibako box show
  --effective` prints each abstract declaration with a
  `binding_derivations.<declaration-key>` line beneath it naming what the box actually
  receives (see *Fixed* under **Unreleased** for that display's own history).
- **`box.images_store` — the host image store behind `box.share_images`, as a key you can
  set.** `box.share_images` gives a box a read-only view of the host's podman image store so
  it does not pull what the host already has. *Which* store that was had no spelling: a
  runtime probe found the podman graph root, and if you wanted a different one there was
  nothing to say it in. It is a declared box key now — `kanibako box set <box>
  box.images_store=/path`, cascadable to the system tier for a machine-wide repoint — the
  probe result is only its default, and the mount the box receives is sourced from it, so
  setting the key **moves that mount**. A store that never sets it resolves exactly as
  before. ⚑ The generated `storage.conf` that makes the store mount usable is internal
  machinery with a fixed location, not a key; there is nothing to configure there.
- **`kanibako setup` names the template files it kept for you.** Step 5 compares your
  template store against the one this build ships and reports what it would add and what it
  would overwrite. A third case was silent: a file you had edited that the shipped copy has
  since moved past is neither added nor overwritten — kanibako keeps yours — and nothing said
  so, which read as though the file were current. The step now prints `Kept YOUR copy (N
  file(s) differ from the shipped one):` and lists each path, on the interactive prompt and
  under `--refresh-templates` alike. One consequence: a store whose *only* difference is
  files in that class no longer reports `Templates are up to date` — it prints the list and
  asks, so you can see the divergence you are carrying.
- **`meta.box.path`** — a read-only per-mode anchor for the box root. The per-mode
  variation now lives in the anchor rather than in three downstream spellings, so
  `box.bindings.rw.home` is one declaration for every mode. The anchor and its settable
  source are both shape-validated (a `workset.boxes: null` used to yield a `/home`
  host source that was then created and mounted over the box home, silently).
- **Braced references — `@{a.b.c}suffix`.** A reference may now carry a literal suffix,
  which the greedy dot-segment name pattern previously ate (`@meta.box.name.jsonl`
  parsed as a reference *named* `…name.jsonl`, resolved to nothing, and coerced to the
  empty string — a path silently lost its filename). Bare `@a.b.c` is unchanged and
  stays the normal spelling; nesting is refused loudly.
- **Standalone boxes get a real box-scope settings file** at `<root>/box_data/settings.yaml`
  (absent until first written); the project-root `settings.yaml` keeps playing the
  workset tier. Values stored at a legacy standalone root still resolve as downward
  defaults, so no box needs migrating — but two read surfaces get truthful (see
  *Changed*).
- **The dev extra pins the gate tooling** (`ruff`, `mypy`, `pytest`). CI installed them
  floating, so an upstream release could change the verdict on source nobody had
  touched; a local green now predicts CI again, and adopting a new tool version is a
  deliberate commit of its own.
- **BREAKING: `workset share add` / `rm` lose their NAME argument — a share is identified by its
  destination.** `workset share add WS NAME host:guest` becomes `workset share add WS host:guest`,
  and `workset share rm WS NAME` becomes `workset share rm WS DEST`, taking the box destination
  exactly as `share list` prints it. The raw `share list` columns change from `NAME / MODE /
  BIND(host:dest)` to **`DEST / MODE / SOURCE`**, and the messages follow (`Added rw share 'data'`
  → `Added rw share at '/home/agent/data'`; `no share 'x'` → `no share at 'x'`). Re-running `add`
  at a destination that already has a share replaces its source. ⚑ **An existing share written by an
  earlier version must be re-added**: the stored shape changed with it, and an old `name: [src,
  dest]` entry is MISREAD rather than rejected (a two-element value now means `[source, options]`),
  so the share name is read as the destination and the real destination as mount options. Nothing
  is mounted in the wrong place — the launch fails at the container runtime, which will not accept a
  path as a mount option — but it fails without naming the cause, which is why re-adding is the
  cure. `kanibako workset share list` refuses such a file outright, naming the offending entry. The name never
  distinguished two shares in the first place: two shares at one destination were already an error,
  decided on the destination and never on the name, so the name was a label that could not affect
  any outcome. `--effective` output is unchanged.
- **`kanibako --restart [box]` — stop a box and start it again in one step.** The cure named
  by the new running-box flag refusals (below): it stops the box, then launches it fresh with
  the invocation's flags in force, so `kanibako --restart -N mybox` does what
  `kanibako start -N mybox` looked like it did. It composes the `stop` verb rather than
  reimplementing teardown, refuses to relaunch if the stop did not take, and is a no-op-then-start
  on a box that was not running. It is also the ONE thing that bypasses those refusals — passing
  it is the statement "I know this needs a fresh container". Spelled bare because `start` is the
  default subcommand.

### Changed

- **BREAKING: a box no longer names its agent with a key of its own — it REQUESTS one.**
  `box.agent_name` is retired; the replacement is `pref.system.agent`, a request written
  in the box (or workset) settings file to set a key that resolves *earlier* than the
  file making it.

  ```yaml
  # box settings.yaml
  pref:
    system:
      agent: goose        # was:  box: {agent_name: goose}
  ```

  - `kanibako box set pref.system.agent=<name>` writes it; `kanibako create --agent
    <name>` persists the request, so a plain `kanibako start` runs that agent —
    unchanged behaviour, new storage.
  - Selection now runs once, early, through one seam: a narrow lenient pre-pass resolves
    `system.agent` with prefs applied, and the resolved selection is installed at the top
    precedence level *whatever chose it* (a pref, `--agent`, or the single-installed
    autopick), so the snapshot's `system.agent` equals the agent that actually runs and
    everything derived from it derives from the truth. (Prefs are new in 1.8.0; the state
    being contrasted here is an earlier point in this release, not anything 1.7.2 shipped.)
  - **A box that still carries `box.agent_name` REFUSES TO LAUNCH**, naming the key, the
    file and the one-line fix. It is not migrated automatically and it is not ignored:
    guessing would launch a *different* agent and seed that agent's credentials into the
    box.
- **BREAKING: `system.default_agent` → `system.agent`**, and it moves out of the reserved
  `agent.default` table into the `system:` table of the same settings file
  (`<data>/global/settings.yaml`). `kanibako setup` writes the new location; `kanibako
  system set system.agent=<name>` now works as an ordinary setting (it was
  previously special-cased). A stale `agent.default.default_agent` is refused by name,
  like the key above.
- **BREAKING: the settable `box.agent.*` mirror is retired.** A box tweaks its agent's
  settings with `pref.agent.<agent>.<key>` (including `null` to suppress an inherited
  bind); the effective values are readable at the read-only `meta.box.agent.<key>` via
  `--effective`. `box set` / `reset box.agent.<key>` refuse with the replacement
  spelling, and a bare `box set model=…` now points at `pref.agent.<agent>.model`.
- **BREAKING: the `shared` mount category is renamed `common`, and agent keys must be
  discriminated.** `<scope>.shared.<name>` becomes `<scope>.common.<name>`, with no
  alias. Separately, the plugin-defaults readers emitted bare `agent.<category>.<name>`
  keys that a launch-time re-root patched onto the active slot — the bare form is not a
  key at all under the closed keyspace. Readers now build the discriminated
  `agent.<agent>.<category>.<name>` key directly, and the bind/mask/env key patterns
  refuse an undiscriminated agent scope, so `set` rejects it instead of quietly
  accepting a non-key.
- **BREAKING: the `commons` channel type-root is renamed `common`** — one word now names
  both the mount category and the channel, discriminated by the `channels.` segment.
  This moves the host directories (`<channelroot>/commons` and each workset's
  `channels/commons`), the settings keys (`system.channels.commons`,
  `workset.channels.commons`) and the in-box paths (`~/channels/commons` and
  `~/channels/workset/commons`) at once. Move the directories before your first launch:
  an empty `common/` is otherwise guarantee-created beside your populated `commons/`,
  silently.
- **BREAKING: contested mount destinations are resolved by an explicit collision table,
  and a working configuration can start failing.** Reconciliation used to resolve a
  contested destination by a fixed rank (seed < cache < binding < shared < synced <
  masks — `shared` being the category this release renames to `common`), which was wrong
  in both directions at once: that entry silently beat a user's real binding while a
  `caches` entry silently lost to one. Now:
  - two concrete bindings at one destination **refuse the launch**, with a message that
    says the rule changed and prints the exact suppress-then-add YAML;
  - an abstraction (`common`/`caches`/`seeded`) extending onto a destination an explicit
    binding already occupies is an **error**; across scopes the nearer scope still wins
    silently; within the winning scope an ambiguity **warns on every launch**;
  - a mask overrides, but contradictions are judged *before* the mask override;
  - `secret_path` is carved out (same variable at one destination is the documented
    per-variable cascade), and mount vs copy destinations are judged separately.

  Default installs cannot hit this — all twelve shipped configurations are verified
  collision-free with standing tests. `kanibako box show --effective` reports collisions
  without launching.
- **BREAKING: category sources are rooted at their declaration, not at assembly.** A bare
  relative `host_src` used to become a real path only because the launch built a table of
  scope roots and prefixed the stored value at assembly time — so the stored key never
  resolved on its own and what a user read in their file was not what mounted. That
  mechanism is deleted:
  - the agent `common` loader now emits self-resolving
    `@meta.agent.<agent>.path/common/<leaf>` sources;
  - **claude's commons move into the agent store's `common/` subdir** —
    `<data>/agents/claude/{plugins,cache}` → `<data>/agents/claude/common/{plugins,cache}`.
    Move them before your first launch or the box binds an empty `common/plugins` over
    `~/.claude/plugins` and every installed plugin appears gone, with no message;
  - `workset share add` absolutises a relative source against the workset root **at write
    time** and stores the result (on the default workset, whose bindings never had a root
    to join, it refuses with the reason); `set` on a bind category refuses a bare
    relative source outright and prints the correctly rooted form. **Already-stored
    relative sources are not rewritten** — they now resolve against the process CWD;
  - a value sitting at a category root, at a `bindings` arm, or under an arm no binding
    declares is refused with the discriminated tier named, instead of being silently
    dropped.
- **BREAKING: `system.base_template` → `system.template`, and it names a template ROOT.**
  The packaged template tree takes its canon shape — per-scope moulds under
  `data/global/template/{box,workset,agent,agent_default}` plus the system handbook —
  replacing the flat `playbook/notebook/workbook` layout, and the box-home seed now lives
  at `global/template/box/home/`. Agent-level and workset-level template dirs restructure
  the same way (`<data>/agents/<agent>/template/box/home/`,
  `<workset>/template/box/home/`). Host stores materialise through **one** copier with
  one discipline: per-scope whitelists that deny by default, symlink and traversal
  refusal on every path component, containment checked before anything is created, and
  per-file create-if-absent wherever user content lives. `kanibako setup` self-heals the
  store *layout*; content you placed at the old flat paths needs a hand-move.
- **BREAKING: a symlink anywhere in a template directory now fails loudly.** The
  template/seed copier refuses, rather than follows, a symlink on either side of a copy —
  a symlinked source could otherwise reach outside its subtree. If your template dirs
  symlink config files into a dotfiles repo, `box create` / `kanibako setup` now fail
  naming the offending path; replace the symlink with a real file, or deliver the content
  through a `bindings.ro` / `bindings.rw` key.
- **BREAKING: system-scope config writes now go to the file the cascade reads.** At
  system scope, `set` and `reset` for the `secret_path` and mount-category families wrote
  `kanibako_config.yaml` while `get` and the launch read the system settings file — a
  `get` could not see what `set` wrote, and a category write survived `system reset
  --all` untouched. All three verbs, reset-all and the launch now agree on
  `<data>/global/settings.yaml`. A hand-placed system binding that exists only in the
  config file now gets a must-exist refusal; the cure is to move the sub-table.
- **BREAKING: a persona's values are resolved LIVE and never written to disk.** The persona-grata
  store used to be reparsed at every start, verified, and PERSISTED into
  `agents/<node>/settings.yaml`, which the launch then resolved. It is now read fresh on every
  launch and resolved directly, as a cascade level below the agent settings file. Nothing is
  written; a launch leaves that file byte-identical and `create` imports nothing. **A value the old
  sync wrote there still OUTRANKS the live store** — delete persona values you did not write
  yourself, or edits to the store will silently do nothing (see `MIGRATION.md` §2.15). A broken
  store config is now a hard error naming the cause instead of a silent fall back to stale values,
  and a token the endpoint rejects (401/403) refuses the launch. An unreachable endpoint only warns.
  ⚑ A `start` that merely **reattaches to an already-running box does not probe** — the box's agent
  is already running and authenticated, and a rejected verdict would otherwise refuse to reattach a
  user to a working box.
- **A persona's whole `env` block now reaches the box.** The reader took exactly three values
  (endpoint, model, token var) and discarded the rest of a persona `settings.json`'s `env`; every
  string-valued entry is now exported inside the container, minus `ANTHROPIC_BASE_URL` and
  `ANTHROPIC_AUTH_TOKEN`, which have their own channels. A non-string value is named rather than
  dropped in silence. Review those blocks before upgrading. Claude personas only.
- **A generated agent settings file no longer carries a model default** (was `model: opus` for
  claude, `gpt-5.5` for codex). A stored default outranks the defaults floor, pinning every seeded
  install to whatever was current when it was made. Not persona-only: `kanibako agent get <agent>
  model` on a fresh install now reports `(not set)` where it reported `opus` — resolution is unchanged, the
  file simply no longer restates what the floor supplies. Existing files are untouched. ⚑ One real
  change: a fresh CODEX-persona box whose store names no model now refuses at the pre-flight
  instead of silently running against `gpt-5.5`.
- **BREAKING (plugin authors): three persona surfaces on `Target` changed shape**, and one fails at
  IMPORT. `probe_verdict` is removed in favour of `probe_outcome`; it was public in 1.7.2 and the
  published 1.7.2 claude plugin imports it at module scope, so an old plugin wheel against the new
  base raises `ImportError` from any command that resolves an agent. `read_persona_settings` now
  returns a tri-state `PersonaReadOutcome`, `verify_persona` a four-way `PersonaProbeOutcome`.
  Upgrade the plugins with the base — see `MIGRATION.md` §3.
- **BREAKING: the legacy claude host-dir credential path is GONE.** A persona used to be
  able to resolve its endpoint, bearer token and model-map env by auto-adopting
  `~/.config/claude/<persona>/settings.json` + its sibling `token` file when nothing was
  configured for it in the keyspace. That was a second ingestion route for values the
  persona-grata store now carries for every harness, and it fired only while nothing was
  configured — so once a persona had adopted and persisted a host dir, later edits to
  that directory never reached it again.
  Nothing reads that directory any more; a persona with no configured endpoint is a hard
  error naming the `kanibako system set agent.<node>.endpoint=<url>` route. The plugin
  descriptor field that gated it, `persona.host_dir_adopt`, is removed — a `persona:`
  block that still declares it is ignored. Cure: set the endpoint and
  `secret_path.<TOKEN_VAR>` for the persona, or give it a persona-store entry.
- **The command line is its own cascade level.** Ephemeral launch flags are no longer
  post-resolve patches: a declared flag-to-key table spells them as level entries — `-M`
  as the active agent's `model` key (the only spelling under which a flag outranks a
  stored per-agent value), and `-N`/`-C`/`-R` as its `continue_mode` — with one guard
  ahead of the snapshot splice refusing undeclared keys, the `meta`/`config`/`pref` heads
  and the locator closure. No resolve whose output is written to disk sees a flag.
- **The instruction flattener moved and got louder.** It now ships at
  `kanibako/scripts/import-directives.py` (reached in-box through the existing package
  bind), strips HTML comments from the flattened artifact — comments are authoring
  guidance for whoever edits the source, and an example `@path` inside one used to
  resolve as a **live import** whenever the target happened to exist — and warns on any
  unresolved import (previously silent) and on a file ending inside an open HTML comment.
  Comments inside fenced code blocks survive.
- **Standalone box reads got truthful.** On a standalone box whose `box.*` values sit in
  the project-root file (every one created before v1.8.0), `kanibako box get box.<key>`
  now prints `(not set)` where it used to print the value — a plain `get` reports what is
  stored *at box scope* — and `box show --effective` drops the `(override)` marker on
  such values. The value still resolves and the launch still uses it. Also:
  previously-inert `workset.{boxes,vault_ro,vault_rw,logs}` entries in a standalone root
  file become **live**.
- **Reading effective config no longer creates vault directories** — the guarantee-create
  is gated to launch. Set-time validation likewise probes only values that actually reach
  the expander, so an `@` or `$` in the verbatim docker `env` family is data (an email
  address is not a dangling reference), and an unknown key is named as such before any
  probe judges its value.
- **BREAKING: flags that a RUNNING box cannot adopt are now refused by name instead of silently
  ignored.** Starting a box that is already up reattaches to it, and a container's creation-time
  inputs and its agent's argv are both fixed at launch — so `--rig`/`--image`, `-e`/`--env`,
  `--no-helpers`, `--no-auto-auth`, `--browser`, `--share-images`, `-N`, `-C`, `-R`, `-M`, `-A`,
  `-S`, and an explicitly typed `--persistent`/`--ephemeral` now produce an actionable error with a
  nonzero exit. ⚑ The one that bit hardest was silent: **`kanibako start -N <running box>`
  reattached you to the OLD conversation** rather than starting a new one, and `--image` was even
  recorded as the box's image before being ignored. The error names every offending flag and the
  cure (`kanibako --restart`). An explicit session-shape refusal leaves the running session
  completely untouched — nothing is signalled, killed, or attached. Exempt because they are
  genuinely honoured against a live box: `--attach`, `--detach`, `--print-container`, `--warm-only`,
  `--entrypoint`, and `-e`/`--env` whenever the invocation starts a second process in the box that
  will apply it (`--entrypoint`, or `kanibako shell --persistent` at a box that is running an
  agent) — env is refused only where nothing would consume it. ⚑ **`--detach` and `--warm-only` are
  not such a door:** nothing runs, so a `--entrypoint` or `-e` passed with either is now refused by
  name. It used to be accepted and dropped at rc 0 — the gate read the flags that pick the exec arm
  while `--detach` closed both of them further down. The refusal is now driven by *which* of the
  running-box regime's exits a launch will take, resolved once, so the gate and the regime cannot
  disagree. ⚑ **Check scripts that
  pass flags to `kanibako start` without knowing whether the box is up** (`MIGRATION.md` §2.17).
- **BREAKING: a launch no longer rebuilds a box whose directory has been deleted — it refuses.**
  If a box's registration survives but its box directory is gone, `kanibako start` used to
  silently re-create the directory and re-seed the home, reporting nothing. That is a *repair*,
  not a launch, and a repair has to be asked for by name. The launch now errors before touching
  the filesystem, names the box and the missing directory, and prints the one command that
  rebuilds it: `kanibako create <workspace>` for a default-mode box, `kanibako workset
  disconnect <workset> <box> && kanibako workset connect <workset> <workspace>` for a workset
  member. Unaffected: `create`, `box extract`, and the first launch of a box added with `workset
  connect` (connect registers the box without seeding it, so that launch is a genuine
  materialisation). ⚑ **Check anything that deletes box directories and relies on the next
  `start` to put them back** (`MIGRATION.md` §2.18).
- **BREAKING: a box-config verb run from a directory that is not a box now errors.** `kanibako
  box set box.<key>=<value>` (and `get`/`show`/`reset`) with no box named, run from a cwd with no
  box, used to write `boxes/__unregistered__/settings.yaml` and report success at rc 0 — a
  settings file for a box that does not exist, which nothing ever reads. It now refuses, naming
  the directory and the two ways forward: name the box (`kanibako box set <box>
  <key>=<value>`) or make one (`kanibako create`).
- **BREAKING (plugin authors): `Target.default_category_binds()` declares a bindings ARM keyed by
  destination, not one key per entry name.** A `bindings.{ro,rw}` default used to be
  `agent.<agent>.bindings.ro.<name>` → `(meta_ref, box_dest[, "ro"])`. It is now the *terminal*
  key `agent.<agent>.bindings.ro`, whose whole value is `{box_dest: (meta_ref[, "ro"])}` — the
  destination is the key and the entry name is gone. The four name-keyed bind categories
  (`common`, `caches`, `seeded`, `synced`) are **unchanged**. A plugin still returning the old
  dotted key is **refused by name** when the launch floor is assembled, not silently ignored;
  there is no shim and no deprecation window. ⚑ The declared return type widened to
  `CategoryBindDefaults`, and `dict` is invariant in its value type, so an override still
  annotated `dict[str, BindDefault]` fails **type checking** even if it declares no bindings at
  all — the three first-party plugins each needed the annotation moved. Plugins that build the
  table from their `<agent>-defaults.yaml` `category_binds:` section get the new shape for free,
  but a `key:` line under a `bindings` category is now refused rather than ignored. Destinations
  must be normalised (`normalize_bind_dest`): arm keys merge as strings but resolve to paths, so an
  unnormalised `~/x` neither matches nor is matched by an override written `/home/agent/x` — the
  two survive as separate entries and then collide at launch as two bindings on one destination.
  See `MIGRATION.md` §3 item 7.

### Fixed

- **`kanibako box info` named a cure that has not worked since v1.7.0.** For a directory with no
  box data it printed `Start a session with 'kanibako start', or create with:` — but the explicit
  create gate means a launch never materialises a box; it errors and points at `create`. The same
  branch also collapsed two different states, so a **registered** box whose directory had been
  deleted was told it *"has not been used with kanibako yet"* — false, and offering a cure that was
  correct only by accident for a primary box and **absent entirely** for a named one. `info` now
  defers that case to the launch refusal's own message, whose cure is already mode-dependent
  (`create` for a primary box, `workset disconnect` + `workset connect` for a named one), so `info`
  and a launch can never name different cures. The registered-box message now goes to stderr, where
  a refusal belongs; the exit code is unchanged (this branch has always exited 1). ⚑ **Standalone
  boxes are outside this fix's reach:** for them the box directory *is* the project root, so the
  refusal this defers to cannot trigger, and a standalone box in the broken state has no name for
  `info` to key on.

- **A broken standalone box was told to run a command that damaged the user's directory.** When a
  standalone box's `box_data/` is deleted the registry entry survives but the box resolves nameless,
  so a launch fell through to the generic "no box here" message — whose suggestion is built from the
  user's own spec. For a bare standalone name that produced `kanibako create <name>`, and running it
  created a directory *literally named* `<name>` in the current directory with a primary box inside.
  Two grammars, one token: `start` resolves a bare token as a name, `create` resolves it as a path.
  A launch at a broken standalone box now names the box, its root, the missing `box_data/`, and the
  actual two-step rebuild — `kanibako box rm <name> && kanibako create --standalone --name <name>
  <root>`. ⚑ `--name` preserves the box's kuid, channel address and every stored reference to it;
  the `rm` is safe here precisely because `box_data/` is already gone, so it drops the registry
  entry and touches nothing on disk.
- **`kanibako create` rebuilt part of a box and then refused, reporting that nothing had happened.**
  With the box directory present but its `home/` deleted, the resolver re-created and bootstrapped
  the home — and, for a standalone box, re-created the workspace directory — before `create` errored
  with `already initialized`. The message was false: a bare home with no seed, no canon skeleton, no
  agent config and no credentials had just been written. The refusal now happens before anything is
  materialised. Affects primary and standalone boxes alike.
- **The `orphaned project data` hint is gone.** It required a launch to be materialising a primary
  box, keyed on the box directory — which is exactly what the refusal added in this release (above)
  now rejects first, so the hint could no longer fire. ⚑ Not the v1.7.0 create gate: that one keys
  on *registration*, and a registered box whose directory was deleted still passed it. Orphan
  reporting remains on `kanibako box list`, which names `box remap` / `box rm`.

- **Reattaching to a running box no longer runs the whole launch preamble.** `kanibako start`
  against a box that is already up reattaches to it — but it used to first resolve the rig
  (**building or pulling an image**), settle the shadowing-flag persist, cache the image's login
  shell, check image freshness, spawn a throwaway container to probe the launch baseline, run the
  persona load-or-error pre-flight **including its network probe**, check box components, and write
  persona artifacts. None of it could affect the session being attached to. ⚑ The persona probe was
  the sharp edge: a **`REJECTED` verdict is a hard error**, so a token revoked since launch locked
  you out of a box whose agent was up and authenticated. A reattach now does only what it needs —
  refresh credentials, print the reconciled-config notice, attach — and the agent config is never
  rewritten under a live agent.
- **`--entrypoint` against a running box runs your command instead of being dropped.** It now execs
  as a second process in the box, per-run `-e` applied — the behaviour `kanibako shell <box> -- cmd`
  already had ephemerally. Previously `start` defaulted to persistent whenever tmux was present, so
  the invocation reattached and the entrypoint was silently discarded. Same fix covers
  `kanibako shell --persistent <box> -- cmd`.
- **`kanibako shell --persistent <box>` gives you a shell, not the agent's session.** Against a box
  that is running an **agent** it attached to that agent's tmux session instead, because the
  box-shell resolution ran only on the path that creates a container. The shell is now resolved on
  the reattach too and exec'd into the live box as a second process. ⚑ Its image tier is read from
  the **running container's own image** (the reference it was created from), not from the configured
  rig — on this path the rig was never resolved, so it can name a different image than the box is
  actually running. Where that image cannot be read, the image tier is dropped rather than guessed:
  `box.shell` → `$KANIBAKO_SHELL` → `sh`. **A persistent no-agent box is unchanged and still
  reattaches**: its session already *is* your shell, so you get back the one you left running
  rather than a new one. (A box launched before agent stamping keeps the old behaviour until its
  next restart.) Per-run `-e`/`--env` is applied to that shell, like any other second process in a
  live box; it stays refused at a no-agent box, which reattaches and would drop it.
- **A flag now works wherever you type it.** `kanibako box set <box> --null <key>` failed
  with `unrecognized arguments: <key>` — argparse groups positionals around the optionals
  between them, so a flag written *between* two positionals stranded everything after it
  and `--null` (and `--force`, `--box`, `--agent`) only worked before them. A pre-parse
  hoist now moves optionals (with their values, read from the parser's own action table)
  to the front on every subcommand whose positionals could be split; a `--` still ends
  flag parsing, and no previously-working invocation changed meaning across a
  thirty-thousand-case differential fuzz.
- **`kanibako agent set --null <key>` performed a silent read.** ⚑ Nothing to do on upgrade:
  `agent set` had no `--null` flag at all in 1.7.2, and this was already fixed by `v1.8.0-rc1`,
  so no released version ever behaved this way. The flag was advertised
  on the parser but never consulted, so the command fell through to its get path: it
  printed the current value and exited 0 without writing anything. It is now an explicit
  refusal naming both cures — `agent reset <agent> <key>` to clear the agent's own value,
  or `--null pref.agent.<agent>.<key>` from a box or workset to suppress what the agent
  declares. (Suppression at agent scope is not supported: the per-agent file is read back
  with every value coerced to a string, so a null there would return as the text `None`.)
- **`--null` help and messages now teach suppression.** The flag's help says that it
  SUPPRESSES an inherited value and that the sibling `reset` verb undoes it, with a
  per-verb example; messages that pointed at a non-existent `--reset` *flag* now name the
  `reset` verb, and suppressed-value messages point at `reset` at the scope that set the
  pref.
- **Persona boxes get their agent's shared directories.** A persona (`navigator+claude`)
  mounted NEITHER `~/.claude/plugins` NOR `~/.claude/cache`: the plugin declares those
  against its harness name while the resolver read them under the persona's node name, so
  nothing matched and the symlink shim pointing `agents/<persona>/common/…` at
  `agents/<harness>/common/…` had no consumer. They are now emitted under the active
  node. Bare (non-persona) agents are unaffected.
- **A no-agent box no longer launches claude.** A suppressed selection produced an empty
  node that `resolve_target` read as *no name given, please auto-detect* — so the
  no-agent box came up running claude, credentials and all. The selection now carries an
  explicit `has_agent`, honoured at every seam that turns a selection into a target
  (launch, bootstrap, reauth).
- **Per-agent credential paths no longer collapse into the workset auth root.** Three
  call sites quietly defaulted the per-agent credential path to none, which collapsed the
  per-agent credential directory into the workset auth root on exactly the commonest host
  shape; the path is now threaded as a required argument.
- **A standalone box could set a value and then not find it.** The pair of paths naming a
  box's settings file and its workset tier was one expression hand-spelled at seventeen
  sites, so `set` wrote the project root while half the readers looked elsewhere.
  The derivation is now one function — and the lifecycle verbs that each held their own
  copy each lost data for it: `convert` and `move` out of standalone copied the project
  root as if it were box metadata (a box setting landed in a file the destination never
  reads), and `duplicate` of a legacy standalone read only the new box tier and dropped
  values still stored at the root. Both now carry the box subtree through the one
  derivation.
- **`box extract` restored into a placeholder directory.** It used the
  `__unregistered__` placeholder as a literal destination; it now restores into the
  box's real metadata directory and registers it, with a true pre-flight name check
  (extract deletes its destination before copying, so a late collision was
  destroy-then-fail) that permits restoring a box into its own workspace.
- **A launch that exited before attach left the box up.** The exited-before-attach branch
  now tears the box down — a pre-existing gap this release's widened pre-attach window
  made deterministic.
- **A slow podman no longer aborts the whole test run.** Both module-scope podman probes
  in the e2e conftest documented a "not available" return on failure, but a subprocess
  *timeout* escaped instead — and since that module is collected by the plain unit run
  too, a merely slow podman took the entire job down before a single unit test ran.

### Removed

- **BREAKING: the `set` / `reset` route for bind entries** —
  `{system,workset,box}.bindings.{ro,rw}.<name>` and `agent.<node>.bindings.{ro,rw}.<key>`
  are both refused from the CLI. `kanibako box set box.bindings.rw.home=/newhome` and
  `kanibako system set agent.claude.bindings.ro.launcher=/newsrc` used to succeed; they now
  print a refusal naming the key and the settings file to edit. ⚑ **The keys themselves are
  NOT removed** — they are still declared, still read at launch, still written by hand in the
  settings YAML, and **`get` still reads them**, so a binding you set is never reported
  as `(not set)`. There is no replacement CLI spelling: a bindings arm is becoming a single
  key whose value is a map keyed by mount *destination*, and the destinations inside that map
  are values rather than keys, so there is no per-entry key left for `set` to name. The other
  mount categories (`caches`, `seeded`, `common`, `synced`) are unaffected and still settable
  at every scope.
- **BREAKING: `box.agent_name` and `system.default_agent`** — replaced by
  `pref.system.agent` and `system.agent`; both are refused by name at launch (above).
- **BREAKING: the `shared` mount category** — renamed `common`, no alias. A leftover
  `shared` entry is not a key, so it stops the resolve rather than quietly dropping the
  bind it declared.
- **BREAKING: `system.base_template`** — replaced by `system.template`, which names a
  template *root* (above).
- **BREAKING: the settable `box.agent.*` mirror** — replaced by
  `pref.agent.<agent>.<key>` for writes and read-only `meta.box.agent.*` for reads.
- **`meta.runtime.ws_settings`** — cut from the keyspace; `meta.workset.settings` now
  spells `@meta.workset.path/settings.yaml` directly, one hop instead of two. Replace the
  reference in any settings file of your own (the resolved value is identical).
- **`meta.box.helper_log`** — never a declared key, only a way around the reference parse
  limit that braced references now remove. The helper-log bind spells its source
  `@workset.logs/@{meta.box.name}.jsonl`. One consequence: the old key was a whole-value
  reference, so an absent referent *dropped* the bind; the spelled form is embedded, so
  an absent or null `workset.logs` yields a degenerate path — and a read-only mount whose
  source is missing is dropped with a warning naming it, which is more visible than the
  silent drop it replaces.
- **The per-file rom keys (`rom_<slug>_<hash>`)** — replaced by the canon binds. A rom
  root with leftover files but no `canon/COLLECTION.md` now refuses to launch
  (fail-closed) where it previously emitted per-file binds. The dead rom `clear.py` /
  `start.py` wiring is deleted with them.
- **`<data>/agents/<agent>/share/`** — the join root died with assembly-time rooting. It
  was verified empty on inspection; if yours has content it belongs to a hand-set
  relative agent binding, which needs absolutising rather than deleting.

## [1.7.2] - 2026-07-16

This release completes **Codex support inside VS Code and multi-shell boxes**:
mounted secrets now reach every in-box shell, and the in-box Codex configuration
is kept in sync with the active agent on every launch.

### Fixed

- **Secrets reach every in-box shell.** A configured `secret_path.<VAR>` (e.g. an
  API key) is now exported into every login shell in the box — the VS Code
  integrated terminal and the Codex panel's app-server, not just the supervised
  agent process — via an `/etc/profile.d` drop-in. Previously only the agent's own
  process saw the variable, so Codex launched from a panel or terminal failed to
  authenticate even though the secret was correctly mounted.

### Changed

- **The in-box Codex config is now a reconciled projection.** `~/.codex/config.toml`
  is reconciled to the resolved active agent on every launch: a persona writes its
  model provider; a bare (non-persona) Codex box wipes the managed
  `model`/`model_provider` selection back to stock. Your own unrelated config
  (extra `[model_providers.*]` tables, other keys) is preserved untouched.
  Previously a box switched from a persona back to bare Codex kept silently running
  the old persona's provider. Reattaching an already-running box now prints a note
  that Codex config changes take effect after a restart. Requires
  `kanibako-agent-codex >= 0.2.4`.
- **Codex `sandbox_mode` is a box invariant** (`danger-full-access`) — the box
  *is* the sandbox, and this stops the Codex panel's app-server from stalling on a
  nested sandbox. `bubblewrap` is now part of the universal image baseline.

VS Code integration and persona agents remain **experimental**; the in-box
graphical Codex panel is a known upstream limitation.

## [1.7.1] - 2026-07-13

This release completes **codex and goose persona support** — a persona pointed at
a third-party endpoint now resolves its provider on those harnesses instead of
coming up bare (the persona feature shipped experimentally in 1.7.0 was most
complete for claude only) — and hardens the box lifecycle and the foreground
box-exit terminal handling.

### Added

- **Codex persona support.** A codex persona now resolves its model provider:
  harness-aware persona resolution writes a `[model_providers.<node>]` block into
  the codex `config.toml` at launch, pointing codex at a third-party
  (self-hosted / OpenAI-compatible) endpoint using the OpenAI **responses** wire
  API. Previously a codex persona came up bare, with no provider wired. Requires
  the `kanibako-agent-codex` plugin ≥ 0.2.2.
- **Goose persona support.** A goose persona is now wired to its endpoint through
  Goose's built-in `openai` provider.
- **`kanibako register` (also `kanibako box register`).** A new verb to readopt a
  deregistered box, or register a standalone box that exists on disk but was never
  indexed. Registration is index-only — it never re-seeds or touches the box home
  — and refuses to clobber an active box that already owns the name or workspace.

### Changed

- **Box lifecycle hardening.** `kanibako rm` now deregisters the box (and can
  purge it by name); `kanibako create` refuses to run over a deregistered or
  orphaned box home instead of silently colliding with it; and deregistered boxes
  are surfaced in listings so they no longer silently vanish. Together with the
  new `register` verb (above) this closes the `rm → deregister → register`
  recovery loop.
- **Foreground box-exit terminal handling.** On a foreground box exit the host
  terminal is now restored — kanibako leaves the alternate screen, resets SGR
  attributes, and shows the cursor — so a TUI agent that died without cleaning up
  after itself no longer wedges your terminal. On a **clean** exit at an
  interactive tty the raw captured pane is suppressed (you already saw the agent's
  output live), but a **crashing** agent's captured logs are still surfaced so you
  can see why it died. Piped output / podman logs / CI (no tty) still receive the
  captured logs verbatim.
- **Instruction directives resolve their imports to full depth.** The launch-time
  directive flattener no longer caps how deep it follows imports; an import it
  cannot resolve now degrades to inert text rather than remaining a live directive.

### Fixed

- **`kanibako code --remote` error attribution.** Errors relayed from the remote
  host are now attributed to the remote host, rather than surfacing as if they
  were local failures.

### Removed

- **BREAKING: the `resource.*` config surface is removed.** The spec-dropped
  `resource.*` settable keys, their override helpers, and the resource-only
  `--local` set flag are gone; `set resource.*` / `--local` now error as an
  unknown key / flag. This surface only ever round-tripped through its own
  get/set/reset and was never read at launch, so no launch behavior changes — but
  any configuration that named `resource.*` keys no longer has a CLI surface (a
  pre-existing inert `resource_overrides` table is filtered out of `system show`).

## [1.7.0] - 2026-07-12

This release lands the **persona agents** feature (experimental) — running a named
identity/mind on top of an agent binary, optionally pointed at a third-party
endpoint — alongside a **credential-sharing rework** (boolean `group_auth` becomes
a three-tier global/workset/box sharing model) and a set of config-key renames and
clean breaks. It also completes a large behind-the-scenes settings/keyspace overhaul
(KeyStore storage, cascade assembly, and resolution/readiness) that is
equivalence-preserving for launches. Alongside these it adds **VS Code integration**
(`kanibako code`, experimental), **always-on boxes** with an in-box session
supervisor (background/`--detach` launches and reattach), and **private boxes**
(`kanibako create --private`) that receive no host credentials.

### Added

- **Persona agents (experimental, opt-in).** `--agent` now accepts a
  `persona+harness` grammar (e.g. `--agent navigator+claude`): a **persona** — a
  named identity/mind with its own agent store and `agent.<…>.*` keyspace slot —
  runs on a **harness** (the agent binary, e.g. `claude`). The harness is
  validated against installed agents; a persona reattaches and stops as a distinct
  node. A bare `--agent claude` is unchanged and **byte-for-byte identical** to
  before, so existing boxes are unaffected. Two new per-agent keys back the
  feature:
  - **`agent.<agent>.endpoint`** — an alternate harness base-URL (a sibling of
    `model`), delivered to the harness as its base-URL env var (for Claude,
    `ANTHROPIC_BASE_URL`). When set, kanibako **does not sync the host Anthropic
    OAuth credential** to that box (a fail-safe credential fork), so your Anthropic
    token is never sent to a third-party endpoint.
  - **`<scope>.secret_path.<VAR> = <host-path>`** (all four scopes; the persona
    bearer token uses `agent.<agent>.secret_path.ANTHROPIC_AUTH_TOKEN`) — the
    first-class **SECRET category**: delivers a host-file secret (e.g. a bearer
    token → `ANTHROPIC_AUTH_TOKEN`) into the box **arm's-length** — the host file is
    read-only bind-mounted to a fixed in-box location and exported in-box by a shim
    at agent start, so kanibako **never reads the secret value** (never into process
    memory, never onto the podman argv, never in the keystore / launch snapshot /
    any box file / logs — only the path pointer is stored). Resolves through the
    `system → workset → box → agent` cascade like any category. A
    missing/unreadable/empty file warns and leaves the var unset (fail-soft).
    *(Renamed from the rc0-rc2 `agent.<agent>.env_file.<VAR>`, which read the value
    into the container env — clean break, no alias.)*

  A persona shares the bare harness's plugins/cache (via a symlink shim into the
  harness store) rather than starting empty.

  **⚠️ Experimental — harness coverage.** Personas are **experimental** in this
  release, and coverage is uneven across harnesses. The path is most complete for
  the **claude** harness (the endpoint fork + secret-token delivery above);
  persona support for **goose** and **codex** is **not yet complete** — treat
  personas on those harnesses as early/incomplete and expect gaps. Bare
  `--agent claude` / `--agent goose` / `--agent codex` (no persona) is unaffected
  and stable.

  **⚠️ Known limitation.** The OAuth credential fork *skips syncing* but does
  **not scrub** a credential already seeded into a box. Converting an **existing**
  bare box into a persona (creating it bare, then `start --agent persona+harness`)
  can leave the real Anthropic token in a box pointed at a custom endpoint. **Only
  freshly created persona boxes are safe** — create the box as a persona from the
  start.

- **Streaming native pull progress.** The launch and `setup` image-pull paths now
  stream podman's layer-by-layer progress (matching `rig prep`), so a slow first
  pull no longer looks like a hang.
- **VS Code integration — `kanibako code`** *(experimental)*. Attach a local
  VS Code window to a box's running container (via the Dev Containers "Attach to
  Running Container" flow), with your chosen agent available in the integrated
  panel and the workspace opened at the project. `kanibako code --remote
  <ssh-host>` attaches a *local* VS Code to a box on a remote machine by
  tunneling the remote container socket over a single owned SSH connection.
  `kanibako system diagnose` gained VS Code host-prerequisite checks.
  **Experimental in this release:** kanibako does not yet enforce a single
  active agent per box — running the VS Code panel and a CLI agent on the same
  box at once silently forks the session; use one surface at a time (see
  README → VS Code Integration).
- **Always-on boxes and session persistence.** A box's agent now runs under a
  small in-box supervisor as PID-1, so the box's lifetime is decoupled from any
  one agent session. `kanibako start --detach` (alias `--background`) launches a
  box in the background and self-heals a crashed agent (resuming with
  `--continue` and a handoff marker); a foreground `kanibako start` still runs
  interactively and now exits with the agent's *true* exit code; reattach with
  `kanibako start` (a `tmux attach` under the hood). `kanibako start --warm-only`
  brings a box up with no CLI agent (for a VS Code panel to drive).
- **Private boxes — `kanibako create --private`.** Create a box that does *not*
  receive your host agent credentials, for self-contained or throwaway auth.
- **Layered instruction delivery.** A shipped `KANIBAKO.md` operating guide is
  delivered into each agent's instruction slot (Claude `~/.claude/CLAUDE.md`,
  Codex, Goose) through a flatten processor, so an agent gets consistent guidance
  about the box environment it is running in. `system.instructions` locates it.
- **Credential writeback on detach.** Detaching from a shared box writes any
  refreshed agent credentials back to the host store (via a trusted host-side
  watcher), so a token rotation inside one box no longer locks out the others.
- **Agent editor extensions on attach.** Attaching VS Code to a box auto-installs
  the Codex (`openai.chatgpt`) and Goose (`block.vscode-goose`) extensions, and
  Goose's `GOOSE_MODE` permission is mirrored into the box for panel parity.

### Changed

- **BREAKING (auth): boolean `group_auth` is replaced by a three-tier
  credential-sharing model.** Credential sharing now composes across **global**
  (host home), **workset** (per-workset store), and **box** (private) tiers — a box
  can be global- and/or workset-shared, with **workset taking precedence over
  global**. A `workset.auth.global_sync` mirror pushes a workset's auth up to
  global. Sharing is capability-gated per agent (only agents that support it
  participate). The old `group_auth` keys are gone (clean break, pre-release); a
  private box is now `box.auth.global_enabled=false` /
  `box.auth.workset_enabled=false`. (See Removed for the retired `--distinct-auth`
  flag.)
- **BREAKING (config): `<scope>.meta` keys move to a top-level `meta.<scope>`
  namespace.** The protected, read-only identity keys are now `meta.box.*`,
  `meta.workset.*`, and `meta.agent.<agent>.*` (e.g. `workset.meta.root` →
  `meta.workset.path`).
- **BREAKING (config): `box.agent` config key renamed to `box.agent_name`.** The
  dotted key, the on-disk `[box]` leaf (`agent` → `agent_name`), and the
  corresponding field are all renamed. No back-compat shim (pre-release).
- **BREAKING (config file): `kanibako.yaml` → `kanibako_config.yaml`.** The general
  config file is now named `kanibako_config.yaml` (clean break, no auto-detection
  of the old name).
- **BREAKING (CLI): the overloaded `config` subcommand is retired** at every scope
  (`box`, `workset`, `system`, `agent`) in favor of four discrete verbs —
  `set` / `get` / `show` / `reset`. There is no `config` alias (clean break,
  pre-release). The old positional/flag mode-switching maps as follows:
  - `<scope> config` → `<scope> show`
  - `<scope> config --effective` → `<scope> show --effective`
  - `<scope> config <key>` → `<scope> get <key>`
  - `<scope> config <key>=<value>` → `<scope> set <key>=<value>`
  - `<scope> config --reset <key>` → `<scope> reset <key>`
  - `<scope> config --reset --all` → `<scope> reset --all`

  `set` keeps `--force` and `--local` (resource keys); `reset` keeps `--all` and
  `--force`; `show` keeps `--effective`; `get` is bare. Each scope's positional
  (box `[project]`, workset/agent `<name>`) is unchanged, as are the
  config.*-forbid guard, the cross-scope write-direction guard, and the set-time
  cascade validation — only the parser/dispatch surface changed.
- **BREAKING (config): `config.*` keys can no longer be set/reset via the CLI.**
  The keys that *locate* kanibako's storage (`config.data`, `config.settings`,
  `config.agents`, `config.primary_workset`, `config.registry`) are read-to-locate
  only; the CLI refuses to write them and points you at the config file. `setup`
  and programmatic writers still write them.
- **`setup` compatibility gate reworked into a 5-band BCV/FCV check.** The single
  `OLDEST_COMPATIBLE_SETUP_VERSION` nudge is replaced by two build constants
  (backward- and forward-compatible versions) and a five-band gate, so routine
  releases stay silent, additive releases nudge (non-blocking), and hard breaks
  error cleanly rather than silently under-configuring.
- **Under the hood: settings/keyspace overhaul (launch behavior preserved).**
  Config and settings are now backed by a unified **KeyStore** with a
  behavior-preserving cascade (base < system < `agent.default` < `agent.<active>` <
  workset < box), `@`-reference resolution with cycle detection, typed access, and
  set-time validation. A resolution/readiness layer routes home/vault/channel binds
  and identity anchors through the keyspace, adds a cross-scope write-direction
  guard and `box.agent.*` overrides, and seeds box settings at create time keyed off
  registry membership. A write-ahead **lifecycle journal** makes an interrupted
  `create` recoverable (replay-idempotent). Users do not configure these internals
  directly, and normal launches are unchanged.
- **Launch warnings print once, after the session.** The baseline-tools and
  bind-shadow warnings no longer print a (wiped) pre-launch copy; each now surfaces
  exactly once in the post-session reprint and covers reattach.
- **BREAKING: launching no longer auto-creates a box — run `kanibako create`
  first.** Creating a box is now a deliberate act. `kanibako` (shortcut for
  `start`), `kanibako start`, `kanibako code`, and `kanibako shell` no longer
  materialize a new box for a path/name that has none — they error with
  `no box at <path>. To create a new box, run 'kanibako create'` and exit
  non-zero. This closes a day-1 footgun where a typo'd project or the wrong
  working directory would silently spawn a brand-new box. Auto-*starting* an
  existing (stopped) box is unchanged. `kanibako create` gained a start hint in
  its success message, and forward-recovery of an interrupted create now belongs
  to `create` alone (re-running it completes the create); a launch treats a
  not-yet-registered box as "no box" rather than resurrecting it. For
  `kanibako code --remote`, the box must exist on the *remote* host — the remote
  "no box" error is surfaced with a hint to run `create` there.
- **A higher scope can now set defaults for the scopes it contains.** The
  config-verb scope guard follows containment (`system ⊃ agent ⊃ workset ⊃
  box`): `kanibako workset set box.image=X` stores `box.image` in the
  *workset's* settings file as an overridable default for that workset's
  boxes; a box-level set overrides it, and a box reset falls back to it.
  Writing *upward* (a box setting a `workset.*`/`system.*` key) remains
  refused. This restores the originally-ruled cascade model; the previous
  own-namespace-only refusal was a spec drift.
- **The directional rule is now enforced at the resolver, not only the CLI.**
  A hand-edited settings file contributes only keys of its own scope and of
  scopes it contains; a containing scope's table found in a lower file (e.g.
  `system:` in a box file) is dropped at assembly with a warning naming the
  file and key. A user file's top-level `meta:` table is likewise dropped —
  the identity anchors are bootstrap-materialized and read-only everywhere.
- **`get` and `reset` now tell the truth.** Plain `get <key>` returns the
  value stored at that command scope's file (including downward defaults it
  stores) or "(not set)" — it no longer fabricates a built-in default or
  leaks another tier's value; `--effective` remains the resolved cascade.
  `reset` says what it did ("Cleared <key> set on the box scope; …") instead
  of claiming a "default" that wasn't what launch resolves, and appends the
  now-effective value and its source tier when the cascade can truthfully
  supply them. `reset --all` clears nested scope tables per the same
  containment rules and reports the real removal count.
- **System settings keys are settable from the CLI.** The `system.*`
  catch-all refused every system key as "structural," pointing at a file the
  resolver never reads for them. The genuinely structural path-tier family
  keeps the file-only refusal (now naming the right file); real settings keys
  — the auth sharing gate, `default_agent`, `env.*` — route to the system
  settings file that launch actually reads, with set/get/show/launch agreeing.
- **`box.agent.*` carries categories.** A box can now supply or suppress its
  active agent's category entries (seeds, binds, caches, masks) through the
  §2b mirror as ordinary box-scope writes — including `null` suppression —
  resolved through the one cascade merge rather than a post-expansion overlay.
- **Bundled templates are refreshed through `kanibako setup`.** Curated
  template content (the base tree, each agent's `template/`, and the shipped
  `KANIBAKO.md`) was only ever installed on a host's first run, so a host set
  up under an older build never received later-shipped or updated template
  files. When the packaged templates differ from what a host last installed,
  the agent commands (`start`, `box start`, `agent reauth`) now stop with
  "run `kanibako setup`" until setup is run. `kanibako setup` shows the files
  it would add or replace, asks before applying, and refreshes them — your own
  (non-shipped) template files are never touched, but edits to a *shipped* file
  are replaced. Declining is remembered as an informed choice and clears the
  block; `kanibako setup --refresh-templates` applies the update non-
  interactively for headless hosts. A first run installs the templates and
  records their stamp silently, as before.

### Fixed

- **Host users other than uid 1000 no longer lose ownership of their project
  tree.** Plain `--userns=keep-id` mapped the calling user beside — not onto —
  the image's `agent` user, and the `:U` bind option then recursively chowned
  the box home *and the user's project directory* to an unrelated subuid,
  breaking every subsequent kanibako command. Boxes now run with
  `--userns=keep-id:uid=1000,gid=1000`, pinning the caller onto the container
  user regardless of host uid (podman ≥ 4.3; uid-1000 hosts are unchanged).
- **`$XDG_CACHE_HOME`-style variables always resolve.** Stored values naming
  an XDG variable crashed resolution ("Variable $XDG_CACHE_HOME is not set in
  this context") on hosts that don't export it — and exporting it didn't
  help, because several resolution contexts were built with partial variable
  maps. One canonical builder now supplies every context, applying the XDG
  Base Directory spec defaults when a variable is unset, empty, or relative.
- **Goose no longer flashes a raw podman error on a box's first launch.** The
  default continue mode attempted a doomed `session --resume` on a fresh box;
  the fast-dying container raced the attach into a raw "container state
  improper" error before the retry recovered. Targets now report whether a
  resumable session exists, and the first launch goes straight to a new
  session.
- **Category-key `set` finds its bind anywhere in the cascade.**
  Repointing a bind's host source (e.g. `box set box.bindings.rw.vault=~/x`)
  required the tuple to exist in the command scope's own file; it now
  resolves against the effective cascade, preserving the guest destination —
  and category-key `reset`, which was rejected outright as an unknown key,
  now removes the command-scope tuple so the cascade value resurfaces.
- **A lone-setter category `null` behaves as suppression everywhere.** A
  present-`null` category leaf that only one level set could crash the
  collector or, for `masks`, be emitted as a real mask entry instead of an
  unmask; the merge now applies the type-split to lone subtrees too.
- **`workset set default <key>` was a silent dead write — the primary workset's
  settings now live at `@config.primary_workset/settings.yaml` (spec §2c).**
  Since 1.6.0 the CLI wrote the default (primary) workset's values to
  `<data>/config.yaml` while the launch cascade read `<data>/settings.yaml`, so
  nothing set via `workset set default` (or `workset share add default`) ever
  took effect. Both the write path and every reader now converge on the one
  spec location, `<data>/primary_workset/settings.yaml`. **No automatic
  migration (clean break, covered by the 1.7.0 setup nudge):** values in the
  legacy `<data>/config.yaml` have been inert since 1.6.0 and stay ignored; a
  hand-made `<data>/settings.yaml` is no longer read (kanibako warns at
  resolve time while it exists without the spec file) — move wanted values
  into `primary_workset/settings.yaml` by hand or re-set them via
  `kanibako workset set default <key>=<value>`.
- **`kanibako workset set <workset> env.<VAR>=<value>` now works (named and primary worksets).**
  The workset handler never threaded its env-file destination into the config
  engine, so every workset-scope `env.*` set/reset failed with "no env file
  path". The workset env tier lives at `<workset root>/env` (primary:
  `<data>/primary_workset/env`), mirroring the box tier's `<box>/env`, and a
  primary-workset box's launch env now includes the primary workset's env tier
  (precedence unchanged: system < agent < workset < box).

### Removed

- **`--distinct-auth` flag removed.** With the three-tier sharing model, a private
  box is expressed as `box.auth.global_enabled=false` /
  `box.auth.workset_enabled=false` rather than a flag.
- **The `required` cascade tier and the `workset_roots` registry section are
  removed.** The settings cascade now ends at `box` (no mandatory, non-overridable
  ceiling tier). The `workset_roots` section collapses onto the `worksets` section
  (identical name→root data); no on-disk migration is needed — a stale
  `workset_roots` section is simply no longer surfaced.

## [1.6.0] - 2026-06-26

This release generalizes kanibako's agent-plugin interface so that any agent is
described by one declarative contract, and ships first-class **Goose** and
**Codex** agents alongside Claude. The `kanibako` meta-package now installs all
three by default. It also lands a large **config / settings revamp** (one breaking
change set) that splits config from settings, renames `crab` → `agent`, restructures
the `system.*` namespace, unifies worksets, rebuilds the comm system as channels,
and reworks templates.

### Added

- **Bind-shadow warning** — when a box launches and a mount destination already
  holds content in the box's home, that content is silently *shadowed* by the
  bind (it stays on disk under the outer home bind but is hidden inside the box).
  Kanibako now detects this before launch and warns, naming each shadowed
  destination, so the hidden files don't go unnoticed. The check covers every
  bind (directories and files), excludes the home/workspace base mounts and
  intentional masks, and is best-effort (never blocks a launch). The warning is
  reprinted after the session closes (the tmux alt-screen wipes pre-launch
  output), matching the baseline-tools warning behavior.
- **`kanibako rig update [<name>]`** — the everyday "get the latest" path for a
  rig. For a pulled/prefab rig it pulls the newer upstream image; for a
  template/built rig it rebuilds on the refreshed base. With no name it targets
  the configured `box.image` rig; `--all` updates every local rig. `rig prep
  --force` is kept as the full rebuild-from-scratch path.
- Friendly preflight error when rootless podman's storage (graph root) is on a
  **virtiofs** filesystem — an unsupported configuration where the box can't
  launch (overlay/`pivot_root` is denied). Instead of a cryptic runtime crash,
  kanibako now explains the problem and suggests fixes (back the graph root with
  a real filesystem, or use a rootful `KANIBAKO_DOCKER_CMD` shim). The check is
  silent under a rootful shim, on docker, or whenever the state can't be
  determined, so it never blocks a normal launch.

### Changed

- A persistent box now tears down (credential writeback + container removal) when
  its in-box session exits; a `Ctrl-b d` detach (or a dropped client) keeps it
  running and reattachable with `kanibako start`. Previously a clean exit left a
  stopped container behind, which blocked the next `kanibako shell`/`start`.
- Image-freshness notices now suggest `kanibako rig update` (was `kanibako rig
  prep --force`).

### Removed

- Dropped the vestigial default `~/workspace/vault` tmpfs mask. It only existed
  to hide the vault back when it lived inside the workspace; the vault moved out
  of the workspace in 1.6.0, so no mask is applied by default. Boxes can still
  declare explicit tmpfs masks via the `box.masks` (or `<scope>.masks`) category.

### Changed (BREAKING — config / settings revamp)

The revamp is **one breaking change set** with **no automatic migration** — see
[`MIGRATION.md`](MIGRATION.md) for the full step-by-step runbook. Summary:

- **Config vs settings split.** Layout (`system.*`, *where things live*) and
  behavior (`agent.*`/`box.*`/`workset.*` + the category keys) now live in separate
  file sets. A 5-tier settings cascade applies (`settings_base < system <
  agent.<agent> < workset < box`); `box` wins.
  The scoped shares/seeds/env collapse into one **category** primitive
  (`masks`/`bindings.ro`/`bindings.rw`/`caches`/`seeded`/`shared`/`synced`/`env`).
  ⚑ Dropping a project `.env` file no longer works — move vars to `<scope>.env.<VAR>`.
- **`crab` → `agent` rename.** The tool, its config keys, directories, and the
  config-facing commands are now `agent`; the `crab` CLI command is **cut** (its
  verbs split between `agent` and `box`). The `$CRAB` reference var is now `$AGENT`,
  and the per-agent YAML section token `crab:` is now `agent:`.
- **`system.*` namespace restructured.** `system.path.*` → `system.*` (the `.path`
  infix dropped); new `system.global`/`backup`/`settings`/`primary_workset`/
  `cache`/`runtime`; the default-agent setting `system.agent` → `system.default_agent`;
  XDG base-directory resolution honored on both host and box side.
- **Worksets + modes.** The two-axis `ProjectMode × ProjectLayout` model becomes a
  single three-mode model (`box.mode` = primary | named | standalone); **layouts are
  removed** (no more human-vault symlinks; vault always at `@workset.vault_{ro,rw}`).
  The PRIMARY workset is now a real directory; standalone state moves into `box_data/`
  and standalone boxes are registered. Trees are **drop-in importable** — detection is
  an on-disk ancestor-walk; an unregistered on-disk tree is auto-imported (with an
  alert), and a name collision is refused.
- **Registry consolidation.** `names.yaml` + `worksets.yaml` + `connected.yaml`
  merge into one `registry.yaml`, now a derived/rebuildable index (losing it no
  longer orphans boxes).
- **Comm system → channels.** The single `~/comms/` mount is replaced by the
  `~/channels/` tree (5 channel types across system + workset scopes). Mailboxes are
  partitioned by workset name (`mailboxes/<ws>/<box>`), and the broadcast log moves
  and changes format (`broadcast.log` → `chat/broadcast.md`). The box-side helper
  socket/log dest is now XDG-aware (`helper-messages.jsonl` → `helpers.jsonl`).
- **Templates + host-config import removal.** The shell-variant template tree, the
  CLAUDE.md instruction merge, and per-agent **host-config import** collapse into one
  **layered seed-once** model (base → agent → workset, last-wins, copied once).
  ⚑ **Your host agent config no longer flows into boxes** — claude `.claude.json`/
  `oauthAccount`, codex `config.toml`, and goose `extensions`/`instructions` are no
  longer imported; boxes use curated templates plus synced credentials (credential
  sync is unchanged). Set goose provider/model via `agent.goose.env.GOOSE_*` settings.
- **Box-side vault dest moved.** Inside a box the vault is now at `~/vault/ro` /
  `~/vault/rw` (was `~/share-ro` / `~/share-rw`).
- **Per-box meta file renamed.** `project.yaml` → `settings.yaml` (all modes).
  A NAMED workset's `workset.yaml` + `config.yaml` likewise consolidate into one
  `<root>/settings.yaml`; the per-agent `agents/<agent>.yaml` sibling file moves
  inside the store dir as `agents/<agent>/settings.yaml`.
- **Agent overrides keyed by agent name.** Agent-specific overrides are now read
  under `agent.<agent>` (e.g. `agent.claude`, `agent.goose`), layered over a
  reserved any-agent tier `agent.default`; the agent-specific value wins. This
  fixes "bleed" where an override (e.g. `model`) set while a box ran one agent
  kept applying after the box switched agents. The agent-agnostic
  `kanibako system config <key> <value>` writes under `agent.default`.

### Removed

- **Claude `-R` / `--resume` conversation picker.** Claude's launch modes are now
  `start` and `continue` only; `-R` / `--resume` now resolves to `--continue`.
  The resume picker remains reachable from within an interactive Claude session.
- **Dead config keys deleted (pre-public clean-house).** `shared.*`, `paths.shell`, `paths.vault`,
  `layout`, and `persistence` are removed (subsumed by newer keys: `shared.*`
  caches → `<scope>.caches.*`; paths folded into `system.*`; layouts replaced by
  `box.mode`). Setting any of them is now rejected as unknown.
- **`system.*` config keys are now file-only.** The CLI reads and shows them but
  **refuses to set/reset** them (`kanibako system config system.<key> <value>`
  errors with a pointer to the config file); `setup` and programmatic writers
  still write them. This now includes `system.default_agent` — choose it with
  `kanibako setup` or by editing the file. `box.*` and other non-`system.` behavior
  settings stay CLI-settable.
- **Arbitrary agent auto-pick removed.** `start` no longer silently launches the
  first installed agent when none is chosen and 2+ are installed — it errors instead
  (see Changed). The single-agent implicit case is unchanged.
- **`refresh -p`/`--project` removed (clean break).** No deprecation alias — use the
  blanket `--box <name-or-path>` selector instead.
- **Legacy plugin hooks removed (descriptor-only).** The legacy `Target` launch
  hooks (`build_cli_args` / `binary_mounts` / `init_home` / `generate_crab_config`)
  and the core legacy assembly branch are gone — every plugin is now driven by its
  declarative `PluginDescriptor`. The `ResourceMapping` / `ResourceScope` /
  `resource_mappings()` resource-sharing abstraction is **deleted**; agent
  resources are expressed via the `agent.<agent>.shared` / `caches` / `seeded`
  categories instead.
- **The shared store and `shared/` data dir are gone.** Per-agent plugins and
  caches now live under the per-agent store at `agents/<agent>/{plugins,cache}`
  (bound rw to `~/.claude/{plugins,cache}` in the box) as `agent.<agent>.shared.*`
  entries, instead of the old `shared/<agent-id>/plugins` store. The top-level
  `<data>/shared/` directory no longer exists; hand-move existing plugin dirs.
- **Host-deployment tooling and legacy example plugins scrubbed.** The VM/host
  provisioning kit (`host-definitions/`, `lint-vm.yml`, `docs/host-deployment.md`),
  the legacy `examples/kanibako-target-*` plugins, and the archived experimental
  Containerfiles are removed from the release tree (preserved on archival
  branches). A descriptor-based example will return later.
- **Rename-class deprecation shims removed (clean break, no aliases left).**
  - The `image` → `rig` and `container` → `box` command aliases are gone; use
    `rig` and `box` directly.
  - The deprecated `rig create` (and `rig create --template`) and `rig rebuild`
    shims are removed. Use `rig prep <name>` to build/pull a rig, `rig prep
    --force` to refresh one, and `rig extend` to build a custom rig
    interactively. (Image-freshness notices now point to `rig prep --force`.)
  - The `image` / `agent` short config-key aliases are removed; use the
    canonical `box.image` / `box.agent`. The empty `_FIELD_ALIASES` config
    scaffolding was also dropped.
  - The top-level `kanibako vault` command alias is removed; vault snapshot
    commands now live only under `kanibako box vault …`.
- **Data-relocation shims removed (clean break, no auto-migration left).**
  - **Snapshot `.tar.xz` support.** Vault snapshots are now directory snapshots
    only (`reflink` / `hardlink`); the legacy compressed-archive format is no
    longer created, listed, restored, or pruned. Pre-existing `.tar.xz`
    archives are simply ignored — migrate them manually.
  - **Config-file auto-migration from the old location.** `config_file_path`
    now resolves only `$XDG_CONFIG_HOME/kanibako.yaml`; a file left at the old
    `$XDG_CONFIG_HOME/kanibako/kanibako.yaml` is no longer detected or moved.
  - **Global env-file auto-migration from the old location.** An `env` file at
    the old `$XDG_CONFIG_HOME/kanibako/env` is no longer moved to
    `<data>/env`. Move it manually.

### Added

- **First-class Goose and Codex agents.** Two new agent plugins,
  `kanibako-agent-goose` and `kanibako-agent-codex`, join `kanibako-agent-claude`.
  All three are now built on the generalized declarative descriptor contract.
  They version independently of the cli (currently `0.1.0`) and are published as
  their own PyPI packages.
- **The `kanibako` meta-package now installs all three agents** (Claude, Goose,
  and Codex) by default. `pip install kanibako` / `pipx install kanibako` gives
  you the cli plus all three agent plugins.
- **Per-agent binding host-source overrides.** A box's bound host directory for a
  given agent resource (e.g. the plugins dir) can be redirected via the config
  key `agent.<agent>.binding.<key>` (layered over `agent.default.binding.<key>`).
  The value is a host path string, or a sub-table with a `host_src` key.
- **Independent agent publishing in the release pipeline.** `kanibako-agent-goose`
  and `kanibako-agent-codex` are built and published by the release pipeline at
  their own static versions (excluded from the shared dev version stamping), using
  skip-existing semantics so a re-run whose version is unchanged is skipped rather
  than failing. A `workflow_dispatch` `agent` input lets a single agent package be
  published on demand without releasing the whole train.
- **`setup` now selects a default agent.** `kanibako setup` gained an interactive
  numbered menu of detected agents (the only interactive prompt in the CLI) and a
  non-interactive `setup --agent <name>` flag. A "skip" option is offered; with 2+
  agents installed it is gated behind a warning + explicit `y`/`yes` confirm, else it
  re-prompts. Non-TTY runs skip selection gracefully (no prompt). The chosen default
  is written programmatically (`system.*` is otherwise file-only).
- **Setup-completion marker + nudge.** `setup` records a completion marker
  (`system.setup_completed`, the build version); agent-requiring commands print a
  **non-blocking** stderr nudge when setup has never been run (or its recorded version
  predates a setup-affecting change), then proceed.
- **Blanket `--agent` and `--box` flags.** `--agent <name>` is a uniform,
  top-precedence, ephemeral (this-invocation) agent override available on every
  agent-touching command. `--box <name-or-path>` is a universal subject/anchor
  selector — operate on a box that is not your cwd, by box name (precedence) or path;
  it coexists with the `move`/`convert` destination group (`--default/--standalone/
  --workset`). Passing either flag to an unrelated command is an error.
- **Deprecation-tracking mechanism** (`@deprecated` decorator + registry + CI gate)
  for managing post-public deprecations under the major-only breaking-change rule.
  Each deprecation records `{deprecated_in, remove_at (next major), replacement}`;
  a pytest gate fails the build once `__version__` reaches a record's `remove_at`,
  forcing the symbol's removal at the right release. The registry ships empty.
- **Box-name validation.** New box names (creation / `--name`) are checked against a
  blocklist: unicode letters/digits plus interior `_ - .` are allowed; control chars,
  whitespace, ASCII punctuation except `_ - .`, `.`/`..`, leading `-`/`.`, trailing
  `.`/whitespace, and length over 64 are rejected. Uppercase ASCII folds to lowercase.
  Pre-existing non-conforming names are flagged (warned), not rejected.

### Changed

- **The agent-plugin interface is generalized onto one declarative contract.**
  Each agent is now described by a single `PluginDescriptor` (launch command,
  modes, safe-mode toggle, settings/flags, bind mounts, and credential files),
  with generic engines that assemble the launch invocation and synchronize
  credentials. Adding or maintaining an agent is now mostly data. Claude's
  observable behavior is preserved.
- **Codex detection prefers a standalone native binary over the npm shim.** When
  a directly-runnable native `codex` executable is on `PATH` it is used directly;
  the npm-installed build (whose binary sits behind a Node shim) is the fallback.
  This avoids depending on a working Node runtime on the host. A host with only
  the npm install behaves as before; a host with only a standalone binary is now
  detected.
- **Goose secure mode (`-S`) now genuinely requires per-tool approval.** It emits
  `GOOSE_MODE=approve` so Goose asks before running any tool. Previously `-S`
  emitted nothing, leaving Goose in its unsafe `auto` default (tools auto-execute).
  The default autonomous path is unchanged. (Verified honored against a real
  provider.)
- **CI now type-checks the plugin packages** (Claude, Goose, and Codex), closing
  the gap where only the core was type-checked and a plugin could regress silently.
- **Agent resolution is now deterministic — 2+ agents with no choice errors
  (BREAKING).** A single resolver applies the cascade `--agent > box > workset >
  system default` across all agent-requiring commands; when nothing resolves, an
  **installed-count rule** decides with no ordering and no tie-break: exactly 1
  installed → used implicitly; 0 → error (install a plugin); **2+ → error (pick one
  via `setup` or `--agent`)**. This replaces the old behavior where `start` would
  arbitrarily launch the first installed agent in plugin-discovery order (the
  "goose-by-luck" footgun under the all-three meta-package). `kanibako shell` is the
  sole no-agent path and never errors on agent resolution.
- **Reattaching to a running box no longer hits the "pick an agent" error.**
  `kanibako start` against an already-running persistent box now reattaches
  even when 2+ agents are installed with no default: the resolved agent is
  stamped on the container at launch (ephemeral env, not durable config) and
  sourced back on reattach for the per-agent credential refresh, so agent
  resolution succeeds. A matching `--agent` is fine; a differing explicit
  `--agent` is a hard error; a differing system default is superseded (the
  running box wins). A brief reattach heads-up is printed to stderr.

### Fixed

- **Goose launch on Goose 1.37.0.** The bundled Goose grammar used a `session
  start` form that 1.37.0 removed, so launching Goose failed. The grammar is
  rewritten to the verified 1.37.0 tokens. (The previous Goose plugin was an
  unpublished `0.1.0`, so this is not a regression.)
- **Credential writeback now runs on every session-end path.** A box's
  in-box agent config/credentials are written back to the host on **exit,
  detach, reattach-exit, and `stop`** (previously some paths skipped it), the
  host credential file is created when absent, and for Claude the in-box
  `oauthAccount` is **merged** back into the host `~/.claude.json` without
  clobbering the machine identity.
- **Agent auth/config is now detected by launch, not a setup exit code.** When
  the pre-launch `check_auth` probe fails and an agent declares an interactive
  setup command, kanibako runs it **in-box** (`goose configure` / `codex
  login`) — also via `agent reauth` — then proceeds to the real launch and
  inspects its output for a config failure (bounded: it errors if still
  unconfigured, never loops). This replaces the post-launch output-matcher
  approach, which could not fire for agents that exit at the auth probe.
- **Goose secrets now work inside a box (`GOOSE_DISABLE_KEYRING`).** The OS
  keyring / D-Bus secret-service is unavailable in a box, so `goose configure`
  could save `config.yaml` but fail to store the provider API key, and launch
  then failed with `Configuration value not found`. Goose boxes now set
  `GOOSE_DISABLE_KEYRING=true`, so Goose stores/reads secrets in
  `~/.config/goose/secrets.yaml` (a file kanibako already syncs host↔box).
- **Goose no longer forces a provider/model.** The Goose target previously
  emitted hardcoded `GOOSE_PROVIDER`/`GOOSE_MODEL` defaults, which override
  Goose's own `config.yaml` and clobbered an in-box `goose configure` choice
  (leading to `Configuration value not found`). Unset provider/model now emit
  no env var, so Goose falls back to its own config; an explicit
  `agent.goose.provider` / `agent.goose.model` still wins the cascade and is
  emitted.
- **Goose `config.yaml` + `custom_providers/` now sync back to the host.**
  In-box `goose configure` writes provider/model (and any custom-provider
  definitions) into the box home; previously only `secrets.yaml` synced back,
  so the host-side auth gate never saw a configured Goose and re-ran
  `goose configure` on every start. These are now two-way sync targets (with
  directory support added to the credential-sync engine), so an in-box Goose
  configuration persists across restarts — parity with Claude/Codex. (This is
  a plain sync of the box's own config; the 1.6.0 host-config *import* stays
  removed.)
- **Goose now falls back to a new session when none exists to resume.** Goose's
  continue mode launches `goose session --resume`; on a fresh box there was no
  prior session, so Goose exited with `No session found to resume` and the box
  closed immediately. Goose now opts into the existing no-session fallback
  (matched case-insensitively) and relaunches with a fresh session.
- **Image-freshness banner only warns when the remote is provably newer.** The
  check previously warned whenever the local and remote `:latest` digests
  differed, so any pinned, locally-built, or retagged image — or a same-version
  rebuild of remote `:latest` — nagged on every start. Freshness now uses a
  two-prong test (compare versions when both resolve via PEP 440, else compare
  build `created` timestamps only when neither side resolves a version, else
  stay silent) and never nags on uncertainty.
- **Seed-once is now non-destructive and registry-tracked (data-loss fix).**
  A box re-seed could clobber user-edited home content (notably `~/playbook/*`):
  seed-once gated only on a `.seeded` marker file under the box metadata dir,
  and any box missing that marker (every box migrated from the pre-1.6.0 layout)
  re-seeded on launch and overwrote its owned home. The redesign closes this on
  two fronts:
  - **Seed application is create-if-absent.** Applying a `seeded` category now
    copies file-by-file and skips any destination that already exists (was a
    destructive `copytree(dirs_exist_ok=True)` / unconditional file copy). A
    seed delivers content once; existing home content is owned by the box and is
    never overwritten. Cross-layer template last-wins is preserved. This is the
    failsafe: even a mis-detected re-seed cannot lose data.
  - **Seed-once detection moved off the marker file.** The launch gate now
    derives "already seeded?" from an explicit per-box `seeded` flag in the
    registry (`registry.yaml` for primary/standalone boxes, the workset's
    `settings.yaml` for named boxes), stamped on first-start completion, ORed
    with a backstop that the box's own mailbox (inbox) dir already exists. The
    brittle `.seeded` sentinel (and its `needs_seed` / `mark_seeded` machinery)
    is **removed**.
  - **Legacy boxes are adopted automatically.** A pre-existing box with no
    registry flag is recognised as seeded via the inbox backstop and stamped
    with the flag on detect — no converter or migration step is required.

## [1.5.1] - 2026-06-16

### Fixed

- **Recurring Claude Code launch crash (`crun: … No such file or directory`).**
  Launching the Claude agent could fail with a cryptic OCI runtime error while
  `system diagnose` still reported the agent `[ok]`. Root cause: kanibako mounted
  the host's live `~/.local/share/claude` read-only and froze the container's
  launcher symlink to the version resolved at detect time; kanibako's own
  host-side auth probes woke Claude's background auto-updater, which repointed and
  pruned that version mid-launch, leaving the frozen pointer dangling. The Claude
  agent binary is now delivered **host-owned and inode-pinned**: kanibako runs a
  synchronous update gate, disables the auto-updater on every Claude invocation it
  owns and inside the box (`DISABLE_AUTOUPDATER=1`), clears the mount
  destinations, and bind-mounts the launcher and install dir **as-is** (podman
  dereferences the symlink and pins the inode at mount, so later host churn —
  prune/repoint — can't pull the file out from under a running box). If a bind
  source is missing at mount time it now fails with a clean, actionable error
  instead of a crun crash. Hardening: the Claude plugin no longer uses `$PATH`
  (`shutil.which`) to locate the binary it execs on the host and mounts into the
  box — it anchors to the contract paths (`~/.local/bin/claude`,
  `~/.local/share/claude`) and re-detects after the update gate, so validation,
  the "Using host Claude Code" line, and the bind all consume one fresh install.
- **Perpetual "a newer version is available" banner for buildx-built images.**
  Image-freshness checks compared the local **amd64 platform-manifest** digest
  against the remote **OCI index** digest — which never match for multi-arch
  buildx images (index + attestation) — so the update banner fired on every run
  even when the local image was already current. Freshness now compares a
  canonical **per-architecture** digest set for the platform actually run: it
  intersects the full set of local digests (including the index digest, which the
  old code never inspected) with the remote index plus its matching per-arch child
  manifest (skipping the `unknown/unknown` attestation entry), and warns only when
  the two sets are genuinely disjoint.

## [1.5.0] - 2026-06-14

### Added (configurable no-agent box shell)

- **`box.shell` setting.** Selects the shell launched for a **no-agent** box
  (`kanibako start` with no agent, and `kanibako shell`); it does not affect agent
  launches (agents keep their own entrypoint). Resolved first-defined-wins:
  `box.shell` (explicit path or name) → `$KANIBAKO_SHELL` (the default of
  `box.shell`) → the image's recorded login shell → `sh`. The image's login shell
  is captured at image install/prep time (via `getent passwd` for the box user)
  and stored keyed by image digest, so launch and diagnose read the recorded value
  rather than probing on the hot path.

### Changed (diagnose: resolved shell + re-graded agent checks)

- **`system diagnose` / `crab diagnose` agent checks.** The "Shell" line now shows
  the resolved no-agent shell and which step won (e.g. `Shell: /bin/bash (image
  default)`). Agent severities were re-graded: an optional agent that is simply not
  installed is now informational (`[--] not installed (optional)`) rather than an
  error; a detected agent whose binary path is missing is reported as an error
  (`[!!] binary not found at <path>`); and the built-in no-agent Shell fallback is
  no longer flagged as "not found".

### Fixed (pre-broad-release)

- **Corrupt/empty host agent binary now fails fast with an actionable error.**
  A 0-byte or non-executable host agent binary previously passed the mount check
  (`is_file()` is true for a 0-byte file) and was exec'd into a brick — in
  persistent/detached mode the user saw only the generic "Container exited before
  session could attach", with no clue it was a binary problem. The launch path now
  validates the resolved host binary (must exist, be non-zero, and be executable)
  before mounting/launching, and on failure prints a clear remediation (reinstall
  the host agent / prune stale 0-byte versions; run `kanibako system diagnose`)
  and aborts instead of launching. `system diagnose` mirrors the same check: a
  detected agent whose binary is empty or non-executable is now flagged `[!!]`
  (previously only a fully *missing* path was flagged). The validation is lenient
  (no ELF/shebang requirement) so legitimate native binaries and wrapper scripts
  are not false-flagged.
- **`box diagnose <name>` / lifecycle commands by bare workset name.** A bare
  registered *workset* name passed to `box diagnose` (or to `remap`/`move`/
  `convert`) is no longer path-ified relative to the current directory (which
  failed with a misleading `path … does not exist`); it now reports clearly that
  the token names a workset, not a single project box. The shared name resolvers
  (`resolve_any_project`, `resolve_lifecycle_target`) now honor a resolved
  workset name as well as a project name. Resolving a bare *project* name from
  any directory already worked and is unchanged.
- **`box diagnose` outside a registered project.** Running it on a moved/copied
  workspace or a plain non-project directory no longer prints a false
  `[ok] Project directory` + `[!!] Shell directory: missing`; it now reports
  clearly that no kanibako project is registered for the path.
- **`box duplicate --to default` / `--to standalone` of an external-connected
  project** now works instead of raising an uncaught `WorksetError`.
- **`kanibako shell` is now image-aware.** An interactive no-agent shell now
  resolves `box.shell` *with* the box's runtime/image, so the image's recorded
  login shell participates (previously `kanibako shell` resolved without an image
  handle and always landed on the `sh` floor when nothing was configured, even
  though `diagnose` advertised the image default). The plain-shell guarantee is
  unchanged: `kanibako shell` never launches an agent even when one is installed.
- **`kanibako system config box.shell`** (read-back) no longer reports an unknown
  config key; `box.shell` is now a recognized GET key (set / `--reset` already
  worked).
- **`kanibako system config box.bootstrap_program`** (read-back) no longer reports
  an unknown config key; `box.bootstrap_program` is now a recognized GET key (set /
  `--reset` already worked).

### Changed (base images are now pull-only; pre-broad-release)

- Base images (`kanibako-{min,oci,lxc,vm}`) are now **pull-only**: the cli no
  longer bundles or builds the base `Containerfile.kanibako`. A pull failure
  reports an actionable error instead of falling back to a local build
  (`rig prep`/`rig rebuild`/box launch all pull, never build, a base image).
  Templates (`Containerfile.template-*`) and `tmux.conf` still ship and still
  build, layering on a *pulled* base. To use a custom base, build it yourself
  from the [kanibako-images](https://github.com/doctorjei/kanibako-images) repo
  and pass it via `--image` / `box_image`.
- Image resolution now prefers a locally present **official** ref
  (`{registry}/{owner}/kanibako-<variant>`) over a non-official local build
  (e.g. `localhost/kanibako-<variant>`); a non-official local build is still
  used when no official image is present locally, and the official ref is the
  pull target when neither is local.

### Changed (v1.5.0 settings-framework rewrite — Part 1; BREAKING, pre-broad-release)

The configured-agent-in-a-box is now consistently called a **crab** (the external
decision-making entity stays an **agent**; the plugin/`Target` layer that adapts an
agent stays agent-domain). These are breaking renames with **no back-compat shims**
(the `kanibako agent` CLI noun is kept only as a typed alias for `kanibako crab`):

- **CLI / data model:** `kanibako agent …` → `kanibako crab …` (alias: `agent`);
  `AgentConfig` → `CrabConfig`; per-crab data dir `agents/` → `crabs/`
  (move the dir by hand — no auto-migration); per-crab TOML section `[agent]` → `[crab]`.
- **Config keys:** `target_name` → `crab_name`; `vault_enabled` → `enable_vault`;
  `helpers_disabled` → `allow_helpers` (**inverted** boolean); `default_args` →
  `run_args`; the project.toml `[target_settings]` section → `[crab_settings]`.
- **Auth is now a boolean:** `auth = "shared"`/`"distinct"` → `group_auth = true`/`false`
  (default `true` = shared credentials across the group). `--distinct-auth` flag
  unchanged (now sets `group_auth = false`).
- **Plugin entry-point group:** `kanibako.targets` → `kanibako.agents` (a registry of
  agent adapters). The Python import path `kanibako.targets.*` and the `Target` API are
  unchanged; reinstall plugin packages for the new group to take effect.
- **Cleanup:** removed the dead `kanibako-plugin-claude` wrapper package; fixed the
  `ws_hints` default name (`working_sets.toml` → `worksets.toml`); dropped the unused
  `paths.workspaces` config key.
- **Core package renamed:** the core distribution `kanibako-base` → `kanibako-cli`
  (matches the post-split repo name). `pip install kanibako` (the meta package) is
  unchanged; the Python import module stays `kanibako`. Old `kanibako-base` (≤1.4.0)
  remains on PyPI but is no longer updated.

### Added (v1.5.0 — default workset; Phase 2)

The default-mode projects group is now modeled as a synthesized **default
workset**, so default-workset settings use the same mechanism as named worksets.

- **Default workset is addressable.** It is a virtual workset (fixed id
  `__default__`, typeable alias `default`; never created or deleted, no on-disk
  file). `kanibako workset list` shows it (root `<default workset>`),
  `kanibako workset info default` works, and `kanibako workset config default
  <key>=<value>` sets defaults for all default-mode projects. `default` /
  `__default__` are reserved as workset names; the default workset cannot be removed.
- **Workset config tier.** A workset's `config.toml` is now applied at box
  start/status. Precedence is now `CLI > project.toml > workset config.toml >
  crab config > kanibako.toml (system) > defaults`. For a named workset the file
  is `<workset_root>/config.toml`; for the default workset it is
  `<data_dir>/config.toml`. `group_auth` set on the default workset applies to
  all default-mode projects (a project may still narrow shared→distinct).

### Changed (Phase 2; pre-broad-release)

- **Named worksets now honor their `config.toml` at box start/status** (the
  `KanibakoConfig` keys, e.g. `container_image`). Previously this file was only
  read by the `workset config` command and ignored when launching — now it
  participates in the precedence chain above. No-op for installs without a
  workset `config.toml`.

### Added (v1.5.0 — settings resolver; Phase 3)

A general configuration **resolver** now underlies all settings. Config keys use
a uniform `level.group.key` scheme resolved across the precedence stack
(`box > workset > crab > system > built-in/target defaults`), with a small grammar
in path-like values:

- **Value grammar.** `@level.group.key` references another resolved value
  (cycle-guarded); `$CRAB` / `$WORKSET` / `$XDG_*` and `~` expand (host `~` vs the
  guest `/home/agent`); `\@ \$ \\ \:` escape. An explicitly-set empty string
  (`key: ""`) is **terminal** — it suppresses an inherited value rather than
  falling through to a default (distinct from "unset").
- **Scoped shares (the dir-sharing mechanism).** `{scope}.path.share_ro.{name}` /
  `{scope}.path.share_rw.{name}` (scopes `system` / `crab` / `workset` / `box`)
  declare `host_src:guest_dest` bind mounts, accumulated `system→crab→workset→box`
  (later wins; per-`(scope,name)` identity). Source roots: `system` →
  `system.path.share_ro|rw`; `crab` → `crabs/{crab}/share`; `workset` → workset
  root; `box` → an arbitrary host path. (Mechanism only; the user-facing
  `workset share` command — see below — drives the workset scope.)
- **Init seeds.** `{level}.path.seeded.{name}` declares `host_src:guest_dest`
  pairs copied **once** into a new box at init.
- **`kanibako box config --effective`** now resolves through the exact same stack
  `start` uses (workset tier + 4-level crab walk + layered env), so it matches
  what a real launch produces.

### Added (v1.5.0 — machine config layer, configurable bootstrap, launch check)

- **`/etc/kanibako/` machine-wide config layer.** A new resolver source for all
  config, below `~/.config` user config and above built-in defaults (user wins
  over `/etc`; `/etc` wins over defaults). `/etc/kanibako/kanibako.yaml` feeds the
  general config stacks; `/etc/kanibako/image-baseline.yaml` overlays the baseline.
  Known limitation (shared by all file layers): a more-specific layer cannot
  reset a value back to one that equals the built-in default, because the overlay
  treats "equals default" as "unset" (e.g. `/etc` setting a non-default
  `box_bootstrap_program` can't be reset to the default `tmux` by user config).
  Tracked for a follow-up (explicit-set tracking).
- **Configurable bootstrap program.** The program that runs the interactive
  session is now the `box_bootstrap_program` config key (default `tmux`),
  resolver-backed so it is project/workset/box overridable. tmux keeps its
  `new-session`/`attach` shape; any other program is exec'd directly.
- **Two-tier baseline check at launch.** Before a box launches, the configured
  image is probed once: the bootstrap program is **launch-critical** (missing →
  hard stop, with a reminder that a shell is still reachable to investigate), and
  the rest of the baseline is **warn-only** (never blocks; warnings are persisted
  to `$XDG_STATE_HOME/kanibako/launch-issues.<box>` and reprinted after the
  session closes). `kanibako diagnose` (rig) gains `--all`/`--only`/`--skip` and
  defaults to the single configured image.

### Added (v1.5.0 — image baseline manifest + `baseline` command)

The set of tools every kanibako box must provide is now declared data, decoupled
from the image build so it applies to any base (including a foreign `--image`):

- **`image-baseline.yaml`** (shipped as package data) maps apt package → the
  executables it must provide (`tmux`, `inotify-tools`, `ripgrep`, `fd-find`,
  `openssh-client`). Install uses the package name; verify uses `command -v` on
  the executable (package-manager-agnostic). Same-named files at `/etc/kanibako/`
  then `~/.config/kanibako/` merge additively on top.
- **`kanibako baseline list|verify|install`.** `list` prints the package names
  (so an image build can `apt-get install $(kanibako baseline list)`);
  `--executables` prints the per-package executables. `verify [IMAGE]` probes a
  single image by default (`--all`, `--only`, `--skip`); exits non-zero if any
  executable is missing. `install` installs the package set on a debian base.
- Fixed a wheel packaging bug that omitted `containers/tmux.conf` from the built
  distribution.

### Added (v1.5.0 — workset share command)

A user-facing surface for the scoped-share mechanism, scoped to a working set:

- **`kanibako workset share add <workset> <name> <host_src:guest_dest> [--mode
  {ro,rw}]`** writes a `workset.path.share_{ro,rw}.{name}` key into the working
  set's `config.yaml` (default mode `rw`). A relative `host_src` resolves under
  the working set root; an absolute `host_src` is used as-is. Re-running `add`
  with the same name overwrites the mapping (this is how a share is "updated" —
  shares are live bind mounts and no content sync exists).
- **`kanibako workset share rm <workset> <name> [--mode {ro,rw}]`** (alias
  `remove`) deletes a configured share. With no `--mode` it removes whichever of
  ro/rw holds the name; `--mode` is required when the name exists in both.
- **`kanibako workset share list <workset> [--effective]`** (alias `ls`, default)
  lists the configured shares (raw `NAME MODE BIND`); `--effective` resolves them
  the way a launch would and prints the final `source -> dest [mode]` mounts.
- Shares are bind mounts fixed at container creation, so changes take effect on
  the **next box launch** (a running box is unaffected); each mutation prints
  this reminder. The general `kanibako share --scope …` surface is deferred.

### Added (v1.5.0 — project lifecycle commands; location/ownership split)

The project-lifecycle commands are redesigned around two explicit axes —
*where the files live* and *which mode/workset owns the project* — built on one
shared transactional routine that unwinds partial changes on error (it never
deletes a user's external source directory). A uniform target vocabulary
`--default` / `--standalone` / `--workset <ws>` (mutually exclusive) appears on
both `move` and `convert`; the target flag itself is the trigger (there is no
`--convert` flag).

- **`kanibako box remap <old> [<new>]`** (new) records-only relocation: after you
  have moved the folder yourself, update kanibako's recorded path, hash, and
  markers (workspace override / `connected.yaml` / `workspaces/<name>` symlink) to
  match the new location's inside/outside-workset status. It does not move files
  and never changes ownership. `<new>` defaults to `./`.
- **`kanibako box convert [<old>] (--default | --standalone | --workset <ws>)
  [--move [path]]`** (new) changes a project's ownership/mode. `<old>` defaults to
  `./`. In-place by default for all modes (the workspace does not move). `--move
  <path>` relocates to that path; bare `--move` moves the workspace into the target
  working set (`<ws>/workspaces/<name>`) and is only valid with `--workset`.
  `--name` renames. New capability: **workset → workset re-rooting**.
- **`kanibako box move`** gains the target vocabulary too — `move <old> <new>
  --workset <ws>` relocates and changes ownership in one step.

### Changed (v1.5.0 — project lifecycle commands; BREAKING, pre-broad-release)

- **`kanibako box move <old> <new>`** (alias `mv`) now requires **both** paths
  explicitly (previously `box move [project] <dest>`). It physically relocates the
  workspace; an optional target flag (`--default` / `--standalone` / `--workset
  <ws>`) also changes ownership (none = pure move, owner unchanged). It now also
  rewrites the project record (mode/paths/hash) and reconciles external/internal
  markers. It refuses external-connected projects — use `remap` or `convert`
  instead.
- **`kanibako box duplicate --bare`** now refuses an external-connected source
  (the connected-directory mapping is 1:1, so a bare copy cannot share the same
  external workspace). Use a non-bare copy, which lands a fresh workspace.
- **`kanibako workset connect`** now refuses a source that is inside another
  registered working set's tree or already connected, and **`kanibako workset
  disconnect`** now cleans up the `connected.yaml` entry, the `workspaces/<name>`
  symlink, and the workspace override; `disconnect --remove-files` never deletes
  the user's external source directory.

### Removed (v1.5.0 — project lifecycle commands; BREAKING, pre-broad-release)

- **`kanibako box migrate` is removed** (clean break, no aliases). It overloaded
  path-remapping and mode-conversion onto one command; those axes are now the
  separate `box remap` / `box move` (location) and `box convert` (ownership)
  commands above. Note that `convert` is **in-place by default for all modes**,
  whereas the old `migrate --to` moved the project into the target workset unless
  `--in-place` was given.

### Changed (v1.5.0 — settings resolver; Phase 3; BREAKING, pre-broad-release)

Breaking config-key and format changes, **no back-compat shims** (see the
**Migration** section below for the one-off conversion):

- **Box scalars move under `box.*`:** `container_image` → `box.image`,
  `crab_name` → `box.crab`, `share_images` → `box.share_images` (per-file section
  `[box]`).
- **System paths move under `system.path.*`:** the former `paths_*` config keys
  become `system.path.{data,boxes,crabs,comms,templates,ws_hints}` (section
  `[system.path]`), each defaulting to an `@`-ref expression (e.g.
  `boxes: "@system.path.data/boxes"`). The `KanibakoConfig.paths_*` fields are
  removed.
- **Crab config is one section.** The crab file's `[state]` table is folded into
  `[crab]` (identity keys `name`/`shell`/`run_args` plus the state knobs), and a
  project's `[crab_settings]` table becomes `[crab]`. Effective crab state is now
  resolved by a 4-level walk (`box > workset > crab > system`) with the target
  plugin's declared defaults as the floor.
- **Env layering.** Environment variables now accumulate across config levels
  `system < crab < workset < box` (box wins on collision), with target-derived
  state env and per-run `-e` on top. A named workset may contribute an `env`
  file.
- **Claude plugins relocated.** The Claude `plugins/` directory is now served via
  a crab-scoped share at `crabs/claude/share/plugins` (declared through the new
  `Target.default_shares()` API) instead of the old global SHARED mapping. No
  migration: the old plugins dir is orphaned and repopulates on next launch.
- **Vault host-side subdirs renamed** `share-ro`/`share-rw` → `ro`/`rw` (the
  in-guest mountpoints `/home/agent/share-ro` and `/home/agent/share-rw` are
  **unchanged**). See Migration for the on-disk move.
- **Config file format is now YAML.** All kanibako-owned config files are written
  and read as YAML (`*.toml` → `*.yaml`): `kanibako.yaml`, `project.yaml`,
  `config.yaml`, `workset.yaml`, `worksets.yaml`, `names.yaml`, `spawn.yaml`,
  `general.yaml`, and the crab configs. Keys and structure are identical to the
  former TOML — only the serialization changed. (`pyproject.toml` is Python
  packaging and is unaffected.)
- **Deferred (intentionally unchanged this release):** `group_auth`,
  `enable_vault`, and `layout` remain init-frozen project-identity fields in the
  `project.yaml` `[project]` meta (not moved to `box.*`); `allow_helpers` stays a
  box-level key. These may be exposed as `box.*` in a later release.

### Changed (v1.5.0 — terminology; BREAKING, pre-broad-release)

- **Project mode `local` → `default`** (BREAKING rename). The mode formerly
  written as `mode: local` in `project.yaml` is now `mode: default`.
  Existing on-disk breadcrumbs with `mode: local` are **auto-migrated** to
  `ProjectMode.default` on read via the back-compat map — no manual conversion
  needed for the mode field.
- **Default workset root display label updated.** The root column shown for
  the default workset in `kanibako workset list` is now `<default workset>`
  (previously displayed a legacy label).

### Migration (v1.5.0 — manual, one-off; no auto-migration)

There is **no migration code** — convert existing installs in a single pass:

1. **Rename config files** `*.toml` → `*.yaml` and **convert their contents to
   YAML** (same keys/sections; `[section]` → `section:` mapping, `k = "v"` →
   `k: "v"`; keep an explicit empty string as `k: ""`, never bare `k:`).
2. **Apply the key map** (old → new):

   | Old key | New key |
   |---------|---------|
   | `container_image` | `box.image` |
   | `crab_name` | `box.crab` |
   | `share_images` | `box.share_images` |
   | `paths_{data_path,boxes,crabs,comms,templates,ws_hints}` | `system.path.{data,boxes,crabs,comms,templates,ws_hints}` |
   | crab file `[state].X` | `[crab].X` |
   | project `[crab_settings].X` | `[crab].X` |

   (The Part-1 renames — `vault_enabled`→`enable_vault`,
   `helpers_disabled`→`allow_helpers` inverted, `default_args`→`run_args`,
   `auth`→`group_auth` boolean, the `agents/`→`crabs/` data dir, `[agent]`→`[crab]`
   — are already documented above and applied if migrating from pre-Part-1.)
3. **Rename vault subdirs** in every project/helper vault: `vault/share-ro` →
   `vault/ro` and `vault/share-rw` → `vault/rw` (`mv` by hand so the share-rw
   **data** is preserved, not orphaned). Guest mountpoints are unchanged.

## [1.4.0] - 2026-06-04

### Changed

- **Bundled templates are now local-build + CI-verified.** The bundled
  toolchain templates (`Containerfile.template-*`) are built locally on the
  user's host via `kanibako rig create <name> --template <name>`. CI now
  *verifies* them -- building each template and running its toolchain smoke
  checks, declared via a new `# kanibako-template-check: <cmd>` Containerfile
  header (sibling to the existing `# kanibako-template:` header) -- instead of
  publishing them. (Running the templates in CI for the first time surfaced and
  fixed three latent issues: the smoke step must bypass the base `ENTRYPOINT`;
  the `android` SDK is found via the image `ENV PATH` (non-login shell); and the
  `js` template pins `pnpm@9` since pnpm 10+ requires Node >=22.13 but the base
  ships Node 20.)
- **`rig create --template` honors the template's declared base.** Each
  template declares its base via `ARG BASE_IMAGE` (default `kanibako-oci`).
  `--template` builds on that declared base by default; `--base <image>` is now
  an explicit override (and prints a note when used).

### Removed

- **Stopped publishing `kanibako-template-*` images to GHCR.** The bundled
  templates are no longer pushed to any registry; they are local-only artifacts
  that CI build- and smoke-verifies. User-built custom rigs remain ordinary OCI
  images you can push to your own registry.

## [1.3.2] - 2026-06-04

### Fixed

- **`kanibako shell <box> -e KEY=VAL -- cmd` now applies `-e` vars when the box
  is already running.** When a persistent box was up, the shell-into-running
  shortcut exec'd the command without the per-run `-e`/`--env` vars, so the same
  command behaved differently depending on whether the box happened to be
  running (the vars were applied on a fresh launch but silently dropped on
  exec). The per-run env is now passed to the exec'd process in both cases.

## [1.3.1] - 2026-06-04

### Added

- **User-override templates are now discoverable.** `rig list` and
  `rig create --template` validation also scan the user-override containers
  directory (`$XDG_DATA_HOME/kanibako/containers/`) for
  `Containerfile.template-<name>` files, mirroring the override-first
  precedence already used when building. User-provided templates appear in
  `rig list` marked `(user)` and a user template overrides a bundled one of the
  same name. (#74b)

### Fixed

- **`kanibako rm --purge` no longer crashes on a started box.** A box whose
  shell directory contains files a rootless container created (owned by mapped
  subuids the host user cannot unlink) previously aborted `rm --purge` with a
  Python traceback. Such trees are now removed via the user namespace
  (`podman unshare`), with a clean warning if removal still cannot complete.

### Changed

- Maintenance: the CI container-image workflow now rebuilds only the bundled
  templates whose `Containerfile.template-*` changed (full rebuild only on a
  shared/base change), cutting build time (#74a). The e2e test harness gained a
  pinned-store pre-warm with diagnostics and a corrected timeout, plus new
  coverage for env forwarding, flag-after-positional parsing, shell-exec into a
  running box, `ps`/`ps -q`, and `rm --purge` (#74c, #79). No user-facing change.

## [1.3.0] - 2026-06-03

### Added

- **Bundled toolchain templates and `rig create --template <name>`.** Kanibako
  ships a curated set of toolchain templates that build on demand onto any base
  rig via `ARG BASE_IMAGE`: `jvm` (Java/Kotlin/Maven), `systems`
  (C/C++/Rust/cross-compilation), `js` (yarn/pnpm/bun/TypeScript), `dotnet`
  (.NET 8 LTS SDK), and `android` (Android SDK command-line tools + NDK).
  - `kanibako rig create <name> --template <template> [--base <variant>]` builds
    the bundled template non-interactively into a local `kanibako-template-<name>`
    image. Without `--template`, `rig create` keeps its interactive
    install-and-commit flow.
  - Templates are auto-discovered by the `Containerfile.template-<name>` filename
    convention (descriptions from a `# kanibako-template:` header); dropping such
    a file makes it appear in `rig list`, become buildable, and get published by
    CI with no code or workflow edits.
  - Prebuilt template images are published to GHCR as
    `kanibako-template-<name>-<variant>` for the `min`/`oci`/`lxc`/`vm` variants,
    via a dynamic CI build matrix.

### Changed

- Default and workset project modes were unified onto a single code path, carrying
  the distinction as data (a `ProjectGroup` descriptor) rather than control flow.
  Behavior-preserving; no user-visible change.

[Unreleased]: https://github.com/doctorjei/kanibako-cli/compare/v1.8.0...HEAD
[1.8.0]: https://github.com/doctorjei/kanibako-cli/compare/v1.7.2...v1.8.0
[1.7.2]: https://github.com/doctorjei/kanibako-cli/compare/v1.7.1...v1.7.2
[1.7.1]: https://github.com/doctorjei/kanibako-cli/compare/v1.7.0...v1.7.1
[1.7.0]: https://github.com/doctorjei/kanibako-cli/compare/v1.6.0...v1.7.0
[1.6.0]: https://github.com/doctorjei/kanibako-cli/compare/v1.5.1...v1.6.0
[1.5.1]: https://github.com/doctorjei/kanibako-cli/compare/v1.5.0...v1.5.1
[1.5.0]: https://github.com/doctorjei/kanibako-cli/compare/v1.4.0...v1.5.0
[1.4.0]: https://github.com/doctorjei/kanibako-cli/compare/v1.3.2...v1.4.0
[1.3.2]: https://github.com/doctorjei/kanibako-cli/compare/v1.3.1...v1.3.2
[1.3.1]: https://github.com/doctorjei/kanibako-cli/compare/v1.3.0...v1.3.1
[1.3.0]: https://github.com/doctorjei/kanibako-cli/releases/tag/v1.3.0
