"""Direct unit tests for the lifecycle engine (commands/box/_lifecycle.py).

These exercise the shared transactional routine that backs the redesigned
remap / move / convert commands (Phase 1 — no CLI wiring yet).
"""

from __future__ import annotations


import pytest

from kanibako.commands.box import _lifecycle as lc
from kanibako.commands.box._lifecycle import (
    BARE_INTO_WS,
    INPLACE,
    UNCHANGED,
    TargetSpec,
    execute_lifecycle,
    resolve_lifecycle_target,
)
from kanibako.settings.config import load_config
from kanibako.settings.config_io import load_doc
from kanibako.errors import ProjectError, WorksetError
from kanibako.settings.paths import load_primary_boxes
from kanibako.settings.paths import (
    BoxMode,
    detect_project_mode,
    load_std_paths,
    resolve_project,
    resolve_standalone_project,
)
from kanibako.project.workset import (
    add_project,
    create_workset,
    load_workset,
)


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _connected_index(std):
    """Reconstruct the ``{external_path: {workset, project}}`` connection view.

    D10 replacement for the retired global ``connected:`` index: a connected box
    is a NAMED workset's per-workset ``boxes:`` entry whose path is EXTERNAL
    (outside that workset root).  Mirrors the old ``_load_connected`` shape.
    """
    from pathlib import Path

    from kanibako.project import registry_store, workset_registry
    from kanibako.settings.config_io import load_doc

    out = {}
    for name, root_str in registry_store.load_section(
        std.registry, "worksets"
    ).items():
        root = Path(root_str)
        registry_path = workset_registry.resolve_workset_registry_path(
            root, load_doc(root / "workset.yaml"),
        )
        for box_name, box_path in workset_registry.load_workset_boxes(
            registry_path
        ).items():
            resolved = Path(box_path).resolve()
            try:
                resolved.relative_to(root.resolve())
                continue
            except ValueError:
                out[str(resolved)] = {"workset": name, "project": box_name}
    return out

@pytest.fixture
def env(config_file, tmp_home, credentials_dir):
    """Loaded config + std + temp home."""
    config = load_config(config_file)
    std = load_std_paths(config)
    return config, std, tmp_home


def _make_default(env, name="proj", contents="hello"):
    config, std, tmp_home = env
    project_dir = tmp_home / name
    project_dir.mkdir()
    (project_dir / "file.txt").write_text(contents)
    resolve_project(std, config, project_dir=str(project_dir), initialize=True)
    return project_dir


def _make_standalone(env, name="sa", contents="hi"):
    config, std, tmp_home = env
    project_dir = tmp_home / name
    project_dir.mkdir()
    (project_dir / "file.txt").write_text(contents)
    resolve_standalone_project(
        std, config, project_dir=str(project_dir), initialize=True,
    )
    return project_dir


def _make_workset(env, ws_name="ws", root_name="ws_root"):
    config, std, tmp_home = env
    return create_workset(ws_name, tmp_home / root_name, std)


def _conf_yes():
    return lambda: True


# ---------------------------------------------------------------------------
# resolve_lifecycle_target
# ---------------------------------------------------------------------------

class TestResolveTarget:
    def test_default(self, env):
        config, std, tmp_home = env
        pdir = _make_default(env)
        state = resolve_lifecycle_target(str(pdir), std, config)
        assert state.owner == "primary"
        assert state.mode == BoxMode.primary
        assert state.workspace_path == pdir.resolve()
        assert state.metadata_path.is_dir()
        assert not state.is_external
        assert state.ws is None

    def test_standalone(self, env):
        config, std, tmp_home = env
        pdir = _make_standalone(env)
        state = resolve_lifecycle_target(str(pdir), std, config)
        assert state.owner == "standalone"
        assert state.mode == BoxMode.standalone
        # Drift I: metadata (workset.yaml) is at the ROOT.
        assert state.metadata_path == pdir.resolve()
        assert not state.is_external

    def test_workset_internal(self, env):
        config, std, tmp_home = env
        ws = _make_workset(env)
        # internal source: a path inside the workset workspaces dir.
        internal_src = ws.workspaces_dir / "wp"
        internal_src.mkdir(parents=True)
        add_project(ws, "wp", internal_src, std)
        state = resolve_lifecycle_target(str(internal_src), std, config)
        assert state.owner == "workset:ws"
        assert state.mode == BoxMode.named
        assert not state.is_external
        assert state.ws is not None and state.ws.name == "ws"

    def test_workset_external_connected(self, env):
        config, std, tmp_home = env
        ws = _make_workset(env)
        external = tmp_home / "external_repo"
        external.mkdir()
        add_project(ws, "ext", external, std)
        state = resolve_lifecycle_target(str(external), std, config)
        assert state.owner == "workset:ws"
        assert state.is_external
        assert state.workspace_path == external.resolve()

    def test_bare_project_name(self, env):
        """A bare REGISTERED project name resolves (cwd is elsewhere)."""
        config, std, tmp_home = env
        pdir = _make_default(env, name="namedproj")
        # cwd is tmp_path/project (set by tmp_home), not pdir; resolve by name.
        state = resolve_lifecycle_target("namedproj", std, config)
        assert state.owner == "primary"
        assert state.workspace_path == pdir.resolve()

    def test_bare_workset_name_errors(self, env):
        """A bare workset name is rejected (a workset is not a single box)."""
        config, std, tmp_home = env
        _make_workset(env, ws_name="lcw", root_name="lcw_root")
        with pytest.raises(WorksetError, match="is a workset"):
            resolve_lifecycle_target("lcw", std, config)

    def test_qualified_workset_project(self, env):
        """A qualified ``workset/project`` token resolves to that project box."""
        config, std, tmp_home = env
        ws = _make_workset(env, ws_name="qw", root_name="qw_root")
        internal_src = ws.workspaces_dir / "api"
        internal_src.mkdir(parents=True)
        add_project(ws, "api", internal_src, std)
        # cwd is elsewhere; address the project by its qualified name.
        state = resolve_lifecycle_target("qw/api", std, config)
        assert state.owner == "workset:qw"
        assert state.mode == BoxMode.named
        assert state.workspace_path == internal_src.resolve()

    def test_qualified_unknown_project_errors(self, env):
        """``workset/project`` for a missing project surfaces a ProjectError.

        The qualified resolver raises ProjectError (caught internally), so the
        token path-ifies and detection fails -- never a silent wrong path.
        """
        config, std, tmp_home = env
        _make_workset(env, ws_name="qw2", root_name="qw2_root")
        with pytest.raises((ProjectError, WorksetError)):
            resolve_lifecycle_target("qw2/nope", std, config)

    def test_slash_token_not_qualified_unchanged(self, env):
        """A slash token that isn't a qualified name behaves as before.

        A nonexistent relative path falls through to path-ify and fails at
        detection -- the qualified branch must not change this.
        """
        config, std, tmp_home = env
        with pytest.raises((ProjectError, WorksetError)):
            resolve_lifecycle_target("no/such/path", std, config)


# ---------------------------------------------------------------------------
# In-place convert each direction
# ---------------------------------------------------------------------------

