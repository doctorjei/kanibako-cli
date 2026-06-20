"""Consolidated name registry (``system.registry`` → ``registry.yaml``).

A single file at ``@system.registry`` (``{data_path}/global/registry.yaml``)
backs every kanibako *name* store.  It replaces the former separate files
``names.yaml`` (projects + worksets), ``worksets.yaml`` (workset name → root)
and ``connected.yaml`` (external-connect redirects), which are no longer read
or written.

The file has these top-level sections::

    projects:
      myapp: /home/user/projects/myapp

    worksets:
      clientwork: /home/user/worksets/client

    workset_roots:
      clientwork: /home/user/worksets/client

    connected:
      /abs/external/repo: {workset: myws, project: foo}

    standalone:
      # box.name → root, populated by sub-step 5d; empty for now.

    rigs:
      corp/base:1.0: {kind: prefab, ...}   # formerly rigs.yaml

    image_shells:
      sha256:abc...: /bin/bash             # formerly image-shells.yaml

``projects`` and ``worksets`` carry the two sections formerly in
``names.yaml`` (the human-name index used for name-based lookups).
``workset_roots`` carries the former ``worksets.yaml`` name → root registry
(used to discover/list worksets).  ``worksets`` and ``workset_roots`` hold the
same name → root data and are kept as the two distinct copies they were AS-IS
(redundant by design — a later sub-step may merge them; this backing-store swap
preserves the two-writer behavior verbatim).  ``connected`` carries the former
``connected.yaml`` payload verbatim.  ``standalone`` is reserved for the
standalone-box identity work in a later sub-step and stays empty here.
``rigs`` carries the former ``rigs.yaml`` payload (added-rig records keyed by
rig name; the ``rig_registry`` module owns its shape).  ``image_shells`` carries
the former ``image-shells.yaml`` map (image store key → captured login shell;
the ``shells`` module owns its shape).

The ``rigs`` and ``image_shells`` sections are owned by ``rig_registry`` and
``shells`` respectively, which read/write them through the path-based
``load_section_at``/``save_section_at`` helpers (preserving sibling sections);
this module passes their values through verbatim.

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
    "workset_roots",
    "connected",
    "standalone",
    "rigs",
    "image_shells",
)
# Name → path sections whose keys are sorted on write (legacy names.yaml shape).
_NAME_SECTIONS: frozenset[str] = frozenset(
    {"projects", "worksets", "workset_roots"}
)


def registry_path(data_path: Path) -> Path:
    """Return the path to ``registry.yaml`` for *data_path*.

    Mirrors the ``system.registry`` resolution (``@system.global/registry.yaml``
    == ``{data_path}/global/registry.yaml``) so the name stores keep their
    ``data_path``-based public signatures.
    """
    return data_path / "global" / "registry.yaml"


def load_registry(data_path: Path) -> dict[str, dict]:
    """Load ``registry.yaml`` and return all sections.

    Absent file → empty sections.  Every section key is always present so
    callers can index it without a ``.get`` default.  ``projects``/``worksets``
    are ``{name: path_str}``; ``connected``/``standalone`` are passed through as
    stored.
    """
    path = registry_path(data_path)
    data = load_doc(path) if path.is_file() else {}
    return {
        "projects": {
            k: str(v) for k, v in dict(data.get("projects", {})).items()
        },
        "worksets": {
            k: str(v) for k, v in dict(data.get("worksets", {})).items()
        },
        "workset_roots": {
            k: str(v) for k, v in dict(data.get("workset_roots", {})).items()
        },
        "connected": dict(data.get("connected", {})),
        "standalone": dict(data.get("standalone", {})),
        "rigs": dict(data.get("rigs", {})),
        "image_shells": dict(data.get("image_shells", {})),
    }


def save_registry(data_path: Path, registry: dict[str, dict]) -> None:
    """Atomically write *registry* to ``registry.yaml``.

    Only the canonical sections are persisted; ``projects``/``worksets`` keys
    are sorted for stable diffs (matching the legacy ``names.yaml`` writer).
    Missing sections default to empty.
    """
    data: dict = {}
    for section in _SECTIONS:
        entries = registry.get(section, {}) or {}
        if section in _NAME_SECTIONS:
            data[section] = {name: entries[name] for name in sorted(entries)}
        else:
            data[section] = dict(entries)
    dump_doc(registry_path(data_path), data)


def load_section(data_path: Path, section: str) -> dict:
    """Return a single section of ``registry.yaml``."""
    return load_registry(data_path)[section]


def save_section(data_path: Path, section: str, entries: dict) -> None:
    """Replace a single section of ``registry.yaml`` and write atomically.

    Reads the current registry, swaps *section*, and writes the whole file so
    the other sections are preserved.
    """
    registry = load_registry(data_path)
    registry[section] = dict(entries)
    save_registry(data_path, registry)


# ---------------------------------------------------------------------------
# Path-based section access (for section-owners with a path-based public API)
# ---------------------------------------------------------------------------
#
# ``rig_registry`` and ``shells`` expose path-based functions (they take the
# ``registry.yaml`` path directly, not ``data_path``).  These two helpers let
# them read/write their own section of the consolidated file while preserving
# every sibling section — without restructuring their public signatures.  The
# ``data_path`` is recovered from the registry path (``…/global/registry.yaml``
# → ``…``) so the canonical loader/writer is reused unchanged.


def _data_path_for(registry_file: Path) -> Path:
    """Recover ``data_path`` from a ``…/global/registry.yaml`` path."""
    return registry_file.parent.parent


def load_section_at(registry_file: Path, section: str) -> dict:
    """Return *section* of the ``registry.yaml`` located at *registry_file*."""
    return load_section(_data_path_for(registry_file), section)


def save_section_at(registry_file: Path, section: str, entries: dict) -> None:
    """Replace *section* of the ``registry.yaml`` at *registry_file*, atomically.

    Sibling sections are preserved (whole-file rewrite via ``save_registry``).
    """
    save_section(_data_path_for(registry_file), section, entries)


# ---------------------------------------------------------------------------
# Standalone-box helpers (``standalone`` section: box.name → project root)
# ---------------------------------------------------------------------------
#
# Standalone boxes are self-describing on disk (``box_data/`` marker under the
# project root); ``registry.standalone`` is a derived index keyed by the box's
# ``<random24>_<leaf>`` name → root path string.  It backs the whole-name
# collision check (D-M13) and the drop-in import work in the next sub-step.


def load_standalone(data_path: Path) -> dict[str, str]:
    """Return the ``standalone`` section as ``{box_name: root_str}``."""
    return {k: str(v) for k, v in load_section(data_path, "standalone").items()}


def standalone_box_names(data_path: Path) -> set[str]:
    """Return the set of registered standalone box names (the collision domain)."""
    return set(load_standalone(data_path))


def register_standalone(data_path: Path, box_name: str, root: Path) -> None:
    """Register a standalone box (``box_name`` → *root*) in the registry.

    Idempotent for a matching ``(box_name, root)`` pair; overwrites the stored
    root if the same name re-registers a different root (a moved box).
    """
    entries = load_standalone(data_path)
    entries[box_name] = str(root)
    save_section(data_path, "standalone", entries)


def unregister_standalone(data_path: Path, box_name: str) -> None:
    """Remove *box_name* from the ``standalone`` section (no-op if absent)."""
    entries = load_standalone(data_path)
    if entries.pop(box_name, None) is not None:
        save_section(data_path, "standalone", entries)


def standalone_name_for_root(data_path: Path, root: Path) -> str | None:
    """Return the registered standalone box name whose root is *root*, if any.

    Lets a caller (e.g. the next drop-in-import sub-step) check whether an
    on-disk standalone root is already registered, and reuse its name.
    """
    target = str(root)
    for name, root_str in load_standalone(data_path).items():
        if root_str == target:
            return name
    return None
