"""DEST PARITY — a characterization oracle for the config engine's write/read sites.

⚑ WHAT THIS FILE IS FOR.  ``config_interface`` selects the FILE and the nested
slot a config value lands in (or is read from) by composing the same two rules in
thirteen places: the noun-file sentinel (``system_settings_path if not None else
<own file>``) and the scope-token gate.  The H2 refactor collapses those thirteen
copies into ONE ``_write_dest`` rule site.  This module pins the CURRENT
behavior of every (verb × command scope × key family) combination so the
collapse can be proven byte-for-byte behavior-preserving.

⚑ WHY IT IS EFFECT-BASED, NOT PATCH-BASED.  Monkeypatching the writer helpers
would bind these tests to symbols the de-bulk RENAMES and RE-ROUTES, so they
would have to be rewritten in the same commit whose correctness they exist to
prove — which is no oracle at all.  Instead every case runs the real verb against
real files in ``tmp_path`` and asserts WHICH FILE CHANGED and WHAT nested path
appeared in it.  Nothing here imports a private name; the whole module survives
the refactor unedited.  Keep it that way.

⚑ THIS FILE PINS BEHAVIOR, INCLUDING BEHAVIOR THAT IS KNOWN-WRONG.  Two cases
below assert a destination that is *deliberately broken* (see
``test_agent_scope_non_bind_category_*``).  They are not aspirational and they
are not bugs in the test: preserving them across the refactor is an explicit
requirement (playbook "H2 exemplar", `3b67e61`).  A future change that FIXES
them must edit the case and say so; a refactor that silently "fixes" them has
violated its charter.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from kanibako.settings.config_keys import ConfigLevel
from kanibako.settings.config_interface import (
    get_config_value,
    reset_all,
    reset_config_value,
    set_config_value,
    show_config,
)
from kanibako.settings.config_io import dump_doc, load_doc


# ---------------------------------------------------------------------------
# The bench — one distinct file per candidate destination
# ---------------------------------------------------------------------------

class Bench:
    """Every file the engine could possibly write, each distinguishable.

    The point of separate files is that "which file did it choose" is answerable
    by looking at the result, with no knowledge of how the choice was made.
    """

    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        self.cf = tmp / "kanibako_config.yaml"   # Layer-1 bootstrap config
        self.ssp = tmp / "global" / "settings.yaml"  # system SETTINGS (@config.settings)
        self.box = tmp / "box" / "settings.yaml"     # a box settings file
        self.ws = tmp / "ws" / "settings.yaml"       # a workset settings file
        self.agents = tmp / "agents"                 # the per-node store root
        self.env_sys = tmp / "env"
        self.env_box = tmp / "box" / "env"
        self.env_ws = tmp / "ws" / "env"
        for p in (self.ssp, self.box, self.ws):
            p.parent.mkdir(parents=True, exist_ok=True)

    # -- snapshotting -------------------------------------------------------

    def _files(self) -> dict[str, Path]:
        out = {
            "cf": self.cf, "ssp": self.ssp, "box": self.box, "ws": self.ws,
        }
        if self.agents.exists():
            for node_file in sorted(self.agents.glob("*/settings.yaml")):
                out[f"agent:{node_file.parent.name}"] = node_file
        return out

    def snapshot(self) -> dict[str, object]:
        return {label: load_doc(p) for label, p in self._files().items()}

    def changed(self, before: dict[str, object]) -> dict[str, object]:
        """``{label: new_doc}`` for every file whose content differs from *before*.

        A file that did not exist before and does not exist now contributes
        nothing (``load_doc`` maps both to ``{}``), so "created" and "modified"
        are one case — which is what a destination assertion cares about.
        """
        after = self.snapshot()
        return {
            label: doc for label, doc in after.items()
            if before.get(label, {}) != doc
        }

    def seed(self, path: Path, sections: tuple[str, ...], leaf: str, value) -> None:
        data = load_doc(path)
        node = data
        for sec in sections:
            node = node.setdefault(sec, {})
        node[leaf] = value
        dump_doc(path, data)

    # -- verb adapters: EXACTLY the kwargs each real command handler passes --

    def node_bind_route(self, key: str) -> "tuple[Path, str] | None":
        """``(node file, node)`` when *key* is a per-node descriptor bind.

        ⚑ Mirrors what ``system_cmd`` does BEFORE calling the engine: a node-bind
        set is aimed at the node's own settings file, with that file also threaded
        as the cascade's agent tier.  Reproduced here (by shape, with no private
        import) so the bench passes the parameters the real handler passes —
        otherwise this file would be testing a call that never happens.
        """
        m = re.match(r"^agent\.(?P<node>[^.]+)\.bindings\.(?:ro|rw)\.", key)
        if m is None:
            return None
        node = m.group("node")
        f = self.agents / node / "settings.yaml"
        f.parent.mkdir(parents=True, exist_ok=True)
        return f, node

    def set(self, scope: ConfigLevel, key: str, value, **extra):
        if scope is ConfigLevel.system:
            node_route = self.node_bind_route(key)
            if node_route is not None and "config_path" not in extra:
                node_file, node = node_route
                extra = {
                    **extra, "config_path": node_file,
                    "cascade_agent_path": node_file, "cascade_agent_name": node,
                }
            extra.setdefault("config_path", self.cf)
            return set_config_value(
                key, value, env_path=self.env_sys,
                is_system=True, system_settings_path=self.ssp,
                cascade_system_path=self.ssp,
                command_scope=ConfigLevel.system, agents_root=self.agents,
                **extra,
            )
        if scope is ConfigLevel.workset:
            return set_config_value(
                key, value, config_path=self.ws, env_path=self.env_ws,
                cascade_system_path=self.ssp, cascade_workset_path=self.ws,
                command_scope=ConfigLevel.workset, **extra,
            )
        return set_config_value(
            key, value, config_path=self.box, env_path=self.env_box,
            cascade_system_path=self.ssp, cascade_workset_path=self.ws,
            cascade_box_path=self.box,
            command_scope=ConfigLevel.box, **extra,
        )

    def reset(self, scope: ConfigLevel, key: str, **extra):
        if scope is ConfigLevel.system:
            return reset_config_value(
                key, config_path=self.cf, env_path=self.env_sys,
                system_settings_path=self.ssp,
                command_scope=ConfigLevel.system,
                cascade_system_path=self.ssp, agents_root=self.agents,
                **extra,
            )
        if scope is ConfigLevel.workset:
            return reset_config_value(
                key, config_path=self.ws, env_path=self.env_ws,
                command_scope=ConfigLevel.workset,
                cascade_system_path=self.ssp, cascade_workset_path=self.ws,
                **extra,
            )
        return reset_config_value(
            key, config_path=self.box, env_path=self.env_box,
            command_scope=ConfigLevel.box,
            cascade_system_path=self.ssp, cascade_workset_path=self.ws,
            cascade_box_path=self.box, **extra,
        )

    def get(self, scope: ConfigLevel, key: str, **extra):
        if scope is ConfigLevel.system:
            # ⚑ THE PRE-LOCKSTEP SCOPE-LESS ARM, KEPT DELIBERATELY.  The system
            # handler now DOES thread ``command_scope`` (the H2 lockstep), so this
            # is no longer a copy of it — it is the call shape that existed before,
            # retained because the rule site still has to answer identically
            # without a scope: every direct caller in the suite omits it, and
            # ``TestSystemGetCommandScopeIsInert`` below pins the two against each
            # other.  Reading this as "what system_cmd does" would be wrong; it is
            # "what the engine must still tolerate".
            return get_config_value(
                key, global_config_path=self.cf, env_global=self.env_sys,
                system_settings_path=self.ssp, agents_root=self.agents, **extra,
            )
        if scope is ConfigLevel.workset:
            return get_config_value(
                key, global_config_path=self.cf, project_toml=self.ws,
                env_global=self.env_sys, env_project=self.env_ws,
                command_scope=ConfigLevel.workset, **extra,
            )
        return get_config_value(
            key, global_config_path=self.cf, project_toml=self.box,
            env_global=self.env_sys, env_project=self.env_box,
            command_scope=ConfigLevel.box, **extra,
        )


@pytest.fixture
def bench(tmp_path: Path) -> Bench:
    return Bench(tmp_path)


# A category tuple that already exists in the cascade, so the F10 must-exist
# gate passes and the repoint is exercised (rather than refused).
_TUPLE = ["/old/src", "/home/agent/dest", "ro"]


# ---------------------------------------------------------------------------
# 1. Routed scalar keys — the ``_KEY_ROUTES`` family
# ---------------------------------------------------------------------------

class TestRoutedScalarDest:

    @pytest.mark.parametrize("scope,expect", [
        (ConfigLevel.box, "box"),
        (ConfigLevel.workset, "ws"),
        (ConfigLevel.system, "ssp"),
    ])
    def test_set_dotted_scope_key(self, bench, scope, expect):
        """A scope-prefixed routed key lands in the noun's SETTINGS file."""
        before = bench.snapshot()
        msg = bench.set(scope, "box.image", "img:1")
        assert not msg.startswith("Error:"), msg
        changed = bench.changed(before)
        assert set(changed) == {expect}, changed
        assert changed[expect]["box"]["image"] == "img:1"

    def test_set_flat_spelling_at_system_lands_in_config_file(self, bench):
        """⚑ PINNED ASYMMETRY, not an endorsement.

        The dest gate tests the token of the key AS TYPED, while the routing
        table lookup uses the canonicalised spelling — so at SYSTEM scope the
        flat form ``box_image`` (whose first dotted token is the whole string,
        not ``box``) falls to the CONFIG file while ``box.image`` goes to the
        SETTINGS file.  At box/workset the two files are the same path, so the
        divergence is invisible there.  Recorded so the H2 collapse reproduces
        it rather than accidentally normalising it.
        """
        before = bench.snapshot()
        msg = bench.set(ConfigLevel.system, "box_image", "img:flat")
        assert not msg.startswith("Error:"), msg
        changed = bench.changed(before)
        assert set(changed) == {"cf"}, changed
        assert changed["cf"]["box"]["image"] == "img:flat"

    @pytest.mark.parametrize("scope,dest", [
        (ConfigLevel.box, "box"),
        (ConfigLevel.workset, "ws"),
        (ConfigLevel.system, "ssp"),
    ])
    def test_reset_removes_from_the_file_set_wrote(self, bench, scope, dest):
        bench.set(scope, "box.image", "img:1")
        before = bench.snapshot()
        msg = bench.reset(scope, "box.image")
        assert not msg.startswith("Error:"), msg
        assert msg.startswith("Cleared"), msg
        changed = bench.changed(before)
        assert set(changed) == {dest}, changed
        assert "image" not in changed[dest].get("box", {})

    def test_get_reads_the_settings_file_not_the_config_file(self, bench):
        """Read-side parity: seed BOTH candidates differently and see which wins."""
        bench.seed(bench.cf, ("box",), "image", "FROM_CF")
        bench.seed(bench.ssp, ("box",), "image", "FROM_SSP")
        assert bench.get(ConfigLevel.system, "box.image") == "FROM_SSP"

    def test_get_at_box_reads_the_box_file(self, bench):
        bench.seed(bench.cf, ("box",), "image", "FROM_CF")
        bench.seed(bench.box, ("box",), "image", "FROM_BOX")
        assert bench.get(ConfigLevel.box, "box.image") == "FROM_BOX"


