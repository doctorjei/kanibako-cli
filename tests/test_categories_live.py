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
) -> ResolveCtx:
    return ResolveCtx(
        agent_name=agent_name,
        workset_name=workset_name,
        host_home=host_home,
        xdg=xdg if xdg is not None else {"XDG_DATA_HOME": "/data"},
    )

# ---------------------------------------------------------------------------
# B2b: per-mode BYTE-IDENTITY of the @-ref-routed home/vault binds (the
# equivalence bar) + the box.bindings.rw.home cascade override + workset anchors.
# ---------------------------------------------------------------------------


def _resolve_home_vault(floor, *, mode):
    """Resolve home/vault through the LIVE build_launch_snapshot pipeline (the
    single route the launch uses) → {box_dest: host_src}."""
    from kanibako.settings_launch import (
        build_launch_snapshot,
        snapshot_category_entries,
    )

    ctx = make_ctx(workset_name=None)
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
        # PRIMARY/NAMED: @workset.boxes/@meta.box.name/home (+ vault) resolve to the
        # SAME literal proj.shell_path / proj.vault_*_path the old injection used.
        from kanibako.settings_launch import (
            meta_identity_floor,
            workset_anchor_floor,
        )

        # The launch materializes these from proj: workset.boxes = shell_path's box-
        # parent, workset.vault_* = vault parent; meta.box.name = box name.
        floor = {
            "box.bindings.rw.home": (
                "@workset.boxes/@meta.box.name/home", "~", "Z,U",
            ),
            "box.bindings.ro.vault": (
                "@workset.vault_ro/@meta.box.name", "~/vault/ro", "ro",
            ),
            "box.bindings.rw.vault": (
                "@workset.vault_rw/@meta.box.name", "~/vault/rw", "Z,U",
            ),
        }
        floor.update(workset_anchor_floor(
            mode="primary",
            boxes="/data/pw/boxes",
            vault_ro="/data/pw/vault/ro",
            vault_rw="/data/pw/vault/rw",
            logs="/data/pw/logs",
            helper_log="/data/pw/logs/mybox.jsonl",
        ))
        floor.update(meta_identity_floor(
            box_name="mybox", project_path="/code/x", inbox="/i",
            share_global="/sg", share_workset="/sw",
        ))
        by_dest = _resolve_home_vault(floor, mode="primary")
        # Byte-identical to proj.shell_path = boxes/<name>/home, vault/{ro,rw}/<name>.
        assert by_dest["/home/agent"] == "/data/pw/boxes/mybox/home"
        assert by_dest["/home/agent/vault/ro"] == "/data/pw/vault/ro/mybox"
        assert by_dest["/home/agent/vault/rw"] == "/data/pw/vault/rw/mybox"

    def test_standalone_home_vault_resolve_to_proj_literals(self):
        # STANDALONE: home/vault route the TRUE spec @meta.workset.path/* chains
        # (§2c L427/425/428).  After the B2b ws_root fix, meta.workset.path = the
        # project ROOT (<root>, = str(proj.metadata_path)), so the chains resolve to
        # <root>/box_data/home = proj.shell_path and <root>/vault/{ro,rw} =
        # proj.vault_{ro,rw}_path — byte-identical, no invented *_src anchor.
        from kanibako.settings_launch import (
            meta_runtime_floor,
            workset_anchor_floor,
        )

        floor = {
            "box.bindings.rw.home": (
                "@meta.workset.path/box_data/home", "~", "Z,U",
            ),
            "box.bindings.ro.vault": (
                "@meta.workset.path/vault/ro", "~/vault/ro", "ro",
            ),
            "box.bindings.rw.vault": (
                "@meta.workset.path/vault/rw", "~/vault/rw", "Z,U",
            ),
        }
        # meta.workset.path = @meta.runtime.ws_root = <root> (the B2b fix passes
        # str(proj.metadata_path) = the project ROOT as ws_root_literal).
        floor.update(meta_runtime_floor(
            mode="standalone", ws_name="__STANDALONE__", ws_root_literal="/proj",
        ))
        floor.update(workset_anchor_floor(
            mode="standalone",
            boxes=None, vault_ro=None, vault_rw=None, logs=None,
            helper_log="/proj/box_data/sb.jsonl",
        ))
        by_dest = _resolve_home_vault(floor, mode="standalone")
        # <root>=/proj: home = /proj/box_data/home, vault = /proj/vault/{ro,rw}.
        assert by_dest["/home/agent"] == "/proj/box_data/home"
        assert by_dest["/home/agent/vault/ro"] == "/proj/vault/ro"
        assert by_dest["/home/agent/vault/rw"] == "/proj/vault/rw"

    def test_box_bindings_home_cascade_override_wins(self):
        # Option A: a box.bindings.rw.home CASCADE override (box scope) WINS over the
        # spec-derived @workset.boxes/@meta.box.name/home default (the new mechanism
        # for a custom home, replacing the dropped meta["shell"] override).
        from kanibako.settings_launch import (
            build_launch_snapshot,
            meta_identity_floor,
            snapshot_category_entries,
            workset_anchor_floor,
        )

        floor = {
            "box.bindings.rw.home": (
                "@workset.boxes/@meta.box.name/home", "~", "Z,U",
            ),
        }
        floor.update(workset_anchor_floor(
            mode="primary", boxes="/data/pw/boxes", vault_ro="/v/ro",
            vault_rw="/v/rw", logs="/l", helper_log="/l/mybox.jsonl",
        ))
        floor.update(meta_identity_floor(
            box_name="mybox", project_path="/code/x", inbox="/i",
            share_global="/sg", share_workset="/sw",
        ))
        ctx = make_ctx(workset_name=None)
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
    """B2b workset path-anchor materialization (JC-B2b-1)."""

    def test_primary_named_anchors_present(self):
        from kanibako.settings_launch import workset_anchor_floor

        floor = workset_anchor_floor(
            mode="named", boxes="/ws/boxes", vault_ro="/ws/vault/ro",
            vault_rw="/ws/vault/rw", logs="/ws/logs",
            helper_log="/ws/logs/b.jsonl",
            workset_channels={"commons": "/ws/ch/commons", "chat": "/ws/ch/chat",
                              "share": "/ws/ch/share"},
        )
        assert floor["workset.boxes"] == "/ws/boxes"
        assert floor["workset.vault_ro"] == "/ws/vault/ro"
        assert floor["workset.vault_rw"] == "/ws/vault/rw"
        assert floor["workset.logs"] == "/ws/logs"
        assert floor["meta.box.helper_log"] == "/ws/logs/b.jsonl"
        assert floor["workset.channels.commons"] == "/ws/ch/commons"

    def test_standalone_anchors_are_none(self):
        from kanibako.settings_launch import workset_anchor_floor

        floor = workset_anchor_floor(
            mode="standalone", boxes=None, vault_ro=None, vault_rw=None,
            logs=None, helper_log="/proj/box_data/b.jsonl",
        )
        # Spec §2c L416: standalone workset path anchors are None — home/vault route
        # the TRUE @meta.workset.path/* chains (no invented *_src anchors; the B2b
        # ws_root fix made those unnecessary).
        assert floor["workset.boxes"] is None
        assert floor["workset.vault_ro"] is None
        assert floor["workset.vault_rw"] is None
        assert floor["workset.logs"] is None
        # helper_log still routes a whole-value anchor (the .jsonl regex-parse limit
        # is independent of ws_root).
        assert floor["meta.box.helper_log"] == "/proj/box_data/b.jsonl"
        # No invented resolved-literal home/vault anchors remain.
        assert "meta.box.home_src" not in floor
        assert "meta.box.vault_ro_src" not in floor
        assert "meta.box.vault_rw_src" not in floor
        # No workset channels for standalone.
        assert "workset.channels.commons" not in floor
