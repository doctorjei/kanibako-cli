# --------------------------------------------------------------------------- #
# The retired-entry cure's PARENT clause: every sentence true for the arm that
# prints it, and the printed sequence runnable top to bottom
# --------------------------------------------------------------------------- #
#
# The delete-before-``set`` step names the parent tables a literal delete of the
# retired entry can strand, and promises that the ``set`` below refuses the
# write while the entry is there. Both halves of that promise have been false:
#
#   * at the levels whose cure lands outside the file holding the stale entry, the
#     ``set`` succeeds (rc 0) and the promised refusal never arrives; and
#   * for the stranded parent, an empty root table is ACCEPTED at the workset and
#     box tiers (rc 0) while a null ``pref:`` and a nested ``pref.agent.claude:
#     {}`` are both refused (rc 1) — so no per-level wording can be true.
#
# ⛔ THE REFUSAL IS NOT PER LEVEL. At the ``agent`` tier it turns on the ARM: the
# cure writes the persona the entry is STORED UNDER, so one entry refuses under
# its own persona and succeeds under another's. Measured per arm in section 5; for
# the behavior keys ``_behavior_cure_checks_file`` is the one place that decides it.
#
# What is therefore stated unconditionally is the one fact that holds at every
# level and for every shape: a table left with nothing under it parses as null,
# and a null under a table key is an entry that is not a key (§0).

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from kanibako.settings.agent_file import _refuse_nested_tables
from kanibako.settings.settings_assemble import (
    _behavior_cure_checks_file,
    _file_spelled_parents,
    refuse_retired_behavior_keys,
    refuse_retired_keys,
)
from kanibako.settings.settings_resolve import (
    SET_TARGETS_ITS_OWN_FILE,
    SettingsError,
)

#: ⚑ THE TREE UNDER REVIEW, carried to the CHILD explicitly — ``pythonpath=["src"]``
#: covers PYTEST's own imports only, and the child is a fresh interpreter.
REPO_SRC = str(Path(__file__).resolve().parents[2] / "src")

_LEVELS = ["base", "system", "workset", "box", "agent"]

#: The refusal this step promises, and the truth it states in its place.
_PROMISES_REFUSAL = "§2a refuses a write that collides with a retired entry"
_PROMISES_NO_REFUSAL = "it does not refuse this entry"
_PROMISES_BAD_ENTRY_REFUSAL = "§2a refuses a write that collides with a bad entry"

#: ``  Delete the `agent: default: default_agent` entry from /p FIRST — and `a:` / `b:`
#: with it, if that leaves them empty: …`` — the step, and the parents it names.
_DELETE_STEP = re.compile(
    r"Delete the `(?P<entry>[^`]+)` (?:entry|table) from (?P<where>\S+) FIRST"
    r"(?: — and (?P<parents>.+?) with it)?",
)
_FIX_CMD = re.compile(r"^\s*Fix: (kanibako [^\n]+)$", re.MULTILINE)


def _behavior_refusal(level, *, node_entry=None, subject="goose"):
    """The retired-behavior refusal at *level*, stored as the caller arranges it."""
    raw = node_entry if node_entry is not None else {"agent": {"default": {"auto_approve": True}}}
    with pytest.raises(SettingsError) as exc:
        refuse_retired_behavior_keys(
            raw, level=level, path=Path("/x/settings.yaml"),
            subject=subject, box_name="mybox",
        )
    return str(exc.value)


def _file_key_refusal(level, raw=None):
    with pytest.raises(SettingsError) as exc:
        refuse_retired_keys(
            raw or {"box": {"agent_name": "claude"}}, level=level, path=Path("/x/settings.yaml"),
        )
    return str(exc.value)


# --------------------------------------------------------------------------- #
# 1 · the clause promises a refusal ONLY where the set gives one
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("level", sorted(SET_TARGETS_ITS_OWN_FILE))
@pytest.mark.parametrize("site", ["behavior", "file-key"])
def test_the_clause_promises_the_refusal_where_the_set_gives_one(level, site) -> None:
    msg = _behavior_refusal(level) if site == "behavior" else _file_key_refusal(level)
    assert _PROMISES_REFUSAL in msg, "this level's cure writes its own file (measured rc 1)"
    assert _PROMISES_NO_REFUSAL not in msg


