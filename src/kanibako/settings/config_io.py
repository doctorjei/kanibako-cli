"""Centralized load/dump/mutate for kanibako config documents (YAML).

Terminology: a config DOCUMENT is one settings-cascade file (``kanibako.cfg``,
a scope's settings file (``yaml``), an agent file, the consolidated name
registry, ``spawn.yaml``); a DOCUMENT MUTATOR is a read-modify-write at a
``(sections, leaf)`` path.  The mutators know nothing about the KEYSPACE — which
file and which nested slot a config KEY maps to is answered by
:mod:`kanibako.settings.config_keys` and :mod:`kanibako.settings.config_dest`.
(pyproject.toml is Python packaging and is NOT handled here.)
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

def render_stored_scalar(v: object) -> str:
    """Render a stored scalar for ``get`` output, keeping §2h's empty idioms apart.

    ⚑⚑ TOTAL, AND THAT IS THE POINT (P3).  Absence is reported ABOVE any render — by
    :func:`read_stored_leaf`, which never reaches here for a leaf that is not in the file —
    so a renderer able to answer ``None`` could only collapse "not set" into a STORED value.
    It used to answer ``None`` for a stored ``""`` and did exactly that: a terminal empty
    string read back "(not set)", which spec §2h forbids in as many words (``""`` ≠ unset).

    THE THREE STORED SPELLINGS, each its own: a present-``None`` (the tri-state OMIT) is
    ``null``; a terminal ``""`` is ``""``; a bool is lowercase, the spelling ``set`` accepts
    back.  ⚑ EACH NAMES THE YAML THE FILE HOLDS, WHICH IS NOT THE SAME AS THE WORD YOU TYPE
    TO WRITE IT.  ``null`` is the file's null, and the write spelling for it is the ``--null``
    FLAG: ``set <key>=null`` stores the three-character STRING, a different value that reads
    back through this same line.  A ``get`` may only teach values the CLI reads, and
    ``str(None)`` taught ``None``, which a typed key refuses and a string key stores as three
    letters — but reading a line back is not always retyping it.
    """
    if v is None:
        return "null"
    if v == "":
        return '""'
    if isinstance(v, bool):
        return str(v).lower()
    return str(v)


def stored_leaf_object(
    noun_file: "Path | None", sections: tuple[str, ...], leaf: str,
    *, default: object = None,
) -> object:
    """The RAW value stored at ``sections/leaf`` in *noun_file*, or *default* when absent.

    ⚑⚑ THE UNRENDERED READ, AND THE ONLY WALK: :func:`read_stored_leaf` and
    :func:`read_stored_pref` are each this plus their own renderer, which is what keeps the
    two from growing separate ideas of where a leaf lives.
    ⚑⚑ A DOOR THAT JUDGES THE VALUE READS HERE, NEVER THE RENDERING.  The renderings are
    deliberately not injective — a stored ``""`` and a stored ``'""'`` both read back ``""``
    (§2h) — so ``rendered == '""'`` cannot tell a user's two-character label from an empty
    one, and a door asking that question must ask it of the object.
    🛑 *default* IS WHAT "NOT THERE" LOOKS LIKE TO THE CALLER, and the caller owns that
    choice because the raw answer cannot make it: a present-``None`` is indistinguishable
    from the ``None`` default.  Pass a sentinel to tell them apart; leave it ``None`` only
    where absent and present-``None`` mean the same thing to you.
    """
    if noun_file is None or not noun_file.exists():
        return default
    node: object = load_doc(noun_file)
    for sec in sections:
        if not isinstance(node, dict):
            return default
        node = node.get(sec)
    if not isinstance(node, dict) or leaf not in node:
        return default
    return node[leaf]


#: :func:`stored_leaf_object`'s "not there" answer for the two rendering readers below, which
#: must tell absence from a present-``None``.  Private: a caller outside this module hands in
#: its own, so this object never travels (P11).
_ABSENT = object()


def read_stored_leaf(
    noun_file: "Path | None", sections: tuple[str, ...], leaf: str,
    *, render: "Callable[[object], str]" = render_stored_scalar,
) -> str | None:
    """The value STORED at ``sections/leaf`` in *noun_file*, or ``None`` when absent / no file.

    ⚑ ``None`` MEANS ABSENT AND NOTHING ELSE, and the TOTAL *render* is what guarantees it:
    a leaf that is not there returns above without rendering anything, and no renderer this
    signature accepts can produce that answer for a value that IS there.  The type carries
    the rule, so no caller has to know it (P3).
    ⚑ *render* is a parameter because the scalar convention is not universal — a leaf whose
    stored shape is not a scalar (``agent_file``'s argv list) renders by a rule that belongs
    with the file that owns the shape, not here.  The default keeps every other caller on the
    scalar convention.
    """
    v = stored_leaf_object(noun_file, sections, leaf, default=_ABSENT)
    return None if v is _ABSENT else render(v)


def render_stored_pref(v: object) -> str:
    """Render a stored ``pref`` REQUEST, keeping all THREE empty idioms apart (spec §2h).

    ⚑ IT AGREES WITH :func:`render_stored_scalar` TODAY, STRING FOR STRING, AND IS STILL NOT
    IT.  They answer different questions: that one DECIDES what a scalar leaf reads back as,
    while this one may only FORWARD — *"the pref layer MUST NOT interpret emptiness AT ALL …
    otherwise it becomes a FOURTH place deciding what 'empty' means"* (§2h).  A request also
    carries whatever shape its TARGET holds, so a rule the scalar convention may adopt about
    values that are not scalars is a rule this side must NOT inherit.  Folding them would put
    one branch under two authorities; the agreement is the point, not a reason to merge.
    """
    if v is None:
        return "null"
    if v == "":
        return '""'
    if isinstance(v, bool):
        return str(v).lower()
    return str(v)


def read_stored_pref(
    noun_file: "Path | None", sections: tuple[str, ...], leaf: str,
    *, render: "Callable[[object], str]" = render_stored_pref,
) -> str | None:
    """Read a stored ``pref`` REQUEST, rendering all THREE empty idioms apart (spec §2h).

    ⚑ *render* IS HANDED IN FOR THE SAME REASON IT IS ON :func:`read_stored_leaf`, and the
    two must not grow separate conventions: a leaf that is not there returns ``None`` above
    without rendering anything, and the TOTAL renderer type is what keeps that the only way
    this answers ``None``.  A pref REQUEST carries whatever shape its TARGET key holds —
    ``pref.agent.default.run_args`` carries the argv LIST — and a shape that is not a scalar
    renders by a rule belonging to the file that owns it, not here.  The default keeps every
    other caller on the three pref idioms.
    """
    v = stored_leaf_object(noun_file, sections, leaf, default=_ABSENT)
    return None if v is _ABSENT else render(v)
