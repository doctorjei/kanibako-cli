"""``pref.*`` machinery — the mutation matrix (spec §2h).

Every risk in this phase is silent-failure shaped, so each test below names the
INVERSION that must turn it red. A test whose inversion does not redden it is
not evidence of anything.
"""

from __future__ import annotations

import logging

import pytest
import yaml

from kanibako.settings.kb_store import Bind, BindEntry
from kanibako.settings.keystore import KeyStore
from kanibako.settings.settings_prefs import (
    ALLOWLIST,
    LOCATOR_CLOSURE,
    AgentNames,
    PrefRequest,
    allowlist_reason,
    apply_prefs,
    collect_prefs,
    forbidden_tier_reason,
    glob_match,
    key_reason,
    pref_overlay,
    pref_request_for,
    pref_value,
    prefs_from_partial,
    refuse_pref_table,
    validate_pref,
)
from kanibako.settings.settings_resolve import SettingsError

AGENTS = AgentNames({"claude", "codex", "goose"})


def req(target, value="v", level="box", source=None):
    return PrefRequest(target=target, value=value, level=level, source=source)


def write(path, doc):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# §0 GLOB convention (spec §0)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("pattern,key,expected", [
    ("system.agent", "system.agent", True),
    ("system.agent", "system.cache", False),
    ("system.agent", "system.agent.x", False),      # `*`-free pattern is exact
    ("agent.*.**", "agent.claude.model", True),
    ("agent.*.**", "agent.claude.bindings.rw", True),
    ("agent.*.**", "agent.claude", False),          # `**` is ONE-or-more
    ("agent.*.**", "agent.claude.", False),         # the malformed trailing dot
    ("agent.*.**", "box.claude.model", False),
])
def test_glob_convention(pattern, key, expected):
    """`*` matches exactly ONE segment; `**` matches the tail at any depth and
    is one-or-more BY CONSTRUCTION (the separator is part of the pattern)."""
    assert glob_match(pattern, key) is expected


# ---------------------------------------------------------------------------
# FILTER 1 — valid key (VALIDITY, not existence)
# ---------------------------------------------------------------------------

def test_filter1_accepts_a_new_name_in_a_parametric_family():
    """INVERT: make filter 1 test EXISTENCE -> this reddens.
    spec §2h."""
    assert key_reason("agent.claude.env.BOOOOOO", valid_agents=AGENTS) is None
    # ⚑ NOT a bind-shaped category — all six are TERMINAL and dest-keyed
    # (``bindings.{ro,rw}`` R-5/R-10; ``caches``/``seeded``/``common``/``synced``
    # 2026-08-08c), so only the BARE category is a valid target and there is no
    # free <name> under it. The destinations live INSIDE the value.
    assert key_reason("agent.claude.bindings.rw", valid_agents=AGENTS) is None
    assert key_reason(
        "agent.claude.bindings.rw.boooooo", valid_agents=AGENTS,
    ) is not None
    assert key_reason("agent.claude.common", valid_agents=AGENTS) is None
    assert key_reason(
        "agent.claude.common.boooooo", valid_agents=AGENTS,
    ) is not None


def test_filter1_rejects_fabrication():
    """INVERT: accept anything -> reddens."""
    assert key_reason("agent.zippity.wibble", valid_agents=AGENTS) is not None
    assert key_reason("agent.claude.notakey", valid_agents=AGENTS) is not None


# ---------------------------------------------------------------------------
# FILTER 2 — the allowlist
# ---------------------------------------------------------------------------

def test_filter2_accepts_the_two_allowlist_entries():
    assert allowlist_reason("system.agent", valid_agents=AGENTS) is None
    assert allowlist_reason("agent.claude.model", valid_agents=AGENTS) is None
    assert allowlist_reason(
        "agent.claude.bindings.rw", valid_agents=AGENTS,
    ) is None


def test_filter2_rejects_everything_else():
    """INVERT: accept anything -> reddens."""
    for target in ("box.image", "workset.boxes", "system.cache", "meta.box.path"):
        r = allowlist_reason(target, valid_agents=AGENTS)
        assert r is not None and "not requestable" in r, target


def test_filter2_rejects_an_unknown_agent_but_accepts_a_non_active_one():
    """§2h — the test is 'is it a VALID agent', NOT 'is it the ACTIVE one'."""
    r = allowlist_reason("agent.zippity.model", valid_agents=AGENTS)
    assert r is not None and "not a valid agent" in r
    assert allowlist_reason("agent.goose.model", valid_agents=AGENTS) is None


