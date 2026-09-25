"""MANIFEST CONFORMANCE — the shipped keyspace REGISTRY asserted against the code.

``kanibako/data/keyspace-manifest.yaml`` is the machine-readable projection of
``specs/settings-keyspace-1.8.0.md``.  It SHIPS IN THE WHEEL as release authority, and
until this module existed nothing checked that it still described the code: it was a
3000-line hand-maintained document whose every value was a VERBATIM COPY of a literal
living somewhere in ``src/``.  Copies do not announce their drift.

⚑ THE DIRECTION IS CODE ← REGISTRY, BOTH WAYS, AND NEITHER IS GENERATED FROM THE OTHER.
Generating the ``DECLARED_*`` frozensets from the manifest was measured and DECLINED
(the manifest's own header records it): a module-scope generation would put a 3000-line
YAML parse on the CLI hot path, would destroy ~90 lines of load-bearing per-entry
commentary inside the frozensets (P7), and would narrow nothing ``key_validity`` does
not already refuse (P4).  Set-equality catches the same drift one gate-run later, at
none of that cost.  So: two carriers, asserted equal here.

⚑⚑ EXEMPTIONS ARE A NAMED TABLE, NEVER A SILENT SKIP.  Where a manifest value has no
code oracle, this file says WHICH rows and WHY, and
:meth:`TestDefaultsCoverage.test_every_default_row_is_pinned_or_named` asserts the
pinned set and the exemption table TOGETHER cover every row the manifest gives a
``default:``.  A new default row is therefore RED until somebody classifies it — that
exhaustiveness, not the individual pins, is what keeps this file from rotting into a
sample.  The same anti-vacuity discipline as ``test_defaults_golden.py``: assert the
corpus is the size it was measured at, assert the targets exist, and say why anything
is left out.

⚑ THE SAME DISCIPLINE COVERS THE ``value:`` CELL (section 4b).  Thirty-one rows carry
a ``value:`` instead of a ``default:`` — the comparable cell for ``meta.*`` rows — and
until section 4b existed nothing pinned them: a ``value:`` edit was green by
construction.  :data:`PINNED_VALUE_KEYS` and :data:`EXEMPT_VALUE_KEYS` partition those
31 exactly as the default tables partition the 79, and the ten rows carrying NEITHER
cell are classified by name in :data:`NEITHER_CELL_KEYS` so a lost cell reds here.

⚑ WHAT IS **NOT** HERE, AND WHERE IT LIVES INSTEAD: the FAMILY half of key-set
conformance — the 9 category families and the ``categories.scopes`` ``agent.active``
spelling.  It is **CLOSED**, by ``tests/test_settings/test_manifest_enforces.py`` §4
(``TestTheFamilySetIsTheDeliveryTable``, landed 2026-08-23), whose §4 header answers all
three reasons this note used to give for the gap.  Note the ARROW differs: that file is
MANIFEST → CODE, this one is CODE → REGISTRY, which is why the two are not merged.  This
file is the SCALAR half, and its own ``BIND_CATEGORIES <= declared_categories`` case
below stays — a names-only SUBSET over 6 of the 9, belonging to this file's arrow.

🛑 DO NOT conformance-test the manifest's ``set:`` column against
``config_keys.KNOWN_CONFIG_KEYS``.  That set's own quarantine block says it is
HAND-MAINTAINED and DELIBERATELY INCOMPLETE, and deriving it from the declaration SoT
was proposed and DECLINED.  A test against it files six false findings.  The routing
table ``config_keys._KEY_ROUTES`` is the honest target, and only in the directions
measured true below.

Indent note: 4 spaces, matching every sibling in ``tests/test_settings/`` (house style
is 2, but this directory — and ``test_defaults_golden.py``, the file this one pairs
with — are 4).
"""

from __future__ import annotations

import importlib.resources
import re
from pathlib import Path

import pytest
import yaml

from kanibako import kuid
from kanibako.launch.templates import agent_template_defaults, template_seed_defaults
from kanibako.settings import core_defaults
from kanibako.settings.config import (
    AGENT_META_FILE,
    WORKSET_META_FILE,
    KanibakoConfig,
    box_scalar_defaults_floor,
    coerce_bool,
    read_box_enable_vault,
    read_workset_skip_kuid_check,
)
from kanibako.settings.config_keys import _KEY_ROUTES
from kanibako.settings.kb_store import SCOPE_CONTAINMENT
from kanibako.settings.keyspace_manifest import (
    KEYSPACE_MANIFEST_FILENAME,
    manifest_doc,
)
from kanibako.settings.paths import BoxMode
from kanibako.settings.settings_keyspace import (
    ACCESS_TIERS,
    BIND_CATEGORIES,
    DECLARED_AGENT_LEAVES,
    DECLARED_BOX_AUTH_LEAVES,
    DECLARED_BOX_LEAVES,
    DECLARED_CONFIG_LEAVES,
    DECLARED_META_AGENT_AUTH_LEAVES,
    DECLARED_META_AGENT_LEAVES,
    DECLARED_META_ASSEMBLY_LEAVES,
    DECLARED_META_BOX_AUTH_LEAVES,
    DECLARED_META_BOX_LEAVES,
    DECLARED_META_RUNTIME_LEAVES,
    DECLARED_META_RUNTIME_ADMIN_LEAVES,
    DECLARED_META_RUNTIME_USER_LEAVES,
    DECLARED_META_WORKSET_LEAVES,
    DECLARED_META_WORKSET_AUTH_LEAVES,
    DECLARED_SYSTEM_AUTH_LEAVES,
    DECLARED_SYSTEM_CHANNEL_LEAVES,
    DECLARED_SYSTEM_HELPERS_LEAVES,
    DECLARED_SYSTEM_LEAVES,
    DECLARED_WORKSET_AUTH_LEAVES,
    DECLARED_WORKSET_CHANNEL_LEAVES,
    DECLARED_WORKSET_LEAVES,
    access_default,
    key_validity,
)
from kanibako.settings.settings_launch import (
    auth_chain_floor,
    meta_agent_grammar_floor,
    meta_agent_path_floor,
    meta_identity_floor,
    meta_runtime_floor,
    workset_anchor_floor,
)
from kanibako.settings.settings_resolve import SettingsError
from kanibako.settings.bootstrap import CONFIG_PATH_DEFAULTS, SYSTEM_PATH_DEFAULTS

# --------------------------------------------------------------------------- #
# Fixtures of fact
# --------------------------------------------------------------------------- #

#: The three box modes.  The manifest spells a per-mode default as a map keyed by
#: exactly these; ``settings_launch._BOX_MODES`` is the code's own set and
#: :meth:`TestManifestLoader.test_the_mode_map_keys_are_the_code_modes` pins the
#: agreement rather than trusting this literal.
MODES = ("primary", "named", "standalone")

#: A discriminator to substitute for the manifest's ``<agent>`` placeholder.  Any
#: legal agent segment does; the parametric row is about SHAPE, not identity.
PROBE_AGENT = "claude"

#: Agents ``key_validity`` is told are real (it takes the set by injection, for purity).
PROBE_AGENTS = frozenset({"claude", "codex", "goose", "navigator℘claude"})

#: A path that does not exist — the "nothing stored" argument for the read-with-default
#: accessors, which is the only way to observe their declared default.
NO_SETTINGS_FILE = Path("/nonexistent/kanibako-conformance-probe/settings.yaml")


def _keys() -> dict:
    """The manifest's ``keys:`` table."""
    return manifest_doc()["keys"]


def _default(key: str) -> object:
    """The manifest's declared ``default:`` for *key* — KeyError if the row has none."""
    return _keys()[key]["default"]


def _value(key: str) -> object:
    """The manifest's declared ``value:`` for *key* — KeyError if the row has none."""
    return _keys()[key]["value"]


def _per_mode(value: object) -> dict[str, object]:
    """A manifest default as a {mode: value} map — a scalar broadcasts to every mode."""
    if isinstance(value, dict) and set(value) == set(MODES):
        return dict(value)
    return {mode: value for mode in MODES}


def _unwrap(value: str) -> str:
    """Strip the manifest's ``(…)`` STRUCTURED-VALUE notation off a bind default.

    ``bind_default_entries`` writes a bind's host_src as ``(@some.key/path)`` — the
    parentheses mark "this is the value half of a dest-keyed entry", they are not part
    of the reference.  The code carries the bare reference.
    """
    text = str(value)
    return text[1:-1] if text.startswith("(") and text.endswith(")") else text


# --------------------------------------------------------------------------- #
# 1. The loader
# --------------------------------------------------------------------------- #

class TestManifestLoader:
    """``keyspace_manifest`` reads the ARTEFACT, caches the parse, and copies out."""

    def test_it_reads_the_installed_package_data_not_the_checkout(self):
        """The artefact property, stated once here instead of in four test docstrings.

        Four ad-hoc ``importlib.resources`` reads (three in
        ``test_settings_keyspace.py``, one in ``test_config_dest_parity.py``) each
        re-derived this; they now go through the loader.  What makes a guard a
        statement about the SHIPPED WHEEL rather than about this working tree is that
        the path comes from ``importlib.resources``, so pin that it does.
        """
        shipped = Path(
            str(importlib.resources.files("kanibako.data").joinpath(
                KEYSPACE_MANIFEST_FILENAME))
        )
        assert shipped.is_file(), f"packaged manifest not found at {shipped}"
        assert manifest_doc() == yaml.safe_load(shipped.read_text())

    def test_it_hands_out_a_fresh_document_every_call(self):
        """Copy-out at the boundary (P8): the parse is shared, the document is not.

        The parse is ``lru_cache``d because the manifest is immutable packaged data and
        parsing it costs ~137 ms.  That cache is exactly why a shared dict would be
        dangerous — a caller's mutation would outlive the test that made it.
        """
        first = manifest_doc()
        assert manifest_doc() is not first
        first["keys"]["config.data"]["default"] = "clobbered"
        assert manifest_doc()["keys"]["config.data"]["default"] != "clobbered"

    def test_the_document_carries_the_sections_this_file_asserts_against(self):
        """Anti-vacuity: name the sections, so a renamed one reds here, not silently."""
        doc = manifest_doc()
        for section in ("registry", "policy", "categories", "keys",
                        "bind_default_entries", "not_keys"):
            assert section in doc, f"manifest section {section!r} is missing"
        assert len(doc["keys"]) == 123, (
            f"the manifest declares {len(doc['keys'])} key rows, not the 123 this "
            f"file's counts were measured against — re-measure, do not adjust blindly"
        )

    def test_the_mode_map_keys_are_the_code_modes(self):
        """:data:`MODES` is the code's own box-mode set, not a literal that can drift."""
        from kanibako.settings.settings_launch import _BOX_MODES

        assert set(MODES) == set(_BOX_MODES)


# --------------------------------------------------------------------------- #
# 2. DEFAULTS conformance — the classes that HAVE a code oracle
# --------------------------------------------------------------------------- #
#
# Each set below is (a) the corpus of a class and (b) an input to the exhaustiveness
# case at the end.  Keep them in sync by ADDING to a class, never by editing the
# exhaustiveness assertion.

#: (i-a) The Layer-1 config + Layer-2 system PATH defaults.
_PATH_ORACLE: dict[str, str] = {**CONFIG_PATH_DEFAULTS, **SYSTEM_PATH_DEFAULTS}

#: (i-b) The layout anchors built per mode by ``settings_launch.workset_anchor_floor``.
_ANCHOR_KEYS = (
    "workset.boxes", "workset.vault_ro", "workset.vault_rw", "workset.logs",
    "workset.canon", "box.canon",
)

#: (i-b2) The NON-LAYOUT ``workset.*`` scalars the SAME builder floors, split by whether
#: the manifest gives standalone a real arm.  Added 2026-08-29: all three were declared
#: rows that no floor emitted in any mode, so a whole-value ``@``-ref to one resolved to
#: ``__MISSING__`` at launch — the ``workset.channelroot`` defect (R-35, "fix the CODE")
#: applied to the rows it left behind.
#: ⚑ ``workset.skip_kuid_check`` is ALSO in ``_SCALAR_KEYS`` below, deliberately: the
#: floor and ``config.read_workset_skip_kuid_check`` are TWO carriers of one bool, and
#: pinning both to the manifest is what stops them drifting apart.
_ANCHOR_SCALAR_KEYS = ("workset.skip_kuid_check",)

#: (i-b3) A VALUE at PRIMARY/NAMED only — their standalone arms are not values:
#: ``workset.registry`` declares ``<None>`` (a lone box has no registry tier) and
#: ``workset.template`` declares ``<None>`` (a lone box has no template tier — a workset
#: template seeds FUTURE boxes, of which a standalone root has none), and the floor SUPPLIES
#: both as a present ``None`` ([R177]: a declared ``<None>`` is a value, not an absence);
#: ``workset.kuid`` declares the PROSE ``<generated at creation>`` and is NOT emitted
#: (``paths.establish_standalone`` mints a real id into the box's own file at create).
#: ⚑ ``workset.template`` JOINED 2026-08-29 from the E1 "path join at use" exemption,
#: which is now RETIRED ENTIRELY (see the tombstone at section 4). It was filed there as
#: "no literal anywhere to compare the
#: manifest to", which was never true — ``launch/templates.py`` wrote one — and the row was
#: ALSO emitted by no floor, so ``@workset.template`` resolved to ``__MISSING__`` for every
#: box that already existed. ``workset_anchor_floor`` now spells the formula (the
#: ``workset.registry`` shape, one more time), and the seed table only references the key.
_ANCHOR_SCALAR_KEYS_PRIMARY_NAMED = (
    "workset.registry", "workset.kuid", "workset.template",
)

#: (i-b4) The MIRROR of the block above — real NAMED and STANDALONE arms, ``null`` at
#: PRIMARY.  ⚑ ``workset.workspaces`` is the only such row, and it is NOT in the tuples
#: above because the floor carries it as a RESOLVED LITERAL rather than the spec formula
#: (``settings/workset_dirkeys.py`` names it first among the keys read on the DETECTION
#: side, before any snapshot exists, so a formula would let detection and the keyspace
#: answer "what kind of box is this" two ways).  A literal cannot be compared to a
#: manifest formula by string equality, so the oracle FOLLOWS the formula one hop against
#: the deriver — the :class:`TestWorksetChannelDefaults` shape, for the same reason.
#: 🛑 The PRIMARY arm is an ABSENCE and stays one: the code honors a primary ``workspaces``
#: repoint (``project.workset.default_workset``), but that divergence is RULED and the
#: user's (manifest note, B2-Editor S-1 — *"do NOT 'conform' the code to the null"*).
_WORKSPACES_KEY = "workset.workspaces"

#: (i-c) The auth 3-tier chain built per mode by ``settings_launch.auth_chain_floor``.
#: ⚑ SIX, not the three the wiring estimate projected: the three ``workset.auth.*``
#: rows are per-mode MAPS whose arms are literals in the same floor builder, so they
#: pin exactly like the box/system rows.  Measured 2026-08-15, all six identical.
_AUTH_KEYS = (
    "system.auth.share_allowed", "box.auth.global_enabled", "box.auth.workset_enabled",
    "workset.auth.share_allowed", "workset.auth.global_sync", "workset.auth.path",
)

#: (i-d) Scalars whose carrier is a config dataclass field or a read-with-default.
#: ⚑ ``box.enable_vault`` STAYS HERE (2026-08-29) even though its carrier changed from the
#: reader to a ``KanibakoConfig`` field: this tuple is "which rows this class pins a value
#: for", not "which artefact carries it", and the row is still one of them. What moved is
#: the ORACLE inside :meth:`TestScalarDefaults._oracle`. Its dual membership — here and in
#: ``defaults_inventory``'s field group — is the same deliberate two-carrier arrangement
#: ``workset.skip_kuid_check`` has, and the twin case in this class asserts the pair equal.
_SCALAR_KEYS = (
    "box.image", "box.share_images", "workset.skip_kuid_check", "box.enable_vault",
)

#: The ``agent.default.*`` BEHAVIOR floor (spec §2d), typed by the manifest itself.
_BEHAVIOR_KEYS = (
    "agent.default.access", "agent.default.allow_helpers",
    "agent.default.continue_mode", "agent.default.bootstrap",
    "agent.default.label",
)

#: The ``agent.shell.*`` tier floor (spec §2d fence, D2) — the shell tier's OWN
#: values, installed unconditionally by ``core_defaults.shell_tier_defaults``.
#: Typed by the manifest itself, like the behavior tuple above.  (The shell
#: `canon` arm is dynamic and sits in ``_SINGLETON_KEYS`` with its producer.)
#: ⚑ ``bootstrap``/``run_args``/``transform`` joined 2026-09-24 from E3, floored by the
#: same ``agent_shell:`` table: ``bootstrap`` as the tier's own ``tmux`` (it no longer
#: falls back to ``agent.default``), the other two as PRESENT ``None``.
_SHELL_TIER_KEYS = (
    "agent.shell.label", "agent.shell.access", "agent.shell.allow_helpers",
    "agent.shell.bootstrap", "agent.shell.run_args", "agent.shell.transform",
)

