"""Host-side rig registry stored in a single ``rigs.yaml`` file.

Pure load/save/query helpers for "added" rig records, keyed by rig name.
Rig names may contain ``/`` and ``:`` (e.g. ``"corp/base:1.0"``); they are
emitted as plain YAML mapping keys (PyYAML quotes them as needed).

Reads and writes go through PyYAML (``yaml.safe_load`` / ``yaml.safe_dump``).
PyYAML handles all escaping and quoting, so there is no hand-rolled
serializer here.

No network, no global state: the registry path is always passed in.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from kanibako.paths import StandardPaths


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
    """Return the path to ``rigs.yaml`` under the standard data directory."""
    return std.data_path / "rigs.yaml"


def load_registry(path: Path) -> dict[str, RigRecord]:
    """Load all rig records from *path*, keyed by rig name.

    A missing or empty file yields an empty dict.  The file is shaped as a
    top-level ``rigs:`` mapping whose keys are rig names::

        rigs:
          corp/base:1.0:
            kind: prefab
            ...
    """
    if not path.exists():
        return {}

    data = yaml.safe_load(path.read_text())
    if not data:
        return {}

    rigs = data.get("rigs", {})
    records: dict[str, RigRecord] = {}
    for name, table in rigs.items():
        kwargs: dict[str, object] = {"name": name}
        for field_name in _INNER_FIELDS:
            if field_name in table:
                kwargs[field_name] = table[field_name]
        records[name] = RigRecord(**kwargs)  # type: ignore[arg-type]
    return records


def save_registry(path: Path, records: dict[str, RigRecord]) -> None:
    """Write *records* to *path* as a ``rigs:`` mapping (one entry per record).

    ``None``-valued fields are omitted so the file stays clean.  ``name`` is the
    mapping key and is not duplicated inside the entry.  The parent directory is
    created if needed.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    rigs: dict[str, dict[str, object]] = {}
    for name, record in records.items():
        table: dict[str, object] = {}
        for field_name in _INNER_FIELDS:
            value = getattr(record, field_name)
            if value is None:
                continue
            table[field_name] = value
        rigs[name] = table

    data = {"rigs": rigs}
    path.write_text(yaml.safe_dump(data, sort_keys=False))


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
