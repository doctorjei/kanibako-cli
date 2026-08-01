"""Tests for :mod:`kanibako.agent_defaults` — the ABSTRACT-category DECLARATION
ROOTING (spec §2a L474-525).

The rule under test: an author writes a bare LEAF in a plugin's defaults file, and
the LOADER stores the full self-resolving ``@meta.agent.<a>.path/<category>/<leaf>``.
Nothing prepends a root later — that mechanism (``scope_roots``) is what P3 deleted.

These drive the REAL loader over SYNTHETIC declaration files (written to a temp
package dir and read through ``importlib.resources``-compatible package access), so
they pin the RULE rather than claude's two shipped rows; the shipped rows are pinned
by ``test_targets/test_claude.py`` and ``test_defaults_golden.py``.
"""

from __future__ import annotations

import sys

import pytest

from kanibako import agent_defaults
from kanibako.agent_config import (
    agent_category_dirname,
    agent_category_root,
    agent_category_root_ref,
    is_self_resolving,
    root_relative_source,
)
from kanibako.settings.settings_resolve import SettingsError


@pytest.fixture
def declfile(tmp_path, monkeypatch):
    """Write a synthetic ``<agent>-defaults.yaml`` into an importable package.

    Returns a ``write(text) -> (package, filename)`` callable; the loader reads it
    through the same ``importlib.resources`` route the shipped plugins use.
    """
    pkg_dir = tmp_path / "kanibako_probe_defaults"
    pkg_dir.mkdir()
    (pkg_dir / "__init__.py").write_text("")
    monkeypatch.syspath_prepend(str(tmp_path))
    sys.modules.pop("kanibako_probe_defaults", None)

    def write(text: str, filename: str = "probe-defaults.yaml"):
        (pkg_dir / filename).write_text(text)
        return "kanibako_probe_defaults", filename

    yield write
    sys.modules.pop("kanibako_probe_defaults", None)


class TestLoadCommonRooting:
    def test_load_common_emits_rooted_ref(self, declfile):
        """T2 — a bare leaf is STORED as the agent DECLARATION ROOT ref.

        (Mutation: ``agent_category_dirname('common')`` -> "" → the ``/common/``
        segment vanishes → RED.)"""
        package, filename = declfile(
            "common:\n"
            "  - key: plugins\n"
            "    host_src: plugins\n"
            "    box_dest: \"$GUEST_HOME/.claude/plugins\"\n"
            "  - key: cache\n"
            "    host_src: cache\n"
            "    box_dest: \"$GUEST_HOME/.claude/cache\"\n"
        )
        common_binds = agent_defaults.load_common(package, filename, "claude")
        assert common_binds == {
            "agent.claude.common.plugins": (
                "@meta.agent.claude.path/common/plugins",
                "/home/agent/.claude/plugins",
            ),
            "agent.claude.common.cache": (
                "@meta.agent.claude.path/common/cache",
                "/home/agent/.claude/cache",
            ),
        }

    def test_rooted_ref_is_built_from_host_src_not_key(self, declfile):
        """T3 — ``key`` names the KEYSPACE entry, ``host_src`` is the PATH leaf.

        They are coincidentally equal for claude's shipped rows; keeping them
        independent is what lets an override repoint the source without renaming
        the key.  (Mutation: build the ref from ``entry['key']`` → the source
        becomes ``…/common/p`` → RED.)"""
        package, filename = declfile(
            "common:\n"
            "  - key: p\n"
            "    host_src: plugins\n"
            "    box_dest: \"$GUEST_HOME/.claude/plugins\"\n"
        )
        common_binds = agent_defaults.load_common(package, filename, "claude")
        assert list(common_binds) == ["agent.claude.common.p"]
        assert common_binds["agent.claude.common.p"][0] == (
            "@meta.agent.claude.path/common/plugins"
        )

    @pytest.mark.parametrize(
        "src",
        ["/abs/dir", "~/tdir", "$XDG_CACHE_HOME/x", "@system.cache/tweakcc"],
    )
    def test_self_resolving_source_is_stored_verbatim(self, declfile, src):
        """An ALREADY self-resolving source is NOT root-joined (spec §2a L518-525
        — the root is a default for RELATIVE sources, not a universal law).

        (Mutation: drop the prefix test in ``root_relative_source`` → each of
        these gains an ``@meta.agent.*`` prefix → RED.)"""
        package, filename = declfile(
            "common:\n"
            "  - key: thing\n"
            f"    host_src: \"{src}\"\n"
            "    box_dest: \"$GUEST_HOME/.thing\"\n"
        )
        common_binds = agent_defaults.load_common(package, filename, "claude")
        assert common_binds["agent.claude.common.thing"][0] == src

    def test_options_are_preserved_on_a_rooted_entry(self, declfile):
        """A 3-tuple (explicit mount options) roots its source the same way."""
        package, filename = declfile(
            "common:\n"
            "  - key: plugins\n"
            "    host_src: plugins\n"
            "    box_dest: \"$GUEST_HOME/.claude/plugins\"\n"
            "    options: ro\n"
        )
        common_binds = agent_defaults.load_common(package, filename, "claude")
        assert common_binds["agent.claude.common.plugins"] == (
            "@meta.agent.claude.path/common/plugins",
            "/home/agent/.claude/plugins",
            "ro",
        )

    def test_no_common_block_yields_empty(self, declfile):
        package, filename = declfile("descriptor: {}\n")
        assert agent_defaults.load_common(package, filename, "goose") == {}

    def test_root_follows_the_declaring_agent(self, declfile):
        """The root is keyed on the DECLARING plugin's own name, so two plugins
        never share a store dir."""
        package, filename = declfile(
            "common:\n"
            "  - key: k\n"
            "    host_src: leaf\n"
            "    box_dest: \"$GUEST_HOME/.k\"\n"
        )
        got = agent_defaults.load_common(package, filename, "goose")
        assert got["agent.goose.common.k"][0] == "@meta.agent.goose.path/common/leaf"


