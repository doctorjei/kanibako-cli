"""Tests for kanibako.settings.config."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from kanibako.settings.config_io import MAX_DOC_DEPTH, dump_doc, load_doc
from kanibako.errors import ConfigError
from kanibako.settings.config import (
    user_config_file,
    BOX_META_FILE,
    KanibakoConfig,
    _BOOL_FALSE,
    _BOOL_TRUE,
    _present_scalar_fields,
    bootstrap_config_paths,
    coerce_bool,
    config_file_path,
    load_config,
    read_box_enable_vault,
    read_setup_completed,
    read_agent_settings,
    write_box_enable_vault,
    write_global_config,
)
from kanibako.settings.settings_launch import load_merged_config
from kanibako.settings.bootstrap import CONFIG_PATH_DEFAULTS, SYSTEM_PATH_DEFAULTS

from tests.support.filenames import CONFIG_FILENAME


class TestLoadConfig:
    def test_defaults(self, tmp_path):
        """An absent Layer-1 file reads as an EMPTY foundation.

        ⚑ It no longer answers ``box_image`` at all: :func:`load_config` returns a
        ``BootstrapConfig``, which has no settings field to answer with.
        """
        cfg = load_config(tmp_path / "nonexistent.yaml")
        assert cfg.config_paths == {}
        assert not hasattr(cfg, "box_image")

    def test_a_written_config_is_empty_and_loads_to_the_defaults(self, tmp_path):
        """⚑ CHANGED 2026-08-26, and the assertions are INVERTED on purpose.

        This was ``test_round_trip``: it wrote a ``KanibakoConfig`` and read the box
        scalar back, and it pinned the ``[config]`` table's Layer-1 DEFAULT expressions
        (a verbatim copy of ``bootstrap.CONFIG_PATH_DEFAULTS``). There is nothing
        left to round-trip — ``write_global_config`` creates the file EMPTY and takes no
        config object, because that file cannot carry settings (Jei) and its own
        ``config.*`` foundation is already declared. What loads is the defaults.
        """
        path = tmp_path / "test.yaml"
        write_global_config(path)
        assert load_config(path).config_paths == {}
        assert load_merged_config().box_image == KanibakoConfig().box_image

    def test_empty_file_resolves_identically_to_the_old_verbatim_defaults(
        self, tmp_path,
    ):
        """The EMPTY file and the old three-table file resolve to the SAME paths.

        This is what makes dropping the tables safe rather than merely tidy, and it
        is the protection the deleted ``test_emits_channelroot_not_stale_channels_leaf``
        really provided: ``resolve_system_paths`` takes ``CONFIG_PATH_DEFAULTS`` /
        ``SYSTEM_PATH_DEFAULTS`` as its ``LevelView`` defaults and layers STORED values
        over them, so a file that stored exactly those defaults never moved a path.
        The renamed ``channelroot`` leaf (198e6ea) is pinned by the equality: a stale
        bare ``channels`` leaf on either side would break it.
        """
        from kanibako.settings.paths import resolve_system_paths

        sparse = tmp_path / "empty.yaml"
        write_global_config(sparse)

        # The Layer-1 half of the file as it was written until 2026-08-26: every
        # ``config.*`` default, verbatim.  ⚑ ITS OTHER TWO TABLES ARE NOT HERE, and that
        # is the next test: since 2026-08-31 a ``system:`` or ``box:`` table in this file
        # is not merely inert, it REFUSES.
        verbose = tmp_path / "verbose.yaml"
        dump_doc(verbose, {
            "config": {
                k.split(".", 1)[1]: v for k, v in CONFIG_PATH_DEFAULTS.items()
            },
        })

        kw = {"data_home": tmp_path / "data", "home": tmp_path / "home"}
        assert resolve_system_paths(load_config(sparse).config_paths, **kw) == \
            resolve_system_paths(load_config(verbose).config_paths, **kw)
        # ...and the merged scalar tier agrees too: the box defaults were the third copy.
        assert load_merged_config().box_image == \
            load_merged_config().box_image

    def test_the_old_three_table_file_refuses_and_names_both_tables(self, tmp_path):
        """The file as it was written until 2026-08-26 is now an ERROR, not an ignore.

        ⚑ THE OTHER HALF of the test above, and the reason its ``system:``/``box:``
        tables moved out of the comparison: the two tables were settings, and a user who
        still has them was silently running something other than what they read.
        """

        verbose = tmp_path / "verbose.yaml"
        dump_doc(verbose, {
            "config": {"data": "/x"},
            "system": {
                k.split(".", 1)[1]: v
                for k, v in SYSTEM_PATH_DEFAULTS.items()
                if "." not in k.split(".", 1)[1]
            },
            "box": {"image": "planted:1"},
        })
        with pytest.raises(ConfigError) as exc:
            load_config(verbose)
        assert str(verbose) in str(exc.value)
        assert "box.image" in str(exc.value)
        assert "system.cache" in str(exc.value)

    def test_channelroot_round_trips_through_load_std_paths(self, tmp_home):
        """A config written by write_global_config resolves cleanly end-to-end:
        the renamed channelroot leaf AND the channels.* children all resolve."""
        from kanibako.settings.paths import load_std_paths

        cf = tmp_home / "config" / CONFIG_FILENAME
        write_global_config(cf)
        std = load_std_paths(load_config(cf))
        # channelroot leaf -> the channels root dir; children hang off it.
        assert std.channels == std.data_path / "channels"
        assert std.channels_common == std.channels / "common"
        assert std.channels_broadcast == std.channels / "chat" / "broadcast.md"

    def test_a_present_null_is_never_the_default(self, tmp_path):
        """A settings file holding ``box: image: null`` does NOT resolve to the default.

        # keyspec §2h, "Values are installed VERBATIM — including ``None``": "KEPT ``None``
        # for a scalar leaf — the consumer reads None, never the key's default."  §2b
        # declares no ``<None>`` for ``box.image``, so there is no consumer a null could
        # mean anything to and the launch refuses rather than substituting the default.
        """
        from kanibako.settings.settings_resolve import SettingsError

        box_file = tmp_path / BOX_META_FILE
        box_file.write_text("box:\n  image: null\n")
        with pytest.raises(SettingsError) as excinfo:
            load_merged_config(box_file)
        assert "box.image" in str(excinfo.value)
        assert str(box_file) in str(excinfo.value)

    def test_an_empty_value_is_the_same_present_null(self, tmp_path):
        """An empty ``image:`` parses to the same ``None`` an explicit ``null`` does, so
        it answers the same way — it is the SAME present ``None``, not a third idiom."""
        from kanibako.settings.settings_resolve import SettingsError

        box_file = tmp_path / BOX_META_FILE
        box_file.write_text("box:\n  image:\n")
        with pytest.raises(SettingsError) as excinfo:
            load_merged_config(box_file)
        assert "box.image" in str(excinfo.value)

    def test_config_table_populates_config_paths(self, tmp_path):
        """[config] keys land in cfg.config_paths (full dotted names)."""
        path = tmp_path / "sys.yaml"
        path.write_text('config:\n  agents: "/x"\n')
        cfg = load_config(path)
        assert cfg.config_paths == {"config.agents": "/x"}


class TestLayer1FileCannotHaveSettings:
    """``kanibako.cfg`` carries ``config.*`` and NOTHING else — on the READ
    side as well as the write side.

    ⚑⚑ Jei, 2026-08-26: *"kanibako_config.yaml <-- cannot have settings. Period."*
    Stopping the WRITE is not enough while the code still reads settings back out of
    that file: a hand-written table, or one left behind by an older build, would go on
    silently overriding the declared defaults. Spec §1 gives Layer 1 the bootstrap
    ``config.*`` paths alone; spec §2b/§2g put the box and system SETTINGS in the
    cascade files.

    ⚑⚑ AND SINCE 2026-08-31 THE ANSWER IS A REFUSAL, NOT AN IGNORE (Jei). Dropping the
    table in silence left a user running a different image than their file said, with
    nothing anywhere reporting the difference. The refusal names the file and the keys.
    """

    def test_a_box_table_in_the_layer1_file_refuses_and_names_it(self, tmp_path):
        """The planted table stops the read, naming the file and BOTH keys."""
        cf = user_config_file()
        cf.parent.mkdir(parents=True, exist_ok=True)
        cf.write_text('box:\n  image: "layer1:planted"\n  share_images: true\n')
        with pytest.raises(ConfigError) as exc:
            load_merged_config()
        assert str(cf) in str(exc.value)
        assert "box.image" in str(exc.value)
        assert "box.share_images" in str(exc.value)

    def test_the_undeclared_flat_spelling_refuses_there_too(self, tmp_path):
        """A top-level ``box_image:`` is not a key anywhere, and Layer 1 says so by name.

        ⚑ It USED to resolve identically to the declared ``box: image:`` — two spellings
        for one key, one of them undeclared (spec §0).
        """
        cf = tmp_path / CONFIG_FILENAME
        cf.write_text('box_image: "layer1:planted"\n')
        with pytest.raises(ConfigError) as exc:
            load_config(cf)
        assert "box_image" in str(exc.value)

    @pytest.mark.parametrize("text", [
        "box:\n",             # YAML parses this to None, NOT {}
        "box: {}\n",          # the explicit empty mapping
        "box:\n  sub: {}\n",  # a table whose only leaf flattens away
    ])
    def test_every_empty_table_spelling_refuses_alike(self, tmp_path, text):
        """🛑 THE THREE EMPTY SPELLINGS AGREE, and two of them used not to.

        A user reads all three as "an empty ``box:`` table". ``box:`` alone was refused as a
        bare ``box``; the other two were silently ACCEPTED — two forms meaning one thing
        giving opposite answers (Convention 0), and the accepting arm was the only thing in
        this rule that behaved like a carve-out. An empty table is still a settings table in
        a file that may not carry one, so all three refuse, named by the table.
        """
        cf = tmp_path / CONFIG_FILENAME
        cf.write_text(text)
        with pytest.raises(ConfigError) as exc:
            load_config(cf)
        assert "box" in str(exc.value)

    def test_a_real_settings_tier_still_wins(self, tmp_path):
        """🛑 The BOX tier still sets the value — the Layer-1 read is what went away."""
        cf = tmp_path / CONFIG_FILENAME
        write_global_config(cf)
        box_file = tmp_path / BOX_META_FILE
        box_file.write_text('box:\n  image: "from-the-box-tier"\n')
        merged = load_merged_config(box_file)
        assert merged.box_image == "from-the-box-tier"

    def test_the_config_table_alone_is_accepted(self, tmp_path):
        """The foundation still loads — the file's whole job, and the only thing in it."""
        cf = tmp_path / CONFIG_FILENAME
        cf.write_text('config:\n  agents: "/x"\n')
        assert bootstrap_config_paths(cf) == {"config.agents": "/x"}

    def test_a_system_table_in_the_layer1_file_refuses_the_path_resolve(self, tmp_path):
        """``load_system_config`` reads the path tier's Layer-2 half from the SETTINGS
        file alone — and refuses a ``system:`` table in the CONFIG file rather than
        quietly resolving around it.

        Before 2026-08-26 such a table entered the resolve as a real (lowest) layer,
        which made that file a settings source in the one place it most mattered: where
        every host path is decided. It was then dropped in silence, which is what the
        2026-08-31 ruling replaced.
        """
        from kanibako.settings.paths import load_system_config

        home = tmp_path / "home"
        data_home = tmp_path / "data"
        config_home = tmp_path / "config"
        config_home.mkdir(parents=True)
        cf = config_home / CONFIG_FILENAME
        cf.write_text(
            f'config:\n  data: "{data_home}/kanibako"\n'
            'system:\n  cache: "/planted-from-layer1"\n'
        )
        with pytest.raises(ConfigError) as exc:
            load_system_config(cf, data_home=data_home, home=home)
        assert "system.cache" in str(exc.value)

        # ...while the SETTINGS file's own row IS honored — the route that replaced it.
        cf.write_text(f'config:\n  data: "{data_home}/kanibako"\n')
        resolved = load_system_config(cf, data_home=data_home, home=home)
        ssp = resolved["config.settings"]
        ssp.parent.mkdir(parents=True, exist_ok=True)
        ssp.write_text('system:\n  cache: "/from-the-settings-file"\n')
        resolved = load_system_config(cf, data_home=data_home, home=home)
        assert str(resolved["system.cache"]) == "/from-the-settings-file"


class TestLayer1ExoticKeyTypes:
    """A YAML key need not be a STRING, and the refusal must survive one that is not.

    🛑 ``sorted`` and ``"\\n  ".join`` both assume ``str``, so ``1: x`` / ``true: x`` /
    ``~: x`` raised a ``TypeError`` TRACEBACK instead of the named refusal — in the one
    file whose whole point is that a hand-editing user finds out. The population that
    reaches this is exactly the population the refusal exists to help.
    """

    @pytest.mark.parametrize("text,named", [
        ("1: x\n", "1"),
        ("true: x\n", "True"),
        ("~: x\n", "None"),
        ("1: x\nbox: y\n", "box"),  # the SORT arm: a str and an int in one document
    ])
    def test_a_non_string_top_level_key_refuses_by_name(self, tmp_path, text, named):
        cf = tmp_path / CONFIG_FILENAME
        cf.write_text(text)
        with pytest.raises(ConfigError) as exc:
            bootstrap_config_paths(cf)
        assert named in str(exc.value)


