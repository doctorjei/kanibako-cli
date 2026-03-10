"""Automated OAuth refresh via headless browser.

Uses Playwright (optional dependency) to navigate the Claude Code OAuth
authorization page and click "Authorize" when the IdP session is still
valid.  Falls back to manual login when the session is stale.

Requires: ``pip install playwright && playwright install chromium``
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kanibako.browser_state import (
    BrowserState,
    from_playwright_context,
    load_state,
    save_state,
    to_playwright_context,
)
from kanibako.log import get_logger

logger = get_logger("auth_browser")

_AUTHORIZE_TIMEOUT_MS = 30_000
_NAVIGATION_TIMEOUT_MS = 30_000

# Lazy-loaded Playwright symbols.  Populated by _check_playwright() so that
# tests can patch them at the module level without actually importing Playwright.
sync_playwright: Any = None
PWTimeout: type[Exception] = Exception  # fallback type for except clauses


@dataclass
class AuthResult:
    """Result of an automated OAuth refresh attempt."""

    success: bool
    key: str | None = None
    error: str | None = None


def _check_playwright() -> bool:
    """Check if Playwright is available and populate module-level symbols."""
    global sync_playwright, PWTimeout  # noqa: PLW0603
    try:
        from playwright.sync_api import (  # type: ignore[import-not-found]
            sync_playwright as _sp,
            TimeoutError as _te,
        )
        sync_playwright = _sp
        PWTimeout = _te
        return True
    except ImportError:
        return False


def refresh_auth(
    url: str,
    data_path: Path,
    *,
    headless: bool = True,
) -> AuthResult:
    """Attempt automated OAuth re-authorization via headless browser.

    1. Load stored browser state (cookies from previous sessions)
    2. Navigate to the OAuth URL
    3. If authorize button is visible → click it → extract key
    4. If IdP login form is shown → abort (manual login required)
    5. Save updated browser state on success

    Returns :class:`AuthResult` with success status and optional key.
    """
    if not _check_playwright():
        return AuthResult(
            success=False,
            error="Playwright not installed. Run: pip install playwright && playwright install chromium",
        )

    state = load_state(data_path)
    storage_state = to_playwright_context(state) if state.cookies else None

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=headless)
            try:
                context = browser.new_context(
                    storage_state=storage_state,
                ) if storage_state else browser.new_context()

                page = context.new_page()
                page.set_default_timeout(_NAVIGATION_TIMEOUT_MS)

                logger.debug("Navigating to OAuth URL: %s", url)
                page.goto(url, wait_until="networkidle")

                # Detect page state
                result = _handle_auth_page(page)

                if result.success:
                    # Save updated browser context
                    ctx_data = context.storage_state()
                    new_state = from_playwright_context(ctx_data)
                    save_state(data_path, new_state)
                    logger.info("OAuth refresh succeeded")

                context.close()
                return result

            finally:
                browser.close()

    except PWTimeout:
        return AuthResult(success=False, error="OAuth page timed out")
    except Exception as exc:
        logger.warning("Browser automation failed: %s", exc)
        return AuthResult(success=False, error=str(exc))


def _handle_auth_page(page) -> AuthResult:
    """Detect and handle the OAuth authorization page.

    Looks for an authorize button or a login form. If the IdP session
    is still valid, the authorize button should be visible. If not,
    a login form (Google, GitHub, etc.) will be shown instead.
    """

    # Check for authorize/approve button (Anthropic consent screen)
    authorize_selectors = [
        'button:has-text("Authorize")',
        'button:has-text("Allow")',
        'button:has-text("Approve")',
        'input[type="submit"][value*="Authorize"]',
        'input[type="submit"][value*="Allow"]',
    ]

    for selector in authorize_selectors:
        try:
            button = page.wait_for_selector(selector, timeout=3000)
            if button and button.is_visible():
                logger.debug("Found authorize button: %s", selector)
                button.click()

                # Wait for redirect after authorization
                page.wait_for_load_state("networkidle")

                # Try to extract the authorization key from the page
                key = _extract_key(page)
                return AuthResult(success=True, key=key)
        except PWTimeout:
            continue

    # Check for IdP login form (Google, GitHub, etc.)
    login_indicators = [
        'input[type="email"]',
        'input[type="password"]',
        '#identifierId',  # Google
        '#login_field',   # GitHub
    ]

    for selector in login_indicators:
        try:
            el = page.wait_for_selector(selector, timeout=2000)
            if el and el.is_visible():
                return AuthResult(
                    success=False,
                    error="IdP session expired — manual login required",
                )
        except PWTimeout:
            continue

    # Neither authorize nor login found
    page_text = page.text_content("body") or ""
    logger.debug("Unrecognized page state. Body preview: %s", page_text[:200])
    return AuthResult(
        success=False,
        error="Unrecognized OAuth page — manual login required",
    )


def _extract_key(page) -> str | None:
    """Try to extract the authorization key from the post-authorize page."""
    # Look for common patterns: displayed code, input field with key, etc.
    key_selectors = [
        'code',
        '.authorization-code',
        'input[readonly]',
        'pre',
    ]

    for selector in key_selectors:
        try:
            el = page.wait_for_selector(selector, timeout=3000)
            if el:
                text = el.text_content() or el.get_attribute("value") or ""
                text = text.strip()
                if text and len(text) < 200:  # reasonable key length
                    return text
        except Exception:
            continue

    return None