class TestCategoryBindsTakeNoRoot:
    """``bindings.{ro,rw}`` take NO root at any scope (spec §2a), so a bare
    relative source there is a plugin DEFECT — refused, not silently rooted."""

    def test_relative_category_bind_is_refused(self, declfile):
        package, filename = declfile(
            "category_binds:\n"
            "  - category: bindings.ro\n"
            "    key: guide\n"
            "    meta_ref: some/relative/path\n"
            "    box_dest: \"$GUEST_HOME/.guide\"\n"
        )
        with pytest.raises(SettingsError) as e:
            agent_defaults.load_category_binds(package, filename, "claude")
        msg = str(e.value)
        assert "agent.claude.bindings.ro.guide" in msg
        assert "some/relative/path" in msg
        assert filename in msg

    @pytest.mark.parametrize(
        "src", ["/abs", "~/x", "$XDG_DATA_HOME/x", "@system.channelroot/x"],
    )
    def test_self_resolving_category_bind_is_accepted(self, declfile, src):
        package, filename = declfile(
            "category_binds:\n"
            "  - category: bindings.ro\n"
            "    key: guide\n"
            f"    meta_ref: \"{src}\"\n"
            "    box_dest: \"$GUEST_HOME/.guide\"\n"
            "    ro: true\n"
        )
        binds = agent_defaults.load_category_binds(package, filename, "claude")
        assert binds == {
            "agent.claude.bindings.ro.guide": (src, "/home/agent/.guide", "ro"),
        }


