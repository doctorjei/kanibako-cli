"""The no-snapshot workset dir-key route: one grammar, and no token ever reaches disk.

⚑ The load-bearing test here is :class:`TestNoResolverLeaksAToken`, which asserts the
RULE over every resolver it DISCOVERS rather than over a list of names: a sixth
``workset.*`` dir key added tomorrow is swept the moment its resolver exists.  The
defect it exists to make impossible: a resolver that ``expanduser()``-ed the raw value
and joined the rest under the workset root, turning the spec's own documented default
``@meta.workset.path/boxes`` into a literal directory named ``@meta.workset.path`` —
while the launch snapshot resolved the same key correctly.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from kanibako.channels.channels import WS_TOKEN_PRIMARY
from kanibako.project import workset, workset_registry
from kanibako.settings.config import system_settings_path
from kanibako.settings.config_io import dump_doc, load_doc
from kanibako.settings.config_keys import _KEY_ROUTES
from kanibako.settings.paths import resolve_system_paths
from kanibako.settings.settings_resolve import UNSET, SettingsError
from kanibako.settings.workset_dirkeys import (
    WORKSET_EARLY_KEYS,
    WORKSET_PATH_REF,
    EarlyScope,
    early_repoint,
    early_system,
    early_tier,
    resolve_workset_dir_key,
)

# The modules that carry a no-snapshot resolver face.  ⚑ A LIST OF MODULES, not of
# functions: the resolvers themselves are discovered by SHAPE below.
_FACE_MODULES = (workset, workset_registry)

# ⚑ Every token shape a stored value may legally carry (spec ``:214``) that this seam
# cannot resolve.  None of them may survive into a returned path.
_UNRESOLVABLE = (
    "@config.registry/x",
    "@workset.boxes/x",
    "@meta.box.path/x",
    "$XDG_RUNTIME_DIR/x",
    "$NOT_A_VARIABLE/x",
    "$AGENT/x",
    "$WORKSET/x",
)

# ⚑ Every value shape [R147] calls AMBIGUOUS: it resolves fine, but to two different
# directories depending on an anchor nobody stated.  Distinct from ``_UNRESOLVABLE``
# above — those refuse because the seam CANNOT answer, these because it MUST NOT.
_AMBIGUOUS = ("comms", "sub/dir", "./here", "../sibling")


class _AnyLeaf(dict):
    """A ``workset:`` table that answers the SAME value for EVERY leaf name.

    ⚑ This is what keeps the sweep free of a key inventory: whichever leaf a resolver
    reaches for — including one that does not exist yet — it gets the poison.
    """

    def __init__(self, value: str) -> None:
        super().__init__()
        self._value = value

    def get(self, key, default=None):  # noqa: ANN001, ANN201 - Mapping protocol
        return self._value

    def __getitem__(self, key):  # noqa: ANN001, ANN204 - Mapping protocol
        return self._value


def _poisoned(value: str) -> dict:
    """A workset.yaml document whose every ``workset.*`` leaf is *value*."""
    return {"workset": _AnyLeaf(value)}


def _discover_resolvers() -> dict[str, object]:
    """Every public no-snapshot resolver, found by SIGNATURE SHAPE across the faces.

    The shape IS the contract: ``(workset_root, workset_settings, …) -> Path``.  A
    helper with other parameters (``resolve_workset_name``) is not one of these and is
    filtered out by the same rule that finds the real ones.
    """
    found: dict[str, object] = {}
    for module in _FACE_MODULES:
        for name, obj in vars(module).items():
            if not name.startswith("resolve_workset_") or not callable(obj):
                continue
            if getattr(obj, "__module__", None) != module.__name__:
                continue
            params = list(inspect.signature(obj).parameters)
            if params[:2] != ["workset_root", "workset_settings"]:
                continue
            found[f"{module.__name__}.{name}"] = obj
    return found


class TestDiscoveryIsNotVacuous:
    def test_finds_resolvers(self):
        # ⚑ The sweep below passes trivially on an empty set; this is what stops it
        # from manufacturing confidence if the discovery rule ever stops matching.
        assert _discover_resolvers(), (
            "no no-snapshot workset dir-key resolver was discovered — the token "
            "sweep would pass vacuously"
        )

    def test_every_face_module_imports_the_one_route(self):
        for module in _FACE_MODULES:
            assert hasattr(module, "resolve_workset_dir_key"), (
                f"{module.__name__} defines a workset dir-key resolver but has not "
                "imported the one no-snapshot route"
            )


class TestNoResolverLeaksAToken:
    """THE RULE: a resolved workset dir key never contains ``@`` or ``$``."""

    @pytest.mark.parametrize("poison", _UNRESOLVABLE)
    def test_unresolvable_token_refuses_rather_than_becoming_a_directory(self, poison, tmp_path):
        for label, resolver in _discover_resolvers().items():
            with pytest.raises(SettingsError) as excinfo:
                resolver(Path("/ws"), _poisoned(poison), early=_scope(tmp_path, {}))
            message = str(excinfo.value)
            assert poison in message, f"{label}: refusal does not quote the value"
            assert "/ws/workset.yaml" in message, f"{label}: refusal names no file"

    # ⚑ ``leaf`` USED TO BE IN THIS LIST and is now in ``_AMBIGUOUS`` below: [R147]
    # made a bare relative a REFUSAL, so it cannot also be a value that resolves.
    @pytest.mark.parametrize(
        "value", ["@meta.workset.path/leaf", "$XDG_DATA_HOME/leaf", "~/leaf"]
    )
    def test_resolvable_value_leaves_no_token_behind(self, value, tmp_path):
        for label, resolver in _discover_resolvers().items():
            resolved = str(resolver(Path("/ws"), _poisoned(value), early=_scope(tmp_path, {})))
            assert "@" not in resolved, f"{label}: '@' survived into {resolved}"
            assert "$" not in resolved, f"{label}: '$' survived into {resolved}"
            assert "~" not in resolved, f"{label}: '~' survived into {resolved}"

    def test_default_when_unset_carries_no_token(self, tmp_path):
        for label, resolver in _discover_resolvers().items():
            resolved = str(resolver(Path("/ws"), None, early=_scope(tmp_path, {})))
            assert not any(c in resolved for c in "@$~"), f"{label}: {resolved}"


class TestNoResolverAnchorsAnAmbiguousValue:
    """[R147] over EVERY discovered resolver, not over a list of key names.

    The twin of :class:`TestNoResolverLeaksAToken`: that one sweeps values this seam
    cannot resolve, this one sweeps values it MUST NOT resolve.  A tenth workset dir
    key added tomorrow is covered the moment its resolver exists.
    """

    @pytest.mark.parametrize("value", _AMBIGUOUS)
    def test_bare_relative_refuses_rather_than_anchoring(self, value, tmp_path):
        for label, resolver in _discover_resolvers().items():
            with pytest.raises(SettingsError) as excinfo:
                resolver(Path("/ws"), _poisoned(value), early=_scope(tmp_path, {}))
            message = str(excinfo.value)
            assert value in message, f"{label}: refusal does not quote the value"
            # BOTH readings, spelled out — the whole point of the refusal ([R147]).
            assert str(Path("/ws") / value) in message, f"{label}: no workset reading"
            assert str(Path.cwd() / value) in message, f"{label}: no cwd reading"


class TestEveryFaceRoutesThroughTheOneResolver:
    """Proof BY MUTATION: break the route and every face must break with it."""

    def test_each_resolver_calls_the_route(self, monkeypatch, tmp_path):
        resolvers = _discover_resolvers()
        assert resolvers
        early = _scope(tmp_path, {})  # built before the tripwire: the record build reads the faces
        for label, resolver in resolvers.items():
            module_name = label.rsplit(".", 1)[0]
            module = next(m for m in _FACE_MODULES if m.__name__ == module_name)
            calls: list[str] = []

            def _tripwire(*args, **kwargs):
                calls.append(label)
                return Path("/sentinel")

            monkeypatch.setattr(module, "resolve_workset_dir_key", _tripwire)
            assert resolver(Path("/ws"), _poisoned("leaf"), early=early) == Path("/sentinel")
            assert calls == [label], f"{label} does not route through the one resolver"
            monkeypatch.undo()


class TestTheRouteItself:
    def test_spec_default_formula_resolves_to_the_root_leaf(self, tmp_path):
        # The exact value the keyspec declares as the default for all five keys.
        assert resolve_workset_dir_key(
            Path("/ws"), f"@{WORKSET_PATH_REF}/boxes", "boxes", key="boxes",
            early=_scope(tmp_path, {}),
        ) == Path("/ws/boxes")

    def test_unset_takes_the_default_leaf(self, tmp_path):
        assert resolve_workset_dir_key(
            Path("/ws"), None, "workspace", key="workspaces",
            early=_scope(tmp_path, {}),
        ) == Path("/ws/workspace")

    def test_absolute_repoint_is_not_reanchored(self, tmp_path):
        assert resolve_workset_dir_key(
            Path("/ws"), "/elsewhere/boxes", "boxes", key="boxes",
            early=_scope(tmp_path, {}),
        ) == Path("/elsewhere/boxes")

    def test_bare_relative_repoint_is_refused_naming_both_readings(self, tmp_path):
        # ⚑ INVERTED, NOT DELETED, by [R147] (2026-08-29).  It used to assert
        # ``== Path("/ws/sub/dir")``.  The reason to set one of these keys at all is
        # to move the directory OFF the workset root, so anchoring there assumes the
        # very intent the user is overriding — and a wrong guess is not a confusing
        # message, it is data written to the wrong directory.
        with pytest.raises(SettingsError) as excinfo:
            resolve_workset_dir_key(
                Path("/ws"), "sub/dir", "boxes", key="boxes", early=_scope(tmp_path, {}),
            )
        message = str(excinfo.value)
        assert "/ws/sub/dir" in message
        assert str(Path.cwd() / "sub/dir") in message
        assert "workset.boxes" in message
        assert "/ws/workset.yaml" in message

    def test_embedded_ref_resolves_mid_path(self, tmp_path):
        assert resolve_workset_dir_key(
            Path("/ws"), f"/mnt/@{{{WORKSET_PATH_REF}}}/b", "boxes", key="boxes",
            early=_scope(tmp_path, {}),
        ) == Path("/mnt/ws/b")

    def test_refusal_names_the_key(self, tmp_path):
        with pytest.raises(SettingsError, match=r"workset\.channelroot"):
            resolve_workset_dir_key(
                Path("/ws"), "@config.data/c", "channels", key="channelroot",
                early=_scope(tmp_path, {}),
            )

    def test_refusal_names_the_only_available_reference(self, tmp_path):
        with pytest.raises(SettingsError, match=WORKSET_PATH_REF):
            resolve_workset_dir_key(
                Path("/ws"), "@config.data/c", "boxes", key="boxes",
                early=_scope(tmp_path, {}),
            )

    def test_resolving_has_no_side_effects_on_the_runtime_dir(self, monkeypatch, tmp_path):
        # ⚑ Detection walks ancestors that may not be worksets; ``host_xdg_map`` would
        # mkdir an XDG_RUNTIME_DIR fallback here.  The route must use the
        # side-effect-free map instead.
        import kanibako.settings.paths as paths_mod

        early = _scope(tmp_path, {})  # built before the probe, so it watches the route alone

        def _boom(*args, **kwargs):
            raise AssertionError("the no-snapshot route touched XDG_RUNTIME_DIR")

        monkeypatch.setattr(paths_mod, "_fallback_runtime_dir", _boom)
        monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
        assert resolve_workset_dir_key(
            Path("/ws"), "$XDG_DATA_HOME/b", "boxes", key="boxes",
            early=early,
        ).is_absolute()


def _write_system(workset_table: dict) -> Path:
    """Write *workset_table* as the system settings file's ``workset:`` table; its path."""
    path = system_settings_path()
    dump_doc(path, {"workset": workset_table})
    return path


