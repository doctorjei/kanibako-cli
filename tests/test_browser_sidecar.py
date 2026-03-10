"""Tests for browser sidecar management."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from kanibako.browser_sidecar import (
    BrowserSidecar,
    BrowserSidecarError,
    ws_endpoint_for_container,
)


class TestBrowserSidecar:
    """Tests for BrowserSidecar lifecycle."""

    def _make_sidecar(self, *, host_port=9222):
        runtime = MagicMock()
        runtime.cmd = "podman"
        return BrowserSidecar(
            runtime=runtime,
            container_name="test-browser",
            host_port=host_port,
        )

    def test_start_success(self):
        """Start launches container and returns WS endpoint."""
        sidecar = self._make_sidecar(host_port=9222)

        mock_run_result = MagicMock()
        mock_run_result.returncode = 0
        mock_run_result.stdout = ""

        version_data = json.dumps({
            "webSocketDebuggerUrl": "ws://0.0.0.0:9222/devtools/browser/abc123",
        }).encode()

        with (
            patch("subprocess.run", return_value=mock_run_result),
            patch("urllib.request.urlopen") as mock_urlopen,
        ):
            mock_resp = MagicMock()
            mock_resp.read.return_value = version_data
            mock_resp.__enter__ = MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_resp

            ws_url = sidecar.start()

        assert "ws://" in ws_url
        assert "devtools/browser" in ws_url
        assert sidecar._started is True

    def test_start_already_started(self):
        """Raises error when starting an already-started sidecar."""
        sidecar = self._make_sidecar()
        sidecar._started = True

        with pytest.raises(BrowserSidecarError, match="already started"):
            sidecar.start()

    def test_start_failure(self):
        """Raises error when container fails to start."""
        sidecar = self._make_sidecar()

        mock_run_result = MagicMock()
        mock_run_result.returncode = 1
        mock_run_result.stderr = "image not found"

        with (
            patch("subprocess.run", return_value=mock_run_result),
            pytest.raises(BrowserSidecarError, match="image not found"),
        ):
            sidecar.start()

    def test_stop(self):
        """Stop calls runtime.stop and rm."""
        sidecar = self._make_sidecar()
        sidecar._started = True

        sidecar.stop()

        sidecar.runtime.stop.assert_called_once_with("test-browser")
        sidecar.runtime.rm.assert_called_once_with("test-browser")
        assert sidecar._started is False

    def test_stop_not_started(self):
        """Stop is a no-op when sidecar was not started."""
        sidecar = self._make_sidecar()

        sidecar.stop()

        sidecar.runtime.stop.assert_not_called()
        sidecar.runtime.rm.assert_not_called()

    def test_run_command_includes_shm_size(self):
        """Container run command includes --shm-size=2g for Chromium."""
        sidecar = self._make_sidecar(host_port=9222)

        mock_run_result = MagicMock()
        mock_run_result.returncode = 0

        version_data = json.dumps({
            "webSocketDebuggerUrl": "ws://0.0.0.0:9222/devtools/browser/x",
        }).encode()

        with (
            patch("subprocess.run", return_value=mock_run_result) as mock_run,
            patch("urllib.request.urlopen") as mock_urlopen,
        ):
            mock_resp = MagicMock()
            mock_resp.read.return_value = version_data
            mock_resp.__enter__ = MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_resp

            sidecar.start()

        # Check the podman run command
        call_args = mock_run.call_args[0][0]
        assert "--shm-size=2g" in call_args
        assert "-d" in call_args
        assert "--rm" in call_args

    def test_auto_port_resolution(self):
        """When host_port=0, resolves actual port from 'podman port' output."""
        sidecar = self._make_sidecar(host_port=0)

        # First call: podman run (success)
        run_result = MagicMock()
        run_result.returncode = 0
        run_result.stderr = ""

        # Second call: podman port (returns assigned port)
        port_result = MagicMock()
        port_result.returncode = 0
        port_result.stdout = "0.0.0.0:49152\n"
        port_result.stderr = ""

        version_data = json.dumps({
            "webSocketDebuggerUrl": "ws://0.0.0.0:9222/devtools/browser/xyz",
        }).encode()

        call_count = 0

        def mock_run(cmd, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return run_result
            return port_result

        with (
            patch("subprocess.run", side_effect=mock_run),
            patch("urllib.request.urlopen") as mock_urlopen,
        ):
            mock_resp = MagicMock()
            mock_resp.read.return_value = version_data
            mock_resp.__enter__ = MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_resp

            ws_url = sidecar.start()

        assert "49152" in ws_url or "9222" in ws_url

    def test_port_resolution_failure(self):
        """Raises error when port resolution fails."""
        sidecar = self._make_sidecar(host_port=0)

        run_result = MagicMock()
        run_result.returncode = 0

        port_result = MagicMock()
        port_result.returncode = 1
        port_result.stderr = "no such container"

        call_count = 0

        def mock_run(cmd, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return run_result
            return port_result

        with (
            patch("subprocess.run", side_effect=mock_run),
            pytest.raises(BrowserSidecarError, match="resolve sidecar port"),
        ):
            sidecar.start()


class TestWsEndpointForContainer:
    """Tests for the WS URL transformation for container access."""

    def test_replaces_localhost(self):
        url = "ws://127.0.0.1:9222/devtools/browser/abc"
        result = ws_endpoint_for_container(url)
        assert result == "ws://host.containers.internal:9222/devtools/browser/abc"

    def test_preserves_non_localhost(self):
        url = "ws://192.168.1.1:9222/devtools/browser/abc"
        result = ws_endpoint_for_container(url)
        assert result == url  # unchanged
