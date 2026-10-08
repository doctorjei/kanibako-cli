"""SPEC ↔ DESCRIPTOR parity at §2d's per-node tier — the arrows the two existing files miss.

``test_manifest_conformance.py`` is CODE ← REGISTRY and ``test_manifest_enforces.py`` is
CODE ← MANIFEST; ``test_manifest_spec_parity.py`` is REGISTRY ↔ SPEC, and it pins
``TIER = "agent.default."`` alone.  None of the three reaches what a plugin's own
descriptor DECLARES at ``agent.<plugin>.*`` — since S3 A2 those rows land at
``agent.<node>`` and are declared by the plugin, not the manifest, so nothing compared
them with the spec that states them.  ``agent.claude.model`` is what that costs: §2d
says it has no plugin default, and ``claude-defaults.yaml`` may grow one without any
existing check noticing.  This file is the mechanical form of the rule.

⚑ THE CLASSIFICATION IS NOT RESTATED HERE.  Which §2d cell states a value a descriptor
is held to, which names a key only, and which states nothing at all is decided once, in
``kinemata_views.classify_spec_cell``; the ``agent-<node>-stated`` kinemata views and
this file both read that verdict, and neither restates its rules.  ``TestTheCellClassification``
pins the three classes it produces, so a cell that moves between them reds HERE.

⚑ THE DESCRIPTOR SIDE IS THE PRODUCTION WIRING, NOT A HAND-BUILT DICT.  Every value read
here comes from ``get_target(node)().setting_descriptors()`` / ``.default_envs()`` for a
plugin and from ``core_defaults.pseudo_tier_defaults()`` / ``env_default_categories()``
for the pseudo-agent — the producers the launch itself reads, reached through
``kinemata_views.agent_tier_defaults``.

⚑ SKIPS OFF-BOX, AND THAT MATTERS FOR HOW A CI GREEN IS READ.  The spec lives in the
canon (``~/canon/workbook/specs/``, or wherever ``KANI_CANON`` points), outside this repo
and absent from CI, so a green run in CI is NOT evidence that this parity holds — it is
evidence that the file did not run.  The pin holds only where the canon is mounted.
Precedent and the same disclosure: ``tests/test_keyspec_extract.py``.

⚑ SCOPE: THE VALUES EACH NODE STATES.  Keys are not compared: §2d ¶1 and §0 let a
plugin define its own ``agent.<agent>.*`` leaves (goose's ``provider``), so a descriptor
key §2d does not name is legal.  Nothing here asserts anything about prose,
realizations or provenance on either side — the ``access`` /
``model`` / ``endpoint`` realization cells, the ``bootstrap`` notes and the
dest-keyed ``[dest]`` entries are classified NOT EXPRESSIBLE and deliberately absent
from every comparison below.

Indent note: 4 spaces, matching every sibling in ``tests/test_settings/``.
"""

from __future__ import annotations

import copy
import importlib

import pytest

import kinemata_views
from kanibako.settings import agent_defaults

#: The nodes this file walks: the three shipped harness plugins and the one
#: pseudo-agent.  The ``default`` tier is NOT here — ``test_manifest_spec_parity``
#: owns it, and a second pin of it is the drift that file's docstring warns about.
NODES = kinemata_views.AGENT_TIER_NODES


@pytest.fixture(scope="module")
def spec_mounted() -> None:
    """Skip, naming where the spec is not, rather than passing on an empty corpus."""
    keyspec = kinemata_views._keyspec_extract()
    if not keyspec._DEFAULT_SPEC.is_file():
        pytest.skip(f"the keyspace spec is not mounted at {keyspec._DEFAULT_SPEC}")


# --------------------------------------------------------------------------- #
# The two claims, as this file states them.  Each is a difference between the
# spec's side and the descriptor's, so each names the key it found — the
# property the controls at the bottom exist to keep.
# --------------------------------------------------------------------------- #


def value_disagreements(node: str) -> dict[str, tuple[str, str]]:
    """``{key: (spec, descriptor)}`` where the two state different defaults."""
    stated = kinemata_views.node_spec_defaults(node)
    produced = kinemata_views.agent_tier_defaults(node)
    return {
        key: (stated[key], produced[key])
        for key in sorted(set(stated) & set(produced))
        if stated[key] != produced[key]
    }


