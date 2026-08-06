"""Unit tests for block 7a — agent descriptor → KeyStore representation.

Covers the brief's §4 checklist: each origin selector (LAUNCHER/INSTALL_DIR/
BINARY/LITERAL) resolves the RIGHT host_src from a fixture ``AgentInstall``;
ro vs rw placement by ``binding.ro``; ``box_dest`` carried VERBATIM (with the
descriptor-loader's ``$GUEST_HOME`` handling — already expanded at load); a
LITERAL-origin raw expr stays raw (§6a); the ``agent.<name>`` key path under the
descriptor's OWN agent name (the §2d ``agent.<agent>.*`` form — NOT a bare
``agent`` token, §0; S27); the None-origin OMIT rule (S27); descriptor order
preserved; PURITY (a fixture install whose paths do NOT exist still represents
them — no ``Path.exists()`` / no I/O).

All KeyStore access uses the UNBOUND ``dict.<method>(store, …)`` form (S3) so a
binding key named ``get``/``items`` could never shadow a method.
"""

from __future__ import annotations

from pathlib import Path

from kanibako.settings.agent_representation import agent_default_partial
from kanibako.settings.settings_store import _MISSING, BindEntry, KeyStore
from kanibako.targets.base import (
    AgentInstall,
    BindKind,
    Binding,
    BindScope,
    HostSrcOrigin,
    PluginDescriptor,
)

# A fixture install whose every path is DELIBERATELY non-existent — purity means
# representation never probes the filesystem, so these resolve + store fine.
INSTALL = AgentInstall(
    name="claude",
    binary=Path("/nope/bin/claude"),
    install_dir=Path("/nope/share/claude"),
    launcher=Path("/nope/launcher/claude"),
)


def _binding(
    *,
    key: str,
    origin: HostSrcOrigin,
    box_dest: str,
    ro: bool = True,
    scope: BindScope = BindScope.AGENT_CRITICAL,
    literal_src: Path | None = None,
    kind: BindKind = BindKind.FILE,
) -> Binding:
    return Binding(
        key=key,
        origin=origin,
        box_dest=box_dest,
        kind=kind,
        scope=scope,
        ro=ro,
        literal_src=literal_src,
    )


def _descriptor(*bindings: Binding) -> PluginDescriptor:
    return PluginDescriptor(command=("claude",), bindings=bindings, mode={})


def _get(store: object, *path: str) -> object:
    """Walk *path* via the UNBOUND dict.get (S3); returns _MISSING if absent."""
    node: object = store
    for part in path:
        if not isinstance(node, dict):
            return _MISSING
        node = dict.get(node, part, _MISSING)
    return node


# --------------------------------------------------------------------------- #
# Shape: a KeyStore rooted at agent.<name> — the §2d agent.<agent>.* form (S27) #
# --------------------------------------------------------------------------- #


def test_returns_keystore_rooted_at_agent_name() -> None:
    partial = agent_default_partial(_descriptor(), INSTALL)
    assert isinstance(partial, KeyStore)
    # Rooted at agent.<name> (the descriptor's own agent), NOT a bare `agent`.
    assert dict.get(partial, "agent", _MISSING) is not _MISSING
    assert dict.get(partial, "claude", _MISSING) is _MISSING  # no bare top-level
    agent = dict.get(partial, "agent")
    assert isinstance(agent, KeyStore)
    # The agent's name node is always present (the §2d agent.<name> form).
    assert dict.get(agent, "claude", _MISSING) is not _MISSING
    agent_claude = dict.get(agent, "claude")
    assert isinstance(agent_claude, KeyStore)
    # No bindings → no `bindings` node under agent.<name> (absent, not present-empty).
    assert dict.get(agent_claude, "bindings", _MISSING) is _MISSING


