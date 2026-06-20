"""Tests for kanibako.shells (image-shell store, getent probe, resolver)."""

from __future__ import annotations

import subprocess

import pytest

from kanibako.config import KanibakoConfig
from kanibako.shells import (
    capture_image_shell,
    image_store_key,
    load_image_shells,
    probe_image_user_shell,
    resolve_box_shell,
    save_image_shell,
)


class _Std:
    """Minimal stand-in for StandardPaths: only ``data_path`` is used."""

    def __init__(self, data_path):
        self.data_path = data_path


class _Runtime:
    """Fake runtime: configurable digest + recorded run() invocations."""

    def __init__(self, *, cmd="podman", digest=None):
        self.cmd = cmd
        self._digest = digest

    def get_local_digest(self, image):
        return self._digest


@pytest.fixture
def std(tmp_path):
    # data_path need not pre-exist; save_image_shell creates it.
    return _Std(tmp_path / "data")


# ---------------------------------------------------------------------------
# store round-trip
# ---------------------------------------------------------------------------


class TestImageShellStore:
    def test_round_trip_two_entries(self, std):
        save_image_shell(std, "sha256:aaa", "/bin/bash")
        save_image_shell(std, "sha256:bbb", "/bin/zsh")
        loaded = load_image_shells(std)
        assert loaded == {"sha256:aaa": "/bin/bash", "sha256:bbb": "/bin/zsh"}

    def test_load_missing_file(self, std):
        assert load_image_shells(std) == {}

    def test_load_malformed_file(self, std):
        # The store is the image_shells section of the consolidated registry.yaml.
        store = std.data_path / "global" / "registry.yaml"
        store.parent.mkdir(parents=True, exist_ok=True)
        store.write_text("this: is: not: valid: yaml\n")
        # load_image_shells is defensive; malformed YAML → {}.
        assert load_image_shells(std) == {}

    def test_save_preserves_existing(self, std):
        save_image_shell(std, "sha256:aaa", "/bin/bash")
        save_image_shell(std, "sha256:aaa", "/bin/sh")  # upsert
        save_image_shell(std, "sha256:bbb", "/bin/zsh")
        assert load_image_shells(std) == {
            "sha256:aaa": "/bin/sh",
            "sha256:bbb": "/bin/zsh",
        }


# ---------------------------------------------------------------------------
# image_store_key
# ---------------------------------------------------------------------------


class TestImageStoreKey:
    def test_uses_digest_when_available(self):
        rt = _Runtime(digest="sha256:deadbeef")
        assert image_store_key(rt, "ghcr.io/x/img:latest") == "sha256:deadbeef"

    def test_falls_back_to_reference(self):
        rt = _Runtime(digest=None)
        assert image_store_key(rt, "ghcr.io/x/img:latest") == "ghcr.io/x/img:latest"


# ---------------------------------------------------------------------------
# probe_image_user_shell
# ---------------------------------------------------------------------------


class TestProbe:
    def _fake_run(self, stdout="", returncode=0, raises=None, capture=None):
        def _run(cmd, capture_output=False, text=False):
            if capture is not None:
                capture.append(cmd)
            if raises is not None:
                raise raises
            return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr="")
        return _run

    def test_success(self, monkeypatch):
        captured: list = []
        monkeypatch.setattr(
            subprocess, "run", self._fake_run(stdout="/bin/bash\n", capture=captured)
        )
        rt = _Runtime(cmd="podman")
        assert probe_image_user_shell(rt, "img:latest") == "/bin/bash"
        # The --entrypoint sh bypass is essential.
        assert "--entrypoint" in captured[0]
        assert captured[0][captured[0].index("--entrypoint") + 1] == "sh"

    def test_nonzero_empty_stdout(self, monkeypatch):
        monkeypatch.setattr(subprocess, "run", self._fake_run(stdout="", returncode=1))
        assert probe_image_user_shell(_Runtime(), "img") is None

    def test_exception(self, monkeypatch):
        monkeypatch.setattr(
            subprocess, "run", self._fake_run(raises=OSError("boom"))
        )
        assert probe_image_user_shell(_Runtime(), "img") is None

    def test_whitespace_stdout(self, monkeypatch):
        monkeypatch.setattr(subprocess, "run", self._fake_run(stdout="  \n  "))
        assert probe_image_user_shell(_Runtime(), "img") is None


# ---------------------------------------------------------------------------
# capture_image_shell (install-time probe + persist)
# ---------------------------------------------------------------------------


