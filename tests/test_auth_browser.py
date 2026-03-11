"""Tests for automated OAuth browser refresh."""

from __future__ import annotations

from unittest.mock import MagicMock, patch


from kanibako.auth_browser import (
    AuthResult,
    _handle_auth_page,
    auto_refresh_auth,
    refresh_auth,
)


class TestAuthResult:
    def test_success(self):
        r = AuthResult(success=True, key="abc123")
        assert r.success is True
        assert r.key == "abc123"
        assert r.error is None

    def test_failure(self):
        r = AuthResult(success=False, error="manual login required")
        assert r.success is False
        assert r.key is None
        assert r.error == "manual login required"

    def test_defaults(self):
        r = AuthResult(success=True)
        assert r.key is None
        assert r.error is None


class TestRefreshAuth:
    def test_no_playwright(self, tmp_path):
        """Returns error when playwright is not installed."""
        with patch("kanibako.auth_browser._check_playwright", return_value=False):
            result = refresh_auth("https://example.com/auth", tmp_path)
        assert result.success is False
        assert "Playwright not installed" in result.error

    def test_with_playwright_success(self, tmp_path):
        """Successful flow with mocked playwright."""
        mock_page = MagicMock()
        mock_context = MagicMock()
        mock_context.new_page.return_value = mock_page
        mock_context.storage_state.return_value = {"cookies": [], "origins": []}
        mock_browser = MagicMock()
        mock_browser.new_context.return_value = mock_context

        mock_pw = MagicMock()
        mock_pw.chromium.launch.return_value = mock_browser

        with (
            patch("kanibako.auth_browser._check_playwright", return_value=True),
            patch("kanibako.auth_browser.sync_playwright") as mock_sp,
            patch("kanibako.auth_browser._handle_auth_page") as mock_handle,
        ):
            mock_sp.return_value.__enter__ = MagicMock(return_value=mock_pw)
            mock_sp.return_value.__exit__ = MagicMock(return_value=False)
            mock_handle.return_value = AuthResult(success=True, key="KEY123")

            result = refresh_auth("https://console.anthropic.com/auth", tmp_path)

        assert result.success is True
        assert result.key == "KEY123"

    def test_with_playwright_failure(self, tmp_path):
        """Failed flow returns error."""
        mock_page = MagicMock()
        mock_context = MagicMock()
        mock_context.new_page.return_value = mock_page
        mock_browser = MagicMock()
        mock_browser.new_context.return_value = mock_context

        mock_pw = MagicMock()
        mock_pw.chromium.launch.return_value = mock_browser

        with (
            patch("kanibako.auth_browser._check_playwright", return_value=True),
            patch("kanibako.auth_browser.sync_playwright") as mock_sp,
            patch("kanibako.auth_browser._handle_auth_page") as mock_handle,
        ):
            mock_sp.return_value.__enter__ = MagicMock(return_value=mock_pw)
            mock_sp.return_value.__exit__ = MagicMock(return_value=False)
            mock_handle.return_value = AuthResult(
                success=False, error="IdP session expired"
            )

            result = refresh_auth("https://console.anthropic.com/auth", tmp_path)

        assert result.success is False
        assert "IdP session expired" in result.error

    def test_browser_exception(self, tmp_path):
        """Exception during browser automation returns error."""
        # Use a distinct timeout class so PWTimeout doesn't catch RuntimeError
        fake_timeout = type("PlaywrightTimeout", (Exception,), {})

        with (
            patch("kanibako.auth_browser._check_playwright", return_value=True),
            patch("kanibako.auth_browser.sync_playwright") as mock_sp,
            patch("kanibako.auth_browser.PWTimeout", fake_timeout),
        ):
            mock_sp.return_value.__enter__ = MagicMock(
                side_effect=RuntimeError("browser crashed")
            )
            mock_sp.return_value.__exit__ = MagicMock(return_value=False)

            result = refresh_auth("https://console.anthropic.com/auth", tmp_path)

        assert result.success is False
        assert "browser crashed" in result.error

    def test_loads_stored_state(self, tmp_path):
        """Uses stored browser state when available."""
        from kanibako.browser_state import BrowserState, save_state

        state = BrowserState(
            cookies=[{"name": "session", "value": "abc"}],
            origins=[],
        )
        save_state(tmp_path, state)

        mock_page = MagicMock()
        mock_context = MagicMock()
        mock_context.new_page.return_value = mock_page
        mock_context.storage_state.return_value = {"cookies": [], "origins": []}
        mock_browser = MagicMock()
        mock_browser.new_context.return_value = mock_context

        mock_pw = MagicMock()
        mock_pw.chromium.launch.return_value = mock_browser

        with (
            patch("kanibako.auth_browser._check_playwright", return_value=True),
            patch("kanibako.auth_browser.sync_playwright") as mock_sp,
            patch("kanibako.auth_browser._handle_auth_page") as mock_handle,
        ):
            mock_sp.return_value.__enter__ = MagicMock(return_value=mock_pw)
            mock_sp.return_value.__exit__ = MagicMock(return_value=False)
            mock_handle.return_value = AuthResult(success=True)

            refresh_auth("https://console.anthropic.com/auth", tmp_path)

        # Should have passed storage_state to new_context
        call_kwargs = mock_browser.new_context.call_args
        assert call_kwargs is not None
        assert "storage_state" in (call_kwargs.kwargs or {})


