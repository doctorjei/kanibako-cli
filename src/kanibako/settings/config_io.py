"""Centralized load/dump/mutate for kanibako config documents (YAML).

All kanibako-owned config files (kanibako_config.yaml, settings.yaml, config.yaml,
settings.yaml, names.yaml, spawn.yaml, general.yaml, agent configs) are
serialized as YAML through :func:`load_doc` / :func:`dump_doc`. There is no
hand-rolled serializer. (pyproject.toml is Python packaging and is NOT handled
here.)

Beside them live the read-modify-write DOCUMENT MUTATORS — read/write/remove at a
``(sections, leaf)`` path, plus the scalar rendering ``get`` displays. They know
nothing about the KEYSPACE: a caller hands them a resolved path into a document
and they preserve everything else in it. Which file, and which nested slot, a
config KEY maps to is a different question, answered by
:mod:`kanibako.settings.config_keys` and :mod:`kanibako.settings.config_dest`.

⚑ These mutators were named ``_write_toml_key*`` / ``_remove_toml_key*`` while
living in ``config_interface``. kanibako has no TOML config file — they called
``load_doc``/``dump_doc`` (YAML) the whole time. The names are now honest.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from kanibako._atomic import atomic_write_text


def load_doc(path: Path | None) -> dict:
    """Load a config document → dict. Missing/empty/non-mapping → {}."""
    if path is None or not path.exists():
        return {}
    text = path.read_text()
    # Defensive: only parse real text. A non-str (e.g. a MagicMock from an
    # under-mocked test path) fed to yaml.safe_load can balloon memory
    # catastrophically — guard the host instead of trusting the input.
    if not isinstance(text, str):
        return {}
    data = yaml.safe_load(text)
    return data if isinstance(data, dict) else {}


def dump_doc(path: Path, data: dict) -> None:
    """Serialize *data* to *path* as YAML (creates parent dirs).

    The write is atomic (temp file in the same dir + ``os.replace``) so a crash
    mid-write can never leave a torn/corrupt config document on disk.
    """
    text = yaml.safe_dump(
        data, sort_keys=False, default_flow_style=False, allow_unicode=True,
    )
    atomic_write_text(path, text)


# ---------------------------------------------------------------------------
# Document mutators (load → mutate → dump)
# ---------------------------------------------------------------------------

def write_root_key(path: Path, key: str, value: object) -> None:
    """Write a TOP-LEVEL scalar key, preserving other content.

    Used for flat KanibakoConfig fields that live at the document root, not
    under a section.
    """
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
    """Write *key* into a nested table (e.g. ``("system", "path")``).

    Preserves other content; creates intermediate tables as needed.
    """
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
    """Remove *key* from a nested table.  Returns True if found.

    Prunes now-empty intermediate tables.
    """
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
) -> str | None:
    """Return the value STORED at ``sections/leaf`` in *noun_file* (the get
    model's stored-at-noun read), or ``None`` when absent / no file.

    A root-level scalar (empty *sections*, e.g. a flat config field) reads the
    document root. Bools render lowercase "true"/"false" (matching ``set``'s
    coercion + ``show``'s rendering); a stored empty string reads as ``None``
    ("(not set)"), preserving the prior "empty ⇒ unset" convention.
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
    return render_stored_scalar(node[leaf])


def read_stored_pref(
    noun_file: "Path | None", sections: tuple[str, ...], leaf: str,
) -> str | None:
    """Read a stored ``pref`` REQUEST, rendering all THREE empty idioms apart.

    ⚑ The general :func:`read_stored_leaf` renders a stored ``""`` as ``None``
    ("(not set)") — the "empty ⇒ unset" convention. That convention is WRONG for
    a pref: §2h designates ``get`` as the verb that *"returns the REQUEST"*, and
    the three idioms it must forward untouched (present-``None``, terminal ``""``,
    and absence) are three DIFFERENT requests. Collapsing two of them into one
    display makes the suppression request — the only channel a box has to drop
    something its agent declares — indistinguishable from having asked nothing.

    absent → ``None`` ("(not set)") · present-``None`` → ``"null"`` ·
    ``""`` → ``'""'`` · else the value.
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
    v = node[leaf]
    if v is None:
        return "null"
    if v == "":
        return '""'
    if isinstance(v, bool):
        return str(v).lower()
    return str(v)
