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
    from kanibako.settings.agent_select import AgentSelection
    from kanibako.settings.settings_launch import AuthSource

    auth = AuthSource(tier="global", global_enabled=True, workset_enabled=False,
                      global_sync=False, workset_source=None)
    real = ClaudeTarget()
    with start_mocks() as m, patch(
        "kanibako.commands.start._resolve_box_launch_decisions",
        return_value=(auth, endpoint, "m-1", secrets),
    ):
        m.proj.is_new = False
        m.resolve_agent.return_value = AgentSelection(node=NODE_ID, source="settings")
        m.target.name = "claude"
        m.target.descriptor = real.descriptor
        m.target.verify_persona = real.verify_persona
        return _run_container(
            project_dir=None, entrypoint=None, image_override=None,
            new_session=False, safe_mode=False, resume_mode=False, extra_args=[],
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
        self, start_mocks, server, file_token, capsys,
    ):
        """(b) Both: the box gets ``-e``, so the probe must too — never the file's."""
        _launch(start_mocks, {VAR: str(file_token)}, [f"{VAR}={E_TOKEN}"], server.endpoint)
        sent = _authorizations(server) - {None}
        assert sent == {f"Bearer {E_TOKEN}"}
        assert FILE_TOKEN not in capsys.readouterr().err

    def test_a_revoked_file_token_does_not_refuse_a_good_e(
        self, start_mocks, file_token, capsys,
    ):
        """(b) end to end: the endpoint refuses the file token and accepts ``-e``'s."""
        srv = _TokenAwareServer(good=E_TOKEN)
        try:
            _launch(start_mocks, {VAR: str(file_token)}, [f"{VAR}={E_TOKEN}"], srv.endpoint)
        finally:
            srv.close()
        assert "cannot be loaded" not in capsys.readouterr().err

    def test_without_e_the_file_token_is_sent_as_before(
        self, start_mocks, server, file_token,
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
        agent_id, secrets, endpoint, logger, target=target, keyspace_model=model,
        probe=True, cli_env=cli_env,
    )


class TestEnvHarness:
    def test_an_unusable_file_with_e_passes(self, server, tmp_path):
        """``-e`` passes the gate whatever the file's state; the file is never mounted."""
        ep, err, _ = _preflight(
            NODE_ID, {VAR: str(tmp_path / "absent")}, server.endpoint, ClaudeTarget(),
            cli_env={VAR: E_TOKEN},
        )
        assert (ep, err) == (server.endpoint, None)
        assert _authorizations(server) - {None} == {f"Bearer {E_TOKEN}"}

    def test_a_keyless_declaration_with_e_sends_the_e_token(self, server):
        _preflight(NODE_ID, {VAR: None}, server.endpoint, ClaudeTarget(),
                   cli_env={VAR: E_TOKEN})
        assert _authorizations(server) - {None} == {f"Bearer {E_TOKEN}"}

    @pytest.mark.parametrize("value", ["", "   "])
    def test_an_empty_e_changes_nothing(self, server, file_token, value):
        """An empty ``-e`` is no token: the gate and the probe act on the file as before."""
        ep, err, _ = _preflight(NODE_ID, {VAR: str(file_token)}, server.endpoint,
                                ClaudeTarget(), cli_env={VAR: value})
        assert err is None and ep == server.endpoint
        assert _authorizations(server) - {None} == {f"Bearer {FILE_TOKEN}"}
        _ep, err, _ = _preflight(NODE_ID, {}, server.endpoint, ClaudeTarget(),
                                 cli_env={VAR: value})
        assert err is not None and "no usable auth token" in err

    def test_another_vars_e_does_not_satisfy_the_gate(self, server):
        _ep, err, _ = _preflight(NODE_ID, {}, server.endpoint, ClaudeTarget(),
                                 cli_env={"SOME_OTHER_VAR": E_TOKEN})
        assert err is not None and "no usable auth token" in err
        assert server.log == []


