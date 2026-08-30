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
from kanibako.targets.base import (
    PersonaProbeVerdict,
    PersonaReadOutcome,
    PersonaSettings,
    Target,
)
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
# verify_persona: the minimal real-completion probe (four-arm PersonaProbeOutcome)
# ---------------------------------------------------------------------------


class _ProbeServer:
    """A scriptable local HTTP endpoint for verify_persona probes.

    Records the last request (path, auth header, JSON body) and answers with a
    scripted status — or hangs (timeout case).  Runs on 127.0.0.1:<ephemeral>.
    """

    def __init__(self, status: int = 200, hang: float = 0.0, body: bytes = b"{}"):
        import json
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        outer = self
        self.status = status
        self.hang = hang
        self.body = body
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
                self.send_header("Content-Length", str(len(outer.body)))
                self.end_headers()
                self.wfile.write(outer.body)

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
    """A harness with NO probe answers NOT_APPLICABLE, naming itself.

    ⚑ Not ``INCONCLUSIVE``: nothing was attempted and nothing ever will be, so a
    caller must stay SILENT.  Collapsing the two is what made every goose persona
    launch warn "could not verify the endpoint" forever, un-actionably.
    """

    def test_base_default_is_not_applicable(self, tmp_path):
        outcome = NoAgentTarget().verify_persona(
            "https://e.example", tmp_path, "m",
        )
        assert outcome.verdict is PersonaProbeVerdict.NOT_APPLICABLE
        assert "no persona verify probe" in outcome.reason

    def test_goose_inherits_not_applicable(self, tmp_path):
        outcome = GooseTarget().verify_persona(
            "https://e.example", tmp_path, "m",
        )
        assert outcome.verdict is PersonaProbeVerdict.NOT_APPLICABLE
        assert "goose" in outcome.reason