# ---------------------------------------------------------------------------
# 2. Bare agent behaviour keys (``agent.default`` tier)
# ---------------------------------------------------------------------------

class TestBareAgentKeyDest:

    def test_set_at_system_lands_in_settings_agent_default(self, bench):
        before = bench.snapshot()
        msg = bench.set(ConfigLevel.system, "model", "opus")
        assert not msg.startswith("Error:"), msg
        changed = bench.changed(before)
        assert set(changed) == {"ssp"}, changed
        assert changed["ssp"]["agent"]["default"]["model"] == "opus"

    @pytest.mark.parametrize("scope", [ConfigLevel.box, ConfigLevel.workset])
    def test_write_refused_below_the_agent_tier_touches_nothing(self, bench, scope):
        before = bench.snapshot()
        msg = bench.set(scope, "model", "opus")
        assert msg.startswith("Error:"), msg
        assert bench.changed(before) == {}

    def test_get_at_system_reads_the_settings_file(self, bench):
        bench.seed(bench.ssp, ("agent", "default"), "model", "FROM_SSP")
        assert bench.get(ConfigLevel.system, "model") == "FROM_SSP"

    def test_reset_at_system_clears_the_settings_file(self, bench):
        bench.set(ConfigLevel.system, "model", "opus")
        before = bench.snapshot()
        msg = bench.reset(ConfigLevel.system, "model")
        assert msg.startswith("Cleared"), msg
        changed = bench.changed(before)
        assert set(changed) == {"ssp"}, changed


