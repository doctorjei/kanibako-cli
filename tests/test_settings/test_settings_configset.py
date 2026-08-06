"""Unit tests for ``config set`` validation + RAW write-back (block 5 + Q9).

Covers :mod:`kanibako.settings.settings_configset`:

Validation (:func:`validate_config_set` — Q9 FULL RESOLUTION + the E3 rule):
* the forbidden ``:`` ``src:dest`` notation → Error (escaped ``\:`` allowed);
* a typed-scalar type mismatch → Error;
* malformed token syntax → Error (pure pre-check);
* the E3 probe returns a reason (edited value's upstream chain unresolvable) →
  Error naming the broken dep; ``None`` (resolves cleanly) → OK;
* a not-yet-existent host source path (literal) → Warn;
* a well-formed repoint that resolves cleanly → OK, NO warning (B4).

The E3 ``resolves`` probe is exercised here with a REAL lenient-``expand``-backed
stub (apply the candidate at the key into a known keyspace, lenient-expand, read
the edited key's defect) — so the validate tests test the wiring AND real
resolution; the full multi-scope seam build is tested in test_config_interface.

Write-back (:func:`repoint_host_src`, S24): repoints ``host_src`` keeping
``box_dest`` + options, writes the FULL tuple as a structured list, refuses a
key that exists NOWHERE in the cascade (F10 — the command file's own tuple is
used when present, else the caller-supplied *cascade_bind*) / a non-category
value, and stores the RAW (UNEXPANDED) form — ``@``-refs / ``$XDG`` / ``~``
preserved verbatim, NEVER an expanded literal (S12).

⚑⚑ READ BEFORE COPYING A KEY OUT OF THIS FILE. Many fixtures here spell
``box.bindings.rw.<name>`` / ``workset.bindings.ro.<name>``. **That is NOT a
settable key any more, and neither is ``agent.<node>.bindings.{ro,rw}.<name>``**
— R-9 retired the bind CLI write route at EVERY scope, and ``config set``/``reset``
refuse both by name (``config_keys.scope_bind_retired_error`` /
``config_keys.agent_node_bind_retired_error``). The spellings survive here because
BOTH functions under test are PURE and KEY-AGNOSTIC: ``validate_config_set``
uses the key as a message label, and ``repoint_host_src`` takes an arbitrary
dotted path and edits the YAML at it — neither consults the keyspace, and a
``bindings.{ro,rw}`` NESTED PATH is still live in the settings FILES the launch
reads, which is the shape these fixtures exercise.

The ONE place the key is genuinely interpreted is ``_rooted_form_hint``, and that
test now uses ``synced`` at every scope on purpose — see
``test_concrete_category_gets_no_rooted_hint``, which explains why a bindings
spelling would pass VACUOUSLY. Do not read any other key in this file as evidence
that a bind is settable from the CLI.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from kanibako.settings.settings_configset import (
    OK,
    ConfigSetError,
    Error,
    Warn,
    repoint_host_src,
    validate_config_set,
)
from kanibako.settings.settings_expand import expand
from kanibako.settings.settings_resolve import ResolveCtx
from kanibako.settings.settings_store import _MISSING, KeyStore

# --------------------------------------------------------------------------- #
# Test stubs for the injected callbacks                                       #
# --------------------------------------------------------------------------- #

#: A keyspace the @-refs in most validation tests resolve against (real values so
#: the lenient expand actually resolves a chain, not just an existence map).
_KEYSPACE: dict = {
    "workset": {"boxes": "/ws/boxes", "vault_rw": "/ws/vault"},
    "system": {"data": "/sys/data"},
    "box": {"meta": {"name": "mybox"}},
}
#: A ctx whose XDG/home set makes $XDG_DATA_HOME/$XDG_STATE_HOME resolvable and
#: $NOPE_VAR / $XDG_CACHE_HOME unset (→ a defect the lenient expand records).
_CTX = ResolveCtx(
    agent_name="claude",
    workset_name="ws",
    host_home="/home/u",
    xdg={
        "XDG_DATA_HOME": "/home/u/.local/share",
        "XDG_STATE_HOME": "/home/u/.local/state",
    },
)


def _resolves(key: str, value: str):
    """The E3 RESOLUTION probe stub: apply *value* at *key* into a fresh copy of
    ``_KEYSPACE``, lenient-``expand``, and return the edited key's defect reason (or
    ``None`` if it resolves cleanly) — the same contract the real seam builds."""
    import copy

    candidate = KeyStore(copy.deepcopy(_KEYSPACE))
    node = candidate
    parts = key.split(".")
    for seg in parts[:-1]:
        sub = dict.get(node, seg, None)
        if not isinstance(sub, KeyStore):
            sub = KeyStore()
            node[seg] = sub
        node = sub
    node[parts[-1]] = value
    _expanded, errors = expand(candidate, _CTX, collect_errors=True)
    reason = dict.get(errors, key, _MISSING)
    return None if reason is _MISSING else reason


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
    resolves=_resolves,
):
    return validate_config_set(
        key,
        value,
        is_category=is_category,
        resolves=resolves,
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
    #
    # ⚑ Anchored ABSOLUTE so the escape rule is what is under test.  P3 added a
    # separate refusal for a bare-relative category source, and ``\$NOTAVAR/x`` is
    # bare-relative once unescaped — it would now be refused for that OTHER reason,
    # which would leave this test green for the wrong cause (or red for one).  The
    # interaction itself is asserted in
    # ``TestRelativeCategorySourceRefused::test_escaped_token_still_relative``.
    v = _validate("box.bindings.rw.home", r"/abs/\$NOTAVAR/x", is_category=True)
    assert v is OK


def test_escaped_at_is_literal_not_a_ref() -> None:
    v = _validate("box.bindings.rw.home", r"/abs/\@nope/x", is_category=True)
    assert v is OK


# --------------------------------------------------------------------------- #
# BRACED @{name} refs (PHASE R) — one grammar with the resolver               #
# --------------------------------------------------------------------------- #


def test_braced_ref_scans_as_one_token_not_a_swallowed_suffix() -> None:
    # ``_scan_tokens`` calls the resolver's OWN parser, so the ref name stops at
    # the closing brace and the ``.jsonl`` suffix is a literal — the whole point
    # of the form. (Bare ``@box.meta.name.jsonl`` would yield one dotted name.)
    from kanibako.settings.settings_configset import _scan_tokens

    assert _scan_tokens("@{box.meta.name}.jsonl") == (["box.meta.name"], [])
    assert _scan_tokens("@{a}/@{b}.x") == (["a", "b"], [])
    assert _scan_tokens("@box.meta.name.jsonl") == (["box.meta.name.jsonl"], [])


def test_braced_ref_that_resolves_is_ok() -> None:
    # ``box.meta.name`` exists in _KEYSPACE → the E3 probe resolves cleanly.
    v = _validate(
        "box.bindings.ro.helper_log",
        "@workset.boxes/@{box.meta.name}.jsonl",
        is_category=True,
    )
    assert v is OK


def test_dangling_braced_ref_is_hard_error_like_a_bare_one() -> None:
    # PHASE R: a dangling BRACED ref is judged exactly as a dangling bare one.
    braced = _validate(
        "box.bindings.rw.home", "@{nope.missing}/x", is_category=True
    )
    bare = _validate("box.bindings.rw.home", "@nope.missing/x", is_category=True)
    assert isinstance(braced, Error) and isinstance(bare, Error)
    assert "nope.missing" in braced.message


def test_malformed_braced_ref_is_hard_error() -> None:
    for value in ("@{a b}/x", "@{unclosed/x", "@{}/x"):
        v = _validate("box.bindings.rw.home", value, is_category=True)
        assert isinstance(v, Error), value
        assert "malformed" in v.message.lower(), value


def test_both_token_families_report_in_the_resolvers_message_style() -> None:
    """One grammar means one ERROR STYLE too, for ``$`` and ``@`` alike.

    PHASE R first routed only the ``@`` arm through the resolver's parser, which
    left ``_scan_tokens`` speaking two message styles in one function — the exact
    "two forms for one thing" this codebase treats as a defect, and the kind that
    gets copied. Both arms now re-raise the resolver's own text.
    """
    from kanibako.settings.settings_configset import _scan_tokens

    for value, expected in (
        ("@", "Malformed @-reference at:"),
        ("$", "Malformed variable reference at:"),
        ("@{x", "Unterminated @{...} reference:"),
        ("${X", "Unterminated ${...} reference:"),
    ):
        with pytest.raises(ValueError) as ei:
            _scan_tokens(value)
        assert str(ei.value).startswith(expected), (value, str(ei.value))


def test_escaped_at_brace_is_literal_not_a_ref() -> None:
    # ``\@{`` is the escape hatch for a literal ``@{`` — not a token, so no
    # dangling-ref error (matches expand_expr's escape rule).
    # Anchored ABSOLUTE — see ``test_escaped_dollar_is_literal_not_a_var``.
    v = _validate("box.bindings.rw.home", r"/abs/\@{nope}/x", is_category=True)
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
# repoint_host_src — the F10 cascade fallback (key-must-exist-in-the-CASCADE)  #
# --------------------------------------------------------------------------- #


def test_repoint_cascade_fallback_writes_full_tuple_creating_tables(
    tmp_path: Path,
) -> None:
    # Key absent from the command-scope file: the caller-supplied effective
    # cascade tuple backs the repoint — dest + opts preserved BYTE-RAW from it,
    # the file's intermediate tables created (F10, spec §2a must-exist-in-the-
    # CASCADE). This test FAILS if the lookup reverts to scope-file-only
    # (mutation proof: without cascade_bind support this raises).
    f = _write_scope(tmp_path / "box.yaml", {"box": {"image": "x"}})
    repoint_host_src(
        f,
        "box.bindings.rw.vault",
        "~/mine",
        cascade_bind=["@config.data/vault", "$XDG_DATA_HOME/vault", "z"],
    )
    out = yaml.safe_load(f.read_text())
    assert out["box"]["bindings"]["rw"]["vault"] == [
        "~/mine", "$XDG_DATA_HOME/vault", "z",
    ]
    assert out["box"]["image"] == "x"  # sibling content untouched


def test_repoint_cascade_fallback_2tuple_stays_2list(tmp_path: Path) -> None:
    # A 2-element cascade tuple writes a 2-list — no null 3rd (options) slot.
    f = _write_scope(tmp_path / "box.yaml", {})
    repoint_host_src(
        f, "box.caches.x", "/new", cascade_bind=["/old", "~/.cache/x"],
    )
    assert yaml.safe_load(f.read_text())["box"]["caches"]["x"] == [
        "/new", "~/.cache/x",
    ]


def test_repoint_cascade_fallback_into_missing_file(tmp_path: Path) -> None:
    # The command-scope file need not exist yet: a cascade-backed repoint
    # CREATES it holding just the override tuple (the downward-default shape).
    f = tmp_path / "absent.yaml"
    repoint_host_src(f, "box.bindings.ro.v", "/new", cascade_bind=["/o", "/d"])
    assert yaml.safe_load(f.read_text())["box"]["bindings"]["ro"]["v"] == [
        "/new", "/d",
    ]


def test_repoint_file_tuple_preferred_over_cascade(tmp_path: Path) -> None:
    # The command file's own tuple (== the merge winner at that scope) supplies
    # dest/opts when present — cascade_bind is only the fallback.
    f = _write_scope(
        tmp_path / "box.yaml",
        {"box": {"bindings": {"rw": {"h": ["/f-old", "/file-dest"]}}}},
    )
    repoint_host_src(
        f,
        "box.bindings.rw.h",
        "/new",
        cascade_bind=["/c-old", "/cascade-dest", "z"],
    )
    assert yaml.safe_load(f.read_text())["box"]["bindings"]["rw"]["h"] == [
        "/new", "/file-dest",
    ]


def test_repoint_cascade_bad_arity_raises(tmp_path: Path) -> None:
    f = _write_scope(tmp_path / "box.yaml", {})
    with pytest.raises(ConfigSetError, match="not a category tuple"):
        repoint_host_src(
            f, "box.bindings.rw.h", "/new", cascade_bind=["only-one"],
        )
    assert yaml.safe_load(f.read_text()) == {}  # nothing written


def test_repoint_non_mapping_intermediate_refuses(tmp_path: Path) -> None:
    # A file intermediate that exists but is NOT a mapping is never clobbered.
    f = _write_scope(tmp_path / "box.yaml", {"box": {"bindings": "oops"}})
    with pytest.raises(ConfigSetError, match="not a mapping"):
        repoint_host_src(
            f, "box.bindings.rw.h", "/new", cascade_bind=["/o", "/d"],
        )
    assert yaml.safe_load(f.read_text()) == {"box": {"bindings": "oops"}}


# --------------------------------------------------------------------------- #
# E3 rule (Q9) — allow iff the edited value resolves cleanly post-edit          #
# --------------------------------------------------------------------------- #
# The E3 matrix (brief a–h). The ``resolves`` stub applies the edited value into a
# keyspace WITH a pre-seeded defect where the case needs one, lenient-expands, and
# the verdict turns ONLY on the edited key's own resolution.


def _resolves_with(extra: dict, *, key: str, value: str):
    """E3 probe over ``_KEYSPACE`` PLUS *extra* pre-seeded keys (defects/deps), so a
    case can place a pre-existing defect somewhere and edit a different key."""
    import copy

    space = copy.deepcopy(_KEYSPACE)
    space.update(copy.deepcopy(extra))
    candidate = KeyStore(space)
    node = candidate
    parts = key.split(".")
    for seg in parts[:-1]:
        sub = dict.get(node, seg, None)
        if not isinstance(sub, KeyStore):
            sub = KeyStore()
            node[seg] = sub
        node = sub
    node[parts[-1]] = value
    _expanded, errors = expand(candidate, _CTX, collect_errors=True)
    reason = dict.get(errors, key, _MISSING)
    return None if reason is _MISSING else reason


def test_e3_a_unrelated_preexisting_defect_allows() -> None:
    # (a) A defect on a DIFFERENT branch does not block an unrelated edit.
    def probe(key, value):
        return _resolves_with(
            {"other": {"broken": "@gone.x"}}, key=key, value=value
        )

    v = _validate(
        "box.bindings.rw.home", "@workset.boxes/ok", is_category=True, resolves=probe
    )
    assert v is OK


def test_e3_b_downstream_child_defect_allows() -> None:
    # (b) A DOWNSTREAM consumer of the edited value is broken; the EDITED value
    # itself resolves → ALLOW. (`dependent` refs the edited key, but `dependent` is
    # not what we edit; its breakage is downstream.)
    def probe(key, value):
        return _resolves_with(
            {"dependent": "@box.bindings.rw.home/@also.gone"},
            key=key, value=value,
        )

    v = _validate(
        "box.bindings.rw.home", "/clean/literal", is_category=True, resolves=probe
    )
    assert v is OK


def test_e3_c_edit_repoints_away_from_broken_dep_allows() -> None:
    # (c) The OLD value referenced a broken dep; the edit re-points to a good one.
    v = _validate(
        "box.bindings.rw.home", "@workset.boxes/x", is_category=True
    )
    assert v is OK


def test_e3_d_edit_fixes_broken_key_itself_allows() -> None:
    # (d) Editing the broken key to a good value resolves it (and dependents
    # recover). Here we edit the key directly to a clean literal.
    def probe(key, value):
        return _resolves_with({}, key=key, value=value)

    v = _validate(
        "workset.vault_rw", "/now/good", is_category=True, resolves=probe
    )
    assert v is OK


def test_e3_e_edited_value_upstream_chain_broken_blocks() -> None:
    # (e) The edited value's UPSTREAM chain stays unresolvable → BLOCK, name the dep.
    v = _validate(
        "box.bindings.rw.home", "@gone.upstream/x", is_category=True
    )
    assert isinstance(v, Error)
    assert "gone.upstream" in v.message


def test_e3_e_embedded_dangling_in_edited_value_blocks() -> None:
    # (e′ — director ruling) an EMBEDDED dangling @-ref in the EDITED value's chain
    # is a defect that BLOCKS at set-time (NOT strict-expand's silent "").
    v = _validate(
        "box.bindings.rw.home", "@workset.boxes/@bad.embedded/x", is_category=True
    )
    assert isinstance(v, Error)
    assert "bad.embedded" in v.message


def test_e3_embedded_dangling_ELSEWHERE_allows() -> None:
    # (E3 holds) an embedded dangler on an UNRELATED key does not block; the edited
    # value resolves cleanly.
    def probe(key, value):
        return _resolves_with(
            # ⚑ BRACED: ``-`` is a ref-name char, so the bare spelling would name
            # ``gone.embedded-post``.  Inert either way (this is the UNRELATED
            # key), but the braces keep the fixture reading as written.
            {"other": {"x": "pre-@{gone.embedded}-post"}}, key=key, value=value
        )

    v = _validate(
        "box.bindings.rw.home", "/clean", is_category=True, resolves=probe
    )
    assert v is OK


def test_e3_f_edit_introduces_cycle_blocks() -> None:
    # (f) The edit makes the edited value's chain a CYCLE → BLOCK.
    def probe(key, value):
        # editing `a` to @b, with b=@a already present → a -> b -> a cycle.
        return _resolves_with({"b": "@a"}, key=key, value=value)

    v = _validate("a", "@b", is_category=True, resolves=probe)
    assert isinstance(v, Error)
    assert "cyclic" in v.message.lower()


def test_e3_g_preexisting_cycle_elsewhere_allows() -> None:
    # (g) A cycle on an UNRELATED branch does not block the edit.
    def probe(key, value):
        return _resolves_with(
            {"loop_a": "@loop_b", "loop_b": "@loop_a"}, key=key, value=value
        )

    v = _validate(
        "box.bindings.rw.home", "@workset.boxes/x", is_category=True, resolves=probe
    )
    assert v is OK


def test_e3_h_not_yet_existent_host_path_warns() -> None:
    # (h) A literal host path that does not exist yet → WARN, proceed.
    v = _validate(
        "box.bindings.rw.home", "/not/here/yet", is_category=True, host_exists=_never
    )
    assert isinstance(v, Warn)


# --------------------------------------------------------------------------- #
# Integration: validate then write (the full source-only repoint path)        #
# --------------------------------------------------------------------------- #


def test_validate_then_repoint_roundtrip(tmp_path: Path) -> None:
    f = _write_scope(
        tmp_path / "box.yaml",
        {"box": {"bindings": {"rw": {"vault": ["@workset.vault_rw/x", "~/vault"]}}}},
    )
    verdict = _validate(
        "box.bindings.rw.vault", "@workset.boxes/custom", is_category=True
    )
    assert verdict is OK
    repoint_host_src(f, "box.bindings.rw.vault", "@workset.boxes/custom")
    out = yaml.safe_load(f.read_text())
    assert out["box"]["bindings"]["rw"]["vault"] == [
        "@workset.boxes/custom",
        "~/vault",
    ]


# --------------------------------------------------------------------------- #
# A bare-relative category host_src → Error (P3 / spec §2a)          #
# --------------------------------------------------------------------------- #


class TestRelativeCategorySourceRefused:
    """T9 — ``config set`` REFUSES a bare-relative category ``host_src``.

    ⚑ Nothing else can catch this.  A literal ``plugins`` resolves PERFECTLY (to
    the relative string ``plugins``), so the E3 resolution probe passes it; the
    mount spec then interprets it against whatever the launching process's CWD
    happens to be.  It is not a broken value, it is a value that silently means
    somewhere else — and the layer that used to supply a root (``scope_roots``) is
    gone, because sources are rooted AT DECLARATION now and ``config set`` is not a
    declaration site.

    ``workset share add`` is deliberately ASYMMETRIC (it absolutises instead): it
    DOCUMENTED a join and must keep producing the same mount for the same input.
    ``config set`` never promised one, so there is nothing to preserve and no
    defensible root to invent.
    """

    @pytest.mark.parametrize(
        "key",
        [
            "box.bindings.rw.home",
            "workset.bindings.ro.docs",
            "agent.claude.common.plugins",
            "agent.claude.caches.transform",
            "system.seeded.template",
        ],
    )
    def test_relative_source_is_refused(self, key) -> None:
        v = _validate(key, "plugins", is_category=True)
        assert isinstance(v, Error)
        assert key in v.message
        assert "plugins" in v.message

    @pytest.mark.parametrize(
        ("key", "rooted"),
        [
            ("agent.claude.common.plugins",
             "@meta.agent.claude.path/common/plugins"),
            ("agent.navigator℘claude.caches.x",
             "@meta.agent.navigator℘claude.path/caches/x"),
            ("workset.common.x", "@meta.workset.path/common/x"),
            ("box.seeded.x", "@meta.box.path/seeded/x"),
            ("system.caches.x", "@config.data/caches/x"),
        ],
    )
    def test_rooted_hint_is_per_scope(self, key, rooted) -> None:
        """⚑ The hint names the root of the scope the user actually typed.

        Spec §2a gives a DIFFERENT root per scope. A single agent-shaped
        hint would tell someone editing ``workset.common.x`` to spell an
        ``@meta.agent.*`` root that has nothing to do with their key — a
        confidently wrong instruction, which is worse than saying nothing.

        The agent row names the agent the user typed (persona nodes included), so
        the suggestion is copy-pasteable rather than a ``<agent>`` placeholder.
        """
        value = key.rsplit(".", 1)[-1]
        v = _validate(key, value, is_category=True)
        assert isinstance(v, Error)
        assert rooted in v.message

    @pytest.mark.parametrize(
        "key",
        [
            "agent.c.synced.s",
            "box.synced.s",
            "workset.synced.d",
        ],
    )
    def test_concrete_category_gets_no_rooted_hint(self, key) -> None:
        """``synced`` takes NO root at any scope (spec §2a), so there is no rooted
        form to suggest — suggesting one would be a lie.

        ⚑ EVERY ROW IS ``synced``, AND THAT IS THE POINT. The rows read
        ``box.bindings.rw.home`` / ``workset.bindings.ro.d`` until R-9's first step,
        then ``agent.claude.bindings.{rw,ro}.*`` until its second. Each time, left in
        place, the test would still have PASSED — and would have been VACUOUS:
        ``_rooted_form_hint`` matches on ``BIND_KEY_RE``, which no longer matches a
        bindings key at ANY scope, so "no rooted hint" would have been proved by the
        key not being a category key rather than by the category not being ABSTRACT.
        ``synced`` is the surviving CONCRETE category and still matches, so the
        assertion still tests the rule it names. Do not reintroduce a bindings row."""
        v = _validate(key, "sub/dir", is_category=True)
        assert isinstance(v, Error)
        assert "rooted form" not in v.message
        assert "@meta." not in v.message

    @pytest.mark.parametrize(
        "value",
        ["/abs/dir", "~/tdir", "$XDG_DATA_HOME/x", "@workset.boxes/x"],
    )
    def test_self_resolving_sources_still_pass(self, value) -> None:
        v = _validate("box.bindings.rw.home", value, is_category=True)
        assert not isinstance(v, Error), v

    @pytest.mark.parametrize(
        "value", [r"\$NOTAVAR/x", r"\@nope/x", r"\@{nope}/x", r"\~foo/x"],
    )
    def test_escaped_token_still_relative(self, value) -> None:
        """An ESCAPED leading token is a literal character, not a token — so the
        value is still a BARE RELATIVE path and is refused.

        This is the same answer the retired assembly-time join gave (it tested the
        post-unescape string for a leading ``/``), reached earlier.
        """
        v = _validate("box.bindings.rw.home", value, is_category=True)
        assert isinstance(v, Error)
        assert "relative" in v.message

    def test_scalar_keys_are_untouched(self) -> None:
        """The refusal is CATEGORY-only: a scalar setting is not a path source."""
        v = _validate("model", "sonnet", is_category=False)
        assert not isinstance(v, Error), v
