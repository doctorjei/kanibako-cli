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
from typing import NamedTuple

import yaml

from kanibako.errors import ConfigError
from kanibako.settings.config_io import load_doc
from kanibako.settings.settings_keyspace import ACCESS_TIERS


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
# Ph4b Vector A: in-box claude settings.json permission-mode delivery.
#
# kanibako's per-agent ``access`` TIER reaches the CLI claude via its argv flag
# row, but NOT the VS Code claude-code EXTENSION panel (the default
# `kanibako code` UX), which reads the box's in-box ``~/.claude/settings.json``.
# This file is PER-BOX (the box home mount), so it is the correctly-scoped place
# to reflect the box's configured tier.
#
# ⚑ R-41 made this projection THREE-VALUED.  The managed
# ``permissions.defaultMode`` is DRIVEN by the box's CASCADE-resolved claude
# ``access`` (never the ephemeral ``-S``/``-A`` — a projection outlives the
# launch, spec §1A):
#     full       -> "bypassPermissions"
#     editing    -> "acceptEdits"
#     restricted -> CLEARED (claude's own default already prompts)
# Every direction merges, never clobbers: the clear removes ONLY a value we
# MANAGE — and that is now the SET of managed values, so switching
# editing → restricted removes the ``acceptEdits`` WE wrote instead of leaving a
# stale middle tier behind.  A user-chosen mode we never write (``plan`` /
# ``default`` / ``dontAsk`` / ``auto``), sibling ``permissions.allow``/``deny``,
# and every other top-level key stay intact.
# ---------------------------------------------------------------------------

# The ``permissions.defaultMode`` value kanibako owns PER TIER.  A tier ABSENT
# from this table renders as "clear the managed value" (restricted); an UNKNOWN
# tier is a hard error (:func:`_claude_managed_mode`) — never a silent clear and
# never a silent bypass.
_CLAUDE_MODE_BY_TIER: "dict[str, str]" = {
    "editing": "acceptEdits",
    "full": "bypassPermissions",
}

#: Every value kanibako may have written here — what the CLEAR path is allowed to
#: remove.  (Was a single ``_MANAGED_MODE`` before the middle tier existed.)
_MANAGED_MODES: frozenset[str] = frozenset(_CLAUDE_MODE_BY_TIER.values())


def _claude_managed_mode(access: str) -> "str | None":
    """The managed ``defaultMode`` for *access*, or ``None`` to CLEAR it.

    RAISES on an unknown tier: this surface must never fall through to the
    permissive arm, and "clear" would be an equally wrong guess for a tier we do
    not understand.
    """
    if access == "restricted":
        return None
    mode = _CLAUDE_MODE_BY_TIER.get(access)
    if mode is None:
        raise ConfigError(
            f"claude panel permissions: unknown access tier {access!r} "
            f"(expected restricted | editing | full)."
        )
    return mode


def merge_permission_mode(settings: dict, mode: str) -> dict:
    """UNION-MERGE ``permissions.defaultMode = <mode>`` into a claude
    ``settings.json`` dict, returning a NEW dict (input never mutated).

    * ``permissions`` — created if absent; if present, its OTHER sub-keys
      (``allow``/``deny``/…) are preserved.  ``defaultMode`` is SET to *mode*
      (the tier's delivery target for the panel).
    * every OTHER top-level key (``$schema``, ``includeCoAuthoredBy``, …) is
      preserved untouched.
    """
    merged = copy.deepcopy(settings)
    current = merged.get("permissions")
    perms = dict(current) if isinstance(current, dict) else {}
    perms["defaultMode"] = mode
    merged["permissions"] = perms
    return merged


def clear_permission_mode(settings: dict) -> dict:
    """Return a NEW dict with kanibako's MANAGED ``permissions.defaultMode`` removed.

    The ``restricted``-tier direction of the delivery.  Pure; input never mutated:

    * ``permissions.defaultMode`` is removed ONLY when it is one of the values we
      manage (:data:`_MANAGED_MODES`).  A user-chosen mode we never write
      (``plan``/``default``/``dontAsk``/``auto``) is left intact, and an absent
      ``permissions``/``defaultMode`` is a no-op.
    * sibling ``permissions`` sub-keys (``allow``/``deny``/…) and every other
      top-level key are preserved.
    * if removing ``defaultMode`` empties the ``permissions`` object, the now-stray
      ``permissions`` key is dropped (we are the only one who created it); a
      ``permissions`` block that still has other keys is KEPT.
    """
    merged = copy.deepcopy(settings)
    current = merged.get("permissions")
    if not isinstance(current, dict) or current.get("defaultMode") not in _MANAGED_MODES:
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


def seed_claude_permission_mode(settings_path: Path, *, access: str) -> bool:
    """Read-modify-write the box's in-box claude ``~/.claude/settings.json``
    (host path *settings_path*) so its ``permissions.defaultMode`` reflects the
    box's resolved ``access`` TIER.

    The WHOLE of ``ClaudeTarget.deliver_panel_permissions`` — the tier→mode
    knowledge lives HERE, beside its codex/goose siblings, not in the plugin's
    branch (R-41: one place decides what a tier means for this surface).

    * ``full`` / ``editing`` — read tolerantly (absent/corrupt → ``{}``), merge
      via :func:`merge_permission_mode`, write pretty JSON (creating parent dirs)
      iff it changed.
    * ``restricted`` — clear the managed mode via :func:`clear_permission_mode`;
      NO-OP when the file is ABSENT (nothing to clear — never creates it).
    * an UNKNOWN tier RAISES (:func:`_claude_managed_mode`) — never a silent
      clear, never a silent bypass.

    Idempotent; returns ``True`` iff it wrote the file.
    """
    mode = _claude_managed_mode(access)
    if mode is None:
        if not settings_path.exists():
            return False
        existing = _read_existing_config(settings_path)
        return _write_if_changed(
            settings_path, existing, clear_permission_mode(existing),
        )
    existing = _read_existing_config(settings_path)
    return _write_if_changed(
        settings_path, existing, merge_permission_mode(existing, mode),
    )


