"""Unit tests for block 3 — the eager build-time EXPANSION (settings_expand).

Covers the brief §5 checklist for the pure ``expand(snapshot, ctx) -> KeyStore``:

* transitive multi-hop chain collapses to a terminal regardless of key ORDER
  (fixpoint / topological);
* WHOLE-VALUE ``@``-ref inherits the referent's 3-state the FULL chain length —
  absent → the key is DROPPED; present-None → ``None``;
* EMBEDDED token → substitution (absent/None → empty), never deletes the key;
* CYCLE (whole-value AND embedded) → hard ``SettingsError`` with the chain;
  KEPT DISTINCT from a legitimately absent/None referent (NOT an error);
* ``host_src`` ``$XDG``/``~`` expand host-side; ``box_dest`` ``$XDG``/``~`` left
  RAW (deferred — asserted PRESENT) while a ``box_dest`` ``@``-ref IS expanded;
* escapes preserved; purity — input snapshot unmutated, idempotent on a terminal.

Authority: design §6h / §6a / §6b / §3; spec §0 / §1 / §2c. The pass keys nothing
by ``box_dest`` (reconcile is §6g) and never merges (block 2b).
"""

from __future__ import annotations

import copy

import pytest

from kanibako.settings.kb_store import Bind, BindEntry
from kanibako.settings.kb_store import __MISSING__
from kanibako.settings.keystore import KeyStore
from kanibako.settings.settings_expand import _is_whole_value_ref, expand
from kanibako.settings.settings_launch import (
    resolve_box_dest,
    snapshot_category_entries,
)
from kanibako.settings.settings_resolve import (
    GUEST_HOME,
    ResolveCtx,
    SettingsError,
    expand_expr,
)

HOST_HOME = "/home/u"


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #


def _ctx(
    *,
    agent_name: str | None = "claude",
    workset_name: str | None = "ws",
    host_home: str = HOST_HOME,
    xdg: dict[str, str] | None = None,
) -> ResolveCtx:
    return ResolveCtx(
        agent_name=agent_name,
        workset_name=workset_name,
        host_home=host_home,
        xdg=xdg if xdg is not None else {
            "XDG_DATA_HOME": "/home/u/.local/share",
            "XDG_STATE_HOME": "/home/u/.local/state",
        },
    )


def _probe(store: KeyStore, *segments: str) -> object:
    """Walk *segments* with the unbound-``dict.get`` probe (S3); ``__MISSING__`` if
    any segment is absent (so a present-None leaf differs from an absent one)."""
    node: object = store
    for seg in segments:
        if not isinstance(node, KeyStore):
            return __MISSING__
        node = dict.get(node, seg, __MISSING__)
    return node


# --------------------------------------------------------------------------- #
# Trivial / purity                                                            #
# --------------------------------------------------------------------------- #


def test_empty_snapshot() -> None:
    assert expand(KeyStore(), _ctx()) == KeyStore()


def test_terminal_scalars_unchanged_and_typed() -> None:
    snap = KeyStore({"a": "plain", "n": 7, "f": 1.5, "b": True, "z": None,
                     "lst": ["x", "y"]})
    out = expand(snap, _ctx())
    assert out["a"] == "plain"
    assert out["n"] == 7 and out["f"] == 1.5 and out["b"] is True
    assert out["z"] is None  # present-None scalar terminal is KEPT (§3).
    assert out["lst"] == ["x", "y"]


def test_idempotent_on_already_terminal() -> None:
    snap = KeyStore({"sys": {"data": "/home/u/.local/share/kanibako"}})
    out1 = expand(snap, _ctx())
    out2 = expand(out1, _ctx())
    assert out1 == out2


def test_input_snapshot_not_mutated() -> None:
    snap = KeyStore({
        "meta": {"workset": {"path": "$XDG_DATA_HOME/kanibako/ws"}},
        "workset": {"logs": "@meta.workset.path/logs"},
    })
    before = copy.deepcopy(snap)
    out = expand(snap, _ctx())
    assert snap == before  # S19 — input untouched.
    assert out is not snap
    assert out["workset"]["logs"] == "/home/u/.local/share/kanibako/ws/logs"


def test_fresh_tree_no_aliasing() -> None:
    snap = KeyStore({"sys": {"x": "term"}})
    out = expand(snap, _ctx())
    assert out["sys"] is not snap["sys"]


# --------------------------------------------------------------------------- #
# Host-side $XDG / ~ expansion                                                #
# --------------------------------------------------------------------------- #


def test_host_xdg_and_tilde_expand() -> None:
    snap = KeyStore({
        "system": {"backup": "$XDG_DATA_HOME/kanibako/backup"},
        "home_thing": "~/sub",
    })
    out = expand(snap, _ctx())
    assert out["system"]["backup"] == "/home/u/.local/share/kanibako/backup"
    assert out["home_thing"] == "/home/u/sub"


def test_braced_xdg_var() -> None:
    snap = KeyStore({"x": "${XDG_STATE_HOME}/k.sock"})
    out = expand(snap, _ctx())
    assert out["x"] == "/home/u/.local/state/k.sock"


# --------------------------------------------------------------------------- #
# Transitive @-ref chains (fixpoint / order-independent)                      #
# --------------------------------------------------------------------------- #


def test_transitive_chain_collapses() -> None:
    # canon -> @workset.template -> @meta.workset.path -> $XDG_DATA_HOME terminal
    # (spec §1 chain shape: a workset's children derive from its path).
    # ⚑ The chain stays INSIDE the snapshot on purpose — ``@config.*`` would not
    # test the fixpoint at all, since the resolver split (spec §1A / JC-2) routes
    # a config ref to the Layer-1 foundation, never to the merged snapshot.
    snap = KeyStore({
        "meta": {"workset": {"path": "$XDG_DATA_HOME/kanibako/ws"}},
        "workset": {
            "template": "@meta.workset.path/template",
            "canon": "@workset.template/canon",
        },
    })
    out = expand(snap, _ctx())
    assert out["workset"]["canon"] == (
        "/home/u/.local/share/kanibako/ws/template/canon"
    )


def test_forward_ref_works_regardless_of_order() -> None:
    # A points at B which is declared AFTER A in dict order — must still resolve.
    snap = KeyStore({"a": "@b/x", "b": "@c", "c": "/root"})
    out = expand(snap, _ctx())
    assert out["a"] == "/root/x"
    assert out["b"] == "/root"
    assert out["c"] == "/root"


def test_multi_hop_whole_value_chain() -> None:
    snap = KeyStore({"a": "@b", "b": "@c", "c": "/leaf"})
    out = expand(snap, _ctx())
    assert out["a"] == "/leaf" and out["b"] == "/leaf"


# --------------------------------------------------------------------------- #
# Whole-value 3-state propagation (§6b/§6h)                                   #
# --------------------------------------------------------------------------- #


