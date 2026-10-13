"""§1A CREATE EXCEPTION — every shadowing flag persists at box creation, and only there.

system-design § *What a flag does*: "at box CREATION only, a shadowing flag's value
PERSISTS — it INITIALIZES the box's stored config … ALL shadowing flags act UNIFORMLY".
The helper (``config_interface.persist_creation_level``) saves exactly what
``build_cli_level`` installs; ``-e`` follows 181-CX1 (a) [Jei:2026-10-13]
(``config_interface.persist_creation_env``). Real box files under a temp std; the launch
wiring is driven through ``start_mocks`` with the helper observed.
"""

from __future__ import annotations

import argparse
from unittest.mock import patch

import pytest

from kanibako.commands.start import _persist_or_announce_flags, _run_container
from kanibako.errors import KanibakoError
from kanibako.settings.config_interface import persist_creation_env, persist_creation_level
from kanibako.settings.config_io import load_doc
from kanibako.settings.paths import box_workset_settings_paths
from kanibako.settings.settings_categories import ENV, CategoryEntry
from kanibako.settings.settings_cli_level import build_cli_level
from kanibako.settings.store_collapse import (
    CLI_PROVENANCE_SCOPE,
    CollapsedEnv,
    collapse_env,
)


def _paths(env):
    return box_workset_settings_paths(env.proj)


def _bytes(path):
    return path.read_bytes() if path.exists() else None


def _persist(env, level, typed_agent=None):
    box, ws = _paths(env)
    persist_creation_level(
        level, typed_agent=typed_agent, box_settings_path=box,
        workset_settings_path=ws, std=env.std, proj=env.proj,
    )
    return load_doc(box)


def _pref_agent(doc, agent):
    return doc.get("pref", {}).get("agent", {}).get(agent, {})


class TestEveryFlagPersistsAtCreation:
    """Each flag × the helper, against the real box file.

    A value is stored as ``kanibako box set <key>=<value>`` stores it: the helper
    writes through the same ``set_config_value``, so a bool level value lands as the
    word ``set`` accepts (``false``/``true``), which ``coerce_bool`` reads back.
    """

    @pytest.mark.parametrize(("flags", "leaf", "value"), [
        ({"model": "m-1"}, "model", "m-1"),
        ({"new_session": True}, "continue_mode", "false"),
        ({"continue_session": True}, "continue_mode", "true"),
        ({"resume": True}, "continue_mode", "true"),
        ({"secure": True}, "access", "restricted"),
        ({"autonomous": True}, "access", "full"),
    ])
    def test_agent_flag_becomes_its_pref_request(self, project_env, flags, leaf, value):
        doc = _persist(project_env, build_cli_level(active_agent="claude", **flags))
        assert _pref_agent(doc, "claude") == {leaf: value}
        # Never an upward write of the agent key itself.
        assert "agent" not in doc

    def test_image_and_share_images_initialize_the_box_tier(self, project_env):
        doc = _persist(project_env, build_cli_level(image="custom:v1", share_images=True))
        assert doc["box"] == {"image": "custom:v1", "share_images": True}

    def test_typed_agent_becomes_the_selection_request(self, project_env):
        level = build_cli_level(selection={"system.agent": "claude"})
        doc = _persist(project_env, level, typed_agent="claude")
        assert doc["pref"]["system"]["agent"] == "claude"

    def test_a_resolved_selection_alone_is_not_saved(self, project_env):
        """No ``--agent`` typed: the cascade's own pick is not a choice to store."""
        box, _ = _paths(project_env)
        before = _bytes(box)
        _persist(project_env, build_cli_level(selection={"system.agent": "claude"}))
        assert _bytes(box) == before

    def test_agent_flags_write_only_the_launched_agent(self, project_env):
        level = build_cli_level(
            selection={"system.agent": "goose"}, active_agent="goose",
            model="g-1", secure=True,
        )
        doc = _persist(project_env, level, typed_agent="goose")
        assert set(doc["pref"]["agent"]) == {"goose"}
        assert _pref_agent(doc, "goose") == {"model": "g-1", "access": "restricted"}
        assert doc["pref"]["system"]["agent"] == "goose"

    def test_a_refused_write_raises_by_key_and_leaves_the_file(self, project_env):
        box, _ = _paths(project_env)
        before = _bytes(box)
        level = {"box.image": "custom:v1", "agent.nosuchagent.model": "m"}
        with pytest.raises(KanibakoError) as exc:
            _persist(project_env, level)
        assert "pref.agent.nosuchagent.model" in str(exc.value)
        assert "drop the flag" in str(exc.value)
        # The box.image written first is put back too: no partial write.
        assert _bytes(box) == before


