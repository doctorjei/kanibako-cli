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
``worksets`` carries the workset name → root registry used both for name-based
lookups AND to discover/list worksets (the former separate ``worksets.yaml`` and
its ``workset_roots`` duplicate were collapsed onto this single section,
2026-06-29f).  ``connected`` carries the former
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
    "connected",
    "standalone",
    "seeded",
    "rigs",
    "image_shells",
)
# Name → path sections whose keys are sorted on write (legacy names.yaml shape).
_NAME_SECTIONS: frozenset[str] = frozenset(
    {"projects", "worksets"}
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
    seeded_raw = data.get("seeded", {})
    if not isinstance(seeded_raw, dict):
        seeded_raw = {}
    return {
        "projects": {
            k: str(v) for k, v in dict(data.get("projects", {})).items()
        },
        "worksets": {
            k: str(v) for k, v in dict(data.get("worksets", {})).items()
        },
        "connected": dict(data.get("connected", {})),
        "standalone": dict(data.get("standalone", {})),
        "seeded": {
            "projects": {
                k: bool(v)
                for k, v in dict(seeded_raw.get("projects", {})).items()
            },
            "standalone": {
                k: bool(v)
                for k, v in dict(seeded_raw.get("standalone", {})).items()
            },
        },
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
        if section == "seeded":
            # Nested ``{domain: {name: bool}}`` shape (NOT a name->path map):
            # persist each domain with its inner keys sorted for stable diffs.
            data[section] = {
                domain: {
                    name: bool(entries.get(domain, {})[name])
                    for name in sorted(entries.get(domain, {}))
                }
                for domain in ("projects", "standalone")
            }
        elif section in _NAME_SECTIONS:
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


# ---------------------------------------------------------------------------
# Seeded-flag helpers (``seeded`` section: {domain: {box_name: bool}})
# ---------------------------------------------------------------------------
#
# These are the per-box seed-once read/write primitives for the PRIMARY
# (``domain == "projects"``) and STANDALONE (``domain == "standalone"``)
# registries, the explicit successor to the brittle ``.seeded`` sentinel file.
# The named-workset seeded flag lives on ``WorksetProject.seeded`` instead.
# The uniform launch-path API that dispatches across all three lives in
# :mod:`kanibako.box_seed`.


def is_box_seeded(data_path: Path, domain: str, box_name: str) -> bool:
    """Return whether *box_name* in *domain* has completed its one-time seed.

    *domain* is ``"projects"`` (PRIMARY) or ``"standalone"``.  False when the
    section/domain/name is absent (a fresh or legacy registry).
    """
    return bool(
        load_registry(data_path)["seeded"].get(domain, {}).get(box_name, False)
    )


def mark_box_seeded_entry(data_path: Path, domain: str, box_name: str) -> None:
    """Record that *box_name* in *domain* has completed its one-time seed.

    Idempotent; preserves every sibling section (whole-file rewrite via
    ``save_registry``).  Reuses ``load_registry``/``save_registry`` — no extra
    YAML I/O.
    """
    registry = load_registry(data_path)
    registry["seeded"].setdefault(domain, {})[box_name] = True
    save_registry(data_path, registry)