class TestLayer1UndeclaredConfigKeys:
    """Spec §1: *"The Layer-1 set is exactly the config keys in the table below."*

    ⚑⚑ THE ASYMMETRY IS THE DEFECT. Starting the walk at the ``config:`` table makes the
    ``config.`` PREFIX unfakeable and says nothing about the TAIL, so a bare ``nonsense``
    was refused loudly while ``config.nonsense`` was accepted, handed to
    ``resolve_config_paths`` — which iterates the DECLARED table — and dropped unread.
    """

    @pytest.mark.parametrize("text,named", [
        ("config:\n  nonsense: /x\n", "config.nonsense"),
        ("config:\n  box:\n    image: SMUGGLE\n", "config.box.image"),
    ])
    def test_an_undeclared_leaf_inside_config_refuses_by_name(self, tmp_path, text, named):
        cf = tmp_path / CONFIG_FILENAME
        cf.write_text(text)
        with pytest.raises(ConfigError) as exc:
            bootstrap_config_paths(cf)
        assert named in str(exc.value)

    def test_both_spellings_of_one_typo_now_agree(self, tmp_path):
        """The finding itself: inside and outside ``config:``, one rule, one answer."""
        cf = tmp_path / CONFIG_FILENAME
        for text in ("nonsense: /x\n", "config:\n  nonsense: /x\n"):
            cf.write_text(text)
            with pytest.raises(ConfigError):
                bootstrap_config_paths(cf)

    def test_the_refusal_names_the_declared_set(self, tmp_path):
        """⚑ DERIVED (P13): the message enumerates :data:`CONFIG_PATH_DEFAULTS`, so a key
        joining §1 joins the message rather than outdating a list."""
        cf = tmp_path / CONFIG_FILENAME
        cf.write_text("config:\n  nonsense: /x\n")
        with pytest.raises(ConfigError) as exc:
            bootstrap_config_paths(cf)
        for key in CONFIG_PATH_DEFAULTS:
            assert key in str(exc.value)

    def test_the_admitted_set_is_the_keyspaces_own(self):
        """🛑 THE COUPLING THE REFUSAL RESTS ON, asserted where it is relied upon.

        ``bootstrap_config_paths`` admits :data:`CONFIG_PATH_DEFAULTS` — the table
        ``resolve_config_paths`` iterates, so *accepted here* ⇒ *resolved there*. That is
        only a CLOSED-KEYSPACE refusal while the same six spellings are what the keyspace
        declares. Both are pinned to the manifest separately; nothing pinned them to EACH
        OTHER, and a divergence would re-open the hole this closed.
        """
        from kanibako.settings.settings_keyspace import DECLARED_CONFIG_LEAVES

        assert set(CONFIG_PATH_DEFAULTS) == {
            f"config.{leaf}" for leaf in DECLARED_CONFIG_LEAVES
        }

    @pytest.mark.parametrize("key", sorted(CONFIG_PATH_DEFAULTS))
    def test_every_declared_key_is_still_read(self, tmp_path, key):
        """Anti-vacuity: the widened refusal must not have caught a real bootstrap key."""
        cf = tmp_path / CONFIG_FILENAME
        cf.write_text(f'config:\n  {key.split(".", 1)[1]}: "/x"\n')
        assert bootstrap_config_paths(cf) == {key: "/x"}

    def test_a_non_table_config_entry_refuses(self, tmp_path):
        """``config: /x`` used to yield ``{}`` in SILENCE — the whole store back at its
        default location for a user whose one line meant to move it."""
        cf = tmp_path / CONFIG_FILENAME
        cf.write_text("config: /x\n")
        with pytest.raises(ConfigError) as exc:
            bootstrap_config_paths(cf)
        assert "/x" in str(exc.value)

    @pytest.mark.parametrize("key", sorted(CONFIG_PATH_DEFAULTS))
    def test_a_null_config_leaf_refuses_by_name(self, tmp_path, key):
        """A ``null`` path used to read as the text ``None`` and be refused as a bare
        relative path — a directory the user never wrote.

        MUTATION: drop the ``_refuse_null_paths`` call in ``bootstrap_config_paths`` and
        this returns ``{key: "None"}``.
        """
        cf = tmp_path / CONFIG_FILENAME
        cf.write_text(f'config:\n  {key.split(".", 1)[1]}: null\n')
        with pytest.raises(ConfigError) as exc:
            bootstrap_config_paths(cf)
        assert str(cf) in str(exc.value)
        assert key in str(exc.value)

    @pytest.mark.parametrize("text", ["", "config:\n", "config: {}\n"])
    def test_the_empty_spellings_all_read_as_an_empty_foundation(self, tmp_path, text):
        """🛑 THE COUNTERWEIGHT to the refusal above: ``write_global_config`` writes ZERO
        bytes, so absent, null and ``{}`` must agree — a ``config:`` carrying nothing is
        the created file's own state, not a malformed entry."""
        cf = tmp_path / CONFIG_FILENAME
        cf.write_text(text)
        assert bootstrap_config_paths(cf) == {}


class TestBoxScalarDefaultsFloor:
    """The declared-default floor — SEPARATED from the file read, not deleted."""

    def test_it_is_the_declared_defaults(self):
        from kanibako.settings.config import box_scalar_defaults_floor

        floor = box_scalar_defaults_floor()
        assert floor["box.image"] == KanibakoConfig().box_image
        assert floor["box.share_images"] is False

    def test_box_shell_is_a_present_none(self):
        """``box.shell``'s field default IS the declared ``<None>``, floored PRESENT.

        Spec §2b ``box.shell | <None>``: a declared ``<None>`` is SUPPLIED, so a
        whole-value ``@box.shell`` resolves instead of dangling.  ``""`` would not do:
        ``build_launch_snapshot`` drops a ``""`` default as a suppression.
        """
        from kanibako.settings.config import box_scalar_defaults_floor

        assert KanibakoConfig().box_shell is None
        floor = box_scalar_defaults_floor()
        assert "box.shell" in floor
        assert floor["box.shell"] is None

    def test_a_floored_none_leaves_the_flat_shell_to_auto_detect(self, tmp_path):
        """The present ``None`` lands AS ``None``, never as the string ``"None"``.

        ``launch.shells.resolve_box_shell`` reads ``box_shell`` and runs it; a stringified
        ``None`` would launch a program called ``None``.
        """
        cfg = load_merged_config()
        assert cfg.box_shell is None

    def test_false_survives_because_it_is_a_value(self):
        """⚑ ``False == ""`` is False — the suppression must not eat a real bool."""
        from kanibako.settings.config import box_scalar_defaults_floor

        assert "box.share_images" in box_scalar_defaults_floor()


class TestSetupVersionConstant:
    """SETUP_BCV/SETUP_FCV are PEP-440 strings obeying BCV <= FCV <= CurrentVer."""

    def test_constants_present_and_parseable(self):
        from packaging.version import Version

        from kanibako import SETUP_BCV, SETUP_FCV, __version__

        # Present and PEP-440 parseable (Version() raises on garbage); exact
        # values move per release, so assert the invariant, not the literals.
        assert Version(SETUP_BCV) and Version(SETUP_FCV)
        # Invariant: BCV <= FCV <= CurrentVer (compared by base version so a
        # dev/rc build of the same base counts as the released base).
        bcv = Version(Version(SETUP_BCV).base_version)
        fcv = Version(Version(SETUP_FCV).base_version)
        cur = Version(Version(__version__).base_version)
        assert bcv <= fcv <= cur


class TestWriteGlobalConfigCreatesAnEmptyFile:
    """``write_global_config`` creates the Layer-1 file EMPTY, always (Jei, 2026-08-26).

    First: *"these should be built-in defaults; the global settings file should be an
    empty file at create time. unless non-defaults are somehow are added by the user."*
    Then, hardened: *"kanibako_config.yaml <-- cannot have settings. Period."* — which
    settles the "unless" for this writer, since the only non-defaults it ever wrote were
    the box SETTINGS.
    """

    def test_create_time_file_is_empty_and_exists(self, tmp_path):
        """Empty, and PRESENT — existence is what ``cli._ensure_initialized`` tests on."""
        cf = tmp_path / CONFIG_FILENAME
        write_global_config(cf)
        assert cf.exists(), "the file must still be CREATED; an absent one re-runs init"
        assert cf.read_text() == "", cf.read_text()
        assert load_doc(cf) == {}

    def test_zero_bytes_not_an_empty_mapping(self, tmp_path):
        """🛑 NOT ``{}``. This file is the hand-edit surface the ``config.*`` refusal
        sends users to, and a leading ``{}`` makes an appended ``config:`` block a
        YAML error."""
        import yaml

        cf = tmp_path / CONFIG_FILENAME
        write_global_config(cf)
        assert cf.read_bytes() == b""
        # The thing the ``{}`` form would break: append and re-parse.
        cf.write_text(cf.read_text() + 'config:\n  agents: "/x"\n')
        assert yaml.safe_load(cf.read_text()) == {"config": {"agents": "/x"}}
        assert load_config(cf).config_paths == {"config.agents": "/x"}

    def test_it_takes_no_config_object(self):
        """⚑ THE RULING IS IN THE SIGNATURE. A ``KanibakoConfig`` is settings, so there
        is nothing it could legitimately contribute — and a parameter that is accepted
        and ignored is a silent no-op for every caller that passes one."""
        import inspect

        params = list(inspect.signature(write_global_config).parameters)
        assert params == ["path"], params

    def test_no_table_of_any_kind_is_emitted(self, tmp_path):
        """The ``config:``, ``system:`` and ``box:`` tables are all GONE.

        ⚑ P7 rides here too: ``box.agent_name`` is RETIRED, and a BOX key had no
        business in the CONFIG file even while it existed (migration M-4).
        """
        cf = tmp_path / CONFIG_FILENAME
        write_global_config(cf)
        assert load_doc(cf) == {}


class TestReadSetupCompleted:
    """read_setup_completed: the raw ``system.setup_completed`` reader (W1 gate).

    ⚑ ITS FILE IS THE SYSTEM SETTINGS FILE since 2026-08-26 (Jei: "there is no reason
    whatsoever that ``system.setup_completed`` should go in the config. It should not.
    It should go in the global settings file") — which is what spec §2g always declared.
    The variable is named ``ssp`` here for that reason: passing ``kanibako.cfg``
    is now the wrong file, and nothing in the shipped code does it.
    """

    def test_reads_stored_string(self, tmp_path):
        from kanibako.settings.config_interface import write_system_value

        ssp = tmp_path / "settings.yaml"
        write_system_value(ssp, "setup_completed", "1.6.0")
        assert read_setup_completed(ssp) == "1.6.0"

    def test_absent_key_returns_none(self, tmp_path):
        ssp = tmp_path / "settings.yaml"
        ssp.write_text("system:\n  agent: claude\n")  # a [system] table, no marker
        assert read_setup_completed(ssp) is None

    def test_missing_file_returns_none(self, tmp_path):
        """A FRESH install has no settings file at all — "setup never run", not "done"."""
        assert read_setup_completed(tmp_path / "nope.yaml") is None
        assert read_setup_completed(None) is None

    def test_empty_value_returns_none(self, tmp_path):
        ssp = tmp_path / "settings.yaml"
        ssp.write_text("system:\n  setup_completed: ''\n")
        assert read_setup_completed(ssp) is None

    def test_a_marker_in_the_old_config_file_is_not_read(self, tmp_path):
        """ONE location, no fallback read — the old Layer-1 slot is not consulted.

        A dual-location reader would be the deprecation window this release refuses;
        the file it names is the file it reads, and that is the whole contract.
        """
        from kanibako.settings.config_interface import write_system_value

        cf = tmp_path / CONFIG_FILENAME
        ssp = tmp_path / "settings.yaml"
        write_system_value(cf, "setup_completed", "1.8.0")
        assert read_setup_completed(ssp) is None

    def test_init_writes_no_setup_completed_anywhere(self, tmp_path):
        """Fresh init leaves the marker ABSENT — no marker, no 'none'."""
        cf = tmp_path / CONFIG_FILENAME
        write_global_config(cf)
        assert load_doc(cf) == {}