class TestUnknownLevelKeys:
    """A level key the helper has no writer for is refused by name, never dropped."""

    @pytest.mark.parametrize("key", ["workset.image", "box.nosuchleaf"])
    def test_an_unwritable_key_raises_by_name(self, project_env, key):
        box, _ = _paths(project_env)
        before = _bytes(box)
        with pytest.raises(KanibakoError) as exc:
            _persist(project_env, {key: "x", "agent.claude.model": "m"})
        assert key in str(exc.value)
        assert _bytes(box) == before


def _slots(*rows, cli_env):
    """The REAL collapse over (scope, VAR, value) rows, then the ``-e`` overlay."""
    entries = [
        CategoryEntry(
            category="env", scope=scope, box_dest=var, host_src=None, delivery=ENV,
            options=value, name=var, key_segments=(scope, "env", var),
        )
        for scope, var, value in rows
    ]
    return collapse_env(entries, cli_env)


def _secret_names(*rows):
    """The REAL :func:`secret_path_names` over (scope, VAR) ``secret_path`` rows."""
    from kanibako.settings.settings_categories import MOUNT, secret_path_names

    return secret_path_names([
        CategoryEntry(
            category="secret_path", scope=scope, box_dest=f"/run/secrets/{var}",
            host_src=f"/host/{var}.key", delivery=MOUNT, options=None, name=var,
            key_segments=(scope, "secret_path", var),
        )
        for scope, var in rows
    ])


def _persist_env(env, cli_env, slots, secret_names=None):
    box, ws = _paths(env)
    persist_creation_env(
        cli_env, slots, secret_names=secret_names or {}, box_settings_path=box,
        workset_settings_path=ws, std=env.std, proj=env.proj,
    )
    return load_doc(box)


