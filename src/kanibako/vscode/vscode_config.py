"""Host-side writers for the config surfaces a VS Code box needs — one per agent.

Terminology.  *Attached container configuration* — the devcontainer.json subset VS Code's
Dev Containers extension reads when attaching to a running container; it is keyed by IMAGE,
not by box, and VS Code OWNS the file.  *Panel* — the in-box agent EXTENSION VS Code runs;
it spawns its own agent process without kanibako's launch flags, so a permission tier
reaches it only through the box's on-disk agent config.  *Managed region* — a
comment-delimited block of a user's codex ``config.toml`` that kanibako regenerates whole;
every byte outside a managed region (comment or data) is preserved.
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


def _read_existing_config(path: Path) -> dict:
    """Read *path* as a JSON object → ``{}`` on absence/corruption; NEVER raises."""
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


# ---------------------------------------------------------------------------
# claude panel permissions: the in-box ~/.claude/settings.json projection.
# ---------------------------------------------------------------------------

# ⚑ Driven by the box's CASCADE-resolved ``access``, NEVER the ephemeral ``-S``/``-A``:
# a projection outlives the launch (spec §1A). A tier ABSENT here means CLEAR.
_CLAUDE_MODE_BY_TIER: "dict[str, str]" = {
    "editing": "acceptEdits",
    "full": "bypassPermissions",
}

#: Every value kanibako may have written here — what the CLEAR path may remove.
_MANAGED_MODES: frozenset[str] = frozenset(_CLAUDE_MODE_BY_TIER.values())


def _claude_managed_mode(access: str) -> "str | None":
    """The managed ``defaultMode`` for *access*, or ``None`` to CLEAR; RAISES on unknown."""
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
    """UNION-MERGE ``permissions.defaultMode = <mode>``, returning a NEW dict."""
    merged = copy.deepcopy(settings)
    current = merged.get("permissions")
    perms = dict(current) if isinstance(current, dict) else {}
    perms["defaultMode"] = mode
    merged["permissions"] = perms
    return merged


def clear_permission_mode(settings: dict) -> dict:
    """Return a NEW dict with kanibako's MANAGED ``permissions.defaultMode`` removed."""
    merged = copy.deepcopy(settings)
    current = merged.get("permissions")
    # ⚑ Only a value WE manage is removed — a user-chosen mode we never write survives.
    if not isinstance(current, dict) or current.get("defaultMode") not in _MANAGED_MODES:
        return merged
    perms = dict(current)
    del perms["defaultMode"]
    # An emptied ``permissions`` was ours to create, so it goes; one with siblings stays.
    if perms:
        merged["permissions"] = perms
    else:
        del merged["permissions"]
    return merged


def _write_if_changed(path: Path, existing: dict, merged: dict) -> bool:
    """Write *merged* to *path* as pretty JSON iff it differs from disk (idempotent)."""
    if path.exists() and merged == existing:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(merged, indent=2) + "\n")
    return True


def seed_claude_permission_mode(settings_path: Path, *, access: str) -> bool:
    """Project the box's ``access`` tier into the box's claude ``settings.json``."""
    mode = _claude_managed_mode(access)
    if mode is None:
        # CLEAR is a NO-OP on an absent file — never create one just to clear it.
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


def _merge_managed_command_hook(
    settings: dict, *, event: str, matcher: str | None, command: str,
) -> dict:
    """UNION-MERGE ONE kanibako-managed ``type:command`` hook into ``hooks.<event>``."""
    merged = copy.deepcopy(settings)
    current_hooks = merged.get("hooks")
    hooks = dict(current_hooks) if isinstance(current_hooks, dict) else {}
    current = hooks.get(event)
    groups = list(current) if isinstance(current, list) else []
    # ⚑ Keyed on the COMMAND, not the matcher — each managed command keeps its OWN group.
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
    )


# ---------------------------------------------------------------------------
# Agent LIVENESS MARKERS (per-PID): the WRITE side of what box_supervisor reads.
# ---------------------------------------------------------------------------

# ⚑ THE CONTRACT, and it has TWO ends that must name ONE dir:
#   * WRITE (box side) — the marker hooks below expand
#     ``${KANIBAKO_AGENT_MARKERS_DIR:-<this constant>}``, i.e. they follow the ENV.
#   * READ (host side) — ``commands/start.py`` hands the supervisor
#     ``--agent-markers-dir <the RESOLVED container_env value>``.
# So the ENV is what actually decides the dir (since MBR-1 P4b it is an ordinary
# ``system.env.KANIBAKO_AGENT_MARKERS_DIR`` slot a user may override), and THIS constant
# is the shared FALLBACK both ends use when nothing overrides it — not the sole source.
# ⚑ Must stay a LITERAL box-local path — it is compared verbatim across the two ends, so
# a shell expression here (``${XDG_RUNTIME_DIR:-/tmp}``) would make them disagree.
AGENT_MARKERS_DIR = "/tmp/kanibako/agents"

