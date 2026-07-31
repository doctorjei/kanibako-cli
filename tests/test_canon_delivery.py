"""CANON delivery (C-CANON R1) — rom delivers ``~/canon/{COLLECTION.md, bible}``.

Two delivery mechanisms land kanibako's shipped directive content into a box:

* the RO packaged CANON (``data/global/rom/canon``) is bound by exactly TWO binds
  (spec §2c) — ``canon_collection`` (the COLLECTION.md index, a FILE bind) and
  ``canon_bible`` (the whole ``bible/`` book, a DIRECTORY bind) — plus a THIRD,
  ``canon_bible_agent``, that core emits from the resolved target when that plugin
  ships a bible chapter; and
* the writable user tree (``data/global/template``) is SEEDED create-if-absent
  through the existing base-template layer at box create.

⚑ This module REPLACES ``test_playbook_delivery.py``, which pinned the retired
per-LEAF-FILE rom enumeration (one ``rom_<slug>_<hash>`` bind per shipped file, at
its mirrored ``~`` path). That enumeration existed for ONE reason — rom used to land
inside the template-seeded WRITABLE ``~/playbook``, where a directory bind would have
turned the user's own tree read-only. ``~/canon/bible`` is a dedicated root with no
writable co-tenant, so the constraint died with the layout.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from kanibako import core_defaults, templates
from kanibako.core_defaults import (
    ROM_AGENT_CHAPTER_REL,
    ROM_BIBLE_REL,
    ROM_COLLECTION_REL,
    ROM_GUIDE_REL,
    ROM_ROOT_PARTS,
)
from kanibako.paths import resolve_project
from kanibako.settings_categories import reconcile_categories
from kanibako.settings_launch import build_launch_snapshot, snapshot_category_entries
from kanibako.settings_resolve import GUEST_HOME, ResolveCtx
from kanibako.targets import resolve_target
from kanibako.targets.no_agent import NoAgentTarget
from kanibako.templates import _packaged_base_template, install_packaged_templates

_AGENTS = ("claude", "codex", "goose")

# The COMPLETE set of canon binds core emits, by key and by guest dest.
_COLLECTION_KEY = "box.bindings.ro.canon_collection"
_BIBLE_KEY = "box.bindings.ro.canon_bible"
_BIBLE_AGENT_KEY = "box.bindings.ro.canon_bible_agent"

_COLLECTION_DEST = "~/canon/COLLECTION.md"
_BIBLE_DEST = "~/canon/bible"
_BIBLE_AGENT_DEST = "~/canon/bible/agent"

# The bible's four chapters — each must exist as a real packaged DIRECTORY.
_CHAPTERS = ("general", "agent", "workset", "box")


def _ctx() -> ResolveCtx:
    return ResolveCtx(
        agent_name="claude",
        workset_name=None,
        host_home="/home/host",
        xdg={"XDG_DATA_HOME": "/data"},
    )


def _packaged_rom_root() -> Path:
    return Path(str(core_defaults.packaged_data_dir(*ROM_ROOT_PARTS)))


def _reconcile(cats: dict) -> object:
    """Drive the real launch cascade → ``reconcile_categories`` over *cats*."""
    snap = build_launch_snapshot(
        agent_name="claude",
        ctx=_ctx(),
        system_path=None,
        agent_path=None,
        workset_path=None,
        box_path=None,
        default_categories=dict(cats),
    )
    entries = snapshot_category_entries(snap, active_agent="claude", box_ctx=_ctx())
    return reconcile_categories(entries)


def _make_fake_rom(root: Path) -> Path:
    """Build a COMPLETE, valid packaged-canon tree under *root*; return the rom root."""
    rom = root / "rom"
    (rom / ROM_BIBLE_REL / "general" / "directives").mkdir(parents=True)
    (rom / ROM_GUIDE_REL).write_text("# guide\n")
    (rom / ROM_COLLECTION_REL).write_text("# collection\n")
    for chapter in ("agent", "workset", "box"):
        d = rom / ROM_BIBLE_REL / chapter / "directives"
        d.mkdir(parents=True)
        (d / f"ROM_{chapter.upper()}.md").write_text("")
    return rom


@pytest.fixture
def fake_rom(tmp_path, monkeypatch):
    """A complete fake packaged canon, substituted for the real rom root.

    Only the ROM ROOT lookup is redirected — ``templates._packaged_base_template``
    holds its OWN reference to ``packaged_data_dir``, so the template tree stays
    real (which is what keeps the disjointness check meaningful here).
    """
    rom = _make_fake_rom(tmp_path)
    real = core_defaults.packaged_data_dir

    def _fake(*parts: str):
        if tuple(parts) == tuple(ROM_ROOT_PARTS):
            return rom
        return real(*parts)

    monkeypatch.setattr(core_defaults, "packaged_data_dir", _fake)
    return rom


class _ChapterTarget:
    """A minimal target whose ``rom_root`` points wherever the test says."""

    name = "fakeharness"

    def __init__(self, root: Path | None) -> None:
        self._root = root

    def rom_root(self) -> Path | None:
        return self._root


# ===========================================================================
# The two CORE canon binds.
# ===========================================================================


class TestCanonBinds:
    def test_emits_exactly_two_binds_at_the_declared_dests(self):
        """Two binds, not N: the COLLECTION.md index and the whole bible."""
        cats = core_defaults.rom_default_categories()
        assert len(cats) == 2, cats
        dests = {dest for _, dest, _ in cats.values()}
        assert dests == {_COLLECTION_DEST, _BIBLE_DEST}
        assert all(opt == "ro" for _, _, opt in cats.values())

    def test_keys_are_the_two_declared_names(self):
        """The keys are the SPEC's stable names — not the retired
        ``rom_<slug>_<hash>`` family, which was content-derived and thus unstable
        under any content edit."""
        first = core_defaults.rom_default_categories()
        second = core_defaults.rom_default_categories()
        assert first == second, "canon bind emission must be deterministic"
        assert set(first) == {_COLLECTION_KEY, _BIBLE_KEY}
        assert not any("rom_" in k.rsplit(".", 1)[-1] for k in first), first

    def test_bible_is_a_directory_bind_and_canon_root_is_never_bound(self):
        """INVERTED from the retired ``test_no_dir_level_bind_emitted``.

        ``~/canon/bible`` IS a whole-DIRECTORY bind now (its host source is a real
        packaged dir). But NOTHING may bind ``~/canon`` itself: that root must stay
        WRITABLE for the SEEDED ``notebook/`` + ``workbook/`` books, which is the
        entire reason COLLECTION.md stays a FILE bind.
        """
        cats = core_defaults.rom_default_categories()
        by_dest = {dest: src for src, dest, _ in cats.values()}
        assert Path(by_dest[_BIBLE_DEST]).is_dir()
        assert Path(by_dest[_COLLECTION_DEST]).is_file()
        assert "~/canon" not in by_dest
        assert "~" not in by_dest

    def test_sources_are_the_packaged_canon_never_a_copy(self):
        cats = core_defaults.rom_default_categories()
        rom = _packaged_rom_root()
        by_dest = {dest: src for src, dest, _ in cats.values()}
        assert Path(by_dest[_COLLECTION_DEST]) == rom / ROM_COLLECTION_REL
        assert Path(by_dest[_BIBLE_DEST]) == rom / ROM_BIBLE_REL
        # The guide is delivered INSIDE the whole-dir bible bind (no bind of its own).
        assert (rom / ROM_GUIDE_REL).is_file()

    def test_reconciles_to_ro_mounts_at_the_guest_slots(self):
        """Both binds resolve through the real launch cascade to RO Mounts."""
        rec = _reconcile(core_defaults.rom_default_categories())
        by_dest = {m.box_dest: m for m in rec.mounts}
        for dest in (f"{GUEST_HOME}/canon/COLLECTION.md", f"{GUEST_HOME}/canon/bible"):
            assert dest in by_dest, f"{dest} not reconciled"
            m = by_dest[dest]
            assert m.scope == "box"
            assert m.category == "bindings.ro"
            assert m.options == "ro"


# ===========================================================================
# FAIL-CLOSED guards — a half-shipped canon must RAISE, never launch quietly.
# ===========================================================================


class TestFailClosed:
    def test_raises_when_guide_missing_from_walk(self, monkeypatch):
        """A populated rom root whose walk omits the guide RAISES — never a silent
        guide-less box (guards the over-broad-filter / wrong-root class)."""
        def _no_guide(root: Path) -> list[tuple[str, Path]]:
            return [("canon/other.md", Path("/pkg/rom/canon/other.md"))]

        monkeypatch.setattr(templates, "walk_shipped_files", _no_guide)
        with pytest.raises(RuntimeError, match="missing the load-bearing box guide"):
            core_defaults.rom_default_categories()

    def test_raises_when_walk_returns_empty(self, monkeypatch):
        """The empty-glob class: the guide is physically on disk but the walk yields
        ZERO files — RAISE, never mistake an empty enumeration for a no-rom install."""
        monkeypatch.setattr(templates, "walk_shipped_files", lambda root: [])
        with pytest.raises(RuntimeError, match="missing the load-bearing box guide"):
            core_defaults.rom_default_categories()

    def test_fake_rom_fixture_is_itself_valid(self, fake_rom):
        """Guard the guard: the complete fake tree must PASS, or the removal cases
        below would pass vacuously."""
        cats = core_defaults.rom_default_categories()
        assert set(cats) == {_COLLECTION_KEY, _BIBLE_KEY}

    def test_raises_when_collection_missing(self, fake_rom):
        """NEW guard: under a whole-dir bible bind, a missing COLLECTION.md raises
        nothing by itself — there is no per-file enumeration left to come up short."""
        (fake_rom / ROM_COLLECTION_REL).unlink()
        with pytest.raises(RuntimeError, match="canon .* is incomplete"):
            core_defaults.rom_default_categories()

    def test_raises_when_agent_chapter_mountpoint_missing(self, fake_rom):
        """⚑ THE MUTATION CONTRACT. ``bible/agent/`` must ship as a real directory:
        podman does NOT error on a missing nested mountpoint, it silently ``mkdir``s
        it INTO the parent bind's host source — here, the packaged tree in
        site-packages (bifrost, podman 5.4.2/crun 1.21, 2026-07-31)."""
        chapter = fake_rom / ROM_AGENT_CHAPTER_REL
        for f in sorted(chapter.rglob("*"), reverse=True):
            f.rmdir() if f.is_dir() else f.unlink()
        chapter.rmdir()
        with pytest.raises(RuntimeError, match="canon .* is incomplete"):
            core_defaults.rom_default_categories()

    def test_absent_rom_root_is_a_no_rom_install(self, tmp_path, monkeypatch):
        real = core_defaults.packaged_data_dir
        monkeypatch.setattr(
            core_defaults, "packaged_data_dir",
            lambda *p: (tmp_path / "nope") if tuple(p) == tuple(ROM_ROOT_PARTS)
            else real(*p),
        )
        assert core_defaults.rom_default_categories() == {}


# ===========================================================================
# DISJOINTNESS — no template seed may land at or under a canon bind dest.
# ===========================================================================


class TestDisjointness:
    def test_prefix_containment_not_set_intersection(self):
        """A whole-dir bind shadows a SUBTREE: a seed does not have to hit the
        bind's exact path to be silently swallowed."""
        with pytest.raises(RuntimeError, match="silently invisible"):
            core_defaults.assert_canon_bind_seed_disjoint(
                {"canon/bible"}, {"canon/bible/general/directives/ROM_GENERAL.md"},
            )

    def test_exact_dest_collision_raises(self):
        with pytest.raises(RuntimeError, match="canon bind dest"):
            core_defaults.assert_canon_bind_seed_disjoint(
                {"canon/COLLECTION.md"}, {"canon/COLLECTION.md"},
            )

    def test_sibling_and_prefix_lookalike_seeds_are_allowed(self):
        """``canon/notebook`` and ``canon/bibles-of-mine`` are NOT under
        ``canon/bible`` — a naive ``startswith`` without the separator would
        wrongly reject the second."""
        core_defaults.assert_canon_bind_seed_disjoint(
            {"canon/bible", "canon/COLLECTION.md"},
            {
                "canon/notebook/MY_CONTENTS.md",
                "canon/workbook/devnotes.md",
                "canon/bibles-of-mine/x.md",
                "canon/COLLECTION.md.bak",
                "playbook/CONTENTS.md",
            },
        )

    def test_shipped_template_tree_is_disjoint_from_the_canon(self):
        """The REAL packaged trees: the writable seed must not touch ~/canon."""
        base = _packaged_base_template()
        assert base is not None
        core_defaults.assert_canon_bind_seed_disjoint(
            {ROM_COLLECTION_REL, ROM_BIBLE_REL},
            (rel for rel, _ in templates.walk_shipped_files(base)),
        )

    def test_emitter_raises_on_a_colliding_template(self, monkeypatch):
        """Driven through the real emitter: a template seed under ``canon/bible``
        aborts the launch rather than being silently shadowed."""
        real_walk = templates.walk_shipped_files

        def _walk(root: Path) -> list[tuple[str, Path]]:
            if root == _packaged_base_template():
                return [("canon/bible/general/directives/ROM_GENERAL.md", root / "x")]
            return real_walk(root)

        monkeypatch.setattr(templates, "walk_shipped_files", _walk)
        with pytest.raises(RuntimeError, match="silently invisible"):
            core_defaults.rom_default_categories()


