"""GOLDEN FIXTURE guardrail for the coalesced keyspace defaults (P6d).

The representation flip (P1-P5) made keyspace category VALUES structured
tuple/dict end-to-end, and the P6 coalesce pulled the ~87 scattered defaults +
guest-path literals into a HANDFUL of shipped declarative data files:

* ``kanibako/data/core-defaults.yaml``      — the ONE system/core file (everything
  NOT agent-specific: the ``box.masks`` vault default + the per-mode channel bind
  table), read by the thin loader :mod:`kanibako.core_defaults`.
* ``<plugin>/<agent>-defaults.yaml``        — ONE file PER AGENT inside its plugin
  package (claude/goose/codex), read by the thin loader
  :mod:`kanibako.agent_defaults`.
* :data:`kanibako.settings_resolve.GUEST_HOME` — the single one-line constant the
  box-side ``/home/agent`` literal derives from (NOT a new file).

This module is the standing GUARDRAIL over that surface.  It does TWO things:

1. **Specced-shape assertion** (catches a future re-flattening AT THE SOURCE):
   loads the system defaults file + each per-agent defaults file and asserts
   every value matches the SPECCED STRUCTURED shape from
   ``settings-keyspace-1.6.0-target.md`` §2a:
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

from kanibako import agent_defaults, core_defaults
from kanibako.settings_resolve import GUEST_HOME
from kanibako.targets.base import (
    BindKind,
    BindScope,
    HostSrcOrigin,
)

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
_PAIR_CATEGORIES = ("bindings", "caches", "seeded", "shared", "synced")

# The repo root (this file lives in <repo>/tests/).
_REPO_ROOT = Path(__file__).resolve().parent.parent

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
        """The loader surfaces ``masks`` as a real ``list[str]``."""
        masks = core_defaults.vault_mask_default()
        assert isinstance(masks, list)
        assert all(isinstance(m, str) for m in masks)
        assert masks == ["~/workspace/vault"]

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
        """Any ``shares`` block declares structured 2-/3-element bind pairs.

        ``shared`` is one of the spec §2a structured-pair categories.  Files that
        declare no shares (goose/codex) yield an empty map — also valid.
        """
        for package, filename in _AGENT_DEFAULTS:
            doc = _load_yaml(package, filename)
            for entry in doc.get("shares", []):
                assert isinstance(entry, dict), (
                    f"{filename}: share entry must be a mapping: {entry!r}"
                )
                assert "key" in entry and "host_src" in entry and "box_dest" in entry
                assert entry["box_dest"].startswith("$GUEST_HOME"), (
                    f"{filename}: share box_dest must be a $GUEST_HOME expression: "
                    f"{entry!r}"
                )

            # And the LOADED shares are real 2-/3-tuples (BindDefault).
            shares = agent_defaults.load_shares(package, filename)
            assert isinstance(shares, dict)
            for key, pair in shares.items():
                _assert_structured_pair(pair, f"{filename} share {key}")
                assert pair[1].startswith(GUEST_HOME), (
                    f"{filename}: loaded share {key} box_dest not under GUEST_HOME: "
                    f"{pair!r}"
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
        assert sites == ["src/kanibako/settings_resolve.py"], (
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
            _REPO_ROOT / "src" / "kanibako" / "core_defaults.py",
            _REPO_ROOT / "src" / "kanibako" / "agent_defaults.py",
        ]
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