class TestConfigFileHarness:
    KEY = "NAVIGATOR_API_KEY"

    def test_an_unusable_file_with_e_passes_and_sends_e(self, server, tmp_path):
        ep, err, provider = _preflight(
            CODEX_ID, {self.KEY: str(tmp_path / "absent")}, server.endpoint, CodexTarget(),
            cli_env={self.KEY: E_TOKEN},
        )
        assert err is None and ep == server.endpoint
        assert provider is not None and provider.env_key == self.KEY
        assert _authorizations(server) - {None} == {f"Bearer {E_TOKEN}"}

    def test_e_beats_a_usable_file(self, server, file_token):
        _preflight(CODEX_ID, {self.KEY: str(file_token)}, server.endpoint, CodexTarget(),
                   cli_env={self.KEY: E_TOKEN})
        assert _authorizations(server) - {None} == {f"Bearer {E_TOKEN}"}

    def test_the_key_must_still_be_declared(self, server):
        """The declared name IS the provider ``env_key``; ``-e`` alone cannot name it."""
        _ep, err, _ = _preflight(CODEX_ID, {}, server.endpoint, CodexTarget(),
                                 cli_env={self.KEY: E_TOKEN})
        assert err is not None and "none was found" in err


class TestAPluginWithoutTheTokenParameter:
    def test_it_is_called_as_before_when_e_gives_no_token(self, file_token):
        """``token=`` is passed only when set: an older plugin signature keeps working."""
        from kanibako.targets.base import PersonaProbeOutcome

        calls = []

        class _Old:
            descriptor = ClaudeTarget().descriptor

            def verify_persona(self, endpoint, token_path, model, *, env=None,
                               timeout=5.0):
                calls.append(token_path)
                return PersonaProbeOutcome.passed()

        logger = MagicMock()
        ep, err, _ = _preflight(NODE_ID, {VAR: str(file_token)}, "https://e.example",
                                _Old(), cli_env=None, logger=logger)
        assert (ep, err) == ("https://e.example", None)
        assert [str(p) for p in calls] == [str(file_token)]

    def test_with_e_it_is_not_probed_and_the_launch_says_so(self, file_token, capsys):
        """With ``-e`` an older plugin is skipped, not handed ``token=`` and failed."""
        from kanibako.targets.base import PersonaProbeOutcome

        calls = []

        class _Old:
            descriptor = ClaudeTarget().descriptor

            def verify_persona(self, endpoint, token_path, model, *, env=None,
                               timeout=5.0):
                calls.append(token_path)
                return PersonaProbeOutcome.passed()

        logger = MagicMock()
        ep, err, _ = _preflight(NODE_ID, {VAR: str(file_token)}, "https://e.example",
                                _Old(), cli_env={VAR: E_TOKEN}, logger=logger)
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
            def verify_persona(self, endpoint, token_path, model, *, env=None,
                               timeout=5.0, token=None):
                got.append((token_path, token))
                return PersonaProbeOutcome.passed()
        else:
            def verify_persona(self, endpoint, token_path, model, **kwargs):
                got.append((token_path, kwargs.get("token")))
                return PersonaProbeOutcome.passed()
        _New.verify_persona = verify_persona

        ep, err, _ = _preflight(NODE_ID, {VAR: str(file_token)}, "https://e.example",
                                _New(), cli_env={VAR: E_TOKEN})
        assert (ep, err) == ("https://e.example", None)
        assert got == [(None, E_TOKEN)]


# ---------------------------------------------------------------------------
# The token VALUE never leaves the request.
# ---------------------------------------------------------------------------


class TestTheETokenNeverLeaks:
    @pytest.mark.parametrize("agent_id,target_cls,var", [
        (NODE_ID, ClaudeTarget, VAR),
        (CODEX_ID, CodexTarget, "NAVIGATOR_API_KEY"),
    ])
    def test_a_refusal_names_e_and_never_the_value(
        self, agent_id, target_cls, var, capsys, caplog,
    ):
        """The provider echoes the token back in a 401; nothing prints or logs it."""
        import logging

        srv = _ProbeServer(
            status=401, body=f'{{"error": "invalid key {E_TOKEN}"}}'.encode(),
        )
        logger = logging.getLogger("kanibako.test.etoken")
        caplog.set_level(logging.DEBUG)
        try:
            secrets = {var: None} if target_cls is CodexTarget else {}
            _ep, err, _ = _preflight(agent_id, secrets, srv.endpoint, target_cls(),
                                     cli_env={var: E_TOKEN}, logger=logger)
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
