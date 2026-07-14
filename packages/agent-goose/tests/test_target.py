"""Tests for GooseTarget."""

from __future__ import annotations

import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from kanibako.plugins.goose import GooseTarget
from kanibako.targets import assembly
from kanibako.targets.base import (
    AgentInstall,
    BindKind,
    BindScope,
    Cadence,
    Channel,
    CredFileSpec,
    HostSrcOrigin,
    PluginDescriptor,
)


class TestProperties:
    def test_name(self):
        assert GooseTarget().name == "goose"

    def test_display_name(self):
        assert GooseTarget().display_name == "Goose"

    def test_config_dir_name(self):
        assert GooseTarget().config_dir_name == ".config/goose"


class TestHasResumableSession:
    """First-launch death-race fix: goose positively reports whether its
    session store (``<home>/.local/share/goose/sessions``, pre-created empty
    by the descriptor's init_dirs) holds anything to resume, so start.py can
    skip the DOOMED default ``session --resume`` on a fresh box."""

    def _store(self, home: Path) -> Path:
        sessions = home / ".local" / "share" / "goose" / "sessions"
        sessions.mkdir(parents=True)
        return sessions

    def test_false_when_store_missing(self, tmp_path: Path):
        # No session store at all -> positively nothing to resume.
        assert GooseTarget().has_resumable_session(tmp_path) is False

    def test_false_when_store_empty(self, tmp_path: Path):
        # The first-launch shape: init_dirs pre-created the dir, goose has
        # never written a session -> positively nothing to resume.  (goose
        # 1.37 creates sessions.db only when it RUNS — including on a failed
        # resume — so a truly untouched box is exactly the empty dir.)
        self._store(tmp_path)
        assert GooseTarget().has_resumable_session(tmp_path) is False

    def test_true_when_session_db_present(self, tmp_path: Path):
        # goose 1.37 store layout: a sqlite db inside the sessions dir.  Any
        # db — even one an earlier FAILED resume created empty — reads True.
        sessions = self._store(tmp_path)
        (sessions / "sessions.db").write_bytes(b"SQLite format 3\x00")
        assert GooseTarget().has_resumable_session(tmp_path) is True

    def test_true_on_any_entry_fail_safe(self, tmp_path: Path):
        # ANY entry counts (not just *.jsonl): a future goose storage-format
        # change must err toward attempting the resume, never denying it.
        sessions = self._store(tmp_path)
        (sessions / "some-subdir").mkdir()
        assert GooseTarget().has_resumable_session(tmp_path) is True

    def test_true_when_store_unreadable_fail_safe(self, tmp_path: Path, monkeypatch):
        # Unreadable store -> unsure -> True (never wrongly deny a real resume).
        self._store(tmp_path)

        def _raise(self):
            raise PermissionError(13, "Permission denied")

        monkeypatch.setattr(Path, "iterdir", _raise)
        assert GooseTarget().has_resumable_session(tmp_path) is True


class TestSetupCommand:
    """In-box setup command (``goose configure``) declared via the base hooks.

    start.py runs this interactively in the box when the pre-launch check_auth
    probe fails (goose unconfigured).
    """

    def test_setup_entrypoint_is_goose_configure(self):
        t = GooseTarget()
        assert t.setup_entrypoint == "goose"
        assert t.setup_args == ["configure"]


class TestShouldRunSetup:
    """Post-launch matcher: launch logs prove the config did NOT take."""

    def test_true_on_not_configured_line(self):
        # goose's verbatim launch stderr when unconfigured.
        assert GooseTarget().should_run_setup(
            "Goose is not configured. Run 'goose configure' to set up."
        )

    def test_case_insensitive(self):
        assert GooseTarget().should_run_setup("GOOSE IS NOT CONFIGURED.")

    def test_true_on_configure_hint_alone(self):
        assert GooseTarget().should_run_setup(
            "please run 'goose configure' first"
        )

    def test_false_on_unrelated_output(self):
        assert not GooseTarget().should_run_setup("goose session started")

    def test_false_on_empty_output(self):
        assert not GooseTarget().should_run_setup("")


