"""Unit tests for the unified scope-category resolver (pure, no I/O)."""

from __future__ import annotations

import pytest

from kanibako.settings_categories import (
    COPY,
    ENV,
    MOUNT,
    CategoryEntry,
    is_category_key,
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
            [LevelView("box", {"box.bindings.ro.docs": "/h/docs:/g/docs"})], ctx
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
        e = _one([LevelView("box", {"box.bindings.rw.work": "/h/w:~/w"})], ctx)
        assert e.category == "bindings.rw"
        assert e.host_src == "/h/w"
        assert e.box_dest == "/home/agent/w"
        assert e.delivery == MOUNT
        assert e.options == "Z,U"

    def test_caches_is_mount_zu(self):
        ctx = make_ctx()
        e = _one([LevelView("agent", {"agent.caches.tweak": "/h/c:~/.cache/x"})], ctx)
        assert e.category == "caches"
        assert e.delivery == MOUNT
        assert e.options == "Z,U"
        assert e.host_src == "/h/c"
        assert e.box_dest == "/home/agent/.cache/x"

    def test_shared_is_mount_zu(self):
        ctx = make_ctx()
        e = _one([LevelView("workset", {"workset.shared.team": "/h/s:~/shared"})], ctx)
        assert e.category == "shared"
        assert e.delivery == MOUNT
        assert e.options == "Z,U"

    def test_seeded_is_copy(self):
        ctx = make_ctx()
        e = _one([LevelView("agent", {"agent.seeded.shell": "/tmpl:~/"})], ctx)
        assert e.category == "seeded"
        assert e.delivery == COPY
        assert e.options == ""
        assert e.host_src == "/tmpl"
        assert e.box_dest == "/home/agent/"

    def test_synced_is_copy(self):
        ctx = make_ctx()
        e = _one(
            [LevelView("agent", {"agent.synced.creds": "~/.claude/c:~/.claude/c"})],
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
# masks list shape
# ---------------------------------------------------------------------------


class TestMasks:
    def test_multiple_masks_from_comma_list(self):
        ctx = make_ctx()
        entries = _resolve(
            [LevelView("box", {"box.masks": "~/workspace/vault, /secret, ~/cache"})],
            ctx,
        )
        assert [e.box_dest for e in entries] == [
            "/home/agent/workspace/vault",
            "/secret",
            "/home/agent/cache",
        ]
        assert all(e.category == "masks" and e.host_src is None for e in entries)

    def test_mask_escaped_comma_kept(self):
        ctx = make_ctx()
        entries = _resolve([LevelView("box", {"box.masks": r"/a\,b"})], ctx)
        assert [e.box_dest for e in entries] == ["/a,b"]

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
                    "box.seeded.s": "/h/s:~/s",
                    "box.synced.y": "/h/y:~/y",
                    "box.bindings.rw.b": "/h/b:~/b",
                    "box.caches.c": "/h/c:~/c",
                    "box.shared.h": "/h/h:~/h",
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
            LevelView("box", {"box.bindings.rw.b": "/hb:/gb"}),
            LevelView("workset", {"workset.bindings.rw.w": "/hw:/gw"}),
            LevelView("agent", {"agent.bindings.rw.a": "/ha:/ga"}),
            LevelView("system", {"system.bindings.rw.s": "/hs:/gs"}),
        ]
        dests = [e.box_dest for e in _resolve(levels, ctx)]
        assert dests == ["/gs", "/ga", "/gw", "/gb"]

    def test_within_scope_ordered_by_category_then_name(self):
        ctx = make_ctx()
        levels = [
            LevelView(
                "box",
                {
                    "box.bindings.rw.z": "/hz:/gz",
                    "box.bindings.rw.a": "/ha:/ga",
                    "box.bindings.ro.m": "/hm:/gm",
                    "box.caches.k": "/hk:/gk",
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
            LevelView("box", {"system.bindings.rw.foo": "/box:/g"}),
            LevelView("system", {"system.bindings.rw.foo": "/sys:/g"}),
        ]
        assert _one(levels, ctx).host_src == "/box"

    def test_terminal_empty_suppresses_binding(self):
        ctx = make_ctx()
        levels = [
            LevelView("box", {"system.bindings.rw.foo": ""}),
            LevelView(
                "system",
                {
                    "system.bindings.rw.foo": "/sf:/gf",
                    "system.bindings.rw.bar": "/sb:/gb",
                },
            ),
        ]
        assert [e.box_dest for e in _resolve(levels, ctx)] == ["/gb"]

    def test_empty_sentinel_disables_copy(self):
        ctx = make_ctx()
        levels = [
            LevelView("box", {"box.seeded.x": "empty"}),
            LevelView("box", {"box.seeded.y": "/hy:/gy"}),
        ]
        # box.seeded.x is disabled; only y survives.
        names = [e.name for e in _resolve(levels, ctx)]
        assert names == ["y"]

    def test_empty_sentinel_does_NOT_disable_mount(self):
        # "empty" is only a sentinel for COPY categories; a binding value of
        # "empty" with no colon is just a malformed bind -> error.
        ctx = make_ctx()
        levels = [LevelView("box", {"box.bindings.rw.x": "empty"})]
        with pytest.raises(SettingsError):
            _resolve(levels, ctx)

    def test_default_only_category_is_discovered(self):
        ctx = make_ctx()
        levels = [
            LevelView("box", {}),
            LevelView(
                "agent", {}, defaults={"agent.bindings.ro.cfg": "/h/cfg:/g/cfg"}
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
            LevelView("agent", {"agent.bindings.rw.plugins": "plugins:~/.claude/plugins"}),
            LevelView("system", {}, defaults={"system.agents": "/data/agents"}),
        ]
        scope_roots = {"agent.bindings.rw": "@system.agents/$AGENT/share"}
        e = _one(levels, ctx, scope_roots=scope_roots)
        assert e.host_src == "/data/agents/claude/share/plugins"

    def test_absolute_host_src_not_joined(self):
        ctx = make_ctx(agent_name="claude")
        levels = [LevelView("agent", {"agent.bindings.rw.x": "/abs:~/x"})]
        scope_roots = {"agent.bindings.rw": "/root"}
        assert _one(levels, ctx, scope_roots=scope_roots).host_src == "/abs"

    def test_caches_group_root(self):
        ctx = make_ctx()
        levels = [LevelView("agent", {"agent.caches.c": "rel:~/c"})]
        e = _one(levels, ctx, scope_roots={"agent.caches": "/croot"})
        assert e.host_src == "/croot/rel"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class TestErrors:
    def test_bind_missing_colon_raises_naming_key(self):
        ctx = make_ctx()
        levels = [LevelView("box", {"box.bindings.rw.bad": "/just/a/path"})]
        with pytest.raises(SettingsError) as exc:
            _resolve(levels, ctx)
        assert "box.bindings.rw.bad" in str(exc.value)

    def test_escaped_colon_survives(self):
        ctx = make_ctx()
        e = _one([LevelView("box", {"box.bindings.rw.c": "/a\\:b:/g"})], ctx)
        assert e.host_src == "/a:b"
        assert e.box_dest == "/g"

    def test_name_with_dots(self):
        ctx = make_ctx()
        e = _one([LevelView("box", {"box.bindings.ro.a.b.c": "/h:/g"})], ctx)
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
