"""The Target plugin contract: the ABC, its descriptor types, and the bind-shaped key map."""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, NamedTuple

from kanibako.settings.settings_keyspace import ACCESS_TIERS

if TYPE_CHECKING:
    from kanibako.settings.agent_config import AgentConfig
    from kanibako.vscode.vscode_config import CodexModelProvider

# The map stored at ANY terminal bind-shaped key: `{box_dest: (host_src[, options])}`.
# ⚑ `BindDefault`, the NAME-keyed tuple, IS GONE — a plugin emitting one is refused BY NAME.
BindArm = dict[str, tuple[str, ...]]

# What `Target.default_category_binds` / `default_common` / `default_seeds` return.
CategoryBindDefaults = dict[str, BindArm]


@dataclass(frozen=True)
class TargetSetting:
    """A runtime setting a target advertises through `setting_descriptors`."""

    key: str                     # Setting key in agent state dict (e.g. "model")
    description: str             # Human-readable description
    default: str = ""            # Default value when not overridden
    choices: tuple[str, ...] = ()  # Valid values; empty = freeform


@dataclass(frozen=True)
class Mount:
    """A volume mount for a container."""

    source: Path
    destination: str
    options: str = ""  # e.g. "ro"

    def to_volume_arg(self) -> str:
        """Return the -v argument string for podman/docker."""
        base = f"{self.source}:{self.destination}"
        return f"{base}:{self.options}" if self.options else base


@dataclass
class AgentInstall:
    """Information about an agent installation on the host."""

    name: str  # e.g. "claude"
    binary: Path  # host symlink/path to agent binary
    install_dir: Path  # root of agent installation
    # Host entrypoint the plugin owns, bound in as-is; anchored, never resolved via $PATH
    launcher: Path | None = None


class BindKind(Enum):
    """Whether a binding mounts a single file or a directory."""

    FILE = "file"
    DIR = "dir"


class HostSrcOrigin(Enum):
    """Where a binding's default host source comes from, before any cascade override."""

    LAUNCHER = "launcher"        # AgentInstall.launcher (detection-derived)
    INSTALL_DIR = "install_dir"  # AgentInstall.install_dir
    BINARY = "binary"            # AgentInstall.binary
    LITERAL = "literal"          # a fixed Path in the descriptor (literal_src)


class BindScope(Enum):
    """How widely a binding applies + its failure semantics."""

    AGENT_CRITICAL = "agent_critical"  # delivery-essential; missing source safe-fails; ro
    AGENT = "agent"                    # agent-level share (plugins); overridable; may be rw


@dataclass(frozen=True)
class Binding:
    """One bound element: a delivery binary/launcher/share, or an agent share."""

    # ⚑ `key` is a SETTINGS-FILE-only override key; there is no CLI route, so document none.
    key: str                          # override key -> agent.<name>.bindings.{ro,rw}.<key>
    origin: HostSrcOrigin
    box_dest: str
    kind: BindKind
    scope: BindScope
    ro: bool = True
    literal_src: Path | None = None   # only when origin == LITERAL


class Channel(Enum):
    """Where a value-bearing arg is emitted: an argv flag or an environment variable."""

    FLAG = "flag"
    ENV = "env"


@dataclass(frozen=True)
class SettingArg:
    """A value-bearing agent setting routed to an argv flag or an env var (model, provider)."""

    setting_key: str                  # agent setting supplying the value ("model", "provider")
    channel: Channel
    flag: tuple[str, ...] = ()        # FLAG form, e.g. ("--model",)
    env_var: str = ""                 # ENV form, e.g. "GOOSE_MODEL"


@dataclass(frozen=True)
class AccessTierRow:
    """How one `access` tier is realized for one harness (spec §2d)."""

    # ⚑ An EMPTY row means emit nothing, DELIBERATELY; a MISSING row means cannot render.
    flag: tuple[str, ...] = ()   # FLAG channel emission ( () = emit nothing )
    env_value: str = ""          # ENV channel emission ( "" = emit nothing )


@dataclass(frozen=True)
class AccessRealization:
    """The per-harness realization of the `access` permission tier (spec §2d)."""

    channel: Channel
    env_var: str = ""            # ENV form's variable name (e.g. goose GOOSE_MODE)
    restricted: "AccessTierRow | None" = None   # None = unrenderable by this harness
    editing: "AccessTierRow | None" = None      # None = unrenderable by this harness
    full: "AccessTierRow | None" = None         # None = unrenderable by this harness
    setting_key: str = ""

    def row(self, tier: str) -> "AccessTierRow | None":
        """The row for *tier*, or `None` when unrenderable — an unknown tier collapses here too."""
        if tier == "restricted":
            return self.restricted
        if tier == "editing":
            return self.editing
        if tier == "full":
            return self.full
        return None

    def renders(self, tier: str) -> bool:
        """Can this harness render *tier* at all? (presence of a row)"""
        return self.row(tier) is not None

    def rendered_tiers(self) -> tuple[str, ...]:
        """The tiers this harness can render, least->most permissive (order from `ACCESS_TIERS`)."""
        return tuple(t for t in ACCESS_TIERS if self.renders(t))


@dataclass(frozen=True)
class PersonaSpec:
    """How a persona's alternate endpoint and bearer token are delivered for this harness."""

    # ⚑ That key's VALUE is THREE-STATE: configured / ABSENT (refuse) / present-null (KEYLESS).
    token_var: str = ""              # secret_path key == in-box env var; EMPTY = harness-dynamic
    endpoint_delivery: str = "env"   # "env" | "config_file"; picks the preflight gate in start.py
    wire_api: str = "responses"      # config-file wire; codex dropped "chat" (openai/codex#7782)
    provider_pin: tuple[tuple[str, str], ...] = ()  # setting pins when endpoint active
    # ⚑ A HARNESS-CAPABILITY veto, NARROWED by the present-null model key: env delivery can
    # omit a model, so a present-null SUPPRESSES this; a config-file harness conflict-refuses.
    model_required: bool = False     # harness VETO: error if endpoint set but no model


