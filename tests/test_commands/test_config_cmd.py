"""Tests for kanibako box config subcommand and config.py utility functions."""

from __future__ import annotations

import argparse

import pytest

from kanibako.settings.config import (
    load_config,
    load_project_overrides,
    write_project_config,
    write_project_config_key,
)


# ---------------------------------------------------------------------------
# box config command tests
# ---------------------------------------------------------------------------

class TestBoxConfigShow:
    def test_show_no_overrides(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box._parser import run_show

        config = load_config(config_file)
        from kanibako.settings.paths import load_std_paths, resolve_project
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        resolve_project(std, config, project_dir=project_dir, initialize=True)

        args = argparse.Namespace(args=[project_dir], effective=False)
        rc = run_show(args)
        assert rc == 0
        captured = capsys.readouterr()
        assert "no overrides" in captured.out

    def test_show_effective(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box._parser import run_show

        config = load_config(config_file)
        from kanibako.settings.paths import load_std_paths, resolve_project
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        resolve_project(std, config, project_dir=project_dir, initialize=True)

        args = argparse.Namespace(args=[project_dir], effective=True)
        rc = run_show(args)
        assert rc == 0
        captured = capsys.readouterr()
        assert "box_image" in captured.out

    def test_show_with_override(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box._parser import run_show

        config = load_config(config_file)
        from kanibako.settings.paths import load_std_paths, resolve_project
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj = resolve_project(std, config, project_dir=project_dir, initialize=True)

        # Write a project override
        project_toml = proj.metadata_path / "box.yaml"
        write_project_config(project_toml, "custom:v1")

        args = argparse.Namespace(args=[project_dir], effective=False)
        rc = run_show(args)
        assert rc == 0
        captured = capsys.readouterr()
        assert "custom:v1" in captured.out

    def test_show_effective_reflects_workset_tier(
        self, config_file, tmp_home, credentials_dir, capsys, monkeypatch,
    ):
        """A value set ONLY in the workset workset.yaml shows in --effective.

        This is the P3.7 parity fix: ``box config --effective`` must reflect
        the workset tier that ``start`` resolves (previously it skipped it).
        """
        from kanibako.commands.box._parser import run_show
        from kanibako.settings.paths import load_std_paths
        from kanibako.project.workset import add_project, create_workset

        config = load_config(config_file)
        std = load_std_paths(config)
        ws = create_workset("cfgtier", tmp_home / "ws_cfgtier", std)

        src = tmp_home / "proj_cfgtier"
        src.mkdir()
        add_project(ws, "myproj", src)

        # Set a box.* value ONLY at the workset level.  The workset settings now
        # live in the SAME workset.yaml that carries the meta.workset identity,
        # so merge the cascade key in rather than clobbering the file.
        from kanibako.settings.config_io import dump_doc, load_doc
        ws_settings = ws.root / "workset.yaml"
        data = load_doc(ws_settings) if ws_settings.is_file() else {}
        data["box"] = {"image": "ws-tier-img:1"}
        dump_doc(ws_settings, data)

        # Resolve via cwd inside the project's workspace dir.
        monkeypatch.chdir(ws.workspaces_dir / "myproj")
        args = argparse.Namespace(args=[], effective=True)
        rc = run_show(args)
        assert rc == 0
        captured = capsys.readouterr()
        assert "box_image" in captured.out
        assert "ws-tier-img:1" in captured.out

    def test_show_effective_reflects_system_settings_file(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        """DISPLAY == LAUNCH file (F2/F3 sibling): a behavior value stored
        ONLY in the system SETTINGS file (``@config.settings`` — the exact
        ``system_path`` the launch snapshot reads, ``std.settings``) shows in
        ``box show --effective``.  Pins the display ctx's system tier to the
        launch derivation — NEVER the kanibako_config.yaml CONFIG file (which
        the launch cascade does not read for settings)."""
        from kanibako.commands.box._parser import run_show
        from kanibako.settings.config_io import write_nested_key
        from kanibako.settings.paths import load_std_paths, resolve_project

        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        resolve_project(std, config, project_dir=project_dir, initialize=True)

        # System settings tier: select claude, and set a behavior key the
        # per-agent file does NOT set (endpoint) so the system-tier value is
        # the effective one at launch — the display must show the same.
        write_nested_key(std.settings, ("system",), "agent", "claude")
        write_nested_key(
            std.settings, ("agent", "default"), "endpoint", "https://ssp.example",
        )

        args = argparse.Namespace(args=[project_dir], effective=True)
        rc = run_show(args)
        assert rc == 0
        assert "https://ssp.example" in capsys.readouterr().out


class TestBoxConfigGet:
    def test_get_image(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box._parser import run_get

        config = load_config(config_file)
        from kanibako.settings.paths import load_std_paths, resolve_project
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        resolve_project(std, config, project_dir=project_dir, initialize=True)

        args = argparse.Namespace(args=[project_dir, "box.image"])
        rc = run_get(args)
        assert rc == 0
        captured = capsys.readouterr()
        # F6 get model: a fresh box stores nothing at box.image → plain get is
        # "(not set)" (stderr), NOT the fabricated built-in default. The default
        # image still applies at launch + under ``show --effective``.
        assert "ghcr.io/doctorjei/kanibako-oci:latest" not in captured.out
        assert "(not set)" in captured.err

    def test_get_known_key_without_project(self, config_file, tmp_home, credentials_dir, capsys):
        """``box get image`` (no project arg) should use cwd."""
        from kanibako.commands.box._parser import run_get

        config = load_config(config_file)
        from kanibako.settings.paths import load_std_paths, resolve_project
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        resolve_project(std, config, project_dir=project_dir, initialize=True)

        # known key as first arg => get operation (project defaults to cwd)
        # In tests the project_dir fixture is not cwd, so use 2-arg form.
        args2 = argparse.Namespace(args=[project_dir, "box.image"])
        rc = run_get(args2)
        assert rc == 0

    def test_get_missing_key_errors(self, config_file, tmp_home, credentials_dir, capsys):
        """``box get`` with no key reports an error (verb requires a key)."""
        from kanibako.commands.box._parser import run_get

        args = argparse.Namespace(args=[])
        rc = run_get(args)
        assert rc == 1
        assert "requires a key" in capsys.readouterr().err

    def test_get_bare_env_key_is_refused_with_the_cure(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        """R-39 read guard at the BOX handler (the get engine returns values,
        never error strings — the same handler-side split as the workset
        bare-agent-key read). ⚑ The legacy ``.env`` file is seeded here on
        purpose: after RQ-1 nothing reads it, so the refusal must fire even when
        the var "is there"."""
        from kanibako.commands.box._parser import run_get

        config = load_config(config_file)
        from kanibako.settings.paths import load_std_paths, resolve_project
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj = resolve_project(std, config, project_dir=project_dir, initialize=True)

        (proj.metadata_path / "env").write_text("MY_VAR=hello\n")

        args = argparse.Namespace(args=[project_dir, "env.MY_VAR"])
        rc = run_get(args)
        assert rc == 1
        captured = capsys.readouterr()
        assert "hello" not in captured.out
        assert "box.env.MY_VAR" in captured.err

    def test_get_scoped_env_key(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box._parser import run_get, run_set

        config = load_config(config_file)
        from kanibako.settings.paths import load_std_paths, resolve_project
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        resolve_project(std, config, project_dir=project_dir, initialize=True)

        rc = run_set(argparse.Namespace(
            args=[project_dir, "box.env.MY_VAR=hello"], force=False,
        ))
        assert rc == 0
        capsys.readouterr()

        rc = run_get(argparse.Namespace(args=[project_dir, "box.env.MY_VAR"]))
        assert rc == 0
        assert "hello" in capsys.readouterr().out


class TestBoxGetIsWiredToTheClosedKeyspace:
    """spec §0 at the ``box`` noun: an undeclared name is REFUSED, not "(not set)".

    ⚑ END-TO-END through the verb. ``tests/test_settings/test_config_interface.py``
    pins the predicate; these pin that the handler CALLS it — the gate and the wiring
    fail independently.
    """

    def _box(self, config_file, tmp_home):
        from kanibako.settings.paths import load_std_paths, resolve_project

        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj = resolve_project(std, config, project_dir=project_dir, initialize=True)
        return project_dir, proj

    def _merge(self, proj, table):
        """MERGE into the box's settings file — never overwrite it: ``create`` wrote
        content of its own, and clobbering it tests a file no user has."""
        from kanibako.settings.config_io import dump_doc, load_doc

        path = proj.metadata_path / "box.yaml"
        doc = load_doc(path)
        doc.setdefault("box", {}).update(table)
        dump_doc(path, doc)

    def test_an_undeclared_key_refuses_at_rc_1_NAMING_it(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        from kanibako.commands.box._parser import run_get

        # MUTATION-PROVED: neuter the handler's ``scope_read_key_error`` call and this
        # reds with ``assert 0 == 1``, as does the workset noun's twin.
        project_dir, _ = self._box(config_file, tmp_home)
        rc = run_get(argparse.Namespace(args=[project_dir, "box.zippity"]))
        assert rc == 1
        captured = capsys.readouterr()
        assert "(not set)" not in captured.err
        assert "box.zippity" in captured.err

    def test_a_DECLARED_but_unset_key_still_answers_not_set_at_rc_0(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        """The §2a read-verb rule is untouched for a REAL key — that distinction is
        the whole value of the refusal above."""
        from kanibako.commands.box._parser import run_get

        project_dir, _ = self._box(config_file, tmp_home)
        rc = run_get(argparse.Namespace(args=[project_dir, "box.shell"]))
        assert rc == 0
        assert "(not set)" in capsys.readouterr().err

    def test_a_HAND_AUTHORED_bind_entry_still_reads_back(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        """§0: *"Refuse the write; keep the read honest."* The CLI write route retired,
        so the read-back is the ONLY way to check the hand edit it prescribes."""
        from kanibako.commands.box._parser import run_get

        project_dir, proj = self._box(config_file, tmp_home)
        self._merge(proj, {
            "bindings": {"ro": {"/in/box": ["/on/host", "ro"]}},
            "caches": {"/in/box/c": ["/on/host/c"]},
        })
        for key in ("box.bindings.ro./in/box", "box.caches./in/box/c"):
            assert run_get(argparse.Namespace(args=[project_dir, key])) == 0, key
            assert "/on/host" in capsys.readouterr().out, key

    def test_a_pref_request_still_reads(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        """§2h requires ``pref.system.agent`` to answer; it must not be collateral."""
        from kanibako.commands.box._parser import run_get, run_set

        project_dir, _ = self._box(config_file, tmp_home)
        assert run_set(argparse.Namespace(
            args=[project_dir, "pref.system.agent=claude"], force=False,
        )) == 0
        capsys.readouterr()
        assert run_get(
            argparse.Namespace(args=[project_dir, "pref.system.agent"]),
        ) == 0
        assert "claude" in capsys.readouterr().out

    def test_the_table_valued_agent_leaf_is_refused_WITH_AN_ADDRESS(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        """END-TO-END through the verb: the one declared leaf this noun cannot serve.

        ``transform_settings`` is the single TABLE-valued agent leaf (spec §2d).  Its
        bare spelling is a key-shaped arg (``KNOWN_CONFIG_KEYS`` admits it, so the
        parser does not read it as a project name), the read gate refuses it, and until
        this the refusal called it "not a declared namespace" and pointed nowhere.  It
        now names the noun that answers.

        ⚑ NOTHING REGRESSES: no value was ever returned here, and the rc is unchanged.
        MUTATION-PROVED at the predicate in
        ``tests/test_settings/test_agent_leaf_shape.py``; this pins that the handler
        prints what the predicate builds.
        """
        from kanibako.commands.box._parser import run_get

        project_dir, _ = self._box(config_file, tmp_home)
        assert run_get(argparse.Namespace(
            args=[project_dir, "transform_settings"],
        )) == 1
        captured = capsys.readouterr()
        assert "(not set)" not in captured.err
        assert "transform_settings" in captured.err
        assert "kanibako agent get" in captured.err

    def test_box_show_marks_a_hand_written_undeclared_entry(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        """The refusal above tells the user to edit the file; this is the surface that
        tells them WHICH LINE."""
        from kanibako.commands.box._parser import run_show

        project_dir, proj = self._box(config_file, tmp_home)
        self._merge(proj, {"image": "myimage", "zippity": "wibble"})
        assert run_show(argparse.Namespace(args=[project_dir], effective=False)) == 0
        out = capsys.readouterr().out
        assert "undeclared" in out
        assert "box.zippity = wibble" in out

    def test_box_show_prints_no_such_block_for_a_clean_file(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        from kanibako.commands.box._parser import run_show

        project_dir, proj = self._box(config_file, tmp_home)
        self._merge(proj, {"image": "myimage"})
        assert run_show(argparse.Namespace(args=[project_dir], effective=False)) == 0
        assert "undeclared" not in capsys.readouterr().out


class TestBoxGetThreadsTheAgentsRoot:
    """``box get agent.<node>.<key>`` reads the node's own file.

    The per-node families live in ``agents/<node>/agent.yaml``, reachable only
    through ``get_config_value``'s ``agents_root``. The handler withheld it, so
    every read of a node key resolved its target to ``None`` and printed
    "(not set)" at rc 0 for a value that IS stored — while ``system get``, the
    one handler that threads it, answered the SAME key correctly.

    ⚑ MUTATION-PROVED: drop ``agents_root=std.agents`` from the ``get`` branch of
    ``commands/box/_parser.py`` and the two read-back tests red on "(not set)".
    ⚑ There is no per-node BIND case here on purpose: the closed-keyspace read gate
    refuses ``agent.<node>.bindings.ro.<dest>`` by name at every noun (the family is
    TERMINAL and dest-keyed), so that engine arm is unreachable from any handler.
    """

    def _box_and_agents_root(self, config_file, tmp_home):
        from kanibako.settings.paths import load_std_paths, resolve_project

        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        resolve_project(std, config, project_dir=project_dir, initialize=True)
        return project_dir, std.agents

    def _write_node(self, agents_root, key, value):
        """Write through the PRODUCTION set route, so this pins the read, not a
        hand-built file shape the writer would never produce."""
        from kanibako.settings.config_interface import set_config_value
        from kanibako.settings.config_keys import ConfigLevel

        msg = set_config_value(
            key, value,
            config_path=agents_root.parent / "unused-box.yaml",
            command_scope=ConfigLevel.system,
            agents_root=agents_root,
        )
        assert not msg.startswith("Error:"), msg

    def test_a_persona_agent_key_reads_back(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        from kanibako.commands.box._parser import run_get

        project_dir, agents_root = self._box_and_agents_root(config_file, tmp_home)
        self._write_node(agents_root, "agent.claude.model", "opus-test")

        rc = run_get(argparse.Namespace(args=[project_dir, "agent.claude.model"]))
        assert rc == 0
        captured = capsys.readouterr()
        assert captured.out.strip() == "opus-test"
        assert "(not set)" not in captured.err

    def test_a_node_secret_path_reads_back(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        from kanibako.commands.box._parser import run_get

        project_dir, agents_root = self._box_and_agents_root(config_file, tmp_home)
        self._write_node(
            agents_root,
            "agent.claude.secret_path.ANTHROPIC_AUTH_TOKEN", "/host/token",
        )

        rc = run_get(argparse.Namespace(args=[
            project_dir, "agent.claude.secret_path.ANTHROPIC_AUTH_TOKEN",
        ]))
        assert rc == 0
        assert capsys.readouterr().out.strip() == "/host/token"

    def test_an_unset_node_key_is_still_honestly_not_set(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        """The threading must not fabricate: with nothing stored, "(not set)"
        stays "(not set)"."""
        from kanibako.commands.box._parser import run_get

        project_dir, _ = self._box_and_agents_root(config_file, tmp_home)
        rc = run_get(argparse.Namespace(args=[project_dir, "agent.claude.model"]))
        assert rc == 0
        assert "(not set)" in capsys.readouterr().err


class TestBoxGetDoesNotAdvertiseTheAgentFlag:
    """``box get --help`` offered ``--agent`` and the handler refused it at rc 2.

    ``inject_blanket_flags`` adds the flag to every leaf so relevance can be judged
    post-parse; ``box get`` is a READ that never runs an agent, so it is absent from
    ``AGENT_FLAG_COMMANDS`` and the advertisement promised a refusal. The CALL: stop
    advertising, keep the refusal — the flag still parses, so ``check_flag_relevance``
    still names where it DOES apply instead of argparse's bare "unrecognized
    arguments".
    """

    def _help(self, argv_leaf):
        from kanibako import cli

        parser = cli.build_parser()
        sub = parser
        for name in argv_leaf:
            action = next(
                a for a in sub._actions
                if isinstance(a, argparse._SubParsersAction)
            )
            sub = action.choices[name]
        return sub.format_help()

    def test_help_does_not_mention_agent(self):
        text = self._help(["box", "get"])
        assert "--agent" not in text

    def test_the_flag_still_parses_and_is_refused_by_name(self):
        from kanibako import cli
        from kanibako.commands.flags import FlagRelevanceError, check_flag_relevance

        args = cli.build_parser().parse_args(
            ["box", "get", "--agent", "claude", "model"],
        )
        assert args.agent == "claude"
        with pytest.raises(FlagRelevanceError, match="box get"):
            check_flag_relevance(args)

    def test_commands_that_take_the_flag_still_advertise_it(self):
        """No collateral on the leaves that legitimately run an agent.

        ⚑ The suppression is now CLASS-WIDE, not ``box get``'s alone: ``_walk``
        advertises ``--agent``/``--box`` only where the key is in the declared set.
        This case is the other direction of that property, kept here because it
        guards the arm a suppression bug would break silently.
        ``test_flags.py`` pins the property over the whole tree.
        """
        for leaf in (["box", "start"], ["box", "create"], ["agent", "reauth"]):
            assert "--agent" in self._help(leaf), leaf


class TestBoxConfigSet:
    def test_set_image(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box._parser import run_set

        config = load_config(config_file)
        from kanibako.settings.paths import load_std_paths, resolve_project
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        resolve_project(std, config, project_dir=project_dir, initialize=True)

        args = argparse.Namespace(
            args=[project_dir, "box.image=new-image:v1"], force=False,
        )
        rc = run_set(args)
        assert rc == 0
        captured = capsys.readouterr()
        assert "Set" in captured.out
        assert "new-image:v1" in captured.out

    def test_set_env_var(self, config_file, tmp_home, credentials_dir, capsys):
        """The bare spelling is REFUSED (R-39) and the cure it names WORKS —
        both halves, because a cure that errors is worse than no cure."""
        from kanibako.commands.box._parser import run_set

        config = load_config(config_file)
        from kanibako.settings.paths import load_std_paths, resolve_project
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj = resolve_project(std, config, project_dir=project_dir, initialize=True)

        rc = run_set(argparse.Namespace(
            args=[project_dir, "env.EDITOR=vim"], force=False,
        ))
        assert rc == 1
        assert "box.env.EDITOR" in capsys.readouterr().err
        assert not (proj.metadata_path / "env").exists()

        rc = run_set(argparse.Namespace(
            args=[project_dir, "box.env.EDITOR=vim"], force=False,
        ))
        assert rc == 0
        assert "Set box.env.EDITOR=vim" in capsys.readouterr().out

    def test_set_model(self, config_file, tmp_home, credentials_dir, capsys):
        """Agent behavior keys are set at box scope via the §2h REQUEST
        ``pref.agent.<agent>.<key>``; the BARE form is refused with a teach
        message (a bare agent key targets ``agent.default``, which a box cannot
        write). ⮕ P7: the cure USED to be the ``box.agent.<key>`` mirror, retired
        by spec §2b."""
        from kanibako.commands.box._parser import run_set

        config = load_config(config_file)
        from kanibako.settings.paths import load_std_paths, resolve_project
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        resolve_project(std, config, project_dir=project_dir, initialize=True)

        # Bare agent key at box scope → refused, teaching the request form.
        args = argparse.Namespace(
            args=[project_dir, "model=sonnet"], force=False,
        )
        rc = run_set(args)
        assert rc == 1
        captured = capsys.readouterr()
        assert "set pref.agent.<agent>.model" in captured.err

        # The request form is the settable one.
        args = argparse.Namespace(
            args=[project_dir, "pref.agent.claude.model=sonnet"], force=False,
        )
        rc = run_set(args)
        assert rc == 0
        captured = capsys.readouterr()
        assert "pref.agent.claude.model" in captured.out

    def test_set_core_bind_repoint_is_refused_end_to_end(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        """R-9 — through the REAL ``box config set`` handler (registry threading
        and all), the scope-level core-bind repoint is REFUSED.

        This test used to assert the opposite (F10 Phase 1: the box handler
        threads the CORE floor registry, so the repoint validated and wrote the
        RAW tuple). That surface is an ACCEPTED LOSS, boarded as DS-BL1 — Jei:
        *"unfortunate, but this is going to have to be a cost we'll pay."* The
        end-to-end value of the test is unchanged: it proves the refusal is what
        the USER meets at the CLI, not just what the engine returns.
        """
        from kanibako.commands.box._parser import run_set
        from kanibako.settings.config_io import load_doc

        config = load_config(config_file)
        from kanibako.settings.paths import load_std_paths, resolve_project
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj = resolve_project(std, config, project_dir=project_dir, initialize=True)

        args = argparse.Namespace(
            args=[project_dir, "box.bindings.rw.home=/newhome"],
            force=False,
        )
        rc = run_set(args)
        assert rc == 1
        captured = capsys.readouterr()
        # NAMES the key the user typed, and says what happened to it (spec §0).
        assert "box.bindings.rw.home" in captured.err, captured.err
        assert "RETIRED" in captured.err, captured.err
        # Nothing was written into the box settings file.
        project_toml = proj.metadata_path / "box.yaml"
        assert "bindings" not in load_doc(project_toml).get("box", {})


class TestBoxConfigReset:
    def test_reset_key(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box._parser import run_reset

        config = load_config(config_file)
        from kanibako.settings.paths import load_std_paths, resolve_project
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj = resolve_project(std, config, project_dir=project_dir, initialize=True)

        # Set first
        project_toml = proj.metadata_path / "box.yaml"
        write_project_config(project_toml, "to-reset:v1")

        # Reset. ⚑ The key is spelled with DOTS — the flat ``box_image`` form this
        # case used to pass is not a declared key and every verb refuses it now.
        args = argparse.Namespace(
            args=[project_dir, "box.image"], reset_all=False, force=False,
        )
        rc = run_reset(args)
        assert rc == 0
        captured = capsys.readouterr()
        # F7 honest message: the box override is CLEARED (no fabricated
        # "reverts to default: <built-in>"); the noun is named from the scope.
        assert "cleared" in captured.out.lower()
        assert "box" in captured.out.lower()
        assert "reverts to default" not in captured.out

    def test_reset_all(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box._parser import run_reset

        config = load_config(config_file)
        from kanibako.settings.paths import load_std_paths, resolve_project
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj = resolve_project(std, config, project_dir=project_dir, initialize=True)

        # Set a value first
        project_toml = proj.metadata_path / "box.yaml"
        write_project_config(project_toml, "override:v1")

        # Reset all with --force (skip confirmation)
        args = argparse.Namespace(
            args=[project_dir], reset_all=True, force=True,
        )
        rc = run_reset(args)
        assert rc == 0
        captured = capsys.readouterr()
        assert "Reset" in captured.out

    def test_reset_nonexistent(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box._parser import run_reset

        config = load_config(config_file)
        from kanibako.settings.paths import load_std_paths, resolve_project
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        resolve_project(std, config, project_dir=project_dir, initialize=True)

        # ⚑ DOTTED: this case is about resetting a key with nothing stored, not
        # about spelling — and the flat form now refuses before it gets that far.
        args = argparse.Namespace(
            args=[project_dir, "box.image"], reset_all=False, force=False,
        )
        rc = run_reset(args)
        assert rc == 0
        captured = capsys.readouterr()
        assert "No override" in captured.out

    def test_reset_requires_key(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box._parser import run_reset

        args = argparse.Namespace(args=[], reset_all=False, force=False)
        rc = run_reset(args)
        assert rc == 1
        assert "requires a key" in capsys.readouterr().err


class TestBoxConfigRefusesThePhantomBox:
    """MBR-6: a subject-less box-config verb run from a cwd that is NOT a box
    must REFUSE, not materialise ``boxes/__unregistered__/box.yaml``.

    ``_resolve_local_dir`` returns that path as a NAME-ASSIGNMENT SENTINEL for
    resolvers that go on to pick a real name.  The config verbs pick none, so the
    sentinel was being written to as if it were a box, at rc 0.
    """

    def _std(self, config_file):
        from kanibako.settings.paths import load_std_paths

        return load_std_paths(load_config(config_file))

    def test_set_from_a_non_box_cwd_errors_and_writes_nothing(
        self, config_file, tmp_home, credentials_dir, capsys, monkeypatch,
    ):
        from kanibako.commands.box._parser import run_set

        std = self._std(config_file)
        elsewhere = tmp_home / "not_a_box"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)

        rc = run_set(argparse.Namespace(args=["box.image=phantom:1"], force=False))
        assert rc == 1
        err = capsys.readouterr().err
        assert "no box at" in err
        assert str(elsewhere) in err
        assert not (std.boxes / "__unregistered__").exists(), (
            "the __unregistered__ sentinel must never be materialized as a real box"
        )

    def test_get_show_and_reset_refuse_the_same_way(
        self, config_file, tmp_home, credentials_dir, capsys, monkeypatch,
    ):
        """One seam, so every verb that addresses a box refuses together —
        ``reset --all`` included (it writes too)."""
        from kanibako.commands.box._parser import (
            run_get,
            run_reset,
            run_show,
        )

        std = self._std(config_file)
        elsewhere = tmp_home / "not_a_box"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)

        assert run_get(argparse.Namespace(args=["box.image"])) == 1
        assert "no box at" in capsys.readouterr().err
        assert run_show(argparse.Namespace(args=[], effective=False)) == 1
        assert "no box at" in capsys.readouterr().err
        assert run_reset(argparse.Namespace(
            args=["box.image"], reset_all=False, force=True,
        )) == 1
        assert "no box at" in capsys.readouterr().err
        assert run_reset(argparse.Namespace(
            args=[], reset_all=True, force=True,
        )) == 1
        assert "no box at" in capsys.readouterr().err
        assert not (std.boxes / "__unregistered__").exists()

    def test_a_real_box_is_untouched_by_the_refusal(
        self, config_file, tmp_home, credentials_dir, capsys, monkeypatch,
    ):
        """The cure the message names — address the box — works."""
        from kanibako.commands.box._parser import run_set
        from kanibako.settings.paths import load_std_paths, resolve_project

        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        resolve_project(std, config, project_dir=project_dir, initialize=True)

        # cwd is NOT the box — the named subject is what resolves it.
        monkeypatch.chdir(tmp_home)
        rc = run_set(argparse.Namespace(
            args=[project_dir, "box.image=real:1"], force=False,
        ))
        assert rc == 0
        assert "real:1" in capsys.readouterr().out
        assert not (std.boxes / "__unregistered__").exists()


class TestBoxConfigArgParsing:
    """Test the discrete-verb parsers and their flags."""

    def test_parser_show_no_args(self):
        from kanibako.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["box", "show"])
        assert args.command == "box"
        assert args.box_command == "show"
        assert args.args == []
        assert args.func.__name__ == "run_show"

    def test_parser_get_key(self):
        from kanibako.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["box", "get", "image"])
        assert args.args == ["image"]
        assert args.func.__name__ == "run_get"

    def test_parser_set_key_equals_value(self):
        from kanibako.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["box", "set", "image=myimg:v1"])
        assert args.args == ["image=myimg:v1"]
        assert args.func.__name__ == "run_set"

    def test_parser_get_project_and_key(self):
        from kanibako.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["box", "get", "myproject", "image"])
        assert args.args == ["myproject", "image"]

    def test_parser_show_effective(self):
        from kanibako.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["box", "show", "--effective"])
        assert args.effective is True

    def test_parser_reset_key(self):
        from kanibako.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["box", "reset", "model"])
        assert args.args == ["model"]
        assert args.func.__name__ == "run_reset"

    def test_parser_reset_all(self):
        from kanibako.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["box", "reset", "--all"])
        assert args.reset_all is True

    def test_parser_set_force(self):
        from kanibako.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["box", "set", "model=x", "--force"])
        assert args.force is True

    def test_config_subcommand_is_gone(self):
        """The overloaded ``box config`` subcommand was retired (clean break)."""
        import pytest
        from kanibako.cli import build_parser
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["box", "config"])


class TestBoxConfigTooManyArgs:
    def test_three_args_returns_error(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.commands.box._parser import run_get

        args = argparse.Namespace(args=["a", "b", "c"])
        rc = run_get(args)
        assert rc == 1
        assert "too many arguments" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# config.py utility function tests (carried forward from old test file)
# ---------------------------------------------------------------------------

class TestWriteProjectConfigKey:
    def test_write_paths_key(self, tmp_path):
        p = tmp_path / "box.yaml"
        write_project_config_key(p, "paths_project_toml", "custom.yaml")
        loaded = load_config(p)
        assert loaded.paths_project_toml == "custom.yaml"
        text = p.read_text()
        assert "paths:" in text
        assert 'project_toml: custom.yaml' in text

    def test_write_box_key(self, tmp_path):
        p = tmp_path / "box.yaml"
        write_project_config_key(p, "box_image", "myimg:v1")
        loaded = load_config(p)
        assert loaded.box_image == "myimg:v1"
        text = p.read_text()
        assert "box:" in text
        assert 'image: myimg:v1' in text

    def test_write_shell_key(self, tmp_path):
        # (⮕ P7: was ``box_agent_name``, retired with spec §2b — the SHAPE under
        # test is the nested box-table write, not that key.)
        p = tmp_path / "box.yaml"
        write_project_config_key(p, "box_shell", "bash")
        loaded = load_config(p)
        assert loaded.box_shell == "bash"
        text = p.read_text()
        assert "box:" in text
        assert 'shell: bash' in text

    def test_write_multiple_sections(self, tmp_path):
        """Writing keys from different sections should create both."""
        p = tmp_path / "box.yaml"
        write_project_config_key(p, "box_image", "multi:v1")
        write_project_config_key(p, "paths_project_toml", "multi.yaml")
        loaded = load_config(p)
        assert loaded.box_image == "multi:v1"
        assert loaded.paths_project_toml == "multi.yaml"

    def test_update_existing_key(self, tmp_path):
        p = tmp_path / "box.yaml"
        write_project_config_key(p, "box_image", "old:v1")
        write_project_config_key(p, "box_image", "new:v2")
        loaded = load_config(p)
        assert loaded.box_image == "new:v2"
        text = p.read_text()
        assert "old:v1" not in text

    def test_backward_compat_with_write_project_config(self, tmp_path):
        """write_project_config (old API) should still work."""
        p = tmp_path / "box.yaml"
        write_project_config(p, "compat:v1")
        loaded = load_config(p)
        assert loaded.box_image == "compat:v1"


class TestUnsetProjectConfigKey:
    def test_unset_removes_key(self, tmp_path):
        from kanibako.settings.config import unset_project_config_key
        p = tmp_path / "box.yaml"
        write_project_config_key(p, "box_image", "remove-me:v1")
        assert unset_project_config_key(p, "box_image") is True
        loaded = load_config(p)
        # Should revert to default
        assert loaded.box_image == "ghcr.io/doctorjei/kanibako-oci:latest"

    def test_unset_nonexistent_key(self, tmp_path):
        from kanibako.settings.config import unset_project_config_key
        p = tmp_path / "box.yaml"
        write_project_config_key(p, "box_image", "keep:v1")
        assert unset_project_config_key(p, "paths_project_toml") is False
        # Original key should still be there
        loaded = load_config(p)
        assert loaded.box_image == "keep:v1"

    def test_unset_no_file(self, tmp_path):
        from kanibako.settings.config import unset_project_config_key
        p = tmp_path / "nonexistent.yaml"
        assert unset_project_config_key(p, "box_image") is False

    def test_unset_preserves_other_keys(self, tmp_path):
        from kanibako.settings.config import unset_project_config_key
        p = tmp_path / "box.yaml"
        write_project_config_key(p, "box_image", "img:v1")
        write_project_config_key(p, "paths_project_toml", "my.yaml")
        assert unset_project_config_key(p, "box_image") is True
        loaded = load_config(p)
        assert loaded.paths_project_toml == "my.yaml"
        assert loaded.box_image == "ghcr.io/doctorjei/kanibako-oci:latest"


class TestLoadProjectOverrides:
    def test_empty_when_no_file(self, tmp_path):
        p = tmp_path / "nonexistent.yaml"
        assert load_project_overrides(p) == {}

    def test_returns_only_overrides(self, tmp_path):
        p = tmp_path / "box.yaml"
        write_project_config_key(p, "box_image", "override:v1")
        overrides = load_project_overrides(p)
        assert "box_image" in overrides
        assert overrides["box_image"] == "override:v1"
        # Other keys should not appear (they are defaults)
        assert "paths_project_toml" not in overrides


class TestSplitConfigKey:
    def test_box_image_key(self):
        from kanibako.settings.config import _split_config_key
        assert _split_config_key("box_image") == ("box", "image")

    def test_paths_key(self):
        from kanibako.settings.config import _split_config_key
        assert _split_config_key("paths_project_toml") == ("paths", "project_toml")

    def test_paths_key_with_underscores(self):
        from kanibako.settings.config import _split_config_key
        assert _split_config_key("paths_project_toml") == ("paths", "project_toml")

    def test_box_shell_key(self):
        from kanibako.settings.config import _split_config_key
        assert _split_config_key("box_shell") == ("box", "shell")

    def test_unprefixed_key_is_top_level_field(self):
        """A key with no section prefix is a TOP-LEVEL scalar field.

        The H1 fix removed the old ValueError raise path: ``_split_config_key``
        now returns an empty section (the typed writer in config_interface is
        the routed set/get/reset path; this helper must never crash on an
        advertised key).
        """
        from kanibako.settings.config import _split_config_key
        assert _split_config_key("allow_helpers") == ("", "allow_helpers")
        assert _split_config_key("unknown_prefix_key") == ("", "unknown_prefix_key")


# ---------------------------------------------------------------------------
# P2 / M-8 — the STANDALONE box tier: ONE file for READ, WRITE and ANCHOR
# ---------------------------------------------------------------------------

class TestStandaloneBoxTierRoundTrip:
    """A standalone box has a BOX TIER at ``box_data/box.yaml`` (spec §2c ALL
    PROJECTS), absent by default, over the ROOT ``workset.yaml`` that plays the
    WORKSET tier.  ``config set`` must WRITE the box tier that ``get`` READS — the
    whole point of M-8: a read/write split is the silent "I set it and nothing
    changed" failure, with no error anywhere."""

    def _standalone(self, config_file, tmp_home):
        from kanibako.settings.paths import load_std_paths, resolve_standalone_project

        config = load_config(config_file)
        std = load_std_paths(config)
        root = tmp_home / "sa"
        root.mkdir()
        resolve_standalone_project(std, config, str(root), initialize=True)
        return root

    @staticmethod
    def _files(root):
        """``(root_file, box_file)`` at their LITERAL spec positions (§4 STANDALONE tree).

        ⚑ Deliberately NOT sourced from ``box_workset_settings_paths``: a test that
        gets both positions from the code under test is self-consistent and therefore
        BLIND to a swapped pair.  These literals are what make the swap mutation
        redden here."""
        from kanibako.settings.paths import STANDALONE_META_DIR

        return root / "workset.yaml", root / STANDALONE_META_DIR / "box.yaml"

    def test_set_writes_the_box_tier_and_leaves_the_root_file_alone(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        from kanibako.commands.box._parser import run_set
        from kanibako.settings.config_io import load_doc

        root = self._standalone(config_file, tmp_home)
        root_file, box_file = self._files(root)
        assert not box_file.exists()   # ABSENT BY DEFAULT (spec §2c + §4 STANDALONE tree)
        root_before = root_file.read_text()

        rc = run_set(argparse.Namespace(
            args=[str(root), "box.image=probe/img:1"], box=None, force=False,
        ))
        assert rc == 0
        capsys.readouterr()

        # The value landed in the BOX tier...
        assert load_doc(box_file)["box"]["image"] == "probe/img:1"
        # ...and the ROOT file (the WORKSET tier + the detection marker) is untouched.
        assert root_file.read_text() == root_before

    def test_get_reads_where_set_wrote(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        """⚑ THE M-8 GATE. A set followed by a get must round-trip through ONE file.
        (Mutation: leave `config set` on the old ``metadata_path/workset.yaml``
        derivation while the read moves → get prints "(not set)" → RED.)"""
        from kanibako.commands.box._parser import run_get, run_set

        root = self._standalone(config_file, tmp_home)
        run_set(argparse.Namespace(
            args=[str(root), "box.image=probe/img:1"], box=None, force=False,
        ))
        capsys.readouterr()
        rc = run_get(argparse.Namespace(
            args=[str(root), "box.image"], box=None, force=False,
        ))
        assert rc == 0
        assert capsys.readouterr().out.strip() == "probe/img:1"

    def test_show_effective_agrees_with_the_stored_value(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        from kanibako.commands.box._parser import run_set, run_show

        root = self._standalone(config_file, tmp_home)
        run_set(argparse.Namespace(
            args=[str(root), "box.image=probe/img:1"], box=None, force=False,
        ))
        capsys.readouterr()
        rc = run_show(argparse.Namespace(
            args=[str(root)], box=None, effective=True, force=False,
        ))
        assert rc == 0
        assert "probe/img:1" in capsys.readouterr().out

    def test_absent_box_file_shows_no_overrides(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        """The ABSENCE case: with no box-tier file, a standalone box reports exactly
        what it reported before P2 — an absent file is an EMPTY tier
        (``config_io.load_doc`` → ``{}``), not a broken one."""
        from kanibako.commands.box._parser import run_show

        root = self._standalone(config_file, tmp_home)
        assert not self._files(root)[1].exists()
        rc = run_show(argparse.Namespace(
            args=[str(root)], box=None, effective=False, force=False,
        ))
        assert rc == 0
        assert "no overrides" in capsys.readouterr().out

    def test_root_stored_value_is_not_a_box_override_but_is_effective(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        """⚑ The one USER-VISIBLE read-surface change (M-8).  A LEGACY standalone box
        stored ``box.*`` in its ROOT file — which is the WORKSET tier now.  A plain
        ``get`` is stored-at-noun (spec §2a "Read verbs"), so it honestly reports the key
        as not stored AT THE BOX; ``show --effective`` still resolves it via the R2
        downward-default.  Nothing is lost; the read got truthful."""
        from kanibako.commands.box._parser import run_get, run_show
        from kanibako.settings.config_io import dump_doc, load_doc

        root = self._standalone(config_file, tmp_home)
        root_file, box_file = self._files(root)
        doc = load_doc(root_file)
        doc.setdefault("box", {})["image"] = "legacy/img:9"
        dump_doc(root_file, doc)
        assert not box_file.exists()

        run_get(argparse.Namespace(
            args=[str(root), "box.image"], box=None, force=False,
        ))
        captured = capsys.readouterr()
        assert captured.out.strip() == ""
        assert "(not set)" in captured.err

        run_show(argparse.Namespace(
            args=[str(root)], box=None, effective=True, force=False,
        ))
        assert "legacy/img:9" in capsys.readouterr().out

    def test_primary_set_get_is_unchanged(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        """Regression pin: PRIMARY still writes and reads its own
        ``<metadata_path>/box.yaml`` — no ``box_data/`` anywhere."""
        from kanibako.commands.box._parser import run_get, run_set
        from kanibako.settings.config_io import load_doc
        from kanibako.settings.paths import BOX_META_FILE, load_std_paths, resolve_project

        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj = resolve_project(std, config, project_dir=project_dir, initialize=True)

        run_set(argparse.Namespace(
            args=[project_dir, "box.image=probe/prim:1"], box=None, force=False,
        ))
        capsys.readouterr()
        run_get(argparse.Namespace(
            args=[project_dir, "box.image"], box=None, force=False,
        ))
        assert capsys.readouterr().out.strip() == "probe/prim:1"
        assert (
            load_doc(proj.metadata_path / BOX_META_FILE)["box"]["image"]
            == "probe/prim:1"
        )
        assert not (proj.metadata_path / "box_data").exists()

    def test_private_create_lands_auth_keys_in_the_box_tier(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        """⚑ AUTH-CRITICAL.  ``create --private`` persists ``box.auth.*=false``, and
        ``seed_new_box``'s ``resolve_auth_source`` reads them off the snapshot's BOX
        tier.  If the write went to a file the snapshot does not read as the box tier,
        a supposedly-private box would resolve a sharing tier and forward the host
        OAuth token into the seed.  Pin that they are the SAME file."""
        from kanibako.commands.box._parser import run_create
        from kanibako.settings.config_io import load_doc
        from kanibako.settings.paths import (
            box_workset_settings_paths,
            load_std_paths,
            resolve_standalone_project,
        )

        root = tmp_home / "priv"
        root.mkdir()
        rc = run_create(argparse.Namespace(
            path=str(root), standalone=True, no_vault=True, private=True,
            name=None, image=None, agent=None, allow_home=False,
        ))
        capsys.readouterr()
        assert rc == 0

        config = load_config(config_file)
        std = load_std_paths(config)
        proj = resolve_standalone_project(std, config, str(root), initialize=False)
        box_tier, _ = box_workset_settings_paths(proj)
        # ⚑ LITERAL position too: asserting only against the pair function would make
        # this AUTH-CRITICAL test self-consistent and blind to a swapped pair, which
        # would point it at the ROOT file while the launch snapshot reads box_data/.
        assert box_tier == root / "box_data" / "box.yaml"
        auth = load_doc(box_tier)["box"]["auth"]
        assert auth["global_enabled"] is False
        assert auth["workset_enabled"] is False

    def test_duplicate_carries_the_box_tier_not_the_root_file(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        """``box duplicate`` must carry the source's BOX TIER (M-8).  It reads the box
        settings WITHOUT the pair idiom, so it was missed by the first sweep and found
        by a final grep — a ``box set box.image=X`` followed by a duplicate would
        otherwise silently lose X.

        ⚑ It must ALSO not carry the source's ``workset.kuid``: that lives in the ROOT
        file (the workset tier), and copying it into the duplicate's box tier would
        OVERRIDE the fresh kuid ``establish_standalone`` generates, giving the new box
        its source's identity."""
        from kanibako.commands.box._duplicate import run_duplicate
        from kanibako.commands.box._parser import run_set
        from kanibako.settings.config import read_workset_kuid
        from kanibako.settings.config_io import load_doc

        src = self._standalone(config_file, tmp_home)
        run_set(argparse.Namespace(
            args=[str(src), "box.image=probe/img:1"], box=None, force=False,
        ))
        capsys.readouterr()
        src_root, _ = self._files(src)
        src_kuid = read_workset_kuid(src_root)

        dst = tmp_home / "dup"
        rc = run_duplicate(argparse.Namespace(
            source_path=str(src), new_path=str(dst), to_mode="standalone",
            bare=False, force=True, box=None,   # force: skip the interactive confirm
        ))
        out = capsys.readouterr()
        assert rc == 0, f"out={out.out!r} err={out.err!r}"

        dst_root, dst_box = self._files(dst)
        # The box-scope value came across, in the BOX tier.
        assert load_doc(dst_box)["box"]["image"] == "probe/img:1"
        # ...and the duplicate got a FRESH workset identity, not the source's.
        assert read_workset_kuid(dst_root) != src_kuid
        assert "workset" not in load_doc(dst_box)

    def test_duplicate_does_not_pin_a_root_stored_value_at_the_box_tier(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        """⚑ A ``box.*`` key in the ROOT file is a WORKSET-tier downward default, and a
        duplicate must NOT persist it into the destination's BOX tier (Jei, 2026-08-26:
        "copy/persist only those elements that are within the box settings").  Pinning it
        at the box tier would convert an overridable workset default into a box-scope
        override that later workset edits could not reach.

        ⚑ It does not reach the duplicate at all, and that is the RULE rather than a
        gap: a standalone duplicate is a NEW workset scope — ``establish_standalone``
        writes the destination ROOT fresh — so a value authored at the SOURCE's workset
        tier is one the destination was never within the scope of.  What a duplicate
        DOES carry is the box tier
        (:meth:`test_duplicate_carries_the_box_tier_not_the_root_file`)."""
        from kanibako.commands.box._duplicate import run_duplicate
        from kanibako.commands.box._parser import run_show
        from kanibako.settings.config import read_workset_kuid
        from kanibako.settings.config_io import dump_doc, load_doc

        src = self._standalone(config_file, tmp_home)
        src_root, src_box = self._files(src)
        doc = load_doc(src_root)
        doc.setdefault("box", {})["image"] = "legacy/img:9"
        dump_doc(src_root, doc)
        assert not src_box.exists()          # nothing at the BOX tier to carry
        src_kuid = read_workset_kuid(src_root)

        dst = tmp_home / "dup_legacy"
        rc = run_duplicate(argparse.Namespace(
            source_path=str(src), new_path=str(dst), to_mode="standalone",
            bare=False, force=True, box=None,
        ))
        out = capsys.readouterr()
        assert rc == 0, f"out={out.out!r} err={out.err!r}"

        dst_root, dst_box = self._files(dst)
        # NOT pinned at the destination's box tier, NOR copied to its workset tier.
        assert "image" not in (load_doc(dst_box).get("box") or {})
        assert "image" not in (load_doc(dst_root).get("box") or {})
        run_show(argparse.Namespace(
            args=[str(dst)], box=None, effective=True, force=False,
        ))
        assert "legacy/img:9" not in capsys.readouterr().out
        # The SOURCE keeps it: the carry READ the source's tiers, it did not move them.
        assert load_doc(src_root)["box"]["image"] == "legacy/img:9"
        # ...and the duplicate still mints its OWN identity.
        assert read_workset_kuid(dst_root) != src_kuid
        assert "workset" not in load_doc(dst_box)


class TestAReservedNameInASettingsFileRefusesInsteadOfCrashing:
    """A RESERVED name hand-written into a settings file is a §0 refusal, NOT a traceback.

    ⚑ THE DEFECT WAS THE EXCEPTION TYPE, NOT A HOLE IN THE RESERVATION. The store
    refuses the name correctly and always did; ``ReservedKeyError`` merely subclassed
    ``KeyError`` alone, so it flew out of ``_file_partial`` → ``assemble_levels``
    past every ``except KanibakoError`` and reached the user as a stack trace.

    ⚑ END-TO-END THROUGH ``cli.main``, deliberately. ``tests/test_settings/
    test_keystore.py`` pins the class's two bases; these pin the OUTCOME, and the
    two fail independently — the store could refuse perfectly while the CLI still
    crashed, which is precisely what it did.
    """

    def _box(self, config_file, tmp_home):
        from kanibako.settings.paths import load_std_paths, resolve_project

        config = load_config(config_file)
        std = load_std_paths(config)
        project_dir = str(tmp_home / "project")
        proj = resolve_project(std, config, project_dir=project_dir, initialize=True)
        return project_dir, proj

    def _author(self, path, table):
        """MERGE a hand-authored table into *path* — never overwrite: the file has
        content of its own, and clobbering it tests a file no user has."""
        from kanibako.settings.config_io import dump_doc, load_doc

        doc = load_doc(path) or {}
        for scope, sub in table.items():
            if isinstance(sub, dict) and isinstance(doc.get(scope), dict):
                doc[scope].update(sub)
            else:
                doc[scope] = sub
        path.parent.mkdir(parents=True, exist_ok=True)
        dump_doc(path, doc)

    def _run(self, argv, capsys):
        """``cli.main`` to completion; return ``(exit_code, stderr)``.

        ⚑ A BARE ``main`` CALL IS THE POINT: the refusal has to survive the real
        entry point's handler stack. ``SystemExit`` NOT being raised is the failure
        mode under test — the old behavior — so its absence is an explicit fail.
        """
        from kanibako import cli

        try:
            cli.main(argv)
        except SystemExit as exc:
            return exc.code, capsys.readouterr().err
        pytest.fail("cli.main returned without SystemExit — the exception escaped")

    # The three reported spellings. ``agent:`` is dropped from a BOX file by
    # ``_drop_upward_scopes``, so the agent-scope specimen is authored where a user
    # would really put it: the SYSTEM file, whose ``agent: <name>:`` table is legal.
    @pytest.mark.parametrize(
        "scope_file,table,name",
        [
            ("box", {"box": {"get": "x"}}, "get"),
            ("box", {"box": {"items": "x"}}, "items"),
            ("system", {"agent": {"items": {"model": "x"}}}, "items"),
        ],
        ids=["box.get", "box.items", "agent.items.model"],
    )
    def test_it_refuses_at_rc_1_NAMING_the_key(
        self, config_file, tmp_home, credentials_dir, capsys, scope_file, table, name,
    ):
        # MUTATION-PROVED: restore ``class ReservedKeyError(KeyError)`` and all three
        # rows red on "cli.main returned without SystemExit".
        project_dir, proj = self._box(config_file, tmp_home)
        path = (
            proj.metadata_path / "box.yaml" if scope_file == "box"
            else tmp_home / "data" / "kanibako" / "global" / "settings.yaml"
        )
        self._author(path, table)

        code, err = self._run(["box", "show", project_dir, "--effective"], capsys)
        assert code == 1                       # a refusal, not a crash
        assert f"key '{name}' is reserved" in err   # ...that NAMES the key
        assert "fromkeys" in err               # ...and lists the set, so it is actionable
        # ⚑ ...and NAMES THE FILE. The key is the defect; the file is the address, and a
        # user with six cascade files had to guess which one to open. MUTATION-PROVED:
        # drop ``path=`` from the matching ``_file_partial`` call and this alone goes red.
        assert str(path) in err
        assert "Traceback" not in err

    def test_the_refusal_is_not_repr_wrapped(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        """⚑ ``KeyError.__str__`` REPR-WRAPS its argument, so without ``__str__`` the
        user's ``Error:`` line wears a pair of stray double quotes no other refusal
        has. Pinned here because it is the half of the fix a base-class change alone
        does not deliver."""
        project_dir, proj = self._box(config_file, tmp_home)
        self._author(proj.metadata_path / "box.yaml", {"box": {"get": "x"}})

        _, err = self._run(["box", "show", project_dir, "--effective"], capsys)
        assert 'Error: key' in err, err
        assert 'Error: "key' not in err, err

    def test_a_clean_file_is_untouched_by_the_refusal(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        """NON-VACUITY: the same verb on the same box succeeds when no reserved name
        is present, so the rows above pin the NAME and not a broken fixture."""
        project_dir, proj = self._box(config_file, tmp_home)
        self._author(proj.metadata_path / "box.yaml", {"box": {"image": "myimage"}})

        code, _ = self._run(["box", "show", project_dir, "--effective"], capsys)
        assert code == 0
