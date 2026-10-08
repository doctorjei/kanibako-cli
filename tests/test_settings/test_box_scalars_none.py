"""``box.*`` scalars that admit ``<None>`` HOLD IT — the declared ``<None>`` is ``None``.

Spec §2b declares ``box.shell | <None> (auto-detect)``, and §2h classifies a
present-``None`` on a scalar leaf as KEPT: *"KEPT ``None`` for a scalar leaf — the
consumer reads None, never the key's default."*  A flat field that spells that
``<None>`` as ``""`` cannot answer either half — the user cannot tell a declared
``<None>`` from a value, and the consumer is handed a string it may run.
"""

from __future__ import annotations

import pytest

from kanibako.launch.shells import resolve_box_shell
from kanibako.settings.config import (
    KanibakoConfig,
    _typed_box_scalar,
    box_scalar_defaults_floor,
    load_config,
    user_config_file,
)
from kanibako.settings.config_interface import show_config
from kanibako.settings.config_keys import ConfigLevel
from kanibako.settings.messages import ERR_BOX_SCALAR_NULL_REASON
from kanibako.settings.paths import load_std_paths
from kanibako.settings.settings_launch import load_merged_config

BOX_META_FILE = "box.yaml"


@pytest.fixture
def std(tmp_home):
    """The standard paths of the isolated home."""
    return load_std_paths(load_config(user_config_file()))


class TestTheDeclaredDefault:
    """What ``KanibakoConfig()`` holds with no file read at all."""

    def test_box_shell_declares_none_not_the_empty_string(self):
        """Spec §2b ``box.shell | <None>`` — the default is ``None``, not ``""``."""
        assert KanibakoConfig().box_shell is None

    def test_the_field_type_admits_none(self):
        """⚑ THE TYPE IS THE RULE: a field that will hold ``None`` says so."""
        assert KanibakoConfig.__annotations__["box_shell"] in ("str | None", "str|None")

    def test_the_floor_still_supplies_a_present_none(self):
        """A declared ``<None>`` is SUPPLIED present, so ``@box.shell`` resolves.

        A whole-value ref to a present ``<None>`` is ``<None>`` (spec §0); the ``""``
        spelling would be dropped as a suppression by the fold and the ref would dangle.
        """
        floor = box_scalar_defaults_floor()
        assert "box.shell" in floor
        assert floor["box.shell"] is None

    def test_the_other_box_scalars_keep_their_real_defaults(self):
        """⚑ THE WIDENING IS SCOPED: only a ``<None>``-admitting scalar changed."""
        cfg = KanibakoConfig()
        assert cfg.box_image == "ghcr.io/doctorjei/kanibako-oci:latest"
        assert cfg.box_share_images is False
        assert cfg.box_enable_vault is True
        floor = box_scalar_defaults_floor()
        assert floor["box.image"] == cfg.box_image
        assert floor["box.share_images"] is False
        assert floor["box.enable_vault"] is True


class TestAPresentNoneSurvivesTheMerge:
    """A ``box.shell: null`` in a settings file is a VALUE the cascade keeps."""

    def test_a_present_null_in_box_yaml_stays_none(self, tmp_path):
        """Spec §2h: the consumer reads ``None``, never the key's default.

        The overlay resets a present ``None`` to the DECLARED DEFAULT, and for
        ``box.shell`` that default IS ``<None>`` — so the two coincide and the ``None``
        reaches the flat field intact.
        """
        box_yaml = tmp_path / BOX_META_FILE
        box_yaml.write_text("box:\n  shell: null\n")
        merged = load_merged_config(box_yaml)
        assert merged.box_shell is None

    def test_a_present_null_suppresses_an_inherited_concrete_value(
        self, tmp_path,
    ):
        """⚑ A present ``None`` OMITS the key — it is not "revert to the default".

        The workset's concrete shell is what a higher-tier ``null`` drops, which is the
        same shape §2h states for a box pref: the request is applied, not undone.
        """
        workset = tmp_path / "workset.yaml"
        workset.write_text("box:\n  shell: /bin/zsh\n")
        box_yaml = tmp_path / BOX_META_FILE
        box_yaml.write_text("box:\n  shell: null\n")
        assert load_merged_config(box_yaml, workset_path=workset).box_shell is None
        # ⚑ The control: with the box tier silent the workset's value is what wins, so
        # the assertion above is about the ``null`` and not about the workset being ignored.
        silent_box = tmp_path / "box-silent.yaml"
        silent_box.write_text("box:\n  image: x\n")
        assert load_merged_config(silent_box, workset_path=workset).box_shell == "/bin/zsh"

    def test_the_empty_string_is_still_a_real_distinct_value(self, tmp_path):
        """``""`` is a VALUE, distinct from ``null`` — this lane does not merge them."""
        box_yaml = tmp_path / BOX_META_FILE
        box_yaml.write_text('box:\n  shell: ""\n')
        assert load_merged_config(box_yaml).box_shell == ""

    def test_a_concrete_shell_is_untouched(self, tmp_path):
        box_yaml = tmp_path / BOX_META_FILE
        box_yaml.write_text("box:\n  shell: /bin/zsh\n")
        assert load_merged_config(box_yaml).box_shell == "/bin/zsh"

    def test_a_typed_box_scalar_keeps_none_only_where_none_is_declared(self):
        """🛑 THE ``"None"`` HAZARD, at the one function that would produce it.

        ``_typed_box_scalar`` lands a value on its field's own type, and ``str(None)``
        is ``"None"`` — a program name.  Only a ``<None>``-admitting field is spared;
        a bool field still lands a bool, so the guard is not a blanket ``None`` passthrough.
        """
        defaults = KanibakoConfig()
        assert _typed_box_scalar(defaults, "box_shell", None) is None
        # ⚑ The controls. ``box.image`` declares no ``<None>`` (spec §2b), and a
        # present ``None`` resolves to the default before it reaches this function, so
        # neither the bool arm nor the ``str`` arm is in question for them.
        assert _typed_box_scalar(defaults, "box_share_images", None) is False
        assert _typed_box_scalar(defaults, "box_enable_vault", None) is False