#: Cap on the provider's own words as echoed back to a user, in CHARACTERS.
_PROVIDER_TEXT_CAP = 300
#: Bytes taken off an error response before capping — a hostile body is never slurped.
_PROVIDER_READ_CAP = 8 * 1024
#: Shortest header value treated as secret-bearing; see `_request_secrets` for why LENGTH.
#: ⚑ EIGHT is where ordinary header values stop being collateral (`gzip` under it,
#: `keep-alive` caught by it) and still below anything issued as a key.
_SECRET_MIN_CHARS = 8
#: How many EMBEDDED JSON documents deep the scrub follows a string value (`_scrub_embedded`).
#: ⚑ One step per proxy hop.  A BOUND, not a measured limit: the input is already capped at
#: `_PROVIDER_READ_CAP`, so hostile work is at most that cap times this depth.
_EMBED_DEPTH = 3
#: How many ORDINARY containers deep the scrub walks one document (`_scrub_decoded`).
#: ⚑ Also a bound, and it exists so the walk's depth is NOT `sys.getrecursionlimit()` — a global
#: any code in the process may retune, and no place to rest a security property.  Orders of
#: magnitude past a real error body.
#: ⚑ IT IS HALF THE STACK, NOT A CORNER OF IT.  A container level costs TWO Python frames before
#: 3.12 (the comprehension takes one of its own until PEP 709 inlines it) and one after, so a full
#: budget peaks at a recursion limit of 508 on the oldest interpreter we support, against a default
#: of 1000.  Stated rather than shrunk because spending it all is NOT a leak: a `RecursionError`
#: out of this walk lands on `_provider_text`'s catch-all and takes `_UNREADABLE`, so a budget set
#: wrong costs the user a message, never an unscrubbed one.  Re-derive the peak from the repo root:
#:     python -c "import sys;sys.path.insert(0,'src')
#:     from kanibako.targets.base import _scrub_decoded as w, _NEST_DEPTH as d
#:     n = 'x'
#:     for _ in range(d): n = {'k': n}
#:     for r in range(50, 2000):
#:         sys.setrecursionlimit(r)
#:         try: w(n, ()); print('peak recursionlimit', r); break
#:         except RecursionError: pass"
_NEST_DEPTH = 250
#: What a secret is replaced BY — and what an over-budget region is replaced by, IN PLACE.
#: ⚑ SAME MARKER FOR BOTH ON PURPOSE: a document we declined to read is a place a secret could
#: be, so it is treated as one.  Replacing it in place, rather than withholding the whole body,
#: is what keeps a bound from costing the user the message that says what went wrong.
_REDACTED = "<redacted>"
# ⚑ STATES THE OUTCOME, NEVER A DIAGNOSIS.  What fired is a MATCH against a request header
# value, and `_request_secrets` is length-keyed, so the match may be an `anthropic-version`
# date.  Telling a user their provider echoed a key back is an accusation this cannot support.
_WITHHELD = "(withheld — the reply still matched a value from the request after scrubbing)"
# ⚑ ITS SIBLING, AND A DIFFERENT FACT — so it does not borrow the sentence above.  Nothing
# matched here; the body was never read, so the scrub's guarantee never applied to it at all.
# Saying "still matched a value from the request" would report a match that never happened.
_UNREADABLE = "(withheld — the reply could not be decoded, so it could not be scrubbed)"


class ProbeResponse(NamedTuple):
    """What `http_probe` saw: an HTTP status (`None` = never reached) and the error text.

    *body* is the provider's own words — normalized to ONE line, capped, and scrubbed of
    every header value the request carried (`_provider_text`).  Empty when there was
    nothing to read; one of two fixed sentences when the text was withheld instead
    (`_WITHHELD`, `_UNREADABLE`).

    🛑 SCRUBBED IS NOT PROVEN CLEAN.  THIS IS THE PUBLISHED CONTRACT FOR *body*, and it is
    on the class so `help(ProbeResponse)` carries it — read it before printing that string
    anywhere.  The scrub matches a credential's OWN CHARACTERS: in plain text, through one
    JSON decode, and through `_EMBED_DEPTH` embedded documents.  A provider that re-encodes
    it otherwise (`%2F`, `&#47;`, base64) defeats the scrub SILENTLY — nothing is withheld
    and this string prints with the credential in it (`_survives`); a credential straddling
    `_PROVIDER_READ_CAP` leaks its head the same way.  An error message for a human, NOT a
    value proven free of secrets.

    ⚑ AND IT IS THE PROVIDER'S MEANING, NOT ITS BYTES: a JSON body is re-serialized, so it
    prints compacted, `1.50` prints as `1.5` and duplicate keys collapse to the last, and a
    region past `_EMBED_DEPTH` or `_NEST_DEPTH` is replaced by the same marker a secret gets
    — so a `<redacted>` here is NOT evidence a secret was there.  Nothing round-trips; do
    not parse this looking for what the provider sent.

    ⚑ ENCODABLE, THOUGH: `_provider_text` guarantees this string survives `str.encode`, so
    printing it to a strict stream cannot raise (`UnicodeEncodeError`).
    """

    status: int | None
    body: str = ""   # 🛑 the contract above governs — read it before you print this


def _request_secrets(headers: Mapping[str, str]) -> tuple[str, ...]:
    """Every caller-supplied header substring that must never survive into printed text.

    ⚑ EVERY VALUE, not just `Authorization`.  `http_probe` is a PUBLISHED plugin helper, so
    a plugin authenticating with `x-api-key` — or any header name nobody here thought of —
    must not depend on this function recognizing it.  The first-party plugins both happen
    to send `Authorization`: that made them safe by luck, not by construction.
    ⚑ A scheme-prefixed value contributes its REMAINDER too, so `Bearer <tok>` redacts on
    the bare token as well as on the whole header.
    ⚑ THE ONLY EXCLUSION IS LENGTH, NEVER A HEADER NAME — a name-keyed exemption list is
    exactly the shape that lets the next credential through under an unforeseen name.  A
    value shorter than `_SECRET_MIN_CHARS` is skipped because an EMPTY one would splice the
    marker between every character of the body.
    ⚑ SO THIS SET IS NOT ALL CREDENTIALS, and a false member is not cheap: the claude
    plugin's `anthropic-version: 2023-06-01` is in it, and an error naming that date can
    trip `_survives` and lose the WHOLE message.  Redaction still wins the trade — but it
    is why `_WITHHELD` reports a MATCH and never accuses the provider of echoing a key.
    ⚑ RETURNED LONGEST-FIRST for `_scrub`, which explains why.
    """
    out: list[str] = []
    for value in headers.values():
        if len(value) >= _SECRET_MIN_CHARS:
            out.append(value)
        _, sep, remainder = value.partition(" ")
        if sep and len(remainder) >= _SECRET_MIN_CHARS:
            out.append(remainder)
    return tuple(sorted(out, key=len, reverse=True))


