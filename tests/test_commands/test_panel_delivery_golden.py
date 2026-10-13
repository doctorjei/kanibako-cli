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

DELIBERATE content changes so far (each = a reviewed regeneration commit):

* Phase 2 D2 (codex liveness markers): the 12 ``codex--*`` fixtures gained the
  per-PID marker ``[[hooks.SessionStart]]`` group + its ``[hooks.state]`` entry
  (+10 lines each, nothing else); claude/goose fixtures byte-UNCHANGED.

* C-CANON R1 (flattener relocation, P-2): the SessionStart hook command in all 6
  ``claude--*`` and 12 ``codex--*`` fixtures moves from
  ``$HOME/playbook/kanibako/scripts/import-directives.py`` to
  ``/opt/kanibako/kanibako/scripts/import-directives.py``. The flattener is
  MACHINERY, not canon: it now ships in the kanibako package and arrives through
  the existing unconditional ``kani_pkg`` bind, so the read-only canon holds only
  instructional text. The codex ``trusted_hash`` moves with it BY DESIGN — it is a
  content hash OF the hook command, so a command change that left it untouched
  would be the bug (codex would prompt at ``/hooks`` on first launch).

* sandbox_mode box invariant: the 12 ``codex--*`` fixtures now carry
  ``sandbox_mode = "danger-full-access"`` UNCONDITIONALLY (was the yolo-gated
  ``workspace-write``, present only ON) — the panel's own app-server must not
  attempt a nested bubblewrap sandbox the container blocks.  So the OFF fixtures
  gain the key and the user fixtures migrate their ``read-only`` to it;
  approval_policy was unchanged at the time (still gated on the boolean).

* The per-PID marker hooks stop being inline shell and become CALLS into the
  packaged charter's ``pid-add.sh`` / ``pid-rm.sh``: the 9 ``claude--*`` fixtures
  change both marker commands, the 12 ``codex--*`` change the write command and
  its ``[hooks.state]`` hash (which is a content hash OF that command, so it must
  move with it); the 6 ``goose--*`` are byte-UNCHANGED.  The scripts arrive by the
  unconditional ``charter/general`` rom bind, so a codex box has them too.

* codex ``restricted`` SETS ``approval_policy = "untrusted"`` (Editor round,
  R-41 follow-up): the 6 ``codex--*--access-restricted--*`` fixtures gain that
  line.  ``restricted`` used to CLEAR the key, which handed the tier to codex's
  own undocumented default while this same writer forced ``sandbox_mode`` open —
  and left ``restricted`` potentially byte-identical to ``editing``.  Now all
  three tiers deliver DISTINCT, kanibako-chosen bytes.

* The core tome is RENAMED ``bible`` → ``charter``, so both marker commands take
  the new box path: the 9 ``claude--*`` and 18 ``codex--*`` fixtures move their
  ``~/canon/charter/general/scripts/util/`` prefix (and the codex ``[hooks.state]``
  hash with it); the 6 ``goose--*`` are byte-UNCHANGED.

* goose stops writing the ``access`` tier into its config.yaml (the launched goose
  gets it as the ``GOOSE_MODE`` env var): the 6 ``goose--*`` fixtures are deleted
  and ``test_goose_delivers_nothing`` pins the absence.

* claude stops writing ``permissions.defaultMode`` (the launched claude gets every
  tier as a flag; ``restricted`` is ``--permission-mode default``): the 3
  ``claude--*--access-full`` and 3 ``--access-editing`` fixtures lose the key or
  keep the user's "plan", so each claude pre-state is now the same bytes at all
  three tiers; the ``--access-restricted`` fixtures are byte-UNCHANGED.

* codex stops writing ``approval_policy`` (the launched codex gets every tier as
  flags; the approval half is ``-c approval_policy="on-request"``): the 12
  absent/relaunch ``codex--*`` fixtures lose the line, and the user pre-state
  gains a user ``approval_policy = "never"`` that survives unchanged, so each
  codex (pre-state, provider) cell is now the same bytes at all three tiers
  (``test_codex_fixtures_identical_across_tiers``).