# ===========================================================================
# The PACKAGED tree — a directory exists in a wheel only if it ships a file.
# ===========================================================================


class TestPackagedCanonTree:
    def test_ships_all_four_chapter_directories(self):
        rom = _packaged_rom_root()
        missing = [
            c for c in _CHAPTERS if not (rom / ROM_BIBLE_REL / c / "directives").is_dir()
        ]
        assert not missing, f"packaged bible is missing chapter dirs: {missing}"

    def test_agent_chapter_directory_exists_as_the_nested_mountpoint(self):
        """⚑ Load-bearing THREE ways: it makes the dir exist in git and in the wheel,
        it is ``canon_bible_agent``'s MOUNTPOINT (a missing one makes podman mkdir
        into site-packages), and it makes ``ROM_CONTENTS.md``'s ``@agent/...`` import
        resolve to an empty section instead of an inert backticked mention."""
        rom = _packaged_rom_root()
        assert (rom / ROM_AGENT_CHAPTER_REL).is_dir()
        assert (rom / ROM_AGENT_CHAPTER_REL / "directives" / "ROM_AGENT.md").is_file()

    def test_index_and_contents_ship(self):
        rom = _packaged_rom_root()
        assert (rom / ROM_COLLECTION_REL).is_file()
        assert (rom / ROM_BIBLE_REL / "ROM_CONTENTS.md").is_file()
        assert (rom / ROM_GUIDE_REL).is_file()

    def test_no_bytecode_or_python_under_the_packaged_bible(self):
        """A whole-dir bind exposes whatever is physically in the packaged dir at
        runtime — the per-file walk's ``_is_shipped_content`` filter no longer stands
        between a dev checkout's ``__pycache__`` and the box. The canon carries no
        ``.py`` at all now (the flattener is machinery and lives in the package)."""
        rom = _packaged_rom_root()
        junk = [
            str(p.relative_to(rom)) for p in (rom / ROM_BIBLE_REL).rglob("*")
            if "__pycache__" in p.parts or p.suffix in (".pyc", ".pyo", ".py")
        ]
        assert not junk, f"packaged bible carries non-content files: {junk}"

    def test_flattener_ships_in_the_package_not_the_canon(self):
        """P-2: ``import-directives.py`` is MACHINERY. It reaches the box through the
        existing unconditional ``kani_pkg`` bind, so the canon holds only text."""
        import importlib.resources

        ref = importlib.resources.files("kanibako.scripts").joinpath(
            "import-directives.py"
        )
        assert Path(str(ref)).is_file()


