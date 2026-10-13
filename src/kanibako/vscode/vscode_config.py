"""Host-side writers for the config surfaces a VS Code box needs — one per agent.

Terminology.  *Attached container configuration* — the devcontainer.json subset VS Code's
Dev Containers extension reads when attaching to a running container; it is keyed by IMAGE,
not by box, and VS Code OWNS the file.  *Panel* — the in-box agent EXTENSION VS Code runs;
it spawns its own agent process without kanibako's launch flags, so no access tier reaches
it; it runs at the user's own agent settings.  *Managed region* — a
comment-delimited block of a user's codex ``config.toml`` that kanibako regenerates whole;
every byte outside a managed region (comment or data) is preserved.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import urllib.parse
from pathlib import Path
from typing import NamedTuple

from kanibako import boxfs



def _strip_jsonc(text: str) -> str:
    """Best-effort strip of JSONC (comments + trailing commas) to plain JSON."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    # ⚑ WHOLE-LINE ``//`` only, deliberately: an inline ``//`` may be inside a string value.
    text = re.sub(r"(?m)^\s*//.*$", "", text)
    text = re.sub(r",(\s*[}\]])", r"\1", text)
    return text


def load_jsonc(text: str) -> object | None:
    """Parse JSONC text, returning the object or ``None`` if unparseable."""
    try:
        return json.loads(text)
    except ValueError:
        pass
    try:
        return json.loads(_strip_jsonc(text))
    except ValueError:
        return None


# ⚑ WIRE PATH — per-IMAGE attached configs, under the user config home (``~/.config``
# on Linux). CONFIRMED by the Phase-0 VS Code test; not a choice of ours.
_IMAGE_CONFIGS_SUBPATH = (
    "Code/User/globalStorage/ms-vscode-remote.remote-containers/imageConfigs"
)


def _encode_image_ref(ref: str) -> str:
    """Percent-encode *ref* the way VS Code keys ``imageConfigs`` files."""
    # ⚑ ``quote`` emits UPPERCASE escapes and VS Code's are lowercase — the ``.lower()``
    # is the whole point of this function. ⚑ Whole-string lowercase is an ASSUMPTION for
    # an uppercase-TAG ref (unverified); it is exact for any all-lowercase ref.
    return urllib.parse.quote(ref, safe="").lower()


def attached_container_config_path(image_ref: str, config_home: Path) -> Path:
    """Return the host path VS Code reads the IMAGE-keyed attached config from."""
    return config_home / _IMAGE_CONFIGS_SUBPATH / f"{_encode_image_ref(image_ref)}.json"


def merge_attached_container_config(
    existing: dict,
    *,
    workspace_folder: str,
    extension: str | None,
) -> dict:
    """UNION-MERGE the box's config into *existing*, returning a NEW dict."""
    merged = copy.deepcopy(existing)

    if extension is not None:
        current = merged.get("extensions")
        exts = list(current) if isinstance(current, list) else []
        if extension not in exts:
            exts.append(extension)
        merged["extensions"] = exts

    # ⚑ Only if ABSENT: VS Code OWNS this file, so an existing value is never clobbered.
    if "workspaceFolder" not in merged:
        merged["workspaceFolder"] = workspace_folder

    return merged


def _read_existing_config(path: Path, root: Path | None = None) -> dict:
    """Read *path* as a JSON object → ``{}`` on absence/corruption; NEVER raises.

    *root* marks *path* as box-writable, under the box tree *root*: the read then goes
    through :mod:`kanibako.boxfs`, and a symlink reads as absent.
    """
    try:
        raw = path.read_text() if root is None else boxfs.read_text(path, root)
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
    """Read-modify-write UNION-MERGE into *path*; ``True`` iff it wrote (idempotent)."""
    existing = _read_existing_config(path)
    merged = merge_attached_container_config(
        existing, workspace_folder=workspace_folder, extension=extension,
    )
    if path.exists() and merged == existing:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    # Non-atomic is acceptable: VS Code re-reads on each attach and the seed is idempotent.
    path.write_text(json.dumps(merged, indent=2) + "\n")
    return True


