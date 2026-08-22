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
    """Test helper: read the on-disk ``workset.bindings.{ro,rw}`` entries as a
    ``{(mode, entry_key): raw_value}`` map — verifying what the share add/rm
    commands WROTE. (The product ``config.read_bindings`` reader was retired in
    block 7c; this local reader keeps these write-assertions on the structured
    on-disk shape.)

    ⚑ The entry key is the box DESTINATION since 2026-08-06c (R-10) — a binding
    has no entry name. Keyed as a TUPLE rather than a dotted string precisely
    because ``workset.bindings.rw.<dest>`` is no longer a KEY: a destination is
    data inside the arm's value, and spelling it as a dotted key here would
    reintroduce the retired shape in the test's own vocabulary.
    """
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
    out: dict[tuple[str, str], object] = {}
    for mode in ("ro", "rw"):
        node = binds.get(mode)
        if isinstance(node, dict):
            for entry_key, val in node.items():
                out[(mode, entry_key)] = val
    return out


@pytest.fixture
def workset(config_file, tmp_home, std):
    """Create and register a named working set; return the Workset."""
    ws_root = tmp_home / "myws"
    return create_workset("myws", ws_root, std)


def _add_args(workset="myws", bind="/host/data:/home/agent/data", mode="rw"):
    return argparse.Namespace(workset=workset, bind=bind, mode=mode)


def _rm_args(workset="myws", dest="/home/agent/data", mode=None):
    return argparse.Namespace(workset=workset, dest=dest, mode=mode)


def _list_args(workset="myws", effective=False):
    return argparse.Namespace(workset=workset, effective=effective)


