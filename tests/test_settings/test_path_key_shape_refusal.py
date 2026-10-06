"""§2a at RESOLVE — a non-string at a PATH key is refused, naming the key and the type.

Keyspec §2a, set-time VALIDATION: *"a type mismatch for a typed scalar key"* is a hard
error. A PATH key is typed ``path``, so a list or a map at one is a type mismatch —
whereas at the two SCALAR families ``env``/``secret_path`` an unquoted ``8080`` is
legal, because those are SCALAR and §2a refuses only *"a non-scalar (list, map, etc)"*.

A hand-edited file bypasses the ``set`` door entirely, so the ruling that the refusal
reaches EVERY path-typed key lands on the READ-time sweep over the levels a user
writes. Two shapes reach it, and they arrive by different roads:

* a **list** stays a LEAF (``_parse_node`` stores it verbatim), so the sweep saw it and
  judged only strings — a list was passed through, and at the Layer-1/Layer-2 keys
  ``config._flatten_dotted`` stringified it into a Python repr that the bare-relative
  arm then reported as a path the user never wrote;
* a **map** is parsed into a NODE, so the key stopped being a leaf and the sweep could
  not see it at all — ``{…}`` at a path key built silently.

Both are pinned here, at one key per layer, through a real ``build_launch_snapshot``.

Mutation notes are on each test: each names the hunk it pins.
"""

from __future__ import annotations

from itertools import count
from pathlib import Path

import pytest

from kanibako.settings.config_io import dump_doc
from kanibako.settings.settings_launch import build_launch_snapshot
from kanibako.settings.settings_resolve import SettingsError

#: One PATH key per layer, each a key the settings file owns at that scope (spec §0).
#: ``system.channels.common`` is the one the board row named — the keyspace spec types
#: it a channel type-ROOT (a directory), so a map there is the same type mismatch.
LAYER_CASES = (
    ("system", "system.channels.common", {"x": None}, "map"),
    ("workset", "workset.canon", ["a"], "list"),
    ("workset", "workset.canon", {"x": None}, "map"),
    ("box", "box.canon", ["a"], "list"),
    ("box", "box.canon", {"x": None}, "map"),
    ("agent", "agent.claude.canon", ["a"], "list"),
)


def _snapshot(tmp_path, *, box_file=None, workset_file=None, system_file=None,
              agent_file=None):
    """A real ``build_launch_snapshot`` over temp settings files, one per tier.

    Every tier the caller supplies is written at ITS OWN scope path: spec §0's
    directional enforcement drops a containing-scope key from a lower file, so a
    ``system.*`` defect must ride the system file to reach the sweep.
    """
    from tests.test_settings.test_settings_launch import _ctx, _mid_floor, _mr_floor
    from tests.test_settings.test_settings_launch import auth_chain_floor

    def to_path(data):
        if data is None:
            return None
        path = tmp_path / f"shape-{next(_COUNTER)}.yaml"
        dump_doc(path, data)
        return path

    return build_launch_snapshot(
        agent_name="claude", ctx=_ctx(),
        system_path=to_path(system_file), agent_path=to_path(agent_file),
        workset_path=to_path(workset_file), box_path=to_path(box_file),
        auth_chain=auth_chain_floor(mode="primary", agent_name="claude"),
        meta_runtime=_mr_floor(
            mode="primary", ws_name="__PRIMARY__", ws_root_literal=None,
        ),
        meta_identity=_mid_floor(
            box_name="b", project_path="/p", inbox="/i", share_global="/sg",
            share_workset=None, agent_name="claude", agent_real_name="claude",
            agent_auth_share_support=True,
        ),
        cli_level={"system.agent": "claude"},
    )


_COUNTER = count()


def _file_for(layer: str, key: str, value: object) -> dict:
    """The tier FILE holding ``value`` at that PATH key, nested as the file spells it."""
    *parents, leaf = key.split(".")
    node: dict = {leaf: value}
    for segment in reversed(parents):
        node = {segment: node}
    return node