def test_key_path_is_agent_name_not_bare_agent() -> None:
    d = _descriptor(
        _binding(key="launcher", origin=HostSrcOrigin.LAUNCHER, box_dest="/b"),
    )
    partial = agent_default_partial(d, INSTALL)
    # The bind lands at agent.claude.bindings.ro.launcher (§2d) — NOT bare
    # agent.bindings.* (a §0 violation).
    assert (
        _get(partial, "agent", "claude", "bindings", "ro", "/b") is not _MISSING
    )
    # The bare agent.bindings.* form does NOT exist (it would be a §0 violation).
    assert _get(partial, "agent", "bindings") is _MISSING


# --------------------------------------------------------------------------- #
# Each origin selector resolves the right host_src (faithful mirror)          #
# --------------------------------------------------------------------------- #


def test_origin_launcher_resolves_install_launcher() -> None:
    d = _descriptor(
        _binding(key="launcher", origin=HostSrcOrigin.LAUNCHER, box_dest="/box/l"),
    )
    bind = _get(agent_default_partial(d, INSTALL), "agent", "claude", "bindings", "ro", "/box/l")
    assert isinstance(bind, BindEntry)
    assert bind.src == str(INSTALL.launcher)


def test_origin_launcher_falls_back_to_binary_when_no_launcher() -> None:
    # resolve_binding_source: LAUNCHER → install.launcher OR install.binary.
    inst = AgentInstall(
        name="x", binary=Path("/nope/bin/x"), install_dir=Path("/nope/share/x"),
    )
    d = _descriptor(
        _binding(key="launcher", origin=HostSrcOrigin.LAUNCHER, box_dest="/b"),
    )
    # inst.name == "x" → the partial roots at agent.x.* (the descriptor's own name).
    bind = _get(agent_default_partial(d, inst), "agent", "x", "bindings", "ro", "/b")
    assert isinstance(bind, BindEntry)
    assert bind.src == str(inst.binary)


def test_origin_install_dir_resolves_install_dir() -> None:
    d = _descriptor(
        _binding(key="share", origin=HostSrcOrigin.INSTALL_DIR, box_dest="/box/s"),
    )
    bind = _get(agent_default_partial(d, INSTALL), "agent", "claude", "bindings", "ro", "/box/s")
    assert isinstance(bind, BindEntry)
    assert bind.src == str(INSTALL.install_dir)


def test_origin_binary_resolves_binary() -> None:
    d = _descriptor(
        _binding(key="binary", origin=HostSrcOrigin.BINARY, box_dest="/box/b"),
    )
    bind = _get(agent_default_partial(d, INSTALL), "agent", "claude", "bindings", "ro", "/box/b")
    assert isinstance(bind, BindEntry)
    assert bind.src == str(INSTALL.binary)


def test_origin_literal_resolves_literal_src() -> None:
    d = _descriptor(
        _binding(
            key="lit",
            origin=HostSrcOrigin.LITERAL,
            box_dest="/box/lit",
            literal_src=Path("/literal/source/path"),
        ),
    )
    bind = _get(agent_default_partial(d, INSTALL), "agent", "claude", "bindings", "ro", "/box/lit")
    assert isinstance(bind, BindEntry)
    assert bind.src == "/literal/source/path"


# --------------------------------------------------------------------------- #
# ro vs rw placement, opts convention                                         #
# --------------------------------------------------------------------------- #


def test_ro_binding_placed_under_ro_with_opts_ro() -> None:
    d = _descriptor(
        _binding(key="share", origin=HostSrcOrigin.INSTALL_DIR, box_dest="/b", ro=True),
    )
    partial = agent_default_partial(d, INSTALL)
    bind = _get(partial, "agent", "claude", "bindings", "ro", "/b")
    assert isinstance(bind, BindEntry)
    assert bind.opts == "ro"
    # NOT in rw.
    assert _get(partial, "agent", "claude", "bindings", "rw") is _MISSING


