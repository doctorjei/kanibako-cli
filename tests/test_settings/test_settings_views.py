"""Unit tests for the typed access layer (block 4: settings_views).

Covers the brief's checklist (§4): the tier-2 category accessor yields
``BindEntry`` (never ``BindEntry | None``); iteration / len / contains; an
arbitrary DYNAMIC key resolves correctly through the accessor; the ``env``
accessor yields
scalars; ``masks_set`` returns exactly the masked dests as a ``set`` (present-None
already dropped by build — fed a post-build node); the tier-1 finite view returns
EXACT types; read-only (no mutation path); purity (the accessor does not copy or
mutate the wrapped node); and the S22 coupling — a ``None`` bind leaf RAISES (a
build-invariant breach), never type-launders.

⚑ The NAME-keyed ``bind_category`` / ``bindings`` lenses are GONE (2026-08-08c):
every bind-shaped category is dest-keyed and terminal, so ``bind_map`` /
``bind_maps`` are the only bind lenses and their mapping KEY is the destination.
The tests that existed solely to exercise the retired pair went with them; each
guarantee they carried is asserted here against the surviving lens.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import pytest

from kanibako.settings.kb_store import Bind, BindEntry
from kanibako.settings.keystore import KeyStore
from kanibako.settings.settings_views import (
    FiniteView,
    MetaView,
    ViewError,
    as_bool,
    as_int,
    as_path,
    as_str,
    bind_map,
    bind_maps,
    env_view,
    masks_set,
    typed_field,
)


# --------------------------------------------------------------------------- #
# Fixtures — small post-build-shaped snapshot nodes                           #
# --------------------------------------------------------------------------- #


def _rw_node() -> KeyStore:
    """A ``bindings.rw`` node: DEST-KEYED, all-``BindEntry`` leaves.

    The mapping key is the box destination (guest-spelled), which is DATA — so
    "an arbitrary dynamic key" is the ordinary case here, not a special one.
    """
    node = KeyStore()
    node["~/"] = BindEntry("/host/home")
    node["~/vault"] = BindEntry("@workset.vault_rw", "Z,U")
    node["/box/getter"] = BindEntry("/host/getter")
    return node


def _env_node() -> KeyStore:
    node = KeyStore()
    node["LANG"] = "en_US.UTF-8"
    node["DEBUG"] = True
    node["RETRIES"] = 3
    node["RATIO"] = 1.5
    # A dynamic env-var-style name (block 1b: ``items`` is reserved; use a
    # non-reserved dynamic name).
    node["ITEMIZED"] = "ok"
    return node


def _masks_node() -> KeyStore:
    """A POST-BUILD masks node — present-None unmasks already dropped (§6f).

    Every surviving value is a mask marker (bare/true). The build never leaves a
    None here, so this models exactly what a consumer sees.
    """
    node = KeyStore()
    node["/secrets"] = True
    node["/home/agent/.ssh"] = True
    node["/box/getter"] = True  # a dynamic masked dest (block 1b: not ``get``).
    return node


# --------------------------------------------------------------------------- #
# Tier-2 — env_view yields scalars                                            #
# --------------------------------------------------------------------------- #


def test_env_view_yields_scalars() -> None:
    view = env_view(_env_node())
    assert isinstance(view, Mapping)
    assert view["LANG"] == "en_US.UTF-8"
    assert view["DEBUG"] is True
    assert view["RETRIES"] == 3
    assert view["RATIO"] == 1.5
    assert len(view) == 5


def test_env_view_dynamic_key() -> None:
    view = env_view(_env_node())
    assert view["ITEMIZED"] == "ok"
    assert set(view) == {"LANG", "DEBUG", "RETRIES", "RATIO", "ITEMIZED"}


def test_env_view_non_scalar_leaf_raises() -> None:
    node = KeyStore()
    node["GOOD"] = "x"
    node["BAD"] = Bind("/h", "/b")  # a bind under env is a build breach
    view = env_view(node)
    assert view["GOOD"] == "x"
    with pytest.raises(ViewError):
        view["BAD"]


def test_env_view_none_leaf_raises() -> None:
    node = KeyStore()
    node["NIL"] = None
    view = env_view(node)
    with pytest.raises(ViewError):
        view["NIL"]


# --------------------------------------------------------------------------- #
# Tier-2 — masks_set is honestly a set[box_dest]                              #
# --------------------------------------------------------------------------- #


def test_masks_set_returns_masked_dests() -> None:
    result = masks_set(_masks_node())
    assert isinstance(result, set)
    assert result == {"/secrets", "/home/agent/.ssh", "/box/getter"}


def test_masks_set_empty_node() -> None:
    assert masks_set(KeyStore()) == set()


def test_masks_set_surviving_none_raises() -> None:
    # A present-None unmask must have been DROPPED at build (§6f). A surviving
    # None is a build breach, not a mask marker → ViewError (the masks S22 check).
    node = KeyStore()
    node["/secrets"] = True
    node["/leaked"] = None
    with pytest.raises(ViewError):
        masks_set(node)


# --------------------------------------------------------------------------- #
# Tier-1 — finite view returns EXACT types                                    #
# --------------------------------------------------------------------------- #


def _meta_node() -> KeyStore:
    node = KeyStore()
    node["name"] = "mybox"
    node["root"] = "/workset/boxes/mybox"
    return node


def test_meta_view_exact_types() -> None:
    view = MetaView(_meta_node())
    assert view.name == "mybox"
    assert isinstance(view.name, str)
    assert view.root == Path("/workset/boxes/mybox")
    assert isinstance(view.root, Path)


def test_meta_view_missing_field_raises() -> None:
    node = KeyStore()
    node["name"] = "x"  # no ``root``
    view = MetaView(node)
    assert view.name == "x"
    with pytest.raises(ViewError):
        view.root


def test_typed_field_key_alias() -> None:
    # A field whose stored key differs from the Python attribute name (e.g. a
    # keyword like ``global`` → ``global_dir``) — the mechanism supports it.
    class _V(FiniteView):
        global_dir: Path = typed_field(as_path, key="global")  # type: ignore[assignment]

    node = KeyStore()
    node["global"] = "/etc/kanibako"
    v = _V(node)
    assert v.global_dir == Path("/etc/kanibako")


def test_typed_field_checking_coerce_rejects_mistyped_leaf() -> None:
    # The (C) foot-gun the Editor flagged: a bare ``str``/``bool`` constructor
    # would LAUNDER a mistyped leaf. The checking coercers REJECT it instead, so
    # a build bug surfaces as ViewError rather than a silently wrong value.

    class _V(FiniteView):
        flag: bool = typed_field(as_bool)  # type: ignore[assignment]
        label: str = typed_field(as_str)  # type: ignore[assignment]
        n: int = typed_field(as_int)  # type: ignore[assignment]
        root: Path = typed_field(as_path)  # type: ignore[assignment]

    # A stored string "false" must NOT launder to bool True.
    bad_flag = KeyStore()
    bad_flag["flag"] = "false"
    bad_flag["label"] = "ok"
    bad_flag["n"] = 1
    bad_flag["root"] = "/x"
    with pytest.raises(ViewError):
        _V(bad_flag).flag

    # A stored int 123 must NOT launder to str "123".
    bad_label = KeyStore()
    bad_label["flag"] = True
    bad_label["label"] = 123
    bad_label["n"] = 1
    bad_label["root"] = "/x"
    with pytest.raises(ViewError):
        _V(bad_label).label

    # A bool must NOT pass as_int (bool ⊂ int), and a non-str must not become a Path.
    assert as_int(5) == 5
    with pytest.raises(ValueError):
        as_int(True)
    with pytest.raises(ValueError):
        as_path(123)
    # Well-typed values pass through unchanged.
    assert as_bool(True) is True
    assert as_str("x") == "x"
    assert as_path("/p") == Path("/p")


# --------------------------------------------------------------------------- #
# Read-only + purity — accessors neither copy nor mutate the node             #
# --------------------------------------------------------------------------- #


def test_views_are_read_only_no_mutation_path() -> None:
    # Mapping (not MutableMapping) → no __setitem__ / __delitem__ surface.
    view = bind_map(_rw_node())
    assert not hasattr(view, "__setitem__")
    with pytest.raises(TypeError):
        view["~/new"] = BindEntry("/h")  # type: ignore[index]


def test_bind_map_reading_does_not_mutate_the_node() -> None:
    node = _rw_node()
    before = dict(node)
    view = bind_map(node)
    _ = [view[k] for k in view]
    assert dict(node) == before


def test_masks_set_does_not_mutate_node() -> None:
    node = _masks_node()
    before = dict(node)
    masks_set(node)
    assert dict(node) == before


# --------------------------------------------------------------------------- #
# Accessors require a real node, not a leaf                                    #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("bad", [Bind("/h", "/b"), "scalar", 5, None, ["x"]])
def test_accessors_reject_non_node(bad: object) -> None:
    for fn in (bind_map, env_view, masks_set):
        with pytest.raises(ViewError):
            fn(bad)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# bind_map / bind_maps — the DEST-KEYED tier-2 lens (R-5/R-6), now the ONLY one #
# --------------------------------------------------------------------------- #


def test_bind_map_is_a_readonly_dest_keyed_mapping() -> None:
    node = KeyStore({"~/.claude": BindEntry("/h/c"), "~/v": BindEntry("/h/v", "ro")})
    view = bind_map(node, label="box.bindings.rw")
    assert isinstance(view, Mapping)
    assert not isinstance(view, dict)
    assert len(view) == 2
    assert set(view) == {"~/.claude", "~/v"}
    assert view["~/v"] == BindEntry("/h/v", "ro")
    assert "~/.claude" in view
    with pytest.raises(KeyError):
        view["nope"]
    assert not hasattr(view, "__setitem__")


def test_bind_map_does_not_copy_the_node() -> None:
    node = KeyStore({"~/a": BindEntry("/h/a")})
    view = bind_map(node)
    node["~/b"] = BindEntry("/h/b")
    assert set(view) == {"~/a", "~/b"}


def test_bind_map_refuses_a_none_leaf() -> None:
    view = bind_map(KeyStore({"~/a": None}))
    with pytest.raises(ViewError):
        view["~/a"]


def test_bind_map_refuses_a_legacy_three_tuple_bind() -> None:
    # ⚑ THE ARITY TRAP at the read surface. A stale NAME-keyed arm handed to the
    # dest-keyed lens is REFUSED, not mis-read — the check is ``isinstance(...,
    # BindEntry)``, which is False for a ``Bind`` even though both are tuples.
    view = bind_map(KeyStore({"home": Bind("/h/src", "~/home")}), label="stale")
    with pytest.raises(ViewError) as exc:
        view["home"]
    assert "BindEntry" in str(exc.value)


def test_bind_map_refuses_a_mistyped_scalar_leaf() -> None:
    # Not only the arity trap: ANY non-BindEntry leaf under a bind-shaped node is
    # a build breach and is refused rather than type-laundered (S22).
    view = bind_map(KeyStore({"~/a": "not-a-bind"}))
    with pytest.raises(ViewError):
        view["~/a"]


def test_bind_maps_splits_ro_and_rw_and_empties_an_absent_arm() -> None:
    node = KeyStore({"rw": {"~/a": BindEntry("/h/a")}})
    ro, rw = bind_maps(node, label="box.bindings")
    assert dict(ro) == {}
    assert dict(rw) == {"~/a": BindEntry("/h/a")}


def test_bind_maps_splits_two_populated_arms() -> None:
    node = KeyStore({"ro": {"/usr/bin": BindEntry("/usr/bin")}, "rw": _rw_node()})
    ro, rw = bind_maps(node, label="box.bindings")
    assert dict(ro) == {"/usr/bin": BindEntry("/usr/bin")}
    assert rw["~/"] == BindEntry("/host/home")
    assert rw["~/vault"].opts == "Z,U"
    assert len(rw) == 3


def test_bind_map_rejects_a_non_node() -> None:
    for bad in ("scalar", 3, None, BindEntry("/h/a")):
        with pytest.raises(ViewError):
            bind_map(bad)  # type: ignore[arg-type]