class TestEnvAtCreation:
    """181-CX1 (a) [Jei:2026-10-13]: unowned VAR → ``box.env.VAR``; another scope's
    VAR → refused."""

    def test_a_var_no_key_owns_is_saved_as_box_env(self, project_env):
        cli_env = {"FOO": "1"}
        doc = _persist_env(project_env, cli_env, _slots(cli_env=cli_env))
        assert doc["box"]["env"] == {"FOO": "1"}

    def test_a_var_a_box_env_key_owns_is_saved(self, project_env):
        """Same key, nearer file: no second scope names the variable."""
        cli_env = {"COLORTERM": "x"}
        slots = _slots(("box", "COLORTERM", "truecolor"), cli_env=cli_env)
        doc = _persist_env(project_env, cli_env, slots)
        assert doc["box"]["env"] == {"COLORTERM": "x"}

    @pytest.mark.parametrize("scope", ["system", "agent", "workset"])
    def test_a_var_another_scope_owns_is_refused_by_name(self, project_env, scope):
        box, _ = _paths(project_env)
        before = _bytes(box)
        cli_env = {"FOO": "1", "BAR": "2"}
        slots = _slots((scope, "BAR", "held"), cli_env=cli_env)
        with pytest.raises(KanibakoError) as exc:
            _persist_env(project_env, cli_env, slots)
        assert str(exc.value) == (
            f"-e BAR cannot be saved: {scope}.env.BAR already sets BAR, and a "
            f"box.env.BAR beside it would refuse every later launch.\n"
            f"  The box was created and registered with its other flags saved; "
            f"no -e value was saved.\n"
            f"  On the next launch -e BAR is per-run; to keep the value, set "
            f"{scope}.env.BAR instead."
        )
        # Refused before anything is written: FOO is not saved either.
        assert _bytes(box) == before

    @pytest.mark.parametrize("scope", ["system", "agent", "workset", "box"])
    def test_a_var_a_secret_path_key_names_is_refused_by_name(self, project_env, scope):
        """``secret_path`` is MOUNT delivery, so the env collapse sees the VAR as
        vacant; a saved ``box.env.VAR`` would meet spec §2a's two-family refusal on
        every later launch."""
        box, _ = _paths(project_env)
        before = _bytes(box)
        cli_env = {"FOO": "1", "TOK": "2"}
        with pytest.raises(KanibakoError) as exc:
            _persist_env(
                project_env, cli_env, _slots(cli_env=cli_env),
                _secret_names((scope, "TOK")),
            )
        key = f"{scope}.secret_path.TOK"
        # ⚑ The cure never says "set <secret_path key>": that key holds a PATH.
        assert str(exc.value) == (
            f"-e TOK cannot be saved: {key} already sets TOK, and a "
            f"box.env.TOK beside it would refuse every later launch.\n"
            f"  The box was created and registered with its other flags saved; "
            f"no -e value was saved.\n"
            f"  On the next launch -e TOK is per-run; to keep the value, change the "
            f"file {key} points at, or remove {key} and set box.env.TOK."
        )
        assert _bytes(box) == before

    def test_a_null_secret_path_is_no_owner(self, project_env):
        """A keyless (present-null) pointer is not weighed by the two-family refusal,
        so it does not block the save either."""
        from kanibako.settings.settings_categories import MOUNT, secret_path_names

        names = secret_path_names([CategoryEntry(
            category="secret_path", scope="system", box_dest="/run/secrets/TOK",
            host_src=None, delivery=MOUNT, options=None, name="TOK",
            key_segments=("system", "secret_path", "TOK"),
        )])
        cli_env = {"TOK": "2"}
        doc = _persist_env(project_env, cli_env, _slots(cli_env=cli_env), names)
        assert doc["box"]["env"] == {"TOK": "2"}


class TestTheSeam:
    """``_persist_or_announce_flags`` on a real box: creating vs existing."""

    def _seam(self, env, level, *, image=None, typed_agent=None):
        box, ws = _paths(env)
        _persist_or_announce_flags(
            env.proj, box, image_override=image, share_images=False,
            cli_level=level, typed_agent=typed_agent,
            workset_settings_path=ws, std=env.std,
        )
        return box

    def _level(self):
        return build_cli_level(
            selection={"system.agent": "claude"}, active_agent="claude",
            model="m-1", new_session=True, autonomous=True, image="custom:v1",
        )

    def test_a_creating_launch_saves_its_whole_level(self, project_env):
        assert project_env.proj.is_new
        doc = load_doc(self._seam(
            project_env, self._level(), image="custom:v1", typed_agent="claude",
        ))
        assert doc["box"]["image"] == "custom:v1"
        assert doc["pref"]["system"]["agent"] == "claude"
        assert _pref_agent(doc, "claude") == {
            "model": "m-1", "continue_mode": "false", "access": "full",
        }

    def test_an_existing_box_file_is_byte_identical(self, project_env, capsys):
        project_env.proj.is_new = False
        box, _ = _paths(project_env)
        box.parent.mkdir(parents=True, exist_ok=True)
        box.write_text("box:\n  image: stored:v0\n")
        before = box.read_bytes()
        self._seam(project_env, self._level(), image="custom:v1", typed_agent="claude")
        assert box.read_bytes() == before
        # The image flags still say they are per-run (Q2 (a)).
        assert "--image custom:v1 applies to THIS launch only" in capsys.readouterr().err

    def test_agent_flags_on_an_existing_box_say_nothing(self, project_env, capsys):
        project_env.proj.is_new = False
        level = build_cli_level(active_agent="claude", model="m-2")
        self._seam(project_env, level)
        assert "applies to THIS launch only" not in capsys.readouterr().err


