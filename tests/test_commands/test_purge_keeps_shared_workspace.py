"""A purge keeps another box's recorded workspace, and names the folder that holds it (S7).

Keyspec § 0 "Per-owner resources": a forced share's destructive verb removes only its own
instance's part.  Surgeon ``alpha`` records its workspace inside patient's store; purging the
patient keeps that whole containing folder and NAMES it (Q3 (A), the ``box_logs_to_purge``
precedent), and still removes the rest of the patient's part.

⚑ The real verbs, through the production parser, over a real tmp filesystem; nothing that
deletes is mocked.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from kanibako.settings.config import WORKSET_META_FILE, load_config
from kanibako.settings.paths import box_log_files, load_std_paths, resolve_project


def _door(*argv: str) -> int:
    """A real verb, through the production parser."""
    from kanibako import cli

    args = cli.build_parser().parse_args(list(argv))
    return int(args.func(args) or 0)


@pytest.fixture
def env(tmp_home, config_file, credentials_dir, monkeypatch):
    # ``create`` asks before it nests a box inside another's tree: answer yes.
    monkeypatch.setattr("builtins.input", lambda *_a, **_k: "yes")
    config = load_config(config_file)
    return config, load_std_paths(config)


def _surgeon(holder: Path, ws=None) -> Path:
    """Box ``alpha``, recording its workspace inside *holder*; returns that workspace.

    A primary box, or with *ws* a member of that workset.
    """
    workspace = holder / "x"
    if ws is None:
        assert _door("create", "--name", "alpha", str(workspace)) == 0
    else:
        # Neither create nor connect reaches a named store yet (S4c/Step 4), so the
        # forced share is staged in the registry by hand, as Step 4's --force will.
        workspace.mkdir(parents=True)
        registry = ws.root / "registry.yaml"
        doc = yaml.safe_load(registry.read_text())
        doc["boxes"]["alpha"] = str(workspace)
        registry.write_text(yaml.safe_dump(doc))
    (workspace / "alpha.txt").write_text("alpha's data\n")
    return workspace


def _standalone(env, tmp_home: Path):
    """``solo`` (registered standalone): ``(rm target, purge target, its store)``."""
    _config, std = env
    root = tmp_home / "solo"
    assert _door("create", "--standalone", "--register", str(root)) == 0
    from kanibako.project.registry_store import load_standalone

    (name,) = load_standalone(std.registry)
    return name, str(root), root / "box_data"


def _primary(env, tmp_home: Path):
    """Primary box ``p``: ``(rm target, purge target, its metadata dir)``."""
    config, std = env
    workspace = tmp_home / "p"
    assert _door("create", "--name", "p", str(workspace)) == 0
    proj = resolve_project(std, config, str(workspace), initialize=False)
    return "p", str(workspace), proj.metadata_path


def _named(env, tmp_home: Path):
    """Member ``m`` of workset ``w``: ``(rm target, purge target, its metadata dir)``."""
    config, std = env
    from kanibako.project.workset import add_project, create_workset

    ws = create_workset("w", tmp_home / "wroot", std)
    src = ws.workspaces_dir / "m"
    src.mkdir(parents=True)
    add_project(ws, "m", src, std)
    meta = ws.projects_dir / "m"
    meta.mkdir(parents=True, exist_ok=True)
    (meta / "home.txt").write_text("m's home\n")
    return "w/m", str(src), meta, ws


_PATIENTS = {"standalone": _standalone, "primary": _primary, "named": _named}


@pytest.mark.parametrize("verb", ["rm --purge", "purge"])
@pytest.mark.parametrize("mode", list(_PATIENTS))
def test_purge_keeps_and_names_a_workspace_recorded_inside_it(
        env, tmp_home, capsys, mode, verb):
    rm_target, purge_target, holder, *ws = _PATIENTS[mode](env, tmp_home)
    workspace = _surgeon(holder, *ws)
    capsys.readouterr()

    argv = (("box", "rm", rm_target, "--purge", "--force") if verb == "rm --purge"
            else ("box", "purge", purge_target, "--force"))
    _door(*argv)
    err = capsys.readouterr().err

    assert (workspace / "alpha.txt").read_text() == "alpha's data\n", (
        f"{verb} of the {mode} patient deleted box alpha's workspace")
    if mode == "named" and verb == "rm --purge":
        return  # `box rm` cannot address a named member yet (plan Step 2b): nothing ran
    assert f"Note: kept {holder}" in err
    assert "alpha' records" in err and str(workspace) in err


@pytest.mark.parametrize("verb", ["rm --purge", "purge"])
def test_the_rest_of_the_standalone_patient_still_goes(env, tmp_home, capsys, verb):
    rm_target, purge_target, store = _standalone(env, tmp_home)
    root = Path(purge_target)
    _surgeon(store)
    assert (root / "canon").is_dir() and (root / WORKSET_META_FILE).is_file()

    argv = (("box", "rm", rm_target, "--purge", "--force") if verb == "rm --purge"
            else ("box", "purge", purge_target, "--force"))
    assert _door(*argv) == 0

    assert not (root / "canon").exists(), "the patient's own canon still goes"
    assert not (root / "vault").exists(), "the patient's own vaults still go"
    assert not (root / WORKSET_META_FILE).exists(), "the patient's declarations still go"
    assert store.is_dir(), "the folder holding alpha's workspace is kept"


@pytest.mark.parametrize("verb", ["rm --purge", "purge"])
def test_the_rest_of_the_primary_patient_still_goes(env, tmp_home, capsys, verb):
    config, std = env
    rm_target, purge_target, meta = _primary(env, tmp_home)
    proj = resolve_project(std, config, purge_target, initialize=False)
    _surgeon(meta)
    vaults = [v for v in (proj.vault_ro_path, proj.vault_rw_path) if v is not None]
    assert vaults and all(v.is_dir() for v in vaults)

    argv = (("box", "rm", rm_target, "--purge", "--force") if verb == "rm --purge"
            else ("box", "purge", purge_target, "--force"))
    assert _door(*argv) == 0

    assert not any(v.exists() for v in vaults), "the patient's own vaults still go"
    assert meta.is_dir()


def test_box_purge_lists_what_it_will_delete_before_the_question(env, tmp_home, capsys):
    config, std = env
    _rm_target, purge_target, meta = _primary(env, tmp_home)
    proj = resolve_project(std, config, purge_target, initialize=False)
    capsys.readouterr()

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("builtins.input", lambda *_a, **_k: "no")
        rc = _door("box", "purge", purge_target)
    out = capsys.readouterr().out

    assert rc == 2
    assert f"box metadata: {meta}" in out
    assert f"vault rw (your files): {proj.vault_rw_path}" in out
    assert meta.is_dir(), "a declined purge deletes nothing"


@pytest.mark.parametrize("verb", ["rm --purge", "purge"])
def test_a_kept_store_keeps_the_vault_on_both_verbs(env, tmp_home, capsys, verb):
    """A retained store leaves the box whole, vault included, whichever verb purges."""
    rm_target, purge_target, store = _standalone(env, tmp_home)
    root = Path(purge_target)
    # A repointed ``workset.boxes`` the box did not create: kanibako keeps that store.
    elsewhere = root.parent / "elsewhere"
    store.rename(elsewhere)
    store.symlink_to(elsewhere)
    vault = root / "vault"
    assert vault.is_dir()

    argv = (("box", "rm", rm_target, "--purge", "--force") if verb == "rm --purge"
            else ("box", "purge", purge_target, "--force"))
    _door(*argv)

    assert vault.is_dir(), f"{verb} deleted the vault of a box whose store it keeps"


def test_box_purge_all_counts_a_failure_and_keeps_the_metadata(
        env, tmp_home, monkeypatch, capsys):
    config, std = env
    _rm_target, purge_target, meta = _primary(env, tmp_home)
    proj = resolve_project(std, config, purge_target, initialize=False)
    logs = box_log_files(std.primary_logs, proj.name)
    for log in logs:
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("x\n")
    real = Path.unlink

    def unlink(self, *args, **kwargs):
        if self == logs.helper:
            raise PermissionError(13, "Permission denied", str(self))
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", unlink)

    rc = _door("box", "purge", "--all", "--force")
    captured = capsys.readouterr()

    assert rc == 1, "a log left behind is a failed purge"
    assert meta.is_dir(), "the anchor stays so a re-run can finish"
    assert "Purged session data for 0 project(s)" in captured.out
    assert "1 could not be fully purged" in captured.err


def test_purge_all_keeps_a_workspace_recorded_inside_a_box(env, tmp_home, capsys):
    _rm_target, _purge_target, meta = _primary(env, tmp_home)
    workspace = _surgeon(meta)

    _door("box", "purge", "--all", "--force")

    assert (workspace / "alpha.txt").is_file()
    assert f"Note: kept {meta}" in capsys.readouterr().err


def test_recorded_workspaces_under_covers_every_registry_literal_and_resolved(
        env, tmp_home):
    _config, std = env
    from kanibako.launch.box_resolve import recorded_workspaces_under

    tree = tmp_home / "tree"
    tree.mkdir()
    link = tmp_home / "link"
    link.symlink_to(tree)
    assert _door("create", "--name", "prim", str(tree / "a")) == 0
    assert _door("create", "--standalone", "--register", str(tree / "s")) == 0
    from kanibako.project.workset import add_project, create_workset

    ws = create_workset("w", tmp_home / "wroot", std)
    add_project(ws, "mem", tree / "m", std)
    assert _door("create", "--name", "vialink", str(link / "b")) == 0
    assert _door("create", "--name", "outside", str(tmp_home / "out")) == 0

    owners = {owner for owner, _path in recorded_workspaces_under(std, tree)}

    assert {"prim", "w/mem", "vialink"} <= owners
    assert any(o.endswith("_s") for o in owners), owners
    assert "outside" not in owners
    assert recorded_workspaces_under(std, tmp_home / "nothing-here") == []


@pytest.mark.parametrize("where", ["store", "workspace leaf"])
def test_disconnect_remove_files_keeps_a_workspace_recorded_inside_the_member(
        env, tmp_home, capsys, where):
    """``workset disconnect --remove-files`` deletes the member's store and its OWN
    workspace leaf; a folder holding ANOTHER box's recorded workspace is kept and named."""
    _rm_target, src, meta, ws = _named(env, tmp_home)
    holder = meta if where == "store" else Path(src)
    workspace = _surgeon(holder, ws)
    capsys.readouterr()

    rc = _door("workset", "disconnect", "w", "m", "--remove-files", "--force")
    err = capsys.readouterr().err

    assert rc == 0
    assert (workspace / "alpha.txt").is_file(), f"disconnect deleted alpha inside the {where}"
    assert f"Note: kept {holder}" in err and "'w/alpha' records" in err
    if where == "store":
        assert not Path(src).exists(), "the member's own workspace leaf still goes"
    else:
        assert not meta.exists(), "the member's own store still goes"


