"""A container-runtime stand-in that really runs the probe script.

A baseline probe's contract is what the shell does with an executable NAME, so it
has to be observed in a real shell: a mocked ``subprocess.run`` cannot tell a
quoted word from an injected command.  :func:`local_sh_runtime` writes a shim that
stands in for ``<runtime> run ...`` and hands the trailing ``sh -lc <script>`` to
the host shell, which is the shell the probe would run inside the image.
"""

from __future__ import annotations

import stat
from pathlib import Path
from types import SimpleNamespace

# Matches both probe call shapes: `<runtime> run --rm <image> sh -lc <script>` and
# `<runtime> run --rm --entrypoint sh <image> -lc <script>`.  The script is always the
# last argument, right after the -lc/-c flag, so scanning for that flag is enough.  It
# is then re-passed with -c, because a shell takes a command STRING as the operand of
# -c; handing it over as a bare argument would ask the shell to run a file of that name.
_SHIM = """#!/bin/sh
while [ $# -gt 0 ]; do
  case "$1" in
    -lc|-c) shift; exec /bin/sh -c "$1" ;;
    *) shift ;;
  esac
done
exit 0
"""


def local_sh_runtime(tmp_path: Path) -> SimpleNamespace:
    """Return a runtime whose ``cmd`` runs the probe script in the host shell."""
    shim = tmp_path / "fake-runtime"
    shim.write_text(_SHIM)
    shim.chmod(shim.stat().st_mode | stat.S_IXUSR)
    return SimpleNamespace(cmd=str(shim))
