"""A stored ``null`` at ``access``, ``allow_helpers``, or ``continue_mode`` refuses the launch.

The rulings (182-NA, [Jei:2026-10-13]): "'null' should be an error, then", widened to
every key that has a default but no meaning for ``None``. Each of the three keys is
never read as unset (which takes its default). Four layers are pinned here:

- the EARLY check, :func:`~kanibako.commands.start._null_behavior_refusal`, over a
  REAL box, for each key at each scope (box pref, system ``agent.<a>`` and
  ``agent.default``, the agent file), with the typed flags shadowing a stored null;
- the one shared check, :func:`~kanibako.commands.start._refuse_null_behavior`,
  which never refuses a key whose declared default is ``None``;
- the realizer's access fence over a real launch snapshot;
- the wiring in ``_run_container`` and end to end through ``kanibako start``.

An ABSENT key is unchanged throughout: its declared default answers.
"""

from __future__ import annotations

import dataclasses
from unittest.mock import patch

import pytest

from kanibako.commands.start import (
    _ALLOW_HELPERS_NULL,
    _CONTINUE_MODE_NULL,
    _LaunchRealizer,
    _NullRefused,
    _access_null,
    _null_behavior_refusal,
    _refuse_null_behavior,
    _run_container,
)
from kanibako.errors import ConfigError
from kanibako.plugins.claude import ClaudeTarget
from kanibako.settings import core_defaults
from kanibako.settings.config_io import dump_doc
from kanibako.settings.settings_cli_level import build_cli_level
from kanibako.settings.settings_launch import build_launch_snapshot
from kanibako.settings.settings_resolve import ResolveCtx
from kanibako.targets.base import descriptor_floor
from tests.test_commands.test_start import _focused, _focused_box

_KEYS = ("access", "allow_helpers", "continue_mode")
_RULES = {
    "access": _access_null("access"),
    "allow_helpers": _ALLOW_HELPERS_NULL,
    "continue_mode": _CONTINUE_MODE_NULL,
}
_VALUES = {
    "access": "Set it to one of restricted | editing | full",
    "allow_helpers": "Set it to true or false",
    "continue_mode": "Set it to true or false",
}
_FLAGS = {
    "access": " -S or -A overrides it for one launch.",
    "allow_helpers": None,
    "continue_mode": " -N, -C, or -R overrides it for one launch.",
}


def _refusal(proj, system_file=None, *, agent_path=None, rules=None, agent="claude",
             **flags):
    level = build_cli_level(
        selection=None, active_agent=agent if agent != "shell" else None, **flags,
    )
    return _null_behavior_refusal(
        proj, system_file, agent, ClaudeTarget() if agent != "shell" else None,
        std=_focused()["std"], level=level,
        rules=list(rules if rules is not None else _RULES.values()),
        agent_path=agent_path,
    )


def _head(where, line):
    return f"{where} sets this agent key to null:\n  {line}\n"


