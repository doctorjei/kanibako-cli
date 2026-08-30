"""The CLI-facing config KEY TAXONOMY — what a key is, and where it lives.

**_Terminology_**
- _family_: a CLI-surface key SHAPE recognised by SPELLING (``pref.<target>``,
  ``agent.<node>.<leaf>``, ``<scope>.<category>.<name>``, ``<scope>.secret_path.<VAR>``, the bare
  agent behaviour keys, the routed scalars, the config-file-only bootstrap tier)
- _route_: the ``(sections, leaf)`` nested slot in a settings file a key is stored at
- _refusal_: the ``Error: …`` string a verb returns for a key it will not serve

⚑ NOT THE KEYSPACE VALIDATOR, and must never become a second one: declaredness has ONE authority,
:mod:`kanibako.settings.settings_keyspace`. This module classifies spellings only.
⚑ Layering: BELOW ``config_interface`` and ``config_dest`` — it must not import either.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path  # noqa: F401  (annotations)
from typing import Collection, Iterator

from kanibako.agent_ref import canonicalize_agent_ref, display_agent_ref
from kanibako.errors import ConfigError
from kanibako.settings.agent_config import (
    DECLARATION_ROOT_LABEL,
    DEFAULT_ROOT_LABEL,
)
from kanibako.settings.config import coerce_bool
from kanibako.settings.kb_store import SCOPE_CONTAINMENT
from kanibako.settings.paths_defaults import CONFIG_PATH_DEFAULTS, SYSTEM_PATH_DEFAULTS
from kanibako.settings.settings_categories import DECLARATION_ROOT_REF
from kanibako.settings.settings_keyspace import (
    ACCESS_TIERS,
    PATH_VALUED_AGENT_LEAVES,
    SCALAR_AGENT_LEAVES,
    TABLE_VALUED_AGENT_LEAVES,
    access_default,
    effective_agent_leaves,
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
# ⚑⚑ SINCE 2026-08-28 NO VERB'S VOCABULARY IS ON IT: ``system get`` was the last
# read gate, and the seven False answers it forwarded ("unknown config key" for a
# key ``get_config_value`` reads) are gone with it. The quarantine now bounds a
# PARSER's disambiguation, not a user-facing refusal — which is why widening the
# set is still not the cure for anything.
# ⚑ The block travels with the set; do not copy the pattern elsewhere. Ruling,
# cost, and the seven False answers: llm-docs/kanibako/settings/config_keys.py.md.
# ---------------------------------------------------------------------------
#: The setup VERSION MARKER (spec §2g) — spelled ONCE, here.
#: ⚑⚑ THE STORAGE/DECLARATION DELTA IS CLOSED (Jei, 2026-08-26).  Spec §2g declares this a
#: Layer-2 ``system`` SETTINGS key, and the code now stores it as one: ``setup`` writes it
#: to ``@config.settings`` (``setup_cmd._write_setup_marker``) and the staleness gate reads
#: it from there (``config.read_setup_completed``), so it is an ORDINARY ``system.*`` key
#: routed through :data:`_KEY_ROUTES` beside ``system.agent``.
#: 🛑 It kept its own destination RULE (``config_dest._BOOTSTRAP``) and its own read family
#: (:func:`is_config_file_only_key`) only for as long as it lived in Layer 1; both are
#: retired.  Do not re-add either — Layer 1 holds the ``config.*`` bootstrap paths ALONE
#: (spec §1), so no key routes there.
SETUP_MARKER_KEY = "system.setup_completed"

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
    # ⚑ THE REST OF ``DECLARED_AGENT_LEAVES``, ADDED 2026-08-23 — the six above were the
    # whole bare surface, so ``config get template`` answered "unknown config key" for a
    # key the manifest declares ``set: cli+file``.  Hand-written because deriving this set
    # was PROPOSED AND DECLINED (the quarantine block above); the completeness it can no
    # longer get by construction it gets by a LOUD failure instead —
    # ``tests/test_settings/test_agent_leaf_shape.py`` reds when a declared agent leaf is
    # missing from here (P15).
    # template / canon: the per-agent template SOURCE + CANON CONTRIBUTION root (spec §2d).
    "template",
    "canon",
    # run_args: the raw-argv passthrough escape hatch (spec §2d).
    "run_args",
    # transform: WHICH binary transform this agent uses (spec §2d); claude's is `tweakcc`.
    "transform",
    # transform_settings: DECLARED and READABLE, but dict-valued — the write verbs refuse
    # it by name (``agent_leaf_table_error``).  It is here so the READ gate admits it and
    # the refusal can name the shape instead of denying the key exists (spec §0).
    "transform_settings",
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
    # Layer-2 system.* path SETTINGS (the ``system:`` table of the SYSTEM SETTINGS
    # file, spec §2g).  ``global`` is ELIMINATED (children inline
    # ``@config.data/global/...``).
    "system.backup",
    "system.channelroot",
    # M-11: ``system.base_template`` → ``system.template``, RETIRED not aliased.
    "system.template",
    "system.canon",
    "system.cache",
    "system.runtime",
    # ⚑ THE ``system.channels.*`` FAMILY, WHOLE — the five declared leaves (spec §2g),
    # STRING paths, one nested slot; the SYSTEM twins of ``workset.channels.*`` above.
    # They are here for the same reason the workset five are: without the spelling the
    # SET gate answers "unknown config key" for a DECLARED, settable key.  ⚑ It was the
    # ``get`` gate too until 2026-08-28; that read is on ``key_validity`` now, so this set
    # no longer bounds any READ.
    "system.channels.common",
    "system.channels.chat",
    "system.channels.share",
    "system.channels.broadcast",
    "system.channels.mailboxes",
    # system.agent (spec §2g): the CURRENT agent's name — a system-scope SETTING, so it
    # routes to the ``system:`` table of the SYSTEM SETTINGS file, not the [system] config table.
    "system.agent",
    # system.setup_completed (spec §2g): the setup VERSION MARKER. ⚑ Here since 2026-08-23,
    # because ``system_cmd``'s get arm gates on this set: a key the CLI can now SET must be
    # a key the CLI can READ, or the two verbs disagree about whether it exists. Its storage
    # is the SYSTEM SETTINGS file since 2026-08-26 — the sibling of ``system.agent`` above
    # in every respect now; see :data:`SETUP_MARKER_KEY`.
    SETUP_MARKER_KEY,
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
    # The setup VERSION MARKER (spec §2g) — an ORDINARY system settings key since
    # 2026-08-26, in the same ``system:`` table of the same file as ``system.agent``.
    SETUP_MARKER_KEY: (("system",), "setup_completed"),
    "system.auth.share_allowed": (("system", "auth"), "share_allowed"),
    # The Layer-2 ``system.*`` PATH keys (spec §2g) — the SYSTEM twins of the
    # ``workset.*`` layout anchors below, wired identically: one nested slot in the
    # system SETTINGS file, STRING paths, no type coercion, the same set-time E3
    # resolution probe (``_has_dedicated_route``). ⚑ They are SETTINGS keys, not §1
    # config keys — the manifest marks all eleven ``set: cli+file``, and §2a names
    # ``system.template`` in the CLI-settable list beside ``workset.vault_{ro,rw}``.
    "system.backup": (("system",), "backup"),
    "system.channelroot": (("system",), "channelroot"),
    "system.template": (("system",), "template"),
    "system.canon": (("system",), "canon"),
    "system.cache": (("system",), "cache"),
    "system.runtime": (("system",), "runtime"),
    # The five declared channel type-roots (spec §2g); ``broadcast`` is a FILE.
    "system.channels.common": (("system", "channels"), "common"),
    "system.channels.chat": (("system", "channels"), "chat"),
    "system.channels.share": (("system", "channels"), "share"),
    "system.channels.broadcast": (("system", "channels"), "broadcast"),
    "system.channels.mailboxes": (("system", "channels"), "mailboxes"),
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

# The DECLARED TYPE of every key whose type the CLI acts on — the code carrier of the
# registry's ``type:`` column.  ⚑ ``access`` is an ENUM guarded by
# :func:`access_value_error`, never a type here.
#
# ``bool``  — coerced to a real bool before writing (the H2 fix).
# ``path``  — a host PATH, so [R147]'s bare-relative refusal reaches it at set time
#             (:func:`is_path_valued_key`).  NOT coerced: the file keeps the raw
#             spelling, tokens and all (spec §0).
#
# ⚑ THE TWO KINDS ARE ONE TABLE ON PURPOSE.  A key has one declared type, and the
# manifest column that states it is one column; two code tables would be two answers to
# "what is this key" and would drift the way every split carrier here has.
# ⚑ THE PARAMETRIC PATH KEYS ARE NOT SPELLABLE HERE — ``agent.<node>.{template,canon}``
# and the ``secret_path.<VAR>`` family have no fixed canonical string, so
# :func:`is_path_valued_key` is the predicate to ask, never this table directly.
KEY_TYPES: dict[str, str] = {
    "box.share_images": "bool",
    "system.auth.share_allowed": "bool",
    "workset.auth.share_allowed": "bool",
    "workset.auth.global_sync": "bool",
    "box.auth.global_enabled": "bool",
    "box.auth.workset_enabled": "bool",
    "box.enable_vault": "bool",
    "workset.skip_kuid_check": "bool",
    # The Layer-1 config tier and the Layer-2 ``system.*`` tier, DERIVED from the two
    # declared-default tables they are (P13) — a path key added to either arrives here
    # with no edit.  ⚑ The six ``config.*`` rows are ``set: file`` and have no CLI write
    # route at all; they are typed anyway because this table answers what a key IS, not
    # what a verb may do to it, and their read-time guard shares the same predicate.
    **{key: "path" for key in CONFIG_PATH_DEFAULTS},
    **{key: "path" for key in SYSTEM_PATH_DEFAULTS},
    # The workset LAYOUT anchors and the six-leaf ``channels`` family (spec §2c) — the
    # keys ``workset_dirkeys.resolve_workset_dir_key`` refuses a bare relative for when
    # it READS them.  ⚑ Spelled out because no live table enumerates them: their
    # defaults reach the launch through ``settings_launch.workset_anchor_floor`` as
    # RESOLVED values, which carry no type.
    "workset.workspaces": "path",
    "workset.boxes": "path",
    "workset.logs": "path",
    "workset.vault_ro": "path",
    "workset.vault_rw": "path",
    "workset.canon": "path",
    "workset.registry": "path",
    "workset.template": "path",
    "workset.auth.path": "path",
    "workset.channelroot": "path",
    "workset.channels.common": "path",
    "workset.channels.chat": "path",
    "workset.channels.broadcast": "path",
    "workset.channels.share": "path",
    "workset.channels.mailboxes": "path",
    "workset.channels.share_global": "path",
    # The two box-scope paths (spec §2b).
    "box.canon": "path",
    "box.images_store": "path",
}


@dataclass(frozen=True)
class CoercionError:
    """A typed key's value could not be read as its declared type — the H2 failure."""

    #: The whole user-facing line, already prefixed ``Error:``.
    message: str


