"""The CLI-facing config KEY TAXONOMY — what a key is, and where it lives.

**_Terminology_**
- _family_: a CLI-surface key SHAPE recognised by SPELLING (``pref.<target>``,
  ``agent.<node>.<leaf>``, ``<scope>.<category>.<name>``, ``<scope>.secret_path.<VAR>``, the bare
  agent behaviour keys, the routed scalars, the structural ``system.*`` path tier)
- _route_: the ``(sections, leaf)`` nested slot in a settings file a key is stored at
- _refusal_: the ``Error: …`` string a verb returns for a key it will not serve

⚑ NOT THE KEYSPACE VALIDATOR, and must never become a second one: declaredness has ONE authority,
:mod:`kanibako.settings.settings_keyspace`. This module classifies spellings only.
⚑ Layering: BELOW ``config_interface`` and ``config_dest`` — it must not import either.
"""

from __future__ import annotations

import re
from enum import Enum
from pathlib import Path  # noqa: F401  (annotations)

from kanibako.agent_ref import canonicalize_agent_ref, display_agent_ref
from kanibako.errors import ConfigError
from kanibako.settings.config import coerce_bool
from kanibako.settings.kb_store import SCOPE_CONTAINMENT
from kanibako.settings.settings_keyspace import (
    ACCESS_DEFAULT,
    ACCESS_TIERS,
    leaf_name_reason,
)
from kanibako.settings.settings_prefs import PREF_ROOT


class ConfigLevel(Enum):
    """Which scope a config operation targets."""

    box = "box"
    workset = "workset"
    agent = "agent"
    system = "system"

# ---------------------------------------------------------------------------
# ⚑⚑⚑ QUARANTINE — THIS SET IS NOT THE MODEL OF WHAT A KEY IS. It is a
# HAND-MAINTAINED, DELIBERATELY INCOMPLETE "key or project name?" list (Jei's
# 2026-08-08 multi-faceted-key ruling); its known-wrong messages are KNOWN.
# ⚑⚑ DO NOT derive it from the declaration SoT — proposed and DECLINED.
# ⚑ The block travels with the set; do not copy the pattern elsewhere. Ruling,
# cost, and the seven False answers: llm-docs/kanibako/settings/config_keys.py.md.
# ---------------------------------------------------------------------------
# The set itself: if a positional arg matches one of these, it is treated as a
# GET request rather than a project name.
KNOWN_CONFIG_KEYS: frozenset[str] = frozenset({
    # Agent flags
    "model",
    # allow_helpers: agent-scope BEHAVIOR key; gates the launch helper hub (spec §2d).
    "allow_helpers",
    # access: the agent-scope PERMISSION TIER, enum ``restricted|editing|full`` (spec §2d).
    "access",
    # endpoint (persona): alternate harness base-URL, a sibling of model (block B).
    "endpoint",
    # bootstrap: agent-scope BEHAVIOR key naming the in-box multiplexer (spec §2d).
    "bootstrap",
    # continue_mode: agent-scope BEHAVIOR key; the persisted continue-vs-fresh fallback (spec §2d).
    "continue_mode",
    # Box.  ⚑ NO ``box.agent_name`` (P7): the agent SELECTION is the §2h request
    # ``pref.system.agent`` (spec §2b RETIRED the box key).
    "box.image",
    "box.share_images",
    # box.images_store: host image-store root behind the shared-images bind (spec §2b).
    "box.images_store",
    "box.shell",
    # Auth sharing — settable 3-tier chain (system/workset/box.auth.*)
    "system.auth.share_allowed",
    "workset.auth.share_allowed",
    "workset.auth.global_sync",
    "box.auth.global_enabled",
    "box.auth.workset_enabled",
    # ⚑ NO ``mode`` key (block B1: the mode is the RO anchor ``meta.box.mode``) and NO bare
    # ``vault.ro``/``vault.rw`` — both RETIRED, not aliased. See llm-docs.
    "box.enable_vault",
    # Per-workset registry location (settings-conformance P3); a settable STRING path.
    "workset.registry",
    # Workset-scope LAYOUT anchors (P6a): per-workset REPOINTABLE dirs, STRING paths.
    "workset.auth.path",
    "workset.boxes",
    "workset.vault_ro",
    "workset.vault_rw",
    "workset.logs",
    # workset.workspaces / workset.channelroot: the two RESOLVED workset dir keys (§3.3).
    "workset.workspaces",
    "workset.channelroot",
    # ⚑ THE ``workset.channels.*`` FAMILY, WHOLE — all six declared leaves (spec §2c;
    # ``DECLARED_WORKSET_CHANNEL_LEAVES``), STRING paths, one nested slot. Three were
    # missing until 2026-08-09; see ``llm-docs/kanibako/settings/config_keys.py.md``.
    "workset.channels.common",
    "workset.channels.chat",
    "workset.channels.share",
    "workset.channels.broadcast",
    "workset.channels.mailboxes",
    "workset.channels.share_global",
    # Per-workset template SOURCE, read by the layer-3 seed (template-trio, spec §2c).
    "workset.template",
    # Per-workset CANON CONTRIBUTION root, the ``canon_hb_workset`` bind SOURCE (spec §2c).
    "workset.canon",
    # Per-BOX canon contribution root (spec §2b). ⚑ ``@box.canon`` is NOT ``~/canon`` — llm-docs.
    "box.canon",
    # Workset kuid + advisory-check toggle (settings-conformance P6d).
    "workset.kuid",
    "workset.skip_kuid_check",
    # Layer-1 CONFIG-key foundation (bootstrap paths; ``[config]`` table, spec §1)
    "config.data",
    "config.settings",
    "config.agents",
    "config.primary_workset",
    "config.registry",
    # config.journal: the lifecycle-journal location, recognised for sibling parity (§3.3).
    "config.journal",
    # Layer-2 system.* path SETTINGS (``[system]`` table, spec §2g).  ``global``
    # is ELIMINATED (children inline ``@config.data/global/...``).
    "system.backup",
    "system.channelroot",
    # M-11: ``system.base_template`` → ``system.template``, RETIRED not aliased.
    "system.template",
    "system.canon",
    "system.cache",
    "system.runtime",
    # system.agent (spec §2g): the CURRENT agent's name — a system-scope SETTING, so it
    # routes to the ``system:`` table of the SYSTEM SETTINGS file, not the [system] config table.
    "system.agent",
})