class TestRetiredTemplatesStamp:
    """R-38: the ``system.templates_stamp`` MECHANISM is gone, the LEAF is inert.

    The reader (``read_templates_stamp``), the gate (``template_staleness_gate``)
    and every writer were deleted.  A ``system: templates_stamp`` leaf left in the
    Layer-1 file is REFUSED BY NAME (that file may not carry a ``system:`` table), so
    the contract these tests pin is: the symbols are GONE, the Layer-1 orphan refuses,
    and the setup gate reads the same answer with the leaf beside its marker.
    """

    def test_symbols_are_gone(self):
        from kanibako.settings import config as config_mod

        assert not hasattr(config_mod, "read_templates_stamp")
        assert not hasattr(config_mod, "template_staleness_gate")

    def test_the_leaf_reaches_no_dataclass_field(self):
        """Neither flat object has a home for it — the symbols went, so did the slot."""
        from dataclasses import fields

        from kanibako.settings.config import BootstrapConfig, KanibakoConfig

        names = {fld.name for fld in fields(KanibakoConfig)}
        names |= {fld.name for fld in fields(BootstrapConfig)}
        assert "templates_stamp" not in names

    def test_the_orphan_in_the_LAYER1_file_refuses_by_name(self, tmp_path):
        """⚑ CHANGED 2026-08-31, and the change is the ruling.

        The orphan used to land in the raw ``config_paths`` set and reach no consumer —
        "orphaned-ignored". A ``system:`` table is a SETTINGS table wherever it sits, and
        the Layer-1 file may not carry one, so the read now names it instead of carrying
        it inertly. ⚑ The orphan in a SETTINGS file is a different question and stays
        §2.47's (an undeclared key stops the command).
        """
        from kanibako.settings.config import load_config
        from kanibako.settings.config_interface import write_system_value

        cf = tmp_path / CONFIG_FILENAME
        write_global_config(cf)
        write_system_value(cf, "templates_stamp", "deadbeef")

        with pytest.raises(ConfigError) as exc:
            load_config(cf)
        assert "system.templates_stamp" in str(exc.value)

    def test_a_clean_config_still_resolves_every_path(self, tmp_path):
        """The other half: with the orphan gone the resolve is untouched."""
        from kanibako.settings.config import load_config
        from kanibako.settings.paths import resolve_system_paths

        clean = tmp_path / "clean.yaml"
        write_global_config(clean)
        kw = {"data_home": tmp_path / "data", "home": tmp_path / "home"}
        resolved = resolve_system_paths(load_config(clean).config_paths, **kw)
        assert set(resolved) >= set(SYSTEM_PATH_DEFAULTS)

    def test_orphaned_leaf_does_not_disturb_the_setup_gate(self, tmp_path):
        """The one gate that remains reads the same answer with the orphan present.

        The orphan sits beside the marker in the SYSTEM SETTINGS file — the one file
        the gate reads (:func:`read_setup_completed`).

        No release wrote this leaf here; it pins only that the raw pre-cascade gate
        reads its one leaf. The settings LOAD refuses the undeclared key separately.
        """
        from packaging.version import Version

        import kanibako
        from kanibako.settings.config import setup_compat_gate
        from kanibako.settings.config_interface import write_system_value

        ssp = tmp_path / "settings.yaml"
        write_system_value(
            ssp, "setup_completed", Version(kanibako.__version__).base_version
        )
        write_system_value(ssp, "templates_stamp", "deadbeef")
        assert setup_compat_gate(ssp) is None  # == band: no nudge, no raise


class TestSetupCompatGate:
    """setup_compat_gate: the 5-band setup/config compatibility gate.

    The shipped constants are BCV == FCV == CurrentVer == 1.8.0 (verified
    2026-08-02, after the R-38 rider bumped BCV), which collapses several bands to
    empty ranges.  To exercise EACH band independently of the
    build version, most tests patch ``kanibako.__version__`` (CurrentVer) and the
    ``SETUP_BCV``/``SETUP_FCV`` module constants — the gate imports them inside
    the function, so patching the ``kanibako`` module attributes is honored.
    """

    # --- helpers -----------------------------------------------------------
    def _gate(self):
        from kanibako.settings.config import setup_compat_gate

        return setup_compat_gate

    def _marker(self, tmp_path, value):
        """Plant *value* in the SYSTEM SETTINGS file — the file production hands the gate."""
        from kanibako.settings.config_interface import write_system_value

        ssp = tmp_path / "settings.yaml"
        write_system_value(ssp, "setup_completed", value)
        return ssp

    def _patch_bands(self, *, version, bcv, fcv):
        """Patch CurrentVer + the two constants on the ``kanibako`` package."""
        from unittest.mock import patch

        import kanibako

        return [
            patch.object(kanibako, "__version__", version),
            patch.object(kanibako, "SETUP_BCV", bcv),
            patch.object(kanibako, "SETUP_FCV", fcv),
        ]

    # --- absent / unparseable ---------------------------------------------
    def test_absent_marker_nudges_setup(self, tmp_path):
        ssp = tmp_path / "settings.yaml"
        ssp.write_text("system:\n  agent: claude\n")  # a system table, no marker
        assert self._gate()(ssp) == (
            "kanibako isn't set up yet. Run 'kanibako setup' to get started."
        )

    def test_missing_file_nudges_setup(self, tmp_path):
        gate = self._gate()
        assert gate(tmp_path / "nope.yaml") == (
            "kanibako isn't set up yet. Run 'kanibako setup' to get started."
        )
        assert gate(None) == (
            "kanibako isn't set up yet. Run 'kanibako setup' to get started."
        )

    def test_unparseable_marker_no_nudge_no_error(self, tmp_path):
        """A hand-edited unparseable marker is treated as present (no nag/error)."""
        ssp = self._marker(tmp_path, "custom-build")
        assert self._gate()(ssp) is None

    # --- band: ConfigVer == CurrentVer (no-op) -----------------------------
    def test_current_marker_no_op(self, tmp_path):
        from packaging.version import Version

        import kanibako

        # Marker == the current build's base version → == band → no-op.
        ssp = self._marker(tmp_path, Version(kanibako.__version__).base_version)
        assert self._gate()(ssp) is None

    def test_dev_marker_of_current_base_no_op(self, tmp_path):
        """A dev build of the current base reads as == (base-version compare)."""
        from packaging.version import Version

        import kanibako

        base = Version(kanibako.__version__).base_version
        ssp = self._marker(tmp_path, f"{base}.dev26")
        assert self._gate()(ssp) is None

    # --- band: ConfigVer > CurrentVer (ERROR) ------------------------------
    def test_newer_than_build_raises(self, tmp_path):
        from packaging.version import Version

        import kanibako
        from kanibako.errors import ConfigError

        # A version strictly greater than the build base → "from the future".
        newer = f"{Version(kanibako.__version__).major + 1}.0.0"
        assert Version(newer) > Version(Version(kanibako.__version__).base_version)
        ssp = self._marker(tmp_path, newer)
        with pytest.raises(ConfigError) as exc:
            self._gate()(ssp)
        assert "newer kanibako" in str(exc.value)

    # --- band: FCV <= ConfigVer < CurrentVer (SILENT BUMP) -----------------
    def test_forward_compatible_silently_bumps(self, tmp_path):
        from kanibako.settings.config import read_setup_completed

        ssp = self._marker(tmp_path, "1.6.0")
        # Pretend the build advanced to 1.8.0 with BCV/FCV still 1.6.0.
        patches = self._patch_bands(version="1.8.0", bcv="1.6.0", fcv="1.6.0")
        for p in patches:
            p.start()
        try:
            assert self._gate()(ssp) is None  # silent, no message
            # SIDE EFFECT: marker rewritten forward to CurrentVer.
            assert read_setup_completed(ssp) == "1.8.0"
        finally:
            for p in patches:
                p.stop()

    def test_silent_bump_persists_then_no_op(self, tmp_path):
        """After a bump, a second run hits the == band (no further write)."""
        from kanibako.settings.config import read_setup_completed

        ssp = self._marker(tmp_path, "1.6.0")
        patches = self._patch_bands(version="1.8.0", bcv="1.6.0", fcv="1.6.0")
        for p in patches:
            p.start()
        try:
            gate = self._gate()
            assert gate(ssp) is None
            assert read_setup_completed(ssp) == "1.8.0"
            # Re-run: now ConfigVer == CurrentVer → no-op, marker unchanged.
            assert gate(ssp) is None
            assert read_setup_completed(ssp) == "1.8.0"
        finally:
            for p in patches:
                p.stop()

    def test_silent_bump_failure_does_not_raise(self, tmp_path):
        """A failed bump WRITE must fall through (return None), never block."""
        from unittest.mock import patch

        from kanibako.settings.config import read_setup_completed

        ssp = self._marker(tmp_path, "1.6.0")
        patches = self._patch_bands(version="1.8.0", bcv="1.6.0", fcv="1.6.0")
        for p in patches:
            p.start()
        try:
            with patch(
                "kanibako.settings.config_interface.write_system_value",
                side_effect=OSError("read-only"),
            ):
                assert self._gate()(ssp) is None  # swallowed, no raise
            # Marker stays unchanged (the bump failed).
            assert read_setup_completed(ssp) == "1.6.0"
        finally:
            for p in patches:
                p.stop()

    # --- band: BCV <= ConfigVer < FCV (NUDGE) ------------------------------
    def test_between_bcv_and_fcv_nudges(self, tmp_path):
        ssp = self._marker(tmp_path, "1.6.0")
        # build 1.8.0, BCV 1.5.0, FCV 1.7.0 → 1.6.0 is in [BCV, FCV) → nudge.
        patches = self._patch_bands(version="1.8.0", bcv="1.5.0", fcv="1.7.0")
        for p in patches:
            p.start()
        try:
            assert self._gate()(ssp) == (
                "kanibako setup is out of date — re-run 'kanibako setup'."
            )
            # NUDGE band does NOT rewrite the marker.
            from kanibako.settings.config import read_setup_completed

            assert read_setup_completed(ssp) == "1.6.0"
        finally:
            for p in patches:
                p.stop()

    # --- band: ConfigVer < BCV (ERROR) -------------------------------------
    @staticmethod
    def _below_bcv():
        """A version string strictly below the live SETUP_BCV base version."""
        from packaging.version import Version

        import kanibako

        bcv = Version(kanibako.SETUP_BCV)
        below = f"{bcv.major - 1}.0.0" if bcv.major >= 1 else f"0.0.{bcv.micro}"
        if not Version(below) < Version(bcv.base_version):
            below = "0.0.1"
        assert Version(below) < Version(bcv.base_version)
        return below

    def test_older_than_bcv_raises(self, tmp_path):
        from kanibako.errors import ConfigError

        # A version strictly below the live BCV → too old → ERROR.
        ssp = self._marker(tmp_path, self._below_bcv())
        with pytest.raises(ConfigError) as exc:
            self._gate()(ssp)
        assert "too old to auto-update" in str(exc.value)

    def test_dev_marker_of_older_base_raises(self, tmp_path):
        """A dev build of a genuinely older base is still < BCV → ERROR."""
        from kanibako.errors import ConfigError

        ssp = self._marker(tmp_path, f"{self._below_bcv()}.dev1")  # base < BCV
        with pytest.raises(ConfigError):
            self._gate()(ssp)

    # --- the R-38 rider: a 1.7.x-era config is HARD-BLOCKED, not nudged ----
    @pytest.mark.parametrize("marker", ["1.7.0", "1.7.2", "1.7.2.dev4", "1.7.0rc1"])
    def test_v1_7_era_marker_is_hard_blocked(self, tmp_path, marker):
        """A host set up by any 1.7.x build must RAISE, using the LIVE constants.

        ⚑ The load-bearing half of R-38.  With ``SETUP_BCV`` still at 1.6.0 a 1.7.x
        marker landed in the BCV..FCV NUDGE band, and the M-11 template-root
        restructure — previously hard-blocked by the now-deleted template-staleness
        gate — would have degraded to a non-blocking message.  Deliberately UNPATCHED
        (no ``_patch_bands``): it is the SHIPPED constants that must produce the hard
        band, so this fails if a future release lowers BCV back below 1.8.0 without
        re-thinking the upgrade path.
        """
        from kanibako.errors import ConfigError

        ssp = self._marker(tmp_path, marker)
        with pytest.raises(ConfigError) as exc:
            self._gate()(ssp)
        assert "too old to auto-update" in str(exc.value)