class TestCreateCommand:
    """``kanibako create -i X --agent A`` through the same helper."""

    def test_create_saves_image_and_agent(self, config_file, tmp_home, credentials_dir):
        from kanibako.commands.box._parser import run_create
        from kanibako.settings.config import load_config
        from kanibako.settings.paths import load_std_paths, resolve_project

        ns = argparse.Namespace(
            path=str(tmp_home / "project"), standalone=False, no_vault=True,
            name=None, image="custom:v1", agent="claude", allow_home=False,
        )
        assert run_create(ns) == 0
        config = load_config(config_file)
        proj = resolve_project(
            load_std_paths(config), config, project_dir=str(tmp_home / "project"),
            initialize=False,
        )
        doc = load_doc(box_workset_settings_paths(proj)[0])
        assert doc["box"]["image"] == "custom:v1"
        assert doc["pref"]["system"]["agent"] == "claude"


#: A ``secret_path_names`` answer no other route produces: seen at the helper only if
#: the launch handed it ``LaunchDeliveries.secret_names``.
_CARRIER_ROW = {"ONLY_FROM_THE_CARRIER": ["workset.secret_path.ONLY_FROM_THE_CARRIER"]}


class TestLaunchWiring:
    """``_run_container`` hands the helpers this launch's level, and only when creating."""

    def _run(self, **kw):
        base = dict(
            project_dir=None, entrypoint=None, image_override=None,
            new_session=False, safe_mode=False, resume_mode=False, extra_args=[],
        )
        base.update(kw)
        return _run_container(**base)

    def test_creating_start_passes_every_flag(self, start_mocks):
        with start_mocks() as m, patch(
            "kanibako.commands.start.persist_creation_level",
        ) as m_helper:
            m.proj.is_new = True
            self._run(
                model_override="m-1", new_session=True, safe_mode=True,
                explicit_agent="claude", image_override="custom:v1",
                share_images=True,
            )
        m_helper.assert_called_once()
        level = m_helper.call_args.args[0]
        assert level["agent.claude.model"] == "m-1"
        assert level["agent.claude.continue_mode"] is False
        assert level["agent.claude.access"] == "restricted"
        assert level["box.image"] == "custom:v1"
        assert level["box.share_images"] is True
        assert m_helper.call_args.kwargs["typed_agent"] == "claude"

    def test_entrypoint_launch_installs_no_agent_pref(self, start_mocks):
        with start_mocks() as m, patch(
            "kanibako.commands.start.persist_creation_level",
        ) as m_helper:
            m.proj.is_new = True
            self._run(entrypoint="/bin/sh", model_override="m-1", image_override="x:1")
        level = m_helper.call_args.args[0]
        assert not [k for k in level if k.startswith("agent.")]
        assert level["box.image"] == "x:1"

    def test_existing_box_calls_neither_helper(self, start_mocks):
        with start_mocks() as m, patch(
            "kanibako.commands.start.persist_creation_level",
        ) as m_helper, patch(
            "kanibako.commands.start.persist_creation_env",
        ) as m_env:
            m.proj.is_new = False
            self._run(model_override="m-1", cli_env=["FOO=1"])
        m_helper.assert_not_called()
        m_env.assert_not_called()

    def test_creating_launch_persists_e_from_the_collapsed_slots(self, start_mocks):
        slots = {"FOO": CollapsedEnv("1", CLI_PROVENANCE_SCOPE, "-e FOO")}
        with start_mocks() as m, patch(
            "kanibako.commands.start.persist_creation_level",
        ), patch(
            "kanibako.commands.start._launch_env_map", return_value=slots,
        ), patch(
            "kanibako.commands.start.persist_creation_env",
        ) as m_env, patch(
            "kanibako.settings.settings_categories.secret_path_names",
            return_value=_CARRIER_ROW,
        ):
            m.proj.is_new = True
            self._run(cli_env=["FOO=1"])
        m_env.assert_called_once()
        assert m_env.call_args.args == ({"FOO": "1"}, slots)
        # The secret_path owners are the carrier's, built by ``launch_deliveries``
        # off the launch's own gated entry list — a row only that call supplies.
        assert m_env.call_args.kwargs["secret_names"] == _CARRIER_ROW