def _coerce_value(canonical: str, value: "str | None") -> object | None:
    """Coerce *value* to the typed form declared for *canonical* in KEY_TYPES."""
    # ⚑ A FAILURE COMES BACK AS :class:`CoercionError`, NEVER AS A BARE ``str``. The
    # callers used to test ``isinstance(result, str) and KEY_TYPES.get(key)`` — which
    # reads "a typed key returned a string, so it must be the error" and stops being
    # true the moment a declared type's own values ARE strings. ``path`` is that type.
    if value is None:
        return None  # an explicit present-None request (--null): never coerced.
    kind = KEY_TYPES.get(canonical)
    if kind == "bool":
        coerced = coerce_bool(value)
        if coerced is not None:
            return coerced
        return CoercionError(
            f"Error: {canonical} expects a boolean "
            f"(true/false/1/0/yes/no), got {value!r}"
        )
    return value


# ---------------------------------------------------------------------------
# PATH-valued keys — the SET-TIME half of [R147]
# ---------------------------------------------------------------------------

def is_path_valued_key(canonical: str) -> bool:
    """True iff *canonical* names a key whose value is a HOST PATH (registry ``type: path``).

    The FIXED spellings come from :data:`KEY_TYPES`; the three PARAMETRIC families —
    the bare agent leaves the CLI serves for the any-agent tier, the per-node
    ``agent.<node>.{template,canon}``, and ``secret_path.<VAR>`` at every scope — have
    no fixed string and are recognised by their own parsers.
    """
    if KEY_TYPES.get(canonical) == "path":
        return True
    if canonical in PATH_VALUED_AGENT_LEAVES:
        return True
    parsed = _parse_persona_agent_key(canonical)
    if parsed is not None and parsed[1] in PATH_VALUED_AGENT_LEAVES:
        return True
    return _is_scope_secret_key(canonical) or _is_agent_node_secret_key(canonical)


def path_key_anchor(canonical: str) -> "tuple[str, str]":
    """``(anchor ref, label)`` for [R147]'s OTHER reading of a bare relative at *canonical*.

    The anchor is the root the user might have meant INSTEAD of the cwd, and it is the
    one the READ-TIME seams already name, so a key cannot get two answers:

    * a Layer-1/Layer-2 path key takes its own declared default's leading token, exactly
      as ``paths._refuse_bare_relative`` derives it (P13 — a key added to either table
      carries its own anchor here with no edit);
    * every ``workset.*`` path key takes ``@meta.workset.path``, which is what
      ``workset_dirkeys.resolve_workset_dir_key`` names for all of them — the six
      ``channels.*`` leaves included, since ``channels.py`` resolves them through that
      same seam rather than through ``@workset.channelroot``;
    * the rest take their spec §2a DECLARATION ROOT
      (:data:`~kanibako.settings.settings_categories.DECLARATION_ROOT_REF`).

    ⚑ THE LABEL IS PART OF THE ANSWER.  ``box.images_store`` is probed from podman at
    runtime and ``secret_path.<VAR>`` declares nothing at all, so for those two the
    anchor is a scope root and is introduced as one — see :data:`DECLARATION_ROOT_LABEL`.
    """
    # ⚑ THE TWO ``secret_path`` SHAPES ARE TESTED FIRST, and the order is load-bearing:
    # ``workset.secret_path.TOKEN`` is a ``workset.*`` key that declares NO default, so
    # the prefix arms below would give it the right root under the wrong label.
    secret = _parse_agent_node_secret_key(canonical)
    if secret is not None:
        return (DECLARATION_ROOT_REF["agent"].format(agent=secret[0]),
                DECLARATION_ROOT_LABEL)
    if _is_scope_secret_key(canonical):
        return DECLARATION_ROOT_REF[canonical.split(".", 1)[0]], DECLARATION_ROOT_LABEL
    declared_default = CONFIG_PATH_DEFAULTS.get(canonical) or SYSTEM_PATH_DEFAULTS.get(canonical)
    if declared_default is not None:
        return declared_default.split("/", 1)[0], DEFAULT_ROOT_LABEL
    if canonical.startswith("workset."):
        return DECLARATION_ROOT_REF["workset"], DEFAULT_ROOT_LABEL
    if canonical == "box.images_store":
        return DECLARATION_ROOT_REF["box"], DECLARATION_ROOT_LABEL
    if canonical.startswith("box."):
        return DECLARATION_ROOT_REF["box"], DEFAULT_ROOT_LABEL
    # The two agent-scope path leaves, anchored at the NODE'S OWN store root. The bare
    # CLI spelling writes the any-agent tier, whose node is the reserved ``default``.
    parsed = _parse_persona_agent_key(canonical)
    node = parsed[0] if parsed is not None else AGENT_DEFAULT_SUB
    return DECLARATION_ROOT_REF["agent"].format(agent=node), DEFAULT_ROOT_LABEL