def test_filter2_accepts_a_persona_node():
    assert allowlist_reason(
        "agent.navigator℘claude.model", valid_agents=AGENTS,
    ) is None


def test_filter2_does_not_suggest_setting_an_unsettable_scope_directly():
    """A 'set it directly at the meta scope' hint would name a place that does
    not exist."""
    for target in ("meta.box.path", "config.data", "pref.system.agent"):
        r = allowlist_reason(target, valid_agents=AGENTS)
        assert r is not None and "directly at the" not in r, target


# ---------------------------------------------------------------------------
# FILTER 3 — the three forbidden tiers (predicate level: see the module note on
# why these are not reachable end-to-end under today's allowlist)
# ---------------------------------------------------------------------------

def test_filter3_structural():
    """A later-or-same-resolving key needs no pref. INVERT: drop the arm."""
    r = forbidden_tier_reason("box.shell", level="box")
    assert r is not None and "structural tier" in r
    r = forbidden_tier_reason("workset.template", level="workset")
    assert r is not None and "structural tier" in r
    # Strictly EARLIER is fine.
    assert forbidden_tier_reason("system.agent", level="box") is None
    assert forbidden_tier_reason("agent.claude.model", level="workset") is None


@pytest.mark.parametrize("target,fragment", [
    ("meta.box.path", "read-only by contract"),
    ("config.data", "bootstrap foundation"),
    ("pref.system.agent", "termination argument"),
])
def test_filter3_categorical(target, fragment):
    """INVERT: drop the categorical arm -> reddens. spec §2h."""
    r = forbidden_tier_reason(target, level="box")
    assert r is not None and "categorical tier" in r and fragment in r


def test_filter3_categorical_does_not_bar_meta_values_from_CHANGING():
    """§2h: only DIRECT targeting is barred — meta VALUES still change
    because the key they derive from changed, which is the entire point."""
    assert forbidden_tier_reason("system.agent", level="box") is None


@pytest.mark.parametrize("target", sorted(LOCATOR_CLOSURE))
def test_filter3_locator_closure(target):
    """INVERT: drop the closure -> reddens. spec §2h."""
    r = forbidden_tier_reason(target, level="box")
    assert r is not None and "locator closure" in r


def test_locator_closure_excludes_system_agent():
    """⚑ The headline feature. A naive DERIVATION of the closure would capture
    system.agent (meta.agent.<a>.settings derives from it) and break prefs
    entirely — this pins the exclusion."""
    assert "system.agent" not in LOCATOR_CLOSURE
    assert forbidden_tier_reason("system.agent", level="box") is None


def test_locator_closure_is_exactly_the_two_settable_members():
    assert LOCATOR_CLOSURE == frozenset({"workset.boxes", "workset.kuid"})


def test_filter3_is_reachable_end_to_end_with_a_widened_allowlist():
    """The tiers are SHADOWED by the allowlist today (nothing barred is
    requestable). Injecting a widened allowlist proves the tier really is wired
    into the decision, not merely present."""
    r = validate_pref(
        req("workset.boxes", level="box"),
        valid_agents=AGENTS,
        allowlist=("workset.**",),
    )
    assert r is not None and "locator closure" in r


# ---------------------------------------------------------------------------
# The three filters are INDEPENDENT and ALL failures are reported
# ---------------------------------------------------------------------------

def test_all_failing_filters_are_reported():
    """INVERT: report only the first -> reddens. Keeps message quality
    independent of the SUPPORTING surface's completeness."""
    r = validate_pref(req("meta.box.path"), valid_agents=AGENTS)
    assert r is not None
    assert "not requestable" in r          # filter 2
    assert "categorical tier" in r         # filter 3


def test_reason_is_a_string_not_a_bool():
    """spec §2h — 'the forbidden-tier check must return a REASON, not a
    boolean', so the error can name the cause."""
    r = forbidden_tier_reason("meta.box.path", level="box")
    assert isinstance(r, str)


# ---------------------------------------------------------------------------
# INSTALLATION — VERBATIM, including None
# ---------------------------------------------------------------------------

