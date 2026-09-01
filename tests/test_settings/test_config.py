"""Tests for kanibako.settings.config."""

from __future__ import annotations

import pytest

from kanibako.settings.config_io import dump_doc, load_doc
from kanibako.errors import ConfigError
from kanibako.settings.config import (
    BOX_META_FILE,
    KanibakoConfig,
    _BOOL_FALSE,
    _BOOL_TRUE,
    _present_scalar_fields,
    bootstrap_config_paths,
    coerce_bool,
    config_file_path,
    load_config,
    load_merged_config,
    read_box_enable_vault,
    read_setup_completed,
    read_agent_settings,
    write_box_enable_vault,
    write_global_config,
    write_project_config,
    write_agent_setting,
)


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
        (a verbatim copy of ``paths_defaults.CONFIG_PATH_DEFAULTS``). There is nothing
        left to round-trip — ``write_global_config`` creates the file EMPTY and takes no
        config object, because that file cannot carry settings (Jei) and its own
        ``config.*`` foundation is already declared. What loads is the defaults.
        """
        path = tmp_path / "test.yaml"
        write_global_config(path)
        assert load_config(path).config_paths == {}
        assert load_merged_config(path).box_image == KanibakoConfig().box_image

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
        from kanibako.settings.paths_defaults import CONFIG_PATH_DEFAULTS

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
        assert load_merged_config(sparse).box_image == \
            load_merged_config(verbose).box_image

    def test_the_old_three_table_file_refuses_and_names_both_tables(self, tmp_path):
        """The file as it was written until 2026-08-26 is now an ERROR, not an ignore.

        ⚑ THE OTHER HALF of the test above, and the reason its ``system:``/``box:``
        tables moved out of the comparison: the two tables were settings, and a user who
        still has them was silently running something other than what they read.
        """
        from kanibako.settings.paths_defaults import SYSTEM_PATH_DEFAULTS

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

        cf = tmp_home / "config" / "kanibako_config.yaml"
        write_global_config(cf)
        std = load_std_paths(load_config(cf))
        # channelroot leaf -> the channels root dir; children hang off it.
        assert std.channels == std.data_path / "channels"
        assert std.channels_common == std.channels / "common"
        assert std.channels_broadcast == std.channels / "chat" / "broadcast.md"

    def test_null_value_resolves_to_default(self, tmp_path):
        """A SETTINGS file with ``box: image: null`` resolves the key to its default.

        ⚑ THE FILE MOVED, not the rule: the reset sentinel is a settings-tier idiom, and
        the Layer-1 file cannot carry the key to reset.
        """
        box_file = tmp_path / BOX_META_FILE
        box_file.write_text("box:\n  image: null\n")
        merged = load_merged_config(tmp_path / "kanibako_config.yaml", box_file)
        assert merged.box_image == "ghcr.io/doctorjei/kanibako-oci:latest"

    def test_empty_value_resolves_to_default(self, tmp_path):
        """An empty ``image:`` (None) resolves the key to its built-in default."""
        box_file = tmp_path / BOX_META_FILE
        box_file.write_text("box:\n  image:\n")
        merged = load_merged_config(tmp_path / "kanibako_config.yaml", box_file)
        assert merged.box_image == "ghcr.io/doctorjei/kanibako-oci:latest"

    def test_config_table_populates_config_paths(self, tmp_path):
        """[config] keys land in cfg.config_paths (full dotted names)."""
        path = tmp_path / "sys.yaml"
        path.write_text('config:\n  agents: "/x"\n')
        cfg = load_config(path)
        assert cfg.config_paths == {"config.agents": "/x"}


class TestLayer1FileCannotHaveSettings:
    """``kanibako_config.yaml`` carries ``config.*`` and NOTHING else — on the READ
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
        cf = tmp_path / "kanibako_config.yaml"
        cf.write_text('box:\n  image: "layer1:planted"\n  share_images: true\n')
        with pytest.raises(ConfigError) as exc:
            load_merged_config(cf)
        assert str(cf) in str(exc.value)
        assert "box.image" in str(exc.value)
        assert "box.share_images" in str(exc.value)

    def test_the_undeclared_flat_spelling_refuses_there_too(self, tmp_path):
        """A top-level ``box_image:`` is not a key anywhere, and Layer 1 says so by name.

        ⚑ It USED to resolve identically to the declared ``box: image:`` — two spellings
        for one key, one of them undeclared (spec §0).
        """
        cf = tmp_path / "kanibako_config.yaml"
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
        cf = tmp_path / "kanibako_config.yaml"
        cf.write_text(text)
        with pytest.raises(ConfigError) as exc:
            load_config(cf)
        assert "box" in str(exc.value)

    def test_a_real_settings_tier_still_wins(self, tmp_path):
        """🛑 The BOX tier still sets the value — the Layer-1 read is what went away."""
        cf = tmp_path / "kanibako_config.yaml"
        write_global_config(cf)
        box_file = tmp_path / BOX_META_FILE
        box_file.write_text('box:\n  image: "from-the-box-tier"\n')
        merged = load_merged_config(cf, box_file)
        assert merged.box_image == "from-the-box-tier"

    def test_the_config_table_alone_is_accepted(self, tmp_path):
        """The foundation still loads — the file's whole job, and the only thing in it."""
        cf = tmp_path / "kanibako_config.yaml"
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
        cf = config_home / "kanibako_config.yaml"
        cf.write_text(
            f'config:\n  data: "{data_home}/kanibako"\n'
            'system:\n  cache: "/planted-from-layer1"\n'
        )
        with pytest.raises(ConfigError) as exc:
            load_system_config(cf, data_home=data_home, home=home)
        assert "system.cache" in str(exc.value)

        # ...while the SETTINGS file's own row IS honoured — the route that replaced it.
        cf.write_text(f'config:\n  data: "{data_home}/kanibako"\n')
        resolved = load_system_config(cf, data_home=data_home, home=home)
        ssp = resolved["config.settings"]
        ssp.parent.mkdir(parents=True, exist_ok=True)
        ssp.write_text('system:\n  cache: "/from-the-settings-file"\n')
        resolved = load_system_config(cf, data_home=data_home, home=home)
        assert str(resolved["system.cache"]) == "/from-the-settings-file"


