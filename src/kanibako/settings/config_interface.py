"""Unified config interface engine — the get/set/show/reset verbs every noun command shares.

**_Argument grammar_**
- ``key=value`` → set · ``key`` → get · no args → show overrides · ``--effective`` → resolved
- ``--null key`` → SET an explicit present-``None``; ``reset key`` is the verb that UNDOES it
- ``reset --all`` → remove all overrides (with confirmation)

⚑ ``reset`` is a sibling VERB (``box reset <key>``), not a ``--reset`` flag — none is defined.
"""

from __future__ import annotations

import sys
from dataclasses import fields
from enum import Enum
from pathlib import Path
from typing import Any

from kanibako.settings.config import (
    load_config,
    load_merged_config,
    load_project_overrides,
    read_agent_settings,
    unset_project_config_key,
)
from kanibako.settings.config_display import (
    _nested_settings_overrides,
    _pref_overrides,
    _print_category_block,
    _print_pref_block,
)
from kanibako.settings.agent_file import (
    AgentFileSlot,
    read_leaf,
    remove_leaf,
    write_leaf,
)
from kanibako.settings.config_dest import (
    DestRoute,
    _write_dest,
    _read_dest,
    noun_settings_file,
    _node_bind_target,
    _node_secret_target,
    _persona_agent_target,
)
from kanibako.settings.config_keys import (
    KEY_TYPES,
    _KEY_ROUTES,
    _SCOPE_WRITE_ALLOWED,
    _SETTINGS_SCOPE_TOKENS,
    _coerce_value,
    _dot_to_flat,
    _is_agent_node_bind_key,
    _is_agent_node_secret_key,
    _is_agent_setting,
    _is_bare_env_key,
    _is_box_agent_key,
    _is_path_category_key,
    _is_persona_agent_key,
    _is_pref_key,
    _is_scope_bind_key,
    _is_scope_env_key,
    _is_scope_secret_key,
    _config_key_refusal,
    _node_secret_display_key,
    _pref_sections_leaf,
    _pref_target_error,
    _pref_write_site_error,
    _probes_at_set_time,
    _persona_display_key,
    _route_key,
    _scope_direction_error,
    access_value_error,
    bare_agent_key_scope_error,
    bare_env_retired_error,
    box_agent_redirect_key,
    box_agent_retired_error,
    is_access_key,
    agent_node_bind_retired_error,
    scope_bind_retired_error,
    scope_env_var_error,
    is_system_path_key,
    system_key_refusal,
    resolve_key,
    ConfigLevel,
)
from kanibako.settings.config_io import (
    dump_doc,
    load_doc,
    read_stored_leaf,
    read_stored_pref,
    remove_nested_key,
    remove_root_key,
    render_stored_scalar,
    write_nested_key,
    write_root_key,
)
from kanibako.errors import UserCancelled
from kanibako.settings.kb_store import __MISSING__
from kanibako.settings.settings_keyspace import key_validity
from kanibako.settings.keystore import ReservedKeyError
from kanibako.settings.settings_prefs import PREF_ROOT
from kanibako.utils import confirm_prompt


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
    """Parse a single positional config argument into ``(action, key, value)``."""
    if set_null:
        return (ConfigAction.set, (arg or "").strip(), None)
    if arg is None:
        return (ConfigAction.show, "", "")
    if "=" in arg:
        key, _, value = arg.partition("=")
        return (ConfigAction.set, key.strip(), value.strip())
    return (ConfigAction.get, arg.strip(), "")


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
) -> str | None:
    """Validate a pref's VALUE against the shape + resolution of its TARGET key."""
    from kanibako.settings.settings_categories import (
        BIND_KEY_RE,
        MASK_KEY_RE,
        SCOPE_BIND_KEY_RE,
    )
    from kanibako.settings.settings_keyspace import is_terminal_category_key

    target = canonical[len(PREF_ROOT) + 1:]
    if value is None:
        return None  # the suppression request — legal at any leaf (§3 / §2h).

    # ⚑ DELIBERATE, DO NOT "FIX": ``pref.system.agent``'s VALUE is not checked against the
    # installed agents — an unknown name surfaces at agent RESOLUTION (P7), not here.

    # ⚑ The auth-critical ``access`` ENUM guard, checked HERE at the TARGET: ``is_access_key``
    # answers False for the ``pref.*`` spelling BY DESIGN, so no other guard sees it.
    if is_access_key(target):
        access_err = access_value_error(canonical, value)
        if access_err is not None:
            return access_err

    # ⚑ EVERY TERM IS LOAD-BEARING — do not drop one as "unreachable". The two regex terms and
    # ``_is_agent_node_bind_key`` name RETIRED per-name spellings a pref may still REQUEST;
    # ``is_terminal_category_key`` is the WHOLE-KEY predicate carrying all six live categories.
    if (
        BIND_KEY_RE.match(target) is not None
        or MASK_KEY_RE.match(target) is not None
        or SCOPE_BIND_KEY_RE.match(target) is not None
        or _is_agent_node_bind_key(target)
        or is_terminal_category_key(target)
    ):
        return (
            f"Error: '{canonical}' targets '{target}', which is a STRUCTURED "
            f"category key — its value is a map keyed by box DESTINATION "
            f"({{<box_dest>: [<host_src>]}}), never a scalar (spec §2a). Write the "
            f"request in the settings file:\n"
            f"  pref:\n"
            f"{chr(10).join('  ' + line for line in _yaml_skeleton(target))}\n"
            f"...or suppress the entry with: --null {canonical}\n"
            f"(that WRITES a suppression; 'reset {canonical}' undoes it)"
        )

    # ⚑ A SCALAR target: the E3 probe runs AT THE TARGET — probing at ``pref.*`` is a NO-OP.
    resolves, _raw = _category_set_lookups(
        config_path,
        canonical=target,
        system_path=system_path,
        agent_path=agent_path,
        workset_path=workset_path,
        box_path=box_path,
        agent_name=agent_name,
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
    """The nested-YAML skeleton for *target*, for a refusal message."""
    from kanibako.settings.settings_keyspace import is_terminal_category_key

    # ⚑ THE LEAF LINE FOLLOWS THE CATEGORY: a terminal dest-keyed category takes a MAP, and
    # printing the retired name-keyed pair form for one would hand back a refused shape.
    parts = target.split(".")
    if is_terminal_category_key(target):
        leaf = (
            "{<box_dest>: true}" if parts[-1] == "masks"
            else "{<box_dest>: [<host_src>]}"
        )
    else:
        leaf = "[<host_src>, <box_dest>]"
    lines = []
    for i, seg in enumerate(parts):
        lines.append("  " * (i + 1) + f"{seg}:" if i < len(parts) - 1
                     else "  " * (i + 1) + f"{seg}: {leaf}")
    return lines


def _host_xdg_map(data_home: "Path | None" = None) -> dict[str, str]:
    """Module-PRIVATE deferred-import delegate to :func:`kanibako.settings.paths.host_xdg_map`."""
    from kanibako.settings.paths import host_xdg_map

    return host_xdg_map(data_home)


def _set_time_ctx(config: "dict[str, str] | None" = None) -> "Any":
    """The :class:`~kanibako.settings.settings_resolve.ResolveCtx` for the set-time E3 probe."""
    from kanibako.settings.settings_resolve import ResolveCtx

    return ResolveCtx(
        agent_name=None,
        workset_name=None,
        host_home=str(Path.home()),
        xdg=_host_xdg_map(),
        config=config or {},
    )


def _path_tier_split() -> "tuple[dict[str, str], dict[str, object]]":
    """The path tier as ``(config_foundation, floor)``, RAISING on failure."""
    # ⚑ THE FAILURE ARM IS THE CALLER'S, DELIBERATELY — do not add a ``try`` here: the two
    # callers disagree about what a failure means and both are right.
    from kanibako.settings.config import config_file_path
    from kanibako.settings.paths import load_system_config, xdg

    floor: dict[str, object] = {}
    config_foundation: dict[str, str] = {}
    user_config = config_file_path(xdg("XDG_CONFIG_HOME", ".config"))
    data_home = xdg("XDG_DATA_HOME", ".local/share")
    for dotted, path in load_system_config(
        user_config, data_home=data_home, home=Path.home(),
    ).items():
        if dotted.startswith("config."):
            config_foundation[dotted] = str(path)
        # ⚑ DECLARED KEYS ONLY, and the filter is not decoration. The path tier's
        # ``system.*`` half is NOT all keyspace: ``resolve_system_paths`` also derives
        # four PRIMARY-workset surrogates (``system._boxes``,
        # ``system._primary_{vault_ro,vault_rw,logs}``) whose ONLY consumers are the
        # ``StandardPaths`` fields built out of the same dict — the manifest classes
        # them ``not_keys.code_residue``, *"appeared in CODE only, never
        # spec-sanctioned"*. Floored unfiltered they became REAL nodes in every
        # resolved store, which is a CLOSED-keyspace breach (spec §0) manufactured by
        # code rather than declared. This is a FILTER, not a silent accept of user
        # input: nothing here comes from a user, and no ``@system._*`` ref exists.
        elif dotted.startswith("system.") and key_validity(
            dotted, valid_agents=(),
        ) is None:
            floor[dotted] = str(path)
    return config_foundation, floor


def _category_set_lookups(
    config_path: Path,
    *,
    canonical: str,
    system_path: Path | None = None,
    agent_path: Path | None = None,
    workset_path: Path | None = None,
    box_path: Path | None = None,
    agent_name: str = "",
):
    """The set-time lookups over ONE merged cascade snapshot: ``(resolves, raw_bind)``."""
    from kanibako.settings.settings_assemble import assemble_levels
    from kanibako.settings.settings_expand import expand
    from kanibako.settings.settings_merge import merge

    # ⚑ A path-tier failure must NOT crash a ``config set`` — fall back to an empty floor.
    try:
        config_foundation, floor = _path_tier_split()
    except Exception:
        config_foundation, floor = {}, {}

    # The agent STORE-ROOT anchor (spec §2d), from the SAME builder the launch floor uses.
    # ⚑ The SECOND anchor (the agent read out of the EDITED KEY) is GONE — do not restore it.
    from kanibako.settings.settings_launch import meta_agent_path_floor

    if agent_name:
        floor.update(meta_agent_path_floor(agent_name))

    ctx = _set_time_ctx(config=config_foundation)

    # ⚑ THERE IS NO SET-TIME FLOOR-REGISTRY FOLD HERE ANY MORE (R-9 / DS-BL1) — do NOT
    # restore it to "fix" a refused bind repoint; the launch-time fold is a different, live one.

    # Place the COMMAND-scope file into its TRUE precedence slot by the edited key's scope token.
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
        # ⚑ The AGENT slot, never the box slot — ``_drop_upward_scopes`` would drop these keys.
        agent_p = cmd if agent_p is None else agent_p
    else:  # box (the default / most-specific scope)
        box_p = cmd if box_p is None else box_p

    # Assemble the FULL cascade with the SAME ``assemble_levels`` the launch uses, then merge.
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
        # Apply the candidate into a FRESH copy (S19), lenient-expand, read the key's defect.
        candidate = _clone_keystore(base_snapshot)
        try:
            _set_leaf(candidate, key.split("."), value)
        except ReservedKeyError as exc:
            # ⚑ A RESERVED leaf name is a set-time DEFECT, not a crash (the H1 never-raises rule).
            return str(exc)
        result = expand(candidate, ctx, collect_errors=True)
        assert isinstance(result, tuple)  # lenient mode → (snapshot, errors)
        errors = result[1]
        if key not in errors:
            return None
        return errors[key]

    def raw_bind(key: str) -> "Any | None":
        # The key's effective RAW tuple in the SAME merged snapshot (F10), via unbound ops (S3).
        from kanibako.settings.kb_store import Bind
        from kanibako.settings.keystore import KeyStore

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
    """Deep-clone a :class:`KeyStore` — nested nodes rebuilt, immutable leaves shared (S19)."""
    from kanibako.settings.keystore import KeyStore

    out = KeyStore()
    for k in dict.keys(store):
        v = dict.__getitem__(store, k)
        out[k] = _clone_keystore(v) if isinstance(v, KeyStore) else v
    return out


def _set_leaf(store: "Any", parts: list, value: object) -> None:
    """Set *value* at the *parts* path in *store*, creating nested KeyStore nodes as needed."""
    from kanibako.settings.keystore import KeyStore

    node = store
    for seg in parts[:-1]:
        existing = dict.get(node, seg, None)
        if not isinstance(existing, KeyStore):
            existing = KeyStore()
            node[seg] = existing
        node = existing
    node[parts[-1]] = value


# ⚑ ``_set_category_value`` and its callee ``settings_configset.repoint_host_src`` are GONE
# (DS-BL1 = (a); QA′ 2026-08-08). Do not reach for either name.


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
    active_agent: str | None = None,
) -> str | None:
    """Read one config value STORED AT THIS NOUN, or ``None`` when it is not set there."""
    canonical = resolve_key(key)

    # A BARE agent behavior key at BOX scope has no readable value of its own — REDIRECT the
    # read to the box's active-agent mirror. WORKSET has no mirror and is refused at the handler.
    _box_agent_redirect = box_agent_redirect_key(
        canonical, command_scope, active_agent,
    )
    if _box_agent_redirect is not None:
        canonical = _box_agent_redirect

    # The NOUN's settings file + the noun's own CONFIG file — the destination rule's two inputs.
    # ⚑ ``get`` is the only verb carrying BOTH as separate parameters, so the mapping onto the
    # shared rule happens here, once.
    noun_file = noun_settings_file(project_toml, system_settings_path)
    own_config = (
        global_config_path if system_settings_path is not None else project_toml
    )

    # ``pref.<target>`` — return the REQUEST stored at this noun (§2h); the RESULT is --effective.
    if _is_pref_key(canonical):
        sections, leaf = _pref_sections_leaf(canonical)
        return read_stored_pref(noun_file, sections, leaf)

    # Bare ``env.*`` — RETIRED (R-39). This engine returns values, never error strings, so the
    # refusal-with-cure lives at the three command handlers; ``None`` keeps a library read honest.
    if _is_bare_env_key(canonical):
        return None

    # ``<scope>.env.<VAR>`` — the LIVE env family, read from the NOUN's settings file.
    if _is_scope_env_key(canonical):
        if noun_file and noun_file.exists():
            parts = canonical.split(".")
            return read_stored_leaf(noun_file, (parts[0], "env"), parts[2])
        return None

    # ``agent.<node>.bindings.{ro,rw}.<name>`` — the per-node DESCRIPTOR bind, read RAW from the
    # node's own ``agents/<node>/agent.yaml``.
    # ⚑ BEFORE the persona branch: a bind NAMED after a state leaf (``…bindings.ro.model``)
    # would otherwise be mis-captured by the persona form.
    # ⚑ THE READ SURVIVED THE WRITE (R-9), on purpose — do not "restore symmetry" by removing it.
    if _is_agent_node_bind_key(canonical):
        bind_target = _node_bind_target(canonical, agents_root)
        if bind_target is None:
            return None
        return read_leaf(bind_target)

    # ``agent.<node>.secret_path.<VAR>`` — the stored PATH, never the secret VALUE (spec §2a).
    # ⚑ BEFORE the persona branch (discriminated node storage).
    if _is_agent_node_secret_key(canonical):
        secret_target = _node_secret_target(canonical, agents_root)
        if secret_target is None:
            return None
        return read_leaf(secret_target)

    # ``<scope>.secret_path.<VAR>`` — the stored PATH from the NOUN's settings file.
    # ⚑ ``noun_file``, NOT ``project_toml``: the SYSTEM handler never threads the latter.
    if _is_scope_secret_key(canonical):
        if noun_file and noun_file.exists():
            parts = canonical.split(".")
            return read_stored_leaf(
                noun_file, (parts[0], "secret_path"), parts[2],
            )
        return None

    # ``agent.<node>.<key>`` — the PER-PERSONA agent key (B1), read from the node's own file.
    if _is_persona_agent_key(canonical):
        target = _persona_agent_target(canonical, agents_root)
        if isinstance(target, AgentFileSlot):
            return read_leaf(target)
        return None

    # Bare agent settings (model, continue_mode, access, allow_helpers).
    if _is_agent_setting(canonical):
        # The agent-agnostic CLI reads the reserved any-agent ``agent.default`` tier.
        setting_src = noun_file
        if setting_src and setting_src.exists():
            settings = read_agent_settings(setting_src, "default")
            if canonical in settings:
                return settings[canonical]
        return None

    # ``box.agent.<key>`` — RETIRED (P7, spec §2b): nothing settable, so nothing to read.
    # ⚑ ``None`` is DELIBERATE — reading a legacy leaf would report a value with no effect.
    if _is_box_agent_key(canonical):
        return None

    # Category keys, ALL READ-ONLY — the RAW value stored at the nested path in the NOUN's file.
    # ⚑ BEFORE the ``system.*`` file-only branch: ``system.caches`` only LOOKS like a config
    # key, and categories are gettable at EVERY scope.
    # ⚑⚑ THERE IS NO WRITE TWIN LEFT to be symmetric with (DS-BL1 = (a) / R-9), and the READ
    # SURVIVES ON PURPOSE — do not delete it to restore symmetry.
    if _is_path_category_key(canonical) or _is_scope_bind_key(canonical):
        # ⚑ Through the SAME rule site the write side uses, which is what makes ``_read_dest``'s
        # one documented divergence from ``_write_dest`` a fact about running code.
        dest = _read_dest(
            canonical, command_scope=command_scope,
            config_path=own_config, settings_path=system_settings_path,
        )
        assert dest is not None  # a category key always has a slot
        return read_stored_leaf(dest.path, dest.sections, dest.leaf)

    # ``config.*`` / ``system.*`` path keys — the raw value from the bootstrap config file.
    # ⚑ ``load_config``, NOT ``load_merged_config``: this tier is CONFIG-FILE-ONLY, and a
    # malformed box settings file must not break a bootstrap-tier read.
    if is_system_path_key(canonical):
        cfg = load_config(global_config_path)
        return cfg.config_paths.get(canonical)

    # Regular config keys — the SAME rule site set/reset write through; unknown → ``None``.
    # ⚑ NEVER ``load_merged_config``: the merged dataclass fabricates the built-in default
    # when the noun stored nothing (the F6 lie) and folds in another tier's value.
    dest = _read_dest(
        canonical, command_scope=command_scope,
        config_path=own_config, settings_path=system_settings_path,
    )
    if dest is None:
        return None
    return read_stored_leaf(dest.path, dest.sections, dest.leaf)


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
) -> str:
    """Write a config value to the appropriate store; returns a message or error, NEVER raises."""
    canonical = resolve_key(key)

    # ⚑ ``config.*`` foundation keys are NEVER CLI-settable (B2) — refused BEFORE the scope
    # guard so every command scope gets the same ruled message.
    if canonical.startswith("config."):
        return _config_key_refusal(canonical, action="set")

    # ⚑ The ``pref.*`` WRITE-SITE guard (§2h) — BEFORE the TARGET filters and the scope guard:
    # a wrong FILE must be reported regardless of the target's quality.
    pref_site_err = _pref_write_site_error(canonical, command_scope, verb="set")
    if pref_site_err is not None:
        return pref_site_err

    # ⚑ Scope-direction guard (B4, spec §0 + §2a) at the TOP, BEFORE any dispatch branch, so
    # EVERY write path is gated uniformly.
    scope_err = _scope_direction_error(canonical, command_scope)
    if scope_err is not None:
        return scope_err

    # A BARE agent behavior key at BOX/WORKSET scope is an UPWARD write the launch DROPS —
    # refuse it HERE, BEFORE the write. Uniform over the whole ``_is_agent_setting`` family.
    bare_err = bare_agent_key_scope_error(
        canonical, command_scope, verb="set",
        active_agent=cascade_agent_name or None,
    )
    if bare_err is not None:
        return bare_err

    # ⚑ Bare ``env.*`` — RETIRED (R-39): refused with the cure BEFORE any write machinery
    # (``--null`` included). The cure is REACHABLE — the scoped arm is routed a few branches below.
    env_err = bare_env_retired_error(
        canonical, verb="set", command_scope=command_scope,
    )
    if env_err is not None:
        return env_err

    # ⚑ A RESERVED VAR in ``<scope>.env.<VAR>`` (spec §0): the shape test deliberately still
    # MATCHES so the message names the rule instead of degrading to "unknown config key".
    env_var_err = scope_env_var_error(canonical)
    if env_var_err is not None:
        return env_var_err

    # ⚑ THE PREAMBLE REFUSAL for the two RETIRED bind write routes (R-9) — BEFORE any write
    # machinery, ``--null`` and the E3 probe included. A retired spelling is refused BY NAME,
    # never degraded to "unknown config key" (§0). The KEYS are not retired; ``get`` reads both.
    scope_bind_err = scope_bind_retired_error(canonical, verb="set")
    if scope_bind_err is not None:
        return scope_bind_err
    node_bind_err = agent_node_bind_retired_error(canonical, verb="set")
    if node_bind_err is not None:
        return node_bind_err

    # ⚑ ``--null`` NEEDS NO EXCEPTION HERE ANY MORE, and the absence is deliberate: the one
    # route that could not express it (the category repoint) is refused by the preamble above.

    # ⚑ Write-time validation for the auth-critical ``access`` key ONLY (Jei) — it routes
    # VERBATIM below, so a typo would otherwise be STORED and re-read at every launch.
    if value is not None and is_access_key(canonical):
        access_err = access_value_error(canonical, value)
        if access_err is not None:
            return access_err

    # ⚑ ``box.agent.<key>`` — RETIRED (P7, spec §2b): refused BY NAME here, in the preamble
    # with the other retired spellings, and NOT further down beside the write branches. The
    # E3 probe below builds a CANDIDATE snapshot with the proposed key spliced into it; run
    # ahead of this refusal it materialises a retired, undeclared key into a real ``KeyStore``
    # before anything judges the name. The keyspace is CLOSED (spec §0) — the name is refused
    # first, and nothing downstream ever sees it. The message and its cure are unchanged.
    if _is_box_agent_key(canonical):
        return box_agent_retired_error(
            canonical, verb="set", active_agent=cascade_agent_name or None,
        )

    # SET-TIME RESOLUTION PROBE for a value the EXPANDER will see (E3, spec §2a / Q9); see
    # :func:`_probes_at_set_time` for which keys qualify. It blocks ONLY on the edited value's
    # own upstream chain, so ``config set`` stays usable to REPAIR a broken config.
    if value is not None and _probes_at_set_time(canonical):
        from kanibako.settings.settings_configset import Error as _SetError
        from kanibako.settings.settings_configset import validate_config_set

        _resolves, _ = _category_set_lookups(
            config_path,
            canonical=canonical,
            system_path=cascade_system_path,
            agent_path=cascade_agent_path,
            workset_path=cascade_workset_path,
            box_path=cascade_box_path,
            agent_name=cascade_agent_name,
        )
        scalar_verdict = validate_config_set(
            canonical, value, resolves=_resolves,
        )
        if isinstance(scalar_verdict, _SetError):
            return f"Error: {scalar_verdict.message}"

    # ``pref.<target>`` — the §2h REQUEST, validated with the SAME filters the launch applies.
    # ⚑ Written NESTED, never as a dotted literal: a dotted bind-shaped value is never
    # bind-parsed, so the two spellings would behave differently (see ``settings_prefs``).
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
        )
        if value_err is not None:
            return value_err
        dest = _write_dest(
            canonical, command_scope=command_scope,
            config_path=config_path, settings_path=system_settings_path,
        )
        assert dest is not None  # the pref family always has a slot
        write_nested_key(dest.file, dest.sections, dest.leaf, value)
        return f"Set {canonical}={'null' if value is None else value}"

    # ⚑ There is NO ``agent.<node>.bindings.{ro,rw}.<name>`` branch here any more (R-9) — its
    # absence is deliberate, and the preamble refusal cannot be out-ordered by a new branch.

    # ``agent.<node>.secret_path.<VAR>`` — a SCALAR path write to the node's OWN settings file
    # at the DISCRIMINATED sub-table. ⚑ BEFORE the persona branch.
    if _is_agent_node_secret_key(canonical):
        secret_target = _node_secret_target(canonical, agents_root)
        if secret_target is None:
            return (
                f"Error: '{key}' is a per-node secret pointer and is only "
                f"settable at the system scope."
            )
        write_leaf(secret_target, value)
        return f"Set {_node_secret_display_key(canonical)}={value}"

    # ``<scope>.secret_path.<VAR>`` — a SCALAR path write to the command scope's SETTINGS file.
    if _is_scope_secret_key(canonical):
        dest = _write_dest(
            canonical, command_scope=command_scope,
            config_path=config_path, settings_path=system_settings_path,
        )
        assert dest is not None  # the scope-secret family always has a slot
        write_nested_key(dest.file, dest.sections, dest.leaf, value)
        return f"Set {canonical}={value}"

    # ``<scope>.env.<VAR>`` — a SCALAR write to the command scope's SETTINGS file, VERBATIM
    # (the set-time E3 probe already ran on it). The AGENT form is routed by the persona branch.
    if _is_scope_env_key(canonical):
        dest = _write_dest(
            canonical, command_scope=command_scope,
            config_path=config_path, settings_path=system_settings_path,
        )
        assert dest is not None  # the scope-env family always has a slot
        write_nested_key(dest.file, dest.sections, dest.leaf, value)
        return f"Set {canonical}={'null' if value is None else value}"

    # ``agent.<node>.<key>`` — the PER-PERSONA key (B1): a VERBATIM write to the node's OWN
    # ``agents/<node>/agent.yaml``, sparse by construction (``write_nested_key`` is RMW).
    if _is_persona_agent_key(canonical):
        target = _persona_agent_target(canonical, agents_root)
        if isinstance(target, str):
            return target  # malformed node ref
        if target is None:
            return (
                f"Error: '{key}' is a per-persona agent setting and is only "
                f"settable at the system scope."
            )
        write_leaf(target, value)
        return f"Set {_persona_display_key(canonical)}={value}"

    # Bare agent settings — the agent-agnostic CLI writes the any-agent ``agent.default`` tier.
    if _is_agent_setting(canonical):
        dest = _write_dest(
            canonical, command_scope=command_scope,
            config_path=config_path, settings_path=system_settings_path,
        )
        assert dest is not None  # the bare-agent family always has a slot
        write_nested_key(dest.file, dest.sections, dest.leaf, value)
        return f"Set {canonical}={value}"

    # ⚑ THERE IS NO ``box.agent.<key>`` BRANCH HERE ANY MORE and its absence is DELIBERATE:
    # the retirement refusal moved UP into the preamble, ahead of the E3 probe, so no write
    # machinery — the probe's candidate store included — ever sees the retired spelling.

    # ⚑ THERE IS NO CATEGORY SET BRANCH ANY MORE (DS-BL1 = (a)) and its absence is DELIBERATE —
    # all six are refused BY NAME in the preamble above. Do NOT "restore" it: re-adding a write
    # route would need a visible spec edit.

    # STRUCTURAL ``system.*`` path-tier keys — FILE-ONLY; the refusal names the config file.
    # ⚑ A precise family check (F2): a ``system.*`` SETTINGS key is never refused here.
    if is_system_path_key(canonical):
        return system_key_refusal(canonical, verb="set")

    # Regular config keys — the single known-key table (H1: an unknown key returns an error
    # string and NEVER raises). Either the canonical dotted spelling or the flat underscore form.
    routed = _route_key(canonical)
    route = _KEY_ROUTES.get(routed)
    if route is None:
        return f"Error: unknown config key: {key}"
    typed = _coerce_value(routed, value)  # the H2 fix (real bool/etc.)
    if isinstance(typed, str) and KEY_TYPES.get(routed):
        # ``_coerce_value`` signalled a parse error (a str only comes back for a typed key).
        return typed
    # A scope-prefixed SETTINGS key lands in the COMMAND scope's SETTINGS file with the key's
    # scope token KEPT — never remapped to the key-scope's own file.
    dest = _write_dest(
        canonical, command_scope=command_scope,
        config_path=config_path, settings_path=system_settings_path,
    )
    assert dest is not None  # ``route`` above proved the routing table claims it
    if dest.sections:
        write_nested_key(dest.file, dest.sections, dest.leaf, typed)
    else:
        write_root_key(dest.file, dest.leaf, typed)
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
) -> str:
    """Remove an override for a single key; returns a confirmation or an error, NEVER raises."""
    canonical = resolve_key(key)

    # ⚑ ``config.*`` foundation keys are NEVER CLI-resettable (B2) — refused FIRST, BEFORE the
    # scope guard, with verb "changed" (a reset is a change, not a "set").
    if canonical.startswith("config."):
        return _config_key_refusal(canonical, action="reset")

    # ``pref.*`` WRITE-SITE guard, symmetric with set (a reset is a WRITE).
    pref_site_err = _pref_write_site_error(canonical, command_scope, verb="reset")
    if pref_site_err is not None:
        return pref_site_err

    # ⚑ Scope-direction guard (B2 RESET-GUARD) — BEFORE any dispatch branch, so every reset
    # path is gated uniformly, exactly as ``set_config_value``'s B4 guard is.
    scope_err = _scope_direction_error(canonical, command_scope)
    if scope_err is not None:
        return scope_err

    # A BARE agent behavior key at BOX/WORKSET scope is REFUSED here, symmetric with set (the
    # model is REFUSE writes, redirect reads — and a reset is a WRITE).
    bare_err = bare_agent_key_scope_error(
        canonical, command_scope, verb="reset",
        active_agent=cascade_agent_name or None,
    )
    if bare_err is not None:
        return bare_err

    # ⚑ Bare ``env.*`` — RETIRED (R-39): refused symmetrically with set, because "No override"
    # would be a lie (the ``.env`` file is not an override store any more).
    env_err = bare_env_retired_error(
        canonical, verb="reset", command_scope=command_scope,
    )
    if env_err is not None:
        return env_err

    # A RESERVED VAR in ``<scope>.env.<VAR>`` — refused symmetrically with set, so a name that
    # can never be written is never reported as merely unset.
    env_var_err = scope_env_var_error(canonical)
    if env_var_err is not None:
        return env_var_err

    # ⚑ The two RETIRED bind routes (R-9) — refused symmetrically with set: "No override for …"
    # would be a lie in BOTH directions.
    scope_bind_err = scope_bind_retired_error(canonical, verb="reset")
    if scope_bind_err is not None:
        return scope_bind_err
    node_bind_err = agent_node_bind_retired_error(canonical, verb="reset")
    if node_bind_err is not None:
        return node_bind_err

    # ``pref.<target>`` — remove the REQUEST from this noun's settings file.
    if _is_pref_key(canonical):
        dest = _reset_dest(canonical, command_scope, config_path, system_settings_path)
        if remove_nested_key(dest.file, dest.sections, dest.leaf):
            return f"Cleared {canonical}"
        return f"No override for {canonical}"

    # ⚑ There is NO ``agent.<node>.bindings.{ro,rw}.<name>`` branch here any more (R-9), and
    # the absence is deliberate — the preamble refuses it BY NAME, symmetrically with set.

    # ``agent.<node>.secret_path.<VAR>`` — remove the stored pointer from the node's OWN file.
    # ⚑ BEFORE the persona branch.
    if _is_agent_node_secret_key(canonical):
        secret_target = _node_secret_target(canonical, agents_root)
        if secret_target is None:
            return (
                f"Error: '{key}' is a per-node secret pointer and is only "
                f"resettable at the system scope."
            )
        display = _node_secret_display_key(canonical)
        if remove_leaf(secret_target):
            return _honest_reset_message(display, command_scope)
        return f"No override for {display}"

    # ``<scope>.secret_path.<VAR>`` — remove the stored pointer from the command scope's file.
    if _is_scope_secret_key(canonical):
        dest = _reset_dest(canonical, command_scope, config_path, system_settings_path)
        if remove_nested_key(dest.file, dest.sections, dest.leaf):
            return _honest_reset_message(canonical, command_scope)
        return f"No override for {canonical}"

    # ``<scope>.env.<VAR>`` — remove the stored value from the command scope's settings file.
    if _is_scope_env_key(canonical):
        dest = _reset_dest(canonical, command_scope, config_path, system_settings_path)
        if remove_nested_key(dest.file, dest.sections, dest.leaf):
            return _honest_reset_message(canonical, command_scope)
        return f"No override for {canonical}"

    # ``agent.<node>.<key>`` — remove the stored override from the node's OWN settings file
    # (``remove_nested_key`` prunes now-empty tables, keeping the file sparse).
    if _is_persona_agent_key(canonical):
        target = _persona_agent_target(canonical, agents_root)
        if isinstance(target, str):
            return target  # malformed node ref
        if target is None:
            return (
                f"Error: '{key}' is a per-persona agent setting and is only "
                f"resettable at the system scope."
            )
        display = _persona_display_key(canonical)
        if remove_leaf(target):
            return _honest_reset_message(display, command_scope)
        return f"No override for {display}"

    # Bare agent settings — reset the any-agent ``agent.default`` tier.
    if _is_agent_setting(canonical):
        dest = _reset_dest(canonical, command_scope, config_path, system_settings_path)
        if remove_nested_key(dest.file, dest.sections, dest.leaf):
            return _honest_reset_message(canonical, command_scope)
        return f"No override for {canonical}"

    # ``box.agent.<key>`` — RETIRED (P7, spec §2b). Refuse with the cure, naming the SAME agent
    # the set path names, so the two verbs prescribe the identical spelling.
    if _is_box_agent_key(canonical):
        return box_agent_retired_error(
            canonical, verb="reset", active_agent=cascade_agent_name or None,
        )

    # ⚑ THERE IS NO CATEGORY RESET BRANCH ANY MORE (DS-BL1 = (a)) — gone symmetrically with its
    # SET twin, which is the point. Do not restore one half of a symmetric pair.

    # STRUCTURAL ``system.*`` path-tier keys — FILE-ONLY, refused for symmetry with set.
    if is_system_path_key(canonical):
        return system_key_refusal(canonical, verb="reset")

    # Regular config keys — the same known-key table as set/get.
    routed = _route_key(canonical)
    route = _KEY_ROUTES.get(routed)
    if route is None:
        return f"Error: unknown config key: {key}"
    # ⚑ Symmetric with ``set_config_value`` BY CONSTRUCTION: the same rule site picks the file.
    dest = _reset_dest(canonical, command_scope, config_path, system_settings_path)
    removed = (
        remove_nested_key(dest.file, dest.sections, dest.leaf)
        if dest.sections
        else remove_root_key(dest.file, dest.leaf)
    )
    flat = _dot_to_flat(routed)
    if removed:
        # The now-effective value + source tier from the POST-RESET cascade (the file is
        # already written, so the assembled snapshot reflects the removal).
        # ⚑ GATED (F1): ONLY a scope-prefixed SETTINGS key READS through the cascade, so only
        # for those is a cascade-derived "effective" a true claim.
        effective = (
            _effective_after_reset(
                routed, dest.sections, dest.leaf,
                agent_name=cascade_agent_name,
                system_path=cascade_system_path,
                agent_path=cascade_agent_path,
                workset_path=cascade_workset_path,
                box_path=cascade_box_path,
            )
            # ⚑ The token test stays HERE, not at the rule site: it asks whether the key READS
            # through the cascade, not where it is STORED — two questions that share a test.
            if canonical.split(".", 1)[0] in _SETTINGS_SCOPE_TOKENS
            else None
        )
        return _honest_reset_message(flat, command_scope, effective)
    return f"No override for {flat}"


