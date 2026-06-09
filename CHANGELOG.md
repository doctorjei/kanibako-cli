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
