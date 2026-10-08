"""``settings_launch.resolve_inputs`` for the box-less subjects: WORKSET and SYSTEM.

A working set names no box and the system scope names neither a box nor a working
set, so each resolve OMITS the keys its target has no value for (spec §0, "never a
fabricated default") and carries every other key exactly as a box's resolve would.
"""

from __future__ import annotations

import pytest

from kanibako.agent_ref import GENERAL_SLOT
from kanibako.channels import channels
from kanibako.project.workset import add_project, create_workset, default_workset
from kanibako.settings.config import WORKSET_META_FILE
from kanibako.settings.keystore import KeyStore
from kanibako.settings.paths import (
    BoxMode,
    WorksetSpec,
    _early_scope,
    resolve_project,
    resolve_workset_project,
)
from kanibako.settings.settings_launch import (
    LaunchInputs,
    ResolveSubject,
    _box_less_omits,
    _omit_derived,
    build_launch_snapshot,
    resolve_inputs,
)
from kanibako.settings.settings_resolve import SettingsError

_FLOORS = ("meta_runtime", "meta_identity", "workset_anchor", "auth_chain")


@pytest.fixture
def named_ws(std, tmp_home):
    ws = create_workset("my-set", tmp_home / "worksets" / "my-set", std)
    source = tmp_home / "original-project"
    source.mkdir()
    add_project(ws, "cool-app", source)
    return ws


@pytest.fixture
def named_box(named_ws, std, config):
    return resolve_workset_project(
        WorksetSpec.from_workset(named_ws), "cool-app", std, config, initialize=True,
    )


def _box(std, proj) -> LaunchInputs:
    return resolve_inputs(
        subject=ResolveSubject.BOX, std=std, proj=proj, agent_name=GENERAL_SLOT,
        system_path=std.settings,
    )


def _workset(std, ws) -> LaunchInputs:
    return resolve_inputs(
        subject=ResolveSubject.WORKSET, std=std, ws=ws, agent_name=GENERAL_SLOT,
        system_path=std.settings,
    )


def _system(std) -> LaunchInputs:
    return resolve_inputs(
        subject=ResolveSubject.SYSTEM, std=std, agent_name=GENERAL_SLOT,
        system_path=std.settings,
    )


def _names(prefixes: tuple[str, ...], key: str, value: object) -> bool:
    """*key* is in one of *prefixes*' namespaces, or its value refs into one.

    ⚑ BOTH GRAMMARS. The filter has to recognise a reference however the floor spells
    it: `@meta.box.path/canon` and `{meta.box.path}/canon` name the same anchor, and
    reading only the old form let a braced value slip past the filter unfiltered.
    """
    return key.startswith(prefixes) or (
        isinstance(value, str)
        and any(f"@{p}" in value or ("{" + p) in value for p in prefixes)
    )


def _snapshot(inputs: LaunchInputs) -> KeyStore:
    return build_launch_snapshot(
        **inputs.as_kwargs(),
        agent_name=GENERAL_SLOT,
        agent_path=None,
        default_categories=dict(inputs.system_floor),
    )


def _leaf(store: KeyStore, dotted: str) -> object:
    node: object = store
    for seg in dotted.split("."):
        assert isinstance(node, KeyStore), dotted
        node = dict.get(node, seg)
    return node


