"""Tests for :mod:`kanibako.settings.agent_defaults` — the ABSTRACT-category DECLARATION
ROOTING (spec §2a).

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

from kanibako.settings import agent_defaults
from kanibako.settings.agent_config import (
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
        """An ALREADY self-resolving source is NOT root-joined (spec §2a
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


class TestPersonaBlockLoading:
    """The ``persona:`` block LOADER: declared-or-nothing (T3.2).

    Every field falls back to its :class:`PersonaSpec` default when the block omits
    it, so a plugin never silently inherits another harness's semantics.  The
    load-bearing one is ``model_required``: a MISSING model is not automatically
    invalid, so absence means "this persona needs none" unless the harness declares
    the veto.
    """

    def _persona(self, declfile, block: str):
        package, filename = declfile(
            "descriptor:\n"
            "  command: [\"probe\"]\n"
            "  persona:\n" + block
        )
        return agent_defaults.load_descriptor(package, filename).persona

    def test_block_without_the_field_does_not_veto(self, declfile):
        """A ``persona:`` block that omits ``model_required`` gets False.

        (Mutation: loader default to True → RED.)"""
        p = self._persona(declfile, "    endpoint_delivery: env\n    token_var: PROBE\n")
        assert p is not None
        assert p.model_required is False
        assert p.token_var == "PROBE"

    def test_declared_true_vetoes(self, declfile):
        """The goose/codex shape: the veto exists, but only when DECLARED."""
        p = self._persona(declfile, "    endpoint_delivery: env\n    model_required: true\n")
        assert p is not None
        assert p.model_required is True

    def test_declared_false_does_not_veto(self, declfile):
        """An explicit refusal reads the same as omission."""
        p = self._persona(declfile, "    endpoint_delivery: env\n    model_required: false\n")
        assert p is not None
        assert p.model_required is False

    def test_a_retired_host_dir_adopt_key_is_IGNORED(self, declfile):
        """B3 is retired (D3): a skewed plugin still declaring it still LOADS.

        Descriptor keys are read individually, so an unrecognized one is simply not
        read.  That silence is the RIGHT answer here — unlike ``safe_bypass``, whose
        absence would degrade the launch into permissive, a stale ``host_dir_adopt``
        asks for a credential route that no longer exists, and refusing to load the
        plugin over it would break an install whose base upgraded first.
        """
        p = self._persona(
            declfile,
            "    endpoint_delivery: env\n"
            "    token_var: PROBE\n"
            "    host_dir_adopt: true\n",
        )
        assert p is not None
        assert p.token_var == "PROBE"
        assert not hasattr(p, "host_dir_adopt")

    def test_no_persona_block_is_none(self, declfile):
        """No block at all → no spec (the legacy claude-shaped fallback territory,
        spelled out explicitly by ``start.py``'s ``_persona_wiring``)."""
        package, filename = declfile("descriptor:\n  command: [\"probe\"]\n")
        assert agent_defaults.load_descriptor(package, filename).persona is None


class TestRetiredSafeBypassKey:
    """The pre-rename key ``safe_bypass:`` is REFUSED BY NAME, not ignored.

    ``load_descriptor`` reads descriptor keys individually, so an unrecognized
    one is simply never read.  For THIS key that silence is the dangerous
    outcome, not the harmless one: the descriptor would load with
    ``access_realization=None``, ``access_row`` would return ``None`` for every
    tier, the un-rendered-tier gate would have nothing to check, and the agent
    would launch with no permission emission at all — which on the ENV channel
    IS the bypass (goose: unset ``GOOSE_MODE`` ⇒ ``auto``).

    (Mutation: drop the guard → the descriptor loads clean with no realization
    → a stale plugin silently runs permissive → RED here.)
    """

    def test_retired_key_is_refused_and_names_its_replacement(self, declfile):
        package, filename = declfile(
            "descriptor:\n"
            "  command: [\"probe\"]\n"
            "  safe_bypass:\n"
            "    channel: env\n"
            "    env_var: PROBE_MODE\n"
            "    setting_key: access\n"
            "    tiers:\n"
            "      restricted: {env_value: approve}\n"
            "      full: {env_value: auto}\n"
        )
        with pytest.raises(SettingsError) as exc:
            agent_defaults.load_descriptor(package, filename)
        msg = str(exc.value)
        assert "safe_bypass" in msg          # names what is wrong
        assert "access_realization" in msg   # ...and the cure
        assert filename in msg               # ...and the file to fix

    def test_new_key_still_loads(self, declfile):
        """The guard keys off the RETIRED spelling only — the new one is fine."""
        package, filename = declfile(
            "descriptor:\n"
            "  command: [\"probe\"]\n"
            "  access_realization:\n"
            "    channel: env\n"
            "    env_var: PROBE_MODE\n"
            "    tiers:\n"
            "      full: {env_value: auto}\n"
        )
        d = agent_defaults.load_descriptor(package, filename)
        assert d.access_realization is not None
        assert d.access_realization.rendered_tiers() == ("full",)


class TestAccessRowChannelDiscipline:
    """``access_realization.tiers`` rows are checked AGAINST THEIR CHANNEL at load.

    The dangerous asymmetry this class pins: an EMPTY row is legal on the FLAG
    channel (emit no flag — claude/codex ``restricted``) and ILLEGAL on the ENV
    channel.  Emitting nothing on ENV leaves the variable unset, and an unset
    permission variable is the harness's own default, which for the agents that
    use this channel is the PERMISSIVE one (goose: no ``GOOSE_MODE`` ⇒ ``auto``
    ⇒ full bypass).  So a declared-but-empty ENV row would deliver ``full`` under
    another tier's name — silently, with the launch reporting success.

    A harness that cannot realize a tier OMITS the row; that is a DECLARATION and
    the launch refuses it loudly by name.  Declaring one obliges it to carry a
    value.
    """

    def _descriptor(self, declfile, block: str):
        package, filename = declfile(
            "descriptor:\n"
            "  command: [\"probe\"]\n"
            "  access_realization:\n" + block
        )
        return agent_defaults.load_descriptor(package, filename)

    def test_env_channel_refuses_a_declared_empty_row(self, declfile):
        """THE fix: ``restricted: {}`` on ENV is REFUSED at descriptor load.

        (Mutation: drop the guard → the row loads with ``env_value=""``,
        ``assemble_env`` emits nothing, and the box runs at goose's ``auto``
        default while reporting the ``restricted`` tier → RED here.)
        """
        with pytest.raises(SettingsError) as exc:
            self._descriptor(
                declfile,
                "    channel: env\n"
                "    env_var: PROBE_MODE\n"
                "    tiers:\n"
                "      restricted: {}\n"
                "      full: {env_value: auto}\n",
            )
        msg = str(exc.value)
        assert "restricted" in msg          # names the tier
        assert "probe-defaults.yaml" in msg  # ...and the plugin file to fix
        assert "env_value" in msg

    def test_env_channel_accepts_rows_that_carry_values(self, declfile):
        """The shipped goose shape stays legal (both rows carry a value)."""
        d = self._descriptor(
            declfile,
            "    channel: env\n"
            "    env_var: PROBE_MODE\n"
            "    tiers:\n"
            "      restricted: {env_value: approve}\n"
            "      full: {env_value: auto}\n",
        )
        assert d.access_realization is not None
        assert d.access_realization.rendered_tiers() == ("restricted", "full")
        assert d.access_realization.restricted.env_value == "approve"

    def test_flag_channel_still_allows_the_empty_row(self, declfile):
        """The claude/codex shape: an empty FLAG row means "emit nothing".

        Legal, because the tier is realized by the ABSENCE of a flag on an argv
        that carries the tier's other flags — nothing is left to a harness
        default that we would then be lying about.
        """
        d = self._descriptor(
            declfile,
            "    channel: flag\n"
            "    tiers:\n"
            "      restricted: {}\n"
            "      full: {flag: [\"--bypass\"]}\n",
        )
        assert d.access_realization is not None
        assert d.access_realization.restricted.flag == ()
        assert d.access_realization.rendered_tiers() == ("restricted", "full")

    def test_omitted_row_is_not_the_same_as_an_empty_one(self, declfile):
        """OMITTING a row on ENV is legal — that is the "cannot render" case."""
        d = self._descriptor(
            declfile,
            "    channel: env\n"
            "    env_var: PROBE_MODE\n"
            "    tiers:\n"
            "      restricted: {env_value: approve}\n"
            "      full: {env_value: auto}\n",
        )
        assert d.access_realization is not None
        assert d.access_realization.editing is None
        assert d.access_realization.renders("editing") is False


class TestEnvChannelBlocksMustNameTheirVariable:
    """F-5 — the SAME discipline the tier ROWS get, applied to the BLOCK's fields.

    The rows above are checked against their channel; the block declaring that
    channel was not.  ``env_var=raw.get("env_var", "")`` accepted an ENV-channel
    realization that named no variable, and ``assemble_env`` emits a row only
    when one is named (``ar.channel is Channel.ENV and ar.env_var``).  So a plain
    typo — ``env_ver:`` for ``env_var:`` — produced a descriptor that loads, a
    launch whose un-rendered-tier gate passes (the ROWS all exist), and a box
    with ``GOOSE_MODE`` UNSET, i.e. goose's ``auto`` default, while the launch
    reports ``restricted``.  A silent, security-relevant downgrade from one
    transposed letter.

    ``settings:`` entries carry the identical hole (``assemble_env`` skips on
    ``and s.env_var``); there the dropped value is claude's ``endpoint``, so a
    persona runs against the harness's DEFAULT endpoint while every preflight
    reports the configured one.

    The FLAG-channel cases in the class above are the pin that this refusal is
    not too broad — a FLAG block legitimately names no ``env_var`` and must keep
    loading.

    ⚑ The ORIGINATING typo (``env_ver:``) is caught by BOTH guards, and the
    unknown-field one fires FIRST.  So the two ``without_env_var`` tests below
    OMIT the field entirely rather than misspelling it: written with the typo
    they pass with the ``env_var`` guard deleted, pinning nothing.  Each test
    here exercises exactly one guard.
    """

    def _descriptor(self, declfile, block: str):
        package, filename = declfile(
            "descriptor:\n"
            "  command: [\"probe\"]\n"
            "  access_realization:\n" + block
        )
        return agent_defaults.load_descriptor(package, filename)

    def test_env_channel_access_realization_without_env_var_is_refused(self, declfile):
        """(Mutation: restore ``env_var=raw.get("env_var", "")`` with no check →
        the descriptor loads, every row is skipped at assembly, the box runs
        permissive under the ``restricted`` name → RED here.)"""
        with pytest.raises(SettingsError) as exc:
            self._descriptor(
                declfile,
                "    channel: env\n"              # ...and no env_var at all
                "    tiers:\n"
                "      restricted: {env_value: approve}\n"
                "      full: {env_value: auto}\n",
            )
        msg = str(exc.value)
        assert "env_var" in msg                  # names the field
        assert "env" in msg                      # ...and the channel
        assert "probe-defaults.yaml" in msg      # ...and the file to fix

    def test_env_channel_setting_arg_without_env_var_is_refused(self, declfile):
        """The second instance, in ``_build_setting_arg``.

        (Mutation: drop the guard → ``endpoint`` loads with ``env_var=""``,
        ``assemble_env`` skips it, and a persona silently talks to the default
        endpoint → RED here.)"""
        package, filename = declfile(
            "descriptor:\n"
            "  command: [\"probe\"]\n"
            "  settings:\n"
            "    - setting_key: endpoint\n"
            "      channel: env\n"               # ...and no env_var at all
        )
        with pytest.raises(SettingsError) as exc:
            agent_defaults.load_descriptor(package, filename)
        msg = str(exc.value)
        assert "endpoint" in msg                 # names the entry
        assert "env_var" in msg                  # ...and the field
        assert filename in msg                   # ...and the file to fix

    def test_unknown_field_in_the_access_block_is_refused(self, declfile):
        """An unrecognized block field is REFUSED, not silently unread.

        (Mutation: drop the guard → ``env_ver:`` above would be accepted as an
        unread extra even once the ENV check exists on some OTHER spelling, and
        every future field typo degrades to silence → RED here.)"""
        with pytest.raises(SettingsError) as exc:
            self._descriptor(
                declfile,
                "    channel: flag\n"
                "    setting_kye: access\n"       # the typo
                "    tiers:\n"
                "      full: {flag: [\"--bypass\"]}\n",
            )
        msg = str(exc.value)
        assert "setting_kye" in msg              # names the offending field
        assert "probe-defaults.yaml" in msg      # ...and the file to fix

    def test_unknown_field_in_a_settings_entry_is_refused(self, declfile):
        """The ``settings:`` half of the same rule (not in the named three; it
        pins the other branch of the guard added with them)."""
        package, filename = declfile(
            "descriptor:\n"
            "  command: [\"probe\"]\n"
            "  settings:\n"
            "    - setting_key: model\n"
            "      channel: flag\n"
            "      flga: [\"--model\"]\n"         # the typo
        )
        with pytest.raises(SettingsError) as exc:
            agent_defaults.load_descriptor(package, filename)
        msg = str(exc.value)
        assert "flga" in msg
        assert "model" in msg
        assert filename in msg

    def test_a_flag_channel_setting_arg_needs_no_env_var(self, declfile):
        """The refusal is CHANNEL-scoped: claude's ``model`` flag arg still loads."""
        package, filename = declfile(
            "descriptor:\n"
            "  command: [\"probe\"]\n"
            "  settings:\n"
            "    - setting_key: model\n"
            "      channel: flag\n"
            "      flag: [\"--model\"]\n"
        )
        d = agent_defaults.load_descriptor(package, filename)
        assert d.settings[0].flag == ("--model",)
        assert d.settings[0].env_var == ""


class TestFlagChannelSettingsMustNameTheirFlag:
    """The FLAG-channel twin of the rule above, on the OMISSION route.

    ``assemble_argv`` emits ``flag + [value]`` for a FLAG setting.  With no
    ``flag`` the extend contributes nothing and the append puts the VALUE on the
    argv ALONE — a BARE POSITIONAL.  For claude that positional is the initial
    PROMPT (``claude [options] [command] [prompt]``, verified against the
    installed binary), so a malformed descriptor turns a setting's value into
    the text the agent is asked to act on; for a harness whose bare positional
    is a SUBCOMMAND (``goose [COMMAND]``) it is a hard launch failure with an
    unrelated-looking message.  Worse than the ENV twin, whose failure is only a
    silent drop.

    ⚑ The TYPO route (``flga:``) is already closed by the unknown-field guard —
    ``test_unknown_field_in_a_settings_entry_is_refused`` above pins it, and a
    test written in that shape would pass with THIS guard deleted, pinning
    nothing.  So both tests below spell the OMISSION: ``flag`` wholly absent,
    and ``flag: []`` present-but-empty.  Each exercises exactly one guard.

    ⚑ This rule does NOT extend to ``AccessTierRow``, whose empty ``flag`` is
    the documented "emit nothing, deliberately" realization (claude/codex
    ``restricted``) and must keep loading — a tier row emits its flag and
    nothing else, so ``()`` there is meaningful; a setting arg always appends
    its value, so for IT an empty flag has no such reading.
    """

    def test_flag_channel_setting_arg_without_flag_is_refused(self, declfile):
        """``flag`` wholly absent.

        (Mutation: drop the guard → the entry loads with ``flag=()`` and
        ``assemble_argv`` appends the model value as claude's prompt → RED.)"""
        package, filename = declfile(
            "descriptor:\n"
            "  command: [\"probe\"]\n"
            "  settings:\n"
            "    - setting_key: model\n"
            "      channel: flag\n"                # ...and no flag at all
        )
        with pytest.raises(SettingsError) as exc:
            agent_defaults.load_descriptor(package, filename)
        msg = str(exc.value)
        assert "model" in msg                      # names the entry
        assert "flag" in msg                       # ...and the field
        assert filename in msg                     # ...and the file to fix

    def test_flag_channel_setting_arg_with_an_empty_flag_is_refused(self, declfile):
        """``flag: []`` — present but empty, which assembles identically.

        (Mutation: drop the guard → same bare positional as above → RED.)"""
        package, filename = declfile(
            "descriptor:\n"
            "  command: [\"probe\"]\n"
            "  settings:\n"
            "    - setting_key: model\n"
            "      channel: flag\n"
            "      flag: []\n"                     # ...declared, and empty
        )
        with pytest.raises(SettingsError) as exc:
            agent_defaults.load_descriptor(package, filename)
        assert "flag" in str(exc.value)
        assert "model" in str(exc.value)

    def test_an_env_channel_setting_arg_needs_no_flag(self, declfile):
        """NOT-TOO-BROAD control, the mirror of the ``needs_no_env_var`` one.

        claude's shipped ``endpoint`` entry is exactly this shape: ENV channel,
        no ``flag``.  A guard that asked for ``flag`` unconditionally would
        refuse every shipped goose setting and claude's endpoint with it."""
        package, filename = declfile(
            "descriptor:\n"
            "  command: [\"probe\"]\n"
            "  settings:\n"
            "    - setting_key: endpoint\n"
            "      channel: env\n"
            "      env_var: \"ANTHROPIC_BASE_URL\"\n"
        )
        d = agent_defaults.load_descriptor(package, filename)
        assert d.settings[0].env_var == "ANTHROPIC_BASE_URL"
        assert d.settings[0].flag == ()

    def test_an_empty_access_tier_row_flag_still_loads(self, declfile):
        """NOT-TOO-BROAD control #2: the rule stops at ``SettingArg``.

        claude's ``restricted`` row is ``{}`` — emit nothing, deliberately — and
        must keep loading.  This is the pin that a future reader does not
        "restore symmetry" by extending the refusal onto the tier rows."""
        package, filename = declfile(
            "descriptor:\n"
            "  command: [\"probe\"]\n"
            "  access_realization:\n"
            "    channel: flag\n"
            "    setting_key: access\n"
            "    tiers:\n"
            "      restricted: {}\n"
            "      full: {flag: [\"--bypass\"]}\n"
        )
        d = agent_defaults.load_descriptor(package, filename)
        assert d.access_realization is not None
        assert d.access_realization.restricted is not None
        assert d.access_realization.restricted.flag == ()
