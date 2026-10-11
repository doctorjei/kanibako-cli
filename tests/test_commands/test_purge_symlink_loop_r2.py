"""purgeloop r2 (kanibako `re-purgeloop-r2` + `-bifrost`): the NON-standalone purge paths,
the looped LEAF, and the one site where the safe answer is the opposite of "not in tree".

r1 fixed the predicates and the standalone door. The review proved the class was unfinished:
the named-member and primary branches of ``_purge_one`` never reach a predicate at all --
they call ``remove_path`` directly, so a loop under the arm raised ``ELOOP`` out of the CLI
(3.13) where r1 had only pinned the 3.11 ``RuntimeError`` face.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from kanibako.project import workset as W

_LOOP_WHY = ("it is a symlink loop that kanibako cannot resolve, so it is left in place "
             "for you to look at.")


def _cli(*argv: str) -> int:
    from kanibako.cli import main

    with pytest.raises(SystemExit) as info:
        main(list(argv))
    return int(info.value.code or 0)


def _loop(path: Path) -> None:
    """Replace *path* with a link to its own name."""
    if path.is_symlink():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)
    path.symlink_to(path.name)


def _named_member(tmp_home, ws_name, member_name):
    """A connected named member; returns ``(member_dir, workset_dir)``."""
    member = tmp_home / "p" / member_name
    member.mkdir(parents=True)
    wsdir = tmp_home / ws_name
    assert _cli("workset", "create", "--name", ws_name, str(wsdir)) == 0
    assert _cli("workset", "connect", ws_name, str(member)) == 0
    return member, wsdir


def _primary(tmp_home, config_file, proj_name):
    from kanibako.settings.config import load_config
    from kanibako.settings.paths import load_std_paths, resolve_project

    cfg = load_config(config_file)
    std = load_std_paths(cfg)
    project_dir = tmp_home / "p" / proj_name
    project_dir.mkdir(parents=True)
    resolve_project(std, cfg, project_dir=str(project_dir), initialize=True)
    return project_dir, std


def _run_purge(argv, capsys):
    """Run a purge door; return ``(rc_or_None, raised_name, stdout+stderr)``."""
    try:
        rc = _cli(*argv)
        raised = None
    except BaseException as e:          # a traceback escaping the door is the bug
        rc, raised = None, type(e).__name__
    out = capsys.readouterr()
    return rc, raised, (out.out + out.err)


class TestItem1LoopedRwArmOnEveryDoor:
    """``_remove_snapshot_store`` reached ``unlink()`` under a loop: ELOOP, rc 1, the purge
    stopped partway with the rest of the box standing."""

    def test_named_member_door_a(self, config_file, tmp_home, credentials_dir, capsys):
        member, wsdir = _named_member(tmp_home, "wsA", "ma")
        loop = wsdir / "vault" / "rw" / "ma"
        _loop(loop)
        capsys.readouterr()
        rc, raised, text = _run_purge(("box", "purge", "--force", str(member)), capsys)
        assert raised is None, text
        assert rc == 0, text
        assert "Traceback" not in text, text
        assert loop.is_symlink(), "the looped leaf must survive"

    def test_primary_door_a(self, config_file, tmp_home, credentials_dir, capsys):
        project_dir, std = _primary(tmp_home, config_file, "pa")
        loop = std.primary_vault_rw
        _loop(loop)
        capsys.readouterr()
        rc, raised, text = _run_purge(("box", "purge", "--force", str(project_dir)),
                                     capsys)
        assert raised is None, text
        assert rc == 0, text
        assert f"Note: left the box store at {loop}/.versions/pa in place — {_LOOP_WHY}" \
               in text, text
        assert loop.is_symlink()

    def test_door_b_cannot_reach_a_named_member(self, config_file, tmp_home,
                                              credentials_dir, capsys):
        # ⚑ ``box rm`` addresses registered boxes only, so door B never reaches the
        # member's leaf: this pins the refusal, not a purge.
        member, wsdir = _named_member(tmp_home, "wsB", "mb")
        loop = wsdir / "vault" / "rw" / "mb"
        _loop(loop)
        capsys.readouterr()
        rc, raised, text = _run_purge(
            ("box", "rm", "--purge", "--force", str(member)), capsys)
        assert raised is None, text
        assert rc != 0, text
        assert "is not a registered box" in text, text
        assert loop.is_symlink(), "the looped leaf must survive"

    def test_primary_door_b(self, config_file, tmp_home, credentials_dir, capsys):
        project_dir, std = _primary(tmp_home, config_file, "pb")
        loop = std.primary_vault_rw / "pb"
        loop.mkdir(parents=True, exist_ok=True)
        _loop(loop)
        capsys.readouterr()
        rc, raised, text = _run_purge(
            ("box", "rm", "--purge", "--force", str(project_dir)), capsys)
        assert raised is None, text
        assert "Traceback" not in text, text
        assert loop.is_symlink(), "the looped leaf must survive"


class TestItem2LoopedLeafIsNamedOnBothDoors:
    """A looped vault LEAF used to be deleted with no Note (named), or deleted with a Note
    claiming a target that does not exist (primary)."""

    def test_named_leaf_is_kept_and_named(self, config_file, tmp_home, credentials_dir,
                                       capsys):
        member, wsdir = _named_member(tmp_home, "wsC", "mc")
        loop = wsdir / "vault" / "rw" / "mc"
        _loop(loop)
        capsys.readouterr()
        rc, raised, text = _run_purge(("box", "purge", "--force", str(member)), capsys)
        assert raised is None, text
        assert loop.is_symlink(), f"the leaf was deleted: {text}"
        assert f"in place — {_LOOP_WHY}" in text, text

    def test_primary_leaf_never_claims_a_target_it_lacks(self, config_file, tmp_home,
                                                     credentials_dir, capsys):
        project_dir, std = _primary(tmp_home, config_file, "pc")
        loop = std.primary_vault_rw / "pc"
        loop.mkdir(parents=True, exist_ok=True)
        _loop(loop)
        capsys.readouterr()
        rc, raised, text = _run_purge(("box", "purge", "--force", str(project_dir)),
                                     capsys)
        assert raised is None, text
        assert loop.is_symlink(), f"the leaf was deleted: {text}"
        # ⚑ The old Note: "removed the link X; left its target <name> untouched" -- a
        # self-loop HAS no target, so that sentence described a directory that never was.
        assert "left its target" not in text, text
        assert f"Note: left the vault at {loop} in place — {_LOOP_WHY}" in text, text

    def test_primary_door_b_names_what_it_kept(self, config_file, tmp_home,
                                            credentials_dir, capsys):
        project_dir, std = _primary(tmp_home, config_file, "pd")
        loop = std.primary_vault_rw / "pd"
        loop.mkdir(parents=True, exist_ok=True)
        _loop(loop)
        capsys.readouterr()
        rc, raised, text = _run_purge(
            ("box", "rm", "--purge", "--force", str(project_dir)), capsys)
        assert raised is None, text
        assert loop.is_symlink(), f"the leaf was deleted: {text}"
        assert "in place —" in text, f"kept but named nothing: {text}"


class TestItem3RefusalGuardKeepsTheSafeAnswer:
    """``refuse_null_box_workspace``: True from the in-tree test means RAISE, so a loop has
    to force the refusal. r1's blanket ``False`` let a looped workspace launch as external."""

    @pytest.fixture
    def guard_env(self, config_file, tmp_home, monkeypatch):
        from kanibako.settings.config import load_config
        from kanibako.settings.paths import BoxMode, _early_scope, load_std_paths

        # STANDALONE early: BoxMode.named needs a workset group to derive its channel
        # token, which this fixture has none of.  The guard's loop branch does not read the
        # mode -- ``_workspaces_null_file`` is stubbed, so `early` only has to exist.
        early = _early_scope(load_std_paths(load_config(config_file)),
                            BoxMode.standalone)
        wsroot = tmp_home / "wsroot"
        wsroot.mkdir()
        nullfile = wsroot / "workset.yaml"
        nullfile.write_text("workset.workspaces: null\n")
        monkeypatch.setattr(W, "_workspaces_null_file",
                           lambda root, *, early: nullfile)
        return wsroot, early

    def test_a_looped_workspace_IS_refused(self, guard_env):
        from kanibako.errors import WorksetError

        wsroot, early = guard_env
        loop = wsroot / "workspaces" / "m"
        loop.parent.mkdir(parents=True, exist_ok=True)
        _loop(loop)
        with pytest.raises(WorksetError) as info:
            W.refuse_null_box_workspace(wsroot, loop, "m", standalone=False,
                                       early=early)
        assert "symlink loop" in str(info.value), str(info.value)
        assert str(loop) in str(info.value)

    def test_a_genuinely_external_workspace_still_launches(self, guard_env):
        wsroot, early = guard_env
        outside = wsroot.parent / "outside" / "m"
        outside.mkdir(parents=True)
        W.refuse_null_box_workspace(wsroot, outside, "m", standalone=False, early=early)

    def test_an_in_tree_workspace_is_still_refused(self, guard_env):
        from kanibako.errors import WorksetError

        wsroot, early = guard_env
        inside = wsroot / "workspaces" / "z"
        inside.mkdir(parents=True)
        with pytest.raises(WorksetError):
            W.refuse_null_box_workspace(wsroot, inside, "m", standalone=False,
                                       early=early)


