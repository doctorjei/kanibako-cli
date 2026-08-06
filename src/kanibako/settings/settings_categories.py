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
``masks``        ``{scope}.masks``  (list[box_dest])    ``None``      MOUNT
``bindings.ro``  ``{scope}.bindings.ro.{name}``         bind          MOUNT
``bindings.rw``  ``{scope}.bindings.rw.{name}``         bind          MOUNT
``caches``       ``{scope}.caches.{name}``              bind          MOUNT
``seeded``       ``{scope}.seeded.{name}``              bind          COPY
``common``       ``{scope}.common.{name}``              bind          MOUNT
``synced``       ``{scope}.synced.{name}``              bind          COPY
``env``          ``{scope}.env.{VAR}``  (value)         ``None``      ENV
``secret_path``  ``{scope}.secret_path.{VAR}`` (path)   host path     MOUNT
================ ===================================== ============= =========

A "bind" value is a STRUCTURED pair/tuple ``[host_src, box_dest[, options]]``
unpacked by the engine's :func:`~kanibako.settings.settings_resolve.unpack_bind` (spec
§2a — never a colon-joined string).  ``masks`` carries a list of guest paths to
tmpfs-hide (no host source); ``env`` carries a scalar value for ``{VAR}`` (no
host source, no guest *path* — its ``box_dest`` field is the VAR name).

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
from typing import TYPE_CHECKING, Final, Literal, Mapping

if TYPE_CHECKING:  # pragma: no cover - typing only
    from kanibako.settings.settings_store import Bind

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

