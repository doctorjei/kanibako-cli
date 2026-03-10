"""Persistent browser state for automated OAuth refresh.

Stores Playwright browser context (cookies, localStorage) so that the
OAuth provider recognizes the session on subsequent refreshes without
requiring a full re-login.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from kanibako.log import get_logger

logger = get_logger("browser_state")


@dataclass
class BrowserState:
    """Persistent browser context for OAuth session reuse.

    Serialized as JSON at ``{data_path}/browser-state/context.json``.
    """

    cookies: list[dict] = field(default_factory=list)
    origins: list[dict] = field(default_factory=list)  # localStorage per origin
    updated_at: float = 0.0

    def is_fresh(self, max_age_days: float = 30.0) -> bool:
        """Check if the stored state is recent enough to be useful."""
        if not self.cookies:
            return False
        age = time.time() - self.updated_at
        return age < max_age_days * 86400


def state_path(data_path: Path) -> Path:
    """Return the browser state file path."""
    return data_path / "browser-state" / "context.json"


def load_state(data_path: Path) -> BrowserState:
    """Load browser state from disk.  Returns empty state on missing/corrupt file."""
    path = state_path(data_path)
    if not path.is_file():
        return BrowserState()

    try:
        with open(path) as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return BrowserState()
        return BrowserState(
            cookies=data.get("cookies", []),
            origins=data.get("origins", []),
            updated_at=float(data.get("updated_at", 0)),
        )
    except (json.JSONDecodeError, OSError, ValueError) as exc:
        logger.warning("Failed to load browser state: %s", exc)
        return BrowserState()


def save_state(data_path: Path, state: BrowserState) -> None:
    """Persist browser state to disk."""
    path = state_path(data_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    state.updated_at = time.time()
    data = {
        "cookies": state.cookies,
        "origins": state.origins,
        "updated_at": state.updated_at,
    }
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    logger.debug("Saved browser state: %d cookies", len(state.cookies))


def clear_state(data_path: Path) -> None:
    """Remove stored browser state (e.g. on logout or credential invalidation)."""
    path = state_path(data_path)
    if path.is_file():
        path.unlink()
        logger.debug("Cleared browser state")


def to_playwright_context(state: BrowserState) -> dict:
    """Convert BrowserState to Playwright's storageState format."""
    return {
        "cookies": state.cookies,
        "origins": state.origins,
    }


def from_playwright_context(context: dict) -> BrowserState:
    """Create BrowserState from Playwright's storageState output."""
    return BrowserState(
        cookies=context.get("cookies", []),
        origins=context.get("origins", []),
        updated_at=time.time(),
    )
