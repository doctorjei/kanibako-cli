"""Category resolution through the LIVE launch pipeline (no frozen oracle).

These cases drive ``build_launch_snapshot`` → ``snapshot_category_entries`` →
``reconcile_categories`` — the single route a real launch takes. They were split
out of the frozen-oracle file on 2026-07-29 so that file has exactly ONE purpose:
the retired by-name resolver and its own direct tests. Nothing here may import
``flawed_oracle_categories``.

Agent-scope keys are DISCRIMINATED (``agent.<agent>.<category>.<name>``, spec §2d /
§0 L21). The undiscriminated ``agent.<category>`` form is not a key and appears
ONLY in the frozen-oracle file.
"""

from __future__ import annotations

from pathlib import Path

from kanibako.settings_categories import reconcile_categories
from kanibako.settings_resolve import (
    ResolveCtx,
)

HOST_HOME = "/home/u"


def make_ctx(
    *,
    agent_name: str | None = "claude",
    workset_name: str | None = "myws",
    host_home: str = HOST_HOME,
    xdg: dict[str, str] | None = None,
    config: dict[str, str] | None = None,
) -> ResolveCtx:
    return ResolveCtx(
        agent_name=agent_name,
        workset_name=workset_name,
        host_home=host_home,
        xdg=xdg if xdg is not None else {"XDG_DATA_HOME": "/data"},
        config=config or {},
    )

# ---------------------------------------------------------------------------
# B2b: per-mode BYTE-IDENTITY of the @-ref-routed home/vault binds (the
# equivalence bar) + the box.bindings.rw.home cascade override + workset anchors.
# ---------------------------------------------------------------------------


def _resolve_home_vault(floor, *, mode, config=None):
    """Resolve home/vault through the LIVE build_launch_snapshot pipeline (the
    single route the launch uses) → {box_dest: host_src}.

    *config* is the Layer-1 ``config.*`` foundation; PRIMARY mode needs
    ``config.primary_workset`` because ``meta.runtime.ws_root`` is the
    ``@config.primary_workset`` @-ref for that mode (spec §1A L233).
    """
    from kanibako.settings_launch import (
        build_launch_snapshot,
        snapshot_category_entries,
    )

    ctx = make_ctx(workset_name=None, config=config)
    snap = build_launch_snapshot(
        agent_name="claude", ctx=ctx,
        system_path=None, agent_path=None, workset_path=None, box_path=None,
        default_categories=floor,
    )
    rec = reconcile_categories(
        snapshot_category_entries(snap, active_agent="claude", box_ctx=ctx)
    )
    return {m.box_dest: m.host_src for m in rec.mounts}