def agent_node_of(canonical: str) -> str:
    """The agent NODE a per-node *canonical* key ADDRESSES, or ``""`` when it names none.

    THE QUESTION A WRITE VERB ASKS to name the agent its command is about when the KEY is
    the only place that says so.  ``agent set`` is handed the node; ``system set
    agent.<node>.canon=…`` holds it nowhere but the key, and without it the set-time
    snapshot floors no ``meta.agent.<node>.path`` — so a value spelled against the very
    store root the write lands in is refused as a dangling ``@``-reference.

    ⚑ DERIVED FROM THE SAME TWO PARSERS :func:`path_key_anchor` CONSULTS, in the same
    order, so the anchor a refusal NAMES and the anchor the set-time floor PROVIDES cannot
    disagree about which store root a key hangs off.  No third parser is introduced.

    ⚑ THE BIND ARM IS DELIBERATELY ABSENT, and its absence is the lesson the removed
    ``_agent_scope_node`` left: an ``agent.<node>.bindings.{ro,rw}.<name>`` set is refused
    BY NAME in the verb preamble (R-9), so an arm for it could never change an outcome —
    it would be dead the day it was written.  :func:`resolve_key` carries that arm because
    it canonicalises a key the READ verbs still serve; this answers a WRITE-time question.
    """
    parsed = (
        _parse_agent_node_secret_key(canonical)
        or _parse_persona_agent_key(canonical)
    )
    return parsed[0] if parsed is not None else ""

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

# ---------------------------------------------------------------------------
# ⚑⚑ THE FLAT UNDERSCORE SPELLING IS GONE, AND ITS ABSENCE IS THE FIX (P3/P7).
# ``_dot_to_flat``, ``_FLAT_TO_CANONICAL`` and ``_route_key`` mapped ``box_image``
# onto ``box.image`` for the write verbs. That made an UNDECLARED spelling a second
# user-facing surface, refused by ``key_validity`` and by ``get`` while ``set``
# accepted it — and worse, the two spellings landed in DIFFERENT FILES: the dest
# rule reads the scope token off the key AS TYPED, so ``box_image`` (whose first
# dotted token is the whole string) fell to the Layer-1 ``kanibako_config.yaml``
# floor while ``box.image`` went to the settings tier. The SPELLING chose the
# precedence. There is no mapping left to consult, so no verb can route one.
# ⚑ DO NOT REINTRODUCE A SPELLING NORMALISER HERE. A key has ONE spelling (spec §0,
# the keyspace is CLOSED); a second one is not an alias, it is a second keyspace.
# The one-way display flatten went with it — a confirmation echoes the CANONICAL
# dotted key, so a successful ``set`` cannot teach a form the next ``get`` refuses.
# ---------------------------------------------------------------------------

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
# The PLUGIN half of the agent vocabulary (spec §0) — supplied to every surface
# in this module that asks what an agent leaf may be called.
# ---------------------------------------------------------------------------

def plugin_declared_leaves() -> "frozenset[str]":
    """The agent leaves the INSTALLED PLUGINS declare (spec §0), or empty.

    ⚑ ONE SUPPLIER FOR THIS MODULE, and that is the whole point of it being a function:
    the per-node RECOGNISER (:data:`_PERSONA_STATE_LEAVES`) and the ``agent`` noun's
    §0 GATE (:func:`agent_key_reason`) asked two different sources until 2026-08-29, so
    a plugin leaf was a key at one verb and not at another.  ``default_valid_agents`` is
    the source both use now: it is the production ``valid_agents`` supplier, it memoizes
    per process, and it has a documented reset seam (``reset_discovery_cache``) that the
    ``settings_keyspace_probe`` memo — primed at ``pytest_configure`` — deliberately
    does not.
    ⚑ IMPORTED IN THE BODY: discovery must never run at module import.
    """
    from kanibako.settings.settings_prefs import default_valid_agents

    return frozenset(getattr(default_valid_agents(), "leaves", None) or ())


class _PluginDeclaredLeaves(Collection[str]):
    """:func:`plugin_declared_leaves` as a set that DISCOVERS ON THE FIRST QUESTION.

    ⚑⚑ IT MUST NOT BE MATERIALISED AT IMPORT OR AT A CALL SITE.  Discovery imports and
    instantiates every installed plugin, and those modules parse YAML in their module
    bodies; it was measured at ``+67 ms`` per settings-resolving command (2026-08-25),
    73% of the whole resolve, when it rode in as an eagerly-evaluated keyword argument.
    Handed to :func:`~kanibako.settings.settings_keyspace.effective_agent_leaves`
    instead, the CORE §2d set is asked first and this is reached only for a leaf core
    cannot answer for.
    ⚑ NOT A MEMO. Every access goes through the supplier, so a test that resets
    discovery is seen here exactly as it is by every other reader.
    """

    __slots__ = ()

    def __contains__(self, item: object) -> bool:
        return item in plugin_declared_leaves()

    def __iter__(self) -> Iterator[str]:
        return iter(plugin_declared_leaves())

    def __len__(self) -> int:
        return len(plugin_declared_leaves())


#: The PLUGIN half of the effective agent vocabulary, as a thing to ASK.
PLUGIN_DECLARED_LEAVES: "Collection[str]" = _PluginDeclaredLeaves()

# ---------------------------------------------------------------------------
# Per-persona agent keys (block B1) — ``agent.<node>.<key>`` set on the agent's
# OWN settings file ``agents/<node>/agent.yaml``.
# ---------------------------------------------------------------------------

