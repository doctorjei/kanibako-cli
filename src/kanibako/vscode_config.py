"""Host-side attached-container config generation for ``kanibako code``.

VS Code's Dev Containers extension reads an "attached container configuration"
(a devcontainer.json subset) when you *attach to a running container*.
``kanibako code`` seeds this config so that, on attach, VS Code opens the box's
workspace folder and auto-installs the box agent's VS Code extension (e.g.
``anthropic.claude-code``, so claude's ``/ide`` integration works in-box).

Everything here is PURE + host-side: it only computes paths and merges/writes a
JSON file next to VS Code's global storage.  It changes NO box/launch behavior —
the ``code`` launcher works identically whether or not seeding succeeds
(Phase-1's zero-launch-delta discipline).

CONFIRMED (VS Code Phase-0 test) — the config is IMAGE-keyed, not name-level:

    <config_home>/Code/User/globalStorage/
        ms-vscode-remote.remote-containers/imageConfigs/<ENC>.json

where ``<ENC>`` is the image reference percent-encoded (``/`` → ``%2f``,
``:`` → ``%3a``) with LOWERCASE hex escapes — VS Code uses an
``encodeURIComponent``-style encoder then lowercases, so e.g.
``ghcr.io/doctorjei/kanibako-oci:latest`` → file
``ghcr.io%2fdoctorjei%2fkanibako-oci%3alatest.json`` (see :func:`_encode_image_ref`;
Python's :func:`urllib.parse.quote` emits UPPERCASE escapes, so we lowercase them).

Because the file is IMAGE-shared (one file per image, used by every box on that
image) and VS Code OWNS it — it reads AND accumulates ``extensions`` as the user
installs more — the seed is a read-modify-write UNION-MERGE, never a clobbering
create-if-absent: we add this box's agent extension iff absent, set
``workspaceFolder`` only if absent, and preserve every other key/extension the
file already holds.  The schema VS Code itself writes is exactly ``extensions``
(a JSON array) and ``workspaceFolder`` — it does NOT write ``remoteUser`` (it
infers the container user), so we omit it to match VS Code's schema exactly.
Our ``kanibako code`` launcher passes an explicit ``--folder-uri`` anyway, so
``workspaceFolder`` is only a fallback default.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import urllib.parse
from pathlib import Path

import yaml

from kanibako.config_io import load_doc


def _strip_jsonc(text: str) -> str:
    """Best-effort strip of JSONC (comments + trailing commas) to plain JSON.

    VS Code ``settings.json`` is JSONC: it permits ``//`` and ``/* */`` comments
    and trailing commas, none of which :func:`json.loads` accepts.  This is a
    light, best-effort pass (not a full JSONC parser): block comments, then
    WHOLE-LINE ``//`` comments only (so ``"http://..."`` inside a string value
    is left intact), then trailing commas before ``}`` / ``]``.

    LIMITATION (deliberate, for string-safety): a TRAILING inline ``//`` comment
    (e.g. ``"...": "podman" // note``) is NOT stripped -- reliably telling a
    real comment from a ``//`` inside a string value would require a real
    tokenizer, and a wrong guess would corrupt string values.  Such a
    hand-edited file therefore fails to parse and degrades to ``None`` in
    :func:`load_jsonc` rather than risking a false read.
    """
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    text = re.sub(r"(?m)^\s*//.*$", "", text)
    text = re.sub(r",(\s*[}\]])", r"\1", text)
    return text


def load_jsonc(text: str) -> object | None:
    """Parse JSONC text, returning the object or ``None`` if unparseable.

    Tries strict :func:`json.loads` first (the common case — VS Code writes
    valid JSON when edited via the settings UI), falling back to a
    comment/trailing-comma strip for hand-edited JSONC.  See
    :func:`_strip_jsonc` for what the fallback does NOT handle (trailing inline
    ``//`` comments), which degrade to ``None`` here.
    """
    try:
        return json.loads(text)
    except ValueError:
        pass
    try:
        return json.loads(_strip_jsonc(text))
    except ValueError:
        return None


# The VS Code Dev Containers global-storage sub-path (relative to the user
# config home, e.g. ``~/.config`` on Linux) under which per-IMAGE
# attached-container configs live.  CONFIRMED by the Phase-0 VS Code test.
_IMAGE_CONFIGS_SUBPATH = (
    "Code/User/globalStorage/ms-vscode-remote.remote-containers/imageConfigs"
)


def _encode_image_ref(ref: str) -> str:
    """Percent-encode *ref* the way VS Code keys ``imageConfigs`` files.

    VS Code encodes an image reference ``encodeURIComponent``-style and
    lowercases, so ``/`` → ``%2f`` and ``:`` → ``%3a``.  :func:`urllib.parse.quote`
    with ``safe=""`` encodes the same code points but emits UPPERCASE escapes
    (``%2F``/``%3A``), so we lowercase the whole result.

    CONFIRMED byte-exact for the canonical lowercase ref
    ``ghcr.io/doctorjei/kanibako-oci:latest`` →
    ``ghcr.io%2fdoctorjei%2fkanibako-oci%3alatest``.  OCI image NAMES are already
    lowercase per spec, so only a tag's case can differ; for an uppercase-tag ref
    we ASSUME VS Code does ``encodeURIComponent(x).toLowerCase()`` (whole-string
    lowercase) — PENDING a Phase-0 uppercase-tag confirm.  Whole-string lowercase
    is the simpler/more-common encoder idiom and is identical to escape-only
    lowercasing for any all-lowercase ref, so it's the safer default.
    """
    return urllib.parse.quote(ref, safe="").lower()


def attached_container_config_path(image_ref: str, config_home: Path) -> Path:
    """Return the host path VS Code reads the IMAGE-keyed attached config from.

    *config_home* is the user config home (``xdg("XDG_CONFIG_HOME", ".config")``);
    *image_ref* is the box's image reference (e.g.
    ``ghcr.io/doctorjei/kanibako-oci:latest``).  The file is
    ``<config_home>/Code/User/globalStorage/
    ms-vscode-remote.remote-containers/imageConfigs/<ENC>.json`` where ``<ENC>``
    is :func:`_encode_image_ref` of the reference.
    """
    return config_home / _IMAGE_CONFIGS_SUBPATH / f"{_encode_image_ref(image_ref)}.json"


def merge_attached_container_config(
    existing: dict,
    *,
    workspace_folder: str,
    extension: str | None,
) -> dict:
    """UNION-MERGE the box's config into *existing*, returning a NEW dict.

    Pure + deterministic; *existing* is never mutated (deep-copied first):

    * ``extensions`` — when *extension* is not ``None``, add it to the array iff
      not already present (set-union: dedup, preserve existing order, NEVER
      remove).  When *extension* is ``None`` the extensions are left untouched
      (no key created if absent).
    * ``workspaceFolder`` — set to *workspace_folder* only if the key is ABSENT;
      an existing value (VS Code's or the user's) is never clobbered.
    * every OTHER key and every OTHER extension already present is preserved.

    ``remoteUser`` is intentionally NOT added — VS Code omits it and infers the
    container user; we match its schema exactly.
    """
    merged = copy.deepcopy(existing)

    if extension is not None:
        current = merged.get("extensions")
        exts = list(current) if isinstance(current, list) else []
        if extension not in exts:
            exts.append(extension)
        merged["extensions"] = exts

    if "workspaceFolder" not in merged:
        merged["workspaceFolder"] = workspace_folder

    return merged


def _read_existing_config(path: Path) -> dict:
    """Read *path* as a JSON object, tolerating absence AND corruption.

    Returns the parsed dict, or ``{}`` if the file is absent, unreadable, not
    valid JSON, or not a JSON object.  NEVER raises — VS Code owns this file and
    may have written anything into it.
    """
    try:
        raw = path.read_text()
    except OSError:
        return {}
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def seed_attached_container_config(
    path: Path,
    *,
    workspace_folder: str,
    extension: str | None,
) -> bool:
    """Read-modify-write UNION-MERGE of the box's config into *path*.

    Reads the existing ``imageConfigs/<ENC>.json`` tolerantly (absent/corrupt →
    ``{}``), computes the :func:`merge_attached_container_config` result, and
    writes it back as pretty JSON (``indent=2``), creating parent dirs.  If the
    merge produces no change versus what is on disk, the write is skipped
    (idempotent).

    Returns ``True`` iff it wrote the file, ``False`` if nothing changed.  NEVER
    removes existing extensions or clobbers an existing ``workspaceFolder``.
    """
    existing = _read_existing_config(path)
    merged = merge_attached_container_config(
        existing, workspace_folder=workspace_folder, extension=extension,
    )
    if path.exists() and merged == existing:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    # Non-atomic write is acceptable here: VS Code re-reads this file on each
    # attach, and the seed is idempotent, so a torn write self-heals next run.
    path.write_text(json.dumps(merged, indent=2) + "\n")
    return True


# ---------------------------------------------------------------------------
# Ph4b Vector A: in-box claude settings.json bypassPermissions delivery.
#
# kanibako's per-agent ``auto_approve`` reaches the CLI claude via the
# ``--dangerously-skip-permissions`` flag, but NOT the VS Code claude-code
# EXTENSION panel (the default `kanibako code` UX), which reads the box's
# in-box ``~/.claude/settings.json``.  This file is PER-BOX (the box home mount),
# so it is the correctly-scoped place to reflect the box's configured yolo.
#
# SYMMETRIC: the managed ``permissions.defaultMode`` is DRIVEN by the box's
# resolved claude ``auto_approve`` — SET to ``"bypassPermissions"`` when yolo is
# ON, and CLEARED when yolo is OFF (so toggling off actually takes effect in the
# panel).  Both directions merge, never clobber: the clear removes ONLY the exact
# value we manage, leaving a user-chosen mode (``plan``/``default``/
# ``acceptEdits``), sibling ``permissions.allow``/``deny``, and every other
# top-level key intact.
# ---------------------------------------------------------------------------

# The exact ``permissions.defaultMode`` value kanibako owns for the panel-yolo
# delivery.  We SET it on and CLEAR it (only when unchanged from this) off — a
# user's own mode is never touched.
_MANAGED_MODE = "bypassPermissions"


def merge_bypass_permissions(settings: dict) -> dict:
    """UNION-MERGE ``permissions.defaultMode = "bypassPermissions"`` into a claude
    ``settings.json`` dict, returning a NEW dict (input never mutated).

    * ``permissions`` — created if absent; if present, its OTHER sub-keys
      (``allow``/``deny``/…) are preserved.  ``defaultMode`` is SET to
      ``"bypassPermissions"`` (the yolo delivery target for the panel).
    * every OTHER top-level key (``$schema``, ``includeCoAuthoredBy``, …) is
      preserved untouched.
    """
    merged = copy.deepcopy(settings)
    current = merged.get("permissions")
    perms = dict(current) if isinstance(current, dict) else {}
    perms["defaultMode"] = _MANAGED_MODE
    merged["permissions"] = perms
    return merged


def clear_bypass_permissions(settings: dict) -> dict:
    """Return a NEW dict with kanibako's MANAGED ``permissions.defaultMode`` removed.

    The OFF-direction of the symmetric delivery.  Pure; input never mutated:

    * ``permissions.defaultMode`` is removed ONLY when it equals the value we
      manage (``"bypassPermissions"``).  A user-chosen mode (``plan``/``default``/
      ``acceptEdits``) is left intact, and an absent ``permissions``/``defaultMode``
      is a no-op.
    * sibling ``permissions`` sub-keys (``allow``/``deny``/…) and every other
      top-level key are preserved.
    * if removing ``defaultMode`` empties the ``permissions`` object, the now-stray
      ``permissions`` key is dropped (we are the only one who created it); a
      ``permissions`` block that still has other keys is KEPT.
    """
    merged = copy.deepcopy(settings)
    current = merged.get("permissions")
    if not isinstance(current, dict) or current.get("defaultMode") != _MANAGED_MODE:
        return merged
    perms = dict(current)
    del perms["defaultMode"]
    if perms:
        merged["permissions"] = perms
    else:
        del merged["permissions"]
    return merged


def _write_if_changed(path: Path, existing: dict, merged: dict) -> bool:
    """Write *merged* to *path* as pretty JSON iff it differs from disk.

    Returns ``True`` iff a write occurred (idempotent), ``False`` when the merge
    is a no-op versus what is already on disk.
    """
    if path.exists() and merged == existing:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(merged, indent=2) + "\n")
    return True


def seed_claude_bypass_permissions(settings_path: Path) -> bool:
    """Read-modify-write: SET ``permissions.defaultMode=bypassPermissions`` into the
    box's in-box claude ``~/.claude/settings.json`` (host path *settings_path*).

    Reads the existing file tolerantly (absent/corrupt → ``{}``), merges via
    :func:`merge_bypass_permissions`, and writes it back as pretty JSON (creating
    parent dirs) iff it changed (idempotent).  Returns ``True`` iff it wrote the
    file.  The ON-direction of :func:`deliver_claude_panel_permissions`.
    """
    existing = _read_existing_config(settings_path)
    return _write_if_changed(
        settings_path, existing, merge_bypass_permissions(existing),
    )


def clear_claude_bypass_permissions(settings_path: Path) -> bool:
    """Read-modify-write: CLEAR our managed ``permissions.defaultMode`` from the
    box's in-box claude ``~/.claude/settings.json`` (host path *settings_path*).

    No-ops when the file is ABSENT (nothing to clear — never creates it).  Reads
    tolerantly, applies :func:`clear_bypass_permissions`, and writes back iff it
    changed (idempotent).  Returns ``True`` iff it wrote the file.  The
    OFF-direction of :func:`deliver_claude_panel_permissions`.
    """
    if not settings_path.exists():
        return False
    existing = _read_existing_config(settings_path)
    return _write_if_changed(
        settings_path, existing, clear_bypass_permissions(existing),
    )


# ---------------------------------------------------------------------------
# Increment 2b: the instruction-delivery SessionStart hook (claude + codex).
#
# The kickoff SEED (~/.config/kanibako/kickoff.md) is a single ``@import`` chain;
# the flattener (RO-bound at ~/playbook/kanibako/scripts/import-directives.py)
# resolves it and, in ``--additional-context`` mode, prints a SessionStart hook
# payload whose ``hookSpecificOutput.additionalContext`` is the flattened text.
# A ``SessionStart`` hook runs the flattener and injects that as context — the
# claude+codex delivery (both agents nest a ``SessionStart`` group of
# ``{matcher, hooks:[{type:"command", command}]}``).  Claude reads its memory
# file BEFORE hooks fire, so a file-rewrite would be too late; the hook's
# additionalContext is the delivery instead.
#
# DELIVERY SURFACE differs by agent:
#   * claude → ``~/.claude/settings.json`` (JSON ``hooks.SessionStart``); see
#     :func:`seed_session_start_hook`.
#   * codex  → ``~/.codex/config.toml`` (INLINE ``[hooks]`` in config.toml, NOT a
#     separate ``hooks.json`` — verified against codex-cli 0.141.0).  codex ALSO
#     gates a hook behind a content-hash trust; the codex config manager
#     (:func:`seed_codex_config`) writes the hook group, the pre-computed
#     trusted_hash, and the directory trust together so the FIRST launch fires
#     the hook with no ``/hooks`` prompt.
#
# The command is SILENT-SAFE (``|| true``): a missing SEED or flattener error
# never aborts the session.  ``$HOME``/``$KANIBAKO_DIRECTIVE_SEED`` expand at
# hook-run time in-box.  Schemas verified against code.claude.com/docs/en/hooks.md
# and learn.chatgpt.com/docs/hooks (codex): both accept an OR-pattern matcher
# (``startup|resume|clear|compact``).
# ---------------------------------------------------------------------------

_SESSION_START_MATCHER = "startup|resume|clear|compact"
_SESSION_START_COMMAND = (
    'python3 "$HOME/playbook/kanibako/scripts/import-directives.py" '
    '--additional-context "$KANIBAKO_DIRECTIVE_SEED" || true'
)


def _merge_managed_command_hook(
    settings: dict, *, event: str, matcher: str | None, command: str,
) -> dict:
    """UNION-MERGE ONE kanibako-managed ``type:command`` hook into ``hooks.<event>``.

    The shared idempotent-append primitive behind every claude JSON hook kanibako
    seeds (instruction-delivery + the E2g pidfile write/remove).  Returns a NEW
    dict (input never mutated):

    * ``hooks`` — created if absent; if present, its OTHER event keys are preserved.
    * ``hooks.<event>`` — a list of matcher-groups; every EXISTING group is
      preserved.  A managed group carrying *command* is APPENDED iff no existing
      group already carries a hook with our EXACT *command* — so a re-run never
      duplicates it (idempotent, keyed on the command string, NOT the matcher).
      When *matcher* is ``None`` the appended group omits the ``matcher`` key.
    * every OTHER top-level key is preserved untouched.

    Keying on the command string keeps each managed command its OWN independent
    group: the pidfile-WRITE hook and the instruction-delivery hook coexist under
    ``SessionStart`` without either's idempotency swallowing the other.
    """
    merged = copy.deepcopy(settings)
    current_hooks = merged.get("hooks")
    hooks = dict(current_hooks) if isinstance(current_hooks, dict) else {}
    current = hooks.get(event)
    groups = list(current) if isinstance(current, list) else []
    already = any(
        isinstance(g, dict)
        and isinstance(g.get("hooks"), list)
        and any(
            isinstance(h, dict) and h.get("command") == command
            for h in g["hooks"]
        )
        for g in groups
    )
    if not already:
        group: dict = {"hooks": [{"type": "command", "command": command}]}
        if matcher is not None:
            group = {"matcher": matcher, **group}
        groups = groups + [group]
    hooks[event] = groups
    merged["hooks"] = hooks
    return merged


def merge_session_start_hook(settings: dict) -> dict:
    """UNION-MERGE kanibako's instruction-delivery ``SessionStart`` hook into a
    claude JSON hooks dict, returning a NEW dict (input never mutated).

    * ``hooks`` — created if absent; if present, its OTHER event keys
      (``PreToolUse``/…) are preserved.
    * ``hooks.SessionStart`` — a list of matcher-groups; every EXISTING group is
      preserved.  Our managed group (matcher ``startup|resume|clear|compact``
      running the flattener in ``--additional-context`` mode) is APPENDED iff no
      existing group already carries our exact command — so a re-run never
      duplicates it (idempotent).
    * every OTHER top-level key is preserved untouched.
    """
    return _merge_managed_command_hook(
        settings,
        event="SessionStart",
        matcher=_SESSION_START_MATCHER,
        command=_SESSION_START_COMMAND,
    )


# ---------------------------------------------------------------------------
# E2g — claude panel-agent LIVENESS MARKER (pidfile) write side.
#
# The box_supervisor's panel-watch mode (E2f) READS a box-local pidfile to detect
# a dead VS-Code-panel claude agent; this is the WRITE side.  We seed a claude
# ``SessionStart`` hook that writes the session's PID to that pidfile and a
# ``SessionEnd`` hook that removes it on a clean exit — so a live panel agent keeps
# the marker present and a clean shutdown clears it.  Both are MANAGED as their own
# groups, idempotent + preserving of user hooks, exactly like the directive hook.
#
# SINGLE SOURCE OF TRUTH for the path: :data:`AGENT_PIDFILE_PATH` is defined HERE
# (the low-level module) and imported by ``commands/start.py`` for BOTH the
# supervisor's ``--agent-pidfile`` (read end) and the ``KANIBAKO_AGENT_PIDFILE`` env
# it seeds (write end), so the two ends of the contract can never desync.  The hook
# command prefers the seeded env (``${KANIBAKO_AGENT_PIDFILE:-...}``) and falls back
# to the SAME literal built from the constant — so it also works where a
# ``podman exec`` panel agent does not inherit the podman-set env.  It MUST be a
# LITERAL box-local path (byte-identical on both ends): podman sets the env verbatim
# and the supervisor reads ``--agent-pidfile`` verbatim, so a shell expression like
# ``${XDG_RUNTIME_DIR:-/tmp}`` would only resolve in a shell context and otherwise
# become a literal ``${...}`` filename — the ends would then disagree.  ``/tmp`` is a
# box-local tmpfs; a pidfile is tiny, so it is a safe universal home (the dir is
# created by the write hook; the reader treats an absent dir/file as "no panel yet").
#
# ⚑ VALIDATION-PENDING (do NOT claim these hold — check at the bifrost e2e):
#   1. ``$PPID`` inside a claude SessionStart ``command`` hook == the claude agent
#      PID.  A ``type:command`` hook is spawned by claude so ``$PPID`` is PLAUSIBLY
#      claude, but this is UNDOCUMENTED/UNVERIFIED; if wrong, swap the write command
#      for a ``/proc`` scan at the e2e.
#   2. The VS Code panel claude executes the box's seeded ``~/.claude/settings.json``
#      hooks at all.  LIKELY but only checkable on a real claude-in-podman box.
# ---------------------------------------------------------------------------

# The box-local panel-agent liveness MARKER path — the SINGLE source of truth for
# both ends of the E2f/E2g contract (supervisor ``--agent-pidfile`` read + the
# ``KANIBAKO_AGENT_PIDFILE`` env write); ``commands/start.py`` imports THIS.
AGENT_PIDFILE_PATH = "/tmp/kanibako/agent.pid"

# claude SessionEnd sources (per the E2g spike): a broad OR so the marker is cleaned
# up on ANY clean session end.
_SESSION_END_MATCHER = "clear|logout|prompt_input_exit|other"

# The pidfile WRITE (SessionStart) + REMOVE (SessionEnd) commands.  Silent-safe
# (``|| true``): a failure NEVER aborts the session.  The default-path portion is
# built FROM :data:`AGENT_PIDFILE_PATH` (not a second literal) so it stays in sync
# with the supervisor's ``--agent-pidfile``.  See the VALIDATION-PENDING note above
# re: ``$PPID`` being the agent PID.
_AGENT_PIDFILE_WRITE_COMMAND = (
    f'f="${{KANIBAKO_AGENT_PIDFILE:-{AGENT_PIDFILE_PATH}}}"; '
    'mkdir -p "$(dirname "$f")" && printf %s "$PPID" > "$f" || true'
)
_AGENT_PIDFILE_REMOVE_COMMAND = (
    f'rm -f "${{KANIBAKO_AGENT_PIDFILE:-{AGENT_PIDFILE_PATH}}}" || true'
)


def merge_pidfile_write_hook(settings: dict) -> dict:
    """UNION-MERGE the E2g pidfile-WRITE ``SessionStart`` hook (its own managed
    group, keyed on the exact command) into a claude JSON hooks dict.

    A SEPARATE managed group from :func:`merge_session_start_hook` — the two
    ``SessionStart`` commands coexist and each is independently idempotent.  Returns
    a NEW dict (input never mutated); preserves all user/other-event hooks.
    """
    return _merge_managed_command_hook(
        settings,
        event="SessionStart",
        matcher=_SESSION_START_MATCHER,
        command=_AGENT_PIDFILE_WRITE_COMMAND,
    )


def merge_session_end_hook(settings: dict) -> dict:
    """UNION-MERGE the E2g pidfile-REMOVE ``SessionEnd`` hook into a claude JSON
    hooks dict, returning a NEW dict (input never mutated).

    Mirrors :func:`merge_session_start_hook`: a managed ``hooks.SessionEnd`` group
    (broad matcher, cleaning up the marker on any clean end) is APPENDED iff no
    existing group already carries our exact remove command (idempotent).  Preserves
    ``hooks`` siblings, existing ``SessionEnd`` groups, and every other top-level key.
    """
    return _merge_managed_command_hook(
        settings,
        event="SessionEnd",
        matcher=_SESSION_END_MATCHER,
        command=_AGENT_PIDFILE_REMOVE_COMMAND,
    )


def seed_session_start_hook(settings_path: Path) -> bool:
    """Read-modify-write UNION-MERGE the box's full claude MANAGED hook set into the
    in-box ``~/.claude/settings.json`` (host path *settings_path*).

    Seeds all three managed claude hooks in ONE read-modify-write, idempotently and
    preserving every user/other-event hook:

    * the instruction-delivery ``SessionStart`` hook (:func:`merge_session_start_hook`);
    * the E2g pidfile-WRITE ``SessionStart`` hook (:func:`merge_pidfile_write_hook`);
    * the E2g pidfile-REMOVE ``SessionEnd`` hook (:func:`merge_session_end_hook`).

    The CLAUDE surface (JSON hooks).  codex does NOT use this — its hook lives in
    ``~/.codex/config.toml`` via :func:`seed_codex_config`.  Reads the existing file
    tolerantly (absent/corrupt → ``{}``) and writes back as pretty JSON (creating
    parent dirs) iff it changed (idempotent).  Returns ``True`` iff it wrote the
    file.  Callers wrap this best-effort so a failure never blocks the launch.
    """
    existing = _read_existing_config(settings_path)
    merged = merge_session_start_hook(existing)
    merged = merge_pidfile_write_hook(merged)
    merged = merge_session_end_hook(merged)
    return _write_if_changed(settings_path, existing, merged)


# ---------------------------------------------------------------------------
# Increment 2b (codex surface): the ~/.codex/config.toml MANAGER.
#
# codex-cli 0.141.0 fires a ``[hooks.SessionStart]`` hook defined INLINE in
# ``~/.codex/config.toml`` and injects its additionalContext in-session — the
# SAME delivery as claude, a DIFFERENT file/format (TOML, not JSON; NOT a
# separate ``hooks.json`` — that path was openai/codex#17532 speculation and is
# wrong for 0.141.0).  codex additionally gates a config-defined hook behind a
# content-hash trust (``[hooks.state]``) PLUS a directory trust
# (``[projects."<cwd>"] trust_level``); pre-seeding both makes the FIRST launch
# fire the hook with no interactive ``/hooks`` prompt.
#
# This is the SINGLE place kanibako's managed codex config.toml writes happen —
# it reconciles the hook group, the trust hash, the directory trust, AND the
# permission-parity keys (approval_policy/sandbox_mode) together.  A later
# increment (goose / auth-parity) builds on this shape.
#
# NO tomlkit dependency (kanibako ships stdlib-only: argcomplete/PyYAML/packaging).
# tomllib is read-only and cannot round-trip comments, and re-serialising an
# arbitrary user config through a hand-rolled emitter risks corrupting exotic
# TOML (multiline strings, datetimes, floats).  So the manager is SURGICAL, not a
# round-trip: it edits ONLY kanibako-managed lines and leaves every other byte of
# the user's file (all comments + all data) untouched.
#   * the hook group + trust-hash + directory-trust TABLES live in a single
#     clearly-delimited kanibako-managed REGION regenerated at the file's end;
#   * the two TOP-LEVEL scalar keys (approval_policy/sandbox_mode) are reconciled
#     by in-place ROOT-SECTION line surgery (a bare key after a ``[table]`` header
#     would bind to that table, so managed root keys can NOT live in the trailing
#     region — they are edited where top-level keys legally belong: before the
#     first table header).
# ---------------------------------------------------------------------------

# The codex INTERNAL event id for the SessionStart hook (snake_case), as used in
# the trust state-table key ``<cfg>:<event>:<group>:<handler>`` and in the trust
# hash identity's ``event_name``.  The config.toml table key is PascalCase
# (``[hooks.SessionStart]``); codex maps it to this event id internally.
_CODEX_EVENT_KEY = "session_start"

# kanibako's managed permission-parity values, driven by the box's resolved codex
# ``auto_approve`` (yolo).  ON → SET both; OFF → CLEAR each ONLY when it still
# equals the managed value (symmetric with :func:`clear_bypass_permissions`; a
# user-chosen approval_policy/sandbox_mode is never touched).
_CODEX_APPROVAL_ON = {
    "approval_policy": "never",
    "sandbox_mode": "workspace-write",
}

# Delimiters bounding the regenerated kanibako-managed region (hook group + trust
# hash + directory trust).  Everything OUTSIDE this region is preserved verbatim.
_CODEX_REGION_BEGIN = (
    "# >>> kanibako-managed (instruction-delivery hook + trust) — do not edit >>>"
)
_CODEX_REGION_END = "# <<< kanibako-managed (instruction-delivery hook + trust) <<<"


def codex_trusted_hash(
    event_key: str,
    matcher: str | None,
    command: str,
    timeout_sec: int = 600,
) -> str:
    """Return codex's content-trust hash for a single command hook.

    Reproduces codex's ``command_hook_hash`` (codex-rs/hooks discovery +
    config/fingerprint): a canonical, key-sorted, whitespace-free JSON encoding
    of the hook IDENTITY, SHA-256'd and prefixed ``sha256:``.  The identity is::

        {"event_name": <event_key>,
         "matcher": <matcher>,                       # key OMITTED when None
         "hooks": [{"type": "command",
                    "command": <RAW command, pre-${ENV} expansion>,
                    "timeout": <timeout_sec, default 600>,
                    "async": false}]}

    ``command`` is the RAW string BEFORE any ``${ENV}`` expansion (codex hashes
    the config text, not the expanded command), and ``timeout`` normalises to the
    600 s default.  Pinned to a real-oracle vector in the tests.
    """
    identity: dict[str, object] = {
        "event_name": event_key,
        "hooks": [
            {
                "type": "command",
                "command": command,
                "timeout": timeout_sec,
                "async": False,
            }
        ],
    }
    if matcher is not None:
        identity["matcher"] = matcher
    canonical = json.dumps(identity, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()


def _toml_basic_string(value: str) -> str:
    """Encode *value* as a TOML basic (double-quoted) string.

    Also used for quoted KEYS (``[hooks.state."a:b"]``), which share basic-string
    escaping.  Escapes backslash, double-quote and the common control chars — the
    only cases our managed values (commands with ``"`` and ``$``, colon/slash
    paths) can hit.
    """
    out = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    return f'"{out}"'


def _first_table_index(lines: list[str]) -> int:
    """Index of the first TOML table/array-of-tables header line (``[`` / ``[[``),
    i.e. the end of the root section.  Returns ``len(lines)`` when there is none.
    """
    for i, line in enumerate(lines):
        if re.match(r"\s*\[", line):
            return i
    return len(lines)


def _strip_codex_region(text: str) -> str:
    """Remove the kanibako-managed region (BEGIN..END markers, inclusive).

    Idempotence + re-run safety: the region is regenerated each write, so it is
    stripped first so the surviving text is pure user content.  A malformed
    region missing its END marker is stripped to end-of-file.  No markers → text
    unchanged.
    """
    lines = text.split("\n")
    begins = [i for i, ln in enumerate(lines) if ln.strip() == _CODEX_REGION_BEGIN]
    if not begins:
        return text
    b = begins[0]
    ends = [i for i, ln in enumerate(lines) if ln.strip() == _CODEX_REGION_END and i >= b]
    e = ends[0] if ends else len(lines) - 1
    del lines[b : e + 1]
    return "\n".join(lines)


def _reconcile_codex_approval(text: str, auto_approve: bool) -> str:
    """Reconcile the managed TOP-LEVEL ``approval_policy``/``sandbox_mode`` keys.

    ON (yolo) → SET each to its managed value (in place if a root-section line
    already exists, else inserted before the first table header).  OFF → REMOVE
    each root-section line ONLY when it still equals the managed value; a
    user-chosen value is left intact (symmetric with
    :func:`clear_bypass_permissions`).  Only the ROOT section (before the first
    ``[table]`` header) is considered, so a same-named key inside a user table
    (e.g. a ``[profiles.x]`` override) is never touched.
    """
    lines = text.split("\n")
    for key, managed_val in _CODEX_APPROVAL_ON.items():
        lines = _apply_root_key(lines, key, managed_val, auto_approve)
    return "\n".join(lines)


def _apply_root_key(
    lines: list[str], key: str, managed_val: str, auto_approve: bool
) -> list[str]:
    """Apply the ON/OFF discipline for one managed root key.  Returns NEW list."""
    result = list(lines)
    table = _first_table_index(result)
    key_re = re.compile(r"\s*" + re.escape(key) + r"\s*=")
    found = next((i for i in range(table) if key_re.match(result[i])), None)
    if auto_approve:
        new_line = f"{key} = {_toml_basic_string(managed_val)}"
        if found is not None:
            result[found] = new_line
        else:
            result.insert(table, new_line)
        return result
    # OFF: remove ONLY our managed value; leave a user-chosen one.
    if found is not None:
        val_re = re.compile(r"\s*" + re.escape(key) + r'\s*=\s*"([^"]*)"')
        m = val_re.match(result[found])
        if m is not None and m.group(1) == managed_val:
            del result[found]
    return result


def _count_session_start_groups(text: str) -> int:
    """Count user ``[[hooks.SessionStart]]`` array-of-table groups in *text*.

    The kanibako-managed group is APPENDED after all user groups, so its group
    index (for the trust state-table key) equals this count.  Text-based (not
    tomllib) so a corrupt user file still yields a usable index (0 for the empty
    template — the common case, matching the oracle ``:0:0``).
    """
    header = re.compile(r"\s*\[\[\s*hooks\.SessionStart\s*\]\]\s*$")
    return sum(1 for ln in text.split("\n") if header.match(ln))


def _build_codex_managed_region(
    *, box_config_path: str, codex_cwd: str, group_index: int
) -> str:
    """Build the regenerated kanibako-managed TOML region.

    Holds the managed ``[[hooks.SessionStart]]`` group (the flattener in
    ``--additional-context`` mode), the pre-computed ``[hooks.state]`` trusted
    hash keyed on ``<box_config_path>:session_start:<group_index>:0``, and the
    ``[projects."<codex_cwd>"] trust_level = "trusted"`` directory trust.
    ``box_config_path`` is the BOX-absolute config path
    (``/home/agent/.codex/config.toml``), NOT the host write path — codex keys
    trust by the path it reads in-box.
    """
    state_key = f"{box_config_path}:{_CODEX_EVENT_KEY}:{group_index}:0"
    thash = codex_trusted_hash(
        _CODEX_EVENT_KEY, _SESSION_START_MATCHER, _SESSION_START_COMMAND,
    )
    return "\n".join(
        [
            _CODEX_REGION_BEGIN,
            "[[hooks.SessionStart]]",
            f"matcher = {_toml_basic_string(_SESSION_START_MATCHER)}",
            "",
            "[[hooks.SessionStart.hooks]]",
            'type = "command"',
            f"command = {_toml_basic_string(_SESSION_START_COMMAND)}",
            "",
            f"[hooks.state.{_toml_basic_string(state_key)}]",
            f"trusted_hash = {_toml_basic_string(thash)}",
            "",
            f"[projects.{_toml_basic_string(codex_cwd)}]",
            'trust_level = "trusted"',
            _CODEX_REGION_END,
        ]
    )


def merge_codex_config(
    text: str, *, box_config_path: str, codex_cwd: str, auto_approve: bool
) -> str:
    """Return *text* with kanibako's managed codex config MERGED in (pure).

    Strips any prior managed region, reconciles the managed root keys
    (approval_policy/sandbox_mode per *auto_approve*), then regenerates the
    managed region at the file's end.  All other user content — comments and data
    alike — is preserved byte-for-byte.  Idempotent: re-merging its own output
    reproduces it exactly.
    """
    # rstrip the region separator immediately so re-merges do not accumulate
    # blank lines (idempotence); reconcile + region append operate on the clean
    # user body.
    body = _strip_codex_region(text).rstrip("\n")
    body = _reconcile_codex_approval(body, auto_approve)
    group_index = _count_session_start_groups(body)
    region = _build_codex_managed_region(
        box_config_path=box_config_path,
        codex_cwd=codex_cwd,
        group_index=group_index,
    )
    if body:
        return body + "\n\n" + region + "\n"
    return region + "\n"


def seed_codex_config(
    config_path: Path,
    *,
    box_config_path: str,
    codex_cwd: str,
    auto_approve: bool,
) -> bool:
    """Read-modify-write the box's in-box ``~/.codex/config.toml`` (host path
    *config_path*): merge kanibako's managed hook group, trust hash, directory
    trust, and approval/sandbox parity.

    Reads tolerantly (absent → empty; the file is TEXT, so a "corrupt" TOML file
    is handled at the text level and never crashes), merges via
    :func:`merge_codex_config`, and writes iff it changed (idempotent).
    ``box_config_path`` is the BOX-absolute config path used for the trust key;
    ``codex_cwd`` is the in-box directory codex runs in (the trusted project).
    Returns ``True`` iff it wrote.  Callers wrap best-effort so a failure never
    blocks the launch.
    """
    try:
        existing = config_path.read_text()
    except OSError:
        existing = ""
    merged = merge_codex_config(
        existing,
        box_config_path=box_config_path,
        codex_cwd=codex_cwd,
        auto_approve=auto_approve,
    )
    if config_path.exists() and merged == existing:
        return False
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(merged)
    return True


def deliver_directive_session_hook(
    *,
    agent_name: str,
    config_root: Path,
    box_codex_config_path: str,
    codex_cwd: str,
    auto_approve: bool,
) -> bool:
    """Route the instruction-delivery SessionStart hook to the agent's NATIVE
    config surface, returning whether a write occurred.

    * ``claude`` → ``<config_root>/.claude/settings.json`` (the full managed JSON
      hook set: instruction-delivery ``SessionStart`` + the E2g pidfile write/remove
      hooks; all unconditional, orthogonal to *auto_approve* — see
      :func:`seed_session_start_hook`).
    * ``codex``  → ``<config_root>/.codex/config.toml`` (the codex config manager:
      hook + trust + approval/sandbox parity, the SINGLE managed-write site).
    * any other agent → inert (``False``).

    The single dispatch point for the launch-side directive-hook delivery (mirrors
    :func:`deliver_claude_panel_permissions`); callers wrap best-effort so a
    failure never blocks the launch.
    """
    if agent_name == "claude":
        return seed_session_start_hook(config_root / ".claude" / "settings.json")
    if agent_name == "codex":
        return seed_codex_config(
            config_root / ".codex" / "config.toml",
            box_config_path=box_codex_config_path,
            codex_cwd=codex_cwd,
            auto_approve=auto_approve,
        )
    return False


def deliver_claude_panel_permissions(
    *, auto_approve: bool, is_claude: bool, claude_config_dir: Path,
) -> bool:
    """GATE + deliver the SYMMETRIC Vector A yolo state for the VS Code panel.

    The single mutation-provable gate for the launch-side Vector A delivery,
    driven by the box's resolved claude ``auto_approve``:

    * non-claude box → inert, does NOTHING (returns ``False``).
    * claude + ``auto_approve`` ON → SET ``permissions.defaultMode=bypassPermissions``
      in ``<claude_config_dir>/settings.json``.
    * claude + ``auto_approve`` OFF → CLEAR that managed value (no-op if the file
      is absent) so toggling yolo off takes effect in the panel.

    Both directions merge (never clobber a user's own settings) and are
    idempotent.  Returns whether a write occurred.  Callers wrap this best-effort
    so a failure never blocks the launch.
    """
    if not is_claude:
        return False
    settings_path = claude_config_dir / "settings.json"
    if auto_approve:
        return seed_claude_bypass_permissions(settings_path)
    return clear_claude_bypass_permissions(settings_path)


# ---------------------------------------------------------------------------
# FF-5 permission parity (goose surface): in-box goose config.yaml GOOSE_MODE.
#
# kanibako's per-agent ``auto_approve`` reaches the CLI goose via the
# ``GOOSE_MODE`` env var it sets on its own launch entrypoint, but NOT the VS
# Code goose EXTENSION panel (``block.vscode-goose``), which spawns its OWN
# in-container goose WITHOUT kanibako's launch env — so the panel goose never
# sees ``GOOSE_MODE``.  ``GOOSE_MODE`` is also a valid top-level goose
# ``config.yaml`` key, so persisting it into the box's in-box
# ``~/.config/goose/config.yaml`` (PER-BOX, the box home mount) gives the panel
# the box's configured yolo.
#
# ASYMMETRIC vs claude (which CLEARS off): goose's UNSET ``GOOSE_MODE`` default
# is ``auto`` (permissive), so an OFF box MUST persist the secure ``approve``
# value EXPLICITLY — clearing the key would silently restore permissive.
# ---------------------------------------------------------------------------

# The exact top-level ``GOOSE_MODE`` values kanibako owns for the panel-yolo
# delivery: ON → ``auto`` (approvals off), OFF → ``approve`` (approvals on).
_GOOSE_MODE_ON = "auto"
_GOOSE_MODE_OFF = "approve"


def deliver_goose_panel_permissions(
    *, auto_approve: bool, is_goose: bool, goose_config_dir: Path,
) -> bool:
    """GATE + deliver the goose panel-permission (GOOSE_MODE) parity, returning
    whether a write occurred.

    Driven by the box's resolved goose ``auto_approve`` and mirroring
    :func:`deliver_claude_panel_permissions`, EXCEPT it writes the OFF value
    explicitly rather than clearing:

    * non-goose box → inert, does NOTHING (returns ``False``).
    * goose + ``auto_approve`` ON  → SET ``GOOSE_MODE: "auto"``.
    * goose + ``auto_approve`` OFF → SET ``GOOSE_MODE: "approve"`` — an UNSET
      ``GOOSE_MODE`` defaults to ``auto`` (permissive), so OFF must persist the
      secure value explicitly, NOT clear the key.

    Merge-preserving (only the top-level ``GOOSE_MODE`` key is set; every other
    key in ``<goose_config_dir>/config.yaml`` is preserved; an absent file is
    created with just ``GOOSE_MODE``) and idempotent (no write when the key
    already equals the desired value).  Returns whether a write occurred.
    Callers wrap this best-effort so a failure never blocks the launch.
    """
    if not is_goose:
        return False
    config_path = goose_config_dir / "config.yaml"
    desired = _GOOSE_MODE_ON if auto_approve else _GOOSE_MODE_OFF
    existing = load_doc(config_path)
    if existing.get("GOOSE_MODE") == desired:
        return False
    merged = dict(existing)
    merged["GOOSE_MODE"] = desired
    # Write through the config_path's own methods (like the claude/codex sibling
    # deliveries), NOT config_io.dump_doc: dump_doc's atomic_write_text coerces
    # the path via Path()/mkstemp and does a real mkdir, which on a mocked
    # proj.shell_path would materialize a stray on-disk dir — the siblings stay
    # mock-safe by writing via the Path object.  A best-effort re-seed is
    # idempotent, so plain (non-atomic) write parity with the siblings is fine.
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        yaml.safe_dump(
            merged, sort_keys=False, default_flow_style=False, allow_unicode=True,
        )
    )
    return True
