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
    from kanibako.settings.config import config_file_path, load_config
    from kanibako.settings.paths import load_std_paths
    from kanibako.targets import discover_targets
    from kanibako.launch.templates import install_packaged_templates

    with _active_env(env):
        config = load_config(config_file_path(Path(env["XDG_CONFIG_HOME"])))
        std = load_std_paths(config)
        install_packaged_templates(std, list(discover_targets()))


KICKOFF_DEST = f"{GUEST_HOME}/.config/kanibako/kickoff.md"

# The flattener's in-box path.  ⚑ It rides the unconditional ``kani_pkg`` bind (the
# whole package dir, ro) and is spelled as ONE literal in
# ``commands/start._directive_flatten_shim`` and another in ``vscode_config``'s
# SessionStart hook; this is a THIRD copy, in a test, and it exists so the assertion
# below invokes exactly what a launch invokes.  If the shim's path moves, this fails
# loudly (rc != 0) rather than silently asserting about a different program.
FLATTENER_IN_BOX = "/opt/kanibako/kanibako/scripts/import-directives.py"

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
# ``@agent/directives/ROM_AGENT.md`` import resolves instead of dangling.  ⚑ The
# ``@notebook/MY_CONTENTS.md`` import RESOLVES from the seeds half onward (the notebook
# is seeded into the box home at create); the kickoff's pre-canon transition import is
# the one remaining expected ``unresolved import`` line, until M-12's window closes.
CANON_AGENT_DEST = f"{GUEST_HOME}/canon/bible/agent"

# The HANDBOOK book's SIBLING binds (spec §2c, the seeds half).  ⚑ Only the two SYSTEM
# rows are unconditional: the system store is materialised by install/setup, so a
# missing one means something is genuinely wrong.  The per-scope CHAPTERS are
# SKIP-IF-ABSENT, so which of them appears depends on what the box's scopes supply —
# see ``HANDBOOK_CHAPTERS_EXPECTED``.
HANDBOOK_SYSTEM_DESTS = (
    f"{GUEST_HOME}/canon/handbook/SYS_CONTENTS.md",
    f"{GUEST_HOME}/canon/handbook/general",
)

# The chapters a freshly created PRIMARY box in this harness actually has content for:
#   agent   — the agent store's chapter, stamped from the plugin's ``data/base`` at
#             install (every first-party plugin ships one).
#   box     — the box's own chapter, seeded at create from the packaged box mould.
#   workset — ABSENT: the default workset ships no chapter, which is the NORMAL case
#             and precisely what skip-if-absent exists for (no warning, no mount).
HANDBOOK_CHAPTERS_EXPECTED = (
    f"{GUEST_HOME}/canon/handbook/agent",
    f"{GUEST_HOME}/canon/handbook/box",
)
HANDBOOK_CHAPTER_ABSENT = f"{GUEST_HOME}/canon/handbook/workset"


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

    ⚑ The writability of ``~/canon/{notebook,workbook}`` is asserted SEPARATELY, in
    :func:`assert_canon_books_writable` — R1b deferred it because nothing created
    those dirs yet; the seeds half does, so it is now live.
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