def _anchor_contract(monkeypatch, binary):
    """Point the goose plugin's contract constant at a tmp binary.

    detect / check_auth anchor to ``_BINARY`` (the contract path
    ~/.local/bin/goose) instead of ``shutil.which`` — tests override that module
    constant rather than mocking PATH.
    """
    import kanibako.plugins.goose.target as goose_mod
    monkeypatch.setattr(goose_mod, "_BINARY", Path(binary))


class TestDetect:
    def test_found(self, tmp_path: Path, monkeypatch):
        """Detect anchors to the contract path (no shutil.which)."""
        binary = tmp_path / "goose"
        binary.write_text("#!/bin/sh\n")
        binary.chmod(0o755)
        _anchor_contract(monkeypatch, binary)

        with patch.object(shutil, "which") as m_which:
            result = GooseTarget().detect()
        m_which.assert_not_called()

        assert result is not None
        assert result.name == "goose"
        assert result.binary == binary.resolve()
        assert result.install_dir == binary.resolve().parent
        assert result.launcher is None

    def test_not_found(self, tmp_path: Path, monkeypatch):
        """Detect returns None when the contract path is absent."""
        _anchor_contract(monkeypatch, tmp_path / "goose")  # never created
        with patch.object(shutil, "which") as m_which:
            assert GooseTarget().detect() is None
        m_which.assert_not_called()

    def test_dangling_symlink_still_installed(self, tmp_path: Path, monkeypatch):
        """A present-but-dangling symlink still counts as installed."""
        target_path = tmp_path / "gone"  # does not exist
        link = tmp_path / "goose"
        link.symlink_to(target_path)  # dangling
        _anchor_contract(monkeypatch, link)

        result = GooseTarget().detect()
        assert result is not None
        assert result.name == "goose"


class TestDescriptorDeliveryMounts:
    """goose's binary bind comes from its descriptor (the legacy ``binary_mounts``
    hook was removed for the descriptor-only public release); core builds it via
    ``descriptor_mounts``."""

    def test_single_mount(self, tmp_path: Path):
        from kanibako.targets.assembly import descriptor_mounts

        binary = tmp_path / "goose"
        binary.write_text("#!/bin/sh\n")
        binary.chmod(0o755)

        install = AgentInstall(name="goose", binary=binary, install_dir=tmp_path)
        mounts = descriptor_mounts(
            GooseTarget().descriptor, install,
        )

        # Two mounts now: the binary + the instruction-delivery kickoff-loader
        # SEED (``managed_pointer``, RO to ~/.config/kanibako/kickoff.md).
        assert len(mounts) == 2
        by_dest = {m.destination: m for m in mounts}
        binary_mount = by_dest["/home/agent/.local/bin/goose"]
        assert binary_mount.source == binary
        assert binary_mount.options == "ro"
        kickoff = by_dest["/home/agent/.config/kanibako/kickoff.md"]
        assert str(kickoff.source).endswith(
            "data/KICKOFF.md"
        )
        assert kickoff.options == "ro"

    def test_safe_fails_when_binary_missing(self, tmp_path: Path):
        from kanibako.targets.assembly import BindingSourceError, descriptor_mounts

        binary = tmp_path / "goose"  # does not exist

        install = AgentInstall(name="goose", binary=binary, install_dir=tmp_path)
        with pytest.raises(BindingSourceError):
            descriptor_mounts(
                GooseTarget().descriptor, install,
            )


class TestCredentialCheckPath:
    def test_returns_correct_path(self, tmp_path: Path):
        result = GooseTarget().credential_check_path(tmp_path)
        assert result == tmp_path / ".config" / "goose" / "secrets.yaml"


class TestInvalidateCredentials:
    def test_deletes_secrets(self, tmp_path: Path):
        secrets = tmp_path / ".config" / "goose" / "secrets.yaml"
        secrets.parent.mkdir(parents=True)
        secrets.write_text("data\n")

        GooseTarget().invalidate_credentials(tmp_path)

        assert not secrets.exists()

    def test_noop_when_missing(self, tmp_path: Path):
        # Should not raise
        GooseTarget().invalidate_credentials(tmp_path)