class TestConvertInPlace:
    def test_default_to_standalone(self, env):
        config, std, tmp_home = env
        pdir = _make_default(env)
        state = resolve_lifecycle_target(str(pdir), std, config)
        new = execute_lifecycle(
            state, TargetSpec(location=INPLACE, ownership="standalone"),
            std, config, confirm=_conf_yes(),
        )
        assert new.mode == BoxMode.standalone
        # Drift I: workset.yaml at the ROOT; box_data/ is the marker dir.
        assert (pdir / "workset.yaml").is_file()
        assert (pdir / "box_data").is_dir()
        assert not (pdir / "box_data" / "box.yaml").exists()
        # P8b/Option A: no on-disk ``project:`` identity — the marker workset.yaml
        # exists (materialized by the sparse kuid write) but carries no
        # ``project:`` section; the standalone identity lives in
        # registry.standalone + new.name.
        from kanibako.settings.config import read_workset_kuid
        from kanibako.kuid import SENTINEL
        from kanibako.project.registry_store import load_standalone
        assert "project" not in load_doc(pdir / "workset.yaml")
        assert read_workset_kuid(pdir / "workset.yaml") != SENTINEL
        assert new.mode == BoxMode.standalone
        assert load_standalone(std.registry).get(new.name) == str(pdir)
        # default-mode name unregistered.
        assert str(pdir) not in load_primary_boxes(std.primary_workset).values()

    def test_convert_to_standalone_is_detectable(self, env):
        config, std, tmp_home = env
        pdir = _make_default(env)
        state = resolve_lifecycle_target(str(pdir), std, config)
        execute_lifecycle(
            state, TargetSpec(location=INPLACE, ownership="standalone"),
            std, config, confirm=_conf_yes(),
        )
        # Canonical box_data/ marker dir + root workset.yaml (drift I), not
        # legacy .kanibako/.
        assert (pdir / "workset.yaml").is_file()
        assert (pdir / "box_data").is_dir()
        assert not (pdir / ".kanibako").exists()
        # Detection recognizes the converted box.
        result = detect_project_mode(pdir, std, config)
        assert result.mode is BoxMode.standalone

    def test_convert_primary_to_standalone_registers_canonical(self, env):
        """BUG#4: converting a PRIMARY box --standalone must ESTABLISH the box
        uniformly with create/duplicate — detected as standalone, REGISTERED in
        registry.standalone with a fresh canonical <kuid>_<leaf> identity,
        and the OLD primary names.yaml entry must be gone (no dangle)."""
        from kanibako.project.registry_store import load_standalone

        config, std, tmp_home = env
        pdir = _make_default(env)
        src_state = resolve_lifecycle_target(str(pdir), std, config)
        src_name = src_state.name
        # The primary source is registered in names.yaml at the project path.
        assert str(pdir) in load_primary_boxes(std.primary_workset).values()

        new = execute_lifecycle(
            src_state, TargetSpec(location=INPLACE, ownership="standalone"),
            std, config, confirm=_conf_yes(),
        )

        # (1) Result is a standalone box.
        assert new.mode is BoxMode.standalone
        result = detect_project_mode(pdir, std, config)
        assert result.mode is BoxMode.standalone

        # (2) P8b/Option A: no on-disk ``project:`` identity — the name is a fresh
        #     canonical <kuid>_<leaf> in new.name + registry.standalone (below),
        #     NOT the source's primary name.  The marker workset.yaml is sparse.
        assert "project" not in load_doc(pdir / "workset.yaml")
        new_name = new.name
        assert new_name != src_name
        prefix, _, leaf = new_name.partition("_")
        assert len(prefix) == 5  # <kuid> Crockford base32 prefix
        assert leaf == "proj"

        # (3) Registered in registry.standalone keyed by the canonical name.
        standalone = load_standalone(std.registry)
        assert new_name in standalone
        assert standalone[new_name] == str(pdir)

        # (4) The old primary names.yaml entry is gone (no dangling registration).
        assert str(pdir) not in load_primary_boxes(std.primary_workset).values()
        assert src_name not in load_primary_boxes(std.primary_workset)

    def test_convert_standalone_no_name_generates_fresh(self, env):
        """No --name on a standalone convert → a freshly generated canonical id
        (leaf from the root basename), NOT the source's name (R1/R3 branch 1)."""
        config, std, tmp_home = env
        pdir = _make_default(env)
        src_state = resolve_lifecycle_target(str(pdir), std, config)
        src_name = src_state.name
        new = execute_lifecycle(
            src_state, TargetSpec(location=INPLACE, ownership="standalone"),
            std, config, confirm=_conf_yes(),
        )
        assert new.mode is BoxMode.standalone
        assert new.name != src_name
        prefix, _, leaf = new.name.partition("_")
        assert len(prefix) == 5
        assert leaf == "proj"

    def test_convert_standalone_honors_canonical_name(self, env):
        """A free, well-formed canonical --name is honored verbatim (no forced
        rename — the OLD BUG#4 behavior is replaced) (R1/R3 match+free)."""
        from kanibako.project.registry_store import load_standalone

        config, std, tmp_home = env
        pdir = _make_default(env)
        src_state = resolve_lifecycle_target(str(pdir), std, config)
        new = execute_lifecycle(
            src_state,
            TargetSpec(location=INPLACE, ownership="standalone", name="abcde_proj"),
            std, config, confirm=_conf_yes(),
        )
        assert new.mode is BoxMode.standalone
        assert new.name == "abcde_proj"
        # P8b/Option A: the honored name lives in registry.standalone + new.name,
        # not an on-disk ``project:`` section (no ``project:`` on disk).
        assert "project" not in load_doc(pdir / "workset.yaml")
        standalone = load_standalone(std.registry)
        assert standalone["abcde_proj"] == str(pdir)

    def test_convert_standalone_noncanonical_name_becomes_leaf(self, env):
        """A non-canonical --name becomes the leaf with a FRESH random prefix
        (lowercased + sanitized) (R1/R3 no-match)."""
        config, std, tmp_home = env
        pdir = _make_default(env)
        src_state = resolve_lifecycle_target(str(pdir), std, config)
        new = execute_lifecycle(
            src_state,
            TargetSpec(location=INPLACE, ownership="standalone", name="MyBox"),
            std, config, confirm=_conf_yes(),
        )
        assert new.mode is BoxMode.standalone
        prefix, sep, leaf = new.name.partition("_")
        assert sep == "_"
        assert len(prefix) == 5
        assert leaf == "mybox"  # lowercased

    def test_convert_standalone_taken_canonical_name_refuses(self, env):
        """A canonical --name that collides with an existing standalone box is
        refused (R1/R3 match+taken)."""
        config, std, tmp_home = env
        # Establish an existing standalone box and learn its real name.
        existing_dir = _make_standalone(env, name="existing")
        existing_state = resolve_lifecycle_target(str(existing_dir), std, config)
        taken = existing_state.name
        assert "_" in taken

        pdir = _make_default(env)
        src_state = resolve_lifecycle_target(str(pdir), std, config)
        with pytest.raises(ProjectError, match="already a box with that name"):
            execute_lifecycle(
                src_state,
                TargetSpec(location=INPLACE, ownership="standalone", name=taken),
                std, config, confirm=_conf_yes(),
            )

    def test_standalone_to_default(self, env):
        config, std, tmp_home = env
        pdir = _make_standalone(env)
        state = resolve_lifecycle_target(str(pdir), std, config)
        new = execute_lifecycle(
            state, TargetSpec(ownership="default"), std, config, confirm=_conf_yes(),
        )
        assert new.mode == BoxMode.primary
        assert new.metadata_path.is_dir()
        assert new.metadata_path.parent == std.boxes
        # P8b/Option A: a primary box no longer self-describes on disk — identity
        # is the names.yaml registration (asserted below); no ``project:`` on disk.
        assert "project" not in load_doc(new.metadata_path / "box.yaml")
        # old in-tree metadata gone.
        assert not (pdir / "box_data").exists()
        # name registered.
        assert str(pdir) in load_primary_boxes(std.primary_workset).values()

    def test_default_to_workset_external(self, env):
        config, std, tmp_home = env
        ws = _make_workset(env)
        pdir = _make_default(env)
        state = resolve_lifecycle_target(str(pdir), std, config)
        new = execute_lifecycle(
            state, TargetSpec(ownership="ws"), std, config, confirm=_conf_yes(),
        )
        assert new.mode == BoxMode.named
        assert new.is_external  # in-place → workspace stays outside ws → external
        # workspace still where it was.
        assert pdir.is_dir() and (pdir / "file.txt").is_file()
        # workset registration + external markers.
        ws2 = load_workset(ws.root, ws.name)
        assert any(p.name == "proj" for p in ws2.projects)
        # P8b/Option A: mode + external workspace live in the returned state and
        # the workset's per-workset ``boxes:`` registry, not an on-disk section.
        assert new.mode == BoxMode.named
        assert new.workspace_path == pdir.resolve()
        from kanibako.project import workset_registry
        from kanibako.settings.config_io import load_doc
        reg = workset_registry.load_workset_boxes(
            workset_registry.resolve_workset_registry_path(
                ws.root, load_doc(ws.root / "workset.yaml"),
            )
        )
        assert reg.get("proj") == str(pdir.resolve())
        # old default name unregistered.
        assert str(pdir) not in load_primary_boxes(std.primary_workset).values()

    def test_standalone_to_workset_external(self, env):
        config, std, tmp_home = env
        _make_workset(env)
        pdir = _make_standalone(env)
        state = resolve_lifecycle_target(str(pdir), std, config)
        new = execute_lifecycle(
            state, TargetSpec(ownership="ws"), std, config, confirm=_conf_yes(),
        )
        assert new.mode == BoxMode.named
        assert new.is_external
        assert pdir.is_dir()
        assert not (pdir / "box_data").exists()

    def test_default_inplace_different_name_refuses(self, env):
        """Fix-2: an in-place default convert with a DIFFERENT --name is REFUSED.

        This test PREVIOUSLY passed ``name="proj2"`` and asserted the box silently
        reused "proj" (dropping the different name) — the old-wrong behavior Fix-2
        removes.  An in-place rename of a primary (default-mode) box is not
        supported, so the engine now raises ``ProjectError`` and the registry is
        left unchanged (name -> path preserved, no stray ``proj2``).
        """
        config, std, tmp_home = env
        pdir = _make_default(env)  # registers "proj" → pdir
        state = resolve_lifecycle_target(str(pdir), std, config)
        with pytest.raises(ProjectError, match="rename of a primary"):
            execute_lifecycle(
                state,
                TargetSpec(location=INPLACE, ownership="default", name="proj2"),
                std, config, confirm=_conf_yes(),
            )
        projects = load_primary_boxes(std.primary_workset)
        # Registry unchanged: original name still maps to the path, no new entry.
        assert projects.get("proj") == str(pdir)
        assert "proj2" not in projects
        assert sum(1 for v in projects.values() if v == str(pdir)) == 1

    def test_workset_to_default(self, env):
        config, std, tmp_home = env
        ws = _make_workset(env)
        external = tmp_home / "ext_repo"
        external.mkdir()
        (external / "file.txt").write_text("data")
        add_project(ws, "ep", external, std)
        state = resolve_lifecycle_target(str(external), std, config)
        new = execute_lifecycle(
            state, TargetSpec(ownership="default"), std, config, confirm=_conf_yes(),
        )
        assert new.mode == BoxMode.primary
        # external dir preserved (NEVER deleted).
        assert external.is_dir() and (external / "file.txt").is_file()
        # workset registration removed.
        ws2 = load_workset(ws.root, ws.name)
        assert not any(p.name == "ep" for p in ws2.projects)
        # connected.yaml cleared.
        assert str(external.resolve()) not in _connected_index(std)

    def test_workset_to_standalone(self, env):
        config, std, tmp_home = env
        ws = _make_workset(env)
        external = tmp_home / "ext_sa"
        external.mkdir()
        add_project(ws, "es", external, std)
        state = resolve_lifecycle_target(str(external), std, config)
        new = execute_lifecycle(
            state, TargetSpec(ownership="standalone"), std, config, confirm=_conf_yes(),
        )
        assert new.mode == BoxMode.standalone
        assert external.is_dir()
        # Drift I: workset.yaml at the ROOT; drift H: the external dir becomes
        # the standalone root and its files move into the workspace/ subdir.
        assert (external / "workset.yaml").is_file()
        assert (external / "box_data").is_dir()
        assert (external / "workspace").is_dir()