def test_rw_binding_placed_under_rw_with_opts_none() -> None:
    d = _descriptor(
        _binding(
            key="cache",
            origin=HostSrcOrigin.INSTALL_DIR,
            box_dest="/b",
            ro=False,
            scope=BindScope.AGENT,
        ),
    )
    partial = agent_default_partial(d, INSTALL)
    bind = _get(partial, "agent", "claude", "bindings", "rw", "/b")
    assert isinstance(bind, BindEntry)
    # opts None (NOT "") — the BindEntry/reconcile convention (S1).
    assert bind.opts is None
    assert _get(partial, "agent", "claude", "bindings", "ro") is _MISSING


def test_mixed_ro_and_rw_both_present() -> None:
    d = _descriptor(
        _binding(key="share", origin=HostSrcOrigin.INSTALL_DIR, box_dest="/s", ro=True),
        _binding(
            key="cache", origin=HostSrcOrigin.BINARY, box_dest="/c", ro=False,
            scope=BindScope.AGENT,
        ),
    )
    partial = agent_default_partial(d, INSTALL)
    assert isinstance(_get(partial, "agent", "claude", "bindings", "ro", "/s"), BindEntry)
    assert isinstance(_get(partial, "agent", "claude", "bindings", "rw", "/c"), BindEntry)


# --------------------------------------------------------------------------- #
# box_dest carried VERBATIM ($GUEST_HOME already expanded at load; raw stays)  #
# --------------------------------------------------------------------------- #


def test_box_dest_carried_verbatim() -> None:
    # The loader has already $GUEST_HOME-expanded, so box_dest arrives literal.
    d = _descriptor(
        _binding(
            key="launcher",
            origin=HostSrcOrigin.LAUNCHER,
            box_dest="/home/agent/.local/bin/claude",
        ),
    )
    ro = _get(agent_default_partial(d, INSTALL), "agent", "claude", "bindings", "ro")
    assert isinstance(ro, KeyStore)
    # ⚑ The destination is now the KEY, not a value field (R-6).
    assert list(dict.keys(ro)) == ["/home/agent/.local/bin/claude"]


def test_literal_origin_raw_box_dest_stays_raw() -> None:
    # A raw @/$XDG/~ box_dest (a LITERAL-origin expr) is NOT expanded here (§6a).
    d = _descriptor(
        _binding(
            key="lit",
            origin=HostSrcOrigin.LITERAL,
            box_dest="$XDG_STATE_HOME/kanibako/helper.sock",
            literal_src=Path("/src"),
        ),
    )
    ro = _get(agent_default_partial(d, INSTALL), "agent", "claude", "bindings", "ro")
    assert isinstance(ro, KeyStore)
    # A raw ``$XDG`` dest is NOT normalized either — R-11 only expands a leading
    # ``~``; everything else is carried through to ``expand``.
    assert list(dict.keys(ro)) == ["$XDG_STATE_HOME/kanibako/helper.sock"]


# --------------------------------------------------------------------------- #
# None-origin rule: OMIT the entry (S27)                                       #
# --------------------------------------------------------------------------- #


def test_none_origin_literal_without_src_is_omitted() -> None:
    # LITERAL with literal_src=None → resolve_binding_source returns None → OMIT.
    d = _descriptor(
        _binding(
            key="ghost", origin=HostSrcOrigin.LITERAL, box_dest="/b", literal_src=None,
        ),
    )
    partial = agent_default_partial(d, INSTALL)
    # The single binding is dropped → no bindings node at all.
    assert _get(partial, "agent", "claude", "bindings") is _MISSING


def test_none_origin_install_dir_unset_is_omitted() -> None:
    # An install whose install_dir is None → INSTALL_DIR resolves to None → OMIT.
    inst = AgentInstall(name="x", binary=Path("/b"), install_dir=None)  # type: ignore[arg-type]
    d = _descriptor(
        _binding(key="share", origin=HostSrcOrigin.INSTALL_DIR, box_dest="/b"),
    )
    partial = agent_default_partial(d, inst)
    # The agent.x node is always present; its only binding was OMITted → no bindings.
    assert _get(partial, "agent", "x") is not _MISSING
    assert _get(partial, "agent", "x", "bindings") is _MISSING


