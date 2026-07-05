# salvage/

This directory holds code retained for **possible future use**. It is **NOT
part of the live product** and is **NOT shipped in the wheel** (the package is
built from `src/` only — see `pyproject.toml` `[tool.setuptools.packages.find]
where = ["src"]`). It is also excluded from `ruff` and `mypy`.

Nothing here is imported by the live tree. Treat every file as a **frozen
reference snapshot**: it may not even import cleanly against the current code
(it deliberately carries frozen local copies of logic that has since been
deleted from the live path).

## Contents

### `primary_reconcile.py`

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
