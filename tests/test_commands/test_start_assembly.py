"""The collapse WIRING — ``meta.assembly.*`` is produced, and since 2a-2 it BINDS.

🛑 **CUTOVER 6-R3 TOOK THE SECOND SIDE OF THIS FILE\'S ORACLE AWAY.** Through 6-R2 a
second, cross-scope ``reconcile`` pass ran beside the collapse and computed its own
whole answer, so most classes here could assert the collapse AGAINST it — equal where
the two agreed, and pinned where they diverged. That pass is deleted. There is ONE
route now, and the oracle is what the configuration DECLARES:

* the collapse runs on the REAL launch seam and its output lands at the declared keys
  (this side is easy to fake green — assert MEANING, not shape);
* the collapsed map is checked against the DECLARATIONS the adapter produced, never
  against a second implementation of the same fold;
* the emitter consumes the SHAPE, and the LAUNCH is pinned to the map it read.

⚑ The contrast tests that had the retired route for their oracle were RETIRED WITH IT
(6-R3), each one named where it stood. What replaced a contrast is a claim about the
declarations, not a rebase onto the survivor — an assertion that the collapse equals
itself would be a tautology.

Refusals are real, not hypothetical: the collapse forbids a bind above a bind and a
mask over a later scope\'s bind, and since 2c those refusals STOP the launch.
Prose: ``llm-docs/kanibako/commands/start.py.md``.
"""

import logging
import re
from pathlib import Path
from unittest.mock import patch

import pytest

from kanibako.commands.start import (
    _bind_map_from_mounts,
    _bind_map_masks,
    _emit_category_mounts,
    _launch_bind_map,
    _resolve_launch_snapshot,
    _snapshot_assembly_bindings,
    _snapshot_assembly_synced,
)
from kanibako.settings.agent_config import AgentConfig
from kanibako.settings.paths import resolve_project
from kanibako.settings.settings_launch import effective_behavior
from kanibako.settings.settings_resolve import SettingsError, normalize_bind_dest
from kanibako.settings.store_collapse import HOME_DEST
from kanibako.targets.assembly import BindingSourceError
from kanibako.targets.no_agent import NoAgentTarget

#: A bind at ``/home`` — legal on the live route (it collides with no dest and just
#: depth-sorts under the home mount), refused by the collapse's rule 1, because home
#: is pid 0 and is already collapsed beneath it before any scope folds.
_SUBSUMING = {"box.bindings.rw": {"/home": ("/tmp",)}}


class _WiringTarget(NoAgentTarget):
    """A REAL target for the live seam, so a new hook cannot make this pass by accident."""

    def rom_root(self) -> Path | None:
        return None


def _resolve(std, proj, **kw):
    """Drive the REAL launch seam with the base families on."""
    kw.setdefault("deliver_creds", True)
    # ⚑ DEFAULTED, not fixed: most cases here declare through
    # ``extra_default_categories`` and want no system FILE at all. A case whose
    # subject IS the file cascade (the same key in two files) passes the real path.
    kw.setdefault("system_settings_path", None)
    # ⚑ DEFAULTED for the same reason: a case whose subject is the per-agent FILE
    # (``self.env`` re-rooted into the cascade) must hand the seam a real path.
    kw.setdefault("agent_cfg_path", None)
    return _resolve_launch_snapshot(
        std=std,
        proj=proj,
        agent_name="claude",
        desc=None,
        install=None,
        target=_WiringTarget(),
        agent_cfg=None,
        **kw,
    )


def _sync(std, proj, *, logger, bindings=None, gated=True, **kw):
    """Drive the REAL sync consumer the way the launch path does: resolve, then apply.

    ⚑⚑ ONE RESOLVE, AND IT IS THE MAIN ONE — that is the whole of cutover 2b-3.
    ``_apply_synced_copies`` used to run a NARROW resolve of its own, which carries
    no base families, hence no home bind, hence NO ``meta.assembly.synced`` leaf at
    all; pointing the consumer at the leaf without moving it here would have read
    ``None`` on every launch and changed nothing. The bind map defaults to the one
    this very resolve produces, because a sync dest must be resolved against the
    mount set the collapse validated it over.

    ⚑ *gated* picks WHICH of the two passes this is — the LAUNCH refresh (mtime
    gate, the default) or the once-at-create UNGATED write (2026-08-11 ruling).
    """
    from kanibako.commands.start import (
        _apply_synced_copies,
        _launch_bind_map,
        _synced_uptodate,
    )

    snapshot, _deliveries = _resolve(std, proj, **kw)
    _apply_synced_copies(
        snapshot=snapshot,
        bindings=_launch_bind_map(snapshot) if bindings is None
        else bindings,
        logger=logger,
        skip_if=_synced_uptodate if gated else None,
    )
    return snapshot


def _assembly(snapshot):
    """The ``meta.assembly`` subtree of *snapshot*, or ``{}`` when it was not written."""
    meta = dict.get(snapshot, "meta") or {}
    return dict.get(meta, "assembly") or {}


def _entries(std, proj, snapshot):
    """The DECLARATIONS the fold saw — the adapter\'s entry list for *snapshot*.

    The oracle this file uses since 6-R3 retired the second route: built by the SAME
    call the seam makes, so it is what was declared and never a second fold.
    """
    from kanibako.commands.start import _launch_snapshot_inputs
    from kanibako.settings.core_defaults import canon_optional_bind_keys
    from kanibako.settings.settings_launch import snapshot_category_entries

    ctx = _launch_snapshot_inputs(std=std, proj=proj, agent_name="claude")[0]
    return snapshot_category_entries(
        snapshot, active_agent="claude", box_ctx=ctx,
        optional_keys=canon_optional_bind_keys(),
    )


class TestTheCollapseIsProduced:
    """``meta.assembly.{bindings,seeded,synced,env}`` reach the snapshot off a real resolve."""

    def test_all_declared_leaves_are_written(self, std, config, project_dir):
        """RED if the wiring is deleted: no leaf exists at all."""
        proj = resolve_project(std, config, str(project_dir), initialize=True)
        snapshot, _deliveries = _resolve(std, proj)

        assert sorted(_assembly(snapshot)) == ["bindings", "env", "seeded", "synced"]

    def test_home_is_pid_zero_and_folded_exactly_once(
        self, std, config, project_dir,
    ):
        """Home is BUILT AT THE SEAM off ``meta.box.home``, so it seeds the fold alone.

        🛑 The map's home entry is not a declaration that survived a fold: since 6-H
        there is no home declaration anywhere to survive one. It is the pid-0
        FOUNDATION, and ``TestTheFoundationIsBuiltAtTheSeam`` below pins where it
        comes from.
        """
        proj = resolve_project(std, config, str(project_dir), initialize=True)
        snapshot, _deliveries = _resolve(std, proj)

        bindings = _assembly(snapshot)["bindings"]
        assert HOME_DEST in bindings
        # The box home SOURCE, and its options carried whole (``Z,U`` — home does
        # not pass through the arm fold, so no ``rw`` is appended to it).
        assert bindings[HOME_DEST].src.endswith("/home")
        assert bindings[HOME_DEST].opts == "Z,U"

    def test_the_fold_sees_the_same_declarations_the_ADAPTER_produced(
        self, std, config, project_dir,
    ):
        """Every DECLARED MOUNT dest is a collapsed bind dest, and vice versa BUT HOME.

        ⚑ HOME IS ON THE RIGHT ONLY, and that asymmetry is the 6-H change stated as an
        equation: a scope's declarations are what the adapter emits, and home stopped
        being one — it is the seam-built foundation, present in the collapsed map and
        in no scope's declarations.

        ⚑ RECOMPOSED AT 6-R3. The oracle was the retired reconcile's MOUNT winners;
        it is the ADAPTER's entry list now — the declarations themselves, which is
        what that route was walking and the only side of the equation left.
        """
        proj = resolve_project(std, config, str(project_dir), initialize=True)
        snapshot, _deliveries = _resolve(std, proj)

        # ⚑ Holds because this configuration triggers no subsumption and no mask; the
        # collapse REMOVES what it subsumes or hides, so this is a pin on THIS
        # fixture, not a law.
        declared = {
            normalize_bind_dest(e.box_dest)
            for e in _entries(std, proj, snapshot) if e.delivery == "MOUNT"
        }
        assert HOME_DEST not in declared
        assert set(_assembly(snapshot)["bindings"]) == declared | {HOME_DEST}

    def test_a_narrow_resolve_writes_THE_SEED_LIST_AND_NOTHING_ELSE(
        self, std, config, project_dir, tmp_path,
    ):
        """🛑 INVERTED AT 2b-1 — this used to assert ``_assembly(snapshot) == {}``.

        The old premise (*no home in the entry list ⇒ no box to assemble*) stays
        true for the three leaves that DESCRIBE an assembly (``bindings``,
        ``synced``, ``env``), and was never true for
        the seed list: home is pid 0, seeded BEFORE any bind folds (§2a), so the
        seed arm is computable with no bind map at all. Gating it on a home bind
        made the leaf unreadable from precisely the caller that needs it — the
        CREATE-side seed resolve (``_apply_init_seeds``), which is narrow and
        reaches ``_seed_box_home`` without ever running a main resolve.
        """
        src = tmp_path / "seedme"
        src.write_text("x")
        proj = resolve_project(std, config, str(project_dir), initialize=True)
        snapshot, _deliveries = _resolve_launch_snapshot(
            std=std, proj=proj, agent_name="claude",
            system_settings_path=None, agent_cfg_path=None,
            desc=None, install=None, target=_WiringTarget(), agent_cfg=None,
            include_base_families=False,
            extra_default_categories={"box.seeded": {"~/seedme": (str(src),)}},
        )

        assert sorted(_assembly(snapshot)) == ["seeded"]
        assert [c.dest for c in _assembly(snapshot)["seeded"]] == ["/home/agent/seedme"]

    def test_a_narrow_resolve_with_no_seeds_writes_an_EMPTY_seed_list(
        self, std, config, project_dir,
    ):
        """ABSENT and EMPTY are different answers: "refused" vs "nothing declared".

        The leaf is written unconditionally when the seed arm folds, so a consumer
        reading ``None`` learns the collapse refused — never that this box seeds
        nothing. RED if the write is skipped for an empty list.
        """
        proj = resolve_project(std, config, str(project_dir), initialize=True)
        snapshot, _deliveries = _resolve_launch_snapshot(
            std=std, proj=proj, agent_name="claude",
            system_settings_path=None, agent_cfg_path=None,
            desc=None, install=None, target=_WiringTarget(), agent_cfg=None,
            include_base_families=False,
        )

        assert _assembly(snapshot) == {"seeded": []}


class TestTheCredentialGateReachesTheCollapse:
    """Cutover 2b-0: D-M4 is applied ONCE, above EVERY consumer of the entry list.

    🛑 Before the hoist the gate lived INSIDE the retired by-dest reconcile while
    ``_install_assembly_collapse`` was handed the RAW entry list, so a PRIVATE box
    got an empty copy set on one route and a ``meta.assembly.synced`` still carrying
    every credential row. Nothing consumed that leaf, so nothing broke — the first
    consumer pointed at it would have delivered the creds and reversed D-M4.

    ⚑ CUTOVER STEP 4 FINISHED IT: the second, internal re-gate is GONE, so the seam
    call in ``_resolve_launch_snapshot`` is the SOLE application of D-M4 and every
    consumer reads one gated list by construction.

    🛑 THIS FILE IS THE ONLY PLACE THAT PINS IT — MEASURED 2026-08-13 by neutering
    the seam call to ``delivered = list(entries)``. Exactly two tests reddened, both
    here: ``test_a_PRIVATE_box_collapses_NO_synced_row`` below and
    ``TestTheSyncApplierConsumesTheLeaf::
    test_a_PRIVATE_box_receives_NO_synced_row_THROUGH_THE_LEAF``. All 400 of
    ``test_commands/test_start.py`` stayed green, as did ``test_category_collisions``,
    ``test_flawed_oracle`` and ``test_channels/test_helper_listener``. Do not delete
    either pin expecting another suite to catch it: there is no other suite.
    """

    @staticmethod
    def _synced_dests(snapshot):
        return [copy.dest for copy in _assembly(snapshot).get("synced", [])]

    def _resolve_with_a_synced_cred(self, std, proj, tmp_path, *, deliver_creds):
        src = tmp_path / "creds.txt"
        src.write_text("token")
        return _resolve(
            std, proj, deliver_creds=deliver_creds,
            extra_default_categories={"box.synced": {"~/cred.txt": (str(src),)}},
        )[0]

    def test_a_shared_box_collapses_the_synced_row(
        self, std, config, project_dir, tmp_path,
    ):
        """The control: with creds shared the row IS in the leaf, so the drop below is real."""
        proj = resolve_project(std, config, str(project_dir), initialize=True)
        snapshot = self._resolve_with_a_synced_cred(
            std, proj, tmp_path, deliver_creds=True,
        )

        assert "/home/agent/cred.txt" in self._synced_dests(snapshot)

    def test_a_PRIVATE_box_collapses_NO_synced_row(
        self, std, config, project_dir, tmp_path,
    ):
        """🛑 THE CREDENTIAL-SAFETY PIN. RED without the hoist, on the identical fixture."""
        proj = resolve_project(std, config, str(project_dir), initialize=True)
        snapshot = self._resolve_with_a_synced_cred(
            std, proj, tmp_path, deliver_creds=False,
        )

        assert self._synced_dests(snapshot) == []

    # 🕯️ ``test_the_reconcile_and_the_collapse_see_ONE_gated_list`` DIED AT 6-R3.
    # It compared the collapse's synced dests against the RETIRED route's copy
    # winners; with one route left the comparison has no second side and a rebase
    # would assert the collapse equals itself. The claim it carried — a PRIVATE box
    # gets no credential row — is carried WHOLE by the two pins above
    # (``test_a_shared_box_collapses_the_synced_row`` +
    # ``test_a_PRIVATE_box_collapses_NO_synced_row``), which are the two this class's
    # own MEASURED mutation reddened; this one did not redden and pinned nothing
    # the pair did not already pin.

    def test_the_gate_drops_a_CREDENTIAL_seed_and_keeps_an_ordinary_one(self, tmp_path):
        """The seeded half, unreachable from a fixture: nothing sets ``is_credential`` yet."""
        from kanibako.settings.settings_categories import (
            CategoryEntry,
            gate_credential_delivery,
        )

        def seed(dest, *, is_credential):
            return CategoryEntry(
                category="seeded", scope="box", box_dest=dest,
                host_src=str(tmp_path), delivery="COPY", options=None, name=dest,
                key_segments=("box", "seeded", dest), is_credential=is_credential,
            )

        cred, plain = seed("/home/agent/.creds", is_credential=True), seed(
            "/home/agent/notes", is_credential=False,
        )

        assert gate_credential_delivery([cred, plain], True) == [cred, plain]
        assert gate_credential_delivery([cred, plain], False) == [plain]
        # IDEMPOTENT — no consumer re-gates any more (step 4 deleted the second
        # copy), but the property is the reason ONE application is safe to rely on:
        # a re-application must never be what makes the result correct.
        assert gate_credential_delivery([plain], False) == [plain]

    def test_the_gate_returns_a_NEW_list_never_the_callers_own(self, tmp_path):
        """P8 — a caller that mutates what it got back must not rewrite the entry list."""
        from kanibako.settings.settings_categories import gate_credential_delivery

        entries = []
        assert gate_credential_delivery(entries, True) is not entries
        assert gate_credential_delivery(entries, False) is not entries