class TestMergedConfig:
    def test_project_overrides_global(self, tmp_path):
        global_path = tmp_path / "global.yaml"
        project_path = tmp_path / BOX_META_FILE

        write_global_config(global_path)
        dump_doc(project_path, {"box": {"image": "my-image:v2"}})

        merged = load_merged_config(project_path)
        assert merged.box_image == "my-image:v2"

    def test_cli_overrides_all(self, tmp_path):
        global_path = tmp_path / "global.yaml"
        project_path = tmp_path / BOX_META_FILE

        write_global_config(global_path)
        dump_doc(project_path, {"box": {"image": "my-image:v2"}})

        merged = load_merged_config(project_path,
            cli_overrides={"box_image": "cli-image:v3"},
        )
        assert merged.box_image == "cli-image:v3"

    def test_workset_path_none_is_byte_identical(self, tmp_path):
        """Omitting workset_path must reproduce the pre-P2.2 global+project merge."""
        global_path = tmp_path / "global.yaml"
        project_path = tmp_path / BOX_META_FILE

        write_global_config(global_path)
        dump_doc(project_path, {"box": {"image": "my-image:v2"}})

        baseline = load_merged_config(project_path)
        with_none = load_merged_config(project_path, workset_path=None)
        assert with_none == baseline
        assert with_none.box_image == "my-image:v2"

    def test_workset_overrides_global(self, tmp_path):
        global_path = tmp_path / "global.yaml"
        workset_path = tmp_path / "ws-config.yaml"

        write_global_config(global_path)
        dump_doc(workset_path, {"box": {"image": "ws-image:v1"}})

        merged = load_merged_config(workset_path=workset_path)
        assert merged.box_image == "ws-image:v1"

    def test_project_overrides_workset(self, tmp_path):
        global_path = tmp_path / "global.yaml"
        workset_path = tmp_path / "ws-config.yaml"
        project_path = tmp_path / BOX_META_FILE

        write_global_config(global_path)
        dump_doc(workset_path, {"box": {"image": "ws-image:v1"}})
        dump_doc(project_path, {"box": {"image": "proj-image:v2"}})

        merged = load_merged_config(project_path, workset_path=workset_path
        )
        assert merged.box_image == "proj-image:v2"

    def test_cli_overrides_workset(self, tmp_path):
        global_path = tmp_path / "global.yaml"
        workset_path = tmp_path / "ws-config.yaml"

        write_global_config(global_path)
        dump_doc(workset_path, {"box": {"image": "ws-image:v1"}})

        merged = load_merged_config(workset_path=workset_path,
            cli_overrides={"box_image": "cli-image:v3"},
        )
        assert merged.box_image == "cli-image:v3"

    def test_default_no_op_when_no_workset_config(self, tmp_path):
        """A default-mode project with no workset config.yaml merges exactly as before."""
        global_path = tmp_path / "global.yaml"
        project_path = tmp_path / BOX_META_FILE
        missing_workset = tmp_path / "no-such-config.yaml"

        write_global_config(global_path)
        dump_doc(project_path, {"box": {"image": "my-image:v2"}})

        baseline = load_merged_config(project_path)
        with_missing = load_merged_config(project_path, workset_path=missing_workset
        )
        assert with_missing == baseline


class TestScalarOverlayPrecedence:
    """Presence-based scalar/bool overlay across the FILE layers.

    The old ``/etc/kanibako/kanibako.yaml`` machine third-file was DELETED in the
    two-layer path reshape (block #3a): ``load_merged_config`` no longer consults any
    ``machine_config_path``.

    ⚑⚑ AND THE ``global_path`` LAYER WENT ON 2026-08-26 — Jei: *"kanibako_config.yaml
    <-- cannot have settings. Period."*  It used to be the least-specific FILE source
    here, so these cases planted their LOWER value in it.  They plant it in the
    WORKSET tier now, which is a real settings file; the layers are built-in defaults
    < workset < box < CLI, and the overlay SEMANTICS under test — presence beats
    absence, a present ``None`` is a value the default never replaces (keyspec §2h),
    ``""`` is a real value — are unchanged and are what these cases were always about.
    """

    def test_no_machine_config_path_attribute(self):
        """The deleted machine third-file is structurally gone (no attribute)."""
        import kanibako.settings.config as config_mod
        assert not hasattr(config_mod, "machine_config_path")

    def test_a_settings_tier_beats_builtin_defaults(self, tmp_path):
        workset_path = tmp_path / "ws-config.yaml"
        workset_path.write_text("box:\n  image: user-image:v2\n")
        merged = load_merged_config(workset_path=workset_path)
        assert merged.box_image == "user-image:v2"

    def test_full_precedence_workset_project(self, tmp_path):
        workset_path = tmp_path / "ws-config.yaml"
        workset_path.write_text("box:\n  image: ws:3\n  shell: bash\n")
        project_path = tmp_path / BOX_META_FILE
        project_path.write_text("box:\n  image: proj:4\n")
        merged = load_merged_config(project_path, workset_path=workset_path
        )
        # project wins for image; shell only set at the workset tier so it survives.
        # (⮕ P7: this used ``agent_name``, a key that no longer exists — the agent
        # SELECTION is the §2h request ``pref.system.agent``, resolved off the
        # snapshot, not by this flat scalar loader.)
        assert merged.box_image == "proj:4"
        assert merged.box_shell == "bash"

    def test_missing_global_file_is_empty_level(self, tmp_path):
        # No file at all → built-in defaults.
        merged = load_merged_config()
        assert merged.box_image == "ghcr.io/doctorjei/kanibako-oci:latest"

    def test_higher_layer_overrides_lower(self, tmp_path):
        workset_path = tmp_path / "ws-config.yaml"
        workset_path.write_text("box:\n  image: img:workset\n")
        merged = load_merged_config(workset_path=workset_path)
        assert merged.box_image == "img:workset"
        # A box layer overrides the workset value (presence-based).
        project_path = tmp_path / BOX_META_FILE
        project_path.write_text("box:\n  image: img:box\n")
        merged2 = load_merged_config(project_path, workset_path=workset_path
        )
        assert merged2.box_image == "img:box"

    def test_set_to_default_value_sticks(self, tmp_path):
        """A layer setting a field to the built-in default wins over a lower
        layer's non-default (presence beats the old ``!= default`` guard)."""
        default_img = "ghcr.io/doctorjei/kanibako-oci:latest"
        workset_path = tmp_path / "ws-config.yaml"
        workset_path.write_text("box:\n  image: img:custom\n")
        project_path = tmp_path / BOX_META_FILE
        # Explicitly set the built-in default — must win.
        project_path.write_text(f"box:\n  image: {default_img}\n")
        merged = load_merged_config(project_path, workset_path=workset_path
        )
        assert merged.box_image == default_img

    def test_a_present_null_in_a_higher_file_does_not_inherit(self, tmp_path):
        """A ``null`` in the box tier over a workset value is a PRESENT ``None``, not a
        request for the built-in default.

        # keyspec §2h: "KEPT ``None`` for a scalar leaf — the consumer reads None, never
        # the key's default."  The higher file's null does not reach down for the lower
        # file's ``img:custom`` and does not fall back to the default either.
        """
        from kanibako.settings.settings_resolve import SettingsError

        workset_path = tmp_path / "ws-config.yaml"
        workset_path.write_text("box:\n  image: img:custom\n")
        project_path = tmp_path / BOX_META_FILE
        project_path.write_text("box:\n  image: null\n")
        with pytest.raises(SettingsError) as excinfo:
            load_merged_config(project_path, workset_path=workset_path
            )
        assert "box.image" in str(excinfo.value)

    def test_an_empty_value_in_a_higher_file_does_not_inherit(self, tmp_path):
        """The empty spelling (``image:``, which parses to ``None``) is the same present
        ``None`` and answers the same way as an explicit ``null``."""
        from kanibako.settings.settings_resolve import SettingsError

        workset_path = tmp_path / "ws-config.yaml"
        workset_path.write_text("box:\n  image: img:custom\n")
        project_path = tmp_path / BOX_META_FILE
        project_path.write_text("box:\n  image:\n")
        with pytest.raises(SettingsError) as excinfo:
            load_merged_config(project_path, workset_path=workset_path
            )
        assert "box.image" in str(excinfo.value)

    def test_empty_string_is_a_real_value_not_unset(self, tmp_path):
        """``""`` is a real value distinct from ``null``: a lower layer sets a
        non-empty box_shell, a higher layer sets ``""`` and that ``""`` wins (it
        does NOT read as unset, so it is not the auto-detect a ``null`` asks for).

        (⮕ P7: was written against ``box_agent_name``, retired with spec §2b; the
        SHAPE under test is the presence-based scalar overlay, not that key.)"""
        workset_path = tmp_path / "ws-config.yaml"
        workset_path.write_text('box:\n  shell: foo\n')
        project_path = tmp_path / BOX_META_FILE
        # Quoted empty string is a real value, not null.
        project_path.write_text('box:\n  shell: ""\n')
        merged = load_merged_config(project_path, workset_path=workset_path
        )
        assert merged.box_shell == ""
        # Sanity: a non-empty lower value is what we are overriding away from.
        merged_ws_only = load_merged_config(workset_path=workset_path)
        assert merged_ws_only.box_shell == "foo"

    def test_higher_layer_overrides_after_null(self, tmp_path):
        """A null is not terminal: a higher layer (CLI override) can set a
        concrete value afterward and it wins.

        # keyspec §2h keeps a present ``None`` only where it WINS the cascade; the
        # refusal judges the resolved value, so an overridden null is never refused.
        """
        workset_path = tmp_path / "ws-config.yaml"
        workset_path.write_text("box:\n  image: null\n")
        merged = load_merged_config(workset_path=workset_path,
            cli_overrides={"box_image": "img:cli"},
        )
        assert merged.box_image == "img:cli"

    def test_a_box_value_overrides_a_workset_null(self, tmp_path):
        """The box tier is more authoritative than the workset tier, so its value wins
        over a workset ``null`` and nothing is refused."""
        workset_path = tmp_path / "ws-config.yaml"
        workset_path.write_text("box:\n  image: null\n")
        project_path = tmp_path / BOX_META_FILE
        project_path.write_text("box:\n  image: img:box\n")
        merged = load_merged_config(project_path, workset_path=workset_path)
        assert merged.box_image == "img:box"


class TestPresentScalarFields:
    """The settings-file read is a walk THROUGH the declared dotted keys.

    ⚑ It replaced a whole-document flatten into underscore-joined names
    (``_flatten_toml``, deleted 2026-08-31) whose namespace collided with the
    ``KanibakoConfig`` field names — which is how an undeclared top-level
    ``box_image:`` came to resolve exactly like the declared ``box: image:``.
    """

    def test_the_declared_nested_spelling_is_read(self, tmp_path):
        path = tmp_path / BOX_META_FILE
        path.write_text('box:\n  image: "x"\n  shell: "y"\n')
        assert _present_scalar_fields(path) == {"box_image": "x", "box_shell": "y"}

    def test_the_flat_spelling_is_not_a_key(self, tmp_path):
        """🛑 THE CLOSED-KEYSPACE HALF (spec §0): ``box_image`` is not a declared key."""
        path = tmp_path / BOX_META_FILE
        path.write_text('box_image: "x"\n')
        assert _present_scalar_fields(path) == {}

    def test_an_undeclared_leaf_under_a_declared_table_is_not_read(self, tmp_path):
        """The walk asks for the four keys by name; it does not sweep the table."""
        path = tmp_path / BOX_META_FILE
        path.write_text('box:\n  image: "x"\n  not_a_key: "y"\n')
        assert _present_scalar_fields(path) == {"box_image": "x"}

    def test_a_present_none_is_the_reset_sentinel(self, tmp_path):
        path = tmp_path / BOX_META_FILE
        path.write_text("box:\n  image:\n")
        assert _present_scalar_fields(path) == {"box_image": None}

    def test_a_bool_stays_a_bool(self, tmp_path):
        """``str(False)`` is the truthy ``"False"`` — the coercion must not stringify."""
        path = tmp_path / BOX_META_FILE
        path.write_text("box:\n  enable_vault: false\n")
        assert _present_scalar_fields(path) == {"box_enable_vault": False}


