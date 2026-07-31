"""Unified config interface engine for all management commands.

Provides a reusable config subsystem that box/workset/agent/system commands
share.  Handles get, set, show, and reset operations with a consistent
syntax:

- ``key=value``  → set
- ``key``        → get (if key is known)
- no args        → show all overrides
- ``--effective`` → show resolved values
- ``--reset key`` → remove override
- ``--reset --all`` → remove all overrides (with confirmation)
"""

from __future__ import annotations

import re
import sys
from dataclasses import fields
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from kanibako.config import (
    coerce_bool,
    load_merged_config,
    load_project_overrides,
    read_agent_settings,
    unset_project_config_key,
)
from kanibako.agent_ref import (
    canonicalize_agent_ref,
    display_agent_ref,
    parse_agent_ref,
)
from kanibako.config_io import dump_doc, load_doc
from kanibako.errors import ConfigError, UserCancelled
from kanibako.settings_prefs import PREF_ROOT
from kanibako.settings_store import SCOPE_CONTAINMENT, ReservedKeyError
from kanibako.shellenv import (
    merge_env,
    read_env_file,
    set_env_var,
    unset_env_var,
    write_env_file,
)
from kanibako.utils import confirm_prompt


# ---------------------------------------------------------------------------
# Key registry
# ---------------------------------------------------------------------------

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
    # Box
    "box.image",
    "box.agent_name",
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
    "system.base_template",
    "system.cache",
    "system.runtime",
    # system.default_agent: the lone system.*-named SETTING (behavior, not a
    # config path).  Routed to the SYSTEM settings tier (the agent.default
    # table), NOT the [system] config table — handled explicitly below.
    "system.default_agent",
})

# Prefixes for dynamic keys (env vars).
DYNAMIC_PREFIXES: tuple[str, ...] = ("env.",)


def is_known_key(arg: str) -> bool:
    """Return True if *arg* looks like a config key (not a project name)."""
    if arg in KNOWN_CONFIG_KEYS:
        return True
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
    # agent.<node>.<key> — the per-persona agent key (block B1): a settable key
    # (recognised on the +form too, before canonicalization) so get/show + the
    # project-name heuristic treat it as a KEY, never a project name.
    if _is_persona_agent_key(arg):
        return True
    # box.agent.<key> — the box-scoped agent mirror (block B5, spec §2b L380): a
    # settable box-scope key (so the get/show paths + the project-name heuristic
    # treat it as a KEY, never a project name).
    if _is_box_agent_key(arg):
        return True
    # Category keys (``<scope>.bindings.{ro,rw}.<name>`` / ``caches`` / ``seeded``
    # / ``shared`` / ``synced``) are settable via ``config set`` (the source-only
    # RAW repoint). Recognize them here too so the get/show paths + the
    # project-name heuristic treat a category key as a KEY, never a project name
    # — the same get-validated/set-unguarded symmetry the H1 fix established.
    return _is_path_category_key(arg)


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
    "box.agent_name": (("box",), "agent_name"),
    "box.shell": (("box",), "shell"),
    "box.share_images": (("box",), "share_images"),
    # Auth sharing — settable 3-tier chain (system/workset/box.auth.*). These are
    # ordinary SETTINGS keys: each routes to its nested ``<scope>.auth.<leaf>``
    # slot in the command-scope settings file (the same nested-settings pattern as
    # ``box.image`` etc.), NOT the [project] meta table.
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
# Config action parsing
# ---------------------------------------------------------------------------

class ConfigAction(Enum):
    """What the user wants to do with config."""

    get = "get"
    set = "set"
    show = "show"
    reset = "reset"


def parse_config_arg(
    arg: str | None, *, set_null: bool = False,
) -> "tuple[ConfigAction, str, str | None]":
    """Parse a single positional config argument.

    Returns ``(action, key, value)``.

    - ``"key=value"`` → ``(set, key, value)``
    - ``"key"``       → ``(get, key, "")``
    - ``None``        → ``(show, "", "")``

    *set_null* is the ``--null`` flag: ``config set --null <key>`` is a SET whose
    value is Python ``None`` — an explicit present-``None``, distinct from the
    terminal empty string ``key=`` and from ``--reset`` (which REMOVES the
    override rather than writing one).

    ⚑ Why a FLAG and not a magic value token. ``config set`` stores scalars
    VERBATIM — nothing in it YAML- or literal-parses a value (only keys declared
    ``bool`` in ``KEY_TYPES`` coerce) — so there is no existing rule under which
    the string ``"null"`` would become ``None``, and inventing one for this route
    alone would be a dialect: ``env.X=null`` and ``box.image=null`` are
    legitimate strings. The flag says what is meant, applies to EVERY key whose
    leaf accepts the §3 present-``None`` terminal, and cannot collide with data.
    It is the CLI spelling of §2h's suppression request
    (``pref.agent.<agent>.<category>.<name>: null``), which is the ONLY channel a
    box has to drop something its agent declares.
    """
    if set_null:
        return (ConfigAction.set, (arg or "").strip(), None)
    if arg is None:
        return (ConfigAction.show, "", "")
    if "=" in arg:
        key, _, value = arg.partition("=")
        return (ConfigAction.set, key.strip(), value.strip())
    return (ConfigAction.get, arg.strip(), "")


# ---------------------------------------------------------------------------
# Canonical key resolution
# ---------------------------------------------------------------------------

def _resolve_key(raw: str) -> str:
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
    bind = _parse_agent_node_bind_key(raw)
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
        "endpoint", "model", "continue_mode", "auto_approve", "allow_helpers",
        "bootstrap",
        # Per-agent template SOURCE (template-trio, spec §2a/§2d L598; Q2 2026-07-09
        # "agent = persona + harness"). A settable STRING-path leaf on the agent
        # (persona+harness) node — default @config.agents/<harness>/template — read
        # by the layer-2 seed ``agent.<a>.seeded.template = (@agent.<a>.template, ~)``,
        # so repointing it reroutes the agent template seed.
        "template",
    }
)
_PERSONA_ENV_SECTIONS: frozenset[str] = frozenset({"env"})

# The RESERVED any-agent tier name (mirrors ``settings_assemble._AGENT_DEFAULT_SUB``
# / ``config.read_agent_settings``: "no real agent may be named default"). It is
# NOT a persona node — an ``agent.default.<key>`` write is refused (the any-agent
# default is the BARE key), so nothing lands at a never-read ``agents/default/``.
_AGENT_DEFAULT_SUB = "default"


