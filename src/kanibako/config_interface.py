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

import sys
from dataclasses import fields
from enum import Enum
from pathlib import Path
from typing import Any

from kanibako.config import (
    _DEFAULTS,
    coerce_bool,
    load_merged_config,
    load_project_overrides,
    read_agent_settings,
    unset_project_config_key,
)
from kanibako.config_io import dump_doc, load_doc
from kanibako.errors import UserCancelled
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
    # Start mode / agent flags
    "start_mode",
    "autonomous",
    "model",
    # endpoint (persona): alternate harness base-URL, a sibling of model (block B).
    "endpoint",
    # Box
    "box.image",
    "box.agent_name",
    "box.share_images",
    "box.shell",
    "box.bootstrap_program",
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
    # ``config set``. The on-disk [project].mode write/read (write_project_meta /
    # read_project_meta) — the bootstrap identity write + detection input — stays.
    # Vault
    "vault.enabled",
    "vault.ro",
    "vault.rw",
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
    # Helpers
    "allow_helpers",
})

# Prefixes for dynamic keys (env vars, resources).
DYNAMIC_PREFIXES: tuple[str, ...] = ("env.", "resource.")


def is_known_key(arg: str) -> bool:
    """Return True if *arg* looks like a config key (not a project name)."""
    if arg in KNOWN_CONFIG_KEYS:
        return True
    if any(arg.startswith(p) for p in DYNAMIC_PREFIXES):
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
# crashed H1).  A key absent from here (and not env./resource./agent.*/
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
    "box.bootstrap_program": (("box",), "bootstrap_program"),
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
    # Project section ([project] table) — vault.* are read back by
    # read_project_meta(); vault.enabled lands in its real stored key
    # ``enable_vault`` (the H1 alias fix).
    # ``mode`` removed from the settable routing table (block B1, spec §2b L486 /
    # §0 meta-RO): the project mode is the RO identity anchor ``meta.box.mode``,
    # set by the bootstrap layer at box creation, never via ``config set``. The
    # on-disk [project].mode write/read (write_project_meta / read_project_meta)
    # — bootstrap identity + detection input — is untouched.
    "vault.enabled": (("project",), "enable_vault"),
    "vault.ro": (("project",), "vault_ro"),
    "vault.rw": (("project",), "vault_rw"),
    # Top-level scalar fields (flat KanibakoConfig fields).
    "allow_helpers": ((), "allow_helpers"),
}

# Keys whose values must be coerced to a real type before writing (the H2 fix).
# Boolean keys parse true/false/1/0/yes/no (case-insensitive) to a Python bool
# so the loader reads back a real bool (``set box.share_images false`` actually
# disables it).  Build this extensibly — later phases add
# vault_enabled / agent.*.{auto_approve,allow_helpers} etc.  The truth table
# itself lives in ``config`` (shared with the box.meta writer); see
# ``config.coerce_bool``.
KEY_TYPES: dict[str, str] = {
    "box.share_images": "bool",
    "allow_helpers": "bool",
    "system.auth.share_allowed": "bool",
    "workset.auth.share_allowed": "bool",
    "workset.auth.global_sync": "bool",
    "box.auth.global_enabled": "bool",
    "box.auth.workset_enabled": "bool",
    "vault.enabled": "bool",
}


def _coerce_value(canonical: str, value: str) -> object | str:
    """Coerce *value* to the typed form declared for *canonical* in KEY_TYPES.

    Returns the typed Python value (e.g. a real ``bool``) on success, or an
    ``"Error: ..."`` string when a bool key is given an unparseable value.
    Scalars (no KEY_TYPES entry) pass through unchanged as the raw string.
    """
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