class TestARefusalStopsTheResolve:
    """⚑⚑ THE 2a-2 SAFETY CLAIM, SPENT — what is left is the REFUSAL half.

    🛑 ``_both_ways`` AND ``test_delivery_is_identical_with_and_without_the_wiring``
    DIED AT 6-R3. They resolved twice — once with the collapse wired, once with it
    patched to a no-op — and compared what the RETIRED route delivered either way.
    With one route left there is nothing for the wiring to leave undisturbed: the
    collapse IS the delivery. What the box receives is pinned at the CALL SITE by
    ``test_commands/test_start.py::TestTheMainPathEmitsFromTheCollapse``, and the
    map\'s content by ``TestTheEmitterConsumesTheShape`` below.

    🛑 THE REFUSAL HALF IS INVERTED (2c). It read "the refusal must reach nobody
    until step 2c takes the swallow out." Step 2c took it out: a fold that refuses
    RAISES out of the resolve and stops the launch, and these say so rather than
    assert the old silence.
    """

    def test_a_BIND_FOLD_refusal_STOPS_THE_RESOLVE(
        self, std, config, project_dir, tmp_path,
    ):
        """⚑⚑ CUTOVER 2c, THE SHARPEST CASE: the fold refuses and the launch STOPS.

        🛑 THE ASSERTION THIS REPLACED IS THE POINT. It read ``sorted(_assembly(
        snapshot)) == ["seeded"]`` — a refused bind fold left the two assembly leaves
        absent, the launch fell back to a second route\'s rows, and a configuration
        the spec forbids started a box anyway. It now raises, naming both
        participants.

        ⚑ The seed is DECLARED here, not inherited: the shipped default-category
        families carry no ``seeded`` entry on this target, so the pre-2c form of this
        test would have rested on an empty list.

        MUTATION ANCHOR: restore the ``except SettingsError`` swallow in
        ``_install_assembly_collapse`` and this goes RED — no exception is raised.
        """
        src = tmp_path / "seedme"
        src.write_text("x")
        proj = resolve_project(std, config, str(project_dir), initialize=True)
        with pytest.raises(SettingsError) as excinfo:
            _resolve(std, proj, extra_default_categories={
                **_SUBSUMING, "box.seeded": {"~/seedme": (str(src),)},
            })

        message = str(excinfo.value)
        assert "'/tmp' at '/home'" in message, message
        assert HOME_DEST in message, message

    def test_a_SEED_ARM_refusal_STOPS_THE_RESOLVE(
        self, std, config, project_dir, tmp_path,
    ):
        """A seed outside home is refused by the fold, and that refusal is the launch's.

        ``store_collapse._refuse_seed_outside_home`` raises before any leaf is written,
        so the seed arm's own refusal reaches the user exactly as the bind fold's does.
        """
        src = tmp_path / "seedme"
        src.write_text("x")
        proj = resolve_project(std, config, str(project_dir), initialize=True)
        with pytest.raises(SettingsError, match="outside the home binding"):
            _resolve(std, proj, extra_default_categories={
                "box.seeded": {"/etc/outside": (str(src),)},
            })

    def test_the_refusal_is_RAISED_and_NOT_logged_away(
        self, caplog, std, config, project_dir,
    ):
        """🛑 THE SWALLOW IS GONE, and its absence is pinned, not just its replacement.

        Until 2c the cause went to ``debug`` as "meta.assembly.* not folded" and the
        launch continued. Asserting only that something raises would still pass if a
        second, quieter swallow were reintroduced beside it, so this asserts the log
        line is gone as well.
        """
        proj = resolve_project(std, config, str(project_dir), initialize=True)
        with caplog.at_level(logging.DEBUG, logger="kanibako.kanibako.commands.start"):
            with pytest.raises(SettingsError):
                _resolve(std, proj, extra_default_categories=_SUBSUMING)

        assert not any(
            "not folded" in r.message for r in caplog.records
        ), [r.message for r in caplog.records]


class TestTheEnvConsumerReadsTheLeaf:
    """The env consumer flip, pinned against the DECLARATIONS.

    ⚑ THIS IS WHY IT WAS WRITTEN THIS WAY. It was built beside a 6-R1 equivalence
    canary whose oracle was the retired route\'s env list; that canary died with the
    route at 6-R3, and had this one shared its oracle the env wire would now be
    pinned by nothing.  The oracle here is the FLOOR — what was declared, and which
    scope\'s value a box is supposed to receive.

    🛑 RENAMED WITH THE ROUTE IT PINS. The carrier it was named for
    (``LaunchDeliveries.envs``) is RETIRED: the launch and ``box show --effective``
    both read the collapse\'s ``meta.assembly.env`` leaf now, through
    ``_launch_env_map``, and the un-arbitrated entry list they used to fold
    themselves does not exist any more. A class still asserting through the carrier
    would not fail — it would simply stop describing the launch.

    🛑 THE TWO-SCOPE CASE ANSWERS DIFFERENTLY THAN IT ONCE DID, and it did not merely
    change its answer. The floor used to declare ``KANI_PINNED`` at BOTH workset and
    box scope and assert box\'s value, because the consumer\'s dict-update over a
    scope-sorted list was the only thing deciding a contested VAR. The collapse
    REFUSES that arrangement upstream, so the resolve raises and no leaf is ever
    written — the contest cannot reach this consumer at all, which is what the last
    case here pins. What survives above it is the part that was never a contest: each
    VAR declared at one scope, delivered, with the agent tier underneath.
    """

    _FLOOR = {
        "workset.env.KANI_PINNED": "workset",
        "workset.env.KANI_ONLY_WORKSET": "ws-only",
    }

    #: The SAME VAR at two scopes — one config, two keys, and no slot for both.
    _TWIN = {
        "workset.env.KANI_PINNED": "workset",
        "box.env.KANI_PINNED": "box",
    }

    #: The CORE stamps the launch DERIVES as ``system.env.<VAR>`` keys (MBR-1 P4b).
    #: They are part of what the collapse decides, not a channel beside it — which
    #: is why the whole-env case below subtracts them rather than excluding them.
    _CORE_STAMPS = {
        "KANIBAKO_NAME",
        "KANIBAKO_DIRECTIVE_SEED",
        "KANIBAKO_AGENT",
        "KANIBAKO_AGENT_MARKERS_DIR",
    }

    def _env(self, std, config, project_dir, floor=None):
        """The container env the way the launch builds it: leaf → consumer."""
        from kanibako.commands.start import _build_config_env, _launch_env_map

        proj = resolve_project(std, config, str(project_dir), initialize=True)
        snapshot, _deliveries = _resolve(
            std, proj,
            extra_default_categories=dict(self._FLOOR if floor is None else floor),
        )
        return _build_config_env(_launch_env_map(snapshot))

    def test_every_declared_VAR_reaches_the_consumer(
        self, std, config, project_dir,
    ):
        """The filter, and the tail a mis-wire would drop."""
        env = self._env(std, config, project_dir)
        assert env["KANI_PINNED"] == "workset"
        assert env["KANI_ONLY_WORKSET"] == "ws-only"

    def test_the_leaf_is_the_WHOLE_config_env(self, std, config, project_dir):
        """🛑 REPLACES ``test_the_agent_tier_stays_the_UNDER_layer`` (MBR-1 P3).

        That test pinned an ``AgentConfig.env`` dict handed to the consumer beside
        the leaf, and the claim it protected — "the agent tier is below every scope
        and is not dropped" — was the DEFECT stated as a property: below every scope
        is below ``system``, which inverts the bracket the spec gives the agent tier.
        The table is a cascade level now, so the consumer takes ONE input and this
        pins that: what the collapse decided is the whole of the config env, with no
        second channel able to add to it or shadow it.

        ⚑ THE CLAIM IS UNCHANGED AND THE SET IS NOT (MBR-1 P4b). The four core
        ``KANIBAKO_*`` stamps used to be assigned onto the finished env AFTER this
        consumer ran — the very "second channel" this case forbids — and they are
        ``system.env.<VAR>`` keys on the same leaf now, so they belong INSIDE the
        set rather than outside it. What the difference pins is that they are the
        WHOLE of the addition: a fifth variable appearing from anywhere fails here.
        ⚑ ``_WiringTarget`` declares no ``default_envs()`` of its own (it inherits
        ``NoAgentTarget``'s empty table), so the agent scope contributes nothing to
        this difference and the stamps are all of it.
        """
        env = self._env(std, config, project_dir)
        declared = {"KANI_PINNED", "KANI_ONLY_WORKSET"}
        assert declared <= set(env)
        assert set(env) - declared == self._CORE_STAMPS

    def test_a_VAR_named_by_two_scopes_never_reaches_the_consumer(
        self, std, config, project_dir,
    ):
        """The contest the dict-update used to settle silently now stops the resolve.

        ⚑ Driven through ``_env`` on purpose: the claim is not merely that something
        raises, it is that the raise happens UPSTREAM of the consumer, so no value is
        picked and no box is built from the half of the config that lost.
        """
        with pytest.raises(SettingsError) as exc:
            self._env(std, config, project_dir, floor=self._TWIN)

        message = str(exc.value)
        assert "KANI_PINNED" in message
        assert "workset.env.KANI_PINNED" in message
        assert "box.env.KANI_PINNED" in message


class TestOneVariableTwoOrderings:
    """T-A′ — the KEY cascades, the VARIABLE realizes, pinned from ONE var.

    These are the two orderings the settings framework runs, and for ``env`` they go
    in OPPOSITE directions — which is exactly why one VAR proves both and why they
    are asserted side by side rather than in two files:

    * a KEY is write-many. The SAME key in two FILES cascades and the NEAREST file
      wins, with no act limit and no complaint. That is ordinary, and this changeset
      did not touch it.
    * a VARIABLE is written ONCE. Two SCOPES' keys naming one VAR contest a slot
      that holds one value, and the collapse refuses, naming both keys.

    ⚑ THE FILES ARE THE POINT OF THE FIRST CASE. Injecting both values through the
    default-category floor would collapse them into one key before the cascade ever
    ran, so the case would pass without a cascade existing. It writes two real
    settings files and lets ``build_launch_snapshot`` merge them.
    """

    _VAR = "KANI_TWO_ORDERINGS"

    def _files(self, std, config, project_dir):
        from kanibako.settings.paths import box_workset_settings_paths

        proj = resolve_project(std, config, str(project_dir), initialize=True)
        box_path, _workset_path = box_workset_settings_paths(proj)
        std.settings.parent.mkdir(parents=True, exist_ok=True)
        box_path.parent.mkdir(parents=True, exist_ok=True)
        return proj, box_path

    def test_the_same_key_in_two_files_cascades_and_the_nearer_file_wins(
        self, std, config, project_dir,
    ):
        """ONE key, TWO files: the box file's value, and NO error."""
        from kanibako.settings.config_io import write_nested_key

        proj, box_path = self._files(std, config, project_dir)
        write_nested_key(std.settings, ("box", "env"), self._VAR, "from-system-file")
        write_nested_key(box_path, ("box", "env"), self._VAR, "from-box-file")

        snapshot, _deliveries = _resolve(
            std, proj, system_settings_path=std.settings,
        )

        winner = _assembly(snapshot)["env"][self._VAR]
        assert winner.value == "from-box-file"
        # ONE key owns the slot, and it is the key both files wrote.
        assert (winner.scope, winner.key) == ("box", f"box.env.{self._VAR}")

    def test_two_scopes_keys_naming_one_variable_refuse_the_launch(
        self, std, config, project_dir,
    ):
        """TWO keys, one slot: refused, and BOTH keys are named."""
        from kanibako.settings.config_io import write_nested_key

        proj, box_path = self._files(std, config, project_dir)
        write_nested_key(std.settings, ("system", "env"), self._VAR, "system-owned")
        write_nested_key(box_path, ("box", "env"), self._VAR, "box-owned")

        with pytest.raises(SettingsError) as exc:
            _resolve(std, proj, system_settings_path=std.settings)

        message = str(exc.value)
        assert self._VAR in message
        assert f"system.env.{self._VAR}" in message
        assert f"box.env.{self._VAR}" in message
        # The cure, not just the complaint.
        assert "ONE owner" in message


