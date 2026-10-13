"""Keyspec §0 "Per-owner resources" at the read door: a per-owner early key the system file
holds with a value that reaches no owner identity is refused by every reader, naming the file.

The set door's judgment, so the two doors agree; in the braced reference spelling. An old
``@x`` spelling is the guard's to refuse, by name with its braced cure, never this door's
(:class:`TestAnOldSpellingIsTheGuards`).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from kanibako.errors import ConfigError
from kanibako.settings.config import WORKSET_META_FILE, load_config
from kanibako.settings.paths import load_std_paths
from kanibako.settings.settings_resolve import SettingsError
from kanibako.settings.workset_dirkeys import (
    EarlyScope, EarlySystem, early_repoint, refuse_inherited_per_owner,
)

REPO_SRC = Path(__file__).resolve().parents[2] / "src"

_FORMS = ("{",)


def _ref(form: str, name: str) -> str:
    return "{" + name + "}"


def _write_system(std, workset: dict, system: dict | None = None) -> None:
    doc: dict = {"workset": workset}
    if system is not None:
        doc["system"] = system
    std.settings.parent.mkdir(parents=True, exist_ok=True)
    std.settings.write_text(yaml.safe_dump(doc))


def _scope(tier: dict[str, str | None], file: Path) -> EarlyScope:
    return EarlyScope(EarlySystem(tier=tier, file=file, system_paths={}), "kento")


class TestLoadStdPaths:
    def test_a_system_boxes_literal_is_refused_naming_the_file(self, std, config_file):
        _write_system(std, {"boxes": "/srv/kb"})
        with pytest.raises(ConfigError) as excinfo:
            load_std_paths(load_config(config_file))
        text = str(excinfo.value)
        assert text.startswith(f"workset.boxes is set to '/srv/kb' in {std.settings},"), text
        assert "does not reach working-set identity" in text
        # The cure order: the anchored system value, the reset-then-each-workset form, stop --all.
        cure = text.index("kanibako system set 'workset.boxes=/srv/kb/{meta.workset.path}'")
        reset = text.index("kanibako system reset workset.boxes")
        assert cure < reset < text.index("kanibako workset set <workset> 'workset.boxes=")
        assert "kanibako stop --all stops every running box even when a settings file is refused" in text
        assert text.endswith(
            "On a first run (no kanibako.cfg yet), these commands refuse too: edit the system settings file by hand."), text

    @pytest.mark.parametrize("form", _FORMS)
    def test_the_anchored_cure_is_read(self, std, config_file, form):
        _write_system(std, {"boxes": "/srv/kb/" + _ref(form, "meta.workset.path")})
        load_std_paths(load_config(config_file))

    @pytest.mark.parametrize("form", _FORMS)
    def test_a_value_built_on_a_refused_referent_is_refused(self, std, config_file, form):
        _write_system(std, {"boxes": "/srv/kb", "logs": _ref(form, "workset.boxes") + "/lg"})
        with pytest.raises(ConfigError, match=r"workset\.boxes is set to '/srv/kb'"):
            load_std_paths(load_config(config_file))

    @pytest.mark.parametrize("form", _FORMS)
    def test_a_value_built_on_an_anchored_referent_is_read(self, std, config_file, form):
        _write_system(std, {
            "boxes": "/srv/kb/" + _ref(form, "meta.workset.path"),
            "logs": _ref(form, "workset.boxes") + "/lg",
        })
        load_std_paths(load_config(config_file))


class TestEarlyRepoint:
    def test_a_workset_own_value_is_not_refused(self, tmp_path):
        own = {"workset": {"boxes": "/srv/kb"}}
        scope = _scope({"workset.boxes": "/srv/kb"}, tmp_path / "settings.yaml")
        assert early_repoint(tmp_path, own, "boxes", early=scope) == (
            "/srv/kb", tmp_path / WORKSET_META_FILE,
        )

    def test_an_inherited_value_is_refused(self, tmp_path):
        scope = _scope({"workset.boxes": "/srv/kb"}, tmp_path / "settings.yaml")
        with pytest.raises(ConfigError, match=str(tmp_path / "settings.yaml")):
            early_repoint(tmp_path, None, "boxes", early=scope)

    @pytest.mark.parametrize("form", _FORMS)
    def test_a_referent_the_workset_owns_anchors_an_inherited_value(self, tmp_path, form):
        own = {"workset": {"boxes": "/srv/kb"}}
        scope = _scope(
            {"workset.logs": _ref(form, "workset.boxes") + "/lg"}, tmp_path / "settings.yaml",
        )
        assert early_repoint(tmp_path, own, "logs", early=scope)[1] == tmp_path / "settings.yaml"

    def test_a_present_null_and_a_shared_key_are_not_judged(self, tmp_path):
        scope = _scope(
            {"workset.boxes": None, "workset.template": "/srv/tmpl"}, tmp_path / "settings.yaml",
        )
        assert early_repoint(tmp_path, None, "boxes", early=scope)[0] is None
        assert early_repoint(tmp_path, None, "template", early=scope)[0] == "/srv/tmpl"


class TestRefuseInheritedPerOwner:
    @pytest.mark.parametrize("form", _FORMS)
    def test_a_partition_literal_is_refused_and_its_cure_passes(self, tmp_path, form):
        settings = tmp_path / "settings.yaml"
        with pytest.raises(ConfigError) as excinfo:
            refuse_inherited_per_owner(
                tmp_path, _scope({"workset.channels.mailboxes": "/srv/mb"}, settings),
            )
        assert "does not reach partition identity" in str(excinfo.value)
        assert "'workset.channels.mailboxes=/srv/mb/{meta.workset.name}'" in str(excinfo.value)
        refuse_inherited_per_owner(tmp_path, _scope(
            {"workset.channels.mailboxes": "/srv/mb/" + _ref(form, "meta.workset.name")}, settings,
        ))

    def test_the_workset_own_file_shields_its_keys(self, tmp_path):
        (tmp_path / WORKSET_META_FILE).write_text(yaml.safe_dump({"workset": {"boxes": "/srv/kb"}}))
        refuse_inherited_per_owner(
            tmp_path, _scope({"workset.boxes": "/srv/kb"}, tmp_path / "settings.yaml"),
        )

    def test_every_inherited_per_owner_key_is_judged(self, tmp_path):
        (tmp_path / WORKSET_META_FILE).write_text(yaml.safe_dump({"workset": {"boxes": "/srv/kb"}}))
        scope = _scope(
            {"workset.boxes": "/srv/kb", "workset.vault_rw": "/srv/vault"}, tmp_path / "s.yaml",
        )
        with pytest.raises(ConfigError, match=r"workset\.vault_rw is set to '/srv/vault'"):
            refuse_inherited_per_owner(tmp_path, scope)

    def test_a_shared_key_is_not_refused(self, tmp_path):
        refuse_inherited_per_owner(
            tmp_path, _scope({"workset.template": "/srv/tmpl"}, tmp_path / "settings.yaml"),
        )

    def test_exclude_narrows_the_walk_and_leaves_the_default_alone(self, tmp_path):
        """``registry`` is judged by the DEFAULT walk and spared only by ``exclude``.

        Pinned both ways, so the default cannot silently narrow: the standalone
        ``create`` door is the only caller that passes ``exclude``.
        """
        settings = tmp_path / "settings.yaml"
        tier = {"workset.registry": "/srv/reg"}
        with pytest.raises(ConfigError, match=r"workset\.registry is set to '/srv/reg'"):
            refuse_inherited_per_owner(tmp_path, _scope(tier, settings))
        refuse_inherited_per_owner(tmp_path, _scope(tier, settings), exclude={"registry"})
        with pytest.raises(ConfigError, match=r"workset\.registry"):
            refuse_inherited_per_owner(tmp_path, _scope(tier, settings), exclude={"boxes"})


class TestWorksetCreate:
    def _create(self, std, ws_root: Path, capsys) -> tuple[int, str]:
        from kanibako import cli

        with pytest.raises(SystemExit) as made:
            cli.main(["workset", "create", str(ws_root), "--name", "kento"])
        return made.value.code, capsys.readouterr().err

    def test_a_system_registry_literal_is_refused_and_nothing_is_created(self, std, tmp_path, capsys):
        _write_system(std, {"registry": "/srv/reg.yaml"})
        code, err = self._create(std, tmp_path / "ws", capsys)
        assert code == 1
        assert f"workset.registry is set to '/srv/reg.yaml' in {std.settings}," in err
        assert "does not reach working-set identity" in err
        assert not (tmp_path / "ws").exists()
        assert not std.registry.exists() or "kento" not in std.registry.read_text()

    @pytest.mark.parametrize("form", _FORMS)
    def test_an_anchored_registry_still_creates(self, std, tmp_path, capsys, form):
        _write_system(std, {"registry": "/srv/reg/" + _ref(form, "meta.workset.path") + "/r.yaml"})
        code, _ = self._create(std, tmp_path / "ws", capsys)
        assert code == 0
        assert (tmp_path / "ws").is_dir()
        assert "kento" in std.registry.read_text()


class TestAnOldSpellingIsTheGuards:
    """BARE-REF-GUARD: grammar first. Judged for its reach, an old spelling reaches nothing
    and would be refused with a per-owner cure built on the refused spelling; this door
    refuses it with the expansion's words and the guard's braced cure instead."""

    _OLD = "/srv/kb/@meta.workset.path"

    def test_load_names_the_braced_cure_not_a_per_owner_one(self, std, config_file):
        _write_system(std, {"boxes": self._OLD})
        with pytest.raises(SettingsError) as excinfo:
            load_std_paths(load_config(config_file))
        text = str(excinfo.value)
        assert "Old reference spelling '@meta.workset.path'" in text, text
        assert "write '{meta.workset.path}'" in text, text
        assert "does not reach" not in text, text

    @pytest.mark.parametrize(("value", "reason"), [
        (_OLD, "write '{meta.workset.path}'"),
        ("}x", "write a literal '}' as '}}'"),
    ])
    def test_the_per_owner_door_refuses_with_the_expansions_words(self, tmp_path, value, reason):
        settings = tmp_path / "settings.yaml"
        scope = _scope({"workset.boxes": value}, settings)
        for read in (lambda: refuse_inherited_per_owner(tmp_path, scope),
                     lambda: early_repoint(tmp_path, None, "boxes", early=scope)):
            with pytest.raises(SettingsError) as excinfo:
                read()
            text = str(excinfo.value)
            assert text.startswith(
                f"workset.boxes is set to {value!r} in {settings}, which cannot be resolved: "
            ), text
            assert reason in text and "does not reach" not in text, text


@pytest.fixture
def cli(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith(("XDG_", "KANIBAKO"))}
    env.update(HOME=str(home), XDG_RUNTIME_DIR=str(tmp_path), PYTHONPATH=str(REPO_SRC))

    def run(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "kanibako", *args], env=env, cwd=home,
            capture_output=True, text=True, timeout=300, check=False,
        )

    run.home = home  # type: ignore[attr-defined]
    assert run("system", "get", "system.agent").returncode == 0  # bootstrap the store
    return run


@pytest.mark.parametrize("stored", [
    {"channels": {"common": "}x"}},
    {"channels": {"common": "/srv/c/@meta.workset.name"}},
    {"channelroot": "}x"},
    {"channelroot": "/srv/cr/@meta.workset.path"},
], ids=["common-malformed", "common-old", "channelroot-malformed", "channelroot-old"])
def test_workset_create_refuses_an_inherited_value_the_grammar_refuses(cli, stored):
    """``workset create`` never expands these keys; the read door is their only judge."""
    settings = cli.home / ".local/share/kanibako/global/settings.yaml"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(yaml.safe_dump({"workset": stored}))
    proc = cli("workset", "create", "ws1")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "which cannot be resolved" in proc.stderr, proc.stderr
    assert not (cli.home / "ws1").exists()
    listed = cli("workset", "list")
    assert "ws1" not in listed.stdout + listed.stderr
