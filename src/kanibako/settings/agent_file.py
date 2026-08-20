"""The per-agent settings file's SHAPE — the ONLY module that spells its root table."""

# ⚑⚑ WHY THIS MODULE EXISTS [spec:15-21, "self"].  ``self`` is NOT a key: it is a
# FILE-SURFACE ALIAS that substitutes to ``agent.<agent>``.  Everything past this boundary
# traffics in the ACTUAL agent reference, and this is the only place a ``self`` string appears in
# shipped source.
#
# ⚑ THE SPLIT WITH ``settings_assemble`` IS DELIBERATE AND LOAD-BEARING: this module produces the
# file's RAW table (:class:`AgentFileLevel`) and never touches ``KeyStore``.  Cutting the seam at
# the SHAPE rather than at the level keeps the import edge one-way — a boundary that imported the
# assembler would close a cycle.  Provenance: llm-docs.

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
#: ``settings_assemble._BEHAVIOR_TABLE_SHAPES``, whose rows are uniform ``(prefix, depth)`` pairs
#: and so cannot take a slot or a level.
#: ⚑ NOT AN INVITATION — a second raw-walk caller means the walk itself belongs in here.
ROOT_SECTIONS: Final[tuple[str, ...]] = (_ROOT,)

# Every schema-owned (MODELLED) key — the ones :class:`AgentConfig` holds as fields of its own.
# 🛑 A CATEGORY MUST NEVER BE ADDED HERE without both an ``AgentConfig`` field and a ``save``
# emission: load would capture it out of the opaque carrier and write would never put it back,
# which is a silent data-loss shape.
_MODELED_KEYS = IDENTITY_KEYS | frozenset({"env", "secret_path", "transform_settings"})

#: EVERY category the per-agent file stores FLAT under ``self`` — ``self`` IS ``agent.<node>``, so
#: there is no second ``<node>`` embedding ([spec:15-21, "self"]; the S2 flatten).
#: ``bindings`` is ONE token: its ``{ro, rw}`` table rides WHOLE. ⚑ ORDER IS NOT SIGNIFICANT. It
#: is ALSO :func:`_read_address`'s category set, which is what stops the value's storage shape and
#: the cascade's read of it from drifting apart.
_FLAT_AGENT_CATEGORIES: tuple[str, ...] = (
    "bindings", "caches", "seeded", "common", "synced", "masks", "secret_path", "env",
)

#: EVERY table the file's ROOT may hold: what the record models, MERGED with what the cascade
#: reads.
#: ⚑⚑ THIS SET *IS* THE REFUSAL RULE — a dict-valued root key not in here is a nested
#: ``self.<sub>:`` sub-table and refuses by name, so there is no second list of refused names to
#: keep in step with it. UNIFORM over any ``<sub>``, ``default`` included.
#: ⚑ The IDENTITY keys stay in the set deliberately: a malformed dict-valued ``name:`` is a
#: mistyped scalar, not a nested sub-table, and keeps its old handling.
_ROOT_TABLES: Final[frozenset[str]] = _MODELED_KEYS | frozenset(_FLAT_AGENT_CATEGORIES)

#: The categories that ride :attr:`AgentConfig.category_tables` OPAQUELY — every flat category the
#: record does NOT model as a field. ONE set for both ends of the round trip, so a modelled table
#: can neither be captured into the carrier (load) nor clobbered from it (write).
_CARRIED_CATEGORIES: Final[frozenset[str]] = frozenset(_FLAT_AGENT_CATEGORIES) - _MODELED_KEYS

#: The categories ``agent set`` can actually WRITE, and so the only ones a cure may name that verb
#: for — a message must never prescribe a verb that does not work. The dest-keyed families' cure
#: is the hand-edit alone.
#: ⚑ IT IS THE SAME FACT :func:`_write_address` ROUTES ON, spelled once for both: these two are
#: the only categories holding a SCALAR per name (``env.<VAR>`` / ``secret_path.<VAR>``).
_VERB_WRITABLE_CATEGORIES: Final[frozenset[str]] = frozenset({"env", "secret_path"})

