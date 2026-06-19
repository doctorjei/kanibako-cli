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

``projects`` and ``worksets`` carry the two sections formerly in
``names.yaml`` (the human-name index used for name-based lookups).
``workset_roots`` carries the former ``worksets.yaml`` name → root registry
(used to discover/list worksets).  ``worksets`` and ``workset_roots`` hold the
same name → root data and are kept as the two distinct copies they were AS-IS
(redundant by design — a later sub-step may merge them; this backing-store swap
preserves the two-writer behavior verbatim).  ``connected`` carries the former
``connected.yaml`` payload verbatim.  ``standalone`` is reserved for the
standalone-box identity work in a later sub-step and stays empty here.

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