# The RETIRED bare env-var prefix (R-39, spec §2a), kept ONLY so the spelling stays
# RECOGNISED as key-shaped and the verbs can refuse it with a cure.
DYNAMIC_PREFIXES: tuple[str, ...] = ("env.",)

# ---------------------------------------------------------------------------
# Typed writer routing table (the H1/H2 core): the single source of truth for HOW
# every non-dynamic, non-env config key is stored — canonical key → the nested
# config location ``(sections_tuple, leaf_name)`` it lands in.
# ---------------------------------------------------------------------------
_KEY_ROUTES: dict[str, tuple[tuple[str, ...], str]] = {
    # Box section ([box] table).
    "box.image": (("box",), "image"),
    "box.shell": (("box",), "shell"),
    "box.share_images": (("box",), "share_images"),
    # box.images_store (B3): the ``box:`` table nested slot, as ``box.image``.
    "box.images_store": (("box",), "images_store"),
    # ``system.agent`` (the agent SELECTION default, spec §2g) and the settable 3-tier
    # auth chain: ordinary SETTINGS keys, each in its own nested scope slot.
    "system.agent": (("system",), "agent"),
    "system.auth.share_allowed": (("system", "auth"), "share_allowed"),
    "workset.auth.share_allowed": (("workset", "auth"), "share_allowed"),
    "workset.auth.global_sync": (("workset", "auth"), "global_sync"),
    "box.auth.global_enabled": (("box", "auth"), "global_enabled"),
    "box.auth.workset_enabled": (("box", "auth"), "workset_enabled"),
    # ⚑ ``box.enable_vault`` is the vault key (P2 clean break); the bare ``vault.*``
    # keys and ``mode`` are RETIRED, never routed. See llm-docs.
    "box.enable_vault": (("box",), "enable_vault"),
    # ``workset.registry`` (P3): the ``workset:`` table nested slot; a STRING path.
    "workset.registry": (("workset",), "registry"),
    # Workset-scope LAYOUT anchors (P6a): the ``workset.<...>`` nested slots, STRING paths.
    "workset.auth.path": (("workset", "auth"), "path"),
    "workset.boxes": (("workset",), "boxes"),
    "workset.vault_ro": (("workset",), "vault_ro"),
    "workset.vault_rw": (("workset",), "vault_rw"),
    "workset.logs": (("workset",), "logs"),
    # workset.workspaces / workset.channelroot (§3.3, bifrost A1): the ``workset:`` slot.
    "workset.workspaces": (("workset",), "workspaces"),
    "workset.channelroot": (("workset",), "channelroot"),
    # The whole six-leaf ``channels`` family, one slot rule (see KNOWN_CONFIG_KEYS).
    "workset.channels.common": (("workset", "channels"), "common"),
    "workset.channels.chat": (("workset", "channels"), "chat"),
    "workset.channels.share": (("workset", "channels"), "share"),
    "workset.channels.broadcast": (("workset", "channels"), "broadcast"),
    "workset.channels.mailboxes": (("workset", "channels"), "mailboxes"),
    "workset.channels.share_global": (("workset", "channels"), "share_global"),
    # Per-workset template SOURCE (template-trio, spec §2c; Q3): the layer-3 seed source.
    "workset.template": (("workset",), "template"),
    # The per-scope CANON CONTRIBUTION roots (spec §2c/§2b); STRING paths.
    "workset.canon": (("workset",), "canon"),
    "box.canon": (("box",), "canon"),
    # Workset kuid + advisory-check toggle (P6d): the ``workset:`` table slot.
    "workset.kuid": (("workset",), "kuid"),
    "workset.skip_kuid_check": (("workset",), "skip_kuid_check"),
    # ⚑ ``allow_helpers`` is NOT routed here — it is an agent-keyspace key (spec §2d).
}

