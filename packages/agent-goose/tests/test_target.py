"""Tests for GooseTarget."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from kanibako.plugins.goose import GooseTarget
from kanibako.settings.settings_resolve import UNSET
from kanibako.targets import assembly
from kanibako.targets.base import (
  AgentInstall,
  BindKind,
  BindScope,
  Cadence,
  Channel,
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
    assert GooseTarget().should_run_setup("please run 'goose configure' first")

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
      GooseTarget().descriptor,
      install,
    )

    # Two mounts now: the binary + the instruction-delivery kickoff-loader
    # SEED (``managed_pointer``, RO to ~/.config/kanibako/kickoff.md).
    assert len(mounts) == 2
    by_dest = {m.destination: m for m in mounts}
    binary_mount = by_dest["/home/agent/.local/bin/goose"]
    assert binary_mount.source == binary
    assert binary_mount.options == "ro"
    kickoff = by_dest["/home/agent/.config/kanibako/kickoff.md"]
    assert str(kickoff.source).endswith("data/KICKOFF.md")
    assert kickoff.options == "ro"

  def test_safe_fails_when_binary_missing(self, tmp_path: Path):
    from kanibako.targets.assembly import BindingSourceError, descriptor_mounts

    binary = tmp_path / "goose"  # does not exist

    install = AgentInstall(name="goose", binary=binary, install_dir=tmp_path)
    with pytest.raises(BindingSourceError):
      descriptor_mounts(
        GooseTarget().descriptor,
        install,
      )


class TestCredentialCheckPath:
  def test_returns_correct_path(self, tmp_path: Path):
    result = GooseTarget().credential_check_path(tmp_path)
    assert result == tmp_path / ".config" / "goose" / "secrets.yaml"


def _real_binary(tmp_path):
  """Create a real (existing) contract binary file under tmp_path."""
  binary = tmp_path / "goose"
  binary.write_text("#!/bin/sh\n")
  binary.chmod(0o755)
  return binary


def _box(tmp_path: Path, *, secrets: str | None = None, config: str | None = None) -> Path:
  """A box home with goose's ``secrets.yaml`` and ``config.yaml`` as given."""
  box = tmp_path / "box"
  (box / ".config" / "goose").mkdir(parents=True)
  if secrets is not None:
    (box / ".config" / "goose" / "secrets.yaml").write_text(secrets)
  if config is not None:
    (box / ".config" / "goose" / "config.yaml").write_text(config)
  return box


# goose 1.38 writes ``active_provider`` (measured: a goose-written config.yaml); an
# older config names ``GOOSE_PROVIDER``.
_CONFIGURED = "active_provider: anthropic\nproviders:\n  anthropic:\n    configured: true\n"