#: (i-e) + the kuid sentinel — one-off rows with a single named carrier each.
#: ``box.env.COLORTERM`` is the third: the ONE ``env`` member kanibako itself ships a
#: default for, carried by ``core_defaults.env_default_categories`` (spec §2b:867).
#: ``agent.default.template`` is the fourth: the §2d DEFAULT-tier arm of the layer-2
#: template SOURCE, carried by ``launch.templates.agent_template_defaults`` beside the
#: per-node arm.  ⚑ Only the DEFAULT arm is pinned here — the per-NODE arm stays in
#: ``NO_ORACLE_REF_HOP`` below, where its VALUE is now asserted outright (finding 1 is
#: closed; what is left is the ``@``-hop the canon sibling has too).
#: ⚑ The helper-hub SPAWN BUDGET pair joined 2026-09-19 with the keys themselves. Their
#: carrier is a pair of MODULE CONSTANTS rather than a floor builder, and that is the
#: whole reason they sit in this class: the spawn verb resolves the budget in-box,
#: before any snapshot exists, so no launch floor installs them and there is no emitter
#: output to read.
#: ⚑ ``agent.default.env.TERM`` joined 2026-09-20, the SECOND ``env`` member kanibako
#: ships a default for; its default is an EXPRESSION (``$TERM``), as
#: ``box.env.COLORTERM``'s became the same day (``$COLORTERM``). It has
#: the same carrier as ``box.env.COLORTERM`` — ``core_defaults.env_default_categories``
#: — so the oracle below reads the emitter ONCE and pins both, which is also what keeps
#: the "exactly the shipped table" half of that case honest as the table grows.
#: ⚑ ``agent.shell.template`` joined 2026-09-24 from E3: the §2d shell fence's
#: ``<None>``, floored as a PRESENT ``None`` by ``agent_template_defaults``' shell arm.
_SINGLETON_KEYS = (
    "agent.default.canon", "workset.kuid", "box.env.COLORTERM",
    "agent.default.env.TERM",
    "agent.default.template",
    "agent.shell.canon",
    "agent.shell.template",
    "system.helpers.depth", "system.helpers.breadth",
)

#: (i-f) The workset CHANNEL family — ``workset.channelroot`` plus the six declared
#: ``workset.channels.*`` leaves (spec §2c), derived by ``channels/channels.py``.
#: ⚑ FORMERLY EXEMPT (E1).  See :class:`TestWorksetChannelDefaults` for the oracle and
#: the E1 comment for why the exemption was wrong.
_CHANNEL_KEYS = (
    "workset.channelroot",
    "workset.channels.common", "workset.channels.chat", "workset.channels.broadcast",
    "workset.channels.share", "workset.channels.mailboxes",
    "workset.channels.share_global",
)

#: Every manifest ``keys:`` row this file pins a VALUE for.
PINNED_DEFAULT_KEYS: frozenset[str] = frozenset(
    set(_PATH_ORACLE) | set(_ANCHOR_KEYS) | set(_ANCHOR_SCALAR_KEYS)
    | set(_ANCHOR_SCALAR_KEYS_PRIMARY_NAMED) | {_WORKSPACES_KEY} | set(_AUTH_KEYS)
    | set(_SCALAR_KEYS) | set(_BEHAVIOR_KEYS) | set(_SHELL_TIER_KEYS)
    | set(_SINGLETON_KEYS) | set(_CHANNEL_KEYS)
)


class TestPathDefaults:
    """(i-a) The 18 path defaults ARE ``bootstrap``' two tables, verbatim."""

    def test_the_corpus_is_the_two_declared_tables(self):
        assert len(CONFIG_PATH_DEFAULTS) == 6
        assert len(SYSTEM_PATH_DEFAULTS) == 12
        assert len(_PATH_ORACLE) == 18, "the two tables must not overlap"

    @pytest.mark.parametrize("key", sorted(_PATH_ORACLE))
    def test_the_manifest_default_is_the_code_literal(self, key):
        assert _default(key) == _PATH_ORACLE[key], (
            f"{key}: manifest says {_default(key)!r}, bootstrap says "
            f"{_PATH_ORACLE[key]!r}"
        )


class TestAnchorDefaults:
    """(i-b) The layout anchors ARE ``workset_anchor_floor``'s per-mode formulas.

    Every anchor is checked in ALL THREE modes, which is what makes the two mode-MAPS
    (``workset.boxes``, ``workset.logs``) real pins rather than a check of one arm: the
    standalone arm of each is a different formula, and it is the arm a lone box runs.
    """

    @staticmethod
    def _floors() -> dict[str, dict[str, object]]:
        return {mode: workset_anchor_floor(mode=mode) for mode in MODES}

    def test_the_builder_declares_every_anchor_in_every_mode(self):
        """Anti-vacuity: a renamed anchor must red HERE, not vanish from the sweep."""
        floors = self._floors()
        for mode in MODES:
            missing = [k for k in _ANCHOR_KEYS if k not in floors[mode]]
            assert not missing, f"{mode}: workset_anchor_floor no longer builds {missing}"

    @pytest.mark.parametrize("key", _ANCHOR_KEYS)
    def test_the_manifest_default_is_the_floor_formula(self, key):
        floors = self._floors()
        want = _per_mode(_default(key))
        for mode in MODES:
            assert floors[mode][key] == want[mode], (
                f"{key} [{mode}]: manifest says {want[mode]!r}, "
                f"workset_anchor_floor says {floors[mode][key]!r}"
            )


class TestAnchorScalarDefaults:
    """(i-b2/i-b3) The three NON-LAYOUT ``workset.*`` scalars the anchor floor emits.

    Split from :class:`TestAnchorDefaults` because two of the three are PRIMARY/NAMED
    only, and their standalone arms are pinned as ABSENCES rather than values — a
    ``<None>`` arm and a PROSE arm are both "no floor may answer this", which the
    all-three-modes loop up there cannot express.
    """

    @staticmethod
    def _floors() -> dict[str, dict[str, object]]:
        return {mode: workset_anchor_floor(mode=mode) for mode in MODES}

    @pytest.mark.parametrize("key", _ANCHOR_SCALAR_KEYS)
    def test_the_uniform_scalar_is_the_floor_value_in_every_mode(self, key):
        floors = self._floors()
        want = _default(key)
        assert not isinstance(want, dict), f"{key} is no longer uniform — move it"
        for mode in MODES:
            assert key in floors[mode], f"{mode}: workset_anchor_floor no longer builds {key}"
            assert floors[mode][key] == want, (
                f"{key} [{mode}]: manifest says {want!r}, "
                f"workset_anchor_floor says {floors[mode][key]!r}"
            )

    @pytest.mark.parametrize("key", _ANCHOR_SCALAR_KEYS_PRIMARY_NAMED)
    def test_the_manifest_default_is_the_floor_value_for_primary_and_named(self, key):
        floors = self._floors()
        want = _per_mode(_default(key))
        for mode in ("primary", "named"):
            assert key in floors[mode], f"{mode}: workset_anchor_floor no longer builds {key}"
            assert floors[mode][key] == want[mode], (
                f"{key} [{mode}]: manifest says {want[mode]!r}, "
                f"workset_anchor_floor says {floors[mode][key]!r}"
            )

    @pytest.mark.parametrize("key", _ANCHOR_SCALAR_KEYS_PRIMARY_NAMED)
    def test_the_standalone_arm_is_what_the_floor_supplies(self, key):
        """⚑ TWO KINDS OF STANDALONE ARM, and the floor answers each as the manifest says.

        A ``<None>`` arm is a VALUE, and the floor SUPPLIES it as a present ``None``
        ([R177]).  Omitting it is not the same thing: the seeded-layer skip fires only on a
        present ``None``, so an absent ``workset.template`` rendered ``""`` inside
        ``@workset.template/box/home`` and seeded from the HOST path ``/box/home``.
        A ``<…>`` PROSE arm (``workset.kuid``'s ``<generated at creation>``) is not a
        value, and the floor emits NO KEY: a literal would shadow nothing on a finished
        standalone box and FABRICATE an identity on a half-created one.
        """
        arm = _per_mode(_default(key))["standalone"]
        floor = self._floors()["standalone"]
        if arm is None:
            assert key in floor, (
                f"{key}: the manifest declares standalone <None>, and the floor OMITS it — "
                f"a supplied <None> is a value, not an absence"
            )
            assert floor[key] is None, f"{key}: floor supplies {floor[key]!r}, not None"
            return
        assert str(arm).startswith("<") and str(arm).endswith(">"), (
            f"{key}: the standalone arm is {arm!r}, which is a VALUE — if the manifest "
            f"now declares one, the floor must emit it"
        )
        assert key not in floor, (
            f"{key}: workset_anchor_floor emits a standalone value for a row the "
            f"manifest declares as the prose {arm!r}"
        )

    def test_the_kuid_floor_value_is_the_codec_sentinel(self):
        """Not a re-typed ``"00000"``: the unmintable parity lives with the codec."""
        assert workset_anchor_floor(mode="primary")["workset.kuid"] is kuid.SENTINEL

    def test_the_skip_kuid_check_floor_equals_the_pre_snapshot_reader(self):
        """⚑ TWO CARRIERS OF ONE BOOL, asserted equal — the floor and the file reader.

        ``config.read_workset_skip_kuid_check`` is still the PRE-SNAPSHOT route (it reads
        a ``workset.yaml`` directly, before any snapshot exists).  The floor now answers
        the same question through the keyspace, so the two must agree; the accessor is
        pointed at a path that does not exist, where what it returns IS the default.
        """
        assert not NO_SETTINGS_FILE.exists()
        assert (
            workset_anchor_floor(mode="primary")["workset.skip_kuid_check"]
            == read_workset_skip_kuid_check(NO_SETTINGS_FILE)
        )


class TestWorksetWorkspacesDefault:
    """(i-b4) ``workset.workspaces`` — the resolved dir, and the PRIMARY absence.

    ⚑⚑ A VALUE ORACLE, NOT A SECOND RESOLVER, and NOT a string compare against the
    manifest formula.  The floor carries this key as a RESOLVED LITERAL (the docstring on
    ``workset_anchor_floor`` says why: it is read on the DETECTION side before a snapshot
    exists, harder than ``channelroot`` is), so the manifest formula is followed ONE HOP —
    its ``@meta.workset.path`` answered by the root the caller already holds — and
    compared to what ``project.workset.resolve_workset_workspaces`` returns.  Nothing here
    re-implements the resolution rule; what is under test is WHICH LEAF the manifest
    claims per mode, which is exactly what a hand-copied arm gets wrong (``workspaces``
    plural for named, ``workspace`` singular for standalone — one character apart).

    ⚑ ANTI-VACUITY: the row answered at NO terminus until 2026-08-29 while
    ``meta.box.workspace`` ``@``-referenced it, so this class reds by ``KeyError`` if the
    floor stops emitting the key rather than passing on an absent one.
    """

    #: The two modes the manifest gives a real arm.
    _REAL_ARM_MODES = ("named", "standalone")

    @staticmethod
    def _derived(root: Path, mode: str) -> Path:
        """The deriver's answer for *root* — the value the launch hands the floor."""
        from kanibako.project.workset import (
            load_workset_settings_doc, resolve_workset_workspaces,
        )

        return resolve_workset_workspaces(
            root, load_workset_settings_doc(root), standalone=(mode == "standalone"),
        )

    @pytest.mark.parametrize("mode", _REAL_ARM_MODES)
    def test_the_manifest_arm_is_the_derived_dir(self, mode, tmp_path):
        arm = _per_mode(_default(_WORKSPACES_KEY))[mode]
        head, sep, leaf = str(arm).partition("/")
        assert sep and head == "@meta.workset.path", (
            f"{_WORKSPACES_KEY} [{mode}]: unfollowable manifest formula {arm!r}"
        )
        assert self._derived(tmp_path, mode) == tmp_path / leaf, (
            f"{_WORKSPACES_KEY} [{mode}]: manifest says {arm!r} (= {tmp_path / leaf}), "
            f"resolve_workset_workspaces derived {self._derived(tmp_path, mode)}"
        )

    @pytest.mark.parametrize("mode", _REAL_ARM_MODES)
    def test_the_floor_publishes_the_derived_dir(self, mode, tmp_path):
        """The launch seam's half: what the deriver answers is what the keyspace gets."""
        derived = self._derived(tmp_path, mode)
        floor = workset_anchor_floor(mode=mode, workspaces=str(derived))
        assert floor[_WORKSPACES_KEY] == str(derived)

    def test_the_primary_arm_is_an_absence_on_both_sides(self):
        """🛑 THE ARM IS "NOTHING", and both carriers must say so.

        The manifest declares ``primary: null``.  The CODE does honor a primary
        ``workspaces`` repoint (``project.workset.default_workset``) and that divergence
        is ruled and the user's — but publishing the resolved value as this KEY would
        conform the declared null to a code value, which is the wrong direction.  The
        floor emits nothing at primary, and REFUSES a value rather than dropping one, so
        a caller cannot re-open the arm quietly.
        """
        assert _per_mode(_default(_WORKSPACES_KEY))["primary"] is None
        assert _WORKSPACES_KEY not in workset_anchor_floor(mode="primary")
        with pytest.raises(SettingsError, match="NO primary arm"):
            workset_anchor_floor(mode="primary", workspaces="/anywhere/workspaces")

    def test_a_repoint_reaches_the_derived_dir(self, tmp_path):
        """⚑ Why the value is RESOLVED and never composed: a repoint must survive it.

        A ``<root>/workspaces`` join would pass every case above and silently drop this
        one, which is the whole distinction between the deriver and a second carrier.
        """
        (tmp_path / "workset.yaml").write_text(
            "workset:\n  workspaces: '@meta.workset.path/pods'\n"
        )
        assert self._derived(tmp_path, "named") == tmp_path / "pods"


class TestAuthChainDefaults:
    """(i-c) The auth chain's six defaults ARE ``auth_chain_floor``'s per-mode values."""

    @staticmethod
    def _floors() -> dict[str, dict[str, object]]:
        return {
            mode: auth_chain_floor(mode=mode, agent_name=PROBE_AGENT) for mode in MODES
        }

    def test_the_builder_declares_every_auth_key_in_every_mode(self):
        floors = self._floors()
        for mode in MODES:
            missing = [k for k in _AUTH_KEYS if k not in floors[mode]]
            assert not missing, f"{mode}: auth_chain_floor no longer builds {missing}"

    @pytest.mark.parametrize("key", _AUTH_KEYS)
    def test_the_manifest_default_is_the_floor_value(self, key):
        floors = self._floors()
        want = _per_mode(_default(key))
        for mode in MODES:
            assert floors[mode][key] == want[mode], (
                f"{key} [{mode}]: manifest says {want[mode]!r}, "
                f"auth_chain_floor says {floors[mode][key]!r}"
            )


class TestScalarDefaults:
    """(i-d) Four scalars whose carrier is a dataclass field or a read-with-default."""

    @staticmethod
    def _oracle() -> dict[str, object]:
        cfg = KanibakoConfig()
        return {
            "box.image": cfg.box_image,
            "box.share_images": cfg.box_share_images,
            # ⚑⚑ ``box.enable_vault`` MOVED HERE 2026-08-29, from the accessor block below.
            # It used to be pinned as ``read_box_enable_vault(NO_SETTINGS_FILE)`` — i.e.
            # this file asserted THE READER IS THE CARRIER, which is the state that let the
            # key answer at no launch terminus and let ``read_box_enable_vault``'s two-file
            # open silently drop the BASE and SYSTEM tiers. The carrier is now the field,
            # published into every box-scalar resolve by ``box_scalar_defaults_floor``.
            # 🛑 Moving it back would re-assert the defect as the contract.
            "box.enable_vault": cfg.box_enable_vault,
            # The read-with-default accessor: pointed at a path that does not exist,
            # what it returns IS the declared default and nothing else.
            "workset.skip_kuid_check": read_workset_skip_kuid_check(NO_SETTINGS_FILE),
        }

    def test_the_probe_path_really_is_absent(self):
        """Anti-vacuity for the accessors: a real file there would fake the pin."""
        assert not NO_SETTINGS_FILE.exists()

    @pytest.mark.parametrize("key", _SCALAR_KEYS)
    def test_the_manifest_default_is_the_code_default(self, key):
        got = self._oracle()[key]
        assert _default(key) == got, (
            f"{key}: manifest says {_default(key)!r}, the code default is {got!r}"
        )

    def test_the_enable_vault_floor_equals_the_pre_snapshot_reader(self):
        """⚑ TWO CARRIERS OF ONE BOOL, asserted equal — the floor and the file reader.

        The twin of :meth:`TestAnchorDefaults.
        test_the_skip_kuid_check_floor_equals_the_pre_snapshot_reader`, and written for the
        same reason one commit later.  ``config.read_box_enable_vault`` is still the
        PRE-SNAPSHOT route — it opens a ``box.yaml`` directly, before any snapshot exists,
        to answer WHICH TIER authored a value, which no merge can say.  The floor now
        answers the DEFAULT through the keyspace, so the two must agree; the accessor is
        pointed at a path that does not exist, where what it returns IS the default.

        ⚑ ANTI-VACUITY: the floor treats ``""`` as a suppression (that is what keeps
        ``box.shell`` out), and ``False`` is a VALUE that must survive it — so the key
        being PRESENT is asserted separately from its value being right.
        """
        floor = box_scalar_defaults_floor()
        assert "box.enable_vault" in floor, (
            "box_scalar_defaults_floor no longer publishes box.enable_vault — the key "
            "dangles at every launch terminus again"
        )
        assert not NO_SETTINGS_FILE.exists()
        assert floor["box.enable_vault"] == read_box_enable_vault(NO_SETTINGS_FILE)


