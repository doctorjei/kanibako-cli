"""The per-agent settings file's SHAPE — the ONLY module that spells its root table."""

# ⚑⚑ WHY THIS MODULE EXISTS (rulings 49-52, 2026-08-14).  ``self`` is NOT a key: it is a
# FILE-SURFACE ALIAS that substitutes to ``agent.<agent>``, created *"exclusively for config
# files"* — *"There's no need for our code to ever use self"*.  Everything past this boundary
# traffics in the ACTUAL agent reference.  Six independent sites used to spell the file's shape
# (and one of them claimed in its own docstring to be the only one); they are all here now, so
# the claim is true by construction rather than by assertion.
#
# ⚑ THE SPLIT WITH ``settings_assemble`` IS DELIBERATE AND LOAD-BEARING: this module produces the
# file's RAW table (:class:`AgentFileLevel`) and never touches ``KeyStore``.  The store coercion
# stays with the assembler.  Cutting the seam at the SHAPE rather than at the level keeps the
# import edge one-way — a boundary that imported the assembler would close a cycle.

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Mapping

from kanibako.settings.agent_config import (
    IDENTITY_KEYS,
    AgentConfig,
    agent_settings_path,
)
from kanibako.settings.config_io import (
    dump_doc,
    load_doc,
    read_stored_leaf,
    remove_nested_key,
    write_nested_key,
)
from kanibako.settings.settings_resolve import SettingsError

#: The per-agent file's ROOT table — the file's self-reference, spelled ONCE, HERE.
_ROOT: Final[str] = "self"

#: The root as a nested-walk PREFIX, for the ONE raw-walk site that needs it:
#: ``settings_assemble._BEHAVIOR_TABLE_SHAPES``, whose rows are uniform
#: ``(prefix, depth-of-<sub>)`` pairs and so cannot take a slot or a level.
#: ⚑ NOT AN INVITATION.  Every other consumer takes an :class:`AgentFileSlot`, an
#: :class:`AgentFileLevel` or :func:`file_spelling` and never sees the spelling at all; a
#: second raw-walk caller means the walk itself belongs in here.
ROOT_SECTIONS: Final[tuple[str, ...]] = (_ROOT,)

# Every schema-owned (MODELLED) key of the per-agent file. Anything else that is
# dict-valued under ``self`` is a discriminated ``self.<node>.*`` sub-table and
# rides ``AgentConfig.node_tables`` opaquely; the modelled keys must never be
# captured there (load) nor clobbered from there (write).
_MODELED_KEYS = IDENTITY_KEYS | frozenset({"env", "secret_path", "transform_settings"})

#: The categories the per-agent file stores FLAT under ``self`` rather than in the discriminated
#: ``self.<node>`` sub-table — ``self`` IS ``agent.<node>``, so there is no second ``<node>``
#: embedding. ⚑ ORDER IS NOT SIGNIFICANT (they are distinct category names, so no re-root can
#: shadow another). The WRITE side of this same fact is :func:`_address`, which routes
#: ``agent set <node> env.FOO=bar`` to exactly the table :func:`level_table` reads back — the two
#: are siblings in this module now, which is what stops them drifting.
_FLAT_AGENT_CATEGORIES: tuple[str, ...] = ("secret_path", "env")

#: The categories NOTHING may nest under inside ``self:`` (ruling 49c, EXTENDED by ruling 50 —
#: *"There is no self:<agent>:foo. It is just self:foo"* / *"self is not a key, it's just an
#: alias / pointer to agent.<agent>"*).
#:
#: ⚑⚑ THE MODEL, and it is not "a second embedding looks redundant": ``self`` is NOT A KEY. It
#: SUBSTITUTES to ``agent.<agent>``, so ``self.<sub>.<category>`` READS
#: ``agent.<agent>.<sub>.<category>`` — a key that cannot exist, because ``agent.claude`` does not
#: contain a ``claude`` level (*"That would be agent.claude.claude"* / *"never ever ever"*). The
#: argument is UNIFORM over any ``<sub>``, which is why the literal ``default`` refuses on the same
#: line and why the agent file has no spelling for the all-agents tier AT ALL.
#:
#: 🛑 ``bindings`` IS DELIBERATELY ABSENT and its absence is not an oversight: nested is bindings'
#: ONLY spelling today, so refusing it before a flat route exists would delete a live delivery
#: path. That flatten is S2 of the rectification — do not add it here ahead of the flat route.
#: ⚑ NOT :data:`_FLAT_AGENT_CATEGORIES`, which answers a different question (where a category is
#: READ FROM, not which nesting is REFUSED); the two happen to agree today and must not be merged.
_REFUSED_NESTED_AGENT_CATEGORIES: tuple[str, ...] = ("env", "secret_path")