# ---------------------------------------------------------------------------
# Increment 2b: the instruction-delivery SessionStart hook (claude + codex).
#
# The kickoff SEED (~/.config/kanibako/kickoff.md) is a single ``@import`` chain;
# the flattener (RO-delivered by the kani_pkg bind at
# /opt/kanibako/kanibako/scripts/import-directives.py — machinery, so it ships in
# the package, NOT in the canon; ⚑ the SAME literal is carried by
# ``start._directive_flatten_shim`` and the two must move TOGETHER)
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
# never aborts the session.  ``$KANIBAKO_DIRECTIVE_SEED`` expands at hook-run time
# in-box (the flattener path is now an ABSOLUTE package-bind path, so ``$HOME`` no
# longer appears here).  Schemas verified against code.claude.com/docs/en/hooks.md
# and learn.chatgpt.com/docs/hooks (codex): both accept an OR-pattern matcher
# (``startup|resume|clear|compact``).
# ---------------------------------------------------------------------------

_SESSION_START_MATCHER = "startup|resume|clear|compact"
_SESSION_START_COMMAND = (
    'python3 "/opt/kanibako/kanibako/scripts/import-directives.py" '
    '--additional-context "$KANIBAKO_DIRECTIVE_SEED" || true'
)


def _merge_managed_command_hook(
    settings: dict, *, event: str, matcher: str | None, command: str,
) -> dict:
    """UNION-MERGE ONE kanibako-managed ``type:command`` hook into ``hooks.<event>``.

    The shared idempotent-append primitive behind every claude JSON hook kanibako
    seeds (instruction-delivery + the per-PID marker write/remove).  Returns a NEW
    dict (input never mutated):

    * ``hooks`` — created if absent; if present, its OTHER event keys are preserved.
    * ``hooks.<event>`` — a list of matcher-groups; every EXISTING group is
      preserved.  A managed group carrying *command* is APPENDED iff no existing
      group already carries a hook with our EXACT *command* — so a re-run never
      duplicates it (idempotent, keyed on the command string, NOT the matcher).
      When *matcher* is ``None`` the appended group omits the ``matcher`` key.
    * every OTHER top-level key is preserved untouched.

    Keying on the command string keeps each managed command its OWN independent
    group: the marker-WRITE hook and the instruction-delivery hook coexist under
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
# E2g / increment 4a — claude agent LIVENESS MARKER (per-PID) write side.
#
# The box_supervisor READS a box-local markers DIRECTORY to enumerate which agent
# sessions are live (panel-watch's dead-panel detection AND increment-4a newcomer
# detection); this is the WRITE side, delivered per agent:
#
#   * claude — a ``SessionStart`` hook writes the per-PID marker FILE
#     ``<dir>/$PPID`` and a ``SessionEnd`` hook removes it on a clean exit, so a
#     clean shutdown clears its own marker.  Both are MANAGED as their own
#     groups, idempotent + preserving of user hooks, exactly like the directive
#     hook.
#   * codex (D2) — the SAME marker-WRITE command, delivered as the second
#     managed ``[[hooks.SessionStart]]`` group in the config.toml region
#     (:func:`_build_codex_managed_region`).  ⚑ codex has NO SessionEnd hook
#     event, so there is NO remove side: a cleanly-exited codex leaves a stale
#     marker that the supervisor's ``kill -0`` scan classifies dead (see the
#     region builder's docstring for why that is the intended semantics).
#   * goose — no marker delivery yet (no panel-liveness surface).
#
# ⚑ PER-PID (increment 4a), not a single ``agent.pid`` file: the FILENAME is the PID
# and the CONTENT is ``$PPID`` too (redundant — readers key on the FILENAME).  The old
# single-path scheme was last-writer-wins, so a CLI incumbent and a VS Code panel
# newcomer sharing one path could not both be held; a DIR of per-PID files enumerates
# the FULL set of live agent PIDs, which the supervisor prunes-dead via ``kill -0`` to
# detect a newcomer (a live marker PID that is not its own agent).
#
# SINGLE SOURCE OF TRUTH for the dir: :data:`AGENT_MARKERS_DIR` is defined HERE (the
# low-level module) and imported by ``commands/start.py`` for BOTH the supervisor's
# ``--agent-markers-dir`` (read end) and the ``KANIBAKO_AGENT_MARKERS_DIR`` env it
# seeds (write end), so the two ends of the contract can never desync.  The hook
# command prefers the seeded env (``${KANIBAKO_AGENT_MARKERS_DIR:-...}``) and falls
# back to the SAME literal built from the constant — so it also works where a
# ``podman exec`` panel agent does not inherit the podman-set env.  It MUST be a
# LITERAL box-local path (byte-identical on both ends): podman sets the env verbatim
# and the supervisor reads ``--agent-markers-dir`` verbatim, so a shell expression
# like ``${XDG_RUNTIME_DIR:-/tmp}`` would only resolve in a shell context and
# otherwise become a literal ``${...}`` path — the ends would then disagree.  ``/tmp``
# is a box-local tmpfs; the markers are tiny, so it is a safe universal home (the dir
# is created by the write hook; the reader treats an absent dir as "no agents yet").
#
# ⚑ VALIDATION-PENDING (do NOT claim these hold — check at the bifrost e2e):
#   1. ``$PPID`` inside a claude SessionStart ``command`` hook == the claude agent
#      PID.  A ``type:command`` hook is spawned by claude so ``$PPID`` is PLAUSIBLY
#      claude, but this is UNDOCUMENTED/UNVERIFIED; if wrong, swap the write command
#      for a ``/proc`` scan at the e2e.
#   2. The VS Code panel claude executes the box's seeded ``~/.claude/settings.json``
#      hooks at all.  LIKELY but only checkable on a real claude-in-podman box.
# ---------------------------------------------------------------------------

# The box-local per-agent liveness MARKER directory — the SINGLE source of truth for
# both ends of the detection contract (supervisor ``--agent-markers-dir`` read + the
# ``KANIBAKO_AGENT_MARKERS_DIR`` env write); ``commands/start.py`` imports THIS.  Each
# live agent session writes ONE marker FILE named for its PID (``<dir>/$PPID``) and
# removes it on a clean SessionEnd, so the dir enumerates the set of live agent PIDs.
AGENT_MARKERS_DIR = "/tmp/kanibako/agents"

# claude SessionEnd sources (per the E2g spike): a broad OR so the marker is cleaned
# up on ANY clean session end.
_SESSION_END_MATCHER = "clear|logout|prompt_input_exit|other"

# The per-PID marker WRITE (SessionStart) + REMOVE (SessionEnd) commands.  Silent-safe
# (``|| true``): a failure NEVER aborts the session.  The default-dir portion is built
# FROM :data:`AGENT_MARKERS_DIR` (not a second literal) so it stays in sync with the
# supervisor's ``--agent-markers-dir``.  The marker FILENAME is ``$PPID`` (the reader
# keys on it); the CONTENT is ``$PPID`` too, purely for debuggability.  See the
# VALIDATION-PENDING note above re: ``$PPID`` being the agent PID.
_AGENT_MARKER_WRITE_COMMAND = (
    f'd="${{KANIBAKO_AGENT_MARKERS_DIR:-{AGENT_MARKERS_DIR}}}"; '
    'mkdir -p "$d" && printf %s "$PPID" > "$d/$PPID" || true'
)
_AGENT_MARKER_REMOVE_COMMAND = (
    f'd="${{KANIBAKO_AGENT_MARKERS_DIR:-{AGENT_MARKERS_DIR}}}"; '
    'rm -f "$d/$PPID" || true'
)


def merge_marker_write_hook(settings: dict) -> dict:
    """UNION-MERGE the per-PID marker-WRITE ``SessionStart`` hook (its own managed
    group, keyed on the exact command) into a claude JSON hooks dict.

    A SEPARATE managed group from :func:`merge_session_start_hook` — the two
    ``SessionStart`` commands coexist and each is independently idempotent.  Returns
    a NEW dict (input never mutated); preserves all user/other-event hooks.
    """
    return _merge_managed_command_hook(
        settings,
        event="SessionStart",
        matcher=_SESSION_START_MATCHER,
        command=_AGENT_MARKER_WRITE_COMMAND,
    )


def merge_marker_remove_hook(settings: dict) -> dict:
    """UNION-MERGE the per-PID marker-REMOVE ``SessionEnd`` hook into a claude JSON
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
        command=_AGENT_MARKER_REMOVE_COMMAND,
    )