# ---------------------------------------------------------------------------
# workset -> workset re-root
# ---------------------------------------------------------------------------

class TestWorksetToWorkset:
    def test_external_reroot_inplace(self, env):
        """External ws->ws repoints redirect/override; no file move needed."""
        config, std, tmp_home = env
        ws_a = create_workset("wsa", tmp_home / "wsa_root", std)
        create_workset("wsb", tmp_home / "wsb_root", std)
        external = tmp_home / "ext_reroot"
        external.mkdir()
        (external / "f.txt").write_text("x")
        add_project(ws_a, "p", external, std)
        state = resolve_lifecycle_target(str(external), std, config)
        new = execute_lifecycle(
            state, TargetSpec(location=INPLACE, ownership="wsb"),
            std, config, confirm=_conf_yes(),
        )
        assert new.owner == "workset:wsb"
        assert new.is_external
        assert external.is_dir()
        # wsa no longer owns it; wsb does.
        assert not any(p.name == "p" for p in load_workset(ws_a.root, ws_a.name).projects)
        assert any(p.name == "p" for p in load_workset(tmp_home / "wsb_root", "wsb").projects)
        # connected.yaml points at wsb now.
        entry = _connected_index(std)[str(external.resolve())]
        assert entry["workset"] == "wsb"

    def test_internal_inplace_refused(self, env):
        """Internal-workspace ws->ws in-place raises the stubborn error."""
        config, std, tmp_home = env
        ws_a = create_workset("ia", tmp_home / "ia_root", std)
        create_workset("ib", tmp_home / "ib_root", std)
        internal = ws_a.workspaces_dir / "wp"
        internal.mkdir(parents=True)
        add_project(ws_a, "wp", internal, std)
        state = resolve_lifecycle_target(str(internal), std, config)
        with pytest.raises(ProjectError, match="Stubbornly refusing"):
            execute_lifecycle(
                state, TargetSpec(location=INPLACE, ownership="ib"),
                std, config, confirm=_conf_yes(),
            )


# ---------------------------------------------------------------------------
# move within same owner
# ---------------------------------------------------------------------------

class TestMoveSameOwner:
    def test_move_default(self, env):
        config, std, tmp_home = env
        pdir = _make_default(env, contents="movecontent")
        state = resolve_lifecycle_target(str(pdir), std, config)
        dest = tmp_home / "newhome"
        new = execute_lifecycle(
            state, TargetSpec(location=dest, ownership=UNCHANGED),
            std, config, confirm=_conf_yes(),
        )
        assert new.mode == BoxMode.primary
        assert dest.is_dir()
        assert (dest / "file.txt").read_text() == "movecontent"
        assert not pdir.exists()
        # P8b/Option A: the moved box's workspace is the names.yaml registration
        # (updated below), not an on-disk ``resolved.workspace``.  The returned
        # state carries the new location; no ``project:`` on disk.
        assert "project" not in load_doc(new.metadata_path / "box.yaml")
        assert new.workspace_path == dest.resolve()
        # names.yaml updated.
        assert str(dest) in load_primary_boxes(std.primary_workset).values()
        assert str(pdir) not in load_primary_boxes(std.primary_workset).values()


# ---------------------------------------------------------------------------
# move + convert combo equals convert + move (same end state)
# ---------------------------------------------------------------------------

class TestCombo:
    def test_move_and_convert_to_standalone(self, env):
        config, std, tmp_home = env
        pdir = _make_default(env, contents="combo")
        state = resolve_lifecycle_target(str(pdir), std, config)
        dest = tmp_home / "combo_dest"
        new = execute_lifecycle(
            state, TargetSpec(location=dest, ownership="standalone"),
            std, config, confirm=_conf_yes(),
        )
        assert new.mode == BoxMode.standalone
        assert dest.is_dir()
        # Drift H: the workspace files land in the workspace/ subdir; drift I:
        # workset.yaml at the root.
        assert (dest / "workspace" / "file.txt").read_text() == "combo"
        assert (dest / "workset.yaml").is_file()
        assert (dest / "box_data").is_dir()
        assert not pdir.exists()

    def test_bare_into_workset(self, env):
        config, std, tmp_home = env
        ws = _make_workset(env)
        pdir = _make_default(env, contents="bare")
        state = resolve_lifecycle_target(str(pdir), std, config)
        new = execute_lifecycle(
            state, TargetSpec(location=BARE_INTO_WS, ownership="ws"),
            std, config, confirm=_conf_yes(),
        )
        assert new.mode == BoxMode.named
        assert not new.is_external  # landed inside ws
        landed = ws.workspaces_dir / "proj"
        assert landed.is_dir() and (landed / "file.txt").read_text() == "bare"
        assert not pdir.exists()
        # P8b/Option A: the recorded workspace is the returned state's in-tree dir
        # (an internal box records ``workspaces/<name>``), not an on-disk section.
        assert new.workspace_path == landed.resolve()
        assert "project" not in load_doc(ws.projects_dir / "proj" / "box.yaml")


# ---------------------------------------------------------------------------
# channel-partition relocation on convert/move (6d, D-M10, §6)
# ---------------------------------------------------------------------------