@pytest.mark.parametrize("level", ["agent", "base"])
@pytest.mark.parametrize("site", ["behavior", "file-key"])
def test_the_clause_never_promises_a_refusal_the_set_does_not_give(level, site) -> None:
    """The defect: a cure that lands outside the file holding the stale entry is
    written (rc 0), so promising a refusal sends the reader after a block that
    never comes. At the ``agent`` tier the default entry is stored under the
    reserved ``default`` persona, so its cure is a ``system set`` — another file."""
    msg = _behavior_refusal(level) if site == "behavior" else _file_key_refusal(level)
    assert _PROMISES_REFUSAL not in msg
    assert _PROMISES_NO_REFUSAL in msg


def test_the_agent_tier_is_decided_per_arm_and_not_per_level() -> None:
    """Same level, same entry spelling, opposite answers — so a level table cannot
    carry it. The persona is *node*, falling back to *subject* (the cure's own rule)."""
    # entry stored under another persona -> the cure writes THAT persona's file
    assert _behavior_cure_checks_file("agent", node="claude", subject="goose") is False
    # entry stored under its own persona -> the cure writes the file holding it
    assert _behavior_cure_checks_file("agent", node="claude", subject="claude") is True
    # the agent file's own root stores no node and falls back to *subject*
    assert _behavior_cure_checks_file("agent", node=None, subject="goose") is True
    # the reserved default tier is cured by the bare key at the SYSTEM scope
    assert _behavior_cure_checks_file("agent", node="default", subject="goose") is False


@pytest.mark.parametrize("level", _LEVELS)
@pytest.mark.parametrize("node", ["claude", None, "default"])
def test_the_wording_matches_the_predicate_at_every_level_and_arm(level, node) -> None:
    """The printed sentence and the decision are the SAME fact — so a spelling stored
    under one persona at one level cannot pick the other's sentence."""
    subject = "goose"
    # `node=None` is the agent file's own ROOT, which stores no node and falls back to
    # *subject*; the `agent:`/`pref: agent:` shapes all carry one.
    raw = {"self": {"auto_approve": True}} if node is None \
        else {"agent": {node: {"auto_approve": True}}}
    if level in ("workset", "box"):
        raw = {"pref": {"agent": {node or "shell": {"auto_approve": True}}}}
    msg = _behavior_refusal(level, node_entry=raw, subject=subject)
    promised = _PROMISES_REFUSAL in msg
    assert promised is _behavior_cure_checks_file(level, node=node, subject=subject), (
        f"{level}/node={node}: the printed sentence must match whether the cure "
        f"writes the file holding this entry"
    )
    assert promised is (_PROMISES_NO_REFUSAL not in msg), "exactly one of the two sentences"


def test_the_level_set_is_the_measured_one() -> None:
    assert SET_TARGETS_ITS_OWN_FILE == {"system", "workset", "box"}
    assert not (SET_TARGETS_ITS_OWN_FILE & {"agent", "base"})


# --------------------------------------------------------------------------- #
# 2 · the stranded-parent reason is true at EVERY level and every shape
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("level", _LEVELS)
def test_the_stranded_parent_is_never_called_a_refusal(level) -> None:
    """An empty root table is ACCEPTED at the workset and box tiers (rc 0) while a
    null and a nested empty table are refused (rc 1) — so no wording here may claim
    the ``set`` refuses it."""
    msg = _behavior_refusal(level)
    assert "refused the same way" not in msg
    step = _DELETE_STEP.search(msg)
    assert step and step.group("parents"), f"no parent named at {level}:\n{msg}"
    tail = msg[step.end():]
    assert "parses as null" in tail and "entry that is not a key (§0)" in tail


@pytest.mark.parametrize(
    ("parts", "spelled"),
    [
        (("agent", "default", "default_agent"), ["`agent: default:`", "`agent:`"]),
        (("agent", "claude", "self", "auto_approve"),
         ["`agent: claude: self:`", "`agent: claude:`", "`agent:`"]),
    ],
)
def test_every_enclosing_table_up_to_the_top_level_is_named(parts, spelled) -> None:
    """Derived from the retired key's OWN path, so it holds at any depth rather than
    from a table of parents measured at one."""
    assert _file_spelled_parents(parts) == spelled


def test_a_top_level_leaf_has_no_parent_to_strand() -> None:
    assert _file_spelled_parents(("agent_name",)) == []