class TestBoxScalarDefaultsFloor:
    """The declared-default floor — SEPARATED from the file read, not deleted."""

    def test_it_is_the_declared_defaults(self):
        from kanibako.settings.config import box_scalar_defaults_floor

        floor = box_scalar_defaults_floor()
        assert floor["box.image"] == KanibakoConfig().box_image
        assert floor["box.share_images"] is False

    def test_box_shell_is_suppressed_not_blank(self):
        """``""`` is a SUPPRESSION — "absent ≡ no default" — so an unset
        ``@box.shell`` refuses BY NAME instead of resolving to blank (spec §2b).

        ⚑ Pinned against ``build_launch_snapshot``'s own rule (``if val == "":
        continue``), which this floor has to agree with or the launch floor and the
        set-time floor answer differently for the same key.
        """
        from kanibako.settings.config import box_scalar_defaults_floor

        assert KanibakoConfig().box_shell == ""
        assert "box.shell" not in box_scalar_defaults_floor()

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
        cf = tmp_path / "kanibako_config.yaml"
        write_global_config(cf)
        assert cf.exists(), "the file must still be CREATED; an absent one re-runs init"
        assert cf.read_text() == "", cf.read_text()
        assert load_doc(cf) == {}

    def test_zero_bytes_not_an_empty_mapping(self, tmp_path):
        """🛑 NOT ``{}``. This file is the hand-edit surface the ``config.*`` refusal
        sends users to, and a leading ``{}`` makes an appended ``config:`` block a
        YAML error."""
        import yaml

        cf = tmp_path / "kanibako_config.yaml"
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
        cf = tmp_path / "kanibako_config.yaml"
        write_global_config(cf)
        assert load_doc(cf) == {}


