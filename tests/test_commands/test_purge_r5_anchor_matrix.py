"""r5 — the anchor rule on EVERY branch, and the running probe on a parked primary.

From kanibako `re-purgeguard-r5.md` (R1, R2, R3, R4) and `re-purgeguard-r5-bifrost.md`
(D1p).  The rule: ``box purge`` puts its anchor LAST, and only when nothing failed.

Every matrix cell here fails ONE real step that runs BEFORE the anchor, then retries.
The r4 version of this matrix patched ``clean.remove_box_tree`` — which is the
anchor's OWN remover — so the anchor survived because its own delete failed.  That
is not "a later step failed" and it proves nothing.  Here the anchor's remover is
never touched; the failure goes into a pre-anchor step, and every injector counts
its own calls so a step that was never reached fails the test instead of passing.
"""
from __future__ import annotations

import argparse
import shlex
from pathlib import Path

import pytest

from kanibako.project import registry_store
from kanibako.settings.bootstrap import CREDS_WATCHER_LOCK_FILE
from kanibako.settings.config import load_config
from kanibako.settings.paths import (
    BoxMode,
    WorksetSpec,
    _early_scope,
    load_std_paths,
    resolve_project,
    resolve_standalone_project,
    resolve_workset_project,
    standalone_box_store,
)
from kanibako.snapshots import box_snapshot_store
from kanibako.utils import project_gitignore_to_strip, write_project_gitignore

_TS = "2026-10-10T00:00:00+00:00"


# --------------------------------------------------------------------------- #
# Failure injectors.  Each returns a dict: {"fail": bool, "calls": int}.  Flip
# "fail" to False to un-block the retry; "calls" proves the step was reached.
# --------------------------------------------------------------------------- #
def _fail_one_path(monkeypatch, victim):
    """Make ``remove_path`` return False for exactly ONE path, in both purge modules.

    Every other removal — including the anchor's own — still runs for real, so the
    only thing simulated is "this one step did not come away".
    """
    from kanibako.runtime.container import remove_path as real_remove

    st = {"fail": True, "calls": 0}

    def _rp(path, *args, **kwargs):
        if Path(path) == victim:
            st["calls"] += 1
            if st["fail"]:
                return False
        return real_remove(path, *args, **kwargs)

    # ``box purge`` and ``rm --purge`` share ``_run_purge_plan``; the named-member and
    # ``--all`` paths still delete from ``clean``.
    monkeypatch.setattr("kanibako.commands.clean.remove_path", _rp)
    monkeypatch.setattr("kanibako.commands.box._parser.remove_path", _rp)
    return st


def _fail_callable(monkeypatch, dotted):
    """Make a named predicate/verb return False, counting the times it was called."""
    import importlib

    mod_name, _, attr = dotted.rpartition(".")
    mod = importlib.import_module(mod_name)
    real = getattr(mod, attr)
    st = {"fail": True, "calls": 0}

    def _wrapped(*args, **kwargs):
        st["calls"] += 1
        if st["fail"]:
            return False
        return real(*args, **kwargs)

    monkeypatch.setattr(mod, attr, _wrapped)
    return st


def _fail_unlink_of(monkeypatch, victim):
    """Raise EACCES from ``Path.unlink`` for ONE path only.

    The creds-watcher lock is unlinked directly, with no bool to intercept, so this
    is the narrowest honest way to make that one unlink fail.
    """
    st = {"fail": True, "calls": 0}
    real_unlink = Path.unlink

    def _unlink(self, *args, **kwargs):
        if Path(self) == victim:
            st["calls"] += 1
            if st["fail"]:
                raise OSError(13, "Permission denied")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", _unlink)
    return st


