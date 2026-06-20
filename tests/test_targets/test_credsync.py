"""Tests for the agent-agnostic credential-sync engine (credsync)."""

from __future__ import annotations

import os
import stat
from pathlib import Path

from kanibako.targets.base import (
    AgentInstall,
    Cadence,
    CredFileSpec,
    Mount,
    PluginDescriptor,
    Target,
)
from kanibako.targets.credsync import (
    refresh_cred_files,
    seed_cred_files,
    writeback_cred_files,
)


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _StubTarget(Target):
    """Minimal concrete Target: abstracts are no-ops; default transform_cred."""

    @property
    def name(self) -> str:
        return "stub"

    @property
    def display_name(self) -> str:
        return "Stub"

    def detect(self) -> AgentInstall | None:
        return None

    def binary_mounts(self, install: AgentInstall) -> list[Mount]:
        return []

    def refresh_credentials(self, home: Path) -> None:
        return None

    def writeback_credentials(self, home: Path) -> None:
        return None

    def build_cli_args(
        self,
        *,
        safe_mode: bool,
        resume_mode: bool,
        new_session: bool,
        is_new_project: bool,
        extra_args: list[str],
    ) -> list[str]:
        return []


class _MarkerTarget(_StubTarget):
    """transform_cred writes a marker recording every call (proves the hook ran)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, bool]] = []  # (home_rel, direction, src_is_none)

    def transform_cred(
        self,
        spec: CredFileSpec,
        src: Path | None,
        dst: Path,
        direction: str,
    ) -> None:
        self.calls.append((spec.home_rel, direction, src is None))
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src is None:
            dst.write_text("MARKER:none")
        else:
            dst.write_text(f"MARKER:{direction}:{Path(src).read_text()}")


class _DirectionTarget(_StubTarget):
    """transform_cred writes different content per direction (proves direction passed)."""

    def transform_cred(
        self,
        spec: CredFileSpec,
        src: Path | None,
        dst: Path,
        direction: str,
    ) -> None:
        dst.parent.mkdir(parents=True, exist_ok=True)
        if direction == "in":
            dst.write_text("FILTERED-IN")
        else:
            dst.write_text("FILTERED-OUT")


# ---------------------------------------------------------------------------
# Synthetic descriptors mirroring claude + goose
# ---------------------------------------------------------------------------

CLAUDE_DESC = PluginDescriptor(
    command=("claude",),
    bindings=(),
    mode={},
    init_dirs=(".claude",),
    cred_files=(
        CredFileSpec(
            home_rel=".claude/.credentials.json",
            host_rel=".claude/.credentials.json",
            cadence=Cadence.SYNC,
            mtime_gate=True,
            filtered=True,
        ),
        CredFileSpec(
            home_rel=".claude.json",
            host_rel=".claude.json",
            cadence=Cadence.SEED_ONCE,
            mtime_gate=False,
            filtered=True,
        ),
    ),
)

GOOSE_DESC = PluginDescriptor(
    command=("goose",),
    bindings=(),
    mode={},
    init_dirs=(".config/goose",),
    cred_files=(
        CredFileSpec(
            home_rel=".config/goose/secrets.yaml",
            host_rel=".config/goose/secrets.yaml",
            cadence=Cadence.SYNC,
            mtime_gate=True,
            filtered=False,
        ),
        CredFileSpec(
            home_rel=".config/goose/config.yaml",
            host_rel=".config/goose/config.yaml",
            cadence=Cadence.SEED_ONCE,
            mtime_gate=False,
            filtered=True,
        ),
    ),
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write(path: Path, text: str, mtime: float | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


# ---------------------------------------------------------------------------
# seed_cred_files
# ---------------------------------------------------------------------------


class TestSeed:
    def test_init_dirs_created(self, tmp_path: Path) -> None:
        host, proj = tmp_path / "host", tmp_path / "proj"
        seed_cred_files(
            GOOSE_DESC, _StubTarget(),
            host_home=host, project_home=proj, group_auth=True,
        )
        assert (proj / ".config/goose").is_dir()

    def test_unfiltered_copied_and_chmod_when_host_present(self, tmp_path: Path) -> None:
        host, proj = tmp_path / "host", tmp_path / "proj"
        _write(host / ".config/goose/secrets.yaml", "secret: s")
        seed_cred_files(
            GOOSE_DESC, _StubTarget(),
            host_home=host, project_home=proj, group_auth=True,
        )
        dst = proj / ".config/goose/secrets.yaml"
        assert dst.read_text() == "secret: s"
        assert _mode(dst) == 0o600

    def test_unfiltered_not_copied_when_not_group_auth(self, tmp_path: Path) -> None:
        host, proj = tmp_path / "host", tmp_path / "proj"
        _write(host / ".config/goose/secrets.yaml", "secret: s")
        seed_cred_files(
            GOOSE_DESC, _StubTarget(),
            host_home=host, project_home=proj, group_auth=False,
        )
        assert not (proj / ".config/goose/secrets.yaml").exists()

    def test_unfiltered_skipped_when_host_absent(self, tmp_path: Path) -> None:
        host, proj = tmp_path / "host", tmp_path / "proj"
        seed_cred_files(
            GOOSE_DESC, _StubTarget(),
            host_home=host, project_home=proj, group_auth=True,
        )
        assert not (proj / ".config/goose/secrets.yaml").exists()

    def test_filtered_routes_through_transform(self, tmp_path: Path) -> None:
        host, proj = tmp_path / "host", tmp_path / "proj"
        _write(host / ".claude/.credentials.json", "CREDS")
        _write(host / ".claude.json", "SETTINGS")
        t = _MarkerTarget()
        seed_cred_files(
            CLAUDE_DESC, t,
            host_home=host, project_home=proj, group_auth=True,
        )
        # Both filtered specs seeded via transform("in") with a present src.
        assert (".claude/.credentials.json", "in", False) in t.calls
        assert (".claude.json", "in", False) in t.calls
        assert (proj / ".claude/.credentials.json").read_text() == "MARKER:in:CREDS"
        assert (proj / ".claude.json").read_text() == "MARKER:in:SETTINGS"

    def test_filtered_src_none_when_host_absent(self, tmp_path: Path) -> None:
        host, proj = tmp_path / "host", tmp_path / "proj"
        t = _MarkerTarget()
        seed_cred_files(
            CLAUDE_DESC, t,
            host_home=host, project_home=proj, group_auth=True,
        )
        # Host files absent -> transform called with src=None (claude touches {}).
        assert (".claude.json", "in", True) in t.calls
        assert (proj / ".claude.json").read_text() == "MARKER:none"

    def test_filtered_src_none_when_not_group_auth(self, tmp_path: Path) -> None:
        host, proj = tmp_path / "host", tmp_path / "proj"
        _write(host / ".claude.json", "SETTINGS")
        t = _MarkerTarget()
        seed_cred_files(
            CLAUDE_DESC, t,
            host_home=host, project_home=proj, group_auth=False,
        )
        # Distinct auth: src forced to None even though host file exists.
        assert (".claude.json", "in", True) in t.calls

    def test_filtered_chmod_600_after_transform(self, tmp_path: Path) -> None:
        host, proj = tmp_path / "host", tmp_path / "proj"
        _write(host / ".claude/.credentials.json", "CREDS")
        _write(host / ".claude.json", "SETTINGS")
        # _MarkerTarget writes dst via write_text (umask ~0644) -> engine must chmod.
        seed_cred_files(
            CLAUDE_DESC, _MarkerTarget(),
            host_home=host, project_home=proj, group_auth=True,
        )
        assert _mode(proj / ".claude/.credentials.json") == 0o600
        assert _mode(proj / ".claude.json") == 0o600

    def test_filtered_default_transform_copies(self, tmp_path: Path) -> None:
        host, proj = tmp_path / "host", tmp_path / "proj"
        _write(host / ".claude.json", "RAW")
        # _StubTarget uses the base default transform_cred -> wholesale copy.
        seed_cred_files(
            CLAUDE_DESC, _StubTarget(),
            host_home=host, project_home=proj, group_auth=True,
        )
        assert (proj / ".claude.json").read_text() == "RAW"


# ---------------------------------------------------------------------------
# refresh_cred_files
# ---------------------------------------------------------------------------


class TestRefresh:
    def test_sync_refreshed_host_to_project(self, tmp_path: Path) -> None:
        host, proj = tmp_path / "host", tmp_path / "proj"
        _write(proj / ".config/goose/secrets.yaml", "old", mtime=100)
        _write(host / ".config/goose/secrets.yaml", "new", mtime=200)
        refresh_cred_files(
            GOOSE_DESC, _StubTarget(),
            host_home=host, project_home=proj, group_auth=True,
        )
        dst = proj / ".config/goose/secrets.yaml"
        assert dst.read_text() == "new"
        assert _mode(dst) == 0o600

    def test_seed_once_not_refreshed(self, tmp_path: Path) -> None:
        host, proj = tmp_path / "host", tmp_path / "proj"
        _write(proj / ".config/goose/config.yaml", "old", mtime=100)
        _write(host / ".config/goose/config.yaml", "new", mtime=200)
        refresh_cred_files(
            GOOSE_DESC, _DirectionTarget(),
            host_home=host, project_home=proj, group_auth=True,
        )
        # SEED_ONCE config.yaml untouched on refresh.
        assert (proj / ".config/goose/config.yaml").read_text() == "old"

    def test_mtime_gate_skips_when_host_not_newer(self, tmp_path: Path) -> None:
        host, proj = tmp_path / "host", tmp_path / "proj"
        _write(proj / ".config/goose/secrets.yaml", "proj", mtime=200)
        _write(host / ".config/goose/secrets.yaml", "host", mtime=100)
        refresh_cred_files(
            GOOSE_DESC, _StubTarget(),
            host_home=host, project_home=proj, group_auth=True,
        )
        assert (proj / ".config/goose/secrets.yaml").read_text() == "proj"

    def test_not_group_auth_noop(self, tmp_path: Path) -> None:
        host, proj = tmp_path / "host", tmp_path / "proj"
        _write(proj / ".config/goose/secrets.yaml", "old", mtime=100)
        _write(host / ".config/goose/secrets.yaml", "new", mtime=200)
        refresh_cred_files(
            GOOSE_DESC, _StubTarget(),
            host_home=host, project_home=proj, group_auth=False,
        )
        assert (proj / ".config/goose/secrets.yaml").read_text() == "old"

    def test_filtered_routes_through_transform_in(self, tmp_path: Path) -> None:
        host, proj = tmp_path / "host", tmp_path / "proj"
        _write(proj / ".claude/.credentials.json", "old", mtime=100)
        _write(host / ".claude/.credentials.json", "new", mtime=200)
        t = _DirectionTarget()
        refresh_cred_files(
            CLAUDE_DESC, t,
            host_home=host, project_home=proj, group_auth=True,
        )
        assert (proj / ".claude/.credentials.json").read_text() == "FILTERED-IN"

    def test_filtered_chmod_600_after_transform(self, tmp_path: Path) -> None:
        host, proj = tmp_path / "host", tmp_path / "proj"
        _write(proj / ".claude/.credentials.json", "old", mtime=100)
        _write(host / ".claude/.credentials.json", "new", mtime=200)
        # _DirectionTarget writes dst via write_text (umask ~0644) -> engine must chmod.
        refresh_cred_files(
            CLAUDE_DESC, _DirectionTarget(),
            host_home=host, project_home=proj, group_auth=True,
        )
        assert _mode(proj / ".claude/.credentials.json") == 0o600

    def test_missing_host_skipped(self, tmp_path: Path) -> None:
        host, proj = tmp_path / "host", tmp_path / "proj"
        _write(proj / ".config/goose/secrets.yaml", "keep", mtime=100)
        # host secrets absent -> no crash, project untouched.
        refresh_cred_files(
            GOOSE_DESC, _StubTarget(),
            host_home=host, project_home=proj, group_auth=True,
        )
        assert (proj / ".config/goose/secrets.yaml").read_text() == "keep"


# ---------------------------------------------------------------------------
# writeback_cred_files
# ---------------------------------------------------------------------------


class TestWriteback:
    def test_sync_written_back_when_project_newer(self, tmp_path: Path) -> None:
        host, proj = tmp_path / "host", tmp_path / "proj"
        _write(host / ".config/goose/secrets.yaml", "old", mtime=100)
        _write(proj / ".config/goose/secrets.yaml", "new", mtime=200)
        writeback_cred_files(
            GOOSE_DESC, _StubTarget(),
            host_home=host, project_home=proj, group_auth=True,
        )
        assert (host / ".config/goose/secrets.yaml").read_text() == "new"

    def test_seed_once_never_written_back(self, tmp_path: Path) -> None:
        host, proj = tmp_path / "host", tmp_path / "proj"
        _write(host / ".config/goose/config.yaml", "old", mtime=100)
        _write(proj / ".config/goose/config.yaml", "new", mtime=200)
        writeback_cred_files(
            GOOSE_DESC, _DirectionTarget(),
            host_home=host, project_home=proj, group_auth=True,
        )
        assert (host / ".config/goose/config.yaml").read_text() == "old"

    def test_mtime_gate_skips_when_project_not_newer(self, tmp_path: Path) -> None:
        host, proj = tmp_path / "host", tmp_path / "proj"
        _write(host / ".config/goose/secrets.yaml", "host", mtime=200)
        _write(proj / ".config/goose/secrets.yaml", "proj", mtime=100)
        writeback_cred_files(
            GOOSE_DESC, _StubTarget(),
            host_home=host, project_home=proj, group_auth=True,
        )
        assert (host / ".config/goose/secrets.yaml").read_text() == "host"

    def test_not_group_auth_noop(self, tmp_path: Path) -> None:
        host, proj = tmp_path / "host", tmp_path / "proj"
        _write(host / ".config/goose/secrets.yaml", "host", mtime=100)
        _write(proj / ".config/goose/secrets.yaml", "proj", mtime=200)
        writeback_cred_files(
            GOOSE_DESC, _StubTarget(),
            host_home=host, project_home=proj, group_auth=False,
        )
        assert (host / ".config/goose/secrets.yaml").read_text() == "host"

    def test_filtered_routes_through_transform_out(self, tmp_path: Path) -> None:
        host, proj = tmp_path / "host", tmp_path / "proj"
        _write(host / ".claude/.credentials.json", "old", mtime=100)
        _write(proj / ".claude/.credentials.json", "new", mtime=200)
        t = _DirectionTarget()
        writeback_cred_files(
            CLAUDE_DESC, t,
            host_home=host, project_home=proj, group_auth=True,
        )
        assert (host / ".claude/.credentials.json").read_text() == "FILTERED-OUT"

    def test_missing_project_skipped(self, tmp_path: Path) -> None:
        host, proj = tmp_path / "host", tmp_path / "proj"
        _write(host / ".config/goose/secrets.yaml", "keep", mtime=100)
        writeback_cred_files(
            GOOSE_DESC, _StubTarget(),
            host_home=host, project_home=proj, group_auth=True,
        )
        assert (host / ".config/goose/secrets.yaml").read_text() == "keep"


# ---------------------------------------------------------------------------
# Direction-awareness + base transform_cred default
# ---------------------------------------------------------------------------


class TestDirectionAware:
    def test_in_and_out_produce_distinct_content(self, tmp_path: Path) -> None:
        host, proj = tmp_path / "host", tmp_path / "proj"
        # Refresh ("in") writes FILTERED-IN into the project.
        _write(proj / ".claude/.credentials.json", "old", mtime=100)
        _write(host / ".claude/.credentials.json", "new", mtime=200)
        refresh_cred_files(
            CLAUDE_DESC, _DirectionTarget(),
            host_home=host, project_home=proj, group_auth=True,
        )
        assert (proj / ".claude/.credentials.json").read_text() == "FILTERED-IN"

        # Writeback ("out") writes FILTERED-OUT into the host (project newer).
        os.utime(proj / ".claude/.credentials.json", (300, 300))
        writeback_cred_files(
            CLAUDE_DESC, _DirectionTarget(),
            host_home=host, project_home=proj, group_auth=True,
        )
        assert (host / ".claude/.credentials.json").read_text() == "FILTERED-OUT"


class TestBaseTransformDefault:
    def test_plain_copy_when_src_exists(self, tmp_path: Path) -> None:
        src = _write(tmp_path / "src.json", "DATA")
        dst = tmp_path / "sub" / "dst.json"
        spec = CredFileSpec(home_rel="x", host_rel="x", filtered=True)
        _StubTarget().transform_cred(spec, src, dst, "in")
        assert dst.read_text() == "DATA"

    def test_noop_when_src_none(self, tmp_path: Path) -> None:
        dst = tmp_path / "dst.json"
        spec = CredFileSpec(home_rel="x", host_rel="x", filtered=True)
        _StubTarget().transform_cred(spec, None, dst, "in")
        assert not dst.exists()

    def test_noop_when_src_missing(self, tmp_path: Path) -> None:
        dst = tmp_path / "dst.json"
        spec = CredFileSpec(home_rel="x", host_rel="x", filtered=True)
        _StubTarget().transform_cred(spec, tmp_path / "nope.json", dst, "in")
        assert not dst.exists()
