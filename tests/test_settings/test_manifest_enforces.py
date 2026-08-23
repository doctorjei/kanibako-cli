"""MANIFEST ENFORCEMENT — the code asserted against the shipped keyspace REGISTRY.

⚑⚑ THE DIRECTION IS **MANIFEST → CODE**, AND IT IS THE OPPOSITE OF THIS FILE'S SIBLING.
``test_manifest_conformance.py`` asks *does the manifest still DESCRIBE the code?* — it
reads a code literal and checks the registry copied it faithfully.  This file asks the
other question: *does the code still OBEY the manifest?*  Here the manifest is the
statement of policy and the code is the thing on trial.

🛑 DO NOT MERGE THE TWO FILES.  They share a data source and nothing else.  A finding in
the sibling means "somebody edited code and forgot the registry"; a finding here means
"the shipped registry promises the user something the code does not do", which is a
different defect with a different owner and a different fix.  Collapsing them would make
every future reader guess which arrow a given case is asserting.

⚑ WHAT IS HERE TODAY — TWO BLOCKS, one section each.

``policy.seed_whitelists`` (§§1-3).  Measured 2026-08-22, that block had ZERO readers —
nothing in ``src/`` or ``tests/`` loaded it — while the LIVE carrier of the same shape is
:data:`kanibako.launch.templates.SCOPE_WHITELISTS`, which is what ``_check_whitelist``
actually reads.  Two carriers of one shape and no parity test is the class this file
exists to close (P10: whichever copy a reader finds first becomes their truth).  The
manifest ships in the wheel as release authority, so its copy is the one a user is
entitled to believe.

``categories`` (§4) — THE FAMILY HALF OF KEY-SET CONFORMANCE.  Its sibling
``test_manifest_conformance.py`` pins the 97 SCALAR keys both ways and says in its own
docstring (``:27-32``) that the family half is deliberately absent.  §4 closes it in the
CODE ← MANIFEST direction: the nine category families the manifest declares, at every
scope reachable by composing the manifest's own expansion rules, are exactly what
``key_validity`` accepts.  ⚑ Of the three reasons that docstring gives for the absence,
one is STALE, one does not reach this arm, and one is real; §4's header answers each.

⚑⚑ EXEMPTIONS ARE A NAMED TABLE WITH A STATED REASON, NEVER A SILENT SKIP — the same
discipline the sibling file holds to.  Every field of the block is classified in
:data:`SCOPE_ROW_FIELDS` or :data:`POLICY_FIELDS`, and the shape cases assert those
tables TOGETHER account for every field the manifest actually carries.  A new field, a
new scope or a new allow entry is therefore RED until somebody classifies it; that
exhaustiveness, not any individual case, is what stops this file rotting into a sample.

Indent note: 4 spaces, matching every sibling in ``tests/test_settings/`` (house style
is 2, but this directory is the exception).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kanibako.errors import TemplateScopeError
from kanibako.launch.templates import SCOPE_WHITELISTS, _check_whitelist
from kanibako.settings.keyspace_manifest import manifest_doc
from kanibako.settings.settings_categories import _DELIVERY
from kanibako.settings.settings_keyspace import TERMINAL_CATEGORY_TAILS, key_validity


# --------------------------------------------------------------------------- #
# The block, and the one notation it writes in
# --------------------------------------------------------------------------- #


def _block() -> dict:
    """The manifest's ``policy.seed_whitelists`` block (a fresh copy — P8)."""
    return manifest_doc()["policy"]["seed_whitelists"]


def _undirect(entry: str) -> str:
    """Strip the manifest's TRAILING-SLASH directory notation off a whitelist entry.

    The manifest writes ``home/``; the code carries ``home``.  That is a notation
    difference, not drift: ``_check_whitelist`` matches ``posix == a`` OR
    ``posix.startswith(f"{a}/")``, i.e. the code appends the separator itself when it
    tests a prefix, so the slash is the manifest SAYING "directory prefix" rather than
    part of the name.

    ⚑ The strip is not a licence to normalise anything into agreement.  On the ALLOW
    side the notation is UNIFORM — every entry ends with ``/`` — and
    :meth:`TestAllowNotation.test_every_allow_entry_is_written_in_directory_notation`
    pins that, so an entry naming a FILE reds here instead of being quietly rewritten
    into a directory.  On the DENY side the notation is deliberately MIXED (``box.yaml``
    is a file, ``auth/`` is a directory) and the slash is what distinguishes them.
    """
    return entry[:-1] if entry.endswith("/") else entry


#: A store-relative entry no scope allows, used to exercise the block's ``default: deny``.
#: ⚑ Spelled to be OBVIOUSLY foreign rather than plausible — a probe that could ever
#: become a real store entry would turn this case into a time bomb.
_UNLISTED_PROBE = "kanibako-enforcement-probe-not-a-store-entry"

#: The fields a SCOPE row carries, each mapped to HOW this file enforces it.  ⚑ This is
#: the classification table, not a description: the shape case asserts every scope row's
#: field set is exactly these keys, so a new column lands RED until it is classified.
SCOPE_ROW_FIELDS: dict[str, str] = {
    "allow": (
        "PARITY, both directions, against launch.templates.SCOPE_WHITELISTS — the live "
        "carrier _check_whitelist reads. This is the case the file was written for"
    ),
    "deny": (
        "REFUSAL, per entry, through _check_whitelist. There is no code table to compare "
        "to — the live whitelist is an ALLOW list and deny-by-default never names a "
        "denied entry — so the checkable claim is that each named row IS in fact refused"
    ),
    "root": (
        "SHAPE ONLY, and the reason is structural: every root is a PLACEHOLDER "
        "(`<box_dir>`, `agents/<agent>`, `<workset_path>`) standing for a path the "
        "caller passes as copy_tree's `dest_root` at each call site. An oracle would "
        "have to resolve the placeholder, i.e. be a second resolver (P2/P4)"
    ),
}