class TestLayoutSingleSource:
    """The layout helpers are THE single source both the ref builder and the
    persona shim read — spelled once, so they cannot drift."""

    def test_ref_and_path_agree(self, tmp_path):
        ref = agent_category_root_ref("claude", "common")
        real = agent_category_root(tmp_path / "agents", "claude", "common")
        assert ref == "@meta.agent.claude.path/common"
        assert real == tmp_path / "agents" / "claude" / "common"
        assert ref.rsplit("/", 1)[-1] == real.name

    @pytest.mark.parametrize("category", ["common", "caches", "seeded"])
    def test_abstract_categories_have_a_dirname(self, category):
        assert agent_category_dirname(category) == category

    @pytest.mark.parametrize("category", ["bindings.ro", "bindings.rw", "env", "nope"])
    def test_concrete_or_unknown_category_is_refused(self, category):
        """An undeclared category is NOT a key — refuse rather than fabricate a
        default (closed-keyspace rule, spec §0)."""
        with pytest.raises(ValueError, match="ABSTRACT category"):
            agent_category_dirname(category)

    @pytest.mark.parametrize(
        ("src", "rooted"),
        [
            ("leaf", "@r/leaf"),
            ("a/b", "@r/a/b"),
            ("/abs", "/abs"),
            ("~/x", "~/x"),
            ("$V/x", "$V/x"),
            ("@k/x", "@k/x"),
            # ⚑ THE TWO LEADING-ESCAPE CASES FALL ON OPPOSITE SIDES, and the rule
            # has to read them the way the RESOLVER does (asserted directly in
            # ``test_escapes_match_the_resolver`` below):
            #   \/foo  unescapes to /foo  -> ABSOLUTE, left alone
            #   \~foo  unescapes to ~foo  -> a literal relative dir, rooted
            ("\\/foo", "\\/foo"),
            ("\\~foo", "@r/\\~foo"),
            ("\\$V/x", "@r/\\$V/x"),
            ("\\@k/x", "@r/\\@k/x"),
        ],
    )
    def test_prefix_rule(self, src, rooted):
        assert root_relative_source(src, "@r") == rooted
        assert is_self_resolving(src) is (rooted == src)

    @pytest.mark.parametrize(
        ("src", "expanded"),
        [
            (r"\/foo", "/foo"),      # escaped slash -> still ABSOLUTE
            (r"\~foo", "~foo"),      # escaped tilde -> a literal relative dir
            (r"\$V/x", "$V/x"),
            (r"\@k/x", "@k/x"),
            ("/abs", "/abs"),
            ("~/x", "/home/u/x"),
            ("leaf", "leaf"),
        ],
    )
    def test_escapes_match_the_resolver(self, src, expanded):
        r"""The rule must agree with what the RESOLVER makes of the same string.

        ⚑ This is the byte-identity boundary. The retired assembly-time join ran
        POST-expand and tested the (already unescaped) string for a leading ``/``;
        this rule runs PRE-expand. They give the same answer only if the pre-expand
        test reads escapes the way ``expand_expr`` does — which a plain first-char
        test does NOT: it calls ``\/foo`` relative and would root a path the old
        join left absolute.

        A value is self-resolving exactly when its expansion is ABSOLUTE (or is a
        token the expansion would have made absolute), so the two are asserted
        against each other rather than against a hand-written expectation.
        """
        from kanibako.settings.settings_resolve import ResolveCtx, expand_expr

        ctx = ResolveCtx(
            agent_name=None, workset_name=None, host_home="/home/u", xdg={},
        )
        assert expand_expr(
            src, space="host", ctx=ctx, lookup=lambda r, c: "",
        ) == expanded
        # Self-resolving iff the expansion lands on an absolute path, OR the value
        # opens with an unexpanded token ($VAR/@ref) that resolves elsewhere.
        expected = expanded.startswith("/") or src[:1] in ("$", "@")
        assert is_self_resolving(src) is expected


class TestPersonaHostDirAdopt:
    """The ``host_dir_adopt`` LOADER default (T3.2): declared-or-nothing.

    The claude-shaped B3 host-dir adoption (``~/.config/claude/<persona>/``) is a
    CLAUDE capability — claude's class-setup script is the only thing that writes
    that dir.  A plugin gets it only by DECLARING it; the loader default is False,
    so no plugin silently inherits claude's adoption semantics.
    """

    def _persona(self, declfile, block: str):
        package, filename = declfile(
            "descriptor:\n"
            "  command: [\"probe\"]\n"
            "  persona:\n" + block
        )
        return agent_defaults.load_descriptor(package, filename).persona

    def test_block_without_the_field_does_not_adopt(self, declfile):
        """A ``persona:`` block that omits ``host_dir_adopt`` gets False.

        (Mutation: loader default back to True → RED.)"""
        p = self._persona(declfile, "    endpoint_delivery: env\n    token_var: PROBE\n")
        assert p is not None
        assert p.host_dir_adopt is False
        assert p.token_var == "PROBE"

    def test_declared_true_adopts(self, declfile):
        """The claude shape: adoption is available, but only when DECLARED."""
        p = self._persona(declfile, "    endpoint_delivery: env\n    host_dir_adopt: true\n")
        assert p is not None
        assert p.host_dir_adopt is True

    def test_declared_false_does_not_adopt(self, declfile):
        """The codex/goose shape (explicit refusal) reads the same as omission."""
        p = self._persona(declfile, "    endpoint_delivery: env\n    host_dir_adopt: false\n")
        assert p is not None
        assert p.host_dir_adopt is False

    def test_no_persona_block_is_none(self, declfile):
        """No block at all → no spec (the legacy claude-shaped fallback territory,
        spelled out explicitly by ``start.py``'s ``_persona_wiring``)."""
        package, filename = declfile("descriptor:\n  command: [\"probe\"]\n")
        assert agent_defaults.load_descriptor(package, filename).persona is None
