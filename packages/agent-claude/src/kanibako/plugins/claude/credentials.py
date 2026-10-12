"""Claude credential JSON merges and the ``.credentials.json`` format check."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from kanibako import boxfs


def merge_oauth_in(src: Path, dst: Path) -> bool:
    """Merge ``claudeAiOauth`` from host creds *src* into project creds *dst*.

    The PURE, gate-free content op behind the host->project credential refresh
    (the descriptor / credsync engine owns mtime/existence gating; this hook is
    a content transform only).  Behavior:

    * *dst* absent  -> wholesale ``shutil.copy2(src, dst)``.
    * *dst* present -> read host JSON, splice ``claudeAiOauth`` into the project
      JSON (preserving the project's other keys), write via temp+rename.

    Defensive: a malformed host file warns and returns without raising; a
    missing ``claudeAiOauth`` key warns and leaves *dst* untouched.

    Returns ``True`` when *dst* was written, ``False`` when the op was skipped
    (unreadable host file / missing oauth key); the ``transform_cred`` path
    ignores it.
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


def credentials_error(data: bytes) -> str | None:
    """Why *data* is not a ``.credentials.json`` claude can use, or ``None``.

    The test :func:`merge_oauth_in` applies to a host file: JSON whose
    ``claudeAiOauth`` block is present, here required to be an object.
    """
    try:
        doc = json.loads(data)
    except ValueError as exc:
        return f"not JSON: {exc}"
    if not isinstance(doc, dict):
        return "not a JSON object"
    if not isinstance(doc.get("claudeAiOauth"), dict):
        return "no 'claudeAiOauth' object"
    return None


def merge_oauth_account_out(project_json: Path, host_json: Path, *, box_root: Path) -> bool:
    """Merge ``oauthAccount`` from the box's ``.claude.json`` into the host's.

    The REVERSE of the (removed) host->project oauthAccount import: after an
    in-box login, the box's ``~/.claude.json`` carries the ``oauthAccount`` block
    (account id/email).  That account info must reach the host so the host claude
    knows who is logged in — but ``.claude.json`` ALSO carries machine-specific
    ``machineID`` / ``userID`` / ``projects`` (and much else) that must NOT
    clobber the host's.  So this splices ONLY ``oauthAccount`` (the gate-free
    content op; the caller owns any gating).

    The box file is read through :mod:`kanibako.boxfs` under *box_root* (the box
    home): a symlink at it or a parent below *box_root* is not followed, and the
    merge is skipped with a warning naming it.

    Behavior:

    * box file absent / unreadable / no ``oauthAccount`` -> no-op (return False).
    * host file absent -> create ``~/.claude.json`` with just ``{"oauthAccount": ...}``.
    * host file present -> read it, splice ``oauthAccount`` in (preserving every
      other host key), write via temp+rename.

    Defensive throughout: never raises on a malformed file.

    Returns ``True`` when the host file was written, ``False`` otherwise.
    """
    if not boxfs.no_symlink_or_warn(project_json, box_root):
        return False
    try:
        box_data = json.loads(boxfs.read_text(project_json, box_root))
    except (ValueError, OSError):
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
