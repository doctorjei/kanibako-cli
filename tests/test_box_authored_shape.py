"""The two authored-``box`` readers that must ask the shape guard — one per harm class.

⚑ ``_read_box_image`` is a BEST-EFFORT read, ``carried_box_settings`` is a VERBATIM
copy, and the two failed in opposite directions on the same malformed input: the read
answered ``None`` (indistinguishable from "no image authored", and a list of pairs
answered as a table), the copy carried the value into a NEW box as if it were authored
settings.  Both refusals are :func:`refuse_scalar_sections` — the guard
:func:`read_box_enable_vault` already asks — so one stored ``box`` value has one answer
however it is reached.

The refusal is an ERROR because it happens on a READ: spec §0, *Namespace*, and §0's
"Anything outside it is REJECTED: reading, setting, or resolving an undeclared key is
an ERROR that NAMES the offending key — never a silent accept, never a fabricated
default, never a free-form passthrough."

Each door is checked through the REAL CLI in a subprocess (isolated ``HOME``/``XDG_*``,
assertions on the FILESYSTEM), because a unit test of a helper cannot show whether the
production call site is wired to it.  The bare ``box duplicate`` copies the source's
metadata VERBATIM, so it asks the guard before the copy, while a refusal can still land
with nothing written.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from kanibako.errors import ConfigError

#: The subprocess must import THIS src, not an installed checkout.
REPO_SRC = Path(__file__).resolve().parents[1] / "src"

#: A ``box`` section that is not a table, one per YAML shape.  ``refuse_scalar_sections``
#: answers from ``isinstance``, so the corpus is a pin on the RULE, not an inventory.
NON_TABLE_BOX_BODIES = [
    pytest.param("box:\n", id="null"),
    pytest.param("box: ''\n", id="empty-string"),
    pytest.param("box: 0\n", id="zero"),
    pytest.param("box: false\n", id="false"),
    pytest.param("box: true\n", id="true"),
    pytest.param("box: 42\n", id="nonzero-int"),
    pytest.param("box: /x\n", id="path-string"),
    pytest.param("box: []\n", id="empty-list"),
    pytest.param("box: [image]\n", id="list-holding-the-key"),
    pytest.param("box: [[image, ubuntu]]\n", id="list-of-pairs"),
    pytest.param("box: 'image: ubuntu'\n", id="string-holding-the-key"),
]


def _expected_refusal(path: Path, body: str) -> str:
    """The one refusal wording both readers must produce for *body*."""
    from kanibako.settings.config_io import load_doc, render_stored_scalar

    stored = render_stored_scalar(load_doc(_write(path, body))["box"])
    return (
        f"the config file {path} holds {stored} at 'box', where a table of keys belongs, "
        f"so 'box.' keys cannot be written under it. "
        f"Fix or delete 'box' in that file by hand, then retry."
    )


def _write(path: Path, body: str) -> Path:
    path.write_text(body)
    return path


class TestReadBoxImageAsksTheShapeGuard:
    """``_read_box_image`` — a malformed ``box`` is NAMED, never answered ``None``."""

    @pytest.mark.parametrize("body", NON_TABLE_BOX_BODIES)
    def test_a_non_table_box_refuses_by_name(self, tmp_path, body):
        """A ``box`` that is not a table refuses by name; the file is byte-identical.

        The read is best-effort only for a file it cannot READ.  A file it reads and
        finds malformed is a different fact, and reporting it as "no image authored"
        parks a recovery blob that cannot restore the box.
        MUTATION: drop the ``refuse_scalar_sections`` call in ``_read_box_image`` and
        every row here reds by not raising.
        """
        from kanibako.commands.box._parser import _read_box_image

        p = _write(tmp_path / "box.yaml", body)

        with pytest.raises(ConfigError) as exc:
            _read_box_image(p)
        assert str(exc.value) == _expected_refusal(p, body)
        assert p.read_text() == body

    def test_a_list_of_pairs_is_not_read_as_a_table(self, tmp_path):
        """``box: [[image, ubuntu]]`` REFUSES — it is not a table of keys.

        ⚑ THE ROW THAT DECIDES IT.  A sequence of pairs is coercible to a table, so a
        reader that coerces reads an image out of a section the keyspace does not
        define; refusing is what keeps one stored ``box`` value from having two answers.
        MUTATION: restore ``dict(data.get("box", {}))`` and this reds, returning
        ``'ubuntu'``.
        """
        from kanibako.commands.box._parser import _read_box_image

        p = _write(tmp_path / "box.yaml", "box: [[image, ubuntu]]\n")

        with pytest.raises(ConfigError) as exc:
            _read_box_image(p)
        assert "at 'box', where a table of keys belongs" in str(exc.value)
        assert p.read_text() == "box: [[image, ubuntu]]\n"

    @pytest.mark.parametrize("body", [
        pytest.param("box:\n  image: custom:v1\n", id="table"),
        pytest.param("box:\n  enable_vault: false\n", id="table-without-image"),
    ])
    def test_a_table_box_still_reads_its_image(self, tmp_path, body):
        """A TABLE ``box`` is still read — the guard refuses shapes, not sections."""
        from kanibako.commands.box._parser import _read_box_image

        p = _write(tmp_path / "box.yaml", body)
        assert _read_box_image(p) == ("custom:v1" if "image" in body else None)

    @pytest.mark.parametrize("name,body", [
        pytest.param("absent.yaml", None, id="absent-file"),
        pytest.param("unreadable.yaml", "box: [\n", id="unparseable-yaml"),
        pytest.param("empty.yaml", "", id="empty-file"),
    ])
    def test_a_file_the_reader_cannot_read_is_still_none(self, tmp_path, name, body):
        """BEST-EFFORT, preserved: a file it cannot read answers ``None``, never raises.

        ⚑ The guard is asked about SHAPE, not about readability, so this arm is the
        one that must NOT refuse — without it a transient read failure would read as a
        malformed settings file.
        MUTATION: widen the ``except`` to cover the guard and this reds with
        ``ConfigError`` on the unparseable row.
        """
        from kanibako.commands.box._parser import _read_box_image

        p = tmp_path / name
        if body is not None:
            p.write_text(body)
        assert _read_box_image(p) is None


class TestCarriedBoxSettingsAsksTheShapeGuard:
    """``carried_box_settings`` — a value is not carried as if it were a key."""

    @pytest.mark.parametrize("body", NON_TABLE_BOX_BODIES)
    def test_a_non_table_box_refuses_by_name(self, tmp_path, body):
        """A ``box`` that is not a table refuses by name; the source is byte-identical.

        ⚑ THE BOUNDARY.  This is where a source box's box tier becomes a NEW box's
        authored settings, so a value where the ``box`` table belongs must not cross —
        it would otherwise land in the destination as authored settings with rc 0.
        MUTATION: drop the ``refuse_scalar_sections`` call in ``carried_box_settings``
        and every row here reds by not raising.
        """
        from kanibako.settings.config import carried_box_settings

        p = _write(tmp_path / "box.yaml", body)

        with pytest.raises(ConfigError) as exc:
            carried_box_settings(p)
        assert str(exc.value) == _expected_refusal(p, body)
        assert p.read_text() == body

    def test_a_table_box_is_carried_and_the_workset_strip_still_holds(self, tmp_path):
        """A TABLE ``box`` is still carried, and a ``workset:`` is still stripped."""
        from kanibako.settings.config import carried_box_settings

        p = _write(tmp_path / "box.yaml", (
            "box:\n  image: custom:v1\n  enable_vault: false\nworkset:\n  kuid: wks_1\n"
        ))
        assert carried_box_settings(p) == {
            "box": {"image": "custom:v1", "enable_vault": False},
        }

    def test_an_absent_file_carries_nothing(self, tmp_path):
        """A box tier that is not a file carries nothing — the guard must not refuse it."""
        from kanibako.settings.config import carried_box_settings

        assert carried_box_settings(tmp_path / "absent.yaml") == {}


def _make_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Build an isolated HOME/XDG tree + a default config; return the child env."""
    dirs = {n: tmp_path / n for n in (
        "home", "config", "data", "state", "cache", "runtime", "ws")}
    for d in dirs.values():
        d.mkdir()
    env = os.environ.copy()
    env.update({
        "HOME": str(dirs["home"]),
        "XDG_CONFIG_HOME": str(dirs["config"]),
        "XDG_DATA_HOME": str(dirs["data"]),
        "XDG_STATE_HOME": str(dirs["state"]),
        "XDG_CACHE_HOME": str(dirs["cache"]),
        "XDG_RUNTIME_DIR": str(dirs["runtime"]),
        "PYTHONPATH": str(REPO_SRC),
    })
    for key, value in env.items():
        if key in ("HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME",
                   "XDG_CACHE_HOME", "XDG_RUNTIME_DIR"):
            monkeypatch.setenv(key, value)

    from kanibako.settings.config import write_global_config
    from tests.support.filenames import CONFIG_FILENAME

    write_global_config(dirs["config"] / CONFIG_FILENAME)
    return env