class TestChannelPartitionRelocation:
    """The box's OWN mailbox/share partition follows it across worksets/modes.

    Best-effort (D-M10): own ``mailboxes/<ws>/<box>`` + ``share/<ws>/<box>``
    move to the new partition; workset-LOCAL channels are NOT relocated; a
    missing source warns and is skipped.  Relocation reads the FINAL post-convert
    identity (A9).
    """

    def _seed_partition(self, std, ws_token, box_name, ws_root, marker="m"):
        """Seed the box's own partition dirs.

        ⚑ *ws_root* is REQUIRED because ``own_partition_dirs`` now reads
        ``workset.channels.{mailboxes,share_global}`` off that root. Every case in
        this class leaves those keys unset, so each resolves to its default — which
        is what these cases are about; the REPOINT half is
        :class:`TestRelocationFollowsThePartitionKEYS`.
        """
        from kanibako.channels.channels import own_partition_dirs

        part = own_partition_dirs(std, ws_token, box_name, ws_root=ws_root)
        part.mailbox.mkdir(parents=True, exist_ok=True)
        (part.mailbox / "msg.txt").write_text(marker)
        part.share_global.mkdir(parents=True, exist_ok=True)
        (part.share_global / "pub.txt").write_text(marker)
        return part

    def test_convert_relocates_own_partition(self, env):
        config, std, tmp_home = env
        from kanibako.channels.channels import (
            WS_TOKEN_PRIMARY,
            WS_TOKEN_STANDALONE,
            own_partition_dirs,
        )

        pdir = _make_default(env)
        # Seed THIS box's own partition under the PRIMARY token.
        self._seed_partition(
            std, WS_TOKEN_PRIMARY, "proj", std.primary_workset, marker="hello",
        )

        state = resolve_lifecycle_target(str(pdir), std, config)
        new = execute_lifecycle(
            state, TargetSpec(location=INPLACE, ownership="standalone"),
            std, config, confirm=_conf_yes(),
        )
        assert new.mode == BoxMode.standalone

        # OLD partition (PRIMARY) is gone; NEW (STANDALONE) holds the content.
        # ⚑ Each side's root is the one that side's keys live under — the primary
        # workset for the old, the standalone box's own root for the new.
        old = own_partition_dirs(
            std, WS_TOKEN_PRIMARY, "proj", ws_root=std.primary_workset,
        )
        dst = own_partition_dirs(
            std, WS_TOKEN_STANDALONE, new.name, ws_root=new.metadata_path,
        )
        assert not old.mailbox.exists()
        assert not old.share_global.exists()
        assert (dst.mailbox / "msg.txt").read_text() == "hello"
        assert (dst.share_global / "pub.txt").read_text() == "hello"

    def test_relocation_best_effort_missing_source(self, env, capsys):
        """A convert with no seeded partition warns nothing fatal + succeeds."""
        config, std, tmp_home = env
        pdir = _make_default(env)
        state = resolve_lifecycle_target(str(pdir), std, config)
        # No partition seeded → relocation finds no source, skips silently.
        new = execute_lifecycle(
            state, TargetSpec(location=INPLACE, ownership="standalone"),
            std, config, confirm=_conf_yes(),
        )
        assert new.mode == BoxMode.standalone  # lifecycle completed

    def test_relocation_best_effort_dest_exists(self, env, capsys, monkeypatch):
        """A pre-existing destination is left in place + warned, not clobbered."""
        config, std, tmp_home = env
        from kanibako.launch import box_identity
        from kanibako.channels.channels import (
            WS_TOKEN_PRIMARY,
            WS_TOKEN_STANDALONE,
            own_partition_dirs,
        )

        # BUG#4: convert --standalone now generates a FRESH canonical
        # <kuid>_<leaf> identity, so the NEW channel partition is keyed by
        # that name (not the source's "proj"). Pin the generated name so we can
        # pre-occupy the destination partition under it.
        canonical = "abcde_proj"
        monkeypatch.setattr(
            box_identity, "make_standalone_box_name",
            lambda root, existing: canonical,
        )

        pdir = _make_default(env)
        self._seed_partition(
            std, WS_TOKEN_PRIMARY, "proj", std.primary_workset, marker="src",
        )
        # Pre-occupy the destination mailbox (keyed by the canonical name).
        dst_pre = own_partition_dirs(
            std, WS_TOKEN_STANDALONE, canonical, ws_root=pdir,
        )
        dst_pre.mailbox.mkdir(parents=True, exist_ok=True)
        (dst_pre.mailbox / "existing.txt").write_text("keep")

        state = resolve_lifecycle_target(str(pdir), std, config)
        new = execute_lifecycle(
            state, TargetSpec(location=INPLACE, ownership="standalone"),
            std, config, confirm=_conf_yes(),
        )
        assert new.mode == BoxMode.standalone
        assert new.name == canonical
        # Dest preserved (not clobbered) + a warning was emitted.
        assert (dst_pre.mailbox / "existing.txt").read_text() == "keep"
        assert "already exists" in capsys.readouterr().err

    def test_workset_local_channels_not_relocated(self, env):
        """Workset-LOCAL channels (common/chat) are scope-owned, not moved."""
        config, std, tmp_home = env
        from kanibako.channels.channels import WS_TOKEN_PRIMARY

        pdir = _make_default(env)
        # Seed a PRIMARY workset-local channels tree (scope-owned).
        local = std.primary_workset / "channels" / "common"
        local.mkdir(parents=True, exist_ok=True)
        (local / "shared.txt").write_text("scope")
        self._seed_partition(std, WS_TOKEN_PRIMARY, "proj", std.primary_workset)

        state = resolve_lifecycle_target(str(pdir), std, config)
        execute_lifecycle(
            state, TargetSpec(location=INPLACE, ownership="standalone"),
            std, config, confirm=_conf_yes(),
        )
        # Workset-local common untouched — the box stops MOUNTING it, the dir
        # itself is not relocated.
        assert (local / "shared.txt").read_text() == "scope"


class TestRelocationFollowsThePartitionKEYS:
    """``workset.channels.{mailboxes,share_global}`` are DECLARED KEYS, and a
    relocation must move the directory the box is actually mounted at.

    ⚑⚑ THE GAP THIS CLOSES was recorded at ``channels.own_partition_dirs`` itself:
    the relocation worked from the RAW ``(std, ws_token)`` primitive, which has no
    workset root and therefore cannot see a repoint.  ``box_channel_addresses`` DOES
    route through the keys, so a workset that repoints ``mailboxes`` mounts the box's
    inbox at the repointed address — and a ``box move`` / ``box convert`` then moved
    the DEFAULT directory, which nothing was mounted at, and left every message the
    box had received behind at an address no longer registered to it.

    It was unreachable until the repoint itself became reachable (R-35 gave the six
    ``workset.channels.*`` leaves real readers), which is why it is closed now.
    """

    def _repoint(self, ws_root, key, value):
        """Store *value* at *key* through the CLI's own write route."""
        from kanibako.settings.config import WORKSET_META_FILE
        from kanibako.settings.config_io import write_nested_key
        from kanibako.settings.config_keys import _KEY_ROUTES

        sections, leaf = _KEY_ROUTES[key]
        write_nested_key(ws_root / WORKSET_META_FILE, sections, leaf, str(value))

    def _seed(self, box_dir, marker):
        box_dir.mkdir(parents=True, exist_ok=True)
        (box_dir / "msg.txt").write_text(marker)

    def test_the_OLD_sides_repointed_mailbox_is_the_one_that_moves(self, env):
        """Primary workset repoints ``mailboxes``; convert the box into a named one."""
        config, std, tmp_home = env
        ws = _make_workset(env)
        pdir = _make_default(env)

        repointed = tmp_home / "primary-mail"
        self._repoint(
            std.primary_workset, "workset.channels.mailboxes", repointed,
        )
        # ⚑ Seeded at the address the box's inbox is ACTUALLY mounted at — read off
        # the keyed derivation, not constructed here, so the test cannot disagree
        # with the launch about where a box's mail lives.
        from kanibako.channels.channels import box_channel_addresses

        state = resolve_lifecycle_target(str(pdir), std, config)
        proj = resolve_project(std, config, str(pdir), initialize=False)
        assert box_channel_addresses(proj, std).inbox == repointed / "proj"
        self._seed(repointed / "proj", "mail-that-must-follow")

        new = execute_lifecycle(
            state, TargetSpec(location=INPLACE, ownership="ws"),
            std, config, confirm=_conf_yes(),
        )
        assert new.mode == BoxMode.named

        assert not (repointed / "proj").exists(), (
            "the box's real mailbox was left behind at the OLD workset's repointed "
            "address; the relocation moved the default directory instead"
        )
        landed = std.channels_mailboxes / ws.name / new.name
        assert (landed / "msg.txt").read_text() == "mail-that-must-follow"

    def test_the_NEW_sides_repoint_is_where_the_mailbox_lands(self, env):
        """The destination workset repoints ``mailboxes``; the box must land there."""
        config, std, tmp_home = env
        ws = _make_workset(env)
        pdir = _make_default(env)

        target_mail = tmp_home / "ws-mail"
        self._repoint(ws.root, "workset.channels.mailboxes", target_mail)
        self._seed(std.channels_mailboxes / "__PRIMARY__" / "proj", "carry-me")

        state = resolve_lifecycle_target(str(pdir), std, config)
        new = execute_lifecycle(
            state, TargetSpec(location=INPLACE, ownership="ws"),
            std, config, confirm=_conf_yes(),
        )
        assert new.mode == BoxMode.named
        assert (target_mail / new.name / "msg.txt").read_text() == "carry-me"
        assert not (std.channels_mailboxes / ws.name / new.name).exists(), (
            "the mailbox landed at the NEW workset's DEFAULT address while the box "
            "will be mounted at its repointed one"
        )

    def test_share_global_follows_its_key_too(self, env):
        """The sibling key, which moves in the same loop and off the same root."""
        config, std, tmp_home = env
        ws = _make_workset(env)
        pdir = _make_default(env)

        repointed = tmp_home / "primary-pub"
        self._repoint(
            std.primary_workset, "workset.channels.share_global", repointed,
        )
        self._seed(repointed / "proj", "published")

        state = resolve_lifecycle_target(str(pdir), std, config)
        new = execute_lifecycle(
            state, TargetSpec(location=INPLACE, ownership="ws"),
            std, config, confirm=_conf_yes(),
        )
        assert not (repointed / "proj").exists()
        assert (
            std.channels_share / ws.name / new.name / "msg.txt"
        ).read_text() == "published"

    def test_an_unresolvable_repoint_warns_and_the_lifecycle_still_completes(
        self, env, capsys,
    ):
        """⚑ BEST-EFFORT IS THE CONTRACT (D-M10): a bad key must not break a move.

        Reading the keys put a REFUSING resolver on this path for the first time —
        ``workset.channels.*`` names the key and raises when it cannot resolve — and a
        settings error in a best-effort cleanup step must never abort a lifecycle
        operation that has already moved files.
        """
        config, std, tmp_home = env
        _make_workset(env)
        pdir = _make_default(env)
        self._repoint(
            std.primary_workset, "workset.channels.mailboxes", "@config.registry/nope",
        )

        state = resolve_lifecycle_target(str(pdir), std, config)
        new = execute_lifecycle(
            state, TargetSpec(location=INPLACE, ownership="ws"),
            std, config, confirm=_conf_yes(),
        )
        assert new.mode == BoxMode.named
        err = capsys.readouterr().err
        assert "channel" in err and "workset.channels.mailboxes" in err


