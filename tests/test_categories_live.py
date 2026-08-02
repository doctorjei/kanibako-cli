"""Category resolution through the LIVE launch pipeline (no frozen oracle).

These cases drive ``build_launch_snapshot`` → ``snapshot_category_entries`` →
``reconcile_categories`` — the single route a real launch takes. They were split
out of the frozen-oracle file on 2026-07-29 so that file has exactly ONE purpose:
the retired by-name resolver and its own direct tests. Nothing here may import
``flawed_oracle_categories``.

Agent-scope keys are DISCRIMINATED (``agent.<agent>.<category>.<name>``, spec §2d /
§0). The undiscriminated ``agent.<category>`` form is not a key and appears
ONLY in the frozen-oracle file.
"""

from __future__ import annotations

from pathlib import Path

from kanibako.settings.settings_categories import reconcile_categories
from kanibako.settings.settings_resolve import (
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
    ``@config.primary_workset`` @-ref for that mode (spec §1A).
    """
    from kanibako.settings.settings_launch import (
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
        from kanibako.settings.settings_launch import (
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
            mode="primary",
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
        from kanibako.settings.settings_launch import (
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
            mode="standalone",
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
        from kanibako.settings.settings_launch import (
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
            mode="primary",
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
        from kanibako.settings.settings_launch import workset_anchor_floor

        floor = workset_anchor_floor(
            mode="named",
            workset_channels={"common": "/ws/ch/common", "chat": "/ws/ch/chat",
                              "share": "/ws/ch/share"},
        )
        # Every anchor is the spec's self-resolving @-ref FORMULA (spec §2c).
        assert floor["workset.boxes"] == "@meta.workset.path/boxes"
        assert floor["workset.vault_ro"] == "@meta.workset.path/vault/ro"
        assert floor["workset.vault_rw"] == "@meta.workset.path/vault/rw"
        assert floor["workset.logs"] == "@meta.workset.path/logs"
        # The RO box root: primary/named carry the per-box name leaf.
        assert floor["meta.box.path"] == "@workset.boxes/@meta.box.name"
        assert floor["workset.channels.common"] == "/ws/ch/common"
        # ⚑ NO construct-time literals here — every anchor is a FORMULA. The
        # retired ``meta.box.helper_log`` was the last one; the helper-log bind
        # now spells itself ``@workset.logs/@{meta.box.name}.jsonl`` (PHASE R).
        assert "meta.box.helper_log" not in floor
        assert all(
            not isinstance(v, str) or v.startswith("@")
            for k, v in floor.items()
            if k.startswith("meta.box.") or k.startswith("workset.vault")
        ), floor

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
        from kanibako.settings.settings_launch import workset_anchor_floor

        floor = workset_anchor_floor(
            mode="standalone",
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
        # No invented resolved-literal anchors remain — not for home/vault, and
        # (since PHASE R made the spec's spelling expressible) not for the log
        # either: the bind is ``@workset.logs/@{meta.box.name}.jsonl``, and
        # ``workset.logs = @meta.box.path`` above is what makes it standalone.
        assert "meta.box.home_src" not in floor
        assert "meta.box.helper_log" not in floor
        assert "meta.box.vault_ro_src" not in floor
        assert "meta.box.vault_rw_src" not in floor
        # No workset channels for standalone.
        assert "workset.channels.common" not in floor


# ---------------------------------------------------------------------------
# P1: meta.box.path — the RO per-mode BOX ROOT (spec §2c, §2a).
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
    from kanibako.settings.paths import (
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
    from kanibako.settings import core_defaults
    from kanibako.settings.settings_launch import (
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
    floor.update(workset_anchor_floor(mode=mode))
    snap = build_launch_snapshot(
        agent_name="claude", ctx=ctx, system_path=None, agent_path=None,
        workset_path=None, box_path=None, default_categories=floor,
    )
    return snap, ctx


def _probe_mounts(mode, proj, ws_root, helper_log):
    from kanibako.settings.settings_launch import snapshot_category_entries

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
        from kanibako.settings import core_defaults

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

    def test_helper_log_bind_is_the_spec_formula_and_mode_independent(self, tmp_path):
        """The helper-log host_src IS the spec's row, spelled once for all modes.

        PHASE R. ``test_vault_and_logs_resolve_identically_in_all_three_modes``
        proves the chain RESOLVES to the right absolute path; this proves it is
        spelled the way the spec says (§2c ALL PROJECTS,
        ``@workset.logs/@{meta.box.name}.jsonl``) rather than resolving correctly
        by some other route. Two things it stops from creeping back:

        * the retired ``@meta.box.helper_log`` construct-time literal, which
          existed ONLY because the braced form did not parse — a second spelling
          for one bind, and an undeclared key besides;
        * a per-mode arm, the duplication ``workset.logs`` exists to absorb.

        ⚑ The BRACES are the load-bearing part: bare ``@meta.box.name.jsonl``
        parses as one greedy dotted name, resolves absent, and coerces to "" —
        losing both the box name and the extension. That failure is silent at
        every layer, which is why the spelling is pinned and not just the result.
        """
        from kanibako.settings import core_defaults

        log = tmp_path / "b.jsonl"
        log.touch()
        emitted = core_defaults.helper_default_categories(
            box_state_kanibako="/home/agent/.local/state/kanibako",
            socket_path=log,  # any existing path; only the log row is asserted.
            log_path=log,
        )["box.bindings.ro.helper_log"]
        assert emitted == (
            "@workset.logs/@{meta.box.name}.jsonl",
            "/home/agent/.local/state/kanibako/helpers.jsonl",
            "ro",
        )
        # ONE ROW FOR ALL MODES is structural, not a coincidence of three equal
        # arms: the builder takes no ``mode`` at all, so there is nowhere for a
        # per-mode arm to live. ``workset.logs`` carries the whole variation.
        import inspect

        assert "mode" not in inspect.signature(
            core_defaults.helper_default_categories
        ).parameters

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


class TestB3ImagesStoreKey:
    """B3 — the image-store bind routes through the USER KEY ``box.images_store``.

    Spec §2b (``box.images_store``) + §2c ALL PROJECTS row ``images`` (D-M8): the
    runtime-probed podman graphroot is the KEY'S DEFAULT, the bind's host_src is
    the @-ref ``@box.images_store``, and a cascade-tier repoint of the key MOVES
    the mount.  The rider is pinned too: the bind entry is ``images`` — the bind
    row and the scalar key no longer share a name.
    """

    def test_images_bind_is_the_at_ref_and_the_probe_is_the_key_default(self):
        """The emitted table = the @-ref bind + the probed-default scalar.

        Spelled, not just resolved (helper_log precedent): the host_src IS
        ``@box.images_store``, so the mount follows the KEY rather than a probed
        literal that happens to match.  The probe enters the keyspace in the SAME
        table, as the key's floor-scalar default.  And the RENAME is structural:
        the old ``box.bindings.ro.images_store`` spelling must not come back — an
        undeclared bind row sharing the scalar's name was the confusion the B3
        rider retired.
        """
        from kanibako.settings import core_defaults

        emitted = core_defaults.image_default_categories(
            graph_root=Path("/host/graph"),
            storage_conf_path=Path("/host/staging/storage.conf"),
        )
        assert emitted["box.bindings.ro.images"] == (
            "@box.images_store",
            "/var/lib/shared-images",
            "ro",
        )
        # The probed graphroot IS the user key's default, entering here.
        assert emitted["box.images_store"] == "/host/graph"
        # The rider: the bind row no longer shares the scalar key's name.
        assert "box.bindings.ro.images_store" not in emitted
        # images_conf stays an INTERNAL bind (NOT a key) and keeps its name.
        assert emitted["box.bindings.ro.images_conf"] == (
            "/host/staging/storage.conf",
            "~/.config/containers/storage.conf",
            "ro",
        )

    @staticmethod
    def _resolve_images_mounts(tmp_path, box_yaml: str | None):
        """Resolve the CONDITIONAL image table through the LIVE pipeline.

        Mirrors the launch's narrow image resolve: ONLY the image table as the
        floor (``include_base_families=False`` shape), plus an optional box
        settings FILE — the tier a user's ``box: images_store:`` repoint lives
        in.  All host sources are real dirs/files so the reconcile keeps them.
        """
        from kanibako.settings import core_defaults
        from kanibako.settings.settings_launch import (
            build_launch_snapshot,
            snapshot_category_entries,
        )

        probed = tmp_path / "probed-graph"
        probed.mkdir()
        conf = tmp_path / "storage.conf"
        conf.write_text("[storage]\n")
        box_path = None
        if box_yaml is not None:
            box_path = tmp_path / "box-settings.yaml"
            box_path.write_text(box_yaml)

        ctx = make_ctx()
        floor = dict(core_defaults.image_default_categories(
            graph_root=probed, storage_conf_path=conf,
        ))
        snap = build_launch_snapshot(
            agent_name="claude", ctx=ctx, system_path=None, agent_path=None,
            workset_path=None, box_path=box_path, default_categories=floor,
        )
        rec = reconcile_categories(
            snapshot_category_entries(snap, active_agent="claude", box_ctx=ctx)
        )
        return probed, {m.box_dest: m.host_src for m in rec.mounts}

    def test_default_resolves_to_the_probed_graphroot(self, tmp_path):
        """Out of the box, ``@box.images_store`` resolves to the probe.

        No file sets the key, so the floor default (the probed graphroot) wins
        and the store mount is byte-identical to the pre-B3 hardwired source.
        """
        probed, by_dest = self._resolve_images_mounts(tmp_path, box_yaml=None)
        assert by_dest["/var/lib/shared-images"] == str(probed)

    def test_images_bind_follows_a_repointed_images_store(self, tmp_path):
        """A box-file ``box.images_store`` repoint MOVES the store mount.

        THE key ruling of B3 (§3.3): the key is consulted — a cascade value
        beats the probed default by name, and the bind lands on the user's
        store, not the probe's.
        """
        custom = tmp_path / "custom-store"
        custom.mkdir()
        probed, by_dest = self._resolve_images_mounts(
            tmp_path, box_yaml=f"box:\n  images_store: {custom}\n",
        )
        assert by_dest["/var/lib/shared-images"] == str(custom)
        assert by_dest["/var/lib/shared-images"] != str(probed)


class TestDeclarationRoots:
    """P3 — the ABSTRACT categories root AT DECLARATION (spec §2a).

    The rule, driven through the REAL pipeline (build → snapshot → reconcile):
    a bare relative leaf resolves under ``<agent-store>/<category>/``; an already
    self-resolving source (absolute / ``~`` / ``$var`` / ``@``-ref) is NOT rooted.
    There is no assembly-time prepend left to compensate for either way.
    """

    @staticmethod
    def _resolve(entries: dict[str, object], *, agent: str = "claude"):
        """Resolve a floor of agent-scope category entries into ``{dest: src}``."""
        from kanibako.settings.settings_launch import (
            build_launch_snapshot,
            meta_identity_floor,
            snapshot_category_entries,
        )

        ctx = make_ctx(
            workset_name=None,
            xdg={"XDG_DATA_HOME": "/data", "XDG_CACHE_HOME": "/xcache"},
            config={"config.data": "/data", "config.agents": "/data/agents"},
        )
        floor: dict[str, object] = {"system.cache": "$XDG_CACHE_HOME/kanibako"}
        floor.update(meta_identity_floor(
            box_name="b", project_path="/p", inbox="/i", share_global="/sg",
            share_workset=None, agent_name=agent,
        ))
        floor.update(entries)
        snap = build_launch_snapshot(
            agent_name=agent, ctx=ctx, system_path=None, agent_path=None,
            workset_path=None, box_path=None, default_categories=floor,
        )
        rec = reconcile_categories(
            snapshot_category_entries(snap, active_agent=agent, box_ctx=ctx)
        )
        # MOUNTs and COPYs together — ``seeded`` is a COPY, so a mounts-only view
        # would silently skip the one abstract category that is not a mount.
        return {e.box_dest: e.host_src for e in (*rec.mounts, *rec.copies)}

    def test_absolute_and_ref_sources_are_not_rooted(self):
        """T4 — the spec's own ``caches.transform`` worked example.

        ``agent.<a>.caches.transform = (@system.cache/tweakcc, =)`` (§2d)
        is an IDENTITY mount rooted DELIBERATELY at ``@system.cache``.  A rule that
        prefixed it would break the spec's own example — which is why §2a
        reversed the "absolute source is a category mismatch" ruling.

        ⚑ THIS TEST IS *NOT* THE INSTRUMENT FOR AN UNCONDITIONAL-ROOTING BUG.
        It injects the entry straight into the floor, which is where a STORED value
        already sits — so it never runs ``root_relative_source`` and stays GREEN if
        that function drops its prefix test (verified by running that mutation).
        What it pins is the OTHER half: that nothing DOWNSTREAM of the declaration
        re-roots a self-resolving source. The declaration-time rule is instrumented
        by ``tests/test_agent_defaults.py`` (``test_self_resolving_source_is_stored
        _verbatim`` + ``TestLayoutSingleSource::test_prefix_rule``), which drive the
        real loader.
        """
        by_dest = self._resolve({
            "agent.claude.caches.transform": (
                "@system.cache/tweakcc", "@system.cache/tweakcc",
            ),
        })
        # No ``common/``, no ``caches/``, no agent-store prefix ANYWHERE.
        assert by_dest["/xcache/kanibako/tweakcc"] == "/xcache/kanibako/tweakcc"
        assert not any("agents/claude" in src for src in by_dest.values())

    def test_self_resolving_shapes_pass_through_untouched(self):
        by_dest = self._resolve({
            "agent.claude.common.a": ("/abs/dir", "/g/a"),
            "agent.claude.common.b": ("~/tdir", "/g/b"),
            "agent.claude.common.c": ("$XDG_DATA_HOME/x", "/g/c"),
            "agent.claude.common.d": ("@meta.agent.claude.path/own", "/g/d"),
        })
        assert by_dest["/g/a"] == "/abs/dir"
        assert by_dest["/g/b"] == f"{HOST_HOME}/tdir"
        assert by_dest["/g/c"] == "/data/x"
        assert by_dest["/g/d"] == "/data/agents/claude/own"

    def test_bare_relative_leaf_is_rooted_under_its_category_dir(self):
        """T5 — the POSITIVE case, per abstract category.

        The rooting happens at DECLARATION, so what the pipeline sees is already
        the full ref; this pins that the ref a declaration loader BUILDS resolves
        to ``<store>/<category>/<leaf>``.
        """
        from kanibako.settings.agent_config import agent_category_root_ref, root_relative_source

        entries = {
            f"agent.claude.{cat}.probe": (
                root_relative_source(
                    "leaf", agent_category_root_ref("claude", cat),
                ),
                f"/g/{cat}",
            )
            for cat in ("common", "caches", "seeded")
        }
        by_dest = self._resolve(entries)
        assert by_dest["/g/common"] == "/data/agents/claude/common/leaf"
        assert by_dest["/g/caches"] == "/data/agents/claude/caches/leaf"
        # ``seeded`` is a COPY, not a MOUNT — but it roots identically.
        assert by_dest["/g/seeded"] == "/data/agents/claude/seeded/leaf"

    def test_claude_commons_resolve_under_common_dir(self):
        """T11 — the PERMANENT form of the gate's one intended change (M-3).

        The REAL ``default_common()`` through the REAL pipeline lands at
        ``<data>/agents/claude/common/{plugins,cache}``.  This is the standing
        replacement for the one-off before/after gate diff.
        """
        from kanibako.plugins.claude import ClaudeTarget

        by_dest = self._resolve(dict(ClaudeTarget().default_common()))
        assert by_dest["/home/agent/.claude/plugins"] == (
            "/data/agents/claude/common/plugins"
        )
        assert by_dest["/home/agent/.claude/cache"] == (
            "/data/agents/claude/common/cache"
        )


class TestDeclarationKeyIsDiscriminated:
    """Every entry carries the DISCRIMINATED key it was declared under (§0).

    ``CategoryEntry.scope`` is the BARE precedence token (``agent``), but
    ``CategoryEntry.key`` must name a real tier — ``agent.<agent>`` or
    ``agent.default``. A bare ``agent.<category>.<name>`` is not a key at all, so
    a collision message or a ``binding_derivations.*`` entry spelled that way
    would point a reader at something the keyspace forbids them to write.

    ⚑ The agent tier is the ONLY place the two can differ, because
    ``_agent_pick_node`` collapses ``agent.default`` and ``agent.<active>`` into
    one effective node BEFORE emission. Both arms are asserted: a name the active
    slot sets, and a name only ``agent.default`` sets.
    """

    @staticmethod
    def _entries(floor, *, agent="claude"):
        from kanibako.settings.settings_launch import (
            build_launch_snapshot,
            meta_identity_floor,
            snapshot_category_entries,
        )

        ctx = make_ctx(
            workset_name=None,
            config={"config.data": "/data", "config.agents": "/data/agents"},
        )
        base: dict[str, object] = dict(meta_identity_floor(
            box_name="b", project_path="/p", inbox="/i", share_global="/sg",
            share_workset=None, agent_name=agent,
        ))
        base.update(floor)
        snap = build_launch_snapshot(
            agent_name=agent, ctx=ctx, system_path=None, agent_path=None,
            workset_path=None, box_path=None, default_categories=base,
        )
        return {
            e.name: e
            for e in snapshot_category_entries(
                snap, active_agent=agent, box_ctx=ctx,
            )
        }

    def test_active_slot_and_default_tier_keys_are_told_apart(self):
        by_name = self._entries({
            "agent.claude.common.plugins": ("/store/plugins", "/g/plugins"),
            "agent.default.common.shared_dir": ("/store/shared", "/g/shared"),
        })
        assert by_name["plugins"].key == "agent.claude.common.plugins"
        assert by_name["shared_dir"].key == "agent.default.common.shared_dir"
        # The PRECEDENCE token stays bare for both — they are different facts.
        assert by_name["plugins"].scope == "agent"
        assert by_name["shared_dir"].scope == "agent"

    def test_the_active_slot_wins_a_name_the_default_tier_also_sets(self):
        by_name = self._entries({
            "agent.default.caches.build": ("/default/build", "/g/build"),
            "agent.claude.caches.build": ("/active/build", "/g/build"),
        })
        assert by_name["build"].host_src == "/active/build"
        assert by_name["build"].key == "agent.claude.caches.build"

    def test_no_emitted_key_uses_the_bare_agent_form(self):
        for entry in self._entries({
            "agent.claude.common.plugins": ("/store/plugins", "/g/plugins"),
            "agent.default.seeded.template": ("/store/tpl", "~"),
            "agent.claude.env.FOO": "bar",
            "agent.claude.secret_path.TOK": "/secrets/tok",
            "box.bindings.rw.home": ("/boxes/b/home", "~"),
        }).values():
            assert not entry.key.startswith("agent.common")
            assert not entry.key.startswith("agent.seeded")
            assert not entry.key.startswith("agent.env")
            assert not entry.key.startswith("agent.secret_path")
        # And the non-agent scopes are spelled with their bare token, as always.
        by_name = self._entries({"box.bindings.rw.home": ("/boxes/b/home", "~")})
        assert by_name["home"].key == "box.bindings.rw.home"


class TestGuaranteeCreateIsALaunchGuaranteeNotAReadOne:
    """A DISPLAY verb must not write to disk.

    ``box config show --effective`` resolves the SAME core table a launch does,
    and that table create-if-missings the vault source dirs so the bind is always
    emitted. Rendering a box must not make that true on disk — so the read path
    passes ``guarantee_create=False``, which suppresses ONLY the mkdir. The binds
    are emitted identically either way, which is what makes the display honest
    rather than merely quiet.
    """

    class _P:
        enable_vault = True

        def __init__(self, root):
            self.shell_path = root / "box_data" / "home"
            self.project_path = root / "workspace"
            self.vault_ro_path = root / "vault" / "ro"
            self.vault_rw_path = root / "vault" / "rw"

    def test_launch_creates_the_vault_sources(self, tmp_path):
        from kanibako.settings import core_defaults

        proj = self._P(tmp_path)
        core_defaults.core_default_categories(
            None, proj, enable_vault=True, mode="standalone",
        )
        assert proj.vault_ro_path.is_dir()
        assert proj.vault_rw_path.is_dir()

    def test_a_read_only_resolve_creates_nothing(self, tmp_path):
        from kanibako.settings import core_defaults

        proj = self._P(tmp_path)
        core_defaults.core_default_categories(
            None, proj, enable_vault=True, mode="standalone",
            guarantee_create=False,
        )
        assert not proj.vault_ro_path.exists()
        assert not proj.vault_rw_path.exists()

    def test_the_emitted_binds_are_identical_either_way(self, tmp_path):
        """The flag gates the SIDE EFFECT, not the result — otherwise the
        display would be showing a different box than the launch mounts."""
        from kanibako.settings import core_defaults

        read = core_defaults.core_default_categories(
            None, self._P(tmp_path / "r"), enable_vault=True,
            mode="standalone", guarantee_create=False,
        )
        launch = core_defaults.core_default_categories(
            None, self._P(tmp_path / "l"), enable_vault=True,
            mode="standalone",
        )
        # Same keys, same shapes; only the tmp root differs in the sources.
        assert set(read) == set(launch)
        assert [v[1:] for v in read.values()] == [v[1:] for v in launch.values()]


class TestEffectiveBlockAgainstARealAgentPlugin:
    """N1 — the declaration/derivation pairing, driven by a REAL ``ClaudeTarget``.

    Every other test of the ``--effective`` block builds its snapshot by hand, so
    the pairing is only ever proven against declarations a test wrote. This one
    takes the SHIPPED claude plugin's own ``default_common()`` /
    ``default_category_binds()`` tables through the REAL
    ``build_launch_snapshot → snapshot_category_entries → derive_binding_keys``
    chain and renders THAT — so a plugin whose declarations do not survive the
    derivation, or whose keys come out undiscriminated, fails here.
    """

    @staticmethod
    def _snapshot():
        from kanibako.commands.start import _install_derived_bindings
        from kanibako.settings.settings_categories import derive_binding_keys
        from kanibako.settings.settings_launch import (
            build_launch_snapshot,
            meta_agent_path_floor,
            meta_identity_floor,
            snapshot_category_entries,
        )
        from kanibako.plugins.claude import ClaudeTarget

        target = ClaudeTarget()
        ctx = make_ctx(
            workset_name=None,
            xdg={"XDG_DATA_HOME": "/data", "XDG_CACHE_HOME": "/xcache"},
            config={"config.data": "/data", "config.agents": "/data/agents"},
        )
        floor: dict[str, object] = {"system.cache": "/xcache/kanibako"}
        floor.update(meta_identity_floor(
            box_name="b", project_path="/p", inbox="/i", share_global="/sg",
            share_workset=None, agent_name=target.name,
        ))
        floor.update(meta_agent_path_floor(target.name))
        floor.update(target.default_common())
        floor.update(target.default_seeds())
        floor.update(target.default_category_binds())
        snap = build_launch_snapshot(
            agent_name=target.name, ctx=ctx, system_path=None, agent_path=None,
            workset_path=None, box_path=None, default_categories=floor,
        )
        entries = snapshot_category_entries(
            snap, active_agent=target.name, box_ctx=ctx,
        )
        _install_derived_bindings(snap, derive_binding_keys(entries))
        return snap, entries

    def test_the_plugins_own_declarations_derive_discriminated_keys(self):
        from kanibako.settings.settings_store import KeyStore
        from kanibako.settings.settings_views import derived_bindings

        snap, entries = self._snapshot()
        abstract = [
            e for e in entries
            if e.category in ("common", "caches", "seeded")
        ]
        assert abstract, "the claude plugin declares no abstract category"

        node = dict.get(snap, "binding_derivations")
        assert isinstance(node, KeyStore)
        derived = derived_bindings(node)
        # One derivation per abstract declaration, keyed by the DISCRIMINATED
        # declaration key — never the bare ``agent.<category>`` form (§0).
        assert set(derived) == {e.key for e in abstract}
        for key in derived:
            assert key.startswith(("agent.claude.", "agent.default.",
                                   "system.", "workset.", "box."))
        # And the derivation carries the RESOLVED guest dest, not the raw one.
        for entry in abstract:
            assert derived[entry.key].box == entry.box_dest

    def test_the_block_renders_the_pair_for_a_real_declaration(self):
        import io

        from kanibako.settings.config_display import _print_category_block

        snap, entries = self._snapshot()
        buf = io.StringIO()
        _print_category_block(snap, None, buf)
        text = buf.getvalue()
        for entry in entries:
            if entry.category not in ("common", "caches", "seeded"):
                continue
            assert f"  {entry.key} = " in text, entry.key
            assert f"    binding_derivations.{entry.key} = " in text, entry.key
        # The bare agent form never appears (it is not a key).
        assert "agent.common" not in text
        assert "agent.caches" not in text


class TestForgedDerivationsTableNeverEntersTheMerge:
    """B4 Editor fix — spec §0 fault class for the RESERVED node (R-8).

    INVERTS the pre-fix repro: a box settings file carrying a hand-forged
    top-level ``binding_derivations:`` table used to ride into the snapshot
    (the old ``meta.derived`` spelling was shielded by the ``meta:`` drop; the
    root-level rename lost the shield). The forged table planted (i) an entry
    COLLIDING with a real declaration key and (ii) a non-``Bind`` junk leaf —
    and the junk leaf crashed the ``derived_bindings`` lens, and with it the
    ``--effective`` display route, with ``ViewError`` (user-reachable via
    ``config show --effective``). Post-fix the table is DROPPED at assembly
    with a warning, so the node holds EXACTLY what the launch seam
    (``_install_derived_bindings``) wrote.
    """

    # The REAL declaration the forged table collides with.
    _DECL_KEY = "agent.claude.common.plugins"

    @staticmethod
    def _snapshot(tmp_path):
        import yaml

        from kanibako.commands.start import _install_derived_bindings
        from kanibako.settings.settings_categories import derive_binding_keys
        from kanibako.settings.settings_launch import (
            build_launch_snapshot,
            meta_identity_floor,
            meta_runtime_floor,
            snapshot_category_entries,
            workset_anchor_floor,
        )

        box_file = tmp_path / "settings.yaml"
        box_file.write_text(yaml.safe_dump({
            "box": {"enable_vault": False},
            "binding_derivations": {
                # (i) collides with the REAL declaration key below.
                "agent": {"claude": {"common": {
                    "plugins": ["/forged/src", "~/forged"],
                }}},
                # (ii) non-Bind junk — pre-fix this leaf crashed the lens.
                "junk": "zebra-not-a-bind",
            },
        }))

        floor: dict[str, object] = {
            "agent.claude.common.plugins": (
                "/store/plugins", "~/.claude/plugins", "Z",
            ),
        }
        floor.update(meta_runtime_floor(mode="primary", ws_name="__PRIMARY__"))
        floor.update(workset_anchor_floor(mode="primary"))
        floor.update(meta_identity_floor(
            box_name="mybox", project_path="/code/x", inbox="/i",
            share_global="/sg", share_workset="/sw", agent_name="claude",
        ))
        ctx = make_ctx(
            workset_name=None, config={"config.primary_workset": "/data/pw"},
        )
        snap = build_launch_snapshot(
            agent_name="claude", ctx=ctx, system_path=None, agent_path=None,
            workset_path=None, box_path=box_file, default_categories=floor,
        )
        entries = snapshot_category_entries(
            snap, active_agent="claude", box_ctx=ctx,
        )
        _install_derived_bindings(snap, derive_binding_keys(entries))
        return snap, box_file

    def test_lens_returns_exactly_the_real_derivations(self, tmp_path, caplog):
        from kanibako.settings.settings_store import KeyStore
        from kanibako.settings.settings_views import derived_bindings

        with caplog.at_level("WARNING"):
            snap, box_file = self._snapshot(tmp_path)
        # The drop announced itself, naming the box file and the token.
        assert [
            r.getMessage() for r in caplog.records
            if r.levelname == "WARNING"
            and "binding_derivations" in r.getMessage()
            and str(box_file) in r.getMessage()
        ]
        node = dict.get(snap, "binding_derivations")
        assert isinstance(node, KeyStore)
        # EXACTLY the real derivations: the launch seam's write, nothing forged.
        derived = derived_bindings(node)  # pre-fix: ViewError on the junk leaf
        assert set(derived) == {self._DECL_KEY}
        # The colliding key carries the REAL host source, not the forged one.
        assert derived[self._DECL_KEY].host == "/store/plugins"

    def test_effective_display_renders_without_viewerror(self, tmp_path):
        import io

        from kanibako.settings.config_display import _print_category_block

        snap, _ = self._snapshot(tmp_path)
        buf = io.StringIO()
        _print_category_block(snap, None, buf)  # pre-fix: raised ViewError
        text = buf.getvalue()
        # The real declaration/derivation pair renders; no phantom lines.
        assert f"    binding_derivations.{self._DECL_KEY} = " in text
        assert "/forged" not in text
        assert "junk" not in text