# The bind-shaped categories (one ``{scope}.<category>.<name>`` key per entry,
# value is a 2-/3-element ``[host_src, box_dest[, options]]`` tuple).  ``masks``
# (a keyed list) and ``env`` (a scalar) have bespoke key shapes handled
# separately below.
#
# ⚑ TWO TUPLES, NOT ONE — they answer different questions, and conflating them is
# what this split exists to prevent:
#
# * :data:`SETTABLE_BIND_CATEGORIES` — which bind-shaped categories may still be
#   named in a ``config set`` / ``config reset`` key.  At EVERY scope: the file
#   scopes (``system`` / ``workset`` / ``box``) AND the discriminated
#   ``agent.<node>`` scope.
# * ``_BIND_CATEGORIES`` — every bind-shaped category, i.e. which keys carry the
#   tuple VALUE SHAPE.  ``bindings.{ro,rw}`` stay in it: they are still declared
#   keys, still authored in YAML, still delivered at launch.
#
# The two diverged when the bind CLI WRITE route was RETIRED (disk-store rework
# step 1, ruling R-9 — an accepted user-surface loss, tracked as DS-BL1).  Those
# keys are becoming a TERMINAL dest-keyed map whose inner keys are NOT part of
# the keyspace, so there is no dotted key left for the CLI to name.  The
# retirement landed in two steps and is now COMPLETE — no scope may name a
# ``bindings.{ro,rw}`` entry in a set/reset key:
#
# * ``{system,workset,box}.bindings.{ro,rw}.<name>`` — recognised by
#   :data:`SCOPE_BIND_KEY_RE`, refused by ``config_keys.scope_bind_retired_error``.
# * ``agent.<node>.bindings.{ro,rw}.<name>`` — recognised by the node-splitting
#   ``config_keys._AGENT_NODE_BIND_RE`` (which must also canonicalize the ``+``
#   node segment, so it cannot be folded into the regex above), refused by
#   ``config_keys.agent_node_bind_retired_error``.
#
# Both retired spellings stay RECOGNISED so the verbs refuse them BY NAME instead
# of degrading to "unknown config key"; the closed keyspace (spec §0) refuses,
# never accepts quietly.  Both stay READABLE via ``config get``.
#
# NOTE the regex order: ``bindings.ro`` / ``bindings.rw`` must precede a bare
# ``bindings`` (there is none), and ``caches``/``seeded``/``common``/``synced`` are
# distinct tokens.  Listed longest-first so every alternation built below is
# unambiguous.
SETTABLE_BIND_CATEGORIES: Final[tuple[str, ...]] = (
    "caches", "seeded", "common", "synced",
)
_BIND_CATEGORIES = ("bindings.ro", "bindings.rw", *SETTABLE_BIND_CATEGORIES)

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
_SETTABLE_CATEGORY_ALT = "|".join(
    c.replace(".", r"\.") for c in SETTABLE_BIND_CATEGORIES
)
#: The categories NO scope may name in a set/reset key any more — derived as the
#: DIFFERENCE from ``_BIND_CATEGORIES``, so "settable" has exactly one definition
#: (the tuple above) and the two regexes below cannot drift apart.  Exported
#: because ``config_keys`` pins its own node-splitting twin against it.
RETIRED_BIND_CATEGORIES: Final[tuple[str, ...]] = tuple(
    c for c in _BIND_CATEGORIES if c not in SETTABLE_BIND_CATEGORIES
)
_RETIRED_CATEGORY_ALT = "|".join(
    c.replace(".", r"\.") for c in RETIRED_BIND_CATEGORIES
)
#: ``{system,workset,box}.bindings.{ro,rw}.<name>`` — the RETIRED FILE-scope bind
#: route (R-9). It exists ONLY to be RECOGNISED and refused by name: the verbs call
#: ``config_keys.scope_bind_retired_error`` on it, and the ``pref`` value guard uses
#: it to keep refusing a scalar written at a bind-shaped target.
#:
#: ⚑ It deliberately does NOT cover ``agent.<node>.bindings.*``.  That form is
#: retired too, but its node segment must be split NON-GREEDILY and canonicalized
#: (``+`` -> ``℘``) before anything else can be done with it, so it has always had
#: its own parser — ``config_keys._AGENT_NODE_BIND_RE`` — and that parser, not a
#: second copy of this one, is what recognises it.
SCOPE_BIND_KEY_RE = re.compile(
    rf"^(?P<scope>{_FILE_SCOPE_ALT})"
    rf"\.(?P<category>{_RETIRED_CATEGORY_ALT})\.(?P<name>.+)$"
)
# The CLI-settable bind-shaped key shape — the four SETTABLE categories, at any
# scope. ``bindings.{ro,rw}`` are absent at EVERY scope (R-9, both steps): they
# claim no ``config set`` route and no destination slot anywhere. The category
# alternation is built from :data:`SETTABLE_BIND_CATEGORIES` directly, so the
# retirement cannot be half-undone by editing one regex.
BIND_KEY_RE = re.compile(
    rf"^(?P<scope>{_FILE_SCOPE_ALT}|{_AGENT_SCOPE})"
    rf"\.(?P<category>{_SETTABLE_CATEGORY_ALT})\.(?P<name>.+)$"
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

    *key* is the FULL DECLARED KEY this entry came from
    (``agent.claude.common.plugins``, ``box.bindings.rw.home``) — the
    DISCRIMINATED spelling, so its agent segment names a real agent tier
    (``agent.<agent>`` / ``agent.default``) and never the bare ``agent.`` form
    §0 forbids. It is DISTINCT from *scope*, which is the BARE precedence
    token, and it replaces nothing: three consumers need it and none of them can
    be served by *scope* + *category* + *name*.

    * the derived-binding materialisation entry (``binding_derivations.<key>``,
      the reserved internal snapshot node — R-8);
    * every collision error / warning message (a message that named only the
      category would not tell a user which declaration to edit);
    * the base-vs-extension provenance the §0 collision table reads.

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

    ⚑⚑ *dest_space* says which FILESYSTEM NAMESPACE ``box_dest`` is spelled in, and
    it exists because the two are TEXTUALLY INDISTINGUISHABLE AND CAN COLLIDE:

    * ``"guest"`` (the default, and every MOUNT) — an in-box path such as
      ``/home/agent/.claude``, translated to its host counterpart by
      ``container._guest_dest_to_host``.
    * ``"host"`` — an ABSOLUTE HOST path a COPY writes to directly, used by the
      §2a seed layers whose dests are the keys ``@meta.box.path/home`` and
      ``@box.canon/handbook``.

    On a host whose user home IS ``/home/agent`` (this project's own dev box, and
    the seadog LXC test envs), a host dest such as
    ``/home/agent/.local/share/kanibako/…/canon/handbook`` starts with the guest
    home prefix, so the guest translator would map it BACK under the box home and
    the copy would land somewhere nothing reads — silently, reporting success.  The
    fix cannot be a smarter prefix test; the entry has to CARRY ITS SPACE.
    :func:`reconcile_categories` therefore groups on ``(dest_space, box_dest)``, so
    a host COPY and a guest MOUNT that happen to share a dest STRING stay two
    entries and the "every mount beats ``seeded``" rule cannot silently eat the seed.

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
    key: str
    is_credential: bool = False
    optional: bool = False
    dest_space: str = "guest"


def _bind_options(category: str) -> str:
    """Mount options for a bind-shaped MOUNT category.

    ``bindings.ro`` is read-only; every other rw bind category (``bindings.rw``,
    ``caches``, ``common``) gets ``Z,U`` (SELinux relabel + userns chown), the
    same options the old ``share_rw`` mounts used.
    """
    return "ro" if category == "bindings.ro" else "Z,U"


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
    every copy (in apply order) — the layered ``seeded.template`` trio all seed
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


def _path_depth(box_dest: str) -> int:
    """Path-depth of a guest dest for the mount depth-sort (shallower first).

    Depth = number of non-empty path components.  ``~/`` / ``/`` is shallowest;
    ``~/workspace`` is deeper; ``~/workspace/vault`` deeper still.  Guest dests are
    already ``@``-expanded (``~`` -> ``/home/agent``) before reaching here.
    """
    return len([c for c in box_dest.strip("/").split("/") if c])


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
      shadow, so the layered ``seeded.template`` trio is not a collision at all;
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
    gated: list[CategoryEntry] = []
    for e in entries:
        if not deliver_creds:
            if e.category == "synced":
                continue
            if e.category == "seeded" and e.is_credential:
                continue
        gated.append(e)

    # --- env entries never collide on a guest path; keep them aside (order kept).
    envs = [e for e in gated if e.delivery == ENV]
    path_entries = [e for e in gated if e.delivery != ENV]

    # --- group by (dest_space, resolved box_dest); resolve each group per §0.
    # Preserve input order — "the existing ordering" the table's row 5 defers to.
    #
    # ⚑ THE SPACE IS PART OF THE KEY, not decoration. A ``host``-space COPY dest and
    # a ``guest``-space MOUNT dest are drawn from two DIFFERENT namespaces that can
    # produce the same STRING (a host store under a ``/home/agent`` user home — see
    # ``CategoryEntry.dest_space``). Keyed on the bare dest they would be reconciled
    # as one destination and the copy-vs-mount rule would silently drop the seed.
    by_dest: dict[tuple[str, str], list[CategoryEntry]] = {}
    for e in path_entries:
        by_dest.setdefault((e.dest_space, e.box_dest), []).append(e)

    winners: list[CategoryEntry] = []
    warnings: list[CategoryCollision] = []
    for (_space, box_dest), group in by_dest.items():
        kept, group_warnings = _resolve_dest_group(box_dest, group)
        winners.extend(kept)
        warnings.extend(group_warnings)

    # --- partition winners by delivery; depth-sort the mounts (shallow first).
    mounts = [w for w in winners if w.delivery == MOUNT]
    copies = [w for w in winners if w.delivery == COPY]

    # MOUNT depth-sort: shallower box_dest first so the deepest (most specific)
    # mount lands LAST and wins. Stable tie-break by box_dest for determinism.
    mounts.sort(key=lambda e: (_path_depth(e.box_dest), e.box_dest))
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
    from kanibako.errors import CategoryCollisionError

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
            + _suppress_then_add(concrete[0].key, ambiguous=True),
            kind="binding_vs_binding",
            box_dest=box_dest,
            entries=tuple((e.key, e.host_src) for e in concrete),
        )

    # ROW 3 — an ABSTRACTION extending onto a dest the CONCRETE base occupies:
    # ERROR, refusing the EXTENSION. "I wrote it down literally" wins.
    if concrete and abstract:
        base = concrete[-1]
        extension = abstract[-1]
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
            + _suppress_then_add(base.key),
            kind="extension_onto_occupied",
            box_dest=box_dest,
            entries=tuple((e.key, e.host_src) for e in (extension, base)),
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


