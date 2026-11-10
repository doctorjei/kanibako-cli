"""The persona pre-flight reads the token var from ``-e``, which outranks a ``secret_path`` file.

``-e VAR=…`` beats a ``secret_path.VAR`` key for the launch (spec §1A): the box gets the
``-e`` value and no file is mounted for it.  The pre-flight used to see only the file, so
a token given only by ``-e`` was refused as missing, and with both, the probe sent the
FILE's token — a revoked file token refused a launch whose ``-e`` token is good.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from kanibako.plugins.claude.target import ClaudeTarget
from kanibako.plugins.codex.target import CodexTarget
from tests.test_targets.test_persona_settings import _ProbeServer

NODE_ID = "navigator℘claude"
CODEX_ID = "navigator℘codex"
VAR = "ANTHROPIC_AUTH_TOKEN"
E_TOKEN = "sk-from-e-7f3a91"
FILE_TOKEN = "sk-revoked-file-0c2d"


@pytest.fixture
def server():
  srv = _ProbeServer(status=200)
  yield srv
  srv.close()


@pytest.fixture
def file_token(tmp_path):
  tok = tmp_path / "token"
  tok.write_text(FILE_TOKEN + "\n")
  return tok


def _authorizations(srv) -> set:
  return {auth for _m, _p, auth, _ct, _b in srv.log}


# ---------------------------------------------------------------------------
# The launch door: ``_run_container`` hands its ``-e`` to the pre-flight.
# ---------------------------------------------------------------------------


def _launch(start_mocks, secrets, cli_env, endpoint):
  """Run ``_run_container`` to its persona pre-flight with a REAL claude probe."""
  from kanibako.commands.start import _run_container
  from tests.support.agent_selection import agent_selection
  from kanibako.settings.settings_launch import AuthSource

  auth = AuthSource(
    tier="global",
    global_enabled=True,
    workset_enabled=False,
    global_sync=False,
    workset_source=None,
  )
  real = ClaudeTarget()
  with (
    start_mocks() as m,
    patch(
      "kanibako.commands.start._resolve_box_launch_decisions",
      return_value=(auth, endpoint, "m-1", secrets),
    ),
  ):
    m.proj.is_new = False
    m.resolve_agent.return_value = agent_selection(node=NODE_ID, source="settings")
    m.target.name = "claude"
    m.target.descriptor = real.descriptor
    m.target.setting_descriptors.return_value = real.setting_descriptors()
    m.agent_cfg.secret_path.update(secrets)
    m.agent_cfg.state.update(endpoint=endpoint, model="m-1")
    m.target.verify_persona = real.verify_persona
    return _run_container(
      project_dir=None,
      entrypoint=None,
      image_override=None,
      new_session=False,
      safe_mode=False,
      resume_mode=False,
      extra_args=[],
      cli_env=cli_env,
    )


class TestTheLaunchReadsE:
  def test_a_token_only_e_gives_is_not_refused(self, start_mocks, server, capsys):
    """(a) No ``secret_path`` at all: the token var is given by ``-e`` alone."""
    _launch(start_mocks, {}, [f"{VAR}={E_TOKEN}"], server.endpoint)
    err = capsys.readouterr().err
    assert "no usable auth token" not in err
    assert _authorizations(server) >= {f"Bearer {E_TOKEN}"}

  def test_the_probe_sends_the_e_token_not_the_files(
    self,
    start_mocks,
    server,
    file_token,
    capsys,
  ):
    """(b) Both: the box gets ``-e``, so the probe must too — never the file's."""
    _launch(start_mocks, {VAR: str(file_token)}, [f"{VAR}={E_TOKEN}"], server.endpoint)
    sent = _authorizations(server) - {None}
    assert sent == {f"Bearer {E_TOKEN}"}
    assert FILE_TOKEN not in capsys.readouterr().err

  def test_a_revoked_file_token_does_not_refuse_a_good_e(
    self,
    start_mocks,
    file_token,
    capsys,
  ):
    """(b) end to end: the endpoint refuses the file token and accepts ``-e``'s."""
    srv = _TokenAwareServer(good=E_TOKEN)
    try:
      _launch(start_mocks, {VAR: str(file_token)}, [f"{VAR}={E_TOKEN}"], srv.endpoint)
    finally:
      srv.close()
    assert "cannot be loaded" not in capsys.readouterr().err

  def test_without_e_the_file_token_is_sent_as_before(
    self,
    start_mocks,
    server,
    file_token,
  ):
    _launch(start_mocks, {VAR: str(file_token)}, None, server.endpoint)
    assert _authorizations(server) - {None} == {f"Bearer {FILE_TOKEN}"}


class _TokenAwareServer:
  """A local endpoint that answers 200 to ``Bearer <good>``, 401 to anything else."""

  def __init__(self, good: str):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    want = f"Bearer {good}"

    class _Handler(BaseHTTPRequestHandler):
      def _answer(self):
        length = int(self.headers.get("Content-Length", 0))
        if length:
          self.rfile.read(length)
        ok = self.headers.get("Authorization") == want
        self.send_response(200 if ok else 401)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"{}")

      do_GET = do_POST = _answer  # noqa: N815 (http.server API names)

      def log_message(self, *args):
        pass

    self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    self.endpoint = f"http://127.0.0.1:{self._server.server_port}"
    threading.Thread(target=self._server.serve_forever, daemon=True).start()

  def close(self):
    self._server.shutdown()
    self._server.server_close()