def _reset_dest(
    canonical: str,
    command_scope: "ConfigLevel | None",
    config_path: Path,
    system_settings_path: "Path | None",
) -> DestRoute:
    """``reset``'s destination — the SAME route ``set`` wrote through."""
    dest = _write_dest(
        canonical, command_scope=command_scope,
        config_path=config_path, settings_path=system_settings_path,
    )
    assert dest is not None
    return dest


def _honest_reset_message(
    flat: str,
    command_scope: "ConfigLevel | None",
    effective: "tuple[str, str] | None" = None,
) -> str:
    """The HONEST ``reset`` confirmation (F7, Jei-ruled 2026-07-02d)."""
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
    """The now-effective ``(value, source_tier)`` for *routed* AFTER a reset, else ``None``."""
    if all(
        p is None for p in (system_path, agent_path, workset_path, box_path)
    ):
        return None
    from kanibako.settings.kb_store import Bind
    from kanibako.settings.keystore import KeyStore
    from kanibako.settings.settings_assemble import assemble_levels
    from kanibako.settings.settings_expand import expand
    from kanibako.settings.settings_merge import merge

    # ⚑ The path tier — identical inputs to the set-time probe, but the failure arm DIFFERS:
    # an "effective" computed without the floor would name a value the cascade never resolves.
    try:
        config_foundation, floor = _path_tier_split()
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
    # ⚑ THE TIER NAMES PARALLEL ``assemble_levels``' ORDER (MOST-SPECIFIC-FIRST) — reordering
    # one without the other mislabels every tier. Read with UNBOUND dict ops (S3).
    tier_names = ("box", "workset", "agent", "agent.default", "system", "base")
    key_path = (*sections, leaf)

    def _reads(level: KeyStore, segs: tuple[str, ...]) -> "tuple[bool, object]":
        node: object = level
        for seg in segs:
            if not isinstance(node, KeyStore):
                return (False, None)
            if dict.get(node, seg, __MISSING__) is __MISSING__:
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
        return None  # a bind/subtree/list/present-None has no single scalar to print
    result = expand(snapshot, ctx, collect_errors=True)
    assert isinstance(result, tuple)  # lenient mode → (snapshot, errors)
    resolved_snap, errors = result
    if routed in errors:
        return None  # unresolved post-reset (dangling ref / cycle) — no guess.
    found, eff = _reads(resolved_snap, key_path)
    if not found or isinstance(eff, (Bind, KeyStore, list)) or eff is None:
        return None
    # ⚑ A stored/resolved EMPTY string has no value to name — never "effective is now <blank>".
    rendered = render_stored_scalar(eff)
    if rendered is None:
        return None
    return (rendered, source_tier)


