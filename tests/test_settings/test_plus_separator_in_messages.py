"""A persona node reaches every user-facing message in its ``+`` spelling, never ``℘``.

``℘`` is the keyspace-internal separator; a message names a node the way the user types
it (``agent_ref.display_agent_ref``).  One test per carrier that interpolates a node.
"""

from __future__ import annotations

import pytest

from kanibako import cli
from kanibako.settings.settings_resolve import SettingsError


def _run(argv: list[str], capsys) -> str:
  capsys.readouterr()
  try:
    cli.main(argv)
  except SystemExit:
    pass
  captured = capsys.readouterr()
  return captured.out + captured.err


class TestThroughTheCli:
  def test_an_unknown_persona_names_its_node_with_plus(self, config_file, capsys):
    out = _run(["system", "get", "agent.nav+zzz.model"], capsys)
    assert "'nav+zzz' is not a valid agent" in out, out
    assert "℘" not in out, out

  def test_an_illegal_access_tier_names_the_key_with_plus(self, config_file, capsys):
    out = _run(["system", "set", "agent.navigator+claude.access=bogus"], capsys)
    assert "agent.navigator+claude.access must be one of" in out, out
    assert "℘" not in out, out

  def test_a_pref_set_at_the_wrong_scope_names_both_keys_with_plus(
    self, config_file, capsys,
  ):
    out = _run(["system", "set", "pref.agent.navigator+claude.access=editing"], capsys)
    assert "'pref.agent.navigator+claude.access' cannot be set" in out, out
    assert "Set 'agent.navigator+claude.access' directly" in out, out
    assert "℘" not in out, out

  def test_an_agent_file_refusal_hands_back_a_pasteable_cure(
    self, credentials_dir, capsys,
  ):
    store = credentials_dir / "agents" / "nav+claude"
    store.mkdir(parents=True)
    (store / "agent.yaml").write_text("self:\n  model: opus\n  zippity: 1\n")
    out = _run(["agent", "get", "nav+claude", "model"], capsys)
    assert "\n  kanibako agent reset nav+claude --all\n" in out, out
    assert "the agent settings file for 'nav+claude'" in out, out
    assert "℘" not in out, out

  def test_an_agent_file_store_path_names_its_node_with_plus(
    self, credentials_dir, capsys,
  ):
    store = credentials_dir / "agents" / "nav+claude"
    store.mkdir(parents=True)
    (store / "agent.yaml").write_text("self:\n  bindings:\n    zz: {}\n")
    out = _run(["agent", "get", "nav+claude", "model"], capsys)
    assert "carries 'agent.nav+claude.bindings.zz'" in out, out
    assert "℘" not in out, out


def test_a_pref_refusal_reason_names_its_target_with_plus():
  from kanibako.settings.settings_prefs import AgentNames, PrefRequest, validate_pref

  why = validate_pref(
    PrefRequest(target="agent.nav℘zzz.model", value="opus", level="box"),
    valid_agents=AgentNames(("claude",)),
  )
  assert why is not None
  assert "nav+zzz" in why, why
  assert "℘" not in why, why


def test_a_category_root_value_names_its_node_with_plus():
  from kanibako.settings.settings_launch import _require_category_node

  with pytest.raises(SettingsError) as exc:
    _require_category_node("agent.nav℘claude", "caches", "a℘b")
  msg = str(exc.value)
  assert msg.startswith("agent.nav+claude.caches is a value"), msg
  # The VALUE is quoted as stored; only the key takes the user spelling.
  assert "'a℘b'" in msg, msg


def test_a_category_entry_label_and_its_pref_origin_agree_on_plus():
  """``pref_origin`` matches an entry's message LABEL, so both carry the ``+`` node."""
  from kanibako.settings.settings_categories import MOUNT, CategoryEntry
  from kanibako.settings.settings_prefs import PrefRequest, pref_origin

  entry = CategoryEntry(
    category="caches", scope="agent", box_dest="/d℘x", host_src=None, delivery=MOUNT,
    options="", name="", key_segments=("agent", "nav℘claude", "caches", "/d℘x"),
  )
  assert entry.label == "agent.nav+claude.caches[/d℘x]", entry.label
  req = PrefRequest(target="agent.nav℘claude.caches", value={"/d℘x": ["s"]}, level="box")
  assert pref_origin(entry.label, [req]) is req