class TestBehaviorDefaults:
    """The ``agent.default.*`` floor — pinned THROUGH the manifest's own ``type:``.

    ⚑ THE CARRIER MOVED THIS WEEK, which is the whole reason this class exists.  D1-1
    and D1-2 pulled ``ACCESS_DEFAULT`` and the bootstrap/helper/continue literals out of
    the code and into ``core-defaults.yaml``; the wiring estimate flagged the class as
    the one that would drift WITH NOTHING TO NOTICE.  It now cannot.

    ⚑ THE TWO CARRIERS SPELL BOOLEANS DIFFERENTLY, DELIBERATELY, AND THAT IS NOT DRIFT.
    ``core-defaults.yaml`` quotes them (``"true"``) because the consumers run the floor
    through ``coerce_bool`` and ``effective_behavior`` stringifies — an unquoted YAML
    bool would reach a consumer as ``"True"`` (``test_defaults_golden`` pins the
    quoting).  The manifest writes the VALUE (``true``) and declares ``type: bool``.  So
    the comparison goes through the declared type, and neither carrier has to change.
    """

    def test_the_manifest_types_the_behavior_floor(self):
        """Anti-vacuity: the typed comparison below is only honest if types are declared."""
        types = {k: _keys()[k].get("type") for k in _BEHAVIOR_KEYS}
        assert types == {
            "agent.default.access": "enum",
            "agent.default.allow_helpers": "bool",
            "agent.default.continue_mode": "bool",
            "agent.default.bootstrap": "str",
            "agent.default.label": "str",
        }, types

    @pytest.mark.parametrize("key", _BEHAVIOR_KEYS)
    def test_the_manifest_default_is_the_shipped_floor_value(self, key):
        leaf = key.rsplit(".", 1)[1]
        declared = _default(key)
        shipped = core_defaults.behavior_default(leaf)
        if _keys()[key].get("type") == "bool":
            assert coerce_bool(shipped) is declared, (
                f"{key}: manifest declares {declared!r} (type bool); core-defaults.yaml "
                f"ships {shipped!r}, which coerces to {coerce_bool(shipped)!r}"
            )
        else:
            assert shipped == declared, (
                f"{key}: manifest says {declared!r}, core-defaults.yaml ships {shipped!r}"
            )

    def test_the_access_tier_choices_are_the_code_tier_set(self):
        """``access`` is the one enum leaf — its ``choices:`` is a second pinnable column."""
        assert tuple(_keys()["agent.default.access"]["choices"]) == ACCESS_TIERS

    def test_the_access_accessor_agrees_with_the_manifest(self):
        """The one leaf with two readers (``access_default``) is checked through both."""
        assert access_default() == _default("agent.default.access")


class TestShellTierDefaults:
    """The ``agent.shell.*`` tier floor — pinned THROUGH the manifest's own ``type:``.

    The shell tier's OWN values (spec §2d fence, D2), installed unconditionally by
    ``core_defaults.shell_tier_defaults`` off the ``agent_shell:`` table — the
    shell-tier twin of :class:`TestBehaviorDefaults`.  Same bool convention: the
    file quotes, the manifest writes the value, the comparison goes through the
    declared type.  (The shell `canon` arm is dynamic and pinned with its
    producer in :class:`TestSingletonDefaults`, beside the default arm.)
    """

    def test_the_manifest_types_the_shell_floor(self):
        """Anti-vacuity: the typed comparison below is only honest if types are declared."""
        types = {k: _keys()[k].get("type") for k in _SHELL_TIER_KEYS}
        assert types == {
            "agent.shell.label": "str",
            "agent.shell.access": "enum",
            "agent.shell.allow_helpers": "bool",
            "agent.shell.bootstrap": "str",
            "agent.shell.run_args": "list",
            "agent.shell.transform": "str",
        }, types

    @pytest.mark.parametrize("key", _SHELL_TIER_KEYS)
    def test_the_manifest_default_is_the_shipped_shell_value(self, key):
        shipped = core_defaults.shell_tier_defaults()[key]
        declared = _default(key)
        if _keys()[key].get("type") == "bool":
            assert coerce_bool(shipped) is declared, (
                f"{key}: manifest declares {declared!r} (type bool); "
                f"core-defaults.yaml ships {shipped!r}, which coerces to "
                f"{coerce_bool(shipped)!r}"
            )
        else:
            assert shipped == declared, (
                f"{key}: manifest says {declared!r}, core-defaults.yaml ships "
                f"{shipped!r}"
            )

    def test_the_shell_access_choices_are_the_code_tier_set(self):
        """``access`` is the one enum leaf — its ``choices:`` is pinnable too."""
        assert tuple(_keys()["agent.shell.access"]["choices"]) == ACCESS_TIERS


class TestSingletonDefaults:
    """(i-e) and the kuid sentinel — rows with exactly one named code carrier."""

    def test_the_agent_default_canon_root(self):
        """``core_defaults.canon_default_categories`` emits this literal (spec §2d).

        Read from the emitter's OUTPUT rather than its source text: the value is what a
        launch actually installs at the agent tier, which is the thing the manifest
        claims.  ``std`` is unused for this key (only the per-NODE arm stats a dir), so
        a bare namespace stand-in is enough to reach it.
        """
        emitted = core_defaults.canon_default_categories(
            _StubStandardPaths(), PROBE_AGENT,
        )
        assert emitted["agent.default.canon"] == _default("agent.default.canon")

    def test_the_agent_shell_canon_arm(self):
        """The shell arm of the same producer — the FENCE LITERAL, unconditional (D2).

        Read from the emitter's OUTPUT like the default arm above: the value a
        launch installs at the shell tier.  The stub store carries no shell canon
        dir, so this is the fallback arm — which is the fence literal itself, not
        the one-hop indirection the per-node arm uses.
        """
        emitted = core_defaults.canon_default_categories(
            _StubStandardPaths(), PROBE_AGENT,
        )
        assert emitted["agent.shell.canon"] == _default("agent.shell.canon")

    def test_the_agent_default_template_root(self):
        """``launch.templates.agent_template_defaults`` emits this literal (spec §2d).

        Read from the emitter's OUTPUT, exactly as the canon sibling above is: the
        value is what a LAUNCH installs at the agent tier, which is what the manifest
        claims.  ⚑ The PER-NODE arm is deliberately not read here — it is spelled one
        ``@``-hop from the registry and is pinned separately, in
        ``TestNoOracleExemptions``.

        ⚑⚑ THE SECOND ASSERTION IS THE NO-SECOND-SPELLING ONE.  The create-time seed
        table COMPOSES this producer rather than restating it, so the two tables cannot
        disagree about the arm; drop the composition and put a literal back into
        ``template_seed_defaults`` and this reds.
        """
        emitted = agent_template_defaults(PROBE_AGENT)
        assert emitted["agent.default.template"] == _default("agent.default.template")
        seeds = template_seed_defaults(_StubProjectPaths(), PROBE_AGENT)
        assert seeds["agent.default.template"] == emitted["agent.default.template"]

    def test_the_agent_shell_template_arm_is_a_present_none(self):
        """The §2d shell fence's ``agent.shell.template | <None>``, read off the emitter.

        ⚑ PRESENT, not merely ``None``-valued on a ``.get``: a ``<None>`` is a SUPPLIED
        value ([R177]) that beats the ``agent.default.template`` fallback, and an
        ABSENT key would let the fallback through.  So the case asserts membership
        before value — dropping the shell arm reds on the first, flooring a store path
        again reds on the second.
        """
        assert _default("agent.shell.template") is None
        emitted = agent_template_defaults("shell")
        assert "agent.shell.template" in emitted
        assert emitted["agent.shell.template"] is None

    def test_the_core_env_floor(self):
        """The registry's env rows ARE ``core-defaults.yaml``'s whole ``env:`` table.

        The emitter builds ``<scope>.env.<VAR>`` keys off the shipped file, so reading
        its OUTPUT pins both halves of each manifest row at once — the key SPELLING and
        the value.  Asserting the table is EXACTLY the registry's env rows is the
        anti-vacuity half: a shipped env default that no row names would otherwise slip
        in unregistered, and the registry's job is to name every one.

        ⚑ THE EXPECTATION IS DERIVED FROM THE REGISTRY, not restated: the set of keys
        comes from the manifest and each value from that key's own ``default:``, so a
        third shipped variable arrives here by declaring a row and by nothing else.
        ⚑ ``agent.default.env.TERM``'s default is the EXPRESSION ``$TERM``, not a
        resolved terminal type.  That is the stored value at every carrier — the file,
        the registry and ``system defaults`` all say ``$TERM``; resolution happens per
        launch, and a carrier holding a resolved string would be pinning THIS host.
        """
        shipped = core_defaults.env_default_categories()
        registry_env = [
            str(k) for k, v in _keys().items()
            if isinstance(v, dict) and "default" in v and ".env." in str(k)
        ]
        assert sorted(shipped) == sorted(registry_env), (
            f"core-defaults.yaml ships {sorted(shipped)} and the registry declares "
            f"{sorted(registry_env)} — every shipped env default needs its own row"
        )
        assert shipped == {k: _default(k) for k in registry_env}

    def test_the_spawn_budget_floor(self):
        """``system.helpers.{depth,breadth}`` ARE the shipped system scalar floor.

        ⚑ TWO CARRIERS, BOTH ASSERTED, because the key has two answering paths and a
        pin on either alone is satisfiable while the other drifts.  The LAUNCH floor is
        what makes ``@system.helpers.depth`` resolve for a box; the in-box spawn
        FALLBACK is what ``kanibako box helper spawn`` uses before any snapshot exists.
        Both are read through their producers, never off ``SPAWN_BUDGET_DEFAULTS`` —
        that table is the shared source, so comparing it to itself would pin nothing.
        """
        from kanibako.channels.helpers import resolve_spawn_budget
        from kanibako.settings.settings_launch import SYSTEM_SCALAR_FLOOR

        assert SYSTEM_SCALAR_FLOOR["system.helpers.depth"] == (
            _default("system.helpers.depth")
        )
        assert SYSTEM_SCALAR_FLOOR["system.helpers.breadth"] == (
            _default("system.helpers.breadth")
        )
        fallback = resolve_spawn_budget(None, None, None, None)
        assert fallback.depth == _default("system.helpers.depth")
        assert fallback.breadth == _default("system.helpers.breadth")

    def test_the_workset_kuid_sentinel(self):
        """The primary/named arms ARE ``kuid.SENTINEL``.

        ⚑ THE STANDALONE ARM IS EXEMPT and it is exempt for a stated reason: the
        manifest writes ``<generated at creation>``, which is PROSE describing a value
        produced at runtime, not a value.  Pinning it would mean asserting a placeholder
        string, which pins nothing.  The two arms that ARE values are pinned.
        """
        arms = _per_mode(_default("workset.kuid"))
        assert arms["primary"] == kuid.SENTINEL
        assert arms["named"] == kuid.SENTINEL
        assert arms["standalone"] == "<generated at creation>"


#: A probe root that does not exist.  ⚑ THAT IS THE POINT: a manifest ``default:`` is
#: by definition the value you get when no ``workset.yaml`` repoints the key, and the
#: repoint reader is absent-tolerant by design, so an absent root selects the default
#: arm of every channel key at once.  The REPOINT half is a different question and is
#: measured against real worksets in ``tests/test_channels/test_channel_keys.py``.
_PROBE_ROOT = Path("/nonexistent/kanibako-conformance-probe")


class _StubChannelPaths:
    """The three ``StandardPaths`` attributes the channel derivations read.

    Deliberately NOT a ``StandardPaths``: constructing one probes the host XDG
    environment, and this case is about a formula, not about the host.
    """

    primary_workset = _PROBE_ROOT / "primary"
    channels_mailboxes = _PROBE_ROOT / "channels" / "mailboxes"
    channels_share = _PROBE_ROOT / "channels" / "share"


class _StubGroup:
    """The two ``ProjectGroup`` attributes ``workset_root`` / ``workset_name_token`` read."""

    name = "conformance-set"
    root = _PROBE_ROOT / "worksets" / "conformance-set"


class _StubChannelProject:
    """The ``ProjectPaths`` attributes the channel derivations read — mode, group, root."""

    def __init__(self, mode: BoxMode):
        self.mode = mode
        self.group = _StubGroup() if mode is BoxMode.named else None
        self.metadata_path = _PROBE_ROOT / "standalone-box"


class TestWorksetChannelDefaults:
    """(i-f) The channel family's defaults ARE what ``channels/channels.py`` derives.

    ⚑⚑ A VALUE ORACLE, NOT A SECOND RESOLVER — the distinction the retired E1 exemption
    got wrong.  Each manifest formula is followed ONE HOP: its ``@``-refs are answered
    by EXERCISING the code that owns them (``workset_root``, ``workset_name_token``,
    the resolved channel root itself), and the result is compared to the ``Path`` the
    derivation returns.  Nothing here re-implements the resolution rule; what is under
    test is *which parent and which leaf* the manifest claims — which is exactly what a
    hand-copied formula gets wrong.

    ⚑ ANTI-VACUITY: three of the seven rows had NO code answering them at all when this
    class was written (``broadcast`` / ``mailboxes`` / ``share_global``), and the class
    reds by ``KeyError`` rather than passing when a key loses its derivation.
    """

    @staticmethod
    def _derived(proj, std) -> dict[str, object]:
        """The key → derived value map, read straight off the derivations."""
        from kanibako.channels import channels as ch

        wch = ch.workset_channel_paths(proj, std)
        part = ch.workset_partition_paths(proj, std)
        return {
            "workset.channelroot": None if wch is None else wch.root,
            "workset.channels.common": None if wch is None else wch.common,
            "workset.channels.chat": None if wch is None else wch.chat,
            "workset.channels.broadcast": None if wch is None else wch.chat_broadcast,
            "workset.channels.share": None if wch is None else wch.share,
            "workset.channels.mailboxes": part.mailboxes,
            "workset.channels.share_global": part.share_global,
        }

    @staticmethod
    def _refs(proj, std, derived) -> dict[str, object]:
        """The ``@``-refs the seven formulas hang off, each answered BY THE CODE."""
        from kanibako.channels import channels as ch

        refs: dict[str, object] = {
            "@meta.workset.path": ch.workset_root(proj, std),
            "@meta.workset.name": ch.workset_name_token(proj),
            "@system.channels.mailboxes": std.channels_mailboxes,
            "@system.channels.share": std.channels_share,
        }
        # The two INTRA-family refs: the family roots itself, which is the shape the
        # manifest declares and the shape a flat re-spelling would silently lose.
        refs["@workset.channelroot"] = derived["workset.channelroot"]
        refs["@workset.channels.chat"] = derived["workset.channels.chat"]
        return refs

    @staticmethod
    def _follow(formula: str, refs: dict[str, object]) -> Path:
        """Follow ``@<ref>/<tail>`` one hop; *tail* may itself be a single ``@``-ref."""
        head, sep, tail = formula.partition("/")
        assert sep and head in refs, f"unfollowable manifest formula {formula!r}"
        return Path(str(refs[head])) / str(refs.get(tail, tail))

    @pytest.mark.parametrize("mode", sorted(MODES))
    def test_the_manifest_default_is_the_derived_path(self, mode):
        proj = _StubChannelProject(BoxMode(mode))
        std = _StubChannelPaths()
        derived = self._derived(proj, std)
        refs = self._refs(proj, std, derived)
        for key in _CHANNEL_KEYS:
            arm = _per_mode(_default(key))[mode]
            got = derived[key]
            if arm is None:
                assert got is None, (
                    f"{key} [{mode}]: manifest declares NO value, the code derived "
                    f"{got!r}"
                )
                continue
            assert got == self._follow(str(arm), refs), (
                f"{key} [{mode}]: manifest says {arm!r} (= "
                f"{self._follow(str(arm), refs)}), the code derived {got!r}"
            )

    def test_the_family_pinned_here_is_the_declared_family(self):
        """Anti-vacuity: a SEVENTH leaf added to the spec must red here, not slip past."""
        leaves = {k.split(".")[-1] for k in _CHANNEL_KEYS if ".channels." in k}
        assert leaves == set(DECLARED_WORKSET_CHANNEL_LEAVES)


class _StubStandardPaths:
    """The one attribute ``canon_default_categories`` reads — a directory that is absent.

    Deliberately NOT a ``StandardPaths``: constructing one probes the host XDG
    environment, and this case is about a literal in an emitter, not about the host.
    An absent ``agents/<node>/canon`` selects the ``@agent.default.canon`` fallback arm,
    which leaves the ``agent.default.canon`` row this case reads untouched either way.
    """

    agents = Path("/nonexistent/kanibako-conformance-probe/agents")


class _StubProjectPaths:
    """The one attribute ``template_seed_defaults`` reads — the box MODE.

    Deliberately NOT a ``ProjectPaths``: building one wants a workset on disk, and
    this case is about a literal in an emitter.  ``standalone`` is the mode that
    reaches the agent arm with the fewest inputs — it has no workset tier (spec
    ``:936``), so the emitter's only other branch is simply off, and the agent-tier
    row under test does not vary by mode.
    """

    mode = BoxMode.standalone


# --------------------------------------------------------------------------- #
# 3. DEFAULTS conformance — the BIND entries
# --------------------------------------------------------------------------- #

#: The one ``bind_default_entries`` row with no carrier to compare to.
#: NAMED, with the reason, because a silent skip here would hide a real regression.
BIND_EXEMPTIONS: dict[str, str] = {
    "<box_image_dir>": (
        "a PLACEHOLDER dest, not a dest: the manifest writes the whole row as the "
        "conditional `%if @box.share_images: (@box.images_store) else None%`, while the "
        "code row is the unconditional `images` entry at /var/lib/shared-images whose "
        "GATE lives at the injection site (core_defaults.image_default_categories). "
        "Two different shapes; comparing them would compare a formula to a value"
    ),
}