class TestCredsyncFold:
    """secrets.yaml host<->box sync is the descriptor CredFileSpec, NOT a bespoke
    per-plugin refresh/writeback override.

    The former ``refresh_secrets`` / ``writeback_secrets`` copies were folded into
    the goose descriptor's ``secrets.yaml`` ``CredFileSpec`` (realized by the
    credsync engine — covered by ``TestCredFiles`` / the engine-level tests).
    goose must therefore NOT override the legacy ``refresh_credentials`` /
    ``writeback_credentials`` hooks: those are reached only on the ``desc is None``
    legacy path, which never holds for descriptor-bearing goose, so an override
    would be an unsanctioned second route.
    """

    def test_does_not_override_legacy_refresh(self):
        from kanibako.targets.base import Target

        assert (
            GooseTarget().refresh_credentials.__func__
            is Target.refresh_credentials
        )

    def test_does_not_override_legacy_writeback(self):
        from kanibako.targets.base import Target

        assert (
            GooseTarget().writeback_credentials.__func__
            is Target.writeback_credentials
        )


def _real_binary(tmp_path):
    """Create a real (existing) contract binary file under tmp_path."""
    binary = tmp_path / "goose"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    return binary


class TestCheckAuth:
    def test_returns_true_when_both_exist(self, tmp_path: Path, fake_host: Path, monkeypatch):
        monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_host))
        _anchor_contract(monkeypatch, _real_binary(tmp_path))

        config = fake_host / ".config" / "goose" / "config.yaml"
        config.write_text("provider: anthropic\n")
        secrets = fake_host / ".config" / "goose" / "secrets.yaml"
        secrets.write_text("key: secret\n")

        with patch.object(shutil, "which") as m_which:
            assert GooseTarget().check_auth() is True
        m_which.assert_not_called()

    def test_returns_false_when_secrets_missing(self, tmp_path: Path, fake_host: Path, monkeypatch):
        monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_host))
        _anchor_contract(monkeypatch, _real_binary(tmp_path))

        config = fake_host / ".config" / "goose" / "config.yaml"
        config.write_text("provider: anthropic\n")
        # No secrets file

        assert GooseTarget().check_auth() is False

    def test_returns_true_when_binary_not_found(self, tmp_path: Path, monkeypatch):
        """No contract binary -> True (defers to later warnings), no which."""
        _anchor_contract(monkeypatch, tmp_path / "goose")  # never created
        with patch.object(shutil, "which") as m_which:
            assert GooseTarget().check_auth() is True
        m_which.assert_not_called()

    def test_returns_false_when_config_missing(self, tmp_path: Path, fake_host: Path, monkeypatch):
        """The bug condition: secrets present but host config.yaml absent -> False.

        Before the config-persistence fix this was the steady state after every
        in-box ``goose configure`` (config.yaml lived only in the box), so the
        host check_auth re-failed and kanibako re-ran setup on each start.
        """
        monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_host))
        _anchor_contract(monkeypatch, _real_binary(tmp_path))

        secrets = fake_host / ".config" / "goose" / "secrets.yaml"
        secrets.write_text("key: secret\n")
        # No config.yaml on the host.

        assert GooseTarget().check_auth() is False

    def test_loop_closes_after_config_written_back(self, tmp_path: Path, fake_host: Path, monkeypatch):
        """End-to-end of the fix: once writeback lands config.yaml on the host (in
        addition to the already-synced secrets.yaml), the NEXT-start check_auth
        passes -> no re-prompt.  This simulates writeback_cred_files having mirrored
        the box's config.yaml back to the host.
        """
        monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_host))
        _anchor_contract(monkeypatch, _real_binary(tmp_path))

        # secrets.yaml is synced today; before the fix config.yaml was not.
        (fake_host / ".config" / "goose" / "secrets.yaml").write_text("key: secret\n")
        assert GooseTarget().check_auth() is False  # bug state: config absent

        # writeback now mirrors the box's config.yaml to the host.
        (fake_host / ".config" / "goose" / "config.yaml").write_text(
            "GOOSE_PROVIDER: custom_navigator\nGOOSE_MODEL: gemma-4-31b-it\n"
        )
        assert GooseTarget().check_auth() is True  # fixed: no re-prompt