def _system_scope(path: Path, tmp_path: Path) -> EarlyScope:
    """The record built from the system document at *path*, as the ``std`` load builds it."""
    resolved = {
        **resolve_system_paths({}, data_home=tmp_path / "data", home=tmp_path),
        "config.settings": path,
    }
    return EarlyScope(early_system(early_tier(load_doc(path)), resolved), "ws")


def _routed(key: str, value: object) -> dict:
    """A ``workset:`` table carrying *value* at ``workset.<key>``'s routed slot."""
    sections, slot = _KEY_ROUTES[f"workset.{key}"]
    table: dict = {slot: value}
    for section in reversed(sections[1:]):
        table = {section: table}
    return table


class TestTheSystemFileIsTheTierBeneath:
    """``system < workset`` for every early key: a system value applies, the workset's wins.

    The system tier is the record built from the system file the test writes.
    """

    @pytest.mark.parametrize("key", sorted(WORKSET_EARLY_KEYS))
    def test_every_early_key_reads_the_system_tier(self, key, tmp_path):
        system = _write_system(_routed(key, "/sys/x"))
        scope = _system_scope(system, tmp_path)
        assert early_repoint(tmp_path, None, key, early=scope) == ("/sys/x", system)

    @pytest.mark.parametrize("key", sorted(WORKSET_EARLY_KEYS))
    def test_the_workset_file_wins_its_null_included(self, key, tmp_path):
        scope = _system_scope(_write_system(_routed(key, "/sys/x")), tmp_path)
        own = tmp_path / "workset.yaml"
        assert early_repoint(
            tmp_path, {"workset": _routed(key, "/own")}, key, early=scope,
        ) == ("/own", own)
        assert early_repoint(
            tmp_path, {"workset": _routed(key, None)}, key, early=scope,
        ) == (None, own)

    def test_a_system_value_reaches_the_faces(self, tmp_path):
        scope = _system_scope(
            _write_system({"boxes": "/sys/boxes", "registry": "@meta.workset.path/r.yaml"}),
            tmp_path,
        )
        assert workset.resolve_workset_boxes(tmp_path, None, early=scope) == Path("/sys/boxes")
        assert workset_registry.resolve_workset_registry_path(tmp_path, None, early=scope) == (
            tmp_path / "r.yaml"
        )

    def test_a_workset_value_beats_it_at_the_face(self, tmp_path):
        scope = _system_scope(_write_system({"boxes": "/sys/boxes"}), tmp_path)
        doc = {"workset": {"boxes": "/own/boxes"}}
        assert workset.resolve_workset_boxes(tmp_path, doc, early=scope) == Path("/own/boxes")

    def test_a_workset_null_beats_a_system_value(self, tmp_path):
        scope = _system_scope(_write_system({"logs": "/sys/logs"}), tmp_path)
        assert workset.resolve_workset_logs(
            tmp_path, {"workset": {"logs": None}}, early=scope,
        ) is None

    def test_a_system_null_is_honored_like_a_workset_one(self, tmp_path):
        system = _write_system({"logs": None, "boxes": None})
        scope = _system_scope(system, tmp_path)
        assert workset.resolve_workset_logs(tmp_path, None, early=scope) is None
        with pytest.raises(SettingsError) as excinfo:
            workset.resolve_workset_boxes(tmp_path, None, early=scope)
        assert str(system) in str(excinfo.value)
        assert "workset.boxes" in str(excinfo.value)

    def test_a_refusal_names_the_system_file(self, tmp_path):
        system = _write_system({"boxes": "/z/$AGENT"})
        with pytest.raises(SettingsError) as excinfo:
            workset.resolve_workset_boxes(tmp_path, None, early=_system_scope(system, tmp_path))
        assert str(system) in str(excinfo.value)


