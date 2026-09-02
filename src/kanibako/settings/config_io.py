"""Centralized load/dump/mutate for kanibako config documents (YAML).

Terminology: a config DOCUMENT is one settings-cascade file (``kanibako.cfg``,
a scope's settings file (``yaml``), an agent file, ``names.yaml``, ``spawn.yaml``); a
DOCUMENT MUTATOR is a read-modify-write at a ``(sections, leaf)`` path.  The
mutators know nothing about the KEYSPACE — which file and which nested slot a
config KEY maps to is answered by :mod:`kanibako.settings.config_keys` and
:mod:`kanibako.settings.config_dest`.  (pyproject.toml is Python packaging and is
NOT handled here.)
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

import yaml

from kanibako._atomic import atomic_write_text
from kanibako.errors import ConfigError


def _yaml_problem(exc: yaml.YAMLError) -> str:
    """One-line rendering of a YAML parse failure (the problem + where)."""
    if isinstance(exc, yaml.MarkedYAMLError) and exc.problem:
        mark = exc.problem_mark
        where = (
            f" (line {mark.line + 1}, column {mark.column + 1})"
            if mark is not None else ""
        )
        return f"{exc.problem}{where}"
    return " ".join(str(exc).split())


def load_doc(path: Path | None) -> dict:
    """Load a config document → dict. Missing/empty/non-mapping → {}."""
    if path is None or not path.exists():
        return {}
    text = path.read_text()
    # ⚑ HOST-SAFETY GUARD, not a type nicety: a non-str fed to yaml.safe_load can OOM the box.
    if not isinstance(text, str):
        return {}
    # ⚑ THE PARSE-FAILURE NORMALIZATION BELONGS HERE — this is the one seam that knows the FILE.
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(
            f"the config file {path} is not valid YAML: {_yaml_problem(exc)}. "
            "Fix or remove the file, then retry."
        ) from exc
    return data if isinstance(data, dict) else {}


def dump_doc(path: Path, data: dict) -> None:
    """Serialize *data* to *path* as YAML, atomically (creates parent dirs)."""
    text = yaml.safe_dump(
        data, sort_keys=False, default_flow_style=False, allow_unicode=True,
    )
    atomic_write_text(path, text)


# ---------------------------------------------------------------------------
# Document mutators (load → mutate → dump)
# ---------------------------------------------------------------------------

# ⚑ No key routes to empty ``sections`` today — this pair is the nested pair's
# structural complement, not dead code.  See llm-docs for the measurement.
def write_root_key(path: Path, key: str, value: object) -> None:
    """Write a TOP-LEVEL scalar key, preserving other content."""
    data = load_doc(path)
    data[key] = value
    dump_doc(path, data)


def remove_root_key(path: Path, key: str) -> bool:
    """Remove a TOP-LEVEL scalar key.  Returns True if it was present."""
    if not path.exists():
        return False
    data = load_doc(path)
    if key not in data:
        return False
    del data[key]
    dump_doc(path, data)
    return True


def write_nested_key(
    path: Path, sections: tuple[str, ...], key: str, value: object,
) -> None:
    """Write *key* into a nested table (e.g. ``("system", "path")``), creating intermediates."""
    data = load_doc(path)
    node = data
    for sec in sections:
        child = node.get(sec)
        if not isinstance(child, dict):
            child = {}
            node[sec] = child
        node = child
    node[key] = value
    dump_doc(path, data)


def remove_nested_key(
    path: Path, sections: tuple[str, ...], key: str,
) -> bool:
    """Remove *key* from a nested table, pruning now-empty intermediates.  True if found."""
    if not path.exists():
        return False

    data = load_doc(path)

    # Walk to the innermost table, recording the chain for pruning.
    chain: list[dict] = [data]
    node = data
    for sec in sections:
        if sec not in node or not isinstance(node[sec], dict):
            return False
        node = node[sec]
        chain.append(node)

    if key not in node:
        return False
    del node[key]

    # Prune empty tables bottom-up.
    for i in range(len(sections) - 1, -1, -1):
        if not chain[i + 1]:
            del chain[i][sections[i]]
        else:
            break
    dump_doc(path, data)
    return True


# ---------------------------------------------------------------------------
# Stored-value reads (the ``get`` model's stored-at-noun read + its rendering)
# ---------------------------------------------------------------------------

def render_stored_scalar(v: object) -> str | None:
    """Render a stored scalar for ``get`` output: bools lowercase, empty → None."""
    if isinstance(v, bool):
        return str(v).lower()
    return str(v) if v != "" else None


def read_stored_leaf(
    noun_file: "Path | None", sections: tuple[str, ...], leaf: str,
    *, render: "Callable[[object], str | None]" = render_stored_scalar,
) -> str | None:
    """The value STORED at ``sections/leaf`` in *noun_file*, or ``None`` when absent / no file.

    ⚑ *render* IS THE ONLY WAY THE ABSENT ANSWER AND A RENDERED ONE STAY APART: a leaf that
    is not there returns ``None`` above without rendering anything, so a caller cannot tell
    the two cases apart from the outside and must hand its rendering IN.  It exists because
    the scalar convention is not universal — a leaf whose stored shape is not a scalar
    (``agent_file``'s argv list) renders by a rule that belongs with the file that owns the
    shape, not here.  The default keeps every other caller on the scalar convention.
    """
    if noun_file is None or not noun_file.exists():
        return None
    node: object = load_doc(noun_file)
    for sec in sections:
        if not isinstance(node, dict):
            return None
        node = node.get(sec)
    if not isinstance(node, dict) or leaf not in node:
        return None
    return render(node[leaf])


def read_stored_pref(
    noun_file: "Path | None", sections: tuple[str, ...], leaf: str,
) -> str | None:
    """Read a stored ``pref`` REQUEST, rendering all THREE empty idioms apart (spec §2h)."""
    if noun_file is None or not noun_file.exists():
        return None
    node: object = load_doc(noun_file)
    for sec in sections:
        if not isinstance(node, dict):
            return None
        node = node.get(sec)
    if not isinstance(node, dict) or leaf not in node:
        return None
    v = node[leaf]
    # ⚑ NOT render_stored_scalar: it collapses None and "" — the three pref idioms must stay apart.
    if v is None:
        return "null"
    if v == "":
        return '""'
    if isinstance(v, bool):
        return str(v).lower()
    return str(v)