def parse_config_arg(arg: str | None) -> tuple[ConfigAction, str, str]:
    """Parse a single positional config argument.

    Returns ``(action, key, value)``.

    - ``"key=value"`` → ``(set, key, value)``
    - ``"key"``       → ``(get, key, "")``
    - ``None``        → ``(show, "", "")``
    """
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

    Config keys are already canonical (dot-notation like ``box.image`` or
    ``vault.enabled``, or a raw flat key); this is the single canonicalization
    seam every get/set/reset path routes through.
    """
    return raw


def _is_env_key(key: str) -> bool:
    return key.startswith("env.")


def _is_resource_key(key: str) -> bool:
    return key.startswith("resource.")


def _is_agent_setting(key: str) -> bool:
    """Keys that belong in the agent section of settings.yaml."""
    return key in {"model", "start_mode", "autonomous", "endpoint"}


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
    and the Layer-2 ``[system]`` path settings (``system.*``, spec §2g) — both
    live in ``kanibako_config.yaml`` and are structural (file-only).

    ``system.default_agent`` is EXCLUDED — it is a SETTING, not a config path,
    and is handled by :func:`_is_default_agent_key` before this check.

    A ``system``-scope CATEGORY key (``system.caches.x`` / ``system.bindings.*`` /
    ``system.seeded.*`` / …) is ALSO excluded: categories exist at every scope
    INCLUDING system (spec §2a — e.g. global ``system.caches``), so a system-scope
    category repoint must reach the source-only ``config set`` path, NOT the
    structural file-only refusal. (Their dotted shape only LOOKS like a
    ``system.*`` config key.)
    """
    if key.startswith("config."):
        # Still consulted on the READ/show path. The set/reset paths now
        # short-circuit config.* earlier with the ruled refusal (block B2), so this
        # branch no longer reaches _system_key_refusal for a config.* set/reset.
        return True
    if not key.startswith("system.") or _is_default_agent_key(key):
        return False
    return not _is_path_category_key(key)


def _system_key_refusal(key: str, config_path: Path) -> str:
    """Error string refusing a CLI write to a FILE-ONLY ``system.*`` config key.

    ``system.*`` keys are STRUCTURAL config (layout), not behavior settings, so
    they are file-only: editable in the config file (or via ``kanibako setup``)
    but never via ``config set``/``--reset``.  Points the user at the file.
    """
    return (
        f"Error: '{key}' is a structural config key and is not settable from "
        f"the CLI. Edit the config file directly:\n  {config_path}\n"
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

    The path is RENDERED (JC-B2-1) so a non-default ``$XDG_CONFIG_HOME`` shows the
    user's real file. But this is an ERROR path — it must never itself raise: if
    XDG/``$HOME`` resolution fails (``xdg`` falls back to ``Path.home()``, which
    raises when ``$HOME`` is unset), fall back to the documented literal default
    rather than turning a clean refusal into a traceback.
    """
    from kanibako.config import config_file_path
    from kanibako.paths import xdg

    try:
        config_file: Path | str = config_file_path(xdg("XDG_CONFIG_HOME", ".config"))
    except Exception:
        config_file = "~/.config/kanibako_config.yaml"
    verb = "changed" if action == "reset" else "set"
    return (
        f"Error: config.* keys can only be {verb} by editing the configuration "
        f"file ({config_file})."
    )


def _is_path_category_key(key: str) -> bool:
    """True iff *key* is a PATH-TUPLE category key settable via ``config set``.

    The source-only RAW repoint (spec §2a / design §6d / S24) applies to the
    bind-shaped categories ONLY — ``bindings.{ro,rw}`` / ``caches`` / ``seeded`` /
    ``shared`` / ``synced`` (a 2-/3-element ``[host_src, box_dest[, options]]``
    tuple). ``env`` (scalar) is routed by the earlier ``_is_env_key`` branch;
    ``masks`` (a keyed list) is YAML-only (spec §2a L216) and is NOT matched here.
    """
    from kanibako.settings_categories import BIND_KEY_RE

    return BIND_KEY_RE.match(key) is not None


# ---------------------------------------------------------------------------
# Scope-direction guard (block B4, spec §0 directional view/set + §2a)
# ---------------------------------------------------------------------------

# The recognized SCOPE namespaces a key may live in (its TOP-LEVEL dotted token).
# A key whose first segment is NOT one of these (``env.*`` / ``resource.*`` and
# the un-prefixed scalars ``model`` / ``start_mode`` / ``autonomous`` /
# ``allow_helpers`` / ``vault.*``) is SCOPELESS — it always writes to the command
# scope's OWN file, so the direction guard does not apply to it. ``config`` is a
# real namespace (config.* keys exist) but no config.* key actually REACHES this
# guard: set/reset short-circuit config.* earlier with the file-only refusal (B2).
_SCOPE_NAMESPACES: frozenset[str] = frozenset({
    "system", "agent", "workset", "box", "config", "meta",
})

# Which key-scope namespaces a COMMAND scope is allowed to WRITE (spec §0:
# a scope writes ONLY its OWN namespace; ``meta.*`` is RO everywhere). ``config.*``
# is NOT writable from ANY command scope (block B2 — it is bootstrap/file-only and
# is refused BEFORE this guard, so it appears in no allow-set; the older JC-B4-1
# "system owns config.*" rule is superseded). ``box.agent.*`` (the §2b B5
# downward-tweak mirror) is the BOX namespace — the guard keys on the TOP-LEVEL
# token (``box``), so ``box set box.agent.X`` is a legal SAME-scope write.
_SCOPE_WRITE_ALLOWED: dict[ConfigLevel, frozenset[str]] = {
    ConfigLevel.system: frozenset({"system"}),
    ConfigLevel.agent: frozenset({"agent"}),
    ConfigLevel.workset: frozenset({"workset"}),
    ConfigLevel.box: frozenset({"box"}),
}


def _scope_direction_error(
    canonical: str, command_scope: "ConfigLevel | None"
) -> str | None:
    """Enforce the §0 directional-WRITE rule for ``config set`` (block B4).

    A ``config set`` writes ONLY keys in the command scope's OWN namespace;
    writing a CONTAINING (or any other) scope's key is REFUSED (spec §0
    "Directional view/set" + §2a "Scope-direction guard"). ``meta.*`` is a
    TOP-LEVEL read-only namespace — refused from EVERY scope.

    Returns an ``Error: …`` string when the write is REFUSED, or ``None`` when it
    is permitted (so the caller proceeds to dispatch).

    *command_scope* is the scope the ``config set`` was issued at (threaded by
    each caller; see the 4 command handlers). When ``None`` the guard is skipped
    (no command-scope context available — preserves callers that do not supply
    one).

    The guard keys on the key's TOP-LEVEL dotted token. A SCOPELESS key
    (``env.*`` / ``resource.*`` / the un-prefixed scalars) is always permitted —
    it writes to the command scope's own file by construction.
    """
    key_scope = canonical.split(".", 1)[0]
    if key_scope not in _SCOPE_NAMESPACES:
        # Scopeless key (env.*, resource.*, model, vault.*, …) — own-file write.
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
        f"{command_scope.value} scope. A config set writes only keys in its own "
        f"scope's namespace (spec §0). Set it at the {key_scope} scope instead."
    )


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
    """
    from kanibako.paths import resolve_xdg, xdg
    from kanibako.settings_resolve import ResolveCtx

    xdg_vars: dict[str, str] = {
        "XDG_DATA_HOME": str(xdg("XDG_DATA_HOME", ".local/share")),
        "XDG_CONFIG_HOME": str(xdg("XDG_CONFIG_HOME", ".config")),
        "XDG_STATE_HOME": str(resolve_xdg("XDG_STATE_HOME", ".local/state")),
        "XDG_CACHE_HOME": str(resolve_xdg("XDG_CACHE_HOME", ".cache")),
        "XDG_RUNTIME_DIR": str(resolve_xdg("XDG_RUNTIME_DIR", None)),
    }
    return ResolveCtx(
        agent_name=None,
        workset_name=None,
        host_home=str(Path.home()),
        xdg=xdg_vars,
        config=config or {},
    )


