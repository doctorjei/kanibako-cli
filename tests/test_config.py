"""Tests for kanibako.config."""

from __future__ import annotations

import pytest

from kanibako.config import (
    KanibakoConfig,
    _flatten_toml,
    config_file_path,
    load_config,
    load_merged_config,
    read_box_enable_vault,
    read_resource_overrides,
    read_setup_completed,
    read_agent_settings,
    remove_resource_override,
    remove_agent_setting,
    write_box_enable_vault,
    write_global_config,
    write_project_config,
    write_resource_override,
    write_agent_setting,
)


class TestLoadConfig:
    def test_defaults(self, tmp_path):
        cfg = load_config(tmp_path / "nonexistent.yaml")
        assert cfg.box_image == "ghcr.io/doctorjei/kanibako-oci:latest"
        assert cfg.config_paths == {}

    def test_round_trip(self, tmp_path):
        path = tmp_path / "test.yaml"
        cfg = KanibakoConfig(box_image="custom:latest")
        write_global_config(path, cfg)
        loaded = load_config(path)
        assert loaded.box_image == "custom:latest"
        # The written [config] table holds the Layer-1 DEFAULT expressions.
        assert loaded.config_paths["config.data"] == "$XDG_DATA_HOME/kanibako"
        assert loaded.config_paths["config.agents"] == "@config.data/agents"

    def test_emits_channelroot_not_stale_channels_leaf(self, tmp_path):
        """write_global_config emits the RENAMED ``system.channelroot`` root leaf.

        The 198e6ea rename made the channels-root key ``channelroot`` (a node is a
        scalar XOR a subtree, so the old bare ``channels`` leaf collided with the
        ``channels.*`` branch).  This writer's L268 comment promises lock-step with
        ``SYSTEM_PATH_DEFAULTS``, which uses ``channelroot`` — so the emitted key
        must be ``channelroot``, never the stale ``channels``.
        """
        path = tmp_path / "g.yaml"
        write_global_config(path)
        loaded = load_config(path)
        assert loaded.config_paths["system.channelroot"] == "@config.data/channels"
        # The stale bare leaf must NOT be emitted (it would collide in the
        # nested KeyStore with the system.channels.* branch).
        assert "system.channels" not in loaded.config_paths

    def test_channelroot_round_trips_through_load_std_paths(self, tmp_home):
        """A config written by write_global_config resolves cleanly end-to-end:
        the renamed channelroot leaf AND the channels.* children all resolve."""
        from kanibako.paths import load_std_paths

        cf = tmp_home / "config" / "kanibako_config.yaml"
        write_global_config(cf)
        std = load_std_paths(load_config(cf))
        # channelroot leaf -> the channels root dir; children hang off it.
        assert std.channels == std.data_path / "channels"
        assert std.channels_commons == std.channels / "commons"
        assert std.channels_broadcast == std.channels / "chat" / "broadcast.md"

    def test_null_value_resolves_to_default(self, tmp_path):
        """A lone file with ``foo: null`` resolves foo to its built-in default."""
        path = tmp_path / "n.yaml"
        path.write_text("box:\n  bootstrap_program: null\n")
        cfg = load_config(path)
        assert cfg.box_bootstrap_program == "tmux"

    def test_empty_value_resolves_to_default(self, tmp_path):
        """An empty ``foo:`` (None) resolves foo to its built-in default."""
        path = tmp_path / "e.yaml"
        path.write_text("box:\n  bootstrap_program:\n")
        cfg = load_config(path)
        assert cfg.box_bootstrap_program == "tmux"

    def test_config_table_populates_config_paths(self, tmp_path):
        """[config] keys land in cfg.config_paths (full dotted names)."""
        path = tmp_path / "sys.yaml"
        path.write_text('config:\n  agents: "/x"\n')
        cfg = load_config(path)
        assert cfg.config_paths == {"config.agents": "/x"}


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


