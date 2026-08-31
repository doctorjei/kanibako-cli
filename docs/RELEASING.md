# Releasing kanibako

This is the operator-facing guide to the kanibako **PyPI** release pipeline.
A fresh operator should be able to cut a release from this document alone.

Two different things get released, from two different repos:

- **The Python packages** — this repo,
  [`doctorjei/kanibako-cli`](https://github.com/doctorjei/kanibako-cli), driven
  by `.github/workflows/release.yml`. That is what this document covers.
- **The container images** — the sibling
  [`doctorjei/kanibako-images`](https://github.com/doctorjei/kanibako-images)
  repo, which has its own tag-driven `release.yml`. See
  [section 6](#6-container-images-live-in-kanibako-images).

Two properties hold this pipeline together:

1. **A pre-release never happens by accident.** Pushing an rc tag uploads
   *nothing*; publishing an rc or a dev build is always an explicit manual
   workflow dispatch.
2. **A production release ships the tree as-is, and only if it is green.** The
   promote job does no version stamping and refuses to publish unless every
   required Tests job succeeded for that exact commit.

---

## 1. Overview — what triggers what

`release.yml` is triggered by **all `v*` tag pushes** and by
**`workflow_dispatch`** (inputs: `publish`, boolean, default `false`; `agent`,
string, default empty). Every job self-gates:

| Job | Fires when | What it does |
| --- | --- | --- |
| `rc-pypi-check` | **push** of an rc tag `v<ver>-rc<n>` | Validates the tag shape, builds all five packages, runs `twine check`. **No upload.** |
| `rc-release` | **push** of an rc tag (after `rc-pypi-check`) | Creates a **DRAFT** GitHub prerelease with generated notes (guarded, so a re-run reuses an existing draft). |
| `dev` | **manual dispatch**, no `agent` input | Builds a pre-release — `<X.Y.Z>rc<N>` when dispatched on an rc tag, `<base>.dev<N>` on a branch. Uploads to PyPI **only** when `publish=true`. |
| `promote` | push of a **bare** `v<ver>` tag (no `-rc`) | Waits for green Tests jobs on the tag's SHA, then builds and publishes all five packages to **prod PyPI** (OIDC) and publishes the GitHub release, deleting the rc draft. |
| `publish-agent` | **manual dispatch** with `agent=agent-goose\|agent-codex` | Builds and publishes that one agent package at its static version. |

Two consequences worth internalising:

- **An rc tag push publishes nothing to PyPI.** It twine-checks the build and
  drafts a GitHub prerelease, and that is all. Getting an rc onto PyPI is a
  separate, deliberate dispatch ([section 3.4](#34-dispatch-the-publish)).
- **The rc path is not self-gated on tests.** Only `promote` waits for green
  Tests. Before dispatching an rc publish, the releaser confirms the tag's
  Tests run by hand ([section 3.3](#33-confirm-the-tags-tests-run-is-green)).

The test gates live in `.github/workflows/test.yml` — workflow name **Tests**,
jobs `test` (ruff + mypy + unit pytest), `integration`, and `e2e`. It runs on
pushes to `main`, on pull requests, and on every `v*` tag. `e2e` is tag-gated:
it runs only on a `v*` tag or a dispatch with `run_e2e=true`, and it exercises
the **HEAD** code by pulling `ghcr.io/doctorjei/kanibako-oci:latest` and
overlaying a freshly built wheel onto it (`tests/e2e/Containerfile.head`).

---

## 2. The packages

Five distributions are built from this repo. Both `scripts/build-all.sh` and
every build step in `release.yml` use exactly this list:

| Package | Source dir | Versioning |
| --- | --- | --- |
| `kanibako-cli` | `.` (repo root) | the train (`.bumpversion.cfg`) |
| `kanibako-agent-claude` | `packages/agent-claude` | the train (`.bumpversion.cfg`) |
| `kanibako` (meta) | `packages/meta` | the train (`.bumpversion.cfg`) |
| `kanibako-agent-goose` | `packages/agent-goose` | **independent** — its own `pyproject.toml` **and** `packages/agent-goose/src/kanibako/plugins/goose/__init__.py` |
| `kanibako-agent-codex` | `packages/agent-codex` | **independent** — its own `pyproject.toml` **and** `packages/agent-codex/src/kanibako/plugins/codex/__init__.py` |

`.bumpversion.cfg` stamps five files — `pyproject.toml`,
`src/kanibako/__init__.py`, `packages/agent-claude/pyproject.toml`,
`packages/agent-claude/src/kanibako/plugins/claude/__init__.py`, and
`packages/meta/pyproject.toml`. The goose and codex packages are deliberately
**not** in it: they version independently and are published either alongside a
train release or on their own ([section 5](#5-publishing-a-single-agent-package)).

⚑ **Every distribution that ships code carries its version in TWO files** — its
`pyproject.toml` and `__version__` in its `__init__.py`. That is the cli as well
as each plugin. `.bumpversion.cfg` stamps the cli's pair and claude's and
neither goose's nor codex's, so those two are hand-edited and can drift apart —
and being stamped is not being asserted, so the stamped pairs are checked too.
What holds them together is
`tests/test_meta_pin.py::test_version_pair_agrees`, over every distribution with
a `src/` tree (`packages/meta` has none, so it has no second copy to disagree
with); it runs in CI's `pytest tests/`, and the test explains why nothing else
can catch the drift. Read a failure there as *bump the other file*, not as a
broken test.

**Meta pinning.** In-tree, `packages/meta` depends on the train as *ranges*, so
a source checkout or a dev flow never breaks. Every publish path in
`release.yml` rewrites the **stamped-train** lines — `kanibako-cli` **and**
`kanibako-agent-claude` — to exact `==<version>` pins at build time, so a
freshly published meta can never pair with a stale train member during PyPI
index propagation. ⚑ **Both, not just the cli:** leaving agent-claude a range
shipped a plugin built against a different cli once already. The contract is
asserted by `tests/test_meta_pin.py`, which records that incident. Meta's
*independently-versioned* agent dependencies — goose and codex — stay `>=`
floors, which is why the `dev` and `promote` jobs also build those two and
upload them with `skip-existing`: the versions meta's floors point at must be
resolvable on the index, but an unchanged agent version must not fail the
publish.

**Version stamping, by path:**

- `dev` (rc/dev pre-releases) — **stamps** the computed version with anchored
  `sed` into the three train `pyproject.toml`s and the two `__init__.py`s (no
  commit, no tag). goose and codex are **not** stamped; they build at their
  static `pyproject.toml` version.
- `promote` (production) — **stamps nothing**. It builds the checked-out tree
  exactly as tagged. ⚑ **The in-tree versions must already equal the tag** at
  tag time (see [section 4.0](#40-preconditions)).

**Config-compat constants.** `src/kanibako/__init__.py` also carries
`SETUP_BCV` / `SETUP_FCV`, the two-tier setup-compatibility markers. They are
independent of the release version and are bumped only when a release changes
setup: `SETUP_FCV` whenever setup adds a feature/case (older configs get a
non-blocking nudge), `SETUP_BCV` only when a config can no longer be
auto-filled (older configs get a hard error). Most releases bump neither.

---

## 3. Cutting and publishing a release candidate

Example throughout: cutting and publishing **`v1.8.0-rc1`**.

### 3.0 Prerequisites

- A clean `main` (no modified tracked files; untracked files are fine).
- The **one-time** PyPI Trusted Publisher config from
  [section 9](#9-prerequisites-one-time-operatoradmin) must already be in
  place, or every publish path fails with `invalid-publisher`.

### 3.1 Mint the release candidate

On a clean `main`:

```bash
scripts/release-rc.sh <patch|minor|major> [--rc N]
# e.g.
scripts/release-rc.sh minor          # -> bumps to 1.8.0, tags v1.8.0-rc1
```

This helper:

1. Verifies the working tree has no modified tracked files.
2. Runs `bump2version --no-tag <part>`, which bumps every version file in
   `.bumpversion.cfg` and makes **one commit** titled `Release v<ver>`.
3. Reads the new version back from `.bumpversion.cfg` and creates the
   `v<ver>-rc<N>` git tag on that commit.
4. Prints the exact push commands and a reminder about rc discipline.

It does **not** push anything — you control when tags reach origin.

Notes:

- `--rc N` sets the rc number (default `1`). Use `--rc 2`, `--rc 3`, … when a
  candidate fails and you cut a fresh one.
- `--version X.Y.Z` sets the version explicitly (a part is still required and
  defaults to `patch`).
- `--dry-run` prints every command it would run without executing anything.
- `bump2version` is not on `PATH` in this environment — it lives in
  `~/.venv/bin/`. The helper locates it automatically (`command -v
  bump2version`/`bumpversion`, then `~/.venv/bin/bump2version`).
- ⚑ **Never run a bare `bump2version <part>`.** `.bumpversion.cfg` sets
  `tag = True` with `tag_name = v{new_version}`, so a bare run creates a
  `v<ver>` tag — which is the **production** trigger. The helper always passes
  `--no-tag`; if you bump by hand, pass it yourself.

### 3.2 Push the branch + rc tag

The helper prints these; run them:

```bash
git push origin main && git push origin v1.8.0-rc1
```

This fires `rc-pypi-check` + `rc-release` (build, `twine check`, draft
prerelease) and the **Tests** workflow, including `e2e`. Nothing is uploaded.

### 3.3 Confirm the tag's Tests run is GREEN

This is the discipline point — **stop and check here.** Unlike `promote`, the
rc publish is *not* gated on tests; you are the gate.

```bash
SHA=$(git rev-parse "v1.8.0-rc1^{}")
gh api "repos/doctorjei/kanibako-cli/actions/workflows/test.yml/runs?head_sha=$SHA" \
  --jq '.workflow_runs[0] | "\(.status) \(.conclusion) \(.html_url)"'
```

⚑ **Filter by workflow, not by SHA alone.** Several workflows fire on the same
commit; a bare "what concluded at this SHA" query can report a run that is not
**Tests**. The query above (and `gh run list --workflow=test.yml --commit
$SHA`) scopes to the right workflow. To be thorough, check the individual job
conclusions — `test`, `integration`, `e2e` — the same three the promote gate
requires:

```bash
RUN=$(gh api "repos/doctorjei/kanibako-cli/actions/workflows/test.yml/runs?head_sha=$SHA" \
  --jq '.workflow_runs[0].id')
gh api "repos/doctorjei/kanibako-cli/actions/runs/$RUN/jobs?per_page=100" \
  --jq '.jobs[] | "\(.name) \(.conclusion)"'
```

Also confirm `release.yml`'s rc jobs went green and **review the draft GitHub
release notes** for `v1.8.0-rc1`.

### 3.4 Dispatch the publish

Publishing the rc to PyPI is an explicit manual dispatch **on the rc tag**:

```bash
gh workflow run release.yml --ref v1.8.0-rc1 -f publish=true
```

- The dispatch **ref** decides the version: an rc tag `v<X.Y.Z>-rc<N>` builds
  the PEP 440 pre-release `<X.Y.Z>rc<N>` — never the bare `<X.Y.Z>`, which is
  reserved for the gated prod promote.
- `kanibako-cli`, `kanibako-agent-claude` and `kanibako` (meta) are stamped to
  `<X.Y.Z>rc<N>`; `kanibako-agent-goose` and `kanibako-agent-codex` are built
  and published **unstamped**, at their own static versions, with
  `skip-existing`.
- `publish=false` (the default) builds and `twine check`s without uploading —
  useful for smoke-testing a build or a new Trusted Publisher.
- ⚑ `workflow_dispatch` runs the workflow file **as-at the dispatched ref**. An
  rc tag cut before a pipeline change carries the *old* workflow.

### 3.5 Dev builds off a branch

The same job serves an on-demand dev channel: dispatch with the ref left at a
branch instead of a tag.

```bash
gh workflow run release.yml -f publish=true          # ref defaults to main
```

The version becomes `<base>.dev<N>`, where `<base>` is the repo version from
`pyproject.toml` with any `.dev` suffix stripped and `<N>` is one past the
highest `<base>.devN` already published on PyPI, floored at 100. The number
counts published cuts, not workflow runs; if PyPI cannot be reached the job
fails rather than guessing. The stamp is ephemeral: no commit, no tag; `main`
keeps its plain `X.Y.Z` baseline.

Per PEP 440 both `X.Y.ZrcN` and `X.Y.Z.devN` sort **below** `X.Y.Z`, so neither
is installed by default. Install one explicitly:

```bash
pip install --pre kanibako            # newest pre-release
pip install kanibako==1.8.0rc1        # an exact pre-release pin
```

### 3.6 Verify

```bash
pip download --no-deps --no-cache-dir --pre -d /tmp/verify kanibako-cli==1.8.0rc1
```

Use `--no-cache-dir` when verifying a just-published version, and see the
**simple-index propagation gotcha** in
[section 6](#6-container-images-live-in-kanibako-images) before chaining
anything (like an image build) onto a fresh publish.

*Proven on `v1.8.0-rc1` (2026-08-01): tag pushed, Tests confirmed green by
hand, publish dispatched on the tag — `kanibako-cli`, `kanibako-agent-claude`
and `kanibako` went out as `1.8.0rc1` pre-releases, while
`kanibako-agent-goose` and `kanibako-agent-codex` published unstamped at
`0.3.0`.*

---

## 4. Promoting to production

### 4.0 Preconditions

- ⚑ **The in-tree versions must already equal the release version.** The
  `promote` job stamps nothing; it publishes the tree as tagged. Coming out of
  the rc flow this is automatic — `release-rc.sh` bumped the tree to `X.Y.Z`
  before tagging `vX.Y.Z-rc1`, and you promote the same commit.
- The rc has been published and soaked to your satisfaction.
- The promote commit must have a **green Tests run**; the job re-checks and
  will refuse otherwise.

### 4.1 Tag and push

With `HEAD` still at the rc commit:

```bash
scripts/release-rc.sh --promote 1.8.0   # tags v1.8.0 on the SAME commit (no bump)
git push origin v1.8.0
```

`--promote` performs **no version bump**. It just tags `v<ver>` on the current
`HEAD`, which must be the rc commit.

### 4.2 What the promote job does

Pushing the bare `v1.8.0` tag triggers `release.yml`'s `promote` job
(`environment: pypi`), which:

1. **Gates on GREEN Tests for this exact commit.** It polls the **Tests**
   workflow run for the tag's SHA (30s interval, 45-minute deadline) until it
   completes, then requires `test`, `integration` **and** `e2e` to each report
   `conclusion == success`. A missing, skipped or renamed required job counts
   as a failure — the gate is fail-safe by design, because both workflows fire
   independently on the tag and a red Tests job would otherwise not block a
   prod publish.
2. Validates the tag shape (`v<MAJOR>.<MINOR>.<PATCH>`, no `-rc`) and derives
   `VER`.
3. Pins `packages/meta`'s **stamped-train** dependencies — `kanibako-cli` and
   `kanibako-agent-claude` — to `==$VER` at build time
   ([section 2](#2-the-packages)). The goose and codex floors are left alone.
4. Builds all five packages from the tagged tree.
5. **Refuses a version collision with different content**
   (`scripts/check-publish-collisions.py`), comparing **every** built wheel
   against what PyPI already serves. It bites hardest on the
   independently-versioned agents: they rebuild at their static version, and
   `skip-existing` below would drop that upload in silence — leaving PyPI
   serving the old wheel under a release claiming the new files.
   **Every path in this repo that uploads to PyPI runs this guard first** — the
   `dev` and `publish-agent` jobs, and the manual `scripts/build-all.sh
   --upload`, which is not part of this procedure but reaches the same index.
   `tests/test_publish_guard.py` asserts that rule rather than a list of paths,
   deriving the paths from the upload mechanisms themselves.
   ⚑ **Wheels only.** A content change confined to an sdist passes the guard;
   the run says so whenever sdists are present, and the script's own docstring
   explains why comparing them would refuse honest releases.
6. **Publishes to prod PyPI** via OIDC trusted publishing
   (`pypa/gh-action-pypi-publish`, `skip-existing: true`, no token) — so the
   independently-versioned agents are skipped rather than failing when their
   version is unchanged.
7. Publishes the GitHub release with generated notes and **deletes** any
   matching `v<ver>-rc*` draft prereleases.

### 4.3 Verify + broadcast

- Prod PyPI shows `<ver>` for `kanibako-cli`, `kanibako-agent-claude` and
  `kanibako`, and the agent packages at their own versions:

  ```bash
  pip download --no-deps --no-cache-dir -d /tmp/verify kanibako==1.8.0
  ```

- The GitHub release for `v<ver>` is published and the rc draft is gone.
- If the release needs new images, hand off to `kanibako-images`
  ([section 6](#6-container-images-live-in-kanibako-images)).
- Then broadcast per project convention.

---

## 5. Publishing a single agent package

`kanibako-agent-goose` and `kanibako-agent-codex` version independently of the
train, so they have their own on-demand path:

```bash
gh workflow run release.yml --ref main -f agent=agent-codex
```

The `publish-agent` job validates the input (only `agent-goose` and
`agent-codex` are accepted), builds **only** that package into `dist/`, refuses
a version collision with different content
(`scripts/check-publish-collisions.py`, the same guard `promote` runs), and
publishes it to prod PyPI at its static `pyproject.toml` version with
`skip-existing`. There is no `publish` toggle on this path and no version
stamping, so the bump is yours to make in a commit first — and it is **three
edits, not one**: the package's `pyproject.toml`, `__version__` in its
`__init__.py` (`packages/agent-<name>/src/kanibako/plugins/<name>/__init__.py`),
and the matching `>=` floor in `packages/meta/pyproject.toml`. Nothing *stamps*
the second one ([section 2](#2-the-packages)); missing it publishes a wheel that
misreports its own version, which is why the test named there gates it — so run
the suite before you tag, not after you publish.

⚑ **The guard covers only one of the two forgettings.** Forgetting the bump
*entirely* — changed files, unchanged version — the job refuses, rather than
letting `skip-existing` drop the upload without a word. Bumping
`pyproject.toml` but not `__version__` it cannot: that is a version PyPI has
never seen, so the comparison short-circuits before it looks at any content, and
the test above is the only thing between you and a wheel that misreports itself.

**Ordering across the base/plugin contract.** Meta's agent dependencies are
`>=` floors, so an agent version that meta requires must be on the index **no
later than** the meta that points at it — which is exactly why the `dev` and
`promote` jobs build goose and codex alongside the train and upload them with
`skip-existing`. When a plugin bump has to go out ahead of, or between, train
releases, publish the plugin through this path first and let the base train
follow. The converse constraint also holds: a plugin change that depends on
base behaviour which only exists on `main` is not usable until the **base** is
released too — "it's on main" is not "it's shipped".

---

## 6. Container images live in `kanibako-images`

Image building left this repo at the 2026-06-12 split. `release.yml` here
publishes **PyPI packages only**; there is no image job, no GHCR push, and the
promote step has no image-promotion phase.

The four base variants (`min`, `oci`, `lxc`, `vm`) are built and published from
[`doctorjei/kanibako-images`](https://github.com/doctorjei/kanibako-images),
which mirrors this rc-then-promote shape in its own `release.yml`: an rc tag
(or a dispatch supplying `version` + `rc`) builds all four variants and
publishes `:<ver>-rc<n>` plus a guarded `:edge` advance, and the promote path
**digest-copies** those exact manifests to `:<ver>`, `:latest` and `:edge` with
no rebuild. Consult that repo for the authoritative procedure.

Two couplings matter to a cli releaser:

- **The image build pins the cli release.** Each variant is built with
  `--build-arg KANIBAKO_CLI_VERSION=<ver>` where `<ver>` is the bare version,
  so **`kanibako-cli <ver>` must already be published on PyPI** before the
  image rc build runs. The cli promote comes first; images follow.
- ⚑ **PyPI simple-index propagation race.** Immediately after a publish, an
  image build's `pip install kanibako-cli==<ver>` can still fail with "No
  matching distribution" — pip's simple index
  (`https://pypi.org/simple/kanibako-cli/`) is a separate cache from the JSON
  API, so the JSON API showing the version is **not** sufficient. Gate the
  image build on the simple index instead:

  ```bash
  curl -s https://pypi.org/simple/kanibako-cli/ | grep 1.8.0-
  ```

- The `e2e` job in `test.yml` consumes a published image
  (`ghcr.io/doctorjei/kanibako-oci:latest`) as the base it overlays HEAD onto,
  so images and cli are coupled in the test direction too.

---

## 7. Bundled templates

The template Containerfiles (`src/kanibako/containers/Containerfile.template-*`
— currently `android`, `dotnet`, `js`, `jvm`, `systems`) still ship inside the
`kanibako-cli` package. They are **not published** to any registry: they are
built locally on the user's host via `kanibako rig prep <name>`.

`.github/workflows/template-verify.yml` build-verifies them on changes to the
template Containerfiles: a `discover` job globs the directory into a matrix (so
adding a `Containerfile.template-*` extends the verification with no workflow
edit), and each matrix job builds the template and runs the smoke checks that
the file declares in its leading `# kanibako-template-check:` headers. A
template with zero declared checks is a hard error.

Since they are never published, templates are **not a release artifact** and
have no part in any publish or promote step.

---

## 8. Discipline rules

- **Never push the rc tag and the release tag back-to-back.** The point of the
  rc is to be published, soaked and confirmed green *before* the same commit is
  promoted. `release-rc.sh` never pushes for you precisely so you control the
  timing.
- **Confirm Tests green before dispatching an rc publish.** The rc path has no
  server-side gate; only the prod promote does.
- **Never run a bare `bump2version`** — `tag = True` in `.bumpversion.cfg`
  means it creates a `v<ver>` tag, which *is* the production trigger. Use
  `release-rc.sh`, or pass `--no-tag` yourself.
- **Dispatch `release.yml` on a branch (dev) or an rc tag (rc) only.** A
  dispatch aimed at a bare `v<ver>` tag can no longer reach the production
  promote: that job requires a `push` event as well as the tag shape, so
  **pushing the tag is the only way to promote.** It used to gate on the ref
  shape alone, and a dispatch on a non-rc `v` tag ran the production promote.
  ⚑ The `dev` job still accepts such a dispatch and would stamp a version from
  that ref; dispatch on a branch.
- **A promote publishes the tree, not the tag's intent.** If the in-tree
  versions do not already equal the tag, the promote publishes the wrong
  version numbers. Check before tagging.

---

## 9. Prerequisites (one-time, operator/admin)

### PyPI Trusted Publishers

Every package that `release.yml` uploads needs a Trusted Publisher on PyPI so
the OIDC publish works — all five:

- `kanibako-cli`
- `kanibako-agent-claude`
- `kanibako`
- `kanibako-agent-goose`
- `kanibako-agent-codex`

For **each** project, configure:

| Field | Value |
| --- | --- |
| Owner | `doctorjei` |
| Repository | `kanibako-cli` |
| Workflow | `release.yml` |
| Environment | `pypi` |

⚑ The repository field is **`kanibako-cli`** — the repo was renamed at the
2026-06-12 split. Without a matching publisher, the upload fails with
`invalid-publisher`.

This must be done manually in the **PyPI web UI**; it cannot be automated.

### GitHub

The draft-prerelease and release-publishing steps use the workflow's built-in
`GITHUB_TOKEN` (`permissions: contents: write`, `id-token: write`). **No extra
secret is required** in this repo. GHCR credentials are a `kanibako-images`
concern.

---

## 10. Known gaps

- **(fixed 2026-08-01)** `template-verify.yml` used to build its base from
  `src/kanibako/containers/Containerfile.kanibako`, which no longer exists in
  this repo (only the `Containerfile.template-*` files remain; the base
  Containerfile moved to `kanibako-images` at the 2026-06-12 split), so the
  workflow was broken — and masked, because its path triggers pointed at the
  moved file. It now builds each template directly FROM the published
  `ghcr.io/doctorjei/kanibako-oci:latest` (the same ref the `e2e` job in
  `test.yml` consumes), and its triggers watch only the template Containerfiles
  that still live here. Verification is no longer hermetic against a
  locally-built base; it depends on the published image, like the e2e job.
- **The bundled template Containerfiles still ship inside the cli package**
  (`pyproject.toml` package-data `"kanibako.containers" = ["Containerfile.*",
  ...]`) even though image building moved to `kanibako-images`. Nothing in the
  release pipeline depends on that; it is noted here so a releaser is not
  surprised to find Containerfiles in a PyPI artifact.

---

*Contributions to this document and the release tooling are made with AI
assistance; see the project AI policy. Contact: `<kirobo at bmail dot club>`.*
