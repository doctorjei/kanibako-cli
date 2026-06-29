"""Tests for kanibako.config."""

from __future__ import annotations



from kanibako.config import (
    KanibakoConfig,
    _flatten_toml,
    config_file_path,
    load_config,
    load_merged_config,
    read_project_meta,
    read_resource_overrides,
    read_setup_completed,
    read_agent_settings,
    remove_resource_override,
    remove_agent_setting,
    write_global_config,
    write_project_config,
    write_project_meta,
    write_resource_override,
    write_agent_setting,
)


class TestLoadConfig:
    def test_defaults(self, tmp_path):
        cfg = load_config(tmp_path / "nonexistent.yaml")
        assert cfg.box_image == "ghcr.io/doctorjei/kanibako-oci:latest"
        assert cfg.system_paths == {}

    def test_round_trip(self, tmp_path):
        path = tmp_path / "test.yaml"
        cfg = KanibakoConfig(box_image="custom:latest")
        write_global_config(path, cfg)
        loaded = load_config(path)
        assert loaded.box_image == "custom:latest"
        # The written [system] table holds DEFAULT expressions (bare keys).
        assert loaded.system_paths["system.data"] == "$XDG_DATA_HOME/kanibako"
        assert loaded.system_paths["system.agents"] == "@system.data/agents"

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
        assert loaded.system_paths["system.channelroot"] == "@system.data/channels"
        # The stale bare leaf must NOT be emitted (it would collide in the
        # nested KeyStore with the system.channels.* branch).
        assert "system.channels" not in loaded.system_paths

    def test_channelroot_round_trips_through_load_std_paths(self, tmp_home):
        """A config written by write_global_config resolves cleanly end-to-end:
        the renamed channelroot leaf AND the channels.* children all resolve."""
        from kanibako.paths import load_std_paths

        cf = tmp_home / "config" / "kanibako.yaml"
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

    def test_system_table_populates_system_paths(self, tmp_path):
        """[system] keys land in cfg.system_paths (bare dotted names)."""
        path = tmp_path / "sys.yaml"
        path.write_text('system:\n  agents: "/x"\n')
        cfg = load_config(path)
        assert cfg.system_paths == {"system.agents": "/x"}


class TestSetupVersionConstant:
    """OLDEST_COMPATIBLE_SETUP_VERSION is a PEP-440-parseable version string."""

    def test_constant_present_and_parseable(self):
        from packaging.version import Version

        from kanibako import OLDEST_COMPATIBLE_SETUP_VERSION, __version__

        assert OLDEST_COMPATIBLE_SETUP_VERSION == "1.6.0"
        # It must never be NEWER than the running build (would self-nudge).
        assert Version(OLDEST_COMPATIBLE_SETUP_VERSION) <= Version(__version__)


class TestReadSetupCompleted:
    """read_setup_completed: raw [system] setup_completed reader (W1 gate)."""

    def test_reads_stored_string(self, tmp_path):
        from kanibako.config_interface import write_system_value

        cf = tmp_path / "kanibako.yaml"
        write_system_value(cf, "setup_completed", "1.6.0")
        assert read_setup_completed(cf) == "1.6.0"

    def test_absent_key_returns_none(self, tmp_path):
        cf = tmp_path / "kanibako.yaml"
        write_global_config(cf)  # has [system] but no setup_completed
        assert read_setup_completed(cf) is None

    def test_missing_file_returns_none(self, tmp_path):
        assert read_setup_completed(tmp_path / "nope.yaml") is None
        assert read_setup_completed(None) is None

    def test_empty_value_returns_none(self, tmp_path):
        cf = tmp_path / "kanibako.yaml"
        cf.write_text("system:\n  setup_completed: ''\n")
        assert read_setup_completed(cf) is None

    def test_init_writes_no_setup_completed_or_default_agent(self, tmp_path):
        """Fresh init leaves both ABSENT — no default_agent, no marker, no 'none'."""
        from kanibako.config_io import load_doc

        cf = tmp_path / "kanibako.yaml"
        write_global_config(cf)
        data = load_doc(cf)
        # No setup marker on a fresh config.
        assert "setup_completed" not in data.get("system", {})
        assert read_setup_completed(cf) is None
        # No system default agent written (box.agent default is empty, not 'none').
        assert "agent" not in data
        assert data["box"]["agent"] == ""
        assert data["box"]["agent"] != "none"


