# kanibako-images

Container base images for [kanibako](https://github.com/doctorjei/kanibako-cli),
the sandboxed-agent CLI. This repo builds and publishes the four `kanibako-*`
base images to GHCR; the CLI itself lives in the separate `kanibako-cli` repo.

## Variants

Each variant is built from the single, `VARIANT`-conditional
`containers/Containerfile.kanibako` over a matching [droste](https://github.com/doctorjei/droste)
base tier:

| Variant | GHCR package      | droste base                          | Use                       |
| ------- | ----------------- | ------------------------------------ | ------------------------- |
| `min`   | `kanibako-min`    | `ghcr.io/doctorjei/droste-seed:1.1.0`   | lean OCI container        |
| `oci`   | `kanibako-oci`    | `ghcr.io/doctorjei/droste-fiber:1.1.0`  | full-featured OCI container |
| `lxc`   | `kanibako-lxc`    | `ghcr.io/doctorjei/droste-thread:1.1.0` | systemd LXC (rootless podman inside) |
| `vm`    | `kanibako-vm`     | `ghcr.io/doctorjei/droste-hair:1.1.0`   | systemd VM                |

The shared (all-variant) package layer installs the kanibako **baseline** tool
set — `tmux inotify-tools ripgrep fd-find openssh-client` — whose list is derived
at build time from `kanibako baseline list`, so the images and the CLI never
drift. `nodejs`/`npm` and `cifs-utils`/`nfs-common` are explicit conveniences
(not part of the baseline contract); `sshpass` is min-only.

The image bundles a pinned `kanibako-cli` release: a `vX.Y.Z` images release
ships `kanibako-cli==X.Y.Z` via the `KANIBAKO_CLI_VERSION` build-arg.

## Building locally

```sh
podman build -f containers/Containerfile.kanibako \
  --build-arg VARIANT=oci \
  --build-arg BASE_IMAGE=ghcr.io/doctorjei/droste-fiber:1.1.0 \
  --build-arg KANIBAKO_CLI_VERSION=1.5.0 \
  -t kanibako-oci:dev \
  containers/
```

Omit `KANIBAKO_CLI_VERSION` to pull the latest `kanibako-cli` from PyPI. Swap
`VARIANT`/`BASE_IMAGE` per the table above for the other variants.

## Release process

Releases follow an rc-then-promote flow (mirrors `kanibako-cli`), so the
tested bits are exactly the shipped bits:

1. Push a release-candidate tag `vX.Y.Z-rcN`. `release.yml` builds all four
   variants (each with `--build-arg KANIBAKO_CLI_VERSION=X.Y.Z`) and publishes
   `kanibako-<variant>:X.Y.Z-rcN`.
2. Wait for green CI on the rc and validate the rc images.
3. Promote: push the final `vX.Y.Z` tag (or run the `release.yml` promote
   dispatch with `promote_version` + `promote_rc`). Promote copies the rc
   manifests **by digest** to `:X.Y.Z` and `:latest` — no rebuild, so the
   published images are byte-identical to the green rc.

### Other workflows

- `build-images.yml` — manual `:latest`-only build/publish. A push to
  `main` (touching `containers/**`) builds and warms the layer cache but does
  not publish; a `workflow_dispatch` with `push=true` publishes `:latest`.
- `prune-tags.yml` — manual, dry-run-default GHCR tag cleanup (deletes by
  manifest digest; skips multi-tag manifests).

The current version line is recorded in [`VERSION`](VERSION); see
[`CHANGELOG.md`](CHANGELOG.md) for history.

## Repository layout

- `containers/` — `Containerfile.kanibako` (the four base variants) + `tmux.conf`.
- `containers/archive/` — retired experimental Containerfiles, not part of the
  `kanibako-*` base matrix (see its README).
- `host/` — dev-host images consumed elsewhere, not base variants (see its README).
- `.github/workflows/` — `release.yml`, `build-images.yml`, `prune-tags.yml`.
