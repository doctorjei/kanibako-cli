"""Unified scope-category resolution (pure, additive).

The VOCABULARY of path delivery at a scope: which category names exist, what key
SHAPE each takes, the :class:`CategoryEntry` an emitted entry is, what its *delivery*
means, and the collision refusals a category set must satisfy.

THE NINE CATEGORIES, at every scope ``{system, agent, workset, box}`` — ``masks``,
``bindings.ro``, ``bindings.rw``, ``caches``, ``seeded``, ``common``, ``synced``,
``env``, ``secret_path``.  ``seeded`` and ``synced`` are file COPIES; ``env`` is an
environment variable; every other is a podman MOUNT that physically shadows whatever
is at the same path.  Key shapes, the TERMINAL / dest-keyed rules (R-5/R-6),
``secret_path``'s arm's-length delivery, the two precedence axes and the apply order:
``llm-docs/kanibako/settings/settings_categories.py.md``.

⚑ IT DOES NOT DISCOVER, RESOLVE OR EMIT — ``settings_launch.snapshot_category_entries``
walks the precedence levels and CONSTRUCTS every ``CategoryEntry``.  This one is
**pure**, importing only stdlib — an invariant, not a coincidence: a resolver import
would close a cycle, which is why emission sits at the launch seam.

⚑ Cross-category collision resolution (the spec §0 identical-dest TABLE) is NOT here
any more.  The by-dest reconcile was RETIRED at 6-R3 and THREE seams replaced it, each
holding the inputs its own rows need — the per-scope ``store_shape`` PRODUCER (the
refusals decidable within one scope, through the two public raisers below), the ASSEMBLY COLLAPSE
(``store_collapse``, the cross-scope pairs), and THIS module's launch-seam pair
:func:`secret_path_deliveries` and :func:`narrow_table_winners`, which answer the two
questions the collapse deliberately does not: what a ``secret_path`` dest delivers,
and what a NARROW resolve's own injected table mounts.  ⚑ The credential gate is none
of the three — a DELIVERY policy, not a collision rule (cutover step 4).

⚑ NO ROOT-JOIN, by rule.  Every ``host_src`` reaching this module already resolves ON
ITS OWN, because the ABSTRACT categories are rooted at DECLARATION.  A layer that
prefixed a root on the way to a mount is the shape §2a calls FORBIDDEN.
"""

from __future__ import annotations

import re
import textwrap
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final, Literal, Mapping, NoReturn

if TYPE_CHECKING:  # pragma: no cover - typing only
    from kanibako.settings.kb_store import Bind
    from kanibako.settings.keystore import KeyStore

# Delivery tags.
Delivery = Literal["COPY", "MOUNT", "ENV"]
COPY: Final[Delivery] = "COPY"
MOUNT: Final[Delivery] = "MOUNT"
ENV: Final[Delivery] = "ENV"

#: Where ``secret_path`` ro-mounts each host secret file, as ``{dir}/{VAR}`` (§2a).
#: ⚑ NOT under the box ``~`` home — it must stay disjoint from the home/workspace/
#: vault mounts and OUT of the ``~``-rooted depth-sort.
SECRET_MOUNT_DIR: Final[str] = "/run/kanibako/secrets"

# The bind-shaped categories — those whose value is a 2-/3-element
# ``[host_src, box_dest[, options]]`` tuple.  ``masks`` (a keyed list) and ``env``
# (a scalar) have bespoke key shapes handled separately below.
#
# ⚑ FOUR TUPLES, AND EACH ANSWERS A DIFFERENT QUESTION; the other three are SUBSETS
# of this one, and conflating any two is what the split exists to prevent (llm-doc).
# ⚑⚑ THE CLI WRITE ROUTE IS RETIRED FOR ALL SIX (DS-BL1 = (a)), and THE READ ROUTE IS
# THE TERMINAL KEY, NOT THE ENTRY.  Every retired spelling stays RECOGNISED so the
# verbs refuse it BY NAME.
#
# NOTE the order: ``bindings.ro``/``bindings.rw`` must precede a bare ``bindings``
# (there is none), longest-first, so every alternation below is unambiguous.
_BIND_CATEGORIES: Final[tuple[str, ...]] = (
    "bindings.ro", "bindings.rw", "caches", "seeded", "common", "synced",
)
#: The bind-shaped categories whose CATEGORY KEY IS TERMINAL — the whole of the key,
#: with a destination-keyed map for a value (R-5/R-6).  ALL SIX since 2026-08-08c.
#: ⚑ A MIRROR, NOT THE DEFINITION — ``settings_keyspace.TERMINAL_CATEGORY_TAILS`` owns
#: that; DERIVED rather than imported to keep this module stdlib-only, and PINNED
#: EQUAL by ``test_the_bind_shaped_terminal_mirror_cannot_drift``.
_TERMINAL_BIND_CATEGORIES: Final[tuple[str, ...]] = _BIND_CATEGORIES
#: Those that still have a PER-ENTRY DOTTED KEY — the derived complement, so
#: "terminal" has exactly one definition here.  ⚑ EMPTY since 2026-08-08c, a case
#: :data:`BIND_KEY_RE` handles EXPLICITLY.
_NON_TERMINAL_BIND_CATEGORIES: Final[tuple[str, ...]] = tuple(
    c for c in _BIND_CATEGORIES if c not in _TERMINAL_BIND_CATEGORIES
)
#: The bind-shaped categories a ``config set`` / ``config reset`` key may name.
#: **EMPTY — DS-BL1 = (a): every bind-shaped category is YAML-only.**  Kept, not
#: deleted: it is the ONE definition of "settable" :data:`RETIRED_BIND_CATEGORIES`
#: derives from.  ⚑ NOTHING may build a regex ALTERNATION from it: ``"|".join(())``
#: matches the EMPTY string and would accept ``system..foo``.
SETTABLE_BIND_CATEGORIES: Final[tuple[str, ...]] = ()