def _scrub(text: str, secrets: tuple[str, ...]) -> str:
    """*text* with every secret in it replaced by `_REDACTED`.

    ⚑ ORDER-SENSITIVE, and `_request_secrets` owns the order: LONGEST FIRST, because a
    secret landing first that merely PREFIXES another eats the longer one's anchor and
    leaves its tail in cleartext.  Sorting there makes that shape unavailable rather than
    resting a security property on the caller's `dict` insertion order.
    """
    for secret in secrets:
        text = text.replace(secret, _REDACTED)
    return text


def _compact(node: object) -> str:
    r"""*node* re-serialized as ONE compact JSON line.

    ⚑ `ensure_ascii=False` is load-bearing, not cosmetic: the default would re-escape a
    non-ASCII credential into a `\uXXXX` spelling the scrub after this cannot see.
    """
    import json as _json

    return _json.dumps(node, ensure_ascii=False, separators=(",", ":"))


def _scrub_decoded(
    node: object,
    secrets: tuple[str, ...],
    embed: int = _EMBED_DEPTH,
    depth: int = _NEST_DEPTH,
) -> object:
    """A `json.loads` result with every string it carries — KEYS INCLUDED — scrubbed.

    TWO BUDGETS, spent on different axes and neither on the other's.  *embed* buys a
    descent into an EMBEDDED document (`_scrub_embedded`); *depth* buys one ordinary
    container, so a deep tree of dicts and lists is one document and costs no *embed*.
    ⚑ Past EITHER, the region we declined to read is replaced by `_REDACTED` IN PLACE
    and the walk carries on around it — see that constant for why the two agree.
    ⚑ *depth* is what keeps this recursion inside the interpreter's limit BY
    CONSTRUCTION.  A parse returns trees far deeper than this walk may follow — how much
    deeper varies by interpreter, and `_provider_text` says where that floor is — and the
    walk is Python frames, so without the budget a body well inside `_PROVIDER_READ_CAP`
    raises `RecursionError`, which `_provider_text` can only answer by losing the whole
    message.  It once answered by printing the undecoded body instead; see that docstring.
    """
    if depth <= 0:
        return _REDACTED
    if isinstance(node, str):
        return _scrub_embedded(node, secrets, embed, depth)
    if isinstance(node, list):
        return [_scrub_decoded(item, secrets, embed, depth - 1) for item in node]
    if isinstance(node, dict):
        return {
            _scrub_embedded(k, secrets, embed, depth): _scrub_decoded(v, secrets, embed, depth - 1)
            for k, v in node.items()
        }
    return node


def _scrub_embedded(text: str, secrets: tuple[str, ...], embed: int, depth: int) -> str:
    """*text* scrubbed, FOLLOWED into an embedded JSON document when it is one.

    ⚑ A gateway fronting another provider routinely echoes the upstream body as a string
    VALUE, which keeps its own escape layer — `_provider_text`'s defect, one level down.
    ⚑ ONLY A CONTAINER IS FOLLOWED, so a string parsing to a number, `null` or a quoted
    word is left as written and ordinary prose is never rewritten as JSON.
    ⚑ Past *embed* the string is REPLACED, not skipped: a document we declined to read is
    a place a secret could be.  It goes IN PLACE — only the unread document disappears,
    and its scrubbed surroundings still reach the user.
    ⚑ `ValueError`, not `Exception`: that is the ONE failure meaning "not JSON".  A wider
    catch would treat a decode we could not FINISH as plain text and scrub it for raw
    spellings alone, which is exactly the shape `_provider_text`'s decode exists to stop.
    ⚑ *depth* CARRIES ACROSS the hop rather than restarting, because it is the recursion
    budget for the whole walk, not for one document.  Restarting it would multiply by
    `_EMBED_DEPTH`, and a body spending the product fits under `_PROVIDER_READ_CAP`.
    """
    import json as _json

    try:
        inner = _json.loads(text)
    except ValueError:
        inner = None
    if isinstance(inner, (dict, list)):
        if embed <= 0:
            return _REDACTED
        return _compact(_scrub_decoded(inner, secrets, embed - 1, depth - 1))
    return _scrub(text, secrets)


def _survives(text: str, secrets: tuple[str, ...]) -> bool:
    r"""Is any secret still recoverable from *text* once backslash escaping is deleted?

    ⚑ A replace is exact-match, so any encoder can hide a secret by escaping the secret's
    own characters.  `_provider_text` undoes the JSON escape alphabet by PARSING; this
    catches one layer past that — a body escaped twice, or a non-JSON body carrying `\/`
    anyway — and FAILS LOUD, replacing the body with `_WITHHELD` rather than printing it.

    🛑 A GUARD, NOT COVERAGE, AND THE TWO OUTCOMES DIFFER.  What it CATCHES costs the user
    an error message.  What WALKS PAST it — any re-encoding that is not backslash escaping:
    `%2F`, `&#47;`, base64 — is NOT caught and IS PRINTED, credential and all, with nothing
    withheld, because nothing here can tell that anything happened.
    ⚑ Closing such a class takes another DECODE layer ahead of the scrub, as
    `_scrub_embedded` did; widening this guard only enumerates spellings.
    """
    bare = text.replace("\\", "")
    return any(secret in text or secret in bare for secret in secrets)


