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

from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Any, Callable, Final, Iterable, Iterator

from kanibako.settings.agent_config import (
    AgentConfig,
    agent_settings_path,
)
from kanibako.settings.config_io import (
    count_leaves,
    dump_doc,
    load_doc,
    read_stored_leaf,
    remove_nested_key,
    render_stored_scalar,
    stored_leaf_object,
    write_nested_key,
)
from kanibako.settings.settings_categories import CATEGORY_FAMILY_ROOTS
from kanibako.settings.settings_drops import cascade_drop_set, contained_scopes
from kanibako.settings.settings_resolve import (
    SettingsError,
    check_bind_tables,
    normalize_bind_dest,
)
from kanibako.utils import deep_merge

#: The per-agent file's ROOT table — the file's self-reference, spelled ONCE, HERE.
_ROOT: Final[str] = "self"

#: The SCOPE this file sits at: its root expands to ``agent.<node>`` [spec:15-21, "self"].
FILE_SCOPE: Final[str] = "agent"

#: The scopes the file CONTAINS (``workset``, ``box``): spec §0 makes their tables inputs,
#: merged defaults-down (Q85), read like those tables in any other settings file.
_CONTAINED: Final[tuple[str, ...]] = contained_scopes(FILE_SCOPE)

#: The top-level tables the file CONTRIBUTES to the cascade — the ONE list every reader takes
#: (:func:`contributed_tables`): its root, its own-scope ``agent:`` table (Q92) and its
#: contained-scope tables (Q85).
_CONTRIBUTED: Final[frozenset[str]] = frozenset({_ROOT, FILE_SCOPE, *_CONTAINED})

#: The root as a nested-walk PREFIX, for the ONE raw-walk site that needs it:
#: ``settings_assemble._BEHAVIOR_TABLE_SHAPES``, whose rows are uniform ``(prefix, depth)`` pairs
#: and so cannot take a slot or a level.
#: ⚑ NOT AN INVITATION — a second raw-walk caller means the walk itself belongs in here.
ROOT_SECTIONS: Final[tuple[str, ...]] = (_ROOT,)

# Every schema-owned (MODELED) key — the ones :class:`AgentConfig` holds as fields of its own.
# 🛑 A CATEGORY MUST NEVER BE ADDED HERE without both an ``AgentConfig`` field and a ``save``
# emission: load would capture it out of the opaque carrier and write would never put it back,
# which is a silent data-loss shape.
# ⚑ ``run_args`` SITS HERE WITH THE REST, not behind a set borrowed from elsewhere: it is a
# modeled field like the other three, and what makes it the odd one is only that its value is
# not a table (:data:`_SCALAR_WRITABLE_KEYS`).  Leaving it out would sweep the stored argv list
# into :attr:`AgentConfig.state` as the ``str()`` of a list, beside the field that already holds
# it properly.
_MODELED_KEYS: Final[frozenset[str]] = frozenset({
    "run_args", "env", "secret_path", "transform_settings",
})

#: EVERY category the per-agent file stores FLAT under ``self`` — ``self`` IS ``agent.<node>``, so
#: there is no second ``<node>`` embedding ([spec:15-21, "self"]; the S2 flatten).
#: ``bindings`` is ONE token: its ``{ro, rw}`` table rides WHOLE. ⚑ ORDER IS NOT SIGNIFICANT. It
#: is ALSO :func:`_read_address`'s category set, which is what stops the value's storage shape and
#: the cascade's read of it from drifting apart.
#: ⚑ NOT the dest-keyed set (``settings_keyspace.TERMINAL_CATEGORY_TAILS``): ``env`` and
#: ``secret_path`` are stored flat too, so this is EVERY family root, derived from its owner.
_FLAT_AGENT_CATEGORIES: tuple[str, ...] = tuple(sorted(CATEGORY_FAMILY_ROOTS))

#: EVERY table the file's ROOT may hold: what the record models, MERGED with what the cascade
#: reads.
#: ⚑⚑ THIS SET *IS* THE REFUSAL RULE — a dict-valued root key not in here is a nested
#: ``self.<sub>:`` sub-table and refuses by name, so there is no second list of refused names to
#: keep in step with it. UNIFORM over any ``<sub>``, ``default`` included.
#: ⚑ It therefore holds one key whose value is NOT a table (:data:`_SCALAR_WRITABLE_KEYS`), and
#: that is deliberate: a malformed dict-valued ``run_args:`` is a mistyped scalar, not a nested
#: sub-table, and keeps its old handling.
_ROOT_TABLES: Final[frozenset[str]] = _MODELED_KEYS | frozenset(_FLAT_AGENT_CATEGORIES)

#: The categories that ride :attr:`AgentConfig.category_tables` OPAQUELY — every flat category the
#: record does NOT model as a field. ONE set for both ends of the round trip, so a modeled table
#: can neither be captured into the carrier (load) nor clobbered from it (write).
_CARRIED_CATEGORIES: Final[frozenset[str]] = frozenset(_FLAT_AGENT_CATEGORIES) - _MODELED_KEYS

#: The categories ``agent set`` can actually WRITE, and so the only ones a cure may name that verb
#: for — a message must never prescribe a verb that does not work. The dest-keyed families' cure
#: is the hand-edit alone.
#: ⚑ IT IS THE SAME FACT :func:`_write_address` ROUTES ON, spelled once for both: these two are
#: the only categories holding a SCALAR per name (``env.<VAR>`` / ``secret_path.<VAR>``).
_VERB_WRITABLE_CATEGORIES: Final[frozenset[str]] = frozenset({"env", "secret_path"})

#: Every ROOT key that TAKES A SCALAR from the command line — the direct answer to "can a SCALAR
#: be written AT this key?", and the reason :data:`_ROOT_TABLES` is not all tables.
#: ⚑ It is NOT a claim about the STORED shape: ``run_args`` takes the scalar and stores it as
#: argv WORDS, so :data:`_LIST_VALUED_KEYS` is a SUBSET of this set — every list-valued key takes
#: the one string it was split from. A plain-scalar modeled key would belong here and NOT there.
_SCALAR_WRITABLE_KEYS: Final[frozenset[str]] = frozenset({"run_args"})

#: Every ROOT key whose VALUE IS A TABLE — the complement, so it cannot drift from the shape the
#: file actually holds. ⚑ The answer to the question above is NO for all of them: an entry inside
#: one of these tables is DATA, never a key segment of its own.
_TABLE_VALUED_KEYS: Final[frozenset[str]] = _ROOT_TABLES - _SCALAR_WRITABLE_KEYS