#: The block's non-scope POLICY fields, likewise classified.  Neither is an exemption in
#: the sense of "unchecked" — each has a case below; what they are exempt from is the
#: SET-PARITY treatment, because neither carries a table to compare.
POLICY_FIELDS: dict[str, str] = {
    "default": (
        "the deny-by-default rule itself. Enforced BEHAVIOURALLY: an entry no scope "
        "lists must be refused by _check_whitelist in every scope"
    ),
    "traversal_defences": (
        "declares that the traversal defences live in CODE, not in this table (R-24's "
        "split), so there is nothing here to enforce parity against. The code carriers "
        "are copy_tree's symlink refusals and _assert_contained, exercised by "
        "tests/test_launch/test_templates.py::TestCopierEnforcement — asserted THERE, "
        "and re-asserting them here would be the second copy P10 forbids"
    ),
}


# --------------------------------------------------------------------------- #
# 1. The shape of the block — the exhaustiveness that makes the rest binding
# --------------------------------------------------------------------------- #

class TestBlockShape:
    """Every field of the block is classified, and every scope row has the same shape."""

    def test_the_block_exists_and_is_a_mapping(self):
        """Anti-vacuity: a renamed or deleted block must red HERE, not vanish."""
        policy = manifest_doc()["policy"]
        assert "seed_whitelists" in policy, (
            "the manifest no longer carries policy.seed_whitelists — this whole file is "
            "asserting against a block that moved"
        )
        assert isinstance(_block(), dict)

    def test_every_block_field_is_a_scope_or_a_classified_policy_field(self):
        """⚑ LOAD-BEARING.  scopes ∪ POLICY_FIELDS == the block's fields, both ways.

        A new policy field lands here as unclassified, which is the correct outcome:
        somebody must decide whether it has a code oracle and say so in
        :data:`POLICY_FIELDS`.  A DELETED one lands here too, so the table cannot go
        stale in the other direction.
        """
        fields = set(_block())
        unclassified = fields - set(SCOPE_WHITELISTS) - set(POLICY_FIELDS)
        assert not unclassified, (
            f"policy.seed_whitelists carries fields this file neither pins nor names: "
            f"{sorted(unclassified)} — add each to POLICY_FIELDS (with a reason) or "
            f"treat it as a scope"
        )
        stale = set(POLICY_FIELDS) - fields
        assert not stale, (
            f"POLICY_FIELDS names fields the manifest no longer carries: {sorted(stale)}"
        )

    @pytest.mark.parametrize("scope", sorted(SCOPE_WHITELISTS))
    def test_every_scope_row_carries_exactly_the_classified_fields(self, scope):
        """A new column on a scope row is RED until :data:`SCOPE_ROW_FIELDS` names it."""
        row = _block()[scope]
        assert set(row) == set(SCOPE_ROW_FIELDS), (
            f"{scope}: manifest row fields {sorted(row)} vs classified "
            f"{sorted(SCOPE_ROW_FIELDS)}"
        )

    @pytest.mark.parametrize("scope", sorted(SCOPE_WHITELISTS))
    def test_the_root_column_is_a_placeholder(self, scope):
        """The stated reason for the ``root`` classification, asserted rather than assumed.

        Its exemption from parity rests on the value being a placeholder; if a root is
        ever written as a REAL path, the reason evaporates and this reds.
        """
        root = str(_block()[scope]["root"])
        assert "<" in root and ">" in root, (
            f"{scope}: root {root!r} is no longer a placeholder — SCOPE_ROW_FIELDS' "
            f"reason for leaving this column unpinned no longer holds"
        )


# --------------------------------------------------------------------------- #
# 2. The parity case — the manifest's allow lists ARE SCOPE_WHITELISTS
# --------------------------------------------------------------------------- #

class TestScopeParity:
    """The set of SCOPES the manifest governs is the set the code implements."""

    def test_the_manifest_scopes_are_the_code_scopes(self):
        """Both directions, named.  A scope in one carrier and not the other is a hole.

        A manifest-only scope is a policy nothing enforces; a code-only scope is a
        whitelist the release authority never declared.  Neither is acceptable and the
        message says which happened.
        """
        manifest = set(_block()) - set(POLICY_FIELDS)
        code = set(SCOPE_WHITELISTS)
        assert manifest == code, (
            f"seed-whitelist SCOPE drift — in the manifest not in "
            f"launch.templates.SCOPE_WHITELISTS: {sorted(manifest - code)}; in the code "
            f"not in the manifest: {sorted(code - manifest)}"
        )


class TestAllowNotation:
    """The notation :func:`_undirect` strips, pinned so the strip cannot hide a change."""

    @pytest.mark.parametrize("scope", sorted(SCOPE_WHITELISTS))
    def test_every_allow_entry_is_written_in_directory_notation(self, scope):
        odd = [e for e in _block()[scope]["allow"] if not str(e).endswith("/")]
        assert not odd, (
            f"{scope}: allow entries {odd} are not written in the block's trailing-slash "
            f"DIRECTORY notation — _undirect() would normalise them silently, so decide "
            f"what a non-directory allow entry means before adding one"
        )


class TestAllowListParity:
    """⚑ THE CASE THIS FILE WAS WRITTEN FOR: the two carriers of one shape are equal.

    Set equality, EXHAUSTIVE IN BOTH DIRECTIONS.  A manifest entry with no code
    counterpart is a permission the release authority grants and the copier refuses; a
    code entry the manifest omits is a permission the copier grants and no one declared.
    The message names which side each difference is on, because a bare ``set == set``
    over five strings says nothing actionable at the moment it fires.

    ⚑ ORDER IS DELIBERATELY NOT ASSERTED.  Matching is order-independent
    (``_check_whitelist`` tests membership with ``any(...)``), so pinning the sequence
    would file a false finding on a harmless reorder — P13: assert the rule, not the
    inventory.
    """

    @pytest.mark.parametrize("scope", sorted(SCOPE_WHITELISTS))
    def test_the_manifest_allow_list_is_the_code_whitelist(self, scope):
        manifest = {_undirect(str(e)) for e in _block()[scope]["allow"]}
        code = set(SCOPE_WHITELISTS[scope])
        assert manifest == code, (
            f"{scope} seed-whitelist drift — allowed by the manifest but NOT by "
            f"launch.templates.SCOPE_WHITELISTS: {sorted(manifest - code)}; allowed by "
            f"the code but NOT declared in the manifest: {sorted(code - manifest)}"
        )

    def test_the_corpus_is_not_empty(self):
        """Anti-vacuity: a block that parsed to empty lists would pass every case above."""
        assert sum(len(_block()[s]["allow"]) for s in SCOPE_WHITELISTS) == 7


