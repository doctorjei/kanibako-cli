# Changelog

All notable changes to the kanibako base images are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Image versions track the `kanibako-cli` version line: a `vX.Y.Z` images release
bundles `kanibako-cli==X.Y.Z` (passed as the `KANIBAKO_CLI_VERSION` build-arg).

## [Unreleased]

### Added
- `release.yml`: rc-then-promote release workflow. An `vX.Y.Z-rcN` tag builds and
  publishes the four `:X.Y.Z-rcN` rc images (each with
  `--build-arg KANIBAKO_CLI_VERSION=X.Y.Z`); a final `vX.Y.Z` tag (or a manual
  promote dispatch) copies the green rc manifests by digest to `:X.Y.Z` and
  `:latest` with no rebuild.
- `CHANGELOG.md`, `README.md`, and a `VERSION` file (this commit).
- `containers/archive/README.md` and `host/README.md` documenting those dirs.

### Changed
- `containers/Containerfile.kanibako`: the shared (all-variant) package set now
  derives its baseline tool list from `kanibako baseline list` at build time
  (tmux, inotify-tools, ripgrep, fd-find, openssh-client) so the image and the
  CLI never drift. The CLI pip install now runs before that step and was renamed
  `kanibako-base` -> `kanibako-cli`, made version-aware via the new
  `KANIBAKO_CLI_VERSION` build-arg. fd-find/inotify-tools/ripgrep dropped from
  the min-only block (now baseline-provided for all variants); sshpass stays
  min-only. The all-variant block comment was reworded "general" -> "shared".
- `build-images.yml`: the manual `:latest` build now pins `KANIBAKO_CLI_VERSION`
  to the repo's `VERSION` file (a new "Resolve cli version" step) instead of
  building against an unpinned `kanibako-cli`, so push-to-main and dispatch
  builds track a known cli release.

## [1.4.0] - 2026-06-13

The history below predates this changelog and is reconstructed from git.

### Changed
- Install `slirp4netns` in the lxc variant for rootless-podman networking
  (40b5382).
- Publish `:latest` only; retire the `:edge` tag. `build-images.yml` now builds
  on push to main and publishes `:latest` only on a manual `push=true` dispatch
  (69e815a).

### Added
- `prune-tags.yml`: manual, dry-run-default GHCR tag cleanup that deletes a tag
  by manifest digest and skips multi-tag manifests (e588c03).

### Repository
- Standalone `kanibako-images` repository restructure, split out of the former
  kanibako monorepo: `containers/` (Containerfile.kanibako + tmux.conf +
  `archive/`), `host/`, and `.github/workflows/build-images.yml` (83239c5).

[Unreleased]: https://github.com/doctorjei/kanibako-images/compare/v1.4.0...HEAD
