"""kanibako agent: agent configuration, authentication, and settings."""

from __future__ import annotations

import argparse
import sys
from typing import TYPE_CHECKING

from kanibako.agent_ref import canonicalize_agent_ref, display_agent_ref

if TYPE_CHECKING:
    from kanibako.agent_config import AgentConfig
    from kanibako.paths import StandardPaths


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "agent",
        help="Agent management, authentication, and settings",
        description="Manage agent configurations, authentication, and settings.",
    )
    agent_sub = p.add_subparsers(dest="agent_command", metavar="COMMAND")

    # agent list (default)
    list_p = agent_sub.add_parser(
        "list",
        aliases=["ls"],
        help="List configured agents",
    )
    list_p.add_argument("-q", "--quiet", action="store_true", help="Names only")
    list_p.set_defaults(func=run_list)

    # agent info <agent>
    info_p = agent_sub.add_parser(
        "info",
        aliases=["inspect"],
        help="Show agent configuration details",
    )
    info_p.add_argument("agent_id", help="Agent identifier")
    info_p.set_defaults(func=run_info)

    # agent set <agent> <key>=<value>
    set_p = agent_sub.add_parser(
        "set",
        help="Set an agent configuration value",
        description=(
            "Set an agent setting (key=value).\n\n"
            "  agent set myagent model=sonnet     set 'model'\n"
            "  agent set myagent env.FOO=bar      set env var FOO\n"
            "  agent set myagent secret_path.TOKEN=~/.config/claude/foo/token\n"
            "                                     TOKEN from a host secret file\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    set_p.add_argument("agent_id", help="Agent identifier")
    set_p.add_argument("key_value", help="key=value pair")
    set_p.set_defaults(func=run_set)

    # agent reset <agent> <key> | --all  [--force]
    reset_p = agent_sub.add_parser(
        "reset",
        help="Reset (remove) an agent configuration override",
        description=(
            "Remove an agent override, reverting to the default.\n\n"
            "  agent reset myagent model          reset one key\n"
            "  agent reset myagent --all          reset all overrides\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    reset_p.add_argument("agent_id", help="Agent identifier")
    reset_p.add_argument("key", nargs="?", default=None, help="Config key to reset")
    reset_p.add_argument(
        "--all", action="store_true", dest="all_keys",
        help="Reset all overrides",
    )
    reset_p.add_argument(
        "--force", action="store_true", help="Skip confirmation prompts",
    )
    reset_p.set_defaults(func=run_reset)

    # agent get <agent> <key>
    get_p = agent_sub.add_parser(
        "get",
        help="Get an agent configuration value",
        description="Read one agent setting.",
    )
    get_p.add_argument("agent_id", help="Agent identifier")
    get_p.add_argument("key", help="Config key to read")
    get_p.set_defaults(func=run_get)

    # agent show <agent> [--effective]
    show_p = agent_sub.add_parser(
        "show",
        help="Show agent configuration",
        description=(
            "Show agent settings.\n\n"
            "  agent show myagent                 show all settings\n"
            "  agent show myagent --effective     show resolved values\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    show_p.add_argument("agent_id", help="Agent identifier")
    show_p.add_argument(
        "--effective", action="store_true",
        help="Show resolved values including defaults",
    )
    show_p.set_defaults(func=run_show)

    # agent reauth [project]
    reauth_p = agent_sub.add_parser(
        "reauth",
        help="Check authentication and login if needed",
        description=(
            "Verify agent authentication status and run interactive "
            "login if credentials are expired or missing."
        ),
    )
    reauth_p.add_argument(
        "project", nargs="?", default=None,
        help="Target project directory or name",
    )
    reauth_p.set_defaults(func=run_reauth)

    # Default to list if no subcommand given.
    p.set_defaults(func=run_list, quiet=False)


# ---------------------------------------------------------------------------
# Agent list / info / config + reauth handlers
# ---------------------------------------------------------------------------


def _load_std() -> StandardPaths:
    """Load config and return the resolved standard paths."""
    from kanibako.config import config_file_path, load_config
    from kanibako.paths import xdg, load_std_paths

    config_file = config_file_path(xdg("XDG_CONFIG_HOME", ".config"))
    config = load_config(config_file)
    return load_std_paths(config)


def run_list(args: argparse.Namespace) -> int:
    """List configured agents."""
    from kanibako.agent_config import load_agent_config

    try:
        std = _load_std()
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    adir = std.agents
    if not adir.is_dir():
        quiet = getattr(args, "quiet", False)
        if not quiet:
            print("No agents configured.")
        return 0

    # Each agent's settings live inside its store dir: agents/<agent>/settings.yaml.
    settings_files = sorted(
        p for p in adir.glob("*/settings.yaml") if p.is_file()
    )
    if not settings_files:
        quiet = getattr(args, "quiet", False)
        if not quiet:
            print("No agents configured.")
        return 0

    quiet = getattr(args, "quiet", False)
    if quiet:
        for f in settings_files:
            # The on-disk dir name is the canonical NODE (``℘``); show ``+`` to the user.
            print(display_agent_ref(f.parent.name))
        return 0

    print(f"{'NAME':<20} {'MODEL'}")
    for f in settings_files:
        cfg = load_agent_config(f)
        name = display_agent_ref(f.parent.name)
        model = cfg.state.get("model", "-")
        print(f"{name:<20} {model}")
    return 0


def run_info(args: argparse.Namespace) -> int:
    """Show agent configuration details."""
    from kanibako.agent_config import agent_settings_path, load_agent_config

    try:
        std = _load_std()
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    # The positional may be a persona ref with the typable ``+``; canonicalise to
    # the ``℘`` node-name to locate the on-disk dir, but DISPLAY the ``+`` form.
    agent_id = canonicalize_agent_ref(args.agent_id)
    agent_display = display_agent_ref(agent_id)
    path = agent_settings_path(std.agents, agent_id)
    if not path.exists():
        print(
            f"Error: agent '{agent_display}' not found ({path})", file=sys.stderr
        )
        return 1

    cfg = load_agent_config(path)
    print(f"Name:         {cfg.name or agent_display}")
    if cfg.run_args:
        print(f"Default args: {' '.join(cfg.run_args)}")
    else:
        print("Default args: (none)")

    if cfg.state:
        print("State:")
        for k, v in sorted(cfg.state.items()):
            print(f"  {k} = {v}")
    else:
        print("State:        (none)")

    if cfg.env:
        print("Env:")
        for k, v in sorted(cfg.env.items()):
            print(f"  {k} = {v}")
    else:
        print("Env:          (none)")

    # SECRET category POINTERS (VAR -> host path). The token file contents (the
    # secret) are never read here — only the path is shown.
    if cfg.secret_path:
        print("Secret paths:")
        for k, v in sorted(cfg.secret_path.items()):
            print(f"  {k} = {v}")

    return 0


def run_set(args: argparse.Namespace) -> int:
    """``agent set <agent> <key>=<value>``."""
    args.reset = None
    args.all_keys = False
    args.effective = False
    args.force = False
    return _run_agent_config(args)


def run_reset(args: argparse.Namespace) -> int:
    """``agent reset <agent> <key>`` / ``agent reset <agent> --all``."""
    key = getattr(args, "key", None)
    all_keys = getattr(args, "all_keys", False)
    if not all_keys and not key:
        print("Error: reset requires a key (or --all)", file=sys.stderr)
        return 1
    # The shared body uses ``reset`` as a presence sentinel and reads the key
    # from ``reset`` (or, with the const fallback, from ``key_value``).
    args.reset = key if key else "__RESET__"
    args.key_value = key
    args.effective = False
    return _run_agent_config(args)


def run_get(args: argparse.Namespace) -> int:
    """``agent get <agent> <key>``."""
    args.key_value = args.key
    args.reset = None
    args.all_keys = False
    args.effective = False
    args.force = False
    return _run_agent_config(args)


def run_show(args: argparse.Namespace) -> int:
    """``agent show <agent> [--effective]``."""
    args.key_value = None
    args.reset = None
    args.all_keys = False
    args.force = False
    return _run_agent_config(args)


def _run_agent_config(args: argparse.Namespace) -> int:
    """Shared agent-config engine dispatch.

    Maps config keys to agent config sections:
      model, start_mode, etc. -> state keys
      env.X                   -> [env]
      shell, run_args, name   -> identity keys
    """
    from kanibako.agent_config import (
        agent_settings_path,
        load_agent_config,
    )
    from kanibako.config import coerce_bool
    from kanibako.config_interface import (
        _is_auto_approve_key,
        _remove_nested_toml_key,
        _write_nested_toml_key,
    )

    try:
        std = _load_std()
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    # Canonicalise the (possibly ``+``) persona ref to the ``℘`` node to locate the
    # on-disk dir; DISPLAY the ``+`` form.
    agent_id = canonicalize_agent_ref(args.agent_id)
    agent_display = display_agent_ref(agent_id)
    path = agent_settings_path(std.agents, agent_id)
    if not path.exists():
        print(
            f"Error: agent '{agent_display}' not found ({path})", file=sys.stderr
        )
        return 1

    key_value = getattr(args, "key_value", None)

    # Handle --reset
    if args.reset is not None:
        if args.all_keys:
            if not args.force:
                from kanibako.utils import confirm_prompt
                from kanibako.errors import UserCancelled

                try:
                    confirm_prompt(
                        "Remove all config overrides? Type 'yes' to proceed: "
                    )
                except UserCancelled:
                    print("Aborted.")
                    return 0
            # Sparse "remove all user overrides": drop the env table entirely and,
            # from [agent], every key EXCEPT ``name`` (this removes run_args, all
            # state keys, AND the discriminated ``<node>`` sub-table that holds
            # secret_path / node binds), then prune the now-empty [agent] table.
            # PRESERVES name + tweakcc — behavior-parity with the old de-sparse reset.
            # Sparse write: no default keys re-materialized ([[settings-must-map-to-
            # keystore-key]]).
            #
            # Report a COUNT of the overrides actually removed — the SAME wording the
            # other scopes' ``reset_all`` (config_interface.py) prints. Each secret_path
            # entry under ``agent.<node>.secret_path`` counts individually (parity with
            # the old flat env_file count); each other removed [agent] key counts once.
            from kanibako.config_io import dump_doc, load_doc

            data = load_doc(path)
            count = 0
            env_tbl = data.pop("env", None)
            if isinstance(env_tbl, dict):
                count += len(env_tbl)
            agent_sec = data.get("agent")
            if isinstance(agent_sec, dict):
                for k in [k for k in agent_sec if k != "name"]:
                    val = agent_sec[k]
                    if k == agent_id and isinstance(val, dict):
                        # The discriminated node sub-table: count each secret_path
                        # pointer per-VAR (parity with the old flat env_file count),
                        # plus one for any other node content (e.g. node binds).
                        secret_sub = val.get("secret_path")
                        if isinstance(secret_sub, dict):
                            count += len(secret_sub)
                        if any(kk != "secret_path" for kk in val):
                            count += 1
                    else:
                        count += 1
                    del agent_sec[k]
                if not agent_sec:
                    del data["agent"]
            dump_doc(path, data)
            print(
                f"Reset {count} override(s)." if count else "No overrides to reset."
            )
            return 0

        # Key can come from --reset VALUE or from positional key_value.
        reset_key = args.reset if args.reset != "__RESET__" else key_value
        if not reset_key:
            print("Error: reset requires a key (or --all)", file=sys.stderr)
            return 1

        key = reset_key.strip()
        sections, leaf = _agent_key_route(key, agent_id)
        changed = _remove_nested_toml_key(path, sections, leaf)
        if changed:
            # Honest cleared-form (F7), same contract as every other noun's
            # reset. This engine edits the sparse settings file directly, NOT
            # the assemble/merge cascade, so it has no resolved cascade inputs
            # to thread — the cleared-only fallback (effective=None) is the
            # correct honest form here (no new plumbing for this micro-block).
            from kanibako.config_interface import (
                ConfigLevel,
                _honest_reset_message,
            )

            print(_honest_reset_message(key, ConfigLevel.agent))
        else:
            print(f"No override for {key}")
        return 0

    # Parse key/value argument
    if key_value is None:
        # Show mode — read the config only where the READ paths need it.
        cfg = load_agent_config(path)
        return _show_agent_config(cfg, agent_display, effective=args.effective)

    if "=" in key_value:
        key, _, value = key_value.partition("=")
        key = key.strip()
        value = value.strip()
        # Write-time validation for the auth-critical ``auto_approve`` permission
        # key (parity with the ``config set`` path in config_interface.py). This
        # sibling ``agent set`` verb writes the SAME keyspace slot but through a
        # different setter, so without this guard a typo (``auto_approve=flase``)
        # would land verbatim as the string ``"flase"`` and ``coerce_bool`` to
        # None at LAUNCH — falling back to the PERMISSIVE default (True →
        # ``--dangerously-skip-permissions``), the unsafe direction. Reject a
        # non-bool value NOW using the SAME truth table (``config.coerce_bool``)
        # the launch coercion uses; only ``auto_approve`` is guarded, never
        # ``model`` / ``allow_helpers`` / ``run_args`` / ``env.*`` / ``secret_path.*``.
        if _is_auto_approve_key(key) and coerce_bool(value) is None:
            print(
                f"Error: auto_approve must be a boolean (true/false); got {value!r}",
                file=sys.stderr,
            )
            return 1
        sections, leaf = _agent_key_route(key, agent_id)
        # run_args is stored as a LIST (space-split); everything else is the
        # raw string. Sparse write — only the touched key is materialized.
        stored: object = value.split() if key == "run_args" else value
        _write_nested_toml_key(path, sections, leaf, stored)
        print(f"Set {key}={value}")
        return 0

    # Get mode
    key = key_value.strip()
    cfg = load_agent_config(path)
    val = _get_agent_key(cfg, key)
    if val is not None:
        print(val)
    else:
        print("(not set)", file=sys.stderr)
    return 0


def _get_agent_key(cfg: AgentConfig, key: str) -> str | None:
    """Read a single key from agent config."""
    # secret_path.<VAR> — the SECRET category POINTER (host path). Checked before the
    # ``env.`` prefix. Returns the stored PATH, never the (secret) file contents.
    if key.startswith("secret_path."):
        var = key[len("secret_path."):]
        return cfg.secret_path.get(var)
    if key.startswith("env."):
        env_name = key[4:]
        return cfg.env.get(env_name)
    if key == "name":
        return cfg.name or None
    if key == "run_args":
        return " ".join(cfg.run_args) if cfg.run_args else None
    # Everything else goes to state
    return cfg.state.get(key)


def _agent_key_route(key: str, node: str) -> tuple[tuple[str, ...], str]:
    """Map an agent config *key* to its ``(sections, leaf)`` file path.

    Single source of truth for the SPARSE set/reset routing (mirrors the read
    routing in :func:`_get_agent_key`). ``secret_path.`` is tested BEFORE ``env.``
    so ``secret_path.X`` is a pointer named ``X``. A ``secret_path.<VAR>`` pointer is
    stored DISCRIMINATED under ``agent.<node>.secret_path.<VAR>`` (the SAME first-class
    SECRET category shape the ``config set agent.<node>.secret_path.<VAR>`` route and
    ``_agent_partial`` use), so *node* (the agent id) is threaded in. ``name``,
    ``run_args``, and every other (state) key live under the ``[agent]`` table.
    """
    # secret_path.<VAR> — the SECRET category POINTER (host path), discriminated
    # under agent.<node>.secret_path (spec §2a; RENAMED from rc-only env_file). The
    # secret stays in the host file; only the PATH is persisted (never read here).
    if key.startswith("secret_path."):
        return ("agent", node, "secret_path"), key[len("secret_path."):]
    if key.startswith("env."):
        return ("env",), key[len("env."):]
    # name / run_args / state (model, auto_approve, allow_helpers, …).
    return ("agent",), key


def _show_agent_config(
    cfg: AgentConfig, agent_id: str, *, effective: bool = False,
) -> int:
    """Display agent config."""
    has_output = False

    # Identity keys
    print(f"  name = {cfg.name or agent_id}")
    if cfg.run_args:
        print(f"  run_args = {cfg.run_args}")
    has_output = True

    # agent-state keys
    if cfg.state:
        for k, v in sorted(cfg.state.items()):
            print(f"  {k} = {v}")
        has_output = True
    elif effective:
        print("  # (no state overrides)")

    # [env] section
    if cfg.env:
        for k, v in sorted(cfg.env.items()):
            print(f"  env.{k} = {v}")
        has_output = True

    # secret_path POINTERS (VAR -> host path). Only the PATH is shown; the token
    # file contents (the secret) are never read here.
    if cfg.secret_path:
        for k, v in sorted(cfg.secret_path.items()):
            print(f"  secret_path.{k} = {v}")
        has_output = True

    if not has_output:
        print("  (no overrides)")

    return 0


def run_reauth(args: argparse.Namespace) -> int:
    """Check authentication and login if needed."""
    from kanibako.config import (
        BOX_META_FILE,
        config_file_path,
        load_config,
        load_merged_config,
        resolve_agent,
    )
    from kanibako.agent_ref import harness_of
    from kanibako.paths import xdg, load_std_paths, workset_settings_path
    from kanibako.targets import resolve_target

    config_file = config_file_path(xdg("XDG_CONFIG_HOME", ".config"))
    config = load_config(config_file)

    # Resolve project to check auth mode.  Reconcile the positional subject with
    # the blanket --box flag (same → warn / differ → error), then route through
    # the path-or-name resolver.
    from kanibako.commands.flags import resolve_subject_value
    from kanibako.paths import resolve_box_target
    std = load_std_paths(config)
    subject = resolve_subject_value(
        getattr(args, "project", None), getattr(args, "box", None),
    )
    proj = resolve_box_target(std, config, subject)

    # Resolve the agent UP FRONT via the unified cascade (explicit > box >
    # workset > system default → installed-count rule).  reauth is an
    # agent-requiring command, so a resolution failure raises a typed
    # AgentResolutionError that the top-level cli.py handler surfaces verbatim
    # (Gate-2a/2b) with a non-zero exit — never a silent fall-through.
    project_toml = proj.metadata_path / BOX_META_FILE
    workset_path = workset_settings_path(proj.group)
    merged = load_merged_config(
        config_file,
        project_toml if project_toml.exists() else None,
        workset_path=workset_path,
    )
    agent_name = resolve_agent(
        explicit_agent=getattr(args, "agent", None),  # Phase D seam (--agent)
        box_agent_name=merged.box_agent_name,
        workset_agent=None,  # merged.box_agent_name already folds the workset tier
        system_default_path=std.settings,
        project_path=proj.project_path,
    )
    # ``agent_name`` is the NODE-name (persona identity); the target/plugin is keyed
    # by the HARNESS. ``agent_settings_path`` above keeps the node (keyspace slot).
    target = resolve_target(harness_of(agent_name), proj.project_path)

    if not target.has_binary:
        print("No agent target configured.", file=sys.stderr)
        return 1

    # Auth 3-tier SHARING + persona endpoint: resolve BOTH per-box decisions off ONE
    # launch snapshot (single-route — the same pipeline ``start`` uses), for the
    # resolved agent. The auth display below gates on whether the box shares; the
    # endpoint drives the OAuth-suppress cred fork (block B) so a reauth on a
    # custom-endpoint box never syncs the Anthropic token into a box pointed at a
    # third-party endpoint.
    from kanibako.agent_config import agent_settings_path, load_agent_config
    from kanibako.commands.start import _resolve_box_launch_decisions
    agent_cfg_path = agent_settings_path(std.agents, agent_name)
    reauth_agent_cfg = (
        load_agent_config(agent_cfg_path) if agent_cfg_path.exists() else None
    )
    auth_src, active_endpoint = _resolve_box_launch_decisions(
        std=std,
        proj=proj,
        target=target,
        agent_name=agent_name,
        agent_cfg=reauth_agent_cfg,
        system_settings_path=std.settings,
        agent_cfg_path=agent_cfg_path,
    )
    suppress_oauth = active_endpoint is not None

    if not auth_src.shares:
        # Private box: check project's own credentials instead of the source.
        creds_path = target.credential_check_path(proj.shell_path)
        if creds_path and creds_path.is_file():
            print(
                f"{target.display_name}: distinct auth (project credentials exist).",
                file=sys.stderr,
            )
            return 0
        else:
            print(
                f"{target.display_name}: distinct auth -- no credentials found. "
                "Launch the container to authenticate.",
                file=sys.stderr,
            )
            return 1

    if target.check_auth():
        # Sync refreshed credentials to the project shell directory.  Mirror the
        # start.py gate exactly: descriptor-bearing targets route their cred
        # refresh through the credsync engine (descriptor.cred_files); only legacy
        # (desc is None) targets fall back to the per-plugin refresh hook.  An
        # ungated target.refresh_credentials here would push a descriptor agent
        # (e.g. goose) down its legacy path / bespoke copy.
        if auth_src.shares:
            from pathlib import Path

            from kanibako.targets import credsync

            desc = target.descriptor
            if desc is not None:
                credsync.refresh_box_credentials(
                    desc, target, auth=auth_src, host_home=Path.home(),
                    project_home=proj.shell_path,
                    suppress_oauth=suppress_oauth,
                )
            else:
                target.refresh_credentials(proj.shell_path)
        print(f"{target.display_name}: authenticated.", file=sys.stderr)
        return 0

    # Auth failed.  If the agent declares an interactive in-box setup command
    # (goose ``configure`` / codex ``login``), run it in the box so the user can
    # configure / log in there (host-side reauth can't do it — the credential
    # lives in box-state), then re-check.  ``_run_container(setup_only=True)``
    # assembles the box, hits the same FIX-2 in-box-setup path, and returns
    # WITHOUT launching a full agent session.  Agents with no setup command
    # (claude by default) fall through to the existing failure message.
    if target.setup_entrypoint is not None:
        from kanibako.commands.start import _run_container
        return _run_container(
            project_dir=subject,
            entrypoint=None,
            image_override=None,
            new_session=False,
            safe_mode=False,
            resume_mode=False,
            extra_args=[],
            persistent=False,
            explicit_agent=getattr(args, "agent", None),
            setup_only=True,
        )

    print(f"{target.display_name}: authentication failed.", file=sys.stderr)
    return 1
