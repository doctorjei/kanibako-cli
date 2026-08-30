"""[R147] at SET TIME — a bare relative path value is refused by every set route.

The read-time half is pinned at its two seams (``test_system_paths.py``'s
``TestBareRelativeIsRefusedNotAnchored`` for the Layer-1/Layer-2 tables,
``test_workset_dirkeys.py`` for the workset dir keys) and the predicate + message are
pinned in ``test_agent_config.py``.  This file pins the OTHER end: that the same rule,
in the same wording, reaches ``system set`` · ``workset set`` · ``box set`` ·
``agent set`` and the ``pref.<target>`` request — because a rule reachable at one noun
and not another is not a rule, it is a habit.

⚑ THE CORPUS IS DERIVED from ``KEY_TYPES``' ``path`` rows (P13), so a path key added to
the registry is swept the moment it is declared; ``test_the_corpus_is_not_empty`` is what
stops the sweep passing vacuously.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kanibako.settings.config import BOX_META_FILE, WORKSET_META_FILE
from kanibako.settings.config_interface import set_config_value
from kanibako.settings.config_io import load_doc
from kanibako.settings.config_keys import (
    KEY_TYPES,
    ConfigLevel,
    is_path_valued_key,
    path_key_anchor,
)

#: Every FIXED path spelling a CLI verb can write.  ⚑ The six ``config.*`` rows are
#: excluded BY THEIR OWN DECLARATION (``set: file``): they are refused at the top of
#: ``set_config_value`` with the §1 message, so read time is their only enforcement.
_SETTABLE_PATH_KEYS = sorted(
    key for key, kind in KEY_TYPES.items()
    if kind == "path" and not key.startswith("config.")
)

#: The command scope that writes each key's own namespace.
_SCOPE_OF = {
    "system": ConfigLevel.system,
    "workset": ConfigLevel.workset,
    "box": ConfigLevel.box,
}

_BARE = "comms"


def _files(tmp_path: Path) -> dict:
    """The four settings files the scopes write, each in a plausible tier directory."""
    ws = tmp_path / "ws"
    box = tmp_path / "ws" / "boxes" / "b"
    ws.mkdir(parents=True)
    box.mkdir(parents=True)
    return {
        "config": tmp_path / "kanibako_config.yaml",
        "system": tmp_path / "settings.yaml",
        "workset": ws / WORKSET_META_FILE,
        "box": box / BOX_META_FILE,
        "agents": tmp_path / "agents",
    }


def _set(key: str, value, files: dict, scope: ConfigLevel) -> str:
    """Drive ``set_config_value`` with the threading the matching noun command uses."""
    if scope is ConfigLevel.system:
        return set_config_value(
            key, value, config_path=files["config"], is_system=True,
            system_settings_path=files["system"], cascade_system_path=files["system"],
            command_scope=scope, agents_root=files["agents"],
        )
    if scope is ConfigLevel.workset:
        return set_config_value(
            key, value, config_path=files["workset"],
            cascade_system_path=files["system"], cascade_workset_path=files["workset"],
            command_scope=scope,
        )
    return set_config_value(
        key, value, config_path=files["box"],
        cascade_system_path=files["system"], cascade_workset_path=files["workset"],
        cascade_box_path=files["box"], command_scope=scope,
    )


def _assert_named_both_readings(message: str, key: str, value: str) -> None:
    """The refusal is [R147]'s, not a generic complaint: both directories are named."""
    assert message.startswith("Error:"), message
    assert key in message
    assert "BARE RELATIVE" in message
    # The cwd reading, in full — the whole point is that the user is told which two
    # directories were in play, not merely that the value was rejected.
    assert str(Path.cwd() / value) in message
    # ...and the OTHER root, resolved or spelled, on its own line.
    anchor_ref, _label = path_key_anchor(key)
    assert anchor_ref in message or message.count(f"/{value}") >= 2


