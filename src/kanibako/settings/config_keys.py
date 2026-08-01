"""The CLI-facing config KEY TAXONOMY — what a key is, and where it lives.

``config_interface`` exposes four verbs (get / set / reset / show) over a key
surface whose families are recognised by SPELLING: ``pref.<target>``,
``agent.<node>.<leaf>``, ``<scope>.<category>.<name>``, ``<scope>.secret_path.
<VAR>``, the bare agent behaviour keys, the routed scalars, the structural
``system.*`` path tier.  This module owns that classification — the recognizers,
the parsers, the per-family display spellings and refusal texts, the scope
tables, and the routing table — so the verbs can dispatch instead of each
re-deriving what a key is.

⚑ THIS IS NOT THE KEYSPACE VALIDATOR, AND MUST NEVER BECOME A SECOND ONE.
Two different questions look alike here and must stay apart:

* *"Is this a DECLARED key?"* — spec §0's CLOSED KEYSPACE. That question has one
  authority, :mod:`kanibako.settings.settings_keyspace`, and one answer. Code
  here CALLS INTO it; it never re-implements the test, never keeps a second copy
  of the key set for the purpose, and never answers "not a key" on its own
  authority.
* *"Which CLI-surface FAMILY is this spelling, and which file and nested slot
  does it map to?"* — this module.

The distinction is the whole reason the module exists: the classification was
smeared across ``config_interface`` as per-family free-function quintets
(recognizer · parser · display · error · target) that each verb re-dispatched
over independently. Collecting them here makes the family structure a fact you
can see rather than a discipline four verbs have to keep. The KeyKind rewrite
collapses each quintet into one descriptor IN THIS MODULE — and that rewrite has
the same constraint stated above: descriptors own the CLI-facing surface and
call into the resolver/keyspace for key semantics, or the fix manufactures the
very duplication it was meant to remove.

Layering: this module sits BELOW ``config_interface`` and ``config_dest`` and
above nothing but the keyspace primitives, so it must not import either of them.
"""

from __future__ import annotations

from enum import Enum

from kanibako.settings.config import coerce_bool
from kanibako.settings.settings_store import SCOPE_CONTAINMENT


class ConfigLevel(Enum):
    """Which scope a config operation targets."""

    box = "box"
    workset = "workset"
    agent = "agent"
    system = "system"

