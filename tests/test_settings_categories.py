"""Unit tests for the unified scope-category resolver (pure, no I/O)."""

from __future__ import annotations

import pytest

from kanibako.errors import ConfigError
from kanibako.settings_categories import (
    COPY,
    ENV,
    MOUNT,
    CategoryEntry,
    is_category_key,
    reconcile_categories,
    resolve_categories,
)
from kanibako.settings_resolve import (
    LevelView,
    ResolveCtx,
    SettingsError,
    _Unset,
    expand_expr,
    resolve_value,
)

HOST_HOME = "/home/u"


def make_ctx(
    *,
    agent_name: str | None = "claude",
    workset_name: str | None = "myws",
    host_home: str = HOST_HOME,
    xdg: dict[str, str] | None = None,
) -> ResolveCtx:
    return ResolveCtx(
        agent_name=agent_name,
        workset_name=workset_name,
        host_home=host_home,
        xdg=xdg if xdg is not None else {"XDG_DATA_HOME": "/data"},
    )


def make_lookup(levels: list[LevelView], ctx: ResolveCtx):
    def lookup(ref: str, chain: tuple[str, ...]) -> str:
        rv = resolve_value(ref, levels=levels, ctx=ctx, lookup=lookup)
        if isinstance(rv, _Unset):
            raise SettingsError(f"Unknown @-reference: {ref}")
        return expand_expr(rv.value, space="host", ctx=ctx, lookup=lookup, chain=chain)

    return lookup


def _resolve(levels, ctx, scope_roots=None) -> list[CategoryEntry]:
    lookup = make_lookup(levels, ctx)
    return resolve_categories(
        levels=levels, ctx=ctx, lookup=lookup, scope_roots=scope_roots
    )


def _one(levels, ctx, **kw) -> CategoryEntry:
    entries = _resolve(levels, ctx, **kw)
    assert len(entries) == 1, entries
    return entries[0]


# ---------------------------------------------------------------------------
# Each category parses to the right CategoryEntry shape + delivery tag.
# ---------------------------------------------------------------------------


class TestEachCategoryShape:
    def test_bindings_ro_is_mount_ro(self):
        ctx = make_ctx()
        e = _one(
            [LevelView("box", {"box.bindings.ro.docs": ["/h/docs", "/g/docs"]})], ctx
        )
        assert e.category == "bindings.ro"
        assert e.scope == "box"
        assert e.host_src == "/h/docs"
        assert e.box_dest == "/g/docs"
        assert e.delivery == MOUNT
        assert e.options == "ro"
        assert e.name == "docs"

    def test_bindings_rw_is_mount_zu(self):
        ctx = make_ctx()
        e = _one([LevelView("box", {"box.bindings.rw.work": ["/h/w", "~/w"]})], ctx)
        assert e.category == "bindings.rw"
        assert e.host_src == "/h/w"
        assert e.box_dest == "/home/agent/w"
        assert e.delivery == MOUNT
        assert e.options == "Z,U"

    def test_caches_is_mount_zu(self):
        ctx = make_ctx()
        e = _one(
            [LevelView("agent", {"agent.caches.tweak": ["/h/c", "~/.cache/x"]})], ctx
        )
        assert e.category == "caches"
        assert e.delivery == MOUNT
        assert e.options == "Z,U"
        assert e.host_src == "/h/c"
        assert e.box_dest == "/home/agent/.cache/x"

    def test_shared_is_mount_zu(self):
        ctx = make_ctx()
        e = _one(
            [LevelView("workset", {"workset.shared.team": ["/h/s", "~/shared"]})], ctx
        )
        assert e.category == "shared"
        assert e.delivery == MOUNT
        assert e.options == "Z,U"

    def test_seeded_is_copy(self):
        ctx = make_ctx()
        e = _one([LevelView("agent", {"agent.seeded.shell": ["/tmpl", "~/"]})], ctx)
        assert e.category == "seeded"
        assert e.delivery == COPY
        assert e.options == ""
        assert e.host_src == "/tmpl"
        assert e.box_dest == "/home/agent/"

    def test_synced_is_copy(self):
        ctx = make_ctx()
        e = _one(
            [LevelView("agent", {"agent.synced.creds": ["~/.claude/c", "~/.claude/c"]})],
            ctx,
        )
        assert e.category == "synced"
        assert e.delivery == COPY
        assert e.options == ""
        assert e.host_src == "/home/u/.claude/c"
        assert e.box_dest == "/home/agent/.claude/c"

    def test_masks_is_mount_no_source(self):
        ctx = make_ctx()
        e = _one([LevelView("box", {"box.masks": "~/workspace/vault"})], ctx)
        assert e.category == "masks"
        assert e.delivery == MOUNT
        assert e.host_src is None
        assert e.box_dest == "/home/agent/workspace/vault"
        assert e.options == "ro"

    def test_env_is_env_value_in_options(self):
        ctx = make_ctx()
        e = _one([LevelView("box", {"box.env.FOO": "bar"})], ctx)
        assert e.category == "env"
        assert e.delivery == ENV
        assert e.host_src is None
        assert e.box_dest == "FOO"   # the VAR name
        assert e.options == "bar"    # the VALUE
        assert e.name == "FOO"