def _provider_text(raw: bytes, headers: Mapping[str, str]) -> str:
    r"""Normalize an error body into ONE capped, secret-scrubbed line.

    ⚑ THE SCRUB IS THIS FUNCTION'S JOB, NOT A CALLER'S, and that is why it lives beside
    the request: the credentials are in *headers* HERE and nowhere downstream, so this is
    the only place a provider echoing one back can be caught at all.

    ⚑ THE SCRUB RUNS ON THE DECODED BODY, NEVER ON THE WIRE BYTES ALONE.  A replace over
    the wire text sees only a secret's raw spelling, and any ESCAPED rendering of it walks
    straight past — a token containing `/`, echoed by a provider whose encoder writes it
    `\/` (PHP's `json_encode` does by default), printed in full.  Decoding first hands the
    scrub the string the provider MEANT, so the escape alphabet is undone by the parser
    instead of enumerated here, and the re-encoding that follows can only escape text the
    secret has already left.  `_scrub_embedded` carries that move `_EMBED_DEPTH` documents
    inward.  A body that does not parse as JSON has no encoding layer to undo and is
    scrubbed as plain text; `_survives` guards both paths — and what walks past IT is
    printed, so read that docstring before trusting this output.

    🛑 "DOES NOT PARSE" MEANS `ValueError` AND NOTHING ELSE.  A body that IS JSON but
    that the decode could not FINISH has an encoding layer, un-undone, and the raw
    spelling is all the plain-text scrub can see — so it takes `_UNREADABLE` instead, which
    reports THAT fact and not a match that never happened.  This was a leak: the walk over
    a decoded body is Python frames, and a swallowed `RecursionError` printed the wire body
    with a `&`-escaped key legible in it (`_survives` deletes backslashes, so it saw
    `u0026`, not `&`).  `_NEST_DEPTH` now keeps the WALK inside the limit; only the parse
    is inside the `ValueError` guard, so nothing past it can take the "not JSON" arm; and
    the catch-all answers for the rest — the stack already under this call, `_compact`, an
    unforeseen shape, and `json.loads` ITSELF.

    🛑 `json.loads` ITSELF IS THE FOURTH, AND IT IS ORDINARY, NOT EXOTIC.  Before 3.12 the
    decoder's own recursion is charged against `sys.getrecursionlimit()`, so on the oldest
    interpreter kanibako supports a merely DEEP body — 995 levels, under 2 KB, less than a
    quarter of `_PROVIDER_READ_CAP` — dies in the parse and costs the user the whole
    message.  A server CAN serve that.  `json.loads` exposes no depth knob, so the honest
    move is to say it rather than to claim a floor we do not have.  Re-derive it with:

        python -c "import json
        d = 0
        try:
            while True:
                d += 1
                json.loads('[' * d + '1' + ']' * d)
        except RecursionError:
            print('deepest nesting parsed:', d - 1)"

    ⚑ A JSON BODY IS RE-SERIALIZED, NOT EDITED: the result is the provider's MEANING, not
    its bytes.  It prints compacted, `1.50` prints as `1.5` and `1e3` as `1000.0`, and
    duplicate keys collapse to the last — none of it reversible from the printed line.  A
    non-JSON body passes through unchanged apart from the whitespace collapse.

    ⚑ TWO CAPS, AND ONLY ONE IS ORDERED SAFELY.  `_PROVIDER_TEXT_CAP` is applied HERE,
    after the scrub, deliberately — truncating first could leave half a token standing.
    `_PROVIDER_READ_CAP` is not ours to order: `http_probe` cut the body at that many BYTES
    before *raw* arrived, so a secret straddling the cut comes in bisected, matches nothing
    in *secrets*, and its surviving head prints whenever the whitespace collapse pulls it
    inside the character cap.  That cut also truncates mid-JSON, so such a body cannot
    parse and lands on the plain-text path.  Only reading more of a hostile body would
    close it, and that is not a trade this function may make.
    """
    import json as _json

    secrets = _request_secrets(headers)
    text = raw.decode("utf-8", "replace")
    # ⚑ THE `ValueError` GUARD HOLDS THE PARSE AND NOTHING ELSE, by construction rather
    # than by the docstring saying so.  Widen it over the walk or the re-serialization and
    # a `ValueError` out of EITHER reaches the "not JSON" arm and prints a body whose
    # encoding layer was never undone — the exact shape the catch-all exists to stop.
    try:
        decoded = _json.loads(text)
    except ValueError:
        # Not JSON: there is no encoding layer to undo, so the wire text IS the display text.
        pass
    except Exception:
        # We set out to read it and could not.  Never fall through to the raw-spelling
        # scrub — see this function's docstring; the safe default belongs on the CATCH-ALL,
        # so a decode failure nobody foresaw withholds instead of printing.
        return _UNREADABLE
    else:
        try:
            text = _compact(_scrub_decoded(decoded, secrets))
        except Exception:
            return _UNREADABLE   # the same safe default, for the same reason
    # ⚑ Scrub BOTH SIDES of the whitespace collapse.  Collapsing can join two runs into a
    # match that was not there before, and can equally destroy one that was — a header value
    # carrying a double space matches the wire body and not the collapsed body.  This is the
    # only scrub the non-JSON path gets, so it does not get to be order-sensitive.
    text = _scrub(text, secrets)
    text = _scrub(" ".join(text.split()), secrets)
    if _survives(text, secrets):
        return _WITHHELD
    # ⚑ ENCODABLE BY CONSTRUCTION, and this is the boundary that owes it.  `json.loads`
    # turns a `\udXXX` escape into a REAL lone surrogate and `_compact`'s
    # `ensure_ascii=False` — load-bearing, see `_compact` — writes it straight back, so
    # without this the returned string raises `UnicodeEncodeError` in the CALLER's print.
    # Every sink kanibako ships is `sys.stderr`, whose `backslashreplace` handler hides it;
    # `http_probe` is a PUBLISHED helper and a plugin printing to `sys.stdout` gets the
    # traceback instead of the error it asked for.
    # ⚑ AFTER the scrub, never before: a replacement can only DESTROY characters, so it
    # cannot rebuild a secret the scrub already took — and running it first would hand
    # `_survives` a string the scrub never saw.
    text = text.encode("utf-8", "replace").decode("utf-8")
    if len(text) > _PROVIDER_TEXT_CAP:
        text = text[:_PROVIDER_TEXT_CAP].rstrip() + "…"
    return text