def _cli(env: dict[str, str], *args: str,
         stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    """Run the REAL CLI (``python -m kanibako``) in a subprocess."""
    return subprocess.run(
        [sys.executable, "-m", "kanibako", *args],
        env=env, capture_output=True, text=True, timeout=300, check=False,
        input=stdin,
    )


def _std():
    """The REAL resolved paths for the tree the env points at."""
    from kanibako.settings.config import load_config, user_config_file
    from kanibako.settings.paths import load_std_paths

    return load_std_paths(load_config(user_config_file()))


def _make_box(name: str, root: Path) -> Path:
    """Create a REAL primary box *name* through the product's own writer."""
    from kanibako.settings.config import load_config, user_config_file
    from kanibako.settings.paths import resolve_project

    root.mkdir(parents=True, exist_ok=True)
    resolve_project(_std(), load_config(user_config_file()),
                    project_dir=str(root), initialize=True, name_override=name)
    return _std().boxes / name


def _make_standalone_box(root: Path) -> str:
    """Create a REAL standalone box at *root*; return its registered name."""
    from kanibako.project import registry_store
    from kanibako.settings.config import load_config, user_config_file
    from kanibako.settings.paths import resolve_standalone_project

    root.mkdir(parents=True, exist_ok=True)
    resolve_standalone_project(_std(), load_config(user_config_file()), str(root),
                               initialize=True)
    name = registry_store.standalone_name_for_root(_std().registry, root)
    assert name is not None
    return name


def _primary_index() -> dict[str, str]:
    """The REAL primary name index, as ``box rm`` reads it."""
    from kanibako.settings.paths import BoxMode, _early_scope, load_primary_boxes

    std = _std()
    return load_primary_boxes(std.primary_workset, early=_early_scope(std, BoxMode.primary))


class TestRealDoors:
    """Both refusals reached through the real CLI, asserted on the FILESYSTEM."""

    def test_box_rm_refuses_a_scalar_box_tier_while_the_box_is_still_registered(
        self, tmp_path, monkeypatch,
    ):
        """``box rm`` on a malformed box tier refuses BEFORE the unregister.

        ⚑ THE DOOR.  ``box rm`` without ``--purge`` retains metadata and parks a
        ``deregistered`` row whose ``image`` this read supplies.  The refusal must fire
        while the box is still in the primary index: after the unregister no readopt
        row would be parked, and nothing in the CLI could find the box again.
        MUTATION: drop the guard in ``_read_box_image`` and this reds with rc 0; move
        the read back below ``unregister_primary_box_name`` and it reds on the index.
        """
        from kanibako.project import registry_store

        env = _make_env(tmp_path, monkeypatch)
        boxes = _make_box("scalarbox", tmp_path / "ws" / "scalarbox")
        tier = boxes / "box.yaml"
        tier.write_text("box: /x\n")

        res = _cli(env, "box", "rm", "scalarbox")

        assert res.returncode == 1, res.stdout + res.stderr
        assert "holds /x at 'box', where a table of keys belongs" in res.stderr
        assert f"{tier}" in res.stderr, "the refusal must NAME the file to fix by hand"
        assert tier.read_text() == "box: /x\n", "the malformed file was rewritten"
        assert "scalarbox" in _primary_index(), "the refusal left the box unregistered"
        assert registry_store.load_deregistered(_std().registry) == {}

        tier.write_text("box: {}\n")
        res = _cli(env, "box", "rm", "scalarbox")
        assert res.returncode == 0, res.stdout + res.stderr
        assert "scalarbox" not in _primary_index()
        res = _cli(env, "box", "register", "scalarbox")
        assert res.returncode == 0, res.stdout + res.stderr
        assert "scalarbox" in _primary_index()

    def test_standalone_box_rm_refuses_a_scalar_box_tier_while_still_registered(
        self, tmp_path, monkeypatch,
    ):
        """The standalone ``box rm`` refuses BEFORE ``unregister_standalone``.

        MUTATION: move the ``_read_box_image_tiered`` read back below the unregister
        and this reds on the standalone registry.
        """
        from kanibako.project import registry_store
        from kanibako.settings.paths import BoxMode, _early_scope, _standalone_settings_files

        env = _make_env(tmp_path, monkeypatch)
        root = tmp_path / "ws" / "sabox"
        name = _make_standalone_box(root)
        tier, _ = _standalone_settings_files(root, early=_early_scope(_std(), BoxMode.standalone))
        tier.write_text("box: /x\n")

        res = _cli(env, "box", "rm", name)

        assert res.returncode == 1, res.stdout + res.stderr
        assert "holds /x at 'box', where a table of keys belongs" in res.stderr
        assert tier.read_text() == "box: /x\n"
        assert name in registry_store.load_standalone(_std().registry), (
            "the refusal left the box unregistered"
        )
        assert registry_store.load_deregistered(_std().registry) == {}

        tier.write_text("box: {}\n")
        res = _cli(env, "box", "rm", name)
        assert res.returncode == 0, res.stdout + res.stderr
        assert name not in registry_store.load_standalone(_std().registry)
        res = _cli(env, "box", "register", name)
        assert res.returncode == 0, res.stdout + res.stderr
        assert name in registry_store.load_standalone(_std().registry)

    def test_box_duplicate_refuses_a_scalar_box_tier_before_any_side_effect(
        self, tmp_path, monkeypatch,
    ):
        """``box duplicate --to primary`` refuses before it registers or copies anything.

        ⚑ THE DOOR.  ``--to primary`` is the route that reads the source's box tier and
        writes the destination's.  The read must come before the name assignment and
        the workspace copy: the caller unwinds only ``ProjectError``/``OSError``, so a
        later refusal left an orphan name and workspace that blocked the retry.
        MUTATION: drop the guard in ``carried_box_settings`` and this reds with rc 0;
        move the read back into ``_duplicate_to_local`` and it reds on the orphan.
        """
        env = _make_env(tmp_path, monkeypatch)
        src = tmp_path / "ws" / "srcbox"
        src_boxes = _make_box("srcbox", src)
        (src / "file.txt").write_text("payload\n")
        (src_boxes / "box.yaml").write_text("box: /x\n")
        dst = tmp_path / "ws" / "dstbox"

        res = _cli(env, "box", "duplicate", str(src), str(dst),
                   "--to", "primary", stdin="yes\n")

        assert res.returncode == 1, res.stdout + res.stderr
        assert "holds /x at 'box', where a table of keys belongs" in res.stderr
        assert set(_primary_index()) == {"srcbox"}, "the refusal left a name behind"
        assert not dst.exists(), "the refusal left the workspace copy behind"
        assert not (_std().boxes / "dstbox").exists()

        (src_boxes / "box.yaml").write_text("box: {}\n")
        res = _cli(env, "box", "duplicate", str(src), str(dst),
                   "--to", "primary", stdin="yes\n")
        assert res.returncode == 0, res.stdout + res.stderr
        assert (dst / "file.txt").read_text() == "payload\n"
        assert len(_primary_index()) == 2


class TestBareDuplicateAsksTheShapeGuard:
    """A malformed source ``box`` refuses, and the refusal writes nothing."""

    @pytest.mark.parametrize("body", [
        pytest.param("box: /x\n", id="path-string"),
        pytest.param("box:\n", id="null"),
        pytest.param("box: [[image, ubuntu]]\n", id="list-of-pairs"),
    ])
    def test_a_non_table_box_refuses_before_anything_is_written(
        self, tmp_path, monkeypatch, body,
    ):
        """The refusal lands with no workspace copy, no name and no metadata written.

        ⚑ THE BOUNDARY.  This path copies the metadata verbatim, so the new box's box
        tier is the source's file: a value where the ``box`` table belongs would land
        there as the new box's authored settings.  The guard is asked before the
        workspace copy, the name registration and the metadata copy, so nothing is
        unwound and nothing needs to be.
        MUTATION: delete the ``refuse_scalar_sections`` call in ``run_duplicate`` and
        every row here reds with rc 0 and a destination holding the value; move the call
        below ``assign_primary_box_name`` and it reds on the index.
        """
        env = _make_env(tmp_path, monkeypatch)
        src = tmp_path / "ws" / "srcbox"
        tier = _make_box("srcbox", src) / "box.yaml"
        (src / "file.txt").write_text("payload\n")
        _write(tier, body)
        dst = tmp_path / "ws" / "dstbox"

        res = _cli(env, "box", "duplicate", str(src), str(dst), stdin="yes\n")

        assert res.returncode == 1, res.stdout + res.stderr
        assert res.stderr.strip() == "Error: " + _expected_refusal(tier, body)
        assert tier.read_text() == body, "the malformed file was rewritten"
        assert not dst.exists(), "the refusal left the workspace copy behind"
        assert not (_std().boxes / "dstbox").exists()
        assert set(_primary_index()) == {"srcbox"}, "the refusal left a name behind"

    def test_a_list_of_pairs_is_not_carried_into_the_new_box(self, tmp_path, monkeypatch):
        """``box: [[image, ubuntu]]`` refuses — a sequence of pairs is not a table.

        ⚑ THE ROW THAT DECIDES IT.  A sequence of pairs is coercible to a table, so a
        copy that accepts it reads settings out of a shape the keyspace does not define,
        and the new box inherits them as authored.
        MUTATION: coerce the source ``box`` to a table before the guard asks and this
        reds with rc 0.
        """
        env = _make_env(tmp_path, monkeypatch)
        src = tmp_path / "ws" / "srcbox"
        tier = _make_box("srcbox", src) / "box.yaml"
        _write(tier, "box: [[image, ubuntu]]\n")
        dst = tmp_path / "ws" / "dstbox"

        res = _cli(env, "box", "duplicate", str(src), str(dst), stdin="yes\n")

        assert res.returncode == 1, res.stdout + res.stderr
        assert "at 'box', where a table of keys belongs" in res.stderr
        assert not (_std().boxes / "dstbox").exists()

    def test_force_does_not_buy_a_malformed_box_tier(self, tmp_path, monkeypatch):
        """``--force`` cannot carry a value, and the occupied destination survives.

        ⚑ ``--force`` removes the destination's box store before copying, so a refusal
        that arrived late would destroy the box it was protecting.
        MUTATION: move the guard below the ``--force`` removal and this reds with the
        retained data gone.
        """
        env = _make_env(tmp_path, monkeypatch)
        src = tmp_path / "ws" / "srcbox"
        tier = _make_box("srcbox", src) / "box.yaml"
        _write(tier, "box: /x\n")
        dst = tmp_path / "ws" / "dstbox"
        dst.mkdir()
        (dst / "keepme.txt").write_text("retained\n")

        res = _cli(env, "box", "duplicate", str(src), str(dst), "--force", stdin="yes\n")

        assert res.returncode == 1, res.stdout + res.stderr
        assert "holds /x at 'box', where a table of keys belongs" in res.stderr
        assert (dst / "keepme.txt").read_text() == "retained\n"
        assert set(_primary_index()) == {"srcbox"}


class TestBareDuplicateStillCopies:
    """The guard refuses SHAPES, not the door — every well-formed tier still copies."""

    @pytest.mark.parametrize("body", [
        pytest.param("", id="no-box-section"),
        pytest.param("box: {}\n", id="empty-table"),
        pytest.param("box:\n  image: custom:v1\n", id="table"),
        pytest.param("box:\n  image: custom:v1\n  enable_vault: false\n", id="table-two-keys"),
    ])
    def test_a_well_formed_tier_survives_the_round_trip_unchanged(
        self, tmp_path, monkeypatch, body,
    ):
        """The new box's tier is BYTE-IDENTICAL to the source's — verbatim is the contract.

        ⚑ A guard that refused a well-formed ``box:`` table would make the bare form
        undupable for every box that has settings, so this row pins the other half of the
        door: the copy still happens and changes nothing.
        MUTATION: raise on any non-ABSENT ``box`` instead of on a non-table one and every
        row here reds with rc 1.
        """
        env = _make_env(tmp_path, monkeypatch)
        src = tmp_path / "ws" / "srcbox"
        tier = _make_box("srcbox", src) / "box.yaml"
        _write(tier, body)
        (src / "file.txt").write_text("payload\n")
        dst = tmp_path / "ws" / "dstbox"

        res = _cli(env, "box", "duplicate", str(src), str(dst), stdin="yes\n")

        assert res.returncode == 0, res.stdout + res.stderr
        copied = _std().boxes / "dstbox" / "box.yaml"
        assert copied.read_text() == body, "the copy did not survive verbatim"
        assert (dst / "file.txt").read_text() == "payload\n"
        assert set(_primary_index()) == {"srcbox", "dstbox"}

    def test_a_destination_that_already_exists_is_still_refused(self, tmp_path, monkeypatch):
        """A well-formed source onto an existing destination keeps the destination's refusal."""
        env = _make_env(tmp_path, monkeypatch)
        src = tmp_path / "ws" / "srcbox"
        tier = _make_box("srcbox", src) / "box.yaml"
        _write(tier, "box:\n  image: custom:v1\n")
        dst = tmp_path / "ws" / "dstbox"
        dst.mkdir()
        (dst / "keepme.txt").write_text("retained\n")

        res = _cli(env, "box", "duplicate", str(src), str(dst), stdin="yes\n")

        assert res.returncode == 1, res.stdout + res.stderr
        assert f"destination already exists: {dst}" in res.stderr
        assert (dst / "keepme.txt").read_text() == "retained\n"
        assert set(_primary_index()) == {"srcbox"}