# ---------------------------------------------------------------------------
# Structured representation (spec §2a): bind values are 2-/3-element pairs/tuples,
# never colon-strings. The 3rd element is the per-entry options override.
# ---------------------------------------------------------------------------


class TestStructuredRepresentation:
    def test_two_tuple_unpacks_host_and_dest(self):
        ctx = make_ctx()
        e = _one([LevelView("box", {"box.bindings.rw.home": ["/host/home", "~/"]})], ctx)
        assert e.host_src == "/host/home"
        assert e.box_dest == "/home/agent/"

    def test_two_tuple_as_python_tuple(self):
        # A Python tuple leaf (as well as a YAML list) unpacks identically.
        ctx = make_ctx()
        e = _one([LevelView("box", {"box.bindings.ro.x": ("/h/x", "/g/x")})], ctx)
        assert e.host_src == "/h/x"
        assert e.box_dest == "/g/x"

    def test_three_tuple_third_element_is_captured(self):
        # The optional 3rd element (per-entry mount-options override) is captured
        # by the structural unpacker. P2 captures it (no crash on the 3-element
        # form); P3 threads it into the entry's mount options.
        from kanibako.settings_resolve import unpack_bind

        assert unpack_bind(["/h/sock", "~/helper.sock", "z"]) == (
            "/h/sock",
            "~/helper.sock",
            "z",
        )
        assert unpack_bind(("/h/sock", "~/helper.sock", "")) == (
            "/h/sock",
            "~/helper.sock",
            "",
        )

    def test_three_tuple_resolves_through_category_path(self):
        # A 3-element binding value resolves end-to-end (host/dest expanded) and the
        # explicit options slot OVERRIDES the category default (P3 threading).
        ctx = make_ctx()
        e = _one(
            [LevelView("box", {"box.bindings.rw.helper_sock": ["/h/s", "~/helper.sock", "z"]})],
            ctx,
        )
        assert e.host_src == "/h/s"
        assert e.box_dest == "/home/agent/helper.sock"
        assert e.options == "z"  # explicit override beats the rw default Z,U


# ---------------------------------------------------------------------------
# P3: the per-entry options override (3rd element) threads to CategoryEntry.options.
# ---------------------------------------------------------------------------