#: The helper socket's host source is COMPUTED by the name rule (companion § "Box and
#: helper identity"), so its row is checked against that rule instead of a ``meta_ref``.
HELPER_SOCKET_DEST = "~/.kanibako/state/helper.sock"
_HELPER_SOCKET_CELL = re.compile(r'^\(@system\.runtime/<bounded name of "(?P<t>[^"]+)">, ""\)$')


def _spec_socket_name(identity: str, run_dir: Path) -> str:
    """The companion's name rule, written out independently of the code under test."""
    import hashlib

    if len(str(run_dir / f"{identity}.sock").encode("utf-8")) < 104:
        return f"{identity}.sock"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16] + ".sock"


def _check_helper_socket_row(raw: object) -> None:
    """The manifest spells the bounded name, and the code computes that name."""
    from kanibako.commands.start import helper_socket_path

    cell = _HELPER_SOCKET_CELL.match(str(raw))
    assert cell, f"{HELPER_SOCKET_DEST}: manifest {raw!r} does not spell the bounded name"
    run_dir = _PROBE_ROOT / "run"
    # meta.workset.name per mode, spelled as the keyspec's channel rows spell it.
    ws_names = {"primary": "__PRIMARY__", "named": _StubGroup.name,
                "standalone": "__STANDALONE__"}
    for mode, ws_name in ws_names.items():
        for box_name in ("app", "x" * 120, "箱" * 30):
            proj = _StubChannelProject(BoxMode(mode))
            proj.name = box_name
            identity = (cell["t"].replace("@{meta.box.name}", box_name)
                        .replace("@{meta.workset.name}", ws_name))
            assert "@" not in identity, f"unrendered ref in {cell['t']!r}"
            want = run_dir / _spec_socket_name(identity, run_dir)
            assert helper_socket_path(proj, run_dir) == want, (mode, box_name)


def _core_defaults_doc() -> dict:
    """The shipped ``core-defaults.yaml``, read as packaged data (as the loader does)."""
    ref = importlib.resources.files("kanibako.data").joinpath("core-defaults.yaml")
    return yaml.safe_load(Path(str(ref)).read_text()) or {}


def _code_bind_refs() -> dict[str, object]:
    """``{box_dest: meta_ref}`` over every declarative bind family in core-defaults.yaml.

    A per-mode row contributes its ``mode_meta_ref`` MAP; a uniform row its ``meta_ref``
    string.  Rows with neither (``kani``, ``kickoff``, the probed helper socket and
    ``images_conf``) have no reference to carry and are simply absent — the manifest
    marks those ``user_key: false`` with a ``value:`` rather than a ``default:``.
    """
    doc = _core_defaults_doc()
    out: dict[str, object] = {}
    for family in ("channels", "core", "kani", "kickoff", "canon", "helpers", "images"):
        for entry in doc.get(family) or []:
            dest = str(entry["box_dest"])
            if "mode_meta_ref" in entry:
                out[dest] = {m: str(v) for m, v in entry["mode_meta_ref"].items()}
            elif "meta_ref" in entry:
                out[dest] = str(entry["meta_ref"])
    return out


def _manifest_bind_defaults() -> dict[str, tuple[str, object]]:
    """``{box_dest: (arm, default)}`` over ``bind_default_entries`` rows with a default."""
    out: dict[str, tuple[str, object]] = {}
    for arm, entries in manifest_doc()["bind_default_entries"].items():
        for dest, entry in entries.items():
            if "default" in entry:
                out[dest] = (arm, entry["default"])
    return out


class TestBindDefaults:
    """The bind rows' host-source references ARE ``core-defaults.yaml``'s ``meta_ref``s."""

    def test_the_two_corpora_are_the_measured_size(self):
        """Anti-vacuity, and the arithmetic the wiring estimate got one off.

        30 bind DESTS (29 before this phase added the ``images_conf`` row), 19 of them
        carrying a ``default:``; 18 code rows carry a ``meta_ref``/``mode_meta_ref``.  17
        pair up, and the helper socket is checked against its name rule.  The estimate's
        "18/20" double-counted the images row: its code carrier
        (``/var/lib/shared-images``) is the code side of the very ``<box_image_dir>`` row
        the estimate itself exempted.
        """
        entries = manifest_doc()["bind_default_entries"]
        assert sum(len(a) for a in entries.values()) == 30
        assert len(_manifest_bind_defaults()) == 19
        assert len(_code_bind_refs()) == 18
        assert len(set(_manifest_bind_defaults()) - set(BIND_EXEMPTIONS)) == 18

    def test_every_exemption_names_a_row_that_exists(self):
        """A stale exemption is worse than none — it silently un-pins a live row."""
        rows = _manifest_bind_defaults()
        for dest in BIND_EXEMPTIONS:
            assert dest in rows, (
                f"exempted bind dest {dest!r} is no longer a manifest row with a "
                f"default — delete the exemption or fix the dest"
            )

    @pytest.mark.parametrize(
        "dest", sorted(set(_manifest_bind_defaults()) - set(BIND_EXEMPTIONS))
    )
    def test_the_manifest_bind_default_is_the_code_meta_ref(self, dest):
        arm, raw = _manifest_bind_defaults()[dest]
        if dest == HELPER_SOCKET_DEST:
            _check_helper_socket_row(raw)
            return
        code = _code_bind_refs()
        assert dest in code, (
            f"{arm} {dest}: the manifest declares a default but core-defaults.yaml has "
            f"no meta_ref row for this dest — either the bind moved or this row is stale"
        )
        want, got = raw, code[dest]
        if isinstance(want, dict):
            assert isinstance(got, dict), f"{dest}: manifest is per-mode, code is not"
            assert {m: _unwrap(v) for m, v in want.items()} == got, (
                f"{arm} {dest}: manifest {want!r} vs core-defaults.yaml {got!r}"
            )
        else:
            assert _unwrap(want) == got, (
                f"{arm} {dest}: manifest {want!r} vs core-defaults.yaml {got!r}"
            )

    def test_the_internal_generated_binds_carry_no_default(self):
        """``user_key: false`` rows declare a ``value:``, never a ``default:``.

        The distinction is the spec's own: an INTERNAL bind (generated content, fixed
        location) has no user-addressable key and therefore no default to override —
        ``images_conf`` is the ruled example (``not_keys.never_a_key``).
        """
        for arm, entries in manifest_doc()["bind_default_entries"].items():
            for dest, entry in entries.items():
                if entry.get("user_key") is False:
                    assert "default" not in entry, f"{arm} {dest} is internal, not defaulted"
                    assert "value" in entry, f"{arm} {dest} declares neither value nor default"

    def test_the_generated_storage_conf_is_declared_internal(self):
        """``images_conf`` — the row added by this phase (registry finding 3).

        Every other internal bind had a ``user_key: false`` row; this one was missing
        while ``not_keys.never_a_key`` already named it.  The dest is read from
        ``core-defaults.yaml`` so the row cannot drift from the bind it describes.
        """
        code_dests = {
            str(e["box_dest"]) for e in (_core_defaults_doc().get("images") or [])
            if str(e["key"]) == "images_conf"
        }
        assert len(code_dests) == 1, f"expected exactly one images_conf bind, got {code_dests}"
        dest = code_dests.pop()
        row = manifest_doc()["bind_default_entries"]["box.bindings.ro"][dest]
        assert row["user_key"] is False
        assert "images_conf" in manifest_doc()["not_keys"]["never_a_key"]


# --------------------------------------------------------------------------- #
# 4. The classes with NO code oracle — the named, reasoned exemption table
# --------------------------------------------------------------------------- #
#
# ⚑ THE VERDICT IS "DECLINE", NOT "TODO".  Each class below was measured, and in each
# case the only available oracle would be a SECOND implementation of the thing under
# test — which pins nothing and rots twice as fast (P2/P4).  Writing them down HERE,
# with the reason, is the deliverable; the exhaustiveness case makes the table binding.

#: ⚑⚑⚑ (E1) ``NO_ORACLE_PATH_JOIN`` IS GONE (2026-08-29) — THE WHOLE CLASS, not a member.
#: Its reason was: realized as a ``Path`` join, never as a formula STRING, so there is no
#: ``"@meta.workset.path/…"`` literal anywhere to compare the manifest to, and an oracle
#: would be a second resolver.  Ten rows were filed under it and every one of them left,
#: each because the reason was false OR because the conclusion did not follow:
#:
#: * THE CHANNEL FAMILY (2026-08-25) — ``workset.channelroot`` + all six
#:   ``workset.channels.*`` leaves.  FALSE for ``broadcast`` / ``mailboxes`` /
#:   ``share_global``: they were realized as no join at all, because no code read them.
#:   True but not conclusive for ``common`` / ``chat`` / ``share``: EXERCISING the
#:   derivation with a known root is a value oracle, and only RE-IMPLEMENTING it would be
#:   a second resolver.  Pinned by :class:`TestWorksetChannelDefaults`.
#: * ``workset.registry`` (2026-08-29) — its join face
#:   (``project/workset_registry.py::resolve_workset_registry_path``) still exists and is
#:   still the pre-snapshot route, but the row was ALSO emitted by no floor at all, so
#:   ``@workset.registry`` dangled in every launch snapshot.  The fix
#:   (``settings_launch.workset_anchor_floor``, the ``channelroot`` precedent) writes the
#:   formula STRING out.  Pinned by :class:`TestAnchorScalarDefaults`.
#: * ``workset.template`` (2026-08-29) — false twice over: ``launch/templates.py`` wrote
#:   the literal out, and the row reached no terminus for a box that already existed.
#:   Pinned by :class:`TestAnchorScalarDefaults`, arm for arm, the standalone ``<None>``
#:   included (supplied as a present ``None`` since 2026-09-24).
#: * ``workset.workspaces`` (2026-08-29) — THE LAST ONE, and the reason died the same way:
#:   the launch now writes the RESOLVED dir out (``workset_anchor_floor``'s ``workspaces``
#:   arm), so there is an artefact to compare to, and the row had dangled at every
#:   terminus while its dependent ``meta.box.workspace`` demanded it.  Pinned by
#:   :class:`TestWorksetWorkspacesDefault` — the named/standalone values against the
#:   manifest formulas, and the PRIMARY ABSENCE on both sides.
#:
#: 🛑 DO NOT RE-CREATE THIS CLASS TO PARK A ROW IN.  A join FACE is not an absence of a
#: carrier; treating it as one is what let four declared rows resolve to ``__MISSING__``
#: at launch while looking ordinary in the exemption table.  The shape check that used to
#: sit under it (``every arm is an @-ref``) went with it: it read no code, so it was never
#: an oracle, and there is now nothing left for it to be the honest floor under.
#:
#: (E2) A PROSE PLACEHOLDER standing in for a runtime-probed value.  The manifest is
#: describing where the value comes from, not declaring one.
NO_ORACLE_PLACEHOLDER: frozenset[str] = frozenset({"box.images_store"})

#: (E3) ``default: <None>`` — an ABSENCE.  No floor builder installs these keys at all,
#: so the property is "no builder emits it", which is a different (and much weaker)
#: claim than a value oracle; a floor that is silent about a key is indistinguishable
#: from a floor that has not been written yet.
#: ⚑ ``agent.shell.template`` LEFT this class 2026-09-24: its ``<None>`` is floored as a
#: PRESENT ``None`` (a supplied value, [R177]), so it has a carrier and is pinned in
#: ``_SINGLETON_KEYS`` against it.  ``agent.shell.{bootstrap,run_args,transform}`` left
#: the same day for ``_SHELL_TIER_KEYS``: the ``agent_shell:`` floor carries them —
#: ``bootstrap`` as the tier's own ``tmux``, the other two as a present ``None``.
NO_ORACLE_ABSENT: frozenset[str] = frozenset({
    "system.agent", "system.setup_completed", "box.shell",
    "agent.default.model", "agent.default.endpoint", "agent.default.run_args",
    "agent.default.transform",
})

#: (E4) ``default: {}`` — the EMPTY CONTAINER a category arm starts at.  That emptiness
#: is the resolver's own initial state, not a declared floor value, and
#: ``test_defaults_golden`` already pins the shape (``masks`` is a dest-keyed map, the
#: bind arms are dest-keyed bindmaps).
NO_ORACLE_EMPTY: frozenset[str] = frozenset({
    "box.bindings.ro", "box.bindings.rw", "box.masks", "agent.default.transform_settings",
    "agent.shell.transform_settings",
})

#: (E5) Two ``@``-ref rows spelled ONE HOP differently from the code, each for its own
#: reason.  See the two cases below — neither is a silent skip.
NO_ORACLE_REF_HOP: frozenset[str] = frozenset({
    "agent.<agent>.canon", "agent.<agent>.template",
})

EXEMPT_DEFAULT_KEYS: frozenset[str] = (
    NO_ORACLE_PLACEHOLDER | NO_ORACLE_ABSENT | NO_ORACLE_EMPTY | NO_ORACLE_REF_HOP
)


class TestNoOracleExemptions:
    """The declined classes, each asserted to be the shape its reason claims."""

    @pytest.mark.parametrize("key", sorted(NO_ORACLE_ABSENT))
    def test_an_absent_default_really_is_null(self, key):
        """The reason is "there is no value"; check there is no value."""
        assert _default(key) is None

    @pytest.mark.parametrize("key", sorted(NO_ORACLE_EMPTY))
    def test_an_empty_default_really_is_an_empty_container(self, key):
        assert _default(key) == {}

    def test_the_placeholder_default_really_is_prose(self):
        assert _default("box.images_store") == "<runtime-probed podman graphroot>"

    def test_the_agent_canon_row_is_one_hop_from_the_code_spelling(self):
        """``agent.<agent>.canon`` — same LOCATION, the ``meta`` hop unrolled.

        The manifest says ``@meta.agent.<agent>.path/canon``; ``canon_default_categories``
        emits ``@config.agents/<agent>/canon``.  ``meta_agent_path_floor`` IS
        ``@config.agents/<name>``, so the two resolve to one place — and that function's
        own docstring records the unrolling as deliberate ("Both resolve identically").
        Asserting equality would require resolving the ref, i.e. a second resolver;
        asserting the SHAPE is what is honestly checkable here.
        """
        assert _default("agent.<agent>.canon") == "@meta.agent.<agent>.path/canon"
        emitted = core_defaults.canon_default_categories(
            _StubStandardPaths(), PROBE_AGENT,
        )
        assert emitted[f"agent.{PROBE_AGENT}.canon"] in (
            f"@config.agents/{PROBE_AGENT}/canon", "@agent.default.canon",
        )

    def test_the_agent_template_row_is_one_hop_from_the_code_spelling(self):
        """``agent.<agent>.template`` — the NODE's own store, the ``meta`` hop unrolled.

        ⚑⚑ THIS CASE REPLACES A BOARDED OPEN QUESTION (finding 1), CLOSED 2026-08-27.
        The manifest and spec §2d say a NODE's own store supplies the seed-layer-2
        template; ``template_seed_defaults`` used to emit the HARNESS's store, via
        ``harness_of``.  Ruling: the CODE was wrong, and the harness's CONTENT reaches a
        persona by SYMLINK instead (``commands.start.ensure_persona_share_symlinks``
        links ``agents/<node>/template`` -> ``agents/<harness>/template``, the way it
        already did for ``common``) — *"the user can always remove the symlink if they
        want to create a separate template for the persona-based agent"*.

        What is left is the SAME one-hop unrolling the ``canon`` sibling above has, and
        for the same reason: ``meta_agent_path_floor`` IS ``@config.agents/<name>``, so
        both spellings name one directory and asserting equality would need a second
        resolver.  ⚑ THE SHAPE IS NOT THE POINT HERE — the DISCRIMINATOR is: the arm
        must be keyed AND rooted at the ACTIVE NODE.  A bare agent cannot show that
        (node == harness ⇒ one string), so the persona probe below is the load-bearing
        half and the bare probe is the no-change control.

        (Mutation: put ``harness_of(...)`` back into either side of the root and the
        persona case goes RED naming the harness store.)
        """
        assert _default("agent.<agent>.template") == "@meta.agent.<agent>.path/template"

        # BARE (node == harness): unchanged by the ruling — the control.
        bare = template_seed_defaults(_StubProjectPaths(), PROBE_AGENT)
        assert bare[f"agent.{PROBE_AGENT}.template"] == (
            f"@config.agents/{PROBE_AGENT}/template"
        )

        # PERSONA: the node's OWN store, NOT ``@config.agents/claude/template``.
        from kanibako.agent_ref import CANONICAL_SEP

        node = f"navigator{CANONICAL_SEP}{PROBE_AGENT}"
        emitted = template_seed_defaults(_StubProjectPaths(), node)
        # ⚑ KEY vs DIRECTORY: the key segment is the canonical node, the value is a
        # store path and carries the ``+`` dirname.  Literal on purpose.
        assert emitted[f"agent.{node}.template"] == (
            f"@config.agents/navigator+{PROBE_AGENT}/template"
        )
        # ...and the layer that consumes it reads the node arm, not a second spelling.
        assert emitted[f"agent.{node}.seeded"] == {
            "~/": (f"@agent.{node}.template/box/home",),
        }


