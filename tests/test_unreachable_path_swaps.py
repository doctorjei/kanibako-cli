"""The settings readers and writers that guarded ``load_doc`` with ``Path.exists()``/``is_file()``.

Those guards raised a bare ``PermissionError`` on an unreadable parent and read a link loop
as absent.  Each now refuses both by name; a missing file still gets its old default.
One parametrized test per module: every function x (unreadable parent, loop, missing).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable

import pytest

from kanibako.errors import ConfigError

_SCENARIOS = ["unreadable", "loop", "missing"]


@pytest.fixture
def scene(tmp_path, request):
    """``(path, expected refusal regex or None)`` for the scenario in ``request.param``."""
    kind = request.param
    if kind == "unreadable":
        if os.geteuid() == 0:
            pytest.skip("root reads a mode-000 directory")
        locked = tmp_path / "locked"
        locked.mkdir()
        (locked / "f.yaml").write_text("a: 1\n")
        locked.chmod(0)
        yield locked / "f.yaml", f"^{locked / 'f.yaml'} could not be read \\(Permission denied\\)"
        locked.chmod(0o755)
        return
    path = tmp_path / "f.yaml"
    if kind == "loop":
        os.symlink("f.yaml", path)
        yield path, f"^{path}: it is a symlink loop"
        return
    yield path, None


def _check(call: Callable[[Path], Any], default: object, scene) -> None:
    path, refusal = scene
    if refusal is None:
        assert call(path) == default
        return
    with pytest.raises(ConfigError, match=refusal):
        call(path)


def _config_cases() -> dict[str, tuple[Callable[[Path], Any], object]]:
    from kanibako import kuid
    from kanibako.settings import config

    return {
        "read_workset_kuid": (config.read_workset_kuid, kuid.SENTINEL),
        "read_workset_skip_kuid_check": (config.read_workset_skip_kuid_check, True),
        "unset_project_config_key": (
            lambda p: config.unset_project_config_key(p, "box_image"), False),
        "read_agent_settings": (lambda p: config.read_agent_settings(p, "claude"), {}),
        "read_system_helpers": (config.read_system_helpers, {}),
        "read_setup_completed": (config.read_setup_completed, None),
    }


@pytest.mark.parametrize("scene", _SCENARIOS, indirect=True)
@pytest.mark.parametrize("func", list(_config_cases()))
def test_config_readers_refuse_an_unreachable_file_and_default_a_missing_one(func, scene):
    _check(*_config_cases()[func], scene)


def _config_io_cases() -> dict[str, tuple[Callable[[Path], Any], object]]:
    from kanibako.settings import config_io

    return {
        "remove_root_key": (lambda p: config_io.remove_root_key(p, "k"), False),
        "refuse_scalar_sections": (
            lambda p: config_io.refuse_scalar_sections(p, ("a", "b")), None),
        "remove_nested_key": (lambda p: config_io.remove_nested_key(p, ("a",), "k"), False),
        "stored_leaf_object": (
            lambda p: config_io.stored_leaf_object(p, ("a",), "k", default="unset"), "unset"),
    }


@pytest.mark.parametrize("scene", _SCENARIOS, indirect=True)
@pytest.mark.parametrize("func", list(_config_io_cases()))
def test_config_io_helpers_refuse_an_unreachable_file_and_default_a_missing_one(func, scene):
    _check(*_config_io_cases()[func], scene)


@pytest.mark.parametrize("scene", _SCENARIOS, indirect=True)
@pytest.mark.parametrize("self_root", [True, False], ids=["self", "agent-node"])
def test_agent_file_spelled_sections_refuses_an_unreachable_file(self_root, scene):
    from kanibako.settings.agent_file import AgentFileSlot, _spelled_sections

    def call(p: Path) -> tuple[str, ...]:
        return _spelled_sections(AgentFileSlot(path=p, tail="k", node="claude",
                                               self_root=self_root), ("self", "s"), "k")

    _check(call, ("self", "s") if self_root else ("agent", "claude", "s"), scene)


@pytest.mark.parametrize("scene", _SCENARIOS, indirect=True)
def test_config_interface_reset_all_refuses_an_unreachable_settings_file(tmp_path, scene):
    """The system scope: ``box.yaml``-style config apart from the settings file it sweeps."""
    from kanibako.settings.config_interface import reset_all

    cfg = tmp_path / "kanibako.cfg"
    cfg.write_text("")

    def call(p: Path) -> str:
        reset_all(config_path=cfg, force=True, system_settings_path=p)
        return "ok"

    _check(call, "ok", scene)