def _category_resolves(
    config_path: Path,
    *,
    canonical: str,
    system_path: Path | None = None,
    agent_path: Path | None = None,
    workset_path: Path | None = None,
    box_path: Path | None = None,
    agent_name: str = "",
):
    """Build the E3 RESOLUTION probe (Q9, spec §2a) for a category ``config set`` at
    *config_path* (the COMMAND-scope file).

    Builds the FULL merged cascade snapshot for the command's TARGET ONCE via the
    committed pipeline (``assemble_levels`` → ``merge`` — single-source, NOT
    re-implemented), then returns ``resolves(key, value)``: it applies the candidate
    RAW *value* (the new ``host_src``) at *key* into a FRESH copy of the merged
    snapshot, lenient-``expand``s it (collect-not-raise), and returns the edited
    key's defect reason (BLOCK) or ``None`` (ALLOW) — the E3 test "does the edited
    value resolve cleanly post-edit?".

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

    ctx = _set_time_ctx(config=config_foundation)

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
        _set_leaf(candidate, key.split("."), value)
        result = expand(candidate, ctx, collect_errors=True)
        assert isinstance(result, tuple)  # lenient mode → (snapshot, errors)
        errors = result[1]
        if key not in errors:
            return None
        return errors[key]

    return resolves


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
) -> str:
    """Validate + RAW-repoint a path-tuple category key (S24/S25, spec §2a).

    Runs ``validate_config_set`` (Error refuses, Warn proceeds-with-message, OK
    silent) BEFORE the write, then ``repoint_host_src`` (swaps host_src, preserves
    box_dest+opts RAW, key-MUST-exist). The WARN message is surfaced to the user
    AND the set proceeds. A ``ConfigSetError`` (key absent / non-tuple value) is
    returned as an ``Error:`` string (the CLI prints it to stderr + exit 1).

    The cascade kwargs (*system_path* / *agent_path* / *workset_path* / *box_path* /
    *agent_name*) are plumbed straight to :func:`_category_resolves` so the E3 probe
    resolves the edited value against the FULL launch cascade (Jei (b), 2026-06-29) —
    a cross-scope ``@``-ref no longer false-blocks.
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

    verdict = validate_config_set(
        canonical,
        value,
        is_category=True,
        resolves=_category_resolves(
            config_path,
            canonical=canonical,
            system_path=system_path,
            agent_path=agent_path,
            workset_path=workset_path,
            box_path=box_path,
            agent_name=agent_name,
        ),
        host_exists=_host_exists,
    )
    if isinstance(verdict, Error):
        return f"Error: {verdict.message}"

    try:
        repoint_host_src(config_path, canonical, value)
    except ConfigSetError as exc:
        return f"Error: {exc}"

    confirm = f"Set {canonical} host source to {value}"
    if isinstance(verdict, Warn):
        return f"{confirm}\nWarning: {verdict.message}"
    return confirm