@pytest.mark.usefixtures("config_file")
class TestTheEarlyCheck:
    """``_null_behavior_refusal`` names the file and the key that answered, and the cure."""

    @pytest.mark.parametrize("key", _KEYS)
    def test_a_box_pref_null_is_named_with_its_file(self, tmp_path, key):
        proj = _focused_box(tmp_path)
        box_file = proj.metadata_path / "box.yaml"
        dump_doc(box_file, {"pref": {"agent": {"claude": {key: None}}}})
        msg = _refusal(proj)
        assert msg is not None
        assert msg.startswith(_head(box_file, f"pref.agent.claude.{key}")), msg
        assert _VALUES[key] + ", or delete the line to use the default." in msg
        if _FLAGS[key] is None:
            assert "overrides it" not in msg
        else:
            assert msg.endswith(_FLAGS[key])

    @pytest.mark.parametrize("key", _KEYS)
    @pytest.mark.parametrize("node", ["claude", "default"])
    def test_a_system_file_null_is_named(self, tmp_path, key, node):
        proj = _focused_box(tmp_path)
        sys_file = tmp_path / "system.yaml"
        dump_doc(sys_file, {"agent": {node: {key: None}}})
        msg = _refusal(proj, sys_file)
        assert msg is not None and msg.startswith(_head(sys_file, f"agent.{node}.{key}"))

    @pytest.mark.parametrize("key", _KEYS)
    def test_an_agent_file_null_is_named(self, tmp_path, key):
        proj = _focused_box(tmp_path)
        agent_file = tmp_path / "agents" / "claude" / "agent.yaml"
        agent_file.parent.mkdir(parents=True)
        dump_doc(agent_file, {"self": {key: None}})
        msg = _refusal(proj, agent_path=agent_file)
        assert msg is not None and msg.startswith(_head(agent_file, f"agent.claude.{key}"))

    def test_every_offending_key_is_named(self, tmp_path):
        proj = _focused_box(tmp_path)
        sys_file = tmp_path / "system.yaml"
        dump_doc(sys_file, {"agent": {"claude": {k: None for k in _KEYS}}})
        msg = _refusal(proj, sys_file)
        assert msg is not None
        for key in _KEYS:
            assert _head(sys_file, f"agent.claude.{key}") in msg

    def test_absent_keys_are_not_refused(self, tmp_path):
        proj = _focused_box(tmp_path)
        assert _refusal(proj) is None

    def test_a_value_is_not_refused(self, tmp_path):
        proj = _focused_box(tmp_path)
        sys_file = tmp_path / "system.yaml"
        dump_doc(sys_file, {"agent": {"default": {k: None for k in _KEYS}}})
        agent_file = tmp_path / "agents" / "claude" / "agent.yaml"
        agent_file.parent.mkdir(parents=True)
        dump_doc(agent_file, {"self": {
            "access": "restricted", "allow_helpers": False, "continue_mode": True,
        }})
        # The node's values shadow the default slot's nulls.
        assert _refusal(proj, sys_file, agent_path=agent_file) is None

    @pytest.mark.parametrize(
        ("key", "flags"),
        [
            ("access", {"secure": True}),
            ("access", {"autonomous": True}),
            ("continue_mode", {"new_session": True}),
            ("continue_mode", {"continue_session": True}),
            ("continue_mode", {"resume": True}),
        ],
    )
    def test_a_typed_flag_shadows_a_stored_null(self, tmp_path, key, flags):
        proj = _focused_box(tmp_path)
        box_file = proj.metadata_path / "box.yaml"
        dump_doc(box_file, {"pref": {"agent": {"claude": {key: None}}}})
        assert _refusal(proj, rules=[_RULES[key]], **flags) is None
        # Without the flag, the same null refuses.
        assert _refusal(proj, rules=[_RULES[key]]) is not None

    def test_a_shell_box_null_allow_helpers_is_refused(self, tmp_path):
        """The shell tier's own value: a plain-shell or agent-less launch reads it too."""
        proj = _focused_box(tmp_path)
        sys_file = tmp_path / "system.yaml"
        dump_doc(sys_file, {"agent": {"shell": {"allow_helpers": None}}})
        msg = _refusal(proj, sys_file, agent="shell", rules=[_ALLOW_HELPERS_NULL])
        assert msg is not None and msg.startswith(
            _head(sys_file, "agent.shell.allow_helpers")
        )

    def test_a_default_slot_null_does_not_reach_the_shell_tier(self, tmp_path):
        proj = _focused_box(tmp_path)
        sys_file = tmp_path / "system.yaml"
        dump_doc(sys_file, {"agent": {"default": {"allow_helpers": None}}})
        assert _refusal(
            proj, sys_file, agent="shell", rules=[_ALLOW_HELPERS_NULL],
        ) is None


def _snapshot(system_file, cli_level=None):
    """claude's launch snapshot over its REAL floors, with *cli_level* on top."""
    return build_launch_snapshot(
        agent_name="claude",
        ctx=ResolveCtx(
            agent_name="claude", workset_name=None, host_home="/home/host",
            xdg={"XDG_DATA_HOME": "/data"},
        ),
        system_path=system_file,
        agent_path=None,
        workset_path=None,
        box_path=None,
        behavior_floor=core_defaults.behavior_defaults(),
        agent_behavior_floor=descriptor_floor(ClaudeTarget().setting_descriptors()),
        cli_level=cli_level,
    )


