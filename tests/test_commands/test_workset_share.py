"""Tests for kanibako.commands.workset_cmd share add/rm/list subcommands."""

from __future__ import annotations

import argparse

import pytest

from kanibako.commands.workset_cmd import (
    run_share_add,
    run_share_list,
    run_share_remove,
)
from kanibako.project.workset import create_workset


def read_bindings(path):
    """Test helper: read the on-disk ``workset.bindings.{ro,rw}.{name}`` leaves as
    a ``{dotted_key: raw_value}`` map — verifying what the share add/rm commands
    WROTE. (The product ``config.read_bindings`` reader was retired in block 7c; this
    local reader keeps these write-assertions on the structured on-disk shape.)"""
    from kanibako.settings.config_io import load_doc

    if path is None or not path.exists():
        return {}
    data = load_doc(path)
    ws = data.get("workset")
    if not isinstance(ws, dict):
        return {}
    binds = ws.get("bindings")
    if not isinstance(binds, dict):
        return {}
    out: dict[str, object] = {}
    for mode in ("ro", "rw"):
        node = binds.get(mode)
        if isinstance(node, dict):
            for name, val in node.items():
                out[f"workset.bindings.{mode}.{name}"] = val
    return out


@pytest.fixture
def workset(config_file, tmp_home, std):
    """Create and register a named working set; return the Workset."""
    ws_root = tmp_home / "myws"
    return create_workset("myws", ws_root, std)


def _add_args(workset="myws", name="data", bind="/host/data:/home/agent/data", mode="rw"):
    return argparse.Namespace(workset=workset, name=name, bind=bind, mode=mode)


def _rm_args(workset="myws", name="data", mode=None):
    return argparse.Namespace(workset=workset, name=name, mode=mode)


def _list_args(workset="myws", effective=False):
    return argparse.Namespace(workset=workset, effective=effective)