class TestBifrostPL3ALoopInsideCanonIsNamed:
    """The leftover-canon reason must name a loop INSIDE the tier, not just one AT it."""

    STAMP = {"handbook": (True, None), "handbook/COLLECTION.md": (False, None)}

    def _why(self, tmp_path, monkeypatch, extra):
        monkeypatch.setattr(W, "_create_canon_stamp", lambda *a, **k: dict(self.STAMP))
        leaf = tmp_path / "canon"
        (leaf / "handbook").mkdir(parents=True)
        (leaf / "handbook" / "COLLECTION.md").write_text("stamp\n")
        extra(leaf / "handbook")
        return W.default_canon_leftover_why(leaf, None)

    def test_a_loop_inside_names_the_inner_loop(self, tmp_path, monkeypatch):
        # ⚑ The tier resolves, so "it is a symlink loop" would be false of it: the
        # reason names the loop it HOLDS.
        def make(handbook):
            inner = handbook / "x"
            inner.mkdir()
            _loop(inner)

        inner = tmp_path / "canon" / "handbook" / "x"
        assert self._why(tmp_path, monkeypatch, make) == (
            f"it holds a symlink loop at {inner} that kanibako cannot resolve")

    def test_an_ordinary_extra_file_still_says_leftover(self, tmp_path, monkeypatch):
        def make(handbook):
            (handbook / "mine.md").write_text("mine\n")

        assert self._why(tmp_path, monkeypatch, make) == W.DEFAULT_CANON_LEFTOVER_WHY


