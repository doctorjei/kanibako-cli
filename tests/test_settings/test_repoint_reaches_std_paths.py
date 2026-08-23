"""A ``system.*`` PATH repoint must REACH the thing it names.

⚑ THE DEFECT CLASS THIS EXISTS TO MAKE IMPOSSIBLE (P15): a key that is accepted,
persisted and HALF-EFFECTIVE.  ``config set system.canon=/tmp/mycanon`` writes the
system settings file, and until 2026-08-23 ``load_system_config`` read the CONFIG files
only — so the value reached the launch cascade and never reached
:class:`~kanibako.settings.paths.StandardPaths`.  ``std.canon`` kept answering the
default.  Nothing was red, because every pin asked WHERE the value was stored and none
asked whether storing it did anything.

⚑ A REFUSAL WOULD HAVE BEEN BETTER THAN THAT.  A refusal confesses; a partial success
teaches the user that the key works.  That is why this file asserts the EFFECT rather
than the destination — the destination is ``test_config_dest_parity``'s subject, and it
was already green while this was broken.

⚑ DERIVED, NEVER LISTED (P13).  The subject is every member of
``paths_defaults.SYSTEM_PATH_DEFAULTS``, so a path key added to that table is covered
here with no edit, and a key removed cannot leave a row asserting about nothing.

⚑ IT REDS ON ITS OWN EMPTINESS: the table is asserted non-empty, and every sentinel must
be accounted for individually.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kanibako.settings.config_io import write_nested_key
from kanibako.settings.paths import load_std_paths, load_system_config
from kanibako.settings.paths_defaults import SYSTEM_PATH_DEFAULTS


def _sentinel(key: str) -> str:
    """A unique, obviously-not-a-default host path for *key*."""
    # ⚑ Under /repointed, not tmp_path: a sentinel that shares a prefix with the
    # default tree could be "reached" by accident when a default happens to nest.
    return "/repointed/" + key.replace(".", "-")


@pytest.fixture
def repointed(config_file, tmp_home):
    """Every ``SYSTEM_PATH_DEFAULTS`` key repointed IN THE SYSTEM SETTINGS FILE.

    ⚑ Written to ``@config.settings`` — the file ``config set system.<key>`` writes
    (``config_dest``/``_KEY_ROUTES``), NOT the bootstrap config file.  Writing the
    config file would exercise the layer that already worked and prove nothing.
    """
    settings_file = load_std_paths().settings
    settings_file.parent.mkdir(parents=True, exist_ok=True)
    for key in SYSTEM_PATH_DEFAULTS:
        sections = tuple(key.split(".")[:-1])
        write_nested_key(settings_file, sections, key.split(".")[-1], _sentinel(key))
    return config_file


class TestTheSettingsFileFeedsThePathTier:

    def test_the_table_is_not_empty(self):
        """⚑ RED ON ITS OWN EMPTINESS — every case below iterates this table."""
        assert SYSTEM_PATH_DEFAULTS

    def test_every_repointed_key_resolves_to_the_repoint(self, repointed, tmp_home):
        """⚑ MUTATION: drop the settings-file ``raw.update`` from
        ``load_system_config`` -> every key answers its default and this dies."""
        resolved = load_system_config(
            repointed, data_home=tmp_home / "data", home=tmp_home / "home",
        )
        wrong = {
            key: str(resolved[key]) for key in SYSTEM_PATH_DEFAULTS
            if str(resolved[key]) != _sentinel(key)
        }
        assert not wrong, (
            "these system path keys were repointed in the system settings file and "
            f"resolved to something else: {wrong}"
        )

    def test_every_repoint_reaches_standard_paths(self, repointed):
        """The CONSUMER half: a repoint the resolver honours must also be what the
        rest of kanibako is handed.

        ⚑ The field MAP is not written down here — the assertion is set membership
        over ``StandardPaths``' own values, so it needs no per-key row and cannot
        drift.  A path key that deliberately reaches no ``StandardPaths`` field would
        red here, and that is the intended conversation: a repointable path nothing
        consumes is the same broken promise in a quieter form.
        """
        std = load_std_paths()
        surfaced = {str(getattr(std, f.name)) for f in _fields(std)}
        missing = sorted(
            key for key in SYSTEM_PATH_DEFAULTS if _sentinel(key) not in surfaced
        )
        assert not missing, (
            f"repointed keys that never reach StandardPaths: {missing}"
        )


class TestTheLayeringIsNotReversed:

    def test_the_settings_file_BEATS_the_config_file(self, repointed, tmp_home):
        """Spec §2g: ``system.*`` paths are SETTINGS, set at the ``system`` cascade
        level — so a leftover value in the bootstrap config file must not win."""
        key = sorted(SYSTEM_PATH_DEFAULTS)[0]
        write_nested_key(
            repointed, tuple(key.split(".")[:-1]), key.split(".")[-1], "/from-config",
        )
        resolved = load_system_config(
            repointed, data_home=tmp_home / "data", home=tmp_home / "home",
        )
        assert str(resolved[key]) == _sentinel(key)

    def test_a_config_table_in_the_settings_file_does_NOT_reach_layer_1(
        self, config_file, tmp_home,
    ):
        """Spec §1: ``config.*`` keys live in ``kanibako_config.yaml`` ALONE.

        The settings file is read for the path tier now, and the filter that keeps
        Layer 1 out of it is the thing this pins — without it, a hand-written
        ``config:`` table in a SETTINGS file would relocate the store that same file
        was found through.
        """
        before = load_std_paths().data
        settings_file = load_std_paths().settings
        settings_file.parent.mkdir(parents=True, exist_ok=True)
        write_nested_key(settings_file, ("config",), "data", "/hijacked")
        assert load_std_paths().data == before


def _fields(std) -> tuple:
    from dataclasses import fields

    return fields(std)


def test_the_probe_paths_are_all_distinct():
    """⚑ A sentinel collision would let one honoured repoint vouch for another."""
    sentinels = {_sentinel(key) for key in SYSTEM_PATH_DEFAULTS}
    assert len(sentinels) == len(SYSTEM_PATH_DEFAULTS)
    assert all(Path(s).is_absolute() for s in sentinels)
