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

from kanibako.errors import ConfigError
from kanibako.plugins.claude.target import ClaudeTarget
from kanibako.targets import assembly
from kanibako.targets.assembly import BindingSourceError, descriptor_mounts
from kanibako.targets.base import AgentInstall


# Claude crab default state: model=opus (generate_agent_config).  ``access`` is
# UNSET here — the launch reader defaults it to the ``full`` tier (R-41).
DEFAULT_STATE = {"model": "opus"}


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

    Mirrors start.py: the launch TIER is resolved by ``effective_access`` from
    the per-launch -S (safe_mode) / -A (autonomous) flags plus the persisted
    ``access`` enum key redeemed via ``safe_bypass.setting_key`` (validated
    against the enum, DEFAULT ``full`` when unset).
    """
    from kanibako.settings.settings_launch import meta_agent_grammar_floor

    sb = desc.safe_bypass
    launch_access = assembly.effective_access(
        secure=safe_mode,
        autonomous=autonomous,
        access=(
            state.get(sb.setting_key)
            if sb is not None and sb.setting_key
            else None
        ),
    )
    # B5: mirror start.py — the launch grammar is MATERIALIZED into the keyspace
    # (the same single descriptor→keyspace builder the launch uses) and the
    # composition reads the table, never the descriptor directly.
    mode_table = meta_agent_grammar_floor("claude", desc)["meta.agent.claude.mode"]
    mode_key = assembly.resolve_mode(
        resume_mode=resume_mode,
        new_session=new_session,
        is_new_project=is_new_project,
        extra_args=extra_args,
        available_modes=mode_table.keys(),
    )
    return assembly.assemble_argv(
        desc,
        mode_fragment=mode_table[mode_key],
        access=launch_access,
        setting_values=state,
        op_fragment=None,
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


class TestPersistedAccessTierClaude:
    """The persisted ``access`` TIER key (spec §2d, R-41) is LIVE for claude.

    The launch tier is resolved by ``effective_access`` from the per-launch
    -S/-A flags plus the redeemed ``access`` key (validated against the enum,
    DEFAULT ``full`` when unset); the rows below are the documented behavior
    contract.  ``--dangerously-skip-permissions`` is claude's ``full`` row and
    ``--permission-mode acceptEdits`` its ``editing`` row (FLAG-channel
    ``safe_bypass``).
    """

    BYPASS = "--dangerously-skip-permissions"
    ACCEPT_EDITS = ("--permission-mode", "acceptEdits")

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

    def _has_edits(self, argv):
        return any(
            argv[i:i + 2] == list(self.ACCEPT_EDITS) for i in range(len(argv))
        )

    def test_default_full_includes_bypass(self):
        """No -S/-A, access=full (the default) -> bypass PRESENT (critical)."""
        argv = self._argv(safe_mode=False, autonomous=False, access="full")
        assert self.BYPASS in argv
        assert not self._has_edits(argv)

    def test_editing_emits_accept_edits_and_not_the_bypass(self):
        """The MIDDLE tier: acceptEdits PRESENT, bypass ABSENT."""
        argv = self._argv(safe_mode=False, autonomous=False, access="editing")
        assert self._has_edits(argv)
        assert self.BYPASS not in argv

    def test_restricted_emits_neither(self):
        """No flags, access=restricted -> nothing emitted (claude prompts)."""
        argv = self._argv(safe_mode=False, autonomous=False, access="restricted")
        assert self.BYPASS not in argv
        assert not self._has_edits(argv)

    def test_secure_flag_beats_every_stored_tier(self):
        """-S wins from ANY stored tier -> restricted (nothing emitted)."""
        for stored in ("restricted", "editing", "full"):
            argv = self._argv(safe_mode=True, autonomous=False, access=stored)
            assert self.BYPASS not in argv, stored
            assert not self._has_edits(argv), stored

    def test_autonomous_flag_beats_every_stored_tier(self):
        """-A wins from ANY stored tier -> full (bypass present)."""
        for stored in ("restricted", "editing", "full"):
            argv = self._argv(safe_mode=False, autonomous=True, access=stored)
            assert self.BYPASS in argv, stored

    def test_unset_defaults_to_full(self):
        """No flags, access unset -> bypass PRESENT (the ``full`` default)."""
        assert self.BYPASS in self._argv(
            safe_mode=False, autonomous=False, access=None,
        )

    def test_unknown_stored_value_is_refused_not_permissive(self):
        """⚑ THE inversion vs the retired boolean: a junk value used to
        ``coerce_bool`` to None and fall back to PERMISSIVE.  It now RAISES."""
        with pytest.raises(ConfigError):
            self._argv(safe_mode=False, autonomous=False, access="bogus")


def _launch_tier(desc, *, secure, autonomous, persisted):
    """Mirror start.py's launch read: validate the persisted ``access``
    (DEFAULT ``full`` when unset) then fold in the per-launch flags."""
    sb = desc.safe_bypass
    return assembly.effective_access(
        secure=secure,
        autonomous=autonomous,
        access=persisted if sb is not None and sb.setting_key else None,
    )


class TestUniformAccessAcrossAgents:
    """``access`` is a UNIFORM persisted key across all 3 shipped agents: each
    descriptor redeems it via ``safe_bypass.setting_key == "access"``
    (claude/codex FLAG channel, goose ENV channel).  This class is the
    3 tiers × 3 agents half of the R-41 matrix on the EPHEMERAL (argv/env)
    consumer; the projected-surface half lives in the panel-delivery tests."""

    def test_all_descriptors_redeem_access(self):
        from kanibako.plugins.codex.target import CodexTarget
        from kanibako.plugins.goose.target import GooseTarget

        for T in (ClaudeTarget, CodexTarget, GooseTarget):
            sb = T().descriptor.safe_bypass
            assert sb is not None and sb.setting_key == "access"

    def _argv_at(self, desc, *, secure=False, autonomous=False, persisted=None):
        tier = _launch_tier(
            desc, secure=secure, autonomous=autonomous, persisted=persisted,
        )
        return assembly.assemble_argv(
            desc, mode_fragment=desc.mode["start"], access=tier,
            setting_values={}, extra_args=[],
        )

    def test_claude_rows_per_tier(self):
        desc = ClaudeTarget().descriptor
        bypass = "--dangerously-skip-permissions"
        assert self._argv_at(desc, persisted="full") == [bypass]
        assert self._argv_at(desc, persisted="editing") == [
            "--permission-mode", "acceptEdits",
        ]
        assert self._argv_at(desc, persisted="restricted") == []
        # unset -> the ``full`` default
        assert self._argv_at(desc, persisted=None) == [bypass]
        # -A overrides a stored restricted -> full; -S overrides a stored full.
        assert self._argv_at(desc, autonomous=True, persisted="restricted") == [bypass]
        assert self._argv_at(desc, secure=True, persisted="full") == []

    def test_codex_rows_per_tier(self):
        from kanibako.plugins.codex.target import CodexTarget

        desc = CodexTarget().descriptor
        bypass = "--dangerously-bypass-approvals-and-sandbox"
        assert self._argv_at(desc, persisted="full") == [bypass]
        # ⚑ The editing row is the VERIFIED sandbox enum's middle step. The
        # approval flag is deliberately NOT emitted: `-a` does not exist on
        # `codex exec`, which shares this argv tail.
        assert self._argv_at(desc, persisted="editing") == ["-s", "workspace-write"]
        assert "-a" not in self._argv_at(desc, persisted="editing")
        assert "--ask-for-approval" not in self._argv_at(desc, persisted="editing")
        assert self._argv_at(desc, persisted="restricted") == []
        assert self._argv_at(desc, persisted=None) == [bypass]
        assert self._argv_at(desc, autonomous=True, persisted="restricted") == [bypass]
        assert self._argv_at(desc, secure=True, persisted="full") == []

    def test_codex_never_emits_the_alias_bypass_flags(self):
        """``--yolo`` / ``--full-auto`` are never emitted (D-7).

        ⚑ NOT because they are absent or undocumented — that justification was
        false. Verified on codex-cli 0.141.0 (2026-08-02): neither is listed in
        ``codex --help`` / ``codex exec --help``, but BOTH parse (``--yolo`` at
        the top level, ``--full-auto`` on ``exec``), and OpenAI's own docs
        reference ``--yolo`` in ordinary prose usage — it is a real user-facing
        spelling.

        The reason is positive: ``--dangerously-bypass-approvals-and-sandbox``
        IS in ``--help`` with an unambiguous description, so the argv kanibako
        emits says what it does to anyone reading a log or a process list. This
        test pins the BEHAVIOUR (which is unchanged); only its justification
        was wrong.
        """
        from kanibako.plugins.codex.target import CodexTarget

        desc = CodexTarget().descriptor
        for tier in ("restricted", "editing", "full"):
            argv = self._argv_at(desc, persisted=tier)
            assert "--full-auto" not in argv
            assert "--yolo" not in argv

    def test_goose_env_per_tier(self):
        from kanibako.plugins.goose.target import GooseTarget

        desc = GooseTarget().descriptor

        def _mode(*, secure=False, autonomous=False, persisted=None):
            tier = _launch_tier(
                desc, secure=secure, autonomous=autonomous, persisted=persisted,
            )
            return assembly.assemble_env(
                desc, access=tier, setting_values={}, agent="goose",
            )["GOOSE_MODE"]

        assert _mode(persisted="restricted") == "approve"
        assert _mode(persisted="full") == "auto"
        # unset -> the ``full`` default
        assert _mode(persisted=None) == "auto"
        # -A overrides a stored restricted; -S overrides a stored full.
        assert _mode(autonomous=True, persisted="restricted") == "auto"
        assert _mode(secure=True, persisted="full") == "approve"

    def test_goose_editing_is_REFUSED_never_substituted(self):
        """The B7b goose ruling, on the ENV consumer.

        ⚑ Non-vacuous in the dangerous direction: goose's UNSET GOOSE_MODE is
        ``auto``, so a fall-through (emitting nothing) would deliver the FULL
        bypass under the name ``editing``.
        """
        from kanibako.plugins.goose.target import GooseTarget

        desc = GooseTarget().descriptor
        with pytest.raises(ConfigError) as exc:
            assembly.assemble_env(
                desc, access="editing", setting_values={}, agent="goose",
            )
        msg = str(exc.value)
        assert "editing" in msg and "goose" in msg
        assert "restricted | full" in msg

    def test_only_goose_lacks_the_editing_row(self):
        """The refusal is goose-SPECIFIC, not a blanket middle-tier hole."""
        from kanibako.plugins.codex.target import CodexTarget
        from kanibako.plugins.goose.target import GooseTarget

        assert ClaudeTarget().descriptor.safe_bypass.rendered_tiers() == (
            "restricted", "editing", "full",
        )
        assert CodexTarget().descriptor.safe_bypass.rendered_tiers() == (
            "restricted", "editing", "full",
        )
        assert GooseTarget().descriptor.safe_bypass.rendered_tiers() == (
            "restricted", "full",
        )


# --------------------------------------------------------------------------- #
# container env assembly                                                       #
# --------------------------------------------------------------------------- #


class TestDescriptorEnv:
    def setup_method(self):
        self.desc = ClaudeTarget().descriptor
        assert self.desc is not None

    def test_carries_autoupdater_and_telemetry(self):
        env = assembly.assemble_env(
            self.desc, access="full", setting_values=DEFAULT_STATE,
        )
        assert env["DISABLE_AUTOUPDATER"] == "1"
        assert env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "1"

    def test_env_present_in_secure_mode_too(self):
        env = assembly.assemble_env(
            self.desc, access="restricted", setting_values=DEFAULT_STATE,
        )
        assert env["DISABLE_AUTOUPDATER"] == "1"
        assert env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "1"

    def test_env_superset_of_legacy_apply_state(self):
        """Descriptor env carries every key legacy apply_state set (DISABLE_AUTOUPDATER)
        plus the telemetry var that core start.py used to inject."""
        target = ClaudeTarget()
        _, legacy_env = target.apply_state(DEFAULT_STATE)
        new_env = assembly.assemble_env(
            target.descriptor, access="full", setting_values=DEFAULT_STATE,
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
        """descriptor_mounts delivers share + launcher + the loader (ro), skips plugins.

        Replaces the former equivalence-with-``binary_mounts`` check (the legacy
        hook was removed for the descriptor-only public release).  Adds the
        best-effort kickoff-loader SEED (shipped source exists).
        """
        target = ClaudeTarget()
        install = self._install(tmp_path)

        new = descriptor_mounts(
            target.descriptor, install,
        )

        # Deliver the share (install_dir) + launcher + kickoff SEED, ro; SKIP plugins.
        assert len(new) == 3
        assert all(m.options == "ro" for m in new)
        dests = {m.destination for m in new}
        assert dests == {
            "/home/agent/.local/share/claude",
            "/home/agent/.local/bin/claude",
            "/home/agent/.config/kanibako/kickoff.md",
        }

    def test_plugins_not_a_descriptor_binding(self, tmp_path):
        """Part 3a: plugins is an ``agent.<agent>.common`` category entry, not a binding.

        The descriptor's delivery binds no longer include plugins (or cache);
        those flow through the category resolver from ``default_common()``.
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
