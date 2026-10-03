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
    """``--effective`` is a line a USER READS, so it spells ``<None>``."""

    def test_effective_prints_angle_bracket_none(self, tmp_path, capsys):
        """Not ``""`` (indistinguishable from a value) and not Python's ``None``."""
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
        assert "box_shell = <None>" in out
        assert "box_shell = None" not in out
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