# ===========================================================================
# The PLUGIN chapter — ``canon_bible_agent`` (spec §2c, the seventh canon bind).
# ===========================================================================


class TestPluginChapterBind:
    @pytest.mark.parametrize("agent", _AGENTS)
    def test_rom_root_resolves_for_every_first_party_harness(self, agent: str):
        """The base ``Target.rom_root`` derives the package from ``__package__``, so
        it works whether the Target class lives in ``target.py`` or ``__init__.py``."""
        root = resolve_target(agent, None).rom_root()
        assert root is not None, f"{agent}: rom_root did not resolve"
        assert root.is_dir()
        assert root.name == "rom" and root.parent.name == "data"

    @pytest.mark.parametrize("agent", _AGENTS)
    def test_gate_is_negative_today_so_no_bind_is_emitted(self, agent: str):
        """R1 lands the emitter + the interface; R2 fans the CONTENT out to the three
        packages. Until a plugin ships ``directives/ROM_AGENT.md``, emitting nothing
        is CORRECT: an empty plugin rom would SHADOW core's placeholder chapter with
        nothing, turning ``@agent/directives/ROM_AGENT.md`` into a dangling import."""
        target = resolve_target(agent, None)
        assert core_defaults.rom_agent_default_categories(target) == {}

    def test_emits_one_bind_when_the_plugin_ships_a_chapter(self, tmp_path):
        chapter = tmp_path / "data" / "rom"
        (chapter / "directives").mkdir(parents=True)
        (chapter / "directives" / "ROM_AGENT.md").write_text("# harness chapter\n")

        cats = core_defaults.rom_agent_default_categories(_ChapterTarget(chapter))
        assert set(cats) == {_BIBLE_AGENT_KEY}
        src, dest, opts = cats[_BIBLE_AGENT_KEY]
        assert Path(src) == chapter, "the plugin's data/rom IS the chapter root"
        assert dest == _BIBLE_AGENT_DEST, "the dest carries NO agent segment"
        assert opts == "ro"

    def test_no_bind_when_the_chapter_marker_is_absent(self, tmp_path):
        chapter = tmp_path / "data" / "rom"
        chapter.mkdir(parents=True)
        (chapter / "_bundled_future_use_").write_text("")
        assert core_defaults.rom_agent_default_categories(_ChapterTarget(chapter)) == {}

    def test_no_bind_when_the_target_has_no_rom_root(self):
        """Directory plugins are not ``kanibako.plugins.*`` packages, so ``rom_root``
        returns None for them — the right answer, not an error."""
        assert core_defaults.rom_agent_default_categories(_ChapterTarget(None)) == {}