# Keys whose values must be coerced to a real type before writing (the H2 fix).
# ⚑ The agent-scope scalars are NOT here, and ``access`` is an ENUM guarded by
# :func:`access_value_error`, never a coercion. See llm-docs.
KEY_TYPES: dict[str, str] = {
    "box.share_images": "bool",
    "system.auth.share_allowed": "bool",
    "workset.auth.share_allowed": "bool",
    "workset.auth.global_sync": "bool",
    "box.auth.global_enabled": "bool",
    "box.auth.workset_enabled": "bool",
    "box.enable_vault": "bool",
    "workset.skip_kuid_check": "bool",
}

def _coerce_value(canonical: str, value: "str | None") -> object | str | None:
    """Coerce *value* to the typed form declared for *canonical* in KEY_TYPES."""
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

# The recognized SCOPE namespaces a key may live in (its TOP-LEVEL dotted token);
# a key whose first segment is not one of these is SCOPELESS and unguarded.
_SCOPE_NAMESPACES: frozenset[str] = frozenset({
    "system", "agent", "workset", "box", "config", "meta",
})

# The module-local alias of the CONTAINMENT order (spec §0): ``system ⊃ agent ⊃
# workset ⊃ box``, OUTERMOST first — declared in ``kb_store``, the stack leaf.
_SCOPE_CONTAINMENT: tuple[str, ...] = SCOPE_CONTAINMENT

# Which key-scope namespaces a COMMAND scope may WRITE (spec §0 + §2a): its OWN
# namespace plus every scope it CONTAINS, derived as a TAIL-SLICE of the order.
_SCOPE_WRITE_ALLOWED: dict[ConfigLevel, frozenset[str]] = {
    level: frozenset(_SCOPE_CONTAINMENT[_SCOPE_CONTAINMENT.index(level.value):])
    for level in ConfigLevel
}

# The scope tokens whose prefixed keys are SETTINGS keys stored in a SETTINGS file.
# ⚑ ``system`` is INCLUDED (F2 fix) — a routed ``system.*`` SETTINGS key lands in the
# system settings file, never the Layer-1 kanibako_config.yaml.
_SETTINGS_SCOPE_TOKENS: frozenset[str] = frozenset(_SCOPE_CONTAINMENT)


def _scope_direction_error(
    canonical: str, command_scope: "ConfigLevel | None"
) -> str | None:
    """Enforce the §0 directional-WRITE rule for ``config set`` (block B4)."""
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


# Reverse of _dot_to_flat: the CLI also accepts the flat underscore spelling
# (``box_image``), normalised here so every verb hits the SAME _KEY_ROUTES entry.
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
    """Return the canonical config key for a user-supplied key name."""
    # ⚑ The bind arm is matched BEFORE the persona form; both canonicalize the node
    # segment as a WHOLE. Order and reasons: llm-docs.
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

# The settable per-persona agent leaves: the FLAT agent-state knobs plus the
# ``env.`` section — the EXACT shape ``agent_file.load`` reads back.
_PERSONA_STATE_LEAVES: frozenset[str] = frozenset(
    {
        "endpoint", "model", "continue_mode", "access", "allow_helpers",
        "bootstrap",
        # Per-agent template SOURCE, read by the layer-2 seed (spec §2a/§2d).
        "template",
        # Per-agent CANON CONTRIBUTION root, the ``canon_hb_agent`` bind SOURCE (spec §2d).
        "canon",
    }
)
_PERSONA_ENV_SECTIONS: frozenset[str] = frozenset({"env"})

# The RESERVED any-agent tier name ("no real agent may be named default"); it is
# NOT a persona node, so an ``agent.default.<key>`` write is refused.
AGENT_DEFAULT_SUB = "default"


def _parse_persona_agent_key(key: str) -> "tuple[str, str] | None":
    """Split an ``agent.<node>.<tail>`` persona key into ``(node_raw, tail)``."""
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
    """True iff *canonical* is the auth-critical ``access`` permission key."""
    if canonical == "access":
        return True
    parsed = _parse_persona_agent_key(canonical)
    return parsed is not None and parsed[1] == "access"


def access_value_error(canonical: str, value: str) -> str | None:
    """The LOUD refusal for an illegal ``access`` value, or ``None`` when legal."""
    # ⚑ EXACT match, no case folding: the launch resolver reads it back exactly.
    if value in ACCESS_TIERS:
        return None
    legal = " | ".join(ACCESS_TIERS)
    return (
        f"Error: {canonical} must be one of {legal} (spec §2d); got {value!r}. "
        f"An unrecognised permission tier is REFUSED, never treated as "
        f"'{ACCESS_DEFAULT}'."
    )

