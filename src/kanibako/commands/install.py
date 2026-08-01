"""kanibako install utilities: setup logic and shell completion.

The ``setup`` CLI command has been replaced by lazy initialization
(``_ensure_initialized`` in ``cli.py``).  This module is kept for
``_install_completion()`` and the ``run()`` helper used by lazy init
and tests.
"""

from __future__ import annotations

import argparse
import subprocess
import sys

from kanibako.config import (
    KanibakoConfig,
    config_file_path,
    load_config,
    write_global_config,
)
from kanibako.container import ContainerRuntime
from kanibako.paths import xdg


def run(args: argparse.Namespace) -> int:
    config_home = xdg("XDG_CONFIG_HOME", ".config")
    config_file = config_file_path(config_home)

    # ------------------------------------------------------------------
    # 1. Write config
    # ------------------------------------------------------------------
    if config_file.exists():
        print("Configuration file already exists, loading.")
        config = load_config(config_file)
    else:
        print("Writing general configuration file (kanibako_config.yaml)... ", end="", flush=True)
        config = KanibakoConfig()
        write_global_config(config_file, config)
        print("done!")

    # ------------------------------------------------------------------
    # 2. Create containers directory for user overrides
    # ------------------------------------------------------------------
    from pathlib import Path

    from kanibako.paths import resolve_system_paths

    data_home = xdg("XDG_DATA_HOME", ".local/share")
    sys_paths = resolve_system_paths(
        config.config_paths, data_home=data_home, home=Path.home(),
    )
    data_path = sys_paths["config.data"]
    containers_dest = data_path / "containers"
    containers_dest.mkdir(parents=True, exist_ok=True)

    # NOTE (block #3a, JC-3): the channel type-root skeleton is NO LONGER
    # pre-created at setup.  ``channelroot`` moved to Layer 2 (a ``system.*``
    # settings key) and the launch path creates the full skeleton (L7
    # guarantee-create for the bind sources + ``_seed_channel_files`` for the chat
    # logs).  No host-side pre-launch consumer of the skeleton exists (audit:
    # every reader is on the box-launch path), so this pre-creation was redundant
    # and is dropped.

    # Create agents directory and generate default per-agent settings files
    # (agents/<agent>/settings.yaml, inside each per-agent store dir).
    from kanibako.agent_config import (
        AgentConfig,
        agent_settings_path,
        write_agent_config,
    )
    from kanibako.targets import discover_targets

    agents_path = sys_paths["config.agents"]
    agents_path.mkdir(parents=True, exist_ok=True)

    # general (no-agent default)
    general_toml = agent_settings_path(agents_path, "general")
    if not general_toml.exists():
        write_agent_config(general_toml, AgentConfig(name="Shell"))

    # Each discovered target plugin
    target_names = list(discover_targets())
    for target_name, cls in discover_targets().items():
        target_toml = agent_settings_path(agents_path, target_name)
        if not target_toml.exists():
            write_agent_config(target_toml, cls().generate_agent_config())

    # Packaged content -> its ENUMERATED host stores (the box + workset moulds, the
    # system handbook, and every agent store), create-if-absent.  The layered
    # seed-once apply then seeds a new box store from them at create.
    from kanibako.paths import load_std_paths
    from kanibako.templates import install_packaged_templates

    install_packaged_templates(load_std_paths(config), target_names)
    # NOTE: this path is currently DEAD (no `set_defaults(func=run)` wiring — the
    # live init path is cli._ensure_initialized).  If it is ever re-wired as an
    # init entry point it MUST also write ``system.templates_stamp`` (mirror
    # cli._ensure_initialized), or a host initialized through it would hard-error
    # on its first agent command via config.template_staleness_gate.

    # Seed default global environment variables (don't overwrite existing).
    from kanibako.shellenv import read_env_file, write_env_file

    global_env_path = data_path / "env"
    global_env = read_env_file(global_env_path)
    _DEFAULT_ENV = {"COLORTERM": "truecolor"}
    for key, value in _DEFAULT_ENV.items():
        global_env.setdefault(key, value)
    write_env_file(global_env_path, global_env)

    # ------------------------------------------------------------------
    # 3. Pull or build base container image
    # ------------------------------------------------------------------
    try:
        runtime = ContainerRuntime()
        from kanibako.commands.image import resolve_image_reference
        image = resolve_image_reference(
            config.box_image, runtime, config.box_image,
        )
        if runtime.image_exists(image):
            print("Container rig already exists, skipping.")
        else:
            print(f"Pulling rig {image}...", flush=True)
            if runtime.pull(image, quiet=False):
                print("Rig pulled from registry!")
            else:
                print(
                    f"Warning: failed to pull rig '{image}'. Check your "
                    "network/registry access. To use a custom base image, build "
                    "it yourself (see github.com/doctorjei/kanibako-images) and "
                    "pass it via --image or set box_image in your config.",
                    file=sys.stderr,
                )
    except Exception as e:
        print(f"Warning: {e}", file=sys.stderr)
        print("Skipping rig setup.")

    # ------------------------------------------------------------------
    # 4. Register shell completion
    # ------------------------------------------------------------------
    print("Setting up shell completion... ", end="", flush=True)
    _install_completion()
    print("done!")

    return 0


def _install_completion() -> None:
    """Register bash/zsh completion for kanibako via argcomplete."""
    completions_dir = xdg("XDG_DATA_HOME", ".local/share") / "bash-completion" / "completions"
    completions_dir.mkdir(parents=True, exist_ok=True)
    target = completions_dir / "kanibako"

    try:
        result = subprocess.run(
            ["register-python-argcomplete", "kanibako"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0 and result.stdout.strip():
            target.write_text(result.stdout)
        else:
            print("(register-python-argcomplete failed, skipping)", end=" ")
    except FileNotFoundError:
        print("(argcomplete not on PATH, skipping)", end=" ")