class TestBoxEnableVault:
    """Direct tests for the sparse box.enable_vault writer/reader.

    P8c: ``write_project_meta`` (which formerly exercised this sparse box-write
    path by analogy) was deleted; ``write_box_enable_vault`` is now the sole
    writer, so it gets its own direct coverage here.
    """

    def test_disabled_writes_box_enable_vault_false(self, tmp_path):
        """(a) enable_vault=False → box:{enable_vault: False} (a real bool)."""
        from kanibako.settings.config import load_doc
        p = tmp_path / BOX_META_FILE
        write_box_enable_vault(p, enable_vault=False)
        data = load_doc(p)
        assert data["box"]["enable_vault"] is False
        assert isinstance(data["box"]["enable_vault"], bool)
        # Round-trips through the paired reader.
        assert read_box_enable_vault(p) is False

    def test_default_true_on_fresh_path_writes_nothing(self, tmp_path):
        """(b) default True on a fresh path writes NOTHING — no file, no empty
        ``box:`` table materialized."""
        p = tmp_path / BOX_META_FILE
        write_box_enable_vault(p)  # default True
        assert not p.exists()
        # The absent file reads back as the default True.
        assert read_box_enable_vault(p) is True

    def test_default_true_drops_stale_override(self, tmp_path):
        """(c) default True with a stale box.enable_vault present → drops it."""
        from kanibako.settings.config import load_doc
        p = tmp_path / BOX_META_FILE
        p.write_text("box:\n  enable_vault: false\n")
        write_box_enable_vault(p)  # default True
        data = load_doc(p)
        assert "enable_vault" not in data.get("box", {})
        assert read_box_enable_vault(p) is True

    @pytest.mark.parametrize("enable_vault", [False, True])
    def test_a_scalar_box_refuses_by_name_instead_of_raising_typeerror(
        self, tmp_path, enable_vault,
    ):
        """(c2) A scalar ``box`` refuses BY NAME and the file is left byte-identical.

        This site's old shape was a CRASH, not a clobber: ``setdefault`` hands back the scalar
        it found instead of a table, so the item assignment raised
        ``TypeError: 'str' object does not support item assignment`` — a traceback where the
        named refusal is the cure.  Nothing was ever lost here, but the user read a crash.
        Both arms are covered: the ``True`` arm used to no-op SILENTLY against a scalar ``box``,
        so the guard makes it say so instead.
        MUTATION: drop the ``refuse_scalar_sections`` call in ``write_box_enable_vault`` and
        the ``False`` param reds with the ``TypeError``, the ``True`` param reds by not raising.
        """
        p = tmp_path / BOX_META_FILE
        p.write_text("box: /x\n")

        with pytest.raises(ConfigError) as exc:
            write_box_enable_vault(p, enable_vault=enable_vault)
        assert str(exc.value) == (
            f"the config file {p} holds /x at 'box', where a table of keys belongs, "
            f"so 'box.' keys cannot be written under it. "
            f"Fix or delete 'box' in that file by hand, then retry."
        )
        assert p.read_text() == "box: /x\n"

    #: A ``box`` SECTION that is not a table, one per YAML shape and per truth value.
    #: The corpus carries the falsy/non-falsy split deliberately: ``or {}`` used to swallow
    #: the falsy rows into a silent default while the non-falsy ones reached ``in``.
    _SCALAR_BOX_BODIES = [
        pytest.param("box:\n", id="null"),
        pytest.param("box: ''\n", id="empty-string"),
        pytest.param("box: 0\n", id="zero"),
        pytest.param("box: false\n", id="false"),
        pytest.param("box: []\n", id="empty-list"),
        pytest.param("box: /x\n", id="path-string"),
        pytest.param("box: 42\n", id="nonzero-int"),
        pytest.param("box: true\n", id="true"),
        pytest.param("box: [enable_vault]\n", id="list-holding-the-key"),
        pytest.param("box: 'enable_vault: false'\n", id="string-holding-the-key"),
    ]

    @pytest.mark.parametrize("body", _SCALAR_BOX_BODIES)
    def test_the_authored_reader_refuses_a_scalar_box_by_name(self, tmp_path, body):
        """A ``box`` that is not a table REFUSES by name, whatever shape or truth value.

        ⚑ THE RULE, not an inventory: a present non-table ``box`` is refused wherever it
        sits, so every row above must reach the SAME named refusal with the file left
        byte-identical.  The rows split on whether ``"enable_vault" in box_tbl`` BLOWS UP
        (``42``, ``true``, a list or string holding the key — a ``TypeError`` out of the
        reader) or answers ``False`` (everything falsy, plus ``/x`` — the default read
        SILENTLY), which is why a corpus and not one example is the pin.
        MUTATION: drop the ``refuse_scalar_sections`` call in ``read_box_enable_vault``
        and the four ``in``-throws rows red with a ``TypeError``; the rest red by not
        raising.
        """
        from kanibako.settings.config_io import render_stored_scalar

        p = tmp_path / BOX_META_FILE
        p.write_text(body)
        stored = load_doc(p)["box"]

        with pytest.raises(ConfigError) as exc:
            read_box_enable_vault(p)
        assert str(exc.value) == (
            f"the config file {p} holds {render_stored_scalar(stored)} at 'box', "
            f"where a table of keys belongs, so 'box.' keys cannot be written under it. "
            f"Fix or delete 'box' in that file by hand, then retry."
        )
        assert p.read_text() == body

    def test_a_falsy_and_a_non_falsy_box_scalar_reach_one_verdict(self, tmp_path):
        """``box: 0`` and ``box: 42`` are the same YAML shape and must answer alike.

        ⚑ THE ASYMMETRY, pinned.  ``load_doc(path).get("box") or {}`` sent every FALSY
        scalar down the ``{}`` arm — so ``0``/``false``/``""``/``null``/``[]`` read as *no
        override present* and returned the default — while a NON-falsy scalar reached
        ``"enable_vault" in box_tbl``, a containment test on a string, a list or a number.
        One YAML kind (a number) therefore produced two opposite verdicts from one reader:
        a silent default against a ``TypeError``.  The stored value and the file name are
        masked so what is compared is the refusal's SHAPE, which is the one thing both rows
        must now share.
        """
        outcomes = []
        for stored, body in (("0", "box: 0\n"), ("42", "box: 42\n")):
            p = tmp_path / BOX_META_FILE
            p.write_text(body)
            with pytest.raises(ConfigError) as exc:
                read_box_enable_vault(p)
            # Mask the PATH first — a digit in a tmp_path would otherwise be masked as
            # the stored value.
            outcomes.append(str(exc.value).replace(str(p), "<PATH>").replace(stored, "<VALUE>"))
        assert outcomes[0] == outcomes[1]
        assert "<PATH> holds <VALUE> at 'box'" in outcomes[0]

    def test_the_authored_reader_and_its_writer_speak_one_message(self, tmp_path):
        """One stored value, one refusal — the reader and the writer of the key agree.

        ⚑ ``box.enable_vault`` has one writer and one reader, so a scalar ``box`` must not
        produce two answers for one file: the reader's text is built from the same
        :func:`~kanibako.settings.config_io.render_stored_scalar` the writer's is.
        """
        p = tmp_path / BOX_META_FILE
        p.write_text("box: /x\n")

        with pytest.raises(ConfigError) as from_reader:
            read_box_enable_vault(p)
        with pytest.raises(ConfigError) as from_writer:
            write_box_enable_vault(p, enable_vault=False)
        assert str(from_reader.value) == str(from_writer.value)
        assert p.read_text() == "box: /x\n"

    @pytest.mark.parametrize("body", ["", "image: custom:v1\n"])
    def test_a_box_tier_without_a_box_section_still_reads_the_default(
        self, tmp_path, body,
    ):
        """Anti-over-refusal: the guard is the SHAPE rule, not a presence test.

        ⚑ An ABSENT section, an EMPTY file, and a doc with no ``box:`` at all are all
        *no override authored*, and must keep answering the default.  Only a present
        NON-TABLE ``box`` refuses; a present table does not.
        """
        p = tmp_path / BOX_META_FILE
        p.write_text(body)
        assert read_box_enable_vault(p) is True

    def test_a_table_box_keeps_reading_its_leaf(self, tmp_path):
        """The named refusal must not reach a ``box`` that IS a table."""
        p = tmp_path / BOX_META_FILE
        p.write_text('box:\n  image: custom:v1\n  enable_vault: "false"\n')
        assert read_box_enable_vault(p) is False

    def test_a_hand_quoted_false_is_not_the_truthy_string(self, tmp_path):
        """The anchor case: ``enable_vault: "false"`` is False, not the truthy ``"false"``.

        ⚑ Settings files are a HAND-EDIT surface, so the stored leaf can be a string.  The
        reader is annotated ``-> bool`` and its callers feed lifecycle destination writes,
        so returning the raw leaf made the AUTHORED answer disagree with
        ``resolve_box_enable_vault``'s until the next write normalized the file.
        """
        p = tmp_path / BOX_META_FILE
        p.write_text('box:\n  enable_vault: "false"\n')
        assert read_box_enable_vault(p) is False

    @pytest.mark.parametrize("authored", sorted(_BOOL_FALSE | _BOOL_TRUE))
    def test_every_truth_table_token_reads_back_as_a_real_bool(self, tmp_path, authored):
        """The RULE, not an inventory: the authored reader applies the SHARED truth table.

        ⚑ The corpus is ``config``'s own ``_BOOL_TRUE``/``_BOOL_FALSE``, so a token added
        there that this reader does not honor reds here instead of outdating a list.
        (Mutation: return ``box_tbl["enable_vault"]`` raw → every token is a non-empty
        string → the four ``_BOOL_FALSE`` cases go RED.)
        """
        p = tmp_path / BOX_META_FILE
        p.write_text(f'box:\n  enable_vault: "{authored}"\n')
        value = read_box_enable_vault(p)
        assert isinstance(value, bool)
        assert value is coerce_bool(authored)

    def test_the_authored_reader_agrees_with_the_resolved_one_on_a_string(
        self, config_file, tmp_home, credentials_dir,
    ):
        """The two halves cannot disagree for the one command that runs before a rewrite.

        ⚑ Same box file, both readers.  ``read_box_enable_vault`` answers *what the box
        authored* and ``resolve_box_enable_vault`` answers *what the cascade resolves*;
        with the value present at the BOX tier and nowhere else those are the same value,
        and a coercion on only one side is exactly how they drifted.
        """
        from kanibako.settings.paths import resolve_box_enable_vault

        box_file = tmp_home / "box.yaml"
        box_file.write_text('box:\n  enable_vault: "false"\n')
        resolved = resolve_box_enable_vault(
            config_file, box_path=box_file, workset_path=None,
        )
        assert read_box_enable_vault(box_file) is resolved

    def test_disabled_merges_beside_existing_box_image(self, tmp_path):
        """(d) disabled merges beside an existing box.image (preserves it)."""
        from kanibako.settings.config import load_doc
        p = tmp_path / BOX_META_FILE
        p.write_text('box:\n  image: "custom:v1"\n')
        write_box_enable_vault(p, enable_vault=False)
        data = load_doc(p)
        assert data["box"]["image"] == "custom:v1"
        assert data["box"]["enable_vault"] is False


class TestTheTwoBoxScalarResolvesAgree:
    """⚑⚑ TWO ROUTES OVER ONE CASCADE, PINNED EQUAL — the anti-drift guard.

    ``box.enable_vault`` is resolved two ways on purpose (2026-08-29).
    ``load_merged_config`` goes through ``_resolve_box_scalars`` →
    ``build_launch_snapshot``, whose LAST step is the whole-tree §0 audit that RAISES on
    an undeclared entry anywhere in the cascade.  ``resolve_box_enable_vault`` goes
    through ``_narrow_box_scalar_cascade``, which stops at the merge — because its caller
    is ``paths.resolve_project``, the PATH resolver every verb runs, including the plain
    ``kanibako box show`` that exists to PRINT an undeclared line rather than refuse it.

    🛑 The audit is the ONLY intended difference.  These cases assert the two agree on the
    VALUE at every tier, so the split cannot quietly become two opinions about the cascade.
    """

    @staticmethod
    def _std(config_file):
        from kanibako.settings.paths import load_std_paths

        return load_std_paths(load_config(config_file))

    @pytest.mark.parametrize(
        "system, workset, box, expected",
        [
            (None, None, None, True),          # nothing stored ⇒ the declared floor
            (False, None, None, False),        # SYSTEM tier alone — the 2026-08-29 fix
            (False, True, None, True),         # workset beats system
            (True, False, None, False),        # workset beats system, other way
            (True, True, False, False),        # box beats both
            (False, False, True, True),        # box beats both, other way
        ],
    )
    def test_the_narrow_cascade_agrees_with_the_merged_loader(
        self, config_file, tmp_home, credentials_dir, system, workset, box, expected,
    ):
        from kanibako.settings.paths import resolve_box_enable_vault

        std = self._std(config_file)
        ws_file = tmp_home / "ws.yaml"
        box_file = tmp_home / "box.yaml"
        for path, value in ((std.settings, system), (ws_file, workset), (box_file, box)):
            if value is None:
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            dump_doc(path, {"box": {"enable_vault": value}})

        narrow = resolve_box_enable_vault(
            std.config_file, box_path=box_file, workset_path=ws_file,
        )
        merged = load_merged_config(box_file, workset_path=ws_file,
        ).box_enable_vault
        assert narrow is expected
        assert narrow == merged, (
            f"the narrow resolve says {narrow!r} and load_merged_config says {merged!r} "
            f"for the same three files — the two routes have drifted"
        )


class TestConfigFilePath:
    def test_returns_new_path_when_neither_exists(self, tmp_path):
        result = config_file_path(tmp_path)
        assert result == tmp_path / CONFIG_FILENAME

    def test_returns_new_path_when_new_exists(self, tmp_path):
        new = tmp_path / CONFIG_FILENAME
        new.write_text("paths:\n")
        result = config_file_path(tmp_path)
        assert result == new

    def test_ignores_legacy_old_subdir_location(self, tmp_path):
        # An old-location file is no longer recognized: always resolves
        # to the current top-level location.
        old = tmp_path / "kanibako" / CONFIG_FILENAME
        old.parent.mkdir()
        old.write_text("paths:\n")
        result = config_file_path(tmp_path)
        assert result == tmp_path / CONFIG_FILENAME