def test_values_are_installed_verbatim_including_none():
    """⚑ THE named hazard (spec §2h): `if value is None: continue` is
    the natural guard and silently implements the REJECTED reading, deleting a
    box's ONLY suppression channel with no error and no diff.
    INVERT: add that guard -> this reddens.

    ⚑ RE-POSED AT THE TERMINAL KEY (2026-08-08c). The request used to name
    ``agent.claude.common.plugins``, which is no longer a key at all — and this
    test STAYED GREEN through the flip, because ``pref_overlay`` installs at the
    dotted path and performs NO key validation (that is ``validate_pref``'s job,
    which this call deliberately bypasses). Green here was never evidence that the
    spelling was legal, and must not be read as such.
    """
    overlay = pref_overlay([
        req("agent.claude.common", value=None),
        req("system.agent", value="goose"),
    ])
    node = overlay["agent"]["claude"]
    assert "common" in dict.keys(node)
    assert dict.__getitem__(node, "common") is None
    assert overlay["system"]["agent"] == "goose"


@pytest.mark.parametrize("value", [None, "", "empty", 0, False, ["a"]])
def test_the_pref_layer_interprets_no_emptiness_idiom(value):
    """spec §2h — present-None (tri-state omit), terminal "" (!= unset)
    and the COPY-disable sentinel "empty" all forward UNTOUCHED; otherwise the
    pref becomes a FOURTH place deciding what 'empty' means.
    INVERT: any interpretation here -> reddens.

    ⚑ RE-POSED AT THE TERMINAL KEY (2026-08-08c): ``agent.claude.seeded.template``
    is no longer a key. It stayed green through the flip only because
    ``pref_overlay`` does no key validation — see
    ``test_values_are_installed_verbatim_including_none``.
    ⚑⚑ ``seeded`` IS A COPY AND STAYS A COPY. Only the KEY SHAPE moved; nothing
    here turns it into a mount.
    """
    overlay = pref_overlay([req("agent.claude.seeded", value=value)])
    got = dict.__getitem__(overlay["agent"]["claude"], "seeded")
    assert got == value or (got is None and value is None)


def test_bind_shaped_pref_value_installs_as_a_bind():
    """⚑⚑ THIS ROW PINNED A STRUCTURALLY IMPOSSIBLE SHAPE UNTIL 2026-08-08c.
    It installed a 3-element ``Bind`` under ``agent.claude.common.x`` and asserted
    it landed as a ``Bind`` — and it passed the whole way through the dest-keying
    flip, because ``pref_overlay`` installs whatever object it is handed at
    whatever dotted path it is given, validating NEITHER. Neither the key nor the
    value type exists any more: ``common`` is TERMINAL, its value is the whole
    dest-keyed map, and its leaf is a 2-element ``BindEntry``.

    ⚑ ``Bind`` and ``BindEntry`` are BOTH legally 2 elements with OPPOSITE
    meanings, so nothing may tell them apart by arity — which is exactly why this
    row asserts the TYPE.
    """
    arm = KeyStore()
    arm["/home/agent/dst"] = BindEntry("/src", None)
    overlay = pref_overlay([req("agent.claude.common", value=arm)])
    installed = overlay["agent"]["claude"]["common"]
    assert isinstance(installed, KeyStore)
    entry = dict.get(installed, "/home/agent/dst")
    assert isinstance(entry, BindEntry)
    assert not isinstance(entry, Bind)
    assert (entry.src, entry.opts) == ("/src", None)


# ---------------------------------------------------------------------------
# apply_prefs — validation, ordering, and the ERROR contract
# ---------------------------------------------------------------------------

def test_apply_splits_by_level_in_order():
    ws, box = apply_prefs(
        [req("system.agent", "a", "workset"), req("system.agent", "b", "box")],
        valid_agents=AGENTS,
    )
    assert ws["system"]["agent"] == "a"
    assert box["system"]["agent"] == "b"


def test_a_failed_pref_raises_naming_key_level_file_and_reason(tmp_path):
    """spec §2h — 'We don't want to just moving on with bad
    settings.' INVERT: warn-and-continue -> reddens."""
    src = tmp_path / "settings.yaml"
    with pytest.raises(SettingsError) as exc:
        apply_prefs(
            [req("agent.zippity.wibble", level="box", source=src)],
            valid_agents=AGENTS,
        )
    msg = str(exc.value)
    assert "pref.agent.zippity.wibble" in msg     # the KEY
    assert "at the box level" in msg              # the LEVEL
    assert str(src) in msg                        # the FILE
    assert "not a valid agent" in msg             # the REASON
    assert "launch is stopped" in msg