def test_whole_value_absent_drops_key_full_chain() -> None:
    # a = @b, b = @c, c ABSENT → a and b are both DROPPED (absence full chain).
    snap = KeyStore({"a": "@b", "b": "@c"})
    out = expand(snap, _ctx())
    assert _probe(out, "a") is __MISSING__
    assert _probe(out, "b") is __MISSING__


def test_whole_value_present_none_propagates_none() -> None:
    # a = @b, b = @c, c present-None → a, b carry None (the §3 terminal).
    snap = KeyStore({"a": "@b", "b": "@c", "c": None})
    out = expand(snap, _ctx())
    assert _probe(out, "a") is None
    assert _probe(out, "b") is None
    assert _probe(out, "c") is None


# --------------------------------------------------------------------------- #
# BRACED @{name} whole-value refs (PHASE R) — the 3-state MUST be inherited    #
# --------------------------------------------------------------------------- #
#
# ⚑ WHY THIS BLOCK EXISTS. ``@{a.b}`` with nothing following is a WHOLE-VALUE ref
# and must inherit the referent's 3-state VERBATIM, exactly as ``@a.b`` does. If
# ``_is_whole_value_ref`` failed to recognise the braced form, the value would
# fall through to the EMBEDDED path, where ``_lookup_str`` coerces absent/None to
# "" — silently turning the §3 "omit this bind" None terminal into a real
# empty-string value. That substitution is invisible at every other layer, so it
# is pinned here. Each test below mirrors its bare-form sibling above one-for-one.


def test_braced_whole_value_absent_drops_key_full_chain() -> None:
    # Mirror of test_whole_value_absent_drops_key_full_chain with braces.
    snap = KeyStore({"a": "@{b}", "b": "@{c}"})
    out = expand(snap, _ctx())
    assert _probe(out, "a") is __MISSING__
    assert _probe(out, "b") is __MISSING__


def test_braced_whole_value_present_none_propagates_none() -> None:
    # ⚑ THE silent-behaviour-change guard. c is present-None; a and b must carry
    # None — NOT "" (the embedded coercion) and NOT "None" (a stringification).
    snap = KeyStore({"a": "@{b}", "b": "@{c}", "c": None})
    out = expand(snap, _ctx())
    for key in ("a", "b", "c"):
        got = _probe(out, key)
        assert got is None, f"{key}: expected the None terminal, got {got!r}"
        # Asserted both ways: a coerced "" and a stringified "None" are the two
        # ways this can silently pass while meaning something else entirely.
        assert got != ""
        assert got != "None"


def test_braced_whole_value_multi_hop_chain() -> None:
    # Mixed spellings across hops collapse to the same terminal.
    snap = KeyStore({"a": "@{b}", "b": "@c", "c": "/leaf"})
    out = expand(snap, _ctx())
    assert out["a"] == "/leaf" and out["b"] == "/leaf"


def test_braced_whole_value_resolves_non_str_terminal_verbatim() -> None:
    # VERBATIM means type-preserving, not just None-preserving: a bool referent
    # comes back a bool, never the string "True".
    snap = KeyStore({
        "avail": "@{workset.skip_kuid_check}",
        "workset": {"skip_kuid_check": True},
    })
    out = expand(snap, _ctx())
    assert out["avail"] is True


def test_braced_embedded_absent_coerces_empty_and_keeps_key() -> None:
    # THE CONTRAST that proves the two shapes stay distinguished: the SAME ref,
    # with a suffix, is EMBEDDED — absent coerces to "" and the key survives.
    snap = KeyStore({"a": "@{x.y}.jsonl", "c": "/ok"})
    out = expand(snap, _ctx())
    assert out["a"] == ".jsonl"
    assert out["c"] == "/ok"


def test_braced_bind_whole_value_host_absent_drops_bind() -> None:
    snap = KeyStore({"box": {"bindings": {"rw": {"x": Bind("@{missing}", "~/x")}}}})
    out = expand(snap, _ctx())
    assert _probe(out, "box", "bindings", "rw", "x") is __MISSING__


def test_braced_bind_whole_value_host_present_none_carries_none() -> None:
    snap = KeyStore({
        "src": None,
        "box": {"bindings": {"rw": {"x": Bind("@{src}", "~/x")}}},
    })
    out = expand(snap, _ctx())
    assert _probe(out, "box", "bindings", "rw", "x") is None


def test_braced_bind_host_suffix_resolves_like_the_spec_row() -> None:
    # The shape the spec's helper_log row uses, end to end through ``expand``.
    snap = KeyStore({
        "workset": {"logs": "/ws/logs"},
        "meta": {"box": {"name": "mybox"}},
        "box": {"bindings": {"ro": {"helper_log": Bind(
            "@workset.logs/@{meta.box.name}.jsonl",
            "$XDG_STATE_HOME/kanibako/helpers.jsonl",
            "ro",
        )}}},
    })
    out = expand(snap, _ctx())
    b = out["box"]["bindings"]["ro"]["helper_log"]
    assert b.host == "/ws/logs/mybox.jsonl"
    # box_dest keeps its ENVIRONMENT token RAW (deferred box-side, S17).
    assert b.box == "$XDG_STATE_HOME/kanibako/helpers.jsonl"


@pytest.mark.parametrize(
    "value,expected",
    [
        ("@a.b", "a.b"),  # bare — unchanged.
        ("@{a.b}", "a.b"),  # braced, nothing following → WHOLE-VALUE.
        ("@{a.b}x", None),  # a suffix makes it embedded.
        ("@{a.b}/c", None),
        ("x@{a.b}", None),
        ("@{a.b", None),  # malformed → None, and must NOT raise (see below).
        ("@{", None),
        ("@{}", None),
        ("@{a@{b}}", None),
        ("", None),
        ("~/x", None),
    ],
)
def test_is_whole_value_ref_braced_shapes(value: str, expected: str | None) -> None:
    # The predicate is TOTAL: malformed input answers None rather than raising, so
    # it falls through to the embedded path and ``expand_expr`` raises there —
    # keeping the error message and its raising site identical to before PHASE R.
    assert _is_whole_value_ref(value) == expected


def test_whole_value_resolves_terminal() -> None:
    snap = KeyStore({
        "avail": "@workset.skip_kuid_check",
        "workset": {"skip_kuid_check": True},
    })
    out = expand(snap, _ctx())
    assert out["avail"] is True


def test_whole_value_ref_to_subtree_fresh_and_expanded() -> None:
    # Degenerate (no spec form refs a whole subtree) but must be TOTAL: the ref'd
    # subtree comes back FRESH (not aliased — S19) AND fully expanded.
    snap = KeyStore({"ref": "@sub", "sub": {"k": "@leaf"}, "leaf": "/v"})
    out = expand(snap, _ctx())
    assert out["ref"] == KeyStore({"k": "/v"})  # inner @leaf expanded.
    assert out["ref"] is not snap["sub"]  # S19 — no aliasing of the input.
    assert out["ref"]["k"] == "/v" and snap["sub"]["k"] == "@leaf"  # input intact.


