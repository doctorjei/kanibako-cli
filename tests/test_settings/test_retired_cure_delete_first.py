# --------------------------------------------------------------------------- #
# The retired-entry cures: the printed sequence must RUN, in the printed order
# --------------------------------------------------------------------------- #
#
# ``d9c362a7`` fixed this for the retired-BEHAVIOR seam. Two sites carried the same
# ``Fix: … then delete …`` order and were left behind:
#
#   * ``settings_assemble.refuse_retired_keys`` — the retired FILE keys (§2h
#     selection / mirror), text built by ``_retired_key_cure``.
#   * ``agent_file._refuse_nested_tables`` — a ``self.<sub>:`` sub-table in an
#     agent file.
#
# The order is not cosmetic, but NEITHER IS IT UNIFORM. keyspec §2a refuses a
# ``set`` whose value collides with a retired entry still STORED upstream, and the
# ``set`` a cure prints reads the very file the stale entry sits in — so where the
# cure IS a runnable ``set``, following the printed sequence top to bottom failed
# at step one, and deleting first is what makes the pair runnable.
#
# ⚑ WHERE THE CURE MOVES RATHER THAN SETS, DELETE-FIRST IS THE BUG, not a
# convenience: that arm tells the user to move the content somewhere else, and a
# delete printed ahead of it destroys the thing being moved (review RETURN, item 1).
# So the order is a property OF THE ARM, and the tests below assert it per arm —
# a blanket "delete always comes first" is exactly the wrong-behavior encoding this
# file was rewritten away from.
#
# The third thing here is the reserved any-agent tier. ``agent set default …`` is
# refused rc 1 (``default`` owns no persona store), so a cure that spelled the node
# out sent the user to a command that cannot run. The tier's default is written at
# the SYSTEM scope as the BARE key, which is the door the tier's own refusal names.
#
# ⚑ THE BAR (review RETURN item 3): printing a cure that happens to be right is not
# evidence. For EACH site — file-key, nested-table ``set`` arm, nested-table
# hand-edit arm, reserved ``default`` — the retired entry is stored in a REAL file
# in a real tree, the door's refusal is captured, THE PRINTED STEPS ARE RUN IN THE
# PRINTED ORDER through ``python -m kanibako``, and the door is re-run to show the
# refusal is GONE. The command run is the one PARSED OUT OF THE MESSAGE, never a
# copy of it typed in here, so a cure that drifts cannot pass by matching itself.

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from kanibako.settings import agent_file
from kanibako.settings.settings_assemble import (
    _retired_behavior_cure,
    refuse_retired_keys,
)
from kanibako.settings.settings_resolve import SettingsError

#: ⚑ THE TREE UNDER REVIEW, carried to the CHILD explicitly. ``_cli`` used to build
#: its env from scratch and drop ``PYTHONPATH``, so in a worktree the subprocess
#: imported MAIN's editable install and the test graded code it was not looking at.
#: pyproject's ``pythonpath=["src"]`` covers PYTEST's own imports only — the child
#: is a fresh interpreter, and there ``PYTHONPATH`` is the only thing that carries.
REPO_SRC = str(Path(__file__).resolve().parents[2] / "src")

_LEVELS = ["base", "system", "workset", "box"]

#: ``  Fix: kanibako …`` — the runnable line of a cure, as PRINTED.
_FIX_CMD = re.compile(r"^\s*Fix: (kanibako [^\n]+)$", re.MULTILINE)


# --------------------------------------------------------------------------- #
# The tree: a real config file, real std paths, real agent files
# --------------------------------------------------------------------------- #