# The per-persona agent leaves this module RECOGNISES — the FLAT agent-state knobs plus
# the ``env.`` section, the EXACT shape ``agent_file.load`` reads back.
# ⚑⚑ RECOGNITION, NOT SETTABILITY, AND THE WHOLE DECLARED SET (spec §0): a declared key
# must be refused BY NAME with its own rule, never degraded to "unknown config key".
# ``transform_settings`` is in here so :func:`agent_leaf_table_error` can say what is
# actually wrong with writing a scalar to it; :data:`SCALAR_AGENT_LEAVES` is the settable
# half, and the verbs consult that one.
# ⚑ DERIVED FROM THE DECLARATION SoT (P13). It was a hand-kept copy until 2026-08-23 and
# had fallen three leaves behind — ``run_args``, ``transform`` and ``transform_settings``
# are declared ``set: cli+file`` and answered "unknown config key" at every spelling.
# ⚑⚑ THE EFFECTIVE SET SINCE 2026-08-29 — core §2d UNIONED with what the PLUGINS declare
# (§0 *"Agent specifics are PLUGIN-declared"*), through the SAME lazy union
# ``key_class`` judges with.  Core-only, it was a SECOND vocabulary disagreeing with the
# judge: ``kanibako system set agent.goose.provider=x`` answered "unknown config key"
# (rc 1) for a leaf the goose target declares, and ``system get agent.goose.provider``
# answered "(not set)" at rc 0 over a value stored in that node's own file — both
# measured, both §0 breaches. It is a ``Collection``, NOT a ``frozenset``: materialising
# it would put plugin discovery on every reader.
_PERSONA_STATE_LEAVES: "Collection[str]" = effective_agent_leaves(
    PLUGIN_DECLARED_LEAVES,
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
        f"'{access_default()}'."
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
    """True iff *key* is the BARE CLI spelling of an ``agent.default.<leaf>`` key (spec §2d).

    ⚑ THE BARE FORM IS THE ONE THE CLI SERVES for the any-agent tier, and this predicate is
    what makes it so at every verb: ``agent.default.<leaf>`` typed in full is refused with a
    cure naming the bare spelling (``config_dest._persona_agent_target``).  Widening this to
    the declaration SoT is therefore what makes that cure TRUE — before 2026-08-23 it named
    ``template`` and ``canon``, and both answered "unknown config key".
    ⚑ SCALAR leaves only: ``transform_settings`` holds a table and is refused by
    :func:`agent_leaf_table_error` instead, at both spellings.
    """
    return key in SCALAR_AGENT_LEAVES


def agent_default_tier_leaf(key: str) -> str | None:
    """The bare leaf *key* names when it spells the any-agent tier IN FULL, else ``None``.

    ``agent.default.model`` → ``"model"``; the reserved ``default`` node is the ANY-AGENT
    TIER, not a persona, so the key it addresses is the one the bare spelling addresses and
    it is stored in the NOUN's settings file under ``agent: default:`` — never in an
    ``agents/default/agent.yaml``, which does not exist.

    ⚑⚑ A DESTINATION FACT, NOT A PERMISSION.  ``set``/``reset`` still refuse this spelling
    with the cure naming the bare form (``config_dest._persona_agent_target``); what this
    answers is where the value LIVES, which is what makes the READ honest.  Until it
    existed, ``system get agent.default.model`` answered "(not set)" at rc 0 over a value
    ``system get model`` returned — a fabricated answer for a declared key (spec §0).

    ⚑ THE DECLARED SET, NOT :data:`SCALAR_AGENT_LEAVES`, and the difference is the point:
    ``transform_settings`` cannot be WRITTEN from the CLI but is declared, hand-authored
    and read, so it must read back here too.
    ⚑ DERIVED (P13) through :func:`_parse_persona_agent_key`, whose own leaf set is the
    declaration SoT — a leaf entering §2d reaches this surface with no edit.  The ``env.``
    section form parses to a dotted tail and is deliberately NOT claimed: it is a different
    family with its own scoped spelling.
    """
    parsed = _parse_persona_agent_key(key)
    if parsed is None or parsed[0] != AGENT_DEFAULT_SUB:
        return None
    # ⚑ NOT REDUNDANT WITH THE PARSE, and :data:`_PERSONA_STATE_LEAVES` is what makes it
    # so: the ``env.`` arm yields a DOTTED tail (``env.FOO``), which is in no leaf set,
    # and that is exactly how this family is excluded.  Reading the EFFECTIVE set is also
    # what keeps the two spellings honest — ``agent.default.provider`` is a key under
    # ``key_class``, so a stored value must read back rather than answer "(not set)".
    return parsed[1] if parsed[1] in _PERSONA_STATE_LEAVES else None


def agent_leaf_table_error(canonical: str, *, verb: str) -> str | None:
    """Refuse a WRITE at a declared agent leaf whose value is a TABLE (spec §2d).

    ⚑ *verb* is REQUIRED; gates itself — ``None`` for every other key, so the verbs apply it
    unconditionally in their preamble.  It must run BEFORE the persona branch: the reserved
    any-agent-tier refusal would otherwise answer ``agent.default.transform_settings`` with a
    cure naming a bare spelling that this rule then refuses, which is the same broken-cure
    shape it exists to prevent.
    ⚑ It names the KEY and the SHAPE, never "unknown config key" — the key is declared, is
    read fine by ``config get``, and is hand-authored in YAML today.
    """
    leaf = canonical.rsplit(".", 1)[-1]
    if leaf not in TABLE_VALUED_AGENT_LEAVES or not _names_agent_leaf(canonical, leaf):
        return None
    return (
        f"Error: '{canonical}' holds a TABLE, not a scalar, so it cannot be {verb} from "
        f"the command line — its entries are DATA inside the table, not keys of their "
        f"own (spec §2d). Edit the '{leaf}' table in the settings file directly; the "
        f"launch reads it from there."
    )


def _terminal_category_message(
    display_key: str, *, verb: str, cure: str, survives: str,
) -> str:
    """THE refusal text for a dest-keyed TERMINAL category write — ONE wording, three arms.

    ⚑ Deliberately the wording of :func:`kanibako.settings.agent_file.table_value_error`,
    which is the AGENT noun's half of this same fact: one key, one shape, one sentence,
    whichever door a user knocks on.
    ⚑ *survives* is a REQUIRED keyword, never a default — the honest read-back differs by
    arm, and a default would let one arm inherit another's promise.
    """
    return (
        f"Error: '{display_key}' holds a TABLE keyed by box DESTINATION, not a scalar, so "
        f"it cannot be {verb} from the command line — its entries are DATA inside the "
        f"table, not keys of their own (spec §2a). {cure} {survives}"
    )


def terminal_category_write_error(canonical: str, *, verb: str) -> str | None:
    """Refuse a WRITE at a DEST-KEYED TERMINAL category key, or ``None`` (spec §2a).

    ⚑⚑ MEASURED, 2026-08-28, and this is why it exists: ``kanibako system set
    system.masks=/tmp`` answered "Error: unknown config key: system.masks" — telling a user
    that a DECLARED key is not a key, which is the one thing §0's closed keyspace forbids in
    both directions.  The REFUSAL is correct and stays: the registry's ``box.masks`` row
    reads ``set: file`` and §2a makes every bind-shaped category YAML-only.  Only the
    MESSAGE was wrong.

    ⚑⚑ IT IS NOT "RETIRED", AND MUST NOT SAY SO.  ``scope_bind_retired_error`` /
    ``agent_node_bind_retired_error`` cover the PER-NAME spellings — ``<scope>.<cat>.<name>``
    — which once HAD a route.  The TERMINAL spelling never had one, so borrowing their word
    would ship a false statement.  Their regexes require a trailing ``.<name>``, so the two
    families are disjoint by construction and neither preempts the other.

    ⚑ DERIVED (P13): the family is ``settings_keyspace.is_terminal_category_key``, the same
    predicate ``agent_category_read_error`` and ``foreign_scope_read_error`` gate on — a
    category entering or leaving §2a moves all three with no edit here.

    ⚑⚑ THREE CURE ARMS, EACH MEASURED BEFORE IT WAS PRINTED.  The ``default`` tier does NOT
    belong with the per-node arm however much its spelling suggests it: there is no
    ``agents/default/agent.yaml`` and ``kanibako agent get default caches`` exits 1 on
    "agent 'default' not found", so that cure would be a lie.  It is a table in the SYSTEM
    settings file and reads back at the system noun (measured, all seven categories).
    """
    from kanibako.settings.agent_file import file_spelling
    from kanibako.settings.settings_keyspace import is_terminal_category_key

    if not is_terminal_category_key(canonical):
        return None
    scope, _, tail = canonical.partition(".")
    if scope == "agent":
        node, _, category = tail.partition(".")
        if node != AGENT_DEFAULT_SUB:
            shown_node = display_agent_ref(node)
            return _terminal_category_message(
                f"agent.{shown_node}.{category}",
                verb=verb,
                # ⚑ The file's own spelling from the BOUNDARY (``agent_file``), never a
                # literal: this message QUOTES that file at the user.
                cure=(
                    f"Author it in the '{file_spelling(category)}' table of that agent's "
                    f"own settings file (agents/{shown_node}/agent.yaml); the launch reads "
                    f"it from there."
                ),
                survives=(
                    f"Reading it back with 'kanibako agent get {shown_node} {category}' "
                    f"still works."
                ),
            )
        return _terminal_category_message(
            canonical,
            verb=verb,
            cure=(
                "Author it in the 'agent: default:' table of the system settings file; "
                "the launch reads it from there."
            ),
            survives=(
                f"Reading it back with 'kanibako system get {canonical}' still works."
            ),
        )
    # ⚑ EXHAUSTIVE BY CONSTRUCTION: ``is_terminal_category_key`` requires a head in
    # ``SCOPE_CONTAINMENT``, and the agent arm above took the only member
    # :data:`_SCOPE_READ_COMMAND` does not key.
    return _terminal_category_message(
        canonical,
        verb=verb,
        cure=(
            f"Author it in the '{scope}:' table of the {scope} settings file; the launch "
            f"reads it from there."
        ),
        survives=(
            f"Reading it back with '{_SCOPE_READ_COMMAND[scope]} {canonical}' still works."
        ),
    )


def _names_agent_leaf(canonical: str, leaf: str) -> bool:
    """Does *canonical* address agent leaf *leaf* — bare, or as ``agent.<node>.<leaf>``?"""
    # ⚑ The two spellings the CLI accepts for one key, and NOTHING else: a ``<scope>`` key
    # that merely ENDS in the same word (a hypothetical ``box.transform_settings``) is not
    # an agent leaf and must not inherit its refusal.
    parsed = _parse_persona_agent_key(canonical)
    return canonical == leaf or (parsed is not None and parsed[1] == leaf)

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
    # ⚑⚑ IT GATES NO VERB'S VOCABULARY ANY MORE (2026-08-28). ``system get`` was the
    # last read on it, and those seven False answers reached the user as "unknown
    # config key" for keys the engine reads fine; the §0 gate
    # (:func:`scope_read_key_error` → ``key_validity``) is the only vocabulary now.
    # What is left is the DISAMBIGUATION callers in ``commands/box/_parser.py``, whose
    # question really is "key or project name" — so do NOT re-wire this into a gate.
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

def is_config_file_only_key(key: str) -> bool:
    """Keys whose value is READ from the bootstrap config file rather than a settings file."""
    # ⚑⚑ THE ``SYSTEM_PATH_DEFAULTS`` FAMILY IS NO LONGER HERE, and its absence is the
    # POINT (spec §2g): ``system.{template,canon,backup,cache,runtime,channelroot}`` and
    # ``system.channels.*`` are Layer-2 SETTINGS keys — the keyspace manifest marks every
    # one of them ``set: cli+file`` — so they route through ``_KEY_ROUTES`` to the SYSTEM
    # SETTINGS file like ``workset.vault_ro`` does to the workset file. Refusing them as
    # "structural" contradicted spec §0/§2g and the CLI-settable list at §2a. Do not
    # re-add the family: a spec edit would have to come first.
    # ⚑⚑ IT IS A READ ROUTE, NOT A REFUSAL (2026-08-23). It used to double as "and
    # therefore the write verbs refuse it", which made ``system.setup_completed`` — declared
    # ``set: cli+file``, "PERSISTS, user-resettable" — unsettable AND unresettable, with a
    # refusal telling the user to hand-edit the file the CLI could have written. This
    # predicate answers only "does a READ come from the config file".
    # ⚑⚑ ``config.*`` IS THE WHOLE FAMILY (2026-08-26). :data:`SETUP_MARKER_KEY` was the
    # last non-``config.*`` member and its STORAGE moved to ``@config.settings`` (spec
    # §2g), so it reads through ``_KEY_ROUTES`` like every other setting. What is left is
    # exactly spec §1's Layer-1 set — which is the definition, not a coincidence.
    # ⚑ set/reset short-circuit ``config.*`` earlier (B2) with their own ruled message.
    return key.startswith("config.")


def _user_config_file_str() -> "Path | str":
    """The RESOLVED user bootstrap config file, for refusal messages."""
    # ⚑ ERROR path — it must never itself raise; the literal default is the fallback.
    from kanibako.settings.config import config_file_path
    from kanibako.settings.paths import xdg

    try:
        return config_file_path(xdg("XDG_CONFIG_HOME", ".config"))
    except Exception:
        return "~/.config/kanibako_config.yaml"


#: ⚑ ``system_key_refusal`` LIVED HERE AND IS DELETED (2026-08-23). It answered "'<key>'
#: is a structural config key and cannot be {set,reset,read} from the CLI" for FILE-ONLY
#: ``system.*`` keys, and after the ``SYSTEM_PATH_DEFAULTS`` family and then
#: :data:`SETUP_MARKER_KEY` left that category, THERE ARE NO FILE-ONLY ``system.*`` KEYS —
#: the phrase named an empty set. Its last reachable caller was ``system_cmd``'s ``get``
#: arm, where the only spellings that still fell into it were UNDECLARED ``config.*``
#: ones — and telling a user that a key which does not exist "is a structural config key"
#: asserts the opposite of the truth. Those now answer the §0 refusal
#: :func:`scope_read_key_error` builds, which NAMES the offending key and lists the six
#: declared Layer-1 spellings — the "unknown config key" wording that briefly stood in its
#: place went with the ``is_known_key`` read gate on 2026-08-28.
#: 🛑 Do not reintroduce it for ``config.*``: those keys have their own ruled message
#: (:func:`_config_key_refusal`), which spec §2a requires NOT to mention ``setup``.


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
    # ⚑ The ``system.channels.*`` paths are NOT covered here and never were — and since
    # 2026-08-23 they are routed scalars, so answering False for them is now simply true.
    from kanibako.settings.settings_keyspace import is_terminal_category_key

    return (
        _is_scope_bind_key(target)
        or _is_agent_scope_bind_key(target)
        or is_terminal_category_key(target)
    )


#: How a user SPELLS the surviving read at each file scope's own noun.
#: ⚑ THERE IS NO ``config`` NOUN — measured: ``kanibako config get <key>`` exits on
#: "unrecognized arguments". The read lives on the scope's own verb, and every noun
#: but ``system`` names its SUBJECT before the key (``box``/``workset`` resolve a
#: project, ``system`` has none to resolve). Keyed by :data:`SCOPE_BIND_KEY_RE`'s
#: own ``scope`` group, whose alternation is exactly these three.
_SCOPE_READ_COMMAND = {
    "system": "kanibako system get",
    "workset": "kanibako workset get <workset>",
    "box": "kanibako box get <box>",
}


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
            f"Reading it back with "
            f"'{_SCOPE_READ_COMMAND[scope]} {canonical}' still works."
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
        # ⚑ The AGENT noun's own verb, and it takes the TAIL — the node is the
        # SUBJECT, so repeating ``agent.<node>.`` inside the key double-prefixes it
        # and the read refuses (measured). There is no ``config`` noun to fall back
        # on; see :data:`_SCOPE_READ_COMMAND`.
        survives = (
            f"Reading it back with 'kanibako agent get {shown_node} "
            f"{category}.{name}' still works."
        )
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
            f"agent.yaml) directly; the launch reads it from there."
        ),
        survives=survives,
    )


