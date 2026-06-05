"""Host-side rig registry stored in a single ``rigs.toml`` file.

Pure load/save/query helpers for "added" rig records, keyed by rig name.
Rig names may contain ``/`` and ``:`` (e.g. ``"corp/base:1.0"``); they are
always emitted as quoted TOML table keys.

Reads use the stdlib :mod:`tomllib` (matching :mod:`kanibako.config`).  Writes
are hand-rolled TOML strings — :mod:`kanibako.config` does the same
(``write_global_config`` builds the file line-by-line) rather than depending on
a third-party writer such as ``tomli_w`` (which is not a project dependency).

No network, no global state: the registry path is always passed in.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from pathlib import Path
from typing import TYPE_CHECKING

# Python 3.11+ stdlib (read-only), as used throughout kanibako.config.
import tomllib

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
# the table key) in a stable, file-friendly order.
_INNER_FIELDS: tuple[str, ...] = tuple(
    f.name for f in fields(RigRecord) if f.name != "name"
)


def registry_path(std: StandardPaths) -> Path:
    """Return the path to ``rigs.toml`` under the standard data directory."""
    return std.data_path / "rigs.toml"


def load_registry(path: Path) -> dict[str, RigRecord]:
    """Load all rig records from *path*, keyed by rig name.

    A missing file yields an empty dict.  The file is shaped as a top-level
    ``[rigs]`` table whose keys are rig names::

        [rigs."corp/base:1.0"]
        kind = "prefab"
        ...
    """
    if not path.exists():
        return {}

    with open(path, "rb") as f:
        data = tomllib.load(f)

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
    """Write *records* to *path* as a ``[rigs."<name>"]`` table per record.

    ``None``-valued fields are omitted so the file stays clean.  ``name`` is the
    table key and is not duplicated inside the table.  The parent directory is
    created if needed.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []
    for name in records:
        record = records[name]
        lines.append(f"[rigs.{_quote_key(name)}]")
        for field_name in _INNER_FIELDS:
            value = getattr(record, field_name)
            if value is None:
                continue
            lines.append(f"{field_name} = {_format_value(value)}")
        lines.append("")

    path.write_text("\n".join(lines))


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


# ---------------------------------------------------------------------------
# Minimal TOML serialization helpers (str + bool only — the types we emit).
# ---------------------------------------------------------------------------

def _escape_basic_string(s: str) -> str:
    """Escape a string for a TOML basic (double-quoted) string."""
    out = []
    for ch in s:
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif ch == "\r":
            out.append("\\r")
        else:
            out.append(ch)
    return "".join(out)


def _quote_key(name: str) -> str:
    """Return *name* as a quoted TOML key (rig names may contain ``/`` and ``:``)."""
    return f'"{_escape_basic_string(name)}"'


def _format_value(value: object) -> str:
    """Format a scalar value as a TOML right-hand side."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return f'"{_escape_basic_string(value)}"'
    raise TypeError(f"unsupported rig record value type: {type(value).__name__}")