class TestNestingOrder:
    def test_bible_mounts_before_its_agent_chapter(self, tmp_path):
        """⚑ THE SHADOW MECHANISM. The plugin chapter's dest NESTS inside the bible's,
        and it works only because the MOUNT depth-sort is ASCENDING — the deeper dest
        must land LAST so the plugin's chapter shadows core's placeholder. A
        depth-sort regression is otherwise entirely silent.
        """
        chapter = tmp_path / "data" / "rom"
        (chapter / "directives").mkdir(parents=True)
        (chapter / "directives" / "ROM_AGENT.md").write_text("")

        cats = dict(core_defaults.rom_default_categories())
        cats.update(core_defaults.rom_agent_default_categories(_ChapterTarget(chapter)))
        assert len(cats) == 3

        rec = _reconcile(cats)
        order = [m.box_dest for m in rec.mounts]
        bible = f"{GUEST_HOME}/canon/bible"
        agent = f"{GUEST_HOME}/canon/bible/agent"
        assert bible in order and agent in order, order
        assert order.index(bible) < order.index(agent), order
        # Nested-but-DIFFERENT dests are blessed, never a collision.
        assert not rec.warnings, rec.warnings


# ===========================================================================
# ⚑ LAUNCH WIRING — the emitters are actually CALLED by the real launch path.
# ===========================================================================


