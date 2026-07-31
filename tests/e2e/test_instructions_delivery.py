"""E2e: the per-agent kickoff-loader SEED is delivered on real podman.

Increment 2a — the PLUGIN-DECLARATION half of instruction delivery.  Each agent
plugin ships a kickoff-loader (the flattener SEED, whose primary import is
``@~/canon/COLLECTION.md`` — the canon index) declared as a best-effort
descriptor ``managed_pointer`` bind delivered read-only to
``~/.config/kanibako/kickoff.md``; goose's ``CONTEXT_FILE_NAMES`` env still lists
AGENTS.md (its native slot the FINAL flatten will land in).  This proves the real
mount/env outcome end-to-end and that podman auto-creates the (absent)
mount-parent dir.  The unit-level bind wiring is in
``tests/test_instructions_bind.py``.

The box-start FLATTEN of the SEED into each agent's native instruction slot (the
FINAL file) is a LATER increment and is NOT asserted here.

Scope: boxes exit immediately in e2e (the agent can't auth) — that's expected; we
inspect the exited container's Mounts/Env.

⚑ The ``e2e_env`` / ``goose_e2e_env`` fixtures pre-write the bootstrap config, so
``_ensure_initialized`` early-returns and the packaged-template install is skipped.
Each test runs the real ``install_packaged_templates`` against the fixture data dir
first — exactly what first-init does — so the install + bind path is genuinely
exercised.  (The box guide itself is delivered live inside the whole-dir canon RO
bind at ``~/canon/bible`` — the guide sits at
``bible/general/directives/ROM_GENERAL.md`` — plus launch-flatten, not installed
to a host path.)
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
    run_kanibako,
)

pytestmark = [pytest.mark.e2e, *e2e_requires]

GUEST_HOME = "/home/agent"


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
    config, so the template staging would never happen.
    """
    from kanibako.config import config_file_path, load_config
    from kanibako.paths import load_std_paths
    from kanibako.targets import discover_targets
    from kanibako.templates import install_packaged_templates

    with _active_env(env):
        config = load_config(config_file_path(Path(env["XDG_CONFIG_HOME"])))
        std = load_std_paths(config)
        install_packaged_templates(std, list(discover_targets()))


KICKOFF_DEST = f"{GUEST_HOME}/.config/kanibako/kickoff.md"

# The canon ENTRY POINT the kickoff loader imports (spec §2c / C-CANON P-4).
DIRECTIVE_IMPORT = "@~/canon/COLLECTION.md"

# ⚑ TRANSITION (one release only): the kickoff also carries the PRE-CANON import so
# a plugin release works against a base that still binds the old rom layout.  Pinned
# so its REMOVAL (migration M-12) is a deliberate edit here, not a silent drift.
LEGACY_DIRECTIVE_IMPORT = "@~/playbook/kanibako/directives/KANIBAKO.md"

# The two CORE canon binds (spec §2c), asserted on the REAL container Mounts — the
# physical materialization host-side tests cannot see.  ⚑ COLLECTION.md is a FILE
# bind precisely so ~/canon itself stays WRITABLE for the seeded notebook/workbook.
CANON_COLLECTION_DEST = f"{GUEST_HOME}/canon/COLLECTION.md"
CANON_BIBLE_DEST = f"{GUEST_HOME}/canon/bible"


def assert_canon_binds_ro(cfg: dict) -> None:
    """The packaged canon is mounted READ-ONLY at both declared guest slots.

    Host-side tests prove the SOURCE→DEST mapping; only a real container proves the
    binds actually materialize — and that ``~/canon`` itself is NOT mounted, which
    is what keeps the seeded books writable.
    """
    for dest in (CANON_COLLECTION_DEST, CANON_BIBLE_DEST):
        m = find_mount(cfg, dest)
        assert m is not None, f"canon bind missing at {dest}"
        assert m.get("RW") is False, f"canon bind at {dest} must be read-only"
    assert find_mount(cfg, f"{GUEST_HOME}/canon") is None, (
        "~/canon must NOT be bound — it has to stay writable for the seeded "
        "notebook/workbook books"
    )


def test_claude_kickoff_loader_delivery(e2e_env):
    """claude: kickoff-loader SEED bound ro to ~/.config/kanibako/kickoff.md
    (single directive import); mount-parent .config/kanibako auto-created by podman."""
    env, project, box = e2e_env["env"], e2e_env["project"], "instr-claude"
    run_install(env)

    r = run_kanibako(["create", str(project), "--name", box], env=env)
    assert r.returncode == 0, f"create failed: {r.stderr}"

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
        km = find_mount(cfg, KICKOFF_DEST)
        assert km is not None, "kickoff-loader not bound to ~/.config/kanibako/kickoff.md"
        assert km.get("RW") is False, "kickoff-loader bind must be read-only"
        kickoff = Path(km["Source"]).read_text()
        assert DIRECTIVE_IMPORT in kickoff
        assert LEGACY_DIRECTIVE_IMPORT in kickoff
        assert_canon_binds_ro(cfg)
    finally:
        rm(container_name(box))


def test_goose_kickoff_loader_delivery(goose_e2e_env):
    """goose: kickoff-loader SEED bound ro to ~/.config/kanibako/kickoff.md and
    CONTEXT_FILE_NAMES lists AGENTS.md (its native FINAL slot); mount-parent
    .config/kanibako auto-created by podman."""
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
        km = find_mount(cfg, KICKOFF_DEST)
        assert km is not None, "kickoff-loader not bound to ~/.config/kanibako/kickoff.md"
        assert km.get("RW") is False, "kickoff-loader bind must be read-only"
        kickoff = Path(km["Source"]).read_text()
        assert DIRECTIVE_IMPORT in kickoff
        assert LEGACY_DIRECTIVE_IMPORT in kickoff
        assert_canon_binds_ro(cfg)
        assert "AGENTS.md" in json.loads(
            env_of(cfg).get("CONTEXT_FILE_NAMES", "[]")
        )
    finally:
        rm(container_name(box))