#: The placeholder a cure renders for a category whose refused table is EMPTY (nothing to quote).
_CATEGORY_VALUE_PLACEHOLDER: "dict[str, str]" = {
    "env": "<value>", "secret_path": "<host-path>",
}


# ---------------------------------------------------------------------------
# The two carriers — a SLOT (one value) and a LEVEL (one cascade tier)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AgentFileSlot:
    """WHERE one per-node value lives: a file, the node it belongs to, and the key TAIL.

    ⚑⚑ IT CARRIES NO ``sections``/``leaf``, AND THAT IS THE WHOLE POINT (P3/P4).  The four
    per-node resolvers used to hand callers a ``(path, sections, leaf)`` triple, so seven
    ``config_interface`` sites held a ``("self", …)`` address — internal code trafficking in the
    file-surface alias.  The address is now produced INSIDE :func:`read_leaf` / :func:`write_leaf`
    / :func:`remove_leaf` and is unavailable to a caller: the violation is not forbidden, it is
    unrepresentable.

    ⚑⚑ A FROZEN DATACLASS, NEVER A ``NamedTuple``.  A NamedTuple keeps ``isinstance(x, tuple)``
    True and every ``path, sections, leaf = route`` unpacking silently working at the WRONG arity
    — the same-arity shape flip that passes green while the meaning changes.  The dataclass makes
    both go loudly false.
    """

    path: Path
    node: str
    tail: str


@dataclass(frozen=True)
class AgentFileLevel:
    """ONE cascade tier read out of the agent file: its §2d discriminator and its RAW table.

    *node* is the discriminator the tier merges under (``default`` or the active agent), NOT
    necessarily the agent whose file this is — ``assemble_levels`` builds BOTH tiers from the one
    file.  *table* is raw YAML: no ``KeyStore``, no bind parsing, no precedence (that is
    ``settings_assemble``'s half of the seam).
    """

    node: str
    table: dict


# ---------------------------------------------------------------------------
# The file ADDRESS — spelled once, produced only in here
# ---------------------------------------------------------------------------

def _address(tail: str, node: str) -> tuple[tuple[str, ...], str]:
    """Map a per-agent-file key TAIL to its ``(sections, leaf)`` inside the file.

    *tail* is the part of a canonical per-node agent key AFTER the ``agent.<node>.``
    prefix (e.g. ``model``, ``env.FOO``, ``secret_path.TOK``, ``bindings.ro.share``);
    *node* is the agent id (the file's own discriminator).

    The file's own top-level table is :data:`_ROOT` (its self-reference — the renamed old bare
    ``agent`` values), and the category split is load-bearing:

    * flat state (``model`` / ``endpoint`` / ``access`` / …) lives DIRECTLY under
      the root — the shape :func:`load` reads into ``AgentConfig`` for the launch invocation;
    * ``env.*`` and ``secret_path.*`` live DIRECTLY under the root too — ``self`` IS
      ``agent.<node>``, so there is NO second ``<node>`` embedding.  ⚑ BOTH are
      re-rooted into the launch CASCADE by :func:`level_table`
      (:data:`_FLAT_AGENT_CATEGORIES`, whose read side this write side must match);
      :func:`load` models them for the ``agent`` verbs' own READS
      (``info`` / ``show`` / ``get``), not to deliver them.  ⚑ A second ``<node>``
      level under the root is REFUSED for BOTH (rulings 49c + 50: ``self`` is an
      ALIAS for ``agent.<node>``, so ``self.<node>.env`` reads
      ``agent.<node>.<node>.env``), so this route is the only spelling — which is
      what keeps write and read from drifting apart.
    * ``bindings.{ro,rw}.*`` still live in the DISCRIMINATED ``self.<node>.*`` sub-table
      — the shape :func:`level_table` reads into the launch cascade (it reads
      ``self.<node>`` and re-roots to ``agent.<node>``). Flattening bindings the same
      way as secret_path is S2 of the rectification (its cascade/repoint machinery needs a
      separate, careful pass).

    ⚑⚑ THE ``bindings`` ARM SPLITS THE DEST ON ``.`` AND THAT IS A KNOWN DEFECT (D-4), MOVED
    HERE VERBATIM ON PURPOSE.  A dotted destination (``bindings.ro.~/.cache/uv``) is shattered
    across YAML levels: the read lands on a slot no file has and the write lays down an unusable
    shape.  S3 fixes it red-then-green — the dest is DATA and is never split.  Do NOT "tidy" it
    into a fix here: S1 is behaviour-preserving, and a silent repair inside a relocation is
    exactly how a behaviour change hides in a move.
    """
    if tail.startswith("secret_path."):
        return (_ROOT, "secret_path"), tail[len("secret_path."):]
    if tail.startswith("bindings."):
        segs = tail.split(".")  # bindings.<ro|rw>.<name>
        return (_ROOT, node, *segs[:-1]), segs[-1]
    if tail.startswith("env."):
        return (_ROOT, "env"), tail[len("env."):]
    return (_ROOT,), tail