# --------------------------------------------------------------------------- #
# 3. The live predicate — allow is ACCEPTED, deny is REFUSED, the rest is DENIED
# --------------------------------------------------------------------------- #
#
# ⚑ These run the manifest's own strings through ``_check_whitelist``, the function the
# copier calls.  Set parity above proves the two TABLES agree; this proves the table the
# code holds is the one the code OBEYS — the second half of "the code obeys the manifest",
# and the half a table-only comparison cannot see.


def _allow_cases() -> list[tuple[str, str]]:
    return [
        (scope, str(entry))
        for scope in sorted(SCOPE_WHITELISTS)
        for entry in _block()[scope]["allow"]
    ]


def _deny_cases() -> list[tuple[str, str]]:
    return [
        (scope, str(entry))
        for scope in sorted(SCOPE_WHITELISTS)
        for entry in _block()[scope]["deny"]
    ]


class TestThePredicateHonoursTheBlock:
    """``_check_whitelist`` accepts what the manifest allows and refuses what it denies."""

    @pytest.mark.parametrize(("scope", "entry"), _allow_cases())
    def test_an_allowed_entry_is_accepted(self, scope, entry):
        """The ANTI-VACUITY half of the refusal cases below.

        Deny-by-default means a refusal case passes for any string that is not allowed —
        including a typo.  Showing the same predicate ACCEPTS the allowed entries is what
        proves it discriminates rather than refusing everything put to it.  Both the bare
        entry and a file under it are checked: the two arms of the ``==`` / prefix match.
        """
        rel = _undirect(entry)
        _check_whitelist(Path(rel), scope)
        _check_whitelist(Path(rel) / "probe.md", scope)

    @pytest.mark.parametrize(("scope", "entry"), _deny_cases())
    def test_a_denied_entry_is_refused(self, scope, entry):
        """Every ``deny:`` row, as the manifest spells it, is refused by the copier.

        ⚑ THIS IS WHAT THE ``deny:`` BLOCK IS WORTH.  It is documentation of WHY certain
        entries must never be seedable — nothing in ``src/`` reads it, and the live
        whitelist is an ALLOW list, so deny-by-default never names these files.  That
        makes the table unenforceable as data and perfectly enforceable as a CLAIM: each
        row asserts "this is denied", and that is exactly what is checked here.
        """
        with pytest.raises(TemplateScopeError):
            _check_whitelist(Path(_undirect(entry)), scope)

    @pytest.mark.parametrize("scope", sorted(SCOPE_WHITELISTS))
    def test_an_unlisted_entry_is_refused_in_every_scope(self, scope):
        """``default: deny`` — the block's own first field, asserted as behaviour."""
        assert _block()["default"] == "deny"
        with pytest.raises(TemplateScopeError):
            _check_whitelist(Path(_UNLISTED_PROBE), scope)

    def test_the_deny_corpus_is_the_measured_size(self):
        """Anti-vacuity: an emptied ``deny:`` map would silently skip the case above."""
        assert len(_deny_cases()) == 12
        empty = [s for s in SCOPE_WHITELISTS if not _block()[s]["deny"]]
        assert not empty, f"scopes with no deny rows at all: {empty}"


#: The scopes whose ``deny:`` block names that tier's SETTINGS FILE, mapped to the code
#: constant that spells it.  ⚑ Derived from the rows' own stated reason — each says
#: *"correctness — = meta.<scope>.settings"* — so this is the rule, not an inventory.
#: ⚑ ``registry.yaml`` (workset) is deliberately ABSENT: it has no named constant, only a
#: literal built inside ``project/workset_registry.py``, so there is nothing to pin it to.
_SETTINGS_FILE_DENIALS: dict[str, str] = {
    "box": "BOX_META_FILE",
    "workset": "WORKSET_META_FILE",
    "agent": "AGENT_META_FILE",
}


class TestTheSettingsFileDenialsNameRealFiles:
    """The ``deny:`` rows that name a settings file name the CODE'S settings file.

    ⚑ WHY THIS EXISTS.  Deny-by-default refuses any string that is not allowed, so the
    refusal cases above pass just as happily for a MISSPELLED deny row — they cannot tell
    ``box.yaml`` from ``bx.yaml``.  This is the case that can: the three tiers each have a
    named filename constant, and a deny row claiming to name one is only true if it does.
    It is what makes the ``settings.yaml`` → ``box.yaml``/``workset.yaml``/``agent.yaml``
    rename ([R140]) a checked claim rather than a hopeful one.
    """

    @pytest.mark.parametrize("scope", sorted(_SETTINGS_FILE_DENIALS))
    def test_the_deny_row_is_the_code_filename(self, scope):
        from kanibako.settings import config

        want = getattr(config, _SETTINGS_FILE_DENIALS[scope])
        assert want in _block()[scope]["deny"], (
            f"{scope}: the manifest's deny rows {sorted(_block()[scope]['deny'])} do not "
            f"name the tier's settings file {want!r} (config.{_SETTINGS_FILE_DENIALS[scope]})"
        )


class TestTraversalDefencesStayInCode:
    """The one field that declares its own absence from the table."""

    def test_the_block_defers_the_defences_to_code(self):
        """R-24's split, pinned: if this ever becomes a TABLE, it needs parity cases.

        The value ``code`` is the manifest saying "no data here" — which is why
        :data:`POLICY_FIELDS` classifies it as having nothing to compare.  Any other
        value means the block grew a traversal table, and that table would need its own
        enforcement rather than inheriting this one's silence.
        """
        assert _block()["traversal_defences"] == "code"


