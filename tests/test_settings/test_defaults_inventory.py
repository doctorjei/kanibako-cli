"""``kanibako system defaults`` — the PROVENANCE listing, asserted against its sources.

The command answers "which of the values in force are kanibako's own defaults, and where
is each one declared". That is only worth printing if it is COMPLETE and if the SOURCE
column is true, so this file asserts exactly those two properties against oracles that
are not the command's own output:

* COMPLETENESS — every section counted against the artefact it enumerates (the manifest's
  own default-row count, its ``bind_default_entries`` arms, the two env emitters).  The
  listing's own row count is never the standard it is measured by.
* PROVENANCE — :func:`~kanibako.settings.defaults_inventory.source_groups` partitions the
  registry rows, and that partition is asserted EQUAL to
  ``test_manifest_conformance``'s classification of the same rows.  Those two carriers
  exist for different reasons (one labels, one pins values) and neither is generated
  from the other, so this is the same "two carriers, asserted equal" arrangement the
  conformance file itself uses.

⚑ THE CROSS-IMPORT IS THE POINT, NOT A SHORTCUT.  Re-listing every classified row here
would be a THIRD copy of the classification, and the one that rots first.  ``tests`` is
a package (``tests/__init__.py``), so the conformance module imports like any other; the
precedent is ``tests/e2e/test_codex_panel.py`` importing its sibling.

Indent note: 4 spaces, matching every sibling in ``tests/test_settings/``.
"""

from __future__ import annotations

import io

import pytest

from kanibako.settings import core_defaults
from kanibako.settings.defaults_inventory import (
    DefaultRow,
    bind_rows,
    env_rows,
    key_rows,
    manifest_default_rows,
    print_defaults,
    source_groups,
)
from kanibako.settings.keyspace_manifest import manifest_doc
from tests.test_settings.test_manifest_conformance import (
    EXEMPT_DEFAULT_KEYS,
    PINNED_DEFAULT_KEYS,
)

#: Which conformance class each SOURCE label partitions.  ⚑ THE CORRESPONDENCE LIVES
#: HERE, not in ``src``: whether a default has a value ORACLE is a property the test
#: suite cares about, while the command only cares WHERE the value is written down.
#: Several labels refine one conformance class (the two canon-producer arms, the two
#: ``paths_defaults`` tiers), so the assertion below is per-class UNION, never 1:1.
LABEL_TO_CONFORMANCE_CLASS: dict[str, str] = {
    "paths_defaults.py (config tier)": "pinned",
    "paths_defaults.py (system tier)": "pinned",
    "settings_launch.py (anchor floor)": "pinned",
    "settings_launch.py (auth floor)": "pinned",
    "core-defaults.yaml (agent_default:)": "pinned",
    "core-defaults.yaml (env:)": "pinned",
    "config.py (KanibakoConfig field)": "pinned",
    "config.py (read-with-default)": "pinned",
    "core_defaults.py (canon producer)": "pinned",
    "kuid.py (SENTINEL)": "pinned",
    # ⚑ MOVED OUT of "path join at use" (2026-08-25) when the family stopped being a
    # join and started being seven resolved keys; the conformance file pins it now.
    "channels/channels.py (channel key derivation)": "pinned",
    "built-in (path join at use)": "exempt",
    "runtime-probed (podman graphroot)": "exempt",
    "(nothing declares it — unset until you set it)": "exempt",
    "(empty — the category starts with no entries)": "exempt",
    "core_defaults.py (canon producer, per node)": "exempt",
    "launch/templates.py (layer-2 seed, default arm)": "pinned",
    "launch/templates.py (layer-2 seed)": "exempt",
}