# ---------------------------------------------------------------------------
# Per-node DESCRIPTOR bind keys (item-0) — ``agent.<node>.bindings.{ro,rw}.<name>``.
# ⮕ The CLI WRITE route is RETIRED (R-9); the key stays declared, hand-authorable
# in the node's settings file, delivered at launch, and READABLE via ``config get``.
# ---------------------------------------------------------------------------

# ``agent.<node>.bindings.{ro,rw}.<name>`` — the per-node descriptor delivery bind;
# ``<node>`` is NON-greedy so the FIRST category segment splits node from name.
# ⚑ THIS IS THE AGENT-SCOPE READ PARSER, NOT THE RECOGNISER (that is
# ``settings_categories.AGENT_BIND_KEY_RE``, which covers all six categories).
# ⚑⚑ DO NOT WIDEN IT to the other four — there is nothing to widen it TO, and a
# widened parser would invent a read for a spelling the keyspace refuses. See llm-docs.
_AGENT_NODE_BIND_RE = re.compile(
    r"^agent\.(?P<node>.+?)\.(?P<cat>bindings\.(?:ro|rw))\.(?P<name>.+)$"
)


def parse_agent_node_bind_key(key: str) -> "tuple[str, str, str] | None":
    """Split ``agent.<node>.bindings.{ro,rw}.<name>`` into ``(node_raw, cat, name)``."""
    m = _AGENT_NODE_BIND_RE.match(key)
    if m is None:
        return None
    return m.group("node"), m.group("cat"), m.group("name")


def _is_agent_node_bind_key(key: str) -> bool:
    """True iff *key* is a per-node descriptor bind ``agent.<node>.bindings.*`` key (item-0)."""
    # ⚑ The CLI WRITE half is RETIRED (R-9); recognition, the ``config get`` route
    # and the persona-branch guard are all still live. Checked BEFORE the persona form.
    return parse_agent_node_bind_key(key) is not None

# ``agent.<node>.secret_path.<VAR>`` — the per-node SECRET category (spec §2a):
# DISCRIMINATED, stored in the node's OWN settings file, value a SCALAR host PATH.
# ``<node>`` is NON-greedy so the FIRST ``.secret_path.`` splits node from VAR.
_AGENT_NODE_SECRET_RE = re.compile(
    r"^agent\.(?P<node>.+?)\.secret_path\.(?P<var>[A-Za-z_][A-Za-z0-9_]*)$"
)


def _parse_agent_node_secret_key(key: str) -> "tuple[str, str] | None":
    """Split ``agent.<node>.secret_path.<VAR>`` into ``(node_raw, var)``, or ``None``."""
    m = _AGENT_NODE_SECRET_RE.match(key)
    if m is None:
        return None
    return m.group("node"), m.group("var")


def _is_agent_node_secret_key(key: str) -> bool:
    """True iff *key* is a per-node ``agent.<node>.secret_path.<VAR>`` key (SECRET category)."""
    return _parse_agent_node_secret_key(key) is not None

def _persona_display_key(canonical: str) -> str:
    """Render a canonical persona key for USER-FACING output (``℘`` -> ``+``)."""
    parsed = _parse_persona_agent_key(canonical)
    if parsed is None:
        return canonical
    node, tail = parsed
    return f"agent.{display_agent_ref(node)}.{tail}"


def _node_secret_display_key(canonical: str) -> str:
    """Render a canonical ``…secret_path.<VAR>`` key for USER-FACING output (``℘`` -> ``+``)."""
    parsed = _parse_agent_node_secret_key(canonical)
    if parsed is None:
        return canonical
    node, var = parsed
    return f"agent.{display_agent_ref(node)}.secret_path.{var}"

# ⚑ ``_floor_bind_display`` USED TO LIVE HERE and was DELETED with the whole
# set-time floor thread (R-9). Do not reintroduce it — llm-docs.


def _is_bare_env_key(key: str) -> bool:
    """The RETIRED bare docker-``.env`` spelling ``env.<VAR>`` (R-39, spec §2a)."""
    # ⚑ RECOGNISE-TO-REFUSE ONLY; the live family is the SCOPED ``<scope>.env.<VAR>``.
    return key.startswith("env.")


# ``<scope>.env.<VAR>`` for the non-agent scopes (system/workset/box) — the LIVE env
# family (spec §2a); the agent form is DISCRIMINATED and routed elsewhere.
# ⚑ The SIBLING-EXACT twin of ``_SCOPE_SECRET_RE``; VAR matching is CASE-SENSITIVE.
_SCOPE_ENV_RE = re.compile(
    r"^(?P<scope>system|workset|box)\.env\.(?P<var>[A-Za-z_][A-Za-z0-9_]*)$"
)


