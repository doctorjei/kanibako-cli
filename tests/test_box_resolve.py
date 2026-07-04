"""Tests for the new-model box identity derivation (``kanibako.box_resolve``).

These build boxes DIRECTLY in the NEW-format registries (per-workset
``workset_registry`` ``boxes:`` membership + the global ``standalone:`` index) —
NOT via the legacy ``write_project_meta`` — and exercise the D3-mode precedence,
the steal-prevention override, and the identity sourcing (name = registry KEY,
registered = membership).

Mutation proof (D3-mode precedence): reorder :func:`detect_box_mode` cases 1 and
2 (workset scan BEFORE the settings-file check) and
``test_steal_prevention_settings_file_overrides_workset`` goes RED — the box,
being path-listed in a workset registry, is then detected as the workset's mode
instead of STANDALONE.
"""

from __future__ import annotations

from pathlib import Path

from kanibako import box_resolve, registry_store, workset_registry
from kanibako.config import BOX_META_FILE
from kanibako.paths import BoxMode, _STANDALONE_META_DIR


# ---------------------------------------------------------------------------
# Fixture builders (new-format — write straight into the registries)
# ---------------------------------------------------------------------------

def _make_standalone_marker(project_dir: Path) -> None:
    """Give *project_dir* the presence-only standalone marker.

    The standalone meta dir + the box settings file — NO ``project.mode`` field
    (the new-model marker is FILE PRESENCE, design D4).
    """
    (project_dir / _STANDALONE_META_DIR).mkdir(parents=True, exist_ok=True)
    (project_dir / BOX_META_FILE).write_text("box: {}\n")


def _make_named_workset(std, root: Path, name: str) -> Path:
    """Register a NAMED workset (global ``worksets:``) rooted at *root*.

    Returns the workset's per-workset registry FILE path.
    """
    root.mkdir(parents=True, exist_ok=True)
    section = registry_store.load_section(std.registry, "worksets")
    section[name] = str(root)
    registry_store.save_section(std.registry, "worksets", section)
    return workset_registry.resolve_workset_registry_path(root, None)


def _register_box(registry_path: Path, box_name: str, box_path: Path) -> None:
    """Add *box_name* → *box_path* to a per-workset registry's ``boxes:``."""
    workset_registry.register_workset_box(registry_path, box_name, box_path)


# ---------------------------------------------------------------------------
# standalone_settings_present — presence only, no project.mode read
# ---------------------------------------------------------------------------

class TestStandaloneSettingsPresent:
    def test_true_when_both_marker_and_settings(self, project_dir):
        _make_standalone_marker(project_dir)
        assert box_resolve.standalone_settings_present(project_dir) is True

    def test_false_when_no_settings_file(self, project_dir):
        (project_dir / _STANDALONE_META_DIR).mkdir()
        assert box_resolve.standalone_settings_present(project_dir) is False

    def test_false_when_no_meta_dir(self, project_dir):
        (project_dir / BOX_META_FILE).write_text("box: {}\n")
        assert box_resolve.standalone_settings_present(project_dir) is False

    def test_does_not_read_project_mode(self, project_dir):
        # A settings file WITHOUT any ``project.mode`` field still counts —
        # presence is the whole signal (would have been False under the legacy
        # ``_is_standalone_meta_dir`` which required ``box.mode == standalone``).
        (project_dir / _STANDALONE_META_DIR).mkdir()
        (project_dir / BOX_META_FILE).write_text("box:\n  enable_vault: false\n")
        assert box_resolve.standalone_settings_present(project_dir) is True


# ---------------------------------------------------------------------------
# find_owning_workset — enumerate + scan per-workset registries
# ---------------------------------------------------------------------------

class TestFindOwningWorkset:
    def test_named_workset_owns_box(self, std, config, tmp_home):
        ws_root = tmp_home / "ws"
        reg = _make_named_workset(std, ws_root, "clientwork")
        box = tmp_home / "external" / "repo"
        box.mkdir(parents=True)
        _register_box(reg, "repo", box)

        found = box_resolve.find_owning_workset(box, std, config)
        assert found is not None
        name, root, mode = found
        assert name == "clientwork"
        assert root == ws_root
        assert mode is BoxMode.named

    def test_primary_workset_owns_box(self, std, config, tmp_home):
        # PRIMARY is enumerated explicitly (anchored by config.primary_workset).
        reg = workset_registry.resolve_workset_registry_path(
            std.primary_workset, None
        )
        box = tmp_home / "primarybox"
        box.mkdir()
        _register_box(reg, "primarybox", box)

        found = box_resolve.find_owning_workset(box, std, config)
        assert found is not None
        name, root, mode = found
        assert mode is BoxMode.primary
        assert root == std.primary_workset

    def test_none_when_not_a_member(self, std, config, project_dir):
        assert box_resolve.find_owning_workset(project_dir, std, config) is None

    def test_path_match_normalizes_both_sides(self, std, config, tmp_home):
        # Registry stores a NON-normalized path with a '..' segment (pathlib does
        # NOT collapse '..' at construction — only .resolve() does, using the
        # filesystem); the caller passes a plain path. The match works ONLY
        # because _find_owning_box .resolve()s both sides.
        # Mutation-proven load-bearing: drop the .resolve() in _find_owning_box
        # → the '..' entry no longer equals project_dir → this goes RED.
        ws_root = tmp_home / "ws"
        reg = _make_named_workset(std, ws_root, "w")
        box = tmp_home / "ext" / "repo"
        box.mkdir(parents=True)
        # ``.../ext/repo/../repo`` — resolves to ``.../ext/repo`` == box.
        _register_box(reg, "repo", box / ".." / box.name)

        found = box_resolve.find_owning_workset(box, std, config)
        assert found is not None and found[0] == "w"