# ---------------------------------------------------------------------------
# The pre-flight itself, per harness.
# ---------------------------------------------------------------------------


def _preflight(agent_id, secrets, endpoint, target, *, cli_env, model="m-1", logger=None):
  from kanibako.commands.start import _preflight_persona_load

  return _preflight_persona_load(
    agent_id,
    secrets,
    endpoint,
    logger,
    target=target,
    keyspace_model=model,
    probe=True,
    cli_env=cli_env,
  )


class TestEnvHarness:
  def test_an_unusable_file_with_e_passes(self, server, tmp_path):
    """``-e`` passes the gate whatever the file's state; the file is never mounted."""
    ep, err, _ = _preflight(
      NODE_ID,
      {VAR: str(tmp_path / "absent")},
      server.endpoint,
      ClaudeTarget(),
      cli_env={VAR: E_TOKEN},
    )
    assert (ep, err) == (server.endpoint, None)
    assert _authorizations(server) - {None} == {f"Bearer {E_TOKEN}"}

  def test_a_keyless_declaration_with_e_sends_the_e_token(self, server):
    _preflight(NODE_ID, {VAR: None}, server.endpoint, ClaudeTarget(), cli_env={VAR: E_TOKEN})
    assert _authorizations(server) - {None} == {f"Bearer {E_TOKEN}"}

  @pytest.mark.parametrize("value", ["", "   ", "\t"])
  def test_a_blank_e_suppresses_the_credential(self, server, file_token, value, capsys):
    """P5: a blank ``-e`` SUPPRESSES the shadowed credential — no probe, no refusal.

    Replaces the old ``test_an_empty_e_changes_nothing``, which pinned the
    opposite contract ("act on the file as before").  Approved canon P5 reverses
    it: blank/whitespace ``-e`` skips the credential gate and ``verify_persona``
    entirely, warns UNVERIFIED, and never PASS.
    """
    ep, err, _ = _preflight(
      NODE_ID, {VAR: str(file_token)}, server.endpoint, ClaudeTarget(), cli_env={VAR: value}
    )
    assert err is None and ep == server.endpoint
    assert server.log == [], "a suppressed credential must not be probed at all"
    err_text = capsys.readouterr().err
    assert "UNVERIFIED" in err_text
    assert VAR in err_text
    assert FILE_TOKEN not in err_text

  @pytest.mark.parametrize("value", ["", "   "])
  def test_a_blank_e_proceeds_when_there_is_nothing_to_suppress(self, server, value, capsys):
    """No ``secret_path`` either: the launch still proceeds UNVERIFIED, not refused.

    This is the half that newly ALLOWS a launch today's code errors on.  A blank
    ``-e`` is a deliberate "run unverified", so refusing it would be kanibako
    closing the very door it exists to open.
    """
    ep, err, _ = _preflight(NODE_ID, {}, server.endpoint, ClaudeTarget(), cli_env={VAR: value})
    assert err is None and ep == server.endpoint
    assert server.log == []
    assert "UNVERIFIED" in capsys.readouterr().err

  @pytest.mark.parametrize("value", ["", "   "])
  def test_a_blank_e_never_stats_the_shadowed_pointer(self, server, file_token, value):
    """The load-bearing claim: NO read and NO stat of the credential material.

    Without this the suite stays green while a ``stat()`` still happens.  The
    contract is about the ABSENCE OF THE CALL, not about the verdict it returns,
    so the call itself is what gets asserted.
    """
    from kanibako.commands import start as start_mod

    seen = []
    real = start_mod._secret_pointer_usable

    def _spy(raw_path):
      seen.append(raw_path)
      return real(raw_path)

    with patch.object(start_mod, "_secret_pointer_usable", _spy):
      ep, err, _ = _preflight(
        NODE_ID, {VAR: str(file_token)}, server.endpoint, ClaudeTarget(), cli_env={VAR: value}
      )
    assert err is None
    assert seen == [], f"suppression must not stat the pointer; saw {seen}"

  def test_a_nonblank_e_still_stats_and_probes_as_before(self, server, file_token):
    """Positive control: the suppression is the blank case ONLY."""
    ep, err, _ = _preflight(
      NODE_ID, {VAR: str(file_token)}, server.endpoint, ClaudeTarget(), cli_env={VAR: E_TOKEN}
    )
    assert err is None and ep == server.endpoint
    assert _authorizations(server) - {None} == {f"Bearer {E_TOKEN}"}

  def test_another_vars_e_does_not_satisfy_the_gate(self, server):
    _ep, err, _ = _preflight(
      NODE_ID, {}, server.endpoint, ClaudeTarget(), cli_env={"SOME_OTHER_VAR": E_TOKEN}
    )
    assert err is not None and "no usable auth token" in err
    assert server.log == []