def http_probe(
    url: str,
    *,
    headers: dict[str, str],
    body: dict,
    timeout: float,
) -> ProbeResponse:
    """POST *body* as JSON to *url*; return the HTTP status and the provider's error text.

    ⚑ NEVER raises, and never logs the request: *headers* carry credentials.
    ⚑ The returned text is scrubbed of EVERY caller-supplied header value
    (`_provider_text`) — whatever header the caller authenticated with, not just
    `Authorization`.  🛑 That scrub has a documented residue, and the residue REACHES
    PRINTED OUTPUT — see `ProbeResponse.body`, which also says what re-serialization
    did to the provider's words, before printing this anywhere.
    ⚑ The body is cut at `_PROVIDER_READ_CAP` BYTES here, ahead of the scrub, and that
    order is not reorderable — a secret straddling the cut arrives bisected and cannot be
    matched (`_provider_text`).
    """
    import json as _json
    import urllib.error
    import urllib.request

    class _NoRedirects(urllib.request.HTTPRedirectHandler):
        """Refuse every redirect: urllib would re-send the caller's headers cross-origin.

        ⚑ EVERY header, not just `Authorization` — this module treats each one as
        credential-bearing (`_request_secrets`), and naming a single header is the
        defect that let a third-party plugin's `x-api-key` through in the first place.
        """

        def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
            return None

    data = _json.dumps(body).encode("utf-8")
    try:
        req = urllib.request.Request(url, data=data, method="POST")
        for key, value in headers.items():
            req.add_header(key, value)
        req.add_header("Content-Type", "application/json")
        opener = urllib.request.build_opener(_NoRedirects)
        with opener.open(req, timeout=timeout) as resp:  # noqa: S310
            return ProbeResponse(int(resp.status))
    except urllib.error.HTTPError as exc:
        try:
            raw = exc.read(_PROVIDER_READ_CAP)
        except Exception:
            raw = b""
        return ProbeResponse(int(exc.code), _provider_text(raw, headers))
    except Exception:
        # Every transport shape (URLError / timeout / ssl / bad URL); the contract is never-raises.
        return ProbeResponse(None)


class PersonaSettings(NamedTuple):
    """Persona-specific values read back OUT of a rendered harness config."""

    endpoint: str | None   # the alternate base URL (codex base_url / claude ANTHROPIC_BASE_URL)
    model: str | None      # the provider model id, when the config names one
    auth_env: str          # env var the bearer token is exported as (codex env_key)
    # ⚑ MappingProxyType, not a bare `{}`: a mutable default is one object shared by every instance.
    env: Mapping[str, str] = MappingProxyType({})   # passthrough env block (no single-source vars)
    env_dropped: tuple[str, ...] = ()               # NAMES skipped: value was not a string


class PersonaReadOutcome(NamedTuple):
    """The tri-state result of `Target.read_persona_settings`."""

    settings: PersonaSettings | None   # non-None = a usable config was read
    reject_reason: str | None          # non-None = PRESENT but UNUSABLE; BOTH None = no reader


class PersonaProbeVerdict(Enum):
    """The four distinct answers `Target.verify_persona` can give."""

    PASS = "pass"                        # 2xx — the endpoint accepted the token
    REJECTED = "rejected"                # 401/403 — a POSITIVE auth reject
    INCONCLUSIVE = "inconclusive"        # probed, could not decide (blip/ambiguous)
    NOT_APPLICABLE = "not_applicable"    # no probe was attempted, and none will be


# Statuses at which the endpoint POSITIVELY refused the request it was handed.
# ⚑ NOT "the token is bad": neither code says WHICH input was at fault, and a 403 on a
# model the caller's team is not entitled to reads identically to a dead credential.
_REFUSAL_STATUSES = (401, 403)


def _tilde(path: Path) -> str:
    """*path* with the user's home abbreviated to `~` — DISPLAY only, never a resolve."""
    try:
        return f"~/{path.relative_to(Path.home())}"
    except (ValueError, RuntimeError, OSError):
        return str(path)


@dataclass(frozen=True)
class ProbeEvidence:
    """What a persona probe SENT and what came back — the facts a non-PASS verdict rests on.

    Filled in TWO halves: a plugin builds the request half before the call, and
    `probe_outcome` fills *status* / *provider_text* from the answer.
    ⚑ *token_path* is a PATH, never a value, so it carries no secret by construction.
    *provider_text* is different: `http_probe` scrubbed it of every header value the
    request carried, but 🛑 SCRUBBED IS NOT PROVEN CLEAN — `ProbeResponse.body` names the
    residue that scrub does not close, and this record inherits it verbatim.  Read this
    block as an error message for a human, not as a record proven free of secrets.

    ⚑ *model* is what went ON THE WIRE, which is not always what the user configured: a
    harness that resolves a tier alias through an env var records the id it SENT here and
    says where it came from in *model_origin*.
    """

    endpoint: str
    model: str | None = None        # None = the `model` key was deliberately OMITTED
    model_origin: str = ""          # "" = sent exactly as configured
    token_path: Path | None = None  # None = a KEYLESS probe (no credential header at all)
    status: int | None = None       # None = the endpoint was never reached
    # ⚑ The provider's MEANING, not its bytes: scrubbed, capped, and — when the body was
    # JSON — re-serialized compactly by `_provider_text`, never edited in place.
    provider_text: str = ""

    def lines(self, indent: str = "  ") -> tuple[str, ...]:
        """The evidence block: one labeled line per input, then the provider's own words.

        ⚑ The closing sentence is emitted for a REFUSAL status ONLY, and it exists to stop
        the one wrong conclusion this block is here to prevent — that a refusal names the
        token.  For any other status the caller's own reason sentence already says what
        was seen, and a second interpretation would be guessing.
        """
        model = "(omitted)" if not self.model else self.model
        if self.model and self.model_origin:
            model = f"{self.model}  ({self.model_origin})"
        token = (
            "(none — this persona declares the endpoint keyless)"
            if self.token_path is None else _tilde(self.token_path)
        )
        out = [
            f"{indent}{'endpoint':<10}{self.endpoint}",
            f"{indent}{'model':<10}{model}",
            f"{indent}{'token':<10}{token}",
        ]
        if self.provider_text:
            out.append(f"{indent}{'provider:':<10}{self.provider_text}")
        if self.status in _REFUSAL_STATUSES:
            out.append(
                f"{indent}An HTTP {self.status} means the endpoint refused this request — "
                f"it does not say which input was at fault."
            )
        return tuple(out)

    def block(self, indent: str = "  ") -> str:
        """`lines`, joined — the form a printed message interpolates."""
        return "\n".join(self.lines(indent))


