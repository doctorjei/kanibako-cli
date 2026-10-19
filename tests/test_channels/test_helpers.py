"""Tests for helper spawning: numbering, spawn budget, and directory structure."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from kanibako.channels.helpers import (
  DEFAULT_BREADTH,
  DEFAULT_DEPTH,
  SpawnBudget,
  bundled_init_script,
  check_spawn_allowed,
  child_budget,
  create_broadcast_dirs,
  create_helper_dirs,
  existing_helpers,
  helper_tree,
  read_helper_number,
  read_spawn_budget,
  remove_helper_dirs,
  resolve_init_script,
  resolve_spawn_budget,
  write_helper_number,
  write_spawn_budget,
)
from tests.support.link_proof import tree_digest


# --- SpawnBudget ---


class TestSpawnBudget:
  def test_defaults(self):
    b = SpawnBudget()
    assert b.depth == DEFAULT_DEPTH
    assert b.breadth == DEFAULT_BREADTH

  def test_frozen(self):
    b = SpawnBudget()
    with pytest.raises(AttributeError):
      b.depth = 10  # type: ignore[misc]


# --- check_spawn_allowed ---


class TestCheckSpawnAllowed:
  def test_allowed(self):
    assert check_spawn_allowed(SpawnBudget(depth=2, breadth=4), 0) is None

  def test_depth_zero_refused(self):
    result = check_spawn_allowed(SpawnBudget(depth=0, breadth=4), 0)
    assert result is not None
    assert "depth" in result

  def test_breadth_exhausted(self):
    result = check_spawn_allowed(SpawnBudget(depth=2, breadth=3), 3)
    assert result is not None
    assert "breadth" in result

  def test_breadth_not_yet_exhausted(self):
    assert check_spawn_allowed(SpawnBudget(depth=2, breadth=3), 2) is None

  def test_unlimited_depth(self):
    assert check_spawn_allowed(SpawnBudget(depth=-1, breadth=4), 0) is None

  def test_unlimited_breadth(self):
    assert check_spawn_allowed(SpawnBudget(depth=2, breadth=-1), 999) is None


# --- child_budget ---


class TestChildBudget:
  def test_decrements_depth(self):
    parent = SpawnBudget(depth=3, breadth=4)
    child = child_budget(parent)
    assert child.depth == 2
    assert child.breadth == 4

  def test_depth_one_to_zero(self):
    child = child_budget(SpawnBudget(depth=1, breadth=2))
    assert child.depth == 0

  def test_unlimited_depth_stays_unlimited(self):
    child = child_budget(SpawnBudget(depth=-1, breadth=3))
    assert child.depth == -1

  def test_breadth_inherited(self):
    child = child_budget(SpawnBudget(depth=4, breadth=7))
    assert child.breadth == 7


# --- resolve_spawn_budget ---


class TestResolveSpawnBudget:
  def test_handed_down_wins(self):
    """A limited box may not raise the limit its parent handed it."""
    handed_down = SpawnBudget(depth=1, breadth=1)
    own = SpawnBudget(depth=4, breadth=4)
    result = resolve_spawn_budget(handed_down, own, cli_depth=10, cli_breadth=10)
    assert result == handed_down

  def test_own_settings_without_a_handed_down_budget(self):
    own = SpawnBudget(depth=3, breadth=5)
    result = resolve_spawn_budget(None, own, cli_depth=10, cli_breadth=10)
    assert result == own

  def test_cli_flags_without_config(self):
    result = resolve_spawn_budget(None, None, cli_depth=2, cli_breadth=6)
    assert result == SpawnBudget(depth=2, breadth=6)

  def test_partial_cli_flags(self):
    result = resolve_spawn_budget(None, None, cli_depth=2, cli_breadth=None)
    assert result.depth == 2
    assert result.breadth == DEFAULT_BREADTH

  def test_defaults_when_nothing_set(self):
    result = resolve_spawn_budget(None, None, None, None)
    assert result == SpawnBudget()


# --- Spawn budget I/O — the declared ``system.helpers.*`` keys (spec §2g) ---


class TestSpawnBudgetIO:
  def test_write_and_read(self, tmp_path):
    path = tmp_path / "spawn.yaml"
    budget = SpawnBudget(depth=3, breadth=5)
    write_spawn_budget(path, budget)
    result = read_spawn_budget(path)
    assert result == budget

  def test_the_written_document_is_the_declared_keyspace(self, tmp_path):
    """⚑ THE ANTI-REGRESSION CASE: a bespoke ``spawn:`` table is what was wrong.

    The stored shape must be the ``system: helpers:`` table the keyspace declares,
    because the SAME reader is pointed at a box's own ``{config.settings}``. A
    round-trip alone would stay green through any private shape.
    """
    from kanibako.settings.config_io import load_doc

    path = tmp_path / "spawn.yaml"
    write_spawn_budget(path, SpawnBudget(depth=3, breadth=5))
    data = load_doc(path)
    assert data == {"system": {"helpers": {"depth": 3, "breadth": 5}}}
    assert "spawn" not in data

  def test_read_missing_file(self, tmp_path):
    assert read_spawn_budget(tmp_path / "nope.yaml") is None

  def test_read_no_helpers_table(self, tmp_path):
    path = tmp_path / "empty.yaml"
    path.write_text("other:\n  foo: 1\n")
    assert read_spawn_budget(path) is None

  def test_a_retired_spawn_table_is_not_a_budget(self, tmp_path):
    """The pre-declaration spelling is not a key, so it carries no budget."""
    path = tmp_path / "legacy.yaml"
    path.write_text("spawn:\n  depth: 9\n  breadth: 9\n")
    assert read_spawn_budget(path) is None

  def test_one_leaf_is_a_partial_override(self, tmp_path):
    path = tmp_path / "partial.yaml"
    path.write_text("system:\n  helpers:\n    depth: 2\n")
    result = read_spawn_budget(path)
    assert result == SpawnBudget(depth=2, breadth=DEFAULT_BREADTH)

  def test_a_non_numeric_leaf_is_refused_by_name(self, tmp_path):
    """Never a fabricated default: a budget the user did not write is not a budget."""
    from kanibako.errors import ConfigError

    path = tmp_path / "bad.yaml"
    path.write_text("system:\n  helpers:\n    depth: lots\n")
    with pytest.raises(ConfigError, match="system.helpers.depth"):
      read_spawn_budget(path)

  def test_a_write_replaces_the_document_whole(self, tmp_path):
    """A delivery document, not a merge target: a planted ``meta.box.helper_num``
    (UNSET for a top-level box) and any other content are gone after the write."""
    from kanibako.settings.config_io import load_doc

    path = tmp_path / "spawn.yaml"
    path.write_text("other:\n  foo: 1\nsystem:\n  agent: claude\n")
    write_helper_number(path, 5)
    write_spawn_budget(path, SpawnBudget(depth=2, breadth=3))
    assert load_doc(path) == {"system": {"helpers": {"depth": 2, "breadth": 3}}}
    assert read_helper_number(path) is None

  def test_unlimited_values(self, tmp_path):
    path = tmp_path / "unlimited.yaml"
    budget = SpawnBudget(depth=-1, breadth=-1)
    write_spawn_budget(path, budget)
    result = read_spawn_budget(path)
    assert result == budget

  def test_creates_parent_dirs(self, tmp_path):
    path = tmp_path / "sub" / "dir" / "spawn.yaml"
    write_spawn_budget(path, SpawnBudget())
    assert path.exists()


class TestHelperNumberIO:
  """keyspec ``meta.box.helper_num`` in a helper's spawn document."""

  def test_round_trip_keeps_the_budget(self, tmp_path):
    path = tmp_path / "spawn.yaml"
    write_spawn_budget(path, SpawnBudget(depth=2, breadth=3))
    write_helper_number(path, 7)
    assert read_helper_number(path) == 7
    assert read_spawn_budget(path) == SpawnBudget(depth=2, breadth=3)

  def test_a_rewrite_replaces_the_number(self, tmp_path):
    path = tmp_path / "spawn.yaml"
    write_helper_number(path, 9)
    write_helper_number(path, 1)
    assert read_helper_number(path) == 1

  def test_the_directors_document_carries_none(self, tmp_path):
    path = tmp_path / "spawn.yaml"
    assert read_helper_number(path) is None
    write_spawn_budget(path, SpawnBudget())
    assert read_helper_number(path) is None

  def test_an_unreadable_document_is_a_clean_refusal(self, tmp_path):
    from kanibako.errors import ConfigError

    locked = tmp_path / "locked"
    write_helper_number(locked / "spawn.yaml", 1)
    locked.chmod(0)
    try:
      with pytest.raises(ConfigError) as info:
        read_helper_number(locked / "spawn.yaml")
    finally:
      locked.chmod(0o700)
    assert str(info.value) == (
      f"{locked / 'spawn.yaml'} could not be read (Permission denied). Make it and "
      "its parent directories readable, then retry."
    )

  def test_a_looped_document_gets_the_loop_cure_not_the_permission_one(self, tmp_path):
    from kanibako.errors import ConfigError
    from kanibako.settings.messages import CANON_SYMLINK_LOOP_WHY

    path = tmp_path / "spawn.yaml"
    path.symlink_to(path)
    with pytest.raises(ConfigError) as info:
      read_helper_number(path)
    assert str(info.value) == (
      f"{path}: {CANON_SYMLINK_LOOP_WHY}. Remove or repair the link, then retry."
    )