class TestComposedCanonNote:
    """The reason is COMPUTED from a looped canon on disk, never handed in: a reason
    that doubles "in place" or "so it is" with the template turns these red."""

    @staticmethod
    def _looped_canon(tmp_path):
        root = tmp_path / "b"
        root.mkdir()
        canon = root / "canon"
        canon.symlink_to("canon")
        return root, canon

    @staticmethod
    def _assert_one_sentence(err, canon):
        assert err == (f"Note: left the canon folder at {canon} in place — it is a "
                      "symlink loop that kanibako cannot resolve, so it is yours to "
                      "remove.\n"), err
        assert err.count("in place") == 1
        assert err.count("so it is") == 1

    def test_through_canon_keep_reason(self, config_file, tmp_path, capsys):
        from kanibako.settings.config import load_config
        from kanibako.settings.paths import BoxMode, _early_scope, load_std_paths

        early = _early_scope(load_std_paths(load_config(config_file)), BoxMode.standalone)
        root, canon = self._looped_canon(tmp_path)
        W.report_retained_canon(canon, root, early=early)
        self._assert_one_sentence(capsys.readouterr().err, canon)

    def test_through_the_fallback(self, tmp_path, capsys):
        root, canon = self._looped_canon(tmp_path)
        W.report_retained_canon(canon, root)
        self._assert_one_sentence(capsys.readouterr().err, canon)

    def test_the_composed_vault_note_is_the_shared_loop_text(self, capsys):
        W.report_retained_loop(Path("/x/vault"), "vault")
        err = capsys.readouterr().err
        assert err == (f"Note: left the vault at /x/vault in place — {_LOOP_WHY}\n"), err


class TestCopyIgnoreLoopBranch:
    """The loop branch compares LITERAL paths while the normal branch compares RESOLVED ones.
    That is not an oversight: for a loop the resolved path does not exist, so the literal
    path is the only identity the entry has -- and ``copytree`` hands the ignore callback
    the source directory as it was given, not a resolved one."""

    def test_a_looped_store_is_excluded_by_its_literal_entry(self, config_file, tmp_home,
                                                          credentials_dir):
        from kanibako.commands.box import _lifecycle
        from kanibako.settings.config import load_config
        from kanibako.settings.paths import BoxMode, _early_scope, load_std_paths

        early = _early_scope(load_std_paths(load_config(config_file)),
                            BoxMode.standalone)
        root = tmp_home / "boxroot"
        store = root / "vault"
        store.mkdir(parents=True)
        _loop(store)
        ignore = _lifecycle._workspace_copy_ignore(store, root, mode=BoxMode.standalone,
                                                 early=early)
        # The store is DERIVED (``box_metadata_dir``), not the loop itself -- assert on
        # what the helper actually named, not on the path I happened to loop.
        from kanibako.settings.paths import box_metadata_dir
        raw = box_metadata_dir(BoxMode.standalone, store, early=early)
        assert W._symlink_loop(raw), f"probe setup did not loop the store: {raw}"
        # copytree passes the directory it is scanning, as given.
        assert ignore(str(raw.parent), [raw.name]) == {raw.name}
        assert ignore(str(raw.parent), ["other"]) == set()
        assert store.is_symlink(), "the ignore must not have touched the link"


