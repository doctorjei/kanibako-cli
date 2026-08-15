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

# Every schema-owned (MODELLED) key of the per-agent file — the ones :class:`AgentConfig` holds
# as fields of its own.
# 🛑 A CATEGORY MUST NEVER BE ADDED HERE without both an ``AgentConfig`` field and a ``save``
# emission: load would capture it out of the opaque carrier and write would never put it back,
# which is a silent data-loss shape.
_MODELED_KEYS = IDENTITY_KEYS | frozenset({"env", "secret_path", "transform_settings"})

#: EVERY category the per-agent file stores FLAT under ``self`` — ``self`` IS ``agent.<node>``, so
#: there is no second ``<node>`` embedding and no other spelling exists (rulings 49-52; the S2
#: flatten). ``bindings`` is ONE token: its ``{ro, rw}`` table rides WHOLE, exactly as the
#: canonical ``agent.<node>.bindings`` key holds both arms. ⚑ ORDER IS NOT SIGNIFICANT (they are
#: distinct category names, so no re-root can shadow another). The WRITE side of this same fact is
#: :func:`_address`, which routes ``agent set <node> env.FOO=bar`` to exactly the table
#: :func:`level_table` reads back — the two are siblings in this module, which is what stops them
#: drifting.
_FLAT_AGENT_CATEGORIES: tuple[str, ...] = (
    "bindings", "caches", "seeded", "common", "synced", "masks", "secret_path", "env",
)

#: EVERY table the file's ROOT may hold: what the record models, MERGED with what the cascade
#: reads.
#:
#: ⚑⚑ THIS SET *IS* THE REFUSAL RULE, and that is why there is no second list of refused names to
#: keep in step with it: a dict-valued root key that is not in here is a nested ``self.<sub>:``
#: sub-table, and ``self`` is NOT A KEY — it SUBSTITUTES to ``agent.<agent>``, so
#: ``self.<sub>.<x>`` READS ``agent.<agent>.<sub>.<x>``, a key that cannot exist because
#: ``agent.claude`` does not contain a ``claude`` level (*"That would be agent.claude.claude"* /
#: *"never ever ever"*). The argument is UNIFORM over any ``<sub>``, which is why the literal
#: ``default`` refuses on the same line and why the agent file has no spelling for the all-agents
#: tier AT ALL (that tier is the SYSTEM file's ``agent: default:`` table).
#:
#: ⚑ The IDENTITY keys stay in the set deliberately: a malformed dict-valued ``name:`` is a
#: mistyped scalar, not a nested sub-table, and it keeps its old handling (``load`` coerces it and
#: the carrier never captures it) rather than becoming a refusal about nesting.
_ROOT_TABLES: Final[frozenset[str]] = _MODELED_KEYS | frozenset(_FLAT_AGENT_CATEGORIES)

#: The categories that ride :attr:`AgentConfig.category_tables` OPAQUELY — every flat category the
#: record does NOT model as a field of its own. ONE set for both ends of the round trip, so a
#: modelled table can neither be captured into the carrier (load) nor clobbered from it (write).
_CARRIED_CATEGORIES: Final[frozenset[str]] = frozenset(_FLAT_AGENT_CATEGORIES) - _MODELED_KEYS

#: The categories ``agent set`` can actually WRITE, and so the only ones a cure may name that verb
#: for. The dest-keyed families take a LIST value the verb cannot express (it would store a dotted
#: literal), and a message must never prescribe a verb that does not work — the same rule
#: ``config_keys``' retired-bind cure follows. Their cure is the hand-edit alone.
_VERB_WRITABLE_CATEGORIES: Final[frozenset[str]] = frozenset({"env", "secret_path"})

#: What a cure renders for a category whose refused table is EMPTY (nothing to quote): a sample
#: ``(key, value)`` for ONE entry. The dest-keyed families all share one entry shape, so they take
#: :data:`_DEST_KEYED_PLACEHOLDER` rather than a row each.
_CATEGORY_PLACEHOLDER: Final[dict[str, tuple[str, str]]] = {
    "env": ("<VAR>", "<value>"),
    "secret_path": ("<VAR>", "<host-path>"),
    "bindings": ("ro", "{<box-dest>: [<host-src>]}"),
}

