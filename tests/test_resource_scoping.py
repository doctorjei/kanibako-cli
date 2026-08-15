"""Tests for resource scoping: kanibako mounts and effective-state precedence.

The ``_build_resource_mounts`` / ``ResourceMapping`` / ``ResourceScope`` resource
abstraction was DELETED in 1.6.0 (Part 3b): every shipped mapping was PROJECT
(lives in the box home bind, no mount), and claude's only shared dirs (plugins +
cache) are now ``agent.<agent>.common.*`` category entries.  Those tests are removed.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from kanibako.settings.agent_config import AgentConfig
from kanibako.targets.base import TargetSetting


class TestKanibakoMounts:
    """Tests for _kanibako_mounts() in start.py."""

    def test_returns_two_mounts(self):
        from kanibako.commands.start import _kanibako_mounts

        mounts = _kanibako_mounts()
        assert len(mounts) == 2

    def test_package_mount_destination(self):
        from kanibako.commands.start import _kanibako_mounts

        mounts = _kanibako_mounts()
        pkg_mount = mounts[0]
        assert pkg_mount.destination == "/opt/kanibako/kanibako"
        assert pkg_mount.options == "ro"

    def test_entry_script_mount_destination(self):
        from kanibako.commands.start import _kanibako_mounts

        mounts = _kanibako_mounts()
        entry_mount = mounts[1]
        assert entry_mount.destination == "/home/agent/.local/bin/kanibako"
        assert entry_mount.options == "ro"

    def test_package_source_is_kanibako_dir(self):
        from kanibako.commands.start import _kanibako_mounts

        mounts = _kanibako_mounts()
        pkg_mount = mounts[0]
        # Source should be the kanibako package directory
        assert pkg_mount.source.is_dir()
        assert (pkg_mount.source / "__init__.py").is_file()

    def test_entry_script_source_exists(self):
        from kanibako.commands.start import _kanibako_mounts

        mounts = _kanibako_mounts()
        entry_mount = mounts[1]
        assert entry_mount.source.is_file()
        content = entry_mount.source.read_text()
        assert "kanibako.cli" in content

    def test_routed_kani_categories_match_hardwired(self):
        """Phase B: the routed kani binds carry the SAME sources/dests/options.

        The box launch now routes the kanibako CLI binds through
        ``core_defaults.kani_default_categories`` (the category resolver) instead
        of the hardwired ``_kanibako_mounts`` ``-v`` list (which survives only as
        the in-helper-container source resolver).  Lock that the two agree
        byte-for-byte so the routing is a pure refactor.
        """
        from kanibako.settings import core_defaults
        from kanibako.commands.start import _kanibako_mounts

        hardwired = _kanibako_mounts()
        cats = core_defaults.kani_default_categories()

        # ⚑ Re-derived for dest-keying (R-3/R-5/R-11, P6): the two binds are
        # ENTRIES of the ONE terminal ``box.bindings.ro`` arm keyed by their
        # destinations, and R-11 absolutizes the declarative file's ``~`` at the
        # producer rather than leaving it for the resolver. The byte-for-byte
        # source/dest/options agreement this test exists to lock is unchanged —
        # only where the destination is written down moved.
        arm = cats["box.bindings.ro"]
        assert arm["/opt/kanibako/kanibako"] == (str(hardwired[0].source), "ro")
        assert arm["/home/agent/.local/bin/kanibako"] == (
            str(hardwired[1].source), "ro",
        )
        # The destinations agree as LITERAL equals now — the hardwired list has
        # always spelled them absolute, and R-11 brings the routed side to match.
        for mount in hardwired:
            assert arm[mount.destination] == (str(mount.source), mount.options)


class TestSecretExportBind:
    """D2: the universal `/etc/profile.d` secret-export snippet, delivered as an
    UNCONDITIONAL kani ro bind (models on kani_pkg/kani_bin).  It reaches the
    non-login `podman exec` paths (VS Code terminal, codex panel app-server) that
    the PID-1 `_secret_export_shim` cannot; the shim stays for the supervised
    PID-1 path (non-login `sh -c`, no profile.d)."""

    _SNIPPET_NAME = "kanibako-secrets.sh"

    def _snippet_path(self):
        import importlib.resources
        from pathlib import Path

        ref = importlib.resources.files("kanibako.scripts").joinpath(self._SNIPPET_NAME)
        return Path(str(ref))

    def test_bind_emitted_with_dest_options_and_real_source(self):
        """`kani_default_categories()` binds the snippet at `/etc/profile.d/…`, ro,
        from a REAL regular-file host source.

        ⚑ Re-derived for dest-keying (R-3/R-5): the ``secret_export`` NAME is
        retired (R-10) and the destination IS the identity, so what pins the bind's
        existence is its dest being present in the terminal ``box.bindings.ro``
        arm.
        """
        from pathlib import Path

        from kanibako.settings import core_defaults

        dest = "/etc/profile.d/kanibako-secrets.sh"
        arm = core_defaults.kani_default_categories()["box.bindings.ro"]
        assert dest in arm
        src, opts = arm[dest]
        assert opts == "ro"
        assert Path(src).is_file()
        # The resolved source IS the packaged snippet.
        assert Path(src) == self._snippet_path()

    def test_snippet_drift_guard_matches_secret_mount_dir(self):
        """The shipped snippet hardcodes SECRET_MOUNT_DIR's value — a change to the
        constant that forgot the snippet would desync silently, so lock it here."""
        from kanibako.settings.settings_categories import SECRET_MOUNT_DIR

        text = self._snippet_path().read_text()
        assert SECRET_MOUNT_DIR in text
        # The self-adapting glob + empty-dir guard + read/export mechanics.
        assert f"{SECRET_MOUNT_DIR}/*" in text
        assert '[ -e "$f" ]' in text
        assert "basename" in text and "cat" in text and "export" in text

    def test_snippet_passes_sh_syntax_check(self):
        """`sh -n` must accept the snippet (it is sourced by every login shell)."""
        import subprocess

        r = subprocess.run(
            ["sh", "-n", str(self._snippet_path())],
            capture_output=True, text=True,
        )
        assert r.returncode == 0, r.stderr

    def test_snippet_exports_mounted_secrets(self, tmp_path):
        """Runtime exercise: with token files present, the snippet exports each
        var with the file's (newline-stripped) contents."""
        import subprocess

        from kanibako.settings.settings_categories import SECRET_MOUNT_DIR

        secrets = tmp_path / "secrets"
        secrets.mkdir()
        (secrets / "FOO").write_text("foo-token-value\n")
        (secrets / "BAR").write_text("bar-val")  # no trailing newline
        body = self._snippet_path().read_text().replace(str(SECRET_MOUNT_DIR), str(secrets))
        script = body + '\nprintf "FOO=[%s]\\nBAR=[%s]\\n" "$FOO" "$BAR"\n'
        r = subprocess.run(["sh", "-c", script], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        assert "FOO=[foo-token-value]" in r.stdout
        assert "BAR=[bar-val]" in r.stdout

    def test_snippet_is_noop_when_empty_or_absent(self, tmp_path):
        """No secrets mounted → the literal glob is guarded to a clean no-op (both
        an empty dir and an absent dir), even under `set -e`."""
        import subprocess

        from kanibako.settings.settings_categories import SECRET_MOUNT_DIR

        empty = tmp_path / "empty"
        empty.mkdir()
        absent = tmp_path / "does-not-exist"
        for target in (empty, absent):
            body = self._snippet_path().read_text().replace(
                str(SECRET_MOUNT_DIR), str(target)
            )
            r = subprocess.run(
                ["sh", "-c", "set -e\n" + body + '\necho DONE\n'],
                capture_output=True, text=True,
            )
            assert r.returncode == 0, r.stderr
            assert "DONE" in r.stdout


class TestBuildEffectiveState:
    """Tests for the ``config --effective`` behavior read
    (``start._effective_behavior_for_display``, block 7c — the snapshot-based
    successor to the retired ``_build_effective_state`` precedence walk). The
    discriminated ``agent.<name>.*`` / ``agent.default.*`` file shapes
    (``write_agent_setting``) feed the snapshot; the §2d active-over-default pick
    yields the launch-correct effective state."""

    def _make_target(self, descriptors, name="claude"):
        target = MagicMock()
        target.setting_descriptors.return_value = descriptors
        target.name = name
        return target

    def _make_global_config(self, tmp_path, settings=None):
        """Create a minimal global kanibako_config.yaml, optionally with [agent]."""
        from kanibako.settings.config import write_agent_setting

        global_toml = tmp_path / "kanibako_config.yaml"
        global_toml.write_text("")
        if settings:
            for k, v in settings.items():
                write_agent_setting(global_toml, k, v, "claude")
        return global_toml

    def _make_workset_config(self, tmp_path, settings=None, agent="claude"):
        """Create a minimal workset config.yaml, optionally with a box→agent tweak.

        A workset-scope per-agent behavior override rides the §2h REQUEST
        ``pref.agent.<agent>.<key>`` — prefs are legal in the WORKSET and BOX
        files, and a workset's request applies to its boxes. (A workset file may
        NOT set ``agent.<name>.*`` directly — that is an upward write dropped at
        RESOLVE, spec §0; workset ⊂ agent. ⮕ P7: was the ``box.agent.*``
        defaults-down mirror, retired by §2b.)
        """
        from kanibako.settings.config_io import dump_doc

        tmp_path.mkdir(parents=True, exist_ok=True)
        ws_toml = tmp_path / "config.yaml"
        if settings:
            dump_doc(ws_toml, {"pref": {"agent": {agent: dict(settings)}}})
        else:
            ws_toml.write_text("")
        return ws_toml

    def _make_project_toml(self, tmp_path, settings=None, agent="claude"):
        """Create a minimal box settings.yaml, optionally with a box→agent tweak.

        A per-agent behavior override at the BOX scope rides the §2h REQUEST
        ``pref.agent.<agent>.<key>``. (A box file may NOT set ``agent.<name>.*``
        directly: that is an upward write dropped at RESOLVE, spec §0.) ⮕ P7: this
        used the ``box.agent.*`` mirror, which spec §2b RETIRED.
        """
        from kanibako.settings.config import write_project_config
        from kanibako.settings.config_io import dump_doc, load_doc

        tmp_path.mkdir(parents=True, exist_ok=True)
        project_toml = tmp_path / "settings.yaml"
        # A minimal box-tier settings file (P8b sparse create writes no identity
        # section; the pref requests below are what this scoping test cares about).
        write_project_config(project_toml, "base:image")
        if settings:
            doc = load_doc(project_toml)
            node = doc.setdefault("pref", {}).setdefault("agent", {}).setdefault(
                agent, {},
            )
            node.update(settings)
            dump_doc(project_toml, doc)
        return project_toml

    def test_target_defaults_only(self, tmp_path):
        """With no agent state and no project overrides, the DECLARED floors apply.

        Two of them, and the display shows BOTH (D1-1): the target's descriptor
        defaults, plus the core behavior floor from ``core-defaults.yaml``
        (``agent_default:`` → ``agent.default.<key>``, spec §2d). The core keys are
        listed here rather than filtered out because the launch applies them too —
        omitting them from the display was the DISPLAY==LAUNCH lie this read exists
        to prevent.
        """
        from kanibako.commands.start import _effective_behavior_for_display as _build_effective_state

        descriptors = [
            TargetSetting(key="model", description="Model", default="opus"),
            TargetSetting(key="access", description="Access", default="permissive"),
        ]
        target = self._make_target(descriptors)
        agent_cfg = AgentConfig()  # empty state
        project_toml = self._make_project_toml(tmp_path)

        result = _build_effective_state(
            target, agent_cfg, project_toml, system_settings_path=None
        )
        assert result == {
            # The DESCRIPTOR floor.
            "model": "opus",
            "access": "permissive",
            # The CORE behavior floor (core-defaults.yaml ``agent_default:``).
            "allow_helpers": "true",
            "continue_mode": "true",
            "bootstrap": "tmux",
        }

    def test_agent_overrides_default(self, tmp_path):
        """Agent config state overrides target defaults."""
        from kanibako.commands.start import _effective_behavior_for_display as _build_effective_state

        descriptors = [
            TargetSetting(key="model", description="Model", default="opus"),
        ]
        target = self._make_target(descriptors)
        agent_cfg = AgentConfig(state={"model": "sonnet"})
        project_toml = self._make_project_toml(tmp_path)

        result = _build_effective_state(
            target, agent_cfg, project_toml, system_settings_path=None
        )
        assert result["model"] == "sonnet"

    def test_project_override_wins(self, tmp_path):
        """Project overrides take highest precedence."""
        from kanibako.commands.start import _effective_behavior_for_display as _build_effective_state

        descriptors = [
            TargetSetting(key="model", description="Model", default="opus"),
        ]
        target = self._make_target(descriptors)
        agent_cfg = AgentConfig(state={"model": "sonnet"})
        project_toml = self._make_project_toml(tmp_path, settings={"model": "haiku"})

        result = _build_effective_state(
            target, agent_cfg, project_toml, system_settings_path=None
        )
        assert result["model"] == "haiku"

    def test_an_undeclared_agent_state_key_is_REFUSED_not_passed_through(self, tmp_path):
        """⚑⚑ INVERTED AT S3 (D-5's other end), and the inversion is the point.

        This asserted that "undeclared keys from agent state are passed through" — the *"old
        ``agent.<name>.<anyleaf>`` behaviour"* spec §0 SPECIFICALLY EXCLUDES. Together with the
        then-ungated ``agent set`` it meant stored garbage was not merely dead: it reached the
        box. The refusal lives at the boundary that builds the level
        (``agent_file.state_level``), so it fires wherever a level is built — here, and at every
        launch. ⚑ ``agent list`` / ``info`` read ``cfg.state`` directly and never build a level,
        so a poisoned file still LISTS and can still be repaired.
        """
        from kanibako.commands.start import _effective_behavior_for_display as _build_effective_state
        from kanibako.settings.settings_resolve import SettingsError

        descriptors = [
            TargetSetting(key="model", description="Model", default="opus"),
        ]
        target = self._make_target(descriptors)
        agent_cfg = AgentConfig(state={"model": "sonnet", "custom_key": "custom_value"})
        project_toml = self._make_project_toml(tmp_path)

        with pytest.raises(SettingsError) as exc:
            _build_effective_state(
                target, agent_cfg, project_toml, system_settings_path=None
            )
        assert "custom_key" in str(exc.value)

        # The CONTROL: the declared key alone still resolves, so the refusal is about
        # declaredness and not about having any agent state at all.
        agent_cfg = AgentConfig(state={"model": "sonnet"})
        assert _build_effective_state(
            target, agent_cfg, project_toml, system_settings_path=None
        )["model"] == "sonnet"

    def test_no_descriptors_returns_agent_state(self, tmp_path):
        """When target has no setting_descriptors, return agent state as-is."""
        from kanibako.commands.start import _effective_behavior_for_display as _build_effective_state

        target = self._make_target([])  # no descriptors
        agent_cfg = AgentConfig(state={"model": "opus", "access": "permissive"})
        project_toml = self._make_project_toml(tmp_path)

        result = _build_effective_state(
            target, agent_cfg, project_toml, system_settings_path=None
        )
        assert result == {"model": "opus", "access": "permissive"}

    def test_system_level_provides_value(self, tmp_path):
        """System [crab] (global kanibako_config.yaml) supplies a value when nothing
        more specific sets it."""
        from kanibako.commands.start import _effective_behavior_for_display as _build_effective_state

        descriptors = [
            TargetSetting(key="model", description="Model", default="opus"),
        ]
        target = self._make_target(descriptors)
        agent_cfg = AgentConfig()  # empty state
        project_toml = self._make_project_toml(tmp_path)
        global_toml = self._make_global_config(tmp_path, settings={"model": "sonnet"})

        result = _build_effective_state(
            target, agent_cfg, project_toml, system_settings_path=global_toml
        )
        # System set value beats the target-default floor.
        assert result["model"] == "sonnet"

    def test_precedence_box_workset_crab_system(self, tmp_path):
        """Precedence is box > workset > crab > system; system beats the floor.

        Levels are most-specific-first ``[box, workset, crab, system]``, so a
        value set at the workset level beats one set in crab state. The box- and
        workset-scope per-agent overrides ride the box.agent.* mirror (§2b): the
        box sets it same-scope, the workset sets it defaults-down (workset ⊂ box);
        effective_behavior reads box.agent first, so a box-file box.agent still
        beats a workset-file box.agent, which still beats the crab (agent-state)
        default. (Neither a box nor a workset file may set agent.<name>.* directly
        — upward, dropped at RESOLVE, spec §0; the mirror is the legal path.)
        """
        from kanibako.commands.start import _effective_behavior_for_display as _build_effective_state

        descriptors = [
            TargetSetting(key="model", description="Model", default="opus"),
            TargetSetting(key="access", description="Access", default="permissive"),
        ]
        target = self._make_target(descriptors)
        global_toml = self._make_global_config(
            tmp_path, settings={"model": "sys-model", "access": "default"}
        )
        # workset config lives in its own dir to avoid colliding filenames.
        ws_toml = self._make_workset_config(
            tmp_path / "ws", settings={"model": "ws-model"}
        )

        # crab state also sets model — but workset is more specific, so workset
        # wins.  access is left for the system level only.
        agent_cfg = AgentConfig(state={"model": "crab-model"})
        proj_dir = tmp_path / "proj"
        proj_dir.mkdir()
        project_toml = self._make_project_toml(proj_dir)

        result = _build_effective_state(
            target,
            agent_cfg,
            project_toml,
            system_settings_path=global_toml,
            workset_config_path=ws_toml,
        )
        # model: box unset → workset (more specific than crab/system) wins.
        assert result["model"] == "ws-model"
        # access: only system sets it; nothing more specific does, so the
        # system set value wins over the "permissive" floor.
        assert result["access"] == "default"

        # Now set model at the box level too → box beats workset.
        box_toml = self._make_project_toml(
            tmp_path / "proj2", settings={"model": "box-model"}
        )
        result2 = _build_effective_state(
            target,
            agent_cfg,
            box_toml,
            system_settings_path=global_toml,
            workset_config_path=ws_toml,
        )
        assert result2["model"] == "box-model"

    def test_empty_string_is_terminal(self, tmp_path):
        """An explicit '' at a level suppresses fall-through to the floor."""
        from kanibako.commands.start import _effective_behavior_for_display as _build_effective_state

        descriptors = [
            TargetSetting(key="model", description="Model", default="opus"),
        ]
        target = self._make_target(descriptors)
        # crab state explicitly clears model.
        agent_cfg = AgentConfig(state={"model": ""})
        project_toml = self._make_project_toml(tmp_path)

        result = _build_effective_state(
            target, agent_cfg, project_toml, system_settings_path=None
        )
        # Terminal "" — does not fall back to the "opus" floor.
        assert result["model"] == ""

    def test_box_override_does_not_bleed_across_agents(self, tmp_path):
        """B3 regression: an agent.claude override must NOT apply when the
        effective state is resolved for agent goose (and vice-versa).

        The per-agent override rides the SYSTEM file — a spec-legal downward
        source that carries a non-active agent's table (system ⊃ agent). (A box
        file may NOT set agent.<name>.* — upward, dropped at RESOLVE, spec §0 — and
        the box.agent.* mirror only targets the ACTIVE agent, so it cannot express
        the claude-vs-goose discrimination this test pins.)"""
        from kanibako.commands.start import _effective_behavior_for_display as _build_effective_state

        descriptors = [
            TargetSetting(key="model", description="Model", default="opus"),
        ]
        # An override for claude, carried by the (legal) system file.
        global_toml = self._make_global_config(tmp_path, settings={"model": "sonnet"})
        project_toml = self._make_project_toml(tmp_path / "proj")
        agent_cfg = AgentConfig()

        # claude sees its override.
        claude = self._make_target(descriptors, name="claude")
        res_claude = _build_effective_state(
            claude, agent_cfg, project_toml, system_settings_path=global_toml
        )
        assert res_claude["model"] == "sonnet"

        # goose does NOT — it falls back to its declared default floor.
        goose = self._make_target(descriptors, name="goose")
        res_goose = _build_effective_state(
            goose, agent_cfg, project_toml, system_settings_path=global_toml
        )
        assert res_goose["model"] == "opus"

    def test_default_tier_applies_to_all_agents_unless_overridden(self, tmp_path):
        """agent.default applies to every agent; agent.<name> overrides it.

        Both the any-agent default and the claude-specific override ride the
        SYSTEM file — the legal downward source that carries per-agent /
        agent.default tables (system ⊃ agent). (A box file may not set agent.*;
        see test_box_override_does_not_bleed_across_agents.)"""
        from kanibako.commands.start import _effective_behavior_for_display as _build_effective_state
        from kanibako.settings.config import write_agent_setting

        descriptors = [
            TargetSetting(key="model", description="Model", default="opus"),
        ]
        # Any-agent default + a claude-specific override, both on the system file.
        global_toml = self._make_global_config(tmp_path, settings={"model": "sonnet"})
        write_agent_setting(global_toml, "model", "haiku", "default")
        project_toml = self._make_project_toml(tmp_path / "proj")
        agent_cfg = AgentConfig()

        claude = self._make_target(descriptors, name="claude")
        res_claude = _build_effective_state(
            claude, agent_cfg, project_toml, system_settings_path=global_toml
        )
        assert res_claude["model"] == "sonnet"  # agent-specific wins

        goose = self._make_target(descriptors, name="goose")
        res_goose = _build_effective_state(
            goose, agent_cfg, project_toml, system_settings_path=global_toml
        )
        assert res_goose["model"] == "haiku"  # default tier applies


class TestXdgFallbackRegression:
    """Stored ``$XDG_*`` values must expand through the ``config --effective``
    behavior-snapshot ctx — the 2026-07-02 live-dogfood XDG-fallback bug.

    Pre-fix, ``start._effective_behavior_for_display`` built its ResolveCtx with
    ``xdg={}``, so ANY stored ``$XDG_CACHE_HOME/...`` value (e.g. the
    setup-materialized ``system.cache`` in a 1.6.0-era global config, expanded
    eagerly by the strict launch-snapshot pipeline) raised ``Variable
    $XDG_CACHE_HOME is not set in this context`` — even with the env var
    exported, because the resolver reads ONLY the ctx map, never the
    environment.  The fix routes every host-side ResolveCtx through the
    canonical ``paths.host_xdg_map()`` (Jei ruling: XDG vars have fallbacks).

    All three tests were verified to FAIL on the pre-fix baseline
    (``SettingsError: Variable $XDG_CACHE_HOME is not set in this context``)
    and pass with the fix.
    """

    def _seeded_setup(self, tmp_path):
        """Build a target + stored configs carrying raw ``$XDG_CACHE_HOME``.

        The global config mirrors the live carrier state: the
        setup-materialized ``system.cache`` entry (the value that crashed the
        launch) PLUS a declared behavior key with the same token, so the
        expanded value is assertable end-to-end through the display read.
        """
        from kanibako.settings.config import write_project_config

        target = MagicMock()
        target.setting_descriptors.return_value = [
            TargetSetting(key="cache_probe", description="Probe", default="unset"),
        ]
        target.name = "claude"

        global_toml = tmp_path / "kanibako_config.yaml"
        global_toml.write_text(
            "system:\n"
            "  cache: $XDG_CACHE_HOME/kanibako\n"
            "agent:\n"
            "  claude:\n"
            "    cache_probe: $XDG_CACHE_HOME/kanibako/probe\n"
        )

        proj_dir = tmp_path / "proj"
        proj_dir.mkdir()
        project_toml = proj_dir / "settings.yaml"
        # A minimal box-tier settings file (P8b sparse create writes no identity
        # section); the XDG expansion under test rides the global/agent tiers.
        write_project_config(project_toml, "base:image")
        return target, AgentConfig(), project_toml, global_toml

    def test_stored_xdg_cache_value_with_env_var_set(self, tmp_path, monkeypatch):
        """THE dogfood repro (map-not-env): the env var IS exported, yet the
        pre-fix ctx still raised — its map simply lacked the key.  With the fix
        the stored value expands to the env var's value."""
        from kanibako.commands.start import _effective_behavior_for_display

        monkeypatch.setenv("XDG_CACHE_HOME", "/custom/cache")
        target, agent_cfg, project_toml, global_toml = self._seeded_setup(tmp_path)

        result = _effective_behavior_for_display(
            target, agent_cfg, project_toml, system_settings_path=global_toml
        )
        assert result["cache_probe"] == "/custom/cache/kanibako/probe"

    def test_stored_xdg_cache_value_env_unset_falls_back(self, tmp_path, monkeypatch):
        """Unset env var → the stored value expands to the XDG-spec default
        ``<home>/.cache/...`` instead of raising."""
        from pathlib import Path

        from kanibako.commands.start import _effective_behavior_for_display

        monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
        target, agent_cfg, project_toml, global_toml = self._seeded_setup(tmp_path)

        result = _effective_behavior_for_display(
            target, agent_cfg, project_toml, system_settings_path=global_toml
        )
        expected = str(Path.home() / ".cache" / "kanibako" / "probe")
        assert result["cache_probe"] == expected

    def test_stored_xdg_cache_value_env_empty_falls_back(self, tmp_path, monkeypatch):
        """An EMPTY env var is as good as unset (per the spec) → default."""
        from pathlib import Path

        from kanibako.commands.start import _effective_behavior_for_display

        monkeypatch.setenv("XDG_CACHE_HOME", "")
        target, agent_cfg, project_toml, global_toml = self._seeded_setup(tmp_path)

        result = _effective_behavior_for_display(
            target, agent_cfg, project_toml, system_settings_path=global_toml
        )
        expected = str(Path.home() / ".cache" / "kanibako" / "probe")
        assert result["cache_probe"] == expected