class TestTheSharedCheck:
    """``_refuse_null_behavior``: the one check every door calls."""

    @pytest.mark.parametrize("key", _KEYS)
    def test_a_present_none_refuses_and_absent_does_not(self, tmp_path, key):
        sys_file = tmp_path / "system.yaml"
        dump_doc(sys_file, {"agent": {"claude": {key: None}}})
        msg = _refuse_null_behavior(_snapshot(sys_file), "claude", _RULES[key])
        assert msg is not None and msg.startswith(
            "the settings resolved for this box set this agent key to null:\n"
            f"  agent.claude.{key}\n"
        )
        dump_doc(sys_file, {})
        assert _refuse_null_behavior(_snapshot(sys_file), "claude", _RULES[key]) is None

    def test_a_key_whose_declared_default_is_none_is_never_refused(self, tmp_path):
        """``model``'s declared default is ``<None>``: a ``null`` is that default."""
        assert core_defaults.behavior_defaults()["model"] is None
        sys_file = tmp_path / "system.yaml"
        dump_doc(sys_file, {"agent": {"claude": {"model": None}}})
        rule = _NullRefused("model", "a model name", "")
        assert _refuse_null_behavior(_snapshot(sys_file), "claude", rule) is None

    def test_the_three_keys_declare_a_value_default(self):
        defaults = core_defaults.behavior_defaults()
        for key in _KEYS:
            assert defaults[key] is not None, key


def _realize(system_file, *, desc=None, secure=False, autonomous=False):
    level = build_cli_level(
        selection=None, active_agent="claude", secure=secure, autonomous=autonomous,
    )
    return _LaunchRealizer(
        desc=desc or ClaudeTarget().descriptor, agent_id="claude", cli_level=level,
    )(_snapshot(system_file, level))


class TestTheRealizerFence:
    """The realizer refuses a present ``None`` at the access key on the main snapshot."""

    @pytest.mark.parametrize("node", ["claude", "default"])
    def test_a_stored_null_refuses(self, tmp_path, node):
        sys_file = tmp_path / "system.yaml"
        dump_doc(sys_file, {"agent": {node: {"access": None}}})
        with pytest.raises(ConfigError) as exc:
            _realize(sys_file)
        assert str(exc.value).startswith(
            "the settings resolved for this box set this agent key to null:\n"
            f"  agent.{node}.access\n"
        )

    @pytest.mark.parametrize(
        ("flags", "tier"),
        [({"secure": True}, "restricted"), ({"autonomous": True}, "full")],
    )
    def test_a_typed_flag_shadows_a_stored_null(self, tmp_path, flags, tier):
        sys_file = tmp_path / "system.yaml"
        dump_doc(sys_file, {"agent": {"claude": {"access": None}}})
        assert _realize(sys_file, **flags).access == tier

    def test_an_absent_key_takes_the_full_floor(self, tmp_path):
        sys_file = tmp_path / "system.yaml"
        dump_doc(sys_file, {})
        assert _realize(sys_file).access == "full"

    def test_a_descriptor_with_no_setting_key_reads_no_stored_null(self, tmp_path):
        """No ``setting_key``: the stored key is never redeemed, so nothing refuses."""
        sys_file = tmp_path / "system.yaml"
        dump_doc(sys_file, {"agent": {"claude": {"access": None}}})
        desc = ClaudeTarget().descriptor
        desc = dataclasses.replace(
            desc,
            access_realization=dataclasses.replace(
                desc.access_realization, setting_key="",
            ),
        )
        assert _realize(sys_file, desc=desc).access == "full"


