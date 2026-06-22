"""Behavior-equivalence tests for start.py's descriptor launch assembly (step 1e).

start.py now assembles a descriptor-bearing target's launch argv, container-env
overlay, and AGENT_CRITICAL delivery mounts declaratively via
``kanibako.targets.assembly`` instead of the legacy ``build_cli_args`` /
``apply_state`` / ``binary_mounts`` hooks.  These tests reproduce the exact
calls start.py makes (same arguments, same ``safe_off = not safe_mode`` mapping)
against claude's real descriptor and assert the result equals what the legacy
hooks produced — argv (modulo flag ordering, which is semantically irrelevant to
claude's CLI), container env, and delivery mounts.

They are pure: assembly only touches the filesystem via ``Path.exists()`` in
``descriptor_mounts``, so the mount tests build real bind sources under
``tmp_path``.
"""

from __future__ import annotations

import pytest

from kanibako.plugins.claude.target import ClaudeTarget
from kanibako.targets import assembly
from kanibako.targets.assembly import BindingSourceError, descriptor_mounts
from kanibako.targets.base import AgentInstall


# Claude crab default state: model=opus, access=permissive (generate_agent_config).
DEFAULT_STATE = {"model": "opus", "access": "permissive"}


def _start_argv(
    desc,
    *,
    safe_mode: bool,
    autonomous: bool = False,
    resume_mode: bool,
    new_session: bool,
    is_new_project: bool,
    extra_args: list[str],
    state: dict[str, str],
) -> list[str]:
    """Reproduce start.py's descriptor argv assembly exactly.

    Mirrors start.py: safe_off is resolved by ``effective_safe_mode_off`` from
    the per-launch -S (safe_mode) / -A (autonomous) flags plus the persisted
    ``access`` setting redeemed via ``safe_bypass.setting_key``.
    """
    sb = desc.safe_bypass
    persisted_access = (
        state.get(sb.setting_key, "")
        if sb is not None and sb.setting_key
        else ""
    )
    safe_off = assembly.effective_safe_mode_off(
        secure=safe_mode,
        autonomous=autonomous,
        persisted_access=persisted_access,
    )
    mode_key = assembly.resolve_mode(
        resume_mode=resume_mode,
        new_session=new_session,
        is_new_project=is_new_project,
        extra_args=extra_args,
        available_modes=desc.mode.keys(),
    )
    return assembly.assemble_argv(
        desc,
        mode_key=mode_key,
        safe_mode_off=safe_off,
        setting_values=state,
        op=None,
        extra_args=extra_args,
    )


# --------------------------------------------------------------------------- #
# argv assembly                                                               #
# --------------------------------------------------------------------------- #