class TestEverySetRouteRefusesABareRelative:
    """The sweep: every settable path key, at the scope that owns it."""

    def test_the_corpus_is_not_empty(self):
        # ⚑ NON-VACUITY. A parametrized sweep over an empty list is green and proves
        # nothing; this is the case that reds if the derivation stops finding keys.
        assert len(_SETTABLE_PATH_KEYS) > 20
        assert {key.split(".", 1)[0] for key in _SETTABLE_PATH_KEYS} == {
            "system", "workset", "box",
        }

    @pytest.mark.parametrize("key", _SETTABLE_PATH_KEYS)
    def test_a_bare_relative_is_refused_and_nothing_is_written(self, key, tmp_path):
        files = _files(tmp_path)
        scope = _SCOPE_OF[key.split(".", 1)[0]]
        message = _set(key, _BARE, files, scope)
        _assert_named_both_readings(message, key, _BARE)
        # ⚑ REFUSED BEFORE THE WRITE, not written and then complained about: a poisoned
        # settings file is exactly what the set-time half exists to prevent.
        target = files["system"] if scope is ConfigLevel.system else files[scope.value]
        assert load_doc(target) in ({}, None) or key.split(".")[-1] not in str(
            load_doc(target)
        )

    @pytest.mark.parametrize("value", ["comms", "./comms", "../comms", "my/dir"])
    def test_every_relative_SHAPE_is_refused_not_just_a_bare_leaf(self, value, tmp_path):
        files = _files(tmp_path)
        message = _set("workset.channelroot", value, files, ConfigLevel.workset)
        _assert_named_both_readings(message, "workset.channelroot", value)

    def test_the_message_names_the_file_the_value_would_have_landed_in(self, tmp_path):
        """``MIGRATION.md`` § 2.62 prints this transcript; the ``in <file>`` clause is
        part of it, and it is the file the WRITE was routed to."""
        files = _files(tmp_path)
        message = _set("workset.channelroot", _BARE, files, ConfigLevel.workset)
        assert f"in {files['workset']}" in message
        assert str(files["workset"].parent / _BARE) in message
        assert "@meta.workset.path" in message


class TestTheAgentRoutes:
    """``agent set`` routes through ``set_config_value`` (2026-08-29), so the two
    path-valued agent leaves come along — at the bare any-agent spelling and the
    per-node one alike."""

    @pytest.mark.parametrize("leaf", ["canon", "template"])
    def test_the_bare_any_agent_spelling_is_refused(self, leaf, tmp_path):
        files = _files(tmp_path)
        message = _set(leaf, _BARE, files, ConfigLevel.system)
        _assert_named_both_readings(message, leaf, _BARE)
        assert "@meta.agent.default.path" in message

    @pytest.mark.parametrize("leaf", ["canon", "template"])
    def test_the_per_node_spelling_is_refused(self, leaf, tmp_path):
        """⚑ THIS IS WHAT THE ``agent set`` ROUTING BUYS. ``agent_cmd`` builds
        ``agent.<id>.<leaf>`` and hands it to ``set_config_value``; before that routing
        landed it wrote the node file directly and no set-time rule saw the value."""
        files = _files(tmp_path)
        key = f"agent.claude.{leaf}"
        message = _set(key, _BARE, files, ConfigLevel.system)
        _assert_named_both_readings(message, key, _BARE)
        assert not (files["agents"] / "claude" / "agent.yaml").exists()

    @pytest.mark.parametrize("leaf", ["canon", "template"])
    def test_a_legal_per_node_value_still_writes(self, leaf, tmp_path):
        files = _files(tmp_path)
        message = _set(f"agent.claude.{leaf}", "/srv/x", files, ConfigLevel.system)
        assert not message.startswith("Error:"), message
        assert load_doc(files["agents"] / "claude" / "agent.yaml") == {
            "self": {leaf: "/srv/x"},
        }


