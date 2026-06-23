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


def merge_oauth_account_out(project_json: Path, host_json: Path) -> bool:
    """Merge ``oauthAccount`` from the box's ``.claude.json`` into the host's.

    The REVERSE of the (removed) host->project oauthAccount import: after an
    in-box login, the box's ``~/.claude.json`` carries the ``oauthAccount`` block
    (account id/email).  That account info must reach the host so the host claude
    knows who is logged in — but ``.claude.json`` ALSO carries machine-specific
    ``machineID`` / ``userID`` / ``projects`` (and much else) that must NOT
    clobber the host's.  So this splices ONLY ``oauthAccount`` (the gate-free
    content op; the caller owns any gating).

    Behaviour:

    * box file absent / unreadable / no ``oauthAccount`` -> no-op (return False).
    * host file absent -> create ``~/.claude.json`` with just ``{"oauthAccount": ...}``.
    * host file present -> read it, splice ``oauthAccount`` in (preserving every
      other host key), write via temp+rename.

    Defensive throughout: never raises on a malformed file.

    Returns ``True`` when the host file was written, ``False`` otherwise.
    """
    if not project_json.is_file():
        return False
    try:
        box_data = json.loads(project_json.read_text())
    except (json.JSONDecodeError, OSError):
        return False
    if not isinstance(box_data, dict):
        return False
    oauth = box_data.get("oauthAccount")
    if oauth is None:
        return False

    if host_json.is_file():
        try:
            host_data = json.loads(host_json.read_text())
            if not isinstance(host_data, dict):
                host_data = {}
        except (json.JSONDecodeError, OSError):
            # Don't clobber an unreadable host file wholesale; bail.
            return False
    else:
        host_data = {}

    host_data["oauthAccount"] = oauth

    try:
        host_json.parent.mkdir(parents=True, exist_ok=True)
        tmp = host_json.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(host_data, indent=2) + "\n")
        tmp.replace(host_json)
    except OSError:
        return False
    return True
