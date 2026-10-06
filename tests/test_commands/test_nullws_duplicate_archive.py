"""S3 ``<None>`` tail, round 2: ``box duplicate`` and ``box archive`` must read the
member's RECORDED workspace, not the resolved ``project_path``.

A named IN-TREE member under a null ``workset.workspaces`` resolves to ``project_path is
None`` while its files stay exactly where the registry row names them.  Both verbs guarded on
``project_path is not None and .is_dir()`` and so treated "nulled" as "nothing there":

* ``box duplicate`` skipped the workspace copy SILENTLY and registered an EMPTY box;
* ``box archive`` skipped its git safety checks and archived at rc 0 recording
  ``Project path: <None>`` — where base refused "Uncommitted changes detected".

The cure is the ONE accessor lifecycle already uses, so the three verbs cannot drift.
"""

from __future__ import annotations


def _std(config_file):
    from kanibako.settings.config import load_config
    from kanibako.settings.paths import load_std_paths

    return load_config(config_file), load_std_paths(load_config(config_file))


def _null_workspaces(root):
    """Set ``workset.workspaces: null`` in *root*'s workset.yaml, keeping what is there."""
    from kanibako.settings.config_io import dump_doc, load_doc

    path = root / "workset.yaml"
    doc = dict(load_doc(path)) if path.is_file() else {}
    doc["workset"] = {**doc.get("workset", {}), "workspaces": None}
    dump_doc(path, doc)
    return path


def _nulled_member(tmp_home, config_file):
    """A named workset with one IN-TREE member ``app``, then its ``workspaces`` nulled.

    Returns (config, std, ws_root, recorded_dir).  ``recorded_dir`` is where the member's
    files actually live and what the registry row names.
    """
    from kanibako.project.workset import add_project, create_workset

    config, std = _std(config_file)
    root = (tmp_home / "worksets" / "nullws").resolve()
    ws = create_workset("nullws", root, std)
    recorded = root / "workspaces" / "app"
    recorded.mkdir(parents=True, exist_ok=True)
    (recorded / "keepme.txt").write_text("payload\n")
    add_project(ws, "app", recorded, std)
    _null_workspaces(root)
    return config, std, root, recorded