class TestCaptureImageShell:
    def test_probes_once_and_persists(self, std, monkeypatch):
        rt = _Runtime(digest="sha256:aaa")
        calls: list = []

        def _run(cmd, capture_output=False, text=False):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout="/bin/bash\n", stderr="")

        monkeypatch.setattr(subprocess, "run", _run)
        capture_image_shell(rt, "img:latest", std)
        assert load_image_shells(std) == {"sha256:aaa": "/bin/bash"}
        assert len(calls) == 1

    def test_skips_when_already_stored(self, std, monkeypatch):
        rt = _Runtime(digest="sha256:aaa")
        save_image_shell(std, "sha256:aaa", "/bin/zsh")

        def _boom(*a, **k):
            raise AssertionError("must not probe when key already stored")

        monkeypatch.setattr(subprocess, "run", _boom)
        capture_image_shell(rt, "img:latest", std)
        # Existing value untouched.
        assert load_image_shells(std) == {"sha256:aaa": "/bin/zsh"}

    def test_probe_none_stores_nothing(self, std, monkeypatch):
        rt = _Runtime(digest="sha256:aaa")

        def _run(cmd, capture_output=False, text=False):
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", _run)
        capture_image_shell(rt, "img:latest", std)  # no exception
        assert load_image_shells(std) == {}

    def test_probe_exception_is_swallowed(self, std, monkeypatch):
        rt = _Runtime(digest="sha256:aaa")

        def _run(*a, **k):
            raise OSError("boom")

        monkeypatch.setattr(subprocess, "run", _run)
        capture_image_shell(rt, "img:latest", std)  # no exception
        assert load_image_shells(std) == {}

    def test_digest_failure_is_swallowed(self, std, monkeypatch):
        class _BadRuntime:
            cmd = "podman"

            def get_local_digest(self, image):
                raise RuntimeError("inspect failed")

        # Must not raise even though image_store_key blows up.
        capture_image_shell(_BadRuntime(), "img:latest", std)
        assert load_image_shells(std) == {}

    def test_falls_back_to_reference_key(self, std, monkeypatch):
        rt = _Runtime(digest=None)  # no digest → reference used as key

        def _run(cmd, capture_output=False, text=False):
            return subprocess.CompletedProcess(cmd, 0, stdout="/bin/sh\n", stderr="")

        monkeypatch.setattr(subprocess, "run", _run)
        capture_image_shell(rt, "img:latest", std)
        assert load_image_shells(std) == {"img:latest": "/bin/sh"}


# ---------------------------------------------------------------------------
# resolve_box_shell precedence
# ---------------------------------------------------------------------------


class TestResolveBoxShell:
    def test_config_box_shell_wins(self, std, monkeypatch):
        monkeypatch.setenv("KANIBAKO_SHELL", "/bin/zsh")
        cfg = KanibakoConfig(box_shell="/usr/bin/fish")
        assert resolve_box_shell(cfg, std, image="img", runtime=_Runtime()) == (
            "/usr/bin/fish",
            "box.shell",
        )

    def test_env_var_when_no_config(self, std, monkeypatch):
        monkeypatch.setenv("KANIBAKO_SHELL", "/bin/zsh")
        cfg = KanibakoConfig(box_shell="")
        assert resolve_box_shell(cfg, std) == ("/bin/zsh", "$KANIBAKO_SHELL")

    def test_stored_image_shell_no_probe(self, std, monkeypatch):
        monkeypatch.delenv("KANIBAKO_SHELL", raising=False)
        cfg = KanibakoConfig(box_shell="")
        rt = _Runtime(digest="sha256:aaa")
        save_image_shell(std, "sha256:aaa", "/bin/bash")

        # Ensure no probe happens: any subprocess.run call would error the test.
        def _boom(*a, **k):
            raise AssertionError("probe should not run when store has a hit")

        monkeypatch.setattr(subprocess, "run", _boom)
        assert resolve_box_shell(cfg, std, image="img", runtime=rt) == (
            "/bin/bash",
            "image",
        )

    def test_lazy_probe_and_persist(self, std, monkeypatch):
        monkeypatch.delenv("KANIBAKO_SHELL", raising=False)
        cfg = KanibakoConfig(box_shell="")
        rt = _Runtime(digest="sha256:aaa")

        def _run(cmd, capture_output=False, text=False):
            return subprocess.CompletedProcess(cmd, 0, stdout="/bin/dash\n", stderr="")

        monkeypatch.setattr(subprocess, "run", _run)
        result = resolve_box_shell(cfg, std, image="img", runtime=rt)
        assert result == ("/bin/dash", "image")
        # Persisted for next time.
        assert load_image_shells(std) == {"sha256:aaa": "/bin/dash"}

    def test_no_runtime_falls_to_sh(self, std, monkeypatch):
        monkeypatch.delenv("KANIBAKO_SHELL", raising=False)
        cfg = KanibakoConfig(box_shell="")
        assert resolve_box_shell(cfg, std, image="img", runtime=None) == ("sh", "sh")

    def test_probe_none_falls_to_sh(self, std, monkeypatch):
        monkeypatch.delenv("KANIBAKO_SHELL", raising=False)
        cfg = KanibakoConfig(box_shell="")
        rt = _Runtime(digest="sha256:aaa")

        def _run(cmd, capture_output=False, text=False):
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", _run)
        assert resolve_box_shell(cfg, std, image="img", runtime=rt) == ("sh", "sh")
