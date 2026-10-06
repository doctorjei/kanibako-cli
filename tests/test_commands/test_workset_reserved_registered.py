"""A pre-1.8 workset registered as ``primary`` or ``standalone`` is refused at load.

The CLI runs in a subprocess against an isolated HOME in a TemporaryDirectory.
The cure test executes the refusal's own printed lines, in order, so the message
is what is proven, not a paraphrase of it.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

REPO_SRC = Path(__file__).resolve().parents[2] / "src"
NEW_NAME = "renamed"


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, str]]:
    with tempfile.TemporaryDirectory(prefix="kb-reserved-ws-") as tmp:
        base = Path(tmp)
        dirs = {n: base / n for n in ("home", "config", "data", "state", "cache", "runtime")}
        for d in dirs.values():
            d.mkdir()
        child = os.environ.copy()
        child.update({
            "HOME": str(dirs["home"]),
            "XDG_CONFIG_HOME": str(dirs["config"]),
            "XDG_DATA_HOME": str(dirs["data"]),
            "XDG_STATE_HOME": str(dirs["state"]),
            "XDG_CACHE_HOME": str(dirs["cache"]),
            "XDG_RUNTIME_DIR": str(dirs["runtime"]),
            "PYTHONPATH": str(REPO_SRC),
        })
        for key in ("HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME",
                    "XDG_CACHE_HOME", "XDG_RUNTIME_DIR"):
            monkeypatch.setenv(key, child[key])

        from kanibako.settings.config import write_global_config
        from tests.support.filenames import CONFIG_FILENAME

        write_global_config(dirs["config"] / CONFIG_FILENAME)
        yield child


def _cli(env: dict[str, str], *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "kanibako", *args],
        env=env, cwd=cwd, capture_output=True, text=True, timeout=300, check=False,
    )


def _registry() -> Path:
    from kanibako.settings.config import load_config, user_config_file
    from kanibako.settings.paths import load_std_paths

    return load_std_paths(load_config(user_config_file())).registry


def _worksets() -> dict[str, str]:
    from kanibako.project.names import read_names

    return dict(read_names(_registry())["worksets"])


def _make_workset(env: dict[str, str], name: str, root: Path,
                  *, with_in_tree: bool = True) -> tuple[Path, Path | None]:
    """A real workset with a connected box ``ext`` and, optionally, an in-tree box ``intree``."""
    made = _cli(env, "workset", "create", str(root), "--name", name)
    assert made.returncode == 0, made.stderr
    external = root.parent / f"{name}-src"
    external.mkdir()
    in_tree = root / "workspaces" / "intree" if with_in_tree else None
    members: list[tuple[str, Path]] = [("ext", external)]
    if in_tree is not None:
        in_tree.mkdir(parents=True)
        members.append(("intree", in_tree))
    for box, source in members:
        done = _cli(env, "workset", "connect", name, str(source), "--name", box)
        assert done.returncode == 0, done.stderr
    return external, in_tree


def _plant_legacy(env: dict[str, str], legacy: str, root: Path,
                  *, with_in_tree: bool = True) -> tuple[Path, Path | None]:
    """What a 1.7 registry holds: a workset registered under *legacy*."""
    from kanibako.project.names import register_name, unregister_name

    sources = _make_workset(env, "staging", root, with_in_tree=with_in_tree)
    unregister_name(_registry(), "staging", section="worksets")
    register_name(_registry(), legacy, str(root), section="worksets")
    return sources


def _run_cure(env: dict[str, str], stderr: str) -> None:
    """Execute the refusal's indented cure lines, in order, with ``<new name>`` filled in."""
    lines = [ln.strip() for ln in stderr.splitlines() if ln.startswith("  ")]
    assert lines, stderr
    outputs = ""
    for line in lines:
        line = line.replace("<new name>", NEW_NAME)
        if line.startswith("mv "):
            _, old_root, new_root = shlex.split(line)
            shutil.move(old_root, new_root)
            continue
        where = None
        if line.startswith("cd "):
            target, line = line[3:].split(" && ", 1)
            where = Path(target)
        argv = shlex.split(line)
        assert argv[0] == "kanibako", line
        result = _cli(env, *argv[1:], cwd=where)
        outputs += result.stdout + result.stderr
        if where is not None and where.name != "intree":
            assert "not in a specific project workspace" in result.stderr, result
        else:
            assert result.returncode == 0, result.stderr
    assert "Imported workset" in outputs, outputs


@pytest.mark.parametrize(("legacy", "leaf", "with_in_tree"), [
    ("primary", "primary", True),
    ("standalone", "sa-root", True),
    ("primary", "primary", False),
])
def test_reserved_registered_workset_is_refused_then_cured(
    env: dict[str, str], legacy: str, leaf: str, with_in_tree: bool,
) -> None:
    home = Path(env["HOME"])
    root = home / "ws" / leaf
    root.parent.mkdir()
    external, in_tree = _plant_legacy(env, legacy, root, with_in_tree=with_in_tree)

    doors = [external, root] + ([in_tree] if in_tree is not None else [])
    for door in doors:
        refused = _cli(env, "box", "info", cwd=door)
        assert refused.returncode == 1, (door, refused.stdout)
        assert f"Working set '{legacy}' is registered under a reserved name" in refused.stderr
        assert f"kanibako workset rm {legacy} --force" in refused.stderr
        assert "No workset found" not in refused.stderr
        assert "default" not in refused.stderr.split(":", 2)[1]
    assert ("  mv " in refused.stderr) == (leaf == legacy)
    assert ("box remap --force" in refused.stderr) == (leaf == legacy and with_in_tree)

    listed = _cli(env, "workset", "list")
    assert listed.returncode == 0, listed.stderr
    assert legacy in listed.stdout.split()

    _run_cure(env, refused.stderr)

    expected = NEW_NAME if leaf == legacy else leaf
    assert sorted(_worksets()) == [expected]
    resolved: list[tuple[Path, str]] = [(external, "ext")]
    if with_in_tree:
        resolved.append((home / "ws" / expected / "workspaces" / "intree", "intree"))
    for source, box in resolved:
        info = _cli(env, "box", "info", cwd=source)
        assert info.returncode == 0, info.stderr
        assert f"kb-{expected.replace('-', '--')}-{box}" in info.stdout


def test_a_normal_workset_beside_a_reserved_one_is_untouched(env: dict[str, str]) -> None:
    home = Path(env["HOME"])
    (home / "ws").mkdir()
    _plant_legacy(env, "primary", home / "ws" / "primary")
    external, _ = _make_workset(env, "team", home / "ws" / "team")

    info = _cli(env, "box", "info", cwd=external)
    assert info.returncode == 0, info.stderr
    assert "kb-team-ext" in info.stdout
    assert "reserved name" not in info.stderr
    assert sorted(_worksets()) == ["primary", "team"]