class TestSourcePartition:
    """The SOURCE column is a true, total, non-overlapping partition of the registry."""

    def test_every_label_is_classified_by_this_file(self):
        """Anti-vacuity: a NEW source label must land here before the pins below mean anything."""
        labels = {label for label, _ in source_groups()}
        assert labels == set(LABEL_TO_CONFORMANCE_CLASS), (
            f"labels in source_groups() with no conformance class here: "
            f"{sorted(labels - set(LABEL_TO_CONFORMANCE_CLASS))}; classes named here "
            f"for labels that no longer exist: "
            f"{sorted(set(LABEL_TO_CONFORMANCE_CLASS) - labels)}"
        )

    def test_the_groups_are_disjoint(self):
        seen: dict[str, str] = {}
        for label, keys in source_groups():
            for key in keys:
                assert key not in seen, f"{key} is in both {seen[key]!r} and {label!r}"
                seen[key] = label

    def test_the_partition_covers_the_manifest_default_rows(self):
        """⚑ THE COMPLETENESS CASE, against the MANIFEST — not against the listing.

        The independent oracle is the registry's own count of rows carrying a
        ``default:``.  Drop an enumeration source and this reds NAMING what vanished,
        which is what a count alone could not say.
        """
        covered = set().union(*(keys for _, keys in source_groups()))
        declared = set(manifest_default_rows())
        assert covered == declared, (
            f"registry defaults with no source: {sorted(declared - covered)}; "
            f"sources for rows the registry no longer defaults: {sorted(covered - declared)}"
        )
        assert len(declared) == 65, (
            f"the manifest gives {len(declared)} rows a default, not the 65 measured"
        )

    def test_the_partition_agrees_with_the_conformance_classification(self):
        """⚑⚑ THE PROVENANCE CASE.  Two carriers, asserted equal.

        A row that moves between "has a code oracle" and "does not" over in
        ``test_manifest_conformance`` reds HERE, because its SOURCE label would then be
        describing an artefact that no longer carries it.
        """
        by_class: dict[str, set[str]] = {"pinned": set(), "exempt": set()}
        for label, keys in source_groups():
            by_class[LABEL_TO_CONFORMANCE_CLASS[label]] |= set(keys)
        assert by_class["pinned"] == set(PINNED_DEFAULT_KEYS), (
            f"labelled as having a declaring artefact but conformance exempts: "
            f"{sorted(by_class['pinned'] - set(PINNED_DEFAULT_KEYS))}; conformance pins "
            f"but this file classes as source-less: "
            f"{sorted(set(PINNED_DEFAULT_KEYS) - by_class['pinned'])}"
        )
        assert by_class["exempt"] == set(EXEMPT_DEFAULT_KEYS)

    def test_the_derived_groups_really_are_read_off_their_carriers(self):
        """The five DERIVED groups must be non-empty, or "derived" is hiding an outage.

        A floor builder that stopped declaring anything would leave an EMPTY group, and
        the coverage case above would then red somewhere else entirely — here it reds
        naming the carrier.
        """
        sizes = {label: len(keys) for label, keys in source_groups()}
        assert sizes["paths_defaults.py (config tier)"] == 6
        assert sizes["paths_defaults.py (system tier)"] == 11
        assert sizes["settings_launch.py (anchor floor)"] == 6
        assert sizes["settings_launch.py (auth floor)"] == 6
        assert sizes["core-defaults.yaml (agent_default:)"] == 4

    def test_the_auth_floor_key_set_does_not_depend_on_the_probe_agent(self):
        """The probe agent name is inert for the six auth rows — pinned, not assumed.

        ``source_groups`` calls ``auth_chain_floor`` with a placeholder name purely to
        enumerate keys.  If a future floor ever spelled an agent INTO one of the six,
        the label would start depending on a fabricated value; this case makes that
        visible instead of silent.
        """
        from kanibako.settings.settings_launch import auth_chain_floor

        declared = set(manifest_default_rows())
        first = set(auth_chain_floor(mode="primary", agent_name="default")) & declared
        second = set(auth_chain_floor(mode="primary", agent_name="goose")) & declared
        assert first == second and len(first) == 6