def assert_handbook_binds_ro(cfg: dict) -> None:
    """The HANDBOOK book materialises as SIBLING ro binds, and SKIP-IF-ABSENT holds.

    Three separate claims, and the third is the one only a real box can settle:

    1. the two SYSTEM rows (``SYS_CONTENTS.md`` file bind + the ``general`` chapter)
       are mounted READ-ONLY — the handbook is HOST-owned content, so the box reads it
       and never edits it (a deliberate behaviour change from the writable
       ``~/playbook`` it replaces; M-10);
    2. the chapters a scope actually supplies are mounted, at dests carrying NO agent
       segment ("storage is varied, binding is not", §2d);
    3. a chapter no scope supplies is simply ABSENT — no mount, and (asserted by the
       caller) no warning.  Without skip-if-absent this is the case that would print
       a ro-drop warning on almost every launch of almost every box.
    """
    for dest in HANDBOOK_SYSTEM_DESTS:
        m = find_mount(cfg, dest)
        assert m is not None, f"handbook bind missing at {dest}"
        assert m.get("RW") is False, f"handbook bind at {dest} must be read-only"
    for dest in HANDBOOK_CHAPTERS_EXPECTED:
        m = find_mount(cfg, dest)
        assert m is not None, f"handbook chapter bind missing at {dest}"
        assert m.get("RW") is False, f"handbook chapter at {dest} must be read-only"
    assert find_mount(cfg, HANDBOOK_CHAPTER_ABSENT) is None, (
        f"{HANDBOOK_CHAPTER_ABSENT} must NOT be bound — no workset chapter exists, "
        "and SKIP-IF-ABSENT means an unsupplied chapter is omitted, not warned about"
    )
    assert find_mount(cfg, f"{GUEST_HOME}/canon/handbook") is None, (
        "the handbook ROOT must not be bound — the book is delivered as SIBLINGS "
        "onto the skeleton's pre-created mountpoints (J-7)"
    )


def assert_canon_books_writable(box: str) -> None:
    """⚑ The R1b-deferred half: notebook + workbook ARE writable, handbook is NOT.

    This is the whole shape of the canon in one assertion. The box's OWN books
    (notebook, workbook) are SEEDED into the home and stay agent-owned and writable —
    undeletable only because their 555 parent says so — while everything BOUND around
    them is read-only. If this ever flips, an agent silently loses the only place it
    is allowed to write inside ``~/canon``.
    """
    out = podman_exec(
        container_name(box),
        ["sh", "-c",
         "touch ~/canon/notebook/.probe 2>&1 && echo nb=ok; "
         "touch ~/canon/workbook/.probe 2>&1 && echo wb=ok; "
         "touch ~/canon/handbook/.probe 2>&1 || echo hb=refused"],
    ).stdout
    assert "nb=ok" in out, f"~/canon/notebook must be WRITABLE in-box, got {out!r}"
    assert "wb=ok" in out, f"~/canon/workbook must be WRITABLE in-box, got {out!r}"
    assert "hb=refused" in out, (
        f"~/canon/handbook must be READ-ONLY in-box, got {out!r}"
    )
    seeded = podman_exec(
        container_name(box),
        ["sh", "-c", "cat ~/canon/notebook/MY_CONTENTS.md"],
    ).stdout
    assert "Notebook" in seeded, (
        f"the seeded notebook entry point is missing in-box, got {seeded!r}"
    )


def assert_no_skip_if_absent_warnings(stderr: str) -> None:
    """No launch warning names an absent handbook chapter (the skip-if-absent payoff).

    ⚑ GREPPED, not eyeballed: warning noise that the NORMAL case produces is what
    teaches users to ignore warnings, and "almost every box has no workset chapter"
    is as normal as it gets.

    ⚑ HOST STDERR IS THE RIGHT CHANNEL **HERE**, unlike the flatten pin below — and
    the two are worth contrasting because they look alike and are not. This warning
    is emitted by ``commands/start._emit_category_mounts``, which runs in the HOST
    kanibako process while it assembles the mount list, BEFORE podman is invoked at
    all. The flatten warning is emitted by a program that ``exec``s inside the tmux
    session, so it never leaves the box. Same-shaped assertion, opposite channels.
    """
    offenders = [
        line for line in stderr.splitlines()
        if "does not exist; dropping mount" in line and "canon_hb" in line
    ]
    assert not offenders, (
        f"an absent handbook chapter warned on launch: {offenders}"
    )


