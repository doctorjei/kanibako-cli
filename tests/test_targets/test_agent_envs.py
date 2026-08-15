"""Plugin-declared AGENT-scope env defaults reach the box through the settings channel.

MBR-1 P4 (ruling 2026-08-14, *"we need those variables to use the env system"*): every
env literal a plugin used to hand core through ``descriptor.container_env`` is now a
DECLARED key, ``agent.<agent>.env.<VAR>`` (``Target.default_envs``), and rides the ONE
channel every other setting rides.

⚑ These are ARRIVAL tests, not route tests. What each case pins is that the plugin's
declared VALUE comes out of the resolve under its VARIABLE NAME — through the real
``build_launch_snapshot`` → ``snapshot_category_entries`` → ``collapse_env`` chain, driven by
the SHIPPED plugins' own tables. A plugin that stops declaring a variable, or declares a
different value, fails here whatever the delivery mechanism underneath is.

⚑ Most cases build their own launch FLOOR rather than taking it from ``commands.start``,
so what they pin is the CHANNEL — that a declared table resolves, arbitrates and carries
provenance correctly — independently of who assembles the floor.
``TestTheLaunchWireCarriesTheDeclaration`` at the bottom pins the other half: that the
REAL launch seam folds a plugin's table in, and folds it re-keyed.

⚑ MBR-1 P4b brought CORE's own four literals through the same door
(``KANIBAKO_NAME`` · ``KANIBAKO_AGENT`` · ``KANIBAKO_DIRECTIVE_SEED`` ·
``KANIBAKO_AGENT_MARKERS_DIR`` → ``system.env.<VAR>``), so this module carries both
halves of the one ruling: ``TestTheCoreStampsRideTheSameWire`` for their arrival, and
a case in ``TestNothingRestampsThemAboveTheChannel`` for the writer that no longer
exists above the channel.
"""

from __future__ import annotations

import logging

import pytest
import yaml

from kanibako.settings.agent_representation import agent_env_for_node
from kanibako.settings.settings_launch import (
    build_launch_snapshot,
    meta_agent_path_floor,
    meta_identity_floor,
    snapshot_category_entries,
)
from kanibako.settings.settings_resolve import ResolveCtx, SettingsError
from kanibako.settings.store_collapse import collapse_env
from kanibako.targets.no_agent import NoAgentTarget

# The SHIPPED declarations, spelled out so a silent change to a plugin's defaults file
# fails here by name. Values are GUEST-side and already ``$GUEST_HOME``-expanded.
DECLARED = {
    "claude": {
        "DISABLE_AUTOUPDATER": "1",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        "KANIBAKO_DIRECTIVE_FINAL": "/home/agent/.claude/CLAUDE.md",
    },
    "codex": {
        "KANIBAKO_DIRECTIVE_FINAL": "/home/agent/.codex/AGENTS.md",
    },
    "goose": {
        "GOOSE_DISABLE_KEYRING": "true",
        "CONTEXT_FILE_NAMES": '[".additionalContext.md","AGENTS.md",".goosehints"]',
        "KANIBAKO_DIRECTIVE_FINAL": "/home/agent/.config/goose/.additionalContext.md",
    },
}


def target_for(harness: str):
    """The SHIPPED plugin target for *harness* (skipped when its wheel is absent)."""
    module = pytest.importorskip(f"kanibako.plugins.{harness}")
    return getattr(module, f"{harness.capitalize()}Target")()


def make_ctx(node: str) -> ResolveCtx:
    return ResolveCtx(
        agent_name=node,
        workset_name=None,
        host_home="/home/u",
        xdg={"XDG_DATA_HOME": "/data", "XDG_CACHE_HOME": "/xcache"},
        config={"config.data": "/data", "config.agents": "/data/agents"},
    )


def env_floor(target, *, node: str, rekey: bool = True) -> dict[str, object]:
    """The launch floor a real launch builds for *target*, carrying its declared envs.

    Mirrors the aggregation ``commands.start`` does: the identity anchors the resolve
    needs, plus the plugin's ``default_envs()`` table re-keyed to the ACTIVE NODE.

    *rekey* False builds the floor the launch would have WITHOUT that re-key — the
    negative control, used to show the re-key is what makes a persona work.
    """
    floor: dict[str, object] = {}
    floor.update(meta_identity_floor(
        box_name="b", project_path="/p", inbox="/i", share_global="/sg",
        share_workset=None, agent_name=node,
    ))
    floor.update(meta_agent_path_floor(node))
    table = target.default_envs()
    floor.update(
        agent_env_for_node(table, node_name=node, harness=target.name)
        if rekey else table
    )
    return floor


