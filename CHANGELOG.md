# Changelog

All notable changes to kanibako are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

> Releases before v1.3.0 are not yet backfilled here. For their notes and full
> changelogs, see the [GitHub releases](https://github.com/doctorjei/kanibako/releases).

## [Unreleased]

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

[Unreleased]: https://github.com/doctorjei/kanibako/compare/v1.3.0...HEAD
[1.3.0]: https://github.com/doctorjei/kanibako/releases/tag/v1.3.0