class TestGenerateAgentConfig:
    def test_returns_empty_state_no_pinned_provider_model(self):
        # kanibako must NOT pin goose's provider/model: an agent-level state would
        # win the resolver cascade and force GOOSE_PROVIDER/GOOSE_MODEL, which
        # override the user's in-box ``goose configure`` choice.  State is empty.
        config = GooseTarget().generate_agent_config()
        assert config.name == "Goose"
        assert config.state == {}
        assert "provider" not in config.state
        assert "model" not in config.state


class TestApplyState:
    def test_provider_env_var(self):
        cli_args, env_vars = GooseTarget().apply_state({"provider": "openai"})
        assert cli_args == []
        assert env_vars["GOOSE_PROVIDER"] == "openai"

    def test_model_env_var(self):
        cli_args, env_vars = GooseTarget().apply_state({"model": "gpt-4"})
        assert cli_args == []
        assert env_vars["GOOSE_MODEL"] == "gpt-4"

    def test_empty_state_no_vars(self):
        cli_args, env_vars = GooseTarget().apply_state({})
        assert cli_args == []
        assert env_vars == {}


class TestSettingDescriptors:
    def test_returns_provider_model_and_endpoint(self):
        settings = GooseTarget().setting_descriptors()
        keys = [s.key for s in settings]
        assert "provider" in keys
        assert "model" in keys
        # endpoint (persona): first-class SETTABLE key mirroring claude/codex.
        assert "endpoint" in keys
        assert len(settings) == 3

    def test_provider_and_model_have_no_default(self):
        # The keys stay declared/settable, but with EMPTY defaults so the
        # resolver floor yields "" (omitted by assemble_env) when unset — goose
        # then reads provider/model from its own config.yaml (goose configure).
        settings = {s.key: s for s in GooseTarget().setting_descriptors()}
        assert settings["provider"].default == ""
        assert settings["model"].default == ""
        # endpoint too: unset = bare/harness-default (no persona).
        assert settings["endpoint"].default == ""


class TestDefaultShares:
    """Part 3b: the resource_mappings abstraction was deleted (all PROJECT —
    those dirs live in the box home bind).  goose declares no agent shares."""

    def test_no_default_shares(self):
        assert GooseTarget().default_shares() == {}