def fabricated_defaults(node: str) -> dict[str, str]:
    """``{key: value}`` the descriptor STATES where §2d states no value at all.

    A §2d cell that is a realization (``--model <val>``) or a sentinel states no
    default the descriptor is held to, so a value appearing there is a fabricated
    floor — the one the conformance board probed and no gate could see.
    """
    stated = kinemata_views.node_spec_defaults(node)
    return {
        key: value
        for key, value in sorted(kinemata_views.agent_tier_defaults(node).items())
        if key not in stated
    }


# --------------------------------------------------------------------------- #
# Controls.  A check that cannot fail is not a check, so each of these mutates
# ONE input of the comparison above and asserts the difference it should
# produce, naming the key.
# --------------------------------------------------------------------------- #


def _rebuild(node: str, doc: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    """Serve *doc* to the production loader for *node*, and rebuild what it cached.

    ⚑ THE SEAM IS THE ONE FUNCTION EVERY READ PASSES THROUGH.
    ``agent_defaults._load_doc`` is the single reader of a defaults file, so
    patching it is a descriptor edit as the loader sees it.  ``load_behavior``
    then runs the real ``_build_behavior`` over the mutated rows, refusals and all.

    ⚑ THE TARGET'S CONSTANT IS REBUILT, NOT THE MODULE RELOADED.  Each plugin calls
    ``load_behavior`` once at import and returns the module-level tuple from
    ``setting_descriptors()``, so rebinding that one name is what a real edit to the
    file would change on the next launch — and it leaves the installed package
    untouched, which a lane may not write.
    """
    module = importlib.import_module(f"kanibako.plugins.{node}.target")
    package, filename = f"kanibako.plugins.{node}", f"{node}-defaults.yaml"
    monkeypatch.setattr(agent_defaults, "_load_doc", lambda p, f: copy.deepcopy(doc))
    monkeypatch.setattr(
        module, f"_{node.upper()}_BEHAVIOR", agent_defaults.load_behavior(package, filename)
    )


@pytest.fixture
def descriptor_edit(monkeypatch: pytest.MonkeyPatch):
    """Edit one node's descriptor as the production loader reads it."""
    def apply(node: str, mutate) -> None:
        package, filename = f"kanibako.plugins.{node}", f"{node}-defaults.yaml"
        doc = copy.deepcopy(agent_defaults._load_doc(package, filename))
        mutate(doc)
        _rebuild(node, doc, monkeypatch)
    return apply


@pytest.fixture
def spec_edit(monkeypatch: pytest.MonkeyPatch):
    """Drop rows from §2d as the view reads them."""
    def apply(node: str, drop: str) -> None:
        real = kinemata_views.spec_fence_rows

        def without_row(section: str, marker: str):
            return [row for row in real(section, marker) if row[0] != drop]

        assert drop in kinemata_views.node_spec_defaults(node), (
            f"{drop} is not a STATED §2d row for {node}, so dropping it tests nothing"
        )
        monkeypatch.setattr(kinemata_views, "spec_fence_rows", without_row)
        assert drop not in kinemata_views.node_spec_defaults(node)
    return apply


class TestTheControlsCanFail:
    """⚑⚑ EACH ONE NAMES THE KEY IT FOUND.  A control that passed would prove the
    comparison sees nothing, which is the specific failure this project has hit
    repeatedly: a green check over an empty corpus."""

    def test_a_mutated_default_is_found(self, spec_mounted, descriptor_edit):
        """claude `model: "opus"` — the probe the conformance board recorded as
        staying green, because no view read a plugin's descriptor."""
        def add_a_floor(doc: dict) -> None:
            for row in doc["behavior"]:
                if row["key"] == "model":
                    row["default"] = "opus"
        descriptor_edit("claude", add_a_floor)

        assert fabricated_defaults("claude") == {"agent.claude.model": "opus"}

    @pytest.mark.parametrize("sentinel", ["{}", "<None>"])
    def test_a_string_sentinel_default_is_found(
        self, spec_mounted, descriptor_edit, sentinel
    ):
        """A default that is the STRING ``"{}"`` or ``"<None>"`` renders like an
        absence but is a value: on claude's ``model`` (no stated §2d value) it is a
        fabricated floor, not an omission."""
        def add_a_floor(doc: dict) -> None:
            for row in doc["behavior"]:
                if row["key"] == "model":
                    row["default"] = sentinel
        descriptor_edit("claude", add_a_floor)

        assert fabricated_defaults("claude") == {"agent.claude.model": sentinel}

    def test_a_dropped_spec_row_is_found(self, spec_mounted, spec_edit):
        """§2d losing a row the descriptor still states must not silently pass: the
        descriptor's value is then one §2d does not hold it to."""
        spec_edit("claude", "agent.claude.env.DISABLE_AUTOUPDATER")

        assert fabricated_defaults("claude") == {
            "agent.claude.env.DISABLE_AUTOUPDATER": "1"
        }

    def test_a_brace_form_expands(self):
        """The one notation rule, on a synthetic row rather than a real one."""
        assert kinemata_views.expand_braces("a.{x,y}.z") == ["a.x.z", "a.y.z"]
        assert kinemata_views.expand_braces("a.plain") == ["a.plain"]

    def test_a_row_inside_an_html_comment_is_not_a_row(self, monkeypatch):
        """A ``key | value`` example inside ``<!-- -->`` — inline or spanning lines —
        is prose about a row, and the reader must not count it."""
        fence = [
            "agent.x.a | 1  real",
            "<!-- agent.x.b | 2  inline -->",
            "<!-- opens here",
            "agent.x.c | 3  inside a multi-line comment",
            "closes here -->",
            "agent.x.d | 4  <!-- trailing note -->",
        ]
        monkeypatch.setattr(kinemata_views, "spec_fence", lambda section, marker: fence)

        assert kinemata_views.spec_fence_rows("2d", "m") == [
            ("agent.x.a", "1"), ("agent.x.d", "4"),
        ]

    def test_an_absence_value_states_no_default(self, monkeypatch):
        """A RAW ``None`` or empty-dict floor is what omitting the row yields, so it
        reads the same as no row; only a real value is produced."""
        monkeypatch.setattr(kinemata_views, "_agent_tier_floors", lambda node: {
            "agent.claude.none": None,
            "agent.claude.empty": {},
            "agent.claude.real": "x",
        })

        assert kinemata_views.agent_tier_defaults("claude") == {"agent.claude.real": "x"}


# --------------------------------------------------------------------------- #
# The anti-vacuity case, and the pins.
# --------------------------------------------------------------------------- #


class TestTheCorporaAreNotEmpty:
    """Every assertion below the pins is a difference between two sets, and the
    difference between two empty sets is green.  The counts are asserted first."""

    def test_every_node_states_a_default(self, spec_mounted):
        for node in NODES:
            stated = kinemata_views.node_spec_defaults(node)
            assert stated, (
                f"§2d states no comparable default for {node} — the fence's row "
                f"notation changed and this file is checking nothing"
            )

    def test_a_plugin_states_something_the_spec_cannot_hold_it_to(self, spec_mounted):
        """Every plugin has a cell that states no default, so the
        `fabricated_defaults` control has a subject rather than passing vacuously."""
        for node in ("claude", "codex", "goose"):
            assert kinemata_views.node_not_expressible(node), (
                f"§2d's {node} fence has no NOT-EXPRESSIBLE cell — a realization or "
                f"dest-keyed row was reworded into a value the view would compare"
            )


class TestTheStatedDefaultsAgree:
    """Where §2d states a value the descriptor can carry, it is the same value."""

    @pytest.mark.parametrize("node", NODES)
    def test_no_key_disagrees(self, spec_mounted, node):
        assert not value_disagreements(node), (
            f"§2d and {node}'s descriptor state different defaults for the same key "
            f"(spec, descriptor): {value_disagreements(node)} — the spec is the "
            f"authority; move the descriptor"
        )

    @pytest.mark.parametrize("node", NODES)
    def test_the_two_sides_name_the_same_keys(self, spec_mounted, node):
        stated = kinemata_views.node_spec_defaults(node)
        produced = kinemata_views.agent_tier_defaults(node)
        assert set(stated) == set(produced), (
            f"§2d states {sorted(set(stated) - set(produced))} and {node} states "
            f"{sorted(set(produced) - set(stated))} — a value one side holds and the "
            f"other does not is a finding, not a shape to reconcile"
        )

    @pytest.mark.parametrize("node", NODES)
    def test_nothing_is_fabricated(self, spec_mounted, node):
        assert not fabricated_defaults(node), (
            f"{node}'s descriptor states a default where §2d states none: "
            f"{fabricated_defaults(node)} — a value there is a fabricated floor"
        )


class TestTheCellClassification:
    """⚑ THE VERDICT IS PINNED, so a cell that moves between the three classes reds
    here rather than quietly changing what the views compare."""

    @pytest.mark.parametrize(
        ("node", "expected"),
        [
            ("claude", [
                "agent.claude.access",
                "agent.claude.bindings.ro[~/.local/bin/claude]",
                "agent.claude.bindings.ro[~/.local/share/claude]",
                "agent.claude.bootstrap",
                "agent.claude.caches[{system.cache}/tweakcc]",
                "agent.claude.common[~/.claude/cache]",
                "agent.claude.common[~/.claude/plugins]",
                "agent.claude.endpoint",
                "agent.claude.model",
                "agent.claude.synced[~/.claude/.credentials.json]",
            ]),
            ("codex", [
                "agent.codex.access",
                "agent.codex.bindings.ro[~/.local/bin/codex]",
                "agent.codex.bootstrap",
                "agent.codex.model",
                "agent.codex.synced[~/.codex/auth.json]",
            ]),
            ("goose", [
                "agent.goose.access",
                "agent.goose.bindings.ro[~/.local/bin/goose]",
                "agent.goose.bootstrap",
                "agent.goose.synced[~/.config/goose/secrets.yaml]",
            ]),
            ("shell", ["agent.shell.canon"]),
        ],
    )
    def test_the_rows_no_claim_is_made_about(self, spec_mounted, node, expected):
        assert kinemata_views.node_not_expressible(node) == expected, (
            f"§2d's {node} fence now classifies as NOT EXPRESSIBLE a different set of "
            f"rows than the one the report listed — re-read them and update this pin "
            f"and the report together"
        )

    def test_a_quoted_cell_is_a_value_and_an_unquoted_one_is_prose(self):
        """The distinction the label rows turn on: `"…"` is the fence's string
        delimiter, so a quoted cell states a value however long it is."""
        assert kinemata_views.classify_spec_cell("k", '"Command Line Shell (shell)"') \
            == kinemata_views.STATED
        assert kinemata_views.classify_spec_cell("k", "tier REALIZATION: full → FLAG") \
            == kinemata_views.NOT_EXPRESSIBLE

    def test_a_sentinel_is_membership_and_a_boolean_is_stated(self):
        """`true` is the fence's boolean; only `<None>` and `{}` are what absence
        yields, so `agent.shell.allow_helpers | true` is a value to compare."""
        assert kinemata_views.classify_spec_cell("k", kinemata_views.SPEC_NULL) \
            == kinemata_views.MEMBERSHIP
        assert kinemata_views.classify_spec_cell("k", kinemata_views.SPEC_EMPTY) \
            == kinemata_views.MEMBERSHIP
        assert kinemata_views.classify_spec_cell("k", "true") == kinemata_views.STATED


class TestTheDescriptorSideIsTheRealWiring:
    """The values above are read the way the product reads them.  This pins the
    route, so a future refactor that reads the YAML directly is a red rather than
    a parity result of unknown provenance."""

    def test_the_plugin_floor_comes_from_the_target(self, spec_mounted):
        from kanibako.targets import get_target

        target = get_target("claude")()
        assert [row.key for row in target.setting_descriptors()] == [
            "label", "model", "endpoint", "transform",
        ], (
            "claude's setting_descriptors() no longer yields the four rows "
            "kinemata_views reads — the descriptor side is being read somewhere else"
        )
        assert kinemata_views.agent_tier_defaults("claude")["agent.claude.label"] == (
            "Claude Code"
        )

    def test_the_pseudo_agent_floor_comes_from_core_defaults(self, spec_mounted):
        from kanibako.settings.core_defaults import pseudo_tier_defaults

        assert pseudo_tier_defaults()["agent.shell.label"] == "Command Line Shell (shell)"
        assert kinemata_views.agent_tier_defaults("shell")["agent.shell.access"] == "full"

    def test_the_guest_home_expansion_is_folded_back(self, spec_mounted):
        """⚑ WITHOUT THIS EVERY `env.KANIBAKO_DIRECTIVE_FINAL` ROW WOULD DISAGREE with a
        spec that is right: the loader stores the in-box path, §2d writes `~/…`."""
        from kanibako.settings.settings_resolve import GUEST_HOME

        raw = get_target_env("claude", "KANIBAKO_DIRECTIVE_FINAL")
        assert raw.startswith(GUEST_HOME), (
            f"claude's KANIBAKO_DIRECTIVE_FINAL no longer arrives as a $GUEST_HOME "
            f"expansion ({raw!r}); the fold in agent_tier_defaults is now untested"
        )
        assert kinemata_views.agent_tier_defaults("claude")[
            "agent.claude.env.KANIBAKO_DIRECTIVE_FINAL"
        ] == "~/.claude/CLAUDE.md"


def get_target_env(node: str, var: str) -> str:
    """One ``env.<VAR>`` value as the product's own ``default_envs()`` returns it."""
    from kanibako.targets import get_target

    return get_target(node)().default_envs()[f"agent.{node}.env.{var}"]