class TestAWorkingSetResolvesAsItsBoxesDoMinusTheBox:
    """The WORKSET inputs ARE a member box's inputs without the box's own keys."""

    @pytest.mark.parametrize("which", ["named", "primary"])
    def test_every_floor_equals_the_box_floor_less_the_box_keys(
        self, which, std, config, project_dir, named_ws, named_box,
    ):
        if which == "named":
            ws, box = named_ws, named_box
        else:
            ws = default_workset(std)
            box = resolve_project(std, config, str(project_dir), initialize=True)
        box_inputs, ws_inputs = _box(std, box), _workset(std, ws)
        for name in _FLOORS:
            expected = {
                k: v for k, v in getattr(box_inputs, name).items()
                if not _names(("meta.box.",), k, v)
            }
            assert dict(getattr(ws_inputs, name)) == expected, name
        assert ws_inputs.ctx == box_inputs.ctx
        assert ws_inputs.system_floor == box_inputs.system_floor
        assert ws_inputs.cascade_workset_path == box_inputs.cascade_workset_path
        assert ws_inputs.cascade_box_path is None
        assert ws_inputs.subject is ResolveSubject.WORKSET

    def test_no_box_anchor_is_carried_or_referred_to(self, std, named_ws):
        """F3: ``meta.box.path`` & co. are OMITTED, and nothing left points at one."""
        inputs = _workset(std, named_ws)
        assert not [
            (k, v) for name in _FLOORS for k, v in getattr(inputs, name).items()
            if _names(("meta.box.",), k, v)
        ]

    def test_the_snapshot_resolves_the_workset_anchors(self, std, named_ws):
        """d5: every workset anchor resolves; none renders the empty string."""
        snap = _snapshot(_workset(std, named_ws))
        root = str(named_ws.root)
        assert _leaf(snap, "meta.workset.path") == root
        assert _leaf(snap, "meta.runtime.ws_root") == root
        assert _leaf(snap, "meta.workset.name") == "my-set"
        assert _leaf(snap, "meta.workset.settings") == f"{root}/{WORKSET_META_FILE}"
        assert _leaf(snap, "workset.auth.path") == f"{root}/auth"
        assert _leaf(snap, "workset.channelroot") == str(
            channels.workset_channels_at(
            named_ws.root, early=_early_scope(std, BoxMode.named, named_ws.name),
        ).root
        )
        for key in ("workset.boxes", "workset.vault_ro", "workset.logs", "workset.canon"):
            assert str(_leaf(snap, key)).startswith(root), key
        meta_box = dict.get(_leaf(snap, "meta"), "box")
        assert not isinstance(meta_box, KeyStore) or dict.get(meta_box, "path") is None


class TestTheSystemScopeHasNoBoxAndNoWorkingSet:

    # Spec §1A: of ``meta.runtime.*``, only these three are resolved per working set.
    _SCOPED = (
        "meta.box.", "meta.workset.", "workset.", "meta.runtime.ws_root",
        "meta.runtime.ws_name", "meta.runtime.project_type",
    )
    _HOST_RUNTIME = (
        "meta.runtime.user.config", "meta.runtime.admin.config",
        "meta.runtime.admin.settings",
    )

    def test_no_box_or_working_set_key_is_carried_or_referred_to(self, std):
        inputs = _system(std)
        assert not [
            (k, v) for name in _FLOORS for k, v in getattr(inputs, name).items()
            if _names(self._SCOPED, k, v)
        ]
        assert inputs.cascade_box_path is None
        assert inputs.cascade_workset_path is None
        assert inputs.ctx.workset_name is None
        assert inputs.subject is ResolveSubject.SYSTEM

    def test_the_host_runtime_keys_are_not_omitted(self, std):
        """§1A: ``meta.runtime.{user,admin}.*`` name HOST files, which the system
        scope has — nor is a key derived from one dropped."""
        floor: dict[str, object] = {k: f"/host/{k}" for k in self._HOST_RUNTIME}
        floor["box.derived"] = "@meta.runtime.admin.settings"
        _omit_derived(
            _system(std).ctx, lambda key: _box_less_omits(key, in_workset=False), floor,
        )
        assert set(floor) == {*self._HOST_RUNTIME, "box.derived"}

    def test_the_agent_and_system_keys_stay(self, std):
        """The control: only box and working-set keys go."""
        inputs = _system(std)
        assert f"meta.agent.{GENERAL_SLOT}.path" in inputs.meta_identity
        assert inputs.auth_chain["system.auth.share_allowed"] is True
        assert _leaf(_snapshot(inputs), f"meta.agent.{GENERAL_SLOT}.path")