def _system_file(config_file, doc: dict) -> None:
  from kanibako.settings.config import load_config
  from kanibako.settings.config_io import dump_doc
  from kanibako.settings.paths import load_std_paths

  settings = load_std_paths(load_config(config_file)).settings
  settings.parent.mkdir(parents=True, exist_ok=True)
  dump_doc(settings, doc)


class TestUndeclaredStorePaths:
  """A stored path names its node with ``+``; every other segment stays as stored."""

  DOC = {"agent": {"nav+claude": {"zzz": 1, "bindings": {"qq": {}}}}}

  @pytest.mark.writes_undeclared(
    "agent.nav℘claude.zzz", "agent.nav℘claude.bindings.qq",
    reason="drives the undeclared listing: the system file must carry both entries.",
  )
  def test_the_stored_listing(self, config_file, capsys):
    _system_file(config_file, self.DOC)
    out = _run(["system", "show"], capsys)
    assert "agent.nav+claude.zzz = 1" in out, out
    assert "℘" not in out, out

  @pytest.mark.writes_undeclared(
    "agent.nav℘claude.zzz", "agent.nav℘claude.bindings.qq",
    reason="drives the undeclared listing: the system file must carry both entries.",
  )
  def test_the_effective_refusal(self, config_file, capsys):
    _system_file(config_file, self.DOC)
    out = _run(["system", "show", "--effective"], capsys)
    assert "  - agent.nav+claude.bindings.qq: " in out, out
    assert ": agent.nav+claude.bindings.qq, agent.nav+claude.zzz" in out, out
    assert "℘" not in out, out

  def test_only_the_node_segment_converts(self):
    from kanibako.settings.settings_keyspace import display_store_path

    assert display_store_path(("pref", "agent", "nav℘claude", "fo℘o")) == (
      "pref.agent.nav+claude.fo℘o"
    )
    assert display_store_path(("system", "fo℘o")) == "system.fo℘o"


def test_a_typed_undeclared_segment_is_quoted_as_typed(config_file, capsys):
  out = _run(["system", "get", "system.fo℘o"], capsys)
  assert "'fo℘o' is not a declared system key" in out, out


def test_a_pref_reason_quotes_a_typed_segment_as_typed():
  from kanibako.settings.settings_prefs import AgentNames, PrefRequest, validate_pref

  why = validate_pref(
    PrefRequest(target="agent.nav℘claude.fo℘o", value="x", level="box"),
    valid_agents=AgentNames(("claude",)),
  )
  assert why is not None
  assert "'fo℘o' is not a declared agent key of 'agent.nav+claude'" in why, why


def test_the_suppress_then_add_block_pastes_with_plus():
  from kanibako.settings.settings_categories import _suppress_then_add

  block = _suppress_then_add(("agent", "nav℘claude", "caches", "/d℘x"), ambiguous=True)
  assert "\n  nav+claude:\n" in block, block
  assert "'agent.nav+claude.caches[/d℘x]'" in block, block
  assert "In nav+claude's OWN settings file" in block, block
  assert "IS 'agent.nav+claude'" in block, block
  assert "nav℘claude" not in block, block


def test_an_effective_declaration_names_its_node_with_plus():
  from kanibako.settings.kb_store import BINDING_DERIVATIONS_NODE, Bind
  from kanibako.settings.keystore import KeyStore
  from kanibako.settings.settings_categories import effective_bindings_and_template_sources

  snap = KeyStore()
  snap.insert_segments(
    (BINDING_DERIVATIONS_NODE, "agent", "nav℘claude", "caches", "/d℘x"),
    Bind(host="/h", box="/d℘x", opts=None),
  )
  keys = [d.declaration.key for d in effective_bindings_and_template_sources(snap)]
  assert keys == ["agent.nav+claude.caches[/d℘x]"], keys
