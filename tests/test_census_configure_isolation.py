"""The census's CONFIGURE-TIME priming call must not read the host's user dirs.

⚑ WHY A CHILD PROCESS.  The defect is not observable from inside a test: the
``settings_keyspace_probe`` memo that carries it is primed in
``pytest_configure``, before this file — or any test — exists, and ``_PLUGINS`` is
process-global and deliberately not resettable from a test (see ``_isolate_user_dirs``
in ``tests/conftest.py``, which says the same). The only honest way to ask "what did
the priming call read?" is to run a whole pytest session in a child and look at what
that process touched.

⚑ THE OBSERVABLE IS THE USER PLUGIN TIER'S IMPORT, in two spellings — the default
``<XDG_DATA_HOME>/kanibako/plugins`` and a directory only a ``kanibako.cfg`` can point
at. Discovery resolves ``config.data`` through ``resolve_data_path``, which reads the
config file BEFORE it falls back to that default, so a cure that redirected only the
XDG bases would still read the host's config file.

⚑ THE SENTINEL IS NOT SHAPED AS A ``Target``.  ``discover_targets`` execs every
``*.py`` in the directory before it looks for ``Target`` subclasses, so the module
BODY alone proves the tier was read — and a sentinel that declared a class could
contribute vocabulary to the census and mask the leak it exists to reveal.

⚑⚑ THE SENTINEL CREATES ITS OWN PARENT, and that is load-bearing rather than tidy.
``_scan_directory_plugins`` wraps every ``exec_module`` in ``except Exception:
continue`` — deliberately, so one broken plugin cannot take discovery down. A
sentinel that wrote into a directory that did not yet exist therefore raised
``FileNotFoundError``, was swallowed, and the test went GREEN having proved nothing:
the first version of this file passed against the defect. A probe that can fail
quietly is worse than no probe, because it looks like a fix.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from kanibako.settings.bootstrap import CONFIG_FILE, KANIBAKO_PATH

from tests._user_dirs import HOME_VAR, USER_DIR_VARS, user_dir_env_vars, user_dir_tree

#: One small, fast, already-green file for the child to collect and run. The child's
#: own work is incidental — the assertion is about what its CONFIGURE did.
CHILD_TEST = "tests/test_atomic.py"

REPO_ROOT = Path(__file__).resolve().parent.parent

SENTINEL_NAME = "sentinel_user_plugin.py"

#: Where the sentinel's marker lands. OUTSIDE the tree the child is redirected to, so
#: "the tree contains what it should" can never be mistaken for "nothing was read".
MARKER_DIRNAME = "OUTSIDE-the-redirected-tree"

#: The child environment's root. Every redirected variable lives under it.
HOST_DIRNAME = "host"

#: A probe the CHILD loads, so the child reports its own primed memo back to this
#: process. It is written into ``tmp_path`` rather than shipped: it is a fixture for
#: one assertion, and a repo module for it would be a file outliving its reason.
MEMO_PROBE_MODULE = "census_memo_probe"
MEMO_PROBE_ENV = "KANI_CENSUS_MEMO_PROBE_OUT"


def _sentinel_source(marker: Path) -> str:
  """A user-tier plugin module whose IMPORT writes *marker*."""
  return (
    "import pathlib\n"
    f"_p = pathlib.Path({str(marker)!r})\n"
    "_p.parent.mkdir(parents=True, exist_ok=True)\n"
    "_p.write_text('imported at configure time')\n"
  )


def _write_sentinel_plugin(plugins_dir: Path, marker: Path) -> None:
  plugins_dir.mkdir(parents=True, exist_ok=True)
  plugins_dir.joinpath(SENTINEL_NAME).write_text(_sentinel_source(marker), encoding="utf-8")


def _child_env(root: Path) -> dict[str, str]:
  """*root*'s per-variable dirs as a child environment, plus this worktree's ``src``."""
  env = dict(os.environ)
  for var, path in user_dir_tree(root).items():
    path.mkdir(parents=True, exist_ok=True)
    env[var] = str(path)
  # ⚑ The child must import the tree under test, not whatever checkout the shared
  # venv happens to have installed.
  src = str(REPO_ROOT / "src")
  env["PYTHONPATH"] = os.pathsep.join(
    [src, *([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])],
  )
  return env


def _run_child(env: dict[str, str], *extra: str) -> subprocess.CompletedProcess[str]:
  return subprocess.run(
    [sys.executable, "-m", "pytest", "-q", "--no-header", "-p", "no:cacheprovider",
     *extra, str(REPO_ROOT / CHILD_TEST)],
    cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=300,
  )


def _armed_host(tmp_path: Path) -> tuple[dict[str, str], Path]:
  """A child environment plus the marker its user plugin tier would write.

  Returns the env and a marker path proven to lie OUTSIDE the redirected tree, so a
  later assertion cannot pass merely because the tree is self-contained.
  """
  host = tmp_path / HOST_DIRNAME
  env = _child_env(host)
  marker = tmp_path / MARKER_DIRNAME / "sentinel-imported"
  assert host not in marker.parents and marker != host, (
    f"the marker {marker} is not outside the redirected tree at {host}"
  )
  assert not marker.exists(), "the marker already exists before anything ran"
  return env, marker


def _child_ran(proc: subprocess.CompletedProcess[str]) -> None:
  """A child that could not run proves nothing — and its silence reads as a pass."""
  assert proc.returncode == 0, (
    f"the child pytest run failed, so the probe below would be vacuous "
    f"(rc={proc.returncode})\n--- child stdout ---\n{proc.stdout}\n"
    f"--- child stderr ---\n{proc.stderr}"
  )


def test_census_priming_never_imports_the_host_user_plugin_tier(tmp_path):
  """The default user plugin tier must not be imported at CONFIGURE time.

  ⚑ THE DEFECT, PRECISELY.  ``pytest_configure`` primes
  ``plugin_agent_leaf_map()`` with whatever the environment hands it, and discovery
  scans ``<config.data>/plugins/``. On a machine that has one, that plugin's declared
  leaves become the session's vocabulary — and the memo is fixed for the whole
  process, long after the autouse ``_isolate_user_dirs`` fixture would have
  redirected anything.
  """
  env, marker = _armed_host(tmp_path)
  _write_sentinel_plugin(Path(env["XDG_DATA_HOME"]) / KANIBAKO_PATH / "plugins", marker)

  proc = _run_child(env)
  _child_ran(proc)

  assert not marker.exists(), (
    "the child pytest run imported the user plugin tier at CONFIGURE time: the "
    f"census primed discovery outside a throwaway HOME (child rc={proc.returncode})\n"
    f"--- child stdout ---\n{proc.stdout}\n--- child stderr ---\n{proc.stderr}"
  )


def test_census_priming_never_follows_a_host_authored_config_file(tmp_path):
  """A ``kanibako.cfg`` must not be read at CONFIGURE time either.

  ⚑ THE CONFIG READ IS A SEPARATE INPUT FROM THE XDG BASES.
  ``resolve_data_path`` reads ``$XDG_CONFIG_HOME/kanibako.cfg`` first and only then
  falls back to ``<XDG_DATA_HOME>/kanibako``. This config repoints ``config.data`` at a
  directory that is NOT the fallback, so the sentinel is reachable only through the
  file: a cure that redirected the XDG data base alone leaves this one armed.

  ⚑ And it is a real read, not a degraded one: ``resolve_data_path`` is total and
  swallows a malformed file into that same fallback, so a sentinel sitting at the
  FALLBACK path would pass here for the wrong reason.
  """
  env, marker = _armed_host(tmp_path)
  repointed = Path(env["XDG_DATA_HOME"]) / "elsewhere"
  Path(env["XDG_CONFIG_HOME"], CONFIG_FILE).write_text(
    f"config:\n  data: {str(repointed)!r}\n", encoding="utf-8",
  )
  _write_sentinel_plugin(repointed / "plugins", marker)

  proc = _run_child(env)
  _child_ran(proc)

  assert not marker.exists(), (
    "the child pytest run followed a HOST-authored kanibako.cfg at CONFIGURE time: "
    f"the priming call read the config file (child rc={proc.returncode})\n"
    f"--- child stdout ---\n{proc.stdout}\n--- child stderr ---\n{proc.stderr}"
  )


def test_census_still_primes_the_memo_and_ignores_the_armed_host_tier(tmp_path):
  """⚑⚑ THE ORDERING HALF, WHICH THE TWO TESTS ABOVE CANNOT SEE.

  Redirecting the priming call is only half the change, and the half that matters
  is that priming still HAPPENS: ``_discover``'s memo is what every later test reads,
  and it must be primed before any test patches discovery. Deleting or making the call
  lazy would silence both leak tests above — the child would read nothing at all — while
  quietly handing the whole session an UNPRIMED keyspace. So this arms the host tier
  AND asserts the memo came back populated.

  ⚑ The probe is a CHILD-side plugin that reports ``_PLUGINS`` at
  ``pytest_collection_finish``: after every ``pytest_configure`` has run, and without
  this process poking at the child's internals. ``AGENT_LEAF_MAP``'s concession rule
  means an unprimed memo fails SAFE (everything unknown is allowed), which is exactly
  why a green suite cannot be the evidence that priming occurred.
  """
  env, marker = _armed_host(tmp_path)
  _write_sentinel_plugin(Path(env["XDG_DATA_HOME"]) / KANIBAKO_PATH / "plugins", marker)

  reported = tmp_path / "memo-reported-by-child.txt"
  probe_dir = tmp_path / "probe"
  probe_dir.mkdir()
  probe_dir.joinpath(f"{MEMO_PROBE_MODULE}.py").write_text(
    "import os\n"
    "\n"
    "def pytest_collection_finish(session):\n"
    "    from kanibako.settings import settings_keyspace_probe as probe\n"
    f"    out = os.environ[{MEMO_PROBE_ENV!r}]\n"
    "    with open(out, 'w', encoding='utf-8') as fh:\n"
    "        fh.write('none' if probe._PLUGINS is None\n"
    "                 else ','.join(sorted(probe._PLUGINS)))\n",
    encoding="utf-8",
  )
  env[MEMO_PROBE_ENV] = str(reported)
  env["PYTHONPATH"] = os.pathsep.join([env["PYTHONPATH"], str(probe_dir)])

  proc = _run_child(env, "-p", MEMO_PROBE_MODULE)
  _child_ran(proc)

  assert not marker.exists(), (
    "the child read the armed host plugin tier at CONFIGURE time"
  )
  assert reported.exists(), (
    f"the child never reported its memo (rc={proc.returncode})\n"
    f"--- child stdout ---\n{proc.stdout}\n--- child stderr ---\n{proc.stderr}"
  )
  keys = reported.read_text(encoding="utf-8")
  assert keys != "none", (
    "the census did not prime settings_keyspace_probe._discover at configure time: "
    "the memo is left to be built by whichever test asks first, which is the "
    "ordering this priming point exists to prevent"
  )
  # ⚑ ONE INSTALLED HARNESS IS ENOUGH and is chosen over the plugin set's size: the
  # list is whatever the environment has, and ``shell`` is seeded by discovery itself.
  assert "shell" in keys.split(","), f"discovery returned no harnesses: {keys!r}"


def test_throwaway_user_dirs_restores_the_environment_on_the_exception_path():

  """⚑ THE ``finally`` IS THE POINT. A restore that only ran on success leaks the
  throwaway tree into every later caller after a single priming failure — the same
  class of leak, moved. This is the one claim a child pytest cannot make, because the
  failure it guards is a raise from INSIDE the window."""
  from tests._user_dirs import throwaway_user_dirs

  # ⚑ Only SOME of the list is pre-set: the restore has to put back "absent" as
  # absent, not as a path that happened to be there before.
  env: dict[str, str] = {HOME_VAR: "/host/home", "XDG_CONFIG_HOME": "/host/config"}
  untouched = "PATH"
  env[untouched] = "/host/bin"
  before = dict(env)
  root = None

  try:
    with throwaway_user_dirs(env) as opened:
      root = opened
      for var in user_dir_env_vars():
        assert Path(env[var]) == user_dir_tree(root)[var], (
          f"{var} was redirected somewhere other than the shared layout"
        )
      # ⚑ A variable outside the list is not this window's business at all.
      assert env[untouched] == before[untouched]
      raise RuntimeError("the priming call failed")
  except RuntimeError as exc:
    assert str(exc) == "the priming call failed", "the window swallowed the failure"
  else:  # pragma: no cover - the raise above is the assertion
    raise AssertionError("the body did not propagate its exception")

  assert env == before, f"the environment was not restored exactly: {env}"
  assert not root.exists(), "the throwaway tree outlived the window"


def test_throwaway_user_dirs_redirects_exactly_the_shared_variable_list():
  """⚑ ONE CARRIER. Two isolations that do DIFFERENT jobs may not each carry their own
  copy of the variable list — the copy that drifts is the defect this module exists to
  prevent. Redirecting the XDG bases without ``HOME`` would still reach the host's
  store through the ``~/.local/share`` default for ``config.data``."""
  from tests._user_dirs import throwaway_user_dirs

  env: dict[str, str] = {name: f"/host/{name}" for name in user_dir_env_vars()}
  before = dict(env)

  with throwaway_user_dirs(env) as root:
    assert set(env) == set(before), "the window added or dropped a variable"
    for var in user_dir_env_vars():
      assert Path(env[var]) == user_dir_tree(root)[var]

  assert env == before


def test_isolate_user_dirs_and_the_census_read_one_variable_list():
  """⚑ THE CONFTEST SIDE OF THE SAME RULE, pinned where the fork would show up."""
  from tests import conftest as suite_conftest

  assert suite_conftest._USER_DIR_VARS is USER_DIR_VARS, (
    "tests/conftest.py's _USER_DIR_VARS is no longer the same object as "
    "tests._user_dirs.USER_DIR_VARS: the two isolations have forked"
  )