# Keys recognized by the unified config interface.
# This set drives the "known-key heuristic": if a positional arg matches one
# of these, it's treated as a GET request rather than a project name.
KNOWN_CONFIG_KEYS: frozenset[str] = frozenset({
    # Agent flags
    "model",
    # allow_helpers: an agent-scope BEHAVIOR key (spec §2d L557
    # ``agent.default.allow_helpers | true``). The bare key is the any-agent
    # ``agent.default`` tier (mirrors ``model``); per-agent overrides are the
    # persona key ``agent.<agent>.allow_helpers``. Gates the helper hub/socket/
    # listener at launch (start.py). Was a flat scopeless top-level scalar
    # (1.7.0-rc clean break — no back-compat for the old bare-config-field form).
    "allow_helpers",
    # auto_approve: an agent-scope BEHAVIOR key (spec §2d L556
    # ``agent.default.auto_approve | true``, PERMISSIVE). The bare key is the
    # any-agent ``agent.default`` tier (mirrors ``model``); per-agent overrides are
    # the persona key ``agent.<agent>.auto_approve``. Redeemed by each descriptor's
    # ``safe_bypass.setting_key`` at launch (claude/codex FLAG, goose GOOSE_MODE
    # ENV), coerced to bool (default True); the per-launch ``-A``/``-S`` flags
    # override it. COLLAPSES the dead ``autonomous`` persisted leaf + the claude-only
    # ``access`` string leaf (1.7.0-rc clean break — no alias for either).
    "auto_approve",
    # endpoint (persona): alternate harness base-URL, a sibling of model (block B).
    "endpoint",
    # bootstrap: an agent-scope BEHAVIOR key (spec §2d L579
    # ``agent.default.bootstrap | tmux``; "bootstrap STAYS a key"). The bare key is
    # the any-agent ``agent.default`` tier (mirrors ``model``); per-agent overrides
    # are the persona key ``agent.<agent>.bootstrap``. Names the in-box multiplexer
    # program for the persistent/reattachable session; the ``none`` sentinel means
    # ephemeral / no-reattach (foreground single-use). Consumed by start.py's
    # persistence-mode heuristic + bootstrap-wrap (consumer default ``tmux`` when
    # unset). RELOCATED from the retired BOX-scope ``box.bootstrap_program`` key
    # (1.7.0-rc clean break — no alias for the old box key).
    "bootstrap",
    # continue_mode: an agent-scope BEHAVIOR key (spec §2d L578
    # ``agent.default.continue_mode | true``; "continue vs fresh; resume removed").
    # The bare key is the any-agent ``agent.default`` tier (mirrors ``model``/
    # ``auto_approve``); per-agent overrides are the persona key
    # ``agent.<agent>.continue_mode``. Coerced to bool (default True): true ⇒
    # continue the most-recent conversation, false ⇒ start fresh. It is the
    # PERSISTED FALLBACK for the continue-vs-fresh decision at launch (start.py's
    # ``resolve_mode`` seam); the per-launch ``-N``/``-C``/``-R`` flags OVERRIDE it
    # (ephemeral wins), mirroring how ``-M`` overrides ``model`` and ``-A``/``-S``
    # override ``auto_approve``. REPLACES the dead ``start_mode`` leaf (never read at
    # launch; spec §3 L769 "``start_mode`` fully covered by ``continue_mode`` +
    # ``auto_approve``" — 1.7.0-rc clean break, no alias).
    "continue_mode",
    # Box.  ⚑ NO ``box.agent_name`` (P7): the agent SELECTION is the §2h request
    # ``pref.system.agent`` (spec §2b RETIRED the box key).
    "box.image",
    "box.share_images",
    "box.shell",
    # Auth sharing — settable 3-tier chain (system/workset/box.auth.*)
    "system.auth.share_allowed",
    "workset.auth.share_allowed",
    "workset.auth.global_sync",
    "box.auth.global_enabled",
    "box.auth.workset_enabled",
    # ``mode`` is NO LONGER a settable config-set key (block B1, spec §2b L486 /
    # §0): the project mode is the RO identity anchor ``meta.box.mode`` (surfacing
    # the runtime-resolved ``@meta.runtime.project_type``), set by the construct-
    # time/bootstrap layer ([project].mode at box creation), NOT overridable via
    # ``config set``. The mode is not persisted to disk (P8b sparse create wrote
    # no ``project:`` section; ``read_project_meta``/``write_project_meta`` were
    # deleted in P8c) — it derives from ``box_resolve`` at resolve time.
    # Vault. ``enable_vault`` migrated to the box-scope key ``box.enable_vault``
    # (P2 clean break — no ``vault.enabled`` alias). The old bare ``vault.ro``/
    # ``vault.rw`` keys are REMOVED (dead residue): P8 deleted the ``project:``
    # settings section + its reader (``read_project_meta``), so a set landed in a
    # section NOTHING reads — a silent dead write. The vault override surface is
    # now the repointable core bind ``box.bindings.{ro,rw}.vault`` (spec §2c).
    "box.enable_vault",
    # Per-workset registry location (settings-conformance P3). A NORMAL settable
    # STRING-path key (default ``@meta.workset.path/registry.yaml``), NOT a
    # config-locate key — routes to the ``workset:`` table nested slot
    # ``registry`` (the same nested-settings pattern as ``box.image``). ADDITIVE:
    # nothing consumes it yet (the launch/create cutover is P4/P5).
    "workset.registry",
    # Workset-scope LAYOUT anchors (settings-conformance P6a). These path anchors
    # are floor-materialized (settings_launch.workset_anchor_floor / start.py) as
    # OVERRIDABLE base-level defaults, but were NOT reachable through the settable
    # surface — a Type-A "meta ⟺ not-settable" violation. Jei ruled them SETTABLE:
    # they are per-workset REPOINTABLE dirs (the same nested-settings STRING-path
    # keys as ``workset.auth.share_allowed``/``workset.registry``). A ``config set
    # workset workset.boxes=…`` writes an EXPLICIT workset-level value that WINS
    # over the base floor default by cascade precedence (workset ⊐ base). NO
    # KEY_TYPES entry (all STRING paths, no bool coercion); routed to the ``workset:``
    # nested slot below. Downward-default-able from a containing scope per R2.
    "workset.auth.path",
    "workset.boxes",
    "workset.vault_ro",
    "workset.vault_rw",
    "workset.logs",
    "workset.channels.common",
    "workset.channels.chat",
    "workset.channels.share",
    # Per-workset template SOURCE (template-trio, spec §2c L507; Q3 2026-07-09).
    # A NORMAL settable STRING-path key (default ``@meta.workset.path/template``);
    # the layer-3 seed source ``workset.seeded.template = (@workset.template, ~)``
    # reads it, so repointing this key reroutes the workset template seed. Routed
    # to the ``workset:`` nested slot (same pattern as ``workset.registry``); a
    # STRING path (no KEY_TYPES). STANDALONE has no workset tier (source <None>).
    "workset.template",
    # Per-workset CANON CONTRIBUTION root (spec §2c ALL PROJECTS). Same shape and
    # same reason as ``workset.template`` above: a NORMAL settable STRING-path key
    # (default ``@meta.workset.path/canon``) that TWO things read — the ro
    # ``canon_hb_workset`` bind's source AND the ``workset.seeded.handbook`` dest —
    # so repointing it moves the workset's handbook chapter and the seed that fills
    # it together. Routed to the ``workset:`` nested slot.
    "workset.canon",
    # Per-BOX canon contribution root (spec §2b). ⚑ ``@box.canon`` is NOT ``~/canon``:
    # it is ``<box_dir>/canon`` on the HOST, whose ``handbook/`` is ONE CHAPTER bound
    # ro at ``~/canon/handbook/box``. The assembled guest view lives under the box
    # HOME and arrives through the home bind. Same word, adjacent paths, opposite
    # directions of travel.
    "box.canon",
    # Workset kuid + advisory-check toggle (settings-conformance P6d). ``workset.
    # kuid`` is the workset's stable id (Crockford-base32; sentinel ``"00000"``
    # for primary/named unless set — a STANDALONE box GENERATES a real one at
    # creation, stored here); ``workset.skip_kuid_check`` (bool, default TRUE)
    # gates the advisory ``Warning: invalid KUID``. Both settable workset.* keys
    # routed to the ``workset:`` nested slot (same pattern as ``workset.registry``).
    "workset.kuid",
    "workset.skip_kuid_check",
    # Layer-1 CONFIG-key foundation (bootstrap paths; ``[config]`` table, spec §1)
    "config.data",
    "config.settings",
    "config.agents",
    "config.primary_workset",
    "config.registry",
    # Layer-2 system.* path SETTINGS (``[system]`` table, spec §2g).  ``global``
    # is ELIMINATED (children inline ``@config.data/global/...``).
    "system.backup",
    "system.channelroot",
    # M-11: ``system.base_template`` → ``system.template``. The old spelling is
    # RETIRED, not aliased — it is not a declared key any more (spec §0's closed
    # keyspace), so ``config set system.base_template`` correctly refuses.
    "system.template",
    "system.canon",
    "system.cache",
    "system.runtime",
    # system.agent (spec §2g L1187): the CURRENT agent's name — a system-scope
    # SETTING (behavior, not a config path), so it routes to the ``system:`` table
    # of the SYSTEM SETTINGS file, NOT the [system] config table.  ⮕ P7 RENAMED it
    # from ``system.default_agent`` AND relocated it out of the reserved
    # ``agent.default`` table, where it had been an undeclared key riding the AGENT
    # tier of the real cascade; it is now an ordinary ``_KEY_ROUTES`` entry and the
    # four-site special case is gone.
    "system.agent",
})

