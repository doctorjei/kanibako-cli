"""Unit tests for the copy-once-at-init seed resolver (pure, no I/O)."""

from __future__ import annotations

import pytest

from kanibako.settings_resolve import (
    LevelView,
    ResolveCtx,
    SettingsError,
    expand_expr,
    resolve_value,
    _Unset,
)
from kanibako.settings_seeds import is_seed_key, resolve_seeds

HOST_HOME = "/home/u"


def make_ctx(
    *,
    crab_name: str | None = "claude",
    workset_name: str | None = "myws",
    host_home: str = HOST_HOME,
    xdg: dict[str, str] | None = None,
) -> ResolveCtx:
    return ResolveCtx(
        crab_name=crab_name,
        workset_name=workset_name,
        host_home=host_home,
        xdg=xdg if xdg is not None else {"XDG_DATA_HOME": "/data"},
    )


def make_lookup(levels: list[LevelView], ctx: ResolveCtx):
    """A lookup that resolves @-refs against *levels* (host-space)."""

    def lookup(ref: str, chain: tuple[str, ...]) -> str:
        rv = resolve_value(ref, levels=levels, ctx=ctx, lookup=lookup)
        if isinstance(rv, _Unset):
            raise SettingsError(f"Unknown @-reference: {ref}")
        return expand_expr(
            rv.value, space="host", ctx=ctx, lookup=lookup, chain=chain,
        )

    return lookup


def _resolve(levels, ctx):
    lookup = make_lookup(levels, ctx)
    return resolve_seeds(levels=levels, ctx=ctx, lookup=lookup)


# ---------------------------------------------------------------------------
# Basic single-seed cases
# ---------------------------------------------------------------------------


class TestSingleSeed:
    def test_crab_scope_seed(self):
        ctx = make_ctx()
        levels = [
            LevelView("box", {}),
            LevelView("workset", {}),
            LevelView("crab", {"crab.path.seeded.shell": "/tmpl/std:~/"}),
            LevelView("system", {}),
        ]
        seeds = _resolve(levels, ctx)
        assert len(seeds) == 1
        s = seeds[0]
        assert s.host_src == "/tmpl/std"
        # Whatever expand_expr("~/", space="guest") yields — guest home + "/".
        assert s.guest_dest == expand_expr("~/", space="guest", ctx=ctx, lookup=make_lookup(levels, ctx))
        assert s.guest_dest == "/home/agent/"
        assert s.scope == "crab"
        assert s.name == "shell"

    def test_empty_config_returns_empty(self):
        ctx = make_ctx()
        levels = [LevelView("box", {}), LevelView("system", {})]
        assert _resolve(levels, ctx) == []


# ---------------------------------------------------------------------------
# Expansion: ~ per space and @-refs
# ---------------------------------------------------------------------------


class TestExpansion:
    def test_tilde_expands_per_space(self):
        ctx = make_ctx(host_home="/home/u")
        levels = [LevelView("box", {"box.path.seeded.h": "~/t:~/x"})]
        seeds = _resolve(levels, ctx)
        assert len(seeds) == 1
        assert seeds[0].host_src == "/home/u/t"
        assert seeds[0].guest_dest == "/home/agent/x"

    def test_ref_in_host_src(self):
        """An @-ref in host_src resolves via lookup against the levels."""
        ctx = make_ctx(crab_name="claude")
        levels = [
            LevelView(
                "box",
                {
                    "box.path.seeded.t": "@system.path.data/templates/$CRAB/standard:~/",
                },
            ),
            LevelView("system", {}, defaults={"system.path.data": "/data"}),
        ]
        seeds = _resolve(levels, ctx)
        assert len(seeds) == 1
        assert seeds[0].host_src == "/data/templates/claude/standard"
        assert seeds[0].guest_dest == "/home/agent/"


# ---------------------------------------------------------------------------
# Accumulation and precedence
# ---------------------------------------------------------------------------


class TestAccumulationAndPrecedence:
    def test_two_keys_accumulate_in_apply_order(self):
        """Distinct seeds at different scopes accumulate; system before crab."""
        ctx = make_ctx()
        levels = [
            LevelView("box", {}),
            LevelView("workset", {}),
            LevelView("crab", {"crab.path.seeded.c": "/hc:/gc"}),
            LevelView("system", {"system.path.seeded.s": "/hs:/gs"}),
        ]
        seeds = _resolve(levels, ctx)
        assert len(seeds) == 2
        # Apply order: system scope first, crab scope last.
        assert seeds[0].guest_dest == "/gs"  # system.seeded.s
        assert seeds[1].guest_dest == "/gc"  # crab.seeded.c

    def test_three_scopes_apply_order(self):
        ctx = make_ctx()
        levels = [
            LevelView("box", {}),
            LevelView("workset", {"workset.path.seeded.w": "/hw:/gw"}),
            LevelView("crab", {"crab.path.seeded.c": "/hc:/gc"}),
            LevelView("system", {"system.path.seeded.s": "/hs:/gs"}),
        ]
        seeds = _resolve(levels, ctx)
        assert [s.guest_dest for s in seeds] == ["/gs", "/gc", "/gw"]

    def test_same_key_most_specific_wins(self):
        """system.path.seeded.foo set at system AND box → box value wins."""
        ctx = make_ctx()
        levels = [
            LevelView("box", {"system.path.seeded.foo": "/box/src:/g"}),
            LevelView("workset", {}),
            LevelView("crab", {}),
            LevelView("system", {"system.path.seeded.foo": "/sys/src:/g"}),
        ]
        seeds = _resolve(levels, ctx)
        assert len(seeds) == 1
        assert seeds[0].host_src == "/box/src"