class TestPerEntryOptionsOverride:
    def test_explicit_override_wins_over_rw_default(self):
        # [host, dest, "z"] -> options == "z" (not the rw default "Z,U").
        ctx = make_ctx()
        e = _one([LevelView("box", {"box.bindings.rw.s": ["/h/s", "/g/s", "z"]})], ctx)
        assert e.options == "z"

    def test_explicit_override_wins_over_ro_default(self):
        # An explicit override beats the ro default "ro" too.
        ctx = make_ctx()
        e = _one([LevelView("box", {"box.bindings.ro.s": ["/h/s", "/g/s", "rw,Z"]})], ctx)
        assert e.options == "rw,Z"

    def test_explicit_empty_override_means_no_relabel(self):
        # [host, dest, ""] resolves to "" — an explicit empty options (live socket:
        # no Z,U relabel), DISTINCT from the 2-element default-fallback case.
        ctx = make_ctx()
        e = _one([LevelView("box", {"box.bindings.rw.sock": ["/h/s", "/g/s", ""]})], ctx)
        assert e.options == ""

    def test_two_element_falls_back_to_rw_default(self):
        # A 2-element rw entry (no override) keeps the category default "Z,U".
        ctx = make_ctx()
        e = _one([LevelView("box", {"box.bindings.rw.s": ["/h/s", "/g/s"]})], ctx)
        assert e.options == "Z,U"

    def test_two_element_falls_back_to_ro_default(self):
        # A 2-element ro entry (no override) keeps the category default "ro".
        ctx = make_ctx()
        e = _one([LevelView("box", {"box.bindings.ro.s": ["/h/s", "/g/s"]})], ctx)
        assert e.options == "ro"

    def test_override_on_caches_and_shared(self):
        # The override channel is per-MOUNT-category, not just bindings.
        ctx = make_ctx()
        ec = _one([LevelView("box", {"box.caches.c": ["/h/c", "/g/c", "U"]})], ctx)
        assert ec.options == "U"
        es = _one([LevelView("box", {"box.shared.s": ["/h/s", "/g/s", ""]})], ctx)
        assert es.options == ""

    def test_copy_category_ignores_options_slot(self):
        # COPY deliveries (seeded/synced) carry no mount flags: options stays ""
        # even when a 3rd element is present (it is not a mount option there).
        ctx = make_ctx()
        e = _one([LevelView("box", {"box.seeded.s": ["/h/s", "/g/s", "z"]})], ctx)
        assert e.delivery == COPY
        assert e.options == ""

    def test_two_tuple_unpack_returns_none_for_options(self):
        from kanibako.settings_resolve import unpack_bind

        assert unpack_bind(["/h", "/g"]) == ("/h", "/g", None)

    def test_non_structured_value_raises(self):
        # A bare scalar (the old colon-string form) is no longer a valid category
        # binding value — the structured pair/tuple is load-bearing.
        ctx = make_ctx()
        levels = [LevelView("box", {"box.bindings.rw.bad": "/just/a/path"})]
        with pytest.raises(SettingsError):
            _resolve(levels, ctx)

    def test_wrong_arity_raises(self):
        from kanibako.settings_resolve import unpack_bind

        with pytest.raises(SettingsError):
            unpack_bind(["only-one"])
        with pytest.raises(SettingsError):
            unpack_bind(["a", "b", "c", "d"])


# ---------------------------------------------------------------------------
# masks list shape
# ---------------------------------------------------------------------------


class TestMasks:
    def test_multiple_masks_from_real_list(self):
        # F1 regression: a multi-element YAML masks list (spec §2a — a real
        # list[box_dest], preserved by the LOAD layer) resolves to ONE entry
        # per element, NOT a single str()-reprd-garbage entry.
        ctx = make_ctx()
        entries = _resolve(
            [LevelView("box", {"box.masks": ["~/workspace/vault", "/secret", "~/cache"]})],
            ctx,
        )
        assert [e.box_dest for e in entries] == [
            "/home/agent/workspace/vault",
            "/secret",
            "/home/agent/cache",
        ]
        assert all(e.category == "masks" and e.host_src is None for e in entries)

    def test_single_mask_scalar_is_one_entry(self):
        # A bare scalar (single-mask config / in-code default) = one element.
        ctx = make_ctx()
        entries = _resolve([LevelView("box", {"box.masks": "/a,b"})], ctx)
        assert [e.box_dest for e in entries] == ["/a,b"]

    def test_mask_list_element_with_literal_comma_kept(self):
        # A literal comma inside a list element is NOT a separator (no shim).
        ctx = make_ctx()
        entries = _resolve([LevelView("box", {"box.masks": ["/a,b", "/c"]})], ctx)
        assert [e.box_dest for e in entries] == ["/a,b", "/c"]

    def test_mask_terminal_suppression(self):
        ctx = make_ctx()
        entries = _resolve(
            [
                LevelView("box", {"system.masks": ""}),
                LevelView("system", {"system.masks": "~/x"}),
            ],
            ctx,
        )
        assert entries == []


# ---------------------------------------------------------------------------
# env value shape
# ---------------------------------------------------------------------------


class TestEnv:
    def test_env_expands_value(self):
        ctx = make_ctx(agent_name="claude")
        e = _one([LevelView("box", {"box.env.HOME_AGENT": "~/.local"})], ctx)
        assert e.box_dest == "HOME_AGENT"
        assert e.options == "/home/agent/.local"

    def test_env_dotted_var_not_matched(self):
        # env VAR names cannot contain dots, so a dotted "VAR" is not an env key.
        assert not is_category_key("box.env.A.B")

    def test_env_precedence_box_over_system(self):
        ctx = make_ctx()
        e = _one(
            [
                LevelView("box", {"system.env.K": "box"}),
                LevelView("system", {"system.env.K": "sys"}),
            ],
            ctx,
        )
        assert e.options == "box"


# ---------------------------------------------------------------------------
# COPY vs MOUNT delivery tagging across the categories
# ---------------------------------------------------------------------------


