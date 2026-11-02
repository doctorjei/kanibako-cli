"""Tests for Claude credential functions (kanibako.plugins.claude.credentials)."""

from __future__ import annotations

import json
import logging

import pytest

from kanibako.plugins.claude import ClaudeTarget
from kanibako.plugins.claude.credentials import (
  merge_oauth_account_out,
  merge_oauth_in,
)


class TestMergeOauthIn:
  def test_creates_project_from_host(self, tmp_path):
    host = tmp_path / "host" / ".credentials.json"
    host.parent.mkdir()
    host.write_text(json.dumps({"claudeAiOauth": {"token": "abc"}}))

    project = tmp_path / "project" / ".credentials.json"
    project.parent.mkdir()

    assert merge_oauth_in(host, project)
    data = json.loads(project.read_text())
    assert data["claudeAiOauth"]["token"] == "abc"

  def test_merges_oauth_key(self, tmp_path):
    host = tmp_path / "host" / ".credentials.json"
    host.parent.mkdir()
    host.write_text(json.dumps({"claudeAiOauth": {"token": "new"}}))

    project = tmp_path / "project" / ".credentials.json"
    project.parent.mkdir()
    project.write_text(json.dumps({"claudeAiOauth": {"token": "old"}, "otherKey": True}))

    assert merge_oauth_in(host, project)
    data = json.loads(project.read_text())
    assert data["claudeAiOauth"]["token"] == "new"
    assert data["otherKey"] is True


# NOTE: the host .claude.json allowlist filter (filter_settings) was removed in
# 1.6.0 along with the host-config import; its tests are deleted.


# ---------------------------------------------------------------------------
# merge_oauth_in — error paths
# ---------------------------------------------------------------------------


class TestMergeOauthInErrors:
  def test_malformed_host_json(self, tmp_path):
    host = tmp_path / "host" / ".credentials.json"
    host.parent.mkdir(parents=True)
    host.write_text("{bad json!!")

    project = tmp_path / "project" / ".credentials.json"
    project.parent.mkdir(parents=True)
    project.write_text(json.dumps({"claudeAiOauth": {"token": "old"}}))

    result = merge_oauth_in(host, project)
    assert result is False
    # Project file unchanged
    data = json.loads(project.read_text())
    assert data["claudeAiOauth"]["token"] == "old"

  def test_empty_host_file(self, tmp_path):
    host = tmp_path / "host" / ".credentials.json"
    host.parent.mkdir(parents=True)
    host.write_text("")

    project = tmp_path / "project" / ".credentials.json"
    project.parent.mkdir(parents=True)
    project.write_text(json.dumps({"claudeAiOauth": {"token": "old"}}))

    result = merge_oauth_in(host, project)
    assert result is False

  def test_missing_oauth_key(self, tmp_path):
    host = tmp_path / "host" / ".credentials.json"
    host.parent.mkdir(parents=True)
    host.write_text(json.dumps({"someOther": "data"}))

    project = tmp_path / "project" / ".credentials.json"
    project.parent.mkdir(parents=True)
    project.write_text(json.dumps({"claudeAiOauth": {"token": "old"}}))

    result = merge_oauth_in(host, project)
    assert result is False

  def test_malformed_project_json(self, tmp_path):
    """When project JSON is malformed, it gets replaced with fresh data."""
    host = tmp_path / "host" / ".credentials.json"
    host.parent.mkdir(parents=True)
    host.write_text(json.dumps({"claudeAiOauth": {"token": "new"}}))

    project = tmp_path / "project" / ".credentials.json"
    project.parent.mkdir(parents=True)
    project.write_text("{bad json")

    result = merge_oauth_in(host, project)
    assert result is True
    data = json.loads(project.read_text())
    assert data["claudeAiOauth"]["token"] == "new"

  def test_empty_project_file(self, tmp_path):
    host = tmp_path / "host" / ".credentials.json"
    host.parent.mkdir(parents=True)
    host.write_text(json.dumps({"claudeAiOauth": {"token": "new"}}))

    project = tmp_path / "project" / ".credentials.json"
    project.parent.mkdir(parents=True)
    project.write_text("")

    result = merge_oauth_in(host, project)
    assert result is True
    data = json.loads(project.read_text())
    assert data["claudeAiOauth"]["token"] == "new"

  def test_project_missing_wholesale_copy(self, tmp_path):
    """When project creds don't exist, host is copied wholesale."""
    host = tmp_path / "host" / ".credentials.json"
    host.parent.mkdir(parents=True)
    host.write_text(json.dumps({"claudeAiOauth": {"token": "x"}, "extra": True}))

    project = tmp_path / "project" / ".credentials.json"
    # project doesn't exist
    result = merge_oauth_in(host, project)
    assert result is True
    assert project.exists()
    data = json.loads(project.read_text())
    assert data["claudeAiOauth"]["token"] == "x"
    assert data["extra"] is True