#: Every ROOT key the file stores as a LIST OF ARGV WORDS rather than as the one string the
#: command line hands over.
#:
#: ⚑⚑ A DIFFERENT QUESTION FROM :data:`_TABLE_VALUED_KEYS`, and the contrast is the point: a
#: table-valued key takes NO scalar at all and is refused by name; one of these TAKES the
#: scalar and stores it as words.  So the translation exists, and it lives HERE with the rest
#: of the shape, at BOTH ends — :func:`stored_leaf_shape` splits it in and
#: :func:`stored_leaf_text` joins it back out, for the SCOPE settings files (``config_interface``)
#: exactly as for this one (:func:`write_leaf` / :func:`read_leaf`).
#:
#: ⚑⚑ WHY BOTH ENDS ARE IN ONE PLACE (P10).  The split used to live in ``agent set``'s own
#: writer and nowhere else, so the file had two write routes disagreeing about one shape:
#: ``config set agent.<node>.run_args="--c --d"`` stored the STRING, :func:`record` read a list
#: or nothing, and the value was DISCARDED — reported set at rc 0, delivered to no launch.
#: A reader who changes the split must see the join, and the reverse.
_LIST_VALUED_KEYS: Final[frozenset[str]] = frozenset({"run_args"})

# The SUBSET the comment above STATES, pinned rather than restated (P15) — a member added here
# and not there cannot be a live shape: a key that takes no scalar from the command line is
# refused by NAME, so there is nothing for the split to translate. The relation is pinned by
# a test, not an import-time assert, in tests/test_settings/test_agent_leaf_shape.py.

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

    ⚑ IT CARRIES THE ``node`` AGAIN (Q92), and only to find the value's SPELLING: the file's
    ``agent:`` table may spell the node's own settings as ``agent: <node>:`` beside ``self:``, so
    every read and write of the slot addresses whichever of the two holds it
    (:func:`_spelled_sections`).
    """

    path: Path
    tail: str
    node: str


@dataclass(frozen=True)
class AgentFileLevel:
    """ONE cascade tier read out of the agent file: its §2d discriminator and its RAW table.

    *node* is the discriminator the tier merges under (``default`` or the active agent), NOT
    necessarily the agent whose file this is.  *table* is raw YAML: no ``KeyStore``, no bind
    parsing, no precedence (that is ``settings_assemble``'s half of the seam).  *path* is the
    file the table was read from, or ``None`` when the caller did not say; it travels WITH the
    table so a refusal about one of its values can name the file ([R147], read time).

    *scope* is the file's top-level ``agent:`` table, RAW (Q92: read like that table in any
    settings file), already judged by :func:`_contribution`; ``settings_assemble`` parses it.
    *contained* is the file's ``workset:`` / ``box:`` tables, RAW, likewise (Q85).

    The file's ROOT is split by KEY (:func:`level_table`): *table* holds every category key,
    whatever it holds, and *state* every other key.
    """

    node: str
    table: dict
    path: Path | None = None
    scope: dict = field(default_factory=dict)
    state: dict = field(default_factory=dict)
    contained: dict = field(default_factory=dict)


def scalar_family_of(tail: str) -> str | None:
    """The SCALAR family *tail* names — ``"env"`` / ``"secret_path"`` — else ``None``.

    :data:`_VERB_WRITABLE_CATEGORIES`' question asked of a READ: the two categories
    holding a scalar per NAME are exactly the two §2a declares scalar, so a caller
    applying the non-scalar refusal (``settings_categories``) asks THIS rather than
    matching the tail itself.  ⚑ The partition is :func:`_read_address`'s — the first
    segment is the category and everything after it is ONE name.

    ⚑ A TAIL, NOT A KEY, because the slot carries no node (:class:`AgentFileSlot`): the
    caller that HAS the canonical key is the one that phrases the refusal, since §2a
    requires the whole key be named and a tail is not one.
    """
    category, sep, rest = tail.partition(".")
    return category if sep and rest and category in _VERB_WRITABLE_CATEGORIES else None


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
    (``model``, ``label``, ``run_args``) and reads ``(root,) / tail`` — including a dotted one,
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
    file holds exactly three kinds of scalar: a FLAT root leaf (``model``, ``label``, ``run_args``),
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
    subsequent :func:`record`, i.e. every launch, list, info and show.

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


#: "Not there" for :func:`_spelled_sections`, which must tell it from a stored ``None``.
_UNSET: Final[object] = object()


def slot_for(agents_root: Path, node: str, tail: str) -> AgentFileSlot:
    """The :class:`AgentFileSlot` for *node*'s *tail* under *agents_root*.

    *node* picks the FILE, and rides on to find the value's spelling (:class:`AgentFileSlot`).
    """
    return AgentFileSlot(agent_settings_path(agents_root, node), tail, node)


def _spelled_sections(
    slot: AgentFileSlot, sections: tuple[str, ...], leaf: str,
) -> tuple[str, ...]:
    """*sections* as the file SPELLS them for *slot*: under ``self:``, or under ``agent: <node>:``.

    ``self`` IS ``agent.<node>``, so a value may sit under either spelling (Q92) and the file's
    readers refuse one written under both (:func:`_refuse_two_spellings`). The own-node spelling
    is taken only when it HOLDS *leaf*; otherwise ``self:`` — where a new value is written.
    """
    doc = load_doc(slot.path) if slot.path.exists() else None
    scope = doc.get(FILE_SCOPE) if isinstance(doc, dict) else None
    if not isinstance(scope, dict):
        return sections
    own_id = _node_identity(slot.node)
    for seg, table in scope.items():
        if not isinstance(seg, str) or not isinstance(table, dict):
            continue
        if _node_identity(seg) != own_id:
            continue
        spelled = (FILE_SCOPE, seg, *sections[1:])
        if stored_leaf_object(slot.path, spelled, leaf, default=_UNSET) is not _UNSET:
            return spelled
    return sections


# ---------------------------------------------------------------------------
# The ARGV translation — one string on the command line, a list of words on disk
# ---------------------------------------------------------------------------

def argv_words(value: str) -> list[str]:
    """The argv WORDS in one command-line *value*, as the file stores them.

    ⚑ DELIBERATELY :meth:`str.split`, NOT ``shlex.split``.  It is what the split has always
    done, and adding quote handling would change the MEANING of values already on disk
    rather than fix one.  A word that must contain a space is hand-edited into the list.

    ⚑ PUBLIC because the LAUNCH splits too: the behavior table hands ``run_args`` over as the
    command-line string :func:`argv_text` joined (a stored list and a hand-written string
    therefore arrive identically), and the consumer splits it back with THIS function.  A
    second splitter anywhere would be a second answer to "what is a word".
    """
    return value.split()


def argv_text(words: Iterable[object]) -> str:
    """*words* as the ONE command-line string they were split from — :func:`argv_words`
    read backwards, for every surface that shows a stored argv list to a user."""
    return " ".join(str(w) for w in words)


def stored_leaf_text(tail: str, value: object) -> str | None:
    """The text a USER is shown for *value* stored at *tail*, or ``None`` when this module owns
    no rule for the pair and the caller must keep the rendering it already had.

    ⚑⚑ THE PUBLIC ENTRY FOR EVERY SURFACE THAT DISPLAYS A STORED LEAF IT DID NOT READ THROUGH
    :func:`read_leaf`.  Each of them coerced with a bare ``str()`` and so printed the Python repr
    ``['--a', '--b']`` at a user.  They ask HERE instead of each learning which leaves are lists
    (P10) — the list of them is :data:`_LIST_VALUED_KEYS`, and it is nobody else's to restate.
    ⚑ NO ROSTER OF THOSE SURFACES LIVES HERE. The rule above IS the membership test; a census
    taken by READING them off missed several that a probe then found, and an inventory that is
    short reads as the whole set to anyone checking their new view against it.

    ⚑⚑ ``None`` MEANS "NOT MINE", NEVER "ABSENT", and that is why this answers only the shape
    question.  The callers' empty-and-bool idioms are not the same as each other — the three
    idioms the read verbs spell apart (spec §2h) against the launch table's raw ``str()`` of a
    bool its consumer re-coerces — so deciding them here would change values that are not argv.
    """
    if tail in _LIST_VALUED_KEYS and isinstance(value, list):
        return argv_text(value)
    return None


def stored_leaf_shape(tail: str, value: object) -> object:
    """*value* in the shape a FILE holds at *tail* — the argv split, or *value* unchanged.

    ⚑⚑ THE WRITE-SIDE TWIN OF :func:`stored_leaf_text`, and PUBLIC for the same reason.  The
    SCOPE settings files hold the §2d leaves too, written by routes that never reach
    :func:`write_leaf`, and each of them stored the STRING the command line handed over —
    ``[R169]``: one parser, two entry points, ONE stored shape.  They ask HERE rather than
    each learning which leaves are lists (P10).

    🛑 *tail* IS THE FILE TAIL, NEVER THE WRITTEN LEAF'S NAME, and that is the whole safety of
    this call.  A user may name an environment variable ``run_args``; its tail is the DOTTED
    ``env.run_args`` (:func:`_address`), which is in no leaf set, so the scalar they typed
    stays a scalar.  Keyed on the last segment instead, this would shell-split it.

    ⚑ ``None`` PASSES THROUGH: it is the ``--null`` suppression idiom (spec §2h), not an
    empty argv line, and splitting it would silently turn a suppression into ``[]``.
    """
    if tail in _LIST_VALUED_KEYS and isinstance(value, str):
        return argv_words(value)
    return value


def stored_leaf_display(tail: str, value: object) -> str:
    """The text shown for *value* stored at *tail*: :func:`stored_leaf_text`, falling back to
    the scalar convention for anything this module owns no rule for.

    ⚑⚑ THE COMPOSED PAIR, PUBLIC SO NO SURFACE HAS TO COMPOSE IT AGAIN (P10).  Every verb
    that shows a stored leaf owes BOTH halves — the shape rule for the leaves this module
    owns, and ``config_io``'s ONE scalar convention for the rest — and a surface that took
    only the second printed the Python repr ``['--a', '--b']`` at a user while one that took
    only the first printed ``None`` for a stored null.
    ⚑ TOTAL, like the convention it falls back to: absence is reported by the READ, above any
    rendering, so a caller with no value to show must not call here at all.

    ⚑ AN EMPTY LIST RENDERS BLANK, and that is deliberate: a present ``run_args: []`` is the
    user's explicit "no arguments", so it is a VALUE and reads back as the empty command line
    it is.  :func:`stored_leaf_text` answers that blank itself, which is what keeps the
    fallback — where the empty STRING has its own spelling, ``""`` (spec §2h) — from giving
    one of kanibako's empty idioms to a shape that is not it.

    ⚑ A STRING at a list-valued *tail* renders through the scalar convention UNCHANGED — that
    is what the other write route stored before both routes agreed, and :func:`record` reads it
    the same way.
    """
    text = stored_leaf_text(tail, value)
    return render_stored_scalar(value) if text is None else text


def stored_leaf_value(slot: AgentFileSlot) -> object:
    """The RAW value the file holds at *slot*, UNRENDERED, or ``None`` when it holds none.

    ⚑⚑ FOR A DOOR THAT JUDGES THE VALUE, WHICH :func:`read_leaf` CANNOT SERVE: the renderings
    are deliberately not injective — a stored ``""`` and a stored ``'""'`` both read back
    ``""`` (spec §2h) — so a door deciding "did the user leave this empty?" must ask the
    object, never the text.
    🛑 ABSENT AND A PRESENT-``None`` BOTH ANSWER ``None`` HERE, and that is the whole reason
    this is not a ``get`` route: both mean "this file names no value at *slot*", which is
    what a FALLING-THROUGH door wants and exactly what a verb reporting "(not set)" must not
    be told.  That verb uses :func:`read_leaf`.
    """
    sections, leaf = _read_address(slot.tail)
    return stored_leaf_object(slot.path, _spelled_sections(slot, sections, leaf), leaf)


def read_leaf(slot: AgentFileSlot) -> str | None:
    """The value STORED at *slot*, or ``None`` when absent / no file.

    ⚑ Through :func:`~kanibako.settings.config_io.read_stored_leaf` — its rendering
    conventions (bools lowercase, and spec §2h's empty idioms each spelled apart) are
    load-bearing for every ``get``, so this must NOT re-render on top of them.  The one leaf
    whose stored shape is NOT a scalar hands its own renderer in instead
    (:func:`stored_leaf_display`); without it a ``get`` printed the Python repr ``['--e', '--f']``
    at the user.
    """
    sections, leaf = _read_address(slot.tail)
    return read_stored_leaf(
        slot.path, _spelled_sections(slot, sections, leaf), leaf,
        render=(
            partial(stored_leaf_display, slot.tail)
            if slot.tail in _LIST_VALUED_KEYS
            else render_stored_scalar
        ),
    )


def write_leaf(slot: AgentFileSlot, value: object) -> None:
    """Write *value* at *slot*, creating intermediate tables (sparse read-modify-write).

    ⚑ Through :func:`_write_address`, which is NARROWER than the read side and raises on a
    dest-keyed tail — the caller gates first.
    ⚑⚑ AND THROUGH :func:`stored_leaf_shape`, so EVERY write route lands the shape
    :func:`record` reads.  A caller must NOT pre-split: a second copy of that rule is the
    defect this closed.
    """
    sections, leaf = _write_address(slot.tail)
    write_nested_key(
        slot.path, _spelled_sections(slot, sections, leaf), leaf,
        stored_leaf_shape(slot.tail, value),
    )


def remove_leaf(slot: AgentFileSlot) -> bool:
    """Remove the value at *slot*, pruning emptied tables; True if one was there.

    ⚑ A remove is a WRITE and takes :func:`_write_address` for it — reset and set must not
    disagree about where a value lives.
    """
    sections, leaf = _write_address(slot.tail)
    return remove_nested_key(slot.path, _spelled_sections(slot, sections, leaf), leaf)


def clear_overrides(path: Path) -> int:
    """Drop every user override from the file at *path*; return the count.

    Sparse: every table the file contributes (:func:`contributed_tables`), then prune it. No
    default keys re-materialized ([[settings-must-map-to-keystore-key]]).

    ⚑⚑ IT REFUSES NOTHING — reset is the REPAIR DOOR. A file carrying a stray top-level key or an
    undeclared leaf that stops every other reader must still be resettable (the stray key itself
    stays, for the user to move or delete), so this asks the file only which tables to clear and
    count, never for a verdict (:func:`_contribution` is the verdict).

    ⚑ IT USED TO PRESERVE ONE KEY, ``name`` — the file's non-key identity field, which D8b
    retired (2026-09-15). Nothing in the file is exempt now: every root key IS an override, so
    preserving one would be preserving a user's setting from a verb whose whole promise is that
    it clears them. The widening is user-visible and documented in ``MIGRATION.md``.

    The COUNT is part of the contract, in the unit every scope's ``reset --all`` reports:
    EACH REMOVED LEAF COUNTS ONCE (``config_io.count_leaves``) — a category table counts its
    entries, a list counts as the one value it is, and so does a VALUE where a table goes
    (``self: null``), which the readers refuse and so must go too.
    """
    data = load_doc(path)
    count = 0
    for key, table in contributed_tables(data).items():
        count += count_leaves(table)
        del data[key]
    dump_doc(path, data)
    return count


# ---------------------------------------------------------------------------
# The WHOLE-FILE round trip (the ``agent`` verbs' own reads + the persona artifact)
# ---------------------------------------------------------------------------

def record(level: AgentFileLevel, *, node: str) -> AgentConfig:
    """The :class:`AgentConfig` record of agent *node*'s file, built from its ACTIVE *level*.

    *level* is :func:`level_table`'s output for the file's own node, so the record and the
    launch read ONE judged view of the file (``settings_assemble.agent_record`` reads it).
    The record holds BOTH spellings of the file's own node — ``self:`` and ``agent: <node>:``
    (:func:`_own_node_settings`) — as the launch reads both.

    ⚑ IT REFUSES every undeclared entry (:func:`_refuse_undeclared_state`, spec §0), over
    ``self:`` AND the ``agent:`` table's nodes, category contents included, so every reader of
    the record — ``agent show`` / ``info`` / ``list`` / ``get`` and the launch — gets the
    launch's verdict. The repair door is :func:`clear_overrides`, which never comes here.
    """
    cfg = AgentConfig()
    own = {**level.table, **level.state}
    agent_sec = _own_node_settings(own, level.scope, node=node)
    # ⚑ NO ``name`` READ, AND ITS ABSENCE IS THE POINT (D8b): the field is retired, so a
    # ``name:`` still in the file falls into ``cfg.state`` below like any other undeclared
    # entry and REFUSES by name at the end of this read.
    # ⚑⚑ A STORED STRING IS SPLIT, NOT DISCARDED, and that is what makes the write
    # routes' old disagreement recoverable without touching anyone's data.  This
    # reader took a list or NOTHING, so every ``run_args`` the ``config set
    # agent.<node>.run_args=…`` route wrote — verbatim, as a string — came back
    # empty: the CLI said "Set", ``agent show`` showed nothing and the launch got no
    # arguments.  Both routes write the list now (:func:`write_leaf`); reading the
    # string keeps the files that route ALREADY wrote working from the next command
    # on, and the next :func:`save` normalizes them.  It is a read rule, not a shim:
    # nothing writes a string here any more.
    # ⚑ A bare ``run_args:`` parses to ``None`` — "no arguments", never the word
    # "None"; anything else scalar is one word's worth of text and is split like one.
    # ⚑⚑ AN ABSENT KEY IS ``None``, NOT AN EMPTY LIST, AND THE MEMBERSHIP TEST IS WHAT
    # TELLS THEM APART (:class:`AgentConfig`, three-state).  The file surface always
    # kept them apart — ``read_leaf`` answers ``None`` for absent and ``""`` for a
    # stored ``[]`` — and the record collapsing them was harmless only while this file
    # was the argv's sole source.  It is not: ``agent.default.run_args`` reaches a
    # launch (`[R169]`), so an agent writing ``run_args: []`` to OPT OUT of that
    # default has to be distinguishable from one that never mentioned the key.
    if "run_args" not in agent_sec:
        cfg.run_args = None
    else:
        raw_args = agent_sec["run_args"]
        if isinstance(raw_args, list):
            cfg.run_args = [str(a) for a in raw_args]
        elif raw_args is None:
            cfg.run_args = []
        else:
            cfg.run_args = argv_words(str(raw_args))

    # Flat state = the SCALAR agent-state knobs. Exclude every key the record MODELS
    # as a field of its own, and any dict-valued entry: a CATEGORY table is a dict
    # and is NOT flat state — those ride ``_agent_partial``, not the
    # ``_agent_state_partial`` state channel.
    # ⚑ EVERY modeled key, not just the ones with a scalar slot (S3/D-7): the
    # narrower test differs only for a MALFORMED file, where it swept a modeled
    # field's garbage — ``env: oops`` — into state as an agent-state knob (llm-docs).
    # ⚑ A ``None`` value is KEPT as ``None`` (2026-08-17 ruling), never coerced
    # through ``str()``: that turned a ``model: null`` into the four-byte string
    # ``"None"``, a bogus model id the launch cascade took as real.
    cfg.state = {
        k: (v if v is None else str(v))
        for k, v in agent_sec.items()
        if k not in _ROOT_TABLES and not isinstance(v, dict)
    }
    # env: VAR -> value, read DIRECTLY from the root's ``env`` table.  Carried for the
    # ``agent info`` / ``show`` / ``get`` READS; the launch reads the same table
    # through the cascade, never off this field (MBR-1 P3).
    # ⚑ ISINSTANCE-GUARDED, like every modeled table below (S3/D-7), so the record
    # builds; a VALUE where ``env``'s table goes is then refused by name at the end of
    # this read (``agent.<node>.env`` is a namespace), as the launch refuses it.  Only
    # ``transform_settings`` — a declared key, whatever its shape — is coerced away
    # here; the WRITE side refuses a wrong shape (``table_value_error``).
    # ⚑⚑ A ``None`` value is KEPT as ``None``, exactly as ``cfg.secret_path`` below and
    # ``cfg.state`` above keep it, and for the same 2026-08-17 reason: it is the DECLARED
    # suppression state (spec §2h's present-``None``), not a malformed one. A bare
    # ``str()`` turned ``FOO:`` into the four-byte string ``"None"`` INSIDE the record —
    # after which no reader could tell it from a user who really wrote ``FOO: None``, and
    # the file fallback that answers ``null`` for every other category was never reached.
    # The two tables disagreed about one idiom while sitting two lines apart.
    env_sub = agent_sec.get("env", {})
    cfg.env = {
        k: (v if v is None else str(v)) for k, v in env_sub.items()
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
        k: dict(v) if isinstance(v, dict) else v
        for k, v in agent_sec.items()
        if k in _CARRIED_CATEGORIES
    }
    _refuse_undeclared_state(
        _undeclared_entries(own, level.scope, node=node), node=node, path=level.path,
    )
    return cfg


def _own_node_settings(own: dict, scope: Any, *, node: str) -> dict:
    """*own* (``self:``) with the ``agent:`` table's entry for *node* added: ONE agent's settings.

    ``self`` IS ``agent.<node>``, so both spellings set the one node the launch reads, and the
    record every ``agent`` verb displays holds both. They share no setting —
    :func:`_refuse_two_spellings` has refused one written twice — so the union loses nothing.
    """
    if not isinstance(scope, dict):
        return own
    own_id = _node_identity(node)
    for seg, other in scope.items():
        if isinstance(other, dict) and _node_identity(seg) == own_id:
            own = deep_merge(own, other)
    return own


def save(path: Path, cfg: AgentConfig) -> None:
    """Write an AgentConfig to a YAML file.

    ⚑ EVERY EMISSION IS SPARSE, so a freshly generated file's root table is EMPTY — that is
    the FILE-PURITY invariant, not an oversight: the file holds user intent, and a new agent
    has none. The one unconditional line was ``name``, which D8b retired.
    """
    agent_sec: dict = {}
    # Sparse, for the SAME reason as every emission below — and this one is the
    # DEFAULT state, so it was the widest phantom in the file: ``run_args`` was emitted
    # unconditionally, so every freshly seeded agent file carried ``run_args: []`` and
    # ``agent reset --all`` counted that empty list as an override the user never wrote.
    # ⚑ A PRESENT ``run_args: []`` IS STILL A VALUE (:func:`stored_leaf_display`) and is NOT at
    # risk here: all three :func:`save` callers write a file that does not yet exist
    # (``cli._ensure_initialized``, and start.py's two ``agent_cfg_dirty`` sites, which
    # are first-use only), so nothing round-trips a user's explicit empty list through
    # here.  ``agent set <node> run_args=""`` writes through :func:`write_leaf` and keeps
    # materializing the empty list.
    # ⚑⚑ THE GUARD IS TRUTHY WHILE THE RECORD IS THREE-STATE, and that is only safe
    # because of the sentence above — re-measured 2026-09-19: every ``generate_agent_config``
    # returns a bare :class:`AgentConfig`, whose ``run_args`` is now ``None``.  🛑 A FOURTH
    # CALLER, or a plugin that seeds ``run_args=[]``, MAKES THIS A DATA-LOSS SHAPE: it would
    # drop the one value that distinguishes "this agent takes no arguments" from "this file
    # says nothing".  Give it ``is not None`` at that point rather than re-deriving why it
    # was ever allowed to be truthy.
    if cfg.run_args:
        agent_sec["run_args"] = list(cfg.run_args)
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
    # The opaquely-carried CATEGORY tables re-emitted — sparse; see :func:`record`.
    # ⚑ ONE set guards BOTH ends: a modeled table can neither be captured into the
    # carrier nor clobber its own emission from there, and nothing the carrier holds
    # can be a shape :func:`record` would refuse. A VALUE (``caches: null``) is emitted as is.
    for category, table in cfg.category_tables.items():
        if table != {} and category in _CARRIED_CATEGORIES:
            agent_sec[category] = dict(table) if isinstance(table, dict) else table

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
    table, carries nothing, and delivers nothing; ``record`` sweeps it into state as the scalar it
    parsed to, and the undeclared-leaf check refuses it there by name.
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


def contributed_tables(raw: Any) -> dict:
    """The top-level tables of *raw* the cascade READS (:data:`_CONTRIBUTED`); judges nothing."""
    if not isinstance(raw, dict):
        return {}
    return {k: v for k, v in raw.items() if str(k) in _CONTRIBUTED}


def _refuse_stray_roots(raw: dict, *, node: str | None, path: Path | None) -> None:
    """RAISE on a key at the FILE's top level that the file neither contributes nor drops (spec §0).

    ⚑ THE FILE-LEVEL HALF OF THE CLOSED KEYSPACE. In the other settings files an unknown
    top-level entry rides into the launch snapshot, where the §0 audit can refuse it BY NAME; this
    file's partial is built from :func:`contributed_tables` alone, so a stray never reached that
    audit and vanished without a word — a ``model:`` written one level too high set nothing.

    ⚑⚑ THE TABLES THE CASCADE DROPS ARE NOT STRAYS. ``system:`` (§0 directional enforcement),
    ``meta:``, ``binding_derivations:`` and ``pref:`` (§2h) drop with a warning at assembly
    (:func:`~kanibako.settings.settings_drops.cascade_drop_set`); the non-launch readers see them
    here before any drop, so this passes them rather than refuse what the launch drops.
    ⚑ ``agent:``, ``workset:`` and ``box:`` ARE NOT STRAYS EITHER: they are READ (Q92, Q85,
    :data:`_CONTRIBUTED`).
    """
    agent = node or "<agent>"
    where = path if path is not None else "the agent settings file"
    passed = cascade_drop_set(FILE_SCOPE)
    for raw_key in raw:
        key = str(raw_key)
        if key in _CONTRIBUTED or key in passed:
            continue
        raise SettingsError(
            f"`{key}` at the top level of {where} is not a settings key, so kanibako "
            f"will not read the file.\n"
            f"This file holds its settings under `{_ROOT}:` — an ALIAS for "
            f"`agent.{agent}` — and nothing beside it is read but the "
            f"`{FILE_SCOPE}:`, `workset:` and `box:` tables (spec §0, closed keyspace). Refusing rather than running: a key "
            f"here used to be ignored without a word, so whatever it set never reached "
            f"a box.\n"
            f"  Fix: if `{key}` is one of this agent's settings, move it under "
            f"`{_ROOT}:`:\n    {_ROOT}:\n      {key}: …\n"
            f"  otherwise delete the `{key}` entry from {where}."
        )


def _contribution(raw: Any, *, node: str | None, path: Path | None) -> dict:
    """:func:`contributed_tables`, after refusing a stray (:func:`_refuse_stray_roots`).

    ⚑⚑ EVERY READER THAT JUDGES THE FILE COMES THROUGH HERE — the launch (:func:`level_table`)
    and the record (:func:`record`: ``agent show`` / ``info`` / ``list``) — so one file gets one
    verdict. The reset does not (:func:`clear_overrides`): it is the repair door.

    ⚑ THE FILE-SHAPE REFUSALS THE ``agent:`` TABLE BROUGHT (Q92) RUN HERE TOO, for that reason: a
    VALUE where its node tables go (:func:`_refuse_scope_value`), one node spelled twice in it
    (:func:`refuse_node_spelled_twice`), and one setting written under both ``self:`` and
    ``agent: <node>:`` (:func:`_refuse_two_spellings`, Q103).
    ⚑ And the dest-keyed bind maps get the settings tier's own checks, HERE because this file
    is read as a RAW node table and nothing downstream of it judges an entry (spec §2a) — in
    the contained-scope tables too, which the cascade reads like any other settings file.
    """
    if not isinstance(raw, dict):
        return contributed_tables(raw)
    _refuse_stray_roots(raw, node=node, path=path)
    tables = contributed_tables(raw)
    scope = tables.get(FILE_SCOPE)
    for token in (FILE_SCOPE, *_CONTAINED):
        _refuse_scope_value(tables, token, path=path)
    if isinstance(scope, dict):
        refuse_node_spelled_twice(scope, prefix=FILE_SCOPE, path=path)
    _refuse_node_values(tables, node=node, path=path)
    _refuse_two_spellings(tables, node=node, path=path)
    check_bind_tables(
        tables, root=_ROOT, scope=FILE_SCOPE, contained=_CONTAINED,
        where=str(path) if path else None,
    )
    return tables


def _refuse_scope_value(tables: dict, token: str, *, path: Path | None) -> None:
    """RAISE on a VALUE where the file's *token* scope table goes; absent passes.

    A scope holds tables, never a value (spec §0, closed keyspace) — merged as one, it would
    replace every other file's tables of that scope. A bare ``agent:`` / ``workset:`` /
    ``box:`` is the value ``null`` and refuses, as the system file's ``agent: null`` does.
    """
    if token not in tables:
        return
    value = tables[token]
    if isinstance(value, dict):
        return
    where = path if path is not None else "the agent settings file"
    held = "agent node tables (`agent: {<agent>: {…}}`)" if token == FILE_SCOPE else (
        f"`{token}.*` settings (`{token}: {{<key>: …}}`)"
    )
    raise SettingsError(
        f"`{token}: {render_stored_scalar(value)}` at the top level of {where} is not a settings key: "
        f"`{token}` is a scope, and it holds {held}, never a value (spec §0, closed "
        f"keyspace).\n"
        f"  Fix: delete the `{token}` entry from {where}, or give it a table."
    )


def _refuse_node_values(tables: dict, *, node: str | None, path: Path | None) -> None:
    """RAISE on ``self:`` or a node of the ``agent:`` table holding a VALUE (or nothing), not a table.

    ``agent.<node>`` names an agent TIER, not a key (spec §2d) — the launch's §0 audit refuses it
    so — and this is where every other reader gets the same verdict. ⚑ The file's OWN node is the
    sharp case: beside a non-empty ``self:`` (which IS ``agent.<node>``), ``agent: {claude: 5}``
    or a bare ``claude:`` writes that node a second time, and merged it would replace every
    setting under ``self:`` without a word; the message says so.
    """
    own = tables.get(_ROOT)
    where = path if path is not None else "the agent settings file"
    if _ROOT in tables and not isinstance(own, dict):
        raise SettingsError(
            f"`{_ROOT}` in {where} holds {render_stored_scalar(own)}, but `{_ROOT}:` IS "
            f"`agent.{node or '<agent>'}`, which names an agent's settings table, not a key "
            f"(spec §2d).\n"
            f"  Fix: delete the `{_ROOT}` entry from {where}, or give it a table of this "
            f"agent's settings."
        )
    scope = tables.get(FILE_SCOPE)
    if not isinstance(scope, dict):
        return
    own_id = _node_identity(node) if node is not None else None
    for seg, other in scope.items():
        if isinstance(other, dict):
            continue
        twice = ""
        if own_id is not None and isinstance(own, dict) and own and _node_identity(seg) == own_id:
            twice = (
                f" It also writes agent '{node}' a second time: `{_ROOT}:` IS "
                f"`agent.{node}`, and neither may silently win (spec §0) — merged, this value "
                f"would replace every setting under `{_ROOT}:`."
            )
        raise SettingsError(
            f"`{FILE_SCOPE}.{seg}` in {where} holds {render_stored_scalar(other)}, but `{FILE_SCOPE}.{seg}` names "
            f"an agent's settings table, not a key (spec §2d).{twice}\n"
            f"  Fix: delete the `{FILE_SCOPE}.{seg}` entry from {where}, or give it a table "
            f"of that agent's settings."
        )


def _refuse_two_spellings(tables: dict, *, node: str | None, path: Path | None) -> None:
    """RAISE when ``self:`` and the ``agent:`` table's own-node entry set one setting (Q103).

    ``self`` IS ``agent.<node>``, so ``self: {model: x}`` beside ``agent: {<node>: {model: y}}``
    writes ONE setting twice in one file; neither may silently win. The node matches as the
    cascade folds it (Q87: ``Claude:`` is ``claude``). Settings as the cascade merge sees them
    (:func:`_setting_leaves`): ``env.A`` beside ``env.B`` merges, and so do two dests of one bind
    arm, but ``/a/`` and ``/a`` are ONE dest. A PREFIX is the same setting too: ``model`` beside
    ``model.x`` writes ``model`` once as a value and once as a table. Every pair is named AS
    WRITTEN.

    An own-node entry that is not a table never reaches here: :func:`_refuse_node_values` has
    refused it first.
    """
    own, scope = tables.get(_ROOT), tables.get(FILE_SCOPE)
    if node is None or not isinstance(own, dict) or not own or not isinstance(scope, dict):
        return
    own_id = _node_identity(node)
    where = path if path is not None else "the agent settings file"
    for seg, other in scope.items():
        if not isinstance(other, dict) or _node_identity(seg) != own_id:
            continue
        mine, theirs = _setting_leaves(own), _setting_leaves(other)
        clashes = [
            (mine[a], theirs[b]) for a in mine for b in theirs
            if a[:len(b)] == b or b[:len(a)] == a
        ]
        if not clashes:
            continue
        pairs = "\n".join(
            f"  `{file_spelling(a)}` and `{FILE_SCOPE}.{seg}.{b}`" for a, b in clashes
        )
        raise SettingsError(
            f"{where} sets the same setting twice — `{_ROOT}:` IS `agent.{node}`, so each "
            f"pair below is ONE key written in two spellings, and neither may silently "
            f"win (spec §0):\n{pairs}\n"
            f"  Fix: keep one spelling of each and remove the other from {where}."
        )


def refuse_node_spelled_twice(table: dict, *, prefix: str, path: Path | None) -> None:
    """RAISE when two keys of the agent node table *table* spell ONE node (spec §0).

    Two spellings are one node when the node they reach agrees — separator AND case
    (``nav+Claude`` beside ``nav℘claude``), not only the case-folded spelling; neither may
    silently win. ONE carrier for every reader of a node table: the file's own readers
    (:func:`_contribution`, over its ``agent:`` table) and the cascade's fold
    (``settings_assemble._fold_node_table``). *prefix* is the table's dotted address.
    """
    from kanibako.agent_ref import display_agent_ref

    where = str(path) if path is not None else "<settings>"
    identity: dict[Any, Any] = {}
    for seg in table:
        ident = _node_identity(seg)
        if ident in identity:
            # ⚑ *ident* is the canonical node the two agree on, named as the user writes it; the
            # two KEYS stay as the file spells them, being the entries to delete.
            raise SettingsError(
                f"'{prefix}.{identity[ident]}' and '{prefix}.{seg}' in settings file {where} "
                f"are ONE agent node ('{display_agent_ref(ident)}') spelled twice; neither may "
                f"silently win. Keep one spelling and remove the other (spec §0: an agent's "
                f"node is lowercase)."
            )
        identity[ident] = seg


def _node_identity(segment: Any) -> Any:
    """The node an ``agent.<segment>`` spelling reaches, case folded (Q87); else *segment*."""
    from kanibako.agent_ref import agent_address_node, agent_segment_case
    from kanibako.errors import ConfigError

    if not isinstance(segment, str):
        return segment
    try:
        return agent_address_node(agent_segment_case(segment))
    except ConfigError:
        return segment


def _setting_leaves(table: dict, trail: tuple[str, ...] = ()) -> dict[tuple[str, ...], str]:
    """Every SETTING a node table writes: its merge address → its dotted spelling AS WRITTEN.

    A table is descended to its leaves, with two stops the cascade merge makes too: a
    table-valued agent key (``transform_settings``, §2d) is ONE setting, whole; and in a
    dest-keyed bind category each DEST is one, compared as its canonical guest path.
    """
    from kanibako.settings.settings_keyspace import (
        BIND_CATEGORIES,
        TABLE_VALUED_AGENT_LEAVES,
    )

    leaves: dict[tuple[str, ...], str] = {}
    for raw_key, value in table.items():
        key = str(raw_key)
        here = (*trail, key)
        if (not trail and key in TABLE_VALUED_AGENT_LEAVES) or not isinstance(value, dict):
            leaves[here] = ".".join(here)
        elif ".".join(here) in BIND_CATEGORIES:
            for dest in value:
                leaves[(*here, normalize_bind_dest(str(dest)))] = ".".join((*here, str(dest)))
        else:
            for address, spelled in _setting_leaves(value, here).items():
                leaves[address] = spelled
    return leaves


def level_table(
    raw: Any, *, sub_key: str, node: str | None = None, path: Path | None = None
) -> AgentFileLevel:
    """The RAW table one agent-tier level reads out of *raw*, under its TRUE §2d name.

    *sub_key* selects the TIER, not a sub-table: since the flatten (S2) every category is read
    FLAT off the root, so the ACTIVE tier's *table* is the file's own tables and the all-agents
    ``default`` tier's *table* is EMPTY — ``self:`` has no spelling for that tier.  The file's
    ``agent:`` table (Q92) does: it rides RAW on EVERY tier's level as *scope*, and which of its
    nodes a tier takes — ``default`` for the all-agents tier — is the cascade's call, made after
    its node fold (``settings_assemble``).  The two agent levels are still kept SEPARATE (spec §2)
    and merge by their true §2d names — NO bare-``agent`` collapse. A missing root table yields an
    EMPTY *table* (its *scope* still rides). *path* only renders the refusal messages; *node*
    renders them too and is the node the two-spelling check matches (:func:`_contribution`).
    The active tier's root is split by KEY (:class:`AgentFileLevel`).

    ⚑ THE REFUSALS RUN FIRST: over the file's TOP level (:func:`_contribution`), then over the
    WHOLE root (:func:`_refuse_nested_tables`).
    """
    from kanibako.settings.config_keys import AGENT_DEFAULT_SUB

    tables = _contribution(raw, node=node, path=path)
    scope = tables.get(FILE_SCOPE) or {}
    contained = (
        {} if sub_key == AGENT_DEFAULT_SUB
        else {k: tables[k] for k in _CONTAINED if k in tables}
    )
    agent = tables.get(_ROOT)
    if not isinstance(agent, dict):
        return AgentFileLevel(sub_key, {}, path, scope, contained=contained)
    _refuse_nested_tables(agent, node=node, path=path)
    if sub_key == AGENT_DEFAULT_SUB:
        return AgentFileLevel(sub_key, {}, path, scope)
    # ⚑ ``self`` IS ``agent.<active-node>``, so EVERY category lives at the file's TOP level —
    # re-rooted for the ACTIVE layer ONLY, never the all-agents ``default`` (they are THIS
    # node's, not every agent's). ``bindings`` rides as ONE table, ``{ro: …, rw: …}`` whole.
    # ⚑⚑ SPLIT BY KEY, NOT BY VALUE: a category key holding a ``null`` or a scalar is still
    # that category's, so it reaches the cascade (``caches: null`` RESETS the category,
    # spec §2a) or the §0 refusal, the way the same value does in any other settings file.
    table = {k: v for k, v in agent.items() if str(k) in _FLAT_AGENT_CATEGORIES}
    state = {k: v for k, v in agent.items() if str(k) not in _FLAT_AGENT_CATEGORIES}
    return AgentFileLevel(sub_key, table, path, scope, state, contained)


def state_level(
    cfg: "AgentConfig | None", *, node: str, path: Path | None = None,
) -> AgentFileLevel | None:
    """The agent file's BEHAVIOR as a DISCRIMINATED level, or ``None`` if it sets none.

    The per-agent file stores behavior FLAT (``model`` — already per-agent), not under the
    sub-tables the cascade merges by.  The discriminator is the file's OWN node and is attached
    HERE, at the boundary, not carried undiscriminated through the launch and attached at
    snapshot build.  *path* is the file *cfg* was read from, attached here for the same
    reason: the launch's read-time path check names it (:class:`AgentFileLevel`).

    ⚑ EVERY producer of a behavior level goes through here (S1b), and
    ``settings_launch._agent_state_partial`` reads the level's node — so the node a table merges
    under is no longer a second, uncross-checked argument.

    ⚑⚑ IT TAKES THE RECORD, NOT :attr:`AgentConfig.state`, AND THAT IS THE ``run_args`` CASCADE
    (`[R169]`).  ``run_args`` is a behavior leaf the RECORD models as a field of its own
    (:data:`_MODELED_KEYS`), so a level built from ``state`` alone dropped it and the file's argv
    reached a launch by a SECOND route — read straight off ``AgentConfig.run_args`` at the seam
    that builds the command line, where no ``agent.default.run_args`` could ever reach it.  Folded
    in here, the §2d pick does the override: a per-agent value REPLACES the any-agent default, the
    way every other ``agent.<agent>.<key> | agent.default.<key>`` row does.
    ⚑ It rides as the stored LIST, which ``effective_behavior`` renders through
    :func:`stored_leaf_text`; the consumer splits that string back with :func:`argv_words`.
    ⚑⚑ THE FOLD IS ``is not None``, NEVER A TRUTHY TEST, and that is the whole reason
    :attr:`AgentConfig.run_args` is three-state.  A present ``run_args: []`` is the user's
    explicit "no arguments" (:func:`stored_leaf_display` says the same of the display), so it must
    reach the cascade and SET the key — that is how an agent OPTS OUT of
    ``agent.default.run_args``.  A truthy test folds it in with the absent key and silently
    hands that agent the default it wrote the empty list to refuse.

    ⚑ ``transform_settings`` (a TABLE-valued agent key, §2d) rides here too, so its consumers
    read the cascade's answer, not the record's.

    ⚑ IT JUDGES NOTHING: an undeclared scalar in the file is refused when the file is READ
    (:func:`record`), so every reader — not the launch alone — refuses it by name.  The record
    arriving here from anywhere else is a plugin's generated one, whose state is empty.
    """
    if cfg is None:
        return None
    table: dict[str, object] = dict(cfg.state or {})
    if cfg.run_args is not None:
        table["run_args"] = list(cfg.run_args)
    if cfg.transform_settings:
        table["transform_settings"] = dict(cfg.transform_settings)
    if not table:
        return None
    return AgentFileLevel(node, table, path)


def _refuse_undeclared_state(
    entries: "Iterable[tuple[str, str, str]]", *, node: str, path: Path | None,
) -> None:
    """RAISE naming EVERY agent-file entry that is not a declared key (spec §0).

    Each of *entries* is ``(shown, spelled, reason)``, from :func:`_undeclared_entries`: how the
    message quotes the entry, where the cure points in the file, and why it is not a key.
    *node* is the file's own agent.  ⚑ EVERY entry, not the first, as the launch's
    ``settings_launch._refuse_undeclared_snapshot`` names them: the cure is a hand-edit, and one
    entry per attempt turns one edit into N.  An entry both passes find is named once.
    """
    found: dict[str, tuple[str, str]] = {}
    for shown, spelled, reason in entries:
        found.setdefault(spelled, (shown, reason))
    if not found:
        return
    lines = "\n".join(
        f"  carries '{shown}', which is not a settings key: {reason}."
        for shown, reason in found.values()
    )
    if len(found) == 1:
        (shown, reason), = found.values()
        head = (
            f"the agent settings file for '{node}' carries '{shown}', which is not a "
            f"settings key: {reason}."
        )
    else:
        head = (
            f"the agent settings file for '{node}' has {len(found)} entries that are not "
            f"settings keys:\n{lines}"
        )
    spelled = ", ".join(f"`{s}`" for s in found)
    raise SettingsError(
        f"{head}\n"
        f"kanibako will not start a box on the file or display it — an undeclared "
        f"key has no meaning to give a box, and carrying it through would be the "
        f"very 'anything goes' behavior the closed keyspace replaces.\n"
        f"  Fix: remove {spelled} from {path or 'the agent settings file'} (or correct the "
        f"spelling), or clear every override with "
        f"'kanibako agent reset {node} --all'."
    )


def _node_tables(
    own: Any, scope: Any, *, node: str,
) -> "list[tuple[str, dict, Callable[..., str]]]":
    """The file's node tables as ``(judged node, table, spelling)`` — ``self:``, then ``agent:``'s.

    ``self:`` is judged as *node*; each ``agent:`` node as the cascade folds it (Q87: ``Claude``
    is ``claude``). *spelling* turns a key tail into the file's own spelling of it. A node that
    is not a table holds nothing to judge (:func:`_refuse_node_values` has refused it).
    """
    from kanibako.agent_ref import agent_segment_case

    tables: list[tuple[str, dict, Callable[..., str]]] = []
    if isinstance(own, dict):
        tables.append((node, own, file_spelling))
    if isinstance(scope, dict):
        for seg, table in scope.items():
            if not isinstance(table, dict):
                continue
            judged = agent_segment_case(seg) if isinstance(seg, str) else str(seg)
            tables.append((judged, table, partial(_scope_spelling, seg)))
    return tables


def _scope_spelling(seg: Any, *tail: str) -> str:
    """The ``agent:`` table's spelling of *tail* under its node *seg*, as written."""
    return ".".join((FILE_SCOPE, str(seg), *tail))


def _undeclared_entries(
    own: Any, scope: Any, *, node: str,
) -> "Iterator[tuple[str, str, str]]":
    """Every entry of the file's node tables the LAUNCH refuses, as ``(shown, spelled, reason)``.

    Two passes, each the launch's own verdict, so the file's readers refuse what the launch does:

    1. every KEY of a node table but a category holding a TABLE, through
       ``config_keys.agent_key_reason`` — the verdict the launch takes from :func:`record`. A
       category holding a VALUE is judged as a key, so ``env: 5`` gets ``agent.<node>.env`` is a
       namespace, under ``self:`` as under ``agent: <node>:``;
    2. every PATH, category contents included, through the launch's whole-snapshot audit
       (``settings_keyspace.undeclared_store_paths`` over ``keyspace_verdict``): an undeclared
       bind arm (``bindings: {zz: …}``), a malformed ``env`` VAR, an ``agent: {self: {}}`` node.

    ⚑ ``self:``'s shown key stays BARE (``'model'``), as the record names it.
    """
    from kanibako.settings.config_keys import agent_key_reason
    from kanibako.settings.settings_keyspace import (
        render_store_path,
        undeclared_store_paths,
    )
    from kanibako.settings.settings_keyspace_probe import keyspace_verdict

    tables = _node_tables(own, scope, node=node)
    for judged, table, spelling in tables:
        for raw_key, value in table.items():
            key = str(raw_key)
            if key in _FLAT_AGENT_CATEGORIES and isinstance(value, dict):
                continue
            reason = agent_key_reason(judged, key)
            if reason is not None:
                shown = key if spelling is file_spelling else spelling(key)
                yield shown, spelling(key), reason
    for judged, table, spelling in tables:
        found = undeclared_store_paths(
            {judged: _str_keys(table)}, oracle=keyspace_verdict, prefix=(FILE_SCOPE,),
        )
        for segments, judgment in found:
            tail = render_store_path(segments[2:], max(judgment.key_len - 2, 0))
            yield render_store_path(segments, judgment.key_len), spelling(tail), judgment.note


def _str_keys(table: dict) -> dict:
    """*table* with every key as a string, as the cascade's ``KeyStore`` holds them."""
    return {
        str(k): _str_keys(v) if isinstance(v, dict) else v for k, v in table.items()
    }