# --- Directory structure ---


class TestCreateHelperDirs:
  def test_creates_standard_layout(self, tmp_path):
    helpers = tmp_path / "helpers"
    helpers.mkdir()
    root = create_helper_dirs(helpers, 1)
    assert root == helpers / "1"
    assert (root / "vault" / "ro").is_dir()
    assert (root / "vault" / "rw").is_dir()
    assert (root / "workspace").is_dir()
    assert (root / "scripts").is_dir()
    # ⚑ The vestigial ``playbook/`` wrapper level is GONE, not renamed: a helper
    # home is not a box, so it gets a flat ``scripts/`` and no canon tree.
    assert not (root / "playbook").exists()
    # No peer-link dir: helpers talk only through the hub (``box helper send``)
    assert not (root / "peers").exists()

  def test_idempotent(self, tmp_path):
    helpers = tmp_path / "helpers"
    helpers.mkdir()
    create_helper_dirs(helpers, 1)
    # Place a file to check it isn't removed
    (helpers / "1" / "workspace" / "test.txt").write_text("hello")
    create_helper_dirs(helpers, 1)
    assert (helpers / "1" / "workspace" / "test.txt").read_text() == "hello"

  def test_seeds_the_shell_files(self, tmp_path):
    """A new helper home gets kanibako's ``.profile`` and ``.bashrc`` (its login
    PATH otherwise runs the image's baked ``kanibako``)."""
    from kanibako.settings.bootstrap import BASHRC_FILE, PROFILE_FILE
    from kanibako.settings.messages import BASHRC_CONTENTS, PROFILE_CONTENTS

    root = create_helper_dirs(tmp_path / "helpers", 1)
    assert (root / PROFILE_FILE).read_text() == PROFILE_CONTENTS
    assert (root / BASHRC_FILE).read_text() == BASHRC_CONTENTS

  def test_never_reseeds_an_existing_shell_file(self, tmp_path):
    from kanibako.settings.bootstrap import PROFILE_FILE

    root = tmp_path / "helpers" / "1"
    root.mkdir(parents=True)
    (root / PROFILE_FILE).write_text("mine\n")
    create_helper_dirs(tmp_path / "helpers", 1)
    assert (root / PROFILE_FILE).read_text() == "mine\n"

  def test_creates_helpers_dir_parents(self, tmp_path):
    helpers = tmp_path / "deep" / "helpers"
    root = create_helper_dirs(helpers, 0)
    assert root.is_dir()