def _is_scope_env_key(key: str) -> bool:
    """True iff *key* is a NON-agent ``<scope>.env.<VAR>`` key (system/workset/box)."""
    # ⚑ SHAPE only — the §0 RESERVED-NAME floor is enforced by :func:`scope_env_var_error`.
    return _SCOPE_ENV_RE.match(key) is not None


def scope_env_var_error(canonical: str) -> str | None:
    """Refuse a ``<scope>.env.<VAR>`` WRITE whose VAR is a RESERVED name (spec §0)."""
    # ⚑ Gates itself — ``None`` for every non-scope-env key, so verbs apply it always.
    m = _SCOPE_ENV_RE.match(canonical)
    if m is None:
        return None
    reason = leaf_name_reason(m.group("var"))
    return None if reason is None else f"Error: {reason}."


def bare_env_retired_error(
    key: str, *, verb: str, command_scope: "ConfigLevel | None" = None,
) -> str | None:
    """The refusal + cure for a RETIRED bare ``env.<VAR>`` op (R-39, spec §2a)."""
    # ⚑ *verb* is REQUIRED; gates itself — ``None`` for every non-bare-env key.
    if not _is_bare_env_key(key):
        return None
    var = key[len("env."):]
    # ⚑ The AGENT scope is DISCRIMINATED (spec §0) — ``agent.env.<VAR>`` is NOT a key,
    # so this arm must name the ``<agent>`` placeholder form, never ``command_scope.value``
    # (that would hand the user a SECOND illegal spelling). Full reason: llm-docs.
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
    """The RETIRED box-scoped agent mirror ``box.agent.<key>`` (spec §2b)."""
    # ⚑ RECOGNISE-TO-REFUSE ONLY (P7); the replacement is the §2h request.
    return key.startswith("box.agent.")


def box_agent_retired_error(
    canonical: str, *, verb: str, active_agent: str | None = None,
) -> str:
    """The refusal + cure for a RETIRED ``box.agent.<key>`` op (P7, spec §2b)."""
    # ⚑ The pointer names what ``--effective`` ACTUALLY RENDERS; do not promise
    # ``meta.box.agent.<key>``, which no renderer emits today.
    tail = canonical[len("box.agent."):]
    agent = active_agent or "<agent>"
    return (
        f"Error: '{canonical}' is RETIRED — a box no longer carries a settable "
        f"mirror of its agent's settings (spec §2b). Tweak the agent for THIS box "
        f"with the request '{verb} pref.agent.{agent}.{tail}' (spec §2h); "
        f"'kanibako box config --effective' then shows that request beside the "
        f"value it produced."
    )

# The command scopes that CANNOT write a BARE agent behavior key — a bare write from
# either is UPWARD and is DROPPED at launch. They differ in the CURE (see llm-docs).
_NO_BARE_AGENT_KEY_SCOPES: "frozenset[ConfigLevel]" = frozenset(
    {ConfigLevel.box, ConfigLevel.workset}
)


def box_agent_redirect_key(
    canonical: str,
    command_scope: "ConfigLevel | None",
    active_agent: str | None = None,
) -> str | None:
    """The ``pref.agent.<active>.<key>`` request a BARE agent key redirects to at BOX scope."""
    # ⚑ Fires ONLY for the bare form at BOX scope; *active_agent* is REQUIRED because
    # the §2h request targets a DISCRIMINATED agent slot (§0). See llm-docs.
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
    """Refuse a WRITE-shaped op on a BARE agent behavior key at box / workset scope."""
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
    # ⚑ WORKSET keeps the PLACEHOLDER on purpose: naming one box's agent would be a lie.
    return (
        f"Error: agent settings can't be {verb} at workset scope (a workset spans "
        f"multiple boxes/agents, so there's no single agent to configure). "
        f"Configure them at system scope to apply to all agents, or per-box via "
        f"'pref.agent.<agent>.{canonical}' (spec §2h)."
    )

#: ⚑ ``_agent_scope_node`` IS GONE (DS-BL1 = (a)) — no bind-shaped category reaches a
#: set any more, so it answered a question nobody asks. Do not restore it (llm-docs).