def _make_box_home(root: Path) -> None:
    """Create the box home *root*, and missing parents, as the seeds always have.

    Through :func:`kanibako.boxfs.mkdir` under the nearest existing ancestor, so a
    dangling link at *root* is refused and nothing is created at its target; an
    existing *root*, linked or not, is used as given.
    """
    if root.is_dir():
        return
    base = root.parent
    while not os.path.lexists(base) and base != base.parent:
        base = base.parent
    boxfs.mkdir(root, base, parents=True, exist_ok=True)


def _write_if_changed(path: Path, existing: dict, merged: dict, *, root: Path) -> bool:
    """Write *merged* to the box file *path* as pretty JSON iff it differs from disk.

    Idempotent; atomic through :mod:`kanibako.boxfs` under the box tree *root*,
    which is created when missing.
    """
    if path.exists() and merged == existing:
        return False
    _make_box_home(root)
    boxfs.write_text(path, root, json.dumps(merged, indent=2) + "\n")
    return True


# ---------------------------------------------------------------------------
# Instruction delivery: the SessionStart hook (claude JSON + codex TOML).
# ---------------------------------------------------------------------------

# OR-pattern matcher; accepted by BOTH the claude and codex hook schemas.
_SESSION_START_MATCHER = "startup|resume|clear|compact"
# ⚑ This flattener path is carried a SECOND time by ``start._directive_flatten_shim``;
# the two literals must move TOGETHER. ⚑ Silent-safe (``|| true``) by design.
_SESSION_START_COMMAND = (
    'python3 "/opt/kanibako/kanibako/scripts/import-directives.py" '
    '--additional-context "$KANIBAKO_DIRECTIVE_SEED" || true'
)

# ⚑ A JOB's retired commands — the exact strings this job's command was before the
# current one, so a re-seed REPLACES them instead of stranding them beside the new
# command forever.  ⚑ CHANGING a command above MEANS adding its predecessor here: the
# set is the only thing that lets the next box drop it, and a spelling nobody lists is
# a spelling nobody removes.  ⚑ Removal is EXACT-match — never a substring (that would
# take a user's hand-edited variant) and never keyed on the MATCHER (the matchers
# overlap across jobs, so that would take a sibling job's hooks).
_SESSION_START_RETIRED_COMMANDS: "frozenset[str]" = frozenset({
    'python3 "$HOME/playbook/kanibako/scripts/import-directives.py" '
    '--additional-context "$KANIBAKO_DIRECTIVE_SEED" || true',
})


def _is_retired_command(entry: object, retired: "frozenset[str]") -> bool:
    """``True`` iff *entry* is a ``type:command`` hook running one of *retired*."""
    return (
        isinstance(entry, dict)
        and isinstance(entry.get("command"), str)
        and entry["command"] in retired
    )


def _drop_retired_hooks(groups: list, retired: "frozenset[str]") -> list:
    """Return *groups* minus the *retired* hook entries; a group they emptied goes too."""
    if not retired:
        return list(groups)
    kept: list = []
    for group in groups:
        if not (isinstance(group, dict) and isinstance(group.get("hooks"), list)):
            kept.append(group)
            continue
        entries = [h for h in group["hooks"] if not _is_retired_command(h, retired)]
        if len(entries) == len(group["hooks"]):
            kept.append(group)
        elif entries:
            kept.append({**group, "hooks": entries})
        # ⚑ A group left with NOTHING was carried solely by the retired command, so
        # dropping the command drops the group.  One that still holds a sibling entry
        # keeps its other keys (``matcher``) and merely loses the retired entry.
    return kept


def _merge_managed_command_hook(
    settings: dict, *, event: str, matcher: str | None, command: str,
    retired: "frozenset[str]",
) -> dict:
    """UNION-MERGE ONE kanibako-managed ``type:command`` hook into ``hooks.<event>``.

    *retired* is that job's own earlier spellings; they are replaced here, and nothing
    else is — a hook kanibako never wrote is never removed.
    """
    merged = copy.deepcopy(settings)
    current_hooks = merged.get("hooks")
    hooks = dict(current_hooks) if isinstance(current_hooks, dict) else {}
    current = hooks.get(event)
    groups = list(current) if isinstance(current, list) else []
    # ⚑ Keyed on the COMMAND, not the matcher — each managed command keeps its OWN group.
    groups = _drop_retired_hooks(groups, retired)
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
    """UNION-MERGE the instruction-delivery ``SessionStart`` hook; returns a NEW dict."""
    return _merge_managed_command_hook(
        settings,
        event="SessionStart",
        matcher=_SESSION_START_MATCHER,
        command=_SESSION_START_COMMAND,
        retired=_SESSION_START_RETIRED_COMMANDS,
    )


