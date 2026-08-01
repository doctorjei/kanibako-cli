"""Consolidated name registry (``config.registry`` → ``registry.yaml``).

A single file at ``@config.registry`` (``@config.data/global/registry.yaml`` ==
``{data_path}/global/registry.yaml``) backs every kanibako *global* name store.
It replaces the former separate files ``names.yaml`` (projects + worksets) and
``worksets.yaml`` (workset name → root), which are no longer read or written.
(The former global ``connected:`` external-connect index is GONE — connections
now live in each workset's per-workset registry as a ``boxes:`` entry, design
D10.)

The file has these top-level sections::

    worksets:
      clientwork: /home/user/worksets/client

    standalone:
      # box.name → root, populated by sub-step 5d; empty for now.

    deregistered:
      # box.name → retained-recovery blob for a box removed by ``rm`` (no
      # ``--purge``); its metadata is kept on disk so a later ``rm <name>
      # --purge`` can find it BY NAME (the active membership is already gone).
      # Each entry: {kind: primary|standalone, workspace, metadata, image?,
      # deregistered_at?}.  Self-heals: a list/purge that finds an entry whose
      # metadata dir is gone drops it.

    # NOTE: there is NO ``seeded`` section.  Registry MEMBERSHIP is itself the
    # seed signal — a box present here (STANDALONE ``standalone`` / NAMED
    # workset-local list / PRIMARY per-workset ``boxes:`` membership) was seeded
    # when ``create`` registered it (seed-then-register, §0/§5 of the keyspace
    # spec).  The former ``seeded`` flag section (and its first-launch gate) are
    # GONE.

    rigs:
      corp/base:1.0: {kind: prefab, ...}   # formerly rigs.yaml

    image_shells:
      sha256:abc...: /bin/bash             # formerly image-shells.yaml

The former ``projects`` section (default-mode box name → external-workspace) has
been RETIRED (clean split, 2026-07-08): a PRIMARY box's identity now lives SOLELY
in the primary workset's per-workset ``boxes:`` membership
(``@config.primary_workset/registry.yaml`` via :mod:`kanibako.project.workset_registry`),
the AUTHORITATIVE source of box names (spec L514).  This section is no longer
loaded or written; a stale ``projects`` block left by an older install is simply
dropped on the next ``save_registry`` (no migration, no legacy read).

``worksets`` carries the workset name → root registry used both for name-based
lookups AND to discover/list worksets (the former separate ``worksets.yaml`` and
its ``workset_roots`` duplicate were collapsed onto this single section,
2026-06-29f).  ``standalone`` maps a registered standalone box's
``<kuid>_<leaf>`` name → root path.
``rigs`` carries the former ``rigs.yaml`` payload (added-rig records keyed by
rig name; the ``rig_registry`` module owns its shape).  ``image_shells`` carries
the former ``image-shells.yaml`` map (image store key → captured login shell;
the ``shells`` module owns its shape).

The ``rigs`` and ``image_shells`` sections are owned by ``rig_registry`` and
``shells`` respectively, which read/write them through ``load_section`` /
``save_section`` (preserving sibling sections); this module passes their values
through verbatim.

Every public function takes the resolved ``config.registry`` FILE path
(``std.registry``) — the single source of the registry location.  A repointed
``config.registry`` is honored end-to-end; nothing reconstructs the path from
``config.data``.

No on-disk migration is performed: the old files are NOT read.  A fresh tree
(absent ``registry.yaml``) yields empty sections.  Writes are atomic (via
``config_io.dump_doc`` — temp file + ``os.replace``).
"""

from __future__ import annotations

from pathlib import Path

from kanibako.settings.config_io import dump_doc, load_doc

# Top-level sections of registry.yaml, in canonical order.  ``projects`` was
# RETIRED (2026-07-08): default-mode box identity lives in the primary workset's
# per-workset ``boxes:`` membership, not here.  Dropping it from this tuple stops
# the loader surfacing it AND drops any stale block on the next write.
_SECTIONS: tuple[str, ...] = (
    "worksets",
    "standalone",
    "deregistered",
    "rigs",
    "image_shells",
)
# Name → path sections whose keys are sorted on write (legacy names.yaml shape).
_NAME_SECTIONS: frozenset[str] = frozenset(
    {"worksets"}
)
# Sections whose keys are sorted on write for stable diffs (name-keyed, but the
# value may be a blob rather than a bare path — ``deregistered`` is name → entry
# dict, so it sorts like a name section without the path-string coercion).
_SORTED_BLOB_SECTIONS: frozenset[str] = frozenset(
    {"deregistered"}
)


