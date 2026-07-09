"""Unified scope-category resolution (pure, additive).

This module GENERALIZES the (now-folded) ``settings_shares`` + ``settings_seeds``
resolvers into one *category* primitive.  Every path-delivery mechanism the
settings framework exposes at a scope is a CATEGORY; this module discovers the
category keys across the precedence levels, resolves each via the engine in
:mod:`kanibako.settings_resolve`, and emits one ordered ``list[CategoryEntry]``
tagged with its *delivery* (COPY vs MOUNT).  It is **pure**: no file I/O, no
mounting/copying, no global mutable state.  It imports only stdlib and the
expression engine.

Cross-category collision resolution (identical-dest authority order, depth-order,
``synced``↔``binding`` errors, the credential ``shares`` gate) is
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
``shared``       ``{scope}.shared.{name}``              bind          MOUNT
``synced``       ``{scope}.synced.{name}``              bind          COPY
``env``          ``{scope}.env.{VAR}``  (value)         ``None``      ENV
``secret_path``  ``{scope}.secret_path.{VAR}`` (path)   host path     MOUNT
================ ===================================== ============= =========

A "bind" value is a STRUCTURED pair/tuple ``[host_src, box_dest[, options]]``
unpacked by the engine's :func:`~kanibako.settings_resolve.unpack_bind` (spec
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
guest path (so it participates in box_dest collisions with a binding-level
authority rank), and its ``host_src`` is the SCALAR path (NOT a Bind tuple).

Delivery
~~~~~~~~
* ``seeded`` and ``synced`` are file **COPIES** (synced creds inode-swap, which
  breaks single-file binds — they are copy-synced).
* ``caches``, ``bindings.ro``, ``bindings.rw``, ``shared``, ``masks`` are podman
  **MOUNTs** that physically shadow whatever is at the same path.
* ``env`` is neither — it is delivered as an environment variable (``ENV``).

Two orthogonal axes (unchanged from shares/seeds)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
* **The KEY's scope** selects the source root (via *scope_roots*) the relative
  ``host_src`` is joined under, and — for ``bindings`` — the mount mode.
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

Root-join rule
~~~~~~~~~~~~~~~
*scope_roots* maps a GROUP PREFIX (the key up to and including the category
token, e.g. ``"agent.bindings.rw"`` or ``"workset.shared"``) to a host-space
root expression.  When a root exists for a key's group AND the resolved
``host_src`` is not absolute, the source becomes ``root / host_src``; otherwise
``host_src`` is used as-is.  Groups absent from *scope_roots* mean no join.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final, Literal

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
# value is a ``host_src:guest_dest`` expression).  ``masks`` (a list) and
# ``env`` (a scalar) have bespoke key shapes handled separately below.
#
# NOTE the regex order: ``bindings.ro`` / ``bindings.rw`` must precede a bare
# ``bindings`` (there is none) and ``seeded``/``shared``/``synced`` are distinct
# tokens.  Listed longest-first so the alternation is unambiguous.
_BIND_CATEGORIES = ("bindings.ro", "bindings.rw", "caches", "seeded", "shared", "synced")

# delivery per category (the COPY/MOUNT split — design §3).
_DELIVERY: dict[str, Delivery] = {
    "masks": MOUNT,
    "bindings.ro": MOUNT,
    "bindings.rw": MOUNT,
    "caches": MOUNT,
    "seeded": COPY,
    "shared": MOUNT,
    "synced": COPY,
    "env": ENV,
    # secret_path: scalar host PATH delivered as a ro MOUNT (spec §2a SECRET
    # category) — same delivery TAG as a binding, but the value is a scalar path
    # (host_src), not a Bind tuple. Its box_dest is a real guest path.
    "secret_path": MOUNT,
}

# One regex for the bind-shaped categories: scope . <category> . name
# (name greedily captures the remainder, which may contain dots).
_CATEGORY_ALT = "|".join(c.replace(".", r"\.") for c in _BIND_CATEGORIES)
BIND_KEY_RE = re.compile(
    rf"^(?P<scope>system|agent|workset|box)\.(?P<category>{_CATEGORY_ALT})\.(?P<name>.+)$"
)
# ``{scope}.masks`` — value-less category (a list of box_dest paths). The KEY has
# no per-entry name; entries are expanded per list element (name = the index).
MASK_KEY_RE = re.compile(r"^(?P<scope>system|agent|workset|box)\.masks$")
# ``{scope}.env.{VAR}`` — scalar env var; VAR may NOT contain dots (env names).
ENV_KEY_RE = re.compile(
    r"^(?P<scope>system|agent|workset|box)\.env\.(?P<name>[A-Za-z_][A-Za-z0-9_]*)$"
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

# Category AUTHORITY order for an identical ``box_dest`` (D-B1; later WINS /
# applied-on-top): ``seed < cache < binding < shared < synced < masks``.
# ``bindings.ro`` and ``bindings.rw`` collapse to the single ``binding`` rank;
# ``seeded`` -> seed, ``caches`` -> cache.  ``env`` is not a path category (its
# "dest" is a VAR name, never a guest path) so it has no authority rank and never
# participates in box_dest collisions.
_CATEGORY_AUTHORITY: dict[str, int] = {
    "seeded": 0,        # seed
    "caches": 1,        # cache
    "bindings.ro": 2,   # binding
    "bindings.rw": 2,   # binding
    "shared": 3,        # shared
    "synced": 4,        # synced
    "masks": 5,         # masks
    # secret_path: a ro MOUNT whose box_dest IS a real guest path (unlike ``env``),
    # so it CAN collide on box_dest. Ranked at binding-level (2) — its dests live in
    # the disjoint ``SECRET_MOUNT_DIR`` namespace, so a collision only ever occurs
    # between two secret_path entries for the SAME VAR (identical box_dest), resolved
    # by scope apply-order (box wins) exactly like env precedence.
    "secret_path": 2,   # binding-level
}


@dataclass(frozen=True)
class CategoryEntry:
    """One resolved scope-category entry (pre-collision-resolution).

    *category* is the category token (``"masks"``, ``"bindings.ro"``,
    ``"bindings.rw"``, ``"caches"``, ``"seeded"``, ``"shared"``, ``"synced"``,
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

    *is_credential* tags an entry whose content is an agent CREDENTIAL.  It is the
    hook the credential ``shares`` gate (D-M4) keys off for ``seeded`` entries: a
    credential ``seeded`` copy is suppressed when the box is PRIVATE (``shares``
    False), exactly as ``synced`` (always credential-bearing) is.  Core never sets
    it; the agent
    plugin marks its cred seeds (Phase 8).  Defaults to False.
    """

    category: str
    scope: str
    box_dest: str
    host_src: str | None
    delivery: Delivery
    options: str
    name: str
    is_credential: bool = False


def _bind_options(category: str) -> str:
    """Mount options for a bind-shaped MOUNT category.

    ``bindings.ro`` is read-only; every other rw bind category (``bindings.rw``,
    ``caches``, ``shared``) gets ``Z,U`` (SELinux relabel + userns chown), the
    same options the old ``share_rw`` mounts used.
    """
    return "ro" if category == "bindings.ro" else "Z,U"


@dataclass(frozen=True)
class ReconciledCategories:
    """The reconciled, emit-ready partition of category entries (sub-step 4b).

    *mounts* are the MOUNT-delivered winners (``caches``, ``bindings.{ro,rw}``,
    ``shared``, ``masks``), depth-sorted by ``box_dest`` path-depth ASCENDING
    (shallower first), so a later ``-v`` / podman's own depth-sort lands the most
    specific mount on top (mask-inside-``~/workspace``, ``home``-under-everything).
    *copies* are the COPY-delivered winners (``seeded``, ``synced``) in a
    deterministic order.  *envs* are the ENV entries (no box_dest collision —
    their "dest" is a VAR name), in deterministic order.

    Each MOUNT ``box_dest`` appears at most once (mounts SHADOW, so an
    identical-dest collision is resolved to one authority winner). COPIES OVERLAY
    rather than shadow, so a dest targeted by copies ONLY keeps every copy (in
    apply order) — the layered ``seeded.template`` trio all seed into ``~`` and
    last-wins-merge there. A dest shared by a mount and copies reverts to the
    single mount winner (the copy cannot survive under a live shadow mount).
    """

    mounts: list[CategoryEntry]
    copies: list[CategoryEntry]
    envs: list[CategoryEntry]


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
    shares: bool = True,
) -> ReconciledCategories:
    """Resolve cross-category collisions and partition for emission (4b, D-B1).

    Takes the ordered ``list[CategoryEntry]`` produced by the category resolver
    (``snapshot_category_entries`` in the live path; apply order, see the module
    docstring) and returns a :class:`ReconciledCategories`
    with the per-dest winners split into MOUNT / COPY / ENV lists.

    Authority order (identical ``box_dest`` -> later WINS / applied-on-top):
    ``seed < cache < binding < shared < synced < masks`` (D-B1).  Among entries
    sharing a resolved ``box_dest`` the HIGHEST-authority category wins; ties
    WITHIN one authority rank are broken by :data:`_SCOPE_APPLY_ORDER` (box scope
    wins), then by the input order (stable) for full determinism.

    A ``synced`` (COPY) and a ``binding`` (MOUNT) naming the EXACT same
    ``box_dest`` is a CONFIG ERROR (a copy cannot override a live mount) — raised
    as :class:`~kanibako.errors.ConfigError`, never a silent no-op.

    The emitted MOUNT list is sorted by ``box_dest`` path-depth ASCENDING so
    podman's last-``-v``-wins/depth-sort resolves nested-but-different dests
    (mask-inside-``~/workspace``, ``home``-under-everything).  COPY and ENV lists
    keep a deterministic order.

    *shares* gates credential delivery (D-M4; auth-level design step 4): when
    False — the box is PRIVATE (auth tier ``"box"``, no shared source, today's
    distinct-auth) — every ``synced`` entry is SUPPRESSED, as is any ``seeded``
    entry flagged :attr:`CategoryEntry.is_credential` (the plugin's cred-seed
    hook).  When True (the box shares at the global OR workset tier) they are kept.
    The gate is applied BEFORE collision resolution, so a suppressed ``synced``
    cannot win — or error against — a colliding binding. (Callers pass
    ``shares=auth.shares`` off the resolved
    :class:`~kanibako.settings_launch.AuthSource`.)

    Raises :class:`~kanibako.errors.ConfigError` on a ``synced``↔``binding``
    identical-dest collision.
    """
    from kanibako.errors import ConfigError

    # --- share gate (D-M4): a PRIVATE box (shares=False) suppresses cred
    # deliveries up front — the same drop today's group_auth=False produced.
    gated: list[CategoryEntry] = []
    for e in entries:
        if not shares:
            if e.category == "synced":
                continue
            if e.category == "seeded" and e.is_credential:
                continue
        gated.append(e)

    # --- env entries never collide on a guest path; keep them aside (order kept).
    envs = [e for e in gated if e.delivery == ENV]
    path_entries = [e for e in gated if e.delivery != ENV]

    # --- group by resolved box_dest; pick the per-dest authority winner.
    # Preserve input order so ties beyond (authority, scope) are stable.
    by_dest: dict[str, list[CategoryEntry]] = {}
    for e in path_entries:
        by_dest.setdefault(e.box_dest, []).append(e)

    winners: list[CategoryEntry] = []
    for box_dest, group in by_dest.items():
        # synced (COPY) vs binding (MOUNT) at the EXACT same dest -> config error.
        has_synced = any(e.category == "synced" for e in group)
        has_binding = any(
            e.category in ("bindings.ro", "bindings.rw") for e in group
        )
        if has_synced and has_binding:
            raise ConfigError(
                f"Category collision at '{box_dest}': a 'synced' copy and a "
                f"'binding' mount target the same destination. A copy cannot "
                f"override a live mount — resolve by removing one (e.g. drop the "
                f"binding or point the synced copy elsewhere)."
            )
        # SEEDS OVERLAY, MOUNTS SHADOW. A ``seeded`` COPY merges its tree into the
        # dest PER-FILE (later scope overlays earlier — the module's "most-specific
        # scope lands LAST" apply order); it does NOT physically shadow the whole
        # dest the way a MOUNT does. So when a dest is targeted by ``seeded`` copies
        # ONLY, KEEP THEM ALL, in apply order (``group`` preserves the input order =
        # system, agent, workset, box). This is what lets the layered
        # ``seeded.template`` trio (system+agent+workset, all seeding into ``~``)
        # co-exist and last-wins-merge at the seam that applies them
        # (:func:`kanibako.commands.start._apply_init_seeds` stages same-dest seeds
        # in this order). Restricted to a PURE ``seeded`` group so the ``synced``↔
        # ``seeded`` / mount authority ordering (a cred sync or a shadow mount at
        # the same dest) is untouched — those revert to the single-winner pick.
        if all(e.category == "seeded" for e in group):
            winners.extend(group)
            continue
        # Highest authority wins; tie -> box scope wins (apply order); then the
        # stable input order. ``enumerate`` index keeps the original sequence.
        winner = max(
            enumerate(group),
            key=lambda pair: (
                _CATEGORY_AUTHORITY[pair[1].category],
                _SCOPE_APPLY_ORDER[pair[1].scope],
                pair[0],
            ),
        )[1]
        winners.append(winner)

    # --- partition winners by delivery; depth-sort the mounts (shallow first).
    mounts = [w for w in winners if w.delivery == MOUNT]
    copies = [w for w in winners if w.delivery == COPY]

    # MOUNT depth-sort: shallower box_dest first so the deepest (most specific)
    # mount lands LAST and wins. Stable tie-break by box_dest for determinism.
    mounts.sort(key=lambda e: (_path_depth(e.box_dest), e.box_dest))
    # COPY: deterministic by box_dest (no depth constraint).
    copies.sort(key=lambda e: e.box_dest)

    return ReconciledCategories(mounts=mounts, copies=copies, envs=envs)