class TestCreateBroadcastDirs:
  def test_creates_all_rw_ro(self, tmp_path):
    helpers = tmp_path / "helpers"
    helpers.mkdir()
    all_dir = create_broadcast_dirs(helpers)
    assert all_dir == helpers / "all"
    assert (all_dir / "rw").is_dir()
    assert (all_dir / "ro").is_dir()

  def test_idempotent(self, tmp_path):
    helpers = tmp_path / "helpers"
    helpers.mkdir()
    create_broadcast_dirs(helpers)
    (helpers / "all" / "rw" / "test.txt").write_text("data")
    create_broadcast_dirs(helpers)
    assert (helpers / "all" / "rw" / "test.txt").read_text() == "data"


class TestRemoveHelperDirs:
  def test_removes_helper_root(self, tmp_path):
    helpers = tmp_path / "helpers"
    helpers.mkdir()
    create_helper_dirs(helpers, 1)
    remove_helper_dirs(helpers, 1)
    assert not (helpers / "1").exists()

  def test_preserves_other_siblings(self, tmp_path):
    helpers = tmp_path / "helpers"
    helpers.mkdir()
    create_helper_dirs(helpers, 1)
    create_helper_dirs(helpers, 2)
    create_broadcast_dirs(helpers)

    remove_helper_dirs(helpers, 2)

    assert not (helpers / "2").exists()
    assert (helpers / "1" / "workspace").is_dir()
    assert (helpers / "all" / "rw").is_dir()

  def test_nonexistent_helper(self, tmp_path):
    """No crash when removing a helper that doesn't exist."""
    helpers = tmp_path / "helpers"
    helpers.mkdir()
    remove_helper_dirs(helpers, 99)


