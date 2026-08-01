"""Tests for the agent-agnostic credential-sync engine (credsync).

The primitives (:func:`seed_cred_files` / :func:`refresh_cred_files` /
:func:`writeback_cred_files`) take an explicit ``source_root`` (the SELECTED tier
source — host home for GLOBAL, the workset dir for WORKSET, ``None`` for the
private/BOX tier). The tier ORCHESTRATORS (:func:`seed_box_credentials` /
:func:`refresh_box_credentials` / :func:`writeback_box_credentials`) dispatch the
primitives per a resolved :class:`~kanibako.settings.settings_launch.AuthSource`, including
the workset↔global ``global_sync`` hop.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

from kanibako.settings.settings_launch import AuthSource
from kanibako.targets.base import (
    AgentInstall,
    Cadence,
    CredFileSpec,
    Mount,
    PluginDescriptor,
    Target,
)
from kanibako.targets.credsync import (
    refresh_box_credentials,
    refresh_cred_files,
    seed_box_credentials,
    seed_cred_files,
    writeback_box_credentials,
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
    auth_share_support=True,
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

# codex descriptor: its login cred is ``.codex/auth.json`` (SYNC cadence), the file
# a NaviGator codex persona must NOT receive (the ChatGPT auth would leak to a
# third-party endpoint box).  Mirrors packages/agent-codex codex-defaults.yaml.
CODEX_DESC = PluginDescriptor(
    command=("codex",),
    bindings=(),
    mode={},
    init_dirs=(".codex",),
    auth_share_support=True,
    cred_files=(
        CredFileSpec(
            home_rel=".codex/auth.json",
            host_rel=".codex/auth.json",
            cadence=Cadence.SYNC,
            mtime_gate=True,
            filtered=False,
        ),
    ),
)

GOOSE_DESC = PluginDescriptor(
    command=("goose",),
    bindings=(),
    mode={},
    init_dirs=(".config/goose",),
    auth_share_support=True,
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


# A descriptor exercising the is_dir spec path (mirrors the real goose
# custom_providers/ DIRECTORY sync added in the 2026-06-24 config-persistence fix).
DIR_DESC = PluginDescriptor(
    command=("goose",),
    bindings=(),
    mode={},
    init_dirs=(".config/goose",),
    auth_share_support=True,
    cred_files=(
        CredFileSpec(
            home_rel=".config/goose/custom_providers",
            host_rel=".config/goose/custom_providers",
            cadence=Cadence.SYNC,
            mtime_gate=True,
            filtered=False,
            is_dir=True,
        ),
    ),
)


# ---------------------------------------------------------------------------
# AuthSource fixtures for the tier orchestrators
# ---------------------------------------------------------------------------


def _global_src() -> AuthSource:
    return AuthSource(
        tier="global", global_enabled=True, workset_enabled=False,
        global_sync=False, workset_source=None,
    )


def _workset_src(workset_source: str, *, global_sync: bool = False) -> AuthSource:
    return AuthSource(
        tier="workset", global_enabled=True, workset_enabled=True,
        global_sync=global_sync, workset_source=workset_source,
    )


def _box_src() -> AuthSource:
    return AuthSource(
        tier="box", global_enabled=False, workset_enabled=False,
        global_sync=False, workset_source=None,
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
# seed_cred_files (primitive; source_root)
# ---------------------------------------------------------------------------


class TestSeed:
    def test_init_dirs_created(self, tmp_path: Path) -> None:
        host, proj = tmp_path / "host", tmp_path / "proj"
        seed_cred_files(GOOSE_DESC, _StubTarget(), source_root=host, project_home=proj)
        assert (proj / ".config/goose").is_dir()

    def test_unfiltered_copied_and_chmod_when_source_present(self, tmp_path: Path) -> None:
        host, proj = tmp_path / "host", tmp_path / "proj"
        _write(host / ".config/goose/secrets.yaml", "secret: s")
        seed_cred_files(GOOSE_DESC, _StubTarget(), source_root=host, project_home=proj)
        dst = proj / ".config/goose/secrets.yaml"
        assert dst.read_text() == "secret: s"
        assert _mode(dst) == 0o600

    def test_unfiltered_not_copied_when_private(self, tmp_path: Path) -> None:
        host, proj = tmp_path / "host", tmp_path / "proj"
        _write(host / ".config/goose/secrets.yaml", "secret: s")
        # source_root=None (private/box tier) -> no cred content seeded.
        seed_cred_files(GOOSE_DESC, _StubTarget(), source_root=None, project_home=proj)
        assert not (proj / ".config/goose/secrets.yaml").exists()
        # init_dirs are STILL created even for the private tier.
        assert (proj / ".config/goose").is_dir()

    def test_unfiltered_skipped_when_source_absent(self, tmp_path: Path) -> None:
        host, proj = tmp_path / "host", tmp_path / "proj"
        seed_cred_files(GOOSE_DESC, _StubTarget(), source_root=host, project_home=proj)
        assert not (proj / ".config/goose/secrets.yaml").exists()

    def test_filtered_routes_through_transform(self, tmp_path: Path) -> None:
        host, proj = tmp_path / "host", tmp_path / "proj"
        _write(host / ".claude/.credentials.json", "CREDS")
        _write(host / ".claude.json", "SETTINGS")
        t = _MarkerTarget()
        seed_cred_files(CLAUDE_DESC, t, source_root=host, project_home=proj)
        assert (".claude/.credentials.json", "in", False) in t.calls
        assert (".claude.json", "in", False) in t.calls
        assert (proj / ".claude/.credentials.json").read_text() == "MARKER:in:CREDS"
        assert (proj / ".claude.json").read_text() == "MARKER:in:SETTINGS"

    def test_filtered_src_none_when_source_absent(self, tmp_path: Path) -> None:
        host, proj = tmp_path / "host", tmp_path / "proj"
        t = _MarkerTarget()
        seed_cred_files(CLAUDE_DESC, t, source_root=host, project_home=proj)
        assert (".claude.json", "in", True) in t.calls
        assert (proj / ".claude.json").read_text() == "MARKER:none"

    def test_filtered_src_none_when_private(self, tmp_path: Path) -> None:
        host, proj = tmp_path / "host", tmp_path / "proj"
        _write(host / ".claude.json", "SETTINGS")
        t = _MarkerTarget()
        seed_cred_files(CLAUDE_DESC, t, source_root=None, project_home=proj)
        # Private tier: src forced to None even though host file exists.
        assert (".claude.json", "in", True) in t.calls

    def test_filtered_chmod_600_after_transform(self, tmp_path: Path) -> None:
        host, proj = tmp_path / "host", tmp_path / "proj"
        _write(host / ".claude/.credentials.json", "CREDS")
        _write(host / ".claude.json", "SETTINGS")
        seed_cred_files(CLAUDE_DESC, _MarkerTarget(), source_root=host, project_home=proj)
        assert _mode(proj / ".claude/.credentials.json") == 0o600
        assert _mode(proj / ".claude.json") == 0o600


# ---------------------------------------------------------------------------
# refresh_cred_files (primitive; source_root)
# ---------------------------------------------------------------------------


class TestRefresh:
    def test_sync_refreshed_source_to_project(self, tmp_path: Path) -> None:
        host, proj = tmp_path / "host", tmp_path / "proj"
        _write(proj / ".config/goose/secrets.yaml", "old", mtime=100)
        _write(host / ".config/goose/secrets.yaml", "new", mtime=200)
        refresh_cred_files(GOOSE_DESC, _StubTarget(), source_root=host, project_home=proj)
        dst = proj / ".config/goose/secrets.yaml"
        assert dst.read_text() == "new"
        assert _mode(dst) == 0o600

    def test_seed_once_not_refreshed(self, tmp_path: Path) -> None:
        host, proj = tmp_path / "host", tmp_path / "proj"
        _write(proj / ".config/goose/config.yaml", "old", mtime=100)
        _write(host / ".config/goose/config.yaml", "new", mtime=200)
        refresh_cred_files(GOOSE_DESC, _DirectionTarget(), source_root=host, project_home=proj)
        assert (proj / ".config/goose/config.yaml").read_text() == "old"

    def test_mtime_gate_skips_when_source_not_newer(self, tmp_path: Path) -> None:
        host, proj = tmp_path / "host", tmp_path / "proj"
        _write(proj / ".config/goose/secrets.yaml", "proj", mtime=200)
        _write(host / ".config/goose/secrets.yaml", "host", mtime=100)
        refresh_cred_files(GOOSE_DESC, _StubTarget(), source_root=host, project_home=proj)
        assert (proj / ".config/goose/secrets.yaml").read_text() == "proj"

    def test_private_noop(self, tmp_path: Path) -> None:
        host, proj = tmp_path / "host", tmp_path / "proj"
        _write(proj / ".config/goose/secrets.yaml", "old", mtime=100)
        _write(host / ".config/goose/secrets.yaml", "new", mtime=200)
        refresh_cred_files(GOOSE_DESC, _StubTarget(), source_root=None, project_home=proj)
        assert (proj / ".config/goose/secrets.yaml").read_text() == "old"

    def test_filtered_routes_through_transform_in(self, tmp_path: Path) -> None:
        host, proj = tmp_path / "host", tmp_path / "proj"
        _write(proj / ".claude/.credentials.json", "old", mtime=100)
        _write(host / ".claude/.credentials.json", "new", mtime=200)
        refresh_cred_files(CLAUDE_DESC, _DirectionTarget(), source_root=host, project_home=proj)
        assert (proj / ".claude/.credentials.json").read_text() == "FILTERED-IN"

    def test_missing_source_skipped(self, tmp_path: Path) -> None:
        host, proj = tmp_path / "host", tmp_path / "proj"
        _write(proj / ".config/goose/secrets.yaml", "keep", mtime=100)
        refresh_cred_files(GOOSE_DESC, _StubTarget(), source_root=host, project_home=proj)
        assert (proj / ".config/goose/secrets.yaml").read_text() == "keep"


# ---------------------------------------------------------------------------
# writeback_cred_files (primitive; source_root)
# ---------------------------------------------------------------------------


class TestWriteback:
    def test_sync_written_back_when_project_newer(self, tmp_path: Path) -> None:
        host, proj = tmp_path / "host", tmp_path / "proj"
        _write(host / ".config/goose/secrets.yaml", "old", mtime=100)
        _write(proj / ".config/goose/secrets.yaml", "new", mtime=200)
        writeback_cred_files(GOOSE_DESC, _StubTarget(), source_root=host, project_home=proj)
        assert (host / ".config/goose/secrets.yaml").read_text() == "new"

    def test_seed_once_never_written_back(self, tmp_path: Path) -> None:
        host, proj = tmp_path / "host", tmp_path / "proj"
        _write(host / ".config/goose/config.yaml", "old", mtime=100)
        _write(proj / ".config/goose/config.yaml", "new", mtime=200)
        writeback_cred_files(GOOSE_DESC, _DirectionTarget(), source_root=host, project_home=proj)
        assert (host / ".config/goose/config.yaml").read_text() == "old"

    def test_mtime_gate_skips_when_project_not_newer(self, tmp_path: Path) -> None:
        host, proj = tmp_path / "host", tmp_path / "proj"
        _write(host / ".config/goose/secrets.yaml", "host", mtime=200)
        _write(proj / ".config/goose/secrets.yaml", "proj", mtime=100)
        writeback_cred_files(GOOSE_DESC, _StubTarget(), source_root=host, project_home=proj)
        assert (host / ".config/goose/secrets.yaml").read_text() == "host"

    def test_private_noop(self, tmp_path: Path) -> None:
        host, proj = tmp_path / "host", tmp_path / "proj"
        _write(host / ".config/goose/secrets.yaml", "host", mtime=100)
        _write(proj / ".config/goose/secrets.yaml", "proj", mtime=200)
        writeback_cred_files(GOOSE_DESC, _StubTarget(), source_root=None, project_home=proj)
        assert (host / ".config/goose/secrets.yaml").read_text() == "host"

    def test_filtered_routes_through_transform_out(self, tmp_path: Path) -> None:
        host, proj = tmp_path / "host", tmp_path / "proj"
        _write(host / ".claude/.credentials.json", "old", mtime=100)
        _write(proj / ".claude/.credentials.json", "new", mtime=200)
        writeback_cred_files(CLAUDE_DESC, _DirectionTarget(), source_root=host, project_home=proj)
        assert (host / ".claude/.credentials.json").read_text() == "FILTERED-OUT"


# ---------------------------------------------------------------------------
# Directory specs (is_dir)
# ---------------------------------------------------------------------------


class TestDirSpec:
    def _host_provider(self, host: Path, text: str = "PROVIDER") -> Path:
        return _write(host / ".config/goose/custom_providers/navigator.json", text)

    def _proj_provider(self, proj: Path, text: str = "PROVIDER") -> Path:
        return _write(proj / ".config/goose/custom_providers/navigator.json", text)

    def test_seed_copies_dir_when_source_present(self, tmp_path: Path) -> None:
        host, proj = tmp_path / "host", tmp_path / "proj"
        self._host_provider(host, "SEED")
        seed_cred_files(DIR_DESC, _StubTarget(), source_root=host, project_home=proj)
        dst = proj / ".config/goose/custom_providers/navigator.json"
        assert dst.read_text() == "SEED"
        assert _mode(dst) == 0o600

    def test_seed_skips_dir_when_private(self, tmp_path: Path) -> None:
        host, proj = tmp_path / "host", tmp_path / "proj"
        self._host_provider(host)
        seed_cred_files(DIR_DESC, _StubTarget(), source_root=None, project_home=proj)
        assert not (proj / ".config/goose/custom_providers").exists()

    def test_writeback_mirrors_dir_project_to_source(self, tmp_path: Path) -> None:
        host, proj = tmp_path / "host", tmp_path / "proj"
        self._proj_provider(proj, "BOXVAL")
        writeback_cred_files(DIR_DESC, _StubTarget(), source_root=host, project_home=proj)
        assert (host / ".config/goose/custom_providers/navigator.json").read_text() == "BOXVAL"

    def test_writeback_dir_noop_when_private(self, tmp_path: Path) -> None:
        host, proj = tmp_path / "host", tmp_path / "proj"
        self._proj_provider(proj, "BOXVAL")
        writeback_cred_files(DIR_DESC, _StubTarget(), source_root=None, project_home=proj)
        assert not (host / ".config/goose/custom_providers").exists()


# ---------------------------------------------------------------------------
# base transform_cred default
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Tier orchestrators — per-tier dispatch + global_sync hop + dir creation
# ---------------------------------------------------------------------------


class TestTierGlobal:
    def test_global_seed_uses_host_home(self, tmp_path: Path) -> None:
        host, proj = tmp_path / "host", tmp_path / "proj"
        _write(host / ".config/goose/secrets.yaml", "gsecret")
        seed_box_credentials(
            GOOSE_DESC, _StubTarget(), auth=_global_src(),
            host_home=host, project_home=proj,
        )
        assert (proj / ".config/goose/secrets.yaml").read_text() == "gsecret"

    def test_global_writeback_to_host_home(self, tmp_path: Path) -> None:
        host, proj = tmp_path / "host", tmp_path / "proj"
        _write(host / ".config/goose/secrets.yaml", "old", mtime=100)
        _write(proj / ".config/goose/secrets.yaml", "new", mtime=200)
        writeback_box_credentials(
            GOOSE_DESC, _StubTarget(), auth=_global_src(),
            host_home=host, project_home=proj,
        )
        assert (host / ".config/goose/secrets.yaml").read_text() == "new"


class TestTierWorkset:
    def test_workset_seed_uses_workset_source_not_host(self, tmp_path: Path) -> None:
        host, proj, ws = tmp_path / "host", tmp_path / "proj", tmp_path / "ws"
        _write(host / ".config/goose/secrets.yaml", "HOST")
        _write(ws / ".config/goose/secrets.yaml", "WORKSET")
        seed_box_credentials(
            GOOSE_DESC, _StubTarget(), auth=_workset_src(str(ws)),
            host_home=host, project_home=proj,
        )
        # WORKSET wins — the box's secret comes from the workset dir, not host.
        assert (proj / ".config/goose/secrets.yaml").read_text() == "WORKSET"

    def test_workset_seed_creates_source_dirs(self, tmp_path: Path) -> None:
        host, proj, ws = tmp_path / "host", tmp_path / "proj", tmp_path / "ws"
        # The workset source dir does NOT exist yet — the plugin creates it.
        seed_box_credentials(
            GOOSE_DESC, _StubTarget(), auth=_workset_src(str(ws)),
            host_home=host, project_home=proj,
        )
        # init_dirs substructure created under the workset source root.
        assert (ws / ".config/goose").is_dir()
        # each cred file's parent dir created.
        assert (ws / ".config/goose").is_dir()

    def test_workset_writeback_to_workset_source(self, tmp_path: Path) -> None:
        host, proj, ws = tmp_path / "host", tmp_path / "proj", tmp_path / "ws"
        _write(ws / ".config/goose/secrets.yaml", "old", mtime=100)
        _write(proj / ".config/goose/secrets.yaml", "new", mtime=200)
        writeback_box_credentials(
            GOOSE_DESC, _StubTarget(), auth=_workset_src(str(ws)),
            host_home=host, project_home=proj,
        )
        # The box writes to the WORKSET dir, not host home.
        assert (ws / ".config/goose/secrets.yaml").read_text() == "new"
        assert not (host / ".config/goose/secrets.yaml").exists()


class TestGlobalSyncHop:
    def test_refresh_top_down_global_to_workset_to_box(self, tmp_path: Path) -> None:
        host, proj, ws = tmp_path / "host", tmp_path / "proj", tmp_path / "ws"
        # global (host) has the freshest secret; the workset dir + box are stale.
        _write(host / ".config/goose/secrets.yaml", "GLOBAL", mtime=300)
        _write(ws / ".config/goose/secrets.yaml", "wsold", mtime=100)
        _write(proj / ".config/goose/secrets.yaml", "boxold", mtime=50)
        refresh_box_credentials(
            GOOSE_DESC, _StubTarget(),
            auth=_workset_src(str(ws), global_sync=True),
            host_home=host, project_home=proj,
        )
        # TOP-DOWN: global refreshed the workset dir, which refreshed the box.
        assert (ws / ".config/goose/secrets.yaml").read_text() == "GLOBAL"
        assert (proj / ".config/goose/secrets.yaml").read_text() == "GLOBAL"

    def test_writeback_bottom_up_box_to_workset_to_global(self, tmp_path: Path) -> None:
        host, proj, ws = tmp_path / "host", tmp_path / "proj", tmp_path / "ws"
        _write(host / ".config/goose/secrets.yaml", "gold", mtime=50)
        _write(ws / ".config/goose/secrets.yaml", "wsold", mtime=100)
        _write(proj / ".config/goose/secrets.yaml", "BOXNEW", mtime=300)
        writeback_box_credentials(
            GOOSE_DESC, _StubTarget(),
            auth=_workset_src(str(ws), global_sync=True),
            host_home=host, project_home=proj,
        )
        # BOTTOM-UP: box -> workset dir -> global (host home).
        assert (ws / ".config/goose/secrets.yaml").read_text() == "BOXNEW"
        assert (host / ".config/goose/secrets.yaml").read_text() == "BOXNEW"

    def test_no_global_sync_leaves_global_untouched(self, tmp_path: Path) -> None:
        host, proj, ws = tmp_path / "host", tmp_path / "proj", tmp_path / "ws"
        _write(host / ".config/goose/secrets.yaml", "GLOBALKEPT", mtime=50)
        _write(proj / ".config/goose/secrets.yaml", "BOXNEW", mtime=300)
        writeback_box_credentials(
            GOOSE_DESC, _StubTarget(),
            auth=_workset_src(str(ws), global_sync=False),
            host_home=host, project_home=proj,
        )
        # The box wrote to the workset dir; global (host) is NOT touched.
        assert (ws / ".config/goose/secrets.yaml").read_text() == "BOXNEW"
        assert (host / ".config/goose/secrets.yaml").read_text() == "GLOBALKEPT"


class TestTierBox:
    def test_private_box_seed_writes_no_cred_content(self, tmp_path: Path) -> None:
        host, proj = tmp_path / "host", tmp_path / "proj"
        _write(host / ".config/goose/secrets.yaml", "HOST")
        seed_box_credentials(
            GOOSE_DESC, _StubTarget(), auth=_box_src(),
            host_home=host, project_home=proj,
        )
        assert not (proj / ".config/goose/secrets.yaml").exists()
        # init_dirs still created.
        assert (proj / ".config/goose").is_dir()

    def test_private_box_writeback_noop(self, tmp_path: Path) -> None:
        host, proj = tmp_path / "host", tmp_path / "proj"
        _write(host / ".config/goose/secrets.yaml", "HOSTKEPT", mtime=100)
        _write(proj / ".config/goose/secrets.yaml", "boxnew", mtime=200)
        writeback_box_credentials(
            GOOSE_DESC, _StubTarget(), auth=_box_src(),
            host_home=host, project_home=proj,
        )
        # Private box never propagates — host untouched, no workset dir made.
        assert (host / ".config/goose/secrets.yaml").read_text() == "HOSTKEPT"


# ---------------------------------------------------------------------------
# REAL (unmocked) end-to-end resolve → seed: no dir escapes the host root
# ---------------------------------------------------------------------------


def _real_auth_source(mode: str, *, agent_name: str = "goose"):
    """Resolve an AuthSource through the REAL pipeline (auth_chain_floor →
    build_launch_snapshot → resolve_auth_source), NOT a hand-built one.

    The hand-built AuthSource fixtures above set ``workset_source`` explicitly, so
    they cannot reproduce the standalone ``@workset.auth.path/<agent>`` →
    ``/<agent>`` escape (workset.auth.path=None renders the @-ref as ``""``). This
    exercises the true resolver so the guard is proven against the real value.
    """
    from kanibako.settings.settings_launch import (
        auth_chain_floor,
        build_launch_snapshot,
        meta_identity_floor,
        meta_runtime_floor,
        resolve_auth_source,
    )
    from kanibako.settings.settings_resolve import ResolveCtx

    ctx = ResolveCtx(
        agent_name=agent_name, workset_name="__STANDALONE__",
        host_home="/host", xdg={},
    )
    chain = auth_chain_floor(mode=mode, agent_name=agent_name)
    meta_id = meta_identity_floor(
        box_name="b", project_path="/p", inbox="/i", share_global="/s",
        share_workset=None, agent_name=agent_name,
        agent_real_name=agent_name, agent_auth_share_support=True,
    )
    mr = meta_runtime_floor(
        mode=mode,
        ws_name=("__PRIMARY__" if mode == "primary" else "__STANDALONE__"),
        ws_root_literal=("/ws" if mode != "primary" else None),
    )
    snap = build_launch_snapshot(
        agent_name=agent_name, ctx=ctx, system_path=None, agent_path=None,
        workset_path=None, box_path=None,
        auth_chain=chain, meta_runtime=mr, meta_identity=meta_id,
    )
    return resolve_auth_source(snap, mode=mode)


class TestNoHostRootEscape:
    """Regression: a non-workset box must NEVER mkdir a workset source dir.

    For standalone, ``meta.box.auth.workset_path = @workset.auth.path/<agent>`` with
    ``workset.auth.path=None`` used to resolve to the literal ``/<agent>`` and be
    carried on ``AuthSource.workset_source``, so ``seed_box_credentials`` →
    ``_create_workset_source_dirs`` did ``Path("/<agent>").mkdir(parents=True)``
    against the host ROOT (PermissionError on non-root; ``/``-pollution as root).
    """

    def test_standalone_resolve_scrubs_workset_source(self) -> None:
        """The REAL resolver yields tier != workset with workset_source None (the
        ``/<agent>`` garbage is scrubbed, not carried)."""
        a = _real_auth_source("standalone")
        assert a.tier == "global"      # standalone still shares at global.
        assert a.workset_source is None  # no ``/goose`` escape onto the source.

    def test_standalone_seed_creates_no_dir_outside_project_home(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """UNMOCKED seed with the REAL standalone AuthSource creates NO directory
        outside project_home / host_home — proves the host-root mkdir is gone.

        Mutation proof: revert EITHER guard (the resolver scrub OR the tier gate in
        _create_workset_source_dirs) and this goes red — a ``/goose`` dir (or a
        PermissionError) appears.
        """
        import kanibako.targets.credsync as _cs

        # Sandbox the filesystem-root mkdir: record any mkdir whose target is NOT
        # under host or proj, so a stray absolute path (``/goose``) is caught here
        # WITHOUT actually touching the real host root.
        host, proj = tmp_path / "host", tmp_path / "proj"
        host.mkdir()
        proj.mkdir()
        escapes: list[str] = []
        real_mkdir = Path.mkdir

        def _guard_mkdir(self, *args, **kw):  # type: ignore[no-untyped-def]
            rp = self.resolve()
            if host.resolve() not in rp.parents and rp != host.resolve() \
               and proj.resolve() not in rp.parents and rp != proj.resolve() \
               and rp != tmp_path.resolve() and tmp_path.resolve() not in rp.parents:
                escapes.append(str(self))
                return  # do NOT actually create it (would hit the real root).
            return real_mkdir(self, *args, **kw)

        monkeypatch.setattr(Path, "mkdir", _guard_mkdir)

        auth = _real_auth_source("standalone")
        _cs.seed_box_credentials(
            GOOSE_DESC, _StubTarget(), auth=auth,
            host_home=host, project_home=proj,
        )

        assert escapes == [], f"seed created dirs outside project/host: {escapes}"
        # init_dirs still land INSIDE the project home (positive control).
        assert (proj / ".config/goose").is_dir()


# ---------------------------------------------------------------------------
# Persona endpoint cred fork (block B, 2026-07-01c) — suppress_oauth
# ---------------------------------------------------------------------------


class TestEndpointCredFork:
    """The fail-safe half of the persona cred fork: when the active agent resolves a
    non-<None> ``agent.<node>.endpoint`` (``suppress_oauth=True``), the box's
    host-login OAuth (the SYNC-cadence cred_files) is DROPPED so the Anthropic token
    is never delivered to a box pointed at a third-party endpoint. SEED_ONCE specs
    (static, non-login) survive. ``suppress_oauth=False`` (bare / <None>) is
    byte-identical to today.
    """

    def test_refresh_suppresses_oauth_sync_when_endpoint_set(
        self, tmp_path: Path
    ) -> None:
        host, proj = tmp_path / "host", tmp_path / "proj"
        _write(host / ".claude/.credentials.json", "OAUTH")
        refresh_box_credentials(
            CLAUDE_DESC, _MarkerTarget(), auth=_global_src(),
            host_home=host, project_home=proj,
            suppress_oauth=True,
        )
        # The OAuth (SYNC, filtered) cred is NOT synced into the box.
        assert not (proj / ".claude/.credentials.json").exists()

    def test_refresh_syncs_oauth_when_endpoint_unset(self, tmp_path: Path) -> None:
        # MUTATION CHECK: the ONLY difference from the suppressed case is the flag.
        # With suppress_oauth=False (bare / <None>), the OAuth cred IS synced —
        # proving the suppression above is non-vacuous.
        host, proj = tmp_path / "host", tmp_path / "proj"
        _write(host / ".claude/.credentials.json", "OAUTH")
        refresh_box_credentials(
            CLAUDE_DESC, _MarkerTarget(), auth=_global_src(),
            host_home=host, project_home=proj,
            suppress_oauth=False,
        )
        assert (proj / ".claude/.credentials.json").read_text() == "MARKER:in:OAUTH"

    def test_refresh_default_is_no_suppression(self, tmp_path: Path) -> None:
        # Backward-compat: omitting suppress_oauth entirely == today's behavior
        # (OAuth synced). Byte-identical to the pre-block-B call signature.
        host, proj = tmp_path / "host", tmp_path / "proj"
        _write(host / ".claude/.credentials.json", "OAUTH")
        refresh_box_credentials(
            CLAUDE_DESC, _MarkerTarget(), auth=_global_src(),
            host_home=host, project_home=proj,
        )
        assert (proj / ".claude/.credentials.json").read_text() == "MARKER:in:OAUTH"

    def test_seed_suppresses_oauth_sync_when_endpoint_set(
        self, tmp_path: Path
    ) -> None:
        host, proj = tmp_path / "host", tmp_path / "proj"
        _write(host / ".claude/.credentials.json", "OAUTH")
        seed_box_credentials(
            CLAUDE_DESC, _MarkerTarget(), auth=_global_src(),
            host_home=host, project_home=proj,
            suppress_oauth=True,
        )
        assert not (proj / ".claude/.credentials.json").exists()
        # init_dirs still created (the box home is still prepared).
        assert (proj / ".claude").is_dir()

    def test_seed_syncs_oauth_when_endpoint_unset(self, tmp_path: Path) -> None:
        # MUTATION CHECK for the seed path.
        host, proj = tmp_path / "host", tmp_path / "proj"
        _write(host / ".claude/.credentials.json", "OAUTH")
        seed_box_credentials(
            CLAUDE_DESC, _MarkerTarget(), auth=_global_src(),
            host_home=host, project_home=proj,
            suppress_oauth=False,
        )
        assert (proj / ".claude/.credentials.json").read_text() == "MARKER:in:OAUTH"

    def test_codex_authjson_dropped_when_endpoint_set(self, tmp_path: Path) -> None:
        # INC 3 confirm: a codex persona launch (endpoint set → suppress_oauth=True)
        # DROPS the codex ``.codex/auth.json`` SYNC cred, so host ChatGPT auth never
        # reaches a NaviGator box — the same generic fork the claude path uses.
        host, proj = tmp_path / "host", tmp_path / "proj"
        _write(host / ".codex/auth.json", "CHATGPT")
        seed_box_credentials(
            CODEX_DESC, _MarkerTarget(), auth=_global_src(),
            host_home=host, project_home=proj,
            suppress_oauth=True,
        )
        assert not (proj / ".codex/auth.json").exists()
        assert (proj / ".codex").is_dir()  # init_dirs still prepared.

    def test_codex_authjson_synced_when_endpoint_unset(self, tmp_path: Path) -> None:
        # MUTATION CHECK: a BARE codex box (suppress_oauth=False) DOES receive
        # auth.json — proving the codex suppression above is non-vacuous.
        host, proj = tmp_path / "host", tmp_path / "proj"
        _write(host / ".codex/auth.json", "CHATGPT")
        seed_box_credentials(
            CODEX_DESC, _MarkerTarget(), auth=_global_src(),
            host_home=host, project_home=proj,
            suppress_oauth=False,
        )
        # codex auth.json is filtered=False → copied as-is (no marker transform).
        assert (proj / ".codex/auth.json").read_text() == "CHATGPT"

    def test_goose_sync_creds_dropped_when_endpoint_set(self, tmp_path: Path) -> None:
        # INC G1 confirm: a goose persona (endpoint set → suppress_oauth=True) DROPS
        # ALL of goose's SYNC-cadence cred files (secrets.yaml + config.yaml +
        # custom_providers/) via the SAME generic fork, so a bare goose login never
        # reaches an OpenAI-compatible-endpoint box.  Uses the REAL goose descriptor.
        from kanibako.plugins.goose.target import GooseTarget
        desc = GooseTarget().descriptor
        # All three shipped goose cred_files are SYNC (the fork drops SYNC).
        sync_rels = [s.home_rel for s in desc.cred_files if s.cadence is Cadence.SYNC]
        assert sorted(sync_rels) == [
            ".config/goose/config.yaml",
            ".config/goose/custom_providers",
            ".config/goose/secrets.yaml",
        ]
        host, proj = tmp_path / "host", tmp_path / "proj"
        _write(host / ".config/goose/secrets.yaml", "SECRETS")
        _write(host / ".config/goose/config.yaml", "CONFIG")
        _write(host / ".config/goose/custom_providers/p.yaml", "PROVIDER")
        seed_box_credentials(
            desc, _StubTarget(), auth=_global_src(),
            host_home=host, project_home=proj,
            suppress_oauth=True,
        )
        assert not (proj / ".config/goose/secrets.yaml").exists()
        assert not (proj / ".config/goose/config.yaml").exists()
        assert not (proj / ".config/goose/custom_providers").exists()
        assert (proj / ".config/goose").is_dir()  # init_dirs still prepared.

    def test_goose_sync_creds_synced_when_endpoint_unset(self, tmp_path: Path) -> None:
        # MUTATION CHECK: a BARE goose box (suppress_oauth=False) DOES receive its
        # SYNC creds — proving the goose suppression above is non-vacuous.
        from kanibako.plugins.goose.target import GooseTarget
        desc = GooseTarget().descriptor
        host, proj = tmp_path / "host", tmp_path / "proj"
        _write(host / ".config/goose/secrets.yaml", "SECRETS")
        seed_box_credentials(
            desc, _StubTarget(), auth=_global_src(),
            host_home=host, project_home=proj,
            suppress_oauth=False,
        )
        assert (proj / ".config/goose/secrets.yaml").exists()
