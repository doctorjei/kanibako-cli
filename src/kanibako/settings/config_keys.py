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
  authority, :mod:`kanibako.settings.settings_keyspace`, and one answer. The
  CONSTRAINT on this module is that it must reach that authority rather than
  grow its own answer: no second copy of the key set for the purpose, no
  "not a key" decided here.
  ⚑ Stated honestly: today this module does not import the validator at all.
  It reaches it INDIRECTLY — ``_pref_target_error`` calls
  ``settings_prefs.validate_pref``, which is where ``key_validity`` /
  ``is_valid_agent_segment`` are applied — and every other recognizer here is a
  SHAPE test that deliberately decides nothing about declaredness. So the rule
  above is a constraint to hold, not a description of an existing call; the
  KeyKind rewrite is where descriptors take the direct dependency.
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

import re
from enum import Enum
from pathlib import Path  # noqa: F401  (annotations)
from typing import Mapping

from kanibako.agent_ref import canonicalize_agent_ref, display_agent_ref
from kanibako.errors import ConfigError
from kanibako.settings.config import coerce_bool
from kanibako.settings.config_io import render_stored_scalar
from kanibako.settings.settings_keyspace import (
    ACCESS_DEFAULT,
    ACCESS_TIERS,
    leaf_name_reason,
)
from kanibako.settings.settings_prefs import PREF_ROOT
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
    # allow_helpers: an agent-scope BEHAVIOR key (spec §2d
    # ``agent.default.allow_helpers | true``). The bare key is the any-agent
    # ``agent.default`` tier (mirrors ``model``); per-agent overrides are the
    # persona key ``agent.<agent>.allow_helpers``. Gates the helper hub/socket/
    # listener at launch (start.py). Was a flat scopeless top-level scalar
    # (1.7.0-rc clean break — no back-compat for the old bare-config-field form).
    "allow_helpers",
    # access: the agent-scope PERMISSION TIER (spec §2d
    # ``agent.default.access | full``, enum ``restricted|editing|full``). The bare
    # key is the any-agent ``agent.default`` tier (mirrors ``model``); per-agent
    # overrides are the persona key ``agent.<agent>.access``. Redeemed by each
    # descriptor's ``access_realization.setting_key`` at launch (claude/codex FLAG rows,
    # goose GOOSE_MODE ENV rows) and by the PROJECTED surfaces; the per-launch
    # ``-S``/``-A`` flags override it for the ARGV only (``-S`` ⇒ restricted,
    # ``-A`` ⇒ full), never for the projection (spec §1A projected-surface
    # exception). SUPERSEDES the boolean ``auto_approve`` (R-41, 1.8.0: mapping
    # true→full / false→restricted; a stored ``auto_approve`` is REFUSED at launch
    # by ``settings_assemble.refuse_retired_behavior_keys``, never mapped
    # silently). An UNKNOWN value is rejected at SET time
    # (:func:`access_value_error`) and at launch — never treated as permissive.
    "access",
    # endpoint (persona): alternate harness base-URL, a sibling of model (block B).
    "endpoint",
    # bootstrap: an agent-scope BEHAVIOR key (spec §2d
    # ``agent.default.bootstrap | tmux``; "bootstrap STAYS a key"). The bare key is
    # the any-agent ``agent.default`` tier (mirrors ``model``); per-agent overrides
    # are the persona key ``agent.<agent>.bootstrap``. Names the in-box multiplexer
    # program for the persistent/reattachable session; the ``none`` sentinel means
    # ephemeral / no-reattach (foreground single-use). Consumed by start.py's
    # persistence-mode heuristic + bootstrap-wrap (consumer default ``tmux`` when
    # unset). RELOCATED from the retired BOX-scope ``box.bootstrap_program`` key
    # (1.7.0-rc clean break — no alias for the old box key).
    "bootstrap",
    # continue_mode: an agent-scope BEHAVIOR key (spec §2d
    # ``agent.default.continue_mode | true``; "continue vs fresh; resume removed").
    # The bare key is the any-agent ``agent.default`` tier (mirrors ``model``/
    # ``access``); per-agent overrides are the persona key
    # ``agent.<agent>.continue_mode``. Coerced to bool (default True): true ⇒
    # continue the most-recent conversation, false ⇒ start fresh. It is the
    # PERSISTED FALLBACK for the continue-vs-fresh decision at launch (start.py's
    # ``resolve_mode`` seam); the per-launch ``-N``/``-C``/``-R`` flags OVERRIDE it
    # (ephemeral wins), mirroring how ``-M`` overrides ``model`` and ``-S``/``-A``
    # override ``access``. REPLACES the dead ``start_mode`` leaf (never read at
    # launch; spec §3 "``start_mode`` fully covered by ``continue_mode`` +
    # ``auto_approve``" — 1.7.0-rc clean break, no alias; ``auto_approve`` has
    # itself since been superseded by ``access``, R-41).
    "continue_mode",
    # Box.  ⚑ NO ``box.agent_name`` (P7): the agent SELECTION is the §2h request
    # ``pref.system.agent`` (spec §2b RETIRED the box key).
    "box.image",
    "box.share_images",
    # box.images_store — the host image-store root behind the shared-images bind
    # (spec §2b; B3): a USER key whose DEFAULT is the runtime-probed podman
    # graphroot, injected as a floor scalar at the launch seam
    # (core_defaults.image_default_categories). A STRING path — no KEY_TYPES
    # entry (only bools are coerced, cf. ``box.share_images``).
    "box.images_store",
    "box.shell",
    # Auth sharing — settable 3-tier chain (system/workset/box.auth.*)
    "system.auth.share_allowed",
    "workset.auth.share_allowed",
    "workset.auth.global_sync",
    "box.auth.global_enabled",
    "box.auth.workset_enabled",
    # ``mode`` is NO LONGER a settable config-set key (block B1, spec §2b /
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
    # workset.workspaces / workset.channelroot — the two RESOLVED workset dir
    # keys (§3.3: real and USED; manifest ``set: cli+file``).  Declared in the
    # keyspace and consumed live (``resolve_workset_workspaces`` /
    # ``resolve_workset_channelroot`` read the ``workset:`` nested slot this
    # route writes), but absent HERE — so ``workset set workset.workspaces=…``
    # refused with "unknown config key" and a repoint required a settings-file
    # edit (bifrost A1).  Same shape as the sibling anchors above: STRING paths
    # (no KEY_TYPES), routed to the ``workset:`` nested slot.
    "workset.workspaces",
    "workset.channelroot",
    "workset.channels.common",
    "workset.channels.chat",
    "workset.channels.share",
    # Per-workset template SOURCE (template-trio, spec §2c; Q3 2026-07-09).
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
    # config.journal — the lifecycle-journal location (§3.3 ruling "needs to be
    # recognized"): resolved/consumed as ``std.journal`` all along, but absent
    # from this list, so the config verbs treated the key name as a project
    # name.  Recognition here gives it EXACT sibling parity: the get/show path
    # reads it through ``is_system_path_key``'s ``config.`` branch, and set/
    # reset refuse it with the ruled bootstrap-file message like the other five.
    "config.journal",
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
    # system.agent (spec §2g): the CURRENT agent's name — a system-scope
    # SETTING (behavior, not a config path), so it routes to the ``system:`` table
    # of the SYSTEM SETTINGS file, NOT the [system] config table.  ⮕ P7 RENAMED it
    # from ``system.default_agent`` AND relocated it out of the reserved
    # ``agent.default`` table, where it had been an undeclared key riding the AGENT
    # tier of the real cascade; it is now an ordinary ``_KEY_ROUTES`` entry and the
    # four-site special case is gone.
    "system.agent",
})