# --------------------------------------------------------------------------- #
# 3 · THE BAR: each printed cure, run through the real CLI, as printed
# --------------------------------------------------------------------------- #

class _Tree:
    """A real, isolated kanibako tree the CLI can be run against."""

    def __init__(self, env, std, agents, home):
        self.env, self.std, self.agents, self.home = env, std, agents, home

    @property
    def system_path(self) -> Path:
        return Path(str(self.std.settings))

    def agent_file(self, node: str) -> Path:
        from kanibako.settings.agent_file import agent_settings_path

        path = agent_settings_path(self.agents, node)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def cli(self, *argv, cwd=None):
        return subprocess.run(
            [sys.executable, "-m", "kanibako", *argv],
            env=self.env, capture_output=True, text=True,
            cwd=str(cwd or self.home), timeout=300,
        )

    def printed_cure(self, refusal: str) -> str:
        found = _FIX_CMD.search(refusal)
        assert found, f"no runnable `Fix: kanibako …` line in:\n{refusal}"
        return found.group(1)


@pytest.fixture
def tree(tmp_path, monkeypatch):
    """Isolated HOME/XDG with a written global config, so path resolution is real.

    ⚑ THE SETUP RUNS IN THIS PROCESS while the steps run in a CHILD, so the env is
    pushed into ``os.environ`` too or the two read different trees.
    """
    from kanibako.settings.agent_config import agents_dir
    from kanibako.settings.config import load_config, write_global_config
    from kanibako.settings.paths import load_std_paths

    dirs = {n: tmp_path / n for n in
            ("home", "config", "data", "state", "cache", "runtime")}
    for directory in dirs.values():
        directory.mkdir()
    env = {
        "HOME": str(dirs["home"]),
        "XDG_CONFIG_HOME": str(dirs["config"]),
        "XDG_DATA_HOME": str(dirs["data"]),
        "XDG_STATE_HOME": str(dirs["state"]),
        "XDG_CACHE_HOME": str(dirs["cache"]),
        "XDG_RUNTIME_DIR": str(dirs["runtime"]),
        "PYTHONPATH": REPO_SRC,
        "PATH": os.environ.get("PATH", ""),
    }
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    cfg = dirs["config"] / "kanibako.cfg"
    write_global_config(cfg)
    std = load_std_paths(load_config(cfg))
    return _Tree(env, std, agents_dir(std.data_path), dirs["home"])


def _perform_printed_delete(path: Path, refusal: str) -> "tuple[list, list]":
    """Apply the message's OWN delete step to the file at *path*, literally.

    Leaf first, then each parent the leaf's removal leaves empty, exactly as the
    sentence names them. Nothing is unlinked and nothing is added.
    """
    from kanibako.settings.config_io import dump_doc

    found = _DELETE_STEP.search(refusal)
    assert found, f"no delete step to perform in:\n{refusal}"
    entry = found.group("entry").split(": ") if ": " in found.group("entry") \
        else found.group("entry").split(".")
    parents = []
    for spelled in (found.group("parents") or "").split("/"):
        spelled = spelled.strip().strip("`")
        if not spelled:
            continue
        segments = spelled.split(": ") if ": " in spelled else spelled.split(".")
        if segments[-1].endswith(":"):        # the colon is the table's, not the key's
            segments[-1] = segments[-1][:-1]
        parents.append(segments)

    doc = yaml.safe_load(path.read_text()) or {}
    holder = doc
    for part in entry[:-1]:
        holder = holder[part]
    assert entry[-1] in holder, f"the message names {entry}, which the file does not hold"
    del holder[entry[-1]]
    for parent in parents:                     # innermost first, as printed
        spot = doc
        for part in parent[:-1]:
            spot = spot.get(part, {})
        stranded = spot.get(parent[-1])
        if isinstance(stranded, dict) and not stranded:
            del spot[parent[-1]]
    dump_doc(path, doc)
    return entry, parents