# Broad OR over claude's SessionEnd sources, so the marker is cleaned up on ANY clean end.
_SESSION_END_MATCHER = "clear|logout|prompt_input_exit|other"

# ⚑ Default-dir portion is built FROM :data:`AGENT_MARKERS_DIR`, never a second literal.
# ⚑ UNVERIFIED (bifrost e2e): that ``$PPID`` in a claude hook IS the agent PID, and that
# the VS Code panel claude runs the box's seeded hooks at all.
_AGENT_MARKER_WRITE_COMMAND = (
    f'd="${{KANIBAKO_AGENT_MARKERS_DIR:-{AGENT_MARKERS_DIR}}}"; '
    'mkdir -p "$d" && printf %s "$PPID" > "$d/$PPID" || true'
)
_AGENT_MARKER_REMOVE_COMMAND = (
    f'd="${{KANIBAKO_AGENT_MARKERS_DIR:-{AGENT_MARKERS_DIR}}}"; '
    'rm -f "$d/$PPID" || true'
)


def merge_marker_write_hook(settings: dict) -> dict:
    """UNION-MERGE the per-PID marker-WRITE ``SessionStart`` hook (its own managed group)."""
    return _merge_managed_command_hook(
        settings,
        event="SessionStart",
        matcher=_SESSION_START_MATCHER,
        command=_AGENT_MARKER_WRITE_COMMAND,
    )


def merge_marker_remove_hook(settings: dict) -> dict:
    """UNION-MERGE the per-PID marker-REMOVE ``SessionEnd`` hook; returns a NEW dict."""
    return _merge_managed_command_hook(
        settings,
        event="SessionEnd",
        matcher=_SESSION_END_MATCHER,
        command=_AGENT_MARKER_REMOVE_COMMAND,
    )


def seed_session_start_hook(settings_path: Path) -> bool:
    """Seed all THREE managed claude hooks into ``settings.json`` in one read-modify-write."""
    existing = _read_existing_config(settings_path)
    merged = merge_session_start_hook(existing)
    merged = merge_marker_write_hook(merged)
    merged = merge_marker_remove_hook(merged)
    return _write_if_changed(settings_path, existing, merged)


# ---------------------------------------------------------------------------
# The ~/.codex/config.toml MANAGER: surgical line/region edits, never a round-trip.
# ---------------------------------------------------------------------------

# ⚑ codex's INTERNAL event id (snake_case), used in the trust state-table key
# ``<cfg>:<event>:<group>:<handler>`` and the hash identity's ``event_name``; the
# config.toml table key is PascalCase (``[hooks.SessionStart]``).
_CODEX_EVENT_KEY = "session_start"

# ⚑ Both managed root keys are kanibako-OWNED: OVERWRITTEN at every tier, never merged.
_CODEX_APPROVAL_POLICY_KEY = "approval_policy"
_CODEX_SANDBOX_MODE_KEY = "sandbox_mode"
# ⚑ BOX INVARIANT, not a tier value: ``workspace-write`` makes the panel's own in-box
# ``codex app-server`` attempt a NESTED bubblewrap sandbox that podman blocks, and it
# stalls ("could not find bubblewrap"). The container IS the sandbox.
_CODEX_SANDBOX_MODE = "danger-full-access"

# ⚑ Exact members of the codex approval enum, verbatim from ``codex --help`` (0.141.0);
# the table is TOTAL over ACCESS_TIERS, which is what makes "kanibako owns this key" real.
# ⚑ The VOCABULARY is verified; the tier↔value PAIRING beyond ``never``/``full`` is NOT —
# confirm on the bifrost matrix.
_CODEX_APPROVAL_BY_TIER: "dict[str, str]" = {
    "restricted": "untrusted",
    "editing": "on-request",
    "full": "never",
}

#: Every ``approval_policy`` value kanibako may have written. ⚑ Currently UNCONSULTED —
#: both managed root keys are SET at every tier, so no CLEAR path reads this allowlist.
_CODEX_MANAGED_APPROVALS: frozenset[str] = frozenset(
    _CODEX_APPROVAL_BY_TIER.values()
)

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