class TestTheSecretPathFamily:
    """``secret_path.<VAR>`` carries ``value: path`` and is PARAMETRIC, so a ``type:``
    grep misses it — it is swept here by name at all four spellings."""

    @pytest.mark.parametrize("scope", ["system", "workset", "box"])
    def test_a_scope_secret_path_refuses_a_bare_relative(self, scope, tmp_path):
        files = _files(tmp_path)
        key = f"{scope}.secret_path.TOKEN"
        message = _set(key, "tok.txt", files, _SCOPE_OF[scope])
        _assert_named_both_readings(message, key, "tok.txt")

    def test_a_per_node_secret_path_refuses_a_bare_relative(self, tmp_path):
        files = _files(tmp_path)
        message = _set("agent.claude.secret_path.TOKEN", "tok.txt", files,
                       ConfigLevel.system)
        _assert_named_both_readings(message, "agent.claude.secret_path.TOKEN", "tok.txt")
        assert not (files["agents"] / "claude" / "agent.yaml").exists()

    def test_the_reading_it_names_is_a_SCOPE_root_not_a_default(self, tmp_path):
        """⚑ ``secret_path`` declares NO default, so the message must not claim one.
        Mutation: change ``path_key_anchor``'s secret arm to ``DEFAULT_ROOT_LABEL`` and
        this reds — a message that invents a fallback for an unset key is the one thing
        a refusal about ambiguity must not do."""
        files = _files(tmp_path)
        message = _set("system.secret_path.TOKEN", "tok.txt", files, ConfigLevel.system)
        assert "this key's scope root" in message
        assert "this key's default root" not in message

    def test_a_legal_secret_path_still_writes(self, tmp_path):
        files = _files(tmp_path)
        message = _set("system.secret_path.TOKEN", "~/.tok", files, ConfigLevel.system)
        assert not message.startswith("Error:"), message
        assert load_doc(files["system"])["system"]["secret_path"]["TOKEN"] == "~/.tok"


class TestThePrefRequest:
    """A ``pref.<target>`` request is INSTALLED at its target during resolution (§2h),
    so a bare relative requested for a path leaf reaches the launch as the value of a
    path key.  ⚑ MUTATION: delete the ``_bare_relative_path_error`` call in
    ``_pref_value_error`` and these two go green with the value stored."""

    @pytest.mark.parametrize("leaf", ["canon", "template"])
    def test_a_pref_targeting_a_path_leaf_is_refused(self, leaf, tmp_path):
        files = _files(tmp_path)
        key = f"pref.agent.claude.{leaf}"
        message = _set(key, _BARE, files, ConfigLevel.box)
        _assert_named_both_readings(message, key, _BARE)
        assert load_doc(files["box"]) in ({}, None)

    def test_a_pref_targeting_a_NON_path_leaf_is_untouched(self, tmp_path):
        """The refusal is typed by the TARGET, not by the ``pref.`` prefix."""
        files = _files(tmp_path)
        message = _set("pref.agent.claude.model", "opus-x", files, ConfigLevel.box)
        assert not message.startswith("Error:"), message


class TestTheLegalShapesAreAccepted:
    """The half that breaks quietly: an over-firing refusal bans a legal spelling and
    the only symptom is a user who cannot configure their box."""

    @pytest.mark.parametrize("value", [
        "/srv/comms",                 # absolute
        "~/comms",                    # home-rooted
        "$XDG_DATA_HOME/comms",       # an XDG base, BARE
        "${XDG_DATA_HOME}/comms",     # ...and BRACED — parsed, never prefix-matched
        "@config.data/comms",         # an @-ref to another key
        "@meta.workset.path/comms",   # ...including the one [R147]'s cure offers
    ])
    def test_workset_scope_accepts_it(self, value, tmp_path):
        files = _files(tmp_path)
        message = _set("workset.channelroot", value, files, ConfigLevel.workset)
        assert not message.startswith("Error:"), message
        assert load_doc(files["workset"])["workset"]["channelroot"] == value

    @pytest.mark.parametrize("value", [
        "/srv/c", "~/c", "$XDG_CACHE_HOME/c", "${XDG_CACHE_HOME}/c", "@config.data/c",
    ])
    def test_system_scope_accepts_it(self, value, tmp_path):
        files = _files(tmp_path)
        message = _set("system.cache", value, files, ConfigLevel.system)
        assert not message.startswith("Error:"), message
        assert load_doc(files["system"])["system"]["cache"] == value

    def test_the_cure_the_refusal_OFFERS_actually_resolves(self, tmp_path):
        """⚑⚑ THE PAIR IS THE POINT, and it is why the set-time snapshot carries the
        ``@meta.{workset,box}.path`` anchors.  The refusal names
        ``@meta.workset.path/comms``; ``MIGRATION.md`` § 2.62's table names it as the
        replacement for the old root-relative reading.  Until the anchor was floored the
        set-time E3 probe answered "dangling @-reference" to it — a rule that banned a
        form and then refused its own cure.
        MUTATION: drop ``_meta_scope_anchor_floor`` from ``_category_set_lookups``'
        floor and this reds while every refusal above stays green."""
        files = _files(tmp_path)
        refusal = _set("workset.channelroot", _BARE, files, ConfigLevel.workset)
        offered = f"@meta.workset.path/{_BARE}"
        assert offered in refusal
        assert not _set(
            "workset.channelroot", offered, files, ConfigLevel.workset,
        ).startswith("Error:")

    def test_the_box_root_cure_resolves_too(self, tmp_path):
        files = _files(tmp_path)
        message = _set("box.canon", "@meta.box.path/canon", files, ConfigLevel.box)
        assert not message.startswith("Error:"), message