class TestHelperTree:
  @staticmethod
  def _recursive(helpers_dir: Path, root: Path) -> list[tuple[Path, int]]:
    """The recursive walk ``helper_tree`` replaced: its order is the contract."""
    found: list[tuple[Path, int]] = []
    for num in existing_helpers(helpers_dir, root):
      found.extend(TestHelperTree._recursive(helpers_dir / str(num) / "helpers", root))
      found.append((helpers_dir, num))
    return found

  def test_deepest_first_in_numeric_order(self, tmp_path: Path) -> None:
    h = tmp_path / "helpers"
    for rel in ("1/helpers/3/helpers/2", "1/helpers/4", "2", "10/helpers/7", "all/rw"):
      (h / rel).mkdir(parents=True)
    (h / "1" / "helpers" / "notes").touch()
    one = h / "1" / "helpers"
    expected = [
      (one / "3" / "helpers", 2),
      (one, 3),
      (one, 4),
      (h, 1),
      (h, 2),
      (h / "10" / "helpers", 7),
      (h, 10),
    ]
    assert helper_tree(h, tmp_path) == expected
    assert helper_tree(h, tmp_path) == self._recursive(h, tmp_path)

  def test_empty_and_missing(self, tmp_path: Path) -> None:
    assert helper_tree(tmp_path / "helpers", tmp_path) == []
    (tmp_path / "helpers").mkdir()
    assert helper_tree(tmp_path / "helpers", tmp_path) == []

  def test_a_tree_deeper_than_the_recursion_limit(self, tmp_path: Path) -> None:
    """A chain of nested helpers deeper than the interpreter's recursion limit is
    walked whole.  The levels are faked, because a real tree that deep is slow to
    read and to clean up."""
    depth = sys.getrecursionlimit() + 100
    h = tmp_path / "helpers"

    def one_helper_per_level(helpers_dir: Path, root: Path) -> list[int]:
      return [1] if len(helpers_dir.relative_to(h).parts) < 2 * depth else []

    with patch("kanibako.channels.helpers.existing_helpers", one_helper_per_level):
      found = helper_tree(h, tmp_path)
    assert len(found) == depth
    assert found[-1] == (h, 1)
    assert len(found[0][0].relative_to(h).parts) == 2 * (depth - 1)


# --- helper-init.sh template ---