class TestTheAgentFileEnvRidesTheOneChannel:
    """MBR-1 P3 — the per-agent FILE's ``self.env`` is an ordinary ``agent.<node>.env.<VAR>``.

    🛑 IT WAS NOT ONE. ``settings_assemble._agent_partial`` re-rooted the file's flat
    ``self.secret_path`` and nothing else, so the env block never became a snapshot
    leaf: it reached the box on a private under-layer BELOW every collapsed slot.
    Two consequences, both closed here and both pinned below — it lost to a ``system``
    value (inverting the bracket, where agent outranks system), and it never saw the
    expand pass, so one written value behaved two ways depending on which FILE spelled
    it. Neither was a decision; both were an omitted arm.

    ⚑ THE INPUT IS WRITTEN THROUGH ``agent_file``, NEVER AT A HAND-SPELLED TABLE. That
    module IS the per-agent file's shape and it is what ``agent set <node> env.FOO=bar``
    routes its write through, so deriving the location from it makes these pins of the
    USER'S VERB reaching the box — not of a YAML layout that could quietly drift away
    from the verb that writes it.
    """

    _VAR = "KANI_FROM_THE_AGENT_FILE"

    def _agent_file(self, std, *, value="from-the-agent-file", var=None):
        """Write the file's flat env leaf exactly where the ``agent set`` verb writes it."""
        from kanibako.settings.agent_file import slot_for, write_leaf

        slot = slot_for(std.agents, "claude", f"env.{var or self._VAR}")
        slot.path.parent.mkdir(parents=True, exist_ok=True)
        write_leaf(slot, value)
        return slot.path

    def _slot(self, std, config, project_dir, *, var=None, **kw):
        """The collapsed slot the launch would deliver, off a real whole-box resolve."""
        proj = resolve_project(std, config, str(project_dir), initialize=True)
        snapshot, _deliveries = _resolve(std, proj, **kw)
        return _assembly(snapshot)["env"][var or self._VAR]

    def test_a_node_file_env_var_reaches_the_collapsed_leaf(
        self, std, config, project_dir,
    ):
        """T-B — the var arrives, at AGENT scope, under its own discriminated key.

        ⚑ The provenance is asserted with the value: arriving with the wrong scope
        would place the variable in the wrong slot-contest bracket, which is the
        half of this defect a value-only assertion cannot see.
        """
        slot = self._slot(
            std, config, project_dir, agent_cfg_path=self._agent_file(std),
        )
        assert slot.value == "from-the-agent-file"
        assert (slot.scope, slot.key) == ("agent", f"agent.claude.env.{self._VAR}")

    def test_the_agent_file_beats_the_system_file_at_the_same_key(
        self, std, config, project_dir,
    ):
        """ORDERING 1, the lower edge of the bracket: ONE key, TWO files, no error.

        ⚑ NOT a slot contest — that is the other ordering. Both files spell the SAME
        key ``agent.claude.env.<VAR>``, so the cascade settles it before the collapse
        sees anything, and what the collapse receives is one agent-scope entry.
        A ``system.env.<VAR>`` here would be a DIFFERENT key at a DIFFERENT scope and
        would refuse (``TestOneVariableTwoOrderings``); this is the case that must NOT.
        """
        from kanibako.settings.config_io import write_nested_key

        write_nested_key(
            std.settings, ("agent", "claude", "env"), self._VAR, "from-the-system-file",
        )
        slot = self._slot(
            std, config, project_dir,
            agent_cfg_path=self._agent_file(std),
            system_settings_path=std.settings,
        )
        assert slot.value == "from-the-agent-file"
        assert (slot.scope, slot.key) == ("agent", f"agent.claude.env.{self._VAR}")

    def test_a_workset_pref_beats_the_agent_file_at_the_same_key(
        self, std, config, project_dir,
    ):
        """The bracket's UPPER edge, by the only route a containing file has.

        ⚑ MEASURED, and it is why this is a pref: a WORKSET file may not spell an
        ``agent:`` table at all — ``_drop_upward_scopes`` drops every containing
        scope's table with a warning (spec §0), and agent CONTAINS workset. So the
        workset's legal way to reach an agent key is ``pref.agent.<agent>.<key>``
        (§2h), whose overlay is spliced above the agent-file rung. Asserting a plain
        ``workset.agent.*`` key here would be asserting a shape the keyspace refuses.
        """
        from kanibako.settings.config_io import write_nested_key
        from kanibako.settings.paths import box_workset_settings_paths

        proj = resolve_project(std, config, str(project_dir), initialize=True)
        _box_path, workset_path = box_workset_settings_paths(proj)
        workset_path.parent.mkdir(parents=True, exist_ok=True)
        write_nested_key(
            workset_path, ("pref", "agent", "claude", "env"),
            self._VAR, "from-the-workset-pref",
        )
        snapshot, _deliveries = _resolve(
            std, proj, agent_cfg_path=self._agent_file(std),
        )
        slot = _assembly(snapshot)["env"][self._VAR]
        assert slot.value == "from-the-workset-pref"

    def test_a_plugin_declared_default_loses_to_the_file_and_does_NOT_refuse(
        self, std, config, project_dir,
    ):
        """The floor and the file are ONE key at two cascade levels — ordering 1.

        P4 put every plugin env literal on ``agent.<agent>.env.<VAR>`` precisely so a
        user overrides one by writing the SAME key in a nearer file. That is a
        cascade, not a contest: exactly one key reaches the collapse, so nothing may
        refuse. Paired deliberately with the case below, which is the twin that MUST.
        """
        slot = self._slot(
            std, config, project_dir,
            agent_cfg_path=self._agent_file(std),
            extra_default_categories={
                f"agent.claude.env.{self._VAR}": "from-the-plugin-floor",
            },
        )
        assert slot.value == "from-the-agent-file"
        assert (slot.scope, slot.key) == ("agent", f"agent.claude.env.{self._VAR}")

    def test_a_box_scope_twin_of_a_file_var_refuses_naming_both_keys(
        self, std, config, project_dir,
    ):
        """The OTHER ordering, from the same input: two SCOPES, one slot, refused.

        The distinguishing pair for this whole phase. Above, a floor value and a file
        value are the same key and the nearer one simply wins. Here the second value
        is a DIFFERENT key at a DIFFERENT scope, so the two contest a variable that
        holds one value — and re-rooting the file's env is what makes that contest
        VISIBLE at all (before it, the box silently took the box-scope value).
        """
        proj = resolve_project(std, config, str(project_dir), initialize=True)
        with pytest.raises(SettingsError) as exc:
            _resolve(
                std, proj,
                agent_cfg_path=self._agent_file(std),
                extra_default_categories={f"box.env.{self._VAR}": "box-owned"},
            )

        message = str(exc.value)
        assert f"agent.claude.env.{self._VAR}" in message
        assert f"box.env.{self._VAR}" in message


class TestTheLaunchAgentFileStateMergesUnderTheLaunchNode:
    """S1b — the LAUNCH producer's node argument, pinned on the real chain.

    ``_resolve_launch_snapshot`` is the fifth and last of the ``agent_file.state_level``
    producers, and the only one that feeds the WHOLE-BOX launch: it wraps the per-agent
    file's flat behaviour table (``agent_cfg.state``) into a discriminated level under
    the node the launch is running as, and ``_agent_state_partial`` files it by
    ``level.node``. A wrong node here files a user's ``model`` under a slot the reader
    never looks in, and the box launches on the target's declared default instead.

    🛑 THIS CLASS EXISTS BECAUSE THAT ARGUMENT WAS UNCOVERED, MEASURED — the whole suite
    stayed green with the node mutated at this one site. ``conftest``'s ``start_mocks``
    stubs ``_resolve_launch_snapshot`` outright, so no test that fixture touches ever
    enters the function, and the real-chain callers elsewhere all pass ``agent_cfg=None``,
    which skips the produce entirely. So the call is made HERE with a real
    :class:`AgentConfig` and the base families ON — the two conditions the site is gated
    on — and the value is read at the CONSUMER, ``effective_behavior``, which is what the
    launch actually asks for the answer.
    """

    def _snapshot(self, std, config, project_dir, *, value="from-the-file"):
        """A whole-box resolve carrying a real per-agent behaviour state."""
        proj = resolve_project(std, config, str(project_dir), initialize=True)
        snapshot, _deliveries = _resolve_launch_snapshot(
            std=std, proj=proj, agent_name="claude",
            system_settings_path=None, agent_cfg_path=None,
            desc=None, install=None, target=_WiringTarget(),
            agent_cfg=AgentConfig(state={"model": value}),
            include_base_families=True,
        )
        return snapshot

    def test_the_file_state_reaches_the_behavior_reader_under_the_launch_node(
        self, std, config, project_dir,
    ):
        """The value the user wrote is the value the launch reads back.

        ⚑ READ THROUGH ``effective_behavior``, not off the raw subtree: the §2d
        active-over-default pick is the step that consults the node, so asserting the
        key's presence anywhere in the snapshot would pass with the table filed under
        the wrong discriminator.
        """
        snapshot = self._snapshot(std, config, project_dir)

        assert effective_behavior(
            snapshot, active_agent="claude",
        )["model"] == "from-the-file"

    def test_the_file_state_is_filed_under_THAT_node_AND_NO_OTHER(
        self, std, config, project_dir,
    ):
        """The other half of the same claim: the level is discriminated, not global.

        Without this the case above would still pass if the state were merged in
        undiscriminated, which is the shape the boundary module replaced.

        ⚑ ASSERTED ON THE ONE KEY, NOT ON AN EMPTY READ — measured: a second node does
        NOT read empty, because ``agent.default`` carries the core floor's own scalars
        (``canon``) and ``effective_behavior`` backstops onto them. What must not cross
        the node boundary is the FILE's key, and that is what this pins.
        """
        snapshot = self._snapshot(std, config, project_dir)

        assert "model" not in effective_behavior(snapshot, active_agent="goose")


class TestTheFoundationIsBuiltAtTheSeam:
    """⚑⚑ CUTOVER 6-H — home LEFT ``bindings.rw`` and the seam constructs it.

    🛑 THIS CLASS REPLACES ``TestHomeIsLiftedOut`` AND
    ``TestABoxIsAssembledOverEXACTLYONEHomeBinding``, whose three functions
    (``_split_home_bind`` · ``_home_bind_entries`` · ``_refuse_without_one_home``)
    went with the ``key: home`` row. What they guarded is not unguarded, it is
    UNCONSTRUCTIBLE or moved:

    * ZERO homes — there is no declaration left to suppress, and the SOURCE key is
      floor-produced and validated by ``settings_launch._assert_box_root_resolved``.
    * TWO homes — the COLLAPSE's ``_refuse_bind_over_bind``, against the foundation
      seeded beneath every scope's shape (``test_store_collapse.py``), reached from a
      real settings file by ``test_categories_live`` and end-to-end below.
    * The NARROW early return — same invariant, new gate (``whole_box``), pinned by
      the third case here under its original name and claim.
    """

    def _declarations(self, std, config, project_dir):
        """A REAL entry list off a real resolve — the fold\'s actual input."""
        proj = resolve_project(std, config, str(project_dir), initialize=True)
        snapshot, _deliveries = _resolve(std, proj)
        return _entries(std, proj, snapshot)

    def test_the_foundation_is_read_from_meta_box_home_with_BARE_options(
        self, std, config, project_dir,
    ):
        """🛑 THE TWO FACTS THE SEAM OWNS: which key it reads, and the options it pairs.

        The snapshot's ``meta.box.home`` is a path that matches NO ``proj`` attribute,
        so a seam that re-derived the foundation from ``proj.shell_path`` — or from an
        inlined ``@meta.box.path/home`` — cannot produce it.

        MUTATION ANCHORS, both proved: change ``_HOME_OPTIONS`` to ``"Z,U,rw"`` and the
        options assertion fails; build the bind from anything but
        ``_snapshot_home(snapshot)`` and the source assertion fails.
        """
        from kanibako.commands.start import _install_assembly_collapse
        from kanibako.settings.keystore import KeyStore

        snapshot = KeyStore()
        snapshot.insert_segments(("meta", "box", "home"), "/nowhere/near/proj/home")
        _install_assembly_collapse(
            snapshot, self._declarations(std, config, project_dir), whole_box=True,
        )

        home = _assembly(snapshot)["bindings"][HOME_DEST]
        assert home.src == "/nowhere/near/proj/home"
        # BARE ``Z,U`` — the foundation is pre-seeded and never passes the arm fold,
        # so no ``rw`` token is ever appended to it.
        assert home.opts == "Z,U"

    def test_a_settings_file_binding_at_home_is_REFUSED(
        self, std, config, project_dir,
    ):
        """⚑⚑ THE CAPABILITY REMOVAL, on the REAL launch seam — MIGRATION §2.32.

        A ``box.bindings.rw`` entry at ``~`` used to be the documented way to give a
        box a custom home: it overrode the core row through the cascade and won. There
        is no core row to override now, so the declaration is a SECOND bind at pid 0's
        point and the collapse refuses it by name. The cure is ``workset.boxes``.
        """
        proj = resolve_project(std, config, str(project_dir), initialize=True)

        with pytest.raises(SettingsError, match="binding") as e:
            _resolve(std, proj, extra_default_categories={
                "box.bindings.rw": {"~": ("/custom/home",)},
            })

        assert HOME_DEST in str(e.value), str(e.value)

    def test_a_NARROW_resolve_with_no_home_STILL_writes_its_seed_leaf(
        self, std, config, project_dir,
    ):
        """🛑 THE HALF THAT MUST NOT MOVE: the two assembly leaves are whole-box only.

        The create-side seed resolve carries no base families and describes no box, so
        it must still get a seed list — and neither of the other two leaves.

        MUTATION ANCHOR: delete the ``if not whole_box: return`` gate from
        ``_install_assembly_collapse`` and this fails — the narrow resolve would write
        all three leaves, off a snapshot that describes an injected table.
        """
        from kanibako.commands.start import _install_assembly_collapse
        from kanibako.settings.keystore import KeyStore

        snapshot = KeyStore()
        _install_assembly_collapse(
            snapshot, self._declarations(std, config, project_dir), whole_box=False,
        )

        assert _assembly(snapshot) == {"seeded": []}


