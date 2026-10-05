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

from kanibako.project.workset import create_workset
from kanibako.settings.config import BOX_META_FILE, WORKSET_META_FILE
from kanibako.settings.config_interface import set_config_value
from kanibako.settings.config_io import load_doc
from kanibako.settings.config_keys import (
    KEY_TYPES,
    ConfigLevel,
    is_path_valued_key,
    path_key_anchor,
)
from tests.support.filenames import CONFIG_FILENAME

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
        "config": tmp_path / CONFIG_FILENAME,
        "system": tmp_path / "settings.yaml",
        "workset": ws / WORKSET_META_FILE,
        "box": box / BOX_META_FILE,
        "agents": tmp_path / "agents",
    }


def _set(key: str, value, files: dict, scope: ConfigLevel, *, std=None, ws=None) -> str:
    """Drive ``set_config_value`` with the threading the matching noun command uses."""
    if scope is ConfigLevel.system:
        return set_config_value(
            key, value, config_path=files["config"],
            system_settings_path=files["system"], cascade_system_path=files["system"],
            command_scope=scope, agents_root=files["agents"],
        )
    if scope is ConfigLevel.workset:
        return set_config_value(
            key, value, config_path=files["workset"],
            cascade_system_path=files["system"], cascade_workset_path=files["workset"],
            command_scope=scope, std=std, ws=ws,
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

    def test_the_message_names_the_file_the_value_would_have_landed_in(
        self, std, tmp_home,
    ):
        """``MIGRATION.md`` § 2.62 prints this transcript; the ``in <file>`` clause is
        part of it, and it is the file the WRITE was routed to.  The other reading is
        the working set's own root, which only the working set as TARGET supplies."""
        from kanibako.settings.paths import workset_settings_path

        ws = create_workset("filews", tmp_home / "filews", std)
        message = _set_ws("workset.channelroot", _BARE, std, ws)
        assert f"in {workset_settings_path(ws)}" in message
        assert str(ws.root / _BARE) in message
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
        # ``@meta.workset.path/comms`` — the cure [R147] offers — needs the COMMAND's
        # target to resolve, so it is pinned with a real working set below
        # (``TestSetTimeResolvesTheCommandsTarget``), not with these bare files.
    ])
    def test_workset_scope_accepts_it(self, value, config_file, tmp_path):
        # ⚑ ``config_file`` (hence ``tmp_home``) is REQUIRED for the ``@config.*`` cases:
        # the set-time probe resolves that foundation from the REAL
        # ``$XDG_CONFIG_HOME/kanibako.cfg`` and CONCEDES on any failure, so an
        # unisolated run answers "dangling @-reference" off the host's own file.
        # ⚑ NOT a workset EARLY key: those take only ``@meta.workset.path``
        # (``test_workset_early_key_set_door.py``).
        files = _files(tmp_path)
        message = _set("workset.auth.path", value, files, ConfigLevel.workset)
        assert not message.startswith("Error:"), message
        assert load_doc(files["workset"])["workset"]["auth"]["path"] == value

    @pytest.mark.parametrize("value", [
        "/srv/c", "~/c", "$XDG_CACHE_HOME/c", "${XDG_CACHE_HOME}/c", "@config.data/c",
    ])
    def test_system_scope_accepts_it(self, value, config_file, tmp_path):
        """⚑ ``config_file`` for the same reason as the case above — host isolation."""
        files = _files(tmp_path)
        message = _set("system.cache", value, files, ConfigLevel.system)
        assert not message.startswith("Error:"), message
        assert load_doc(files["system"])["system"]["cache"] == value

    def test_the_cure_the_refusal_OFFERS_actually_resolves(self, std, tmp_home):
        """⚑⚑ THE PAIR IS THE POINT, and it is why the set-time snapshot resolves the
        command's target.  The refusal names ``@meta.workset.path/comms``;
        ``MIGRATION.md`` § 2.62's table names it as the replacement for the old
        root-relative reading.  Without the working set's anchors the set-time E3
        probe answered "dangling @-reference" to it — a rule that banned a form and
        then refused its own cure."""
        ws = create_workset("curews", tmp_home / "curews", std)
        refusal = _set_ws("workset.channelroot", _BARE, std, ws)
        offered = f"@meta.workset.path/{_BARE}"
        assert offered in refusal
        assert not _set_ws("workset.channelroot", offered, std, ws).startswith("Error:")

    def test_the_box_root_cure_resolves_too(self, std, config_file, tmp_home):
        proj = _primary_box(std, config_file, tmp_home)
        message = _set_box("box.canon", "@meta.box.path/canon", std, proj)
        assert not message.startswith("Error:"), message


