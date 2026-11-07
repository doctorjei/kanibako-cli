"""XDG base directories, runtime fallback, and host resolution maps."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from kanibako.log import get_logger
from kanibako.settings.bootstrap import (
  KANIBAKO_PATH,
  RUN_USER_UID_PATH,
  XDG_CONFIG_HOME,
  XDG_DATA_HOME,
  XDG_RUNTIME_DIR,
  XDG_SPEC_DEFAULTS,
)
from kanibako.settings.messages import (
  WARN_FALLBACK_RT_DIR,
  WARN_RELATIVE_XDG,
  WARN_RUNDIR_UNUSABLE,
)

logger = get_logger("paths")


def resolve_xdg(var_name: str, spec_default_suffix: str | None) -> Path:
  """Resolve an XDG base dir per the freedesktop spec — env honored iff set AND absolute."""
  val = os.environ.get(var_name, "")
  if val:
    if os.path.isabs(val):
      return Path(val).resolve()

    # Relative value: invalid per spec → ignore and fall through to default.
    logger.warning(WARN_RELATIVE_XDG, var_name, val)

  if spec_default_suffix is not None:
    return Path.home() / spec_default_suffix

  # XDG_RUNTIME_DIR has no spec default — pick a replacement and warn.
  return _fallback_runtime_dir(var_name)


# Process-lifetime cache of the chosen runtime-dir fallback, keyed by (var_name, env value).
_runtime_fallback_cache: dict[tuple[str, str], Path] = {}

#: The temp-root name prefix shared by BOTH last-resort arms (P10 — spelled ONCE, so a
#: reader is never left deciding whether the reused dir and the ``mkdtemp`` dir are one
#: thing or two).  It ends in ``-``, so the reused name is the prefix + the uid.
_RUNTIME_TMP_PREFIX = "kanibako-runtime-"


def _fallback_runtime_dir(var_name: str) -> Path:
  """Choose a replacement for an unset/invalid ``XDG_RUNTIME_DIR`` and warn (never silent)."""
  cache_key = (var_name, os.environ.get(var_name, ""))
  cached = _runtime_fallback_cache.get(cache_key)
  if cached is not None and cached.is_dir():
    return cached

  uid = os.getuid()
  run_user = Path(RUN_USER_UID_PATH % uid)
  if _runtime_base_usable(run_user):
    chosen = run_user / KANIBAKO_PATH
    chosen.mkdir(mode=0o700, parents=True, exist_ok=True)
    logger.warning(WARN_FALLBACK_RT_DIR, var_name, chosen, var_name)
    _runtime_fallback_cache[cache_key] = chosen
    return chosen

  # Last resort, FIRST arm: a STABLE per-uid dir under the temp root, reused by every
  # later process.  Not silent either way — both arms warn.
  #
  # ⚑ The name buys nothing on its own — any user can create it.  Under a sticky
  # world-writable root, ours is told from theirs by the owner + mode check below.
  stable = Path(tempfile.gettempdir()) / f"{_RUNTIME_TMP_PREFIX}{uid}"
  try:
    stable.mkdir(mode=0o700)
  except FileExistsError:
    # Pre-existing, and therefore NOT ours to chmod — the trust check may adopt it,
    # but nothing here may rewrite its mode.
    pass
  except OSError:
    # Unusable temp root, or a non-directory planted in the way (ENOTDIR).  Fall
    # through: the check below will refuse it too, and the mkdtemp arm answers.
    pass
  else:
    # We just created it, so this is the ONE path we may chmod.  `mkdir`'s mode is
    # masked by the umask, and a umask that strips owner bits leaves a dir the check
    # below would refuse (or that we could not write) — so re-assert 0700 here.
    stable.chmod(0o700)
  if _runtime_base_usable(stable, follow_symlinks=False, require_private=True):
    logger.warning(WARN_RUNDIR_UNUSABLE, var_name, uid, stable, var_name)
    _runtime_fallback_cache[cache_key] = stable
    return stable

  # Last resort, SECOND arm: a fresh 0700 temp dir, exactly as before.  A stable name
  # we could not trust is worse than a name nobody else can predict.
  chosen = Path(tempfile.mkdtemp(prefix=_RUNTIME_TMP_PREFIX))
  chosen.chmod(0o700)
  logger.warning(WARN_RUNDIR_UNUSABLE, var_name, uid, chosen, var_name)
  _runtime_fallback_cache[cache_key] = chosen
  return chosen


def _runtime_base_usable(
  base: Path, *, follow_symlinks: bool = True, require_private: bool = False
) -> bool:
  """True iff *base* is a directory we own and can write to (any OS error ⇒ not usable).

  ⚑ THE OWNER TEST IS SPELLED ONCE, HERE.  Both callers — the ``/run/user`` base and
  the stable temp dir — reach it through this function, because a second copy of
  "do we own it" is how two arms drift apart on the one check that decides whether
  kanibako will write into a directory somebody else may also reach.

  ⚑ *follow_symlinks* and *require_private* are keyword-only and default to exactly
  what the ``/run/user`` call has always got: ``stat``, which follows a link, and no
  mode test.  That call site passes NEITHER, so its behavior is unchanged.  The
  stable temp dir is a different trust problem — a PREDICTABLE name in a
  world-writable root — so it passes both: ``lstat`` so a planted symlink is refused
  as a symlink instead of being read through to its target, and a mode test so a
  group- or other-accessible directory is refused.
  """
  try:
    st = base.stat() if follow_symlinks else os.lstat(base)
  except OSError:
    return False
  import stat as _stat

  if not follow_symlinks and _stat.S_ISLNK(st.st_mode):
    return False
  if not _stat.S_ISDIR(st.st_mode):
    return False
  if st.st_uid != os.getuid():
    return False
  if require_private and st.st_mode & 0o077:
    return False
  return os.access(base, os.W_OK | os.X_OK)


def xdg(env_var: str, default_suffix: str) -> Path:
  """Backward-compatible thin wrapper over :func:`resolve_xdg` for plain XDG base dirs."""
  return resolve_xdg(env_var, default_suffix)


def user_config_home() -> Path:
  """The XDG config base dir — the ONE internal resolver for directory users
  (keyspec §1A "never composed").

  ⚑ NOT A KEY: keyspec §1A declares the finished config file and no companion
  ``config_home`` key — 18 sites use this value as a DIRECTORY, two outside kanibako's
  tree entirely (VS Code's ``settings.json``, the persona-grata root), so they route
  through here instead.
  """
  return xdg(XDG_CONFIG_HOME, XDG_SPEC_DEFAULTS[XDG_CONFIG_HOME])


def default_data_home() -> Path:
  """The XDG data base dir: ``$XDG_DATA_HOME``, or its spec default under ``$HOME``."""
  return xdg(XDG_DATA_HOME, XDG_SPEC_DEFAULTS[XDG_DATA_HOME])


def spec_default_xdg_map(data_home: Path | None) -> dict[str, str]:
  """The XDG vars that HAVE a spec default (data/config/state/cache) — no ``XDG_RUNTIME_DIR``.

  ⚑ Side-effect-free: unlike ``XDG_RUNTIME_DIR`` (see :func:`_fallback_runtime_dir`), none of
  these four ever mkdir a fallback dir — :func:`resolve_data_path` relies on that to stay total.
  ⚑ PUBLIC for the second caller that needs exactly that guarantee:
  ``settings/workset_dirkeys.py`` resolves ``{$XDG_*}`` inside the ancestor WALK, where a
  mkdir-and-warn on a directory that turns out not to be a workset is a real side effect.
  It is the SAME builder, not a copy — :func:`host_xdg_map` still wraps it.
  """
  xdg_map: dict[str, str] = {}
  for name, suffix in XDG_SPEC_DEFAULTS.items():
    if name == XDG_DATA_HOME and data_home is not None:
      # Already resolved by the caller — don't re-read the env (it would re-warn).
      xdg_map[name] = str(data_home)
    else:
      xdg_map[name] = str(resolve_xdg(name, suffix))
  return xdg_map


def host_xdg_map(data_home: Path | None = None) -> dict[str, str]:
  """THE single builder for the ``xdg=`` argument of every host-side ``ResolveCtx``."""
  xdg_map = spec_default_xdg_map(data_home)
  xdg_map[XDG_RUNTIME_DIR] = str(resolve_xdg(XDG_RUNTIME_DIR, None))
  return xdg_map