#: Every ROOT key whose VALUE IS A TABLE — derived, so it cannot drift from the shape the file
#: actually holds: everything the root may carry EXCEPT the two identity fields. ⚑ It answers ONE
#: question — "can a SCALAR be written AT this key?" — and the answer is no for all of them: an
#: entry inside one of these tables is DATA, never a key segment of its own.
_TABLE_VALUED_KEYS: Final[frozenset[str]] = _ROOT_TABLES - IDENTITY_KEYS

#: What a cure renders for a category whose refused table is EMPTY (nothing to quote): a sample
#: ``(key, value)`` for ONE entry. The dest-keyed families share
#: :data:`_DEST_KEYED_PLACEHOLDER` rather than taking a row each.
_CATEGORY_PLACEHOLDER: Final[dict[str, tuple[str, str]]] = {
    "env": ("<VAR>", "<value>"),
    "secret_path": ("<VAR>", "<host-path>"),
    "bindings": ("ro", "{<box-dest>: [<host-src>]}"),
}

#: Every dest-keyed category's entry shape: the box DESTINATION is the key, the value is
#: ``[<host-src>[, <options>]]``.
_DEST_KEYED_PLACEHOLDER: Final[tuple[str, str]] = ("<box-dest>", "[<host-src>]")


# ---------------------------------------------------------------------------
# The two carriers — a SLOT (one value) and a LEVEL (one cascade tier)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AgentFileSlot:
    """WHERE one per-node value lives: a file and the key TAIL.

    ⚑⚑ IT CARRIES NO ``sections``/``leaf``, AND THAT IS THE WHOLE POINT (P3/P4).  The address is
    produced INSIDE :func:`read_leaf` / :func:`write_leaf` / :func:`remove_leaf` and is
    unavailable to a caller: the violation is not forbidden, it is unrepresentable.

    ⚑⚑ A FROZEN DATACLASS, NEVER A ``NamedTuple``.  A NamedTuple keeps ``isinstance(x, tuple)``
    True and every ``path, sections, leaf = route`` unpacking silently working at the WRONG arity
    — the same-arity shape flip that passes green while the meaning changes.

    ⚑ IT CARRIES NO ``node`` EITHER, SINCE S3: the node picks the FILE (``slot_for`` still takes
    it) and nothing else — ``self`` IS that node, so no address depends on whose file it is.
    """

    path: Path
    tail: str


@dataclass(frozen=True)
class AgentFileLevel:
    """ONE cascade tier read out of the agent file: its §2d discriminator and its RAW table.

    *node* is the discriminator the tier merges under (``default`` or the active agent), NOT
    necessarily the agent whose file this is.  *table* is raw YAML: no ``KeyStore``, no bind
    parsing, no precedence (that is ``settings_assemble``'s half of the seam).
    """

    node: str
    table: dict


# ---------------------------------------------------------------------------
# The file ADDRESS — spelled once, produced only in here
# ---------------------------------------------------------------------------

def _read_address(tail: str) -> tuple[tuple[str, ...], str]:
    """Map a per-agent-file key TAIL to the ``(sections, leaf)`` it is READ from.

    *tail* is the part of a canonical per-node agent key AFTER the ``agent.<node>.`` prefix
    (``model``, ``env.FOO``, ``bindings.ro./box/share``, ``caches./h/uv``).

    ⚑⚑ THE PARTITION RULE, AND IT IS THE WHOLE OF IT: the FIRST segment is the CATEGORY;
    ``bindings`` — and only ``bindings`` — then takes an ARM; EVERYTHING after that is ONE
    DESTINATION.  A dest is DATA (a guest-side path, dots and all), so it is never split and never
    re-joined: two :meth:`str.partition` calls, never ``split(".")``.  ⚑ The primitives underneath
    are dotted-leaf-safe: *leaf* is a literal dict key.

    ⚑ THE FALLTHROUGH IS LOAD-BEARING: a tail whose head is not a category is a FLAT root leaf
    (``model``, ``name``, ``run_args``) and reads ``(root,) / tail`` — including a dotted one,
    which lands on a literal dotted key rather than being exploded.
    """
    category, sep, rest = tail.partition(".")
    if not sep or category not in _FLAT_AGENT_CATEGORIES:
        return (_ROOT,), tail
    if category == "bindings":
        arm, arm_sep, dest = rest.partition(".")
        if arm_sep:
            return (_ROOT, category, arm), dest
    return (_ROOT, category), rest