class TestCheckAuth:
  @pytest.fixture(autouse=True)
  def _host(self, tmp_path: Path, fake_host: Path, monkeypatch):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_host))
    _anchor_contract(monkeypatch, _real_binary(tmp_path))
    self.host_secrets = fake_host / ".config" / "goose" / "secrets.yaml"

  def test_checks_the_box(self):
    assert GooseTarget().checks_box_login is True

  def test_host_check_passes_on_host_secrets_alone(self):
    """No host config.yaml is needed: nothing writes one back any more."""
    self.host_secrets.write_text("key: secret\n")
    with patch.object(shutil, "which") as m_which:
      assert GooseTarget().check_auth() is True
    m_which.assert_not_called()

  def test_host_check_fails_without_host_secrets(self):
    assert GooseTarget().check_auth() is False

  def test_box_login_and_box_provider_pass(self, tmp_path: Path):
    box = _box(tmp_path, secrets="key: secret\n", config=_CONFIGURED)
    assert GooseTarget().check_auth(box_home=box, delivered=frozenset()) is True

  def test_an_older_config_naming_GOOSE_PROVIDER_passes(self, tmp_path: Path):
    box = _box(tmp_path, secrets="key: secret\n", config="GOOSE_PROVIDER: openai\n")
    assert GooseTarget().check_auth(box_home=box, delivered=frozenset()) is True

  def test_a_login_without_a_provider_fails(self, tmp_path: Path):
    """A second box: the login synced, but its config.yaml is the template."""
    from importlib.resources import files

    template = (
      files("kanibako.plugins.goose")
      .joinpath(
        "data",
        "base",
        "template",
        "box",
        "home",
        ".config",
        "goose",
        "config.yaml",
      )
      .read_text()
    )
    self.host_secrets.write_text("key: secret\n")
    # A host config.yaml naming a provider (what the old writeback left) does not count.
    (self.host_secrets.parent / "config.yaml").write_text(_CONFIGURED)
    box = _box(tmp_path, secrets="key: secret\n", config=template)
    assert GooseTarget().check_auth(box_home=box, delivered=frozenset()) is False

  @pytest.mark.parametrize(
    "config",
    [
      "active_provider: ''\n",
      "GOOSE_PROVIDER: null\n",
      "active_provider: [a]\n",
      "- a list\n",
      "{",
    ],
  )
  def test_an_empty_or_malformed_provider_names_none(self, tmp_path: Path, config):
    box = _box(tmp_path, secrets="key: secret\n", config=config)
    assert GooseTarget().check_auth(box_home=box, delivered=frozenset()) is False

  def test_a_delivered_provider_passes_without_a_box_config(self, tmp_path: Path):
    box = _box(tmp_path, secrets="key: secret\n")
    assert (
      GooseTarget().check_auth(
        box_home=box,
        delivered=frozenset({"GOOSE_PROVIDER"}),
      )
      is True
    )

  def test_host_secrets_count_as_the_login(self, tmp_path: Path):
    """The refresh copies them in after the gate."""
    self.host_secrets.write_text("key: secret\n")
    box = _box(tmp_path, config=_CONFIGURED)
    assert GooseTarget().check_auth(box_home=box, delivered=frozenset()) is True

  def test_a_delivered_login_var_is_a_login(self, tmp_path: Path):
    box = _box(tmp_path)
    assert (
      GooseTarget().check_auth(
        box_home=box,
        delivered=frozenset({"GOOSE_PROVIDER", "ANTHROPIC_API_KEY"}),
      )
      is True
    )

  def test_no_login_fails_even_with_a_provider(self, tmp_path: Path):
    box = _box(tmp_path, config=_CONFIGURED)
    assert (
      GooseTarget().check_auth(
        box_home=box,
        delivered=frozenset({"GOOSE_PROVIDER"}),
      )
      is False
    )

  def test_an_empty_box_secrets_is_no_login(self, tmp_path: Path):
    box = _box(tmp_path, secrets="", config=_CONFIGURED)
    assert GooseTarget().check_auth(box_home=box, delivered=frozenset()) is False

  def test_a_planted_config_symlink_names_no_provider(self, tmp_path: Path):
    """The box config is read through boxfs: a link to a configured file fails."""
    planted = tmp_path / "elsewhere.yaml"
    planted.write_text(_CONFIGURED)
    box = _box(tmp_path, secrets="key: secret\n")
    (box / ".config" / "goose" / "config.yaml").symlink_to(planted)
    assert GooseTarget().check_auth(box_home=box, delivered=frozenset()) is False

  def test_an_oversized_box_config_is_not_read(self, tmp_path: Path):
    """The box writes its config.yaml; one past the cap names no provider."""
    box = _box(tmp_path, secrets="key: secret\n")
    big = _CONFIGURED + "# " + "x" * (2 << 20) + "\n"  # still names a provider
    (box / ".config" / "goose" / "config.yaml").write_text(big)
    assert GooseTarget().check_auth(box_home=box, delivered=frozenset()) is False

  def test_setup_in_the_box_closes_the_loop(self, tmp_path: Path):
    """``goose configure`` writes the box's config.yaml; the next start passes."""
    box = _box(tmp_path, secrets="key: secret\n")
    assert GooseTarget().check_auth(box_home=box, delivered=frozenset()) is False
    (box / ".config" / "goose" / "config.yaml").write_text(_CONFIGURED)
    assert GooseTarget().check_auth(box_home=box, delivered=frozenset()) is True

  def test_returns_true_when_binary_not_found(self, tmp_path: Path, monkeypatch):
    """No contract binary -> True (defers to later warnings), no which."""
    _anchor_contract(monkeypatch, tmp_path / "absent" / "goose")  # never created
    with patch.object(shutil, "which") as m_which:
      assert GooseTarget().check_auth() is True
      assert (
        GooseTarget().check_auth(
          box_home=_box(tmp_path),
          delivered=frozenset(),
        )
        is True
      )
    m_which.assert_not_called()