@pytest.mark.parametrize("target,level", [
    ("agent.claude.notakey", "box"),
    ("box.image", "box"),
    ("meta.box.path", "box"),
    ("config.data", "box"),
    ("pref.system.agent", "box"),
    ("agent.zippity.model", "workset"),
])
def test_every_rejection_names_key_level_and_reason(target, level, tmp_path):
    src = tmp_path / f"{level}.yaml"
    with pytest.raises(SettingsError) as exc:
        apply_prefs(
            [req(target, level=level, source=src)], valid_agents=AGENTS,
        )
    msg = str(exc.value)
    assert f"pref.{target}" in msg
    assert f"at the {level} level" in msg
    assert str(src) in msg
    assert "spec §2h" in msg


def test_valid_prefs_are_accepted():
    ws, box = apply_prefs(
        [
            req("system.agent", "goose", "workset"),
            req("agent.claude.model", "opus", "box"),
            # ⚑ The CATEGORY is the target — ``common`` is TERMINAL and
            # dest-keyed (2026-08-08c), so there is no per-entry key to request.
            req("agent.claude.common", None, "box"),
        ],
        valid_agents=AGENTS,
    )
    assert ws["system"]["agent"] == "goose"
    assert box["agent"]["claude"]["model"] == "opus"


# ---------------------------------------------------------------------------
# COLLECTION — nested only, both files, order
# ---------------------------------------------------------------------------

def test_collect_reads_both_files_workset_first(tmp_path):
    ws = write(tmp_path / "ws.yaml", {"pref": {"system": {"agent": "goose"}}})
    box = write(
        tmp_path / "box.yaml",
        {"pref": {"agent": {"claude": {"model": "opus"}}}, "box": {"image": "x"}},
    )
    got = collect_prefs(ws, box)
    assert [(p.target, p.level) for p in got] == [
        ("system.agent", "workset"), ("agent.claude.model", "box"),
    ]
    assert got[0].source == ws and got[1].source == box


def test_collect_tolerates_absent_files_and_absent_tables(tmp_path):
    assert collect_prefs(None, None) == []
    empty = write(tmp_path / "e.yaml", {"box": {"image": "x"}})
    assert collect_prefs(None, empty) == []
    assert collect_prefs(None, tmp_path / "nope.yaml") == []


def test_collect_parses_a_bind_shaped_pref_as_a_bind(tmp_path):
    """The pref path mirrors the target path, so `_parse_node`'s ancestor test
    makes a bind-shaped request a real bind — the property D5 protects.

    ⚑ ``common`` is TERMINAL and DEST-KEYED since 2026-08-08c, so the request ends
    AT the category and its value is the whole ``{box_dest: [src[, opts]]}`` map.
    The leaf is a 2-element :class:`BindEntry` carrying NO destination — the
    destination is the map key, and R-11 canonicalizes it on read.
    """
    box = write(
        tmp_path / "box.yaml",
        {"pref": {"agent": {"claude": {"common": {"~/dst": ["/src"]}}}}},
    )
    (p,) = collect_prefs(None, box)
    assert p.target == "agent.claude.common"
    assert isinstance(p.value, KeyStore)
    entry = dict.get(p.value, "/home/agent/dst")
    assert isinstance(entry, BindEntry)
    assert (entry.src, entry.opts) == ("/src", None)


def test_collect_preserves_a_null_request(tmp_path):
    """⚑ The null now lands on the CATEGORY, which is the whole key: a
    dest-keyed category has no per-entry key to null from the pref table."""
    box = write(
        tmp_path / "box.yaml",
        {"pref": {"agent": {"claude": {"common": None}}}},
    )
    (p,) = collect_prefs(None, box)
    assert p.target == "agent.claude.common"
    assert p.value is None


def test_a_dotted_leaf_inside_the_pref_table_is_an_error(tmp_path):
    """D5 — one spelling. A dotted literal would never be bind-parsed, so the
    SAME request would behave differently depending on how it was spelled.
    INVERT: accept both -> reddens."""
    box = write(tmp_path / "box.yaml", {"pref": {"system.agent": "goose"}})
    with pytest.raises(SettingsError) as exc:
        collect_prefs(None, box)
    assert "DOTTED key" in str(exc.value)
    assert "NESTED table" in str(exc.value)


def test_prefs_from_partial_on_a_non_pref_store():
    assert prefs_from_partial(KeyStore(), level="box") == []