def test_none_origin_omitted_resolvable_kept() -> None:
    # A mix: one resolvable, one None-origin → only the resolvable survives.
    d = _descriptor(
        _binding(key="good", origin=HostSrcOrigin.BINARY, box_dest="/g"),
        _binding(
            key="ghost", origin=HostSrcOrigin.LITERAL, box_dest="/b", literal_src=None,
        ),
    )
    partial = agent_default_partial(d, INSTALL)
    assert isinstance(_get(partial, "agent", "claude", "bindings", "ro", "/g"), BindEntry)
    assert _get(partial, "agent", "claude", "bindings", "ro", "/b") is _MISSING


# --------------------------------------------------------------------------- #
# Descriptor order preserved                                                   #
# --------------------------------------------------------------------------- #


def test_descriptor_order_preserved() -> None:
    d = _descriptor(
        _binding(key="z", origin=HostSrcOrigin.BINARY, box_dest="/z"),
        _binding(key="a", origin=HostSrcOrigin.BINARY, box_dest="/a"),
        _binding(key="m", origin=HostSrcOrigin.BINARY, box_dest="/m"),
    )
    ro = _get(agent_default_partial(d, INSTALL), "agent", "claude", "bindings", "ro")
    assert isinstance(ro, KeyStore)
    assert list(dict.keys(ro)) == ["/z", "/a", "/m"]


# --------------------------------------------------------------------------- #
# Purity: non-existent fixture paths still represent (no exists()/I/O)         #
# --------------------------------------------------------------------------- #


def test_pure_no_filesystem_probe() -> None:
    # INSTALL's paths do not exist; descriptor_mounts would raise/skip, but pure
    # representation stores them regardless.
    d = _descriptor(
        _binding(key="share", origin=HostSrcOrigin.INSTALL_DIR, box_dest="/b"),
        _binding(key="launcher", origin=HostSrcOrigin.LAUNCHER, box_dest="/l"),
    )
    partial = agent_default_partial(d, INSTALL)
    share = _get(partial, "agent", "claude", "bindings", "ro", "/b")
    launcher = _get(partial, "agent", "claude", "bindings", "ro", "/l")
    assert isinstance(share, BindEntry) and share.src == str(INSTALL.install_dir)
    assert isinstance(launcher, BindEntry) and launcher.src == str(INSTALL.launcher)


def test_does_not_mutate_inputs() -> None:
    d = _descriptor(
        _binding(key="share", origin=HostSrcOrigin.INSTALL_DIR, box_dest="/b"),
    )
    before = tuple(d.bindings)
    agent_default_partial(d, INSTALL)
    assert tuple(d.bindings) == before
    # install fields unchanged.
    assert INSTALL.binary == Path("/nope/bin/claude")


# ----------------------------------------------------------------------------- #
# PERSONA node-name rooting (Block E fix 2a) — binds root under the ACTIVE node, #
# NOT install.name (the harness); bare (node==harness) stays byte-identical.     #
# ----------------------------------------------------------------------------- #


def test_persona_node_name_roots_binds_under_node_not_harness() -> None:
    # A persona's active node is ``navigator℘claude``; install.name is the HARNESS
    # ``claude`` (hardcoded in claude's detect()). The binds MUST land under the
    # node the read side (_agent_pick_node walks agent.<active_agent>) can see.
    node = "navigator℘claude"
    d = _descriptor(
        _binding(key="launcher", origin=HostSrcOrigin.LAUNCHER, box_dest="/l"),
        _binding(key="share", origin=HostSrcOrigin.INSTALL_DIR, box_dest="/s"),
    )
    partial = agent_default_partial(d, INSTALL, node_name=node)
    # Binds land under agent.<node>.bindings.* ...
    launcher = _get(partial, "agent", node, "bindings", "ro", "/l")
    share = _get(partial, "agent", node, "bindings", "ro", "/s")
    assert isinstance(launcher, BindEntry) and launcher.src == str(INSTALL.launcher)
    assert isinstance(share, BindEntry) and share.src == str(INSTALL.install_dir)
    # ... and NOT orphaned at agent.claude.* (the harness = install.name), which
    # the persona read path never walks (the e2e-observed defect).
    assert _get(partial, "agent", "claude") is _MISSING


