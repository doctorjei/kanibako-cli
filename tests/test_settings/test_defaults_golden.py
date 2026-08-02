"""GOLDEN FIXTURE guardrail for the coalesced keyspace defaults (P6d).

The representation flip (P1-P5) made keyspace category VALUES structured
tuple/dict end-to-end, and the P6 coalesce pulled the ~87 scattered defaults +
guest-path literals into a HANDFUL of shipped declarative data files:

* ``kanibako/data/core-defaults.yaml``      — the ONE system/core file (everything
  NOT agent-specific: the ``box.masks`` vault default + the per-mode channel bind
  table), read by the thin loader :mod:`kanibako.settings.core_defaults`.
* ``<plugin>/<agent>-defaults.yaml``        — ONE file PER AGENT inside its plugin
  package (claude/goose/codex), read by the thin loader
  :mod:`kanibako.settings.agent_defaults`.
* :data:`kanibako.settings.settings_resolve.GUEST_HOME` — the single one-line constant the
  box-side ``/home/agent`` literal derives from (NOT a new file).

This module is the standing GUARDRAIL over that surface.  It does TWO things:

1. **Specced-shape assertion** (catches a future re-flattening AT THE SOURCE):
   loads the system defaults file + each per-agent defaults file and asserts
   every value matches the SPECCED STRUCTURED shape from
   ``settings-keyspace-1.8.0.md`` §2a:
   ``bindings/caches/seeded/shared/synced`` = 2-/3-element lists/tuples,
   ``masks`` = a real list, and NO colon/comma-string value anywhere in a binding
   pair (the colon-string form is the divergence the flip reversed).

2. **De-hardcoding lock** (a GREP-GUARD): asserts NO raw ``/home/agent`` literal
   survives in the COALESCED-DEFAULTS SURFACE (the shipped data files + their
   loaders) outside the single :data:`GUEST_HOME` constant definition, and that
   ``GUEST_HOME`` is defined in exactly one place.

Scope note (design): the grep-guard locks the surface P6 actually coalesced — the
shipped defaults data files, the two loaders, and the GUEST_HOME constant.  It
deliberately does NOT blanket all of ``src/``: the legacy direct core-mount path
(``container.py``'s ``-v …:/home/agent…`` args + the kani_bin/vault seam emitters
in ``start.py``) still carries box-dest literals BY DESIGN — routing those through
``box.bindings.*`` is the still-deferred key-route rework step 3/4, not part of
this coalesce.  Locking those now would red on intentionally-unmigrated code; this
guard locks exactly what the coalesce de-hardcoded so a regression there is caught.
"""

from __future__ import annotations

import importlib.resources
import re
from pathlib import Path

import yaml

from kanibako.settings import agent_defaults, core_defaults
from kanibako.settings.settings_resolve import GUEST_HOME
from kanibako.targets.base import (
    BindKind,
    BindScope,
    HostSrcOrigin,
)
from tests.support.repo import REPO_ROOT

# --------------------------------------------------------------------------- #
# Surface under guard
# --------------------------------------------------------------------------- #

# Each agent plugin: (import package, defaults filename).  Mirrors the
# ``_DEFAULTS_PACKAGE`` / ``_DEFAULTS_FILE`` constants each plugin's target.py
# passes to the loader.
_AGENT_DEFAULTS = (
    ("kanibako.plugins.claude", "claude-defaults.yaml"),
    ("kanibako.plugins.goose", "goose-defaults.yaml"),
    ("kanibako.plugins.codex", "codex-defaults.yaml"),
)

# The structured-binding category names (spec §2a): each is a dict[name -> pair]
# where every pair is a 2-/3-element list/tuple — NEVER a colon-joined string.
_PAIR_CATEGORIES = ("bindings", "caches", "seeded", "common", "synced")

# The repo root — resolved once, in one place, so this file may live at any depth
# under ``tests/`` (see ``tests/support/repo.py``).
_REPO_ROOT = REPO_ROOT

_HOME_LITERAL = "/home/agent"


def _data_file_path(package: str, filename: str) -> Path:
    """Resolve a shipped data file to an on-disk path."""
    ref = importlib.resources.files(package).joinpath(filename)
    return Path(str(ref))


def _load_yaml(package: str, filename: str) -> dict:
    return yaml.safe_load(_data_file_path(package, filename).read_text()) or {}


# --------------------------------------------------------------------------- #
# Helpers — structured-shape predicates
# --------------------------------------------------------------------------- #

