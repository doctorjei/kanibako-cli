"""Claude credential copy, JSON merge, and mtime-based freshness."""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

from kanibako.utils import cp_if_newer


def merge_oauth_in(src: Path, dst: Path) -> bool:
    """Merge ``claudeAiOauth`` from host creds *src* into project creds *dst*.

    The PURE, gate-free content op behind the host->project credential refresh
    (the descriptor / credsync engine owns mtime/existence gating; this hook is
    a content transform only).  Behaviour:

    * *dst* absent  -> wholesale ``shutil.copy2(src, dst)``.
    * *dst* present -> read host JSON, splice ``claudeAiOauth`` into the project
      JSON (preserving the project's other keys), write via temp+rename.

    Defensive: a malformed host file warns and returns without raising; a
    missing ``claudeAiOauth`` key warns and leaves *dst* untouched.  This is
    exactly ``refresh_host_to_project`` minus its internal mtime/existence gate.

    Returns ``True`` when *dst* was written, ``False`` when the op was skipped
    (unreadable host file / missing oauth key) — the legacy wrapper threads this
    through as its return value; the ``transform_cred`` path ignores it.
    """
    # If project creds don't exist, just copy host wholesale.
    if not dst.is_file():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(src), str(dst))
        return True

    try:
        host_data = json.loads(src.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        print(f"Warning: Cannot read host credentials: {exc}", file=sys.stderr)
        return False

    # Guard for missing key (known issue #2)
    oauth = host_data.get("claudeAiOauth")
    if oauth is None:
        print("Warning: Host credentials missing 'claudeAiOauth' key; skipping merge.", file=sys.stderr)
        return False

    try:
        project_data = json.loads(dst.read_text())
    except (json.JSONDecodeError, OSError):
        project_data = {}

    project_data["claudeAiOauth"] = oauth

    tmp = dst.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(project_data, indent=2) + "\n")
    tmp.replace(dst)
    return True


def refresh_host_to_project(host_creds: Path, project_creds: Path) -> bool:
    """Merge claudeAiOauth from host credentials into project credentials.

    Only acts when the host file is newer than the project file.
    Returns True if the project file was updated.

    Legacy path (still live this phase): it does the mtime/existence GATE and
    then delegates the content op to :func:`merge_oauth_in`.  Its return value
    is preserved exactly (False when the gate blocks OR the merge is skipped).
    """
    if not host_creds.is_file():
        return False

    # If project creds don't exist, copy host wholesale (gate-free part lives
    # in merge_oauth_in, which handles the absent-dst case identically).
    if not project_creds.is_file():
        return merge_oauth_in(host_creds, project_creds)

    # mtime gate (legacy behavior preserved).
    if os.stat(host_creds).st_mtime <= os.stat(project_creds).st_mtime:
        return False

    return merge_oauth_in(host_creds, project_creds)


def writeback_project_to_host(project_creds: Path) -> None:
    """Write back refreshed credentials from project → host (if newer)."""
    if not project_creds.is_file():
        return
    host_creds = Path.home() / ".claude" / ".credentials.json"
    cp_if_newer(project_creds, host_creds)


def filter_settings(src: Path, dst: Path) -> None:
    """Copy host .claude.json with only safe keys (replaces jq filter)."""
    try:
        data = json.loads(src.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        print(f"Warning: Cannot read {src}: {exc}", file=sys.stderr)
        return
    filtered = {
        "oauthAccount": data.get("oauthAccount"),
        "hasCompletedOnboarding": True,
        "installMethod": data.get("installMethod"),
    }
    # Remove None values
    filtered = {k: v for k, v in filtered.items() if v is not None}
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(filtered, indent=2) + "\n")