def file_spelling(node: str, tail: str = "") -> str:
    """The agent file's OWN spelling of *node*'s sub-table, with an optional *tail*.

    For the two message surfaces that must QUOTE the file at a user — a refusal naming the table
    it found, and a bind cure naming the table to hand-edit.  ⚑ It renders the DISCRIMINATED
    sub-table, which is the shape those two messages are about; S2 changes the wording in one
    place when the nested spelling dies.
    """
    return f"{_ROOT}.{node}.{tail}" if tail else f"{_ROOT}.{node}"


def slot_for(agents_root: Path, node: str, tail: str) -> AgentFileSlot:
    """The :class:`AgentFileSlot` for *node*'s *tail* under *agents_root*."""
    return AgentFileSlot(agent_settings_path(agents_root, node), node, tail)


def read_leaf(slot: AgentFileSlot) -> str | None:
    """The value STORED at *slot*, or ``None`` when absent / no file.

    ⚑ Straight through :func:`~kanibako.settings.config_io.read_stored_leaf` — its two rendering
    conventions (bools lowercase, a stored ``""`` reading as ``None``) are load-bearing for every
    ``get``, so this must NOT re-render on top of them.
    """
    sections, leaf = _address(slot.tail, slot.node)
    return read_stored_leaf(slot.path, sections, leaf)


def write_leaf(slot: AgentFileSlot, value: object) -> None:
    """Write *value* at *slot*, creating intermediate tables (sparse read-modify-write)."""
    sections, leaf = _address(slot.tail, slot.node)
    write_nested_key(slot.path, sections, leaf, value)


def remove_leaf(slot: AgentFileSlot) -> bool:
    """Remove the value at *slot*, pruning emptied tables; True if one was there."""
    sections, leaf = _address(slot.tail, slot.node)
    return remove_nested_key(slot.path, sections, leaf)


def clear_overrides(path: Path, node: str) -> int:
    """Drop every user override from *node*'s file, PRESERVING ``name``; return the count.

    Sparse "remove all user overrides": from the root table, every key EXCEPT ``name`` (this
    removes run_args, all state keys, AND the discriminated ``<node>`` sub-table that holds
    secret_path / node binds), then prune the now-empty root table.  Sparse write: no default keys
    re-materialized ([[settings-must-map-to-keystore-key]]).

    The COUNT is what ``agent reset --all`` reports, in the same terms the other scopes'
    ``reset_all`` uses: each ``secret_path`` entry under the node sub-table counts individually
    (parity with the old flat env_file count); each other removed key counts once.
    """
    data = load_doc(path)
    count = 0
    agent_sec = data.get(_ROOT)
    if isinstance(agent_sec, dict):
        for k in [k for k in agent_sec if k != "name"]:
            val = agent_sec[k]
            if k == node and isinstance(val, dict):
                # The discriminated node sub-table: count each secret_path
                # pointer per-VAR (parity with the old flat env_file count),
                # plus one for any other node content (e.g. node binds).
                secret_sub = val.get("secret_path")
                if isinstance(secret_sub, dict):
                    count += len(secret_sub)
                if any(kk != "secret_path" for kk in val):
                    count += 1
            else:
                count += 1
            del agent_sec[k]
        if not agent_sec:
            del data[_ROOT]
    dump_doc(path, data)
    return count


# ---------------------------------------------------------------------------
# The WHOLE-FILE round trip (the ``agent`` verbs' own reads + the persona artifact)
# ---------------------------------------------------------------------------