def is_known_key(arg: str) -> bool:
    """Return True if *arg* looks like a config key (not a project name)."""
    # ⚑ It answers False for SEVEN DECLARED KEYS on purpose — see the QUARANTINE
    # block above :data:`KNOWN_CONFIG_KEYS`. Every branch below is KEY-SHAPED
    # recognition only: a retired spelling must be refused by name, never read as a
    # project name. Per-branch reasons: llm-docs.
    if arg in KNOWN_CONFIG_KEYS:
        return True
    # Bare env.<VAR> — RETIRED (R-39), recognised so the verbs can refuse it.
    if any(arg.startswith(p) for p in DYNAMIC_PREFIXES):
        return True
    # pref.<target-key> — the §2h REQUEST family; SHAPE-only here.
    if _is_pref_key(arg):
        return True
    # agent.<node>.bindings.{ro,rw}.<name> — item-0; write route RETIRED (R-9), read lives.
    if _is_agent_node_bind_key(arg):
        return True
    # agent.<node>.secret_path.<VAR> and the NON-agent <scope>.secret_path.<VAR>.
    if _is_agent_node_secret_key(arg) or _is_scope_secret_key(arg):
        return True
    # <scope>.env.<VAR> (system/workset/box) — the LIVE env family (spec §2a).
    if _is_scope_env_key(arg):
        return True
    # agent.<node>.<key> — the per-persona agent key (block B1).
    if _is_persona_agent_key(arg):
        return True
    # box.agent.<key> — the box-scoped agent mirror (block B5, spec §2b).
    if _is_box_agent_key(arg):
        return True
    # {system,workset,box}.<bind-shaped category>.<name> — the RETIRED scope-level route.
    if _is_scope_bind_key(arg):
        return True
    # agent.<node>.<bind-shaped category>.<name> — the AGENT spelling, retired the same way.
    # ⚑ Must NOT be ``_is_path_category_key``: that predicate fails closed for every key
    # since 2026-08-08c and silently took this arm down with it.
    return _is_agent_scope_bind_key(arg)

def is_system_path_key(key: str) -> bool:
    """Keys that belong in the bootstrap config file's PATH tables (file-only)."""
    # ⚑ A PRECISE family membership check, NOT a ``system.*``-wide catch-all (F2/F3):
    # a ``system.*`` SETTINGS key is not this family. See llm-docs.
    if key.startswith("config."):
        # Still consulted on the READ/show path; set/reset short-circuit earlier (B2).
        return True
    if not key.startswith("system."):
        return False
    if key == "system.setup_completed":
        return True
    # Lazy import (config_interface ↔ paths would cycle at module load).
    from kanibako.settings.paths import SYSTEM_PATH_DEFAULTS

    return key in SYSTEM_PATH_DEFAULTS


def _user_config_file_str() -> "Path | str":
    """The RESOLVED user bootstrap config file, for refusal messages."""
    # ⚑ ERROR path — it must never itself raise; the literal default is the fallback.
    from kanibako.settings.config import config_file_path
    from kanibako.settings.paths import xdg

    try:
        return config_file_path(xdg("XDG_CONFIG_HOME", ".config"))
    except Exception:
        return "~/.config/kanibako_config.yaml"


def system_key_refusal(key: str, *, verb: str) -> str:
    """Refuse a CLI *verb* (``set``/``reset``/``read``) on a FILE-ONLY ``system.*`` key."""
    # ⚑ *verb* is REQUIRED, and the READ tail omits ``setup`` (a WRITE cure). Why
    # both: ``llm-docs/kanibako/settings/config_keys.py.md``.
    if verb == "read":
        return (
            f"Error: '{key}' is a structural config key and cannot be read from "
            f"the CLI. Its value lives in the config file:\n  {_user_config_file_str()}"
        )
    return (
        f"Error: '{key}' is a structural config key and cannot be {verb} from "
        f"the CLI. Edit the config file directly:\n  {_user_config_file_str()}\n"
        f"(or re-run 'kanibako setup')."
    )


def _config_key_refusal(canonical: str, *, action: str) -> str:
    """Error string refusing a CLI set/reset of a ``config.*`` foundation key."""
    # ⚑ The message deliberately does NOT mention ``setup`` — it is not how a
    # ``config.*`` value is set. Rationale (Jei, load-bearing): llm-docs.
    config_file = _user_config_file_str()
    verb = "changed" if action == "reset" else "set"
    return (
        f"Error: config.* keys can only be {verb} by editing the configuration "
        f"file ({config_file})."
    )

# ``<scope>.secret_path.<VAR>`` for the NON-agent scopes (system/workset/box); the
# agent form is DISCRIMINATED and routed to the node file elsewhere.
_SCOPE_SECRET_RE = re.compile(
    r"^(?P<scope>system|workset|box)\.secret_path\.(?P<var>[A-Za-z_][A-Za-z0-9_]*)$"
)


def _is_scope_secret_key(key: str) -> bool:
    """True iff *key* is a NON-agent ``<scope>.secret_path.<VAR>`` SECRET-category key."""
    return _SCOPE_SECRET_RE.match(key) is not None

def _is_pref_key(key: str) -> bool:
    """True iff *key* is a ``pref.<target-key>`` REQUEST key (spec §2h)."""
    # ⚑ SHAPE ONLY — the real §2h validation runs in the set / get / reset branches.
    return key.startswith(f"{PREF_ROOT}.")


def _pref_level(command_scope: "ConfigLevel | None") -> str | None:
    """The pref LEVEL name for a command scope, or ``None`` where it is illegal (spec §2h)."""
    if command_scope is ConfigLevel.box:
        return "box"
    if command_scope is ConfigLevel.workset:
        return "workset"
    return None


