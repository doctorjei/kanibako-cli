"""Tests for resolve_workset_project() in kanibako.settings.paths."""

from __future__ import annotations


import pytest

from kanibako.errors import WorksetError
from kanibako.settings.paths import (
  BoxMode,
  WorksetSpec,
  resolve_workset_project,
)
from kanibako.utils import project_hash
from kanibako.project.workset import add_project, create_workset, default_workset


# ---------------------------------------------------------------------------
# WorksetSpec.from_workset
# ---------------------------------------------------------------------------


class TestWorksetSpecFromWorkset:
  def test_carries_is_default_false(self, std, tmp_home):
    ws = create_workset("named", tmp_home / "worksets" / "named", std)
    spec = WorksetSpec.from_workset(ws)
    assert spec.is_default is False

  def test_carries_is_default_true(self, std):
    spec = WorksetSpec.from_workset(default_workset(std))
    assert spec.is_default is True


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def workset_env(std, config, tmp_home):
  """Create a workset with one project and return (ws, project_name)."""
  ws_root = tmp_home / "worksets" / "my_set"
  ws = create_workset("my_set", ws_root, std)
  source = tmp_home / "original-project"
  source.mkdir()
  add_project(ws, "cool_app", source)
  return ws, "cool_app"


# ---------------------------------------------------------------------------
# TestResolveWorksetProject
# ---------------------------------------------------------------------------


class TestResolveWorksetProject:
  def test_returns_project_paths_with_workset_mode(self, workset_env, std, config):
    ws, name = workset_env
    proj = resolve_workset_project(WorksetSpec.from_workset(ws), name, std, config)
    assert proj.mode is BoxMode.named

  def test_paths_use_project_name_not_hash(self, workset_env, std, config):
    ws, name = workset_env
    proj = resolve_workset_project(WorksetSpec.from_workset(ws), name, std, config)

    assert proj.project_path == ws.workspaces_dir / name
    assert proj.metadata_path == ws.projects_dir / name
    assert proj.shell_path == ws.projects_dir / name / "home"
    assert proj.vault_ro_path == ws.vault_dir / "ro" / name
    assert proj.vault_rw_path == ws.vault_dir / "rw" / name

  def test_project_hash_is_sha256_of_workspace_path(self, workset_env, std, config):
    ws, name = workset_env
    proj = resolve_workset_project(WorksetSpec.from_workset(ws), name, std, config)

    expected = project_hash(str((ws.workspaces_dir / name).resolve()))
    assert proj.project_hash == expected

  def test_project_not_found_raises(self, workset_env, std, config):
    ws, _ = workset_env
    with pytest.raises(WorksetError, match="not found"):
      resolve_workset_project(WorksetSpec.from_workset(ws), "nonexistent", std, config)

  def test_initialize_creates_shell_path(self, workset_env, std, config, credentials_dir):
    ws, name = workset_env
    proj = resolve_workset_project(
      WorksetSpec.from_workset(ws), name, std, config, initialize=True
    )

    assert proj.shell_path.is_dir()

  def test_initialize_does_not_copy_credentials(self, workset_env, std, config, credentials_dir):
    """Credential copy is now handled by target.init_home(), not during init."""
    ws, name = workset_env
    proj = resolve_workset_project(
      WorksetSpec.from_workset(ws), name, std, config, initialize=True
    )

    creds_file = proj.shell_path / ".claude" / ".credentials.json"
    assert not creds_file.exists()

  def test_initialize_bootstraps_shell(self, workset_env, std, config, credentials_dir):
    ws, name = workset_env
    proj = resolve_workset_project(
      WorksetSpec.from_workset(ws), name, std, config, initialize=True
    )

    assert (proj.shell_path / ".bashrc").is_file()
    assert (proj.shell_path / ".profile").is_file()

  def test_no_initialize_skips_creation(self, workset_env, std, config):
    ws, name = workset_env
    proj = resolve_workset_project(
      WorksetSpec.from_workset(ws), name, std, config, initialize=False
    )

    assert not proj.shell_path.is_dir()
    assert not proj.is_new

  def test_is_new_true_on_first_init(self, workset_env, std, config, credentials_dir):
    ws, name = workset_env
    proj = resolve_workset_project(
      WorksetSpec.from_workset(ws), name, std, config, initialize=True
    )
    assert proj.is_new is True

  def test_is_new_false_on_reinit(self, workset_env, std, config, credentials_dir):
    ws, name = workset_env
    resolve_workset_project(WorksetSpec.from_workset(ws), name, std, config, initialize=True)
    proj2 = resolve_workset_project(
      WorksetSpec.from_workset(ws), name, std, config, initialize=True
    )
    assert proj2.is_new is False

  def test_recovery_missing_shell_path(self, workset_env, std, config, credentials_dir):
    ws, name = workset_env
    # First init to create everything.
    proj = resolve_workset_project(
      WorksetSpec.from_workset(ws), name, std, config, initialize=True
    )
    # Delete shell_path to simulate corruption.
    import shutil

    shutil.rmtree(proj.shell_path)
    assert not proj.shell_path.exists()

    # Re-resolve with initialize; recovery should recreate shell_path.
    proj2 = resolve_workset_project(
      WorksetSpec.from_workset(ws), name, std, config, initialize=True
    )
    assert proj2.shell_path.is_dir()

  def test_no_project_path_breadcrumb(self, workset_env, std, config, credentials_dir):
    """Workset projects should NOT create project-path.txt."""
    ws, name = workset_env
    proj = resolve_workset_project(
      WorksetSpec.from_workset(ws), name, std, config, initialize=True
    )
    assert not (proj.metadata_path / "project-path.txt").exists()

  def test_no_vault_gitignore(self, workset_env, std, config, credentials_dir):
    """Workset projects should NOT create .gitignore in vault dirs."""
    ws, name = workset_env
    resolve_workset_project(WorksetSpec.from_workset(ws), name, std, config, initialize=True)
    assert not (ws.vault_dir / "ro" / name / ".gitignore").exists()
    assert not (ws.vault_dir / "rw" / name / ".gitignore").exists()


