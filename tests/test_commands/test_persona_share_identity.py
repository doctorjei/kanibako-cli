"""Disconnected identities share Kanibako stores, never Persona Grata entries."""

from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from kanibako.agent_ref import AgentIdentity
from kanibako.commands import start
from kanibako.launch.templates import stage_layers
from kanibako.plugins.claude import ClaudeTarget
from kanibako.plugins.goose import GooseTarget
from kanibako.settings.agent_config import agent_settings_path, store_dirname
from kanibako.settings.agent_representation import agent_categories_for_node
from kanibako.settings.config_io import dump_doc
from tests.test_commands.test_start import _focused_box, _focused_std


@pytest.fixture(autouse=True)
def no_pg_reads(tmp_path):
  entry = tmp_path / "PG" / "CaseSensitive"
  entry.mkdir(parents=True)
  (entry / "sentinel").write_bytes(b"PG-owned\x00content")
  before = [(p.relative_to(entry), p.read_bytes()) for p in entry.rglob("*") if p.is_file()]
  with (
    patch("kanibako.persona_store.read_registered_persona", side_effect=AssertionError("PG read")),
    patch(
      "kanibako.commands.start._persona_bundle_for", side_effect=AssertionError("legacy read")
    ),
    patch(
      "kanibako.settings.config.resolve_agent_identity", side_effect=AssertionError("selection")
    ),
  ):
    yield entry
  assert [
    (p.relative_to(entry), p.read_bytes()) for p in entry.rglob("*") if p.is_file()
  ] == before
  assert sorted(str(p.relative_to(entry)) for p in entry.rglob("*")) == ["sentinel"]


def _identity(entry, node="scout", target="claude"):
  return AgentIdentity(node, node.title(), "Guide", target.title(), target, entry)


def _declarations(harness, external):
  root = f"{{meta.agent.{harness}.path}}"
  return SimpleNamespace(
    default_common=lambda: {f"agent.{harness}.common": {"~/shared": (root + "/common/data",)}},
    default_seeds=lambda: {f"agent.{harness}.seeded": {"~/seed": (root + "/seed",)}},
    default_category_binds=lambda: {
      f"agent.{harness}.bindings.ro": {
        "~/declared": (root + "/bits", "ro"),
        "~/external": (str(external), "ro"),
      },
    },
  )


@pytest.mark.parametrize("node,harness", [("claude", "goose"), ("scout", "claude")])
def test_resolved_harness_shares_node_paths_and_template_bytes(
  tmp_path, no_pg_reads, node, harness
):
  std = SimpleNamespace(agents=tmp_path / "agents")
  identity = _identity(no_pg_reads, node, harness)
  target = _declarations(harness, tmp_path / "external")
  source = std.agents / harness
  for leaf in ("template/box/home", "canon/handbook", "common/data", "seed", "bits"):
    path = source / leaf
    path.mkdir(parents=True)
    (path / "marker").write_text(leaf)
  start.ensure_persona_share_symlinks(std, node, target, agent_identity=identity)
  node_root = std.agents / node
  for leaf in ("template", "canon", "common", "seed", "bits"):
    assert (node_root / leaf).readlink() == source / leaf
  adapted = agent_categories_for_node(target.default_common(), node_name=node, harness=harness)
  assert adapted[f"agent.{node}.common"]["~/shared"] == (
    f"{{meta.agent.{node}.path}}/common/data",
  )
  external = agent_categories_for_node(
    target.default_category_binds(), node_name=node, harness=harness
  )
  assert external[f"agent.{node}.bindings.ro"]["~/external"] == (str(tmp_path / "external"), "ro")
  assert not (node_root / "external").exists()
  home = tmp_path / "home"
  stage_layers(home, [node_root / "template/box/home"])
  assert (home / "marker").read_text() == "template/box/home"
  assert not (home / "marker").is_symlink()


@pytest.mark.parametrize("kind", ["directory", "file", "link"])
@pytest.mark.parametrize("leaf", ["template", "canon", "common", "seed", "bits"])
def test_existing_node_paths_are_never_replaced(tmp_path, no_pg_reads, kind, leaf):
  std = SimpleNamespace(agents=tmp_path / "agents")
  identity = _identity(no_pg_reads, "claude", "goose")
  (std.agents / "goose" / "canon").mkdir(parents=True)
  path = std.agents / "claude" / leaf
  path.parent.mkdir(parents=True, exist_ok=True)
  owned = tmp_path / "owned"
  owned.mkdir()
  (owned / "marker").write_text("owned")
  if kind == "directory":
    path.mkdir()
    (path / "marker").write_text("owned")
  elif kind == "file":
    path.write_text("owned")
  else:
    path.symlink_to(owned)
  start.ensure_persona_share_symlinks(
    std, identity.node, _declarations("goose", owned), agent_identity=identity
  )
  if kind == "file":
    assert path.read_text() == "owned"
    assert not path.is_symlink()
  else:
    assert (path / "marker").read_text() == "owned"
    assert path.is_symlink() is (kind == "link")
    if kind == "link":
      assert path.readlink() == owned