# --------------------------------------------------------------------------- #
# 4b. VALUE-cell conformance — the rows whose comparable cell is ``value:``
# --------------------------------------------------------------------------- #
#
# Census 2026-09-22: 120 ``keys:`` rows = 79 ``default:`` + 31 ``value:`` + 0 both +
# 10 neither.  The 31 ``value:`` rows are the ``meta.*`` comparable cell (the
# ``meta.box.path`` / ``meta.box.home`` hole that took an outside oracle to surface),
# and the 10 neither-rows are a third unpinned class the default coverage never saw.
#
# TWO PIN SHAPES, following the file's existing idioms.  A LITERAL pin asserts the
# manifest string and the floor string equal (per mode where the row is a mode map).
# A ONE-HOP pin is for rows where the floor carries the RESOLVED literal while the
# manifest declares the formula — the ``workset.workspaces`` shape: the manifest arm is
# followed one hop against the derivation that owns the ref, never re-resolved.  Where
# the floor only CARRIES a caller literal (the box-identity addresses), the pin is the
# manifest formula verbatim plus the passthrough, and the caller wiring is named.
#
# ⚑ TWO MEASURED DEVIATIONS from the briefing, both in the pins' favor.  First,
# ``meta.agent.<agent>.path`` is NOT spelled identically on both sides: the manifest
# says ``@config.agents/@meta.agent.<agent>.name`` while the floor unrolls the name hop
# through ``store_dirname`` — so it is pinned as a RELATION (path == ``@config.agents/``
# + the name floor's value), the one-hop shape, not a string equality that would be
# false.  Second, ``meta.agent.shell.mode`` is PINNED, not exempt: ``{}`` here is a
# MATERIALIZED positive declaration (the grammar-floor reader raises on an absence —
# D2/D4), not the resolver-initial-state emptiness E4 declines.  An exemption reason
# for it would have been false, which is worse than a re-bucketing.
#: (ii-a) Value rows floored by ``settings_launch.workset_anchor_floor``.
_VALUE_ANCHOR_KEYS = (
    "meta.box.path", "meta.box.home",
)

#: (ii-b) Value rows floored by ``settings_launch.auth_chain_floor``.
_VALUE_AUTH_KEYS = (
    "meta.box.auth.workset_path", "meta.box.agent.auth.share_support",
)

#: (ii-c) Value rows floored by ``settings_launch.meta_runtime_floor``.
_VALUE_REROOT_KEYS = (
    "meta.workset.path", "meta.workset.name", "meta.workset.settings",
    "meta.box.mode",
)

#: (ii-d) Value rows floored by the agent-identity builders (``meta_agent_path_floor``,
#: ``meta_identity_floor``, ``meta_agent_grammar_floor``) — the parametric pair, the
#: default-tier name literal, and the four shell-fence literals.
_VALUE_AGENT_IDENTITY_KEYS = (
    "meta.agent.<agent>.path", "meta.agent.<agent>.settings",
    "meta.agent.default.name",
    "meta.agent.shell.path", "meta.agent.shell.name",
    "meta.agent.shell.settings", "meta.agent.shell.mode",
)

#: (ii-e) Value rows the identity floor CARRIES as caller literals — the channel
#: partition addresses, the box-tier file anchor, the workspace source, and the box
#: name — pinned against the manifest formulas plus the derivation that owns each ref.
_VALUE_BOX_ADDRESS_KEYS = (
    "meta.box.inbox", "meta.box.share_global", "meta.box.share_workset",
    "meta.box.settings", "meta.box.workspace", "meta.box.name",
)

#: (ii-f) Value rows COMPUTED post-expand by
#: ``settings_launch._materialize_auth_active`` (Q61) — the manifest carries the
#: ``<computed>`` placeholder, NOT a formula: a real ``@``-ref there would resolve
#: through ``expand`` and change launch behavior, so the placeholder is
#: load-bearing, not vagueness. Pinned verbatim, plus the producer proof.
_VALUE_AUTH_ACTIVE_KEYS = (
    "meta.workset.auth.global_active",
    "meta.box.auth.global_active",
    "meta.box.auth.workset_active",
)

#: Every manifest ``keys:`` row this section pins a VALUE for: 24.
PINNED_VALUE_KEYS: frozenset[str] = frozenset(
    set(_VALUE_ANCHOR_KEYS) | set(_VALUE_AUTH_KEYS) | set(_VALUE_REROOT_KEYS)
    | set(_VALUE_AGENT_IDENTITY_KEYS) | set(_VALUE_BOX_ADDRESS_KEYS)
    | set(_VALUE_AUTH_ACTIVE_KEYS)
)


class TestValueAnchorFormulas:
    """(ii-a) ``meta.box.path`` / ``meta.box.home`` ARE the anchor floor's formulas.

    The literal half of the hole the board row was filed on: the manifest records no
    ``default:`` for these rows because the spelling lives in the ``value:`` cell, and
    nothing compared that cell to ``workset_anchor_floor``.
    """

    @staticmethod
    def _floors() -> dict[str, dict[str, object]]:
        return {mode: workset_anchor_floor(mode=mode) for mode in MODES}

    def test_the_builder_carries_both_rows_in_every_mode(self):
        """Anti-vacuity: a floor that stopped emitting a row must red HERE."""
        floors = self._floors()
        for mode in MODES:
            missing = [k for k in _VALUE_ANCHOR_KEYS if k not in floors[mode]]
            assert not missing, f"{mode}: workset_anchor_floor no longer builds {missing}"

    def test_the_manifest_value_is_the_spelled_formula(self):
        """The manifest side, verbatim — a reworded formula reds before any floor runs."""
        assert _per_mode(_value("meta.box.path")) == {
            "primary": "@workset.boxes/@meta.box.name",
            "named": "@workset.boxes/@meta.box.name",
            "standalone": "@workset.boxes",
        }
        assert _value("meta.box.home") == "@meta.box.path/home"

    @pytest.mark.parametrize("key", _VALUE_ANCHOR_KEYS)
    def test_the_manifest_value_is_the_floor_formula(self, key):
        floors = self._floors()
        want = _per_mode(_value(key))
        for mode in MODES:
            assert floors[mode][key] == want[mode], (
                f"{key} [{mode}]: manifest value says {want[mode]!r}, "
                f"workset_anchor_floor says {floors[mode][key]!r}"
            )

    def test_the_home_row_is_the_named_key(self):
        """``meta.box.home`` is spelled once, as ``BOX_HOME_KEY`` — the assembly seam and
        ``box show --effective`` read the same constant, so a re-typed string here is a
        second carrier, not a pin."""
        from kanibako.settings.settings_launch import BOX_HOME_KEY

        assert BOX_HOME_KEY == "meta.box.home"
        assert BOX_HOME_KEY in _VALUE_ANCHOR_KEYS


class TestValueAuthFormulas:
    """(ii-b) The auth value rows ARE ``auth_chain_floor``'s per-mode values.

    ``meta.box.auth.workset_path`` pins like the (i-c) default chain, standalone-``None``
    arm included.  ``meta.box.agent.auth.share_support`` pins as a DOCUMENTED
    interpolation: the manifest uses the ``<@system.agent>`` node-selector notation and
    the floor interpolates the selected node, because the resolver cannot express the
    selector (the floor's own docstring says so) — asserting string equality would pin a
    spelling the code cannot emit.
    """

    @staticmethod
    def _floors() -> dict[str, dict[str, object]]:
        return {
            mode: auth_chain_floor(mode=mode, agent_name=PROBE_AGENT) for mode in MODES
        }

    def test_the_manifest_values_are_the_spelled_formulas(self):
        assert _per_mode(_value("meta.box.auth.workset_path")) == {
            "primary": "@workset.auth.path/@system.agent",
            "named": "@workset.auth.path/@system.agent",
            "standalone": None,
        }
        assert (
            _value("meta.box.agent.auth.share_support")
            == "@meta.agent.<@system.agent>.auth.share_support"
        )

    def test_the_workset_path_value_is_the_floor_value(self):
        floors = self._floors()
        want = _per_mode(_value("meta.box.auth.workset_path"))
        for mode in MODES:
            assert floors[mode]["meta.box.auth.workset_path"] == want[mode], (
                f"meta.box.auth.workset_path [{mode}]: manifest value says "
                f"{want[mode]!r}, auth_chain_floor says "
                f"{floors[mode]['meta.box.auth.workset_path']!r}"
            )

    def test_the_capability_mirror_is_the_interpolated_ref(self):
        """The floor answers the manifest's node-selector with the selected node filled
        in — the one spelling the resolver can carry.

        (Mutation: interpolate a different node and the launch resolves the mirror
        against an agent tier that was never materialized.)
        """
        floors = self._floors()
        for mode in MODES:
            assert floors[mode]["meta.box.agent.auth.share_support"] == (
                f"@meta.agent.{PROBE_AGENT}.auth.share_support"
            ), (
                f"meta.box.agent.auth.share_support [{mode}]: "
                f"{floors[mode]['meta.box.agent.auth.share_support']!r}"
            )


class TestValueAuthActiveFormulas:
    """(ii-f) The computed sharing-state rows ARE ``_materialize_auth_active``'s output.

    The manifest carries the ``<computed>`` placeholder rather than a formula:
    ``&&`` is inexpressible in ``@``-ref grammar, so no formula could spell the
    conjunction — the placeholder is load-bearing. Pinned verbatim, plus the
    producer proof — a floorless launch snapshot carries all three as bools (the
    fail-CLOSED arm; the truth table itself is pinned by
    ``test_settings_launch.TestAuthActiveKeys``).
    """

    @pytest.mark.parametrize("key", _VALUE_AUTH_ACTIVE_KEYS)
    def test_the_manifest_value_is_the_computed_placeholder(self, key):
        assert _value(key) == "<computed>"

    def test_the_producer_materializes_all_three_as_bools(self):
        """Anti-vacuity: a producer that stopped emitting a row must red HERE."""
        from kanibako.settings.settings_launch import build_launch_snapshot
        from kanibako.settings.settings_resolve import ResolveCtx

        snap = build_launch_snapshot(
            agent_name="claude",
            ctx=ResolveCtx(
                agent_name="claude",
                workset_name=None,
                host_home="/home/host",
                xdg={"XDG_DATA_HOME": "/data"},
            ),
            system_path=None,
            agent_path=None,
            workset_path=None,
            box_path=None,
        )
        assert snap.meta.workset.auth.global_active is False
        assert snap.meta.box.auth.global_active is False
        assert snap.meta.box.auth.workset_active is False


class TestValueRerootFormulas:
    """(ii-c) The re-rooted ``meta.*`` values ARE ``meta_runtime_floor``'s formulas.

    Uniform in every mode (the per-mode variation lives one hop down, in
    ``meta.runtime.ws_root`` / ``ws_name`` — both PROSE rows this section exempts, so
    the re-root is what is honestly pinnable here).
    """

    @staticmethod
    def _floors() -> dict[str, dict[str, object]]:
        return {
            "primary": meta_runtime_floor(mode="primary", ws_name="__PRIMARY__"),
            "named": meta_runtime_floor(
                mode="named", ws_name="conformance-set",
                ws_root_literal="/w/conformance-set",
            ),
            "standalone": meta_runtime_floor(
                mode="standalone", ws_name="__STANDALONE__",
                ws_root_literal="/tmp/proj",
            ),
        }

    def test_the_manifest_values_are_the_spelled_formulas(self):
        assert _value("meta.workset.path") == "@meta.runtime.ws_root"
        assert _value("meta.workset.name") == "@meta.runtime.ws_name"
        assert _value("meta.workset.settings") == "@meta.workset.path/workset.yaml"
        assert _value("meta.box.mode") == "@meta.runtime.project_type"

    def test_the_settings_leaf_is_the_one_carrier(self):
        """The filename is drawn from ``WORKSET_META_FILE``, never re-typed — the spec
        fixes the ``@``-anchor, and the floor's own comment says the leaf comes from
        its one carrier."""
        assert WORKSET_META_FILE == "workset.yaml"

    @pytest.mark.parametrize("key", _VALUE_REROOT_KEYS)
    def test_the_manifest_value_is_the_floor_formula(self, key):
        floors = self._floors()
        want = _value(key)
        assert not isinstance(want, dict), f"{key} is no longer uniform — move it"
        for mode in MODES:
            assert floors[mode][key] == want, (
                f"{key} [{mode}]: manifest value says {want!r}, "
                f"meta_runtime_floor says {floors[mode][key]!r}"
            )


class TestValueAgentIdentity:
    """(ii-d) The agent-identity values ARE the identity builders' formulas.

    The parametric ``path`` row is the measured non-literal: the manifest composes the
    store root off ``@meta.agent.<agent>.name`` while the floor unrolls that hop through
    ``store_dirname``, so the pin is the RELATION (path == ``@config.agents/`` + the
    name floor's value), with the ``<agent>`` hop answered by the name floor — the
    one-hop shape.  ``settings`` is the same relation one hop further down, through the
    ``path`` anchor plus ``AGENT_META_FILE``.  The ``default.name`` / ``shell.*`` rows
    are plain literals: each fires when its node is the active one.
    """

    def test_the_manifest_values_are_the_spelled_formulas(self):
        assert _value("meta.agent.<agent>.path") == (
            "@config.agents/@meta.agent.<agent>.name"
        )
        assert _value("meta.agent.<agent>.settings") == (
            "@meta.agent.<agent>.path/agent.yaml"
        )
        assert _value("meta.agent.default.name") == "default"
        assert _value("meta.agent.shell.path") == "@config.agents/shell"
        assert _value("meta.agent.shell.name") == "shell"
        assert _value("meta.agent.shell.settings") == (
            "@meta.agent.shell.path/agent.yaml"
        )
        assert _value("meta.agent.shell.mode") == {}

    def test_the_settings_leaf_is_the_one_carrier(self):
        assert AGENT_META_FILE == "agent.yaml"

    @staticmethod
    def _identity(agent: str) -> dict[str, object]:
        return meta_identity_floor(
            box_name="conformance-box", project_path="/p", inbox="/i",
            share_global="/g", share_workset="/s", agent_name=agent,
        )

    def test_the_parametric_path_is_the_name_hop_unrolled(self):
        """``meta.agent.<a>.path`` == ``@config.agents/`` + the name floor's value.

        BARE (node == harness): the control — both spellings are one string.  PERSONA:
        the load-bearing half — the KEY segment stays canonical (``℘``) while the VALUE
        wears the ``+`` store spelling, and the relation must hold for both.
        """
        path_floor = meta_agent_path_floor(PROBE_AGENT)
        name_value = self._identity(PROBE_AGENT)[f"meta.agent.{PROBE_AGENT}.name"]
        assert path_floor[f"meta.agent.{PROBE_AGENT}.path"] == (
            f"@config.agents/{name_value}"
        )

        from kanibako.agent_ref import CANONICAL_SEP

        node = f"navigator{CANONICAL_SEP}{PROBE_AGENT}"
        node_path_floor = meta_agent_path_floor(node)
        node_name_value = self._identity(node)[f"meta.agent.{node}.name"]
        assert node_path_floor[f"meta.agent.{node}.path"] == (
            f"@config.agents/{node_name_value}"
        )

    def test_the_parametric_settings_hangs_off_the_path_anchor(self):
        floor = self._identity(PROBE_AGENT)
        assert floor[f"meta.agent.{PROBE_AGENT}.settings"] == (
            f"@meta.agent.{PROBE_AGENT}.path/{AGENT_META_FILE}"
        )

    def test_the_default_name_is_the_active_default_node(self):
        """Fires when ``default`` IS the active node — the tier core itself owns (there
        is no default plugin package), so no descriptor ever carries it."""
        floor = self._identity("default")
        assert floor["meta.agent.default.name"] == "default"

    def test_the_shell_fence_literals_are_the_active_shell_node(self):
        """The three shell-identity literals fire when ``shell`` is the active node (a
        plain-shell box selects node ``shell`` — ``agent_select`` — so this is the
        launch path, not only the set-time snapshot's ``meta_agent_path_floor`` arm)."""
        floor = self._identity("shell")
        assert floor["meta.agent.shell.name"] == "shell"
        assert floor["meta.agent.shell.path"] == "@config.agents/shell"
        assert floor["meta.agent.shell.settings"] == "@meta.agent.shell.path/agent.yaml"
        assert meta_agent_path_floor("shell") == {
            "meta.agent.shell.path": "@config.agents/shell",
        }

    def test_no_agent_box_materializes_no_agent_identity(self):
        """Anti-vacuity: the literals above fire for the shell NODE, not for a box with
        no agent at all — ``agent_name=None`` must carry no ``meta.agent.*`` key."""
        floor = meta_identity_floor(
            box_name="conformance-box", project_path="/p", inbox="/i",
            share_global="/g", share_workset="/s", agent_name=None,
        )
        assert [k for k in floor if k.startswith("meta.agent.")] == []

    def test_the_shell_mode_is_the_materialized_empty_grammar(self):
        """``meta.agent.shell.mode`` == ``{}`` — PINNED, not exempt.

        ``{}`` here says "the grammar IS empty" (the built-in ships no descriptor);
        an ABSENCE would say "no grammar was materialized" and the reader raises on
        it (D2/D4 — the floor's own docstring).  That is a materialized positive
        declaration with a named producer, which is exactly what E4 declines to cover.
        """
        assert meta_agent_grammar_floor("shell", None) == {"meta.agent.shell.mode": {}}
        # ...and the discriminator matters: a descriptor-less NON-shell agent
        # materializes nothing at all — no key, not an empty one.
        assert meta_agent_grammar_floor(PROBE_AGENT, None) == {}


class _ProbeNamedBox(_StubChannelProject):
    """A channel stub WITH a name — ``box_channel_addresses`` raises on a nameless box."""

    name = "conformance-box"


