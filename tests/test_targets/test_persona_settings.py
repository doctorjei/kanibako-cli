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
import re
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
    _EMBED_DEPTH,
    _NEST_DEPTH,
    _PROVIDER_READ_CAP,
    _PROVIDER_TEXT_CAP,
    _SECRET_MIN_CHARS,
    _UNREADABLE,
    _WITHHELD,
    _provider_text,
    _scrub_decoded,
    http_probe,
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

    🛑 WHAT THESE PIN IS THE REWRITE, NOT THE WHOLE RULE ABOVE.  ``env`` reaches the
    probe as ``PersonaBundle.env`` — the persona STORE entry's own block — and NOT as
    the collapsed ``env`` family the box receives, which only a whole-box resolve
    folds and which the launch does not have at pre-flight time.  A mapping written
    at the agent-file / workset / box rung, or a keyspace-only persona with no store
    block, therefore STILL probes with the raw alias and can still be refused on the
    403.  Do not read a green class here as the heading being satisfied end to end.
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

    @pytest.mark.parametrize("env", [
        None,                                            # the default call shape
        {},                                              # present but empty
        {"ANTHROPIC_DEFAULT_OPUS_MODEL": "unrelated"},   # present, other tiers only
    ])
    def test_the_PROCESS_environment_is_NEVER_read_as_a_fallback(
        self, token_file, monkeypatch, env,
    ):
        """🛑 *env* is the ONLY source; ``os.environ`` is never consulted.

        The process environment belongs to whoever ran ``kanibako`` — an id taken
        from it was written for some other purpose and never for this persona's
        endpoint, so sending it is the substitution rule 2 of
        ``procedures/persona-resolution-model.md`` forbids ("NEVER substitute a
        placeholder model id" — a hardwired-model server can reject an id it does
        not serve, and a false REJECTED refuses a working box).

        ⚑ THE HOLE THIS CLOSES: every other test in this class hands the var in
        *env*, so widening the read to ``(env or os.environ)`` leaves all of them
        GREEN while the probe silently sends what the box never will.

        (Mutation: ``(env or {})`` → ``(env or os.environ)`` → ``host-only`` on the
        wire for the first two cases → RED.)
        """
        monkeypatch.setenv("ANTHROPIC_DEFAULT_SONNET_MODEL", "host-only")
        server = _ProbeServer(status=200)
        try:
            ClaudeTarget().verify_persona(
                server.endpoint, token_file, "sonnet", env=env, timeout=5.0,
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


@pytest.fixture
def slashed_token_file(tmp_path):
    """A key from the STANDARD base64 alphabet — the one an encoder escapes.

    Nothing constrains a third-party provider's key to base64url: the persona's
    ``secret_path`` file holds whatever THAT provider issued, and ``/`` is exactly
    the character a JSON encoder is free to write as ``\\/``.
    """
    tok = tmp_path / "slashed-token"
    tok.write_text("sk-abc/def+ghi=\n")
    return tok


class TestProviderTextScrub:
    """The provider's echoed words are scrubbed — for the spellings the scrub reaches,
    and for EVERY header a plugin authenticates with.

    The defects this class pins, all reachable, none theoretical:

    * the scrub was a LITERAL replace over the wire bytes, so any ESCAPED rendering
      of the key walked past it (a ``/`` written ``\\/`` — PHP's ``json_encode``
      does that by default — printed the key in full);
    * only ``Authorization`` was scrubbed, though ``http_probe`` is a PUBLISHED
      plugin helper a third-party plugin may authenticate through any way it likes.
      The first-party plugins both send ``Authorization``; that made them safe by luck;
    * a body EMBEDDING another JSON document as a string value kept its own escape
      layer inside that string, hiding the key one level below the outer parse;
    * and one the CURE introduced — decoding the body hands back real lone surrogates
      that ``ensure_ascii=False`` writes straight out, so the returned line could
      raise ``UnicodeEncodeError`` in a caller's own ``print``.  Undoing an encoding
      layer is not free, and this is what it cost.

    🛑 AND IT PINS WHAT IS STILL OPEN.  Two tests here assert a KNOWN LEAK on
    purpose — a re-encoding that is not backslash escaping (``%2F``), and a key
    straddling ``_PROVIDER_READ_CAP``.  Neither is caught, neither is withheld, and
    both are documented on ``ProbeResponse.body``.  A reader who finds only the
    green cases would conclude the coverage is total; it is not.
    """

    @staticmethod
    def _unescape_once(text: str) -> str:
        """*text* with ONE backslash-escape layer undone — what a reader recovers by eye.

        ``sk-…A\\u0026B`` is not the key's raw spelling, so ``secret in text`` calls it
        absent; it is nonetheless the key, legible to anyone reading the line.  The leak
        tests below ask THIS question instead — and it is strictly stronger than
        ``_survives``, which only DELETES backslashes and so reads that spelling as
        ``u0026``.  Not a JSON parse: the printed line need not be a JSON document.
        """
        return re.sub(
            r"\\u([0-9a-fA-F]{4})|\\(.)",
            lambda m: chr(int(m[1], 16)) if m[1] else m[2],
            text,
        )

    @staticmethod
    def _echo(headers: dict[str, str], body: bytes, status: int = 403):
        """Probe a server that answers *status* with *body*; return the scrubbed text."""
        server = _ProbeServer(status=status, body=body)
        try:
            return http_probe(
                server.endpoint, headers=headers, body={"ping": 1}, timeout=5.0,
            ).body
        finally:
            server.close()

    # -- Defect A: an escaped rendering of the key ----------------------------

    @pytest.mark.parametrize(
        ("label", "echoed"),
        [
            ("solidus", rb"sk-abc\/def+ghi="),        # PHP json_encode's default
            # The same '/' as a JSON \\uXXXX escape, spelled without a literal
            # backslash-u in this source so no tool in the chain can normalize it away.
            ("unicode", b"sk-abc" + b"\\" + b"u002fdef+ghi="),
        ],
    )
    @pytest.mark.parametrize("target_cls", [ClaudeTarget, CodexTarget])
    def test_an_ESCAPED_echo_of_the_key_never_leaks_it(
        self, slashed_token_file, target_cls, label, echoed,
    ):
        """🛑 MANDATORY.  A literal replace only ever sees a secret's RAW spelling.

        (Mutation: scrub the wire bytes instead of the decoded body — as
        ``_provider_text`` did before — and both the ``<redacted>`` marker and the
        surviving ``def+ghi=`` fragment go RED.)
        """
        secret = slashed_token_file.read_text().strip()
        tail = secret.split("/")[-1]   # a distinctive run of the key, unescaped in both forms
        server_body = b'{"error":{"message":"invalid key: Bearer ' + echoed + b' rejected"}}'
        server = _ProbeServer(status=403, body=server_body)
        try:
            outcome = target_cls().verify_persona(
                server.endpoint, slashed_token_file, "gemma4", timeout=5.0,
            )
        finally:
            server.close()
        assert outcome.verdict is PersonaProbeVerdict.REJECTED
        assert outcome.evidence is not None
        text = outcome.evidence.provider_text
        assert secret not in text
        assert tail not in text, f"the {label} spelling left a fragment of the key standing"
        assert secret not in text.replace("\\", "")
        assert "<redacted>" in text
        assert tail not in outcome.evidence_block()

    def test_a_DOUBLY_escaped_echo_is_WITHHELD_rather_than_printed(self):
        """Decoding undoes ONE layer; the guard catches the residue and drops the body.

        A message the user does not get is a cost; a key the user's terminal prints
        is a breach.  The guard picks the cost.
        """
        secret = "sk-abc/def+ghi="
        body = rb'{"error":"invalid key sk-abc\\/def+ghi= rejected"}'
        assert self._echo({"Authorization": f"Bearer {secret}"}, body) == _WITHHELD

    def test_a_key_straddling_the_TEXT_cap_is_scrubbed_before_truncation(self):
        """``_PROVIDER_TEXT_CAP`` — the ONE of the two caps this module gets to order.

        Applied first it would leave the head of a key standing in the output, so
        the scrub runs ahead of it.  The other cap is not ours to order; see
        ``test_a_key_straddling_the_READ_cap_leaks_its_head_KNOWN_RESIDUE``.
        """
        secret = "sk-" + "z" * 40
        pad = "y" * (_PROVIDER_TEXT_CAP - 30)   # the key opens inside the cap, ends past it
        text = self._echo(
            {"Authorization": f"Bearer {secret}"},
            f"not json: {pad}{secret} trailing".encode(),
        )
        assert secret[:20] not in text
        assert "<redacted>" in text

    def test_a_key_straddling_the_READ_cap_leaks_its_head_KNOWN_RESIDUE(self):
        """🛑 THIS TEST ASSERTS A KNOWN LEAK, DELIBERATELY, SO IT CANNOT BE BELIEVED AWAY.

        ``http_probe`` cuts the body at ``_PROVIDER_READ_CAP`` BYTES before the scrub
        ever runs, and nothing downstream can reorder that.  A key straddling the cut
        arrives bisected, matches no secret, and the whitespace collapse pulls its
        surviving head inside the character cap — printed in cleartext.

        Closing it means reading more of a hostile body, which is the trade the read
        cap exists to refuse.  If this ever goes RED because the class was closed,
        delete it and say so in the commit.
        """
        secret = "sk-" + "Q" * 40
        pad = b" " * (_PROVIDER_READ_CAP - len("error: ") - 20)
        text = self._echo(
            {"Authorization": f"Bearer {secret}"},
            b"error: " + pad + secret.encode() + b" tail",
        )
        assert secret not in text                      # the whole key does not survive
        assert text == f"error: {secret[:20]}"         # ...but twenty characters of it do

    # -- Defect B: every caller-supplied header, not just Authorization -------

    @pytest.mark.parametrize("header", ["x-api-key", "X-Goog-Api-Key", "Proxy-Authorization"])
    def test_a_NON_authorization_credential_is_scrubbed_TOO(self, header):
        """``http_probe`` is a published helper; a plugin may authenticate any way.

        (Mutation: restrict the secret set to ``Authorization`` — as
        ``_bearer_secrets`` did — and every parametrization goes RED.)
        """
        secret = "sk-third-party-plugin-key-0001"
        text = self._echo({header: secret}, b'{"error":"rejected key ' + secret.encode() + b'"}')
        assert secret not in text
        assert "<redacted>" in text

    def test_a_value_TOO_SHORT_to_be_a_credential_stays_legible(self):
        """The one exclusion is LENGTH, never a header name — and the rule is pinned
        from ``_SECRET_MIN_CHARS`` itself, so moving the constant moves the test.
        """
        short = "v" * (_SECRET_MIN_CHARS - 1)
        long_ = "v" * _SECRET_MIN_CHARS
        kept = self._echo({"x-thing": short}, f'{{"e":"unsupported {short}"}}'.encode())
        assert short in kept
        hidden = self._echo({"x-thing": long_}, f'{{"e":"unsupported {long_}"}}'.encode())
        assert long_ not in hidden
        assert "<redacted>" in hidden

    def test_an_EMPTY_header_value_does_not_splice_the_marker_into_the_body(self):
        """``"abc".replace("", m)`` puts *m* between every character — the reason the
        length floor is not merely a noise filter.
        """
        assert self._echo({"x-blank": ""}, b'{"e":"plain words"}') == '{"e":"plain words"}'

    def test_a_value_that_PREFIXES_another_cannot_eat_the_longer_one(self):
        """Replacement order is a security property, so it is not left to the caller.

        Where one header value is a strict prefix of another, replacing the short
        one first consumes the long one's anchor and leaves its tail in cleartext.
        ``_request_secrets`` returns longest-first so the shape is unavailable.

        (Mutation: return the secrets in ``dict`` order and this goes RED with
        ``<redacted>SECRETTAIL`` standing — and ``_survives`` does NOT fire on it.)
        """
        short, long_ = "sk-abcdefgh", "sk-abcdefghSECRETTAIL"
        text = self._echo(
            {"x-api-key": short, "x-api-key-full": long_},
            b'{"e":"rejected ' + long_.encode() + b'"}',
        )
        assert "SECRETTAIL" not in text
        assert text == '{"e":"rejected <redacted>"}'

    # -- Defect C: a document embedded in the body as a STRING ----------------

    def test_an_EMBEDDED_json_document_is_FOLLOWED_and_scrubbed(self):
        """A gateway echoing an upstream error body as a string value hides the key.

        The inner document keeps its own escape layer, so scrubbing the outer
        string as text sees only raw spellings — the top-level defect, one level in.
        Fronting one provider with another is ordinary deployment, and a persona's
        ``ANTHROPIC_BASE_URL`` may name any third party at all.

        (Mutation: drop the ``_scrub_embedded`` descent and this goes RED with the
        credential recoverable by a second ``json.loads``.)
        """
        secret = "sk-abc/def+ghi="
        # The inner '/' as a JSON \\uXXXX escape, spelled without a literal
        # backslash-u here so no tool in the chain can normalize it away.
        inner = '{"tok":"sk-abc' + "\\" + 'u002fdef+ghi="}'
        text = self._echo(
            {"Authorization": f"Bearer {secret}"},
            json.dumps({"e": inner}).encode(),
        )
        assert secret not in text
        assert "def+ghi=" not in text
        assert "<redacted>" in text
        assert json.loads(json.loads(text)["e"])["tok"] == "<redacted>"

    def test_embedding_PAST_the_depth_bound_is_REDACTED_IN_PLACE_not_skipped(self):
        """The bound is a bound, never a silent cliff — and it costs only what it must.

        A document we declined to read is a place a secret could be, so the whole
        over-budget document is replaced.  IN PLACE: only IT disappears — the documents
        within budget are still scrubbed and re-serialized around it, and the user keeps
        a message instead of losing the body.  The depth is derived from the constant,
        so moving it moves the test.

        (Mutation: raise past the budget and withhold the whole body — as ``_TooDeep``
        did — and the surroundings go RED.)
        """
        secret = "sk-abc/def+ghi="
        headers = {"Authorization": f"Bearer {secret}"}
        node = {"tok": secret}
        for _ in range(_EMBED_DEPTH):
            node = {"e": json.dumps(node)}
        assert secret not in self._echo(headers, json.dumps(node).encode())
        node = {"e": json.dumps(node)}   # one embedded document past the budget
        text = self._echo(headers, json.dumps({"note": "read the docs", **node}).encode())
        assert secret not in self._unescape_once(text)
        assert text != _WITHHELD
        assert '"note":"read the docs"' in text   # its surroundings survived the refusal
        assert "<redacted>" in text

    def test_a_body_TOO_DEEP_FOR_THE_WALK_is_not_printed_undecoded(self):
        """🛑 The ``_EMBED_DEPTH`` rule, applied to the OTHER way the decode can stop.

        A parse returns trees the walk over its result cannot follow — that walk is
        Python recursion and dies near CPython's limit.  Swallowing that
        ``RecursionError`` fell through to the plain-text path, where the scrub sees only
        the credential's RAW spelling — so an escaped echo printed with the key
        recoverable by one unescape, and ``_survives`` could not see it (deleting
        backslashes leaves ``u0026``, not ``&``).  ``&`` is not exotic: Go's
        ``encoding/json`` HTML-escapes ``&``, ``<`` and ``>`` by DEFAULT, so any Go
        gateway echoing a key containing one writes exactly this.  ``_NEST_DEPTH`` answers
        it by REDACTING IN PLACE, so the user still gets the surrounding message.

        ⚑ THE DEPTH IS DERIVED FROM ``_NEST_DEPTH``, NOT PICKED.  A literal deep enough to
        look convincing (1100) is past ``json.loads``' OWN floor on the oldest interpreter
        kanibako supports — the body would die in the parse, take the catch-all arm and
        never reach the walk at all, so the test would pass or fail for a reason it does
        not name.  ``_provider_text``'s docstring carries that floor.

        (Mutation: drop ``_NEST_DEPTH`` from ``_scrub_decoded`` and restore the bare
        ``except Exception: pass`` — as ``_provider_text`` had — and both asserts go RED.)
        """
        secret = "sk-ant-api03-A&BcDeFgHiJkLmN"
        # The '&' as a JSON \\uXXXX escape, spelled without a literal backslash-u
        # here so no tool in the chain can normalize it away.
        escaped = "sk-ant-api03-A" + "\\" + "u0026BcDeFgHiJkLmN"
        depth = _NEST_DEPTH + 1   # one container past the walk's budget
        raw = (
            '{"error":"invalid key ' + escaped + '","d":'
            + "[" * depth + "1" + "]" * depth + "}"
        )
        body = raw.encode()
        assert len(body) < _PROVIDER_READ_CAP
        # 🛑 RED ON ITS OWN EMPTINESS.  If an interpreter ever parses shallower than this,
        # the assertions below would be measuring the catch-all arm instead of the walk.
        json.loads(raw)
        text = self._echo({"Authorization": f"Bearer {secret}"}, body)
        assert text not in (_UNREADABLE, _WITHHELD)   # the walk finished; nothing was withheld
        assert secret not in self._unescape_once(text)
        assert "<redacted>" in text

    def test_ORDINARY_nesting_past_ITS_bound_is_redacted_IN_PLACE_too(self):
        """The walk carries its own depth budget rather than borrowing the interpreter's.

        Leaving the limit to CPython rests a security property on
        ``sys.setrecursionlimit`` — a tunable global that differs by build.  ``_NEST_DEPTH``
        is spent on ordinary containers, ``_EMBED_DEPTH`` on embedded documents, and
        both replace what they decline to read with the same marker, in place.  Depths
        are derived from the constant, so moving it moves the test.
        """
        secrets = ("sk-abcdefghijkl",)
        under = json.loads("[" * (_NEST_DEPTH - 1) + '"deep"' + "]" * (_NEST_DEPTH - 1))
        assert "deep" in json.dumps(_scrub_decoded(under, secrets))
        over = json.loads("[" * (_NEST_DEPTH + 1) + '"deep"' + "]" * (_NEST_DEPTH + 1))
        walked = json.dumps(_scrub_decoded(over, secrets))
        assert "deep" not in walked
        assert "<redacted>" in walked
        # IN PLACE: a sibling of the over-deep branch is untouched by its refusal.
        mixed = {"keep": "visible", "d": over}
        assert "visible" in json.dumps(_scrub_decoded(mixed, secrets))

    def test_a_body_the_decode_CANNOT_FINISH_is_withheld_and_SAYS_SO_ACCURATELY(self):
        """The residual arm: what neither bound owns still fails LOUD, never quietly.

        The bounds keep the WALK inside the interpreter's limit; they cannot answer
        for the stack already under this call, for ``_compact``, or for a decode shape
        nobody foresaw.  A body ``_provider_text`` set out to read and could not is a
        document we declined to read, so it withholds rather than falling through to
        the raw-spelling scrub.

        🛑 AND IT GETS ITS OWN SENTENCE.  ``_WITHHELD`` reports a MATCH against a
        request value; nothing matched here, so reusing it would tell the user about
        a match that never happened.  Two causes, two messages.

        ⚑ Reached through ``_provider_text`` directly and DELIBERATELY: the depth here is
        past EVERY supported interpreter's parse floor, and nesting THAT deep no longer
        fits under ``_PROVIDER_READ_CAP``, so ``http_probe`` could not have handed it over.

        🛑 THAT IS THIS TEST'S SHAPE, NOT A CLAIM ABOUT THE ARM.  On the oldest interpreter
        kanibako supports the parse floor sits under 2 KB of nesting — a body a server can
        serve perfectly well — so this arm is ORDINARY there, not residual.
        ``_provider_text`` carries the floor and the command that re-derives it.

        (Mutation: make the catch-all ``pass`` instead of withholding and this goes RED.)
        """
        secret = "sk-ant-api03-A&BcDeFgHiJkLmN"
        escaped = "sk-ant-api03-A" + "\\" + "u0026BcDeFgHiJkLmN"
        depth = 20_000   # past `json.loads`' OWN recursion, so the parse never returns
        body = (
            '{"error":"invalid key ' + escaped + '","d":'
            + "[" * depth + "1" + "]" * depth + "}"
        ).encode()
        assert len(body) > _PROVIDER_READ_CAP
        assert _provider_text(body, {"Authorization": f"Bearer {secret}"}) == _UNREADABLE
        assert _UNREADABLE != _WITHHELD
        assert "matched" not in _UNREADABLE

    def test_a_LONE_SURROGATE_cannot_make_the_returned_line_UNPRINTABLE(self):
        r"""The decode hands back a REAL surrogate, and ``ensure_ascii=False`` writes it out.

        ``json.loads`` turns ``\ud800`` into an unpaired surrogate, and ``_compact`` keeps
        it raw ON PURPOSE — re-escaping is exactly what would hide a non-ASCII credential
        from the scrub that follows.  So the sanitize belongs at ``_provider_text``'s
        return, after the scrub, and not on ``_compact``.

        Without it the returned string raises ``UnicodeEncodeError`` in the CALLER's own
        ``print``.  Kanibako's own sinks are all ``sys.stderr``, whose ``backslashreplace``
        handler swallows it — but ``http_probe`` is a PUBLISHED plugin helper and
        ``sys.stdout`` is strict, so the plugin author gets a traceback instead of the
        error they asked for.  The pre-image could not reach this at all: it printed
        ``raw.decode("utf-8", "replace")``, which never yields a surrogate.

        (Mutation: drop the ``encode``/``decode`` at ``_provider_text``'s return and the
        ``text.encode("utf-8")`` below raises.)
        """
        secret = "sk-abcdefghijkl"
        text = self._echo(
            {"Authorization": f"Bearer {secret}"},
            rb'{"e":"bad key \ud800 here"}',
        )
        text.encode("utf-8")                # 🛑 THE POINT: this must not raise
        assert "\ud800" not in text
        assert "bad key" in text            # ...and the user still gets the message

    def test_the_ENCODABLE_sanitize_does_not_defeat_the_scrub(self):
        """A body carrying BOTH a surrogate and the key comes back printable AND scrubbed.

        The two treatments co-exist: the sanitize does not eat the ``<redacted>`` the scrub
        wrote, and the scrub is not thrown by the surrogate sitting beside its match.

        🛑 IT DOES NOT PIN THE ORDER, AND NO INPUT HERE COULD.  ``json.loads`` undoes the
        ``\\/`` before either step runs, so the key is present in its raw spelling and the
        scrub catches it whichever way round the two go — a distinguishing body would need
        a secret the sanitize could damage, and a replacement only ever destroys.  That the
        sanitize must nonetheless FOLLOW the scrub — it can never rebuild a secret, and
        running it first would hand ``_survives`` a string the scrub never saw — is an
        invariant argued at ``_provider_text``'s call site and UNPINNED here.
        """
        secret = "sk-abc/def+ghi="
        text = self._echo(
            {"Authorization": f"Bearer {secret}"},
            rb'{"e":"bad key \ud800 sk-abc\/def+ghi= here"}',
        )
        text.encode("utf-8")
        assert secret not in text
        assert "def+ghi=" not in text
        assert "<redacted>" in text

    def test_a_secret_the_WHITESPACE_COLLAPSE_would_break_is_scrubbed_first(self):
        """The collapse can DESTROY a match as easily as create one, so both sides scrub.

        A header value carrying a double space matches the wire body exactly; collapse
        it first and the body no longer contains the value, ``_survives`` agrees it is
        gone, and the full credential prints.

        (Mutation: drop the scrub ahead of the join and this goes RED with the whole
        value standing.)
        """
        secret = "sk-abcdefghijkl  m"
        text = self._echo({"x-api-key": secret}, b"not json: rejected sk-abcdefghijkl  m here")
        assert "sk-abcdefghijkl" not in text
        assert text == "not json: rejected <redacted> here"

    def test_a_RE_ENCODED_key_is_PRINTED_and_that_residue_is_DOCUMENTED(self):
        """🛑 THIS TEST ASSERTS A KNOWN LEAK, DELIBERATELY, SO IT CANNOT BE BELIEVED AWAY.

        ``%2F`` is not a JSON escape: no decode layer here undoes it, the scrub
        never sees the key's own characters, and ``_survives`` cannot tell that
        anything happened — so the body prints with the credential in it.  Nothing
        is withheld.  ``_survives`` is a guard, not coverage.

        Closing this class takes another DECODE layer ahead of the scrub, the way
        ``_scrub_embedded`` closed embedded JSON — never a wider ``_survives``,
        which would become an enumeration of spellings, wrong the moment a provider
        picks a new one.  If this ever goes RED because the class was closed, delete
        it and say so in the commit.
        """
        secret = "sk-abc/def+ghi="
        body = b'{"e":"bad key sk-abc%2Fdef+ghi="}'
        assert self._echo({"Authorization": f"Bearer {secret}"}, body) == body.decode()

    def test_WITHHELD_states_the_outcome_and_accuses_the_provider_of_nothing(self):
        """The secret set is length-keyed, so a match is not proof of a credential.

        The claude plugin ships ``anthropic-version: 2023-06-01``; an escaped
        mention of that date trips ``_survives`` and drops the whole message.
        Telling the user their provider echoed a key back would be an accusation
        this code cannot support.
        """
        headers = {"anthropic-version": "2023-06-01", "Authorization": "Bearer sk-zzzzzzzzzzzz"}
        assert self._echo(headers, b"unsupported anthropic-version 2023-\\06-01 here") == _WITHHELD
        assert "credential" not in _WITHHELD
        assert "echoed" not in _WITHHELD