class TestDescriptor:
    """The declarative PluginDescriptor that puts goose on the generic launch path."""

    def test_is_plugin_descriptor(self):
        assert isinstance(GooseTarget().descriptor, PluginDescriptor)

    def test_command(self):
        assert GooseTarget().descriptor.command == ("goose",)

    def test_vscode_extension_id(self):
        # FF-4: Block's official ACP extension id (Marketplace-only, not on
        # Open VSX); auto-installed on `kanibako code` attach.
        assert GooseTarget().descriptor.vscode_extension == "block.vscode-goose"

    def test_binary_binding(self):
        d = GooseTarget().descriptor
        bindings = {b.key: b for b in d.bindings}
        # ``managed_pointer`` is the instruction-delivery kickoff-loader SEED
        # (delivered RO to ~/.config/kanibako/kickoff.md) added alongside the
        # binary bind.
        assert set(bindings) == {"binary", "managed_pointer"}
        binary = bindings["binary"]
        assert binary.origin == HostSrcOrigin.BINARY
        assert binary.box_dest == "/home/agent/.local/bin/goose"
        assert binary.kind == BindKind.FILE
        assert binary.scope == BindScope.AGENT_CRITICAL
        assert binary.ro is True

    def test_mode_uses_bare_session(self):
        """goose 1.37.0: bare `session` / `session --resume` (NO start/resume subcmds)."""
        d = GooseTarget().descriptor
        assert d.mode["start"] == ("session",)
        assert d.mode["continue"] == ("session", "--resume")
        # The removed-subcommand grammar must NOT appear anywhere.
        assert "resume" not in d.mode

    def test_operations_exec(self):
        d = GooseTarget().descriptor
        assert "exec" in d.operations
        assert d.operations["exec"].fragment == ("run", "--no-session", "-t")

    def test_safe_bypass_env_goose_mode(self):
        """Safe-bypass is symmetric ENV GOOSE_MODE (NO --approve-all flag in 1.37.0).

        safe-OFF/-A -> ``auto``; safe-ON/-S -> ``approve``.  The secure value is
        MANDATORY because goose's unset GOOSE_MODE default is itself ``auto``
        (the A1 fix).
        """
        sb = GooseTarget().descriptor.safe_bypass
        assert sb is not None
        assert sb.channel == Channel.ENV
        assert sb.env_var == "GOOSE_MODE"
        assert sb.env_value == "auto"
        assert sb.secure_env_value == "approve"
        assert sb.flag == ()
        assert sb.secure_flag == ()
        # goose persists auto_approve uniformly: the persisted default is redeemed
        # via setting_key="auto_approve" (spec §2d L655).
        assert sb.setting_key == "auto_approve"

    def test_settings_model_provider_and_endpoint_env(self):
        d = GooseTarget().descriptor
        settings = {s.setting_key: s for s in d.settings}
        assert set(settings) == {"model", "provider", "endpoint"}
        assert settings["model"].channel == Channel.ENV
        assert settings["model"].env_var == "GOOSE_MODEL"
        assert settings["provider"].channel == Channel.ENV
        assert settings["provider"].env_var == "GOOSE_PROVIDER"
        # endpoint (persona): ENV channel -> OPENAI_HOST (goose's built-in openai
        # provider reads it), mirroring claude's endpoint->ANTHROPIC_BASE_URL.
        assert settings["endpoint"].channel == Channel.ENV
        assert settings["endpoint"].env_var == "OPENAI_HOST"

    def test_persona_spec_env_delivery_openai(self):
        # INC G1: goose persona = ENV delivery, OPENAI_API_KEY token var, NO claude
        # host-dir adopt, GOOSE_PROVIDER auto-pin (via provider setting), model gate.
        p = GooseTarget().descriptor.persona
        assert p is not None
        assert p.endpoint_delivery == "env"
        assert p.token_var == "OPENAI_API_KEY"
        assert p.host_dir_adopt is False
        assert p.model_required is True
        assert p.provider_pin == (("provider", "openai"),)

    def test_container_env_disables_keyring(self):
        # GOOSE_DISABLE_KEYRING is ALWAYS set for goose boxes (static, not a
        # setting): the in-box OS keyring/D-Bus secret-service is unavailable, so
        # goose must store secrets in the file ~/.config/goose/secrets.yaml.
        # CONTEXT_FILE_NAMES (STEP 2a) now lists the instruction-delivery FINAL
        # slot ``.additionalContext.md`` (the flattened per-agent guide the
        # box-start flattener writes) alongside the default context filenames;
        # KANIBAKO_DIRECTIVE_FINAL names that slot for the flattener.
        assert GooseTarget().descriptor.container_env == {
            "GOOSE_DISABLE_KEYRING": "true",
            "CONTEXT_FILE_NAMES": '[".additionalContext.md","AGENTS.md",".goosehints"]',
            "KANIBAKO_DIRECTIVE_FINAL": "/home/agent/.config/goose/.additionalContext.md",
        }

    def test_cred_files(self):
        # The old host config.yaml IMPORT (SEED_ONCE, filtered) stays removed.
        # As of the 2026-06-24 config-persistence fix, goose two-way SYNCs three
        # things: secrets.yaml + config.yaml (files) and custom_providers/ (dir),
        # so in-box ``goose configure`` persists back to the host (host check_auth
        # then passes -> no re-prompt).  secrets.yaml + custom_providers/ are
        # unfiltered; config.yaml is FILTERED (D Part 4) so the box-local
        # panel-parity GOOSE_MODE is not written back over the host's own.
        d = GooseTarget().descriptor
        specs = {s.home_rel: s for s in d.cred_files}
        assert set(specs) == {
            ".config/goose/secrets.yaml",
            ".config/goose/config.yaml",
            ".config/goose/custom_providers",
        }

        secrets = specs[".config/goose/secrets.yaml"]
        assert secrets.host_rel == ".config/goose/secrets.yaml"
        assert secrets.cadence == Cadence.SYNC
        assert secrets.mtime_gate is True
        assert secrets.filtered is False
        assert secrets.is_dir is False

        config = specs[".config/goose/config.yaml"]
        assert config.host_rel == ".config/goose/config.yaml"
        assert config.cadence == Cadence.SYNC
        assert config.filtered is True
        assert config.is_dir is False

        providers = specs[".config/goose/custom_providers"]
        assert providers.host_rel == ".config/goose/custom_providers"
        assert providers.cadence == Cadence.SYNC
        assert providers.filtered is False
        assert providers.is_dir is True

    def test_host_prep_and_init_dirs(self):
        d = GooseTarget().descriptor
        assert d.host_prep is False
        assert d.init_dirs == (".config/goose", ".local/share/goose/sessions")