class TestValueBoxAddresses:
    """(ii-e) The box-partition addresses ARE what ``channels`` derives, carried through
    the identity floor as resolved literals.

    ⚑ A VALUE ORACLE, NOT A SECOND RESOLVER — the :class:`TestWorksetChannelDefaults`
    shape, one hop further out.  Each manifest formula is followed ONE HOP: its channel
    ref answered by the derivation that owns it, its ``@meta.box.name`` by the probe
    name — and compared to what ``box_channel_addresses`` returns, which is exactly what
    the launch seam hands ``meta_identity_floor`` (``commands/start.py`` passes
    ``addr.inbox`` / ``addr.share_global`` / ``addr.share_workset`` and
    ``proj.project_path`` through).  ``meta.box.settings`` / ``meta.box.workspace`` /
    ``meta.box.name`` have no channel derivation behind them, so their pin is the
    manifest formula verbatim plus the floor passthrough.
    """

    @staticmethod
    def _addrs(mode: str):
        from kanibako.channels import channels as ch

        proj = _ProbeNamedBox(BoxMode(mode))
        return ch.box_channel_addresses(proj, _StubChannelPaths())

    @staticmethod
    def _refs(mode: str) -> dict[str, object]:
        proj = _ProbeNamedBox(BoxMode(mode))
        std = _StubChannelPaths()
        derived = TestWorksetChannelDefaults._derived(proj, std)
        return {
            "@workset.channels.mailboxes": derived["workset.channels.mailboxes"],
            "@workset.channels.share_global": derived["workset.channels.share_global"],
            "@workset.channels.share": derived["workset.channels.share"],
            "@meta.box.name": _ProbeNamedBox.name,
        }

    @staticmethod
    def _follow(formula: str, refs: dict[str, object]) -> Path:
        return TestWorksetChannelDefaults._follow(formula, refs)

    def test_the_manifest_values_are_the_spelled_formulas(self):
        assert _value("meta.box.inbox") == "@workset.channels.mailboxes/@meta.box.name"
        assert _value("meta.box.share_global") == (
            "@workset.channels.share_global/@meta.box.name"
        )
        assert _per_mode(_value("meta.box.share_workset")) == {
            "primary": "@workset.channels.share/@meta.box.name",
            "named": "@workset.channels.share/@meta.box.name",
            "standalone": None,
        }
        assert _value("meta.box.settings") == "@meta.box.path/box.yaml"
        assert _per_mode(_value("meta.box.workspace")) == {
            "primary": "<the user's real project dir>",
            "named": "@workset.workspaces/@meta.box.name",
            "standalone": "@workset.workspaces",
        }
        assert _per_mode(_value("meta.box.name")) == {
            "primary": "<construct-time>",
            "named": "<construct-time>",
            "standalone": "<@workset.kuid>_%leaf(@meta.workset.path)%",
        }

    @pytest.mark.parametrize("mode", sorted(MODES))
    def test_the_channel_formulas_are_the_derived_addresses(self, mode):
        """The manifest's three channel formulas, followed one hop, ARE the deriver's
        answers — which is what the floor then carries."""
        refs = self._refs(mode)
        addr = self._addrs(mode)
        assert addr.inbox == self._follow(str(_value("meta.box.inbox")), refs)
        assert addr.share_global == self._follow(
            str(_value("meta.box.share_global")), refs
        )
        arm = _per_mode(_value("meta.box.share_workset"))[mode]
        if arm is None:
            assert addr.share_workset is None, (
                f"meta.box.share_workset [{mode}]: manifest declares NO value, the "
                f"deriver answered {addr.share_workset!r}"
            )
        else:
            assert addr.share_workset == self._follow(str(arm), refs)

    @pytest.mark.parametrize("mode", sorted(MODES))
    def test_the_floor_carries_the_derived_addresses(self, mode):
        """The launch seam's half: what the deriver answers is what the keyspace gets —
        a RESOLVED literal, never the formula (the manifest's own workspace note
        authorizes this: "DECLARATION only — the launch floor carries the RESOLVED dir.
        Do NOT spell the floor as this formula")."""
        addr = self._addrs(mode)
        floor = meta_identity_floor(
            box_name=_ProbeNamedBox.name, project_path="/p", inbox=str(addr.inbox),
            share_global=str(addr.share_global),
            share_workset=(
                None if addr.share_workset is None else str(addr.share_workset)
            ),
        )
        assert floor["meta.box.inbox"] == str(addr.inbox)
        assert floor["meta.box.share_global"] == str(addr.share_global)
        assert floor["meta.box.share_workset"] == (
            None if addr.share_workset is None else str(addr.share_workset)
        )

    def test_the_settings_anchor_is_a_passthrough_of_the_cascade_path(self):
        """``meta.box.settings`` carries the SAME ``cascade_box_path`` the snapshot's
        box tier reads (``commands/start.py`` M-8: one pair feeds both, so anchor and
        cascade cannot drift) — the formula's ``box.yaml`` leaf is the declaration."""
        floor = meta_identity_floor(
            box_name="b", project_path="/p", inbox="/i", share_global="/g",
            share_workset="/s", box_settings="/box/box.yaml",
        )
        assert floor["meta.box.settings"] == "/box/box.yaml"

    @pytest.mark.parametrize("mode", sorted(MODES))
    def test_the_workspace_and_name_floors_carry_the_caller_literals(self, mode):
        """``workspace`` (every mode) and ``name`` are caller literals — the primary
        ``<the user's real project dir>`` and the per-mode ``<construct-time>`` arms are
        PROSE about values composed outside any floor, asserted verbatim above."""
        floor = meta_identity_floor(
            box_name="conformance-box", project_path="/p", inbox="/i",
            share_global="/g", share_workset=None, agent_name=None,
        )
        assert floor["meta.box.workspace"] == "/p"
        assert floor["meta.box.name"] == "conformance-box"

    def test_the_standalone_name_is_the_composed_kuid_leaf(self):
        """The standalone arm's formula ``<@workset.kuid>_%leaf(@meta.workset.path)%``
        IS what ``launch.box_identity`` composes: the stored kuid, joined by ``_`` to
        the sanitized, capped project-root leaf — against a HAND-computed leaf, not the
        composer's own helper, so the leaf grammar is pinned too."""
        from kanibako.launch.box_identity import compose_standalone_name

        assert compose_standalone_name("7xk9q", Path("/ws/my proj!")) == "7xk9q_my_proj_"


#: (E6) ``value:`` rows whose cell is PROSE, not a comparable literal.  The
#: ``meta.runtime.*`` partition-token rows describe one value per mode in words
#: (``standalone=__STANDALONE__ · primary=__PRIMARY__ · named=<detected name>``) — there
#: is no single string any oracle could equal.  What IS pinned is the re-root that
#: resolves THROUGH them (``meta.workset.{path,name}``, class (ii-c) above).
NO_VALUE_RUNTIME_PROSE: frozenset[str] = frozenset({
    "meta.runtime.ws_name", "meta.runtime.ws_root",
})

#: (E7) ``value:`` rows answered OFF-SNAPSHOT through bootstrap constants, which no
#: launch floor emits.  ``user_config_file`` is READ, never composed (``[R154]``), and
#: the site pair lives as ``SITE_CONFIG_DIR`` + the file names — joining them here
#: would be a second carrier of the site layout, so each side is asserted verbatim and
#: the reason names the carrier.
NO_VALUE_BOOTSTRAP_OFF_SNAPSHOT: frozenset[str] = frozenset({
    "meta.runtime.admin.config", "meta.runtime.admin.settings",
    "meta.runtime.user.config",
})

#: (E8) ``value:`` rows CONSTRUCTED at delivery by the collapse route — one collapsed
#: map/list per row, with no single literal anywhere to compare the manifest's
#: type-prose (``dict[guest_dest -> (host_src, opts)]``) against.  An oracle would
#: re-implement the collapse (P2/P4).  The shape check names the carrier functions.
NO_VALUE_DELIVERY_CONSTRUCTED: frozenset[str] = frozenset({
    "meta.assembly.bindings", "meta.assembly.env",
    "meta.assembly.seeded", "meta.assembly.synced",
})

#: (E9) ``value:`` rows RENDERED off-snapshot, never sourced from it.
#: ``meta.box.container_name`` has NO producer (``settings_keyspace`` says so at the
#: ``meta.box`` declaration: it renders in ``utils.container_name_for`` off proj attrs,
#: not the store) — and the renderer cannot equal the manifest's ``%if`` template
#: without a template engine, i.e. a second resolver.  A whole-value ``@``-ref to it
#: would dangle, which is why nothing floors it.
NO_VALUE_OFF_SNAPSHOT_RENDER: frozenset[str] = frozenset({
    "meta.box.container_name",
})

EXEMPT_VALUE_KEYS: frozenset[str] = (
    NO_VALUE_RUNTIME_PROSE | NO_VALUE_BOOTSTRAP_OFF_SNAPSHOT
    | NO_VALUE_DELIVERY_CONSTRUCTED | NO_VALUE_OFF_SNAPSHOT_RENDER
)


class TestNoValueExemptions:
    """The declined ``value:`` classes, each asserted to be the shape its reason claims."""

    def test_the_runtime_prose_rows_really_are_prose(self):
        """E6: multi-arm descriptions, one value per mode in words — not literals."""
        assert _value("meta.runtime.ws_name") == (
            "standalone=__STANDALONE__ · primary=__PRIMARY__ · named=<detected name>"
        )
        assert _value("meta.runtime.ws_root") == (
            "standalone=<runtime project dir> · primary=@config.primary_workset · "
            "named=<detected workset root>"
        )

    def test_the_bootstrap_rows_name_their_off_snapshot_carrier(self):
        """E7: the manifest literals verbatim, and the bootstrap constants verbatim —
        deliberately NOT equated (see E7's reason: the join would be a second carrier).
        """
        assert _value("meta.runtime.admin.config") == "/etc/kanibako/base.cfg"
        assert _value("meta.runtime.admin.settings") == (
            "/etc/kanibako/settings_base.yaml"
        )
        assert _value("meta.runtime.user.config") == (
            "$XDG_CONFIG_HOME/kanibako.cfg, or ~/.config/ if unset / not absolute"
        )

        from kanibako.settings.bootstrap import (
            CONFIG_FILE,
            SITE_CONFIG_DIR,
            SITE_CONFIG_FILE,
            SITE_SETTINGS_FILE,
        )

        assert SITE_CONFIG_DIR == "/etc/kanibako"
        assert SITE_CONFIG_FILE == "base.cfg"
        assert SITE_SETTINGS_FILE == "settings_base.yaml"
        assert CONFIG_FILE == "kanibako.cfg"

    def test_the_delivery_rows_are_the_collapse_outputs(self):
        """E8: the manifest's type-prose verbatim, and the collapse entry points that
        construct them present — the reason's carrier, pinned by name."""
        assert _value("meta.assembly.bindings") == (
            "dict[guest_dest -> (host_src, opts)]"
        )
        assert _value("meta.assembly.env") == "dict[VAR -> (value, scope, key)]"
        assert _value("meta.assembly.seeded") == (
            "list[(host_src, guest_dest, opts)]"
        )
        assert _value("meta.assembly.synced") == (
            "list[(host_src, guest_dest, opts)]"
        )

        from kanibako.settings import store_collapse

        for entry in ("collapse_store_shapes", "collapse_seeded", "collapse_env"):
            assert callable(getattr(store_collapse, entry, None)), (
                f"store_collapse.{entry} is gone — E8's carrier moved, re-classify"
            )
        assert hasattr(store_collapse, "CollapsedStore")

    def test_the_container_name_really_renders_off_snapshot(self):
        """E9: the manifest's ``%if`` template verbatim, and the renderer measured —
        ``container_name_for`` answers off proj attrs (no snapshot in, no ``%if`` out),
        so equality with the template is unstatable without a second resolver."""
        assert _value("meta.box.container_name") == (
            "kanibako-@meta.box.name%if @meta.box.helper_num: "
            "-helper-@meta.box.helper_num%"
        )

        from types import SimpleNamespace

        from kanibako.utils import container_name_for

        named = SimpleNamespace(
            mode=SimpleNamespace(value="named"), name="conformance-box",
            project_hash="ab" * 32, metadata_path=Path("/ws/conformance-box"),
        )
        assert container_name_for(named) == "kanibako-conformance-box"
        standalone = SimpleNamespace(
            mode=SimpleNamespace(value="standalone"), name="7xk9q_ws",
            project_hash="ab" * 32, metadata_path=Path("/x/y"),
        )
        assert container_name_for(standalone) == "kanibako-ronin-x-y"


#: The manifest rows carrying NEITHER a ``default:`` nor a ``value:`` — measured ten,
#: each with a stated reason.  A row gaining a cell leaves this set (caught by the
#: value/default coverage); a new cell-less row lands here unclassified.
NEITHER_CELL_KEYS: frozenset[str] = frozenset({
    "agent.<agent>.<key>",
    "agent.<agent>.access",
    "meta.agent.<agent>.auth.share_support",
    "meta.agent.<agent>.exec",
    "meta.agent.<agent>.mode",
    "meta.agent.<agent>.name",
    "meta.agent.shell.exec",
    "meta.box.agent.<key>",
    "meta.box.helper_num",
    "meta.runtime.project_type",
})

#: Why each neither-row has no comparable cell.  Every key above must appear here —
#: a row without a reason is unclassified, which is what this table exists to forbid.
NEITHER_CELL_REASONS: dict[str, str] = {
    # SHAPE rows: the parametric contract, not one value — any single spelling would
    # be a fabrication, and the per-node values live in plugin/launch tiers.
    "agent.<agent>.<key>": "shape row (per-agent contract; values are plugin-declared)",
    "meta.box.agent.<key>": (
        "shape row (RO read-back mirror of the effective agent subtree)"
    ),
    # Per-agent leaves that FALL BACK or ARRIVE per-plugin: no declared value of their
    # own — ``access`` reads ``agent.default.access``; the auth capability, ``exec``
    # fragment and ``mode`` grammar arrive per descriptor at launch.
    "agent.<agent>.access": "fallback leaf (agent.default.access answers)",
    "meta.agent.<agent>.auth.share_support": "plugin-set capability per agent",
    "meta.agent.<agent>.exec": "per-descriptor one-shot fragment, absent if undeclared",
    "meta.agent.<agent>.mode": "per-descriptor launch grammar, absent if undeclared",
    # Construct-time identity: REQUIRED per agent but resolved per box, so the
    # registry declares the requirement, not a spelling (the concrete shell twin
    # IS a value row, pinned in (ii-d)).
    "meta.agent.<agent>.name": "construct-time identity, required, no declared spelling",
    # The fence no-op: never set for shell — no STANDALONE op, no descriptor — and
    # the fence reads ``<None>`` (the row's own note).
    "meta.agent.shell.exec": "never set for shell; the fence reads <None>",
    # Never-in-snapshot: ``helper_num`` travels as a STRUCTURED field in helper
    # messages (its row's note); ``project_type`` is the mode token the runtime floor
    # TAKES AS INPUT, not a value it declares.
    "meta.box.helper_num": "spawn-only structured field, unset for a top-level box",
    "meta.runtime.project_type": "runtime-treewalk input token, not a declared value",
}