# ---------------------------------------------------------------------------
# TestWorksetProjectCredentialFlow
# ---------------------------------------------------------------------------

# The global/local shared-path fields were removed in 1.6.0 (Part 4): no
# ``shared/`` dir exists in the target tree; claude shared lives under
# ``agents/<agent>/``.  The shared-path assertions are deleted.


class TestWorksetProjectCredentialFlow:
  def test_no_credentials_during_init(self, workset_env, std, config, credentials_dir):
    """Init no longer copies credentials; that's target.init_home()'s job."""
    ws, name = workset_env
    proj = resolve_workset_project(
      WorksetSpec.from_workset(ws), name, std, config, initialize=True
    )

    creds_file = proj.shell_path / ".claude" / ".credentials.json"
    assert not creds_file.exists()


# ---------------------------------------------------------------------------
# TestIterWorksetProjects
# ---------------------------------------------------------------------------


class TestIterWorksetProjects:
  def test_iter_workset_projects_normal(self, std, config, tmp_home):
    from kanibako.settings.paths import iter_workset_projects

    ws_root = tmp_home / "worksets" / "iter_set"
    ws = create_workset("iter_set", ws_root, std)
    source = tmp_home / "iter-src"
    source.mkdir()
    add_project(ws, "proj_a", source)

    results = iter_workset_projects(std, config)
    assert len(results) == 1
    ws_name, ws_obj, project_list = results[0]
    assert ws_name == "iter_set"
    assert len(project_list) == 1
    assert project_list[0] == ("proj_a", "ok")

  def test_iter_workset_projects_missing_workspace(self, std, config, tmp_home):
    import shutil
    from kanibako.settings.paths import iter_workset_projects

    ws_root = tmp_home / "worksets" / "miss_set"
    ws = create_workset("miss_set", ws_root, std)
    source = tmp_home / "miss-src"
    source.mkdir()
    add_project(ws, "miss_proj", source)
    # Remove workspace dir
    shutil.rmtree(ws.workspaces_dir / "miss_proj")

    results = iter_workset_projects(std, config)
    assert len(results) == 1
    _, _, project_list = results[0]
    assert project_list[0] == ("miss_proj", "missing")

  def test_iter_workset_projects_missing_root(self, std, config, tmp_home, capsys):
    import shutil
    from kanibako.settings.paths import iter_workset_projects

    ws_root = tmp_home / "worksets" / "gone_set"
    create_workset("gone_set", ws_root, std)
    shutil.rmtree(ws_root)

    results = iter_workset_projects(std, config)
    # Gone workset produces warning, no entries
    assert len(results) == 0
    err = capsys.readouterr().err
    assert "Warning" in err


# ---------------------------------------------------------------------------
# Characterization: NAMED-workset fixed path table (Phase 5, no layout axis)
# ---------------------------------------------------------------------------


