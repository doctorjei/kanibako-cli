"""``box list`` / ``box ps`` visibility, driven through ``cli.main``.

⚑ END-TO-END THROUGH ``cli.main``, deliberately: the rows are what a user reads,
and what decides whether a row is printed at all is the command's own
determination — the argument parser, the registry reads, and the container
lookup all run here.  The container runtime is a shell stub named by
``KANIBAKO_DOCKER_CMD``, so a box can be made running with no runtime present.

Pinned here:

- a registered box is never hidden for a workspace folder that is gone; the
  condition is shown on the row instead,
- a RUNNING box is what ``ps`` reports, whatever its workspace folder looks like,
- ``--orphan`` still names exactly the boxes whose workspace is gone,
- a healthy box's row is unchanged by any of the above.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

import pytest

from kanibako.settings.config import load_config
from kanibako.settings.paths import (
    BoxMode, _early_scope, load_primary_boxes, load_std_paths, resolve_project,
    resolve_standalone_project,
    unregister_primary_box_name,
)
from kanibako.project.workset import add_project, create_workset
from kanibako.utils import container_name_for_box_name, container_name_for_standalone_root


@pytest.fixture(autouse=True)
def _kanibako_logger_keeps_its_handlers():
    """Hand the ``kanibako`` logger back the handlers it had.

    ``cli.main`` runs ``setup_logging``, which installs a ``StreamHandler`` bound to
    whatever ``sys.stderr`` is at that moment — this test's capture buffer — and
    nothing removes it afterwards.  A later test's log record then lands on a buffer
    pytest has already closed, and logging answers with ``--- Logging error ---`` plus
    ``ValueError: I/O operation on closed file`` on ITS stderr, which reds any test
    asserting that stderr is clean.  The leak is in ``kanibako.log.setup_logging``,
    not here; this keeps it inside the file that provokes it.
    """
    logger = logging.getLogger("kanibako")
    saved = list(logger.handlers)
    yield
    logger.handlers[:] = saved

_ALIVE = "alive"     # primary box, workspace folder present
_GONE = "gone"       # primary box, workspace folder deleted
_STRAY = "stray"     # primary box with NO membership (no recorded workspace)
_MEMBER = "member"   # a NAMED workset's member, workspace folder deleted


class Sandbox:
    """One box per interesting state, each built by a production resolver."""

    def __init__(self, *, lone_root: Path) -> None:
        self.lone_root = lone_root

    def running_containers(self) -> tuple[str, ...]:
        """Container names for every box in the sandbox — the names ``start`` gives."""
        return (
            container_name_for_box_name(_ALIVE),
            container_name_for_box_name(_GONE),
            container_name_for_box_name(_STRAY),
            container_name_for_box_name(_MEMBER),
            container_name_for_standalone_root(self.lone_root),
        )


@pytest.fixture
def sandbox(config_file, tmp_home, credentials_dir, capsys):
    """Build the sandbox through the production resolvers."""
    config = load_config(config_file)
    std = load_std_paths(config)

    for name in (_ALIVE, _GONE, _STRAY):
        workspace = tmp_home / name
        workspace.mkdir()
        resolve_project(std, config, project_dir=str(workspace), initialize=True)
    # The membership stays; only the workspace folder goes.
    shutil.rmtree(tmp_home / _GONE)
    # A box with no membership has no recorded workspace to report.  The name comes
    # back from the registry the listing itself reads, never from the path.
    unregister_primary_box_name(
        std.primary_workset, _primary_name(std, _STRAY),
        early=_early_scope(std, BoxMode.primary),
    )

    # A workset member is never ALSO a primary box, and its workspace lives under
    # the workset's own ``workspaces/`` — not at the path handed to ``add_project``.
    ws = create_workset("demo", tmp_home / "worksets" / "demo", std)
    (tmp_home / _MEMBER).mkdir()
    add_project(ws, _MEMBER, tmp_home / _MEMBER)
    shutil.rmtree(ws.workspaces_dir / _MEMBER)

    # A STANDALONE box, whose container is keyed by its root rather than a name.
    lone = tmp_home / "lone"
    lone.mkdir()
    lone_root = resolve_standalone_project(
        std, config, str(lone), initialize=True,
    ).metadata_path
    shutil.rmtree(lone_root)

    capsys.readouterr()  # the resolvers announce their own setup; the rows are next
    return Sandbox(lone_root=lone_root)


def _primary_name(std, workspace_name: str) -> str:
    """The PRIMARY box name whose recorded workspace is *workspace_name*."""
    return next(
        name for name, workspace in load_primary_boxes(
            std.primary_workset, early=_early_scope(std, BoxMode.primary),
        ).items()
        if Path(workspace).name == workspace_name
    )


def _cli(argv, running: tuple[str, ...], tmp_path, monkeypatch, capsys):
    """``cli.main`` to completion; return its stdout, with *running* containers up."""
    rows = "".join(f"  printf '{n}\\tkanibako-oci:latest\\tUp 2 minutes\\n'\n" for n in running)
    stub = tmp_path / "container-runtime-stub"
    stub.write_text(
        "#!/bin/sh\n" + (f'if [ "$1" = "ps" ]; then\n{rows}fi\n' if rows else "") + "exit 0\n",
    )
    stub.chmod(0o755)
    monkeypatch.setenv("KANIBAKO_DOCKER_CMD", str(stub))
    capsys.readouterr()
    from kanibako import cli

    try:
        cli.main(argv)
    except SystemExit as exc:
        assert exc.code in (0, None), (argv, exc.code)
    return capsys.readouterr().out


def _row(out: str, name: str, status: str) -> str:
    """The one row for *name* whose STATUS cell reads *status*."""
    rows = [line for line in out.splitlines() if line.split()[:2] == [name, status]]
    assert len(rows) == 1, (name, status, out)
    return rows[0]


def _row_at(out: str, path: str, status: str) -> str:
    """The one row whose PATH/SOURCE/ROOT cell is *path* and whose STATUS is *status*."""
    rows = [
        line for line in out.splitlines()
        if line.split()[2:3] == [path] and line.split()[1:2] == [status]
    ]
    assert len(rows) == 1, (path, status, out)
    return rows[0]


# ---------------------------------------------------------------------------
# A missing workspace is information, not a filter.
# ---------------------------------------------------------------------------


def test_list_shows_a_box_whose_workspace_folder_is_gone(
    sandbox, tmp_path, monkeypatch, capsys,
):
    """A registered box with a deleted workspace folder is listed, not hidden."""
    out = _cli(["box", "list"], (), tmp_path, monkeypatch, capsys)
    row = _row(out, _GONE, "missing")
    # The STATUS cell already names the condition, so the path cell adds nothing.
    assert "missing workspace" not in row
    assert "no breadcrumb" not in row


def test_list_shows_a_standalone_box_whose_root_is_gone(
    sandbox, tmp_path, monkeypatch, capsys,
):
    """A registered standalone box whose root is gone is listed, not hidden."""
    out = _cli(["box", "list"], (), tmp_path, monkeypatch, capsys)
    assert "Standalone boxes:" in out
    _row_at(out, str(sandbox.lone_root), "missing")


def test_list_shows_a_named_workset_member_whose_workspace_is_gone(
    sandbox, tmp_path, monkeypatch, capsys,
):
    """A named workset's member with a deleted workspace is listed, not hidden."""
    out = _cli(["box", "list"], (), tmp_path, monkeypatch, capsys)
    assert "Workset: demo" in out
    _row(out, _MEMBER, "missing")