# ---------------------------------------------------------------------------
# detect_box_mode — the D3-mode precedence
# ---------------------------------------------------------------------------

class TestDetectBoxMode:
    def test_steal_prevention_settings_file_overrides_workset(
        self, std, config, tmp_home
    ):
        """THE key case: a dir that BOTH has an in-place settings file AND is
        path-listed in a workset registry → STANDALONE, not the workset.

        Mutation proof: reorder cases 1 and 2 in ``detect_box_mode`` (scan the
        workset before the settings-file check) → this asserts STANDALONE but
        gets ``named`` → RED.
        """
        box = tmp_home / "declares_standalone"
        box.mkdir()
        _make_standalone_marker(box)
        # ALSO list it in a workset's registry (the "steal" attempt).
        ws_root = tmp_home / "ws"
        reg = _make_named_workset(std, ws_root, "greedy")
        _register_box(reg, "declares_standalone", box)

        result = box_resolve.detect_box_mode(box, std, config)
        assert result is not None
        assert result.mode is BoxMode.standalone
        assert result.project_root == box.resolve()
        # And the workset genuinely WOULD have claimed it (guards the test's
        # own validity — without the settings-file precedence this is 'named').
        assert box_resolve.find_owning_workset(box, std, config) is not None

    def test_standalone_by_presence_no_registry(self, std, config, project_dir):
        _make_standalone_marker(project_dir)
        result = box_resolve.detect_box_mode(project_dir, std, config)
        assert result is not None
        assert result.mode is BoxMode.standalone

    def test_workset_connected_box_no_settings_file(
        self, std, config, tmp_home
    ):
        # No in-place settings file; path listed in workset W's registry.
        ws_root = tmp_home / "ws"
        reg = _make_named_workset(std, ws_root, "W")
        box = tmp_home / "ext" / "repo"
        box.mkdir(parents=True)
        _register_box(reg, "repo", box)

        result = box_resolve.detect_box_mode(box, std, config)
        assert result is not None
        assert result.mode is BoxMode.named
        assert result.project_root == box.resolve()

    def test_treewalk_fallthrough_under_workset_root(
        self, std, config, tmp_home
    ):
        # cwd under a registered workset root, NO settings file, NO registry
        # entry → detect_project_mode's workset-containment → named.
        ws_root = tmp_home / "ws"
        _make_named_workset(std, ws_root, "contain")  # registered, no box entry
        target = ws_root / "workspaces" / "somebox"
        target.mkdir(parents=True)

        result = box_resolve.detect_box_mode(target, std, config)
        assert result is not None
        assert result.mode is BoxMode.named

    def test_not_a_box_empty_dir_is_none(self, std, config, project_dir):
        # Empty dir, no marker, no membership, not under any workset →
        # detect_project_mode defaults to primary (its no-marker case) → None.
        assert box_resolve.detect_box_mode(project_dir, std, config) is None


# ---------------------------------------------------------------------------
# resolve_box_identity — name from registry KEY, registered from membership
# ---------------------------------------------------------------------------