# --------------------------------------------------------------------------- #
# 4. categories — THE FAMILY HALF OF KEY-SET CONFORMANCE
# --------------------------------------------------------------------------- #
#
# ⚑⚑ WHAT THIS SECTION ASSERTS, IN ONE SENTENCE: the code accepts EXACTLY the family
# surface the manifest declares — the nine ``categories:`` families, at every scope the
# manifest's own expansion rules put them at, and nowhere else.
#
# ⚑ ITS SIBLING'S THREE REASONS FOR NOT DOING THIS, ANSWERED (measured 2026-08-23).
# ``test_manifest_conformance.py:27-32`` records the family half as deliberately absent
# for three reasons:
#   1. "no single code object holds the families" — STALE.
#      :data:`kanibako.settings.settings_categories._DELIVERY` holds all nine.
#      🛑 It is not treated here as a DECLARATION of keyspace membership: its subject is
#      DELIVERY KIND (mount / copy / env), and that it happens to be family-complete is a
#      coincidence somebody could break without noticing.  That is precisely why
#      :class:`TestTheFamilySetIsTheDeliveryTable` asserts the two sets EQUAL rather than
#      reading either as authority.  ``BIND_CATEGORIES`` cannot serve: it holds SIX, and
#      ``env`` / ``secret_path`` exist in the validator only as inline literals.
#   2. ``plugin_contributed.conformance_must_consult_descriptors`` making the arm
#      environment-dependent — TRUE, BUT NOT OF THIS ARM.  Measured both with
#      ``agent_leaves=None`` and with the plugin union: the family fail-set is EMPTY and
#      IDENTICAL, because ``_agent_tail_reason`` dispatches to ``_category_reason`` on
#      ``_looks_like_category`` BEFORE it ever consults ``leaves``.  Descriptor
#      availability governs the LEAF arm, which is not what this section tests, so no
#      descriptor pinning is built here.
#   3. "``agent.active`` is not a code spelling" — REAL, and resolved by
#      :data:`_SCOPE_TOKEN_SPELLINGS` below rather than by touching the manifest.
#
# ⚑ RELATION TO ``test_manifest_conformance.py:887-890``.  That case asserts
# ``BIND_CATEGORIES <= declared_categories`` — a SUBSET, over 6 of the 9, comparing names
# only.  This section supersedes it in coverage (equality, all nine, plus the acceptance
# surface) and does not replace it: the sibling's case belongs to the manifest ← code
# arrow and is the right assertion to find in that file.

#: Agent discriminators ``key_validity`` is told are real.  It takes the set by
#: INJECTION (purity), so this is a fixed probe rather than a discovery — the same
#: choice ``test_manifest_conformance.PROBE_AGENTS`` makes, and what keeps this section
#: independent of which plugins happen to be installed on the runner.
PROBE_AGENTS: frozenset[str] = frozenset({"claude", "codex", "goose"})

#: The manifest's ``categories.scopes`` tokens, each mapped to the scope prefix(es)
#: ``key_validity`` actually spells.  Identity for four of the five.
#:
#: ⚑⚑ ``agent.active`` IS THE ONE THAT MOVES, AND IT IS A NOTATION DIFFERENCE, NOT DRIFT.
#: The SPEC spells this cascade level ``agent.<active>`` and says what it denotes:
#: *"the agent tier splits into the ``agent.default`` all-agents layer and the more
#: specific ``agent.<active>`` layer … written ``agent.<agent>`` elsewhere for the
#: generic per-agent contract shape"* (``settings-keyspace-1.8.0.md:546-548``).  So the
#: token names a level SELECTED BY AGENT NAME; ``active`` is the placeholder, never a
#: segment a user writes.  The code has no reason to know the word, and
#: :meth:`TestTheSurfaceBoundary.test_the_unmapped_scope_token_is_refused` pins that it
#: stays REFUSED — the mapping cannot decay into permissiveness.
#:
#: 🛑 The manifest is NOT edited to say ``agent.<active>``: it is shipped release data
#: and a respell is not this file's call.  The mapping lives on the TEST side, which is
#: where a notation bridge belongs.
_SCOPE_TOKEN_SPELLINGS: dict[str, tuple[str, ...]] = {
    "system": ("system",),
    "agent.default": ("agent.default",),
    "agent.active": tuple(f"agent.{a}" for a in sorted(PROBE_AGENTS)),
    "workset": ("workset",),
    "box": ("box",),
}

#: The ``categories:`` fields that are NOT a family, each with the reason.  ⚑ A
#: classification table, not a skip list: :class:`TestTheCategoriesBlockShape` asserts it
#: and the family set TOGETHER account for every field, both directions, so a new field
#: is RED until somebody decides which it is.
_NON_FAMILY_FIELDS: dict[str, str] = {
    "scopes": (
        "the SETTABLE-cascade list the families are declared over — an INPUT to the "
        "surface this section composes, not a member of it"
    ),
    "scopes_spec": "the spec citation for the line above; prose, nothing to enforce",
}

#: The manifest's RO mirror prefix (``policy.parametric_expansion`` bullet 3, spec §2b).
#: A category-bearing prefix that ``categories.scopes`` does NOT list — which is the
#: whole reason the surface has to be COMPOSED rather than read off that one line.
_MIRROR_PREFIX = "meta.box.agent"

#: A token that is not a family and must never become one.  Spelled to be obviously
#: foreign, for the same reason as :data:`_UNLISTED_PROBE`.
_NOT_A_FAMILY = "kanibako_enforcement_probe_not_a_category"

#: A legal value for a family's ``parametric:`` placeholder segment.  Both families that
#: take one spell it ``<VAR>``, and ``_category_reason`` checks it against
#: ``_VAR_RE``, so the probe is written as a legal environment variable name.
_PROBE_VAR = "KANIBAKO_ENFORCEMENT_PROBE"

