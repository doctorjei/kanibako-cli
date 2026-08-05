"""Tests for ``Target.read_persona_settings`` (persona-grata store extraction).

The harness-config extraction seam: each plugin parses ITS OWN rendered config
out of a store entry's ``<pid>/<hid>/`` dir into the harness-neutral
:class:`~kanibako.targets.base.PersonaSettings`, wrapped in a
:class:`~kanibako.targets.base.PersonaReadOutcome`.  Pure reads, fail-soft: an
unusable config never raises and comes back as ``settings=None`` plus a
SPECIFIC ``reject_reason`` naming the file and the cause.  Claude parses
``settings.json`` (``env.ANTHROPIC_BASE_URL`` + top-level ``model``, fixed
``ANTHROPIC_AUTH_TOKEN`` auth var, the REST of ``env`` carried through as
passthrough); codex parses ``config.toml`` (the inverse of the
``CodexModelProvider`` shape kanibako emits — ``base_url``/``env_key``
+ top-level ``model``/``model_provider``).  Goose/no_agent inherit the base
no-reader default: BOTH fields ``None``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kanibako.plugins.claude.target import ClaudeTarget
from kanibako.plugins.codex.target import CodexTarget
from kanibako.plugins.goose.target import GooseTarget
from kanibako.targets.base import PersonaReadOutcome, PersonaSettings, Target
from kanibako.targets.no_agent import NoAgentTarget


def _reject(outcome, *needles: str) -> None:
    """Assert *outcome* is a NAMED reject mentioning each of *needles*."""
    assert outcome.settings is None
    assert outcome.reject_reason, "a reject must carry a specific reason"
    for needle in needles:
        assert needle in outcome.reject_reason, (
            f"reject reason {outcome.reject_reason!r} does not name {needle!r}"
        )


class TestBaseDefault:
    def test_default_is_no_reader_not_a_reject(self, tmp_path):
        # BOTH None = "this harness has no persona reader" — distinct from a
        # present-but-unusable config, which must name its own cause.
        assert NoAgentTarget().read_persona_settings(tmp_path) == (
            PersonaReadOutcome(settings=None, reject_reason=None)
        )

    def test_goose_and_no_agent_inherit_the_default(self):
        for target in (GooseTarget(), NoAgentTarget()):
            assert (
                target.read_persona_settings.__func__
                is Target.read_persona_settings
            ), f"{target.name} should inherit the base no-op"

    def test_claude_and_codex_override(self):
        for target in (ClaudeTarget(), CodexTarget()):
            assert (
                target.read_persona_settings.__func__
                is not Target.read_persona_settings
            ), f"{target.name} must override read_persona_settings"


def _write_claude_settings(config_dir: Path, data) -> None:
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "settings.json").write_text(json.dumps(data))


class TestClaudeReadPersonaSettings:
    def test_well_formed(self, tmp_path):
        _write_claude_settings(tmp_path, {
            "env": {"ANTHROPIC_BASE_URL": "https://api.navigator.example/v1"},
            "model": "gemma4",
        })
        got = ClaudeTarget().read_persona_settings(tmp_path)
        assert got.reject_reason is None
        assert got.settings == PersonaSettings(
            endpoint="https://api.navigator.example/v1",
            model="gemma4",
            auth_env="ANTHROPIC_AUTH_TOKEN",
        )
        assert got.settings.env == {}
        assert got.settings.env_dropped == ()

    def test_model_absent_is_none(self, tmp_path):
        _write_claude_settings(tmp_path, {
            "env": {"ANTHROPIC_BASE_URL": "https://e.example"},
        })
        got = ClaudeTarget().read_persona_settings(tmp_path).settings
        assert got is not None
        assert got.model is None
        assert got.endpoint == "https://e.example"

    def test_non_string_model_is_none(self, tmp_path):
        _write_claude_settings(tmp_path, {
            "env": {"ANTHROPIC_BASE_URL": "https://e.example"},
            "model": 7,
        })
        got = ClaudeTarget().read_persona_settings(tmp_path).settings
        assert got is not None and got.model is None

    def test_extra_env_vars_pass_through(self, tmp_path):
        # The PASSTHROUGH contract: everything in the env block rides along —
        # including a var kanibako has never heard of — EXCEPT the two
        # single-source vars, which travel on their own channels (endpoint
        # field / secret-path bind) and must never be duplicated here.
        _write_claude_settings(tmp_path, {
            "env": {
                "ANTHROPIC_BASE_URL": "https://e.example",
                "ANTHROPIC_AUTH_TOKEN": "sk-should-never-ride-here",
                "ANTHROPIC_SMALL_FAST_MODEL": "gemma4-mini",
                "SOME_NEW_VAR": "whatever-the-store-renders",
            },
            "model": "gemma4",
        })
        got = ClaudeTarget().read_persona_settings(tmp_path).settings
        assert got.env == {
            "ANTHROPIC_SMALL_FAST_MODEL": "gemma4-mini",
            "SOME_NEW_VAR": "whatever-the-store-renders",
        }
        assert got.env_dropped == ()

    def test_non_string_env_values_are_reported_not_silently_dropped(self, tmp_path):
        # A JSON number/bool/null cannot be delivered as an env value (it would
        # be str()'d into a Python repr) — the NAME is reported instead.
        _write_claude_settings(tmp_path, {
            "env": {
                "ANTHROPIC_BASE_URL": "https://e.example",
                "KEPT": "yes",
                "A_NUMBER": 7,
                "A_NULL": None,
                "A_BOOL": True,
            },
        })
        got = ClaudeTarget().read_persona_settings(tmp_path).settings
        assert got.env == {"KEPT": "yes"}
        assert got.env_dropped == ("A_BOOL", "A_NULL", "A_NUMBER")  # sorted names

    def test_missing_base_url_is_unusable(self, tmp_path):
        _write_claude_settings(tmp_path, {"env": {"OTHER": "x"}, "model": "m"})
        _reject(
            ClaudeTarget().read_persona_settings(tmp_path),
            "ANTHROPIC_BASE_URL", "settings.json",
        )

    def test_empty_base_url_is_unusable(self, tmp_path):
        _write_claude_settings(tmp_path, {"env": {"ANTHROPIC_BASE_URL": ""}})
        _reject(
            ClaudeTarget().read_persona_settings(tmp_path),
            "ANTHROPIC_BASE_URL", "settings.json",
        )

    def test_non_dict_env(self, tmp_path):
        _write_claude_settings(tmp_path, {"env": ["not", "a", "dict"]})
        _reject(
            ClaudeTarget().read_persona_settings(tmp_path), "env", "settings.json",
        )

    def test_non_object_document(self, tmp_path):
        _write_claude_settings(tmp_path, ["not", "an", "object"])
        _reject(
            ClaudeTarget().read_persona_settings(tmp_path),
            "JSON object", "settings.json",
        )

    def test_malformed_json(self, tmp_path):
        (tmp_path / "settings.json").write_text("{not json")
        _reject(
            ClaudeTarget().read_persona_settings(tmp_path),
            "valid JSON", "settings.json",
        )

    def test_absent_file(self, tmp_path):
        # An ABSENT file and a MALFORMED one are different user problems and
        # must not collapse into one reason.
        _reject(
            ClaudeTarget().read_persona_settings(tmp_path),
            "unreadable", "settings.json",
        )


def _write_codex_config(config_dir: Path, text: str) -> None:
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.toml").write_text(text)


_NAVIGATOR_TABLE = """
[model_providers.navigator]
name = "navigator"
base_url = "https://api.navigator.example/v1"
wire_api = "responses"
env_key = "NAVIGATOR_API_KEY"
"""


class TestCodexReadPersonaSettings:
    def test_well_formed_single_table(self, tmp_path):
        _write_codex_config(tmp_path, 'model = "gemma4"\n' + _NAVIGATOR_TABLE)
        got = CodexTarget().read_persona_settings(tmp_path)
        assert got.reject_reason is None
        assert got.settings == PersonaSettings(
            endpoint="https://api.navigator.example/v1",
            model="gemma4",
            auth_env="NAVIGATOR_API_KEY",
        )
        # A codex config carries no env block -> the defaults stand.
        assert got.settings.env == {}
        assert got.settings.env_dropped == ()

    def test_model_provider_selects_among_tables(self, tmp_path):
        _write_codex_config(tmp_path, (
            'model = "gemma4"\nmodel_provider = "navigator"\n'
            + _NAVIGATOR_TABLE
            + '\n[model_providers.other]\n'
            'base_url = "https://other.example"\nenv_key = "OTHER_KEY"\n'
        ))
        got = CodexTarget().read_persona_settings(tmp_path).settings
        assert got is not None
        assert got.endpoint == "https://api.navigator.example/v1"
        assert got.auth_env == "NAVIGATOR_API_KEY"

    def test_multiple_tables_without_selector_is_ambiguous(self, tmp_path):
        _write_codex_config(tmp_path, (
            _NAVIGATOR_TABLE
            + '\n[model_providers.other]\n'
            'base_url = "https://other.example"\nenv_key = "OTHER_KEY"\n'
        ))
        _reject(
            CodexTarget().read_persona_settings(tmp_path),
            "model_provider", "config.toml",
        )

    def test_selected_entry_is_not_a_table(self, tmp_path):
        _write_codex_config(tmp_path, 'model_providers = { navigator = "nope" }\n')
        _reject(
            CodexTarget().read_persona_settings(tmp_path),
            "not a table", "config.toml",
        )

    def test_stale_selector_falls_back_to_single_table(self, tmp_path):
        _write_codex_config(
            tmp_path, 'model_provider = "gone"\n' + _NAVIGATOR_TABLE,
        )
        got = CodexTarget().read_persona_settings(tmp_path).settings
        assert got is not None
        assert got.auth_env == "NAVIGATOR_API_KEY"

    def test_model_absent_is_none(self, tmp_path):
        _write_codex_config(tmp_path, _NAVIGATOR_TABLE)
        got = CodexTarget().read_persona_settings(tmp_path).settings
        assert got is not None and got.model is None

    def test_missing_env_key_is_unusable(self, tmp_path):
        _write_codex_config(tmp_path, (
            '[model_providers.navigator]\n'
            'base_url = "https://api.navigator.example/v1"\n'
        ))
        _reject(
            CodexTarget().read_persona_settings(tmp_path),
            "env_key", "config.toml",
        )

    def test_missing_base_url_is_unusable(self, tmp_path):
        _write_codex_config(tmp_path, (
            '[model_providers.navigator]\nenv_key = "NAVIGATOR_API_KEY"\n'
        ))
        _reject(
            CodexTarget().read_persona_settings(tmp_path),
            "base_url", "config.toml",
        )

    def test_no_provider_table(self, tmp_path):
        _write_codex_config(tmp_path, 'model = "gemma4"\n')
        _reject(
            CodexTarget().read_persona_settings(tmp_path),
            "model_providers", "config.toml",
        )

    def test_malformed_toml(self, tmp_path):
        _write_codex_config(tmp_path, "[model_providers.navigator\nbroken")
        _reject(
            CodexTarget().read_persona_settings(tmp_path),
            "valid TOML", "config.toml",
        )

    def test_absent_file(self, tmp_path):
        _reject(
            CodexTarget().read_persona_settings(tmp_path),
            "unreadable", "config.toml",
        )


# ---------------------------------------------------------------------------
# verify_persona: the minimal real-completion probe (tri-state verdict)
# ---------------------------------------------------------------------------


class _ProbeServer:
    """A scriptable local HTTP endpoint for verify_persona probes.

    Records the last request (path, auth header, JSON body) and answers with a
    scripted status — or hangs (timeout case).  Runs on 127.0.0.1:<ephemeral>.
    """

    def __init__(self, status: int = 200, hang: float = 0.0):
        import json
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        outer = self
        self.status = status
        self.hang = hang
        self.last_path: str | None = None
        self.last_auth: str | None = None
        self.last_version: str | None = None
        self.last_body: dict | None = None

        class _Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802 (http.server API name)
                import time

                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length)
                outer.last_path = self.path
                outer.last_auth = self.headers.get("Authorization")
                outer.last_version = self.headers.get("anthropic-version")
                try:
                    outer.last_body = json.loads(raw)
                except ValueError:
                    outer.last_body = None
                if outer.hang:
                    time.sleep(outer.hang)
                self.send_response(outer.status)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"{}")

            def log_message(self, *args):  # silence test output
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.endpoint = f"http://127.0.0.1:{self._server.server_port}"
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True,
        )
        self._thread.start()

    def close(self):
        self._server.shutdown()
        self._server.server_close()


@pytest.fixture
def token_file(tmp_path):
    tok = tmp_path / "token"
    tok.write_text("sk-test-secret\n")
    return tok


def _probe(target, server, token_file, *, model="gemma4", timeout=5.0):
    return target.verify_persona(
        server.endpoint, token_file, model, timeout=timeout,
    )


class TestVerifyPersonaBase:
    def test_base_default_is_unverifiable(self, tmp_path):
        assert (
            NoAgentTarget().verify_persona("https://e.example", tmp_path, "m")
            is None
        )

    def test_goose_inherits_unverifiable(self, tmp_path):
        assert (
            GooseTarget().verify_persona("https://e.example", tmp_path, "m")
            is None
        )


class TestVerifyPersonaWire:
    """Both harness probes against a live local endpoint: verdicts + wire shape."""

    @pytest.mark.parametrize("target_cls,path,versioned", [
        (ClaudeTarget, "/v1/messages", True),
        (CodexTarget, "/responses", False),
    ])
    def test_accepting_endpoint_passes(
        self, token_file, target_cls, path, versioned,
    ):
        server = _ProbeServer(status=200)
        try:
            assert _probe(target_cls(), server, token_file) is True
        finally:
            server.close()
        assert server.last_path == path
        assert server.last_auth == "Bearer sk-test-secret"
        assert (server.last_version is not None) is versioned
        assert server.last_body is not None
        assert server.last_body["model"] == "gemma4"

    @pytest.mark.parametrize("status", [401, 403])
    @pytest.mark.parametrize("target_cls", [ClaudeTarget, CodexTarget])
    def test_auth_reject_fails(self, token_file, target_cls, status):
        server = _ProbeServer(status=status)
        try:
            assert _probe(target_cls(), server, token_file) is False
        finally:
            server.close()

    @pytest.mark.parametrize("status", [404, 429, 500])
    @pytest.mark.parametrize("target_cls", [ClaudeTarget, CodexTarget])
    def test_ambiguous_status_is_unverifiable(self, token_file, target_cls, status):
        server = _ProbeServer(status=status)
        try:
            assert _probe(target_cls(), server, token_file) is None
        finally:
            server.close()

    @pytest.mark.parametrize("target_cls", [ClaudeTarget, CodexTarget])
    def test_unreachable_endpoint_is_unverifiable(self, token_file, target_cls):
        server = _ProbeServer()
        server.close()  # closed port -> connection refused
        assert _probe(target_cls(), server, token_file) is None

    @pytest.mark.parametrize("target_cls", [ClaudeTarget, CodexTarget])
    def test_timeout_is_unverifiable(self, token_file, target_cls):
        server = _ProbeServer(status=200, hang=2.0)
        try:
            assert (
                _probe(target_cls(), server, token_file, timeout=0.3) is None
            )
        finally:
            server.close()

    @pytest.mark.parametrize("target_cls", [ClaudeTarget, CodexTarget])
    def test_garbage_response_is_unverifiable(self, token_file, target_cls):
        import socket
        import threading

        # A raw socket that answers with non-HTTP garbage then closes.
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        port = sock.getsockname()[1]

        def _serve_garbage():
            conn, _ = sock.accept()
            conn.recv(4096)
            conn.sendall(b"utter garbage\r\n\r\n")
            conn.close()

        thread = threading.Thread(target=_serve_garbage, daemon=True)
        thread.start()
        try:
            target = target_cls()
            assert target.verify_persona(
                f"http://127.0.0.1:{port}", token_file, "gemma4", timeout=2.0,
            ) is None
        finally:
            sock.close()

    @pytest.mark.parametrize("target_cls", [ClaudeTarget, CodexTarget])
    def test_redirect_not_followed_and_token_not_resent(
        self, token_file, target_cls,
    ):
        # TOKEN HYGIENE: urllib re-sends every header (the Authorization
        # bearer) when following a redirect — the probe must refuse them.  A
        # 3xx is an ambiguous answer (-> unverifiable) and the redirect target
        # must never see a request.
        import socket
        import threading

        leak_target = _ProbeServer(status=200)
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        port = sock.getsockname()[1]

        def _serve_redirect():
            conn, _ = sock.accept()
            conn.recv(65536)
            conn.sendall(
                b"HTTP/1.1 302 Found\r\n"
                b"Location: " + leak_target.endpoint.encode() + b"/v1/messages\r\n"
                b"Content-Length: 0\r\n\r\n"
            )
            conn.close()

        threading.Thread(target=_serve_redirect, daemon=True).start()
        try:
            verdict = target_cls().verify_persona(
                f"http://127.0.0.1:{port}", token_file, "gemma4", timeout=2.0,
            )
        finally:
            sock.close()
            leak_target.close()
        assert verdict is None  # 302 = ambiguous, not pass/fail
        assert leak_target.last_path is None  # bearer never left for the target
        assert leak_target.last_auth is None

    @pytest.mark.parametrize("target_cls", [ClaudeTarget, CodexTarget])
    def test_missing_token_file_is_unverifiable(self, tmp_path, target_cls):
        server = _ProbeServer(status=200)
        try:
            assert _probe(
                target_cls(), server, tmp_path / "nonexistent-token",
            ) is None
            assert server.last_path is None  # no request was even sent
        finally:
            server.close()

    @pytest.mark.parametrize("target_cls", [ClaudeTarget, CodexTarget])
    def test_empty_token_file_is_unverifiable(self, tmp_path, target_cls):
        tok = tmp_path / "token"
        tok.write_text("   \n")
        server = _ProbeServer(status=200)
        try:
            assert _probe(target_cls(), server, tok) is None
            assert server.last_path is None
        finally:
            server.close()

    @pytest.mark.parametrize("target_cls", [ClaudeTarget, CodexTarget])
    def test_no_model_is_unverifiable(self, token_file, target_cls):
        server = _ProbeServer(status=200)
        try:
            assert _probe(target_cls(), server, token_file, model=None) is None
            assert server.last_path is None  # a real call needs a model id
        finally:
            server.close()

    def test_claude_sends_anthropic_version_header_and_one_token(self, token_file):
        server = _ProbeServer(status=200)
        try:
            _probe(ClaudeTarget(), server, token_file)
        finally:
            server.close()
        assert server.last_body == {
            "model": "gemma4",
            "max_tokens": 1,
            "messages": [{"role": "user", "content": "ping"}],
        }

    def test_codex_sends_responses_wire_body(self, token_file):
        server = _ProbeServer(status=200)
        try:
            _probe(CodexTarget(), server, token_file)
        finally:
            server.close()
        assert server.last_body == {
            "model": "gemma4",
            "input": "ping",
            "max_output_tokens": 16,
        }