# ---------------------------------------------------------------------------
# bare --move only valid with workset target
# ---------------------------------------------------------------------------

class TestValidation:
    def test_bare_into_ws_requires_workset(self, env):
        config, std, tmp_home = env
        pdir = _make_default(env)
        state = resolve_lifecycle_target(str(pdir), std, config)
        with pytest.raises(ProjectError, match="bare"):
            execute_lifecycle(
                state, TargetSpec(location=BARE_INTO_WS, ownership="standalone"),
                std, config, confirm=_conf_yes(),
            )

    def test_dest_occupied(self, env):
        config, std, tmp_home = env
        pdir = _make_default(env)
        state = resolve_lifecycle_target(str(pdir), std, config)
        dest = tmp_home / "occupied"
        dest.mkdir()
        with pytest.raises(ProjectError, match="already exists"):
            execute_lifecycle(
                state, TargetSpec(location=dest), std, config, confirm=_conf_yes(),
            )

    def test_membership_guard(self, env):
        """Refuse landing files inside a non-member workset."""
        config, std, tmp_home = env
        other = create_workset("other", tmp_home / "other_root", std)
        pdir = _make_default(env)
        state = resolve_lifecycle_target(str(pdir), std, config)
        dest = other.workspaces_dir / "intruder"
        with pytest.raises(ProjectError, match="not .* a member"):
            execute_lifecycle(
                state, TargetSpec(location=dest, ownership=UNCHANGED),
                std, config, confirm=_conf_yes(),
            )

    def test_confirm_false_aborts(self, env):
        config, std, tmp_home = env
        pdir = _make_default(env)
        state = resolve_lifecycle_target(str(pdir), std, config)
        with pytest.raises(ProjectError, match="Aborted"):
            execute_lifecycle(
                state, TargetSpec(ownership="standalone"),
                std, config, confirm=lambda: False,
            )
        # nothing changed: the box still resolves as a primary box in place.
        assert resolve_lifecycle_target(str(pdir), std, config).mode == BoxMode.primary
        assert not (pdir / "box_data").exists()  # no standalone conversion happened


# ---------------------------------------------------------------------------
# Failure injection -> unwind
# ---------------------------------------------------------------------------

class TestUnwind:
    def test_ownership_failure_restores_state(self, env, monkeypatch):
        """If the ownership step blows up mid-move, original state is restored."""
        config, std, tmp_home = env
        pdir = _make_default(env, contents="unwind")
        state = resolve_lifecycle_target(str(pdir), std, config)
        dest = tmp_home / "unwind_dest"

        names_before = dict(load_primary_boxes(std.primary_workset))
        meta_before = load_doc(state.metadata_path / "box.yaml")

        # Force the standalone ownership step to raise AFTER file move + name
        # work has begun.
        def boom(*a, **kw):
            raise RuntimeError("injected failure")

        monkeypatch.setattr(lc, "_to_standalone", boom)

        with pytest.raises(RuntimeError, match="injected"):
            execute_lifecycle(
                state, TargetSpec(location=dest, ownership="standalone"),
                std, config, confirm=_conf_yes(),
            )

        # Files restored: dest copy removed, original intact.
        assert not dest.exists()
        assert pdir.is_dir() and (pdir / "file.txt").read_text() == "unwind"
        # Names + metadata unchanged.
        assert dict(load_primary_boxes(std.primary_workset)) == names_before
        assert load_doc(state.metadata_path / "box.yaml") == meta_before

    def test_workset_failure_unwinds_registration(self, env, monkeypatch):
        """A failure after add_project unwinds the workset registration."""
        config, std, tmp_home = env
        ws = _make_workset(env)
        pdir = _make_default(env, contents="wsunwind")
        state = resolve_lifecycle_target(str(pdir), std, config)

        # Patch the sparse settings writer inside _lifecycle to raise after
        # add_project + copytree have run (P8b: write_project_meta retired from
        # the move path — the box.enable_vault sparse write is the same seam).
        calls = {"n": 0}

        def flaky_write(*a, **kw):
            calls["n"] += 1
            raise RuntimeError("write failed")

        monkeypatch.setattr(lc, "write_box_enable_vault", flaky_write)

        with pytest.raises(RuntimeError, match="write failed"):
            execute_lifecycle(
                state, TargetSpec(ownership="ws"), std, config, confirm=_conf_yes(),
            )
        assert calls["n"] >= 1  # the patched seam actually fired

        # workset registration unwound.
        ws2 = load_workset(ws.root, ws.name)
        assert not any(p.name == "proj" for p in ws2.projects)
        # original default project intact + still resolves as primary in place.
        assert str(pdir) in load_primary_boxes(std.primary_workset).values()
        assert resolve_lifecycle_target(str(pdir), std, config).mode == BoxMode.primary


# ---------------------------------------------------------------------------
# _default_state_from_meta — the remap fallback when the workspace dir is gone.
# P8b: existence is REGISTRY membership (not on-disk project.mode); enable_vault
# is the plain box-scope ``box.enable_vault`` read (decoupled from identity).
# ---------------------------------------------------------------------------

class TestDefaultStateFromMeta:
    def test_registry_membership_sources_state(self, env):
        config, std, tmp_home = env
        pdir = _make_default(env, name="gonebox")
        # Disable vault via the box-scope key so we can prove enable_vault is
        # sourced from ``box.enable_vault`` (not a project-identity field).
        from kanibako.commands.box._lifecycle import _default_state_from_meta
        from kanibako.settings.config_io import write_nested_key

        write_nested_key(
            std.boxes / "gonebox" / "box.yaml",
            ("box",), "enable_vault", False,
        )

        state = _default_state_from_meta(pdir, std)
        assert state is not None
        assert state.name == "gonebox"
        assert state.mode is BoxMode.primary
        assert state.metadata_path == std.boxes / "gonebox"
        assert state.enable_vault is False  # sourced from box.enable_vault

    def test_unregistered_workspace_returns_none(self, env):
        config, std, tmp_home = env
        from kanibako.commands.box._lifecycle import _default_state_from_meta

        # No registration for this path → no membership → None.
        assert _default_state_from_meta(tmp_home / "nope", std) is None

    def test_membership_without_settings_still_resolves(self, env):
        """Mutation guard: the OLD ``read_project_meta`` presence gate returned
        None when box.yaml was absent.  Now membership alone suffices — a
        registered box whose box.yaml is gone still yields a state (enable_
        vault defaulting True via the box-scope reader)."""
        config, std, tmp_home = env
        pdir = _make_default(env, name="nosettings")
        from kanibako.commands.box._lifecycle import _default_state_from_meta

        # Remove the box's box.yaml (identity no longer self-describes).
        # (Under sparse create a default-vault primary box may never have written
        # one — missing_ok makes the "settings gone" precondition robust.)
        (std.boxes / "nosettings" / "box.yaml").unlink(missing_ok=True)
        # Sanity: the box dir + registration remain.
        assert (std.boxes / "nosettings").is_dir()

        state = _default_state_from_meta(pdir, std)
        assert state is not None
        assert state.name == "nosettings"
        assert state.enable_vault is True  # default via read_box_enable_vault


# ---------------------------------------------------------------------------
# A ``box.enable_vault`` published at the WORKSET tier is an OVERRIDABLE DEFAULT:
# it must resolve the same either way, and it must never harden into a box tier.
# ---------------------------------------------------------------------------

