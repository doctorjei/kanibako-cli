# Releasing kanibako

This is the operator-facing guide to the **rc-then-promote** release pipeline.
A fresh operator should be able to cut a release from this document alone.

The core property of this model: **the bits that get tested are the bits that
get shipped.** A release candidate is built, validated, and pushed as
immutable, version-scoped artifacts; the final release does not rebuild
anything — it *promotes* those exact artifacts.

---

## 1. Overview

A release is driven by **two git tags** on the **same commit**:

| Tag | Shape | What CI does |
| --- | --- | --- |
| Release candidate | `v<ver>-rc<n>` (e.g. `v1.2.5-rc1`) | Builds + validates artifacts. Pushes rc-scoped image tags `ghcr.io/doctorjei/kanibako-<variant>:<ver>-rc<n>`. Builds the 4 PyPI packages and runs `twine check` (**no upload**). Creates a **DRAFT** GitHub prerelease. **Nothing** touches `:latest`, `:edge`, or prod PyPI. |
| Release | `v<ver>` (e.g. `v1.2.5`) | Does **NOT** rebuild images. **Promotes** the rc images server-side (`docker buildx imagetools create`) to `:<ver>` + `:latest`. Publishes the 4 PyPI packages to **prod** (OIDC trusted publishing). Publishes the GitHub release and deletes the rc draft. |

The pipeline lives in `.github/workflows/release.yml`, triggered on **all
`v*` tags**, self-gated per job by tag shape:

- `rc-pypi-check`, `rc-images`, `rc-release` run when the tag name contains
  `-rc`.
- `promote` runs when the tag starts with `v` and does **not** contain `-rc`.

The standard test gates in `.github/workflows/test.yml` (`test`,
`integration`, `e2e`) also run on every `v*` tag push, independently of
`release.yml`.

---

## 2. Image tag semantics (the dual-track model)

There are three distinct image tracks. Keeping them separate is what lets the
promote step ship exactly what the rc tested.

- **`:edge`** — the moving **dev** tag. `build-images.yml` pushes it for the 4
  base variants (`min`, `oci`, `lxc`, `vm`) on every push to `main` that
  touches `src/kanibako/containers/**`. The **e2e CI job pulls `:edge`**, so
  e2e always exercises current `main`.
- **`:<ver>-rc<n>`** — the **rc** images, pushed by the rc-tag build
  (`rc-images` job). These are the artifacts that later get promoted.
- **`:<ver>` + `:latest`** — set **only** by the release-tag `promote` step.
  `:latest` therefore equals the **last promoted release**, *not* the tip of
  `main`.

So: `main` moves `:edge`; a release moves `:latest`. They are deliberately
decoupled.

**Templates** (one per `src/kanibako/containers/Containerfile.template-*`;
currently `jvm`, `systems`, `android`, `dotnet`, `js`) are **not published** to
any registry. They are built **locally** on the user's host via
`kanibako rig create <name> --template <name>`. `build-images.yml` on `main`
pushes runs a dynamic `discover-templates` → `build-templates` matrix that
**build-verifies** each template (building it and running its toolchain smoke
checks, declared via `# kanibako-template-check:` headers) but does **not** push
the resulting images. Adding a `Containerfile.template-*` file extends this
verification with no workflow edit. Since they are never published, templates
are **not a release artifact** and have no part in the promote step.

The four base variants and their droste base images (from `release.yml` /
`build-images.yml`):

| Variant | Base image |
| --- | --- |
| `min` | `ghcr.io/doctorjei/droste-seed:1.1.0` |
| `oci` | `ghcr.io/doctorjei/droste-fiber:1.1.0` |
| `lxc` | `ghcr.io/doctorjei/droste-thread:1.1.0` |
| `vm`  | `ghcr.io/doctorjei/droste-hair:1.1.0` |

---

## 3. The PyPI packages

Four packages are built and published on a release. They share one version,
bumped together by `.bumpversion.cfg`:

