# Changelog

All notable changes to kanibako are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

> Releases before v1.3.0 are not yet backfilled here. For their notes and full
> changelogs, see the [GitHub releases](https://github.com/doctorjei/kanibako/releases).

## [Unreleased]

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

### Added (v1.5.0 — workset↔account merge; Phase 2)

The account / local-projects group is now modeled as a synthesized **default
workset**, so "account-wide" settings use the same mechanism as named worksets.

- **Default workset is addressable.** It is a virtual workset (fixed id
  `__default__`, typeable alias `default`; never created or deleted, no on-disk
  file). `kanibako workset list` shows it (root `<account-wide>`),
  `kanibako workset info default` works, and `kanibako workset config default
  <key>=<value>` sets defaults for all local/account projects. `default` /
  `__default__` are reserved as workset names; the default workset cannot be removed.
- **Workset config tier.** A workset's `config.toml` is now applied at box
  start/status. Precedence is now `CLI > project.toml > workset config.toml >
  crab config > kanibako.toml (system) > defaults`. For a named workset the file
  is `<workset_root>/config.toml`; for the default workset it is
  `<data_dir>/config.toml`. `group_auth` set on the default workset applies to
  all account projects (a project may still narrow shared→distinct).

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
  root; `box` → an arbitrary host path. (This ships the mechanism only; a
  user-facing "share a directory" command is a separate follow-on.)
- **Init seeds.** `{level}.path.seeded.{name}` declares `host_src:guest_dest`
  pairs copied **once** into a new box at init.
- **`kanibako box config --effective`** now resolves through the exact same stack
  `start` uses (workset tier + 4-level crab walk + layered env), so it matches
  what a real launch produces.

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

- Local and workset project modes were unified onto a single code path, carrying
  the distinction as data (a `ProjectGroup` descriptor) rather than control flow.
  Behavior-preserving; no user-visible change.

[Unreleased]: https://github.com/doctorjei/kanibako/compare/v1.3.2...HEAD
[1.3.2]: https://github.com/doctorjei/kanibako/compare/v1.3.1...v1.3.2
[1.3.1]: https://github.com/doctorjei/kanibako/compare/v1.3.0...v1.3.1
[1.3.0]: https://github.com/doctorjei/kanibako/releases/tag/v1.3.0