def _parse_persona_agent_key(key: str) -> "tuple[str, str] | None":
    """Split an ``agent.<node>.<tail>`` persona key into ``(node_raw, tail)``.

    Returns ``None`` when *key* is not a settable per-persona agent key. The
    settable *tail* forms are a FLAT state leaf (``endpoint`` / ``model`` /
    ``continue_mode`` / ``auto_approve`` / ``allow_helpers``) or a sectioned ``env.<VAR>``
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


def _is_auto_approve_key(canonical: str) -> bool:
    """True iff *canonical* is the auth-critical ``auto_approve`` permission key.

    Matches BOTH settable forms: the BARE any-agent ``agent.default`` tier key
    (``canonical == "auto_approve"``, routed via :func:`_is_agent_setting`) and a
    per-persona override ``agent.<node>.auto_approve`` (routed via
    :func:`_is_persona_agent_key`).  Used to WRITE-VALIDATE the value at ``config
    set`` time: ``auto_approve`` drives ``--dangerously-skip-permissions`` and is
    ``coerce_bool``'d at LAUNCH with an UNRECOGNISED value falling back to the
    PERMISSIVE default (True) — so a typo (``flase``) must be REJECTED here, never
    silently resolved permissive (the unsafe direction).  Only ``auto_approve``
    gets this guard (Jei: only the auth-critical key), not ``allow_helpers`` /
    ``model``.
    """
    if canonical == "auto_approve":
        return True
    parsed = _parse_persona_agent_key(canonical)
    return parsed is not None and parsed[1] == "auto_approve"


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
# (§2d / §0 L21); an undiscriminated ``agent.<category>`` is not a key and
# ``BIND_KEY_RE`` refuses it. ``BIND_KEY_RE`` DOES match this node form (it is a
# well-formed category key), so both predicates fire — every dispatch checks the
# node-bind FIRST. This does not match the ``box.agent.bindings.*`` box-mirror form
# (a ``box`` top-token).
_AGENT_NODE_BIND_RE = re.compile(
    r"^agent\.(?P<node>.+?)\.(?P<cat>bindings\.(?:ro|rw))\.(?P<name>.+)$"
)


def _parse_agent_node_bind_key(key: str) -> "tuple[str, str, str] | None":
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
    return _parse_agent_node_bind_key(key) is not None


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


def _persona_agent_target(
    canonical: str, agents_root: "Path | None",
) -> "tuple[Path, tuple[str, ...], str] | str | None":
    """Resolve a canonical persona key to its FILE write/read location.

    Returns one of:

    * ``(path, sections, leaf)`` — the route into ``agents/<node>/settings.yaml``
      (``path``), the nested file table (``("self",)`` for a flat state leaf,
      ``("self", "env")`` for an env pointer), and the leaf name;
    * an ``"Error: ..."`` string — a MALFORMED node ref (validated, never routed);
    * ``None`` — not a persona key, OR *agents_root* was not supplied (the per-
      persona store is global under ``config.agents`` and is only reachable when
      the caller threads its root — the system scope).

    The node is taken VERBATIM from *canonical* (already ``℘``-canonicalized by
    :func:`_resolve_key`) and used AS-IS for the dir — it is only VALIDATED here
    (via :func:`parse_agent_ref`), never re-swapped.  So breaking the
    :func:`_resolve_key` swap routes a ``+`` key to a ``agents/<node-with-+>/``
    dir the resolver never reads (the canonicalization mutation the gate proves).
    """
    parsed = _parse_persona_agent_key(canonical)
    if parsed is None or agents_root is None:
        return None
    node, tail = parsed
    # ``default`` is the RESERVED any-agent tier name (read_agent_settings: "no
    # real agent may be named default") — the launch NEVER reads an
    # ``agents/default/`` dir as a node, so writing one would breach the
    # keystore-maps-to-a-real-key rule + foot-gun a user who wants the any-agent
    # default (that is the BARE key, e.g. ``system set model=…``). Refuse it.
    if node == _AGENT_DEFAULT_SUB:
        return (
            f"Error: 'default' is the reserved any-agent tier, not a persona "
            f"node; set the any-agent default with the bare key "
            f"(e.g. '{tail}') instead."
        )
    from kanibako.agent_config import agent_file_route, agent_settings_path

    try:
        parse_agent_ref(node)  # validate only (raises on a malformed ref)
    except ConfigError as exc:
        return f"Error: {exc}"
    path = agent_settings_path(agents_root, node)
    sections, leaf = agent_file_route(tail, node)
    return path, sections, leaf


def _node_bind_target(
    canonical: str, agents_root: "Path | None",
) -> "tuple[Path, tuple[str, ...], str] | None":
    """Resolve a canonical per-node DESCRIPTOR bind key
    ``agent.<node>.bindings.{ro,rw}.<name>`` (item-0) to its FILE read/reset
    location — the get/reset symmetry twin of the set path (which routes through
    ``_set_category_value`` → ``repoint_host_src``).

    Returns ``(path, sections, leaf)`` via the file-shape SoT
    :func:`agent_config.agent_file_route`: the node's OWN settings file
    ``agents/<node>/settings.yaml`` (*path*), and the nested table the bind write
    targets — ``self.<node>.bindings.<ro|rw>.<name>`` split into ``(sections, leaf)``
    (the SAME route the set path passes to ``repoint_host_src`` as ``dest_parts``),
    so get/reset read/remove precisely where set wrote (the shape ``_agent_partial``
    reads back at launch). The node appears BOTH in the dir path AND in the nested
    key — that is the launch read shape, not a bug.

    Returns ``None`` when *canonical* is not a node bind, *agents_root* was not
    threaded (the per-node store is global under ``config.agents`` — only reachable
    at the SYSTEM scope, mirroring ``_persona_agent_target``), the node is the
    reserved any-agent tier, or the node ref is MALFORMED (validate-only via
    :func:`parse_agent_ref`, never re-swapped).
    """
    parsed = _parse_agent_node_bind_key(canonical)
    if parsed is None or agents_root is None:
        return None
    node, _cat, _name = parsed
    if node == _AGENT_DEFAULT_SUB:
        return None
    from kanibako.agent_config import agent_file_route, agent_settings_path

    try:
        parse_agent_ref(node)  # validate only (raises on a malformed ref)
    except ConfigError:
        return None
    path = agent_settings_path(agents_root, node)
    # ``_cat`` is the FULL ``bindings.ro`` / ``bindings.rw`` segment (not the bare
    # ``ro``/``rw``), so the tail is ``{cat}.{name}`` — no extra ``bindings.`` prefix.
    sections, leaf = agent_file_route(f"{_cat}.{_name}", node)
    return path, sections, leaf


def _node_secret_target(
    canonical: str, agents_root: "Path | None",
) -> "tuple[Path, tuple[str, ...], str] | None":
    """Resolve a canonical ``agent.<node>.secret_path.<VAR>`` key (SECRET category)
    to its FILE write/read/reset location — the get/set/reset symmetry twin.

    Returns ``(path, sections, leaf)`` via the file-shape SoT
    :func:`agent_config.agent_file_route`: the node's OWN settings file
    ``agents/<node>/settings.yaml`` (*path*) and the DISCRIMINATED nested table
    ``self.<node>.secret_path`` (*sections*) with *leaf* = the VAR — EXACTLY the shape
    ``_agent_partial`` reads into the launch cascade and ``load_agent_config`` reads
    back into ``AgentConfig.secret_path``. The node appears BOTH in the dir path AND
    the nested key — that is the launch read shape, not a bug (same as
    ``_node_bind_target``).

    Returns ``None`` when *canonical* is not a node secret key, *agents_root* was not
    threaded (the per-node store is global under ``config.agents`` — only reachable at
    the SYSTEM scope, mirroring ``_node_bind_target``), the node is the reserved
    any-agent tier, or the node ref is MALFORMED (validate-only; never re-swapped).
    """
    parsed = _parse_agent_node_secret_key(canonical)
    if parsed is None or agents_root is None:
        return None
    node, _var = parsed
    if node == _AGENT_DEFAULT_SUB:
        return None
    from kanibako.agent_config import agent_file_route, agent_settings_path

    try:
        parse_agent_ref(node)  # validate only (raises on a malformed ref)
    except ConfigError:
        return None
    path = agent_settings_path(agents_root, node)
    sections, leaf = agent_file_route(f"secret_path.{_var}", node)
    return path, sections, leaf


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
    from kanibako.core_defaults import FLOOR_PLACEHOLDER_SRC

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
    rendered = _render_stored_scalar(parts)
    if rendered is None:
        return None
    return (rendered, "descriptor floor; host re-resolved at launch")


def _is_env_key(key: str) -> bool:
    return key.startswith("env.")


def _is_agent_setting(key: str) -> bool:
    """Keys that belong in the agent section of settings.yaml."""
    return key in {
        "model", "continue_mode", "auto_approve", "endpoint", "allow_helpers",
        "bootstrap",
    }


def _is_box_agent_key(key: str) -> bool:
    """The box-scoped agent mirror ``box.agent.<key>`` (block B5, spec §2b L380).

    The box's box-scoped mirror of its active agent's WHOLE settings subtree —
    ``box.agent.<key>`` DEFAULTS (views up) to the resolved ``agent.<box.agent_name>.
    <key>`` and the box overriding any ``box.agent.<key>`` is an ORDINARY same-scope
    (box) write (§0: the no-special-case downward tweak; §2b). It is the BOX
    namespace (top-level token ``box``), so the B4 directional guard ALLOWS
    ``box set box.agent.<key>`` as a same-scope write (the guard keys on the
    ``box`` token). It is settable here so the override lands in the box settings
    file — exactly the box-scope override the materializer (settings_launch) then
    keeps (it gap-fills only the names the box did NOT set).

    Matched strictly as ``box.agent.<something>`` so it does NOT collide with the
    flat box scalar ``box.agent_name`` (which has no dotted tail).
    """
    return key.startswith("box.agent.")


# The command scopes that CANNOT write a BARE agent behavior key: a bare key
# (``_is_agent_setting``) targets the any-agent ``agent.default`` tier, which both
# box (agent ⊃ box) and workset (agent ⊃ workset) CONTAIN — so a bare write from
# either is UPWARD and is DROPPED at launch by
# ``settings_assemble._drop_upward_scopes`` (a silent no-op the CLI reported as
# "Set"). The two differ in the CURE: a BOX has a single active agent, so it gets
# the ``box.agent.<key>`` mirror (redirect/teach); a WORKSET spans many boxes/
# agents, so there is deliberately NO ``workset.agent.*`` mirror — it simply
# refuses (configure at system scope for all agents, or per-box via the mirror).
_NO_BARE_AGENT_KEY_SCOPES: "frozenset[ConfigLevel]" = frozenset(
    {ConfigLevel.box, ConfigLevel.workset}
)


def box_agent_redirect_key(
    canonical: str, command_scope: "ConfigLevel | None",
) -> str | None:
    """The canonical ``box.agent.<key>`` mirror a BARE agent behavior key redirects
    to at BOX command scope, or ``None`` when this case does not apply.

    A BARE agent behavior key — the WHOLE :func:`_is_agent_setting` family
    (``model`` / ``auto_approve`` / ``bootstrap`` / ``endpoint`` /
    ``allow_helpers`` / ``continue_mode``), uniformly, NOT a per-key list — targets
    the any-agent ``agent.default`` tier. From a BOX that is an UPWARD write (agent
    ⊃ box in the containment order): spec L440 ("a box tweaks its agent through its
    own box-scoped ``box.agent.*`` mirror") + the §0 directional rule REFUSE it.
    The old code wrote ``agent.default.<key>`` into the BOX settings file, which
    ``settings_assemble._drop_upward_scopes`` then DROPPED at launch (a box file may
    not set a containing ``agent`` table) — a silent no-op the CLI still reported as
    "Set". So the bare form at box scope is REDIRECTED to the box's active-agent
    mirror ``box.agent.<key>``: ``set``/``reset`` REFUSE (the value lives at, and is
    set/reset at, the mirror), ``get`` reads/names the mirror.

    Fires ONLY for the bare form at BOX command scope (the mirror is box-specific).
    A WORKSET bare agent key is caught by :func:`bare_agent_key_scope_error`
    (refuse, no mirror). The already-qualified ``box.agent.<key>`` is
    ``_is_box_agent_key`` (NOT ``_is_agent_setting``) — a legal SAME-scope box
    write; a per-agent ``agent.<name>.<key>`` is ``_is_persona_agent_key``; a bare
    key at SYSTEM scope is a DOWNWARD write (agent is a scope the system CONTAINS).
    None of those match, so all stay unaffected.
    """
    if command_scope is ConfigLevel.box and _is_agent_setting(canonical):
        return f"box.agent.{canonical}"
    return None


def bare_agent_key_scope_error(
    canonical: str, command_scope: "ConfigLevel | None", *, verb: str,
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

    * **BOX** — a box has a single active agent, so the refusal TEACHES the
      ``box.agent.<key>`` mirror (the box-scoped tweak surface; spec §2b L380).
    * **WORKSET** — a workset spans multiple boxes/agents, so there is deliberately
      NO ``workset.agent.*`` mirror (no single "the agent"). The refusal points at
      system scope (all agents) or the per-box ``box.agent.<key>`` mirror.

    Returns ``None`` for every other scope — a bare key at SYSTEM scope is a legit
    DOWNWARD write; ``agent`` / ``system`` (no command scope) is unconstrained here.
    """
    if not _is_agent_setting(canonical) or command_scope not in _NO_BARE_AGENT_KEY_SCOPES:
        return None
    if command_scope is ConfigLevel.box:
        return (
            f"Error: box-scope agent settings can't be {verb} bare (a bare agent "
            f"key targets agent.default, which a box cannot write). "
            f"Use '{verb} box.agent.<key>' — did you mean '{verb} box.agent.{canonical}'?"
        )
    # workset — no mirror; point at system (all agents) or the per-box mirror.
    return (
        f"Error: agent settings can't be {verb} at workset scope (a workset spans "
        f"multiple boxes/agents, so there's no single agent to configure). "
        f"Configure them at system scope to apply to all agents, or per-box via "
        f"'box.agent.{canonical}'."
    )


# ``system.default_agent`` is the lone ``system.*``-named SETTING (behavior, not
# a config path).  It does NOT land in the ``[system]`` config table; it lands in
# the SYSTEM settings tier — the reserved any-agent ``agent.default`` table, key
# ``default_agent`` — where ``config.read_default_agent`` reads it back.  Phase 5
# re-points the system settings tier to ``@config.settings``.
_DEFAULT_AGENT_KEY = "system.default_agent"
_DEFAULT_AGENT_SECTIONS: tuple[str, ...] = ("agent", "default")
_DEFAULT_AGENT_LEAF = "default_agent"


def _is_default_agent_key(key: str) -> bool:
    """The ``system.default_agent`` SETTING (routed to the settings tier)."""
    return key == _DEFAULT_AGENT_KEY


def _is_system_path_key(key: str) -> bool:
    """Keys that belong in the bootstrap config file's PATH tables (file-only).

    Covers BOTH the Layer-1 ``[config]`` foundation keys (``config.*``, spec §1)
    and the STRUCTURAL Layer-2 ``system.*`` path-tier family — the exact
    :data:`~kanibako.paths.SYSTEM_PATH_DEFAULTS` set that
    ``resolve_system_paths`` materializes from ``kanibako_config.yaml``'s
    ``[system]`` table — both live in ``kanibako_config.yaml`` and are
    structural (file-only).

    The F2/F3 fix: this is a PRECISE family membership check, NOT a
    ``system.*``-wide catch-all.  A ``system.*`` SETTINGS key (the auth chain
    ``system.auth.share_allowed``, ``system.default_agent``, categories, env)
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
        # branch no longer reaches _system_key_refusal for a config.* set/reset.
        return True
    if not key.startswith("system."):
        return False
    if key == "system.setup_completed":
        return True
    # Lazy import (config_interface ↔ paths would cycle at module load).
    from kanibako.paths import SYSTEM_PATH_DEFAULTS

    return key in SYSTEM_PATH_DEFAULTS


def _user_config_file_str() -> "Path | str":
    """The RESOLVED user bootstrap config file, for refusal messages.

    Rendered (JC-B2-1) so a non-default ``$XDG_CONFIG_HOME`` shows the user's
    real file.  This is an ERROR path — it must never itself raise: if
    XDG/``$HOME`` resolution fails (``xdg`` falls back to ``Path.home()``, which
    raises when ``$HOME`` is unset), fall back to the documented literal default
    rather than turning a clean refusal into a traceback.
    """
    from kanibako.config import config_file_path
    from kanibako.paths import xdg

    try:
        return config_file_path(xdg("XDG_CONFIG_HOME", ".config"))
    except Exception:
        return "~/.config/kanibako_config.yaml"


def _system_key_refusal(key: str) -> str:
    """Error string refusing a CLI write to a FILE-ONLY ``system.*`` config key.

    STRUCTURAL ``system.*`` path-tier keys (the ``SYSTEM_PATH_DEFAULTS`` family,
    see :func:`_is_system_path_key`) are layout config, not behavior settings,
    so they are file-only: editable in the config file (or via ``kanibako
    setup``) but never via ``config set``/``--reset``.  Points the user at the
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
    illegal (spec §2h L1252-1254 — workset and box ONLY)."""
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
    from kanibako.settings_prefs import (
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


def _pref_value_error(
    canonical: str,
    value: "str | None",
    *,
    config_path: Path,
    system_path: Path | None,
    agent_path: Path | None,
    workset_path: Path | None,
    box_path: Path | None,
    agent_name: str,
    default_categories: "Mapping[str, object] | None",
) -> str | None:
    """Validate a pref's VALUE against the shape + resolution of its TARGET key.

    ⚑ THE VALUE IS VALIDATED AT THE **TARGET** PATH, NEVER AT THE ``pref.*`` PATH.
    A pref's key position says nothing about what value is legal there — the
    TARGET does. Two consequences, both of which bit before this existed:

    * **A structured target rejects a scalar.** ``pref.agent.claude.common.x
      just-a-string`` used to be accepted and then killed the LAUNCH with
      "category agent.claude.common.x is str, expected a Bind" — naming a key the
      user never wrote. The direct category route refuses a malformed value at
      set time; the pref route must too, or it is a hole in the same wall.
    * **The E3 resolution probe must run at the TARGET.** Probing at
      ``pref.<target>`` is a NO-OP by construction: ``expand`` carries the
      ``pref`` subtree through unexpanded (spec §2h L1263), so no ``@``-ref in it
      is ever resolved and no defect can be recorded. ``pref.agent.claude.template
      @typo`` was therefore accepted and then silently DROPPED the target at
      launch. Applying the candidate at the target path is what makes the probe
      mean anything.

    Returns an ``Error: …`` string when refused, else ``None``. A ``None`` *value*
    (the ``--null`` suppression request) is always shape-legal: present-``None``
    is the §3 terminal every category and scalar leaf accepts, and it is §2h's
    ONLY suppression channel.
    """
    from kanibako.settings_categories import BIND_KEY_RE, MASK_KEY_RE

    target = canonical[len(PREF_ROOT) + 1:]
    if value is None:
        return None  # the suppression request — legal at any leaf (§3 / §2h).

    # ⚑ DELIBERATE, DO NOT "FIX": the VALUE of ``pref.system.agent`` is NOT
    # checked against the installed agents. §2h validates the TARGET KEY, and
    # its own agent rule is about the DISCRIMINATOR in ``agent.*.**`` — *"the
    # agent test is 'is it a VALID agent', NOT 'is it the ACTIVE agent' — so
    # pre-configuring an agent you may switch to is allowed"* (§2h L1221-1222).
    # An unknown NAME here surfaces at agent RESOLUTION (P7), with the error that
    # subsystem already owns, rather than being pre-judged by the config writer.

    if BIND_KEY_RE.match(target) is not None or MASK_KEY_RE.match(target) is not None:
        return (
            f"Error: '{canonical}' targets '{target}', which is a STRUCTURED "
            f"category entry — a binding is a pair [host_src, box_dest], never a "
            f"scalar (spec §2a). Write the request in the settings file:\n"
            f"  pref:\n"
            f"    {chr(10).join(_yaml_skeleton(target)).lstrip()}\n"
            f"...or suppress the entry with: --null {canonical}"
        )

    # A SCALAR target: run the same E3 resolution probe the direct scalar route
    # runs, applied AT THE TARGET so the expander actually sees the candidate.
    resolves, _raw = _category_set_lookups(
        config_path,
        canonical=target,
        system_path=system_path,
        agent_path=agent_path,
        workset_path=workset_path,
        box_path=box_path,
        agent_name=agent_name,
        default_categories=default_categories,
    )
    defect = resolves(target, value)
    if defect is not None:
        return (
            f"Error: '{canonical}' value {value!r} does not resolve at its target "
            f"'{target}': {defect}. A pref is installed at the target key and "
            f"resolved like any other value (spec §2h), so an unresolvable request "
            f"would silently change or drop '{target}' at launch."
        )
    return None


def _yaml_skeleton(target: str) -> list[str]:
    """The nested-YAML skeleton for *target*, for a refusal message.

    A user refused a CLI spelling needs the spelling that DOES work; printing the
    dotted key they just typed would only repeat what failed.
    """
    parts = target.split(".")
    lines = []
    for i, seg in enumerate(parts):
        lines.append("  " * (i + 1) + f"{seg}:" if i < len(parts) - 1
                     else "  " * (i + 1) + f"{seg}: [<host_src>, <box_dest>]")
    return lines


def _is_path_category_key(key: str) -> bool:
    """True iff *key* is a PATH-TUPLE category key settable via ``config set``.

    The source-only RAW repoint (spec §2a / design §6d / S24) applies to the
    bind-shaped categories ONLY — ``bindings.{ro,rw}`` / ``caches`` / ``seeded`` /
    ``common`` / ``synced`` (a 2-/3-element ``[host_src, box_dest[, options]]``
    tuple). ``env`` (scalar) is routed by the earlier ``_is_env_key`` branch;
    ``masks`` (a keyed list) is YAML-only (spec §2a L216) and is NOT matched here.
    """
    from kanibako.settings_categories import BIND_KEY_RE

    return BIND_KEY_RE.match(key) is not None


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


def _host_xdg_map(data_home: "Path | None" = None) -> dict[str, str]:
    """Thin module-PRIVATE delegate to :func:`kanibako.paths.host_xdg_map`.

    Exists so the ONE canonical XDG-map builder is reachable as a
    ``config_interface`` attribute (patchable, single-source) WITHOUT a
    module-load import of ``paths`` (which would cycle: ``config_interface`` ↔
    ``paths``). Underscored so it is NOT a second PUBLIC import surface for the
    builder (Editor NIT): the one public builder stays ``paths.host_xdg_map``;
    this is only the deferred-import hook ``_set_time_ctx`` calls. There is no
    second hand-rolled XDG map (spec §1 XDG clause + L2 §3).
    """
    from kanibako.paths import host_xdg_map

    return host_xdg_map(data_home)


def _set_time_ctx(config: "dict[str, str] | None" = None) -> "Any":
    """Build the :class:`~kanibako.settings_resolve.ResolveCtx` for the set-time E3
    resolution probe.

    Populates the FULL XDG var set (so ``$XDG_*`` host-source tokens resolve) plus
    home; ``$AGENT`` / ``$WORKSET`` are left unset here (a set-time check has no live
    launch agent/workset, and a category ``host_src`` carrying ``$AGENT``/``$WORKSET``
    is unusual — an unset one falls into the resolver's "not set in this context"
    branch, which the lenient expand records as a defect, exactly as build would for
    a host-side ``$AGENT`` with no agent). Box-side ``$XDG``/``~`` in a ``box_dest``
    are NOT validated here — they are DEFERRED (S17) and the probe only resolves the
    host_src half.

    *config* is the Layer-1 ``config.*`` foundation (resolved bootstrap paths) so an
    ``@config.*`` host_src ref routes to the foundation (JC-2), NOT the snapshot.

    The ``$XDG_*`` map is built by the ONE canonical builder
    :func:`kanibako.paths.host_xdg_map` (spec §1 XDG clause + L2 §3 single-source-
    of-truth: a hand-rolled per-context map is a bug), reached through the
    module-private :func:`_host_xdg_map` deferred-import hook (avoids the
    ``config_interface`` ↔ ``paths`` module-load cycle) so it stays a single
    source.
    """
    from kanibako.settings_resolve import ResolveCtx

    return ResolveCtx(
        agent_name=None,
        workset_name=None,
        host_home=str(Path.home()),
        xdg=_host_xdg_map(),
        config=config or {},
    )


def _agent_scope_node(key: str) -> str:
    """The agent NODE a discriminated agent-scope *key* names, else ``""``.

    ``agent.claude.common.plugins`` -> ``claude``; ``box.bindings.rw.home`` -> ``""``.
    Reads the scope token the category regex already parses, so the discriminator is
    split the one canonical way (a persona node carries no dot, so the first two
    segments are the whole scope).
    """
    from kanibako.settings_categories import BIND_KEY_RE

    m = BIND_KEY_RE.match(key)
    if m is None:
        return ""
    scope = m.group("scope")
    return scope.split(".", 1)[1] if scope.startswith("agent.") else ""


def _category_set_lookups(
    config_path: Path,
    *,
    canonical: str,
    system_path: Path | None = None,
    agent_path: Path | None = None,
    workset_path: Path | None = None,
    box_path: Path | None = None,
    agent_name: str = "",
    default_categories: "Mapping[str, object] | None" = None,
):
    """Build the set-time lookups for a category ``config set`` at *config_path*
    (the COMMAND-scope file): the E3 RESOLUTION probe (Q9, spec §2a) AND the
    raw-cascade Bind lookup (F10 — the must-exist-in-the-CASCADE check), both over
    the SAME single merged snapshot (E3 single-snapshot; no second assembly).

    Builds the FULL merged cascade snapshot for the command's TARGET ONCE via the
    committed pipeline (``assemble_levels`` → ``merge`` — single-source, NOT
    re-implemented), then returns ``(resolves, raw_bind)``:

    * ``resolves(key, value)`` applies the candidate RAW *value* (the new
      ``host_src``) at *key* into a FRESH copy of the merged snapshot,
      lenient-``expand``s it (collect-not-raise), and returns the edited key's
      defect reason (BLOCK) or ``None`` (ALLOW) — the E3 test "does the edited
      value resolve cleanly post-edit?".
    * ``raw_bind(key)`` returns the key's effective RAW pre-expansion
      :class:`~kanibako.settings_store.Bind` from the merged snapshot — the tuple
      the resolver would pick (merge precedence) — or ``None`` when no scope in
      the set-time cascade sets a bind there (absent / suppressed / not
      bind-shaped). NOTE: the set-time cascade covers every scope's settings
      FILE plus the resolved ``system.*`` floor; the runtime-gathered default
      binds (core/kani/channel/target tables, launch-only floor) are NOT in it.

    FULL CASCADE at set-time (Jei ruling 2026-06-29 — (b)). The visible keyspace is
    the SAME resolved cascade the launch would see (spec §2a "layer the target's
    settings in precedence order"): every scope's settings file
    (*system_path* / *agent_path* / *workset_path* / *box_path*) is layered in its
    TRUE precedence slot — EXACTLY as ``settings_launch.build_launch_snapshot`` /
    ``start._effective_behavior_for_display`` assemble for ``config --effective`` —
    plus the resolved ``system.*`` config tier folded as the ``base`` FLOOR (so
    ``@config.data`` etc. resolve). So a cross-scope ``@``-ref in the edited value
    (e.g. a ``box set`` value referencing ``@workset.vault_ro/x``) resolves at
    set-time exactly as it would at launch — no longer a false-block.

    The COMMAND-scope file (*config_path*) is placed into its OWN precedence slot by
    the edited key's SCOPE token (``box.*`` → box slot, ``workset.*`` → workset slot,
    ``system.*`` → system slot), NOT always the box slot — so a sibling repoint still
    sees the file's own keys, and a higher-scope ref sees the higher-scope file. The
    explicit ``*_path`` kwargs default to the command-scope file (so a caller that
    passes ONLY *config_path* still gets the file in its true slot); a caller that
    plumbs the full cascade (the three set handlers) passes every scope's file.

    Resolution NEVER touches the stored file — it writes RAW (§0); the snapshot is
    in-memory and for the CHECK only.
    """
    from kanibako.config import config_file_path
    from kanibako.paths import load_system_config, xdg
    from kanibako.settings_assemble import assemble_levels
    from kanibako.settings_expand import expand
    from kanibako.settings_merge import merge

    # The path tier as the resolution context: the Layer-1 ``config.*`` foundation
    # goes into ``ctx.config`` (so an ``@config.*`` host_src routes there — JC-2),
    # and the Layer-2 ``system.*`` paths into the cascade FLOOR (so an
    # ``@system.*`` host_src resolves from the snapshot). A resolution failure here
    # must NOT crash a config set — fall back to empty (sibling refs still
    # resolve).
    floor: dict[str, object] = {}
    config_foundation: dict[str, str] = {}
    try:
        config_home = xdg("XDG_CONFIG_HOME", ".config")
        user_config = config_file_path(config_home)
        data_home = xdg("XDG_DATA_HOME", ".local/share")
        for dotted, path in load_system_config(
            user_config, data_home=data_home, home=Path.home(),
        ).items():
            if dotted.startswith("config."):
                config_foundation[dotted] = str(path)
            elif dotted.startswith("system."):
                floor[dotted] = str(path)
    except Exception:
        pass

    # The agent STORE-ROOT anchors (spec §2d L515), from the SAME builder the launch
    # floor uses. Load-bearing for the bare-relative refusal: that error tells the
    # user to spell an abstract-category source as
    # ``@meta.agent.<a>.path/<category>/<name>``, and without this the very value the
    # tool just recommended would be rejected here as a dangling @-reference. A hint
    # the tool then refuses is worse than no hint.
    #
    # Anchored for BOTH the agent the command targets AND the agent the EDITED KEY
    # names. The second is not redundancy: the per-node routing that supplies
    # *agent_name* covers ``agent.<node>.bindings.*`` only, so an agent-scope
    # ``common`` / ``caches`` / ``seeded`` set arrives here with no agent name at all
    # — and the store root a value needs is the one its own key names, whichever
    # agent the surrounding command happens to be about.
    #
    # With no agent in play at either seam the key stays absent, so an
    # ``@meta.agent.*`` source is correctly a DANGLING ref rather than a
    # silently-empty one.
    from kanibako.settings_launch import meta_agent_path_floor

    for anchor_agent in (agent_name, _agent_scope_node(canonical)):
        if anchor_agent:
            floor.update(meta_agent_path_floor(anchor_agent))

    ctx = _set_time_ctx(config=config_foundation)

    # F10 / item-0: fold the caller's context-light default-category FLOOR registry
    # into the SAME base floor so a source-only repoint of a LAUNCH-ONLY floor bind
    # (the CORE box mounts — ``box.bindings.{ro,rw}.<key>``) sees the key in the
    # SET-TIME cascade. Those binds live only in the launch floor
    # (``core_default_categories``, host-probed per box/mode), so before this fold
    # the F10 must-exist gate refused a repoint of them ("nowhere in the cascade").
    # The registry (``core_defaults.core_default_bind_keys``) carries the STATIC
    # box_dest + options with a PLACEHOLDER host_src — exactly what the repoint needs
    # (``repoint_host_src`` keeps only ``base[1:]``, discarding the placeholder). The
    # keys are ALREADY fully scope-qualified (``box.*``), so this is a DIRECT union
    # (no agent-scope discrimination needed — agent-scope default tables are built
    # DISCRIMINATED by the declaring plugin, and this bindings-only registry emits
    # only ``box.*`` keys anyway). A scope FILE tuple at the same key still OVERRIDES this floor via merge
    # (base is least-specific), so an already-file-set bind repoints from the file
    # (no regression), and a box-scope written tuple wins at launch by reconcile
    # precedence (box beats the base floor).
    if default_categories:
        for reg_key, reg_val in default_categories.items():
            if reg_val == "":
                continue
            floor[reg_key] = reg_val

    # Place the COMMAND-scope file (config_path) into its TRUE precedence slot by the
    # edited key's scope token — a box.* set lands in the box slot, workset.* in the
    # workset slot, system.* in the system slot (NOT always the box slot). The
    # explicit cascade kwargs (passed by the set handlers) supply the OTHER scopes'
    # files so a cross-scope @-ref resolves as it would at launch; each defaults to
    # the command-scope file for its own slot, so a caller that passes only
    # config_path still gets the file placed correctly.
    scope = canonical.split(".", 1)[0]
    cmd = config_path if config_path.exists() else None
    sys_p = system_path
    agent_p = agent_path
    ws_p = workset_path
    box_p = box_path
    if scope == "system":
        sys_p = cmd if sys_p is None else sys_p
    elif scope == "workset":
        ws_p = cmd if ws_p is None else ws_p
    elif scope == "agent":
        # A per-node descriptor bind (``agent.<node>.bindings.*``, item-0) sets the
        # AGENT-scope file (``agents/<node>/settings.yaml``); place it in the agent
        # slot so its own already-set tuple (read by ``_agent_partial`` at the
        # ``agent.<agent_name>`` sub-table) is the cascade winner — NOT the box slot
        # (where ``_drop_upward_scopes`` would DROP its agent-scope keys).
        agent_p = cmd if agent_p is None else agent_p
    else:  # box (the default / most-specific scope)
        box_p = cmd if box_p is None else box_p

    # Assemble the FULL cascade — the command-scope file in its slot, the other
    # scopes' files in theirs (single-source: the same assemble_levels the launch
    # snapshot uses) — then merge to ONE raw snapshot.
    levels = assemble_levels(
        agent_name=agent_name,
        system_path=sys_p,
        agent_path=agent_p,
        workset_path=ws_p,
        box_path=box_p,
        floor=floor,
    )
    base_snapshot = merge(levels)

    def resolves(key: str, value: str) -> "str | None":
        # Apply the candidate raw host_src at *key* into a FRESH copy (S19 — never
        # mutate the shared merged snapshot), lenient-expand, and read the edited
        # key's defect (if any). Setting the leaf to the raw host_src STRING is
        # sufficient for the E3 upstream-chain check — ``_expand_str`` resolves it
        # host-side exactly as ``_expand_bind`` resolves the host half.
        candidate = _clone_keystore(base_snapshot)
        try:
            _set_leaf(candidate, key.split("."), value)
        except ReservedKeyError as exc:
            # A RESERVED leaf name (``…common.get``) is a set-time DEFECT, not a
            # crash: ReservedKeyError is a KeyError, so it escaped this closure
            # and broke set_config_value's "returns an error string, NEVER
            # raises" contract (the H1 rule). Report it as the defect it is.
            return str(exc)
        result = expand(candidate, ctx, collect_errors=True)
        assert isinstance(result, tuple)  # lenient mode → (snapshot, errors)
        errors = result[1]
        if key not in errors:
            return None
        return errors[key]

    def raw_bind(key: str) -> "Any | None":
        # The key's effective RAW tuple in the SAME merged snapshot (F10): walk
        # the pre-expansion store with unbound dict ops (S3) and yield the leaf
        # iff it is a Bind — the merge already picked the precedence winner.
        from kanibako.settings_store import Bind, KeyStore

        node: "Any" = base_snapshot
        for seg in key.split("."):
            if not isinstance(node, KeyStore):
                return None
            node = dict.get(node, seg)
            if node is None:
                return None
        return node if isinstance(node, Bind) else None

    return resolves, raw_bind


def _clone_keystore(store: "Any") -> "Any":
    """Deep-clone a :class:`KeyStore` (nested KeyStores rebuilt; leaves shared —
    they are immutable Binds / scalars). Used so the candidate-edit + lenient expand
    never mutate the shared base merged snapshot (S19). Unbound ``dict`` ops (S3)."""
    from kanibako.settings_store import KeyStore

    out = KeyStore()
    for k in dict.keys(store):
        v = dict.__getitem__(store, k)
        out[k] = _clone_keystore(v) if isinstance(v, KeyStore) else v
    return out


def _set_leaf(store: "Any", parts: list, value: object) -> None:
    """Set *value* at the dotted *parts* path in *store*, creating nested KeyStore
    nodes as needed (unbound ``dict`` ops, S3). Used to apply the candidate edit
    into the cloned snapshot before the E3 lenient-expand check."""
    from kanibako.settings_store import KeyStore

    node = store
    for seg in parts[:-1]:
        existing = dict.get(node, seg, None)
        if not isinstance(existing, KeyStore):
            existing = KeyStore()
            node[seg] = existing
        node = existing
    node[parts[-1]] = value


def _set_category_value(
    canonical: str,
    value: str,
    *,
    config_path: Path,
    system_path: Path | None = None,
    agent_path: Path | None = None,
    workset_path: Path | None = None,
    box_path: Path | None = None,
    agent_name: str = "",
    default_categories: "Mapping[str, object] | None" = None,
) -> str:
    """Validate + RAW-repoint a path-tuple category key (S24/S25, spec §2a).

    Runs ``validate_config_set`` (Error refuses, Warn proceeds-with-message, OK
    silent) BEFORE the write, then ``repoint_host_src`` (swaps host_src, preserves
    box_dest+opts RAW, key-MUST-exist-in-the-CASCADE — F10: the effective raw
    cascade tuple from the SAME set-time merged snapshot the E3 probe uses backs
    a repoint whose key the command's own file does not set yet; refused only
    when NO scope sets it). The WARN message is surfaced to the user AND the set
    proceeds. A ``ConfigSetError`` (key nowhere in the cascade / non-tuple value)
    is returned as an ``Error:`` string (the CLI prints it to stderr + exit 1).

    The cascade kwargs (*system_path* / *agent_path* / *workset_path* / *box_path* /
    *agent_name*) are plumbed straight to :func:`_category_set_lookups` so the E3
    probe resolves the edited value against the FULL launch cascade (Jei (b),
    2026-06-29) — a cross-scope ``@``-ref no longer false-blocks — and the F10
    must-exist lookup sees the same full cascade.
    """
    from kanibako.settings_configset import (
        ConfigSetError,
        Error,
        Warn,
        repoint_host_src,
        validate_config_set,
    )

    def _host_exists(raw: str) -> bool:
        # A plain literal host path; ``~`` is home-relative. (A token-bearing
        # value is not path-checked — validate_config_set only calls this for a
        # literal host_src.)
        from pathlib import Path as _Path
        return _Path(raw).expanduser().exists()

    resolves, raw_bind = _category_set_lookups(
        config_path,
        canonical=canonical,
        system_path=system_path,
        agent_path=agent_path,
        workset_path=workset_path,
        box_path=box_path,
        agent_name=agent_name,
        default_categories=default_categories,
    )
    verdict = validate_config_set(
        canonical,
        value,
        is_category=True,
        resolves=resolves,
        host_exists=_host_exists,
    )
    if isinstance(verdict, Error):
        return f"Error: {verdict.message}"

    # F10: the effective RAW cascade tuple (merge-precedence winner), normalized
    # to the plain 2-/3-element list shape the writer stores — a 2-tuple bind has
    # opts=None, which is ABSENT in the file form, never a stored null.
    bind = raw_bind(canonical)
    cascade_tuple: "list[str] | None" = None
    if bind is not None:
        cascade_tuple = (
            [bind.host, bind.box]
            if bind.opts is None
            else [bind.host, bind.box, bind.opts]
        )

    # A per-node agent bind (``agent.<node>.bindings.*``) is stored in the per-agent
    # file under its ``self`` table (``self.<node>.bindings.*``), NOT the canonical
    # ``agent`` token — resolve that FILE route through the shape SoT so the write
    # lands exactly where ``_agent_partial`` / ``_node_bind_target`` read it. Every
    # other scope bind (box/workset/system) writes at its canonical split (dest=None).
    dest_parts: "tuple[str, ...] | None" = None
    if _is_agent_node_bind_key(canonical):
        parsed = _parse_agent_node_bind_key(canonical)
        if parsed is not None:
            node, _cat, _name = parsed  # _cat = full "bindings.ro"/"bindings.rw"
            from kanibako.agent_config import agent_file_route

            secs, leaf = agent_file_route(f"{_cat}.{_name}", node)
            dest_parts = (*secs, leaf)

    try:
        repoint_host_src(
            config_path, canonical, value,
            cascade_bind=cascade_tuple, dest_parts=dest_parts,
        )
    except ConfigSetError as exc:
        return f"Error: {exc}"

    confirm = f"Set {canonical} host source to {value}"
    if isinstance(verdict, Warn):
        return f"{confirm}\nWarning: {verdict.message}"
    return confirm


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
# Get / set / reset operations
# ---------------------------------------------------------------------------

def get_config_value(
    key: str,
    *,
    global_config_path: Path,
    project_toml: Path | None = None,
    env_global: Path | None = None,
    env_project: Path | None = None,
    system_settings_path: Path | None = None,
    agents_root: Path | None = None,
    command_scope: "ConfigLevel | None" = None,
) -> str | None:
    """Read a single config value from the appropriate store.

    Returns the resolved (merged) value as a string, or None if the key
    is not set.

    *system_settings_path*, when supplied (the SYSTEM scope), is the file used
    for SETTINGS reads (``system.default_agent`` + agent settings) — i.e.
    ``@config.settings`` = ``global/settings.yaml``.  When None (box/workset
    scope) the existing ``project_toml``/``global_config_path`` paths are used,
    so those scopes keep their own ``settings.yaml`` behavior.  CONFIG
    (``system.*`` layout) reads always use ``global_config_path``.

    GET SEMANTICS (spec §2a "Read verbs" clause, folded 2026-07-02 — Jei clause 5,
    impl ``3e0eb9e``): a plain ``get <key>`` returns the value STORED AT THIS
    NOUN'S settings file (including a downward key it stored), else ``None``
    (rendered "(not set)").  It NEVER fabricates a built-in default and NEVER
    returns another tier's value — that is the ``--effective`` cascade view (the
    ``show`` path), which is unchanged.  So a settings read here reads the
    NOUN'S file (``settings_dest`` = ``system_settings_path`` at SYSTEM, else
    ``project_toml``) — get reads exactly where ``set`` wrote (F5/F6 + the
    F2/F3-class downward-key sibling: all "get reads where set wrote").
    """
    canonical = _resolve_key(key)

    # A BARE agent behavior key at BOX command scope has no readable value of its
    # own: a box cannot write ``agent.default.<key>`` (it is dropped at launch — see
    # :func:`box_agent_redirect_key` + ``set_config_value``). REDIRECT the read to
    # the box's active-agent mirror ``box.agent.<key>`` so ``get`` reads exactly
    # where a corrected ``set box.agent.<key>`` wrote, and the caller NAMES the
    # value ``box.agent.<key>`` (teaching the canonical form). WORKSET has no mirror,
    # so a workset bare-agent-key get is REFUSED at the command handler
    # (:func:`bare_agent_key_scope_error`, verb "read"), not here — this forgiving
    # read only applies to box. Every other form / scope is unchanged.
    _box_agent_redirect = box_agent_redirect_key(canonical, command_scope)
    if _box_agent_redirect is not None:
        canonical = _box_agent_redirect

    # The NOUN's settings file — the SAME per-noun selection ``set``/``reset``
    # use (``settings_dest``): the system settings file at SYSTEM scope, else the
    # command's own settings file (box/workset ``project_toml``).  A plain get
    # reads ONLY this file for settings keys.
    noun_file = (
        system_settings_path if system_settings_path is not None else project_toml
    )

    # pref.<target-key> — return the REQUEST stored at this noun (spec §2h
    # "config get pref.system.agent returns the REQUEST"; clause 5's plain
    # get = stored-at-noun). The RESOLVED result is the --effective view.
    if _is_pref_key(canonical):
        sections, leaf = _pref_sections_leaf(canonical)
        return _read_stored_pref(noun_file, sections, leaf)

    # env.* keys — read from env files
    if _is_env_key(canonical):
        env_name = canonical[4:]  # strip "env."
        merged = merge_env(env_global, env_project)
        return merged.get(env_name)

    # agent.<node>.bindings.{ro,rw}.<name> — the per-node DESCRIPTOR bind (item-0):
    # read the RAW tuple STORED at ``agent.<node>.bindings.<ro|rw>.<name>`` in the
    # node's OWN settings file ``agents/<node>/settings.yaml`` (the get/set/reset
    # symmetry twin — get reads exactly where ``repoint_host_src`` wrote). Checked
    # BEFORE the persona branch: a bind literally NAMED after a state leaf
    # (``agent.<node>.bindings.ro.model``) would otherwise be mis-captured by the
    # persona form (``model`` is a state leaf). A plain get is stored-at-noun — the
    # RESOLVED/effective bind (descriptor floor + this override) is the ``show
    # --effective`` cascade view, not this (matching persona get: stored-at-noun
    # only). A missing agents_root (box/workset scope) / malformed node → ``None``.
    if _is_agent_node_bind_key(canonical):
        bind_target = _node_bind_target(canonical, agents_root)
        if bind_target is None:
            return None
        path, sections, leaf = bind_target
        return _read_stored_leaf(path, sections, leaf)

    # agent.<node>.secret_path.<VAR> — the per-node SECRET category (spec §2a): read
    # the stored PATH (never the secret VALUE) at the DISCRIMINATED
    # ``agent.<node>.secret_path.<VAR>`` slot in the node's OWN settings file — the
    # get/set/reset symmetry twin. Checked BEFORE the persona branch. Missing
    # agents_root / malformed node → ``None`` ("(not set)").
    if _is_agent_node_secret_key(canonical):
        secret_target = _node_secret_target(canonical, agents_root)
        if secret_target is None:
            return None
        path, sections, leaf = secret_target
        return _read_stored_leaf(path, sections, leaf)

    # <scope>.secret_path.<VAR> (system/workset/box) — read the stored PATH from the
    # NOUN's settings file (stored-at-noun; the --effective cascade view is the show
    # path). project_toml is the command scope's settings file here.
    if _is_scope_secret_key(canonical):
        if project_toml and project_toml.exists():
            parts = canonical.split(".")
            return _read_stored_leaf(
                project_toml, (parts[0], "secret_path"), parts[2],
            )
        return None

    # agent.<node>.<key> — the PER-PERSONA agent key (block B1): read the value
    # STORED at the flat slot in the agent's OWN settings file
    # ``agents/<node>/settings.yaml`` (symmetric with the set/reset branches; the
    # get model's stored-at-noun read — the cascade/effective view is ``show
    # --effective`` / ``agent show``, not this).  A missing agents_root or a
    # malformed node → ``None`` ("(not set)").
    if _is_persona_agent_key(canonical):
        target = _persona_agent_target(canonical, agents_root)
        if isinstance(target, tuple):
            path, sections, leaf = target
            return _read_stored_leaf(path, sections, leaf)
        return None

    # target settings (model, continue_mode, auto_approve, allow_helpers)
    if _is_agent_setting(canonical):
        # The agent-agnostic ``config`` CLI reads/writes the reserved any-agent
        # ``agent.default`` tier; per-agent overrides live under ``agent.<name>``
        # and are resolved by the launch-time effective-state cascade.  For the
        # SYSTEM scope these are SETTINGS that live in the system settings file
        # (system_settings_path), not the kanibako_config.yaml CONFIG file.
        setting_src = (
            system_settings_path if system_settings_path is not None else project_toml
        )
        if setting_src and setting_src.exists():
            settings = read_agent_settings(setting_src, "default")
            if canonical in settings:
                return settings[canonical]
        return None

    # system.default_agent — the SETTING (not a config path).  Read it from the
    # NOUN's settings file ONLY (spec §2a "Read verbs", clause 5): the system
    # settings tier (``@config.settings`` = ``global/settings.yaml``,
    # ``system_settings_path``) at the SYSTEM scope, else this noun's own settings
    # file (``project_toml``).  The OLD box/workset path also fell back to
    # ``global_config_path`` — reading the CONTAINING (global) tier's value from a
    # lower noun, the clause-5 violation ("never another tier's value"; that is the
    # ``--effective`` cascade view).  ``noun_file`` is exactly where ``set``/
    # ``reset`` write this key, so get now reads where set wrote (residuals item 2).
    if _is_default_agent_key(canonical):
        if noun_file is None or not noun_file.exists():
            return None
        settings = read_agent_settings(noun_file, "default")
        if _DEFAULT_AGENT_LEAF in settings:
            return settings[_DEFAULT_AGENT_LEAF] or None
        return None

    # box.agent.<key> — the box-scoped agent mirror (F5, block B5, spec §2b
    # L380). SYMMETRIC with the set/reset branches: read the value STORED at the
    # nested ``box.agent.<key>`` path in the NOUN's settings file (== the box
    # file at box scope). ``get_config_value`` previously lacked this branch (set
    # was test-pinned; get untested), so a ``box get box.agent.<key>`` returned
    # "(not set)" for what ``box set box.agent.<key>`` had just written. Checked
    # BEFORE the routing table so a ``box.agent.bindings.ro.X`` reads its box
    # override, not a routing miss. A plain get is stored-at-noun-only (the
    # cascade fallback to the mirrored ``agent.<box.agent_name>.<key>`` default is
    # the ``--effective`` view, not this).
    if _is_box_agent_key(canonical):
        tail = canonical.split(".")  # ["box", "agent", <key...>, leaf]
        return _read_stored_leaf(noun_file, tuple(tail[:-1]), tail[-1])

    # Path-TUPLE category keys (``<scope>.bindings.{ro,rw}.<name>`` / ``caches`` /
    # ``seeded`` / ``shared`` / ``synced``) — the get/set/reset symmetry twin of the
    # category SET branch (F10, spec §2a). Read the RAW tuple STORED at the nested
    # dotted path in the NOUN's settings file (== the box file at box scope, the
    # system settings file at SYSTEM), exactly where ``repoint_host_src`` wrote it.
    # Checked BEFORE the ``system.*`` file-only branch because a SYSTEM-scope
    # category key (``system.bindings.*``) only LOOKS like a ``system.*`` config
    # key — categories are settable/gettable at every scope (mirrors the set/reset
    # order). A plain get is stored-at-noun; the resolved-with-floor bind is the
    # ``show --effective`` cascade view. Absent → ``None`` ("(not set)").
    if _is_path_category_key(canonical):
        tail = canonical.split(".")
        return _read_stored_leaf(noun_file, tuple(tail[:-1]), tail[-1])

    # config.* / system.* path keys — read the raw set-value from the bootstrap
    # config file's [config]/[system] tables (file-only tier; not a merged-config
    # field).
    if _is_system_path_key(canonical):
        cfg = load_merged_config(global_config_path, project_toml)
        return cfg.config_paths.get(canonical)

    # Regular config keys — route via the SAME known-key table that set/reset
    # use (no get-validated/set-unguarded asymmetry).  An unknown key returns
    # None (rendered "not set").
    routed = _route_key(canonical)
    route = _KEY_ROUTES.get(routed)
    if route is None:
        return None

    # Read the value STORED AT THIS NOUN (F6 + the F2/F3-class sibling). The OLD
    # path returned ``getattr(load_merged_config(...), flat)`` — the merged
    # dataclass, which fabricates the built-in DEFAULT when the noun stored
    # nothing (the F6 lie: ``box get box.image`` printing the default image) and
    # folds in the GLOBAL config file (returning another tier's value). Under the
    # get model a plain get reads ONLY the file ``set`` wrote to, at the routed
    # ``(sections, leaf)`` slot. Mirror ``set``/``reset``'s ``dest`` selection
    # EXACTLY: a scope-prefixed SETTINGS key ({system,agent,workset,box}.*,
    # including a downward key) lands in — and is read from — the NOUN's settings
    # file (``settings_dest``); a SCOPELESS key (vault.*, allow_helpers) lands in
    # the command's own config file (``project_toml`` at box/workset,
    # ``global_config_path`` at SYSTEM). (F2/F3 sibling: a downward ``box.image``
    # set at the system noun lands in the system settings file and is read back
    # HERE.) Absent → ``None`` ("(not set)"); the resolved-with-defaults value is
    # the ``--effective`` cascade (``show``).
    sections, leaf = route
    if canonical.split(".", 1)[0] in _SETTINGS_SCOPE_TOKENS:
        read_file = noun_file
    else:
        read_file = (
            global_config_path if system_settings_path is not None else project_toml
        )
    return _read_stored_leaf(read_file, sections, leaf)


def _read_stored_leaf(
    noun_file: "Path | None", sections: tuple[str, ...], leaf: str,
) -> str | None:
    """Return the value STORED at ``sections/leaf`` in *noun_file* (the get
    model's stored-at-noun read), or ``None`` when absent / no file.

    A root-level scalar (empty *sections*, e.g. ``allow_helpers``) reads the
    document root. Bools render lowercase "true"/"false" (matching ``set``'s
    coercion + ``show``'s rendering); a stored empty string reads as ``None``
    ("(not set)"), preserving the prior "empty ⇒ unset" convention.
    """
    if noun_file is None or not noun_file.exists():
        return None
    node: object = load_doc(noun_file)
    for sec in sections:
        if not isinstance(node, dict):
            return None
        node = node.get(sec)
    if not isinstance(node, dict) or leaf not in node:
        return None
    return _render_stored_scalar(node[leaf])


def _read_stored_pref(
    noun_file: "Path | None", sections: tuple[str, ...], leaf: str,
) -> str | None:
    """Read a stored ``pref`` REQUEST, rendering all THREE empty idioms apart.

    ⚑ The general :func:`_read_stored_leaf` renders a stored ``""`` as ``None``
    ("(not set)") — the "empty ⇒ unset" convention. That convention is WRONG for
    a pref: §2h designates ``get`` as the verb that *"returns the REQUEST"*, and
    the three idioms it must forward untouched (present-``None``, terminal ``""``,
    and absence) are three DIFFERENT requests. Collapsing two of them into one
    display makes the suppression request — the only channel a box has to drop
    something its agent declares — indistinguishable from having asked nothing.

    absent → ``None`` ("(not set)") · present-``None`` → ``"null"`` ·
    ``""`` → ``'""'`` · else the value.
    """
    if noun_file is None or not noun_file.exists():
        return None
    node: object = load_doc(noun_file)
    for sec in sections:
        if not isinstance(node, dict):
            return None
        node = node.get(sec)
    if not isinstance(node, dict) or leaf not in node:
        return None
    v = node[leaf]
    if v is None:
        return "null"
    if v == "":
        return '""'
    if isinstance(v, bool):
        return str(v).lower()
    return str(v)


def _render_stored_scalar(v: object) -> str | None:
    """Render a stored scalar for ``get`` output: bools lowercase, empty → None."""
    if isinstance(v, bool):
        return str(v).lower()
    return str(v) if v != "" else None


def _has_dedicated_route(canonical: str) -> bool:
    """Does SOME ``set_config_value`` branch claim *canonical*?

    ⚑ MIRRORS THE DISPATCH CHAIN in :func:`set_config_value`, in the same order.
    A branch added there without a term here would make this say "no route" for a
    key that in fact has one — so the two must be edited together, and
    ``TestSetDispatchCoverage`` fails if they drift.

    Its ONE job is to keep :func:`_probes_at_set_time` off keys nothing handles,
    so an unknown key still reaches the routing table at the bottom of the
    dispatch and is reported as ``unknown config key: <key>`` — the error §0
    requires, which NAMES the key, rather than a resolution complaint about a
    value on a key that does not exist.
    """
    return (
        _is_pref_key(canonical)
        or _is_env_key(canonical)
        or _is_agent_node_bind_key(canonical)
        or _is_agent_node_secret_key(canonical)
        or _is_scope_secret_key(canonical)
        or _is_persona_agent_key(canonical)
        or _is_agent_setting(canonical)
        or _is_box_agent_key(canonical)
        or _is_path_category_key(canonical)
        or _is_default_agent_key(canonical)
        or _is_system_path_key(canonical)
        or _route_key(canonical) in _KEY_ROUTES
    )


def _probes_at_set_time(canonical: str) -> bool:
    """Does a ``config set`` of *canonical* run the E3 RESOLUTION probe?

    The test is **"does this value reach the expander"**, NOT "is it a scalar".
    A value the expander never sees carries no ``@``/``$`` SYNTAX — those
    characters are DATA in it — so probing would refuse legitimate input with no
    correct spelling available.

    Excluded, and why:

    * **The docker ``.env`` family (bare ``env.<VAR>``).** It is written VERBATIM
      by ``shellenv.set_env_var`` and read verbatim into the container env; it
      never enters the settings snapshot. So ``env.EMAIL=jei@example.com`` and
      ``env.MY_PATH=$HOME/bin`` are ordinary values — the second deliberately
      names a GUEST-side variable, which is the whole point of deferral. ⚑ The
      escape hatch does not help here and must not be suggested: ``\\@`` passes
      the probe but lands in the file WITH the backslash, because the write is
      verbatim while only the probe unescapes. There is no CLI spelling that
      would produce the right value, which is what makes this an exclusion rather
      than a documented sharp edge.
    * **The two CATEGORY paths.** They run their own probe with
      ``is_category=True``, which additionally enforces the source-only and
      self-resolving rules; probing twice would only duplicate the diagnosis.
    * **Keys nothing claims.** They must reach the routing table and be reported
      as an unknown KEY (see :func:`_has_dedicated_route`).

    ⚑ RULED, not incidental — the exclusion is the DOCKER family and nothing
    more. Every other scalar whose value the expander DOES see stays LOUD:
    ``box.shell``, ``workset.boxes``, ``<scope>.secret_path.<VAR>`` and the rest
    all probe, because for them a dangling ref really does resolve to ``""``
    silently at launch.

    ⚑ The KEYSPACE env arm ``<scope>.env.<VAR>`` (``box.env.FOO``) is a DIFFERENT
    key from the bare ``env.FOO`` above and is deliberately NOT excluded here — it
    IS host-expanded at launch (``settings_launch._emit_scope_node`` reads it off
    the EXPANDED snapshot). It is nonetheless unreachable from ``config set``
    today for an unrelated reason: no dispatch branch claims it, so it is reported
    as an unknown key. When a route for it is added it will probe by default,
    which is the intended direction — do not "fix" that by widening
    :func:`_is_env_key`, which would silence the arm that needs the check.
    """
    if _is_env_key(canonical):
        return False
    if _is_path_category_key(canonical) or _is_agent_node_bind_key(canonical):
        return False
    if _is_pref_key(canonical):
        # ⚑ The GENERIC probe is a NO-OP at a ``pref.*`` path and must not run
        # there: ``expand`` carries the ``pref`` subtree through unexpanded (spec
        # §2h L1263), so nothing in it is ever resolved and no defect can be
        # recorded. Worse, applying the candidate at the pref path WRITES the
        # target's leaf names into a KeyStore, so a target whose leaf is a
        # RESERVED name (``…common.get``) raised ReservedKeyError straight out of
        # this function — breaking ``set_config_value``'s "returns an error
        # string, never raises" contract. The pref route runs the REAL probe at
        # the TARGET path instead (:func:`_pref_value_error`).
        return False
    return _has_dedicated_route(canonical)


def set_config_value(
    key: str,
    value: "str | None",
    *,
    config_path: Path,
    env_path: Path | None = None,
    is_system: bool = False,
    system_settings_path: Path | None = None,
    cascade_system_path: Path | None = None,
    cascade_agent_path: Path | None = None,
    cascade_workset_path: Path | None = None,
    cascade_box_path: Path | None = None,
    cascade_agent_name: str = "",
    command_scope: ConfigLevel | None = None,
    agents_root: Path | None = None,
    default_categories: "Mapping[str, object] | None" = None,
) -> str:
    """Write a config value to the appropriate store.

    *config_path* is the settings.yaml (for box/workset) or kanibako_config.yaml
    (for system).  *system_settings_path*, when supplied (the SYSTEM scope), is
    the file SETTINGS (``system.default_agent`` + agent settings) are written to
    — ``@config.settings`` = ``global/settings.yaml`` — keeping them out of the
    kanibako_config.yaml CONFIG file.  When None (box/workset) writes go to
    ``config_path`` as before.  Returns a human-readable confirmation message.

    The ``cascade_*`` kwargs supply the FULL launch cascade (every scope's settings
    file + the active agent name) for a CATEGORY ``config set``'s set-time E3
    resolution probe (Jei (b), 2026-06-29): the three set handlers
    (``box/_parser.py`` / ``workset_cmd.py`` / ``system_cmd.py``) already hold this
    context and thread it here so a cross-scope ``@``-ref resolves at set-time
    exactly as it would at launch. They are additive and only consulted on the
    category path; absent, the command-scope file is still placed in its true slot.

    *default_categories* is the caller's context-light set-time FLOOR registry
    (F10 / item-0) — the LAUNCH-ONLY core-bind KEYS (``box.bindings.{ro,rw}.<key>``
    from ``core_defaults.core_default_bind_keys``) with STATIC box_dest+options and a
    placeholder host_src — folded into the category set-time cascade so a source-only
    repoint of a core floor bind is no longer refused as "nowhere in the cascade".
    Only consulted on the category path; the box handler builds and threads it.

    *command_scope* is the scope the ``config set`` was issued at (block B4). It
    drives the §0 directional-write guard (``_scope_direction_error``): a write is
    permitted for a key of the command scope's OWN namespace or of any scope it
    CONTAINS (``system ⊃ agent ⊃ workset ⊃ box`` — a downward write lands in the
    command scope's file as an overridable default); an UPWARD write (and any
    ``meta.*`` write) is REFUSED. When ``None`` the guard is skipped.
    """
    canonical = _resolve_key(key)

    # config.* foundation keys are NEVER CLI-settable (block B2): they locate the
    # files everything else lands in, so they cannot live in those files — they
    # live in the bootstrap config file, hand-edited by a human/admin. Refused
    # EXPLICITLY here, BEFORE the scope guard, so every command scope gets the same
    # ruled message (not the cross-scope guard message, and not the older generic
    # _system_key_refusal that mentions `setup`). The READ/show path still consults
    # _is_system_path_key's config. branch — only set/reset short-circuit here.
    if canonical.startswith("config."):
        return _config_key_refusal(canonical, action="set")

    # ``pref.*`` WRITE-SITE guard (spec §2h L1252-1254) — BEFORE the three TARGET
    # filters and before the scope guard. A pref is legal only in a workset or box
    # settings file, and that restriction is what BOUNDS the resolution recursion,
    # so it is a hard rule rather than a convenience. Checked ahead of the target
    # filters deliberately: a user at the system scope must be told the FILE is
    # wrong regardless of the target's quality, or they fix the target and only
    # then discover the write site was never legal.
    pref_site_err = _pref_write_site_error(canonical, command_scope, verb="set")
    if pref_site_err is not None:
        return pref_site_err

    # Scope-direction guard (block B4, spec §0 + §2a) — enforced at the TOP, after
    # canonical key resolution and BEFORE any dispatch branch (env /
    # category / system / regular), so EVERY write path is gated uniformly.
    scope_err = _scope_direction_error(canonical, command_scope)
    if scope_err is not None:
        return scope_err

    # A BARE agent behavior key at BOX or WORKSET command scope targets the
    # any-agent ``agent.default`` tier — an UPWARD write (agent contains both box
    # and workset) that ``settings_assemble._drop_upward_scopes`` DROPS at launch (a
    # silent no-op the old CLI reported as "Set"). Refuse it HERE, BEFORE the write:
    # box teaches the ``box.agent.<key>`` mirror; workset refuses (no mirror — a
    # workset spans many agents). Uniform over the whole ``_is_agent_setting`` family
    # (NOT a per-key list). Legitimate forms untouched: ``box.agent.<key>`` is
    # ``_is_box_agent_key`` (a SAME-scope box write); ``agent.<name>.<key>`` is
    # ``_is_persona_agent_key``; a bare key at SYSTEM scope is a DOWNWARD write.
    bare_err = bare_agent_key_scope_error(canonical, command_scope, verb="set")
    if bare_err is not None:
        return bare_err

    # ``--null`` ROUTE COVERAGE. The RULE is uniform — ``--null <key>`` writes an
    # explicit present-``None`` at that key — but two write MECHANISMS cannot
    # express it, and silently doing something else would be worse than refusing:
    #
    # * ``env.<VAR>`` is a docker ``.env`` STRING store (``shellenv.set_env_var``);
    #   it has no null, and writing the text "None" is the bug this refuses.
    # * the CATEGORY route is a SOURCE-ONLY REPOINT (``repoint_host_src``) — it
    #   rewrites the host half of an EXISTING tuple and has no null form. Direct
    #   category suppression is its own feature (write ``null`` in the settings
    #   file); it is NOT part of this phase, and half-implementing it here would
    #   put two spellings of one idea in the tree.
    #
    # Everything else lands through a nested YAML write, which carries ``None``
    # natively — so ``pref.*``, ``box.agent.*`` and the routed scalars all work.
    if value is None:
        if _is_env_key(canonical):
            return (
                f"Error: --null is not supported for '{canonical}': the env file "
                f"is a plain string store with no null value. Use "
                f"'--reset {canonical}' to remove the variable."
            )
        if _is_path_category_key(canonical) or _is_agent_node_bind_key(canonical):
            return (
                f"Error: --null is not yet supported for the category key "
                f"'{canonical}' (a config set of a category is a source-only "
                f"repoint, which has no null form). Suppress the entry by "
                f"writing 'null' at the key in the settings file, or request the "
                f"suppression from a box/workset with "
                f"'--null pref.{canonical}' (spec §2h)."
            )

    # Write-time validation for the auth-critical ``auto_approve`` permission key
    # (Editor finding B). It routes VERBATIM below (bare -> ``_is_agent_setting``;
    # per-node -> ``_is_persona_agent_key``) and is ``coerce_bool``'d at LAUNCH with
    # an UNRECOGNISED value falling back to the PERMISSIVE default (True). So a typo
    # (``config set auto_approve=flase``) would otherwise be accepted here and
    # silently bring the box up permissive (the UNSAFE direction). Reject a non-bool
    # value NOW using the SAME truth table (``config.coerce_bool``) the launch
    # coercion uses — the happy literals (true/false/1/0/yes/no/on/off, any case)
    # still write verbatim as before; ONLY ``auto_approve`` is guarded (Jei: only
    # the auth-critical key), not ``allow_helpers`` / ``model``.
    if (
        value is not None
        and _is_auto_approve_key(canonical)
        and coerce_bool(value) is None
    ):
        return f"Error: auto_approve must be a boolean (true/false); got {value!r}"

    # SET-TIME RESOLUTION PROBE for a value the EXPANDER will see (E3, spec §2a
    # / Q9).  See :func:`_probes_at_set_time` for exactly which keys qualify and
    # why the test is "does this value reach ``expand``" rather than "is it a
    # scalar".
    #
    # The probe was wired ONLY at the category path, so a set accepted a value
    # whose ``@``-ref or ``$VAR`` does not resolve — e.g.
    # ``config set workset.boxes "@meta.nope.key/boxes"``. For an expanded value
    # that is not inert: an embedded dangling ref is substituted with the EMPTY
    # STRING at launch (§6b) and the key silently resolves to something else.
    #
    # The probe blocks ONLY on the edited value's own transitive upstream chain,
    # so an UNRELATED pre-existing defect still allows the set and ``config set``
    # stays usable to REPAIR a broken config. ``--reset`` is untouched: removing
    # an override cannot introduce a dangling ref in the removed value.
    if value is not None and _probes_at_set_time(canonical):
        from kanibako.settings_configset import Error as _SetError
        from kanibako.settings_configset import validate_config_set

        _resolves, _ = _category_set_lookups(
            config_path,
            canonical=canonical,
            system_path=cascade_system_path,
            agent_path=cascade_agent_path,
            workset_path=cascade_workset_path,
            box_path=cascade_box_path,
            agent_name=cascade_agent_name,
            default_categories=default_categories,
        )
        scalar_verdict = validate_config_set(
            canonical, value, is_category=False, resolves=_resolves,
        )
        if isinstance(scalar_verdict, _SetError):
            return f"Error: {scalar_verdict.message}"

    settings_dest = (
        system_settings_path if system_settings_path is not None else config_path
    )

    # pref.<target-key> — the §2h REQUEST. Validated with the SAME three filters
    # the launch applies (so a stored request cannot fail every future launch),
    # then written to the COMMAND scope's settings file at the NESTED
    # ``pref.<target…>`` slot — the shape ``assemble_levels`` mirrors and
    # ``collect_prefs`` reads. NESTED, never a dotted literal (a bind-shaped
    # value spelled the dotted way would never be bind-parsed, so the two
    # spellings would behave differently — see settings_prefs).
    if _is_pref_key(canonical):
        target_err = _pref_target_error(canonical, command_scope)
        if target_err is not None:
            return target_err
        value_err = _pref_value_error(
            canonical, value,
            config_path=config_path,
            system_path=cascade_system_path,
            agent_path=cascade_agent_path,
            workset_path=cascade_workset_path,
            box_path=cascade_box_path,
            agent_name=cascade_agent_name,
            default_categories=default_categories,
        )
        if value_err is not None:
            return value_err
        sections, leaf = _pref_sections_leaf(canonical)
        _write_nested_toml_key(settings_dest, sections, leaf, value)
        return f"Set {canonical}={'null' if value is None else value}"

    # env.* keys
    if _is_env_key(canonical):
        env_name = canonical[4:]
        if env_path is None:
            return f"Error: no env file path for key {canonical}"
        # value is narrowed by the --null route guard above (env has no null).
        assert value is not None
        try:
            set_env_var(env_path, env_name, value)
        except ValueError as e:
            return f"Error: {e}"
        return f"Set {env_name}={value}"

    # agent.<node>.<key> — the PER-PERSONA agent key (block B1): write to the
    # agent's OWN settings file ``agents/<node>/settings.yaml`` (NOT the command
    # scope's settings file), at the FLAT slot ``load_agent_config`` reads back
    # (state leaf under ``agent:``; ``env.<VAR>`` under ``env:``).  The SECRET
    # pointer ``secret_path.<VAR>`` is handled EARLIER (discriminated node storage,
    # ``_is_agent_node_secret_key``), not here.  The node was ``℘``-canonicalized by
    # ``_resolve_key``. Sparse by construction: ``_write_nested_toml_key`` is
    # read-modify-write, so only the key the user set is materialised — a
    # default-only persona file stays empty of everything else.  The value is
    # written VERBATIM (like every other agent-setting write) — the persona-critical
    # trio (endpoint, secret_path.ANTHROPIC_AUTH_TOKEN, model) are strings.  ``agents_root`` is
    # supplied only by the system scope (the global ``config.agents`` store);
    # absent it, the write is refused (the directional guard already refuses this
    # key from box/workset — an UPWARD agent-scope write).
    # agent.<node>.bindings.{ro,rw}.<name> — the per-node DESCRIPTOR delivery bind
    # (item-0): a SOURCE-ONLY repoint of the descriptor bind (claude launcher/share)
    # on the agent's OWN settings file. Routed to the CATEGORY path (NOT the persona
    # verbatim-scalar branch below — else it would write a malformed source-only bind
    # with no box_dest) so ``repoint_host_src`` writes the RAW tuple
    # ``[<new_src>, <descriptor box_dest>, <opts>]``. Checked BEFORE
    # ``_is_persona_agent_key`` because a bind literally NAMED ``model`` /
    # ``endpoint`` (``agent.<node>.bindings.ro.model``) would otherwise be captured by
    # the persona branch (``model`` is a state leaf). The §0 directional guard already
    # ran above: system ⊃ agent so a system-scope write is DOWNWARD (allowed); a box/
    # workset write is UPWARD (refused). The command scope (system) supplies
    # ``config_path`` = the node file + the descriptor floor registry
    # (``agent_representation.agent_default_bind_keys``) as ``default_categories`` so
    # the must-exist gate sees the launch-only descriptor floor.
    if _is_agent_node_bind_key(canonical):
        # Narrowed by the --null route guard above (a repoint has no null form).
        assert value is not None
        return _set_category_value(
            canonical, value, config_path=config_path,
            system_path=cascade_system_path,
            agent_path=cascade_agent_path,
            workset_path=cascade_workset_path,
            box_path=cascade_box_path,
            agent_name=cascade_agent_name,
            default_categories=default_categories,
        )

    # agent.<node>.secret_path.<VAR> — the per-node SECRET category (spec §2a). A
    # SCALAR path write to the node's OWN settings file at the DISCRIMINATED
    # ``agent.<node>.secret_path`` sub-table (the shape ``_agent_partial`` reads into
    # the cascade + ``load_agent_config`` reads back). Checked BEFORE the persona
    # branch (env_file was there in rc; secret_path is discriminated node storage, a
    # clean break). The §0 directional guard already ran: agent.* is settable only
    # DOWNWARD from system, so box/workset was refused above; SYSTEM threads agents_root.
    if _is_agent_node_secret_key(canonical):
        secret_target = _node_secret_target(canonical, agents_root)
        if secret_target is None:
            return (
                f"Error: '{key}' is a per-node secret pointer and is only "
                f"settable at the system scope."
            )
        path, sections, leaf = secret_target
        _write_nested_toml_key(path, sections, leaf, value)
        return f"Set {_node_secret_display_key(canonical)}={value}"

    # <scope>.secret_path.<VAR> (system/workset/box) — the SECRET category at a
    # NON-agent scope: a SCALAR path write to the command scope's SETTINGS file at
    # the nested ``<scope>.secret_path.<VAR>`` slot (the shape ``_file_partial`` reads
    # into the cascade). The §0 directional guard already permitted it (own/contained
    # scope). settings_dest = the command scope's settings file (config_path at box/
    # workset; the system settings file at SYSTEM — never the Layer-1 config file).
    if _is_scope_secret_key(canonical):
        parts = canonical.split(".")  # [<scope>, "secret_path", <VAR>]
        _write_nested_toml_key(
            settings_dest, (parts[0], "secret_path"), parts[2], value,
        )
        return f"Set {canonical}={value}"

    if _is_persona_agent_key(canonical):
        target = _persona_agent_target(canonical, agents_root)
        if isinstance(target, str):
            return target  # malformed node ref
        if target is None:
            return (
                f"Error: '{key}' is a per-persona agent setting and is only "
                f"settable at the system scope."
            )
        path, sections, leaf = target
        _write_nested_toml_key(path, sections, leaf, value)
        return f"Set {_persona_display_key(canonical)}={value}"

    # target settings — the agent-agnostic CLI writes the any-agent
    # ``agent.default`` tier (per-agent overrides live under ``agent.<name>``).
    # SYSTEM scope routes to the system settings file (settings_dest).
    if _is_agent_setting(canonical):
        _write_nested_toml_key(settings_dest, ("agent", "default"), canonical, value)
        return f"Set {canonical}={value}"

    # box.agent.<key> — the box-scoped agent mirror (block B5, spec §2b L380). An
    # ORDINARY same-scope (box) write of the box's agent-tweak override; the B4
    # directional guard (above) already PERMITTED it (the box namespace). Write the
    # value VERBATIM into the box settings file at the nested ``box.agent.<key>``
    # path — exactly the box-scope override ``_file_partial`` reads back and the
    # settings_launch materializer keeps (it gap-fills only the names the box did
    # NOT set, so this write WINS). Checked BEFORE the path-category branch so a
    # ``box.agent.bindings.ro.X`` lands as a box-scope override (it has no
    # pre-existing box-file tuple to source-only repoint). The nested sections are
    # the dotted tail under ``box.agent`` (``box.agent.model`` →
    # ``[box][agent]model``; ``box.agent.bindings.ro.share`` → ``[box][agent][
    # bindings][ro]share``). Bind-shaped values are written as the user's RAW string
    # (no tuple parse here — full structured binds belong in the YAML, like every
    # category; this convenience write matches a hand-edit of the box file).
    if _is_box_agent_key(canonical):
        tail = canonical.split(".")  # ["box", "agent", <key...>, leaf]
        sections = tuple(tail[:-1])  # ("box", "agent", ...)
        leaf = tail[-1]
        # A BOX-namespace settings key: lands in the command scope's SETTINGS
        # file (== config_path at box/workset; the system settings file at
        # SYSTEM — a downward write never lands in the Layer-1 config file).
        _write_nested_toml_key(settings_dest, sections, leaf, value)
        return f"Set {canonical}={'null' if value is None else value}"

    # Path-TUPLE category keys (``bindings.{ro,rw}`` / ``caches`` / ``seeded`` /
    # ``shared`` / ``synced``) — the source-only RAW repoint (S24/S25, spec §2a,
    # design §6d). Checked BEFORE the ``system.*`` file-only refusal because a
    # SYSTEM-scope category key (``system.caches.x`` / ``system.bindings.*``) only
    # LOOKS like a ``system.*`` config key — categories are settable at every
    # scope (spec §2a). ``config set <key> <value>`` validates the RAW value at
    # set time (``validate_config_set``) then swaps ONLY ``host_src`` in the
    # existing tuple at the COMMAND-scope file (``repoint_host_src``), preserving
    # ``box_dest`` + options RAW. Source-only: it REPOINTS an existing bind, never
    # creates one. ``env`` (scalar) was handled above; ``masks`` is YAML-only
    # (spec §2a L216) — not a tuple, so a repoint is refused as non-category.
    if _is_path_category_key(canonical):
        # Narrowed by the --null route guard above (a repoint has no null form).
        assert value is not None
        return _set_category_value(
            canonical, value, config_path=config_path,
            system_path=cascade_system_path,
            agent_path=cascade_agent_path,
            workset_path=cascade_workset_path,
            box_path=cascade_box_path,
            agent_name=cascade_agent_name,
            default_categories=default_categories,
        )

    # system.default_agent — a SETTING (F3): lands in the settings tier's
    # reserved any-agent ``agent.default`` table, leaf ``default_agent`` —
    # EXACTLY where the shipped reader (``config.read_default_agent``) reads it
    # back and where ``setup`` writes it.  settings_dest is the system settings
    # file (``@config.settings``) at the SYSTEM scope; set/get/launch all agree
    # on that one location.
    if _is_default_agent_key(canonical):
        _write_nested_toml_key(
            settings_dest, _DEFAULT_AGENT_SECTIONS, _DEFAULT_AGENT_LEAF, value,
        )
        return f"Set {canonical}={value}"

    # STRUCTURAL system.* path-tier keys (the SYSTEM_PATH_DEFAULTS family) —
    # FILE-ONLY: they live in kanibako_config.yaml's [system] table (the file
    # ``resolve_system_paths`` reads), editable there or via ``kanibako setup``
    # (write_system_value bypasses this guard).  The refusal names THAT file.
    # This is a precise family check (F2): a system.* SETTINGS key (auth chain /
    # default_agent / categories / env) was routed above or falls through to the
    # routing table below — it is never refused here.
    if _is_system_path_key(canonical):
        return _system_key_refusal(canonical)

    # Regular config keys — route via the single known-key table (the H1 fix:
    # an unknown key returns an error string and NEVER raises).  Accept either
    # the canonical dotted spelling or the flat underscore form.
    routed = _route_key(canonical)
    route = _KEY_ROUTES.get(routed)
    if route is None:
        return f"Error: unknown config key: {key}"
    sections, leaf = route
    typed = _coerce_value(routed, value)  # the H2 fix (real bool/etc.)
    if isinstance(typed, str) and KEY_TYPES.get(routed):
        # _coerce_value signalled a parse error (it only returns a str for a
        # typed key when coercion failed).
        return typed
    # A scope-prefixed SETTINGS key ({agent,workset,box}.* — including a DOWNWARD
    # write at a containing command scope, spec §0) lands in the COMMAND scope's
    # SETTINGS file with the key's scope token kept (the nested form
    # ``assemble_levels`` mirrors — never remapped to the key-scope's own file).
    # settings_dest == config_path at box/workset; at SYSTEM it is the system
    # settings file (``@config.settings``) — settings keys never land in the
    # Layer-1 kanibako_config.yaml (spec §1). Non-scope keys (allow_helpers) and
    # system.* regular keys keep their historical config_path slot.
    dest = (
        settings_dest
        if canonical.split(".", 1)[0] in _SETTINGS_SCOPE_TOKENS
        else config_path
    )
    if sections:
        _write_nested_toml_key(dest, sections, leaf, typed)
    else:
        _write_toml_key_root(dest, leaf, typed)
    return f"Set {_dot_to_flat(routed)}={'null' if value is None else value}"


def reset_config_value(
    key: str,
    *,
    config_path: Path,
    env_path: Path | None = None,
    system_settings_path: Path | None = None,
    command_scope: ConfigLevel | None = None,
    cascade_system_path: Path | None = None,
    cascade_agent_path: Path | None = None,
    cascade_workset_path: Path | None = None,
    cascade_box_path: Path | None = None,
    cascade_agent_name: str = "",
    agents_root: Path | None = None,
    default_categories: "Mapping[str, object] | None" = None,
) -> str:
    """Remove an override for a single key.  Returns confirmation message.

    *system_settings_path*, when supplied (SYSTEM scope), is where SETTINGS
    (``system.default_agent`` + agent settings) are removed from
    (``@config.settings`` = ``global/settings.yaml``); when None (box/workset)
    they are removed from ``config_path`` as before.

    *command_scope* is the scope the ``config --reset`` was issued at (block B2,
    RESET-GUARD). It drives the §0 directional-write guard
    (``_scope_direction_error``) symmetrically with ``set_config_value``: a reset
    is permitted for a key of the command scope's OWN namespace or of any scope
    it CONTAINS (containment order, spec §0); an UPWARD reset (and any ``meta.*``
    reset) is REFUSED. When ``None`` the guard is skipped.

    The ``cascade_*`` kwargs supply the FULL launch cascade (every scope's
    settings file + the active agent name) — the SAME context
    ``set_config_value`` receives — so the honest cleared-message can append the
    now-effective value + its source tier AFTER the removal (residuals item 1,
    F7 "where cheap"). They are additive and consulted ONLY for that message; a
    caller that omits them still gets the correct cleared-only form.

    *default_categories* is the caller's context-light FLOOR registry (item 3) — the
    launch-only descriptor bind KEYS (``agent.<node>.bindings.{ro,rw}.<name>`` from
    ``agent_representation.agent_default_bind_keys``) with STATIC box_dest+options.
    Consulted ONLY on the per-node bind reset path so the honest cleared-message can
    name the reverted-to FLOOR value; a caller that omits it keeps the cleared-only
    form.
    """
    canonical = _resolve_key(key)

    # config.* foundation keys are NEVER CLI-resettable (block B2) — same rationale
    # as set (they locate files everything else lands in; hand-edited in the
    # bootstrap config file). Refused FIRST, BEFORE the scope guard, with the ruled
    # message (verb "changed" — a reset is a change, not a "set"), pointing at the
    # SAME config file.
    if canonical.startswith("config."):
        return _config_key_refusal(canonical, action="reset")

    # ``pref.*`` WRITE-SITE guard, symmetric with set (a reset is a WRITE).
    pref_site_err = _pref_write_site_error(canonical, command_scope, verb="reset")
    if pref_site_err is not None:
        return pref_site_err

    # Scope-direction guard (block B2 RESET-GUARD, mirrors set_config_value's B4
    # guard, spec §0 + §2a) — after config.* forbid and BEFORE any dispatch branch,
    # so every reset path is gated uniformly.
    scope_err = _scope_direction_error(canonical, command_scope)
    if scope_err is not None:
        return scope_err

    # A BARE agent behavior key at BOX or WORKSET command scope is REFUSED here,
    # symmetric with ``set_config_value`` (the model is: REFUSE writes, redirect
    # reads — a reset is a WRITE). Without this, a bare ``reset <key>`` fell to the
    # ``_is_agent_setting`` branch below and removed ``agent.default.<key>`` from the
    # command file — which the box/workset never wrote (it is DROPPED at launch), so
    # it reported "No override" while the real value (at ``box.agent.<key>`` for a
    # box) stayed STUCK. Refuse BEFORE the removal path: box teaches the
    # ``reset box.agent.<key>`` mirror; workset refuses (no mirror). Uniform over the
    # whole ``_is_agent_setting`` family; SYSTEM-scope bare resets + the
    # ``box.agent.<key>`` / per-agent forms are UNAFFECTED.
    bare_err = bare_agent_key_scope_error(canonical, command_scope, verb="reset")
    if bare_err is not None:
        return bare_err

    settings_dest = (
        system_settings_path if system_settings_path is not None else config_path
    )

    # pref.<target-key> — remove the REQUEST from this noun's settings file
    # (symmetric with the set/get branches: reset clears exactly where set wrote).
    if _is_pref_key(canonical):
        sections, leaf = _pref_sections_leaf(canonical)
        if _remove_nested_toml_key(settings_dest, sections, leaf):
            return f"Cleared {canonical}"
        return f"No override for {canonical}"

    # env.* keys
    if _is_env_key(canonical):
        env_name = canonical[4:]
        if env_path and unset_env_var(env_path, env_name):
            return f"Unset env.{env_name}"
        return f"No override for env.{env_name}"

    # agent.<node>.bindings.{ro,rw}.<name> — the per-node DESCRIPTOR bind (item-0):
    # remove the source-only repoint from the node's OWN settings file
    # ``agents/<node>/settings.yaml`` (the get/set/reset symmetry twin — reset
    # removes exactly where set wrote). Checked BEFORE the persona branch (a bind
    # NAMED after a state leaf must route here). The §0 directional guard already
    # ran: agent.* is settable/resettable only DOWNWARD from system, so a box/
    # workset reset was refused above — reaching here means SYSTEM scope, where
    # ``agents_root`` is threaded. After removal the bind reverts to the descriptor
    # FLOOR; when the caller threads that floor registry (``default_categories`` =
    # ``agent_default_bind_keys(node)``) the honest cleared-message names the
    # reverted-to floor value (item 3), else the cleared-only form.
    if _is_agent_node_bind_key(canonical):
        bind_target = _node_bind_target(canonical, agents_root)
        if bind_target is None:
            return (
                f"Error: '{key}' is a per-node descriptor bind and is only "
                f"resettable at the system scope."
            )
        path, sections, leaf = bind_target
        if _remove_nested_toml_key(path, sections, leaf):
            floor = _floor_bind_display(canonical, default_categories)
            return _honest_reset_message(canonical, command_scope, floor)
        return f"No override for {canonical}"

    # agent.<node>.secret_path.<VAR> — the per-node SECRET category (spec §2a):
    # remove the stored pointer from the node's OWN settings file (symmetric with
    # set/get). Checked BEFORE the persona branch. A missing agents_root / malformed
    # node → refused (only resettable at the system scope).
    if _is_agent_node_secret_key(canonical):
        secret_target = _node_secret_target(canonical, agents_root)
        if secret_target is None:
            return (
                f"Error: '{key}' is a per-node secret pointer and is only "
                f"resettable at the system scope."
            )
        path, sections, leaf = secret_target
        display = _node_secret_display_key(canonical)
        if _remove_nested_toml_key(path, sections, leaf):
            return _honest_reset_message(display, command_scope)
        return f"No override for {display}"

    # <scope>.secret_path.<VAR> (system/workset/box) — remove the stored pointer
    # from the command scope's settings file (symmetric with set/get).
    if _is_scope_secret_key(canonical):
        parts = canonical.split(".")
        if _remove_nested_toml_key(settings_dest, (parts[0], "secret_path"), parts[2]):
            return _honest_reset_message(canonical, command_scope)
        return f"No override for {canonical}"

    # agent.<node>.<key> — the PER-PERSONA agent key (block B1): remove the stored
    # override from the agent's OWN settings file ``agents/<node>/settings.yaml``
    # (symmetric with set/get; ``_remove_nested_toml_key`` prunes now-empty
    # ``agent:``/``env:`` tables, keeping the file sparse).
    if _is_persona_agent_key(canonical):
        target = _persona_agent_target(canonical, agents_root)
        if isinstance(target, str):
            return target  # malformed node ref
        if target is None:
            return (
                f"Error: '{key}' is a per-persona agent setting and is only "
                f"resettable at the system scope."
            )
        path, sections, leaf = target
        display = _persona_display_key(canonical)
        if _remove_nested_toml_key(path, sections, leaf):
            return _honest_reset_message(display, command_scope)
        return f"No override for {display}"

    # target settings — reset the any-agent ``agent.default`` tier (SYSTEM scope
    # routes to the system settings file).
    if _is_agent_setting(canonical):
        if _remove_nested_toml_key(settings_dest, ("agent", "default"), canonical):
            return _honest_reset_message(canonical, command_scope)
        return f"No override for {canonical}"

    # box.agent.<key> — the box-scoped agent mirror (block B5, spec §2b L380):
    # reset = remove the box-scope override so box.agent.<key> falls back to the
    # mirrored agent.<box.agent_name>.<key> default again. Symmetric with the set
    # branch (same nested box.agent.<key> location in the box settings file).
    if _is_box_agent_key(canonical):
        tail = canonical.split(".")
        sections = tuple(tail[:-1])
        leaf = tail[-1]
        # Symmetric with set: the command scope's SETTINGS file. The honest
        # cleared-message form (F7) — same as every other reset branch — replaces
        # the older plain "Reset <key>" so the box.agent mirror reset reads
        # consistently with the rest (residuals item 5).
        if _remove_nested_toml_key(settings_dest, sections, leaf):
            return _honest_reset_message(canonical, command_scope)
        return f"No override for {canonical}"

    # Path-TUPLE category keys — reset symmetry with the category SET branch
    # (F10, spec §2a): remove the COMMAND-scope override tuple from the SAME file
    # the set wrote (config_path), pruning emptied tables, so the cascade's own
    # tuple (a higher scope's or the launch floor's) resurfaces at the next
    # assemble. Before this branch a category key fell through to the routing
    # table and mis-reported "unknown config key".
    #
    # The honest cleared-message (Bug 2) names the reverted-to FLOOR value when the
    # caller threads the context-light core-bind registry (``default_categories`` =
    # ``core_default_bind_keys()``): a CORE bind (``box.bindings.{ro,rw}.<key>``)
    # reverts to the launch descriptor floor, so ``_floor_bind_display`` reports its
    # static box_dest+options (the host_src is a set-time placeholder, re-resolved at
    # launch — never printed). A NON-core category key (a user ``box.caches.foo``, or
    # a caller that omits the registry) → ``None`` → the cleared-only form, same
    # information as the old plain "Reset" but via the honest formatter.
    if _is_path_category_key(canonical):
        tail = canonical.split(".")
        if _remove_nested_toml_key(config_path, tuple(tail[:-1]), tail[-1]):
            floor = _floor_bind_display(canonical, default_categories)
            return _honest_reset_message(canonical, command_scope, floor)
        return f"No override for {canonical}"

    # system.default_agent — a SETTING (F3), symmetric with set: remove it from
    # the settings tier's ``agent.default`` table (where ``read_default_agent``
    # reads), reverting to "no system default" (agent auto-detect).
    if _is_default_agent_key(canonical):
        if _remove_nested_toml_key(
            settings_dest, _DEFAULT_AGENT_SECTIONS, _DEFAULT_AGENT_LEAF,
        ):
            return _honest_reset_message(canonical, command_scope)
        return f"No override for {canonical}"

    # STRUCTURAL system.* path-tier keys — FILE-ONLY (see set_config_value).
    # The CLI refuses to RESET them too (for symmetry); edit the config file
    # directly or re-run ``kanibako setup``.
    if _is_system_path_key(canonical):
        return _system_key_refusal(canonical)

    # Regular config keys — route via the same known-key table as set/get
    # (no get-validated/set-unguarded asymmetry).
    routed = _route_key(canonical)
    route = _KEY_ROUTES.get(routed)
    if route is None:
        return f"Error: unknown config key: {key}"
    sections, leaf = route
    # Symmetric with set_config_value: a scope-prefixed SETTINGS key is removed
    # from the COMMAND scope's settings file (== config_path at box/workset;
    # the system settings file at SYSTEM).
    dest = (
        settings_dest
        if canonical.split(".", 1)[0] in _SETTINGS_SCOPE_TOKENS
        else config_path
    )
    removed = (
        _remove_nested_toml_key(dest, sections, leaf)
        if sections
        else _remove_toml_key_root(dest, leaf)
    )
    flat = _dot_to_flat(routed)
    if removed:
        # Compute the now-effective value + source tier from the POST-RESET
        # cascade (item 1) — the file is already written, so the assembled
        # snapshot reflects the removal. Threads the SAME cascade files/agent the
        # 3 handlers hold; None (no inputs / unresolved) → cleared-only form.
        #
        # GATE (Editor F1): ONLY a scope-prefixed SETTINGS key
        # ({system,agent,workset,box}.*) actually READS through the
        # assemble/merge cascade — so only for those is the assembled snapshot the
        # key's real read path. A SCOPELESS key (``vault.*``, ``allow_helpers``,
        # ``model``/``continue_mode``/``auto_approve``) is read from a single settings
        # file / the flat ``KanibakoConfig`` (NOT the cascade), so a
        # cascade-derived "effective" would name a value from a tier NOTHING reads
        # — a wrong claim. Those keep the cleared-only form. This is the SAME token
        # test that picks ``dest`` above (the write path and the read path agree).
        effective = (
            _effective_after_reset(
                routed, sections, leaf,
                agent_name=cascade_agent_name,
                system_path=cascade_system_path,
                agent_path=cascade_agent_path,
                workset_path=cascade_workset_path,
                box_path=cascade_box_path,
            )
            if canonical.split(".", 1)[0] in _SETTINGS_SCOPE_TOKENS
            else None
        )
        return _honest_reset_message(flat, command_scope, effective)
    return f"No override for {flat}"


def _honest_reset_message(
    flat: str,
    command_scope: "ConfigLevel | None",
    effective: "tuple[str, str] | None" = None,
) -> str:
    """The HONEST ``reset`` confirmation (F7, Jei-ruled 2026-07-02d).

    The behavior is right — clearing a scope override lets the value fall back
    through the cascade — but the OLD message lied: it printed "reverts to
    default: <built-in>" even when the fallback lands on a HIGHER-TIER stored
    default (a workset/system value), not the built-in.  The ruling: say we
    CLEARED the value set on THIS noun (named from the COMMAND scope, not
    hardcoded "box"), and — "where cheap" — show the now-effective value + its
    source tier.

    *effective*, when supplied (residuals item 1 — the caller threads the same
    resolved cascade ``set_config_value`` receives, so it IS cheap now), is the
    ``(value, tier)`` the POST-RESET cascade resolves for this key, computed by
    the SAME assemble/merge/expand path the launch uses (no bespoke re-derivation,
    no built-in guess).  When ``None`` — no cascade inputs supplied, OR the key
    does not resolve cleanly post-reset — we keep the cleared-only form (evidence
    honesty: omit rather than guess a wrong value, the exact lie being fixed).
    """
    scope_phrase = (
        f"the {command_scope.value} scope"
        if command_scope is not None
        else "this scope"
    )
    base = f"Cleared {flat} set on {scope_phrase}; "
    if effective is not None:
        value, tier = effective
        return f"{base}effective is now {value} ({tier})."
    return f"{base}it now falls back through the cascade."


def _effective_after_reset(
    routed: str,
    sections: tuple[str, ...],
    leaf: str,
    *,
    agent_name: str,
    system_path: Path | None,
    agent_path: Path | None,
    workset_path: Path | None,
    box_path: Path | None,
) -> "tuple[str, str] | None":
    """The now-effective ``(value, source_tier)`` for *routed* AFTER a reset has
    removed the command-scope override (residuals item 1, F7 "where cheap").

    Reuses the SAME committed pipeline the launch + set-time probe use
    (``assemble_levels`` → ``merge`` → lenient ``expand``, single-source — NOT a
    re-implementation), so the tier is the one the cascade ACTUALLY resolves. The
    reset already wrote the file, so the assembled snapshot is the POST-RESET
    state (the Editor's condition: build AFTER removal, not stale).

    Returns ``None`` — so the caller keeps the cleared-only form — when: no
    cascade files are supplied (a caller that does not thread them), the key is
    absent from the post-reset snapshot, it is not a plain scalar (a Bind/list
    has no single "effective value" to print here), or it does not expand cleanly
    (an unresolved ``@``-ref / cycle — no built-in guess).
    """
    if all(
        p is None for p in (system_path, agent_path, workset_path, box_path)
    ):
        return None
    from kanibako.config import config_file_path
    from kanibako.paths import load_system_config, xdg
    from kanibako.settings_assemble import assemble_levels
    from kanibako.settings_expand import expand
    from kanibako.settings_merge import merge
    from kanibako.settings_store import Bind, KeyStore

    # The path tier (Layer-1 config.* foundation into ctx.config, Layer-2 system.*
    # into the base FLOOR) — identical to _category_set_lookups; a resolution
    # failure must not break a reset (fall back to empty → keep cleared-only form).
    floor: dict[str, object] = {}
    config_foundation: dict[str, str] = {}
    try:
        user_config = config_file_path(xdg("XDG_CONFIG_HOME", ".config"))
        data_home = xdg("XDG_DATA_HOME", ".local/share")
        for dotted, path in load_system_config(
            user_config, data_home=data_home, home=Path.home(),
        ).items():
            if dotted.startswith("config."):
                config_foundation[dotted] = str(path)
            elif dotted.startswith("system."):
                floor[dotted] = str(path)
    except Exception:
        return None

    ctx = _set_time_ctx(config=config_foundation)
    levels = assemble_levels(
        agent_name=agent_name,
        system_path=system_path,
        agent_path=agent_path,
        workset_path=workset_path,
        box_path=box_path,
        floor=floor,
    )
    # The tier NAMES parallel assemble_levels' order (MOST-SPECIFIC-FIRST):
    # [box, workset, agent.<active>, agent.default, system, base]. The SOURCE tier
    # is the first level that SETS the key (the merge's precedence winner) — read
    # with the UNBOUND dict ops (S3, collision-safe), NEVER the bound .get.
    tier_names = ("box", "workset", "agent", "agent.default", "system", "base")
    key_path = (*sections, leaf)

    def _reads(level: KeyStore, segs: tuple[str, ...]) -> "tuple[bool, object]":
        node: object = level
        for seg in segs:
            if not isinstance(node, KeyStore):
                return (False, None)
            if dict.get(node, seg, _NO_KEY) is _NO_KEY:
                return (False, None)
            node = dict.get(node, seg)
        return (True, node)

    source_tier: str | None = None
    for idx, level in enumerate(levels):
        found, _val = _reads(level, key_path)
        if found:
            source_tier = tier_names[idx] if idx < len(tier_names) else "base"
            break
    if source_tier is None:
        return None  # absent post-reset → nothing effective to name.

    # Read the winning RAW value from the merged snapshot and lenient-expand it.
    snapshot = merge(levels)
    found, raw = _reads(snapshot, key_path)
    if not found or isinstance(raw, (Bind, KeyStore, list)) or raw is None:
        # A bind/subtree/list/present-None has no single scalar to print here.
        return None
    result = expand(snapshot, ctx, collect_errors=True)
    assert isinstance(result, tuple)  # lenient mode → (snapshot, errors)
    resolved_snap, errors = result
    if routed in errors:
        return None  # unresolved post-reset (dangling ref / cycle) — no guess.
    found, eff = _reads(resolved_snap, key_path)
    if not found or isinstance(eff, (Bind, KeyStore, list)) or eff is None:
        return None
    # A stored/resolved empty string has no value to name (Editor NIT-a): render
    # to None → the caller keeps the cleared-only form, never "effective is now
    # <blank>". (``_render_stored_scalar`` already maps "" → None.)
    rendered = _render_stored_scalar(eff)
    if rendered is None:
        return None
    return (rendered, source_tier)


# A private sentinel for _effective_after_reset's unbound-dict presence probe
# (S3): distinct from ``None`` (a present-None leaf is still SET) and from any
# real value. Kept module-local so it is a stable identity across calls.
_NO_KEY: object = object()


def write_system_value(config_path: Path, leaf: str, value: object) -> None:
    """Programmatically write a ``[system] <leaf>`` key to the CONFIG file.

    This is the PROGRAM editing the config file on the user's behalf — it
    bypasses the file-only CLI guard in :func:`set_config_value` (which refuses
    the STRUCTURAL ``system.*`` path-tier family).  Used by ``kanibako setup``
    to record host-global values
    (e.g. ``system.setup_completed`` → ``[system] setup_completed``) that the CLI
    deliberately will not let a user SET directly.

    *leaf* is the bare key name under the ``[system]`` table (NOT prefixed with
    ``system.``).  Writes preserve all other config content (read-modify-write
    via :func:`_write_nested_toml_key`).
    """
    _write_nested_toml_key(config_path, ("system",), leaf, value)


def _count_leaves(node: object) -> int:
    """Count the scalar/leaf entries under a nested-dict *node* (a scope table).

    A ``dict`` recurses; anything else (scalar / list / Bind) is ONE leaf. Used
    so ``reset_all`` reports the real number of overrides it removed when it
    clears a whole nested scope table (residuals item 3).
    """
    if isinstance(node, dict):
        return sum(_count_leaves(v) for v in node.values())
    return 1


def _clear_writable_scope_tables(
    path: Path, command_scope: "ConfigLevel | None",
) -> int:
    """Drop the top-level SCOPE tables *command_scope* is permitted to write from
    *path*, returning the number of leaves removed (residuals item 3).

    ``reset --all`` mirrors a per-key reset over the WHOLE file: a nested scope
    table (``box:`` in a workset file, ``system: auth:`` / ``workset: auth:`` /
    ``box: bindings:`` …) is cleared IFF a single reset of a key in it at this
    command scope would PASS the §0 scope-direction guard — i.e. the table's
    top-level token is in ``_SCOPE_WRITE_ALLOWED[command_scope]`` (the command
    scope's OWN namespace + those it CONTAINS). An UPWARD table (e.g. a hostile
    ``system:`` hand-edited into a box file) is LEFT INTACT — a single reset of
    such a key is refused, so ``--all`` must not clear it either.

    NEVER touched here: ``agent`` (agent-keyed; cleared by the caller's dedicated
    pass, which holds the scopeless ``model``/``continue_mode`` settings),
    ``meta`` (RO identity, §0), and
    non-scope keys (top-level scalars like ``allow_helpers`` — the flat
    ``load_project_overrides`` pass owns those). When *command_scope* is ``None``
    (no scope context) NOTHING is cleared here — the guard cannot be evaluated.
    """
    if command_scope is None or not path.exists():
        return 0
    allowed = _SCOPE_WRITE_ALLOWED.get(command_scope, frozenset())
    data = load_doc(path)
    if not isinstance(data, dict):
        return 0
    removed = 0
    # Iterate a snapshot of the top-level tables. Only SCOPE tokens the command
    # scope may write are candidates; ``agent``/``meta``
    # are excluded by construction (agent is handled elsewhere; meta is
    # never in ``_SCOPE_WRITE_ALLOWED`` — it is not a containment scope).
    for token in list(data):
        if token not in allowed or token == "agent":
            continue
        table = data.get(token)
        if not isinstance(table, dict):
            continue
        removed += _count_leaves(table)
        data.pop(token, None)
    if removed:
        dump_doc(path, data)
    return removed


def reset_all(
    *,
    config_path: Path,
    env_path: Path | None = None,
    force: bool = False,
    system_settings_path: Path | None = None,
    command_scope: "ConfigLevel | None" = None,
) -> str:
    """Remove all overrides at this config level.  Confirms unless *force*.

    *system_settings_path*, when supplied (SYSTEM scope), is where the SETTINGS
    (the ``agent`` table + nested SCOPE tables) are
    cleared from (``@config.settings`` = ``global/settings.yaml``), while CONFIG
    overrides are cleared from ``config_path``.  When None (box/workset)
    everything is cleared from ``config_path`` as before.

    *command_scope* drives the §0 scope-direction guard for the nested SCOPE
    tables (residuals item 3): ``--all`` clears a nested table iff a single reset
    of a key in it at this scope would pass ``_scope_direction_error`` — the
    command scope's OWN namespace + those it CONTAINS; an UPWARD table is left
    intact. When ``None`` the flat/agent/env clears still run (backward
    compatible) but no nested SCOPE table is touched.
    """
    if not force:
        try:
            confirm_prompt("Remove all config overrides? Type 'yes' to proceed: ")
        except UserCancelled:
            return "Aborted."

    count = 0

    # Clear project-level config overrides (always from config_path).
    # Count ONLY what was actually removed (Editor F2): load_project_overrides
    # can report a phantom ``config_paths`` field for any file carrying a
    # [system]/[config] table (KanibakoConfig folds those), and
    # unset_project_config_key returns False when the flat key names no real
    # top-level entry — so an unconditional ``count += 1`` over-reported (a file
    # with only a [system] table said "Reset 1" while removing nothing, and
    # SYSTEM-scope --all could never say "No overrides"). Gate the count on the
    # real removal.
    overrides = load_project_overrides(config_path)
    for key in overrides:
        if unset_project_config_key(config_path, key):
            count += 1

    # Clear target settings.  SYSTEM scope keeps these in
    # the system settings file (settings_dest); box/workset use config_path.
    settings_dest = (
        system_settings_path if system_settings_path is not None else config_path
    )
    if settings_dest.exists():
        data = load_doc(settings_dest)
        agent_tbl = data.get("agent")
        if isinstance(agent_tbl, dict):
            # agent table is agent-keyed: {<agent>: {key: val}}; clear every
            # agent's subsection (the reserved "default" tier included).
            for agent, sec in list(agent_tbl.items()):
                if isinstance(sec, dict):
                    for k in list(sec):
                        _remove_nested_toml_key(settings_dest, ("agent", agent), k)
                        count += 1

    # Clear the nested SCOPE tables the command scope is permitted to write
    # (residuals item 3): the flat ``load_project_overrides`` pass only reaches
    # the ``KanibakoConfig`` dataclass fields, leaving nested scope tables
    # (``<scope>.auth`` / ``box.bindings`` / a downward ``box:`` table in a
    # workset file …) intact. Same file the settings live in (settings_dest —
    # config_path at box/workset, the system settings file at SYSTEM); gated by
    # the §0 containment guard.
    count += _clear_writable_scope_tables(settings_dest, command_scope)

    # Clear env file
    if env_path and env_path.is_file():
        env = read_env_file(env_path)
        if env:
            count += len(env)
            write_env_file(env_path, {})

    return f"Reset {count} override(s)." if count else "No overrides to reset."


def _nested_settings_overrides(path: Path | None) -> dict[str, str]:
    """Flatten a settings file's nested SCOPE tables to ``dotted.key → value``.

    The display companion of the ``_SETTINGS_SCOPE_TOKENS`` routing (F2): a
    ``config set`` at the SYSTEM scope nests scope-token settings (e.g.
    ``system.auth.share_allowed``, downward ``workset.*``/``box.*`` defaults)
    in the system SETTINGS file — entries the flat ``KanibakoConfig`` override
    view cannot see.  Flattens every top-level scope table EXCEPT ``agent``
    (rendered by the agent-settings view).  Bools render lowercase, matching
    ``get``.
    """
    if path is None or not path.exists():
        return {}
    data = load_doc(path)
    if not isinstance(data, dict):
        return {}
    out: dict[str, str] = {}

    def _walk(node: dict, prefix: str) -> None:
        for k, v in node.items():
            if isinstance(v, dict):
                _walk(v, f"{prefix}{k}.")
            elif isinstance(v, bool):
                out[f"{prefix}{k}"] = str(v).lower()
            else:
                out[f"{prefix}{k}"] = str(v)

    for key, val in data.items():
        # ``resource_overrides`` is the LEGACY dead table of the dropped
        # ``resource.*`` surface (spec §3 D-M7): the settable code is gone, so a
        # pre-1.7.x file may still carry an inert table.  Skip it here so it never
        # renders in ``system show``/``--effective`` — display-only, not a revived
        # settable surface.
        if key in ("agent", "resource_overrides") or not isinstance(val, dict):
            continue
        _walk(val, f"{key}.")
    return out


def _pref_overrides(path: Path | None) -> dict[str, str]:
    """Flatten a settings file's ``pref:`` table to ``pref.<target> -> value``.

    ``config show`` must LIST prefs (spec §2h read verbs). The box/workset plain
    view reads ``load_project_overrides`` + ``read_agent_settings``, neither of
    which can see a ``pref:`` table, so it is flattened here with the SAME walk
    ``_nested_settings_overrides`` uses. A present-``None`` request renders as
    ``null`` — it is a REQUEST TO SUPPRESS, and showing it as blank would make
    the one thing a box cannot otherwise express look like nothing at all.
    """
    if path is None or not path.exists():
        return {}
    data = load_doc(path)
    if not isinstance(data, dict):
        return {}
    table = data.get(PREF_ROOT)
    if not isinstance(table, dict):
        return {}
    out: dict[str, str] = {}

    def _walk(node: dict, prefix: str) -> None:
        for k, v in node.items():
            if isinstance(v, dict):
                _walk(v, f"{prefix}{k}.")
            elif isinstance(v, bool):
                out[f"{prefix}{k}"] = str(v).lower()
            elif v is None:
                out[f"{prefix}{k}"] = "null"
            else:
                out[f"{prefix}{k}"] = str(v)

    _walk(table, f"{PREF_ROOT}.")
    return out


def _print_pref_block(snapshot: Any, out: Any) -> None:
    """Render each ``pref`` REQUEST beside the RESULT it produced (spec §2h).

    *"--effective shows BOTH the request and the resulting value — so 'why did
    system.agent resolve to zippity' is answerable from the snapshot instead of
    by reading files. This is what closes the 'I set it and nothing happened'
    failure family."*

    Both halves come off the SAME snapshot, and that is exactly why ``expand``
    carries the ``pref`` subtree through UNEXPANDED: the request is readable in
    the form it was WRITTEN (``@meta.workset.path/tpl``) while the target holds
    the resolved terminal. Rendering an expanded request beside its result would
    print the same string twice and answer nothing.
    """
    from kanibako.settings_store import Bind, KeyStore

    node = snapshot
    for seg in (PREF_ROOT,):
        if not isinstance(node, KeyStore):
            return
        node = dict.get(node, seg, None)
    if not isinstance(node, KeyStore):
        return

    requests: dict[str, Any] = {}

    def _walk(sub: Any, prefix: str) -> None:
        for k in dict.keys(sub):
            v = dict.__getitem__(sub, k)
            if isinstance(v, KeyStore):
                _walk(v, f"{prefix}{k}.")
            else:
                requests[f"{prefix}{k}"] = v

    _walk(node, "")
    if not requests:
        return

    def _render(value: Any) -> str:
        if isinstance(value, Bind):
            opts = f"  [{value.opts}]" if value.opts else ""
            return f"{value.host} -> {value.box}{opts}"
        if value is None:
            return "null"
        return str(value)

    print("", file=out)
    for target in sorted(requests):
        print(f"  {PREF_ROOT}.{target} = {_render(requests[target])}", file=out)
        # The RESULT, read at the TARGET key in the same snapshot.
        cur: Any = snapshot
        missing = False
        for seg in target.split("."):
            if not isinstance(cur, KeyStore) or dict.get(cur, seg, _NO_KEY) is _NO_KEY:
                missing = True
                break
            cur = dict.get(cur, seg)
        if missing:
            # The ordinary present-None rule OMITTED it: a bind / category /
            # masks leaf was suppressed. Saying so is the whole point — this is
            # the difference between "suppressed" and "unset".
            result = "(omitted — the entry is suppressed; no mount)"
        elif cur is None:
            result = "(unset — the consumer applies its default)"
        else:
            result = _render(cur)
        print(f"    -> {target} = {result}", file=out)


def _print_category_block(snapshot: Any, error: str | None, out: Any) -> None:
    """Render the ``--effective`` PATH-DELIVERY block (spec §0; box scope, D6).

    Every CONCRETE binding is listed with the destination it occupies, and every
    ABSTRACT declaration (``common`` / ``caches`` / ``seeded``) is listed with the
    ``meta.derived.<declaration-key>`` binding it produces indented beneath it —
    so a reader can see the declaration AND the mount it becomes, which is the
    whole point of materialising the derivation.

    Both halves are read off the SAME snapshot: the declaration at its own key,
    the derivation at ``meta.derived.<that key>``.  Nothing is re-derived here.
    """
    from kanibako.settings_store import Bind, KeyStore
    from kanibako.settings_views import derived_bindings

    print("", file=out)
    if error is not None:
        for line in error.splitlines():
            print(f"  {line}" if line else "", file=out)
        return

    def _leaf(dotted: str) -> Any:
        node: Any = snapshot
        for seg in dotted.split("."):
            if not isinstance(node, KeyStore):
                return None
            node = dict.get(node, seg, None)
        return node

    def _pair(bind: Bind) -> str:
        opts = f"  [{bind.opts}]" if bind.opts else ""
        return f"{bind.host} -> {bind.box}{opts}"

    # CONCRETE first — the source of truth a mount is emitted from.
    for scope in ("system", "agent", "workset", "box"):
        scope_node = _leaf(scope)
        if not isinstance(scope_node, KeyStore):
            continue
        for tier, prefix in _iter_agent_tiers(scope, scope_node):
            for mode in ("ro", "rw"):
                mode_node = _sub(tier, ("bindings", mode))
                if not isinstance(mode_node, KeyStore):
                    continue
                for name in sorted(dict.keys(mode_node)):
                    bind = dict.__getitem__(mode_node, name)
                    if isinstance(bind, Bind):
                        print(
                            f"  {prefix}.bindings.{mode}.{name} = {_pair(bind)}",
                            file=out,
                        )

    # ABSTRACT declarations, each with the binding it derives.
    #
    # ⚑ The derivation line carries its DELIVERY. ``seeded`` derives a COPY, not
    # a mount (spec §0), and the two are not interchangeable at all: a mount is
    # live and shadows the dest, while a copy runs once at create and is then the
    # box's own file. A reader who cannot tell them apart cannot answer the
    # question this display exists for — WHY is this here, and what happens if I
    # change it.
    derived_node = _leaf("meta.derived")
    if isinstance(derived_node, KeyStore):
        for decl_key, bind in sorted(derived_bindings(derived_node).items()):
            declaration = _leaf(decl_key)
            if isinstance(declaration, Bind):
                print(f"  {decl_key} = {_pair(declaration)}", file=out)
            kind = "copy" if _declaration_delivery(decl_key) == "COPY" else "mount"
            print(
                f"    meta.derived.{decl_key} = {_pair(bind)}  ({kind})",
                file=out,
            )


def _declaration_delivery(decl_key: str) -> str:
    """The COPY/MOUNT delivery of a declaration key, from the category table.

    The category is the segment after the scope, and the AGENT scope is
    DISCRIMINATED — two segments (``agent.<tier>``) where every other scope is
    one. Parsed by position rather than by substring search, so a name that
    happens to spell a category (``box.caches.common``) cannot be misread.

    The delivery itself is read off ``settings_categories._DELIVERY``: it has ONE
    definition, and a display keeping its own copy would drift the moment a
    category moved between COPY and MOUNT.
    """
    from kanibako.settings_categories import _DELIVERY

    parts = decl_key.split(".")
    idx = 2 if parts[0] == "agent" else 1
    category = parts[idx] if len(parts) > idx else ""
    return _DELIVERY.get(category, "MOUNT")


def _iter_agent_tiers(scope: str, scope_node: Any):
    """``(node, key-prefix)`` per DISCRIMINATED tier of *scope*.

    Only the agent scope has tiers (``agent.default`` / ``agent.<agent>``); every
    other scope is itself.  Keeps the display from printing the bare
    ``agent.bindings.*`` form, which is not a key (spec §0 L21).
    """
    from kanibako.settings_store import KeyStore

    if scope != "agent":
        yield scope_node, scope
        return
    for tier in sorted(dict.keys(scope_node)):
        tier_node = dict.__getitem__(scope_node, tier)
        if isinstance(tier_node, KeyStore):
            yield tier_node, f"agent.{tier}"


def _sub(node: Any, path: "tuple[str, ...]") -> Any:
    """Walk *path* under *node* with unbound ``dict`` ops (S3); ``None`` if absent."""
    from kanibako.settings_store import KeyStore

    cur: Any = node
    for seg in path:
        if not isinstance(cur, KeyStore):
            return None
        cur = dict.get(cur, seg, None)
    return cur


def show_config(
    *,
    global_config_path: Path,
    config_path: Path | None = None,
    env_global: Path | None = None,
    env_project: Path | None = None,
    effective: bool = False,
    file: Any = None,
    workset_path: Path | None = None,
    agent_state: dict[str, str] | None = None,
    env_resolved: dict[str, str] | None = None,
    system_settings_path: Path | None = None,
    category_snapshot: Any = None,
    category_error: str | None = None,
) -> int:
    """Display config values.  Returns exit code.

    - *effective=False*: show only overrides at this level.
    - *effective=True*: show all resolved values including inherited defaults.

    *category_snapshot* (BOX scope, ``--effective`` only) is the resolved launch
    KeyStore.  When supplied, the PATH-DELIVERY categories are rendered too: each
    binding, and each ABSTRACT declaration paired with the ``meta.derived.*``
    binding it produces (spec §0 — "``--effective`` shows BOTH the declaration and
    the derived binding and a user can see WHY a mount exists").  *category_error*
    carries a collision message when the snapshot could not be resolved, so
    ``config show --effective`` REPORTS an M-7 collision rather than dying on it —
    it is the migration's own detection recipe.

    ⚑ ONE SCOPE. The workset / system / agent ``config show --effective`` verbs
    still render no category key at all: that display predates the keystore and
    reads ``load_merged_config``.  Extending it across all five scopes is a
    read-surface job with its own owner, not a side effect of this one.

    *system_settings_path*, when supplied (SYSTEM scope), is the file the agent
    SETTINGS + ``system.default_agent`` are DISPLAYED from (``@config.settings``
    = ``global/settings.yaml``); the ``system.*`` CONFIG display always uses
    ``global_config_path``.  When None (box/workset) settings display reads
    ``config_path`` as before.
    """
    out = file or sys.stdout
    # The file agent SETTINGS are read from for display: system settings file for
    # the SYSTEM scope, else the level's own config_path (box/workset).
    settings_src = (
        system_settings_path if system_settings_path is not None else config_path
    )

    if effective:
        # Show all resolved values
        cfg = load_merged_config(
            global_config_path, config_path, workset_path=workset_path,
        )
        overrides = load_project_overrides(config_path) if config_path else {}
        for fld in fields(cfg):
            val = getattr(cfg, fld.name)
            marker = " (override)" if fld.name in overrides else ""
            print(f"  {fld.name} = {val}{marker}", file=out)

        # Agent settings.  When a fully-resolved agent_state is supplied (box
        # view), render it; mark only the keys actually set at the box level.
        # Otherwise fall back to the project-level overrides (today's behavior).
        if agent_state is not None:
            proj_agent = (
                read_agent_settings(settings_src, "default")
                if settings_src and settings_src.exists()
                else {}
            )
            if agent_state:
                print("", file=out)
                for k, v in sorted(agent_state.items()):
                    marker = " (override)" if k in proj_agent else ""
                    print(f"  {k} = {v}{marker}", file=out)
        elif settings_src and settings_src.exists():
            settings = read_agent_settings(settings_src, "default")
            if settings:
                print("", file=out)
                for k, v in sorted(settings.items()):
                    print(f"  {k} = {v} (override)", file=out)

        # SYSTEM scope: nested settings-tier entries in the system settings
        # file (``system.auth.share_allowed``, downward scope defaults) — the
        # values a system-scope ``set`` stores and the launch cascade reads
        # (F2: the effective view must show what set wrote).
        if system_settings_path is not None:
            nested = _nested_settings_overrides(system_settings_path)
            if nested:
                print("", file=out)
                for k in sorted(nested):
                    print(f"  {k} = {nested[k]}", file=out)

        # ``pref`` REQUESTS + the RESULT each produced (spec §2h read verbs).
        if category_snapshot is not None:
            _print_pref_block(category_snapshot, out)

        # Path-delivery CATEGORIES + their materialised derivations (§0).
        if category_error is not None or category_snapshot is not None:
            _print_category_block(category_snapshot, category_error, out)

        # Env vars.  Prefer the fully-resolved env (box view) when supplied.
        merged = (
            env_resolved
            if env_resolved is not None
            else merge_env(env_global, env_project)
        )
        if merged:
            print("", file=out)
            for k in sorted(merged):
                print(f"  env.{k} = {merged[k]}", file=out)

    else:
        # Show only overrides
        has_output = False

        overrides = load_project_overrides(config_path) if config_path else {}
        for k, v in sorted(overrides.items()):
            print(f"  {k} = {v}", file=out)
            has_output = True

        if settings_src and settings_src.exists():
            settings = read_agent_settings(settings_src, "default")
            for k, v in sorted(settings.items()):
                print(f"  {k} = {v}", file=out)
                has_output = True

        # SYSTEM scope: nested settings-tier overrides (see the effective
        # branch) — they ARE overrides at this level, so the plain view shows
        # them too.
        if system_settings_path is not None:
            nested = _nested_settings_overrides(system_settings_path)
            for k, v in sorted(nested.items()):
                print(f"  {k} = {v}", file=out)
                has_output = True

        # ``pref`` REQUESTS stored at this noun (spec §2h "config show lists
        # prefs"). They ARE overrides at this level, so the plain view shows them.
        for k, v in sorted(_pref_overrides(config_path).items()):
            print(f"  {k} = {v}", file=out)
            has_output = True

        # Env vars (project-level only)
        if env_project:
            env = read_env_file(env_project)
            for k in sorted(env):
                print(f"  env.{k} = {env[k]}", file=out)
                has_output = True

        if not has_output:
            print("  (no overrides)", file=out)

    return 0


# ---------------------------------------------------------------------------
# Config section helpers (load → mutate → dump as YAML)
# ---------------------------------------------------------------------------

def _write_toml_key(path: Path, section: str, key: str, value: object) -> None:
    """Write a key to a specific config section, preserving other content."""
    data = load_doc(path)
    sec = data.get(section)
    if not isinstance(sec, dict):
        sec = {}
        data[section] = sec
    sec[key] = value
    dump_doc(path, data)


def _write_toml_key_root(path: Path, key: str, value: object) -> None:
    """Write a TOP-LEVEL scalar key, preserving other content.

    Used for flat KanibakoConfig fields (e.g. ``allow_helpers``) that live at
    the document root, not under a section.
    """
    data = load_doc(path)
    data[key] = value
    dump_doc(path, data)


def _remove_toml_key_root(path: Path, key: str) -> bool:
    """Remove a TOP-LEVEL scalar key.  Returns True if it was present."""
    if not path.exists():
        return False
    data = load_doc(path)
    if key not in data:
        return False
    del data[key]
    dump_doc(path, data)
    return True


def _remove_toml_key(path: Path, section: str, key: str) -> bool:
    """Remove a key from a specific config section.  Returns True if found."""
    if not path.exists():
        return False

    data = load_doc(path)
    sec = data.get(section, {})
    if not isinstance(sec, dict) or key not in sec:
        return False

    del sec[key]
    if not sec:
        del data[section]
    dump_doc(path, data)
    return True


def _write_nested_toml_key(
    path: Path, sections: tuple[str, ...], key: str, value: object,
) -> None:
    """Write *key* into a nested table (e.g. ``("system", "path")``).

    Preserves other content; creates intermediate tables as needed.
    """
    data = load_doc(path)
    node = data
    for sec in sections:
        child = node.get(sec)
        if not isinstance(child, dict):
            child = {}
            node[sec] = child
        node = child
    node[key] = value
    dump_doc(path, data)


def _remove_nested_toml_key(
    path: Path, sections: tuple[str, ...], key: str,
) -> bool:
    """Remove *key* from a nested table.  Returns True if found.

    Prunes now-empty intermediate tables.
    """
    if not path.exists():
        return False

    data = load_doc(path)

    # Walk to the innermost table, recording the chain for pruning.
    chain: list[dict] = [data]
    node = data
    for sec in sections:
        if sec not in node or not isinstance(node[sec], dict):
            return False
        node = node[sec]
        chain.append(node)

    if key not in node:
        return False
    del node[key]

    # Prune empty tables bottom-up.
    for i in range(len(sections) - 1, -1, -1):
        if not chain[i + 1]:
            del chain[i][sections[i]]
        else:
            break
    dump_doc(path, data)
    return True