# ---------------------------------------------------------------------------
# ⚑ THE WALK STOPS AT A TERMINAL DEST-KEYED CATEGORY (§4(e), disk-store R-5/R-10)
#
# Before this, `_flatten_pref_node` recursed into EVERY nested KeyStore, so a
# pref over `masks` or a bindings arm was walked one level too far: the walker
# reached the DESTINATIONS, which are DATA, and either manufactured a target that
# is not a key (`pref.agent.claude.bindings.rw./home/agent/x`) or — for the
# overwhelmingly common case of a dest containing a dot — tripped the DOTTED-KEY
# raise and reported a spelling fault the user never committed.
# ---------------------------------------------------------------------------

def test_a_pref_over_a_bindings_arm_is_ONE_request_carrying_the_whole_map(tmp_path):
    """R-5 — the arm is the request; the destinations are its VALUE.

    ⚑ MUTATION: drop `and not is_terminal_category_tail(child)` from
    `_flatten_pref_node` -> the walker descends into the arm, the dotted dest
    `~/.claude` trips the DOTTED-key raise, and this test dies on the raise.
    Nothing else in this file asserts a target ENDING at a bindings arm.
    """
    box = write(
        tmp_path / "box.yaml",
        {"pref": {"agent": {"claude": {"bindings": {"rw": {
            "~/.claude": ["/host/claude", "~/.claude"],
            "/home/agent/data": ["/host/data", "/home/agent/data"],
        }}}}}},
    )
    (p,) = collect_prefs(None, box)
    assert p.target == "agent.claude.bindings.rw"
    # The VALUE is the whole map, carried as the nested node the merge folds
    # PER-ENTRY (the masks precedent) — not flattened into per-dest requests.
    assert isinstance(p.value, KeyStore)
    # ⚑ The file spells one dest ``~/.claude`` and it reads back ABSOLUTIZED: R-11
    # canonicalizes a binding DESTINATION at the reader as well as the producer,
    # so ``~`` and ``~/`` cannot become two entries at one destination. That the
    # normalization happens to the DESTINATIONS rather than to the target is the
    # same fact this test pins from the other side — they are DATA, not key
    # segments. (``masks`` below is deliberately NOT normalized: R-11 is bindings
    # only until Jei rules DS-BL7.)
    assert set(dict.keys(p.value)) == {"/home/agent/.claude", "/home/agent/data"}


def test_a_pref_over_masks_is_ONE_request_too(tmp_path):
    """masks is the SAME shape and was ALWAYS latently broken here — zero masks
    coverage existed in this file before P4'. One rule, both categories."""
    box = write(
        tmp_path / "box.yaml",
        {"pref": {"agent": {"claude": {"masks": {
            "~/.ssh": True, "/home/agent/x": None,
        }}}}},
    )
    (p,) = collect_prefs(None, box)
    assert p.target == "agent.claude.masks"
    assert isinstance(p.value, KeyStore)
    assert dict.get(p.value, "~/.ssh") is True
    # A present-None entry survives VERBATIM — it is the per-entry unmask, and
    # this layer classifies nothing (§2h).
    assert "/home/agent/x" in dict.keys(p.value)
    assert dict.get(p.value, "/home/agent/x") is None


def test_the_terminal_stop_makes_the_request_VALIDATE_instead_of_crashing(tmp_path):
    """The point of the fix, end to end: a pref over a bindings arm now reaches
    the THREE FILTERS and gets a real verdict.

    Previously `pref.box.bindings.rw` (a genuinely un-requestable target) died in
    the WALKER with "uses a DOTTED key inside the 'pref:' table" — a message about
    a spelling the user did not use, hiding the actual fault. Now it is refused by
    the allowlist and the structural tier, which is the truth.
    """
    box = write(
        tmp_path / "box.yaml",
        {"pref": {"box": {"bindings": {"rw": {"~/.claude": ["/h", "~/.claude"]}}}}},
    )
    (p,) = collect_prefs(None, box)
    assert p.target == "box.bindings.rw"
    with pytest.raises(SettingsError) as exc:
        apply_prefs([p], valid_agents=AGENTS)
    msg = str(exc.value)
    assert "DOTTED" not in msg, msg
    assert "not requestable" in msg
    assert "resolves at or after the box level" in msg


def test_the_dotted_key_raise_still_fires_everywhere_else(tmp_path):
    """The terminal stop is NARROW: it does not weaken D5's one-spelling rule.

    ⚑ MUTATION: widen the stop to every KeyStore -> nothing here changes, but
    `test_a_dotted_leaf_inside_the_pref_table_is_an_error` and this test both
    stay green only because the guard is on the RECURSION, not on the raise.
    """
    box = write(
        tmp_path / "box.yaml",
        {"pref": {"agent": {"claude": {"common.plugins": ["/s", "~/d"]}}}},
    )
    with pytest.raises(SettingsError) as exc:
        collect_prefs(None, box)
    assert "DOTTED key" in str(exc.value)


