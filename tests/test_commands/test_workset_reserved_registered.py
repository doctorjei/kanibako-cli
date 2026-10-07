"""A pre-1.8 workset registered under a reserved name is refused at load.

The names split two ways and the split is what the cure is built from: a PARTITION
TOKEN (``primary``) is unregistered by name, while an ALIAS VARIANT (``Default``) is
not — the ``workset`` verbs resolve ``default``/``__default__`` to the synthesized
default workset — so its cure renames the DIRECTORY instead.

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
#: The names the ``workset`` verbs resolve to the synthesized default workset.
ALIASES = frozenset({"default", "__default__"})


def _is_alias(legacy: str) -> bool:
    """Whether *legacy* is a case variant of a name the verbs resolve to PRIMARY."""
    return legacy.lower() in ALIASES


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
            # The path is a printed shell WORD, so it is read the way the shell
            # reads it; slicing the text left any quoting in the path.
            where = Path(shlex.split(target)[0])
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
    # ⚑ An ALIAS VARIANT is refused too, and MOVES even when its directory is
    # already legal: the registry KEY is the reserved thing, and the basename is
    # the only name a re-import can give the tree.
    ("Default", "Default", True),
    ("DEFAULT", "sa-root", True),
    ("__Default__", "__Default__", False),
    ("default", "sa-root", False),
])
def test_reserved_registered_workset_is_refused_then_cured(
    env: dict[str, str], legacy: str, leaf: str, with_in_tree: bool,
) -> None:
    home = Path(env["HOME"])
    root = home / "ws" / leaf
    root.parent.mkdir()
    external, in_tree = _plant_legacy(env, legacy, root, with_in_tree=with_in_tree)
    aliased = _is_alias(legacy)
    moved = aliased or leaf == legacy

    doors = [external, root] + ([in_tree] if in_tree is not None else [])
    for door in doors:
        refused = _cli(env, "box", "info", cwd=door)
        assert refused.returncode == 1, (door, refused.stdout)
        assert f"Working set '{legacy}' is registered under a reserved name" in refused.stderr
        # ⚑ A NAME-BASED step is printed only when it can run: ``workset rm
        # Default`` resolves to PRIMARY and refuses, so it is never offered.
        assert (f"kanibako workset rm {legacy} --force" in refused.stderr) == (not aliased)
        assert "No workset found" not in refused.stderr
        if not aliased:
            assert "default" not in refused.stderr.split(":", 2)[1]
        else:
            # ⚑ The rationale follows the split: an alias is refused for being the
            # verbs' name for PRIMARY, not for the partition-key collision that does
            # not apply to it — and it cites the MIGRATION heading for ITS case.
            assert "name for the primary working set" in refused.stderr
            assert "A working set named default must be registered again" in refused.stderr
            assert "__PRIMARY__" not in refused.stderr
    assert ("  mv " in refused.stderr) == moved
    assert ("box remap --force" in refused.stderr) == (moved and with_in_tree)

    listed = _cli(env, "workset", "list")
    assert listed.returncode == 0, listed.stderr
    assert legacy in listed.stdout.split()

    _run_cure(env, refused.stderr)

    expected = NEW_NAME if moved else leaf
    # ⚑ An ALIAS VARIANT's key outlives `workset rm`, which reads it as PRIMARY; the
    # first load after the `mv` drops it, its root being gone.
    assert sorted(_worksets()) == [expected]
    resolved: list[tuple[Path, str]] = [(external, "ext")]
    if with_in_tree:
        resolved.append((home / "ws" / expected / "workspaces" / "intree", "intree"))
    for source, box in resolved:
        info = _cli(env, "box", "info", cwd=source)
        assert info.returncode == 0, info.stderr
        assert f"kb-{expected.replace('-', '--')}-{box}" in info.stdout


def _tree(root: Path) -> dict[str, bytes]:
    """Every file under *root*, by relative path, with its bytes."""
    return {str(f.relative_to(root)): f.read_bytes() for f in root.rglob("*") if f.is_file()}


def test_an_alias_key_whose_root_is_gone_is_dropped_with_a_note(env: dict[str, str]) -> None:
    from kanibako.project.names import register_name

    home = Path(env["HOME"])
    gone = home / "ws" / "Default"
    gone.parent.mkdir()
    (gone.parent / "beside.txt").write_text("kept\n")
    register_name(_registry(), "Default", str(gone), section="worksets")
    before = _tree(home)

    listed = _cli(env, "workset", "list")
    assert listed.returncode == 0, listed.stderr
    assert f"Note: removed working set 'Default' ({gone}) from the registry" in listed.stderr
    assert "ERROR" not in listed.stdout and "Default" not in listed.stdout
    assert "Default" not in _worksets()
    assert _tree(home) == before
    again = _cli(env, "workset", "list")
    assert "Note:" not in again.stderr


def test_an_alias_key_on_the_primary_store_is_dropped_never_moved(env: dict[str, str]) -> None:
    """A hand-edited ``Default: <primary>`` is dropped; no ``mv`` of the primary store."""
    from kanibako.project.names import register_name
    from kanibako.settings.config import load_config, user_config_file
    from kanibako.settings.paths import load_std_paths

    project = Path(env["HOME"]) / "proj"
    project.mkdir()
    made = _cli(env, "box", "create", cwd=project)
    assert made.returncode == 0, made.stderr
    primary = load_std_paths(load_config(user_config_file())).primary_workset
    register_name(_registry(), "__DEFAULT__", str(primary), section="worksets")
    before = _tree(primary)
    assert before

    info = _cli(env, "box", "info", cwd=project)
    assert info.returncode == 0, info.stderr
    assert f"Note: removed working set '__DEFAULT__' ({primary}) from the registry" in info.stderr
    assert "reserved name" not in info.stderr and "mv " not in info.stderr
    assert "kb-primary-proj" in info.stdout
    assert _worksets() == {}
    assert _tree(primary) == before


def test_a_default_alias_in_any_case_is_refused_at_the_create_door(
    env: dict[str, str],
) -> None:
    """The create door clears the same bar case-blind: no variant registers."""
    home = Path(env["HOME"])
    for legacy in ("default", "Default", "DEFAULT", "__default__", "__Default__"):
        made = _cli(env, "workset", "create", str(home / "ws" / legacy), "--name", legacy)
        assert made.returncode == 1, (legacy, made.stdout)
        assert f"Workset name '{legacy}' is reserved" in made.stderr
        assert legacy not in _worksets()


@pytest.mark.parametrize("legacy", ["default", "Default", "DEFAULT", "__Default__"])
def test_default_alias_still_addresses_the_primary_workset(
    env: dict[str, str], legacy: str,
) -> None:
    """The reservation is at the doors, NOT at resolve: the verbs still read the
    alias as PRIMARY, and the primary workset is never itself refused."""
    info = _cli(env, "workset", "info", legacy)
    assert info.returncode == 0, info.stderr
    assert "Name:     __default__" in info.stdout
    assert "<default workset>" in info.stdout


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