class TestKeyRows:
    """Section 1's rendered rows."""

    def test_every_registry_default_gets_exactly_one_row(self):
        rows = key_rows()
        assert len(rows) == len(manifest_default_rows())
        assert len({r.key for r in rows}) == len(rows)

    def test_no_row_has_an_empty_cell(self):
        """The whole point is CLARITY: a blank SOURCE or SCOPE would be worse than nothing."""
        for row in key_rows():
            assert row.key and row.scope and row.source, row
            assert row.value or row.per_mode, row

    def test_the_scope_fallback_fires_only_for_the_layer_1_config_tier(self):
        """Anti-vacuity for ``_scope_of``'s fallback — it must not absorb a scopeless row.

        Exactly six rows carry ``layer: 1`` instead of a ``scope:``, and their head IS
        their scope.  A seventh would mean a row lost its scope, which this catches.
        """
        rows = manifest_default_rows()
        scopeless = {key for key, row in rows.items() if not row.get("scope")}
        assert scopeless == {
            "config.data", "config.settings", "config.agents",
            "config.primary_workset", "config.registry", "config.journal",
        }, "a default row lost its scope, or a seventh layer-1 row appeared"
        by_key = {r.key: r for r in key_rows()}
        assert all(by_key[key].scope == "config" for key in scopeless)

    def test_the_per_mode_rows_are_the_manifest_per_mode_rows(self):
        """14 rows default per box MODE; each renders as continuation lines, not a cell."""
        per_mode = [r for r in key_rows() if r.per_mode]
        assert len(per_mode) == 14
        assert all(r.value == "(varies by box mode)" for r in per_mode)
        assert all(not r.per_mode for r in key_rows() if r.value != "(varies by box mode)")

    def test_equal_mode_arms_collapse(self):
        """``workset.boxes`` agrees for primary/named and differs for standalone."""
        row = next(r for r in key_rows() if r.key == "workset.boxes")
        assert row.per_mode == (
            ("primary, named", "@meta.workset.path/boxes"),
            ("standalone", "@meta.workset.path/box_data"),
        )

    def test_an_unclassified_row_is_refused_rather_than_printed_blank(self, monkeypatch):
        """⚑ MUTATION PROOF 1 — drop an enumeration source; the listing REFUSES, naming it.

        This is the failure the command must never have: a default printed with no
        source.  Removing one group makes 17 rows unclassified, and ``key_rows`` raises
        naming them rather than rendering empty cells.
        """
        import kanibako.settings.defaults_inventory as inv

        full = inv.source_groups()
        monkeypatch.setattr(
            inv, "source_groups",
            lambda: tuple(g for g in full if not g[0].startswith("paths_defaults.py")),
        )
        with pytest.raises(RuntimeError) as exc:
            inv.key_rows()
        assert "no source classification" in str(exc.value)
        assert "config.data" in str(exc.value)

    def test_a_doubly_classified_row_is_refused(self, monkeypatch):
        """⚑ MUTATION PROOF 2 — two SOURCES for one key is a contradiction, not a pick-one."""
        import kanibako.settings.defaults_inventory as inv

        clash = (*inv.source_groups(), ("a second opinion", frozenset({"box.image"})))
        monkeypatch.setattr(inv, "source_groups", lambda: clash)
        with pytest.raises(RuntimeError, match="classified twice"):
            inv.key_rows()