class TestDuplicateReadsTheRecordedWorkspace:
    """D1: a nulled in-tree member still has files; duplicate must copy them, not register
    an empty box."""

    def test_duplicate_copies_the_recorded_files_not_an_empty_box(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        from kanibako.commands.box._duplicate import run_duplicate
        import argparse

        config, std, _root, recorded = _nulled_member(tmp_home, config_file)
        dest = (tmp_home / "dup-target").resolve()

        rc = run_duplicate(argparse.Namespace(
            source_path=str(recorded), new_path=str(dest),
            to_mode="standalone", bare=False, force=True,
        ))
        cap = capsys.readouterr()

        assert rc == 0, cap.out + cap.err
        # The payload must have come along.  An empty box is the defect.
        copied = list(dest.rglob("keepme.txt"))
        assert copied, (
            f"duplicate registered an EMPTY box — the recorded workspace "
            f"{recorded} was never copied. out={cap.out}\nerr={cap.err}"
        )
        assert copied[0].read_text() == "payload\n"

    def test_a_missing_recorded_workspace_refuses_before_the_prompt(
        self, config_file, tmp_home, credentials_dir, capsys, monkeypatch,
    ):
        import argparse
        import shutil

        from kanibako.commands.box import _duplicate

        config, std, _root, recorded = _nulled_member(tmp_home, config_file)
        shutil.rmtree(recorded)

        def _prompted(*_a, **_k):
            raise AssertionError("prompted before refusing")

        monkeypatch.setattr(_duplicate, "confirm_prompt", _prompted)
        # Called below ``run_duplicate``, whose own source-path check fires first.
        rc = _duplicate._duplicate_from_workset(
            argparse.Namespace(to_mode="standalone", bare=False, force=False),
            recorded, (tmp_home / "dup-target").resolve(), std, config,
        )
        cap = capsys.readouterr()

        assert rc == 1, cap.out + cap.err
        assert "does not exist; nothing to copy" in cap.err


class TestArchiveRunsGitChecksOnTheRecordedWorkspace:
    """D2: the git safety check must run against the recorded workspace, so uncommitted
    work still stops the archive."""

    def test_uncommitted_changes_in_the_recorded_workspace_refuse_the_archive(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        import subprocess

        config, std, _root, recorded = _nulled_member(tmp_home, config_file)

        # Make the recorded workspace a git repo with an UNCOMMITTED change.
        # ⚑ The dirty file must be one git already TRACKS: `check_uncommitted` runs
        # `git diff-index --quiet HEAD --`, which is blind to UNTRACKED files, so a
        # brand-new file would pass the check and prove nothing.
        subprocess.run(["git", "init", "-q"], cwd=recorded, check=True)
        subprocess.run(["git", "add", "-A"], cwd=recorded, check=True)
        subprocess.run(
            ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "seed"],
            cwd=recorded, check=True,
        )
        (recorded / "keepme.txt").write_text("uncommitted work\n")

        from kanibako.commands.archive import _archive_one
        from kanibako.settings.paths import resolve_any_project

        proj = resolve_any_project(std, config, project_dir=str(recorded), initialize=False)
        out_file = str(tmp_home / "arc.txz")

        rc = _archive_one(std, config, proj, output_file=out_file, args=_archive_args(out_file))
        cap = capsys.readouterr()

        assert rc == 1, (
            "archive SUCCEEDED despite uncommitted changes — the git safety check was "
            f"skipped because project_path is None. out={cap.out}\nerr={cap.err}"
        )
        assert "Uncommitted" in (cap.out + cap.err) or "uncommitted" in (cap.out + cap.err)


def _archive_args(out_file):
    import argparse

    return argparse.Namespace(
        file=out_file, allow_uncommitted=False, allow_unpushed=False,
        all_projects=False,
    )


class TestArchiveRoundTripRestoresTheRecordedWorkspace:
    """The archive records the member's RECORDED workspace, so ``extract --all`` finds it
    again instead of failing on a literal ``<None>`` path."""

    def test_archive_then_extract_all_restores_the_nulled_member(
        self, config_file, tmp_home, credentials_dir, capsys, monkeypatch,
    ):
        import argparse

        from kanibako.commands.archive import _archive_one
        from kanibako.commands.restore import run as extract_run
        from kanibako.settings.paths import resolve_any_project

        config, std, _root, recorded = _nulled_member(tmp_home, config_file)
        proj = resolve_any_project(std, config, project_dir=str(recorded), initialize=False)
        (proj.metadata_path / "mydata.txt").write_text("important")

        arc_dir = (tmp_home / "arcs").resolve()
        arc_dir.mkdir()
        out_file = str(arc_dir / "kanibako-app-x.txz")
        rc = _archive_one(std, config, proj, output_file=out_file, args=_archive_args(out_file))
        assert rc == 0, capsys.readouterr()

        (proj.metadata_path / "mydata.txt").unlink()
        monkeypatch.chdir(arc_dir)
        capsys.readouterr()
        rc = extract_run(argparse.Namespace(
            file=None, path=None, name=None, all_archives=True, force=True,
        ))
        cap = capsys.readouterr()

        assert rc == 0, cap.out + cap.err
        assert "<None>" not in cap.out + cap.err
        assert str(recorded) in cap.out
        assert (proj.metadata_path / "mydata.txt").read_text() == "important"

        # And by path: ``extract <file> <path>`` names the recorded workspace too.
        rc = extract_run(argparse.Namespace(
            file=out_file, path=str(recorded), name=None, all_archives=False, force=True,
        ))
        cap = capsys.readouterr()
        assert rc == 0, cap.out + cap.err
        assert f"Session data restored to {recorded}" in cap.out
