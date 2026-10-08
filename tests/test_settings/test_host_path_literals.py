"""A host path is DATA wherever kanibako hands it to the expander: every floor value built
from a real directory, and every referent a deferred ``box_dest`` substitutes, reaches the
launch byte-for-byte whatever ``$ @ \\ ~ {`` its directory names hold.

Driven through ``kanibako create`` and the real launch snapshot
(``start._resolve_launch_snapshot`` → the one mount emitter), with HOME, every XDG root
and the box directory under a directory that holds the character, so the system tier,
the Layer-1 config tier, the channel addresses, the workspace and the workset root all
carry it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

#: One directory-name fragment per character the expander reads as syntax, with ``{``
#: (syntax only after ``$``/``@``) and ``~`` (syntax only at position 0) as guards.
_FRAGMENTS = {
    "dollar": "x$y",
    "at": "x@y",
    "backslash": "x\\y",
    "tilde": "x~y",
    "brace": "x{y}",
}


@pytest.fixture(params=sorted(_FRAGMENTS), ids=sorted(_FRAGMENTS))
def odd_root(request, tmp_path, monkeypatch) -> Path:
    """HOME and every XDG root under ``<tmp>/<fragment>``; the cwd is a scratch dir."""
    root = tmp_path / _FRAGMENTS[request.param]
    for name, sub in (
        ("HOME", "home"), ("XDG_CONFIG_HOME", "config"), ("XDG_DATA_HOME", "data"),
        ("XDG_STATE_HOME", "state"), ("XDG_CACHE_HOME", "cache"),
        ("XDG_RUNTIME_DIR", "run"),
    ):
        (root / sub).mkdir(parents=True)
        monkeypatch.setenv(name, str(root / sub))
    scratch = tmp_path / "cwd"
    scratch.mkdir()
    monkeypatch.chdir(scratch)
    from kanibako.settings.config import user_config_file, write_global_config

    write_global_config(user_config_file())
    return root


def _create_and_launch(
    root: Path, *create_args: str, box_dir: Path | None = None, agent: str = "shell",
    install: object = None, leaf: str = "proj", desc: object = None,
) -> tuple[Path, dict, object]:
    """``kanibako create`` a box at *box_dir* (default ``<root>/box/<leaf>``; an existing
    *box_dir* is a named box, created by its name from the cwd), give it two
    identity binds whose DESTINATIONS reference the workspace, launch it as *agent* with
    *install* and *desc* (default: the target's descriptor), and return ``(box_dir, mounts by dest, snapshot)``."""
    from kanibako import cli
    from kanibako.commands.start import (
        _emit_category_mounts,
        _launch_bind_map,
        _resolve_launch_snapshot,
    )
    from kanibako.settings.config import load_config, user_config_file
    from kanibako.settings.config_io import dump_doc, load_doc
    from kanibako.settings.paths import load_std_paths, resolve_box_target
    from kanibako.settings.settings_launch import box_workset_settings_paths
    from kanibako.targets import resolve_target

    designation = box_dir.name if box_dir is not None else None
    box_dir = box_dir if box_dir is not None else root / "box" / leaf
    if designation is None:
        box_dir.mkdir(parents=True)
    with pytest.raises(SystemExit) as created:
        cli.main(["create", designation or str(box_dir), "--agent", "shell", *create_args])
    assert created.value.code == 0
    config = load_config(user_config_file())
    std = load_std_paths(config)
    proj = resolve_box_target(std, config, str(box_dir), initialize=False)
    box_file, _ = box_workset_settings_paths(proj)
    doc = load_doc(box_file)
    doc.setdefault("box", {})["bindings"] = {"ro": {
        "@meta.box.workspace": ["@meta.box.workspace"],
        "@meta.box.workspace/sub": ["@meta.box.workspace"],
    }}
    dump_doc(box_file, doc)
    target = resolve_target(agent)
    snapshot, deliveries = _resolve_launch_snapshot(
        std=std, proj=proj, agent_name=agent, system_settings_path=std.settings,
        agent_cfg_path=None, desc=desc if desc is not None else target.descriptor,
        install=install, target=target,
        guarantee_create=False, cli_level=None,
    )
    mounts = _emit_category_mounts(
        _launch_bind_map(snapshot), label="host-path-literals",
        skip_if_absent=deliveries.agent_dests,
    )
    return box_dir, {m.destination: m for m in mounts}, snapshot


def test_a_primary_box_mounts_its_host_paths_verbatim(odd_root) -> None:
    """The workspace (``meta.box.workspace``), the inbox (``meta.box.inbox`` off the
    Layer-1 data root) and both deferred destinations keep the character.

    Before the fix: ``$`` refused the create (``Unknown variable``), ``@`` cut the path at
    the ``@``, ``\\`` was eaten as an escape.
    """
    box_dir, mounts, _ = _create_and_launch(odd_root)
    assert mounts["/home/agent/workspace"].source == box_dir
    assert mounts["/home/agent/channels/inbox"].source.as_posix().startswith(
        str(odd_root / "data") + "/"
    )
    assert mounts[str(box_dir)].source == box_dir
    assert mounts[f"{box_dir}/sub"].source == box_dir


def test_a_standalone_box_roots_its_layout_at_the_real_directory(odd_root) -> None:
    """A standalone box's workset root (``meta.runtime.ws_root``) is its own directory, so
    its home and its workspace (``workset.workspaces``) hang off that literal."""
    box_dir, mounts, _ = _create_and_launch(odd_root, "--standalone")
    assert mounts["/home/agent"].source.as_posix().startswith(str(box_dir) + "/")
    workspace = str(mounts["/home/agent/workspace"].source)
    assert workspace.startswith(str(box_dir) + "/")
    assert str(mounts[workspace].source) == workspace


def test_an_agent_install_under_the_home_is_delivered_verbatim(odd_root) -> None:
    """A descriptor's PROBED sources (the install's binary, launcher and share dir, here
    under the home) mount from the real directory."""
    from kanibako.targets import resolve_target
    from kanibako.targets.base import AgentInstall

    home = odd_root / "home"
    share = home / ".local" / "share" / "claude"
    share.mkdir(parents=True)
    binary = home / ".local" / "bin" / "claude"
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\n")
    install = AgentInstall(
        name="claude", binary=binary, install_dir=share, launcher=binary,
    )
    _, mounts, _ = _create_and_launch(odd_root, agent="claude", install=install)
    desc = resolve_target("claude").descriptor
    assert desc is not None
    probed = {
        b.box_dest: b for b in desc.bindings if b.origin.name in ("BINARY", "LAUNCHER")
    }
    assert probed, "the claude descriptor declares a binary delivery"
    for dest in probed:
        assert mounts[dest.replace("~", "/home/agent", 1)].source == binary
    shares = [b.box_dest for b in desc.bindings if b.origin.name == "INSTALL_DIR"]
    assert shares and all(mounts[dest].source == share for dest in shares)


@pytest.mark.parametrize("name", ["w$x", "v@x", "b\\x"])
def test_a_workset_name_is_kept_in_its_channel_addresses(
    tmp_path, monkeypatch, name,
) -> None:
    """A workset NAME (``meta.runtime.ws_name``) takes any character; ``meta.workset.name``
    and the box's partitioned inbox ``<mailboxes>/<name>/<box>`` keep it as typed."""
    from kanibako import cli
    from kanibako.settings.settings_launch import snapshot_leaf

    root = tmp_path / "plain"
    for var, sub in (
        ("HOME", "home"), ("XDG_CONFIG_HOME", "config"), ("XDG_DATA_HOME", "data"),
        ("XDG_STATE_HOME", "state"), ("XDG_CACHE_HOME", "cache"),
        ("XDG_RUNTIME_DIR", "run"),
    ):
        (root / sub).mkdir(parents=True)
        monkeypatch.setenv(var, str(root / sub))
    monkeypatch.chdir(tmp_path)
    from kanibako.settings.config import user_config_file, write_global_config

    write_global_config(user_config_file())
    ws_root = tmp_path / "ws"
    with pytest.raises(SystemExit) as made:
        cli.main(["workset", "create", str(ws_root), "--name", name])
    assert made.value.code == 0
    monkeypatch.chdir(ws_root)
    _, mounts, snapshot = _create_and_launch(root, box_dir=ws_root / "workspaces" / "proj")
    assert snapshot_leaf(snapshot, "meta.workset.name") == name
    inbox = mounts["/home/agent/channels/inbox"].source
    assert inbox.parent.name == name and inbox.name == "proj", inbox


@pytest.mark.parametrize("leaf", ["n$m", "n@m", "n\\m"])
def test_a_box_named_for_its_directory_keeps_the_name(tmp_path, monkeypatch, leaf) -> None:
    """A box an older ``create`` named for its directory, any character included:
    ``meta.box.name`` and the ``KANIBAKO_NAME`` stamp carry the name as it is."""
    from kanibako.settings.settings_launch import snapshot_leaf

    root = tmp_path / "plain"
    for var, sub in (
        ("HOME", "home"), ("XDG_CONFIG_HOME", "config"), ("XDG_DATA_HOME", "data"),
        ("XDG_STATE_HOME", "state"), ("XDG_CACHE_HOME", "cache"),
        ("XDG_RUNTIME_DIR", "run"),
    ):
        (root / sub).mkdir(parents=True)
        monkeypatch.setenv(var, str(root / sub))
    monkeypatch.chdir(tmp_path)
    from kanibako.settings.config import user_config_file, write_global_config

    write_global_config(user_config_file())
    monkeypatch.setattr("kanibako.settings.paths.box_name_reason", lambda name: None)
    _, _, snapshot = _create_and_launch(root, leaf=leaf)
    assert snapshot_leaf(snapshot, "meta.box.name") == leaf
    assert snapshot_leaf(snapshot, "system.env.KANIBAKO_NAME") == leaf


def test_a_literal_origin_source_in_an_installed_package_is_delivered_verbatim(
    odd_root,
) -> None:
    """A LITERAL-origin descriptor source is a resolved path too: the shipped kickoff
    loader is the INSTALLED package file, so a package under an odd directory mounts
    from exactly that file."""
    import dataclasses

    from kanibako.targets import resolve_target
    from kanibako.targets.base import AgentInstall, HostSrcOrigin

    home = odd_root / "home"
    binary = home / ".local" / "bin" / "claude"
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\n")
    install = AgentInstall(name="claude", binary=binary, install_dir=None, launcher=binary)
    kickoff = home / "site-packages" / "kanibako" / "plugins" / "claude" / "KICKOFF.md"
    kickoff.parent.mkdir(parents=True)
    kickoff.write_text("k")
    desc = resolve_target("claude").descriptor
    assert desc is not None
    literal = [b for b in desc.bindings if b.origin is HostSrcOrigin.LITERAL]
    assert literal, "the claude descriptor declares a literal-origin kickoff source"
    moved = dataclasses.replace(desc, bindings=type(desc.bindings)(
        dataclasses.replace(b, literal_src=kickoff) if b in literal else b
        for b in desc.bindings
    ))
    _, mounts, _ = _create_and_launch(
        odd_root, agent="claude", install=install, desc=moved,
    )
    for b in literal:
        assert mounts[b.box_dest.replace("~", "/home/agent", 1)].source == kickoff