class TestGenerateAgentConfig:
  def test_returns_empty_state_no_pinned_provider_model(self):
    # kanibako must NOT pin goose's provider/model: an agent-level state would
    # win the resolver cascade and force GOOSE_PROVIDER/GOOSE_MODEL, which
    # override the user's in-box ``goose configure`` choice.  State is empty.
    config = GooseTarget().generate_agent_config()
    assert config.state == {}
    assert "provider" not in config.state
    assert "model" not in config.state

  def test_the_description_is_a_DECLARED_KEY_not_a_file_field(self):
    """D8b: ``AgentConfig`` has no ``name``; the description is ``agent.goose.label``.

    ⚑ NON-VACUITY: the string is the one spec §2d states, so a plugin that stopped
    declaring it would read the all-agents backstop, the agent's own name.
    """
    import dataclasses

    from kanibako.settings.agent_config import AgentConfig

    assert "name" not in {f.name for f in dataclasses.fields(AgentConfig)}
    label = next(d for d in GooseTarget().setting_descriptors() if d.key == "label")
    assert label.default == "Goose Harness"


# The retired ``apply_state`` hook translated provider/model into GOOSE_PROVIDER /
# GOOSE_MODEL.  That route lives in the descriptor's ``settings:`` ENV realizations
# now — pinned by ``TestDescriptorAssembly.test_env_model_and_provider_from_settings``
# and its two omission cases, which are what this class used to cover.


class TestSettingDescriptors:
  def test_returns_provider_model_and_endpoint(self):
    settings = GooseTarget().setting_descriptors()
    keys = [s.key for s in settings]
    assert "provider" in keys
    assert "model" in keys
    # endpoint (persona): first-class SETTABLE key mirroring claude/codex.
    assert "endpoint" in keys
    # ⚑ FOUR SINCE D8b: ``label`` joined them, declared in the same ``behavior:`` table.
    assert "label" in keys
    assert len(settings) == 4

  def test_provider_and_model_have_no_default(self):
    # The keys stay declared/settable, but set no value, so the launch omits
    # them when unset — goose then reads provider/model from its own
    # config.yaml (goose configure).  ``provider`` is goose's own key and says
    # <None> as ``null``; ``model``/``endpoint`` inherit ``agent.default``.
    settings = {s.key: s for s in GooseTarget().setting_descriptors()}
    assert settings["provider"].default is None
    assert settings["model"].default is UNSET
    # endpoint too: unset = bare/harness-default (no persona).
    assert settings["endpoint"].default is UNSET


