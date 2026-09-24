"""A malformed persona endpoint is refused WITHOUT printing its credential.

``persona_store.validate_endpoint`` raises three ``ConfigError``s, and each one
names the endpoint.  A malformed endpoint is still the user's, credential and
all, so every message renders it through the ONE userinfo scrub
(``targets.base._scrub_endpoint_userinfo``) that the probe evidence uses: the
credential goes, the scheme and host stay legible where the scrub can tell
them apart from userinfo.

The helper's malformed-input branch is pinned here too, because these errors
are what feeds it malformed input.  Its well-formed behavior is pinned beside
the evidence block in ``test_targets/test_persona_settings.py``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from kanibako.errors import ConfigError
from kanibako.persona_store import read_persona_bundle, validate_endpoint
from kanibako.targets.base import _scrub_endpoint_userinfo

_TOKEN = "sk-endpoint-userinfo-7QX3"


def _refusal(endpoint: str) -> str:
    with pytest.raises(ConfigError) as info:
        validate_endpoint(endpoint)
    return str(info.value)


class TestNotAWellFormedUrl:
    """``urlsplit`` itself raises — and urllib quotes the raw netloc in some texts."""

    def test_a_broken_ipv6_host_keeps_the_scheme_and_host(self):
        message = _refusal(f"https://{_TOKEN}@[::1/v1")
        assert "is not a well-formed URL" in message
        assert _TOKEN not in message
        assert "https://<redacted>@[::1/v1" in message
        assert "Invalid IPv6 URL" in message

    def test_urllibs_own_detail_is_scrubbed_too(self):
        """The NFKC refusal text quotes the netloc — userinfo included — so the
        detail is re-read off the scrubbed endpoint rather than printed raw."""
        message = _refusal(f"https://{_TOKEN}@host／x/v1")
        assert _TOKEN not in message
        assert "netloc '<redacted>@host／x'" in message

    def test_a_fault_inside_the_userinfo_is_named_without_printing_it(self):
        message = _refusal("https://sk-brack[et-9F@gw.example.com/v1")
        assert "sk-brack" not in message
        assert "https://<redacted>@gw.example.com/v1" in message
        assert "the userinfo before '@' is not well-formed" in message

    def test_the_raw_error_is_not_chained(self):
        """``__cause__`` would carry urllib's raw text to any traceback or
        ``logging.exception``."""
        with pytest.raises(ConfigError) as info:
            validate_endpoint(f"https://{_TOKEN}@host／x/v1")
        assert info.value.__cause__ is None
        assert info.value.__suppress_context__


class TestNoRecognisedScheme:
    def test_a_misspelled_scheme_keeps_the_scheme_and_host(self):
        message = _refusal(f"htps://{_TOKEN}@gw.example.com/v1")
        assert "has no recognised scheme" in message
        assert _TOKEN not in message
        assert "'htps://<redacted>@gw.example.com/v1'" in message
        assert "(got 'htps';" in message

    def test_a_scheme_less_endpoint_loses_its_userinfo(self):
        """The live incident's shape (``myhost:8080/v1``) with a credential in
        front: no ``//``, so urllib sees no authority at all."""
        message = _refusal(f"{_TOKEN}@myhost:8080/v1")
        assert _TOKEN not in message
        assert "'<redacted>@myhost:8080/v1'" in message

    def test_a_username_misread_as_the_scheme_is_not_echoed(self):
        """``user:pw@host`` splits with scheme ``user`` — the ``got`` echo would
        print the username the endpoint line just scrubbed."""
        message = _refusal("agent-7Q:pw-8Z@gw.example.com/v1")
        assert "agent-7Q" not in message
        assert "pw-8Z" not in message
        assert "'<redacted>@gw.example.com/v1'" in message
        assert "(got '<redacted>';" in message

    def test_a_credential_free_endpoint_reads_as_before(self):
        """Legibility pin: the live incident's own message is unchanged."""
        assert _refusal("myhost:8080/v1") == (
            "persona endpoint 'myhost:8080/v1' has no recognised scheme "
            "(got 'myhost'; must start with 'http://' or 'https://')"
        )