class TestRunContainerWiring:
    """``_run_container``: refused before the rig prep, and fenced on the main snapshot."""

    def _kwargs(self, **over):
        base = dict(
            project_dir=None, entrypoint=None, image_override=None,
            new_session=False, safe_mode=False, resume_mode=False, extra_args=[],
        )
        base.update(over)
        return base

    def test_a_refusal_stops_before_the_rig_prep(self, start_mocks, capsys):
        with start_mocks() as m:
            m.null_behavior_refusal.return_value = "X sets this agent key to null"
            rc = _run_container(**self._kwargs())
        assert rc == 1
        assert "Error: X sets this agent key to null" in capsys.readouterr().err
        m.load_registry.assert_not_called()
        m.launch_check.assert_not_called()
        m.runtime.run.assert_not_called()
        kw = m.null_behavior_refusal.call_args.kwargs
        assert [r.key for r in kw["rules"]] == [
            "allow_helpers", "transform", "continue_mode", "access",
        ]

    @pytest.mark.parametrize(
        ("over", "entry"),
        [
            ({"safe_mode": True}, ("access", "restricted")),
            ({"autonomous": True}, ("access", "full")),
            ({"new_session": True}, ("continue_mode", False)),
            ({"continue_override": True}, ("continue_mode", True)),
        ],
    )
    def test_the_typed_flags_ride_the_level(self, start_mocks, over, entry):
        with start_mocks() as m:
            _run_container(**self._kwargs(**over))
        level = m.null_behavior_refusal.call_args.kwargs["level"]
        key, value = entry
        node = m.null_behavior_refusal.call_args.args[2]
        assert level[f"agent.{node}.{key}"] == value

    def test_the_check_reads_the_launchs_one_level(self, start_mocks):
        """The level the launch built, not a second one: ``--model`` rides it too."""
        with start_mocks() as m:
            _run_container(**self._kwargs(model_override="opus"))
        level = m.null_behavior_refusal.call_args.kwargs["level"]
        node = m.null_behavior_refusal.call_args.args[2]
        assert level[f"agent.{node}.model"] == "opus"

    def test_a_refused_restart_leaves_the_running_box_alone(self, start_mocks):
        with start_mocks() as m, patch("kanibako.commands.stop._stop_one") as stop:
            m.runtime.is_running.return_value = True
            m.runtime.container_exists.return_value = True
            m.runtime.inspect_env.return_value = "claude"
            m.null_behavior_refusal.return_value = "X sets this agent key to null"
            rc = _run_container(**self._kwargs(persistent=True, restart=True))
        assert rc == 1
        stop.assert_not_called()
        m.runtime.run.assert_not_called()

    def test_a_restart_the_bootstrap_guard_refuses_leaves_the_box_alone(
        self, start_mocks, capsys,
    ):
        """The same ordering for the other settings refusal ahead of the stop."""
        with start_mocks() as m, patch("kanibako.commands.stop._stop_one") as stop:
            m.runtime.is_running.return_value = True
            m.runtime.container_exists.return_value = True
            m.runtime.inspect_env.return_value = "claude"
            m.bootstrap_program.return_value = "none"
            rc = _run_container(**self._kwargs(persistent=True, restart=True))
        assert rc == 1
        assert "cannot run a persistent session" in capsys.readouterr().err
        stop.assert_not_called()

    @pytest.mark.parametrize("key", ["allow_helpers", "continue_mode"])
    def test_the_main_snapshot_read_refuses(self, start_mocks, key):
        """A null the early read could not see still stops the launch at the read."""
        from kanibako.commands import start as start_mod

        real = start_mod._refuse_null_behavior

        def fake(snapshot, agent_id, rule, **kw):
            if rule.key == key:
                return f"fenced {key}"
            return real(snapshot, agent_id, rule, **kw)

        with start_mocks() as m, patch.object(start_mod, "_refuse_null_behavior", fake):
            with pytest.raises(ConfigError, match=f"fenced {key}"):
                _run_container(**self._kwargs())
        m.runtime.run.assert_not_called()