# ---------------------------------------------------------------------------
# Suppression and disable sentinel
# ---------------------------------------------------------------------------


class TestSuppression:
    def test_suppression_terminal_empty(self):
        """Box sets a system-scoped key to '' → suppressed; sibling survives."""
        ctx = make_ctx()
        levels = [
            LevelView("box", {"system.path.seeded.foo": ""}),
            LevelView("workset", {}),
            LevelView("crab", {}),
            LevelView(
                "system",
                {
                    "system.path.seeded.foo": "/sys/foo:/g/foo",
                    "system.path.seeded.bar": "/sys/bar:/g/bar",
                },
            ),
        ]
        seeds = _resolve(levels, ctx)
        assert len(seeds) == 1
        assert seeds[0].guest_dest == "/g/bar"

    def test_empty_sentinel_disables(self):
        """The literal 'empty' value disables a seed."""
        ctx = make_ctx()
        levels = [
            LevelView("box", {"box.path.seeded.x": "empty"}),
            LevelView("crab", {"crab.path.seeded.y": "/hy:/gy"}),
        ]
        seeds = _resolve(levels, ctx)
        assert len(seeds) == 1
        assert seeds[0].name == "y"


# ---------------------------------------------------------------------------
# Discovery includes defaults
# ---------------------------------------------------------------------------


class TestDiscovery:
    def test_default_only_seed_is_discovered(self):
        """A seed present only in a level's .defaults is emitted."""
        ctx = make_ctx()
        levels = [
            LevelView("box", {}),
            LevelView(
                "crab",
                {},
                defaults={"crab.path.seeded.cfg": "/host/cfg:/g/cfg"},
            ),
            LevelView("system", {}),
        ]
        seeds = _resolve(levels, ctx)
        assert len(seeds) == 1
        assert seeds[0].host_src == "/host/cfg"
        assert seeds[0].guest_dest == "/g/cfg"


# ---------------------------------------------------------------------------
# Errors and edge cases
# ---------------------------------------------------------------------------


class TestErrors:
    def test_missing_colon_raises_naming_key(self):
        ctx = make_ctx()
        levels = [LevelView("box", {"box.path.seeded.bad": "/just/a/path"})]
        with pytest.raises(SettingsError) as exc:
            _resolve(levels, ctx)
        assert "box.path.seeded.bad" in str(exc.value)

    def test_escaped_colon_survives(self):
        ctx = make_ctx()
        levels = [LevelView("box", {"box.path.seeded.c": "/a\\:b:/guest"})]
        seeds = _resolve(levels, ctx)
        assert seeds[0].host_src == "/a:b"
        assert seeds[0].guest_dest == "/guest"

    def test_name_with_dots(self):
        ctx = make_ctx()
        levels = [LevelView("box", {"box.path.seeded.a.b.c": "/h:/g"})]
        seeds = _resolve(levels, ctx)
        assert len(seeds) == 1
        assert seeds[0].name == "a.b.c"


# ---------------------------------------------------------------------------
# Ordering determinism within a scope
# ---------------------------------------------------------------------------


class TestOrdering:
    def test_within_scope_ordered_by_name(self):
        ctx = make_ctx()
        levels = [
            LevelView(
                "box",
                {
                    "box.path.seeded.z": "/hz:/gz",
                    "box.path.seeded.a": "/ha:/ga",
                    "box.path.seeded.m": "/hm:/gm",
                },
            ),
        ]
        seeds = _resolve(levels, ctx)
        assert [s.guest_dest for s in seeds] == ["/ga", "/gm", "/gz"]


# ---------------------------------------------------------------------------
# is_seed_key
# ---------------------------------------------------------------------------


class TestIsSeedKey:
    def test_true_for_each_scope(self):
        assert is_seed_key("system.path.seeded.foo")
        assert is_seed_key("crab.path.seeded.bar")
        assert is_seed_key("workset.path.seeded.x")
        assert is_seed_key("box.path.seeded.y")
        # Dotted name is allowed (greedy remainder).
        assert is_seed_key("system.path.seeded.a.b.c")

    def test_false_for_non_seed_keys(self):
        assert not is_seed_key("system.path.data")
        assert not is_seed_key("crab.model")
        assert not is_seed_key("box.image")
        assert not is_seed_key("nope.path.seeded.foo")
        assert not is_seed_key("system.path.share_rw.foo")
        # Missing the trailing name.
        assert not is_seed_key("system.path.seeded")
