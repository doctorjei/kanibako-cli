"""Unified scope-category resolution (pure, additive).

This module GENERALIZES the (now-folded) ``settings_shares`` + ``settings_seeds``
resolvers into one *category* primitive.  Every path-delivery mechanism the
settings framework exposes at a scope is a CATEGORY; this module discovers the
category keys across the precedence levels, resolves each via the engine in
:mod:`kanibako.settings.settings_resolve`, and emits one ordered ``list[CategoryEntry]``
tagged with its *delivery* (COPY vs MOUNT).  It is **pure**: no file I/O, no
mounting/copying, no global mutable state.  It imports only stdlib and the
expression engine.

Cross-category collision resolution (the spec §0 identical-dest TABLE, depth-order,
``synced``↔``binding`` errors, the credential ``deliver_creds`` gate) is
:func:`reconcile_categories` (sub-step 4b), layered on top of category entries.

**Block 7c status:** :func:`reconcile_categories` is LIVE — the by-dest pass the
KeyStore snapshot path uses (fed by ``settings_launch.snapshot_category_entries``).
The OLD by-NAME LevelView resolver was RETIRED (it was wrong in a number of cases,
which is why the snapshot pipeline replaced it); its frozen, non-shipping remnant
lives ONLY in ``tests/support/flawed_oracle.py`` as a drift tripwire for the
equivalence test — NOT a correctness authority. The ``settings_shares`` /
``settings_seeds`` wrapper modules it used to feed were retired in 7c (the launch +
``workset share`` paths now resolve through the snapshot pipeline).

The nine categories
-------------------
Available at every scope ``{system, agent, workset, box}``:

================ ===================================== ============= =========
category         key shape                              host_src      delivery
================ ===================================== ============= =========
``masks``        ``{scope}.masks``  (TERMINAL)          ``None``      MOUNT
``bindings.ro``  ``{scope}.bindings.ro``  (TERMINAL)    entry         MOUNT
``bindings.rw``  ``{scope}.bindings.rw``  (TERMINAL)    entry         MOUNT
``caches``       ``{scope}.caches.{name}``              bind          MOUNT
``seeded``       ``{scope}.seeded.{name}``              bind          COPY
``common``       ``{scope}.common.{name}``              bind          MOUNT
``synced``       ``{scope}.synced.{name}``              bind          COPY
``env``          ``{scope}.env.{VAR}``  (value)         ``None``      ENV
``secret_path``  ``{scope}.secret_path.{VAR}`` (path)   host path     MOUNT
================ ===================================== ============= =========

⚑ ``masks`` and ``bindings.{ro,rw}`` are TERMINAL keys (R-5): the whole arm is
ONE key and the inner names are NOT part of the keyspace. A ``bindings`` arm is
DEST-KEYED (R-6) — its inner key IS the box destination and its "entry" value is
the 2-element ``[host_src[, options]]`` unpacked by
:func:`~kanibako.settings.settings_resolve.unpack_bind_entry`. ``masks`` is keyed
by box destination too, its value carrying only presence (a masked dest).

The four abstract categories are still keyed by NAME, and a "bind" value there is
the 3-element ``[host_src, box_dest[, options]]`` unpacked by
:func:`~kanibako.settings.settings_resolve.unpack_bind` (spec §2a — never a
colon-joined string). ⚑ The two unpackers are NOT interchangeable: both accept a
2-element list and the two mean OPPOSITE things, so the route is chosen from the
NODE the value came from, never sniffed off the value. ``env`` carries a scalar
value for ``{VAR}`` (no host source, no guest *path* — its ``box_dest`` field is
the VAR name).

``secret_path`` (spec §2a SECRET category, 2026-07-06) is SCALAR-valued like
``env`` (a host PATH to secret material, e.g. a 0600 bearer-token file), but
delivered ARM'S-LENGTH as a ro MOUNT: at launch the cascade-resolved host path is
ro-bind-mounted to a fixed, non-persistent in-box location
(:data:`SECRET_MOUNT_DIR`\\ ``/{VAR}``) and a box-side shim exports ``{VAR}`` from
that mount — so kanibako NEVER reads the secret VALUE (never in process memory /
the podman argv / the snapshot / keystore / logs). Keyed per ``{VAR}`` (same
cascade merge + reserved-name floor as ``env.{VAR}``); its ``box_dest`` IS a real
guest path (so it participates in box_dest collisions, as a CONCRETE-layer peer of
``bindings.{ro,rw}`` — see :data:`CONCRETE_CATEGORIES` for the per-VAR carve-out),
and its ``host_src`` is the SCALAR path (NOT a Bind tuple).

Delivery
~~~~~~~~
* ``seeded`` and ``synced`` are file **COPIES** (synced creds inode-swap, which
  breaks single-file binds — they are copy-synced).
* ``caches``, ``bindings.ro``, ``bindings.rw``, ``common``, ``masks`` are podman
  **MOUNTs** that physically shadow whatever is at the same path.
* ``env`` is neither — it is delivered as an environment variable (``ENV``).

Two orthogonal axes (unchanged from ``common``/``seeded``)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
* **The KEY's scope** selects — for ``bindings`` — the mount mode, and names the
  DECLARATION ROOT an abstract-category source is spelled against when it is
  declared (spec §2a; the rooting happens at the declaration site, never here).
* **The LEVEL where the key is SET** decides *precedence*.  A box may set a
  system-scoped key to a terminal ``""`` to suppress an inherited entry.

Accumulate / apply order
~~~~~~~~~~~~~~~~~~~~~~~~~~
Distinct ``(category, scope, name)`` entries accumulate.  For a single key, the
most-specific level that set it wins (:func:`resolve_value`).  Entries are
returned in scope *apply* order ``system, agent, workset, box`` (the REVERSE of
precedence) so the most-specific scope lands LAST — letting a later copy overlay
an earlier one and podman's "last ``-v`` wins" dedup honor box over system.
Within a scope they are ordered ``(category, name)`` ascending for determinism.
4b imposes the cross-category authority order on top of this.

No root-join
~~~~~~~~~~~~~~~
There is NONE, by rule.  Every ``host_src`` reaching this module already resolves
ON ITS OWN — absolute, ``~``, ``$var`` or an ``@``-ref (spec §2a) —
because the ABSTRACT categories (``common``/``caches``/``seeded``) are rooted at
DECLARATION: an author writes a bare leaf and the declaring loader stores the full
``@<scope-root>/<category>/<leaf>``.  A layer that prefixed a root on the way to a
mount is the shape §2a calls FORBIDDEN: it hides the true source and
resolves differently in any other context.  Nothing here joins anything.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, Literal, Mapping, NoReturn

if TYPE_CHECKING:  # pragma: no cover - typing only
    from kanibako.settings.settings_store import Bind, KeyStore

# Delivery tags.
Delivery = Literal["COPY", "MOUNT", "ENV"]
COPY: Final[Delivery] = "COPY"
MOUNT: Final[Delivery] = "MOUNT"
ENV: Final[Delivery] = "ENV"

#: The fixed, non-persistent in-box directory the SECRET category (``secret_path``)
#: ro-mounts each host secret file into, as ``{SECRET_MOUNT_DIR}/{VAR}`` (spec §2a
#: SECRET category, 2026-07-06). NOT under the box ``~`` home (so it is disjoint
#: from the home/workspace/vault mounts and stays OUT of the ``~``-rooted mount
#: depth-sort). A box-side export shim (``start.py``) reads each file here into
#: ``{VAR}`` at agent start — kanibako only ever writes the mount PATH.
SECRET_MOUNT_DIR: Final[str] = "/run/kanibako/secrets"

# The bind-shaped categories (value is a 2-/3-element
# ``[host_src, box_dest[, options]]`` tuple).  ``masks`` (a keyed list) and ``env``
# (a scalar) have bespoke key shapes handled separately below.
#
# ⚑ FOUR TUPLES, AND EACH ANSWERS A DIFFERENT QUESTION.  Conflating any two is
# what this split exists to prevent:
#
# * ``_BIND_CATEGORIES`` — every bind-shaped category, i.e. which keys carry the
#   tuple VALUE SHAPE.  All six are declared keys, authored in YAML, delivered at
#   launch.  The other three tuples are ITS SUBSETS.
# * ``_TERMINAL_BIND_CATEGORIES`` — the ones where the CATEGORY KEY IS THE WHOLE
#   KEY: its value is a map keyed by box DESTINATION, and a destination is DATA, so
#   ``<scope>.<category>.<name>`` is NOT a key at all (R-5/R-6).
# * ``_NON_TERMINAL_BIND_CATEGORIES`` — the COMPLEMENT, derived: the ones that DO
#   have a per-entry dotted key, so ``config get`` can read one at a dotted path,
#   the positional-vs-key disambiguator must read the shape as a key, and the write
#   verbs must be able to name one in a refusal.  ⚑ MEMBERSHIP MOVES, THE QUESTION
#   DOES NOT: as each category goes dest-keyed it moves to the terminal tuple and
#   drops out of this one automatically.  Nothing here asserts anything about WHICH
#   categories those are — and as of 2026-08-08c it is EMPTY, because all six
#   moved.  That is the DERIVATION working, not a special case: the per-entry
#   dotted key does not exist for any bind-shaped category any more.
# * :data:`SETTABLE_BIND_CATEGORIES` — which bind-shaped categories may be named
#   in a ``config set`` / ``config reset`` key.  **EMPTY.  All six are YAML-only.**
#
# ⚑⚑ THE CLI WRITE ROUTE IS RETIRED FOR ALL SIX (ruling DS-BL1 = (a), 2026-08-07g:
# *"accept the loss uniformly"*).  ``bindings.{ro,rw}`` lost it first (R-9, two
# steps) because their per-name key stopped existing; the other four lost it by
# the uniform ruling rather than by shape, and it is a KNOWN, ACCEPTED
# user-surface loss — a bind-shaped entry is authored in YAML, full stop.  Every
# retired spelling stays RECOGNISED so the verbs refuse it BY NAME instead of
# degrading to "unknown config key"; the closed keyspace (spec §0) refuses,
# never accepts quietly.  All six stay READABLE via ``config get``:
#
# * ``{system,workset,box}.<any-of-the-six>.<name>`` — recognised by
#   :data:`SCOPE_BIND_KEY_RE`, refused by ``config_keys.scope_bind_retired_error``.
# * ``agent.<node>.<any-of-the-six>.<name>`` — recognised by the node-splitting
#   :data:`AGENT_BIND_KEY_RE` (the node segment may itself be dotted, so it cannot
#   be folded into the regex above), refused by
#   ``config_keys.agent_node_bind_retired_error``.  The ``bindings`` arms are ALSO
#   matched by the narrower ``config_keys._AGENT_NODE_BIND_RE``, which is the READ
#   parser, not the recogniser — see :data:`AGENT_BIND_KEY_RE`.
#
# ⚑ THE READ ROUTE IS THE TERMINAL KEY, NOT THE ENTRY (2026-08-08c).  The
# per-entry dotted spelling is not a key at ANY scope now, so ``config get``
# reads ``<scope>.<category>`` — the WHOLE dest-keyed map — through
# ``settings_keyspace.is_terminal_category_tail``, exactly as it already read
# ``<scope>.bindings.ro`` and ``<scope>.masks``.  ⚑ A per-entry spelling is
# therefore refused WITHOUT a read to offer: the refusal names the terminal key
# and says the entry lives inside its value, rather than promising a ``config
# get`` of the entry.  ⚑ THE ONE EXCEPTION IS THE AGENT-SCOPE TERMINAL READ:
# ``config get agent.<node>.<category>`` still resolves through
# ``config_dest``'s known-broken ``_CATEGORY`` arm to the NOUN's settings file
# rather than to ``agents/<node>/settings.yaml``, so the value it returns is the
# wrong file's.  That arm's repointing is an OWED, separately-ruled pass (it moves
# ``agent_config.agent_file_route``, the per-agent file-shape SoT); it is named
# here so nothing writes a message promising that read works.
#
# NOTE the regex order: ``bindings.ro`` / ``bindings.rw`` must precede a bare
# ``bindings`` (there is none), and ``caches``/``seeded``/``common``/``synced`` are
# distinct tokens.  Listed longest-first so every alternation built below is
# unambiguous.
_BIND_CATEGORIES: Final[tuple[str, ...]] = (
    "bindings.ro", "bindings.rw", "caches", "seeded", "common", "synced",
)
#: The bind-shaped categories whose CATEGORY KEY IS TERMINAL — the whole of the
#: key, with a destination-keyed map for a value (R-5/R-6).  No ``.<name>`` key
#: exists under one, so no per-entry dotted spelling can be read, refused or set.
#:
#: ⚑ A MIRROR, NOT THE DEFINITION.  The keyspace validator owns that:
#: ``settings_keyspace.TERMINAL_CATEGORY_TAILS`` (which also lists ``masks`` — a
#: terminal category, but not bind-shaped, so it is absent here).  It is DERIVED
#: from :data:`_BIND_CATEGORIES` rather than imported because this module is
#: deliberately stdlib-only (see the module docstring), and the two are PINNED
#: EQUAL by
#: ``test_settings_keyspace.test_the_bind_shaped_terminal_mirror_cannot_drift`` so
#: the mirror cannot drift.
#: ⚑ THIS TUPLE FINISHED MOVING 2026-08-08c: it is now ALL SIX.  Every
#: bind-shaped category is dest-keyed and terminal, so
#: :data:`_NON_TERMINAL_BIND_CATEGORIES` below is EMPTY and
#: :data:`BIND_KEY_RE` compiles its never-matching form.  A category is added
#: here in the same pass that flips its parsing, never before.
_TERMINAL_BIND_CATEGORIES: Final[tuple[str, ...]] = _BIND_CATEGORIES
#: The bind-shaped categories that still have a PER-ENTRY DOTTED KEY — derived as
#: the complement of the terminal ones, so "terminal" has exactly one definition
#: here and this can never disagree with it.  ⚑ EMPTY since 2026-08-08c; the
#: derivations below (notably :data:`BIND_KEY_RE`) handle that case EXPLICITLY.
_NON_TERMINAL_BIND_CATEGORIES: Final[tuple[str, ...]] = tuple(
    c for c in _BIND_CATEGORIES if c not in _TERMINAL_BIND_CATEGORIES
)
#: The bind-shaped categories a ``config set`` / ``config reset`` key may name.
#: **EMPTY — DS-BL1 = (a): every bind-shaped category is YAML-only.**  It is kept
#: as an EMPTY TUPLE rather than deleted because it is the ONE definition of
#: "settable" that :data:`RETIRED_BIND_CATEGORIES` is derived from, so the two can
#: never drift and re-admitting a category would be a one-line edit here (plus a
#: visible spec edit — the keyspace is CLOSED).
#: ⚑ NOTHING may build a regex ALTERNATION from this tuple: ``"|".join(())`` is
#: the empty string, which yields a group that matches the EMPTY string and would
#: accept ``system..foo``.  Every alternation below is built from a tuple that
#: cannot be empty.
SETTABLE_BIND_CATEGORIES: Final[tuple[str, ...]] = ()

# The ABSTRACT categories — the three that let an author write a bare LEAF, rooted
# at DECLARATION under ``<scope-root>/<category>/`` (spec §2a). The rest
# (``bindings.{ro,rw}``, ``synced``) are CONCRETE: they take NO root at any scope,
# so a relative source there is a defect, not a shorthand.
ABSTRACT_CATEGORIES: Final[tuple[str, ...]] = ("common", "caches", "seeded")

# spec §2a DECLARATION ROOTS — the per-SCOPE root an abstract category's sources
# are spelled against. THE single copy of the spec's table; ``{agent}`` is the only
# placeholder (the agent tier is discriminated, so its root names the agent).
# Consumers: the declaration-time ref builder (agent scope) and the ``config set``
# refusal message, which must name the root of the scope the user actually typed.
DECLARATION_ROOT_REF: Final[Mapping[str, str]] = {
    "system": "@config.data",
    "agent": "@meta.agent.{agent}.path",
    "workset": "@meta.workset.path",
    "box": "@meta.box.path",
}

# delivery per category (the COPY/MOUNT split — design §3).
_DELIVERY: dict[str, Delivery] = {
    "masks": MOUNT,
    "bindings.ro": MOUNT,
    "bindings.rw": MOUNT,
    "caches": MOUNT,
    "seeded": COPY,
    "common": MOUNT,
    "synced": COPY,
    "env": ENV,
    # secret_path: scalar host PATH delivered as a ro MOUNT (spec §2a SECRET
    # category) — same delivery TAG as a binding, but the value is a scalar path
    # (host_src), not a Bind tuple. Its box_dest is a real guest path.
    "secret_path": MOUNT,
}

# One regex for the bind-shaped categories: scope . <category> . name
# (name greedily captures the remainder, which may contain dots).
#
# ⚠ The AGENT scope is DISCRIMINATED and there is NO exception: it is written
# ``agent.<agent>`` (an explicit agent name) or ``agent.default`` (the agent tier's
# FALLBACK). A BARE ``agent.<category>.<name>`` is NOT A KEY — the keyspace is
# CLOSED (spec §0), an undeclared key is not a key, so these patterns must REFUSE
# it rather than quietly accept it. Do not "helpfully" widen this back.
_AGENT_SCOPE = r"agent\.[^.]+"
_FILE_SCOPE_ALT = "system|workset|box"
_NON_TERMINAL_CATEGORY_ALT = "|".join(
    c.replace(".", r"\.") for c in _NON_TERMINAL_BIND_CATEGORIES
)
#: The categories NO scope may name in a set/reset key any more — derived as the
#: DIFFERENCE from ``_BIND_CATEGORIES``, so "settable" has exactly one definition
#: (:data:`SETTABLE_BIND_CATEGORIES`) and the regexes below cannot drift apart.
#: Since DS-BL1 = (a) that difference is ALL SIX.  It feeds BOTH scope regexes
#: below (:data:`SCOPE_BIND_KEY_RE`, :data:`AGENT_BIND_KEY_RE`), so the file and
#: agent doors cover the same categories by DERIVATION rather than by two hand
#: lists.  ⚑ ``config_keys._AGENT_NODE_BIND_RE`` also pins itself against this
#: tuple, as a SUBSET rather than an equality, and that is a MEASUREMENT: it is the
#: agent-scope READ parser, and ``agent_config.agent_file_route`` has a nested
#: table for ``bindings.<arm>.<name>`` and none for the other four (it would read
#: the dotted leaf ``self."common.x"``).  Recognition is derived here; resolution
#: is not.
RETIRED_BIND_CATEGORIES: Final[tuple[str, ...]] = tuple(
    c for c in _BIND_CATEGORIES if c not in SETTABLE_BIND_CATEGORIES
)
_RETIRED_CATEGORY_ALT = "|".join(
    c.replace(".", r"\.") for c in RETIRED_BIND_CATEGORIES
)
#: ``{system,workset,box}.<bind-shaped category>.<name>`` — the RETIRED FILE-scope
#: bind route.  It covers ALL SIX categories since DS-BL1 = (a) emptied
#: :data:`SETTABLE_BIND_CATEGORIES` (it reads
#: :data:`RETIRED_BIND_CATEGORIES`, so it widened by DERIVATION, not by an edit
#: here).  It exists ONLY to be RECOGNISED and refused by name: the verbs call
#: ``config_keys.scope_bind_retired_error`` on it, ``config get`` reads the tuple
#: through the slot it routes, and the ``pref`` value guard uses it to keep
#: refusing a scalar written at a bind-shaped target.
#:
#: ⚑ It deliberately does NOT cover the AGENT scope.  ``agent.<node>``'s node
#: segment must be split NON-GREEDILY and canonicalized (``+`` -> ``℘``) before
#: anything else can be done with it, so the agent-scope spelling has its own
#: recogniser, :data:`AGENT_BIND_KEY_RE` — built from the SAME alternation, so the
#: two doors cover the same six categories without a second hand list.
SCOPE_BIND_KEY_RE = re.compile(
    rf"^(?P<scope>{_FILE_SCOPE_ALT})"
    rf"\.(?P<category>{_RETIRED_CATEGORY_ALT})\.(?P<name>.+)$"
)
#: ``agent.<node>.<bind-shaped category>.<name>`` — the AGENT-scope twin of
#: :data:`SCOPE_BIND_KEY_RE`, over the SAME derived alternation, so the two scopes
#: cannot come to cover different category sets.  It exists for ONE reason: to be
#: RECOGNISED and refused BY NAME (``config_keys.agent_node_bind_retired_error``,
#: ``config_keys.is_known_key``).
#:
#: ⚑ THE NODE IS NON-GREEDY, and that is the whole reason this cannot be folded
#: into the regex above: a node segment may itself contain dots
#: (``navigator.v2℘claude``), so the FIRST category segment is what splits node
#: from name.  An UNDISCRIMINATED ``agent.<category>.<name>`` therefore does NOT
#: match — the agent tier is discriminated (spec §0/§2d) and an undeclared
#: spelling must stay unrecognised rather than be quietly admitted.
#:
#: ⚑⚑ RECOGNITION ONLY — IT PICKS NO READ ROUTE, and that separation is load-bearing.
#: ``config_keys._AGENT_NODE_BIND_RE`` is the narrower parser that DOES pick one
#: (``agent_config.agent_file_route``), and it covers the ``bindings`` arms alone
#: because that file-shape SoT has a nested table for ``bindings.<arm>.<name>`` and
#: none for the other four: widening THAT parser would resolve
#: ``agent.claude.common.plugins`` to the dotted leaf ``self."common.plugins"`` and
#: answer a silent "(not set)".  Recognising a spelling in order to REFUSE it is a
#: different job from resolving one, so it gets a different parser.
AGENT_BIND_KEY_RE = re.compile(
    rf"^agent\.(?P<node>.+?)"
    rf"\.(?P<category>{_RETIRED_CATEGORY_ALT})\.(?P<name>.+)$"
)
# The PER-ENTRY bind-shaped key shape — a NON-TERMINAL bind category's
# ``<scope>.<category>.<name>``, at any scope (the file scopes AND the
# discriminated ``agent.<node>``).
#
# ⚑⚑ THIS IS NOT A "SETTABLE" SHAPE ANY MORE.  Under DS-BL1 = (a) nothing
# bind-shaped is CLI-settable, so an alternation over
# :data:`SETTABLE_BIND_CATEGORIES` would be EMPTY — a ``(?P<category>)`` group that
# matches the empty string and accepts ``system..foo``.  What its consumers needed
# was the per-ENTRY shape, for a question that is not settability: does a per-entry
# dotted key EXIST here?  So it is built from the NON-TERMINAL complement; a
# terminal arm is absent for the reason it always was — there is no per-entry
# spelling of one at all.
#
# ⚑⚑⚑ IT FAILS CLOSED, AND SINCE 2026-08-08c THAT IS ITS ONLY STATE: the last
# non-terminal category went dest-keyed, ``_NON_TERMINAL_BIND_CATEGORIES`` is
# ``()``, and this compiles ``(?!)`` — no per-entry key exists under any
# bind-shaped category, therefore nothing matches.  (An empty ALTERNATION would
# instead have produced the degenerate ``system..foo``-accepting pattern, which is
# why the empty case is spelled explicitly rather than left to ``"|".join``.)
#
# ⚑ THE READ ROUTE THIS GUARD ONCE OWED ITS CONSUMERS IS PAID, and it was paid by
# ANSWERING the question rather than by widening anything: a per-entry spelling is
# not a key at any scope, so there is no per-entry read to route.  What the
# consumers needed instead was RECOGNITION — so the verbs refuse a retired spelling
# BY NAME (spec §0 refuses loudly, never quietly) and ``is_known_key`` does not
# mistake it for a project name — and that is :data:`SCOPE_BIND_KEY_RE` at the file
# scopes and :data:`AGENT_BIND_KEY_RE` at the agent scope, both derived from
# :data:`RETIRED_BIND_CATEGORIES`.  ⚑ DO NOT RE-OPEN THIS REGEX to restore a
# refusal: a match here would mean "this per-entry key exists", which is the one
# thing that is no longer true.
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
# ``{scope}.secret_path.{VAR}`` — the SECRET category (spec §2a, 2026-07-06): a
# scalar host PATH keyed by the env VAR it delivers. VAR is the env-name shape
# (mirrors :data:`ENV_KEY_RE`), never dotted.
SECRET_KEY_RE = re.compile(
    r"^(?P<scope>system|agent|workset|box)\.secret_path\."
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)$"
)
#: The bare-VAR shape (the ``name`` group of :data:`SECRET_KEY_RE`), enforced AGAIN
#: at launch emit — the VAR is interpolated into a generated ``sh -c`` export shim,
#: so a VAR that slipped past ``config set`` validation (a hand-edited YAML, or a
#: future settable surface) must be re-checked before it reaches the shell. Keep in
#: sync with the ``name`` group above.
SECRET_VAR_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Sentinel value that disables a COPY entry (the "empty" terminal, preserved
# from the seed resolver).
_DISABLE_SENTINEL = "empty"

# Apply order: REVERSE of precedence (most-specific scope lands LAST).
_SCOPE_APPLY_ORDER = {"system": 0, "agent": 1, "workset": 2, "box": 3}

# The CONCRETE MOUNT categories — the layer §0 calls the SOURCE OF TRUTH. A mount
# is emitted from a ``bindings.{ro,rw}`` declaration and from nothing else; the
# abstract categories reach a mount only by DERIVING one of these.
#
# ``secret_path`` is filed here because it is likewise an explicit, actionable
# declaration that emits a mount directly and takes no root — it is a concrete
# peer, not an abstraction. ⚑ D2 CARVE-OUT: a group whose concrete members are ALL
# ``secret_path`` is NOT a row-1 collision. Its dest is ``SECRET_MOUNT_DIR/{VAR}``
# by construction, so two such entries at one dest are always the SAME VAR arriving
# from two scopes — the ordinary per-VAR cascade that spec §2a documents as a
# FEATURE ("a box ``secret_path.<VAR>`` overrides a workset's pointer for the same
# VAR"), not two different names contending for one destination.
CONCRETE_CATEGORIES: Final[tuple[str, ...]] = (
    "bindings.ro", "bindings.rw", "secret_path",
)

# The release in which the §0 collision table replaced the flat authority ladder.
# Named ONCE: it appears in the migration-grade paragraph of every error message
# whose OUTCOME changed (M-7), and nowhere else.
_RULE_CHANGE_RELEASE: Final[str] = "1.8.0"


@dataclass(frozen=True)
class CategoryEntry:
    """One resolved scope-category entry (pre-collision-resolution).

    *category* is the category token (``"masks"``, ``"bindings.ro"``,
    ``"bindings.rw"``, ``"caches"``, ``"seeded"``, ``"common"``, ``"synced"``,
    ``"env"``, ``"secret_path"``).  *scope* is the KEY's scope.  *box_dest* is the
    in-box destination (a guest path for path categories — INCLUDING
    ``secret_path``'s ``SECRET_MOUNT_DIR/{VAR}``; the VAR name for ``env``).
    *host_src* is the resolved host source path, or ``None`` for value-only
    categories (``masks`` and ``env``).  For ``secret_path`` *host_src* is the
    SCALAR host path and *box_dest* is ``SECRET_MOUNT_DIR/{VAR}``; *name* is the
    VAR (the box-side shim exports it).  *delivery* is COPY / MOUNT / ENV per the
    category.  *options* carries mount flags (``"ro"`` / ``"Z,U"``) for MOUNT
    entries (and ``env``'s VALUE for ``env`` entries — see below).  *name* is the
    ``<name>`` leaf for diagnostics.

    For ``env`` entries, *box_dest* is the variable NAME and *options* holds the
    resolved variable VALUE (env carries no path / mount flags).

    *key_segments* is the DECLARATION KEY plus the ENTRY'S DESTINATION, ONE
    SEGMENT PER NODE (``("agent", "claude", "common", "~/.claude/plugins")``,
    ``("box", "bindings", "rw", "~/w")``) — ⚑ not a keyspace key: since
    2026-08-08c every bind-shaped category is TERMINAL, so the part that IS a key
    ends at the category and the LAST segment is the map's DEST, which is data.
    (It read ``("agent", "claude", "common", "plugins")`` while that per-name
    spelling was still a key.)  It carries the DISCRIMINATED spelling, so its
    agent segment names a real agent tier (``agent.<agent>`` / ``agent.default``)
    and never the bare ``agent.`` form §0 forbids. It is DISTINCT from *scope*,
    which is the BARE precedence token, and it replaces nothing: three consumers
    need it and none of them can be served by *scope* + *category* + *name*.

    * the derived-binding materialisation entry
      (``binding_derivations.<declaration-key>.<dest>``, the reserved internal
      snapshot node — R-8);
    * every collision error / warning message (a message that named only the
      category would not tell a user which declaration to edit);
    * the base-vs-extension provenance the §0 collision table reads.

    ⚑⚑ SEGMENTS, NOT A DOTTED STRING, AND THAT IS THE POINT.  A destination
    routinely contains ``.`` (``~/.cache/uv``, ``/home/agent/.claude/plugins``),
    so a dotted spelling is AMBIGUOUS with the key-path separator: the
    materialisation used to hand the joined form to a splitting installer and the
    dest shattered into extra tree levels, and two dests whose shattered paths
    nest (``~/.claude`` under ``~/.claude.json``) silently overwrote one another.
    Carrying segments makes the split unnecessary rather than careful.  :attr:`key`
    below is the DOTTED spelling, DERIVED — for messages and for matching, never
    for structure.

    *is_credential* tags an entry whose content is an agent CREDENTIAL.  It is the
    hook the credential ``deliver_creds`` gate (D-M4) keys off for ``seeded`` entries: a
    credential ``seeded`` copy is suppressed when the box is PRIVATE (``deliver_creds``
    False), exactly as ``synced`` (always credential-bearing) is.  Core never sets
    it; the agent
    plugin marks its cred seeds (Phase 8).  Defaults to False.

    *optional* marks a MOUNT whose SOURCE is legitimately allowed not to exist, so
    the emitter DROPS it SILENTLY instead of warning (spec §2c "SKIP-IF-ABSENT").
    The handbook's per-scope chapter binds are the live users: a workset or box that
    has written no chapter is the NORMAL case, and warning about it on every launch
    of almost every box is the noise that trains users to ignore warnings.  The
    ro-drop WARNING stays for every non-optional bind — it is the safety net for a
    mis-pathed one and must not be softened globally.  Set by KEY NAME at the
    emitter (``settings_launch.snapshot_category_entries(optional_keys=…)``), never
    by a resolve-time ``exists()`` probe: this module is PURE.

    🛑 **NOTHING READS THIS FIELD ANY MORE** (cutover step 3, 2026-08-10).  The
    emitter takes the same policy as a DEST SET parameter — ``optional`` is a
    DECLARATION fact and the fold into ``CollapsedBind(src, opts)`` has no room for
    it.  The field is left standing deliberately; its retirement is step 5's.

    ⚑⚑ THERE IS NO ``dest_space`` FIELD, AND ITS ABSENCE IS THE DESIGN (2026-08-08c).
    ``box_dest`` is a GUEST path for EVERY category, bind-shaped or copy-shaped —
    spec §0 *"ONE DEST SPACE, TWO DELIVERIES"*.  A COPY's guest dest is the
    SPELLING; the copy is RESOLVED to a host path when it runs — a ``seeded`` dest
    by ``container._guest_dest_to_host``, a ``synced`` dest by
    ``commands.start._synced_host_dest``, which resolves it through the bind that
    covers it rather than under the box home (cutover 2b-3).  Either way there are
    no longer two namespaces to tell apart, nothing for an entry to carry, and
    :func:`reconcile_categories` groups on the bare ``box_dest``.

    ⚑ WHAT THE RETIRED FIELD WAS FOR, kept as the record of a real bug it closed:
    the §2a seed layers used to spell their dest ``@meta.box.path/home`` — an
    ABSOLUTE HOST path.  On a host whose user home IS ``/home/agent`` (this
    project's own dev box, and the seadog LXC test envs) such a path STARTS WITH
    the guest home prefix, so the guest translator mapped it BACK under the box
    home and the copy landed where nothing reads it, silently reporting success.
    No prefix test could tell the two apart, so the entry carried its space.  The
    RESPELL removed the ambiguity at the source instead: the seed dest is now
    ``~/``, which needs no discriminator because it is not a host path at all.
    ⚑ Do not reintroduce a host-spelled dest — the discriminator that made it
    safe is gone.

    ⚑ THE ``canon`` NAMING TRAP, quarantined here because both spellings meet in
    this dataclass: a box store holds TWO different ``canon`` directories.
    ``<box_dir>/home/canon`` is the box's ASSEMBLED GUEST VIEW (``~/canon``,
    delivered by the home bind).  ``<box_dir>/canon`` (= the key ``@box.canon``) is
    the box's CONTRIBUTION root, whose ``handbook/`` is ONE CHAPTER bound RO at
    ``~/canon/handbook/box``.  **``@box.canon`` is NOT ``~/canon``** — same word,
    adjacent paths, opposite directions of travel.
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
    """Mount options for a bind-shaped MOUNT category.

    ``bindings.ro`` is read-only; every other rw bind category (``bindings.rw``,
    ``caches``, ``common``) gets ``Z,U`` (SELinux relabel + userns chown), the
    same options the old ``share_rw`` mounts used.
    """
    return "ro" if category == "bindings.ro" else "Z,U"


def is_read_only(options: str | None) -> bool:
    """True when mount *options* carries ``ro`` as a comma-separated TOKEN."""
    return "ro" in {token.strip() for token in (options or "").split(",")}


@dataclass(frozen=True)
class CategoryCollision:
    """One SAME-SCOPE abstraction ambiguity at one ``box_dest`` (§0 row 5).

    Returned as DATA by :func:`reconcile_categories` — which stays PURE — and
    rendered by the one emission seam
    (``kanibako.commands.start.emit_collision_warnings``).  *scope* is the bare
    scope token both declarations share; *winner_key* is the declaration that
    survives the existing ordering; *loser_keys* are the same-scope declarations
    at that dest which do not.
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


@dataclass(frozen=True)
class ReconciledCategories:
    """The reconciled, emit-ready partition of category entries (sub-step 4b).

    *mounts* are the MOUNT-delivered winners (``caches``, ``bindings.{ro,rw}``,
    ``common``, ``masks``), depth-sorted by ``box_dest`` path-depth ASCENDING
    (shallower first), so a later ``-v`` / podman's own depth-sort lands the most
    specific mount on top (mask-inside-``~/workspace``, ``home``-under-everything).
    *copies* are the COPY-delivered winners (``seeded``, ``synced``) in a
    deterministic order.  *envs* are the ENV entries (no box_dest collision —
    their "dest" is a VAR name), in deterministic order.

    Each MOUNT ``box_dest`` appears at most once (mounts SHADOW, so an
    identical-dest collision is resolved to one winner per the §0 table). COPIES
    OVERLAY rather than shadow, so a dest targeted by ``seeded`` copies ONLY keeps
    every copy (in apply order) — the layered ``seeded[~/]`` trio all seed
    into ``~`` and last-wins-merge there. A dest shared by a mount and copies
    reverts to the single mount winner (the copy cannot survive under a live
    shadow mount) EXCEPT for a ``synced`` copy-sync, which outranks a non-``masks``
    mount exactly as it always has.

    *warnings* carries the §0 row-5 SAME-SCOPE abstraction ambiguities as DATA.
    This function stays PURE (no logging, no process state), so the caller owns
    emission — see ``kanibako.commands.start.emit_collision_warnings``.
    """

    mounts: list[CategoryEntry]
    copies: list[CategoryEntry]
    envs: list[CategoryEntry]
    warnings: tuple[CategoryCollision, ...] = ()


def path_depth(box_dest: str) -> int:
    """Path-depth of a guest dest for the mount depth-sort (shallower first).

    Depth = number of non-empty path components.  ``~/`` / ``/`` is shallowest;
    ``~/workspace`` is deeper; ``~/workspace/vault`` deeper still.  Guest dests are
    already ``@``-expanded (``~`` -> ``/home/agent``) before reaching here.

    PUBLIC because emission depth-sorts too: the collapsed bind map is dest-keyed
    and carries NO order (``store_collapse.CollapsedBindings``), so
    ``commands/start._emit_category_mounts`` sorts on this same key.  One depth
    rule, two consumers — a second spelling would drift the podman mount order
    away from the reconcile's on exactly the nested dests it exists to resolve.
    """
    return len([c for c in box_dest.strip("/").split("/") if c])


def gate_credential_delivery(
    entries: list[CategoryEntry], deliver_creds: bool,
) -> list[CategoryEntry]:
    """Drop what a PRIVATE box must not receive (D-M4). PUBLIC, PURE and IDEMPOTENT."""
    # ⚑ ONE spelling of the rule, because it now has TWO consumers: the reconcile
    # below, and the ASSEMBLY COLLAPSE beside it. A second spelling would let the
    # two routes describe a differently-private box — which is how a credential
    # reaches a box the user made private.
    if deliver_creds:
        return list(entries)
    return [
        e for e in entries
        if e.category != "synced"
        and not (e.category == "seeded" and e.is_credential)
    ]


def reconcile_categories(
    entries: list[CategoryEntry],
    *,
    deliver_creds: bool = True,
) -> ReconciledCategories:
    """Resolve cross-category collisions and partition for emission (4b, D-B1).

    Takes the ordered ``list[CategoryEntry]`` produced by the category resolver
    (``snapshot_category_entries`` in the live path; apply order, see the module
    docstring) and returns a :class:`ReconciledCategories`
    with the per-dest winners split into MOUNT / COPY / ENV lists.

    The §0 COLLISION TABLE (2026-07-29) — keyed on the resolved ``box_dest``,
    NEVER on the name. It replaced the flat authority ladder
    ``seed < cache < binding < common < synced < masks``, which resolved every
    collision to one silent winner:

    ==== ============================================ ==========================
    row  case                                          outcome
    ==== ============================================ ==========================
    1    two CONCRETE declarations at one dest         **ERROR** — always
    2    ``masks`` at a dest a binding occupies        OVERRIDE (that is its job)
    3    an ABSTRACTION extending onto an occupied     **ERROR** — refuse the
         dest                                          EXTENSION, keep the base
    4    abstraction vs abstraction, DIFFERENT scopes  scope precedence, SILENT
    5    abstraction vs abstraction, SAME scope        existing ordering + WARN
    ==== ============================================ ==========================

    Rows 1/3 raise :class:`~kanibako.errors.CategoryCollisionError`; row 5 is
    RETURNED as data on :attr:`ReconciledCategories.warnings` (this function is
    pure — the caller emits).  "The existing ordering" is the input order the
    adapter produced (``scope`` apply order, then ``(category, name)``), LAST
    wins — the same ordering the retired ladder produced for every abstract pair,
    because ``common`` outranked ``caches`` there and also sorts after it here.

    ⚑ **The table governs MOUNT-vs-MOUNT only.**  The COPY layer and the
    copies-vs-mounts boundary keep their own, UNCHANGED rules (spec §0
    states them independently of the table):

    * a PURE-``seeded`` dest keeps EVERY entry — copies OVERLAY, they do not
      shadow, so the layered ``seeded[~/]`` trio is not a collision at all;
    * a ``synced`` copy-sync replaces a ``seeded`` at the same dest;
    * a ``synced`` (COPY) and a ``binding`` (MOUNT) at the EXACT same dest is a
      CONFIG ERROR (a copy cannot override a live mount), never a silent no-op;
    * otherwise, where a MOUNT winner and a COPY winner name one dest: ``masks``
      beats everything including ``synced``; ``synced`` beats every other mount;
      every mount beats ``seeded``.

    ⚑ Rows 1 and 3 are evaluated BEFORE the row-2 mask OVERRIDE.  §0 states row 1
    as "ERROR, always — any scope, any mode", and a mask that happens to cover a
    contradictory pair does not make the contradiction go away; it only hides its
    consequence.  A mask over ONE binding (the shipped shape) is untouched.

    The emitted MOUNT list is sorted by ``box_dest`` path-depth ASCENDING so
    podman's last-``-v``-wins/depth-sort resolves nested-but-different dests
    (mask-inside-``~/workspace``, ``home``-under-everything).  COPY and ENV lists
    keep a deterministic order.

    *deliver_creds* gates credential delivery (D-M4; auth-level design step 4): when
    False — the box is PRIVATE (auth tier ``"box"``, no shared source, today's
    distinct-auth) — every ``synced`` entry is SUPPRESSED, as is any ``seeded``
    entry flagged :attr:`CategoryEntry.is_credential` (the plugin's cred-seed
    hook).  When True (the box receives creds at the global OR workset tier) they are kept.
    The gate is applied BEFORE collision resolution, so a suppressed ``synced``
    cannot win — or error against — a colliding binding. (Callers pass
    ``deliver_creds=auth.creds_shared`` off the resolved
    :class:`~kanibako.settings.settings_launch.AuthSource`.)

    Raises :class:`~kanibako.errors.CategoryCollisionError` (a
    :class:`~kanibako.errors.ConfigError`) on a row-1 / row-3 collision and on a
    ``synced``↔``binding`` identical-dest collision.
    """
    # --- credential-delivery gate (D-M4): a PRIVATE box (deliver_creds=False) suppresses cred
    # deliveries up front — the same drop today's group_auth=False produced.
    # ⚑ BEFORE collision resolution, deliberately: a suppressed ``synced`` must not
    # be able to error against — or win over — a binding it never delivers.
    # ⚑ The LAUNCH caller has already applied this above both of its consumers
    # (``commands/start._resolve_launch_snapshot``); the gate is idempotent, and it
    # stays here for every OTHER caller of this function.
    gated = gate_credential_delivery(entries, deliver_creds)

    # --- env entries never collide on a guest path; keep them aside (order kept).
    envs = [e for e in gated if e.delivery == ENV]
    path_entries = [e for e in gated if e.delivery != ENV]

    # --- group by resolved box_dest; resolve each group per §0.
    # Preserve input order — "the existing ordering" the table's row 5 defers to.
    #
    # ⚑ THE BARE DEST IS THE WHOLE KEY (2026-08-08c). It used to be
    # ``(dest_space, box_dest)``, because a COPY's dest was an absolute HOST path
    # and a MOUNT's was a guest path — two namespaces that can produce the same
    # STRING. Copy dests are GUEST-spelled now (spec §0 "ONE DEST SPACE, TWO
    # DELIVERIES"), so there is one namespace and one key.
    # ⚑⚑ THIS TURNED COLLISION RULES BACK ON that the split key made unreachable:
    # a COPY and a MOUNT at one dest now meet in ``_resolve_dest_group`` and the
    # cross-delivery ladder there decides between them. That is the intended
    # consequence of collapsing the key, not a side effect to route around.
    by_dest: dict[str, list[CategoryEntry]] = {}
    for e in path_entries:
        by_dest.setdefault(e.box_dest, []).append(e)

    winners: list[CategoryEntry] = []
    warnings: list[CategoryCollision] = []
    for box_dest, group in by_dest.items():
        kept, group_warnings = _resolve_dest_group(box_dest, group)
        winners.extend(kept)
        warnings.extend(group_warnings)

    # --- partition winners by delivery; depth-sort the mounts (shallow first).
    mounts = [w for w in winners if w.delivery == MOUNT]
    copies = [w for w in winners if w.delivery == COPY]

    # MOUNT depth-sort: shallower box_dest first so the deepest (most specific)
    # mount lands LAST and wins. Stable tie-break by box_dest for determinism.
    mounts.sort(key=lambda e: (path_depth(e.box_dest), e.box_dest))
    # COPY: deterministic by box_dest (no depth constraint).
    copies.sort(key=lambda e: e.box_dest)

    return ReconciledCategories(
        mounts=mounts, copies=copies, envs=envs, warnings=tuple(warnings),
    )


def _resolve_dest_group(
    box_dest: str, group: list[CategoryEntry],
) -> tuple[list[CategoryEntry], list[CategoryCollision]]:
    """Resolve ONE ``box_dest`` group per §0 → (survivors, row-5 warnings).

    *group* is in input (apply) order.  The three layers are resolved
    independently and then reconciled across the delivery boundary; each step is
    named for the rule it implements so no single ladder stands in for five
    different decisions.
    """
    from kanibako.errors import CategoryCollisionError

    # The copy-vs-mount CONFIG ERROR (spec §0) — stated independently of
    # the collision table and UNCHANGED by it, so it is checked first and carries
    # no rule-changed paragraph.
    synced = [e for e in group if e.category == "synced"]
    bindings = [e for e in group if e.category in ("bindings.ro", "bindings.rw")]
    if synced and bindings:
        raise CategoryCollisionError(
            f"Category collision at '{box_dest}': a 'synced' copy and a "
            f"'binding' mount target the same destination.\n"
            + _entry_lines(synced + bindings)
            + "A copy cannot override a live mount — resolve by removing one "
            "(e.g. drop the binding or point the synced copy elsewhere).",
            kind="synced_vs_binding",
            box_dest=box_dest,
            entries=tuple((e.key, e.host_src) for e in synced + bindings),
        )

    mount_sub = [e for e in group if e.delivery == MOUNT]
    copy_sub = [e for e in group if e.delivery == COPY]

    mount_winner, warnings = _resolve_mount_group(box_dest, mount_sub)
    copy_winners = _resolve_copy_group(copy_sub)

    # CROSS-DELIVERY (preserved, unchanged): a mount physically shadows a copied
    # file, so it beats a ``seeded`` copy; a ``synced`` inode-swap cannot live
    # under a bind, so it beats every non-``masks`` mount; a ``masks`` tmpfs hides
    # the dest outright, so it beats even a ``synced``.
    if mount_winner is not None and copy_winners:
        if mount_winner.category == "masks":
            return [mount_winner], warnings
        if any(e.category == "synced" for e in copy_winners):
            return copy_winners, warnings
        return [mount_winner], warnings
    if mount_winner is not None:
        return [mount_winner], warnings
    return copy_winners, warnings


def _resolve_mount_group(
    box_dest: str, mount_sub: list[CategoryEntry],
) -> tuple[CategoryEntry | None, list[CategoryCollision]]:
    """Apply §0 rows 1-5 to the MOUNT entries at ONE dest (input order kept)."""
    if not mount_sub:
        return None, []
    if len(mount_sub) == 1:
        return mount_sub[0], []

    concrete = [e for e in mount_sub if e.category in CONCRETE_CATEGORIES]
    abstract = [e for e in mount_sub if e.category in ABSTRACT_CATEGORIES]
    masks = [e for e in mount_sub if e.category == "masks"]

    # ROW 1 — two CONCRETE declarations at one dest: ERROR, always. D2 carve-out:
    # an ALL-``secret_path`` set is the documented per-VAR cascade (one NAME from
    # two scopes), not two names contending, so it falls through to the ordinary
    # last-wins pick below.
    if len(concrete) > 1 and not all(
        e.category == "secret_path" for e in concrete
    ):
        raise_binding_vs_binding(box_dest, concrete)

    # ROW 3 — an ABSTRACTION extending onto a dest the CONCRETE base occupies:
    # ERROR, refusing the EXTENSION. "I wrote it down literally" wins.
    if concrete and abstract:
        raise_extension_onto_occupied(
            box_dest, extension=abstract[-1], base=concrete[-1],
        )

    # ROW 2 — a mask OVERRIDES whatever else lands here (hiding a bound path is
    # the whole job). Several masks at one dest are the same instruction twice.
    if masks:
        return _most_specific(masks), []

    # ROWS 4/5 — abstraction vs abstraction. SCOPE PRECEDENCE decides (row 4),
    # then the existing ordering within a scope; a collision WITHIN the winning
    # scope is the ambiguity §0 says to proceed on and WARN about (row 5).
    winner = _most_specific(mount_sub)
    same_scope_losers = tuple(
        e.key for e in mount_sub
        if e is not winner
        and e.scope == winner.scope
        and e.category in ABSTRACT_CATEGORIES
    )
    warnings: list[CategoryCollision] = []
    if same_scope_losers and winner.category in ABSTRACT_CATEGORIES:
        # ⚑ Only the WINNING scope's internal ambiguity is reported — spec §0
        # row 5's qualifier ("the warn applies to the WINNING scope's own
        # ambiguity", 2026-07-31). A losing scope's tie is wholly masked by row 4.
        warnings.append(CategoryCollision(
            box_dest=box_dest,
            scope=winner.scope,
            winner_key=winner.key,
            loser_keys=same_scope_losers,
        ))
    return winner, warnings


def raise_binding_vs_binding(
    box_dest: str, concrete: list[CategoryEntry],
) -> NoReturn:
    """Raise the §0 row-1 refusal: two CONCRETE declarations at one *box_dest*.

    PUBLIC because row 1 is decidable inside ONE scope as well as across two, so
    it has TWO callers: :func:`_resolve_mount_group` (the cross-scope pass) and
    the per-scope ``store_shape`` producer (``settings.store_shape``).  The
    message is spec-mandated, so it is written ONCE, here — a second remedy text
    is the drift this extraction exists to prevent.  The D2 ``secret_path``
    carve-out is the CALLER's test, not this function's: it decides whether the
    set in hand is a collision at all.
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
    """Raise the §0 row-3 refusal: *extension* extends onto the *base*'s *box_dest*.

    PUBLIC for the same reason as :func:`raise_binding_vs_binding` — row 3 is
    decidable inside ONE scope, so the per-scope ``store_shape`` producer raises
    it too, with this one message.  The BASE always survives, so the remedy names
    it without the row-1 "either one may be the one you keep" hedge.
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


def _resolve_copy_group(copy_sub: list[CategoryEntry]) -> list[CategoryEntry]:
    """Resolve the COPY entries at ONE dest (unchanged by the §0 table).

    SEEDS OVERLAY, MOUNTS SHADOW. A ``seeded`` COPY merges its tree into the dest
    PER-FILE (later scope overlays earlier — the module's "most-specific scope
    lands LAST" apply order); it does NOT physically shadow the whole dest the way
    a MOUNT does. So a dest targeted by ``seeded`` copies ONLY keeps them ALL, in
    apply order — which is what lets the layered ``seeded[~/]`` trio
    (system+agent+workset, all seeding into ``~``) co-exist and last-wins-merge at
    the seam that applies them (``commands.start._apply_init_seeds`` stages
    same-dest seeds in this order). Overlaying copies displace nothing, so this is
    not a collision and never warns.

    A ``synced`` cred copy-sync is not a layer: it REPLACES whatever else copies to
    that dest. ⚑ That pick is resolved by SCOPE PRECEDENCE, not input order, and it
    is the CREDENTIAL pick — getting it wrong copies the wrong credentials into the
    box, silently.
    """
    if not copy_sub:
        return []
    synced = [e for e in copy_sub if e.category == "synced"]
    if synced:
        return [_most_specific(synced)]
    return list(copy_sub)


def _most_specific(entries: list[CategoryEntry]) -> CategoryEntry:
    """The winner among same-layer peers: SCOPE PRECEDENCE first, then input order.

    Spec §0 row 4 says scope precedence decides an abstraction-vs-abstraction
    collision across scopes, so the CALLER's list order must not be able to
    override it (``reconcile_categories`` takes an arbitrary list; only the live
    adapter happens to hand it apply-ordered).  Within one scope the input order
    decides, LAST wins — which reproduces the retired ladder exactly, because its
    only same-scope abstract pair was ``common`` over ``caches`` and ``common``
    also sorts after ``caches``.
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


def _rule_changed(body: str) -> str:
    """The migration-grade paragraph (M-7) — ONLY on a rule whose outcome changed.

    Putting it on a rule that did NOT change trains a reader to skip it, so the
    unchanged ``synced``↔``binding`` error deliberately does not carry it.
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

    ⚑ There is no CLI verb that can express THIS suppression: ``config set``
    writes a string value, the category set path is source-only by contract, and
    ``reset`` REMOVES this file's own override (the opposite operation — it
    re-exposes the inherited entry). The one CLI suppression channel,
    ``set --null pref.<key>``, does not reach here either: a pref may be WRITTEN
    only at workset/box (``settings_prefs.PREF_LEGAL_LEVELS``, enforced by
    ``refuse_pref_table``) and may TARGET only ``system.agent`` or
    ``agent.*.**`` (``settings_prefs.ALLOWLIST``), while the occupant named here
    can be any category key of any scope. So the remedy is a hand edit of the
    settings file that owns the key, and the message says so rather than naming a
    command that would not work. It also names the SCOPE, because a box file may
    not suppress a containing scope's key by writing that scope's top-level table
    (``settings_assemble._drop_upward_scopes`` drops it) — the edit belongs in
    that scope's own file.

    *ambiguous* is True when the caller could not know WHICH entry the user wants
    to keep (row 1: two peers, either is a legitimate choice), so the printed
    block is labelled an example rather than a prescription. Row 3 passes False —
    there the occupant is determined, because the base always survives.
    """
    # ⚑ SEGMENTS, NEVER A DOTTED SPLIT. The LAST segment is the
    # entry's DESTINATION, which is data and routinely carries dots of its own
    # (``~/.cache/uv``); splitting shattered it across YAML levels and printed a
    # block that is not a declaration at all. See :class:`CategoryEntry`.
    occupant_key = ".".join(occupant_segments)
    scope = occupant_segments[0]
    last = len(occupant_segments) - 1
    indent = "  "
    lines = [f"{scope}:"]
    for depth, seg in enumerate(occupant_segments[1:], start=1):
        lines.append(f"{indent * depth}{seg}:" if depth < last
                     else f"{indent * depth}{seg}: null")
    # ⚑ The AGENT scope stores its own node under ``self.<node>`` in the per-agent
    # settings file (the shape ``_agent_partial`` reads back); the canonical
    # ``agent.<node>`` spelling is what a CONTAINING scope's file writes as a
    # defaults-down override. Printing one shape without saying so would hand the
    # reader an edit that silently does nothing in the other file.
    caveat = (
        "\n⚑ In the per-agent settings file itself the node is spelled "
        f"'self.{occupant_segments[1]}' rather\nthan "
        f"'{scope}.{occupant_segments[1]}'; the form above is "
        "what a CONTAINING scope's file writes.\n"
        if scope == "agent" and len(occupant_segments) > 1 else ""
    )
    which = (
        "Either entry may be the one you keep — the block below suppresses "
        f"'{occupant_key}';\nuse whichever key you do NOT want.\n\n"
        if ambiguous else ""
    )
    return (
        "To change what occupies a destination you must SUPPRESS the entry you do "
        "not\nwant and then declare the one you do. An override is not enough: "
        "these are two\ndifferent KEYS, so both survive the cascade. Set the "
        "unwanted key to null in the\nsettings file for its scope (a file may "
        "write its own scope and the scopes it\ncontains):\n\n"
        + which
        + "\n".join(lines)
        + "\n"
        + caveat
    )


def derive_binding_keys(
    entries: list[CategoryEntry],
) -> dict[tuple[str, ...], "Bind"]:
    """The MATERIALISED derived bindings for the ABSTRACT declarations (§0).

    ``common`` / ``caches`` / ``seeded`` are ROOTED declarations that EXTEND
    ``bindings.rw``; §0 requires the binding each one produces to be materialised
    BESIDE the declaration so ``--effective`` can show both and a reader can see
    WHY a mount exists.  The entry is
    ``binding_derivations.<declaration-key>.<dest>`` (R-8: the reserved INTERNAL
    node at the snapshot root — NOT a key) — mechanically one fixed prefix on the
    entry's own segments, so the pairing cannot drift.

    ⚑ KEYED BY SEGMENTS, and the installer that consumes this map
    (``settings_store.insert_segments``) splits nothing.  The last segment is the
    entry's DESTINATION, which is DATA and routinely contains ``.``; the dotted
    spelling that used to be returned here shattered such a dest across tree
    levels and could silently overwrite a sibling derivation.  See
    :class:`CategoryEntry` for the full account.

    ⚑ It is deliberately NOT written into ``<scope>.bindings.rw.<name>``, which is
    §0's own ruling.  Be precise about WHY, because the obvious argument is
    wrong: this map does not feed back into the entry list, so filing it under
    ``bindings.rw`` would NOT break row 3 at runtime — the reconcile never reads
    it.  What it would break is MEANING.  The concrete category is the layer §0
    calls the source of truth; a key sitting in it that no user wrote and that
    emits no mount is a FORGERY of the one thing the table reads.  Every reader —
    ``config show``, the ``--effective`` block, a future validator, the next
    person to add a consumer — would have to learn a rule ("some
    ``bindings.rw.*`` are real and some are shadows") that the
    ``binding_derivations`` prefix states structurally and for free.  The node
    is also not a key at all, and is unwritable by TWO protections: the config
    verbs refuse the head (the closed head dispatch, R-8) and assembly DROPS a
    file-borne top-level table with a warning
    (``settings_assemble._drop_upward_scopes``) — exactly the status a
    derivation has.

    PURE — takes the adapter's entry list, returns a fresh ``{key: Bind}`` map;
    the ONE seam that installs it into a snapshot is
    ``commands.start._resolve_launch_snapshot``.  Every abstract entry that
    survived the credential gate gets one, WINNERS AND LOSERS ALIKE: a losing
    declaration's derivation is exactly what explains the warning that names it,
    so hiding it would defeat the purpose.  ``seeded`` derives a COPY rather than
    a mount; the resolved pair is identical, only the delivery differs.

    ⚑ Distinct by NAME from the READ lens ``settings_views.derived_bindings`` —
    one PRODUCES the keys, the other READS them back off a snapshot.
    """
    from kanibako.settings.settings_store import BINDING_DERIVATIONS_NODE, Bind

    out: dict[tuple[str, ...], Bind] = {}
    for e in entries:
        if e.category not in ABSTRACT_CATEGORIES:
            continue
        out[(BINDING_DERIVATIONS_NODE, *e.key_segments)] = Bind(
            host=e.host_src or "", box=e.box_dest,
            opts=e.options or None,
        )
    return out


#: The message the stub below raises with. Kept as ONE string so the disabled
#: consumer can PRINT the reason instead of restating it — a second copy of this
#: sentence is a second thing to drift.
_EFFECTIVE_DELIVERY_STUB_REASON: Final[str] = (
    "the effective binding / template-source calculation is deliberately "
    "unimplemented pending the collapse function; the '--effective' "
    "binding-derivations display is disabled until it lands"
)


def effective_bindings_and_template_sources(snapshot: "KeyStore") -> Any:
    """The EFFECTIVE bindings and template sources of *snapshot* — **STUB**.

    ⚑ **DELIBERATELY UNIMPLEMENTED.** It raises :class:`NotImplementedError`
    (:data:`_EFFECTIVE_DELIVERY_STUB_REASON`), and its one consumer today — the
    ``binding_derivations`` half of the ``--effective`` block in
    :mod:`kanibako.settings.config_display` — is DISABLED rather than left to
    print something it can no longer stand behind.  Do not grow a partial
    implementation here: a stub that quietly acquires a body is worse than an
    honest hole, because nothing announces the day it started being believed.

    THE SINGLE SOURCE
        This is the ONE place the effective bindings and the effective template
        sources are calculated.  Anything that needs either — the launch, the
        display, ``workset show`` — reads it from here.  A caller that recomputes
        its own answer is a second opinion about what the box sees, which is the
        failure the ``--effective`` output exists to DETECT, not to commit.

    BUILT ON THE COLLAPSE FUNCTION
        The body will be written against the forthcoming *collapse* function
        (author: Jei), which is being built separately and which this reuses
        rather than reimplements.  That is why the body is empty and not merely
        rough: the primitive it stands on does not exist yet.

    NOTE — the two halves DO NOT share a shape, and that is not an accident.
        Bindings **COLLAPSE**: a destination can be occupied by exactly one
        winner, so the answer is a per-dest pick and the losers are gone
        (visible, if at all, as an explanation of why).  Template sources
        **LAYER**: the seed is ordered and every layer contributes, later over
        earlier, so the answer is a SEQUENCE and dropping a member loses content.
        One question, two arities — which is precisely why they are computed
        together, by one function, instead of being conflated by a caller that
        assumed they behaved alike.

        ⚑ Nothing beyond that distinction is decided here.  Which entry wins a
        dest, how layers are ordered, what the return VALUE looks like — those
        are the collapse function's contract and are OPEN.  In particular the
        return is annotated ``Any`` ON PURPOSE: a container chosen now would
        encode a merge semantics, and merge semantics is a CHOICE, not a
        property of the container.

    *snapshot* is the resolved settings snapshot every other reader already
    holds; the argument is here so the seam is real and callable, not because
    the input set is settled either.
    """
    raise NotImplementedError(_EFFECTIVE_DELIVERY_STUB_REASON)