# --------------------------------------------------------------------------- #
# Embedded token substitution (§6b) — never deletes the key                   #
# --------------------------------------------------------------------------- #


def test_embedded_absent_token_to_empty_keeps_key() -> None:
    # container_name-style: an embedded ref to an absent key → "" (key SURVIVES).
    snap = KeyStore({"name": "kanibako-@box.missing"})
    out = expand(snap, _ctx())
    assert _probe(out, "name") == "kanibako-"  # key present, token empty.


def test_embedded_present_none_token_to_empty() -> None:
    # ⚑ The ref is BRACED because ``-`` is a ref-name character (a persona
    # node-name may contain one — ``settings_resolve._REF_SEG``); bare ``@y-z``
    # would be the single name ``y-z``.  The braced form is the sanctioned
    # spelling for a LITERAL suffix after a ref, and is what is under test here:
    # a present-but-None key substitutes to "" without deleting the key.
    snap = KeyStore({"name": "x-@{y}-z", "y": None})
    out = expand(snap, _ctx())
    assert out["name"] == "x--z"


def test_embedded_ref_substitutes_value() -> None:
    snap = KeyStore({"name": "kanibako-@box.image", "box": {"image": "abc"}})
    out = expand(snap, _ctx())
    assert out["name"] == "kanibako-abc"


def test_if_block_literal_carried_for_downstream_renderer() -> None:
    # %if% is a downstream RENDER concern, NOT this pass: the embedded @-ref
    # substitutes; the %...% literal text is carried verbatim (no key deletion).
    snap = KeyStore({
        "cn": "kanibako-@meta.box.name"
              "%if @meta.box.helper_num: -h-@meta.box.helper_num%",
        "meta": {"box": {"name": "droste"}},
    })
    out = expand(snap, _ctx())
    # name substitutes; absent helper_num → ""; %if% literal text preserved.
    assert out["cn"] == "kanibako-droste%if : -h-%"


# --------------------------------------------------------------------------- #
# CONFIG-vs-ENV split on a Bind (S17)                                         #
# --------------------------------------------------------------------------- #


def test_bind_host_expands_box_xdg_deferred() -> None:
    # host_src: @-ref + ~ expand host-side; box_dest $XDG left RAW (deferred).
    snap = KeyStore({
        "workset": {"logs": "/ws/logs"},
        "box": {"bindings": {"ro": {"helper_log": Bind(
            "@workset.logs/h.jsonl", "$XDG_STATE_HOME/kanibako/helpers.jsonl",
        )}}},
    })
    out = expand(snap, _ctx())
    b = out["box"]["bindings"]["ro"]["helper_log"]
    assert isinstance(b, Bind)
    assert b.host == "/ws/logs/h.jsonl"
    # DEFERRED — the box-side $XDG token is PRESENT (raw), not host-expanded.
    assert b.box == "$XDG_STATE_HOME/kanibako/helpers.jsonl"


def test_bind_box_tilde_deferred() -> None:
    snap = KeyStore({
        "workset": {"boxes": "/ws/boxes"},
        "box": {"bindings": {"rw": {"home": Bind("@workset.boxes/home", "~/")}}},
    })
    out = expand(snap, _ctx())
    b = out["box"]["bindings"]["rw"]["home"]
    assert b.host == "/ws/boxes/home"
    assert b.box == "~/"  # ~ NOT expanded to GUEST_HOME (deferred box-side).
    assert b.box != GUEST_HOME + "/"


def test_bind_box_at_ref_IS_expanded() -> None:
    # box_dest @-ref (CONFIG) IS expanded even though $XDG/~ are not.
    snap = KeyStore({
        "dest_root": "/box/root",
        "box": {"bindings": {"rw": {"x": Bind("/h/src", "@dest_root/sub")}}},
    })
    out = expand(snap, _ctx())
    b = out["box"]["bindings"]["rw"]["x"]
    assert b.host == "/h/src"
    assert b.box == "/box/root/sub"


def test_bind_box_mixed_at_and_xdg() -> None:
    # box_dest with BOTH an @-ref and a deferred $XDG: @ expands, $XDG stays raw.
    snap = KeyStore({
        "seg": "mid",
        "box": {"bindings": {"rw": {"x": Bind("/h", "$XDG_STATE_HOME/@seg/end")}}},
    })
    out = expand(snap, _ctx())
    b = out["box"]["bindings"]["rw"]["x"]
    assert b.box == "$XDG_STATE_HOME/mid/end"


def test_bind_opts_carried() -> None:
    snap = KeyStore({"box": {"caches": {"c": Bind("/h", "~/c", "z")}}})
    out = expand(snap, _ctx())
    b = out["box"]["caches"]["c"]
    assert b.opts == "z" and b.box == "~/c"


def test_bind_whole_value_host_absent_drops_bind() -> None:
    # host_src is a whole-value @-ref to an absent key → the bind is DROPPED.
    snap = KeyStore({"box": {"bindings": {"rw": {"x": Bind("@missing", "~/x")}}}})
    out = expand(snap, _ctx())
    assert _probe(out, "box", "bindings", "rw", "x") is __MISSING__


def test_bind_whole_value_host_present_none_carries_none() -> None:
    snap = KeyStore({
        "src": None,
        "box": {"bindings": {"rw": {"x": Bind("@src", "~/x")}}},
    })
    out = expand(snap, _ctx())
    assert _probe(out, "box", "bindings", "rw", "x") is None


def test_bind_host_xdg_and_tilde_expand_host_side() -> None:
    # The host half of the asymmetry: host_src $XDG / ~ DO expand host-side.
    snap = KeyStore({
        "box": {"bindings": {"rw": {
            "h": Bind("$XDG_DATA_HOME/kanibako/x", "/box/x"),
            "t": Bind("~/src", "/box/y"),
        }}},
    })
    out = expand(snap, _ctx())
    assert out["box"]["bindings"]["rw"]["h"].host == (
        "/home/u/.local/share/kanibako/x"
    )
    assert out["box"]["bindings"]["rw"]["t"].host == "/home/u/src"


def test_bind_box_whole_value_absent_ref_raises() -> None:
    # A whole-value box_dest @-ref to an absent key is unreachable on spec forms
    # and a mount foot-gun if coerced to "" — so it raises (not silently empty).
    snap = KeyStore({"box": {"caches": {"c": Bind("/h", "@nope")}}})
    with pytest.raises(SettingsError) as ei:
        expand(snap, _ctx())
    assert "box_dest" in str(ei.value)


def test_bind_box_whole_value_present_none_ref_raises() -> None:
    snap = KeyStore({
        "d": None,
        "box": {"caches": {"c": Bind("/h", "@d")}},
    })
    with pytest.raises(SettingsError):
        expand(snap, _ctx())


# --------------------------------------------------------------------------- #
# Cycle detection (§6h / B7) — hard error, with the chain                     #
# --------------------------------------------------------------------------- #