class TestConfigFileHarness:
  KEY = "NAVIGATOR_API_KEY"

  def test_an_unusable_file_with_e_passes_and_sends_e(self, server, tmp_path):
    ep, err, provider = _preflight(
      CODEX_ID,
      {self.KEY: str(tmp_path / "absent")},
      server.endpoint,
      CodexTarget(),
      cli_env={self.KEY: E_TOKEN},
    )
    assert err is None and ep == server.endpoint
    assert provider is not None and provider.env_key == self.KEY
    assert _authorizations(server) - {None} == {f"Bearer {E_TOKEN}"}

  def test_e_beats_a_usable_file(self, server, file_token):
    _preflight(
      CODEX_ID,
      {self.KEY: str(file_token)},
      server.endpoint,
      CodexTarget(),
      cli_env={self.KEY: E_TOKEN},
    )
    assert _authorizations(server) - {None} == {f"Bearer {E_TOKEN}"}

  def test_the_key_must_still_be_declared(self, server):
    """The declared name IS the provider ``env_key``; ``-e`` alone cannot name it."""
    _ep, err, _ = _preflight(
      CODEX_ID, {}, server.endpoint, CodexTarget(), cli_env={self.KEY: E_TOKEN}
    )
    assert err is not None and "none was found" in err

  @pytest.mark.parametrize("value", ["", "   "])
  def test_a_blank_e_suppresses_the_codex_probe_too(self, server, file_token, value, capsys):
    """Same P5 contract on the config-file harness — and the provider still builds.

    The provider carries the ``config.toml`` wiring, not the credential, so
    suppressing the credential must not suppress it.
    """
    ep, err, provider = _preflight(
      CODEX_ID,
      {self.KEY: str(file_token)},
      server.endpoint,
      CodexTarget(),
      cli_env={self.KEY: value},
    )
    assert err is None and ep == server.endpoint
    assert provider is not None and provider.env_key == self.KEY
    assert server.log == [], "a suppressed credential must not be probed at all"
    err_text = capsys.readouterr().err
    assert "UNVERIFIED" in err_text
    assert FILE_TOKEN not in err_text

  @pytest.mark.parametrize("value", ["", "   "])
  def test_a_blank_e_still_cannot_name_an_undeclared_key(self, server, value):
    """Suppression suppresses the CREDENTIAL, not the declaration the provider needs."""
    _ep, err, _ = _preflight(
      CODEX_ID, {}, server.endpoint, CodexTarget(), cli_env={self.KEY: value}
    )
    assert err is not None and "none was found" in err


class TestAPluginWithoutTheTokenParameter:
  def test_it_is_called_as_before_when_e_gives_no_token(self, file_token):
    """``token=`` is passed only when set: an older plugin signature keeps working."""
    from kanibako.targets.base import PersonaProbeOutcome

    calls = []

    class _Old:
      descriptor = ClaudeTarget().descriptor

      def verify_persona(self, endpoint, token_path, model, *, env=None, timeout=5.0):
        calls.append(token_path)
        return PersonaProbeOutcome.passed()

    logger = MagicMock()
    ep, err, _ = _preflight(
      NODE_ID, {VAR: str(file_token)}, "https://e.example", _Old(), cli_env=None, logger=logger
    )
    assert (ep, err) == ("https://e.example", None)
    assert [str(p) for p in calls] == [str(file_token)]

  def test_with_e_it_is_not_probed_and_the_launch_says_so(self, file_token, capsys):
    """With ``-e`` an older plugin is skipped, not handed ``token=`` and failed."""
    from kanibako.targets.base import PersonaProbeOutcome

    calls = []

    class _Old:
      descriptor = ClaudeTarget().descriptor

      def verify_persona(self, endpoint, token_path, model, *, env=None, timeout=5.0):
        calls.append(token_path)
        return PersonaProbeOutcome.passed()

    logger = MagicMock()
    ep, err, _ = _preflight(
      NODE_ID,
      {VAR: str(file_token)},
      "https://e.example",
      _Old(),
      cli_env={VAR: E_TOKEN},
      logger=logger,
    )
    assert (ep, err) == ("https://e.example", None)
    assert calls == []
    stderr = capsys.readouterr().err
    assert "cannot take a token from -e" in stderr
    assert "is not verified" in stderr
    assert "the probe itself failed" not in stderr
    assert E_TOKEN not in stderr and FILE_TOKEN not in stderr
    assert not any(c.kwargs.get("exc_info") for c in logger.debug.call_args_list)

  @pytest.mark.parametrize("sig", ["token", "kwargs"])
  def test_a_plugin_that_accepts_token_gets_the_e_value(self, file_token, sig):
    from kanibako.targets.base import PersonaProbeOutcome

    got = []

    class _New:
      descriptor = ClaudeTarget().descriptor

    if sig == "token":

      def verify_persona(self, endpoint, token_path, model, *, env=None, timeout=5.0, token=None):
        got.append((token_path, token))
        return PersonaProbeOutcome.passed()
    else:

      def verify_persona(self, endpoint, token_path, model, **kwargs):
        got.append((token_path, kwargs.get("token")))
        return PersonaProbeOutcome.passed()

    _New.verify_persona = verify_persona

    ep, err, _ = _preflight(
      NODE_ID, {VAR: str(file_token)}, "https://e.example", _New(), cli_env={VAR: E_TOKEN}
    )
    assert (ep, err) == ("https://e.example", None)
    assert got == [(None, E_TOKEN)]


