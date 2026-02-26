"""Claude credential copy, JSON merge, and mtime-based freshness."""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

from kanibako.utils import cp_if_newer


def refresh_host_to_project(host_creds: Path, project_creds: Path) -> bool:
    """Merge claudeAiOauth from host credentials into project credentials.

    Only acts when the host file is newer than the project file.
    Returns True if the project file was updated.
    """
    if not host_creds.is_file():
        return False

    # If project creds don't exist, just copy host wholesale
    if not project_creds.is_file():
        project_creds.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(host_creds), str(project_creds))
        return True

    # mtime check
    if os.stat(host_creds).st_mtime <= os.stat(project_creds).st_mtime:
        return False

    try:
        host_data = json.loads(host_creds.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        print(f"Warning: Cannot read host credentials: {exc}", file=sys.stderr)
        return False

    # Guard for missing key (known issue #2)
    oauth = host_data.get("claudeAiOauth")
    if oauth is None:
        print("Warning: Host credentials missing 'claudeAiOauth' key; skipping merge.", file=sys.stderr)
        return False

    try:
        project_data = json.loads(project_creds.read_text())
    except (json.JSONDecodeError, OSError):
        project_data = {}

    project_data["claudeAiOauth"] = oauth

    tmp = project_creds.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(project_data, indent=2) + "\n")
    tmp.replace(project_creds)
    return True


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