class TestDefaultsCoverage:
    """The property that keeps this file honest as the manifest grows."""

    def test_every_default_row_is_pinned_or_named(self):
        """PINNED ∪ EXEMPT == every manifest row carrying a ``default:``.

        ⚑ THIS IS THE LOAD-BEARING CASE.  Any individual pin above can be deleted and
        the suite still looks fine; delete a row from the coverage and this goes red
        naming it.  A NEW default row added to the manifest lands here as an unclassified
        key — which is the correct outcome: somebody must decide whether it has an
        oracle, and say so in one of the tables above.
        """
        declared = {
            str(k) for k, v in _keys().items() if isinstance(v, dict) and "default" in v
        }
        # ⚑ The NAMES first, the count second.  A new default row must red saying WHICH
        # key is unclassified; a bare count would only say the arithmetic moved.
        unclassified = declared - PINNED_DEFAULT_KEYS - EXEMPT_DEFAULT_KEYS
        assert not unclassified, (
            f"manifest default rows with neither an oracle nor a named exemption: "
            f"{sorted(unclassified)} — add each to a class in this file (with a reason) "
            f"or pin it"
        )
        stale = (PINNED_DEFAULT_KEYS | EXEMPT_DEFAULT_KEYS) - declared
        assert not stale, (
            f"this file classifies rows the manifest no longer declares a default for: "
            f"{sorted(stale)}"
        )
        assert len(declared) == 79, (
            f"the manifest gives {len(declared)} rows a default, not the 79 measured — "
            f"re-classify, do not adjust the count"
        )

    def test_the_split_is_the_measured_split(self):
        """53 pinned rows, 14 exempted — stated so a silent migration between them reds.

        ⚑ Was 41/24 until the seven-row channel family moved from E1 to a real oracle
        (2026-08-25), then 48/17 until ``workset.registry`` followed it out of E1
        (2026-08-29, when the anchor floor started spelling its formula), then 49/16, then
        50/15 (``workset.template``, out the same way and for the same two reasons: the
        "no literal" claim was false, and the row answered at no terminus).
        ⚑⚑ NOW 51/14 — ``workset.workspaces``, E1's LAST member, so **the class itself is
        gone** rather than left standing empty for a future row to be parked in. There is
        no "path join" exemption to move back to; a row that wants one has to argue for a
        new class with its own reason.
        ⚑ 51/14 → 52/14 (2026-09-08): ``agent.default.label`` joined the BEHAVIOR floor.
        It arrived PINNED, not exempt — the spec declares a literal value and
        ``core-defaults.yaml``'s ``agent_default:`` carries it, so there is an artefact to
        compare against and no reason to decline one.
        ⚑ 52/14 → 53/14 (2026-09-14): ``system.state`` was declared in the code, closing a
        spec-conformance gap (the keyspec §2g has carried the row since R-43). It arrives
        PINNED with no edit here — ``_PATH_ORACLE`` IS ``SYSTEM_PATH_DEFAULTS``.
        ⚑ 53/14 → 55/14 (2026-09-19): ``system.helpers.{depth,breadth}`` were declared,
        closing a §0 violation — the pair was LIVE and read from a bespoke file. They
        arrive PINNED against the spawn resolver's own fallback; a budget default with no
        oracle would be a number nobody is answerable for.
        ⚑ 55/14 → 56/14 (2026-09-20): ``agent.default.env.TERM``, the second shipped
        ``env`` default (Jei's all-agents ruling). PINNED for the same reason its
        ``box.env.COLORTERM`` twin is — ``core-defaults.yaml``'s ``env:`` table carries
        it, and :meth:`TestSingletonDefaults.test_the_core_env_floor` reads that emitter.
        ⚑ 56/14 → 60/19 (2026-09-21): the shell tier's fence rows (D1 Step 2 on D2's
        node). PINNED: ``agent.shell.{label,access,allow_helpers}`` against the new
        ``agent_shell:`` floor and ``agent.shell.canon`` against its producer arm.
        EXEMPT in the existing classes: the four ``<None>`` tier rows join E3
        (no floor installs them — the resolve reads the §2d fallback) and the
        ``{}`` row joins E4.
        ⚑ 60/19 → 61/18 (2026-09-24): ``agent.shell.template`` left E3 for
        ``_SINGLETON_KEYS`` — the shell arm of ``agent_template_defaults`` now floors
        its ``<None>`` as a PRESENT ``None``, so the row has a carrier to pin.
        ⚑ 61/18 → 64/15 (2026-09-24): ``agent.shell.{bootstrap,run_args,transform}``
        left E3 for ``_SHELL_TIER_KEYS`` — ``core-defaults.yaml``'s ``agent_shell:``
        floors them (``bootstrap`` as ``tmux``, the other two as PRESENT ``None``).
        """
        assert len(PINNED_DEFAULT_KEYS) == 64
        assert len(EXEMPT_DEFAULT_KEYS) == 15
        assert not (PINNED_DEFAULT_KEYS & EXEMPT_DEFAULT_KEYS)

    def test_every_value_row_is_pinned_or_named(self):
        """PINNED ∪ EXEMPT == every manifest row carrying a ``value:``.

        The value-side twin of
        :meth:`test_every_default_row_is_pinned_or_named`: a ``value:`` edit was green
        by construction until section 4b existed, so this case is what makes a lost
        value cell red.  A NEW value row lands here as an unclassified key.
        """
        declared = {
            str(k) for k, v in _keys().items() if isinstance(v, dict) and "value" in v
        }
        unclassified = declared - PINNED_VALUE_KEYS - EXEMPT_VALUE_KEYS
        assert not unclassified, (
            f"manifest value rows with neither a pin nor a named exemption: "
            f"{sorted(unclassified)} — add each to a class in section 4b (with a "
            f"reason) or pin it"
        )
        stale = (PINNED_VALUE_KEYS | EXEMPT_VALUE_KEYS) - declared
        assert not stale, (
            f"section 4b classifies rows the manifest no longer declares a value for: "
            f"{sorted(stale)}"
        )
        assert len(declared) == 34, (
            f"the manifest gives {len(declared)} rows a value, not the 34 measured — "
            f"re-classify, do not adjust the count"
        )

    def test_the_value_split_is_the_measured_split(self):
        """24 pinned rows, 10 exempted — stated so a silent migration between them reds."""
        assert len(PINNED_VALUE_KEYS) == 24
        assert len(EXEMPT_VALUE_KEYS) == 10
        assert not (PINNED_VALUE_KEYS & EXEMPT_VALUE_KEYS)

    def test_the_neither_rows_are_exactly_the_measured_ten(self):
        """The ten rows carrying NEITHER cell are named, cell-less, and reasoned.

        A row gaining a ``default:`` or ``value:`` leaves this set (and must arrive in
        one of the two coverage cases above); a new cell-less row lands here
        unclassified.  A row without a stated reason is unclassified too.
        """
        neither = {
            str(k) for k, v in _keys().items()
            if isinstance(v, dict) and "default" not in v and "value" not in v
        }
        assert neither == NEITHER_CELL_KEYS, (
            f"neither-cell rows moved: unclassified={sorted(neither - NEITHER_CELL_KEYS)}, "
            f"stale={sorted(NEITHER_CELL_KEYS - neither)}"
        )
        assert len(NEITHER_CELL_KEYS) == 10
        assert set(NEITHER_CELL_REASONS) == NEITHER_CELL_KEYS, (
            f"reason table drift: unreasoned="
            f"{sorted(NEITHER_CELL_KEYS - set(NEITHER_CELL_REASONS))}, "
            f"stale={sorted(set(NEITHER_CELL_REASONS) - NEITHER_CELL_KEYS)}"
        )

    def test_the_default_value_neither_cells_partition_the_registry(self):
        """79 + 34 + 10 == 123, disjoint — no row carries both cells, none carries
        neither unnoticed.  The 123 is the loader's own count, re-stated here as the
        arithmetic the three coverage cases must sum to."""
        keys = _keys()
        defaulted = {
            str(k) for k, v in keys.items() if isinstance(v, dict) and "default" in v
        }
        valued = {
            str(k) for k, v in keys.items() if isinstance(v, dict) and "value" in v
        }
        neither = {
            str(k) for k, v in keys.items()
            if isinstance(v, dict) and "default" not in v and "value" not in v
        }
        assert not (defaulted & valued), (
            f"rows carrying BOTH cells: {sorted(defaulted & valued)}"
        )
        assert defaulted | valued | neither == {str(k) for k in keys}
        assert (len(defaulted), len(valued), len(neither)) == (79, 34, 10)


# --------------------------------------------------------------------------- #
# 5. KEY-SET conformance — the SCALAR half
# --------------------------------------------------------------------------- #

#: The manifest rows that declare a SHAPE rather than a key: the ``<key>`` placeholder
#: stands for "any legal tail", so there is no single spelling to validate.
SHAPE_ROWS: frozenset[str] = frozenset({
    "agent.<agent>.<key>", "meta.box.agent.<key>",
})

#: The transcribed shell-tier fence rows (D1 Step 2 on D2's node): concrete rows the
#: scalar declarations cannot cover — ``agent.shell.*`` is neither the default tier
#: nor the parametric per-node arm, and ``meta.agent.shell.*`` is neither the
#: default literal nor the parametric arm.  Enumerated (a sixth kind, with the
#: anti-vacuity pin below asserting exactly these members), because a derivation
#: off the leaf sets would ALSO cover the three leaves the fence deliberately
#: omits (continue_mode/model/endpoint) and the bindings arms the category
#: family owns — the fence's shape, not the vocabulary's, is what is pinned.
SHELL_TIER_ROWS: frozenset[str] = frozenset({
    "agent.shell.label", "agent.shell.access", "agent.shell.allow_helpers",
    "agent.shell.bootstrap", "agent.shell.run_args", "agent.shell.transform",
    "agent.shell.transform_settings", "agent.shell.template",
    "agent.shell.canon",
    "meta.agent.shell.name", "meta.agent.shell.path",
    "meta.agent.shell.settings", "meta.agent.shell.mode",
    "meta.agent.shell.exec",
})

#: ⚑ FINDING 4 IS CLOSED (2026-08-21) and its exemption is GONE, which is what the
#: exemption's own anti-vacuity case demanded happen on a fix.  ``key_validity`` used to
#: refuse the DECLARED row ``meta.box.agent.auth.share_support``: ``_meta_reason``'s
#: ``meta.box.agent`` mirror arm delegated to ``_agent_tail_reason``, which knows the
#: ``agent.<a>.<leaf>`` contract and has no ``auth.<leaf>`` arm, while the thing being
#: mirrored is a ``meta.agent.<a>.auth.*`` key.  The refusal was called "inert" because
#: ``key_validity`` guards the SET boundary and a ``meta.*`` key is refused there anyway
#: — the PART 2 ``KeyStore`` write census killed that word by MEASURING the key
#: materialized into real stores 1334× on the launch path.  The mirror now carries its
#: own ``auth.*`` arm, sourced from ``DECLARED_META_AGENT_AUTH_LEAVES``.
#: (No exemption dict remains; a future finding of this kind re-introduces one here.)


#: The ``(prefix, leaves)`` table :func:`_code_scalar_keys` expands, lifted out of the
#: function so that :meth:`TestKeySetConformance.test_no_declaration_family_is_empty`
#: reads the SAME table the sweep does.  A vacuity guard holding its own copy of the
#: prefixes would keep passing after a family stopped being swept, which is the failure
#: it exists to catch.
_SCALAR_DECLARATIONS: tuple[tuple[str, frozenset[str]], ...] = (
    ("config.", DECLARED_CONFIG_LEAVES),
    ("system.", DECLARED_SYSTEM_LEAVES),
    ("system.channels.", DECLARED_SYSTEM_CHANNEL_LEAVES),
    ("system.helpers.", DECLARED_SYSTEM_HELPERS_LEAVES),
    ("system.auth.", DECLARED_SYSTEM_AUTH_LEAVES),
    ("box.", DECLARED_BOX_LEAVES),
    ("box.auth.", DECLARED_BOX_AUTH_LEAVES),
    ("workset.", DECLARED_WORKSET_LEAVES),
    ("workset.auth.", DECLARED_WORKSET_AUTH_LEAVES),
    ("workset.channels.", DECLARED_WORKSET_CHANNEL_LEAVES),
    ("agent.default.", DECLARED_AGENT_LEAVES),
    ("meta.runtime.", DECLARED_META_RUNTIME_LEAVES),
    ("meta.runtime.user.", DECLARED_META_RUNTIME_USER_LEAVES),
    ("meta.runtime.admin.", DECLARED_META_RUNTIME_ADMIN_LEAVES),
    ("meta.assembly.", DECLARED_META_ASSEMBLY_LEAVES),
    ("meta.workset.", DECLARED_META_WORKSET_LEAVES),
    ("meta.workset.auth.", DECLARED_META_WORKSET_AUTH_LEAVES),
    ("meta.box.", DECLARED_META_BOX_LEAVES),
    ("meta.box.auth.", DECLARED_META_BOX_AUTH_LEAVES),
    ("meta.agent.<agent>.", DECLARED_META_AGENT_LEAVES),
    ("meta.agent.<agent>.auth.", DECLARED_META_AGENT_AUTH_LEAVES),
)


def _code_scalar_keys() -> set[str]:
    """Every SCALAR key the ``DECLARED_*`` frozensets declare, as dotted spellings.

    ⚑ The agent tier is enumerated at ``agent.default.*`` only.  The per-node arm
    (``agent.<agent>.<leaf>``) is covered by the manifest's SHAPE row
    ``agent.<agent>.<key>`` — a derivation, not a hand list — so expanding it here would
    manufacture 11 spurious "code-not-manifest" rows.
    ⚑ Category keys (``<scope>.bindings.ro`` and friends) are declared under the
    manifest's ``categories:`` table, not ``keys:``, and the frozensets do not contain
    them either, so they never enter this diff.
    ⚑ The NESTED arms are members here like any other: ``meta.runtime.{user,admin}.``
    and the three auth arms ``meta.box.auth.`` / ``meta.agent.<agent>.auth.`` /
    ``meta.workset.auth.`` are pinned by the two directions below, which is where
    a reader who finds no nested guard in ``test_settings_keyspace.py`` should look.
    """
    out: set[str] = set()
    for prefix, leaves in _SCALAR_DECLARATIONS:
        out |= {prefix + leaf for leaf in leaves}
    return out