def _listed(out: str) -> list[str]:
    """The delete list a purge printed before its question."""
    lines = out.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("--purge deletes"))
    return [line for line in lines[start:] if line.startswith("  ")]


@pytest.mark.parametrize("mode", ["standalone", "primary"])
def test_box_purge_lists_exactly_what_rm_purge_lists(env, tmp_home, capsys, mode):
    """One plan for both verbs: a vault leaf the user deleted is listed by neither."""
    rm_target, purge_target, _holder = _PATIENTS[mode](env, tmp_home)
    config, std = env
    proj = resolve_project(std, config, purge_target, initialize=False) \
        if mode == "primary" else None
    gone = (Path(purge_target) / "vault" / "ro") if proj is None else proj.vault_ro_path
    import shutil

    shutil.rmtree(gone)
    capsys.readouterr()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("builtins.input", lambda *_a, **_k: "no")
        assert _door("box", "purge", purge_target) == 2
        purge_list = _listed(capsys.readouterr().out)
        assert _door("box", "rm", rm_target, "--purge") == 2
        rm_list = _listed(capsys.readouterr().out)

    assert purge_list == rm_list
    assert not any(str(gone) in line for line in purge_list)


def test_workset_rm_purge_keeps_a_workspace_recorded_inside_its_root(env, tmp_home, capsys):
    """``workset rm --purge`` deletes its own members, never another box's workspace."""
    _config, std = env
    from kanibako.project.workset import create_workset

    ws = create_workset("w", tmp_home / "wroot", std)
    assert _door("create", "--name", "alpha", str(tmp_home / "elsewhere")) == 0
    # create refuses a primary box in w's path space, so the record is staged by hand.
    workspace = ws.root / "x"
    workspace.mkdir()
    (workspace / "alpha.txt").write_text("alpha's data\n")
    registry = std.primary_workset / "registry.yaml"
    doc = yaml.safe_load(registry.read_text())
    doc["boxes"]["alpha"] = str(workspace)
    registry.write_text(yaml.safe_dump(doc))
    capsys.readouterr()

    rc = _door("workset", "rm", "w", "--purge", "--force")
    err = capsys.readouterr().err

    assert rc == 0
    assert (workspace / "alpha.txt").is_file(), "workset rm --purge deleted alpha's workspace"
    assert f"Note: kept {ws.root}" in err and "box 'alpha' records" in err


@pytest.mark.skipif(os.geteuid() == 0, reason="root unlinks in a 0555 directory anyway")
def test_purge_all_counts_a_vault_link_it_cannot_unlink(env, tmp_home, capsys):
    """A symlinked vault leaf in a read-only parent: a counted failure, no traceback."""
    config, std = env
    _rm_target, purge_target, meta = _primary(env, tmp_home)
    proj = resolve_project(std, config, purge_target, initialize=False)
    leaf = proj.vault_ro_path
    import shutil

    shutil.rmtree(leaf)
    target = tmp_home / "vault-target"
    target.mkdir()
    leaf.symlink_to(target)
    leaf.parent.chmod(0o555)
    try:
        rc = _door("box", "purge", "--all", "--force")
    finally:
        leaf.parent.chmod(0o755)
    captured = capsys.readouterr()

    assert rc == 1
    assert "Purge incomplete" in captured.err
    assert leaf.is_symlink() and meta.is_dir(), "the anchor stays so a re-run can finish"