# Prefixes for dynamic keys (env vars).
DYNAMIC_PREFIXES: tuple[str, ...] = ("env.",)

# ---------------------------------------------------------------------------
# Typed writer routing table (the H1/H2 core)
# ---------------------------------------------------------------------------
#
# The single source of truth for HOW every non-dynamic, non-env config key is
# stored.  ``get``/``set``/``reset`` all consult this table so the same key set
# is recognised on every path (no "get-validated, set-unguarded" asymmetry that
# crashed H1).  A key absent from here (and not env./agent.*/
# system.path.*) is UNKNOWN — the writer returns an error string, never raises.
#
# Each entry maps the canonical key → the nested config location it lands in:
# ``(sections_tuple, leaf_name)``.  An empty ``sections`` tuple means a
# top-level scalar field (e.g. ``allow_helpers``).  This is the *currently
# advertised* key set; later phases (4) extend it with the new categories
# (masks/bindings/synced/caches) without touching the routing mechanism.
_KEY_ROUTES: dict[str, tuple[tuple[str, ...], str]] = {
    # Box section ([box] table).
    "box.image": (("box",), "image"),
    "box.shell": (("box",), "shell"),
    "box.share_images": (("box",), "share_images"),
    # Auth sharing — settable 3-tier chain (system/workset/box.auth.*). These are
    # ordinary SETTINGS keys: each routes to its nested ``<scope>.auth.<leaf>``
    # slot in the command-scope settings file (the same nested-settings pattern as
    # ``box.image`` etc.), NOT the [project] meta table.
    # system.agent — the agent SELECTION default (spec §2g L1187). An ORDINARY
    # settings-tier route (P7): the ``system:`` table of the system settings file,
    # which is exactly where ``assemble_levels`` reads the system tier and where
    # ``config.read_system_agent`` reads it back.
    "system.agent": (("system",), "agent"),
    "system.auth.share_allowed": (("system", "auth"), "share_allowed"),
    "workset.auth.share_allowed": (("workset", "auth"), "share_allowed"),
    "workset.auth.global_sync": (("workset", "auth"), "global_sync"),
    "box.auth.global_enabled": (("box", "auth"), "global_enabled"),
    "box.auth.workset_enabled": (("box", "auth"), "workset_enabled"),
    # ``enable_vault`` is the box-scope key ``box.enable_vault`` (P2 clean
    # break): it routes to the ``box:`` table nested slot ``enable_vault`` (the
    # same nested-settings pattern as ``box.image``), read back by
    # read_box_enable_vault() from ``box.enable_vault`` — NO ``project`` fallback.
    # The old bare ``vault.ro``/``vault.rw`` keys are REMOVED (dead residue): they
    # routed to the ``project:`` section P8 DELETED (reader ``read_project_meta``
    # gone) — a silent dead write. The vault override surface is the repointable
    # core bind ``box.bindings.{ro,rw}.vault`` (spec §2c), not a bare key here.
    # ``mode`` removed from the settable routing table (block B1, spec §2b L486 /
    # §0 meta-RO): the project mode is the RO identity anchor ``meta.box.mode``,
    # never via ``config set``. The mode is not persisted to disk (P8b sparse
    # create); it derives from ``box_resolve`` at resolve time.
    "box.enable_vault": (("box",), "enable_vault"),
    # ``workset.registry`` (settings-conformance P3): the per-workset registry
    # file location, routed to the ``workset:`` table nested slot ``registry``
    # (same nested-settings pattern as ``box.image``). A STRING path — NO
    # KEY_TYPES entry (no bool coercion); written sparsely on set. ADDITIVE: no
    # consumer wiring yet (P4/P5).
    "workset.registry": (("workset",), "registry"),
    # Workset-scope LAYOUT anchors (settings-conformance P6a): the per-workset
    # REPOINTABLE dirs floor-materialized in ``workset_anchor_floor``, now settable.
    # Each routes to its nested ``workset.<...>`` slot in the command-scope settings
    # file — the SAME nested-settings pattern as ``workset.auth.share_allowed`` /
    # ``workset.registry`` — so a set-value lands where ``assemble_levels`` mirrors it
    # and OUT-PRECEDES the base floor default at launch. STRING paths (no KEY_TYPES).
    "workset.auth.path": (("workset", "auth"), "path"),
    "workset.boxes": (("workset",), "boxes"),
    "workset.vault_ro": (("workset",), "vault_ro"),
    "workset.vault_rw": (("workset",), "vault_rw"),
    "workset.logs": (("workset",), "logs"),
    "workset.channels.common": (("workset", "channels"), "common"),
    "workset.channels.chat": (("workset", "channels"), "chat"),
    "workset.channels.share": (("workset", "channels"), "share"),
    # Per-workset template SOURCE (template-trio, spec §2c L507; Q3): the layer-3
    # seed source, routed to the ``workset:`` table slot (same nested-settings
    # pattern as ``workset.registry``). STRING path (no KEY_TYPES / no bool coerce).
    "workset.template": (("workset",), "template"),
    # The per-scope CANON CONTRIBUTION roots (spec §2c/§2b), routed exactly like
    # ``workset.template`` / ``box.image`` — the ``workset:`` and ``box:`` table
    # slots. STRING paths (no KEY_TYPES / no bool coerce).
    "workset.canon": (("workset",), "canon"),
    "box.canon": (("box",), "canon"),
    # Workset kuid + advisory-check toggle (P6d): the same nested-settings pattern
    # as ``workset.registry`` — routed to the ``workset:`` table slot. ``workset.
    # kuid`` is a STRING (no KEY_TYPES entry); ``workset.skip_kuid_check`` is a bool
    # (see KEY_TYPES). A standalone box's kuid is written here sparsely at create
    # (paths.establish_standalone); primary/named default to the sentinel/true.
    "workset.kuid": (("workset",), "kuid"),
    "workset.skip_kuid_check": (("workset",), "skip_kuid_check"),
    # (``allow_helpers`` is NO LONGER a routed top-level scalar: it moved to the
    # agent keyspace (spec §2d) — the bare key routes through ``_is_agent_setting``
    # to the ``agent.default`` tier, per-agent via the ``_PERSONA_STATE_LEAVES``
    # form ``agent.<agent>.allow_helpers``, exactly like ``model``.)
}