def resolve_envs(target, *, node: str | None = None, rekey: bool = True, **files):
    """Run the real chain and return ``collapse_env``'s VAR → winner map."""
    node = node or target.name
    ctx = make_ctx(node)
    paths = {
        "system_path": None, "agent_path": None,
        "workset_path": None, "box_path": None,
    }
    paths.update(files)
    snapshot = build_launch_snapshot(
        agent_name=node, ctx=ctx,
        default_categories=env_floor(target, node=node, rekey=rekey),
        **paths,
    )
    return collapse_env(snapshot_category_entries(
        snapshot, active_agent=node, box_ctx=ctx,
    ))


def write_yaml(path, doc):
    path.write_text(yaml.safe_dump(doc))
    return path


class TestTheDeclaredValuesArrive:
    """T-D — the plugin's declared value comes out of the resolve under its VAR name."""

    @pytest.mark.parametrize("harness", sorted(DECLARED))
    def test_every_declared_variable_arrives_with_its_value(self, harness):
        target = target_for(harness)
        slots = resolve_envs(target)
        for var, value in DECLARED[harness].items():
            assert var in slots, f"{harness} lost {var}"
            assert slots[var].value == value

    @pytest.mark.parametrize("harness", sorted(DECLARED))
    def test_the_winner_names_the_discriminated_key_that_declared_it(self, harness):
        # Provenance is required, not decoration: ``box show --effective`` renders it
        # and the twin refusal names it. A bare ``agent.env.<VAR>`` is not a key (§0).
        target = target_for(harness)
        slots = resolve_envs(target)
        for var in DECLARED[harness]:
            assert slots[var].scope == "agent"
            assert slots[var].key == f"agent.{harness}.env.{var}"

    @pytest.mark.parametrize("harness", sorted(DECLARED))
    def test_the_declaration_table_is_exactly_the_shipped_set(self, harness):
        target = target_for(harness)
        assert target.default_envs() == {
            f"agent.{harness}.env.{var}": value
            for var, value in DECLARED[harness].items()
        }


class TestTheOverrideStory:
    """Ruling 41's ordering-1: a user overrides a declared default BY THE SAME KEY.

    The declared table is a FLOOR default, so any settings file that may write
    ``agent.<node>.env.<VAR>`` beats it — the keys are ordinary write-many settings keys
    and the nearest file wins. Naming the same VARIABLE at a DIFFERENT scope is the other
    story entirely (the collapse refusal below), never an override.
    """

    def test_the_agent_file_beats_the_plugin_default(self, tmp_path):
        # ⚑ The FLAT table is the agent file's only env spelling: ``self:`` IS
        # ``agent.goose``, so a second ``goose:`` level under it would read
        # ``agent.goose.goose.env`` and refuses (rulings 49c + 50; pinned in
        # ``TestTheNestedSpellingRefuses`` below).
        target = target_for("goose")
        agent_file = write_yaml(
            tmp_path / "agent.yaml",
            {"self": {"env": {"GOOSE_DISABLE_KEYRING": "false"}}},
        )
        slots = resolve_envs(target, agent_path=agent_file)
        assert slots["GOOSE_DISABLE_KEYRING"].value == "false"
        # Still the SAME key: an override moves the value, never the owner.
        assert slots["GOOSE_DISABLE_KEYRING"].key == (
            "agent.goose.env.GOOSE_DISABLE_KEYRING"
        )

    def test_the_system_file_beats_the_plugin_default(self, tmp_path):
        # A CONTAINING scope's file may write an agent-tier key (§0 directional
        # enforcement runs the other way), and it is still the same key.
        target = target_for("goose")
        system_file = write_yaml(
            tmp_path / "system.yaml",
            {"agent": {"goose": {"env": {"GOOSE_DISABLE_KEYRING": "false"}}}},
        )
        slots = resolve_envs(target, system_path=system_file)
        assert slots["GOOSE_DISABLE_KEYRING"].value == "false"

    def test_the_all_agents_default_tier_arrives_at_all(self, tmp_path):
        """The POSITIVE CONTROL for the pair below: the all-agents tier is a LIVE route.

        ⚑ Without this, the negative next door passes just as well when nothing reaches the
        cascade — a dead route and a losing route are indistinguishable from the winner alone.
        The tier is written in the SYSTEM file (``agent: default: env:``); the agent file has
        no spelling for it, since its flat table is re-rooted for the ACTIVE node only.
        """
        target = target_for("goose")
        system_file = write_yaml(
            tmp_path / "system.yaml",
            {"agent": {"default": {"env": {"PAGER": "less"}}}},
        )
        slots = resolve_envs(target, system_path=system_file)
        assert slots["PAGER"].value == "less"
        assert slots["PAGER"].key == "agent.default.env.PAGER"

    def test_the_all_agents_default_tier_does_not_beat_it(self, tmp_path):
        """``agent.default`` is the BACKSTOP tier and loses to the active slot.

        The §2d pick is active-over-default PER NAME, and a plugin declares under its own
        node — so a value written at the all-agents tier fills a gap, it does not override.
        """
        target = target_for("goose")
        system_file = write_yaml(
            tmp_path / "system.yaml",
            {"agent": {"default": {"env": {"GOOSE_DISABLE_KEYRING": "false"}}}},
        )
        slots = resolve_envs(target, system_path=system_file)
        assert slots["GOOSE_DISABLE_KEYRING"].value == "true"


