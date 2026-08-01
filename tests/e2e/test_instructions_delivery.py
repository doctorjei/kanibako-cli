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

⚑ C-CANON R2 adds the PLUGIN's bible chapter to what each test asserts: every
first-party plugin now ships ``data/rom/directives/ROM_AGENT.md``, so core emits the
sixth canon bind (``canon_bible_agent``) onto the skeleton's ``~/canon/bible/agent``
mountpoint and the chapter is readable in-box.

The box-start FLATTEN of the SEED into each agent's native instruction slot (the
FINAL file) is a LATER increment and is NOT asserted here.

Scope: boxes exit immediately in e2e (the agent can't auth) — that's expected; we
inspect the exited container's Mounts/Env.

⚑ The ``e2e_env`` / ``goose_e2e_env`` fixtures pre-write the bootstrap config, so
``_ensure_initialized`` early-returns and the packaged-template install is skipped.
Each test runs the real ``install_packaged_templates`` against the fixture data dir
first — exactly what first-init does — so the install + bind path is genuinely
exercised.  (The box guide itself is delivered live inside the ``canon_bible_general``
CHAPTER RO bind at ``~/canon/bible/general`` — the guide sits at
``directives/ROM_GENERAL.md`` inside it — plus launch-flatten, not installed to a
host path.  J-7 replaced R1's single whole-dir ``~/canon/bible`` bind with these
per-chapter siblings.)
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
    podman_exec,
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

# The FIVE CORE canon binds (spec §2c, J-7 SIBLING model), asserted on the REAL
# container Mounts — the physical materialization host-side tests cannot see.
# ⚑ The two indexes are FILE binds, landing file-onto-file on the 0-byte mountpoints
# the box-create skeleton made; each bible chapter is its own directory bind.  NEITHER
# book ROOT is bound: ~/canon holds the SEEDED notebook/workbook, and ~/canon/bible is
# R1's retired whole-dir bind (re-introducing it would put the agent chapter's
# mountpoint back inside a bind SOURCE, which is what J-7 removed).
CANON_CORE_DESTS = (
    f"{GUEST_HOME}/canon/COLLECTION.md",
    f"{GUEST_HOME}/canon/bible/ROM_CONTENTS.md",
    f"{GUEST_HOME}/canon/bible/general",
    f"{GUEST_HOME}/canon/bible/workset",
    f"{GUEST_HOME}/canon/bible/box",
)
CANON_UNBOUND_ROOTS = (f"{GUEST_HOME}/canon", f"{GUEST_HOME}/canon/bible")

# The SIXTH canon bind — the resolved plugin's own bible chapter (``canon_bible_agent``).
# ⚑ C-CANON R2: every first-party plugin now ships ``data/rom/directives/ROM_AGENT.md``,
# so this bind is EMITTED on a real agent box and the bible's
# ``@agent/directives/ROM_AGENT.md`` import resolves instead of dangling — one fewer
# ``unresolved import`` line on stderr per launch (the remaining ones are the
# not-yet-seeded ``@notebook/MY_CONTENTS.md`` and the kickoff's pre-canon transition
# import, both expected until their own phases land).
CANON_AGENT_DEST = f"{GUEST_HOME}/canon/bible/agent"


def assert_canon_binds_ro(cfg: dict) -> None:
    """The packaged canon is mounted READ-ONLY at all five declared guest slots.

    Host-side tests prove the SOURCE→DEST mapping; only a real container proves the
    binds actually materialize onto the pre-created mountpoints, and that neither
    book root is mounted.
    """
    for dest in CANON_CORE_DESTS:
        m = find_mount(cfg, dest)
        assert m is not None, f"canon bind missing at {dest}"
        assert m.get("RW") is False, f"canon bind at {dest} must be read-only"
    for root in CANON_UNBOUND_ROOTS:
        assert find_mount(cfg, root) is None, (
            f"{root} must NOT be bound — the canon is delivered as SIBLINGS onto "
            "pre-created mountpoints (J-7), and ~/canon must stay traversable to the "
            "seeded notebook/workbook books"
        )


def assert_agent_chapter_bound_ro(cfg: dict, box: str) -> None:
    """The PLUGIN's bible chapter is mounted RO at ~/canon/bible/agent (C-CANON R2).

    Host-side tests prove core emits the bind from the resolved target; only a real
    container proves the whole-directory bind lands on the skeleton's pre-created
    (root-owned, EMPTY) mountpoint and that the chapter is actually readable in-box —
    which is what makes the bible's ``@agent/directives/ROM_AGENT.md`` import resolve
    instead of dangling.
    """
    m = find_mount(cfg, CANON_AGENT_DEST)
    assert m is not None, f"plugin bible chapter not bound at {CANON_AGENT_DEST}"
    assert m.get("RW") is False, "the plugin bible chapter must be read-only"
    assert Path(m["Source"], "directives/ROM_AGENT.md").is_file(), (
        f"the bound chapter source {m['Source']} carries no ROM_AGENT.md"
    )
    in_box = podman_exec(
        container_name(box),
        ["cat", f"{CANON_AGENT_DEST}/directives/ROM_AGENT.md"],
    ).stdout
    assert "Core Tome" in in_box, (
        f"the agent chapter is not readable in-box, got {in_box!r}"
    )


def assert_canon_locked_down(box: str) -> None:
    """⚑ THE OWNERSHIP FLIP (J-7) — the half no mount table can show.

    Two assertions, and the SECOND is the one bifrost exists to settle:

    1. ``mkdir ~/canon/scratch`` is REFUSED. Under R1 it SUCCEEDED — that is exactly
       the stray-file pollution the skeleton exists to prevent, so its refusal is the
       behavioural contract.
    2. ``~/canon`` and ``~/canon/bible`` are owned by uid 0 IN-BOX. Without this a
       wrong ``UNSHARE_BOX_ROOT_UID`` landing on some other non-agent subuid would
       satisfy assertion 1 and sail through the entire suite — and that uid is
       precisely the derivation this e2e is here to prove (``chown 0:0`` inside
       ``podman unshare`` is the REAL HOST USER, whom ``keep-id:uid=1000`` maps to
       the in-box agent; container-root is ns-uid 1).

    ⚑ NOT asserted here: that ``~/canon/{notebook,workbook}`` are writable. Nothing
    in R1b CREATES them — the template relayout that seeds them is the seeds half's
    (M-11) — and ``~/canon`` is root-owned 555, so an agent ``mkdir`` of them is
    correctly refused today. This assertion returns WITH the seeds half.
    """
    refused = podman_exec(
        container_name(box),
        ["sh", "-c", "mkdir ~/canon/scratch 2>&1; echo rc=$?"],
    ).stdout
    assert "rc=0" not in refused, (
        f"~/canon must be UNWRITABLE from inside the box, got: {refused!r}"
    )

    owners = podman_exec(
        container_name(box),
        ["sh", "-c", "stat -c %u ~/canon ~/canon/bible"],
    ).stdout.split()
    assert owners == ["0", "0"], (
        f"the canon book roots must be ROOT-OWNED in-box, got uids {owners!r}. "
        "A non-zero non-1000 uid means UNSHARE_BOX_ROOT_UID landed on the wrong "
        "subuid; 1000 means it landed on the agent (the chown 0:0 trap)."
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
        assert_agent_chapter_bound_ro(cfg, box)
        assert_canon_locked_down(box)
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
        assert_agent_chapter_bound_ro(cfg, box)
        assert_canon_locked_down(box)
        assert "AGENTS.md" in json.loads(
            env_of(cfg).get("CONTEXT_FILE_NAMES", "[]")
        )
    finally:
        rm(container_name(box))
