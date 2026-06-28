"""Unit tests for block 7a — agent descriptor → KeyStore representation.

Covers the brief's §4 checklist: each origin selector (LAUNCHER/INSTALL_DIR/
BINARY/LITERAL) resolves the RIGHT host_src from a fixture ``AgentInstall``;
ro vs rw placement by ``binding.ro``; ``box_dest`` carried VERBATIM (with the
descriptor-loader's ``$GUEST_HOME`` handling — already expanded at load); a
LITERAL-origin raw expr stays raw (§6a); the ``agent.<name>`` key path under the
descriptor's OWN agent name (the §2d ``agent.<agent>.*`` form — NOT a bare
``agent`` token, §0 L21; S27); the None-origin OMIT rule (S27); descriptor order
preserved; PURITY (a fixture install whose paths do NOT exist still represents
them — no ``Path.exists()`` / no I/O).

All KeyStore access uses the UNBOUND ``dict.<method>(store, …)`` form (S3) so a
binding key named ``get``/``items`` could never shadow a method.
"""

from __future__ import annotations

from pathlib import Path

from kanibako.agent_representation import agent_default_partial
from kanibako.settings_store import _MISSING, Bind, KeyStore
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
    # agent.bindings.* (a §0 L21 violation).
    assert (
        _get(partial, "agent", "claude", "bindings", "ro", "launcher") is not _MISSING
    )
    # The bare agent.bindings.* form does NOT exist (it would be a §0 L21 violation).
    assert _get(partial, "agent", "bindings") is _MISSING


# --------------------------------------------------------------------------- #
# Each origin selector resolves the right host_src (faithful mirror)          #
# --------------------------------------------------------------------------- #


def test_origin_launcher_resolves_install_launcher() -> None:
    d = _descriptor(
        _binding(key="launcher", origin=HostSrcOrigin.LAUNCHER, box_dest="/box/l"),
    )
    bind = _get(agent_default_partial(d, INSTALL), "agent", "claude", "bindings", "ro", "launcher")
    assert isinstance(bind, Bind)
    assert bind.host == str(INSTALL.launcher)


def test_origin_launcher_falls_back_to_binary_when_no_launcher() -> None:
    # resolve_binding_source: LAUNCHER → install.launcher OR install.binary.
    inst = AgentInstall(
        name="x", binary=Path("/nope/bin/x"), install_dir=Path("/nope/share/x"),
    )
    d = _descriptor(
        _binding(key="launcher", origin=HostSrcOrigin.LAUNCHER, box_dest="/b"),
    )
    # inst.name == "x" → the partial roots at agent.x.* (the descriptor's own name).
    bind = _get(agent_default_partial(d, inst), "agent", "x", "bindings", "ro", "launcher")
    assert isinstance(bind, Bind)
    assert bind.host == str(inst.binary)


def test_origin_install_dir_resolves_install_dir() -> None:
    d = _descriptor(
        _binding(key="share", origin=HostSrcOrigin.INSTALL_DIR, box_dest="/box/s"),
    )
    bind = _get(agent_default_partial(d, INSTALL), "agent", "claude", "bindings", "ro", "share")
    assert isinstance(bind, Bind)
    assert bind.host == str(INSTALL.install_dir)


def test_origin_binary_resolves_binary() -> None:
    d = _descriptor(
        _binding(key="binary", origin=HostSrcOrigin.BINARY, box_dest="/box/b"),
    )
    bind = _get(agent_default_partial(d, INSTALL), "agent", "claude", "bindings", "ro", "binary")
    assert isinstance(bind, Bind)
    assert bind.host == str(INSTALL.binary)


def test_origin_literal_resolves_literal_src() -> None:
    d = _descriptor(
        _binding(
            key="lit",
            origin=HostSrcOrigin.LITERAL,
            box_dest="/box/lit",
            literal_src=Path("/literal/source/path"),
        ),
    )
    bind = _get(agent_default_partial(d, INSTALL), "agent", "claude", "bindings", "ro", "lit")
    assert isinstance(bind, Bind)
    assert bind.host == "/literal/source/path"


# --------------------------------------------------------------------------- #
# ro vs rw placement, opts convention                                         #
# --------------------------------------------------------------------------- #


def test_ro_binding_placed_under_ro_with_opts_ro() -> None:
    d = _descriptor(
        _binding(key="share", origin=HostSrcOrigin.INSTALL_DIR, box_dest="/b", ro=True),
    )
    partial = agent_default_partial(d, INSTALL)
    bind = _get(partial, "agent", "claude", "bindings", "ro", "share")
    assert isinstance(bind, Bind)
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
    bind = _get(partial, "agent", "claude", "bindings", "rw", "cache")
    assert isinstance(bind, Bind)
    # opts None (NOT "") — the Bind/reconcile convention (S1).
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
    assert isinstance(_get(partial, "agent", "claude", "bindings", "ro", "share"), Bind)
    assert isinstance(_get(partial, "agent", "claude", "bindings", "rw", "cache"), Bind)


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
    bind = _get(agent_default_partial(d, INSTALL), "agent", "claude", "bindings", "ro", "launcher")
    assert isinstance(bind, Bind)
    assert bind.box == "/home/agent/.local/bin/claude"


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
    bind = _get(agent_default_partial(d, INSTALL), "agent", "claude", "bindings", "ro", "lit")
    assert isinstance(bind, Bind)
    assert bind.box == "$XDG_STATE_HOME/kanibako/helper.sock"


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
    assert isinstance(_get(partial, "agent", "claude", "bindings", "ro", "good"), Bind)
    assert _get(partial, "agent", "claude", "bindings", "ro", "ghost") is _MISSING


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
    assert list(dict.keys(ro)) == ["z", "a", "m"]


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
    share = _get(partial, "agent", "claude", "bindings", "ro", "share")
    launcher = _get(partial, "agent", "claude", "bindings", "ro", "launcher")
    assert isinstance(share, Bind) and share.host == str(INSTALL.install_dir)
    assert isinstance(launcher, Bind) and launcher.host == str(INSTALL.launcher)


def test_does_not_mutate_inputs() -> None:
    d = _descriptor(
        _binding(key="share", origin=HostSrcOrigin.INSTALL_DIR, box_dest="/b"),
    )
    before = tuple(d.bindings)
    agent_default_partial(d, INSTALL)
    assert tuple(d.bindings) == before
    # install fields unchanged.
    assert INSTALL.binary == Path("/nope/bin/claude")