def test_bare_node_name_matches_harness_byte_identical() -> None:
    # Bare claude: the node-name IS the harness "claude" == install.name, so
    # passing node_name explicitly is byte-identical to the install.name default.
    d = _descriptor(
        _binding(key="launcher", origin=HostSrcOrigin.LAUNCHER, box_dest="/l"),
        _binding(key="cfg", origin=HostSrcOrigin.LITERAL,
                 literal_src=Path("/host/cfg"), box_dest="/c", ro=False),
    )
    default = agent_default_partial(d, INSTALL)            # node_name omitted
    explicit = agent_default_partial(d, INSTALL, node_name="claude")
    assert default == explicit
    # Both root at agent.claude (node == harness).
    assert _get(explicit, "agent", "claude", "bindings", "ro", "/l") is not _MISSING


def test_node_name_none_falls_back_to_install_name() -> None:
    # Legacy / test-convenience default: node_name=None → install.name.
    inst = AgentInstall(
        name="x", binary=Path("/nope/bin/x"), install_dir=Path("/nope/share/x"),
    )
    d = _descriptor(
        _binding(key="launcher", origin=HostSrcOrigin.LAUNCHER, box_dest="/b"),
    )
    partial = agent_default_partial(d, inst)  # no node_name
    assert _get(partial, "agent", "x", "bindings", "ro", "/b") is not _MISSING


# ---------------------------------------------------------------------------
# item-0 — ``agent_default_bind_keys`` and its ``TestAgentDefaultBindKeys`` suite
# are GONE (R-9, disk-store rework step 1).
#
# The registry existed for ONE consumer: the ``config set`` set-time floor, so a
# source-only repoint of ``agent.<node>.bindings.{ro,rw}.<key>`` would not be
# refused as "nowhere in the cascade". That CLI write route is retired — an
# accepted loss, backlog DS-BL1 — so the registry had no consumer left and was
# deleted with it.
#
# ⚑ NOTHING ABOUT LAUNCH CHANGED, and the tests that prove it are ABOVE, untouched:
# ``agent_default_partial`` is the launch representation, and a hand-authored
# override in ``agents/<node>/settings.yaml`` still beats it by cascade merge
# (pinned by ``test_config_interface.TestAgentNodeBindWriteRouteRetired::
# test_written_tuple_still_overrides_descriptor_floor_at_launch``). Do not read the
# absence of these tests as a delivery path having gone untested.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# P7 — agent-scope COMMONS re-keyed to the ACTIVE NODE (the persona fix)
# ---------------------------------------------------------------------------