class TestTransformCred:
    """transform_cred — goose FILTERS config.yaml (D Part 4), plain-copies the rest.

    The host config.yaml IMPORT (the extensions/instructions allowlist filter) stays
    removed, but goose now overrides transform_cred for ONE reason: config.yaml is a
    two-way SYNC file, and the box seeds a PANEL-parity ``GOOSE_MODE`` into it (for the
    VS Code panel's yolo).  That box-local value must NOT be written back over the
    host's own GOOSE_MODE.  A non-config.yaml filtered spec still plain-copies.
    """

    _SECRETS_SPEC = CredFileSpec(
        ".config/goose/secrets.yaml", ".config/goose/secrets.yaml",
        cadence=Cadence.SYNC, mtime_gate=True, filtered=False,
    )
    _CONFIG_SPEC = CredFileSpec(
        ".config/goose/config.yaml", ".config/goose/config.yaml",
        cadence=Cadence.SYNC, mtime_gate=True, filtered=True,
    )

    def test_non_config_spec_plain_copies(self, tmp_path):
        """A non-config.yaml filtered spec still gets a plain wholesale copy."""
        src = tmp_path / "secrets.yaml"
        src.write_text("api_key: secret123\n")
        dst = tmp_path / "home" / ".config" / "goose" / "secrets.yaml"

        GooseTarget().transform_cred(self._SECRETS_SPEC, src, dst, "in")

        assert dst.is_file()
        assert dst.read_text() == "api_key: secret123\n"

    def test_config_in_wholesale_copies(self, tmp_path):
        """direction 'in' (host->box): config.yaml is copied verbatim."""
        src = tmp_path / "config.yaml"
        src.write_text("GOOSE_PROVIDER: openai\nGOOSE_MODE: approve\n")
        dst = tmp_path / "box" / ".config" / "goose" / "config.yaml"

        GooseTarget().transform_cred(self._CONFIG_SPEC, src, dst, "in")

        import yaml
        got = yaml.safe_load(dst.read_text())
        assert got == {"GOOSE_PROVIDER": "openai", "GOOSE_MODE": "approve"}

    def test_config_out_preserves_host_goose_mode(self, tmp_path):
        """direction 'out' (writeback): the HOST's GOOSE_MODE is preserved.

        Mutation-proof: the box has a DIFFERENT (panel-parity) GOOSE_MODE plus a real
        user change (provider); writeback must carry the provider change but keep the
        host's own GOOSE_MODE untouched.
        """
        import yaml
        box = tmp_path / "config.yaml"
        box.write_text("GOOSE_PROVIDER: anthropic\nGOOSE_MODE: auto\n")  # box: auto (panel yolo)
        host = tmp_path / "host" / ".config" / "goose" / "config.yaml"
        host.parent.mkdir(parents=True)
        host.write_text("GOOSE_PROVIDER: openai\nGOOSE_MODE: approve\n")  # host: approve

        GooseTarget().transform_cred(self._CONFIG_SPEC, box, host, "out")

        got = yaml.safe_load(host.read_text())
        assert got["GOOSE_MODE"] == "approve"       # host's OWN mode preserved
        assert got["GOOSE_PROVIDER"] == "anthropic"  # real user change still flowed

    def test_config_out_drops_box_mode_when_host_has_none(self, tmp_path):
        """direction 'out': when the host has NO GOOSE_MODE, the box-local one is dropped."""
        import yaml
        box = tmp_path / "config.yaml"
        box.write_text("GOOSE_PROVIDER: anthropic\nGOOSE_MODE: auto\n")
        host = tmp_path / "host" / ".config" / "goose" / "config.yaml"
        host.parent.mkdir(parents=True)
        host.write_text("GOOSE_PROVIDER: openai\n")  # no GOOSE_MODE

        GooseTarget().transform_cred(self._CONFIG_SPEC, box, host, "out")

        got = yaml.safe_load(host.read_text())
        assert "GOOSE_MODE" not in got               # box-local mode NOT introduced
        assert got["GOOSE_PROVIDER"] == "anthropic"

    def test_config_out_noop_when_only_goose_mode_differs(self, tmp_path):
        """direction 'out': if only the box-local GOOSE_MODE differs, host is untouched."""
        box = tmp_path / "config.yaml"
        box.write_text("GOOSE_PROVIDER: openai\nGOOSE_MODE: auto\n")   # box: auto
        host = tmp_path / "host" / ".config" / "goose" / "config.yaml"
        host.parent.mkdir(parents=True)
        original = "GOOSE_PROVIDER: openai\nGOOSE_MODE: approve\n"     # host: approve
        host.write_text(original)

        GooseTarget().transform_cred(self._CONFIG_SPEC, box, host, "out")

        assert host.read_text() == original          # byte-identical: true no-op

    def test_config_out_malformed_box_does_not_clobber_host(self, tmp_path):
        """direction 'out': an unparseable box config.yaml must NOT wipe the host file."""
        box = tmp_path / "config.yaml"
        box.write_text("this: is: not: valid: yaml\n  - broken")  # read_yaml -> {}
        host = tmp_path / "host" / ".config" / "goose" / "config.yaml"
        host.parent.mkdir(parents=True)
        original = "GOOSE_PROVIDER: openai\nGOOSE_MODE: approve\n"
        host.write_text(original)

        GooseTarget().transform_cred(self._CONFIG_SPEC, box, host, "out")

        assert host.read_text() == original          # host preserved, not truncated


