# salvage/

**The holding pen for DORMANT SOURCE — code kept for a designed future purpose.**

This directory holds code retained for **possible future use**. It is **NOT part
of the live product** and is **NOT shipped in the wheel** (the package is built
from `src/` only — see `pyproject.toml` `[tool.setuptools.packages.find]
where = ["src"]`). It is also excluded from `ruff` (`extend-exclude = ["salvage"]`)
and `mypy` (`exclude = ['^salvage/']`), and it is not collected by pytest
(`testpaths = ["tests"]`).

Nothing here is imported by the live tree, and nothing here is dead weight
awaiting deletion — each resident is here because it has a **named future**.
Two distinct kinds live here, and the difference matters when you read one:

- **Frozen reference snapshots** — retired code kept as the *pattern* for future
  work. These may not even import cleanly against the current tree (they
  deliberately carry frozen local copies of logic since deleted from the live
  path). Port the shape; do not copy-paste. → `primary_reconcile.py`
- **Dormant complete machinery** — working code, unmodified from its last live
  state, parked until the conditions it was written for arrive. Reactivation is
  a move back plus a doc re-point, nothing more. → `deprecation.py` (+ its test)

**Adding a resident:** give the file a header stating *when* it was sequestered,
*why*, and *exactly what reactivation requires*; add a section below; and if a
doc describes the machinery, make that doc say the machinery is sequestered
here rather than silently pointing at a path that no longer exists.

## Contents

### `primary_reconcile.py`
*(frozen reference snapshot)*

The primary-box **drop-in reconcile-from-disk importers** — `import_primary_box`,
`import_primary_box_for_workspace`, and `reconcile_primary_boxes` — retired from
the live path in **P8b (Option A: the registry is the sole authority for box
identity; a box does not self-describe its identity on disk)**.

Under the pre-P8b model a primary box's `settings.yaml` carried a `project:`
section (name/mode) and a `resolved:` section (workspace/shell/vault paths).
These functions enumerated `@config.primary_workset/boxes/*`, read that on-disk
meta, matched a box to an external workspace, refused name collisions, and
journalled the register atomically — the mechanism that let a user move a box
tree and have kanibako re-discover it.

P8b made `create` write a **sparse** `settings.yaml` (user-overrides only; no
`project:`/`resolved:`), so there is no longer any on-disk identity meta for
these functions to read. The `read_project_meta` / `write_project_meta` helpers
they depended on were **deleted from live `config.py` in P8c**. The frozen copy
here therefore carries its own local `_read_project_meta` so the snapshot reads
coherently in isolation.

**Why keep it:** this enumerate / match-by-workspace / collision-refuse /
journal-atomic skeleton is the seed of a future **heuristic, possibly
interactive `system recover`** (rebuild registry entries from the on-disk box
layout). Under the current model that repair would re-source identity from
`box_resolve.resolve_box_identity` (dir layout + `box_data/` marker + the sparse
`workset.kuid`) rather than the `project:`/`resolved:` meta these frozen copies
read — so a real `system recover` is a **re-source + re-write**, not a
copy-paste of this file. This is the pattern to port, not live code to import.

### `deprecation.py` + `test_deprecations.py`
*(dormant complete machinery — sequestered 2026-08-01, Phase 0, Jei ruling)*

The post-public **deprecation policy** machinery: an in-process registry of
deprecation records, a `@deprecated` decorator and declarative `register(...)`
helper, and the CI gate (`test_no_overdue_deprecations`) that fails the build
once `kanibako.__version__` reaches any record's `remove_at` while the entry is
still present.

The Phase-0 retirement sweep measured it as having **zero production
consumers** — the registry is empty by design, so the gate passed trivially and
nothing in `src/` or `packages/` ever imported it. That made it a deletion
candidate on the numbers. It is **not** residue, though: it is infrastructure
whose entire value is being in place *before* the first deprecation is
declared, and `docs/architecture.md` documents the policy it implements.

**Jei's ruling (2026-08-01):** *"if it's all one file, sequester it in a source
folder for this purpose; we might run into more like this."* Hence this folder's
generalized charter above.

**Why keep it:** the project's breaking-change rule is major-only, and the
moment a post-public deprecation is declared this gate is what forces its actual
removal at the right release. Rewriting it later against a drifted version
scheme is strictly worse than parking the working version now.

**Reactivation:** move `deprecation.py` → `src/kanibako/`, `test_deprecations.py`
→ `tests/`, and re-point `docs/architecture.md` (whose policy section currently
records that the machinery is sequestered here). The code is unmodified from its
last live state, so nothing else is required.