class TestWorksetFixedPaths:
  """Pin the concrete paths the NAMED fixed table produces (shell under the
  per-project box dir; vault under the workset vault dir).
  """

  def test_named_paths(self, workset_env, std, config, credentials_dir):
    ws, name = workset_env
    proj = resolve_workset_project(
      WorksetSpec.from_workset(ws),
      name,
      std,
      config,
      initialize=True,
    )

    workspace = ws.workspaces_dir / name
    assert proj.project_path == workspace
    assert proj.metadata_path == ws.projects_dir / name
    # Fixed NAMED table: shell under the box dir, vault under the workset.
    assert proj.shell_path == ws.projects_dir / name / "home"
    assert proj.vault_ro_path == ws.vault_dir / "ro" / name
    assert proj.vault_rw_path == ws.vault_dir / "rw" / name


# ---------------------------------------------------------------------------
# R2 downward-defaults: a NAMED box inherits ``box.enable_vault`` from its
# workset tier (spec §0 "Directional view/set across CONTAINMENT levels" +
# §2 "Cascade (override precedence, box wins — FULL bracket)").
# ---------------------------------------------------------------------------


class TestNamedWorksetEnableVaultDownwardDefault:
  """``workset create --no-vault`` writes ``box.enable_vault`` at the WORKSET
  tier; the NAMED resolver must honor it as an overridable default.
  """

  @staticmethod
  def _write_workset_enable_vault(ws, value):
    """Write ``box.enable_vault`` at the workset tier, as ``workset create --no-vault`` does."""
    from kanibako.settings.config_io import dump_doc, load_doc
    from kanibako.settings.paths import workset_settings_path

    path = workset_settings_path(WorksetSpec.from_workset(ws))
    doc = load_doc(path) if path.is_file() else {}
    doc.setdefault("box", {})["enable_vault"] = value
    dump_doc(path, doc)
    return path

  def test_workset_tier_false_reaches_the_box(self, workset_env, std, config):
    ws, name = workset_env
    self._write_workset_enable_vault(ws, False)
    proj = resolve_workset_project(WorksetSpec.from_workset(ws), name, std, config)
    assert proj.vault_enabled() is False

  def test_box_tier_true_overrides_workset_tier_false(self, workset_env, std, config):
    """The contained scope always wins per the cascade (spec §0)."""
    from kanibako.settings.config_io import dump_doc
    from kanibako.settings.paths import BOX_META_FILE

    ws, name = workset_env
    self._write_workset_enable_vault(ws, False)
    box_tier = ws.projects_dir / name / BOX_META_FILE
    box_tier.parent.mkdir(parents=True, exist_ok=True)
    dump_doc(box_tier, {"box": {"enable_vault": True}})
    proj = resolve_workset_project(WorksetSpec.from_workset(ws), name, std, config)
    assert proj.vault_enabled() is True

  def test_explicit_param_still_wins(self, workset_env, std, config):
    ws, name = workset_env
    self._write_workset_enable_vault(ws, False)
    proj = resolve_workset_project(
      WorksetSpec.from_workset(ws), name, std, config, enable_vault=True
    )
    assert proj.vault_enabled() is True

  def test_absent_everywhere_still_defaults_true(self, workset_env, std, config):
    """MUTATION-GUARD: the False above comes from the workset tier, not a moved floor."""
    ws, name = workset_env
    proj = resolve_workset_project(WorksetSpec.from_workset(ws), name, std, config)
    assert proj.vault_enabled() is True

  def test_create_does_not_pin_the_inherited_default(
    self, workset_env, std, config, credentials_dir
  ):
    """Spec ``:868``: sparse — absent from the box file unless THE USER sets it there.

    The inherited workset default must resolve, but must NOT be materialized into
    the box tier, or a later workset edit could never reach the box.
    """
    from kanibako.settings.config_io import load_doc
    from kanibako.settings.paths import BOX_META_FILE

    ws, name = workset_env
    self._write_workset_enable_vault(ws, False)
    proj = resolve_workset_project(
      WorksetSpec.from_workset(ws), name, std, config, initialize=True
    )
    assert proj.vault_enabled() is False

    box_tier = ws.projects_dir / name / BOX_META_FILE
    stored = (load_doc(box_tier).get("box") or {}) if box_tier.is_file() else {}
    assert "enable_vault" not in stored

  def test_create_does_pin_an_explicit_box_flag(self, workset_env, std, config, credentials_dir):
    """``kanibako create --no-vault`` IS the user setting it at box scope — persist it."""
    from kanibako.settings.config_io import load_doc
    from kanibako.settings.paths import BOX_META_FILE

    ws, name = workset_env
    proj = resolve_workset_project(
      WorksetSpec.from_workset(ws), name, std, config, initialize=True, enable_vault=False
    )
    assert proj.vault_enabled() is False

    box_tier = ws.projects_dir / name / BOX_META_FILE
    assert load_doc(box_tier)["box"]["enable_vault"] is False
