# Changelog

All notable changes to kanibako are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

> Releases before v1.3.0 are not yet backfilled here. For their notes and full
> changelogs, see the [GitHub releases](https://github.com/doctorjei/kanibako/releases).

## [Unreleased]

### Added

- **`kanibako rig update [<name>]`** — the everyday "get the latest" path for a
  rig. For a pulled/prefab rig it pulls the newer upstream image; for a
  template/built rig it rebuilds on the refreshed base. With no name it targets
  the configured `box.image` rig; `--all` updates every local rig. `rig prep
  --force` is kept as the full rebuild-from-scratch path.

### Changed

- Image-freshness notices now suggest `kanibako rig update` (was `kanibako rig
  prep --force`).

## [1.6.0] - 2026-06-17

This release generalizes kanibako's agent-plugin interface so that any agent is
described by one declarative contract, and ships first-class **Goose** and
**Codex** agents alongside Claude. The `kanibako` meta-package now installs all
three by default. It also lands a large **config / settings revamp** (one breaking
change set) that splits config from settings, renames `crab` → `agent`, restructures
the `system.*` namespace, unifies worksets, rebuilds the comm system as channels,
and reworks templates.

### Changed (BREAKING — config / settings revamp)

The revamp is **one breaking change set** with **no automatic migration** — see
[`MIGRATION.md`](MIGRATION.md) for the full step-by-step runbook. Summary:

- **Config vs settings split.** Layout (`system.*`, *where things live*) and
  behavior (`agent.*`/`box.*`/`workset.*` + the category keys) now live in separate
  file sets. A 6-tier settings cascade applies (`settings_base < system <
  agent.<agent> < workset < box`, with `*_required` an absolute cap above `box`).
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

[Unreleased]: https://github.com/doctorjei/kanibako/compare/v1.6.0...HEAD
[1.6.0]: https://github.com/doctorjei/kanibako/compare/v1.5.1...v1.6.0
[1.5.1]: https://github.com/doctorjei/kanibako/compare/v1.5.0...v1.5.1
[1.5.0]: https://github.com/doctorjei/kanibako/compare/v1.4.0...v1.5.0
[1.4.0]: https://github.com/doctorjei/kanibako/compare/v1.3.2...v1.4.0
[1.3.2]: https://github.com/doctorjei/kanibako/compare/v1.3.1...v1.3.2
[1.3.1]: https://github.com/doctorjei/kanibako/compare/v1.3.0...v1.3.1
[1.3.0]: https://github.com/doctorjei/kanibako/releases/tag/v1.3.0