class _WiringTarget(NoAgentTarget):
    """A REAL ``Target`` for the live ``_resolve_launch_snapshot`` seam, with only
    ``rom_root`` overridden.

    Subclassing the built-in fallback (rather than duck-typing) means every other
    hook that seam reads — ``default_common``, ``default_seeds``,
    ``default_category_binds``, ``setting_descriptors``, … — comes from the real
    ABC, so this cannot pass by accident when the seam grows a new call.
    """

    def __init__(self, rom_root: Path | None = None) -> None:
        self._rom_root = rom_root

    def rom_root(self) -> Path | None:
        return self._rom_root


class TestLaunchWiring:
    """⚑ THE CALL SITES THEMSELVES. Every other test in this module enters at
    ``build_launch_snapshot`` with a hand-built category table, which proves the
    emitters are CORRECT but not that anything CALLS them — deleting both
    ``default_categories.update(...)`` lines from ``start._resolve_launch_snapshot``
    left the entire suite green (mutation-proved, 2026-07-31). These tests close
    that gap by driving the REAL launch seam with real ``std``/``proj`` objects, in
    the style of ``test_start_channels``.
    """

    def _launch_mounts(self, std, proj, target) -> dict:
        from kanibako.commands.start import (
            _emit_category_mounts,
            _resolve_launch_snapshot,
        )

        _snapshot, reconciled = _resolve_launch_snapshot(
            std=std,
            proj=proj,
            agent_name="claude",
            system_settings_path=None,
            agent_cfg_path=None,
            desc=None,
            install=None,
            target=target,
            agent_cfg=None,
            deliver_creds=True,
        )
        mounts = _emit_category_mounts(reconciled, label="canon-wiring")
        return {m.destination: m for m in mounts}

    def test_core_canon_binds_reach_a_real_launch(self, std, config, project_dir):
        """RED if ``core_defaults.rom_default_categories()`` stops being unioned
        into the launch snapshot: both canon mounts vanish from the emitted set."""
        proj = resolve_project(std, config, str(project_dir), initialize=True)
        by_dest = self._launch_mounts(std, proj, _WiringTarget())

        collection = f"{GUEST_HOME}/canon/COLLECTION.md"
        bible = f"{GUEST_HOME}/canon/bible"
        assert collection in by_dest, sorted(by_dest)
        assert bible in by_dest, sorted(by_dest)
        assert by_dest[collection].options == "ro"
        assert by_dest[bible].options == "ro"
        assert Path(by_dest[collection].source).is_file()
        assert Path(by_dest[bible].source).is_dir()
        # ⚑ ~/canon itself must NOT be mounted — it has to stay writable for the
        # SEEDED notebook/workbook books.
        assert f"{GUEST_HOME}/canon" not in by_dest

    def test_plugin_chapter_bind_reaches_a_real_launch(
        self, std, config, project_dir, tmp_path,
    ):
        """RED if ``core_defaults.rom_agent_default_categories(target)`` stops being
        unioned in: the nested chapter mount vanishes.

        Also pins the ORDER on the real path — ``bible`` must be emitted BEFORE
        ``bible/agent`` or the plugin's chapter would be shadowed BY the placeholder
        instead of shadowing it.
        """
        chapter = tmp_path / "plugin-pkg" / "data" / "rom"
        (chapter / "directives").mkdir(parents=True)
        (chapter / "directives" / "ROM_AGENT.md").write_text("# chapter\n")

        proj = resolve_project(std, config, str(project_dir), initialize=True)
        by_dest = self._launch_mounts(std, proj, _WiringTarget(chapter))

        agent_dest = f"{GUEST_HOME}/canon/bible/agent"
        assert agent_dest in by_dest, sorted(by_dest)
        assert by_dest[agent_dest].options == "ro"
        assert Path(by_dest[agent_dest].source) == chapter

        order = [m.destination for m in by_dest.values()]
        assert order.index(f"{GUEST_HOME}/canon/bible") < order.index(agent_dest)

    def test_no_plugin_chapter_mount_when_the_gate_is_negative(
        self, std, config, project_dir, tmp_path,
    ):
        """A plugin with no chapter contributes no mount — it must not shadow core's
        placeholder chapter with an empty directory."""
        bare = tmp_path / "plugin-pkg" / "data" / "rom"
        bare.mkdir(parents=True)
        (bare / "_bundled_future_use_").write_text("")

        proj = resolve_project(std, config, str(project_dir), initialize=True)
        by_dest = self._launch_mounts(std, proj, _WiringTarget(bare))

        assert f"{GUEST_HOME}/canon/bible" in by_dest
        assert f"{GUEST_HOME}/canon/bible/agent" not in by_dest

    def test_core_canon_binds_land_even_without_an_agent(
        self, std, config, project_dir,
    ):
        """A no-agent (plain shell) box still gets the canon: the two core binds are
        emitted OUTSIDE the ``target is not None`` block, only the plugin chapter is
        inside it."""
        proj = resolve_project(std, config, str(project_dir), initialize=True)
        by_dest = self._launch_mounts(std, proj, None)

        assert f"{GUEST_HOME}/canon/COLLECTION.md" in by_dest
        assert f"{GUEST_HOME}/canon/bible" in by_dest
        assert f"{GUEST_HOME}/canon/bible/agent" not in by_dest


