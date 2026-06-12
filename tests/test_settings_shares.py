"""Unit tests for the scoped-share resolver (pure, no I/O)."""

from __future__ import annotations

from pathlib import Path

import pytest

from kanibako.settings_resolve import (
    LevelView,
    ResolveCtx,
    SettingsError,
    expand_expr,
    resolve_value,
    _Unset,
)
from kanibako.settings_shares import is_share_key, resolve_shares

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


def _resolve(levels, ctx, scope_roots=None):
    lookup = make_lookup(levels, ctx)
    return resolve_shares(
        levels=levels, ctx=ctx, lookup=lookup, scope_roots=scope_roots,
    )


# ---------------------------------------------------------------------------
# Basic single-share cases
# ---------------------------------------------------------------------------


class TestSingleShare:
    def test_box_scope_rw(self):
        ctx = make_ctx()
        levels = [
            LevelView("box", {"box.path.share_rw.work": "/host/data:~/data"}),
            LevelView("workset", {}),
            LevelView("crab", {}),
            LevelView("system", {}),
        ]
        mounts = _resolve(levels, ctx)
        assert len(mounts) == 1
        m = mounts[0]
        assert m.source == Path("/host/data")
        assert m.destination == "/home/agent/data"
        assert m.options == "Z,U"

    def test_ro_mode_options(self):
        ctx = make_ctx()
        levels = [
            LevelView("box", {"box.path.share_ro.docs": "/host/docs:/srv/docs"}),
        ]
        mounts = _resolve(levels, ctx)
        assert len(mounts) == 1
        assert mounts[0].options == "ro"

    def test_empty_config_returns_empty(self):
        ctx = make_ctx()
        levels = [LevelView("box", {}), LevelView("system", {})]
        assert _resolve(levels, ctx) == []


# ---------------------------------------------------------------------------
# Accumulation and precedence
# ---------------------------------------------------------------------------


class TestAccumulationAndPrecedence:
    def test_two_keys_accumulate_in_apply_order(self):
        """Distinct shares at different scopes accumulate; system before box."""
        ctx = make_ctx()
        levels = [
            LevelView("box", {"box.path.share_rw.b": "/hb:/gb"}),
            LevelView("workset", {}),
            LevelView("crab", {}),
            LevelView(
                "system", {"system.path.share_rw.a": "/ha:/ga"},
            ),
        ]
        mounts = _resolve(levels, ctx)
        assert len(mounts) == 2
        # Apply order: system scope first, box scope last.
        assert mounts[0].destination == "/ga"  # system.share_rw.a
        assert mounts[1].destination == "/gb"  # box.share_rw.b

    def test_same_key_most_specific_wins(self):
        """system.path.share_rw.foo set at system AND box → box value mounts."""
        ctx = make_ctx()
        levels = [
            LevelView("box", {"system.path.share_rw.foo": "/box/src:/g"}),
            LevelView("workset", {}),
            LevelView("crab", {}),
            LevelView("system", {"system.path.share_rw.foo": "/sys/src:/g"}),
        ]
        mounts = _resolve(levels, ctx)
        assert len(mounts) == 1
        # Box value wins (the box LEVEL is most specific). Note no root for
        # box -> but key is system-scoped; without scope_roots, used as-is.
        assert mounts[0].source == Path("/box/src")

    def test_suppression_terminal_empty(self):
        """Box sets a system-scoped key to '' → suppressed; sibling survives."""
        ctx = make_ctx()
        levels = [
            LevelView("box", {"system.path.share_rw.foo": ""}),
            LevelView("workset", {}),
            LevelView("crab", {}),
            LevelView(
                "system",
                {
                    "system.path.share_rw.foo": "/sys/foo:/g/foo",
                    "system.path.share_rw.bar": "/sys/bar:/g/bar",
                },
            ),
        ]
        mounts = _resolve(levels, ctx)
        assert len(mounts) == 1
        assert mounts[0].destination == "/g/bar"


# ---------------------------------------------------------------------------
# Root join
# ---------------------------------------------------------------------------