# ---------------------------------------------------------------------------
# 3. ``<scope>.secret_path.<VAR>`` — the non-agent SECRET category
# ---------------------------------------------------------------------------

class TestScopeSecretDest:

    @pytest.mark.parametrize("scope,key,dest", [
        (ConfigLevel.box, "box.secret_path.TOKEN", "box"),
        (ConfigLevel.workset, "workset.secret_path.TOKEN", "ws"),
        (ConfigLevel.system, "system.secret_path.TOKEN", "ssp"),
    ])
    def test_set_get_reset_all_name_one_file(self, bench, scope, key, dest):
        before = bench.snapshot()
        msg = bench.set(scope, key, "/host/secret")
        assert not msg.startswith("Error:"), msg
        changed = bench.changed(before)
        assert set(changed) == {dest}, changed
        scope_token = key.split(".", 1)[0]
        assert changed[dest][scope_token]["secret_path"]["TOKEN"] == "/host/secret"

        # get reads exactly where set wrote (the `3b67e61` symmetry).
        assert bench.get(scope, key) == "/host/secret"

        before = bench.snapshot()
        assert bench.reset(scope, key).startswith("Cleared")
        assert set(bench.changed(before)) == {dest}


# ---------------------------------------------------------------------------
# 4. Path-tuple CATEGORY keys
# ---------------------------------------------------------------------------

