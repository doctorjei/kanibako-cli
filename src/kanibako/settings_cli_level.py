"""The §1A **CLI LEVEL** — the one builder and the one guard (P8).

Spec §1A L320-338: *"The COMMAND LINE is its OWN LEVEL — the highest, above
everything … It is NOT a cascade scope and NOT a ``pref`` (§2h): it is a separate
input level that outranks every settings file AND every pref. **This is a GENERAL
rule, not a carve-out for particular flags** — ANY flag that shadows a key
overrides that key for the launch."*

P7 built the first instance of that level for exactly one key (``system.agent``,
the resolved agent selection). P8 generalises it: this module owns the FLAG→KEY
table, builds the level, and guards it — so the level is ONE mechanism rather
than N checks scattered across the launch.

What the level IS, and is not
-----------------------------
* **EPHEMERAL, always** (spec §1A). A flag applies to ONE launch and NEVER
  mutates a stored value. Nothing here is written to any settings file; the level
  exists only as the top entry of one merge.
* **No recompute** (unlike prefs, §2h). CLI values are known BEFORE resolution
  starts, so they seed it as the highest-precedence input and resolve once. A flag
  cannot pull in a new file carrying new flags, so the termination argument prefs
  need does not arise.
* **Not a scope.** The level carries keys of ANY scope; it is not subject to §0's
  directional (own-scope) enforcement, which governs what a settings FILE may
  contribute.

Why the guard exists (spec §1A L335-338)
----------------------------------------
*"Because the CLI is not a pref, the §2h forbidden tiers do NOT automatically
cover it, so a flag that could set a LOCATOR-class value (one that relocates a
cascade-input file) needs its OWN guard, or the standing guarantee that CLI values
are applied only as an initial-value overlay that never triggers a re-read."*

BOTH halves hold here, and the explicit guard is implemented anyway:

* the **standing guarantee** is true today — the cascade's input FILE paths come
  from ``paths.box_workset_settings_paths``, a runtime treewalk that consults no
  settings key, so a CLI value cannot relocate a cascade input and cannot trigger
  a re-read;
* the **explicit guard** is still written, because that guarantee is an invariant
  of code living elsewhere, and because a locator-class CLI value would corrupt
  derived PATHS even without a re-read: a CLI ``workset.boxes`` re-points
  ``meta.box.path`` → the ``box.bindings.rw.home`` host_src → the §2c
  mount-over-the-box-home catastrophe, silently.

⚑ ``system.agent`` is deliberately PERMITTED even though it selects a
cascade-input file (``meta.agent.<agent>.settings``). It is excluded from
``settings_prefs.LOCATOR_CLOSURE`` for the reason recorded there — an agent file
may not carry prefs, so re-selecting one introduces no new requests — and the same
argument covers the CLI level. It is the whole point of the feature; a guard that
refused it would break P7.

PURE: no I/O and no plugin import at module load, like
:mod:`kanibako.settings_keyspace`. The set of valid agent names is injected.
"""

from __future__ import annotations

from typing import Collection, Final, Mapping

from kanibako.settings_keyspace import key_validity
from kanibako.settings_prefs import LOCATOR_CLOSURE
from kanibako.settings_resolve import SettingsError

#: The key naming the agent a box runs (spec §2g L1187) — re-exported from the
#: selection seam's spelling so the level and the selection cannot drift.
SELECTION_KEY: Final[str] = "system.agent"

