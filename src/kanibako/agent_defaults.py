"""Per-agent plugin defaults — thin reader of each plugin's shipped defaults file.

Each agent plugin package (``kanibako.plugins.claude`` / ``…goose`` / ``…codex``)
owns ONE declarative defaults file (``<agent>-defaults.yaml``) holding that
agent's complete DEFAULT-SET — its :class:`~kanibako.targets.base.PluginDescriptor`
data (bindings, launch grammar, settings, container env, cred files, …) plus its
AGENT-scope ``default_shares`` — in the specced structured form.  This mirrors how
the system/core defaults ship (:mod:`kanibako.core_defaults` /
``kanibako/data/core-defaults.yaml``) and how containerfiles/templates ship via
:mod:`importlib.resources`.

This module is the THIN reader those plugins call from their ``descriptor`` /
``default_shares`` properties.  It builds the descriptor from the file so the
in-code descriptor is no longer hand-written per plugin.

Split (documented in each YAML header too):

* DECLARATIVE — everything in the file: the binding ORIGIN selectors (which
  detected install field supplies the host source — a declarative string, NOT a
  probed value), box-side destinations, the mode/operations grammar, settings
  routing, container env, cred-file lifecycle, host_prep/init_dirs, and the
  agent-share box_dests.  Box-side destinations under the guest home are written
  as ``$GUEST_HOME`` EXPRESSIONS (e.g. ``$GUEST_HOME/.local/bin/claude``) that
  this loader expands from the single :data:`~kanibako.settings_resolve.GUEST_HOME`
  constant — no ``/home/agent`` literal in the file.
* CODE-RESOLVED — the CRITICAL/runtime-probed HOST binary paths stay in the
  plugin's ``detect()`` (the contract-path constants + npm/ELF resolution).  The
  file only names, declaratively, WHICH detected field (``binary`` / ``launcher`` /
  ``install_dir``) each binding's host source comes from (its ``origin``); the
  actual probed path is filled in later by ``descriptor_mounts`` from the
  :class:`~kanibako.targets.base.AgentInstall`.
"""

from __future__ import annotations

import importlib.resources
from pathlib import Path
from typing import Any

import yaml

from kanibako.settings_resolve import GUEST_HOME
from kanibako.targets.base import (
    BindDefault,
    BindKind,
    Binding,
    BindScope,
    Cadence,
    Channel,
    CredFileSpec,
    HostSrcOrigin,
    Operation,
    PersonaSpec,
    PluginDescriptor,
    SafeBypass,
    SettingArg,
)


def _expand(value: str) -> str:
    """Expand a ``$GUEST_HOME`` prefix in a box-side path expression.

    The defaults files write every in-box destination as a ``$GUEST_HOME``
    expression so the guest-home literal lives in exactly one place (the
    :data:`~kanibako.settings_resolve.GUEST_HOME` constant, single SoT).  Only the
    leading ``$GUEST_HOME`` token is substituted; the rest is left verbatim.
    """
    if value.startswith("$GUEST_HOME"):
        return GUEST_HOME + value[len("$GUEST_HOME"):]
    return value


def _load_doc(package: str, filename: str) -> dict[str, Any]:
    """Read and parse a plugin's bundled defaults file."""
    ref = importlib.resources.files(package).joinpath(filename)
    raw = yaml.safe_load(Path(str(ref)).read_text()) or {}
    if not isinstance(raw, dict):
        return {}
    return raw