# ===========================================================================
# The writable seed layer is UNCHANGED by this cutover.
# ===========================================================================


class TestBaseTemplateSeedsPlaybook:
    """The base-template seed source (``data/global/template``) is untouched by the
    canon cutover: it still carries ``playbook/CONTENTS.md`` and still seeds it at
    box home create-if-absent. (Its own relayout is M-11's, not this phase's.)"""

    def test_packaged_source_carries_playbook_contents(self):
        base = _packaged_base_template()
        assert base is not None
        assert (base / "playbook" / "CONTENTS.md").is_file()
        assert not (base / "INSTRUCTIONS.md").exists()

    def test_install_lands_playbook_in_base_template_dir(self, std):
        install_packaged_templates(std, ["claude"])
        assert (std.base_template / "playbook" / "CONTENTS.md").is_file()

    def test_seed_lands_playbook_contents_at_home(self, std, config, project_dir):
        """End-to-end: the base layer seeds ``~/playbook/CONTENTS.md`` at box create
        through the single keystore-routed seed (create-if-absent)."""
        from kanibako.commands.start import _apply_init_seeds

        proj = resolve_project(std, config, str(project_dir), initialize=True)
        install_packaged_templates(std, ["claude"])
        _apply_init_seeds(
            std=std,
            proj=proj,
            agent_name="claude",
            target=_FakeTarget(),
            global_config_path=std.settings,
            agent_config_path=std.agents / "claude" / "settings.yaml",
            logger=logging.getLogger("test-canon-seed"),
            deliver_creds=True,
        )
        assert (proj.shell_path / "playbook" / "CONTENTS.md").is_file()


class _FakeTarget:
    """Minimal resolved-agent stand-in for the seed seam (mirrors test_templates):
    only ``.name`` + empty ``default_seeds()`` are read for a non-descriptor target."""

    name = "claude"

    def default_seeds(self):
        return {}