class TestB2bHomeVaultByteIdentity:
    """The equivalence bar: a DEFAULT box's resolved home/vault binds are
    byte-identical to the pre-B2b proj-attr literals, per mode."""

    def test_primary_named_home_vault_resolve_to_proj_literals(self):
        # PRIMARY/NAMED: home routes the ONE mode-independent @meta.box.path/home
        # declaration; vault routes @workset.vault_*/@meta.box.name.  Both resolve to
        # the SAME literal proj.shell_path / proj.vault_*_path the old injection used.
        # ⚑ The ASSERTED PATHS are unchanged from before the anchor collapse — only
        # the floor SPELLINGS moved.  That is the point of the phase.
        from kanibako.settings_launch import (
            meta_identity_floor,
            meta_runtime_floor,
            workset_anchor_floor,
        )

        floor = {
            "box.bindings.rw.home": ("@meta.box.path/home", "~", "Z,U"),
            "box.bindings.ro.vault": (
                "@workset.vault_ro/@meta.box.name", "~/vault/ro", "ro",
            ),
            "box.bindings.rw.vault": (
                "@workset.vault_rw/@meta.box.name", "~/vault/rw", "Z,U",
            ),
        }
        # The anchors are @-ref FORMULAS rooted at meta.workset.path, so the workset
        # root is what pins the resolved values (= @config.primary_workset at launch).
        floor.update(meta_runtime_floor(
            mode="primary", ws_name="__PRIMARY__",
        ))
        floor.update(workset_anchor_floor(
            mode="primary", helper_log="/data/pw/logs/mybox.jsonl",
        ))
        floor.update(meta_identity_floor(
            box_name="mybox", project_path="/code/x", inbox="/i",
            share_global="/sg", share_workset="/sw",
        ))
        by_dest = _resolve_home_vault(
            floor, mode="primary", config={"config.primary_workset": "/data/pw"},
        )
        # Byte-identical to proj.shell_path = boxes/<name>/home, vault/{ro,rw}/<name>.
        assert by_dest["/home/agent"] == "/data/pw/boxes/mybox/home"
        assert by_dest["/home/agent/vault/ro"] == "/data/pw/vault/ro/mybox"
        assert by_dest["/home/agent/vault/rw"] == "/data/pw/vault/rw/mybox"

    def test_standalone_home_vault_resolve_to_proj_literals(self):
        # STANDALONE: home routes the SAME @meta.box.path/home declaration as
        # primary/named (the per-mode variation lives in meta.box.path = the EMPTY
        # LEAF @workset.boxes); vault routes the bare @workset.vault_* (a lone box has
        # no per-box vault subdir).  meta.workset.path = the project ROOT, so these
        # resolve to <root>/box_data/home = proj.shell_path and <root>/vault/{ro,rw} =
        # proj.vault_{ro,rw}_path — byte-identical to before the anchor collapse.
        from kanibako.settings_launch import (
            meta_identity_floor,
            meta_runtime_floor,
            workset_anchor_floor,
        )

        floor = {
            "box.bindings.rw.home": ("@meta.box.path/home", "~", "Z,U"),
            "box.bindings.ro.vault": ("@workset.vault_ro", "~/vault/ro", "ro"),
            "box.bindings.rw.vault": ("@workset.vault_rw", "~/vault/rw", "Z,U"),
        }
        # meta.workset.path = @meta.runtime.ws_root = <root> (the standalone launch
        # passes str(proj.metadata_path) = the project ROOT as ws_root_literal).
        floor.update(meta_runtime_floor(
            mode="standalone", ws_name="__STANDALONE__", ws_root_literal="/proj",
        ))
        floor.update(workset_anchor_floor(
            mode="standalone", helper_log="/proj/box_data/sb.jsonl",
        ))
        floor.update(meta_identity_floor(
            box_name="sb", project_path="/proj/workspace", inbox="/i",
            share_global="/sg", share_workset=None,
        ))
        by_dest = _resolve_home_vault(floor, mode="standalone")
        # <root>=/proj: home = /proj/box_data/home, vault = /proj/vault/{ro,rw}.
        assert by_dest["/home/agent"] == "/proj/box_data/home"
        assert by_dest["/home/agent/vault/ro"] == "/proj/vault/ro"
        assert by_dest["/home/agent/vault/rw"] == "/proj/vault/rw"

    def test_box_bindings_home_cascade_override_wins(self):
        # Option A: a box.bindings.rw.home CASCADE override (box scope) WINS over the
        # spec-derived @meta.box.path/home default (the mechanism for a custom home,
        # replacing the dropped meta["shell"] override).
        from kanibako.settings_launch import (
            build_launch_snapshot,
            meta_identity_floor,
            meta_runtime_floor,
            snapshot_category_entries,
            workset_anchor_floor,
        )

        floor = {
            "box.bindings.rw.home": ("@meta.box.path/home", "~", "Z,U"),
        }
        floor.update(meta_runtime_floor(mode="primary", ws_name="__PRIMARY__"))
        floor.update(workset_anchor_floor(
            mode="primary", helper_log="/l/mybox.jsonl",
        ))
        floor.update(meta_identity_floor(
            box_name="mybox", project_path="/code/x", inbox="/i",
            share_global="/sg", share_workset="/sw",
        ))
        ctx = make_ctx(
            workset_name=None, config={"config.primary_workset": "/data/pw"},
        )
        # A box settings FILE setting box.bindings.rw.home to a custom host path.
        box_overrides = {
            "box": {"bindings": {"rw": {"home": ["/custom/home", "~"]}}},
        }
        import yaml
        import tempfile
        import os
        fd, path = tempfile.mkstemp(suffix=".yaml")
        os.write(fd, yaml.safe_dump(box_overrides).encode())
        os.close(fd)
        try:
            from pathlib import Path
            snap = build_launch_snapshot(
                agent_name="claude", ctx=ctx,
                system_path=None, agent_path=None, workset_path=None,
                box_path=Path(path),
                default_categories=floor,
            )
            rec = reconcile_categories(
                snapshot_category_entries(snap, active_agent="claude", box_ctx=ctx)
            )
            by_dest = {m.box_dest: m.host_src for m in rec.mounts}
            # The box cascade override WINS over the spec-derived default.
            assert by_dest["/home/agent"] == "/custom/home"
        finally:
            os.unlink(path)