def _primary_box(std, config_file, tmp_home):
    """A real primary box in the default working set."""
    from kanibako.settings.config import load_config
    from kanibako.settings.paths import resolve_project

    return resolve_project(
        std, load_config(config_file), project_dir=str(tmp_home / "project"),
        initialize=True,
    )


def _set_box(key: str, value, std, proj, *, force: bool = False) -> str:
    """``set_config_value`` threaded as ``box set`` threads it: the box is the target."""
    from kanibako.settings.paths import box_workset_settings_paths

    box_file, _ = box_workset_settings_paths(proj)
    return set_config_value(
        key, value, config_path=box_file, cascade_system_path=std.settings,
        command_scope=ConfigLevel.box, std=std, proj=proj, force=force,
    )


def _set_ws(key: str, value, std, ws) -> str:
    """``set_config_value`` threaded as ``workset set`` threads it: the working set is the target."""
    from kanibako.settings.paths import workset_settings_path

    return set_config_value(
        key, value, config_path=workset_settings_path(ws),
        cascade_system_path=std.settings, command_scope=ConfigLevel.workset,
        std=std, ws=ws,
    )


class TestSetTimeResolvesTheCommandsTarget:
    """Spec §2a, *"Build the full cascade snapshot for the COMMAND's target"*: the
    set-time snapshot is ``settings_launch.resolve_inputs`` for the box, the working
    set or the system scope the command names — never a root rebuilt from where its
    settings files sit.  ⚑ Each ACCEPT row below was refused as a dangling reference
    while the snapshot floored only ``@meta.{workset,box}.path`` from the file paths."""

    def test_a_box_value_resolves_against_the_boxs_own_anchors(
        self, std, config_file, tmp_home,
    ):
        # ``meta.box.home`` hangs off the box's real ``meta.box.path``; only the
        # box's own resolve has it.
        proj = _primary_box(std, config_file, tmp_home)
        message = _set_box("box.canon", "@meta.box.home/canon", std, proj)
        assert message == "Set box.canon=@meta.box.home/canon", message

    def test_a_box_refusal_names_the_boxs_real_root(self, std, config_file, tmp_home):
        proj = _primary_box(std, config_file, tmp_home)
        message = _set_box("box.canon", _BARE, std, proj)
        assert f"{proj.metadata_path / _BARE}   (this key's default root" in message

    def test_a_workset_value_resolves_against_the_worksets_anchors(self, std, tmp_home):
        ws = create_workset("anchorws", tmp_home / "anchorws", std)
        message = _set_ws("workset.auth.path", "@meta.runtime.ws_root/auth", std, ws)
        assert message == "Set workset.auth.path=@meta.runtime.ws_root/auth", message

    @pytest.mark.parametrize("ref", ["@meta.box.path", "@meta.box.home"])
    def test_a_workset_value_needing_a_box_is_judged_without_one(
        self, ref, std, tmp_home,
    ):
        """A working set names no box, and a workset key may not name one: ``meta.box``
        resolves after the workset keys (system-design "Ordering rule")."""
        ws = create_workset("noboxws", tmp_home / "noboxws", std)
        message = _set_ws("workset.auth.path", f"{ref}/auth", std, ws)
        assert message.startswith("Error:"), message
        assert f"points at '{ref}'" in message, message
        assert 'system-design "Ordering rule"' in message, message

    def test_a_write_naming_no_target_gets_no_anchor_from_its_file(self, tmp_path):
        """⚑ NO TARGET, NO ANCHOR: ``meta.workset.path`` is never read off the parent
        of the file being written — the guess spec §2a removes."""
        files = _files(tmp_path)
        message = _set(
            "workset.channelroot", f"@meta.workset.path/{_BARE}", files,
            ConfigLevel.workset,
        )
        assert message.startswith("Error:"), message
        assert "dangling @-reference '@meta.workset.path'" in message


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

    def test_an_EMPTY_value_is_not_this_rules_business(self, tmp_path, std):
        """The guard matches the read-time one (``paths._refuse_bare_relative``): there
        is no bare relative to disambiguate, and calling ``''`` a relative path would be
        a refusal a user cannot act on."""
        from kanibako.project.workset import Workset

        files = _files(tmp_path)
        ws = Workset(name="ws", root=files["workset"].parent, early_system=std.early_system)
        assert not _set(
            "workset.canon", "", files, ConfigLevel.workset, std=std, ws=ws,
        ).startswith("Error:")

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