class TestBundledInitScript:
  def test_bundled_exists(self):
    path = bundled_init_script()
    assert path.is_file()

  def test_bundled_is_bash(self):
    path = bundled_init_script()
    content = path.read_text()
    assert content.startswith("#!/usr/bin/env bash")

  def test_bundled_contains_shebang(self):
    path = bundled_init_script()
    content = path.read_text()
    assert "set -euo pipefail" in content

  def test_bundled_socket_path_matches_mount_dest(self):
    # The hub socket is mounted under the FIXED pinned root, so the script must
    # check exactly that path for `kanibako box helper register` to run and the
    # helper to join the hub. An ${XDG_STATE_HOME:-...} form here would re-open
    # the host-vs-box guess the pin deleted, so its ABSENCE is asserted too --
    # the script runs at ENTRYPOINT, before the post-boot XDG projection is
    # guaranteed to have happened, so it must not depend on it.
    path = bundled_init_script()
    content = path.read_text()
    assert 'SOCKET_PATH="$HOME/.kanibako/state/hub/helper.sock"' in content
    # No EXECUTABLE line may RESOLVE anything from XDG (the comment explaining
    # why is exempt: it is the prose, not the resolution).
    #
    # ⚑ This was a flat "XDG_STATE_HOME appears on no executable line at all",
    # which was a sound PROXY only while the script had no reason to mention the
    # variable.  The script now also RUNS the projection, whose whole job is to
    # read $XDG_STATE_HOME and point it AT the pinned dir -- the opposite of
    # deriving the socket from it.  So the proxy is replaced by the thing it was
    # standing in for: the projection's own lines are the ONLY ones allowed to
    # name the variable, and they are verbatim-pinned to the generator by
    # TestXdgProjectionSh.test_helper_init_carries_the_snippet_verbatim.  A
    # ${XDG_STATE_HOME:-...} creeping into SOCKET_PATH still fails here.
    from kanibako.box_supervisor import xdg_projection_sh

    code = [ln for ln in content.splitlines() if not ln.lstrip().startswith("#")]
    xdg_lines = [ln for ln in code if "XDG_STATE_HOME" in ln]
    projection = xdg_projection_sh().splitlines()
    assert xdg_lines, "the projection block went missing from helper-init.sh"
    assert [ln for ln in xdg_lines if ln not in projection] == []

  def test_pinned_socket_dest_agrees_across_every_spelling(self):
    """The declared MOUNT dest, the box shell, the in-box CLI and PID-1 all
    spell one pinned path.

    This is what the retired ``box_state_home`` derivation held together by
    hand across four resolvers -- and it had already drifted (the dest honored
    ``$XDG_STATE_HOME`` while ``start.py``'s matching mkdir was hardcoded at
    ``.local/state``). With the dest pinned there is nothing left to derive,
    but the spellings still have to AGREE, so the agreement is asserted
    directly instead of being inferred from a shared function. Two of them
    CANNOT import the constant -- bash, and the import-light PID-1 -- which is
    exactly why this test exists rather than a shared import.
    """
    from kanibako.box_supervisor import PINNED_ROOT_RELPATH
    from kanibako.channels.helpers import SPAWN_CONFIG_FILENAME
    from kanibako.commands.helper_cmd import _log_path, _socket_path
    from kanibako.settings import core_defaults
    from kanibako.settings.settings_resolve import (
      BOX_PINNED_ROOT_RELPATH,
      BOX_PINNED_STATE_RELPATH,
      GUEST_HOME,
    )

    # 1. PID-1's quarantined duplicate == the single source of truth.
    assert PINNED_ROOT_RELPATH == BOX_PINNED_ROOT_RELPATH == ".kanibako"
    assert BOX_PINNED_STATE_RELPATH == ".kanibako/state"

    # 2. The DECLARED mount dests (what a launch actually mounts).
    declared = {e["key"]: e["box_dest"] for e in core_defaults._load_doc()["helpers"]}
    assert declared == {
      "helper_sock": f"~/{BOX_PINNED_STATE_RELPATH}/hub",
      "helper_log": f"~/{BOX_PINNED_STATE_RELPATH}/helpers.jsonl",
      # The spawn-budget bind: the in-box reader is ``Path.home() /
      # SPAWN_CONFIG_FILENAME`` (``helper_cmd.run_spawn``), so dest and reader agree.
      "helper_budget": f"~/{SPAWN_CONFIG_FILENAME}",
    }

    # 3. The box-side SHELL literal, resolved with the box's HOME.
    shell_line = 'SOCKET_PATH="$HOME/.kanibako/state/hub/helper.sock"'
    assert shell_line in bundled_init_script().read_text()
    shell_dest = shell_line.split('"')[1].replace("$HOME", GUEST_HOME)

    # 4. The in-box CLI readers, with HOME being the box's.
    with patch.object(Path, "home", staticmethod(lambda: Path(GUEST_HOME))):
      cli_sock, cli_log = str(_socket_path()), str(_log_path())

    mount_sock = (
      declared["helper_sock"].replace("~", GUEST_HOME, 1) + "/" + core_defaults.HELPER_SOCKET_NAME
    )
    mount_log = declared["helper_log"].replace("~", GUEST_HOME, 1)
    assert shell_dest == cli_sock == mount_sock
    assert cli_log == mount_log

  def test_it_registers_through_the_real_cli_spelling_and_shows_a_refusal(self):
    """The script registers via ``kanibako box helper register <N>`` (the spelling
    the real parser routes to ``run_register``), in the background so the agent
    still starts, and keeps the command's stderr: a refused registration is
    visible in the helper's output, not swallowed, and not fatal."""
    import shutil
    import socket
    import subprocess
    import tempfile

    from kanibako.cli import build_parser
    from kanibako.commands.helper_cmd import run_register

    home = Path(tempfile.mkdtemp(prefix="kbi-", dir="/tmp"))
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
      (home / ".kanibako" / "state" / "hub").mkdir(parents=True)
      listener.bind(str(home / ".kanibako" / "state" / "hub" / "helper.sock"))
      listener.listen(1)
      fakebin = home / "bin"
      fakebin.mkdir()
      (fakebin / "kanibako").write_text(
        "#!/bin/sh\n"
        'printf \'%s\\n\' "$@" > "$HOME/argv.tmp"\n'
        'mv "$HOME/argv.tmp" "$HOME/argv"\n'
        "echo 'kanibako: fake refusal' >&2\n"
        "exit 2\n"
      )
      (fakebin / "kanibako").chmod(0o755)
      wait_for_record = (
        'i=0; while [ ! -e "$HOME/argv" ] && [ $i -lt 100 ]; do '
        "sleep 0.1; i=$((i+1)); done; sleep 0.3"
      )
      proc = subprocess.run(
        ["bash", str(bundled_init_script()), "7", "sh", "-c", wait_for_record],
        env={"HOME": str(home), "PATH": f"{fakebin}:/usr/bin:/bin"},
        capture_output=True,
        text=True,
        timeout=60,
      )
      assert proc.returncode == 0, proc.stderr
      argv = (home / "argv").read_text().splitlines()
    finally:
      listener.close()
      shutil.rmtree(home, ignore_errors=True)
    assert argv == ["box", "helper", "register", "7"]
    args = build_parser().parse_args(argv)
    assert (args.func, args.number) == (run_register, 7)
    assert "kanibako: fake refusal" in proc.stderr
    assert "Helper 7 initialized." in proc.stderr