# ---------------------------------------------------------------------------
# The token VALUE never leaves the request.
# ---------------------------------------------------------------------------


class TestTheETokenNeverLeaks:
  @pytest.mark.parametrize(
    "agent_id,target_cls,var",
    [
      (NODE_ID, ClaudeTarget, VAR),
      (CODEX_ID, CodexTarget, "NAVIGATOR_API_KEY"),
    ],
  )
  def test_a_refusal_names_e_and_never_the_value(
    self,
    agent_id,
    target_cls,
    var,
    capsys,
    caplog,
  ):
    """The provider echoes the token back in a 401; nothing prints or logs it."""
    import logging

    srv = _ProbeServer(
      status=401,
      body=f'{{"error": "invalid key {E_TOKEN}"}}'.encode(),
    )
    logger = logging.getLogger("kanibako.test.etoken")
    caplog.set_level(logging.DEBUG)
    try:
      secrets = {var: None} if target_cls is CodexTarget else {}
      _ep, err, _ = _preflight(
        agent_id, secrets, srv.endpoint, target_cls(), cli_env={var: E_TOKEN}, logger=logger
      )
    finally:
      srv.close()
    assert _authorizations(srv) - {None} == {f"Bearer {E_TOKEN}"}
    assert err is not None and "cannot be loaded" in err
    assert "(the value of -e on the command line)" in err
    out = capsys.readouterr()
    for text in (err, out.out, out.err, caplog.text):
      assert E_TOKEN not in text

  def test_the_evidence_carries_a_flag_not_the_value(self):
    from kanibako.targets.base import ProbeEvidence, probe_token

    token, outcome = probe_token(None, E_TOKEN)
    assert (token, outcome) == (E_TOKEN, None)
    ev = ProbeEvidence(endpoint="https://e.example", token_by_value=True)
    assert E_TOKEN not in repr(ev)
    assert "-e on the command line" in "\n".join(ev.lines())


