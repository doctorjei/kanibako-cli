"""``box purge`` of a NAMED member never touches the primary registry or the parked section.

``_unregister_purged`` ran its PRIMARY branch for a named member: a successful purge of
``nws/bar`` unregistered a same-named PRIMARY box ``bar``, and a failed one also parked
the member as ``kind: primary``.  The member's membership is its workset's; it stays
registered, so its path is the retry's address.
"""

from __future__ import annotations

import argparse

import pytest

from kanibako.project import registry_store
from kanibako.project.workset import add_project, create_workset, load_workset
from kanibako.settings.config import load_config
from kanibako.settings.paths import (
    BoxMode, WorksetSpec, _early_scope, load_std_paths, primary_box_name_for_workspace,
    resolve_project, resolve_workset_project)


def _world(config_file, tmp_home):
    """A PRIMARY ``bar`` and a NAMED member ``nws/bar`` — one name, two boxes."""
    config = load_config(config_file)
    std = load_std_paths(config)
    pdir = tmp_home / "code" / "bar"
    pdir.mkdir(parents=True)
    primary = resolve_project(std, config, project_dir=str(pdir), initialize=True)
    assert primary.mode is BoxMode.primary and primary.name == "bar", "setup"
    ws = create_workset("nws", tmp_home / "worksets" / "nws", std)
    src = tmp_home / "nsrc" / "bar"
    src.mkdir(parents=True)
    add_project(ws, "bar", src)
    member = resolve_workset_project(WorksetSpec.from_workset(ws), "bar", std, config,
                                     initialize=True)
    assert member.mode is BoxMode.named, "setup"
    for proj in (primary, member):
        proj.vault_rw_path.mkdir(parents=True, exist_ok=True)
        (proj.vault_rw_path / "notes.txt").write_text("mine")
    return std, ws, primary, member


def _primary_name(std, primary):
    return primary_box_name_for_workspace(
        std.primary_workset, str(primary.project_path),
        early=_early_scope(std, BoxMode.primary))


def _members(std, ws):
    return {p.name for p in load_workset(ws.root, ws.name,
                                         early_system=std.early_system).projects}


def _purge(path):
    from kanibako.commands.clean import run
    return run(argparse.Namespace(all_projects=False, force=True, path=path))


def _fail_member_vault(monkeypatch, member):
    from kanibako.runtime.container import remove_path as real

    st = {"fail": True, "calls": 0}

    def _rp(path, *a, **k):
        if str(path) == str(member.vault_ro_path):
            st["calls"] += 1
            if st["fail"]:
                return False
        return real(path, *a, **k)
    monkeypatch.setattr("kanibako.commands.clean.remove_path", _rp)
    return st


def _primary_untouched(std, primary):
    assert _primary_name(std, primary) == "bar", "the PRIMARY 'bar' was unregistered"
    assert primary.metadata_path.is_dir(), "the PRIMARY's metadata was touched"
    assert (primary.vault_rw_path / "notes.txt").is_file(), "the PRIMARY's vault was touched"


class TestAMemberPurgeLeavesTheSameNamedPrimary:
    @pytest.mark.parametrize("ref", ["source", "nws/bar"])
    def test_success_keeps_the_primary_registered(
            self, ref, config_file, tmp_home, credentials_dir, capsys):
        std, ws, primary, member = _world(config_file, tmp_home)
        target = str(member.project_path) if ref == "source" else ref

        assert _purge(target) == 0, capsys.readouterr()
        assert not member.metadata_path.exists(), "setup: the member was purged"
        _primary_untouched(std, primary)
        assert dict(registry_store.load_deregistered(std.registry)) == {}

    def test_failure_keeps_the_primary_and_parks_nothing(
            self, config_file, tmp_home, credentials_dir, capsys, monkeypatch):
        std, ws, primary, member = _world(config_file, tmp_home)
        st = _fail_member_vault(monkeypatch, member)

        assert _purge(str(member.project_path)) == 1
        capsys.readouterr()
        assert st["calls"], "setup: the injected step was never reached"
        assert member.metadata_path.is_dir(), "the anchor stays for the retry"
        _primary_untouched(std, primary)
        assert dict(registry_store.load_deregistered(std.registry)) == {}, (
            "a named member must never be parked (as a primary or at all)")

    def test_the_bare_name_inside_the_workset_takes_the_member_only(
            self, config_file, tmp_home, credentials_dir, capsys, monkeypatch):
        std, ws, primary, member = _world(config_file, tmp_home)
        monkeypatch.chdir(ws.root)

        assert _purge("bar") == 0, capsys.readouterr()
        assert not member.metadata_path.exists()
        _primary_untouched(std, primary)

    def test_the_bare_name_outside_takes_the_primary_only(
            self, config_file, tmp_home, credentials_dir, capsys, monkeypatch):
        std, ws, primary, member = _world(config_file, tmp_home)
        monkeypatch.chdir(tmp_home)

        assert _purge("bar") == 0, capsys.readouterr()
        assert not primary.metadata_path.exists()
        assert _primary_name(std, primary) is None, "a primary purge still unregisters"
        assert member.metadata_path.is_dir() and "bar" in _members(std, ws)
        assert (member.vault_rw_path / "notes.txt").is_file()


class TestAFailedMemberPurgeIsNotParked:
    """R6: the failure line names the member's own retry; nothing is parked."""

    def test_the_failure_names_the_member_and_its_retry(
            self, config_file, tmp_home, credentials_dir, capsys, monkeypatch):
        std, ws, _primary, member = _world(config_file, tmp_home)
        st = _fail_member_vault(monkeypatch, member)

        assert _purge("nws/bar") == 1
        err = capsys.readouterr().err
        assert st["calls"], "setup: the injected step was never reached"
        assert "parked" not in err, err
        assert ("nws/bar stays a member of its workset" in err
                and "kanibako box purge nws/bar" in err), err
        assert "bar" in _members(std, ws)
        assert dict(registry_store.load_deregistered(std.registry)) == {}

        st["fail"] = False
        assert _purge("nws/bar") == 0, "the printed retry finishes the purge"
        capsys.readouterr()
        assert not member.metadata_path.exists()
        assert dict(registry_store.load_deregistered(std.registry)) == {}

    def test_a_failed_primary_purge_still_parks_as_primary(
            self, config_file, tmp_home, credentials_dir, capsys, monkeypatch):
        std, _ws, primary, _member = _world(config_file, tmp_home)
        from kanibako.runtime.container import remove_path as real
        victim = str(primary.vault_ro_path)
        monkeypatch.setattr("kanibako.commands.box._parser.remove_path",
                            lambda p, *a, **k: False if str(p) == victim else real(p, *a, **k))

        assert _purge(str(primary.project_path)) == 1
        assert "the box is parked" in capsys.readouterr().err
        parked = dict(registry_store.load_deregistered(std.registry))
        assert parked.get("bar", {}).get("kind") == "primary", parked