def _assert_structured_pair(value: object, where: str) -> None:
    """Assert *value* is a specced binding pair: a 2-/3-element list/tuple of str.

    Catches the reversed divergence: a colon-joined ``"host:box"`` string (or any
    scalar) here means the structured representation was re-flattened at the
    source.
    """
    assert isinstance(value, (list, tuple)), (
        f"{where}: binding value must be a structured list/tuple "
        f"(spec §2a), got {type(value).__name__}: {value!r}"
    )
    assert len(value) in (2, 3), (
        f"{where}: binding pair must have 2 or 3 elements "
        f"(host_src, box_dest[, options]), got {len(value)}: {value!r}"
    )
    for elem in value:
        assert isinstance(elem, str), (
            f"{where}: every binding element must be a str, got "
            f"{type(elem).__name__}: {elem!r}"
        )
    # The host_src + box_dest elements must not themselves be colon/comma-joined
    # (a re-flattened "host:box" element is the divergence §2a reversed); the
    # optional 3rd element is the mount-OPTIONS string (e.g. "Z,U") where a comma
    # is legitimate, so the joined-string guard applies to the path elements only.
    for elem in value[:2]:
        _assert_no_joined_string(elem, where)


def _assert_no_joined_string(elem: str, where: str) -> None:
    """Assert a host_src/box_dest element is not a colon/comma-joined string.

    A literal ``:`` separating two paths (host:box) or a ``,`` separating list
    members is the old flattened wire-form; the structured representation carries
    those as distinct tuple elements / real list members instead.  (A bare drive-
    less POSIX path never contains ``:``; a real box_dest never contains ``,``.)
    """
    assert ":" not in elem, (
        f"{where}: element looks colon-joined (old wire-form) — "
        f"structured pairs carry host/box as separate elements: {elem!r}"
    )
    assert "," not in elem, (
        f"{where}: element looks comma-joined (old list wire-form) — "
        f"masks/lists carry members as real list entries: {elem!r}"
    )


# --------------------------------------------------------------------------- #
# 1. SPECCED-SHAPE ASSERTIONS — system/core defaults file
# --------------------------------------------------------------------------- #