def _codex_managed_approval(access: str) -> str:
    """The managed ``approval_policy`` for *access* — one per tier; RAISES on unknown."""
    policy = _CODEX_APPROVAL_BY_TIER.get(access)
    if policy is None:
        raise ConfigError(
            f"codex panel approval: unknown access tier {access!r} "
            f"(expected restricted | editing | full)."
        )
    return policy


def _reconcile_codex_approval(text: str, access: str) -> str:
    """Reconcile the managed ``sandbox_mode`` (invariant) and ``approval_policy`` (tier) keys."""
    lines = text.split("\n")
    # ⚑ ORDER IS LOAD-BEARING: emitting the unconditional invariant FIRST makes a fresh
    # insert land ``sandbox_mode`` above ``approval_policy`` — the canonical byte order.
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


def _count_session_start_groups(text: str) -> int:
    """Count user ``[[hooks.SessionStart]]`` groups — the index the managed ones follow."""
    # Text-based, not tomllib, so a corrupt user file still yields a usable index.
    header = re.compile(r"\s*\[\[\s*hooks\.SessionStart\s*\]\]\s*$")
    return sum(1 for ln in text.split("\n") if header.match(ln))


def _build_codex_managed_region(
    *, box_config_path: str, codex_cwd: str, group_index: int
) -> str:
    """Build the regenerated managed TOML region (hook groups + trust hashes + dir trust)."""
    # ⚑ TWO SINGLE-HANDLER groups, never one two-handler group: the trust hash is
    # oracle-pinned for a ONE-command identity; the multi-handler shape is unverified.
    # ⚑ There is no marker-REMOVE group because codex has NO SessionEnd/exit event.
    # ⚑ *box_config_path* is the BOX-absolute path, NOT the host write path: codex keys
    # trust by the path it reads IN-BOX.
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
) -> bool:
    """Write the box's ``~/.codex/config.toml`` managed hook, trust and provider regions."""
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
    """Write ONLY the managed codex approval/sandbox parity keys; the SOLE writer of them."""
    try:
        existing = config_path.read_text()
    except OSError:
        existing = ""
    # ⚑ EXTRACT the managed regions before root-key surgery: an insert goes before the
    # first table, which in a region-bearing file would otherwise be OUR OWN region.
    body, hook_region = _extract_delimited_region(
        existing, _CODEX_REGION_BEGIN, _CODEX_REGION_END,
    )
    body, provider_region = _extract_delimited_region(
        body, _CODEX_PROVIDER_REGION_BEGIN, _CODEX_PROVIDER_REGION_END,
    )
    clean = body.rstrip("\n")
    reconciled = _reconcile_codex_approval(clean, access)
    if reconciled == clean:
        # Already correct — NEVER rewrite: no gratuitous normalization of user bytes.
        return False
    regions = [r for r in (hook_region, provider_region) if r is not None]
    merged = _assemble_codex_managed(reconciled, regions)
    if config_path.exists() and merged == existing:
        return False
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(merged)
    return True


# ---------------------------------------------------------------------------
# goose panel permissions: the in-box goose config.yaml GOOSE_MODE projection.
# ---------------------------------------------------------------------------

# ⚑ ``restricted`` PERSISTS ``approve`` rather than clearing: goose's UNSET default is
# ``auto`` (permissive), so a clear would silently restore permissive.
# ⚑ ``editing`` is deliberately ABSENT — goose has no value that realizes it, and this
# surface is PERSISTED, so a substitution would outlive the launch.
_GOOSE_MODE_BY_TIER: "dict[str, str]" = {
    "restricted": "approve",
    "full": "auto",
}


def seed_goose_mode(config_path: Path, *, access: str) -> bool:
    """SET the box's goose ``GOOSE_MODE`` to its ``access`` tier parity value."""
    # ⚑ This raise is the SECOND fence, not the gate: callers wrap panel delivery
    # best-effort and would swallow it. ``targets.assembly.access_row`` refuses first.
    desired = _GOOSE_MODE_BY_TIER.get(access)
    if desired is None:
        # ⚑ ACCESS_TIERS order (least → most permissive); a sorted() here would print
        # "full | restricted" and read as a ladder running the wrong way.
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
    # ⚑ Write via the Path object, NOT ``config_io.dump_doc``: its ``atomic_write_text``
    # coerces through ``Path()``/mkstemp and does a REAL mkdir, so a mocked path would
    # materialize a stray on-disk dir. A best-effort re-seed is idempotent anyway.
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        yaml.safe_dump(
            merged, sort_keys=False, default_flow_style=False, allow_unicode=True,
        )
    )
    return True