@pytest.mark.parametrize("layer,key,value,shape", LAYER_CASES)
def test_a_non_string_at_a_path_key_is_refused_naming_key_and_type(
    tmp_path, layer, key, value, shape,
):
    """A hand-edited list/map at a PATH key refuses, naming the KEY and the WRONG TYPE.

    Mutation: drop the ``is_path_key_value`` arm from
    ``_refuse_ambiguous_path_values`` → this snapshot builds and the non-string reaches
    the launch (or, for a list at a Layer-1/2 key, the bare-relative arm quotes the
    repr instead).
    """
    with pytest.raises(SettingsError) as exc:
        _snapshot(tmp_path, **{f"{layer}_file": _file_for(layer, key, value)})
    msg = str(exc.value)
    # ⭐ THE KEY IS NAMED — the whole dotted key, not "a path key".
    assert key in msg, msg
    # ⭐ THE WRONG TYPE IS NAMED — a generic "invalid value" would satisfy a bare-rc
    # test and miss the rule, so the shape word is pinned.
    assert shape in msg, msg
    # Not the bare-relative answer: this is a TYPE defect, and quoting a Python repr as
    # a path is the misreading this replaces.
    assert "BARE RELATIVE" not in msg, msg
    assert repr(value) not in msg, msg


def test_a_list_at_a_bind_shaped_path_key_is_refused_by_the_bind_parser(tmp_path):
    """A LIST at ``system.channels.common`` refuses on the bind parser's own arm.

    ``common`` is a bind-shaped category, so the file READ refuses a list there as a
    malformed bind — the repr is quoted, and the KEY is named by
    ``settings_assemble._unpack_bind_named``, which is the only frame holding the key's
    segments. ⭐ ``settings_resolve.unpack_bind`` keeps its own signature and its own
    sentence; only the key is added, on the route that has one.

    Mutation: drop the key from ``_unpack_bind_named``'s re-raise → the
    ``system.channels.common`` assertion goes red.
    """
    with pytest.raises(SettingsError) as exc:
        _snapshot(tmp_path, system_file={"system": {"channels": {"common": ["a"]}}})
    msg = str(exc.value)
    assert "['a']" in msg
    assert "system.channels.common" in msg, msg


def test_a_map_at_system_channels_common_is_refused_not_built(tmp_path):
    """The board row's case: ``{x: null}`` at ``system.channels.common`` refuses.

    It BUILT before — the map parses to a node, so a leaves-only sweep never saw the
    key. Mutation: skip node positions in ``_path_key_leaves`` → it builds again.
    """
    with pytest.raises(SettingsError) as exc:
        _snapshot(tmp_path, system_file={"system": {"channels": {"common": {"x": None}}}})
    msg = str(exc.value)
    assert "system.channels.common" in msg
    assert "map" in msg


@pytest.mark.parametrize(
    "value,shape", [(8080, "an integer"), (True, "a boolean"), (1.5, "a number")],
)
def test_a_non_string_scalar_at_a_path_key_is_refused_too(tmp_path, value, shape):
    """An INT, BOOL and FLOAT refuse too — not only a list or a map.

    The two families differ because their DECLARED types do: ``is_scalar_family_value``
    keeps ``8080`` at ``box.env.PORT`` (§2a refuses only a non-scalar), while a PATH key
    is typed ``path`` and §2a makes a type mismatch a hard error.

    ⭐ THE SHAPE IS SPELLED AS THE FILE SPELLS IT, WITH ITS ARTICLE — ``an integer``,
    ``a boolean``, ``a number`` — because a settings file says ``8080`` / ``true`` / ``1.5``
    and never ``int`` / ``bool`` / ``float``.  The whole phrase is asserted, so a bare
    ``int`` that only survived as a SUBSTRING of ``integer`` cannot pass as the word.

    Mutation: reuse ``is_scalar_family_value`` for the path family → every row builds.
    Mutation: return ``type(value).__name__`` from ``_shape_phrase`` → every row fails,
    naming a Python type the reader never wrote.
    """
    with pytest.raises(SettingsError) as exc:
        _snapshot(tmp_path, box_file={"box": {"canon": value}})
    msg = str(exc.value)
    assert "box.canon" in msg
    assert f"box.canon holds {shape};" in msg, msg


def test_a_tuple_at_a_path_key_is_refused_as_a_list(tmp_path):
    """A TUPLE takes the list arm: ``_value_shape`` groups it with a list, as a file spells it."""
    with pytest.raises(SettingsError) as exc:
        _snapshot(tmp_path, box_file={"box": {"canon": ["a", "b"]}})
    assert "box.canon" in str(exc.value)
    assert "list" in str(exc.value)


def test_a_present_none_at_a_path_key_is_still_legal(tmp_path):
    """A present-``None`` is the tri-state OMIT a reset writes — not a type mismatch.

    Mutation: refuse ``None`` at a path key → every reset and standalone pin is dead.
    """
    assert _snapshot(tmp_path, box_file={"box": {"canon": None}}) is not None