def seed_session_start_hook(settings_path: Path) -> bool:
    """Read-modify-write UNION-MERGE the box's full claude MANAGED hook set into the
    in-box ``~/.claude/settings.json`` (host path *settings_path*).

    Seeds all three managed claude hooks in ONE read-modify-write, idempotently and
    preserving every user/other-event hook:

    * the instruction-delivery ``SessionStart`` hook (:func:`merge_session_start_hook`);
    * the per-PID marker-WRITE ``SessionStart`` hook (:func:`merge_marker_write_hook`);
    * the per-PID marker-REMOVE ``SessionEnd`` hook (:func:`merge_marker_remove_hook`).

    The CLAUDE surface (JSON hooks).  codex does NOT use this — its hook lives in
    ``~/.codex/config.toml`` via :func:`seed_codex_config`.  Reads the existing file
    tolerantly (absent/corrupt → ``{}``) and writes back as pretty JSON (creating
    parent dirs) iff it changed (idempotent).  Returns ``True`` iff it wrote the
    file.  Callers wrap this best-effort so a failure never blocks the launch.
    """
    existing = _read_existing_config(settings_path)
    merged = merge_session_start_hook(existing)
    merged = merge_marker_write_hook(merged)
    merged = merge_marker_remove_hook(merged)
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
# The managed codex config.toml has exactly TWO writers, split along the T1
# Target seams so no managed key is ever written by both:
#   * :func:`seed_codex_config` (``CodexTarget.deliver_directive_hook``) — the
#     hook group + trust hash + directory trust (+ the persona model-provider
#     region), i.e. everything region-shaped;
#   * :func:`seed_codex_approval` (``CodexTarget.deliver_panel_permissions``) —
#     ONLY the top-level approval_policy/sandbox_mode permission-parity keys.
# Both share the surgical primitives below (regions + root-key line surgery +
# :func:`_assemble_codex_managed`), so their composed output is byte-stable.
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

# kanibako's two managed root keys split along their DIFFERENT disciplines:
#
#   * ``approval_policy`` — TIER-DRIVEN by the box's CASCADE-resolved codex
#     ``access`` (R-41), and SET at EVERY tier: ``full`` → ``"never"``;
#     ``editing`` → ``"on-request"``; ``restricted`` → ``"untrusted"``.
#     ⚑ ``restricted`` used to CLEAR the line instead, which was a promise
#     kanibako could not keep.  Clearing leaves codex running at ITS OWN default
#     approval policy — a value NOT documented anywhere in 0.141.0's local help,
#     so unknown to us — while this same function forces ``sandbox_mode`` to
#     ``danger-full-access``.  "Restricted" would then have meant "unknown
#     approval behaviour with full disk access", and it may well have been
#     BYTE-IDENTICAL to ``editing``'s delivered state on a file that already
#     carried ``on-request``.  Setting ``untrusted`` — the most guarded member of
#     the verified enum (``untrusted | on-failure | on-request | never``; its
#     help text: "Only run 'trusted' commands (e.g. ls, cat, sed) without asking
#     for user approval") — makes the tier mean something we control.  Same
#     defensive reasoning as goose, which writes ``approve`` explicitly rather
#     than relying on an unset default.
#     CONSEQUENCE, deliberate: kanibako now OWNS this key the way it owns
#     ``sandbox_mode``.  A user-chosen ``approval_policy`` in the box's in-box
#     config.toml is OVERWRITTEN at every tier, not preserved — the permission
#     tier the box was configured with is the one the panel gets.
#   * ``sandbox_mode`` — a BOX INVARIANT, NOT yolo-gated.  A kanibako box is a
#     hardened rootless podman container = the security boundary, so the codex
#     ``openai.chatgpt`` panel's OWN in-box ``codex app-server`` (which reads
#     this key and, unlike the kanibako-launched CLI codex, gets NO
#     ``--dangerously-bypass-approvals-and-sandbox`` flag) must run with
#     sandboxing OFF.  ``workspace-write`` makes the app-server attempt a NESTED
#     bubblewrap sandbox, which needs nested user namespaces podman blocks, so it
#     stalls on sandbox setup ("could not find bubblewrap").  kanibako therefore
#     OWNS this key and FORCES it to ``"danger-full-access"`` — ALWAYS present,
#     INDEPENDENT of ``access``, NEVER removed by the restricted path, and any
#     prior value (the old managed ``"workspace-write"``, or a user-chosen one
#     that would re-break the panel) is MIGRATED to it.  Parity with the CLI's
#     unconditional bypass: the container is the sandbox.
#     ⚑ This is ALSO why the PANEL's middle tier is carried by the APPROVAL axis
#     and not by ``sandbox_mode``: the CLI's ``editing`` row uses
#     ``-s workspace-write``, but writing that same value HERE is the exact
#     configuration that hangs the panel's app-server.  Two surfaces, one tier,
#     different realizations — each verified against the surface it is written to.
_CODEX_APPROVAL_POLICY_KEY = "approval_policy"
_CODEX_SANDBOX_MODE_KEY = "sandbox_mode"
_CODEX_SANDBOX_MODE = "danger-full-access"

