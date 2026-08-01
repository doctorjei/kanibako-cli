"""CANON delivery (C-CANON R1b) — the SIBLING binds + the box-create SKELETON.

Two delivery mechanisms land kanibako's shipped directive content into a box:

* the RO packaged CANON (``data/global/rom``) is bound by FIVE SIBLING binds (spec
  §2c, J-7) — ``canon_collection`` and ``canon_bible_contents`` as FILE binds, plus
  one whole-directory bind per packaged bible chapter
  (``canon_bible_{general,workset,box}``) — with a SIXTH, ``canon_bible_agent``, that
  core emits from the resolved target when that plugin ships a bible chapter; and
* the writable user tree (``data/global/template``) is SEEDED create-if-absent
  through the existing base-template layer at box create.

⚑ J-7 (2026-07-31) REPLACED R1's whole-directory ``canon_bible`` bind (shipped only
in the unreleased ``93b9a9d``) and the nested ``canon_bible_agent`` that sat inside
it. The nested-mount physics PASSED on real podman; the model was retired anyway
because nesting forced MOUNTPOINTS to live inside bind SOURCES — site-packages for
the bible chapter, the user's own stores for the handbook chapters — where a wheel
cannot ship an empty directory and no runtime may safely write. Under the sibling
model every mountpoint lives in the box home, materialised once at box create by
``core_defaults.materialize_canon_skeleton`` and made root-owned + 555.

⚑ This module REPLACED ``test_playbook_delivery.py`` (the retired per-LEAF-FILE rom
enumeration) in R1, and is REWRITTEN here for the sibling set.
"""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from kanibako.settings import core_defaults
from kanibako.launch import templates
from kanibako.settings.core_defaults import (
    BIBLE_AGENT_CHAPTER,
    CANON_SEED_DENY_PREFIXES,
    CANON_SKELETON_DIR_MODE,
    CANON_SKELETON_FILE_MODE,
    HANDBOOK_CHAPTERS,
    PLUGIN_CHAPTER_MARKER_REL,
    ROM_BIBLE_CHAPTERS,
    ROM_BIBLE_REL,
    ROM_COLLECTION_REL,
    ROM_CONTENTS_REL,
    ROM_GUIDE_REL,
    ROM_ROOT_PARTS,
    UNSHARE_BOX_ROOT_GID,
    UNSHARE_BOX_ROOT_UID,
)
from kanibako.settings.paths import resolve_project
from kanibako.settings.settings_categories import reconcile_categories
from kanibako.settings.settings_launch import build_launch_snapshot, snapshot_category_entries
from kanibako.settings.settings_resolve import GUEST_HOME, ResolveCtx
from kanibako.targets import resolve_target
from kanibako.targets.no_agent import NoAgentTarget
from kanibako.launch.templates import (
    _packaged_base_template,
    install_packaged_templates,
    packaged_box_home_template,
)

_AGENTS = ("claude", "codex", "goose")

# The COMPLETE set of canon binds core emits, by key and by guest dest.  ⚑ FIVE from
# the packaged rom + ONE gated plugin chapter; there is NO ``canon_bible`` whole-dir
# key any more (RETIRED by J-7 — an undeclared key from the spec edit forward).
_CORE_KEYS = {
    "box.bindings.ro.canon_collection": "~/canon/COLLECTION.md",
    "box.bindings.ro.canon_bible_contents": "~/canon/bible/ROM_CONTENTS.md",
    "box.bindings.ro.canon_bible_general": "~/canon/bible/general",
    "box.bindings.ro.canon_bible_workset": "~/canon/bible/workset",
    "box.bindings.ro.canon_bible_box": "~/canon/bible/box",
}
# The two FILE binds (file-onto-file, over the skeleton's 0-byte mountpoints).
_FILE_KEYS = {
    "box.bindings.ro.canon_collection",
    "box.bindings.ro.canon_bible_contents",
}
_BIBLE_AGENT_KEY = "box.bindings.ro.canon_bible_agent"
_BIBLE_AGENT_DEST = "~/canon/bible/agent"

# Sources, rom-root-relative, in the same order as ``_CORE_KEYS``.
_CORE_SOURCES = {
    "box.bindings.ro.canon_collection": ROM_COLLECTION_REL,
    "box.bindings.ro.canon_bible_contents": ROM_CONTENTS_REL,
    "box.bindings.ro.canon_bible_general": f"{ROM_BIBLE_REL}/general",
    "box.bindings.ro.canon_bible_workset": f"{ROM_BIBLE_REL}/workset",
    "box.bindings.ro.canon_bible_box": f"{ROM_BIBLE_REL}/box",
}


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
    """Build a COMPLETE, valid packaged-canon tree under *root*; return the rom root.

    ⚑ FLAT: ``rom/{COLLECTION.md, bible/**}`` with no ``canon/`` wrapper, and NO
    ``bible/agent/`` — matching what the wheel actually ships under J-7.
    """
    rom = root / "rom"
    (rom / ROM_BIBLE_REL).mkdir(parents=True)
    (rom / ROM_COLLECTION_REL).write_text("# collection\n")
    (rom / ROM_CONTENTS_REL).write_text("# contents\n")
    for chapter in ROM_BIBLE_CHAPTERS:
        d = rom / ROM_BIBLE_REL / chapter / "directives"
        d.mkdir(parents=True)
        (d / f"ROM_{chapter.upper()}.md").write_text("x\n")
    assert (rom / ROM_GUIDE_REL).is_file(), "fixture must satisfy the guide guard"
    return rom