def load_registry(registry: Path) -> dict[str, dict]:
    """Load the ``registry.yaml`` at *registry* and return all sections.

    *registry* is the resolved ``config.registry`` file path (the single source of
    the registry location; callers pass ``std.registry``).  A user who repoints
    ``config.registry`` is honored end-to-end — there is no ``data_path``-relative
    reconstruction.

    Absent file → empty sections.  Every section key is always present so
    callers can index it without a ``.get`` default.  ``worksets`` is
    ``{name: path_str}``; ``standalone`` is passed through as stored.  A stale
    ``projects`` block (retired 2026-07-08) is ignored — never surfaced.
    """
    data = load_doc(registry) if registry.is_file() else {}
    return {
        "worksets": {
            k: str(v) for k, v in dict(data.get("worksets", {})).items()
        },
        "standalone": dict(data.get("standalone", {})),
        "deregistered": {
            k: dict(v) for k, v in dict(data.get("deregistered", {})).items()
        },
        "rigs": dict(data.get("rigs", {})),
        "image_shells": dict(data.get("image_shells", {})),
    }


def save_registry(registry: Path, sections: dict[str, dict]) -> None:
    """Atomically write *sections* to the ``registry.yaml`` at *registry*.

    *registry* is the resolved ``config.registry`` file path.  Only the canonical
    sections are persisted; the ``worksets`` name section's keys are sorted for
    stable diffs (matching the legacy ``names.yaml`` writer).  Missing sections
    default to empty.  A retired ``projects`` block is NOT among the canonical
    sections, so this write drops it (clean split).
    """
    data: dict = {}
    for section in _SECTIONS:
        entries = sections.get(section, {}) or {}
        if section in _NAME_SECTIONS:
            data[section] = {name: entries[name] for name in sorted(entries)}
        elif section in _SORTED_BLOB_SECTIONS:
            data[section] = {name: dict(entries[name]) for name in sorted(entries)}
        else:
            data[section] = dict(entries)
    dump_doc(registry, data)


def load_section(registry: Path, section: str) -> dict:
    """Return a single section of the ``registry.yaml`` at *registry*."""
    return load_registry(registry)[section]


def save_section(registry: Path, section: str, entries: dict) -> None:
    """Replace a single section of the ``registry.yaml`` at *registry*, atomically.

    Reads the current registry, swaps *section*, and writes the whole file so
    the other sections are preserved.
    """
    sections = load_registry(registry)
    sections[section] = dict(entries)
    save_registry(registry, sections)


# ---------------------------------------------------------------------------
# Standalone-box helpers (``standalone`` section: box.name → project root)
# ---------------------------------------------------------------------------
#
# Standalone boxes are self-describing on disk (``box_data/`` marker under the
# project root); ``registry.standalone`` is a derived index keyed by the box's
# ``<kuid>_<leaf>`` name → root path string.  It backs the whole-name
# collision check (D-M13) and the drop-in import work in the next sub-step.


def load_standalone(registry: Path) -> dict[str, str]:
    """Return the ``standalone`` section as ``{box_name: root_str}``."""
    return {k: str(v) for k, v in load_section(registry, "standalone").items()}


def standalone_box_names(registry: Path) -> set[str]:
    """Return the set of registered standalone box names (the collision domain)."""
    return set(load_standalone(registry))


def register_standalone(registry: Path, box_name: str, root: Path) -> None:
    """Register a standalone box (``box_name`` → *root*) in the registry.

    Idempotent for a matching ``(box_name, root)`` pair; overwrites the stored
    root if the same name re-registers a different root (a moved box).
    """
    entries = load_standalone(registry)
    entries[box_name] = str(root)
    save_section(registry, "standalone", entries)


def unregister_standalone(registry: Path, box_name: str) -> None:
    """Remove *box_name* from the ``standalone`` section (no-op if absent)."""
    entries = load_standalone(registry)
    if entries.pop(box_name, None) is not None:
        save_section(registry, "standalone", entries)


def standalone_name_for_root(registry: Path, root: Path) -> str | None:
    """Return the registered standalone box name whose root is *root*, if any.

    Lets a caller (e.g. the next drop-in-import sub-step) check whether an
    on-disk standalone root is already registered, and reuse its name.
    """
    target = str(root)
    for name, root_str in load_standalone(registry).items():
        if root_str == target:
            return name
    return None


# ---------------------------------------------------------------------------
# Deregistered-box helpers (``deregistered`` section: box.name → retained blob)
# ---------------------------------------------------------------------------
#
# When ``rm`` (no ``--purge``) deregisters a box, its metadata is retained on
# disk and a small blob is parked here so a later ``--purge`` (or, in I2, a
# ``register`` readopt) can find it BY NAME — the active membership is already
# gone, so name → path resolution against the live registry would miss it.
#
# KEYING: a single flat map keyed by the bare box NAME, with ``kind``
# (``primary`` | ``standalone``) stored INSIDE each entry.  Box names come from a
# single validated namespace (primary boxes in the primary-workset ``boxes:``
# membership; standalone boxes as canonical ``<kuid>_<leaf>`` names), and the
# user-facing recovery verbs (``rm <name> --purge`` / ``register <name>``) are
# keyed by that bare name — so a flat name → entry map matches the lookup exactly
# and keeps the YAML round-trip clean (tuple keys do not serialise).  The
# per-kind teardown/readopt routing reads ``kind`` from the entry, so no
# composite key is needed; if a real primary/standalone name collision domain
# ever emerges the entry already carries ``kind`` and the key can be promoted to
# ``"<kind>/<name>"`` locally.
#
# Each entry: ``kind``, ``workspace`` (project/workspace path), ``metadata`` (box
# dir — primary ``std.boxes/<name>``; standalone in-tree root), optional
# ``image`` and ``deregistered_at`` (stamped at the CLI seam).