# The ABSTRACT categories — the three that let an author write a bare LEAF, rooted at
# DECLARATION under ``<scope-root>/<category>/`` (spec §2a).  ⚑ The rest
# (``bindings.{ro,rw}``, ``synced``) are CONCRETE and take NO root at any scope, so a
# relative source there is a defect, not a shorthand.
ABSTRACT_CATEGORIES: Final[tuple[str, ...]] = ("common", "caches", "seeded")

# spec §2a DECLARATION ROOTS — THE single copy of the spec's table.  ``{agent}`` is the
# only placeholder, because the agent tier is discriminated.
DECLARATION_ROOT_REF: Final[Mapping[str, str]] = {
    "system": "@config.data",
    "agent": "@meta.agent.{agent}.path",
    "workset": "@meta.workset.path",
    "box": "@meta.box.path",
}

_DELIVERY: dict[str, Delivery] = {
    "masks": MOUNT,
    "bindings.ro": MOUNT,
    "bindings.rw": MOUNT,
    "caches": MOUNT,
    "seeded": COPY,
    "common": MOUNT,
    "synced": COPY,
    "env": ENV,
    # ⚑ secret_path takes a binding's TAG; its value is a scalar PATH, not a Bind.
    "secret_path": MOUNT,
}

#: The §2a category FAMILY ROOTS — the FIRST key segment of each family, so the two
#: ``bindings`` arms share the one root ``bindings``.  ⚑ DERIVED FROM
#: :data:`_DELIVERY` (P13), which is the ONE table naming every family, because every
#: family is delivered.  The bind-shaped, terminal and concrete tuples above each name
#: a SUBSET, so a consumer that wants "is this segment a category at all" must read
#: this and not one of them: ``masks``, ``env`` and ``secret_path`` are in no subset
#: that also holds the four abstract ones.
CATEGORY_FAMILY_ROOTS: Final[frozenset[str]] = frozenset(
    name.split(".", 1)[0] for name in _DELIVERY
)