def agent_key_reason(node: str, tail: str) -> str | None:
    """The §0 reason *tail* is not a declared key of agent *node*, or ``None`` when it is.

    ⚑ ONE CONSTRUCTION, THREE CONSUMERS — the ``agent`` verb's write gate, its read gate, and the
    LAUNCH boundary's passthrough refusal (``agent_file.state_level``).  A REASON rather than a
    message, for the reason ``config_dest.NodeRouteRefusal`` gives: the rule is one, but a verb
    owes a cure and a refused launch owes a file to open.

    ⚑⚑ ``key_validity`` ON THE CANONICAL KEY, NEVER ``is_known_key``, AND THE DIFFERENCE IS
    MEASURED: ``is_known_key("agent.claude.self.model")`` is **True** (the persona parser splits on
    the LAST segment and reads the node as ``claude.self``), so the shape ruling 55 exists to
    refuse would sail through, while ``run_args`` and ``name`` — both live, both pinned — would be
    refused.  ``is_known_key`` answers "is this key-SHAPED, as opposed to a project name"; §0 asks
    "is this a DECLARED key", and only ``key_validity`` answers that.

    *node* is the agent whose file this is — it is KNOWN GOOD (the on-disk store dir), so it is
    supplied AS the valid-agent set rather than re-litigated: an agent-discovery result must never
    be able to refuse a key on an agent the user is demonstrably running.  The PLUGIN-declared
    leaves are unioned in the OTHER direction (§0 *"Agent specifics are PLUGIN-declared"*), without
    which a legitimate ``agent.goose.provider`` would be refused.

    ⚑ :data:`PLUGIN_DECLARED_LEAVES`, THE MODULE'S ONE SUPPLIER, and it is passed rather than
    materialised.  ``default_valid_agents().leaves`` read here directly was the same VALUE reached
    a second way, and :data:`_PERSONA_STATE_LEAVES` did not read it at all — which is how one verb
    came to declare a key another called unknown.  Passing it also DEFERS discovery: a core §2d
    leaf is answered without importing a single plugin.

    ⚑ THE IDENTITY RESIDUE: ``name`` / ``run_args`` are FILE-identity fields of ``AgentConfig``,
    not keyspace leaves (``agent_file._MODELED_KEYS`` already says so), and both are live, written
    and displayed.  ``run_args`` happens to be a declared §2d leaf as well; ``name`` is not, so the
    allowlist is what keeps a shipped, pinned surface working — refusing it would be a breaking
    change no ruling asks for.
    """
    from kanibako.settings.agent_config import IDENTITY_KEYS
    from kanibako.settings.settings_keyspace import key_validity

    if tail in IDENTITY_KEYS:
        return None
    return key_validity(
        f"agent.{node}.{tail}",
        valid_agents=(node,),
        agent_leaves=PLUGIN_DECLARED_LEAVES,
    )