def _write_address(tail: str) -> tuple[tuple[str, ...], str]:
    """Map a per-agent-file key TAIL to the ``(sections, leaf)`` a SCALAR is WRITTEN at.

    ⚑⚑ NARROWER THAN :func:`_read_address` BY CONSTRUCTION, AND THAT IS THE POINT (P3/P4).  The
    file holds exactly three kinds of scalar: a FLAT root leaf (``model``, ``name``, ``run_args``),
    an ``env.<VAR>`` and a ``secret_path.<VAR>`` (:data:`_VERB_WRITABLE_CATEGORIES`).  Every other
    category is DEST-KEYED — its entries are box destinations INSIDE its value — so there is no
    address to produce and this raises rather than inventing one.

    ⚑ THE RAISE IS A BACKSTOP, NOT THE USER-FACING REFUSAL: every write caller gates first and
    names the key itself, because a refusal owes the user a cure this function cannot phrase.
    Reaching here means a caller skipped its gate — the VALUE-SHAPE one
    (:func:`table_value_error`) or the CLOSED-KEYSPACE one (``config_keys.agent_write_key_error``).
    """
    head, sep, rest = tail.partition(".")
    if not sep and not _is_table_valued(tail):
        return (_ROOT,), tail
    if sep and head in _VERB_WRITABLE_CATEGORIES:
        return (_ROOT, head), rest
    raise SettingsError(
        f"'{tail}' has no scalar slot in the agent settings file. Its caller must "
        f"refuse the key by name before asking for a write address."
    )


def _is_table_valued(tail: str) -> bool:
    """Does *tail* name a whole TABLE of the agent file (so no scalar can live AT it)?

    ⚑ ``env.<VAR>`` / ``secret_path.<VAR>`` are the ONLY exception: those two categories hold a
    SCALAR per name.  Every other category — and ``transform_settings``, and a bare ``env:`` —
    IS the value.
    """
    head, sep, _rest = tail.partition(".")
    if sep and head in _VERB_WRITABLE_CATEGORIES:
        return False
    return head in _TABLE_VALUED_KEYS


def table_value_error(tail: str, *, path: Path, verb: str) -> str | None:
    """Why *tail* takes no scalar ``agent set`` / ``agent reset``, or ``None`` when it does.

    The VALUE-SHAPE half of the verb's gate (the D-7 cure): ``transform_settings``, ``masks`` and
    the dest-keyed category tables all hold a MAP, so a scalar written at one is a wrong SHAPE,
    not a wrong value — and until this refused, a scalar ``transform_settings`` crashed every
    subsequent :func:`load`, i.e. every launch, list, info and show.

    ⚑ SET AND RESET TAKE IT ALIKE; the hand-edit is the honest cure for both.  (``agent reset
    --all`` still drops them wholesale — it is the file-wide verb, not a per-key one.)
    ⚑ ``file_spelling(tail)`` takes *tail* WHOLE — it JOINS under the root and never splits, so a
    dotted arm (``bindings.ro``) renders as itself.
    """
    if not _is_table_valued(tail):
        return None
    return (
        f"Error: '{tail}' holds a TABLE, not a scalar, so it cannot be {verb} from "
        f"the command line — its entries are DATA inside the table, not keys of "
        f"their own.\n"
        f"  Fix: edit the `{file_spelling(tail)}` table of {path} directly; the "
        f"launch reads it from there."
    )


def file_spelling(*segments: str) -> str:
    """The agent file's OWN spelling of *segments*, under the root — ``self.env``, ``self.claude``.

    For the message surfaces that must QUOTE the file at a user, and never with a literal.  Empty
    segments are dropped, which is what lets a caller pass an optional tail without a branch of
    its own.
    """
    return ".".join((_ROOT, *(s for s in segments if s)))