class TestTargetSettings:
    """Tests for target setting override storage in box.yaml."""

    def _write_base_toml(self, path, agent=None):
        """Write a minimal box.yaml for testing, with an optional ``agent`` table."""
        data: dict = {"box": {"image": "base:image"}}
        if agent is not None:
            data["agent"] = agent
        dump_doc(path, data)

    def test_round_trip(self, tmp_path):
        """Read back agent-keyed target settings."""
        p = tmp_path / BOX_META_FILE
        self._write_base_toml(p, {"claude": {"model": "sonnet", "access": "permissive"}})

        settings = read_agent_settings(p, "claude")
        assert settings == {"model": "sonnet", "access": "permissive"}

    def test_backward_compat_no_section(self, tmp_path):
        """box.yaml without a [agent] section returns empty dict."""
        p = tmp_path / BOX_META_FILE
        self._write_base_toml(p)

        settings = read_agent_settings(p, "claude")
        assert settings == {}

    def test_flat_legacy_crab_treated_as_unset(self, tmp_path):
        """A legacy FLAT [agent] table (scalars, no per-agent dicts) is ignored.

        Pass 1 does NOT migrate; only nested agent.<agent>/agent.default tiers
        are honored, so a hand-edited flat shape reads as empty.
        """
        from kanibako.settings.config import dump_doc, load_doc

        p = tmp_path / BOX_META_FILE
        self._write_base_toml(p)
        data = load_doc(p)
        data["agent"] = {"model": "sonnet"}  # flat scalar — old shape
        dump_doc(p, data)

        assert read_agent_settings(p, "claude") == {}

    def test_default_tier_applies_to_any_agent(self, tmp_path):
        """agent.default values apply to every agent unless overridden."""
        p = tmp_path / BOX_META_FILE
        self._write_base_toml(p, {"default": {"model": "sonnet"}})

        assert read_agent_settings(p, "claude") == {"model": "sonnet"}
        assert read_agent_settings(p, "goose") == {"model": "sonnet"}

    def test_agent_specific_wins_over_default(self, tmp_path):
        """agent.<agent> overrides agent.default within one file."""
        p = tmp_path / BOX_META_FILE
        self._write_base_toml(p, {"default": {"model": "sonnet"}, "claude": {"model": "opus"}})

        assert read_agent_settings(p, "claude") == {"model": "opus"}
        # A different agent still gets the default tier.
        assert read_agent_settings(p, "goose") == {"model": "sonnet"}

    def test_an_agent_tier_category_map_renders_as_rows_not_a_repr(self):
        """A category map at the agent tier renders through the one show walk: destination
        normalized, entry ``src`` / ``src  [options]`` — never the dict repr
        ``caches = {'~/c/': ['uv']}``.  Scalars keep their bare row.
        MUTATION: render each leaf as a scalar again in ``config.agent_settings_of`` (the
        pre-fix per-leaf map, which stringified a table) and this reds."""
        from kanibako.settings.config import agent_settings_of

        rows = agent_settings_of(
            {"agent": {"default": {"model": "opus", "caches": {"~/c/": ["uv"]}},
                       "claude": {"common": {"~/p": ["q", "ro"]}}}},
            "claude",
        )
        assert rows == {
            "model": "opus",
            "caches[/home/agent/c]": "uv",
            "common[/home/agent/p]": "q  [ro]",
        }, rows

    def test_no_bleed_across_agents(self, tmp_path):
        """An override set for one agent does NOT bleed onto another (B3 bug)."""
        p = tmp_path / BOX_META_FILE
        self._write_base_toml(p, {"claude": {"model": "sonnet"}})

        assert read_agent_settings(p, "claude") == {"model": "sonnet"}
        assert read_agent_settings(p, "goose") == {}


class TestPersistCreationFlags:
    """The §1A CREATE EXCEPTION gate (B6, R-11a + the 2026-08-02 materialization
    ruling) — the ONE function through which a shadowing flag's value ever
    persists.  ``kanibako create`` and the launch-materialization path both call
    THIS gate; there is no per-path persist logic anywhere else.
    """

    def test_materializing_with_image_persists_it(self, tmp_path):
        from kanibako.settings.config import persist_creation_flags
        from kanibako.settings.config_io import load_doc

        p = tmp_path / BOX_META_FILE
        persist_creation_flags(p, materializing=True, image="custom:v1")
        assert load_doc(p)["box"]["image"] == "custom:v1"

    def test_materializing_with_share_images_persists_a_real_bool(self, tmp_path):
        from kanibako.settings.config import persist_creation_flags
        from kanibako.settings.config_io import load_doc

        p = tmp_path / BOX_META_FILE
        persist_creation_flags(p, materializing=True, share_images=True)
        assert load_doc(p)["box"]["share_images"] is True

    def test_not_materializing_never_writes(self, tmp_path):
        """The prove-the-negative: on an EXISTING box (materializing=False) a
        flag is STRICTLY EPHEMERAL — the gate writes nothing at all."""
        from kanibako.settings.config import persist_creation_flags

        p = tmp_path / BOX_META_FILE
        persist_creation_flags(
            p, materializing=False, image="custom:v1", share_images=True,
        )
        assert not p.exists()

    def test_no_flags_writes_nothing_not_even_an_empty_file(self, tmp_path):
        """A no-flag create bakes NOTHING (the stop-baking decision): absent
        flags leave the box tier untouched, so the box resolves the live
        cascade.  ``""`` for image is absent, not a value (absent ≠ '')."""
        from kanibako.settings.config import persist_creation_flags

        p = tmp_path / BOX_META_FILE
        persist_creation_flags(p, materializing=True)
        persist_creation_flags(p, materializing=True, image="", share_images=None)
        assert not p.exists()

    def test_write_is_merge_preserving(self, tmp_path):
        from kanibako.settings.config import persist_creation_flags
        from kanibako.settings.config_io import dump_doc, load_doc

        p = tmp_path / BOX_META_FILE
        dump_doc(p, {"box": {"enable_vault": False}, "agent": {"claude": {"model": "opus"}}})
        persist_creation_flags(p, materializing=True, image="custom:v1")
        doc = load_doc(p)
        assert doc["box"] == {"enable_vault": False, "image": "custom:v1"}
        assert doc["agent"] == {"claude": {"model": "opus"}}

    def test_a_scalar_box_refuses_rather_than_being_replaced(self, tmp_path):
        """A scalar where the ``box`` TABLE belongs refuses by name, and the file is left
        byte-identical.

        Before the fix this gate silently threw the user's ``/x`` away and wrote a fresh
        table over it.  The refusal is the one :func:`refuse_scalar_sections` owns, so the
        wording matches ``TestNestedWriteRefusesANonTableSection`` exactly.
        MUTATION: drop the ``refuse_scalar_sections`` call in ``persist_creation_flags``
        and this reds.
        """
        from kanibako.settings.config import persist_creation_flags

        p = tmp_path / BOX_META_FILE
        p.write_text("box: /x\n")

        with pytest.raises(ConfigError) as exc:
            persist_creation_flags(p, materializing=True, image="custom:v1")
        assert str(exc.value) == (
            f"the config file {p} holds /x at 'box', where a table of keys belongs, "
            f"so 'box.' keys cannot be written under it. "
            f"Fix or delete 'box' in that file by hand, then retry."
        )
        assert p.read_text() == "box: /x\n"


class TestMergedConfigKeyspaceResolve:
    """B6 (R-11a(a), option (b)): ``load_merged_config``'s box scalars resolve
    through the KEYSPACE — one resolve behind every consumer, agent-lessly.
    """

    def test_floor_is_the_declared_default_not_the_layer1_file(self, config_file):
        """The resolve's floor is the DECLARED DEFAULT — and a ``[box]`` table in the
        Layer-1 file does not displace it.

        ⚑ THE REPLACEMENT for ``test_stored_system_default_is_mapped_not_stranded``,
        and it asserts the OPPOSITE. That case pinned "consumer-map risk 1": the
        ``kanibako.cfg [box]`` table was written at init on EVERY install and
        the settings cascade did not read it, so its values would be STRANDED unless
        mapped in as the floor. Nothing settings-shaped is written there any more, so
        there is nothing to strand — and a table left by hand must not resolve.

        ⚑ 2026-08-31: the planted table is REFUSED rather than merely not resolving, so
        the floor is measured on a CLEAN file. Which value the planted one loses to is
        no longer a question the code can be asked.
        """
        gp = config_file
        gp.write_text("")
        merged = load_merged_config(None)
        assert merged.box_image == KanibakoConfig().box_image

        gp.write_text("box:\n  image: layer1:planted\n")
        with pytest.raises(ConfigError) as exc:
            load_merged_config(None)
        assert "box.image" in str(exc.value)

    def test_box_tier_beats_workset_beats_global(self, tmp_path, config_file):
        ws = tmp_path / "wconfig.yaml"
        ws.write_text("box:\n  image: ws-img:2\n")
        bt = tmp_path / BOX_META_FILE
        bt.write_text("box:\n  image: box-img:3\n  shell: zsh\n")
        assert load_merged_config(None, workset_path=ws).box_image == "ws-img:2"
        merged = load_merged_config(bt, workset_path=ws)
        assert merged.box_image == "box-img:3"
        assert merged.box_shell == "zsh"  # box.shell rides the same resolve

    def test_system_settings_file_box_table_now_resolves(self, tmp_path, config_file):
        """``kanibako system set box.image=…`` has always written the
        ``box:`` table of global/settings.yaml — stranded before B6, live now."""
        ssp = tmp_path / "data" / "kanibako" / "global" / "settings.yaml"
        ssp.parent.mkdir(parents=True)
        ssp.write_text("box:\n  image: sys-img:4\n")
        assert load_merged_config(None).box_image == "sys-img:4"
        # ...but every settings-file tier above it still wins.
        bt = tmp_path / BOX_META_FILE
        bt.write_text("box:\n  image: box-img:3\n")
        assert load_merged_config(bt).box_image == "box-img:3"

    def test_cli_level_outranks_every_file(self, tmp_path, config_file):
        bt = tmp_path / BOX_META_FILE
        bt.write_text("box:\n  image: box-img:3\n")
        merged = load_merged_config(bt,
            cli_overrides={"box_image": "cli-img:9", "box_share_images": True},
        )
        assert merged.box_image == "cli-img:9"
        assert merged.box_share_images is True

    def test_share_images_resolves_as_a_bool_from_files(self, tmp_path, config_file):
        bt = tmp_path / BOX_META_FILE
        bt.write_text("box:\n  share_images: true\n")
        assert load_merged_config(bt).box_share_images is True

    def test_agentless_resolve_without_any_agent(self, config_file, tmp_path):
        """The resolve is AGENT-LESS by construction (the ``kanibako shell``
        requirement): nothing here selects or consults an agent, and a host with
        zero agents still resolves the box scalars."""
        assert Path.home() == tmp_path / "home"
        merged = load_merged_config(None)
        assert merged.box_image == KanibakoConfig().box_image