class TestMergeOauthAccountOut:
  """FIX 1: reverse-merge box ``.claude.json`` oauthAccount into the host's."""

  def test_creates_host_when_absent(self, tmp_path):
    box = tmp_path / "box" / ".claude.json"
    box.parent.mkdir(parents=True)
    box.write_text(json.dumps({"oauthAccount": {"emailAddress": "a@b.c"}}))
    host = tmp_path / "home" / ".claude.json"  # absent

    assert merge_oauth_account_out(box, host, box_root=box.parent) is True
    assert host.exists()
    data = json.loads(host.read_text())
    assert data["oauthAccount"]["emailAddress"] == "a@b.c"

  def test_merges_without_clobbering_machine_fields(self, tmp_path):
    box = tmp_path / "box" / ".claude.json"
    box.parent.mkdir(parents=True)
    # Box also has machine-specific fields that MUST NOT reach the host.
    box.write_text(
      json.dumps(
        {
          "oauthAccount": {"emailAddress": "new@b.c"},
          "machineID": "box-machine",
          "userID": "box-user",
          "projects": {"/box/path": {}},
        }
      )
    )
    host = tmp_path / "home" / ".claude.json"
    host.parent.mkdir(parents=True)
    host.write_text(
      json.dumps(
        {
          "oauthAccount": {"emailAddress": "old@b.c"},
          "machineID": "host-machine",
          "userID": "host-user",
          "projects": {"/host/path": {}},
          "otherHostKey": 123,
        }
      )
    )

    assert merge_oauth_account_out(box, host, box_root=box.parent) is True
    data = json.loads(host.read_text())
    # oauthAccount updated...
    assert data["oauthAccount"]["emailAddress"] == "new@b.c"
    # ...but host machine-specific fields untouched (NOT clobbered).
    assert data["machineID"] == "host-machine"
    assert data["userID"] == "host-user"
    assert data["projects"] == {"/host/path": {}}
    assert data["otherHostKey"] == 123

  def test_noop_when_box_file_absent(self, tmp_path):
    box = tmp_path / "box" / ".claude.json"  # absent
    host = tmp_path / "home" / ".claude.json"
    assert merge_oauth_account_out(box, host, box_root=box.parent) is False
    assert not host.exists()

  def test_noop_when_no_oauth_account_key(self, tmp_path):
    box = tmp_path / "box" / ".claude.json"
    box.parent.mkdir(parents=True)
    box.write_text(json.dumps({"machineID": "x"}))
    host = tmp_path / "home" / ".claude.json"
    assert merge_oauth_account_out(box, host, box_root=box.parent) is False
    assert not host.exists()

  def test_malformed_box_json_is_safe(self, tmp_path):
    box = tmp_path / "box" / ".claude.json"
    box.parent.mkdir(parents=True)
    box.write_text("{not json")
    host = tmp_path / "home" / ".claude.json"
    assert merge_oauth_account_out(box, host, box_root=box.parent) is False

  def test_unreadable_host_not_clobbered(self, tmp_path):
    box = tmp_path / "box" / ".claude.json"
    box.parent.mkdir(parents=True)
    box.write_text(json.dumps({"oauthAccount": {"e": 1}}))
    host = tmp_path / "home" / ".claude.json"
    host.parent.mkdir(parents=True)
    host.write_text("{corrupt")
    # Bails rather than overwrite an unreadable host file.
    assert merge_oauth_account_out(box, host, box_root=box.parent) is False
    assert host.read_text() == "{corrupt"

  @pytest.mark.parametrize("to", ["outside", "inside"])
  def test_box_file_link_is_never_followed(self, tmp_path, to, caplog):
    """No-follow patch (B2): a box ``.claude.json`` that is a symlink -- to a host
    file outside the box or a file inside it -- is not read; the host file is
    untouched, the linked file too, and the warning names the box path."""
    root = tmp_path / "box"
    root.mkdir()
    held = tmp_path / "outside.json" if to == "outside" else root / "elsewhere.json"
    held.write_text(json.dumps({"oauthAccount": {"emailAddress": "leak@x"}}))
    before = held.read_bytes()
    box = root / ".claude.json"
    box.symlink_to(held)
    host = tmp_path / "home" / ".claude.json"
    with caplog.at_level(logging.WARNING):
      assert merge_oauth_account_out(box, host, box_root=root) is False
    assert not host.exists()
    assert held.read_bytes() == before
    assert str(box) in caplog.text and "symlink" in caplog.text


