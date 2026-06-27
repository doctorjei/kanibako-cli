"""Unit tests for block 5 — ``config set`` validation + RAW write-back.

Covers the brief §3 checklist for :mod:`kanibako.settings_configset`:

Validation (:func:`validate_config_set`, the B5 severity split, S25):
* the forbidden ``:`` ``src:dest`` notation → Error (escaped ``\:`` allowed);
* a typed-scalar type mismatch → Error;
* a dangling ``@``-ref (no such config key) → Error;
* an unknown ``$VAR`` → Error;
* malformed token syntax → Error;
* a not-yet-existent host source path (literal) → Warn;
* a well-formed ``@``-ref repoint → OK, NO warning (B4).

Write-back (:func:`repoint_host_src`, S24): repoints ``host_src`` keeping
``box_dest`` + options, writes the FULL tuple as a structured list, refuses a
non-existent key / a non-category value, and stores the RAW (UNEXPANDED) form —
``@``-refs / ``$XDG`` / ``~`` preserved verbatim, NEVER an expanded literal (S12).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from kanibako.settings_configset import (
    OK,
    ConfigSetError,
    Error,
    Warn,
    make_ref_lookup,
    repoint_host_src,
    validate_config_set,
)
from kanibako.settings_store import KeyStore

# --------------------------------------------------------------------------- #
# Test stubs for the injected callbacks                                       #
# --------------------------------------------------------------------------- #

#: Refs that "exist" in the keyspace for most validation tests.
_KNOWN_REFS = {"workset.boxes", "system.data", "box.meta.name", "workset"}
#: Vars that are "known/resolvable" in context.
_KNOWN_VARS = {"XDG_DATA_HOME", "XDG_STATE_HOME", "AGENT", "WORKSET"}


def _ref_exists(dotted: str) -> bool:
    return dotted in _KNOWN_REFS


def _var_known(name: str) -> bool:
    return name in _KNOWN_VARS


def _always(_path: str) -> bool:
    return True


def _never(_path: str) -> bool:
    return False


def _validate(
    key: str,
    value: str,
    *,
    is_category: bool = False,
    host_exists=None,
):
    return validate_config_set(
        key,
        value,
        is_category=is_category,
        ref_exists=_ref_exists,
        var_known=_var_known,
        host_exists=host_exists,
    )


# --------------------------------------------------------------------------- #
# Verdict — the ``:`` src:dest notation → Error (S25)                          #
# --------------------------------------------------------------------------- #


def test_colon_notation_is_hard_error() -> None:
    v = _validate("box.bindings.rw.home", "/host:/box", is_category=True)
    assert isinstance(v, Error)
    assert ":" in v.message or "src:dest" in v.message


def test_escaped_colon_is_allowed() -> None:
    # An ESCAPED ``\:`` is a literal colon in a single path, NOT the forbidden
    # delimiter — split_bind only splits at an UNESCAPED colon.
    v = _validate("box.bindings.rw.home", r"/weird\:path", is_category=True)
    assert v is OK


# --------------------------------------------------------------------------- #
# Verdict — typed-scalar type mismatch → Error                                #
# --------------------------------------------------------------------------- #


def test_typed_scalar_mismatch_is_hard_error() -> None:
    # box.share_images is a bool key (KEY_TYPES) — a non-bool value fails coercion.
    v = _validate("box.share_images", "notabool")
    assert isinstance(v, Error)
    assert "boolean" in v.message.lower() or "bool" in v.message.lower()


def test_typed_scalar_valid_is_ok() -> None:
    v = _validate("box.share_images", "true")
    assert v is OK


def test_untyped_scalar_is_ok() -> None:
    # A plain non-typed scalar value passes (no KEY_TYPES entry).
    v = _validate("model", "opus")
    assert v is OK


# --------------------------------------------------------------------------- #
# Verdict — dangling @-ref / unknown $VAR → Error                             #
# --------------------------------------------------------------------------- #


def test_dangling_ref_is_hard_error() -> None:
    v = _validate("box.bindings.rw.home", "@nope.not.a.key", is_category=True)
    assert isinstance(v, Error)
    assert "@nope.not.a.key" in v.message


def test_existing_whole_value_ref_repoint_is_ok_no_warn() -> None:
    # B4: repointing host_src to a whole-value @-ref to an EXISTING key is OK, and
    # carries NO @-ref-repoint warning.
    v = _validate("box.bindings.rw.home", "@workset.boxes", is_category=True)
    assert v is OK


def test_embedded_ref_to_existing_key_is_ok() -> None:
    v = _validate(
        "box.bindings.rw.home", "@workset.boxes/sub/dir", is_category=True
    )
    assert v is OK


def test_embedded_dangling_ref_is_hard_error() -> None:
    v = _validate(
        "box.bindings.rw.home", "@workset.boxes/@bad.ref/x", is_category=True
    )
    assert isinstance(v, Error)
    assert "@bad.ref" in v.message


def test_unknown_var_is_hard_error() -> None:
    v = _validate("box.bindings.rw.home", "$NOPE_VAR/x", is_category=True)
    assert isinstance(v, Error)
    assert "$NOPE_VAR" in v.message


def test_known_var_is_ok() -> None:
    v = _validate(
        "box.bindings.rw.home", "$XDG_DATA_HOME/kanibako", is_category=True
    )
    assert v is OK


def test_braced_known_var_is_ok() -> None:
    v = _validate(
        "box.bindings.rw.home", "${XDG_DATA_HOME}/x", is_category=True
    )
    assert v is OK


def test_braced_unknown_var_is_hard_error() -> None:
    v = _validate("box.bindings.rw.home", "${NOPE}/x", is_category=True)
    assert isinstance(v, Error)
    assert "$NOPE" in v.message


# --------------------------------------------------------------------------- #
# Verdict — malformed token syntax → Error                                    #
# --------------------------------------------------------------------------- #


def test_unterminated_braced_var_is_hard_error() -> None:
    v = _validate("box.bindings.rw.home", "${XDG_DATA_HOME/x", is_category=True)
    assert isinstance(v, Error)
    assert "malformed" in v.message.lower()


def test_bare_dollar_then_nonname_is_hard_error() -> None:
    # ``$/`` — a ``$`` not followed by a valid variable name is malformed (matches
    # expand_expr, which raises on the same shape).
    v = _validate("box.bindings.rw.home", "$/notavar", is_category=True)
    assert isinstance(v, Error)


def test_escaped_dollar_is_literal_not_a_var() -> None:
    # ``\$`` is an escaped literal ``$`` — NOT a variable token, so no unknown-var
    # error (matches expand_expr's escape rule).
    v = _validate("box.bindings.rw.home", r"\$NOTAVAR/x", is_category=True)
    assert v is OK


def test_escaped_at_is_literal_not_a_ref() -> None:
    v = _validate("box.bindings.rw.home", r"\@nope/x", is_category=True)
    assert v is OK


# --------------------------------------------------------------------------- #
# Verdict — not-yet-existent host path → Warn (proceed)                        #
# --------------------------------------------------------------------------- #


def test_missing_host_path_literal_is_warn() -> None:
    v = _validate(
        "box.bindings.rw.home", "/not/here/yet", is_category=True, host_exists=_never
    )
    assert isinstance(v, Warn)
    assert "/not/here/yet" in v.message


def test_present_host_path_literal_is_ok() -> None:
    v = _validate(
        "box.bindings.rw.home", "/exists", is_category=True, host_exists=_always
    )
    assert v is OK


def test_token_bearing_host_src_is_not_path_checked() -> None:
    # A host_src carrying a token resolves at build — it is NOT a concrete host
    # path, so a "missing" host_exists must NOT fire a warn for it.
    v = _validate(
        "box.bindings.rw.home",
        "@workset.boxes/x",
        is_category=True,
        host_exists=_never,
    )
    assert v is OK


def test_scalar_key_is_never_path_checked() -> None:
    # host_exists is only consulted for a category key; a scalar value is never a
    # host path, even with a "never exists" probe.
    v = _validate("model", "opus", host_exists=_never)
    assert v is OK


def test_host_exists_omitted_defaults_to_no_warn() -> None:
    v = _validate("box.bindings.rw.home", "/whatever", is_category=True)
    assert v is OK


# --------------------------------------------------------------------------- #
# repoint_host_src — the RAW category write-back (S24)                         #
# --------------------------------------------------------------------------- #


def _write_scope(path: Path, data: dict) -> Path:
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    return path


def test_repoint_swaps_host_keeps_dest_2tuple(tmp_path: Path) -> None:
    f = _write_scope(
        tmp_path / "box.yaml",
        {"box": {"bindings": {"rw": {"home": ["@workset.boxes/home", "~/"]}}}},
    )
    repoint_host_src(f, "box.bindings.rw.home", "/new/host/home")
    out = yaml.safe_load(f.read_text())
    assert out["box"]["bindings"]["rw"]["home"] == ["/new/host/home", "~/"]


def test_repoint_keeps_options_3tuple(tmp_path: Path) -> None:
    f = _write_scope(
        tmp_path / "box.yaml",
        {"box": {"bindings": {"rw": {"sock": ["/old/sock", "~/x.sock", "z"]}}}},
    )
    repoint_host_src(f, "box.bindings.rw.sock", "/new/sock")
    out = yaml.safe_load(f.read_text())
    assert out["box"]["bindings"]["rw"]["sock"] == ["/new/sock", "~/x.sock", "z"]


def test_repoint_stores_raw_ref_not_expanded(tmp_path: Path) -> None:
    # The written host_src is the RAW form: an @-ref / $XDG / ~ is stored VERBATIM,
    # never expanded to a literal (S12 / spec §0 files store UNRESOLVED).
    f = _write_scope(
        tmp_path / "box.yaml",
        {"box": {"bindings": {"rw": {"home": ["/old", "~/"]}}}},
    )
    repoint_host_src(f, "box.bindings.rw.home", "@workset.vault_rw/data")
    out = yaml.safe_load(f.read_text())
    assert out["box"]["bindings"]["rw"]["home"] == ["@workset.vault_rw/data", "~/"]
    # And the box_dest ~ is also preserved raw (not expanded host-side).
    assert out["box"]["bindings"]["rw"]["home"][1] == "~/"


def test_repoint_preserves_raw_box_dest_xdg_token(tmp_path: Path) -> None:
    f = _write_scope(
        tmp_path / "box.yaml",
        {
            "box": {
                "bindings": {
                    "ro": {"log": ["/old", "$XDG_STATE_HOME/kanibako/h.jsonl"]}
                }
            }
        },
    )
    repoint_host_src(f, "box.bindings.ro.log", "@workset.logs/box.jsonl")
    out = yaml.safe_load(f.read_text())
    assert out["box"]["bindings"]["ro"]["log"] == [
        "@workset.logs/box.jsonl",
        "$XDG_STATE_HOME/kanibako/h.jsonl",
    ]


def test_repoint_missing_key_raises(tmp_path: Path) -> None:
    f = _write_scope(tmp_path / "box.yaml", {"box": {"image": "x"}})
    with pytest.raises(ConfigSetError, match="must already exist"):
        repoint_host_src(f, "box.bindings.rw.home", "/new")


def test_repoint_missing_intermediate_raises(tmp_path: Path) -> None:
    f = _write_scope(
        tmp_path / "box.yaml",
        {"box": {"bindings": {"rw": {"vault": ["/v", "~/vault"]}}}},
    )
    with pytest.raises(ConfigSetError, match="must already exist"):
        repoint_host_src(f, "box.bindings.ro.images", "/new")


def test_repoint_non_category_value_raises(tmp_path: Path) -> None:
    f = _write_scope(tmp_path / "box.yaml", {"box": {"image": "ghcr/x"}})
    with pytest.raises(ConfigSetError, match="not a category tuple"):
        repoint_host_src(f, "box.image", "scalar")


def test_repoint_preserves_other_content(tmp_path: Path) -> None:
    f = _write_scope(
        tmp_path / "box.yaml",
        {
            "box": {
                "image": "ghcr/x",
                "bindings": {
                    "rw": {
                        "home": ["/old", "~/"],
                        "vault": ["/v", "~/vault"],
                    }
                },
            }
        },
    )
    repoint_host_src(f, "box.bindings.rw.home", "/new")
    out = yaml.safe_load(f.read_text())
    # Sibling bind + the scalar both survive untouched.
    assert out["box"]["bindings"]["rw"]["vault"] == ["/v", "~/vault"]
    assert out["box"]["image"] == "ghcr/x"
    assert out["box"]["bindings"]["rw"]["home"] == ["/new", "~/"]


def test_repoint_writes_a_list_not_a_colon_string(tmp_path: Path) -> None:
    f = _write_scope(
        tmp_path / "box.yaml",
        {"box": {"bindings": {"rw": {"home": ["/old", "~/"]}}}},
    )
    repoint_host_src(f, "box.bindings.rw.home", "/new")
    leaf = yaml.safe_load(f.read_text())["box"]["bindings"]["rw"]["home"]
    assert isinstance(leaf, list)
    assert ":" not in str(leaf[0]) or leaf[0] == "/new"  # no colon-join


# --------------------------------------------------------------------------- #
# make_ref_lookup — snapshot-backed RefLookup (S3)                            #
# --------------------------------------------------------------------------- #


def test_make_ref_lookup_finds_present_key() -> None:
    snap = KeyStore({"workset": {"boxes": "@x"}, "box": {"image": "y"}})
    lk = make_ref_lookup(snap)
    assert lk("workset.boxes") is True
    assert lk("box.image") is True


def test_make_ref_lookup_absent_key_false() -> None:
    snap = KeyStore({"workset": {"boxes": "@x"}})
    lk = make_ref_lookup(snap)
    assert lk("workset.nope") is False
    assert lk("nothing.here") is False


def test_make_ref_lookup_present_none_counts_as_existing() -> None:
    # A present-None leaf is SET (just to None) — a legitimate @-ref target (§3).
    snap = KeyStore({"box": {"agent": None}})
    lk = make_ref_lookup(snap)
    assert lk("box.agent") is True


def test_make_ref_lookup_handles_collision_named_keys() -> None:
    # A key literally named ``get`` must not crash the lookup (S3 — unbound probe).
    snap = KeyStore({"box": {"get": "x", "items": "y"}})
    lk = make_ref_lookup(snap)
    assert lk("box.get") is True
    assert lk("box.items") is True


def test_make_ref_lookup_descend_through_scalar_is_false() -> None:
    # A dotted path that tries to descend THROUGH a scalar leaf does not exist.
    snap = KeyStore({"box": {"image": "ghcr/x"}})
    lk = make_ref_lookup(snap)
    assert lk("box.image.deeper") is False


# --------------------------------------------------------------------------- #
# Integration: validate then write (the full source-only repoint path)        #
# --------------------------------------------------------------------------- #


def test_validate_then_repoint_roundtrip(tmp_path: Path) -> None:
    f = _write_scope(
        tmp_path / "box.yaml",
        {"box": {"bindings": {"rw": {"vault": ["@workset.vault_rw/x", "~/vault"]}}}},
    )
    snap = KeyStore({"workset": {"boxes": "y", "vault_rw": "z"}})
    verdict = validate_config_set(
        "box.bindings.rw.vault",
        "@workset.boxes/custom",
        is_category=True,
        ref_exists=make_ref_lookup(snap),
        var_known=_var_known,
    )
    assert verdict is OK
    repoint_host_src(f, "box.bindings.rw.vault", "@workset.boxes/custom")
    out = yaml.safe_load(f.read_text())
    assert out["box"]["bindings"]["rw"]["vault"] == [
        "@workset.boxes/custom",
        "~/vault",
    ]
