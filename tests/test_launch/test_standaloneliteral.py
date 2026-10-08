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