def test_whole_value_cycle_raises_with_chain() -> None:
    snap = KeyStore({"a": "@b", "b": "@a"})
    with pytest.raises(SettingsError) as ei:
        expand(snap, _ctx())
    msg = str(ei.value)
    assert "ycl" in msg  # "Cyclic"
    assert "a" in msg and "b" in msg


def test_self_cycle_whole_value_raises() -> None:
    snap = KeyStore({"a": "@a"})
    with pytest.raises(SettingsError):
        expand(snap, _ctx())


def test_embedded_cycle_raises() -> None:
    # B7 — a cycle reached THROUGH an embedded token is also a hard error.
    # ⚑ BRACED: ``-`` is a ref-name character, so bare ``@b-y`` would name the
    # absent key ``b-y`` and there would be no cycle to detect.  The braces keep
    # the fixture's intent (an embedded ref with a literal ``-`` suffix).
    snap = KeyStore({"a": "x-@{b}-y", "b": "p-@{a}-q"})
    with pytest.raises(SettingsError) as ei:
        expand(snap, _ctx())
    assert "ycl" in str(ei.value).lower() or "cycl" in str(ei.value).lower()


def test_mixed_whole_then_embedded_cycle_raises() -> None:
    snap = KeyStore({"a": "@b", "b": "z-@a"})
    with pytest.raises(SettingsError):
        expand(snap, _ctx())


def test_absent_ref_is_NOT_a_cycle_error() -> None:
    # A legit-absent referent is §6b propagation, NOT a cycle — must not raise.
    snap = KeyStore({"a": "@nope"})
    out = expand(snap, _ctx())  # no exception.
    assert _probe(out, "a") is __MISSING__


# --------------------------------------------------------------------------- #
# Escapes preserved                                                           #
# --------------------------------------------------------------------------- #


def test_escaped_at_is_literal() -> None:
    snap = KeyStore({"x": r"lit-\@notaref"})
    out = expand(snap, _ctx())
    assert out["x"] == "lit-@notaref"


def test_escaped_dollar_is_literal() -> None:
    snap = KeyStore({"x": r"\$NOTVAR"})
    out = expand(snap, _ctx())
    assert out["x"] == "$NOTVAR"


def test_box_side_env_escape_carried_verbatim() -> None:
    # In the deferred (box) space, an escape of an ENV-significant char (\$ / \~ /
    # \\) is carried VERBATIM so the BOX resolver sees the same escape and does NOT
    # re-expand it (the user's "literal, NOT a var" intent must survive to the box).
    snap = KeyStore({"box": {"bindings": {"rw": {"x": Bind("/h", r"~/a\$b")}}}})
    out = expand(snap, _ctx())
    assert out["box"]["bindings"]["rw"]["x"].box == r"~/a\$b"


def test_box_side_escaped_at_unescaped() -> None:
    # \@ is still unescaped to @ in defer space: this pass OWNS @-refs on both
    # sides; the box never processes @, so a literal @ is the correct residue.
    snap = KeyStore({"box": {"caches": {"c": Bind("/h", r"~/x\@y")}}})
    out = expand(snap, _ctx())
    assert out["box"]["caches"]["c"].box == "~/x@y"


def test_box_side_braced_var_deferred_verbatim() -> None:
    # ${VAR} braces are part of the verbatim deferred span (Editor req 2).
    snap = KeyStore({"box": {"caches": {"c": Bind("/h", "${XDG_CACHE_HOME}/k")}}})
    out = expand(snap, _ctx(xdg={"XDG_CACHE_HOME": "/host/cache"}))
    assert out["box"]["caches"]["c"].box == "${XDG_CACHE_HOME}/k"


# --------------------------------------------------------------------------- #
# Whole-value parser (S18) unit                                               #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("value,expected", [
    ("@a.b.c", "a.b.c"),
    ("@x", "x"),
    ("@a-@b", None),     # two tokens — embedded.
    ("x@a", None),       # leading literal — embedded.
    ("@a/c", None),      # trailing literal — embedded.
    ("@a ", None),       # trailing space — embedded.
    ("~/x", None),       # environment token, not a ref.
    ("$VAR", None),
    ("", None),
    ("plain", None),
    # PERSONA node-separator ℘ (U+2118) is a valid ref-name char (Block E fix 1a):
    # a whole ref containing a ℘ node-name matches WHOLE, not truncated at ℘.
    ("@meta.agent.navigator℘claude.auth.share_support",
     "meta.agent.navigator℘claude.auth.share_support"),
    ("@agent.navigator℘claude.model", "agent.navigator℘claude.model"),
    # An embedded ℘-ref followed by a literal is still embedded (path ref).
    ("@config.data/navigator℘claude", None),
])
def test_is_whole_value_ref(value: str, expected: str | None) -> None:
    assert _is_whole_value_ref(value) == expected


def test_ref_regex_admits_persona_node_separator() -> None:
    # Block E fix 1a: _REF_NAME_RE must match a persona node-name (persona℘harness)
    # as ONE component. Mutation-check: revert the ℘ addition to the char classes
    # and this fails (the match truncates to "…navigator", end() != len).
    from kanibako.settings.settings_resolve import _REF_NAME_RE
    ref = "meta.agent.navigator℘claude.auth.share_support"
    m = _REF_NAME_RE.match(ref)
    assert m is not None
    assert m.group(0) == ref
    assert m.end() == len(ref)
    # NARROW: a slash still terminates the ref (embedded @config.data/... intact).
    mp = _REF_NAME_RE.match("config.data/foo")
    assert mp is not None and mp.group(0) == "config.data"


def test_whole_value_persona_ref_resolves_bool_not_valueerror() -> None:
    # The e2e-crashing case: an @-ref whose key contains a ℘ node-name resolves to
    # the value AT that key (a bool), not the garbage-truncated "…navigator"
    # sub-string that fed as_bool a bad literal and raised ValueError at launch.
    snap = KeyStore({
        "meta": {"agent": {"navigator℘claude": {"auth": {"share_support": True}}}},
        "x": "@meta.agent.navigator℘claude.auth.share_support",
    })
    out = expand(snap, _ctx())
    assert out["x"] is True


# --------------------------------------------------------------------------- #
# Memoization / diamond (fixpoint, no false cycle)                            #
# --------------------------------------------------------------------------- #


def test_diamond_no_false_cycle() -> None:
    # d and e both reference b; b is resolved once and reused (no false cycle).
    snap = KeyStore({"b": "/root", "d": "@b/x", "e": "@b/y"})
    out = expand(snap, _ctx())
    assert out["d"] == "/root/x" and out["e"] == "/root/y"


# --------------------------------------------------------------------------- #
# LENIENT / error-COLLECTING mode (Q9 — collect_errors=True)                  #
# --------------------------------------------------------------------------- #
#
# STRICT (default) is byte-identical (the suite above asserts it). LENIENT
# collects dangling @-refs / unknown $VAR / cycles into an error map keyed by the
# owning leaf's dotted path, resolves everything else, and TERMINATES on a cycle.


