"""The §1A **CLI LEVEL** — the one flag→key table, the one builder, the one guard (P8).

The command line is its own input level, the highest, above every settings file and every
pref — a GENERAL rule, not a carve-out for particular flags. The level is EPHEMERAL always:
a flag applies to ONE launch and never mutates a stored value.

⚑ ``system.agent`` is deliberately PERMITTED at this level even though it selects a
cascade-input file (``meta.agent.<agent>.settings``); it is excluded from
``settings_prefs.LOCATOR_CLOSURE`` for the reason recorded there. It is the whole point of
the feature, and a guard that refused it would break P7.

PURE: no I/O and no plugin import at module load, like
:mod:`kanibako.settings.settings_keyspace`. The set of valid agent names is injected.

See ``llm-docs/kanibako/settings/settings_cli_level.py.md`` for the level's contract
(ephemeral · no recompute · not a scope) and for why the guard is written even though the
standing no-re-read guarantee already holds.
"""

from __future__ import annotations

from typing import Collection, Final, Mapping

from kanibako.settings.settings_keyspace import key_validity
from kanibako.settings.settings_prefs import LOCATOR_CLOSURE
from kanibako.settings.settings_resolve import SettingsError

#: The key naming the agent a box runs (spec §2g) — re-exported from the
#: selection seam's spelling so the level and the selection cannot drift.
SELECTION_KEY: Final[str] = "system.agent"

#: The DECLARED flag→key table (spec §1A). **WIRED ENTRIES ONLY** — the spec also names
#: ``-S``/``-A`` (``access``), which are deliberately absent because installing them here
#: would let an ephemeral flag mutate a stored value; see the llm-doc.
#:
#: The value is a display TEMPLATE (``<agent>`` = the active discriminator), used by tests
#: and by humans; the builder below is the executable form.
CLI_SHADOWED_KEYS: Final[Mapping[str, str]] = {
    "--agent": SELECTION_KEY,
    "-M/--model": "agent.<agent>.model",
    "-N/-C/-R": "agent.<agent>.continue_mode",
    "--image": "box.image",
    "--share-images": "box.share_images",
}

#: Namespaces a CLI value may never target (spec §2h's CATEGORICAL tier, which the CLI does
#: not inherit and therefore restates): ``meta`` is RO by contract, ``config`` is resolved
#: before this cascade, and ``pref`` is a request rather than a value. The refusal messages
#: below carry the per-head reason.
_FORBIDDEN_HEADS: Final[frozenset[str]] = frozenset({"meta", "config", "pref"})


def build_cli_level(
    *,
    selection: "Mapping[str, object] | None" = None,
    active_agent: "str | None" = None,
    model: "str | None" = None,
    new_session: bool = False,
    continue_session: bool = False,
    resume: bool = False,
    image: "str | None" = None,
    share_images: bool = False,
) -> "dict[str, object] | None":
    """Build the §1A CLI level for one launch, or ``None`` when it is empty.

    *selection* is P7's resolved-agent level, carried through VERBATIM; agent-scope entries
    are emitted only when *active_agent* is truthy. The llm-doc has the per-flag table.

    ⚑ **The agent-scope spelling is ``agent.<active>.<leaf>``, never
    ``agent.default.<leaf>``.** ``effective_behavior`` performs the §2d active-over-default
    pick AFTER the cascade merge, so a default-spelled value would lose to any file's active
    one even from level index 0 — contradicting "the highest, above everything".

    ⚑ An un-given flag installs NOTHING: ``None``/``""`` for *model* and *image*, ``False``
    for *share_images* (an argparse ``store_true``, so there is no negative spelling). Absent
    ≠ an explicit override, and ``""`` is a terminal value the resolver treats as meaningful.

    ⚑ ``-R`` installing ``True`` is LOAD-BEARING, not cosmetic. ``assembly.resolve_mode``
    falls through its picker arm for a descriptor with no ``resume`` mode (goose/codex) and
    then keys on ``skip_continue``, so were ``-R`` to install nothing, a box with a stored
    ``continue_mode: false`` would flip to ``"start"``.
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
    if image:
        level["box.image"] = image
    if share_images:
        level["box.share_images"] = True
    return level or None


def guard_cli_level(
    level: "Mapping[str, object] | None",
    *,
    active_agent: "str | None" = None,
    valid_agents: "Collection[str] | None" = None,
    agent_leaves: "Collection[str] | None" = None,
) -> None:
    """Refuse an illegal CLI-level key, NAMING it (spec §1A, §0).

    Called from INSIDE :func:`kanibako.settings.settings_launch.build_launch_snapshot`, before
    the splice, so no call site can bypass it. A no-op for an empty level. The three arms
    below run in order: closed keyspace, categorical head, locator closure.

    *active_agent* is UNIONED into *valid_agents* — it is valid BY CONSTRUCTION, having just
    been resolved. The union is not a bypass: a key naming any OTHER agent is still refused.
    The llm-doc explains why ``None`` there means "do not pay for plugin discovery".

    ⚑ A dotted key is split on ``.``, so an agent NODE whose name contains a dot is refused
    by arm 1 rather than silently mis-parsed. Defence in depth since ``agent_ref`` banned
    ``.`` in a persona/harness segment (2026-08-04), kept because every dotted-key builder in
    the launch splits the same way; refusing loudly beats mis-resolving silently.
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