def test_a_bindings_arm_pref_installs_the_map_AT_the_arm(tmp_path):
    """`pref_overlay` must land the map at the arm key, not explode it — that is
    what lets `settings_merge` fold it PER-ENTRY against inherited levels."""
    # ⚑ The VALUE is ``[src[, options]]`` — the DESTINATION is the map key and is
    # NOT repeated inside it (R-3/R-5). The pre-P6 fixture spelled
    # ``["/host/x", "~/.claude/x"]``, which still has two elements and so parses
    # CLEANLY as ``(src, opts)`` with the destination becoming mount options —
    # the arity hazard ``unpack_bind_entry``'s docstring warns about, silently
    # wrong rather than refused. That is why the fixture is re-derived here rather
    # than left to "still pass".
    box = write(
        tmp_path / "box.yaml",
        {"pref": {"agent": {"claude": {"bindings": {"ro": {
            "~/.claude/x": ["/host/x", "ro"],
        }}}}}},
    )
    (p,) = collect_prefs(None, box)
    overlay = pref_overlay([p])
    arm = dict.get(dict.get(dict.get(
        dict.get(overlay, "agent"), "claude"), "bindings"), "ro")
    assert isinstance(arm, KeyStore)
    # ⚑ The file spells the dest ``~/.claude/x``; R-11 absolutizes a binding
    # destination on read, so the map lands keyed by the canonical form. The
    # overlay must carry that same canonicalization, or a pref would land at a
    # destination the floor spells differently and silently fail to override it.
    assert "/home/agent/.claude/x" in dict.keys(arm)
    assert "~/.claude/x" not in dict.keys(arm)
    # The entry arrived through the SAME parse the target key uses, so it is a
    # real BindEntry — the D5 property, preserved across the terminal stop.
    # ⚑ ``BindEntry``, not ``Bind``: a DEST-KEYED arm's leaf is the 2-element
    # ``(src, opts)`` pair (R-6). ``Bind`` remains the name-keyed 3-element type
    # the other categories still use, which is exactly why the two must never be
    # told apart by arity.
    entry = dict.get(arm, "/home/agent/.claude/x")
    assert isinstance(entry, BindEntry)
    assert (entry.src, entry.opts) == ("/host/x", "ro")


# ---------------------------------------------------------------------------
# WRITE SITE — warn+drop in a file where a pref is illegal (D4)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("level", ["base", "system", "agent"])
def test_pref_table_in_an_illegal_file_warns_and_drops(level, tmp_path, caplog):
    """D4 — the same treatment `_drop_upward_scopes` gives the sibling fault.
    The HARD refusal lives at the write site (`config set`).
    INVERT: drop it silently -> the caplog assertion reddens."""
    raw = {"pref": {"system": {"agent": "goose"}}, "system": {"cache": "/c"}}
    with caplog.at_level(logging.WARNING):
        out = refuse_pref_table(raw, level=level, path=tmp_path / "f.yaml")
    assert "pref" not in out
    assert "system" in out              # untouched
    assert raw["pref"]                  # never mutates the input
    assert "workset or box settings file" in caplog.text
    assert "bounds the resolution recursion" in caplog.text


def test_refuse_pref_table_is_a_noop_without_a_pref_table():
    raw = {"system": {"cache": "/c"}}
    assert refuse_pref_table(raw, level="system", path=None) is raw


# ---------------------------------------------------------------------------
# Read helpers (what agent selection uses in P7)
# ---------------------------------------------------------------------------

def test_pref_value_last_wins():
    prefs = [req("system.agent", "a", "workset"), req("system.agent", "b", "box")]
    assert pref_value(prefs, "system.agent") == "b"
    assert pref_value(prefs, "nope") is None
    assert pref_request_for(prefs, "system.agent").level == "box"


def test_allowlist_constant_is_the_specced_pair():
    assert ALLOWLIST == ("system.agent", "agent.*.**")


# ---------------------------------------------------------------------------
# END-TO-END: the CLI suppression spelling actually removes the mount
# ---------------------------------------------------------------------------