# ---------------------------------------------------------------------------
# Agent LIVENESS MARKERS (per-PID): the WRITE side of what box_supervisor reads.
# ---------------------------------------------------------------------------

# ⚑ THE CONTRACT, and it has TWO ends that must name ONE dir:
#   * WRITE (box side) — the marker hooks below run the packaged charter's ``pid-add.sh`` /
#     ``pid-rm.sh``, which expand ``${KANIBAKO_AGENT_MARKERS_DIR:-<this constant's
#     value>}``, i.e. they follow the ENV.
#   * READ (host side) — ``commands/start.py`` hands the supervisor
#     ``--agent-markers-dir <the RESOLVED container_env value>``.
# So the ENV is what actually decides the dir (since MBR-1 P4b it is an ordinary
# ``system.env.KANIBAKO_AGENT_MARKERS_DIR`` slot a user may override), and THIS constant
# is the shared FALLBACK both ends use when nothing overrides it — not the sole source.
# ⚑ Must stay a LITERAL box-local path — it is compared verbatim across the two ends, so
# a shell expression here (``${XDG_RUNTIME_DIR:-/tmp}``) would make them disagree.
# ⚑ THE WRITE-SIDE FALLBACK IS A SECOND SPELLING of this literal — a shell script cannot
# import a Python constant, so ``pid-add.sh``/``pid-rm.sh`` each carry their own ``:-``
# default. Neither language offers a way to make that drift IMPOSSIBLE, so it is made
# LOUD instead: ``test_code_config`` reads the shipped script bytes and pins them here.
AGENT_MARKERS_DIR = "/tmp/kanibako/agents"

# Broad OR over claude's SessionEnd sources, so the marker is cleaned up on ANY clean end.
_SESSION_END_MATCHER = "clear|logout|prompt_input_exit|other"

# The packaged charter's PID helpers, at their BOX path. ⚑ Available in EVERY box, not just
# a claude one: ``core_defaults.rom_default_categories`` takes no agent and ``start.py``
# folds it in unconditionally, so ``charter/general`` is bound wherever a box is launched.
# ⚑ This path is the bind's own dest — ``core_defaults._canon_dest`` of the scripts'
# rom-relative source — spelled out rather than imported, so this module keeps no edge
# into the settings layer for one string join. ``test_code_config`` computes it from
# ``_canon_dest`` and pins it, so relocating the canon dest reds instead of leaving the
# hook pointed at nothing.
_PID_SCRIPT_DIR = "~/canon/charter/general/scripts/util"

# ⚑ ``"$PPID"`` is passed EXPLICITLY rather than left to the script's own default: by the
# time the script runs, ITS ``$PPID`` is this hook's transient shell, which exits at once
# — the marker would name a dead process. The hook shell's ``$PPID`` is the agent.
# ⚑ Silent-safe (``|| true``) by design: a marker is liveness bookkeeping and must never
# be what fails a session start or end.
# ⚑ VERIFIED on real podman (2026-08-30 panel dogfood, both harnesses): the VS Code panel
# DOES run the box's seeded SessionStart hook, and the ``$PPID`` it writes IS the agent.
_AGENT_MARKER_WRITE_COMMAND = f'{_PID_SCRIPT_DIR}/pid-add.sh "$PPID" || true'
_AGENT_MARKER_REMOVE_COMMAND = f'{_PID_SCRIPT_DIR}/pid-rm.sh "$PPID" || true'

# ⚑ Both retired sets are EXACT historical strings, never derived from a live constant:
# the point of a retired command is that the value it was built from no longer exists.
_AGENT_MARKER_WRITE_RETIRED_COMMANDS: "frozenset[str]" = frozenset({
    '~/canon/bible/general/scripts/util/pid-add.sh "$PPID" || true',
    'd="${KANIBAKO_AGENT_MARKERS_DIR:-/tmp/kanibako/agents}"; '
    'mkdir -p "$d" && printf %s "$PPID" > "$d/$PPID" || true',
})
_AGENT_MARKER_REMOVE_RETIRED_COMMANDS: "frozenset[str]" = frozenset({
    '~/canon/bible/general/scripts/util/pid-rm.sh "$PPID" || true',
    'd="${KANIBAKO_AGENT_MARKERS_DIR:-/tmp/kanibako/agents}"; '
    'rm -f "$d/$PPID" || true',
})