* codex gets claude's canon hooks: the 18 ``codex--*`` fixtures swap the bare
  ``pid-add.sh`` SessionStart group for the four charter start scripts (each
  cascades to ``pid-add.sh``), gain the mail monitor and ``edited.sh``
  (PostToolUse) and ``stop.sh`` (Stop), and every managed handler gains an
  explicit ``timeout`` with one ``[hooks.state]`` entry each.  NO ``end.sh``
  SessionEnd group: codex fires SessionEnd per thread in one shared app-server
  PID, so its pid-rm would clear a live panel's marker.  The directive group's
  hash is unchanged (600 was already its implicit timeout); the 9 ``claude--*``
  fixtures are byte-UNCHANGED.

Matrix per agent (delivered file: claude ``.claude/settings.json``, codex
``.codex/config.toml``).  goose delivers NOTHING — its tier reaches the launched
goose as the ``GOOSE_MODE`` env var only — so it has no fixtures; the module's
last test asserts its config.yaml is left byte-identical at every tier:

* pre-state ∈ {absent, user (representative user content incl. user-chosen
  values + a user hook group), relaunch (the absent-run output fed back in —
  asserts idempotence AND that the second pass reports no write)}
* ``access`` ∈ {restricted, editing, full}
* codex only: ``model_provider`` ∈ {None, the navigator persona sample}

→ 9 claude + 18 codex = 27 fixtures under
``tests/fixtures/panel_delivery/``, named
``<agent>--<pre>--access-<tier>[--prov-<none|nav>].<real suffix>``.

⚑ R-41 REGENERATION (this pass): the ``auto-on``/``auto-off`` cells were RENAMED
to ``access-full``/``access-restricted`` and their bytes verified BYTE-IDENTICAL
to the pre-R-41 fixtures — the boolean's two states map onto two of the three
tiers exactly, so the tier rework provably changed nothing for a box that used
either of them.  The 11 new ``access-editing`` cells (claude + codex) are the
only genuinely new bytes.

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

from kanibako.vscode.vscode_config import CodexModelProvider

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
#: The agents whose delivery writes bytes — the fixture axis.
_GOLDEN_AGENTS = ("claude", "codex")