class TestTheNestedSpellingRefuses:
    """Ruling 49c — a second ``<node>`` level under ``self:`` refuses, through the REAL chain.

    ⚑ The layer-level cases live in ``test_settings_launch.py``; what this one adds is that the
    refusal is REACHABLE from the path a launch actually walks, not only from ``_agent_partial``
    called on its own. It is a DIFFERENT raise from the twin refusal below — assembly-time file
    spelling, not collapse-time slot arbitration.
    """

    def test_a_second_node_level_under_self_refuses_naming_the_spelling(self, tmp_path):
        target = target_for("goose")
        agent_file = write_yaml(
            tmp_path / "agent.yaml",
            {"self": {"goose": {"env": {"GOOSE_DISABLE_KEYRING": "false"}}}},
        )
        with pytest.raises(SettingsError) as exc:
            resolve_envs(target, agent_path=agent_file)
        message = str(exc.value)
        assert "self.goose.env" in message
        # The cure names the flat table the ``agent set`` verb writes.
        assert "kanibako agent set goose env.GOOSE_DISABLE_KEYRING=false" in message


class TestATwinAtAnotherScopeRefuses:
    """Row 38: two scopes' keys naming ONE variable REFUSE the launch, naming both.

    This is the user-visible break the fold introduces. Before it, the plugin literal rode
    ABOVE the whole settings channel (``state_env``) and silently beat a user's
    ``box.env.<VAR>``; now the two are ordinary keys contesting one write-once slot.
    """

    def test_a_box_scope_twin_of_a_declared_variable_refuses(self, tmp_path):
        target = target_for("goose")
        box_file = write_yaml(
            tmp_path / "box.yaml",
            {"box": {"env": {"GOOSE_DISABLE_KEYRING": "false"}}},
        )
        with pytest.raises(SettingsError) as excinfo:
            resolve_envs(target, box_path=box_file)
        message = str(excinfo.value)
        assert "agent.goose.env.GOOSE_DISABLE_KEYRING" in message
        assert "box.env.GOOSE_DISABLE_KEYRING" in message

    def test_an_unrelated_box_variable_is_untouched(self, tmp_path):
        target = target_for("goose")
        box_file = write_yaml(
            tmp_path / "box.yaml", {"box": {"env": {"EDITOR": "vim"}}},
        )
        slots = resolve_envs(target, box_path=box_file)
        assert slots["EDITOR"].value == "vim"
        assert slots["GOOSE_DISABLE_KEYRING"].value == "true"


