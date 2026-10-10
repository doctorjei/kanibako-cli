"""A box root reached through another spelling than the one it is registered under.

[R188]: a box is its path as given.  A no-box message names the path as given, never its
resolved spelling, and ``create --standalone`` refuses a second link to a registered
standalone root, as a launch does.
"""

from __future__ import annotations

import argparse
from pathlib import Path


from kanibako.runtime.container import remove_box_tree


def _std(config_file):
    from kanibako.settings.config import load_config
    from kanibako.settings.paths import load_std_paths

    config = load_config(config_file)
    return config, load_std_paths(config)


def _create_args(path: Path, **over) -> argparse.Namespace:
    ns = argparse.Namespace(
        path=str(path), standalone=False, no_vault=True, name=None, image=None,
        agent=None, allow_home=False, recover=False, register=False,
    )
    for k, v in over.items():
        setattr(ns, k, v)
    return ns


class TestNoBoxMessagesNameThePathAsGiven:
    """[R188]: ``link/p`` is its own (absent) box, so the message names ``link/p``."""

    @staticmethod
    def _link(tmp_home: Path) -> tuple[Path, Path]:
        real = tmp_home / "real"
        (real / "p").mkdir(parents=True)
        (tmp_home / "link").symlink_to(real)
        return real / "p", tmp_home / "link" / "p"

    def test_by_path(self, config_file, tmp_home, credentials_dir) -> None:
        from kanibako.commands.start import _no_box_error

        real, link = self._link(tmp_home)
        _config, std = _std(config_file)
        msg = _no_box_error(str(link), std)
        assert f"no box at {link}." in msg, msg
        assert str(real) not in msg

    def test_by_cwd(self, config_file, tmp_home, credentials_dir, monkeypatch) -> None:
        from kanibako.commands.start import _no_box_error

        real, link = self._link(tmp_home)
        _config, std = _std(config_file)
        monkeypatch.chdir(link)
        monkeypatch.setenv("PWD", str(link))
        msg = _no_box_error(None, std)
        assert f"no box at {link}." in msg, msg
        assert str(real) not in msg

    def test_pending_create_at_the_link_spelling(self, tmp_home) -> None:
        from types import SimpleNamespace

        from kanibako.commands.start import (
            _unregistered_pending_create_error, _write_create_entry,
        )
        from kanibako.settings.paths import BoxMode

        real, link = self._link(tmp_home)
        std = SimpleNamespace(journal=tmp_home / "journal.yaml")
        proj = SimpleNamespace(
            shell_path=tmp_home / "boxes" / "p" / "home", mode=BoxMode.primary,
            name="p", project_path=link, group=None,
        )
        _write_create_entry(std, proj)
        msg = _unregistered_pending_create_error(std, str(link))
        assert msg is not None
        assert f"no box at {link}, but an interrupted 'create' is pending" in msg, msg
        assert str(real) not in msg


class TestStandaloneCreateAtAlias:
    """``create --standalone`` refuses a second link to a registered standalone root."""

    def test_refused_and_nothing_minted(
        self, config_file, tmp_home, credentials_dir, capsys,
    ) -> None:
        from kanibako.commands.box._parser import run_create
        from kanibako.project import registry_store
        from kanibako.settings.config import read_workset_kuid

        real = tmp_home / "real"
        (real / "s1").mkdir(parents=True)
        (tmp_home / "link").symlink_to(real)
        link_root = tmp_home / "link" / "s1"
        assert run_create(_create_args(link_root, standalone=True, register=True)) == 0
        remove_box_tree(real / "s1" / "box_data")
        _config, std = _std(config_file)
        registry_before = registry_store.load_standalone(std.registry)
        kuid_before = read_workset_kuid(real / "s1" / "workset.yaml")
        capsys.readouterr()

        rc = run_create(_create_args(real / "s1", standalone=True))

        err = capsys.readouterr().err
        assert rc == 1
        assert f"{real / 's1'} is another path to standalone box" in err, err
        assert f"registered at {link_root}" in err
        assert registry_store.load_standalone(std.registry) == registry_before
        assert read_workset_kuid(real / "s1" / "workset.yaml") == kuid_before
        assert not (real / "s1" / "box_data").exists()
