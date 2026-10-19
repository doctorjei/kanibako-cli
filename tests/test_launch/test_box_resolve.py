"""Tests for the new-model box identity derivation (``kanibako.launch.box_resolve``).

These build boxes DIRECTLY in the NEW-format registries (per-workset
``workset_registry`` ``boxes:`` membership + the global ``standalone:`` index) —
NOT via the legacy ``write_project_meta`` — and exercise the D3-mode precedence,
the path-space registry before the settings file, and the identity sourcing (name = registry KEY,
registered = membership).

Mutation proof (D3-mode precedence): move the settings-file check of
:func:`detect_box_mode` ahead of the path-space registry and
``test_path_space_registry_beats_marker_primary`` goes RED — the box, listed in
the primary registry, is then detected as STANDALONE.
"""

from __future__ import annotations

from pathlib import Path

from kanibako.project import registry_store, workset_registry
from kanibako.launch import box_resolve
from kanibako.settings.config import WORKSET_META_FILE
from kanibako.settings.paths import (
  BoxMode,
  STANDALONE_META_DIR,
  register_primary_box_name,
)


# ---------------------------------------------------------------------------
# Fixture builders (new-format — write straight into the registries)
# ---------------------------------------------------------------------------


def _make_standalone_marker(project_dir: Path) -> None:
  """Give *project_dir* the standalone marker: a root ``registry.yaml`` holding the
  mark (plus the ``workset.yaml`` and ``box_data/`` dir a real box has).
  """
  (project_dir / STANDALONE_META_DIR).mkdir(parents=True, exist_ok=True)
  (project_dir / WORKSET_META_FILE).touch()
  (project_dir / "registry.yaml").write_text("__STANDALONE__\n")


def _make_named_workset(std, root: Path, name: str) -> Path:
  """Register a NAMED workset (global ``worksets:``) rooted at *root*.

  Returns the workset's per-workset registry FILE path.
  """
  root.mkdir(parents=True, exist_ok=True)
  section = registry_store.load_section(std.registry, "worksets")
  section[name] = str(root)
  registry_store.save_section(std.registry, "worksets", section)
  return workset_registry.workset_registry_path(root)


def _register_box(registry_path: Path, box_name: str, box_path: Path) -> None:
  """Add *box_name* → *box_path* to a per-workset registry's ``boxes:``."""
  workset_registry.register_workset_box(registry_path, box_name, box_path)


# ---------------------------------------------------------------------------
# is_standalone_root — the registry.yaml mark, no project.mode read
# ---------------------------------------------------------------------------


class TestIsStandaloneRoot:
  def test_true_when_both_marker_and_settings(self, project_dir):
    _make_standalone_marker(project_dir)
    assert box_resolve.is_standalone_root(project_dir) is True

  def test_false_when_no_settings_file(self, project_dir):
    (project_dir / STANDALONE_META_DIR).mkdir()
    assert box_resolve.is_standalone_root(project_dir) is False

  def test_false_without_the_mark(self, project_dir):
    (project_dir / STANDALONE_META_DIR).mkdir()
    (project_dir / WORKSET_META_FILE).write_text("box: {}\n")
    assert box_resolve.is_standalone_root(project_dir) is False

  def test_does_not_read_project_mode(self, project_dir):
    # A settings file WITHOUT any ``project.mode`` field still counts — the
    # registry.yaml mark is the whole signal (the legacy
    # ``_is_standalone_meta_dir`` required ``box.mode == standalone``).
    (project_dir / STANDALONE_META_DIR).mkdir()
    (project_dir / WORKSET_META_FILE).write_text("box:\n  enable_vault: false\n")
    (project_dir / "registry.yaml").write_text("__STANDALONE__\n")
    assert box_resolve.is_standalone_root(project_dir) is True


# ---------------------------------------------------------------------------
# detect_box_mode — the D3-mode precedence
# ---------------------------------------------------------------------------