class TestDescriptorArgv:
    def setup_method(self):
        self.desc = ClaudeTarget().descriptor
        assert self.desc is not None

    def test_default_continue_and_model(self):
        """Existing project, autonomous default: --continue + bypass + --model opus."""
        argv = _start_argv(
            self.desc,
            safe_mode=False,
            resume_mode=False,
            new_session=False,
            is_new_project=False,
            extra_args=[],
            state=DEFAULT_STATE,
        )
        assert "--continue" in argv
        assert "--dangerously-skip-permissions" in argv
        assert argv[argv.index("--model") + 1] == "opus"

    def test_autonomous_default_includes_bypass(self):
        """safe_off (-A / default) emits the skip-permissions flag."""
        argv = _start_argv(
            self.desc,
            safe_mode=False,
            resume_mode=False,
            new_session=False,
            is_new_project=False,
            extra_args=[],
            state=DEFAULT_STATE,
        )
        assert "--dangerously-skip-permissions" in argv

    def test_secure_excludes_bypass(self):
        """-S (safe_mode=True) omits the bypass flag."""
        argv = _start_argv(
            self.desc,
            safe_mode=True,
            resume_mode=False,
            new_session=False,
            is_new_project=False,
            extra_args=[],
            state=DEFAULT_STATE,
        )
        assert "--dangerously-skip-permissions" not in argv
        assert "--continue" in argv

    def test_new_session_skips_continue(self):
        """-N (new_session) drops --continue and emits no resume."""
        argv = _start_argv(
            self.desc,
            safe_mode=False,
            resume_mode=False,
            new_session=True,
            is_new_project=False,
            extra_args=[],
            state=DEFAULT_STATE,
        )
        assert "--continue" not in argv
        assert "--resume" not in argv

    def test_new_project_skips_continue(self):
        """A brand-new project skips --continue."""
        argv = _start_argv(
            self.desc,
            safe_mode=False,
            resume_mode=False,
            new_session=False,
            is_new_project=True,
            extra_args=[],
            state=DEFAULT_STATE,
        )
        assert "--continue" not in argv

    def test_resume_mode_falls_through_to_continue(self):
        """-R (resume_mode) on claude falls through to --continue.

        Resume was intentionally cut from claude's descriptor (user 2026-06-17:
        nonstandard/unused; reachable from interactive mode), so the mode map is
        {start, continue}. resolve_mode has no "resume" key to select, and -R does
        not force a new session, so it resolves to continue-last (--continue).
        """
        argv = _start_argv(
            self.desc,
            safe_mode=False,
            resume_mode=True,
            new_session=False,
            is_new_project=False,
            extra_args=[],
            state=DEFAULT_STATE,
        )
        assert "--continue" in argv
        assert "--resume" not in argv

    def test_extra_resume_skips_continue(self):
        """--resume passed through extra_args also skips --continue."""
        argv = _start_argv(
            self.desc,
            safe_mode=False,
            resume_mode=False,
            new_session=False,
            is_new_project=False,
            extra_args=["--resume"],
            state=DEFAULT_STATE,
        )
        assert argv.count("--continue") == 0
        assert "--resume" in argv

    def test_no_model_when_unset(self):
        """No model in state -> no --model flag."""
        argv = _start_argv(
            self.desc,
            safe_mode=False,
            resume_mode=False,
            new_session=False,
            is_new_project=False,
            extra_args=[],
            state={},
        )
        assert "--model" not in argv

    @pytest.mark.parametrize(
        "safe_mode,resume_mode,new_session,is_new_project,extra",
        [
            (False, False, False, False, []),
            (True, False, False, False, []),
            (False, False, True, False, []),
            (False, False, False, True, []),
            (False, False, False, False, ["--resume"]),
            (False, False, True, False, ["bar", "baz"]),
        ],
    )
    def test_descriptor_argv_carries_expected_invariants(
        self, safe_mode, resume_mode, new_session, is_new_project, extra
    ):
        """The descriptor argv carries the documented launch invariants.

        Replaces the former legacy ``build_cli_args``/``apply_state`` equivalence
        check (those hooks were removed for the descriptor-only public release).
        Asserts the same invariants the legacy comparison guaranteed, directly
        on the descriptor-assembled argv: ``--dangerously-skip-permissions`` iff
        not secure; ``--continue`` iff continuing (no new-session forcing and no
        ``--resume`` in extra); the ``--model opus`` setting flag; and that
        ``*extra`` is passed through.

        NOTE: ``resume_mode=True`` cases are excluded — resume was cut from
        claude's descriptor (user 2026-06-17), so the descriptor emits
        ``--continue`` where legacy emitted ``--resume`` (covered by
        ``test_resume_mode_falls_through_to_continue``).
        """
        argv = _start_argv(
            ClaudeTarget().descriptor,
            safe_mode=safe_mode,
            resume_mode=resume_mode,
            new_session=new_session,
            is_new_project=is_new_project,
            extra_args=list(extra),
            state=DEFAULT_STATE,
        )

        # safe-bypass: present iff NOT secure (-S).
        assert ("--dangerously-skip-permissions" in argv) is (not safe_mode)

        # continue: present iff continuing (not new-session/new-project and no
        # --resume requested in extra).
        skip_continue = (
            new_session or is_new_project or "--resume" in extra
        )
        assert ("--continue" in argv) is (not skip_continue)

        # model setting flag + passthrough of extra args.
        assert argv[argv.index("--model") + 1] == "opus"
        for a in extra:
            assert a in argv


class TestPersistedAccessSafeBypass:
    """The persisted ``access`` setting (step 1g) is now LIVE for claude.

    safe_off is resolved by ``effective_safe_mode_off`` from the per-launch
    -S/-A flags plus the redeemed ``access`` setting; the five rows below are
    the documented behavior contract.  ``--dangerously-skip-permissions`` is the
    claude safe-bypass flag (FLAG-channel ``safe_bypass``).
    """

    BYPASS = "--dangerously-skip-permissions"

    def setup_method(self):
        self.desc = ClaudeTarget().descriptor
        assert self.desc is not None

    def _argv(self, *, safe_mode, autonomous, access):
        state = {"model": "opus"}
        if access is not None:
            state["access"] = access
        return _start_argv(
            self.desc,
            safe_mode=safe_mode,
            autonomous=autonomous,
            resume_mode=False,
            new_session=False,
            is_new_project=False,
            extra_args=[],
            state=state,
        )

    def test_default_permissive_includes_bypass(self):
        """No -A/-S, access=permissive (default) -> bypass PRESENT (critical)."""
        argv = self._argv(safe_mode=False, autonomous=False, access="permissive")
        assert self.BYPASS in argv

    def test_secure_excludes_bypass_regardless_of_access(self):
        """-S wins: bypass ABSENT even when access=permissive."""
        argv = self._argv(safe_mode=True, autonomous=False, access="permissive")
        assert self.BYPASS not in argv

    def test_autonomous_overrides_restricted(self):
        """-A wins: bypass PRESENT even when access=restricted."""
        argv = self._argv(safe_mode=False, autonomous=True, access="restricted")
        assert self.BYPASS in argv

    def test_restricted_excludes_bypass(self):
        """No flags, access=restricted -> bypass ABSENT (new persistent-safe)."""
        argv = self._argv(safe_mode=False, autonomous=False, access="restricted")
        assert self.BYPASS not in argv

    def test_unknown_access_defaults_autonomous(self):
        """No flags, access unset/unknown (stale 'default') -> bypass PRESENT."""
        argv_unset = self._argv(safe_mode=False, autonomous=False, access=None)
        argv_stale = self._argv(safe_mode=False, autonomous=False, access="default")
        assert self.BYPASS in argv_unset
        assert self.BYPASS in argv_stale


