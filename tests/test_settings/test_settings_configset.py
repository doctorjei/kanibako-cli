"""Unit tests for ``config set`` validation (block 5 + Q9).

Covers :mod:`kanibako.settings.settings_configset` —
:func:`validate_config_set` (Q9 FULL RESOLUTION + the E3 rule):

* malformed token syntax → Error (pure pre-check);
* the E3 probe returns a reason (edited value's upstream chain unresolvable) →
  Error naming the broken dep; ``None`` (resolves cleanly) → OK;
* a typed-scalar type mismatch → Error;
* a well-formed edit that resolves cleanly → OK, NO warning (B4).

The E3 ``resolves`` probe is exercised here with a REAL lenient-``expand``-backed
stub (apply the candidate at the key into a known keyspace, lenient-expand, read
the edited key's defect) — so the validate tests test the wiring AND real
resolution; the full multi-scope seam build is tested in test_config_interface.

⚑⚑ THE WRITE-BACK HALF OF THIS FILE IS GONE (QA′, 2026-08-08, on Jei's word).
``repoint_host_src`` and ``validate_config_set``'s ``is_category`` arm were deleted
from the source, so every test whose SUBJECT was one of them went with it. Two
graveyard blocks below name what died and what a rebuild would owe — read them
before concluding that a rule is silently untested.

⚑ WHY THE FIXTURES STILL SPELL ``box.bindings.rw.<name>`` AND FRIENDS. The
validator is key-agnostic apart from one lookup: it consults ``KEY_TYPES`` for the
typed-scalar coercion and otherwise uses the key as a MESSAGE LABEL only. None of
the bind-shaped spellings below is in ``KEY_TYPES`` (verified), so they exercise
exactly the same code path a scalar key does, and they were left as-authored rather
than re-keyed inside a deletion pass. ⚑⚑ **They are NOT settable keys** — ``config
set``/``reset`` refuse all six bind-shaped categories BY NAME at every scope
(DS-BL1 = (a), R-9), and no category key reaches this validator in product at all
(``config_interface._probes_at_set_time`` gates the one live call site). Do not
read any key in this file as evidence that a bind is settable from the CLI.
"""

from __future__ import annotations

import pytest

from kanibako.settings.settings_configset import OK, Error, validate_config_set
from kanibako.settings.settings_expand import expand
from kanibako.settings.settings_resolve import ResolveCtx
from kanibako.settings.settings_store import _MISSING, KeyStore

# --------------------------------------------------------------------------- #
# Test stubs for the injected callback                                        #
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


def _validate(key: str, value: str, *, resolves=_resolves):
    return validate_config_set(key, value, resolves=resolves)


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


def test_a_colon_in_a_value_is_ordinary_content() -> None:
    """⚑ THE REPLACEMENT FOR THE DELETED ``:`` REFUSAL, POSED THE OTHER WAY ROUND.

    The forbidden ``src:dest`` notation was a CATEGORY rule about the bind SHAPE (a
    structured pair spelled as a joined string) and went with the ``is_category``
    arm in QA′. What must hold on the surviving path is the OPPOSITE claim, and it
    is the one with real users: a colon is ordinary content. Pinned rather than left
    implicit because the refusal was ungated once before, and it then refused every
    ``https://`` value the moment a scalar caller existed.
    """
    v = _validate("model", "https://api.example.com:8443/v1")
    assert v is OK


# --------------------------------------------------------------------------- #
# Verdict — dangling @-ref / unknown $VAR → Error                             #
# --------------------------------------------------------------------------- #


def test_dangling_ref_is_hard_error() -> None:
    v = _validate("box.bindings.rw.home", "@nope.not.a.key")
    assert isinstance(v, Error)
    assert "@nope.not.a.key" in v.message


def test_existing_whole_value_ref_repoint_is_ok_no_warn() -> None:
    # B4: repointing a value to a whole-value @-ref to an EXISTING key is OK, and
    # carries NO @-ref-repoint warning.
    v = _validate("box.bindings.rw.home", "@workset.boxes")
    assert v is OK