def test_a_scalar_at_a_secret_path_key_is_refused_beside_one_at_env(tmp_path):
    """``box.secret_path.T: 8080`` REFUSES while ``box.env.PORT: 8080`` BUILDS — one value, two keys.

    ⭐ THE TWO ARE THE SAME INTEGER, so only the KEY's declared type can separate them:
    ``secret_path.<VAR>`` is typed ``path`` and §2a makes a type mismatch for a typed
    scalar key a hard error, while ``env.<VAR>`` is SCALAR and §2a refuses only *"a
    non-scalar (list, map, etc)"*.  A name-parametric family is not a reason to leave a
    value loose, and ``is_path_key_value`` — not ``is_scalar_family_value`` — is the
    predicate that says so.

    Mutation: judge both families with ``is_scalar_family_value`` → the build stays green
    and the refusal row here builds too, which is the regression this pins.
    """
    # ⭐ THE CONTROL FIRST: the same 8080 at the SCALAR family is legal, so a blanket
    # "refuse every int" cure cannot make the row below pass.
    assert _snapshot(tmp_path, box_file={"box": {"env": {"PORT": 8080}}}) is not None

    with pytest.raises(SettingsError) as exc:
        _snapshot(tmp_path, box_file={"box": {"secret_path": {"T": 8080}}})
    msg = str(exc.value)
    assert "box.secret_path.T" in msg, msg
    assert "holds an integer;" in msg, msg


def test_a_string_at_a_path_key_is_still_legal(tmp_path):
    """The control: the ordinary legal value still builds (the arm is not a blanket)."""
    assert _snapshot(tmp_path, box_file={"box": {"canon": "/srv/canon"}}) is not None


def test_an_ambiguous_string_still_gets_the_bare_relative_answer(tmp_path):
    """The two arms cannot both answer one key: a bare relative keeps its OWN refusal.

    Mutation: judge every non-``None`` as a shape → this loses its two readings and the
    cwd anchor.
    """
    with pytest.raises(SettingsError) as exc:
        _snapshot(tmp_path, box_file={"box": {"canon": "auth"}})
    msg = str(exc.value)
    assert "BARE RELATIVE" in msg
    assert str(Path.cwd() / "auth") in msg


def test_a_map_shaped_category_key_is_not_judged(tmp_path):
    """A MAP is legal where the keyspace DECLARES one: ``box.bindings`` is not a path key.

    Mutation: judge every node in a written level, ignoring ``is_path_valued_key`` →
    every legitimate bind map is refused.
    """
    assert _snapshot(tmp_path, box_file={"box": {"bindings": {"ro": {"/a": ["/host"]}}}}) is not None


def test_a_value_kanibako_supplies_is_not_the_users_to_fix(tmp_path):
    """The sweep judges the WRITTEN levels, so kanibako's own floor is never refused.

    Mutation: judge the merged snapshot → a refusal would name a value no file holds.
    """
    assert _snapshot(tmp_path) is not None


@pytest.mark.parametrize("value", ["/srv/canon", None, "~", "@config.data/c", ""])
def test_ambiguous_path_shape_error_refuses_to_invent_a_refusal_for_a_legal_value(value):
    """The public shape-error helper is TOTAL: a legal value raises, never returns a sentence.

    It is public and its return type is ``str``, so a value a PATH key may hold must not
    come back as a message: it has no refusal to render, and returning one would describe a
    shape the rule does not refuse — the one sentence a reader cannot act on.

    Mutation: drop the ``is_path_key_value`` guard → ``message`` is left unbound and this
    raises ``UnboundLocalError`` instead of ``ValueError``.
    """
    from kanibako.settings.agent_config import ambiguous_path_shape_error

    with pytest.raises(ValueError) as exc:
        ambiguous_path_shape_error("box.canon", value)
    assert "may hold that value" in str(exc.value)


def test_ambiguous_path_shape_error_still_answers_a_non_string():
    """The totality guard does not swallow the case the helper exists for.

    Mutation: make the guard unconditional → every refusal becomes a ``ValueError``.
    """
    from kanibako.settings.agent_config import ambiguous_path_shape_error

    msg = ambiguous_path_shape_error("box.canon", ["a"], where="/f.yaml")
    assert "box.canon" in msg and "list" in msg
    assert msg.endswith("(in /f.yaml)")
