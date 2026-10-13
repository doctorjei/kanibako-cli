"""The shipped ``mail-stop.sh`` goose Stop hook: a check-comms alert becomes a Stop block.

Each test runs the real adapter with ``bash`` under a throwaway ``HOME`` holding the
real core ``check-comms.sh`` at its canon path, and a unique ``KANIBAKO_NAME`` so the
``/tmp/kanibako-comms-<name>`` state dir starts fresh.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
ADAPTER = (
    _REPO / "packages/agent-goose/src/kanibako/plugins/goose/data/agents-plugin/hooks"
    / "mail-stop.sh"
)
CHECK_COMMS = (
    _REPO / "src/kanibako/data/templates/system/canon/handbook/general/scripts/behavior"
    / "check-comms.sh"
)
_TOOLS = (
    "bash", "cat", "mkdir", "touch", "find", "head", "wc",
    "sort", "md5sum", "cut", "sed", "rm",
)


@pytest.fixture
def box(tmp_path):
    home = tmp_path / "home"
    inbox = home / "channels" / "inbox"
    inbox.mkdir(parents=True)
    canon = home / "canon/handbook/general/scripts/behavior"
    canon.mkdir(parents=True)
    shutil.copy2(CHECK_COMMS, canon / "check-comms.sh")
    (canon / "check-comms.sh").chmod(0o755)
    name = f"pytest-mail-stop-{uuid.uuid4().hex}"
    yield home, inbox, name
    shutil.rmtree(f"/tmp/kanibako-comms-{name}", ignore_errors=True)


def _no_jq_path(tmp_path: Path) -> str:
    shim = tmp_path / "bin-no-jq"
    shim.mkdir()
    for tool in _TOOLS:
        real = shutil.which(tool)
        assert real, f"{tool} is not installed"
        (shim / tool).symlink_to(real)
    return str(shim)


def _stop(home: Path, name: str, path: str | None = None) -> subprocess.CompletedProcess:
    env = {"HOME": str(home), "KANIBAKO_NAME": name,
           "PATH": path if path is not None else os.environ["PATH"]}
    return subprocess.run(
        [shutil.which("bash") or "/bin/bash", str(ADAPTER)],
        input='{"event":"Stop","session_id":"s","matcher_context":null}',
        capture_output=True, text=True, env=env, timeout=30,
    )


@pytest.fixture(params=["jq", "no-jq"])
def path(request, tmp_path):
    if request.param == "jq":
        if not shutil.which("jq"):
            pytest.skip("jq is not installed")
        return None
    return _no_jq_path(tmp_path)


def test_no_mail_allows_the_stop(box, path):
    home, _inbox, name = box
    r = _stop(home, name, path)
    assert (r.returncode, r.stdout) == (0, "")


def test_new_mail_blocks_once_with_the_alert(box, path):
    home, inbox, name = box
    _stop(home, name, path)
    (inbox / 'a "quoted" \\ name.md').write_text("hi")
    r = _stop(home, name, path)
    assert r.returncode == 0
    assert json.loads(r.stdout) == {
        "decision": "block", "reason": 'NEW MAIL (1): a "quoted" \\ name.md',
    }
    # The block is goose's only route to the model; the next stop must not re-block.
    again = _stop(home, name, path)
    assert (again.returncode, again.stdout) == (0, "")


def test_a_repeating_alert_blocks_only_once(box, path):
    """A check-comms fault re-alerts every run; one block, not GOOSE_STOP_HOOK_BLOCK_CAP."""
    home, inbox, name = box
    locked = inbox / "locked"
    locked.mkdir()
    locked.chmod(0)
    try:
        if os.access(locked, os.R_OK):
            pytest.skip("running as a user that reads mode-000 dirs")
        first = _stop(home, name, path)
        assert "MAIL SCAN INCOMPLETE" in json.loads(first.stdout)["reason"]
        second = _stop(home, name, path)
        assert (second.returncode, second.stdout) == (0, "")
    finally:
        locked.chmod(0o755)
    # A clean check clears the memo, so the next alert blocks again.
    assert _stop(home, name, path).stdout == ""
    (inbox / "b.md").write_text("hi")
    assert json.loads(_stop(home, name, path).stdout)["reason"] == "NEW MAIL (1): b.md"


def test_missing_canon_script_allows_the_stop(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    r = _stop(home, f"pytest-mail-stop-{uuid.uuid4().hex}")
    assert (r.returncode, r.stdout) == (0, "")
