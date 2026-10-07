"""Make a TESTING-ONLY agent plugin visible to discovery the way an install does.

A plugin reaches :func:`kanibako.targets.discover_targets` through the
``kanibako.agents`` entry-point group (keyspec §2, *"Plugins load only from
installed packages"*).  Discovery reads installed metadata and nothing else, so a
test that wants a real ``Target`` has to present the two files
:mod:`importlib.metadata` needs to recognise a package — a ``*.dist-info/``
holding ``METADATA`` and ``entry_points.txt`` — beside the plugin module.  Putting
the returned directory on ``PYTHONPATH`` is then all a subprocess needs: no build,
no ``pip``, and nothing under ``tests/`` is ever packaged.

TESTING-ONLY: production code never imports this, and every plugin it installs
lives under ``tests/`` (see :mod:`tests.e2e.test_interactive_attach`).
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

__all__ = ["entry_point_plugin_path", "install_entry_point_plugin"]


def install_entry_point_plugin(
    site_dir: Path,
    *,
    src: Path,
    module: str,
    entry: str,
    attr: str,
    dist: str,
) -> Path:
    """Install *src* into *site_dir* as the ``kanibako.agents`` entry point *entry*.

    *module* is the importable module name ``entry_points.txt`` loads, *attr* the
    ``Target`` subclass it must yield, *entry* the entry-point key (``ep.name``,
    which is what ``_register`` is handed and derives the node from), and *dist*
    the distribution name carried by the ``*.dist-info`` directory.  Returns
    *site_dir* for the caller to put on ``PYTHONPATH``.
    """
    site_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, site_dir / f"{module}.py")

    dist_info = site_dir / f"{dist}-0.0.dist-info"
    dist_info.mkdir()
    (dist_info / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: {dist}\nVersion: 0.0\n"
    )
    (dist_info / "entry_points.txt").write_text(
        f"[kanibako.agents]\n{entry} = {module}:{attr}\n"
    )
    return site_dir


def entry_point_plugin_path(env: dict[str, str], *site_dirs: Path) -> str:
    """``PYTHONPATH`` for *env* with *site_dirs* ahead of whatever it already carries.

    :data:`os.pathsep`-joined so a Windows host splits it the same way the child
    process does, and an inherited value is preserved rather than replaced.
    """
    existing = env.get("PYTHONPATH", "")
    return os.pathsep.join([*(str(d) for d in site_dirs), *([existing] if existing else [])])