# One regex for the bind-shaped categories: scope . <category> . name (name greedily
# captures the remainder, which may contain dots).
#
# ⚠ The AGENT scope is DISCRIMINATED, with NO exception: ``agent.<agent>`` or
# ``agent.default`` (the tier's FALLBACK). A BARE ``agent.<category>.<name>`` is NOT A
# KEY — the keyspace is CLOSED (spec §0), so these patterns must REFUSE it rather than
# quietly accept it. Do not "helpfully" widen this back.
_AGENT_SCOPE = r"agent\.[^.]+"
_FILE_SCOPE_ALT = "system|workset|box"
_NON_TERMINAL_CATEGORY_ALT = "|".join(
    c.replace(".", r"\.") for c in _NON_TERMINAL_BIND_CATEGORIES
)
#: The categories NO scope may name in a set/reset key any more — the DIFFERENCE from
#: ``_BIND_CATEGORIES``, ALL SIX since DS-BL1 = (a).  ⚑ It feeds BOTH scope regexes
#: below, so the two doors cover the same categories by DERIVATION, not by two hand
#: lists.  Recognition is derived here; resolution is not (llm-doc).
RETIRED_BIND_CATEGORIES: Final[tuple[str, ...]] = tuple(
    c for c in _BIND_CATEGORIES if c not in SETTABLE_BIND_CATEGORIES
)
_RETIRED_CATEGORY_ALT = "|".join(
    c.replace(".", r"\.") for c in RETIRED_BIND_CATEGORIES
)
#: ``{system,workset,box}.<bind-shaped category>.<name>`` — the RETIRED FILE-scope
#: bind route, which exists ONLY to be RECOGNISED and refused by name.  ⚑ NOT the
#: AGENT scope — its node segment must be split NON-GREEDILY and canonicalized
#: (``+`` -> ``℘``) first, so it has its own recogniser below.
SCOPE_BIND_KEY_RE = re.compile(
    rf"^(?P<scope>{_FILE_SCOPE_ALT})"
    rf"\.(?P<category>{_RETIRED_CATEGORY_ALT})\.(?P<name>.+)$"
)
#: ``agent.<node>.<bind-shaped category>.<name>`` — the AGENT-scope twin, for ONE
#: purpose: to be RECOGNISED and refused BY NAME.  ⚑ THE NODE IS NON-GREEDY so the
#: FIRST category segment splits node from name: a DEST tail may itself contain a
#: DOT-PRECEDED category token (``caches.~/.caches.x`` — measured: greedy parses
#: node=``claude.caches.~/``).  An UNDISCRIMINATED ``agent.<category>.<name>`` does NOT
#: match (§0/§2d).  ⚑⚑ RECOGNITION ONLY — IT PICKS NO READ ROUTE, and that separation
#: is load-bearing; ``config_keys._AGENT_NODE_BIND_RE`` is the parser that picks one.
AGENT_BIND_KEY_RE = re.compile(
    rf"^agent\.(?P<node>.+?)"
    rf"\.(?P<category>{_RETIRED_CATEGORY_ALT})\.(?P<name>.+)$"
)
# The PER-ENTRY bind-shaped key shape.  ⚑ NOT a "settable" shape: it answers "does a
# per-entry dotted key EXIST here?", so it is built from the NON-TERMINAL complement.
#
# ⚑⚑⚑ IT FAILS CLOSED, AND SINCE 2026-08-08c THAT IS ITS ONLY STATE: the last
# non-terminal category went dest-keyed, so this compiles ``(?!)`` and nothing matches.
# The empty case is spelled EXPLICITLY rather than left to ``"|".join``, which would
# have produced the degenerate ``system..foo``-accepting pattern.  ⚑ DO NOT RE-OPEN IT
# to restore a refusal: a match would mean "this per-entry key exists", the one thing
# that is no longer true.
BIND_KEY_RE = re.compile(
    rf"^(?P<scope>{_FILE_SCOPE_ALT}|{_AGENT_SCOPE})"
    rf"\.(?P<category>{_NON_TERMINAL_CATEGORY_ALT})\.(?P<name>.+)$"
    if _NON_TERMINAL_CATEGORY_ALT
    else r"(?!)"  # matches nothing, ever
)
# ``{scope}.masks`` — value-less category (a list of box_dest paths). The KEY has
# no per-entry name; entries are expanded per list element (name = the index).
MASK_KEY_RE = re.compile(rf"^(?P<scope>system|workset|box|{_AGENT_SCOPE})\.masks$")
# ``{scope}.env.{VAR}`` — scalar env var; VAR may NOT contain dots (env names).
ENV_KEY_RE = re.compile(
    rf"^(?P<scope>system|workset|box|{_AGENT_SCOPE})"
    r"\.env\.(?P<name>[A-Za-z_][A-Za-z0-9_]*)$"
)
# ``{scope}.secret_path.{VAR}`` — the SECRET category (spec §2a): a scalar host PATH
# keyed by the env VAR it delivers.  VAR is the env-name shape, never dotted.
SECRET_KEY_RE = re.compile(
    r"^(?P<scope>system|agent|workset|box)\.secret_path\."
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)$"
)
#: The bare-VAR shape above, enforced AGAIN at launch emit — ⚑ the VAR is
#: interpolated into a generated ``sh -c`` export shim, so one that slipped past
#: validation must be re-checked before it reaches the shell.  Keep the two in sync.
SECRET_VAR_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Apply order: REVERSE of precedence (most-specific scope lands LAST).
_SCOPE_APPLY_ORDER = {"system": 0, "agent": 1, "workset": 2, "box": 3}

# The CONCRETE MOUNT categories — the layer §0 calls the SOURCE OF TRUTH.  A mount is
# emitted from a ``bindings.{ro,rw}`` declaration and from nothing else; the abstract
# categories reach one only by DERIVING it, and ``secret_path`` is a concrete peer.
# ⚑ D2 CARVE-OUT: a group whose concrete members are ALL ``secret_path`` is NOT a
# bind-vs-bind collision — its dest is ``SECRET_MOUNT_DIR/{VAR}`` by construction, so two
# such entries share one VAR across two scopes.
CONCRETE_CATEGORIES: Final[tuple[str, ...]] = (
    "bindings.ro", "bindings.rw", "secret_path",
)

# The release in which the §0 collision table replaced the flat authority ladder.
# Named ONCE, for the migration-grade paragraph of every message whose OUTCOME
# changed (M-7).
_RULE_CHANGE_RELEASE: Final[str] = "1.8.0"


@dataclass(frozen=True)
class CategoryEntry:
    """One resolved scope-category entry (pre-collision-resolution).

    Field-by-field: ``llm-docs/kanibako/settings/settings_categories.py.md``.  ⚑ Note
    that ``env`` bends two — *box_dest* is its VAR NAME, *options* its VALUE.

    *key_segments* is the DECLARATION KEY plus the ENTRY'S DESTINATION, ONE SEGMENT
    PER NODE (``("box", "bindings", "rw", "~/w")``).  ⚑⚑ SEGMENTS, NOT A DOTTED
    STRING: a dest routinely contains ``.`` (``~/.cache/uv``), so a dotted spelling is
    AMBIGUOUS with the key-path separator; the joined form used to shatter a dest
    across tree levels.  :attr:`key` is the DERIVED dotted form, never structure.

    *is_credential* is what :func:`gate_credential_delivery` (D-M4) keys off.

    *optional* marks a MOUNT whose SOURCE may legitimately not exist, so the emitter
    DROPS it SILENTLY (spec §2c "SKIP-IF-ABSENT").  ⚑ Set by KEY NAME at the emitter,
    never by a resolve-time ``exists()`` probe: this module is PURE.  🛑 **NOTHING
    READS IT ANY MORE** (cutover step 3); its retirement is 5's.

    ⚑⚑ THERE IS NO ``dest_space`` FIELD, AND ITS ABSENCE IS THE DESIGN: ``box_dest``
    is a GUEST path for EVERY category, spec §0 *"ONE DEST SPACE, TWO DELIVERIES"*.
    ⚑ Do not reintroduce a host-spelled dest — one used to be mapped BACK under the
    box home, landing where nothing reads it, and the discriminator is gone (llm-doc).

    ⚑ THE ``canon`` NAMING TRAP, here because both spellings meet in this dataclass:
    **``@box.canon`` (= ``<box_dir>/canon``, the box's CONTRIBUTION root, one RO
    chapter at ``~/canon/handbook/box``) is NOT ``~/canon``**, the ASSEMBLED GUEST
    VIEW at ``<box_dir>/home/canon`` that the home bind delivers.
    """

    category: str
    scope: str
    box_dest: str
    host_src: str | None
    delivery: Delivery
    options: str
    name: str
    key_segments: tuple[str, ...]
    is_credential: bool = False
    optional: bool = False

    @property
    def key(self) -> str:
        """The DOTTED spelling of :attr:`key_segments` — for display and matching."""
        return ".".join(self.key_segments)