class TestReadSetupCompleted:
    """read_setup_completed: raw [system] setup_completed reader (W1 gate)."""

    def test_reads_stored_string(self, tmp_path):
        from kanibako.config_interface import write_system_value

        cf = tmp_path / "kanibako_config.yaml"
        write_system_value(cf, "setup_completed", "1.6.0")
        assert read_setup_completed(cf) == "1.6.0"

    def test_absent_key_returns_none(self, tmp_path):
        cf = tmp_path / "kanibako_config.yaml"
        write_global_config(cf)  # has [system] but no setup_completed
        assert read_setup_completed(cf) is None

    def test_missing_file_returns_none(self, tmp_path):
        assert read_setup_completed(tmp_path / "nope.yaml") is None
        assert read_setup_completed(None) is None

    def test_empty_value_returns_none(self, tmp_path):
        cf = tmp_path / "kanibako_config.yaml"
        cf.write_text("system:\n  setup_completed: ''\n")
        assert read_setup_completed(cf) is None

    def test_init_writes_no_setup_completed_or_default_agent(self, tmp_path):
        """Fresh init leaves both ABSENT — no default_agent, no marker, no 'none'."""
        from kanibako.config_io import load_doc

        cf = tmp_path / "kanibako_config.yaml"
        write_global_config(cf)
        data = load_doc(cf)
        # No setup marker on a fresh config.
        assert "setup_completed" not in data.get("system", {})
        assert read_setup_completed(cf) is None
        # No system default agent written (box.agent_name default is empty, not 'none').
        assert "agent" not in data
        assert data["box"]["agent_name"] == ""
        assert data["box"]["agent_name"] != "none"