class TestB2bWorksetAnchors:
    """Layout-anchor materialization: the workset roots + the RO box root."""

    def test_primary_named_anchors_present(self):
        from kanibako.settings_launch import workset_anchor_floor

        floor = workset_anchor_floor(
            mode="named",
            helper_log="/ws/logs/b.jsonl",
            workset_channels={"commons": "/ws/ch/commons", "chat": "/ws/ch/chat",
                              "share": "/ws/ch/share"},
        )
        # Every anchor is the spec's self-resolving @-ref FORMULA (spec §2c).
        assert floor["workset.boxes"] == "@meta.workset.path/boxes"
        assert floor["workset.vault_ro"] == "@meta.workset.path/vault/ro"
        assert floor["workset.vault_rw"] == "@meta.workset.path/vault/rw"
        assert floor["workset.logs"] == "@meta.workset.path/logs"
        # The RO box root: primary/named carry the per-box name leaf.
        assert floor["meta.box.path"] == "@workset.boxes/@meta.box.name"
        assert floor["meta.box.helper_log"] == "/ws/logs/b.jsonl"
        assert floor["workset.channels.commons"] == "/ws/ch/commons"

    def test_standalone_anchors(self):
        """Standalone's anchors carry REAL values (they used to be ``None``).

        ⚑ DELIBERATE INVERSION of the retired ``test_standalone_anchors_are_none``.
        The spec states the new values outright — ``workset.boxes =
        @meta.workset.path/box_data`` (§2c STANDALONE), ``workset.logs =
        @meta.box.path``, and ``workset.vault_{ro,rw}`` uniform in ALL PROJECTS — so
        the old "these are None" assertion is what the spec changed, not something
        this test may keep asserting.

        What BACKSTOPS the inversion is NOT this test: it is the byte-identity gate
        over the RESOLVED ABSOLUTE PATHS (home / vault ro / vault rw / helper_log ×
        3 modes), captured from the unmodified tree before the change and re-run
        after. No edit to this file can influence that comparison. See also
        ``TestP1BoxRootAnchor`` below, which asserts the resolved paths directly.
        """
        from kanibako.settings_launch import workset_anchor_floor

        floor = workset_anchor_floor(
            mode="standalone", helper_log="/proj/box_data/b.jsonl",
        )
        # Standalone roots its degenerate workset at the project dir: the box store
        # is the box_data/ marker dir, and the logs live inside the box root itself.
        assert floor["workset.boxes"] == "@meta.workset.path/box_data"
        assert floor["workset.logs"] == "@meta.box.path"
        # The vault roots are UNIFORM with primary/named (only the BIND differs).
        assert floor["workset.vault_ro"] == "@meta.workset.path/vault/ro"
        assert floor["workset.vault_rw"] == "@meta.workset.path/vault/rw"
        # The EMPTY LEAF: a BARE whole-value ref — workset.boxes IS the box root, so
        # there is no join, hence no trailing separator and no empty path segment.
        assert floor["meta.box.path"] == "@workset.boxes"
        assert not floor["meta.box.path"].endswith("/")
        # helper_log still routes a whole-value anchor (the spec's literal spelling
        # is not expressible — the ref-name grammar swallows the .jsonl suffix).
        assert floor["meta.box.helper_log"] == "/proj/box_data/b.jsonl"
        # No invented resolved-literal home/vault anchors remain.
        assert "meta.box.home_src" not in floor
        assert "meta.box.vault_ro_src" not in floor
        assert "meta.box.vault_rw_src" not in floor
        # No workset channels for standalone.
        assert "workset.channels.commons" not in floor


# ---------------------------------------------------------------------------
# P1: meta.box.path — the RO per-mode BOX ROOT (spec §2c L740/L770, §2a L505).
#
# These drive the REAL shipped defaults (``core_defaults.core_default_categories``
# reading ``core-defaults.yaml``) through the REAL floor builders and the REAL
# launch pipeline, for all three modes — rather than a hand-written floor. They are
# the standing form of the P1 acceptance gate: the resolved home/vault/log mounts
# must equal the ``proj.*`` host paths the launch has always used, so the anchor
# collapse cannot silently relocate a user's home, vault or log.
# ---------------------------------------------------------------------------