class TestSetupNudgeMessage:
    """setup_nudge_message: Gate-1 absent/stale/current advisory (non-blocking)."""

    def test_absent_marker_nudges_setup(self, tmp_path):
        from kanibako.config import setup_nudge_message

        cf = tmp_path / "kanibako.yaml"
        write_global_config(cf)  # no setup_completed
        msg = setup_nudge_message(cf)
        assert msg == "kanibako isn't set up yet. Run 'kanibako setup' to get started."

    def test_missing_file_nudges_setup(self, tmp_path):
        from kanibako.config import setup_nudge_message

        assert setup_nudge_message(tmp_path / "nope.yaml") == (
            "kanibako isn't set up yet. Run 'kanibako setup' to get started."
        )
        assert setup_nudge_message(None) == (
            "kanibako isn't set up yet. Run 'kanibako setup' to get started."
        )

    def test_stale_marker_nudges_reupdate(self, tmp_path):
        from kanibako.config import setup_nudge_message
        from kanibako.config_interface import write_system_value

        cf = tmp_path / "kanibako.yaml"
        # A version older than OLDEST_COMPATIBLE_SETUP_VERSION (== "1.6.0").
        write_system_value(cf, "setup_completed", "1.5.0")
        assert setup_nudge_message(cf) == (
            "kanibako setup is out of date — re-run 'kanibako setup'."
        )

    def test_current_marker_no_nudge(self, tmp_path):
        from kanibako.config import setup_nudge_message
        from kanibako.config_interface import write_system_value

        cf = tmp_path / "kanibako.yaml"
        write_system_value(cf, "setup_completed", "1.6.0")
        assert setup_nudge_message(cf) is None

    def test_newer_marker_no_nudge(self, tmp_path):
        from kanibako.config import setup_nudge_message
        from kanibako.config_interface import write_system_value

        cf = tmp_path / "kanibako.yaml"
        write_system_value(cf, "setup_completed", "1.7.0")
        assert setup_nudge_message(cf) is None

    def test_dev_marker_of_current_base_no_nudge(self, tmp_path):
        """A dev/rc build of the current base must NOT nag right after setup.

        ``setup`` writes the running ``__version__`` (e.g. ``1.6.0.dev26``) as
        the marker.  PEP 440 makes ``1.6.0.dev26 < 1.6.0`` True, so a naive
        compare nags every dev build.  We compare by BASE version instead, so a
        dev build of the same base as the constant (== "1.6.0") is current.
        """
        from kanibako.config import setup_nudge_message
        from kanibako.config_interface import write_system_value

        cf = tmp_path / "kanibako.yaml"
        write_system_value(cf, "setup_completed", "1.6.0.dev26")
        assert setup_nudge_message(cf) is None

    def test_dev_marker_of_older_base_nudges(self, tmp_path):
        """A dev/rc build of a genuinely older base still nags."""
        from kanibako.config import setup_nudge_message
        from kanibako.config_interface import write_system_value

        cf = tmp_path / "kanibako.yaml"
        # 1.5.0.dev1 has base 1.5.0 < 1.6.0 → stale.
        write_system_value(cf, "setup_completed", "1.5.0.dev1")
        assert setup_nudge_message(cf) == (
            "kanibako setup is out of date — re-run 'kanibako setup'."
        )

    def test_unparseable_marker_no_nudge(self, tmp_path):
        """A hand-edited unparseable marker is treated as present (no nag)."""
        from kanibako.config import setup_nudge_message
        from kanibako.config_interface import write_system_value

        cf = tmp_path / "kanibako.yaml"
        write_system_value(cf, "setup_completed", "custom-build")
        assert setup_nudge_message(cf) is None


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


