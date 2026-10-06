"""Keyspec §2a, through the real CLI: a DECLARED entry holding a value its key refuses, in a
file the command reads, refuses ``set`` unless ``--force`` — the same out-of-chain arm an
undeclared entry takes. Setting the bad key itself is the repair, and is never blocked."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_SRC = Path(__file__).resolve().parents[2] / "src"


@pytest.fixture
def cli(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith(("XDG_", "KANIBAKO"))}
    env.update(HOME=str(home), XDG_RUNTIME_DIR=str(tmp_path), PYTHONPATH=str(REPO_SRC))

    def run(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "kanibako", *args], env=env, cwd=home,
            capture_output=True, text=True, timeout=300, check=False,
        )

    run.home = home  # type: ignore[attr-defined]
    assert run("system", "get", "system.agent").returncode == 0  # bootstrap the store
    return run


def _system_file(cli) -> Path:
    path = cli.home / ".local/share/kanibako/global/settings.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _assert_refused(proc, path: Path, before: bytes, entry: str) -> None:
    assert proc.returncode == 1, proc.stderr
    assert str(path) in proc.stderr and entry in proc.stderr
    assert "--force" in proc.stderr
    assert path.read_bytes() == before


class TestSystemDoor:
    def test_a_list_at_a_path_key_refuses_and_writes_nothing(self, cli):
        path = _system_file(cli)
        path.write_text("system:\n  agent: shell\n  cache: [x]\n")
        before = path.read_bytes()
        _assert_refused(cli("system", "set", "system.agent=claude"), path, before,
                        "system.cache = ['x']")

    @pytest.mark.parametrize(
        "value,entry,shape", [(8080, "system.cache = 8080", "an integer"),
                              ("true", "system.cache = true", "a boolean"),
                              ("1.5", "system.cache = 1.5", "a number")],
    )
    def test_a_scalar_at_a_path_key_refuses_and_writes_nothing(self, cli, value, entry, shape):
        """An INT, BOOL and FLOAT at a PATH key refuse on the same arm a list does.

        A PATH key is typed ``path``, so keyspec §2a's *"a type mismatch for a typed
        scalar key"* reaches every non-string — not only the two non-scalars. ⭐ THE SHAPE
        IS THE FILE'S OWN WORD, so ``8080`` is answered ``an integer`` and not ``int``.

        Mutation: point ``config_interface._path_value_reason`` back at a list/map test of
        its own → every row BUILDS the set: rc 0 and the file is rewritten.
        """
        path = _system_file(cli)
        path.write_text(f"system:\n  agent: shell\n  cache: {value}\n")
        before = path.read_bytes()
        proc = cli("system", "set", "system.agent=claude")
        _assert_refused(proc, path, before, entry)
        assert shape in proc.stderr, proc.stderr

    def test_an_int_at_a_layer2_path_key_refuses_and_writes_nothing(self, cli):
        """The board's own case: ``system.canon: 8080`` refuses a set that edits ANOTHER key.

        ⭐ OUT-OF-CHAIN, which is the arm that matters: keyspec §2a says a bad entry
        outside the edited value's upstream chain is *"ERROR by default — name it, write
        nothing"* — so an unrelated ``set`` is refused by an entry it never touched.

        Mutation: drop the ``_path_value_reason`` repoint onto the §2a carrier → ``rc`` is 0
        and ``system.canon = 8080`` is rewritten as a string.
        """
        path = _system_file(cli)
        path.write_text("system:\n  canon: 8080\n")
        before = path.read_bytes()
        proc = cli("system", "set", "system.agent=claude")
        _assert_refused(proc, path, before, "system.canon = 8080")
        assert "an integer" in proc.stderr, proc.stderr

    def test_force_warns_and_writes(self, cli):
        path = _system_file(cli)
        path.write_text("system:\n  agent: shell\n  cache: [x]\n")
        proc = cli("system", "set", "--force", "system.agent=claude")
        assert proc.returncode == 0, proc.stderr
        assert "Warning:" in proc.stderr and "system.cache = ['x']" in proc.stderr
        assert "agent: claude" in path.read_text()

    def test_setting_the_bad_key_itself_is_the_repair(self, cli, tmp_path):
        path = _system_file(cli)
        path.write_text("system:\n  agent: shell\n  cache: [x]\n")
        proc = cli("system", "set", f"system.cache={tmp_path / 'c'}")
        assert proc.returncode == 0, proc.stderr
        assert "system.cache" not in proc.stderr

    def test_a_null_at_a_path_key_the_launch_refuses(self, cli):
        path = _system_file(cli)
        path.write_text("system:\n  agent: shell\n  backup: null\n")
        before = path.read_bytes()
        _assert_refused(cli("system", "set", "system.agent=claude"), path, before,
                        "system.backup = null")

    def test_a_clean_file_sets_silently(self, cli):
        path = _system_file(cli)
        path.write_text("system:\n  agent: shell\n")
        proc = cli("system", "set", "system.agent=claude")
        assert proc.returncode == 0, proc.stderr
        assert "refuse" not in proc.stderr
        assert "agent: claude" in path.read_text()


class TestWorksetDoor:
    @pytest.fixture
    def ws_file(self, cli):
        assert cli("workset", "create", "ws1").returncode == 0
        path = cli.home / "ws1" / "workset.yaml"
        path.write_text("workset:\n  vault_rw: /tmp/vrw\n  auth:\n    path: [a]\n")
        return path

    def test_refused_and_nothing_written(self, cli, ws_file):
        before = ws_file.read_bytes()
        _assert_refused(cli("workset", "set", "ws1", "workset.vault_rw=/tmp/vrw2"), ws_file,
                        before, "workset.auth.path = ['a']")

    def test_force_warns_and_writes(self, cli, ws_file):
        proc = cli("workset", "set", "--force", "ws1", "workset.vault_rw=/tmp/vrw2")
        assert proc.returncode == 0, proc.stderr
        assert "Warning:" in proc.stderr and "workset.auth.path" in proc.stderr
        assert "/tmp/vrw2" in ws_file.read_text()

    def test_setting_the_bad_key_itself_is_the_repair(self, cli, ws_file, tmp_path):
        proc = cli("workset", "set", "ws1", f"workset.auth.path={tmp_path / 'a'}")
        assert proc.returncode == 0, proc.stderr


class TestBoxDoor:
    @pytest.fixture
    def box_file(self, cli):
        proj = cli.home / "proj"
        proj.mkdir()
        assert cli("box", "create", "--name", "b1", str(proj)).returncode == 0
        return cli.home / ".local/share/kanibako/primary_workset/boxes/b1/box.yaml"

    @pytest.mark.parametrize("line, entry", [
        ("share_images: maybe", "box.share_images = maybe"),
        ("image: null", "box.image = null"),
        ("env:\n    FOO: [a]", "box.env.FOO = ['a']"),
    ])
    def test_refused_and_nothing_written(self, cli, box_file, line, entry):
        box_file.write_text(f"box:\n  shell: zsh\n  {line}\n")
        before = box_file.read_bytes()
        _assert_refused(cli("box", "set", "b1", "box.shell=bash"), box_file, before, entry)

    def test_force_warns_and_writes(self, cli, box_file):
        box_file.write_text("box:\n  shell: zsh\n  share_images: maybe\n")
        proc = cli("box", "set", "--force", "b1", "box.shell=bash")
        assert proc.returncode == 0, proc.stderr
        assert "Warning:" in proc.stderr and "box.share_images" in proc.stderr
        assert "shell: bash" in box_file.read_text()

    def test_well_typed_values_set_silently(self, cli, box_file):
        box_file.write_text("box:\n  shell: zsh\n  share_images: true\n  env:\n    PORT: 8080\n")
        proc = cli("box", "set", "b1", "box.shell=bash")
        assert proc.returncode == 0, proc.stderr
        assert "refuse" not in proc.stderr


def test_a_standalone_boxs_null_registry_marker_is_not_a_bad_entry(cli):
    proj = cli.home / "sa"
    proj.mkdir()
    assert cli("box", "create", "--standalone", str(proj)).returncode == 0
    assert "registry: null" in (proj / "workset.yaml").read_text()
    proc = cli("box", "set", str(proj), "box.shell=bash")
    assert proc.returncode == 0, proc.stderr


def test_a_null_workset_registry_in_the_system_file_is_refused(cli):
    path = _system_file(cli)
    path.write_text("system:\n  agent: shell\nworkset:\n  registry: null\n")
    before = path.read_bytes()
    _assert_refused(cli("system", "set", "system.agent=claude"), path, before,
                    "workset.registry = null")


def test_force_does_not_set_a_value_whose_chain_reaches_an_ill_typed_entry(cli):
    path = _system_file(cli)
    path.write_text("system:\n  agent: shell\n  cache: [x]\n")
    before = path.read_bytes()
    proc = cli("system", "set", "--force", "system.backup={system.cache}/b")
    assert proc.returncode == 1, proc.stderr
    assert "upstream chain reaches system.cache" in proc.stderr
    assert path.read_bytes() == before


class TestAgentDoor:
    @pytest.fixture
    def agent_file(self, cli):
        path = cli.home / ".local/share/kanibako/agents/shell/agent.yaml"
        path.write_text("self:\n  env:\n    FOO: [a]\n")
        return path

    def test_refused_and_nothing_written(self, cli, agent_file):
        before = agent_file.read_bytes()
        _assert_refused(cli("agent", "set", "shell", "model=x"), agent_file, before,
                        "agent.shell.env.FOO = ['a']")

    def test_force_warns_and_writes(self, cli, agent_file):
        proc = cli("agent", "set", "--force", "shell", "model=x")
        assert proc.returncode == 0, proc.stderr
        assert "Warning:" in proc.stderr and "agent.shell.env.FOO" in proc.stderr
        assert "model: x" in agent_file.read_text()

    def test_setting_the_bad_key_itself_is_the_repair(self, cli, agent_file):
        proc = cli("agent", "set", "shell", "env.FOO=b")
        assert proc.returncode == 0, proc.stderr
        assert "FOO: b" in agent_file.read_text()


@pytest.mark.parametrize("body", [
    "self:\n  model: y\nagent:\n  shell:\n    env:\n      FOO: [a]\n",
    "agent:\n  shell:\n    env:\n      FOO: [a]\n",
    "self:\n  model: y\nagent:\n  Shell:\n    env:\n      FOO: [a]\n",
])
def test_the_agent_door_reads_the_files_own_agent_table_too(cli, body):
    path = cli.home / ".local/share/kanibako/agents/shell/agent.yaml"
    path.write_text(body)
    before = path.read_bytes()
    _assert_refused(cli("agent", "set", "shell", "model=x"), path, before, "env.FOO = ['a']")
