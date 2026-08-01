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

from kanibako.settings_expand import _is_whole_value_ref, expand
from kanibako.settings_resolve import GUEST_HOME, ResolveCtx, SettingsError
from kanibako.settings_store import _MISSING, Bind, KeyStore

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
    """Walk *segments* with the unbound-``dict.get`` probe (S3); ``_MISSING`` if
    any segment is absent (so a present-None leaf differs from an absent one)."""
    node: object = store
    for seg in segments:
        if not isinstance(node, KeyStore):
            return _MISSING
        node = dict.get(node, seg, _MISSING)
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
        "system": {"data": "$XDG_DATA_HOME/kanibako",
                   "backup": "@system.data/backup"},
    })
    before = copy.deepcopy(snap)
    out = expand(snap, _ctx())
    assert snap == before  # S19 — input untouched.
    assert out is not snap
    assert out["system"]["backup"] == "/home/u/.local/share/kanibako/backup"


def test_fresh_tree_no_aliasing() -> None:
    snap = KeyStore({"sys": {"x": "term"}})
    out = expand(snap, _ctx())
    assert out["sys"] is not snap["sys"]


# --------------------------------------------------------------------------- #
# Host-side $XDG / ~ expansion                                                #
# --------------------------------------------------------------------------- #


def test_host_xdg_and_tilde_expand() -> None:
    snap = KeyStore({
        "system": {"data": "$XDG_DATA_HOME/kanibako"},
        "home_thing": "~/sub",
    })
    out = expand(snap, _ctx())
    assert out["system"]["data"] == "/home/u/.local/share/kanibako"
    assert out["home_thing"] == "/home/u/sub"


def test_braced_xdg_var() -> None:
    snap = KeyStore({"x": "${XDG_STATE_HOME}/k.sock"})
    out = expand(snap, _ctx())
    assert out["x"] == "/home/u/.local/state/k.sock"


# --------------------------------------------------------------------------- #
# Transitive @-ref chains (fixpoint / order-independent)                      #
# --------------------------------------------------------------------------- #


def test_transitive_chain_collapses() -> None:
    # backup -> @system.data -> $XDG_DATA_HOME terminal (spec §1 chain shape).
    snap = KeyStore({
        "system": {
            "data": "$XDG_DATA_HOME/kanibako",
            "global": "@system.data/global",
            "template": "@system.global/template",
        },
    })
    out = expand(snap, _ctx())
    assert out["system"]["template"] == (
        "/home/u/.local/share/kanibako/global/template"
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
    assert _probe(out, "a") is _MISSING
    assert _probe(out, "b") is _MISSING


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
    assert _probe(out, "a") is _MISSING
    assert _probe(out, "b") is _MISSING


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
    snap = KeyStore({"avail": "@{workset.enabled}", "workset": {"enabled": True}})
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
    assert _probe(out, "box", "bindings", "rw", "x") is _MISSING


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
    snap = KeyStore({"avail": "@workset.enabled", "workset": {"enabled": True}})
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
    snap = KeyStore({"name": "x-@y-z", "y": None})
    out = expand(snap, _ctx())
    assert out["name"] == "x--z"


def test_embedded_ref_substitutes_value() -> None:
    snap = KeyStore({"name": "kanibako-@box.id", "box": {"id": "abc"}})
    out = expand(snap, _ctx())
    assert out["name"] == "kanibako-abc"


def test_if_block_literal_carried_for_downstream_renderer() -> None:
    # %if% is a downstream RENDER concern, NOT this pass: the embedded @-ref
    # substitutes; the %...% literal text is carried verbatim (no key deletion).
    snap = KeyStore({
        "cn": "kanibako-@box.meta.name%if @box.meta.helper_num: -h-@box.meta.helper_num%",
        "box": {"meta": {"name": "droste"}},
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
    assert _probe(out, "box", "bindings", "rw", "x") is _MISSING


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
    snap = KeyStore({"a": "x-@b-y", "b": "p-@a-q"})
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
    assert _probe(out, "a") is _MISSING


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
    from kanibako.settings_resolve import _REF_NAME_RE
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
    assert _probe(expanded, "a") is _MISSING
    assert expanded["c"] == "/ok"


def test_lenient_collects_dangling_embedded_ref() -> None:
    snap = KeyStore({"a": "pre-@nope.x-post", "c": "/ok"})
    expanded, errors = expand(snap, _ctx(), collect_errors=True)
    assert "a" in errors and "nope.x" in errors["a"]
    assert _probe(expanded, "a") is _MISSING
    assert expanded["c"] == "/ok"


def test_lenient_collects_dangling_braced_ref_like_a_bare_one() -> None:
    # PHASE R: "set-time validation treats a dangling BRACED ref exactly as it
    # treats a dangling bare one" — same error map, same key, same named ref, in
    # BOTH the whole-value and the embedded (suffixed) position.
    snap = KeyStore({"a": "@{nope.missing}", "b": "@{nope.missing}.jsonl", "c": "/ok"})
    expanded, errors = expand(snap, _ctx(), collect_errors=True)
    assert "a" in errors and "nope.missing" in errors["a"]
    assert "b" in errors and "nope.missing" in errors["b"]
    assert _probe(expanded, "a") is _MISSING
    assert _probe(expanded, "b") is _MISSING
    assert expanded["c"] == "/ok"


def test_lenient_collects_malformed_braced_ref() -> None:
    # A malformed brace is a defect RECORDED against its leaf, not a raise that
    # takes the whole lenient pass down (the ``config set`` path depends on this).
    snap = KeyStore({"a": "@{nope", "c": "/ok"})
    expanded, errors = expand(snap, _ctx(), collect_errors=True)
    assert "a" in errors and "Unterminated" in errors["a"]
    assert _probe(expanded, "a") is _MISSING
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
    assert _probe(expanded, "a") is _MISSING
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
    assert _probe(expanded, "x") is _MISSING


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
    """spec §2h L1263 — 'pref.* keys never participate in resolution as
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
    """A ``@pref.…`` reference is REFUSED, never resolved (spec §2h L1263)."""

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