class TestVerifyPersonaWire:
    """Both harness probes against a live local endpoint: outcomes + wire shape."""

    @pytest.mark.parametrize("target_cls,path,versioned", [
        (ClaudeTarget, "/v1/messages", True),
        (CodexTarget, "/responses", False),
    ])
    def test_accepting_endpoint_passes(
        self, token_file, target_cls, path, versioned,
    ):
        server = _ProbeServer(status=200)
        try:
            outcome = _probe(target_cls(), server, token_file)
            assert outcome.verdict is PersonaProbeVerdict.PASS
        finally:
            server.close()
        assert server.last_path == path
        assert server.last_auth == "Bearer sk-test-secret"
        assert (server.last_version is not None) is versioned
        assert server.last_body is not None
        assert server.last_body["model"] == "gemma4"

    @pytest.mark.parametrize("status", [401, 403])
    @pytest.mark.parametrize("target_cls", [ClaudeTarget, CodexTarget])
    def test_auth_reject_is_REJECTED(self, token_file, target_cls, status):
        server = _ProbeServer(status=status)
        try:
            outcome = _probe(target_cls(), server, token_file)
            assert outcome.verdict is PersonaProbeVerdict.REJECTED
        finally:
            server.close()

    @pytest.mark.parametrize("status", [404, 429, 500])
    @pytest.mark.parametrize("target_cls", [ClaudeTarget, CodexTarget])
    def test_ambiguous_status_is_INCONCLUSIVE(self, token_file, target_cls, status):
        server = _ProbeServer(status=status)
        try:
            outcome = _probe(target_cls(), server, token_file)
            assert outcome.verdict is PersonaProbeVerdict.INCONCLUSIVE
            assert str(status) in outcome.reason
        finally:
            server.close()

    @pytest.mark.parametrize("target_cls", [ClaudeTarget, CodexTarget])
    def test_unreachable_endpoint_is_INCONCLUSIVE(self, token_file, target_cls):
        server = _ProbeServer()
        server.close()  # closed port -> connection refused
        outcome = _probe(target_cls(), server, token_file)
        assert outcome.verdict is PersonaProbeVerdict.INCONCLUSIVE
        assert "could not be reached" in outcome.reason

    @pytest.mark.parametrize("target_cls", [ClaudeTarget, CodexTarget])
    def test_timeout_is_INCONCLUSIVE(self, token_file, target_cls):
        server = _ProbeServer(status=200, hang=2.0)
        try:
            outcome = _probe(target_cls(), server, token_file, timeout=0.3)
            assert outcome.verdict is PersonaProbeVerdict.INCONCLUSIVE
        finally:
            server.close()

    @pytest.mark.parametrize("target_cls", [ClaudeTarget, CodexTarget])
    def test_garbage_response_is_INCONCLUSIVE(self, token_file, target_cls):
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
            outcome = target.verify_persona(
                f"http://127.0.0.1:{port}", token_file, "gemma4", timeout=2.0,
            )
            assert outcome.verdict is PersonaProbeVerdict.INCONCLUSIVE
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
            outcome = target_cls().verify_persona(
                f"http://127.0.0.1:{port}", token_file, "gemma4", timeout=2.0,
            )
        finally:
            sock.close()
            leak_target.close()
        # 302 = ambiguous, not pass/fail.
        assert outcome.verdict is PersonaProbeVerdict.INCONCLUSIVE
        assert leak_target.last_path is None  # bearer never left for the target
        assert leak_target.last_auth is None

    @pytest.mark.parametrize("target_cls", [ClaudeTarget, CodexTarget])
    def test_missing_token_file_is_NOT_APPLICABLE(self, tmp_path, target_cls):
        """No token to send = nothing was learned; the user cannot act on it."""
        server = _ProbeServer(status=200)
        try:
            outcome = _probe(
                target_cls(), server, tmp_path / "nonexistent-token",
            )
            assert outcome.verdict is PersonaProbeVerdict.NOT_APPLICABLE
            assert "could not be read" in outcome.reason
            assert server.last_path is None  # no request was even sent
        finally:
            server.close()

    @pytest.mark.parametrize("target_cls", [ClaudeTarget, CodexTarget])
    def test_empty_token_file_is_NOT_APPLICABLE(self, tmp_path, target_cls):
        server = _ProbeServer(status=200)
        empty = tmp_path / "empty-token"
        empty.write_text("   \n")
        try:
            outcome = _probe(target_cls(), server, empty)
            assert outcome.verdict is PersonaProbeVerdict.NOT_APPLICABLE
            assert "is empty" in outcome.reason
            assert server.last_path is None
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


