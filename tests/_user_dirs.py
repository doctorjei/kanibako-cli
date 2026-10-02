"""THE user-dir variable list, in one place, for BOTH of the suite's redirections.

⚑⚑ TWO REDIRECTIONS, TWO JOBS, ONE LIST.  The suite points ``HOME`` and the XDG
base dirs somewhere else twice, for two unrelated reasons, and this module is the
ONLY definition of which variables either of them moves:

* :func:`throwaway_user_dirs` — the **CONFIGURE-TIME WINDOW** in
  ``tests._keystore_census``.  ``pytest_configure`` runs before every fixture, so
  the autouse ``_isolate_user_dirs`` fixture in ``tests/conftest.py`` has redirected
  nothing by then.  This covers ONE call (the plugin-discovery priming) and
  restores the environment afterwards, on the exception path too.
* ``_isolate_user_dirs`` in ``tests/conftest.py`` — the **PER-TEST FRESHNESS**
  fixture, which redirects for every test except ``integration`` / ``e2e`` (those
  drive the real container runtime, whose rootless image store lives under the real
  ``HOME``/``XDG_DATA_HOME``).

  Neither subsumes the other and neither is a duplicate of the other, so neither may
  be merged into the other or deleted as redundant.  What they SHARE is this list;
  what they do not share is scope, lifetime, or who may still see the real home.

⚑ THE LIST IS HERE, NOT IN EITHER CALLER, because the census is configured before
``tests/conftest`` is finished importing: a module-level name living in the conftest
would not be available to the plugin that runs first, and a literal pasted into both
is exactly the pair that drifts.  Import it here instead.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Iterator, MutableMapping
from contextlib import contextmanager
from pathlib import Path

#: ``HOME`` is not an XDG base, but both redirections move it: the ``~/.local/share``
#: default for ``config.data`` is a function of ``HOME``, and a redirect that left it
#: in place would reach the developer's real store through that default.
HOME_VAR = "HOME"

#: The XDG base directories the suite redirects. ``XDG_RUNTIME_DIR`` is absent: it
#: holds no store, and its unset/invalid fallback is itself under test.
USER_DIR_VARS = ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME")


def user_dir_env_vars() -> tuple[str, ...]:
  """``HOME`` plus :data:`USER_DIR_VARS` — the full set either redirect sets."""
  return (HOME_VAR, *USER_DIR_VARS)


def user_dir_tree(root: Path) -> dict[str, Path]:
  """*root*'s per-variable directories: ``root/home`` plus one per XDG var.

  One mapping so both redirections pick the same layout, and so a variable added to
  :data:`USER_DIR_VARS` is created by both without a second edit.
  """
  return {
    HOME_VAR: root / "home",
    **{var: root / var.lower() for var in USER_DIR_VARS},
  }


@contextmanager
def throwaway_user_dirs(
  environ: MutableMapping[str, str] | None = None,
) -> Iterator[Path]:
  """Substitute a fresh ``mkdtemp`` tree for the user dirs for the BODY only.

  The configure-time window of the module docstring.

  ⚑ THE RESTORE IS IN A ``finally`` AND RECORDS UNSET AS UNSET.  A restore that
  only ran on the success path would leak the throwaway tree into every later test
  after a single priming failure — the same class of leak this exists to stop, moved.
  A variable that was absent before is removed rather than set to the tree's path,
  so an absent-then-present variable is not silently inverted.

  *environ* defaults to :data:`os.environ`; pass a mapping to drive this without
  touching the process (which is how it is unit-tested).
  """
  env = os.environ if environ is None else environ
  root = Path(tempfile.mkdtemp(prefix="kanibako-user-dirs-"))
  saved = {var: env.get(var) for var in user_dir_env_vars()}
  try:
    for var, path in user_dir_tree(root).items():
      path.mkdir(parents=True, exist_ok=True)
      env[var] = str(path)
    yield root
  finally:
    for var, was in saved.items():
      if was is None:
        env.pop(var, None)
      else:
        env[var] = was
    shutil.rmtree(root, ignore_errors=True)