class TestKeySetConformance:
    """The manifest's ``keys:`` rows and the code's ``DECLARED_*`` sets are one set."""

    def test_every_manifest_row_is_a_key_the_code_recognizes(self):
        """Direction 1, against the PRODUCTION predicate rather than a set copy.

        ``key_validity`` is what actually decides "is this a key" at every boundary, so
        running the whole registry through it is a stronger statement than any set
        arithmetic: it exercises the parametric arms, the ``meta`` group dispatch and the
        category-position rules exactly as a user's ``config set`` would.
        """
        refused: dict[str, str] = {}
        for row in _keys():
            key = str(row)
            if key in SHAPE_ROWS:
                continue
            reason = key_validity(
                key.replace("<agent>", PROBE_AGENT), valid_agents=PROBE_AGENTS,
            )
            if reason is not None:
                refused[key] = reason
        assert not refused, (
            f"the manifest declares rows the code refuses as keys (closed keyspace, "
            f"spec §0): {refused}"
        )

    def test_the_shape_rows_are_shape_rows(self):
        """Anti-vacuity for the two skips above: they must carry the ``<key>`` placeholder."""
        for row in SHAPE_ROWS:
            assert row in _keys(), f"{row} is no longer a manifest row"
            assert "<key>" in row

    def test_the_agent_mirror_carries_the_auth_capability(self):
        """Finding 4's CLOSE-OUT pin: the mirror accepts ``auth.*``, the SCOPE does not.

        Both halves matter.  Accepting the mirror row without refusing the agent-scope
        spelling would make the capability look settable, and it is plugin-set (spec
        :1103) — there is no ``agent.<agent>.auth.*`` key to mirror.
        """
        assert key_validity(
            "meta.box.agent.auth.share_support", valid_agents=PROBE_AGENTS,
        ) is None
        assert key_validity(
            f"agent.{PROBE_AGENT}.auth.share_support", valid_agents=PROBE_AGENTS,
        ) is not None
        assert key_validity(
            "meta.box.agent.auth.invented", valid_agents=PROBE_AGENTS,
        ) is not None

    def test_no_declared_scalar_key_is_missing_from_the_manifest(self):
        """Direction 2 — every ``DECLARED_*`` spelling has a registry row, with NO exemption.

        ``agent.default.template`` used to be exempt here (finding 2).  It is MEANT and it
        has its row: it is the §2d default-tier arm of the template SOURCE, like every
        other ``DECLARED_AGENT_LEAVES`` member.  Do not re-open it.
        """
        missing = _code_scalar_keys() - {str(k) for k in _keys()}
        assert missing == set(), (
            f"declared keys with no manifest row: {sorted(missing)}"
        )

    def test_no_manifest_row_is_missing_from_the_declarations(self):
        """The other side of direction 2, with the derivations spelled out.

        What is left over after the scalar sets is exactly four kinds of row, and each
        kind is a DECLARED SHAPE rather than an omission — which is why this is an
        enumerated assertion and not an exemption list: if a FIFTH kind appears, it is a
        real drift and it lands here.
        """
        leftover = {str(k) for k in _keys()} - _code_scalar_keys()
        # ⚑ THE CATEGORY KIND IS DERIVED FROM THE MANIFEST'S OWN `categories:` TABLE,
        # not hand-listed, so it states its reason exactly once: the DECLARED_* sets
        # hold SCALAR leaves, and anything whose head is a declared category — the
        # terminal dest-keyed rows (`box.bindings.ro`) and a member of a parametric
        # family (`box.env.COLORTERM` and `agent.default.env.TERM`, the two env
        # defaults kanibako ships) — is declared THERE instead.  One reason, no
        # per-row exception.
        # ⚑ THE SCOPE HEAD IS STRIPPED BY THE KEYSPACE'S RULE, not by a `box.` prefix
        # test (2026-09-20).  The prefix was incidental — every category row happened
        # to be box-scope — and it silently classified an agent-scope member of a
        # declared family as an unaccounted row.  A category family is declared at
        # FOUR scopes (spec §2a), so the derivation reads the head off the key: one
        # segment, or TWO for the agent tier, whose head is `agent.<node>` because
        # bare `agent` is not a key.
        declared_categories = {
            name for name, row in manifest_doc()["categories"].items()
            if isinstance(row, dict) and "value" in row
        }
        assert BIND_CATEGORIES <= declared_categories, (
            "the manifest's categories: table no longer names every BIND_CATEGORIES "
            f"member: {sorted(BIND_CATEGORIES - declared_categories)}"
        )

        def _category_tail(key: str) -> str:
            """*key* with its SCOPE head removed; ``""`` when it names no scope."""
            head, _, tail = key.partition(".")
            if head not in SCOPE_CONTAINMENT:
                return ""
            if head == "agent":
                _, _, tail = tail.partition(".")
            return tail

        category_rows = {
            key for key in leftover
            if (tail := _category_tail(key))
            and (tail in declared_categories
                 or tail.rpartition(".")[0] in declared_categories)
        }
        parametric_agent = {
            f"agent.<agent>.{leaf}" for leaf in DECLARED_AGENT_LEAVES
        }
        expected = (
            SHAPE_ROWS                                   # `<key>` placeholders
            | (leftover & category_rows)                 # declared under `categories:`
            | (leftover & parametric_agent)              # the per-node agent arm
            | {"meta.agent.default.name"}                # the always-legal `default` node
            | {"meta.box.agent.auth.share_support"}      # the agent-mirror sub-namespace
            | (leftover & SHELL_TIER_ROWS)               # the transcribed shell fence (D2)
        )
        assert leftover == expected, (
            f"manifest rows the scalar declarations do not account for: "
            f"{sorted(leftover - expected)}; classified rows the manifest no "
            f"longer declares: {sorted(expected - leftover)}"
        )
        # And the derivations are not vacuous: each class actually has members.
        assert leftover & category_rows == {
            "box.bindings.ro", "box.bindings.rw", "box.masks", "box.env.COLORTERM",
            "agent.default.env.TERM",
        }
        assert leftover & parametric_agent == {
            "agent.<agent>.access", "agent.<agent>.template", "agent.<agent>.canon",
        }
        assert leftover & SHELL_TIER_ROWS == SHELL_TIER_ROWS, (
            f"shell-tier rows missing from the manifest: "
            f"{sorted(SHELL_TIER_ROWS - leftover)}"
        )

    def test_no_declaration_family_is_empty(self):
        """ANTI-VACUITY for both directions above (P15): an empty family is SILENT.

        Set-equality catches drift on ONE side.  Empty a ``DECLARED_*`` frozenset and
        delete its manifest rows in the same edit, and every case above stays green:
        the code contributes no spelling to miss and the manifest offers no row to
        leave over.  MEASURED 2026-09-19 on a stand-in manifest — dropping
        ``meta.box.auth.workset_path`` from the registry while
        ``DECLARED_META_BOX_AUTH_LEAVES`` goes empty reds none of the cases above.

        ⚑ FOUR families hold a SINGLE leaf and are the exposed ones, because one
        deletion empties them outright: ``system.auth``, ``meta.runtime.user``,
        ``meta.workset.auth`` and ``meta.agent.<agent>.auth``.  Not every auth arm is
        small — ``box.auth`` holds 2 and ``workset.auth`` and ``meta.box.auth`` hold
        3 each (counted 2026-09-19, re-counted 2026-09-22).

        🛑 WHAT THIS DOES NOT BUY — BOTH HALVES MEASURED 2026-09-19, on a stand-in:

        * RETIRING A FAMILY IS STILL UNWITNESSED.  Delete a family's
          :data:`_SCALAR_DECLARATIONS` row — the edit this case's own failure message
          asks for — together with its manifest rows, and leave the frozenset
          declared: every case in this class is GREEN, while ``key_validity`` still
          answers ``meta.box.auth.workset_path`` with ``None``.  That is a live key
          with no registry row and no coverage.  The only red is the loader's hand
          count of manifest rows, which a developer re-measures as a matter of course.  A
          retirement is complete only when the frozenset goes too, and nothing here
          says so.
        * A COORDINATED EDIT dropping one leaf of a multi-leaf family from both
          carriers.  Two carriers cannot witness their own agreement; the spec is the
          outside oracle, and the keyspec is what a key deletion has to move first.
        """
        empty = [prefix for prefix, leaves in _SCALAR_DECLARATIONS if not leaves]
        assert not empty, (
            f"declaration families with no leaves: {empty} — the sweep above covers "
            f"them vacuously.  If the family is genuinely retired, drop its row from "
            f"_SCALAR_DECLARATIONS in the same edit"
        )

    def test_the_declaration_table_names_every_exported_leaf_set(self):
        """ANTI-VACUITY for the sweep itself (P15): the table cannot silently shrink.

        ⚑ THE ATTACK THIS CLOSES, MEASURED 2026-09-19: delete a family's
        :data:`_SCALAR_DECLARATIONS` row — the edit the sibling case's own failure
        message asks for — together with its manifest rows, re-measure the loader's
        hand count, and leave the ``DECLARED_*`` frozenset declared: every case in
        this class stays GREEN while ``key_validity`` still answers the key with
        ``None`` (measured on ``meta.box.auth.workset_path``).  That is a live key
        with no registry row and no coverage.

        🛑 THE COUNT PIN IS NOT THE GUARD — it reds on the manifest half of that
        edit, but its own message says *"re-measure"*, so a developer re-measures
        it as a matter of course.  THIS EQUALITY IS THE GUARD: the set of
        ``DECLARED_*_LEAVES`` names :data:`_SCALAR_DECLARATIONS` references must
        EQUAL the set ``settings_keyspace`` actually exports.  A dropped row names
        its family here, and so does a new export nobody wired into the sweep.

        ⚑ Identity is per OBJECT, so two exported names bound to one frozenset are
        one leaf set: the row referencing it names BOTH, and neither is unswept.
        """
        from kanibako.settings import settings_keyspace as keyspace_module

        exported = {
            name
            for name, value in vars(keyspace_module).items()
            if name.startswith("DECLARED_") and name.endswith("_LEAVES")
            and isinstance(value, frozenset)
        }
        names_by_id: dict[int, set[str]] = {}
        for name in exported:
            names_by_id.setdefault(id(getattr(keyspace_module, name)), set()).add(name)
        unnamed = [
            prefix for prefix, leaves in _SCALAR_DECLARATIONS
            if id(leaves) not in names_by_id
        ]
        assert not unnamed, (
            f"declaration rows referencing no exported DECLARED_*_LEAVES set: "
            f"{unnamed} — the sweep above covers them against nothing"
        )
        row_ids = [id(leaves) for _, leaves in _SCALAR_DECLARATIONS]
        referenced = set().union(*(names_by_id[row_id] for row_id in row_ids))
        assert len(set(row_ids)) == len(row_ids), (
            f"two declaration rows reference one leaf set: "
            f"{len(row_ids)} rows name {len(set(row_ids))} sets "
            f"({sorted(referenced)}) — one family rides another's coverage"
        )
        assert referenced == exported, (
            f"the declaration table and the keyspace exports disagree: rows with "
            f"no export: {sorted(referenced - exported)}; exports with no row "
            f"(unswept families): {sorted(exported - referenced)}"
        )


class TestSetColumnConformance:
    """The ``set:`` column, in the two directions that are MEASURED TRUE."""

    def test_every_routed_key_is_a_declared_manifest_row(self):
        """``config_keys._KEY_ROUTES`` ⊆ the registry — no route to an undeclared key.

        This is the closed keyspace stated at the CLI SET seam: the routing table decides
        which settings-file slot a ``config set`` writes into, so a route to a spelling
        the registry does not declare would be a write to a key that does not exist.
        """
        undeclared = set(_KEY_ROUTES) - {str(k) for k in _keys()}
        assert not undeclared, (
            f"_KEY_ROUTES routes keys the manifest does not declare: {sorted(undeclared)}"
        )
        assert len(_KEY_ROUTES) >= 30, "the routing table shrank — re-measure this pin"

    def test_no_never_settable_row_is_routed(self):
        """``set: never`` rows are exactly the ``meta.*`` group, and none is routed.

        ⚑ THE CONVERSE IS NOT ASSERTED **HERE**, and the reason narrowed on 2026-08-23.
        ``cli+file ⇒ in _KEY_ROUTES`` was never the right shape: the ``agent.*`` tier is
        written through the agent-file writer, and the bare any-agent keys through their
        own branch, so this table is one of several write routes.  It ALSO used to carry
        a false reason — that the ``system.*`` path keys "are hand-edited in
        ``kanibako_config.yaml`` and CLI-REFUSED by design".  They were refused, and it
        was a spec violation (§2g), not a design; they are routed now.
        ⚑ The converse IS asserted, against the VERB rather than this table, by
        ``tests/test_settings/test_set_column_conformance.py``.
        """
        never = {
            str(k) for k, v in _keys().items()
            if isinstance(v, dict) and v.get("set") == "never"
        }
        # ⚑ THE PROPERTY IS AN IFF AND IS ASSERTED AS ONE (P13).  The manifest's own
        # ``keys:`` header states it — *"set == never IFF the key is meta.*.  The
        # conformance test should ASSERT that property rather than trust each entry"* —
        # and half of it used to be bought with ``len(never) == 31``, a hand-maintained
        # count that says nothing about the ``meta.*`` row a future edit forgets to mark
        # ``never``.  The re-measure tripwire is the row count in
        # ``TestManifestLoader.test_the_document_carries_the_sections_this_file_asserts_against``;
        # a second one here was a second carrier of the same fact.
        meta_rows = {str(k) for k in _keys() if str(k).startswith("meta.")}
        assert never, "no row is set: never — the corpus is empty, not clean"
        assert never == meta_rows, (
            f"set: never rows that are not meta.*: {sorted(never - meta_rows)}; "
            f"meta.* rows that are not set: never: {sorted(meta_rows - never)}"
        )
        assert not (never & set(_KEY_ROUTES))


class TestThePathTypeColumnHasOneCodeCarrier:
    """The registry's ``type: path`` rows and what the CODE treats as a path must AGREE.

    ⚑ WHY THIS PIN EXISTS (P15).  [R147]'s refusal reaches exactly the keys
    ``config_keys.is_path_valued_key`` claims, and a registry row the code does not claim
    is a path key that quietly takes an ambiguous value at every set route.  Completeness
    is by HAND for the ``workset.*`` and ``box.*`` rows — no live table enumerates them —
    so it is bought back here, LOUDLY, instead of being trusted.

    ⚑ THE FOUR ``agent`` ROWS AND THE ``secret_path`` FAMILY ARE PARAMETRIC and carry no
    fixed canonical spelling, so they are asserted through the predicate at the shapes the
    keyspace admits rather than through ``KEY_TYPES``.

    ⚑ THE CORPUS IS THE **SETTABLE** PATH ROWS, AND THE ``set:`` COLUMN IS WHAT SPLITS IT —
    not a name list.  [R147] governs a STORED value: its own words are *"not a legal STORED
    VALUE … refused at SET TIME and at READ TIME"*, the keyspace set/get routes.  A
    ``set: never`` row has NO set route — ``TestSetColumnConformance`` asserts the whole
    ``meta.*`` group is absent from ``_KEY_ROUTES`` — so there is no seam at which the
    predicate could reach one, and ``is_path_valued_key``'s own header says it is the
    SET-TIME half.  Both directions are still asserted below: the settable rows must be
    claimed, and the never-settable ones must NOT be, so ``KEY_TYPES`` cannot quietly grow
    a ``meta.*`` entry that reads as a set-time guard which can never fire.
    """

    def _declared_path_rows(self) -> "tuple[set[str], set[str]]":
        """The registry's ``type: path`` rows, split ``(settable, never)`` by ``set:``."""
        rows = {
            str(key): row for key, row in _keys().items()
            if isinstance(row, dict) and row.get("type") == "path"
        }
        never = {key for key, row in rows.items() if row.get("set") == "never"}
        return set(rows) - never, never

    def test_every_settable_registry_path_row_is_claimed_by_the_predicate(self):
        from kanibako.settings.config_keys import is_path_valued_key

        declared, _ = self._declared_path_rows()
        assert len(declared) >= 39, "the registry's path rows shrank — re-measure this pin"
        # ⚑ ``<agent>`` is the registry's PLACEHOLDER for a discriminated node, not a
        # spelling; substituting a node is what the keyspace itself does.
        unclaimed = {
            key for key in declared
            if not is_path_valued_key(key.replace("<agent>", "claude"))
        }
        assert not unclaimed, (
            f"the registry declares these keys ``type: path`` and the code does not "
            f"treat them as paths, so [R147]'s refusal never reaches them: "
            f"{sorted(unclaimed)}"
        )

    def test_a_never_settable_path_row_is_not_claimed_by_the_set_time_predicate(self):
        """The other half of the split — asserted, not assumed away.

        A ``set: never`` path row never travels a set route: ``meta.box.home``
        resolves off ``@meta.box.path``, and the ``meta.runtime.*`` bootstrap
        locators are declared-but-unproduced (their producers are their own seam).
        Claiming one in
        ``is_path_valued_key`` would advertise a set-time refusal that has nothing to
        refuse, and would hand ``path_key_anchor`` a key with no anchor to name.
        """
        from kanibako.settings.config_keys import is_path_valued_key

        _, never = self._declared_path_rows()
        assert never, (
            "no ``set: never`` row declares ``type: path`` — this case is vacuous, so "
            "the split in the sibling test above is buying nothing; re-measure"
        )
        assert all(key.startswith("meta.") for key in never), sorted(
            key for key in never if not key.startswith("meta.")
        )
        claimed = {
            key for key in never
            if is_path_valued_key(key.replace("<agent>", "claude"))
        }
        assert not claimed, (
            f"the code treats these never-settable rows as set-time path keys, but they "
            f"have no set route for [R147] to reach: {sorted(claimed)}"
        )

    def test_the_code_types_every_key_the_registry_declares_it_at(self):
        """``KEY_TYPES`` ⊆ the registry's ``type:`` COLUMN — the pairs, not just the names.

        ⚑ THE NAME-ONLY VERSION OF THIS TEST WAS NARROWER THAN ITS OWN TITLE.  It asked
        whether a ``path``-typed ``KEY_TYPES`` entry NAMED a declared row and stopped
        there, so the registry's ``type:`` was never consulted and the code could type a
        real key as anything it liked.  Measured on the honest tree: injecting
        ``KEY_TYPES["meta.box.name"] = "path"`` passes the name-only version, because
        ``meta.box.name`` IS a declared row; here it is a MISMATCH.

        ⚑⚑ AND THE REGISTRY'S TYPE COLUMN IS PARTIAL, WHICH IS WHAT MADE THAT INJECTION
        INVISIBLE RATHER THAN MERELY UNCHECKED.  The rows carrying NO ``type:`` FIELD AT
        ALL are the untyped ``meta.*`` shapes and the parametric
        ``agent.<agent>.<key>`` contract shape, which is ``set: cli+file`` and untyped
        because it is a SHAPE, not a key.  ⚑ WHICH ``meta.*`` ROWS GO UNTYPED IS STATED
        ONCE, in the ``##`` header above that group in the manifest — do not restate the
        rule here, which is how this docstring grew a bucket the ratification never
        named.  A missing type is therefore asserted as a MISMATCH (``None != kind``) rather than skipped, because
        an absent registry type cannot license a code type; that is the closed keyspace
        (spec §0) applied one level down from the key name to the key's SHAPE.

        The existence half is kept — a ``KEY_TYPES`` entry naming no declared row at all
        is the §0 violation proper — but it is now asserted for EVERY type rather than
        for ``path`` alone, so the two halves are one carrier of one property.
        """
        from kanibako.settings.config_keys import KEY_TYPES

        rows = {str(key): row for key, row in _keys().items() if isinstance(row, dict)}
        assert KEY_TYPES, "KEY_TYPES is empty — this corpus is vacuous, not clean"

        invented = {str(key) for key in KEY_TYPES if str(key) not in rows}
        assert not invented, (
            f"KEY_TYPES types these but the registry declares no such key "
            f"(the keyspace is CLOSED, spec §0): {sorted(invented)}"
        )
        mistyped = {
            str(key): (kind, rows[str(key)].get("type"))
            for key, kind in KEY_TYPES.items()
            if str(key) in rows and rows[str(key)].get("type") != kind
        }
        assert not mistyped, (
            f"KEY_TYPES disagrees with the registry's type: column — "
            f"{{key: (code says, registry says)}}: {mistyped}. The registry is "
            f"authority; a registry type of None means the row declares no type, which "
            f"licenses no code type at all"
        )

    def test_the_parametric_secret_path_family_is_claimed_at_every_spelling(self):
        """``secret_path`` carries ``value: path``, which a ``type:`` grep MISSES."""
        from kanibako.settings.config_keys import is_path_valued_key

        assert manifest_doc()["categories"]["secret_path"]["value"] == "path"
        for spelling in (
            "system.secret_path.TOKEN", "workset.secret_path.TOKEN",
            "box.secret_path.TOKEN", "agent.claude.secret_path.TOKEN",
        ):
            assert is_path_valued_key(spelling), spelling


# --------------------------------------------------------------------------- #
# 6. The ``cli_set`` column — the categories table's guard cell
# --------------------------------------------------------------------------- #

class TestCliSetColumnConformance:
    """The ``categories:`` ``cli_set`` cell, pinned directly rather than by proxy."""

    def test_the_cli_set_cell_partitions_exactly_the_nine_families(self):
        """The guard both kinemata mechanisms read is present, boolean, and 7/2."""
        # ⚑ THE TWO CONSUMERS LIVE OUTSIDE THIS FILE (``kinemata.toml``): the four
        # ``[[shape]]`` rules on the ``categories`` registry and the ``bind-shaped``
        # parity view both guard on this cell's VALUE. A row silently losing it
        # would red THERE, naming ``TERMINAL_CATEGORY_TAILS`` — never the cell.
        # ⚑ LITERAL SETS, DELIBERATELY: the code-side agreement is the parity
        # view's claim, so deriving the seven from the code here would make this
        # a second carrier of that check rather than a pin on the cell itself.
        cats = manifest_doc()["categories"]
        fams = {name: row for name, row in cats.items() if isinstance(row, dict)}
        # The two non-family records are not mappings, so no guard arm can sweep
        # them in — and a third one would land here, not slip past.
        assert set(cats) - set(fams) == {"scopes", "scopes_spec"}, sorted(set(cats))
        untyped = {
            name for name, row in fams.items()
            if type(row.get("cli_set")) is not bool
        }
        assert not untyped, (
            f"categories rows with no boolean cli_set cell: {sorted(untyped)}"
        )
        assert {n for n, r in fams.items() if r["cli_set"] is False} == {
            "masks", "bindings.ro", "bindings.rw", "caches",
            "seeded", "common", "synced",
        }
        assert {n for n, r in fams.items() if r["cli_set"] is True} == {
            "env", "secret_path",
        }