def merge_marker_write_hook(settings: dict) -> dict:
    """UNION-MERGE the per-PID marker-WRITE ``SessionStart`` hook (its own managed group)."""
    return _merge_managed_command_hook(
        settings,
        event="SessionStart",
        matcher=_SESSION_START_MATCHER,
        command=_AGENT_MARKER_WRITE_COMMAND,
        retired=_AGENT_MARKER_WRITE_RETIRED_COMMANDS,
    )


def merge_marker_remove_hook(settings: dict) -> dict:
    """UNION-MERGE the per-PID marker-REMOVE ``SessionEnd`` hook; returns a NEW dict."""
    return _merge_managed_command_hook(
        settings,
        event="SessionEnd",
        matcher=_SESSION_END_MATCHER,
        command=_AGENT_MARKER_REMOVE_COMMAND,
        retired=_AGENT_MARKER_REMOVE_RETIRED_COMMANDS,
    )


def seed_session_start_hook(settings_path: Path, *, root: Path) -> bool:
    """Seed all THREE managed claude hooks into ``settings.json`` in one read-modify-write.

    *root* is the box home *settings_path* lives under; a symlink at the file or a
    parent skips the seed with a warning (:func:`kanibako.boxfs.no_symlink_or_warn`).
    """
    if not boxfs.no_symlink_or_warn(settings_path, root):
        return False
    existing = _read_existing_config(settings_path, root)
    merged = merge_session_start_hook(existing)
    merged = merge_marker_write_hook(merged)
    merged = merge_marker_remove_hook(merged)
    return _write_if_changed(settings_path, existing, merged, root=root)


# ---------------------------------------------------------------------------
# The ~/.codex/config.toml MANAGER: surgical line/region edits, never a round-trip.
# ---------------------------------------------------------------------------

# ⚑ codex's INTERNAL event id (snake_case), used in the trust state-table key
# ``<cfg>:<event>:<group>:<handler>`` and the hash identity's ``event_name``; the
# config.toml table key is PascalCase (``[hooks.SessionStart]``).
_CODEX_EVENT_KEY = "session_start"
_CODEX_EVENT_KEYS = {
    "SessionStart": _CODEX_EVENT_KEY,
    "PostToolUse": "post_tool_use",
    "Stop": "stop",
}

# The packaged charter's lifecycle hooks and the handbook's mail monitor, at their BOX
# paths — the same commands the claude seed template wires (one spelling per job).
_CHARTER_HOOK_DIR = "~/canon/charter/general/scripts/hooks"
_MAIL_MONITOR_COMMAND = "~/canon/handbook/general/scripts/behavior/check-comms.sh"
# claude's tool names; codex maps ``apply_patch`` onto ``Write``/``Edit`` and
# ``spawn_agent`` onto ``Agent``, and has no ``Read``/``Glob``/``Grep`` (the regex still
# compiles, so one matcher serves both agents).
_MAIL_MONITOR_MATCHER = "Bash|Read|Write|Edit|Glob|Grep|Agent"
_EDITED_MATCHER = "Write|Edit"


class _CodexHook(NamedTuple):
    """One managed single-handler codex hook group."""

    event: str  # the config.toml table key (PascalCase)
    matcher: "str | None"
    command: str
    # ⚑ ALWAYS written explicitly and hashed with the SAME value: codex hashes its
    # per-event DEFAULT when the key is absent, so an implicit timeout is a trust hash
    # that silently fails to match.
    timeout: int