class TestDescriptorAssembly:
    """Integration: goose's argv/env assembled from the descriptor via assembly.*."""

    def _argv(self, *, resume_mode=False, safe_off=True, state=None, extra_args=None):
        d = GooseTarget().descriptor
        mode_key = assembly.resolve_mode(
            resume_mode=resume_mode,
            new_session=False,
            is_new_project=False,
            extra_args=extra_args or [],
            available_modes=d.mode.keys(),
        )
        return assembly.assemble_argv(
            d,
            mode_key=mode_key,
            safe_mode_off=safe_off,
            setting_values=state or {},
            op=None,
            extra_args=extra_args or [],
        )

    def test_default_argv_is_continue(self):
        """Default launch (no new-session forcing) resolves to continue-last.

        With ``continue`` available and nothing forcing a fresh session,
        resolve_mode -> 'continue' -> ['session', '--resume'].  GOOSE_MODE /
        --approve-all never appear in argv (the bypass is an env var).
        """
        argv = self._argv()
        assert argv == ["session", "--resume"]
        assert "GOOSE_MODE" not in argv
        assert "--approve-all" not in argv

    def test_start_mode_is_bare_session(self):
        """Explicit start mode -> bare ['session']."""
        d = GooseTarget().descriptor
        argv = assembly.assemble_argv(
            d, mode_key="start", safe_mode_off=True,
            setting_values={}, op=None, extra_args=[],
        )
        assert argv == ["session"]

    def test_continue_argv(self):
        d = GooseTarget().descriptor
        argv = assembly.assemble_argv(
            d, mode_key="continue", safe_mode_off=True,
            setting_values={}, op=None, extra_args=[],
        )
        assert argv == ["session", "--resume"]

    def test_new_session_forced_is_bare_session(self):
        d = GooseTarget().descriptor
        mode_key = assembly.resolve_mode(
            resume_mode=False, new_session=True, is_new_project=False,
            extra_args=[], available_modes=d.mode.keys(),
        )
        argv = assembly.assemble_argv(
            d, mode_key=mode_key, safe_mode_off=True,
            setting_values={}, op=None, extra_args=[],
        )
        assert argv == ["session"]

    def test_exec_op_argv(self):
        d = GooseTarget().descriptor
        argv = assembly.assemble_argv(
            d, mode_key="start", safe_mode_off=True,
            setting_values={}, op="exec", extra_args=["do the thing"],
        )
        assert argv == ["run", "--no-session", "-t", "do the thing"]

    def test_env_safe_off_sets_goose_mode_auto(self):
        d = GooseTarget().descriptor
        env = assembly.assemble_env(d, safe_mode_off=True, setting_values={})
        assert env["GOOSE_MODE"] == "auto"

    def test_env_safe_on_sets_goose_mode_approve(self):
        """The A1 fix: -S/secure emits GOOSE_MODE=approve (NOT nothing).

        goose's unset GOOSE_MODE default is ``auto`` (tools auto-run), so -S must
        emit a restrictive value or it would not actually be safe.
        """
        d = GooseTarget().descriptor
        env = assembly.assemble_env(d, safe_mode_off=False, setting_values={})
        assert env["GOOSE_MODE"] == "approve"

    def test_env_model_and_provider_from_settings(self):
        d = GooseTarget().descriptor
        env = assembly.assemble_env(
            d, safe_mode_off=True,
            setting_values={"model": "claude-4", "provider": "anthropic"},
        )
        assert env["GOOSE_MODEL"] == "claude-4"
        assert env["GOOSE_PROVIDER"] == "anthropic"

    def test_env_omits_provider_model_when_unset(self):
        """No provider/model setting -> GOOSE_PROVIDER/GOOSE_MODEL NOT emitted.

        This is the in-box-``goose configure`` fix: kanibako must not force the
        provider/model env vars (they override goose's config.yaml).  With no
        setting, assemble_env (``if value:``) omits both — but GOOSE_MODE and
        GOOSE_DISABLE_KEYRING still emit.
        """
        d = GooseTarget().descriptor
        env = assembly.assemble_env(d, safe_mode_off=True, setting_values={})
        assert "GOOSE_PROVIDER" not in env
        assert "GOOSE_MODEL" not in env
        assert env["GOOSE_MODE"] == "auto"
        assert env["GOOSE_DISABLE_KEYRING"] == "true"

    def test_env_omits_provider_model_when_empty_string(self):
        """Empty-string values (the resolver floor for the unset case) are
        falsy and so are omitted, exactly like a missing key."""
        d = GooseTarget().descriptor
        env = assembly.assemble_env(
            d, safe_mode_off=True,
            setting_values={"model": "", "provider": ""},
        )
        assert "GOOSE_PROVIDER" not in env
        assert "GOOSE_MODEL" not in env
        assert env["GOOSE_DISABLE_KEYRING"] == "true"

    def test_env_always_disables_keyring(self):
        """GOOSE_DISABLE_KEYRING=true is in the assembled box env unconditionally.

        It is a static container_env constant, so it survives every assembly path
        regardless of safe-mode or settings (in-box keyring unavailable -> goose
        falls back to file-based ~/.config/goose/secrets.yaml).
        """
        d = GooseTarget().descriptor
        for safe_off in (True, False):
            env = assembly.assemble_env(d, safe_mode_off=safe_off, setting_values={})
            assert env["GOOSE_DISABLE_KEYRING"] == "true"
        env = assembly.assemble_env(
            d, safe_mode_off=True,
            setting_values={"model": "claude-4", "provider": "anthropic"},
        )
        assert env["GOOSE_DISABLE_KEYRING"] == "true"