class TestResolvedPersonaGate:
  def deliveries(self, kind, var=VAR, *, node=NODE_ID, declared=True, keyless=False):
    from pathlib import Path
    from kanibako.settings.settings_categories import (
      MOUNT,
      CategoryEntry,
      CredentialState,
      LaunchDeliveries,
      PersonaCredential,
      SECRET_MOUNT_DIR,
    )

    declaration = CategoryEntry(
      "secret_path",
      "agent",
      f"{SECRET_MOUNT_DIR}/{var}",
      "/unread-credential" if kind in ("file", "exact-mask-suppressed") else None,
      MOUNT,
      "ro",
      var,
      ("agent", node, "secret_path", var),
      persona_credential=PersonaCredential("Guide", Path("/unread-store"), var, False)
      if kind == "pg"
      else None,
    )
    return LaunchDeliveries(
      [],
      frozenset(),
      credential_states={} if kind is None else {var: CredentialState(kind, declaration)},
      active_secret_names={var: [declaration.key]} if declared else {},
      keyless_persona=PersonaCredential("Guide", Path("/unread-store"), None, True)
      if keyless
      else None,
    )

  def gate(self, deliveries, *, target=None, node=NODE_ID, cli=None, **kw):
    from kanibako.commands.start import _preflight_resolved_persona_load

    return _preflight_resolved_persona_load(
      node,
      deliveries,
      "https://endpoint.invalid",
      None,
      target=target or ClaudeTarget(),
      env={"FINAL": "env"},
      cli_env=cli or {},
      probe_model="final-model",
      provider_model="stored-model",
      **kw,
    )

  @pytest.mark.parametrize("kind", ["file", "keyless-null", "cli-value"])
  def test_final_probe_uses_only_returned_state_and_explicit_inputs(self, kind):
    from kanibako.targets.base import PersonaProbeOutcome

    deliveries = self.deliveries(kind)
    with (
      patch("kanibako.commands.start._secret_pointer_usable", return_value=True) as usable,
      patch.object(
        ClaudeTarget, "verify_persona", return_value=PersonaProbeOutcome.passed()
      ) as probe,
      patch(
        "kanibako.settings.settings_launch.snapshot_category_entries", side_effect=AssertionError
      ),
      patch(
        "kanibako.settings.settings_categories.secret_path_winners", side_effect=AssertionError
      ),
      patch("kanibako.settings.store_collapse.collapse_env", side_effect=AssertionError),
      patch("kanibako.settings.config_io.fetch_persona_secret", side_effect=AssertionError),
    ):
      result = self.gate(deliveries, cli={VAR: E_TOKEN, "UNRELATED": "ignored"})
    assert result == ("https://endpoint.invalid", None, None)
    assert usable.call_count == (1 if kind == "file" else 0)
    args, kwargs = probe.call_args
    assert args[0] == "https://endpoint.invalid"
    assert str(args[1]) == ("/unread-credential" if kind == "file" else "None")
    assert args[2] == "final-model"
    assert kwargs["env"] == {"FINAL": "env"}
    assert kwargs.get("token") == (E_TOKEN if kind == "cli-value" else None)

  @pytest.mark.parametrize("kind", ["absent", "exact-mask-suppressed"])
  def test_absence_and_exact_mask_refuse_without_access_or_probe(self, kind):
    with (
      patch("kanibako.commands.start._secret_pointer_usable", side_effect=AssertionError),
      patch.object(ClaudeTarget, "verify_persona", side_effect=AssertionError),
    ):
      endpoint, error, provider = self.gate(self.deliveries(kind))
    assert endpoint is None and provider is None
    assert "no usable auth token" in error

  @pytest.mark.parametrize("value", ["", "   ", "\t"])
  def test_blank_cli_skips_every_material_operation_and_warns_once(self, value, capsys):
    with (
      patch("kanibako.commands.start._secret_pointer_usable", side_effect=AssertionError),
      patch.object(ClaudeTarget, "verify_persona", side_effect=AssertionError),
      patch("kanibako.settings.config_io.fetch_persona_secret", side_effect=AssertionError),
    ):
      result = self.gate(self.deliveries("cli-blank-suppressed"), cli={VAR: value})
    assert result == ("https://endpoint.invalid", None, None)
    assert capsys.readouterr().err.count("UNVERIFIED") == 1

  def test_dynamic_names_ignore_other_scopes_and_provider_model_stays_stored(self):
    from kanibako.targets.base import PersonaProbeOutcome

    deliveries = self.deliveries("keyless-null", "TOKEN", node=CODEX_ID)
    deliveries.active_secret_names["OTHER"] = [
      "box.secret_path.OTHER",
      "agent.default.secret_path.OTHER",
    ]
    with patch.object(
      CodexTarget, "verify_persona", return_value=PersonaProbeOutcome.passed()
    ) as probe:
      endpoint, error, provider = self.gate(deliveries, target=CodexTarget(), node=CODEX_ID)
    assert error is None and endpoint == "https://endpoint.invalid"
    assert provider.env_key == "TOKEN" and provider.model == "stored-model"
    assert probe.call_args.args[2] == "final-model"
    assert probe.call_args.kwargs["env"] == {"FINAL": "env"}

  def test_dynamic_multiple_own_names_preserve_ambiguity(self):
    deliveries = self.deliveries("keyless-null", "TOKEN", node=CODEX_ID)
    deliveries.active_secret_names["OTHER"] = [f"agent.{CODEX_ID}.secret_path.OTHER"]
    endpoint, error, provider = self.gate(deliveries, target=CodexTarget(), node=CODEX_ID)
    assert endpoint is None and provider is None
    assert "ambiguous" in error

  def test_keyless_without_var_keeps_empty_provider_env_key(self):
    from kanibako.targets.base import PersonaProbeOutcome

    with patch.object(
      CodexTarget, "verify_persona", return_value=PersonaProbeOutcome.passed()
    ) as probe:
      endpoint, error, provider = self.gate(
        self.deliveries(None, declared=False, keyless=True),
        target=CodexTarget(),
        node=CODEX_ID,
      )
    assert error is None and endpoint == "https://endpoint.invalid"
    assert provider.env_key == ""
    assert probe.call_args.args[1] is None
    assert "token" not in probe.call_args.kwargs

  def test_pg_is_not_adapted_to_null_or_probed_as_keyless(self):
    from kanibako.commands.start import _resolved_persona_secret_table

    deliveries = self.deliveries("pg")
    assert VAR not in _resolved_persona_secret_table(deliveries, NODE_ID, {})
    with (
      patch("kanibako.commands.start._secret_pointer_usable", side_effect=AssertionError),
      patch.object(ClaudeTarget, "verify_persona", side_effect=AssertionError),
      patch("kanibako.settings.config_io.fetch_persona_secret", side_effect=AssertionError),
    ):
      assert self.gate(deliveries) == ("https://endpoint.invalid", None, None)

  def test_preparation_is_material_free_but_compatibility_probe_false_still_gates(self):
    from kanibako.commands.start import _prepare_persona_load, _preflight_persona_load

    with (
      patch("kanibako.commands.start._secret_pointer_usable", side_effect=AssertionError),
      patch.object(ClaudeTarget, "verify_persona", side_effect=AssertionError),
    ):
      assert _prepare_persona_load(NODE_ID, "https://endpoint.invalid", target=ClaudeTarget()) == (
        "https://endpoint.invalid",
        None,
        None,
      )
      endpoint, error, provider = _preflight_persona_load(
        NODE_ID,
        {},
        "https://endpoint.invalid",
        None,
        target=ClaudeTarget(),
        probe=False,
      )
    assert endpoint is None and provider is None
    assert "no usable auth token" in error


