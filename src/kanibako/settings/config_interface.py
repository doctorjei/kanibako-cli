"""Unified config interface engine for all management commands.

Provides a reusable config subsystem that box/workset/agent/system commands
share.  Handles get, set, show, and reset operations with a consistent
syntax:

- ``key=value``  → set
- ``key``        → get (if key is known)
- no args        → show all overrides
- ``--effective`` → show resolved values
- ``--null key`` → SET an explicit present-``None`` (the suppression request)
- ``reset key``  → remove override (the verb that UNDOES ``--null``)
- ``reset --all`` → remove all overrides (with confirmation)

⚑ ``reset`` is a sibling VERB (``box reset <key>``), not a ``--reset`` flag —
no parser defines one.  ``args.reset`` is only the namespace attribute the
command modules set before calling in here.
"""

from __future__ import annotations

import sys
from dataclasses import fields
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from kanibako.settings.config import (
    coerce_bool,
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
from kanibako.settings.config_dest import (
    DestRoute,
    _write_dest,
    _read_dest,
    noun_settings_file,
    check_agent_node,
    _node_bind_target,
    _node_secret_target,
    _persona_agent_target,
)
from kanibako.settings.config_keys import (
    KEY_TYPES,
    _KEY_ROUTES,
    _SCOPE_WRITE_ALLOWED,
    _SETTINGS_SCOPE_TOKENS,
    _agent_scope_node,
    _coerce_value,
    _dot_to_flat,
    _floor_bind_display,
    _is_agent_node_bind_key,
    _is_agent_node_secret_key,
    _is_agent_setting,
    _is_box_agent_key,
    _is_env_key,
    _is_path_category_key,
    _is_persona_agent_key,
    _is_pref_key,
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
    bare_agent_key_scope_error,
    box_agent_redirect_key,
    box_agent_retired_error,
    is_auto_approve_key,
    is_system_path_key,
    parse_agent_node_bind_key,
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
from kanibako.settings.settings_prefs import PREF_ROOT
from kanibako.settings.settings_store import _MISSING, ReservedKeyError
from kanibako.shellenv import (
    merge_env,
    read_env_file,
    set_env_var,
    unset_env_var,
    write_env_file,
)
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
    """Parse a single positional config argument.

    Returns ``(action, key, value)``.

    - ``"key=value"`` → ``(set, key, value)``
    - ``"key"``       → ``(get, key, "")``
    - ``None``        → ``(show, "", "")``

    *set_null* is the ``--null`` flag: ``config set --null <key>`` is a SET whose
    value is Python ``None`` — an explicit present-``None``, distinct from the
    terminal empty string ``key=`` and from the sibling ``reset`` VERB (which
    REMOVES the override rather than writing one, and is therefore how a user
    UNDOES a ``--null``).

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
    from kanibako.settings.settings_categories import BIND_KEY_RE, MASK_KEY_RE

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
            f"...or suppress the entry with: --null {canonical}\n"
            f"(that WRITES a suppression; 'reset {canonical}' undoes it)"
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


def _host_xdg_map(data_home: "Path | None" = None) -> dict[str, str]:
    """Thin module-PRIVATE delegate to :func:`kanibako.settings.paths.host_xdg_map`.

    Exists so the ONE canonical XDG-map builder is reachable as a
    ``config_interface`` attribute (patchable, single-source) WITHOUT a
    module-load import of ``paths`` (which would cycle: ``config_interface`` ↔
    ``paths``). Underscored so it is NOT a second PUBLIC import surface for the
    builder (Editor NIT): the one public builder stays ``paths.host_xdg_map``;
    this is only the deferred-import hook ``_set_time_ctx`` calls. There is no
    second hand-rolled XDG map (spec §1 XDG clause + L2 §3).
    """
    from kanibako.settings.paths import host_xdg_map

    return host_xdg_map(data_home)


def _set_time_ctx(config: "dict[str, str] | None" = None) -> "Any":
    """Build the :class:`~kanibako.settings.settings_resolve.ResolveCtx` for the set-time E3
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
    :func:`kanibako.settings.paths.host_xdg_map` (spec §1 XDG clause + L2 §3 single-source-
    of-truth: a hand-rolled per-context map is a bug), reached through the
    module-private :func:`_host_xdg_map` deferred-import hook (avoids the
    ``config_interface`` ↔ ``paths`` module-load cycle) so it stays a single
    source.
    """
    from kanibako.settings.settings_resolve import ResolveCtx

    return ResolveCtx(
        agent_name=None,
        workset_name=None,
        host_home=str(Path.home()),
        xdg=_host_xdg_map(),
        config=config or {},
    )


def _path_tier_split() -> "tuple[dict[str, str], dict[str, object]]":
    """The path tier as ``(config_foundation, floor)``, RAISING on failure.

    The Layer-1 ``config.*`` foundation goes to the resolve context (so an
    ``@config.*`` host_src routes there — JC-2) and the Layer-2 ``system.*``
    paths become the cascade FLOOR (so an ``@system.*`` host_src resolves from
    the snapshot).

    ⚑ THE FAILURE ARM IS THE CALLER'S, DELIBERATELY.  Its two callers disagree
    about what a resolution failure means and they are BOTH right: a ``config
    set`` must still work with an empty floor, because refusing to write when the
    path tier is unreadable would make the tool useless for repairing exactly
    that; while a post-reset "effective value" computed on an empty floor would
    NAME A VALUE THAT IS NOT THE ONE the cascade resolves, and the honest answer
    there is to say nothing. So this function raises and each caller catches what
    it means. Collapsing the two arms into one would be a behavior change on one
    of them, and which one is correct is a spec question, not a refactor's call.
    """
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
        elif dotted.startswith("system."):
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
      :class:`~kanibako.settings.settings_store.Bind` from the merged snapshot — the tuple
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
    from kanibako.settings.settings_assemble import assemble_levels
    from kanibako.settings.settings_expand import expand
    from kanibako.settings.settings_merge import merge

    # The path tier as the resolution context. A resolution failure here must NOT
    # crash a config set — fall back to empty (sibling refs still resolve).
    try:
        config_foundation, floor = _path_tier_split()
    except Exception:
        config_foundation, floor = {}, {}

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
    from kanibako.settings.settings_launch import meta_agent_path_floor

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
        from kanibako.settings.settings_store import Bind, KeyStore

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
    from kanibako.settings.settings_store import KeyStore

    out = KeyStore()
    for k in dict.keys(store):
        v = dict.__getitem__(store, k)
        out[k] = _clone_keystore(v) if isinstance(v, KeyStore) else v
    return out


def _set_leaf(store: "Any", parts: list, value: object) -> None:
    """Set *value* at the dotted *parts* path in *store*, creating nested KeyStore
    nodes as needed (unbound ``dict`` ops, S3). Used to apply the candidate edit
    into the cloned snapshot before the E3 lenient-expand check."""
    from kanibako.settings.settings_store import KeyStore

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
    from kanibako.settings.settings_configset import (
        ConfigSetError,
        Error,
        Warn,
        repoint_host_src,
        validate_config_set,
    )

    # NODE GUARDS for the per-node agent bind route (``agent.<node>.bindings.*``) —
    # the SAME two refusals its three siblings enforce (``_persona_agent_target``,
    # ``_node_bind_target``, ``_node_secret_target``): the RESERVED any-agent tier is
    # not a persona node, and a MALFORMED ref is not a node at all. This route
    # (``dest_parts`` below) computed the file route inline and skipped both, so SET
    # wrote values ``get``/``reset`` then refused to read or remove — a write the CLI
    # cannot undo, and (for the reserved name) an ``agents/default/`` dir the launch
    # never reads as a node. Checked FIRST, before the cascade/value validation:
    # whether the KEY exists precedes whether its VALUE is good, so the refusal is
    # deterministic instead of racing a must-exist error.
    _node_parse = parse_agent_node_bind_key(canonical)
    if _node_parse is not None:
        _refusal = check_agent_node(_node_parse[0])
        if _refusal is not None:
            if _refusal.reason == "reserved":
                return (
                    f"Error: 'default' is the reserved any-agent tier, not a persona "
                    f"node; a per-node descriptor bind must name a real agent node "
                    f"(e.g. 'agent.<persona>+<harness>.{_node_parse[1]}."
                    f"{_node_parse[2]}')."
                )
            return f"Error: {_refusal.detail}"

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
    if _node_parse is not None:
        node, _cat, _name = _node_parse  # _cat = full "bindings.ro"/"bindings.rw"
        from kanibako.settings.agent_config import agent_file_route

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
    """Read a single config value from the appropriate store.

    *active_agent* is the box's resolved agent NODE, needed ONLY to redirect a
    BARE agent behavior key at box scope to its ``pref.agent.<active>.<key>``
    request (P7 — see :func:`box_agent_redirect_key`). Absent/unknown ⇒ no
    redirect.

    Returns the resolved (merged) value as a string, or None if the key
    is not set.

    *system_settings_path*, when supplied (the SYSTEM scope), is the file used
    for SETTINGS reads (``system.agent`` + agent settings) — i.e.
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
    canonical = resolve_key(key)

    # A BARE agent behavior key at BOX command scope has no readable value of its
    # own: a box cannot write ``agent.default.<key>`` (it is dropped at launch — see
    # :func:`box_agent_redirect_key` + ``set_config_value``). REDIRECT the read to
    # the box's active-agent mirror ``box.agent.<key>`` so ``get`` reads exactly
    # where a corrected ``set box.agent.<key>`` wrote, and the caller NAMES the
    # value ``box.agent.<key>`` (teaching the canonical form). WORKSET has no mirror,
    # so a workset bare-agent-key get is REFUSED at the command handler
    # (:func:`bare_agent_key_scope_error`, verb "read"), not here — this forgiving
    # read only applies to box. Every other form / scope is unchanged.
    _box_agent_redirect = box_agent_redirect_key(
        canonical, command_scope, active_agent,
    )
    if _box_agent_redirect is not None:
        canonical = _box_agent_redirect

    # The NOUN's settings file and the noun's own CONFIG file — the two inputs the
    # destination rule composes.  ⚑ ``get`` is the only verb carrying BOTH files as
    # separate parameters (``global_config_path`` + ``project_toml``) where the
    # write verbs carry one ``config_path``, so the mapping onto the shared rule
    # happens here, once.  Collapsing the two parameters is a signature change and
    # belongs to the verb rewrite, not to a move.
    noun_file = noun_settings_file(project_toml, system_settings_path)
    own_config = (
        global_config_path if system_settings_path is not None else project_toml
    )

    # pref.<target-key> — return the REQUEST stored at this noun (spec §2h
    # "config get pref.system.agent returns the REQUEST"; clause 5's plain
    # get = stored-at-noun). The RESOLVED result is the --effective view.
    if _is_pref_key(canonical):
        sections, leaf = _pref_sections_leaf(canonical)
        return read_stored_pref(noun_file, sections, leaf)

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
        return read_stored_leaf(path, sections, leaf)

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
        return read_stored_leaf(path, sections, leaf)

    # <scope>.secret_path.<VAR> (system/workset/box) — read the stored PATH from the
    # NOUN's settings file (stored-at-noun; the --effective cascade view is the show
    # path). ``noun_file`` is the SAME per-noun selection set/reset use
    # (``settings_dest``): the system settings file at SYSTEM, else the command's own
    # ``project_toml``. It read ``project_toml`` unconditionally before, which the
    # SYSTEM handler never threads — so a ``system set system.secret_path.X`` (written
    # to the system settings file) read back "(not set)" forever while ``reset``
    # cleared it. Box/workset are unaffected: there ``noun_file`` IS ``project_toml``.
    if _is_scope_secret_key(canonical):
        if noun_file and noun_file.exists():
            parts = canonical.split(".")
            return read_stored_leaf(
                noun_file, (parts[0], "secret_path"), parts[2],
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
            return read_stored_leaf(path, sections, leaf)
        return None

    # target settings (model, continue_mode, auto_approve, allow_helpers)
    if _is_agent_setting(canonical):
        # The agent-agnostic ``config`` CLI reads/writes the reserved any-agent
        # ``agent.default`` tier; per-agent overrides live under ``agent.<name>``
        # and are resolved by the launch-time effective-state cascade.  For the
        # SYSTEM scope these are SETTINGS that live in the system settings file
        # (system_settings_path), not the kanibako_config.yaml CONFIG file.
        setting_src = noun_file
        if setting_src and setting_src.exists():
            settings = read_agent_settings(setting_src, "default")
            if canonical in settings:
                return settings[canonical]
        return None

    # box.agent.<key> — RETIRED (P7, spec §2b): there is no settable box-scoped
    # agent mirror any more, so there is no stored value to read. Returning ``None``
    # (rather than reading a hand-written legacy leaf) is deliberate: reading it
    # would report a value that no longer has ANY effect on the launch, which is
    # worse than "(not set)". The set/reset verbs refuse with the cure; the
    # effective value is readable at ``meta.box.agent.<key>`` via --effective.
    if _is_box_agent_key(canonical):
        return None

    # Path-TUPLE category keys (``<scope>.bindings.{ro,rw}.<name>`` / ``caches`` /
    # ``seeded`` / ``shared`` / ``synced``) — the get/set/reset symmetry twin of the
    # category SET branch (F10, spec §2a). Read the RAW tuple STORED at the nested
    # dotted path in the NOUN's settings file (== the box file at box scope, the
    # system settings file at SYSTEM) — for a FILE-scope key (``system``/``workset``/
    # ``box``) that is exactly where ``repoint_host_src`` wrote it, TRUE since the
    # set/reset branches were routed through ``settings_dest`` (they wrote/removed a
    # SYSTEM-scope tuple in the kanibako_config.yaml CONFIG file before, which this
    # branch never read — a set that read back "(not set)"). The claim is NOT yet
    # true for a non-bind AGENT-scope category (``agent.<node>.common.*`` /
    # ``caches`` / ``seeded`` / ``synced``): set AND reset both use the caller's
    # ``config_path`` (at the system handler, kanibako_config.yaml) while this read
    # uses ``noun_file`` — and kanibako_config.yaml is in NO cascade level, so that
    # set is a SILENT NO-OP WRITE (the agent tier reads the ``self:`` table of
    # ``agents/<node>/settings.yaml``). The per-node BIND form is routed EARLIER
    # (``_is_agent_node_bind_key``, the node file) and is symmetric.
    # Checked BEFORE the ``system.*`` file-only branch because a SYSTEM-scope
    # category key (``system.bindings.*``) only LOOKS like a ``system.*`` config
    # key — categories are settable/gettable at every scope (mirrors the set/reset
    # order). A plain get is stored-at-noun; the resolved-with-floor bind is the
    # ``show --effective`` cascade view. Absent → ``None`` ("(not set)").
    if _is_path_category_key(canonical):
        # Through the SAME rule site the write side uses — which is what makes
        # ``_read_dest``'s one documented divergence from ``_write_dest`` (this
        # family, at agent scope) a fact about running code rather than a claim
        # in a docstring nothing exercised.
        dest = _read_dest(
            canonical, command_scope=command_scope,
            config_path=own_config, settings_path=system_settings_path,
        )
        assert dest is not None  # a category key always has a slot
        return read_stored_leaf(dest.path, dest.sections, dest.leaf)

    # config.* / system.* path keys — read the raw set-value from the bootstrap
    # config file's [config]/[system] tables (file-only tier; not a merged-config
    # field).
    if is_system_path_key(canonical):
        cfg = load_merged_config(global_config_path, project_toml)
        return cfg.config_paths.get(canonical)

    # Regular config keys — route via the SAME known-key table that set/reset
    # use (no get-validated/set-unguarded asymmetry).  An unknown key returns
    # None (rendered "not set").
    # Read the value STORED AT THIS NOUN (F6 + the F2/F3-class sibling). The OLD
    # path returned ``getattr(load_merged_config(...), flat)`` — the merged
    # dataclass, which fabricates the built-in DEFAULT when the noun stored
    # nothing (the F6 lie: ``box get box.image`` printing the default image) and
    # folds in the GLOBAL config file (returning another tier's value). Under the
    # get model a plain get reads ONLY the file ``set`` wrote to, at the routed
    # ``(sections, leaf)`` slot — and it reads through the SAME rule site
    # ``set``/``reset`` write through, so "get reads where set wrote" is now
    # structural rather than a claim two copies had to keep agreeing on.
    # An unknown key (no family claims it) reads ``None`` ("(not set)"), exactly
    # as the routing-table miss did before. Absent → ``None``; the
    # resolved-with-defaults value is the ``--effective`` cascade (``show``).
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
    default_categories: "Mapping[str, object] | None" = None,
) -> str:
    """Write a config value to the appropriate store.

    *config_path* is the settings.yaml (for box/workset) or kanibako_config.yaml
    (for system).  *system_settings_path*, when supplied (the SYSTEM scope), is
    the file SETTINGS (``system.agent`` + agent settings) are written to
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
    canonical = resolve_key(key)

    # config.* foundation keys are NEVER CLI-settable (block B2): they locate the
    # files everything else lands in, so they cannot live in those files — they
    # live in the bootstrap config file, hand-edited by a human/admin. Refused
    # EXPLICITLY here, BEFORE the scope guard, so every command scope gets the same
    # ruled message (not the cross-scope guard message, and not the older generic
    # system_key_refusal that mentions `setup`). The READ/show path still consults
    # is_system_path_key's config. branch — only set/reset short-circuit here.
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
    bare_err = bare_agent_key_scope_error(
        canonical, command_scope, verb="set",
        active_agent=cascade_agent_name or None,
    )
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
                f"'reset {canonical}' to remove the variable."
            )
        if _is_path_category_key(canonical) or _is_agent_node_bind_key(canonical):
            return (
                f"Error: --null is not yet supported for the category key "
                f"'{canonical}' (a config set of a category is a source-only "
                f"repoint, which has no null form). Suppress the entry by "
                f"writing 'null' at the key in the settings file, or request the "
                f"suppression from a box/workset with "
                f"'--null pref.{canonical}' (spec §2h) — which 'reset "
                f"pref.{canonical}' undoes."
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
        and is_auto_approve_key(canonical)
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
    # stays usable to REPAIR a broken config. ``reset`` is untouched: removing
    # an override cannot introduce a dangling ref in the removed value.
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
            default_categories=default_categories,
        )
        scalar_verdict = validate_config_set(
            canonical, value, is_category=False, resolves=_resolves,
        )
        if isinstance(scalar_verdict, _SetError):
            return f"Error: {scalar_verdict.message}"

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
        dest = _write_dest(
            canonical, command_scope=command_scope,
            config_path=config_path, settings_path=system_settings_path,
        )
        assert dest is not None  # the pref family always has a slot
        write_nested_key(dest.file, dest.sections, dest.leaf, value)
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
    # ``resolve_key``. Sparse by construction: ``write_nested_key`` is
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
        write_nested_key(path, sections, leaf, value)
        return f"Set {_node_secret_display_key(canonical)}={value}"

    # <scope>.secret_path.<VAR> (system/workset/box) — the SECRET category at a
    # NON-agent scope: a SCALAR path write to the command scope's SETTINGS file at
    # the nested ``<scope>.secret_path.<VAR>`` slot (the shape ``_file_partial`` reads
    # into the cascade). The §0 directional guard already permitted it (own/contained
    # scope). settings_dest = the command scope's settings file (config_path at box/
    # workset; the system settings file at SYSTEM — never the Layer-1 config file).
    if _is_scope_secret_key(canonical):
        dest = _write_dest(
            canonical, command_scope=command_scope,
            config_path=config_path, settings_path=system_settings_path,
        )
        assert dest is not None  # the scope-secret family always has a slot
        write_nested_key(dest.file, dest.sections, dest.leaf, value)
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
        write_nested_key(path, sections, leaf, value)
        return f"Set {_persona_display_key(canonical)}={value}"

    # target settings — the agent-agnostic CLI writes the any-agent
    # ``agent.default`` tier (per-agent overrides live under ``agent.<name>``).
    # SYSTEM scope routes to the system settings file (settings_dest).
    if _is_agent_setting(canonical):
        dest = _write_dest(
            canonical, command_scope=command_scope,
            config_path=config_path, settings_path=system_settings_path,
        )
        assert dest is not None  # the bare-agent family always has a slot
        write_nested_key(dest.file, dest.sections, dest.leaf, value)
        return f"Set {canonical}={value}"

    # box.agent.<key> — RETIRED (P7, spec §2b). There is NO settable box-scoped
    # mirror of the agent's settings any more: §2b replaced it with the RO
    # read-back ``meta.box.agent.<key>``, and a box tweaks its agent through the
    # §2h request ``pref.agent.<active>.<key>``, which targets the agent tier
    # properly instead of smuggling a box-scope key into it. So this branch
    # REFUSES and names the cure; nothing is written.
    #
    # It is still checked BEFORE the path-category branch, because
    # ``box.agent.bindings.ro.X`` matches the category regex too and would
    # otherwise be accepted as an ordinary box-scope category write — the refusal
    # must claim the whole retired spelling, not just its scalar half.
    if _is_box_agent_key(canonical):
        return box_agent_retired_error(
            canonical, verb="set", active_agent=cascade_agent_name or None,
        )

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
        # A FILE-scope category (``system``/``workset``/``box``) writes the COMMAND
        # scope's SETTINGS file (``settings_dest``) — the same destination the
        # scope-prefixed SCALAR keys below use, and the file the launch cascade reads
        # for that tier (at SYSTEM, ``@config.settings``). This branch passed
        # ``config_path`` before: identical at box/workset (``settings_dest`` IS
        # ``config_path`` there), but at SYSTEM ``config_path`` is the
        # kanibako_config.yaml CONFIG file — so a system category set wrote where GET
        # never looks, where the launch never reads, and where ``reset --all`` (which
        # sweeps the settings file's scope tables) could not clear it.
        #
        # The AGENT scope keeps ``config_path`` — UNCHANGED, not endorsed. Its store
        # is a PER-NODE file (``agents/<node>/settings.yaml``, the ``self:`` table the
        # agent tier reads), and only the per-node BIND route reaches it (the branch
        # above, via system_cmd's node-file threading). A non-bind agent category
        # (``agent.<node>.common.*`` / ``caches`` / ``seeded`` / ``synced``) is routed
        # by NO handler and lands in the command's own file, which is in no cascade
        # level — so today that set is a SILENT NO-OP WRITE. ``settings_dest`` would
        # not fix it either (it is not the node file), so this behavior-only fix
        # leaves the broken case exactly as it found it rather than moving it to a
        # second wrong file. Fixing it is its own change: route it to the node file.
        # ⚑ Both arms now come from ``_write_dest``, which reproduces this exact
        # split and says so at the rule site — the broken arm is preserved, not
        # inherited by accident.
        _cat_dest = _write_dest(
            canonical, command_scope=command_scope,
            config_path=config_path, settings_path=system_settings_path,
        )
        assert _cat_dest is not None  # a category key always has a slot
        return _set_category_value(
            canonical, value,
            config_path=_cat_dest.file,
            system_path=cascade_system_path,
            agent_path=cascade_agent_path,
            workset_path=cascade_workset_path,
            box_path=cascade_box_path,
            agent_name=cascade_agent_name,
            default_categories=default_categories,
        )

    # STRUCTURAL system.* path-tier keys (the SYSTEM_PATH_DEFAULTS family) —
    # FILE-ONLY: they live in kanibako_config.yaml's [system] table (the file
    # ``resolve_system_paths`` reads), editable there or via ``kanibako setup``
    # (write_system_value bypasses this guard).  The refusal names THAT file.
    # This is a precise family check (F2): a system.* SETTINGS key (auth chain /
    # system.agent / categories / env) was routed above or falls through to the
    # routing table below — it is never refused here.
    if is_system_path_key(canonical):
        return system_key_refusal(canonical)

    # Regular config keys — route via the single known-key table (the H1 fix:
    # an unknown key returns an error string and NEVER raises).  Accept either
    # the canonical dotted spelling or the flat underscore form.
    routed = _route_key(canonical)
    route = _KEY_ROUTES.get(routed)
    if route is None:
        return f"Error: unknown config key: {key}"
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
    default_categories: "Mapping[str, object] | None" = None,
) -> str:
    """Remove an override for a single key.  Returns confirmation message.

    *system_settings_path*, when supplied (SYSTEM scope), is where SETTINGS
    (``system.agent`` + agent settings) are removed from
    (``@config.settings`` = ``global/settings.yaml``); when None (box/workset)
    they are removed from ``config_path`` as before.

    *command_scope* is the scope the ``config reset`` was issued at (block B2,
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
    canonical = resolve_key(key)

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
    bare_err = bare_agent_key_scope_error(
        canonical, command_scope, verb="reset",
        active_agent=cascade_agent_name or None,
    )
    if bare_err is not None:
        return bare_err

    # pref.<target-key> — remove the REQUEST from this noun's settings file
    # (symmetric with the set/get branches: reset clears exactly where set wrote).
    if _is_pref_key(canonical):
        dest = _reset_dest(canonical, command_scope, config_path, system_settings_path)
        if remove_nested_key(dest.file, dest.sections, dest.leaf):
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
        if remove_nested_key(path, sections, leaf):
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
        if remove_nested_key(path, sections, leaf):
            return _honest_reset_message(display, command_scope)
        return f"No override for {display}"

    # <scope>.secret_path.<VAR> (system/workset/box) — remove the stored pointer
    # from the command scope's settings file (symmetric with set/get).
    if _is_scope_secret_key(canonical):
        dest = _reset_dest(canonical, command_scope, config_path, system_settings_path)
        if remove_nested_key(dest.file, dest.sections, dest.leaf):
            return _honest_reset_message(canonical, command_scope)
        return f"No override for {canonical}"

    # agent.<node>.<key> — the PER-PERSONA agent key (block B1): remove the stored
    # override from the agent's OWN settings file ``agents/<node>/settings.yaml``
    # (symmetric with set/get; ``remove_nested_key`` prunes now-empty
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
        if remove_nested_key(path, sections, leaf):
            return _honest_reset_message(display, command_scope)
        return f"No override for {display}"

    # target settings — reset the any-agent ``agent.default`` tier (SYSTEM scope
    # routes to the system settings file).
    if _is_agent_setting(canonical):
        dest = _reset_dest(canonical, command_scope, config_path, system_settings_path)
        if remove_nested_key(dest.file, dest.sections, dest.leaf):
            return _honest_reset_message(canonical, command_scope)
        return f"No override for {canonical}"

    # box.agent.<key> — RETIRED (P7, spec §2b). Symmetric with set: refuse with the
    # cure rather than silently clearing a key that no longer does anything — and
    # with the SAME named agent the set path uses, so the two verbs prescribe the
    # identical spelling.
    if _is_box_agent_key(canonical):
        return box_agent_retired_error(
            canonical, verb="reset", active_agent=cascade_agent_name or None,
        )

    # Path-TUPLE category keys — reset symmetry with the category SET branch
    # (F10, spec §2a): remove the COMMAND-scope override tuple from the SAME file
    # the set wrote (see the dest rule below), pruning emptied tables, so the
    # cascade's own tuple (a higher scope's or the launch floor's) resurfaces at
    # the next assemble. Before this branch a category key fell through to the routing
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
        # Removed from the file the category SET branch WRITES — literally the
        # same call, so set and reset can no longer name different files even in
        # principle. It removed from ``config_path`` unconditionally before, which
        # at SYSTEM is the kanibako_config.yaml CONFIG file: neither where set
        # wrote nor where get reads.
        dest = _reset_dest(canonical, command_scope, config_path, system_settings_path)
        if remove_nested_key(dest.file, dest.sections, dest.leaf):
            floor = _floor_bind_display(canonical, default_categories)
            return _honest_reset_message(canonical, command_scope, floor)
        return f"No override for {canonical}"

    # STRUCTURAL system.* path-tier keys — FILE-ONLY (see set_config_value).
    # The CLI refuses to RESET them too (for symmetry); edit the config file
    # directly or re-run ``kanibako setup``.
    if is_system_path_key(canonical):
        return system_key_refusal(canonical)

    # Regular config keys — route via the same known-key table as set/get
    # (no get-validated/set-unguarded asymmetry).
    routed = _route_key(canonical)
    route = _KEY_ROUTES.get(routed)
    if route is None:
        return f"Error: unknown config key: {key}"
    # Symmetric with set_config_value BY CONSTRUCTION: the same rule site picks
    # the file, so a scope-prefixed SETTINGS key is removed from exactly the file
    # the set wrote it to.
    dest = _reset_dest(canonical, command_scope, config_path, system_settings_path)
    removed = (
        remove_nested_key(dest.file, dest.sections, dest.leaf)
        if dest.sections
        else remove_root_key(dest.file, dest.leaf)
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
                routed, dest.sections, dest.leaf,
                agent_name=cascade_agent_name,
                system_path=cascade_system_path,
                agent_path=cascade_agent_path,
                workset_path=cascade_workset_path,
                box_path=cascade_box_path,
            )
            # ⚑ The token test stays HERE rather than moving to the rule site:
            # it asks whether the key READS through the cascade, not where it is
            # stored. Those are different questions that happen to share a test.
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
    """``reset``'s destination — the SAME route ``set`` wrote through.

    A thin adapter over :func:`config_dest._write_dest` so each reset branch reads
    as one line. It asserts the route exists because every branch that calls it
    has already established its family, and a family with no slot would mean the
    dispatch and the rule site disagree — which is a bug to surface, not to route
    around.
    """
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
    from kanibako.settings.settings_assemble import assemble_levels
    from kanibako.settings.settings_expand import expand
    from kanibako.settings.settings_merge import merge
    from kanibako.settings.settings_store import Bind, KeyStore

    # The path tier — identical inputs to the set-time probe; a resolution failure
    # must not break a reset, and an "effective" computed without the floor would
    # name a value the cascade does not resolve, so we keep the cleared-only form.
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
            if dict.get(node, seg, _MISSING) is _MISSING:
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
    # <blank>". (``render_stored_scalar`` already maps "" → None.)
    rendered = render_stored_scalar(eff)
    if rendered is None:
        return None
    return (rendered, source_tier)


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
    via :func:`write_nested_key`).
    """
    write_nested_key(config_path, ("system",), leaf, value)


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
    settings_dest = noun_settings_file(config_path, system_settings_path)
    if settings_dest is not None and settings_dest.exists():
        data = load_doc(settings_dest)
        agent_tbl = data.get("agent")
        if isinstance(agent_tbl, dict):
            # agent table is agent-keyed: {<agent>: {key: val}}; clear every
            # agent's subsection (the reserved "default" tier included).
            for agent, sec in list(agent_tbl.items()):
                if isinstance(sec, dict):
                    for k in list(sec):
                        remove_nested_key(settings_dest, ("agent", agent), k)
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
    SETTINGS + ``system.agent`` are DISPLAYED from (``@config.settings``
    = ``global/settings.yaml``); the ``system.*`` CONFIG display always uses
    ``global_config_path``.  When None (box/workset) settings display reads
    ``config_path`` as before.
    """
    out = file or sys.stdout
    # The file agent SETTINGS are read from for display: system settings file for
    # the SYSTEM scope, else the level's own config_path (box/workset).
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