| Package | Source dir |
| --- | --- |
| `kanibako-cli` | `.` (repo root) |
| `kanibako-agent-claude` | `packages/agent-claude` |
| `kanibako` (meta) | `packages/meta` |

`kanibako-agent-goose` is **excluded** — it versions independently (`0.1.0`),
is off-PyPI, and has no image. It is not part of this pipeline.

Both `scripts/build-all.sh` and the `release.yml` build steps use exactly this
package list.

---

## 4. Step-by-step release procedure

Example throughout: cutting **v1.2.5** from a `v1.2.5-rc1` candidate.

### 4.0 Prerequisites

- A clean `main` (no modified tracked files; untracked files are fine).
- The **one-time** PyPI Trusted Publisher config from
  [section 7](#7-prerequisites-one-time-operatoradmin) must already be in
  place, or the promote PyPI publish will fail.

### 4.1 Mint the release candidate

On a clean `main`:

```bash
scripts/release-rc.sh <patch|minor|major> [--rc N]
# e.g.
scripts/release-rc.sh patch          # -> bumps to 1.2.5, tags v1.2.5-rc1
```

This helper:

1. Verifies the working tree has no modified tracked files.
2. Runs `bump2version --no-tag <part>`, which bumps every version file in
   `.bumpversion.cfg` and makes **one commit** titled `Release v<ver>` (it does
   **not** auto-tag in this mode).
3. Reads the new version from `.bumpversion.cfg` and creates the
   `v<ver>-rc<N>` git tag on that commit.
4. Prints the exact push commands and a reminder about rc discipline.

It does **not** push anything — you control when tags reach origin.

Notes:

- `--rc N` sets the rc number (default `1`). Use `--rc 2`, `--rc 3`, … when a
  candidate fails and you cut a fresh one.
- `--version X.Y.Z` sets the version explicitly (a part is still required and
  defaults to `patch`).
- `bump2version` is not on `PATH` in this environment — it lives in
  `~/.venv/bin/`. The helper locates it automatically (`command -v
  bump2version`/`bumpversion`, then `~/.venv/bin/bump2version`).
- `--dry-run` prints every command it would run without executing anything.

### 4.2 Push the branch + rc tag

The helper prints these; run them:

```bash
git push origin main && git push origin v1.2.5-rc1
```

### 4.3 Wait for GREEN CI on the rc tag

This is the discipline point — **stop and wait here.** Both workflows must go
green on the rc tag:

- `test.yml` — `test` + `integration` + `e2e`.
- `release.yml` — the rc jobs: `rc-pypi-check`, `rc-images`, `rc-release`
  (build + push rc images, `twine check`, draft prerelease).

Then **review the draft GitHub release notes** for `v1.2.5-rc1`.

### 4.4 Promote (only after green)

With `HEAD` still at the rc commit:

```bash
scripts/release-rc.sh --promote 1.2.5   # tags v1.2.5 on the SAME commit (no bump)
git push origin v1.2.5
```

`--promote` performs **no version bump**. It just tags `v<ver>` on the current
`HEAD`, which must be the rc commit.

### 4.5 The promote job runs

Pushing `v1.2.5` triggers `release.yml`'s `promote` job, which:

1. Validates the tag shape (`v<MAJOR>.<MINOR>.<PATCH>`, no `-rc`).
2. **Finds the matching rc tag** (`v1.2.5-rc*`, highest by version sort) and
   **hard-fails if none exists**.
3. Verifies the rc images exist (`docker buildx imagetools inspect` for each of
   the 4 variants) and **fails fast** if any is missing.
4. **Promotes** rc → `:<ver>` + `:latest` via `docker buildx imagetools create`
   (a bit-identical, server-side multi-tag copy — no pull/push round-trip, no
   templates).
5. Builds the 4 PyPI packages and **publishes to prod PyPI** via OIDC trusted
   publishing (`pypa/gh-action-pypi-publish`, `environment: pypi`, no token).
6. Publishes the GitHub release for `v1.2.5` and **deletes the rc draft**.

### 4.6 Verify + broadcast

- PyPI shows `1.2.5` for all four packages.
- `ghcr.io/doctorjei/kanibako-oci:1.2.5` and
  `ghcr.io/doctorjei/kanibako-oci:latest` resolve (e.g.
  `docker buildx imagetools inspect ...`).
- Then **broadcast** to the cluster (droste / gemet) per project convention.

---

## 5. Development builds

Alongside the official tag-driven flow, `release.yml` exposes an **on-demand
development channel** for publishing `.dev` pre-releases of the three PyPI
packages — useful for smoke-testing a build (or a new Trusted Publisher) before
cutting a final release.

- **Trigger:** GitHub → **Actions** → **Release** → **Run workflow**
  (`workflow_dispatch`). This is decoupled from any git tag.
- **`publish` input** (boolean, default `false`):
  - `false` — build all three packages and run `twine check` only; **nothing**
    is uploaded.
  - `true` — additionally upload to **prod PyPI** via the same OIDC Trusted
    Publisher used by `promote` (`environment: pypi`).
- **Versioning:** the build is stamped `X.Y.Z.dev<run_number>`, where `X.Y.Z`
  is the repo version (`pyproject.toml`, pre-release suffix stripped) and
  `<run_number>` is the workflow run number — monotonic, so each upload is
  unique on PyPI. The stamp is ephemeral (no commit, no tag); `main` keeps its
  plain `X.Y.Z` baseline.
- **Pre-release semantics:** per PEP 440, `X.Y.Z.devN < X.Y.Z`, so dev builds
  are **not** installed by default. Install one explicitly with `--pre`:

  ```bash
  pip install --pre kanibako-cli      # or: pip install --pre kanibako
  ```

The official rc→promote flow ([section 4](#4-step-by-step-release-procedure))
is unaffected by this channel.

---

## 6. The wait-for-green / rc-discipline rule

**NEVER push the rc tag and the release tag back-to-back.**

The entire point of this pipeline is to let the rc go **green first**, so that
the promoted artifacts are exactly the validated ones. The `release-rc.sh`
helper never pushes for you precisely so you stay in control of timing.

The pipeline enforces this from the server side too:

- `promote` **hard-fails** if no matching `v<ver>-rc*` tag exists.
- `promote` **fails fast** if the rc images for any variant are missing.

So a stray `v<ver>` tag with no green rc behind it cannot ship.

---

## 7. Prerequisites (one-time, operator/admin)

### PyPI Trusted Publishers

Each of the **three** PyPI projects must have a Trusted Publisher configured so
the `promote` job can publish via OIDC:

- `kanibako-cli`
- `kanibako-agent-claude`
- `kanibako`

For **each** project, add a Trusted Publisher with:

| Field | Value |
| --- | --- |
| Owner | `doctorjei` |
| Repository | `kanibako` |
| Workflow | `release.yml` |
| Environment | `pypi` |

This is **additive** to any existing `test.yml` publisher — keep both. Without
the `release.yml` publisher, the promote PyPI publish fails with
`invalid-publisher`.

This must be done manually in the **PyPI web UI**; it cannot be automated.

### GHCR

GHCR publishing (rc image push + promote `imagetools` copy) uses the workflow's
built-in `GITHUB_TOKEN`. **No extra secret is required.**

---

## 8. Deferred / out of scope

- **Templates** — bundled templates (one per `Containerfile.template-*`) are
  built locally and CI-verified (build + toolchain smoke checks) by
  `build-images.yml`; they are **not published** to any registry and so are not
  a release artifact. Nothing template-related is part of the promote step.
- **Tag-immutability rulesets** — making release tags (`v<ver>`) immutable
  while rc tags (`v<ver>-rc<n>`) stay mutable is a separate repo-settings
  follow-up, not yet applied.
- **`kanibako-agent-goose`** — versions independently (`0.1.0`), off-PyPI, no
  image; excluded from this pipeline entirely.

---

*Contributions to this document and the release tooling are made with AI
assistance; see the project AI policy. Contact: `<kirobo at bmail dot club>`.*