def _bind_options(category: str) -> str:
    """Mount options for a bind-shaped MOUNT category — ``ro``, else ``Z,U``."""
    return "ro" if category == "bindings.ro" else "Z,U"


def is_read_only(options: str | None) -> bool:
    """True when mount *options* carries ``ro`` as a comma-separated TOKEN."""
    return "ro" in {token.strip() for token in (options or "").split(",")}


@dataclass(frozen=True)
class CategoryCollision:
    """One SAME-SCOPE ``caches``/``common`` ambiguity at one ``box_dest`` (§0's exempt pair).

    Built as DATA by the ``store_shape`` PRODUCER (which stays PURE) and rendered by
    ``commands.start.emit_collision_warnings``.  ⚑ IT LIVES HERE, NOT IN
    ``store_shape``, DELIBERATELY: the message below is spec §0's, written ONCE,
    beside the two refusal texts the producer also raises.  ⚑ That producer is the
    ONLY builder (5-1c retired the second) — do not add one.
    """

    box_dest: str
    scope: str
    winner_key: str
    loser_keys: tuple[str, ...]

    def message(self) -> str:
        """The rendered warning line (one per collision, §0 "WARN every launch")."""
        losers = ", ".join(self.loser_keys)
        ignored = "is" if len(self.loser_keys) == 1 else "are"
        return (
            f"Two {self.scope!r}-scope declarations target {self.box_dest!r}: "
            f"{self.winner_key} wins, {losers} {ignored} ignored. A same-scope "
            f"collision is ambiguous — kanibako keeps the existing ordering but "
            f"cannot know which you meant. Suppress one (set it to null) or "
            f"repoint it. This warning repeats every launch until it is fixed."
        )


def path_depth(box_dest: str) -> int:
    """Path-depth of a guest dest for the mount depth-sort (shallower first).

    ⚑ PUBLIC because emission depth-sorts too — one rule, two consumers; a second
    spelling would drift the podman mount order on the nested dests it resolves.
    """
    return len([c for c in box_dest.strip("/").split("/") if c])


def gate_credential_delivery(
    entries: list[CategoryEntry], deliver_creds: bool,
) -> list[CategoryEntry]:
    """Drop what a PRIVATE box must not receive (D-M4). PUBLIC, PURE and IDEMPOTENT."""
    # ⚑ THE ONLY SPELLING OF THE RULE, APPLIED ONCE — at the launch seam, above EVERY
    # consumer of the entry list. A second application is how a credential reaches a
    # box the user made private.
    if deliver_creds:
        return list(entries)
    return [
        e for e in entries
        if e.category != "synced"
        and not (e.category == "seeded" and e.is_credential)
    ]


@dataclass(frozen=True)
class LaunchDeliveries:
    """What the launch entry list delivers BESIDE the collapse's mount set — and WHO DECLARED it.

    Built ONCE at the seam (``commands.start._resolve_launch_snapshot``) off the
    CREDENTIAL-GATED list the collapse also sees, so the two describe one box.

    ⚑ *agent_dests* (the emitter's SKIP-IF-ABSENT set) is a PARAMETER, not a filter
    written here: the predicate deciding what an agent delivery IS belongs to the
    emitter.  ⚑ *narrow_bindings* is a NARROW resolve's whole mount product and
    ``None`` otherwise — the collapse returns before writing
    ``meta.assembly.bindings`` — and ``None`` UNLESS THE CALLER ASKED, so the main
    path cannot reach a map it never requested (P3).

    ⚑⚑ *declared_by* IS THE FOLD'S OWN
    :attr:`~kanibako.settings.store_collapse.CollapsedStore.declared_by`, carried out
    of the resolve rather than written into the snapshot — dest-keyed, EMPTY on a
    narrow resolve (which folds no bind map at all).  It rides HERE because this
    carrier is the resolve's ONE out-of-band return channel: a second channel for one
    map is the two-carriers defect, and the alternative — a fourth ``meta.assembly``
    leaf — is the closed-keyspace addition ``store_collapse`` forbids by name.  It is
    read by a display that must NAME the declaration a mount came from
    (``settings.config_display``, ``box show --effective``).

    🛑 THE ENVIRONMENT IS NOT HERE ANY MORE — the variables are arbitrated by
    ``store_collapse.collapse_env`` and read off ``meta.assembly.env``.  A second,
    un-arbitrated view of the same declarations is what let a per-VAR contest be
    settled silently by a consumer's ``dict.update``.  🛑 AND THIS IS A RETURN VALUE,
    NEVER A SNAPSHOT KEY: ``meta.assembly.*`` is a CLOSED set of DECLARED leaves and
    an UNDECLARED one installs SILENTLY.  A FIELD is cheap; a LEAF is a keyspace edit.
    """

    secrets: list[CategoryEntry]
    agent_dests: frozenset[str]
    narrow_bindings: "dict[str, object] | None" = None
    declared_by: "dict[str, str]" = field(default_factory=dict)