class TestVerifyPersonaWithoutAModel:
    """⚑ A persona that names NO model is PROBED — with the key OMITTED.

    Jei's ruling: a persona endpoint is a third-party anthropic-/OpenAI-compatible
    provider, not the reference API, and such a server may serve exactly one model
    or apply its own default.  So "no model" is not a permanent can't-probe — it is
    UNKNOWN UNTIL WE ASK.  Declining to ask would let a DEAD token sail past the
    launch gate and 401 inside the box, which is the one protection the per-launch
    probe was added to give.

    ⚑ And never a SUBSTITUTE.  A server with a hardwired model can REJECT an id it
    does not serve, so injecting a plausible default (the harness floor, a
    well-known id, anything) risks a FALSE ``REJECTED`` — a hard error that would
    refuse a working box.  The probe verifies the TOKEN, not the model.
    """

    @pytest.mark.parametrize("target_cls,required_keys", [
        (ClaudeTarget, ("max_tokens", "messages")),
        (CodexTarget, ("input", "max_output_tokens")),
    ])
    def test_the_request_body_carries_NO_model_key(
        self, token_file, target_cls, required_keys,
    ):
        """⚑ Do not "helpfully" restore a fallback value here.

        Not an empty string, not a default id, not the descriptor floor: the key
        is ABSENT, and everything else the call needs is still present.
        """
        server = _ProbeServer(status=200)
        try:
            outcome = _probe(target_cls(), server, token_file, model=None)
        finally:
            server.close()
        assert outcome.verdict is PersonaProbeVerdict.PASS
        assert server.last_body is not None
        assert "model" not in server.last_body
        for key in required_keys:
            assert key in server.last_body

    @pytest.mark.parametrize("status", [401, 403])
    @pytest.mark.parametrize("target_cls", [ClaudeTarget, CodexTarget])
    def test_a_model_less_persona_still_reports_an_auth_REJECT(
        self, token_file, target_cls, status,
    ):
        """⚑ The regression this whole arm exists to prevent.

        An auth reject is an auth reject whether or not a model was named.  If a
        model-less persona were never probed, a dead token would reach the box.
        """
        server = _ProbeServer(status=status)
        try:
            outcome = _probe(target_cls(), server, token_file, model=None)
            assert outcome.verdict is PersonaProbeVerdict.REJECTED
        finally:
            server.close()

    @pytest.mark.parametrize("status", [400, 422])
    @pytest.mark.parametrize("target_cls", [ClaudeTarget, CodexTarget])
    def test_a_model_required_answer_is_NOT_APPLICABLE(
        self, token_file, target_cls, status,
    ):
        """The endpoint DOES need a model: we learned nothing about the token.

        Silent, not a warning and never a refusal — the harness may still supply
        its own default at runtime.
        """
        server = _ProbeServer(status=status)
        try:
            outcome = _probe(target_cls(), server, token_file, model=None)
            assert outcome.verdict is PersonaProbeVerdict.NOT_APPLICABLE
            assert "requires a model" in outcome.reason
        finally:
            server.close()

    @pytest.mark.parametrize("status", [400, 422])
    @pytest.mark.parametrize("target_cls", [ClaudeTarget, CodexTarget])
    def test_the_same_status_stays_INCONCLUSIVE_when_a_model_WAS_named(
        self, token_file, target_cls, status,
    ):
        """The model-required reading is gated on OMITTING the field.

        With a model named, a 400 is just a malformed-request answer like any
        other — it must keep warning, not go quiet.
        """
        server = _ProbeServer(status=status)
        try:
            outcome = _probe(target_cls(), server, token_file, model="gemma4")
            assert outcome.verdict is PersonaProbeVerdict.INCONCLUSIVE
        finally:
            server.close()

    @pytest.mark.parametrize("target_cls", [ClaudeTarget, CodexTarget])
    def test_an_unreachable_endpoint_still_warns_for_a_model_less_persona(
        self, token_file, target_cls,
    ):
        """The real signal must survive the fix that silenced the false ones."""
        server = _ProbeServer()
        server.close()  # closed port -> connection refused
        outcome = _probe(target_cls(), server, token_file, model=None)
        assert outcome.verdict is PersonaProbeVerdict.INCONCLUSIVE

    @pytest.mark.parametrize("target_cls", [ClaudeTarget, CodexTarget])
    def test_an_empty_model_string_is_treated_as_absent(
        self, token_file, target_cls,
    ):
        """``""`` is not a model id; it must be omitted, never sent as empty."""
        server = _ProbeServer(status=200)
        try:
            _probe(target_cls(), server, token_file, model="")
        finally:
            server.close()
        assert server.last_body is not None
        assert "model" not in server.last_body