def assert_flatten_warns_only_about_the_kickoff_legacy_line(
    cfg: dict, box: str,
) -> None:
    """⚑⚑ EXACTLY ONE unresolved-import warning, and it is a KNOWN one.

    The count is the whole point, so it is asserted as a count and not as an absence.
    Two separate mechanisms had to land for it to be true:

    * the box's NOTEBOOK is seeded, so ``@notebook/MY_CONTENTS.md`` resolves;
    * the skeleton's 0-byte IMPORT-FALLBACK files (F1) make ``SYS_CONTENTS.md``'s
      UNCONDITIONAL chapter imports resolve-to-empty on a box that supplies no
      workset/box chapter — skip-if-absent governs the BIND, not the INDEX, so
      without them every primary box warned about ``@workset/`` on every launch.

    The ONE that remains is the plugin KICKOFF's pre-canon transition import, which
    exists so a plugin release works against an older base and disappears with the
    deferred plugin-deletion release (M-12). Pinning the count is what makes that
    removal a deliberate edit here rather than a silent drift — and what stops a NEW
    dangling import from hiding inside an expected-noise allowance.

    ⚑⚑ WHY THIS RE-RUNS THE FLATTENER INSTEAD OF READING ``start``'s STDERR — DO NOT
    MOVE IT BACK.  The flatten shim ``exec``s INSIDE the tmux session
    (``_directive_flatten_shim`` nests within the bootstrap wrap), so on the real
    non-tty path its stderr goes to the PANE and never reaches the host stderr of
    ``kanibako start``.  Asserting on ``res.stderr`` therefore reads an EMPTY set and
    the pin silently degrades from "exactly one known warning" to "no warnings
    anywhere", which would pass while the box printed anything at all.  Measured on
    both real-podman legs of the gate: 0 lines on host stderr, the expected single
    line in-box.

    So the flattener is re-run IN-BOX over the SAME inputs the agent process used —
    its own ``KANIBAKO_DIRECTIVE_SEED`` / ``KANIBAKO_DIRECTIVE_FINAL`` container env,
    read off the inspect payload — and THAT stderr is pinned.  ``podman exec`` is
    preferred over capturing the tmux pane because it is deterministic: no pane
    timing, no scrollback truncation, no escape stripping.  Re-running is safe by
    construction — the flattener is idempotent (a second pass over the same seed
    produces the same file) and this runs immediately before the box is torn down.
    """
    env = env_of(cfg)
    seed = env.get("KANIBAKO_DIRECTIVE_SEED", "")
    final = env.get("KANIBAKO_DIRECTIVE_FINAL", "")
    assert seed and final, (
        "the box carries no KANIBAKO_DIRECTIVE_SEED/FINAL env, so the launch could "
        f"not have flattened anything: seed={seed!r} final={final!r}"
    )
    result = podman_exec(
        container_name(box),
        ["sh", "-c", f'python3 {FLATTENER_IN_BOX} "$1" "$2"', "sh", seed, final],
    )
    assert result.returncode == 0, (
        f"the in-box flatten failed (rc={result.returncode}); it is ``|| true``-"
        f"guarded at launch, so a failure here is invisible in a real session.\n"
        f"stderr:\n{result.stderr}"
    )
    warnings = [
        line for line in result.stderr.splitlines() if "unresolved import" in line
    ]
    assert len(warnings) == 1, (
        f"expected exactly ONE flatten warning (the kickoff's pre-canon transition "
        f"import), got {len(warnings)}:\n" + "\n".join(warnings)
    )
    assert LEGACY_DIRECTIVE_IMPORT.lstrip("@") in warnings[0], (
        f"the single remaining flatten warning is not the expected kickoff legacy "
        f"line: {warnings[0]!r}"
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
        assert_handbook_binds_ro(cfg)
        assert_canon_locked_down(box)
        assert_canon_books_writable(box)
        assert_no_skip_if_absent_warnings(res.stderr)
        assert_flatten_warns_only_about_the_kickoff_legacy_line(cfg, box)
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
        assert_handbook_binds_ro(cfg)
        assert_canon_locked_down(box)
        assert_canon_books_writable(box)
        assert_no_skip_if_absent_warnings(res.stderr)
        assert_flatten_warns_only_about_the_kickoff_legacy_line(cfg, box)
        assert "AGENTS.md" in json.loads(
            env_of(cfg).get("CONTEXT_FILE_NAMES", "[]")
        )
    finally:
        rm(container_name(box))