def load(path: Path) -> AgentConfig:
    """Read an agent config file and return an AgentConfig.

    Returns defaults if the file does not exist.
    """
    cfg = AgentConfig()
    if not path.exists():
        return cfg

    data = load_doc(path)

    agent_sec = data.get(_ROOT, {})
    if not isinstance(agent_sec, dict):
        agent_sec = {}
    cfg.name = str(agent_sec.get("name", ""))
    raw_args = agent_sec.get("run_args", [])
    cfg.run_args = [str(a) for a in raw_args] if isinstance(raw_args, list) else []

    # Flat state = the SCALAR agent-state knobs. Exclude identity keys AND any
    # dict-valued entry: the discriminated ``<node>:`` sub-table (env, secret_path,
    # node binds) is a dict and is NOT flat state — it rides ``_agent_partial``, not
    # the ``_agent_state_partial`` state channel.
    cfg.state = {
        k: str(v)
        for k, v in agent_sec.items()
        if k not in IDENTITY_KEYS and not isinstance(v, dict)
    }
    # env: VAR -> value, read DIRECTLY from the root's ``env`` table (``self`` IS
    # ``agent.<node>``).  Carried for the ``agent info`` / ``show`` / ``get`` READS;
    # the launch reads the same table off the file through the cascade, never off
    # this field (MBR-1 P3).
    cfg.env = {k: str(v) for k, v in agent_sec.get("env", {}).items()}
    # secret_path: VAR -> host PATH pointer, read DIRECTLY from the root's
    # ``secret_path`` table (spec §2a SECRET category — ``self`` IS ``agent.<node>``,
    # no second embedding).
    # Stored as a plain string path; the file's CONTENTS (the secret) are never
    # persisted here nor read — they are ro-mounted + exported IN-BOX only at launch.
    secret_sub = agent_sec.get("secret_path", {})
    cfg.secret_path = {
        k: str(v) for k, v in secret_sub.items()
    } if isinstance(secret_sub, dict) else {}
    cfg.transform_settings = dict(agent_sec.get("transform_settings", {}))
    # Any OTHER dict-valued entry under the root is a discriminated
    # ``self.<node>.*`` sub-table (node binds).  Carry it OPAQUELY: a load→write
    # round trip that did NOT carry it would silently DROP a user's node binds.
    # ⚑ NO LIVE CALLER MAKES THAT ROUND TRIP TODAY (measured — see the
    # ``AgentConfig`` docstring); the carry is a guard, not a running guarantee.
    cfg.node_tables = {
        k: dict(v)
        for k, v in agent_sec.items()
        if isinstance(v, dict) and k not in _MODELED_KEYS
    }

    return cfg


def save(path: Path, cfg: AgentConfig) -> None:
    """Write an AgentConfig to a YAML file."""
    agent_sec: dict = {
        "name": cfg.name,
        "run_args": list(cfg.run_args),
    }
    for k, v in cfg.state.items():
        agent_sec[k] = v
    # secret_path (spec §2a SECRET category) is stored DIRECTLY under the root
    # (``self.secret_path.<VAR>`` — ``self`` IS ``agent.<node>``, no second ``<node>``
    # embedding) — the SAME first-class category location ``config set
    # agent.<node>.secret_path.<VAR>`` writes and :func:`level_table` reads into the
    # launch cascade. Only materialized when non-empty (sparse).
    if cfg.secret_path:
        agent_sec["secret_path"] = dict(cfg.secret_path)
    # Sparse write — an EMPTY category is not materialized (parity with
    # secret_path above; [[settings-must-map-to-keystore-key]]). A phantom
    # ``transform_settings: {}`` / ``env: {}`` would otherwise be counted as an
    # override by ``agent reset --all``. transform_settings is NOT a reset-all
    # exception — when set it is a normal override, wiped like any other.
    if cfg.transform_settings:
        agent_sec["transform_settings"] = dict(cfg.transform_settings)
    if cfg.env:
        agent_sec["env"] = dict(cfg.env)
    # Discriminated ``self.<node>.*`` sub-tables (node binds) re-emitted
    # opaquely — sparse (an empty table is dropped, parity with the categories
    # above); see :func:`load` and the ``AgentConfig`` class docstring.  A
    # schema-owned key can never ride through here (guard both ends: load never
    # captures one, and a hand-built config cannot clobber a modelled table).
    for node_key, node_table in cfg.node_tables.items():
        if node_table and node_key not in _MODELED_KEYS:
            agent_sec[node_key] = dict(node_table)

    data: dict = {
        _ROOT: agent_sec,
    }
    # The settings file lives inside the per-agent store dir
    # (agents/<agent>/settings.yaml); ensure that dir exists.
    path.parent.mkdir(parents=True, exist_ok=True)
    dump_doc(path, data)


