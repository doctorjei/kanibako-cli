"""Standalone box identity is the PATH, not the resolved target (Q4Jei 155-LITERAL).

A standalone box at ``/data/proj`` reached through ``/home/alias`` (a symlink)
is found by literal path: ``box info /home/alias`` answers for the box at
``/home/alias``, the source file is the link, the path-as-given is the
identity.  A second standalone box at a different link to the same
target is its own box (no shadowing).

No backward-compat test for pre-1.8.0 boxes that registered the
RESOLVED path — the brief retracted the dual-match lookup; 1.8.0 is a
clean break.
"""

from __future__ import annotations

from pathlib import Path

from kanibako.launch import box_resolve
from kanibako.settings.config import load_config
from kanibako.settings.paths import (
    BoxMode,
    load_std_paths,
    resolve_standalone_project,
)


def _standalone(env, path: Path) -> None:
    config, std, _tmp_home = env
    resolve_standalone_project(std, config, project_dir=str(path), initialize=True)


class TestAStandaloneReachedThroughALinkIsFoundByLiteralPath:
    def test_a_link_to_the_standalone_root_resolves_to_that_box(
            self, config_file, tmp_home, credentials_dir):
        config = load_config(config_file)
        std = load_std_paths(config)
        env = (config, std, tmp_home)
        real = tmp_home / "data" / "proj"
        real.mkdir(parents=True)
        link = tmp_home / "alias"
        link.symlink_to(real)
        _standalone(env, real)

        # ``detect_box_mode`` short-circuits on the standalone marker and would
        # (on base) return the resolved target; the fix returns the link.
        det = box_resolve.detect_box_mode(link, std, config)
        assert det is not None
        assert det.mode is BoxMode.standalone
        assert det.project_root == link

    def test_a_subdir_under_the_link_resolves_to_the_same_box(
            self, config_file, tmp_home, credentials_dir):
        config = load_config(config_file)
        std = load_std_paths(config)
        env = (config, std, tmp_home)
        real = tmp_home / "data" / "proj"
        real.mkdir(parents=True)
        link = tmp_home / "alias"
        link.symlink_to(real)
        _standalone(env, real)

        sub = link / "src" / "nested"
        sub.mkdir(parents=True)
        # ``box info <subdir>`` from the link subdir answers for the link.
        det = box_resolve.detect_box_mode(sub, std, config)
        assert det is not None
        assert det.mode is BoxMode.standalone
        assert det.project_root == link

    def test_a_second_link_to_the_same_target_is_its_own_box(
            self, config_file, tmp_home, credentials_dir):
        config = load_config(config_file)
        std = load_std_paths(config)
        env = (config, std, tmp_home)
        real = tmp_home / "data" / "proj"
        real.mkdir(parents=True)
        first = tmp_home / "first"
        second = tmp_home / "second"
        first.symlink_to(real)
        second.symlink_to(real)
        _standalone(env, real)
        _standalone(env, first)

        # Detection at the SECOND link yields the second, not the first.
        det = box_resolve.detect_box_mode(second, std, config)
        assert det is not None
        assert det.mode is BoxMode.standalone
        assert det.project_root == second
        # The first link is still a different box (its own entry).
        det_first = box_resolve.detect_box_mode(first, std, config)
        assert det_first is not None
        assert det_first.project_root == first


# ---------------------------------------------------------------------------
# The CLI chain: create, look up, register, warn, and the $HOME guard
# ---------------------------------------------------------------------------

def _create_args(path, **over):
    import argparse
    ns = argparse.Namespace(
        path=None if path is None else str(path), standalone=True, no_vault=True,
        name=None, image=None, agent=None, allow_home=False,
        private=False, register=True,
    )
    for k, v in over.items():
        setattr(ns, k, v)
    return ns


def _enter(monkeypatch, path: Path) -> None:
    """``cd`` the way a shell does: the directory, and ``$PWD`` spelled as reached."""
    monkeypatch.chdir(path)
    monkeypatch.setenv("PWD", str(path))