def _build_binding(entry: dict[str, Any], package: str) -> Binding:
    """Build one :class:`Binding` from a declarative file entry.

    ``origin`` names the detected install field that supplies the host source
    (``binary`` / ``launcher`` / ``install_dir`` / ``literal``); ``box_dest`` is a
    ``$GUEST_HOME`` expression expanded here.  ``kind`` / ``scope`` map to their
    enums; ``ro`` defaults to True.

    A ``literal`` origin's fixed host source is named EITHER as a
    ``literal_src_pkg`` (a *package*-relative resource — e.g. a plugin's shipped
    kickoff-loader SEED file — resolved here to its installed host path
    via :mod:`importlib.resources`, the same seam :func:`_load_doc` uses) OR as a
    plain ``literal_src`` filesystem path.  ``literal_src_pkg`` wins when both are
    present.
    """
    literal_pkg = entry.get("literal_src_pkg")
    literal = entry.get("literal_src")
    if literal_pkg is not None:
        ref = importlib.resources.files(package).joinpath(literal_pkg)
        literal_src: Path | None = Path(str(ref))
    elif literal is not None:
        literal_src = Path(literal)
    else:
        literal_src = None
    return Binding(
        key=entry["key"],
        origin=HostSrcOrigin(entry["origin"]),
        box_dest=_expand(entry["box_dest"]),
        kind=BindKind(entry["kind"]),
        scope=BindScope(entry["scope"]),
        ro=bool(entry.get("ro", True)),
        literal_src=literal_src,
    )


def _build_safe_bypass(raw: dict[str, Any] | None) -> SafeBypass | None:
    """Build the :class:`SafeBypass` toggle from its declarative block (or None)."""
    if not raw:
        return None
    return SafeBypass(
        channel=Channel(raw["channel"]),
        flag=tuple(raw.get("flag", ())),
        env_var=raw.get("env_var", ""),
        env_value=raw.get("env_value", ""),
        secure_env_value=raw.get("secure_env_value", ""),
        secure_flag=tuple(raw.get("secure_flag", ())),
        setting_key=raw.get("setting_key", ""),
    )


def _build_setting_arg(entry: dict[str, Any]) -> SettingArg:
    return SettingArg(
        setting_key=entry["setting_key"],
        channel=Channel(entry["channel"]),
        flag=tuple(entry.get("flag", ())),
        env_var=entry.get("env_var", ""),
    )


def _build_persona(raw: dict[str, Any] | None) -> PersonaSpec | None:
    """Build the harness-specific :class:`PersonaSpec` from its block (or None).

    Absent ``persona:`` → ``None`` → the preflight falls back to claude-style ENV
    endpoint delivery + the ``ANTHROPIC_AUTH_TOKEN`` token var (byte-identical).
    """
    if not raw:
        return None
    pin_raw = raw.get("provider_pin") or {}
    provider_pin = tuple(sorted((str(k), str(v)) for k, v in pin_raw.items()))
    return PersonaSpec(
        token_var=raw.get("token_var", ""),
        endpoint_delivery=raw.get("endpoint_delivery", "env"),
        wire_api=raw.get("wire_api", "chat"),
        host_dir_adopt=bool(raw.get("host_dir_adopt", True)),
        provider_pin=provider_pin,
        model_required=bool(raw.get("model_required", False)),
    )


def _build_cred_file(entry: dict[str, Any]) -> CredFileSpec:
    return CredFileSpec(
        home_rel=entry["home_rel"],
        host_rel=entry["host_rel"],
        cadence=Cadence(entry.get("cadence", "sync")),
        mtime_gate=bool(entry.get("mtime_gate", True)),
        filtered=bool(entry.get("filtered", False)),
        is_dir=bool(entry.get("is_dir", False)),
    )


