"""Standalone box identity is the PATH, not the resolved target (Q4Jei 155-LITERAL).

A standalone box at ``/data/proj`` reached through ``/home/alias`` (a symlink)
is found by literal path: ``box info /home/alias`` answers for the box at
``/home/alias``, the source file is the link, the path-as-given is the
identity.  A second link to a registered standalone root is refused ([R188]).

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
        _standalone(env, link)

        sub = link / "src" / "nested"
        sub.mkdir(parents=True)
        # ``box info <subdir>`` from the link subdir answers for the link.
        det = box_resolve.detect_box_mode(sub, std, config)
        assert det is not None
        assert det.mode is BoxMode.standalone
        assert det.project_root == link

    def test_a_second_link_to_a_registered_root_is_refused(
            self, config_file, tmp_home, credentials_dir):
        """[R188]: in standalone mode a second link to one root is refused, not a second box."""
        import pytest

        from kanibako.project import registry_store
        from kanibako.project.import_reconcile import ImportConflictError

        config = load_config(config_file)
        std = load_std_paths(config)
        env = (config, std, tmp_home)
        real = tmp_home / "data" / "proj"
        real.mkdir(parents=True)
        second = tmp_home / "second"
        second.symlink_to(real)
        _standalone(env, real)
        before = registry_store.load_standalone(std.registry)
        assert list(before.values()) == [str(real)]

        from kanibako.settings.paths import detect_project_mode
        with pytest.raises(ImportConflictError) as exc:
            detect_project_mode(second, std, config)
        assert f"Work from {real}." in str(exc.value)
        assert registry_store.load_standalone(std.registry) == before


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


# ---------------------------------------------------------------------------
# Re-register through a link: the stale ``deregistered:`` row goes with it
# ---------------------------------------------------------------------------

def _rm_args(target, **over):
    import argparse
    ns = argparse.Namespace(target=str(target), box=None, purge=False, force=False)
    for k, v in over.items():
        setattr(ns, k, v)
    return ns


def _register(target) -> int:
    import argparse

    from kanibako.commands.box._parser import run_register
    return run_register(argparse.Namespace(target=str(target), box=None))


def _deregistered_box(config_file, tmp_home, monkeypatch, link_leaf: str):
    """A standalone box created at ``<tmp>/real/proj``, then ``rm``'d; a link to it at
    ``<tmp>/<link_leaf>``.  Returns ``(std, name, real, link)``."""
    from kanibako.commands.box._parser import run_create, run_rm
    from kanibako.project import registry_store

    std = load_std_paths(load_config(config_file))
    real = tmp_home / "real" / "proj"
    real.mkdir(parents=True)
    assert run_create(_create_args(real)) == 0
    (name,) = _standalone_entries(std)
    assert run_rm(_rm_args(name)) == 0
    assert name in registry_store.load_deregistered(std.registry)
    link = tmp_home / link_leaf
    link.symlink_to(real)
    _enter(monkeypatch, tmp_home)
    return std, name, real, link


class TestReRegisterThroughALink:
    def test_register_through_a_link_drops_the_stale_row(
            self, config_file, tmp_home, credentials_dir, monkeypatch, capsys):
        from kanibako.project import registry_store

        std, name, _real, link = _deregistered_box(
            config_file, tmp_home, monkeypatch, "proj")
        assert _register(link) == 0
        assert _standalone_entries(std) == {name: str(link)}
        assert registry_store.load_deregistered(std.registry) == {}
        capsys.readouterr()
        # ``register <name>`` no longer meets a stale row and its "Purge" advice.
        assert _register(name) == 0
        out = capsys.readouterr()
        assert "Purge" not in out.err
        assert f"already registered (standalone box at {link})" in out.out

    def test_purge_by_a_stale_row_never_deletes_the_live_box(
            self, config_file, tmp_home, credentials_dir, monkeypatch, capsys):
        """A row parked before this fix: the purge refuses and drops only the row."""
        from kanibako.commands.box._parser import run_rm
        from kanibako.project import registry_store

        std, name, real, link = _deregistered_box(
            config_file, tmp_home, monkeypatch, "elsewhere")
        stale = registry_store.lookup_deregistered(std.registry, name)
        assert _register(link) == 0
        (live,) = _standalone_entries(std)
        assert live != name
        registry_store.save_section(std.registry, "deregistered", {name: stale})
        capsys.readouterr()

        assert run_rm(_rm_args(name, purge=True, force=True)) == 1
        assert "not purging" in capsys.readouterr().err
        assert (real / "box_data").is_dir()
        assert registry_store.load_deregistered(std.registry) == {}
        assert _standalone_entries(std) == {live: str(link)}

    def test_register_by_a_stale_row_drops_only_the_row(
            self, config_file, tmp_home, credentials_dir, monkeypatch, capsys):
        from kanibako.project import registry_store

        std, name, _real, link = _deregistered_box(
            config_file, tmp_home, monkeypatch, "elsewhere")
        stale = registry_store.lookup_deregistered(std.registry, name)
        assert _register(link) == 0
        (live,) = _standalone_entries(std)
        registry_store.save_section(std.registry, "deregistered", {name: stale})
        capsys.readouterr()

        assert _register(name) == 0
        assert "dropped its stale deregistered entry" in capsys.readouterr().out
        assert registry_store.load_deregistered(std.registry) == {}
        assert _standalone_entries(std) == {live: str(link)}

    def test_a_second_link_is_refused_with_the_path_to_work_from(
            self, config_file, tmp_home, credentials_dir, monkeypatch, capsys):
        std, _name, real, link = _deregistered_box(
            config_file, tmp_home, monkeypatch, "proj")
        assert _register(real) == 0
        before = _standalone_entries(std)
        capsys.readouterr()

        assert _register(link) == 1
        err = capsys.readouterr().err
        assert f"Work from {real}." in err
        assert "relocate" not in err
        assert _standalone_entries(std) == before
