"""A packaged ``channels:`` row that reads its PROBE and gets ``<None>`` is OMITTED with
one warning at launch (spec §2a), not a hard failure.

⚑ THE SHAPE.  Every shipped ``channels:`` row carries a ``meta_ref``, so its source is a
braced ``{system.channels.*}`` ref that resolves through ``system_path_floor`` and a null
at that key omits the bind with the [R185] warning.  The row that carries NO ``meta_ref``
reads its runtime-probed host path instead -- the ``entry.get("meta_ref", sources[source])``
arm in ``core_defaults.channel_default_categories``.  Four of those probe names are
STANDARD bind sources, so the probe can answer ``None``, and the producer keeps the slot
null on purpose (the slot is the standalone-omit gate; see
``test_null_system_path_key.py::TestTheChannelTableKeepsANullArmNull``).

⚑ THE DEFECT that file pins the far end of.  A ``<None>`` source left in the floor reaches
the bind parse, is stringified to the four-character path ``"None"``, and
``refuse_unrooted_source`` refuses it: ``bindings.rw entry at '…/channels/common' declares
a bare-relative host source 'None'`` -- a HARD LAUNCH FAILURE for a bind the spec omits.
Keyspec §2a is flat on the point: *"any layer whose source/dest is ``<None>`` is SKIPPED"*,
and §Channels: *"STANDARD binds are omitted by setting the entry and its source key to
``<None>``.  If only one is set, a warning is issued."*

⚑ THE FIX IS A RECLASSIFICATION, NOT A NEW PATH.  A row whose value came from a declared
source key is a STANDARD bind WITH that key, so the floor repoints its null source at the
key the row declares -- the very thing a ``meta_ref`` row carries -- and every existing
seam then does its job: the ref resolves to the same ``<None>``, the collapse omits the
bind, and ``_warn_lone_none_standard_binds`` issues the one warning.  Nothing downstream
ever holds a ``None`` source, and the producer's ``(None,)`` is untouched.

⚑ THE BOUNDARY IS PINNED TOO.  A literal-source floor entry is INTERNAL ([Q95] 2) and is
left exactly as it is; a null source with NO declared key is left out, since there is no
key to name in a report nobody could act on.
"""

from __future__ import annotations

import copy

import pytest

from kanibako.settings import core_defaults
from kanibako.settings.config_io import write_nested_key
from kanibako.settings.config_keys import _KEY_ROUTES
from kanibako.settings.paths import (
    _floor_field,
    load_std_paths,
    resolve_project,
    system_path_floor,
)
from kanibako.settings.settings_launch import (
    build_launch_snapshot,
    reset_none_warnings,
    snapshot_category_entries,
)
from kanibako.settings.settings_resolve import ResolveCtx

#: The guest destination of the ``channels_common`` row -- the one this file nulls.
COMMON_DEST = "/home/agent/channels/common"
NULL_KEY = "system.channels.common"
_LOGGER = "kanibako.settings.settings_launch"


def _row_reads_its_probe(monkeypatch, source: str) -> None:
    """Drop the ``meta_ref`` from the ``channels:`` row named *source*, leaving that row
    to read its probed host path directly.

    ⚑ ONE ROW ONLY: the ``workset_*`` rows are substituted from their ``meta_ref`` when
    the workset root is null, so stripping the whole table would break a row this file is
    not testing.
    """
    doc = copy.deepcopy(core_defaults._load_doc())
    matched = [row for row in doc["channels"] if row["source"] == source]
    assert matched, f"no packaged channels row names source {source!r}"
    for row in matched:
        row.pop("meta_ref", None)
    monkeypatch.setattr(core_defaults, "_load_doc", lambda: copy.deepcopy(doc))


def _nulled(std, key: str):
    """Set *key* to ``<None>`` in the real system settings file and re-read ``std``."""
    sections, slot = _KEY_ROUTES[key]
    write_nested_key(std.settings, sections, slot, None)
    return load_std_paths()


def _launch(std, proj) -> list:
    """The real launch assembly over the real channel floor, as ``start`` folds it.

    ⚑ ``system_path_floor`` rides in beside the channel table because the real launch
    folds it: the rows that keep their ``meta_ref`` resolve THEIR source through it, so
    without it a faithful control would look like a defect.
    """
    ctx = ResolveCtx(
        agent_name="claude", workset_name=None, host_home="/home/host",
        xdg={"XDG_DATA_HOME": "/data"},
    )
    reset_none_warnings()
    snap = build_launch_snapshot(
        agent_name="claude", ctx=ctx, system_path=std.settings, agent_path=None,
        workset_path=None, box_path=None,
        default_categories={
            **system_path_floor(std),
            **core_defaults.channel_default_categories(std, proj),
        },
        meta_identity={"meta.box.name": "b1"}, valid_agents=("claude",),
    )
    return snapshot_category_entries(snap, active_agent="claude", box_ctx=ctx)


def _bindings(entries) -> dict[str, str | None]:
    return {
        e.box_dest: e.host_src for e in entries if e.category.startswith("bindings")
    }


def _warnings(caplog) -> list[str]:
    return [
        r.getMessage() for r in caplog.records if r.name == _LOGGER
    ]


