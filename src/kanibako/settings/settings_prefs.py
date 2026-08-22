"""``pref.*`` — REQUESTS to set an earlier-resolving key (spec §2h).

A ``pref.<target-key>`` written in a WORKSET or BOX settings file is a REQUEST to
install a value at a key that resolves STRICTLY EARLIER than the requesting
level. It is not a value of its own; it is an instruction consumed during
resolution (spec §2h).

```
pref.system.agent         | <agent name>   SELECTION — "I want to use this agent, by name."
pref.agent.<agent>.<key>  | <value>        CONFIGURATION — a value for that agent, at agent scope.
```

⚑ Prefs are collected BEFORE the merge and installed as two additional cascade
LEVELS, one per pref-legal level, placed immediately BELOW that level's own
partial. §2h's **RECOMPUTE** is then satisfied *a fortiori*. Do NOT instead patch
the EXPANDED snapshot afterwards, beside ``_materialize_box_agent_mirror`` /
``_install_derived_bindings`` — those are legitimate post-expand ``meta.*``
materialisations, a pref is not, because a pref's value is an INPUT to
resolution. That is the DELTA failure mode §2h warns about;
``tests/test_settings/test_settings_launch.py``
``TestPrefRecomputeNotDelta.test_a_key_derived_from_a_prefd_value_updates`` is the
discriminator.

⚑ TERMINATION: no pref may change which files feed the cascade. Two independent
guarantees — the LOCATOR-CLOSURE filter below, and the pref-legal file pair
coming from ``paths._box_settings_files``, a runtime TREEWALK that consults no
settings key at all. The second is what makes :func:`collect_prefs` safe to call
as a targeted PRE-READ before the cascade runs, which is what agent SELECTION
needs.

⚑ ``pref.system.agent`` DOES change a cascade input (it selects
``meta.agent.<agent>.settings``). That is safe, and deliberately excluded from
the closure, because an AGENT file may not carry prefs — so a re-selected agent
file cannot introduce new requests. See :data:`LOCATOR_CLOSURE`.

Placement order, the recompute argument and the termination proof in full:
``llm-docs/kanibako/settings/settings_prefs.py.md``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Collection, Final, Iterable, Sequence

from kanibako.settings.kb_store import StoreValue
from kanibako.settings.keystore import KeyStore
from kanibako.settings.settings_keyspace import (
    is_terminal_category_key,
    is_terminal_category_tail,
    is_valid_agent_segment,
    key_validity,
)
from kanibako.settings.settings_resolve import SettingsError

_log = logging.getLogger(__name__)

#: The top-level table prefs live under, in a settings file.
PREF_ROOT: Final[str] = "pref"

#: The ONLY levels at which a pref may be WRITTEN (spec §2h).
#: ⚑ *"This is what BOUNDS the recursion, so it is a hard rule, not a
#: convenience."*
PREF_LEGAL_LEVELS: Final[tuple[str, ...]] = ("workset", "box")

#: The ALLOWLIST (spec §2h) — a list of ENTRIES, each either one key or a KEY SET
#: written with §0's glob convention (:func:`glob_match`). Nothing else is
#: requestable today.
ALLOWLIST: Final[tuple[str, ...]] = ("system.agent", "agent.*.**")

#: The LOCATOR CLOSURE (spec §2h) — the forbidden-tier arm that is a
#: **TERMINATION guarantee, not tidiness**: a key here relocates a cascade-input
#: settings FILE, so requesting it from a lower level could pull in a different
#: file carrying its own prefs, which could relocate again — unbounded, and able
#: to oscillate between two files pointing at each other. Both members lead to
#: THE BOX SETTINGS FILE: ``workset.boxes`` → ``meta.box.path`` →
#: ``meta.box.settings`` (§2c), and ``workset.kuid`` → the STANDALONE
#: ``meta.box.name`` → the same chain one hop further out.
#:
#: ⚑⚑ ``system.agent`` IS DELIBERATELY EXCLUDED even though
#: ``meta.agent.<agent>.settings`` derives from it (§2d). It is the whole
#: point of the feature, and the termination argument still holds because the
#: agent file may not carry prefs. **A naive derivation of this closure would
#: capture ``system.agent`` and break the headline feature** — read this before
#: implementing the TODO below.
#:
#: **TODO (agreed, not now) — DERIVE this set, do not hand-list it** (spec §2h):
#: a hand-written list rots the moment someone adds a derivation. Read the
#: llm-doc's LOCATOR CLOSURE section first — it carries the shape the derivation
#: must take, why only TWO keys are listed where §2h's sketch lists seven, and
#: the near-miss keys deliberately NOT in the closure.
LOCATOR_CLOSURE: Final[frozenset[str]] = frozenset({
    "workset.boxes",
    "workset.kuid",
})

#: Resolution ORDER of the cascade levels, for the STRUCTURAL forbidden tier
#: (spec §1A). A pref may target only a key resolving STRICTLY EARLIER
#: than the level setting it.
_LEVEL_ORDER: Final[dict[str, int]] = {
    "config": 0,    # L0.1
    "meta": 1,      # L0.2 / L1.x / L4.1 — bootstrap anchors
    "base": 2,      # L2.1
    "system": 3,    # L2.2
    "agent": 4,     # L3.1
    "workset": 5,   # L3.2
    "box": 6,       # L4.2
}


@dataclass(frozen=True)
class PrefRequest:
    """ONE ``pref.<target>: <value>`` request, as read from ONE settings file.

    *value* is carried VERBATIM — including ``None`` (spec §2h). This layer
    performs NO emptiness interpretation of any kind: present-``None``, terminal
    ``""`` and the COPY-disable sentinel all forward untouched.
    """

    target: str
    value: StoreValue
    level: str
    source: Path | None = None

    @property
    def key(self) -> str:
        """The full ``pref.<target>`` key, for messages."""
        return f"{PREF_ROOT}.{self.target}"

    @property
    def where(self) -> str:
        return str(self.source) if self.source is not None else "<settings>"


# ---------------------------------------------------------------------------
# §0 GLOB convention
# ---------------------------------------------------------------------------

def glob_match(pattern: str, key: str) -> bool:
    """Match *key* against a §0 glob *pattern*.

    Convention (spec §0): ``*`` matches exactly ONE segment; ``**`` matches the
    remaining tail at ANY depth. ``**`` is ONE-or-more *by construction*, not by
    rule — the separator is part of the pattern, so a zero-length tail on
    ``agent.*.**`` would yield the malformed ``agent.foo.`` (trailing dot).
    """
    pat = pattern.split(".")
    seg = key.split(".")
    # ⚑ A key with an EMPTY segment is not a key (§0), and this is where the
    # "one-or-more BY CONSTRUCTION" argument bites: without this guard
    # ``agent.*.**`` would MATCH the malformed ``agent.claude.``.
    if any(s == "" for s in seg):
        return False
    for i, token in enumerate(pat):
        if token == "**":
            # The tail: one-or-more remaining segments.
            return len(seg) > i
        if i >= len(seg):
            return False
        if token != "*" and token != seg[i]:
            return False
    return len(seg) == len(pat)


# ---------------------------------------------------------------------------
# Collection
# ---------------------------------------------------------------------------

def _flatten_pref_node(
    node: KeyStore, prefix: tuple[str, ...], *, level: str, path: Path | None,
) -> list[PrefRequest]:
    """Walk a parsed ``pref`` subtree into flat requests.

    ⚑ Flattening walks NESTED tables ONLY. A leaf whose own segment contains a
    dot (``pref: {"system.agent": x}``) is an ERROR, not a second accepted
    spelling: a bind-shaped value spelled the dotted way would stay a raw ``list``
    and never become a :class:`~kanibako.settings.kb_store.Bind`, so one request
    would behave differently depending on how it was spelled. One form, enforced
    (§0 convention 0).

    ⚑⚑ **THE WALK STOPS AT A TERMINAL DEST-KEYED CATEGORY** — ``<scope>.masks``
    and ``<scope>.bindings.{ro,rw}``
    (:func:`~kanibako.settings.settings_keyspace.is_terminal_category_tail`). Those
    keys' VALUES are maps keyed by box DESTINATION, and a destination is DATA, not
    a key segment (spec §2a), so descending would manufacture a target that is not
    a key. The arm ITSELF is the request: one :class:`PrefRequest` carrying the
    WHOLE map, which ``settings_merge`` then merges PER-ENTRY across levels. That
    is what makes the spec's per-entry suppression spelling
    ``pref.<scope>.bindings.ro: {<dest>: null}`` work.

    The dotted-key raise is UNCHANGED for every other node: only these terminal
    keys stop the walk, and they stop it before their data keys are ever read.
    """
    out: list[PrefRequest] = []
    for name in dict.keys(node):
        if "." in name:
            raise SettingsError(
                f"{PREF_ROOT}.{'.'.join((*prefix, name))} in the {level} settings "
                f"file {path if path is not None else '<settings>'} uses a DOTTED "
                f"key inside the 'pref:' table. A pref is written as a NESTED "
                f"table (pref: {{system: {{agent: goose}}}}), never as a dotted "
                f"literal: a bind-shaped value spelled the dotted way is never "
                f"parsed as a binding, so the two spellings would behave "
                f"differently (spec §2h / §0)."
            )
        value = dict.__getitem__(node, name)
        child = (*prefix, name)
        if isinstance(value, KeyStore) and not is_terminal_category_tail(child):
            out.extend(_flatten_pref_node(value, child, level=level, path=path))
            continue
        # A LEAF — including present-None, "" and the COPY-disable sentinel — or
        # a TERMINAL dest-keyed category node, carried WHOLE (see above).
        out.append(
            PrefRequest(
                target=".".join(child), value=value, level=level, source=path,
            )
        )
    return out


def prefs_from_partial(
    partial: KeyStore, *, level: str, path: Path | None = None,
) -> list[PrefRequest]:
    """Extract the requests from ONE already-parsed level partial."""
    node = dict.get(partial, PREF_ROOT, None)
    if not isinstance(node, KeyStore):
        return []
    return _flatten_pref_node(node, (), level=level, path=path)


def collect_prefs(
    workset_path: Path | None, box_path: Path | None,
) -> list[PrefRequest]:
    """Read the two pref-legal settings files and return their requests, IN
    APPLICATION ORDER — workset (L3.2) first, box (L4.2) second.

    Agent-INDEPENDENT by design: it needs only the two paths, both of which the
    launch path holds BEFORE it resolves the agent. That is what lets agent
    SELECTION consult ``pref.system.agent`` without a resolution loop.

    Parses through ``settings_assemble._file_partial`` — the SAME parse the
    cascade uses — so a bind-shaped pref value arrives as a
    :class:`~kanibako.settings.kb_store.Bind`, exactly as it would at its target
    key. Re-reading the file is deliberate.

    ⚑⚑ THE ``pref:`` TABLE ONLY, and the narrowing is load-bearing. This is the
    SECOND reader of the same file, and the two must agree about what the file
    contains: ``assemble_levels`` runs every raw doc through
    ``_drop_upward_scopes`` first, which strips the top-level ``meta:`` table.
    A workset root's identity is on-disk metadata, NOT a key — it lives in
    ``registry.yaml`` and is read raw by ``read_workset_identity`` — but 1.6.0/1.7.x
    kept it in this file, and parsing the whole document here materialised that table
    into a ``KeyStore`` as ``meta.workset.created`` / ``.projects``, neither of which
    the keyspace declares (spec §0 declares ``meta.workset.{path,name,settings}``).
    Nothing read
    the result — the extractor below keeps only ``pref.*`` — but a filter that
    guards ONE reader of a file guards only that reader. Restricting the parse to
    the one table this function consumes removes the disagreement at the source
    instead of chasing it downstream, and is strictly narrower than the drop the
    cascade applies.
    """
    from kanibako.settings.config_io import load_doc
    from kanibako.settings.settings_assemble import _file_partial

    out: list[PrefRequest] = []
    for level, path in zip(PREF_LEGAL_LEVELS, (workset_path, box_path)):
        if path is None:
            continue
        raw = load_doc(path)
        table = raw.get(PREF_ROOT) if isinstance(raw, dict) else None
        if table is None:
            continue
        out.extend(
            prefs_from_partial(
                _file_partial({PREF_ROOT: table}), level=level, path=path,
            )
        )
    return out


def refuse_pref_table(raw: Any, *, level: str, path: Path | None) -> Any:
    """Drop a top-level ``pref:`` table from a file where a pref is ILLEGAL.

    Prefs are legal in WORKSET and BOX files only (spec §2h). A ``pref:`` table in
    a base / system / agent file has NO equivalent there and is DROPPED with a
    warning naming the file — never silently relocated to a legal level, and never
    read as a value. That is the SAME treatment ``_drop_upward_scopes`` gives the
    sibling fault. The HARD refusal §2h calls for lives at the write site
    (``config set pref.*`` at these scopes RAISES).

    Returns a shallow copy without the table; never mutates *raw*.
    """
    if not isinstance(raw, dict) or PREF_ROOT not in raw:
        return raw
    _log.warning(
        "Dropping top-level 'pref' table from %s settings file %s: a pref is a "
        "REQUEST and may be written ONLY in a workset or box settings file "
        "(spec §2h) — that restriction is what bounds the resolution recursion. "
        "The requests are ignored.",
        level, str(path) if path is not None else "<settings>",
    )
    return {k: v for k, v in raw.items() if k != PREF_ROOT}


# ---------------------------------------------------------------------------
# The THREE INDEPENDENT FILTERS (spec §2h)
# ---------------------------------------------------------------------------

def key_reason(target: str, *, valid_agents: Collection[str]) -> str | None:
    """FILTER 1 — is the target a VALID key? (spec §2h)

    ⚑ VALIDITY, not EXISTENCE. ``agent.claude.env.BOOOOOO`` is legal: a new name
    inside a parametric family is exactly what a user may want to add. An existence
    test would permit only modifying keys that already hold a value.

    ⚑⚑ NO BIND-SHAPED CATEGORY IS SUCH A FAMILY ANY MORE (this docstring twice
    said otherwise — see the llm-doc). Only the BARE terminal key
    (``agent.claude.common``) is a valid pref target; the destinations live inside
    its value, and ``agent.claude.common.<name>`` is REFUSED by
    :func:`key_validity`, correctly. The families that DO still carry a free
    ``<name>`` are ``env.<VAR>``, ``secret_path.<VAR>`` and the agent
    discriminator itself.
    """
    return key_validity(
        target,
        valid_agents=valid_agents,
        agent_leaves=getattr(valid_agents, "leaves", None),
    )


def allowlist_reason(
    target: str,
    *,
    valid_agents: Collection[str],
    allowlist: Sequence[str] = ALLOWLIST,
) -> str | None:
    """FILTER 2 — is the target requestable IN PRINCIPLE? (spec §2h)

    Membership alone is NOT sufficient (filter 3 still applies). The agent
    segment of ``agent.*.**`` is INVALID unless it names a valid agent or
    ``default`` — and the test is *is it a VALID agent*, NOT *is it the ACTIVE
    agent*, so pre-configuring an agent you may switch to is legal.
    """
    for pattern in allowlist:
        if not glob_match(pattern, target):
            continue
        if pattern == "agent.*.**":
            name = target.split(".")[1]
            if not is_valid_agent_segment(name, valid_agents):
                if getattr(valid_agents, "discovery_failed", False):
                    # ⚑ An ENVIRONMENT fault, not a user mistake. Reporting
                    # "'claude' is not a valid agent" when the plugin registry
                    # could not be read sends the user to fix a correct name.
                    return (
                        f"agent DISCOVERY FAILED, so '{name}' could not be "
                        f"validated. This is an environment fault, not a "
                        f"problem with the name: the plugin registry could not "
                        f"be read. Check the kanibako install (run 'kanibako "
                        f"system diagnose'); the request itself may be fine"
                    )
                known = ", ".join(sorted({*valid_agents, "default"}))
                return (
                    f"it names agent '{name}', which is not a valid agent "
                    f"(valid: {known}). A pref MAY pre-configure an agent this "
                    f"box is not running, but not an unknown one (spec §2h)"
                )
        return None
    scope = target.split(".", 1)[0]
    base = (
        "it is not requestable: only 'system.agent' and "
        "'agent.<agent>.<key>' may be requested (spec §2h allowlist)"
    )
    # Only suggest a direct set where one is actually possible: ``meta.*`` is RO by
    # contract, ``config.*`` is hand-edited in the bootstrap file, ``pref.*`` is not
    # a value scope at all, and a YAML-only key (the bind-shaped categories,
    # ``masks``) has no CLI write route. Each would send the user to a scope that
    # does not exist or a command that refuses. ONE predicate answers it for both
    # message sites, deferred-imported to keep this module free of a module-scope
    # edge back to the key registry.
    from kanibako.settings.config_keys import has_no_cli_write_route

    if scope in ("system", "agent", "workset", "box") and not has_no_cli_write_route(
        target
    ):
        return f"{base}. Set '{target}' directly at the {scope} scope instead"
    return base


def forbidden_tier_reason(target: str, *, level: str) -> str | None:
    """FILTER 3 — is the target barred by a forbidden TIER? (spec §2h)

    Returns a REASON string, never a bool: §2h requires the error to say WHY.

    Three arms, checked in the spec's order:

    * **Structural** — the target must resolve STRICTLY EARLIER than the level
      setting it. (A later-resolving key needs no pref: set it directly.)
    * **Categorical** — never ``meta.*``, ``config.*`` or ``pref.*`` itself.
      ⚑ This does NOT stop meta VALUES from changing —
      ``meta.box.auth.workset_path`` changes because ``system.agent`` changed,
      which is the entire point. Only DIRECT targeting is barred.
    * **Locator closure** — see :data:`LOCATOR_CLOSURE`.
    """
    head = target.split(".", 1)[0]

    # STRUCTURAL.
    target_rank = _LEVEL_ORDER.get(head)
    level_rank = _LEVEL_ORDER.get(level)
    if target_rank is not None and level_rank is not None:
        if target_rank >= level_rank:
            return (
                f"it targets '{target}', which resolves at or after the {level} "
                f"level. A later-resolving key needs no pref — set it directly "
                f"(spec §2h structural tier)"
            )

    # CATEGORICAL.
    if head in ("meta", "config", PREF_ROOT):
        why = {
            "meta": "meta.* is read-only by contract, so a pref targeting it "
                    "would be a backdoor around RO",
            "config": "config.* is the bootstrap foundation",
            PREF_ROOT: "a request-of-a-request has no termination argument",
        }[head]
        return (
            f"it targets '{target}', and meta.* / config.* / pref.* may never be "
            f"requested (spec §2h categorical tier): {why}"
        )

    # LOCATOR CLOSURE.
    if target in LOCATOR_CLOSURE:
        return (
            f"it targets '{target}', which locates a cascade-input settings file "
            f"(workset.boxes -> meta.box.path -> meta.box.settings). Requesting "
            f"it from a lower level could relocate the very file the request came "
            f"from, so it is barred (spec §2h locator closure). Setting it in a "
            f"workset FILE is still allowed"
        )

    return None


def validate_pref(
    req: PrefRequest,
    *,
    valid_agents: Collection[str],
    allowlist: Sequence[str] = ALLOWLIST,
) -> str | None:
    """Run all THREE filters; return ONE joined reason, or ``None`` to accept.

    ⚑ ALL failing filters are reported, in filter order — not just the first. The
    filters are INDEPENDENT (§2h) and the decision is their conjunction, so
    reporting every failure is faithful; reporting only the first would make
    message quality hostage to the validator's SUPPORTING-surface completeness.
    """
    reasons: list[str] = []
    k = key_reason(req.target, valid_agents=valid_agents)
    if k is not None:
        reasons.append(f"its target is not a declared key — {k}")
    a = allowlist_reason(
        req.target, valid_agents=valid_agents, allowlist=allowlist,
    )
    if a is not None:
        reasons.append(a)
    f = forbidden_tier_reason(req.target, level=req.level)
    if f is not None:
        reasons.append(f)
    if not reasons:
        return None
    return " — and ".join(reasons)


# ---------------------------------------------------------------------------
# Installation
# ---------------------------------------------------------------------------

def pref_overlay(requests: Iterable[PrefRequest]) -> KeyStore:
    """Build the cascade-level overlay installing *requests* at their targets.

    ⚑ VALUES ARE INSTALLED **VERBATIM, INCLUDING ``None``** (spec §2h).
    ``if value is None: continue`` is the most natural guard to write here and it
    silently implements the REJECTED reading ("no request"), deleting a box's ONLY
    suppression channel with no error and no visible diff. There is deliberately no
    such guard, and ``test_settings_launch.py``
    ``TestPrefNullSuppression.test_a_null_pref_suppresses_an_inherited_agent_bind``
    reddens if one appears.

    A present-``None`` lands on the target key and is then classified by the
    ORDINARY present-``None`` rule at the TARGET's path (``settings_merge``):
    OMIT for a bind / category / masks leaf, KEPT ``None`` for a scalar leaf.
    This layer classifies nothing.
    """
    overlay = KeyStore()
    for req in requests:
        overlay.insert_segments(req.target.split("."), req.value)
    return overlay


def apply_prefs(
    requests: Sequence[PrefRequest],
    *,
    valid_agents: "Collection[str] | None" = None,
    allowlist: Sequence[str] = ALLOWLIST,
) -> tuple[KeyStore, KeyStore]:
    """Validate every request and build ``(workset_overlay, box_overlay)``.

    RAISES :class:`~kanibako.settings.settings_resolve.SettingsError` on the FIRST
    invalid request, naming the key, the LEVEL, the FILE and the REASON (spec §2h):
    the launch FAILS rather than proceeding with a partially-applied request, and
    never a silent skip. Only the first offender is reported —
    fix-one-then-see-the-next, as every other config error in this codebase.
    """
    # ⚑ Discovery is reached ONLY when a request actually names ``agent.*``, and
    # the test is ``is None`` — NOT falsiness. An EMPTY ``AgentNames`` (a box with
    # no agent plugins installed) is falsy, so a truthiness test would discard a
    # caller's deliberate empty set and silently re-discover.
    if valid_agents is None:
        valid_agents = (
            default_valid_agents() if _needs_agent_discovery(requests)
            else AgentNames(())
        )

    ws: list[PrefRequest] = []
    box: list[PrefRequest] = []
    for req in requests:
        why = validate_pref(req, valid_agents=valid_agents, allowlist=allowlist)
        if why is not None:
            raise SettingsError(
                f"{req.key} at the {req.level} level ({req.where}) was refused: "
                f"{why}. The launch is stopped rather than proceeding with a "
                f"partially-applied request (spec §2h)."
            )
        if req.level not in PREF_LEGAL_LEVELS:
            # Unreachable from the collector (it labels by FILE), but a caller
            # constructing requests by hand must not be able to smuggle in a
            # level where a pref is illegal — that is the recursion bound.
            raise SettingsError(
                f"{req.key} carries level {req.level!r}, but a pref is legal "
                f"only at {' / '.join(PREF_LEGAL_LEVELS)} (spec §2h)."
            )
        if req.level == "box":
            box.append(req)
        else:
            ws.append(req)
    return pref_overlay(ws), pref_overlay(box)


# ---------------------------------------------------------------------------
# Suppliers / helpers for consumers
# ---------------------------------------------------------------------------

#: Process-scoped memo for plugin discovery, which walks entry points, a module
#: namespace and two plugin DIRECTORIES. Reset via :func:`reset_discovery_cache`.
_DISCOVERY: "dict[str, AgentNames]" = {}


def reset_discovery_cache() -> None:
    """Clear the process-scoped agent-discovery memo (test seam)."""
    _DISCOVERY.clear()


class AgentNames(Collection[str]):
    """The ``valid_agents`` collection: discovered HARNESSES + any persona NODE
    built on one.

    A ``Collection[str]`` rather than a bare ``frozenset`` because the valid set is
    not enumerable: the agent tier is discriminated by NODE (``navigator℘claude``)
    and persona nodes are user-created, so membership is a PREDICATE while
    iteration yields the finite harness list an error message should name.

    ⚑ Membership is deliberately a VALIDITY test, not an EXISTENCE test: §2h lets
    a pref pre-configure an agent you may switch to, so requiring the persona's
    store dir to already exist would be the same existence error the spec rejects
    for keys.
    """

    def __init__(
        self,
        discovered: Collection[str],
        *,
        leaves: "Collection[str] | None" = None,
        discovery_failed: bool = False,
    ) -> None:
        self._discovered = frozenset(discovered)
        #: PLUGIN-declared agent keys, unioned over the core §2d contract by the
        #: validator (§0 "Agent specifics are PLUGIN-declared").
        self.leaves = frozenset(leaves or ())
        #: ⚑ Discovery FAILED (an environment fault), as distinct from "no agents
        #: are installed". Without this an unreadable plugin dir reports *"'claude'
        #: is not a valid agent"* — blaming the user's spelling for a broken box.
        self.discovery_failed = discovery_failed

    def __contains__(self, item: object) -> bool:
        if not isinstance(item, str):
            return False
        if item in self._discovered:
            return True
        from kanibako.agent_ref import canonicalize_agent_ref, harness_of
        from kanibako.errors import ConfigError

        try:
            node = canonicalize_agent_ref(item)
        except ConfigError:
            return False
        return harness_of(node) in self._discovered

    def __iter__(self):
        return iter(sorted(self._discovered))

    def __len__(self) -> int:
        return len(self._discovered)

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"AgentNames({sorted(self._discovered)!r})"


def default_valid_agents() -> AgentNames:
    """The production ``valid_agents`` supplier — every DISCOVERED agent, plus
    the agent keys those plugins DECLARE.

    MEMOIZED for the process (:data:`_DISCOVERY`) and reached only when a request
    actually names ``agent.*`` (:func:`_needs_agent_discovery`), so laziness is
    enforced by the call site, not just asserted. A discovery FAILURE is recorded
    on the result rather than swallowed: an environment fault must not be reported
    as a bad agent name.
    """
    cached = _DISCOVERY.get("default")
    if cached is not None:
        return cached
    from kanibako.targets import discover_targets

    try:
        targets = discover_targets()
        leaves: set[str] = set()
        for target_cls in targets.values():
            try:
                # ``discover_targets`` yields CLASSES; descriptors are declared
                # per-instance, so instantiate to read them. A plugin whose
                # constructor or descriptor list raises must not break config
                # validation — it simply contributes no extra leaves.
                leaves.update(d.key for d in target_cls().setting_descriptors())
            except Exception:  # pragma: no cover - a plugin must not break config
                _log.debug(
                    "setting_descriptors() failed for a target", exc_info=True,
                )
        result = AgentNames(targets.keys(), leaves=leaves)
    except Exception:
        _log.debug("agent discovery failed while validating a pref", exc_info=True)
        result = AgentNames((), discovery_failed=True)
    _DISCOVERY["default"] = result
    return result


def _needs_agent_discovery(requests: Sequence[PrefRequest]) -> bool:
    """Does validating *requests* require knowing which agents exist?

    Only an agent-scope target does. ``pref.system.agent`` does NOT — its VALUE
    names an agent, but §2h validates the target key, not the value (and a
    not-yet-installed agent name is legal there, see :func:`allowlist_reason`).
    """
    return any(
        r.target.startswith("agent.") or r.target.startswith("meta.agent.")
        for r in requests
    )


def pref_value(
    requests: Sequence[PrefRequest], target: str,
) -> StoreValue | None:
    """The effective REQUEST for *target*, or ``None`` when none was made.

    Later requests win (box after workset), matching the overlay precedence.
    The read helper agent SELECTION uses: ``pref_value(prefs, "system.agent")``.

    ⚑ A present-``None`` request is indistinguishable from "no request" through
    this helper's return type. That is deliberate for the ``system.agent`` case —
    ``pref.system.agent: null`` MEANS the NO-AGENT box (§2b), which is
    the same outcome as no agent being selected. A caller needing the
    distinction should use :func:`pref_request_for`.
    """
    req = pref_request_for(requests, target)
    return None if req is None else req.value


def pref_request_for(
    requests: Sequence[PrefRequest], target: str,
) -> PrefRequest | None:
    """The winning :class:`PrefRequest` for *target* (last wins), else ``None``."""
    winner: PrefRequest | None = None
    for req in requests:
        if req.target == target:
            winner = req
    return winner


def pref_entry_keys(req: PrefRequest) -> tuple[str, ...]:
    """Every DECLARATION-ENTRY key *req* can account for.

    A settings ENTRY is identified downstream (collision errors,
    ``binding_derivations.*``) by ``<decl-scope>.<category>.<dest>``. For most
    targets that string IS the pref target, because ``<VAR>`` is a key SEGMENT. For
    the SEVEN terminal dest-keyed categories (the six bind-shaped ones plus
    ``masks``) it is not: the target stops at the category and the destinations
    live INSIDE the value, so one request accounts for one entry key PER
    DESTINATION IT DECLARES.

    ⚑⚑ **THE DESTINATIONS ARE READ FROM THE REQUEST'S OWN VALUE, not derived by
    trimming the entry key.** A bare prefix test (``key.startswith(target + ".")``)
    is the tempting one-liner and it MISATTRIBUTES — two prefs may target one
    category at different levels while declaring DIFFERENT destinations, and the
    entry at a given dest may not have come from a pref at all. Containment
    answers both.

    ⚑ A DECLARED-``None`` destination is EXCLUDED. Present-``None`` is the
    per-entry suppression spelling (§2h / §6e): it removes the entry rather than
    installing one, so a surviving entry at that dest is somebody else's.

    ⚑ The terminal-category test gates on the KEYSPACE
    (:func:`is_terminal_category_key`), not on "the value happens to be a dict",
    and on the WHOLE key rather than a suffix — both keep this from manufacturing
    per-destination strings that are not keys. A terminal target whose value is NOT
    a map yields the bare target, which is what the adapter's own error names.
    """
    if not is_terminal_category_key(req.target):
        return (req.target,)
    value = req.value
    if not isinstance(value, dict):
        return (req.target,)
    return tuple(
        f"{req.target}.{dest}"
        for dest in dict.keys(value)
        if dict.__getitem__(value, dest) is not None
    )


def pref_origin(
    target_key: str, requests: Sequence[PrefRequest],
) -> PrefRequest | None:
    """The request that INSTALLED *target_key*, for error enrichment.

    A collision error identifies an entry by the DECLARATION KEY plus that entry's
    DEST (``agent.claude.common.~/newthing``) — an identifier a user who wrote
    ``pref.agent.claude.common`` never wrote and cannot write. Matching is
    therefore containment in :func:`pref_entry_keys`; last request wins, matching
    the overlay precedence (box after workset).

    ⚑ :func:`pref_request_for` is deliberately NOT reused here. Its contract is
    exact target equality, the read that agent SELECTION depends on
    (``pref_value(prefs, "system.agent")``), and widening it so this diagnostic
    could reach the dest-keyed categories would silently change that read. Two
    questions, two functions.
    """
    winner: PrefRequest | None = None
    for req in requests:
        if target_key in pref_entry_keys(req):
            winner = req
    return winner