#: The composition rules, keyed by the arm of this section that implements each, valued
#: by a substring that identifies the manifest bullet it comes from.  ⚑ The bullets are
#: PROSE (``policy.parametric_expansion`` is three English sentences in a YAML list), so
#: the classification is by DISCRIMINATING SUBSTRING — stable under reflow, and matched
#: one-to-one in both directions by :class:`TestTheCompositionRulesAreClassified`, so a
#: FOURTH bullet lands RED and a DELETED arm lands red too.
_COMPOSITION_RULES: dict[str, str] = {
    "the cascade cross-product — every family at every categories.scopes token": (
        "every category in `categories` exists at every scope"
    ),
    "the agent-tier expansion — what makes `agent.active` a set of real prefixes": (
        "ALSO exists as agent.<agent>.<key>"
    ),
    "the RO mirror arm — meta.box.agent carries the whole agent subtree": (
        "MIRRORS (RO)"
    ),
}


def _families() -> list[str]:
    """The manifest's nine ``categories:`` family names.

    ⚑ Derived STRUCTURALLY — a family row is the mapping that describes a family; the
    two non-family fields are a list and a string.  The name-keyed
    :data:`_NON_FAMILY_FIELDS` is the second lock, not the first, and the shape case
    asserts the two agree.
    """
    cats = manifest_doc()["categories"]
    return sorted(k for k, v in cats.items() if isinstance(v, dict))


def _cascade_prefixes() -> list[str]:
    """The ``categories.scopes`` tokens, spelled the way the code spells them."""
    scopes = manifest_doc()["categories"]["scopes"]
    return [s for token in scopes for s in _SCOPE_TOKEN_SPELLINGS[token]]


def _surface_prefixes() -> list[str]:
    """Every prefix that may carry a category, per the composed rules.

    ⚑ 16 = (5 tokens, ``agent.active`` expanding to 3 ⇒ 7 cascade prefixes) + the 1
    mirror prefix, the whole DOUBLED by the ``pref.`` recursion.
    """
    base = [*_cascade_prefixes(), _MIRROR_PREFIX]
    return [*base, *(f"pref.{p}" for p in base)]


def _parametric_segments(family: str) -> list[str]:
    """The placeholder segments *family* takes AFTER its token, per the manifest.

    ⚑ Read from the row's own ``parametric:`` field (``["VAR"]`` for ``env`` and
    ``secret_path``, absent for the other seven), so :func:`_spell` never guesses a key
    shape.  🛑 NOT read off :data:`_DELIVERY`: delivery kind does not determine key
    shape — ``masks`` and ``secret_path`` are both MOUNT and their keys differ.
    """
    return list(manifest_doc()["categories"][family].get("parametric", []))


def _bind_shaped_families() -> list[str]:
    """The families whose key ENDS at its own token — the manifest's NON-parametric rows.

    ⚑ THE ONE DERIVATION OF THIS SET, used by every case that needs it.  Structural:
    a family is bind-shaped iff the manifest declares it no ``parametric:`` segments.
    :meth:`TestTheCategoriesBlockShape
    .test_the_non_parametric_families_are_the_code_terminal_tails` asserts the result
    equals ``TERMINAL_CATEGORY_TAILS``, so the set is cross-checked against the code
    rather than trusted, and no case below has to spell seven names (P13).
    """
    return [f for f in _families() if not _parametric_segments(f)]


def _parametric_families() -> list[str]:
    """The complement: the families that take a ``<VAR>`` segment after their token."""
    return [f for f in _families() if _parametric_segments(f)]


def _spell(prefix: str, family: str) -> str:
    """The KEY a *prefix* and a *family* name.

    Seven families are TERMINAL — the key ENDS at the family token.  The two the
    manifest marks ``parametric`` take one probe segment per placeholder (spec §2a).
    """
    tail = "".join(f".{_PROBE_VAR}" for _ in _parametric_segments(family))
    return f"{prefix}.{family}{tail}"


def _surface_pairs() -> list[tuple[str, str]]:
    return [(p, f) for p in _surface_prefixes() for f in _families()]


def _short_of_key_cases() -> list[tuple[str, str]]:
    """Every surface key that HAS a legal-looking shorter form, paired with that form.

    ⚑ Derived, not listed: drop the last segment and keep the result only if it is
    still longer than the prefix.  The five single-token terminal families have nothing
    to stop short of and so contribute no case — which is a fact about their SHAPE, not
    an exclusion somebody chose.
    """
    cases = []
    for prefix, family in _surface_pairs():
        short = _spell(prefix, family).rsplit(".", 1)[0]
        if short != prefix:
            cases.append((prefix, short))
    return cases


class TestTheFamilySetIsTheDeliveryTable:
    """⚑ THE PARITY CASE: the manifest's nine families ARE the code's nine."""

    def test_the_manifest_families_are_the_code_families(self):
        """Set equality, EXHAUSTIVE IN BOTH DIRECTIONS, each side named in the message.

        A manifest-only family is a delivery category the release authority declares and
        nothing delivers; a code-only family is a route into the box that the shipped
        registry never declared — and the single-route invariant is exactly the claim
        that no such route exists.  A bare ``set == set`` over nine strings says nothing
        actionable at the moment it fires.
        """
        manifest = set(_families())
        code = set(_DELIVERY)
        assert manifest == code, (
            f"category FAMILY drift — declared in the manifest's categories: block but "
            f"absent from settings_categories._DELIVERY: {sorted(manifest - code)}; "
            f"carried by _DELIVERY but not declared in the manifest: "
            f"{sorted(code - manifest)}"
        )

    def test_the_family_corpus_is_the_measured_size(self):
        """Anti-vacuity: two empty sets are equal, and would pass every case here."""
        assert len(_families()) == 9