def test_embedded_ref_to_existing_key_is_ok() -> None:
    v = _validate("box.bindings.rw.home", "@workset.boxes/sub/dir")
    assert v is OK


def test_embedded_dangling_ref_is_hard_error() -> None:
    v = _validate("box.bindings.rw.home", "@workset.boxes/@bad.ref/x")
    assert isinstance(v, Error)
    assert "@bad.ref" in v.message


def test_unknown_var_is_hard_error() -> None:
    v = _validate("box.bindings.rw.home", "$NOPE_VAR/x")
    assert isinstance(v, Error)
    assert "$NOPE_VAR" in v.message


def test_known_var_is_ok() -> None:
    v = _validate("box.bindings.rw.home", "$XDG_DATA_HOME/kanibako")
    assert v is OK


def test_braced_known_var_is_ok() -> None:
    v = _validate("box.bindings.rw.home", "${XDG_DATA_HOME}/x")
    assert v is OK


def test_braced_unknown_var_is_hard_error() -> None:
    v = _validate("box.bindings.rw.home", "${NOPE}/x")
    assert isinstance(v, Error)
    assert "$NOPE" in v.message


# --------------------------------------------------------------------------- #
# Verdict — malformed token syntax → Error                                    #
# --------------------------------------------------------------------------- #


def test_unterminated_braced_var_is_hard_error() -> None:
    v = _validate("box.bindings.rw.home", "${XDG_DATA_HOME/x")
    assert isinstance(v, Error)
    assert "malformed" in v.message.lower()


def test_bare_dollar_then_nonname_is_hard_error() -> None:
    # ``$/`` — a ``$`` not followed by a valid variable name is malformed (matches
    # expand_expr, which raises on the same shape).
    v = _validate("box.bindings.rw.home", "$/notavar")
    assert isinstance(v, Error)


def test_escaped_dollar_is_literal_not_a_var() -> None:
    # ``\$`` is an escaped literal ``$`` — NOT a variable token, so no unknown-var
    # error (matches expand_expr's escape rule).
    #
    # ⚑ THE ABSOLUTE ANCHOR IS VESTIGIAL NOW, AND IS KEPT ONLY BECAUSE CHANGING A
    # FIXTURE PROVES NOTHING. It was added because the bare-relative CATEGORY
    # refusal would have failed ``\$NOTAVAR/x`` for that OTHER reason, leaving this
    # green for the wrong cause. QA′ deleted that refusal, so the leading ``/`` no
    # longer separates anything — the escape rule is what is under test either way.
    v = _validate("box.bindings.rw.home", r"/abs/\$NOTAVAR/x")
    assert v is OK


def test_escaped_at_is_literal_not_a_ref() -> None:
    v = _validate("box.bindings.rw.home", r"/abs/\@nope/x")
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
        "box.bindings.ro.helper_log", "@workset.boxes/@{box.meta.name}.jsonl",
    )
    assert v is OK


def test_dangling_braced_ref_is_hard_error_like_a_bare_one() -> None:
    # PHASE R: a dangling BRACED ref is judged exactly as a dangling bare one.
    braced = _validate("box.bindings.rw.home", "@{nope.missing}/x")
    bare = _validate("box.bindings.rw.home", "@nope.missing/x")
    assert isinstance(braced, Error) and isinstance(bare, Error)
    assert "nope.missing" in braced.message


def test_malformed_braced_ref_is_hard_error() -> None:
    for value in ("@{a b}/x", "@{unclosed/x", "@{}/x"):
        v = _validate("box.bindings.rw.home", value)
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
    v = _validate("box.bindings.rw.home", r"/abs/\@{nope}/x")
    assert v is OK