class TestSuppressionEndToEnd:
    """MUST-1(c) — §2h's suppression channel must have a WORKING CLI spelling.

    ⚑ This is the test the Editor required: `set --null <pref>` written through
    the real config writer, then read back through the real launch pipeline, and
    the agent's declared mount must be GONE. Before the `--null` flag existed the
    natural spelling wrote the STRING "null", which installs a *value* rather
    than a suppression — the mount survived and §2h's only suppression channel
    had no CLI spelling at all.

    ⚑ THE SUPPRESSION IS NOW SPELLED AT THE CATEGORY (2026-08-08c). ``common`` is
    a TERMINAL dest-keyed key, so `agent.claude.common` is the whole key and the
    destinations are DATA inside its value; there is no `…common.plugins` pref to
    write. The declaration's emitted key is `<scope>.common.<box_dest>`, which is
    what the mount assertions below name.
    """

    #: The key ``snapshot_category_entries`` stamps on the declared entry — the
    #: category joined to its DESTINATION, canonicalized to the guest home (R-11).
    MOUNT_KEY = "agent.claude.common./home/agent/.claude/plugins"

    def _entries(self, tmp_path, box_file):
        from kanibako.settings.settings_launch import (
            build_launch_snapshot,
            snapshot_category_entries,
        )
        from kanibako.settings.settings_resolve import ResolveCtx

        ctx = ResolveCtx(
            agent_name="claude", workset_name=None,
            host_home="/home/host", xdg={"XDG_DATA_HOME": "/data"},
        )
        snap = build_launch_snapshot(
            agent_name="claude", ctx=ctx,
            system_path=None, agent_path=None,
            workset_path=None, box_path=box_file,
            default_categories={
                "agent.claude.common": {"~/.claude/plugins": ("/host/plugins",)},
            },
            valid_agents=AGENTS,
        )
        return snap, snapshot_category_entries(
            snap, active_agent="claude", box_ctx=ctx,
        )

    def test_the_declared_mount_is_present_without_a_request(self, tmp_path):
        box = tmp_path / "settings.yaml"
        box.write_text("box: {}\n")
        _snap, entries = self._entries(tmp_path, box)
        assert any(e.key == self.MOUNT_KEY for e in entries)

    def test_set_null_through_the_real_writer_removes_the_mount(self, tmp_path):
        from kanibako.settings.config_keys import ConfigLevel
        from kanibako.settings.config_interface import (
            parse_config_arg,
            set_config_value,
        )

        box = tmp_path / "settings.yaml"
        # Exactly what the CLI does for `box set --null <key>`.
        action, key, value = parse_config_arg(
            "pref.agent.claude.common", set_null=True,
        )
        msg = set_config_value(
            key, value, config_path=box, command_scope=ConfigLevel.box,
        )
        assert msg.startswith("Set")

        snap, entries = self._entries(tmp_path, box)
        # THE MOUNT IS GONE.
        assert not any(e.key == self.MOUNT_KEY for e in entries)
        assert not any(e.category == "common" for e in entries)
        # ...and the REQUEST is still visible, so --effective can explain why.
        assert dict.__getitem__(
            snap["pref"]["agent"]["claude"], "common",
        ) is None

    def test_the_string_null_does_NOT_suppress(self, tmp_path):
        """The trap this flag exists to close: a verbatim "null" is a VALUE.

        It is refused at set time now (a scalar at a bind-shaped target), which
        is the honest outcome — the user is told the spelling that works instead
        of getting a config that quietly does nothing.
        """
        from kanibako.settings.config_interface import set_config_value
        from kanibako.settings.config_keys import ConfigLevel

        box = tmp_path / "settings.yaml"
        msg = set_config_value(
            "pref.agent.claude.common", "null",
            config_path=box, command_scope=ConfigLevel.box,
        )
        assert msg.startswith("Error:")
        assert "--null" in msg
        assert not box.exists()


# ---------------------------------------------------------------------------
# Editor review — discovery: union, laziness, caching, is-None, failure
# ---------------------------------------------------------------------------

class TestPluginDeclaredAgentLeaves:
    """SHOULD-1 — §0: agent specifics are PLUGIN-declared."""

    def test_a_plugin_declared_key_validates(self):
        """goose declares `provider` via setting_descriptors(); before the union
        `agent.goose.provider` was REFUSED as undeclared."""
        from kanibako.settings.settings_prefs import default_valid_agents

        agents = default_valid_agents()
        if "provider" not in agents.leaves:      # no goose plugin installed here
            pytest.skip("goose plugin not installed in this environment")
        assert validate_pref(
            req("agent.goose.provider", level="box"), valid_agents=agents,
        ) is None

    def test_the_union_does_not_widen_the_core_contract(self):
        agents = AgentNames({"claude"}, leaves={"provider"})
        assert key_reason("agent.claude.provider", valid_agents=agents) is None
        assert key_reason("agent.claude.notakey", valid_agents=agents) is not None