# Keys whose values must be coerced to a real type before writing (the H2 fix).
# Boolean keys parse true/false/1/0/yes/no (case-insensitive) to a Python bool
# so the loader reads back a real bool (``set box.share_images false`` actually
# disables it).  Build this extensibly — later phases add vault_enabled etc.  The
# truth table itself lives in ``config`` (shared with the box.meta writer); see
# ``config.coerce_bool``.  NOTE: the agent-scope scalars (``allow_helpers`` /
# ``auto_approve``) are NOT here — the bare key routes through ``_is_agent_setting``
# (verbatim string write, like ``model``) and the launch reader coerces at read;
# this table only governs the ROUTED ``_KEY_ROUTES`` writer + the category
# ``validate_config_set`` path.
KEY_TYPES: dict[str, str] = {
    "box.share_images": "bool",
    "system.auth.share_allowed": "bool",
    "workset.auth.share_allowed": "bool",
    "workset.auth.global_sync": "bool",
    "box.auth.global_enabled": "bool",
    "box.auth.workset_enabled": "bool",
    "box.enable_vault": "bool",
    # P6d: gates the advisory invalid-KUID warning (default true, set via config).
    "workset.skip_kuid_check": "bool",
}

def _coerce_value(canonical: str, value: "str | None") -> object | str | None:
    """Coerce *value* to the typed form declared for *canonical* in KEY_TYPES.

    Returns the typed Python value (e.g. a real ``bool``) on success, or an
    ``"Error: ..."`` string when a bool key is given an unparseable value.
    Scalars (no KEY_TYPES entry) pass through unchanged as the raw string.
    """
    if value is None:
        return None  # an explicit present-None request (--null): never coerced.
    kind = KEY_TYPES.get(canonical)
    if kind == "bool":
        coerced = coerce_bool(value)
        if coerced is not None:
            return coerced
        return (
            f"Error: {canonical} expects a boolean "
            f"(true/false/1/0/yes/no), got {value!r}"
        )
    return value