def _pref_write_site_error(
    canonical: str, command_scope: "ConfigLevel | None", *, verb: str = "set",
) -> str | None:
    """Refuse a ``pref.*`` WRITE outside the workset / box scopes (spec §2h)."""
    # ⚑ Workset and box ONLY — that restriction is what BOUNDS the resolution
    # recursion, so it is a hard rule. Checked BEFORE the three TARGET filters.
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
        if scope in ("system", "agent", "workset", "box")
        # ⚑ ...but NOT for a YAML-only target: there is no direct set to redirect to
        # (:func:`has_no_cli_write_route`), and naming one would prescribe a command
        # that refuses.
        and not has_no_cli_write_route(target) else ""
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
    """Run the three §2h filters on the ``pref.*`` TARGET KEY at SET time."""
    # ⚑ The KEY is only half of a request — the VALUE is checked separately by
    # :func:`_pref_value_error`; these two are NOT equivalent to launch-time validation.
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
    """The :data:`SCOPE_BIND_KEY_RE` match for *key* — the ONE file-scope parse site."""
    from kanibako.settings.settings_categories import SCOPE_BIND_KEY_RE

    return SCOPE_BIND_KEY_RE.match(key)


def _agent_bind_match(key: str) -> "re.Match[str] | None":
    """The ``AGENT_BIND_KEY_RE`` match — the ONE agent-scope parse site (twin of the above)."""
    from kanibako.settings.settings_categories import AGENT_BIND_KEY_RE

    return AGENT_BIND_KEY_RE.match(key)


def _is_agent_scope_bind_key(key: str) -> bool:
    """The RETIRED AGENT-scope bind route ``agent.<node>.<bind-shaped category>.<name>``."""
    # ⚑ A deliberate SUPERSET of :func:`_is_agent_node_bind_key` (all six categories,
    # recognition only); where both matter the NARROW one is checked FIRST.
    return _agent_bind_match(key) is not None


def _is_scope_bind_key(key: str) -> bool:
    """The RETIRED SCOPE-level bind route ``{system,workset,box}.bindings.{ro,rw}.<name>``."""
    # ⚑ RECOGNISE-TO-REFUSE (R-9); it does NOT cover the AGENT scope, which needs a
    # non-greedy node split — that is :func:`_is_agent_scope_bind_key`.
    return _scope_bind_match(key) is not None


def _retired_because(category: str) -> str:
    """WHY a bind-shaped category has no CLI write route — the one clause that differs BY CATEGORY."""
    # ⚑ The two RETURNED strings are pinned APART by
    # ``test_config_interface.TestCategoryConfigSet.test_the_refusal_states_the_RULING_not_the_shape``
    # on the OLD justification; collapsing them is a behaviour+test change. See llm-docs.
    if category.startswith("bindings."):
        return (
            "the two bindings arms are a single terminal key keyed by DESTINATION, "
            "so a per-name key no longer exists"
        )
    return (
        "a bind-shaped category is authored in YAML only — the CLI has no write "
        "route to one, an accepted loss tracked as DS-BL1"
    )


def has_no_cli_write_route(target: str) -> bool:
    """True iff *target* has NO ``config set`` route, so no message may say "set it directly"."""
    # ⚑ The agent-scope term MUST be :func:`_is_agent_scope_bind_key`, never
    # :func:`_is_path_category_key` (which fails closed for every key since 2026-08-08c).
    # ⚑ The STRUCTURAL ``system.channels.*`` paths are NOT covered here and never were.
    from kanibako.settings.settings_keyspace import is_terminal_category_key

    return (
        _is_scope_bind_key(target)
        or _is_agent_scope_bind_key(target)
        or is_terminal_category_key(target)
    )


def _bind_route_retired_message(
    display_key: str, *, verb: str, route: str, why: str, cure: str, survives: str,
) -> str:
    """THE refusal text for a retired bind-shaped CLI write route — ONE wording, both scopes."""
    # ⚑ *survives* is a REQUIRED keyword, never a default: the honest answer differs by
    # door, and a default would let one door inherit the wrong one silently.
    # ⚑ *display_key* is the ``+`` spelling — ``℘`` must never reach a message.
    return (
        f"Error: '{display_key}' cannot be {verb} from the CLI — the "
        f"'{route}' route is RETIRED ({why}). {cure} {survives}"
    )


def scope_bind_retired_error(canonical: str, *, verb: str) -> str | None:
    """The refusal + cure for a RETIRED file-scope bind-shaped WRITE, or ``None``."""
    # ⚑ Covers ALL SIX categories and widens WITHOUT an edit here (the regex is derived).
    # ⚑ The ``survives`` clause offers the per-entry READ because at these three scopes a
    # slot is still claimed for it — unlike the agent scope. See llm-docs.
    m = _scope_bind_match(canonical)
    if m is None:
        return None
    scope, category = m.group("scope"), m.group("category")
    return _bind_route_retired_message(
        canonical,
        verb=verb,
        route=f"{scope}.{category}.<name>",
        why=_retired_because(category),
        cure=(
            f"Edit the '{scope}:' table of the {scope} settings file "
            f"directly; the launch reads it from there."
        ),
        survives=(
            f"Reading it back with 'config get {canonical}' still works."
        ),
    )