class TestCategoryDest:
    """The per-entry bind-shaped category families — now READ-ONLY at every scope.

    ⚑⚑ THE WRITE ROWS ARE GONE, ON A RULING. This class used to pin "set repoints in
    the noun settings file" / "reset removes from the same file" for
    ``box.synced.vault`` / ``workset.caches.pip`` / ``system.caches.pip``. DS-BL1 =
    (a) (Jei, 2026-08-07g) retired the ``config set`` route for EVERY bind-shaped
    category, so those verbs now refuse by name and touch nothing — pinned below
    with the SAME bench (the point of the bench is that a refusal moves no file).
    The surviving question is the READ slot, which is unchanged.
    """

    @pytest.mark.parametrize("scope,key,dest", [
        (ConfigLevel.box, "box.synced.vault", "box"),
        (ConfigLevel.workset, "workset.caches.pip", "ws"),
        (ConfigLevel.system, "system.caches.pip", "ssp"),
    ])
    def test_set_and_reset_refuse_and_touch_no_file(self, bench, scope, key, dest):
        parts = key.split(".")
        target = {"box": bench.box, "ws": bench.ws, "ssp": bench.ssp}[dest]
        bench.seed(target, tuple(parts[:-1]), parts[-1], list(_TUPLE))
        before = bench.snapshot()
        set_msg = bench.set(scope, key, str(bench.tmp))
        reset_msg = bench.reset(scope, key)
        assert set_msg.startswith("Error:") and "RETIRED" in set_msg, set_msg
        assert reset_msg.startswith("Error:") and "RETIRED" in reset_msg, reset_msg
        # ⚑ NO file moved — the bench's whole purpose. RED if a refusal still wrote.
        assert bench.changed(before) == {}, bench.changed(before)

    def test_get_reads_the_noun_settings_file(self, bench):
        bench.seed(bench.cf, ("system", "caches"), "pip", ["/cf", "/d"])
        bench.seed(bench.ssp, ("system", "caches"), "pip", ["/ssp", "/d"])
        assert "/ssp" in (bench.get(ConfigLevel.system, "system.caches.pip") or "")


class TestRetiredScopeBindDest:
    """R-9 — ``{system,workset,box}.bindings.{ro,rw}.<name>`` keeps a READ slot and
    loses its WRITE route. The asymmetry is deliberate and is what the parity
    bench exists to make visible rather than accidental."""

    def test_get_still_finds_the_noun_settings_file(self, bench):
        bench.seed(bench.box, ("box", "bindings", "ro"), "vault", list(_TUPLE))
        got = bench.get(ConfigLevel.box, "box.bindings.ro.vault") or ""
        assert _TUPLE[0] in got, got

    def test_set_and_reset_refuse_and_touch_no_file(self, bench):
        bench.seed(bench.box, ("box", "bindings", "ro"), "vault", list(_TUPLE))
        before = bench.snapshot()
        set_msg = bench.set(ConfigLevel.box, "box.bindings.ro.vault", str(bench.tmp))
        reset_msg = bench.reset(ConfigLevel.box, "box.bindings.ro.vault")
        assert set_msg.startswith("Error:") and "RETIRED" in set_msg, set_msg
        assert reset_msg.startswith("Error:") and "RETIRED" in reset_msg, reset_msg
        # ⚑ The bench's whole point: NO file moved. RED if a refusal still wrote.
        assert bench.changed(before) == {}, bench.changed(before)