class TestSameSetRefs:
    """A workset early key may reference another one; the same route resolves it."""

    def test_the_spec_channel_default_resolves(self, tmp_path):
        doc = {"workset": {"channelroot": "@meta.workset.path/chan"}}
        assert resolve_workset_dir_key(
            tmp_path, "@workset.channelroot/chat", "chat", key="channels.chat",
            standalone=False, workset_settings=doc, early=_scope(tmp_path, {}),
        ) == tmp_path / "chan" / "chat"

    def test_an_unset_referent_takes_its_declared_default_through_a_chain(self, tmp_path):
        assert resolve_workset_dir_key(
            tmp_path, "@workset.channels.chat/broadcast.md", "", key="channels.broadcast",
            standalone=False, early=_scope(tmp_path, {}),
        ) == tmp_path / "channels" / "chat" / "broadcast.md"

    def test_a_referent_reads_the_system_tier(self, tmp_path):
        scope = _system_scope(_write_system({"channelroot": "/sys/chan"}), tmp_path)
        assert resolve_workset_dir_key(
            tmp_path, "@workset.channelroot/chat", "", key="channels.chat", standalone=False,
            early=scope,
        ) == Path("/sys/chan/chat")

    @pytest.mark.parametrize(("standalone", "leaf"), [(True, "box_data"), (False, "boxes")])
    def test_the_mode_picks_the_referents_default(self, standalone, leaf, tmp_path):
        assert resolve_workset_dir_key(
            tmp_path, "@workset.boxes/x", "", key="logs", standalone=standalone,
            early=_scope(tmp_path, {}),
        ) == tmp_path / leaf / "x"

    def test_an_unknown_mode_refuses_a_mode_split_default(self, tmp_path):
        with pytest.raises(SettingsError) as excinfo:
            resolve_workset_dir_key(
                tmp_path, "@workset.boxes/x", "", key="canon", early=_scope(tmp_path, {}),
            )
        message = str(excinfo.value)
        assert "'@workset.boxes' is unset" in message
        assert "box_data" in message

    def test_a_cycle_is_refused(self, tmp_path):
        doc = {"workset": {
            "channelroot": "@workset.channels.chat/x",
            "channels": {"chat": "@workset.channelroot/chat"},
        }}
        with pytest.raises(SettingsError, match="Cyclic @-reference: workset.channelroot"):
            resolve_workset_dir_key(
                tmp_path, "@workset.channels.chat/x", "", key="channelroot",
                standalone=False, workset_settings=doc, early=_scope(tmp_path, {}),
            )

    def test_a_null_referent_is_refused(self, tmp_path):
        with pytest.raises(SettingsError, match="'@workset.channelroot' is null"):
            resolve_workset_dir_key(
                tmp_path, "@workset.channelroot/chat", "", key="channels.chat",
                standalone=False, workset_settings={"workset": {"channelroot": None}},
                early=_scope(tmp_path, {}),
            )

    def test_a_workset_key_outside_the_early_keys_is_refused(self, tmp_path):
        with pytest.raises(SettingsError, match="'@workset.kuid' cannot be resolved here"):
            resolve_workset_dir_key(
                tmp_path, "@workset.kuid/x", "", key="logs", early=_scope(tmp_path, {}),
            )

    def test_a_ref_into_a_later_set_gets_the_ordering_verdict(self, tmp_path):
        with pytest.raises(SettingsError) as excinfo:
            resolve_workset_dir_key(
                tmp_path, "@meta.box.path", "", key="logs", standalone=True, early=_scope(tmp_path, {}),
            )
        message = str(excinfo.value)
        assert 'system-design "Ordering rule"' in message
        assert str(tmp_path / "workset.yaml") in message

    def test_the_standalone_logs_default_is_the_box_store(self, tmp_path):
        assert workset.resolve_workset_logs(
            tmp_path, None, standalone=True, early=_scope(tmp_path, {}),
        ) == (
            tmp_path / "box_data"
        )
        doc = {"workset": {"boxes": "@meta.workset.path/store"}}
        assert workset.resolve_workset_logs(
            tmp_path, doc, standalone=True, early=_scope(tmp_path, {}),
        ) == (
            tmp_path / "store"
        )