# ---------------------------------------------------------------------------
# Scope-direction guard (block B4, spec §0 directional view/set + §2a)
# ---------------------------------------------------------------------------

# The recognized SCOPE namespaces a key may live in (its TOP-LEVEL dotted token).
# A key whose first segment is NOT one of these (``env.*`` and
# the un-prefixed scalars ``model`` / ``continue_mode`` / ``auto_approve`` /
# ``allow_helpers``) is SCOPELESS — it always writes to the command
# scope's OWN file, so the direction guard does not apply to it. ``config`` is a
# real namespace (config.* keys exist) but no config.* key actually REACHES this
# guard: set/reset short-circuit config.* earlier with the file-only refusal (B2).
_SCOPE_NAMESPACES: frozenset[str] = frozenset({
    "system", "agent", "workset", "box", "config", "meta",
})

# The CONTAINMENT order (spec §0 "Directional view/set across CONTAINMENT
# levels", repaired 2026-07-02): ``system ⊃ agent ⊃ workset ⊃ box``, OUTERMOST
# first. The single source the write-allow sets derive from — it lives in
# ``settings_store`` (the stack leaf) so the RESOLVE-time drop
# (``settings_assemble``) shares the SAME tuple without an import cycle; this is
# the module-local alias.
_SCOPE_CONTAINMENT: tuple[str, ...] = SCOPE_CONTAINMENT