class _ProbeProj:
    """ProjectPaths stand-in carrying the per-mode layout the real helpers build."""

    def __init__(self, **kw):
        self.__dict__.update(kw)


class _ProbeStd:
    """StandardPaths stand-in: only the PRIMARY vault roots are consumed here."""

    def __init__(self, pw):
        self.primary_vault_ro = pw / "vault" / "ro"
        self.primary_vault_rw = pw / "vault" / "rw"


def _probe_cases(tmp_path):
    """(mode, proj, ws_root, helper_log) for each of the three box modes.

    The per-mode layouts come from the REAL path helpers, so a change to the
    on-disk layout shows up here rather than being re-asserted by hand.
    """
    from kanibako.paths import (
        _primary_box_paths,
        _standalone_box_paths,
        _workset_box_paths,
    )

    pw = tmp_path / "pw"
    md = pw / "boxes" / "mybox"
    sh, vro, vrw = _primary_box_paths(_ProbeStd(pw), md, "mybox")
    yield (
        "primary",
        _ProbeProj(name="mybox", metadata_path=md, shell_path=sh,
                   project_path=tmp_path / "code" / "x",
                   vault_ro_path=vro, vault_rw_path=vrw),
        str(pw), pw / "logs" / "mybox.jsonl",
    )

    ws = tmp_path / "ws"
    md = ws / "boxes" / "nbox"
    sh, vro, vrw = _workset_box_paths(md, ws / "vault", "nbox")
    yield (
        "named",
        _ProbeProj(name="nbox", metadata_path=md, shell_path=sh,
                   project_path=ws / "workspaces" / "nbox",
                   vault_ro_path=vro, vault_rw_path=vrw),
        str(ws), ws / "logs" / "nbox.jsonl",
    )

    root = tmp_path / "proj"
    sh, vro, vrw = _standalone_box_paths(root)
    yield (
        "standalone",
        _ProbeProj(name="ab12c_proj", metadata_path=root, shell_path=sh,
                   project_path=root / "workspace",
                   vault_ro_path=vro, vault_rw_path=vrw),
        str(root), root / "box_data" / "ab12c_proj.jsonl",
    )


def _probe_snapshot(mode, proj, ws_root, helper_log):
    """Build the LIVE launch snapshot for *proj* from the REAL shipped defaults."""
    from kanibako import core_defaults
    from kanibako.settings_launch import (
        build_launch_snapshot,
        meta_identity_floor,
        meta_runtime_floor,
        workset_anchor_floor,
    )

    helper_log.parent.mkdir(parents=True, exist_ok=True)
    helper_log.touch()
    ctx = make_ctx(
        workset_name=None,
        xdg={"XDG_DATA_HOME": "/data", "XDG_STATE_HOME": "/state"},
        config={"config.primary_workset": ws_root},
    )
    floor = dict(core_defaults.core_default_categories(
        None, proj, enable_vault=True, mode=mode,
    ))
    floor.update(core_defaults.helper_default_categories(
        box_state_kanibako="/home/agent/.local/state/kanibako",
        socket_path=helper_log,  # any existing path; the socket bind is not asserted
        log_path=helper_log,
    ))
    floor.update(meta_runtime_floor(
        mode=mode, ws_name="__X__",
        ws_root_literal=None if mode == "primary" else ws_root,
    ))
    floor.update(meta_identity_floor(
        box_name=proj.name, project_path=str(proj.project_path),
        inbox="/i", share_global="/sg", share_workset=None,
    ))
    floor.update(workset_anchor_floor(mode=mode, helper_log=str(helper_log)))
    snap = build_launch_snapshot(
        agent_name="claude", ctx=ctx, system_path=None, agent_path=None,
        workset_path=None, box_path=None, default_categories=floor,
    )
    return snap, ctx


def _probe_mounts(mode, proj, ws_root, helper_log):
    from kanibako.settings_launch import snapshot_category_entries

    snap, ctx = _probe_snapshot(mode, proj, ws_root, helper_log)
    rec = reconcile_categories(
        snapshot_category_entries(snap, active_agent="claude", box_ctx=ctx)
    )
    return {m.box_dest: m.host_src for m in rec.mounts}