# ---------------------------------------------------------------------------
# 5. ⚑ THE PRESERVED-BROKEN DESTINATION (agent-scope, non-bind category)
# ---------------------------------------------------------------------------

class TestAgentScopeNonBindCategoryIsDeliberatelyBroken:
    """⚑⚑⚑ THE PRESERVED-BROKEN DESTINATION IS UNREACHABLE FROM THE VERBS — it died
    by its ROUTE being retired, not by being fixed.

    The defect: a non-bind AGENT-scope category was routed by NO handler, so ``set``
    landed in the command's own Layer-1 CONFIG file (in no cascade level — a SILENT
    NO-OP WRITE) while ``get`` read the NOUN's settings file. The two verbs
    disagreed, and `3b67e61` recorded fixing it as its own future change rather than
    doing it inside a behavior-preserving commit.

    DS-BL1 = (a) removed the write route for every bind-shaped category, so no
    ``_CATEGORY`` slot reaches ``_write_dest`` any more and there is no write left to
    disagree with the read. This class now pins BOTH halves of that statement, so
    neither can change silently:

    1. the VERBS refuse and move nothing (the write half is gone);
    2. the ARM still exists in ``_write_dest`` as a function (kept with its
       ``agent_scope_to_config`` flag and this bench's read/write parity, because
       collapsing it merges the two wrappers — an OWED, separate pass).

    ⚑ When that collapse lands, THIS CLASS is the thing to edit, deliberately, in
    that commit. It must never change as a side effect.
    """

    def test_the_verbs_refuse_and_move_nothing(self, bench):
        bench.seed(bench.cf, ("agent", "claude", "caches"), "pip", list(_TUPLE))
        before = bench.snapshot()
        set_msg = bench.set(
            ConfigLevel.system, "agent.claude.caches.pip", str(bench.tmp),
        )
        reset_msg = bench.reset(ConfigLevel.system, "agent.claude.caches.pip")
        assert set_msg.startswith("Error:") and "RETIRED" in set_msg, set_msg
        assert reset_msg.startswith("Error:") and "RETIRED" in reset_msg, reset_msg
        # The wrong file is not written, and neither is the node's own file.
        assert bench.changed(before) == {}, bench.changed(before)
        assert not (bench.agents / "claude" / "settings.yaml").exists()

    def test_the_broken_arm_still_exists_at_the_function_level(self, bench):
        """The arm itself, called directly — the read/write divergence the two
        wrappers still carry. Unreachable through a verb; NOT yet deleted."""
        from kanibako.settings.config_dest import _read_dest, _write_dest

        key = "agent.claude.caches.pip"
        w = _write_dest(
            key, command_scope=ConfigLevel.system,
            config_path=bench.cf, settings_path=bench.ssp,
        )
        r = _read_dest(
            key, command_scope=ConfigLevel.system,
            config_path=bench.cf, settings_path=bench.ssp,
        )
        assert w is not None and r is not None
        assert w.file == bench.cf          # the file no cascade level reads
        assert r.file == bench.ssp         # the noun's settings file
        assert w.sections == r.sections == ("agent", "claude", "caches")
        assert w.leaf == r.leaf == "pip"

    def test_get_reads_the_noun_settings_file(self, bench):
        """The surviving arm. Seeding BOTH candidates with different values makes
        the assertion name a FILE rather than a value: only the settings-file value
        can come back."""
        bench.seed(bench.cf, ("agent", "claude", "caches"), "pip", "FROM_CF")
        bench.seed(bench.ssp, ("agent", "claude", "caches"), "pip", "FROM_SSP")
        assert bench.get(ConfigLevel.system, "agent.claude.caches.pip") == "FROM_SSP"


# ---------------------------------------------------------------------------
# 6. The per-node agent families — persona / node bind / node secret
# ---------------------------------------------------------------------------