class TestShareAdd:
    def test_add_rw_writes_key(self, config_file, tmp_home, workset, capsys):
        rc = run_share_add(_add_args(mode="rw"))
        assert rc == 0
        bindings = read_bindings(workset.root / "settings.yaml")
        # Storage is STRUCTURED (spec §2a): a [host_src, box_dest] list, NOT a
        # colon-joined string (the colon form is only the CLI input grammar).
        assert bindings == {
            "workset.bindings.rw.data": ["/host/data", "/home/agent/data"]
        }
        out = capsys.readouterr().out
        assert "Added rw share 'data'" in out
        assert "next box launch" in out

    def test_add_ro_writes_key(self, config_file, tmp_home, workset):
        rc = run_share_add(_add_args(name="docs", bind="/host/docs:/srv/docs", mode="ro"))
        assert rc == 0
        bindings = read_bindings(workset.root / "settings.yaml")
        assert bindings == {"workset.bindings.ro.docs": ["/host/docs", "/srv/docs"]}

    def test_add_overwrite_updates(self, config_file, tmp_home, workset, capsys):
        run_share_add(_add_args(bind="/host/a:/g"))
        capsys.readouterr()
        rc = run_share_add(_add_args(bind="/host/b:/g"))
        assert rc == 0
        bindings = read_bindings(workset.root / "settings.yaml")
        assert bindings == {"workset.bindings.rw.data": ["/host/b", "/g"]}
        assert "Updated rw share 'data'" in capsys.readouterr().out

    def test_add_relative_is_absolutised_at_write(
        self, config_file, tmp_home, workset, capsys
    ):
        """T6 — a bare-relative host source is resolved against the working set
        root AT WRITE TIME and STORED absolute (spec §2a).

        The documented convenience is preserved (same input, same mount — see
        ``test_list_effective_relative_joins_root``); what changes is the ARTIFACT.
        A stored relative only resolved in the one context that knew the root, and
        the root lived in two places besides.

        (Mutation: store the raw relative again → the stored value is ``sub/dir``
        → RED here, and RED in the ``--effective`` twin.)
        """
        rc = run_share_add(_add_args(bind="sub/dir:/home/agent/data"))
        assert rc == 0
        bindings = read_bindings(workset.root / "settings.yaml")
        assert bindings["workset.bindings.rw.data"] == [
            str(workset.root / "sub" / "dir"), "/home/agent/data",
        ]
        # The user is TOLD the path was rewritten (a silent rewrite of a path the
        # user typed is the kind of thing they should hear about once).
        assert str(workset.root / "sub" / "dir") in capsys.readouterr().out

    @pytest.mark.parametrize(
        "src", ["/abs/host", "~/tdir", "$XDG_DATA_HOME/x", "@system.channelroot/x"],
    )
    def test_add_absolute_tilde_var_ref_are_stored_verbatim(
        self, config_file, tmp_home, workset, src
    ):
        """A source that already resolves on its own is stored UNTOUCHED — the
        root is a default for RELATIVE sources, not a universal law (§2a).
        """
        rc = run_share_add(_add_args(bind=f"{src}:/home/agent/data"))
        assert rc == 0
        bindings = read_bindings(workset.root / "settings.yaml")
        assert bindings["workset.bindings.rw.data"] == [src, "/home/agent/data"]

    def test_add_relative_on_default_workset_is_refused(
        self, config_file, tmp_home, capsys
    ):
        """The DEFAULT working set has no bindings root, so a relative source has
        nowhere to resolve — it is REFUSED at the moment of the mistake.

        Pre-P3 it was stored and then went to the mount spec as a relative string
        (both root tables set the workset arms only ``if not is_default``), i.e. it
        resolved against whatever the process CWD happened to be.
        """
        rc = run_share_add(_add_args(workset="default", bind="sub/dir:/g"))
        assert rc == 1
        err = capsys.readouterr().err
        assert "sub/dir" in err
        assert "default working set" in err

    def test_add_absolute_on_default_workset_is_allowed(
        self, config_file, tmp_home, std,
    ):
        """The refusal is narrow: only the shape that cannot resolve is refused."""
        rc = run_share_add(_add_args(workset="default", bind="/abs/h:/g"))
        assert rc == 0
        bindings = read_bindings(std.primary_workset / "settings.yaml")
        assert bindings["workset.bindings.rw.data"] == ["/abs/h", "/g"]

    @pytest.mark.parametrize("bind", ["nocolon", ":/dest", "/src:", "a:b:c"])
    def test_add_rejects_bad_bind(self, config_file, tmp_home, workset, bind, capsys):
        rc = run_share_add(_add_args(bind=bind))
        assert rc == 1
        assert "invalid bind" in capsys.readouterr().err

    def test_add_escaped_colon_in_host_src(self, config_file, tmp_home, workset):
        r"""P4 fix: a literal ':' in the host path is written ``\:`` and now parses
        via the canonical escape-aware ``split_bind`` — the split falls on the FIRST
        UNESCAPED ':' (before ``/guest``), and the stored host half has its escape
        resolved to a literal colon. The pre-fix ``partition(':')`` parser split on
        the escaped colon and then rejected the bind (``':' in guest_dest``); this is
        the divergence P4 closes."""
        rc = run_share_add(_add_args(bind="/host/pa\\:th:/guest/dest"))
        assert rc == 0
        bindings = read_bindings(workset.root / "settings.yaml")
        assert bindings == {
            "workset.bindings.rw.data": ["/host/pa:th", "/guest/dest"]
        }

    def test_add_escaped_colon_only_no_separator_rejected(
        self, config_file, tmp_home, workset, capsys
    ):
        r"""An escaped ``\:`` is a literal colon, NOT the host/guest separator: with
        no UNESCAPED ':' there is no separator, so this is rejected (guest half is
        None) — the required-two-fields check still surfaces."""
        rc = run_share_add(_add_args(bind="/host/pa\\:th"))
        assert rc == 1
        assert "invalid bind" in capsys.readouterr().err

    @pytest.mark.parametrize("name", ["bad name", "has/slash", "with:colon", ""])
    def test_add_rejects_bad_name(self, config_file, tmp_home, workset, name, capsys):
        rc = run_share_add(_add_args(name=name))
        assert rc == 1
        assert "invalid share name" in capsys.readouterr().err

    def test_add_unknown_workset(self, config_file, tmp_home, capsys):
        rc = run_share_add(_add_args(workset="nope"))
        assert rc == 1
        assert "not registered" in capsys.readouterr().err