def slot_for(agents_root: Path, node: str, tail: str) -> AgentFileSlot:
    """The :class:`AgentFileSlot` for *node*'s *tail* under *agents_root*.

    *node* picks the FILE and is not carried any further — see :class:`AgentFileSlot`.
    """
    return AgentFileSlot(agent_settings_path(agents_root, node), tail)


def read_leaf(slot: AgentFileSlot) -> str | None:
    """The value STORED at *slot*, or ``None`` when absent / no file.

    ⚑ Straight through :func:`~kanibako.settings.config_io.read_stored_leaf` — its two rendering
    conventions (bools lowercase, a stored ``""`` reading as ``None``) are load-bearing for every
    ``get``, so this must NOT re-render on top of them.
    """
    sections, leaf = _read_address(slot.tail)
    return read_stored_leaf(slot.path, sections, leaf)


def write_leaf(slot: AgentFileSlot, value: object) -> None:
    """Write *value* at *slot*, creating intermediate tables (sparse read-modify-write).

    ⚑ Through :func:`_write_address`, which is NARROWER than the read side and raises on a
    dest-keyed tail — the caller gates first.
    """
    sections, leaf = _write_address(slot.tail)
    write_nested_key(slot.path, sections, leaf, value)


def remove_leaf(slot: AgentFileSlot) -> bool:
    """Remove the value at *slot*, pruning emptied tables; True if one was there.

    ⚑ A remove is a WRITE and takes :func:`_write_address` for it — reset and set must not
    disagree about where a value lives.
    """
    sections, leaf = _write_address(slot.tail)
    return remove_nested_key(slot.path, sections, leaf)