class TestSetupCompatGate:
    """setup_compat_gate: the 5-band setup/config compatibility gate.

    The shipped constants are BCV == FCV == CurrentVer == 1.6.0, which collapses
    several bands to empty ranges.  To exercise EACH band independently of the
    build version, most tests patch ``kanibako.__version__`` (CurrentVer) and the
    ``SETUP_BCV``/``SETUP_FCV`` module constants — the gate imports them inside
    the function, so patching the ``kanibako`` module attributes is honoured.
    """

    # --- helpers -----------------------------------------------------------
    def _gate(self):
        from kanibako.config import setup_compat_gate

        return setup_compat_gate

    def _marker(self, tmp_path, value):
        from kanibako.config_interface import write_system_value

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
        from kanibako.config import read_setup_completed

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
        from kanibako.config import read_setup_completed

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

        from kanibako.config import read_setup_completed

        cf = self._marker(tmp_path, "1.6.0")
        patches = self._patch_bands(version="1.8.0", bcv="1.6.0", fcv="1.6.0")
        for p in patches:
            p.start()
        try:
            with patch(
                "kanibako.config_interface.write_system_value",
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
            from kanibako.config import read_setup_completed

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


class TestMergedConfig:
    def test_project_overrides_global(self, tmp_path):
        global_path = tmp_path / "global.yaml"
        project_path = tmp_path / "settings.yaml"

        write_global_config(global_path)
        write_project_config(project_path, "my-image:v2")

        merged = load_merged_config(global_path, project_path)
        assert merged.box_image == "my-image:v2"

    def test_cli_overrides_all(self, tmp_path):
        global_path = tmp_path / "global.yaml"
        project_path = tmp_path / "settings.yaml"

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
        project_path = tmp_path / "settings.yaml"

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
        project_path = tmp_path / "settings.yaml"

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
        project_path = tmp_path / "settings.yaml"
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
    two-layer path reshape (block #3a): ``load_merged_config`` no longer consults
    any ``machine_config_path``.  The least-specific FILE source is now the user
    global; the layers are user-global < workset < project < CLI, over the
    built-in defaults.  These tests exercise the SAME overlay semantics through
    the surviving layers.
    """

    def test_no_machine_config_path_attribute(self):
        """The deleted machine third-file is structurally gone (no attribute)."""
        import kanibako.config as config_mod
        assert not hasattr(config_mod, "machine_config_path")

    def test_user_global_beats_builtin_defaults(self, tmp_path):
        global_path = tmp_path / "global.yaml"
        global_path.write_text("box:\n  image: user-image:v2\n")
        merged = load_merged_config(global_path)
        assert merged.box_image == "user-image:v2"

    def test_full_precedence_user_workset_project(self, tmp_path):
        global_path = tmp_path / "global.yaml"
        global_path.write_text("box:\n  image: user:2\n  agent_name: claude\n")
        workset_path = tmp_path / "ws-config.yaml"
        workset_path.write_text("box:\n  image: ws:3\n")
        project_path = tmp_path / "settings.yaml"
        project_path.write_text("box:\n  image: proj:4\n")
        merged = load_merged_config(
            global_path, project_path, workset_path=workset_path
        )
        # project wins for image; agent only set at user-global so it survives.
        assert merged.box_image == "proj:4"
        assert merged.box_agent_name == "claude"

    def test_missing_global_file_is_empty_level(self, tmp_path):
        # No file at all → built-in defaults.
        merged = load_merged_config(tmp_path / "absent.yaml")
        assert merged.box_image == "ghcr.io/doctorjei/kanibako-oci:latest"

    def test_higher_layer_overrides_lower(self, tmp_path):
        global_path = tmp_path / "global.yaml"
        global_path.write_text("box:\n  bootstrap_program: zellij\n")
        merged = load_merged_config(global_path)
        assert merged.box_bootstrap_program == "zellij"
        # A workset layer overrides the user-global value (presence-based).
        workset_path = tmp_path / "ws-config.yaml"
        workset_path.write_text("box:\n  bootstrap_program: screen\n")
        merged2 = load_merged_config(global_path, workset_path=workset_path)
        assert merged2.box_bootstrap_program == "screen"

    def test_set_to_default_value_sticks(self, tmp_path):
        """A layer setting a field to the built-in default wins over a lower
        layer's non-default (presence beats the old ``!= default`` guard)."""
        global_path = tmp_path / "global.yaml"
        global_path.write_text("box:\n  bootstrap_program: zellij\n")
        project_path = tmp_path / "settings.yaml"
        # Explicitly set the built-in default "tmux" — must win.
        project_path.write_text("box:\n  bootstrap_program: tmux\n")
        merged = load_merged_config(global_path, project_path)
        assert merged.box_bootstrap_program == "tmux"

    def test_null_resets_to_default(self, tmp_path):
        """A YAML ``null`` in a more-specific layer resets to the built-in
        default, discarding a lower layer's non-default value."""
        global_path = tmp_path / "global.yaml"
        global_path.write_text("box:\n  bootstrap_program: zellij\n")
        project_path = tmp_path / "settings.yaml"
        project_path.write_text("box:\n  bootstrap_program: null\n")
        merged = load_merged_config(global_path, project_path)
        assert merged.box_bootstrap_program == "tmux"

    def test_empty_value_resets_to_default(self, tmp_path):
        """An empty ``foo:`` (parses to None) also resets to the built-in
        default, same as an explicit ``null``."""
        global_path = tmp_path / "global.yaml"
        global_path.write_text("box:\n  bootstrap_program: screen\n")
        project_path = tmp_path / "settings.yaml"
        project_path.write_text("box:\n  bootstrap_program:\n")
        merged = load_merged_config(global_path, project_path)
        assert merged.box_bootstrap_program == "tmux"

    def test_empty_string_is_a_real_value_not_unset(self, tmp_path):
        """``""`` is a real value distinct from ``null``: a lower layer sets a
        non-empty box_agent_name, a higher layer sets ``""`` and that ``""`` wins
        (it does NOT reset to box_agent_name's built-in default, which is also "")."""
        global_path = tmp_path / "global.yaml"
        global_path.write_text('box:\n  agent_name: foo\n')
        project_path = tmp_path / "settings.yaml"
        # Quoted empty string is a real value, not null.
        project_path.write_text('box:\n  agent_name: ""\n')
        merged = load_merged_config(global_path, project_path)
        assert merged.box_agent_name == ""
        # Sanity: a non-empty lower value is what we are overriding away from.
        merged_global_only = load_merged_config(global_path)
        assert merged_global_only.box_agent_name == "foo"

    def test_higher_layer_overrides_after_null(self, tmp_path):
        """A null reset is not terminal: a higher layer (CLI override) can set a
        concrete value afterward and it wins."""
        global_path = tmp_path / "global.yaml"
        global_path.write_text("box:\n  bootstrap_program: null\n")
        merged = load_merged_config(
            global_path, cli_overrides={"box_bootstrap_program": "screen"}
        )
        assert merged.box_bootstrap_program == "screen"

    def test_bootstrap_program_default(self, tmp_path):
        merged = load_merged_config(tmp_path / "no-global.yaml")
        assert merged.box_bootstrap_program == "tmux"


class TestFlattenToml:
    def test_nested_dict(self):
        data = {"paths": {"boxes": "x", "shell": "y"}}
        flat = _flatten_toml(data)
        assert flat == {"paths_boxes": "x", "paths_shell": "y"}

    def test_deeply_nested(self):
        data = {"a": {"b": {"c": "deep"}}}
        flat = _flatten_toml(data)
        assert flat == {"a_b_c": "deep"}

    def test_flat_input(self):
        data = {"key": "val"}
        flat = _flatten_toml(data)
        assert flat == {"key": "val"}


class TestWriteProjectConfig:
    def test_creates_new(self, tmp_path):
        path = tmp_path / "settings.yaml"
        write_project_config(path, "new-image:latest")
        cfg = load_config(path)
        assert cfg.box_image == "new-image:latest"

    def test_updates_existing(self, tmp_path):
        path = tmp_path / "settings.yaml"
        write_project_config(path, "first:latest")
        write_project_config(path, "second:latest")
        cfg = load_config(path)
        assert cfg.box_image == "second:latest"

    def test_update_existing_image(self, tmp_path):
        p = tmp_path / "settings.yaml"
        write_project_config(p, "img:v1")
        assert "image: img:v1" in p.read_text()
        write_project_config(p, "img:v2")
        text = p.read_text()
        assert "image: img:v2" in text
        assert "img:v1" not in text

    def test_add_image_to_container_section(self, tmp_path):
        p = tmp_path / "settings.yaml"
        p.write_text("box:\n  # empty section\n")
        write_project_config(p, "new:img")
        text = p.read_text()
        assert "image: new:img" in text

    def test_create_new_file(self, tmp_path):
        p = tmp_path / "sub" / "settings.yaml"
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
        from kanibako.config import load_doc
        p = tmp_path / "settings.yaml"
        write_box_enable_vault(p, enable_vault=False)
        data = load_doc(p)
        assert data["box"]["enable_vault"] is False
        assert isinstance(data["box"]["enable_vault"], bool)
        # Round-trips through the paired reader.
        assert read_box_enable_vault(p) is False

    def test_default_true_on_fresh_path_writes_nothing(self, tmp_path):
        """(b) default True on a fresh path writes NOTHING — no file, no empty
        ``box:`` table materialized."""
        p = tmp_path / "settings.yaml"
        write_box_enable_vault(p)  # default True
        assert not p.exists()
        # The absent file reads back as the default True.
        assert read_box_enable_vault(p) is True

    def test_default_true_drops_stale_override(self, tmp_path):
        """(c) default True with a stale box.enable_vault present → drops it."""
        from kanibako.config import load_doc
        p = tmp_path / "settings.yaml"
        p.write_text("box:\n  enable_vault: false\n")
        write_box_enable_vault(p)  # default True
        data = load_doc(p)
        assert "enable_vault" not in data.get("box", {})
        assert read_box_enable_vault(p) is True

    def test_disabled_merges_beside_existing_box_image(self, tmp_path):
        """(d) disabled merges beside an existing box.image (preserves it)."""
        from kanibako.config import load_doc
        p = tmp_path / "settings.yaml"
        p.write_text('box:\n  image: "custom:v1"\n')
        write_box_enable_vault(p, enable_vault=False)
        data = load_doc(p)
        assert data["box"]["image"] == "custom:v1"
        assert data["box"]["enable_vault"] is False


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


class TestResourceOverrides:
    """Tests for resource scope override storage in settings.yaml."""

    def _write_base_toml(self, path):
        """Write a minimal settings.yaml for testing."""
        write_project_config(path, "base:image")

    def test_round_trip(self, tmp_path):
        """Write and read back resource overrides."""
        p = tmp_path / "settings.yaml"
        self._write_base_toml(p)
        write_resource_override(p, "plugins/", "project")
        write_resource_override(p, "settings.json", "shared")

        overrides = read_resource_overrides(p)
        assert overrides == {"plugins/": "project", "settings.json": "shared"}

    def test_backward_compat_no_section(self, tmp_path):
        """Old settings.yaml without [resource_overrides] returns empty dict."""
        p = tmp_path / "settings.yaml"
        self._write_base_toml(p)

        overrides = read_resource_overrides(p)
        assert overrides == {}

    def test_remove_override(self, tmp_path):
        """remove_resource_override removes a single override."""
        p = tmp_path / "settings.yaml"
        self._write_base_toml(p)
        write_resource_override(p, "plugins/", "project")
        write_resource_override(p, "cache/", "project")

        assert remove_resource_override(p, "plugins/") is True
        overrides = read_resource_overrides(p)
        assert "plugins/" not in overrides
        assert "cache/" in overrides

    def test_remove_nonexistent(self, tmp_path):
        """remove_resource_override returns False for missing key."""
        p = tmp_path / "settings.yaml"
        self._write_base_toml(p)

        assert remove_resource_override(p, "nonexistent/") is False

    def test_preserves_other_sections(self, tmp_path):
        """Writing resource overrides doesn't clobber other sections."""
        p = tmp_path / "settings.yaml"
        self._write_base_toml(p)
        write_resource_override(p, "plugins/", "project")

        # The base box section should still be intact.
        cfg = load_config(p)
        assert cfg.box_image == "base:image"


class TestTargetSettings:
    """Tests for target setting override storage in settings.yaml."""

    def _write_base_toml(self, path):
        """Write a minimal settings.yaml for testing."""
        write_project_config(path, "base:image")

    def test_round_trip(self, tmp_path):
        """Write and read back agent-keyed target settings."""
        p = tmp_path / "settings.yaml"
        self._write_base_toml(p)
        write_agent_setting(p, "model", "sonnet", "claude")
        write_agent_setting(p, "access", "permissive", "claude")

        settings = read_agent_settings(p, "claude")
        assert settings == {"model": "sonnet", "access": "permissive"}

    def test_backward_compat_no_section(self, tmp_path):
        """settings.yaml without a [agent] section returns empty dict."""
        p = tmp_path / "settings.yaml"
        self._write_base_toml(p)

        settings = read_agent_settings(p, "claude")
        assert settings == {}

    def test_flat_legacy_crab_treated_as_unset(self, tmp_path):
        """A legacy FLAT [agent] table (scalars, no per-agent dicts) is ignored.

        Pass 1 does NOT migrate; only nested agent.<agent>/agent.default tiers
        are honored, so a hand-edited flat shape reads as empty.
        """
        from kanibako.config import dump_doc, load_doc

        p = tmp_path / "settings.yaml"
        self._write_base_toml(p)
        data = load_doc(p)
        data["agent"] = {"model": "sonnet"}  # flat scalar — old shape
        dump_doc(p, data)

        assert read_agent_settings(p, "claude") == {}

    def test_default_tier_applies_to_any_agent(self, tmp_path):
        """agent.default values apply to every agent unless overridden."""
        p = tmp_path / "settings.yaml"
        self._write_base_toml(p)
        write_agent_setting(p, "model", "sonnet", "default")

        assert read_agent_settings(p, "claude") == {"model": "sonnet"}
        assert read_agent_settings(p, "goose") == {"model": "sonnet"}

    def test_agent_specific_wins_over_default(self, tmp_path):
        """agent.<agent> overrides agent.default within one file."""
        p = tmp_path / "settings.yaml"
        self._write_base_toml(p)
        write_agent_setting(p, "model", "sonnet", "default")
        write_agent_setting(p, "model", "opus", "claude")

        assert read_agent_settings(p, "claude") == {"model": "opus"}
        # A different agent still gets the default tier.
        assert read_agent_settings(p, "goose") == {"model": "sonnet"}

    def test_no_bleed_across_agents(self, tmp_path):
        """An override set for one agent does NOT bleed onto another (B3 bug)."""
        p = tmp_path / "settings.yaml"
        self._write_base_toml(p)
        write_agent_setting(p, "model", "sonnet", "claude")

        assert read_agent_settings(p, "claude") == {"model": "sonnet"}
        assert read_agent_settings(p, "goose") == {}

    def test_remove_setting(self, tmp_path):
        """remove_agent_setting removes a single agent-keyed setting."""
        p = tmp_path / "settings.yaml"
        self._write_base_toml(p)
        write_agent_setting(p, "model", "sonnet", "claude")
        write_agent_setting(p, "access", "permissive", "claude")

        assert remove_agent_setting(p, "model", "claude") is True
        settings = read_agent_settings(p, "claude")
        assert "model" not in settings
        assert "access" in settings

    def test_remove_nonexistent(self, tmp_path):
        """remove_agent_setting returns False for missing key."""
        p = tmp_path / "settings.yaml"
        self._write_base_toml(p)

        assert remove_agent_setting(p, "nonexistent", "claude") is False

    def test_preserves_other_sections(self, tmp_path):
        """Writing target settings doesn't clobber other sections."""
        p = tmp_path / "settings.yaml"
        self._write_base_toml(p)
        write_agent_setting(p, "model", "haiku", "claude")

        # The base box section should still be intact.
        cfg = load_config(p)
        assert cfg.box_image == "base:image"


class TestReadBindingOverrides:
    """Phase 1h: agent-keyed descriptor binding host-source overrides
    (agent.<agent>.binding.<key>, layered over agent.default.binding)."""

    def _write(self, path, agent):
        from kanibako.config import dump_doc

        dump_doc(path, {"agent": agent})

    def test_absent_returns_empty(self, tmp_path):
        from kanibako.config import read_binding_overrides

        assert read_binding_overrides(None, "claude") == {}
        assert read_binding_overrides(tmp_path / "nope.yaml", "claude") == {}

    def test_no_crab_or_binding_returns_empty(self, tmp_path):
        from kanibako.config import read_binding_overrides

        p = tmp_path / "settings.yaml"
        p.write_text("box:\n  foo: bar\n")
        assert read_binding_overrides(p, "claude") == {}
        self._write(p, {"claude": {"model": "opus"}})  # crab, but no binding
        assert read_binding_overrides(p, "claude") == {}

    def test_bare_string_host_src(self, tmp_path):
        from kanibako.config import read_binding_overrides

        p = tmp_path / "settings.yaml"
        self._write(p, {"claude": {"binding": {"plugins": "/custom/plugins"}}})
        assert read_binding_overrides(p, "claude") == {"plugins": "/custom/plugins"}

    def test_subtable_host_src(self, tmp_path):
        from kanibako.config import read_binding_overrides

        p = tmp_path / "settings.yaml"
        self._write(
            p, {"claude": {"binding": {"plugins": {"host_src": "/custom/plugins"}}}}
        )
        assert read_binding_overrides(p, "claude") == {"plugins": "/custom/plugins"}

    def test_subtable_without_host_src_skipped(self, tmp_path):
        from kanibako.config import read_binding_overrides

        p = tmp_path / "settings.yaml"
        self._write(p, {"claude": {"binding": {"plugins": {"ro": True}}}})
        assert read_binding_overrides(p, "claude") == {}

    def test_default_tier_applies_to_any_agent(self, tmp_path):
        from kanibako.config import read_binding_overrides

        p = tmp_path / "settings.yaml"
        self._write(p, {"default": {"binding": {"plugins": "/shared/plugins"}}})
        assert read_binding_overrides(p, "claude") == {"plugins": "/shared/plugins"}
        assert read_binding_overrides(p, "goose") == {"plugins": "/shared/plugins"}

    def test_agent_specific_wins_over_default(self, tmp_path):
        from kanibako.config import read_binding_overrides

        p = tmp_path / "settings.yaml"
        self._write(
            p,
            {
                "default": {"binding": {"plugins": "/shared/plugins"}},
                "claude": {"binding": {"plugins": "/claude/plugins"}},
            },
        )
        assert read_binding_overrides(p, "claude") == {"plugins": "/claude/plugins"}
        # A different agent still gets the default tier.
        assert read_binding_overrides(p, "goose") == {"plugins": "/shared/plugins"}

    def test_no_bleed_across_agents(self, tmp_path):
        from kanibako.config import read_binding_overrides

        p = tmp_path / "settings.yaml"
        self._write(p, {"claude": {"binding": {"plugins": "/claude/plugins"}}})
        assert read_binding_overrides(p, "claude") == {"plugins": "/claude/plugins"}
        assert read_binding_overrides(p, "goose") == {}

    def test_flat_legacy_crab_treated_as_unset(self, tmp_path):
        from kanibako.config import read_binding_overrides

        p = tmp_path / "settings.yaml"
        self._write(p, {"binding": {"plugins": "/x"}})  # flat under crab — old shape
        assert read_binding_overrides(p, "claude") == {}