def _resolve_copy_group(copy_sub: list[CategoryEntry]) -> list[CategoryEntry]:
    """Resolve the COPY entries at ONE dest (unchanged by the §0 table).

    SEEDS OVERLAY, MOUNTS SHADOW. A ``seeded`` COPY merges its tree into the dest
    PER-FILE (later scope overlays earlier — the module's "most-specific scope
    lands LAST" apply order); it does NOT physically shadow the whole dest the way
    a MOUNT does. So a dest targeted by ``seeded`` copies ONLY keeps them ALL, in
    apply order — which is what lets the layered ``seeded.template`` trio
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


def _suppress_then_add(occupant_key: str, *, ambiguous: bool = False) -> str:
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
    parts = occupant_key.split(".")
    scope = parts[0]
    indent = "  "
    lines = [f"{scope}:"]
    for depth, seg in enumerate(parts[1:], start=1):
        lines.append(f"{indent * depth}{seg}:" if depth < len(parts) - 1
                     else f"{indent * depth}{seg}: null")
    # ⚑ The AGENT scope stores its own node under ``self.<node>`` in the per-agent
    # settings file (the shape ``_agent_partial`` reads back); the canonical
    # ``agent.<node>`` spelling is what a CONTAINING scope's file writes as a
    # defaults-down override. Printing one shape without saying so would hand the
    # reader an edit that silently does nothing in the other file.
    caveat = (
        "\n⚑ In the per-agent settings file itself the node is spelled "
        f"'self.{parts[1]}' rather\nthan '{scope}.{parts[1]}'; the form above is "
        "what a CONTAINING scope's file writes.\n"
        if scope == "agent" and len(parts) > 1 else ""
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


def derive_binding_keys(entries: list[CategoryEntry]) -> dict[str, "Bind"]:
    """The MATERIALISED derived bindings for the ABSTRACT declarations (§0).

    ``common`` / ``caches`` / ``seeded`` are ROOTED declarations that EXTEND
    ``bindings.rw``; §0 requires the binding each one produces to be materialised
    BESIDE the declaration so ``--effective`` can show both and a reader can see
    WHY a mount exists.  The entry is ``binding_derivations.<declaration-key>``
    (R-8: the reserved INTERNAL node at the snapshot root — NOT a key) —
    mechanically one fixed prefix on the declaration key, so the pairing is a
    string operation and cannot drift.

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

    out: dict[str, Bind] = {}
    for e in entries:
        if e.category not in ABSTRACT_CATEGORIES:
            continue
        out[f"{BINDING_DERIVATIONS_NODE}.{e.key}"] = Bind(
            host=e.host_src or "", box=e.box_dest,
            opts=e.options or None,
        )
    return out