class TestShareRemove:
    def test_rm_removes(self, config_file, tmp_home, workset, capsys):
        run_share_add(_add_args())
        capsys.readouterr()
        rc = run_share_remove(_rm_args())
        assert rc == 0
        assert read_bindings(workset.root / "settings.yaml") == {}
        out = capsys.readouterr().out
        assert "Removed rw share 'data'" in out
        assert "next box launch" in out

    def test_rm_with_explicit_mode(self, config_file, tmp_home, workset):
        run_share_add(_add_args(mode="ro", bind="/h:/g"))
        rc = run_share_remove(_rm_args(mode="ro"))
        assert rc == 0
        assert read_bindings(workset.root / "settings.yaml") == {}

    def test_rm_missing_returns_1(self, config_file, tmp_home, workset, capsys):
        rc = run_share_remove(_rm_args(name="ghost"))
        assert rc == 1
        assert "no share 'ghost'" in capsys.readouterr().err

    def test_rm_ambiguous_without_mode_returns_1(self, config_file, tmp_home, workset, capsys):
        run_share_add(_add_args(mode="rw", bind="/h:/g1"))
        run_share_add(_add_args(mode="ro", bind="/h:/g2"))
        capsys.readouterr()
        rc = run_share_remove(_rm_args(mode=None))
        assert rc == 1
        err = capsys.readouterr().err
        assert "both ro and rw" in err
        # Nothing removed.
        assert len(read_bindings(workset.root / "settings.yaml")) == 2

    def test_rm_ambiguous_with_mode_removes_one(self, config_file, tmp_home, workset):
        run_share_add(_add_args(mode="rw", bind="/h:/g1"))
        run_share_add(_add_args(mode="ro", bind="/h:/g2"))
        rc = run_share_remove(_rm_args(mode="rw"))
        assert rc == 0
        bindings = read_bindings(workset.root / "settings.yaml")
        assert bindings == {"workset.bindings.ro.data": ["/h", "/g2"]}

    def test_rm_wrong_mode_returns_1(self, config_file, tmp_home, workset, capsys):
        run_share_add(_add_args(mode="rw"))
        rc = run_share_remove(_rm_args(mode="ro"))
        assert rc == 1
        assert "(ro)" in capsys.readouterr().err


class TestShareList:
    def test_list_empty(self, config_file, tmp_home, workset, capsys):
        rc = run_share_list(_list_args())
        assert rc == 0
        assert "No bindings configured" in capsys.readouterr().out

    def test_list_raw(self, config_file, tmp_home, workset, capsys):
        run_share_add(_add_args(name="data", bind="/host/data:/home/agent/data", mode="rw"))
        run_share_add(_add_args(name="docs", bind="/host/docs:/srv/docs", mode="ro"))
        capsys.readouterr()
        rc = run_share_list(_list_args())
        assert rc == 0
        out = capsys.readouterr().out
        assert "data" in out and "/host/data:/home/agent/data" in out
        assert "docs" in out and "/host/docs:/srv/docs" in out
        assert "rw" in out and "ro" in out

    def test_list_effective_absolute_passthrough(self, config_file, tmp_home, workset, capsys):
        run_share_add(_add_args(bind="/abs/host:/home/agent/data", mode="rw"))
        capsys.readouterr()
        rc = run_share_list(_list_args(effective=True))
        assert rc == 0
        out = capsys.readouterr().out
        assert "/abs/host -> /home/agent/data  [rw]" in out

    def test_list_effective_relative_joins_root(self, config_file, tmp_home, workset, capsys):
        """The USER-VISIBLE outcome of a relative source is UNCHANGED by P3.

        The join moved from launch/display time to WRITE time, so the same input
        still prints (and mounts) the same absolute path — that equivalence is the
        whole reason the move is safe.  What is gone is the second implementation
        of the root: this display no longer joins anything, it just resolves what
        was stored.
        """
        run_share_add(_add_args(bind="sub/dir:/home/agent/data", mode="rw"))
        capsys.readouterr()
        rc = run_share_list(_list_args(effective=True))
        assert rc == 0
        out = capsys.readouterr().out
        expected = f"{workset.root / 'sub' / 'dir'} -> /home/agent/data  [rw]"
        assert expected in out

    def test_list_effective_ro_mode(self, config_file, tmp_home, workset, capsys):
        run_share_add(_add_args(name="docs", bind="/abs/docs:/srv/docs", mode="ro"))
        capsys.readouterr()
        rc = run_share_list(_list_args(effective=True))
        assert rc == 0
        assert "/abs/docs -> /srv/docs  [ro]" in capsys.readouterr().out