#: The DECLARED flag→key table (spec §1A L323-326). **WIRED ENTRIES ONLY.**
#:
#: ⚑ The spec's enumeration also names ``--image`` (``box.image``), ``-S``/``-A``
#: (``auto_approve``) and ``--share-images`` (``box.share_images``). Those are NOT
#: listed here, because listing an unwired entry would read as a contract this
#: module honours — the same reason ``settings_keyspace`` deleted its unused
#: ``CATEGORY_SCOPES`` rather than keeping it. Each is blocked on something real,
#: recorded in the P8 plan:
#:
#: * ``--image`` / ``--share-images`` — ``box.image`` / ``box.share_images`` are
#:   declared KEYS but have no keyspace CONSUMER: they are read off the legacy flat
#:   loader (``config.load_merged_config`` → ``KanibakoConfig``), and the image is
#:   needed BEFORE the agent is resolved, i.e. before any launch snapshot exists.
#:   Adding the level entry without migrating the consumer would install a key
#:   nothing reads.
#: * ``-S``/``-A`` — ``auto_approve`` is read once and consumed TWICE: by the
#:   ephemeral safe-bypass argv, and by ``deliver_panel_permissions`` /
#:   ``deliver_directive_hook``, which WRITE it onto the box's own persisted agent
#:   config surface. Installing the flag at this level would make an ephemeral flag
#:   mutate a stored value — exactly what §1A forbids.
#:
#: The value is a display TEMPLATE (``<agent>`` = the active discriminator), used
#: by tests and by humans; the builder below is the executable form.
CLI_SHADOWED_KEYS: Final[Mapping[str, str]] = {
    "--agent": SELECTION_KEY,
    "-M/--model": "agent.<agent>.model",
    "-N/-C/-R": "agent.<agent>.continue_mode",
}

#: Namespaces a CLI value may never target (spec §2h's CATEGORICAL tier, which the
#: CLI does not inherit and therefore restates):
#:
#: * ``meta`` — RO by contract (§0); a CLI set would be a backdoor around RO.
#: * ``config`` — the Layer-1 foundation, resolved BEFORE the cascade this level
#:   splices into, so a value here could not take effect and must not pretend to.
#: * ``pref`` — a REQUEST is not a value; a CLI request-of-a-request has no
#:   termination argument.
_FORBIDDEN_HEADS: Final[frozenset[str]] = frozenset({"meta", "config", "pref"})


def build_cli_level(
    *,
    selection: "Mapping[str, object] | None" = None,
    active_agent: "str | None" = None,
    model: "str | None" = None,
    new_session: bool = False,
    continue_session: bool = False,
    resume: bool = False,
) -> "dict[str, object] | None":
    """Build the §1A CLI level for one launch, or ``None`` when it is empty.

    *selection* is P7's resolved-agent level (``{"system.agent": node}`` from
    :attr:`kanibako.agent_select.AgentSelection.selection_level`, or ``None`` for a
    NO-AGENT box). It is carried through VERBATIM: it is installed on EVERY resolve,
    whichever of its three sources won, so ``@system.agent`` equals the node that
    actually runs (see :mod:`kanibako.agent_select` for why that is load-bearing).

    *active_agent* is the discriminator the agent-scope keys are spelled against.
    The agent-scope entries are emitted ONLY when it is truthy — a NO-AGENT /
    ``--entrypoint`` launch resolves against the ``"general"`` template slot, which
    is not an agent, so a flag there is simply not applicable and is dropped rather
    than fabricating ``agent.general.*``.

    ⚑ **The agent-scope spelling is ``agent.<active>.<leaf>``, never
    ``agent.default.<leaf>``.** ``effective_behavior`` performs the §2d L368
    active-over-default pick AFTER the cascade merge, so a value at
    ``agent.default.<leaf>`` loses to any file's ``agent.<active>.<leaf>`` even from
    level index 0 — which would contradict "the highest, above everything". The
    spec writes these keys bare (``model``, ``continue_mode``); the discriminated
    active spelling is the only reading under which the CLI actually outranks the
    files (spec §0 L21: there is no bare ``agent.<key>``).

    *model* (``-M``/``--model``): a non-empty string installs
    ``agent.<active>.model``. ``None`` or ``""`` installs NOTHING — absent ≠ ``""``,
    and ``""`` is a terminal value the resolver treats as meaningful, so a
    flag-not-given must not be laundered into one.

    *new_session* / *continue_session* / *resume* (``-N`` / ``-C`` / ``-R``, an
    argparse MUTUALLY EXCLUSIVE group) fold into ``agent.<active>.continue_mode``:

    * ``-N`` → ``False`` (start fresh);
    * ``-C`` → ``True`` (continue the last conversation);
    * ``-R`` → ``True``;
    * none of them → the key is ABSENT and the stored/default value stands.

    ⚑ ``-R`` installing ``True`` is LOAD-BEARING, not cosmetic.
    ``assembly.resolve_mode`` falls through its picker arm for a descriptor with no
    ``resume`` mode (goose/codex) and then keys on ``skip_continue``. The retired
    ``resolve_new_session`` returned "not fresh" for ``-R`` regardless of the stored
    key, so a picker-less ``-R`` yielded ``"continue"``. If ``-R`` installed nothing,
    a box with a stored ``continue_mode: false`` would flip to ``"start"``.
    """
    level: dict[str, object] = {}
    if selection:
        level.update(selection)
    if active_agent:
        if model:
            level[f"agent.{active_agent}.model"] = model
        if new_session:
            level[f"agent.{active_agent}.continue_mode"] = False
        elif continue_session or resume:
            level[f"agent.{active_agent}.continue_mode"] = True
    return level or None