class TestCoreDefaultsShape:
    """The ONE system/core defaults file holds the specced structured shape."""

    def test_masks_is_real_list_of_box_dests(self):
        """``masks`` is a real ``list[box_dest]`` (spec §2a), NOT a comma-string."""
        doc = _load_yaml("kanibako.data", "core-defaults.yaml")
        masks = doc["masks"]
        assert isinstance(masks, list), (
            f"core masks must be a real list, got {type(masks).__name__}: {masks!r}"
        )
        for m in masks:
            assert isinstance(m, str), f"mask entry must be str, got {m!r}"
            _assert_no_joined_string(m, "core-defaults.yaml masks")

    def test_loader_masks_is_list(self):
        """The loader surfaces ``masks`` as a real ``list[str]`` — now EMPTY.

        The vestigial ``~/workspace/vault`` default mask was dropped (the vault
        moved out of the workspace in 1.6.0), so the default ``box.masks`` is an
        empty list; the seam stays so explicit ``box.masks`` declarations work.
        """
        masks = core_defaults.vault_mask_default()
        assert isinstance(masks, list)
        assert all(isinstance(m, str) for m in masks)
        assert masks == []

    def test_channel_entries_are_structured(self):
        """Each channel default carries the structural fields, box_dest a clean str.

        The channel table's HOST sources are runtime-probed (injected at the seam),
        so the file names them symbolically; what must be structured here is each
        entry's shape (key/source/box_dest/scope) and its box-side destination.
        """
        doc = _load_yaml("kanibako.data", "core-defaults.yaml")
        channels = doc["channels"]
        assert isinstance(channels, list) and channels
        for entry in channels:
            assert isinstance(entry, dict), f"channel entry must be a mapping: {entry!r}"
            for fld in ("key", "source", "box_dest", "scope"):
                assert fld in entry, f"channel entry missing {fld!r}: {entry!r}"
                assert isinstance(entry[fld], str), (
                    f"channel {fld} must be str: {entry!r}"
                )
            assert entry["scope"] in ("system", "workset"), (
                f"channel scope must be system|workset: {entry!r}"
            )
            _assert_no_joined_string(
                entry["box_dest"], f"core channel {entry['key']} box_dest"
            )

    def test_core_entries_are_structured(self):
        """Each core box-mount default carries the structural fields + clean dest.

        The core mounts' HOST sources are runtime-probed off ``ProjectPaths``
        (injected at the seam), so the file names them symbolically; what must be
        structured here is each entry's shape (key/category/source/box_dest/
        options/scope) and its box-side destination + options (the per-entry mount
        OPTIONS being the binding tuple's 3rd slot, spec §2a).
        """
        doc = _load_yaml("kanibako.data", "core-defaults.yaml")
        core = doc["core"]
        assert isinstance(core, list) and core
        for entry in core:
            assert isinstance(entry, dict), f"core entry must be a mapping: {entry!r}"
            for fld in ("key", "category", "source", "box_dest", "options", "scope"):
                assert fld in entry, f"core entry missing {fld!r}: {entry!r}"
                assert isinstance(entry[fld], str), (
                    f"core {fld} must be str: {entry!r}"
                )
            assert entry["category"] in ("bindings.ro", "bindings.rw"), (
                f"core category must be a bindings.* token: {entry!r}"
            )
            assert entry["scope"] in ("system", "vault"), (
                f"core scope must be system|vault: {entry!r}"
            )
            _assert_no_joined_string(
                entry["box_dest"], f"core mount {entry['key']} box_dest"
            )

    def test_images_entries_are_structured_and_keyed(self):
        """The image-sharing table: structured entries + the B3 key routing.

        Shape first (the golden guard's job): every ``images:`` entry carries the
        structural fields with clean box_dests.  Then the two B3 pins this file
        is the right home for, because both are properties of the SHIPPED DATA:

        * the store bind's entry is ``images`` — NOT ``images_store``.  The bind
          row and the scalar user key ``box.images_store`` must not share a name
          (the B3 rider; a revived ``images_store`` entry here would silently
          re-open the collision the rename closed);
        * the store bind's emitted host_src is the @-ref ``@box.images_store``
          (``meta_ref``), so the mount follows the USER KEY (spec §2b/D-M8) —
          a bare probed literal here would disconnect the key from its bind.
        """
        doc = _load_yaml("kanibako.data", "core-defaults.yaml")
        images = doc["images"]
        assert isinstance(images, list) and images
        for entry in images:
            assert isinstance(entry, dict), f"images entry must be a mapping: {entry!r}"
            for fld in ("key", "category", "source", "box_dest", "options"):
                assert fld in entry, f"images entry missing {fld!r}: {entry!r}"
                assert isinstance(entry[fld], str), (
                    f"images {fld} must be str: {entry!r}"
                )
            assert entry["category"] == "bindings.ro", (
                f"images category must be bindings.ro: {entry!r}"
            )
            _assert_no_joined_string(
                entry["box_dest"], f"images mount {entry['key']} box_dest"
            )
        keys = [entry["key"] for entry in images]
        assert keys == ["images", "images_conf"], (
            f"images table must be exactly the store bind ('images' — renamed "
            f"from 'images_store', B3 rider) + the internal 'images_conf': {keys}"
        )
        assert images[0].get("meta_ref") == "@box.images_store", (
            "the store bind's host_src must be the @-ref to the user key "
            f"box.images_store (spec §2b/D-M8): {images[0]!r}"
        )


# --------------------------------------------------------------------------- #
# 1. SPECCED-SHAPE ASSERTIONS — per-agent defaults files
# --------------------------------------------------------------------------- #