class TestBindRows:
    """Section 2 — the dest-keyed bind and copy entries."""

    def test_every_manifest_bind_and_category_entry_gets_a_row(self):
        """Counted against the MANIFEST's arms, not against the section's own length."""
        doc = manifest_doc()
        binds = sum(len(arm) for arm in doc["bind_default_entries"].values())
        cats = sum(
            len(arm) for name, arm in doc["category_default_entries"].items()
            if name != "parametric_keys"
        )
        assert binds == 30 and cats == 3
        assert len(bind_rows()) == binds + cats

    def test_the_internal_entries_are_marked_and_are_the_manifest_s(self):
        """``user_key: false`` rows are INCLUDED (a box gets them) and flagged as internal."""
        doc = manifest_doc()
        internal = {
            f"{arm}[{dest}]"
            for arm, entries in doc["bind_default_entries"].items()
            for dest, entry in entries.items()
            if entry.get("user_key") is False
        }
        assert len(internal) == 11
        assert {r.key for r in bind_rows() if r.internal} == internal

    def test_a_dest_renders_bracketed_and_is_never_split_on_a_dot(self):
        """A dest is DATA and contains dots; it must survive whole into the key cell."""
        row = next(
            r for r in bind_rows()
            if r.key == "box.bindings.ro[~/canon/bible/ROM_CONTENTS.md]"
        )
        assert row.scope == "box"
        assert "ROM_CONTENTS.md" in row.key

    def test_the_family_labels_come_from_the_file_the_binds_live_in(self):
        """The dest→section read is ``core_defaults``' own, so a moved dest re-labels itself."""
        families = core_defaults.bind_dest_families()
        assert families["~/workspace"] == "core"
        assert families["~/channels/chat"] == "channels"
        by_key = {r.key: r for r in bind_rows()}
        assert by_key["box.bindings.rw[~/workspace]"].source == "core-defaults.yaml (core:)"
        assert (
            by_key["box.bindings.rw[~/channels/chat]"].source
            == "core-defaults.yaml (channels:)"
        )

    def test_every_named_outside_source_still_names_a_live_manifest_row(self):
        """A stale hand-written label silently mis-attributes a bind — so it must red."""
        import kanibako.settings.defaults_inventory as inv

        dests = {
            dest
            for entries in manifest_doc()["bind_default_entries"].values()
            for dest in entries
        }
        for dest in inv._BIND_SOURCES_OUTSIDE_THE_FILE:
            assert dest in dests, f"{dest!r} is no longer a manifest bind entry"

    def test_a_dest_with_no_declaring_artefact_is_refused(self, monkeypatch):
        """⚑ MUTATION PROOF 3 — a bind whose source cannot be found REFUSES, naming it."""
        import kanibako.settings.defaults_inventory as inv

        monkeypatch.setattr(inv, "_BIND_SOURCES_OUTSIDE_THE_FILE", {})
        monkeypatch.setattr(core_defaults, "bind_dest_families", dict)
        with pytest.raises(RuntimeError) as exc:
            inv.bind_rows()
        assert "no declaring artefact" in str(exc.value)


class TestEnvRows:
    """Section 3 — the env instances a box gets, registry-enumerated or not."""

    def test_the_registry_enumerates_exactly_the_core_env_default(self):
        """⚑ THE REGISTRY NOW CARRIES ONE env INSTANCE, and section 3 still lists it.

        This case used to assert the registry carried NONE, and said that a red here
        meant per-VAR rows had been ratified and the section had to be re-derived.  They
        were: spec §2b declares ``box.env.COLORTERM`` (2026-08-15, ``9ac0980``) and the
        manifest gained its row.  The re-derivation the old text demanded is NOT a
        removal — section 3 reports the live env floor a box gets, so the one var
        kanibako ships belongs in it, and it is section 1 that grew a DECLARED KEY row.
        What this pins is that the overlap is exactly that one key: a SECOND registry
        env row would be a new ratification and must be read before it prints.
        """
        registry_env = [str(k) for k in manifest_doc()["keys"] if ".env." in str(k)]
        assert registry_env == ["box.env.COLORTERM"]
        assert "box.env.COLORTERM" in {r.key for r in env_rows()[0]}

    def test_the_core_env_floor_is_the_shipped_emitter_s(self):
        rows, _ = env_rows()
        core = {r.key: r.value for r in rows if r.source == "core-defaults.yaml (env:)"}
        assert core == core_defaults.env_default_categories()
        assert core == {"box.env.COLORTERM": "truecolor"}

    def test_every_installed_target_is_consulted_and_its_vars_listed(self):
        """Counted against ``discover_targets`` + each target's own ``default_envs``."""
        from kanibako.targets import discover_targets

        targets = discover_targets()
        rows, plugins = env_rows()
        assert set(plugins.consulted) == set(targets)
        expected: dict[str, str] = {}
        for name, cls in targets.items():
            expected.update(cls().default_envs())
        listed = {r.key: r.value for r in rows if r.source.endswith("plugin defaults (env:)")}
        assert listed == expected
        assert set(plugins.declaring) == {
            name for name, cls in targets.items() if cls().default_envs()
        }

    def test_an_empty_discovery_lists_no_plugin_vars_and_does_not_crash(self, monkeypatch):
        """⚑ MUTATION PROOF 4 — discovery returning NOTHING is legible, never a traceback.

        ⚑ This is the DISCOVERY-FAILED case, NOT a cli-only install: ``no_agent`` is
        kanibako-cli's own entry point, so a working cli-only install still consults
        one target and takes the other branch.
        """
        import kanibako.targets as targets_pkg

        monkeypatch.setattr(targets_pkg, "discover_targets", lambda *a, **k: {})
        rows, plugins = env_rows()
        assert plugins.consulted == () and plugins.declaring == ()
        assert [r.key for r in rows] == ["box.env.COLORTERM"]
        out = io.StringIO()
        print_defaults(out)
        assert "No agent targets could be discovered" in out.getvalue()

    def test_a_target_that_cannot_load_is_named_but_takes_nothing_down(self, monkeypatch):
        """``system defaults`` must work on a BROKEN install — that is when it is needed."""
        import kanibako.targets as targets_pkg

        class _Exploding:
            def __init__(self):
                raise RuntimeError("boom")

        monkeypatch.setattr(
            targets_pkg, "discover_targets", lambda *a, **k: {"broken": _Exploding},
        )
        rows, plugins = env_rows()
        assert plugins.consulted == ("broken",) and plugins.declaring == ()
        assert [r.key for r in rows] == ["box.env.COLORTERM"]