class TestTheEmitterConsumesTheShape:
    """Cutover 2a-2: one emitter, one dest-keyed ``(src, opts)`` shape.

    🛑 ``_both_shapes`` AND ``test_both_shapes_emit_the_same_destinations`` DIED AT
    6-R3 — "two sources" was the retired route and this map\'s translation of it.
    The collapsed map\'s DEST SET is pinned against the declarations by
    ``TestTheCollapseIsProduced::test_the_fold_sees_the_same_declarations_the_ADAPTER_produced``,
    which is the same equation with the one surviving side on it.
    """

    def _collapsed(self, std, proj):
        """The collapsed map off a real resolve."""
        snapshot, _deliveries = _resolve(std, proj)
        collapsed = _snapshot_assembly_bindings(snapshot)
        assert collapsed is not None, "the fixture must actually collapse"
        return snapshot, collapsed

    def test_the_snapshot_reader_returns_a_copy_not_the_live_node(
        self, std, config, project_dir,
    ):
        """P8 — a caller mutating what it read must not rewrite the snapshot."""
        proj = resolve_project(std, config, str(project_dir), initialize=True)
        snapshot, _deliveries = _resolve(std, proj)
        first = _snapshot_assembly_bindings(snapshot)
        first.clear()

        assert _snapshot_assembly_bindings(snapshot), "the node was emptied through the read"

    def test_absent_reads_as_None_so_the_caller_can_tell_empty_from_missing(
        self, std, config, project_dir,
    ):
        """ABSENT is still a real state — but only a NARROW resolve can produce it now.

        🛑 REWRITTEN AT 2c. It used to reach ABSENT through a REFUSED fold, which is
        the state that no longer exists: a refusal raises. What is left is the narrow
        resolve, which carries no base families, hence no home bind, hence nothing to
        assemble — and it must stay distinguishable from an assembled-but-empty map.
        """
        proj = resolve_project(std, config, str(project_dir), initialize=True)
        narrow, _deliveries = _resolve_launch_snapshot(
            std=std, proj=proj, agent_name="claude",
            system_settings_path=None, agent_cfg_path=None,
            desc=None, install=None, target=_WiringTarget(), agent_cfg=None,
            include_base_families=False,
        )

        assert _snapshot_assembly_bindings(narrow) is None

    def test_the_main_path_takes_the_COLLAPSED_map_when_there_is_one(
        self, std, config, project_dir,
    ):
        """The switch itself, over a REAL resolve.

        ⚑ The second assertion — that the chosen map differs from the retired
        route\'s — died with that route at 6-R3. What it distinguished the collapse
        FROM no longer exists; that the map is the COLLAPSE\'s and not an injected
        one is pinned at the call site in
        ``test_commands/test_start.py::TestTheMainPathEmitsFromTheCollapse``.
        """
        proj = resolve_project(std, config, str(project_dir), initialize=True)
        snapshot, collapsed = self._collapsed(std, proj)

        assert _launch_bind_map(snapshot) == collapsed
        assert collapsed, "the fixture must produce a non-empty map"

    def test_a_NARROW_snapshot_REFUSES_instead_of_returning_None(
        self, std, config, project_dir,
    ):
        """🛑 THE FALLBACK IS GONE, and what replaced it is a NAMED error.

        This test used to assert the opposite — that a snapshot with no assembly leaf
        fell back to a second route\'s rows translated into this shape. With the arm removed
        the reader's ``None`` would reach ``_emit_category_mounts`` and die on
        ``.items()``: an uncaught ``AttributeError`` traceback rather than a
        ``KanibakoError``. So the seam states the wiring invariant itself.

        ⚑ A whole-box resolve cannot reach here — it refuses at the fold — so the
        narrow snapshot is the only way to hand this function an absent leaf at all.

        MUTATION ANCHOR: delete the ``if collapsed is None`` guard in
        ``_launch_bind_map`` and this fails with ``DID NOT RAISE``.
        """
        proj = resolve_project(std, config, str(project_dir), initialize=True)
        narrow, _deliveries = _resolve_launch_snapshot(
            std=std, proj=proj, agent_name="claude",
            system_settings_path=None, agent_cfg_path=None,
            desc=None, install=None, target=_WiringTarget(), agent_cfg=None,
            include_base_families=False,
        )

        with pytest.raises(SettingsError, match="meta.assembly.bindings"):
            _launch_bind_map(narrow)

    def test_the_collapse_folds_THE_MODE_INTO_THE_OPTIONS(
        self, std, config, project_dir,
    ):
        """🛑 THE MODE FOLD, PINNED AGAINST THE DECLARATIONS — not smoothed over.

        The five-arm shape carries ro/rw as the ARM, so the collapse folds the mode
        back into the option string (``store_collapse.fold_opt``): a rw DECLARATION
        whose own options read ``Z,U`` arrives as ``Z,U,rw``. Podman\'s default IS rw,
        so nothing about the box changes — but the option string podman receives does,
        and that is exactly the kind of difference a suite must state out loud rather
        than let a later reader discover in an argv.

        ⚑ HOME is the exception BY CONSTRUCTION: it is pid 0, SEEDED before any scope
        folds, so no arm ever appends to its options.

        ⚑ RECOMPOSED AT 6-R3. The oracle was the retired route\'s option strings; it
        is the DECLARATION\'s own ``options`` now, which is what that route emitted
        verbatim (``settings_categories._bind_options``) and the only side left.

        🛑 THE VALUE ORACLE IS UNMOVED: ``collapsed[HOME_DEST] == "Z,U"``, bare, and
        home appears in NO declaration (6-H) — its absence there is asserted too.
        """
        from kanibako.settings.settings_categories import is_read_only

        proj = resolve_project(std, config, str(project_dir), initialize=True)
        snapshot, collapsed = self._collapsed(std, proj)
        declared = {
            normalize_bind_dest(e.box_dest): e.options
            for e in _entries(std, proj, snapshot)
            if e.delivery == "MOUNT" and e.category != "masks"
        }

        assert collapsed[HOME_DEST].opts == "Z,U"
        assert HOME_DEST not in declared
        rw = [d for d, o in declared.items() if not is_read_only(o) and d in collapsed]
        assert rw, "the fixture must carry a rw bind that is not home"
        assert all(collapsed[d].opts == "Z,U,rw" for d in rw), collapsed
        ro = [d for d, o in declared.items() if is_read_only(o) and d in collapsed]
        assert ro, "the fixture must carry a ro bind"
        assert all(collapsed[d].opts == "ro" for d in ro), collapsed

    def test_A_MASK_NOW_HIDES_THE_BIND_NESTED_UNDER_IT(
        self, std, config, project_dir,
    ):
        """⚑⚑ THE USER-VISIBLE DIVERGENCE OF 2a-2 — the one that is not cosmetic.

        The PRE-2a-2 route emitted a mask and a bind INSIDE it as two mounts, and
        podman\'s depth-sort then landed the bind on top: the mask hid only what
        nothing else claimed. The collapse SWEEPS everything at or inside a mask,
        which is what a mask means. It does NOT refuse this configuration, so the
        difference reaches the box — hence CHANGELOG + MIGRATION §2.27 at 2a-2.

        ⚑ The second half of this test — the retired route still carrying the swept
        bind — died with that route at 6-R3. The SWEEP is what matters and it is
        asserted here; that the bind was DECLARED is asserted beside it, so its
        absence from the map is the sweep and not a resolve miss.
        """
        proj = resolve_project(std, config, str(project_dir), initialize=True)
        snapshot, _deliveries = _resolve(std, proj, extra_default_categories={
            "box.bindings.ro": {"~/masked/inside": ("/tmp",)},
            "box.masks": ["~/masked"],
        })
        chosen = _launch_bind_map(snapshot)

        assert chosen is not None
        assert _snapshot_assembly_bindings(snapshot) is not None, "must NOT refuse"
        assert "/home/agent/masked/inside" not in chosen, sorted(chosen)
        assert "/home/agent/masked/inside" in {
            normalize_bind_dest(e.box_dest) for e in _entries(std, proj, snapshot)
        }

    def test_a_mask_in_the_map_emits_no_mount(self):
        """A MASK has no host source; the tmpfs arm takes it (:class:`TestTheMaskArm`)."""
        from kanibako.settings.store_collapse import MASK, CollapsedBind

        emitted = _emit_category_mounts(
            {"/home/agent/w": MASK, "/tmp": CollapsedBind("/tmp", "rw")},
            label="shape",
        )

        assert [m.destination for m in emitted] == ["/tmp"]

    def test_the_emitter_depth_sorts_the_map_it_is_given(self, tmp_path):
        """⚑ A dest-keyed map carries NO order, so EMISSION owns the depth-sort.

        Podman resolves nested dests by last-``-v``-wins/depth-sort, so the deepest
        mount must be emitted LAST. The reconcile used to hand the emitter a sorted
        list; a map cannot, and taking insertion order would ship the fold's scope
        order to podman.
        """
        from kanibako.settings.store_collapse import CollapsedBind

        src = str(tmp_path)
        deep, mid, shallow = "/home/agent/w/v/u", "/home/agent/w", "/home"
        emitted = _emit_category_mounts(
            {d: CollapsedBind(src, "ro") for d in (deep, mid, shallow)},
            label="shape",
        )

        assert [m.destination for m in emitted] == [shallow, mid, deep]


class TestTheMaskArm:
    """Cutover 2a-4: the tmpfs masks and the bind mounts are halves of ONE map.

    The mask arm used to be built from a SECOND, cross-scope route\'s rows while the
    mounts beside it came from the collapse, so the two could — and at one dest did —
    disagree about what the box gets. That route is retired (6-R3); every test here
    still asks the question its divergence asked, because one map answering both arms
    is the property, not the absence of a rival: does the mask arm say the same thing
    the mount arm says, off the same value?
    """

    @staticmethod
    def _mounted(bindings):
        """The dests the emitter would mount from *bindings* (sources that exist)."""
        return [m.destination for m in _emit_category_mounts(bindings, label="mask-arm")]

    def test_a_declared_mask_reaches_the_tmpfs_arm_THROUGH_THE_COLLAPSE(
        self, std, config, project_dir,
    ):
        """The live seam: a ``<scope>.masks`` entry arrives as a mask IN THE MAP.

        RED if the arm is ever taken from anything but the map — the assertion is
        that the map it is taken from IS the collapsed one.
        """
        proj = resolve_project(std, config, str(project_dir), initialize=True)
        snapshot, _deliveries = _resolve(
            std, proj, extra_default_categories={"box.masks": ["~/private"]},
        )
        collapsed = _snapshot_assembly_bindings(snapshot)
        assert collapsed is not None, "the fixture must actually collapse"

        assert _bind_map_masks(_launch_bind_map(snapshot)) == [
            "/home/agent/private",
        ]
        # …and it is the COLLAPSE's own answer, not the fallback's.
        assert _bind_map_masks(collapsed) == ["/home/agent/private"]

    def test_the_mask_is_emitted_and_the_bind_UNDER_it_is_not(
        self, std, config, project_dir,
    ):
        """⚑⚑ BOTH ARMS, ONE CONFIGURATION — the divergence this step closes.

        A bind inside a mask is swept by the fold (MIGRATION §2.27, shipped at 2a-2
        for the mount arm). What 2a-4 adds is that the SAME map answers the mask arm,
        so the tmpfs the box receives is the one the sweep was performed against.
        """
        proj = resolve_project(std, config, str(project_dir), initialize=True)
        snapshot, _deliveries = _resolve(std, proj, extra_default_categories={
            "box.bindings.ro": {"~/private/notes": ("/tmp",)},
            "box.masks": ["~/private"],
        })
        assert _snapshot_assembly_bindings(snapshot) is not None, "must NOT refuse"
        chosen = _launch_bind_map(snapshot)

        assert _bind_map_masks(chosen) == ["/home/agent/private"]
        assert "/home/agent/private/notes" not in self._mounted(chosen)

    def test_a_bind_AT_a_mask_dest_takes_the_point_instead_of_emitting_BOTH(
        self, std, config, project_dir,
    ):
        """🛑 THE MEASURED DIVERGENCE OF 2a-4, and the reason it is not cosmetic.

        A mask may be TAKEN by a bind at its own destination in a later scope (the
        fold sweeps the mask and the bind lands). The retired cross-scope route
        resolved that same collision the other way — §0 row 2, a mask OVERRIDES a
        binding at its dest — so with the arms on two sources the launch emitted a
        ``-v`` bind AND a ``--mount type=tmpfs`` at ONE destination. Off one map it
        emits the bind.

        ⚑ The final assertion — the retired arm still answering the OPPOSITE way —
        died with that arm at 6-R3. Both DECLARATIONS are asserted present instead,
        so the outcome is still shown to be a decision rather than a resolve miss.
        """
        proj = resolve_project(std, config, str(project_dir), initialize=True)
        snapshot, _deliveries = _resolve(std, proj, extra_default_categories={
            "agent.claude.masks": ["~/contested"],
            "box.bindings.ro": {"~/contested": ("/tmp",)},
        })
        assert _snapshot_assembly_bindings(snapshot) is not None, "must NOT refuse"
        chosen = _launch_bind_map(snapshot)

        assert _bind_map_masks(chosen) == []
        assert "/home/agent/contested" in self._mounted(chosen)
        assert {"masks", "bindings.ro"} <= {
            e.category for e in _entries(std, proj, snapshot)
            if normalize_bind_dest(e.box_dest) == "/home/agent/contested"
        }

    def test_a_mask_ABOVE_a_LATER_scopes_bind_STOPS_THE_LAUNCH(
        self, std, config, project_dir,
    ):
        """⚑ MEASURED, and it is what MIGRATION §2.27 can no longer leave unsaid.

        Whether a mask sweeps a bind nested under it depends on the SCOPE DIRECTION:
        the sweep only happens when the mask folds LAST. A mask whose scope strictly
        PRECEDES the bind's is a bind arriving INSIDE an existing mask, which the
        collapse REFUSES.

        🛑 INVERTED AT 2c. Until now that refusal left the leaf absent, both arms fell
        back, and the box quietly received the pre-collapse answer (mask AND bind).
        The refusal is the launch's now: this arrangement stops the box instead of
        delivering a bind inside the void that is supposed to hide it.
        """
        proj = resolve_project(std, config, str(project_dir), initialize=True)
        with pytest.raises(SettingsError, match="sits inside the mask at"):
            _resolve(std, proj, extra_default_categories={
                "agent.claude.masks": ["~/private"],
                "box.bindings.ro": {"~/private/notes": ("/tmp",)},
            })

    def test_the_two_arms_PARTITION_one_map(self, tmp_path):
        """⚑ ONE VALUE, BOTH ARMS: mutate the map and BOTH answers move with it.

        The arms are complementary halves of the same keys — nothing is delivered
        twice and nothing is dropped. Flipping one entry moves it from one arm to the
        other, which is the property two independent sources could never have.
        """
        from kanibako.settings.store_collapse import MASK, CollapsedBind

        src = str(tmp_path)
        bindings = {
            "/home/agent/bound": CollapsedBind(src, "ro"),
            "/home/agent/hidden": MASK,
        }
        assert self._mounted(bindings) == ["/home/agent/bound"]
        assert _bind_map_masks(bindings) == ["/home/agent/hidden"]

        bindings["/home/agent/bound"] = MASK

        assert self._mounted(bindings) == []
        assert _bind_map_masks(bindings) == ["/home/agent/bound", "/home/agent/hidden"]

    def test_the_LAUNCH_reads_the_map_ONCE_and_serves_both_arms_from_it(
        self, start_mocks, tmp_path,
    ):
        """⚑⚑ THE PRODUCTION SEAM, not the functions: `_run_container` → `runtime.run`.

        Patching ``_launch_bind_map`` makes the map the ONLY place either arm could
        have come from — the harness resolves no mask at all, so any other spelling
        would hand podman an empty mask list while mounting the bind. The call COUNT
        is asserted too: two reads is not one value.
        """
        from kanibako.commands.start import _run_container
        from kanibako.settings.store_collapse import MASK, CollapsedBind

        bindings = {
            "/home/agent/hidden": MASK,
            "/home/agent/bound": CollapsedBind(str(tmp_path), "ro"),
        }
        with start_mocks() as m, patch(
            "kanibako.commands.start._launch_bind_map", return_value=bindings,
        ) as m_map:
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[],
            )
            kwargs = m.runtime.run.call_args.kwargs

        assert m_map.call_count == 1, "the map is read ONCE for both arms"
        assert kwargs["tmpfs_masks"] == ["/home/agent/hidden"]
        assert "/home/agent/bound" in [
            str(mount.destination) for mount in kwargs["extra_mounts"]
        ]

    def test_the_mask_arm_depth_sorts_on_the_key_the_mounts_use(self):
        """A dest-keyed map carries no order, so the tmpfs arm sorts too — same key.

        Podman is handed the masks and the binds as one argv; sorting them on two
        different keys would order a mask against the mount it sits inside by luck.

        ⚑ ``/home/agent/w/a`` before ``/home/agent/x`` is where the two orders PART:
        a plain lexicographic sort puts the deeper one first, and every shallower-is-
        alphabetically-earlier fixture passes under both.
        """
        from kanibako.settings.store_collapse import MASK

        deep, mid, shallow = "/home/agent/w/a", "/home/agent/x", "/home"

        assert _bind_map_masks(dict.fromkeys((deep, mid, shallow), MASK)) == [
            shallow, mid, deep,
        ]