# The ``approval_policy`` value kanibako owns PER TIER (R-41).  Values are exact
# members of the codex approval enum, verified verbatim from `codex --help`
# (codex-cli 0.141.0):
#   untrusted  — "Only run \"trusted\" commands (e.g. ls, cat, sed) without asking
#                 for user approval. Will escalate to the user if the model
#                 proposes a command that is not in the \"trusted\" set"
#                                                          → the `restricted` tier
#   on-request — "The model decides when to ask the user for approval"
#                                                             → the `editing` tier
#   never      — "Never ask for user approval Execution failures are immediately
#                 returned to the model"                      → the `full` tier
# EVERY tier has an entry — the table is TOTAL over ACCESS_TIERS, which is what
# makes "kanibako owns this key" true rather than aspirational.  (``on-failure``
# is the enum's fourth member and is deliberately unused: its own help text marks
# it DEPRECATED.)
# ⚑ FLAG FOR THE LIVE MATRIX: ``never``/``full`` is the pre-existing, shipped
# pairing; ``on-request``↔``editing`` and ``untrusted``↔``restricted`` are this
# pass's judgments from the verified enum text — the VOCABULARY is verified, the
# tiers' *semantics* on the panel are not separately verified and must be
# confirmed by the bifrost matrix.
_CODEX_APPROVAL_BY_TIER: "dict[str, str]" = {
    "restricted": "untrusted",
    "editing": "on-request",
    "full": "never",
}

#: Every ``approval_policy`` value kanibako may have written.  Passed as the
#: CLEAR-path allowlist for symmetry with the ``sandbox_mode`` call; neither key
#: is ever cleared today (both are SET at every tier), so like that call's
#: ``managed`` argument it is currently unconsulted — it stays as the single
#: record of the values kanibako owns.
_CODEX_MANAGED_APPROVALS: frozenset[str] = frozenset(
    _CODEX_APPROVAL_BY_TIER.values()
)

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


def _extract_delimited_region(
    text: str, begin: str, end: str,
) -> tuple[str, str | None]:
    """Split a comment-delimited managed region (*begin*..*end*, inclusive) OUT
    of *text*, returning ``(body_without_region, region_text | None)``.

    The shared primitive behind every kanibako-managed codex config.toml region
    (the instruction-delivery hook region AND the model-provider region).
    Idempotence + re-run safety: a region is regenerated each write, so it is
    split off first so the surviving body is pure user content.  A malformed
    region missing its END marker extends to end-of-file.  No *begin* marker →
    ``(text, None)``.

    The EXTRACT (vs. strip) form exists for :func:`seed_codex_approval`, which
    must edit the ROOT-section approval keys while carrying the managed
    region(s) through VERBATIM: root-key surgery inserts before the first
    ``[table]`` line, and in a region-bearing file with no user tables that
    first table is the managed region's own — a naive in-place insert would land
    INSIDE the region and be swallowed by the next regeneration.
    """
    lines = text.split("\n")
    begins = [i for i, ln in enumerate(lines) if ln.strip() == begin]
    if not begins:
        return text, None
    b = begins[0]
    ends = [i for i, ln in enumerate(lines) if ln.strip() == end and i >= b]
    e = ends[0] if ends else len(lines) - 1
    region = "\n".join(lines[b : e + 1])
    del lines[b : e + 1]
    return "\n".join(lines), region


def _strip_delimited_region(text: str, begin: str, end: str) -> str:
    """Remove a comment-delimited managed region (see
    :func:`_extract_delimited_region` — this is its body-only form)."""
    return _extract_delimited_region(text, begin, end)[0]


def _assemble_codex_managed(body: str, regions: list[str]) -> str:
    """Assemble a managed codex config.toml: clean user *body* + regenerated /
    carried-through managed *regions*, in order.

    The SINGLE source of truth for the separator/trailing-newline bytes between
    the body and the managed regions — used by :func:`merge_codex_config`
    (regenerated regions) AND :func:`seed_codex_approval` (regions carried
    verbatim), so the two writers can never drift on assembly bytes.  *body*
    must already be rstripped of trailing newlines.
    """
    if not regions:
        return body + "\n" if body else ""
    region = "\n\n".join(regions)
    if body:
        return body + "\n\n" + region + "\n"
    return region + "\n"


def _strip_codex_region(text: str) -> str:
    """Remove the kanibako-managed instruction-delivery region (inclusive).

    Thin wrapper over :func:`_strip_delimited_region` for the hook+trust region's
    BEGIN..END markers.
    """
    return _strip_delimited_region(text, _CODEX_REGION_BEGIN, _CODEX_REGION_END)


def _codex_managed_approval(access: str) -> str:
    """The managed ``approval_policy`` for *access* — one per tier, always a value.

    RAISES on an unknown tier — this surface must never fall through to the
    permissive arm, and "clear it" would be an equally wrong guess (clearing is
    what the ``restricted`` tier used to do, and it left the panel at codex's
    own undocumented default; see ``_CODEX_APPROVAL_BY_TIER``).
    """
    policy = _CODEX_APPROVAL_BY_TIER.get(access)
    if policy is None:
        raise ConfigError(
            f"codex panel approval: unknown access tier {access!r} "
            f"(expected restricted | editing | full)."
        )
    return policy