class TestReadSetupCompleted:
    """read_setup_completed: the raw ``system.setup_completed`` reader (W1 gate).

    ⚑ ITS FILE IS THE SYSTEM SETTINGS FILE since 2026-08-26 (Jei: "there is no reason
    whatsoever that ``system.setup_completed`` should go in the config. It should not.
    It should go in the global settings file") — which is what spec §2g always declared.
    The variable is named ``ssp`` here for that reason: passing ``kanibako_config.yaml``
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

        cf = tmp_path / "kanibako_config.yaml"
        ssp = tmp_path / "settings.yaml"
        write_system_value(cf, "setup_completed", "1.8.0")
        assert read_setup_completed(ssp) is None

    def test_init_writes_no_setup_completed_anywhere(self, tmp_path):
        """Fresh init leaves the marker ABSENT — no marker, no 'none'."""
        cf = tmp_path / "kanibako_config.yaml"
        write_global_config(cf)
        assert load_doc(cf) == {}
        assert read_setup_completed(cf) is None


class TestRetiredTemplatesStamp:
    """R-38: the ``system.templates_stamp`` MECHANISM is gone, the LEAF is inert.

    The reader (``read_templates_stamp``), the gate (``template_staleness_gate``)
    and every writer were deleted.  What an existing host keeps on disk is an
    ORPHANED ``[system] templates_stamp`` leaf — the retired-``projects:``-section
    precedent — so the contract these tests pin is: the symbols are GONE, and a
    config still carrying the leaf loads and gates exactly like one without it.
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

        cf = tmp_path / "kanibako_config.yaml"
        write_global_config(cf)
        write_system_value(cf, "templates_stamp", "deadbeef")

        with pytest.raises(ConfigError) as exc:
            load_config(cf)
        assert "system.templates_stamp" in str(exc.value)

    def test_a_clean_config_still_resolves_every_path(self, tmp_path):
        """The other half: with the orphan gone the resolve is untouched."""
        from kanibako.settings.config import load_config
        from kanibako.settings.paths import resolve_system_paths
        from kanibako.settings.paths_defaults import SYSTEM_PATH_DEFAULTS

        clean = tmp_path / "clean.yaml"
        write_global_config(clean)
        kw = {"data_home": tmp_path / "data", "home": tmp_path / "home"}
        resolved = resolve_system_paths(load_config(clean).config_paths, **kw)
        assert set(resolved) >= set(SYSTEM_PATH_DEFAULTS)

    def test_orphaned_leaf_does_not_disturb_the_setup_gate(self, tmp_path):
        """The one gate that remains reads the same answer with the orphan present."""
        from packaging.version import Version

        import kanibako
        from kanibako.settings.config import setup_compat_gate
        from kanibako.settings.config_interface import write_system_value

        cf = tmp_path / "kanibako_config.yaml"
        write_system_value(
            cf, "setup_completed", Version(kanibako.__version__).base_version
        )
        write_system_value(cf, "templates_stamp", "deadbeef")
        assert setup_compat_gate(cf) is None  # == band: no nudge, no raise