def test_lenient_returns_pair_and_empty_errors_on_clean() -> None:
    # A clean snapshot in lenient mode resolves identically AND reports no errors.
    snap = KeyStore({"a": "@b/x", "b": "@c", "c": "/root"})
    strict = expand(snap, _ctx())
    expanded, errors = expand(snap, _ctx(), collect_errors=True)
    assert errors == {}
    assert expanded == strict  # lenient result matches strict when there is no defect.


def test_lenient_off_returns_bare_keystore() -> None:
    # collect_errors=False (explicit) returns a bare KeyStore, not a pair.
    snap = KeyStore({"c": "/root"})
    out = expand(snap, _ctx(), collect_errors=False)
    assert isinstance(out, KeyStore)


def test_lenient_collects_dangling_whole_value_ref() -> None:
    snap = KeyStore({"a": "@nope.missing", "c": "/ok"})
    expanded, errors = expand(snap, _ctx(), collect_errors=True)
    assert "a" in errors and "nope.missing" in errors["a"]
    # The defective leaf is OMITTED; the clean leaf still resolves.
    assert _probe(expanded, "a") is __MISSING__
    assert expanded["c"] == "/ok"


def test_lenient_collects_dangling_embedded_ref() -> None:
    # ⚑ BRACED: ``-`` is a ref-name char, so bare ``@nope.x-post`` would name
    # ``nope.x-post``.  That still dangles, and the assertion below is a SUBSTRING
    # check, so the test would pass either way — it would just no longer test the
    # ref it names.  The braces keep the fixture honest.
    snap = KeyStore({"a": "pre-@{nope.x}-post", "c": "/ok"})
    expanded, errors = expand(snap, _ctx(), collect_errors=True)
    assert "a" in errors and "nope.x" in errors["a"]
    assert _probe(expanded, "a") is __MISSING__
    assert expanded["c"] == "/ok"


def test_lenient_collects_dangling_braced_ref_like_a_bare_one() -> None:
    # PHASE R: "set-time validation treats a dangling BRACED ref exactly as it
    # treats a dangling bare one" — same error map, same key, same named ref, in
    # BOTH the whole-value and the embedded (suffixed) position.
    snap = KeyStore({"a": "@{nope.missing}", "b": "@{nope.missing}.jsonl", "c": "/ok"})
    expanded, errors = expand(snap, _ctx(), collect_errors=True)
    assert "a" in errors and "nope.missing" in errors["a"]
    assert "b" in errors and "nope.missing" in errors["b"]
    assert _probe(expanded, "a") is __MISSING__
    assert _probe(expanded, "b") is __MISSING__
    assert expanded["c"] == "/ok"


def test_lenient_collects_malformed_braced_ref() -> None:
    # A malformed brace is a defect RECORDED against its leaf, not a raise that
    # takes the whole lenient pass down (the ``config set`` path depends on this).
    snap = KeyStore({"a": "@{nope", "c": "/ok"})
    expanded, errors = expand(snap, _ctx(), collect_errors=True)
    assert "a" in errors and "Unterminated" in errors["a"]
    assert _probe(expanded, "a") is __MISSING__
    assert expanded["c"] == "/ok"


def test_lenient_collects_dangling_through_transitive_chain() -> None:
    # The defect is UPSTREAM (a -> b -> dangling); it is attributed to the OWNING
    # leaf `a`, and the intermediate `b` (which is itself dangling-rooted) too.
    snap = KeyStore({"a": "@b", "b": "@gone.x", "c": "/ok"})
    expanded, errors = expand(snap, _ctx(), collect_errors=True)
    assert "a" in errors and "gone.x" in errors["a"]
    assert "b" in errors
    assert expanded["c"] == "/ok"


def test_lenient_collects_unknown_var() -> None:
    snap = KeyStore({"a": "$NOPE_VAR/x", "c": "/ok"})
    expanded, errors = expand(snap, _ctx(), collect_errors=True)
    assert "a" in errors and "NOPE_VAR" in errors["a"]
    assert _probe(expanded, "a") is __MISSING__
    assert expanded["c"] == "/ok"


def test_lenient_collects_unset_xdg_var() -> None:
    # A known XDG name absent from ctx.xdg is unset → collected (not raised).
    snap = KeyStore({"a": "$XDG_CACHE_HOME/x"})
    expanded, errors = expand(snap, _ctx(), collect_errors=True)
    assert "a" in errors and "XDG_CACHE_HOME" in errors["a"]


def test_lenient_collects_cycle_and_terminates() -> None:
    # A -> B -> A cycle is recorded (both leaves), the pass TERMINATES, and an
    # unrelated clean leaf still resolves.
    snap = KeyStore({"a": "@b", "b": "@a", "c": "/ok"})
    expanded, errors = expand(snap, _ctx(), collect_errors=True)
    assert "a" in errors and "cyclic" in errors["a"].lower()
    assert "b" in errors
    assert expanded["c"] == "/ok"


def test_lenient_self_cycle_recorded() -> None:
    snap = KeyStore({"a": "@a"})
    expanded, errors = expand(snap, _ctx(), collect_errors=True)
    assert "a" in errors and "cyclic" in errors["a"].lower()
    assert expanded == KeyStore()


def test_lenient_records_nested_path_key() -> None:
    # The error key is the FULL dotted path of the owning leaf.
    snap = KeyStore({"box": {"bindings": {"rw": {"home": "@gone.x"}}}})
    expanded, errors = expand(snap, _ctx(), collect_errors=True)
    assert "box.bindings.rw.home" in errors


def test_lenient_unrelated_defect_does_not_block_clean_leaves() -> None:
    # Multiple defects collected together; all clean leaves still resolve.
    snap = KeyStore({
        "bad1": "@gone",
        "bad2": "$UNKNOWN/y",
        "good1": "@base/x",
        "base": "/root",
        "good2": "~/cfg",
    })
    expanded, errors = expand(snap, _ctx(), collect_errors=True)
    assert set(errors) == {"bad1", "bad2"}
    assert expanded["good1"] == "/root/x"
    assert expanded["good2"] == f"{HOST_HOME}/cfg"


def test_lenient_present_none_ref_is_not_a_defect() -> None:
    # A whole-value ref to a present-None key is legitimate (§6b), NOT a dangling
    # defect: it propagates None (kept), and is NOT recorded as an error.
    snap = KeyStore({"a": "@b", "b": None})
    expanded, errors = expand(snap, _ctx(), collect_errors=True)
    assert errors == {}
    assert _probe(expanded, "a") is None


def test_lenient_bind_dangling_host_recorded() -> None:
    snap = KeyStore({"x": Bind("@gone.src", "~/dest", None)})
    expanded, errors = expand(snap, _ctx(), collect_errors=True)
    assert "x" in errors and "gone.src" in errors["x"]
    assert _probe(expanded, "x") is __MISSING__


