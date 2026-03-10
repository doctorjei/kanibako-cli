"""Parse Claude Code auth command output to extract OAuth URLs and codes.

Used by the automated OAuth refresh flow to extract the authorization URL
from ``claude auth login`` output and feed back the authorization code.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class AuthPrompt:
    """Parsed auth prompt from ``claude auth login`` output."""

    url: str
    code: str | None = None  # verification code (if displayed)


# URL pattern: look for anthropic or console.anthropic URLs
_URL_RE = re.compile(
    r"(https?://(?:console\.anthropic\.com|claude\.ai)[^\s\"'<>]+)",
)

# Verification code: typically 4-8 character alphanumeric.
# Matches patterns like "code: ABCD1234", "code is: XY12AB", "key = WXYZ99"
# Requires a colon or equals as separator to avoid false positives.
_CODE_RE = re.compile(
    r"(?:verification\s+code|code|key)\s*(?:is)?[:=]\s*([A-Z0-9]{4,8})\b",
    re.IGNORECASE,
)


def parse_auth_output(output: str) -> AuthPrompt | None:
    """Extract OAuth URL and optional code from claude auth login output.

    Returns *None* if no recognizable URL is found.
    """
    url_match = _URL_RE.search(output)
    if not url_match:
        return None

    url = url_match.group(1)

    code: str | None = None
    code_match = _CODE_RE.search(output)
    if code_match:
        code = code_match.group(1)

    return AuthPrompt(url=url, code=code)
