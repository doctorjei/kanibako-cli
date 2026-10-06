# --------------------------------------------------------------------------- #
# The two remaining retired-entry cures print the DELETE before the SET
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
# The order is not cosmetic. keyspec §2a refuses a ``set`` whose value collides with
# a retired entry still STORED upstream, and the ``set`` the cure prints reads the
# very file the stale entry sits in — so following the printed sequence top to
# bottom failed at step one. Deleting first is what makes the pair runnable.
#
# The third thing here is the reserved any-agent tier. ``agent set default …`` is
# refused rc 1 (``default`` owns no persona store), so a cure that spelled the node
# out sent the user to a command that cannot run. The tier's default is written at
# the SYSTEM scope as the BARE key, which is the door the tier's own refusal names.
# Printing the right text is not enough: each printed cure is RUN below.

import os
import subprocess
import sys

import pytest

from kanibako.settings import agent_file
from kanibako.settings.settings_assemble import (
    _retired_behavior_cure,
    refuse_retired_keys,
)
from kanibako.settings.settings_resolve import SettingsError

_LEVELS = ["base", "system", "workset", "box"]


def _cli(tmp_path, *argv):
    """Run ``python -m kanibako`` against a throwaway HOME/XDG under *tmp_path*."""
    home = tmp_path / "home"
    env = {
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home / "config"),
        "XDG_DATA_HOME": str(home / "data"),
        "XDG_STATE_HOME": str(home / "state"),
        "XDG_RUNTIME_DIR": str(tmp_path / "run"),
        "PATH": os.environ.get("PATH", ""),
    }
    return subprocess.run(
        [sys.executable, "-m", "kanibako", *argv],
        env=env, capture_output=True, text=True,
    )


def _file_key_refusal(raw, *, level, path):
    with pytest.raises(SettingsError) as exc:
        refuse_retired_keys(raw, level=level, path=path)
    return str(exc.value)


# --------------------------------------------------------------------------- #
# 1 · the FILE-key cure prints the delete first
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("level", _LEVELS)
def test_the_file_key_cure_prints_the_delete_before_the_set(level, tmp_path) -> None:
    msg = _file_key_refusal(
        {"box": {"agent_name": "claude"}}, level=level, path=tmp_path / "s.yaml",
    )
    assert "then delete" not in msg, "the old order made the printed sequence fail at step one"
    assert f"Delete the `box agent_name` entry from {tmp_path / 's.yaml'} FIRST" in msg
    assert msg.index("FIRST") < msg.index("Fix: "), "the delete must precede the set"


def test_the_file_key_cure_names_the_file_when_the_path_is_unknown() -> None:
    """A caller with no path still gets a deletable location, never the string ``None``."""
    msg = _file_key_refusal({"box": {"agent_name": "claude"}}, level="box", path=None)
    assert "<settings>" in msg
    assert " entry from None" not in msg


# --------------------------------------------------------------------------- #
# 2 · the agent file's nested-table cure prints the delete first
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "root_tbl",
    [{"behavior": {"auto_approve": True}}, {"state": {"x": 1}}],
)
def test_the_nested_agent_cure_prints_the_delete_before_the_set(root_tbl, tmp_path) -> None:
    with pytest.raises(SettingsError) as exc:
        agent_file._refuse_nested_tables(root_tbl, node="claude", path=tmp_path / "agent.yaml")
    msg = str(exc.value)
    assert "then delete" not in msg
    assert "FIRST" in msg
    assert msg.index("FIRST") < msg.index("Fix: "), "the delete must precede the set"


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
# 4 · the printed cure is a command the CLI ACCEPTS (printing it is not enough)
# --------------------------------------------------------------------------- #

def test_the_printed_reserved_default_cure_runs(tmp_path) -> None:
    cure = _retired_behavior_cure(
        "access", level="agent", tier="full", subject="default", node="default",
    )
    proc = _cli(tmp_path, *cure.split()[1:])
    assert proc.returncode == 0, f"{cure!r} was refused:\n{proc.stdout}{proc.stderr}"


def test_the_spelled_reserved_default_is_refused_by_the_cli(tmp_path) -> None:
    """Locks WHY the bare key is required: the old cure is a command that cannot run."""
    proc = _cli(tmp_path, "agent", "set", "default", "access=full")
    out = proc.stdout + proc.stderr
    assert proc.returncode == 1
    assert "reserved any-agent tier" in out


def test_the_bare_key_lands_on_the_any_agent_tier(tmp_path) -> None:
    """The cure must set the tier it names, not something adjacent."""
    assert _cli(tmp_path, "system", "set", "access=full").returncode == 0
    read = _cli(tmp_path, "system", "get", "agent.default.access")
    assert read.returncode == 0
    assert read.stdout.strip() == "agent.default.access=full"