# Which key-scope namespaces a COMMAND scope is allowed to WRITE (spec §0 + §2a
# "Scope-direction guard": command-scope ≥ key-scope). A scope writes its OWN
# namespace AND that of every scope it CONTAINS — the write lands in the COMMAND
# scope's file as an overridable default (the contained scope always wins per the
# cascade); writing UPWARD is refused. Derived as each scope's TAIL-SLICE of the
# containment order (one source, no per-scope hand list). ``meta.*`` is RO
# everywhere. ``config.*`` is NOT writable from ANY command scope (block B2 — it
# is bootstrap/file-only and is refused BEFORE this guard, so it appears in no
# allow-set; the older JC-B4-1 "system owns config.*" rule is superseded).
# ``box.agent.*`` (the §2b B5 downward-tweak mirror) is the BOX namespace — the
# guard keys on the TOP-LEVEL token (``box``), so ``box set box.agent.X`` is a
# legal SAME-scope write.
_SCOPE_WRITE_ALLOWED: dict[ConfigLevel, frozenset[str]] = {
    level: frozenset(_SCOPE_CONTAINMENT[_SCOPE_CONTAINMENT.index(level.value):])
    for level in ConfigLevel
}

# The scope tokens whose prefixed keys are SETTINGS keys stored in a SETTINGS
# file (a downward write keeps the key's scope token, nested in the COMMAND
# scope's settings file — spec §0; the form ``assemble_levels`` mirrors).
# ``system`` is INCLUDED (F2 fix): a routed ``system.*`` SETTINGS key (the auth
# chain ``system.auth.share_allowed``) lands in the system SETTINGS file
# (``@config.settings``) — the file the launch cascade's system tier reads —
# NOT the Layer-1 kanibako_config.yaml.  The STRUCTURAL ``system.*`` path-tier
# family never reaches this routing (refused by ``_is_system_path_key`` first).
_SETTINGS_SCOPE_TOKENS: frozenset[str] = frozenset(_SCOPE_CONTAINMENT)


def _scope_direction_error(
    canonical: str, command_scope: "ConfigLevel | None"
) -> str | None:
    """Enforce the §0 directional-WRITE rule for ``config set`` (block B4).

    A ``config set`` writes keys of the command scope's OWN namespace AND of any
    scope it CONTAINS (command-scope ≥ key-scope over ``system ⊃ agent ⊃ workset
    ⊃ box`` — a downward write is an overridable DEFAULT stored in the command
    scope's file); writing UPWARD (a CONTAINING scope's key) is REFUSED (spec §0
    "Directional view/set" + §2a "Scope-direction guard", repaired 2026-07-02).
    ``meta.*`` is a TOP-LEVEL read-only namespace — refused from EVERY scope.

    Returns an ``Error: …`` string when the write is REFUSED, or ``None`` when it
    is permitted (so the caller proceeds to dispatch).

    *command_scope* is the scope the ``config set`` was issued at (threaded by
    each caller; see the 4 command handlers). When ``None`` the guard is skipped
    (no command-scope context available — preserves callers that do not supply
    one).

    The guard keys on the key's TOP-LEVEL dotted token. A SCOPELESS key
    (``env.*`` / the un-prefixed scalars) is always permitted —
    it writes to the command scope's own file by construction.
    """
    key_scope = canonical.split(".", 1)[0]
    if key_scope not in _SCOPE_NAMESPACES:
        # Scopeless key (env.*, model, allow_helpers, …) — own-file write.
        return None
    if key_scope == "meta":
        return (
            f"Error: '{canonical}' is a read-only meta.* identity key and cannot "
            f"be set from the CLI (meta.* is set by the construct-time/bootstrap "
            f"layer, spec §0)."
        )
    if command_scope is None:
        return None
    allowed = _SCOPE_WRITE_ALLOWED.get(command_scope, frozenset())
    if key_scope in allowed:
        return None
    return (
        f"Error: '{canonical}' (scope '{key_scope}') cannot be set from the "
        f"{command_scope.value} scope. A config set writes keys of its own scope "
        f"and of scopes it contains (system ⊃ agent ⊃ workset ⊃ box, spec §0); "
        f"writing upward is refused. Set it at the {key_scope} scope instead."
    )

def _dot_to_flat(key: str) -> str:
    """Convert ``box.image`` to ``box_image``, etc."""
    return key.replace(".", "_")


# Reverse of _dot_to_flat for the routing table: the CLI surface (and prior
# code) also accepts the flat underscore form of a key (``box_image``).
# Normalise it to the canonical routing key so get/set/reset all hit the SAME
# _KEY_ROUTES entry regardless of which spelling was given.
_FLAT_TO_CANONICAL: dict[str, str] = {
    _dot_to_flat(canonical): canonical
    for canonical in _KEY_ROUTES
    if _dot_to_flat(canonical) != canonical
}


def _route_key(canonical: str) -> str:
    """Map a flat-underscore key spelling to its canonical routing key."""
    return _FLAT_TO_CANONICAL.get(canonical, canonical)