class TestAgentDefaultsShape:
    """Each per-agent defaults file holds the specced structured shape."""

    def test_descriptor_bindings_are_structured(self):
        """Every ``descriptor.bindings`` entry is a structured mapping (not a string).

        The file declares each binding's ORIGIN (which detected install field
        supplies the host source) + a ``$GUEST_HOME`` box_dest EXPRESSION + the
        kind/scope enums — a structured mapping, never a colon-joined string.
        """
        for package, filename in _AGENT_DEFAULTS:
            doc = _load_yaml(package, filename)
            bindings = doc["descriptor"]["bindings"]
            assert isinstance(bindings, list) and bindings, (
                f"{filename}: descriptor.bindings must be a non-empty list"
            )
            for b in bindings:
                assert isinstance(b, dict), (
                    f"{filename}: each binding must be a mapping, got {b!r}"
                )
                for fld in ("key", "origin", "box_dest", "kind", "scope"):
                    assert fld in b, f"{filename}: binding missing {fld!r}: {b!r}"
                # Enum-valued fields must name a real enum member.
                HostSrcOrigin(b["origin"])
                BindKind(b["kind"])
                BindScope(b["scope"])
                # box_dest is a $GUEST_HOME expression (de-hardcoded) — never a
                # raw /home/agent literal, never colon/comma-joined.
                assert b["box_dest"].startswith("$GUEST_HOME"), (
                    f"{filename}: binding box_dest must be a $GUEST_HOME expression "
                    f"(de-hardcoded), got {b['box_dest']!r}"
                )
                _assert_no_joined_string(
                    b["box_dest"], f"{filename} binding {b['key']} box_dest"
                )

    def test_loaded_descriptors_build_structured_bindings(self):
        """Loading each file yields real ``Binding`` objects with str box_dests."""
        for package, filename in _AGENT_DEFAULTS:
            desc = agent_defaults.load_descriptor(package, filename)
            assert desc.bindings, f"{filename}: descriptor has no bindings"
            for b in desc.bindings:
                assert isinstance(b.box_dest, str)
                assert isinstance(b.origin, HostSrcOrigin)
                assert isinstance(b.kind, BindKind)
                assert isinstance(b.scope, BindScope)
                # Expanded box_dest is anchored under the single GUEST_HOME SoT.
                assert b.box_dest.startswith(GUEST_HOME), (
                    f"{filename}: expanded box_dest {b.box_dest!r} not under GUEST_HOME"
                )

    def test_shares_are_structured_pairs(self):
        """Any ``common`` block declares structured 2-/3-element bind pairs.

        ``common`` is one of the spec §2a structured-pair categories.  Files that
        declare no ``common`` entries (goose/codex) yield an empty map — also valid.

        ⚑ THE host_src ASSERTIONS ARE LOAD-BEARING, at BOTH levels.  Every generic
        predicate here (2-/3-tuple, no ``:``, no ``,``, dest under ``$GUEST_HOME``)
        is satisfied by an ``@``-ref just as it was by a bare leaf, so without an
        explicit shape check this "golden" guard would silently stop guarding
        ``host_src`` across P3's declaration-rooting flip.  The YAML level pins the
        AUTHORED form (a bare leaf is what an author may write); the LOADED level
        pins the STORED form (self-resolving, spec §2a).
        """
        from kanibako.settings.agent_config import agent_category_root_ref, is_self_resolving

        for package, filename in _AGENT_DEFAULTS:
            doc = _load_yaml(package, filename)
            agent = filename.removesuffix("-defaults.yaml")
            for entry in doc.get("common", []):
                assert isinstance(entry, dict), (
                    f"{filename}: common entry must be a mapping: {entry!r}"
                )
                assert "key" in entry and "host_src" in entry and "box_dest" in entry
                assert entry["box_dest"].startswith("$GUEST_HOME"), (
                    f"{filename}: common box_dest must be a $GUEST_HOME expression: "
                    f"{entry!r}"
                )
                assert isinstance(entry["host_src"], str) and entry["host_src"], (
                    f"{filename}: common host_src must be a non-empty string: "
                    f"{entry!r}"
                )

            # And the LOADED entries are real 2-/3-tuples (BindDefault).
            common_binds = agent_defaults.load_common(package, filename, agent)
            assert all(k.startswith(f"agent.{agent}.common.") for k in common_binds), (
                f"{filename}: loaded common keys must be DISCRIMINATED: "
                f"{list(common_binds)}"
            )
            assert isinstance(common_binds, dict)
            root_ref = agent_category_root_ref(agent, "common")
            for key, pair in common_binds.items():
                _assert_structured_pair(pair, f"{filename} common {key}")
                assert pair[1].startswith(GUEST_HOME), (
                    f"{filename}: loaded common {key} box_dest not under GUEST_HOME: "
                    f"{pair!r}"
                )
                # The STORED source must resolve on its own — a bare relative leaf
                # here would need a layer to prepend a root, which is exactly what
                # spec §2a forbids.
                assert is_self_resolving(pair[0]), (
                    f"{filename}: loaded common {key} host_src {pair[0]!r} does not "
                    "resolve on its own (spec §2a L474-486)"
                )
                # A leaf the author wrote bare is rooted at the agent DECLARATION
                # ROOT; an author-supplied self-resolving source is stored verbatim.
                authored = next(
                    e["host_src"] for e in doc.get("common", [])
                    if f"agent.{agent}.common.{e['key']}" == key
                )
                expected = (
                    authored if is_self_resolving(authored)
                    else f"{root_ref}/{authored}"
                )
                # ⚑ TAUTOLOGY WARNING — this comparison builds ``expected`` from
                # the SAME helpers the loader uses, so it verifies the loader APPLIED
                # the rule, not that the rule produces the right PATH. Change the
                # layout (e.g. drop the ``common/`` segment) and both sides move
                # together and this stays GREEN. The LITERAL carriers — the ones that
                # spell the resolved path out and go red on a layout change — are
                # ``test_targets/test_claude.py::TestDefaultShares`` (the stored ref)
                # and ``test_categories_live.py::TestDeclarationRoots::
                # test_claude_commons_resolve_under_common_dir`` (the resolved mount).
                assert pair[0] == expected, (
                    f"{filename}: loaded common {key} host_src {pair[0]!r} != "
                    f"{expected!r}"
                )