class TestNoHostAfterTheScheme:
    @pytest.mark.parametrize("endpoint, shown", [
        (f"https://{_TOKEN}@", "https://<redacted>@"),
        (f"https://{_TOKEN}@/v1", "https://<redacted>@/v1"),
        (f"https:///{_TOKEN}@gw.example.com/v1", "https:///<redacted>@gw.example.com/v1"),
    ], ids=["nothing-after-at", "path-after-at", "triple-slash-typo"])
    def test_the_credential_goes_and_the_scheme_stays(self, endpoint, shown):
        message = _refusal(endpoint)
        assert "names no host after the scheme" in message
        assert _TOKEN not in message
        assert f"'{shown}'" in message
        assert "(expected 'https://<host>')" in message

    def test_a_scheme_the_scrub_took_is_still_named_in_the_hint(self):
        """``https:tok@host`` prints as ``<redacted>@host``, hiding both the
        scheme and the missing ``//``; the scheme passed the gate, so the hint
        can name the form the endpoint needed without printing the token."""
        message = _refusal(f"https:{_TOKEN}@gw.example.com")
        assert _TOKEN not in message
        assert "'<redacted>@gw.example.com'" in message
        assert "(expected 'https://<host>')" in message


class TestTheStoreRejectReason:
    """End to end: the reject reason a launch prints carries the scrubbed text."""

    def test_a_credential_in_a_malformed_store_endpoint_is_not_printed(self, tmp_home):
        config_dir = Path(os.environ["XDG_CONFIG_HOME"]) / "personas" / "navigator" / "claude"
        config_dir.mkdir(parents=True)
        (config_dir / "settings.json").write_text(json.dumps({
            "env": {
                "ANTHROPIC_BASE_URL": f"htps://{_TOKEN}@gw.example.com/v1",
                "ANTHROPIC_AUTH_TOKEN": "sk-never-read",
            },
            "model": "gemma4",
        }))
        from kanibako.plugins.claude.target import ClaudeTarget

        bundle = read_persona_bundle("navigator+claude", ClaudeTarget())
        assert bundle is not None
        assert bundle.reject_reason is not None
        assert _TOKEN not in bundle.reject_reason
        assert "htps://<redacted>@gw.example.com/v1" in bundle.reject_reason


class TestTheScrubOnMalformedInput:
    """The one helper's fallback, for strings ``urlsplit`` raises on or finds no
    authority in."""

    @pytest.mark.parametrize("endpoint, shown", [
        ("https://tok@[::1", "https://<redacted>@[::1"),
        ("tok@myhost:8080/v1", "<redacted>@myhost:8080/v1"),
        ("user:pw@host/v1", "<redacted>@host/v1"),
        ("https:/tok@host/v1", "https:/<redacted>@host/v1"),
        ("//tok@host/v1", "//<redacted>@host/v1"),
        ("https://a:b@c@host/v1", "https://<redacted>@host/v1"),
    ], ids=["unsplittable", "scheme-less", "user-password-no-slashes",
            "single-slash-typo", "network-path", "last-at-wins"])
    def test_the_userinfo_span_is_dropped(self, endpoint, shown):
        assert _scrub_endpoint_userinfo(endpoint) == shown

    def test_a_scheme_with_no_slashes_goes_with_the_userinfo(self):
        """Over-redaction, by design: ``https:tok@host`` cannot be told apart
        from ``user:pw@host``, so both lose everything before the ``@``."""
        assert _scrub_endpoint_userinfo("https:tok@host") == "<redacted>@host"

    def test_a_first_path_segment_goes_with_the_userinfo(self):
        """Over-redaction, by design: with no authority, an ``@`` in the first
        path segment reads as userinfo there."""
        assert (
            _scrub_endpoint_userinfo("gw.example.com/team@corp/v1")
            == "gw.example.com/<redacted>@corp/v1"
        )

    @pytest.mark.parametrize("endpoint", [
        "gw.example.com/v1?notify=ops@example.com",
        "host?x=/a@b",
        "host#/a@b",
        "a/b/c@d",
        "myhost:8080/v1",
    ], ids=["query-at", "query-at-after-slash", "fragment-at", "later-path-segment",
            "no-at"])
    def test_an_at_outside_the_would_be_authority_is_left_alone(self, endpoint):
        assert _scrub_endpoint_userinfo(endpoint) == endpoint