class TestANullProbedChannelSourceIsOmittedAndWarned:
    """The row is a STANDARD bind whose source key is null: omit it, warn once, name
    everything the clause asks for, and never fail the launch."""

    def test_the_launch_does_not_fail(self, std, config, project_dir, monkeypatch, caplog):
        _row_reads_its_probe(monkeypatch, "channels_common")
        nulled = _nulled(std, NULL_KEY)
        proj = resolve_project(nulled, config, str(project_dir), initialize=True)
        assert COMMON_DEST not in _bindings(_launch(nulled, proj))

    def test_the_row_is_omitted_and_every_other_row_still_mounts(
        self, std, config, project_dir, monkeypatch, caplog,
    ):
        _row_reads_its_probe(monkeypatch, "channels_common")
        nulled = _nulled(std, NULL_KEY)
        proj = resolve_project(nulled, config, str(project_dir), initialize=True)
        mounted = _bindings(_launch(nulled, proj))
        assert COMMON_DEST not in mounted, sorted(mounted)
        assert "/home/agent/channels/chat" in mounted, sorted(mounted)
        assert "/home/agent/channels/share" in mounted, sorted(mounted)

    def test_the_warning_names_the_entry_the_source_key_and_the_file(
        self, std, config, project_dir, monkeypatch, caplog,
    ):
        _row_reads_its_probe(monkeypatch, "channels_common")
        nulled = _nulled(std, NULL_KEY)
        proj = resolve_project(nulled, config, str(project_dir), initialize=True)
        _launch(nulled, proj)
        warnings = _warnings(caplog)
        assert len(warnings) == 1, warnings
        text = warnings[0]
        assert f"box.bindings.rw[{COMMON_DEST}]" in text
        assert NULL_KEY in text
        assert str(nulled.settings) in text

    def test_no_string_none_reaches_a_mount_or_a_message(
        self, std, config, project_dir, monkeypatch, caplog,
    ):
        _row_reads_its_probe(monkeypatch, "channels_common")
        nulled = _nulled(std, NULL_KEY)
        proj = resolve_project(nulled, config, str(project_dir), initialize=True)
        mounted = _bindings(_launch(nulled, proj))
        assert "None" not in {src for src in mounted.values() if isinstance(src, str)}
        assert "'None'" not in " ".join(_warnings(caplog))

    def test_the_same_row_with_a_real_probe_still_mounts(
        self, std, config, project_dir, monkeypatch,
    ):
        """⚑ THE CONTROL, and the anti-vacuity half: with the key NOT nulled the same
        probe answers a real host path and the row mounts as it always did, silent."""
        _row_reads_its_probe(monkeypatch, "channels_common")
        proj = resolve_project(std, config, str(project_dir), initialize=True)
        mounted = _bindings(_launch(std, proj))
        assert mounted.get(COMMON_DEST) == str(std.channels_common)
        assert std.channels_common is not None


class TestTheReclassificationStaysInsideItsLane:
    """Only a null source WITH a declared key is repointed; a literal source stays
    INTERNAL and a null with no key is left out.

    ⚑ THE FLOOR HELPER IS IMPORTED PER TEST, not at module scope: on the unfixed base
    the symbol does not exist, and a module-level import would take the behavioral
    class above down with it as a collection error instead of the launch failure it
    is meant to show.
    """

    @staticmethod
    def _floor(entry, dest=COMMON_DEST):
        from kanibako.settings.settings_launch import _floored_bind_entry

        return _floored_bind_entry(dest, entry)

    def test_a_literal_source_entry_is_left_alone(self):
        entry = ("/h/pkg", "ro")
        assert self._floor(entry) is entry

    def test_a_null_entry_is_left_alone(self):
        """A null ENTRY (``{dest: null}``) is not a null SOURCE -- the merge and the
        [R185] both-null rule own it, not this repoint."""
        assert self._floor(None) is None

    def test_a_null_source_with_a_declared_key_is_repointed_at_it(self):
        assert self._floor((None,)) == (f"{{{NULL_KEY}}}",)

    def test_the_options_of_a_repointed_entry_are_kept(self):
        assert self._floor((None, "ro")) == (f"{{{NULL_KEY}}}", "ro",)

    def test_a_null_source_with_no_declared_key_is_left_out(self):
        """No packaged row names a source key for this destination, so there is no key
        to name in a warning -- the omit stands without one."""
        from kanibako.settings.kb_store import __MISSING__

        assert self._floor((None,), "/nowhere/at/all") is __MISSING__

    @pytest.mark.parametrize("probe, key", [
        ("channels_common", "system.channels.common"),
        ("channels_chat", "system.channels.chat"),
        ("channels_share", "system.channels.share"),
        ("channels_mailboxes", "system.channels.mailboxes"),
    ])
    def test_the_declared_key_is_the_route_that_feeds_the_probe(self, probe, key):
        """⚑ THE MAPPING IS READ BACKWARD FROM THE KEY ROUTES, so it cannot drift from
        the one function that says which ``std`` field a key resolves into."""
        dest = next(
            row["box_dest"] for row in core_defaults._load_doc()["channels"]
            if row["source"] == probe
        )
        from kanibako.settings.core_defaults import channel_source_key
        from kanibako.settings.settings_resolve import normalize_bind_dest

        assert channel_source_key(normalize_bind_dest(str(dest))) == key
        assert _floor_field(key) == probe

    def test_a_probe_no_key_feeds_has_no_source_key(self):
        """``inbox`` is the construct-set read-only ``meta.box.inbox``, and the
        ``workset_*`` probes hang off the workset anchor -- neither has a file key."""
        from kanibako.settings.core_defaults import channel_source_key
        from kanibako.settings.settings_resolve import normalize_bind_dest

        inbox = next(
            row["box_dest"] for row in core_defaults._load_doc()["channels"]
            if row["source"] == "inbox"
        )
        assert channel_source_key(normalize_bind_dest(str(inbox))) is None