def load_deregistered(registry: Path) -> dict[str, dict]:
    """Return the ``deregistered`` section as ``{box_name: entry_dict}``."""
    return {
        k: dict(v) for k, v in load_section(registry, "deregistered").items()
    }


def register_deregistered(
    registry: Path,
    box_name: str,
    *,
    kind: str,
    workspace: str | None,
    metadata: str,
    image: str | None = None,
    deregistered_at: str | None = None,
) -> None:
    """Park a deregistered box's recovery blob under *box_name*.

    Overwrites any existing entry for *box_name* (a re-``rm`` refreshes the
    retained blob).  *metadata* is the box dir a later ``--purge`` deletes
    (primary ``std.boxes/<name>``; standalone in-tree root); *workspace* is the
    project path (for readopt / create-conflict detection in later increments).
    """
    entries = load_deregistered(registry)
    entry: dict = {
        "kind": kind,
        "workspace": str(workspace) if workspace is not None else None,
        "metadata": str(metadata),
    }
    if image is not None:
        entry["image"] = str(image)
    if deregistered_at is not None:
        entry["deregistered_at"] = str(deregistered_at)
    entries[box_name] = entry
    save_section(registry, "deregistered", entries)


def unregister_deregistered(registry: Path, box_name: str) -> bool:
    """Drop *box_name* from the ``deregistered`` section.

    Returns ``True`` if an entry was removed, ``False`` if none was present
    (a no-op — supports idempotent purge).
    """
    entries = load_deregistered(registry)
    if entries.pop(box_name, None) is not None:
        save_section(registry, "deregistered", entries)
        return True
    return False


def lookup_deregistered(registry: Path, box_name: str) -> dict | None:
    """Return the deregistered entry for *box_name*, or ``None``.

    A pure read — self-healing (dropping entries whose metadata dir is gone)
    happens at the ``list`` / ``purge`` seam (:func:`list_deregistered` and the
    purge handler), never here, so callers get a predictable lookup.
    """
    entry = load_deregistered(registry).get(box_name)
    return dict(entry) if entry is not None else None


def _metadata_definitively_gone(path: str) -> bool:
    """True only when *path* is DEFINITIVELY absent — the drop-safe case.

    The self-heal in :func:`list_deregistered` must NOT drop a recovery pointer
    on a TRANSIENT filesystem error: a plain ``Path.exists()`` collapses "the dir
    is genuinely gone" and "I could not stat it (permission / I/O error)" into the
    same ``False``, so an ambiguous error would false-drop a still-present box and
    lose the only handle to ``register`` / ``purge`` it.

    Distinguish the two by inspecting the ``OSError``: only ``ENOENT`` (no such
    file) / ``ENOTDIR`` (a path component is not a directory) prove the target is
    really gone.  Any other error (``EACCES``, ``EIO``, ``ESTALE``, …) is treated
    as "present, cannot confirm removal" so the entry is KEPT.  ``os.stat``
    follows symlinks (matching the previous ``Path.exists()`` semantics), so a
    dangling symlink still reads as gone.
    """
    import errno
    import os

    try:
        os.stat(path)
    except OSError as exc:
        return exc.errno in (errno.ENOENT, errno.ENOTDIR)
    return False


def list_deregistered(registry: Path) -> dict[str, dict]:
    """Return the deregistered entries, self-healing genuinely-stale ones.

    An entry is dropped only when its ``metadata`` path is empty (no recovery
    target) or DEFINITIVELY gone from disk (the box was deleted out-of-band); the
    pruned section is persisted when anything is removed.  A transient stat error
    (permission / I/O) is NOT a genuine removal, so such an entry is KEPT rather
    than false-dropped (which would lose its recovery pointer) — see
    :func:`_metadata_definitively_gone`.  This is the ``list`` self-heal from the
    design, hardened for the transient-FS case now that ``box list`` wires it.
    """
    entries = load_deregistered(registry)
    live: dict[str, dict] = {}
    dropped = False
    for name, entry in entries.items():
        meta = entry.get("metadata")
        if not meta or _metadata_definitively_gone(str(meta)):
            dropped = True
        else:
            live[name] = entry
    if dropped:
        save_section(registry, "deregistered", live)
    return live