# --------------------------------------------------------------------------- #
# ⚑⚑ THE RAW CATEGORY WRITE-BACK LIVED HERE AND IS GONE (QA′, 2026-08-08).
#
# ``repoint_host_src`` (S24) had had NO CALLER since DS-BL1 = (a) retired the CLI
# category write route, and Jei ruled its deletion.  21 test functions / 24
# parametrized cases went with it (counted, not estimated): the
# host_src swap keeping box_dest/options, the RAW-form preservation (``@``-refs /
# ``$XDG`` / ``~`` stored verbatim, never expanded), the F10 cascade fallback
# (``test_repoint_cascade_fallback_*``, ``test_repoint_missing_key_raises`` — spec
# §2a's "the key MUST ALREADY EXIST in the cascade"), the non-category-value /
# non-mapping-intermediate refusals, the list-not-a-colon-string storage form, and
# the validate-then-write round trip.
#
# ⚑ TWO OF THOSE WERE RULINGS RATHER THAN MECHANICS, SO THEY ARE RECORDED HERE — A
# REBUILD OWES THEM BOTH RATHER THAN REDISCOVERING THEM:
#
# 1. **R-8's THREE-ELEMENT refusal** (``TestStaleBindShapeRefused``: 4 cases plus a
#    4-way parametrize over caches/seeded/common/synced).  It raised
#    ``ConfigSetError`` when a dest-keyed category held a ``[host_src, box_dest,
#    options]`` triple — the retired name-keyed shape — instead of silently
#    rewriting element 1 as a ``box_dest``.  Each case asserted ``"STALE 3-element"
#    in str(exc.value)`` plus the KEY and the ARM by name, and that the file was
#    left untouched.
# 2. **The DS-BL8/8a pin of the DECLINED option B**
#    (``test_two_element_bindings_value_is_still_accepted``), whose assertion was
#    ``…["box"]["bindings"]["rw"]["home"] == ["/new", "~/"]`` — i.e. that a
#    2-element value under a bindings arm is CARRIED THROUGH, not refused.  A stored
#    ``[src, box_dest]`` and a live ``[src, options]`` are indistinguishable at two
#    elements; the heuristic refusal was offered as option B and Jei DECLINED it in
#    favour of option A (docs only, 2026-08-06e).  ⚑⚑ That test existed PRECISELY to
#    go red if anyone built the declined thing.  Its subject is gone, so the guard is
#    gone with it — **the 2-element accept is still the ruling, and a rebuilt
#    category write route owes it a new pin.**
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# E3 rule (Q9) — allow iff the edited value resolves cleanly post-edit          #
# --------------------------------------------------------------------------- #
# The E3 matrix (brief a–g). The ``resolves`` stub applies the edited value into a
# keyspace WITH a pre-seeded defect where the case needs one, lenient-expands, and
# the verdict turns ONLY on the edited key's own resolution.
#
# ⚑ CASE (h) — "a not-yet-existent literal host path WARNS" — IS GONE.  It was the
# SOLE producer of the ``Warn`` verdict and lived in the ``is_category`` arm, so
# QA′ deleted the arm, the ``host_exists`` callback and the ``Warn`` class together
# (a union member no code path can produce is a shape a consumer branches on for
# nothing).  There is no warn severity on this path any more; restoring one means
# restoring a producer in the same change.


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

    v = _validate("box.bindings.rw.home", "@workset.boxes/ok", resolves=probe)
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

    v = _validate("box.bindings.rw.home", "/clean/literal", resolves=probe)
    assert v is OK


def test_e3_c_edit_repoints_away_from_broken_dep_allows() -> None:
    # (c) The OLD value referenced a broken dep; the edit re-points to a good one.
    v = _validate("box.bindings.rw.home", "@workset.boxes/x")
    assert v is OK


def test_e3_d_edit_fixes_broken_key_itself_allows() -> None:
    # (d) Editing the broken key to a good value resolves it (and dependents
    # recover). Here we edit the key directly to a clean literal.
    def probe(key, value):
        return _resolves_with({}, key=key, value=value)

    v = _validate("workset.vault_rw", "/now/good", resolves=probe)
    assert v is OK