class _Tree:
    """A real, isolated kanibako tree the CLI can be run against."""

    def __init__(self, env, std, system_path, agents_root, home):
        self.env = env
        self.std = std
        self.system_path = system_path
        self.agents_root = agents_root
        self.home = home

    def agent_file(self, node: str) -> Path:
        from kanibako.settings.agent_file import agent_settings_path

        path = agent_settings_path(self.agents_root, node)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def write_system(self, text: str) -> Path:
        self.system_path.parent.mkdir(parents=True, exist_ok=True)
        self.system_path.write_text(text)
        return self.system_path

    def cli(self, *argv, cwd=None):
        return subprocess.run(
            [sys.executable, "-m", "kanibako", *argv],
            env=self.env, capture_output=True, text=True,
            cwd=str(cwd or self.home), timeout=300,
        )

    def run_printed_cure(self, refusal: str):
        """Run the ``Fix: kanibako …`` line THIS message printed, as printed."""
        found = _FIX_CMD.search(refusal)
        assert found, f"no runnable `Fix: kanibako …` line in:\n{refusal}"
        return found.group(1), self.cli(*found.group(1).split()[1:])


@pytest.fixture
def tree(tmp_path, monkeypatch):
    """Isolated HOME/XDG with a written global config, so path resolution is real.

    ⚑ THE SETUP RUNS IN THIS PROCESS while the STEPS run in a CHILD, so both must
    see the same tree — the env is pushed into ``os.environ`` for the parent as
    well, or ``load_std_paths`` resolves under the developer's own data dir and
    the child reads an empty one.
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
    return _Tree(
        env=env, std=std,
        system_path=Path(str(std.settings)),
        agents_root=agents_dir(std.data_path),
        home=dirs["home"],
    )


def _file_key_refusal(raw, *, level, path):
    with pytest.raises(SettingsError) as exc:
        refuse_retired_keys(raw, level=level, path=path)
    return str(exc.value)


# --------------------------------------------------------------------------- #
# 1 · the FILE-key cure: delete first, and the key quoted the way the file spells it
# --------------------------------------------------------------------------- #
# Every file-key cure IS a ``set`` (``system set system.agent=…`` / ``box set …
# pref.system.agent=…``), so this site takes delete-first at EVERY level — the
# arm-by-arm question below is the agent file's, not this one's.

@pytest.mark.parametrize("level", _LEVELS)
def test_the_file_key_cure_prints_the_delete_before_the_set(level, tmp_path) -> None:
    msg = _file_key_refusal(
        {"box": {"agent_name": "claude"}}, level=level, path=tmp_path / "s.yaml",
    )
    assert f"Delete the `box: agent_name` entry from {tmp_path / 's.yaml'} FIRST" in msg
    assert msg.index("FIRST") < msg.index("Fix: "), "the delete must precede the set"


def test_the_file_key_cure_quotes_the_key_as_the_file_spells_it(tmp_path) -> None:
    """``': '.join(parts)``, the YAML the reader is looking at — not a space-joined
    hybrid that is neither YAML nor a dotted key (review RETURN item 2)."""
    msg = _file_key_refusal(
        {"box": {"agent_name": "claude"}}, level="box", path=tmp_path / "s.yaml",
    )
    assert "`box: agent_name`" in msg
    assert "`box agent_name`" not in msg, "space-joined: neither YAML nor a dotted key"


def test_the_file_key_cure_names_the_file_when_the_path_is_unknown() -> None:
    """A caller with no path still gets a deletable location, never the string ``None``."""
    msg = _file_key_refusal({"box": {"agent_name": "claude"}}, level="box", path=None)
    assert "<settings>" in msg
    assert " entry from None" not in msg


# --------------------------------------------------------------------------- #
# 2 · the agent file's nested-table cure: the ORDER IS A PROPERTY OF THE ARM
# --------------------------------------------------------------------------- #

def _nested_refusal(root_tbl, *, node="claude", path=None) -> str:
    with pytest.raises(SettingsError) as exc:
        agent_file._refuse_nested_tables(
            root_tbl, node=node, path=path or Path("/tmp/agent.yaml"),
        )
    return str(exc.value)


def test_the_nested_set_arm_prints_the_delete_before_the_cure(tmp_path) -> None:
    """``env``/``secret_path`` cure with ``agent set``, which READS the file the
    stale table sits in — so that table has to be gone before the set can land."""
    msg = _nested_refusal(
        {"foo": {"env": {"KANI_PROBE": "yes"}}}, path=tmp_path / "agent.yaml",
    )
    assert "  Delete the `self.foo` table from " in msg
    assert msg.index("Delete the `self.foo` table") < msg.index("Fix: ")
    assert "kanibako agent set foo env.KANI_PROBE=yes" in msg


def test_the_no_category_arm_moves_first_and_deletes_after(tmp_path) -> None:
    """Category ``None`` is a MOVE (up one level). Deleting first would destroy
    what the user was just told to move (review RETURN item 1)."""
    msg = _nested_refusal(
        {"foo": {"not_a_category": 1}}, path=tmp_path / "agent.yaml",
    )
    assert msg.index("Fix: ") < msg.index("then delete the `self.foo` table")
    assert "UP ONE LEVEL" in msg
    assert "FIRST" not in msg, "a move cannot follow its own deletion"


@pytest.mark.parametrize(
    ("root_tbl", "category"),
    [({"foo": {"caches": {"a": "/x"}}}, "caches"),
     ({"foo": {"bindings": {"a": "/x"}}}, "bindings")],
)
def test_a_non_verb_category_is_a_hand_edit_and_deletes_after(root_tbl, category,
                                                          tmp_path) -> None:
    """No ``agent set`` verb exists for these (:data:`_VERB_WRITABLE_CATEGORIES` is
    ``env``/``secret_path`` only), so nothing reads the file mid-sequence: the edit
    comes first, the delete after."""
    msg = _nested_refusal(root_tbl, path=tmp_path / "agent.yaml")
    assert msg.index("Fix: ") < msg.index("then delete the `self.foo` table")
    assert "kanibako agent set" not in msg
    assert category in msg


def test_the_default_sub_table_is_a_hand_edit_not_a_verb(tmp_path) -> None:
    """``self.default:`` names the all-agents tier, which lives in the SYSTEM file —
    the cure points there and never prints ``agent set default``."""
    msg = _nested_refusal({"default": {"env": {"X": "y"}}}, path=tmp_path / "agent.yaml")
    assert msg.index("Fix: ") < msg.index("then delete the `self.default` table")
    assert "kanibako agent set" not in msg
    assert "SYSTEM settings file" in msg


# --------------------------------------------------------------------------- #
# 3 · the reserved any-agent tier is cured with the bare key at EVERY non-pref level
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("level", ["agent", "base", "system"])
def test_the_reserved_default_cure_is_the_bare_key(level) -> None:
    cure = _retired_behavior_cure(
        "access", level=level, tier="full", subject="default", node="default",
    )
    assert cure == "kanibako system set access=full"
    assert "agent set default" not in cure, "the reserved tier owns no persona store"


def test_a_persona_node_at_agent_level_still_names_the_node() -> None:
    """The bare key is the ANY-agent default; a persona's own tier stays spelled out."""
    assert _retired_behavior_cure(
        "access", level="agent", tier="full", subject="claude", node="claude",
    ) == "kanibako agent set claude access=full"