def secret_path_winners(entries: list[CategoryEntry]) -> list[CategoryEntry]:
    """The per-VAR ``secret_path`` winners — spec §2a's cascade, at the seam.

    Every ``secret_path`` dest is ``SECRET_MOUNT_DIR/{VAR}`` BY CONSTRUCTION, so a
    group sharing a dest is ONE VAR from several scopes — the per-VAR cascade, picked
    by :func:`_most_specific`, then depth-sorted.  ⚑ P7 — WHAT IT DOES *NOT* DECIDE:
    whether anything ELSE contends for that dest.  That is
    :func:`secret_path_deliveries`'.
    """
    by_dest: dict[str, list[CategoryEntry]] = {}
    for e in entries:
        if e.category == "secret_path":
            by_dest.setdefault(e.box_dest, []).append(e)
    winners = [_most_specific(group) for group in by_dest.values()]
    winners.sort(key=lambda e: (path_depth(e.box_dest), e.box_dest))
    return winners


def secret_path_deliveries(entries: list[CategoryEntry]) -> list[CategoryEntry]:
    """The ``secret_path`` mounts a launch DELIVERS: the per-VAR winners, §0-gated.

    ⚑⚑ THE §0 CROSS-CATEGORY GATE FOR SECRET DESTS LIVES HERE (6-R2), BECAUSE NOTHING
    ELSE HOLDS THE INPUTS: ``secret_path`` carries no arm in the disk-store shape
    (producer DESIGN §7.4), so the COLLAPSE never sees a secret.  Two rules apply over
    the SAME entry list in the SAME order, at secret dests only:

    * a BIND at the dest — a ``bindings.*`` row, or an abstraction deriving one, aimed
      at ``SECRET_MOUNT_DIR/<VAR>`` REFUSES the launch, naming BOTH declarations.
      ⚑ Several ``secret_path`` rows at one dest are the per-VAR cascade and NOT a
      collision — the D2 carve-out, the CALLER's test exactly as
      :func:`raise_binding_vs_binding` says.
    * a MASK at the dest — it takes the dest and the VAR is not delivered,
      SILENTLY: the tmpfs lands there anyway.

    ⚑ EXACT DEST ONLY, and that is not a narrowing: a bind or mask over the secrets
    DIRECTORY never contended with ``SECRET_MOUNT_DIR/<VAR>`` (MEASURED, 6-R2).
    🛑 But the dest GROUP is the WHOLE mount group, per-VAR LOSERS included, so a
    refusal names every participant rather than just one.
    """
    winners = secret_path_winners(entries)
    dests = {w.box_dest for w in winners}
    by_dest: dict[str, list[CategoryEntry]] = {}
    for e in entries:
        if e.delivery == MOUNT and e.box_dest in dests:
            by_dest.setdefault(e.box_dest, []).append(e)

    delivered: list[CategoryEntry] = []
    for winner in winners:
        group = by_dest[winner.box_dest]
        concrete = [e for e in group if e.category in CONCRETE_CATEGORIES]
        abstract = [e for e in group if e.category in ABSTRACT_CATEGORIES]
        if len(concrete) > 1 and not all(
            e.category == "secret_path" for e in concrete
        ):
            raise_binding_vs_binding(winner.box_dest, concrete)
        if concrete and abstract:
            raise_extension_onto_occupied(
                winner.box_dest, extension=abstract[-1], base=concrete[-1],
            )
        if any(e.category == "masks" for e in group):
            continue
        delivered.append(winner)
    return delivered


def launch_deliveries(
    entries: list[CategoryEntry], *, agent_dests: frozenset[str],
    narrow_bindings: "dict[str, object] | None" = None,
    declared_by: "dict[str, str] | None" = None,
) -> LaunchDeliveries:
    """Build the :class:`LaunchDeliveries` carrier from a CREDENTIAL-GATED list.

    ⚑ NO ``ENV`` FILTER HERE, AND ADDING ONE BACK WOULD BE A SECOND ROUTE: the ``env``
    rows leave through the assembly collapse, off this same list, so a box's variables
    and its mounts fold from one input.

    ⚑ *declared_by* is HANDED IN, never derived: it is the FOLD's record (see the field),
    and this function has no bind map to read one off.  Omitted, the carrier reports an
    empty map and every reader of it prints exactly what it always did.
    """
    return LaunchDeliveries(
        secrets=secret_path_deliveries(entries),
        agent_dests=agent_dests,
        narrow_bindings=narrow_bindings,
        declared_by=dict(declared_by or {}),
    )