class TestShareAdd:
    def test_add_rw_is_keyed_by_the_destination(
        self, config_file, tmp_home, workset, capsys
    ):
        """⚑ R-10 — the DESTINATION is the entry key; there is no share NAME.

        MUTATION: write ``subtree[host_src]`` (or restore a ``name`` positional
        and write ``subtree[name]``) in ``run_share_add`` -> the entry key is no
        longer the destination and this dies on the dict comparison. It is the
        KEY that is asserted, not just the value, so a value-only regression
        cannot keep it green.
        """
        rc = run_share_add(_add_args(mode="rw"))
        assert rc == 0
        bindings = read_bindings(workset.root / "settings.yaml")
        # Storage is STRUCTURED (spec §2a): a list, NOT a colon-joined string
        # (the colon form is only the CLI input grammar).
        # ⚑ The value is the 1-ELEMENT dest-keyed entry [src] (R-6): the
        # destination is the KEY and is written exactly once. P4′ stored the
        # 2-element [src, dest] pair only because the reader had not flipped yet;
        # P6 flipped reader + floor + this writer together.
        assert bindings == {("rw", "/home/agent/data"): ["/host/data"]}
        out = capsys.readouterr().out
        assert "Added rw share at '/home/agent/data'" in out
        assert "next box launch" in out

    def test_add_ro_writes_key(self, config_file, tmp_home, workset):
        rc = run_share_add(_add_args(bind="/host/docs:/srv/docs", mode="ro"))
        assert rc == 0
        bindings = read_bindings(workset.root / "settings.yaml")
        assert bindings == {("ro", "/srv/docs"): ["/host/docs"]}

    def test_add_overwrite_is_keyed_on_the_destination(
        self, config_file, tmp_home, workset, capsys
    ):
        """Re-adding at the SAME destination updates its source — that is the
        'update' path now that the name is gone (R-10)."""
        run_share_add(_add_args(bind="/host/a:/g"))
        capsys.readouterr()
        rc = run_share_add(_add_args(bind="/host/b:/g"))
        assert rc == 0
        bindings = read_bindings(workset.root / "settings.yaml")
        assert bindings == {("rw", "/g"): ["/host/b"]}
        assert "Updated rw share at '/g'" in capsys.readouterr().out

    def test_add_two_different_destinations_coexist(
        self, config_file, tmp_home, workset
    ):
        """The flip side: one SOURCE at two destinations is two entries. Under
        the retired name-keying the second `add` needed a second NAME to invent;
        now the destinations distinguish themselves."""
        run_share_add(_add_args(bind="/host/a:/g1"))
        run_share_add(_add_args(bind="/host/a:/g2"))
        bindings = read_bindings(workset.root / "settings.yaml")
        assert bindings == {
            ("rw", "/g1"): ["/host/a"],
            ("rw", "/g2"): ["/host/a"],
        }

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
        assert bindings[("rw", "/home/agent/data")] == [
            str(workset.root / "sub" / "dir"),
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
        assert bindings[("rw", "/home/agent/data")] == [src]

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
        assert bindings[("rw", "/g")] == ["/abs/h"]

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
        assert bindings == {("rw", "/guest/dest"): ["/host/pa:th"]}

    def test_add_escaped_colon_only_no_separator_rejected(
        self, config_file, tmp_home, workset, capsys
    ):
        r"""An escaped ``\:`` is a literal colon, NOT the host/guest separator: with
        no UNESCAPED ':' there is no separator, so this is rejected (guest half is
        None) — the required-two-fields check still surfaces."""
        rc = run_share_add(_add_args(bind="/host/pa\\:th"))
        assert rc == 1
        assert "invalid bind" in capsys.readouterr().err

    @pytest.mark.parametrize("dest", ["/has/slash", "~/tilde/dir", "relative/dir"])
    def test_add_accepts_any_destination_the_bind_grammar_admits(
        self, config_file, tmp_home, workset, dest
    ):
        """``_SHARE_NAME_RE`` is RETIRED and was NOT reborn as a destination
        validator (R-10). Its class excluded '/', so it could not describe a path;
        and the destination's real rule is R-11 (canonicalize the guest dest),
        which has now landed HERE, in ``run_share_add`` itself. A weaker second
        rule alongside it would be two rules for one thing.

        What still guards ``add`` is the BIND GRAMMAR, pinned by
        ``test_add_rejects_bad_bind``: exactly one unescaped ':', both halves
        non-empty.

        ⚑ The STORED key is the R-11-canonical spelling, not the typed one — ``~/``
        is expanded — so the expectation goes through ``normalize_bind_dest``. Using
        the real function is deliberate: hard-coding ``/home/agent/tilde/dir`` here
        would pin the guest home in a second place, and R-11 exists precisely so
        there is only one.
        """
        from kanibako.settings.settings_resolve import normalize_bind_dest

        rc = run_share_add(_add_args(bind=f"/host/src:{dest}"))
        assert rc == 0
        bindings = read_bindings(workset.root / "settings.yaml")
        assert ("rw", normalize_bind_dest(dest)) in bindings

    def test_add_unknown_workset(self, config_file, tmp_home, capsys):
        rc = run_share_add(_add_args(workset="nope"))
        assert rc == 1
        assert "not registered" in capsys.readouterr().err


class TestShareRemove:
    def test_rm_removes_by_destination(self, config_file, tmp_home, workset, capsys):
        run_share_add(_add_args())
        capsys.readouterr()
        rc = run_share_remove(_rm_args(dest="/home/agent/data"))
        assert rc == 0
        assert read_bindings(workset.root / "settings.yaml") == {}
        out = capsys.readouterr().out
        assert "Removed rw share at '/home/agent/data'" in out
        assert "next box launch" in out

    def test_rm_with_explicit_mode(self, config_file, tmp_home, workset):
        run_share_add(_add_args(mode="ro", bind="/h:/g"))
        rc = run_share_remove(_rm_args(dest="/g", mode="ro"))
        assert rc == 0
        assert read_bindings(workset.root / "settings.yaml") == {}

    def test_rm_missing_returns_1(self, config_file, tmp_home, workset, capsys):
        rc = run_share_remove(_rm_args(dest="/ghost"))
        assert rc == 1
        assert "no share at '/ghost'" in capsys.readouterr().err

    def test_rm_by_the_old_share_NAME_no_longer_finds_it(
        self, config_file, tmp_home, workset, capsys
    ):
        """R-10 is a CLEAN BREAK: the identity moved, so the old spelling misses.

        ⚑ MUTATION: key ``run_share_add``'s write on anything but the destination
        and this flips to rc 0 for the wrong reason — which is why the companion
        ``test_add_rw_is_keyed_by_the_destination`` asserts the key positively.
        """
        run_share_add(_add_args(bind="/host/data:/home/agent/data"))
        capsys.readouterr()
        rc = run_share_remove(_rm_args(dest="data"))
        assert rc == 1
        assert "no share at 'data'" in capsys.readouterr().err

    def test_rm_ambiguous_without_mode_returns_1(self, config_file, tmp_home, workset, capsys):
        run_share_add(_add_args(mode="rw", bind="/h:/g"))
        run_share_add(_add_args(mode="ro", bind="/h:/g"))
        capsys.readouterr()
        rc = run_share_remove(_rm_args(dest="/g", mode=None))
        assert rc == 1
        err = capsys.readouterr().err
        assert "both ro and rw" in err
        # Nothing removed.
        assert len(read_bindings(workset.root / "settings.yaml")) == 2

    def test_rm_ambiguous_with_mode_removes_one(self, config_file, tmp_home, workset):
        run_share_add(_add_args(mode="rw", bind="/h1:/g"))
        run_share_add(_add_args(mode="ro", bind="/h2:/g"))
        rc = run_share_remove(_rm_args(dest="/g", mode="rw"))
        assert rc == 0
        bindings = read_bindings(workset.root / "settings.yaml")
        assert bindings == {("ro", "/g"): ["/h2"]}

    def test_rm_wrong_mode_returns_1(self, config_file, tmp_home, workset, capsys):
        run_share_add(_add_args(mode="rw"))
        rc = run_share_remove(_rm_args(dest="/home/agent/data", mode="ro"))
        assert rc == 1
        assert "(ro)" in capsys.readouterr().err

    def test_rm_can_still_delete_a_retired_name_keyed_entry(
        self, config_file, tmp_home, workset, capsys
    ):
        """The cure ``_workset_raw_shares`` prescribes must be spellable: ``rm``
        deletes whatever entry key it is given, so a legacy name-keyed leftover
        can be removed by its name."""
        _write_legacy_named_entry(workset, "data", "/host/data", "/home/agent/data")
        rc = run_share_remove(_rm_args(dest="data"))
        assert rc == 0
        assert read_bindings(workset.root / "settings.yaml") == {}


def _write_legacy_named_entry(workset, name, src, dest, mode="rw"):
    """Hand-author the RETIRED name-keyed shape: entry key != its destination."""
    from kanibako.settings.config_io import dump_doc, load_doc

    path = workset.root / "settings.yaml"
    data = load_doc(path) if path.exists() else {}
    data.setdefault("workset", {}).setdefault("bindings", {}).setdefault(
        mode, {}
    )[name] = [src, dest]
    dump_doc(path, data)


class TestShareList:
    def test_list_empty(self, config_file, tmp_home, workset, capsys):
        rc = run_share_list(_list_args())
        assert rc == 0
        assert "No bindings configured" in capsys.readouterr().out

    def test_list_raw_columns_are_dest_mode_source(
        self, config_file, tmp_home, workset, capsys
    ):
        """The DEST column is the share's IDENTITY and is exactly what ``rm``
        takes — so it is the DEST that is printed, not a name and not the old
        colon-joined ``host:dest`` echo (which repeated the dest column)."""
        run_share_add(_add_args(bind="/host/data:/home/agent/data", mode="rw"))
        run_share_add(_add_args(bind="/host/docs:/srv/docs", mode="ro"))
        capsys.readouterr()
        rc = run_share_list(_list_args())
        assert rc == 0
        out = capsys.readouterr().out
        assert "DEST" in out and "SOURCE" in out and "NAME" not in out
        assert "/home/agent/data" in out and "/host/data" in out
        assert "/srv/docs" in out and "/host/docs" in out
        assert "rw" in out and "ro" in out
        # The retired display grammar is GONE — the dest is a column, not a
        # suffix on the source.
        assert "/host/data:/home/agent/data" not in out

    def test_list_refuses_a_retired_name_keyed_entry(
        self, config_file, tmp_home, workset, capsys
    ):
        """⚑ R-10 CONFORMANCE REFUSAL, and the reason it is a REFUSAL rather than
        a display fallback: under dest-keying the entry key IS the destination, so
        an entry keyed by a NAME cannot be shown honestly — the DEST column would
        print a NAME, and the ``rm`` argument it advertises would be a name too.

        ⚑ THE TEST MOVED WITH THE CODE (P6). P4′ could compare the key against the
        value's own second element; R-6 dropped that element, so the discriminator
        is now whether the KEY is spellable as a destination at all
        (``is_self_resolving``). The old assertion on ``destination is '…'`` is
        therefore GONE — that string can no longer be known — and the value below is
        asserted through the CURE line instead, which is where it does still appear.

        ⚑ MUTATION: delete the ``if not is_self_resolving(dest): raise`` block in
        ``_workset_raw_shares`` -> rc becomes 0 and the listing prints ``data`` in
        the DEST column. This test dies, and it is the ONLY place that token
        ("RETIRED name-keyed shape") is emitted — nothing else in the suite reaches
        this code path, so it cannot pass for another reason.
        """
        _write_legacy_named_entry(workset, "data", "/host/data", "/home/agent/data")
        rc = run_share_list(_list_args())
        assert rc == 1
        err = capsys.readouterr().err
        assert "RETIRED name-keyed shape" in err
        assert "keyed 'data'" in err
        # The cure is spellable, names BOTH halves the user needs, and hands back
        # the entry's own SOURCE so the re-add is copy-pasteable.
        assert "workset share rm" in err and "workset share add" in err
        assert "/host/data:<box_dest>" in err

    def test_list_reports_a_malformed_entry_instead_of_a_traceback(
        self, config_file, tmp_home, workset, capsys
    ):
        """``_workset_raw_shares`` goes through the real file reader, so a bad
        arity raises. A listing command must report it, not traceback.

        ⚑ THREE elements is the malformed shape: the dest-keyed entry takes
        ``[src[, options]]``, so a ONE-element list is perfectly legal.  (Until the
        identity moved to registry.yaml this test passed for the wrong reason — the
        rewritten settings.yaml dropped the identity, so ``load_workset`` refused
        before the reader was ever reached.)"""
        from kanibako.settings.config_io import dump_doc

        dump_doc(
            workset.root / "settings.yaml",
            {"workset": {"bindings": {"rw": {"/g": ["src", "opts", "extra"]}}}},
        )
        rc = run_share_list(_list_args())
        assert rc == 1
        assert "Error:" in capsys.readouterr().err

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
        run_share_add(_add_args(bind="/abs/docs:/srv/docs", mode="ro"))
        capsys.readouterr()
        rc = run_share_list(_list_args(effective=True))
        assert rc == 0
        assert "/abs/docs -> /srv/docs  [ro]" in capsys.readouterr().out