class TestVerifyPersonaWithoutAToken:
    """⚑ A persona whose ``secret_path`` key is PRESENT-null is PROBED —
    with the ``Authorization`` header OMITTED (2026-08-17 ruling).

    A self-hosted endpoint may genuinely require no bearer credential; sending
    the request bare and letting the SERVER decide is the same "unknown until
    we ask" reasoning ``TestVerifyPersonaWithoutAModel`` already applies to a
    model-less persona, and never a placeholder credential — a hardwired-auth
    server can reject one it does not serve, and a false ``REJECTED`` is a hard
    error that would refuse a working box.
    """

    @pytest.mark.parametrize("target_cls,versioned", [
        (ClaudeTarget, True), (CodexTarget, False),
    ])
    def test_the_request_carries_NO_authorization_header(
        self, target_cls, versioned,
    ):
        """⚑ Do not "helpfully" substitute anything here either."""
        server = _ProbeServer(status=200)
        try:
            outcome = target_cls().verify_persona(
                server.endpoint, None, "gemma4", timeout=5.0,
            )
        finally:
            server.close()
        assert outcome.verdict is PersonaProbeVerdict.PASS
        assert server.last_auth is None
        # The anthropic-version header (claude only) rides independently of auth.
        assert (server.last_version is not None) is versioned
        assert server.last_body is not None
        assert server.last_body["model"] == "gemma4"

    @pytest.mark.parametrize("status", [401, 403])
    @pytest.mark.parametrize("target_cls", [ClaudeTarget, CodexTarget])
    def test_a_keyless_declaration_the_server_disagrees_with_is_REJECTED(
        self, target_cls, status,
    ):
        """The server said it DOES need auth — a genuine, useful REJECTED, not
        suppressed just because the user believed the endpoint was keyless.
        """
        server = _ProbeServer(status=status)
        try:
            outcome = target_cls().verify_persona(
                server.endpoint, None, "gemma4", timeout=5.0,
            )
            assert outcome.verdict is PersonaProbeVerdict.REJECTED
        finally:
            server.close()

    @pytest.mark.parametrize("target_cls", [ClaudeTarget, CodexTarget])
    def test_an_unreachable_endpoint_still_warns_with_no_token(
        self, target_cls,
    ):
        server = _ProbeServer()
        server.close()  # closed port -> connection refused
        outcome = target_cls().verify_persona(
            server.endpoint, None, "gemma4", timeout=2.0,
        )
        assert outcome.verdict is PersonaProbeVerdict.INCONCLUSIVE

    @pytest.mark.parametrize("target_cls", [ClaudeTarget, CodexTarget])
    def test_no_token_and_no_model_together_still_probes(self, target_cls):
        """Both the token AND the model may be declared unneeded at once — the
        two omissions are independent, and neither one skips the probe.
        """
        server = _ProbeServer(status=200)
        try:
            outcome = target_cls().verify_persona(
                server.endpoint, None, None, timeout=5.0,
            )
        finally:
            server.close()
        assert outcome.verdict is PersonaProbeVerdict.PASS
        assert server.last_auth is None
        assert server.last_body is not None
        assert "model" not in server.last_body


