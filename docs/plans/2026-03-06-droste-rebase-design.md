# Droste Rebase: Image Architecture Redesign

**Date**: 2026-03-06
**Status**: Approved

## Summary

Replace kanibako's custom image tree (base → host → host-claude, plus
jvm/android/ndk/dotnet/systems/behemoth) with four thin images built on
droste tiers, plus a user-driven template system for dev toolchains.

## Motivation

- Current images duplicate work already done by droste (OS, packages,
  container tooling).
- The base/host split is artificial — any image with podman can serve
  both roles.
- Pre-built dev variant images (jvm, systems, behemoth) are a
  maintenance burden and a poor fit for diverse user needs.

## New Image Architecture

Four images, each a thin kanibako layer on a droste base:

| Image | Droste Base | Format | Role |
|-------|-------------|--------|------|
| `kanibako-min` | `droste-seed` | OCI process | Lightweight agent container |
| `kanibako-oci` | `droste-fiber` | OCI process | Agent container + nested OCI host |
| `kanibako-lxc` | `droste-thread` | LXC system | System container host (kento) |
| `kanibako-vm` | `droste-hair` | VM | VM host (kento + tenkei) |

### The kanibako layer

Identical across all four images:

1. **User setup** — configurable via `ARG AGENT_USER=agent`. Renames the
   droste user (UID 1000) or creates `agent` with passwordless sudo.
2. **Extra packages** — `gh` (GitHub CLI) and `ripgrep`. These are the
   only packages not in droste seed/fiber.
3. **Directory scaffolding** — `workspace/`, `.kanibako/`, `.local/bin/`,
   `share-ro/`, `share-rw/`.
4. **PATH** — `/home/$AGENT_USER/.local/bin` prepended.
5. **Entrypoint** — `/bin/bash` (agent binary bind-mounted at runtime).

### What's removed

- `Containerfile.base` (ubuntu:devel) → replaced by droste bases
- `Containerfile.host` / `Containerfile.host-claude` → collapsed into
  `kanibako-oci` / `-lxc` / `-vm` (fiber already has podman)
- `Containerfile.jvm`, `.android`, `.ndk`, `.dotnet`, `.systems`,
  `.behemoth` → replaced by template system
- Plugin image layers → plugins are host-side only, never needed in
  agent containers

### kanibako-oci as dual-role image

`kanibako-oci` (based on droste-fiber) serves double duty:
- **Agent container**: run an AI agent directly inside it
- **Nested OCI host**: run kanibako inside it to manage child containers

Fiber already includes podman, fuse-overlayfs, slirp4netns, and uidmap
with rootless config — the same packages that the old `Containerfile.host`
added manually.

## Template System

Replace pre-built dev variant images with user-created templates.

### Workflow

1. **Create**: `kanibako template create jvm --base kanibako-oci`
   - Starts an interactive container from the chosen base image
   - User installs whatever tools they need

2. **Save**: `kanibako template save` (or automatic on exit)
   - Runs `podman commit` to save the container as a local OCI image
   - Image named `kanibako-template-<name>` (e.g. `kanibako-template-jvm`)

3. **Use**: `kanibako init --template jvm`
   - Creates new project using the template image
   - Template layers shared read-only across all projects using it

4. **Share**: Standard OCI image — push to any registry
   ```
   podman push kanibako-template-jvm ghcr.io/myorg/kanibako-template-jvm
   ```

5. **Manage**: `kanibako template list`, `kanibako template delete jvm`

### Storage efficiency

All containers from the same template share image layers
(content-addressable, deduplicated). Only per-container runtime changes
consume additional disk.

### Official templates

We can publish common templates (jvm, systems) to GHCR as convenience
images, built the same way users build theirs — not a separate CI
pipeline.

## Plugin Architecture (unchanged)

Plugins remain host-side only. Every method in the `Target` ABC
(`detect()`, `binary_mounts()`, `init_home()`, `refresh_credentials()`,
`writeback_credentials()`, `build_cli_args()`, etc.) executes on the host,
never inside the container. No plugin code needs to be in agent images.

Plugins are installed wherever kanibako runs (bare metal host, or inside
a host container like `kanibako-oci`).

## CI/CD Changes

### Old pipeline

```
build-images.yml:     base → systems, jvm → android → ndk, dotnet, behemoth (7 images)
build-host-image.yml: base → host → host-claude (3 images, overlaps base)
```

### New pipeline

```
build-images.yml: kanibako-min, kanibako-oci, kanibako-lxc, kanibako-vm (4 images)
```

Each image uses `ARG BASE_IMAGE=ghcr.io/doctorjei/droste-<tier>:latest`
so CI pulls from droste's GHCR, adds the kanibako layer, and pushes to
`ghcr.io/doctorjei/kanibako-<variant>:latest`.

The host-image workflow is eliminated (merged into the single workflow).

## Config Changes

- Default `container_image` changes from `ghcr.io/doctorjei/kanibako-base:latest`
  to `ghcr.io/doctorjei/kanibako-oci:latest`.
- `_IMAGE_CONTAINERFILE_MAP` updated for new image names.
- `_VARIANT_DESCRIPTIONS` and `_KNOWN_SUFFIXES` updated.
- `kanibako image list` shows new variants + local templates.

## Migration Path

- Old images continue to work — users who reference `kanibako-base` by
  name can keep using existing pulled images until they update.
- `kanibako setup` / first run after upgrade pulls the new default image.
- Old Containerfiles kept in `archive/` for reference.
- No data migration needed — container images are independent of project
  data.

## Files Affected

### New files
- `src/kanibako/containers/Containerfile.min` — kanibako layer on droste-seed
- `src/kanibako/containers/Containerfile.oci` — kanibako layer on droste-fiber
- `src/kanibako/containers/Containerfile.lxc` — kanibako layer on droste-thread
- `src/kanibako/containers/Containerfile.vm` — kanibako layer on droste-hair
- `src/kanibako/commands/template_cmd.py` — template create/save/list/delete CLI
- `src/kanibako/templates_image.py` — template image management logic
- `tests/test_commands/test_template_cmd.py` — template CLI tests
- `tests/test_templates_image.py` — template logic tests

### Modified files
- `src/kanibako/container.py` — update `_IMAGE_CONTAINERFILE_MAP`
- `src/kanibako/commands/image.py` — update variants, descriptions, suffixes
- `src/kanibako/config.py` — update default `container_image`
- `src/kanibako/cli.py` — register template command
- `.github/workflows/build-images.yml` — replace with 4-image pipeline
- `.github/workflows/build-host-image.yml` — delete (merged)

### Removed files (move to archive/)
- `src/kanibako/containers/Containerfile.base`
- `src/kanibako/containers/Containerfile.jvm`
- `src/kanibako/containers/Containerfile.android`
- `src/kanibako/containers/Containerfile.ndk`
- `src/kanibako/containers/Containerfile.dotnet`
- `src/kanibako/containers/Containerfile.systems`
- `src/kanibako/containers/Containerfile.behemoth`
- `host-definitions/Containerfile.host`
- `host-definitions/Containerfile.host-claude`