# Fixture filename suffix = the real delivered filename, for at-a-glance format.
_FIXTURE_SUFFIX = {
    "claude": ".settings.json",
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


_TIERS = ("restricted", "editing", "full")


def _matrix() -> list[tuple[str, str, str, CodexModelProvider | None]]:
    """Every (agent, pre_state, access, model_provider) cell, in a fixed
    deterministic order.  codex fans out over the provider axis; claude
    always launches with ``provider=None`` (the call site only ever resolves a
    provider for a codex persona)."""
    cells: list[tuple[str, str, str, CodexModelProvider | None]] = []
    for agent in _GOLDEN_AGENTS:
        providers: tuple[CodexModelProvider | None, ...] = (
            (None, NAVIGATOR_PROVIDER) if agent == "codex" else (None,)
        )
        for pre in _PRE_STATES:
            for tier in _TIERS:
                for provider in providers:
                    cells.append((agent, pre, tier, provider))
    return cells


def _cell_stem(agent: str, pre: str, tier: str, provider) -> str:
    stem = f"{agent}--{pre}--access-{tier}"
    if agent == "codex":
        stem += f"--prov-{'nav' if provider is not None else 'none'}"
    return stem


def _fixture_name(agent: str, pre: str, tier: str, provider) -> str:
    return _cell_stem(agent, pre, tier, provider) + _FIXTURE_SUFFIX[agent]


_MATRIX = _matrix()
_IDS = [_cell_stem(a, p, t, pr) for a, p, t, pr in _MATRIX]


# --------------------------------------------------------------------------- #
# Representative USER pre-state content (fixed literals — deterministic)
# --------------------------------------------------------------------------- #

# claude: existing user settings + a user hook group + a user-chosen
# ``permissions.defaultMode`` ("plan"), which no tier touches.
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

# codex: user root keys incl. a ``sandbox_mode`` that kanibako OWNS — always
# MIGRATED to the ``danger-full-access`` box invariant (both ON and OFF), NOT
# preserved — + a user table + a user ``[[hooks.SessionStart]]`` group
# (exercises trust-key ``group_index`` != 0).
_CODEX_USER_CONFIG = """\
# user config — hands off
model_reasoning_effort = "high"
sandbox_mode = "read-only"
approval_policy = "never"

[profiles.fast]
model = "gpt-5-codex"

[[hooks.SessionStart]]
matcher = "startup"

[[hooks.SessionStart.hooks]]
type = "command"
command = "echo user-session-start"
"""

# goose: existing user keys (top-level scalars + a nested block), including the
# user's own GOOSE_MODE, which no tier may touch.
_GOOSE_USER_CONFIG = """\
GOOSE_PROVIDER: openai
GOOSE_MODEL: gpt-4o
GOOSE_MODE: smart_approve
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
    access: str,
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
        config_root=config_root, access=access,
    )
    wrote = (
        target.deliver_directive_hook(
            config_root=config_root,
            access=access,
            model_provider=provider,
        )
        or wrote
    )
    return wrote


def _sanity_check(
    agent: str,
    tier: str,
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
        # No tier writes ``defaultMode`` (the launch flag carries it): absent
        # stays absent and the user's own "plan" survives at every tier.
        mode = doc.get("permissions", {}).get("defaultMode")
        assert mode in (None, "plan")
    else:  # codex
        assert "# >>> kanibako-managed (instruction-delivery hook + trust)" in text
        # No tier writes approval_policy (the launch flag carries it): absent
        # stays absent and the user's own "never" survives at every tier.
        # sandbox_mode is a BOX INVARIANT always forced to danger-full-access.
        assert text.count("approval_policy") == text.count(
            'approval_policy = "never"'
        ) <= 1
        assert 'sandbox_mode = "danger-full-access"' in text
        assert ("# >>> kanibako-managed (model provider)" in text) == (
            provider is not None
        )


# --------------------------------------------------------------------------- #
# The golden gate
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(("agent", "pre", "tier", "provider"), _MATRIX, ids=_IDS)
def test_delivered_bytes_match_golden(tmp_path, agent, pre, tier, provider):
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
            agent=agent, config_root=config_root, access=tier, provider=provider,
        )
        assert first is True, "absent-state first pass must report a write"

    wrote = drive(
        agent=agent, config_root=config_root, access=tier, provider=provider,
    )
    if pre == "relaunch":
        assert wrote is False, "relaunch pass must report NO write (idempotence)"
    else:
        assert wrote is True

    delivered = delivered_path.read_bytes()
    _sanity_check(agent, tier, provider, delivered, tmp_path)

    fixture = FIXTURES_DIR / _fixture_name(agent, pre, tier, provider)
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


@pytest.mark.parametrize("pre", ["absent", "user", "relaunch"])
@pytest.mark.parametrize("prov", ["none", "nav"])
def test_codex_fixtures_identical_across_tiers(pre, prov):
    """codex's config.toml no longer carries the tier, so each (pre-state,
    provider) cell is the same bytes at every tier."""
    blobs = {
        (FIXTURES_DIR / f"codex--{pre}--access-{tier}--prov-{prov}.config.toml")
        .read_bytes()
        for tier in ("restricted", "editing", "full")
    }
    assert len(blobs) == 1


def test_fixture_inventory_matches_matrix():
    """The fixture dir holds EXACTLY the matrix's files — no strays, no gaps."""
    if _update_mode():
        pytest.skip("update mode: inventory is being (re)written by this run")
    expected = {_fixture_name(a, p, t, pr) for a, p, t, pr in _MATRIX}
    assert len(expected) == 27  # 9 claude + 18 codex
    actual = {p.name for p in FIXTURES_DIR.iterdir() if p.is_file()}
    assert actual == expected


@pytest.mark.parametrize("tier", _TIERS)
@pytest.mark.parametrize("pre", ("absent", "user"))
def test_goose_delivers_nothing(tmp_path, pre, tier):
    """goose's config.yaml is the user's: no tier writes to it.

    The launched goose gets the tier as the ``GOOSE_MODE`` env var; the VS Code
    panel's goose reads the file's own ``GOOSE_MODE``.  A user's value survives a
    start byte-identical, and an absent file is not created.
    """
    config_root = tmp_path / "box-home"
    config_root.mkdir()
    delivered_path = config_root / DELIVERED_FILE["goose"]
    if pre == "user":
        delivered_path.parent.mkdir(parents=True, exist_ok=True)
        delivered_path.write_text(_USER_CONTENT["goose"])
    before = delivered_path.read_bytes() if delivered_path.exists() else None

    wrote = _drive_seam_path(
        agent="goose", config_root=config_root, access=tier, provider=None,
    )

    assert wrote is False
    after = delivered_path.read_bytes() if delivered_path.exists() else None
    assert after == before
    if pre == "absent":
        assert not (config_root / ".config").exists()
