"""The launch's persona verify probe reads the env the BOX receives, not the store's block.

A claude persona whose model is the tier alias ``sonnet`` is sent, in the box, as
``$ANTHROPIC_DEFAULT_SONNET_MODEL``.  The probe used to see only the persona store's
``env:`` block, so a mapping at any other rung (or a keyspace-only persona with no store
block at all) probed the RAW alias, and a 403 on it refused a working box.
"""
from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from kanibako.plugins.claude import ClaudeTarget

NODE = "navigator+claude"
VAR = "ANTHROPIC_DEFAULT_SONNET_MODEL"
MAPPED = "claude-sonnet-4-5-20990101"


def _table(floor, *, cli_env=None):
    """The pre-flight table, off the PRODUCTION chain (build → category entries)."""
    from kanibako.commands.start import _persona_secret_table
    from kanibako.settings.settings_launch import build_launch_snapshot
    from kanibako.settings.settings_resolve import ResolveCtx

    ctx = ResolveCtx(
        agent_name=NODE, workset_name="ws", host_home="/home/u",
        xdg={"XDG_DATA_HOME": "/data"}, config={},
    )
    snap = build_launch_snapshot(
        agent_name=NODE, ctx=ctx, system_path=None, agent_path=None,
        workset_path=None, box_path=None, default_categories=floor,
    )
    return _persona_secret_table(snap, NODE, ctx, cli_env=cli_env)


@pytest.mark.parametrize("key", [
    f"agent.{NODE}.env.{VAR}",
    f"agent.default.env.{VAR}",
    f"workset.env.{VAR}",
    f"box.env.{VAR}",
    f"system.env.{VAR}",
])
def test_every_rung_reaches_the_probe_env(key):
    assert _table({key: MAPPED}).env[VAR] == MAPPED


def test_a_per_run_e_reaches_the_probe_env():
    """``-e`` overrides the key's value in the box, so it does in the probe too."""
    table = _table({f"agent.{NODE}.env.{VAR}": "from-key"}, cli_env={VAR: MAPPED})
    assert table.env[VAR] == MAPPED


def test_a_refused_fold_leaves_no_env_and_the_probe_falls_back_to_the_store():
    """Two scopes naming one VAR: the launch refuses later; the pre-flight must not."""
    from kanibako.commands.start import _persona_probe_env

    table = _table({f"system.env.{VAR}": "a", f"box.env.{VAR}": "b"})
    assert table.env is None
    bundle = mock.Mock(env={VAR: "store"})
    assert _persona_probe_env(table, bundle) == {VAR: "store"}
    assert _persona_probe_env(table, None) is None


def _probe_wire_model(std, proj, system_file, agent_file, *, cli_env=None):
    """Run the LAUNCH pre-flight with a real ClaudeTarget; return the model it sent."""
    from kanibako.commands import start as start_cmd
    from kanibako.settings.agent_select import AgentSelection

    target = ClaudeTarget()
    selection = AgentSelection(node=NODE, source="settings").selection_level
    _auth, endpoint, model, secrets = start_cmd._resolve_box_launch_decisions(
        std=std, proj=proj, target=target, agent_name=NODE,
        system_settings_path=system_file, agent_cfg_path=agent_file,
        selection_level=selection, cli_env=cli_env,
    )
    sent: dict = {}

    def _model_access(url, *, body, **_kw):
        sent.update(body)
        raise RuntimeError("stop after capture")

    with mock.patch(
        "kanibako.plugins.claude.target.probe_general_access",
        return_value=mock.Mock(),
    ), mock.patch(
        "kanibako.plugins.claude.target.probe_model_access",
        side_effect=_model_access,
    ):
        _ep, err, _prov = start_cmd._preflight_persona_load(
            NODE, secrets, endpoint, None,
            target=target, keyspace_model=model, bundle=None, probe=True,
        )
    assert err is None
    return sent.get("model"), secrets


def _persona_files(tmp_path: Path) -> tuple[Path, Path]:
    """A KEYSPACE-ONLY claude persona (no store block) aliased to ``sonnet``.

    The tier mapping sits at the AGENT rung — the persona's own agent file.
    """
    token = tmp_path / "token"
    token.write_text("t0k\n")
    system_file = tmp_path / "settings.yaml"
    system_file.write_text(
        f"agent:\n  {NODE}:\n"
        f"    endpoint: https://proxy.example\n"
        f"    model: sonnet\n"
        f"    secret_path:\n      ANTHROPIC_AUTH_TOKEN: {token}\n"
    )
    agent_file = tmp_path / "agent.yaml"
    agent_file.write_text(f"env:\n  {VAR}: {MAPPED}\n")
    return system_file, agent_file


def test_the_launch_probe_sends_the_mapped_id_not_the_alias(
    std, config, project_dir, tmp_path: Path,
):
    """RED on the base: the probe sent ``sonnet``; the box sends the mapped id."""
    from kanibako.settings.paths import resolve_project

    proj = resolve_project(std, config, str(project_dir), initialize=True)
    system_file, agent_file = _persona_files(tmp_path)
    wire, _secrets = _probe_wire_model(std, proj, system_file, agent_file)
    assert wire == MAPPED


def test_the_probe_env_equals_the_boxs_collapsed_env(
    std, config, project_dir, tmp_path: Path,
):
    """ONE TRUTH: the pre-flight's value is the whole-box resolve's ``meta.assembly.env``."""
    from kanibako.commands import start as start_cmd
    from kanibako.settings.agent_select import AgentSelection
    from kanibako.settings.paths import resolve_project

    proj = resolve_project(std, config, str(project_dir), initialize=True)
    system_file, agent_file = _persona_files(tmp_path)
    cli_env = {"EXTRA_FROM_E": "1"}
    _wire, secrets = _probe_wire_model(
        std, proj, system_file, agent_file, cli_env=cli_env,
    )
    selection = AgentSelection(node=NODE, source="settings").selection_level
    snapshot, _deliveries = start_cmd._resolve_launch_snapshot(
        std=std, proj=proj, agent_name=NODE, system_settings_path=system_file,
        agent_cfg_path=agent_file, desc=None, install=None, target=ClaudeTarget(),
        cli_level=selection, cli_env=cli_env,
    )
    box_env = start_cmd._build_config_env(start_cmd._launch_env_map(snapshot))
    assert box_env[VAR] == secrets.env[VAR] == MAPPED
    assert box_env["EXTRA_FROM_E"] == secrets.env["EXTRA_FROM_E"] == "1"
    # Every var the pre-flight carries reaches the box with the same value.
    assert {k: box_env[k] for k in secrets.env} == secrets.env