class TestPerNodeAgentDest:

    def test_persona_state_leaf(self, bench):
        before = bench.snapshot()
        msg = bench.set(ConfigLevel.system, "agent.claude.model", "opus")
        assert not msg.startswith("Error:"), msg
        changed = bench.changed(before)
        assert set(changed) == {"agent:claude"}, changed
        assert changed["agent:claude"]["self"]["model"] == "opus"
        assert bench.get(ConfigLevel.system, "agent.claude.model") == "opus"

        before = bench.snapshot()
        assert bench.reset(ConfigLevel.system, "agent.claude.model").startswith(
            "Cleared"
        )
        assert set(bench.changed(before)) == {"agent:claude"}

    def test_node_secret_pointer(self, bench):
        before = bench.snapshot()
        msg = bench.set(
            ConfigLevel.system, "agent.claude.secret_path.TOKEN", "/host/tok",
        )
        assert not msg.startswith("Error:"), msg
        changed = bench.changed(before)
        assert set(changed) == {"agent:claude"}, changed
        assert (
            changed["agent:claude"]["self"]["secret_path"]["TOKEN"] == "/host/tok"
        )
        assert bench.get(
            ConfigLevel.system, "agent.claude.secret_path.TOKEN",
        ) == "/host/tok"

    def test_node_descriptor_bind_keeps_its_read_and_loses_its_write(self, bench):
        """R-9 — ``agent.<node>.bindings.{ro,rw}.<name>`` keeps a READ slot in the
        node's own file and loses its WRITE route. The asymmetry is deliberate and
        is what the parity bench exists to make visible rather than accidental.
        (This test asserted the repoint WROTE, until the route was retired.)"""
        node_file = bench.agents / "claude" / "settings.yaml"
        node_file.parent.mkdir(parents=True, exist_ok=True)
        bench.seed(
            node_file, ("self", "claude", "bindings", "ro"), "launcher",
            list(_TUPLE),
        )
        # The READ finds the hand-authored tuple in the node file.
        got = bench.get(
            ConfigLevel.system, "agent.claude.bindings.ro.launcher",
        ) or ""
        assert _TUPLE[0] in got, got

        before = bench.snapshot()
        set_msg = bench.set(
            ConfigLevel.system, "agent.claude.bindings.ro.launcher", str(bench.tmp),
        )
        reset_msg = bench.reset(
            ConfigLevel.system, "agent.claude.bindings.ro.launcher",
        )
        assert set_msg.startswith("Error:") and "RETIRED" in set_msg, set_msg
        assert reset_msg.startswith("Error:") and "RETIRED" in reset_msg, reset_msg
        # ⚑ The bench's whole point: NO file moved. RED if a refusal still wrote.
        assert bench.changed(before) == {}, bench.changed(before)

    @pytest.mark.parametrize("key", [
        "agent.claude.model",
        "agent.claude.secret_path.TOKEN",
        "agent.claude.bindings.ro.launcher",
    ])
    def test_refused_from_box_scope_touches_nothing(self, bench, key):
        before = bench.snapshot()
        msg = bench.set(ConfigLevel.box, key, "/x")
        assert msg.startswith("Error:"), msg
        assert bench.changed(before) == {}


# ---------------------------------------------------------------------------
# 7. ``pref.*`` requests
# ---------------------------------------------------------------------------

class TestPrefDest:

    @pytest.mark.parametrize("scope,dest", [
        (ConfigLevel.box, "box"),
        (ConfigLevel.workset, "ws"),
    ])
    def test_set_and_reset_use_the_noun_settings_file(self, bench, scope, dest):
        before = bench.snapshot()
        msg = bench.set(scope, "pref.agent.claude.model", "opus")
        assert not msg.startswith("Error:"), msg
        changed = bench.changed(before)
        assert set(changed) == {dest}, changed
        assert changed[dest]["pref"]["agent"]["claude"]["model"] == "opus"
        assert bench.get(scope, "pref.agent.claude.model") == "opus"

        before = bench.snapshot()
        assert bench.reset(scope, "pref.agent.claude.model").startswith("Cleared")
        assert set(bench.changed(before)) == {dest}

    def test_refused_at_system_scope_touches_nothing(self, bench):
        before = bench.snapshot()
        msg = bench.set(ConfigLevel.system, "pref.agent.claude.model", "opus")
        assert msg.startswith("Error:"), msg
        assert bench.changed(before) == {}


# ---------------------------------------------------------------------------
# 8. File-only ``system.*`` path keys — refused, nothing written
# ---------------------------------------------------------------------------