class TestResolveBoxIdentity:
    def test_none_when_not_a_box(self, std, config, project_dir):
        assert box_resolve.resolve_box_identity(project_dir, std, config) is None

    def test_workset_box_name_is_registry_key(self, std, config, tmp_home):
        ws_root = tmp_home / "ws"
        reg = _make_named_workset(std, ws_root, "W")
        box = tmp_home / "ext" / "repo"
        box.mkdir(parents=True)
        # Registry KEY deliberately DIFFERS from the on-disk dir leaf to prove
        # the name is sourced from the KEY, not the path/dir.
        _register_box(reg, "keyname", box)

        identity = box_resolve.resolve_box_identity(box, std, config)
        assert identity is not None
        assert identity["mode"] is BoxMode.named
        assert identity["name"] == "keyname"  # the KEY, not "repo"
        assert identity["workspace"] == box.resolve()
        assert identity["registered"] is True

    def test_standalone_registered_name_from_global_key(
        self, std, config, project_dir
    ):
        _make_standalone_marker(project_dir)
        registry_store.register_standalone(
            std.registry, "regname", project_dir.resolve()
        )
        identity = box_resolve.resolve_box_identity(project_dir, std, config)
        assert identity is not None
        assert identity["mode"] is BoxMode.standalone
        assert identity["name"] == "regname"  # the global standalone KEY
        assert identity["registered"] is True
        assert identity["workspace"] == project_dir.resolve()

    def test_standalone_unregistered_name_from_dir_leaf(
        self, std, config, project_dir
    ):
        _make_standalone_marker(project_dir)
        identity = box_resolve.resolve_box_identity(project_dir, std, config)
        assert identity is not None
        assert identity["mode"] is BoxMode.standalone
        assert identity["name"] == project_dir.resolve().name  # dir leaf
        assert identity["registered"] is False

    def test_standalone_name_composed_live_from_stored_kuid(
        self, std, config, project_dir
    ):
        # P6d: a stored ``workset.kuid`` composes the name LIVE as
        # ``<kuid>_<current-dir leaf>`` — OVERRIDING even a stale registered name
        # (the leaf tracks the dir; the kuid is the stable prefix). Mutation:
        # revert box_resolve to ``registered_name or box_root.name`` → this goes RED.
        (project_dir / _STANDALONE_META_DIR).mkdir(parents=True, exist_ok=True)
        (project_dir / BOX_META_FILE).write_text("workset:\n  kuid: abcde\n")
        registry_store.register_standalone(
            std.registry, "abcde_oldleaf", project_dir.resolve()
        )
        identity = box_resolve.resolve_box_identity(project_dir, std, config)
        assert identity is not None
        assert identity["mode"] is BoxMode.standalone
        assert identity["name"] == f"abcde_{project_dir.resolve().name}"
        assert identity["registered"] is True  # still a registry member

    def test_registered_flag_reflects_membership(self, std, config, tmp_home):
        # A workset box: registry membership present → registered True.
        ws_root = tmp_home / "ws"
        reg = _make_named_workset(std, ws_root, "W")
        box = tmp_home / "ext" / "repo"
        box.mkdir(parents=True)
        _register_box(reg, "repo", box)
        identity = box_resolve.resolve_box_identity(box, std, config)
        assert identity is not None and identity["registered"] is True

    def test_standalone_detected_from_subdir_anchors_on_box_root(
        self, std, config, tmp_home
    ):
        # F1 regression: when standalone is detected via the TREEWALK from a
        # SUBDIR (case 1 checks project_dir only → miss; case 3
        # detect_project_mode finds the marker at an ANCESTOR), the identity
        # must anchor on the box ROOT (result.project_root), NOT the subdir.
        # detect_project_mode's treewalk marker still reads the legacy
        # ``project.mode`` field, so build that ancestor marker here.
        box_root = tmp_home / "sabox"
        (box_root / _STANDALONE_META_DIR).mkdir(parents=True)
        (box_root / BOX_META_FILE).write_text("project:\n  mode: standalone\n")
        registry_store.register_standalone(std.registry, "sakey", box_root.resolve())
        subdir = box_root / "deep" / "sub"
        subdir.mkdir(parents=True)

        identity = box_resolve.resolve_box_identity(subdir, std, config)
        assert identity is not None
        assert identity["mode"] is BoxMode.standalone
        # Anchored on the ROOT: registered name + root workspace, NOT the subdir.
        assert identity["name"] == "sakey"
        assert identity["workspace"] == box_root.resolve()
        assert identity["registered"] is True

    def test_named_orphan_under_workset_is_unregistered(
        self, std, config, tmp_home
    ):
        # Under a workset root but NOT a registry member (D3-auth orphan):
        # detected named via treewalk, registered=False, layout-derived name.
        ws_root = tmp_home / "ws"
        _make_named_workset(std, ws_root, "contain")
        target = ws_root / "workspaces" / "orphanbox"
        target.mkdir(parents=True)

        identity = box_resolve.resolve_box_identity(target, std, config)
        assert identity is not None
        assert identity["mode"] is BoxMode.named
        assert identity["registered"] is False
        assert identity["name"] == "orphanbox"


# ---------------------------------------------------------------------------
# P5a wiring guard — the resolvers now consume box_resolve (was P4-additive)
# ---------------------------------------------------------------------------

def test_box_resolve_is_wired_into_the_resolvers():
    """P5a cut the resolvers over to box_resolve.  Confirm ``paths.py`` (the
    resolver home) references it — the inverse of the retired P4 "imported by
    nothing" additive guard."""
    import subprocess

    src = Path(__file__).resolve().parents[1] / "src"
    out = subprocess.run(
        ["grep", "-rln", "--include=*.py", "box_resolve", str(src)],
        capture_output=True, text=True,
    ).stdout.splitlines()
    referencers = {Path(p).name for p in out}
    assert "paths.py" in referencers, (
        f"box_resolve should be wired into paths.py; referencers: {referencers}"
    )
