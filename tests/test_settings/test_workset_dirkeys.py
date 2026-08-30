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

from kanibako.project import workset, workset_registry
from kanibako.settings.settings_resolve import SettingsError
from kanibako.settings.workset_dirkeys import (
    WORKSET_PATH_REF,
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
    def test_unresolvable_token_refuses_rather_than_becoming_a_directory(self, poison):
        for label, resolver in _discover_resolvers().items():
            with pytest.raises(SettingsError) as excinfo:
                resolver(Path("/ws"), _poisoned(poison))
            message = str(excinfo.value)
            assert poison in message, f"{label}: refusal does not quote the value"
            assert "/ws/workset.yaml" in message, f"{label}: refusal names no file"

    # ⚑ ``leaf`` USED TO BE IN THIS LIST and is now in ``_AMBIGUOUS`` below: [R147]
    # made a bare relative a REFUSAL, so it cannot also be a value that resolves.
    @pytest.mark.parametrize(
        "value", ["@meta.workset.path/leaf", "$XDG_DATA_HOME/leaf", "~/leaf"]
    )
    def test_resolvable_value_leaves_no_token_behind(self, value):
        for label, resolver in _discover_resolvers().items():
            resolved = str(resolver(Path("/ws"), _poisoned(value)))
            assert "@" not in resolved, f"{label}: '@' survived into {resolved}"
            assert "$" not in resolved, f"{label}: '$' survived into {resolved}"
            assert "~" not in resolved, f"{label}: '~' survived into {resolved}"

    def test_default_when_unset_carries_no_token(self):
        for label, resolver in _discover_resolvers().items():
            resolved = str(resolver(Path("/ws"), None))
            assert not any(c in resolved for c in "@$~"), f"{label}: {resolved}"


class TestNoResolverAnchorsAnAmbiguousValue:
    """[R147] over EVERY discovered resolver, not over a list of key names.

    The twin of :class:`TestNoResolverLeaksAToken`: that one sweeps values this seam
    cannot resolve, this one sweeps values it MUST NOT resolve.  A tenth workset dir
    key added tomorrow is covered the moment its resolver exists.
    """

    @pytest.mark.parametrize("value", _AMBIGUOUS)
    def test_bare_relative_refuses_rather_than_anchoring(self, value):
        for label, resolver in _discover_resolvers().items():
            with pytest.raises(SettingsError) as excinfo:
                resolver(Path("/ws"), _poisoned(value))
            message = str(excinfo.value)
            assert value in message, f"{label}: refusal does not quote the value"
            # BOTH readings, spelled out — the whole point of the refusal ([R147]).
            assert str(Path("/ws") / value) in message, f"{label}: no workset reading"
            assert str(Path.cwd() / value) in message, f"{label}: no cwd reading"


class TestEveryFaceRoutesThroughTheOneResolver:
    """Proof BY MUTATION: break the route and every face must break with it."""

    def test_each_resolver_calls_the_route(self, monkeypatch):
        resolvers = _discover_resolvers()
        assert resolvers
        for label, resolver in resolvers.items():
            module_name = label.rsplit(".", 1)[0]
            module = next(m for m in _FACE_MODULES if m.__name__ == module_name)
            calls: list[str] = []

            def _tripwire(*args, **kwargs):
                calls.append(label)
                return Path("/sentinel")

            monkeypatch.setattr(module, "resolve_workset_dir_key", _tripwire)
            assert resolver(Path("/ws"), _poisoned("leaf")) == Path("/sentinel")
            assert calls == [label], f"{label} does not route through the one resolver"
            monkeypatch.undo()


class TestTheRouteItself:
    def test_spec_default_formula_resolves_to_the_root_leaf(self):
        # The exact value the keyspec declares as the default for all five keys.
        assert resolve_workset_dir_key(
            Path("/ws"), f"@{WORKSET_PATH_REF}/boxes", "boxes", key="boxes",
        ) == Path("/ws/boxes")

    def test_unset_takes_the_default_leaf(self):
        assert resolve_workset_dir_key(
            Path("/ws"), None, "workspace", key="workspaces",
        ) == Path("/ws/workspace")

    def test_absolute_repoint_is_not_reanchored(self):
        assert resolve_workset_dir_key(
            Path("/ws"), "/elsewhere/boxes", "boxes", key="boxes",
        ) == Path("/elsewhere/boxes")

    def test_bare_relative_repoint_is_refused_naming_both_readings(self):
        # ⚑ INVERTED, NOT DELETED, by [R147] (2026-08-29).  It used to assert
        # ``== Path("/ws/sub/dir")``.  The reason to set one of these keys at all is
        # to move the directory OFF the workset root, so anchoring there assumes the
        # very intent the user is overriding — and a wrong guess is not a confusing
        # message, it is data written to the wrong directory.
        with pytest.raises(SettingsError) as excinfo:
            resolve_workset_dir_key(Path("/ws"), "sub/dir", "boxes", key="boxes")
        message = str(excinfo.value)
        assert "/ws/sub/dir" in message
        assert str(Path.cwd() / "sub/dir") in message
        assert "workset.boxes" in message
        assert "/ws/workset.yaml" in message

    def test_embedded_ref_resolves_mid_path(self):
        assert resolve_workset_dir_key(
            Path("/ws"), f"/mnt/@{{{WORKSET_PATH_REF}}}/b", "boxes", key="boxes",
        ) == Path("/mnt/ws/b")

    def test_refusal_names_the_key(self):
        with pytest.raises(SettingsError, match=r"workset\.channelroot"):
            resolve_workset_dir_key(
                Path("/ws"), "@config.data/c", "channels", key="channelroot",
            )

    def test_refusal_names_the_only_available_reference(self):
        with pytest.raises(SettingsError, match=WORKSET_PATH_REF):
            resolve_workset_dir_key(
                Path("/ws"), "@config.data/c", "boxes", key="boxes",
            )

    def test_resolving_has_no_side_effects_on_the_runtime_dir(self, monkeypatch):
        # ⚑ Detection walks ancestors that may not be worksets; ``host_xdg_map`` would
        # mkdir an XDG_RUNTIME_DIR fallback here.  The route must use the
        # side-effect-free map instead.
        import kanibako.settings.paths as paths_mod

        def _boom(*args, **kwargs):
            raise AssertionError("the no-snapshot route touched XDG_RUNTIME_DIR")

        monkeypatch.setattr(paths_mod, "_fallback_runtime_dir", _boom)
        monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
        assert resolve_workset_dir_key(
            Path("/ws"), "$XDG_DATA_HOME/b", "boxes", key="boxes",
        ).is_absolute()


class TestExtraRefsIsNarrow:
    """``extra_refs`` admits ONLY what the caller passes, and only where it passes it.

    ⚑ The sweep above is the other half of this pair and must stay untouched: it calls
    every face on the UN-WIDENED route, where ``@meta.box.path`` is one of the
    ``_UNRESOLVABLE`` poisons.  If widening this route ever made that sweep go green
    without ``extra_refs``, the refusal would have become a guess — and in primary/named
    the guess is a trailing-separator box root, not an error anyone would see.
    """

    #: The one ref a caller supplies today (standalone ``workset.logs``), with a value
    #: it has already resolved.  The NAME alone buys nothing — the value is the point.
    _BOX_PATH = {"meta.box.path": "/ws/box_data"}

    def test_a_supplied_ref_resolves(self):
        assert resolve_workset_dir_key(
            Path("/ws"), "@meta.box.path/x", "", key="logs",
            extra_refs=self._BOX_PATH,
        ) == Path("/ws/box_data/x")

    def test_the_same_ref_still_refuses_without_it(self):
        with pytest.raises(SettingsError) as excinfo:
            resolve_workset_dir_key(
                Path("/ws"), "@meta.box.path/x", "logs", key="logs",
            )
        message = str(excinfo.value)
        assert "meta.box.path" in message
        assert "workset.logs" in message
        assert "/ws/workset.yaml" in message

    def test_a_ref_outside_the_supplied_map_still_refuses(self):
        # Widening for ONE name must not widen for the next one along.
        with pytest.raises(SettingsError) as excinfo:
            resolve_workset_dir_key(
                Path("/ws"), "@config.data/x", "", key="logs",
                extra_refs=self._BOX_PATH,
            )
        message = str(excinfo.value)
        assert "@config.data" in message
        # The refusal lists what IS available, supplied refs included.
        assert WORKSET_PATH_REF in message
        assert "meta.box.path" in message

    def test_the_workset_root_ref_survives_the_widening(self):
        assert resolve_workset_dir_key(
            Path("/ws"), f"@{WORKSET_PATH_REF}/logs", "", key="logs",
            extra_refs=self._BOX_PATH,
        ) == Path("/ws/logs")

    def test_a_supplied_value_is_a_leaf_not_a_second_expansion_pass(self):
        # ⚑ ``expand_expr`` never re-scans a substituted value; a host dir whose NAME
        # contains an ``@`` must survive verbatim rather than being resolved again.
        assert resolve_workset_dir_key(
            Path("/ws"), "@meta.box.path/x", "", key="logs",
            extra_refs={"meta.box.path": "/store/@config.data"},
        ) == Path("/store/@config.data/x")