# ---------------------------------------------------------------------------
# The CASCADE view — one file, two tiers, and the refusals that guard the shape
# ---------------------------------------------------------------------------

def _nested_agent_cure(
    category: str, sub_key: str, *, var: str, value: str
) -> str:
    """The ARM-APPROPRIATE fix for a refused ``self.<sub>.<category>`` (rulings 49c / 50).

    ⚑ THE EXPLANATION IS UNIFORM (alias expansion) BUT THE CURES ARE NOT, which is the whole
    reason this is a function and not one message. The flat ``self: <category>:`` table is
    re-rooted for the ACTIVE layer ONLY, so the all-agents tier has no agent-file spelling to
    move to at all — it is written in the SYSTEM file as ``agent: default: <category>:``. Sending
    an all-agents value to the flat table would silently NARROW it to one node, so the default
    arm must NOT name ``agent set``. Both routes measured live (2026-08-14).
    """
    from kanibako.settings.config_keys import AGENT_DEFAULT_SUB

    if sub_key == AGENT_DEFAULT_SUB:
        return (
            f"the all-agents tier is written in the SYSTEM settings file, not the "
            f"agent file:\n"
            f"    agent:\n      default:\n        {category}:\n          {var}: {value}"
        )
    return (
        f"kanibako agent set {sub_key} {category}.{var}={value}\n"
        f"  — or by hand, in the FLAT table (`{_ROOT}:` expands to `agent.{sub_key}`, "
        f"so the {category} table sits DIRECTLY under it):\n"
        f"    {_ROOT}:\n      {category}:\n        {var}: {value}"
    )


def _refuse_nested_agent_categories(
    node_tbl: dict, *, sub_key: str, node: str | None, path: Path | None
) -> None:
    """RAISE when the agent file nests a :data:`_REFUSED_NESTED_AGENT_CATEGORIES` table under a
    second level inside ``self:`` (rulings 49c / 50).

    *node* is the agent whose FILE this is — it renders the ALIAS EXPANSION in the message and is
    never read; ``None`` renders the shape ``<agent>``.

    ⚑ *node_tbl* must be the ``self.<sub_key>`` sub-table AS READ, BEFORE the flat splice re-roots
    anything into it — otherwise a legal flat ``self: env:`` is indicted for the nested table's sin,
    and (worse) a nested-ONLY file, which is the common case, is not caught at all.

    ⚑ PRESENCE, not truthiness: a bare ``env:`` leaf parses to ``None`` and an empty one to ``{}``,
    and both are still the spelling being refused — the same present-vs-absent trap the
    assembler's ``_NO_LEAF`` sentinel exists for.
    """
    from kanibako.settings.config_keys import AGENT_DEFAULT_SUB

    agent = node or "<agent>"
    for category in _REFUSED_NESTED_AGENT_CATEGORIES:
        if category not in node_tbl:
            continue
        spelling = file_spelling(sub_key, category)
        entries = node_tbl[category]
        table = entries if isinstance(entries, dict) else {}
        var = sorted(str(k) for k in table)[0] if table else "<VAR>"
        value = (
            str(table[var]) if var in table
            else _CATEGORY_VALUE_PLACEHOLDER.get(category, "<value>")
        )
        named = ", ".join(sorted(str(k) for k in table)) or "(none yet)"
        # ⚑⚑ ONE EXPLANATION FOR BOTH ARMS — ruling 50's alias semantics, not a redundancy
        # argument. ``self`` is not a key; it SUBSTITUTES to ``agent.<agent>``, so the spelling
        # expands to a key that cannot exist, and that is equally true of ``default``. Only the
        # HISTORY and the CURE split by arm, each for its own real reason.
        expansion = f"agent.{agent}.{sub_key}.{category}"
        if sub_key == AGENT_DEFAULT_SUB:
            history = (
                f"Refusing rather than running: it used to resolve as though it were "
                f"the all-agents `agent.default.{category}.<VAR>` tier, which is a "
                f"real tier — but one the SYSTEM file spells, not this one."
            )
        else:
            history = (
                f"Refusing rather than running: it used to resolve to the same "
                f"`agent.{sub_key}.{category}.<VAR>` keys as the flat table, and in a "
                f"file carrying BOTH the flat one REPLACED it wholesale — every entry "
                f"spelled only here vanished without a word."
            )
        raise SettingsError(
            f"`{spelling}` is not a settings key, so kanibako will not read it.\n"
            f"`{_ROOT}:` is NOT a key — it is an ALIAS that substitutes to "
            f"`agent.{agent}`. So `{spelling}` reads `{expansion}`, which is never "
            f"syntactically correct: `agent.{agent}` does not contain a `{sub_key}` "
            f"level. Nothing nests under `{_ROOT}:` but the categories themselves "
            f"(spec §0, closed keyspace).\n"
            f"Found in the {sub_key} sub-table of "
            f"{path if path is not None else '<agent settings>'}; entries: "
            f"{named}.\n"
            f"{history}\n"
            f"  Fix: {_nested_agent_cure(category, sub_key, var=var, value=value)}\n"
            f"  then delete the `{spelling}` table from "
            f"{path if path is not None else 'the agent settings file'}."
        )