def test_list_all_includes_a_box_whose_workspace_folder_is_gone(
    sandbox, tmp_path, monkeypatch, capsys,
):
    """``-a`` still lists it — ``-a`` no longer decides whether a box appears."""
    out = _cli(["box", "list", "-a"], (), tmp_path, monkeypatch, capsys)
    _row(out, _GONE, "missing")


def test_orphan_still_names_the_boxes_whose_workspace_is_gone(
    sandbox, tmp_path, monkeypatch, capsys,
):
    """``--orphan`` names the gone-workspace boxes and the no-workspace box only."""
    out = _cli(["box", "list", "--orphan"], (), tmp_path, monkeypatch, capsys)
    assert _GONE in out
    assert _STRAY in out
    assert _MEMBER in out
    assert _ALIVE not in out


# ---------------------------------------------------------------------------
# A running container is what `ps` reports.
# ---------------------------------------------------------------------------


def test_ps_shows_a_running_box_whose_workspace_folder_is_gone(
    sandbox, tmp_path, monkeypatch, capsys,
):
    """``ps`` reports the running box and flags the workspace that is gone."""
    out = _cli(["box", "ps"], sandbox.running_containers(), tmp_path, monkeypatch, capsys)
    assert "missing workspace" in _row(out, _GONE, "active")


def test_list_shows_a_running_box_whose_workspace_folder_is_gone(
    sandbox, tmp_path, monkeypatch, capsys,
):
    """``list`` shows the running box as ``active``, with the condition on the row."""
    out = _cli(
        ["box", "list"], sandbox.running_containers(), tmp_path, monkeypatch, capsys,
    )
    assert "missing workspace" in _row(out, _GONE, "active")


