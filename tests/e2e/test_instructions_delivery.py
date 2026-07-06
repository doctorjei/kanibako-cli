"""E2e: the default box-guidance file (KANIBAKO.md) is delivered to each
harness's native instruction slot on real podman.

Covers the DEFAULT-PLUGIN-CONFIG delivery path end-to-end: the packaged default
``KANIBAKO.md`` is installed to ``<data>/global/KANIBAKO.md`` and bound read-only
into each agent's config-dir slot, the claude loader + editable ``AGENTS.md`` seed
land, goose's ``CONTEXT_FILE_NAMES`` env is set, and podman auto-creates the
(absent) mount-parent dirs. The unit-level bind wiring is in
``tests/test_instructions_bind.py``; this proves the real mount/env/seed outcome.

Scope: boxes exit immediately in e2e (the agent can't auth) — that's expected;
we inspect the exited container's Mounts/Env plus the host-side seeded files.
Whether a *running* agent actually reads the file (claude secure-mode import,
codex 0.77.0 global-load) is agent-internal runtime behavior, out of scope here.

Codex delivery uses the identical ``meta_ref`` bind machinery as goose (unit-
covered in ``test_instructions_bind.py``); there is no codex stub fixture, so it
is not exercised here.

⚑ The ``e2e_env`` / ``goose_e2e_env`` fixtures pre-write the bootstrap config, so
``_ensure_initialized`` early-returns and the packaged-template install (which
ships KANIBAKO.md) is skipped. Each test runs the real
``install_packaged_templates`` against the fixture data dir first — exactly what
first-init does — so the install + bind path is genuinely exercised.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tests.e2e.conftest import (
    _active_env,
    _podman,
    e2e_requires,
    resolve_box_dir,
    run_kanibako,
)

pytestmark = [pytest.mark.e2e, *e2e_requires]

GUEST_HOME = "/home/agent"
GUIDE_HEADER = "# KANIBAKO.md — Operating Guide for Agents in a Kanibako Box"


def container_name(box: str) -> str:
    return "kanibako-" + box


def rm(name: str) -> None:
    subprocess.run(
        [_podman, "rm", "-f", "-t", "1", name], capture_output=True, timeout=20
    )


def safe_inspect(name: str):
    r = subprocess.run(
        [_podman, "inspect", name], capture_output=True, text=True, timeout=20
    )
    if r.returncode != 0:
        return None
    return json.loads(r.stdout)[0]


def find_mount(cfg: dict, dest: str):
    for m in cfg.get("Mounts", []):
        if m.get("Destination") == dest:
            return m
    return None


def env_of(cfg: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in cfg.get("Config", {}).get("Env", []) or []:
        k, _, v = str(item).partition("=")
        out[k] = v
    return out


def run_install(env: dict) -> None:
    """Run the real packaged-template install against the fixture data dir.

    First-init is otherwise skipped because the fixture pre-writes the bootstrap
    config, so the KANIBAKO.md ship + template staging would never happen.
    """
    from kanibako.config import config_file_path, load_config
    from kanibako.paths import load_std_paths
    from kanibako.targets import discover_targets
    from kanibako.templates import install_packaged_templates

    with _active_env(env):
        config = load_config(config_file_path(Path(env["XDG_CONFIG_HOME"])))
        std = load_std_paths(config)
        install_packaged_templates(std, list(discover_targets()))


def test_claude_kanibako_md_delivery(e2e_env):
    """claude: guide bound ro to ~/.claude/KANIBAKO.md, loader bound ro to
    ~/.claude/CLAUDE.md (@KANIBAKO.md + @AGENTS.md), editable ~/.claude/AGENTS.md
    seeded; mount-parent .claude auto-created by podman."""
    env, project, box = e2e_env["env"], e2e_env["project"], "instr-claude"
    run_install(env)

    guide_src = e2e_env["data_home"] / "kanibako" / "global" / "KANIBAKO.md"
    assert guide_src.is_file(), "install did not ship <data>/global/KANIBAKO.md"

    r = run_kanibako(["create", str(project), "--name", box], env=env)
    assert r.returncode == 0, f"create failed: {r.stderr}"

    seeded = resolve_box_dir(env, project) / "home" / ".claude" / "AGENTS.md"
    assert seeded.is_file(), "editable ~/.claude/AGENTS.md was not seeded"

    res = run_kanibako(
        ["start", box, "--agent", "claude", "-e", "CLAUDE_STUB_MODE=long-running"],
        env=env,
        timeout=90,
    )
    cfg = safe_inspect(container_name(box))
    assert cfg is not None, (
        f"no container created; rc={res.returncode} stderr={res.stderr[-300:]!r}"
    )
    try:
        gm = find_mount(cfg, f"{GUEST_HOME}/.claude/KANIBAKO.md")
        assert gm is not None, "KANIBAKO.md not bound into ~/.claude"
        assert gm.get("RW") is False, "guide bind must be read-only"
        assert str(gm["Source"]).endswith("global/KANIBAKO.md")
        assert Path(gm["Source"]).read_text().startswith(GUIDE_HEADER)

        lm = find_mount(cfg, f"{GUEST_HOME}/.claude/CLAUDE.md")
        assert lm is not None, "loader not bound to ~/.claude/CLAUDE.md"
        assert lm.get("RW") is False, "loader bind must be read-only"
        loader = Path(lm["Source"]).read_text()
        assert "@KANIBAKO.md" in loader and "@AGENTS.md" in loader, loader
    finally:
        rm(container_name(box))


def test_goose_kanibako_md_delivery(goose_e2e_env):
    """goose: guide bound ro to ~/.config/goose/KANIBAKO.md and CONTEXT_FILE_NAMES
    set so goose loads it; mount-parent .config/goose auto-created by podman."""
    env, project, box = (
        goose_e2e_env["env"],
        goose_e2e_env["project"],
        "instr-goose",
    )
    run_install(env)

    r = run_kanibako(["create", str(project), "--name", box], env=env)
    assert r.returncode == 0, f"create failed: {r.stderr}"

    res = run_kanibako(
        ["start", box, "--agent", "goose", "-e", "GOOSE_STUB_MODE=long-running"],
        env=env,
        timeout=90,
    )
    cfg = safe_inspect(container_name(box))
    assert cfg is not None, (
        f"no container created; rc={res.returncode} stderr={res.stderr[-300:]!r}"
    )
    try:
        gm = find_mount(cfg, f"{GUEST_HOME}/.config/goose/KANIBAKO.md")
        assert gm is not None, "KANIBAKO.md not bound into ~/.config/goose"
        assert gm.get("RW") is False, "guide bind must be read-only"
        assert str(gm["Source"]).endswith("global/KANIBAKO.md")
        assert (
            env_of(cfg).get("CONTEXT_FILE_NAMES")
            == '["KANIBAKO.md","AGENTS.md",".goosehints"]'
        )
    finally:
        rm(container_name(box))