# ⚑ Order is the byte order of the region and fixes each group's trust index.  The
# directive group stays FIRST so its trust key does not move.  The four charter start
# scripts call ``pid-add.sh`` themselves, so they REPLACE the bare marker-write group.
# ⚑ NO SessionEnd group (``end.sh`` calls ``pid-rm.sh``): codex DOES fire SessionEnd,
# but per THREAD, and every thread lives in ONE shared app-server PID, so a pid-rm
# there clears a live panel's marker.  The supervisor reads a stale marker as DEAD,
# the useful verdict (SELF_HEAL_CLI).
_CODEX_MANAGED_HOOKS: "tuple[_CodexHook, ...]" = (
    _CodexHook("SessionStart", _SESSION_START_MATCHER, _SESSION_START_COMMAND, 600),
    *(
        _CodexHook(
            "SessionStart", source, f'{_CHARTER_HOOK_DIR}/{script}.sh "$PPID"', 600,
        )
        for source, script in (
            ("startup", "startup"),
            ("resume", "resume"),
            ("clear", "clear-start"),
            ("compact", "compact"),
        )
    ),
    _CodexHook("PostToolUse", _MAIL_MONITOR_MATCHER, _MAIL_MONITOR_COMMAND, 5),
    _CodexHook("PostToolUse", _EDITED_MATCHER, f"{_CHARTER_HOOK_DIR}/edited.sh", 5),
    _CodexHook("Stop", None, f"{_CHARTER_HOOK_DIR}/stop.sh", 5),
)

# ⚑ The one managed root key; kanibako-OWNED: OVERWRITTEN at every start, never merged.
# ``approval_policy`` is NOT managed: the tier reaches the CLI as ``-c`` on the argv, and
# the panel reads the user's own value.
_CODEX_SANDBOX_MODE_KEY = "sandbox_mode"
# ⚑ BOX INVARIANT, not a tier value: ``workspace-write`` makes the panel's own in-box
# ``codex app-server`` attempt a NESTED bubblewrap sandbox that podman blocks, and it
# stalls ("could not find bubblewrap"). The container IS the sandbox.
_CODEX_SANDBOX_MODE = "danger-full-access"

