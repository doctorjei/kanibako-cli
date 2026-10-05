"""A ``<None>`` at a STANDARD bind's SOURCE KEY omits the bind (spec §2a), not a refusal.

``STANDARD_BIND_SOURCE_KEYS`` is the one exclusion in ``config.refuses_null_path_key``,
and the comment on that constant promises a test that pins it against the REAL floor —
this is that test: a sixth standard-bind source cannot be added without this set
noticing.  The rest of the file pins the two ends of the round trip: a null survives
the read as a real ``None`` (never the word ``"None"``), and the launch OMITS the
bind rather than mounting a directory called ``None`` or a bare-relative path.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kanibako.settings.bootstrap import SYSTEM_PATH_DEFAULTS
from kanibako.settings.config import (
    STANDARD_BIND_SOURCE_KEYS,
    refuses_null_path_key,
    system_path_set_values,
)
from kanibako.settings.paths import (
    load_std_paths,
    load_system_config,
    resolve_system_paths,
    system_path_floor,
)
from kanibako.settings.settings_resolve import match_braced


def _settings(tmp_path, doc: str):
    path = tmp_path / "settings.yaml"
    path.write_text(doc)
    return path


class TestTheSetIsExactlyTheStandardBindSources:
    """⚑ THE CONSTANT IS DERIVED, NOT AUTHORED: these are the ``system.*`` keys the
    SHIPPED bind rows name as their source, read through the production reader of
    ``core-defaults.yaml``.  Repointing or adding a bind moves this set — or reds here,
    which is what the comment on the constant promises."""

    @staticmethod
    def _declared_bind_source_keys() -> set[str]:
        from kanibako.settings.core_defaults import _load_doc

        doc = _load_doc()
        named: set[str] = set()
        for section in ("channels", "canon"):
            for row in doc.get(section, []) or []:
                ref = str(row.get("meta_ref", ""))
                for i, c in enumerate(ref):
                    braced = match_braced(ref, i) if c == "{" else None
                    if braced and braced[0] == "ref" and braced[1].startswith("system."):
                        named.add(braced[1])
        return named

    def test_it_is_exactly_the_keys_the_shipped_binds_name(self):
        assert self._declared_bind_source_keys() == set(STANDARD_BIND_SOURCE_KEYS)

    def test_a_key_outside_the_set_still_refuses(self):
        for key in SYSTEM_PATH_DEFAULTS:
            expected = key not in STANDARD_BIND_SOURCE_KEYS
            assert refuses_null_path_key(key) is expected, key


class TestANullSurvivesTheReadAsANull:
    """The two places the word ``"None"`` used to be born."""

    def test_the_set_values_carry_a_real_none(self, tmp_path):
        path = _settings(tmp_path, "system:\n  canon: null\n")
        values = system_path_set_values(path)
        assert values["system.canon"] is None
        assert not isinstance(values["system.canon"], str)

    def test_the_resolved_tier_omits_the_key(self, tmp_path):
        path = _settings(tmp_path, "system:\n  channels:\n    common: null\n")
        values = system_path_set_values(path)
        resolved = resolve_system_paths(
            values, data_home=tmp_path / "data", home=tmp_path / "home",
        )
        assert "system.channels.common" not in resolved
        assert "system.channels.chat" in resolved  # its siblings are untouched

    def test_a_null_referent_nulls_the_value_that_names_it(self, tmp_path):
        """``system.channels.broadcast`` is ``@system.channels.chat/broadcast.md``.

        ⚑ THE ONE THAT MEASURED: with a null chat this resolved to the PATH
        ``None/broadcast.md``, and the chat seeder created that directory in the CWD.
        §0 says an embedded reference to a present ``<None>`` IS ``<None>``.
        """
        path = _settings(tmp_path, "system:\n  channels:\n    chat: null\n")
        values = system_path_set_values(path)
        resolved = resolve_system_paths(
            values, data_home=tmp_path / "data", home=tmp_path / "home",
        )
        assert "system.channels.broadcast" not in resolved
        for value in resolved.values():
            assert "None" not in str(value)

    def test_a_layer1_null_is_still_refused(self, tmp_path):
        """The premise the ``config.*`` narrowing rests on, pinned."""
        from kanibako.errors import KanibakoError

        path = _settings(tmp_path, "system:\n  channelroot: null\n")
        with pytest.raises(KanibakoError) as caught:
            system_path_set_values(path)
        assert "system.channelroot" in str(caught.value)


class TestTheFloorCarriesTheNull:
    """⚑ ``system_path_floor`` is where a bind's source RESOLVES, so this is the map that
    decides whether a bind collapses.  A ``"None"`` string here would name a directory."""

    def test_a_null_std_field_is_a_real_none_in_the_floor(self, std):
        from kanibako.settings.config_io import dump_doc

        dump_doc(std.settings, {"system": {"canon": None}})
        nulled = load_std_paths()
        floor = system_path_floor(nulled)
        assert nulled.canon is None
        assert floor["system.canon"] is None
        assert not isinstance(floor["system.canon"], str)
        assert isinstance(floor["system.channels.chat"], str)

    def test_load_std_paths_does_not_raise_on_the_omitted_key(self, std):
        """⚑ IT USED TO BE A SUBSCRIPT (``resolved["system.canon"]``), which raises
        ``KeyError`` the moment the key is omitted — the omission is the feature."""
        from kanibako.settings.config_io import dump_doc

        dump_doc(std.settings, {"system": {"canon": None}})
        assert load_std_paths().canon is None


class TestTheChannelTableKeepsANullArmNull:
    """``channel_default_categories`` probes a host path per source name, and four of
    those names are STANDARD bind sources — so the probe can answer ``None``.

    ⚑ THE SLOT IS THE GATE, THE VALUE IS THE PIN.  A row whose source name is absent
    from the map is skipped (that is the standalone-box omit), so a null arm must leave
    the slot PRESENT and null: skipping it would drop the row, and ``str()``-ing it would
    put the four-character path ``"None"`` in a table of host paths.
    """

    #: Every shipped ``channels:`` row names an ``@``-ref, so ``entry.get("meta_ref",
    #: sources[source])`` never reads the probed source and no shipped row can observe
    #: the map's VALUE.  Dropping the ref leaves the fallback as what answers, which is
    #: the arm the code carries for a row that reads its probe directly.
    @staticmethod
    def _rows_reading_their_probed_source(monkeypatch):
        import copy

        from kanibako.settings import core_defaults

        doc = copy.deepcopy(core_defaults._load_doc())
        for row in doc["channels"]:
            row.pop("meta_ref", None)
        monkeypatch.setattr(core_defaults, "_load_doc", lambda: doc)
        return doc

    @staticmethod
    def _primary(std, config, project_dir):
        from kanibako.settings.paths import resolve_project

        return resolve_project(std, config, str(project_dir), initialize=True)

    def _table(self, std, proj):
        from kanibako.settings.core_defaults import channel_default_categories

        return channel_default_categories(std, proj)["box.bindings.rw"]

    @pytest.mark.parametrize("key, dest", [
        ("system.channels.chat", "/home/agent/channels/chat"),
        ("system.channels.common", "/home/agent/channels/common"),
        ("system.channels.share", "/home/agent/channels/share"),
        ("system.channels.mailboxes", "/home/agent/channels/mailboxes"),
    ])
    def test_the_slot_stays_present_with_a_null_value(
        self, std, config, project_dir, monkeypatch, key, dest,
    ):
        from kanibako.settings.config_io import write_nested_key
        from kanibako.settings.config_keys import _KEY_ROUTES
        from kanibako.settings.paths import _floor_field, load_std_paths

        sections, slot = _KEY_ROUTES[key]
        write_nested_key(std.settings, sections, slot, None)
        nulled = load_std_paths()
        assert getattr(nulled, _floor_field(key)) is None, key
        proj = self._primary(nulled, config, project_dir)
        self._rows_reading_their_probed_source(monkeypatch)

        table = self._table(nulled, proj)
        # ⚑ PRESENT, not skipped: a skipped slot would omit a row the shipped manifest
        # declares for every mode.
        assert dest in table, sorted(table)
        sources = table[dest]
        assert sources == (None,), f"{key} reached the table as {sources!r}"
        assert not [s for srcs in table.values() for s in srcs if s == "None"]

    def test_an_absent_key_still_probes_a_real_path(
        self, std, config, project_dir, monkeypatch,
    ):
        """The control, and the anti-vacuity half: without a null the same row reads an
        ABSOLUTE path, so a green above is the null and not a broken probe."""
        self._rows_reading_their_probed_source(monkeypatch)
        proj = self._primary(std, config, project_dir)
        table = self._table(std, proj)
        (source,) = table["/home/agent/channels/chat"]
        assert source == str(std.channels_chat)
        assert Path(source).is_absolute()

    def test_the_shipped_rows_carry_no_probed_host_path(
        self, std, config, project_dir,
    ):
        """⚑ WITH THE MANIFEST AS SHIPPED: every source is a braced ``{key}`` ref, so the table
        never holds a host path at all — which is why the null arm needs the row above
        to be observable, and why this file does not pretend otherwise."""
        proj = self._primary(std, config, project_dir)
        table = self._table(std, proj)
        assert all(
            match_braced(s, 0) is not None for srcs in table.values() for s in srcs
        )
        assert not [s for srcs in table.values() for s in srcs if s == "None"]


def test_the_launch_reader_accepts_a_stored_null(tmp_path, std, config_file):
    """``load_system_config`` is the launch's own path-tier read: no refusal, no ``None``."""
    from kanibako.settings.config_io import dump_doc

    dump_doc(std.settings, {"system": {"canon": None}})
    resolved = load_system_config(
        config_file, data_home=std.data_home, home=tmp_path / "home",
    )
    assert "system.canon" not in resolved
    assert "system.channels.common" in resolved


