"""Keyspec §2a, through the real CLI: a stored entry the set-time FULL RESOLUTION marks as
unresolvable (a cycle, a dangling ref, an unknown variable, a chain past the depth cap), in a
file the command reads, is a bad entry. Outside the edited value's chain it refuses ``set``
unless ``--force``, ``get`` warns and reads on, and setting the bad key itself is the repair."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_SRC = Path(__file__).resolve().parents[2] / "src"

_CYCLE = 'system:\n  env:\n    V0: "{system.env.V1}"\n    V1: "{system.env.V0}"\n'


@pytest.fixture
def cli(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith(("XDG_", "KANIBAKO"))}
    env.pop("KB_NOPE_UNSET", None)
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


_DEEP = "system:\n  env:\n" + "".join(
    f'    V{i}: "{{system.env.V{i + 1}}}"\n' for i in range(70)
) + "    V70: /end\n"


@pytest.mark.parametrize("stored, entry, reason", [
    (_CYCLE, "system.env.V0 = {system.env.V1}", "cyclic @-reference"),
    ('system:\n  env:\n    D: "a{system.env.NOPE}"\n', "system.env.D = a{system.env.NOPE}",
     "dangling @-reference '@system.env.NOPE'"),
    ('system:\n  env:\n    U: "{$KB_NOPE_UNSET}"\n', "system.env.U = {$KB_NOPE_UNSET}",
     "Unknown variable: $KB_NOPE_UNSET"),
    (_DEEP, "system.env.V0 = {system.env.V1}", "depth cap (64) exceeded"),
], ids=["cycle", "dangling", "unknown-variable", "depth-cap"])
def test_an_unresolvable_entry_refuses_an_unrelated_set(cli, stored, entry, reason):
    """Mutation: drop the ``defects=`` argument at the set door's scan → rc 0, file rewritten."""
    path = _system_file(cli)
    path.write_text(stored)
    before = path.read_bytes()
    proc = cli("system", "set", "system.agent=claude")
    assert proc.returncode == 1, proc.stderr
    assert str(path) in proc.stderr and "do not resolve" in proc.stderr, proc.stderr
    assert entry in proc.stderr and reason in proc.stderr, proc.stderr
    assert "--force" in proc.stderr
    assert path.read_bytes() == before


def test_force_warns_and_writes(cli):
    path = _system_file(cli)
    path.write_text(_CYCLE)
    proc = cli("system", "set", "--force", "system.agent=claude")
    assert proc.returncode == 0, proc.stderr
    assert "Warning:" in proc.stderr and "system.env.V0 = {system.env.V1}" in proc.stderr
    text = path.read_text()
    assert "agent: claude" in text and "{system.env.V0}" in text  # set never removes it


def test_get_warns_and_reads_on(cli):
    path = _system_file(cli)
    path.write_text(_CYCLE + "  agent: shell\n")
    proc = cli("system", "get", "system.agent")
    assert proc.returncode == 0, proc.stderr
    assert "shell" in proc.stdout
    assert "Warning:" in proc.stderr and "cyclic @-reference" in proc.stderr, proc.stderr


def test_setting_the_bad_key_itself_is_the_repair(cli):
    """Repointing ``V1`` breaks the cycle, so ``V0`` resolves AFTER the edit and is not named."""
    path = _system_file(cli)
    path.write_text(_CYCLE)
    proc = cli("system", "set", "system.env.V1=/plain")
    assert proc.returncode == 0, proc.stderr
    assert "do not resolve" not in proc.stderr, proc.stderr
    assert "/plain" in path.read_text()


def test_a_downward_default_the_cascade_cannot_see_is_not_a_bad_entry(cli):
    """A ``box.*`` default in the system file naming ``{meta.workset.path}`` is cascade
    blindness, forgiven exactly as the edited value's probe forgives it."""
    path = _system_file(cli)
    path.write_text("box:\n  shell: /s/{meta.workset.path}\n")
    proc = cli("system", "set", "system.agent=claude")
    assert proc.returncode == 0, proc.stderr
    assert "do not resolve" not in proc.stderr, proc.stderr