def test_ps_all_reports_a_running_box_with_its_workspace_gone_as_active(
    sandbox, tmp_path, monkeypatch, capsys,
):
    """``ps -a`` agrees with ``ps`` about which box is running."""
    out = _cli(
        ["box", "ps", "-a"], sandbox.running_containers(), tmp_path, monkeypatch, capsys,
    )
    assert "missing workspace" in _row(out, _GONE, "active")


def test_ps_shows_a_running_workset_member_whose_workspace_is_gone(
    sandbox, tmp_path, monkeypatch, capsys,
):
    """A running workset member with a deleted workspace is reported and flagged."""
    out = _cli(["box", "ps"], sandbox.running_containers(), tmp_path, monkeypatch, capsys)
    assert "missing workspace" in _row(out, _MEMBER, "active")


def test_ps_shows_a_running_standalone_box_whose_root_is_gone(
    sandbox, tmp_path, monkeypatch, capsys,
):
    """A running standalone box whose root is gone is reported and flagged."""
    out = _cli(["box", "ps"], sandbox.running_containers(), tmp_path, monkeypatch, capsys)
    row = _row_at(out, str(sandbox.lone_root), "active")
    assert "missing workspace" in row


# ---------------------------------------------------------------------------
# A box with no recorded workspace at all.
# ---------------------------------------------------------------------------


def test_list_shows_a_box_with_no_recorded_workspace(
    sandbox, tmp_path, monkeypatch, capsys,
):
    """A box with no recorded workspace is listed, labelled by that fact."""
    out = _cli(["box", "list"], (), tmp_path, monkeypatch, capsys)
    assert "no breadcrumb" in _row(out, _STRAY, "unknown")


def test_ps_shows_a_running_box_with_no_recorded_workspace(
    sandbox, tmp_path, monkeypatch, capsys,
):
    """A running box with no recorded workspace is reported by ``ps``."""
    out = _cli(["box", "ps"], sandbox.running_containers(), tmp_path, monkeypatch, capsys)
    _row(out, _STRAY, "active")


# ---------------------------------------------------------------------------
# What must NOT change.
# ---------------------------------------------------------------------------


def test_list_row_for_a_healthy_box_is_unchanged(
    sandbox, tmp_path, monkeypatch, capsys,
):
    """A box with its workspace folder present carries no condition marker."""
    idle = _cli(["box", "list"], (), tmp_path, monkeypatch, capsys)
    assert "missing workspace" not in _row(idle, _ALIVE, "stopped")
    assert "no breadcrumb" not in _row(idle, _ALIVE, "stopped")
    busy = _cli(
        ["box", "list"], sandbox.running_containers(), tmp_path, monkeypatch, capsys,
    )
    assert "missing workspace" not in _row(busy, _ALIVE, "active")
    assert "no breadcrumb" not in _row(busy, _ALIVE, "active")


def test_ps_omits_a_stopped_box(sandbox, tmp_path, monkeypatch, capsys):
    """A stopped box is absent from ``ps``, and ``ps -a`` calls it ``stopped``."""
    out = _cli(["box", "ps"], (), tmp_path, monkeypatch, capsys)
    assert "No active boxes." in out
    assert _ALIVE not in out
    _row(_cli(["box", "ps", "-a"], (), tmp_path, monkeypatch, capsys), _ALIVE, "stopped")


def test_ps_omits_a_stopped_box_whose_workspace_folder_is_gone(
    sandbox, tmp_path, monkeypatch, capsys,
):
    """A box that is not running is absent from ``ps`` even with its workspace gone."""
    out = _cli(["box", "ps"], (), tmp_path, monkeypatch, capsys)
    assert _GONE not in out
    assert _MEMBER not in out
    assert "No active boxes." in out


# ---------------------------------------------------------------------------
# `-a` says one thing, for both verbs.
# ---------------------------------------------------------------------------


def test_show_all_help_is_one_string_for_list_and_ps():
    """``list -a`` and ``ps -a`` reach the same listing, so they read the same."""
    assert _show_all_help("list") == _show_all_help("ps")
    assert "every box" in _show_all_help("list")


def _show_all_help(verb: str) -> str:
    """The help string of *verb*'s ``-a`` option, read off the real parser."""
    import argparse

    from kanibako.commands.box._parser import add_parser

    def _subparser(parser: argparse.ArgumentParser, name: str) -> argparse.ArgumentParser:
        return next(
            action.choices[name]
            for action in parser._actions
            if isinstance(action, argparse._SubParsersAction)
        )

    root = argparse.ArgumentParser()
    add_parser(root.add_subparsers(dest="command"))
    verb_parser = _subparser(_subparser(root, "box"), verb)
    return str(next(a for a in verb_parser._actions if "-a" in a.option_strings).help)