class TestTheStoredSpellingIsTheSetSpelling:
    """A bool level value is stored as ``box set <key>=<value>`` stores it."""

    def test_continue_mode_matches_box_set(self, project_env):
        from kanibako.settings.config_interface import set_config_value
        from kanibako.settings.config_keys import ConfigLevel

        box, ws = _paths(project_env)
        _persist(project_env, build_cli_level(active_agent="claude", new_session=True))
        by_helper = box.read_bytes()
        box.unlink()
        msg = set_config_value(
            "pref.agent.claude.continue_mode", "false", config_path=box,
            cascade_system_path=project_env.std.settings, cascade_workset_path=ws,
            cascade_box_path=box, command_scope=ConfigLevel.box,
            std=project_env.std, proj=project_env.proj,
        )
        assert msg.startswith("Set ")
        assert box.read_bytes() == by_helper


def _mode_proj(mode, config_file, tmp_home):
    """A real, freshly materialized box in *mode* (``named-home-missing``: its home gone)."""
    import shutil

    from kanibako.project.workset import add_project, create_workset
    from kanibako.settings.config import load_config
    from kanibako.settings.paths import (
        WorksetSpec,
        load_std_paths,
        resolve_project,
        resolve_standalone_project,
        resolve_workset_project,
    )

    config = load_config(config_file)
    std = load_std_paths(config)
    if mode == "primary":
        proj = resolve_project(std, config, project_dir=str(tmp_home / "project"),
                               initialize=True)
    elif mode == "standalone":
        root = tmp_home / "sa"
        root.mkdir()
        proj = resolve_standalone_project(std, config, str(root), initialize=True)
    else:
        ws = create_workset("cxws", tmp_home / "worksets" / "cxws", std)
        src = tmp_home / "cx_src"
        src.mkdir()
        add_project(ws, "cxproj", src)
        proj = resolve_workset_project(WorksetSpec.from_workset(ws), "cxproj", std, config,
                                       initialize=True)
        if mode == "named-home-missing":
            shutil.rmtree(proj.shell_path)
    return std, proj


class TestEveryMode:
    """The helper writes the box tier of every mode, and nothing above it."""

    @pytest.mark.parametrize("mode", ["primary", "standalone", "named", "named-home-missing"])
    def test_the_level_lands_in_the_box_tier(self, mode, config_file, tmp_home,
                                             credentials_dir):
        std, proj = _mode_proj(mode, config_file, tmp_home)
        box, ws = box_workset_settings_paths(proj)
        ws_before = _bytes(ws) if ws is not None else None
        level = build_cli_level(
            selection={"system.agent": "claude"}, active_agent="claude", model="m-1",
            image="custom:v1",
        )
        persist_creation_level(
            level, typed_agent="claude", box_settings_path=box,
            workset_settings_path=ws, std=std, proj=proj,
        )
        doc = load_doc(box)
        assert doc["box"]["image"] == "custom:v1"
        assert doc["pref"]["system"]["agent"] == "claude"
        assert _pref_agent(doc, "claude") == {"model": "m-1"}
        if ws is not None:
            assert _bytes(ws) == ws_before
        if mode == "named-home-missing":
            # The settings write never needs, or makes, the box's home.
            assert not proj.shell_path.exists()


