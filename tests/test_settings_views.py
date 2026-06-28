"""Unit tests for the typed access layer (block 4: settings_views).

Covers the brief's checklist (§4): tier-2 category accessors yield ``Bind`` (never
``Bind | None``); iteration / len / contains; an arbitrary DYNAMIC key (a bind
named ``getter``) resolves correctly through the accessor (block 1b retired the
reserved-name ``get`` collision case — that name is now unstorable); the ``env``
accessor yields
scalars; ``masks_set`` returns exactly the masked dests as a ``set`` (present-None
already dropped by build — fed a post-build node); the tier-1 finite view returns
EXACT types; read-only (no mutation path); purity (the accessor does not copy or
mutate the wrapped node); and the S22 coupling — a ``None`` bind leaf RAISES (a
build-invariant breach), never type-launders.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import pytest

from kanibako.settings_store import Bind, KeyStore
from kanibako.settings_views import (
    FiniteView,
    MetaView,
    ViewError,
    as_bool,
    as_int,
    as_path,
    as_str,
    bind_category,
    bindings,
    env_view,
    masks_set,
    typed_field,
)


# --------------------------------------------------------------------------- #
# Fixtures — small post-build-shaped snapshot nodes                           #
# --------------------------------------------------------------------------- #


def _rw_node() -> KeyStore:
    """A ``bindings.rw`` node: all-``Bind`` leaves, incl. a dynamic key.

    Block 1b: reserved dict-method names (``get``/``items``/...) can no longer be
    stored, so the dynamic-key case uses a non-reserved name (``getter``); the
    accessor must still resolve an arbitrary dynamic bind key, which is the point.
    """
    node = KeyStore()
    node["home"] = Bind("/host/home", "/home/agent")
    node["vault"] = Bind("@workset.vault_rw", "/vault", "Z,U")
    node["getter"] = Bind("/host/getter", "/box/getter")
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
# Tier-2 — bind_category yields Bind, never Bind | None                        #
# --------------------------------------------------------------------------- #


def test_bind_category_yields_bind_values() -> None:
    view = bind_category(_rw_node(), label="box.bindings.rw")
    assert isinstance(view, Mapping)
    assert isinstance(view["home"], Bind)
    assert view["home"] == Bind("/host/home", "/home/agent")
    assert view["vault"].opts == "Z,U"
    # Every value is a Bind (the S22 typed contract).
    for key in view:
        assert isinstance(view[key], Bind)


def test_bind_category_dynamic_key_resolves() -> None:
    # A bind under an arbitrary dynamic key resolves to the STORED bind. (Block
    # 1b retired the reserved-name ``get`` collision case — that name is now
    # unstorable; the accessor must still handle a generic dynamic key.)
    view = bind_category(_rw_node())
    assert view["getter"] == Bind("/host/getter", "/box/getter")
    assert "getter" in view
    assert isinstance(view["getter"], Bind)


def test_bind_category_iter_len_contains() -> None:
    view = bind_category(_rw_node())
    assert len(view) == 3
    assert set(view) == {"home", "vault", "getter"}
    assert "home" in view
    assert "absent" not in view
    assert dict(view.items()) == {
        "home": Bind("/host/home", "/home/agent"),
        "vault": Bind("@workset.vault_rw", "/vault", "Z,U"),
        "getter": Bind("/host/getter", "/box/getter"),
    }


def test_bind_category_missing_key_raises_keyerror() -> None:
    view = bind_category(_rw_node())
    with pytest.raises(KeyError):
        view["nope"]


# --------------------------------------------------------------------------- #
# S22 — a None / mistyped bind leaf is a build breach → ViewError              #
# --------------------------------------------------------------------------- #


def test_bind_category_none_leaf_raises_not_type_launders() -> None:
    # Build (block 2b) OMITS present-None binds; a surviving None under a bind
    # category is an invariant breach. The accessor RAISES rather than exposing
    # a None where the contract promises Bind (S22).
    node = KeyStore()
    node["home"] = Bind("/h", "/b")
    node["broken"] = None  # would-be present-None bind that build should've dropped
    view = bind_category(node)
    assert view["home"] == Bind("/h", "/b")
    with pytest.raises(ViewError):
        view["broken"]


def test_bind_category_mistyped_leaf_raises() -> None:
    node = KeyStore()
    node["oops"] = "not-a-bind"
    view = bind_category(node)
    with pytest.raises(ViewError):
        view["oops"]


# --------------------------------------------------------------------------- #
# Tier-2 — bindings() splits a whole node into (ro, rw)                        #
# --------------------------------------------------------------------------- #


def test_bindings_splits_ro_rw() -> None:
    node = KeyStore()
    ro = KeyStore()
    ro["bin"] = Bind("/usr/bin", "/usr/bin")
    node["ro"] = ro
    node["rw"] = _rw_node()
    ro_view, rw_view = bindings(node, label="box.bindings")
    assert ro_view["bin"] == Bind("/usr/bin", "/usr/bin")
    assert rw_view["home"] == Bind("/host/home", "/home/agent")
    assert len(ro_view) == 1
    assert len(rw_view) == 3


def test_bindings_absent_mode_is_empty_mapping() -> None:
    # A mode the build omitted (absent / fully reset, §3/§6e) → an EMPTY lens,
    # never an error.
    node = KeyStore()
    node["rw"] = _rw_node()  # no ``ro`` at all
    ro_view, rw_view = bindings(node)
    assert len(ro_view) == 0
    assert dict(ro_view) == {}
    assert len(rw_view) == 3


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
    node["group_auth_available"] = True
    return node


def test_meta_view_exact_types() -> None:
    view = MetaView(_meta_node())
    assert view.name == "mybox"
    assert isinstance(view.name, str)
    assert view.root == Path("/workset/boxes/mybox")
    assert isinstance(view.root, Path)
    assert view.group_auth_available is True
    assert isinstance(view.group_auth_available, bool)


def test_meta_view_missing_field_raises() -> None:
    node = KeyStore()
    node["name"] = "x"  # no ``root`` / ``group_auth_available``
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
    view = bind_category(_rw_node())
    assert not hasattr(view, "__setitem__")
    with pytest.raises(TypeError):
        view["new"] = Bind("/h", "/b")  # type: ignore[index]


def test_bind_category_does_not_copy_or_mutate_node() -> None:
    node = _rw_node()
    before = dict(node)
    view = bind_category(node)
    # Reading does not mutate the wrapped node.
    _ = [view[k] for k in view]
    assert dict(node) == before
    # The view reflects the SAME node (no copy): a later write to the node shows
    # through — proving it is a live lens, the single source of truth (design §0).
    node["late"] = Bind("/h/late", "/b/late")
    assert view["late"] == Bind("/h/late", "/b/late")
    assert len(view) == len(before) + 1


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
    for fn in (bind_category, env_view, masks_set):
        with pytest.raises(ViewError):
            fn(bad)  # type: ignore[arg-type]