class TestMachineConfigLayer:
    """The /etc machine-wide layer: below user-global, above built-in defaults."""

    def _patch_machine(self, monkeypatch, path):
        import kanibako.config as config_mod
        monkeypatch.setattr(config_mod, "machine_config_path", lambda: path)

    def test_machine_beats_builtin_defaults(self, tmp_path, monkeypatch):
        machine = tmp_path / "machine.yaml"
        machine.write_text("box:\n  image: machine-image:v1\n")
        self._patch_machine(monkeypatch, machine)
        # No user global / project: machine value wins over the built-in default.
        merged = load_merged_config(tmp_path / "no-global.yaml")
        assert merged.box_image == "machine-image:v1"

    def test_user_global_beats_machine(self, tmp_path, monkeypatch):
        machine = tmp_path / "machine.yaml"
        machine.write_text("box:\n  image: machine-image:v1\n")
        self._patch_machine(monkeypatch, machine)
        global_path = tmp_path / "global.yaml"
        global_path.write_text("box:\n  image: user-image:v2\n")
        merged = load_merged_config(global_path)
        assert merged.box_image == "user-image:v2"

    def test_full_precedence_machine_user_workset_project(self, tmp_path, monkeypatch):
        machine = tmp_path / "machine.yaml"
        machine.write_text("box:\n  image: machine:1\n  agent: claude\n")
        self._patch_machine(monkeypatch, machine)
        global_path = tmp_path / "global.yaml"
        global_path.write_text("box:\n  image: user:2\n")
        workset_path = tmp_path / "ws-config.yaml"
        workset_path.write_text("box:\n  image: ws:3\n")
        project_path = tmp_path / "settings.yaml"
        project_path.write_text("box:\n  image: proj:4\n")
        merged = load_merged_config(
            global_path, project_path, workset_path=workset_path
        )
        # project wins for image; agent only set at machine so it survives.
        assert merged.box_image == "proj:4"
        assert merged.box_agent == "claude"

    def test_missing_machine_file_is_empty_level(self, tmp_path, monkeypatch):
        self._patch_machine(monkeypatch, tmp_path / "absent.yaml")
        global_path = tmp_path / "global.yaml"
        global_path.write_text("box:\n  image: user:1\n")
        merged = load_merged_config(global_path)
        assert merged.box_image == "user:1"

    def test_machine_bootstrap_program(self, tmp_path, monkeypatch):
        machine = tmp_path / "machine.yaml"
        machine.write_text("box:\n  bootstrap_program: zellij\n")
        self._patch_machine(monkeypatch, machine)
        merged = load_merged_config(tmp_path / "no-global.yaml")
        assert merged.box_bootstrap_program == "zellij"
        # User global overrides the machine value. The overlay is presence-based:
        # any value the user file actually sets wins over the lower layer, even
        # one equal to the built-in default.
        global_path = tmp_path / "global.yaml"
        global_path.write_text("box:\n  bootstrap_program: screen\n")
        merged2 = load_merged_config(global_path)
        assert merged2.box_bootstrap_program == "screen"

    def test_set_to_default_value_sticks(self, tmp_path, monkeypatch):
        """A layer setting a field to the built-in default wins over a lower
        layer's non-default (presence beats the old ``!= default`` guard)."""
        machine = tmp_path / "machine.yaml"
        machine.write_text("box:\n  bootstrap_program: zellij\n")
        self._patch_machine(monkeypatch, machine)
        global_path = tmp_path / "global.yaml"
        # User explicitly sets the built-in default "tmux" — must win.
        global_path.write_text("box:\n  bootstrap_program: tmux\n")
        merged = load_merged_config(global_path)
        assert merged.box_bootstrap_program == "tmux"

    def test_null_resets_to_default(self, tmp_path, monkeypatch):
        """A YAML ``null`` in a more-specific layer resets to the built-in
        default, discarding a lower layer's non-default value."""
        machine = tmp_path / "machine.yaml"
        machine.write_text("box:\n  bootstrap_program: zellij\n")
        self._patch_machine(monkeypatch, machine)
        global_path = tmp_path / "global.yaml"
        global_path.write_text("box:\n  bootstrap_program: null\n")
        merged = load_merged_config(global_path)
        assert merged.box_bootstrap_program == "tmux"

    def test_empty_value_resets_to_default(self, tmp_path, monkeypatch):
        """An empty ``foo:`` (parses to None) also resets to the built-in
        default, same as an explicit ``null``."""
        machine = tmp_path / "machine.yaml"
        machine.write_text("box:\n  bootstrap_program: zellij\n")
        self._patch_machine(monkeypatch, machine)
        # Reset via a more-specific project layer using an empty value.
        global_path = tmp_path / "global.yaml"
        global_path.write_text("box:\n  bootstrap_program: screen\n")
        project_path = tmp_path / "settings.yaml"
        project_path.write_text("box:\n  bootstrap_program:\n")
        merged = load_merged_config(global_path, project_path)
        assert merged.box_bootstrap_program == "tmux"

    def test_empty_string_is_a_real_value_not_unset(self, tmp_path, monkeypatch):
        """``""`` is a real value distinct from ``null``: a lower layer sets a
        non-empty box_agent, a higher layer sets ``""`` and that ``""`` wins
        (it does NOT reset to box_agent's built-in default, which is also "")."""
        machine = tmp_path / "machine.yaml"
        machine.write_text('box:\n  agent: foo\n')
        self._patch_machine(monkeypatch, machine)
        global_path = tmp_path / "global.yaml"
        # Quoted empty string is a real value, not null.
        global_path.write_text('box:\n  agent: ""\n')
        merged = load_merged_config(global_path)
        assert merged.box_agent == ""
        # Sanity: a non-empty lower value is what we are overriding away from.
        merged_machine_only = load_merged_config(tmp_path / "no-global.yaml")
        assert merged_machine_only.box_agent == "foo"

    def test_higher_layer_overrides_after_null(self, tmp_path, monkeypatch):
        """A null reset is not terminal: a higher layer (CLI override) can set a
        concrete value afterward and it wins."""
        machine = tmp_path / "machine.yaml"
        machine.write_text("box:\n  bootstrap_program: zellij\n")
        self._patch_machine(monkeypatch, machine)
        global_path = tmp_path / "global.yaml"
        global_path.write_text("box:\n  bootstrap_program: null\n")
        merged = load_merged_config(
            global_path, cli_overrides={"box_bootstrap_program": "screen"}
        )
        assert merged.box_bootstrap_program == "screen"

    def test_bootstrap_program_default(self, tmp_path, monkeypatch):
        self._patch_machine(monkeypatch, tmp_path / "absent.yaml")
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


