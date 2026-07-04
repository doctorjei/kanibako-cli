"""Consolidated name registry (``config.registry`` → ``registry.yaml``).

A single file at ``@config.registry`` (``@config.data/global/registry.yaml`` ==
``{data_path}/global/registry.yaml``) backs every kanibako *name* store.  It replaces the former separate files
``names.yaml`` (projects + worksets) and ``worksets.yaml`` (workset name →
root), which are no longer read or written.  (The former global ``connected:``
external-connect index is GONE — connections now live in each workset's
per-workset registry as a ``boxes:`` entry, design D10.)

The file has these top-level sections::

    projects:
      myapp: /home/user/projects/myapp

    worksets:
      clientwork: /home/user/worksets/client

    standalone:
      # box.name → root, populated by sub-step 5d; empty for now.

    # NOTE: there is NO ``seeded`` section.  Registry MEMBERSHIP is itself the
    # seed signal — a box present here (PRIMARY ``projects`` / STANDALONE
    # ``standalone`` / NAMED workset-local list) was seeded when ``create``
    # registered it (seed-then-register, §0/§5 of the keyspace spec).  The
    # former ``seeded`` flag section (and its first-launch gate) are GONE.

    rigs:
      corp/base:1.0: {kind: prefab, ...}   # formerly rigs.yaml

    image_shells:
      sha256:abc...: /bin/bash             # formerly image-shells.yaml

``projects`` and ``worksets`` carry the two sections formerly in
``names.yaml`` (the human-name index used for name-based lookups).
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

from kanibako.config_io import dump_doc, load_doc

# Top-level sections of registry.yaml, in canonical order.
_SECTIONS: tuple[str, ...] = (
    "projects",
    "worksets",
    "standalone",
    "rigs",
    "image_shells",
)
# Name → path sections whose keys are sorted on write (legacy names.yaml shape).
_NAME_SECTIONS: frozenset[str] = frozenset(
    {"projects", "worksets"}
)


def load_registry(registry: Path) -> dict[str, dict]:
    """Load the ``registry.yaml`` at *registry* and return all sections.

    *registry* is the resolved ``config.registry`` file path (the single source of
    the registry location; callers pass ``std.registry``).  A user who repoints
    ``config.registry`` is honored end-to-end — there is no ``data_path``-relative
    reconstruction.

    Absent file → empty sections.  Every section key is always present so
    callers can index it without a ``.get`` default.  ``projects``/``worksets``
    are ``{name: path_str}``; ``connected``/``standalone`` are passed through as
    stored.
    """
    data = load_doc(registry) if registry.is_file() else {}
    return {
        "projects": {
            k: str(v) for k, v in dict(data.get("projects", {})).items()
        },
        "worksets": {
            k: str(v) for k, v in dict(data.get("worksets", {})).items()
        },
        "standalone": dict(data.get("standalone", {})),
        "rigs": dict(data.get("rigs", {})),
        "image_shells": dict(data.get("image_shells", {})),
    }


def save_registry(registry: Path, sections: dict[str, dict]) -> None:
    """Atomically write *sections* to the ``registry.yaml`` at *registry*.

    *registry* is the resolved ``config.registry`` file path.  Only the canonical
    sections are persisted; ``projects``/``worksets`` keys are sorted for stable
    diffs (matching the legacy ``names.yaml`` writer).  Missing sections default
    to empty.
    """
    data: dict = {}
    for section in _SECTIONS:
        entries = sections.get(section, {}) or {}
        if section in _NAME_SECTIONS:
            data[section] = {name: entries[name] for name in sorted(entries)}
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
