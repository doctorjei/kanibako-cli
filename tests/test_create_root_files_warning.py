"""``create --standalone`` on a non-empty directory warns, naming what stays outside.

The user's root files stay where they are (nothing moves); the warning names each one the
box does not see, and never the layout's own entries.
"""

from __future__ import annotations

import argparse

from kanibako.commands.box._parser import run_create

_WARN = "Warning: the box does not see these entries in "


def _create(root, **over):
    ns = argparse.Namespace(
        path=str(root), standalone=True, no_vault=True,
        name=None, image=None, agent=None, allow_home=False, recover=False,
    )
    for k, v in over.items():
        setattr(ns, k, v)
    return run_create(ns)


def _warning(err: str) -> str:
    lines = [ln for ln in err.splitlines() if ln.startswith(_WARN)]
    assert len(lines) <= 1, err
    return lines[0] if lines else ""


class TestRootFilesWarning:
    def test_names_each_user_entry_and_moves_nothing(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        root = (tmp_home / "proj").resolve()
        (root / "src").mkdir(parents=True)
        (root / "src" / "x.py").write_text("x\n")
        (root / "README.md").write_text("hi\n")
        (root / ".env").write_text("n\n")
        (root / ".gitignore").write_text("*.pyc\n")
        (root / "link").symlink_to(root / "README.md")

        assert _create(root) == 0

        line = _warning(capsys.readouterr().err)
        assert line == (f"{_WARN}{root}, outside its workspace {root / 'workspace'}: "
                        f".env, README.md, link, src")
        # Nothing moved: the files stay at the root, the workspace holds none of them.
        assert (root / "README.md").read_text() == "hi\n"
        assert (root / "src" / "x.py").is_file()
        assert (root / "link").is_symlink()
        assert not any((root / "workspace" / n).exists()
                       for n in (".env", "README.md", "link", "src"))

    def test_empty_directory_does_not_warn(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        root = (tmp_home / "empty").resolve()
        root.mkdir()
        assert _create(root) == 0
        assert _warning(capsys.readouterr().err) == ""

    def test_absent_directory_does_not_warn(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        assert _create((tmp_home / "fresh").resolve()) == 0
        assert _warning(capsys.readouterr().err) == ""

    def test_a_present_workspace_dir_is_not_named(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        root = (tmp_home / "w2").resolve()
        (root / "workspace").mkdir(parents=True)
        (root / "workspace" / "a").write_text("a\n")
        (root / "b").write_text("b\n")
        assert _create(root, register=True) == 0
        line = _warning(capsys.readouterr().err)
        assert line.endswith(f"outside its workspace {root / 'workspace'}: b"), line