def _reconcile_codex_approval(text: str, access: str) -> str:
    """Reconcile the managed TOP-LEVEL ``approval_policy``/``sandbox_mode`` keys
    (see the constants above):

    * ``approval_policy`` (TIER-DRIVEN, R-41) — ALWAYS SET, one value per tier:
      ``full`` → ``"never"``, ``editing`` → ``"on-request"``, ``restricted`` →
      ``"untrusted"``.  In place if a root-section line exists, else inserted
      before the first table header.  ⚑ ``restricted`` no longer CLEARS: clearing
      handed the tier to codex's own undocumented default while ``sandbox_mode``
      was being forced open, which is not a "restricted" anyone can rely on.
    * ``sandbox_mode`` (BOX INVARIANT) — ALWAYS SET ``"danger-full-access"``,
      independent of *access*: forced in place if a line already exists (so a
      prior ``"workspace-write"``/user value is MIGRATED) else inserted, and
      NEVER removed.  kanibako owns this key (a nested sandbox re-breaks the
      panel app-server).

    Both keys are therefore kanibako-OWNED and are overwritten, not merged: a
    user value in either is replaced by the box's configured tier.

    Only the ROOT section (before the first ``[table]`` header) is considered,
    so a same-named key inside a user table (e.g. a ``[profiles.x]`` override) is
    never touched.
    """
    lines = text.split("\n")
    # sandbox_mode FIRST (it is unconditional — a box invariant, not a tier
    # toggle — so it is forced present/migrated at EVERY tier).  Emitting the
    # always-present invariant before the tier-gated approval_policy makes the
    # freshly-inserted order CANONICAL and toggle-STABLE: a full→restricted→full
    # round-trip re-inserts approval_policy AFTER the surviving sandbox_mode
    # line, reproducing the original bytes exactly.
    lines = _apply_root_key(
        lines, _CODEX_SANDBOX_MODE_KEY,
        desired=_CODEX_SANDBOX_MODE, managed=(_CODEX_SANDBOX_MODE,),
    )
    lines = _apply_root_key(
        lines, _CODEX_APPROVAL_POLICY_KEY,
        desired=_codex_managed_approval(access),
        managed=tuple(sorted(_CODEX_MANAGED_APPROVALS)),
    )
    return "\n".join(lines)