# --------------------------------------------------------------------------- #
# 2. DE-HARDCODING LOCK — grep-guard over the coalesced-defaults surface
# --------------------------------------------------------------------------- #

class TestNoHardcodedGuestHome:
    """No raw ``/home/agent`` literal survives in the coalesced-defaults surface.

    Outside the single :data:`GUEST_HOME` constant definition, the de-hardcoded
    surface (the shipped data files + their loaders) must derive the box-side home
    from the constant — never re-embed the literal.
    """

    def test_guest_home_defined_exactly_once(self):
        """``GUEST_HOME`` is assigned in exactly one place (settings_resolve.py)."""
        src = (_REPO_ROOT / "src" / "kanibako").rglob("*.py")
        assign_re = re.compile(r"^GUEST_HOME\s*=\s*[\"']")
        sites: list[str] = []
        for py in src:
            for ln in py.read_text().splitlines():
                if assign_re.match(ln):
                    sites.append(str(py.relative_to(_REPO_ROOT)))
        assert sites == ["src/kanibako/settings/settings_resolve.py"], (
            f"GUEST_HOME must be defined exactly once in settings_resolve.py, "
            f"found assignments in: {sites}"
        )
        # And the value is the box home literal (the single allowed site).
        assert GUEST_HOME == _HOME_LITERAL

    def test_no_home_literal_in_shipped_data_files(self):
        """The shipped defaults DATA files carry NO ``/home/agent`` literal.

        Every in-box destination in the data files is a ``~``/``$GUEST_HOME``
        expression; a raw ``/home/agent`` would mean a re-hardcoded box path.
        (Comment lines are exempt — they are not data the loaders read; the
        loaders parse YAML values, and the assertions above already prove those
        are ``$GUEST_HOME`` expressions.)
        """
        data_files = [
            _data_file_path("kanibako.data", "core-defaults.yaml"),
        ]
        for package, filename in _AGENT_DEFAULTS:
            data_files.append(_data_file_path(package, filename))

        offenders: list[str] = []
        for f in data_files:
            for i, ln in enumerate(f.read_text().splitlines(), 1):
                stripped = ln.lstrip()
                if stripped.startswith("#"):
                    continue
                # Strip a trailing inline comment before checking the value.
                value_part = ln.split("#", 1)[0]
                if _HOME_LITERAL in value_part:
                    offenders.append(f"{f.name}:{i}: {ln.strip()}")
        assert not offenders, (
            "raw /home/agent literal in shipped defaults data (use a "
            "$GUEST_HOME/~ expression instead):\n  " + "\n  ".join(offenders)
        )

    def test_no_home_literal_in_defaults_loaders(self):
        """The two thin loaders carry NO ``/home/agent`` STRING LITERAL in code.

        The loaders expand box paths from the imported :data:`GUEST_HOME`; a code
        literal here would bypass the single SoT.  Comment/docstring mentions of
        the path (explaining the de-hardcoding) are exempt — only executable
        string literals are caught.
        """
        loaders = [
            _REPO_ROOT / "src" / "kanibako" / "settings" / "core_defaults.py",
            _REPO_ROOT / "src" / "kanibako" / "settings" / "agent_defaults.py",
        ]
        for f in loaders:
            # The guard is only as good as its target: a path that stopped
            # existing (a module move) would make the scan below pass over
            # nothing and report clean.
            assert f.is_file(), f"defaults loader not found at {f} — fix the path"
        # A /home/agent inside a quoted string literal (single or double quote).
        literal_re = re.compile(r"""['"][^'"]*/home/agent[^'"]*['"]""")
        offenders: list[str] = []
        for f in loaders:
            for i, ln in enumerate(f.read_text().splitlines(), 1):
                code = ln.split("#", 1)[0]
                if literal_re.search(code):
                    offenders.append(f"{f.name}:{i}: {ln.strip()}")
        assert not offenders, (
            "raw /home/agent string literal in a defaults loader (expand from "
            "GUEST_HOME instead):\n  " + "\n  ".join(offenders)
        )