def test_lenient_bind_box_dest_xdg_deferred_not_a_defect() -> None:
    # A box_dest $XDG token is DEFERRED (S17), never resolved host-side, so an
    # unknown-to-host XDG name in a box_dest is NOT a defect.
    snap = KeyStore({"x": Bind("/host", "$XDG_CACHE_HOME/d", "ro")})
    expanded, errors = expand(snap, _ctx(), collect_errors=True)
    assert errors == {}
    bind = expanded["x"]
    assert isinstance(bind, Bind)
    assert bind.box == "$XDG_CACHE_HOME/d"  # deferred raw.


# ---------------------------------------------------------------------------
# ``pref.*`` — never participates in resolution as a derivable key (spec §2h)
# ---------------------------------------------------------------------------

class TestPrefSubtreeIsNotExpanded:
    """spec §2h — 'pref.* keys never participate in resolution as
    derivable keys — resolve_key_set ignores them.'"""

    def test_the_pref_subtree_is_carried_through_raw(self):
        """The RAW request must survive expansion so ``--effective`` can show
        BOTH the request and the result.
        INVERT: remove the expand skip -> the request resolves and this reddens.
        """
        snap = KeyStore({
            "workset": {"template": "/ws/tpl"},
            "pref": {"agent": {"claude": {"template": "@workset.template/sub"}}},
            "agent": {"claude": {"template": "@workset.template/sub"}},
        })
        out = expand(snap, _ctx())
        # The REQUEST stays raw ...
        assert out["pref"]["agent"]["claude"]["template"] == "@workset.template/sub"
        # ... while the same expression AT THE TARGET resolves.
        assert out["agent"]["claude"]["template"] == "/ws/tpl/sub"

    def test_a_null_request_survives_expansion(self):
        snap = KeyStore({"pref": {"agent": {"claude": {"model": None}}}})
        out = expand(snap, _ctx())
        assert dict.__getitem__(out["pref"]["agent"]["claude"], "model") is None

    def test_a_dangling_ref_in_a_request_does_not_drop_the_request(self):
        """A whole-value dangling ref would normally DROP its key (§6b). The
        request record is not resolved, so it cannot be dropped by resolution —
        the user can still SEE what they asked for while the target reports the
        failure."""
        snap = KeyStore({"pref": {"system": {"agent": "@nope.nothing"}}})
        out = expand(snap, _ctx())
        assert out["pref"]["system"]["agent"] == "@nope.nothing"

    def test_the_carried_subtree_is_a_fresh_copy(self):
        """S19 — expansion never aliases its input."""
        inner = KeyStore({"system": {"agent": "goose"}})
        snap = KeyStore({"pref": inner})
        out = expand(snap, _ctx())
        assert out["pref"] == inner
        assert out["pref"] is not inner

    def test_a_key_named_pref_deeper_down_is_still_expanded(self):
        """The skip keys on the ROOT segment only."""
        snap = KeyStore({
            "system": {"cache": "/c"},
            "box": {"env": {"P": "@system.cache/x"}},
            "workset": {"caches": {"pref": Bind("@system.cache/p", "~/p")}},
        })
        out = expand(snap, _ctx())
        assert out["workset"]["caches"]["pref"].host == "/c/p"


class TestPrefReferencesAreRefused:
    """A ``@pref.…`` reference is REFUSED, never resolved (spec §2h)."""

    def test_strict_mode_raises(self):
        """INVERT: return ``_ABSENT`` instead -> the referring key would be
        silently DROPPED and this reddens."""
        snap = KeyStore({
            "pref": {"system": {"agent": "goose"}},
            "box": {"shell": "@pref.system.agent"},
        })
        with pytest.raises(SettingsError) as exc:
            expand(snap, _ctx())
        assert "not resolvable" in str(exc.value)
        assert "REQUEST, not a value" in str(exc.value)

    def test_embedded_reference_also_raises(self):
        snap = KeyStore({
            "pref": {"system": {"agent": "goose"}},
            "box": {"shell": "/bin/@pref.system.agent"},
        })
        with pytest.raises(SettingsError):
            expand(snap, _ctx())

    def test_lenient_mode_records_it_against_the_owning_leaf(self):
        snap = KeyStore({
            "pref": {"system": {"agent": "goose"}},
            "box": {"shell": "@pref.system.agent"},
        })
        expanded, errors = expand(snap, _ctx(), collect_errors=True)
        assert "box.shell" in errors
        assert "not resolvable" in errors["box.shell"]


# --------------------------------------------------------------------------- #
# DEST-KEYED entries (BindEntry) — the destination is the KEY (R-5/R-6)       #
# --------------------------------------------------------------------------- #


def _arm(entries: dict) -> KeyStore:
    return KeyStore({
        "meta": {"box": {"path": "/data/box"}},
        "box": {"bindings": {"rw": entries}},
    })


def test_bind_entry_src_expands_host_side() -> None:
    snap = _arm({"~/a": BindEntry("$XDG_DATA_HOME/seed")})
    out = expand(snap, _ctx())
    entry = _probe(out, "box", "bindings", "rw", "~/a")
    assert entry == BindEntry("/home/u/.local/share/seed")
    assert type(entry) is BindEntry


def test_bind_entry_dest_key_expands_refs_but_defers_tilde_and_vars() -> None:
    # The destination moved from the VALUE to the KEY, so the key now carries the
    # box_dest rule: ``@``-refs resolve; ``$XDG``/``~`` stay RAW for the box side
    # (S17). Both halves of that rule are asserted on ONE arm.
    snap = _arm(
        {
            "@meta.box.path/home": BindEntry("/h/home"),
            "~/.claude": BindEntry("/h/claude"),
            "$XDG_STATE_HOME/k": BindEntry("/h/k"),
        }
    )
    out = expand(snap, _ctx())
    arm = _probe(out, "box", "bindings", "rw")
    assert isinstance(arm, KeyStore)
    assert set(dict.keys(arm)) == {
        "/data/box/home",  # @-ref expanded
        "~/.claude",  # ~ deferred, RAW
        "$XDG_STATE_HOME/k",  # $VAR deferred, RAW
    }


def test_bind_entry_opts_carried_verbatim() -> None:
    snap = _arm({"~/a": BindEntry("@meta.box.path/s", "ro")})
    out = expand(snap, _ctx())
    assert _probe(out, "box", "bindings", "rw", "~/a") == BindEntry("/data/box/s", "ro")


def test_bind_entry_whole_value_src_absent_drops_the_entry() -> None:
    # Same 3-state rule as the name-keyed Bind: an absent whole-value src means the
    # binding cannot point anywhere, so the entry is DROPPED (§6b/§3).
    snap = _arm({"~/a": BindEntry("@box.nope"), "~/b": BindEntry("/h/b")})
    out = expand(snap, _ctx())
    assert _probe(out, "box", "bindings", "rw", "~/a") is __MISSING__
    assert _probe(out, "box", "bindings", "rw", "~/b") == BindEntry("/h/b")