class TestEndToEnd:
    """Through ``kanibako start`` on a box ``create`` made."""

    @pytest.mark.parametrize("key", (*_KEYS, "bootstrap"))
    def test_a_box_pref_null_refuses_the_start(
        self, config_file, tmp_home, credentials_dir, capsys, key,
    ):
        from kanibako import cli
        from kanibako.settings.config import load_config
        from kanibako.settings.config_io import load_doc
        from kanibako.settings.paths import load_std_paths

        box_dir = tmp_home / "nullbox"
        box_dir.mkdir()
        with pytest.raises(SystemExit) as created:
            cli.main(["create", str(box_dir), "--agent", "claude"])
        assert created.value.code == 0, capsys.readouterr().err
        box_file = load_std_paths(load_config(config_file)).boxes / "nullbox" / "box.yaml"
        doc = load_doc(box_file)
        doc.setdefault("pref", {}).setdefault("agent", {}).setdefault(
            "claude", {},
        )[key] = None
        dump_doc(box_file, doc)
        capsys.readouterr()

        with (
            patch("kanibako.commands.start.ContainerRuntime.run") as run,
            patch("kanibako.commands.start.resolve_rig") as rig,
        ):
            with pytest.raises(SystemExit) as started:
                cli.main(["start", str(box_dir)])
        err = capsys.readouterr().err
        assert started.value.code == 1, err
        assert "Error: " + _head(box_file, f"pref.agent.claude.{key}") in err
        rig.assert_not_called()
        run.assert_not_called()


# ---------------------------------------------------------------------------
# The same rule at ``bootstrap``, ``transform``, and ``label``
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("config_file")
class TestBootstrapAndTransform:
    """``bootstrap`` (default tmux) and claude's ``transform`` (default tweakcc) refuse a null."""

    @pytest.mark.parametrize("node", ["claude", "default"])
    def test_a_null_bootstrap_refuses_where_it_is_read(self, tmp_path, node):
        from kanibako.commands.start import _bootstrap_choice

        proj = _focused_box(tmp_path)
        sys_file = tmp_path / "system.yaml"
        dump_doc(sys_file, {"agent": {node: {"bootstrap": None}}})
        with pytest.raises(ConfigError) as exc:
            _bootstrap_choice(proj, sys_file, "claude", **_focused())
        msg = str(exc.value)
        assert msg.startswith(_head(sys_file, f"agent.{node}.bootstrap")), msg
        assert "Set it to a program such as tmux, or none," in msg

    def test_none_and_empty_bootstrap_are_still_values(self, tmp_path):
        from kanibako.commands.start import _bootstrap_choice

        proj = _focused_box(tmp_path)
        sys_file = tmp_path / "system.yaml"
        for value, program in (("none", "none"), ("", None)):
            dump_doc(sys_file, {"agent": {"claude": {"bootstrap": value}}})
            assert _bootstrap_choice(proj, sys_file, "claude", **_focused()).program == program

    def test_a_null_transform_refuses_for_claude(self, tmp_path):
        from kanibako.commands.start import _TRANSFORM_NULL, _effective_transform

        proj = _focused_box(tmp_path)
        sys_file = tmp_path / "system.yaml"
        dump_doc(sys_file, {"agent": {"claude": {"transform": None}}})
        msg = _refusal(proj, sys_file, rules=[_TRANSFORM_NULL])
        assert msg is not None and msg.startswith(_head(sys_file, "agent.claude.transform"))
        with pytest.raises(ConfigError, match="agent.claude.transform"):
            _effective_transform(
                proj, sys_file, "claude", ClaudeTarget(), **_focused(),
            )
        # ``""`` is the spelling for no transform, and stays legal.
        dump_doc(sys_file, {"agent": {"claude": {"transform": ""}}})
        assert _refusal(proj, sys_file, rules=[_TRANSFORM_NULL]) is None
        assert _effective_transform(
            proj, sys_file, "claude", ClaudeTarget(), **_focused(),
        ) is None

    def test_a_default_slot_null_transform_is_not_claudes(self, tmp_path):
        """``agent.default.transform``'s default is ``<None>``; claude's own row wins anyway."""
        from kanibako.commands.start import _TRANSFORM_NULL, _effective_transform

        proj = _focused_box(tmp_path)
        sys_file = tmp_path / "system.yaml"
        dump_doc(sys_file, {"agent": {"default": {"transform": None}}})
        assert _refusal(proj, sys_file, rules=[_TRANSFORM_NULL]) is None
        assert _effective_transform(
            proj, sys_file, "claude", ClaudeTarget(), **_focused(),
        ) == "tweakcc"

    def test_a_floor_none_under_a_pluginless_target_is_not_a_stored_null(self, tmp_path):
        """No plugin row reaches the snapshot (``ShellTarget``), so its transform default
        is core's ``<None>``: the ``None`` the pick lands on is that floor, never a
        stored ``null``, and nothing refuses. Same for the early check."""
        from kanibako.commands.start import _TRANSFORM_NULL, _effective_transform
        from kanibako.targets.shell import ShellTarget

        proj = _focused_box(tmp_path)
        assert _effective_transform(
            proj, None, "claude", ShellTarget(), **_focused(),
        ) is None
        level = build_cli_level(selection=None, active_agent="claude")
        assert _null_behavior_refusal(
            proj, None, "claude", ShellTarget(), std=_focused()["std"], level=level,
            rules=[_TRANSFORM_NULL],
        ) is None

    def test_the_effective_defaults(self):
        from kanibako.settings.config_interface import effective_behavior_default

        assert effective_behavior_default("claude", "transform") == "tweakcc"
        assert effective_behavior_default("default", "transform") is None
        assert effective_behavior_default("shell", "transform") is None
        assert effective_behavior_default("shell", "bootstrap") == "tmux"
        assert effective_behavior_default("shell", "continue_mode") is None
        assert effective_behavior_default("claude", "label") == "Claude Code"
        assert effective_behavior_default("default", "model") is None

    def test_the_early_check_reads_transform_only_where_the_launch_does(self, start_mocks):
        with start_mocks() as m:
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False, extra_args=[],
            )
        rules = [r.key for r in m.null_behavior_refusal.call_args.kwargs["rules"]]
        assert rules == ["allow_helpers", "transform", "continue_mode", "access"]