class TestSetupCompatGate:
    """setup_compat_gate: the 5-band setup/config compatibility gate.

    The shipped constants are BCV == FCV == CurrentVer == 1.8.0 (verified
    2026-08-02, after the R-38 rider bumped BCV), which collapses several bands to
    empty ranges.  To exercise EACH band independently of the
    build version, most tests patch ``kanibako.__version__`` (CurrentVer) and the
    ``SETUP_BCV``/``SETUP_FCV`` module constants — the gate imports them inside
    the function, so patching the ``kanibako`` module attributes is honoured.
    """

    # --- helpers -----------------------------------------------------------
    def _gate(self):
        from kanibako.settings.config import setup_compat_gate

        return setup_compat_gate

    def _marker(self, tmp_path, value):
        from kanibako.settings.config_interface import write_system_value

        cf = tmp_path / "kanibako_config.yaml"
        write_system_value(cf, "setup_completed", value)
        return cf

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
        cf = tmp_path / "kanibako_config.yaml"
        write_global_config(cf)  # no setup_completed
        assert self._gate()(cf) == (
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
        cf = self._marker(tmp_path, "custom-build")
        assert self._gate()(cf) is None

    # --- band: ConfigVer == CurrentVer (no-op) -----------------------------
    def test_current_marker_no_op(self, tmp_path):
        from packaging.version import Version

        import kanibako

        # Marker == the current build's base version → == band → no-op.
        cf = self._marker(tmp_path, Version(kanibako.__version__).base_version)
        assert self._gate()(cf) is None

    def test_dev_marker_of_current_base_no_op(self, tmp_path):
        """A dev build of the current base reads as == (base-version compare)."""
        from packaging.version import Version

        import kanibako

        base = Version(kanibako.__version__).base_version
        cf = self._marker(tmp_path, f"{base}.dev26")
        assert self._gate()(cf) is None

    # --- band: ConfigVer > CurrentVer (ERROR) ------------------------------
    def test_newer_than_build_raises(self, tmp_path):
        from packaging.version import Version

        import kanibako
        from kanibako.errors import ConfigError

        # A version strictly greater than the build base → "from the future".
        newer = f"{Version(kanibako.__version__).major + 1}.0.0"
        assert Version(newer) > Version(Version(kanibako.__version__).base_version)
        cf = self._marker(tmp_path, newer)
        with pytest.raises(ConfigError) as exc:
            self._gate()(cf)
        assert "newer kanibako" in str(exc.value)

    # --- band: FCV <= ConfigVer < CurrentVer (SILENT BUMP) -----------------
    def test_forward_compatible_silently_bumps(self, tmp_path):
        from kanibako.settings.config import read_setup_completed

        cf = self._marker(tmp_path, "1.6.0")
        # Pretend the build advanced to 1.8.0 with BCV/FCV still 1.6.0.
        patches = self._patch_bands(version="1.8.0", bcv="1.6.0", fcv="1.6.0")
        for p in patches:
            p.start()
        try:
            assert self._gate()(cf) is None  # silent, no message
            # SIDE EFFECT: marker rewritten forward to CurrentVer.
            assert read_setup_completed(cf) == "1.8.0"
        finally:
            for p in patches:
                p.stop()

    def test_silent_bump_persists_then_no_op(self, tmp_path):
        """After a bump, a second run hits the == band (no further write)."""
        from kanibako.settings.config import read_setup_completed

        cf = self._marker(tmp_path, "1.6.0")
        patches = self._patch_bands(version="1.8.0", bcv="1.6.0", fcv="1.6.0")
        for p in patches:
            p.start()
        try:
            gate = self._gate()
            assert gate(cf) is None
            assert read_setup_completed(cf) == "1.8.0"
            # Re-run: now ConfigVer == CurrentVer → no-op, marker unchanged.
            assert gate(cf) is None
            assert read_setup_completed(cf) == "1.8.0"
        finally:
            for p in patches:
                p.stop()

    def test_silent_bump_failure_does_not_raise(self, tmp_path):
        """A failed bump WRITE must fall through (return None), never block."""
        from unittest.mock import patch

        from kanibako.settings.config import read_setup_completed

        cf = self._marker(tmp_path, "1.6.0")
        patches = self._patch_bands(version="1.8.0", bcv="1.6.0", fcv="1.6.0")
        for p in patches:
            p.start()
        try:
            with patch(
                "kanibako.settings.config_interface.write_system_value",
                side_effect=OSError("read-only"),
            ):
                assert self._gate()(cf) is None  # swallowed, no raise
            # Marker stays unchanged (the bump failed).
            assert read_setup_completed(cf) == "1.6.0"
        finally:
            for p in patches:
                p.stop()

    # --- band: BCV <= ConfigVer < FCV (NUDGE) ------------------------------
    def test_between_bcv_and_fcv_nudges(self, tmp_path):
        cf = self._marker(tmp_path, "1.6.0")
        # build 1.8.0, BCV 1.5.0, FCV 1.7.0 → 1.6.0 is in [BCV, FCV) → nudge.
        patches = self._patch_bands(version="1.8.0", bcv="1.5.0", fcv="1.7.0")
        for p in patches:
            p.start()
        try:
            assert self._gate()(cf) == (
                "kanibako setup is out of date — re-run 'kanibako setup'."
            )
            # NUDGE band does NOT rewrite the marker.
            from kanibako.settings.config import read_setup_completed

            assert read_setup_completed(cf) == "1.6.0"
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
        cf = self._marker(tmp_path, self._below_bcv())
        with pytest.raises(ConfigError) as exc:
            self._gate()(cf)
        assert "too old to auto-update" in str(exc.value)

    def test_dev_marker_of_older_base_raises(self, tmp_path):
        """A dev build of a genuinely older base is still < BCV → ERROR."""
        from kanibako.errors import ConfigError

        cf = self._marker(tmp_path, f"{self._below_bcv()}.dev1")  # base < BCV
        with pytest.raises(ConfigError):
            self._gate()(cf)

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

        cf = self._marker(tmp_path, marker)
        with pytest.raises(ConfigError) as exc:
            self._gate()(cf)
        assert "too old to auto-update" in str(exc.value)


class TestMergedConfig:
    def test_project_overrides_global(self, tmp_path):
        global_path = tmp_path / "global.yaml"
        project_path = tmp_path / BOX_META_FILE

        write_global_config(global_path)
        write_project_config(project_path, "my-image:v2")

        merged = load_merged_config(global_path, project_path)
        assert merged.box_image == "my-image:v2"

    def test_cli_overrides_all(self, tmp_path):
        global_path = tmp_path / "global.yaml"
        project_path = tmp_path / BOX_META_FILE

        write_global_config(global_path)
        write_project_config(project_path, "my-image:v2")

        merged = load_merged_config(
            global_path,
            project_path,
            cli_overrides={"box_image": "cli-image:v3"},
        )
        assert merged.box_image == "cli-image:v3"

    def test_workset_path_none_is_byte_identical(self, tmp_path):
        """Omitting workset_path must reproduce the pre-P2.2 global+project merge."""
        global_path = tmp_path / "global.yaml"
        project_path = tmp_path / BOX_META_FILE

        write_global_config(global_path)
        write_project_config(project_path, "my-image:v2")

        baseline = load_merged_config(global_path, project_path)
        with_none = load_merged_config(global_path, project_path, workset_path=None)
        assert with_none == baseline
        assert with_none.box_image == "my-image:v2"

    def test_workset_overrides_global(self, tmp_path):
        global_path = tmp_path / "global.yaml"
        workset_path = tmp_path / "ws-config.yaml"

        write_global_config(global_path)
        write_project_config(workset_path, "ws-image:v1")

        merged = load_merged_config(global_path, workset_path=workset_path)
        assert merged.box_image == "ws-image:v1"

    def test_project_overrides_workset(self, tmp_path):
        global_path = tmp_path / "global.yaml"
        workset_path = tmp_path / "ws-config.yaml"
        project_path = tmp_path / BOX_META_FILE

        write_global_config(global_path)
        write_project_config(workset_path, "ws-image:v1")
        write_project_config(project_path, "proj-image:v2")

        merged = load_merged_config(
            global_path, project_path, workset_path=workset_path
        )
        assert merged.box_image == "proj-image:v2"

    def test_cli_overrides_workset(self, tmp_path):
        global_path = tmp_path / "global.yaml"
        workset_path = tmp_path / "ws-config.yaml"

        write_global_config(global_path)
        write_project_config(workset_path, "ws-image:v1")

        merged = load_merged_config(
            global_path,
            workset_path=workset_path,
            cli_overrides={"box_image": "cli-image:v3"},
        )
        assert merged.box_image == "cli-image:v3"

    def test_default_no_op_when_no_workset_config(self, tmp_path):
        """A default-mode project with no workset config.yaml merges exactly as before."""
        global_path = tmp_path / "global.yaml"
        project_path = tmp_path / BOX_META_FILE
        missing_workset = tmp_path / "no-such-config.yaml"

        write_global_config(global_path)
        write_project_config(project_path, "my-image:v2")

        baseline = load_merged_config(global_path, project_path)
        with_missing = load_merged_config(
            global_path, project_path, workset_path=missing_workset
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
    absence, ``null``/empty resets to the built-in default, ``""`` is a real value —
    are unchanged and are what these cases were always about.
    """

    def test_no_machine_config_path_attribute(self):
        """The deleted machine third-file is structurally gone (no attribute)."""
        import kanibako.settings.config as config_mod
        assert not hasattr(config_mod, "machine_config_path")

    def test_a_settings_tier_beats_builtin_defaults(self, tmp_path):
        global_path = tmp_path / "global.yaml"
        workset_path = tmp_path / "ws-config.yaml"
        workset_path.write_text("box:\n  image: user-image:v2\n")
        merged = load_merged_config(global_path, workset_path=workset_path)
        assert merged.box_image == "user-image:v2"

    def test_full_precedence_workset_project(self, tmp_path):
        global_path = tmp_path / "global.yaml"
        workset_path = tmp_path / "ws-config.yaml"
        workset_path.write_text("box:\n  image: ws:3\n  shell: bash\n")
        project_path = tmp_path / BOX_META_FILE
        project_path.write_text("box:\n  image: proj:4\n")
        merged = load_merged_config(
            global_path, project_path, workset_path=workset_path
        )
        # project wins for image; shell only set at the workset tier so it survives.
        # (⮕ P7: this used ``agent_name``, a key that no longer exists — the agent
        # SELECTION is the §2h request ``pref.system.agent``, resolved off the
        # snapshot, not by this flat scalar loader.)
        assert merged.box_image == "proj:4"
        assert merged.box_shell == "bash"

    def test_missing_global_file_is_empty_level(self, tmp_path):
        # No file at all → built-in defaults.
        merged = load_merged_config(tmp_path / "absent.yaml")
        assert merged.box_image == "ghcr.io/doctorjei/kanibako-oci:latest"

    def test_higher_layer_overrides_lower(self, tmp_path):
        global_path = tmp_path / "global.yaml"
        workset_path = tmp_path / "ws-config.yaml"
        workset_path.write_text("box:\n  image: img:workset\n")
        merged = load_merged_config(global_path, workset_path=workset_path)
        assert merged.box_image == "img:workset"
        # A box layer overrides the workset value (presence-based).
        project_path = tmp_path / BOX_META_FILE
        project_path.write_text("box:\n  image: img:box\n")
        merged2 = load_merged_config(
            global_path, project_path, workset_path=workset_path
        )
        assert merged2.box_image == "img:box"

    def test_set_to_default_value_sticks(self, tmp_path):
        """A layer setting a field to the built-in default wins over a lower
        layer's non-default (presence beats the old ``!= default`` guard)."""
        default_img = "ghcr.io/doctorjei/kanibako-oci:latest"
        global_path = tmp_path / "global.yaml"
        workset_path = tmp_path / "ws-config.yaml"
        workset_path.write_text("box:\n  image: img:custom\n")
        project_path = tmp_path / BOX_META_FILE
        # Explicitly set the built-in default — must win.
        project_path.write_text(f"box:\n  image: {default_img}\n")
        merged = load_merged_config(
            global_path, project_path, workset_path=workset_path
        )
        assert merged.box_image == default_img

    def test_null_resets_to_default(self, tmp_path):
        """A YAML ``null`` in a more-specific layer resets to the built-in
        default, discarding a lower layer's non-default value."""
        global_path = tmp_path / "global.yaml"
        workset_path = tmp_path / "ws-config.yaml"
        workset_path.write_text("box:\n  image: img:custom\n")
        project_path = tmp_path / BOX_META_FILE
        project_path.write_text("box:\n  image: null\n")
        merged = load_merged_config(
            global_path, project_path, workset_path=workset_path
        )
        assert merged.box_image == "ghcr.io/doctorjei/kanibako-oci:latest"

    def test_empty_value_resets_to_default(self, tmp_path):
        """An empty ``foo:`` (parses to None) also resets to the built-in
        default, same as an explicit ``null``."""
        global_path = tmp_path / "global.yaml"
        workset_path = tmp_path / "ws-config.yaml"
        workset_path.write_text("box:\n  image: img:custom\n")
        project_path = tmp_path / BOX_META_FILE
        project_path.write_text("box:\n  image:\n")
        merged = load_merged_config(
            global_path, project_path, workset_path=workset_path
        )
        assert merged.box_image == "ghcr.io/doctorjei/kanibako-oci:latest"

    def test_empty_string_is_a_real_value_not_unset(self, tmp_path):
        """``""`` is a real value distinct from ``null``: a lower layer sets a
        non-empty box_shell, a higher layer sets ``""`` and that ``""`` wins (it
        does NOT reset to box_shell's built-in default, which is also "").

        (⮕ P7: was written against ``box_agent_name``, retired with spec §2b; the
        SHAPE under test is the presence-based scalar overlay, not that key.)"""
        global_path = tmp_path / "global.yaml"
        workset_path = tmp_path / "ws-config.yaml"
        workset_path.write_text('box:\n  shell: foo\n')
        project_path = tmp_path / BOX_META_FILE
        # Quoted empty string is a real value, not null.
        project_path.write_text('box:\n  shell: ""\n')
        merged = load_merged_config(
            global_path, project_path, workset_path=workset_path
        )
        assert merged.box_shell == ""
        # Sanity: a non-empty lower value is what we are overriding away from.
        merged_ws_only = load_merged_config(global_path, workset_path=workset_path)
        assert merged_ws_only.box_shell == "foo"

    def test_higher_layer_overrides_after_null(self, tmp_path):
        """A null reset is not terminal: a higher layer (CLI override) can set a
        concrete value afterward and it wins."""
        global_path = tmp_path / "global.yaml"
        workset_path = tmp_path / "ws-config.yaml"
        workset_path.write_text("box:\n  image: null\n")
        merged = load_merged_config(
            global_path, workset_path=workset_path,
            cli_overrides={"box_image": "img:cli"},
        )
        assert merged.box_image == "img:cli"


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


class TestWriteProjectConfig:
    def test_creates_new(self, tmp_path):
        path = tmp_path / BOX_META_FILE
        write_project_config(path, "new-image:latest")
        assert _present_scalar_fields(path)["box_image"] == "new-image:latest"

    def test_updates_existing(self, tmp_path):
        path = tmp_path / BOX_META_FILE
        write_project_config(path, "first:latest")
        write_project_config(path, "second:latest")
        assert _present_scalar_fields(path)["box_image"] == "second:latest"

    def test_update_existing_image(self, tmp_path):
        p = tmp_path / BOX_META_FILE
        write_project_config(p, "img:v1")
        assert "image: img:v1" in p.read_text()
        write_project_config(p, "img:v2")
        text = p.read_text()
        assert "image: img:v2" in text
        assert "img:v1" not in text

    def test_add_image_to_container_section(self, tmp_path):
        p = tmp_path / BOX_META_FILE
        p.write_text("box:\n  # empty section\n")
        write_project_config(p, "new:img")
        text = p.read_text()
        assert "image: new:img" in text

    def test_create_new_file(self, tmp_path):
        p = tmp_path / "sub" / BOX_META_FILE
        write_project_config(p, "fresh:v1")
        assert p.exists()
        assert "box:" in p.read_text()
        assert "image: fresh:v1" in p.read_text()


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
        there that this reader does not honour reds here instead of outdating a list.
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
        from kanibako.settings.config import resolve_box_enable_vault

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
        from kanibako.settings.config import resolve_box_enable_vault

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
        merged = load_merged_config(
            std.config_file, box_file, workset_path=ws_file,
        ).box_enable_vault
        assert narrow is expected
        assert narrow == merged, (
            f"the narrow resolve says {narrow!r} and load_merged_config says {merged!r} "
            f"for the same three files — the two routes have drifted"
        )


class TestConfigFilePath:
    def test_returns_new_path_when_neither_exists(self, tmp_path):
        result = config_file_path(tmp_path)
        assert result == tmp_path / "kanibako_config.yaml"

    def test_returns_new_path_when_new_exists(self, tmp_path):
        new = tmp_path / "kanibako_config.yaml"
        new.write_text("paths:\n")
        result = config_file_path(tmp_path)
        assert result == new

    def test_ignores_legacy_old_subdir_location(self, tmp_path):
        # An old-location file is no longer recognized: always resolves
        # to the current top-level location.
        old = tmp_path / "kanibako" / "kanibako_config.yaml"
        old.parent.mkdir()
        old.write_text("paths:\n")
        result = config_file_path(tmp_path)
        assert result == tmp_path / "kanibako_config.yaml"


class TestTargetSettings:
    """Tests for target setting override storage in box.yaml."""

    def _write_base_toml(self, path):
        """Write a minimal box.yaml for testing."""
        write_project_config(path, "base:image")

    def test_round_trip(self, tmp_path):
        """Write and read back agent-keyed target settings."""
        p = tmp_path / BOX_META_FILE
        self._write_base_toml(p)
        write_agent_setting(p, "model", "sonnet", "claude")
        write_agent_setting(p, "access", "permissive", "claude")

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
        self._write_base_toml(p)
        write_agent_setting(p, "model", "sonnet", "default")

        assert read_agent_settings(p, "claude") == {"model": "sonnet"}
        assert read_agent_settings(p, "goose") == {"model": "sonnet"}

    def test_agent_specific_wins_over_default(self, tmp_path):
        """agent.<agent> overrides agent.default within one file."""
        p = tmp_path / BOX_META_FILE
        self._write_base_toml(p)
        write_agent_setting(p, "model", "sonnet", "default")
        write_agent_setting(p, "model", "opus", "claude")

        assert read_agent_settings(p, "claude") == {"model": "opus"}
        # A different agent still gets the default tier.
        assert read_agent_settings(p, "goose") == {"model": "sonnet"}

    def test_no_bleed_across_agents(self, tmp_path):
        """An override set for one agent does NOT bleed onto another (B3 bug)."""
        p = tmp_path / BOX_META_FILE
        self._write_base_toml(p)
        write_agent_setting(p, "model", "sonnet", "claude")

        assert read_agent_settings(p, "claude") == {"model": "sonnet"}
        assert read_agent_settings(p, "goose") == {}

    def test_preserves_other_sections(self, tmp_path):
        """Writing target settings doesn't clobber other sections."""
        p = tmp_path / BOX_META_FILE
        self._write_base_toml(p)
        write_agent_setting(p, "model", "haiku", "claude")

        # The base box section should still be intact.
        assert _present_scalar_fields(p)["box_image"] == "base:image"


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


class TestMergedConfigKeyspaceResolve:
    """B6 (R-11a(a), option (b)): ``load_merged_config``'s box scalars resolve
    through the KEYSPACE — one resolve behind every consumer, agent-lessly.
    """

    def _global(self, tmp_path, monkeypatch):
        """An EMPTY Layer-1 file + the XDG env the resolve reads.

        ⚑ It used to write ``box.image=global-img:1`` in here, because the config
        file's ``[box]`` table was the resolve's FLOOR. Jei retired that on
        2026-08-26 ("kanibako_config.yaml <-- cannot have settings. Period."), so the
        helper plants nothing and the cases below name their own tier.
        """
        from kanibako.settings.config import write_global_config

        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
        (tmp_path / "config").mkdir(exist_ok=True)
        gp = tmp_path / "config" / "kanibako_config.yaml"
        write_global_config(gp)
        return gp

    def test_floor_is_the_declared_default_not_the_layer1_file(
        self, tmp_path, monkeypatch,
    ):
        """The resolve's floor is the DECLARED DEFAULT — and a ``[box]`` table in the
        Layer-1 file does not displace it.

        ⚑ THE REPLACEMENT for ``test_stored_system_default_is_mapped_not_stranded``,
        and it asserts the OPPOSITE. That case pinned "consumer-map risk 1": the
        ``kanibako_config.yaml [box]`` table was written at init on EVERY install and
        the settings cascade did not read it, so its values would be STRANDED unless
        mapped in as the floor. Nothing settings-shaped is written there any more, so
        there is nothing to strand — and a table left by hand must not resolve.

        ⚑ 2026-08-31: the planted table is REFUSED rather than merely not resolving, so
        the floor is measured on a CLEAN file. Which value the planted one loses to is
        no longer a question the code can be asked.
        """
        gp = self._global(tmp_path, monkeypatch)
        gp.write_text("")
        merged = load_merged_config(gp, None)
        assert merged.box_image == KanibakoConfig().box_image

        gp.write_text("box:\n  image: layer1:planted\n")
        with pytest.raises(ConfigError) as exc:
            load_merged_config(gp, None)
        assert "box.image" in str(exc.value)

    def test_box_tier_beats_workset_beats_global(self, tmp_path, monkeypatch):
        gp = self._global(tmp_path, monkeypatch)
        ws = tmp_path / "wconfig.yaml"
        ws.write_text("box:\n  image: ws-img:2\n")
        bt = tmp_path / BOX_META_FILE
        bt.write_text("box:\n  image: box-img:3\n  shell: zsh\n")
        assert load_merged_config(gp, None, workset_path=ws).box_image == "ws-img:2"
        merged = load_merged_config(gp, bt, workset_path=ws)
        assert merged.box_image == "box-img:3"
        assert merged.box_shell == "zsh"  # box.shell rides the same resolve

    def test_system_settings_file_box_table_now_resolves(self, tmp_path, monkeypatch):
        """``kanibako system set box.image=…`` has always written the
        ``box:`` table of global/settings.yaml — stranded before B6, live now."""
        gp = self._global(tmp_path, monkeypatch)
        ssp = tmp_path / "data" / "kanibako" / "global" / "settings.yaml"
        ssp.parent.mkdir(parents=True)
        ssp.write_text("box:\n  image: sys-img:4\n")
        assert load_merged_config(gp, None).box_image == "sys-img:4"
        # ...but every settings-file tier above it still wins.
        bt = tmp_path / BOX_META_FILE
        bt.write_text("box:\n  image: box-img:3\n")
        assert load_merged_config(gp, bt).box_image == "box-img:3"

    def test_cli_level_outranks_every_file(self, tmp_path, monkeypatch):
        gp = self._global(tmp_path, monkeypatch)
        bt = tmp_path / BOX_META_FILE
        bt.write_text("box:\n  image: box-img:3\n")
        merged = load_merged_config(
            gp, bt,
            cli_overrides={"box_image": "cli-img:9", "box_share_images": True},
        )
        assert merged.box_image == "cli-img:9"
        assert merged.box_share_images is True

    def test_share_images_resolves_as_a_bool_from_files(self, tmp_path, monkeypatch):
        gp = self._global(tmp_path, monkeypatch)
        bt = tmp_path / BOX_META_FILE
        bt.write_text("box:\n  share_images: true\n")
        assert load_merged_config(gp, bt).box_share_images is True

    def test_agentless_resolve_no_agent_required(self, tmp_path, monkeypatch):
        """The resolve is AGENT-LESS by construction (the ``kanibako shell``
        requirement): nothing here selects or consults an agent, and a host with
        zero agents still resolves the box scalars."""
        gp = self._global(tmp_path, monkeypatch)
        merged = load_merged_config(gp, None)
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

    def test_valid_yaml_is_untouched(self, tmp_path):
        """The guard is a normalization, not a new refusal."""
        from kanibako.settings.config_io import load_doc

        good = tmp_path / BOX_META_FILE
        good.write_text("box:\n  image: ok:1\n")
        assert load_doc(good) == {"box": {"image": "ok:1"}}

    def test_boxless_merged_resolve_raises_the_named_error(
        self, tmp_path, monkeypatch,
    ):
        """The BOX-LESS shape (``load_merged_config(cf, None)``) — the one every
        rig/setup/baseline call site uses — surfaces the named error."""
        from kanibako.errors import ConfigError

        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
        (tmp_path / "config").mkdir(exist_ok=True)
        gp = tmp_path / "config" / "kanibako_config.yaml"
        write_global_config(gp)
        ssp = tmp_path / "data" / "kanibako" / "global" / "settings.yaml"
        ssp.parent.mkdir(parents=True)
        ssp.write_text(self._CORRUPT)

        with pytest.raises(ConfigError) as exc:
            load_merged_config(gp, None)
        assert str(ssp) in str(exc.value)

    def test_boxless_verb_exits_rc1_with_a_clean_message(
        self, tmp_path, monkeypatch, capsys,
    ):
        """E2E through ``main(["rig", "list"])``: rc1 + ``Error: …``, no traceback.

        ``rig list`` is a BOX-LESS verb whose ``load_merged_config(cf, None)``
        reaches the corrupt system settings file; before the normalization it
        died with a ``yaml.parser.ParserError`` traceback.
        """
        from unittest.mock import patch

        from kanibako.cli import main

        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
        (tmp_path / "config").mkdir(exist_ok=True)
        write_global_config(tmp_path / "config" / "kanibako_config.yaml")
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