def level_table(
    raw: Any, *, sub_key: str, node: str | None = None, path: Path | None = None
) -> AgentFileLevel:
    """The RAW table one agent-tier level reads out of *raw*, under its TRUE §2d name.

    *sub_key* selects the sub-table; the two agent levels are kept SEPARATE (spec §2) and merge by
    their true §2d names — NO bare-``agent`` collapse.  A missing root table, or a *sub_key* with
    no matching sub-table, yields an EMPTY table.  *path* and *node* only render the refusal
    message (the file's name, and the agent ``self`` expands to); neither is read.

    ⚑ THE REFUSAL RUNS BEFORE THE FLAT SPLICE, and the ordering is the contract — see
    :func:`_refuse_nested_agent_categories`.
    """
    from kanibako.settings.config_keys import AGENT_DEFAULT_SUB

    agent = raw.get(_ROOT) if isinstance(raw, dict) else None
    if not isinstance(agent, dict):
        return AgentFileLevel(sub_key, {})
    sub = agent.get(sub_key)
    node_tbl: dict = dict(sub) if isinstance(sub, dict) else {}
    # ⚑ BEFORE the splice below, and deliberately so — see the function's contract.
    _refuse_nested_agent_categories(
        node_tbl, sub_key=sub_key, node=node, path=path,
    )
    # ⚑ ``self`` IS ``agent.<active-node>``, so the FLATTENED categories live at the file's TOP
    # level, not in the ``self.<node>`` sub-table — re-root them for the ACTIVE layer ONLY, never
    # the all-agents ``default`` (they are THIS node's, not every agent's). Without this a
    # category is not in the cascade at all: the launch secret export saw no agent-scope
    # secret_path and mounted no token, and an ``agent.<node>.env.<VAR>`` was no snapshot leaf,
    # so it reached the box only on a private under-layer BELOW every collapsed slot and never
    # saw the expand pass (llm-docs).
    # ⚑ CONTRACT THE TYPES CANNOT CARRY: the splice REPLACES a same-named table found in the
    # ``self.<node>`` sub-table, so a file carrying BOTH spellings of one category would resolve
    # the FLAT one and lose the nested table WHOLESALE. ⚑ NEITHER SPLICED CATEGORY CAN REACH THAT
    # SHAPE ANY MORE — :func:`_refuse_nested_agent_categories` above rejects a nested ``env`` OR
    # ``secret_path`` by name (rulings 49c / 50), which is what makes the silent loss UNREACHABLE
    # rather than merely documented. The replacement itself is kept, not deleted: it is what makes
    # the refusal's placement testable, and ``bindings`` still rides the sub-table.
    if sub_key != AGENT_DEFAULT_SUB:
        for category in _FLAT_AGENT_CATEGORIES:
            flat = agent.get(category)
            if isinstance(flat, dict) and flat:
                node_tbl[category] = flat
    return AgentFileLevel(sub_key, node_tbl)


def state_level(
    state: Mapping[str, str] | None, *, node: str
) -> AgentFileLevel | None:
    """The agent file's FLAT behaviour state as a DISCRIMINATED level, or ``None`` if empty.

    The per-agent file stores behaviour FLAT (``model`` — already per-agent), not under the
    ``agent.<active>`` / ``agent.default`` sub-tables the cascade merges by.  The discriminator is
    the file's OWN node and is attached HERE, at the boundary, rather than being carried
    undiscriminated through the launch and attached at snapshot build.

    ⚑ NO PRODUCTION CALLER YET: the carrier swap through ``settings_launch._agent_state_partial``
    and the ``start.py`` producers is S1b, serialized behind P4b (``start.py`` is single-writer).
    Declared here with S1's other boundary surface so the two halves of one seam land together.
    """
    if not state:
        return None
    return AgentFileLevel(node, dict(state))
