"""The shipped ``statusline.sh`` — its printed line, and where it writes.

Each test runs the real script with ``bash`` under a throwaway ``HOME``, feeding
statusline JSON on stdin.  The script parses with ``jq``, so every test skips
when ``jq`` is not installed.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "src/kanibako/plugins/claude/data/base/canon/handbook/scripts/interface"
    / "statusline.sh"
)

pytestmark = pytest.mark.skipif(shutil.which("jq") is None, reason="jq is not installed")

# Color, bracketed used/max, percentage, then the (empty) cost field.
_LINE = re.compile(r"^\x1b\[3[123]m \[[0-9.]+/[0-9.]+ kTok\]\x1b\[0m \([0-9.]+%\) $")


def _payload(**extra) -> str:
    data = {
        "context_window": {
            "context_window_size": 200000,
            "current_usage": {"input_tokens": 50000},
        },
        "cost": {"total_cost_usd": 1.5},
    }
    data.update(extra)
    return json.dumps(data)


def _run(home: Path, stdin: str) -> subprocess.CompletedProcess:
    (home / ".claude").mkdir(parents=True, exist_ok=True)
    return subprocess.run(
        ["bash", str(SCRIPT)], input=stdin, capture_output=True, text=True,
        env={"HOME": str(home), "PATH": os.environ["PATH"]}, timeout=30,
    )


def _files(root: Path) -> set[str]:
    return {str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()}


_OWN = {".claude/context-status.json", ".claude/context-usage.txt"}


def test_normal_payload_prints_the_line_and_records_the_session(tmp_path):
    home = tmp_path / "home"
    result = _run(home, _payload(session_id="abc-123"))
    assert result.returncode == 0, result.stderr
    assert _LINE.match(result.stdout.rstrip("\n")), repr(result.stdout)
    assert _files(home) == _OWN | {".claude/context-lastvalid/abc-123"}


@pytest.mark.parametrize("session_id", [
    "../outside", "../../outside", "a/../../../outside", "/abs/outside", ".", "..",
])
def test_hostile_session_id_stays_in_the_state_dir(tmp_path, session_id):
    """The id names a state file; no value may place that file outside its dir."""
    home = tmp_path / "home"
    result = _run(home, _payload(session_id=session_id))
    assert result.returncode == 0, result.stderr
    assert _LINE.match(result.stdout.rstrip("\n")), repr(result.stdout)
    assert not (tmp_path / "outside").exists()
    written = _files(home) - _OWN
    assert len(written) == 1, written
    (state,) = written
    assert Path(state).parent == Path(".claude/context-lastvalid"), state


@pytest.mark.parametrize("session_id", ["", None, "absent"], ids=["empty", "null", "absent"])
def test_missing_session_id_writes_no_state_file(tmp_path, session_id):
    home = tmp_path / "home"
    payload = _payload() if session_id == "absent" else _payload(session_id=session_id)
    result = _run(home, payload)
    assert result.returncode == 0, result.stderr
    assert _LINE.match(result.stdout.rstrip("\n")), repr(result.stdout)
    assert _files(home) == _OWN