class TestDeliveryTagging:
    def test_copy_set_vs_mount_set(self):
        ctx = make_ctx()
        levels = [
            LevelView(
                "box",
                {
                    "box.seeded.s": ["/h/s", "~/s"],
                    "box.synced.y": ["/h/y", "~/y"],
                    "box.bindings.rw.b": ["/h/b", "~/b"],
                    "box.caches.c": ["/h/c", "~/c"],
                    "box.shared.h": ["/h/h", "~/h"],
                },
            ),
        ]
        by_cat = {e.category: e.delivery for e in _resolve(levels, ctx)}
        assert by_cat["seeded"] == COPY
        assert by_cat["synced"] == COPY
        assert by_cat["bindings.rw"] == MOUNT
        assert by_cat["caches"] == MOUNT
        assert by_cat["shared"] == MOUNT


# ---------------------------------------------------------------------------
# Scope apply order (system -> agent -> workset -> box, box LAST)
# ---------------------------------------------------------------------------


class TestScopeApplyOrder:
    def test_distinct_scopes_apply_order(self):
        ctx = make_ctx()
        levels = [
            LevelView("box", {"box.bindings.rw.b": ["/hb", "/gb"]}),
            LevelView("workset", {"workset.bindings.rw.w": ["/hw", "/gw"]}),
            LevelView("agent", {"agent.bindings.rw.a": ["/ha", "/ga"]}),
            LevelView("system", {"system.bindings.rw.s": ["/hs", "/gs"]}),
        ]
        dests = [e.box_dest for e in _resolve(levels, ctx)]
        assert dests == ["/gs", "/ga", "/gw", "/gb"]

    def test_within_scope_ordered_by_category_then_name(self):
        ctx = make_ctx()
        levels = [
            LevelView(
                "box",
                {
                    "box.bindings.rw.z": ["/hz", "/gz"],
                    "box.bindings.rw.a": ["/ha", "/ga"],
                    "box.bindings.ro.m": ["/hm", "/gm"],
                    "box.caches.k": ["/hk", "/gk"],
                },
            ),
        ]
        # bindings.ro < bindings.rw < caches (category asc); within → name asc.
        dests = [e.box_dest for e in _resolve(levels, ctx)]
        assert dests == ["/gm", "/ga", "/gz", "/gk"]


# ---------------------------------------------------------------------------
# Precedence / suppression / discovery (engine semantics, generalized)
# ---------------------------------------------------------------------------


class TestPrecedenceAndSuppression:
    def test_same_key_most_specific_wins(self):
        ctx = make_ctx()
        levels = [
            LevelView("box", {"system.bindings.rw.foo": ["/box", "/g"]}),
            LevelView("system", {"system.bindings.rw.foo": ["/sys", "/g"]}),
        ]
        assert _one(levels, ctx).host_src == "/box"

    def test_terminal_empty_suppresses_binding(self):
        ctx = make_ctx()
        levels = [
            LevelView("box", {"system.bindings.rw.foo": ""}),
            LevelView(
                "system",
                {
                    "system.bindings.rw.foo": ["/sf", "/gf"],
                    "system.bindings.rw.bar": ["/sb", "/gb"],
                },
            ),
        ]
        assert [e.box_dest for e in _resolve(levels, ctx)] == ["/gb"]

    def test_empty_sentinel_disables_copy(self):
        ctx = make_ctx()
        levels = [
            LevelView("box", {"box.seeded.x": "empty"}),
            LevelView("box", {"box.seeded.y": ["/hy", "/gy"]}),
        ]
        # box.seeded.x is disabled; only y survives.
        names = [e.name for e in _resolve(levels, ctx)]
        assert names == ["y"]

    def test_empty_sentinel_does_NOT_disable_mount(self):
        # "empty" is only a sentinel for COPY categories; a binding (MOUNT) value
        # of "empty" is a non-structured scalar -> not a valid pair/tuple -> error.
        ctx = make_ctx()
        levels = [LevelView("box", {"box.bindings.rw.x": "empty"})]
        with pytest.raises(SettingsError):
            _resolve(levels, ctx)

    def test_default_only_category_is_discovered(self):
        ctx = make_ctx()
        levels = [
            LevelView("box", {}),
            LevelView(
                "agent", {}, defaults={"agent.bindings.ro.cfg": ["/h/cfg", "/g/cfg"]}
            ),
        ]
        e = _one(levels, ctx)
        assert e.host_src == "/h/cfg"
        assert e.options == "ro"


# ---------------------------------------------------------------------------
# Root join (bind-shaped categories)
# ---------------------------------------------------------------------------