def guard_cli_level(
    level: "Mapping[str, object] | None",
    *,
    active_agent: "str | None" = None,
    valid_agents: "Collection[str] | None" = None,
    agent_leaves: "Collection[str] | None" = None,
) -> None:
    """Refuse an illegal CLI-level key, NAMING it (spec §1A L335-338, §0 L160-162).

    Called from INSIDE :func:`kanibako.settings_launch.build_launch_snapshot`, before
    the splice, so no call site can bypass it. A no-op for ``None`` / an empty level.

    Refuses, in this order (each arm reports the key and the section that bars it):

    1. **Closed keyspace** — anything :func:`kanibako.settings_keyspace.key_validity`
       rejects. §0: an undeclared key is an ERROR that names it, never a silent
       accept and never a fabricated default.
    2. **Categorical** — ``meta.*`` / ``config.*`` / ``pref.*``
       (:data:`_FORBIDDEN_HEADS`).
    3. **Locator closure** — :data:`kanibako.settings_prefs.LOCATOR_CLOSURE`. This
       is the arm §1A explicitly asks for.

    *valid_agents* injects the agent-discriminator set for arm 1, and
    *active_agent* is UNIONED into it — the active agent is valid BY CONSTRUCTION,
    having just been resolved by :func:`kanibako.agent_select.select_agent`. When
    *valid_agents* is ``None`` the set is exactly ``{active_agent}``: every
    agent-scope key this module builds is spelled against that same discriminator,
    so plugin discovery would be pure cost — a flag-free launch keeps paying
    nothing, the same trade ``settings_prefs.apply_prefs`` makes. The union is not a
    bypass: a key naming any OTHER agent is still refused. *agent_leaves* likewise
    unions the plugin-declared agent keys over the core §2d set when a caller has
    them.

    ⚑ A dotted key is split on ``.``, so an agent NODE whose name contains a dot
    (``a.b℘claude``) is refused by arm 1 rather than silently mis-parsed. Such a box
    is already broken end-to-end — every dotted-key builder in the launch
    (``agent_defaults``, ``meta_agent_path_floor``, ``dotted_partial``) splits the
    same way and would never re-find the node — so this converts a silent
    mis-resolution into a loud one. Fixing the underlying spelling is out of scope.
    """
    if not level:
        return

    agents: set[str] = set(valid_agents or ())
    if active_agent:
        agents.add(active_agent)

    for key in level:
        reason = key_validity(
            key, valid_agents=agents, agent_leaves=agent_leaves,
        )
        if reason is not None:
            raise SettingsError(
                f"the command line cannot set '{key}': {reason}. The keyspace is "
                f"CLOSED (spec §0) — the CLI is a LEVEL over it, not a way around it."
            )

        head = key.split(".", 1)[0]
        if head in _FORBIDDEN_HEADS:
            raise SettingsError(
                f"the command line cannot set '{key}': '{head}.*' is never a CLI "
                f"target (spec §1A / §2h categorical tier) — meta.* is RO by "
                f"contract, config.* is the Layer-1 foundation resolved before the "
                f"cascade, and pref.* is a request, not a value."
            )

        if key in LOCATOR_CLOSURE:
            raise SettingsError(
                f"the command line cannot set '{key}': it is a LOCATOR-class key "
                f"(spec §1A L335-338) — it relocates the box root and the settings "
                f"file the cascade reads. The CLI is not a pref, so §2h's forbidden "
                f"tiers do not cover it and this guard bars it directly."
            )