class TestTheDeclarationIsRefusedRatherThanBent:
    """A plugin's declaration is checked AT LOAD, against the CLOSED keyspace (§0).

    Every refusal here names what is wrong. The alternative — a floor entry that only
    fails at the next launch, or a coerced value — is exactly the deferred surprise §0
    forbids, and on this channel a silently absent variable is a box that misbehaves
    rather than one that fails.
    """

    @staticmethod
    def _load(monkeypatch, doc):
        from kanibako.settings import agent_defaults

        monkeypatch.setattr(agent_defaults, "_load_doc", lambda pkg, fn: doc)
        return agent_defaults

    def test_an_illegal_variable_name_is_refused_by_name(self, monkeypatch):
        agent_defaults = self._load(monkeypatch, {"env": {"2FA": "x"}})
        with pytest.raises(SettingsError) as excinfo:
            agent_defaults.load_envs("pkg", "f.yaml", "goose")
        assert "2FA" in str(excinfo.value)

    def test_a_non_string_value_is_refused_rather_than_coerced(self, monkeypatch):
        # YAML would turn an unquoted ``true`` into a bool whose str() is "True".
        agent_defaults = self._load(monkeypatch, {"env": {"GOOSE_DISABLE_KEYRING": True}})
        with pytest.raises(SettingsError) as excinfo:
            agent_defaults.load_envs("pkg", "f.yaml", "goose")
        assert "GOOSE_DISABLE_KEYRING" in str(excinfo.value)

    def test_no_env_section_declares_nothing(self, monkeypatch):
        agent_defaults = self._load(monkeypatch, {"descriptor": {"command": ["x"]}})
        assert agent_defaults.load_envs("pkg", "f.yaml", "goose") == {}

    def test_the_retired_descriptor_block_is_refused_by_name(self, monkeypatch):
        # A plugin still declaring ``container_env:`` under ``descriptor:`` would
        # otherwise load with an unread key and launch with none of its variables.
        agent_defaults = self._load(monkeypatch, {
            "descriptor": {"command": ["x"], "container_env": {"A": "1"}},
        })
        with pytest.raises(SettingsError) as excinfo:
            agent_defaults.load_descriptor("pkg", "f.yaml")
        assert "container_env" in str(excinfo.value)
        assert "env:" in str(excinfo.value)


class TestAPersonaNodeSeesThem:
    """The §2d pick reads ``agent.default`` ∪ ``agent.<ACTIVE NODE>``.

    A plugin declares against its HARNESS name, so without the re-key a PERSONA box would
    launch with none of its harness's required variables — the same live bug
    ``agent_common_for_node`` was written to close, on the env half.
    """

    def test_the_declared_variables_arrive_for_a_persona_node(self):
        target = target_for("goose")
        node = "navigator℘goose"
        slots = resolve_envs(target, node=node)
        for var, value in DECLARED["goose"].items():
            assert slots[var].value == value
            assert slots[var].key == f"agent.{node}.env.{var}"

    def test_a_bare_node_is_the_identity(self):
        target = target_for("goose")
        table = target.default_envs()
        assert agent_env_for_node(
            table, node_name=target.name, harness=target.name,
        ) == table

    def test_a_harness_keyed_floor_reaches_a_persona_with_nothing(self):
        """⚑ THE NEGATIVE CONTROL, measured through the real chain: 3 → 0.

        Feed the launch floor the plugin's own HARNESS-keyed table — the floor a
        launch would build if it dropped the re-key — and a persona node resolves
        NONE of the variables its harness requires. Nothing raises and nothing warns;
        the box simply comes up wrong, which is why the positive case above is not
        enough on its own.
        """
        target = target_for("goose")
        node = "navigator℘goose"
        slots = resolve_envs(target, node=node, rekey=False)
        for var in DECLARED["goose"]:
            assert var not in slots, f"{var} arrived without the re-key"