@pytest.fixture
def fake_rom(tmp_path, monkeypatch):
    """A complete fake packaged canon, substituted for the real rom root.

    Only the ROM ROOT lookup is redirected — ``templates.packaged_box_home_template``
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
# The FIVE core canon binds.
# ===========================================================================


class TestCanonBinds:
    def test_emits_exactly_the_five_declared_siblings(self):
        cats = core_defaults.rom_default_categories()
        assert set(cats) == set(_CORE_KEYS), cats
        assert {dest for _, dest, _ in cats.values()} == set(_CORE_KEYS.values())
        assert all(opt == "ro" for _, _, opt in cats.values())

    def test_keys_are_the_spec_names_and_emission_is_deterministic(self):
        """Stable SPEC names — not the retired content-derived ``rom_<slug>_<hash>``
        family, and not R1's retired whole-dir ``canon_bible``."""
        first = core_defaults.rom_default_categories()
        assert first == core_defaults.rom_default_categories()
        assert "box.bindings.ro.canon_bible" not in first, (
            "canon_bible is RETIRED (J-7) — an undeclared key, not a bind"
        )
        assert not any("rom_" in k.rsplit(".", 1)[-1] for k in first), first

    def test_each_key_lands_at_its_declared_dest(self):
        cats = core_defaults.rom_default_categories()
        for key, dest in _CORE_KEYS.items():
            assert cats[key][1] == dest, key

    def test_file_binds_are_files_and_chapter_binds_are_directories(self):
        """The shapes are load-bearing: a file bind mounts file-onto-file over the
        skeleton's 0-byte mountpoint; a chapter bind replaces a whole directory."""
        cats = core_defaults.rom_default_categories()
        for key, (src, _dest, _opt) in cats.items():
            if key in _FILE_KEYS:
                assert Path(src).is_file(), key
            else:
                assert Path(src).is_dir(), key

    def test_neither_canon_root_nor_bible_root_is_ever_bound(self):
        """⚑ THE SIBLING CONTRACT. ``~/canon`` must never be bound (it holds the
        SEEDED notebook/workbook), and neither must ``~/canon/bible`` — re-introducing
        that whole-dir bind is exactly the R1 model J-7 retired, and it would put the
        agent chapter's mountpoint back inside a bind source."""
        dests = {dest for _, dest, _ in core_defaults.rom_default_categories().values()}
        assert "~/canon" not in dests
        assert "~/canon/bible" not in dests
        assert "~" not in dests

    def test_sources_are_the_packaged_canon_never_a_copy(self):
        cats = core_defaults.rom_default_categories()
        rom = _packaged_rom_root()
        for key, rel in _CORE_SOURCES.items():
            assert Path(cats[key][0]) == rom / rel, key
        # The guide has NO bind of its own — it rides the ``general`` chapter's.
        guide = rom / ROM_GUIDE_REL
        assert guide.is_file()
        assert guide.is_relative_to(Path(cats["box.bindings.ro.canon_bible_general"][0]))

    def test_reconciles_to_ro_mounts_at_every_guest_slot(self):
        rec = _reconcile(core_defaults.rom_default_categories())
        by_dest = {m.box_dest: m for m in rec.mounts}
        for dest in _CORE_KEYS.values():
            guest = dest.replace("~", GUEST_HOME, 1)
            assert guest in by_dest, f"{guest} not reconciled"
            m = by_dest[guest]
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
            return [("other.md", Path("/pkg/rom/other.md"))]

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
        """Guard the guard: the complete fake tree must PASS, or every removal case
        below would pass vacuously."""
        assert set(core_defaults.rom_default_categories()) == set(_CORE_KEYS)

    @pytest.mark.parametrize("rel", sorted(set(_CORE_SOURCES.values())))
    def test_raises_when_any_emitted_source_is_missing(self, fake_rom, rel: str):
        """⚑ EVERY emitted bind's source is a required member. A missing one would
        otherwise be DROPPED by ``_emit_category_mounts`` with only a per-launch
        warning — a box quietly short one chapter of its own directives."""
        import shutil

        target = fake_rom / rel
        shutil.rmtree(target) if target.is_dir() else target.unlink()
        with pytest.raises(RuntimeError, match="canon .* is incomplete"):
            core_defaults.rom_default_categories()

    def test_packaged_agent_chapter_is_NOT_required(self, fake_rom):
        """⚑ THE INVERTED R1 ASSERTION. R1 REQUIRED a packaged ``bible/agent/``
        directory as the nested bind's mountpoint (podman silently mkdir'd a missing
        one into site-packages). J-7 removed the nesting, so that placeholder must NOT
        ship — and its absence must NOT raise. The fixture never creates it."""
        assert not (fake_rom / ROM_BIBLE_REL / BIBLE_AGENT_CHAPTER).exists()
        assert set(core_defaults.rom_default_categories()) == set(_CORE_KEYS)

    def test_absent_rom_root_is_a_no_rom_install(self, tmp_path, monkeypatch):
        real = core_defaults.packaged_data_dir
        monkeypatch.setattr(
            core_defaults, "packaged_data_dir",
            lambda *p: (tmp_path / "nope") if tuple(p) == tuple(ROM_ROOT_PARTS)
            else real(*p),
        )
        assert core_defaults.rom_default_categories() == {}