def write_system_value(config_path: Path, leaf: str, value: object) -> None:
    """Write a ``[system] <leaf>`` key to the CONFIG file programmatically, past the CLI guard."""
    write_nested_key(config_path, ("system",), leaf, value)


def _count_leaves(node: object) -> int:
    """Count the scalar/leaf entries under a nested-dict *node* (a scope table)."""
    if isinstance(node, dict):
        return sum(_count_leaves(v) for v in node.values())
    return 1


def _clear_writable_scope_tables(
    path: Path, command_scope: "ConfigLevel | None",
) -> int:
    """Drop the top-level SCOPE tables *command_scope* may write from *path*; count the leaves."""
    if command_scope is None or not path.exists():
        return 0
    allowed = _SCOPE_WRITE_ALLOWED.get(command_scope, frozenset())
    data = load_doc(path)
    if not isinstance(data, dict):
        return 0
    removed = 0
    # ⚑ Only SCOPE tokens the command scope may WRITE are candidates; ``agent`` is handled
    # elsewhere and ``meta`` is never in ``_SCOPE_WRITE_ALLOWED`` (it is not a containment scope).
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
    """Remove all overrides at this config level.  Confirms unless *force*."""
    if not force:
        try:
            confirm_prompt("Remove all config overrides? Type 'yes' to proceed: ")
        except UserCancelled:
            return "Aborted."

    count = 0

    # Clear project-level config overrides (always from config_path).
    # ⚑ COUNT ONLY WHAT WAS ACTUALLY REMOVED (F2): an unconditional ``count += 1`` over-reported
    # (a file with only a ``[system]`` table said "Reset 1" while removing nothing).
    overrides = load_project_overrides(config_path)
    for key in overrides:
        if unset_project_config_key(config_path, key):
            count += 1

    # Clear the agent settings — SYSTEM keeps these in the system settings file.
    settings_dest = noun_settings_file(config_path, system_settings_path)
    if settings_dest is not None and settings_dest.exists():
        data = load_doc(settings_dest)
        agent_tbl = data.get("agent")
        if isinstance(agent_tbl, dict):
            # The agent table is agent-keyed; clear every agent's subsection, "default" included.
            for agent, sec in list(agent_tbl.items()):
                if isinstance(sec, dict):
                    for k in list(sec):
                        remove_nested_key(settings_dest, ("agent", agent), k)
                        count += 1

    # ⚑ The nested SCOPE tables need their own pass: the flat ``load_project_overrides`` one
    # only reaches the ``KanibakoConfig`` dataclass fields and leaves them intact.
    count += _clear_writable_scope_tables(settings_dest, command_scope)

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
    category_snapshot: Any = None,
    category_error: str | None = None,
) -> int:
    """Display config values — overrides only, or the full resolved view.  Returns an exit code."""
    out = file or sys.stdout
    # The file agent SETTINGS are displayed from: the system settings file at SYSTEM, else the
    # level's own ``config_path``.
    settings_src = noun_settings_file(config_path, system_settings_path)

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

        # Agent settings: render a supplied box-view ``agent_state`` (marking only the keys set
        # at the box level), else fall back to the project-level overrides.
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

        # SYSTEM scope: the nested settings-tier entries a system-scope ``set`` stores and the
        # launch cascade reads (F2 — the effective view must show what set wrote).
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

        # ⚑ Env vars come from the resolved BOX VIEW and ONLY that — never from the retired
        # ``.env`` files, whose rows would assert an effect that does not happen.
        # ⚑ Rendered ``env <VAR>``, NOT ``env.<VAR>``: these rows are a MERGE, not a key, and
        # the dotted spelling is REFUSED (R-39) if a reader copies it into ``config set``.
        if env_resolved:
            print("", file=out)
            for k in sorted(env_resolved):
                print(f"  env {k} = {env_resolved[k]}", file=out)

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

        # SYSTEM scope: the nested settings-tier overrides ARE overrides at this level.
        if system_settings_path is not None:
            nested = _nested_settings_overrides(system_settings_path)
            for k, v in sorted(nested.items()):
                print(f"  {k} = {v}", file=out)
                has_output = True

        # ``pref`` REQUESTS stored at this noun (§2h) ARE overrides at this level.
        for k, v in sorted(_pref_overrides(config_path).items()):
            print(f"  {k} = {v}", file=out)
            has_output = True

        # ⚑ NO docker ``.env`` block, deliberately — those rows would name a refused spelling
        # AND assert an override that has no effect.

        if not has_output:
            print("  (no overrides)", file=out)

    return 0
