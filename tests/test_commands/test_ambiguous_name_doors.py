"""The LAUNCH front doors must let an ambiguous box name through by TYPE.

``AmbiguousNameError`` is a ``ProjectError``, and both launch doors read
``ProjectError`` as "there is no box here" -- so a name that belongs to two
worksets was reported as a plain miss instead of as the choice the user has to
make.  The message the resolver builds names every candidate by
``<workset>/<name>`` and states that cure; these tests pin that it reaches the
user.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from kanibako.errors import AmbiguousNameError


def _make_member(std, config, tmp_home, workset: str, member: str) -> Path:
    """Create a NAMED workset with one materialized member box; return its workspace."""
    from kanibako.project.workset import add_project, create_workset
    from kanibako.settings.paths import WorksetSpec, resolve_workset_project

    ws = create_workset(workset, tmp_home / "worksets" / workset, std)
    source = tmp_home / f"{workset}-src"
    source.mkdir()
    add_project(ws, member, source)
    resolve_workset_project(
        WorksetSpec.from_workset(ws), member, std, config, initialize=True,
    )
    return Path(ws.workspaces_dir) / member


def _two_worksets_holding(std, config, tmp_home, member: str) -> tuple[Path, Path]:
    """Register *member* in two worksets -- the ambiguous-name setup."""
    first = _make_member(std, config, tmp_home, "cluster-a", member)
    second = _make_member(std, config, tmp_home, "cluster-b", member)
    return first, second


def _assert_actionable(message: str, name: str) -> None:
    """The message must name BOTH candidates as ``<workset>/<name>``, plus the cure.

    The candidates are named by QUALIFIED name, not by workspace path: a
    ``workset.workspaces`` repoint puts the path outside the workset root, so a
    path need not contain the workset name and the prescribed cure is not
    derivable from one.
    """
    assert "Ambiguous box name" in message
    assert f"cluster-a/{name}" in message
    assert f"cluster-b/{name}" in message
    assert f"<workset>/{name}" in message


def _args(project=None, box=None) -> argparse.Namespace:
    return argparse.Namespace(project=project, box=box)


@pytest.fixture
def working_runtime():
    """A container runtime that is present.

    ``run_code`` builds its runtime BEFORE it resolves the target, so a runtime
    that refuses would return first and the resolve would never be reached --
    which would make every assertion below vacuous.  An absent ``code`` CLI is
    the discriminator that the resolve was passed rather than refused: the door
    reports the missing CLI only once it holds a resolved box.
    """
    rt = MagicMock()
    rt.is_running.return_value = True
    rt.container_image.return_value = "ghcr.io/doctorjei/kanibako-oci:latest"
    with patch("kanibako.commands.code_cmd.ContainerRuntime", return_value=rt), patch(
        "kanibako.commands.code_cmd._resolve_code_cli", return_value=None,
    ):
        yield rt


# --- start -----------------------------------------------------------------

class TestStartDoorAmbiguity:
    def test_start_door_refuses_an_ambiguous_name(
        self, std, config, tmp_home, monkeypatch,
    ):
        """``kanibako start foo`` names the choice, it does not read as a miss.

        The gate that covers every launch route resolves the target, then treats
        a refusal as "no box exists here" and points at ``kanibako create`` --
        a cure for a box the user already has, twice.
        """
        from kanibako.commands.start import _resolve_existing_box

        _two_worksets_holding(std, config, tmp_home, "foo")
        monkeypatch.chdir(tmp_home)

        with pytest.raises(AmbiguousNameError) as excinfo:
            _resolve_existing_box(std, config, "foo")
        _assert_actionable(str(excinfo.value), "foo")

    def test_start_door_unique_name_still_resolves(
        self, std, config, tmp_home, monkeypatch,
    ):
        """ONE workset is not ambiguous: the door resolves the box, unchanged."""
        from kanibako.commands.start import _resolve_existing_box

        _make_member(std, config, tmp_home, "solo", "foo")
        monkeypatch.chdir(tmp_home)

        proj = _resolve_existing_box(std, config, "foo")
        assert proj is not None
        assert proj.name == "foo"

    def test_start_door_unknown_name_is_still_no_box(
        self, std, config, tmp_home, monkeypatch,
    ):
        """An UNKNOWN name is the plain miss the door has always reported.

        This is the half of the pairing that keeps the fix from over-correcting:
        a name no workset holds must still read as "no box", not as ambiguity.
        """
        from kanibako.commands.start import _resolve_existing_box

        _make_member(std, config, tmp_home, "cluster-a", "foo")
        monkeypatch.chdir(tmp_home)

        assert _resolve_existing_box(std, config, "nonesuch") is None


# --- code ------------------------------------------------------------------

class TestCodeDoorAmbiguity:
    def test_code_door_refuses_an_ambiguous_name(
        self, std, config, tmp_home, monkeypatch, working_runtime,
    ):
        """``kanibako code foo`` names the choice, it does not read as a miss.

        This door resolves the same target as the launch gate but keeps its own
        handler, which discarded the ambiguity error and fell through to the
        generic "no box; run create" message.
        """
        from kanibako.commands.code_cmd import run_code

        _two_worksets_holding(std, config, tmp_home, "foo")
        monkeypatch.chdir(tmp_home)

        with pytest.raises(AmbiguousNameError) as excinfo:
            run_code(_args(project="foo"))
        _assert_actionable(str(excinfo.value), "foo")

    def test_code_door_unique_name_passes_the_resolve(
        self, std, config, tmp_home, monkeypatch, working_runtime, capsys,
    ):
        """ONE workset is not ambiguous: the door gets past the resolve.

        The missing ``code`` CLI is the discriminator -- it is reported only
        once the door holds a resolved box, so the ambiguous case never gets
        there.
        """
        from kanibako.commands.code_cmd import run_code

        _make_member(std, config, tmp_home, "solo", "foo")
        monkeypatch.chdir(tmp_home)
        capsys.readouterr()

        rc = run_code(_args(project="foo"))
        err = capsys.readouterr().err
        assert rc == 1
        assert "Ambiguous box name" not in err
        assert "code' CLI was not found" in err

    def test_code_door_unknown_name_keeps_the_generic_no_box_message(
        self, std, config, tmp_home, monkeypatch, working_runtime, capsys,
    ):
        """An UNKNOWN name keeps the no-box message, as before."""
        from kanibako.commands.code_cmd import run_code

        _make_member(std, config, tmp_home, "cluster-a", "foo")
        monkeypatch.chdir(tmp_home)
        capsys.readouterr()

        rc = run_code(_args(project="nonesuch"))
        err = capsys.readouterr().err
        assert rc == 1
        assert "Ambiguous box name" not in err
        assert "kanibako create" in err

    def test_code_door_never_advises_creating_an_ambiguous_box(
        self, std, config, tmp_home, monkeypatch, working_runtime, capsys,
    ):
        """The harm of the swallow was the ADVICE, so the advice is pinned.

        "Run ``kanibako create foo``" is what the user is told when the name
        already names two boxes; creating a third is the wrong move, and it is
        the one instruction the old message actually gave.
        """
        from kanibako.commands.code_cmd import run_code

        _two_worksets_holding(std, config, tmp_home, "foo")
        monkeypatch.chdir(tmp_home)
        capsys.readouterr()

        with pytest.raises(AmbiguousNameError):
            run_code(_args(project="foo"))
        assert "kanibako create" not in capsys.readouterr().err


# --- the user-visible rendering --------------------------------------------

class TestAmbiguityReachesTheUser:
    @pytest.mark.parametrize("argv", [["start", "foo"], ["code", "foo"]])
    def test_cli_prints_the_ambiguity_message(
        self, argv, std, config, tmp_home, monkeypatch, capsys,
    ):
        """The message survives to the terminal, candidates and cure included.

        Both doors raise; the top level renders any ``KanibakoError`` as a single
        ``Error:`` line.  This is what makes the type pass-through the contract
        rather than an internal detail.
        """
        from kanibako import cli

        _two_worksets_holding(std, config, tmp_home, "foo")
        monkeypatch.chdir(tmp_home)
        runtime = MagicMock()
        runtime.is_running.return_value = True
        with patch(
            "kanibako.commands.code_cmd.ContainerRuntime", return_value=runtime,
        ), patch("kanibako.cli._setup_nudge"), patch(
            "kanibako.cli._ensure_initialized",
        ):
            capsys.readouterr()
            with pytest.raises(SystemExit) as excinfo:
                cli.main(argv)
            out = capsys.readouterr()

        assert excinfo.value.code == 1, out.err
        _assert_actionable(out.err, "foo")
        # The generic no-box cure must NOT be what the user is told to do.
        assert "kanibako create" not in out.err