class TestProjectMeta:
    """Tests for write_project_meta / read_project_meta."""

    def test_write_and_read(self, tmp_path):
        toml_path = tmp_path / "settings.yaml"
        write_project_meta(
            toml_path,
            mode="primary",
            workspace="/home/user/myproject",
            shell="/data/kanibako/settings/abc/shell",
            vault_ro="/home/user/myproject/vault/ro",
            vault_rw="/home/user/myproject/vault/rw",
        )
        assert toml_path.is_file()

        meta = read_project_meta(toml_path)
        assert meta is not None
        assert meta["mode"] == "primary"
        assert meta["workspace"] == "/home/user/myproject"
        assert meta["shell"] == "/data/kanibako/settings/abc/shell"
        assert meta["vault_ro"] == "/home/user/myproject/vault/ro"
        assert meta["vault_rw"] == "/home/user/myproject/vault/rw"

    def test_read_missing_file(self, tmp_path):
        meta = read_project_meta(tmp_path / "nonexistent.yaml")
        assert meta is None

    def test_read_no_project_section(self, tmp_path):
        toml_path = tmp_path / "settings.yaml"
        toml_path.write_text('box:\n  image: "foo"\n')
        meta = read_project_meta(toml_path)
        assert meta is None

    def test_preserves_existing_sections(self, tmp_path):
        toml_path = tmp_path / "settings.yaml"
        toml_path.write_text('box:\n  image: "custom:v1"\n')

        write_project_meta(
            toml_path,
            mode="standalone",
            workspace="/tmp/proj",
            shell="/tmp/proj/.kanibako/shell",
            vault_ro="/tmp/proj/vault/ro",
            vault_rw="/tmp/proj/vault/rw",
        )

        # Container section preserved
        cfg = load_config(toml_path)
        assert cfg.box_image == "custom:v1"

        # Metadata also present
        meta = read_project_meta(toml_path)
        assert meta["mode"] == "standalone"

    def test_overwrite_existing_meta(self, tmp_path):
        toml_path = tmp_path / "settings.yaml"
        write_project_meta(
            toml_path,
            mode="primary",
            workspace="/old",
            shell="/old/shell",
            vault_ro="/old/vault/ro",
            vault_rw="/old/vault/rw",
        )
        write_project_meta(
            toml_path,
            mode="named",
            workspace="/new",
            shell="/new/shell",
            vault_ro="/new/vault/ro",
            vault_rw="/new/vault/rw",
        )

        meta = read_project_meta(toml_path)
        assert meta["mode"] == "named"
        assert meta["workspace"] == "/new"

    def test_new_fields_round_trip(self, tmp_path):
        """New fields (metadata, project_hash) round-trip.

        The global_shared/local_shared resolved keys were removed in 1.6.0
        (Part 4): no ``shared/`` dir exists in the target tree.
        """
        toml_path = tmp_path / "settings.yaml"
        write_project_meta(
            toml_path,
            mode="primary",
            workspace="/home/user/proj",
            shell="/data/boxes/abc/shell",
            vault_ro="/home/user/proj/vault/ro",
            vault_rw="/home/user/proj/vault/rw",
            metadata="/data/boxes/abc",
            project_hash="abc123def456",
        )

        meta = read_project_meta(toml_path)
        assert meta is not None
        assert meta["metadata"] == "/data/boxes/abc"
        assert meta["project_hash"] == "abc123def456"
        assert "global_shared" not in meta
        assert "local_shared" not in meta

    def test_backward_compat_missing_new_fields(self, tmp_path):
        """Old settings.yaml without new fields returns empty strings."""
        toml_path = tmp_path / "settings.yaml"
        # Write old-style config without new fields.
        toml_path.write_text(
            'project:\n  mode: "default"\n  layout: "default"\n'
            '  enable_vault: true\n  group_auth: true\n\n'
            'resolved:\n  workspace: "/old"\n  shell: "/old/shell"\n'
            '  vault_ro: "/old/ro"\n  vault_rw: "/old/rw"\n'
        )

        meta = read_project_meta(toml_path)
        assert meta is not None
        assert meta["metadata"] == ""
        assert meta["project_hash"] == ""
        assert "global_shared" not in meta
        assert "local_shared" not in meta

    def test_mode_token_read_verbatim(self, tmp_path):
        """The on-disk ``box.mode`` token is read verbatim (no back-compat).

        1.6.0 is a hard break (fresh trees only): pre-1.6.0 tokens such as
        ``default``/``workset``/``account_centric`` are NOT translated.
        """
        for raw_mode in ("primary", "named", "standalone", "default", "account_centric"):
            toml_path = tmp_path / f"project-{raw_mode}.yaml"
            toml_path.write_text(
                f'project:\n  mode: "{raw_mode}"\n  layout: "default"\n'
                '  enable_vault: true\n  group_auth: true\n\n'
                'resolved:\n  workspace: "/old"\n  shell: "/old/shell"\n'
                '  vault_ro: "/old/ro"\n  vault_rw: "/old/rw"\n'
            )
            meta = read_project_meta(toml_path)
            assert meta is not None
            assert meta["mode"] == raw_mode, f"{raw_mode} should read verbatim"

    def test_partial_new_fields(self, tmp_path):
        """Only some new fields present — missing ones default to empty string."""
        toml_path = tmp_path / "settings.yaml"
        write_project_meta(
            toml_path,
            mode="named",
            workspace="/ws/proj",
            shell="/ws/proj/shell",
            vault_ro="/ws/vault/proj/ro",
            vault_rw="/ws/vault/proj/rw",
            metadata="/ws/data/proj",
            # project_hash not passed → default ""
        )

        meta = read_project_meta(toml_path)
        assert meta["metadata"] == "/ws/data/proj"
        assert meta["project_hash"] == ""
        assert "global_shared" not in meta
        assert "local_shared" not in meta


