"""Detect how kanibako itself was installed + format tailored install commands.

The agent-resolution gates surface an actionable "install a plugin" hint when no
agent is installed (or a named agent's adapter is missing).  The right command
depends on how *kanibako itself* was installed (pipx inject vs uv tool --with vs
plain pip), so this module keys detection off kanibako's own environment.
"""

from __future__ import annotations

import os
import sysconfig
from pathlib import Path


def detect_install_method() -> str:
    """Return kanibako's own install method: ``"pipx"`` | ``"uv"`` | ``"pip"``.

    Detection is keyed off how *kanibako itself* was installed, checked in order
    (pipx, then uv, then pip as the undetected default):

    * **pipx**: ``PIPX_HOME`` or ``PIPX_BIN_DIR`` set, OR ``pipx`` appears as a
      path component of :data:`sys.prefix` (e.g. ``.../pipx/venvs/kanibako-cli``).
    * **uv**: ``UV_TOOL_DIR`` set, OR a ``uv`` path component AND ``tools``
      appear in :data:`sys.prefix`.
    * **pip**: the default when neither is detected.
    """
    import sys

    prefix = sys.prefix
    prefix_parts = Path(prefix).parts
    sep = os.sep

    # pipx first.
    if os.environ.get("PIPX_HOME") or os.environ.get("PIPX_BIN_DIR"):
        return "pipx"
    if "pipx" in prefix_parts:
        return "pipx"

    # uv next.
    if os.environ.get("UV_TOOL_DIR"):
        return "uv"
    if (f"{sep}uv{sep}" in prefix) and ("tools" in prefix):
        return "uv"

    # Undetected -> pip.
    return "pip"


def is_externally_managed() -> bool:
    """Return True when the active environment is PEP 668 externally-managed.

    Looks for an ``EXTERNALLY-MANAGED`` marker file next to the stdlib.  Any
    error (missing config, permissions) is swallowed and treated as False.
    """
    try:
        stdlib = sysconfig.get_path("stdlib")
        if not stdlib:
            return False
        return (Path(stdlib) / "EXTERNALLY-MANAGED").exists()
    except Exception:
        return False


def install_command(plugin: str) -> str:
    """Return the install command string for *plugin* tailored to kanibako's install method.

    *plugin* is used verbatim (callers pass e.g. a package name like
    ``kanibako-agent-claude``).

    * pipx -> ``pipx inject kanibako-cli <plugin>``
    * uv   -> ``uv tool install kanibako-cli --with <plugin>``
    * pip  -> ``pip install <plugin>`` (``--break-system-packages`` appended when
      the environment is externally-managed)
    """
    method = detect_install_method()
    if method == "pipx":
        return f"pipx inject kanibako-cli {plugin}"
    if method == "uv":
        return f"uv tool install kanibako-cli --with {plugin}"
    cmd = f"pip install {plugin}"
    if is_externally_managed():
        cmd += " --break-system-packages"
    return cmd