class TestTheConsumerAutoDetects:
    """``resolve_box_shell`` is the single source of truth for the no-agent shell."""

    def test_a_none_box_shell_auto_detects(self, std):
        """A declared ``<None>`` means AUTO-DETECT (spec §2b), not an empty program."""
        assert resolve_box_shell(KanibakoConfig(), std) == ("sh", "sh")

    def test_a_none_box_shell_reaches_the_resolver_through_the_merge(
        self, tmp_path, std,
    ):
        """THE PRODUCTION CHAIN: stored ``null`` → merged ``None`` → auto-detect."""
        box_yaml = tmp_path / BOX_META_FILE
        box_yaml.write_text("box:\n  shell: null\n")
        cfg = load_merged_config(box_yaml)
        assert cfg.box_shell is None
        assert resolve_box_shell(cfg, std) == ("sh", "sh")

    def test_a_concrete_shell_still_wins_over_the_image(self, std):
        cfg = KanibakoConfig(box_shell="/usr/bin/fish")
        assert resolve_box_shell(cfg, std) == ("/usr/bin/fish", "box.shell")


class TestTheEffectiveViewPrintsTheSpecSpelling:
    """``--effective`` is a line a USER READS, so it spells a present ``None`` as
    ``config_io.render_stored_scalar`` spells it — ``null``."""

    def test_effective_prints_a_present_none_as_null(self, tmp_path, capsys):
        """The ONE renderer's spelling, not ``<None>`` and not Python's ``None``.

        ``<None>`` is a keyspec-notation spelling of a DECLARED default, and the same
        output prints a stored null as ``null`` — so a line reading ``<None>`` beside
        lines reading ``null`` teaches two spellings for one stored value.  ``None``
        is the program name, which is what a launch would have to run.
        """
        global_cfg = tmp_path / "kanibako.cfg"
        global_cfg.write_text("")
        box_yaml = tmp_path / BOX_META_FILE
        box_yaml.write_text("box:\n  shell: null\n")
        show_config(
            command_scope=ConfigLevel.box,
            global_config_path=global_cfg,
            config_path=box_yaml,
            effective=True,
        )
        out = capsys.readouterr().out
        assert "box_shell = null" in out
        assert "<None>" not in out
        assert "None" not in out
        assert "box_shell = \n" not in out

    def test_effective_prints_a_concrete_shell_verbatim(self, tmp_path, capsys):
        global_cfg = tmp_path / "kanibako.cfg"
        global_cfg.write_text("")
        box_yaml = tmp_path / BOX_META_FILE
        box_yaml.write_text("box:\n  shell: /bin/zsh\n")
        show_config(
            command_scope=ConfigLevel.box,
            global_config_path=global_cfg,
            config_path=box_yaml,
            effective=True,
        )
        assert "box_shell = /bin/zsh" in capsys.readouterr().out

    def test_effective_leaves_the_bool_scalars_alone(self, tmp_path, capsys):
        """🛑 THE SCOPE OF THE ROUTING: the ``None`` case only.

        ``render_stored_scalar`` would also lowercase a bool and quote a terminal
        ``""``; this block has always printed ``True`` and ``""`` bare, and nothing
        here decides to change that.
        """
        global_cfg = tmp_path / "kanibako.cfg"
        global_cfg.write_text("")
        box_yaml = tmp_path / BOX_META_FILE
        box_yaml.write_text('box:\n  shell: ""\n')
        show_config(
            command_scope=ConfigLevel.box,
            global_config_path=global_cfg,
            config_path=box_yaml,
            effective=True,
        )
        out = capsys.readouterr().out
        assert "box_enable_vault = True" in out
        assert "box_share_images = False" in out
        assert "box_enable_vault = true" not in out