class TestWorksetTierVaultDefaultIsNotPinned:
    @staticmethod
    def _publish_primary_default(std, value):
        """Write ``box.enable_vault`` at the PRIMARY workset tier."""
        from kanibako.settings.config_io import write_nested_key

        write_nested_key(
            std.primary_workset / "workset.yaml", ("box",), "enable_vault", value,
        )

    @staticmethod
    def _author_at_box(std, box_name, value):
        """Write ``box.enable_vault`` at the BOX's own tier."""
        from kanibako.settings.config_io import write_nested_key

        write_nested_key(
            std.boxes / box_name / "box.yaml", ("box",), "enable_vault", value,
        )

    def test_remap_resolution_ignores_whether_the_workspace_dir_survives(self, env):
        """``remap``'s metadata-only fallback resolves like the live path (one answer)."""
        config, std, tmp_home = env
        from kanibako.commands.box._lifecycle import _default_state_from_meta

        pdir = _make_default(env, name="dvbox")
        self._publish_primary_default(std, False)

        # Workspace still on disk -> the ordinary resolver.
        live = resolve_lifecycle_target(str(pdir), std, config)
        assert live.enable_vault is False
        assert live.box_authored_vault is True

        # Workspace already moved away -> the registered-metadata fallback.
        import shutil

        shutil.rmtree(pdir)
        fallback = _default_state_from_meta(pdir, std)
        assert fallback is not None
        assert fallback.enable_vault is False
        assert fallback.box_authored_vault is True

    def test_move_does_not_harden_the_workset_default_into_the_box_tier(self, env):
        """A value the box merely INHERITED is not persisted as its own override."""
        config, std, tmp_home = env
        pdir = _make_default(env, name="inherit")
        self._publish_primary_default(std, False)

        state = resolve_lifecycle_target(str(pdir), std, config)
        new = execute_lifecycle(
            state, TargetSpec(location=tmp_home / "inherit_moved", ownership=UNCHANGED),
            std, config, confirm=_conf_yes(),
        )
        stored = load_doc(new.metadata_path / "box.yaml").get("box", {})
        assert "enable_vault" not in stored

    def test_workset_move_leaves_the_source_worksets_default_behind(self, env):
        """The SOURCE workset's default does not follow the box into another workset."""
        config, std, tmp_home = env
        pdir = _make_default(env, name="leaver")
        self._publish_primary_default(std, False)
        _make_workset(env, ws_name="dvdest", root_name="dvdest_root")

        state = resolve_lifecycle_target(str(pdir), std, config)
        new = execute_lifecycle(
            state, TargetSpec(ownership="dvdest"), std, config, confirm=_conf_yes(),
        )
        stored = load_doc(new.metadata_path / "box.yaml").get("box", {})
        assert "enable_vault" not in stored

    def test_convert_to_standalone_leaves_the_worksets_default_behind(self, env):
        """Leaving the workset for standalone drops the value, because it was the workset's."""
        config, std, tmp_home = env
        pdir = _make_default(env, name="salever")
        self._publish_primary_default(std, False)

        state = resolve_lifecycle_target(str(pdir), std, config)
        new = execute_lifecycle(
            state, TargetSpec(location=INPLACE, ownership="standalone"),
            std, config, confirm=_conf_yes(),
        )
        stored = load_doc(new.metadata_path / "box_data" / "box.yaml").get("box", {})
        assert "enable_vault" not in stored
        assert new.enable_vault is True

    def test_a_box_authored_value_still_travels(self, env):
        """Guard against over-fixing: the box's OWN override survives every hop."""
        config, std, tmp_home = env
        pdir = _make_default(env, name="ownvalue")
        state = resolve_lifecycle_target(str(pdir), std, config)
        self._author_at_box(std, state.name, False)
        _make_workset(env, ws_name="dvkeep", root_name="dvkeep_root")

        state = resolve_lifecycle_target(str(pdir), std, config)
        assert state.box_authored_vault is False
        new = execute_lifecycle(
            state, TargetSpec(ownership="dvkeep"), std, config, confirm=_conf_yes(),
        )
        stored = load_doc(new.metadata_path / "box.yaml").get("box", {})
        assert stored.get("enable_vault") is False

    # -- duplicate: a FOURTH write site, outside the lifecycle engine --
    #
    # ⚑ ``box duplicate --to standalone`` reaches ``establish_standalone`` through
    # ``_duplicate.py``, not ``_lifecycle.py``, so the engine's ``box_authored_vault``
    # does not cover it.  The rule is the same one: a duplicate is a NEW workset scope,
    # so the source workset's downward default does not travel.

    @staticmethod
    def _duplicate_to_standalone(src, dst):
        """Run the real ``box duplicate --to standalone`` CLI entry point."""
        import argparse

        from kanibako.commands.box._duplicate import run_duplicate

        return run_duplicate(argparse.Namespace(
            source_path=str(src), new_path=str(dst), to_mode="standalone",
            bare=False, force=True, box=None, workset=None, project_name=None,
        ))

    def test_duplicate_to_standalone_leaves_the_worksets_default_behind(self, env):
        """A duplicate does not acquire the source workset's default as its own override."""
        config, std, tmp_home = env
        pdir = _make_default(env, name="dupleaver")
        self._publish_primary_default(std, False)

        assert self._duplicate_to_standalone(pdir, tmp_home / "dupleaver_copy") == 0

        dst = tmp_home / "dupleaver_copy"
        stored = load_doc(dst / "box_data" / "box.yaml").get("box", {})
        assert "enable_vault" not in stored
        # The duplicate's own root workset.yaml carries only ``workset.kuid``, so the
        # RESOLVED answer is the default — the value stayed with the workset it belonged to.
        dup = resolve_standalone_project(std, config, project_dir=str(dst), initialize=False)
        assert dup.enable_vault is True

    def test_duplicate_from_a_standalone_source_leaves_its_root_default_behind(self, env):
        """The source's ROOT file is its WORKSET tier; its ``box.*`` default does not travel."""
        config, std, tmp_home = env
        from kanibako.settings.config_io import write_nested_key

        src = _make_standalone(env, name="dupsa")
        write_nested_key(src / "workset.yaml", ("box",), "enable_vault", False)

        assert self._duplicate_to_standalone(src, tmp_home / "dupsa_copy") == 0

        dst = tmp_home / "dupsa_copy"
        stored = load_doc(dst / "box_data" / "box.yaml").get("box", {})
        assert "enable_vault" not in stored

    def test_duplicate_carries_a_box_authored_value(self, env):
        """Guard against over-fixing: the source box's OWN override still travels."""
        config, std, tmp_home = env
        pdir = _make_default(env, name="dupowner")
        state = resolve_lifecycle_target(str(pdir), std, config)
        self._author_at_box(std, state.name, False)

        assert self._duplicate_to_standalone(pdir, tmp_home / "dupowner_copy") == 0

        dst = tmp_home / "dupowner_copy"
        stored = load_doc(dst / "box_data" / "box.yaml").get("box", {})
        assert stored.get("enable_vault") is False
        dup = resolve_standalone_project(std, config, project_dir=str(dst), initialize=False)
        assert dup.enable_vault is False

    def test_duplicate_creates_no_vault_either_way(self, env):
        """Resolved governs CREATION, and on this path nothing is created — before or after."""
        config, std, tmp_home = env
        pdir = _make_default(env, name="dupvault")
        self._publish_primary_default(std, False)
        src_proj = resolve_project(std, config, project_dir=str(pdir), initialize=False)

        assert self._duplicate_to_standalone(pdir, tmp_home / "dupvault_copy") == 0

        dst = tmp_home / "dupvault_copy"
        dup = resolve_standalone_project(std, config, project_dir=str(dst), initialize=False)
        # A duplicate never carries a vault (``_duplicate.py``, confirmed intended) …
        assert not dup.vault_ro_path.exists()
        assert not dup.vault_rw_path.exists()
        # … and never removes the source's.
        assert src_proj.vault_rw_path.is_dir()


# ---------------------------------------------------------------------------
# The standalone consolidate sweep — kanibako's own directories are RESOLVED
# ---------------------------------------------------------------------------

def _convert_in_place(env, root, ownership, name=None):
    """Run the real in-place convert of *root* to *ownership*, optionally renaming."""
    config, std, _tmp_home = env
    state = resolve_lifecycle_target(str(root), std, config)
    return execute_lifecycle(
        state, TargetSpec(location=INPLACE, ownership=ownership, name=name),
        std, config, confirm=_conf_yes(),
    )


def _convert_to_standalone_in_place(env, root):
    """Run the real convert that consolidates *root* into its standalone workspace dir."""
    return _convert_in_place(env, root, "standalone")


def _default_with_workset_keys(env, name, table):
    """A default-mode box whose dir carries a ``workset.yaml`` repointing *table*."""
    config, std, tmp_home = env
    from kanibako.settings.config_io import dump_doc

    root = tmp_home / name
    root.mkdir()
    (root / "file.txt").write_text("mine")
    resolve_project(std, config, project_dir=str(root), initialize=True)
    dump_doc(root / "workset.yaml", {"workset": table})
    return root