def clear_overrides(path: Path) -> int:
    """Drop every user override from the file at *path*, PRESERVING ``name``; return the count.

    Sparse: from the root table, every key EXCEPT ``name``, then prune the now-empty root table.
    No default keys re-materialized ([[settings-must-map-to-keystore-key]]).

    The COUNT is part of the contract, in the same terms the other scopes' ``reset_all`` uses:
    EACH REMOVED ROOT KEY COUNTS ONCE, whatever it holds — a category table counts as the one
    override it is.
    """
    data = load_doc(path)
    count = 0
    agent_sec = data.get(_ROOT)
    if isinstance(agent_sec, dict):
        for k in [k for k in agent_sec if k != "name"]:
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

    ⚑ IT RUNS THE SAME REFUSAL THE CASCADE DOES (:func:`_refuse_nested_tables`): two readers of
    ONE file must not disagree about what the file means.  Before this, ``load`` accepted a nested
    sub-table the launch refused, so ``agent show`` described a shape that could not start a box.
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

    # Flat state = the SCALAR agent-state knobs. Exclude every key the record MODELS
    # as a field of its own, and any dict-valued entry: a CATEGORY table is a dict
    # and is NOT flat state — those ride ``_agent_partial``, not the
    # ``_agent_state_partial`` state channel.
    # ⚑ ``_MODELED_KEYS``, NOT ``IDENTITY_KEYS`` (S3/D-7): the two tests differ only
    # for a MALFORMED file, and the narrower one swept a modelled field's garbage
    # into state (llm-docs).
    # ⚑ A ``None`` value is KEPT as ``None`` (2026-08-17 ruling), never coerced
    # through ``str()``: that turned a ``model: null`` into the four-byte string
    # ``"None"``, a bogus model id the launch cascade took as real.
    cfg.state = {
        k: (v if v is None else str(v))
        for k, v in agent_sec.items()
        if k not in _MODELED_KEYS and not isinstance(v, dict)
    }
    # env: VAR -> value, read DIRECTLY from the root's ``env`` table.  Carried for the
    # ``agent info`` / ``show`` / ``get`` READS; the launch reads the same table
    # through the cascade, never off this field (MBR-1 P3).
    # ⚑ ISINSTANCE-GUARDED, like every modelled table below (S3/D-7): the READ side
    # stays permissive about a wrong SHAPE on purpose — the reads are how a user SEES
    # a broken file. The WRITE side refuses it (``table_value_error``).
    env_sub = agent_sec.get("env", {})
    cfg.env = {
        k: str(v) for k, v in env_sub.items()
    } if isinstance(env_sub, dict) else {}
    # secret_path: VAR -> host PATH pointer, read DIRECTLY from the root's
    # ``secret_path`` table (spec §2a SECRET category).  A plain string path; the
    # file's CONTENTS (the secret) are never persisted here nor read — they are
    # ro-mounted + exported IN-BOX only at launch.
    # ⚑ A ``None`` value is KEPT as ``None`` (2026-08-17 ruling): it means "this VAR
    # is deliberately keyless", a DECLARED third state, not a malformed second one;
    # see ``AgentConfig.secret_path``.
    secret_sub = agent_sec.get("secret_path", {})
    cfg.secret_path = {
        k: (v if v is None else str(v)) for k, v in secret_sub.items()
    } if isinstance(secret_sub, dict) else {}
    transform_sub = agent_sec.get("transform_settings", {})
    cfg.transform_settings = (
        dict(transform_sub) if isinstance(transform_sub, dict) else {}
    )
    # The CATEGORY tables the record does not model as fields of its own.  Carried
    # OPAQUELY: a load→write round trip that did NOT carry them would silently DROP a
    # user's binds.  ⚑ NO LIVE CALLER MAKES THAT ROUND TRIP TODAY (measured — see the
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
    # secret_path (spec §2a SECRET category) is stored DIRECTLY under the root — the
    # SAME first-class category location ``config set agent.<node>.secret_path.<VAR>``
    # writes and :func:`level_table` reads. Only materialized when non-empty (sparse).
    if cfg.secret_path:
        agent_sec["secret_path"] = dict(cfg.secret_path)
    # Sparse write — an EMPTY category is not materialized, or a phantom ``{}`` would
    # be counted as an override by ``agent reset --all``
    # ([[settings-must-map-to-keystore-key]]).
    if cfg.transform_settings:
        agent_sec["transform_settings"] = dict(cfg.transform_settings)
    if cfg.env:
        agent_sec["env"] = dict(cfg.env)
    # The opaquely-carried CATEGORY tables re-emitted — sparse; see :func:`load`.
    # ⚑ ONE set guards BOTH ends: a modelled table can neither be captured into the
    # carrier nor clobber its own emission from there, and nothing the carrier holds
    # can be a shape :func:`load` would refuse.
    for category, table in cfg.category_tables.items():
        if table and category in _CARRIED_CATEGORIES:
            agent_sec[category] = dict(table)

    data: dict = {
        _ROOT: agent_sec,
    }
    # The settings file lives inside the per-agent store dir; ensure that dir exists.
    path.parent.mkdir(parents=True, exist_ok=True)
    dump_doc(path, data)


# ---------------------------------------------------------------------------
# The CASCADE view — one file, two tiers, and the refusals that guard the shape
# ---------------------------------------------------------------------------