class TestTheLaunchWireCarriesTheDeclaration:
    """The REAL launch seam folds ``default_envs()`` into its floor, re-keyed.

    Everything above resolves a floor this module assembles, so it stays green even
    if ``commands.start`` never asks a plugin for its table. These two ask the seam
    itself, through ``_resolve_launch_snapshot``, and read the winner off the
    ``meta.assembly.env`` leaf the launch consumes.
    """

    VAR = "KANI_WIRED"

    class _DeclaringTarget(NoAgentTarget):
        """A REAL target declaring ONE variable against its own HARNESS name."""

        @property
        def name(self) -> str:
            return "claude"

        def default_envs(self) -> dict[str, str]:
            return {"agent.claude.env.KANI_WIRED": "wired"}

        def rom_root(self):
            return None

    def _launch_slots(self, std, config, project_dir, *, node: str):
        from kanibako.commands.start import (
            _launch_env_map,
            _resolve_launch_snapshot,
        )
        from kanibako.settings.paths import resolve_project

        proj = resolve_project(std, config, str(project_dir), initialize=True)
        snapshot, _deliveries = _resolve_launch_snapshot(
            std=std, proj=proj, agent_name=node,
            system_settings_path=None, agent_cfg_path=None,
            desc=None, install=None, target=self._DeclaringTarget(),
            agent_cfg=None, deliver_creds=True,
        )
        return _launch_env_map(snapshot)

    def test_a_bare_agent_launch_carries_the_declaration(
        self, std, config, project_dir,
    ):
        slots = self._launch_slots(std, config, project_dir, node="claude")
        assert slots[self.VAR].value == "wired"
        assert slots[self.VAR].key == "agent.claude.env.KANI_WIRED"

    def test_a_persona_launch_carries_it_under_the_NODE_key(
        self, std, config, project_dir,
    ):
        """RED if the seam folds the table in bare: the winner is the NODE's key.

        A harness-keyed entry is invisible to the §2d pick for a persona, so dropping
        the re-key empties this slot entirely — and empties it on the one launch shape
        no bare-agent test can see.
        """
        node = "navigator℘claude"
        slots = self._launch_slots(std, config, project_dir, node=node)
        assert slots[self.VAR].value == "wired"
        assert slots[self.VAR].key == f"agent.{node}.env.KANI_WIRED"

    @pytest.mark.parametrize("harness", sorted(DECLARED))
    def test_every_shipped_declaration_reaches_the_leaf(
        self, harness, std, config, project_dir,
    ):
        """The SHIPPED plugins, through the real seam: every variable, in the leaf.

        The cases at the top of this module resolve a floor assembled here; this one
        asks ``commands.start`` for the floor, so it fails if the seam ever stops
        carrying a plugin's declaration — with the plugin's own table, not a stand-in.
        """
        from kanibako.commands.start import (
            _launch_env_map,
            _resolve_launch_snapshot,
        )
        from kanibako.settings.paths import resolve_project

        target = target_for(harness)
        proj = resolve_project(std, config, str(project_dir), initialize=True)
        snapshot, _deliveries = _resolve_launch_snapshot(
            std=std, proj=proj, agent_name=harness,
            system_settings_path=None, agent_cfg_path=None,
            desc=None, install=None, target=target, agent_cfg=None,
            deliver_creds=True,
        )
        slots = _launch_env_map(snapshot)
        for var, value in DECLARED[harness].items():
            assert var in slots, f"{harness} lost {var} at the launch seam"
            assert slots[var].value == value
            assert slots[var].scope == "agent"
            assert slots[var].key == f"agent.{harness}.env.{var}"