#: The entry shape every dest-keyed category takes: the box DESTINATION is the key, the value is
#: ``[<host-src>[, <options>]]``.
_DEST_KEYED_PLACEHOLDER: Final[tuple[str, str]] = ("<box-dest>", "[<host-src>]")


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
    * ``bindings.{ro,rw}.*`` are still WRITTEN into the DISCRIMINATED ``self.<node>.*``
      sub-table here — and that is now a DEFECT with a slot, not a shape.  ⚑ S2 flattened
      the READ side: :func:`level_table` reads ``bindings`` FLAT with every other category
      and REFUSES the nested sub-table by name, so a value written through this arm lands
      where nothing reads it.  S3 flattens this arm to match, red-then-green; it is not
      "tidied" here, because a silent repair inside another pass is exactly how a
      behaviour change hides.

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


def file_spelling(*segments: str) -> str:
    """The agent file's OWN spelling of *segments*, under the root — ``self.env``, ``self.claude``.

    For the message surfaces that must QUOTE the file at a user: a cure naming the FLAT table to
    hand-edit (one segment), and a refusal naming the NESTED table it found (the shape the user
    actually wrote, so two).  Empty segments are dropped, which is what lets a caller pass an
    optional tail without a branch of its own.
    """
    return ".".join((_ROOT, *(s for s in segments if s)))


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
    removes run_args, all state keys, and every category table), then prune the now-empty root
    table.  Sparse write: no default keys re-materialized
    ([[settings-must-map-to-keystore-key]]).

    The COUNT is what ``agent reset --all`` reports, in the same terms the other scopes'
    ``reset_all`` uses: each removed root key counts once.

    ⚑ THE ``node`` BRANCH BELOW IS DEAD AND *node* IS UNUSED SINCE THE FLATTEN (S2): a root key
    equal to the node is a nested sub-table, which :func:`_refuse_nested_tables` now refuses, so
    no file this verb can be pointed at has one.  Both retire at S3 with the write side — kept
    here so the removal is a change of its own rather than a rider on the read flatten.
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

    ⚑ IT RUNS THE SAME REFUSAL THE CASCADE DOES (:func:`_refuse_nested_tables`), and that is the
    point: two readers of ONE file must not disagree about what the file means.  Before this,
    ``load`` accepted a nested sub-table the launch refused, so ``agent show`` described a shape
    that could not start a box.
    """
    cfg = AgentConfig()
    if not path.exists():
        return cfg

    data = load_doc(path)

    agent_sec = data.get(_ROOT, {})
    if not isinstance(agent_sec, dict):
        agent_sec = {}
    _refuse_nested_tables(agent_sec, node=None, path=path)
    cfg.name = str(agent_sec.get("name", ""))
    raw_args = agent_sec.get("run_args", [])
    cfg.run_args = [str(a) for a in raw_args] if isinstance(raw_args, list) else []

    # Flat state = the SCALAR agent-state knobs. Exclude identity keys AND any
    # dict-valued entry: every CATEGORY table (env, secret_path, bindings, …) is a
    # dict and is NOT flat state — those ride ``_agent_partial``, not the
    # ``_agent_state_partial`` state channel.
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
    # The CATEGORY tables the record does not model as fields of its own (bindings,
    # caches, seeded, common, synced, masks).  Carried OPAQUELY: a load→write round
    # trip that did NOT carry them would silently DROP a user's binds.
    # ⚑ NO LIVE CALLER MAKES THAT ROUND TRIP TODAY (measured — see the
    # ``AgentConfig`` docstring); the carry is a guard, not a running guarantee.
    cfg.category_tables = {
        k: dict(v)
        for k, v in agent_sec.items()
        if k in _CARRIED_CATEGORIES and isinstance(v, dict)
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
    # The opaquely-carried CATEGORY tables re-emitted — sparse (an empty table is
    # dropped, parity with the categories above); see :func:`load` and the
    # ``AgentConfig`` class docstring.  ⚑ ONE set guards BOTH ends: a modelled table
    # can neither be captured into the carrier nor clobber its own emission from
    # there, and nothing the carrier holds can be a shape :func:`load` would refuse.
    for category, table in cfg.category_tables.items():
        if table and category in _CARRIED_CATEGORIES:
            agent_sec[category] = dict(table)

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
    category: str | None, sub_key: str, *, var: str, value: str
) -> str:
    """The ARM-APPROPRIATE fix for a refused ``self.<sub>:`` sub-table (rulings 49-52).

    ⚑ THE EXPLANATION IS UNIFORM (alias expansion) BUT THE CURES ARE NOT, which is the whole
    reason this is a function and not one message. The flat ``self: <category>:`` table is
    re-rooted for the ACTIVE layer ONLY, so the all-agents tier has no agent-file spelling to
    move to at all — it is written in the SYSTEM file as ``agent: default: <category>:``. Sending
    an all-agents value to the flat table would silently NARROW it to one node, so the default
    arm must NOT name ``agent set``. Both routes measured live (2026-08-14).

    *category* is ``None`` when the refused sub-table holds nothing that is a category at all
    (state knobs, a typo, another node's name): there is no table to point at, so the cure is the
    rule itself.
    """
    from kanibako.settings.config_keys import AGENT_DEFAULT_SUB

    if category is None:
        # ⚑ It must not prescribe the DELETION — the caller's closing line already does,
        # and a cure that also said "delete it" read as "delete the content", which is
        # the opposite of what to do with it.
        return (
            f"move what is inside it UP ONE LEVEL. The state knobs sit DIRECTLY under "
            f"`{_ROOT}:` (`model: opus`), and so does every category table;"
        )
    by_hand = (
        f"in the FLAT table (`{_ROOT}:` expands to `agent.{sub_key}`, so the "
        f"{category} table sits DIRECTLY under it):\n"
        f"    {_ROOT}:\n      {category}:\n        {var}: {value}"
    )
    if sub_key == AGENT_DEFAULT_SUB:
        return (
            f"the all-agents tier is written in the SYSTEM settings file, not the "
            f"agent file:\n"
            f"    agent:\n      default:\n        {category}:\n          {var}: {value}"
        )
    # ⚑ The verb is named ONLY where it works — see :data:`_VERB_WRITABLE_CATEGORIES`.
    if category in _VERB_WRITABLE_CATEGORIES:
        return (
            f"kanibako agent set {sub_key} {category}.{var}={value}\n"
            f"  — or by hand, {by_hand}"
        )
    return f"by hand, {by_hand}"


def _refused_category(sub_tbl: dict) -> str | None:
    """The first CATEGORY a refused sub-table holds, or ``None`` if it holds none.

    File order, not sorted: it names the table the user wrote first, which is the one they are
    most likely looking at.
    """
    return next((k for k in sub_tbl if k in _FLAT_AGENT_CATEGORIES), None)


def _refuse_nested_tables(
    root_tbl: dict, *, node: str | None, path: Path | None
) -> None:
    """RAISE when the agent file's ROOT holds a table that is not one of its own (rulings 49-52).

    ⚑⚑ ONE PREDICATE, over the ROOT: *any dict-valued root key outside* :data:`_ROOT_TABLES`
    *is a nested* ``self.<sub>:`` *sub-table and refuses by name*. It is not an enumeration of
    refused categories, and deliberately so — one rule closes the CATEGORY case, the STATE case (a
    scalar carrier a per-category loop cannot express: ``self: claude: model: opus``), the
    all-agents ``default`` arm, and every spelling nobody has thought of yet. The representation
    IS the enforcement, so there is no second list to drift.

    *node* is the agent whose FILE this is — it renders the ALIAS EXPANSION in the message and is
    never read; ``None`` renders the shape ``<agent>``.

    ⚑ PRESENCE, not truthiness: an empty ``claude: {}`` sub-table is still the spelling being
    refused. A BARE ``claude:`` leaf parses to ``None`` and is NOT refused here — it is not a
    table, carries nothing, and delivers nothing; ``load`` sweeps it into state as the scalar it
    parsed to, exactly as it does any other stray root leaf.
    """
    from kanibako.settings.config_keys import AGENT_DEFAULT_SUB

    agent = node or "<agent>"
    for sub_key, sub_val in root_tbl.items():
        if sub_key in _ROOT_TABLES or not isinstance(sub_val, dict):
            continue
        category = _refused_category(sub_val)
        spelling = file_spelling(sub_key, category or "")
        table = sub_val.get(category) if category else None
        table = table if isinstance(table, dict) else {}
        var_ph, value_ph = _CATEGORY_PLACEHOLDER.get(
            category or "", _DEST_KEYED_PLACEHOLDER,
        )
        var = sorted(str(k) for k in table)[0] if table else var_ph
        value = str(table[var]) if var in table else value_ph
        held = ", ".join(sorted(str(k) for k in sub_val)) or "(nothing)"
        # ⚑⚑ ONE EXPLANATION FOR EVERY ARM — ruling 50's alias semantics, not a redundancy
        # argument. ``self`` is not a key; it SUBSTITUTES to ``agent.<agent>``, so the spelling
        # expands to a key that cannot exist, and that is equally true of ``default``. Only the
        # HISTORY and the CURE split by arm, each for its own real reason.
        expansion = f"agent.{agent}.{sub_key}" + (f".{category}" if category else "")
        if sub_key == AGENT_DEFAULT_SUB:
            history = (
                "Refusing rather than running: it used to resolve as though it were "
                "the all-agents `agent.default.*` tier, which is a real tier — but one "
                "the SYSTEM file spells, not this one."
            )
        else:
            history = (
                f"Refusing rather than running: it used to resolve to the same "
                f"`agent.{sub_key}.*` keys as the flat tables, and in a file carrying "
                f"BOTH the flat one REPLACED it wholesale — every entry spelled only "
                f"here vanished without a word."
            )
        raise SettingsError(
            f"`{spelling}` is not a settings key, so kanibako will not read it.\n"
            f"`{_ROOT}:` is NOT a key — it is an ALIAS that substitutes to "
            f"`agent.{agent}`. So `{spelling}` reads `{expansion}`, which is never "
            f"syntactically correct: `agent.{agent}` does not contain a `{sub_key}` "
            f"level. Nothing nests under `{_ROOT}:` but the categories themselves "
            f"(spec §0, closed keyspace).\n"
            f"Found in the {sub_key} sub-table of "
            f"{path if path is not None else '<agent settings>'}; it holds: "
            f"{held}.\n"
            f"{history}\n"
            f"  Fix: {_nested_agent_cure(category, sub_key, var=var, value=value)}\n"
            f"  then delete the `{file_spelling(sub_key)}` table from "
            f"{path if path is not None else 'the agent settings file'}."
        )


def level_table(
    raw: Any, *, sub_key: str, node: str | None = None, path: Path | None = None
) -> AgentFileLevel:
    """The RAW table one agent-tier level reads out of *raw*, under its TRUE §2d name.

    *sub_key* selects the TIER, not a sub-table: since the flatten (S2) every category is read
    FLAT off the root, so the ACTIVE tier is the file's own tables and the all-agents ``default``
    tier is STRUCTURALLY EMPTY — the file has no spelling for it at all (the SYSTEM file's
    ``agent: default:`` table is that tier's route). The two agent levels are still kept SEPARATE
    (spec §2) and merge by their true §2d names — NO bare-``agent`` collapse. A missing root table
    yields an EMPTY table. *path* and *node* only render the refusal message (the file's name, and
    the agent ``self`` expands to); neither is read.

    ⚑ THE REFUSAL RUNS FIRST, over the WHOLE root — see :func:`_refuse_nested_tables`.
    """
    from kanibako.settings.config_keys import AGENT_DEFAULT_SUB

    agent = raw.get(_ROOT) if isinstance(raw, dict) else None
    if not isinstance(agent, dict):
        return AgentFileLevel(sub_key, {})
    _refuse_nested_tables(agent, node=node, path=path)
    # ⚑ ``self`` IS ``agent.<active-node>``, so EVERY category lives at the file's TOP level —
    # re-root them for the ACTIVE layer ONLY, never the all-agents ``default`` (they are THIS
    # node's, not every agent's). Without this a category is not in the cascade at all: the
    # launch secret export saw no agent-scope secret_path and mounted no token, and an
    # ``agent.<node>.env.<VAR>`` was no snapshot leaf, so it reached the box only on a private
    # under-layer BELOW every collapsed slot and never saw the expand pass (llm-docs).
    # ⚑ ``bindings`` rides as ONE table, ``{ro: …, rw: …}`` whole: the canonical
    # ``agent.<node>.bindings`` key holds both arms, so re-rooting the token re-roots the pair.
    node_tbl: dict = {}
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

    ⚑ EVERY producer of a behaviour level goes through here (S1b): the five ``start.py`` sites that
    used to hand ``build_launch_snapshot`` a bare ``dict(agent_cfg.state)``, plus its own
    ``agent_path`` load. ``settings_launch._agent_state_partial`` takes the level and reads its
    node — so the node a table merges under is no longer a second, uncross-checked argument.
    """
    if not state:
        return None
    return AgentFileLevel(node, dict(state))