def test_bind_entry_whole_value_src_present_none_yields_none() -> None:
    # ``images_store`` is a NULLABLE path key — a declared box key that is
    # legitimately present-None, which is exactly the 3-state this needs.
    snap = KeyStore(
        {
            "box": {
                "images_store": None,
                "bindings": {"rw": {"~/a": BindEntry("@box.images_store")}},
            }
        }
    )
    out = expand(snap, _ctx())
    assert _probe(out, "box", "bindings", "rw", "~/a") is None


def test_bind_entry_whole_value_dest_key_to_absent_raises() -> None:
    # A destination cannot resolve to no path — the same loud refusal the
    # name-keyed ``box_dest`` gets, moved to the key.
    snap = _arm({"@box.nope": BindEntry("/h/a")})
    with pytest.raises(SettingsError) as exc:
        expand(snap, _ctx())
    assert "destination cannot" in str(exc.value)


def test_two_dest_keys_resolving_to_one_destination_raise() -> None:
    # ⚑ Files store UNRESOLVED, so dest-keying is unique in the UNRESOLVED
    # namespace only (design §2b-CAVEAT). Expansion can collapse two distinct
    # stored keys onto one destination; installing the second would SILENTLY
    # delete the first, and no downstream check could ever see the loss.
    snap = _arm(
        {"@meta.box.path/a": BindEntry("/h/1"), "/data/box/a": BindEntry("/h/2")}
    )
    with pytest.raises(SettingsError) as exc:
        expand(snap, _ctx())
    assert "same destination" in str(exc.value)


def test_non_bind_entry_keys_are_never_expanded() -> None:
    # The key-expansion branch is gated on the VALUE's TYPE, not on the key's
    # SHAPE.  ``box.caches`` is DEST-KEYED, so a key containing ``$`` or ``@`` is
    # legal data there; but these values are plain strings, NOT BindEntry, so the
    # keys must be carried through verbatim — token-looking or not.
    snap = KeyStore({"box": {"caches": {"$HOME": "x", "@a": "y"}}})
    out = expand(snap, _ctx())
    caches = _probe(out, "box", "caches")
    assert isinstance(caches, KeyStore)
    assert set(dict.keys(caches)) == {"$HOME", "@a"}


def test_bind_and_bind_entry_expand_side_by_side() -> None:
    # ⚑ The P5 bridge: both shapes are live at once and each keeps its own rule —
    # the Bind's dest is in its VALUE, the BindEntry's is its KEY.
    snap = KeyStore(
        {
            "meta": {"box": {"path": "/data/box"}},
            "box": {
                "bindings": {
                    "ro": {"legacy": Bind("/h/l", "@meta.box.path/l")},
                    "rw": {"@meta.box.path/n": BindEntry("/h/n")},
                },
            },
        }
    )
    out = expand(snap, _ctx())
    assert _probe(out, "box", "bindings", "ro", "legacy") == Bind("/h/l", "/data/box/l")
    assert _probe(out, "box", "bindings", "rw", "/data/box/n") == BindEntry("/h/n")


def test_lenient_mode_records_a_bind_entry_defect_against_its_leaf() -> None:
    snap = _arm({"~/a": BindEntry("@box.nope/x"), "~/b": BindEntry("/h/b")})
    out, errors = expand(snap, _ctx(), collect_errors=True)
    assert "box.bindings.rw.~/a" in errors
    assert _probe(out, "box", "bindings", "rw", "~/b") == BindEntry("/h/b")


# --------------------------------------------------------------------------- #
# WHICH ENVIRONMENT WON — host-vs-guest variable discrimination               #
# --------------------------------------------------------------------------- #
#
# Every other test in this file resolves against ONE variable map, so a resolved
# string can only prove that expansion HAPPENED — never WHICH side's values it
# used.  These tests give the host and the guest the SAME variable NAMES with
# DIFFERENT VALUES, so the resolved string alone names the winner.  Nothing here
# needs a container: the whole simulation is string checks.
#
#   /path/h/...  a simulated HOST path        /path/b/...  a simulated BOX path
#   host:  XDG_DATA_HOME=foo  XDG_CONFIG_HOME=bar
#   guest: XDG_DATA_HOME=baz  XDG_CONFIG_HOME=qat
#
# The NEGATIVE assertions are the load-bearing half: a future change that starts
# consulting a box-side environment reds here and NAMES the value that leaked.
#
# ⚑ ``$H`` / ``$B`` are not resolvable names — ``settings_resolve._resolve_var``
# admits ``$AGENT``, ``$WORKSET`` and the ``$XDG_*`` family and nothing else — so
# the two XDG names below play the two roles.  The values are bare leaf tokens
# rather than absolute paths because the engine is a pure string substituter and
# a one-word value is what makes the negative assertions readable.
#
# ⚑ ``~`` is NOT a variable; it is part of the path itself, and it is the one
# genuinely two-sided token: the BOX's home in a ``box_dest``, the HOST's home in
# a ``host_src`` — from the SAME context object.  Pinned in both directions.

#: The role of the design's ``$H`` — carried by a ``host_src``.
HOST_HALF_VAR = "XDG_DATA_HOME"
#: The role of the design's ``$B`` — carried by a ``box_dest``.
BOX_HALF_VAR = "XDG_CONFIG_HOME"

SIMULATED_HOST_VARS = {HOST_HALF_VAR: "foo", BOX_HALF_VAR: "bar"}
SIMULATED_GUEST_VARS = {HOST_HALF_VAR: "baz", BOX_HALF_VAR: "qat"}
#: Values that exist ONLY on the simulated guest; none may appear in any result
#: produced by the production path, which resolves host-side throughout.
GUEST_ONLY_VALUES = ("baz", "qat")

SIMULATED_HOST_HOME = "/path/h/home-of-the-host"

HOST_SRC_EXPRESSION = f"/path/h/${HOST_HALF_VAR}"
BOX_DEST_EXPRESSION = f"/path/b/${BOX_HALF_VAR}"
HOST_SRC_ON_THE_HOST = "/path/h/foo"
BOX_DEST_ON_THE_HOST = "/path/b/bar"


def _simulated_host_ctx() -> ResolveCtx:
    """The host-side ctx, shaped like ``agent_select.launch_resolve_ctx`` builds it."""
    return _ctx(host_home=SIMULATED_HOST_HOME, xdg=dict(SIMULATED_HOST_VARS))


def _simulated_guest_ctx() -> ResolveCtx:
    """The counterfactual: the SAME names carrying the box's values."""
    return _ctx(host_home=SIMULATED_HOST_HOME, xdg=dict(SIMULATED_GUEST_VARS))


def _assert_no_guest_value_leaked(resolved: str) -> None:
    """Fail naming the guest value that reached *resolved*."""
    for guest_value in GUEST_ONLY_VALUES:
        assert guest_value not in resolved, (
            f"guest value {guest_value!r} leaked into {resolved!r}"
        )