def _asked_container_names(monkeypatch, tmp_path, P, running):
    """Run the REAL liveness check against a stand-in container CLI; record the NAME it asked.

    ⚑ Why not stub ``_check_container_running``: a stub answers "running" for whatever
    name it is handed, so it cannot see a probe that renders the WRONG one — which is
    exactly r6 V1.  Here the name is rendered by the code under test and matched against
    a fixed set of containers by the real ``_check_container_running`` body, so a wrong
    render comes back "not running" and the guard fails to fire.

    ``tests/conftest.py`` answers ``_check_container_running`` as "no runtime" for every
    test unless ``KANIBAKO_DOCKER_CMD`` names one, so the way to reach the real body is
    to name a CLI — that is the harness's own documented door.
    """
    from kanibako.utils import container_name_for

    asked: "list[str | None]" = []
    body = ["#!/bin/sh", 'case "$1" in', "  ps)"]
    for cname in running:
        body.append(f'    printf "%s\\timg:tag\\tUp 1 minute\\n" {shlex.quote(cname)}')
    body += ["    ;;", "  inspect)", "    exit 1", "    ;;",
             "  *)", "    exit 0", "    ;;", "esac"]
    stub = tmp_path / "fake-container-runtime.sh"
    stub.write_text("\n".join(body) + "\n")
    stub.chmod(0o755)
    monkeypatch.setenv("KANIBAKO_DOCKER_CMD", str(stub))

    real = P._check_container_running

    def _spy(proj, **kwargs):
        name = kwargs.get("container_name")
        if name is None and proj is not None:
            name = container_name_for(proj)
        asked.append(name)
        return real(proj, **kwargs)

    monkeypatch.setattr(P, "_check_container_running", _spy)
    return asked


def _retry_parked_by_name(parked_name):
    """Re-enter a parked box the way the registry actually lets you: by NAME.

    A primary/named member is addressed through its membership row, and pass 1
    drops that row when it parks.  `box purge <path>` therefore answers
    "No session data found" for a box whose anchor is sitting right there — see
    the r5 letter; that route is a separate decision.  `box rm <name> --purge`
    resolves the parked entry and is the route that finishes the job.
    """
    from kanibako.commands.box._parser import run_rm

    return run_rm(argparse.Namespace(target=parked_name, box=None, purge=True,
                                    force=True))


def _parked_for(std, identity):
    """The parked (name, entry) whose workspace or metadata names *identity*.

    ``_park`` records the box's OWN root/metadata, not the anchor subdirectory, so
    callers pass the identity the park call would have written.
    """
    needle = str(identity)
    for name, entry in dict(registry_store.load_deregistered(std.registry)).items():
        if needle in (str(entry.get("workspace", "")), str(entry.get("metadata", ""))):
            return name, entry
    return None


def _assert_kept_then_retry_finishes(std, config, target, anchor, identity, retry,
                                    st, capsys, cell):
    """Pass 1 keeps the anchor and parks; pass 2 finishes and drops the parked row.

    *retry* is a callable taking the parked name and returning the pass-2 rc.  It
    differs by mode because the routes differ: a standalone is re-found from disk, a
    primary/named member is re-found from its parked registry entry.
    """
    from kanibako.commands.clean import _purge_one

    capsys.readouterr()
    rc1 = _purge_one(std, config, target, force=True)
    first = capsys.readouterr()

    assert st["calls"], f"{cell}: the injected step was never reached — vacuous case"
    assert rc1 != 0, (
        f"{cell}: a purge that kept something must not report success: {first.out!r}")
    assert "done." not in first.out.lower(), f"{cell}: {first.out!r}"
    assert anchor.exists(), (
        f"{cell}: the anchor {anchor} has to survive a failed step, or the retry has "
        f"nothing to re-enter by")
    parked = _parked_for(std, identity)
    assert parked is not None, (
        f"{cell}: a purge that kept something parks the entry rather than dropping "
        f"the trail: {list(dict(registry_store.load_deregistered(std.registry)))}")
    parked_name = parked[0]

    st["fail"] = False
    capsys.readouterr()
    rc2 = retry(parked_name)
    second = capsys.readouterr()

    assert "No session data found" not in second.out, (
        f"{cell}: the retry must still see the box: {second.out!r}")
    assert rc2 == 0, (
        f"{cell}: the retry must finish: out={second.out!r} err={second.err!r}")
    assert not anchor.exists(), f"{cell}: the retry must take the anchor"
    # R3: `box list --all` reads the `deregistered` section.  A completed purge
    # that leaves the row there is claiming a retained box that no longer exists.
    assert _parked_for(std, identity) is None, (
        f"{cell}: R3 — a completed purge must drop the parked row, or `box list --all` "
        f"goes on listing a box under 'metadata retained' after `done.`")