# --------------------------------------------------------------------------- #
# container env assembly                                                       #
# --------------------------------------------------------------------------- #


class TestDescriptorEnv:
    def setup_method(self):
        self.desc = ClaudeTarget().descriptor
        assert self.desc is not None

    def test_carries_autoupdater_and_telemetry(self):
        env = assembly.assemble_env(
            self.desc, safe_mode_off=True, setting_values=DEFAULT_STATE,
        )
        assert env["DISABLE_AUTOUPDATER"] == "1"
        assert env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "1"

    def test_env_present_in_secure_mode_too(self):
        env = assembly.assemble_env(
            self.desc, safe_mode_off=False, setting_values=DEFAULT_STATE,
        )
        assert env["DISABLE_AUTOUPDATER"] == "1"
        assert env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "1"

    def test_env_superset_of_legacy_apply_state(self):
        """Descriptor env carries every key legacy apply_state set (DISABLE_AUTOUPDATER)
        plus the telemetry var that core start.py used to inject."""
        target = ClaudeTarget()
        _, legacy_env = target.apply_state(DEFAULT_STATE)
        new_env = assembly.assemble_env(
            target.descriptor, safe_mode_off=True, setting_values=DEFAULT_STATE,
        )
        for k, v in legacy_env.items():
            assert new_env.get(k) == v
        # plus the telemetry var that was previously core's special-case.
        assert new_env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "1"


# --------------------------------------------------------------------------- #
# delivery mounts                                                              #
# --------------------------------------------------------------------------- #


class TestDescriptorMounts:
    def _install(self, tmp_path):
        install_dir = tmp_path / "share" / "claude"
        install_dir.mkdir(parents=True)
        launcher = tmp_path / "bin" / "claude"
        launcher.parent.mkdir(parents=True)
        launcher.write_text("#!/bin/sh\n")
        return AgentInstall(
            name="claude",
            binary=launcher,
            install_dir=install_dir,
            launcher=launcher,
        )

    def test_delivery_mounts_cover_share_and_launcher(self, tmp_path):
        """descriptor_mounts delivers the share + launcher (ro) and skips plugins.

        Replaces the former equivalence-with-``binary_mounts`` check (the legacy
        hook was removed for the descriptor-only public release).
        """
        target = ClaudeTarget()
        install = self._install(tmp_path)

        new = descriptor_mounts(
            target.descriptor, install,
        )

        # Deliver the share (install_dir) + launcher, ro, and SKIP plugins.
        assert len(new) == 2
        assert all(m.options == "ro" for m in new)
        dests = {m.destination for m in new}
        assert dests == {
            "/home/agent/.local/share/claude",
            "/home/agent/.local/bin/claude",
        }

    def test_plugins_not_a_descriptor_binding(self, tmp_path):
        """Part 3a: plugins is an ``agent.shared`` category entry, not a binding.

        The descriptor's delivery binds no longer include plugins (or cache);
        those flow through the category resolver from ``default_shares()``.
        """
        target = ClaudeTarget()
        install = self._install(tmp_path)
        new = descriptor_mounts(
            target.descriptor, install,
        )
        assert all("plugins" not in m.destination for m in new)
        assert "plugins" not in {b.key for b in target.descriptor.bindings}

    def test_missing_source_raises_binding_source_error(self, tmp_path):
        """A vanished AGENT_CRITICAL source raises BindingSourceError (clean safe-fail)."""
        target = ClaudeTarget()
        install = self._install(tmp_path)
        # Remove the launcher source after building the install.
        install.launcher.unlink()
        with pytest.raises(BindingSourceError):
            descriptor_mounts(target.descriptor, install)