class TestSystemPathKeyRefusal:

    @pytest.mark.parametrize("key", ["system.cache", "config.data"])
    def test_set_and_reset_refuse_and_write_nothing(self, bench, key):
        before = bench.snapshot()
        assert bench.set(ConfigLevel.system, key, "/x").startswith("Error:")
        assert bench.reset(ConfigLevel.system, key).startswith("Error:")
        assert bench.changed(before) == {}

    def test_get_reads_the_config_file(self, bench):
        bench.seed(bench.cf, ("system",), "cache", "/from/cf")
        assert bench.get(ConfigLevel.system, "system.cache") == "/from/cf"


# ---------------------------------------------------------------------------
# 9. ``reset --all`` and ``show`` — the same file selection
# ---------------------------------------------------------------------------

class TestResetAllAndShowDest:

    def test_reset_all_at_system_clears_the_settings_file_scope_tables(self, bench):
        bench.seed(bench.ssp, ("box",), "image", "img:1")
        bench.seed(bench.ssp, ("agent", "default"), "model", "opus")
        bench.seed(bench.cf, ("system",), "cache", "/keep/me")
        before = bench.snapshot()
        msg = reset_all(
            config_path=bench.cf, env_path=bench.env_sys, force=True,
            system_settings_path=bench.ssp, command_scope=ConfigLevel.system,
        )
        assert msg.startswith("Reset"), msg
        changed = bench.changed(before)
        assert set(changed) == {"ssp"}, changed
        assert "box" not in changed["ssp"]
        # The Layer-1 config file is NOT swept.
        assert load_doc(bench.cf)["system"]["cache"] == "/keep/me"

    def test_reset_all_at_box_clears_the_box_file(self, bench):
        bench.seed(bench.box, ("box",), "image", "img:1")
        before = bench.snapshot()
        msg = reset_all(
            config_path=bench.box, env_path=bench.env_box, force=True,
            command_scope=ConfigLevel.box,
        )
        assert msg.startswith("Reset"), msg
        assert set(bench.changed(before)) == {"box"}

    def test_show_at_system_renders_the_settings_file(self, bench, capsys):
        bench.seed(bench.ssp, ("agent", "default"), "model", "FROM_SSP")
        show_config(
            global_config_path=bench.cf, config_path=bench.cf,
            system_settings_path=bench.ssp,
        )
        assert "FROM_SSP" in capsys.readouterr().out

    def test_show_at_box_renders_the_box_file(self, bench, capsys):
        bench.seed(bench.box, ("agent", "default"), "model", "FROM_BOX")
        show_config(global_config_path=bench.cf, config_path=bench.box)
        assert "FROM_BOX" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# 10. The system-scope ``get`` / ``command_scope`` inertness proof
# ---------------------------------------------------------------------------

class TestSystemGetCommandScopeIsInert:
    """``system_cmd`` calls ``get`` WITHOUT ``command_scope``; box/workset pass it.

    The H2 design makes ``command_scope`` explicit at the rule site, which means
    the system handler must start threading it.  That is only safe if passing it
    changes nothing — ``get`` consumes ``command_scope`` for the box-scope
    redirect and nothing else.  These cases prove it for every family, so the
    lockstep edit to ``system_cmd.py`` is covered BEFORE it is made.
    """

    @pytest.mark.parametrize("key,seed", [
        ("box.image", (lambda b: b.seed(b.ssp, ("box",), "image", "v"))),
        ("model", (lambda b: b.seed(b.ssp, ("agent", "default"), "model", "v"))),
        ("system.secret_path.TOKEN",
         (lambda b: b.seed(b.ssp, ("system", "secret_path"), "TOKEN", "v"))),
        ("system.caches.pip",
         (lambda b: b.seed(b.ssp, ("system", "caches"), "pip", "v"))),
        ("system.cache", (lambda b: b.seed(b.cf, ("system",), "cache", "v"))),
        ("agent.claude.model",
         (lambda b: b.seed(
             b.agents / "claude" / "settings.yaml",
             ("self",), "model", "v"))),
    ])
    def test_threading_the_scope_changes_no_read(self, bench, key, seed):
        seed(bench)
        without = get_config_value(
            key, global_config_path=bench.cf, env_global=bench.env_sys,
            system_settings_path=bench.ssp, agents_root=bench.agents,
        )
        with_scope = get_config_value(
            key, global_config_path=bench.cf, env_global=bench.env_sys,
            system_settings_path=bench.ssp, agents_root=bench.agents,
            command_scope=ConfigLevel.system,
        )
        assert without == with_scope
        assert without is not None
