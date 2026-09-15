"""Persistent browser state for automated OAuth refresh.

Stores Playwright browser context (cookies, localStorage) so that the
OAuth provider recognizes the session on subsequent refreshes without
requiring a full re-login.

⚑ THE JAR IS STATE, NOT DATA ([R168]).  It persists between runs on purpose, and that
never decided anything: the test is precious-and-portable vs regenerable-and-machine-local,
and a cookie jar is the latter — worthless on another machine, authored by nobody, and worth
an authorization rather than anything of the user's if it is lost.  So it derives from
``system.state`` and from nothing else ([R166]), and this module resolves that key ITSELF
rather than taking a directory from its caller: the one parameter ``auth_browser`` ever
threaded here existed only to locate this file.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from kanibako.log import get_logger
from kanibako.settings.paths import resolve_state_path

logger = get_logger("browser_state")


@dataclass
class BrowserState:
    """Persistent browser context for OAuth session reuse.

    Serialized as JSON at ``<system.state>/browser-state/context.json``.
    """

    cookies: list[dict] = field(default_factory=list)
    origins: list[dict] = field(default_factory=list)  # localStorage per origin
    updated_at: float = 0.0


def state_path() -> Path:
    """The browser state file — under ``system.state``, the declared key.

    [R166]: a state store derives from ``system.state`` and from nothing else, so this is
    the key's RESOLVED value, never ``$XDG_STATE_HOME`` composed with a leaf and never
    ``config.data``.  :func:`resolve_state_path` is PURE and TOTAL (never raises, creates
    nothing): it degrades to the key's own default, ``$XDG_STATE_HOME/kanibako``, whenever
    the host config or settings file is absent, unreadable or malformed — so this function
    is as total as the hardcoded join it replaces, and reaches a repointed state root when
    those files ARE readable.
    """
    return resolve_state_path() / "browser-state" / "context.json"


def load_state() -> BrowserState:
    """Load browser state from disk.  Returns empty state on missing/corrupt file."""
    path = state_path()
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


def save_state(state: BrowserState) -> None:
    """Persist browser state to disk."""
    path = state_path()
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
