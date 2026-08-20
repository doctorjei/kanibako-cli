"""Consolidated name registry (``config.registry`` → ``registry.yaml``).

One file backs every *global* name store: ``worksets`` (name → root),
``standalone`` (box name → root), ``deregistered`` (recovery blobs), plus ``rigs``
and ``image_shells``, whose shapes are owned by ``rig_registry`` / ``shells`` and
passed through verbatim here.

Every public function takes the resolved ``config.registry`` FILE path
(``std.registry``); nothing reconstructs it from ``config.data``.  Old files are
never read (no migration), an absent file yields empty sections, and writes are
atomic via ``config_io.dump_doc``.

See ``llm-docs/kanibako/project/registry_store.py.md`` for the file layout, what
this consolidation replaced, and the design of the ``deregistered`` section.
"""

from __future__ import annotations

from pathlib import Path

from kanibako.settings.config_io import dump_doc, load_doc

# Top-level sections of registry.yaml, in canonical order.
# ⚑ This tuple is the RETIREMENT mechanism: a section absent from it is neither
# surfaced by the loader nor rewritten, so the retired ``projects`` block is
# dropped on the next write.  ⚑ There is NO ``seeded`` section and must not be —
# membership IS the seed signal, one way only (present ==> seeded, not converse).
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
# Also sorted on write, but the value is a blob rather than a bare path — so no
# path-string coercion.
_SORTED_BLOB_SECTIONS: frozenset[str] = frozenset(
    {"deregistered"}
)


def load_registry(registry: Path) -> dict[str, dict]:
    """Load the ``registry.yaml`` at *registry*; absent file → empty sections.

    Every canonical section key is always present, so callers can index the result
    without a ``.get`` default.
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

    Only the canonical sections are persisted (a missing one defaults to empty),
    and name-keyed sections are sorted for stable diffs.
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
    """Replace one section of the ``registry.yaml``, preserving the others."""
    sections = load_registry(registry)
    sections[section] = dict(entries)
    save_registry(registry, sections)


# ---------------------------------------------------------------------------
# Standalone-box helpers (``standalone`` section: box.name → project root)
# ---------------------------------------------------------------------------
#
# ⚑ A DERIVED index, not the truth: standalone boxes are self-describing on disk
# (``box_data/`` marker under the project root).


def load_standalone(registry: Path) -> dict[str, str]:
    """Return the ``standalone`` section as ``{box_name: root_str}``."""
    return {k: str(v) for k, v in load_section(registry, "standalone").items()}


def standalone_box_names(registry: Path) -> set[str]:
    """Return the set of registered standalone box names (the collision domain)."""
    return set(load_standalone(registry))


def register_standalone(registry: Path, box_name: str, root: Path) -> None:
    """Register a standalone box (``box_name`` → *root*); a re-register overwrites."""
    entries = load_standalone(registry)
    entries[box_name] = str(root)
    save_section(registry, "standalone", entries)


def unregister_standalone(registry: Path, box_name: str) -> None:
    """Remove *box_name* from the ``standalone`` section (no-op if absent)."""
    entries = load_standalone(registry)
    if entries.pop(box_name, None) is not None:
        save_section(registry, "standalone", entries)


def standalone_name_for_root(registry: Path, root: Path) -> str | None:
    """Return the registered standalone box name whose root is *root*, if any."""
    target = str(root)
    for name, root_str in load_standalone(registry).items():
        if root_str == target:
            return name
    return None


# ---------------------------------------------------------------------------
# Deregistered-box helpers (``deregistered`` section: box.name → retained blob)
# ---------------------------------------------------------------------------
#
# ``rm`` without ``--purge`` parks a recovery blob here so a later ``--purge``
# (or an I2 ``register`` readopt) can still find the box BY NAME once its active
# membership is gone.  Entry: ``kind`` (primary|standalone), ``workspace``,
# ``metadata`` (the box dir ``--purge`` deletes), optional ``image`` and
# ``deregistered_at``.
#
# ⚑ FLAT map keyed by the BARE name, with ``kind`` inside the entry — do not
# promote ``kind`` into the key.  The recovery verbs are keyed by the bare name,
# and tuple keys do not serialise to YAML.  See the llm-doc.


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
    """Park a deregistered box's recovery blob under *box_name*, overwriting any prior."""
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
    """Drop *box_name* from ``deregistered``; ``False`` if absent (purge is idempotent)."""
    entries = load_deregistered(registry)
    if entries.pop(box_name, None) is not None:
        save_section(registry, "deregistered", entries)
        return True
    return False


def lookup_deregistered(registry: Path, box_name: str) -> dict | None:
    """Return the deregistered entry for *box_name*, or ``None``.

    ⚑ A PURE read: self-healing belongs at the ``list`` / ``purge`` seam, never
    here, so callers get a predictable lookup.
    """
    entry = load_deregistered(registry).get(box_name)
    return dict(entry) if entry is not None else None


def _metadata_definitively_gone(path: str) -> bool:
    """True only when *path* is DEFINITIVELY absent — the drop-safe case.

    ⚑ NOT ``Path.exists()``: that collapses "genuinely gone" and "could not stat
    it" into one ``False``, so a transient permission / I/O error would false-drop
    a live box and lose the only handle left to ``register`` or ``purge`` it.
    Only ``ENOENT`` / ``ENOTDIR`` prove removal; anything else KEEPS the entry.
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

    Dropped only when ``metadata`` is empty (no recovery target) or definitively
    gone; the pruned section is persisted when anything is removed.
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