def _dot_to_flat(key: str) -> str:
    """Convert ``vault.enabled`` to ``enable_vault``, etc."""
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
    """
    canonical = _resolve_key(key)

    # env.* keys — read from env files
    if _is_env_key(canonical):
        env_name = canonical[4:]  # strip "env."
        merged = merge_env(env_global, env_project)
        return merged.get(env_name)

    # resource.* keys — read from resource_overrides in settings.yaml
    if _is_resource_key(canonical):
        resource_name = canonical[9:]  # strip "resource."
        if project_toml and project_toml.exists():
            data = load_doc(project_toml)
            overrides = data.get("resource_overrides", {})
            return str(overrides.get(resource_name, "")) or None
        return None

    # target settings (model, start_mode, autonomous)
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
    # system settings tier: ``@config.settings`` = ``global/settings.yaml``
    # (system_settings_path) for the SYSTEM scope, else the project/global paths.
    if _is_default_agent_key(canonical):
        sources = (
            (project_toml, system_settings_path)
            if system_settings_path is not None
            else (project_toml, global_config_path)
        )
        for src in sources:
            if src is None or not src.exists():
                continue
            settings = read_agent_settings(src, "default")
            if _DEFAULT_AGENT_LEAF in settings:
                return settings[_DEFAULT_AGENT_LEAF] or None
        return None

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

    # Keys backed by a flat KanibakoConfig field use the merged config so
    # defaults + inheritance apply (box.*, allow_helpers, box.share_images).
    flat = _dot_to_flat(routed)
    cfg = load_merged_config(global_config_path, project_toml)
    valid = {fld.name for fld in fields(cfg)}
    if flat in valid:
        val = getattr(cfg, flat)
        if isinstance(val, bool):
            return str(val).lower()
        return str(val) if val else None

    # Keys with no flat field (vault.*, *.auth.*) land in [project]/nested — read
    # the raw set-value from the routed location. (``mode`` is no longer a settable
    # key — it is the RO identity anchor meta.box.mode, block B1.)
    sections, leaf = route
    for src in (project_toml, global_config_path):
        if src is None or not src.exists():
            continue
        node: object = load_doc(src)
        for sec in sections:
            if not isinstance(node, dict):
                node = None
                break
            node = node.get(sec)
        if isinstance(node, dict) and leaf in node:
            v = node[leaf]
            if isinstance(v, bool):
                return str(v).lower()
            return str(v) if v != "" else None
    return None


def set_config_value(
    key: str,
    value: str,
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

    *command_scope* is the scope the ``config set`` was issued at (block B4). It
    drives the §0 directional-write guard (``_scope_direction_error``): a write is
    permitted ONLY for a key in the command scope's OWN namespace; a cross-scope
    write (and any ``meta.*`` write) is REFUSED. When ``None`` the guard is skipped.
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

    # Scope-direction guard (block B4, spec §0 + §2a) — enforced at the TOP, after
    # canonical key resolution and BEFORE any dispatch branch (env / resource /
    # category / system / regular), so EVERY write path is gated uniformly.
    scope_err = _scope_direction_error(canonical, command_scope)
    if scope_err is not None:
        return scope_err

    settings_dest = (
        system_settings_path if system_settings_path is not None else config_path
    )

    # env.* keys
    if _is_env_key(canonical):
        env_name = canonical[4:]
        if env_path is None:
            return f"Error: no env file path for key {canonical}"
        try:
            set_env_var(env_path, env_name, value)
        except ValueError as e:
            return f"Error: {e}"
        return f"Set {env_name}={value}"

    # resource.* keys — write to [resource_overrides]
    if _is_resource_key(canonical):
        resource_name = canonical[9:]
        _write_toml_key(config_path, "resource_overrides", resource_name, value)
        return f"Set resource.{resource_name}={value}"

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
        _write_nested_toml_key(config_path, sections, leaf, value)
        return f"Set {canonical}={value}"

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
        return _set_category_value(
            canonical, value, config_path=config_path,
            system_path=cascade_system_path,
            agent_path=cascade_agent_path,
            workset_path=cascade_workset_path,
            box_path=cascade_box_path,
            agent_name=cascade_agent_name,
        )

    # system.* keys (INCLUDING system.default_agent) — FILE-ONLY host-global
    # config (W1, option (a) narrow scope).  The CLI reads/shows them but
    # refuses to SET them: edit the config file directly, or run
    # ``kanibako setup`` (which writes ``default_agent`` programmatically via
    # write_system_value, bypassing this guard).  ``default_agent`` joins this
    # rule per §Design 1 — it is the host-global default and stays in system.*.
    # (A system-scope CATEGORY key was already routed above — ``_is_system_path_
    # key`` excludes it — so this refusal applies only to true structural keys.)
    if _is_default_agent_key(canonical) or _is_system_path_key(canonical):
        return _system_key_refusal(canonical, config_path)

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
    if sections:
        _write_nested_toml_key(config_path, sections, leaf, typed)
    else:
        _write_toml_key_root(config_path, leaf, typed)
    return f"Set {_dot_to_flat(routed)}={value}"


def reset_config_value(
    key: str,
    *,
    config_path: Path,
    env_path: Path | None = None,
    system_settings_path: Path | None = None,
    command_scope: ConfigLevel | None = None,
) -> str:
    """Remove an override for a single key.  Returns confirmation message.

    *system_settings_path*, when supplied (SYSTEM scope), is where SETTINGS
    (``system.default_agent`` + agent settings) are removed from
    (``@config.settings`` = ``global/settings.yaml``); when None (box/workset)
    they are removed from ``config_path`` as before.

    *command_scope* is the scope the ``config --reset`` was issued at (block B2,
    RESET-GUARD). It drives the §0 directional-write guard
    (``_scope_direction_error``) symmetrically with ``set_config_value``: a reset
    is permitted ONLY for a key in the command scope's OWN namespace; a cross-scope
    reset (and any ``meta.*`` reset) is REFUSED. When ``None`` the guard is skipped.
    """
    canonical = _resolve_key(key)

    # config.* foundation keys are NEVER CLI-resettable (block B2) — same rationale
    # as set (they locate files everything else lands in; hand-edited in the
    # bootstrap config file). Refused FIRST, BEFORE the scope guard, with the ruled
    # message (verb "changed" — a reset is a change, not a "set"), pointing at the
    # SAME config file.
    if canonical.startswith("config."):
        return _config_key_refusal(canonical, action="reset")

    # Scope-direction guard (block B2 RESET-GUARD, mirrors set_config_value's B4
    # guard, spec §0 + §2a) — after config.* forbid and BEFORE any dispatch branch,
    # so every reset path is gated uniformly.
    scope_err = _scope_direction_error(canonical, command_scope)
    if scope_err is not None:
        return scope_err

    settings_dest = (
        system_settings_path if system_settings_path is not None else config_path
    )

    # env.* keys
    if _is_env_key(canonical):
        env_name = canonical[4:]
        if env_path and unset_env_var(env_path, env_name):
            return f"Unset env.{env_name}"
        return f"No override for env.{env_name}"

    # resource.* keys
    if _is_resource_key(canonical):
        resource_name = canonical[9:]
        if _remove_toml_key(config_path, "resource_overrides", resource_name):
            return f"Reset resource.{resource_name}"
        return f"No override for resource.{resource_name}"

    # target settings — reset the any-agent ``agent.default`` tier (SYSTEM scope
    # routes to the system settings file).
    if _is_agent_setting(canonical):
        if _remove_nested_toml_key(settings_dest, ("agent", "default"), canonical):
            return f"Reset {canonical}"
        return f"No override for {canonical}"

    # box.agent.<key> — the box-scoped agent mirror (block B5, spec §2b L380):
    # reset = remove the box-scope override so box.agent.<key> falls back to the
    # mirrored agent.<box.agent_name>.<key> default again. Symmetric with the set
    # branch (same nested box.agent.<key> location in the box settings file).
    if _is_box_agent_key(canonical):
        tail = canonical.split(".")
        sections = tuple(tail[:-1])
        leaf = tail[-1]
        if _remove_nested_toml_key(config_path, sections, leaf):
            return f"Reset {canonical}"
        return f"No override for {canonical}"

    # system.* keys (INCLUDING system.default_agent) — FILE-ONLY host-global
    # config (see set_config_value).  The CLI refuses to RESET them too (for
    # symmetry); edit the config file directly or re-run ``kanibako setup``.
    if _is_default_agent_key(canonical) or _is_system_path_key(canonical):
        return _system_key_refusal(canonical, config_path)

    # Regular config keys — route via the same known-key table as set/get
    # (no get-validated/set-unguarded asymmetry).
    routed = _route_key(canonical)
    route = _KEY_ROUTES.get(routed)
    if route is None:
        return f"Error: unknown config key: {key}"
    sections, leaf = route
    removed = (
        _remove_nested_toml_key(config_path, sections, leaf)
        if sections
        else _remove_toml_key_root(config_path, leaf)
    )
    flat = _dot_to_flat(routed)
    if removed:
        default_val = _DEFAULTS.get(flat, "(none)")
        return f"Reset {flat} (reverts to default: {default_val})"
    return f"No override for {flat}"


def write_system_value(config_path: Path, leaf: str, value: object) -> None:
    """Programmatically write a ``[system] <leaf>`` key to the CONFIG file.

    This is the PROGRAM editing the config file on the user's behalf — it
    bypasses the file-only CLI guard in :func:`set_config_value` (which refuses
    ``system.*`` keys).  Used by ``kanibako setup`` to record host-global values
    (e.g. ``system.setup_completed`` → ``[system] setup_completed``) that the CLI
    deliberately will not let a user SET directly.

    *leaf* is the bare key name under the ``[system]`` table (NOT prefixed with
    ``system.``).  Writes preserve all other config content (read-modify-write
    via :func:`_write_nested_toml_key`).
    """
    _write_nested_toml_key(config_path, ("system",), leaf, value)


def reset_all(
    *,
    config_path: Path,
    env_path: Path | None = None,
    force: bool = False,
    system_settings_path: Path | None = None,
) -> str:
    """Remove all overrides at this config level.  Confirms unless *force*.

    *system_settings_path*, when supplied (SYSTEM scope), is where the SETTINGS
    (the ``agent`` table + ``resource_overrides``) are cleared from
    (``@config.settings`` = ``global/settings.yaml``), while CONFIG overrides are
    cleared from ``config_path``.  When None (box/workset) everything is cleared
    from ``config_path`` as before.
    """
    if not force:
        try:
            confirm_prompt("Remove all config overrides? Type 'yes' to proceed: ")
        except UserCancelled:
            return "Aborted."

    count = 0

    # Clear project-level config overrides (always from config_path).
    overrides = load_project_overrides(config_path)
    for key in overrides:
        unset_project_config_key(config_path, key)
        count += 1

    # Clear target settings + resource overrides.  SYSTEM scope keeps these in
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
        if data.get("resource_overrides"):
            for k in list(data["resource_overrides"]):
                _remove_toml_key(settings_dest, "resource_overrides", k)
                count += 1

    # Clear env file
    if env_path and env_path.is_file():
        env = read_env_file(env_path)
        if env:
            count += len(env)
            write_env_file(env_path, {})

    return f"Reset {count} override(s)." if count else "No overrides to reset."


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
) -> int:
    """Display config values.  Returns exit code.

    - *effective=False*: show only overrides at this level.
    - *effective=True*: show all resolved values including inherited defaults.

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