class TestResolveInitScript:
  def test_custom_takes_priority(self, tmp_path):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    custom = scripts / "helper-init.sh"
    custom.write_text("#!/bin/bash\necho custom\n")
    result = resolve_init_script(scripts)
    assert result == custom

  def test_falls_back_to_bundled(self, tmp_path):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    # No helper-init.sh in scripts dir
    result = resolve_init_script(scripts)
    assert result == bundled_init_script()

  def test_none_scripts_dir(self):
    result = resolve_init_script(None)
    assert result == bundled_init_script()


# ---------------------------------------------------------------------------
# No-follow (174 lane 3): the box-home ``helpers/`` tree
# ---------------------------------------------------------------------------


def _helpers_with_link(tmp_path: Path, how: str, where: str) -> tuple[Path, Path, Path]:
  """``home/helpers`` whose helper 1 is reached through a planted link.

  *how* ``leaf`` links ``helpers/1/state.json``; ``parent`` links ``helpers/1``.
  Returns (helpers_dir, the tree the link leads to, the link).
  """
  home = tmp_path / "home"
  helpers = home / "helpers"
  helpers.mkdir(parents=True)
  base = tmp_path / "outside" if where == "outside" else home / "elsewhere"
  base.mkdir(parents=True)
  (base / "state.json").write_text('{"status": "running", "owner": "host"}\n')
  if how == "leaf":
    (helpers / "1").mkdir()
    link = helpers / "1" / "state.json"
    link.symlink_to(base / "state.json")
  else:
    link = helpers / "1"
    link.symlink_to(base)
  return helpers, base, link