# The RETIRED bare env-var prefix (R-39, spec §2a: the env family is SCOPED —
# ``<scope>.env.<VAR>``; a bare ``env.<VAR>`` is not a key). Kept ONLY so the
# spelling stays RECOGNISED as key-shaped: ``is_known_key`` must not read it as
# a project name, and the verbs refuse it with the cure
# (:func:`bare_env_retired_error`) rather than fail as an unknown key.
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
    # box.images_store (B3): routes to the ``box:`` table nested slot
    # ``images_store`` — the SAME nested-settings pattern as ``box.image``, and
    # exactly the file shape the launch cascade reads when resolving the
    # ``@box.images_store`` host_src of the shared-images bind.
    "box.images_store": (("box",), "images_store"),
    # Auth sharing — settable 3-tier chain (system/workset/box.auth.*). These are
    # ordinary SETTINGS keys: each routes to its nested ``<scope>.auth.<leaf>``
    # slot in the command-scope settings file (the same nested-settings pattern as
    # ``box.image`` etc.), NOT the [project] meta table.
    # system.agent — the agent SELECTION default (spec §2g). An ORDINARY
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
    # ``mode`` removed from the settable routing table (block B1, spec §2b /
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
    # workset.workspaces / workset.channelroot (§3.3, bifrost A1): routed to the
    # ``workset:`` nested slot — EXACTLY where ``resolve_workset_workspaces`` /
    # ``resolve_workset_channelroot`` (and ``resolve_workset_registry_path``'s
    # sibling pattern) read the repoint back.  STRING paths (no KEY_TYPES).
    "workset.workspaces": (("workset",), "workspaces"),
    "workset.channelroot": (("workset",), "channelroot"),
    "workset.channels.common": (("workset", "channels"), "common"),
    "workset.channels.chat": (("workset", "channels"), "chat"),
    "workset.channels.share": (("workset", "channels"), "share"),
    # Per-workset template SOURCE (template-trio, spec §2c; Q3): the layer-3
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
# ``access``) are NOT here — the bare key routes through ``_is_agent_setting``
# (verbatim string write, like ``model``) and the launch reader coerces at read;
# this table only governs the ROUTED ``_KEY_ROUTES`` writer + the category
# ``validate_config_set`` path.  ``access`` is an ENUM, not a bool: its set-time
# guard is :func:`access_value_error` (which REFUSES an unknown value outright
# rather than coercing it), not a KEY_TYPES coercion.
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
# A key whose first segment is NOT one of these (the un-prefixed scalars
# ``model`` / ``continue_mode`` / ``access`` / ``allow_helpers``) is
# SCOPELESS — it always writes to the command
# scope's OWN file, so the direction guard does not apply to it. (The RETIRED
# bare ``env.<VAR>`` is scopeless in SHAPE too, but it never reaches this guard:
# set/reset refuse it in the preamble — R-39. The live env family is
# ``<scope>.env.<VAR>``, whose token IS a namespace and IS guarded.) ``config`` is a
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
# family never reaches this routing (refused by ``is_system_path_key`` first).
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
    (the un-prefixed scalars) is always permitted — it writes to the command
    scope's own file by construction. (The RETIRED bare ``env.*`` is scopeless
    in shape too but never arrives here: the verbs refuse it earlier — R-39.)
    """
    key_scope = canonical.split(".", 1)[0]
    if key_scope not in _SCOPE_NAMESPACES:
        # Scopeless key (model, allow_helpers, …) — own-file write.
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


# ---------------------------------------------------------------------------
# Canonical key resolution
# ---------------------------------------------------------------------------

def resolve_key(raw: str) -> str:
    """Return the canonical config key for a user-supplied key name.

    Most config keys are already canonical (dot-notation like ``box.image`` or
    ``box.enable_vault``, or a raw flat key) and pass through unchanged; this is the
    single canonicalization seam every get/set/reset path routes through.

    The ONE canonicalization it performs (block B1): for a per-persona agent key
    ``agent.<node>.<key>`` it canonicalizes the ``<node>`` SEGMENT ``+`` -> ``℘``
    (``agent.navigator+claude.endpoint`` -> ``agent.navigator℘claude.endpoint``),
    so the write/get/reset all target the canonical ``agents/<node>/`` slot the
    resolver reads.  The node segment is canonicalized as a WHOLE via
    :func:`canonicalize_agent_ref` (agent_ref design law: never re-split a ref on
    the raw separator); the tail (``endpoint`` / ``env.<VAR>`` / ``secret_path.<VAR>``)
    is preserved verbatim.  A malformed node is left RAW here — the set/reset
    persona branch surfaces the parse error (and a bad node never silently swaps).
    Applied ONLY to the ``agent.<node>.*`` node segment, never blindly to all keys.

    The per-node DESCRIPTOR bind key ``agent.<node>.bindings.{ro,rw}.<name>`` (item-0)
    is canonicalized the SAME way (``<node>`` ``+`` -> ``℘``) and is matched BEFORE the
    persona form — a bind named after a persona state leaf
    (``agent.<node>.bindings.ro.model``) would otherwise be mis-parsed by
    :func:`_parse_persona_agent_key` (``model`` is a state leaf). The ``bindings.
    {ro,rw}`` category segment + the bind name are preserved verbatim.
    """
    bind = parse_agent_node_bind_key(raw)
    if bind is not None:
        node_raw, cat, name = bind
        try:
            node = canonicalize_agent_ref(node_raw)
        except ConfigError:
            return raw
        return f"agent.{node}.{cat}.{name}"
    secret = _parse_agent_node_secret_key(raw)
    if secret is not None:
        node_raw, var = secret
        try:
            node = canonicalize_agent_ref(node_raw)
        except ConfigError:
            return raw
        return f"agent.{node}.secret_path.{var}"
    parsed = _parse_persona_agent_key(raw)
    if parsed is None:
        return raw
    node_raw, tail = parsed
    try:
        node = canonicalize_agent_ref(node_raw)
    except ConfigError:
        return raw
    return f"agent.{node}.{tail}"

# ---------------------------------------------------------------------------
# Per-persona agent keys (block B1) — ``agent.<node>.<key>`` set on the agent's
# OWN settings file ``agents/<node>/settings.yaml``.
# ---------------------------------------------------------------------------

# The settable per-persona agent leaves: the FLAT agent-state knobs (``_is_agent_
# setting`` set) plus the ``env.`` section — the EXACT shape
# ``agent_config.load_agent_config`` reads back (``AgentConfig.state`` / ``.env``),
# so a value ``set`` here is what the launch snapshot resolves for the persona
# (endpoint via ``effective_behavior``). The former ``env_file.`` section is RENAMED
# to the DISCRIMINATED ``agent.<node>.secret_path.<VAR>`` SECRET category (routed by
# ``_is_agent_node_secret_key`` → ``_node_secret_target``, NOT here — a clean break;
# ``env_file`` only shipped rc0-rc2, no alias).
_PERSONA_STATE_LEAVES: frozenset[str] = frozenset(
    {
        "endpoint", "model", "continue_mode", "access", "allow_helpers",
        "bootstrap",
        # Per-agent template SOURCE (template-trio, spec §2a/§2d; Q2 2026-07-09
        # "agent = persona + harness"). A settable STRING-path leaf on the agent
        # (persona+harness) node — default @config.agents/<harness>/template — read
        # by the layer-2 seed ``agent.<a>.seeded.template = (@agent.<a>.template, ~)``,
        # so repointing it reroutes the agent template seed.
        "template",
        # Per-agent CANON CONTRIBUTION root (spec §2d) — the source of the ro
        # ``canon_hb_agent`` bind, i.e. WHERE THIS AGENT'S HANDBOOK CHAPTER LIVES.
        # A settable STRING-path leaf on the agent (persona+harness) node, wired
        # here for the same reason ``template`` is: both are per-agent path SOURCES a
        # user may legitimately relocate, and both default off the agent's store.
        # ⚑ Its FLOOR default is chosen per J-1: the node's own store when that store
        # provides a canon, else the ``agent.default`` tier — and a value set HERE
        # beats the floor either way, which is what makes the tier a fallback rather
        # than a ceiling.
        "canon",
    }
)
_PERSONA_ENV_SECTIONS: frozenset[str] = frozenset({"env"})

# The RESERVED any-agent tier name (mirrors ``settings_assemble._AGENT_DEFAULT_SUB``
# / ``config.read_agent_settings``: "no real agent may be named default"). It is
# NOT a persona node — an ``agent.default.<key>`` write is refused (the any-agent
# default is the BARE key), so nothing lands at a never-read ``agents/default/``.
AGENT_DEFAULT_SUB = "default"


def _parse_persona_agent_key(key: str) -> "tuple[str, str] | None":
    """Split an ``agent.<node>.<tail>`` persona key into ``(node_raw, tail)``.

    Returns ``None`` when *key* is not a settable per-persona agent key. The
    settable *tail* forms are a FLAT state leaf (``endpoint`` / ``model`` /
    ``continue_mode`` / ``access`` / ``allow_helpers``) or a sectioned ``env.<VAR>``
    pointer.  The SECRET pointer ``secret_path.<VAR>`` is NOT parsed here — it is
    matched EARLIER (``_is_agent_node_secret_key``) and stored DISCRIMINATED (spec §2a;
    it replaced the rc-only ``env_file.<VAR>``, which routed here).  The node segment
    is returned VERBATIM (possibly a ``+`` form, possibly itself dotted — a
    persona/harness segment may contain ``.``) for :func:`canonicalize_agent_ref` to
    canonicalize as a WHOLE.

    Parsed from the RIGHT: the closed set of settable tails is unambiguous, so
    everything left of a recognised tail is the node.  ``env`` is matched BEFORE the
    flat leaves so ``agent.<node>.env.MODEL`` is an env var named ``MODEL``, never
    mis-split as the state leaf ``model``.
    """
    if not key.startswith("agent."):
        return None
    rest = key[len("agent."):]
    parts = rest.split(".")
    # env.<VAR> — the section is the 2nd-from-last segment.
    if len(parts) >= 3 and parts[-2] in _PERSONA_ENV_SECTIONS:
        return (".".join(parts[:-2]), f"{parts[-2]}.{parts[-1]}")
    # Flat state leaf — the last segment.
    if len(parts) >= 2 and parts[-1] in _PERSONA_STATE_LEAVES:
        return (".".join(parts[:-1]), parts[-1])
    return None


def _is_persona_agent_key(key: str) -> bool:
    """True iff *key* is a settable per-persona ``agent.<node>.<key>`` key (B1)."""
    return _parse_persona_agent_key(key) is not None


def is_access_key(canonical: str) -> bool:
    """True iff *canonical* is the auth-critical ``access`` permission key.

    Matches both DIRECT settable forms: the BARE any-agent ``agent.default`` tier
    key (``canonical == "access"``, routed via :func:`_is_agent_setting`) and a
    per-persona override ``agent.<node>.access`` (routed via
    :func:`_is_persona_agent_key`).  Used to WRITE-VALIDATE the value at ``config
    set`` time (:func:`access_value_error`): ``access`` decides whether the box's
    agent prompts at all, so an unrecognised value must be REJECTED at the write,
    never stored to be re-read at launch.  Only ``access`` gets this guard (Jei:
    only the auth-critical key), not ``allow_helpers`` / ``model``.

    ⚑ It answers False for the §2h REQUEST spelling ``pref.agent.<node>.access``,
    and that is correct rather than a gap: this predicate names TARGET keys, and
    a pref is not its target.  The request form is guarded at its TARGET, by
    ``config_interface._pref_value_error`` — which calls THIS function on the
    target it extracts, so both spellings redeem the one truth table.  (That call
    is what closes the hole; before it, ``box set pref.agent.claude.access=fll``
    — the very command the RQ-2 refusal prescribes — was accepted and stored.)

    ⚑ R-41 (1.8.0): this is the RENAMED ``is_auto_approve_key``. The rename
    follows the KEY, not the name — the guard exists because this key is the
    permission axis, so it must travel with the axis when the axis is respelled.
    """
    if canonical == "access":
        return True
    parsed = _parse_persona_agent_key(canonical)
    return parsed is not None and parsed[1] == "access"


def access_value_error(canonical: str, value: str) -> str | None:
    """The LOUD refusal for an illegal ``access`` value, or ``None`` when legal.

    The single set-time validator for the permission tier, shared by BOTH set
    paths (``config_interface.validate_config_set`` and the ``agent set``
    verb) so there is one message and one truth table.  Legal values are exactly
    :data:`~kanibako.settings.settings_keyspace.ACCESS_TIERS`; the message NAMES
    the key and lists them.

    ⚑ Never lenient, never permissive-by-default: an unknown value is refused
    outright rather than coerced or defaulted (a typo must not be the difference
    between an agent that prompts and one that does not).  Matching is EXACT —
    no case folding — because the stored value is what the launch resolver reads
    back and the launch resolver is exact too.
    """
    if value in ACCESS_TIERS:
        return None
    legal = " | ".join(ACCESS_TIERS)
    return (
        f"Error: {canonical} must be one of {legal} (spec §2d); got {value!r}. "
        f"An unrecognised permission tier is REFUSED, never treated as "
        f"'{ACCESS_DEFAULT}'."
    )

# ---------------------------------------------------------------------------
# Per-node DESCRIPTOR bind keys (item-0) — ``agent.<node>.bindings.{ro,rw}.<name>``
# repointed (source-only) on the agent's OWN settings file, via the CATEGORY path.
# ---------------------------------------------------------------------------

# ``agent.<node>.bindings.{ro,rw}.<name>`` — the per-node descriptor delivery bind
# (claude launcher/share …). ``<node>`` is NON-greedy so the FIRST ``.bindings.
# {ro,rw}.`` segment splits node from name (a bind literally NAMED ``model`` — the
# name group — is thus ``agent.<node>.bindings.ro.model``, disambiguated from the
# persona state leaf ``agent.<node>.model`` by the ``bindings.{ro,rw}`` segment).
# NOTE: the agent tier is DISCRIMINATED — ``agent.<node>`` is the ONLY agent form
# (§2d / §0); an undiscriminated ``agent.<category>`` is not a key and
# ``BIND_KEY_RE`` refuses it. ``BIND_KEY_RE`` DOES match this node form (it is a
# well-formed category key), so both predicates fire — every dispatch checks the
# node-bind FIRST. This does not match the ``box.agent.bindings.*`` box-mirror form
# (a ``box`` top-token).
_AGENT_NODE_BIND_RE = re.compile(
    r"^agent\.(?P<node>.+?)\.(?P<cat>bindings\.(?:ro|rw))\.(?P<name>.+)$"
)


def parse_agent_node_bind_key(key: str) -> "tuple[str, str, str] | None":
    """Split ``agent.<node>.bindings.{ro,rw}.<name>`` into ``(node_raw, cat, name)``.

    Returns ``None`` when *key* is not a per-node descriptor bind key. ``cat`` is the
    ``bindings.ro`` / ``bindings.rw`` segment; ``node_raw`` is VERBATIM (possibly a
    ``+`` form) for :func:`canonicalize_agent_ref` to canonicalize as a WHOLE. Parsed
    BEFORE :func:`_parse_persona_agent_key` everywhere so a bind named after a persona
    state leaf (``agent.claude.bindings.ro.model``) is a BIND, never mis-split as the
    state key ``agent.claude.model``.
    """
    m = _AGENT_NODE_BIND_RE.match(key)
    if m is None:
        return None
    return m.group("node"), m.group("cat"), m.group("name")


def _is_agent_node_bind_key(key: str) -> bool:
    """True iff *key* is a per-node descriptor bind ``agent.<node>.bindings.*`` key
    (item-0). Checked BEFORE :func:`_is_persona_agent_key` in the routing dispatch."""
    return parse_agent_node_bind_key(key) is not None

# ``agent.<node>.secret_path.<VAR>`` — the per-node SECRET category (spec §2a, 2026-
# 07-06). Like the descriptor bind key it is DISCRIMINATED (node in the key) and
# stored UNDER the ``agent.<node>.secret_path`` sub-table in the node's OWN settings
# file — the shape ``_agent_partial`` reads into the launch cascade — but the value
# is a SCALAR host PATH, not a Bind tuple (so it routes via a plain scalar write, NOT
# ``_set_category_value``/``repoint_host_src``). ``<node>`` is NON-greedy so the
# FIRST ``.secret_path.`` splits node from VAR; VAR is the env-name shape (no dots).
_AGENT_NODE_SECRET_RE = re.compile(
    r"^agent\.(?P<node>.+?)\.secret_path\.(?P<var>[A-Za-z_][A-Za-z0-9_]*)$"
)


def _parse_agent_node_secret_key(key: str) -> "tuple[str, str] | None":
    """Split ``agent.<node>.secret_path.<VAR>`` into ``(node_raw, var)``, or ``None``.

    ``node_raw`` is VERBATIM (possibly a ``+`` form) for :func:`canonicalize_agent_ref`
    to canonicalize as a WHOLE. Parsed BEFORE :func:`_parse_persona_agent_key` so a
    secret pointer never falls through to the (now env_file-less) persona branch.
    """
    m = _AGENT_NODE_SECRET_RE.match(key)
    if m is None:
        return None
    return m.group("node"), m.group("var")


def _is_agent_node_secret_key(key: str) -> bool:
    """True iff *key* is a per-node ``agent.<node>.secret_path.<VAR>`` key (SECRET
    category). Checked BEFORE the persona + path-category branches in dispatch."""
    return _parse_agent_node_secret_key(key) is not None

def _persona_display_key(canonical: str) -> str:
    """Render a canonical persona key for USER-FACING output (``℘`` -> ``+``)."""
    parsed = _parse_persona_agent_key(canonical)
    if parsed is None:
        return canonical
    node, tail = parsed
    return f"agent.{display_agent_ref(node)}.{tail}"


def _node_secret_display_key(canonical: str) -> str:
    """Render a canonical ``agent.<node>.secret_path.<VAR>`` key for USER-FACING
    output (``℘`` -> ``+`` on the node segment)."""
    parsed = _parse_agent_node_secret_key(canonical)
    if parsed is None:
        return canonical
    node, var = parsed
    return f"agent.{display_agent_ref(node)}.secret_path.{var}"

def _floor_bind_display(
    canonical: str, default_categories: "Mapping[str, object] | None",
) -> "tuple[str, str] | None":
    """The reverted-to descriptor FLOOR ``(value, tier)`` a reset of a floor bind
    lands on (item 3), or ``None`` when no registry is threaded / no floor entry.

    *default_categories* is the SAME context-light floor registry the set path folds
    (``agent_representation.agent_default_bind_keys`` for a node bind). Its element-0
    host_src is a SET-TIME SENTINEL (``core_defaults.FLOOR_PLACEHOLDER_SRC``) — the
    real host source is re-resolved at LAUNCH (``detect()``), so it is NEVER printed
    as a value (evidence-honesty: the exact fabricate-a-value lie the honest-reset
    fix targets). We report the STATIC part that actually reverts — the descriptor
    destination [+ options] — and name the tier so the user knows the host source is
    launch-resolved. A non-tuple / absent / placeholder-only entry → ``None`` (keep
    the cleared-only form).
    """
    from kanibako.settings.core_defaults import FLOOR_PLACEHOLDER_SRC

    if not default_categories:
        return None
    val = default_categories.get(canonical)
    if not isinstance(val, (list, tuple)) or len(val) < 2:
        return None
    parts = list(val)
    if parts and parts[0] == FLOOR_PLACEHOLDER_SRC:
        parts = parts[1:]  # drop the set-time sentinel — it is not a launch value
    if not parts:
        return None
    rendered = render_stored_scalar(parts)
    if rendered is None:
        return None
    return (rendered, "descriptor floor; host re-resolved at launch")

def _is_bare_env_key(key: str) -> bool:
    """The RETIRED bare docker-``.env`` spelling ``env.<VAR>`` (R-39, spec §2a).

    ⮕ **R-39 RETIRED THE BARE SPELLING.** The keyspace env family is SCOPED —
    ``<scope>.env.<VAR>``, matched by :func:`_is_scope_env_key` — and the bare
    form wrote the docker ``.env`` FILE instead: an undiscriminated variant that
    silently meant something different from the discriminated key (Code
    Convention 0's failure mode). This predicate now exists ONLY to RECOGNISE
    the retired spelling so set / reset / get can refuse it with the cure
    (:func:`bare_env_retired_error`) rather than fail as an unknown key — the
    same recognise-to-refuse role as :func:`_is_box_agent_key` (P7).
    """
    return key.startswith("env.")


# ``<scope>.env.<VAR>`` for the non-agent scopes (system/workset/box) — the LIVE
# env family (spec §2a L383 ``<scope>.env.<VAR> | value | scoped env var``; §2a
# L496 "Scalars (incl. ``env.<VAR>``, whose value is scalar) → full CLI set").
# The AGENT scope form ``agent.<node>.env.<VAR>`` is DISCRIMINATED and routed by
# ``_is_persona_agent_key`` (the node file); this covers the other three, which
# write a scalar to the COMMAND scope's OWN settings file at
# ``<scope>.env.<VAR>`` — the shape ``_file_partial`` reads into the cascade and
# ``settings_launch._emit_scope_node`` delivers as a ``category="env"`` entry.
# Deliberately the SIBLING-EXACT twin of ``_SCOPE_SECRET_RE``: same three scopes,
# same VAR shape (no dots), same NOUN-file destination. VAR matching is
# CASE-SENSITIVE (spec §0 "Reserved key names" / §2a L386) — there is no
# case-folding anywhere on this path.
_SCOPE_ENV_RE = re.compile(
    r"^(?P<scope>system|workset|box)\.env\.(?P<var>[A-Za-z_][A-Za-z0-9_]*)$"
)


def _is_scope_env_key(key: str) -> bool:
    """True iff *key* is a NON-agent ``<scope>.env.<VAR>`` key (system/workset/
    box) — settable to the command scope's own settings file.

    SHAPE only. The spec §0 RESERVED-NAME floor is enforced at WRITE time by
    :func:`scope_env_var_error`, not here: a reserved VAR must be refused by
    NAME ("'get' is a RESERVED key name"), and excluding it from the shape test
    would instead report the far less useful "unknown config key".
    """
    return _SCOPE_ENV_RE.match(key) is not None


def scope_env_var_error(canonical: str) -> str | None:
    """Refuse a ``<scope>.env.<VAR>`` WRITE whose VAR is a RESERVED name.

    Spec §0: an ``env.<VAR>`` name may not be a public ``dict`` method name nor
    match the dunder pattern, and the refusal is due "loudly at write/``config
    set`` time" — this is that site (set AND reset; a reset is a write, and
    "No override for box.env.get" would imply the name was legal).

    Returns an ``Error: …`` string when refused, else ``None`` (including for
    every non-scope-env key, so the verbs can apply it unconditionally).
    """
    m = _SCOPE_ENV_RE.match(canonical)
    if m is None:
        return None
    reason = leaf_name_reason(m.group("var"))
    return None if reason is None else f"Error: {reason}."


def bare_env_retired_error(
    key: str, *, verb: str, command_scope: "ConfigLevel | None" = None,
) -> str | None:
    """The refusal + cure for a RETIRED bare ``env.<VAR>`` op (R-39, spec §2a),
    or ``None`` when *key* is not a bare env key.

    The cure NAMES the DISCRIMINATED key for the command scope (``box`` when no
    scope is threaded — the box tier is where the old spelling's writes bit) and
    quotes the user's spelling. That key is REAL and REACHABLE: this same change
    routed ``<scope>.env.<VAR>`` through get / set / reset
    (:func:`_is_scope_env_key`), so the cure is an instruction the user can
    follow verbatim, not a pointer at an unimplemented key. The one scope that
    cannot be named verbatim is ``agent``: it is DISCRIMINATED, so the cure
    carries an explicit ``<agent>`` placeholder rather than the illegal bare
    ``agent.env.<VAR>`` (see the guard below).

    ⚑ The docker ``.env`` FILES the bare spelling used to write are RETIRED
    OUTRIGHT (Jei's 2026-08-02 RQ-1 re-ruling; the ratified manifest records the
    files as DROPPED): the launch-side three-tier read is gone, so a hand edit
    no longer reaches the box either. The message says so — a cure that implied
    "edit the file instead" would send the user to a surface nothing reads.

    *verb* is the op word for the message (``"set"`` / ``"reset"`` / ``"read"``).
    Like :func:`bare_agent_key_scope_error` this gates itself — ``None`` for
    every other key — so every verb door applies it uniformly and unconditionally.
    """
    if not _is_bare_env_key(key):
        return None
    var = key[len("env."):]
    # ⚑ The AGENT scope is DISCRIMINATED (spec §0) — ``agent.env.<VAR>`` is NOT a
    # key. ``ENV_KEY_RE``/``BIND_KEY_RE`` (settings_categories) spell the agent
    # scope ``agent.<node>`` and REFUSE the bare ``agent.`` form outright, so
    # ``command_scope.value`` would hand the user a SECOND illegal spelling to
    # replace the first — a cure that cannot be followed. The agent arm names the
    # discriminated form with an explicit ``<agent>`` placeholder instead.
    #
    # (The ``agent`` NOUN's own verbs never reach here: ``agent set <node>
    # env.FOO=bar`` passes a key TAIL under an already-named node, which
    # ``agent_file_route`` writes to the DECLARED ``agent.<node>.env.FOO``. This
    # arm exists so the GENERIC engine cannot emit the illegal spelling if an
    # agent-scope caller is ever wired into it.)
    if command_scope is ConfigLevel.agent:
        cure = f"agent.<agent>.env.{var}"
        store = "agent"
    else:
        store = command_scope.value if command_scope is not None else "box"
        cure = f"{store}.env.{var}"
    return (
        f"Error: '{key}' cannot be {verb} — the bare env.<VAR> spelling is "
        f"RETIRED (the env family is scoped, spec §2a). Use '{cure}', "
        f"which is stored in the {store} settings file and exported into the box "
        f"at launch. The docker .env files the bare spelling wrote are no longer "
        f"read at all."
    )


def _is_agent_setting(key: str) -> bool:
    """Keys that belong in the agent section of settings.yaml."""
    return key in {
        "model", "continue_mode", "access", "endpoint", "allow_helpers",
        "bootstrap",
    }

def _is_box_agent_key(key: str) -> bool:
    """The RETIRED box-scoped agent mirror ``box.agent.<key>`` (spec §2b).

    ⮕ **P7 RETIRED THE SETTABLE MIRROR.** ``box.agent.<key>`` was the box's
    box-scoped override of its active agent's settings subtree. Spec §2b
    replaces it with the RO read-back ``meta.box.agent.<key>`` (readable, never
    settable — ``meta.*`` is RO by contract), and a box tweaks its agent with the
    §2h request ``pref.agent.<agent>.<key>``, which targets the AGENT tier properly
    instead of smuggling a box-scope key into it.

    This predicate now exists ONLY to RECOGNISE the retired spelling so set / reset
    / get can refuse it with the cure (:func:`box_agent_retired_error`) rather than
    fail as an unknown key — a user who has the old form in muscle memory must be
    TOLD what replaced it.
    """
    return key.startswith("box.agent.")


def box_agent_retired_error(
    canonical: str, *, verb: str, active_agent: str | None = None,
) -> str:
    """The refusal + cure for a RETIRED ``box.agent.<key>`` op (P7, spec §2b).

    ⚑ The pointer names what ``--effective`` ACTUALLY RENDERS — the ``pref`` block,
    which prints each REQUEST beside the value it produced. It deliberately does NOT
    promise ``meta.box.agent.<key>``: that key is real in the snapshot (the RO
    read-back) but no renderer emits it today, and a cure that points at output the
    user will not find is worse than no pointer.
    """
    tail = canonical[len("box.agent."):]
    agent = active_agent or "<agent>"
    return (
        f"Error: '{canonical}' is RETIRED — a box no longer carries a settable "
        f"mirror of its agent's settings (spec §2b). Tweak the agent for THIS box "
        f"with the request '{verb} pref.agent.{agent}.{tail}' (spec §2h); "
        f"'kanibako box config --effective' then shows that request beside the "
        f"value it produced."
    )

# The command scopes that CANNOT write a BARE agent behavior key: a bare key
# (``_is_agent_setting``) targets the any-agent ``agent.default`` tier, which both
# box (agent ⊃ box) and workset (agent ⊃ workset) CONTAIN — so a bare write from
# either is UPWARD and is DROPPED at launch by
# ``settings_assemble._drop_upward_scopes`` (a silent no-op the CLI reported as
# "Set"). The two differ in the CURE: a BOX has a single active agent, so it is
# redirected to the spec §2h request ``pref.agent.<active>.<key>``
# (``box_agent_redirect_key``); a WORKSET spans many boxes/agents, so there is no
# single agent to redirect TO — it simply refuses (configure at system scope for
# all agents, or per-box via the §2h request).
_NO_BARE_AGENT_KEY_SCOPES: "frozenset[ConfigLevel]" = frozenset(
    {ConfigLevel.box, ConfigLevel.workset}
)


def box_agent_redirect_key(
    canonical: str,
    command_scope: "ConfigLevel | None",
    active_agent: str | None = None,
) -> str | None:
    """The canonical ``pref.agent.<active>.<key>`` request a BARE agent behavior key
    redirects to at BOX command scope, or ``None`` when this case does not apply.

    ⮕ **P7 RETARGETED THIS.** It used to name the settable ``box.agent.<key>``
    mirror, which spec §2b retired; the box-scoped way to set an agent value is now
    the §2h request. *active_agent* is the box's resolved agent NODE — required,
    because the request targets a DISCRIMINATED agent slot (there is no bare
    ``agent.<key>``, §0). With no resolvable agent there is nothing to redirect
    to, so this returns ``None`` and the caller falls through to the ordinary
    bare-key refusal, which names the shape.

    A BARE agent behavior key — the WHOLE :func:`_is_agent_setting` family
    (``model`` / ``access`` / ``bootstrap`` / ``endpoint`` /
    ``allow_helpers`` / ``continue_mode``), uniformly, NOT a per-key list — targets
    the any-agent ``agent.default`` tier. From a BOX that is an UPWARD write (agent
    ⊃ box in the containment order): the §0 directional rule REFUSES it (spec §2h
    replaced the old "box tweaks its agent through its own mirror" device).
    The old code wrote ``agent.default.<key>`` into the BOX settings file, which
    ``settings_assemble._drop_upward_scopes`` then DROPPED at launch (a box file may
    not set a containing ``agent`` table) — a silent no-op the CLI still reported as
    "Set". So the bare form at box scope is REDIRECTED to the box's request
    ``pref.agent.<active>.<key>``: ``set``/``reset`` REFUSE (the value lives at, and
    is set/reset at, the request), ``get`` reads/names the request.

    Fires ONLY for the bare form at BOX command scope (the mirror is box-specific).
    A WORKSET bare agent key is caught by :func:`bare_agent_key_scope_error`
    (refuse, no mirror). The already-qualified ``box.agent.<key>`` is
    ``_is_box_agent_key`` (NOT ``_is_agent_setting``) — a legal SAME-scope box
    write; a per-agent ``agent.<name>.<key>`` is ``_is_persona_agent_key``; a bare
    key at SYSTEM scope is a DOWNWARD write (agent is a scope the system CONTAINS).
    None of those match, so all stay unaffected.
    """
    if (
        command_scope is ConfigLevel.box
        and _is_agent_setting(canonical)
        and active_agent
    ):
        return f"pref.agent.{active_agent}.{canonical}"
    return None


def bare_agent_key_scope_error(
    canonical: str,
    command_scope: "ConfigLevel | None",
    *,
    verb: str,
    active_agent: str | None = None,
) -> str | None:
    """Error string refusing a WRITE-shaped op on a BARE agent behavior key at a
    scope that cannot write it (box / workset), or ``None`` when it is permitted.

    A BARE agent behavior key (:func:`_is_agent_setting`, the whole family —
    uniform, NOT a per-key list) targets the any-agent ``agent.default`` tier, which
    both BOX (agent ⊃ box) and WORKSET (agent ⊃ workset) CONTAIN. A bare write from
    either is UPWARD — ``settings_assemble._drop_upward_scopes`` DROPS it at launch,
    a silent no-op the old CLI reported as "Set". So it is refused HERE, uniformly
    for ``set`` / ``reset`` (writes) at both scopes, and for the workset ``get``
    (the box ``get`` instead REDIRECTS via :func:`box_agent_redirect_key`).

    *verb* is the op word for the message (``"set"`` / ``"reset"`` / ``"read"``).
    *active_agent* names the box's resolved agent in the cure so the suggested
    command is COPY-PASTEABLE (``pref.agent.claude.model``) rather than a shape to
    fill in; the placeholder is used only where no agent resolves.

    * **BOX** — a box has a single active agent, so the refusal TEACHES the §2h
      request ``pref.agent.<agent>.<key>`` (the box-scoped tweak surface since P7
      retired the ``box.agent.*`` mirror).
    * **WORKSET** — a workset spans multiple boxes/agents, so there is deliberately
      no single "the agent". The refusal points at system scope (all agents) or the
      per-box request.

    Returns ``None`` for every other scope — a bare key at SYSTEM scope is a legit
    DOWNWARD write; ``agent`` / ``system`` (no command scope) is unconstrained here.
    """
    if not _is_agent_setting(canonical) or command_scope not in _NO_BARE_AGENT_KEY_SCOPES:
        return None
    agent = active_agent or "<agent>"
    if command_scope is ConfigLevel.box:
        return (
            f"Error: box-scope agent settings can't be {verb} bare (a bare agent "
            f"key targets agent.default, which a box cannot write). "
            f"Use '{verb} pref.agent.{agent}.<key>' — did you mean "
            f"'{verb} pref.agent.{agent}.{canonical}'? (spec §2h)"
        )
    # workset — no mirror; point at system (all agents) or the per-box mirror.
    # WORKSET keeps the PLACEHOLDER on purpose: a workset spans many boxes and many
    # agents, so naming one box's resolved agent here would be a lie.
    return (
        f"Error: agent settings can't be {verb} at workset scope (a workset spans "
        f"multiple boxes/agents, so there's no single agent to configure). "
        f"Configure them at system scope to apply to all agents, or per-box via "
        f"'pref.agent.<agent>.{canonical}' (spec §2h)."
    )

def _agent_scope_node(key: str) -> str:
    """The agent NODE a discriminated agent-scope *key* names, else ``""``.

    ``agent.claude.common.plugins`` -> ``claude``; ``box.bindings.rw.home`` -> ``""``.
    Reads the scope token the category regex already parses, so the discriminator is
    split the one canonical way (a persona node carries no dot, so the first two
    segments are the whole scope).
    """
    from kanibako.settings.settings_categories import BIND_KEY_RE

    m = BIND_KEY_RE.match(key)
    if m is None:
        return ""
    scope = m.group("scope")
    return scope.split(".", 1)[1] if scope.startswith("agent.") else ""


def is_known_key(arg: str) -> bool:
    """Return True if *arg* looks like a config key (not a project name)."""
    if arg in KNOWN_CONFIG_KEYS:
        return True
    # Bare env.<VAR> — RETIRED (R-39) but still KEY-SHAPED on purpose: the verbs
    # must refuse it with the cure, which requires the positional-vs-key
    # disambiguator to read it as a key, never as a project name.
    if any(arg.startswith(p) for p in DYNAMIC_PREFIXES):
        return True
    # pref.<target-key> — the §2h REQUEST family. SHAPE-only here: this is the
    # positional-vs-key disambiguator, and no project is named ``pref.…``. The
    # three filters run in the set / get / reset branches.
    if _is_pref_key(arg):
        return True
    # agent.<node>.bindings.{ro,rw}.<name> — the per-node DESCRIPTOR bind key
    # (item-0): a settable key (recognised on the +form too, before canonicalization)
    # so get/show + the project-name heuristic treat it as a KEY. Checked BEFORE the
    # persona form so a bind named after a state leaf is recognised as the bind.
    if _is_agent_node_bind_key(arg):
        return True
    # agent.<node>.secret_path.<VAR> — the per-node SECRET category (spec §2a): a
    # settable key (recognised on the +form too, before canonicalization). Checked
    # here so get/show + the project-name heuristic treat it as a KEY. Also the
    # NON-agent ``<scope>.secret_path.<VAR>`` scope form.
    if _is_agent_node_secret_key(arg) or _is_scope_secret_key(arg):
        return True
    # <scope>.env.<VAR> (system/workset/box) — the LIVE env family (spec §2a
    # L383). Its agent twin ``agent.<node>.env.<VAR>`` is recognised by the
    # per-persona arm below.
    if _is_scope_env_key(arg):
        return True
    # agent.<node>.<key> — the per-persona agent key (block B1): a settable key
    # (recognised on the +form too, before canonicalization) so get/show + the
    # project-name heuristic treat it as a KEY, never a project name.
    if _is_persona_agent_key(arg):
        return True
    # box.agent.<key> — the box-scoped agent mirror (block B5, spec §2b): a
    # settable box-scope key (so the get/show paths + the project-name heuristic
    # treat it as a KEY, never a project name).
    if _is_box_agent_key(arg):
        return True
    # {system,workset,box}.bindings.{ro,rw}.<name> — the RETIRED scope-level bind
    # route (R-9). Kept KEY-SHAPED for the same reason as the bare ``env.<VAR>``
    # spelling: the positional-vs-key disambiguator must read it as a key so the
    # verbs can refuse it with the cure (``scope_bind_retired_error``) rather than
    # mistake it for a project name. It is also still READABLE (``config get``),
    # which on its own makes it a key here.
    if _is_scope_bind_key(arg):
        return True
    # Category keys (``<scope>.caches`` / ``seeded`` / ``common`` / ``synced``,
    # plus every ``agent.<node>.<category>.<name>``) are settable via
    # ``config set`` (the source-only RAW repoint). Recognize them here too so the
    # get/show paths + the project-name heuristic treat a category key as a KEY,
    # never a project name — the same get-validated/set-unguarded symmetry the H1
    # fix established.
    return _is_path_category_key(arg)

def is_system_path_key(key: str) -> bool:
    """Keys that belong in the bootstrap config file's PATH tables (file-only).

    Covers BOTH the Layer-1 ``[config]`` foundation keys (``config.*``, spec §1)
    and the STRUCTURAL Layer-2 ``system.*`` path-tier family — the exact
    :data:`~kanibako.settings.paths.SYSTEM_PATH_DEFAULTS` set that
    ``resolve_system_paths`` materializes from ``kanibako_config.yaml``'s
    ``[system]`` table — both live in ``kanibako_config.yaml`` and are
    structural (file-only).

    The F2/F3 fix: this is a PRECISE family membership check, NOT a
    ``system.*``-wide catch-all.  A ``system.*`` SETTINGS key (the auth chain
    ``system.auth.share_allowed``, ``system.agent``, categories, env)
    is NOT this family — ``resolve_system_paths`` drops unknown ``[system]``
    entries, so routing such a key to the config file was a write-only no-op;
    the launch reads them from the system SETTINGS file (``@config.settings``).
    Those keys now fall through to their settings-tier routing.

    ``system.setup_completed`` IS kept in this family: its shipped reader
    (``config.read_setup_completed``) reads the ``[system]`` table of
    ``kanibako_config.yaml`` (where ``setup`` writes it), so the config-file
    routing/advice is TRUE for it.  (Spec §2g lists it as a settings key —
    flagged as a spec-vs-code divergence; relocating the reader is out of
    scope here.)
    """
    if key.startswith("config."):
        # Still consulted on the READ/show path. The set/reset paths now
        # short-circuit config.* earlier with the ruled refusal (block B2), so this
        # branch no longer reaches system_key_refusal for a config.* set/reset.
        return True
    if not key.startswith("system."):
        return False
    if key == "system.setup_completed":
        return True
    # Lazy import (config_interface ↔ paths would cycle at module load).
    from kanibako.settings.paths import SYSTEM_PATH_DEFAULTS

    return key in SYSTEM_PATH_DEFAULTS


def _user_config_file_str() -> "Path | str":
    """The RESOLVED user bootstrap config file, for refusal messages.

    Rendered (JC-B2-1) so a non-default ``$XDG_CONFIG_HOME`` shows the user's
    real file.  This is an ERROR path — it must never itself raise: if
    XDG/``$HOME`` resolution fails (``xdg`` falls back to ``Path.home()``, which
    raises when ``$HOME`` is unset), fall back to the documented literal default
    rather than turning a clean refusal into a traceback.
    """
    from kanibako.settings.config import config_file_path
    from kanibako.settings.paths import xdg

    try:
        return config_file_path(xdg("XDG_CONFIG_HOME", ".config"))
    except Exception:
        return "~/.config/kanibako_config.yaml"


def system_key_refusal(key: str) -> str:
    """Error string refusing a CLI write to a FILE-ONLY ``system.*`` config key.

    STRUCTURAL ``system.*`` path-tier keys (the ``SYSTEM_PATH_DEFAULTS`` family,
    see :func:`is_system_path_key`) are layout config, not behavior settings,
    so they are file-only: editable in the config file (or via ``kanibako
    setup``) but never via ``config set``/``config reset``.  Points the user at the
    REAL resolved config file — the ``kanibako_config.yaml`` ``[system]`` table
    that ``resolve_system_paths`` actually reads — never the command scope's
    settings file (which would be wrong-file advice: the F2 lesson)."""
    return (
        f"Error: '{key}' is a structural config key and is not settable from "
        f"the CLI. Edit the config file directly:\n  {_user_config_file_str()}\n"
        f"(or re-run 'kanibako setup')."
    )


def _config_key_refusal(canonical: str, *, action: str) -> str:
    """Error string refusing a CLI set/reset of a ``config.*`` foundation key.

    RATIONALE (Jei, load-bearing): ``config.*`` keys LOCATE the files everything
    else is stored in (``config.settings`` IS where the settings file lives;
    ``config.registry`` IS the registry).  A key cannot live IN the file it
    locates → they live in the bootstrap config file, resolved BEFORE anything
    loads.  So the CLI is a *settings* manager: it READS ``config.*`` (to find
    where to write settings) but NEVER WRITES them — there is no coherent file to
    write them to.  The bootstrap config file is a HUMAN/ADMIN hand-edited
    surface.  The message deliberately does NOT mention ``setup`` (naming it would
    wrongly imply it is how you set a ``config.*`` value).

    *action* is ``"set"`` or ``"reset"`` — selects the verb (a ``set`` can only be
    done by editing the file; a ``reset`` is a change, so it says "changed") while
    pointing at the SAME resolved config file.

    The path is RENDERED via :func:`_user_config_file_str` (JC-B2-1: the user's
    real resolved file, with a raise-proof fallback — see that helper).
    """
    config_file = _user_config_file_str()
    verb = "changed" if action == "reset" else "set"
    return (
        f"Error: config.* keys can only be {verb} by editing the configuration "
        f"file ({config_file})."
    )

# ``<scope>.secret_path.<VAR>`` for the NON-agent scopes (system/workset/box). The
# AGENT scope form ``agent.<node>.secret_path.<VAR>`` is DISCRIMINATED and routed by
# ``_is_agent_node_secret_key`` (the node file); this covers the other three, which
# write a scalar to the COMMAND scope's OWN settings file at ``<scope>.secret_path.<VAR>``
# (the shape ``_file_partial`` reads into the cascade).
_SCOPE_SECRET_RE = re.compile(
    r"^(?P<scope>system|workset|box)\.secret_path\.(?P<var>[A-Za-z_][A-Za-z0-9_]*)$"
)


def _is_scope_secret_key(key: str) -> bool:
    """True iff *key* is a NON-agent ``<scope>.secret_path.<VAR>`` SECRET-category
    key (system/workset/box) — settable to the command scope's own settings file."""
    return _SCOPE_SECRET_RE.match(key) is not None

def _is_pref_key(key: str) -> bool:
    """True iff *key* is a ``pref.<target-key>`` REQUEST key (spec §2h).

    SHAPE ONLY — deliberately not a validity test. Its job on the
    :func:`is_known_key` path is the positional-vs-key DISAMBIGUATOR (is this
    argument a config key or a project name?), and for that the prefix is both
    sufficient and correct: no project is named ``pref.something``. The real
    validation runs in the set / get / reset branches, where a bad request can
    be reported with a reason instead of silently reinterpreted as a project.
    """
    return key.startswith(f"{PREF_ROOT}.")


def _pref_level(command_scope: "ConfigLevel | None") -> str | None:
    """The pref LEVEL name for a command scope, or ``None`` where a pref is
    illegal (spec §2h — workset and box ONLY)."""
    if command_scope is ConfigLevel.box:
        return "box"
    if command_scope is ConfigLevel.workset:
        return "workset"
    return None


def _pref_write_site_error(
    canonical: str, command_scope: "ConfigLevel | None", *, verb: str = "set",
) -> str | None:
    """Refuse a ``pref.*`` WRITE outside the workset / box scopes (spec §2h).

    ⚑ *"Where a pref may be written. Workset (L3.2) and box (L4.2) levels ONLY —
    never base, system or agent. **This is what BOUNDS the recursion**, so it is
    a hard rule, not a convenience.* ``config set pref.<key> <value>`` at
    base/system/agent scope must RAISE, not silently write a dead entry."*

    Checked BEFORE the three TARGET filters: a user at the system scope must be
    told the FILE is wrong regardless of the target's quality — fixing the target
    first would only surface this error afterwards.

    Returns an ``Error: …`` string when refused, else ``None``.
    """
    if not _is_pref_key(canonical):
        return None
    if _pref_level(command_scope) is not None:
        return None
    if command_scope is None:
        return None  # no command-scope context — the guard is skipped, as elsewhere.
    target = canonical[len(PREF_ROOT) + 1:]
    scope = target.split(".", 1)[0]
    hint = (
        f" Set '{target}' directly at the {scope} scope instead."
        if scope in ("system", "agent", "workset", "box") else ""
    )
    return (
        f"Error: '{canonical}' cannot be {verb} from the {command_scope.value} "
        f"scope. A pref is a REQUEST written in a workset or box settings file "
        f"only (spec §2h) — that restriction is what bounds the resolution "
        f"recursion.{hint}"
    )


def _pref_target_error(
    canonical: str, command_scope: "ConfigLevel | None",
) -> str | None:
    """Run the three §2h filters on the ``pref.*`` TARGET KEY at SET time.

    Same predicate the launch path applies TO THE KEY. ⚑ The KEY is only half of
    a request: the VALUE is checked separately by :func:`_pref_value_error`,
    against the TARGET's shape and resolution. An earlier version of this
    docstring claimed set-time and launch-time validation were equivalent —
    they were not, and the gap was real: a scalar at a bind-shaped target, and an
    unresolvable ``@``-ref, were both accepted here and failed at LAUNCH.
    """
    from kanibako.settings.settings_prefs import (
        PrefRequest,
        default_valid_agents,
        validate_pref,
    )

    level = _pref_level(command_scope)
    if level is None:
        return None  # the write-site guard already refused (or there is no scope).
    target = canonical[len(PREF_ROOT) + 1:]
    why = validate_pref(
        PrefRequest(target=target, value=None, level=level),
        valid_agents=default_valid_agents(),
    )
    if why is None:
        return None
    return f"Error: '{canonical}' was refused: {why}."


def _pref_sections_leaf(canonical: str) -> "tuple[tuple[str, ...], str]":
    """The nested write location for a pref: ``(("pref", *head), leaf)``."""
    parts = canonical.split(".")
    return tuple(parts[:-1]), parts[-1]

def _scope_bind_match(key: str) -> "re.Match[str] | None":
    """The :data:`SCOPE_BIND_KEY_RE` match for *key* — the ONE parse site, so the
    predicate below and the message it feeds can never disagree about what they
    matched."""
    from kanibako.settings.settings_categories import SCOPE_BIND_KEY_RE

    return SCOPE_BIND_KEY_RE.match(key)


def _is_scope_bind_key(key: str) -> bool:
    """The RETIRED SCOPE-level bind route ``{system,workset,box}.bindings.{ro,rw}.<name>``.

    ⮕ **R-9 RETIRED THE SCOPE-LEVEL CLI ROUTE** (disk-store rework step 1). The
    two ``bindings`` arms are becoming a TERMINAL key whose VALUE is a dest-keyed
    map, and *the inner map's keys are not part of the keyspace* — so there is no
    dotted key left for ``config set`` / ``config reset`` to name. The keys
    themselves are NOT retired: they are still declared, still authored in the
    settings YAML, still delivered at launch. Only the CLI route is gone, and it
    is a KNOWN, ACCEPTED loss (backlog DS-BL1).

    Like :func:`_is_bare_env_key` and :func:`_is_box_agent_key`, this predicate
    exists to RECOGNISE the retired spelling so the write verbs refuse it BY NAME
    with a cure (:func:`scope_bind_retired_error`) instead of degrading to
    "unknown config key" — spec §0 refuses loudly, never quietly.

    ⚑ It does NOT cover ``agent.<node>.bindings.{ro,rw}.<name>``
    (:func:`_is_agent_node_bind_key`), which is a separate, still-live route.
    """
    return _scope_bind_match(key) is not None


def scope_bind_retired_error(canonical: str, *, verb: str) -> str | None:
    """The refusal + cure for a RETIRED scope-level bind WRITE (R-9), or ``None``
    when *canonical* is not one.

    ⚑ The cure is HONEST about the loss. There is no equivalent CLI spelling to
    redirect to — that is precisely what R-9 accepted — so the message names the
    settings FILE as the surface, which is real and reachable (the launch cascade
    reads that tuple today, exactly as written). Prescribing a command that does
    not exist would be worse than naming the loss.

    *verb* is the op word for the message (``"set"`` / ``"reset"``). Gates itself
    — ``None`` for every other key — so every verb door applies it uniformly.
    """
    m = _scope_bind_match(canonical)
    if m is None:
        return None
    scope, category = m.group("scope"), m.group("category")
    return (
        f"Error: '{canonical}' cannot be {verb} from the CLI — the scope-level "
        f"'{scope}.{category}.<name>' route is RETIRED (the two bindings arms are "
        f"a single terminal key keyed by DESTINATION, so a per-name key no longer "
        f"exists). Edit the '{scope}:' table of the {scope} settings file "
        f"directly; the launch reads it from there. Reading it back with "
        f"'config get {canonical}' still works."
    )


def _is_path_category_key(key: str) -> bool:
    """True iff *key* is a PATH-TUPLE category key settable via ``config set``.

    The source-only RAW repoint (spec §2a / design §6d / S24) applies to the
    bind-shaped categories — a 2-/3-element ``[host_src, box_dest[, options]]``
    tuple — and, since R-9, only to those still reachable by a dotted key:

    * at ``system`` / ``workset`` / ``box``: ``caches`` / ``seeded`` / ``common``
      / ``synced``. **The two ``bindings.{ro,rw}`` arms are NOT matched here any
      more** — their scope-level CLI route is retired, recognised instead by
      :func:`_is_scope_bind_key` and refused by name.
    * at the discriminated ``agent.<node>`` scope: all six, ``bindings.{ro,rw}``
      included — that route is untouched by R-9.

    ``env`` (scalar) is NOT matched here — the live ``<scope>.env.<VAR>`` arm is
    routed by the earlier :func:`_is_scope_env_key` branch as a plain scalar
    write, and the bare spelling is refused before dispatch (R-39); ``masks``
    (a keyed list) is YAML-only (spec §2a) and is NOT matched here.
    """
    from kanibako.settings.settings_categories import BIND_KEY_RE

    return BIND_KEY_RE.match(key) is not None

def _has_dedicated_route(canonical: str) -> bool:
    """Does SOME ``set_config_value`` branch claim *canonical*?

    ⚑ MIRRORS THE DISPATCH CHAIN in :func:`set_config_value`, in the same order.
    A branch added there without a term here would make this say "no route" for a
    key that in fact has one — so the two must be edited together, and
    ``TestSetDispatchCoverage`` fails if they drift.  PREAMBLE guards are NOT
    terms: a key refused before the dispatch (the bare-agent scalars at box/
    workset; the retired bare ``env.<VAR>``, R-39) never reaches the probe this
    feeds, so a term for it here would be a second spelling of the refusal.

    Its ONE job is to keep :func:`_probes_at_set_time` off keys nothing handles,
    so an unknown key still reaches the routing table at the bottom of the
    dispatch and is reported as ``unknown config key: <key>`` — the error §0
    requires, which NAMES the key, rather than a resolution complaint about a
    value on a key that does not exist.
    """
    return (
        _is_pref_key(canonical)
        or _is_agent_node_bind_key(canonical)
        or _is_agent_node_secret_key(canonical)
        or _is_scope_secret_key(canonical)
        or _is_scope_env_key(canonical)
        or _is_persona_agent_key(canonical)
        or _is_agent_setting(canonical)
        or _is_box_agent_key(canonical)
        or _is_path_category_key(canonical)
        or is_system_path_key(canonical)
        or _route_key(canonical) in _KEY_ROUTES
    )


def _probes_at_set_time(canonical: str) -> bool:
    """Does a ``config set`` of *canonical* run the E3 RESOLUTION probe?

    The test is **"does this value reach the expander"**, NOT "is it a scalar".
    A value the expander never sees carries no ``@``/``$`` SYNTAX — those
    characters are DATA in it — so probing would refuse legitimate input with no
    correct spelling available.

    Excluded, and why:

    * **The two CATEGORY paths.** They run their own probe with
      ``is_category=True``, which additionally enforces the source-only and
      self-resolving rules; probing twice would only duplicate the diagnosis.
    * **Keys nothing claims.** They must reach the routing table and be reported
      as an unknown KEY (see :func:`_has_dedicated_route`).

    The RETIRED scope-level bind route ``{system,workset,box}.bindings.{ro,rw}
    .<name>`` (R-9) is in the SAME position as the bare env spelling: refused in
    the verb preamble (:func:`scope_bind_retired_error`), so it never reaches
    this predicate and needs no exclusion term of its own.

    The docker ``.env`` family (bare ``env.<VAR>``, written VERBATIM to a file
    the expander never saw) WAS the third exclusion. R-39 retired the spelling:
    it is refused in the verb preamble (:func:`bare_env_retired_error`) and
    never reaches this predicate, so the exclusion went with the route. Every
    scalar whose value the expander DOES see stays LOUD: ``box.shell``,
    ``workset.boxes``, ``<scope>.secret_path.<VAR>`` and the rest all probe,
    because for them a dangling ref really does resolve to ``""`` silently at
    launch.

    ⚑ The KEYSPACE env arm ``<scope>.env.<VAR>`` (``box.env.FOO``) is a DIFFERENT
    key from the retired bare ``env.FOO`` and is deliberately NOT excluded — it
    IS host-expanded at launch (``settings_launch._emit_scope_node`` reads it off
    the EXPANDED snapshot), so a dangling ref in it fails silently exactly the
    way the probe exists to catch. It now HAS a dispatch branch
    (:func:`_is_scope_env_key`), so it reaches the fall-through below and probes
    — the intended direction. Do NOT "fix" a probe complaint on it by widening
    :func:`_is_bare_env_key`: that would drag the live scoped keys into the
    retired spelling's refusal.
    """
    if _is_path_category_key(canonical) or _is_agent_node_bind_key(canonical):
        return False
    if _is_pref_key(canonical):
        # ⚑ The GENERIC probe is a NO-OP at a ``pref.*`` path and must not run
        # there: ``expand`` carries the ``pref`` subtree through unexpanded (spec
        # §2h), so nothing in it is ever resolved and no defect can be
        # recorded. Worse, applying the candidate at the pref path WRITES the
        # target's leaf names into a KeyStore, so a target whose leaf is a
        # RESERVED name (``…common.get``) raised ReservedKeyError straight out of
        # this function — breaking ``set_config_value``'s "returns an error
        # string, never raises" contract. The pref route runs the REAL probe at
        # the TARGET path instead (:func:`_pref_value_error`).
        return False
    return _has_dedicated_route(canonical)