class TestRootJoin:
    def test_relative_host_src_joined_under_group_root(self):
        ctx = make_ctx(agent_name="claude")
        levels = [
            LevelView("agent", {"agent.bindings.rw.plugins": ["plugins", "~/.claude/plugins"]}),
            LevelView("system", {}, defaults={"system.agents": "/data/agents"}),
        ]
        scope_roots = {"agent.bindings.rw": "@system.agents/$AGENT/share"}
        e = _one(levels, ctx, scope_roots=scope_roots)
        assert e.host_src == "/data/agents/claude/share/plugins"

    def test_absolute_host_src_not_joined(self):
        ctx = make_ctx(agent_name="claude")
        levels = [LevelView("agent", {"agent.bindings.rw.x": ["/abs", "~/x"]})]
        scope_roots = {"agent.bindings.rw": "/root"}
        assert _one(levels, ctx, scope_roots=scope_roots).host_src == "/abs"

    def test_caches_group_root(self):
        ctx = make_ctx()
        levels = [LevelView("agent", {"agent.caches.c": ["rel", "~/c"]})]
        e = _one(levels, ctx, scope_roots={"agent.caches": "/croot"})
        assert e.host_src == "/croot/rel"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class TestErrors:
    def test_non_structured_bind_raises_naming_key(self):
        # A bare scalar (the old colon-string form) is no longer a valid binding
        # value; the wrapped error names the offending category key.
        ctx = make_ctx()
        levels = [LevelView("box", {"box.bindings.rw.bad": "/just/a/path"})]
        with pytest.raises(SettingsError) as exc:
            _resolve(levels, ctx)
        assert "box.bindings.rw.bad" in str(exc.value)

    def test_literal_colon_in_path_needs_no_escaping(self):
        # In the structured form a path with a literal ':' is just a list
        # element — no colon-escaping (the structured shape has no delimiter).
        ctx = make_ctx()
        e = _one([LevelView("box", {"box.bindings.rw.c": ["/a:b", "/g"]})], ctx)
        assert e.host_src == "/a:b"
        assert e.box_dest == "/g"

    def test_name_with_dots(self):
        ctx = make_ctx()
        e = _one([LevelView("box", {"box.bindings.ro.a.b.c": ["/h", "/g"]})], ctx)
        assert e.name == "a.b.c"


# ---------------------------------------------------------------------------
# is_category_key
# ---------------------------------------------------------------------------


class TestIsCategoryKey:
    def test_true_for_each_category(self):
        assert is_category_key("system.masks")
        assert is_category_key("box.bindings.ro.x")
        assert is_category_key("box.bindings.rw.x")
        assert is_category_key("agent.caches.k")
        assert is_category_key("agent.seeded.t")
        assert is_category_key("workset.shared.s")
        assert is_category_key("agent.synced.c")
        assert is_category_key("box.env.FOO")
        # Dotted name allowed for bind categories.
        assert is_category_key("system.bindings.rw.a.b.c")

    def test_false_for_non_category_keys(self):
        assert not is_category_key("system.data")
        assert not is_category_key("agent.model")
        assert not is_category_key("box.image")
        assert not is_category_key("nope.bindings.rw.x")
        assert not is_category_key("system.path.share_rw.foo")  # old shape gone
        assert not is_category_key("system.bindings.rw")        # missing name
        assert not is_category_key("box.bindings.xx.y")         # bad mode
        assert not is_category_key("box.env")                   # missing VAR


# ---------------------------------------------------------------------------
# 4b — collision / precedence resolver (reconcile_categories)
#
# Authority order (identical box_dest, later WINS): seed < cache < binding <
# shared < synced < masks. Ties within a category -> box scope wins. synced
# (COPY) vs binding (MOUNT) at the same dest -> ConfigError. MOUNTs emit
# depth-sorted (shallow first). group_auth=False suppresses synced + cred seeds.
# ---------------------------------------------------------------------------


def _reconcile(levels, ctx, *, group_auth=True, scope_roots=None):
    return reconcile_categories(
        _resolve(levels, ctx, scope_roots=scope_roots), group_auth=group_auth
    )


def _entry(category, *, box_dest, scope="box", host_src="/h", is_credential=False):
    """Build a CategoryEntry directly (bypasses parsing) for precise collisions."""
    from kanibako.settings_categories import _DELIVERY, _bind_options

    delivery = _DELIVERY[category]
    host = None if category in ("masks", "env") else host_src
    options = _bind_options(category) if delivery == MOUNT else ""
    return CategoryEntry(
        category=category,
        scope=scope,
        box_dest=box_dest,
        host_src=host,
        delivery=delivery,
        options=options,
        name=box_dest,
        is_credential=is_credential,
    )