class TestTheMembershipIsDerivedFromTheDeclaredDefault:
    """``config.refuses_null_box_scalar`` — a box scalar is refused exactly when its own
    declared default is a VALUE (spec §2b, §2h)."""

    @pytest.mark.parametrize(
        "dotted", ["box.image", "box.share_images", "box.enable_vault"],
    )
    def test_a_scalar_whose_default_is_a_value_refuses_a_null(self, dotted):
        from kanibako.settings.config import refuses_null_box_scalar

        assert refuses_null_box_scalar(dotted) is True

    def test_box_shell_is_not_refused_because_its_default_is_none(self):
        """🛑 THE NEIGHBOR THAT MUST KEEP WORKING: ``box.shell``'s declared ``<None>`` is
        a MEANING (auto-detect, spec §2b), not a gap — so the door stays silent and
        ``--null box.shell`` is still a legal write."""
        from kanibako.settings.config import refuses_null_box_scalar

        assert refuses_null_box_scalar("box.shell") is False

    def test_a_key_outside_the_overlay_is_not_a_member(self):
        """The membership is the OVERLAY, not every key: a path key is the path door's."""
        from kanibako.settings.config import refuses_null_box_scalar

        assert refuses_null_box_scalar("box.bindings.ro") is False
        assert refuses_null_box_scalar("workset.boxes") is False


class TestTheLaunchRefusesAndTheSetDoorRefuses:
    """One stored value, one answer: a box scalar the launch refuses a ``null`` at is
    refused at the door that would write it, and the launch names the key and the file."""

    def _box_yaml(self, tmp_path, dotted, spelling="null"):
        leaf = dotted.split(".", 1)[1]
        p = tmp_path / BOX_META_FILE
        p.write_text(f"box:\n  {leaf}: {spelling}\n")
        return p

    @pytest.mark.parametrize(
        "dotted", ["box.image", "box.share_images", "box.enable_vault"],
    )
    def test_the_launch_refuses_and_names_the_key_and_the_file(self, tmp_path, dotted):
        """A refusal the user READS is code: it must name the key, the file and the cure —
        and it must NOT substitute the default the way the read used to."""
        from kanibako.settings.settings_resolve import SettingsError

        p = self._box_yaml(tmp_path, dotted)
        with pytest.raises(SettingsError) as excinfo:
            load_merged_config(p)
        message = str(excinfo.value)
        assert dotted in message
        assert str(p) in message
        assert "default" in message

    def test_a_null_box_shell_still_reaches_the_merge(self, tmp_path):
        """🛑 THE CONTROL: the refusal must not have widened onto ``box.shell``, whose null
        means auto-detect.  It is in the same overlay and the same resolve."""
        p = self._box_yaml(tmp_path, "box.shell")
        assert load_merged_config(p).box_shell is None

    def test_a_file_silent_about_the_key_is_never_refused(self, tmp_path):
        """§2h's distinction: an ABSENT key is a default, and a present ``None`` is a
        value.  Only the second is refused, so an untouched box still launches."""
        p = tmp_path / BOX_META_FILE
        p.write_text("box:\n  image: x\n")
        assert load_merged_config(p).box_image == "x"

    def test_the_set_door_refuses_the_write_the_launch_refuses(self, tmp_path):
        """``6bea7719``'s carrier, one membership: the ``set`` door answers in its OWN
        words (it wrote no line to name) and shares the REASON sentence."""
        from kanibako.settings.config_interface import set_config_value

        box_yaml = tmp_path / BOX_META_FILE
        box_yaml.write_text("box:\n  shell: null\n")
        message = set_config_value(
            "box.image", None, config_path=box_yaml,
            command_scope=ConfigLevel.box,
        )
        assert message.startswith("Error: ")
        assert "box.image" in message
        assert ERR_BOX_SCALAR_NULL_REASON in message
        assert "Nothing was written" in message
        # 🛑 NOTHING WAS WRITTEN — the whole point of refusing at the door.
        assert "box.image" not in box_yaml.read_text()

    def test_the_set_door_still_writes_a_null_box_shell(self, tmp_path):
        """🛑 THE CONTROL for the door too: ``--null box.shell`` is a legal write."""
        from kanibako.settings.config_interface import set_config_value

        box_yaml = tmp_path / BOX_META_FILE
        message = set_config_value(
            "box.shell", None, config_path=box_yaml,
            command_scope=ConfigLevel.box,
        )
        assert not message.startswith("Error: ")
        assert "shell" in box_yaml.read_text()

    def test_a_concrete_value_is_never_refused(self, tmp_path):
        """Only the ``None`` idiom is refused — a value the user chose still writes."""
        from kanibako.settings.config_interface import set_config_value

        box_yaml = tmp_path / BOX_META_FILE
        message = set_config_value(
            "box.image", "myimg:1", config_path=box_yaml,
            command_scope=ConfigLevel.box,
        )
        assert not message.startswith("Error: ")
        assert "myimg:1" in box_yaml.read_text()