def load_descriptor(package: str, filename: str) -> PluginDescriptor:
    """Build a plugin's :class:`PluginDescriptor` from its shipped defaults file.

    *package* is the plugin's import package (e.g. ``"kanibako.plugins.claude"``)
    and *filename* its declarative defaults file (e.g. ``"claude-defaults.yaml"``).
    The returned descriptor is byte-for-byte equivalent to the former hand-written
    one — box_dest ``$GUEST_HOME`` expressions are expanded and every enum field
    is mapped from its string name.
    """
    doc = _load_doc(package, filename)
    desc = doc.get("descriptor", {})

    return PluginDescriptor(
        command=tuple(desc["command"]),
        bindings=tuple(_build_binding(b, package) for b in desc.get("bindings", [])),
        mode={k: tuple(v) for k, v in desc.get("mode", {}).items()},
        operations={
            k: Operation(tuple(v["fragment"]))
            for k, v in desc.get("operations", {}).items()
        },
        safe_bypass=_build_safe_bypass(desc.get("safe_bypass")),
        settings=tuple(_build_setting_arg(s) for s in desc.get("settings", [])),
        persona=_build_persona(desc.get("persona")),
        container_env={
            k: _expand(v) for k, v in desc.get("container_env", {}).items()
        },
        cred_files=tuple(_build_cred_file(c) for c in desc.get("cred_files", [])),
        host_prep=bool(desc.get("host_prep", False)),
        init_dirs=tuple(desc.get("init_dirs", ())),
        auth_share_support=bool(desc.get("auth_share_support", False)),
        vscode_extension=desc.get("vscode_extension"),
    )


def load_category_binds(package: str, filename: str) -> dict[str, BindDefault]:
    """Build a plugin's AGENT-scope ``@``-ref-sourced category BINDS from its file.

    Each entry in the file's ``category_binds:`` section declares one agent-scope
    category default whose HOST SOURCE is an ``@``-ref (``meta_ref``) — the mirror
    of :mod:`kanibako.core_defaults`'s ``meta_ref`` bind shape, at AGENT scope.  It
    returns a mapping of the (un-discriminated) scoped category key
    ``agent.<category>.<key>`` → a STRUCTURED bind tuple ``(meta_ref, box_dest[,
    "ro"])`` (spec §2a — a tuple, NOT a colon-joined string).

    The value's element 0 is the RAW ``@``-ref STRING (e.g. a ``"@system.*"``
    source key); the launch category cascade folds it into the floor and ``expand``
    resolves it to the referenced path — so a plugin declares a bind to a shared
    source WITHOUT any per-harness path knowledge in core (spec §2d L608).
    ``start.py`` unions this table
    into ``default_categories`` alongside :func:`load_shares`; the bare
    ``agent.<category>.*`` key is re-rooted to the active slot by
    ``settings_launch._agent_scope_qualify`` exactly like a share.

    ``box_dest`` is a ``~`` / ``$GUEST_HOME`` expression (``$GUEST_HOME`` is
    expanded here; a leading ``~`` is left for the box-side ``box_dest`` resolve in
    :func:`~kanibako.settings_launch.snapshot_category_entries`).  ``ro: true`` emits
    the explicit ``"ro"`` mount option (element 2); otherwise a 2-tuple is emitted
    and reconcile falls back to the category default.  Returns ``{}`` when the file
    declares no category binds.
    """
    binds: dict[str, BindDefault] = {}
    for entry in _load_doc(package, filename).get("category_binds", []):
        key = f"agent.{entry['category']}.{entry['key']}"
        box_dest = _expand(entry["box_dest"])
        host_src = entry["meta_ref"]
        if entry.get("ro", False):
            binds[key] = (host_src, box_dest, "ro")
        else:
            binds[key] = (host_src, box_dest)
    return binds


def load_shares(package: str, filename: str) -> dict[str, BindDefault]:
    """Build a plugin's AGENT-scope ``default_shares`` map from its defaults file.

    Each entry maps a scoped category key (``agent.shared.<name>``) to a STRUCTURED
    bind pair ``(host_src, box_dest)`` (spec §2a — a tuple, NOT a colon-joined
    string).  The relative ``host_src`` joins under the per-agent store root in the
    category resolver; ``box_dest`` is a ``$GUEST_HOME`` expression expanded here.
    Returns ``{}`` when the file declares no shares.
    """
    shares: dict[str, BindDefault] = {}
    for entry in _load_doc(package, filename).get("shares", []):
        options = entry.get("options")
        if options is not None:
            shares[entry["key"]] = (
                entry["host_src"], _expand(entry["box_dest"]), options,
            )
        else:
            shares[entry["key"]] = (entry["host_src"], _expand(entry["box_dest"]))
    return shares