class TestReconcileAuthorityOrder:
    def test_identical_dest_full_authority_ladder(self):
        # The authority ladder at one dest: each category beats every category
        # BELOW it. synced<->binding is a hard error so it's never co-stacked;
        # we test each rung against the rungs beneath it (excluding the
        # synced/binding pairing, which has its own error test).
        # seed < cache < binding < shared < synced < masks
        D = "/g/x"
        for top, rest in [
            ("masks", ["shared", "bindings.rw", "caches", "seeded"]),
            ("masks", ["synced", "shared", "caches", "seeded"]),
            ("synced", ["shared", "caches", "seeded"]),
            ("shared", ["bindings.rw", "caches", "seeded"]),
            ("bindings.rw", ["caches", "seeded"]),
            ("caches", ["seeded"]),
        ]:
            entries = [_entry(c, box_dest=D) for c in rest] + [
                _entry(top, box_dest=D)
            ]
            rec = reconcile_categories(entries)
            winners = rec.mounts + rec.copies
            assert len(winners) == 1, (top, winners)
            assert winners[0].category == top, top

    def test_mask_beats_synced_beats_shared_beats_binding_beats_cache_beats_seed(
        self,
    ):
        ctx = make_ctx()
        # Five categories piled on /g/x via config; mask is highest authority.
        levels = [
            LevelView(
                "box",
                {
                    "box.seeded.s": ["/h/s", "/g/x"],
                    "box.caches.c": ["/h/c", "/g/x"],
                    "box.bindings.rw.b": ["/h/b", "/g/x"],
                    "box.shared.h": ["/h/h", "/g/x"],
                    "box.masks": "/g/x",
                },
            ),
        ]
        rec = _reconcile(levels, ctx)
        winners = rec.mounts + rec.copies
        assert [w.category for w in winners] == ["masks"]

    def test_seed_beats_nothing_alone_survives(self):
        # A lone seed at a dest with no higher-authority collider survives.
        rec = reconcile_categories([_entry("seeded", box_dest="/g/only")])
        assert [w.category for w in rec.copies] == ["seeded"]
        assert rec.mounts == []


class TestReconcileTieBreak:
    def test_tie_within_category_box_scope_wins(self):
        # Two bindings at the same dest from different scopes -> box wins.
        sys_e = _entry("bindings.rw", box_dest="/g/d", scope="system", host_src="/sys")
        box_e = _entry("bindings.rw", box_dest="/g/d", scope="box", host_src="/box")
        rec = reconcile_categories([sys_e, box_e])
        assert len(rec.mounts) == 1
        assert rec.mounts[0].host_src == "/box"

    def test_tie_within_category_box_wins_regardless_of_input_order(self):
        sys_e = _entry("caches", box_dest="/g/c", scope="system", host_src="/sys")
        box_e = _entry("caches", box_dest="/g/c", scope="box", host_src="/box")
        # box listed FIRST: still wins (scope beats input order).
        rec = reconcile_categories([box_e, sys_e])
        assert rec.mounts[0].host_src == "/box"


class TestReconcileSyncedBindingError:
    def test_synced_vs_binding_same_dest_raises(self):
        synced = _entry("synced", box_dest="/g/clash")
        binding = _entry("bindings.rw", box_dest="/g/clash")
        with pytest.raises(ConfigError) as exc:
            reconcile_categories([synced, binding])
        assert "/g/clash" in str(exc.value)

    def test_synced_vs_binding_ro_same_dest_raises(self):
        synced = _entry("synced", box_dest="/g/clash")
        binding = _entry("bindings.ro", box_dest="/g/clash")
        with pytest.raises(ConfigError):
            reconcile_categories([synced, binding])

    def test_synced_and_shared_same_dest_is_not_an_error(self):
        # Only synced<->binding is the hard error; synced beats shared cleanly.
        synced = _entry("synced", box_dest="/g/ok")
        shared = _entry("shared", box_dest="/g/ok")
        rec = reconcile_categories([synced, shared])
        assert [w.category for w in (rec.mounts + rec.copies)] == ["synced"]