class TestTheThreeMissingSourcePolicies:
    """One emitter, three per-dest answers to "the host source is not there" (2a-3).

    ⚑ The set membership tests are spelled against a dest that does NOT exist on
    disk, because every one of these policies is reachable only through the missing
    branch — a test whose source exists passes under all three and pins none.
    """

    _CRITICAL = "/home/agent/.local/bin/claude"

    def _emit(self, dest, src, **policy):
        from kanibako.settings.store_collapse import CollapsedBind

        return _emit_category_mounts(
            {dest: CollapsedBind(src, "ro")}, label="shape", **policy,
        )

    def test_a_missing_critical_source_RAISES_instead_of_dropping(self):
        """MUST-EXIST: the AGENT_CRITICAL safe-fail, now the emitter's own branch.

        RED if ``must_exist`` stops reaching the emitter — a missing agent binary
        would be dropped with a warning and reach podman as a crun crash instead of
        the clean exit-1.
        """
        with pytest.raises(BindingSourceError, match=re.escape(self._CRITICAL)):
            self._emit(
                self._CRITICAL, "/nonexistent/claude",
                must_exist=frozenset({self._CRITICAL}),
            )

    def test_a_must_exist_set_spelled_as_KEYS_matches_nothing_and_is_caught(self):
        """⚑⚑ THE HISTORICAL BUG, in its third home.

        ``critical_keys`` once held descriptor KEY NAMES while the arm was keyed by
        DESTINATION, so every critical bind silently degraded to best-effort. The
        collapsed map is dest-keyed too, so a key-spelled ``must_exist`` matches
        NOTHING — and the failure is SILENT (a warn-and-drop that looks ordinary).
        This test is the noise that makes it loud.
        """
        emitted = self._emit(
            self._CRITICAL, "/nonexistent/claude",
            must_exist=frozenset({"launcher", f"agent.claude.bindings.ro.{self._CRITICAL}"}),
        )
        assert emitted == [], "a key-spelled must_exist must not match a dest"

    def test_must_exist_beats_skip_if_absent_at_a_shared_dest(self):
        """A dest in BOTH sets must raise: must-exist wins its own dests outright.

        The live call site subtracts the critical dests from the skip set, so this
        can only fire on a caller that does not — and "which test ran first" is not
        an answer a safe-fail may depend on.
        """
        with pytest.raises(BindingSourceError):
            self._emit(
                self._CRITICAL, "/nonexistent/claude",
                must_exist=frozenset({self._CRITICAL}),
                skip_if_absent=frozenset({self._CRITICAL}),
            )

    def test_a_missing_critical_source_RAISES_before_any_mkdir(self, tmp_path):
        """⚑⚑ THE ORDERING, PINNED: the policy is read BEFORE the rw guarantee-create.

        An ``rw`` critical dest whose source vanished must RAISE. Consulted after the
        guarantee-create instead, ``mkdir(parents=True)`` would manufacture the very
        thing must-exist asks about — the safe-fail becomes an EMPTY DIRECTORY bound
        over the agent's binary, and nothing anywhere reports it.

        RED two ways: the raise disappears, AND the directory appears on disk.
        """
        from kanibako.settings.store_collapse import CollapsedBind

        src = tmp_path / "gone" / "claude"
        with pytest.raises(BindingSourceError):
            _emit_category_mounts(
                {self._CRITICAL: CollapsedBind(str(src), "rw")},
                label="shape", must_exist=frozenset({self._CRITICAL}),
            )
        assert not src.exists(), "the policy was consulted AFTER the guarantee-create"

    def test_skip_if_absent_drops_silently_and_the_default_warns(self, caplog):
        """SKIP-IF-ABSENT vs WARN-AND-DROP: same mount set, different noise.

        The agent's best-effort dests join the skip set for exactly this reason — a
        missing or suppressed agent share is ordinary, and warning on it every launch
        is what trains users to ignore warnings.
        """
        dest, src = "/home/agent/canon/handbook/box", "/nonexistent/chapter"
        with caplog.at_level(logging.WARNING):
            assert self._emit(dest, src, skip_if_absent=frozenset({dest})) == []
        assert caplog.records == []
        with caplog.at_level(logging.WARNING):
            assert self._emit(dest, src) == []
        assert [r.levelname for r in caplog.records] == ["WARNING"]

    def test_a_narrow_resolve_emits_ONLY_its_own_tables_dests(self, tmp_path):
        """🐞 THE D1 DEFECT, FIXED (cutover 6-R2) — and the pin that proves the filter.

        A narrow resolve reads the user's WHOLE cascade, so every user row reaches
        it — and the MAIN path already emits every one of them from the collapse.
        Emitting them here mounted each a SECOND time (shipped in v1.7.2:
        the image resolve\'s emitter had no filter at all) and did it from
        RAW rows, so a later-scope ``masks`` sweep the collapse applied was defeated.
        RED if the dest filter is dropped: the user rows come back.
        """
        from kanibako.commands.start import _narrow_bind_map
        from kanibako.settings.settings_categories import CategoryEntry

        table = "/home/agent/.kanibako/state/helper.sock"

        def row(scope, category, dest):
            return CategoryEntry(
                category=category, scope=scope, box_dest=dest,
                host_src=str(tmp_path), delivery="MOUNT", options="ro", name=dest,
                key_segments=(scope, *category.split("."), dest),
            )

        rows = [
            row("box", "bindings.rw", table),                       # THE TABLE's own
            row("agent", "bindings.ro", "/home/agent/.local/bin/claude"),
            row("agent", "common", "/home/agent/.claude/plugins"),
            row("box", "bindings.ro", "/opt/kanibako"),
        ]

        assert list(_narrow_bind_map(rows, frozenset({table}))) == [table]
        # ⚑ The rows ARE there to be emitted — the filter is what drops them, not
        # some upstream absence.
        assert sorted(_bind_map_from_mounts(rows)) == [
            "/home/agent/.claude/plugins", "/home/agent/.kanibako/state/helper.sock",
            "/home/agent/.local/bin/claude", "/opt/kanibako",
        ]

    def test_a_BOX_scope_bind_at_a_CRITICAL_dest_takes_THAT_DESTS_policy(
        self, std, config, project_dir,
    ):
        """The scope-blindness the emitter's dest-keying implies, MEASURED end to end.

        The policy sets are dest-spelled, and by the time the emitter reads the map
        the scope is gone — so a user's own bind that WINS one of the agent's delivery
        destinations inherits that destination's must-exist safe-fail rather than the
        warn-and-drop it would have taken on the old route. Documented in MIGRATION
        §2.28; this is the half of it the fold has to get right — that the box-scope
        declaration actually lands at that exact key.
        """
        critical = "/home/agent/.local/bin/claude"
        proj = resolve_project(std, config, str(project_dir), initialize=True)
        snapshot, _deliveries = _resolve(std, proj, extra_default_categories={
            "box.bindings.ro": {critical: ("/nonexistent/kanibako-test-source",)},
        })
        chosen = _launch_bind_map(snapshot)
        assert chosen[critical].src == "/nonexistent/kanibako-test-source"

        with pytest.raises(BindingSourceError, match=re.escape(critical)):
            _emit_category_mounts(
                chosen, label="policy", must_exist=frozenset({critical}),
            )

    def test_an_rw_dest_with_no_policy_still_guarantee_creates(self, tmp_path):
        """The L7 rw arm is UNMOVED — it just runs after the policy now."""
        from kanibako.settings.store_collapse import CollapsedBind

        src = tmp_path / "made" / "here"
        emitted = _emit_category_mounts(
            {"/home/agent/w": CollapsedBind(str(src), "rw")}, label="shape",
        )

        assert src.is_dir()
        assert [m.destination for m in emitted] == ["/home/agent/w"]


@pytest.mark.parametrize("leaf", ["bindings", "seeded", "synced", "env"])
def test_the_leaves_are_installed_as_segments_never_a_dotted_key(
    leaf, std, config, project_dir,
):
    """A dest is DATA: the leaf holds ONE value, never a tree shattered on its dots."""
    proj = resolve_project(std, config, str(project_dir), initialize=True)
    # ⚑ ONE DECLARATION PER LEAF, so no case is vacuous. The env one is what makes
    # the ``env`` parameter say anything: an empty map satisfies every shape.
    snapshot, _deliveries = _resolve(std, proj, extra_default_categories={
        "box.env.KANI_LEAF_SHAPE": "value",
    })
    value = _assembly(snapshot)[leaf]

    if leaf == "env":
        # ⚑ THE SLOT IS A VAR NAME, and the DOTTED thing it carries is the winner's
        # provenance KEY. Both must arrive whole: a dotted install would shatter
        # ``box.env.KANI_LEAF_SHAPE`` into three tree levels and the VAR would hold
        # a node instead of its value.
        assert isinstance(value, dict)
        winner = value["KANI_LEAF_SHAPE"]
        assert (winner.value, winner.scope) == ("value", "box")
        assert winner.key == "box.env.KANI_LEAF_SHAPE"
        return

    # ⚑ TWO SHAPES, ONE PROPERTY. ``bindings`` is dest-KEYED; ``seeded`` and
    # ``synced`` are scope-ordered LISTS that CARRY their dest (2026-08-09d for the
    # first, 2026-08-10b for the split). Either way the dest arrives WHOLE.
    assert isinstance(value, dict if leaf == "bindings" else list)
    dests = list(value) if leaf == "bindings" else [entry.dest for entry in value]
    assert all("/" in dest for dest in dests), sorted(dests)