# ===========================================================================
# DISJOINTNESS — no template seed may land in the managed ~/canon region.
# ===========================================================================


class TestDisjointness:
    def test_prefix_containment_not_set_intersection(self):
        """A managed prefix covers a SUBTREE: a seed does not have to hit an exact
        path to be silently swallowed."""
        with pytest.raises(RuntimeError, match="silently invisible"):
            core_defaults.assert_canon_bind_seed_disjoint(
                {"canon/bible"}, {"canon/bible/general/directives/ROM_GENERAL.md"},
            )

    def test_exact_collision_raises(self):
        with pytest.raises(RuntimeError, match="managed canon path"):
            core_defaults.assert_canon_bind_seed_disjoint(
                {"canon/COLLECTION.md"}, {"canon/COLLECTION.md"},
            )

    def test_seed_into_the_agent_chapter_still_raises(self):
        """⚑ THE J-7 WIDENING. Under sibling binds ``canon/bible`` is no longer itself
        a bind dest — only its chapters are — so a guard built from the LITERAL dests
        would silently start allowing a seed at ``canon/bible/agent/…``. Spec §2c
        forbids seeding anywhere under ``canon/bible/``, and under J-7 that path is a
        root-owned 555 mountpoint, so the copy would fail at create rather than merely
        be shadowed at launch. RED if someone narrows the deny list back to the dests.
        """
        with pytest.raises(RuntimeError, match="silently invisible"):
            core_defaults.assert_canon_bind_seed_disjoint(
                CANON_SEED_DENY_PREFIXES, {"canon/bible/agent/directives/ROM_AGENT.md"},
            )

    def test_deny_list_covers_the_whole_bible_not_just_its_chapters(self):
        assert "canon/bible" in CANON_SEED_DENY_PREFIXES
        assert "canon/COLLECTION.md" in CANON_SEED_DENY_PREFIXES

    def test_sibling_and_prefix_lookalike_seeds_are_allowed(self):
        """``canon/notebook`` and ``canon/bibles-of-mine`` are NOT under
        ``canon/bible`` — a naive ``startswith`` without the separator would
        wrongly reject the second."""
        core_defaults.assert_canon_bind_seed_disjoint(
            CANON_SEED_DENY_PREFIXES,
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
        home_root = packaged_box_home_template()
        assert home_root is not None
        core_defaults.assert_canon_bind_seed_disjoint(
            CANON_SEED_DENY_PREFIXES,
            (rel for rel, _ in templates.walk_shipped_files(home_root)),
        )

    def test_guard_is_anchored_at_the_home_relative_root(self):
        """⚑ REGRESSION on the guard itself. Both sides must be HOME-relative or the
        prefix comparison means nothing. The packaged TEMPLATE ROOT is no longer the
        home-relative root (it yields ``box/home/...``), so a guard fed the root walk
        would run, pass, and check NOTHING — this pins the seam that must not slip
        back one level."""
        home_root = packaged_box_home_template()
        base = _packaged_base_template()
        assert home_root is not None and base is not None
        assert home_root != base
        rels = {rel for rel, _ in templates.walk_shipped_files(home_root)}
        # Home-relative: the notebook the seed deposits at ~/canon/notebook.
        assert "canon/notebook/MY_CONTENTS.md" in rels, rels

    def test_emitter_raises_on_a_colliding_template(self, monkeypatch):
        """Driven through the real emitter: a template seed under ``canon/bible``
        aborts the launch rather than being silently shadowed."""
        real_walk = templates.walk_shipped_files

        def _walk(root: Path) -> list[tuple[str, Path]]:
            if root == packaged_box_home_template():
                return [("canon/bible/general/directives/ROM_GENERAL.md", root / "x")]
            return real_walk(root)

        monkeypatch.setattr(templates, "walk_shipped_files", _walk)
        with pytest.raises(RuntimeError, match="silently invisible"):
            core_defaults.rom_default_categories()


# ===========================================================================
# The PACKAGED tree — a directory exists in a wheel only if it ships a file.
# ===========================================================================


class TestPackagedCanonTree:
    def test_layout_is_flat_with_no_canon_wrapper(self):
        """J-7 / Jei's samples: the packaged rom is ``rom/{COLLECTION.md, bible/**}``.
        The old ``rom/canon/**`` level is gone, so a rom-relative path is no longer
        its own ``~``-dest — every dest goes through ``_canon_dest``."""
        rom = _packaged_rom_root()
        assert (rom / "COLLECTION.md").is_file()
        assert (rom / "bible").is_dir()
        assert not (rom / "canon").exists()

    def test_ships_every_packaged_chapter_directory(self):
        rom = _packaged_rom_root()
        missing = [
            c for c in ROM_BIBLE_CHAPTERS
            if not (rom / ROM_BIBLE_REL / c / "directives").is_dir()
        ]
        assert not missing, f"packaged bible is missing chapter dirs: {missing}"

    def test_agent_chapter_does_NOT_ship_in_the_package(self):
        """⚑ J-7 KILLED THE WHEEL MOUNTPOINT. R1 shipped a 0-byte
        ``bible/agent/directives/ROM_AGENT.md`` purely to make that directory exist in
        git and in the wheel, because a NESTED bind's mountpoint had to live inside its
        parent's SOURCE. With siblings the mountpoint lives in the box home instead, so
        the package must carry nothing here — a stray empty chapter would bind over the
        plugin's, or be mistaken for content."""
        rom = _packaged_rom_root()
        assert not (rom / ROM_BIBLE_REL / BIBLE_AGENT_CHAPTER).exists()

    def test_index_and_contents_ship(self):
        rom = _packaged_rom_root()
        assert (rom / ROM_COLLECTION_REL).is_file()
        assert (rom / ROM_CONTENTS_REL).is_file()
        assert (rom / ROM_GUIDE_REL).is_file()

    def test_no_bytecode_or_python_under_the_packaged_bible(self):
        """A directory bind exposes whatever is physically in the packaged dir at
        runtime — the per-file walk's ``_is_shipped_content`` filter no longer stands
        between a dev checkout's ``__pycache__`` and the box."""
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
# The PLUGIN chapter — ``canon_bible_agent`` (spec §2c, the sixth canon bind).
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
    def test_every_first_party_harness_ships_a_chapter_so_the_gate_is_positive(
        self, agent: str,
    ):
        """⚑ THE R2 FLIP. Each plugin now ships ``data/rom/directives/ROM_AGENT.md``,
        so the gate is TRUE for all three and the bible's agent chapter is a REAL bind
        on every first-party box — which is what makes ``@agent/directives/ROM_AGENT.md``
        in the bible's ``ROM_CONTENTS.md`` resolve instead of dangling.

        Gate-FALSE is not left uncovered: it is exercised by the two temp-plugin tests
        below (bare ``data/rom`` with no marker, and no ``rom_root`` at all), which is
        where it belongs now that no shipped plugin can demonstrate it."""
        target = resolve_target(agent, None)
        root = target.rom_root()
        assert root is not None
        assert (root / PLUGIN_CHAPTER_MARKER_REL).is_file(), (
            f"{agent}: the plugin must ship its bible chapter at "
            f"data/rom/{PLUGIN_CHAPTER_MARKER_REL}"
        )

        cats = core_defaults.rom_agent_default_categories(target)
        assert set(cats) == {_BIBLE_AGENT_KEY}, cats
        src, dest, opts = cats[_BIBLE_AGENT_KEY]
        assert Path(src) == root, "the plugin's data/rom IS the chapter root (D3)"
        assert dest == _BIBLE_AGENT_DEST
        assert opts == "ro"

    def test_the_three_shipped_chapters_are_byte_identical(self):
        """One authored chapter, delivered per HARNESS. Per-harness customization is a
        later, deliberate edit — until then a drifting copy is a mistake, not a
        variant, and this is where it surfaces."""
        digests = {
            agent: (
                resolve_target(agent, None).rom_root() / PLUGIN_CHAPTER_MARKER_REL
            ).read_bytes()
            for agent in _AGENTS
        }
        assert len(set(digests.values())) == 1, {
            a: len(b) for a, b in digests.items()
        }

    @pytest.mark.parametrize("agent", _AGENTS)
    def test_no_bytecode_or_python_under_a_packaged_plugin_chapter(self, agent: str):
        """R1's N3 handoff — the CORE-side twin of
        ``TestPackagedCanonTree::test_no_bytecode_or_python_under_the_packaged_bible``.

        The plugin's ``data/rom`` is now a REAL whole-directory bind source, and a
        whole-dir bind exposes whatever is physically in the packaged dir: the
        per-file walk's ``_is_shipped_content`` filter no longer stands between a dev
        checkout's ``__pycache__`` and the box. A plugin chapter is TEXT only."""
        root = resolve_target(agent, None).rom_root()
        assert root is not None
        junk = [
            str(p.relative_to(root)) for p in root.rglob("*")
            if "__pycache__" in p.parts or p.suffix in (".pyc", ".pyo", ".py")
        ]
        assert not junk, f"{agent}: packaged plugin chapter carries non-content: {junk}"

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


class TestSiblingAssembly:
    """⚑ REPLACES R1's ``TestNestingOrder``, which pinned the SHADOW mechanism (the
    plugin chapter mounting INSIDE ``canon_bible`` and the ascending depth-sort that
    made it land last). J-7 retired nesting outright, so the invariant to pin is the
    opposite one — and keeping a "nesting works" test after nesting was removed would
    read as precedent for re-introducing it.
    """

    def test_no_canon_dest_is_a_prefix_of_another(self, tmp_path):
        chapter = tmp_path / "data" / "rom"
        (chapter / "directives").mkdir(parents=True)
        (chapter / "directives" / "ROM_AGENT.md").write_text("")

        cats = dict(core_defaults.rom_default_categories())
        cats.update(core_defaults.rom_agent_default_categories(_ChapterTarget(chapter)))
        assert len(cats) == 6

        dests = sorted(dest for _, dest, _ in cats.values())
        for a in dests:
            for b in dests:
                assert a == b or not b.startswith(f"{a}/"), (
                    f"{b!r} nests inside {a!r} — J-7 retired nested canon binds"
                )

    def test_all_six_reconcile_without_collision_warnings(self, tmp_path):
        chapter = tmp_path / "data" / "rom"
        (chapter / "directives").mkdir(parents=True)
        (chapter / "directives" / "ROM_AGENT.md").write_text("")

        cats = dict(core_defaults.rom_default_categories())
        cats.update(core_defaults.rom_agent_default_categories(_ChapterTarget(chapter)))
        rec = _reconcile(cats)
        assert not rec.warnings, rec.warnings
        assert f"{GUEST_HOME}/canon/bible/agent" in {m.box_dest for m in rec.mounts}


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
    that gap by driving the REAL launch seam with real ``std``/``proj`` objects.
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

    def test_all_five_core_canon_binds_reach_a_real_launch(
        self, std, config, project_dir,
    ):
        """RED if ``core_defaults.rom_default_categories()`` stops being unioned
        into the launch snapshot: every canon mount vanishes from the emitted set."""
        proj = resolve_project(std, config, str(project_dir), initialize=True)
        by_dest = self._launch_mounts(std, proj, _WiringTarget())

        for dest in _CORE_KEYS.values():
            guest = dest.replace("~", GUEST_HOME, 1)
            assert guest in by_dest, sorted(by_dest)
            assert by_dest[guest].options == "ro"
        # ⚑ Neither book ROOT is mounted: ~/canon holds the SEEDED notebook/workbook,
        # and ~/canon/bible is the retired whole-dir bind.
        assert f"{GUEST_HOME}/canon" not in by_dest
        assert f"{GUEST_HOME}/canon/bible" not in by_dest

    def test_plugin_chapter_bind_reaches_a_real_launch(
        self, std, config, project_dir, tmp_path,
    ):
        """RED if ``core_defaults.rom_agent_default_categories(target)`` stops being
        unioned in: the chapter mount vanishes."""
        chapter = tmp_path / "plugin-pkg" / "data" / "rom"
        (chapter / "directives").mkdir(parents=True)
        (chapter / "directives" / "ROM_AGENT.md").write_text("# chapter\n")

        proj = resolve_project(std, config, str(project_dir), initialize=True)
        by_dest = self._launch_mounts(std, proj, _WiringTarget(chapter))

        agent_dest = f"{GUEST_HOME}/canon/bible/agent"
        assert agent_dest in by_dest, sorted(by_dest)
        assert by_dest[agent_dest].options == "ro"
        assert Path(by_dest[agent_dest].source) == chapter

    def test_gate_negative_yields_no_mount_but_the_skeleton_still_pre_created_it(
        self, std, config, project_dir, tmp_path,
    ):
        """⚑ THE GATE-FALSE SHAPE J-7 SPECIFIES: no bind, but the mountpoint EXISTS in
        the box home as an empty root-owned directory. The dangling ``@agent/`` import
        warning from the flattener is the honest signal — not a missing directory."""
        bare = tmp_path / "plugin-pkg" / "data" / "rom"
        bare.mkdir(parents=True)
        (bare / "_bundled_future_use_").write_text("")

        proj = resolve_project(std, config, str(project_dir), initialize=True)
        by_dest = self._launch_mounts(std, proj, _WiringTarget(bare))
        assert f"{GUEST_HOME}/canon/bible/agent" not in by_dest

        with patch("kanibako.runtime.container.ContainerRuntime"):
            core_defaults.materialize_canon_skeleton(proj.shell_path)
        chapter_dir = proj.shell_path / "canon" / "bible" / "agent"
        assert chapter_dir.is_dir(), "the mountpoint must exist even with no bind"
        assert not any(chapter_dir.iterdir()), "and it must be EMPTY"

    def test_core_canon_binds_land_even_without_an_agent(
        self, std, config, project_dir,
    ):
        """A no-agent (plain shell) box still gets the canon: the five core binds are
        emitted OUTSIDE the ``target is not None`` block, only the plugin chapter is
        inside it."""
        proj = resolve_project(std, config, str(project_dir), initialize=True)
        by_dest = self._launch_mounts(std, proj, None)

        for dest in _CORE_KEYS.values():
            assert dest.replace("~", GUEST_HOME, 1) in by_dest
        assert f"{GUEST_HOME}/canon/bible/agent" not in by_dest


# ===========================================================================
# The box-create CANON SKELETON (J-7).
# ===========================================================================


@pytest.fixture
def fake_runtime():
    """Patch ``ContainerRuntime`` at its definition module (the import inside
    ``_protect_canon_skeleton`` is deferred, so this is the attribute it reads)."""
    with patch("kanibako.runtime.container.ContainerRuntime") as rt:
        rt.return_value.unshare_chown.return_value = True
        rt.return_value.unshare_chmod.return_value = True
        yield rt


class TestCanonSkeleton:
    def test_materializes_the_full_declared_set(self, tmp_path, fake_runtime):
        home = tmp_path / "home"
        home.mkdir()
        core_defaults.materialize_canon_skeleton(home)

        for rel, is_dir in core_defaults.canon_skeleton_rels():
            p = home / rel
            assert p.exists(), rel
            assert p.is_dir() == is_dir, rel

    def test_the_three_file_mountpoints_are_zero_byte_files(self, tmp_path, fake_runtime):
        """A FILE bind mounts file-onto-file, so each needs a real (empty) file to
        land on — not a directory, and not nothing."""
        home = tmp_path / "home"
        home.mkdir()
        core_defaults.materialize_canon_skeleton(home)

        for rel in (
            "canon/COLLECTION.md",
            "canon/bible/ROM_CONTENTS.md",
            "canon/handbook/SYS_CONTENTS.md",
        ):
            p = home / rel
            assert p.is_file(), rel
            assert p.stat().st_size == 0, rel

    def test_skeleton_mirrors_the_bind_dests_exactly(self, tmp_path, fake_runtime):
        """⚑ THE MOUNTPOINT CONTRACT. Every canon bind dest must have a mountpoint in
        the skeleton. A dest with none is a mountpoint podman creates itself — which
        under the retired nested model meant mkdir-ing into site-packages, the exact
        failure J-7 exists to remove."""
        cats = dict(core_defaults.rom_default_categories())
        cats[_BIBLE_AGENT_KEY] = ("/x", _BIBLE_AGENT_DEST, "ro")
        skeleton = {rel for rel, _ in core_defaults.canon_skeleton_rels()}
        for _src, dest, _opt in cats.values():
            assert dest.removeprefix("~/") in skeleton, dest

    def test_creates_the_handbook_mountpoints_too(self, tmp_path, fake_runtime):
        """⚑ The handbook BINDS are the seeds half's (they need ``<scope>.canon``
        resolution, which does not exist yet) but their MOUNTPOINTS are part of THIS
        one closed set. J-7 specifies the skeleton as a single set, an absent chapter
        must show as an empty root-owned dir, and creating them later would mean
        mkdir-ing into an already-555 tree."""
        home = tmp_path / "home"
        home.mkdir()
        core_defaults.materialize_canon_skeleton(home)

        assert (home / "canon" / "handbook" / "SYS_CONTENTS.md").is_file()
        for chapter in HANDBOOK_CHAPTERS:
            assert (home / "canon" / "handbook" / chapter).is_dir(), chapter

    def test_creates_the_import_fallback_entry_files(self, tmp_path, fake_runtime):
        """⚑⚑ F1: the three 0-byte IMPORT-FALLBACK files INSIDE the chapter dirs.

        ``SYS_CONTENTS.md`` imports all FOUR chapters UNCONDITIONALLY, and
        skip-if-absent governs the BIND, not the INDEX — so without these, every box
        with no workset chapter (i.e. every primary box) printed ``unresolved import
        @workset/directives/SYS_WORKSET.md`` on EVERY launch. With them, an unbound
        chapter RESOLVES-TO-EMPTY; a bound one has its whole directory replaced by the
        mount, so the store's real file shadows the fallback.
        """
        home = tmp_path / "home"
        home.mkdir()
        core_defaults.materialize_canon_skeleton(home)

        hb = home / "canon" / "handbook"
        for chapter, entry in core_defaults.HANDBOOK_FALLBACK_ENTRIES:
            fallback = hb / chapter / "directives" / entry
            assert fallback.is_file(), fallback
            assert fallback.read_bytes() == b"", "the fallback must be 0-byte"

    def test_general_gets_no_import_fallback(self, tmp_path, fake_runtime):
        """⚑ DELIBERATE ASYMMETRY. The system store ALWAYS supplies ``general``, so a
        fallback there would mask a genuinely missing system handbook — which is
        precisely what ``canon_hb_general`` being NON-optional exists to surface."""
        home = tmp_path / "home"
        home.mkdir()
        core_defaults.materialize_canon_skeleton(home)
        assert not (home / "canon" / "handbook" / "general" / "directives").exists()

    def test_the_fallback_names_match_what_SYS_CONTENTS_imports(self):
        """The fallback filenames are only useful if they are the ones the packaged
        index actually imports — spelled in a different file, so drift is silent."""
        contents = (
            templates._packaged_base_template()
            / "handbook" / "SYS_CONTENTS.md"
        ).read_text()
        for chapter, entry in core_defaults.HANDBOOK_FALLBACK_ENTRIES:
            assert f"@{chapter}/directives/{entry}" in contents, (
                f"SYS_CONTENTS.md does not import @{chapter}/directives/{entry}; "
                "the skeleton's import-fallback would resolve nothing"
            )

    def test_fallbacks_are_protected_like_the_rest_of_the_skeleton(
        self, tmp_path, fake_runtime,
    ):
        """They are MACHINERY, not content: root-owned and unwritable like every
        other skeleton entry, so the agent cannot edit its own 'empty chapter'."""
        home = tmp_path / "home"
        home.mkdir()
        core_defaults.materialize_canon_skeleton(home)

        chown_paths = fake_runtime.return_value.unshare_chown.call_args.args[0]
        covered = {str(p) for p in chown_paths}
        hb = home / "canon" / "handbook"
        for chapter, entry in core_defaults.HANDBOOK_FALLBACK_ENTRIES:
            assert str(hb / chapter / "directives" / entry) in covered
            assert str(hb / chapter / "directives") in covered

    def test_does_not_create_the_seeded_books(self, tmp_path, fake_runtime):
        """``notebook``/``workbook`` are SEEDED, agent-owned and writable. They become
        undeletable only because their parent is 555 — intended, per J-7."""
        home = tmp_path / "home"
        home.mkdir()
        core_defaults.materialize_canon_skeleton(home)
        assert not (home / "canon" / "notebook").exists()
        assert not (home / "canon" / "workbook").exists()

    def test_is_idempotent(self, tmp_path, fake_runtime):
        home = tmp_path / "home"
        home.mkdir()
        core_defaults.materialize_canon_skeleton(home)
        (home / "canon" / "notebook").mkdir()
        (home / "canon" / "notebook" / "keep.md").write_text("mine\n")

        core_defaults.materialize_canon_skeleton(home)  # must not raise or clobber
        assert (home / "canon" / "notebook" / "keep.md").read_text() == "mine\n"

    def test_chown_covers_everything_with_the_container_root_uid(
        self, tmp_path, fake_runtime,
    ):
        """⚑ THE UID ORACLE. ``chown 0:0`` inside ``podman unshare`` is the REAL host
        user, whom ``keep-id:uid=1000`` maps to the in-box AGENT — so 0 would leave the
        books agent-owned, the opposite of the intent. Pin the constant."""
        home = tmp_path / "home"
        home.mkdir()
        core_defaults.materialize_canon_skeleton(home)

        chown_paths, uid, gid = fake_runtime.return_value.unshare_chown.call_args.args
        assert (uid, gid) == (UNSHARE_BOX_ROOT_UID, UNSHARE_BOX_ROOT_GID)
        assert uid != 0, "0 inside podman unshare is the host user = the in-box agent"
        # ONE chown over the WHOLE skeleton — ownership is uniform, only modes split.
        expected = [home / rel for rel, _ in core_defaults.canon_skeleton_rels()]
        assert sorted(chown_paths) == sorted(expected)

    def test_chmod_splits_dirs_555_from_file_mountpoints_444(
        self, tmp_path, fake_runtime,
    ):
        """⚑ TWO MODES, TWO CALLS (spec J-7 banner, amended 2026-07-31).

        The split is not cosmetic on the directory side: 555 keeps the SEARCH bit, and
        a 444 directory would make crun's openat2 walk fail on every canon bind. On the
        file side 555 would mark a 0-byte ``.md`` executable for no reason.
        """
        home = tmp_path / "home"
        home.mkdir()
        core_defaults.materialize_canon_skeleton(home)

        calls = {
            mode: paths
            for (paths, mode) in (
                c.args for c in fake_runtime.return_value.unshare_chmod.call_args_list
            )
        }
        assert set(calls) == {CANON_SKELETON_DIR_MODE, CANON_SKELETON_FILE_MODE}
        assert (CANON_SKELETON_DIR_MODE, CANON_SKELETON_FILE_MODE) == ("555", "444")

        rels = core_defaults.canon_skeleton_rels()
        assert sorted(calls["555"]) == sorted(home / r for r, d in rels if d)
        assert sorted(calls["444"]) == sorted(home / r for r, d in rels if not d)

    def test_ownership_never_recurses_over_the_seeded_books(self, tmp_path, fake_runtime):
        """A ``-R`` sweep of ``canon/`` would take the agent's own notebook/workbook
        with it. The path list must be the enumerated skeleton, nothing more."""
        home = tmp_path / "home"
        home.mkdir()
        (home / "canon" / "notebook").mkdir(parents=True)
        core_defaults.materialize_canon_skeleton(home)

        chown_paths = fake_runtime.return_value.unshare_chown.call_args.args[0]
        assert home / "canon" / "notebook" not in chown_paths

    def test_falls_back_with_a_warning_when_no_runtime(self, tmp_path, caplog):
        """DEGRADED BUT FUNCTIONAL: box create must not hard-fail without podman —
        creating a box works today with no runtime installed — and the skeleton is
        what makes the binds land, so the box is fully usable, just not litter-proof.
        """
        from kanibako.runtime.container import ContainerError

        home = tmp_path / "home"
        home.mkdir()
        with patch("kanibako.runtime.container.ContainerRuntime",
                   side_effect=ContainerError("no podman")), \
                caplog.at_level(logging.WARNING):
            core_defaults.materialize_canon_skeleton(home)

        assert (home / "canon" / "bible" / "general").is_dir(), "skeleton still built"
        assert any("left writable" in r.message for r in caplog.records), caplog.text

    @pytest.mark.parametrize("failing", ("unshare_chown", "unshare_chmod"))
    def test_falls_back_with_a_warning_when_unshare_fails(
        self, tmp_path, caplog, failing: str,
    ):
        """docker (no ``unshare``) or a failing call — same degraded shape."""
        home = tmp_path / "home"
        home.mkdir()
        rt = MagicMock()
        rt.unshare_chown.return_value = True
        rt.unshare_chmod.return_value = True
        getattr(rt, failing).return_value = False
        with patch("kanibako.runtime.container.ContainerRuntime", return_value=rt), \
                caplog.at_level(logging.WARNING):
            core_defaults.materialize_canon_skeleton(home)

        assert (home / "canon" / "COLLECTION.md").is_file()
        # ⚑ The two arms say DIFFERENT things and must not be conflated: a failed
        # CHOWN leaves the tree agent-owned and writable; a failed CHMOD leaves it
        # root-owned at default modes, where the agent still cannot write.
        warning = " ".join(r.message for r in caplog.records)
        if failing == "unshare_chown":
            assert "left writable" in warning, caplog.text
        else:
            assert "root-owned but keep their default modes" in warning, caplog.text
            assert "left writable" not in warning, caplog.text

    def test_create_runs_the_skeleton_after_the_seed(self):
        """⚑ ORDER IS LOAD-BEARING. The seeds half seeds ``canon/{notebook,workbook}``
        UNDER the root this step makes 555; protect first and those copies die with
        EACCES. Asserted on the SOURCE so it cannot rot silently.
        """
        import inspect

        from kanibako.commands.box import _parser

        src = inspect.getsource(_parser.run_create)
        seed = src.index("seed_new_box(std, config, proj")
        skeleton = src.index("materialize_canon_skeleton(proj.shell_path)")
        clear = src.index("_clear_create_entry(std, proj)")
        assert seed < skeleton, "the skeleton must be materialized AFTER the seed"
        assert skeleton < clear, (
            "and INSIDE the create-journal window, so an interrupted create replays it"
        )


# ===========================================================================
# The writable seed layer is UNCHANGED by this cutover.
# ===========================================================================


class TestBaseTemplateSeedsTheNotebook:
    """The box-home seed source (``data/global/template/box/home``) after the canon
    restructure: it carries the NOTEBOOK + WORKBOOK (the box's own, agent-writable
    books) and seeds them into the box home create-if-absent. ⚑ The retired
    ``playbook/`` tree is gone — its content became the canon HANDBOOK, which is
    BOUND from a host store, never seeded (M-10)."""

    def test_packaged_source_carries_the_notebook(self):
        home_root = packaged_box_home_template()
        assert home_root is not None
        assert (home_root / "canon" / "notebook" / "MY_CONTENTS.md").is_file()
        assert (home_root / "canon" / "workbook" / "devnotes.md").is_file()
        # The retired roots are GONE from the package.
        base = _packaged_base_template()
        assert not (base / "playbook").exists()
        assert not (base / "notebook").exists()
        assert not (base / "workbook").exists()

    def test_install_stages_the_box_mould(self, std):
        install_packaged_templates(std, ["claude"])
        assert (
            std.template / "box" / "home" / "canon" / "notebook" / "MY_CONTENTS.md"
        ).is_file()

    def test_seed_lands_the_notebook_at_home(self, std, config, project_dir):
        """End-to-end: the base layer seeds ``~/canon/notebook/MY_CONTENTS.md`` at
        box create through the single keystore-routed seed (create-if-absent)."""
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
        assert (
            proj.shell_path / "canon" / "notebook" / "MY_CONTENTS.md"
        ).is_file()
        assert (
            proj.shell_path / "canon" / "workbook" / "devnotes.md"
        ).is_file()


class _FakeTarget:
    """Minimal resolved-agent stand-in for the seed seam (mirrors test_templates):
    only ``.name`` + empty ``default_seeds()`` are read for a non-descriptor target."""

    name = "claude"

    def default_seeds(self):
        return {}