class TestAPresentNullIsNotAnAbsentKey:
    """⚑ THE PAIR, WRITTEN AS A PAIR.  Spec §2h draws one line between a key a file HOLDS
    as ``None`` and a key a file says nothing about, and for ``box.*`` the two answer
    differently: the first is a value (kept, or refused where the declared default gives
    it no meaning), the second is a default.  A test that showed only one half would pass
    on code that got the other half wrong, so every box scalar gets all three."""

    #: ``box.*`` dotted key → the flat field it lands on.
    FIELDS = {
        "box.image": "box_image",
        "box.share_images": "box_share_images",
        "box.enable_vault": "box_enable_vault",
        "box.shell": "box_shell",
    }
    #: Each key's declared default (spec §2b) — the ABSENT key's answer.
    DEFAULTS = {
        "box.image": "ghcr.io/doctorjei/kanibako-oci:latest",
        "box.share_images": False,
        "box.enable_vault": True,
        "box.shell": None,
    }
    #: A concrete value of each key's own type, as a file spells it and as it resolves.
    REAL = {
        "box.image": ("myimg:1", "myimg:1"),
        "box.share_images": ("true", True),
        "box.enable_vault": ("false", False),
        "box.shell": ("/bin/zsh", "/bin/zsh"),
    }

    def _box_yaml(self, tmp_path, dotted, spelling):
        p = tmp_path / BOX_META_FILE
        p.write_text(f"box:\n  {dotted.split('.', 1)[1]}: {spelling}\n")
        return p

    @pytest.mark.parametrize("dotted", sorted(DEFAULTS))
    def test_an_absent_key_still_gets_the_default(self, tmp_path, dotted):
        """⚑ THE ABSENT HALF, per key: a file that never mentions it resolves to its
        declared default — no refusal, and no ``None`` either.

        The file names a DIFFERENT scalar, so the key under test is genuinely absent and
        the assertion is about the default rather than about the file being unread.
        """
        other = next(k for k in self.DEFAULTS if k != dotted)
        p = self._box_yaml(tmp_path, other, self.REAL[other][0])
        assert getattr(load_merged_config(p), self.FIELDS[dotted]) == self.DEFAULTS[dotted]

    @pytest.mark.parametrize("dotted", sorted(DEFAULTS))
    def test_a_real_value_still_wins(self, tmp_path, dotted):
        """⚑ THE REAL-VALUE HALF, per key: a value the user chose resolves to itself."""
        spelling, expected = self.REAL[dotted]
        p = self._box_yaml(tmp_path, dotted, spelling)
        assert getattr(load_merged_config(p), self.FIELDS[dotted]) == expected

    @pytest.mark.parametrize("dotted", sorted(DEFAULTS))
    def test_a_present_null_is_never_read_as_the_default(self, tmp_path, dotted):
        """⚑ AND THE POINT OF THE PAIR: no present ``None`` answers with the key's own
        default.  Where §2b declares a ``<None>`` the consumer reads ``None``; where it
        declares a value the launch refuses.  Neither arm substitutes the default."""
        from kanibako.settings.settings_resolve import SettingsError

        p = self._box_yaml(tmp_path, dotted, "null")
        if self.DEFAULTS[dotted] is None:
            assert getattr(load_merged_config(p), self.FIELDS[dotted]) is None
        else:
            with pytest.raises(SettingsError) as excinfo:
                load_merged_config(p)
            assert dotted in str(excinfo.value)