class TestTheSeedApplierConsumesTheLeaf:
    """Cutover 2b-2: consumer 5 (``_apply_init_seeds``) reads ``meta.assembly.seeded``.

    ⚑⚑ THE ARM IS A LIST AND A DEST MAY REPEAT — spec ``settings-keyspace-1.8.0.md``
    :147-149, *"both flat scope-ordered lists"*, *"nothing is arbitrated at a
    destination"*. That is what these tests exist for: a dest-KEYED seed arm passes
    almost everything else in the suite and silently collapses the §2a template trio
    down to its last layer. Every test below is mutation-proved against exactly that.

    ⚑ The category FILTER is gone with the switch. ``seeded`` and ``synced`` are two
    SEPARATE leaves, so there is no discriminator left to test — and they are still
    COPIES, on both sides of the move.
    """

    def _seed(self, std, proj, seeds):
        """Drive the REAL create-side seed — narrow resolve, no home bind, no main resolve.

        ⚑ The extra rows arrive as the TARGET's ``default_seeds()``, which is the
        production door: ``_apply_init_seeds`` folds that table with
        ``template_seed_defaults`` and injects the result as the narrow resolve's
        ``extra_default_categories``. There is no other way in, and inventing one
        would test a route ``box create`` does not take.
        """
        from kanibako.commands.start import _apply_init_seeds

        class _T:
            name = "claude"

            def default_seeds(self):
                return seeds

        return _apply_init_seeds(
            std=std, proj=proj, agent_name="claude", target=_T(),
            global_config_path=std.settings,
            agent_config_path=std.agents / "claude" / "settings.yaml",
            logger=logging.getLogger("seed-consumer"), deliver_creds=True,
        )

    def _two_layers_at_one_dest(self, tmp_path):
        """Two seed source dirs, in two SCOPES, at ONE destination.

        ⚑⚑ TWO SCOPES AND NOT ONE, AND THAT IS A MEASUREMENT, not a preference.
        A SAME-scope duplicate dest is NOT EXPRESSIBLE from a declaration:
        ``settings_assemble._dest_keyed_map`` canonicalizes every key on read (R-11 —
        it is the one place that happens), so a scope's arm is a map KEYED by the
        normalized dest and ``~/x`` + ``/home/agent/x`` are ONE entry, last-wins,
        before any of this runs. The repetition the leaf's type permits therefore
        arrives ACROSS scopes — which is exactly the §2a template trio.

        ``store_shape``'s copy arm still appends rather than keys, and still must:
        that is a property of the ARM's type, not of what today's producer emits.
        The applier's own contract against a same-scope repeat is pinned separately,
        by injecting the leaf.
        """
        first, second = tmp_path / "first", tmp_path / "second"
        first.mkdir()
        second.mkdir()
        (first / "only-first.txt").write_text("first")
        (first / "shared.txt").write_text("first")
        (second / "shared.txt").write_text("second")
        return {
            "system.seeded": {"~/x": (str(first),)},
            "box.seeded": {"~/x": (str(second),)},
        }

    def test_the_leaf_carries_BOTH_rows_at_one_dest_in_SCOPE_order(
        self, std, config, project_dir, tmp_path,
    ):
        """The producer half: two rows at one dest, neither arbitrated away.

        RED if anything keys the seed arm on its dest, at any layer between the
        declaration and the leaf.
        """
        proj = resolve_project(std, config, str(project_dir), initialize=True)
        snapshot = self._seed(
            std, proj, self._two_layers_at_one_dest(tmp_path),
        )
        rows = [c for c in _assembly(snapshot)["seeded"] if c.dest == "/home/agent/x"]

        # system BEFORE box — ``SCOPE_CONTAINMENT``, not declaration order.
        assert [Path(c.src).name for c in rows] == ["first", "second"]

    def test_BOTH_rows_at_ONE_dest_are_applied_IN_ORDER(
        self, std, config, project_dir, tmp_path,
    ):
        """🐞 THE LOSS THIS STEP COULD HAVE CAUSED, pinned on the real seed path.

        MUTATION-PROVED against a dest-KEYED grouping arm
        (``by_dest[seed.dest] = [seed]``): ``only-first.txt`` then never lands,
        because the second row replaced the first before anything was staged. Proved
        AGAIN against a REVERSED group (``[*group][::-1]``): ``shared.txt`` then
        reads ``first``. Neither mutation is caught by the leaf test above — that one
        pins the producer, this one pins the CONSUMER's grouping.
        """
        proj = resolve_project(std, config, str(project_dir), initialize=True)
        self._seed(
            std, proj, self._two_layers_at_one_dest(tmp_path),
        )
        landed = proj.shell_path / "x"

        assert (landed / "only-first.txt").read_text() == "first", sorted(
            p.name for p in landed.iterdir()
        )
        # LAST-WINS per file — the overlay's whole meaning, and the order oracle.
        assert (landed / "shared.txt").read_text() == "second"

    def test_a_SAME_SCOPE_repeat_in_the_leaf_is_applied_as_TWO_ROWS(
        self, monkeypatch, std, config, project_dir, tmp_path,
    ):
        """The applier's contract against the LEAF'S TYPE, not against today's producer.

        ``CollapsedCopies`` is a flat scope-ordered list in which *a dest MAY repeat*
        (spec :147-149), and nothing in that type says the repeats came from
        different scopes. The declaration route cannot currently emit a same-scope
        repeat (see ``_two_layers_at_one_dest``), so it is INJECTED here — otherwise
        the consumer's handling of the shape it is typed against is untested, and a
        producer change would be the thing that discovers it.

        MUTATION-PROVED against the same dest-keyed grouping arm.
        """
        from kanibako.settings.store_collapse import CollapsedCopy

        first, second = tmp_path / "one", tmp_path / "two"
        first.mkdir()
        second.mkdir()
        (first / "only-first.txt").write_text("first")
        (first / "shared.txt").write_text("first")
        (second / "shared.txt").write_text("second")
        leaf = [
            CollapsedCopy(str(first), "/home/agent/x", ""),
            CollapsedCopy(str(second), "/home/agent/x", ""),
        ]
        monkeypatch.setattr(
            "kanibako.commands.start._snapshot_assembly_seeded", lambda _snap: leaf,
        )
        proj = resolve_project(std, config, str(project_dir), initialize=True)
        self._seed(std, proj, {})
        landed = proj.shell_path / "x"

        assert (landed / "only-first.txt").read_text() == "first"
        assert (landed / "shared.txt").read_text() == "second"

    def test_the_applier_reads_THE_LEAF_and_NOTHING_ELSE(
        self, monkeypatch, std, config, project_dir, tmp_path,
    ):
        """🛑 THE SWITCH ITSELF. Empty the LEAF and nothing is seeded, though every
        row is still declared.

        RED before 2b-2 on the identical fixture: the old loop walked the retired
        route\'s copy winners, which this monkeypatch does not touch at all.
        """
        monkeypatch.setattr(
            "kanibako.commands.start._snapshot_assembly_seeded", lambda _snap: [],
        )
        proj = resolve_project(std, config, str(project_dir), initialize=True)
        self._seed(
            std, proj, self._two_layers_at_one_dest(tmp_path),
        )

        assert not (proj.shell_path / "x").exists()
        assert not (proj.shell_path / "canon").exists(), "no layer may have seeded"

    def test_a_missing_seed_source_WARNS_NAMING_THE_RESOLVED_DEST(
        self, caplog, std, config, project_dir, tmp_path,
    ):
        """The one USER-VISIBLE text this step moves — CHANGELOG'd, so pinned.

        ``CategoryEntry.name`` is the dest AS AUTHORED (``~/gone``) while
        ``CollapsedCopy`` carries only the RESOLVED dest, so the warning's identity
        token changes value. Measured, not assumed: ``name='~/gone'`` vs
        ``box_dest='/home/agent/gone'`` off the real ``snapshot_category_entries``.

        Same change 2a-2 made for the mount warnings, now true of the seed path —
        which is why CHANGELOG.md and MIGRATION.md §2.27 were extended rather than
        given a second entry.
        """
        proj = resolve_project(std, config, str(project_dir), initialize=True)
        missing = tmp_path / "not-there"
        with caplog.at_level(logging.WARNING, logger="seed-consumer"):
            self._seed(std, proj, {"box.seeded": {"~/gone": (str(missing),)}})

        messages = [r.getMessage() for r in caplog.records]
        assert any(
            m.startswith("seed /home/agent/gone: host_src ") for m in messages
        ), messages
        # ⚑ NOT the authored spelling — that is the half the CHANGELOG names.
        assert not any(m.startswith("seed ~/gone:") for m in messages), messages

    def _create_sync(self, std, proj, *, logger):
        """Drive the REAL create-side sync — ``_seed``'s production SIBLING.

        ⚑ The function ``seed_new_box`` and the launch auto-create block both call,
        with the FULL resolve it runs for itself. Not re-spelled here: a harness that
        composed its own resolve + apply could pass while production passed the
        launch's mtime gate, which is precisely the delivery this pins.
        """
        from kanibako.commands.start import _sync_box_at_create

        return _sync_box_at_create(
            std=std, proj=proj, agent_name="claude", target=_WiringTarget(),
            global_config_path=std.settings,
            agent_config_path=std.agents / "claude" / "settings.yaml",
            logger=logger, deliver_creds=True,
        )

    def test_a_SEED_at_a_SYNCED_dest_IS_OVERWRITTEN_BY_THE_CREATE_TIME_SYNC(
        self, std, config, project_dir, tmp_path,
    ):
        """⚖️ RULED 2026-08-11 — SEED-THEN-OVERWRITE, and the collapse prunes NOTHING.

        🛑 THIS TEST WAS INVERTED. It used to pin the opposite outcome (the collapse
        DROPPED the seed row at a synced dest, so the dest stayed absent until the
        sync wrote it). Jei replaced that rule with a DELIVERY one — *"at box
        creation … write synced to it once at creation, irrespective of date"* — and
        confirmed the prune comes out with it.

        The hazard is unchanged and still real:

        1. the seed runs FIRST (create, create-if-absent) through ``shutil.copy2``,
           which PRESERVES the source mtime;
        2. ``_synced_uptodate`` skips the sync when ``dest.st_mtime >= src.st_mtime``;
        3. ⇒ a seed source NEWER than the sync source would pin the SEED's bytes at a
           credential dest, permanently and silently.

        What closes it is step (2) below: the create-time sync is UNGATED, so the
        destination holds SYNC-written bytes from creation onward — which is the fact
        the launch gate was always assuming and never had.

        ⚑⚑ THE ``os.utime`` CALLS ARE THE TEST. Without them the seed source would be
        the older one, every write would land for incidental reasons, and this would
        pin nothing. Steps (3) and (4) hold the mtime FIXED across a content change to
        prove the launch gate is live in both directions.

        MUTATION-PROVED against: restoring the ``collapse_seeded`` prune (step 1
        fails); giving the create-time sync the launch's mtime gate (step 2 fails);
        dropping ``skip_if`` on the launch pass (step 3 fails).
        """
        import os

        proj = resolve_project(std, config, str(project_dir), initialize=True)
        logger = logging.getLogger("seed-consumer")
        seed_src = tmp_path / "seed-cred.txt"
        sync_src = tmp_path / "sync-cred.txt"
        seed_src.write_text("SEED BYTES")
        sync_src.write_text("SYNC BYTES")
        # ⚑ The seed source is STRICTLY NEWER — the mtime gate's blind spot.
        os.utime(sync_src, (1000, 1000))
        os.utime(seed_src, (2000, 2000))
        (proj.metadata_path / "settings.yaml").write_text(
            f'box:\n'
            f'  seeded:\n    "~/cred.txt": ["{seed_src}"]\n'
            f'  synced:\n    "~/cred.txt": ["{sync_src}"]\n'
        )
        landed = proj.shell_path / "cred.txt"

        # (1) NOTHING IS ARBITRATED AT A DESTINATION: the seed row survives the
        #     collapse and is delivered, at a dest a sync also claims.
        self._seed(std, proj, {})
        assert landed.read_text() == "SEED BYTES"

        # (2) The create-time sync OVERWRITES it — *irrespective of date*, though the
        #     seed's mtime is NEWER, which is exactly what a gated pass would skip.
        self._create_sync(std, proj, logger=logger)
        assert landed.read_text() == "SYNC BYTES"

        # (3) ...and the dest now carries the SYNC's own mtime, so the LAUNCH pass
        #     compares against the sync's prior write and no-ops. Proved by changing
        #     the source's BYTES while holding its mtime: nothing may be delivered.
        sync_src.write_text("STALE-MTIME BYTES")
        os.utime(sync_src, (1000, 1000))
        _sync(std, proj, logger=logger)
        assert landed.read_text() == "SYNC BYTES"

        # (4) The gate still works FORWARD — a genuinely newer source is delivered.
        os.utime(sync_src, (3000, 3000))
        _sync(std, proj, logger=logger)
        assert landed.read_text() == "STALE-MTIME BYTES"

    def test_an_ABSENT_leaf_REFUSES_instead_of_falling_back(
        self, monkeypatch, std, config, project_dir, tmp_path,
    ):
        """🛑 INVERTED AT 2c — and this arm was UNREACHABLE, not merely unused.

        It read: an absent leaf falls back to a second route\'s ``seeded`` winners, so a
        refused fold costs a brand-new box nothing. The seed leaf rides its own gate
        (2b-1) and every resolve writes it, so once refusals stopped being swallowed
        the only way to reach ``None`` was to patch the reader — which is what this
        test had to do to exercise it. A route only a monkeypatch can take is not a
        route. What remains is the wiring invariant, named rather than silent: a create
        that seeds an empty home must say so.

        MUTATION ANCHOR: delete the ``if collapsed is None`` guard in
        ``_launch_seed_list`` and this fails with ``DID NOT RAISE``.
        """
        monkeypatch.setattr(
            "kanibako.commands.start._snapshot_assembly_seeded", lambda _snap: None,
        )
        proj = resolve_project(std, config, str(project_dir), initialize=True)

        with pytest.raises(SettingsError, match="meta.assembly.seeded"):
            self._seed(std, proj, self._two_layers_at_one_dest(tmp_path))

        assert not (proj.shell_path / "x").exists()

    def test_EVERY_resolve_writes_the_seed_leaf_so_the_consumer_never_guesses(
        self, std, config, project_dir,
    ):
        """🛑 REWRITTEN AT 2c: ABSENT is no longer a state a resolve can produce.

        This read ``_snapshot_assembly_seeded(refused) is None`` against a seed dest
        outside home — the arm that routed the old fallback. That refusal raises
        now, so the leaf's ``None`` means only "this snapshot was never resolved", and
        the property worth pinning is the one 2b-1 built: the seed leaf rides its OWN
        gate, so even a NARROW resolve — the create-side seed path, which has no home
        bind and can assemble nothing — still gets a list rather than a hole.

        MUTATION ANCHOR: gate the seed leaf on ``home_bind`` (move its insert below the
        ``if home_bind is None: return``) and the narrow read comes back ``None``.
        """
        from kanibako.commands.start import _snapshot_assembly_seeded

        proj = resolve_project(std, config, str(project_dir), initialize=True)
        narrow, _deliveries = _resolve_launch_snapshot(
            std=std, proj=proj, agent_name="claude",
            system_settings_path=None, agent_cfg_path=None,
            desc=None, install=None, target=_WiringTarget(), agent_cfg=None,
            include_base_families=False,
        )
        whole_box, _deliveries = _resolve(std, proj)

        assert _snapshot_assembly_seeded(narrow) == []
        assert _snapshot_assembly_seeded(whole_box) is not None

    def test_the_snapshot_reader_returns_a_copy_not_the_live_node(
        self, std, config, project_dir, tmp_path,
    ):
        """P8 — a caller mutating what it read must not rewrite the snapshot.

        ⚑ The fixture must actually SEED something, or ``clear()`` is a no-op and the
        test passes against a reader that hands out the live node.
        """
        from kanibako.commands.start import _snapshot_assembly_seeded

        src = tmp_path / "seedme"
        src.write_text("x")
        proj = resolve_project(std, config, str(project_dir), initialize=True)
        snapshot, _deliveries = _resolve(std, proj, extra_default_categories={
            "box.seeded": {"~/seedme": (str(src),)},
        })
        first = _snapshot_assembly_seeded(snapshot)
        assert first, "the fixture must produce a NON-EMPTY seed leaf"
        first.clear()

        assert _snapshot_assembly_seeded(snapshot), (
            "the seed leaf was emptied through the read"
        )