@pytest.mark.parametrize("level", ["box", "workset"])
def test_a_pref_request_still_spells_the_reserved_tier_as_data(level) -> None:
    """§2h requests carry the node as data, so the bare-key rule must NOT reach them."""
    cure = _retired_behavior_cure(
        "access", level=level, tier="full", subject="claude",
        box_name="mybox", node="default",
    )
    assert "pref.agent.default.access=full" in cure



# --------------------------------------------------------------------------- #
# 4 · THE BAR: each site, end to end — store, refuse, DO WHAT THE MESSAGE SAYS,
#     re-open
# --------------------------------------------------------------------------- #
# ⚑ THE STEPS ARE PERFORMED AS PRINTED, not by a shortcut that reaches the same end.
# The first version of these tests unlinked the whole file, or rewrote it with extra
# content, and marked that "# the printed delete step". It was not. Deleting the named
# entry ALONE strands its parent holding nothing, which YAML reads as a null, and the
# fix the very same message prints then refuses: "stores entries that are not keys …
# Nothing was written". A test that walks a different route than the one it certifies
# certifies nothing — which is how that got past a green run.
#
# So the delete is PARSED OUT OF THE MESSAGE and applied to the file as described,
# leaf first and then any parent the leaf's removal leaves empty, exactly as the
# sentence says. Nothing is unlinked and nothing is added.