class TestConfigFilePath:
    def test_returns_new_path_when_neither_exists(self, tmp_path):
        result = config_file_path(tmp_path)
        assert result == tmp_path / "kanibako.yaml"

    def test_returns_new_path_when_new_exists(self, tmp_path):
        new = tmp_path / "kanibako.yaml"
        new.write_text("paths:\n")
        result = config_file_path(tmp_path)
        assert result == new

    def test_ignores_legacy_old_subdir_location(self, tmp_path):
        # An old-location file is no longer recognized: always resolves
        # to the current top-level location.
        old = tmp_path / "kanibako" / "kanibako.yaml"
        old.parent.mkdir()
        old.write_text("paths:\n")
        result = config_file_path(tmp_path)
        assert result == tmp_path / "kanibako.yaml"


class TestResourceOverrides:
    """Tests for resource scope override storage in settings.yaml."""

    def _write_base_toml(self, path):
        """Write a minimal settings.yaml for testing."""
        write_project_meta(
            path,
            mode="primary",
            workspace="/w", shell="/s", vault_ro="/ro", vault_rw="/rw",
        )

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

        # Project metadata should still be intact.
        meta = read_project_meta(p)
        assert meta is not None
        assert meta["mode"] == "primary"


class TestTargetSettings:
    """Tests for target setting override storage in settings.yaml."""

    def _write_base_toml(self, path):
        """Write a minimal settings.yaml for testing."""
        write_project_meta(
            path,
            mode="primary",
            workspace="/w", shell="/s", vault_ro="/ro", vault_rw="/rw",
        )

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

        # Project metadata should still be intact.
        meta = read_project_meta(p)
        assert meta is not None
        assert meta["mode"] == "primary"


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
