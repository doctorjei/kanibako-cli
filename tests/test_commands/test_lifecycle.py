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
from kanibako.config import load_config
from kanibako.config_io import load_doc
from kanibako.errors import ProjectError, WorksetError
from kanibako.names import read_names
from kanibako.paths import (
    BoxMode,
    detect_project_mode,
    load_std_paths,
    resolve_project,
    resolve_standalone_project,
)
from kanibako.workset import (
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

    from kanibako import registry_store, workset_registry
    from kanibako.config_io import load_doc

    out = {}
    for name, root_str in registry_store.load_section(
        std.registry, "worksets"
    ).items():
        root = Path(root_str)
        registry_path = workset_registry.resolve_workset_registry_path(
            root, load_doc(root / "settings.yaml"),
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
        # Drift I: metadata (settings.yaml) is at the ROOT.
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
        # Drift I: settings.yaml at the ROOT; box_data/ is the marker dir.
        assert (pdir / "settings.yaml").is_file()
        assert (pdir / "box_data").is_dir()
        assert not (pdir / "box_data" / "settings.yaml").exists()
        # P8b/Option A: no on-disk ``project:`` identity — the marker settings.yaml
        # exists (materialized by the sparse kuid write) but carries no
        # ``project:`` section; the standalone identity lives in
        # registry.standalone + new.name.
        from kanibako.config import read_workset_kuid
        from kanibako.kuid import SENTINEL
        from kanibako.registry_store import load_standalone
        assert "project" not in load_doc(pdir / "settings.yaml")
        assert read_workset_kuid(pdir / "settings.yaml") != SENTINEL
        assert new.mode == BoxMode.standalone
        assert load_standalone(std.registry).get(new.name) == str(pdir)
        # default-mode name unregistered.
        assert str(pdir) not in read_names(std.registry)["projects"].values()

    def test_convert_to_standalone_is_detectable(self, env):
        config, std, tmp_home = env
        pdir = _make_default(env)
        state = resolve_lifecycle_target(str(pdir), std, config)
        execute_lifecycle(
            state, TargetSpec(location=INPLACE, ownership="standalone"),
            std, config, confirm=_conf_yes(),
        )
        # Canonical box_data/ marker dir + root settings.yaml (drift I), not
        # legacy .kanibako/.
        assert (pdir / "settings.yaml").is_file()
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
        from kanibako.registry_store import load_standalone

        config, std, tmp_home = env
        pdir = _make_default(env)
        src_state = resolve_lifecycle_target(str(pdir), std, config)
        src_name = src_state.name
        # The primary source is registered in names.yaml at the project path.
        assert str(pdir) in read_names(std.registry)["projects"].values()

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
        #     NOT the source's primary name.  The marker settings.yaml is sparse.
        assert "project" not in load_doc(pdir / "settings.yaml")
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
        assert str(pdir) not in read_names(std.registry)["projects"].values()
        assert src_name not in read_names(std.registry)["projects"]

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
        from kanibako.registry_store import load_standalone

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
        assert "project" not in load_doc(pdir / "settings.yaml")
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
        assert "project" not in load_doc(new.metadata_path / "settings.yaml")
        # old in-tree metadata gone.
        assert not (pdir / "box_data").exists()
        # name registered.
        assert str(pdir) in read_names(std.registry)["projects"].values()

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
        ws2 = load_workset(ws.root)
        assert any(p.name == "proj" for p in ws2.projects)
        # P8b/Option A: mode + external workspace live in the returned state and
        # the workset's per-workset ``boxes:`` registry, not an on-disk section.
        assert new.mode == BoxMode.named
        assert new.workspace_path == pdir.resolve()
        from kanibako import workset_registry
        from kanibako.config_io import load_doc
        reg = workset_registry.load_workset_boxes(
            workset_registry.resolve_workset_registry_path(
                ws.root, load_doc(ws.root / "settings.yaml"),
            )
        )
        assert reg.get("proj") == str(pdir.resolve())
        # old default name unregistered.
        assert str(pdir) not in read_names(std.registry)["projects"].values()

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

    def test_default_inplace_reuses_name_no_suffix(self, env):
        """L2: an in-place default convert reuses the registered name, not foo2.

        Pre-fix, ``_to_default`` ran ``assign_name`` while the box's own name was
        still registered, so the unchanged-path convert auto-suffixed (foo→foo2)
        and stranded the original entry. The path is unchanged here, so the
        existing name must be reused and there must be no ``proj2``.
        """
        config, std, tmp_home = env
        pdir = _make_default(env)  # registers "proj" → pdir
        state = resolve_lifecycle_target(str(pdir), std, config)
        new = execute_lifecycle(
            state, TargetSpec(location=INPLACE, ownership="default", name="proj2"),
            std, config, confirm=_conf_yes(),
        )
        assert new.mode == BoxMode.primary
        # Name reused (no auto-suffix), path unchanged.
        assert new.name == "proj"
        projects = read_names(std.registry)["projects"]
        assert projects.get("proj") == str(pdir)
        # No stranded suffixed entry.
        assert "proj2" not in projects
        # Exactly one registry entry points at this workspace.
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
        ws2 = load_workset(ws.root)
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
        # Drift I: settings.yaml at the ROOT; drift H: the external dir becomes
        # the standalone root and its files move into the workspace/ subdir.
        assert (external / "settings.yaml").is_file()
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
        assert not any(p.name == "p" for p in load_workset(ws_a.root).projects)
        assert any(p.name == "p" for p in load_workset(tmp_home / "wsb_root").projects)
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
        assert "project" not in load_doc(new.metadata_path / "settings.yaml")
        assert new.workspace_path == dest.resolve()
        # names.yaml updated.
        assert str(dest) in read_names(std.registry)["projects"].values()
        assert str(pdir) not in read_names(std.registry)["projects"].values()


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
        # settings.yaml at the root.
        assert (dest / "workspace" / "file.txt").read_text() == "combo"
        assert (dest / "settings.yaml").is_file()
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
        assert "project" not in load_doc(ws.projects_dir / "proj" / "settings.yaml")


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

    def _seed_partition(self, std, ws_token, box_name, marker="m"):
        from kanibako.channels import own_partition_dirs

        part = own_partition_dirs(std, ws_token, box_name)
        part.mailbox.mkdir(parents=True, exist_ok=True)
        (part.mailbox / "msg.txt").write_text(marker)
        part.share_global.mkdir(parents=True, exist_ok=True)
        (part.share_global / "pub.txt").write_text(marker)
        return part

    def test_convert_relocates_own_partition(self, env):
        config, std, tmp_home = env
        from kanibako.channels import (
            WS_TOKEN_PRIMARY,
            WS_TOKEN_STANDALONE,
            own_partition_dirs,
        )

        pdir = _make_default(env)
        # Seed THIS box's own partition under the PRIMARY token.
        self._seed_partition(std, WS_TOKEN_PRIMARY, "proj", marker="hello")

        state = resolve_lifecycle_target(str(pdir), std, config)
        new = execute_lifecycle(
            state, TargetSpec(location=INPLACE, ownership="standalone"),
            std, config, confirm=_conf_yes(),
        )
        assert new.mode == BoxMode.standalone

        # OLD partition (PRIMARY) is gone; NEW (STANDALONE) holds the content.
        old = own_partition_dirs(std, WS_TOKEN_PRIMARY, "proj")
        dst = own_partition_dirs(std, WS_TOKEN_STANDALONE, new.name)
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
        from kanibako import box_identity
        from kanibako.channels import (
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
        self._seed_partition(std, WS_TOKEN_PRIMARY, "proj", marker="src")
        # Pre-occupy the destination mailbox (keyed by the canonical name).
        dst_pre = own_partition_dirs(std, WS_TOKEN_STANDALONE, canonical)
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
        """Workset-LOCAL channels (commons/chat) are scope-owned, not moved."""
        config, std, tmp_home = env
        from kanibako.channels import WS_TOKEN_PRIMARY

        pdir = _make_default(env)
        # Seed a PRIMARY workset-local channels tree (scope-owned).
        local = std.primary_workset / "channels" / "commons"
        local.mkdir(parents=True, exist_ok=True)
        (local / "shared.txt").write_text("scope")
        self._seed_partition(std, WS_TOKEN_PRIMARY, "proj")

        state = resolve_lifecycle_target(str(pdir), std, config)
        execute_lifecycle(
            state, TargetSpec(location=INPLACE, ownership="standalone"),
            std, config, confirm=_conf_yes(),
        )
        # Workset-local commons untouched — the box stops MOUNTING it, the dir
        # itself is not relocated.
        assert (local / "shared.txt").read_text() == "scope"


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

        names_before = dict(read_names(std.registry)["projects"])
        meta_before = load_doc(state.metadata_path / "settings.yaml")

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
        assert dict(read_names(std.registry)["projects"]) == names_before
        assert load_doc(state.metadata_path / "settings.yaml") == meta_before

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
        ws2 = load_workset(ws.root)
        assert not any(p.name == "proj" for p in ws2.projects)
        # original default project intact + still resolves as primary in place.
        assert str(pdir) in read_names(std.registry)["projects"].values()
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
        from kanibako.config_interface import _write_nested_toml_key

        _write_nested_toml_key(
            std.boxes / "gonebox" / "settings.yaml",
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
        None when settings.yaml was absent.  Now membership alone suffices — a
        registered box whose settings.yaml is gone still yields a state (enable_
        vault defaulting True via the box-scope reader)."""
        config, std, tmp_home = env
        pdir = _make_default(env, name="nosettings")
        from kanibako.commands.box._lifecycle import _default_state_from_meta

        # Remove the box's settings.yaml (identity no longer self-describes).
        # (Under sparse create a default-vault primary box may never have written
        # one — missing_ok makes the "settings gone" precondition robust.)
        (std.boxes / "nosettings" / "settings.yaml").unlink(missing_ok=True)
        # Sanity: the box dir + registration remain.
        assert (std.boxes / "nosettings").is_dir()

        state = _default_state_from_meta(pdir, std)
        assert state is not None
        assert state.name == "nosettings"
        assert state.enable_vault is True  # default via read_box_enable_vault