class TestTheCategoriesBlockShape:
    """Every field of ``categories:`` is a family or a classified non-family."""

    def test_the_block_exists_and_is_a_mapping(self):
        """Anti-vacuity: a renamed or deleted block must red HERE, not vanish."""
        assert isinstance(manifest_doc().get("categories"), dict)

    def test_every_categories_field_is_a_family_or_classified(self):
        """⚑ LOAD-BEARING.  families ∪ _NON_FAMILY_FIELDS == the block's fields, both ways.

        A new field lands here unclassified, which is the correct outcome: somebody must
        decide whether it is a tenth family (and then whether the code delivers it) or a
        policy field, and say so.  A DELETED one lands here too, so the table cannot rot
        in the other direction.
        """
        fields = set(manifest_doc()["categories"])
        unclassified = fields - set(_families()) - set(_NON_FAMILY_FIELDS)
        assert not unclassified, (
            f"categories: carries fields this section neither treats as a family nor "
            f"names: {sorted(unclassified)} — add each to _NON_FAMILY_FIELDS (with a "
            f"reason) or let it be a family"
        )
        stale = set(_NON_FAMILY_FIELDS) - fields
        assert not stale, (
            f"_NON_FAMILY_FIELDS names fields categories: no longer carries: "
            f"{sorted(stale)}"
        )

    def test_the_non_parametric_families_are_the_code_terminal_tails(self):
        """⚑ THE SECOND PARITY, and what licenses :func:`_spell` to trust the manifest.

        The manifest's block opens *"TWO SHAPES, do not conflate"*: a family is either
        ``parametric`` (takes a ``<VAR>``) or the key ENDS at its token.  The code says
        the same thing in :data:`~kanibako.settings.settings_keyspace
        .TERMINAL_CATEGORY_TAILS`.  Asserting the two agree, both directions, is what
        stops :func:`_spell` from quietly writing a key of the wrong SHAPE and the
        surface cases from passing on strings nobody meant.

        ⚑ WHY THE DISCRIMINATOR IS ``parametric:`` AND NOT THE ``terminal_key:`` /
        ``dest_keyed:`` FLAGS — WHICH IS NO LONGER OBVIOUS.  It was once forced:
        ``masks`` carried neither flag while its six bind-shaped siblings carried
        both, so absence of a positive marker could not discriminate anything.  Jei
        closed that gap on 2026-08-23 and all seven now carry both, which makes the
        flags LOOK like a simpler split.  They are not the one to use.  ``parametric:``
        is the field :func:`_parametric_segments` already reads to SPELL a key, so
        discriminating on it keeps one field deciding one thing and keeps this
        cross-check independent of the flags — which is what lets
        :class:`TestTheBindShapedRowsCarryTheKeyShapeFlags` pin the flags at all.
        """
        manifest = set(_bind_shaped_families())
        code = {".".join(t) for t in TERMINAL_CATEGORY_TAILS}
        assert manifest == code, (
            f"category KEY-SHAPE drift — the manifest declares no parametric segments "
            f"for {sorted(manifest - code)}, but settings_keyspace."
            f"TERMINAL_CATEGORY_TAILS does not end a key there; and it ends a key at "
            f"{sorted(code - manifest)}, which the manifest marks parametric"
        )


#: The two fields a bind-shaped ``categories:`` row carries, each mapped to the claim it
#: makes ABOUT THE KEY.  ⚑ Neither is a delivery property: ``masks`` and ``secret_path``
#: are both MOUNT in :data:`_DELIVERY` and only one of them is either of these.
_KEY_SHAPE_FLAGS: dict[str, str] = {
    "terminal_key": "the key ENDS at the family token — what follows it is DATA",
    "dest_keyed": "the value is one map whose inner keys are destinations, not segments",
}


class TestTheBindShapedRowsCarryTheKeyShapeFlags:
    """⚑ The key-shape flags are UNIFORM across the bind-shaped rows — and ABSENT from
    the parametric pair, which is the half that keeps the two classes distinguishable.

    WHY THIS EXISTS.  Until 2026-08-23 ``masks`` carried neither ``terminal_key:`` nor
    ``dest_keyed:`` while its six bind-shaped siblings carried both, even though the
    block's own prose called it dest-keyed and the code listed it in
    ``TERMINAL_CATEGORY_TAILS`` — the row was simply written before the others and never
    caught up.  Jei brought it into line, and nothing asserted the resulting uniformity,
    so the EIGHTH bind-shaped row would have reintroduced exactly the same silent
    inconsistency.  This is the case that reds instead.

    ⚑⚑ THE SET IS DERIVED FROM ``parametric:`` (:func:`_bind_shaped_families`),
    DELIBERATELY NOT FROM THE FLAGS THIS CASE ASSERTS.  That is not a stylistic
    preference, it is the difference between a test and a tautology: derive
    "bind-shaped" from ``terminal_key: true`` and a row that LOSES the flag simply
    leaves the set, so the case that was meant to catch the loss passes over a smaller
    corpus instead.  A pin may never read its own subject.

    🛑 THE PARAMETRIC PAIR IS ASSERTED, NOT EXCLUDED.  ``env`` and ``secret_path`` carry
    NEITHER flag today, and the absence is meaningful rather than an oversight: their
    keys do NOT end at the family token (``env.<VAR>`` — spec §2a), so ``terminal_key``
    would be a false claim; and their ``<VAR>`` is a KEYSPACE SEGMENT the validator
    parses, which is the precise opposite of what ``dest_keyed`` says about a bind map's
    inner keys.  So the rule is: the flags mark the bind-shaped class and mark nothing
    else, and either flag appearing on a parametric row is a row contradicting its own
    ``parametric:`` field.  An unasserted exclusion is where the next gap hides.
    """

    @pytest.mark.parametrize("family", _bind_shaped_families())
    def test_the_bind_shaped_row_carries_both_flags(self, family):
        """Both flags, ``true``, on every row the manifest declares non-parametric."""
        row = manifest_doc()["categories"][family]
        missing = {f: why for f, why in _KEY_SHAPE_FLAGS.items() if row.get(f) is not True}
        assert not missing, (
            f"the categories: row {family!r} is bind-shaped (no parametric: segments) "
            f"but does not declare {sorted(missing)} as true — each states something the "
            f"key shape needs: "
            + "; ".join(f"{f}: {why}" for f, why in sorted(missing.items()))
        )

    @pytest.mark.parametrize("family", _parametric_families())
    def test_the_parametric_row_carries_neither_flag(self, family):
        """The other half of the rule, so the two classes cannot converge silently."""
        row = manifest_doc()["categories"][family]
        present = sorted(f for f in _KEY_SHAPE_FLAGS if f in row)
        assert not present, (
            f"the categories: row {family!r} declares parametric: "
            f"{_parametric_segments(family)} — its key ends at a <VAR> SEGMENT, not at "
            f"the family token — yet it also carries {present}, which is what the "
            f"bind-shaped rows use to say the opposite"
        )

    def test_the_two_classes_partition_the_families(self):
        """Anti-vacuity: an empty parametrize list runs ZERO cases and stays green.

        Stated as the rule rather than as counts — the sizes are pinned by
        :meth:`TestTheFamilySetIsTheDeliveryTable.test_the_family_corpus_is_the_measured_size`
        and by the ``TERMINAL_CATEGORY_TAILS`` equality above.
        """
        bind, parametric = set(_bind_shaped_families()), set(_parametric_families())
        assert bind and parametric, (
            f"one of the two shape classes is EMPTY (bind-shaped {sorted(bind)}, "
            f"parametric {sorted(parametric)}) — its cases above ran on nothing"
        )
        assert not bind & parametric
        assert bind | parametric == set(_families())


