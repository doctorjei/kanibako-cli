"""Tests for auth output parsing."""

from __future__ import annotations

from kanibako.auth_parser import AuthPrompt, parse_auth_output


class TestParseAuthOutput:
    def test_anthropic_url(self):
        output = "Open this URL in your browser:\nhttps://console.anthropic.com/oauth?code=abc123\n"
        result = parse_auth_output(output)
        assert result is not None
        assert result.url == "https://console.anthropic.com/oauth?code=abc123"

    def test_claude_ai_url(self):
        output = "Visit https://claude.ai/auth/login?redirect=foo to authorize\n"
        result = parse_auth_output(output)
        assert result is not None
        assert result.url == "https://claude.ai/auth/login?redirect=foo"

    def test_no_url(self):
        output = "Error: network connection failed\n"
        assert parse_auth_output(output) is None

    def test_url_with_verification_code(self):
        output = (
            "Open: https://console.anthropic.com/oauth/authorize\n"
            "Your verification code is: ABCD1234\n"
        )
        result = parse_auth_output(output)
        assert result is not None
        assert "anthropic.com" in result.url
        assert result.code == "ABCD1234"

    def test_code_with_colon(self):
        output = "URL: https://console.anthropic.com/auth\nCode: XY12AB\n"
        result = parse_auth_output(output)
        assert result is not None
        assert result.code == "XY12AB"

    def test_code_with_equals(self):
        output = "Visit https://console.anthropic.com/auth\nkey = WXYZ99\n"
        result = parse_auth_output(output)
        assert result is not None
        assert result.code == "WXYZ99"

    def test_no_code(self):
        output = "Open: https://console.anthropic.com/oauth\n"
        result = parse_auth_output(output)
        assert result is not None
        assert result.code is None

    def test_empty_output(self):
        assert parse_auth_output("") is None

    def test_url_in_middle_of_text(self):
        output = (
            "Authentication required.\n"
            "Please visit https://console.anthropic.com/oauth/device "
            "and enter the code below.\n"
        )
        result = parse_auth_output(output)
        assert result is not None
        assert result.url == "https://console.anthropic.com/oauth/device"

    def test_multiple_urls_takes_first(self):
        output = (
            "Visit https://console.anthropic.com/first\n"
            "Or try https://console.anthropic.com/second\n"
        )
        result = parse_auth_output(output)
        assert result is not None
        assert result.url == "https://console.anthropic.com/first"

    def test_non_anthropic_url_ignored(self):
        output = "Visit https://example.com/auth for help\n"
        assert parse_auth_output(output) is None


class TestAuthPrompt:
    def test_defaults(self):
        prompt = AuthPrompt(url="https://example.com")
        assert prompt.url == "https://example.com"
        assert prompt.code is None

    def test_with_code(self):
        prompt = AuthPrompt(url="https://example.com", code="ABC123")
        assert prompt.code == "ABC123"