def _cli(*argv):
    import contextlib
    import io

    from kanibako.cli import main

    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        try:
            main(list(argv))
            rc = 0
        except SystemExit as e:
            rc = e.code if isinstance(e.code, int) else 1
    return rc, out.getvalue()


class TestTheSetDoor:
    """``set --null`` at a refused key writes nothing, at every noun (keyspec §2h)."""

    @pytest.mark.parametrize(
        "key",
        [
            "agent.claude.access", "agent.default.allow_helpers", "agent.claude.continue_mode",
            "agent.default.bootstrap", "agent.claude.transform", "agent.claude.label",
            "agent.shell.bootstrap", "bootstrap",
        ],
    )
    def test_system_set_null_is_refused(self, config_file, credentials_dir, key):
        from kanibako.settings.config import load_config
        from kanibako.settings.paths import load_std_paths

        settings = load_std_paths(load_config(config_file)).settings
        before = settings.read_bytes() if settings.exists() else None
        rc, out = _cli("system", "set", "--null", key)
        assert rc == 1, out
        assert "the launch refuses a null at this agent key, so this set is refused" in out
        assert key in out and "Nothing was written" in out
        assert (settings.read_bytes() if settings.exists() else None) == before

    @pytest.mark.parametrize(
        "key", ["transform", "agent.shell.continue_mode", "agent.claude.model"],
    )
    def test_a_key_whose_default_is_none_still_takes_a_null(
        self, config_file, credentials_dir, key,
    ):
        rc, out = _cli("system", "set", "--null", key)
        assert rc == 0, out

    def test_box_and_agent_set_null_are_refused(
        self, config_file, tmp_home, credentials_dir,
    ):
        box_dir = tmp_home / "nullset"
        box_dir.mkdir()
        rc, out = _cli("create", str(box_dir), "--agent", "claude")
        assert rc == 0, out
        rc, out = _cli("box", "set", "nullset", "--null", "pref.agent.claude.bootstrap")
        assert rc == 1, out
        assert "pref.agent.claude.bootstrap" in out and "Nothing was written" in out
        rc, out = _cli("agent", "set", "claude", "--null", "label")
        assert rc == 1, out
        assert "Nothing was written" in out

    def test_workset_set_null_is_refused(self, config_file, tmp_home, credentials_dir):
        from kanibako.project.workset import create_workset
        from kanibako.settings.config import load_config
        from kanibako.settings.paths import load_std_paths

        std = load_std_paths(load_config(config_file))
        create_workset("wsn", (tmp_home / "wsn").resolve(), std)
        rc, out = _cli("workset", "set", "wsn", "--null", "pref.agent.claude.continue_mode")
        assert rc == 1, out
        assert "pref.agent.claude.continue_mode" in out and "Nothing was written" in out