class TestDefaultShares:
  """Part 3b: the resource_mappings abstraction was deleted (all PROJECT —
  those dirs live in the box home bind).  goose declares no agent chares."""

  def test_no_default_shares(self):
    assert GooseTarget().default_common() == {}


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

  def test_access_rows_env_goose_mode(self):
    """The ``access`` tier rows ride ENV GOOSE_MODE (goose has no permission
    CLI flag).

    ``full`` -> ``auto``; ``restricted`` -> ``approve``.  BOTH values are
    MANDATORY because goose's unset GOOSE_MODE default is itself ``auto``
    (i.e. "emit nothing" would BE the bypass).
    """
    ar = GooseTarget().descriptor.access_realization
    assert ar is not None
    assert ar.channel == Channel.ENV
    assert ar.env_var == "GOOSE_MODE"
    assert ar.full.env_value == "auto"
    assert ar.restricted.env_value == "approve"
    assert ar.full.flag == () and ar.restricted.flag == ()
    # goose persists the tier uniformly: redeemed via setting_key="access".
    assert ar.setting_key == "access"

  def test_editing_tier_has_NO_row_and_is_refused(self):
    """R-41 / [Jei:2026-08-02]: goose CANNOT render ``editing``.

    Source-verified at goose v1.38.0: no GOOSE_MODE value realizes it
    (``smart_approve`` auto-approves READ-ONLY tools and PROMPTS for writes —
    the inverse of claude's acceptEdits).  So the row is ABSENT and the tier
    is REFUSED by name, never substituted with a neighbor.
    """
    from kanibako.errors import ConfigError
    from kanibako.targets import assembly

    desc = GooseTarget().descriptor
    ar = desc.access_realization
    assert ar.editing is None
    assert ar.renders("editing") is False
    assert ar.rendered_tiers() == ("restricted", "full")
    with pytest.raises(ConfigError) as exc:
      assembly.assemble_env(
        desc,
        access="editing",
        setting_values={},
        agent="goose",
      )
    assert "restricted | full" in str(exc.value)

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
    # INC G1: goose persona = ENV delivery, OPENAI_API_KEY token var,
    # GOOSE_PROVIDER auto-pin (via provider setting); no model gate.
    p = GooseTarget().descriptor.persona
    assert p is not None
    assert p.endpoint_delivery == "env"
    assert p.token_var == "OPENAI_API_KEY"
    assert not hasattr(p, "model_required")
    assert p.provider_pin == (("provider", "openai"),)

  def test_declared_envs_are_the_plugin_required_variables(self):
    # goose declares the three variables a goose box does not work without, as
    # DECLARED KEYS (agent.goose.env.<VAR>, spec §2d) reaching the box through
    # the settings channel — so a user can override one BY NAME.  The
    # user-preference variables (GOOSE_PROVIDER / GOOSE_MODEL) are deliberately
    # NOT declared: goose owns those in its own persistent config.
    envs = GooseTarget().default_envs()
    assert envs == {
      "agent.goose.env.GOOSE_DISABLE_KEYRING": "true",
      "agent.goose.env.CONTEXT_FILE_NAMES": (
        '[".additionalContext.md","AGENTS.md",".goosehints"]'
      ),
      "agent.goose.env.KANIBAKO_DIRECTIVE_FINAL": (
        "/home/agent/.config/goose/.additionalContext.md"
      ),
    }

  def test_cred_files(self):
    # Only secrets.yaml syncs.  config.yaml is the box's own (the template, then
    # whatever ``goose configure`` writes there); custom_providers/ is not synced.
    d = GooseTarget().descriptor
    assert [s.home_rel for s in d.cred_files] == [".config/goose/secrets.yaml"]
    secrets = d.cred_files[0]
    assert secrets.host_rel == ".config/goose/secrets.yaml"
    assert secrets.cadence == Cadence.SYNC
    assert secrets.mtime_gate is True
    assert secrets.filtered is False
    assert secrets.is_dir is False

  def test_login_vars(self):
    """goose's provider key variables; the persona token variable is one."""
    d = GooseTarget().descriptor
    assert {"ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY"} <= set(d.login_vars)
    assert d.persona is not None and d.persona.token_var in d.login_vars
    assert "GOOSE_PROVIDER" not in d.login_vars
    assert len(set(d.login_vars)) == len(d.login_vars)

  def test_host_prep_and_init_dirs(self):
    d = GooseTarget().descriptor
    assert d.host_prep is False
    assert d.init_dirs == (".config/goose", ".local/share/goose/sessions")