def test_the_system_file_cure_runs_end_to_end(tree, tmp_path) -> None:
    """The selection cure with the system file holding ONLY the retired chain: delete
    the entry, remove every table the deletion leaves empty, then run the printed
    ``set``. An ``agent: {}`` left behind is what refused the ``set`` before every
    enclosing table was named — the file here must end clean, not half-emptied."""
    tree.agent_file("claude").write_text("self:\n  model: opus\n")
    stale = tree.system_path
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_text("agent:\n  default:\n    default_agent: goose\n")
    proj = tmp_path / "proj"
    proj.mkdir()

    first = tree.cli("agent", "reauth", str(proj))
    refusal = first.stdout + first.stderr
    assert first.returncode != 0, f"the stale key was not refused:\n{refusal}"
    assert "'system.default_agent' is RETIRED" in refusal
    assert _PROMISES_REFUSAL in refusal, "this cure writes the file the entry is in"

    entry, parents = _perform_printed_delete(stale, refusal)
    assert entry == ["agent", "default", "default_agent"]
    assert parents == [["agent", "default"], ["agent"]], "every enclosing table, top to bottom"
    assert yaml.safe_load(stale.read_text()) == {}, f"left behind: {stale.read_text()!r}"

    printed = tree.printed_cure(refusal)
    cure = tree.cli(*printed.split()[1:])
    assert cure.returncode == 0, f"{printed!r} failed:\n{cure.stdout}{cure.stderr}"
    assert stale.read_text().strip() == "system:\n  agent: goose", stale.read_text()

    again = tree.cli("agent", "reauth", str(proj))
    assert "is RETIRED" not in again.stdout + again.stderr, "the refusal survived its cure"


def test_the_agent_file_cure_runs_end_to_end(tree) -> None:
    """The reserved ``default`` persona, stored in the persona's OWN file: the cure is a
    ``system set``, so it writes another file, succeeds, and promises nothing. Following
    the printed steps literally must still work — which is the whole bar."""
    stale = tree.agent_file("goose")
    stale.write_text("agent:\n  default:\n    auto_approve: true\n")
    tree.agent_file("claude").write_text("self:\n  model: opus\n")

    first = tree.cli("agent", "show", "goose")
    refusal = first.stdout + first.stderr
    assert first.returncode != 0, f"the stale tier entry was not refused:\n{refusal}"
    assert "'auto_approve' is RETIRED" in refusal
    assert _PROMISES_REFUSAL not in refusal, (
        "promises a refusal the `system set` below does not give (measured rc 0)"
    )
    assert _PROMISES_NO_REFUSAL in refusal

    entry, parents = _perform_printed_delete(stale, refusal)
    assert entry == ["agent", "default", "auto_approve"]
    assert parents == [["agent", "default"], ["agent"]]

    printed = tree.printed_cure(refusal)
    assert printed == "kanibako system set access=full", "the cure the reader will paste"
    cure = tree.cli(*printed.split()[1:])
    assert cure.returncode == 0, f"{printed!r} failed:\n{cure.stdout}{cure.stderr}"

    again = tree.cli("agent", "show", "goose")
    assert again.returncode == 0, f"the door still refuses:\n{again.stdout}{again.stderr}"
    assert "is RETIRED" not in again.stdout + again.stderr


def test_the_agent_file_cure_for_another_persona_runs_end_to_end(tree) -> None:
    """The other agent arm: a persona's file storing ANOTHER persona's retired entry.
    The cure writes that other persona's file, so it succeeds and promises nothing —
    yet the door that printed it is the one that must be re-run afterwards."""
    stale = tree.agent_file("goose")
    stale.write_text("agent:\n  claude:\n    auto_approve: true\n")
    tree.agent_file("claude").write_text("self:\n  model: opus\n")

    first = tree.cli("agent", "show", "goose")
    refusal = first.stdout + first.stderr
    assert first.returncode != 0, f"the stale tier entry was not refused:\n{refusal}"
    assert _PROMISES_REFUSAL not in refusal

    entry, parents = _perform_printed_delete(stale, refusal)
    assert entry == ["agent", "claude", "auto_approve"]

    printed = tree.printed_cure(refusal)
    assert printed == "kanibako agent set claude access=full"
    cure = tree.cli(*printed.split()[1:])
    assert cure.returncode == 0, f"{printed!r} failed:\n{cure.stdout}{cure.stderr}"

    again = tree.cli("agent", "show", "goose")
    assert again.returncode == 0, f"the door still refuses:\n{again.stdout}{again.stderr}"