def test_host_src_variable_resolves_from_the_host_map() -> None:
    # Element 0 of an entry: the eager pass expands it host-side, so the host
    # value wins and neither guest value can appear.
    snap = _arm({"/path/b/fixed": BindEntry(HOST_SRC_EXPRESSION)})
    out = expand(snap, _simulated_host_ctx())
    entry = _probe(out, "box", "bindings", "rw", "/path/b/fixed")
    assert isinstance(entry, BindEntry)
    assert entry.src == HOST_SRC_ON_THE_HOST
    _assert_no_guest_value_leaked(entry.src)


def test_box_dest_variable_resolves_from_the_host_map() -> None:
    # The map KEY: DEFERRED by the eager pass, then resolved by the ONE box_dest
    # resolver.  Deferral is not guest resolution — the values that arrive are
    # still the host's (keyspec :344-347).
    snap = _arm({BOX_DEST_EXPRESSION: BindEntry("/path/h/fixed")})
    out = expand(snap, _simulated_host_ctx())
    # Still RAW after the eager pass: the token survived, unexpanded.
    assert _probe(out, "box", "bindings", "rw", BOX_DEST_EXPRESSION) == BindEntry(
        "/path/h/fixed"
    )
    resolved = resolve_box_dest(BOX_DEST_EXPRESSION, _simulated_host_ctx())
    assert resolved == BOX_DEST_ON_THE_HOST
    _assert_no_guest_value_leaked(resolved)


def test_the_launch_seam_keeps_both_halves_on_the_host_map() -> None:
    """The production path: ONE ctx feeds the eager pass AND the box_dest resolve.

    ``commands.start`` passes ``launch_resolve_ctx``'s host-side ctx as
    ``box_ctx``, so this mirrors what a real launch does with both halves at once.
    """
    snap = _arm({BOX_DEST_EXPRESSION: BindEntry(HOST_SRC_EXPRESSION)})
    host_ctx = _simulated_host_ctx()
    entries = snapshot_category_entries(
        expand(snap, host_ctx), active_agent="claude", box_ctx=host_ctx,
    )
    assert len(entries) == 1
    entry = entries[0]
    assert entry.host_src == HOST_SRC_ON_THE_HOST
    assert entry.box_dest == BOX_DEST_ON_THE_HOST
    _assert_no_guest_value_leaked(entry.host_src)
    _assert_no_guest_value_leaked(entry.box_dest)


def test_a_guest_valued_context_would_change_both_answers() -> None:
    """The discriminator is LIVE — the negatives above are not vacuous.

    Feeding the same expressions a guest-valued map yields the guest strings, so
    the map is genuinely what decides, and the host results are a real choice.
    """
    snap = _arm({BOX_DEST_EXPRESSION: BindEntry(HOST_SRC_EXPRESSION)})
    guest_ctx = _simulated_guest_ctx()
    entry = _probe(
        expand(snap, guest_ctx), "box", "bindings", "rw", BOX_DEST_EXPRESSION
    )
    assert isinstance(entry, BindEntry)
    assert entry.src == "/path/h/baz"
    assert resolve_box_dest(BOX_DEST_EXPRESSION, guest_ctx) == "/path/b/qat"


def test_the_context_map_is_the_only_variable_source(monkeypatch) -> None:
    """No ambient environment is consulted — not the host's, not any box's.

    ``ResolveCtx.xdg`` is the ONE variable map the engine reads; there is no
    guest-side map to leak from, and an unknown name is a NAMED error rather than
    a fabricated default or an ``os.environ`` fallback.
    """
    monkeypatch.setenv(BOX_HALF_VAR, SIMULATED_GUEST_VARS[BOX_HALF_VAR])
    monkeypatch.setenv(HOST_HALF_VAR, SIMULATED_GUEST_VARS[HOST_HALF_VAR])
    # The ctx still decides.
    assert (
        resolve_box_dest(BOX_DEST_EXPRESSION, _simulated_host_ctx())
        == BOX_DEST_ON_THE_HOST
    )
    # An empty map does NOT fall through to the process environment.
    empty_map_ctx = _ctx(host_home=SIMULATED_HOST_HOME, xdg={})
    with pytest.raises(SettingsError) as exc:
        resolve_box_dest(BOX_DEST_EXPRESSION, empty_map_ctx)
    assert BOX_HALF_VAR in str(exc.value)


def test_space_selects_the_home_and_nothing_else_about_variables() -> None:
    """THE STRUCTURAL FACT the tests above rest on: there is no guest variable map.

    ``space`` picks which home a leading ``~`` becomes and NOTHING ELSE —
    ``_resolve_var`` never consults it — so a ``$VAR``-only expression resolves
    IDENTICALLY in both spaces from one ctx.  A future guest-side map would have
    to break this equality, which is what makes it worth pinning.
    """
    host_ctx = _simulated_host_ctx()
    lookup = _refusing_lookup
    in_host_space = expand_expr(
        BOX_DEST_EXPRESSION, space="host", ctx=host_ctx, lookup=lookup
    )
    in_guest_space = expand_expr(
        BOX_DEST_EXPRESSION, space="guest", ctx=host_ctx, lookup=lookup
    )
    assert in_host_space == in_guest_space == BOX_DEST_ON_THE_HOST

    # ``~`` is the one token the space DOES change — the contrast that shows the
    # equality above is a fact about variables, not about a dead parameter.
    assert (
        expand_expr("~/x", space="host", ctx=host_ctx, lookup=lookup)
        == SIMULATED_HOST_HOME + "/x"
    )
    assert (
        expand_expr("~/x", space="guest", ctx=host_ctx, lookup=lookup)
        == GUEST_HOME + "/x"
    )


def _refusing_lookup(ref: str, chain: tuple[str, ...]) -> str:
    """``expand_expr`` lookup for expressions that carry no ``@``-ref."""
    raise AssertionError(f"unexpected @-ref lookup: {ref}")


def test_tilde_is_the_box_home_in_a_box_dest_and_the_host_home_in_a_host_src() -> None:
    """The one two-sided token, both directions, from a SINGLE context object.

    ``~`` is not a variable — it is part of the path itself — so it does NOT
    follow the ``$VAR`` rule pinned above: it answers per SIDE, not per map.
    """
    host_ctx = _simulated_host_ctx()
    snap = _arm({"~/dest": BindEntry("~/src")})
    entries = snapshot_category_entries(
        expand(snap, host_ctx), active_agent="claude", box_ctx=host_ctx,
    )
    assert len(entries) == 1
    entry = entries[0]

    # host_src side: the HOST's home, and the box home never appears.
    assert entry.host_src == SIMULATED_HOST_HOME + "/src"
    assert GUEST_HOME not in entry.host_src

    # box_dest side: the BOX's home, and the host home never appears.
    assert entry.box_dest == GUEST_HOME + "/dest"
    assert SIMULATED_HOST_HOME not in entry.box_dest