class TestDescriptorAssembly:
  """Integration: goose's argv/env assembled from the descriptor via assembly.*."""

  def _argv(self, *, resume_mode=False, access="full", state=None, extra_args=None):
    d = GooseTarget().descriptor
    mode_key = assembly.resolve_mode(
      resume_mode=resume_mode,
      new_session=False,
      is_new_project=False,
      extra_args=extra_args or [],
      available_modes=d.mode.keys(),
    )
    # B5: fragments are passed in (the live caller reads them off the
    # launch snapshot's meta.agent.<a>.{mode,exec}); the descriptor is the
    # fixture's fragment source here.
    return assembly.assemble_argv(
      d,
      mode_fragment=d.mode[mode_key],
      access=access,
      setting_values=state or {},
      op_fragment=None,
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
      d,
      mode_fragment=d.mode["start"],
      access="full",
      setting_values={},
      op_fragment=None,
      extra_args=[],
    )
    assert argv == ["session"]

  def test_continue_argv(self):
    d = GooseTarget().descriptor
    argv = assembly.assemble_argv(
      d,
      mode_fragment=d.mode["continue"],
      access="full",
      setting_values={},
      op_fragment=None,
      extra_args=[],
    )
    assert argv == ["session", "--resume"]

  def test_new_session_forced_is_bare_session(self):
    d = GooseTarget().descriptor
    mode_key = assembly.resolve_mode(
      resume_mode=False,
      new_session=True,
      is_new_project=False,
      extra_args=[],
      available_modes=d.mode.keys(),
    )
    argv = assembly.assemble_argv(
      d,
      mode_fragment=d.mode[mode_key],
      access="full",
      setting_values={},
      op_fragment=None,
      extra_args=[],
    )
    assert argv == ["session"]

  def test_exec_op_argv(self):
    d = GooseTarget().descriptor
    argv = assembly.assemble_argv(
      d,
      mode_fragment=d.mode["start"],
      access="full",
      setting_values={},
      op_fragment=d.operations["exec"].fragment,
      extra_args=["do the thing"],
    )
    assert argv == ["run", "--no-session", "-t", "do the thing"]

  def test_env_full_tier_sets_goose_mode_auto(self):
    d = GooseTarget().descriptor
    env = assembly.assemble_env(d, access="full", setting_values={})
    assert env["GOOSE_MODE"] == "auto"

  def test_env_restricted_tier_sets_goose_mode_approve(self):
    """``restricted`` emits GOOSE_MODE=approve (NOT nothing).

    goose's unset GOOSE_MODE default is ``auto`` (tools auto-run), so the
    restrictive tier must emit a value or it would not actually restrict.
    """
    d = GooseTarget().descriptor
    env = assembly.assemble_env(d, access="restricted", setting_values={})
    assert env["GOOSE_MODE"] == "approve"

  def test_env_model_and_provider_from_settings(self):
    d = GooseTarget().descriptor
    env = assembly.assemble_env(
      d,
      access="full",
      setting_values={"model": "claude-4", "provider": "anthropic"},
    )
    assert env["GOOSE_MODEL"] == "claude-4"
    assert env["GOOSE_PROVIDER"] == "anthropic"

  def test_env_omits_provider_model_when_unset(self):
    """No provider/model setting -> GOOSE_PROVIDER/GOOSE_MODEL NOT emitted.

    This is the in-box-``goose configure`` fix: kanibako must not force the
    provider/model env vars (they override goose's config.yaml).  With no
    setting, assemble_env (``if value:``) omits both — while GOOSE_MODE, an
    access-tier realization, still emits.
    """
    d = GooseTarget().descriptor
    env = assembly.assemble_env(d, access="full", setting_values={})
    assert "GOOSE_PROVIDER" not in env
    assert "GOOSE_MODEL" not in env
    assert env["GOOSE_MODE"] == "auto"

  def test_an_empty_model_emits_GOOSE_MODEL_and_an_empty_provider_nothing(self):
    """The shipped entries declare no ``empty:``: ``""`` emits for ``model``
    (``GOOSE_MODEL=``, the empty model) and not for ``provider``."""
    d = GooseTarget().descriptor
    env = assembly.assemble_env(
      d,
      access="full",
      setting_values={"model": "", "provider": ""},
    )
    assert "GOOSE_PROVIDER" not in env
    assert env["GOOSE_MODEL"] == ""

  def test_the_declared_variables_are_not_assembled_here(self):
    """The assembly emits REALIZATIONS only; the declared keys are elsewhere.

    GOOSE_DISABLE_KEYRING is unconditional for a goose box (the in-box keyring
    is unavailable, so goose must fall back to ~/.config/goose/secrets.yaml) —
    and it is unconditional because it is a DECLARED KEY carried by the
    settings channel, not because some assembly path always emits it.  Asserting
    it here would pin the wrong mechanism.
    """
    d = GooseTarget().descriptor
    for tier in ("restricted", "full"):
      env = assembly.assemble_env(d, access=tier, setting_values={})
      assert "GOOSE_DISABLE_KEYRING" not in env
    assert "agent.goose.env.GOOSE_DISABLE_KEYRING" in GooseTarget().default_envs()


class TestDeliverySeams:
  """T1 seams: goose writes no ``access`` tier (the launch carries it as the
  ``GOOSE_MODE`` env var); its directive-hook seam writes the canon-hooks plugin
  (``TestCanonHooksPlugin``)."""

  def _config(self, config_root: Path) -> Path:
    return config_root / ".config" / "goose" / "config.yaml"

  @pytest.mark.parametrize("tier", ["restricted", "editing", "full"])
  def test_panel_permissions_leave_user_goose_mode_byte_identical(
    self,
    tmp_path,
    tier,
  ):
    """A user's own ``GOOSE_MODE`` survives every tier, byte-identical."""
    cfg = self._config(tmp_path)
    cfg.parent.mkdir(parents=True)
    cfg.write_text("GOOSE_PROVIDER: openai\nGOOSE_MODE: smart_approve\n")
    before = cfg.read_bytes()
    assert (
      GooseTarget().deliver_panel_permissions(
        config_root=tmp_path,
        access=tier,
      )
      is False
    )
    assert cfg.read_bytes() == before

  @pytest.mark.parametrize("tier", ["restricted", "editing", "full"])
  def test_panel_permissions_create_no_file(self, tmp_path, tier):
    assert (
      GooseTarget().deliver_panel_permissions(
        config_root=tmp_path,
        access=tier,
      )
      is False
    )
    assert not (tmp_path / ".config").exists()