@pytest.fixture
def no_system_open(monkeypatch):
    """Make the early route's own read of the system settings file raise."""
    from kanibako.settings import workset_dirkeys

    def refuse(*_args, **_kwargs):
        raise AssertionError("the early route opened the system settings file")

    monkeypatch.setattr(workset_dirkeys, "load_doc", refuse)


def _scope(tmp_path: Path, tier: dict, name: str = "ws") -> EarlyScope:
    """A record whose system tier is *tier*, built by the constructor from a resolved path tier."""
    resolved = resolve_system_paths({}, data_home=tmp_path / "data", home=tmp_path)
    return EarlyScope(early_system(tier, resolved), name)


class TestARecordIsTheSystemTier:
    """Given a record, every early reader takes the system tier from it and opens no file.

    The system file on disk states a DIFFERENT value, so a reader that reached it would answer
    that value even without the probe.
    """

    @pytest.mark.parametrize("key", sorted(WORKSET_EARLY_KEYS))
    def test_early_repoint_reads_the_record(self, key, tmp_path, no_system_open):
        _write_system(_routed(key, "/file/x"))
        scope = _scope(tmp_path, {f"workset.{key}": "/rec/x"})
        assert early_repoint(tmp_path, None, key, early=scope) == ("/rec/x", scope.system.file)

    def test_a_key_the_record_omits_is_unset(self, tmp_path, no_system_open):
        scope = _scope(tmp_path, {})
        value, where = early_repoint(tmp_path, None, "boxes", early=scope)
        assert value is UNSET
        assert where == tmp_path / "workset.yaml"

    def test_a_record_null_is_a_null(self, tmp_path, no_system_open):
        scope = _scope(tmp_path, {"workset.logs": None})
        assert early_repoint(tmp_path, None, "logs", early=scope) == (None, scope.system.file)

    def test_the_workset_file_still_wins(self, tmp_path, no_system_open):
        scope = _scope(tmp_path, {"workset.boxes": "/rec/boxes"})
        doc = {"workset": {"boxes": "/own/boxes"}}
        assert workset.resolve_workset_boxes(tmp_path, doc, early=scope) == Path("/own/boxes")

    def test_a_referent_reads_the_record(self, tmp_path, no_system_open):
        _write_system({"channelroot": "/file/chan"})
        scope = _scope(tmp_path, {"workset.channelroot": "/rec/chan"})
        assert resolve_workset_dir_key(
            tmp_path, "@workset.channelroot/chat", "", key="channels.chat", standalone=False,
            early=scope,
        ) == Path("/rec/chan/chat")

    def test_the_faces_read_the_record(self, tmp_path, no_system_open):
        _write_system({"boxes": "/file/boxes", "registry": "/file/r.yaml"})
        scope = _scope(tmp_path, {
            "workset.boxes": "/rec/boxes", "workset.registry": "@meta.workset.path/r.yaml",
        })
        assert workset.resolve_workset_boxes(tmp_path, None, early=scope) == Path("/rec/boxes")
        assert workset_registry.resolve_workset_registry_path(
            tmp_path, None, early=scope,
        ) == tmp_path / "r.yaml"

    def test_a_refusal_names_the_records_file(self, tmp_path, no_system_open):
        scope = _scope(tmp_path, {"workset.boxes": None})
        with pytest.raises(SettingsError) as excinfo:
            workset.resolve_workset_boxes(tmp_path, None, early=scope)
        assert str(scope.system.file) in str(excinfo.value)

    def test_a_workset_carries_its_record_to_its_readers(self, tmp_path, no_system_open):
        scope = _scope(tmp_path, {"workset.boxes": "/rec/boxes"})
        ws = workset.Workset(name="ws", root=tmp_path, early_system=scope.system)
        assert ws.early_scope == scope
        assert ws.projects_dir == Path("/rec/boxes")

    def test_the_default_worksets_scope_is_the_primary_partition(self, tmp_path):
        scope = _scope(tmp_path, {})
        ws = workset.Workset(
            name=workset.DEFAULT_WORKSET_ID, root=tmp_path, is_default=True,
            early_system=scope.system,
        )
        assert ws.early_scope == EarlyScope(scope.system, WS_TOKEN_PRIMARY)

    def test_the_channel_keys_read_the_record(self, tmp_path, no_system_open):
        from kanibako.channels.channels import workset_channels_at

        scope = _scope(tmp_path, {
            "workset.channelroot": "/rec/chan", "workset.channels.chat": "/rec/chat",
        })
        channels = workset_channels_at(tmp_path, early=scope)
        assert channels is not None
        assert (channels.root, channels.chat) == (Path("/rec/chan"), Path("/rec/chat"))

    def test_the_stamp_dirs_read_the_record(self, tmp_path, no_system_open):
        from kanibako.launch.templates import _workset_stamp_dirs

        scope = _scope(tmp_path, {"workset.canon": "@meta.workset.path/c"})
        canon, template = _workset_stamp_dirs(tmp_path, canon_only=False, early=scope)
        assert (canon, template) == (tmp_path / "c", tmp_path / "template")

    def test_the_primary_roots_read_the_record(self, config_file, tmp_home, no_system_open):
        from kanibako.settings.config import system_settings_path as settings_file
        from kanibako.settings.paths import load_system_tier

        settings_file().parent.mkdir(parents=True, exist_ok=True)
        dump_doc(settings_file(), {"workset": {"boxes": "/sys/boxes", "logs": None}})
        resolved, record = load_system_tier(
            config_file, data_home=tmp_home / "data", home=tmp_home / "home",
        )
        assert record.tier == {"workset.boxes": "/sys/boxes", "workset.logs": None}
        assert resolved["_primary_boxes"] == Path("/sys/boxes")
        assert "_primary_logs" not in resolved