class TestTheRuleDoesNotOVERREACH:
    """What the refusal must NOT touch.  Every case here was reachable by writing the
    predicate one notch wider."""

    @pytest.mark.parametrize("key,value,scope", [
        ("box.image", "myimage:1", ConfigLevel.box),
        ("box.shell", "bash", ConfigLevel.box),
        ("model", "opus", ConfigLevel.system),
        ("box.env.GREETING", "hello", ConfigLevel.box),
    ])
    def test_a_NON_path_key_takes_a_bare_relative_looking_value(
        self, key, value, scope, tmp_path,
    ):
        """A path rule that swept every scalar would refuse an image tag and a shell
        name.  ⚑ ``is_path_valued_key`` is what keeps it narrow — widen it to
        ``KEY_TYPES`` membership and these red."""
        assert not is_path_valued_key(key)
        files = _files(tmp_path)
        assert not _set(key, value, files, scope).startswith("Error:")

    def test_an_EMPTY_value_is_not_this_rules_business(self, tmp_path):
        """The guard matches the read-time one (``paths._refuse_bare_relative``): there
        is no bare relative to disambiguate, and calling ``''`` a relative path would be
        a refusal a user cannot act on."""
        files = _files(tmp_path)
        assert not _set("workset.canon", "", files, ConfigLevel.workset).startswith(
            "Error:"
        )

    def test_an_explicit_null_is_not_refused(self, tmp_path):
        """``--null`` writes a present-``None``; there is no path to judge."""
        files = _files(tmp_path)
        assert not _set(
            "system.secret_path.TOKEN", None, files, ConfigLevel.system,
        ).startswith("Error:")

    def test_the_bind_source_predicate_is_UNCHANGED_by_this(self):
        """⚑ ``is_self_resolving`` rules on a bind SOURCE, where any ``$VAR`` is legal;
        [R147]'s predicate rules on a user-typed path key.  The contrast is pinned in
        ``test_agent_config.py``; this row exists so a reader of the SET-TIME file is
        told the two are different rules before they "unify" them."""
        from kanibako.settings.agent_config import (
            is_self_resolving,
            is_unambiguous_path_value,
        )

        assert is_self_resolving("$AGENT/logs") is True
        assert is_unambiguous_path_value("$AGENT/logs") is False


class TestTheAnchorDegradesHONESTLY:
    """A downward write names a root the command does not hold."""

    def test_a_system_scope_workset_write_still_names_the_reading(self, tmp_path):
        """``system set workset.channelroot=comms`` is a legal DOWNWARD write, and the
        system command holds no workset — so the other reading cannot be resolved to a
        directory.  It is still NAMED, in a spelling the user can paste, and the
        ``spelled '...'`` clause is dropped rather than repeating the same text twice."""
        files = _files(tmp_path)
        message = _set("workset.channelroot", _BARE, files, ConfigLevel.system)
        assert f"@meta.workset.path/{_BARE}" in message
        # ⚑ The ``, spelled '<ref>/<value>'`` CLAUSE, not the word: the closing line of
        # every one of these messages ends "spelled so it resolves on its own".
        assert ", spelled '" not in message
        assert str(Path.cwd() / _BARE) in message