class TestHandleAuthPage:
    """Tests for _handle_auth_page with mocked Playwright page."""

    def _make_page(self, *, authorize_visible=False, login_visible=False):
        """Create a mock Playwright page."""
        page = MagicMock()

        # Import the timeout error for mocking
        timeout_error = type("TimeoutError", (Exception,), {})

        def wait_for_selector(selector, timeout=None):
            if authorize_visible and "Authorize" in selector:
                btn = MagicMock()
                btn.is_visible.return_value = True
                btn.text_content.return_value = ""
                return btn
            if login_visible and any(
                x in selector for x in ["email", "password", "identifierId", "login_field"]
            ):
                el = MagicMock()
                el.is_visible.return_value = True
                return el
            raise timeout_error("timeout")

        page.wait_for_selector = MagicMock(side_effect=wait_for_selector)
        page.text_content = MagicMock(return_value="some page content")
        return page, timeout_error

    def test_authorize_button_found(self):
        """Clicks authorize when button is visible."""
        page, timeout_cls = self._make_page(authorize_visible=True)

        with patch("kanibako.auth_browser.PWTimeout", timeout_cls):
            with patch("kanibako.auth_browser._extract_key", return_value="KEY"):
                result = _handle_auth_page(page)

        assert result.success is True
        assert result.key == "KEY"

    def test_login_form_detected(self):
        """Returns failure when login form is shown."""
        page, timeout_cls = self._make_page(login_visible=True)

        with patch("kanibako.auth_browser.PWTimeout", timeout_cls):
            result = _handle_auth_page(page)

        assert result.success is False
        assert "manual login required" in result.error

    def test_unrecognized_page(self):
        """Returns failure when neither authorize nor login found."""
        page, timeout_cls = self._make_page()

        with patch("kanibako.auth_browser.PWTimeout", timeout_cls):
            result = _handle_auth_page(page)

        assert result.success is False
        assert "Unrecognized" in result.error


class TestAutoRefreshAuth:
    """Tests for auto_refresh_auth orchestrator."""

    def test_no_playwright(self, tmp_path):
        """Returns error when playwright is not installed."""
        with patch("kanibako.auth_browser._check_playwright", return_value=False):
            result = auto_refresh_auth("/usr/bin/claude", tmp_path)
        assert result.success is False
        assert "Playwright not installed" in result.error

    def test_binary_not_found(self, tmp_path):
        """Returns error when claude binary doesn't exist."""
        with patch("kanibako.auth_browser._check_playwright", return_value=True):
            result = auto_refresh_auth("/nonexistent/claude", tmp_path)
        assert result.success is False
        assert "Failed to start auth" in result.error

    def test_no_url_in_output(self, tmp_path):
        """Returns error when auth output contains no OAuth URL."""
        mock_proc = MagicMock()
        mock_proc.stdout = iter(["Welcome to Claude\n", "Please log in\n"])
        mock_proc.stdin = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.kill.return_value = None
        mock_proc.wait.return_value = 0

        with (
            patch("kanibako.auth_browser._check_playwright", return_value=True),
            patch("subprocess.Popen", return_value=mock_proc),
        ):
            result = auto_refresh_auth("/usr/bin/claude", tmp_path)

        assert result.success is False
        assert "No OAuth URL" in result.error
        mock_proc.kill.assert_called_once()

    def test_successful_auto_auth(self, tmp_path):
        """Successful flow: URL found → browser clicks authorize → login completes."""
        mock_proc = MagicMock()
        mock_proc.stdout = iter([
            "Open this URL: https://console.anthropic.com/oauth/authorize?foo=bar\n",
        ])
        mock_proc.stdin = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.wait.return_value = 0

        with (
            patch("kanibako.auth_browser._check_playwright", return_value=True),
            patch("subprocess.Popen", return_value=mock_proc),
            patch("kanibako.auth_browser.refresh_auth") as mock_refresh,
        ):
            mock_refresh.return_value = AuthResult(success=True, key="KEY123")
            result = auto_refresh_auth("/usr/bin/claude", tmp_path)

        assert result.success is True
        assert result.key == "KEY123"
        mock_refresh.assert_called_once()
        # URL should have been extracted and passed to refresh_auth
        call_args = mock_refresh.call_args
        assert "console.anthropic.com" in call_args.args[0]

    def test_browser_auth_fails(self, tmp_path):
        """Browser automation fails → process is killed."""
        mock_proc = MagicMock()
        mock_proc.stdout = iter([
            "Open: https://console.anthropic.com/oauth/authorize\n",
        ])
        mock_proc.stdin = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.kill.return_value = None
        mock_proc.wait.return_value = 1

        with (
            patch("kanibako.auth_browser._check_playwright", return_value=True),
            patch("subprocess.Popen", return_value=mock_proc),
            patch("kanibako.auth_browser.refresh_auth") as mock_refresh,
        ):
            mock_refresh.return_value = AuthResult(
                success=False, error="IdP session expired"
            )
            result = auto_refresh_auth("/usr/bin/claude", tmp_path)

        assert result.success is False
        mock_proc.kill.assert_called_once()

    def test_feeds_key_to_stdin(self, tmp_path):
        """When refresh_auth returns a key, it's fed to the login process."""
        mock_proc = MagicMock()
        mock_proc.stdout = iter([
            "URL: https://console.anthropic.com/oauth/authorize\n",
        ])
        mock_stdin = MagicMock()
        mock_proc.stdin = mock_stdin
        mock_proc.poll.return_value = None  # process still running
        mock_proc.wait.return_value = 0

        with (
            patch("kanibako.auth_browser._check_playwright", return_value=True),
            patch("subprocess.Popen", return_value=mock_proc),
            patch("kanibako.auth_browser.refresh_auth") as mock_refresh,
        ):
            mock_refresh.return_value = AuthResult(success=True, key="MYKEY")
            auto_refresh_auth("/usr/bin/claude", tmp_path)

        mock_stdin.write.assert_called_once_with("MYKEY\n")
        mock_stdin.flush.assert_called_once()