class TestTheSubjectNamesItsTarget:

    @pytest.mark.parametrize("subject, give", [
        (ResolveSubject.BOX, {}),
        (ResolveSubject.BOX, {"ws": "ws"}),
        (ResolveSubject.WORKSET, {}),
        (ResolveSubject.WORKSET, {"proj": "box"}),
        (ResolveSubject.SYSTEM, {"ws": "ws"}),
        (ResolveSubject.SYSTEM, {"proj": "box"}),
    ])
    def test_a_mismatched_target_is_refused(
        self, subject, give, std, named_ws, named_box,
    ):
        target = {"ws": named_ws, "box": named_box}
        with pytest.raises(ValueError, match=f"subject {subject.name} takes"):
            resolve_inputs(  # type: ignore[call-overload]
                subject=subject, std=std, agent_name=GENERAL_SLOT,
                system_path=std.settings,
                **{k: target[v] for k, v in give.items()},
            )

    @pytest.mark.writes_undeclared(
        "workset.nosuchleaf", reason="the undeclared key IS the input the refusal names.",
    )
    def test_the_workset_refusal_speaks_for_the_working_set(self, std, named_ws):
        (named_ws.root / WORKSET_META_FILE).write_text("workset:\n  nosuchleaf: 1\n")
        with pytest.raises(SettingsError) as exc:
            _snapshot(_workset(std, named_ws))
        assert f"resolved for {ResolveSubject.WORKSET.what} carry" in str(exc.value)
        assert ResolveSubject.WORKSET.cure_note in str(exc.value)

    @pytest.mark.writes_undeclared(
        "box.nosuchleaf", reason="the undeclared key IS the input the refusal names.",
    )
    def test_the_system_refusal_speaks_for_the_system_scope(self, std):
        std.settings.parent.mkdir(parents=True, exist_ok=True)
        std.settings.write_text("box:\n  nosuchleaf: 1\n")
        with pytest.raises(SettingsError) as exc:
            _snapshot(_system(std))
        assert f"resolved for {ResolveSubject.SYSTEM.what} carry" in str(exc.value)
        assert ResolveSubject.SYSTEM.cure_note in str(exc.value)


class TestTheWorksetKeyedChannelHelpersAreTheOnlyCarriers:
    """The ``ProjectPaths`` forms delegate, so a box and its working set agree."""

    def test_channels_and_token(self, std, named_ws, named_box):
        assert channels.workset_channels_at(
            named_ws.root, early=_early_scope(std, BoxMode.named, named_ws.name),
        ) == (
            channels.workset_channel_paths(named_box, std)
        )
        assert channels.workset_token(BoxMode.named, named_ws.name) == (
            channels.workset_name_token(named_box)
        )


def _subject_inputs(which: str, std, ws, box) -> LaunchInputs:
    return {
        "system": lambda: _system(std),
        "workset": lambda: _workset(std, ws),
        "box": lambda: _box(std, box),
    }[which]()


def _refer_to(std, refs: dict[str, str]) -> None:
    """Write system binds whose SOURCES are the ``@``-refs in *refs* (guest dest → ref)."""
    std.settings.parent.mkdir(parents=True, exist_ok=True)
    std.settings.write_text("system:\n  bindings:\n    ro:\n" + "".join(
        f"      {dest}: ['{ref}']\n" for dest, ref in refs.items()
    ))


def _bind_sources(snap: KeyStore) -> dict[str, str]:
    return {dest: e.src for dest, e in dict(snap.system.bindings.ro).items()}


#: One expander-significant character each, spelled into a host directory name.
_SIGNIFICANT = ["$HOME", "@b", "@meta.box.name", "\\q", "~"]


class TestAHostPathInTheRuntimeFloorIsALiteral:
    """§1A: a ``meta.runtime`` host-file path is DATA. A ``$``, ``@``, ``\\`` or ``~`` in a
    directory name is neither expanded nor read as a reference, in every subject."""

    @pytest.mark.parametrize("refer", [False, True])
    @pytest.mark.parametrize("which", ["system", "workset", "box"])
    @pytest.mark.parametrize("ch", _SIGNIFICANT)
    def test_the_user_config_file(
        self, ch, which, refer, std, tmp_home, named_ws, named_box, monkeypatch,
    ):
        from tests.support.filenames import CONFIG_FILENAME

        config_home = tmp_home / f"x{ch}cfg"
        config_home.mkdir()
        monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
        if refer:
            _refer_to(std, {"~/host.cfg": "@meta.runtime.user.config"})
        snap = _snapshot(_subject_inputs(which, std, named_ws, named_box))
        user_config = str(config_home / CONFIG_FILENAME)
        assert _leaf(snap, "meta.runtime.user.config") == user_config
        if refer:
            assert list(_bind_sources(snap).values()) == [user_config]