class TestMalformedSettingsFileIsNamed:
    """B6-Editor S-3: a malformed settings YAML surfaces as a NAMED ConfigError.

    B6 put a real keyspace resolve inside ``load_merged_config``, so every
    consumer — including the BOX-LESS verbs (``rig list`` / ``setup`` /
    ``baseline``), which pass no project — now parses the cascade files. A raw
    ``yaml.parser.ParserError`` out of that resolve is a traceback, because
    ``cli.main`` converts only ``KanibakoError`` into a clean rc1. The
    normalization sits at the ONE load seam that knows the file
    (``config_io.load_doc``); these pin the seam, the resolve, and the verb.
    """

    _CORRUPT = "box:\n  image: ok\n :\n  - [unclosed\n"

    def test_load_doc_names_the_file_and_the_problem(self, tmp_path):
        """The seam raises ConfigError naming the FILE and the parse problem."""
        from kanibako.errors import ConfigError, KanibakoError
        from kanibako.settings.config_io import load_doc

        bad = tmp_path / BOX_META_FILE
        bad.write_text(self._CORRUPT)

        with pytest.raises(ConfigError) as exc:
            load_doc(bad)
        assert isinstance(exc.value, KanibakoError)  # → cli's clean rc1 band
        msg = str(exc.value)
        assert str(bad) in msg                       # the FILE
        assert "not valid YAML" in msg               # the CLASS of failure
        assert "line " in msg and "column " in msg   # the parse problem, located

    @pytest.mark.parametrize("text, shape", [
        ("hello\n", "a single value"),
        ("42\n", "a single value"),
        ("- box\n- image\n", "a list"),
    ])
    def test_load_doc_refuses_a_document_that_is_not_a_mapping(self, tmp_path, text, shape):
        """Spec §0: a file that is one value or a list is refused naming the file, never
        read as empty.

        MUTATION: restore ``else {}`` for a non-mapping top level and every case reds.
        """
        from kanibako.errors import ConfigError
        from kanibako.settings.config_io import load_doc

        bad = tmp_path / BOX_META_FILE
        bad.write_text(text)

        with pytest.raises(ConfigError) as exc:
            load_doc(bad)
        msg = str(exc.value)
        assert str(bad) in msg
        assert f"is {shape}, not a mapping of keys" in msg

    @pytest.mark.parametrize("text", ["", "\n  \n", "# only a comment\n"])
    def test_load_doc_reads_an_empty_document_as_empty(self, tmp_path, text):
        """The control: a document with nothing in it (YAML ``None``) is ``{}``."""
        from kanibako.settings.config_io import load_doc

        empty = tmp_path / BOX_META_FILE
        empty.write_text(text)
        assert load_doc(empty) == {}

    def test_valid_yaml_is_untouched(self, tmp_path):
        """The guard is a normalization, not a new refusal."""
        from kanibako.settings.config_io import load_doc

        good = tmp_path / BOX_META_FILE
        good.write_text("box:\n  image: ok:1\n")
        assert load_doc(good) == {"box": {"image": "ok:1"}}

    def test_boxless_merged_resolve_raises_the_named_error(
        self, tmp_path, config_file,
    ):
        """The BOX-LESS shape (``load_merged_config(None)``) — the one every
        rig/setup/baseline call site uses — surfaces the named error."""
        from kanibako.errors import ConfigError

        ssp = tmp_path / "data" / "kanibako" / "global" / "settings.yaml"
        ssp.parent.mkdir(parents=True)
        ssp.write_text(self._CORRUPT)

        with pytest.raises(ConfigError) as exc:
            load_merged_config(None)
        assert str(ssp) in str(exc.value)

    def test_boxless_verb_exits_rc1_with_a_clean_message(
        self, tmp_path, config_file, capsys,
    ):
        """E2E through ``main(["rig", "list"])``: rc1 + ``Error: …``, no traceback.

        ``rig list`` is a BOX-LESS verb whose ``load_merged_config(None)``
        reaches the corrupt system settings file; before the normalization it
        died with a ``yaml.parser.ParserError`` traceback.
        """
        from unittest.mock import patch

        from kanibako.cli import main

        ssp = tmp_path / "data" / "kanibako" / "global" / "settings.yaml"
        ssp.parent.mkdir(parents=True)
        ssp.write_text(self._CORRUPT)

        with patch("kanibako.cli._ensure_initialized"):
            with pytest.raises(SystemExit) as exc:
                main(["rig", "list", "-q"])
        assert exc.value.code == 1  # the clean band, not an interpreter crash
        err = capsys.readouterr().err
        assert err.startswith("Error: ")
        assert str(ssp) in err
        assert "not valid YAML" in err

    def test_unreadable_file_raises_config_error(self, tmp_path):
        """A chmod 000 file raises ConfigError with OSError as cause."""
        if os.geteuid() == 0:
            pytest.skip("root can read chmod 000 files")
        bad = tmp_path / "unreadable.yaml"
        bad.write_text("key: value\n")
        os.chmod(bad, 0o000)
        with pytest.raises(ConfigError) as exc_info:
            load_doc(bad)
        assert str(bad) in str(exc_info.value)
        assert exc_info.value.__cause__ is not None
        assert isinstance(exc_info.value.__cause__, OSError)

    def test_non_utf8_file_raises_config_error(self, tmp_path):
        """A file containing invalid UTF-8 raises ConfigError with UnicodeDecodeError as cause."""
        bad = tmp_path / "non_utf8.yaml"
        bad.write_bytes(b"\x80\x81\x82")
        with pytest.raises(ConfigError) as exc_info:
            load_doc(bad)
        assert str(bad) in str(exc_info.value)
        assert exc_info.value.__cause__ is not None
        assert isinstance(exc_info.value.__cause__, UnicodeDecodeError)

    def test_directory_raises_config_error(self, tmp_path):
        """A directory at the config path raises ConfigError with IsADirectoryError as cause."""
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        with pytest.raises(ConfigError) as exc_info:
            load_doc(subdir)
        assert str(subdir) in str(exc_info.value)
        assert exc_info.value.__cause__ is not None
        assert isinstance(exc_info.value.__cause__, IsADirectoryError)


class TestRepeatedKeyIsRefused:
    """Q103 / S4 I17: a settings file that writes one key twice is refused, naming both lines.

    PyYAML keeps the LAST of a repeated key without a word (``box: {a: 1}`` then ``box: {b: 2}``
    loads as ``{'box': {'b': 2}}``), so the refusal lives in ``load_doc``'s loader.
    MUTATION: load with ``yaml.safe_load`` again and every refusal case reds.
    """

    @pytest.mark.parametrize("text, dotted, first, second", [
        ("box: {a: 1}\nbox: {b: 2}\n", "box", 1, 2),
        ("box:\n  env:\n    A: '1'\n    A: '2'\n", "box.env.A", 3, 4),
        ("self:\n  model: opus\n  model: sonnet\n", "self.model", 2, 3),
        ("list:\n  - {k: 1}\n  - k: 1\n    k: 2\n", "list[1].k", 3, 4),
        ("=: x\n=: y\n", "=", 1, 2),
        ("x:\n  <<:\n    a: 1\n    a: 2\n", "x.<<.a", 3, 4),
        ("x:\n  <<:\n    - {b: 1}\n    - a: 1\n      a: 2\n", "x.<<[1].a", 4, 5),
    ])
    def test_repeated_key_names_file_path_and_both_lines(
        self, tmp_path, text, dotted, first, second,
    ):
        """Top level, nested, inside ``self:``, and inside a list item: file + dotted path + lines."""
        bad = tmp_path / "settings.yaml"
        bad.write_text(text)

        with pytest.raises(ConfigError) as exc:
            load_doc(bad)
        assert str(exc.value) == (
            f"the config file {bad} sets '{dotted}' twice (line {first} and line {second}). "
            "Remove one of the two, then retry."
        )

    def test_clean_file_loads_unchanged(self, tmp_path):
        """The control: the same keys once each load exactly as ``yaml.safe_load`` reads them."""
        import yaml

        text = (
            "box:\n  image: ok:1\n  env: {A: '1', B: '2'}\n"
            "self:\n  model: opus\nagent:\n  claude: {model: sonnet}\n"
        )
        good = tmp_path / "settings.yaml"
        good.write_text(text)
        assert load_doc(good) == yaml.safe_load(text)

    def test_a_repeat_on_one_line_names_the_columns(self, tmp_path):
        """A flow map repeating a key on one line: the lines alone cannot tell them apart."""
        bad = tmp_path / "settings.yaml"
        bad.write_text("box: {a: 1, a: 2}\n")

        with pytest.raises(ConfigError) as exc:
            load_doc(bad)
        assert "sets 'box.a' twice (line 1, column 7 and line 1, column 13)" in str(exc.value)

    def test_a_bare_equals_key_loads_as_the_string(self, tmp_path):
        """``=`` is YAML's ``value`` tag; ``yaml.safe_load`` reads it as ``"="`` and so must this."""
        good = tmp_path / "settings.yaml"
        good.write_text("a:\n  =: x\n")
        assert load_doc(good) == {"a": {"=": "x"}}

    def test_merge_key_override_is_not_a_repeat(self, tmp_path):
        """A key beside ``<<:`` overrides the merged one by YAML's own rule; that is not a repeat."""
        good = tmp_path / "settings.yaml"
        good.write_text("base: &b {a: 1}\nbox:\n  <<: *b\n  a: 2\n")
        assert load_doc(good) == {"base": {"a": 1}, "box": {"a": 2}}

    def test_boxless_verb_exits_rc1_naming_the_repeat(self, tmp_path, config_file, capsys):
        """Through ``main(["rig", "list"])``: a repeated key in the system settings file is rc1."""
        from unittest.mock import patch

        from kanibako.cli import main

        ssp = tmp_path / "data" / "kanibako" / "global" / "settings.yaml"
        ssp.parent.mkdir(parents=True)
        ssp.write_text("box:\n  image: a\nbox:\n  image: b\n")

        with patch("kanibako.cli._ensure_initialized"):
            with pytest.raises(SystemExit) as exc:
                main(["rig", "list", "-q"])
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert err.startswith("Error: ")
        assert f"{ssp} sets 'box' twice (line 1 and line 3)" in err


def _nested_tables(depth: int) -> str:
    """A block document of *depth* nested tables, the outermost at depth 1.

    ⚑ DERIVED, NOT LITERAL: the boundary is ``MAX_DOC_DEPTH``, so the numbers come from
    the constant under test and a change to it moves the pair with it.
    """
    lines = ["  " * i + f"k{i}:" for i in range(depth - 1)]
    lines.append("  " * (depth - 1) + "leaf: 1")
    return "\n".join(lines) + "\n"


def _aliased_levels(levels: int, fanout: int = 9) -> str:
    """The BILLION-LAUGHS shape: *levels* nested lists, each *fanout* aliases to the one below.

    ``fanout`` defaults to 9 because that is the published shape; the document stays a few
    hundred bytes at any *levels*, which is the point of the test that uses it.
    """
    lines = ["seed: &a ['x']"]
    previous = "a"
    for i in range(1, levels):
        name = chr(ord("a") + i)
        aliases = ",".join(["*" + previous] * fanout)
        lines.append(f"{name}: &{name} [{aliases}]")
        previous = name
    lines.append(f"top: *{previous}")
    return "\n".join(lines) + "\n"


class TestSelfReferentialDocumentIsRefused:
    """A document that contains ITSELF, or nests past ``MAX_DOC_DEPTH``, is refused by name.

    ⚑ THE REFUSAL BELONGS WHERE A DOCUMENT ENTERS: a guard in one reader would leave the
    same trap open in the next one, and the crash would land later as a raw
    ``RecursionError`` traceback out of whatever walked it next.

    MUTATION: drop the ``_guard_document`` call in ``parse_doc_text`` and every refusal
    here reds (the cycle becomes a ``RecursionError``, the deep one loads).
    """

    def test_a_cycle_names_the_file_and_the_path(self, tmp_path):
        """``box.x`` is the same table as ``box``: named as a self-reference, not a crash."""
        bad = tmp_path / "box.yaml"
        bad.write_text("box: &a {image: ok:1, x: *a}\n")

        with pytest.raises(ConfigError) as exc:
            load_doc(bad)
        assert str(exc.value) == (
            f"the config file {bad} refers to itself at 'box.x' "
            "(a YAML alias inside its own anchor). Fix or remove the file, then retry."
        )

    def test_a_cycle_through_a_list_is_refused_too(self, tmp_path):
        """A list is a container like any other: ``box.items[0]`` closes the same loop."""
        bad = tmp_path / "box.yaml"
        bad.write_text("box: &a {items: [*a]}\n")

        with pytest.raises(ConfigError) as exc:
            load_doc(bad)
        assert "refers to itself at 'box.items[0]'" in str(exc.value)

    def test_a_shared_sibling_alias_still_loads(self, tmp_path):
        """⚑ THE CONTROL: two keys may NAME one table. Only a path back into itself is a cycle."""
        good = tmp_path / "box.yaml"
        good.write_text("a: &x {k: 1}\nb: *x\n")

        data = load_doc(good)
        assert data == {"a": {"k": 1}, "b": {"k": 1}}
        # They really are ONE object — sharing is what the guard must NOT read as a cycle.
        assert data["a"] is data["b"]

    def test_a_merge_key_still_loads(self, tmp_path):
        """``<<`` shares by name; YAML's own override rule is untouched by the guard."""
        good = tmp_path / "box.yaml"
        good.write_text("base: &b {a: 1}\nbox:\n  <<: *b\n  a: 2\n")
        assert load_doc(good) == {"base": {"a": 1}, "box": {"a": 2}}

    def test_shared_aliases_walk_in_time_proportional_to_containers(self, tmp_path):
        """⚑ BILLION-LAUGHS IS A HOST-SAFETY PROBLEM, NOT A SLOW TEST.

        ``rig.yaml`` and a baseline overlay arrive inside image bundles, so a crafted
        document reaches this walk before any user sees it. Eight levels of nine aliases
        is a few hundred bytes and nine-to-the-eighth distinct PATHS to the same handful of
        tables: a guard that walks each path is exponential in a number the attacker picks.
        The bound is generous (1 s for a walk that must finish in microseconds) so the test
        cannot flake on a loaded box while still failing by four orders of magnitude.
        """
        good = tmp_path / "laugh.yaml"
        good.write_text(_aliased_levels(8))

        start = time.perf_counter()
        data = load_doc(good)
        elapsed = time.perf_counter() - start

        assert "top" in data
        assert elapsed < 1.0, (
            f"the guard took {elapsed:.1f}s on a {len(good.read_text())}-byte document "
            f"of shared aliases"
        )

    def test_a_cycle_reachable_only_through_a_shared_alias_is_still_refused(self, tmp_path):
        """⚑ THE MEMO MUST NOT BLIND THE CYCLE CHECK.

        ``c`` holds ``d``, ``d`` points back at ``c``, and ``e.z`` is a SECOND, shallower
        sighting of the very table ``d`` — the sharing the guard is required to tolerate
        and the loop it is required to refuse, in one document. The memo may only ever
        decide how many times a container is walked, never whether it is checked, so this
        document refuses on the first sighting of the loop.

        ⚑ THE CONTROL IS THE SAME SHAPE WITHOUT THE LOOP (``y: *d`` instead of ``y: *c``):
        one table named twice is legal, so what is refused below is the loop, not the
        sharing.
        """
        bad = tmp_path / "box.yaml"
        bad.write_text("c: &c\n  x: &d\n    y: *c\ne: &e\n  z: *d\n")

        with pytest.raises(ConfigError) as exc:
            load_doc(bad)
        assert str(exc.value) == (
            f"the config file {bad} refers to itself at 'e.z.y.x' "
            "(a YAML alias inside its own anchor). Fix or remove the file, then retry."
        )

    def test_the_same_shape_without_the_loop_still_loads(self, tmp_path):
        """⚑ THE CONTROL for the test above: one table, two names, no loop — it loads."""
        good = tmp_path / "box.yaml"
        good.write_text("c: &c\n  x: &d\n    y: 1\ne: &e\n  z: *d\n")

        data = load_doc(good)
        assert data["c"]["x"] is data["e"]["z"]

    def test_depth_at_the_limit_loads_and_one_past_it_is_refused(self, tmp_path):
        """``MAX_DOC_DEPTH`` tables load; ``MAX_DOC_DEPTH + 1`` is refused, naming the path."""
        ok = tmp_path / "ok.yaml"
        ok.write_text(_nested_tables(MAX_DOC_DEPTH))
        node = load_doc(ok)
        for i in range(MAX_DOC_DEPTH - 1):  # walk to the INNERMOST table, not just the root
            node = node[f"k{i}"]
        assert node == {"leaf": 1}

        bad = tmp_path / "bad.yaml"
        bad.write_text(_nested_tables(MAX_DOC_DEPTH + 1))
        with pytest.raises(ConfigError) as exc:
            load_doc(bad)
        assert str(exc.value) == (
            f"the config file {bad} nests deeper than {MAX_DOC_DEPTH} levels at "
            f"{'.'.join(f'k{i}' for i in range(MAX_DOC_DEPTH))!r}. "
            "Fix or remove the file, then retry."
        )

    def test_a_flow_collection_too_deep_for_the_loader_is_the_same_refusal(self, tmp_path):
        """⚑ The loader composes a huge flow collection in the Python stack: ``RecursionError``
        out of ``yaml.load`` is the SAME document hazard the depth guard measures, so it is
        reported as that refusal — still a ConfigError naming the file, never a traceback."""
        bad = tmp_path / "box.yaml"
        bad.write_text("k: " + "[" * 4000 + "]" * 4000 + "\n")

        with pytest.raises(ConfigError) as exc:
            load_doc(bad)
        assert str(exc.value) == (
            f"the config file {bad} nests deeper than {MAX_DOC_DEPTH} levels. "
            "Fix or remove the file, then retry."
        )

    def test_boxless_verb_exits_rc1_naming_the_cycle(self, tmp_path, monkeypatch, capsys):
        """Through ``main(["rig", "list"])``: a cyclic settings file is rc1 with no traceback."""
        from unittest.mock import patch

        from kanibako.cli import main

        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
        # ⚑ HERMETIC, unlike the neighbor above: with XDG_RUNTIME_DIR unset and no
        # /run/user/<uid> (a container), kanibako.paths warns on stderr FIRST and this
        # test's subject — that the USER's error is the only thing on stderr — goes untested.
        (tmp_path / "run").mkdir()
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
        (tmp_path / "config").mkdir(exist_ok=True)
        write_global_config(tmp_path / "config" / CONFIG_FILENAME)
        ssp = tmp_path / "data" / "kanibako" / "global" / "settings.yaml"
        ssp.parent.mkdir(parents=True)
        ssp.write_text("box: &a {image: a, x: *a}\n")

        with patch("kanibako.cli._ensure_initialized"):
            with pytest.raises(SystemExit) as exc:
                main(["rig", "list", "-q"])
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert err.startswith("Error: ")
        assert f"{ssp} refers to itself at 'box.x'" in err
        assert "Traceback" not in err