# --------------------------------------------------------------------------- #
# R4 item 3 — the real "a later step failed" matrix, per mode.
# --------------------------------------------------------------------------- #
class TestStandaloneLaterStepFailure:
    """Standalone: the anchor is ``box_data/``, the detection marker itself."""

    @pytest.mark.parametrize("step", [
        "gitignore", "canon", "leftover_canon", "creds_lock", "vault", "snapshot_store",
    ])
    def test_a_standalone_later_step_failure_keeps_the_anchor(
        self, step, config_file, tmp_home, credentials_dir, capsys, monkeypatch,
    ):
        config = load_config(config_file)
        std = load_std_paths(config)
        root = tmp_home / "sa_matrix"
        root.mkdir()
        proj = resolve_standalone_project(std, config, project_dir=str(root),
                                         initialize=True)
        anchor = root / "box_data"
        assert anchor.is_dir(), "setup: a created standalone has a box_data/ anchor"

        if step == "gitignore":
            write_project_gitignore(root)
            assert project_gitignore_to_strip(root) is not None, \
                "setup: no kanibako gitignore line, so the step is unreachable"
            st = _fail_callable(monkeypatch,
                               "kanibako.commands.box._parser.strip_project_gitignore")
        elif step == "canon":
            st = _fail_one_path(monkeypatch, root / "canon")
        elif step == "leftover_canon":
            # Repoint the canon AFTER create so the default `root/canon` becomes a
            # leftover; otherwise `leftover_canon_rm` is None and the step never runs.
            # ⚑ Seed the LIVE mold first: the stamp is read from it, so with no mold the
            # leftover is never judged removable and the patched step is never reached.
            from kanibako.launch.templates import install_packaged_templates
            from kanibako.settings.config_io import write_nested_key
            install_packaged_templates(std, [])
            write_nested_key(root / "workset.yaml", ("workset",), "canon",
                            str(tmp_home / "sa_matrix_out_canon"))
            st = _fail_callable(monkeypatch,
                               "kanibako.project.workset.remove_default_canon_stamp")
        elif step == "creds_lock":
            lock = root / CREDS_WATCHER_LOCK_FILE
            lock.write_text("")
            st = _fail_unlink_of(monkeypatch, lock)
        elif step == "vault":
            st = _fail_one_path(monkeypatch, root / "vault" / "ro")
        elif step == "snapshot_store":
            store = box_snapshot_store(proj.vault_rw_path, proj.name)
            assert store is not None, "setup: no snapshot store to fail"
            store.mkdir(parents=True, exist_ok=True)  # the plan lists only a store on disk
            st = _fail_one_path(monkeypatch, store)
        else:  # pragma: no cover
            pytest.fail(f"unknown step {step!r}")

        def _retry_standalone(_name):
            from kanibako.commands.clean import _purge_one
            return _purge_one(std, config, str(root), force=True)

        _assert_kept_then_retry_finishes(
            std, config, str(root), anchor, root, _retry_standalone,
            st, capsys, f"standalone/{step}")