#: The malformed ``pref:`` entry is the POINT of the three box cases, and the target-less
#: snapshot reads the file it sits in, so the census sees it written.
_WRITES_THE_DOTTED_PREF = pytest.mark.writes_undeclared(
    "pref.system.agent",
    reason="a DOTTED pref entry in box.yaml is the broken config these cases repair; "
           "the target-less set-time snapshot reads that file.",
)


class TestABrokenTargetDoesNotBlockTheRepair:
    """Spec §2a: ``config set`` MUST stay usable to FIX a broken config.  A defect in
    the target's own files (a DOTTED ``pref:`` entry, which ``collect_prefs`` refuses)
    stops ``resolve_inputs``; the write is then judged WITHOUT the target, which can only
    refuse more, and a refusal says why the target was missing."""

    @staticmethod
    def _break(proj):
        from kanibako.settings.paths import box_workset_settings_paths

        box_file, _ = box_workset_settings_paths(proj)
        box_file.parent.mkdir(parents=True, exist_ok=True)
        with box_file.open("a") as fh:
            fh.write("pref:\n  system.agent: claude\n")

    @_WRITES_THE_DOTTED_PREF
    def test_an_unrelated_value_is_written_only_with_force(self, std, config_file, tmp_home):
        """Keyspec §2a: the dotted ``pref`` entry is a bad entry outside the edited value's
        chain, so a plain ``set`` refuses naming it, and ``--force`` writes — the broken
        TARGET still blocks nothing."""
        proj = _primary_box(std, config_file, tmp_home)
        self._break(proj)
        message = _set_box("box.canon", "/abs/canon", std, proj)
        assert message.startswith("Error:") and "pref | system.agent" in message, message
        assert "--force" in message
        message = _set_box("box.canon", "/abs/canon", std, proj, force=True)
        assert message == "Set box.canon=/abs/canon", message

    @_WRITES_THE_DOTTED_PREF
    def test_the_pref_cure_itself_is_still_written(self, std, config_file, tmp_home):
        proj = _primary_box(std, config_file, tmp_home)
        self._break(proj)
        message = _set_box("pref.system.agent", "claude", std, proj)
        assert not message.startswith("Error:"), message

    @_WRITES_THE_DOTTED_PREF
    def test_a_value_needing_the_missing_anchor_names_why_it_is_missing(
        self, std, config_file, tmp_home,
    ):
        proj = _primary_box(std, config_file, tmp_home)
        self._break(proj)
        message = _set_box("box.canon", "@meta.box.home/canon", std, proj)
        assert message.startswith("Error:"), message
        assert "dangling @-reference '@meta.box.home'" in message
        assert "did not resolve:" in message and "DOTTED" in message

    @_WRITES_THE_DOTTED_PREF
    def test_the_fallback_reads_the_same_files_so_it_cannot_accept_more(
        self, std, config_file, tmp_home, capsys,
    ):
        """⚑ THE COUNTEREXAMPLE. The fallback is "strictly less context" only while the
        caller still threads its tier files: without the working-set tier, a valid
        system-level ``workset.canon`` showed through the box's DANGLING working-set
        value and ``@workset.canon/x`` was WRITTEN, though the box's real cascade
        refuses it. Driven through ``box set`` itself, so the caller's threading is
        what is pinned."""
        import argparse

        from kanibako.commands.box._parser import run_set
        from kanibako.settings.paths import box_workset_settings_paths

        proj = _primary_box(std, config_file, tmp_home)
        box_file, workset_file = box_workset_settings_paths(proj)
        std.settings.parent.mkdir(parents=True, exist_ok=True)
        std.settings.write_text("workset:\n  canon: /sys/canon\n")
        workset_file.parent.mkdir(parents=True, exist_ok=True)
        workset_file.write_text('workset:\n  canon: "@workset.nonexistent_zz/c"\n')
        box_file.parent.mkdir(parents=True, exist_ok=True)
        box_file.write_text("box:\n  canon: /first\npref:\n  system.agent: claude\n")
        rc = run_set(argparse.Namespace(
            args=[str(proj.project_path), "box.canon=@workset.canon/x"],
            box=None, force=False,
        ))
        err = capsys.readouterr().err
        assert rc == 1, err
        assert "did not resolve:" in err
        assert "canon: /first" in box_file.read_text()

    def test_a_caller_that_could_not_name_its_target_gets_the_same_rule(self, tmp_path):
        files = _files(tmp_path)
        message = set_config_value(
            "workset.channelroot", f"@meta.workset.path/{_BARE}",
            config_path=files["workset"], cascade_system_path=files["system"],
            command_scope=ConfigLevel.workset, target_error="no std here",
        )
        assert message.startswith("Error:"), message
        assert message.endswith("did not resolve: no std here)")