class TestReconcileDepthOrder:
    def test_mounts_emitted_shallow_to_deep(self):
        home = _entry("bindings.rw", box_dest="/home/agent")
        ws = _entry("bindings.rw", box_dest="/home/agent/workspace")
        vault = _entry("masks", box_dest="/home/agent/workspace/vault")
        # Input deliberately scrambled.
        rec = reconcile_categories([vault, home, ws])
        assert [m.box_dest for m in rec.mounts] == [
            "/home/agent",
            "/home/agent/workspace",
            "/home/agent/workspace/vault",
        ]

    def test_mask_inside_workspace_lands_on_top(self):
        # mask at deeper dest emits AFTER the workspace binding (last -v wins).
        ws = _entry("bindings.rw", box_dest="/home/agent/workspace")
        mask = _entry("masks", box_dest="/home/agent/workspace/vault")
        rec = reconcile_categories([mask, ws])
        assert [m.box_dest for m in rec.mounts] == [
            "/home/agent/workspace",
            "/home/agent/workspace/vault",
        ]
        assert rec.mounts[-1].category == "masks"

    def test_root_home_before_nested(self):
        root = _entry("bindings.rw", box_dest="/")
        nested = _entry("bindings.rw", box_dest="/home/agent/workspace/vault")
        rec = reconcile_categories([nested, root])
        assert [m.box_dest for m in rec.mounts] == [
            "/",
            "/home/agent/workspace/vault",
        ]


class TestReconcileGroupAuthGate:
    def test_group_auth_false_suppresses_synced(self):
        synced = _entry("synced", box_dest="/g/cred")
        binding = _entry("bindings.rw", box_dest="/g/keep")
        rec = reconcile_categories([synced, binding], group_auth=False)
        cats = [w.category for w in (rec.mounts + rec.copies)]
        assert "synced" not in cats
        assert "bindings.rw" in cats

    def test_group_auth_false_suppresses_credential_seed_only(self):
        cred_seed = _entry("seeded", box_dest="/g/cred", is_credential=True)
        plain_seed = _entry("seeded", box_dest="/g/plain", is_credential=False)
        rec = reconcile_categories([cred_seed, plain_seed], group_auth=False)
        dests = [c.box_dest for c in rec.copies]
        assert "/g/cred" not in dests
        assert "/g/plain" in dests

    def test_group_auth_true_keeps_synced_and_cred_seed(self):
        synced = _entry("synced", box_dest="/g/s")
        cred_seed = _entry("seeded", box_dest="/g/cred", is_credential=True)
        rec = reconcile_categories([synced, cred_seed], group_auth=True)
        dests = {c.box_dest for c in rec.copies}
        assert dests == {"/g/s", "/g/cred"}

    def test_gate_applied_before_collision_so_no_false_synced_binding_error(self):
        # synced suppressed by gate -> no clash with the binding at same dest.
        synced = _entry("synced", box_dest="/g/d")
        binding = _entry("bindings.rw", box_dest="/g/d")
        rec = reconcile_categories([synced, binding], group_auth=False)
        assert [m.category for m in rec.mounts] == ["bindings.rw"]


class TestReconcilePartition:
    def test_copy_mount_env_split(self):
        ctx = make_ctx()
        levels = [
            LevelView(
                "box",
                {
                    "box.seeded.s": ["/h/s", "/g/s"],
                    "box.synced.y": ["/h/y", "/g/y"],
                    "box.bindings.rw.b": ["/h/b", "/g/b"],
                    "box.masks": "/g/m",
                    "box.env.FOO": "bar",
                },
            ),
        ]
        rec = _reconcile(levels, ctx)
        assert {m.delivery for m in rec.mounts} == {MOUNT}
        assert {c.delivery for c in rec.copies} == {COPY}
        assert [e.box_dest for e in rec.envs] == ["FOO"]
        assert {e.delivery for e in rec.envs} == {ENV}

    def test_env_never_collides_with_path_dest(self):
        # An env VAR name equal to a path dest must not be treated as a collision.
        env = _entry("env", box_dest="/g/x")  # contrived VAR name
        binding = _entry("bindings.rw", box_dest="/g/x")
        rec = reconcile_categories([env, binding])
        assert len(rec.mounts) == 1
        assert len(rec.envs) == 1


# ---------------------------------------------------------------------------
# core_default_categories (step 3) — the core box mounts (home/workspace/vault)
# routed through the resolver as AGENT-level default_categories.
# ---------------------------------------------------------------------------


class _FakeProj:
    """Minimal ProjectPaths stand-in for core_default_categories."""

    def __init__(self, tmp_path, *, vault_dirs: bool):
        from pathlib import Path

        self.shell_path = Path("/host/shell")
        self.project_path = Path("/host/proj")
        self.vault_ro_path = tmp_path / "vro"
        self.vault_rw_path = tmp_path / "vrw"
        if vault_dirs:
            self.vault_ro_path.mkdir()
            self.vault_rw_path.mkdir()


