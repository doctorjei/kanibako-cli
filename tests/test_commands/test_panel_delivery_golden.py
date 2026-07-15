"""GOLDEN byte-identity gate for the launch-side panel/directive delivery.

Phase-1 Step 1 of the codex-VSCode seam plan (``codex-vscode-BUILD.md`` §4):
these fixtures freeze the EXACT bytes today's delivery pipeline writes onto each
agent's panel-visible config surface, captured at the pre-change SHA.  Every
later step (Target seams, codex approval split, wrapper deletion) must keep the
delivered files BYTE-IDENTICAL — this module is the gate that proves it.

The driver reproduces the ``start.py:_deliver_panel_permissions`` call-site
sequence: the launching agent's real ``Target``, ``deliver_panel_permissions``
then ``deliver_directive_hook`` — core's unconditional seam order (T1.1/T1.2).
The fixtures were CAPTURED from the pre-seam legacy pipeline at ``27d5bd6`` and
the seam path was proven byte-identical against them before the legacy
wrappers were deleted (plan Steps 3-5); this module is now the permanent
byte-identity net (it also guards the later T2.3 emitter-body moves).

Matrix per agent (delivered file: claude ``.claude/settings.json``, goose
``.config/goose/config.yaml``, codex ``.codex/config.toml``):

* pre-state ∈ {absent, user (representative user content incl. user-chosen
  values + a user hook group), relaunch (the absent-run output fed back in —
  asserts idempotence AND that the second pass reports no write)}
* ``auto_approve`` ∈ {True, False}
* codex only: ``model_provider`` ∈ {None, the navigator persona sample}

→ 6 claude + 6 goose + 12 codex = 24 fixtures under
``tests/fixtures/panel_delivery/``, named
``<agent>--<pre>--auto-<on|off>[--prov-<none|nav>].<real suffix>``.

Fixtures are machine-independent: every in-file path is a GUEST_HOME
box-absolute literal; no host/tmp path ever appears in the bytes (asserted).

REGENERATION GUARD: fixtures are only (re)written when the environment variable
``KANI_UPDATE_GOLDENS=1`` is set::

    KANI_UPDATE_GOLDENS=1 pytest tests/test_commands/test_panel_delivery_golden.py

Regeneration must be a DELIBERATE act — a normal run never rewrites a fixture,
it only compares.  If a behavior change is intentional, regenerate, eyeball the
fixture diff, and commit it WITH the change.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import yaml

from kanibako.vscode_config import CodexModelProvider

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "panel_delivery"

_UPDATE_ENV = "KANI_UPDATE_GOLDENS"


def _update_mode() -> bool:
    return os.environ.get(_UPDATE_ENV) == "1"


# --------------------------------------------------------------------------- #
# Matrix
# --------------------------------------------------------------------------- #

# The delivered file per agent, relative to config_root (the box home as seen
# from the host) — the literals from today's call site / dispatcher.
DELIVERED_FILE = {
    "claude": Path(".claude") / "settings.json",
    "goose": Path(".config") / "goose" / "config.yaml",
    "codex": Path(".codex") / "config.toml",
}

# Fixture filename suffix = the real delivered filename, for at-a-glance format.
_FIXTURE_SUFFIX = {
    "claude": ".settings.json",
    "goose": ".config.yaml",
    "codex": ".config.toml",
}

# The navigator persona sample — verbatim from
# ``test_start.py:TestCodexPersonaLaunchWiring._provider`` (fixed literals).
NAVIGATOR_PROVIDER = CodexModelProvider(
    provider_id="navigator",
    name="navigator",
    base_url="https://api.example/v1",
    wire_api="chat",
    env_key="NAVIGATOR_API_KEY",
    model="gemma-4-31b-it",
)

_PRE_STATES = ("absent", "user", "relaunch")


def _matrix() -> list[tuple[str, str, bool, CodexModelProvider | None]]:
    """Every (agent, pre_state, auto_approve, model_provider) cell, in a fixed
    deterministic order.  codex fans out over the provider axis; claude/goose
    always launch with ``provider=None`` (the call site only ever resolves a
    provider for a codex persona)."""
    cells: list[tuple[str, str, bool, CodexModelProvider | None]] = []
    for agent in ("claude", "goose", "codex"):
        providers: tuple[CodexModelProvider | None, ...] = (
            (None, NAVIGATOR_PROVIDER) if agent == "codex" else (None,)
        )
        for pre in _PRE_STATES:
            for auto in (True, False):
                for provider in providers:
                    cells.append((agent, pre, auto, provider))
    return cells


def _cell_stem(agent: str, pre: str, auto: bool, provider) -> str:
    stem = f"{agent}--{pre}--auto-{'on' if auto else 'off'}"
    if agent == "codex":
        stem += f"--prov-{'nav' if provider is not None else 'none'}"
    return stem


def _fixture_name(agent: str, pre: str, auto: bool, provider) -> str:
    return _cell_stem(agent, pre, auto, provider) + _FIXTURE_SUFFIX[agent]


_MATRIX = _matrix()
_IDS = [_cell_stem(a, p, au, pr) for a, p, au, pr in _MATRIX]


# --------------------------------------------------------------------------- #
# Representative USER pre-state content (fixed literals — deterministic)
# --------------------------------------------------------------------------- #

# claude: existing user settings + a user hook group + a user-chosen
# ``permissions.defaultMode`` ("plan": overwritten by ON, PRESERVED by OFF).
_CLAUDE_USER_SETTINGS = (
    json.dumps(
        {
            "$schema": "https://json.schemastore.org/claude-code-settings.json",
            "includeCoAuthoredBy": False,
            "permissions": {"allow": ["Bash(ls:*)"], "defaultMode": "plan"},
            "hooks": {
                "SessionStart": [
                    {
                        "matcher": "startup",
                        "hooks": [
                            {"type": "command", "command": "echo user-session-start"}
                        ],
                    }
                ],
                "PreToolUse": [
                    {
                        "matcher": "Bash",
                        "hooks": [{"type": "command", "command": "echo user-pre-tool"}],
                    }
                ],
            },
        },
        indent=2,
    )
    + "\n"
)

# codex: user root keys incl. a user-chosen ``sandbox_mode`` (PRESERVED by OFF,
# overwritten by ON) + a user table + a user ``[[hooks.SessionStart]]`` group
# (exercises trust-key ``group_index`` != 0).
_CODEX_USER_CONFIG = """\
# user config — hands off
model_reasoning_effort = "high"
sandbox_mode = "read-only"