class TestTheSyncApplierConsumesTheLeaf:
    """Cutover 2b-3: consumer 6 (``_apply_synced_copies``) reads ``meta.assembly.synced``.

    ⚑⚑ **THE SWITCH IS ONLY REAL BECAUSE THE PASS MOVED.** Measured 2026-08-11: the
    narrow resolve the function used to run carries no base families, therefore no
    home bind, therefore ``_install_assembly_collapse`` writes no ``synced`` leaf on
    it — ever. Pointing the consumer at the leaf while it still resolved for itself
    would have read ``None`` on every launch and moved nothing at all, which is
    exactly the trap 2b-1 had to fix on the seed side. So the pass now consumes the
    MAIN launch resolve, below the bind map, and every test here drives that shape.

    ⚑ A sync dest is resolved against the mount set the collapse VALIDATED it over
    (``_collapse_synced`` folds against the bind map in the same ``CollapsedStore``),
    which is why ``_sync`` defaults *bindings* to that resolve's own map.

    ⚑⚑ ``synced`` IS A COPY AND STAYS A COPY. Nothing here turns one into a mount:
    the bind map decides only WHERE ON THE HOST a copy lands.
    """

    @staticmethod
    def _two_scopes_at_one_dest(tmp_path):
        """One credential dest, claimed by ``system`` and by ``box``.

        ⚑⚑ THE ``os.utime`` IS THE TEST, exactly as it is on the seed side. The
        SYSTEM source is made STRICTLY NEWER so that applying both rows in list
        order lets ``_synced_uptodate`` skip the box row — the failure mode
        ``_synced_last_wins`` exists for. Without it the box row would land last and
        the test would pass against no rule at all.
        """
        import os

        system_src, box_src = tmp_path / "system-cred", tmp_path / "box-cred"
        system_src.write_text("SYSTEM BYTES")
        box_src.write_text("BOX BYTES")
        os.utime(box_src, (1000, 1000))
        os.utime(system_src, (2000, 2000))
        return {
            "system.synced": {"~/cred.txt": (str(system_src),)},
            "box.synced": {"~/cred.txt": (str(box_src),)},
        }

    def test_the_leaf_carries_BOTH_rows_at_one_dest_in_SCOPE_order(
        self, std, config, project_dir, tmp_path,
    ):
        """The producer half: the sync arm is a FLAT list, not one arbitrated winner.

        RED if anything between the declaration and the leaf keys the sync arm on its
        destination and returns ONE row — which is what the retired by-dest reconcile
        did, and what the leaf deliberately does not.
        """
        proj = resolve_project(std, config, str(project_dir), initialize=True)
        snapshot, _deliveries = _resolve(
            std, proj, extra_default_categories=self._two_scopes_at_one_dest(tmp_path),
        )
        rows = [
            c for c in _assembly(snapshot)["synced"] if c.dest == "/home/agent/cred.txt"
        ]

        assert [Path(c.src).name for c in rows] == ["system-cred", "box-cred"]

    def test_the_LAST_row_at_a_dest_WINS_even_when_its_SOURCE_IS_OLDER(
        self, std, config, project_dir, tmp_path,
    ):
        """🐞🐞 THE SECOND CREDENTIAL REGRESSION THIS STEP OPENED, closed and pinned.

        ``_resolve_copy_group`` returned one row per dest and called it *the
        credential pick*. The leaf returns BOTH. Applied in list order under the
        mtime gate, a NEWER system source makes the box row a skip — so the LESS
        SPECIFIC scope silently keeps a credential destination.

        MUTATION-PROVED against dropping ``_synced_last_wins``: ``SYSTEM BYTES``.
        """
        proj = resolve_project(std, config, str(project_dir), initialize=True)
        landed = proj.shell_path / "cred.txt"

        _sync(
            std, proj, logger=logging.getLogger("sync-consumer"),
            extra_default_categories=self._two_scopes_at_one_dest(tmp_path),
        )

        assert landed.read_text() == "BOX BYTES"

    def test_NO_synced_declaration_copies_nothing(
        self, std, config, project_dir,
    ):
        """ADDITIVE: an empty sync arm is a no-op, not an empty-list special case.

        ⚑ MOVED HERE AT 2c from ``test_start.TestApplySyncedCopies``, whose whole class
        drove a NARROW resolve and therefore exercised the fallback arm that the
        cutover deleted. The behaviour is unchanged; what changed is that it is now
        asserted against the route the box actually takes.
        """
        proj = resolve_project(std, config, str(project_dir), initialize=True)
        before = sorted(p.name for p in proj.shell_path.iterdir())

        _sync(std, proj, logger=logging.getLogger("sync-consumer"))

        assert sorted(p.name for p in proj.shell_path.iterdir()) == before

    def test_a_MISSING_host_source_is_skipped_rather_than_raising(
        self, std, config, project_dir, tmp_path,
    ):
        """A declared source that is not on disk costs the launch nothing.

        ⚑ MOVED HERE AT 2c from ``test_start.TestApplySyncedCopies`` (see above). A
        sync source can legitimately be absent — a credential the host has not written
        yet — and the pass must step over it, not stop the box.
        """
        proj = resolve_project(std, config, str(project_dir), initialize=True)
        missing = tmp_path / "nope"

        _sync(
            std, proj, logger=logging.getLogger("sync-consumer"),
            extra_default_categories={"box.synced": {"~/gone": (str(missing),)}},
        )

        assert not (proj.shell_path / "gone").exists()

    def test_the_MTIME_GATE_leaves_a_destination_newer_than_its_source_alone(
        self, std, config, project_dir, tmp_path,
    ):
        """The LAUNCH pass is gated; an unchanged source is not recopied.

        ⚑ MOVED HERE AT 2c from ``test_start.TestApplySyncedCopies`` (see above).
        ⚑⚑ THE ``os.utime`` IS THE TEST: without it the source is the newer file, the
        copy happens, and the assertion below would be pinning nothing.
        🛑 This is the LAUNCH refresh (``gated=True``). The CREATE-time write is
        ungated by ruling and must NOT be made to share this answer.
        """
        import os

        proj = resolve_project(std, config, str(project_dir), initialize=True)
        src = tmp_path / "cred.txt"
        src.write_text("old")
        landed = proj.shell_path / "cred.txt"
        landed.parent.mkdir(parents=True, exist_ok=True)
        landed.write_text("newer")
        os.utime(src, (1000, 1000))
        os.utime(landed, (2000, 2000))

        _sync(
            std, proj, logger=logging.getLogger("sync-consumer"),
            extra_default_categories={"box.synced": {"~/cred.txt": (str(src),)}},
        )

        assert landed.read_text() == "newer"

    def test_a_dest_inside_a_NON_HOME_bind_lands_in_THAT_BINDS_SOURCE(
        self, std, config, project_dir, tmp_path,
    ):
        """⚑ THE DEST GENERALIZATION, on the one shipped bind that proves it.

        ``~/vault/rw`` is a real rw bind in every vault-enabled box, with a source
        that is NOT under the box home. The retired translator mapped every
        non-workspace guest path to ``shell_path/<rel>`` — a host location the vault
        bind SHADOWS, so the box never saw the copy.

        MUTATION-PROVED (M2) against restoring ``container._guest_dest_to_host``:
        this test goes RED while ``test_a_WORKSPACE_dest...`` below stays GREEN —
        the workspace arm alone is NOT sufficient coverage for the generalization.
        """
        src = tmp_path / "note.txt"
        src.write_text("in-vault")
        proj = resolve_project(std, config, str(project_dir), initialize=True)

        _sync(
            std, proj, logger=logging.getLogger("sync-consumer"),
            extra_default_categories={
                "box.synced": {"~/vault/rw/note.txt": (str(src),)},
            },
        )

        assert (proj.vault_rw_path / "note.txt").read_text() == "in-vault"
        # The shadowed host path the old translator computed was NOT written.
        assert not (proj.shell_path / "vault" / "rw" / "note.txt").exists()

    def test_a_WORKSPACE_dest_still_lands_under_the_workspace_bind(
        self, std, config, project_dir, tmp_path,
    ):
        """The control for M2, and the shipped behaviour that must not move.

        ⚑ It stays GREEN under the mutation above precisely because
        ``_guest_dest_to_host`` has a hardwired ``~/workspace`` arm. That is what
        makes it a control rather than a second copy of the test above.
        """
        src = tmp_path / "ws.txt"
        src.write_text("in-workspace")
        proj = resolve_project(std, config, str(project_dir), initialize=True)

        _sync(
            std, proj, logger=logging.getLogger("sync-consumer"),
            extra_default_categories={
                "box.synced": {"~/workspace/sub/f.txt": (str(src),)},
            },
        )

        assert (proj.project_path / "sub" / "f.txt").read_text() == "in-workspace"

    def test_a_dest_under_a_MASK_is_skipped_and_does_not_RAISE(
        self, caplog, std, config, project_dir, tmp_path,
    ):
        """🛑 The arm that must precede every ``Path(bind.src)``, and why.

        The collapse refuses a sync NOTHING (ruling 2026-08-12), so every declared row
        reaches delivery — a dest whose cover is a mask included. Delivery therefore
        meets a ``CollapsedBind(None, None)``.

        MUTATION-PROVED against dropping the ``is_mask`` arm: ``TypeError: argument
        should be a str or an os.PathLike object where __fspath__ returns a str, not
        'NoneType'`` — a crashed launch, not a skipped copy.
        """
        src = tmp_path / "hidden.txt"
        src.write_text("x")
        proj = resolve_project(std, config, str(project_dir), initialize=True)

        with caplog.at_level(logging.WARNING):
            _sync(
                std, proj, logger=logging.getLogger("sync-consumer"),
                extra_default_categories={
                    "box.masks": ["~/private"],
                    "box.synced": {"~/private/hidden.txt": (str(src),)},
                },
            )

        assert not (proj.shell_path / "private" / "hidden.txt").exists()
        assert any("is a mask" in r.getMessage() for r in caplog.records), [
            r.getMessage() for r in caplog.records
        ]

    def test_a_dest_inside_a_READ_ONLY_bind_is_skipped(
        self, caplog, std, config, project_dir, tmp_path,
    ):
        """A read-only bind's host source is not a delivery target (spec is SILENT).

        Refusing is the strict start: home and workspace are both ``rw``, so no
        shipped configuration loses anything, and loosening this later breaks no
        existing box. Under the retired translator the copy landed under the home
        stub — behind the read-only mount, invisible in the box — so nothing that
        used to be DELIVERED stops being delivered; only the warning is new.
        """
        src = tmp_path / "ro.txt"
        src.write_text("x")
        proj = resolve_project(std, config, str(project_dir), initialize=True)

        with caplog.at_level(logging.WARNING):
            _sync(
                std, proj, logger=logging.getLogger("sync-consumer"),
                extra_default_categories={
                    "box.bindings.ro": {"~/ro-area": (str(tmp_path / "roroot"),)},
                    "box.synced": {"~/ro-area/f.txt": (str(src),)},
                },
            )

        assert not (tmp_path / "roroot" / "f.txt").exists()
        assert any("read-only" in r.getMessage() for r in caplog.records), [
            r.getMessage() for r in caplog.records
        ]

    def test_a_dest_NO_BINDING_COVERS_is_skipped(
        self, caplog, std, config, project_dir, tmp_path,
    ):
        """No cover ⇒ no host location the copy could arrive at.

        ⚑ Wider than the retired outside-home skip: this fires for any guest path
        outside every bind, not only for one outside ``/home/agent``.
        """
        src = tmp_path / "out.txt"
        src.write_text("x")
        proj = resolve_project(std, config, str(project_dir), initialize=True)

        with caplog.at_level(logging.WARNING):
            _sync(
                std, proj, logger=logging.getLogger("sync-consumer"),
                extra_default_categories={
                    "box.synced": {"/srv/outside.txt": (str(src),)},
                },
            )

        assert any("no binding covers" in r.getMessage() for r in caplog.records), [
            r.getMessage() for r in caplog.records
        ]

    def test_an_ABSENT_leaf_REFUSES_instead_of_falling_back(
        self, monkeypatch, std, config, project_dir, tmp_path,
    ):
        """🛑 INVERTED AT 2c — the safety arm this asserted is gone.

        It read: an absent leaf falls back to a second route\'s ``synced`` winners, so a
        refused fold costs the box nothing. The fold's refusals are the launch's now,
        and the arm went with the swallow; what is left is the wiring invariant, which
        must be a NAMED error rather than a silent empty sync list.

        MUTATION ANCHOR: delete the ``if collapsed is None`` guard in
        ``_launch_synced_list`` and this fails with ``DID NOT RAISE`` — and the sync
        would silently deliver nothing.
        """
        monkeypatch.setattr(
            "kanibako.commands.start._snapshot_assembly_synced", lambda _snap: None,
        )
        src = tmp_path / "cred.txt"
        src.write_text("token")
        proj = resolve_project(std, config, str(project_dir), initialize=True)

        with pytest.raises(SettingsError, match="meta.assembly.synced"):
            _sync(
                std, proj, logger=logging.getLogger("sync-consumer"),
                extra_default_categories={"box.synced": {"~/cred.txt": (str(src),)}},
            )

        assert not (proj.shell_path / "cred.txt").exists()

    def test_the_sync_pass_applies_SYNCED_rows_AND_NEVER_SEEDED_ONES(
        self, std, config, project_dir, tmp_path,
    ):
        """🛑🛑 THE PROPERTY OUTLIVED THE FILTER THAT USED TO CARRY IT.

        The retired route\'s copy winners were ONE list holding BOTH copy categories,
        so the fallback arm needed a ``category == "synced"`` test to say which half
        it wanted. The arm is gone at 2c and the filter with it — the two categories are
        two LEAVES now, and this pass reads one of them. That makes the property
        structural rather than enforced, which is exactly when a test earns its keep:
        it is the thing that goes RED if the consumer is ever pointed at the wrong
        leaf, and nothing about the types would stop that.

        ⚑⚑ THE ``os.utime`` IS THE TEST. Without it the box's own file is the newer
        one, ``_synced_uptodate`` skips the copy, and this passes against no rule at
        all — MEASURED: the mutation went GREEN on the first form of this test.

        MUTATION-PROVED against pointing ``_launch_synced_list`` at
        ``_snapshot_assembly_seeded``: ``~/owned.txt`` is clobbered back to ``SEED``.
        """
        import os

        seed_src = tmp_path / "seed.txt"
        seed_src.write_text("SEED")
        proj = resolve_project(std, config, str(project_dir), initialize=True)
        owned = proj.shell_path / "owned.txt"
        owned.parent.mkdir(parents=True, exist_ok=True)
        owned.write_text("THE BOX OWNS THIS")
        # The seed source is STRICTLY NEWER — so only the category filter stops it.
        os.utime(owned, (1000, 1000))
        os.utime(seed_src, (2000, 2000))

        _sync(
            std, proj, logger=logging.getLogger("sync-consumer"),
            extra_default_categories={
                "box.seeded": {"~/owned.txt": (str(seed_src),)},
            },
        )

        assert owned.read_text() == "THE BOX OWNS THIS"

    def test_a_PRIVATE_box_receives_NO_synced_row_THROUGH_THE_LEAF(
        self, std, config, project_dir, tmp_path,
    ):
        """D-M4 on the NEW route — the 2b-0 hoist is what keeps this true.

        ⚑ NOTHING IN ``test_commands/test_start.py`` PINS THIS, and the note that
        used to name ``TestApplySyncedCopies.test_synced_suppressed_when_not_sharing``
        pointed at a test that no longer exists (corrected 2026-08-13). That file's
        surviving neighbour, ``TestApplyInitSeeds::
        test_non_credential_seed_copied_even_when_not_sharing``, asserts the gate's
        NARROWNESS — a plain seed still copies for a private box — so it is green
        whether the gate runs or not, by construction. This test resolves with base
        families on, so the leaf exists and what it pins is the COLLAPSE arm.

        MUTATION-PROVED against reverting ``gate_credential_delivery`` to hand
        ``_install_assembly_collapse`` the UNGATED entry list: the credential lands.
        """
        src = tmp_path / "cred.txt"
        src.write_text("token")
        proj = resolve_project(std, config, str(project_dir), initialize=True)
        landed = proj.shell_path / "cred.txt"
        cats = {"box.synced": {"~/cred.txt": (str(src),)}}

        snapshot = _sync(
            std, proj, logger=logging.getLogger("sync-consumer"),
            extra_default_categories=cats, deliver_creds=False,
        )

        # The control lives in the assertion: the leaf EXISTS (so the fallback is not
        # what is being observed) and is EMPTY of the row.
        assert _snapshot_assembly_synced(snapshot) == []
        assert not landed.exists()

    def test_the_consumer_CANNOT_BE_CALLED_before_the_bind_map_exists(self):
        """P3 — the failure is made unavailable, not merely detectable.

        There is no launch-harness test that could catch a mis-placed call:
        ``start_mocks`` patches ``_resolve_launch_snapshot`` outright, so no
        ``_run_container`` test drives the real resolve at all. The signature is
        therefore the guard — every input REQUIRED, keyword-only, and none of them
        nameable at the site this pass used to occupy.

        RED the moment any of the four grows a default (``= None`` most of all,
        which is how this becomes a silent no-delivery again).

        ⚑ ``skip_if`` JOINED THEM 2026-08-11 and is required for a DIFFERENT reason:
        the launch refresh and the once-at-create write are two passes with two
        answers, and a default would silently hand one of them the other's.

        ⚑ A ``reconciled`` PARAMETER LEFT THEM at cutover 2c: it was read only by the
        sync list\'s fallback arm, and an input nothing reads cannot make a mis-placed
        call fail.
        """
        import inspect

        from kanibako.commands.start import _apply_synced_copies

        params = inspect.signature(_apply_synced_copies).parameters

        assert list(params) == ["snapshot", "bindings", "logger", "skip_if"]
        assert all(
            p.kind is inspect.Parameter.KEYWORD_ONLY for p in params.values()
        ), {n: str(p.kind) for n, p in params.items()}
        assert all(
            p.default is inspect.Parameter.empty for p in params.values()
        ), {n: p.default for n, p in params.items()}