def agent_node_bind_retired_error(canonical: str, *, verb: str) -> str | None:
    """The refusal + cure for a RETIRED AGENT-scope bind-shaped WRITE, or ``None``."""
    # ⚑ ONE PARSER, ALL SIX (``AGENT_BIND_KEY_RE``) — it used to be two, and the second
    # half went silently DEAD on 2026-08-08c. Do not re-split it.
    # ⚑ WHAT SURVIVES DIFFERS BY ARM: only a ``bindings`` arm keeps a per-entry READ.
    # ⚑ The cure names the NODE's own settings file, never a scope table.
    from kanibako.settings.agent_file import file_spelling

    m = _agent_bind_match(canonical)
    if m is None:
        return None
    node, category, name = m.group("node"), m.group("category"), m.group("name")
    shown_node = display_agent_ref(node)
    display_key = f"agent.{shown_node}.{category}.{name}"
    if _is_agent_node_bind_key(canonical):
        survives = f"Reading it back with 'config get {display_key}' still works."
    else:
        survives = (
            f"The surviving key is 'agent.{shown_node}.{category}' — the whole "
            f"dest-keyed map; an entry inside it is DATA, not a key of its own."
        )
    return _bind_route_retired_message(
        display_key,
        verb=verb,
        route=f"agent.<node>.{category}.<name>",
        why=_retired_because(category),
        # ⚑ The file's own spelling comes from the BOUNDARY (``agent_file``), never a literal
        # here: this message QUOTES the agent file at the user, so it must say whatever that
        # file actually spells — which is why S2's flatten changed it in ONE place.
        # ⚑ The NODE is not in the spelling any more, and its absence is the point: the file IS
        # that node's, so ``self:`` expands to ``agent.<node>`` and the category table sits
        # DIRECTLY under it. The node stays in the PATH, which is what tells the user which
        # file to open.
        cure=(
            f"Edit the '{file_spelling(category)}' table of that "
            f"agent's own settings file (agents/{shown_node}/"
            f"settings.yaml) directly; the launch reads it from there."
        ),
        survives=survives,
    )


def _is_path_category_key(key: str) -> bool:
    """True iff *key* is a PER-NAME PATH-TUPLE category key."""
    # ⚑⚑ IT IS NOW FALSE FOR EVERY KEY, AND THAT IS THE CORRECT ANSWER (2026-08-08c).
    # ⚑ KEPT, not inlined to ``False``: it must keep asking the REGEX, so re-admitting a
    # per-name category stays a one-line edit to ``_NON_TERMINAL_BIND_CATEGORIES``.
    # ⚑⚑ It is NO LONGER A RECOGNISER — do not wire it back into one (llm-docs).
    from kanibako.settings.settings_categories import BIND_KEY_RE

    return BIND_KEY_RE.match(key) is not None

def _has_dedicated_route(canonical: str) -> bool:
    """Does SOME ``set_config_value`` branch claim *canonical*?"""
    # ⚑ MIRRORS THE DISPATCH CHAIN in :func:`set_config_value`, in the same order — edit
    # the two together (``TestSetDispatchCoverage`` fails if they drift). PREAMBLE guards
    # are NOT terms. ⚑ Do NOT restore the ``_is_path_category_key`` term: it would report
    # a route for a key nothing writes.
    return (
        _is_pref_key(canonical)
        or _is_agent_node_secret_key(canonical)
        or _is_scope_secret_key(canonical)
        or _is_scope_env_key(canonical)
        or _is_persona_agent_key(canonical)
        or _is_agent_setting(canonical)
        or _is_box_agent_key(canonical)
        or is_system_path_key(canonical)
        or _route_key(canonical) in _KEY_ROUTES
    )


def _probes_at_set_time(canonical: str) -> bool:
    """Does a ``config set`` of *canonical* run the E3 RESOLUTION probe?"""
    # ⚑ The test is "does this value reach the expander", NOT "is it a scalar".
    # ⚑ The live ``<scope>.env.<VAR>`` arm is deliberately NOT excluded — do not "fix" a
    # probe complaint on it by widening :func:`_is_bare_env_key`. See llm-docs.
    if _is_pref_key(canonical):
        # ⚑ The GENERIC probe is a NO-OP at a ``pref.*`` path and MUST NOT run there: it
        # would write the target's leaf names into a KeyStore, and a RESERVED leaf name
        # then raises ReservedKeyError, breaking the "never raises" contract. The pref
        # route runs the REAL probe at the TARGET path (:func:`_pref_value_error`).
        return False
    return _has_dedicated_route(canonical)