class TestPreparedPersonaDelivery:
  deliveries = TestResolvedPersonaGate.deliveries

  def prepare(
    self,
    deliveries,
    *,
    target=None,
    node=NODE_ID,
    cli=None,
    endpoint="https://endpoint.invalid",
    **kw,
  ):
    from kanibako.commands.start import _prepare_resolved_persona_delivery

    return _prepare_resolved_persona_delivery(
      node,
      deliveries,
      endpoint,
      None,
      target=target or ClaudeTarget(),
      env={"FINAL": "env"},
      cli_env=cli or {},
      probe_model="final-model",
      provider_model="stored-model",
      **kw,
    )

  def test_pg_fetches_once_from_captured_root_and_probes_payload_value(self):
    from pathlib import Path
    from kanibako.targets.base import PersonaProbeOutcome

    deliveries = self.deliveries("pg")
    token = " synthetic-credential-λ\t "
    with (
      patch("kanibako.settings.config_io.fetch_persona_secret", return_value=token) as fetch,
      patch("kanibako.commands.start._secret_pointer_usable", side_effect=AssertionError),
      patch.object(
        ClaudeTarget, "verify_persona", return_value=PersonaProbeOutcome.passed()
      ) as probe,
    ):
      result, payload = self.prepare(deliveries)
    assert result == ("https://endpoint.invalid", None, None)
    fetch.assert_called_once_with("Guide", root=Path("/unread-store"))
    probe.assert_called_once_with(
      "https://endpoint.invalid", None, "final-model", env={"FINAL": "env"}, token=token
    )
    assert payload.names == (VAR,)
    assert payload.values[0].value == token.encode("utf-8")
    assert token not in repr(payload)
    assert deliveries.credential_states[VAR].kind == "pg"

  def test_every_pg_winner_is_fetched_not_only_the_harness_token(self):
    from dataclasses import replace
    from pathlib import Path
    from kanibako.targets.base import PersonaProbeOutcome

    deliveries = self.deliveries("pg")
    other = self.deliveries("pg", "OTHER_KEY").credential_states["OTHER_KEY"]
    other = replace(
      other,
      declaration=replace(
        other.declaration,
        persona_credential=replace(other.declaration.persona_credential, registered_name="Other"),
      ),
    )
    deliveries.credential_states["OTHER_KEY"] = other
    with (
      patch(
        "kanibako.settings.config_io.fetch_persona_secret",
        side_effect=["first-value", "second-value"],
      ) as fetch,
      patch.object(
        ClaudeTarget, "verify_persona", return_value=PersonaProbeOutcome.passed()
      ) as probe,
    ):
      result, payload = self.prepare(deliveries)
    assert result[1] is None
    assert fetch.call_args_list == [
      (("Guide",), {"root": Path("/unread-store")}),
      (("Other",), {"root": Path("/unread-store")}),
    ]
    assert [(entry.name, entry.value) for entry in payload.values] == [
      (VAR, b"first-value"),
      ("OTHER_KEY", b"second-value"),
    ]
    assert probe.call_count == 1 and probe.call_args.kwargs["token"] == "first-value"

  @pytest.mark.parametrize(
    "kind",
    [
      "file",
      "keyless-null",
      "cli-value",
      "cli-blank-suppressed",
      "absent",
      "exact-mask-suppressed",
    ],
  )
  def test_eliminated_pg_sources_never_open_a_manager_or_fetch(self, kind):
    from kanibako.targets.base import PersonaProbeOutcome

    with (
      patch("kanibako.settings.config_io.fetch_persona_secret", side_effect=AssertionError),
      patch("persona_grata.AgentStore", side_effect=AssertionError),
      patch("subprocess.Popen", side_effect=AssertionError),
      patch("kanibako.commands.start._secret_pointer_usable", return_value=True) as usable,
      patch.object(
        ClaudeTarget, "verify_persona", return_value=PersonaProbeOutcome.passed()
      ) as probe,
    ):
      result, payload = self.prepare(
        self.deliveries(kind), cli={VAR: "cli-value" if kind == "cli-value" else " "}
      )
    assert payload.names == ()
    assert usable.call_count == (1 if kind == "file" else 0)
    assert probe.call_count == (1 if kind in ("file", "keyless-null", "cli-value") else 0)
    if kind == "keyless-null":
      assert probe.call_args.args[1] is None and "token" not in probe.call_args.kwargs
    if kind in ("absent", "exact-mask-suppressed"):
      assert result[0] is None and "no usable auth token" in result[1]
    else:
      assert result[1] is None

  @pytest.mark.parametrize("failure", ["endpoint", "bundle", "ambiguous", "missing"])
  def test_loadability_and_provider_failures_precede_every_pg_access(self, failure):
    from types import SimpleNamespace

    dynamic = failure in ("ambiguous", "missing")
    deliveries = self.deliveries(
      "pg", "OWN_KEY" if dynamic else VAR, node=CODEX_ID if dynamic else NODE_ID
    )
    if failure == "ambiguous":
      deliveries.active_secret_names["SECOND"] = [f"agent.{CODEX_ID}.secret_path.SECOND"]
    elif failure == "missing":
      deliveries.active_secret_names.clear()
    with (
      patch("kanibako.settings.config_io.fetch_persona_secret", side_effect=AssertionError),
      patch("persona_grata.AgentStore", side_effect=AssertionError),
      patch("subprocess.Popen", side_effect=AssertionError),
      patch("kanibako.commands.start._secret_pointer_usable", side_effect=AssertionError),
      patch.object(ClaudeTarget, "verify_persona", side_effect=AssertionError),
      patch.object(CodexTarget, "verify_persona", side_effect=AssertionError),
    ):
      result, payload = self.prepare(
        deliveries,
        target=CodexTarget() if dynamic else ClaudeTarget(),
        node=CODEX_ID if dynamic else NODE_ID,
        endpoint=None if failure == "endpoint" else "https://endpoint.invalid",
        bundle=SimpleNamespace(reject_reason="unusable config") if failure == "bundle" else None,
      )
    assert result[0] is None and result[1] is not None and result[2] is None
    assert payload.names == ()

  @pytest.mark.parametrize("token", [None, b"bytes", "", "  ", "a\nb", "a\rb", "a\x00b", "\udcff"])
  def test_invalid_public_fetch_values_never_probe_or_reach_payload(self, token, capsys):
    with (
      patch("kanibako.settings.config_io.fetch_persona_secret", return_value=token),
      patch.object(ClaudeTarget, "verify_persona", side_effect=AssertionError),
    ):
      result, payload = self.prepare(self.deliveries("pg"))
    assert result[0] is None and "PG credential" in result[1]
    assert payload.names == () and capsys.readouterr() == ("", "")

  def test_public_fetch_exceptions_are_never_rendered(self, capsys):
    with (
      patch(
        "kanibako.settings.config_io.fetch_persona_secret",
        side_effect=RuntimeError("synthetic-private-value"),
      ),
      patch.object(ClaudeTarget, "verify_persona", side_effect=AssertionError),
    ):
      result, payload = self.prepare(self.deliveries("pg"))
    assert "retrieval failed" in result[1] and "synthetic-private-value" not in result[1]
    assert payload.names == () and capsys.readouterr() == ("", "")

  def test_cancellation_is_not_swallowed(self):
    with patch("kanibako.settings.config_io.fetch_persona_secret", side_effect=KeyboardInterrupt):
      with pytest.raises(KeyboardInterrupt):
        self.prepare(self.deliveries("pg"))

  def test_dynamic_pg_provider_reuses_final_model_and_exact_token(self):
    from kanibako.targets.base import PersonaProbeOutcome

    with (
      patch("kanibako.settings.config_io.fetch_persona_secret", return_value="value"),
      patch.object(
        CodexTarget, "verify_persona", return_value=PersonaProbeOutcome.passed()
      ) as probe,
    ):
      result, payload = self.prepare(
        self.deliveries("pg", "OWN_KEY", node=CODEX_ID), target=CodexTarget(), node=CODEX_ID
      )
    assert result[2].env_key == "OWN_KEY" and result[2].model == "stored-model"
    assert probe.call_args.args == ("https://endpoint.invalid", None, "final-model")
    assert probe.call_args.kwargs == {"env": {"FINAL": "env"}, "token": "value"}
    assert payload.names == ("OWN_KEY",)

  @pytest.mark.parametrize("verdict", ["rejected", "inconclusive", "not-applicable", "raised"])
  @pytest.mark.parametrize("token", ["q", "short", "a'\\\"λ", "synthetic-sensitive-value"])
  def test_exact_token_echoes_are_scrubbed_and_pg_source_is_truthful(self, verdict, token, capsys):
    from kanibako.targets.base import PersonaProbeOutcome, ProbeEvidence
    from kanibako.commands.start import _prepare_resolved_persona_delivery

    logger = MagicMock()
    evidence = ProbeEvidence(
      endpoint="https://endpoint.invalid",
      model="final-model",
      status=401,
      provider_text=f"echo={token}",
      token_by_value=True,
    )
    outcome = (
      PersonaProbeOutcome.rejected(evidence)
      if verdict == "rejected"
      else PersonaProbeOutcome.inconclusive(f"echo={token}", evidence)
      if verdict == "inconclusive"
      else PersonaProbeOutcome.not_applicable(f"echo={token}")
    )
    with (
      patch("kanibako.settings.config_io.fetch_persona_secret", return_value=token),
      patch.object(
        ClaudeTarget,
        "verify_persona",
        side_effect=RuntimeError(token) if verdict == "raised" else None,
        return_value=outcome,
      ),
    ):
      result, payload = _prepare_resolved_persona_delivery(
        NODE_ID,
        self.deliveries("pg"),
        "https://endpoint.invalid",
        logger,
        target=ClaudeTarget(),
        env={},
        cli_env={},
        probe_model="final-model",
        provider_model=None,
      )
    output = capsys.readouterr()
    rendered = str(result[1] or "") + output.out + output.err + repr(logger.debug.call_args_list)
    assert token not in rendered
    assert not any(call.kwargs.get("exc_info") for call in logger.debug.call_args_list)
    if verdict in ("rejected", "inconclusive"):
      assert "persona-grata credential" in rendered and "-e on the command line" not in rendered
    if verdict == "rejected":
      assert result[0] is None and payload.names == () and "HTTP 401" in result[1]
    else:
      assert result[1] is None and payload.values[0].value == token.encode()

  def test_late_fetch_failure_discards_partial_payload_before_probe(self):
    deliveries = self.deliveries("pg")
    deliveries.credential_states["OTHER_KEY"] = self.deliveries(
      "pg", "OTHER_KEY"
    ).credential_states["OTHER_KEY"]
    with (
      patch(
        "kanibako.settings.config_io.fetch_persona_secret",
        side_effect=["first-private-value", RuntimeError("second-private-value")],
      ) as fetch,
      patch.object(ClaudeTarget, "verify_persona", side_effect=AssertionError),
    ):
      result, payload = self.prepare(deliveries)
    assert fetch.call_count == 2
    assert result[0] is None and payload.names == ()
    assert "first-private-value" not in result[1] and "second-private-value" not in result[1]

  @pytest.mark.parametrize("broken", ["missing", "mismatched", "keyless"])
  def test_malformed_final_source_never_fetches(self, broken):
    from dataclasses import replace

    deliveries = self.deliveries("pg")
    state = deliveries.credential_states[VAR]
    source = state.declaration.persona_credential
    declaration = (
      None
      if broken == "missing"
      else self.deliveries("pg", "OTHER_KEY").credential_states["OTHER_KEY"].declaration
      if broken == "mismatched"
      else replace(state.declaration, persona_credential=replace(source, keyless=True))
    )
    deliveries.credential_states[VAR] = replace(state, declaration=declaration)
    with (
      patch("kanibako.settings.config_io.fetch_persona_secret", side_effect=AssertionError),
      patch.object(ClaudeTarget, "verify_persona", side_effect=AssertionError),
    ):
      result, payload = self.prepare(deliveries)
    assert result[0] is None and "invalid PG credential source" in result[1]
    assert payload.names == ()

  def test_keyless_without_var_is_still_probed_without_any_fetch(self):
    from kanibako.targets.base import PersonaProbeOutcome

    with (
      patch("kanibako.settings.config_io.fetch_persona_secret", side_effect=AssertionError),
      patch("persona_grata.AgentStore", side_effect=AssertionError),
      patch.object(
        CodexTarget, "verify_persona", return_value=PersonaProbeOutcome.passed()
      ) as probe,
    ):
      result, payload = self.prepare(
        self.deliveries(None, declared=False, keyless=True), target=CodexTarget(), node=CODEX_ID
      )
    assert result[1] is None and result[2].env_key == "" and payload.names == ()
    assert probe.call_count == 1 and probe.call_args.args[1] is None
    assert "token" not in probe.call_args.kwargs

  def test_old_plugin_is_unverified_without_a_false_cli_source(self, capsys):
    from kanibako.targets.base import PersonaProbeOutcome

    class OldTarget:
      descriptor = ClaudeTarget().descriptor

      def verify_persona(self, endpoint, pointer, model, *, env=None):
        pytest.fail("old plugin must not probe a different token")
        return PersonaProbeOutcome.passed()

    with patch("kanibako.settings.config_io.fetch_persona_secret", return_value="private-value"):
      result, payload = self.prepare(self.deliveries("pg"), target=OldTarget())
    assert result[1] is None and payload.values[0].value == b"private-value"
    output = capsys.readouterr().err
    assert "is not verified" in output and "persona-grata credential" in output
    assert "from -e" not in output and "private-value" not in output

  def test_both_probe_axes_scrub_exact_token_echoes(self):
    from kanibako.targets.base import (
      AccessReport,
      AxisResult,
      GeneralAccess,
      ModelAccess,
      PersonaProbeOutcome,
      PersonaProbeVerdict,
      ProbeEvidence,
    )

    token = "sensitive-value"
    general = ProbeEvidence(
      endpoint="https://endpoint.invalid",
      status=403,
      provider_text=f"general {token}",
      request="GET /models",
      token_by_value=True,
    )
    model = ProbeEvidence(
      endpoint="https://endpoint.invalid",
      model="final-model",
      status=401,
      provider_text=f"model {token}",
      request="POST /messages",
      token_by_value=True,
    )
    outcome = PersonaProbeOutcome(
      PersonaProbeVerdict.REJECTED,
      evidence=model,
      access=AccessReport(
        AxisResult(GeneralAccess.DENIED, general), AxisResult(ModelAccess.DENIED, model)
      ),
    )
    with (
      patch("kanibako.settings.config_io.fetch_persona_secret", return_value=token),
      patch.object(ClaudeTarget, "verify_persona", return_value=outcome),
    ):
      result, payload = self.prepare(self.deliveries("pg"))
    assert result[0] is None and payload.names == () and token not in result[1]
    assert "HTTP 401" in result[1] and "HTTP 403" in result[1]
    assert "persona-grata credential" in result[1] and "-e on the command line" not in result[1]