_DELETE_STEP = re.compile(
    r"Delete the `(?P<entry>[^`]+)` (?:entry|table) from (?P<where>\S+) FIRST"
    r"(?: — and (?P<parents>.+?) with it)?",
)


def _spelling_parts(spelling: str) -> "list[str]":
    """A quoted key back into its path segments, whichever of the file's spellings it is."""
    return spelling.split(": ") if ": " in spelling else spelling.split(".")


def perform_printed_delete(path: Path, refusal: str) -> "tuple[list, list]":
    """Apply the message's own delete step to the file at *path*, literally.

    Returns ``(entry, parents)`` so a test can say what it just did.
    """
    import yaml

    from kanibako.settings.config_io import dump_doc

    found = _DELETE_STEP.search(refusal)
    assert found, f"no delete step to perform in:\n{refusal}"
    entry = _spelling_parts(found.group("entry"))
    parents = []
    for spelled in (found.group("parents") or "").split("/"):
        spelled = spelled.strip().strip("`")
        if not spelled:
            continue
        segments = _spelling_parts(spelled)
        # A parent is quoted WITH its YAML colon ("`agent: default:`"); the colon is the
        # table's, not part of the key's name.
        if segments[-1].endswith(":"):
            segments[-1] = segments[-1][:-1]
        parents.append(segments)

    doc = yaml.safe_load(path.read_text()) or {}
    holder = doc
    for part in entry[:-1]:
        holder = holder[part]
    assert entry[-1] in holder, f"the message names {entry}, which the file does not hold"
    del holder[entry[-1]]

    for parent in parents:                       # innermost first, as printed
        spot = doc
        for part in parent[:-1]:
            spot = spot.get(part, {})
        stranded = spot.get(parent[-1])
        if isinstance(stranded, dict) and not stranded:
            del spot[parent[-1]]                 # the parent the delete stranded

    dump_doc(path, doc)
    return entry, parents


def test_the_file_key_site_cures_itself_end_to_end(tree, tmp_path) -> None:
    """``agent.default.default_agent`` stored in the real SYSTEM file, refused by the
    real selection door, cured by doing exactly what that message told the user to do."""
    stale = tree.write_system("agent:\n  default:\n    default_agent: claude\n")
    proj = tmp_path / "proj"
    proj.mkdir()

    first = tree.cli("agent", "reauth", str(proj))
    refusal = first.stdout + first.stderr
    assert first.returncode != 0, f"the stale key was not refused:\n{refusal}"
    assert "'system.default_agent' is RETIRED" in refusal
    assert "agent: default:" in refusal, (
        "the delete step does not name the parent it can strand"
    )

    entry, parents = perform_printed_delete(stale, refusal)
    assert entry == ["agent", "default", "default_agent"]
    assert parents == [["agent", "default"], ["agent"]]
    printed, cure = tree.run_printed_cure(refusal)
    assert cure.returncode == 0, f"{printed!r} failed:\n{cure.stdout}{cure.stderr}"

    again = tree.cli("agent", "reauth", str(proj))
    assert "is RETIRED" not in again.stdout + again.stderr, "the refusal survived its own cure"


def test_the_nested_set_arm_cures_itself_end_to_end(tree) -> None:
    """A ``self.<node>.env`` table in a real agent file: the printed ``agent set``
    lands the value and the door that refused the file now reads it."""
    stale_file = tree.agent_file("shell")
    stale_file.write_text('self:\n  claude:\n    env:\n      KANI_PROBE: "yes"\n')
    tree.agent_file("claude").write_text("self:\n  model: opus\n")

    first = tree.cli("agent", "show", "shell")
    refusal = first.stdout + first.stderr
    assert first.returncode != 0, f"the nested table was not refused:\n{refusal}"
    assert "`self.claude.env` is not a settings key" in refusal

    entry, parents = perform_printed_delete(stale_file, refusal)
    assert entry == ["self", "claude"]
    assert parents == [["self"]], "the `self:` root is the parent this delete strands"
    printed, cure = tree.run_printed_cure(refusal)
    assert cure.returncode == 0, f"{printed!r} failed:\n{cure.stdout}{cure.stderr}"

    again = tree.cli("agent", "show", "shell")
    assert again.returncode == 0, f"the door still refuses:\n{again.stdout}{again.stderr}"
    assert "holds null" not in again.stdout + again.stderr
    read = tree.cli("agent", "get", "claude", "env.KANI_PROBE")
    assert read.returncode == 0, f"the cured key will not read back:\n{read.stderr}"
    assert read.stdout.strip() == printed.split("=", 1)[1], (
        "the value that landed is not the value the cure printed"
    )