def test_e3_e_edited_value_upstream_chain_broken_blocks() -> None:
    # (e) The edited value's UPSTREAM chain stays unresolvable → BLOCK, name the dep.
    v = _validate("box.bindings.rw.home", "@gone.upstream/x")
    assert isinstance(v, Error)
    assert "gone.upstream" in v.message


def test_e3_e_embedded_dangling_in_edited_value_blocks() -> None:
    # (e′ — director ruling) an EMBEDDED dangling @-ref in the EDITED value's chain
    # is a defect that BLOCKS at set-time (NOT strict-expand's silent "").
    v = _validate("box.bindings.rw.home", "@workset.boxes/@bad.embedded/x")
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

    v = _validate("box.bindings.rw.home", "/clean", resolves=probe)
    assert v is OK


def test_e3_f_edit_introduces_cycle_blocks() -> None:
    # (f) The edit makes the edited value's chain a CYCLE → BLOCK.
    def probe(key, value):
        # editing `a` to @b, with b=@a already present → a -> b -> a cycle.
        return _resolves_with({"b": "@a"}, key=key, value=value)

    v = _validate("a", "@b", resolves=probe)
    assert isinstance(v, Error)
    assert "cyclic" in v.message.lower()


def test_e3_g_preexisting_cycle_elsewhere_allows() -> None:
    # (g) A cycle on an UNRELATED branch does not block the edit.
    def probe(key, value):
        return _resolves_with(
            {"loop_a": "@loop_b", "loop_b": "@loop_a"}, key=key, value=value
        )

    v = _validate("box.bindings.rw.home", "@workset.boxes/x", resolves=probe)
    assert v is OK


# --------------------------------------------------------------------------- #
# ⚑⚑ THE BARE-RELATIVE CATEGORY SOURCE REFUSAL LIVED HERE AND IS GONE
# (QA′, 2026-08-08).
#
# ``TestRelativeCategorySourceRefused`` (T9 / P3 / spec §2a) drove
# ``validate_config_set``'s ``is_category`` arm: a bare-relative ``host_src`` such
# as ``plugins`` was REFUSED at set time, because it resolves PERFECTLY — to the
# relative string ``plugins``, which the mount spec then interprets against whatever
# the launching process's CWD happens to be.  Nothing downstream could catch it.
# 7 test functions / 24 parametrized cases went (counted, not estimated): the
# refusal itself over five spellings, ``_rooted_form_hint``'s PER SCOPE rooted cure
# (``@config.data`` / ``@meta.agent.<a>.path`` / ``@meta.workset.path`` /
# ``@meta.box.path``), the undiscriminated-``agent.<category>`` hintlessness, the
# concrete-vs-abstract discriminator, and the escaped-token-is-still-relative rows.
#
# ⚑ A NINTH AND TENTH CASUALTY SIT OUTSIDE BOTH BLOCKS, so they are named here:
# ``test_colon_notation_is_hard_error`` / ``test_escaped_colon_is_allowed`` (the
# ``:`` src:dest refusal and its escape hatch) and the six ``host_exists``/``Warn``
# rows.  8 functions in total; the colon claim is re-posed positively as
# ``test_a_colon_in_a_value_is_ordinary_content`` above.
#
# ⚑ THIS IS THE ONE DELETION THAT REMOVES A RULE RATHER THAN AN ORPHANED MECHANISM,
# SO SAY IT PLAINLY: the rule was ALREADY unreachable.  DS-BL1 = (a) made every
# bind-shaped category YAML-only, so no CLI door reaches this validator with a
# category key — ``test_system_cmd_config.py`` records the same thing at the point
# where the end-to-end twin of these tests was deleted in that pass.  What is lost is
# a check on a route that no longer exists.  ⚑⚑ AND NOTE WHAT WAS NEVER COVERED: a
# category source authored directly in YAML has never been checked by this validator,
# before or after.  Closing that gap needs a DECLARATION-time check, not this one
# restored.
# --------------------------------------------------------------------------- #
