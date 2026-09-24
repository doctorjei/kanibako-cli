# Releasing kanibako

This is the operator-facing guide to the kanibako release pipeline.
A fresh operator should be able to cut a release from this document alone.

Two different things get released, both from this repo,
[`doctorjei/kanibako-cli`](https://github.com/doctorjei/kanibako-cli), and by
the same tag:

- **The Python packages**, to PyPI, driven by `.github/workflows/release.yml`.
- **The container images** (`kanibako-{min,oci,lxc,vm}`), to GHCR, from the
  sources in `images/`. `release.yml` drives them too, through the reusable
  `.github/workflows/images.yml`. See
  [section 6](#6-container-images-release-with-the-cli).

Two properties hold this pipeline together:

1. **A PyPI pre-release never happens by accident.** Pushing an rc tag uploads
   *nothing* to PyPI; publishing an rc or a dev build there is always an
   explicit manual workflow dispatch. (The rc tag does publish the rc
   *images*, `:<ver>-rc<n>`, to GHCR; nothing pulls those unless asked to.)
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
| `images-rc` | **push** of an rc tag (after `rc-pypi-check`) | Builds the four images from the tagged tree's wheel and publishes `:<ver>-rc<n>` to GHCR, refusing to overwrite an existing rc tag; advances `:edge` unless it would fall behind `:latest`. |
| `images-verify` | push of a **bare** `v<ver>` tag | Requires all four rc images for this commit to exist and to carry its revision label. Writes nothing. |
| `promote` | push of a **bare** `v<ver>` tag (no `-rc`), after `images-verify` | Waits for green Tests jobs on the tag's SHA, then builds and publishes all five packages to **prod PyPI** (OIDC) and publishes the GitHub release, deleting the rc draft. |
| `images-promote` | push of a **bare** `v<ver>` tag, after `promote` | Copies the verified rc images **by digest** to `:<ver>`, `:latest` and `:edge`. No rebuild. |
| `publish-agent` | **manual dispatch** with `agent=agent-goose\|agent-codex` | Builds and publishes that one agent package at its static version. |

The three `images-*` jobs call `.github/workflows/images.yml`, and every one of
them requires a `push` event, so no dispatch of `release.yml` reaches an image
job ([section 6](#6-container-images-release-with-the-cli)).

Two consequences worth internalising:

- **An rc tag push publishes nothing to PyPI.** It twine-checks the build and
  drafts a GitHub prerelease, and that is all. Getting an rc onto PyPI is a
  separate, deliberate dispatch ([section 3.4](#34-dispatch-the-publish)).
- **The rc path is not self-gated on tests.** Only `promote` waits for green
  Tests. Before dispatching an rc publish, the releaser confirms the tag's
  Tests run by hand ([section 3.3](#33-confirm-the-tags-tests-run-is-green)).

The test gates live in `.github/workflows/test.yml` — workflow name **Tests**,
jobs `test` (ruff + mypy + unit pytest), `conformance` (kinemata),
`integration`, and `e2e`. It runs on
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
prerelease), `images-rc` (publishes the `:1.8.0-rc1` images to GHCR) and the
**Tests** workflow, including `e2e`. Nothing is uploaded to PyPI.

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
conclusions — `test`, `conformance`, `integration`, `e2e` — the same four the
promote gate requires:

```bash
RUN=$(gh api "repos/doctorjei/kanibako-cli/actions/workflows/test.yml/runs?head_sha=$SHA" \
  --jq '.workflow_runs[0].id')
gh api "repos/doctorjei/kanibako-cli/actions/runs/$RUN/jobs?per_page=100" \
  --jq '.jobs[] | "\(.name) \(.conclusion)"'
```

Also confirm `release.yml`'s rc jobs went green, `images-rc` included, and
**review the draft GitHub release notes** for `v1.8.0-rc1`. Pull and
smoke-test at least one rc image (`ghcr.io/doctorjei/kanibako-oci:1.8.0-rc1`).
A broken rc image is fixed by cutting the next rc; the rc tag is never
rewritten.

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

Use `--no-cache-dir` when verifying a just-published version.

⚑ **PyPI simple-index propagation.** Immediately after a publish,
`pip install kanibako-cli==<ver>` can still fail with "No matching
distribution": pip's simple index (`https://pypi.org/simple/kanibako-cli/`) is
a separate cache from the JSON API, so the JSON API showing the version is
**not** sufficient. Before chaining anything onto a fresh publish, check the
simple index:

```bash
curl -s https://pypi.org/simple/kanibako-cli/ | grep 1.8.0rc1
```

The images are not exposed to this: they bundle the tree's own wheel, never a
PyPI download.

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
- The rc has been published and soaked to your satisfaction, and its images
  (`:<ver>-rc<n>`, all four variants) were published from this same commit.
  `images-verify` finds them through the rc tag on the commit, so the final tag
  must sit on the rc commit.
- The promote commit must have a **green Tests run**; the job re-checks and
  will refuse otherwise.

### 4.1 Tag and push

With `HEAD` still at the rc commit:

```bash
scripts/release-rc.sh --promote 1.8.0   # tags v1.8.0 on the SAME commit (no bump)
git push origin v1.8.0
```

`--promote` performs **no version bump**. It just tags `v<ver>` on the current
`HEAD`, and refuses unless a `v<ver>-rc<n>` tag already points at `HEAD`.

If a final tag was pushed and its rc images are missing, tag the next rc on the
**same** commit, push it, and let `images-rc` publish; then use "Re-run failed
jobs" on the final tag's run (`rc-check` re-reads the tags on the commit).

### 4.2 What the promote job does

Pushing the bare `v1.8.0` tag first runs `images-verify`: the newest
`v1.8.0-rc<n>` tag on the same commit names the source rc, and all four
`:1.8.0-rc<n>` images must exist with that commit as their
`org.opencontainers.image.revision` label. If they do not, nothing below runs.
Then `release.yml`'s `promote` job (`environment: pypi`):

1. **Gates on GREEN Tests for this exact commit.** It polls the **Tests**
   workflow run for the tag's SHA (30s interval, 45-minute deadline) until it
   completes, then requires `test`, `conformance`, `integration` **and** `e2e`
   to each report `conclusion == success`. A missing, skipped or renamed required job counts
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

Only after `promote` succeeds does `images-promote` copy the verified rc images
by digest to `:1.8.0`, `:latest` and `:edge`, so `:latest` never moves ahead of
the packages it bundles.

### 4.3 Verify + broadcast

- Prod PyPI shows `<ver>` for `kanibako-cli`, `kanibako-agent-claude` and
  `kanibako`, and the agent packages at their own versions:

  ```bash
  pip download --no-deps --no-cache-dir -d /tmp/verify kanibako==1.8.0
  ```

- The GitHub release for `v<ver>` is published and the rc draft is gone.
- `images-promote` went green, and `:<ver>` and `:latest` resolve to the rc's
  digest for all four variants. If it failed after PyPI succeeded, **re-run
  that job**; it is an idempotent digest copy. Never re-tag
  ([section 6](#6-container-images-release-with-the-cli)).
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

## 6. Container images release with the cli

The four base variants (`min`, `oci`, `lxc`, `vm`) are built from
`images/containers/Containerfile.kanibako` (see
[`images/README.md`](../images/README.md)) by the reusable workflow
`.github/workflows/images.yml`. `release.yml` calls it on the tags that drive
the PyPI release, so one tag releases both.

**The image bundles this tree's own wheel.** Every image build runs
`python -m build --wheel` on the checked-out commit and hands the wheel to the
Containerfile as the named build context `cliwheel`. On an rc tag the tree is
already `X.Y.Z`, and the final tag sits on the same commit, so the image
carries exactly the cli that PyPI gets, and no image build waits on PyPI.

| Event | Image jobs, in order |
| --- | --- |
| push of `v<ver>-rc<n>` | `rc-pypi-check` → `images-rc`: build the four variants, refuse if `:<ver>-rc<n>` exists, push it, guarded `:edge` advance |
| push of `v<ver>` | `images-verify` → `promote` (PyPI) → `images-promote`: digest copy to `:<ver>`, `:latest`, `:edge` |
| push to `main` or a PR touching `images/**`, `src/kanibako/containers/tmux.conf`, the `kanibako baseline list` inputs (`src/kanibako/data/image-baseline.yaml`, `runtime/baseline.py`, `commands/baseline_cmd.py`), `pyproject.toml` or `images.yml` | `images.yml` builds the four variants; nothing is pushed |
| `gh workflow run images.yml` | build only; `-f publish=true` pushes `:<version>-dev.<sha7>`, never a release tag and never `:edge` |

- **Coupled both ways.** A red `images-verify` blocks the PyPI publish, and a
  failed `promote` blocks the image promote. The cost is that an image-only
  breakage (a droste base, an apt mirror) holds up a cli release. The coupling
  toward PyPI is the single `needs: [images-verify]` line on `promote`.
- **Image-only changes ride a cli release.** A droste base bump, a
  Containerfile fix or a security rebuild ships with the next cli release, or
  with a patch release cut for it. There is no image-only release path.
- **Never re-tag in place.** `images-rc` refuses to overwrite an existing rc
  tag, so a broken rc image means cutting the next rc. `images-promote` is an
  idempotent digest copy; if it fails, re-run it.
- **Image inputs under `src/kanibako/**` first meet a full image build at the
  next rc tag.** The path filters above catch the known ones (the baseline
  list, `pyproject.toml`) at PR time. Any other change there that breaks the
  image build turns `images-rc` red on the rc tag, and that blocks the final.
- **Partially-failed rc run: use "Re-run failed jobs", never "Re-run all
  jobs".** A variant that already pushed its `:<ver>-rc<n>` would fail the
  refuse-if-exists check on a full re-run. If a variant pushed but its `:edge`
  step failed, its `:edge` catches up at the next rc.
- **Image dispatch lives in `images.yml`, never in `release.yml`.** Every
  dispatch of `release.yml` reaches the PyPI-uploading `dev` job, so an image
  entry point there would carry a PyPI upload with it.
- **Test an image change before a tag.** A pull request builds the images, and
  on `main` a dispatch with `publish=true` proves GHCR write access through a
  throwaway dev tag. Remove that tag afterwards with
  `.github/workflows/images-prune-tags.yml` (dry run first). ⚑ Never prune a
  promoted `-rc<n>` tag: the promoted tags reference its manifest as a child,
  and the prune guard cannot see that.
- The `e2e` job in `test.yml` consumes a published image
  (`ghcr.io/doctorjei/kanibako-oci:latest`) as the base it overlays HEAD onto,
  so images and cli are coupled in the test direction too.
- **First image release from this repo:** do the one-time GHCR steps in
  [section 9, GHCR package access](#ghcr-package-access) first.

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
- **Never move or re-push a release tag, and never overwrite an image tag.** A
  bad rc (packages or images) is fixed by the next rc. A failed
  `images-promote` is re-run, not re-tagged.
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
secret is required** in this repo.

### GHCR package access

The image jobs push with the same built-in `GITHUB_TOKEN` (job-level
`packages: write`), which reaches a GHCR package only if the package grants
this repo access. For **each** of `kanibako-min`, `kanibako-oci`,
`kanibako-lxc` and `kanibako-vm`: open the package on GitHub → **Package
settings** → **Manage Actions access** → add the `kanibako-cli` repository with
the **Write** role, or **Admin** if `images-prune-tags.yml` is to delete
versions. Without it, the first image push fails with a 403. This must be done
in the GitHub web UI.

Keep the `kanibako-images` repository's Actions access on these packages, and
archive that repository read-only, **only after** the first promote from this
repo succeeds. Archiving stops its tag-triggered workflow from firing again in
the meantime; revoking its access first would leave no working image path if
the first release from here fails.

---

## 10. Known gaps

- **(fixed 2026-08-01)** `template-verify.yml` used to build its base from
  `src/kanibako/containers/Containerfile.kanibako`, which no longer exists
  (only the `Containerfile.template-*` files remain there; the base
  Containerfile left at the 2026-06-12 split and is now back under `images/`),
  so the workflow was broken — and masked, because its path triggers pointed
  at the moved file. It now builds each template directly FROM the published
  `ghcr.io/doctorjei/kanibako-oci:latest` (the same ref the `e2e` job in
  `test.yml` consumes), and its triggers watch only the template Containerfiles
  that still live here. Verification is no longer hermetic against a
  locally-built base; it depends on the published image, like the e2e job.
- **The bundled template Containerfiles still ship inside the cli package**
  (`pyproject.toml` package-data `"kanibako.containers" = ["Containerfile.*",
  ...]`) even though the base images are built from `images/`. Nothing in the
  release pipeline depends on that; it is noted here so a releaser is not
  surprised to find Containerfiles in a PyPI artifact.

---

*Contributions to this document and the release tooling are made with AI
assistance; see the project AI policy. Contact: `<kirobo at bmail dot club>`.*