# Delimiters of the regenerated managed region; everything OUTSIDE it is preserved verbatim.
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
    """Return codex's content-trust hash (``sha256:``) for a single command hook."""
    # ⚑ WIRE IDENTITY — reproduces codex's own ``command_hook_hash``; oracle-pinned in
    # the tests. *command* is RAW, pre-``${ENV}``-expansion (codex hashes the config text).
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
    # ⚑ The ``matcher`` key is OMITTED, not null, when absent — part of the hashed identity.
    if matcher is not None:
        identity["matcher"] = matcher
    canonical = json.dumps(identity, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()


def _toml_basic_string(value: str) -> str:
    """Encode *value* as a TOML basic (double-quoted) string; also used for quoted KEYS."""
    out = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    return f'"{out}"'


def _first_table_index(lines: list[str]) -> int:
    """Index of the first TOML table header (end of the root section), or ``len(lines)``."""
    for i, line in enumerate(lines):
        if re.match(r"\s*\[", line):
            return i
    return len(lines)


def _extract_delimited_region(
    text: str, begin: str, end: str,
) -> tuple[str, str | None]:
    """Split a managed region (*begin*..*end*, inclusive) OUT of *text*; no marker → as-is."""
    lines = text.split("\n")
    begins = [i for i, ln in enumerate(lines) if ln.strip() == begin]
    if not begins:
        return text, None
    b = begins[0]
    ends = [i for i, ln in enumerate(lines) if ln.strip() == end and i >= b]
    # A malformed region whose END marker is missing extends to end-of-file.
    e = ends[0] if ends else len(lines) - 1
    region = "\n".join(lines[b : e + 1])
    del lines[b : e + 1]
    return "\n".join(lines), region


def _strip_delimited_region(text: str, begin: str, end: str) -> str:
    """Body-only form of :func:`_extract_delimited_region`."""
    return _extract_delimited_region(text, begin, end)[0]


def _assemble_codex_managed(body: str, regions: list[str]) -> str:
    """Assemble a managed config.toml: clean user *body* + managed *regions*, in order."""
    # ⚑ The SINGLE SoT for the separator/trailing-newline bytes — BOTH writers call it,
    # so their composed output can never drift. *body* must arrive rstripped of newlines.
    if not regions:
        return body + "\n" if body else ""
    region = "\n\n".join(regions)
    if body:
        return body + "\n\n" + region + "\n"
    return region + "\n"


def _strip_codex_region(text: str) -> str:
    """Remove the kanibako-managed instruction-delivery region (inclusive)."""
    return _strip_delimited_region(text, _CODEX_REGION_BEGIN, _CODEX_REGION_END)


def _reconcile_codex_sandbox_mode(text: str) -> str:
    """Force the managed ``sandbox_mode`` root key to the box invariant."""
    lines = _apply_root_key(
        text.split("\n"), _CODEX_SANDBOX_MODE_KEY,
        desired=_CODEX_SANDBOX_MODE, managed=(_CODEX_SANDBOX_MODE,),
    )
    return "\n".join(lines)


def _apply_root_key(
    lines: list[str], key: str, *,
    desired: "str | None",
    managed: "tuple[str, ...]",
    unconditional: bool = False,
) -> list[str]:
    """Apply the SET-or-CLEAR discipline for one managed root key. Returns a NEW list."""
    result = list(lines)
    # ⚑ ROOT SECTION ONLY: a same-named key inside a user table (``[profiles.x].model``)
    # is never touched, and an insert must land where top-level keys legally bind.
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
    # CLEAR: *unconditional* drops any value; otherwise only a value WE manage, so a
    # user-chosen one survives while a stale value from another tier of ours does not.
    if found is not None:
        if unconditional:
            del result[found]
            return result
        val_re = re.compile(r"\s*" + re.escape(key) + r'\s*=\s*"([^"]*)"')
        m = val_re.match(result[found])
        if m is not None and m.group(1) in managed:
            del result[found]
    return result


def _count_hook_groups(text: str, event: str) -> int:
    """Count user ``[[hooks.<event>]]`` groups — the index the managed ones follow."""
    # Text-based, not tomllib, so a corrupt user file still yields a usable index.
    header = re.compile(r"\s*\[\[\s*hooks\." + re.escape(event) + r"\s*\]\]\s*$")
    return sum(1 for ln in text.split("\n") if header.match(ln))


def _build_codex_managed_region(
    *, box_config_path: str, codex_cwd: str, user_groups: "dict[str, int]",
) -> str:
    """Build the regenerated managed TOML region (hook groups + trust hashes + dir trust)."""
    # ⚑ SINGLE-HANDLER groups, never one multi-handler group: the trust hash is
    # oracle-pinned for a ONE-command identity; the multi-handler shape is unverified.
    # ⚑ *box_config_path* is the BOX-absolute path, NOT the host write path: codex keys
    # trust by the path it reads IN-BOX.  ⚑ A group's trust index counts the user's
    # groups of the SAME event first (*user_groups*), then ours in order.
    groups: list[str] = []
    state: list[str] = []
    next_index = dict(user_groups)
    for hook in _CODEX_MANAGED_HOOKS:
        event_key = _CODEX_EVENT_KEYS[hook.event]
        index = next_index.get(hook.event, 0)
        next_index[hook.event] = index + 1
        groups.append(f"[[hooks.{hook.event}]]")
        if hook.matcher is not None:
            groups.append(f"matcher = {_toml_basic_string(hook.matcher)}")
        groups += [
            "",
            f"[[hooks.{hook.event}.hooks]]",
            'type = "command"',
            f"command = {_toml_basic_string(hook.command)}",
            f"timeout = {hook.timeout}",
            "",
        ]
        trust_key = f"{box_config_path}:{event_key}:{index}:0"
        trust_hash = codex_trusted_hash(
            event_key, hook.matcher, hook.command, hook.timeout,
        )
        state += [
            f"[hooks.state.{_toml_basic_string(trust_key)}]",
            f"trusted_hash = {_toml_basic_string(trust_hash)}",
            "",
        ]
    return "\n".join(
        [
            _CODEX_REGION_BEGIN,
            *groups,
            *state,
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
    """Return *text* with kanibako's managed codex config MERGED in (pure, idempotent)."""
    # ⚑ rstrip on every strip: re-merges must not accumulate blank lines (idempotence).
    body = _strip_codex_region(text).rstrip("\n")
    # ⚑ Strip the provider region UNCONDITIONALLY, in BOTH directions — that is what
    # makes this a symmetric reconciliation rather than a one-way add.
    body = _strip_codex_provider_region(body).rstrip("\n")
    if model_provider is not None:
        # ⚑ REPLACE onto the CLEAN body (no regions yet), so root keys land in the legal
        # top-level position and OUTSIDE both regions — never swallowed by a re-strip.
        body = _apply_provider_root_keys(
            body, model=model_provider.model, provider_id=model_provider.provider_id,
        )
    else:
        # WIPE: drop the kanibako-owned root keys so no stale selection lingers on a
        # box that has gone bare.
        body = _remove_provider_root_keys(body)
    user_groups = {
        event: _count_hook_groups(body, event) for event in _CODEX_EVENT_KEYS
    }
    regions = [
        _build_codex_managed_region(
            box_config_path=box_config_path,
            codex_cwd=codex_cwd,
            user_groups=user_groups,
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
# The ~/.codex/config.toml MODEL-PROVIDER generator (codex personas).
# ---------------------------------------------------------------------------

# ⚑ DISTINCT from the hook region's markers, so the two regions strip and regenerate
# independently and can coexist in one file.
_CODEX_PROVIDER_REGION_BEGIN = (
    "# >>> kanibako-managed (model provider) — do not edit >>>"
)
_CODEX_PROVIDER_REGION_END = "# <<< kanibako-managed (model provider) <<<"


class CodexModelProvider(NamedTuple):
    """The six resolved values selecting a codex external model provider."""

    provider_id: str
    name: str
    base_url: str
    wire_api: str
    env_key: str
    model: str


def _strip_codex_provider_region(text: str) -> str:
    """Remove the kanibako-managed model-provider region (inclusive)."""
    return _strip_delimited_region(
        text, _CODEX_PROVIDER_REGION_BEGIN, _CODEX_PROVIDER_REGION_END,
    )


def _apply_provider_root_keys(body: str, *, model: str, provider_id: str) -> str:
    """SET the managed top-level ``model``/``model_provider`` root keys on *body*."""
    # ⚑ *body* must be CLEAN user content: the "first table" has to be a USER table,
    # never one of our own managed regions.
    lines = body.split("\n")
    lines = _apply_root_key(lines, "model", desired=model, managed=(model,))
    lines = _apply_root_key(
        lines, "model_provider", desired=provider_id, managed=(provider_id,),
    )
    return "\n".join(lines)


def _remove_provider_root_keys(body: str) -> str:
    """REMOVE the kanibako-owned ``model``/``model_provider`` root keys (the WIPE side)."""
    # ⚑ UNCONDITIONAL: kanibako owns these outright, so a stale persona selection is not
    # a hand-edit to preserve. *body* must be CLEAN user content.
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
    """Build the regenerated ``[model_providers.<id>]`` TOML region."""
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
    """Return *text* with the codex model-provider selection MERGED in (pure, idempotent)."""
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
    root: Path,
) -> bool:
    """Write the box's ``~/.codex/config.toml`` managed hook, trust and provider regions.

    *root* is the box home *config_path* lives under; a symlink at the file or a
    parent skips the seed with a warning, and the write goes through :mod:`kanibako.boxfs`.
    """
    if not boxfs.no_symlink_or_warn(config_path, root):
        return False
    try:
        existing = boxfs.read_text(config_path, root)
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
    _make_box_home(root)
    boxfs.write_text(config_path, root, merged)
    return True


def seed_codex_sandbox_mode(config_path: Path, *, root: Path) -> bool:
    """Write ONLY the managed codex ``sandbox_mode`` invariant; the SOLE writer of it.

    *root* is the box home *config_path* lives under; a symlink at the file or a
    parent skips the write with a warning, and the write goes through :mod:`kanibako.boxfs`.
    """
    if not boxfs.no_symlink_or_warn(config_path, root):
        return False
    found = True
    try:
        existing = boxfs.read_text(config_path, root)
    except OSError:
        existing, found = "", False
    # ⚑ EXTRACT the managed regions before root-key surgery: an insert goes before the
    # first table, which in a region-bearing file would otherwise be OUR OWN region.
    body, hook_region = _extract_delimited_region(
        existing, _CODEX_REGION_BEGIN, _CODEX_REGION_END,
    )
    body, provider_region = _extract_delimited_region(
        body, _CODEX_PROVIDER_REGION_BEGIN, _CODEX_PROVIDER_REGION_END,
    )
    clean = body.rstrip("\n")
    reconciled = _reconcile_codex_sandbox_mode(clean)
    if reconciled == clean:
        # Already correct — NEVER rewrite: no gratuitous normalization of user bytes.
        return False
    regions = [r for r in (hook_region, provider_region) if r is not None]
    merged = _assemble_codex_managed(reconciled, regions)
    if found and merged == existing:
        return False
    boxfs.write_text(config_path, root, merged)
    return True
