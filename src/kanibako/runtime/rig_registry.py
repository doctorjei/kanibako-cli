"""Host-side rig registry, stored as the ``rigs`` section of ``registry.yaml``.

Pure load/save/query helpers for "added" rig records, keyed by rig name.
Rig names may contain ``/`` and ``:`` (e.g. ``"corp/base:1.0"``); they are
emitted as plain YAML mapping keys (the YAML writer quotes them as needed).

The records live as the ``rigs:`` top-level section of the consolidated
``system.registry`` file (``registry.yaml``); this module owns that section's
shape and reads/writes it via ``registry_store.load_section`` /
``save_section`` (which preserve every sibling section).  ``RigRecord`` and
the public load/save/query API (all path-based) are unchanged from when this was
its own ``rigs.yaml`` — only the on-disk *location* moved.

No network, no global state: the registry path is always passed in.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from pathlib import Path
from typing import TYPE_CHECKING

from kanibako import registry_store

if TYPE_CHECKING:
    from kanibako.settings.paths import StandardPaths

# The section of ``registry.yaml`` this module owns.
_SECTION = "rigs"


@dataclass
class RigRecord:
    """A single "added" rig record.

    ``name`` is also the registry key; the remaining fields are optional and
    carry whatever metadata is relevant to the rig's kind (prefab / extended).
    """

    name: str
    kind: str
    source: str | None = None
    source_type: str | None = None
    image: str | None = None
    parent: str | None = None
    foundation_source: str | None = None
    reproducible: bool | None = None
    created: str | None = None
    added: str | None = None


# Fields stored *inside* the table (i.e. everything except ``name``, which is
# the mapping key) in a stable, file-friendly order.
_INNER_FIELDS: tuple[str, ...] = tuple(
    f.name for f in fields(RigRecord) if f.name != "name"
)


def registry_path(std: StandardPaths) -> Path:
    """Return the path to the consolidated ``registry.yaml`` (``system.registry``).

    The single source of the registry location is the resolved ``config.registry``
    surfaced as ``std.registry`` (a repointed ``config.registry`` is honored).  Rig
    records live as the ``rigs:`` section of this file; the path-based public API
    keeps its ``StandardPaths``-derived signature, so call sites are unchanged.
    """
    return std.registry


def load_registry(path: Path) -> dict[str, RigRecord]:
    """Load all rig records from the ``rigs`` section of *path*, keyed by name.

    A missing file (or absent ``rigs:`` section) yields an empty dict.  The
    section is a mapping whose keys are rig names::

        rigs:
          corp/base:1.0:
            kind: prefab
            ...
    """
    rigs = registry_store.load_section(path, _SECTION)
    records: dict[str, RigRecord] = {}
    for name, table in rigs.items():
        kwargs: dict[str, object] = {"name": name}
        for field_name in _INNER_FIELDS:
            if field_name in table:
                kwargs[field_name] = table[field_name]
        records[name] = RigRecord(**kwargs)  # type: ignore[arg-type]
    return records


def save_registry(path: Path, records: dict[str, RigRecord]) -> None:
    """Write *records* to the ``rigs`` section of *path* (one entry per record).

    ``None``-valued fields are omitted so the file stays clean.  ``name`` is the
    mapping key and is not duplicated inside the entry.  Sibling sections of
    ``registry.yaml`` are preserved; the write is atomic.
    """
    rigs: dict[str, dict[str, object]] = {}
    for name, record in records.items():
        table: dict[str, object] = {}
        for field_name in _INNER_FIELDS:
            value = getattr(record, field_name)
            if value is None:
                continue
            table[field_name] = value
        rigs[name] = table

    registry_store.save_section(path, _SECTION, rigs)


def upsert(path: Path, record: RigRecord) -> None:
    """Insert *record* (or overwrite the existing record with the same name)."""
    records = load_registry(path)
    records[record.name] = record
    save_registry(path, records)


def remove(path: Path, name: str) -> bool:
    """Remove the record named *name*.

    Returns ``True`` if a record was removed, ``False`` if it was absent.
    """
    records = load_registry(path)
    if name not in records:
        return False
    del records[name]
    save_registry(path, records)
    return True


def get(path: Path, name: str) -> RigRecord | None:
    """Return the record named *name*, or ``None`` if it is not registered."""
    return load_registry(path).get(name)
