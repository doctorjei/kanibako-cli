"""On-demand browser sidecar for AI agents.

Launches a headless Chrome container (``chromedp/headless-shell``) that
agents can connect to via the Chrome DevTools Protocol over WebSocket.
The agent receives the ``BROWSER_WS_ENDPOINT`` environment variable
pointing to the sidecar's DevTools port.

The sidecar is started before the agent container and stopped after it
exits.  It is *not* a long-running service — it only lives for the
duration of one agent session.
"""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass

from kanibako.container import ContainerRuntime
from kanibako.log import get_logger

logger = get_logger("browser_sidecar")

_DEFAULT_IMAGE = "chromedp/headless-shell:latest"
_CDP_PORT = 9222
_STARTUP_TIMEOUT = 30
_HEALTH_CHECK_INTERVAL = 0.5


@dataclass
class BrowserSidecar:
    """Manages a headless browser container for agent web access.

    The sidecar publishes Chrome DevTools Protocol on a host port.
    The agent container connects via the host gateway IP.
    """

    runtime: ContainerRuntime
    container_name: str
    image: str = _DEFAULT_IMAGE
    host_port: int = 0  # 0 = auto-assign
    _started: bool = False

    def start(self) -> str:
        """Start the browser sidecar and return the WebSocket endpoint URL.

        Blocks until the container is healthy or *_STARTUP_TIMEOUT* elapses.

        Returns the ``ws://`` URL suitable for ``BROWSER_WS_ENDPOINT``.

        Raises :class:`BrowserSidecarError` on failure.
        """
        if self._started:
            raise BrowserSidecarError("Sidecar already started")

        port_spec = (
            f"{self.host_port}:{_CDP_PORT}"
            if self.host_port
            else str(_CDP_PORT)
        )

        cmd = [
            self.runtime.cmd,
            "run",
            "-d",
            "--rm",
            "--name",
            self.container_name,
            "--shm-size=2g",
            "-p",
            port_spec,
            self.image,
            # headless-shell uses --remote-debugging-address by default;
            # ensure it listens on all interfaces inside the container.
            "--remote-debugging-address=0.0.0.0",
            f"--remote-debugging-port={_CDP_PORT}",
        ]

        logger.debug("Starting browser sidecar: %s", cmd)
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise BrowserSidecarError(
                f"Failed to start sidecar: {result.stderr.strip()}"
            )

        self._started = True

        # Resolve the actual host port (if auto-assigned).
        actual_port = self._resolve_port()

        # Wait for the DevTools endpoint to be ready.
        ws_url = self._wait_for_endpoint(actual_port)
        logger.info("Browser sidecar ready: %s", ws_url)
        return ws_url

    def stop(self) -> None:
        """Stop and remove the browser sidecar."""
        if not self._started:
            return

        logger.debug("Stopping browser sidecar: %s", self.container_name)
        self.runtime.stop(self.container_name)
        # --rm flag means container is auto-removed after stop, but
        # call rm() defensively in case stop fails to clean up.
        self.runtime.rm(self.container_name)
        self._started = False

    def _resolve_port(self) -> int:
        """Discover the host port assigned to the sidecar's CDP port."""
        if self.host_port:
            return self.host_port

        cmd = [
            self.runtime.cmd,
            "port",
            self.container_name,
            str(_CDP_PORT),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise BrowserSidecarError(
                f"Failed to resolve sidecar port: {result.stderr.strip()}"
            )

        # Output format: "0.0.0.0:PORT\n" or "[::]:PORT\n"
        for line in result.stdout.splitlines():
            line = line.strip()
            if ":" in line:
                port_str = line.rsplit(":", 1)[-1]
                try:
                    return int(port_str)
                except ValueError:
                    continue

        raise BrowserSidecarError(
            f"Could not parse port from: {result.stdout.strip()}"
        )

    def _wait_for_endpoint(self, port: int) -> str:
        """Poll the DevTools endpoint until it returns a WebSocket URL.

        Chrome's ``/json/version`` endpoint returns the browser's WS URL.
        """
        import urllib.request

        url = f"http://127.0.0.1:{port}/json/version"
        deadline = time.monotonic() + _STARTUP_TIMEOUT

        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(url, timeout=2) as resp:
                    data = json.loads(resp.read())
                ws_url = data.get("webSocketDebuggerUrl", "")
                if ws_url:
                    # Replace the internal address with the host-accessible one.
                    # From inside another container, use the gateway IP.
                    ws_url = ws_url.replace("ws://0.0.0.0:", f"ws://127.0.0.1:{port}/")
                    ws_url = ws_url.replace(
                        f"ws://127.0.0.1:{_CDP_PORT}/",
                        f"ws://127.0.0.1:{port}/",
                    )
                    return ws_url
            except Exception:
                time.sleep(_HEALTH_CHECK_INTERVAL)

        raise BrowserSidecarError(
            f"Sidecar did not become ready within {_STARTUP_TIMEOUT}s"
        )


def ws_endpoint_for_container(ws_url: str) -> str:
    """Convert a host-local WS URL to one reachable from a container.

    In rootless Podman, the host gateway is typically ``10.0.2.2``
    (slirp4netns) or ``host.containers.internal`` (pasta).
    We use ``host.containers.internal`` which works with both.
    """
    return ws_url.replace("127.0.0.1", "host.containers.internal")


class BrowserSidecarError(Exception):
    """Error starting or managing the browser sidecar."""