def test_the_same_file_cure_promises_its_refusal_and_runs_end_to_end(tree) -> None:
    """The arm whose file the cure DOES write: the refusal promised here is real, and
    the promise is only worth making because the sequence still completes."""
    stale = tree.agent_file("claude")
    stale.write_text("self:\n  auto_approve: true\n")
    tree.agent_file("goose").write_text("self:\n  model: opus\n")

    first = tree.cli("agent", "show", "claude")
    refusal = first.stdout + first.stderr
    assert first.returncode != 0, f"the stale tier entry was not refused:\n{refusal}"
    assert _PROMISES_REFUSAL in refusal, (
        "`agent set claude` writes the very file holding the entry — measured rc 1"
    )

    entry, parents = _perform_printed_delete(stale, refusal)
    assert entry == ["self", "auto_approve"]
    assert parents == [["self"]]

    printed = tree.printed_cure(refusal)
    cure = tree.cli(*printed.split()[1:])
    assert cure.returncode == 0, f"{printed!r} failed:\n{cure.stdout}{cure.stderr}"

    again = tree.cli("agent", "show", "claude")
    assert again.returncode == 0, f"the door still refuses:\n{again.stdout}{again.stderr}"


def test_the_nested_table_arm_runs_end_to_end(tree) -> None:
    """``agent_file`` shares this helper, so its arm takes the same measured sentence.
    Following its printed steps must still work, and the value must be the one printed."""
    stale = tree.agent_file("shell")
    stale.write_text('self:\n  claude:\n    env:\n      KANI_PROBE: "yes"\n')
    tree.agent_file("claude").write_text("self:\n  model: opus\n")

    first = tree.cli("agent", "show", "shell")
    refusal = first.stdout + first.stderr
    assert first.returncode != 0, f"the nested table was not refused:\n{refusal}"
    assert _PROMISES_REFUSAL not in refusal, (
        "`agent set claude` writes claude's file, not shell's — measured rc 0"
    )
    assert _PROMISES_NO_REFUSAL in refusal

    entry, parents = _perform_printed_delete(stale, refusal)
    assert entry == ["self", "claude"]
    assert parents == [["self"]]

    printed = tree.printed_cure(refusal)
    cure = tree.cli(*printed.split()[1:])
    assert cure.returncode == 0, f"{printed!r} failed:\n{cure.stdout}{cure.stderr}"

    again = tree.cli("agent", "show", "shell")
    assert again.returncode == 0, f"the door still refuses:\n{again.stdout}{again.stderr}"
    read = tree.cli("agent", "get", "claude", "env.KANI_PROBE")
    assert read.returncode == 0, f"the cured key will not read back:\n{read.stderr}"
    assert read.stdout.strip() == printed.split("=", 1)[1], (
        "the value that landed is not the value the cure printed"
    )


@pytest.mark.parametrize(
    ("node", "refuses"), [("shell", False), ("claude", True), ("Claude", True)],
)
def test_the_nested_table_arm_promises_a_refusal_only_in_its_own_file(node, refuses) -> None:
    """``agent set claude`` writes claude's file: measured rc 1 while that file still
    stores ``self.claude``, rc 0 when shell's does. Case folds as the node does."""
    with pytest.raises(SettingsError) as exc:
        _refuse_nested_tables(
            {"claude": {"env": {"KANI_PROBE": "yes"}}}, node=node, path=Path("/x/agent.yaml"),
        )
    msg = str(exc.value)
    assert (_PROMISES_BAD_ENTRY_REFUSAL in msg) is refuses
    assert (_PROMISES_NO_REFUSAL in msg) is not refuses
    assert _PROMISES_REFUSAL not in msg, "a nested table is not a retired entry"


def test_the_retired_mirror_in_an_agent_file_promises_its_refusal() -> None:
    """Its cure is ``agent set <agent>``; for the file's own agent that ``set`` reads this
    file and refuses (measured rc 1), so promising nothing would be false."""
    msg = _file_key_refusal("agent", {"box": {"agent": {"model": "sonnet"}}})
    assert "kanibako agent set <agent> model=sonnet" in msg
    assert _PROMISES_REFUSAL in msg
    assert _PROMISES_NO_REFUSAL not in msg


# --------------------------------------------------------------------------- #
# 4 · the stranded-parent wording, against the REAL writers
# --------------------------------------------------------------------------- #