class TestRendering:
    """The printed surface — what a reader actually gets."""

    @staticmethod
    def _output() -> str:
        out = io.StringIO()
        print_defaults(out)
        return out.getvalue()

    def test_it_prints_every_row_of_every_section(self):
        """Each section's own rows must all appear — the listing is not a sample."""
        text = self._output()
        for row in (*key_rows(), *bind_rows(), *env_rows()[0]):
            assert row.key in text, f"{row.key} is enumerated but not printed"

    def test_the_columns_are_headed_and_aligned(self):
        text = self._output()
        assert text.count("KEY ") >= 3, "each of the three sections carries a header"
        for column in ("DEFAULT", "SCOPE", "SOURCE"):
            assert column in text

    def test_the_section_counts_are_stated_and_true(self):
        text = self._output()
        assert f"DECLARED KEYS ({len(key_rows())})" in text
        assert f"BIND AND COPY ENTRIES ({len(bind_rows())})" in text
        assert f"ENVIRONMENT VARIABLES ({len(env_rows()[0])})" in text

    def test_the_derived_stamps_are_excluded_out_loud(self):
        """The one class deliberately not listed says so, and says where to look."""
        text = self._output()
        assert "KANIBAKO_*" in text
        assert "box show --effective" in text

    def test_it_says_nothing_here_is_stored(self):
        """The distinction the command exists for: declared default vs stored value."""
        assert "Nothing here is stored in your settings" in self._output()

    def test_a_source_label_flip_reds_the_pin(self, monkeypatch):
        """⚑ MUTATION PROOF 5 — the SOURCE cell is pinned, so a wrong label cannot ship.

        The pin is ``test_the_partition_agrees_with_the_conformance_classification`` and
        ``test_every_label_is_classified_by_this_file``: both key off the LABEL, so a
        relabelled group reds there.  This case proves the label reaches the printed
        cell at all — without it, "the label is pinned" would be a claim about a string
        nobody renders.
        """
        import kanibako.settings.defaults_inventory as inv

        flipped = tuple(
            ("A WRONG LABEL", keys) if label == "paths_defaults.py (config tier)"
            else (label, keys)
            for label, keys in inv.source_groups()
        )
        monkeypatch.setattr(inv, "source_groups", lambda: flipped)
        row = next(r for r in inv.key_rows() if r.key == "config.data")
        assert row.source == "A WRONG LABEL"
        assert "A WRONG LABEL" in self._output()
        # And the flipped label is NOT one this file classifies — the partition case
        # above is exactly what would red on a real relabelling.
        assert "A WRONG LABEL" not in LABEL_TO_CONFORMANCE_CLASS


class TestRowShape:
    """The row type itself — the contract the renderer relies on."""

    def test_a_row_defaults_to_scalar_and_not_internal(self):
        row = DefaultRow(key="k", value="v", scope="box", source="s")
        assert row.per_mode == () and row.internal is False