class TestTheCoreStampsRideTheSameWire:
    """MBR-1 P4b: the four core ``KANIBAKO_*`` stamps as ``system.env.<VAR>`` keys.

    The class above pins the PLUGIN half of ruling 2026-08-14 (*"the 'KANIBAKO_'
    stuff should be in system.env, agent stuff should be in agent.env"*); this one
    pins the CORE half, in the same shape and through the same seam. Each case reads
    the ``meta.assembly.env`` leaf the launch consumes, so what it asserts is what a
    box gets — and the KEY it asserts beside the value is the whole point: a stamp
    written onto the container after the resolve has no key, no scope and no way for
    a user to override or contest it, which is the arrangement this replaces.

    ⚑ VALUES ARE INDEPENDENT LITERALS, not a second call to the code under test.
    They are GUEST-side and fixed by construction (``GUEST_HOME`` is a constant), so
    a change to either one fails here by name rather than agreeing with itself.
    """

    #: VAR → the value a launch of ``project_dir`` under node ``claude`` must carry.
    #: ⚑ ``KANIBAKO_NAME`` is the project directory's name — ``project_dir`` is the
    #: shared fixture, so this is that fixture's leaf name, not a free choice.
    STAMPS = {
        "KANIBAKO_NAME": "project",
        "KANIBAKO_DIRECTIVE_SEED": "/home/agent/.config/kanibako/kickoff.md",
        "KANIBAKO_AGENT": "claude",
        "KANIBAKO_AGENT_MARKERS_DIR": "/tmp/kanibako/agents",
    }

    class _CoreTarget(NoAgentTarget):
        """A REAL target declaring NO env of its own — every VAR here is core's."""

        @property
        def name(self) -> str:
            return "claude"

        def rom_root(self):
            return None

    def _launch_slots(self, std, config, project_dir, *, node="claude", target=...):
        from kanibako.commands.start import (
            _launch_env_map,
            _resolve_launch_snapshot,
        )
        from kanibako.settings.paths import resolve_project

        if target is ...:
            target = self._CoreTarget()
        proj = resolve_project(std, config, str(project_dir), initialize=True)
        snapshot, _deliveries = _resolve_launch_snapshot(
            std=std, proj=proj, agent_name=node,
            system_settings_path=None, agent_cfg_path=None,
            desc=None, install=None, target=target,
            agent_cfg=None, deliver_creds=True,
        )
        return _launch_env_map(snapshot)

    @pytest.mark.parametrize("var", sorted(STAMPS))
    def test_each_core_stamp_arrives_under_its_system_scope_key(
        self, var, std, config, project_dir,
    ):
        """The VALUE, the SCOPE and the KEY — the three a post-resolve stamp lacked."""
        slots = self._launch_slots(std, config, project_dir)
        assert var in slots, f"the launch seam lost {var}"
        assert slots[var].value == self.STAMPS[var]
        assert slots[var].scope == "system"
        assert slots[var].key == f"system.env.{var}"

    def test_a_no_agent_launch_carries_the_other_three_and_no_agent_stamp(
        self, std, config, project_dir,
    ):
        """The ONE gate that is not unconditional, kept where a shell launch sees it.

        ⚑ ``KANIBAKO_DIRECTIVE_SEED`` is asserted PRESENT here on purpose: its
        kickoff BIND is descriptor-gated and the variable is not, so a no-agent box
        gets it today. Tidying the variable onto the bind's gate would pass every
        agent case above and silently break exactly this one.
        """
        slots = self._launch_slots(std, config, project_dir, target=None)
        assert "KANIBAKO_AGENT" not in slots
        for var in ("KANIBAKO_NAME", "KANIBAKO_DIRECTIVE_SEED",
                    "KANIBAKO_AGENT_MARKERS_DIR"):
            assert slots[var].value == self.STAMPS[var]

    def test_a_system_file_entry_overrides_a_stamp_by_the_same_key(
        self, std, config, project_dir, tmp_path,
    ):
        """The override story ruling 41 states: same key, nearer file, no refusal.

        The derived stamps are the BASE FLOOR, below every settings file — which is
        what makes this the ordinary cascade rather than a special case. A twin at
        ANOTHER scope is the refusal instead, in the case below.
        """
        from kanibako.commands.start import (
            _launch_env_map,
            _resolve_launch_snapshot,
        )
        from kanibako.settings.paths import resolve_project

        var = "KANIBAKO_AGENT_MARKERS_DIR"
        # ⚑ THE CONTROL FIRST: without the file the slot carries the DERIVED value.
        # Without it this case would pass on a build that derives nothing at all,
        # asserting only that a system file is read — which is not the claim.
        assert self._launch_slots(
            std, config, project_dir,
        )[var].value == self.STAMPS[var]

        system_file = write_yaml(tmp_path / "system.yaml", {
            "system": {"env": {var: "/tmp/mine"}},
        })
        proj = resolve_project(std, config, str(project_dir), initialize=True)
        snapshot, _ = _resolve_launch_snapshot(
            std=std, proj=proj, agent_name="claude",
            system_settings_path=system_file, agent_cfg_path=None,
            desc=None, install=None, target=self._CoreTarget(),
            agent_cfg=None, deliver_creds=True,
        )
        slots = _launch_env_map(snapshot)
        assert slots[var].value == "/tmp/mine"
        assert slots[var].key == f"system.env.{var}"

    def test_a_twin_of_a_core_stamp_at_another_scope_refuses_naming_both(
        self, std, config, project_dir,
    ):
        """The BREAKING half of the fold, at the seam a user meets it.

        A ``box.env.KANIBAKO_NAME`` used to launch, with kanibako's own value written
        over it a moment later and nothing said. The stamps are ordinary keys now, so
        the two contest one write-once slot and the launch refuses naming both — the
        same rule ``TestATwinAtAnotherScopeRefuses`` pins for a plugin's declaration.
        """
        from kanibako.commands.start import _resolve_launch_snapshot
        from kanibako.settings.paths import resolve_project

        proj = resolve_project(std, config, str(project_dir), initialize=True)
        with pytest.raises(SettingsError) as excinfo:
            _resolve_launch_snapshot(
                std=std, proj=proj, agent_name="claude",
                system_settings_path=None, agent_cfg_path=None,
                desc=None, install=None, target=self._CoreTarget(),
                agent_cfg=None, deliver_creds=True,
                extra_default_categories={"box.env.KANIBAKO_NAME": "mine"},
            )
        message = str(excinfo.value)
        assert "system.env.KANIBAKO_NAME" in message
        assert "box.env.KANIBAKO_NAME" in message

    def test_an_unnamed_project_declares_no_name_stamp(self):
        """The ``proj.name`` gate, which no real launch can reach.

        ⚑ THE ONE CASE DRIVEN AT THE FUNCTION rather than through the seam, and
        deliberately: ``resolve_project`` always names a project, so the falsy branch
        has no production path to probe. Everything else in this class goes through
        ``_resolve_launch_snapshot``.
        """
        from types import SimpleNamespace

        from kanibako.commands.start import _core_env_default_categories

        table = _core_env_default_categories(
            proj=SimpleNamespace(name=""), target=None, agent_id="claude",
        )
        assert "system.env.KANIBAKO_NAME" not in table
        assert "system.env.KANIBAKO_DIRECTIVE_SEED" in table