def test_an_empty_root_table_is_ACCEPTED_so_no_wording_may_call_it_refused(tree) -> None:
    """The reason the stranded-parent sentence cannot promise a refusal: at the
    workset tier an empty root table is written straight through."""
    assert tree.cli("workset", "create", "ws1").returncode == 0
    stale = tree.home / "ws1" / "workset.yaml"
    stale.write_text("pref: {}\n")
    cure = tree.cli("workset", "set", "ws1", "pref.agent.default.access=full")
    assert cure.returncode == 0, (
        "this tier now refuses a stranded empty root table — the wording may be "
        "strengthened, but only after re-measuring every level"
    )


def test_a_stranded_null_root_is_refused_so_the_wording_stops_short_of_claiming(tree) -> None:
    """The neighboring truth that makes a per-level promise impossible: the same tier,
    the same file, the same cure — a null ``pref:`` is refused where ``pref: {}`` is not."""
    assert tree.cli("workset", "create", "ws1").returncode == 0
    stale = tree.home / "ws1" / "workset.yaml"
    stale.write_text("pref:\n")
    cure = tree.cli("workset", "set", "ws1", "pref.agent.default.access=full")
    assert cure.returncode == 1, (
        "a stranded null is no longer refused here — re-measure before changing the wording"
    )


# --------------------------------------------------------------------------- #
# 5 · THE MEASUREMENT the wording rests on — the real CLI, every level and arm
# --------------------------------------------------------------------------- #

def test_the_system_tier_cure_really_refuses_while_the_entry_is_there(tree) -> None:
    """``SET_TARGETS_ITS_OWN_FILE`` says ``system`` refuses. Prove it, so a writer that
    stops refusing reddens the sentence that depends on it."""
    assert tree.cli("workset", "create", "ws1").returncode == 0
    stale = tree.system_path
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_text("agent:\n  default:\n    default_agent: goose\n")
    assert tree.cli("system", "set", "system.agent=goose").returncode == 1, (
        "the system `set` no longer refuses a retired entry in its own file — "
        "the clause promising a refusal is now false"
    )


def test_the_workset_tier_cure_really_refuses_while_the_entry_is_there(tree) -> None:
    """``workset`` is in the set for the same reason. ⚑ NO CONTAINER: the ``--effective``
    preview runs the same ``build_launch_snapshot`` the launch does."""
    assert tree.cli("workset", "create", "ws1").returncode == 0
    stale = tree.home / "ws1" / "workset.yaml"
    stale.write_text("pref:\n  agent:\n    default:\n      auto_approve: true\n")
    cure = tree.cli("workset", "set", "ws1", "pref.agent.default.access=full")
    assert cure.returncode == 1, (
        "the workset `set` no longer refuses a retired entry in its own file — "
        "the clause promising a refusal is now false"
    )


def test_the_agent_tier_cure_refuses_only_for_the_persona_holding_the_entry(tree) -> None:
    """The per-arm measurement. Both halves, because the clause differs by exactly this:
    another persona's file -> rc 0 and the clause promises nothing; the holder's own
    file -> rc 1 and the clause promises the refusal."""
    # stored under ANOTHER persona: the cure writes that persona's file
    tree.agent_file("claude").write_text("self:\n  model: opus\n")
    tree.agent_file("goose").write_text("agent:\n  claude:\n    auto_approve: true\n")
    assert tree.cli("agent", "set", "claude", "access=full").returncode == 0, (
        "this arm now refuses — the clause would be true and the measured arm wrong"
    )
    # stored under the persona whose own file the cure writes
    tree.agent_file("claude").write_text("agent:\n  claude:\n    auto_approve: true\n")
    assert tree.cli("agent", "set", "claude", "access=full").returncode == 1, (
        "this arm no longer refuses — the promise the clause makes is now false"
    )


def test_the_reserved_default_arm_cure_writes_the_system_file_and_succeeds(tree) -> None:
    """The arm whose clause was false at its own level: the entry sits in the persona's
    file, the cure is a ``system set``, and the write succeeds with the entry present."""
    tree.agent_file("goose").write_text("agent:\n  default:\n    auto_approve: true\n")
    tree.agent_file("claude").write_text("self:\n  model: opus\n")
    assert tree.cli("system", "set", "access=full").returncode == 0, (
        "this arm now refuses — re-measure before changing the wording"
    )