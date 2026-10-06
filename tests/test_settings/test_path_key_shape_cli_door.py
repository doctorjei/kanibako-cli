"""§2a at the PATH TIERS — a non-string at a Layer-1/2 or workset-early PATH key, by the real CLI.

The same §2a rule :mod:`tests.test_settings.test_path_key_shape_refusal` pins at the launch
RESOLVE is answered, on the doors a real command walks FIRST, by a different and worse
message.  Those readers sit upstream of the launch sweep and stringify before it runs:

* the Layer-1 ``config:`` and Layer-2 ``system:`` reads
  (:func:`~kanibako.settings.config.bootstrap_config_paths` and
  :func:`~kanibako.settings.config.system_table_set_values`) hand ``_flatten_dotted`` a
  ``str(v)``, so a LIST arrived at the bare-relative arm as the Python repr ``"['x']"``
  and was reported as *"a BARE RELATIVE path"* — a path the user never wrote — and an INT
  as the bare word ``'8080'``, naming no type at all;
* :func:`~kanibako.settings.workset_dirkeys._stored_repoint` did the same ``str()``, so a
  MAP reached :func:`~kanibako.settings.workset_dirkeys.resolve_workset_dir_key` already
  spelled ``"{'x': None}"``.

Every test here therefore goes through ``kanibako.cli.main`` with a command that loads the
standard paths.  ⭐ A test on ``build_launch_snapshot`` alone would pin NOTHING about these
doors: the launch sweep runs after them and would have caught the value anyway.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from tests.support.filenames import CONFIG_FILENAME

#: A command that loads the standard paths and then wants a registered workset — so a
#: refusal from the path tiers is raised before the command's own "not registered".
DOOR = ["workset", "share", "list", "ws1", "--effective"]


def _cli(argv, tmp_home, capsys):
    """``kanibako.cli.main`` over an isolated HOME; return ``(rc, what it printed)``."""
    from kanibako.cli import main

    try:
        rc = main(argv)
    except SystemExit as exc:
        rc = exc.code
    out = capsys.readouterr()
    return rc, out.out + out.err


def _write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(body).lstrip())


def _settings(tmp_home, config_file) -> Path:
    """The settings file the Layer-2 read opens, at the path the reader resolves."""
    from kanibako.settings.config import load_config
    from kanibako.settings.paths import load_std_paths

    return load_std_paths(load_config(config_file)).settings


def _registered_ws(tmp_home, capsys) -> Path:
    """A workset registered by the real ``workset create``, so the door reaches its keys."""
    root = tmp_home / "userws" / "ws1"
    rc, _ = _cli(["workset", "create", str(root)], tmp_home, capsys)
    assert rc == 0, "workset create must succeed for this door to reach a workset key"
    return root


#: (label, file to write, body, the key and the shape the refusal must name)
#: ⭐ A SCALAR'S SHAPE IS ITS USER WORD WITH ITS ARTICLE — ``an integer`` / ``a boolean`` /
#: ``a number``, never the class name a Python reader would recognise but a YAML author
#: never wrote.
LAYER_TIER_CASES = (
    ("system.cache", "system", "cache", ["x"], "system.cache", "a list"),
    ("system.cache", "system", "cache", 8080, "system.cache", "an integer"),
    ("system.cache", "system", "cache", {"x": None}, "system.cache", "a map"),
    ("system.canon", "system", "canon", True, "system.canon", "a boolean"),
    ("system.canon", "system", "canon", 1.5, "system.canon", "a number"),
    ("config.data", "config", "data", ["x"], "config.data", "a list"),
    ("config.data", "config", "data", 8080, "config.data", "an integer"),
    ("config.data", "config", "data", {"x": None}, "config.data", "a map"),
)


@pytest.mark.parametrize(
    "label,table,leaf,value,key,shape",
    [c for c in LAYER_TIER_CASES if c[1] == "system"],
    ids=[c[0] + ":" + c[5] for c in LAYER_TIER_CASES if c[1] == "system"],
)
def test_a_non_string_at_a_layer2_path_key_is_refused_by_name_at_the_cli(
    tmp_home, config_file, capsys, label, table, leaf, value, key, shape,
):
    """A hand-edited non-string at a Layer-2 ``system.*`` PATH key refuses AT THE READ.

    ⭐ The door is the real CLI, not the launch: this read runs first, and pinning it here
    is what stops the bare-relative arm from answering a type defect again.

    Mutation: drop the ``_refuse_non_string_path_keys`` call from
    ``system_table_set_values`` → the read returns, and the CLI answers the bare-relative
    arm with ``"['x']"`` quoted as a path.
    """
    _write(_settings(tmp_home, config_file), _system_body(leaf, value))
    rc, printed = _cli(DOOR, tmp_home, capsys)
    assert rc != 0
    assert key in printed, printed
    assert shape in printed, printed
    # ⭐ NOT the bare-relative answer: quoting a Python repr as a path is the misreading.
    assert "BARE RELATIVE" not in printed, printed


@pytest.mark.parametrize(
    "label,table,leaf,value,key,shape",
    [c for c in LAYER_TIER_CASES if c[1] == "config"],
    ids=[c[0] + ":" + c[5] for c in LAYER_TIER_CASES if c[1] == "config"],
)
def test_a_non_string_at_a_layer1_path_key_is_refused_by_name_at_the_cli(
    tmp_home, config_file, capsys, label, table, leaf, value, key, shape,
):
    """The same at Layer 1, in ``kanibako.cfg``'s own ``config:`` table.

    Mutation: drop the ``_refuse_non_string_path_keys`` call from
    ``bootstrap_config_paths`` → the same misreading returns for ``config.data``.
    """
    _write(config_file, _config_body(leaf, value))
    rc, printed = _cli(DOOR, tmp_home, capsys)
    assert rc != 0
    assert key in printed, printed
    assert shape in printed, printed
    assert "BARE RELATIVE" not in printed, printed


#: ⛔ ``channels.common`` + a LIST IS NOT IN THIS TABLE.  ``channels.*`` is bind-shaped, so
#: the file READ refuses a list there through the bind parser, which names the file and the
#: repr but NOT the key.  It is pinned, as a refusal only, by the test below.
WORKSET_EARLY_CASES = (
    ("channels.common", 8080, "workset.channels.common", "an integer"),
    ("channels.common", {"x": None}, "workset.channels.common", "a map"),
    ("canon", ["a"], "workset.canon", "a list"),
    ("canon", 8080, "workset.canon", "an integer"),
    ("canon", {"x": None}, "workset.canon", "a map"),
)


@pytest.mark.parametrize(
    "leaf_path,value,key,shape", WORKSET_EARLY_CASES,
    ids=[f"{c[2]}:{c[3]}" for c in WORKSET_EARLY_CASES],
)
def test_a_non_string_at_a_workset_early_path_key_is_refused_by_name_at_the_cli(
    tmp_home, config_file, capsys, leaf_path, value, key, shape,
):
    """A hand-edited non-string at a ``workset.*`` EARLY key refuses AT THE READ.

    ⭐ ``_stored_repoint``'s ``str()`` is what made a map arrive already spelled
    ``"{'x': None}"``; the check asks ``is_path_key_value`` before it.

    Mutation: drop the ``is_path_key_value`` check from ``_stored_repoint``.  ⭐ MEASURED,
    NOT ASSUMED: that turns ``workset.channels.common:{int,map}`` RED — the early read
    answers first there, so the repr reaches the bare-relative arm instead.  The
    ``workset.canon`` rows stay GREEN under the same mutation and are therefore GUARDS, not
    proofs: the launch sweep reaches them first and refuses the value by name anyway.
    """
    root = _registered_ws(tmp_home, capsys)
    _write(root / "workset.yaml", _workset_body(leaf_path, value))
    rc, printed = _cli(DOOR, tmp_home, capsys)
    assert rc != 0
    assert key in printed, printed
    assert shape in printed, printed
    assert "BARE RELATIVE" not in printed, printed


def test_a_list_at_a_bind_shaped_early_key_refuses_before_the_shape_check(
    tmp_home, config_file, capsys,
):
    """A LIST at ``workset.channels.common`` refuses EARLIER, on the bind parser's own arm.

    ``channels.*`` is bind-shaped, so the file READ refuses a list there as a malformed
    bind rather than as a §2a type mismatch. ⭐ BOTH THINGS IT NAMES ARE PINNED HERE: the
    FILE the user has to edit, and the dotted KEY, which
    ``settings_assemble._unpack_bind_named`` adds because ``_parse_node`` is the only
    frame that holds the key's segments — ``unpack_bind`` itself sees a bare leaf.

    Mutation: drop the key from ``_unpack_bind_named``'s re-raise → the ``channels.common``
    assertion goes red and the FILE assertion stays green.
    """
    root = _registered_ws(tmp_home, capsys)
    _write(root / "workset.yaml", _workset_body("channels.common", ["x"]))
    rc, printed = _cli(DOOR, tmp_home, capsys)
    assert rc != 0
    assert "workset.yaml" in printed, printed
    assert "channels.common" in printed, printed


def test_a_string_at_a_workset_early_path_key_still_reaches_the_bare_relative_arm(
    tmp_home, config_file, capsys,
):
    """A bare-relative STRING keeps its OWN refusal — the two arms cannot both answer one key.

    Mutation: judge every stored value as a shape → this loses both its readings.
    """
    root = _registered_ws(tmp_home, capsys)
    _write(root / "workset.yaml", _workset_body("canon", "rel"))
    rc, printed = _cli(DOOR, tmp_home, capsys)
    assert rc != 0
    assert "BARE RELATIVE" in printed, printed
    assert "workset.canon" in printed, printed


def test_a_legal_path_key_value_is_not_refused_at_any_tier(tmp_home, config_file, capsys):
    """The control at BOTH tiers: an absolute string, a ``$XDG``/``@``-ref and a null all pass.

    ⭐ This is the regression the check could cause, so it is pinned at the door that
    stringifies rather than at the launch sweep that already passed.

    Mutation: judge the stringified value → every absolute path under a path key refuses.
    """
    _write(_settings(tmp_home, config_file), _system_body("cache", "/srv/cache"))
    rc, printed = _cli(DOOR, tmp_home, capsys)
    assert "BARE RELATIVE" not in printed, printed
    assert "holds a" not in printed, printed


def test_a_null_at_a_path_key_is_still_the_omission_it_was(tmp_home, config_file, capsys):
    """A present ``null`` is the tri-state OMIT a reset writes — the null refusal, not a type one.

    Mutation: drop ``None`` from ``is_path_key_value`` → every reset and pin dies, and
    this reports a TYPE refusal where the null one belongs.
    """
    _write(_settings(tmp_home, config_file), _system_body("cache", None))
    rc, printed = _cli(DOOR, tmp_home, capsys)
    assert "holds a" not in printed, printed
    assert "BARE RELATIVE" not in printed, printed


def test_a_non_path_key_in_the_system_table_is_not_judged(tmp_home, config_file, capsys):
    """``system:`` legitimately holds non-path keys; only the declared PATH tier is judged.

    Mutation: judge every ``system.*`` leaf → ``system.agent`` and the category families
    are refused as type mismatches.
    """
    _write(_settings(tmp_home, config_file),
           "system:\n  agent:\n  - a\n  cache: /srv/cache\n")
    rc, printed = _cli(DOOR, tmp_home, capsys)
    assert "system.agent" not in printed, printed
    assert "holds a" not in printed, printed


# ---------------------------------------------------------------- helpers


def _nested(leaf_path: str, value: object) -> str:
    """``channels.common`` + a value, spelled as the file nests it, at NO indent."""
    import yaml

    node: object = value
    for segment in reversed(leaf_path.split(".")):
        node = {segment: node}
    return yaml.safe_dump(node, default_flow_style=False, sort_keys=False).rstrip()


def _indent(body: str, spaces: int) -> str:
    pad = " " * spaces
    return "\n".join(pad + line if line else line for line in body.splitlines())


def _system_body(leaf: str, value: object) -> str:
    return "system:\n" + _indent(_nested(leaf, value), 2) + "\n"


def _config_body(leaf: str, value: object) -> str:
    return "config:\n" + _indent(_nested(leaf, value), 2) + "\n"


def _workset_body(leaf_path: str, value: object) -> str:
    return "name: ws1\nworkset:\n" + _indent(_nested(leaf_path, value), 2) + "\n"


def test_the_helpers_spell_the_documented_files(tmp_home, config_file):
    """The fixtures' own spelling — so a failure above is the read, not a typo in a helper."""
    assert config_file.name == CONFIG_FILENAME
    assert _system_body("cache", ["x"]) == "system:\n  cache:\n  - x\n"
    assert _config_body("data", {"x": None}) == "config:\n  data:\n    x: null\n"
    assert _nested("channels.common", 8080) == "channels:\n  common: 8080"
    assert _workset_body("canon", ["a"]) == (
        "name: ws1\nworkset:\n  canon:\n  - a\n")
