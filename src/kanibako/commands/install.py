"""Shell-completion registration for the kanibako CLI.

The ``setup`` CLI command was replaced by lazy initialization
(``_ensure_initialized`` in ``cli.py``) plus the explicit ``kanibako setup``
wizard (``commands/setup_cmd.py``).  What remains here is
``_install_completion()``, which ``cli.py`` calls on first-run init.
"""

from __future__ import annotations

import subprocess

from kanibako.paths import xdg


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