class TestWritebackExtra:
  """ClaudeTarget.writeback_extra wires merge_oauth_account_out."""

  def test_writeback_extra_merges_oauth_account(self, tmp_path):
    project_home = tmp_path / "project"
    host_home = tmp_path / "host"
    (project_home).mkdir()
    (host_home).mkdir()
    (project_home / ".claude.json").write_text(
      json.dumps({"oauthAccount": {"emailAddress": "z@z.z"}})
    )
    ClaudeTarget().writeback_extra(
      project_home=project_home,
      host_home=host_home,
    )
    host = host_home / ".claude.json"
    assert host.exists()
    assert json.loads(host.read_text())["oauthAccount"]["emailAddress"] == "z@z.z"

  def test_writeback_extra_skips_a_linked_box_file(self, tmp_path):
    project_home = tmp_path / "project"
    host_home = tmp_path / "host"
    project_home.mkdir()
    host_home.mkdir()
    secret = tmp_path / "secret.json"
    secret.write_text(json.dumps({"oauthAccount": {"emailAddress": "leak@x"}}))
    (project_home / ".claude.json").symlink_to(secret)
    ClaudeTarget().writeback_extra(project_home=project_home, host_home=host_home)
    assert not (host_home / ".claude.json").exists()


class TestCredentialConfigTransport:
  def test_missing_destination_copy_keeps_source_metadata(self, tmp_path):
    import os

    source = tmp_path / "source.json"
    source.write_text('{"claudeAiOauth": {}, "unknown": true}')
    source.chmod(0o640)
    os.utime(source, ns=(1000000000, 2000000000))
    link = tmp_path / "source-link.json"
    link.symlink_to(source)
    destination = tmp_path / "new" / ".credentials.json"
    assert merge_oauth_in(link, destination)
    assert destination.read_bytes() == source.read_bytes()
    assert destination.stat().st_mode == source.stat().st_mode
    assert destination.stat().st_mtime_ns == source.stat().st_mtime_ns
    assert link.is_symlink()

  def test_merge_keeps_fixed_temp_mode_and_unknown_destination_keys(self, tmp_path):
    source = tmp_path / "source.json"
    source.write_text('{"claudeAiOauth": {}, "host_only": true}')
    destination = tmp_path / "credentials.json"
    destination.write_text('{"unknown": [1, 2]}')
    temporary = destination.with_suffix(".json.tmp")
    temporary.write_text("old")
    temporary.chmod(0o640)
    before = temporary.stat()
    assert merge_oauth_in(source, destination)
    assert json.loads(destination.read_text()) == {"unknown": [1, 2], "claudeAiOauth": {}}
    assert destination.stat().st_ino == before.st_ino
    assert destination.stat().st_mode == before.st_mode
    assert not temporary.exists()

  def test_account_failed_replace_keeps_host_and_written_temp(self, tmp_path, monkeypatch):
    from pathlib import Path

    box = tmp_path / "box" / ".claude.json"
    box.parent.mkdir()
    box.write_text('{"oauthAccount": {"synthetic": true}}')
    host = tmp_path / "host" / ".claude.json"
    host.parent.mkdir()
    host.write_text('{"unknown": true}')
    before = host.read_bytes()
    temporary = host.with_suffix(".json.tmp")
    original_replace = Path.replace

    def fail(path, target):
      if path == temporary:
        raise PermissionError("synthetic replacement denial")
      return original_replace(path, target)

    monkeypatch.setattr(Path, "replace", fail)
    assert merge_oauth_account_out(box, host, box_root=box.parent) is False
    assert host.read_bytes() == before
    assert json.loads(temporary.read_text()) == {
      "unknown": True,
      "oauthAccount": {"synthetic": True},
    }

  @pytest.mark.parametrize("operation", ["in", "account-out"])
  def test_invalid_host_utf8_retains_existing_exception(self, tmp_path, operation):
    source = tmp_path / "source.json"
    destination = tmp_path / "destination.json"
    if operation == "in":
      source.write_bytes(b"\xff")
      destination.write_text("{}")
      with pytest.raises(UnicodeDecodeError):
        merge_oauth_in(source, destination)
      assert destination.read_text() == "{}"
    else:
      source.write_text('{"oauthAccount": {}}')
      destination.write_bytes(b"\xff")
      with pytest.raises(UnicodeDecodeError):
        merge_oauth_account_out(source, destination, box_root=tmp_path)
      assert destination.read_bytes() == b"\xff"

  def test_account_parent_link_below_box_root_is_not_followed(self, tmp_path, caplog):
    root = tmp_path / "box"
    root.mkdir()
    held = tmp_path / "outside"
    held.mkdir()
    source = held / ".claude.json"
    source.write_text('{"oauthAccount": {}}')
    (root / "linked").symlink_to(held, target_is_directory=True)
    host = tmp_path / "host.json"
    host.write_text('{"unknown": true}')
    with caplog.at_level(logging.WARNING):
      assert not merge_oauth_account_out(root / "linked" / source.name, host, box_root=root)
    assert host.read_text() == '{"unknown": true}'
    assert source.read_text() == '{"oauthAccount": {}}'
    assert "symlink" in caplog.text

  @pytest.mark.parametrize("host_link", [False, True])
  def test_real_staging_consumers_keep_merge_backup_mode_and_times(self, tmp_path, host_link):
    import os
    import stat

    from kanibako.targets.credsync import refresh_cred_files, writeback_cred_files

    target = ClaudeTarget()
    host, box = tmp_path / "host", tmp_path / "box"
    rel = ".claude/.credentials.json"
    store = host / rel
    store.parent.mkdir(parents=True)
    actual = tmp_path / "actual-store.json" if host_link else store
    original = {"claudeAiOauth": {"phase": 1}, "host_unknown": True}
    actual.write_text(json.dumps(original))
    actual.chmod(0o640)
    os.utime(actual, (200, 200))
    if host_link:
      store.symlink_to(actual)
    project = box / rel
    project.parent.mkdir(parents=True)
    project.write_text('{"claudeAiOauth": {}, "box_unknown": true}')
    os.utime(project, (100, 100))

    refresh_cred_files(target.descriptor, target, source_root=host, project_home=box)
    assert json.loads(project.read_text()) == {
      "claudeAiOauth": {"phase": 1},
      "box_unknown": True,
    }
    assert stat.S_IMODE(project.stat().st_mode) == 0o600
    project.write_text('{"claudeAiOauth": {"phase": 2}, "box_unknown": true}')
    os.utime(project, ns=(300000000000, 400000000000))
    box_mtime = project.stat().st_mtime_ns

    writeback_cred_files(target.descriptor, target, source_root=host, project_home=box)
    assert json.loads(actual.read_text()) == {
      "claudeAiOauth": {"phase": 2},
      "box_unknown": True,
    }
    assert stat.S_IMODE(actual.stat().st_mode) == 0o640
    assert actual.stat().st_mtime_ns == box_mtime
    backup = store.with_name(store.name + ".bak")
    assert json.loads(backup.read_text()) == original
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600
    assert store.is_symlink() is host_link