class TestConsolidateResolvesTheRootsOwnKeys:
    """``box convert --standalone`` sweeps the root into the workspace dir, and what it
    must NOT sweep is kanibako's own layout — which is a set of RESOLVED ``workset.*``
    directory keys, never a set of leaf names.

    ⚑ The three cases below are the three shapes a name set answers differently:
    NO repoint (a name set can express it, and still missed ``workset.canon``), an
    IN-ROOT repoint (the root child is a name no list holds), and an ABSOLUTE repoint
    (not representable in a name set at all).
    """

    def test_no_repoint_keeps_the_default_canon_tree(self, env):
        """⚑ NO repoint is involved: ``canon`` was simply never in the name set, so a
        round trip out of standalone and back swept kanibako's own canon tree into the
        user's workspace."""
        config, std, tmp_home = env
        root = _make_standalone(env, name="rt")
        (root / "canon" / "MARKER").write_text("CANON")

        # Out of standalone (lifts ``workspace/`` back to the root) …
        state = resolve_lifecycle_target(str(root), std, config)
        execute_lifecycle(
            state, TargetSpec(location=INPLACE, ownership="default"),
            std, config, confirm=_conf_yes(),
        )
        # … and back in, which consolidates.
        _convert_to_standalone_in_place(env, root)

        assert (root / "canon" / "MARKER").read_text() == "CANON"
        assert not (root / "workspace" / "canon").exists()
        assert (root / "workspace" / "file.txt").is_file()

    def test_in_root_repoint_keeps_the_directory_that_holds_the_arm(self, env, capsys):
        """``vault_ro: @meta.workset.path/store/ro`` makes the root child ``store`` — a
        name no list holds.  The ANCESTOR test is what keeps it; an equality test would
        sweep it.  ⚑ The repoint carries the ``@``-ref because [R147] refuses a bare
        relative; the directory it names is the same one ``store/ro`` used to reach."""
        root = _default_with_workset_keys(
            env, "inroot",
            {"vault_ro": "@meta.workset.path/store/ro",
             "vault_rw": "@meta.workset.path/store/rw",
             "canon": "@meta.workset.path/kanon"},
        )
        (root / "store" / "ro").mkdir(parents=True)
        (root / "store" / "ro" / "SECRET").write_text("RO")
        (root / "store" / "rw").mkdir(parents=True)
        (root / "kanon").mkdir()
        (root / "kanon" / "MARKER").write_text("CANON")

        _convert_to_standalone_in_place(env, root)

        assert (root / "store" / "ro" / "SECRET").read_text() == "RO"
        assert (root / "kanon" / "MARKER").read_text() == "CANON"
        assert not (root / "workspace" / "store").exists()
        assert not (root / "workspace" / "kanon").exists()
        # The user's own content still goes where a consolidate puts it.
        assert (root / "workspace" / "file.txt").read_text() == "mine"

    def test_a_repointed_keep_is_reported_and_names_the_key(self, env, capsys):
        """[R144] — a keep that cannot name the path as the user's is just a leak.  The
        DEFAULT layout's keeps stay silent; only what the user repointed is announced."""
        root = _default_with_workset_keys(
            env, "reported", {"vault_ro": "@meta.workset.path/store/ro"},
        )
        (root / "store" / "ro").mkdir(parents=True)

        _convert_to_standalone_in_place(env, root)

        err = capsys.readouterr().err
        assert str(root / "store") in err
        assert "workset.vault_ro" in err
        # ⚑ Silence for the default layout: ``vault/`` is kanibako's, not the user's.
        assert "workset.vault_rw" not in err

    def test_absolute_workspaces_repoint_fills_the_dir_the_box_reads(self, env):
        """⚑⚑ THE CASE A NAME SET CANNOT EXPRESS.  With ``workset.workspaces`` pointed
        at an absolute path, a literal ``<root>/workspace`` destination fills a
        directory the box never looks in: ``resolve_standalone_project`` answers the
        repoint, so the box would open an EMPTY workspace with its files elsewhere."""
        config, std, tmp_home = env
        elsewhere = tmp_home / "elsewhere" / "work"
        root = _default_with_workset_keys(
            env, "absws", {"workspaces": str(elsewhere)},
        )
        (root / "code.py").write_text("CODE")

        new = _convert_to_standalone_in_place(env, root)

        proj = resolve_standalone_project(
            std, config, project_dir=str(root), initialize=False,
        )
        assert proj.project_path == elsewhere
        assert (elsewhere / "code.py").read_text() == "CODE"
        assert (elsewhere / "file.txt").read_text() == "mine"
        # ⚑ ONE answer for one box: the state the lifecycle returns and the state the
        # resolver answers name the same directory.
        assert new.workspace_path == proj.project_path
        assert not (root / "workspace").exists()

    def test_an_unresolvable_repoint_refuses_before_anything_moves(self, env):
        """A root whose layout keys do not answer is a root whose children cannot be
        told apart — and the sweep MOVES USER DATA.  Refuse while the tree is whole."""
        from kanibako.settings.settings_resolve import SettingsError

        root = _default_with_workset_keys(
            env, "unresolvable", {"vault_ro": "@config.registry/ro"},
        )
        (root / "keepme").mkdir()

        with pytest.raises(SettingsError) as exc:
            _convert_to_standalone_in_place(env, root)
        assert "workset.vault_ro" in str(exc.value)
        # Nothing swept: the refusal lands before the first move.
        assert (root / "keepme").is_dir()
        assert (root / "file.txt").is_file()
        assert not (root / "workspace").exists()

    def test_a_workspaces_key_at_the_root_consolidates_nothing(self, env):
        """``workspaces: @meta.workset.path`` means the workspace already IS the root —
        there is nothing to consolidate, and moving each child onto itself would raise.
        ⚑ It used to be spelled ``.``, which [R147] refuses along with every other bare
        relative; the ``@``-ref names the same directory and names it unambiguously."""
        root = _default_with_workset_keys(
            env, "atroot", {"workspaces": "@meta.workset.path"},
        )
        (root / "keepme").mkdir()

        _convert_to_standalone_in_place(env, root)

        assert (root / "file.txt").read_text() == "mine"
        assert (root / "keepme").is_dir()

    def test_no_leaf_name_decides_what_the_sweep_keeps(self):
        """⚑ TRIPWIRE (P15).  Pins the rule at the SITE, so a reintroduced name test reds
        without anyone re-deriving the census.  ⚑ The banned spellings include the LEAF
        CONSTANTS, not just the bare strings — a string-only pin passes
        ``root / paths_defaults.WORKSPACE_PATH``, which is the same defect wearing an
        import.  ⚑ ``paths_defaults.VAULT_PATH`` is deliberately absent from the ban:
        NO key names the ``vault/`` skeleton parent (``project/workset.py::_VAULT_LEAF``),
        so a literal is the only correct spelling for it.
        """
        from tests.support.repo import REPO_ROOT

        src = (REPO_ROOT / "src" / "kanibako" / "commands" / "box"
               / "_lifecycle.py").read_text(encoding="utf-8")
        # ⚑ TWO regions, because the sweep has two ends and each broke on its own: the
        # consolidate machinery decides what it KEEPS, and ``_to_standalone`` decides
        # where it PUTS the rest.  A literal at either end is the same defect.  Both are
        # bounded by their own symbols, so an edit above them does not drift the pin.
        region = (
            src[src.index("_STANDALONE_FIXED_ARTIFACTS = frozenset("):
                src.index("def _undo_consolidate(")]
            + src[src.index("def _to_standalone("):
                  src.index("def _to_workset(")]
        )
        # ⚑ CODE only.  A pin that reds on PROSE is a false-alarm generator, and the
        # comments here quote the banned spellings on purpose, to say why they are banned.
        region = "\n".join(
            line for line in region.splitlines()
            if not line.lstrip().startswith("#")
        )
        for spelling in ('"workspace"', "'workspace'", "WORKSPACE_PATH",
                         '"workspaces"', "WORKSPACES_PATH",
                         '"canon"', "'canon'"):
            assert spelling not in region, (
                f"{spelling} decides an artifact by NAME in the consolidate region; "
                f"resolve the workset key instead (_STANDALONE_ROOT_DIR_KEYS)"
            )
        # ⚑ And the DECISION PROCEDURE itself, which is what catches a leaf the list
        # above does not name — ``VAULT_PATH`` included.  The one legitimate name test
        # is against the un-repointable set; every other one is the defect.
        for line in region.splitlines():
            if ".name" not in line or "_STANDALONE_FIXED_ARTIFACTS" in line:
                continue
            assert not any(op in line for op in (".name ==", ".name in", ".name !=")), (
                f"a leaf-name comparison decides an artifact here: {line.strip()!r}; "
                f"compare RESOLVED paths (_artifact_claiming)"
            )

    def test_every_root_dir_key_is_resolved_not_defaulted(self, env):
        """⚑ Reds on its own emptiness (P15): each declared root key must actually move
        when repointed, so a table that silently loses an entry cannot pass."""
        from kanibako.commands.box._lifecycle import (
            _STANDALONE_ROOT_DIR_KEYS, _standalone_root_artifacts,
        )
        from kanibako.settings.config_io import dump_doc

        config, std, tmp_home = env
        assert _STANDALONE_ROOT_DIR_KEYS, "the key table must not be empty"
        for key, _resolver in _STANDALONE_ROOT_DIR_KEYS:
            leaf = key.split(".", 1)[1]
            root = tmp_home / f"probe_{leaf}"
            root.mkdir()
            dump_doc(root / "workset.yaml", {
                "workset": {leaf: f"@meta.workset.path/moved_{leaf}"},
            })
            answered = {
                k: (p, repointed)
                for k, p, repointed in _standalone_root_artifacts(root)
            }
            path, repointed = answered[key]
            assert path == root / f"moved_{leaf}", key
            assert repointed is True, key


