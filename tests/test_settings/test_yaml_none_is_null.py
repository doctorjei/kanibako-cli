"""A plain ``None`` is null in kanibako's own YAML and on the ``set`` command line.

A user who types Python's spelling means the null.  Only the plain scalar: a quoted
``'None'`` stays a string, and the dumper quotes a str ``'None'`` so it reads back as written.
"""

from __future__ import annotations

import pytest

from kanibako.cli import main
from kanibako.settings.config_interface import ConfigAction, parse_config_arg
from kanibako.settings.config_io import _dump_text, load_doc, parse_doc_text, parse_packaged
from kanibako.settings.settings_assemble import ReadPurpose, agent_record
from kanibako.settings.settings_launch import load_merged_config


@pytest.mark.parametrize("parse", [parse_doc_text, parse_packaged])
class TestTheLoaders:
  def test_a_plain_none_is_null(self, parse):
    assert parse("a: None\nb: [None]\n") == {"a": None, "b": [None]}

  @pytest.mark.parametrize("text", ["a: 'None'", 'a: "None"'])
  def test_a_quoted_none_stays_a_string(self, parse, text):
    assert parse(text) == {"a": "None"}

  @pytest.mark.parametrize("word", ["none", "NONE", "Nonesuch"])
  def test_only_the_exact_spelling_is_null(self, parse, word):
    assert parse(f"a: {word}") == {"a": word}


def test_the_dumper_round_trips_the_string_none():
  doc = {"s": "None", "n": None}
  text = _dump_text(doc)
  assert "s: 'None'" in text
  assert parse_doc_text(text) == doc


def test_a_bare_none_at_a_bool_box_scalar_is_refused_like_null(tmp_path):
  """It used to resolve to True: ``bool('None')``."""
  from kanibako.settings.settings_resolve import SettingsError

  box_yaml = tmp_path / "box.yaml"
  box_yaml.write_text("box:\n  enable_vault: None\n")
  with pytest.raises(SettingsError, match="box.enable_vault"):
    load_merged_config(box_yaml)


def test_a_bare_none_env_value_is_the_suppression_null(tmp_path):
  agent_yaml = tmp_path / "claude" / "agent.yaml"
  agent_yaml.parent.mkdir()
  agent_yaml.write_text("self:\n  env:\n    FOO: None\n    BAR: 'None'\n")
  cfg = agent_record(agent_yaml, node="claude", purpose=ReadPurpose.RESOLVE)
  assert cfg.env == {"FOO": None, "BAR": "None"}


class TestTheCommandLine:
  def test_set_key_none_parses_as_the_null(self):
    assert parse_config_arg("box.shell=None") == (ConfigAction.set, "box.shell", None)
    assert parse_config_arg("box.shell=null") == (ConfigAction.set, "box.shell", "null")

  def test_system_set_none_stores_a_null(self, tmp_home):
    with pytest.raises(SystemExit) as exit_:
      main(["system", "set", "box.shell=None"])
    assert exit_.value.code == 0
    (settings,) = (tmp_home / "data").rglob("global/settings.yaml")
    assert load_doc(settings) == {"box": {"shell": None}}

  def test_system_set_none_is_refused_where_null_is(self, tmp_home, capsys):
    with pytest.raises(SystemExit) as exit_:
      main(["system", "set", "box.enable_vault=None"])
    assert exit_.value.code == 1
    assert "box.enable_vault" in capsys.readouterr().err


def _cli(*argv):
  import contextlib
  import io

  out = io.StringIO()
  with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
    try:
      main(list(argv))
      rc = 0
    except SystemExit as e:
      rc = e.code if isinstance(e.code, int) else 1
  return rc, out.getvalue()


@pytest.mark.parametrize(
  "scope",
  [
    ("box", "set", "nb"),
    ("workset", "set", "wsn"),
    ("agent", "set", "claude"),
  ],
)
def test_every_set_door_types_none_as_the_null(config_file, tmp_home, credentials_dir, scope):
  """``key=None`` answers exactly as ``--null key``: here, the same refusal (the string
  ``None`` would have been written)."""
  from kanibako.project.workset import create_workset
  from kanibako.settings.config import load_config
  from kanibako.settings.paths import load_std_paths

  (tmp_home / "nb").mkdir()
  assert _cli("create", str(tmp_home / "nb"), "--agent", "claude")[0] == 0
  create_workset("wsn", (tmp_home / "wsn").resolve(), load_std_paths(load_config(config_file)))
  key = "label" if scope[0] == "agent" else "pref.agent.claude.bootstrap"
  by_flag = _cli(*scope, "--null", key)
  by_value = _cli(*scope, f"{key}=None")
  assert by_flag[0] == by_value[0] == 1, by_value
  assert by_value[1] == by_flag[1]
  assert "Nothing was written" in by_value[1]


class TestAKeyThatIsNotAName:
  """A config document's mapping key must be text; ``None:``, ``true:`` or ``1:`` is refused
  by name, never a crash and never a silent ``{None: …}``."""

  @pytest.mark.parametrize("key", ["None", "null", "true", "1"])
  def test_a_box_file_is_refused_naming_the_table(self, tmp_path, key):
    from kanibako.errors import ConfigError

    box_yaml = tmp_path / "box.yaml"
    box_yaml.write_text(f"box:\n  env:\n    {key}: x\n")
    with pytest.raises(ConfigError, match=r"'box\.env'.*Quote that key"):
      load_merged_config(box_yaml)

  def test_an_agent_file_is_refused_too(self, tmp_path):
    from kanibako.errors import ConfigError

    agent_yaml = tmp_path / "claude" / "agent.yaml"
    agent_yaml.parent.mkdir()
    agent_yaml.write_text("self:\n  env:\n    None: x\n")
    with pytest.raises(ConfigError, match=r"'self\.env' that YAML reads as null"):
      agent_record(agent_yaml, node="claude", purpose=ReadPurpose.RESOLVE)

  def test_a_quoted_key_is_a_name(self, tmp_path):
    p = tmp_path / "doc.yaml"
    p.write_text("a:\n  'None': x\n")
    assert load_doc(p) == {"a": {"None": "x"}}