class TestVerifyPersonaResolvesATierAlias:
    """⚑ THE PROBE MUST SEND WHAT THE BOX WILL SEND.

    MEASURED, 2026-08-30: a persona resolving ``model: sonnet`` was probed with
    ``sonnet`` on the wire; the endpoint answered 403 "team not allowed to access
    model" and kanibako refused the launch, blaming a token that was perfectly
    valid.  In the box, Claude Code reads that alias through
    ``ANTHROPIC_DEFAULT_SONNET_MODEL`` and sends ``gemma-4-31b-it``, which the same
    endpoint serves with a 200.  The probe was asking a question the box never asks.

    ⚑ This resolves the USER's OWN mapping and never invents one — the two rules of
    ``procedures/persona-resolution-model.md`` (omit an absent model; never
    substitute a placeholder id) are untouched below.
    """

    def test_an_alias_is_resolved_through_the_env_var_before_the_send(
        self, token_file,
    ):
        """(Mutation: send *model* instead of the resolved id → ``sonnet`` on the
        wire → RED.)"""
        server = _ProbeServer(status=200)
        try:
            outcome = ClaudeTarget().verify_persona(
                server.endpoint, token_file, "sonnet",
                env={"ANTHROPIC_DEFAULT_SONNET_MODEL": "gemma-4-31b-it"},
                timeout=5.0,
            )
        finally:
            server.close()
        assert outcome.verdict is PersonaProbeVerdict.PASS
        assert server.last_body is not None
        assert server.last_body["model"] == "gemma-4-31b-it"

    @pytest.mark.parametrize("tier,var", [
        ("opus", "ANTHROPIC_DEFAULT_OPUS_MODEL"),
        ("haiku", "ANTHROPIC_DEFAULT_HAIKU_MODEL"),
        ("fable", "ANTHROPIC_DEFAULT_FABLE_MODEL"),
    ])
    def test_the_var_name_is_DERIVED_from_the_alias_not_a_tier_list(
        self, token_file, tier, var,
    ):
        """⚑ P13: the rule is the naming convention, never an inventory of tiers.

        A tier this code has never heard of resolves the moment the user names its
        var, so a new one cannot silently outdate a hardcoded list.
        """
        server = _ProbeServer(status=200)
        try:
            ClaudeTarget().verify_persona(
                server.endpoint, token_file, tier, env={var: f"{tier}-real"},
                timeout=5.0,
            )
        finally:
            server.close()
        assert server.last_body is not None
        assert server.last_body["model"] == f"{tier}-real"

    def test_a_model_with_NO_mapping_is_sent_exactly_AS_GIVEN(self, token_file):
        """🛑 Never guess.  No var, no rewrite — the configured id goes out intact."""
        server = _ProbeServer(status=200)
        try:
            ClaudeTarget().verify_persona(
                server.endpoint, token_file, "sonnet",
                env={"ANTHROPIC_DEFAULT_OPUS_MODEL": "some-other-model"},
                timeout=5.0,
            )
        finally:
            server.close()
        assert server.last_body is not None
        assert server.last_body["model"] == "sonnet"

    def test_a_real_provider_id_is_never_treated_as_an_alias(self, token_file):
        """A hyphenated id cannot name an env var, so it is never looked up."""
        server = _ProbeServer(status=200)
        try:
            ClaudeTarget().verify_persona(
                server.endpoint, token_file, "gemma-4-31b-it",
                env={"ANTHROPIC_DEFAULT_SONNET_MODEL": "wrong"}, timeout=5.0,
            )
        finally:
            server.close()
        assert server.last_body is not None
        assert server.last_body["model"] == "gemma-4-31b-it"

    def test_no_model_stays_OMITTED_even_with_tier_vars_present(self, token_file):
        """⚑ RULE 1 IS UNTOUCHED.  A persona naming no model is still probed with
        the key ABSENT — an env block full of tier vars is not a model.
        """
        server = _ProbeServer(status=200)
        try:
            outcome = ClaudeTarget().verify_persona(
                server.endpoint, token_file, None,
                env={"ANTHROPIC_DEFAULT_SONNET_MODEL": "gemma-4-31b-it"},
                timeout=5.0,
            )
        finally:
            server.close()
        assert outcome.verdict is PersonaProbeVerdict.PASS
        assert server.last_body is not None
        assert "model" not in server.last_body

    def test_codex_sends_its_configured_id_verbatim(self, token_file):
        """codex names its model in ``config.toml`` and sends that id; there is no
        alias layer to resolve, so *env* changes nothing on this wire.
        """
        server = _ProbeServer(status=200)
        try:
            CodexTarget().verify_persona(
                server.endpoint, token_file, "sonnet",
                env={"ANTHROPIC_DEFAULT_SONNET_MODEL": "gemma-4-31b-it"},
                timeout=5.0,
            )
        finally:
            server.close()
        assert server.last_body is not None
        assert server.last_body["model"] == "sonnet"