def _linked_proj(tmp_home: Path, link: str = "alias") -> Path:
    """``<tmp>/<link>/proj``, where ``<link>`` points at ``<tmp>/real``."""
    real = tmp_home / "real"
    (real / "proj").mkdir(parents=True, exist_ok=True)
    (tmp_home / link).symlink_to(real)
    return tmp_home / link / "proj"


def _standalone_entries(std) -> dict:
    from kanibako.project import registry_store
    return registry_store.load_standalone(std.registry)


class TestTheCliChainKeepsTheLiteralPath:
    def test_create_through_a_link_registers_the_link_and_looks_up_from_it(
            self, config_file, tmp_home, credentials_dir, monkeypatch):
        from kanibako.commands.box._parser import run_create
        from kanibako.settings.paths import resolve_any_project

        config = load_config(config_file)
        std = load_std_paths(config)
        proj = _linked_proj(tmp_home)
        assert run_create(_create_args(proj)) == 0
        entries = _standalone_entries(std)
        assert list(entries.values()) == [str(proj)]
        (name,) = entries

        sub = proj / "sub"
        sub.mkdir()
        for where, designation in ((proj, None), (sub, None), (proj, "."), (tmp_home, str(proj))):
            _enter(monkeypatch, where)
            found = resolve_any_project(std, config, designation)
            assert found.name == name
            assert found.metadata_path == proj
        assert _standalone_entries(std) == {name: str(proj)}

    def test_create_with_no_path_takes_the_cwd_as_the_shell_reached_it(
            self, config_file, tmp_home, credentials_dir, monkeypatch):
        from kanibako.commands.box._parser import run_create

        std = load_std_paths(load_config(config_file))
        proj = _linked_proj(tmp_home)
        _enter(monkeypatch, proj)
        assert run_create(_create_args(None)) == 0
        assert list(_standalone_entries(std).values()) == [str(proj)]

    def test_box_register_through_a_link_stores_the_link(
            self, config_file, tmp_home, credentials_dir, monkeypatch):
        import argparse

        from kanibako.commands.box._parser import run_create, run_register

        std = load_std_paths(load_config(config_file))
        proj = _linked_proj(tmp_home)
        assert run_create(_create_args(proj, register=False)) == 0
        assert _standalone_entries(std) == {}
        _enter(monkeypatch, proj.parent)
        assert run_register(argparse.Namespace(target="proj", box=None)) == 0
        assert list(_standalone_entries(std).values()) == [str(proj)]

    def test_a_second_link_spelling_of_a_registered_name_warns_with_its_path(
            self, config_file, tmp_home, credentials_dir, monkeypatch, caplog):
        from kanibako.project import registry_store
        from kanibako.settings.paths import resolve_designation

        std = load_std_paths(load_config(config_file))
        proj = _linked_proj(tmp_home)
        (tmp_home / "alias2").symlink_to(tmp_home / "real")
        registry_store.register_standalone(std.registry, "proj", proj)
        _enter(monkeypatch, tmp_home / "alias2")
        with caplog.at_level("WARNING"):
            assert resolve_designation(std, "proj", unknown_name_is_path=False) == "proj"
        warned = [r.getMessage() for r in caplog.records if "is shadowed" in r.getMessage()]
        assert len(warned) == 1
        assert str(tmp_home / "alias2" / "proj") in warned[0]


class TestTheHomeGuardComparesResolvedPaths:
    def test_a_link_to_home_is_refused_without_allow_home(
            self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.box._parser import run_create

        home = Path.home()
        (tmp_home / "homelink").symlink_to(home)
        assert run_create(_create_args(tmp_home / "homelink", register=False)) == 1
        assert not (home / "box_data").exists()

    def test_home_spelled_through_a_link_is_refused_without_allow_home(
            self, config_file, tmp_home, credentials_dir, monkeypatch):
        from kanibako.commands.box._parser import run_create

        linkhome = tmp_home / "linkhome"
        linkhome.symlink_to(Path.home())
        monkeypatch.setenv("HOME", str(linkhome))
        assert run_create(_create_args(linkhome, register=False)) == 1
        assert not (linkhome / "box_data").exists()