def test_harness_file_ancestor_is_left_to_source_consumers(tmp_path, no_pg_reads):
  std = SimpleNamespace(agents=tmp_path / "agents")
  common = std.agents / "claude/common"
  common.parent.mkdir(parents=True)
  common.write_text("user-owned")
  start.ensure_persona_share_symlinks(
    std,
    "scout",
    _declarations("claude", tmp_path / "external"),
    agent_identity=_identity(no_pg_reads),
  )
  assert common.read_text() == "user-owned"
  assert not common.is_symlink()
  assert (std.agents / "scout/common").readlink() == common


def test_native_identity_is_a_noop(tmp_path, no_pg_reads):
  std = SimpleNamespace(agents=tmp_path / "agents")
  start.ensure_persona_share_symlinks(
    std,
    "claude",
    Mock(),
    agent_identity=AgentIdentity("claude", "Claude", None, "Claude", "claude", no_pg_reads),
  )
  assert not std.agents.exists()


def test_missing_target_shares_template_and_only_existing_canon(tmp_path, no_pg_reads):
  std = SimpleNamespace(agents=tmp_path / "agents")
  identity = _identity(no_pg_reads)
  start.ensure_persona_share_symlinks(std, "scout", None, agent_identity=identity)
  assert (std.agents / "scout/template").readlink() == std.agents / "claude/template"
  assert not (std.agents / "scout/canon").exists()
  assert not (std.agents / "scout/common").exists()
  (std.agents / "claude/canon").mkdir()
  start.ensure_persona_share_symlinks(std, "scout", None, agent_identity=identity)
  assert (std.agents / "scout/canon").readlink() == std.agents / "claude/canon"


@pytest.mark.parametrize("node", ["guide℘claude", "claude"])
def test_legacy_ref_grammar_is_unchanged(tmp_path, node):
  std = SimpleNamespace(agents=tmp_path / "agents")
  start.ensure_persona_share_symlinks(std, node, None)
  if node == "claude":
    assert not std.agents.exists()
  else:
    assert (std.agents / store_dirname(node) / "template").readlink() == (
      std.agents / "claude/template"
    )


@pytest.mark.usefixtures("config_file")
@pytest.mark.parametrize(
  "node,harness",
  [
    pytest.param(
      "claude",
      "goose",
      marks=pytest.mark.writes_undeclared(
        "agent.claude.provider",
        reason="Injected registered Claude uses Goose; the process-wide census stays legacy-only",
      ),
    ),
    ("scout", "claude"),
  ],
)
@pytest.mark.parametrize("scope", ["default", "node"])
def test_settings_overrides_keep_template_and_canon_sources(
  tmp_path, no_pg_reads, node, harness, scope
):
  std = _focused_std()
  proj = _focused_box(tmp_path)
  identity = _identity(no_pg_reads, node, harness)
  target = GooseTarget() if harness == "goose" else ClaudeTarget()
  roots = {key: tmp_path / key for key in ("template", "canon")}
  (roots["template"] / "box/home").mkdir(parents=True)
  (roots["template"] / "box/home/marker").write_text("override")
  roots["canon"].mkdir()
  values = {key: str(path) for key, path in roots.items()}
  system_path = tmp_path / "settings.yaml"
  agent_path = agent_settings_path(std.agents, node)
  dump_doc(system_path, {"agent": {"default": values}} if scope == "default" else {})
  dump_doc(agent_path, {"self": values} if scope == "node" else {})
  start.ensure_persona_share_symlinks(std, node, target, agent_identity=identity)
  snapshot, _ = start._resolve_launch_snapshot(
    std=std,
    proj=proj,
    agent_name=node,
    agent_identity=identity,
    system_settings_path=system_path,
    agent_cfg_path=agent_path,
    desc=None,
    install=None,
    target=target,
    cli_level={"system.agent": node},
  )
  for key, path in roots.items():
    assert snapshot["agent"][node][key] == str(path)
  start._apply_init_seeds(
    std=std,
    proj=proj,
    agent_name=node,
    agent_identity=identity,
    target=target,
    global_config_path=system_path,
    agent_config_path=agent_path,
    logger=Mock(),
    deliver_creds=False,
    selection_level={"system.agent": node},
  )
  assert (proj.shell_path / "marker").read_text() == "override"
  assert not (proj.shell_path / "marker").is_symlink()