class TestDetectBoxMode:
  def test_path_space_registry_beats_marker_primary(self, std, config, tmp_home):
    """S1: a marker AND a primary-registry entry at the path → the PRIMARY box.

    D3-mode 1: the path space's registry comes first.  Mutation proof: check the
    settings file first → STANDALONE → RED.
    """
    box = tmp_home / "p"
    box.mkdir()
    _make_standalone_marker(box)
    register_primary_box_name(std.primary_workset, "p", str(box))

    result = box_resolve.detect_box_mode(box, std, config)
    assert result is not None
    assert result.mode is BoxMode.primary
    assert result.project_root == box

  def test_path_space_registry_beats_marker_named(self, std, config, tmp_home):
    """A marker at a member path INSIDE the named workset's root → the member (W's space)."""
    ws_root = tmp_home / "ws"
    reg = _make_named_workset(std, ws_root, "greedy")
    box = ws_root / "workspaces" / "member"
    box.mkdir(parents=True)
    _make_standalone_marker(box)
    _register_box(reg, "member", box)

    result = box_resolve.detect_box_mode(box, std, config)
    assert result is not None
    assert result.mode is BoxMode.named
    assert result.project_root == box

  def test_a_primary_row_under_a_named_root_stays_the_worksets(self, std, config, tmp_home):
    """The walk reaches a named ROOT above the path, so the workset answers: a primary
    row there (one made before the root became a workset) does not win.  Mutation: ask
    the primary registry under a root too → PRIMARY → RED."""
    ws_root = tmp_home / "wz"
    reg = _make_named_workset(std, ws_root, "wz")
    member = ws_root / "workspaces" / "m"
    _register_box(reg, "m", member)
    for name, q in (("q", ws_root / "q"), ("mq", member / "q")):
      q.mkdir(parents=True)
      register_primary_box_name(std.primary_workset, name, str(q))
      result = box_resolve.detect_box_mode(q, std, config)
      assert result is not None and result.mode is BoxMode.named, q

  def test_a_primary_box_under_an_external_member_stays_itself(self, std, config, tmp_home):
    """An external member's space holds the paths under it; a primary box made there
    before the connect is still the box AT its own path, not the member above it."""
    reg = _make_named_workset(std, tmp_home / "wn", "wn")
    member = tmp_home / "YN"
    p = member / "p"
    p.mkdir(parents=True)
    _register_box(reg, "YN", member)
    register_primary_box_name(std.primary_workset, "p", str(p))
    result = box_resolve.detect_box_mode(p, std, config)
    assert result is not None
    assert (result.mode, result.project_root) == (BoxMode.primary, p)

  def test_other_space_connection_beats_a_leftover_marker(self, std, config, tmp_home):
    """A box is in one workset only (180-DF2 [Jei:2026-10-13]): a marked root CONNECTED into a
    named workset from outside its tree is that workset's NAMED box.  Connect drops the marker
    last, so this state is a connect killed between the two writes.
    """
    box = tmp_home / "declares_standalone"
    box.mkdir()
    _make_standalone_marker(box)
    ws_root = tmp_home / "ws"
    reg = _make_named_workset(std, ws_root, "greedy")
    _register_box(reg, "declares_standalone", box)

    result = box_resolve.detect_box_mode(box, std, config)
    assert result is not None
    assert result.mode is BoxMode.named
    assert result.project_root == box

  def test_standalone_registry_hit_without_marker_is_that_box(self, std, config, tmp_home):
    """S1c: the standalone registry names the path but its ``workset.yaml`` is gone →
    that STANDALONE box (D3-mode 2), never "not a box" (which offers a create).  The
    launch gate then refuses it, naming the file (``test_start``).
    """
    box = tmp_home / "solo"
    box.mkdir()
    registry_store.register_standalone(std.registry, "solo", box)

    result = box_resolve.detect_box_mode(box, std, config)
    assert result is not None
    assert result.mode is BoxMode.standalone
    assert result.project_root == box

  def test_ancestor_primary_box_from_a_subdir(self, std, config, tmp_home):
    """The treewalk's primary ANCESTOR match is a box, not the create default."""
    box = tmp_home / "anc"
    sub = box / "deep" / "sub"
    sub.mkdir(parents=True)
    register_primary_box_name(std.primary_workset, "anc", str(box))
    (std.boxes / "anc").mkdir(parents=True)

    result = box_resolve.detect_box_mode(sub, std, config)
    assert result is not None
    assert result.mode is BoxMode.primary
    assert result.project_root == box

    identity = box_resolve.resolve_box_identity(sub, std, config)
    assert identity is not None
    assert identity["name"] == "anc"
    assert identity["registered"] is True

  def test_standalone_by_presence_no_registry(self, std, config, project_dir):
    _make_standalone_marker(project_dir)
    result = box_resolve.detect_box_mode(project_dir, std, config)
    assert result is not None
    assert result.mode is BoxMode.standalone

  def test_workset_connected_box_no_settings_file(self, std, config, tmp_home):
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

  def test_treewalk_fallthrough_under_workset_root(self, std, config, tmp_home):
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

  def test_standalone_registered_name_from_global_key(self, std, config, project_dir):
    _make_standalone_marker(project_dir)
    registry_store.register_standalone(std.registry, "regname", project_dir.resolve())
    identity = box_resolve.resolve_box_identity(project_dir, std, config)
    assert identity is not None
    assert identity["mode"] is BoxMode.standalone
    assert identity["name"] == "regname"  # the global standalone KEY
    assert identity["registered"] is True
    assert identity["workspace"] == project_dir.resolve()

  def test_standalone_unregistered_name_from_dir_leaf(self, std, config, project_dir):
    _make_standalone_marker(project_dir)
    identity = box_resolve.resolve_box_identity(project_dir, std, config)
    assert identity is not None
    assert identity["mode"] is BoxMode.standalone
    assert identity["name"] == project_dir.resolve().name  # dir leaf
    assert identity["registered"] is False

  def test_standalone_name_composed_live_from_stored_kuid(self, std, config, project_dir):
    # P6d: a stored ``workset.kuid`` composes the name LIVE as
    # ``<kuid>_<current-dir leaf>`` — OVERRIDING even a stale registered name
    # (the leaf tracks the dir; the kuid is the stable prefix). Mutation:
    # revert box_resolve to ``registered_name or box_root.name`` → this goes RED.
    (project_dir / STANDALONE_META_DIR).mkdir(parents=True, exist_ok=True)
    (project_dir / WORKSET_META_FILE).write_text("workset:\n  kuid: abcde\n")
    (project_dir / "registry.yaml").write_text("__STANDALONE__\n")
    registry_store.register_standalone(std.registry, "abcde_oldleaf", project_dir.resolve())
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

  def test_standalone_detected_from_subdir_anchors_on_box_root(self, std, config, tmp_home):
    # F1 regression: when standalone is detected via the TREEWALK from a
    # SUBDIR (case 1 checks project_dir only → miss; case 3
    # detect_project_mode finds the marker at an ANCESTOR), the identity
    # must anchor on the box ROOT (result.project_root), NOT the subdir.
    # detect_project_mode's treewalk marker still reads the legacy
    # ``project.mode`` field, so build that ancestor marker here.
    box_root = tmp_home / "sabox"
    (box_root / STANDALONE_META_DIR).mkdir(parents=True)
    (box_root / WORKSET_META_FILE).write_text("project:\n  mode: standalone\n")
    (box_root / "registry.yaml").write_text("__STANDALONE__\n")
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

  def test_named_orphan_under_workset_is_unregistered(self, std, config, tmp_home):
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

  from tests.support.repo import REPO_ROOT

  src = REPO_ROOT / "src"
  out = subprocess.run(
    ["grep", "-rln", "--include=*.py", "box_resolve", str(src)],
    capture_output=True,
    text=True,
  ).stdout.splitlines()
  referencers = {Path(p).name for p in out}
  assert "paths.py" in referencers, (
    f"box_resolve should be wired into paths.py; referencers: {referencers}"
  )


# ---------------------------------------------------------------------------
# unregistered_box_refusal — the one carrier of the nameless-primary refusal head
# ---------------------------------------------------------------------------


def _proj(mode: BoxMode, name: str, path: str = "/w/here"):
  from types import SimpleNamespace

  return SimpleNamespace(mode=mode, name=name, project_path=Path(path))


def test_unregistered_box_refusal_names_the_phantom():
  head = box_resolve.unregistered_box_refusal(
    _proj(BoxMode.primary, ""), "a vault belongs to a box", "kanibako box vault list <box>"
  )
  assert head == (
    "no box at /w/here — kanibako has no box registered for this directory, "
    "and a vault belongs to a box.\n"
    "  Name the box:   kanibako box vault list <box>\n"
  )


def test_unregistered_box_refusal_passes_a_real_box():
  for proj in (
    _proj(BoxMode.primary, "alpha"),
    _proj(BoxMode.named, ""),
    _proj(BoxMode.standalone, ""),
  ):
    assert box_resolve.unregistered_box_refusal(proj, "x", "y") is None