class TestDiscoveryIsLazyCachedAndHonest:
    def test_discovery_is_NOT_reached_without_an_agent_target(self, monkeypatch):
        """SHOULD-3 — the docstring's 'lazy' claim enforced by the call site."""
        import kanibako.settings.settings_prefs as sp

        sp.reset_discovery_cache()
        calls = []
        monkeypatch.setattr(
            sp, "default_valid_agents",
            lambda: calls.append(1) or AgentNames(()),
        )
        sp.apply_prefs([req("system.agent", "goose", "box")], valid_agents=None)
        assert calls == []

    def test_discovery_IS_reached_for_an_agent_target(self, monkeypatch):
        import kanibako.settings.settings_prefs as sp

        sp.reset_discovery_cache()
        calls = []
        monkeypatch.setattr(
            sp, "default_valid_agents",
            lambda: calls.append(1) or AgentNames({"claude"}),
        )
        sp.apply_prefs([req("agent.claude.model", "opus", "box")], valid_agents=None)
        assert calls == [1]

    def test_discovery_is_cached_across_calls(self, monkeypatch):
        import kanibako.settings.settings_prefs as sp

        sp.reset_discovery_cache()
        calls = []

        def fake_discover():
            calls.append(1)
            return {}

        monkeypatch.setattr("kanibako.targets.discover_targets", fake_discover)
        sp.default_valid_agents()
        sp.default_valid_agents()
        sp.default_valid_agents()
        assert calls == [1]
        sp.reset_discovery_cache()
        sp.default_valid_agents()
        assert calls == [1, 1]

    def test_an_EMPTY_valid_agents_is_honoured_not_re_discovered(self, monkeypatch):
        """SHOULD-4 — `is None`, not falsy. An empty AgentNames is falsy, and a
        truthiness test would silently discard a caller's deliberate empty set."""
        import kanibako.settings.settings_prefs as sp

        sp.reset_discovery_cache()
        calls = []
        monkeypatch.setattr(
            sp, "default_valid_agents",
            lambda: calls.append(1) or AgentNames({"claude"}),
        )
        empty = AgentNames(())
        assert not empty                      # it really is falsy
        with pytest.raises(SettingsError):    # claude is NOT valid in an empty set
            sp.apply_prefs(
                [req("agent.claude.model", "opus", "box")], valid_agents=empty,
            )
        assert calls == []                    # and discovery was never consulted

    def test_a_discovery_FAILURE_gets_its_own_message(self, monkeypatch):
        """SHOULD-5 — never blame the user's spelling for an environment fault."""
        import kanibako.settings.settings_prefs as sp

        sp.reset_discovery_cache()

        def boom():
            raise RuntimeError("plugin dir unreadable")

        monkeypatch.setattr("kanibako.targets.discover_targets", boom)
        agents = sp.default_valid_agents()
        assert agents.discovery_failed
        r = allowlist_reason("agent.claude.model", valid_agents=agents)
        assert r is not None
        assert "DISCOVERY FAILED" in r
        assert "environment fault" in r
        assert "is not a valid agent" not in r
        sp.reset_discovery_cache()


class TestPrefLegalLevelsIsWired:
    """SHOULD-6 — a constant nobody reads is a contract nobody honours."""

    def test_a_hand_built_request_at_an_illegal_level_is_refused(self):
        """`agent` level with a `system.agent` target passes BOTH the structural
        filter (system resolves before agent) and the allowlist, so it reaches
        the level guard — which is the only thing standing between a
        hand-constructed request and the recursion bound."""
        with pytest.raises(SettingsError) as exc:
            apply_prefs(
                [req("system.agent", "goose", level="agent")],
                valid_agents=AGENTS,
            )
        assert "legal only at workset / box" in str(exc.value)

    def test_the_structural_filter_catches_the_ordinary_illegal_levels_first(self):
        """base/system levels are refused earlier, by the structural tier — the
        level guard is the backstop, not the primary defence."""
        with pytest.raises(SettingsError) as exc:
            apply_prefs(
                [req("system.agent", "goose", level="system")],
                valid_agents=AGENTS,
            )
        assert "structural tier" in str(exc.value)