def _sync_over_the_launch_map(std, proj, *, logger, gated=False, **kw):
    """Run the sync pass the way the LAUNCH does — and hand the map back.

    ⚑ THE COMPOSITION IS THE PRODUCTION ONE, IN ITS ORDER: resolve →
    ``_launch_bind_map`` → ``_apply_synced_copies`` → (the caller's)
    ``_bind_map_masks``. The module-level ``_sync`` helper builds its map inline and
    drops it, which cannot observe a mask the sync pass DELETED; this returns the very
    object ``_run_container`` keeps, so the tmpfs arm can be read off it.
    """
    from kanibako.commands.start import _apply_synced_copies, _synced_uptodate

    snapshot, _deliveries = _resolve(std, proj, **kw)
    bindings = _launch_bind_map(snapshot)
    _apply_synced_copies(
        snapshot=snapshot, bindings=bindings, logger=logger,
        skip_if=_synced_uptodate if gated else None,
    )
    return bindings


class TestTheCopyRowsAtAMasksOwnPoint:
    """Spec §0's containment table, the two COPY rows, where they DIFFER.

    ============  ===============  ==============
    Arriving      Point is mask    Parent is mask
    ============  ===============  ==============
    copy (file)   ok — delete it   REFUSE
    copy (dir)    REFUSE           REFUSE
    ============  ===============  ==============

    ONE cell, and it is the entire reason there are two rows: a copied FILE at a
    mask's OWN point replaces the mask — one file filling one void is total, so
    nothing partial survives — while a copied DIRECTORY at that same point is
    REFUSED, because a mask may not be left partially populated. A mask that is a
    strict PARENT refuses both.

    ⚑ ``synced`` is the only copy category these rows reach: it copies LAST, through
    the final bind map, so it meets mounts by construction. ``seeded`` applies to home
    alone and completes before any binding folds.

    ⚑ THE MASK DELETION IS OBSERVED WHERE THE BOX WOULD OBSERVE IT — ``_bind_map_masks``
    over the map the launch keeps, which is what feeds ``runtime.run(tmpfs_masks=...)``.
    Asserting only that the file landed would pass with the tmpfs still mounted over
    it, i.e. against a box that never sees the copy.
    """

    def test_a_synced_FILE_at_a_masks_own_point_LANDS_AND_DELETES_THE_MASK(
        self, caplog, std, config, project_dir, tmp_path,
    ):
        """🔴 THE ONE-CELL FLIP. Before it, this row took the cover-is-a-MASK refusal.

        ``is_within`` is INCLUSIVE, so at the exact point the mask IS the longest-prefix
        cover and the same arm fired that serves mask-as-parent — refusing the FILE case
        the table accepts.

        MUTATION-PROVED against dropping the ``_synced_masks_replaced`` call from
        ``_apply_synced_copies``: no file lands, the mask stays in the map, and the
        cover-is-a-MASK warning comes back — the three things this asserts.
        """
        src = tmp_path / "cred.json"
        src.write_text("TOKEN")
        proj = resolve_project(std, config, str(project_dir), initialize=True)

        with caplog.at_level(logging.WARNING):
            bindings = _sync_over_the_launch_map(
                std, proj, logger=logging.getLogger("sync-consumer"),
                extra_default_categories={
                    "box.masks": ["~/cred.json"],
                    "box.synced": {"~/cred.json": (str(src),)},
                },
            )

        assert (proj.shell_path / "cred.json").read_text() == "TOKEN"
        assert _bind_map_masks(bindings) == []
        assert not [
            r.getMessage() for r in caplog.records if "is a mask" in r.getMessage()
        ]

    def test_a_synced_DIRECTORY_at_a_masks_own_point_is_STILL_REFUSED(
        self, caplog, std, config, project_dir, tmp_path,
    ):
        """The other half of the cell — and a mask survives a refusal INTACT.

        A directory copied onto a void would leave the mask partially populated, which
        is a state the table has no room for. The refusal is warn-and-skip, as every
        copy refusal here is.

        MUTATION-PROVED against ``_synced_masks_replaced`` asking ``exists()`` instead
        of ``is_file()``: the tree lands and the mask is gone.
        """
        src = tmp_path / "tree"
        (src / "nested").mkdir(parents=True)
        (src / "nested" / "f.txt").write_text("x")
        proj = resolve_project(std, config, str(project_dir), initialize=True)

        with caplog.at_level(logging.WARNING):
            bindings = _sync_over_the_launch_map(
                std, proj, logger=logging.getLogger("sync-consumer"),
                extra_default_categories={
                    "box.masks": ["~/tree"],
                    "box.synced": {"~/tree": (str(src),)},
                },
            )

        assert not (proj.shell_path / "tree").exists()
        assert _bind_map_masks(bindings) == [normalize_bind_dest("~/tree")]
        assert any("is a mask" in r.getMessage() for r in caplog.records), [
            r.getMessage() for r in caplog.records
        ]

    def test_a_mask_as_a_strict_PARENT_refuses_a_DIRECTORY_source_TOO(
        self, caplog, std, config, project_dir, tmp_path,
    ):
        """The PARENT column, whose two cells agree — the FILE half is pinned above it.

        ⚑ THE SIBLING IS THE POINT: ``test_a_dest_under_a_MASK_is_skipped_and_does_not_RAISE``
        pins the FILE source under a parent mask. Only the OWN-POINT column
        discriminates on the source's type, and this is what says so.

        MUTATION-PROVED against BOTH halves of the lookup relaxed at once —
        ``_synced_masks_replaced`` asking the longest-prefix COVER instead of the
        dest's own point, and ``exists()`` instead of ``is_file()``: the parent mask is
        deleted and the tree lands in the home bind's source. ⚑ Either half ALONE
        leaves this green (a directory is not a file; a parent is not the point), which
        is exactly why the two columns need a test each.
        """
        src = tmp_path / "tree"
        src.mkdir()
        (src / "f.txt").write_text("x")
        proj = resolve_project(std, config, str(project_dir), initialize=True)

        with caplog.at_level(logging.WARNING):
            bindings = _sync_over_the_launch_map(
                std, proj, logger=logging.getLogger("sync-consumer"),
                extra_default_categories={
                    "box.masks": ["~/private"],
                    "box.synced": {"~/private/tree": (str(src),)},
                },
            )

        assert not (proj.shell_path / "private").exists()
        assert _bind_map_masks(bindings) == [normalize_bind_dest("~/private")]
        assert any("is a mask" in r.getMessage() for r in caplog.records), [
            r.getMessage() for r in caplog.records
        ]

    def test_the_FILE_lands_in_the_INNERMOST_REAL_COVER_once_the_mask_is_gone(
        self, std, config, project_dir, tmp_path,
    ):
        """With the mask deleted the dest resolves through what REMAINS — the binds.

        Deleting the mask does not invent a destination: the row goes back through
        ``_synced_host_dest``'s ordinary longest-prefix cover, which is now the
        innermost REAL bind rather than home.

        MUTATION-PROVED twice: drop the deletion and the row takes the mask refusal
        again (no file anywhere), and flip ``_synced_host_dest``'s ``max(covers)`` to
        ``min`` and the file lands under HOME — inside the bind, where the box would
        never see it.
        """
        src = tmp_path / "f.txt"
        src.write_text("INNER")
        inner_root = tmp_path / "innerroot"
        proj = resolve_project(std, config, str(project_dir), initialize=True)

        bindings = _sync_over_the_launch_map(
            std, proj, logger=logging.getLogger("sync-consumer"),
            extra_default_categories={
                "box.bindings.rw": {"~/inner": (str(inner_root),)},
                "box.masks": ["~/inner/f.txt"],
                "box.synced": {"~/inner/f.txt": (str(src),)},
            },
        )

        assert (inner_root / "f.txt").read_text() == "INNER"
        assert not (proj.shell_path / "inner" / "f.txt").exists()
        assert _bind_map_masks(bindings) == []

    def test_the_mask_GOES_EVEN_WHEN_THE_MTIME_GATE_SKIPS_THE_COPY(
        self, std, config, project_dir, tmp_path,
    ):
        """⚑ CONTAINMENT IS DECIDED OVER THE DECLARATIONS, NOT OVER WHAT WAS WRITTEN.

        The launch refresh is mtime-gated, so a destination already newer than its
        source is a no-op — but the mask is replaced by the DECLARATION, and a mask
        that came back on the launches where the copy happened to be up to date would
        shadow the file on exactly those launches.

        MUTATION-PROVED against gating the deletion on the copy being WRITTEN (delete
        per row, and put the mask back when ``_synced_uptodate`` skips): the mask is
        handed to podman on exactly the launches where the sync had nothing to do.
        """
        import os

        src = tmp_path / "cred.json"
        src.write_text("OLD")
        proj = resolve_project(std, config, str(project_dir), initialize=True)
        landed = proj.shell_path / "cred.json"
        landed.parent.mkdir(parents=True, exist_ok=True)
        landed.write_text("THE BOX HAS THE NEWER ONE")
        os.utime(src, (1000, 1000))
        os.utime(landed, (2000, 2000))

        bindings = _sync_over_the_launch_map(
            std, proj, logger=logging.getLogger("sync-consumer"), gated=True,
            extra_default_categories={
                "box.masks": ["~/cred.json"],
                "box.synced": {"~/cred.json": (str(src),)},
            },
        )

        assert landed.read_text() == "THE BOX HAS THE NEWER ONE"
        assert _bind_map_masks(bindings) == []

    def test_THE_LAUNCH_HANDS_PODMAN_NO_TMPFS_AT_THE_REPLACED_POINT(
        self, start_mocks, tmp_path,
    ):
        """⚑⚑ THE PRODUCTION SEAM: ``_run_container`` → ``runtime.run(tmpfs_masks=…)``.

        The deletion is only worth anything if it reaches podman. It does so by ORDER
        alone — one map, both arms (2a-4), with the tmpfs arm read AFTER the sync pass
        that deletes from it. Nothing in the types enforces that, so this is the test
        that holds the line.

        The SECOND mask is the control: it keeps the argument a non-empty list, so a
        regression shows up as the WRONG list rather than as a falsy value that an
        absent key would also satisfy.

        ⚑ The COPY itself is stubbed out: ``start_mocks`` patches ``builtins.open``, so
        a real ``copy2`` here writes nothing and then dies in ``utime``. That costs the
        test nothing — the deletion is decided from the DECLARATION and the source
        STAT, both of them real above, and where the bytes land is pinned by the
        sibling tests against a REAL project.

        MUTATION-PROVED against putting ``tmpfs_masks = _bind_map_masks(launch_binds)`` above
        the ``_apply_synced_copies`` call and this reads
        ``["/home/agent/cred.json", "/home/agent/hidden"]`` — podman mounts a tmpfs
        over the credential the same launch just wrote.
        """
        from kanibako.commands.start import _run_container
        from kanibako.settings.store_collapse import (
            MASK,
            CollapsedBind,
            CollapsedCopy,
        )

        src = tmp_path / "cred.json"
        src.write_text("TOKEN")
        bindings = {
            "/home/agent": CollapsedBind(str(tmp_path / "boxhome"), "rw"),
            "/home/agent/cred.json": MASK,
            "/home/agent/hidden": MASK,
        }
        with start_mocks() as m, patch(
            "kanibako.commands.start._launch_bind_map", return_value=bindings,
        ), patch(
            "kanibako.commands.start._launch_synced_list",
            return_value=[CollapsedCopy(str(src), "/home/agent/cred.json", None)],
        ), patch("kanibako.commands.start._apply_shell_copy"):
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[],
            )
            kwargs = m.runtime.run.call_args.kwargs

        assert kwargs["tmpfs_masks"] == ["/home/agent/hidden"]