class TestALoopedLandingIsRefused:
    """The relocation guards REFUSE on True from ``is_in_tree_workspace``, which answers a
    loop "not in tree": a loop above the landing walked past all three.  On 3.13 the move
    then died on ELOOP, and an in-place convert SUCCEEDED, recording a looped workspace."""

    @staticmethod
    def _standalone_and_ws(tmp_home):
        wsdir = tmp_home / "ws2"
        assert _cli("workset", "create", "--name", "ws2", str(wsdir)) == 0
        root = tmp_home / "p" / "k"
        root.mkdir(parents=True)
        assert _cli("create", "--standalone", "--register", str(root)) == 0
        return root, wsdir

    @staticmethod
    def _assert_refused(argv, landing, root, capsys):
        capsys.readouterr()
        rc, raised, text = _run_purge(argv, capsys)
        assert raised is None, text
        assert rc == 1, text
        assert f"at {landing}: that path runs through a symlink loop" in text, text
        assert (root / "box_data").is_dir(), "the refused box must stay where it was"

    def test_membership_guard(self, config_file, tmp_home, credentials_dir, capsys):
        root, wsdir = self._standalone_and_ws(tmp_home)
        (wsdir / "workspaces" / "L").symlink_to("L")
        landing = wsdir / "workspaces" / "L" / "x"
        self._assert_refused(("box", "move", "--force", str(root), str(landing)),
                             landing, root, capsys)

    def test_member_leaf_guard(self, config_file, tmp_home, credentials_dir, capsys):
        root, wsdir = self._standalone_and_ws(tmp_home)
        (wsdir / "workspaces" / "L").symlink_to("L")
        landing = wsdir / "workspaces" / "L" / "x"
        self._assert_refused(("box", "move", "--force", str(root), str(landing),
                              "--workset", "ws2"), landing, root, capsys)

    def test_in_place_convert_guard(self, config_file, tmp_home, credentials_dir, capsys):
        root, wsdir = self._standalone_and_ws(tmp_home)
        landing = wsdir / "workspaces" / "L" / "q"
        landing.mkdir(parents=True)
        with (root / "workset.yaml").open("a") as f:
            f.write(f"  workspaces: {landing}\n")
        shutil.rmtree(wsdir / "workspaces" / "L")
        (wsdir / "workspaces" / "L").symlink_to("L")
        self._assert_refused(("box", "convert", "--force", str(root), "--workset", "ws2"),
                             landing, root, capsys)


class TestALoopedWorkspaceIsNamedAtLaunch:
    """bifrost PL9: resolution caught the loop refusal and set ``project_path`` to None, so
    the launch guard re-asked with None and blamed the null ``workset.workspaces`` key."""

    def test_launch_names_the_loop_and_reaches_no_runtime(
            self, config_file, tmp_home, credentials_dir, monkeypatch):
        from kanibako.commands import start
        from kanibako.errors import WorksetError
        from kanibako.project.workset import add_project, create_workset
        from kanibako.settings.config import load_config
        from kanibako.settings.config_io import dump_doc, load_doc
        from kanibako.settings.paths import load_std_paths

        std = load_std_paths(load_config(config_file))
        root = (tmp_home / "worksets" / "ws9").resolve()
        ws = create_workset("ws9", root, std)
        leaf = root / "workspaces" / "m"
        add_project(ws, "m", leaf, std)
        _loop(leaf)
        settings = root / "workset.yaml"
        doc = dict(load_doc(settings)) if settings.is_file() else {}
        doc["workset"] = {**doc.get("workset", {}), "workspaces": None}
        dump_doc(settings, doc)

        runtimes: list = []

        def _runtime(*args, **kwargs):
            runtimes.append((args, kwargs))
            raise AssertionError("the launch reached the container runtime")

        monkeypatch.setattr(start, "ContainerRuntime", _runtime)
        with pytest.raises(WorksetError) as info:
            start._run_container(project_dir="ws9/m", entrypoint=None,
                                 image_override=None, new_session=False,
                                 safe_mode=False, resume_mode=False, extra_args=[])
        message = str(info.value)
        assert message.startswith(f"Cannot launch box 'm': its workspace {leaf} is a "
                                  "symlink loop"), message
        assert "sets workset.workspaces to null" not in message, message
        assert runtimes == [], runtimes
        assert leaf.is_symlink(), "the refused launch must not touch the loop"
