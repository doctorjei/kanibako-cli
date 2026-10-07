"""Tests for target discovery and resolution."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from kanibako.agent_ref import PSEUDO_AGENT_NAMES
from kanibako.targets import discover_targets, get_target, resolve_target
from kanibako.targets.base import (
    AgentInstall,
    BindKind,
    Binding,
    BindScope,
    HostSrcOrigin,
    PluginDescriptor,
    Target,
)
from kanibako.targets.shell import ShellTarget

from tests.e2e._entry_point_plugin import install_entry_point_plugin
from tests.support.repo import REPO_ROOT


def _isolate_config(tmp_path, monkeypatch) -> None:
    """Pin the CONFIG side of the user plugin dir, which is ``config.data``/plugins.

    ⚑ ``XDG_DATA_HOME`` alone does not determine where a ``config.data`` dir resolves
    ([R155]), so an unisolated ``XDG_CONFIG_HOME`` — or the site base under ``/etc`` —
    would let whatever config happens to be on the box running the suite move it away
    from the one these tests write into.  The one class that needs this floor
    (``TestFileDropDirectoriesAreNotADiscoveryRoute``) keeps an ``autouse`` wrapper so
    the floor stays scoped to the tests that write plugin files.
    """
    import kanibako.settings.config as cfg_mod

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setattr(cfg_mod, "config_base_path", lambda: tmp_path / "etc_absent.cfg")


def _minimal_descriptor(command: tuple[str, ...] = ("fake-bin",)) -> PluginDescriptor:
    """The smallest descriptor a REGISTRABLE plugin can declare.

    One shared shape for every fixture in this file.  ``targets._register`` refuses a
    plugin whose ``descriptor`` is None (the plugin system is descriptor-only,
    keyspec §2d), so a fixture that wants to test a different refusal — a reserved
    name, a case collision — has to be a well-shaped plugin FIRST, or it would be
    skipped by the shape gate before the gate under test ever saw it.  Field-for-field
    the shape of ``test_plugin_descriptor_frozen`` in ``tests/test_targets/test_base.py``.
    """
    return PluginDescriptor(
        command=command,
        bindings=(
            Binding(
                key="binary",
                origin=HostSrcOrigin.BINARY,
                box_dest="/usr/local/bin/fake-bin",
                kind=BindKind.FILE,
                scope=BindScope.AGENT_CRITICAL,
            ),
        ),
        mode={"start": ()},
    )


class _FakeTarget(Target):
    """Minimal concrete Target for testing."""

    _detect_result: AgentInstall | None = None

    @property
    def name(self) -> str:
        return "fake"

    @property
    def display_name(self) -> str:
        return "Fake Agent"

    @property
    def descriptor(self) -> PluginDescriptor:
        return _minimal_descriptor()

    @property
    def default_entrypoint(self) -> str | None:
        return "fake-bin"

    def detect(self):
        return self._detect_result

    def binary_mounts(self, install):
        return []

    def refresh_credentials(self, home):
        pass

    def writeback_credentials(self, home):
        pass

    def build_cli_args(self, **kwargs):
        return []


class _DetectableTarget(_FakeTarget):
    """Target whose detect() returns a valid install."""

    @property
    def name(self) -> str:
        return "detectable"

    @property
    def display_name(self) -> str:
        return "Detectable Agent"

    def detect(self):
        return AgentInstall(name="detectable", binary=Path("/bin/x"), install_dir=Path("/opt/x"))


class _NoNameTarget(_FakeTarget):
    """Target with an empty meta.agent.<agent>.name (invalid — has no store dir)."""

    @property
    def name(self) -> str:
        return ""

    def detect(self):
        return None


class _ReservedNameTarget(_FakeTarget):
    """Target claiming a RESERVED pseudo-agent name (keyspec §2d)."""

    @property
    def name(self) -> str:
        return "shell"

    def detect(self):
        return None


def _mock_entry_point(name: str, cls: type) -> MagicMock:
    ep = MagicMock()
    ep.name = name
    ep.load.return_value = cls
    return ep


class TestDiscoverTargets:
    def test_discovers_registered_targets(self):
        ep = _mock_entry_point("fake", _FakeTarget)
        with patch("kanibako.targets.entry_points", return_value=[ep]):
            targets = discover_targets()
        assert "fake" in targets
        assert targets["fake"] is _FakeTarget

    def test_empty_when_no_targets(self):
        # ⚑ Never TRULY empty (D2): the ``shell`` built-in is SEEDED, not
        # discovered, so the registry always carries it ([R175]).
        with patch("kanibako.targets.entry_points", return_value=[]):
            targets = discover_targets()
        assert targets == {"shell": ShellTarget}

    def test_multiple_targets(self):
        ep1 = _mock_entry_point("a", _FakeTarget)
        ep2 = _mock_entry_point("b", _DetectableTarget)
        with patch("kanibako.targets.entry_points", return_value=[ep1, ep2]):
            targets = discover_targets()
        assert len(targets) == 3  # a, b, plus the seeded shell built-in
        assert "a" in targets
        assert "b" in targets
        assert targets["shell"] is ShellTarget


class TestBrokenEntryPointIsSkipped:
    """A plugin that cannot IMPORT must not take the whole CLI down with it.

    Regression, measured 2026-08-17: a stale ``kanibako-agent-goose`` wheel raised
    ``ImportError: cannot import name 'BindDefault'`` from its own module body.
    ``ep.load()`` was unguarded, so that escaped ``discover_targets`` as a raw
    traceback and killed every command that resolves an agent — including
    ``kanibako setup``, which calls discovery too, so the documented cure was
    unreachable and hand-editing site-packages was the only way back in.
    """

    @staticmethod
    def _broken_entry_point(name: str, exc: Exception) -> MagicMock:
        ep = MagicMock()
        ep.name = name
        ep.load.side_effect = exc
        return ep

    @pytest.fixture(autouse=True)
    def _clear_warn_dedupe(self):
        # The warning is once-per-process, so reset the memo or test order decides
        # whether a later test sees the message.
        from kanibako.targets import _EP_LOAD_FAILED
        _EP_LOAD_FAILED.clear()
        yield
        _EP_LOAD_FAILED.clear()

    def test_broken_plugin_does_not_abort_discovery(self):
        broken = self._broken_entry_point(
            "goose",
            ImportError("cannot import name 'BindDefault' from 'kanibako.targets.base'"),
        )
        good = _mock_entry_point("fake", _FakeTarget)
        with patch("kanibako.targets.entry_points", return_value=[broken, good]):
            targets = discover_targets()
        # The healthy agent survives; the broken one is simply absent.
        assert targets["fake"] is _FakeTarget
        assert "goose" not in targets

    def test_broken_plugin_is_reported_on_stderr_with_the_cure(self, capsys):
        broken = self._broken_entry_point("goose", ImportError("cannot import name 'BindDefault'"))
        with patch("kanibako.targets.entry_points", return_value=[broken]):
            discover_targets()
        err = capsys.readouterr().err
        # Named, not swallowed: a pip-installed adapter that cannot load is a
        # broken install the user has to know about.
        assert "goose" in err
        assert "SKIPPED" in err
        assert "ImportError" in err
        assert "BindDefault" in err
        # And it says what still works, so the user does not conclude the CLI is dead.
        assert "kanibako setup" in err

    def test_warning_is_emitted_once_per_process(self, capsys):
        broken = self._broken_entry_point("goose", ImportError("boom"))
        with patch("kanibako.targets.entry_points", return_value=[broken]):
            discover_targets()
            discover_targets()
            discover_targets()
        # discover_targets runs several times per command; the paragraph must not
        # be repeated each time or it buries the real output.
        assert capsys.readouterr().err.count("failed to load") == 1

    def test_a_plugin_raising_a_non_import_error_is_also_survived(self):
        """The guard is deliberately broad: any exception from third-party code."""
        broken = self._broken_entry_point("exploding", RuntimeError("bad metaclass"))
        good = _mock_entry_point("fake", _FakeTarget)
        with patch("kanibako.targets.entry_points", return_value=[broken, good]):
            targets = discover_targets()
        assert "fake" in targets
        assert "exploding" not in targets


class TestReservedPseudoAgentNameIsRefused:
    """A plugin may not register a PSEUDO-AGENT name (keyspec §2d).

    *"Their names are RESERVED and MUST be refused to any agent, persona, or harness."*
    ``default`` and ``shell`` already own an ``agent.<name>.*`` cascade slot and a store
    dir in the keyspace itself, so a harness answering to one would own them too.
    Before 2026-09-18 ``discover_targets`` keyed straight off ``ep.name`` with no name
    validation at all.
    """

    @pytest.fixture(autouse=True)
    def _clear_warn_dedupe(self):
        # Once-per-process, like the load-failure warning — reset it or test order
        # decides whether a later test sees the message.
        from kanibako.targets import _RESERVED_NAME_WARNED
        _RESERVED_NAME_WARNED.clear()
        yield
        _RESERVED_NAME_WARNED.clear()

    def test_the_reserved_set_is_not_empty(self):
        # The sweep below is parametrized over the set; an empty one would pass
        # vacuously.  The oracle is the spec's own two subheadings.
        assert PSEUDO_AGENT_NAMES == {"default", "shell"}

    @pytest.mark.parametrize("name", sorted(PSEUDO_AGENT_NAMES))
    def test_entry_point_with_a_reserved_name_is_skipped(self, name):
        bad = _mock_entry_point(name, _FakeTarget)
        good = _mock_entry_point("fake", _FakeTarget)
        with patch("kanibako.targets.entry_points", return_value=[bad, good]):
            targets = discover_targets()
        # SKIPPED, not fatal: discovery runs on every command, so one plugin's bad
        # name must not take the CLI down with it.
        assert targets["fake"] is _FakeTarget
        if name == "shell":
            # ⚑ The SEEDED built-in owns this slot ([R175]) — the rogue plugin
            # neither registers under it nor displaces the owner.
            assert targets["shell"] is ShellTarget
        else:
            assert name not in targets

    def test_the_refusal_names_the_name_and_says_it_was_skipped(self, capsys):
        bad = _mock_entry_point("shell", _FakeTarget)
        with patch("kanibako.targets.entry_points", return_value=[bad]):
            discover_targets()
        err = capsys.readouterr().err
        assert "'shell'" in err
        assert "RESERVED" in err
        assert "SKIPPED" in err

    def test_the_warning_is_emitted_once_per_process(self, capsys):
        bad = _mock_entry_point("shell", _FakeTarget)
        with patch("kanibako.targets.entry_points", return_value=[bad]):
            discover_targets()
            discover_targets()
            discover_targets()
        assert capsys.readouterr().err.count("RESERVED") == 1

    def test_an_ordinary_name_still_registers(self):
        # Non-vacuity: prove the gate above refuses on the NAME, not on everything.
        ep = _mock_entry_point("shellfish", _FakeTarget)
        with patch("kanibako.targets.entry_points", return_value=[ep]):
            targets = discover_targets()
        assert targets["shellfish"] is _FakeTarget

    def test_require_meta_name_refuses_a_reserved_harness_name(self):
        # The floor under a Target that reaches a caller without going through
        # discovery.  It RAISES rather than skipping: by here it is the target
        # being launched, and there is nothing left to fall back to.
        from kanibako.targets import _require_meta_name

        with pytest.raises(ValueError, match="RESERVED pseudo-agent name"):
            _require_meta_name(_ReservedNameTarget())


class TestTheRegistryIsKeyedByNode:
    """``[R173]``: an agent's NAME keeps its case, its NODE is always lowercase.

    The node is what the ``agents/<node>/`` store dir and the ``agent.<node>.*``
    cascade slot are spelled from, so a plugin calling itself ``Shell`` must not
    reach disk as ``agents/Shell/`` — the macOS collision that opened this arc.
    Deriving the node at REGISTRATION is the cure; ``agent_config.store_dirname``
    is correct as it stands and must not fold.
    """

    @pytest.fixture(autouse=True)
    def _clear_warn_dedupe(self):
        from kanibako.targets import _COLLIDING_NAME_WARNED, _RESERVED_NAME_WARNED

        _RESERVED_NAME_WARNED.clear()
        _COLLIDING_NAME_WARNED.clear()
        yield
        _RESERVED_NAME_WARNED.clear()
        _COLLIDING_NAME_WARNED.clear()

    def test_a_declared_MixedCase_name_registers_under_its_lowercase_node(self):
        ep = _mock_entry_point("Kirobo", _FakeTarget)
        with patch("kanibako.targets.entry_points", return_value=[ep]):
            targets = discover_targets()
        assert "kirobo" in targets
        assert "Kirobo" not in targets

    def test_the_declared_NAME_is_untouched_on_the_plugin(self):
        """The other half of the rule — folding the node must not erase the name."""

        class _MixedCaseTarget(_FakeTarget):
            @property
            def name(self) -> str:
                return "Kirobo"

        ep = _mock_entry_point("Kirobo", _MixedCaseTarget)
        with patch("kanibako.targets.entry_points", return_value=[ep]):
            targets = discover_targets()
        assert targets["kirobo"]().name == "Kirobo"

    def test_a_case_variant_of_a_RESERVED_name_is_refused_too(self):
        """What a pseudo-agent owns follows the NODE, so ``Shell`` claims it as well.

        Before the node fold this plugin registered happily under ``Shell`` and took
        ``agents/Shell/`` — one case-insensitive filesystem away from the reserved
        ``shell`` store.
        """
        bad = _mock_entry_point("Shell", _FakeTarget)
        good = _mock_entry_point("fake", _FakeTarget)
        with patch("kanibako.targets.entry_points", return_value=[bad, good]):
            targets = discover_targets()
        assert "Shell" not in targets
        # ⚑ The node is owned by the SEEDED built-in, which the rogue plugin
        # neither registers as nor displaces.
        assert targets["shell"] is ShellTarget
        assert targets["fake"] is _FakeTarget

    def test_that_refusal_names_BOTH_spellings(self, capsys):
        """A plugin author reading it looks for the name they wrote, not the node."""
        bad = _mock_entry_point("Shell", _FakeTarget)
        with patch("kanibako.targets.entry_points", return_value=[bad]):
            discover_targets()
        err = capsys.readouterr().err
        assert "'Shell'" in err
        assert "'shell'" in err
        assert "SKIPPED" in err


class TestACaseCollidingSecondPluginIsRefused:
    """``[R173]``: two plugins collapsing to one node is a COLLISION, not a race.

    Discovery order within a tier is arbitrary — entry points come back in whatever
    order the metadata yields — so letting the later one win means the box's agent
    depends on install order.  The refusal is skip-and-warn, like every other one at
    this gate.
    """

    @pytest.fixture(autouse=True)
    def _clear_warn_dedupe(self):
        from kanibako.targets import _COLLIDING_NAME_WARNED

        _COLLIDING_NAME_WARNED.clear()
        yield
        _COLLIDING_NAME_WARNED.clear()

    def test_the_first_declared_spelling_keeps_the_node(self):
        first = _mock_entry_point("Kirobo", _FakeTarget)
        second = _mock_entry_point("kirobo", _DetectableTarget)
        with patch("kanibako.targets.entry_points", return_value=[first, second]):
            targets = discover_targets()
        # ⚑ ``shell`` leads: the built-in is seeded before any plugin tier runs.
        assert list(targets) == ["shell", "kirobo"]
        assert targets["kirobo"] is _FakeTarget

    def test_the_refusal_says_what_collided_and_with_what(self, capsys):
        first = _mock_entry_point("Kirobo", _FakeTarget)
        second = _mock_entry_point("kirobo", _DetectableTarget)
        with patch("kanibako.targets.entry_points", return_value=[first, second]):
            discover_targets()
        err = capsys.readouterr().err
        assert "'kirobo' " in err
        assert "'Kirobo'" in err
        assert "SKIPPED" in err

    def test_an_EXACT_repeat_is_an_ordinary_override_not_a_collision(self, capsys):
        """Non-vacuity, and the boundary: the guard fires on CASE, not on repetition."""
        first = _mock_entry_point("kirobo", _FakeTarget)
        second = _mock_entry_point("kirobo", _DetectableTarget)
        with patch("kanibako.targets.entry_points", return_value=[first, second]):
            targets = discover_targets()
        assert targets["kirobo"] is _DetectableTarget
        assert "collides" not in capsys.readouterr().err


class TestAPluginMustHaveThePluginShape:
    """A plugin with no descriptor is not a plugin — it is refused AT DISCOVERY.

    Keyspec §2d: *"A pseudo-agent […] lacks the elements a harness plugin provides:
    default settings, credential mechanism(s), interactive modes, & customized
    settings."*  The plugin system is descriptor-only, so those elements ARE the
    descriptor, and ``default_entrypoint`` is the program an interactive mode runs.
    A plugin declaring neither has nothing to supply and would launch as a plain
    shell — and because ``_run_container`` read "has a plugin" three ways from three
    attributes, the same registered target got DIFFERENT answers at different sites.

    So the refusal belongs at the ONE registration gate, and the built-in
    ``ShellTarget`` is safe: it is SEEDED, never discovered ([R175]), so this gate
    never sees the one target that legitimately has no plugin.
    """

    @pytest.fixture(autouse=True)
    def _clear_warn_dedupe(self):
        from kanibako.targets import _NO_PLUGIN_SHAPE_WARNED

        _NO_PLUGIN_SHAPE_WARNED.clear()
        yield
        _NO_PLUGIN_SHAPE_WARNED.clear()

    @pytest.fixture(autouse=True)
    def _isolate_config(self, tmp_path, monkeypatch):
        _isolate_config(tmp_path, monkeypatch)

    @staticmethod
    def _no_descriptor_target(name: str) -> type:
        """A ``Target`` subclass declaring NO descriptor and NO entrypoint."""
        from kanibako.targets.base import Target as _T

        class _NoDesc(_T):
            @property
            def name(self) -> str:
                return name

            @property
            def display_name(self) -> str:
                return f"No Descriptor {name}"

            def detect(self):
                return None

        return _NoDesc

    @staticmethod
    def _no_entrypoint_target(name: str) -> type:
        """A ``Target`` subclass with a descriptor but NO ``default_entrypoint``.

        The OTHER half of the invariant ``has_plugin`` rests on: discovery refuses
        this too, so a registered target cannot hold one without the other.
        """
        from kanibako.targets.base import Target as _T

        class _NoEntry(_T):
            @property
            def name(self) -> str:
                return name

            @property
            def display_name(self) -> str:
                return f"No Entrypoint {name}"

            @property
            def descriptor(self):
                return _minimal_descriptor(command=(f"{name}-bin",))

            def detect(self):
                return None

        return _NoEntry

    def test_an_entry_point_plugin_with_no_descriptor_is_absent(self, capsys):
        bad = _mock_entry_point("nodesc", self._no_descriptor_target("nodesc"))
        good = _mock_entry_point("fake", _FakeTarget)
        with patch("kanibako.targets.entry_points", return_value=[bad, good]):
            targets = discover_targets()
        err = capsys.readouterr().err

        assert "nodesc" not in targets
        # SKIPPED, not fatal: discovery runs on every command.
        assert targets["fake"] is _FakeTarget
        assert "'nodesc'" in err
        assert "SKIPPED" in err

    def test_a_plugin_with_no_default_entrypoint_is_skipped(self, capsys):
        bad = _mock_entry_point("noentry", self._no_entrypoint_target("noentry"))
        with patch("kanibako.targets.entry_points", return_value=[bad]):
            targets = discover_targets()
        err = capsys.readouterr().err

        assert "noentry" not in targets
        assert "'noentry'" in err
        assert "SKIPPED" in err
        # The message names WHICH half is missing, so the author knows what to add.
        assert "default_entrypoint" in err

    def test_the_warning_names_the_missing_half(self, capsys):
        """What a plugin author acts on: which attribute, and that it is required."""
        bad = _mock_entry_point("nodesc", self._no_descriptor_target("nodesc"))
        with patch("kanibako.targets.entry_points", return_value=[bad]):
            discover_targets()
        err = capsys.readouterr().err

        assert "plugin descriptor" in err
        assert "descriptor-only" in err
        assert "kanibako setup" in err  # what still works
        assert "'descriptor'" in err and "'default_entrypoint'" in err  # the cure

    def test_a_plugin_that_fails_the_shape_probe_is_skipped_not_fatal(self, capsys):
        """Reading the shape must not be able to take the CLI down either.

        ``ep.load()`` already returns whatever the plugin hands back, with no
        ``issubclass`` filter, so a non-``Target`` object reaches this gate. It
        still gets a warning, not a traceback.
        """
        bad = _mock_entry_point("notap", object)
        good = _mock_entry_point("fake", _FakeTarget)
        with patch("kanibako.targets.entry_points", return_value=[bad, good]):
            targets = discover_targets()
        err = capsys.readouterr().err

        assert "notap" not in targets
        assert targets["fake"] is _FakeTarget
        assert "'notap'" in err
        assert "SKIPPED" in err
        # The refusal carries the reason, not just the verdict.
        assert "usable descriptor" in err
        assert "AttributeError" in err

    def test_the_warning_is_emitted_once_per_process(self, capsys):
        bad = _mock_entry_point("nodesc", self._no_descriptor_target("nodesc"))
        with patch("kanibako.targets.entry_points", return_value=[bad]):
            discover_targets()
            discover_targets()
            discover_targets()
        assert capsys.readouterr().err.count("SKIPPED") == 1

    def test_the_seeded_shell_target_is_unaffected(self, capsys):
        """The ONE target that legitimately has no plugin ([R175]).

        It is SEEDED, not discovered, so it never passes through the gate; a gate
        that ran on it would empty the registry of the very slot the reservation
        refusal above protects.
        """
        with patch("kanibako.targets.entry_points", return_value=[]):
            targets = discover_targets()
        assert targets["shell"] is ShellTarget
        assert ShellTarget().descriptor is None
        assert capsys.readouterr().err == ""  # the gate never saw it

    def test_a_well_shaped_plugin_still_registers_unchanged(self, capsys):
        """Non-vacuity: the gate refuses on the SHAPE, not on every plugin."""
        ep = _mock_entry_point("wellshaped", _FakeTarget)
        with patch("kanibako.targets.entry_points", return_value=[ep]):
            targets = discover_targets()
        assert targets["wellshaped"] is _FakeTarget
        assert "SKIPPED" not in capsys.readouterr().err


class TestGetTarget:
    def test_found(self):
        ep = _mock_entry_point("fake", _FakeTarget)
        with patch("kanibako.targets.entry_points", return_value=[ep]):
            cls = get_target("fake")
        assert cls is _FakeTarget

    def test_not_found(self):
        with patch("kanibako.targets.entry_points", return_value=[]):
            with pytest.raises(KeyError, match="Unknown target 'nope'"):
                get_target("nope")

    def test_the_lookup_is_case_blind(self):
        """``--agent Fake`` and ``--agent fake`` name ONE agent (keyspec §0).

        The query is a NAME — typed, or carried in ``system.agent`` — and the
        registry is keyed by NODE, so the comparison folds both sides.  Exact
        matching made a capital an agent that is not installed.
        """
        ep = _mock_entry_point("fake", _FakeTarget)
        with patch("kanibako.targets.entry_points", return_value=[ep]):
            assert get_target("Fake") is _FakeTarget
            assert get_target("FAKE") is _FakeTarget
            assert get_target("fake") is _FakeTarget

    def test_an_unrelated_name_still_misses(self):
        """Non-vacuity: folding widened the match, it did not remove it."""
        ep = _mock_entry_point("fake", _FakeTarget)
        with patch("kanibako.targets.entry_points", return_value=[ep]):
            with pytest.raises(KeyError, match="Unknown target 'faked'"):
                get_target("faked")


class TestResolveTarget:
    def test_resolve_by_name(self):
        ep = _mock_entry_point("fake", _FakeTarget)
        with patch("kanibako.targets.entry_points", return_value=[ep]):
            t = resolve_target("fake")
        assert isinstance(t, _FakeTarget)

    def test_resolve_by_name_not_found(self):
        with patch("kanibako.targets.entry_points", return_value=[]):
            with pytest.raises(KeyError):
                resolve_target("missing")

    def test_auto_detect(self):
        ep = _mock_entry_point("detectable", _DetectableTarget)
        with patch("kanibako.targets.entry_points", return_value=[ep]):
            t = resolve_target()
        assert isinstance(t, _DetectableTarget)

    def test_auto_detect_skips_undetectable(self):
        ep1 = _mock_entry_point("fake", _FakeTarget)
        ep2 = _mock_entry_point("detectable", _DetectableTarget)
        with patch("kanibako.targets.entry_points", return_value=[ep1, ep2]):
            t = resolve_target()
        assert isinstance(t, _DetectableTarget)

    def test_auto_detect_none_found_returns_shell(self):
        ep = _mock_entry_point("fake", _FakeTarget)
        with patch("kanibako.targets.entry_points", return_value=[ep]):
            t = resolve_target()
        assert isinstance(t, ShellTarget)

    def test_auto_detect_empty_returns_shell(self):
        with patch("kanibako.targets.entry_points", return_value=[]):
            t = resolve_target()
        assert isinstance(t, ShellTarget)

    def test_resolve_by_name_requires_meta_name(self):
        # meta.agent.<agent>.name (the plugin's `name`) is REQUIRED; an empty
        # name has no resolvable store dir / cascade key -> fail loudly.
        ep = _mock_entry_point("blank", _NoNameTarget)
        with patch("kanibako.targets.entry_points", return_value=[ep]):
            with pytest.raises(ValueError, match=r"meta\.agent\.<agent>\.name"):
                resolve_target("blank")


# ── A registrable plugin, as BYTES a caller could put on disk ────────

_PLUGIN_SOURCE = '''\
from kanibako.targets.base import (
    BindKind,
    Binding,
    BindScope,
    HostSrcOrigin,
    PluginDescriptor,
    Target,
)


class MyFilePlugin(Target):
    @property
    def name(self):
        return "{name}"

    @property
    def display_name(self):
        return "File Plugin {name}"

    @property
    def descriptor(self):
        # The plugin system's own floor (keyspec §2d): no descriptor, no plugin.
        # ``targets._register`` refuses one that returns None, so a fixture
        # built to test a DIFFERENT refusal must carry this shape too.
        return PluginDescriptor(
            command=("file-bin",),
            bindings=(
                Binding(
                    key="binary",
                    origin=HostSrcOrigin.BINARY,
                    box_dest="/usr/local/bin/file-bin",
                    kind=BindKind.FILE,
                    scope=BindScope.AGENT_CRITICAL,
                ),
            ),
            mode={{"start": ()}},
        )

    @property
    def default_entrypoint(self):
        return "file-bin"

    def detect(self):
        return None

    def binary_mounts(self, install):
        return []

    def refresh_credentials(self, home):
        pass

    def writeback_credentials(self, home):
        pass

    def build_cli_args(self, **kwargs):
        return []
'''


def _write_plugin(directory: Path, filename: str, name: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / filename).write_text(_PLUGIN_SOURCE.format(name=name))


class TestFileDropDirectoriesAreNotADiscoveryRoute:
    """A ``.py`` file under a plugins directory is INERT DATA, never a plugin.

    ⭐ THE SECURITY PIN.  A plugin is host code, and it loads only from an installed
    package's ``kanibako.agents`` entry point — so the retired file-drop doors
    (``<config.data>/plugins/`` and a box store's ``plugins/``) must stay shut.  Both
    tests drop the SAME ``_PLUGIN_SOURCE`` those doors used to ``exec_module`` and ask
    for it back, so each one is red on any tree where a scan still reads the directory.
    """

    @pytest.fixture(autouse=True)
    def _isolate_config(self, tmp_path, monkeypatch):
        _isolate_config(tmp_path, monkeypatch)

    def test_a_py_file_in_the_user_plugins_dir_does_not_load(
        self, tmp_path, monkeypatch
    ):
        user_plugins = tmp_path / "kanibako" / "plugins"
        _write_plugin(user_plugins, "myplugin.py", "myplugin")
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))

        with patch("kanibako.targets.entry_points", return_value=[]):
            targets = discover_targets()
        assert "myplugin" not in targets

    def test_a_py_file_in_a_box_store_plugins_dir_does_not_load(self, tmp_path):
        """The PER-BOX door, in the layout it read: the RESOLVED store's ``plugins/``."""
        proj = tmp_path / "myproject"
        _write_plugin(proj / "box_data" / "plugins", "projplugin.py", "projplugin")

        with patch("kanibako.targets.entry_points", return_value=[]):
            targets = discover_targets(project_path=proj)
        assert "projplugin" not in targets

    def test_the_same_plugin_source_still_loads_from_an_entry_point(self, tmp_path):
        """⚑ NON-VACUITY: the file the two tests above drop is a REGISTRABLE plugin.

        The very same ``_PLUGIN_SOURCE`` bytes are loaded as a module and handed to the
        registry the installed-package route reads — and they land.  So what the door
        tests prove is the DIRECTORY, not a malformed fixture.
        """
        import importlib.util

        dropped = tmp_path / "dropped.py"
        dropped.write_text(_PLUGIN_SOURCE.format(name="myplugin"))
        spec = importlib.util.spec_from_file_location("dropped_myplugin", dropped)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        ep = _mock_entry_point("myplugin", module.MyFilePlugin)
        with patch("kanibako.targets.entry_points", return_value=[ep]):
            targets = discover_targets()
        assert targets["myplugin"] is module.MyFilePlugin


# The e2e PTY fixtures are the reason this route needs a probe at all.
_E2E_FIXTURES = Path(REPO_ROOT) / "tests" / "e2e" / "fixtures"

# (fixture dir, module name, entry-point key, Target class)
_PUBLISHED_AGENTS = [
    ("dead-agent", "dead", "dead", "DeadTarget"),
    ("live-agent", "live", "live", "LiveTarget"),
]

_DISCOVER_IN_CHILD = (
    "import json;"
    "from kanibako.targets import discover_targets;"
    "print(json.dumps(sorted(discover_targets())))"
)


def _discover_in_child(pythonpath: str) -> list[str]:
    """Every node ``discover_targets`` returns in a VIRGIN interpreter.

    A fresh subprocess, per the house rule in ``test_plugin_import_compat``: the
    whole mechanism under test is an import-time one (``importlib.metadata`` reads
    ``sys.path``), so it cannot be observed by patching in a process that already
    imported the packages.
    """
    proc = subprocess.run(
        [sys.executable, "-c", _DISCOVER_IN_CHILD],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env={**os.environ, "PYTHONPATH": pythonpath},
        timeout=120,
    )
    assert proc.returncode == 0, f"discovery child failed:\n{proc.stderr}"
    return json.loads(proc.stdout)


class TestAPluginPublishedAsAnInstalledEntryPoint:
    """The route the e2e PTY fixtures publish their plugins through.

    ⚑ THE FIXTURES ARE THE SUBJECT, NOT A LOOKALIKE.  ``dead``/``live`` are the
    REAL ``tests/e2e/fixtures`` modules and the REAL
    :func:`tests.e2e._entry_point_plugin.install_entry_point_plugin` the e2e calls,
    so a break in either is a break the e2e would hit.  Discovery reads installed
    metadata and nothing else, so a plugin a test merely drops in a directory is
    never seen — publishing means writing the ``*.dist-info`` a package install
    would have written, and putting it on the child's ``PYTHONPATH``.
    """

    @pytest.mark.parametrize(
        "fixture_dir,module,entry,attr", _PUBLISHED_AGENTS,
        ids=[row[2] for row in _PUBLISHED_AGENTS],
    )
    def test_the_plugin_is_discovered_and_is_the_real_class(
        self, tmp_path, fixture_dir, module, entry, attr
    ):
        site = install_entry_point_plugin(
            tmp_path / "ep",
            src=_E2E_FIXTURES / fixture_dir / f"{module}.py",
            module=module, entry=entry, attr=attr, dist=f"{module}-agent",
        )
        nodes = _discover_in_child(os.pathsep.join((str(site), str(REPO_ROOT / "src"))))
        assert entry in nodes, (
            f"{attr} was not discovered from its synthetic install — the e2e PTY "
            f"fixtures would start with no such agent:\n{nodes}"
        )

    def test_publishing_is_what_makes_the_difference(self, tmp_path):
        """Anti-vacuity: WITHOUT the published install the same plugin is invisible.

        The module is present and importable either way, so the only difference is
        the metadata discovery reads.  Without this, a fixture could load because
        the host happened to have the agent installed and the route would read as
        working for the wrong reason.
        """
        fixture_dir, module, entry, attr = _PUBLISHED_AGENTS[0]
        install_entry_point_plugin(
            tmp_path / "ep",
            src=_E2E_FIXTURES / fixture_dir / f"{module}.py",
            module=module, entry=entry, attr=attr, dist=f"{module}-agent",
        )
        assert entry not in _discover_in_child(str(REPO_ROOT / "src")), (
            f"'{entry}' resolved with nothing published for it — this suite would "
            f"pass without proving the entry-point route works"
        )