class TestTheScopeTokenMapping:
    """The token bridge is exhaustive, so a new scope cannot slip through untranslated."""

    def test_every_scopes_token_is_mapped_and_every_mapping_is_used(self):
        """Both directions.  A new ``categories.scopes`` token is RED until translated.

        This is the case that makes the ``agent.active`` bridge safe.  An unmapped token
        would otherwise be silently DROPPED from the surface — the failure mode that
        does not go red, it goes green over less.
        """
        tokens = set(manifest_doc()["categories"]["scopes"])
        mapped = set(_SCOPE_TOKEN_SPELLINGS)
        assert tokens == mapped, (
            f"categories.scopes token drift — declared in the manifest but not "
            f"translated to a code spelling here: {sorted(tokens - mapped)}; translated "
            f"here but no longer declared: {sorted(mapped - tokens)}"
        )

    def test_the_mapped_agents_are_treated_as_real(self):
        """The mapping's own premise: each expansion of ``agent.active`` is a valid agent.

        If :data:`PROBE_AGENTS` and the injected ``valid_agents`` ever came apart, every
        acceptance case in this section would pass for the wrong reason — or fail for
        one.  Cheap to state, and it states which one the surface rests on.
        """
        for spelling in _SCOPE_TOKEN_SPELLINGS["agent.active"]:
            assert spelling.split(".", 1)[1] in PROBE_AGENTS


class TestTheCompositionRulesAreClassified:
    """⚑⚑ THE EXHAUSTIVENESS THAT MAKES THE SURFACE BINDING RATHER THAN A SAMPLE.

    ``categories.scopes`` IS NOT THE ACCEPTANCE SURFACE — it is the settable-cascade
    list.  Reading it literally and stopping there under-covers by the mirror arm and
    the ``pref.`` recursion.  The surface is COMPOSED, and a composition rule that this
    section stops implementing makes the surface SMALLER without making anything red.
    These cases are what closes that: each rule is tied to the manifest text that
    declares it, one-to-one in both directions.
    """

    def test_every_parametric_expansion_bullet_is_classified(self):
        """A fourth bullet is RED until an arm claims it; a dropped arm is red too."""
        bullets = manifest_doc()["policy"]["parametric_expansion"]
        for arm, discriminator in _COMPOSITION_RULES.items():
            hits = [b for b in bullets if discriminator in b]
            assert len(hits) == 1, (
                f"the composition rule implemented as {arm!r} matches "
                f"{len(hits)} of policy.parametric_expansion's bullets on "
                f"{discriminator!r} — it must match exactly one"
            )
        unclaimed = [
            b for b in bullets
            if not any(d in b for d in _COMPOSITION_RULES.values())
        ]
        assert not unclaimed, (
            f"policy.parametric_expansion declares expansion rules this section does "
            f"not compose into its surface: {unclaimed} — implement each as an arm and "
            f"add it to _COMPOSITION_RULES, or say why it generates no category keys"
        )

    def test_the_pref_recursion_is_declared_by_the_manifest(self):
        """The fourth rule, whose carrier is ``pref.filters`` rather than a bullet.

        ⚑ ``valid_key`` is what makes ``pref.<target>`` a key whenever ``<target>`` is —
        the manifest spells it *"target matches a declared key or a declared PARAMETRIC
        FAMILY"* — and it is why the surface below doubles.  🛑 VALIDITY IS NOT
        REQUESTABILITY: the same block's ``allowlist`` and ``forbidden_tiers`` filters
        answer whether a pref may actually be REQUESTED, and ``key_validity``'s own
        docstring draws that line (*"Whether it may be REQUESTED is a separate question
        the allowlist + forbidden tiers answer — this is only 'is it a key'"*).  Nothing
        here claims ``pref.meta.box.agent.caches`` is requestable; the CATEGORICAL
        forbidden tier says it is not.
        """
        assert "valid_key" in manifest_doc()["pref"]["filters"]


class TestTheComposedSurfaceIsAccepted:
    """Every (prefix, family) pair the composed rules generate is a KEY."""

    @pytest.mark.parametrize(("prefix", "family"), _surface_pairs())
    def test_the_pair_is_a_declared_key(self, prefix, family):
        key = _spell(prefix, family)
        reason = key_validity(key, valid_agents=PROBE_AGENTS)
        assert reason is None, (
            f"the manifest declares category {family!r} at scope {prefix!r}, but "
            f"key_validity REFUSES {key!r}: {reason}"
        )

    def test_the_surface_is_the_measured_size(self):
        """Anti-vacuity, and the case a DROPPED COMPOSITION ARM reds.

        16 prefixes × 9 families.  ⚑ P13 says pin the rule, not the inventory — the
        pairs themselves are DERIVED, and this pins only that the derivation still
        produces a surface of the measured size.  An arm quietly removed from
        :func:`_surface_prefixes` leaves every remaining pair passing; this is what
        notices.
        """
        assert len(_surface_prefixes()) == 16
        assert len(_surface_pairs()) == 144