class TestPrimaryLaterStepFailure:
    """Primary: the anchor is the box's ``metadata_path`` under the primary boxes dir."""

    @pytest.mark.parametrize("step", ["vault_ro", "vault_rw", "snapshot_store"])
    @pytest.mark.parametrize("route", ["by_path", "by_name"])
    def test_a_primary_later_step_failure_keeps_the_anchor(
        self, step, route, config_file, tmp_home, credentials_dir, capsys, monkeypatch,
    ):
        config = load_config(config_file)
        std = load_std_paths(config)
        ws = tmp_home / "pr_matrix"
        ws.mkdir()
        proj = resolve_project(std, config, project_dir=str(ws), initialize=True)
        assert proj.mode is BoxMode.primary
        anchor = proj.metadata_path
        assert anchor.is_dir(), "setup: a created primary has a metadata dir"

        if step == "vault_ro":
            st = _fail_one_path(monkeypatch, proj.vault_ro_path)
        elif step == "vault_rw":
            st = _fail_one_path(monkeypatch, proj.vault_rw_path)
        elif step == "snapshot_store":
            store = box_snapshot_store(proj.vault_rw_path, proj.name)
            store.mkdir(parents=True, exist_ok=True)  # the plan lists only a store on disk
            st = _fail_one_path(monkeypatch, store)
        else:  # pragma: no cover
            pytest.fail(f"unknown step {step!r}")

        if route == "by_path":
            # ⚑ r6 bifrost #2: the promise "re-run the purge to finish" has to be
            # true at THIS door.  Pass 1 dropped the ``boxes:`` row, so pass 2 used to
            # resolve the path to ``boxes/__unregistered__`` and answer "No session
            # data found" at rc 0 with the kept anchor right there.
            def _retry(_parked_name):
                from kanibako.commands.clean import _purge_one
                return _purge_one(std, config, str(proj.project_path), force=True)
        else:
            _retry = _retry_parked_by_name

        _assert_kept_then_retry_finishes(
            std, config, str(proj.project_path), anchor, anchor,
            _retry, st, capsys, f"primary/{step}/{route}")


class TestNamedLaterStepFailure:
    """Named member: the anchor is the member's metadata dir; the steps are its OWN
    ro/rw vault leaves.  (A "member leaf" has no standalone or primary form — it is
    what a named member owns, so it is covered here rather than invented elsewhere.)
    """

    @pytest.mark.parametrize("step", ["member_ro_leaf", "member_rw_leaf"])
    def test_a_named_later_step_failure_keeps_the_anchor(
        self, step, config_file, tmp_home, credentials_dir, capsys, monkeypatch,
    ):
        """A named member re-enters a parked purge through the BY-PATH door.

        ⚑ The BY-NAME door is NOT exercised here: it is F2, which kanibako boarded
        separately in the r6 letter ("leave them").  ``_unregister_purged`` has no
        named branch, so a named member is parked as ``kind: "primary"`` and
        ``box rm <name> --purge`` applies ``must_be_under=std.boxes`` to a
        workset-local metadata path and refuses.  That defect is pinned on its own in
        :func:`test_a_parked_named_member_is_finished_by_the_by_name_door` so it cannot be
        lost, and un-marked when F2 lands.
        """
        from kanibako.project.workset import add_project, create_workset

        config = load_config(config_file)
        std = load_std_paths(config)
        ws = create_workset("r5-named-ws", tmp_home / "worksets" / "r5-named-ws", std)
        source = tmp_home / "r5_named_src"
        source.mkdir()
        add_project(ws, "namedone", source)
        proj = resolve_workset_project(WorksetSpec.from_workset(ws), "namedone",
                                      std, config, initialize=True)
        assert proj.mode is BoxMode.named
        anchor = proj.metadata_path
        assert anchor.is_dir(), "setup: a created named member has a metadata dir"

        victim = proj.vault_ro_path if step == "member_ro_leaf" else proj.vault_rw_path
        st = _fail_one_path(monkeypatch, victim)

        def _retry(_parked_name):
            from kanibako.commands.clean import _purge_one
            return _purge_one(std, config, str(proj.project_path), force=True)

        _assert_kept_then_retry_finishes(
            std, config, str(proj.project_path), anchor, anchor,
            _retry, st, capsys, f"named/{step}/by_path")