class TestADerivedNullIsRefusedWhereAStoredOneIs:
    """A key can be nulled by a WRITTEN ``null`` or by a DERIVED one — its value
    references a present ``<None>`` (spec §0) — and the launch gives a null path key no
    meaning EITHER WAY (spec §2a).  Only the written one was checked, so a derived null
    silently DROPPED a key the consumer subscripts, and the refusal reached the user as
    a raw ``KeyError`` out of :func:`load_std_paths` instead of a named refusal.

    ⚑ THE CONSUMER IS WHY THIS MATTERS: ``load_std_paths`` reads five of these by
    subscript, so an omitted one is a traceback, not a default.
    """

    @staticmethod
    def _resolve(tmp_path, doc: str):
        path = _settings(tmp_path, doc)
        values = system_path_set_values(path)
        return load_std_paths, resolve_system_paths, values

    def test_a_derived_null_at_a_refusing_key_is_refused_not_dropped(self, tmp_path):
        from kanibako.errors import KanibakoError

        _load, resolve, values = self._resolve(
            tmp_path, 'system:\n  canon: null\n  cache: "@system.canon/cache"\n',
        )
        with pytest.raises(KanibakoError) as caught:
            resolve(values, data_home=tmp_path / "data", home=tmp_path / "home")
        text = str(caught.value)
        assert "system.cache" in text          # the key whose value is null
        assert "system.canon" in text          # and the key it referenced
        assert "KeyError" not in text

    def test_the_consumer_reads_it_rather_than_raising_a_bare_KeyError(self, tmp_path, std):
        """The production call site: ``load_std_paths`` subscripts ``system.cache``."""
        from kanibako.errors import KanibakoError
        from kanibako.settings.config_io import dump_doc

        dump_doc(std.settings, {"system": {
            "canon": None, "cache": "@system.canon/cache",
        }})
        with pytest.raises(KanibakoError) as caught:
            load_std_paths()
        assert "KeyError" not in str(caught.value)
        assert "system.cache" in str(caught.value)

    @pytest.mark.parametrize("key", sorted(SYSTEM_PATH_DEFAULTS))
    def test_every_key_lands_where_its_consumer_can_read_it(
        self, tmp_path, key,
    ):
        """One rule, both roads, every key — measured against the CONSUMER.

        A key ``load_std_paths`` SUBSCRIPTS has nowhere to put an absence, so a derived
        null there is REFUSED BY NAME (a raw ``KeyError`` is not a message).  Every other
        key is read with ``.get``, so the resolved table being keyed by what RESOLVED is
        exactly what a ``<None>`` means there and the key is simply absent.
        """
        from kanibako.errors import KanibakoError
        from kanibako.settings.paths import SUBSCRIPTED_SYSTEM_PATH_KEYS

        leaf = key.rsplit(".", 1)[1]
        if leaf == "canon":
            # A self-reference is a CYCLE, not a null: null the REFERENT this key reads
            # instead, and assert on the key the cycle would otherwise have hidden.
            doc = "system:\n  channels:\n    chat: null\n"
            target = "system.channels.broadcast"
        elif leaf in ("chat", "common", "mailboxes", "share", "broadcast"):
            # These hang under ``system.channels``, so the doc is NESTED and the referent
            # must be a key of the same file that is not itself derived from the target.
            doc = f'system:\n  canon: null\n  channels:\n    {leaf}: "@system.canon/x"\n'
            target = key
        else:
            doc = f'system:\n  canon: null\n  {leaf}: "@system.canon/x"\n'
            target = key
        _load, resolve, values = self._resolve(tmp_path, doc)
        if target in SUBSCRIPTED_SYSTEM_PATH_KEYS:
            with pytest.raises(KanibakoError) as caught:
                resolve(values, data_home=tmp_path / "data", home=tmp_path / "home")
            assert target in str(caught.value)
        else:
            resolved = resolve(
                values, data_home=tmp_path / "data", home=tmp_path / "home",
            )
            assert target not in resolved

    def test_a_null_chat_still_omits_the_broadcast_it_derives(self, tmp_path):
        """⚑ THE ASYMMETRY, PINNED FROM BOTH ENDS.  ``system.channels.broadcast`` REFUSES
        a written ``null`` and is read with ``.get``, so a null CHAT omits it rather than
        refusing the whole file — the broadcast log belongs to the chat it derives from.

        This is the case ``refuses_null_path_key`` alone gets wrong: using it as the
        derived road's discriminator refuses the file, which is why the discriminator is
        the consumer instead.
        """
        from kanibako.settings.config import refuses_null_path_key
        from kanibako.settings.paths import resolve_system_paths

        assert refuses_null_path_key("system.channels.broadcast")  # a WRITTEN null is refused
        _load, _resolve, values = self._resolve(
            tmp_path, "system:\n  channels:\n    chat: null\n",
        )
        resolved = resolve_system_paths(
            values, data_home=tmp_path / "data", home=tmp_path / "home",
        )
        assert "system.channels.broadcast" not in resolved
        assert "system.channels.common" in resolved  # its siblings stand