class TestNothingRestampsThemAboveTheChannel:
    """The launch layer that used to sit ABOVE every settings file is empty now.

    ``assemble_env`` feeds ``state_env``, which the launch applies OVER the resolved
    config levels. While a plugin's static literals were seeded into it, a user who
    overrode one by key had the plugin's value written back on top a moment later —
    which is what made the override story impossible to tell. It realizes RESOLVED
    values only now, so nothing static passes it.

    ⚑ AND SINCE MBR-1 P4b THE SAME HOLDS FOR CORE'S OWN FOUR (below). They were the
    LAST writers above the channel, and the reason the class above can assert an
    override at all is that nothing re-stamps the winner afterwards.
    """

    @pytest.mark.parametrize("harness", sorted(DECLARED))
    def test_no_declared_variable_is_realized_over_the_resolved_value(self, harness):
        from kanibako.targets.assembly import assemble_env

        descriptor = target_for(harness).descriptor
        for tier in ("restricted", "full"):
            realized = assemble_env(descriptor, access=tier, setting_values={})
            for var in DECLARED[harness]:
                assert var not in realized, f"{var} would re-stamp the user's value"

    def test_nothing_above_the_channel_writes_the_core_stamps(self, tmp_path):
        """MBR-1 P4b: ``_assemble_launch_env`` is a PROJECTION of the leaf now.

        Handed an EMPTY slot map it must return an EMPTY env — the four
        ``KANIBAKO_*`` variables included. Anything it adds of its own is above every
        settings file and above ``-e`` by construction, which is the arrangement the
        fold removed; a fifth stamp reintroduced anywhere in that function fails here
        without needing to be named in advance.

        ⚑ Driven at the CONSUMER on purpose. The arrival half is
        ``TestTheCoreStampsRideTheSameWire``; what this pins is the ABSENCE of a
        second writer, which no arrival assertion can see — a stamp restored beside
        the channel would leave every arrival case green.

        ⚑⚑ STRENGTHENED AT P4c-1: the function takes NO ``cli_env`` any more, so the
        projection this asserts is now total for everything except ``state_env`` —
        there is no longer a per-run layer it could be adding on top. Restoring that
        parameter fails this case at the CALL, before the assertion.
        """
        from types import SimpleNamespace

        from kanibako.commands.start import _assemble_launch_env

        extra_mounts: list = []
        env, secret_vars = _assemble_launch_env(
            # ⚑ Only what ``_warn_legacy_env_files`` reads: the three legacy env
            # FILE paths it probes (none of which exist under ``tmp_path``, so it
            # prints nothing) — this case is about what the function RETURNS.
            std=SimpleNamespace(data_path=tmp_path),
            proj=SimpleNamespace(
                name="b", group=None,
                metadata_path=tmp_path, project_path=tmp_path,
            ),
            deliveries=SimpleNamespace(secrets=[]),
            env_slots={},
            state_env={},
            extra_mounts=extra_mounts,
            logger=logging.getLogger("test.p4b"),
        )
        assert env == {}
        assert secret_vars == []
        assert extra_mounts == []