class TestP1BoxRootAnchor:
    """The RO box root and the one-declaration binds rooted against it."""

    def test_home_resolves_identically_in_all_three_modes(self, tmp_path):
        """The ONE ``@meta.box.path/home`` declaration lands on ``proj.shell_path``.

        This is the standing form of the P1 gate: whatever the mode, the resolved
        home mount is byte-identical to the host home dir the launch has always
        used. A regression in the anchor chain shows up HERE as a wrong absolute
        path, not as a changed spelling.
        """
        for mode, proj, ws_root, hl in _probe_cases(tmp_path):
            by_dest = _probe_mounts(mode, proj, ws_root, hl)
            assert by_dest["/home/agent"] == str(proj.shell_path), mode

    def test_vault_and_logs_resolve_identically_in_all_three_modes(self, tmp_path):
        """The vault + helper-log mounts are unmoved by the anchor collapse."""
        for mode, proj, ws_root, hl in _probe_cases(tmp_path):
            by_dest = _probe_mounts(mode, proj, ws_root, hl)
            assert by_dest["/home/agent/vault/ro"] == str(proj.vault_ro_path), mode
            assert by_dest["/home/agent/vault/rw"] == str(proj.vault_rw_path), mode
            log_dest = "/home/agent/.local/state/kanibako/helpers.jsonl"
            assert by_dest[log_dest] == str(hl), mode

    def test_home_bind_declaration_is_mode_independent(self):
        """The DUPLICATION is retired at the source, not merely at resolution.

        ``core-defaults.yaml`` used to carry a 3-arm per-mode map for the home
        host_src. Asserting the emitted tuple is EQUAL across modes is what stops a
        future per-mode arm from creeping back in.
        """
        from kanibako import core_defaults

        class _P:
            shell_path = Path("/h/home")
            project_path = Path("/h/proj")
            vault_ro_path = Path("/h/vro")
            vault_rw_path = Path("/h/vrw")

        emitted = {
            mode: core_defaults.core_default_categories(
                None, _P(), enable_vault=False, mode=mode,
            )["box.bindings.rw.home"]
            for mode in ("primary", "named", "standalone")
        }
        assert emitted["primary"] == ("@meta.box.path/home", "~", "Z,U")
        assert len(set(emitted.values())) == 1, emitted

    def test_meta_box_path_is_the_box_root_per_mode(self, tmp_path):
        """The anchor means what its name says: the host-side BOX ROOT.

        ``proj.shell_path`` always ends in ``home/``, so its parent IS the box dir
        that contains it — ``boxes/<name>`` for primary/named, ``<root>/box_data``
        for standalone (the EMPTY-LEAF case, where the box root IS
        ``workset.boxes``).
        """
        for mode, proj, ws_root, hl in _probe_cases(tmp_path):
            snap, _ = _probe_snapshot(mode, proj, ws_root, hl)
            assert snap.meta.box.path == str(proj.shell_path.parent), mode

    def test_box_root_has_no_trailing_separator_in_any_mode(self, tmp_path):
        """No mode leaves a dangling separator or a doubled slash in the box root.

        Standalone is the interesting one — its ``meta.box.path`` is a BARE
        whole-value ref, so the resolver inherits ``@workset.boxes`` verbatim rather
        than joining an empty leaf onto it — but the property is asserted for ALL
        THREE modes, because primary/named can produce the same artefact by a
        different route: an empty ``meta.box.name`` makes
        ``@workset.boxes/@meta.box.name`` resolve to ``<…>/boxes/``, which is the
        SHARED box store rather than this box (guarded in
        ``settings_launch._assert_box_root_resolved``, pinned in
        ``tests/test_settings_launch.py``).
        """
        for mode, proj, ws_root, hl in _probe_cases(tmp_path):
            snap, _ = _probe_snapshot(mode, proj, ws_root, hl)
            root = snap.meta.box.path
            assert not root.endswith("/"), mode
            assert "//" not in root, mode
        # The EMPTY LEAF specifically: for standalone the box store IS the box root.
        cases = {m: (p, w, h) for m, p, w, h in _probe_cases(tmp_path)}
        proj, ws_root, hl = cases["standalone"]
        snap, _ = _probe_snapshot("standalone", proj, ws_root, hl)
        assert snap.meta.box.path == snap.workset.boxes