class PersonaProbeOutcome(NamedTuple):
    """The result of `Target.verify_persona`: a verdict, a named cause, and its evidence."""

    verdict: PersonaProbeVerdict
    reason: str | None = None   # set for the two non-answer arms; interpolates into a sentence
    # ⚑ REQUIRED for a REJECTED (`rejected` takes it positionally): a refusal a user cannot
    # see the inputs of is the message that sent them to fix a token that was never wrong.
    evidence: "ProbeEvidence | None" = None

    @classmethod
    def passed(cls) -> "PersonaProbeOutcome":
        """2xx: the endpoint accepted the request."""
        return cls(PersonaProbeVerdict.PASS)

    @classmethod
    def rejected(cls, evidence: ProbeEvidence) -> "PersonaProbeOutcome":
        """401/403: the endpoint positively refused the request *evidence* describes."""
        return cls(PersonaProbeVerdict.REJECTED, None, evidence)

    @classmethod
    def inconclusive(
        cls, reason: str, evidence: "ProbeEvidence | None" = None,
    ) -> "PersonaProbeOutcome":
        """The probe ran and could not decide; *reason* says what it saw.

        *evidence* is absent for the one arm that has none — a probe that never got as far
        as a request (a plugin that raised).
        """
        return cls(PersonaProbeVerdict.INCONCLUSIVE, reason, evidence)

    @classmethod
    def not_applicable(cls, reason: str) -> "PersonaProbeOutcome":
        """No probe was attempted; *reason* says why none is possible."""
        return cls(PersonaProbeVerdict.NOT_APPLICABLE, reason)

    def evidence_block(self, indent: str = "  ") -> str:
        """The evidence lines to append to a printed message, newline-led; `""` when none.

        ⚑ Empty unless the endpoint ANSWERED.  A probe that never reached it has no facts
        to lay out, and four lines restating the caller's own inputs is noise, not
        evidence.  THE single renderer for both the launch error and the create warning —
        one shape, so the two can never drift.
        """
        if self.evidence is None or self.evidence.status is None:
            return ""
        return "\n" + self.evidence.block(indent)


def probe_outcome(response: ProbeResponse, sent: ProbeEvidence) -> PersonaProbeOutcome:
    """Map an HTTP probe *response* onto a `PersonaProbeOutcome`; `NOT_APPLICABLE` never comes here.

    *sent* is the request half of the evidence, built by the plugin that made the call;
    the answer half is filled in here so no plugin can forget to.
    """
    evidence = replace(
        sent, status=response.status, provider_text=response.body,
    )
    if response.status is None:
        return PersonaProbeOutcome.inconclusive(
            "the endpoint could not be reached", evidence
        )
    if 200 <= response.status < 300:
        return PersonaProbeOutcome.passed()
    if response.status in _REFUSAL_STATUSES:
        return PersonaProbeOutcome.rejected(evidence)
    return PersonaProbeOutcome.inconclusive(
        f"the endpoint answered HTTP {response.status}, which is neither an accept "
        f"nor an auth reject",
        evidence,
    )


# Statuses meaning "well-formed EXCEPT for a missing `model`"; consulted ONLY by the no-model probe.
# ⚑ An INFERENCE, deliberately narrow — the wire never says "you omitted the model".
_MODEL_REQUIRED_STATUSES = (400, 422)


def probe_outcome_no_model(
    response: ProbeResponse, sent: ProbeEvidence,
) -> PersonaProbeOutcome:
    """`probe_outcome`, for a probe that deliberately OMITTED `model`."""
    if response.status in _MODEL_REQUIRED_STATUSES:
        return PersonaProbeOutcome.not_applicable(
            f"the endpoint requires a model in the request (HTTP {response.status}) and "
            f"the persona names none"
        )
    return probe_outcome(response, sent)


@dataclass(frozen=True)
class Operation:
    """A standalone op fragment (exec/headless), no session mode; spliced after `command`."""

    fragment: tuple[str, ...]


class Cadence(Enum):
    """Credential/config file sync cadence."""

    SYNC = "sync"            # bidirectional, mtime-gated each launch (credentials/token files)
    SEED_ONCE = "seed_once"  # one-way host->project at init, never written back (config files)


@dataclass(frozen=True)
class CredFileSpec:
    """A credential/config file's lifecycle; the filter/merge payload stays a plugin hook."""

    home_rel: str                     # path under the project shell home (".claude/settings.json")
    host_rel: str                     # path under the host home (".claude/settings.json")
    cadence: Cadence = Cadence.SYNC
    mtime_gate: bool = True           # only meaningful for SYNC
    filtered: bool = False            # True -> plugin transform_cred hook runs
    is_dir: bool = False              # DIRECTORY: recursive copy, no mtime gate, no filter