def agent_write_key_error(node: str, tail: str, *, verb: str) -> str | None:
    """Why ``agent <verb> <node> <tail>`` names no key, or ``None`` when it does (spec §0).

    THE ``agent`` NOUN'S CLOSED-KEYSPACE GATE (D-5).  Its sibling verbs route through
    ``set_config_value``, which owns this check for every other noun; this one has its own writer
    and had NO validation at all, so ``agent set claude anything.at.all=x`` stored garbage rc=0 —
    a closed-keyspace breach on a first-class write path, and the ONE place a user could type
    ``self.`` and have it land (ruling 55).
    """
    reason = agent_key_reason(node, tail)
    if reason is None:
        return None
    return (
        f"Error: '{tail}' cannot be {verb} on agent "
        f"'{display_agent_ref(node)}': {reason}."
    )


def agent_read_key_error(node: str, tail: str) -> str | None:
    """Why ``agent get <node> <tail>`` names no key, or ``None`` when it does (spec §0).

    The WRITE vocabulary (:func:`agent_write_key_error`) PLUS the one retired spelling whose READ
    survived: ``agent.<node>.bindings.{ro,rw}.<name>`` is not a declared key — the arm is terminal
    and its entries are destinations inside the value — but ``config get`` reads it anyway (R-9:
    *"the read survived the write, on purpose"*), and the hand-edit that refusal prescribes is
    only checkable if the read-back works.  Two verbs over ONE file must not disagree about it, so
    the carve-out is taken from the SAME predicate ``get_config_value`` branches on rather than
    re-stated here.

    ⚑ Reading an undeclared key is an error under §0 exactly as writing one is, which is why this
    exists at all: ``agent get claude self.model`` refuses instead of answering "(not set)".
    """
    if _is_agent_node_bind_key(f"agent.{node}.{tail}"):
        return None
    return agent_write_key_error(node, tail, verb="read")


def agent_file_identity_only(tail: str) -> bool:
    """True iff *tail* is a per-agent FILE-identity field and NOT a declared key (spec §0).

    THE IDENTITY RESIDUE :func:`agent_key_reason` admits by allowlist, asked as its own
    question because the ``agent`` noun's ``set`` has to ACT on it: a declared leaf is written
    through ``config_interface.set_config_value``, the ONE setter every noun shares, and this is
    the tail for which that setter has no key to route — so it goes to the file boundary
    directly.  ⚑ THAT IS NOT A CARVE-OUT: an undeclared key would be a §0 breach, and ``name``
    is not a key at all — it is a field of :class:`~kanibako.settings.agent_config.AgentConfig`,
    live, written and displayed since long before the keyspace closed.

    ⚑ DERIVED, NEVER LISTED (P13).  ``name`` is the whole of it today only because ``run_args``
    — the other identity field — is ALSO a declared §2d leaf; a leaf entering or leaving either
    set moves this answer with no edit here.  The vocabulary is the EFFECTIVE one (core ∪
    plugin-declared), the same set :func:`_is_persona_agent_key` routes on, so the two cannot
    disagree about which tails the shared setter claims.
    """
    from kanibako.settings.agent_config import IDENTITY_KEYS

    return tail in IDENTITY_KEYS and tail not in _PERSONA_STATE_LEAVES


#: How a user SPELLS the STORED view at each file scope's own noun — the sibling of
#: :data:`_SCOPE_READ_COMMAND`, keyed the same way (:class:`ConfigLevel`'s own value).
#: It is the ONE surface on which an entry the keyspace does not declare is visible,
#: which is why :func:`scope_read_key_error` names it: the cure for such an entry is a
#: hand edit, and nobody can hand-edit a line they were never shown.
_SCOPE_SHOW_COMMAND = {
    "system": "kanibako system show",
    "workset": "kanibako workset show <workset>",
    "box": "kanibako box show <box>",
}


def scope_key_reason(canonical: str) -> str | None:
    """The §0 reason *canonical* names no key, or ``None`` when it does.

    ⚑ ONE CONSTRUCTION, TWO CONSUMERS — the file-scope nouns' READ gate
    (:func:`scope_read_key_error`) and the STORED view's undeclared-entry block
    (``config_interface.show_config``).  The rule is one, so neither restates it.

    ⚑⚑ ``key_validity`` VIA ``key_reason``, NEVER ``is_known_key``, for exactly the reason
    :func:`agent_key_reason` gives: ``is_known_key`` answers *"is this key-SHAPED, as opposed
    to a project name"*, and §0 asks *"is this a DECLARED key"*.  The two disagree on SEVEN
    declared keys (the six bind-shaped category terminals and ``<scope>.masks``), which is why
    NO read gate is on ``is_known_key`` any more: ``system get`` carried one until 2026-08-28
    and refused all seven by name.
    ``key_reason`` also unions the PLUGIN-declared agent leaves, without which a legitimate
    ``pref.agent.goose.provider`` would be refused.
    """
    from kanibako.settings.settings_prefs import default_valid_agents, key_reason

    return key_reason(canonical, valid_agents=default_valid_agents())