def test_the_nested_hand_edit_arm_cures_itself_end_to_end(tree) -> None:
    """No command to run: the printed steps ARE the file edit. Follow them in the
    printed order — move the content up, THEN delete the sub-table — and the door
    opens with the moved content readable."""
    import yaml

    from kanibako.settings.config_io import dump_doc

    stale_file = tree.agent_file("shell")
    stale_file.write_text("self:\n  foo:\n    model: opus\n")

    first = tree.cli("agent", "show", "shell")
    refusal = first.stdout + first.stderr
    assert first.returncode != 0, f"the nested table was not refused:\n{refusal}"
    assert "UP ONE LEVEL" in refusal
    assert refusal.index("Fix: ") < refusal.index("then delete the `self.foo` table")

    # The MOVE the fix describes, then the delete it describes — in that order.
    doc = yaml.safe_load(stale_file.read_text())
    doc["self"].update(doc["self"].pop("foo"))
    dump_doc(stale_file, doc)
    again = tree.cli("agent", "show", "shell")
    assert again.returncode == 0, f"the door still refuses:\n{again.stdout}{again.stderr}"
    assert "model = opus" in again.stdout, "the moved content is not what the door reads"


def test_the_reserved_default_site_cures_itself_end_to_end(tree, tmp_path) -> None:
    """Site 4, through a REAL CLI DOOR: ``workset show --effective`` builds the same
    launch snapshot the launch does, so the retired ``auto_approve`` refuses it, the
    printed bare-key cure sets the tier it names, and the same verb then reads clean.

    ⚑ NO CONTAINER NEEDED. An earlier note claimed this site could only be driven
    in-process because the launch needs docker; that was simply unchecked — the
    ``--effective`` preview runs the very same ``build_launch_snapshot``.
    """
    ws = "ws1"
    created = tree.cli("workset", "create", ws)
    assert created.returncode == 0, f"workset create failed:\n{created.stdout}{created.stderr}"
    tree.write_system("agent:\n  default:\n    auto_approve: true\n")

    def door():
        return tree.cli("workset", "show", str(ws), "--effective")

    first = door()
    refusal = first.stdout + first.stderr
    assert first.returncode != 0, f"the stale tier entry was not refused:\n{refusal}"
    assert "'auto_approve' is RETIRED" in refusal

    entry, parents = perform_printed_delete(tree.system_path, refusal)
    assert entry == ["agent", "default", "auto_approve"]
    assert parents == [["agent", "default"], ["agent"]]
    printed, cure = tree.run_printed_cure(refusal)
    assert "system set access=full" in printed
    assert cure.returncode == 0, f"{printed!r} failed:\n{cure.stdout}{cure.stderr}"

    again = door()
    assert "is RETIRED" not in again.stdout + again.stderr, (
        f"the door still refuses after its own cure:\n{again.stdout}{again.stderr}"
    )
    read = tree.cli("system", "get", "agent.default.access")
    assert read.returncode == 0 and read.stdout.strip() == "agent.default.access=full", (
        "the cure set a tier other than the one it named"
    )


def test_the_reserved_default_cure_is_the_only_spelling_the_cli_takes(tree) -> None:
    """Locks WHY the bare key is required: the old spelled-out cure is a command that
    cannot run, at the door that prints it."""
    proc = tree.cli("agent", "set", "default", "access=full")
    out = proc.stdout + proc.stderr
    assert proc.returncode == 1
    assert "reserved any-agent tier" in out