class TestNestedWriteRefusesANonTableSection:
    """S2e: ``write_nested_key`` refuses a present non-table section instead of replacing it.

    Spec §0: a write must not silently discard what the user wrote. A present ``null`` is refused
    too — the section is a namespace, and no value there (empty included) is an accepted setting.
    MUTATION: restore ``if not isinstance(child, dict): child = {}`` and every refusal case reds.
    """

    @pytest.mark.parametrize("text, sections, dotted, found", [
        ("system: /x\n", ("system",), "system", "/x"),
        ("box: 7\n", ("box",), "box", "7"),
        ("box:\n  env: [a, b]\n", ("box", "env"), "box.env", "['a', 'b']"),
        ("system:\n", ("system",), "system", "null"),
    ])
    def test_non_table_section_is_refused_and_kept(
        self, tmp_path, text, sections, dotted, found,
    ):
        """String, int, list, and a bare ``null``: named with file + path + value; file unchanged."""
        from kanibako.settings.config_io import write_nested_key

        path = tmp_path / "settings.yaml"
        path.write_text(text)

        with pytest.raises(ConfigError) as exc:
            write_nested_key(path, sections, "leaf", "v")
        assert str(exc.value) == (
            f"the config file {path} holds {found} at '{dotted}', where a table of keys "
            f"belongs, so '{dotted}.' keys cannot be written under it. "
            f"Fix or delete '{dotted}' in that file by hand, then retry."
        )
        assert path.read_text() == text

    def test_absent_sections_are_still_created(self, tmp_path):
        """The control: missing intermediates are created, other content kept."""
        from kanibako.settings.config_io import write_nested_key

        path = tmp_path / "settings.yaml"
        path.write_text("box:\n  image: ok:1\n")
        write_nested_key(path, ("box", "env"), "FOO", "bar")
        assert load_doc(path) == {"box": {"image": "ok:1", "env": {"FOO": "bar"}}}

    @pytest.mark.parametrize("text, sections, dotted, found", [
        ("workset: /x\n", ("workset",), "workset", "/x"),
        ("box: 7\n", ("box",), "box", "7"),
        ("box:\n  env: [a, b]\n", ("box", "env"), "box.env", "['a', 'b']"),
        ("system:\n", ("system",), "system", "null"),
    ])
    def test_reader_asks_the_same_refusal_as_the_writer(
        self, tmp_path, text, sections, dotted, found,
    ):
        """The reader-side twin: the SAME message, with nothing written, for the shapes
        the writer refuses — so a caller that must refuse BEFORE writing can ask."""
        from kanibako.settings.config_io import refuse_scalar_sections

        path = tmp_path / "settings.yaml"
        path.write_text(text)

        with pytest.raises(ConfigError) as exc:
            refuse_scalar_sections(path, sections)
        assert str(exc.value) == (
            f"the config file {path} holds {found} at '{dotted}', where a table of keys "
            f"belongs, so '{dotted}.' keys cannot be written under it. "
            f"Fix or delete '{dotted}' in that file by hand, then retry."
        )
        assert path.read_text() == text

    def test_reader_has_nothing_to_refuse_in_a_missing_path_or_section(self, tmp_path):
        """The controls: a path that is not a file, and a section that is absent, are both
        what the writer creates — so neither is a refusal for a pre-write reader."""
        from kanibako.settings.config_io import refuse_scalar_sections

        refuse_scalar_sections(tmp_path / "absent.yaml", ("workset",))

        path = tmp_path / "settings.yaml"
        path.write_text("box:\n  enable_vault: false\n")
        refuse_scalar_sections(path, ("workset",))
        refuse_scalar_sections(path, ("workset", "kuid"))
        assert path.read_text() == "box:\n  enable_vault: false\n"

    # The verb resolves the cascade before writing, so the census sees the fixture's ``box.env: 7``.
    @pytest.mark.writes_undeclared("box.env")
    def test_set_verb_exits_rc1_and_keeps_the_value(self, tmp_path, config_file, capsys):
        """Through ``main(["system", "set", …])``: rc1 + ``Error: …``, the stored value untouched."""
        from unittest.mock import patch

        from kanibako.cli import main

        ssp = tmp_path / "data" / "kanibako" / "global" / "settings.yaml"
        ssp.parent.mkdir(parents=True)
        ssp.write_text("box:\n  env: 7\n")

        with patch("kanibako.cli._ensure_initialized"):
            with pytest.raises(SystemExit) as exc:
                main(["system", "set", "box.env.FOO=bar"])
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert err.startswith("Error: ")
        assert f"{ssp} holds 7 at 'box.env'" in err
        assert ssp.read_text() == "box:\n  env: 7\n"


class TestAnAgentNodeIsStoredAsAUserWritesIt:
    """``persona+harness`` in a settings file, ``persona℘harness`` only inside a key path.

    The cascade builds its keys canonically and the store serialized them through, so a ``+``
    typed on the command line landed in the file as ``℘`` -- an internal form in a file the user
    edits by hand, and a spelling no command accepts. The store now writes the form a human
    writes, and BOTH walks (this one write, :func:`stored_leaf_object`'s read) resolve a node
    against the file's own spelling, so read and write agree and a file written either way names
    one node.
    MUTATION: pass ``sec`` through verbatim -> the stored-spelling and the read tests red.
    """

    def test_a_written_node_lands_in_the_file_with_the_typed_separator(self, tmp_path):
        """A fresh node is stored the way a human writes it."""
        from kanibako.settings.config_io import write_nested_key

        path = tmp_path / "box.yaml"
        write_nested_key(
            path, ("pref", "agent", "navigator℘claude"), "access", "editing",
        )
        assert load_doc(path) == {
            "pref": {"agent": {"navigator+claude": {"access": "editing"}}},
        }

    def test_a_node_the_file_already_holds_is_reused_not_duplicated(self, tmp_path):
        """A file written before the store rendered ``+`` still holds ``℘`` and is never
        migrated, so the node already in the table WINS: writing must not leave one node in two
        spellings, the shape ``refuse_node_spelled_twice`` refuses."""
        from kanibako.settings.config_io import write_nested_key

        path = tmp_path / "box.yaml"
        path.write_text("pref:\n  agent:\n    navigator℘claude:\n      access: editing\n")
        write_nested_key(
            path, ("pref", "agent", "navigator℘claude"), "access", "restricted",
        )
        assert load_doc(path) == {
            "pref": {"agent": {"navigator℘claude": {"access": "restricted"}}},
        }

    @pytest.mark.parametrize("stored", ("navigator+claude", "navigator℘claude"))
    def test_a_stored_node_reads_under_either_spelling_of_the_key(self, tmp_path, stored):
        """The read walk resolves the node too, so the key the user types and the spelling the
        file holds do not have to agree."""
        from kanibako.settings.config_io import stored_leaf_object

        path = tmp_path / "box.yaml"
        dump_doc(path, {"pref": {"agent": {stored: {"access": "editing"}}}})
        assert stored_leaf_object(
            path, ("pref", "agent", "navigator℘claude"), "access",
        ) == "editing"

    @pytest.mark.parametrize("section", ("system", "box", "env", "channelroot"))
    def test_a_section_that_is_not_a_node_is_stored_as_written(self, tmp_path, section):
        """The render is a no-op on every other section, so one call covers every write route."""
        from kanibako.settings.config_io import write_nested_key

        path = tmp_path / "box.yaml"
        write_nested_key(path, (section,), "leaf", "v")
        assert load_doc(path) == {section: {"leaf": "v"}}

    def test_a_stored_node_is_matched_on_the_separator_alone(self, tmp_path):
        """🛑 A node's CASE is the Q87 fold's business and that fold WARNS about it, so a walk
        that silently reached a capital node would make the warning a lie and collapse a
        spelling the two-spellings refusal deliberately keeps apart."""
        from kanibako.settings.config_io import stored_leaf_object

        path = tmp_path / "box.yaml"
        dump_doc(path, {"pref": {"agent": {"navigator℘Claude": {"access": "V"}}}})
        assert stored_leaf_object(
            path, ("pref", "agent", "navigator℘claude"), "access",
        ) is None

    @pytest.mark.parametrize("stored", ("navigator+claude", "navigator℘claude"))
    def test_a_reset_reaches_a_node_the_file_holds_under_either_spelling(self, tmp_path, stored):
        """The REMOVE walk resolves the node too, so a reset cannot report no override for an
        entry the file holds: the key the user types and the spelling the file holds do not
        have to agree, whichever direction they differ in."""
        from kanibako.settings.config_io import remove_nested_key

        path = tmp_path / "box.yaml"
        dump_doc(path, {"pref": {"agent": {stored: {"access": "editing"}}}})
        assert remove_nested_key(
            path, ("pref", "agent", "navigator℘claude"), "access",
        ) is True
        assert load_doc(path) == {}, "the emptied node table was not pruned"

    def test_the_set_reset_get_round_trip_of_one_node_ends_unset(self, tmp_path):
        """The whole round trip, on the canonical section every reset path hands this function:
        a value written under a node is read back, removed, and gone -- pruning the tables it
        emptied on the way, so the file does not keep a node whose last leaf was reset."""
        from kanibako.settings.config_io import (
            remove_nested_key, stored_leaf_object, write_nested_key,
        )

        path = tmp_path / "box.yaml"
        sections = ("pref", "agent", "navigator℘claude")
        write_nested_key(path, sections, "access", "editing")
        assert stored_leaf_object(path, sections, "access") == "editing"
        assert remove_nested_key(path, sections, "access") is True
        assert stored_leaf_object(path, sections, "access") is None
        assert load_doc(path) == {}
