"""The census's CONFIGURE-TIME priming call, and the redirect window it runs inside.

⚑ WHY A CHILD PROCESS.  The defect is not observable from inside a test: the
``settings_keyspace_probe`` memo that carries it is primed in
``pytest_configure``, before this file — or any test — exists, and ``_PLUGINS`` is
process-global and deliberately not resettable from a test (see ``_isolate_user_dirs``
in ``tests/conftest.py``, which says the same). The only honest way to ask "what did
the priming call read?" is to run a whole pytest session in a child and look at what
that process touched.

⚑ WHAT IS PINNED HERE IS THAT THE PRIMING HAPPENS, not what it read.
``plugin_agent_leaf_map`` is eager ON PURPOSE: it is the priming point, and
``_discover``'s process memo is what every later test reads. Its concession rule
concedes an unprimed memo (an empty map means "no agent's vocabulary is known here"),
which fails SAFE — so a green suite is not evidence that priming occurred, and
``test_census_still_primes_the_memo`` is the only assertion here that can fail for a
reason about the code under it.

⚑ THE WINDOW HAS NO USER-DIR OBSERVABLE TO GUARD, and that is a property of the
priming call rather than of this file.  ``discover_targets`` reads installed
entry-point metadata and the ``kanibako.plugins`` namespace, and resolves no path
from the environment: it never consults ``config.data``, so ``resolve_data_path`` is
unreachable from here and no ``kanibako.cfg`` on this box is consulted either. The
observable a leak probe would need — something the window's redirection can move —
therefore does not exist.  ``throwaway_user_dirs`` stays armed around the call
anyway (it costs one ``mkdtemp``, and it is what keeps that true if discovery ever
grows a path-dependent input again); what the two tests below pin is that the window
moves exactly the shared variable list and restores it, on the exception path too.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from tests._user_dirs import HOME_VAR, user_dir_env_vars, user_dir_tree

#: One small, fast, already-green file for the child to collect and run. The child's
#: own work is incidental — the assertion is about what its CONFIGURE did.
CHILD_TEST = "tests/test_atomic.py"

REPO_ROOT = Path(__file__).resolve().parent.parent

#: The child environment's root. Every redirected variable lives under it.
HOST_DIRNAME = "host"

#: A probe the CHILD loads, so the child reports its own primed memo back to this
#: process. It is written into ``tmp_path`` rather than shipped: it is a fixture for
#: one assertion, and a repo module for it would be a file outliving its reason.
MEMO_PROBE_MODULE = "census_memo_probe"
MEMO_PROBE_ENV = "KANI_CENSUS_MEMO_PROBE_OUT"


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


def _child_ran(proc: subprocess.CompletedProcess[str]) -> None:
  """A child that could not run proves nothing — and its silence reads as a pass."""
  assert proc.returncode == 0, (
    f"the child pytest run failed, so the probe below would be vacuous "
    f"(rc={proc.returncode})\n--- child stdout ---\n{proc.stdout}\n"
    f"--- child stderr ---\n{proc.stderr}"
  )


def test_census_still_primes_the_memo_at_configure_time(tmp_path):
  """⚑⚑ THAT THE PRIMING HAPPENS AT ALL, which is the only child-probe claim here.

  Priming is eager ON PURPOSE: ``_discover``'s process memo is what every later test
  reads, and it must be filled before any test patches discovery. Deleting the call or
  making it lazy would not red anything — the concession rule treats an unprimed memo as
  "no agent's vocabulary is known here", so every unknown key is allowed and the run
  stays green — while quietly handing the whole session an unprimed keyspace. So the
  child reports what its OWN ``_PLUGINS`` holds.

  ⚑ The probe is a CHILD-side plugin that reports ``_PLUGINS`` at
  ``pytest_collection_finish``: after every ``pytest_configure`` has run, and without
  this process poking at the child's internals.
  """
  env = _child_env(tmp_path / HOST_DIRNAME)

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
  """The window moves exactly the shared list of ``tests._user_dirs``, ``HOME`` included."""
  from tests._user_dirs import throwaway_user_dirs

  env: dict[str, str] = {name: f"/host/{name}" for name in user_dir_env_vars()}
  before = dict(env)

  with throwaway_user_dirs(env) as root:
    assert set(env) == set(before), "the window added or dropped a variable"
    for var in user_dir_env_vars():
      assert Path(env[var]) == user_dir_tree(root)[var]

  assert env == before