class TestCodexModelProjection:
    """codex delivers ``model`` by writing it to disk, so the -M bar matters here."""

    _NODE = "navigator℘codex"

    def _decisions(self, project_env, selection_level=None):
        from kanibako.commands.start import _resolve_box_launch_decisions
        from kanibako.plugins.codex.target import CodexTarget

        return _resolve_box_launch_decisions(
            std=project_env.std, proj=project_env.proj, target=CodexTarget(),
            agent_name=self._NODE, system_settings_path=None, agent_cfg_path=None,
            selection_level=selection_level, persona_values=None,
        )

    def test_a_saved_model_reaches_the_next_bare_starts_projection(self, project_env):
        """``-M`` on the creating launch is stored; a later bare start projects it."""
        _model_before = self._decisions(project_env)[2]
        assert _model_before != "m-1"
        _persist(project_env, build_cli_level(active_agent=self._NODE, model="m-1"))
        assert self._decisions(project_env)[2] == "m-1"

    def test_an_ephemeral_model_on_an_existing_box_never_reaches_the_projection(
        self, start_mocks,
    ):
        """Not creating: ``-M`` rides the launch snapshot only, never the projected model."""
        from kanibako.settings.agent_select import AgentSelection
        from kanibako.settings.settings_launch import AuthSource

        auth = AuthSource(tier="global", global_enabled=True, workset_enabled=False,
                          global_sync=False, workset_source=None)
        with start_mocks() as m, patch(
            "kanibako.commands.start._resolve_box_launch_decisions",
            return_value=(auth, "https://nav.example/v1", "stored-m", {}),
        ) as m_decide, patch(
            "kanibako.commands.start._preflight_persona_load",
            return_value=("https://nav.example/v1", None, None),
        ) as m_gate, patch(
            "kanibako.commands.start.persist_creation_level",
        ) as m_persist:
            m.proj.is_new = False
            m.resolve_agent.return_value = AgentSelection(node=self._NODE, source="settings")
            m.target.name = "codex"
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False, extra_args=[],
                model_override="m-eph",
            )
        m_gate.assert_called_once()
        assert m_gate.call_args.kwargs["keyspace_model"] == "stored-m"
        decided_on = m_decide.call_args.kwargs["selection_level"] or {}
        assert "m-eph" not in decided_on.values()
        m_persist.assert_not_called()


class TestShellCreatingLaunch:
    """``kanibako shell`` that creates the box saves its flags through the same helpers."""

    def test_shell_saves_image_and_env(self, start_mocks):
        from kanibako.commands.start import run_shell

        slots = {"FOO": CollapsedEnv("1", CLI_PROVENANCE_SCOPE, "-e FOO")}
        with start_mocks() as m, patch(
            "kanibako.commands.start.persist_creation_level",
        ) as m_helper, patch(
            "kanibako.commands.start._launch_env_map", return_value=slots,
        ), patch(
            "kanibako.commands.start.persist_creation_env",
        ) as m_env, patch(
            "kanibako.settings.settings_categories.secret_path_names",
            return_value=_CARRIER_ROW,
        ):
            m.proj.is_new = True
            run_shell(argparse.Namespace(
                project=None, box=None, shell_args=[], entrypoint=None,
                image="custom:v1", no_helpers=False, share_images=True,
                env=["FOO=1"], persistent=False, ephemeral=False,
            ))
        m_helper.assert_called_once()
        level = m_helper.call_args.args[0]
        assert level["box.image"] == "custom:v1"
        assert level["box.share_images"] is True
        assert not [k for k in level if k.startswith("agent.")]
        assert m_helper.call_args.kwargs["typed_agent"] is None
        m_env.assert_called_once()
        assert m_env.call_args.args == ({"FOO": "1"}, slots)
        # The secret_path owners are the carrier's, built by ``launch_deliveries``
        # off the launch's own gated entry list — a row only that call supplies.
        assert m_env.call_args.kwargs["secret_names"] == _CARRIER_ROW


class TestReauthSavesNoAgent:
    """``agent reauth`` (``setup_only``) can reach a creating launch, but its help says
    ``--agent`` is not saved, so that launch passes no typed agent to the helper."""

    def test_setup_only_creating_launch_saves_no_agent(self, start_mocks):
        with start_mocks() as m, patch(
            "kanibako.commands.start.persist_creation_level",
        ) as m_helper:
            m.proj.is_new = True
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False, extra_args=[],
                persistent=False, explicit_agent="claude", setup_only=True,
            )
        m_helper.assert_called_once()
        assert m_helper.call_args.kwargs["typed_agent"] is None

    def test_a_start_creating_launch_still_saves_it(self, start_mocks):
        with start_mocks() as m, patch(
            "kanibako.commands.start.persist_creation_level",
        ) as m_helper:
            m.proj.is_new = True
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False, extra_args=[],
                explicit_agent="claude",
            )
        assert m_helper.call_args.kwargs["typed_agent"] == "claude"