def table_leaf_read_cure(canonical: str, active_agent: str | None = None) -> str | None:
    """Where a BARE table-valued agent leaf is actually readable, or ``None`` (spec §2d).

    ⚑ THE READ HALF of :func:`agent_leaf_table_error`, which owns the write half.  Both
    exist for the same §0 reason — a DECLARED key must be refused by its own rule, never
    degraded to "not a key" — and the shapes match: the write verbs refuse the bare
    spelling and name the file to edit, so the read verb refuses it and names the noun
    that answers.  ``config_keys``' own :data:`KNOWN_CONFIG_KEYS` comment already promised
    this ("so the READ gate admits it and the refusal can name the shape instead of
    denying the key exists"); until this, only the write half kept it.

    ⚑ THE BARE SPELLING ONLY (``canonical`` IS the leaf).  ``agent.<node>.<leaf>`` is not
    refused by this gate at all, and a refusal of ``agent.<bogus>.transform_settings``
    is about the NODE — appending a shape cure to it would answer a question the user
    did not ask.

    ⚑ It replaces the generic "your settings file may carry this entry" cure rather than
    following it: that cure prescribes a HAND DELETION, and the entry this key names is
    legitimate one scope up.  The head of the message — the key, then the §0 reason — is
    untouched, for the reason ``settings_assemble._parse_naming_file`` states: the key
    must stay the first thing the user reads on ``cli.main``'s ``Error: {e}`` line.
    """
    if canonical not in TABLE_VALUED_AGENT_LEAVES:
        return None
    agent = display_agent_ref(active_agent) if active_agent else "<agent>"
    return (
        f" '{canonical}' IS a declared agent leaf (spec §2d), but a TABLE-valued one — "
        f"no scalar request can carry it, so it has no bare spelling at a file scope. "
        f"Read it at the agent noun: 'kanibako agent get {agent} {canonical}'."
    )


def agent_category_read_error(canonical: str, key: str) -> str | None:
    """Why a FILE-SCOPE ``get`` cannot serve ``agent.<node>.<category>``, or ``None`` (spec §2a).

    ⚑ THE OTHER HALF OF :func:`table_leaf_read_cure`'s JOB, for the family that half does not
    cover.  Both answer one question — *a declared key this noun has no read for: where IS it
    readable?* — and both end in the same sentence, because the answer is the same noun.  What
    differs is the reason: a bare table leaf has no spelling here at all, while THIS spelling is
    a perfectly good key whose VALUE lives in a file the file-scope nouns never open.

    ⚑⚑ MEASURED, 2026-08-28, and this is why it exists: with an ``agents/claude/agent.yaml``
    carrying all seven category tables, ``kanibako system get agent.claude.caches`` answered
    "(not set)" at rc 0 — a fabricated answer over a table that IS there, which §0 forbids in
    the same breath as the undeclared read.  ``key_validity`` DECLARES the key, so the closed
    keyspace has no complaint and cannot be the thing that catches it.

    ⚑ THE ``default`` TIER IS NOT THIS, AND MUST NOT BE SWEPT IN.  ``agent.default.<category>``
    is the any-agent tier: it is stored in the SYSTEM settings file, ``get_config_value`` reads
    it there, and ``kanibako system get agent.default.caches`` returns the map (measured beside
    the case above).  Refusing it would break a working read, and the cure would be a lie —
    there is no ``agents/default/agent.yaml`` and ``kanibako agent get default …`` exits 1 on
    "agent 'default' not found".

    ⚑ DERIVED (P13): the family is ``settings_keyspace.is_terminal_category_key``, the
    declaration SoT for "dest-keyed terminal category, at the position a scope ends" — so a
    category entering or leaving §2a moves this refusal with it, and the ``agent.<node>``
    -vs-file-scope split is the one that predicate already makes.
    """
    from kanibako.settings.settings_keyspace import is_terminal_category_key

    if not is_terminal_category_key(canonical):
        return None
    scope, _, tail = canonical.partition(".")
    if scope != "agent":
        return None
    node, _, category = tail.partition(".")
    if node == AGENT_DEFAULT_SUB:
        return None
    shown_node = display_agent_ref(node)
    # ⚑ THE MESSAGE MUST NOT SPELL THE STRING "(not set)", however tempting — the refusal
    # tests across this suite assert that literal is ABSENT from a refusal's output, which
    # is how a fabricated answer is caught. Describing the fault beats quoting it.
    return (
        f"Error: '{key}' cannot be read here: a per-agent category table lives in that "
        f"agent's own settings file (agents/{shown_node}/agent.yaml), which this noun does "
        f"not read — reporting it unset here would invent an answer over a table that "
        f"exists (spec §2a). Read it at the agent noun: "
        f"'kanibako agent get {shown_node} {category}'."
    )


def foreign_scope_read_error(
    canonical: str, key: str, command_scope: "ConfigLevel | None",
) -> str | None:
    """Why a declared key belongs to a scope THIS noun cannot answer for, or ``None``.

    ⚑⚑ JEI'S RULING, 2026-08-28: *"i dont see any justification for crossscope 'get'. it makes
    no sense at the cli."*  A ``get`` reads what its own noun answers for; asking the ``system``
    noun for ``box.caches`` is asking a question the CLI has no reason to answer.

    ⚑⚑ WHAT THE RULE IS NOT: "the key's scope must equal the noun's scope", flat.  MEASURED
    2026-08-28 — that gate refuses **86** reads that answer today (29 at ``system``, 23 at
    ``workset``, 34 at ``box``), because a DOWNWARD DEFAULT is spelled with the TARGET's scope
    token while living in THIS noun's file.  ``kanibako system set box.image=…`` is a legal
    containment write (spec §0/§2a), and refusing ``system get box.image`` would leave a write
    with no read-back at the noun that performed it.

    ⚑⚑⚑ THREE BASES WERE TRIED.  TWO ARE DEAD, AND BOTH LOOK RIGHT UNTIL MEASURED — which is
    why the measurements are kept here rather than compressed to the verdict.

    1. **THE WRITE ROUTE** (``has_no_cli_write_route``) — REJECTED, though it selects exactly
       the right rows today.  It derives a ``get`` gate from whether ``set`` works, and Jei has
       held the two verbs apart deliberately: *"set is different tho"*, and *"i did not say
       anything about set. set is different, i said, specifically."*  Right rows, wrong reason:
       if ``set``'s rules move, ``get`` would follow for a reason that has nothing to do with
       reading.
    2. **"DOES THIS NOUN'S OWN FILE CARRY THE KEY?"** — REJECTED BY MEASUREMENT, and this is the
       one a future reader is most likely to re-derive.  IT CANNOT DISTINGUISH ANYTHING: a
       higher tier carrying a lower scope's key IS the cascade.  A ``box.<category>`` table
       authored in the SYSTEM settings file is not inert — it reaches the box at launch.  Built
       through ``build_launch_snapshot`` → ``snapshot_category_entries`` with the system file as
       the ONLY file supplied, every category at every scope token came through
       (``caches``/``seeded``/``common``/``synced``/``bindings.ro``/``bindings.rw``/``masks``,
       × ``system``/``box``/``workset`` — 21 for 21), e.g.
       ``CategoryEntry(category='caches', scope='box', box_dest='/home/agent/.dflt',
       host_src='/host/dflt', delivery='MOUNT')``.  So the system file legitimately carries
       ``box.caches``, this test answers YES for it, and the gate would let the very read the
       ruling forbids straight through.
    3. **THE FRAGMENT BASIS** — ADOPTED, and it is ``get``-native: it is about what a READ
       MEANS and never mentions ``set``.  A terminal category key's value is **per-entry
       cascade-merged across tiers** (spec ``:1085``, *"per-ENTRY cascade merge"*), so any ONE
       tier's copy is a FRAGMENT, never the key's value.  Read at its OWN noun that fragment is
       a complete statement of that scope's contribution — which is exactly R-9's *"Refuse the
       write; keep the read honest"*, and why ``system get system.caches`` stays.  Read at a
       FOREIGN noun it is a partial map no box ever sees.  A SCALAR has no such problem: one
       tier holds one whole value, so ``system get box.image`` is a complete answer and stays.

    ⚑ DERIVED (P13), not hand-listed: the family is ``settings_keyspace.is_terminal_category_key``
    — the same SoT :func:`agent_category_read_error` uses — so a category entering or leaving
    §2a moves this refusal with it.

    ⚑ ``meta.*`` IS THE SECOND ARM, and it is not a scope mismatch at all — it is DERIVED per
    box at launch and stored in no settings file, so no file-scope noun has a value to report.
    It reached this gate the same way the terminals did (``key_validity`` declares it), and
    answering "(not set)" for all 24 would be the same fabrication.
    """
    from kanibako.settings.settings_keyspace import is_terminal_category_key

    if canonical.startswith("meta."):
        return (
            f"Error: '{key}' cannot be read here: 'meta.*' keys are DERIVED per box when it "
            f"launches, not stored in any settings file, so no file-scope noun holds a value "
            f"for one (spec §2c). Use 'kanibako box show <box> --effective', which resolves "
            f"them against a real box."
        )
    scope = canonical.split(".")[0]
    noun = command_scope.value if command_scope is not None else None
    if noun is None or scope == noun or scope not in _SCOPE_READ_COMMAND:
        return None
    if not is_terminal_category_key(canonical):
        return None
    # ⚑ THE MESSAGE STATES THE FRAGMENT REASON, and must NOT say this noun cannot STORE the
    # key — measured false (basis 2 above: the system file carries ``box.caches`` and the
    # launch honours it).  What is true is that storing a fragment is not holding the value.
    return (
        f"Error: '{key}' cannot be read at the '{noun}' noun: it is a declared {scope}-scope "
        f"key whose value is merged entry by entry across tiers, so this noun holds at most a "
        f"fragment of it and never the value (spec §2a). Read it with "
        f"'{_SCOPE_READ_COMMAND[scope]} {key}'."
    )


