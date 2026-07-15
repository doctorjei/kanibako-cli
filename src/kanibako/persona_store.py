"""persona-grata store discovery, resolve, and import mapping (no box).

The persona-grata STANDARD lays down per-persona / per-harness harness-native
config under a fixed discovery root (design SOT
``plans/persona-grata-autoimport-DESIGN.md`` §1):

    $XDG_CONFIG_HOME/personas/          <- discovery root (FIXED, not configurable)
      <pid>/                            <- persona (identity segment)
        .secret_path                    <- ONE line: the path to the token file
        <hid>/                          <- harness (e.g. codex/, claude/)
          config.toml | settings.json   <- rendered harness-NATIVE config

kanibako CONSUMES the store: a ``<pid>+<hid>`` agent ref whose store entry
exists is a *persona agent*; its endpoint/model/token-pointer are reparsed from
the store at every start and synced into the agent-scope settings (settings
sync, never file sync — DESIGN §2b).  Store PRESENCE decides persona-vs-plain
(DESIGN §4): everything here returns a clean "not a persona" ``None`` on a
miss so callers fall through to normal agent handling.

Two halves live here:

* **discovery + resolve** (pure reads): locate an entry and resolve its
  ``.secret_path`` token pointer.  Harness-config extraction lives on the
  Target plugins (:meth:`kanibako.targets.base.Target.read_persona_settings`).
* **import mapping** (the settings sync): :func:`build_candidate` turns a
  located entry into an IN-MEMORY candidate :class:`AgentConfig` (current
  agent-file values + the store's endpoint/model/token-pointer applied), and
  :func:`persist_candidate` commits it through ``write_agent_config`` — which
  routes the values to ``self.endpoint`` / ``self.model`` /
  ``self.secret_path.<VAR>`` (the flattened shape, ``agent_file_route`` SoT).
  The split is load-bearing: the start-flow hook (Phase 3) VERIFIES the
  candidate first and persists only on PASS (verified swap — DESIGN §2b), while
  keeping the on-disk last-known-good on FAIL.  :func:`import_persona_entry`
  is the build+persist convenience for the unverified paths (initial create
  registration).

The start-flow / create wiring itself is NOT here.  Nothing in this module
reads the TOKEN file itself (the pointer is handled arm's-length, exactly like
the ``secret_path`` keyspace category it feeds).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

from kanibako.agent_config import (
    AgentConfig,
    agent_settings_path,
    load_agent_config,
    write_agent_config,
)
from kanibako.agent_ref import harness_of, parse_agent_ref, persona_of
from kanibako.paths import xdg

if TYPE_CHECKING:
    from kanibako.targets.base import Target

#: Read cap for ``.secret_path`` (bytes).  The file holds one token path
#: (PATH_MAX-ish); anything larger is a malformed store — rejected WITHOUT
#: slurping it into memory (tolerance contract: fail warnable, never OOM).
_POINTER_CAP = 16 * 1024


@dataclass(frozen=True)
class PersonaEntry:
    """A located persona-grata store entry for one ``<pid>/<hid>`` pair."""

    node: str          # canonical node-name "<pid>℘<hid>"
    persona: str       # pid (identity segment)
    harness: str       # hid (harness segment)
    persona_dir: Path  # <root>/<pid>/  (anchors relative .secret_path values)
    config_dir: Path   # <root>/<pid>/<hid>/  (the rendered harness config dir)


class SecretPathResult(NamedTuple):
    """Outcome of resolving a persona's ``.secret_path`` token pointer.

    Exactly one of the fields is set: *path* (an ABSOLUTE host path) on
    success, else *error* (a caller-warnable reason).  Tolerant by contract —
    :func:`resolve_secret_path` never raises through.
    """

    path: Path | None
    error: str | None


def persona_store_root() -> Path:
    """The persona-grata discovery root: ``$XDG_CONFIG_HOME/personas/``.

    FIXED by design (DESIGN §5a — not configurable); the single builder for
    the store path.  Reuses the spec-backed XDG resolution in
    :func:`kanibako.paths.xdg` (env var honored iff set AND absolute, else
    ``$HOME/.config``).
    """
    return xdg("XDG_CONFIG_HOME", ".config") / "personas"


def locate_entry(ref: str) -> PersonaEntry | None:
    """Locate the store entry for an agent *ref*, iff one exists.

    *ref* is any accepted agent ref (``navigator+codex``, ``navigator℘codex``,
    or an already-canonical node-name); it is normalised through
    :func:`kanibako.agent_ref.parse_agent_ref` like every other ref source.

    Returns ``None`` — a clean "not a persona" — when:

    * the ref is BARE (node == harness, e.g. ``claude``): a bare agent never
      has a persona store entry;
    * either segment is ``.`` / ``..`` (legal in a ref, but AS A PATH COMPONENT
      it would escape the store root — ``..+claude`` must not hit
      ``$XDG_CONFIG_HOME/claude/``);
    * the store dir ``<root>/<pid>/<hid>/`` is absent (or not a directory):
      store PRESENCE decides persona-vs-plain (DESIGN §4), so the caller falls
      through to normal agent handling.

    A malformed ref raises :class:`~kanibako.errors.ConfigError` from
    ``parse_agent_ref`` — the same contract as every other ref consumer (a bad
    ref is a user error, not a store miss).
    """
    node, harness = parse_agent_ref(ref)
    if harness_of(node) == node:
        return None  # bare agent: no persona segment -> never a store entry
    persona = persona_of(node)
    if persona in (".", "..") or harness in (".", ".."):
        return None  # dot segments traverse OUT of the store root: never a persona
    root = persona_store_root()
    persona_dir = root / persona
    config_dir = persona_dir / harness
    try:
        if not config_dir.is_dir():
            return None
    except OSError:
        return None
    return PersonaEntry(
        node=node,
        persona=persona,
        harness=harness,
        persona_dir=persona_dir,
        config_dir=config_dir,
    )


def resolve_secret_path(entry: PersonaEntry) -> SecretPathResult:
    """Resolve ``<pid>/.secret_path`` to an ABSOLUTE host token path.

    The pointer file holds EXACTLY ONE line — the path to the token file
    (DESIGN §2).  Resolution rules, in order:

    * expand ``$VAR`` then ``~`` (``os.path.expandvars`` →
      ``Path.expanduser`` — the same order as the launch-side
      ``_secret_pointer_usable``);
    * an ABSOLUTE result is used as-is;
    * a RELATIVE result (``./token``, bare ``token``) is anchored to the
      directory ``.secret_path`` sits in — *entry.persona_dir* — so
      ``./token`` → ``<root>/<pid>/token``.

    An absolute HOST path is required because kanibako mounts the token
    arm's-length into the box, where a relative anchor is meaningless
    (DESIGN §3).  The token file itself is NEVER opened or stat'd here — the
    result is a pointer (it resolves even when the token does not exist yet;
    usability is the launch gate's job).

    Tolerant errors (never raises through): a missing / unreadable / non-text /
    oversized / empty / whitespace-only / MULTI-line ``.secret_path``, or a
    line that cannot expand to a usable path (``~nosuchuser``, an embedded NUL)
    yields ``SecretPathResult(None, <reason>)`` for the caller to warn on.  A
    single trailing newline is not a second line.
    """
    pointer = entry.persona_dir / ".secret_path"
    try:
        with pointer.open(encoding="utf-8") as fh:
            raw = fh.read(_POINTER_CAP + 1)  # capped: one path line, never a slurp
    except FileNotFoundError:
        return SecretPathResult(None, f"no .secret_path file at {pointer}")
    except UnicodeDecodeError:
        return SecretPathResult(None, f"{pointer} is not valid UTF-8 text")
    except OSError as exc:
        return SecretPathResult(None, f"cannot read {pointer}: {exc}")
    if len(raw) > _POINTER_CAP:
        return SecretPathResult(
            None, f"{pointer} is too large to be a token path (one line expected)",
        )

    # Exactly one line: strip ONE trailing newline (LF or CRLF), then any
    # remaining newline means a real second line (the file is malformed).
    body = raw[:-1] if raw.endswith("\n") else raw
    body = body[:-1] if body.endswith("\r") else body
    if "\n" in body or "\r" in body:
        return SecretPathResult(
            None, f"{pointer} must hold exactly one line (the token path)",
        )
    line = body.strip()
    if not line:
        return SecretPathResult(None, f"{pointer} is empty")

    try:
        expanded = Path(os.path.expandvars(line)).expanduser()
        if expanded.is_absolute():
            return SecretPathResult(expanded.resolve(), None)
        return SecretPathResult((entry.persona_dir / expanded).resolve(), None)
    except (OSError, RuntimeError, ValueError) as exc:
        # expanduser -> RuntimeError (unresolvable ~user); resolve -> ValueError
        # (embedded NUL) / OSError.  All are malformed-pointer shapes; the
        # never-raises-through contract holds.
        return SecretPathResult(None, f"cannot resolve {pointer}: {exc}")


# --------------------------------------------------------------------------
# Import mapping (DESIGN §2b/§3): store entry -> agent-scope settings sync.
# --------------------------------------------------------------------------


class ImportResult(NamedTuple):
    """Outcome of building (and optionally persisting) a persona import.

    * *candidate* — the in-memory :class:`AgentConfig` with the store's values
      applied on top of the CURRENT agent-file values; ``None`` on a hard
      failure (nothing importable — the store's harness config is missing /
      unusable, or names no endpoint).
    * *error* — the hard-failure reason (*candidate* is ``None``).
    * *warning* — a SOFT, caller-warnable condition on a successful build:
      the ``.secret_path`` pointer did not resolve, so the endpoint/model were
      imported but the existing (last-known-good) token pointer was KEPT —
      the DESIGN §5b fail-safe shape.  ``None`` when everything resolved.
    """

    candidate: AgentConfig | None
    error: str | None
    warning: str | None


def build_candidate(
    agents_root: Path, entry: PersonaEntry, target: Target,
) -> ImportResult:
    """Build the IN-MEMORY candidate agent settings for a store *entry*.

    Read-modify-write shape, WITHOUT the write: load the node's CURRENT
    settings file (absent file = fresh defaults), then apply ONLY the values
    the store owns (DESIGN §2b — the store owns the AGENT scope):

    * ``state["endpoint"]`` ← the harness config's endpoint (REQUIRED: a
      persona import with no endpoint is unusable → hard error);
    * ``state["model"]`` ← the harness config's model, when it names one (a
      config that names none leaves any existing agent-scope model untouched —
      there is no store value to sync);
    * ``secret_path[<auth_env>]`` ← the resolved ABSOLUTE host token path.  An
      unresolvable ``.secret_path`` is a WARNING, not a failure: endpoint and
      model still import, the existing token pointer (last-known-good) is
      kept, and the reason rides ``ImportResult.warning``.

    Everything else in the file (name, run_args, env, transform_settings,
    other secret_path vars, the discriminated node-bind sub-tables) is
    PRESERVED verbatim — this function does not own it.  NOTHING is persisted
    here: the caller verifies the candidate (Phase 3 S9) and commits via
    :func:`persist_candidate` only on PASS.
    """
    settings = target.read_persona_settings(entry.config_dir)
    if settings is None:
        return ImportResult(None, (
            f"persona store entry for '{entry.node}' has no usable "
            f"{entry.harness} config under {entry.config_dir}"
        ), None)
    if not settings.endpoint:
        return ImportResult(None, (
            f"persona store config at {entry.config_dir} names no endpoint; "
            f"a persona import needs one"
        ), None)

    cfg = load_agent_config(agent_settings_path(agents_root, entry.node))
    cfg.state["endpoint"] = settings.endpoint
    if settings.model:
        cfg.state["model"] = settings.model

    warning: str | None = None
    token = resolve_secret_path(entry)
    if token.path is not None:
        cfg.secret_path[settings.auth_env] = str(token.path)
    else:
        warning = (
            f"persona '{entry.node}': token pointer did not resolve "
            f"({token.error}); keeping the existing secret_path values"
        )
    return ImportResult(cfg, None, warning)


def persist_candidate(
    agents_root: Path, entry: PersonaEntry, candidate: AgentConfig,
) -> Path:
    """Commit a verified *candidate* to the node's settings file.

    Routes through :func:`~kanibako.agent_config.write_agent_config` — the
    established persist seam — which stores the persona values as
    ``self.endpoint`` / ``self.model`` and ``self.secret_path.<VAR>`` (the
    FLATTENED shape: ``self`` IS ``agent.<node>``, no second node embedding)
    and re-emits everything the candidate carried through.  Returns the
    settings-file path written.
    """
    path = agent_settings_path(agents_root, entry.node)
    write_agent_config(path, candidate)
    return path


def import_persona_entry(
    agents_root: Path, entry: PersonaEntry, target: Target,
) -> ImportResult:
    """Build AND persist a persona import (the UNVERIFIED convenience).

    :func:`build_candidate` + :func:`persist_candidate` in one step, for the
    paths that import without a verify probe (the initial ``create``
    registration — DESIGN §4).  A hard failure persists NOTHING (the on-disk
    file, if any, is untouched); a soft ``warning`` still persists (endpoint/
    model imported, existing token kept) and is the caller's to surface.
    The start-flow reparse (Phase 3 S9) must NOT use this — it verifies the
    candidate first and swaps only on PASS.
    """
    result = build_candidate(agents_root, entry, target)
    if result.candidate is not None:
        persist_candidate(agents_root, entry, result.candidate)
    return result