_HELPER_LINKS = [(h, w) for h in ("leaf", "parent") for w in ("outside", "inside")]


class TestHelpersTreeNoFollow:
  @pytest.mark.parametrize("how,where", _HELPER_LINKS)
  def test_state_read_and_write_refuse_a_link(
    self,
    tmp_path: Path,
    how: str,
    where: str,
  ) -> None:
    from kanibako.boxfs import SymlinkRefusedError
    from kanibako.channels.helpers import read_helper_state, write_helper_state

    helpers, dest, link = _helpers_with_link(tmp_path, how, where)
    before = tree_digest(dest)

    with pytest.raises(SymlinkRefusedError):
      read_helper_state(helpers, 1)
    with pytest.raises(SymlinkRefusedError):
      write_helper_state(helpers, 1, {"status": "stopped"})

    assert tree_digest(dest) == before
    assert link.is_symlink()

  @pytest.mark.parametrize("how,where", _HELPER_LINKS)
  def test_hub_mark_stopped_skips_a_link_with_a_warning(
    self,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    how: str,
    where: str,
  ) -> None:
    import logging

    from kanibako.channels.helpers import mark_helper_stopped

    helpers, dest, link = _helpers_with_link(tmp_path, how, where)
    before = tree_digest(dest)

    with caplog.at_level(logging.WARNING, logger="kanibako"):
      mark_helper_stopped(helpers, 1)

    assert tree_digest(dest) == before
    assert any(str(link) in r.getMessage() for r in caplog.records)

  @pytest.mark.parametrize("where", ["outside", "inside"])
  def test_remove_removes_a_linked_helper_dir_as_a_link(
    self,
    tmp_path: Path,
    where: str,
  ) -> None:
    helpers, dest, link = _helpers_with_link(tmp_path, "parent", where)
    before = tree_digest(dest)

    remove_helper_dirs(helpers, 1)

    assert not link.is_symlink() and not link.exists()
    assert tree_digest(dest) == before

  @pytest.mark.parametrize("where", ["outside", "inside"])
  def test_remove_refuses_a_linked_helpers_dir(self, tmp_path: Path, where: str) -> None:
    """``helpers`` itself linked: the home is the root, so the link is refused."""
    from kanibako.boxfs import SymlinkRefusedError

    home = tmp_path / "home"
    home.mkdir()
    dest = tmp_path / "outside" if where == "outside" else home / "elsewhere"
    create_helper_dirs(dest, 1)
    (dest / "1" / "workspace" / "data.txt").write_text("keep")
    (home / "helpers").symlink_to(dest)
    before = tree_digest(dest)

    with pytest.raises(SymlinkRefusedError):
      remove_helper_dirs(home / "helpers", 1)
    with pytest.raises(SymlinkRefusedError):
      create_helper_dirs(home / "helpers", 2)

    assert tree_digest(dest) == before

  def test_control_state_round_trip_and_mark(self, tmp_path: Path) -> None:
    from kanibako.channels.helpers import (
      mark_helper_stopped,
      read_helper_state,
      write_helper_state,
    )

    helpers = tmp_path / "home" / "helpers"
    assert read_helper_state(helpers, 1) == {}
    write_helper_state(helpers, 1, {"status": "running", "n": 1})
    path = helpers / "1" / "state.json"
    assert path.read_text() == '{\n  "status": "running",\n  "n": 1\n}\n'
    assert path.stat().st_mode & 0o777 == 0o644
    mark_helper_stopped(helpers, 1)
    assert read_helper_state(helpers, 1) == {"status": "stopped", "n": 1}