class TestRootJoin:
    def test_crab_scope_root_join(self):
        """The Claude-relocation shape: crab share joined under crabs/$CRAB/share."""
        ctx = make_ctx(crab_name="claude")
        levels = [
            LevelView("box", {}),
            LevelView("workset", {}),
            LevelView(
                "crab",
                {"crab.path.share_rw.plugins": "plugins:~/.claude/plugins"},
            ),
            LevelView(
                "system",
                {},
                defaults={"system.path.crabs": "/data/crabs"},
            ),
        ]
        scope_roots = {"crab.path.share_rw": "@system.path.crabs/$CRAB/share"}
        mounts = _resolve(levels, ctx, scope_roots=scope_roots)
        assert len(mounts) == 1
        m = mounts[0]
        assert m.source == Path("/data/crabs/claude/share/plugins")
        assert m.destination == "/home/agent/.claude/plugins"
        assert m.options == "Z,U"

    def test_absolute_host_src_not_joined(self):
        """An absolute host_src bypasses the root even when one exists."""
        ctx = make_ctx(crab_name="claude")
        levels = [
            LevelView(
                "crab",
                {"crab.path.share_rw.x": "/abs/path:~/x"},
            ),
            LevelView("system", {}, defaults={"system.path.crabs": "/data/crabs"}),
        ]
        scope_roots = {"crab.path.share_rw": "@system.path.crabs/$CRAB/share"}
        mounts = _resolve(levels, ctx, scope_roots=scope_roots)
        assert len(mounts) == 1
        assert mounts[0].source == Path("/abs/path")

    def test_group_absent_from_scope_roots_no_join(self):
        """A relative host_src for a group with no root is used as-is."""
        ctx = make_ctx()
        levels = [
            LevelView("box", {"box.path.share_rw.x": "rel/dir:~/x"}),
        ]
        # box group not in scope_roots.
        mounts = _resolve(levels, ctx, scope_roots={"crab.path.share_rw": "/r"})
        assert mounts[0].source == Path("rel/dir")

    def test_empty_root_no_join(self):
        ctx = make_ctx()
        levels = [LevelView("crab", {"crab.path.share_rw.x": "rel:~/x"})]
        mounts = _resolve(levels, ctx, scope_roots={"crab.path.share_rw": ""})
        assert mounts[0].source == Path("rel")


# ---------------------------------------------------------------------------
# Errors and expansion edge cases
# ---------------------------------------------------------------------------


class TestErrorsAndExpansion:
    def test_missing_colon_raises_naming_key(self):
        ctx = make_ctx()
        levels = [LevelView("box", {"box.path.share_rw.bad": "/just/a/path"})]
        with pytest.raises(SettingsError) as exc:
            _resolve(levels, ctx)
        assert "box.path.share_rw.bad" in str(exc.value)

    def test_tilde_expands_per_space(self):
        ctx = make_ctx(host_home="/home/u")
        levels = [LevelView("box", {"box.path.share_rw.h": "~/x:~/y"})]
        mounts = _resolve(levels, ctx)
        assert mounts[0].source == Path("/home/u/x")
        assert mounts[0].destination == "/home/agent/y"

    def test_escaped_colon_survives(self):
        ctx = make_ctx()
        # host half contains a literal colon via \: ; the real split is the
        # second (unescaped) colon.
        levels = [
            LevelView("box", {"box.path.share_rw.c": "/a\\:b:/guest"}),
        ]
        mounts = _resolve(levels, ctx)
        assert mounts[0].source == Path("/a:b")
        assert mounts[0].destination == "/guest"

    def test_name_with_dots(self):
        """A share name may contain dots (longest prefix match for scope/mode)."""
        ctx = make_ctx()
        levels = [
            LevelView("box", {"box.path.share_ro.a.b.c": "/h:/g"}),
        ]
        mounts = _resolve(levels, ctx)
        assert len(mounts) == 1
        assert mounts[0].destination == "/g"


# ---------------------------------------------------------------------------
# Discovery includes defaults
# ---------------------------------------------------------------------------


class TestDiscovery:
    def test_default_only_share_is_discovered(self):
        """A share present only in a level's .defaults is mounted."""
        ctx = make_ctx()
        levels = [
            LevelView("box", {}),
            LevelView(
                "crab",
                {},
                defaults={"crab.path.share_ro.cfg": "/host/cfg:/g/cfg"},
            ),
            LevelView("system", {}),
        ]
        mounts = _resolve(levels, ctx)
        assert len(mounts) == 1
        assert mounts[0].source == Path("/host/cfg")
        assert mounts[0].options == "ro"


# ---------------------------------------------------------------------------
# Ordering determinism within a scope
# ---------------------------------------------------------------------------


class TestOrdering:
    def test_within_scope_ordered_by_mode_then_name(self):
        ctx = make_ctx()
        levels = [
            LevelView(
                "box",
                {
                    "box.path.share_rw.z": "/hz:/gz",
                    "box.path.share_rw.a": "/ha:/ga",
                    "box.path.share_ro.m": "/hm:/gm",
                },
            ),
        ]
        mounts = _resolve(levels, ctx)
        # ro sorts before rw; within rw, name ascending.
        assert [m.destination for m in mounts] == ["/gm", "/ga", "/gz"]


# ---------------------------------------------------------------------------
# is_share_key
# ---------------------------------------------------------------------------


class TestIsShareKey:
    def test_true_for_each_scope_and_mode(self):
        assert is_share_key("system.path.share_ro.foo")
        assert is_share_key("crab.path.share_rw.bar")
        assert is_share_key("workset.path.share_ro.x")
        assert is_share_key("box.path.share_rw.y")
        # Dotted name is allowed (greedy remainder).
        assert is_share_key("system.path.share_rw.a.b.c")

    def test_false_for_non_share_keys(self):
        assert not is_share_key("system.path.data")
        assert not is_share_key("crab.model")
        assert not is_share_key("box.image")
        assert not is_share_key("nope.path.share_rw.foo")
        assert not is_share_key("system.path.share_xx.foo")
        # Missing the trailing name.
        assert not is_share_key("system.path.share_rw")