@dataclass(frozen=True)
class PluginDescriptor:
    """Declarative data a plugin exposes via `Target.descriptor`; divergent LOGIC stays in hooks."""

    command: tuple[str, ...]                       # box argv prefix (e.g. ("claude",))
    bindings: tuple[Binding, ...]                  # ALL bound elements; ordered; >=1
    mode: dict[str, tuple[str, ...]]               # INTERACTIVE launch ONLY: {"start", "continue"}
    operations: dict[str, Operation] = field(default_factory=dict)  # standalone ops, no mode
    access_realization: AccessRealization | None = None
    settings: tuple[SettingArg, ...] = ()
    persona: "PersonaSpec | None" = None   # harness-specific persona endpoint/token delivery
                                           # (None = claude-style env + ANTHROPIC_AUTH_TOKEN)
    # ⚑ NO `container_env` FIELD — plugin env is `agent.<agent>.env.<VAR>`, and
    # `agent_defaults.load_descriptor` refuses `container_env:` BY NAME.
    cred_files: tuple[CredFileSpec, ...] = ()
    host_prep: bool = False           # True -> core calls Target.prepare_host before mounts
    init_dirs: tuple[str, ...] = ()   # extra dirs to mkdir in the project home (home-relative)
    auth_share_support: bool = False  # RO CAPABILITY (spec §2d): supports shared credentials?
    vscode_extension: str | None = None  # extension id auto-installed on attach; None = none


def _validate_agent_binary(binary: Path) -> str | None:
    """Return a REASON string when *binary* is an unusable host executable, else `None`.

    ⚑ Deliberately LENIENT: ELF magic is NOT required (shebang wrappers are legitimate).
    """
    try:
        path = Path(binary)
    except TypeError:
        return f"invalid binary path: {binary!r}"

    if not path.exists():
        return f"binary not found at {path}"
    if not path.is_file():
        return f"binary path is not a regular file at {path}"

    try:
        size = path.stat().st_size
    except OSError as e:
        return f"cannot stat binary at {path}: {e}"
    if size == 0:
        return f"binary is empty (0 bytes) at {path}"

    if not os.access(path, os.X_OK):
        return f"binary present but not executable at {path}"

    # Lenient corruption check: leading bytes all NUL is neither a binary nor a shebang wrapper.
    try:
        with open(path, "rb") as fh:
            head = fh.read(4)
        if head and set(head) == {0}:
            return f"binary appears corrupt (leading bytes are all NUL) at {path}"
    except OSError:
        pass

    return None