class TestCoreDefaultCategories:
    """core_defaults.core_default_categories emits the structured core binds."""

    def test_home_and_workspace_structured_triples(self, tmp_path):
        from kanibako import core_defaults

        proj = _FakeProj(tmp_path, vault_dirs=True)
        binds = core_defaults.core_default_categories(None, proj, enable_vault=True)
        # home: rw bind, dest ~ , options Z,U (the structured 3-tuple's 3rd slot).
        assert binds["box.bindings.rw.home"] == ("/host/shell", "~", "Z,U")
        # workspace: rw bind, dest ~/workspace, options Z,U.
        assert binds["box.bindings.rw.workspace"] == (
            "/host/proj",
            "~/workspace",
            "Z,U",
        )
        # Every value is a structured 3-tuple (spec §2a), never a colon-string.
        for v in binds.values():
            assert isinstance(v, tuple) and len(v) == 3
            assert ":" not in v[1]

    def test_vault_keys_present_when_enabled_and_dirs_exist(self, tmp_path):
        from kanibako import core_defaults

        proj = _FakeProj(tmp_path, vault_dirs=True)
        binds = core_defaults.core_default_categories(None, proj, enable_vault=True)
        # vault_ro -> bindings.ro with ro options; vault_rw -> bindings.rw Z,U.
        assert binds["box.bindings.ro.vault"] == (
            str(proj.vault_ro_path),
            "~/vault/ro",
            "ro",
        )
        assert binds["box.bindings.rw.vault"] == (
            str(proj.vault_rw_path),
            "~/vault/rw",
            "Z,U",
        )

    def test_vault_keys_absent_when_disabled(self, tmp_path):
        from kanibako import core_defaults

        proj = _FakeProj(tmp_path, vault_dirs=True)
        binds = core_defaults.core_default_categories(None, proj, enable_vault=False)
        assert "box.bindings.ro.vault" not in binds
        assert "box.bindings.rw.vault" not in binds
        # home + workspace stay unconditional.
        assert "box.bindings.rw.home" in binds
        assert "box.bindings.rw.workspace" in binds

    def test_vault_keys_absent_when_source_missing(self, tmp_path):
        from kanibako import core_defaults

        proj = _FakeProj(tmp_path, vault_dirs=False)  # vault dirs do NOT exist
        binds = core_defaults.core_default_categories(None, proj, enable_vault=True)
        assert "box.bindings.ro.vault" not in binds
        assert "box.bindings.rw.vault" not in binds
        assert "box.bindings.rw.home" in binds
        assert "box.bindings.rw.workspace" in binds

    def test_options_flow_through_resolver_to_entry(self, tmp_path):
        """The 3rd-slot options survive resolve_categories as the entry options.

        Injected as AGENT-level defaults, the structured triples resolve to
        CategoryEntry with the per-entry options override applied (so vault_ro keeps
        ``ro`` and the rw binds keep ``Z,U``) — proving the options slot flows
        end-to-end to the emitted Mount.
        """
        from kanibako import core_defaults

        proj = _FakeProj(tmp_path, vault_dirs=True)
        defaults = core_defaults.core_default_categories(
            None, proj, enable_vault=True
        )
        ctx = make_ctx()
        levels = [
            LevelView("box", {}),
            LevelView("agent", {}, defaults=defaults),
        ]
        entries = _resolve(levels, ctx)
        by_dest = {e.box_dest: e for e in entries}
        assert by_dest["/home/agent"].options == "Z,U"
        assert by_dest["/home/agent"].category == "bindings.rw"
        assert by_dest["/home/agent/workspace"].options == "Z,U"
        assert by_dest["/home/agent/vault/ro"].options == "ro"
        assert by_dest["/home/agent/vault/ro"].category == "bindings.ro"
        assert by_dest["/home/agent/vault/rw"].options == "Z,U"

    def test_home_and_workspace_depth_order_keeps_both(self, tmp_path):
        """reconcile depth-sort keeps BOTH the nested home + workspace binds."""
        from kanibako import core_defaults

        proj = _FakeProj(tmp_path, vault_dirs=True)
        defaults = core_defaults.core_default_categories(
            None, proj, enable_vault=True
        )
        ctx = make_ctx()
        levels = [
            LevelView("box", {}),
            LevelView("agent", {}, defaults=defaults),
        ]
        rec = _reconcile(levels, ctx)
        dests = [m.box_dest for m in rec.mounts]
        # Both kept; home (shallower) emitted before workspace (deeper).
        assert "/home/agent" in dests
        assert "/home/agent/workspace" in dests
        assert dests.index("/home/agent") < dests.index("/home/agent/workspace")