class TestEveryNounPassesItsTarget:
    """The CALLERS hold the target, so each noun must hand it over — a noun that
    passes none silently falls back to a snapshot with no box or working-set anchor."""

    def test_box_set(self, std, config_file, tmp_home, capsys):
        import argparse

        from kanibako.commands.box._parser import run_set

        proj = _primary_box(std, config_file, tmp_home)
        rc = run_set(argparse.Namespace(
            args=[str(proj.project_path), "box.canon=@meta.box.home/canon"],
            box=None, force=False,
        ))
        assert rc == 0, capsys.readouterr().err

    def test_workset_set(self, std, tmp_home, capsys):
        import argparse

        from kanibako.commands.workset_cmd import run_set

        create_workset("cliws", tmp_home / "cliws", std)
        rc = run_set(argparse.Namespace(
            workset="cliws", key_value="workset.auth.path=@meta.runtime.ws_root/auth",
            force=False,
        ))
        assert rc == 0, capsys.readouterr().err

    def test_system_set_names_the_system_scope(self, config_file, monkeypatch):
        import argparse

        from kanibako.commands.system_cmd import run_set
        from kanibako.settings import config_interface

        seen: dict = {}

        def spy(*args, **kwargs):
            seen.update(kwargs)
            return "Set spied"

        monkeypatch.setattr(config_interface, "set_config_value", spy)
        assert run_set(argparse.Namespace(key_value="system.cache=/srv/c", force=True)) == 0
        assert seen["std"] is not None
        assert seen.get("proj") is None and seen.get("ws") is None


# ---------------------------------------------------------------------------
# The system PATH TIER's @-ref scope (system-design "Ordering rule": a key depends only on preceding sets)
# ---------------------------------------------------------------------------

#: Shapes whose ``@``-ref names a key OUTSIDE the system path tier.  Each parses to a
#: real ref the set-time CASCADE floor can satisfy, and each reads as
#: ``Unknown @-reference`` at the launch — the two doors disagreeing, which is the defect.
_OUT_OF_TIER_SHAPES = [
    "@box.image/x",
    "@box.enable_vault/x",
    "@box.shell/x",
    "@box.image./x",
    "@box.image",
    "@box..image/x",
    "@box.image /x",
]