class TestProbeEvidence:
    """A non-PASS verdict carries WHAT WAS SENT and WHAT CAME BACK.

    🛑 The refusal message it feeds names the REFUSAL, never a culprit: a 401/403
    does not say which input was at fault, and the message that asserted "the
    endpoint rejected the token" sent a user to replace a valid credential.
    """

    _PROVIDER_BODY = (
        b'{"error":{"message":"team not allowed to access model. This team can '
        b"only access models=['flux.1-dev', 'gemma-4-31b-it', 'gpt-oss-120b']\"}}"
    )

    def test_a_403_carries_status_model_token_and_provider_text(self, token_file):
        server = _ProbeServer(status=403, body=self._PROVIDER_BODY)
        try:
            outcome = ClaudeTarget().verify_persona(
                server.endpoint, token_file, "sonnet", timeout=5.0,
            )
        finally:
            server.close()
        assert outcome.verdict is PersonaProbeVerdict.REJECTED
        ev = outcome.evidence
        assert ev is not None
        assert ev.status == 403
        assert ev.model == "sonnet"
        assert ev.token_path == token_file
        assert ev.endpoint == server.endpoint
        assert "team not allowed to access model" in ev.provider_text
        block = outcome.evidence_block()
        assert "provider: " in block
        assert "does not say which input was at fault" in block

    def test_the_evidence_says_where_a_resolved_model_came_from(self, token_file):
        """The user configured ``sonnet`` and the wire saw something else — say so,
        or the message reads as if kanibako invented an id.
        """
        server = _ProbeServer(status=403, body=self._PROVIDER_BODY)
        try:
            outcome = ClaudeTarget().verify_persona(
                server.endpoint, token_file, "sonnet",
                env={"ANTHROPIC_DEFAULT_SONNET_MODEL": "gemma-4-31b-it"},
                timeout=5.0,
            )
        finally:
            server.close()
        block = outcome.evidence_block()
        assert "gemma-4-31b-it" in block
        assert "sonnet" in block
        assert "ANTHROPIC_DEFAULT_SONNET_MODEL" in block

    def test_an_omitted_model_and_a_keyless_token_render_as_such(self):
        server = _ProbeServer(status=401, body=b"nope")
        try:
            outcome = ClaudeTarget().verify_persona(
                server.endpoint, None, None, timeout=5.0,
            )
        finally:
            server.close()
        block = outcome.evidence_block()
        assert "(omitted)" in block
        assert "keyless" in block

    def test_an_ambiguous_status_carries_evidence_too(self, token_file):
        server = _ProbeServer(status=500, body=b"upstream on fire")
        try:
            outcome = ClaudeTarget().verify_persona(
                server.endpoint, token_file, "gemma4", timeout=5.0,
            )
        finally:
            server.close()
        assert outcome.verdict is PersonaProbeVerdict.INCONCLUSIVE
        assert "upstream on fire" in outcome.evidence_block()
        # ⚑ The refusal reading is for 401/403 ONLY; anything else would be a guess.
        assert "which input was at fault" not in outcome.evidence_block()

    def test_an_unreachable_endpoint_renders_NO_evidence_block(self, token_file):
        """Nothing answered, so there is nothing to lay out — four lines restating
        the caller's own inputs is noise, not evidence.
        """
        server = _ProbeServer()
        server.close()
        outcome = ClaudeTarget().verify_persona(
            server.endpoint, token_file, "gemma4", timeout=2.0,
        )
        assert outcome.verdict is PersonaProbeVerdict.INCONCLUSIVE
        assert outcome.evidence_block() == ""

    def test_a_long_provider_body_is_TRUNCATED(self, token_file):
        server = _ProbeServer(status=403, body=b"x" * 5000)
        try:
            outcome = ClaudeTarget().verify_persona(
                server.endpoint, token_file, "gemma4", timeout=5.0,
            )
        finally:
            server.close()
        assert outcome.evidence is not None
        assert len(outcome.evidence.provider_text) <= 301   # cap + the ellipsis
        assert outcome.evidence.provider_text.endswith("…")

    @pytest.mark.parametrize("target_cls", [ClaudeTarget, CodexTarget])
    def test_a_provider_that_ECHOES_THE_TOKEN_never_leaks_it(
        self, token_file, target_cls,
    ):
        """🛑 MANDATORY.  The token is in hand at the probe and nowhere downstream,
        so the scrub belongs beside the request or it belongs nowhere.

        (Mutation: drop the scrub in ``targets.base._provider_text`` → the secret
        appears in the printed message → RED.)
        """
        secret = token_file.read_text().strip()
        server = _ProbeServer(
            status=403,
            body=f'{{"error":"bad key Bearer {secret} rejected"}}'.encode(),
        )
        try:
            outcome = target_cls().verify_persona(
                server.endpoint, token_file, "gemma4", timeout=5.0,
            )
        finally:
            server.close()
        assert outcome.verdict is PersonaProbeVerdict.REJECTED
        assert outcome.evidence is not None
        assert secret not in outcome.evidence.provider_text
        assert secret not in outcome.evidence_block()
        assert "<redacted>" in outcome.evidence.provider_text