class TestAgentCommonForNode:
    """A plugin declares its commons against its HARNESS name; the §2d read pick
    reads them under the ACTIVE NODE. For a PERSONA those never matched, so the
    box mounted NEITHER ``~/.claude/plugins`` NOR ``~/.claude/cache`` while
    ``ensure_persona_share_symlinks`` maintained links nothing consumed.

    This is the ONE deliberate behaviour change in P7 beyond key renames, and the
    delta below is the phase's enumerated gate: identity for a bare agent, exactly
    two mounts gained for a claude persona, nothing for goose/codex (their
    ``default_common()`` is empty).
    """

    TABLE = {
        "agent.claude.common.plugins": (
            "@meta.agent.claude.path/common/plugins", "~/.claude/plugins",
        ),
        "agent.claude.common.cache": (
            "@meta.agent.claude.path/common/cache", "~/.claude/cache",
        ),
    }

    def test_a_bare_agent_gets_the_identity(self):
        """BYTE-IDENTICAL for every non-persona launch. INVERT: re-key
        unconditionally -> a bare agent's keys change and the equivalence gate
        for the whole phase breaks."""
        from kanibako.settings.agent_representation import agent_common_for_node

        assert agent_common_for_node(
            self.TABLE, node_name="claude", harness="claude",
        ) == self.TABLE

    def test_a_persona_is_rekeyed_AND_rerooted_to_the_node(self):
        """BOTH halves move. INVERT: re-key without re-rooting -> the persona
        binds the HARNESS dir directly, which makes the shim's "a persona that
        legitimately has its own dir wins" branch unreachable."""
        from kanibako.settings.agent_representation import agent_common_for_node

        out = agent_common_for_node(
            self.TABLE, node_name="nav℘claude", harness="claude",
        )
        assert set(out) == {
            "agent.nav℘claude.common.plugins", "agent.nav℘claude.common.cache",
        }
        assert out["agent.nav℘claude.common.plugins"] == (
            "@meta.agent.nav℘claude.path/common/plugins", "~/.claude/plugins",
        )

    def test_a_self_resolving_source_is_carried_verbatim(self):
        """The re-root rule is NARROW: only the harness's own declaration root
        moves. An absolute / ``~`` / ``$var`` / unrelated ``@``-ref source is the
        plugin's deliberate choice (spec §2a), not "my store dir"."""
        from kanibako.settings.agent_representation import agent_common_for_node

        table = {"agent.claude.common.fixed": ("/opt/fixed", "~/x")}
        out = agent_common_for_node(
            table, node_name="nav℘claude", harness="claude",
        )
        assert out["agent.nav℘claude.common.fixed"] == ("/opt/fixed", "~/x")

    def test_an_empty_table_stays_empty(self):
        """goose / codex declare no commons — no delta for their personas."""
        from kanibako.settings.agent_representation import agent_common_for_node

        assert agent_common_for_node({}, node_name="nav℘goose", harness="goose") == {}

    def test_the_persona_gains_exactly_two_mounts_end_to_end(self):
        """⚑ THE ENUMERATED DELTA, measured through the real adapter: 0 -> 2.

        INVERT: revert the call site in ``start.py`` to ``target.default_common()``
        and the persona is back to ZERO agent-scope commons.
        """
        from kanibako.settings.agent_representation import agent_common_for_node
        from kanibako.settings.settings_launch import (
            build_launch_snapshot,
            snapshot_category_entries,
        )
        from kanibako.settings.settings_resolve import ResolveCtx

        node = "nav℘claude"
        ctx = ResolveCtx(
            agent_name=node, workset_name=None, host_home="/home/h",
            xdg={"XDG_DATA_HOME": "/data"},
        )
        floor = {
            "meta.agent.claude.path": "/store/agents/claude",
            f"meta.agent.{node}.path": f"/store/agents/{node}",
        }

        def _mounts(table):
            snap = build_launch_snapshot(
                agent_name=node, ctx=ctx,
                system_path=None, agent_path=None, workset_path=None, box_path=None,
                default_categories={**table, **floor},
            )
            return [
                e for e in snapshot_category_entries(
                    snap, active_agent=node, box_ctx=ctx,
                )
                if e.category == "common"
            ]

        assert _mounts(self.TABLE) == []          # the BUG (harness-keyed)
        fixed = _mounts(agent_common_for_node(
            self.TABLE, node_name=node, harness="claude",
        ))
        assert sorted(e.name for e in fixed) == ["cache", "plugins"]
        # Sourced through the NODE path — the symlink the shim maintains.
        assert all(e.host_src.startswith(f"/store/agents/{node}/common/") for e in fixed)
