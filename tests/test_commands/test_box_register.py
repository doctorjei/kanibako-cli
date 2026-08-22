"""Tests for ``kanibako box register`` — the readopt / late-standalone verb (I2).

``register`` is INDEX-ONLY and SEED-FREE: it unifies (a) READOPT of a box
deregistered by ``rm`` (no ``--purge``) — moving it from the global
``deregistered`` section back to active membership — and (b) late REGISTER of a
standalone box that exists on disk but carries no ``registry.standalone`` entry.
It NEVER re-seeds or touches the box's home content (membership is itself the
seed signal), and it refuses to clobber an active box that already owns the
target name/workspace.

These are REAL-path tests (real ``std``/resolver/registry); ``register`` writes
only the registry index, so no container/runtime stubbing is needed.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

from kanibako.project import registry_store
from kanibako.commands.box._parser import run_create, run_register, run_rm
from kanibako.project.names import resolve_name, register_name


# ---------------------------------------------------------------------------
# Namespace + snapshot helpers
# ---------------------------------------------------------------------------

def _create_args(path, **over):
    ns = argparse.Namespace(
        path=str(path), standalone=False, no_vault=True,
        name=None, image=None, agent=None, allow_home=False,
        private=False, force=False, register=False,
    )
    for k, v in over.items():
        setattr(ns, k, v)
    return ns


# A valid kuid (5 Crockford base32 chars, odd parity) used to build a VERBATIM
# canonical ``<kuid>_<leaf>`` ``--name``, the one form that outlives the create.
_A_KUID = "pznvh"


def _register_args(target, **over):
    ns = argparse.Namespace(target=str(target), box=None, force=False)
    for k, v in over.items():
        setattr(ns, k, v)
    return ns


def _rm_args(target, **over):
    ns = argparse.Namespace(
        target=str(target), box=None, purge=False, force=False,
    )
    for k, v in over.items():
        setattr(ns, k, v)
    return ns


def _std(config_file):
    from kanibako.settings.config import load_config
    from kanibako.settings.paths import load_std_paths

    config = load_config(config_file)
    return config, load_std_paths(config)


def _tree_digest(root: Path) -> dict[str, str]:
    """Map every file under *root* to a content hash (for mutation-proving).

    Returns ``{relpath: sha256}`` — byte-identical trees produce equal maps, so
    a before/after comparison proves the tree was untouched.
    """
    out: dict[str, str] = {}
    if not root.exists():
        return out
    for p in sorted(root.rglob("*")):
        if p.is_file():
            rel = str(p.relative_to(root))
            out[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


# ---------------------------------------------------------------------------
# PRIMARY readopt: restores active membership + resolve works again
# ---------------------------------------------------------------------------

class TestReadoptPrimary:
    def test_readopt_restores_membership_and_resolves(
        self, config_file, tmp_home, credentials_dir
    ):
        from kanibako.settings.paths import load_primary_boxes

        config, std = _std(config_file)
        proj_dir = tmp_home / "proj"
        proj_dir.mkdir()

        assert run_create(_create_args(proj_dir, name="mybox")) == 0
        # Deregister (rm without --purge parks a deregistered entry).
        assert run_rm(_rm_args("mybox")) == 0
        assert load_primary_boxes(std.primary_workset) == {}  # membership gone
        assert "mybox" in registry_store.load_deregistered(std.registry)

        # Readopt.
        assert run_register(_register_args("mybox")) == 0

        # Active membership restored → resolve_name finds the box again.
        boxes = load_primary_boxes(std.primary_workset)
        assert boxes.get("mybox") == str(proj_dir)
        path, kind = resolve_name(
            std.registry, "mybox", primary_workset=std.primary_workset,
        )
        assert kind == "project"
        assert Path(path) == proj_dir

    def test_readopt_drops_deregistered_entry(
        self, config_file, tmp_home, credentials_dir
    ):
        config, std = _std(config_file)
        proj_dir = tmp_home / "proj"
        proj_dir.mkdir()
        assert run_create(_create_args(proj_dir, name="mybox")) == 0
        assert run_rm(_rm_args("mybox")) == 0
        assert "mybox" in registry_store.load_deregistered(std.registry)

        assert run_register(_register_args("mybox")) == 0
        # The deregistered entry is gone after readopt.
        assert registry_store.load_deregistered(std.registry) == {}

    def test_rm_register_round_trip(
        self, config_file, tmp_home, credentials_dir
    ):
        """rm (deregister) → register (readopt) → box resolves active again."""
        config, std = _std(config_file)
        proj_dir = tmp_home / "proj"
        proj_dir.mkdir()
        assert run_create(_create_args(proj_dir, name="loop")) == 0

        assert run_rm(_rm_args("loop")) == 0
        # While deregistered, the bare name no longer resolves as active.
        from kanibako.errors import ProjectError
        import pytest
        with pytest.raises(ProjectError):
            resolve_name(std.registry, "loop", primary_workset=std.primary_workset)

        assert run_register(_register_args("loop")) == 0
        path, kind = resolve_name(
            std.registry, "loop", primary_workset=std.primary_workset,
        )
        assert kind == "project"
        assert Path(path) == proj_dir


# ---------------------------------------------------------------------------
# ⚑ SEED-FREE: register NEVER touches the box home content (mutation-prove)
# ---------------------------------------------------------------------------

class TestRegisterNeverReseeds:
    def test_primary_readopt_leaves_box_home_byte_identical(
        self, config_file, tmp_home, credentials_dir
    ):
        config, std = _std(config_file)
        proj_dir = tmp_home / "proj"
        proj_dir.mkdir()
        assert run_create(_create_args(proj_dir, name="mybox")) == 0
        assert run_rm(_rm_args("mybox")) == 0

        # Snapshot the box metadata tree (home + everything under boxes/<name>)
        # AND the workspace BEFORE readopt.
        box_dir = std.boxes / "mybox"
        before_box = _tree_digest(box_dir)
        before_ws = _tree_digest(proj_dir)
        assert before_box  # the seed produced content to protect

        assert run_register(_register_args("mybox")) == 0

        # Readopt wrote ONLY the registry index — home + workspace untouched.
        assert _tree_digest(box_dir) == before_box
        assert _tree_digest(proj_dir) == before_ws

    def test_standalone_register_later_leaves_home_byte_identical(
        self, config_file, tmp_home, credentials_dir
    ):
        from kanibako.settings.paths import resolve_standalone_project

        config, std = _std(config_file)
        root = tmp_home / "sa"
        root.mkdir()
        # Create a real standalone box on disk, then wipe the standalone index so
        # it is on-disk-but-unregistered (the register-later scenario).
        resolve_standalone_project(std, config, str(root), initialize=True)
        registry_store.save_section(std.registry, "standalone", {})

        box_home = root / "box_data"
        before = _tree_digest(box_home)
        assert before

        assert run_register(_register_args(root)) == 0

        # Registered (index entry added) but the on-disk box home is untouched.
        assert registry_store.load_standalone(std.registry)  # now indexed
        assert _tree_digest(box_home) == before


# ---------------------------------------------------------------------------
# STANDALONE register-later (on-disk → indexed)
# ---------------------------------------------------------------------------

class TestStandaloneRegisterLater:
    def test_registers_unregistered_standalone_by_path(
        self, config_file, tmp_home, credentials_dir
    ):
        from kanibako.settings.paths import resolve_standalone_project

        config, std = _std(config_file)
        root = tmp_home / "sa"
        root.mkdir()
        proj = resolve_standalone_project(
            std, config, str(root), initialize=True,
        )
        name = proj.name
        registry_store.save_section(std.registry, "standalone", {})
        assert name not in registry_store.load_standalone(std.registry)

        assert run_register(_register_args(root)) == 0
        assert registry_store.load_standalone(std.registry).get(name) == str(
            root.resolve()
        )

    def test_already_registered_standalone_by_path_is_gentle_noop(
        self, config_file, tmp_home, credentials_dir
    ):
        from kanibako.settings.paths import resolve_standalone_project

        config, std = _std(config_file)
        root = tmp_home / "sa"
        root.mkdir()
        resolve_standalone_project(std, config, str(root), initialize=True)
        before = registry_store.load_standalone(std.registry)

        # Already registered → gentle "already registered", rc 0, no change.
        assert run_register(_register_args(root)) == 0
        assert registry_store.load_standalone(std.registry) == before

    def test_readopt_standalone_deregistered_by_name(
        self, config_file, tmp_home, credentials_dir
    ):
        from kanibako.settings.paths import resolve_standalone_project

        config, std = _std(config_file)
        root = tmp_home / "sa"
        root.mkdir()
        proj = resolve_standalone_project(
            std, config, str(root), initialize=True,
        )
        name = proj.name
        # Deregister by name (rm parks a standalone deregistered entry).
        assert run_rm(_rm_args(name)) == 0
        assert name in registry_store.load_deregistered(std.registry)
        assert name not in registry_store.load_standalone(std.registry)

        # Readopt by name restores the standalone index + drops the entry.
        assert run_register(_register_args(name)) == 0
        assert registry_store.load_standalone(std.registry).get(name) == str(
            root.resolve()
        )
        assert name not in registry_store.load_deregistered(std.registry)


# ---------------------------------------------------------------------------
# Conflict-safety: never clobber an ACTIVE box on the same name/workspace
# ---------------------------------------------------------------------------

class TestConflictSafety:
    def test_readopt_refused_when_active_box_owns_name(
        self, config_file, tmp_home, credentials_dir, capsys
    ):
        from kanibako.settings.paths import load_primary_boxes, register_primary_box_name

        config, std = _std(config_file)
        # Box A named "dup" → deregister it (retained metadata at std.boxes/dup).
        dir_a = tmp_home / "a"
        dir_a.mkdir()
        assert run_create(_create_args(dir_a, name="dup")) == 0
        assert run_rm(_rm_args("dup")) == 0

        # A NEW active box also named "dup" at a different workspace.  I4 now
        # BLOCKS `create --name dup` over the deregistered home (that was the
        # data-loss hole), so build the split-brain state directly: register the
        # active "dup" membership at dir_b (an index-only write, no home reuse).
        dir_b = tmp_home / "b"
        dir_b.mkdir()
        register_primary_box_name(
            std.primary_workset, std.registry, "dup", str(dir_b),
        )
        capsys.readouterr()

        # Readopt of the deregistered "dup" must REFUSE (active box owns the name)
        # and leave the active box's membership untouched.
        rc = run_register(_register_args("dup"))
        assert rc == 1
        err = capsys.readouterr().err
        assert "already registered" in err.lower() or "dup" in err
        # The active box is unchanged; the deregistered entry is preserved (not
        # silently dropped) so the user can still purge it.
        assert load_primary_boxes(std.primary_workset).get("dup") == str(dir_b)
        assert "dup" in registry_store.load_deregistered(std.registry)

    def test_standalone_register_refused_on_name_collision(
        self, config_file, tmp_home, credentials_dir, capsys
    ):
        from kanibako.settings.paths import resolve_standalone_project

        config, std = _std(config_file)
        root = tmp_home / "sa"
        root.mkdir()
        proj = resolve_standalone_project(
            std, config, str(root), initialize=True,
        )
        name = proj.name
        # Point the box's composed name at a DIFFERENT root in the index, then
        # wipe this root's entry: registering it must refuse (name collision).
        registry_store.save_section(
            std.registry, "standalone", {name: "/some/other/root"},
        )
        capsys.readouterr()

        rc = run_register(_register_args(root))
        assert rc == 1
        assert "already registered" in capsys.readouterr().err.lower()
        # The colliding entry is untouched.
        assert registry_store.load_standalone(std.registry) == {
            name: "/some/other/root"
        }


# ---------------------------------------------------------------------------
# Clear errors: nothing-to-register + worksets refused + already-active
# ---------------------------------------------------------------------------

class TestClearErrors:
    def test_nothing_to_register(self, config_file, tmp_home, credentials_dir, capsys):
        config, std = _std(config_file)
        rc = run_register(_register_args("ghost"))
        assert rc == 1
        assert "nothing to register" in capsys.readouterr().err.lower()

    def test_already_active_primary_is_gentle_noop(
        self, config_file, tmp_home, credentials_dir, capsys
    ):
        config, std = _std(config_file)
        proj_dir = tmp_home / "proj"
        proj_dir.mkdir()
        assert run_create(_create_args(proj_dir, name="live")) == 0
        capsys.readouterr()

        rc = run_register(_register_args("live"))
        assert rc == 0
        assert "already registered" in capsys.readouterr().out.lower()

    def test_workset_name_refused(
        self, config_file, tmp_home, credentials_dir, capsys
    ):
        config, std = _std(config_file)
        ws_root = tmp_home / "ws"
        ws_root.mkdir()
        register_name(std.registry, "myws", str(ws_root), section="worksets")
        capsys.readouterr()

        rc = run_register(_register_args("myws"))
        assert rc == 1
        err = capsys.readouterr().err
        assert "workset" in err.lower()
        # Untouched.
        assert "myws" in registry_store.load_registry(std.registry)["worksets"]


# ---------------------------------------------------------------------------
# Self-heal: a deregistered entry whose metadata is gone drops on readopt
# ---------------------------------------------------------------------------

class TestSelfHeal:
    def test_readopt_drops_stale_primary_entry_when_metadata_gone(
        self, config_file, tmp_home, credentials_dir, capsys
    ):
        from kanibako.runtime.container import remove_box_tree

        config, std = _std(config_file)
        proj_dir = tmp_home / "proj"
        proj_dir.mkdir()
        assert run_create(_create_args(proj_dir, name="mybox")) == 0
        assert run_rm(_rm_args("mybox")) == 0
        # Delete the retained box metadata out-of-band.
        #
        # ⚑ NOT a bare ``shutil.rmtree``. Since J-7 a created box home carries the
        # canon skeleton, and on a host where ``podman unshare`` works that skeleton
        # is subuid-owned and 555 — so ``rmtree`` raises ``PermissionError`` partway
        # through and this test fails. It passed everywhere ``podman unshare`` does
        # NOT work (the protect pass short-circuits before the chmod, leaving a plain
        # 755 tree), which is why it went red only in CI. ``remove_box_tree`` is the
        # sanctioned deleter and is exactly what the real ``rm --purge`` path uses.
        assert remove_box_tree(std.boxes / "mybox")
        capsys.readouterr()

        rc = run_register(_register_args("mybox"))
        assert rc == 1
        assert "nothing to restore" in capsys.readouterr().err.lower()
        # Stale entry self-healed away.
        assert "mybox" not in registry_store.load_deregistered(std.registry)


# ---------------------------------------------------------------------------
# I3 / §D4a: `create --standalone` registers only on --register
# ---------------------------------------------------------------------------

class TestCreateStandaloneOptIn:
    """``create --standalone`` no longer indexes the box; ``--register`` opts in.

    §D4a (owner 2026-07-04): the registry is the by-name-from-another-directory
    index and nothing else, so a standalone box is independent by default and
    moves freely.  ``--name`` names the registry entry, hence is a NO-OP without
    ``--register``.  An unregistered box is adopted later by the ``register``
    verb (I2) — index-only and seed-free.
    """

    @staticmethod
    def _stored_kuid(root: Path) -> str:
        from kanibako.settings.config import read_workset_kuid

        return read_workset_kuid(root / "workset.yaml")

    def test_default_create_is_unregistered(
        self, config_file, tmp_home, credentials_dir
    ):
        """THE FLIP: a plain ``create --standalone`` writes NO registry entry."""
        config, std = _std(config_file)
        root = tmp_home / "sa"
        root.mkdir()

        assert run_create(_create_args(root, standalone=True)) == 0

        # The box is on disk and complete...
        assert (root / "box_data" / "home").is_dir()
        assert (root / "workset.yaml").is_file()
        # ...and absent from the index.
        assert registry_store.load_standalone(std.registry) == {}
        assert registry_store.standalone_name_for_root(std.registry, root) is None

    def test_register_flag_indexes_the_box(
        self, config_file, tmp_home, credentials_dir
    ):
        """``--register`` writes the ``registry.standalone`` entry at create."""
        config, std = _std(config_file)
        root = tmp_home / "sa"
        root.mkdir()

        assert run_create(_create_args(root, standalone=True, register=True)) == 0

        registered = registry_store.load_standalone(std.registry)
        assert len(registered) == 1
        (name, entry), = registered.items()
        assert entry == str(root)
        # The composed identity: <stored kuid>_<leaf>.
        assert name.partition("_")[0] == self._stored_kuid(root)

    def test_register_honors_name(self, config_file, tmp_home, credentials_dir):
        """With ``--register``, ``--name`` sources the registry entry's leaf."""
        config, std = _std(config_file)
        root = tmp_home / "sa"
        root.mkdir()

        assert run_create(
            _create_args(root, standalone=True, register=True, name="chosen")
        ) == 0

        registered = registry_store.load_standalone(std.registry)
        (name,) = registered
        assert name.partition("_")[2] == "chosen"
        assert name.partition("_")[2] != root.name

    def test_name_is_a_noop_without_register(
        self, config_file, tmp_home, credentials_dir
    ):
        """⚑ §D4a: ``--name`` names the INDEX ENTRY, so with no entry it does nothing.

        Pinned on the one effect of ``--name`` that would otherwise OUTLIVE the
        create: a verbatim canonical ``<kuid>_<leaf>`` asserts the box's kuid, and
        that kuid is persisted as ``workset.kuid``.  Ignored, the box gets a fresh
        one.
        """
        config, std = _std(config_file)
        root = tmp_home / "sa"
        root.mkdir()
        supplied = f"{_A_KUID}_pinned"

        assert run_create(
            _create_args(root, standalone=True, name=supplied)
        ) == 0

        assert registry_store.load_standalone(std.registry) == {}
        stored = self._stored_kuid(root)
        assert stored and stored != _A_KUID

    def test_name_is_honored_verbatim_with_register(
        self, config_file, tmp_home, credentials_dir
    ):
        """The same canonical ``--name`` WITH ``--register`` is taken verbatim.

        The contrast half of the no-op pin: identical input, opposite outcome, so
        the difference is the flag and nothing else.
        """
        config, std = _std(config_file)
        root = tmp_home / "sa"
        root.mkdir()
        supplied = f"{_A_KUID}_pinned"

        assert run_create(
            _create_args(root, standalone=True, register=True, name=supplied)
        ) == 0

        assert registry_store.load_standalone(std.registry) == {supplied: str(root)}
        assert self._stored_kuid(root) == _A_KUID

    def test_unregistered_box_is_adopted_by_the_register_verb(
        self, config_file, tmp_home, credentials_dir, capsys
    ):
        """I2 is the opt-in-later half: ``box register <path>`` adopts the box.

        Seed-free — the home tree is byte-identical across the adoption.
        """
        config, std = _std(config_file)
        root = tmp_home / "sa"
        root.mkdir()
        assert run_create(_create_args(root, standalone=True)) == 0
        assert registry_store.load_standalone(std.registry) == {}
        before = _tree_digest(root / "box_data" / "home")
        capsys.readouterr()

        assert run_register(_register_args(root)) == 0

        registered = registry_store.load_standalone(std.registry)
        assert list(registered.values()) == [str(root)]
        assert _tree_digest(root / "box_data" / "home") == before

    def test_unregistered_create_prints_the_pasteable_cure(
        self, config_file, tmp_home, credentials_dir, capsys
    ):
        """The flip is SIGNALLED: v1.7.2 registered silently, so silence would
        report the old outcome.

        ⚑ The asserted command is closed with its quote on purpose.  The success
        line above it prints ``project_path`` (the ``workspace/`` SUBDIR), and an
        unterminated match would pass on that path too — while
        ``box register <root>/workspace`` finds no standalone marker and does NOT
        paste.
        """
        root = tmp_home / "sa"
        root.mkdir()

        assert run_create(_create_args(root, standalone=True)) == 0
        out = capsys.readouterr().out
        assert f"'kanibako box register {root}'" in out
        assert f"'kanibako box register {root / 'workspace'}'" not in out

        # The opposite arm: nothing to cure, so no hint.
        other = tmp_home / "sa2"
        other.mkdir()
        assert run_create(_create_args(other, standalone=True, register=True)) == 0
        assert "box register" not in capsys.readouterr().out

    def test_primary_create_still_registers_without_the_flag(
        self, config_file, tmp_home, credentials_dir
    ):
        """⚑ The gate is standalone-only: a PRIMARY box's membership IS its workset."""
        from kanibako.settings.paths import load_primary_boxes

        config, std = _std(config_file)
        proj_dir = tmp_home / "proj"
        proj_dir.mkdir()

        assert run_create(_create_args(proj_dir, name="mybox")) == 0

        assert load_primary_boxes(std.primary_workset).get("mybox") == str(proj_dir)