# ---------------------------------------------------------------------------
# The standalone ROOT is READ off the state, never counted off the workspace
# ---------------------------------------------------------------------------

class TestStandaloneRootIsNotAPositionInThePath:
    """An in-place convert of a STANDALONE box must aim at the box's ROOT — and the root
    is ``ProjectState.metadata_path`` (drift I), not ``workspace_path.parent``.

    ⚑ ``workspace_path`` is the RESOLVED ``workset.workspaces``, so its parent is the root
    ONLY in the default layout.  The three cases below are the three layouts that
    distinguishes: no repoint, an IN-ROOT repoint (one level deeper ⇒ the parent is an
    intermediate directory), and an ABSOLUTE repoint (the parent is not under the root at
    all, and is a directory kanibako was never given).
    """

    def test_default_layout_lifts_into_the_root(self, env):
        """The no-repoint case, unchanged — the parent and the root coincide here, which
        is exactly why the positional spelling survived this long."""
        root = _make_default(env, name="liftplain")
        first = _convert_to_standalone_in_place(env, root)
        assert first.workspace_path == root / "workspace"

        new = _convert_in_place(env, root, "default")

        assert new.workspace_path == root
        assert (root / "file.txt").read_text() == "hello"
        assert not (root / "workspace").exists()

    def test_in_root_repoint_lifts_into_the_root_not_the_workspaces_parent(self, env):
        """``workspaces`` one level deeper: the parent is ``<root>/nested``, a directory
        that exists only to hold the workspace.  Lifting into it leaves the box's files
        one level below the project dir the box now claims."""
        config, std, tmp_home = env
        root = _default_with_workset_keys(
            env, "liftnested",
            {"workspaces": "@meta.workset.path/nested/deep"},
        )
        _convert_to_standalone_in_place(env, root)
        assert (root / "nested" / "deep" / "file.txt").is_file()

        new = _convert_in_place(env, root, "default")

        assert new.workspace_path == root
        assert (root / "file.txt").read_text() == "mine"
        # ⚑ The interposed directory only ever existed to hold the workspace, and the
        # ``workset.yaml`` that named it is gone — so it does not outlive the convert.
        assert not (root / "nested").exists()
        # The box the user will open is registered AT the root, not below it.
        assert str(root) in load_primary_boxes(std.primary_workset).values()

    def test_absolute_repoint_keeps_the_users_workspace_and_reports_it(self, env, capsys):
        """⚑⚑ [R144] — the case a positional parent cannot express AT ALL.  The parent of
        an absolute workspace is a directory the user never gave kanibako: lifting into it
        EMPTIED the directory the user named, deleted it, and scattered its contents into
        a sibling tree.  Nothing needs to move here — every other mode roots its workspace
        at the project dir, and that dir may be anywhere."""
        config, std, tmp_home = env
        elsewhere = tmp_home / "outside" / "work"
        root = _default_with_workset_keys(
            env, "liftabs", {"workspaces": str(elsewhere)},
        )
        _convert_to_standalone_in_place(env, root)
        assert (elsewhere / "file.txt").is_file()
        capsys.readouterr()

        new = _convert_in_place(env, root, "default")

        assert new.workspace_path == elsewhere
        assert (elsewhere / "file.txt").read_text() == "mine"
        # ⚑ The parent is NOT ours: nothing of the user's was moved up into it.
        assert not (elsewhere.parent / "file.txt").exists()
        assert str(elsewhere) in load_primary_boxes(std.primary_workset).values()
        # A keep that cannot name the path as the user's is just a leak.
        err = capsys.readouterr().err
        assert str(elsewhere) in err
        assert "workset.workspaces" in err

    def test_rename_in_place_does_not_lay_a_second_box_in_the_workspace(self, env):
        """A standalone box renamed AT ITS OWN ROOT.  ``_to_standalone`` reads its
        ``root`` argument as the root, so handing it the workspace built a whole second
        box inside the first — and then the source teardown removed the ORIGINAL's
        ``box_data/``, meta and vault, because the destination was no longer the source.
        """
        from kanibako.project import registry_store
        from kanibako.settings.paths import STANDALONE_META_DIR

        config, std, tmp_home = env
        root = _make_default(env, name="renameplain")
        first = _convert_to_standalone_in_place(env, root)
        # Data at every target the teardown would have taken.
        first.vault_rw.mkdir(parents=True, exist_ok=True)
        (first.vault_rw / "SECRET").write_text("RW")
        (first.shell_path / "notes.md").write_text("HOME")

        renamed = _convert_in_place(env, root, "standalone", name="renamedbox")

        assert renamed.metadata_path == root
        assert renamed.workspace_path == first.workspace_path
        # ⚑ ONE box: no second root inside the live workspace.
        assert not (renamed.workspace_path / STANDALONE_META_DIR).exists()
        assert not (renamed.workspace_path / "workset.yaml").exists()
        # ⚑ The rename is an IDENTITY change: home, vault and workspace are untouched.
        assert (renamed.shell_path / "notes.md").read_text() == "HOME"
        assert (renamed.vault_rw / "SECRET").read_text() == "RW"
        assert (renamed.workspace_path / "file.txt").read_text() == "hello"
        # ⚑ …and kanibako's own root ``.gitignore`` did not travel into the workspace.
        assert not (renamed.workspace_path / ".gitignore").exists()
        standalone = registry_store.load_section(std.registry, "standalone")
        assert standalone[renamed.name] == str(root)
        assert first.name not in standalone

    def test_rename_in_place_under_a_repoint_stays_at_the_root(self, env):
        """The same rename with the workspace repointed away from its default leaf — the
        one shape where a positional root and the real root differ by more than a name."""
        from kanibako.settings.paths import STANDALONE_META_DIR

        root = _default_with_workset_keys(
            env, "renamenested",
            {"workspaces": "@meta.workset.path/nested/deep"},
        )
        first = _convert_to_standalone_in_place(env, root)

        renamed = _convert_in_place(env, root, "standalone", name="renamedeep")

        assert renamed.metadata_path == root
        assert renamed.workspace_path == root / "nested" / "deep"
        assert renamed.workspace_path == first.workspace_path
        assert (root / STANDALONE_META_DIR).is_dir()
        assert not (renamed.workspace_path / STANDALONE_META_DIR).exists()
        assert (renamed.workspace_path / "file.txt").read_text() == "mine"

    def test_a_failed_convert_out_puts_the_workspace_back(self, env, monkeypatch):
        """The lift is compensated, and under a repoint the compensation has to REBUILD
        what the lift removed: the workspace dir and the directories the repoint
        interposed are gone by the time a later step fails, so a restore that assumed
        them would move every file onto a missing parent and swallow the OSError."""
        root = _default_with_workset_keys(
            env, "unwindnested",
            {"workspaces": "@meta.workset.path/nested/deep"},
        )
        _convert_to_standalone_in_place(env, root)

        def boom(*a, **kw):
            raise RuntimeError("injected failure")

        monkeypatch.setattr(lc, "_to_default", boom)
        with pytest.raises(RuntimeError, match="injected"):
            _convert_in_place(env, root, "default")

        assert (root / "nested" / "deep" / "file.txt").read_text() == "mine"
        assert not (root / "file.txt").exists()

    def test_no_positional_parent_derives_the_standalone_root(self):
        """⚑ TRIPWIRE (P15).  Pins the RULE at the site: the standalone root is carried on
        the state, so no step may recover it by walking up from the workspace.  A
        reintroduced ``.parent`` reds here instead of waiting for a repointed user.
        ⚑ CODE only — the comments quote the banned spelling on purpose, to say why.
        """
        from tests.support.repo import REPO_ROOT

        src = (REPO_ROOT / "src" / "kanibako" / "commands" / "box"
               / "_lifecycle.py").read_text(encoding="utf-8")
        for line in src.splitlines():
            if line.lstrip().startswith("#"):
                continue
            assert "workspace_path.parent" not in line, (
                f"the standalone root is derived POSITIONALLY here: {line.strip()!r}; "
                f"read it off ProjectState.metadata_path (drift I) instead"
            )