def narrow_table_winners(
    entries: list[CategoryEntry], dests: frozenset[str],
) -> list[CategoryEntry]:
    """A NARROW resolve's mount winners: its OWN table's dests, one row each.

    A narrow resolve carries one injected table (``include_base_families=False``) but
    still resolves the user's whole CASCADE, so a user's declaration reaches it —
    emitting those is the D1 defect, deleted (P4) by filtering to *dests*, the
    table's own.  At such a dest §0 still decides, and these two rules are its:

    * a MASK at the dest OVERRIDES the table's bind: the COLLAPSE's rule, applied
      here because the collapse returns early on a narrow resolve.
    * anything else contending for a table dest is REFUSED by name.  The producer
      already raised both refusals for a SAME-scope pair; what is left here is the
      CROSS-scope pair.  ⚑ A bare dest-filter would let both rows into a dest-keyed
      map and resolve them by INSERTION ORDER, silently.

    ⚑ NO SILENT PICK, deliberately — "two mounts at one destination are an error in
    every scope combination but one" is the ratified rule, its one exception is the
    producer's, and a narrow resolve has no cross-scope arbiter to defer to.
    """
    from kanibako.settings.settings_resolve import normalize_bind_dest

    by_dest: dict[str, list[CategoryEntry]] = {}
    for e in entries:
        if e.delivery != MOUNT:
            continue
        dest = normalize_bind_dest(e.box_dest)
        if dest in dests:
            by_dest.setdefault(dest, []).append(e)

    winners: list[CategoryEntry] = []
    for dest, group in by_dest.items():
        masks = [e for e in group if e.category == "masks"]
        if masks:
            winners.append(masks[-1])
            continue
        if len(group) > 1:
            concrete = [e for e in group if e.category in CONCRETE_CATEGORIES]
            abstract = [e for e in group if e.category in ABSTRACT_CATEGORIES]
            if concrete and abstract:
                raise_extension_onto_occupied(
                    dest, extension=abstract[-1], base=concrete[-1],
                )
            raise_binding_vs_binding(dest, group)
        winners.append(group[0])
    return winners


def raise_binding_vs_binding(
    box_dest: str, concrete: list[CategoryEntry],
) -> NoReturn:
    """Raise §0's refusal of two CONCRETE declarations at one *box_dest*.

    PUBLIC because that is decidable inside ONE scope as well as across two, so it
    has THREE callers: the ``store_shape`` producer and the two launch-seam functions
    above.  ⚑ The D2 carve-out is the CALLER's test: it decides whether the set in
    hand is a collision at all.
    """
    from kanibako.errors import CategoryCollisionError

    raise CategoryCollisionError(
        f"Two bindings target the same box destination '{box_dest}':\n"
        + _entry_lines(concrete)
        + "A destination may be bound exactly once. Choosing one silently "
        "would give you a\nread-only mount where the other declaration asked "
        "for read-write.\n\n"
        + _rule_changed(
            "Until {rel} the more specific scope won, silently — a "
            "configuration that\nlaunched before can refuse to launch now. "
            "Your files did not change; the rule did."
        )
        + _suppress_then_add(concrete[0].key_segments, ambiguous=True),
        kind="binding_vs_binding",
        box_dest=box_dest,
        entries=tuple((e.key, e.host_src) for e in concrete),
    )


def raise_extension_onto_occupied(
    box_dest: str, *, extension: CategoryEntry, base: CategoryEntry,
) -> NoReturn:
    """Raise §0's refusal of *extension* extending onto the *base*'s *box_dest*.

    PUBLIC for the same reason as :func:`raise_binding_vs_binding`, with the same
    three callers.  ⚑ The BASE always survives, so the remedy names it without the
    two-peers "either one may be the one you keep" hedge.
    """
    from kanibako.errors import CategoryCollisionError

    raise CategoryCollisionError(
        f"'{extension.key}' extends onto '{box_dest}', which\n"
        f"'{base.key}' already binds.\n"
        "'common', 'caches' and 'seeded' are ABSTRACT declarations: each "
        "derives a\nbindings.rw entry. The explicit binding is the BASE and "
        "survives; the derived\nextension is refused.\n\n"
        + _rule_changed(
            "Until {rel} a 'common' silently overrode a binding at the same "
            "destination,\nand a 'caches' silently lost to one — two "
            "abstractions, two opposite silent\noutcomes. Both are now "
            "refused."
        )
        + _suppress_then_add(base.key_segments),
        kind="extension_onto_occupied",
        box_dest=box_dest,
        entries=tuple((e.key, e.host_src) for e in (extension, base)),
    )


def _most_specific(entries: list[CategoryEntry]) -> CategoryEntry:
    """The winner among same-layer peers: SCOPE PRECEDENCE first, then input order.

    ⚑ The scope order is authoritative and the CALLER's list order must not override
    it — this takes an ARBITRARY list; only the live adapter hands it apply-ordered.
    Within one scope input order decides, LAST wins.  ⚑ ONE CALLER since 6-R3.
    """
    return max(
        enumerate(entries),
        key=lambda pair: (_SCOPE_APPLY_ORDER[pair[1].scope], pair[0]),
    )[1]