# --------------------------------------------------------------------------- #
# R4 item 1 — the stamp-bytes fix had NO test at all.
# --------------------------------------------------------------------------- #
class TestAStampFileWithBytesIsNotDeleted:
    """`remove_default_canon_stamp` must re-apply the planner's BYTE rule at the
    instant of `unlink`.  A stamp file the box wrote into is the user's folder."""

    def _a_stamp_file(self, std):
        """(rel path, shipped bytes) for one real stamp FILE in the canon stamp map.

        ⚑ The bytes come from the SAME live mold the stamp is derived from, so the setup
        cannot drift from what `create` actually lays.
        """
        from kanibako.launch.templates import workset_mold_canon_source
        from kanibako.project.workset import _create_canon_stamp

        src = workset_mold_canon_source(std)
        for rel, (is_dir, digest) in _create_canon_stamp(std).items():
            if not is_dir and digest:
                return rel, (src / rel).read_bytes()
        pytest.skip("no stamp FILE in the canon stamp map")  # pragma: no cover

    def test_a_stamp_file_holding_bytes_is_kept_and_the_walk_refuses(
        self, tmp_path, config_file,
    ):
        from kanibako.project.workset import remove_default_canon_stamp

        std = load_std_paths(load_config(config_file))
        # ⚑ Seed the LIVE mold the way `create` does; the stamp is read from it, so with no
        # mold seeded there is no stamp and this test would skip instead of testing.
        from kanibako.launch.templates import install_packaged_templates
        install_packaged_templates(std, [])
        rel, shipped = self._a_stamp_file(std)
        leaf = tmp_path / "canon"
        victim = leaf / rel
        victim.parent.mkdir(parents=True)
        # ⚑ THE REAL SHIPPED BYTES.  `create` lays a template file that is NOT empty, so
        # writing b"" here would satisfy the byte rule by accident on any mold whose file is
        # also empty, and the setup assert would pass for the wrong reason.
        assert shipped, "setup: the shipped stamp file is not empty"
        victim.write_bytes(shipped)
        assert remove_default_canon_stamp(leaf, std) is True, (
            "setup: a pure stamp must come away clean")
        assert not victim.exists()

        # Now the same walk over a stamp file that has been WRITTEN INTO: same path the
        # stamp names, different bytes, so only the digest can tell them apart.
        leaf.mkdir(parents=True, exist_ok=True)
        victim.parent.mkdir(parents=True, exist_ok=True)
        victim.write_bytes(b"the box wrote into this")
        assert remove_default_canon_stamp(leaf, std) is False, (
            "a stamp file whose bytes differ from the mold is the user's; the delete verb "
            "must refuse it")
        assert victim.exists(), "the bytes must survive — this is the data at stake"
        assert victim.read_bytes() == b"the box wrote into this"