class Target(ABC):
    """Abstract base class for agent targets — ALL agent knowledge lives in implementations.

    ⚑ The legacy per-METHOD launch hooks (`binary_mounts`, `init_home`, `build_cli_args`,
    `resource_mappings`, `apply_state`) are GONE — the launch is assembled from `descriptor`.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier for this target (e.g. 'claude')."""
        ...

    @property
    @abstractmethod
    def display_name(self) -> str:
        """Human-readable name (e.g. 'Claude Code')."""
        ...

    @abstractmethod
    def detect(self) -> AgentInstall | None:
        """Detect the agent installation on the host; `None` if it is not installed."""
        ...

    @property
    def has_binary(self) -> bool:
        """Whether this target requires a host-installed binary."""
        return True

    @property
    def descriptor(self) -> "PluginDescriptor | None":
        """Declarative plugin descriptor; None ONLY for the built-in no-agent shell."""
        return None

    def check_auth(self) -> bool:
        """Check if the agent is authenticated. Returns True if ok."""
        return True

    def prepare_host(self, install: "AgentInstall", *, auto_auth: bool, data_path: Path) -> None:
        """Plugin-owned pre-launch host preparation, BEFORE mounts are built. Default: no-op.

        ⚑ Implementations MUST NOT crash the launch — log and swallow.
        """
        return None

    def default_common(self) -> dict[str, BindArm]:
        """Declare default AGENT-scope common/caches binds: TERMINAL key -> its whole `BindArm`."""
        return {}

    def default_seeds(self) -> dict[str, BindArm]:
        """Declare default copy-once-at-init seeds: `agent.<agent>.seeded` -> its whole `BindArm`.

        ⚑ ONE DEST SPACE, TWO DELIVERIES (spec §0): a `seeded` entry STAYS A COPY.
        """
        return {}

    def default_envs(self) -> dict[str, str]:
        """Declare default AGENT-scope env VARIABLES: `agent.<agent>.env.<VAR>` -> scalar value.

        ⚑ The ONLY route a plugin has for a STATIC variable; the write-once arbitration
        belongs to `store_collapse.collapse_env`, and there is none here.
        """
        return {}

    def rom_root(self) -> Path | None:
        """Locate this plugin's packaged BIBLE CHAPTER root (`<pkg>/data/rom`); `None` on ANY failure.

        ⚑ Derived from `__package__`, NOT from `name`: `name` is the HARNESS name and only
        happens to match `kanibako.plugins.<name>` for the first-party plugins.
        """
        import importlib.resources
        import sys

        try:
            module = sys.modules[type(self).__module__]
            package = getattr(module, "__package__", None)
            if not package:
                return None
            ref = importlib.resources.files(package).joinpath("data", "rom")
            path = Path(str(ref))
        except Exception:
            return None
        return path if path.is_dir() else None

    def default_category_binds(self) -> CategoryBindDefaults:
        """Declare default AGENT-scope `@`-ref-sourced category binds: TERMINAL key -> `BindArm`.

        ⚑ Every destination must be normalized with `settings_resolve.normalize_bind_dest` —
        the launch floor merge DEDUPES on these keys BEFORE anything parses them.
        ⚑ ONE SHAPE, TWO DELIVERIES: `seeded` and `synced` are COPIES and stay copies.
        ⚑ A retired name-keyed `agent.<agent>.<category>.<name>` key is REFUSED BY NAME at
        `settings_assemble._insert_dotted`; there is no shim.
        ⚑ A plugin's own bible chapter is NOT declared here — see `rom_root`.
        """
        return {}

    def setting_descriptors(self) -> list[TargetSetting]:
        """Declare what runtime settings this target supports, as `TargetSetting` entries."""
        return []

    def generate_agent_config(self) -> AgentConfig:
        """Return a default AgentConfig for this target."""
        from kanibako.settings.agent_config import AgentConfig as _AgentConfig

        return _AgentConfig(name=self.display_name)

    @property
    def default_entrypoint(self) -> str | None:
        """Binary name for container entrypoint. None = use bash."""
        return None

    def has_resumable_session(self, home: Path) -> bool:
        """Report whether this agent has a session to resume under HOST-side *home*.

        ⚑ The SOLE continue-vs-fresh guard: return `False` ONLY on a POSITIVE determination
        that no resumable session exists — a wrong `False` silently drops a conversation.
        """
        return True

    def should_run_setup(self, output: str) -> bool:
        """Check if a launched session's output PROVES the config did NOT take.

        ⚑ BOUNDED: setup already ran ONCE this invocation, so a match only ERRORS — it
        never loops back into setup.
        """
        return False

    @property
    def setup_entrypoint(self) -> str | None:
        """Container entrypoint for the one-time interactive setup; `None` = no setup step."""
        return None

    @property
    def setup_args(self) -> list[str]:
        """Arguments for the one-time setup command (see `setup_entrypoint`)."""
        return []

    @property
    def config_dir_name(self) -> str:
        """Agent config dir relative to home (e.g. '.claude'). Default: '.{name}'."""
        return f".{self.name}"

    def credential_check_path(self, home: Path) -> Path | None:
        """Path to check for credential existence, or None."""
        return None

    def deliver_panel_permissions(
        self, *, config_root: Path, access: str,
    ) -> bool:
        """Persist the box's resolved `access` TIER onto this agent's panel-visible config.

        ⚑ Keyed on the CASCADE-resolved `access`, NEVER the per-launch `-S`/`-A` flags
        (spec §1A's projected-surface exception — the projection OUTLIVES the launch).
        ⚑ An implementation MUST render every tier explicitly and MUST NOT fall through to
        the permissive arm; an unrecognised value here is a BUG and should raise.
        Best-effort: the caller wraps the call. Returns whether a write occurred.
        """
        return False

    def deliver_directive_hook(
        self,
        *,
        config_root: Path,
        access: str,
        model_provider: "CodexModelProvider | None" = None,
    ) -> bool:
        """Seed this agent's SessionStart directive hook + any coupled managed config.

        ⚑ Box-side literals are derived by the PLUGIN from `settings_resolve.GUEST_HOME`,
        deliberately NOT seam parameters; if the in-box workdir ever becomes
        key-configurable, promote an agent-agnostic `box_workdir` parameter HERE.
        Best-effort as `deliver_panel_permissions`. Returns whether a write occurred.
        """
        return False

    def reattach_config_notice(self) -> str | None:
        """A heads-up to print when REATTACHING to an ALREADY-RUNNING box; `None` = no notice.

        ⚑ A reattach does NOT re-deliver config: it is unsafe to rewrite config under an
        app-server that already read it.
        """
        return None

    def read_persona_settings(self, config_dir: Path) -> PersonaReadOutcome:
        """Extract persona values from a rendered harness config in *config_dir*.

        ⚑ FAIL-SOFT: NEVER raises; an unusable config returns a SPECIFIC reject reason,
        not a bare `None`. Pure read — never writes, never reads the token file.
        """
        return PersonaReadOutcome(settings=None, reject_reason=None)

    def verify_persona(
        self,
        endpoint: str,
        token_path: Path | None,
        model: str | None,
        *,
        env: Mapping[str, str] | None = None,
        timeout: float = 5.0,
    ) -> PersonaProbeOutcome:
        """Probe *endpoint*, credential-authed with the token at *token_path* — a minimal real ack.

        ⚑ *token_path* MAY be `None` (a KEYLESS persona): probe it with the credential header
        (`Authorization` for the first-party plugins) OMITTED rather than decline, and NEVER
        substitute a placeholder credential.
        ⚑ *model* MAY be `None`: probe with the field OMITTED, never a placeholder id.
        ⚑ *env* is the PERSONA-STORE passthrough env block — that store entry's own `env:`
        map, less the two single-source vars — and NOTHING ELSE.  🛑 IT IS NOT THE COLLAPSED
        `env` FAMILY the box receives: those slots are folded at `meta.assembly.env` by the
        WHOLE-BOX resolve, which runs AFTER this pre-flight, so a mapping the user set at
        the agent-file (`kanibako agent set <a> env.<VAR>=…`), workset or box rung — or on a
        keyspace-only persona, which has no store block at all — is ABSENT here.  A harness
        whose runtime resolves *model* through one of them (claude reads a tier alias through
        `ANTHROPIC_DEFAULT_<TIER>_MODEL`) MUST resolve it here too, or the probe asks the
        provider a question the box never asks and a valid persona is refused on the answer.
        ⚑ Where the mapping lives OUTSIDE this block the alias still goes out raw and that
        false `REJECTED` survives: a KNOWN UNCLOSED HOLE, not a contract this argument keeps.
        Resolving a mapping the USER wrote is not the substitution the rule above forbids;
        inventing one is.
        ⚑ Contract: NEVER raises; SHORT *timeout*; the token is read TRANSIENTLY for the
        request only — never logged, never persisted, never returned.
        """
        return PersonaProbeOutcome.not_applicable(
            f"the {self.name} harness implements no persona verify probe"
        )

    def invalidate_credentials(self, home: Path) -> None:
        """Remove credential files when switching to distinct auth. Default: no-op."""

    def transform_cred(
        self,
        spec: CredFileSpec,
        src: Path | None,
        dst: Path,
        direction: str,
    ) -> None:
        """Transform a FILTERED credential/config file between host and project.

        ⚑ Called ONLY for specs with `filtered=True`; *direction* is `"in"` (host->project)
        or `"out"`, and *src* is `None` when no source file is available.
        """
        import shutil
        if src is not None and Path(src).is_file():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(src), str(dst))

    def refresh_credentials(self, home: Path) -> None:
        """Refresh agent credentials from host into the project home. Default: no-op."""
        return None

    def writeback_credentials(self, home: Path) -> None:
        """Write back credentials from project home to host. Default: no-op."""
        return None

    def writeback_extra(self, *, project_home: Path, host_home: Path) -> None:
        """Plugin-specific post-session writeback BEYOND `cred_files` specs. Default: no-op.

        ⚑ MUST be defensive — never raise on a malformed or absent file; a clean teardown
        is the contract.
        """
        return None
