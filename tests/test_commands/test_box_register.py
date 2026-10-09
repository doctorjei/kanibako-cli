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

import pytest

from kanibako.project import registry_store
from kanibako.launch.box_identity import box_name_reason
from kanibako.settings.messages import CURE_LEAF_NOT_ASCII
from kanibako.commands.box._parser import run_create, run_register, run_rm
from kanibako.project.names import resolve_name, register_name
from kanibako.settings.paths import load_primary_boxes
from kanibako.settings.paths import BoxMode, _early_scope


# ---------------------------------------------------------------------------
# Namespace + snapshot helpers
# ---------------------------------------------------------------------------

def _create_args(path, **over):
    ns = argparse.Namespace(
        path=str(path), standalone=False, no_vault=True,
        name=None, image=None, agent=None, allow_home=False,
        private=False, register=False,
    )
    for k, v in over.items():
        setattr(ns, k, v)
    return ns


# A valid kuid (5 Crockford base32 chars, odd parity) used to build a VERBATIM
# canonical ``<kuid>_<leaf>`` ``--name``, the one form that outlives the create.
_A_KUID = "pznvh"


def _register_args(target, **over):
    ns = argparse.Namespace(target=str(target), box=None)
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
        early = _early_scope(std, BoxMode.primary)
        assert load_primary_boxes(std.primary_workset, early=early) == {}  # membership gone
        assert "mybox" in registry_store.load_deregistered(std.registry)

        # Readopt.
        assert run_register(_register_args("mybox")) == 0

        # Active membership restored → resolve_name finds the box again.
        boxes = load_primary_boxes(std.primary_workset, early=early)
        assert boxes.get("mybox") == str(proj_dir)
        path, kind = resolve_name(
            std.registry, "mybox", primary_workset=std.primary_workset,
            early_system=std.early_system,
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
            resolve_name(
                std.registry, "loop", primary_workset=std.primary_workset,
                early_system=std.early_system,
            )

        assert run_register(_register_args("loop")) == 0
        path, kind = resolve_name(
            std.registry, "loop", primary_workset=std.primary_workset,
            early_system=std.early_system,
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
            std.primary_workset, "dup", str(dir_b),
            early=_early_scope(std, BoxMode.primary),
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
        assert load_primary_boxes(
            std.primary_workset, early=_early_scope(std, BoxMode.primary),
        ).get("dup") == str(dir_b)
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


class TestPathDesignationIsNeverAName:
    """A designation that breaks the box-name rule is a PATH (system-design §
    Detection & import), so ``rm`` and ``register`` never look it up by name."""

    def _hidden_box(self, config_file, tmp_home, monkeypatch):
        """A primary box an older ``create`` registered as ``.hidden``; cwd then moves to an empty dir."""
        config, std = _std(config_file)
        monkeypatch.chdir(tmp_home)
        (tmp_home / ".hidden").mkdir()
        with monkeypatch.context() as patch:  # the older create; the rule holds again after
            patch.setattr("kanibako.settings.paths.box_name_reason", lambda name: None)
            assert run_create(_create_args(".hidden")) == 0
        assert ".hidden" in load_primary_boxes(
            std.primary_workset, early=_early_scope(std, BoxMode.primary),
        )
        empty = tmp_home / "empty"
        empty.mkdir()
        monkeypatch.chdir(empty)
        return std

    def test_rm_does_not_resolve_it_by_name(
        self, config_file, tmp_home, credentials_dir, capsys, monkeypatch,
    ):
        std = self._hidden_box(config_file, tmp_home, monkeypatch)
        capsys.readouterr()
        assert run_rm(_rm_args(".HIDDEN")) == 1
        err = capsys.readouterr().err
        assert "not a registered box" not in err
        path = tmp_home / ".hidden"
        assert err == (
            f"Error: box name '.hidden' does not meet the naming rules "
            f"({box_name_reason('.hidden')}), so the box is reached by its path only. "
            f"Remove it, or give it a valid name:\n"
            f"  kanibako box rm {path}\n"
            f"  kanibako box move {path} <new-path> --name <new-name>\n"
        )
        assert ".hidden" in load_primary_boxes(
            std.primary_workset, early=_early_scope(std, BoxMode.primary),
        )

    def test_rm_of_an_existing_unboxed_dir_is_that_path_not_the_legacy_box(
        self, config_file, tmp_home, credentials_dir, capsys, monkeypatch,
    ):
        """The typed designation names a directory here, so the path wins."""
        self._hidden_box(config_file, tmp_home, monkeypatch)
        (tmp_home / "empty" / ".HIDDEN").mkdir()
        capsys.readouterr()
        assert run_rm(_rm_args(".HIDDEN")) == 1
        assert capsys.readouterr().err == "Error: '.HIDDEN' is not a registered box.\n"

    def test_rm_names_the_path_of_a_legacy_standalone_name(
        self, config_file, tmp_home, credentials_dir, capsys, monkeypatch,
    ):
        _, std = _std(config_file)
        monkeypatch.chdir(tmp_home)
        root = tmp_home / "my sa"
        registry_store.register_standalone(std.registry, "my sa", root)
        assert run_rm(_rm_args("my sa")) == 1
        assert capsys.readouterr().err.endswith(
            f"  kanibako box rm '{root}'\n"
            f"  kanibako box move '{root}' <new-path>\n")
        assert "my sa" in registry_store.load_standalone(std.registry)

    @pytest.mark.parametrize("stored, typed", [("strasse", "straße"), ("kit", "\u212ait")])
    def test_a_casefold_match_on_a_valid_name_is_not_legacy(
        self, stored, typed, config_file, tmp_home, credentials_dir, capsys, monkeypatch,
    ):
        """An invalid typed target that only casefolds onto a VALID stored name is a plain miss."""
        _, std = _std(config_file)
        monkeypatch.chdir(tmp_home)
        (tmp_home / stored).mkdir()
        assert run_create(_create_args(stored)) == 0
        capsys.readouterr()
        assert run_rm(_rm_args(typed)) == 1
        assert capsys.readouterr().err == f"Error: '{typed}' is not a registered box.\n"
        assert stored in load_primary_boxes(
            std.primary_workset, early=_early_scope(std, BoxMode.primary),
        )

    def test_register_does_not_resolve_it_by_name(
        self, config_file, tmp_home, credentials_dir, capsys, monkeypatch,
    ):
        self._hidden_box(config_file, tmp_home, monkeypatch)
        capsys.readouterr()
        assert run_register(_register_args(".hidden")) == 1
        captured = capsys.readouterr()
        assert "already registered" not in captured.out
        assert "nothing to register" in captured.err.lower()


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
    moves freely.  ``--name`` never names that entry — the KEY comes from the
    box's own directory — and on a standalone box it is REFUSED outright
    (ruling 2026-10-08).  An unregistered box is adopted later by the
    ``register`` verb (I2) — index-only and seed-free.
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

    def test_register_refuses_a_divergent_name(
        self, config_file, tmp_home, credentials_dir
    ):
        """⚑ INVERTED by ruling 2026-10-08.  This pinned ``--name`` SOURCING the
        entry's leaf; the ruling says the KEY is composed from the root, so a
        divergent ``--name`` is REFUSED rather than honored."""
        config, std = _std(config_file)
        root = tmp_home / "sa"
        root.mkdir()

        assert run_create(
            _create_args(root, standalone=True, register=True, name="chosen")
        ) == 1
        assert registry_store.load_standalone(std.registry) == {}
        assert not (root / "box_data").exists()
        assert not (root / "workset.yaml").exists()

    def test_name_is_refused_without_register(
        self, config_file, tmp_home, credentials_dir
    ):
        """⚑ INVERTED by ruling 2026-10-08.  ``--name`` used to be a silent no-op
        with no ``--register``.  A typed flag that does nothing is never silently
        ignored in 1.8.0, so it is refused with or without the flag."""
        config, std = _std(config_file)
        root = tmp_home / "sa"
        root.mkdir()
        supplied = f"{_A_KUID}_pinned"

        assert run_create(
            _create_args(root, standalone=True, name=supplied)
        ) == 1

        assert registry_store.load_standalone(std.registry) == {}
        assert not (root / "box_data").exists()
        assert not (root / "workset.yaml").exists()

    def test_verbatim_canonical_name_is_refused_at_create(
        self, config_file, tmp_home, credentials_dir
    ):
        """⚑ INVERTED by ruling 2026-10-08.  At CREATE there is no stored kuid to
        check a verbatim ``<kuid>_<leaf>`` against, so it is refused."""
        config, std = _std(config_file)
        root = tmp_home / "sa"
        root.mkdir()
        supplied = f"{_A_KUID}_pinned"

        assert run_create(
            _create_args(root, standalone=True, register=True, name=supplied)
        ) == 1

        assert registry_store.load_standalone(std.registry) == {}

    def test_non_ascii_root_takes_the_ascii_refusal_not_the_directory_rule(
        self, config_file, tmp_home, credentials_dir, capsys
    ):
        """Ruling 2026-10-09: on a non-ASCII standalone root, the ASCII refusal wins.

        ``refuse_nonleaf_standalone_name`` computes ``sanitize_cap(root.name)`` BEFORE it
        compares ``--name``, so a root with no ASCII spelling raises ``DerivedBoxNameError``
        first — with or without a typed name.  The door must let that error keep its own
        ``CURE_LEAF_NOT_ASCII`` and must NOT print the generic "rename the directory to
        rename the box" cure: that would send someone with a 東京 directory chasing the
        wrong problem.  Asserted against the imported constants, not a paraphrase, so the
        two messages cannot silently drift into one another.
        """
        config, std = _std(config_file)
        root = tmp_home / "東京"
        root.mkdir()

        assert run_create(
            _create_args(root, standalone=True, register=True, name="x")
        ) == 1

        err = capsys.readouterr().err
        assert CURE_LEAF_NOT_ASCII in err, err
        assert "cannot spell in ASCII" in err, err
        assert "rename the directory to rename the box" not in err, err

        assert registry_store.load_standalone(std.registry) == {}
        assert not (root / "box_data").exists()
        assert not (root / "workset.yaml").exists()

    def test_bare_leaf_name_is_accepted_as_a_noop(
        self, config_file, tmp_home, credentials_dir
    ):
        """The accepted no-op: ``--name`` equal to the directory's own basename."""
        config, std = _std(config_file)
        root = tmp_home / "sa"
        root.mkdir()

        assert run_create(
            _create_args(root, standalone=True, register=True, name="sa")
        ) == 0

        registered = registry_store.load_standalone(std.registry)
        (name,) = registered
        assert name == f"{self._stored_kuid(root)}_sa"

    def test_registry_key_is_the_recomposed_name(
        self, config_file, tmp_home, credentials_dir
    ):
        """THE REPORTED BUG, pinned: ``box list``'s key and ``box info``'s name
        were different strings.  The producer now composes the key from the root,
        so the two agree by construction — one carrier, the D1b principle."""
        config, std = _std(config_file)
        root = tmp_home / "widget"
        root.mkdir()

        assert run_create(
            _create_args(root, standalone=True, register=True)
        ) == 0

        from kanibako.launch.box_identity import compose_standalone_name

        (key,) = registry_store.load_standalone(std.registry)
        assert key == compose_standalone_name(self._stored_kuid(root), root)
        assert key.endswith("_widget")

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
        """The flip is SIGNALED: v1.7.2 registered silently, so silence would
        report the old outcome.

        ⚑ The asserted command is a WHOLE line on purpose.  The success line above
        it prints ``project_path`` (the ``workspace/`` SUBDIR), and an unterminated
        match would pass on that path too — while ``box register <root>/workspace``
        finds no standalone marker and does NOT paste.
        """
        root = tmp_home / "sa"
        root.mkdir()

        assert run_create(_create_args(root, standalone=True)) == 0
        lines = capsys.readouterr().out.splitlines()
        assert f"  kanibako box register {root}" in lines
        assert f"  kanibako box register {root / 'workspace'}" not in lines

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

        assert load_primary_boxes(
            std.primary_workset, early=_early_scope(std, BoxMode.primary),
        ).get("mybox") == str(proj_dir)