# --------------------------------------------------------------------------- #
# R4 item 2 — clean.py's leftover-canon branch, BOTH halves, through clean.py.
# --------------------------------------------------------------------------- #
class TestCleanLeftoverCanonBranchBothHalves:
    """The r4 test called the *workset* helper and asserted only the filled half.
    These drive ``_purge_one`` and assert both."""

    def _repointed_standalone(self, config, std, tmp_home, name):
        from kanibako.launch.templates import install_packaged_templates
        from kanibako.settings.config_io import write_nested_key

        # ⚑ Seed the LIVE mold: the stamp is read from it, so with no mold seeded nothing
        # is recognized as create's and both halves of this pair pass for the wrong reason.
        install_packaged_templates(std, [])
        root = tmp_home / name
        root.mkdir()
        resolve_standalone_project(std, config, project_dir=str(root),
                                   initialize=True)
        write_nested_key(root / "workset.yaml", ("workset",), "canon",
                        str(tmp_home / f"{name}_out_canon"))
        return root

    def test_a_stamp_only_leftover_is_removed_by_the_purge(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        from kanibako.commands.clean import _purge_one

        config = load_config(config_file)
        std = load_std_paths(config)
        root = self._repointed_standalone(config, std, tmp_home, "lo_clean")
        leftover = root / "canon"
        assert leftover.is_dir(), "setup: the default canon is the leftover"
        capsys.readouterr()

        rc = _purge_one(std, config, str(root), force=True)
        out = capsys.readouterr()
        assert rc == 0, out.out + out.err
        assert not leftover.exists(), (
            "a leftover holding ONLY create's stamp is kanibako's; the purge takes it")

    def test_a_filled_leftover_is_retained_and_named_by_the_purge(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        from kanibako.commands.clean import _purge_one

        config = load_config(config_file)
        std = load_std_paths(config)
        root = self._repointed_standalone(config, std, tmp_home, "lo_kept")
        leftover = root / "canon"
        (leftover / "user.txt").write_text("mine")
        capsys.readouterr()

        _purge_one(std, config, str(root), force=True)
        out = capsys.readouterr()
        assert leftover.exists(), (
            "a leftover with user content must survive the purge — this is the "
            "half the r4 test never asserted")
        assert str(leftover) in (out.err + out.out), (
            "a kept folder has to be NAMED; an unnamed leftover is unrecoverable")


# --------------------------------------------------------------------------- #
# D1p — a parked PRIMARY whose container is still up.
# --------------------------------------------------------------------------- #
class TestAParkedPrimaryIsProbedByPathNotByName:
    """A parked name is out of the membership, so both resolvers read it as a
    relative path and miss.  ``proj`` came back None, the runtime was never asked,
    and the guard fell back to the lock file alone — and a detached start writes no
    lock.  Result: vaults and metadata deleted under a running container.

    ⚑ r6 V1: asking at all is not enough — the test pins WHICH container gets asked.
    The r5 version stubbed ``_check_container_running`` to answer "running" for ANY
    project, so it stayed green while the probe was rendering
    ``kb-standalone-<hash>`` for a box that runs as ``kb-primary-<name>``.
    """

    def _parked_primary(self, std, tmp_home, name="bP1"):
        md = std.boxes / name
        md.mkdir(parents=True)
        (md / "keep.txt").write_text("must survive")
        ws = tmp_home / f"{name}_ws"
        ws.mkdir()
        registry_store.register_deregistered(
            std.registry, name, kind="primary",
            workspace=str(ws), metadata=str(md), deregistered_at=_TS,
        )
        return md, ws

    def _parked_standalone(self, std, config, tmp_home, name="sa_parked"):
        """A real created standalone, then parked the way a failed pass 1 parks one.

        ⚑ A standalone's ``metadata_path`` IS its project ROOT (drift I), and
        ``_unregister_purged`` parks ``workspace == metadata == proj.metadata_path``.
        Parking the ``box_data/`` dir instead makes the parked entry name a directory
        two levels too deep, and the purge answers "No metadata directory found" for a
        box that is sitting right there.
        """
        root = tmp_home / name
        root.mkdir()
        proj = resolve_standalone_project(std, config, project_dir=str(root),
                                         initialize=True)
        assert proj.mode is BoxMode.standalone and proj.name, \
            "setup: a created standalone carries its live composed name"
        store = standalone_box_store(root, early=_early_scope(std, BoxMode.standalone))
        assert store.is_dir(), f"setup: a created standalone has a {store} anchor"
        registry_store.unregister_standalone(std.registry, proj.name)
        registry_store.register_deregistered(
            std.registry, proj.name, kind="standalone",
            workspace=str(proj.metadata_path), metadata=str(proj.metadata_path),
            deregistered_at=_TS,
        )
        return proj, root, store

    def test_a_parked_primary_is_asked_about_its_own_container(
        self, config_file, tmp_home, tmp_path, credentials_dir, capsys, monkeypatch,
    ):
        from kanibako.commands.box import _parser as P
        from kanibako.commands.box._parser import run_rm

        std, config = _reload(config_file)
        md, _ws = self._parked_primary(std, tmp_home)
        # NO lock on disk: a detached `start` writes none.  Only the runtime can tell.
        asked = _asked_container_names(monkeypatch, tmp_path, P, ["kb-primary-bP1"])
        prompted = []
        monkeypatch.setattr(P, "_confirm_purge",
                           lambda *a, **k: (prompted.append(True), False)[1])
        capsys.readouterr()

        rc = run_rm(argparse.Namespace(target="bP1", box=None, purge=True, force=False))
        err = capsys.readouterr().err

        assert "kb-primary-bP1" in asked, (
            f"V1: the runtime must be asked about the box's OWN container; it was "
            f"asked about {asked!r}")
        assert not [n for n in asked if n and n.startswith("kb-standalone-")], (
            f"V1: a parked PRIMARY must never be probed as a standalone — that was "
            f"the whole defect; asked {asked!r}")
        assert not prompted, (
            "D1p: the guard must fire BEFORE the delete list is shown; base went "
            "straight to the prompt")
        assert rc != 0, f"a live box must not be purged: {err}"
        assert "may be running" in err, err
        assert md.is_dir() and (md / "keep.txt").read_text() == "must survive"

    def test_a_parked_standalone_is_asked_about_its_own_container(
        self, config_file, tmp_home, tmp_path, credentials_dir, capsys, monkeypatch,
    ):
        """The other kind, pinned the same way: the standalone route asks its own name.

        A standalone's live name is carried by its own ``workset.yaml`` (kuid + leaf),
        so its resolver renders it — unlike a primary, whose name died with the
        membership row.  Pinning both kinds is what keeps a future "just use the
        resolver for everything" change from silently un-pinning the primary.
        """
        from kanibako.commands.box import _parser as P
        from kanibako.commands.box._parser import run_rm

        std, config = _reload(config_file)
        proj, _root, store = self._parked_standalone(std, config, tmp_home)
        live = f"kb-standalone-{proj.name}"
        asked = _asked_container_names(monkeypatch, tmp_path, P, [live])
        prompted = []
        monkeypatch.setattr(P, "_confirm_purge",
                           lambda *a, **k: (prompted.append(True), False)[1])
        capsys.readouterr()

        rc = run_rm(argparse.Namespace(target=proj.name, box=None, purge=True,
                                      force=False))
        err = capsys.readouterr().err

        assert live in asked, (
            f"V1: the standalone route must ask {live!r}; it asked {asked!r}")
        assert not [n for n in asked if n and n.startswith("kb-primary-")], (
            f"V1: a parked STANDALONE must not be probed as a primary; asked {asked!r}")
        assert not prompted and rc != 0, (
            f"a live box must not be purged: rc={rc} err={err!r}")
        assert "may be running" in err, err
        assert store.is_dir(), "the refused purge must leave the anchor alone"

    def test_an_unresolvable_target_is_never_read_as_not_running(
        self, config_file, tmp_home, credentials_dir, capsys, monkeypatch,
    ):
        """The general rule: a resolver that FAILS must never mean "not running"."""
        from kanibako.commands.box import _parser as P

        std, config = _reload(config_file)
        md = std.boxes / "ghost"
        md.mkdir(parents=True)
        monkeypatch.setattr(P, "_resolve_purge_probe_target",
                           lambda *a, **k: (_ for _ in ()).throw(RuntimeError("nope")))
        monkeypatch.setattr(P, "_check_container_running",
                           lambda proj, fail_closed=True, container_name=None:
                           (False, "down"))
        capsys.readouterr()

        refused = P._refuse_purge_while_live(std, config, "ghost", md, False,
                                           "Purging this box")
        err = capsys.readouterr().err
        assert refused is True, (
            "an unresolvable box with no lock file is SILENCE, not evidence that it "
            "is stopped")
        assert "does not resolve" in err, err

    def test_force_still_overrides_the_parked_primary_guard(
        self, config_file, tmp_home, tmp_path, credentials_dir, capsys, monkeypatch,
    ):
        from kanibako.commands.box import _parser as P
        from kanibako.commands.box._parser import run_rm

        std, config = _reload(config_file)
        md, _ws = self._parked_primary(std, tmp_home, name="bPforce")
        _asked_container_names(monkeypatch, tmp_path, P, ["kb-primary-bPforce"])
        capsys.readouterr()

        run_rm(argparse.Namespace(target="bPforce", box=None, purge=True, force=True))
        capsys.readouterr()
        assert not md.exists(), "--force must still be able to purge"


@pytest.mark.xfail(
    strict=False,
    reason="F2 — boarded SEPARATELY by kanibako (r6 letter: \"F2 … boarded separately; "
           "leave them\"), so it is pinned here rather than fixed.  "
           "``_unregister_purged`` has no NAMED branch, so a named member is parked as "
           "``kind: \"primary\"``; ``box rm <name> --purge`` then applies "
           "``must_be_under=std.boxes`` to a workset-local metadata path and refuses "
           "with \"not contained under …/primary_workset/boxes\".  The guard is right, "
           "the label is wrong.  This asserts what SHOULD happen, so it XFAILS today; "
           "when F2 lands it turns XPASS and should become a normal test.",
)
def test_a_parked_named_member_is_finished_by_the_by_name_door(
    config_file, tmp_home, credentials_dir, capsys, monkeypatch,
):
    """A named member parked by a failed purge should be finishable by NAME, as a
    primary is.  Reproduced against the real park, not asserted from the report."""
    from kanibako.project.workset import add_project, create_workset

    config = load_config(config_file)
    std = load_std_paths(config)
    ws = create_workset("r6-f2-ws", tmp_home / "worksets" / "r6-f2-ws", std)
    source = tmp_home / "r6_f2_src"
    source.mkdir()
    add_project(ws, "f2member", source)
    proj = resolve_workset_project(WorksetSpec.from_workset(ws), "f2member",
                                  std, config, initialize=True)
    assert proj.mode is BoxMode.named
    st = _fail_one_path(monkeypatch, proj.vault_ro_path)

    from kanibako.commands.clean import _purge_one

    capsys.readouterr()
    assert _purge_one(std, config, str(proj.project_path), force=True) != 0
    capsys.readouterr()
    assert st["calls"], "setup: the injected step was never reached"
    assert proj.metadata_path.is_dir(), "setup: pass 1 kept the anchor"

    st["fail"] = False
    rc = _retry_parked_by_name("f2member")
    out = capsys.readouterr()
    assert rc == 0, (
        f"F2: the by-name door must finish a parked named member; rc={rc} "
        f"err={out.err!r}")
    assert not proj.metadata_path.exists(), (
        "F2: the anchor is still on disk after a retry that claimed to finish")


def test_a_failed_purge_ends_its_stdout_line(
    config_file, tmp_home, credentials_dir, capsys, monkeypatch,
):
    """bifrost r6 #3 — a failed purge must not leave stdout ending mid-sentence.

    ``Removing session data... `` is written with ``end=""`` so the success path can
    finish it with ``done.``.  On a FAILED primary purge every later line goes to
    stderr, so stdout's last byte was a space: piped into a log or a pager the run ends
    without a terminator, and a ``tail -1`` reads a truncated line.
    """
    config = load_config(config_file)
    std = load_std_paths(config)
    ws = tmp_home / "nl_probe"
    ws.mkdir()
    proj = resolve_project(std, config, project_dir=str(ws), initialize=True)
    st = _fail_one_path(monkeypatch, proj.vault_ro_path)

    from kanibako.commands.clean import _purge_one

    capsys.readouterr()
    rc = _purge_one(std, config, str(proj.project_path), force=True)
    out = capsys.readouterr()

    assert st["calls"], "setup: the injected step was never reached"
    assert rc != 0, "setup: this must be the failing path"
    assert "Removing session data" in out.out, repr(out.out)
    assert out.out.endswith("\n"), (
        f"stdout ends mid-line: tail={out.out[-40:]!r}")


def _reload(config_file):
    config = load_config(config_file)
    return load_std_paths(config), config