def scope_read_key_error(
    key: str,
    command_scope: "ConfigLevel | None",
    *,
    active_agent: str | None = None,
) -> str | None:
    """Why ``<noun> get <key>`` names no key, or ``None`` when it does (spec §0).

    THE CLOSED-KEYSPACE READ GATE for the file-scope nouns, the twin of
    :func:`agent_read_key_error`.  Without it ``box get <box> nonsense`` printed "(not set)"
    at rc 0 — a silent accept of an undeclared name, which §0 forbids in the same breath as
    the write: *"reading, setting, or resolving an undeclared key is an ERROR that NAMES the
    offending key"*.

    ⚑ IT JUDGES THE KEY THE ENGINE WILL ACTUALLY READ, which is two transforms deep:
    ``resolve_key`` first, then the BOX-scope bare-agent redirect ``get_config_value`` applies
    before it reads anything.  Judging the raw spelling instead refuses ``box get <box> model``
    — a legal read — on the true-but-irrelevant ground that ``model`` is not a namespace.

    ⚑ The bind-shaped carve-out is taken from the SAME predicate ``get_config_value`` branches
    on, as :func:`agent_read_key_error` takes its own: ``<scope>.bindings.{ro,rw}.<name>`` and
    ``<scope>.{caches,seeded,common,synced}.<name>`` are NOT declared keys, and §0 keeps them
    READABLE anyway — *"Refuse the write; keep the read honest"* — because a hand-authored
    entry reporting "(not set)" is worse than the lost write route.  ``<scope>.masks.<anything>``
    is deliberately NOT in it: ``masks`` never had entry names, so §0 gives it the generic
    refusal and this gate is where that refusal happens.

    ⚑ LAST of the handlers' guards, never first.  ``bare_env_retired_error`` and
    ``bare_agent_key_scope_error`` refuse a RECOGNISED spelling by name and hand back a cure;
    a generic "not a key" arriving before either one would overwrite the cure with less truth.
    """
    canonical = resolve_key(key)
    redirect = box_agent_redirect_key(canonical, command_scope, active_agent)
    if redirect is not None:
        canonical = redirect
    # ⚑ THE SPELLINGS THE SYSTEM NOUN ITSELF SERVES, admitted here so the gate cannot refuse
    # an honest read.  Both terms are taken from the predicate ``get_config_value`` branches
    # on, exactly as the bind carve-out below and :func:`agent_read_key_error` take theirs —
    # the question is "does THIS noun's read serve this spelling", never "does the name look
    # declared".  ⚑ SYSTEM-SCOPED, and deliberately not widened past it: what box and workset
    # print for these spellings is settled elsewhere — the redirect just above for box, the
    # handler's ``bare_agent_key_scope_error`` for workset — so dropping the scope test here
    # would silently change two other nouns' output to fix a third.  Any such widening is its
    # own change, with its own measurement.
    #   · ``_is_agent_setting`` — a BARE agent leaf IS the any-agent tier's key at this noun
    #     (``system set model=opus`` writes ``agent.default.model``).  It is SCALAR-only BY
    #     CONSTRUCTION (``SCALAR_AGENT_LEAVES``), so the one leaf it withholds is the
    #     TABLE-valued one — which is the point: ``transform_settings`` falls through to the
    #     refusal below and gets the address cure, as at the other two nouns (spec §2d).
    #   · ``_is_agent_node_bind_key`` — R-9's *"the read survived the write, on purpose"*;
    #     a hand-authored ``agent.<node>.bindings.{ro,rw}.<name>`` reads back, and that
    #     read-back is the only way to check the hand edit the write refusal prescribes.
    #     ⚑ THE NARROW PREDICATE, NOT ``_is_agent_scope_bind_key``: the wide one also spans
    #     ``caches``/``synced``/… , which the engine does NOT serve — a hand-authored entry
    #     there reads "(not set)", so admitting it would re-fabricate the answer this gate
    #     exists to stop.  MEASURED, not assumed (2026-08-27).
    # ⚑ Derived, never listed (P13): a new scalar leaf is admitted, a new table leaf refused.
    if command_scope is ConfigLevel.system and (
        _is_agent_setting(canonical) or _is_agent_node_bind_key(canonical)
    ):
        return None
    if _is_path_category_key(canonical) or _is_scope_bind_key(canonical):
        return None
    reason = scope_key_reason(canonical)
    if reason is None:
        # ⚑ DECLARED — and still not readable HERE, in two families.  The closed keyspace has
        # nothing to say about a key it declares, so the fabricated-answer half of §0 needs
        # its own tests; both return ``None`` for every key this noun can actually serve.
        # ⚑ THE AGENT ARM FIRST: ``agent.<node>.caches`` is not a scope MISMATCH (``agent`` is
        # not a file scope), and its cure names a different noun than the other arm's.
        return agent_category_read_error(canonical, key) or foreign_scope_read_error(
            canonical, key, command_scope,
        )
    shown = _SCOPE_SHOW_COMMAND.get(
        command_scope.value if command_scope is not None else "",
    )
    # ⚑ The ADDRESS cure wins where there is one: a declared key refused only because
    # this scope has no spelling for it is told WHERE it lives, not offered a deletion.
    cure = table_leaf_read_cure(canonical, active_agent) or (
        f" If your settings file carries this entry, '{shown}' lists it as "
        f"undeclared; removing it means editing that file by hand."
        if shown else ""
    )
    # ⚑ *key* AS TYPED, not the canonical form: §0 asks the error to name the OFFENDING key,
    # and the user can only act on the string they wrote.  It is also the ``+`` spelling, so
    # ``℘`` cannot reach a message by this route.
    return f"Error: '{key}' cannot be read: {reason}.{cure}"


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
        # ⚑ THE MARKER ALONE, not ``is_config_file_only_key`` (2026-08-23): that
        # predicate also answers True for ``config.*``, which set/reset refuse in the
        # PREAMBLE — and a preamble guard is not a dispatch branch, per the rule above.
        or canonical == SETUP_MARKER_KEY
        or canonical in _KEY_ROUTES
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