_HOOKS = Path(".agents", "plugins", "kanibako", "hooks")
_CANON_HOOKS = "~/canon/charter/general/scripts/hooks/"


class TestCanonHooksPlugin:
  """``deliver_directive_hook`` writes the kanibako goose plugin at every start."""

  def _deliver(self, root: Path) -> bool:
    return GooseTarget().deliver_directive_hook(config_root=root, access="full")

  def test_writes_only_hooks_json(self, tmp_path):
    assert self._deliver(tmp_path) is True
    hooks = tmp_path / _HOOKS
    assert sorted(p.name for p in hooks.iterdir()) == ["hooks.json"]
    assert not (tmp_path / ".config").exists()

  def test_adapter_ships_in_the_charter_chapter(self):
    """The adapter reaches the box through the rom bind, never a copy (Single route)."""
    rom = GooseTarget().rom_root()
    assert rom is not None
    assert (rom / "ROM_AGENT.md").is_file()
    assert (rom / "scripts" / "hooks" / "mail-stop.sh").is_file()

  def test_event_map(self, tmp_path):
    self._deliver(tmp_path)
    doc = json.loads((tmp_path / _HOOKS / "hooks.json").read_text())["hooks"]
    commands = {
      event: [h["command"] for rule in rules for h in rule["hooks"]]
      for event, rules in doc.items()
    }
    assert commands == {
      "SessionStart": [_CANON_HOOKS + 'startup.sh "$PPID"'],
      "SessionEnd": [_CANON_HOOKS + 'end.sh "$PPID"'],
      "AfterFileEdit": [_CANON_HOOKS + "edited.sh"],
      "Stop": [
        _CANON_HOOKS + "stop.sh",
        "bash ~/canon/charter/agent/scripts/hooks/mail-stop.sh",
      ],
    }
    # goose tests a matcher against "" for session events, so none may be set.
    assert all("matcher" not in rule for rules in doc.values() for rule in rules)

  def test_check_comms_is_never_a_post_tool_use_hook(self, tmp_path):
    """goose discards PostToolUse output, so check-comms there eats mail unseen."""
    self._deliver(tmp_path)
    text = (tmp_path / _HOOKS / "hooks.json").read_text()
    assert "check-comms" not in text
    assert "PostToolUse" not in text

  def test_unchanged_files_are_not_rewritten(self, tmp_path):
    self._deliver(tmp_path)
    before = {p: p.stat().st_mtime_ns for p in (tmp_path / _HOOKS).iterdir()}
    assert self._deliver(tmp_path) is False
    assert {p: p.stat().st_mtime_ns for p in before} == before

  def test_existing_box_is_rewritten(self, tmp_path):
    """A box that predates the plugin, or holds a stale copy, gets the shipped one."""
    hooks = tmp_path / _HOOKS
    hooks.mkdir(parents=True)
    (hooks / "hooks.json").write_text("{}")
    assert self._deliver(tmp_path) is True
    assert "SessionStart" in (hooks / "hooks.json").read_text()

  def test_symlinked_parent_is_not_followed(self, tmp_path):
    home, outside = tmp_path / "home", tmp_path / "outside"
    home.mkdir()
    outside.mkdir()
    (home / ".agents").symlink_to(outside)
    assert self._deliver(home) is False
    assert list(outside.rglob("*")) == []

  def test_symlinked_leaf_is_not_written_through(self, tmp_path):
    hooks = tmp_path / "home" / _HOOKS
    hooks.mkdir(parents=True)
    victim = tmp_path / "victim"
    victim.write_text("keep")
    (hooks / "hooks.json").symlink_to(victim)
    assert self._deliver(tmp_path / "home") is False
    assert victim.read_text() == "keep"
    assert (hooks / "hooks.json").is_symlink()