def _nested_agent_cure(
    category: str | None, sub_key: str, *, var: str, value: str
) -> str:
    """The ARM-APPROPRIATE fix for a refused ``self.<sub>:`` sub-table [spec:15-21, "self"].

    ⚑ THE EXPLANATION IS UNIFORM (alias expansion) BUT THE CURES ARE NOT, which is why this is a
    function and not one message. The all-agents tier has no agent-file spelling at all — it is
    written in the SYSTEM file as ``agent: default: <category>:`` — so sending an all-agents value
    to the flat table would silently NARROW it to one node, and that arm must NOT name
    ``agent set``. Both routes measured live; llm-docs sets out the three arms.

    *category* is ``None`` when the refused sub-table holds nothing that is a category at all
    (state knobs, a typo, another node's name): there is no table to point at, so the cure is the
    rule itself.
    """
    from kanibako.settings.config_keys import AGENT_DEFAULT_SUB

    if category is None:
        # ⚑ It must not prescribe the DELETION — the caller's closing line already does,
        # and a cure that also said "delete it" read as "delete the content".
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

    File order, not sorted: it names the table the user wrote first.
    """
    return next((k for k in sub_tbl if k in _FLAT_AGENT_CATEGORIES), None)


def _refuse_nested_tables(
    root_tbl: dict, *, node: str | None, path: Path | None
) -> None:
    """RAISE when the agent file's ROOT holds a table that is not its own [spec:15-21, "self"].

    ⚑⚑ ONE PREDICATE, over the ROOT: *any dict-valued root key outside* :data:`_ROOT_TABLES` *is
    a nested* ``self.<sub>:`` *sub-table and refuses by name*. Not an enumeration, deliberately —
    the representation IS the enforcement, so there is no second list to drift.

    *node* is the agent whose FILE this is — it renders the ALIAS EXPANSION in the message and is
    never read; ``None`` renders the shape ``<agent>``.

    ⚑ PRESENCE, not truthiness: an empty ``claude: {}`` sub-table is still the spelling being
    refused. A BARE ``claude:`` leaf parses to ``None`` and is NOT refused here — it is not a
    table, carries nothing, and delivers nothing; ``load`` sweeps it into state as the scalar it
    parsed to.
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
        # ⚑⚑ ONE EXPLANATION FOR EVERY ARM — the alias semantics of [spec:15-21, "self"],
        # not a redundancy argument: the spelling expands to a key that cannot exist, and that is
        # equally true of ``default``. Only the HISTORY and the CURE split by arm.
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
    tier is STRUCTURALLY EMPTY (the SYSTEM file's ``agent: default:`` table is that tier's route).
    The two agent levels are still kept SEPARATE (spec §2) and merge by their true §2d names — NO
    bare-``agent`` collapse. A missing root table yields an EMPTY table. *path* and *node* only
    render the refusal message; neither is read.

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
    # ``agent.<node>.env.<VAR>`` was no snapshot leaf (llm-docs).
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
    state: "Mapping[str, str | None] | None", *, node: str
) -> AgentFileLevel | None:
    """The agent file's FLAT behaviour state as a DISCRIMINATED level, or ``None`` if empty.

    The per-agent file stores behaviour FLAT (``model`` — already per-agent), not under the
    sub-tables the cascade merges by.  The discriminator is the file's OWN node and is attached
    HERE, at the boundary, not carried undiscriminated through the launch and attached at
    snapshot build.

    ⚑ EVERY producer of a behaviour level goes through here (S1b), and
    ``settings_launch._agent_state_partial`` reads the level's node — so the node a table merges
    under is no longer a second, uncross-checked argument.

    ⚑⚑ AND IT IS WHERE THE FORWARD-COMPAT PASSTHROUGH CLOSES (S3, D-5's other end): an undeclared
    scalar in the file used to ride into the launch snapshot VERBATIM.  The refusal is LAUNCH-ONLY
    on purpose — ``agent list`` / ``info`` read ``cfg.state`` directly and the repair verbs never
    call :func:`load`, so a poisoned file still LISTS, still DISPLAYS, and can still be fixed;
    only starting a box on it refuses, by name.
    """
    if not state:
        return None
    _refuse_undeclared_state(state, node=node)
    return AgentFileLevel(node, dict(state))


def _refuse_undeclared_state(state: "Mapping[str, str | None]", *, node: str) -> None:
    """RAISE on the first agent-file state key that is not a declared key (spec §0).

    ⚑ THE PLUGIN UNION IS LOAD-BEARING, not a nicety: ``config_keys.agent_key_reason`` unions the
    leaves the installed targets DECLARE, and without it a legitimate ``agent.goose.provider``
    would refuse a working box at launch.
    """
    from kanibako.settings.config_keys import agent_key_reason

    for key in state:
        reason = agent_key_reason(node, key)
        if reason is None:
            continue
        raise SettingsError(
            f"the agent settings file for '{node}' carries '{key}', which is not a "
            f"settings key: {reason}.\n"
            f"kanibako will not start a box on it — an undeclared key has no "
            f"meaning to give the box, and carrying it through would be the very "
            f"'anything goes' behaviour the closed keyspace replaces.\n"
            f"  Fix: remove `{file_spelling(key)}` from agents/{node}/settings.yaml "
            f"(or correct the spelling); 'kanibako agent info {node}' still lists "
            f"what the file holds."
        )