def _entry_lines(entries: list[CategoryEntry]) -> str:
    """``    <key>  ->  <host_src>`` lines, key-column aligned."""
    width = max((len(e.key) for e in entries), default=0)
    return "".join(
        f"    {e.key.ljust(width)}  ->  {e.host_src}\n" for e in entries
    )


#: The SUPPRESS-THEN-ADD cure (§0), unwrapped.  ⚑⚑ ONE CARRIER, and that is the whole
#: point: a user who meets one refusal in this family has met the others, and two
#: spellings of one cure send them to two mechanisms.  Every message that offers the
#: remedy spells it FROM HERE — :func:`_suppress_then_add` wraps it for its YAML block,
#: and ``store_collapse``'s refusals take it as it stands.  It carries no terminal
#: punctuation, so a caller ends it with the ``.`` or ``:`` its own sentence needs.
SUPPRESS_THEN_ADD: Final[str] = (
    "To change what occupies a destination you must SUPPRESS the entry you do not "
    "want and then declare the one you do. An override is not enough: these are two "
    "different KEYS, so both survive the cascade. Set the unwanted key to null in the "
    "settings file for its scope (a file may write its own scope and the scopes it "
    "contains)"
)

#: The width :func:`_suppress_then_add` wraps at.  ⚑ It is a MESSAGE-LAYOUT choice, not
#: the source line length: the block below sits under a key path a reader must be able
#: to read as one unit.
_REMEDY_WRAP: Final[int] = 80


def _rule_changed(body: str) -> str:
    """The migration-grade paragraph (M-7) — ⚑ ONLY on a rule whose outcome changed;
    on one that did NOT it trains a reader to skip it.
    """
    return (
        f"⚑ THIS RULE CHANGED IN kanibako {_RULE_CHANGE_RELEASE}. "
        + body.format(rel=_RULE_CHANGE_RELEASE)
        + "\n\n"
    )


def _suppress_then_add(
    occupant_segments: tuple[str, ...], *, ambiguous: bool = False,
) -> str:
    """The SUPPRESS-THEN-ADD remedy (§0), spelled as the YAML edit it really is.

    ⚑ The prose is :data:`SUPPRESS_THEN_ADD`, wrapped — NOT a literal of its own.

    ⚑ NO CLI verb can express THIS suppression — ``set``, ``reset`` and
    ``set --null pref.<key>`` each fail for a different reason (llm-doc).  So the
    remedy is a hand edit, and the message says so rather than naming a command that
    would not work.  ⚑ It also names the SCOPE: a box file may not suppress a
    containing scope's key.  *ambiguous* is True when the caller could not know WHICH
    entry to keep (two peers), so the block is labelled an example rather than a
    prescription; the extension-onto-occupied refusal passes False.
    """
    # ⚑ SEGMENTS, NEVER A DOTTED SPLIT — a split block is not a declaration at all.
    occupant_key = ".".join(occupant_segments)
    scope = occupant_segments[0]
    last = len(occupant_segments) - 1
    indent = "  "
    lines = [f"{scope}:"]
    for depth, seg in enumerate(occupant_segments[1:], start=1):
        lines.append(f"{indent * depth}{seg}:" if depth < last
                     else f"{indent * depth}{seg}: null")
    # ⚑ An agent's OWN file has NO node level: its root ``self:`` IS ``agent.<node>``,
    # with the category table DIRECTLY under it, so printing only the canonical form
    # would hand the reader an edit that silently does nothing there. ⚑ That spelling
    # comes from the BOUNDARY, never a literal here — the caveat QUOTES the file, and
    # it quotes the TABLE, never the entry, whose last segment is the DEST.
    from kanibako.settings.agent_file import file_spelling

    caveat = (
        f"\n⚑ In {occupant_segments[1]}'s OWN settings file there is no node level: "
        f"the table is\nspelled '{file_spelling(*occupant_segments[2:last])}', because "
        f"'{file_spelling()}:' IS '{scope}.{occupant_segments[1]}'. The form above is "
        "what a CONTAINING scope's file writes.\n"
        if scope == "agent" and len(occupant_segments) > 1 else ""
    )
    which = (
        "Either entry may be the one you keep — the block below suppresses "
        f"'{occupant_key}';\nuse whichever key you do NOT want.\n\n"
        if ambiguous else ""
    )
    return (
        textwrap.fill(SUPPRESS_THEN_ADD, _REMEDY_WRAP)
        + ":\n\n"
        + which
        + "\n".join(lines)
        + "\n"
        + caveat
    )


def derive_binding_keys(
    entries: list[CategoryEntry],
) -> dict[tuple[str, ...], "Bind"]:
    """The MATERIALISED derived bindings for the ABSTRACT declarations (§0).

    §0 requires the binding each abstract declaration produces to be materialised
    BESIDE it, at ``binding_derivations.<declaration-key>.<dest>`` (R-8: the reserved
    INTERNAL node at the snapshot root — NOT a key).  ⚑ KEYED BY SEGMENTS, and
    ``KeyStore.insert_segments`` splits nothing — see :class:`CategoryEntry`.

    ⚑ It is deliberately NOT written into ``<scope>.bindings.rw.<name>`` (§0's own
    ruling) — not for a runtime reason, but for MEANING: a key in the concrete layer
    that no user wrote and that emits no mount is a FORGERY of the one thing the §0
    table reads (llm-doc: the argument in full).

    PURE; the ONE seam that installs the map is
    ``commands.start._resolve_launch_snapshot``.  ⚑ Every abstract entry surviving the
    credential gate gets one, WINNERS AND LOSERS ALIKE: a loser's derivation is what
    explains the warning that names it.
    """
    from kanibako.settings.kb_store import BINDING_DERIVATIONS_NODE, Bind

    out: dict[tuple[str, ...], Bind] = {}
    for e in entries:
        if e.category not in ABSTRACT_CATEGORIES:
            continue
        out[(BINDING_DERIVATIONS_NODE, *e.key_segments)] = Bind(
            host=e.host_src or "", box=e.box_dest,
            opts=e.options or None,
        )
    return out