class TestASystemPathValueMayNotPointOutOfItsTier:
    """``system.template=@box.image/x`` was ACCEPTED and stored, and every launch-seam
    read then failed ``Unknown @-reference: box.image`` — whether or not ``box.image``
    held a value.  The set door judged the value against the full cascade snapshot,
    whose floor carries the box scalars' declared defaults; the system path tier
    resolves through one lookup that sees only the ``config.*`` foundation and the
    single ``system`` level.  The two doors disagreed."""

    @pytest.mark.parametrize("value", _OUT_OF_TIER_SHAPES)
    def test_it_is_refused_and_nothing_is_written(self, value, config_file, tmp_path):
        files = _files(tmp_path)
        message = _set("system.template", value, files, ConfigLevel.system)
        assert message.startswith("Error:"), message
        # ⚑ THE REF AND THE WHY, not a generic complaint: the user must learn WHICH
        # reference was out of scope and that the launch could not read it back.
        assert "@box" in message
        assert "outside the system path tier" in message
        # ⚑ REFUSED BEFORE THE WRITE — a poisoned settings file is what the set-time
        # half exists to prevent.
        written = load_doc(files["system"])
        assert written in ({}, None) or "template" not in str(written)

    def test_the_refusal_holds_whether_or_not_the_referent_has_a_value(
        self, config_file, tmp_path,
    ):
        """The row is explicit that this failed *whether or not* ``box.image`` is null,
        because the box scalars' DECLARED-DEFAULT floor is what let the set door say
        yes in either state.  Both are pinned: with no box tier on the path, and with a
        real box tier file present."""
        for name in ("no-box-tier", "box-tier-present"):
            files = _files(tmp_path / name)
            message = _set("system.template", "@box.image/x", files, ConfigLevel.system)
            assert message.startswith("Error:"), f"{name}: {message}"

    def test_a_non_path_system_ref_is_refused_and_the_message_says_so(
        self, config_file, tmp_path,
    ):
        """``@system.agent`` is a ``system.*`` key but NOT a system PATH key, so the tier
        lookup cannot see it.  With ``system.agent`` set, the E3 probe resolves it, so
        this door is the one that refuses — and its message must not claim that any
        ``@system.*`` key is allowed."""
        files = _files(tmp_path)
        files["system"].write_text("system:\n  agent: claude\n")
        message = _set("system.template", "@system.agent/x", files, ConfigLevel.system)
        assert message.startswith("Error:"), message
        assert "'@system.agent'" in message and "outside the system path tier" in message
        assert "@config.* keys and the system path keys" in message, message
        assert "@system.* keys" not in message, message
        assert "template" not in str(load_doc(files["system"]))

    # ---- the half that breaks quietly: an over-firing door ----

    def test_every_ref_inside_the_tier_is_still_ACCEPTED(self, config_file, tmp_path):
        """The non-regression, swept over the WHOLE tier rather than one key: a value
        referencing ANY other ``system.*`` path key, and ANY ``config.*`` key.  A
        too-eager set door breaks a working box, so this is the case that must not move.
        ``system.template`` is excluded — a self-reference is CYCLIC, the E3 probe's
        own refusal, and is not this door's business."""
        from kanibako.settings.bootstrap import CONFIG_PATH_DEFAULTS, SYSTEM_PATH_DEFAULTS

        referable = [k for k in sorted(SYSTEM_PATH_DEFAULTS) if k != "system.template"]
        referable += sorted(CONFIG_PATH_DEFAULTS)
        assert len(referable) > 10, referable
        for ref in referable:
            files = _files(tmp_path / ref.replace(".", "_"))
            value = f"@{ref}/sub"
            message = _set("system.template", value, files, ConfigLevel.system)
            assert not message.startswith("Error:"), f"{ref}: {message}"
            assert load_doc(files["system"])["system"]["template"] == value

    def test_the_non_ref_shapes_are_untouched(self, config_file, tmp_path):
        for i, value in enumerate(("/srv/t", "~/t", "$XDG_DATA_HOME/t", "${XDG_DATA_HOME}/t")):
            files = _files(tmp_path / f"v{i}")
            message = _set("system.template", value, files, ConfigLevel.system)
            assert not message.startswith("Error:"), f"{value!r}: {message}"

    def test_a_null_is_not_this_doors_business(self, config_file, tmp_path):
        """⚑ A ``null`` at ``system.template`` IS refused — by ``_null_path_key_error``,
        which runs earlier and is the ``--null`` precedent's own rule (spec §2a).  What
        is pinned here is that THIS door adds nothing to that: the predicate passes a
        null straight through, so the null door's own message and rc are the whole
        answer, exactly as before."""
        from kanibako.settings.config import system_path_ref_error

        assert system_path_ref_error("system.template", None) is None
        assert system_path_ref_error("system.template", "") is None
        files = _files(tmp_path)
        message = _set("system.template", None, files, ConfigLevel.system)
        assert message.startswith("Error:")
        assert "null path key" in message, message

    def test_the_scope_is_the_tier_paths_ITSELF_resolves(self):
        """THE ANTI-SECOND-LIST PIN.  The membership this door judges by is the very
        table ``paths._resolve_system_path_keys`` resolves the tier from — the same
        object, not a copy of it — so a key added to the tier is judged here with no
        edit and the scope cannot drift from the launch's."""
        from kanibako.settings import bootstrap, paths
        from kanibako.settings.config import system_path_ref_error

        assert paths.SYSTEM_PATH_DEFAULTS is bootstrap.SYSTEM_PATH_DEFAULTS
        # ⚑ THE PREDICATE ANSWERS THE CARRIER TEXT; the set door prepends ``Error: ``,
        # the same split ``null_path_keys_error`` and the E3 probe both use.  The
        # membership is SYSTEM_PATH_DEFAULTS ENTIRE — the system path tier.
        assert len(paths.SYSTEM_PATH_DEFAULTS) > 5
        for key in paths.SYSTEM_PATH_DEFAULTS:
            message = system_path_ref_error(key, "@box.image/x")
            assert message is not None and key in message and "@box.image" in message, key
        # ⚑ AND ``config.*`` IS NOT IN IT, by its own declaration rather than by an
        # omission here: those six are ``set: file`` with no CLI write route, so
        # ``set_config_value`` refuses them with the §1 message LONG before this door
        # runs.  They are carried by their read-time doors alone.
        for key in bootstrap.CONFIG_PATH_DEFAULTS:
            assert system_path_ref_error(key, "@box.image/x") is None, key

    def test_it_is_not_this_door_for_a_key_outside_the_system_path_tier(self):
        """⚑ THE NARROWNESS, pinned.  A downward ref is a spec question for EVERY key,
        but this door is scoped to the one tier whose reader demonstrably cannot
        resolve it.  A ``box.*``/``workset.*``/``agent.*`` path key and a non-path
        ``system.*`` key answer ``None`` here — widening this one silently would ban
        spellings the box and workset tiers read perfectly well."""
        from kanibako.settings.config import system_path_ref_error

        for key in ("box.canon", "workset.channelroot", "agent.canon", "system.model"):
            assert system_path_ref_error(key, "@box.image/x") is None, key

    def test_the_read_seam_still_reports_the_unknown_reference(self, tmp_home):
        """THE LAUNCH IS UNCHANGED.  A value written by hand, past every set door, is
        still read as ``Unknown @-reference`` — this door changed what may be STORED,
        not what the launch says about a value already in the file."""
        from kanibako.settings.paths import resolve_system_paths

        with pytest.raises(Exception) as exc:
            resolve_system_paths(
                {"system.template": "@box.image/x"},
                data_home=tmp_home / "data", home=tmp_home,
            )
        assert "Unknown @-reference" in str(exc.value), str(exc.value)
