# kanibako images

Container base images for [kanibako](../README.md), the sandboxed-agent CLI.
This directory holds the sources for the four `kanibako-*` base images on GHCR;
they are built and released from this repo, by the same tag that releases the
CLI to PyPI.

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

Each image bundles the `kanibako-cli` wheel built from the same commit, handed
to the Containerfile through the named build context `cliwheel`, so an image
never waits on a PyPI release. The image's `org.opencontainers.image.version`
label reports that wheel's version and `org.opencontainers.image.revision`
reports the commit. The default `/etc/tmux.conf` is the CLI's own
`src/kanibako/containers/tmux.conf`, handed in as the named build context
`clicontainers`.

## Building locally

From the repo root:

```sh
python -m build --wheel --outdir dist/cliwheel .
podman build -f images/containers/Containerfile.kanibako \
  --build-context cliwheel=dist/cliwheel \
  --build-context clicontainers=src/kanibako/containers \
  --build-arg VARIANT=oci \
  --build-arg BASE_IMAGE=ghcr.io/doctorjei/droste-fiber:1.1.0 \
  -t kanibako-oci:dev \
  images/containers/
```

The wheel directory the first command writes must hold exactly one
`kanibako_cli-*.whl`, so empty it before rebuilding. Swap
`VARIANT`/`BASE_IMAGE` per the table above for the other variants.

## Release process

The images release with the CLI, in its rc-then-promote flow; the runbook is
[`docs/RELEASING.md`](../docs/RELEASING.md). In short:

1. The rc tag `vX.Y.Z-rcN` builds all four variants from the tagged tree and
   publishes `kanibako-<variant>:X.Y.Z-rcN`, refusing to overwrite an existing
   rc tag, then advances `:edge` if it is not behind `:latest`.
2. The final tag `vX.Y.Z`, on the same commit, first verifies that the four rc
   images exist and were built from that commit. Only then does the PyPI
   publish run, and only after it succeeds are the rc manifests copied **by
   digest** to `:X.Y.Z`, `:latest` and `:edge`. There is no rebuild, so the
   published images are byte-identical to the rc.

### Workflows (repo root, `.github/workflows/`)

- `images.yml` builds the images. A push to `main` or a pull request that
  touches `images/` builds all four without publishing; `release.yml` calls it
  for the rc, verify and promote steps. A manual dispatch builds, and with
  `publish=true` pushes `:<version>-dev.<sha7>`, never a release tag.
- `images-prune-tags.yml` is a manual, dry-run-default GHCR tag cleanup. It
  deletes by manifest digest, skips multi-tag manifests and refuses `:edge` and
  `:latest`. Never point it at a promoted `-rcN` tag; its header explains why.

The version is the CLI's (`pyproject.toml`); see [`CHANGELOG.md`](CHANGELOG.md)
for the images' history before they rejoined this repo.

## Layout

- `containers/Containerfile.kanibako`: the four base variants, selected by
  `VARIANT` and `BASE_IMAGE`.