def declaration_delivery(decl_key: str) -> Delivery:
    """The COPY/MOUNT delivery of a declaration KEY, off the ONE category table.

    The category is the segment after the scope, and the AGENT scope is
    DISCRIMINATED — two segments (``agent.<tier>``) where every other scope is one.
    Parsed BY POSITION rather than by substring search, so a trailing DESTINATION
    that happens to spell a category (``box.caches.common``) cannot be misread.
    (The trailing segment is a dest, not a name: the four categories went terminal
    and dest-keyed on 2026-08-08c.)

    ⚑ IT LIVES BESIDE :data:`_DELIVERY`, which is the ONE definition of what a
    category delivers.  It was ``config_display._declaration_delivery`` until the
    ``--effective`` pairing landed, and moved here because the pairing needs the
    same answer: a renderer keeping its own copy would drift the moment a category
    moved between COPY and MOUNT.
    """
    parts = decl_key.split(".")
    idx = 2 if parts[0] == "agent" else 1
    return _DELIVERY.get(parts[idx] if len(parts) > idx else "", MOUNT)


def effective_bindings_and_template_sources(
    snapshot: "KeyStore",
) -> "tuple[Any, ...]":
    """Every ABSTRACT declaration paired with the delivery the box ACTUALLY receives.

    Returns ``store_collapse.Derivation`` rows, one per declaration, sorted by
    declaration key.  THE SINGLE SOURCE for the ``--effective``
    binding-derivations block: a consumer that recomputes either half is the second
    opinion that display exists to DETECT.

    🛑🛑 **IT TAKES TWO INPUTS AND BOTH ARE LOAD-BEARING.**  The reserved
    ``binding_derivations`` node supplies the DECLARATIONS and nothing else: it is
    populated BEFORE arbitration, DELIBERATELY (R-8 — a derived binding is a
    property of the DECLARATION, and :func:`derive_binding_keys` materialises one
    for winners and losers ALIKE), so every row in it reads as a live mount.  What
    the box receives is ``meta.assembly.bindings`` / ``.seeded`` / ``.synced`` — the
    arbitrated collapse.  **Reading the reserved node ALONE is the measured failure
    this function exists to prevent**: a ``common`` declaration under a mask at the
    same dest collapses to a mask sentinel — no mount — and the node still says
    mount, so the block printed ``(mount)`` and printed no mask at all.

    ⚑ Absent assembly leaves are a NARROW resolve, and the pairing says so by name
    (``DERIVED_UNCOVERED``) rather than reporting every declaration as unmounted.

    ⚑ ABSTRACT DECLARATIONS ONLY, because that is what the reserved node carries
    and what keyspec ``:88`` obliges.  The CONCRETE half of the block is rendered
    from the per-scope ``bindings.{ro,rw}`` arms and needs no pairing: a concrete
    declaration IS the source of truth a mount is emitted from.
    """
    from kanibako.settings.kb_store import BINDING_DERIVATIONS_NODE
    from kanibako.settings.keystore import KeyStore
    from kanibako.settings.settings_launch import snapshot_leaf
    from kanibako.settings.settings_views import derived_bindings
    from kanibako.settings.store_collapse import Declaration, pair_declarations

    node = dict.get(snapshot, BINDING_DERIVATIONS_NODE)
    derived = derived_bindings(node) if isinstance(node, KeyStore) else {}
    declarations = [
        Declaration(
            key=key, dest=bind.box, src=bind.host,
            delivery=declaration_delivery(key),
        )
        for key, bind in sorted(derived.items())
    ]
    bindings = snapshot_leaf(snapshot, "meta.assembly.bindings")
    return pair_declarations(
        declarations,
        dict(bindings) if isinstance(bindings, dict) else {},
        [
            *_assembly_copy_list(snapshot, "meta.assembly.seeded"),
            *_assembly_copy_list(snapshot, "meta.assembly.synced"),
        ],
    )


def _assembly_copy_list(snapshot: "KeyStore", dotted: str) -> list[Any]:
    """One collapsed copy leaf as a list — ABSENT and EMPTY both yield ``[]`` here.

    ⚑ The distinction the launch readers keep (``commands.start
    ._snapshot_assembly_seeded``: absent = a narrow resolve) is not one this caller
    can act on.  A copy is arbitrated at no destination, so an absent list and an
    empty one both mean *no copy row accounts for this declaration* — which the
    pairing already reports as a loss, by the same route a same-scope loser takes.
    """
    from kanibako.settings.settings_launch import snapshot_leaf

    rows = snapshot_leaf(snapshot, dotted)
    return list(rows) if isinstance(rows, list) else []