class TestTheSurfaceBoundary:
    """⚑ THE OTHER HALF: what the code must REFUSE.

    An acceptance-only section is satisfied by a validator that accepts everything.
    Every case here is DERIVED FROM THE FAMILY LIST rather than written out, so a tenth
    family is refused-checked at every boundary the moment it is declared.
    """

    @pytest.mark.parametrize("family", _families())
    def test_the_unmapped_scope_token_is_refused(self, family):
        """🛑 THE GUARD ON :data:`_SCOPE_TOKEN_SPELLINGS`.

        ``agent.active`` is a MANIFEST NOTATION, not a segment a user may write, and the
        mapping above exists to translate it — not to bless it.  If the code ever began
        accepting the literal token, the mapping would have become permissive and this
        section would be asserting a surface wider than the keyspace.
        """
        key = _spell("agent.active", family)
        assert key_validity(key, valid_agents=PROBE_AGENTS) is not None, (
            f"{key!r} is ACCEPTED — 'active' is the manifest's placeholder for the "
            f"active agent's NAME (spec :546), never a legal agent segment"
        )

    @pytest.mark.parametrize("family", _families())
    def test_the_undiscriminated_agent_scope_is_refused(self, family):
        """The agent tier is DISCRIMINATED: a bare ``agent.<family>`` is not a key."""
        key = _spell("agent", family)
        assert key_validity(key, valid_agents=PROBE_AGENTS) is not None

    @pytest.mark.parametrize(
        "prefix", ["config", "meta.box", "meta.workset", "meta.agent.claude"],
    )
    @pytest.mark.parametrize("family", _families())
    def test_a_non_category_bearing_prefix_is_refused(self, prefix, family):
        """The surface stops where it stops — these prefixes carry NO family.

        ⚑ ``meta.box`` and ``meta.agent.<agent>`` are the load-bearing ones: the mirror
        arm is ``meta.box.agent`` SPECIFICALLY, and if the mirror leaked one segment up
        or sideways the acceptance cases could not tell.
        """
        key = _spell(prefix, family)
        assert key_validity(key, valid_agents=PROBE_AGENTS) is not None

    @pytest.mark.parametrize("prefix", _surface_prefixes())
    @pytest.mark.parametrize("family", _families())
    def test_one_segment_past_the_key_is_refused(self, prefix, family):
        """The families are TERMINAL (or VAR-terminal): what follows is DATA, not key.

        A destination inside a bind map, and the value under an ``env`` VAR, are values
        — the manifest's ``pref.filters.valid_key`` note says so in as many words
        (*"pref.<...>.bindings.rw.<anything> and pref.<...>.seeded.<anything> alike are
        INVALID — only the bare key is a target"*).  Without this, a validator that
        accepted any tail under a family would pass every acceptance case above.
        """
        key = f"{_spell(prefix, family)}.probe"
        assert key_validity(key, valid_agents=PROBE_AGENTS) is not None

    @pytest.mark.parametrize(("prefix", "short"), _short_of_key_cases())
    def test_a_key_stopping_short_of_the_family_is_refused(self, prefix, short):
        """The other side of the same rule: a key must reach its declared end.

        For the parametric families that is the family token with no ``<VAR>``; for
        ``bindings.{ro,rw}`` it is the arm-less ``bindings`` root (spec §2d: an ARM-LESS
        binding is not a declared key).  Both are REFUSED.
        """
        assert key_validity(short, valid_agents=PROBE_AGENTS) is not None

    @pytest.mark.parametrize("prefix", _surface_prefixes())
    def test_a_fabricated_family_is_refused_at_every_prefix(self, prefix):
        """§0: the keyspace is CLOSED.  Acceptance is by DECLARATION, not by shape."""
        key = f"{prefix}.{_NOT_A_FAMILY}"
        assert key_validity(key, valid_agents=PROBE_AGENTS) is not None

    def test_a_request_of_a_request_is_refused(self):
        """The ``pref.`` recursion terminates — it is not a repeatable prefix."""
        assert key_validity(
            "pref.pref.box.masks", valid_agents=PROBE_AGENTS,
        ) is not None


class TestMetaAssemblyIsNotACategoryBearingScope:
    """🛑 THE NAME COLLISION, PINNED SO NOBODY 'FIXES' IT INTO THE SURFACE.

    ``meta.assembly`` carries leaves called ``seeded``, ``synced``, ``env`` and
    ``bindings`` (:data:`~kanibako.settings.settings_keyspace
    .DECLARED_META_ASSEMBLY_LEAVES`) — the COLLAPSE OUTPUTS, scalar keys that merely
    share a name with a category family.  They are not family hits and ``meta.assembly``
    is not in :func:`_surface_prefixes`.  The discriminator is observable, which is what
    makes this a test rather than a comment: under a real category-bearing scope the
    VAR-parametric families take a ``<VAR>``; under ``meta.assembly`` the same spelling
    is REFUSED, because the leaf is scalar and the key ends there.
    """

    @pytest.mark.parametrize(
        "family", sorted(set(_families()) & {"seeded", "synced", "env", "bindings.ro"}),
    )
    def test_the_collapse_leaf_is_accepted_bare(self, family):
        leaf = family.split(".")[0]
        assert key_validity(
            f"meta.assembly.{leaf}", valid_agents=PROBE_AGENTS,
        ) is None

    def test_the_collapse_leaf_takes_no_category_tail(self):
        """``box.env.<VAR>`` is a key; ``meta.assembly.env.<VAR>`` is not."""
        assert key_validity(
            f"box.env.{_PROBE_VAR}", valid_agents=PROBE_AGENTS,
        ) is None
        assert key_validity(
            f"meta.assembly.env.{_PROBE_VAR}", valid_agents=PROBE_AGENTS,
        ) is not None
        assert key_validity(
            "meta.assembly.bindings.ro", valid_agents=PROBE_AGENTS,
        ) is not None