[profiles.fast]
model = "gpt-5-codex"

[[hooks.SessionStart]]
matcher = "startup"

[[hooks.SessionStart.hooks]]
type = "command"
command = "echo user-session-start"
"""

# goose: existing user keys (top-level scalars + a nested block).
_GOOSE_USER_CONFIG = """\
GOOSE_PROVIDER: openai
GOOSE_MODEL: gpt-4o
extensions:
  developer:
    enabled: true
"""

_USER_CONTENT = {
    "claude": _CLAUDE_USER_SETTINGS,
    "codex": _CODEX_USER_CONFIG,
    "goose": _GOOSE_USER_CONFIG,
}


# --------------------------------------------------------------------------- #
# Driver — TODAY's exact call-site sequence with the real call-site arguments
# --------------------------------------------------------------------------- #


def _drive_seam_path(
    *,
    agent: str,
    config_root: Path,
    auto_approve: bool,
    provider: CodexModelProvider | None,
) -> bool:
    """Drive the delivery for a launch of *agent*: its REAL Target,
    ``deliver_panel_permissions`` then ``deliver_directive_hook`` — core's
    exact call order (order is load-bearing for the composed codex/claude
    bytes).  The sole driver AND the ``KANI_UPDATE_GOLDENS`` capture source.
    NO try/except here — a delivery failure must fail the golden loudly, never
    silently capture broken bytes.  Returns whether ANY write occurred.
    """
    from kanibako.plugins.claude.target import ClaudeTarget
    from kanibako.plugins.codex.target import CodexTarget
    from kanibako.plugins.goose.target import GooseTarget

    target = {
        "claude": ClaudeTarget,
        "codex": CodexTarget,
        "goose": GooseTarget,
    }[agent]()
    wrote = target.deliver_panel_permissions(
        config_root=config_root, auto_approve=auto_approve,
    )
    wrote = (
        target.deliver_directive_hook(
            config_root=config_root,
            auto_approve=auto_approve,
            model_provider=provider,
        )
        or wrote
    )
    return wrote


def _sanity_check(
    agent: str,
    auto: bool,
    provider: CodexModelProvider | None,
    data: bytes,
    tmp_path: Path,
) -> None:
    """Structural guards so a silently-broken capture can never become a fixture.

    NOT byte assertions (the fixture IS the byte oracle) — just the invariants
    any correct delivery must satisfy, plus machine-independence.
    """
    text = data.decode()
    assert str(tmp_path) not in text, "host tmp path leaked into delivered bytes"
    if agent == "claude":
        doc = json.loads(text)
        commands = [
            h["command"]
            for group in doc["hooks"]["SessionStart"]
            for h in group["hooks"]
        ]
        assert any("import-directives.py" in c for c in commands)
        if auto:
            assert doc["permissions"]["defaultMode"] == "bypassPermissions"
        else:
            assert doc.get("permissions", {}).get("defaultMode") != "bypassPermissions"
    elif agent == "goose":
        doc = yaml.safe_load(text)
        assert doc["GOOSE_MODE"] == ("auto" if auto else "approve")
    else:  # codex
        assert "# >>> kanibako-managed (instruction-delivery hook + trust)" in text
        assert ('approval_policy = "never"' in text) == auto
        assert ('sandbox_mode = "workspace-write"' in text) == auto
        assert ("# >>> kanibako-managed (model provider)" in text) == (
            provider is not None
        )


# --------------------------------------------------------------------------- #
# The golden gate
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(("agent", "pre", "auto", "provider"), _MATRIX, ids=_IDS)
def test_delivered_bytes_match_golden(tmp_path, agent, pre, auto, provider):
    drive = _drive_seam_path
    config_root = tmp_path / "box-home"
    config_root.mkdir()
    delivered_path = config_root / DELIVERED_FILE[agent]

    if pre == "user":
        delivered_path.parent.mkdir(parents=True, exist_ok=True)
        delivered_path.write_text(_USER_CONTENT[agent])
    elif pre == "relaunch":
        # Feed the absent-run output back in: the first pass materializes the
        # delivered file, the second pass must be a byte-level no-op.
        first = drive(
            agent=agent, config_root=config_root, auto_approve=auto, provider=provider,
        )
        assert first is True, "absent-state first pass must report a write"

    wrote = drive(
        agent=agent, config_root=config_root, auto_approve=auto, provider=provider,
    )
    if pre == "relaunch":
        assert wrote is False, "relaunch pass must report NO write (idempotence)"
    else:
        assert wrote is True

    delivered = delivered_path.read_bytes()
    _sanity_check(agent, auto, provider, delivered, tmp_path)

    fixture = FIXTURES_DIR / _fixture_name(agent, pre, auto, provider)
    if _update_mode():
        fixture.parent.mkdir(parents=True, exist_ok=True)
        fixture.write_bytes(delivered)
        return
    assert fixture.exists(), (
        f"missing golden fixture {fixture.name}; regenerate DELIBERATELY with "
        f"{_UPDATE_ENV}=1 and commit the diff"
    )
    assert delivered == fixture.read_bytes(), (
        f"delivered bytes drifted from golden {fixture.name} — if intentional, "
        f"regenerate with {_UPDATE_ENV}=1 and commit the fixture diff"
    )


def test_fixture_inventory_matches_matrix():
    """The fixture dir holds EXACTLY the matrix's files — no strays, no gaps."""
    if _update_mode():
        pytest.skip("update mode: inventory is being (re)written by this run")
    expected = {_fixture_name(a, p, au, pr) for a, p, au, pr in _MATRIX}
    assert len(expected) == 24  # 6 claude + 6 goose + 12 codex
    actual = {p.name for p in FIXTURES_DIR.iterdir() if p.is_file()}
    assert actual == expected