class TestTheDisplaySurvivesAValueTheLaunchRefuses:
    """``show --effective`` must ANSWER with what is stored, so a ``null`` it is about to
    print is not a reason to raise."""

    @pytest.mark.parametrize(
        ("dotted", "field", "default_row"),
        [
            ("box.image", "box_image", "box_image = ghcr.io/doctorjei/kanibako-oci:latest"),
            ("box.share_images", "box_share_images", "box_share_images = False"),
            ("box.enable_vault", "box_enable_vault", "box_enable_vault = True"),
        ],
    )
    def test_effective_prints_null_for_a_refused_scalar(
        self, tmp_path, capsys, dotted, field, default_row,
    ):
        """Each refused key prints ``null`` — the stored value, in the one spelling.

        ⚑ THE ROW, NOT THE OUTPUT: the other three scalars print their own defaults in
        the same block, so the assertion is that THIS key's row is not its default row.
        The row is keyed by the FLAT FIELD the display prints, not the dotted leaf.
        """
        from kanibako.settings.config_interface import show_config

        global_cfg = tmp_path / "kanibako.cfg"
        global_cfg.write_text("")
        box_yaml = tmp_path / BOX_META_FILE
        box_yaml.write_text(f"box:\n  {dotted.split('.', 1)[1]}: null\n")
        rc = show_config(
            command_scope=ConfigLevel.box,
            global_config_path=global_cfg,
            config_path=box_yaml,
            effective=True,
        )
        out = capsys.readouterr().out
        assert rc == 0
        assert f"  {field} = null" in out
        # 🛑 NOT the declared default: that was the defect — a value the file never held.
        assert default_row not in out
        assert "= None" not in out
        assert "<None>" not in out


class TestTheRefusalJudgesTheResolvedValue:
    """The refusal reads the value that WINS the whole cascade (system < agent < workset <
    box, then the CLI), so a null at any tier is judged and an overridden null is not.

    # keyspec §2h: "KEPT ``None`` for a scalar leaf — the consumer reads None, never the
    # key's default" — the consumer is the RESOLVED value, wherever its null came from.
    """

    def _write(self, path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def test_a_system_tier_null_is_refused_naming_the_system_file(self, std):
        from kanibako.settings.settings_resolve import SettingsError

        self._write(std.settings, "box:\n  image: null\n")
        with pytest.raises(SettingsError) as excinfo:
            load_merged_config()
        assert "box.image" in str(excinfo.value)
        assert str(std.settings) in str(excinfo.value)

    def test_an_agent_tier_null_is_refused_naming_the_agent_file(self, std, tmp_path):
        from kanibako.settings.settings_resolve import SettingsError

        agent = self._write(tmp_path / "agent.yaml", "box:\n  share_images: null\n")
        box = self._write(tmp_path / BOX_META_FILE, "{}\n")
        with pytest.raises(SettingsError) as excinfo:
            load_merged_config(box, agent_name="claude", agent_path=agent)
        assert "box.share_images" in str(excinfo.value)
        assert str(agent) in str(excinfo.value)

    def test_a_box_value_overrides_an_agent_tier_null(self, std, tmp_path):
        agent = self._write(tmp_path / "agent.yaml", "box:\n  image: null\n")
        box = self._write(tmp_path / BOX_META_FILE, "box:\n  image: img:box\n")
        cfg = load_merged_config(box, agent_name="claude", agent_path=agent)
        assert cfg.box_image == "img:box"

    def test_a_cli_value_overrides_a_system_tier_null(self, std):
        self._write(std.settings, "box:\n  image: null\n")
        assert load_merged_config(cli_overrides={"box_image": "img:cli"}).box_image == "img:cli"

    def test_a_box_null_over_a_workset_value_is_refused_naming_the_box_file(self, std, tmp_path):
        """The null WINS here, so it is refused, and the file named is the winner's."""
        from kanibako.settings.settings_resolve import SettingsError

        ws = self._write(tmp_path / "ws.yaml", "box:\n  image: img:ws\n")
        box = self._write(tmp_path / BOX_META_FILE, "box:\n  image: null\n")
        with pytest.raises(SettingsError) as excinfo:
            load_merged_config(box, workset_path=ws)
        assert str(box) in str(excinfo.value)
        assert str(ws) not in str(excinfo.value)

    def test_system_show_effective_prints_a_system_tier_null(self, std, capsys):
        self._write(std.settings, "box:\n  image: null\n")
        cfg_path = user_config_file()
        rc = show_config(
            command_scope=ConfigLevel.system,
            global_config_path=cfg_path,
            config_path=cfg_path,
            effective=True,
        )
        out = capsys.readouterr().out
        assert rc == 0
        assert "  box_image = null" in out
        assert "kanibako-oci:latest" not in out