def _apply_root_key(
    lines: list[str], key: str, *,
    desired: "str | None",
    managed: "tuple[str, ...]",
    unconditional: bool = False,
) -> list[str]:
    """Apply the SET-or-CLEAR discipline for one managed root key.  NEW list.

    *desired* is the value this launch wants (SET it: in place if a root-section
    line exists, else inserted before the first table header) or ``None`` to
    CLEAR the line.  The CLEAR removes it only when the current value is one of
    *managed* — the values kanibako itself may have written — so a user-chosen
    value survives (symmetric with :func:`clear_permission_mode`), while a stale
    value from a DIFFERENT tier of ours (``on-request`` left over from
    ``editing``) is correctly removed.  With *unconditional* the line is removed
    REGARDLESS of value: that is for keys kanibako OWNS OUTRIGHT as a derived
    projection rather than a hand-edit surface (D1: the codex
    ``model``/``model_provider`` wipe when a box goes bare — *managed* is then
    irrelevant and unused).
    """
    result = list(lines)
    table = _first_table_index(result)
    key_re = re.compile(r"\s*" + re.escape(key) + r"\s*=")
    found = next((i for i in range(table) if key_re.match(result[i])), None)
    if desired is not None:
        new_line = f"{key} = {_toml_basic_string(desired)}"
        if found is not None:
            result[found] = new_line
        else:
            result.insert(table, new_line)
        return result
    # CLEAR: remove the root-section line.  Unconditional (kanibako-owned key) →
    # drop regardless of value; otherwise drop ONLY a value we manage, leaving a
    # user-chosen one intact.
    if found is not None:
        if unconditional:
            del result[found]
            return result
        val_re = re.compile(r"\s*" + re.escape(key) + r'\s*=\s*"([^"]*)"')
        m = val_re.match(result[found])
        if m is not None and m.group(1) in managed:
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

    Holds TWO managed ``[[hooks.SessionStart]]`` groups, each with its own
    pre-computed ``[hooks.state]`` trusted hash, plus the
    ``[projects."<codex_cwd>"] trust_level = "trusted"`` directory trust:

    * group *group_index* (state key ``…:session_start:<group_index>:0``) — the
      instruction-delivery hook (the flattener in ``--additional-context``
      mode), exactly as claude's;
    * group *group_index*+1 (state key ``…:session_start:<group_index+1>:0``) —
      the per-PID liveness MARKER write (codex D2), reusing claude's
      :data:`_AGENT_MARKER_WRITE_COMMAND` VERBATIM (one SoT for the command
      bytes on both agents).

    TWO SINGLE-HANDLER groups, deliberately NOT one two-handler group: the
    trust hash (:func:`codex_trusted_hash`) is oracle-pinned for the
    ``{event_name, matcher, hooks:[ONE command]}`` identity; a multi-handler
    group's per-handler hash shape is unverified upstream.  ⚑ codex has NO
    SessionEnd/exit hook event (``HookEventName`` verified identical at
    ``rust-v0.141.0`` and 0.144.x: SessionStart/Stop/… — ``Stop`` is per-TURN,
    not process exit), so there is NO marker-REMOVE hook here: a clean codex
    exit leaves a stale marker whose PID the box_supervisor's ``kill -0`` scan
    classifies dead.  That is the intended semantics — the panel's sessions are
    threads inside one long-lived ``codex app-server`` process, so marker-PID
    death ≈ the panel process itself is gone.

    ``box_config_path`` is the BOX-absolute config path
    (``/home/agent/.codex/config.toml``), NOT the host write path — codex keys
    trust by the path it reads in-box.  *group_index* is the count of USER
    ``[[hooks.SessionStart]]`` groups (the managed groups are appended after
    them, so codex indexes them ``group_index`` and ``group_index + 1``).
    """
    directive_key = f"{box_config_path}:{_CODEX_EVENT_KEY}:{group_index}:0"
    directive_hash = codex_trusted_hash(
        _CODEX_EVENT_KEY, _SESSION_START_MATCHER, _SESSION_START_COMMAND,
    )
    marker_key = f"{box_config_path}:{_CODEX_EVENT_KEY}:{group_index + 1}:0"
    marker_hash = codex_trusted_hash(
        _CODEX_EVENT_KEY, _SESSION_START_MATCHER, _AGENT_MARKER_WRITE_COMMAND,
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
            "[[hooks.SessionStart]]",
            f"matcher = {_toml_basic_string(_SESSION_START_MATCHER)}",
            "",
            "[[hooks.SessionStart.hooks]]",
            'type = "command"',
            f"command = {_toml_basic_string(_AGENT_MARKER_WRITE_COMMAND)}",
            "",
            f"[hooks.state.{_toml_basic_string(directive_key)}]",
            f"trusted_hash = {_toml_basic_string(directive_hash)}",
            "",
            f"[hooks.state.{_toml_basic_string(marker_key)}]",
            f"trusted_hash = {_toml_basic_string(marker_hash)}",
            "",
            f"[projects.{_toml_basic_string(codex_cwd)}]",
            'trust_level = "trusted"',
            _CODEX_REGION_END,
        ]
    )


def merge_codex_config(
    text: str,
    *,
    box_config_path: str,
    codex_cwd: str,
    model_provider: CodexModelProvider | None = None,
) -> str:
    """Return *text* with kanibako's managed codex config MERGED in (pure).

    Strips any prior managed region, then regenerates it at the file's end.
    All other user content — comments and data alike — is preserved
    byte-for-byte.  Idempotent: re-merging its own output reproduces it exactly.

    Hook/trust/provider ONLY: the managed ``approval_policy``/``sandbox_mode``
    parity keys are NEVER touched here — they belong solely to
    :func:`seed_codex_approval` (``Target.deliver_panel_permissions``), so no
    managed key has two writers (T1 seam split).

    The model-provider selection is a RECONCILED PROJECTION kanibako OWNS (D1),
    reconciled SYMMETRICALLY every merge — the managed ``[model_providers.<id>]``
    region and the top-level ``model``/``model_provider`` root keys are stripped
    FIRST regardless of *model_provider*:

    * *model_provider* supplied (persona active) → REPLACE: the provider region
      is regenerated and the two root keys are SET (see
      :func:`merge_codex_model_provider`);
    * *model_provider* ``None`` (bare / non-persona) → WIPE: the region stays
      stripped and the two root keys are REMOVED UNCONDITIONALLY — kanibako owns
      these outright, so a stale persona/custom selection never lingers on a box
      that has gone bare.

    The wipe is SCOPED to the critical set: a user's INDEPENDENT (non-managed)
    ``[model_providers.<other>]`` table and every non-critical key/comment are
    preserved byte-for-byte (only the two named root keys + the delimited managed
    region are touched).  Idempotent in BOTH directions (re-merging own output
    reproduces it: a second WIPE finds nothing left to strip/remove).
    """
    # rstrip the region separator immediately so re-merges do not accumulate
    # blank lines (idempotence); reconcile + region append operate on the clean
    # user body.
    body = _strip_codex_region(text).rstrip("\n")
    # Strip our OWN prior provider region UNCONDITIONALLY (both directions), so a
    # stale managed region is reconciled — regenerated on REPLACE, gone on WIPE.
    body = _strip_codex_provider_region(body).rstrip("\n")
    if model_provider is not None:
        # REPLACE: provider root keys are applied to the CLEAN body (no managed
        # regions yet), so they land in the legal top-level position (before any
        # table) and OUTSIDE both regenerated regions — never swallowed by a
        # re-strip.
        body = _apply_provider_root_keys(
            body, model=model_provider.model, provider_id=model_provider.provider_id,
        )
    else:
        # WIPE: remove the kanibako-owned model/model_provider root keys
        # unconditionally (scoped to those two keys — a user's independent
        # provider table and all other content survive).
        body = _remove_provider_root_keys(body)
    group_index = _count_session_start_groups(body)
    regions = [
        _build_codex_managed_region(
            box_config_path=box_config_path,
            codex_cwd=codex_cwd,
            group_index=group_index,
        )
    ]
    if model_provider is not None:
        regions.append(
            _build_codex_provider_region(
                provider_id=model_provider.provider_id,
                name=model_provider.name,
                base_url=model_provider.base_url,
                wire_api=model_provider.wire_api,
                env_key=model_provider.env_key,
            )
        )
    return _assemble_codex_managed(body, regions)


# ---------------------------------------------------------------------------
# Codex personas INC 1: the ~/.codex/config.toml MODEL-PROVIDER generator.
#
# A codex persona (e.g. ``navigator℘codex``) reaches an external OpenAI-compatible
# model provider by SELECTING it in config.toml — a top-level ``model`` +
# ``model_provider`` pair plus a ``[model_providers.<id>]`` table carrying the
# provider's name/base_url/wire_api/env_key.  This is the pure generator for that
# region; INC 2 resolves the six field values from the persona keyspace and INC 3
# invokes it on codex-persona launch (via :func:`merge_codex_config`'s
# ``model_provider`` seam / :func:`seed_codex_config`).  NOTHING here is wired into
# launch yet.
#
# It MATCHES the surgical, comment-delimited mechanism of the instruction-delivery
# region (NO tomlkit round-trip — see the module note above): the provider TABLE
# lives in its own clearly-delimited managed REGION regenerated at the file's end,
# and the two TOP-LEVEL scalar keys (``model``/``model_provider``) are reconciled
# by the SAME root-section line surgery as approval_policy/sandbox_mode
# (:func:`_apply_root_key`) — a bare key after a ``[table]`` header would bind to
# that table, so managed root keys are edited where top-level keys legally belong,
# before the first table header.
# ---------------------------------------------------------------------------

# Delimiters bounding the regenerated model-provider region.  DISTINCT from the
# hook region's markers so the two regions strip/regenerate independently and can
# coexist in one file.  Everything OUTSIDE this region is preserved verbatim.
_CODEX_PROVIDER_REGION_BEGIN = (
    "# >>> kanibako-managed (model provider) — do not edit >>>"
)
_CODEX_PROVIDER_REGION_END = "# <<< kanibako-managed (model provider) <<<"


class CodexModelProvider(NamedTuple):
    """The six resolved values selecting a codex external model provider.

    An immutable bundle threaded (optionally) through :func:`merge_codex_config` /
    :func:`seed_codex_config`.  INC 2 constructs this from the persona keyspace
    (``agent.<persona>℘codex.{endpoint,model,secret_path.<VAR>,...}``); the fields
    map 1:1 onto the emitted TOML:

    * *provider_id* → the ``[model_providers.<id>]`` table id AND top-level
      ``model_provider`` value;
    * *name*/*base_url*/*wire_api*/*env_key* → the table's four keys;
    * *model* → the top-level ``model`` value.
    """

    provider_id: str
    name: str
    base_url: str
    wire_api: str
    env_key: str
    model: str


def _strip_codex_provider_region(text: str) -> str:
    """Remove the kanibako-managed model-provider region (inclusive).

    Thin wrapper over :func:`_strip_delimited_region` for the provider region's
    BEGIN..END markers.  Independent of :func:`_strip_codex_region` (distinct
    markers), so stripping one never touches the other.
    """
    return _strip_delimited_region(
        text, _CODEX_PROVIDER_REGION_BEGIN, _CODEX_PROVIDER_REGION_END,
    )


def _apply_provider_root_keys(body: str, *, model: str, provider_id: str) -> str:
    """SET the managed top-level ``model``/``model_provider`` root keys on *body*.

    Reuses :func:`_apply_root_key`'s SET-direction (in place if a
    root-section line already exists else inserted before the first table header),
    so the keys land in the legal top-level position and updating a value touches
    ONLY that line — never a user key.  *body* must be CLEAN user content (no
    managed region), so the "first table" is a real user table, not our own.
    """
    lines = body.split("\n")
    lines = _apply_root_key(lines, "model", desired=model, managed=(model,))
    lines = _apply_root_key(
        lines, "model_provider", desired=provider_id, managed=(provider_id,),
    )
    return "\n".join(lines)


def _remove_provider_root_keys(body: str) -> str:
    """REMOVE the kanibako-owned top-level ``model``/``model_provider`` root keys
    (the OFF/WIPE direction of :func:`_apply_provider_root_keys`).

    The bare / non-persona projection (D1): kanibako owns these two keys
    outright, so they are dropped UNCONDITIONALLY (regardless of value — a stale
    persona selection is not a hand-edit to preserve).  SCOPED to the root
    section (before the first table header) and to exactly these two keys, so a
    same-named key inside a user table (e.g. ``[profiles.x].model``) and a user's
    independent ``[model_providers.<other>]`` table are never touched.  *body*
    must be CLEAN user content (managed regions already stripped).
    """
    lines = body.split("\n")
    lines = _apply_root_key(
        lines, "model", desired=None, managed=(), unconditional=True,
    )
    lines = _apply_root_key(
        lines, "model_provider", desired=None, managed=(), unconditional=True,
    )
    return "\n".join(lines)


def _build_codex_provider_region(
    *, provider_id: str, name: str, base_url: str, wire_api: str, env_key: str
) -> str:
    """Build the regenerated model-provider TOML region.

    Holds the ``[model_providers.<provider_id>]`` table with the provider's
    ``name``/``base_url``/``wire_api``/``env_key``.  The table id is a QUOTED key
    (``[model_providers."navigator"]`` — equivalent to the unquoted form but safe
    for ids with special characters), matching how the hook region quotes
    ``[projects."<cwd>"]``.  All four values are TOML-basic-string encoded.
    """
    return "\n".join(
        [
            _CODEX_PROVIDER_REGION_BEGIN,
            f"[model_providers.{_toml_basic_string(provider_id)}]",
            f"name = {_toml_basic_string(name)}",
            f"base_url = {_toml_basic_string(base_url)}",
            f"wire_api = {_toml_basic_string(wire_api)}",
            f"env_key = {_toml_basic_string(env_key)}",
            _CODEX_PROVIDER_REGION_END,
        ]
    )


def merge_codex_model_provider(
    text: str,
    *,
    provider_id: str,
    name: str,
    base_url: str,
    wire_api: str,
    env_key: str,
    model: str,
) -> str:
    """Return *text* with the codex model-provider selection MERGED in (pure).

    The standalone generator for the INC-1 region: strips any prior provider
    region, SETs the top-level ``model``/``model_provider`` root keys, then
    regenerates the ``[model_providers.<provider_id>]`` table region at the file's
    end.  All other user content — comments, other keys, other tables (including
    an unrelated ``[model_providers.<other>]``) — is preserved byte-for-byte.

    Pure/deterministic (string in, string out, no I/O) and idempotent: re-merging
    its own output reproduces it exactly.  Updating a single field (e.g. *model*
    or *base_url*) changes ONLY that emitted value, never a user key.  Top-level
    keys are emitted before the table, so the result is valid TOML.
    """
    body = _strip_codex_provider_region(text).rstrip("\n")
    body = _apply_provider_root_keys(body, model=model, provider_id=provider_id)
    region = _build_codex_provider_region(
        provider_id=provider_id,
        name=name,
        base_url=base_url,
        wire_api=wire_api,
        env_key=env_key,
    )
    if body:
        return body + "\n\n" + region + "\n"
    return region + "\n"


def seed_codex_config(
    config_path: Path,
    *,
    box_config_path: str,
    codex_cwd: str,
    model_provider: CodexModelProvider | None = None,
) -> bool:
    """Read-modify-write the box's in-box ``~/.codex/config.toml`` (host path
    *config_path*): merge kanibako's managed hook group, trust hash, and
    directory trust.

    The codex DIRECTIVE-HOOK write (``CodexTarget.deliver_directive_hook``).
    Never the approval/sandbox parity keys — those are written solely by
    :func:`seed_codex_approval` (the panel-permissions seam), so no managed key
    has two writers.

    Reads tolerantly (absent → empty; the file is TEXT, so a "corrupt" TOML file
    is handled at the text level and never crashes), merges via
    :func:`merge_codex_config`, and writes iff it changed (idempotent).
    ``box_config_path`` is the BOX-absolute config path used for the trust key;
    ``codex_cwd`` is the in-box directory codex runs in (the trusted project).
    Returns ``True`` iff it wrote.  Callers wrap best-effort so a failure never
    blocks the launch.

    *model_provider* is the reconciled projection seam (D1): when supplied the
    merged config carries the model-provider region + root keys (REPLACE); when
    ``None`` (bare / non-persona) the managed region and the kanibako-owned
    ``model``/``model_provider`` root keys are WIPED back to stock (see
    :func:`merge_codex_config`).  Either way the write fires iff the bytes
    changed, so a box already in the correct state is a no-op.
    """
    try:
        existing = config_path.read_text()
    except OSError:
        existing = ""
    merged = merge_codex_config(
        existing,
        box_config_path=box_config_path,
        codex_cwd=codex_cwd,
        model_provider=model_provider,
    )
    if config_path.exists() and merged == existing:
        return False
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(merged)
    return True


def seed_codex_approval(config_path: Path, *, access: str) -> bool:
    """Read-modify-write ONLY the managed codex approval/sandbox parity keys in
    the box's in-box ``~/.codex/config.toml`` (host path *config_path*).

    The codex PANEL-permissions deliverer (``Target.deliver_panel_permissions``):
    the ``openai.chatgpt`` panel spawns its OWN in-box codex without kanibako's
    launch flags, and codex approval/sandbox has NO VS Code settings key — the
    box's CASCADE-resolved ``access`` TIER reaches the panel only via the managed
    ``approval_policy``/``sandbox_mode`` root keys here.  The SOLE writer of
    those keys; the directive-hook write (:func:`seed_codex_config`) never
    touches them, so no key ever has two writers (T1 seam split).

    Discipline (see :func:`_reconcile_codex_approval`): the TIER-driven
    ``approval_policy`` is SET at EVERY tier (``never`` / ``on-request`` /
    ``untrusted``), while ``sandbox_mode`` is a BOX INVARIANT forced to
    ``"danger-full-access"`` ALWAYS (independent of *access*, migrating any
    prior value, never removed) — so even a ``restricted`` launch (or one over a
    file with no ``sandbox_mode`` line) writes to establish/repair both.  An
    UNKNOWN tier RAISES.  The managed region(s) —
    instruction-delivery hook + model provider — are carried through VERBATIM
    (extracted before the root-key surgery, reattached by the shared
    :func:`_assemble_codex_managed`, so a root-key insert can never land inside
    a region and assembly bytes can never drift from the merge's).

    Short-circuits to ``False`` (NO write, no normalization of user bytes) when
    the reconciled state already matches (approval_policy correct AND
    ``sandbox_mode`` already ``"danger-full-access"``).  Returns ``True`` iff it
    wrote.  Callers wrap best-effort so a failure never blocks the launch.
    """
    try:
        existing = config_path.read_text()
    except OSError:
        existing = ""
    body, hook_region = _extract_delimited_region(
        existing, _CODEX_REGION_BEGIN, _CODEX_REGION_END,
    )
    body, provider_region = _extract_delimited_region(
        body, _CODEX_PROVIDER_REGION_BEGIN, _CODEX_PROVIDER_REGION_END,
    )
    clean = body.rstrip("\n")
    reconciled = _reconcile_codex_approval(clean, access)
    if reconciled == clean:
        # Approval state already correct — NEVER rewrite (no gratuitous
        # normalization of user bytes; OFF on an absent file never creates it).
        return False
    regions = [r for r in (hook_region, provider_region) if r is not None]
    merged = _assemble_codex_managed(reconciled, regions)
    if config_path.exists() and merged == existing:
        return False
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(merged)
    return True


# ---------------------------------------------------------------------------
# FF-5 permission parity (goose surface): in-box goose config.yaml GOOSE_MODE.
#
# kanibako's per-agent ``access`` TIER reaches the CLI goose via the
# ``GOOSE_MODE`` env var it sets on its own launch entrypoint, but NOT the VS
# Code goose EXTENSION panel (``block.vscode-goose``), which spawns its OWN
# in-container goose WITHOUT kanibako's launch env — so the panel goose never
# sees ``GOOSE_MODE``.  ``GOOSE_MODE`` is also a valid top-level goose
# ``config.yaml`` key, so persisting it into the box's in-box
# ``~/.config/goose/config.yaml`` (PER-BOX, the box home mount) gives the panel
# the box's configured tier.
#
# ASYMMETRIC vs claude (which CLEARS at ``restricted``): goose's UNSET
# ``GOOSE_MODE`` default is ``auto`` (permissive), so a ``restricted`` box MUST
# persist ``approve`` EXPLICITLY — clearing the key would silently restore
# permissive.  Same reason there is no "emit nothing" tier here at all.
# (codex USED to clear too; it now writes ``approval_policy = "untrusted"`` at
# ``restricted`` for this same reason — its own default is not documented, so it
# is not something to hand the tier to.  claude is the only CLEAR left, and it
# is sound there: claude's unset ``permissions.defaultMode`` default PROMPTS.)
#
# ⚑ ``editing`` is REFUSED, not approximated (R-41 / the B7b goose ruling): no
# GOOSE_MODE value realizes it (``smart_approve`` auto-approves READ-ONLY tools
# and PROMPTS for writes — the inverse of acceptEdits), and this surface is
# PERSISTED, so a silent substitution would outlive the launch.  The launch gate
# refuses the tier before delivery; this refusal is the second fence.
# ---------------------------------------------------------------------------

# The exact top-level ``GOOSE_MODE`` value kanibako owns PER TIER: ``full`` →
# ``auto`` (approvals off), ``restricted`` → ``approve`` (approvals on).
# ``editing`` is deliberately ABSENT — goose cannot render it (see above).
_GOOSE_MODE_BY_TIER: "dict[str, str]" = {
    "restricted": "approve",
    "full": "auto",
}


def seed_goose_mode(config_path: Path, *, access: str) -> bool:
    """Read-modify-write the box's in-box goose ``config.yaml`` (host path
    *config_path*): SET the top-level ``GOOSE_MODE`` to this box's ``access``
    tier parity value.

    The goose PANEL-permissions emitter behind
    ``GooseTarget.deliver_panel_permissions``: ``full`` → ``auto`` (approvals
    off), ``restricted`` → the EXPLICIT ``approve`` (an unset ``GOOSE_MODE``
    defaults to permissive ``auto``, so restricted must persist, not clear — see
    the FF-5 block comment above).  Merge-preserving (only the top-level
    ``GOOSE_MODE`` key is set; every other key is preserved; an absent file is
    created with just ``GOOSE_MODE``) and idempotent (no write when the key
    already equals the desired value).  Returns whether a write occurred.

    RAISES for ``editing`` (goose has no realization for it) and for any unknown
    tier.  ⚑ The callers wrap panel delivery best-effort, so this raise alone
    would be SWALLOWED — which is exactly why the launch refuses an unrenderable
    tier BEFORE any delivery runs (``targets.assembly.access_row``).  This check
    exists so the function is honest in isolation, never as the primary gate.
    """
    desired = _GOOSE_MODE_BY_TIER.get(access)
    if desired is None:
        # Ordered least → most permissive, like every other tier listing (a
        # sorted() here would print "full | restricted" and read as a ladder
        # that runs the wrong way).
        legal = " | ".join(
            t for t in ACCESS_TIERS if t in _GOOSE_MODE_BY_TIER
        )
        raise ConfigError(
            f"goose has no GOOSE_MODE realization for access tier {access!r}; "
            f"goose renders {legal}. Refusing rather than persisting a "
            f"DIFFERENT tier onto the box's config.yaml."
        )
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
